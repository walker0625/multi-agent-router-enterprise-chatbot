"""용어사전 질의 확장.

질의에 사내 약어·용어가 나오면 그 바로 뒤에 괄호로 별칭을 끼운다("CR 승인" → "CR(변경요청,
변경 요청서) 승인"). 확장 질의는 검색 재현율에만 쓰고, 리랭크·답변은 사용자가 쓴 원문으로 한다.

- 대소문자·공백은 무시하고, 앞뒤가 다른 글자와 이어지면 매칭하지 않는다("CRM" 의 CR 은 아님).
  한글 용어 뒤에는 조사가 바로 붙으므로("검수를") 오른쪽 경계는 영숫자만 본다.
- 뜻이 둘인 약어(PO·PM)는 LLM 이 이 질의에서의 뜻을 고른다. 실패하거나 시간이 넘으면 그 용어는
  확장하지 않는다 — 틀린 별칭을 붙이는 것보다 원문 그대로가 낫다.
- 확장은 검색을 돕는 부가 단계라 어떤 경우에도 예외를 올리지 않는다. 실패하면 원문을 쓴다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from chatcore.deadline import Deadline
from chatcore.llm import ChatModel, Usage, system, user
from chatcore.prompts import VersionedFile
from delivery_agent.cache import TTLCache

log = logging.getLogger(__name__)

DISAMBIGUATE_TIMEOUT_S = 3.0

_SCHEMA = {
    "type": "object",
    "properties": {"choice": {"type": "string", "enum": ["A", "B"]}},
    "required": ["choice"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class GlossaryTerm:
    term: str
    aliases: tuple[str, ...]
    description: str
    ambiguous_with: str | None = None


@dataclass(frozen=True)
class Expansion:
    text: str
    usage: Usage = field(default_factory=Usage)


def load_glossary(path: Path) -> list[GlossaryTerm]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        GlossaryTerm(t["term"], tuple(t["aliases"]), t["description"], t.get("ambiguous_with")) for t in raw
    ]


def term_pattern(term: str) -> re.Pattern[str]:
    body = r"\s*".join(re.escape(c) for c in term if not c.isspace())
    right = r"(?![0-9A-Za-z])" if term[-1].isascii() and term[-1].isalnum() else ""
    return re.compile(rf"(?<![0-9A-Za-z가-힣]){body}{right}", re.IGNORECASE)


class GlossaryExpander:
    def __init__(
        self,
        terms: Sequence[GlossaryTerm],
        chat: ChatModel,
        prompts: VersionedFile,
        *,
        disambiguate_timeout_s: float = DISAMBIGUATE_TIMEOUT_S,
    ) -> None:
        self._terms = [(t, term_pattern(t.term)) for t in terms]
        self._chat = chat
        self._prompts = prompts
        self._timeout_s = disambiguate_timeout_s
        self._cache: TTLCache[str, str] = TTLCache(maxsize=512, ttl_s=600)

    async def expand(self, query: str, deadline: Deadline) -> Expansion:
        try:
            return await self._expand(query, deadline)
        except Exception:
            log.warning("glossary expansion failed; using the original query", exc_info=True)
            return Expansion(query)

    async def _expand(self, query: str, deadline: Deadline) -> Expansion:
        if (cached := self._cache.get(query)) is not None:
            return Expansion(cached)
        found = [(t, m) for t, pattern in self._terms if (m := pattern.search(query))]
        ambiguous = [t for t, _ in found if t.ambiguous_with]
        decisions = await asyncio.gather(*(self._decide(t, query, deadline) for t in ambiguous))
        keep = {t.term: chosen for t, (chosen, _) in zip(ambiguous, decisions, strict=True)}
        text = query
        # 뒤쪽부터 끼워 넣어야 앞쪽 매칭 위치가 밀리지 않는다.
        for term, match in sorted(found, key=lambda tm: tm[1].end(), reverse=True):
            if keep.get(term.term, True):
                text = f"{text[: match.end()]}({', '.join(term.aliases)}){text[match.end() :]}"
        if all(chosen is not None for chosen, _ in decisions):
            self._cache.put(query, text)  # 판정에 실패한 결과는 캐시하지 않고 다음 요청에서 다시 묻는다
        return Expansion(text, sum((u for _, u in decisions), Usage()))

    async def _decide(self, term: GlossaryTerm, query: str, deadline: Deadline) -> tuple[bool | None, Usage]:
        """용어사전의 뜻(A)이면 True, 다른 뜻(B)이면 False, 고르지 못하면 None."""
        try:
            step = deadline.child(self._timeout_s)
            messages = [
                system(self._prompts.render("glossary_system")),
                user(
                    self._prompts.render(
                        "glossary_user",
                        term=term.term,
                        primary=f"{term.description} ({', '.join(term.aliases)})",
                        other=term.ambiguous_with,
                        query=query,
                    )
                ),
            ]
            completion = await step.run(
                self._chat.complete(
                    messages, task="delivery.glossary_disambiguate", deadline=step, json_schema=_SCHEMA
                )
            )
            choice = json.loads(completion.text)["choice"]
            if choice not in ("A", "B"):
                raise ValueError(f"unexpected choice: {choice!r}")
        except Exception as exc:
            log.info("glossary disambiguation failed", extra={"term": term.term, "error": type(exc).__name__})
            return None, Usage()
        return choice == "A", completion.usage
