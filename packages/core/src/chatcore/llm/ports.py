"""LLM·임베딩 포트.

서비스 코드는 SDK 를 직접 부르지 않고 이 Protocol 에만 의존한다. 구현은 앱 팩토리에서
한 번 만들어 주입한다 — OpenAI 호환 구현(`openai_compat`)과 키 없이 도는 결정적
구현(`fake`) 두 가지가 있다.

`task` 는 호출 목적 라벨이다(예: "route.judge", "delivery.synthesize"). 토큰 사용량을
단계별로 집계하는 키이자, fake 구현이 응답 규칙을 고르는 키다.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from typing import Any, Protocol

from chatcore.deadline import Deadline
from chatcore.llm.types import Completion, Message, StreamChunk, ToolSpec


class ChatModel(Protocol):
    @property
    def name(self) -> str: ...

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        task: str,
        deadline: Deadline,
        json_schema: dict[str, Any] | None = None,
        tools: Sequence[ToolSpec] | None = None,
        max_tokens: int | None = None,
    ) -> Completion: ...

    def stream(
        self,
        messages: Sequence[Message],
        *,
        task: str,
        deadline: Deadline,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[StreamChunk]: ...


class Embedder(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
