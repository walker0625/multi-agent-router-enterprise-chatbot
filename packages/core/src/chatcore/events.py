"""스트림 이벤트 계약 — 라우터↔웹, 도메인 에이전트↔라우터가 같은 모델을 쓴다.

이벤트는 Pydantic 모델이고 SSE 문자열로는 HTTP 경계(`chatcore.sse`)에서 한 번만
바꾼다. 웹의 TypeScript 타입과 `docs/api-contract.md` 는 이 모델의 JSON Schema 에서
생성한다(`scripts/export_contract.py`).

- 도메인 에이전트는 `progress` · `delta` · `references` 를 보내고 `channel_done` 또는
  `error` 로 끝낸다. `channel` 은 비워 두고, 라우터가 전달하면서 채운다.
- 라우터는 `turn` 으로 시작해 `done` 으로 끝낸다. `done` 은 저장된 턴과 같은 내용이다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from chatcore.errors import ErrorCode
from chatcore.llm.types import Usage

SCHEMA_VERSION = "1.0"


class Outcome(StrEnum):
    SINGLE = "SINGLE"
    PARALLEL = "PARALLEL"
    HITL = "HITL"
    BLOCKED = "BLOCKED"


class ChannelStatus(StrEnum):
    OK = "ok"
    TRUNCATED = "truncated"
    ERROR = "error"
    STOPPED = "stopped"


class TurnStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    HITL = "hitl"
    BLOCKED = "blocked"
    ERROR = "error"
    STOPPED = "stopped"
    DISCONNECTED = "disconnected"


class Reference(BaseModel):
    """답변 근거 카드. 본문의 `[N](cite:<id>)` 마커가 `id` 를 가리킨다."""

    id: str
    source: str  # mail · note · attachment · account · contact · meeting · faq · web
    title: str
    snippet: str = ""
    date: str | None = None
    url: str | None = None
    score: float | None = None
    meta: dict[str, str] = Field(default_factory=dict)


class DomainChoice(BaseModel):
    domain: str
    label: str
    description: str


class TurnEvent(BaseModel):
    type: Literal["turn"] = "turn"
    chat_id: str
    turn_id: str
    turn_no: int


class RouteEvent(BaseModel):
    type: Literal["route"] = "route"
    outcome: Outcome
    agents: list[str]
    decided_by: str  # L1 · L2 · L3 · forced · replay · guard


class ProgressEvent(BaseModel):
    type: Literal["progress"] = "progress"
    channel: str | None = None
    message: str
    percent: int = Field(ge=0, le=100)


class DeltaEvent(BaseModel):
    type: Literal["delta"] = "delta"
    channel: str | None = None
    text: str


class ReferencesEvent(BaseModel):
    type: Literal["references"] = "references"
    channel: str | None = None
    items: list[Reference]


class ChannelDoneEvent(BaseModel):
    type: Literal["channel_done"] = "channel_done"
    channel: str | None = None
    status: ChannelStatus
    usage: Usage = Field(default_factory=Usage)


class HitlEvent(BaseModel):
    """되묻기. `suggestions` 는 바로 보낼 수 있는 재질문, `choices` 는 도메인 버튼."""

    type: Literal["hitl"] = "hitl"
    stage: Literal["rewrite", "domain"]
    message: str
    suggestions: list[str] = Field(default_factory=list)
    choices: list[DomainChoice] = Field(default_factory=list)


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    channel: str | None = None
    code: ErrorCode
    message: str


class Answer(BaseModel):
    channel: str
    text: str
    status: ChannelStatus
    references: list[Reference] = Field(default_factory=list)


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    turn_id: str
    status: TurnStatus
    outcome: Outcome | None = None
    answers: list[Answer] = Field(default_factory=list)
    hitl: HitlEvent | None = None
    recommendations: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0


Event = Annotated[
    TurnEvent
    | RouteEvent
    | ProgressEvent
    | DeltaEvent
    | ReferencesEvent
    | ChannelDoneEvent
    | HitlEvent
    | ErrorEvent
    | DoneEvent,
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


def parse_event(data: str | bytes) -> Event:
    return EVENT_ADAPTER.validate_json(data)
