"""영업 에이전트 프롬프트와 메시지 구성.

지시문은 `prompts.yaml` 에 두고 파일 태그를 턴 로그에 남긴다. 메시지 순서는
시스템(지시 + canary + 오늘 날짜) → 사용자 지침(user, 래핑) → 직전 대화 → 이번 질문이다.
사용자 지침은 system 이 아니라 user 메시지로 넣어 규칙·권한을 바꾸지 못하게 한다.

답변 단계는 계획 단계의 도구 대화를 그대로 넘기지 않고, 실제로 쓴 검색 결과만 근거 블록으로
묶어 새로 구성한다. 모델이 보는 근거와 화면의 근거 카드가 같은 집합이 되고, 도구 정의 없이
도구 메시지를 보내는 공급자 호환 문제도 생기지 않는다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from chatcore.agent_api import AgentRequest
from chatcore.guard import canary_instruction, wrap_user_instructions
from chatcore.llm import Message, assistant, system, user
from chatcore.prompts import VersionedFile

PROMPTS_PATH = Path(__file__).with_name("prompts.yaml")

# 근거 블록 경계. fake 답변 핸들러도 이 표식으로 근거를 찾는다.
EVIDENCE_OPEN = "<search_results>"
EVIDENCE_CLOSE = "</search_results>"


class SalesPrompts:
    def __init__(self, file: VersionedFile) -> None:
        self.file = file

    @classmethod
    def load(cls, path: Path = PROMPTS_PATH) -> SalesPrompts:
        return cls(VersionedFile.load(path))

    @property
    def tag(self) -> str:
        return self.file.tag

    @property
    def tool_description(self) -> str:
        return self.file.data["tool_description"]

    def plan_messages(self, req: AgentRequest, *, canary: str, today: date) -> list[Message]:
        instructions = self.file.render(
            "plan_system", today=today.isoformat(), canary=canary_instruction(canary)
        )
        return [*self._preamble(instructions, req), user(req.query)]

    def answer_messages(
        self, req: AgentRequest, *, canary: str, today: date, evidence: list[dict[str, Any]]
    ) -> list[Message]:
        instructions = self.file.render(
            "answer_system", today=today.isoformat(), canary=canary_instruction(canary)
        )
        block = f"{EVIDENCE_OPEN}\n{json.dumps(evidence, ensure_ascii=False, indent=1)}\n{EVIDENCE_CLOSE}"
        question = self.file.render("answer_user", question=req.query, evidence=block)
        return [*self._preamble(instructions, req), user(question)]

    def _preamble(self, instructions: str, req: AgentRequest) -> list[Message]:
        messages = [system(instructions)]
        if req.system_prompt:
            messages.append(user(wrap_user_instructions(req.system_prompt)))
        for turn in req.history:
            messages += [user(turn.question), assistant(turn.answer)]
        return messages
