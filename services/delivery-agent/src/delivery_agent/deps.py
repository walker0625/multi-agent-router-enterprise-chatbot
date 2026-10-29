"""포트 묶음과 조립.

서비스 코드는 `Deps` 에 담긴 포트(LLM·임베딩·인덱스·리랭커·토큰 검증기)에만 의존한다. 실제 조립은
`build_deps` 한 곳에서 하고(lifespan 에서 호출), 테스트는 fake 를 넣은 `Deps` 를 직접 만든다.
리랭커는 fake 모드에서 `LexicalReranker`, openai 모드에서 `LLMReranker` 이고, 근거 품질 기준도
그 리랭커의 점수 척도에 맞춘 값을 고른다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from chatcore.auth import TokenVerifier
from chatcore.llm import ChatModel, Embedder, build_llm
from chatcore.prompts import VersionedFile
from chatcore.search import LexicalReranker, LLMReranker, Reranker
from delivery_agent.fakes import fake_handlers
from delivery_agent.glossary import GlossaryTerm, load_glossary
from delivery_agent.indexes import DeliveryIndexes, build_indexes
from delivery_agent.settings import Settings

AUDIENCE = "delivery-agent"
PROMPTS_PATH = Path(__file__).with_name("prompts.yaml")


@dataclass
class Deps:
    chat: ChatModel
    embedder: Embedder
    indexes: DeliveryIndexes
    reranker: Reranker
    verifier: TokenVerifier
    prompts: VersionedFile
    glossary: Sequence[GlossaryTerm]
    min_score: float  # 근거 품질 기준(리랭크 최고 점수)
    today: Callable[[], date] = date.today


async def build_deps(settings: Settings) -> Deps:
    prompts = VersionedFile.load(PROMPTS_PATH)
    chat, embedder = build_llm(settings.llm, fake_handlers(prompts))
    lexical = settings.llm.provider == "fake"
    return Deps(
        chat=chat,
        embedder=embedder,
        indexes=await build_indexes(settings.data_dir, embedder),
        reranker=LexicalReranker() if lexical else LLMReranker(chat, task="delivery.rerank"),
        verifier=TokenVerifier(
            settings.auth.public_key_path.read_bytes(), issuer=settings.auth.issuer, audience=AUDIENCE
        ),
        prompts=prompts,
        glossary=load_glossary(settings.data_dir / "delivery" / "glossary.json"),
        min_score=float(prompts.data["grade_min_score"]["lexical" if lexical else "llm"]),
    )
