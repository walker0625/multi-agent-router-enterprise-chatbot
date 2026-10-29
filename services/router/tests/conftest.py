"""라우터 테스트 공용 픽스처. 네트워크·API 키 없이 돈다(fake LLM, 스크립트 에이전트)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

from chatcore.agent_api import AgentRequest
from chatcore.auth import AuthSettings, Principal, generate_keypair
from chatcore.deadline import Deadline
from chatcore.events import (
    ChannelDoneEvent,
    ChannelStatus,
    DeltaEvent,
    Event,
    ProgressEvent,
    Reference,
    ReferencesEvent,
)
from chatcore.llm import FakeChatModel, HashingEmbedder, LLMSettings, Usage
from router_service.deps import Deps
from router_service.fakes import fake_handlers
from router_service.routing import Domain
from router_service.runtime import Runtime, build_runtime
from router_service.settings import Settings


class ScriptedAgent:
    """미리 정한 이벤트를 흘리는 에이전트. 받은 요청을 기록한다."""

    def __init__(self, events: Sequence[Event], *, delay_s: float = 0.0) -> None:
        self.events = list(events)
        self.delay_s = delay_s
        self.requests: list[AgentRequest] = []
        self.closed = False

    async def stream(
        self, req: AgentRequest, principal: Principal, deadline: Deadline
    ) -> AsyncGenerator[Event]:
        self.requests.append(req)
        try:
            for event in self.events:
                if self.delay_s:
                    await asyncio.sleep(self.delay_s)
                yield event
        finally:
            self.closed = True


def answer_events(text: str, *ref_ids: str, status: ChannelStatus = ChannelStatus.OK) -> list[Event]:
    half = len(text) // 2
    return [
        ProgressEvent(message="자료 검색 중", percent=30),
        ReferencesEvent(items=[Reference(id=r, source="mail", title=f"제목 {r}") for r in ref_ids]),
        DeltaEvent(text=text[:half]),
        DeltaEvent(text=text[half:]),
        ChannelDoneEvent(status=status, usage=Usage(input_tokens=100, output_tokens=20)),
    ]


@pytest.fixture
def make_settings(tmp_path: Path) -> Callable[..., Settings]:
    private, public = generate_keypair()
    (tmp_path / "jwt_private.pem").write_bytes(private)
    (tmp_path / "jwt_public.pem").write_bytes(public)

    def make(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "app_env": "test",
            "db_path": str(tmp_path / "router.db"),
            "auth": AuthSettings(
                private_key_path=tmp_path / "jwt_private.pem", public_key_path=tmp_path / "jwt_public.pem"
            ),
            "llm": LLMSettings(provider="fake"),
        }
        values.update(overrides)
        return Settings(**values)

    return make


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings()


@pytest.fixture
def chat() -> FakeChatModel:
    return FakeChatModel(fake_handlers())


@pytest.fixture
def agents() -> dict[Domain, ScriptedAgent]:
    return {
        Domain.DELIVERY: ScriptedAgent(
            answer_events("P-101 재고 실사 개발이 14일 지연되고 있습니다. [1](cite:ML-0004)", "ML-0004")
        ),
        Domain.SALES: ScriptedAgent(
            answer_events(
                "새솔물류는 1차 견적 대비 10% 인하를 요청했습니다. [1](cite:meeting:M-008)", "meeting:M-008"
            )
        ),
    }


@pytest.fixture
def deps(chat: FakeChatModel, agents: dict[Domain, ScriptedAgent]) -> Deps:
    return Deps(chat=chat, embedder=HashingEmbedder(512), agents=dict(agents))


@pytest.fixture
async def make_runtime(
    make_settings: Callable[..., Settings], deps: Deps
) -> AsyncIterator[Callable[..., Awaitable[Runtime]]]:
    created: list[Runtime] = []

    async def make(**overrides: Any) -> Runtime:
        rt = await build_runtime(make_settings(**overrides), deps)
        created.append(rt)
        return rt

    yield make
    for rt in created:
        await rt.aclose()


@pytest.fixture
async def runtime(make_runtime: Callable[..., Awaitable[Runtime]]) -> Runtime:
    return await make_runtime()


@pytest.fixture
def write_yaml(tmp_path: Path) -> Callable[[str, dict[str, Any]], Path]:
    def write(name: str, data: dict[str, Any]) -> Path:
        path = tmp_path / name
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return path

    return write


@pytest.fixture
def scripted() -> type[ScriptedAgent]:
    return ScriptedAgent


@pytest.fixture
def answer() -> Callable[..., list[Event]]:
    return answer_events
