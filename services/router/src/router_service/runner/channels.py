"""채널 실행 — 에이전트 스트림 여러 개를 이벤트 한 줄로 합친다.

SINGLE(채널 하나)과 PARALLEL(채널 둘)이 같은 경로를 탄다. 채널마다 pump 태스크를 하나씩 두고
bounded 큐에 넣으며, 꺼내 쓰는 쪽은 이 실행 하나뿐이다. 소비가 느리면 큐가 차서 생산자가
기다린다(백프레셔). 채널이 끝나면 센티넬을 넣는다.

- 채널 오류는 그 채널에서만 끝낸다(`error` → `channel_done(error)`). 다른 채널은 계속 간다.
- 중단 플래그는 이벤트가 없을 때도 `poll_s` 간격으로 확인한다. 확인되면 남은 채널을
  `channel_done(stopped)` 로 닫는다. 턴 예산이 끝나면 남은 채널을 TIMEOUT 오류로 닫는다.
- 어떻게 끝나든 pump 를 취소한다. 취소는 하위 스트림 안까지 전달돼 HTTP 연결도 닫힌다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from chatcore.deadline import Deadline
from chatcore.errors import PUBLIC_MESSAGES, AppError, ErrorCode
from chatcore.events import (
    Answer,
    ChannelDoneEvent,
    ChannelStatus,
    DeltaEvent,
    ErrorEvent,
    Event,
    ProgressEvent,
    Reference,
    ReferencesEvent,
)
from chatcore.llm import Usage

log = logging.getLogger(__name__)

ChannelStream = Callable[[], AsyncGenerator[Event]]
_CHANNEL_EVENTS = (ProgressEvent, ReferencesEvent, DeltaEvent, ChannelDoneEvent, ErrorEvent)


@dataclass
class ChannelState:
    name: str
    chunks: list[str] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    status: ChannelStatus | None = None  # None = 아직 진행 중
    usage: Usage = field(default_factory=Usage)

    def answer(self) -> Answer:
        return Answer(
            channel=self.name,
            text="".join(self.chunks),
            status=self.status or ChannelStatus.STOPPED,
            references=list(self.references),
        )


def _error(channel: str, code: ErrorCode, message: str | None = None) -> ErrorEvent:
    return ErrorEvent(channel=channel, code=code, message=message or PUBLIC_MESSAGES[code])


class ChannelRun:
    def __init__(
        self,
        streams: Mapping[str, ChannelStream],
        *,
        deadline: Deadline,
        should_stop: Callable[[], Awaitable[bool]],
        poll_s: float,
        queue_size: int,
    ) -> None:
        self._streams = dict(streams)
        self._deadline = deadline
        self._should_stop = should_stop
        self._poll_s = poll_s
        self._queue: asyncio.Queue[tuple[str, Event | None]] = asyncio.Queue(maxsize=queue_size)
        self.states = {name: ChannelState(name) for name in streams}
        self.stopped = False

    async def _pump(self, name: str, factory: ChannelStream) -> None:
        stream = factory()
        try:
            async for event in stream:
                if not isinstance(event, _CHANNEL_EVENTS):
                    continue  # 하위 에이전트는 채널 이벤트만 보낼 수 있다
                await self._queue.put((name, event.model_copy(update={"channel": name})))
                if isinstance(event, ChannelDoneEvent | ErrorEvent):
                    break
        except AppError as exc:
            log.warning(
                "channel failed", extra={"channel": name, "code": exc.code.value, "detail": exc.detail}
            )
            await self._queue.put((name, _error(name, exc.code, exc.public_message)))
        except Exception:
            log.exception("channel crashed", extra={"channel": name})
            await self._queue.put((name, _error(name, ErrorCode.INTERNAL)))
        finally:
            await stream.aclose()
        await self._queue.put((name, None))

    def _accept(self, name: str, event: Event) -> list[Event]:
        state = self.states[name]
        match event:
            case ReferencesEvent(items=items):
                state.references.extend(items)
            case DeltaEvent(text=text):
                state.chunks.append(text)
            case ChannelDoneEvent(status=status, usage=usage):
                state.status, state.usage = status, usage
            case ErrorEvent():
                state.status = ChannelStatus.ERROR
                return [event, ChannelDoneEvent(channel=name, status=ChannelStatus.ERROR)]
            case _:
                pass
        return [event]

    def _close_open(self, status: ChannelStatus, code: ErrorCode | None = None) -> list[Event]:
        out: list[Event] = []
        for name, state in self.states.items():
            if state.status is not None:
                continue
            state.status = status
            if code is not None:
                out.append(_error(name, code))
            out.append(ChannelDoneEvent(channel=name, status=status))
        return out

    async def events(self) -> AsyncGenerator[Event]:
        pumps = [asyncio.create_task(self._pump(n, f)) for n, f in self._streams.items()]
        loop = asyncio.get_running_loop()
        last_check = loop.time()
        try:
            while any(s.status is None for s in self.states.values()):
                wait = max(0.0, min(self._poll_s, self._deadline.remaining()))
                try:
                    name, event = await asyncio.wait_for(self._queue.get(), timeout=wait)
                except TimeoutError:
                    pass
                else:
                    if self.states[name].status is None:
                        if event is None:  # 끝 이벤트 없이 스트림이 끝났다
                            event = _error(name, ErrorCode.AGENT_UNAVAILABLE)
                        for out in self._accept(name, event):
                            yield out
                if self._deadline.expired():
                    for out in self._close_open(ChannelStatus.ERROR, ErrorCode.TIMEOUT):
                        yield out
                    return
                if loop.time() - last_check >= self._poll_s:
                    last_check = loop.time()
                    if await self._should_stop():
                        self.stopped = True
                        for out in self._close_open(ChannelStatus.STOPPED):
                            yield out
                        return
        finally:
            for task in pumps:
                task.cancel()
