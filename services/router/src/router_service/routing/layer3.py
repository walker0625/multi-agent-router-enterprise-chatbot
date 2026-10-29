"""L3 — LLM 도메인별 YES/NO 판정.

확신도 점수는 받지 않는다. LLM 이 스스로 매긴 확신도는 보정되지 않은 자기 보고라 임계값으로
쓸 수 없다. 도메인마다 YES/NO 와 근거 한 줄만 받고, 결과는 이진 규칙으로 정한다.
- 근거가 빈 YES 는 NO 로 본다(근거 없이 찍은 답은 판정으로 인정하지 않는다).
- general YES 가 업무 도메인 YES 와 함께 나오면 general 을 뺀다. 업무 자료가 필요한 질문을
  일반 답변으로 흘리지 않는다.
- YES 0개 = HITL, 1개 = SINGLE, 2개 이상 = PARALLEL.

JSON 이 깨지면 같은 예산 안에서 최대 2번 다시 묻고, 그래도 안 되면 결정하지 않는다(→ HITL).
안전 필터는 BLOCKED 로 결정한다. 시간 초과는 이 레이어의 판단 실패가 아니라 턴 전체의 TIMEOUT
이라 잡지 않고 위로 올린다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ValidationError, model_validator

from chatcore.deadline import Deadline
from chatcore.errors import ContentFiltered, ProviderError, ProviderTimeout
from chatcore.events import Outcome
from chatcore.llm import ChatModel, Message, Usage, assistant, system, user
from chatcore.prompts import VersionedFile
from router_service.routing.base import Domain, LayerResult

log = logging.getLogger(__name__)

MAX_RETRIES = 2

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "enum": [d.value for d in Domain]},
                    "answer": {"type": "string", "enum": ["YES", "NO"]},
                    "reason": {"type": "string"},
                },
                "required": ["domain", "answer", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


class Verdict(BaseModel):
    domain: Domain
    answer: Literal["YES", "NO"]
    reason: str


class JudgeOutput(BaseModel):
    verdicts: list[Verdict]

    @model_validator(mode="after")
    def _one_per_domain(self) -> Self:
        if sorted(v.domain for v in self.verdicts) != sorted(Domain):
            raise ValueError("verdicts must contain exactly one entry per domain")
        return self


def decide(output: JudgeOutput) -> tuple[Outcome, list[Domain]]:
    yes = [v.domain for v in output.verdicts if v.answer == "YES" and v.reason.strip()]
    if Domain.GENERAL in yes and len(yes) > 1:
        yes.remove(Domain.GENERAL)
    order = list(Domain)
    yes.sort(key=order.index)
    if not yes:
        return Outcome.HITL, []
    return (Outcome.SINGLE if len(yes) == 1 else Outcome.PARALLEL), yes


class JudgeLayer:
    def __init__(self, chat: ChatModel, file: VersionedFile) -> None:
        self._chat = chat
        self._file = file
        self._prefix = self._build_prefix(file)

    @classmethod
    def load(cls, chat: ChatModel, path: Path) -> JudgeLayer:
        return cls(chat, VersionedFile.load(path))

    @property
    def name(self) -> str:
        return "L3"

    @property
    def tags(self) -> list[str]:
        return [self._file.tag]

    @staticmethod
    def _build_prefix(file: VersionedFile) -> list[Message]:
        data = file.data
        blocks = []
        for name, spec in data["domains"].items():
            blocks.append(
                f"[{Domain(name).value}] {spec['label']}\n- 다루는 것: {spec['covers'].strip()}\n"
                f"- 다루지 않는 것: {spec['excludes'].strip()}"
            )
        messages = [system(data["instruction"].strip() + "\n\n" + "\n\n".join(blocks))]
        for shot in data["few_shots"]:
            verdicts = JudgeOutput.model_validate({"verdicts": shot["verdicts"]})
            messages += [user(shot["query"]), assistant(verdicts.model_dump_json())]
        return messages

    async def route(self, query: str, deadline: Deadline) -> LayerResult:
        messages = [*self._prefix, user(query)]
        usage = Usage()
        errors: list[str] = []
        for _attempt in range(1 + MAX_RETRIES):
            try:
                completion = await self._chat.complete(
                    messages, task="route.judge", deadline=deadline, json_schema=JUDGE_SCHEMA
                )
            except ContentFiltered:
                return LayerResult(
                    layer=self.name,
                    status="decided",
                    outcome=Outcome.BLOCKED,
                    reason="content_filtered",
                    usage=usage,
                )
            except (ProviderError, ProviderTimeout) as exc:
                errors.append(type(exc).__name__)
                continue
            usage += completion.usage
            try:
                output = JudgeOutput.model_validate(json.loads(completion.text))
            except (json.JSONDecodeError, ValidationError) as exc:
                errors.append(type(exc).__name__)
                continue
            outcome, domains = decide(output)
            return LayerResult(
                layer=self.name,
                status="decided",
                outcome=outcome,
                domains=domains,
                reason="no_domain" if outcome is Outcome.HITL else "judge",
                detail={"verdicts": output.model_dump(mode="json")["verdicts"], "errors": errors},
                usage=usage,
            )
        log.warning("L3 judge failed", extra={"errors": errors})
        return LayerResult(
            layer=self.name, status="undecided", reason="judge_failed", detail={"errors": errors}, usage=usage
        )
