"""질의 의도와 기간 분류.

LLM 이 JSON 스키마로 의도와 기간(`date_from`·`date_to`)을 고른다. 기간은 ISO 날짜로 파싱하고,
파싱되지 않는 값은 그 값만 버린다 — 잘못된 날짜로 필터를 걸면 근거가 통째로 사라지기 때문이다.
LLM 이 실패하면(오류·시간 초과·형식 불량) YAML 키워드 규칙으로 의도만 고른다. 안전 필터 차단은
실패가 아니라 판정이므로 폴백하지 않고 그대로 올린다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from chatcore.deadline import Deadline
from chatcore.errors import AppError, ContentFiltered
from chatcore.llm import ChatModel, Usage, system, user
from chatcore.prompts import VersionedFile
from delivery_agent.cache import TTLCache

log = logging.getLogger(__name__)


class Intent(StrEnum):
    SCHEDULE = "schedule"
    ISSUE = "issue"
    CHANGE = "change"
    TEST = "test"
    COST = "cost"
    STAFFING = "staffing"
    ACCEPTANCE = "acceptance"
    STATUS = "status"
    OTHER = "other"


INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": [i.value for i in Intent]},
        "date_from": {"type": ["string", "null"]},
        "date_to": {"type": ["string", "null"]},
    },
    "required": ["intent", "date_from", "date_to"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    date_from: date | None = None
    date_to: date | None = None


def parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def keyword_intent(query: str, rules: Sequence[Mapping[str, Any]]) -> Intent:
    """규칙을 위에서부터 보고 키워드가 처음 나오는 의도를 고른다(점수 합산 없음)."""
    text = query.lower()
    for rule in rules:
        if any(str(k).lower() in text for k in rule["keywords"]):
            return Intent(rule["intent"])
    return Intent.OTHER


def _parse(text: str) -> IntentResult:
    data = json.loads(text)
    return IntentResult(
        Intent(data["intent"]), parse_date(data.get("date_from")), parse_date(data.get("date_to"))
    )


class IntentClassifier:
    def __init__(self, chat: ChatModel, prompts: VersionedFile) -> None:
        self._chat = chat
        self._prompts = prompts
        self._rules: list[Mapping[str, Any]] = prompts.data["intent_keywords"]
        # "지난달" 같은 상대 기간은 날짜가 바뀌면 다른 답이 되므로 오늘 날짜도 키에 넣는다.
        self._cache: TTLCache[tuple[str, date], IntentResult] = TTLCache(maxsize=512, ttl_s=600)

    async def classify(self, query: str, *, today: date, deadline: Deadline) -> tuple[IntentResult, Usage]:
        if (cached := self._cache.get((query, today))) is not None:
            return cached, Usage()
        messages = [
            system(self._prompts.render("intent_system")),
            user(self._prompts.render("intent_user", today=today.isoformat(), query=query)),
        ]
        try:
            completion = await deadline.run(
                self._chat.complete(
                    messages, task="delivery.intent", deadline=deadline, json_schema=INTENT_SCHEMA
                )
            )
            result = _parse(completion.text)
        except ContentFiltered:
            raise
        except (AppError, ValueError, KeyError, TypeError) as exc:
            log.warning("intent LLM failed; using keyword fallback", extra={"error": type(exc).__name__})
            return IntentResult(keyword_intent(query, self._rules)), Usage()
        self._cache.put((query, today), result)
        return result, completion.usage
