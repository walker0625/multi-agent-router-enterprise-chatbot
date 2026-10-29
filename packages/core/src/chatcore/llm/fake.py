"""API 키 없이 결정적으로 동작하는 LLM·임베딩.

로컬 데모와 테스트가 같은 구현을 쓴다. `FakeChatModel` 은 `task` 별 핸들러로 응답을
만들며, 핸들러는 각 서비스가 자기 업무에 맞게 등록한다(라우팅 판정, 답변 합성 등).
`HashingEmbedder` 는 글자 n-gram 해싱 벡터라 의미가 아니라 표기 유사도를 잰다 —
오프라인 데모에는 충분하고, 실제 모델로 바꾸면 그대로 의미 유사도가 된다.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from chatcore.deadline import Deadline
from chatcore.errors import ContentFiltered
from chatcore.llm.types import Completion, FinishReason, Message, StreamChunk, ToolCall, ToolSpec, Usage


@dataclass
class FakeRequest:
    task: str
    messages: Sequence[Message]
    json_schema: dict[str, Any] | None = None
    tools: Sequence[ToolSpec] | None = None
    max_tokens: int | None = None

    @property
    def last_user(self) -> str:
        for m in reversed(self.messages):
            if m.role == "user":
                return m.content
        return ""

    @property
    def system_text(self) -> str:
        return "\n".join(m.content for m in self.messages if m.role == "system")


@dataclass
class FakeReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: FinishReason = "stop"


FakeHandler = Callable[[FakeRequest], FakeReply | str]

# 데모에서 안전 필터 경로를 확인하기 위한 표식. 실제 모델에서는 공급자가 판정한다.
CONTENT_FILTER_TRIGGER = "#blocked"


def _default_handler(req: FakeRequest) -> FakeReply:
    return FakeReply(text=f"(fake:{req.task}) {req.last_user[:80]}")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 2)


class FakeChatModel:
    def __init__(
        self,
        handlers: Mapping[str, FakeHandler] | None = None,
        *,
        chunk_chars: int = 8,
        chunk_delay_s: float = 0.0,
    ) -> None:
        self._handlers = dict(handlers or {})
        self._chunk_chars = chunk_chars
        self._chunk_delay_s = chunk_delay_s
        self.calls: list[FakeRequest] = []

    @property
    def name(self) -> str:
        return "fake-chat"

    def register(self, task: str, handler: FakeHandler) -> None:
        self._handlers[task] = handler

    def _reply(self, req: FakeRequest) -> FakeReply:
        self.calls.append(req)
        if CONTENT_FILTER_TRIGGER in req.last_user:
            raise ContentFiltered("fake content filter trigger")
        handler = self._handlers.get(req.task, _default_handler)
        reply = handler(req)
        reply = FakeReply(text=reply) if isinstance(reply, str) else reply
        if req.max_tokens is not None and estimate_tokens(reply.text) > req.max_tokens:
            reply = FakeReply(text=reply.text[: req.max_tokens * 2], finish_reason="length")
        return reply

    def _usage(self, req: FakeRequest, reply: FakeReply) -> Usage:
        prompt = sum(estimate_tokens(m.content) for m in req.messages)
        return Usage(input_tokens=prompt, output_tokens=estimate_tokens(reply.text))

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        task: str,
        deadline: Deadline,
        json_schema: dict[str, Any] | None = None,
        tools: Sequence[ToolSpec] | None = None,
        max_tokens: int | None = None,
    ) -> Completion:
        deadline.check()
        req = FakeRequest(task, list(messages), json_schema, tools, max_tokens)
        reply = self._reply(req)
        finish: FinishReason = "tool_calls" if reply.tool_calls else reply.finish_reason
        return Completion(
            text=reply.text, tool_calls=reply.tool_calls, finish_reason=finish, usage=self._usage(req, reply)
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        task: str,
        deadline: Deadline,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        deadline.check()
        req = FakeRequest(task, list(messages), max_tokens=max_tokens)
        reply = self._reply(req)
        text = reply.text
        for start in range(0, len(text), self._chunk_chars):
            if self._chunk_delay_s:
                await asyncio.sleep(self._chunk_delay_s)
            deadline.check()
            yield StreamChunk(text=text[start : start + self._chunk_chars])
        yield StreamChunk(finish_reason=reply.finish_reason, usage=self._usage(req, reply))


_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")


class HashingEmbedder:
    """글자 2·3-gram 을 해시해 고정 차원에 뿌린 L2 정규화 벡터."""

    def __init__(self, dim: int = 512) -> None:
        self._dim = dim

    @property
    def name(self) -> str:
        return f"hashing-{self._dim}"

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]

    def embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in _TOKEN.findall(text.lower()):
            padded = f" {token} "
            for n in (2, 3):
                for i in range(len(padded) - n + 1):
                    digest = hashlib.blake2b(padded[i : i + n].encode(), digest_size=8).digest()
                    h = int.from_bytes(digest, "big")
                    vec[h % self._dim] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec
