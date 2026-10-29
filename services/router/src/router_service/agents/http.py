"""원격 도메인 에이전트 클라이언트(HTTP + SSE).

호출할 때마다 그 에이전트용 단기 토큰을 새로 발급한다(`aud=<에이전트 이름>`). 웹이 받은
라우터용 토큰은 하위 에이전트에서 통과하지 않는다. 연결·응답 실패는 예외 대신 그 채널의
`ErrorEvent` 로 바꿔, 병렬 실행에서 다른 채널을 함께 무너뜨리지 않는다.

httpx 타임아웃은 connect·write·pool 을 짧게 고정하고, read 는 턴 예산의 남은 시간으로 둔다.
스트림 중간의 긴 침묵도 예산을 넘지 못한다.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

import httpx

from chatcore.agent_api import AgentRequest
from chatcore.auth import Principal, TokenIssuer
from chatcore.deadline import Deadline
from chatcore.errors import PUBLIC_MESSAGES, ErrorCode
from chatcore.events import ErrorEvent, Event
from chatcore.sse import iter_events

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 3.0
WRITE_TIMEOUT_S = 5.0
POOL_TIMEOUT_S = 3.0
LIVE_TIMEOUT_S = 1.0


def _error(code: ErrorCode) -> ErrorEvent:
    return ErrorEvent(code=code, message=PUBLIC_MESSAGES[code])


class HttpAgentClient:
    def __init__(
        self, name: str, base_url: str, issuer: TokenIssuer, *, http: httpx.AsyncClient, token_ttl_s: int
    ) -> None:
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._issuer = issuer
        self._http = http
        self._token_ttl_s = token_ttl_s

    async def stream(
        self, req: AgentRequest, principal: Principal, deadline: Deadline
    ) -> AsyncGenerator[Event]:
        token = self._issuer.issue(principal, audience=self.name, ttl_s=self._token_ttl_s)
        timeout = httpx.Timeout(
            connect=CONNECT_TIMEOUT_S, write=WRITE_TIMEOUT_S, pool=POOL_TIMEOUT_S, read=deadline.timeout()
        )
        try:
            async with self._http.stream(
                "POST",
                f"{self._base_url}/v1/stream",
                json=req.model_dump(mode="json"),
                headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
                timeout=timeout,
            ) as resp:
                if resp.status_code != 200:
                    log.warning(
                        "agent rejected request", extra={"agent": self.name, "status": resp.status_code}
                    )
                    yield _error(
                        ErrorCode.FORBIDDEN if resp.status_code == 403 else ErrorCode.AGENT_UNAVAILABLE
                    )
                    return
                async for event in iter_events(resp.aiter_lines()):
                    yield event
        except httpx.ReadTimeout:
            # read 타임아웃 = 턴 예산의 남은 시간이므로, 여기 걸렸다면 예산이 끝난 것이다.
            yield _error(ErrorCode.TIMEOUT)
        except httpx.TransportError as exc:
            log.warning("agent unreachable", extra={"agent": self.name, "detail": repr(exc)})
            yield _error(ErrorCode.AGENT_UNAVAILABLE)

    async def live(self) -> bool:
        try:
            resp = await self._http.get(f"{self._base_url}/health/live", timeout=LIVE_TIMEOUT_S)
        except httpx.HTTPError:
            return False
        return resp.status_code == 200
