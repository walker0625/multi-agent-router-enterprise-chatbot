"""앱 팩토리.

`create_app` 은 설정만 읽고 I/O 를 하지 않는다. 데이터 로딩·인덱스 생성·저장소 연결은 lifespan
에서 `build_runtime` 이 한다. 미들웨어는 순수 ASGI(요청 ID, 본문 크기 제한)와 CORS 다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chatcore.http import BodyLimitMiddleware, RequestIdMiddleware, install_error_handlers
from router_service.api import auth, chats, health, turns
from router_service.deps import Deps, build_deps
from router_service.runtime import build_runtime
from router_service.settings import Settings

MAX_BODY_BYTES = 64_000


def create_app(settings: Settings | None = None, deps: Deps | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = await build_runtime(settings, deps or build_deps(settings))
        app.state.runtime = runtime
        try:
            yield
        finally:
            await runtime.aclose()

    app = FastAPI(title="누리솔루션 라우터", lifespan=lifespan)
    install_error_handlers(app)
    for module in (auth, chats, turns, health):
        app.include_router(module.router)
    # CORS 허용 출처는 설정 한 곳에서만 관리한다.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
    )
    app.add_middleware(BodyLimitMiddleware, max_bytes=MAX_BODY_BYTES)
    app.add_middleware(RequestIdMiddleware)
    return app
