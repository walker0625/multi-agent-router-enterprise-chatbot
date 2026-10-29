"""일반 에이전트 — 사용법·사내 규정 질문을 라우터 안에서 답한다.

FAQ 를 메모리 인덱스(BM25 + 벡터)로 찾고, 질의 토큰이 충분히 겹치는 FAQ 만 근거로 쓴다. 근거가
없으면 웹 검색 포트를 부른다. 웹 검색은 데모용 고정 결과만 있고, 결과에 그 사실을 밝힌다.
출력은 도메인 에이전트와 같은 이벤트 계약(progress → references → delta → channel_done)을 따른다.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from typing import Protocol

from chatcore.agent_api import AgentRequest
from chatcore.auth import Principal
from chatcore.deadline import Deadline
from chatcore.events import Event, ProgressEvent, Reference, ReferencesEvent
from chatcore.guard import wrap_user_instructions
from chatcore.llm import ChatModel, Embedder, Message, assistant, system, user
from chatcore.prompts import VersionedFile
from chatcore.search import Document, InMemoryIndex, LexicalReranker, embed_documents
from router_service.catalog import FaqItem
from router_service.streaming import guarded_stream

TOP_K = 3


class WebSearch(Protocol):
    async def search(self, query: str, deadline: Deadline) -> list[Reference]: ...


class DemoWebSearch:
    """실제 웹을 검색하지 않는다. 데모용 고정 결과를 주고 결과 자체에 그 사실을 적는다."""

    async def search(self, query: str, deadline: Deadline) -> list[Reference]:
        deadline.check()
        return [
            Reference(
                id="web:demo-1",
                source="web",
                title="[데모] 웹 검색 결과",
                snippet=(
                    "데모 환경이라 실제 웹 검색은 하지 않았습니다. 운영 환경에서는 이 자리에 "
                    f"'{query[:40]}'에 대한 공개 웹 검색 결과가 들어갑니다."
                ),
                url="https://example.com/search",
            )
        ]


class GeneralAgent:
    def __init__(
        self,
        chat: ChatModel,
        embedder: Embedder,
        index: InMemoryIndex,
        web: WebSearch,
        prompts: VersionedFile,
        *,
        max_chars: int,
        min_overlap: float,
    ) -> None:
        self._chat = chat
        self._embedder = embedder
        self._index = index
        self._web = web
        self._prompts = prompts
        self._max_chars = max_chars
        self._min_overlap = min_overlap
        self._reranker = LexicalReranker()

    @classmethod
    async def build(
        cls,
        faq: Sequence[FaqItem],
        *,
        chat: ChatModel,
        embedder: Embedder,
        web: WebSearch,
        prompts: VersionedFile,
        max_chars: int,
        min_overlap: float,
    ) -> GeneralAgent:
        docs = [
            Document(id=item.id, text=f"{item.q}\n{item.a}", fields={"q": item.q, "a": item.a})
            for item in faq
        ]
        await embed_documents(docs, embedder)
        index = InMemoryIndex("faq", docs, filterable=())
        return cls(chat, embedder, index, web, prompts, max_chars=max_chars, min_overlap=min_overlap)

    async def _references(self, query: str, deadline: Deadline) -> list[Reference]:
        [vector] = await deadline.run(self._embedder.embed([query]))
        hits = await self._index.search(text=query, vector=vector, top_k=TOP_K)
        overlaps = (await self._reranker.rerank(query, [h.doc for h in hits], deadline=deadline)).scores
        refs = [
            Reference(
                id=h.doc.id,
                source="faq",
                title=h.doc.fields["q"],
                snippet=h.doc.fields["a"][:160],
                score=round(overlap, 3),
            )
            for h, overlap in zip(hits, overlaps, strict=True)
            if overlap >= self._min_overlap
        ]
        return refs or await self._web.search(query, deadline)

    async def stream(
        self, req: AgentRequest, principal: Principal, deadline: Deadline
    ) -> AsyncGenerator[Event]:
        yield ProgressEvent(message="사용 안내 자료 찾는 중", percent=30)
        refs = await self._references(req.query, deadline)
        yield ReferencesEvent(items=refs)
        evidence = "\n".join(f"[{i}] ({r.id}) {r.title} — {r.snippet}" for i, r in enumerate(refs, 1))

        def build(canary: str) -> list[Message]:
            messages = [system(self._prompts.render("general_system", canary=canary))]
            for turn in req.history:
                messages += [user(turn.question), assistant(turn.answer)]
            if req.system_prompt:
                messages.append(user(wrap_user_instructions(req.system_prompt)))
            messages.append(user(self._prompts.render("general_user", evidence=evidence, query=req.query)))
            return messages

        async for event in guarded_stream(
            self._chat, build, task="general.answer", deadline=deadline, max_chars=self._max_chars
        ):
            yield event
