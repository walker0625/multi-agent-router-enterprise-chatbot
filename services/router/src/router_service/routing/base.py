"""라우팅 공통 타입.

레이어는 `route(query, deadline) -> LayerResult` 하나만 노출한다. 위 레이어의 점수나 판단은
아래 레이어로 넘어가지 않고, 모든 레이어가 같은 질의를 받는다. `skipped`(설정이 없어 돌지
않음)와 `undecided`(돌았지만 정하지 못함)를 구분해 trace 에 남긴다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from chatcore.deadline import Deadline
from chatcore.events import Outcome
from chatcore.llm import Usage


class Domain(StrEnum):
    DELIVERY = "delivery"
    SALES = "sales"
    GENERAL = "general"


LayerStatus = Literal["decided", "undecided", "skipped"]


class LayerResult(BaseModel):
    layer: str
    status: LayerStatus
    outcome: Outcome | None = None
    domains: list[Domain] = Field(default_factory=list)
    reason: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)  # trace 용: 매칭 키워드, top-3 점수, 판정 목록 등
    usage: Usage = Field(default_factory=Usage)


class Layer(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def tags(self) -> list[str]: ...  # 이 레이어가 읽은 YAML 파일 태그

    async def route(self, query: str, deadline: Deadline) -> LayerResult: ...


class RouteTrace(BaseModel):
    layers: list[LayerResult]
    tags: list[str]
    chat_model: str
    embedder: str
    elapsed_ms: int


class RouteResult(BaseModel):
    outcome: Outcome
    domains: list[Domain]
    decided_by: str  # L1 · L2 · L3, 아무 레이어도 정하지 못하면 cascade
    query: str  # 입구 병합 후 질의
    original_query: str
    trace: RouteTrace
    usage: Usage = Field(default_factory=Usage)  # 입구 병합 + 레이어 LLM 호출 합계
