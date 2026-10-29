from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Self

from openai import AsyncOpenAI
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from chatcore.llm.fake import FakeChatModel, FakeHandler, HashingEmbedder
from chatcore.llm.openai_compat import OpenAIChatModel, OpenAIEmbedder
from chatcore.llm.ports import ChatModel, Embedder


class LLMSettings(BaseSettings):
    """`LLM_PROVIDER=fake`(기본)면 키 없이 동작한다. `openai` 면 base_url 로 호환 API 를 고른다."""

    model_config = SettingsConfigDict(env_prefix="LLM_", extra="ignore")

    provider: Literal["fake", "openai"] = "fake"
    base_url: str | None = None
    api_key: SecretStr | None = None
    chat_model: str | None = None
    embedding_model: str | None = None
    embedding_dim: int = 512
    call_timeout_s: float = 30.0
    max_retries: int = 1
    fake_chunk_delay_s: float = 0.0

    @model_validator(mode="after")
    def _require_openai_fields(self) -> Self:
        if self.provider == "openai":
            missing = [
                name
                for name, value in (
                    ("LLM_API_KEY", self.api_key),
                    ("LLM_CHAT_MODEL", self.chat_model),
                    ("LLM_EMBEDDING_MODEL", self.embedding_model),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"LLM_PROVIDER=openai 에 필요한 값이 없습니다: {', '.join(missing)}")
        return self


def build_llm(
    settings: LLMSettings, fake_handlers: Mapping[str, FakeHandler] | None = None
) -> tuple[ChatModel, Embedder]:
    if settings.provider == "fake":
        chat = FakeChatModel(fake_handlers, chunk_delay_s=settings.fake_chunk_delay_s)
        return chat, HashingEmbedder(settings.embedding_dim)
    assert settings.api_key and settings.chat_model and settings.embedding_model
    client = AsyncOpenAI(
        api_key=settings.api_key.get_secret_value(),
        base_url=settings.base_url,
        max_retries=settings.max_retries,
    )
    return (
        OpenAIChatModel(client, settings.chat_model, call_timeout_s=settings.call_timeout_s),
        OpenAIEmbedder(client, settings.embedding_model, settings.embedding_dim),
    )
