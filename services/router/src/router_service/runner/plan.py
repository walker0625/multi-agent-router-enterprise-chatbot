"""실행 계획(plan)과 그 출처.

일반 질문·강제 도메인·다시 생성은 plan 을 만드는 방법만 다르고 실행은 같다. 출처마다 파이프라인을
복사하지 않도록 plan 만들기를 이 출처들로 나누고, 실행기는 하나만 둔다.
- `CascadeSource`: 일반 질문. 입구 병합 → 3단 캐스케이드.
- `ForcedSource`: 웹이 도메인을 지정했거나 HITL 도메인 버튼을 눌렀을 때. 라우팅하지 않는다.
- `ReplaySource`: 다시 생성. 저장된 plan 을 그대로 쓰고 다시 라우팅하지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, Field

from chatcore.agent_api import HistoryTurn
from chatcore.deadline import Deadline
from chatcore.events import Outcome
from chatcore.llm import Usage
from router_service.routing import Cascade, Domain, RouteTrace


class Plan(BaseModel):
    outcome: Outcome
    domains: list[Domain]
    decided_by: str
    query: str  # 에이전트에 보낼 질의(입구 병합 후)
    original_query: str
    project_ids: list[str] = Field(default_factory=list)  # 요청한 범위. 비우면 토큰 범위 전체
    trace: RouteTrace | None = None


@dataclass
class Planned:
    plan: Plan
    usage: Usage = field(default_factory=Usage)


class PlanSource(Protocol):
    @property
    def progress_message(self) -> str | None: ...  # 라우팅을 실제로 할 때만 "질문 분석 중"을 띄운다

    async def plan(self, history: Sequence[HistoryTurn], deadline: Deadline) -> Planned: ...


class CascadeSource:
    def __init__(self, cascade: Cascade, query: str, project_ids: list[str]) -> None:
        self._cascade = cascade
        self._query = query
        self._project_ids = project_ids

    @property
    def progress_message(self) -> str | None:
        return "질문 분석 중"

    async def plan(self, history: Sequence[HistoryTurn], deadline: Deadline) -> Planned:
        route = await self._cascade.route(self._query, deadline, history=history)
        plan = Plan(
            outcome=route.outcome,
            domains=route.domains,
            decided_by=route.decided_by,
            query=route.query,
            original_query=route.original_query,
            project_ids=self._project_ids,
            trace=route.trace,
        )
        return Planned(plan, route.usage)


class ForcedSource:
    def __init__(self, domain: Domain, query: str, project_ids: list[str]) -> None:
        self._plan = Plan(
            outcome=Outcome.SINGLE,
            domains=[domain],
            decided_by="forced",
            query=query,
            original_query=query,
            project_ids=project_ids,
        )

    @property
    def progress_message(self) -> str | None:
        return None

    async def plan(self, history: Sequence[HistoryTurn], deadline: Deadline) -> Planned:
        return Planned(self._plan)


class ReplaySource:
    def __init__(self, plan: Plan) -> None:
        self._plan = plan.model_copy(update={"decided_by": "replay"})

    @property
    def progress_message(self) -> str | None:
        return None

    async def plan(self, history: Sequence[HistoryTurn], deadline: Deadline) -> Planned:
        return Planned(self._plan)
