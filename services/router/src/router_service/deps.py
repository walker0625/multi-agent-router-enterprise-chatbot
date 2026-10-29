"""주입할 포트 묶음. 테스트는 fake 를 넣고, 실행 시에는 `build_deps(settings)` 가 설정대로 만든다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from chatcore.llm import ChatModel, Embedder, build_llm
from router_service.agents import AgentClient, DemoWebSearch, WebSearch
from router_service.fakes import fake_handlers
from router_service.routing import Domain
from router_service.settings import Settings


@dataclass
class Deps:
    chat: ChatModel
    embedder: Embedder
    agents: Mapping[Domain, AgentClient] = field(default_factory=dict)  # 비운 도메인은 기본 클라이언트를 쓴다
    web_search: WebSearch = field(default_factory=DemoWebSearch)


def build_deps(settings: Settings) -> Deps:
    chat, embedder = build_llm(settings.llm, fake_handlers())
    return Deps(chat=chat, embedder=embedder)
