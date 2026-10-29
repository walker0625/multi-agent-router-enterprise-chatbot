import json
from collections.abc import Callable

import httpx
import pytest

from chatcore.agent_api import AgentRequest
from chatcore.auth import Principal, TokenIssuer, TokenVerifier, generate_keypair
from chatcore.deadline import Deadline
from chatcore.errors import ErrorCode
from chatcore.events import ChannelDoneEvent, ChannelStatus, DeltaEvent, ErrorEvent, Event
from chatcore.sse import encode
from router_service.agents import HttpAgentClient

PRIVATE, PUBLIC = generate_keypair()
PRINCIPAL = Principal(sub="u-02", name="이도윤", project_ids=["P-101"])


async def collect(handler: Callable[[httpx.Request], httpx.Response]) -> list[Event]:
    issuer = TokenIssuer(PRIVATE, issuer="nuri-router")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HttpAgentClient("delivery-agent", "http://delivery/", issuer, http=http, token_ttl_s=60)
        return [e async for e in client.stream(AgentRequest(query="일정"), PRINCIPAL, Deadline(5))]


async def test_streams_events_with_agent_scoped_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://delivery/v1/stream"
        token = request.headers["authorization"].removeprefix("Bearer ")
        verified = TokenVerifier(PUBLIC, issuer="nuri-router", audience="delivery-agent").verify(token)
        assert verified == PRINCIPAL
        assert json.loads(request.content)["query"] == "일정"
        body = (
            encode(DeltaEvent(text="답")) + b": ping\n\n" + encode(ChannelDoneEvent(status=ChannelStatus.OK))
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    events = await collect(handler)
    assert [e.type for e in events] == ["delta", "channel_done"]


@pytest.mark.parametrize(
    ("status", "code"),
    [(403, ErrorCode.FORBIDDEN), (503, ErrorCode.AGENT_UNAVAILABLE), (500, ErrorCode.AGENT_UNAVAILABLE)],
)
async def test_http_errors_become_error_events(status: int, code: ErrorCode) -> None:
    events = await collect(lambda _req: httpx.Response(status, json={"code": "x"}))
    assert len(events) == 1 and isinstance(events[0], ErrorEvent) and events[0].code is code


async def test_connection_failure_becomes_agent_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    [event] = await collect(handler)
    assert isinstance(event, ErrorEvent) and event.code is ErrorCode.AGENT_UNAVAILABLE


async def test_live_check() -> None:
    issuer = TokenIssuer(PRIVATE, issuer="nuri-router")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "down":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        up = HttpAgentClient("sales-agent", "http://up", issuer, http=http, token_ttl_s=60)
        down = HttpAgentClient("sales-agent", "http://down", issuer, http=http, token_ttl_s=60)
        assert await up.live() and not await down.live()
