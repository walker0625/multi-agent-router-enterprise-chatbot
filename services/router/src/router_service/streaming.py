"""라우터가 직접 만드는 답변(일반 답변·병렬 요약)의 스트리밍.

출력은 모두 `OutputGuard` 를 지난다. 시스템 프롬프트에 심은 canary 가 출력에 나오면 지시문
유출로 보고 멈추고(`OutputBlocked`), 길이 상한을 넘으면 잘라 `truncated` 로 끝낸다. 모델이
`length` 로 끝난 경우도 `truncated` 다.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Sequence

from chatcore.deadline import Deadline
from chatcore.events import ChannelDoneEvent, ChannelStatus, DeltaEvent
from chatcore.guard import OutputGuard, canary_instruction, make_canary
from chatcore.llm import ChatModel, Message, Usage


async def guarded_stream(
    chat: ChatModel,
    build_messages: Callable[[str], Sequence[Message]],
    *,
    task: str,
    deadline: Deadline,
    max_chars: int,
) -> AsyncGenerator[DeltaEvent | ChannelDoneEvent]:
    """`build_messages(canary_instruction)` 로 만든 대화를 스트리밍한다."""
    canary = make_canary()
    guard = OutputGuard(canary, max_chars=max_chars)
    finish, usage = "stop", Usage()
    messages = build_messages(canary_instruction(canary))
    # 토큰 상한은 비용 상한일 뿐이다. 화면에 나가는 길이는 가드의 글자 상한이 정한다.
    async for chunk in chat.stream(messages, task=task, deadline=deadline, max_tokens=max_chars):
        if chunk.text and (shown := guard.feed(chunk.text)):
            yield DeltaEvent(text=shown)
        if chunk.finish_reason:
            finish, usage = chunk.finish_reason, chunk.usage or Usage()
    if tail := guard.flush():
        yield DeltaEvent(text=tail)
    truncated = finish == "length" or guard.truncated
    yield ChannelDoneEvent(status=ChannelStatus.TRUNCATED if truncated else ChannelStatus.OK, usage=usage)
