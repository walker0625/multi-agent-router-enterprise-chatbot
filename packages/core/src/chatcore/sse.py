"""SSE 직렬화·역직렬화와 하트비트 응답."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from chatcore.events import Event, parse_event

HEARTBEAT = b": ping\n\n"


def encode(event: BaseModel) -> bytes:
    kind = getattr(event, "type", "message")
    return f"event: {kind}\ndata: {event.model_dump_json()}\n\n".encode()


async def with_heartbeat(source: AsyncIterator[bytes], interval_s: float) -> AsyncIterator[bytes]:
    """`interval_s` 동안 이벤트가 없으면 주석 프레임을 끼운다.

    다음 이벤트를 기다리는 태스크는 취소하지 않고 계속 기다린다 — 타임아웃으로 취소하면
    감싼 제너레이터 안의 진행 중인 호출까지 끊긴다.
    """
    it = aiter(source)
    pending: asyncio.Task[bytes] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(it))
            done, _ = await asyncio.wait({pending}, timeout=interval_s)
            if not done:
                yield HEARTBEAT
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            finally:
                pending = None
            yield chunk
    finally:
        if pending is not None:
            pending.cancel()
            with contextlib.suppress(BaseException):
                await pending
        aclose = getattr(it, "aclose", None)
        if aclose is not None:
            await aclose()


def event_stream_response(
    events: AsyncIterator[BaseModel], *, heartbeat_s: float = 15.0
) -> StreamingResponse:
    async def body() -> AsyncIterator[bytes]:
        async for event in events:
            yield encode(event)

    return StreamingResponse(
        with_heartbeat(body(), heartbeat_s),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def iter_events(lines: AsyncIterator[str]) -> AsyncIterator[Event]:
    """SSE 줄 스트림(httpx `aiter_lines`)을 이벤트 모델로 바꾼다. 주석·빈 프레임은 건너뛴다."""
    data: list[str] = []
    async for line in lines:
        if line == "":
            if data:
                yield parse_event("\n".join(data))
                data = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if field == "data":
            data.append(value[1:] if value.startswith(" ") else value)
    if data:
        yield parse_event("\n".join(data))
