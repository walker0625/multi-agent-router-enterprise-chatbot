"""라우터 → 도메인 에이전트 요청 계약.

도메인 에이전트는 대화를 저장하지 않는다. 라우터가 필요한 만큼의 직전 대화를 잘라서
넘긴다. 항목별·전체 길이를 모두 제한해 한 요청이 컨텍스트를 독점하지 못하게 한다.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, Field, model_validator

MAX_HISTORY_CHARS = 12_000


class HistoryTurn(BaseModel):
    question: str = Field(max_length=2_000)
    answer: str = Field(max_length=8_000)


class AgentRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=6)
    project_ids: list[str] = Field(default_factory=list, max_length=50)
    system_prompt: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def _history_budget(self) -> Self:
        total = sum(len(t.question) + len(t.answer) for t in self.history)
        if total > MAX_HISTORY_CHARS:
            raise ValueError(f"history too long: {total} > {MAX_HISTORY_CHARS} chars")
        return self
