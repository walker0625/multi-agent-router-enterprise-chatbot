"""영업 에이전트 테스트 공용 픽스처. 네트워크·API 키 없이 fake LLM 과 실제 데모 데이터로 돈다."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import date

import pytest

from chatcore.agent_api import AgentRequest
from chatcore.auth import Principal
from chatcore.deadline import Deadline
from chatcore.events import Event
from chatcore.filters import Filter
from chatcore.llm import LLMSettings
from chatcore.search import Document, Hit, SearchIndex
from sales_agent.context import RequestContext, TurnBudget
from sales_agent.deps import Deps, build_deps
from sales_agent.orchestrator import run_turn
from sales_agent.permissions import meeting_scope
from sales_agent.retrieval import Retriever
from sales_agent.settings import DEFAULT_DATA_DIR, Settings

TODAY = date(2026, 10, 29)


def load_principal(sub: str) -> Principal:
    users = json.loads((DEFAULT_DATA_DIR / "users.json").read_text(encoding="utf-8"))
    return Principal.model_validate(next(u for u in users if u["sub"] == sub))


@pytest.fixture
def seller() -> Principal:
    return load_principal("u-01")  # S1 비공개 회의록 + 연락처 원문


@pytest.fixture
def outsider() -> Principal:
    return load_principal("u-03")  # 공개 정보만


@pytest.fixture
def s2_lead() -> Principal:
    return load_principal("u-04")  # S2 비공개 회의록 + 연락처 원문


@pytest.fixture
def settings() -> Settings:
    return Settings(app_env="test", llm=LLMSettings(provider="fake"))


@pytest.fixture
async def deps(settings: Settings) -> Deps:
    return await build_deps(settings)


@pytest.fixture
def retriever(deps: Deps) -> Retriever:
    return Retriever(deps.indexes, deps.embedder, deps.cipher)


@pytest.fixture
def make_ctx() -> Callable[..., RequestContext]:
    def make(principal: Principal, **budget: int) -> RequestContext:
        return RequestContext(principal, meeting_scope(principal), TurnBudget(Deadline(30), **budget))

    return make


class RecordingIndex:
    """검색 호출을 (태스크, 인덱스, 필터)로 기록한다. 지연을 주면 동시 요청이 서로 끼어든다."""

    def __init__(self, inner: SearchIndex, log: list[tuple[str, str, Filter | None]], delay_s: float) -> None:
        self._inner = inner
        self._log = log
        self._delay_s = delay_s

    @property
    def name(self) -> str:
        return self._inner.name

    async def search(
        self,
        *,
        text: str | None = None,
        vector: Sequence[float] | None = None,
        filter: Filter | None = None,
        top_k: int = 10,
    ) -> list[Hit]:
        task = asyncio.current_task()
        self._log.append((task.get_name() if task else "", self.name, filter))
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        return await self._inner.search(text=text, vector=vector, filter=filter, top_k=top_k)

    async def get(self, ids: Sequence[str]) -> list[Document]:
        return await self._inner.get(ids)


SearchLog = list[tuple[str, str, Filter | None]]


@pytest.fixture
def recorded(deps: Deps) -> Callable[..., tuple[Deps, SearchLog]]:
    def make(delay_s: float = 0.0) -> tuple[Deps, SearchLog]:
        log: SearchLog = []
        idx = deps.indexes
        wrapped = replace(
            idx,
            accounts=RecordingIndex(idx.accounts, log, delay_s),
            contacts=RecordingIndex(idx.contacts, log, delay_s),
            meetings=RecordingIndex(idx.meetings, log, delay_s),
            attachments=RecordingIndex(idx.attachments, log, delay_s),
        )
        return replace(deps, indexes=wrapped), log

    return make


@pytest.fixture
def turn() -> Callable[[str, Principal, Deps], Awaitable[list[Event]]]:
    async def run(query: str, principal: Principal, deps: Deps) -> list[Event]:
        events: AsyncIterator[Event] = run_turn(AgentRequest(query=query), principal, deps, today=TODAY)
        return [e async for e in events]

    return run
