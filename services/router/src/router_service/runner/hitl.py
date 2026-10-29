"""되묻기(HITL) 응답 만들기.

- `rewrite` 단계: LLM 이 바로 보낼 수 있는 재질문 2~3개를 제안한다. 프로젝트는 이름 대신
  `{unit:P-101}` 자리표시자로 쓰게 하고, 사용자가 볼 수 있는 프로젝트인지 대조한 뒤 이름으로
  바꾼다. 허용 목록 밖의 자리표시자가 든 제안은 버린다 — 모델이 권한 밖 프로젝트 이름을 화면에
  흘리지 못한다. 남은 제안이 없으면 고정 문구를 쓴다.
- `domain` 단계: 같은 대화에서 되묻기가 이어지면 제안 없이 도메인 버튼만 준다(선택 강제).

사용자 프로젝트 목록은 이 응답을 만들 때만 쓴다. 라우팅 입력에는 넣지 않는다.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Collection, Mapping
from typing import Literal

from pydantic import BaseModel, ValidationError

from chatcore.deadline import Deadline
from chatcore.errors import AppError
from chatcore.events import DomainChoice, HitlEvent
from chatcore.llm import ChatModel, Usage, system, user
from chatcore.prompts import VersionedFile
from router_service.catalog import Project

log = logging.getLogger(__name__)

CALL_CAP_S = 8.0
MAX_SUGGESTIONS = 3
PLACEHOLDER = re.compile(r"\{unit:([^}]*)\}")

SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {"suggestions": {"type": "array", "items": {"type": "string"}}},
    "required": ["suggestions"],
    "additionalProperties": False,
}


class _Suggestions(BaseModel):
    suggestions: list[str]


def resolve_placeholders(suggestion: str, allowed: Mapping[str, Project]) -> str | None:
    """자리표시자를 프로젝트 이름으로 바꾼다. 허용 목록 밖 자리표시자가 있으면 None."""
    if any(pid not in allowed for pid in PLACEHOLDER.findall(suggestion)):
        return None
    return PLACEHOLDER.sub(lambda m: allowed[m.group(1)].name, suggestion)


class HitlResponder:
    def __init__(self, chat: ChatModel, prompts: VersionedFile, projects: Mapping[str, Project]) -> None:
        self._chat = chat
        self._prompts = prompts
        self._projects = projects
        self._choices = [DomainChoice.model_validate(c) for c in prompts.data["domain_choices"]]

    async def respond(
        self,
        stage: Literal["rewrite", "domain"],
        query: str,
        project_ids: Collection[str],
        deadline: Deadline,
    ) -> tuple[HitlEvent, Usage]:
        if stage == "domain":
            message = self._prompts.data["hitl_message_domain"]
            return HitlEvent(stage="domain", message=message, choices=self._choices), Usage()
        allowed = {pid: self._projects[pid] for pid in project_ids if pid in self._projects}
        suggestions, usage = await self._suggest(query, allowed, deadline)
        return (
            HitlEvent(
                stage="rewrite",
                message=self._prompts.data["hitl_message_rewrite"],
                suggestions=suggestions or list(self._prompts.data["hitl_fallback_suggestions"]),
                choices=self._choices,
            ),
            usage,
        )

    async def _suggest(
        self, query: str, allowed: Mapping[str, Project], deadline: Deadline
    ) -> tuple[list[str], Usage]:
        listing = "\n".join(f"- {{unit:{p.id}}} {p.name} (고객사 {p.account_name})" for p in allowed.values())
        messages = [
            system(self._prompts.data["hitl_system"]),
            user(self._prompts.render("hitl_user", query=query, projects=listing or "(없음)")),
        ]
        try:
            completion = await self._chat.complete(
                messages, task="hitl.suggest", deadline=deadline.child(CALL_CAP_S), json_schema=SUGGEST_SCHEMA
            )
            raw = _Suggestions.model_validate(json.loads(completion.text)).suggestions
        except (AppError, json.JSONDecodeError, ValidationError) as exc:
            log.warning(
                "hitl suggestion failed, using fixed suggestions", extra={"error": type(exc).__name__}
            )
            return [], Usage()
        resolved = [s for s in (resolve_placeholders(x.strip(), allowed) for x in raw) if s]
        return resolved[:MAX_SUGGESTIONS], completion.usage
