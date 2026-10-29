import asyncio
from collections.abc import Awaitable, Callable
from datetime import date

import pytest
from pydantic import BaseModel

from chatcore.agent_api import AgentRequest
from chatcore.auth import TokenVerifier, generate_keypair
from chatcore.llm import ChatModel, FakeChatModel, HashingEmbedder
from chatcore.prompts import VersionedFile
from chatcore.search import LexicalReranker, Reranker
from delivery_agent.deps import AUDIENCE, PROMPTS_PATH, Deps
from delivery_agent.fakes import fake_handlers
from delivery_agent.glossary import load_glossary
from delivery_agent.graph import build_graph, stream_events
from delivery_agent.indexes import DeliveryIndexes, build_indexes
from delivery_agent.settings import Settings

TODAY = date(2026, 10, 29)
DATA_DIR = Settings().data_dir
EMBED_DIM = 512

MakeDeps = Callable[..., Deps]
Collect = Callable[..., Awaitable[list[BaseModel]]]


@pytest.fixture(scope="session")
def prompts() -> VersionedFile:
    return VersionedFile.load(PROMPTS_PATH)


@pytest.fixture(scope="session")
def indexes() -> DeliveryIndexes:
    # 인덱스는 읽기 전용이라 세션에서 한 번 만들어 같이 쓴다.
    return asyncio.run(build_indexes(DATA_DIR, HashingEmbedder(EMBED_DIM)))


@pytest.fixture(scope="session")
def keypair() -> tuple[bytes, bytes]:
    return generate_keypair()


@pytest.fixture
def make_deps(prompts: VersionedFile, indexes: DeliveryIndexes, keypair: tuple[bytes, bytes]) -> MakeDeps:
    def factory(
        *,
        chat: ChatModel | None = None,
        index_set: DeliveryIndexes | None = None,
        reranker: Reranker | None = None,
    ) -> Deps:
        return Deps(
            chat=chat or FakeChatModel(fake_handlers(prompts)),
            embedder=HashingEmbedder(EMBED_DIM),
            indexes=index_set or indexes,
            reranker=reranker or LexicalReranker(),
            verifier=TokenVerifier(keypair[1], issuer="nuri-router", audience=AUDIENCE),
            prompts=prompts,
            glossary=load_glossary(DATA_DIR / "delivery" / "glossary.json"),
            min_score=float(prompts.data["grade_min_score"]["lexical"]),
            today=lambda: TODAY,
        )

    return factory


@pytest.fixture
def collect() -> Collect:
    async def run(deps: Deps, query: str, scope: list[str], **request: object) -> list[BaseModel]:
        graph = build_graph(deps)
        req = AgentRequest.model_validate({"query": query, **request})
        return [e async for e in stream_events(graph, req, scope)]

    return run
