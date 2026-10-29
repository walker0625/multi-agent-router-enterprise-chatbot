"""HTTP 경계 — 토큰 검증, 권한 범위 검사, 그래프 실행을 SSE 로 내보내기.

권한 위반(요청한 `project_ids` 가 토큰 범위 밖)은 스트림을 열기 전에 403 으로 막는다. 스트림이 열린
뒤의 실패는 `error` 이벤트 하나로 알린다(`graph.stream_events`). 데이터 적재와 인덱스 생성은
lifespan 에서 하므로 앱을 만들거나 임포트하는 시점에는 I/O 가 없다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from chatcore.agent_api import AgentRequest
from chatcore.auth import Principal, bearer_token
from chatcore.errors import Forbidden
from chatcore.http import BodyLimitMiddleware, RequestIdMiddleware, install_error_handlers
from chatcore.logs import configure_logging
from chatcore.sse import event_stream_response
from delivery_agent.deps import Deps, build_deps
from delivery_agent.graph import build_graph, stream_events
from delivery_agent.settings import Settings

router = APIRouter()


def _install(app: FastAPI, deps: Deps) -> None:
    app.state.deps = deps
    app.state.graph = build_graph(deps)


def _principal(request: Request, token: Annotated[str, Depends(bearer_token)]) -> Principal:
    deps: Deps = request.app.state.deps
    return deps.verifier.verify(token)


@router.post("/v1/stream")
async def stream(
    body: AgentRequest, request: Request, principal: Annotated[Principal, Depends(_principal)]
) -> StreamingResponse:
    outside = set(body.project_ids) - set(principal.project_ids)
    if outside:
        raise Forbidden(f"project_ids outside token scope: {sorted(outside)}")
    scope = body.project_ids or principal.project_ids
    return event_stream_response(stream_events(request.app.state.graph, body, scope))


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    if request.app.state.deps is None:
        return JSONResponse({"status": "loading"}, status_code=503)
    return JSONResponse({"status": "ready"})


def create_app(settings: Settings | None = None, deps: Deps | None = None) -> FastAPI:
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging("delivery-agent")
        if app.state.deps is None:
            _install(app, await build_deps(resolved))
        yield

    app = FastAPI(title="delivery-agent", lifespan=lifespan)
    app.state.deps = None
    app.state.graph = None
    if deps is not None:
        _install(app, deps)
    install_error_handlers(app)
    app.add_middleware(BodyLimitMiddleware, max_bytes=64_000)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(router)
    return app
