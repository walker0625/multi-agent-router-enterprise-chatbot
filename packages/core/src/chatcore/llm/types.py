from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]
FinishReason = Literal["stop", "length", "tool_calls", "content_filter"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: str  # 모델이 준 JSON 문자열 그대로. 파싱·검증은 도구 실행기가 한다.


class Message(BaseModel):
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None

    def to_openai(self) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            data["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in self.tool_calls
            ]
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        return data


def system(content: str) -> Message:
    return Message(role="system", content=content)


def user(content: str) -> Message:
    return Message(role="user", content=content)


def assistant(content: str) -> Message:
    return Message(role="assistant", content=content)


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class Completion(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: FinishReason = "stop"
    usage: Usage = Field(default_factory=Usage)


class StreamChunk(BaseModel):
    """스트림 조각. 마지막 조각만 `finish_reason` 과 `usage` 를 가진다."""

    text: str = ""
    finish_reason: FinishReason | None = None
    usage: Usage | None = None
