"""HTTP 진입점 — 토큰 검증, SSE 스트림, 헬스.

공개키 읽기와 데이터 적재는 lifespan 에서 한다(임포트·앱 생성 시점에는 I/O 가 없다). 토큰은
`aud=sales-agent` 로만 받아, 라우터용·다른 에이전트용 토큰을 들고 와도 통과하지 않는다.
클라이언트 연결이 끊기면 응답 제너레이터가 취소되고, 그 취소가 진행 중인 LLM·검색 호출까지
전파된다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from chatcore.agent_api import AgentRequest
from chatcore.auth import Principal, TokenVerifier, bearer_token
from chatcore.http import BodyLimitMiddleware, RequestIdMiddleware, install_error_handlers
from chatcore.sse import event_stream_response
from sales_agent.deps import Deps, build_deps
from sales_agent.orchestrator import run_turn
from sales_agent.settings import Settings

AUDIENCE = "sales-agent"

router = APIRouter()


def current_principal(request: Request, token: Annotated[str, Depends(bearer_token)]) -> Principal:
    verifier: TokenVerifier = request.app.state.verifier
    return verifier.verify(token)


@router.post("/v1/stream")
async def stream(
    body: AgentRequest, request: Request, principal: Annotated[Principal, Depends(current_principal)]
) -> StreamingResponse:
    # project_ids 는 수행 자료 범위라 영업 자료 검색에는 쓰지 않는다.
    return event_stream_response(run_turn(body, principal, request.app.state.deps))


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    deps: Deps | None = getattr(request.app.state, "deps", None)
    if deps is None:
        return JSONResponse({"status": "starting"}, status_code=503)
    return JSONResponse({"status": "ready", "prompts": deps.prompts.tag})


def create_app(settings: Settings | None = None, deps: Deps | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        public_key = settings.auth.public_key_path.read_bytes()
        app.state.verifier = TokenVerifier(public_key, issuer=settings.auth.issuer, audience=AUDIENCE)
        app.state.deps = deps or await build_deps(settings)
        yield

    app = FastAPI(title="sales-agent", lifespan=lifespan)
    install_error_handlers(app)
    app.add_middleware(BodyLimitMiddleware, max_bytes=64_000)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(router)
    return app
