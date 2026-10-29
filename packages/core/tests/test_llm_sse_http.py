import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from chatcore.agent_api import AgentRequest, HistoryTurn
from chatcore.deadline import Deadline
from chatcore.errors import ContentFiltered, NotFound
from chatcore.events import ChannelStatus, DeltaEvent, DoneEvent, TurnStatus, parse_event
from chatcore.http import BodyLimitMiddleware, RequestIdMiddleware, install_error_handlers
from chatcore.llm import FakeChatModel, FakeReply, LLMSettings, user
from chatcore.sse import HEARTBEAT, encode, iter_events, with_heartbeat


async def test_fake_model_uses_task_handler_and_streams() -> None:
    chat = FakeChatModel({"greet": lambda req: f"안녕하세요, {req.last_user}"}, chunk_chars=3)
    parts = [c async for c in chat.stream([user("민우")], task="greet", deadline=Deadline(5))]
    assert "".join(p.text for p in parts) == "안녕하세요, 민우"
    assert parts[-1].finish_reason == "stop" and parts[-1].usage is not None


async def test_fake_model_reports_length_truncation() -> None:
    chat = FakeChatModel({"long": lambda _req: FakeReply(text="가" * 100)})
    result = await chat.complete([user("x")], task="long", deadline=Deadline(5), max_tokens=10)
    assert result.finish_reason == "length"


async def test_fake_content_filter_is_typed_error() -> None:
    with pytest.raises(ContentFiltered):
        await FakeChatModel().complete([user("#blocked")], task="t", deadline=Deadline(5))


def test_openai_provider_requires_credentials() -> None:
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        LLMSettings(provider="openai")


async def test_sse_roundtrip() -> None:
    events = [
        DeltaEvent(channel="sales", text="첫 줄\n둘째 줄"),
        DoneEvent(turn_id="t1", status=TurnStatus.DONE),
    ]

    async def lines() -> AsyncIterator[str]:
        for e in events:
            for line in encode(e).decode().split("\n"):
                yield line

    parsed = [e async for e in iter_events(lines())]
    assert parsed == events


async def test_heartbeat_does_not_cancel_slow_producer() -> None:
    async def slow() -> AsyncIterator[bytes]:
        await asyncio.sleep(0.12)
        yield b"data"

    out = [chunk async for chunk in with_heartbeat(slow(), 0.05)]
    assert out[-1] == b"data"
    assert HEARTBEAT in out


def test_event_parsing_rejects_unknown_type() -> None:
    with pytest.raises(ValueError):
        parse_event(json.dumps({"type": "nope"}))
    assert ChannelStatus("truncated") is ChannelStatus.TRUNCATED


def test_agent_request_limits_history() -> None:
    long_turn = HistoryTurn(question="q" * 2000, answer="a" * 8000)
    with pytest.raises(ValueError):
        AgentRequest(query="질문", history=[long_turn, long_turn])


class Body(BaseModel):
    text: str


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/echo")
    async def echo(body: Body) -> dict[str, str]:
        return {"text": body.text}

    @app.get("/missing")
    async def missing() -> None:
        raise NotFound("doc 42 missing in store")

    app.add_middleware(BodyLimitMiddleware, max_bytes=64)
    app.add_middleware(RequestIdMiddleware)
    return app


def test_http_wiring() -> None:
    client = TestClient(_app())
    ok = client.post("/echo", json={"text": "hi"}, headers={"x-request-id": "abc"})
    assert ok.json() == {"text": "hi"} and ok.headers["x-request-id"] == "abc"

    too_big = client.post("/echo", json={"text": "x" * 200})
    assert too_big.status_code == 413 and too_big.json()["code"] == "PAYLOAD_TOO_LARGE"

    missing = client.get("/missing")
    assert missing.status_code == 404
    assert "doc 42" not in missing.text  # 내부 원인은 응답에 나가지 않는다

    invalid = client.post("/echo", json={"wrong": 1})
    assert invalid.status_code == 422 and invalid.json()["code"] == "INVALID_REQUEST"
