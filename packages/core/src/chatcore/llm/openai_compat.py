"""OpenAI 호환 API 구현 (OpenAI · Azure OpenAI v1 · Ollama 등 base_url 로 전환).

공급자 오류는 여기서 타입 오류로 바꾼다. 안전 필터는 오류 메시지 문자열이 아니라
응답의 `finish_reason` 과 오류 `code` 로 판정한다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import openai
from openai import AsyncOpenAI

from chatcore.deadline import Deadline
from chatcore.errors import AppError, ContentFiltered, ProviderError, ProviderTimeout
from chatcore.llm.types import Completion, FinishReason, Message, StreamChunk, ToolCall, ToolSpec, Usage

_FINISH: dict[str, FinishReason] = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
    "content_filter": "content_filter",
}


def _translate(exc: Exception) -> AppError:
    if isinstance(exc, openai.APITimeoutError):
        return ProviderTimeout(str(exc))
    if isinstance(exc, openai.BadRequestError) and getattr(exc, "code", None) == "content_filter":
        return ContentFiltered(str(exc))
    return ProviderError(f"{type(exc).__name__}: {exc}")


def _usage(raw: Any) -> Usage:
    if raw is None:
        return Usage()
    return Usage(input_tokens=raw.prompt_tokens or 0, output_tokens=raw.completion_tokens or 0)


class OpenAIChatModel:
    def __init__(self, client: AsyncOpenAI, model: str, *, call_timeout_s: float = 30.0) -> None:
        self._client = client
        self._model = model
        self._call_timeout_s = call_timeout_s

    @property
    def name(self) -> str:
        return self._model

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
        kwargs: dict[str, Any] = {}
        if json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": task.replace(".", "_"), "schema": json_schema},
            }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ]
        if max_tokens is not None:
            kwargs["max_completion_tokens"] = max_tokens
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[m.to_openai() for m in messages],  # type: ignore[misc]
                timeout=deadline.timeout(self._call_timeout_s),
                **kwargs,
            )
        except openai.OpenAIError as exc:
            raise _translate(exc) from exc
        choice = resp.choices[0]
        finish = _FINISH.get(choice.finish_reason or "stop", "stop")
        if finish == "content_filter":
            raise ContentFiltered("finish_reason=content_filter")
        calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "{}")
            for c in (choice.message.tool_calls or [])
            if c.type == "function"
        ]
        return Completion(
            text=choice.message.content or "",
            tool_calls=calls,
            finish_reason=finish,
            usage=_usage(resp.usage),
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        task: str,
        deadline: Deadline,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        kwargs: dict[str, Any] = {}
        if max_tokens is not None:
            kwargs["max_completion_tokens"] = max_tokens
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[m.to_openai() for m in messages],  # type: ignore[misc]
                stream=True,
                stream_options={"include_usage": True},
                timeout=deadline.timeout(self._call_timeout_s),
                **kwargs,
            )
            finish: FinishReason = "stop"
            usage = Usage()
            async for chunk in resp:
                if chunk.usage is not None:
                    usage = _usage(chunk.usage)
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish = _FINISH.get(choice.finish_reason, "stop")
                if choice.delta and choice.delta.content:
                    yield StreamChunk(text=choice.delta.content)
        except openai.OpenAIError as exc:
            raise _translate(exc) from exc
        if finish == "content_filter":
            raise ContentFiltered("finish_reason=content_filter")
        yield StreamChunk(finish_reason=finish, usage=usage)


class OpenAIEmbedder:
    def __init__(self, client: AsyncOpenAI, model: str, dim: int) -> None:
        self._client = client
        self._model = model
        self._dim = dim

    @property
    def name(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        try:
            for start in range(0, len(texts), 256):
                resp = await self._client.embeddings.create(
                    model=self._model, input=list(texts[start : start + 256]), dimensions=self._dim
                )
                out.extend(d.embedding for d in resp.data)
        except openai.OpenAIError as exc:
            raise _translate(exc) from exc
        return out
