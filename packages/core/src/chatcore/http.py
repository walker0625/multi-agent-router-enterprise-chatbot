"""FastAPI 공통 배선 — 오류 직렬화, 요청 ID, 본문 크기 제한.

미들웨어는 순수 ASGI 로 작성했다. `BaseHTTPMiddleware` 는 SSE 응답을 감싸면서 스트림
취소·백프레셔를 흐리게 만든다.
"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from chatcore.errors import PUBLIC_MESSAGES, AppError, ErrorCode
from chatcore.logs import request_id_var

log = logging.getLogger(__name__)


def error_body(code: ErrorCode, message: str | None = None) -> dict[str, str]:
    return {"code": code.value, "message": message or PUBLIC_MESSAGES[code]}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        log.warning("request failed", extra={"code": exc.code.value, "detail": exc.detail})
        return JSONResponse(error_body(exc.code, exc.public_message), status_code=exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()]
        return JSONResponse({**error_body(ErrorCode.INVALID_REQUEST), "fields": fields}, status_code=422)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", exc_info=exc)
        return JSONResponse(error_body(ErrorCode.INTERNAL), status_code=500)


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        rid = headers.get(b"x-request-id", b"").decode()[:64] or uuid.uuid4().hex
        token = request_id_var.set(rid)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)


class BodyLimitMiddleware:
    """선언된 길이와 실제로 읽은 길이를 모두 확인한다."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await _reject(send)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _TooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _TooLarge:
            await _reject(send)


class _TooLarge(Exception):
    pass


async def _reject(send: Send) -> None:
    body = json.dumps(error_body(ErrorCode.PAYLOAD_TOO_LARGE), ensure_ascii=False).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
