import asyncio
import re
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from chatcore.agent_api import AgentRequest
from chatcore.deadline import Deadline
from chatcore.errors import ErrorCode, ProviderError, SearchFailed
from chatcore.events import (
    ChannelDoneEvent,
    ChannelStatus,
    DeltaEvent,
    ErrorEvent,
    ProgressEvent,
    ReferencesEvent,
)
from chatcore.filters import And, Filter, In, Range
from chatcore.llm import Completion, FakeChatModel, FakeReply, HashingEmbedder, Message, StreamChunk, ToolSpec
from chatcore.prompts import VersionedFile
from chatcore.search import Document, Hit, Reranked, SearchIndex
from delivery_agent.deps import Deps
from delivery_agent.fakes import fake_handlers
from delivery_agent.graph import build_graph, search_sources, stream_events
from delivery_agent.indexes import DeliveryIndexes, Source

MakeDeps = Callable[..., Deps]
Collect = Callable[..., Any]

U02 = ["P-101", "P-102", "P-103"]


@dataclass
class SpyIndex:
    """실제 인덱스를 감싸 호출 인자를 기록한다. `fail` 이면 검색이 실패한다."""

    inner: SearchIndex
    fail: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.inner.name

    async def search(
        self,
        *,
        text: str | None = None,
        vector: Sequence[float] | None = None,
        filter: Filter | None = None,
        top_k: int = 10,
    ) -> list[Hit]:
        self.calls.append({"text": text, "filter": filter, "top_k": top_k})
        if self.fail:
            raise SearchFailed(f"{self.name} down")
        return await self.inner.search(text=text, vector=vector, filter=filter, top_k=top_k)

    async def get(self, ids: Sequence[str]) -> list[Document]:
        return await self.inner.get(ids)


def _spied(
    indexes: DeliveryIndexes, failing: frozenset[str] = frozenset()
) -> tuple[DeliveryIndexes, list[SpyIndex]]:
    spies = [SpyIndex(s.index, fail=s.index.name in failing) for s in indexes.sources]
    sources = tuple(Source(spy, s.dated) for spy, s in zip(spies, indexes.sources, strict=True))
    return DeliveryIndexes(sources, indexes.project_names), spies


def _of[T](events: list[BaseModel], kind: type[T]) -> list[T]:
    return [e for e in events if isinstance(e, kind)]


def _answer(events: list[BaseModel]) -> str:
    return "".join(e.text for e in _of(events, DeltaEvent))


def _filter_has(f: Filter | None, kind: type) -> bool:
    if isinstance(f, And):
        return any(_filter_has(i, kind) for i in f.items)
    return isinstance(f, kind)


# --- retrieve -------------------------------------------------------------------------------------


async def test_documents_outside_scope_never_come_back(make_deps: MakeDeps, collect: Collect) -> None:
    index_set, spies = _spied(make_deps().indexes)
    # 한빛유통(P-101) 자료를 정확히 가리키는 질문이지만 범위는 P-104 뿐이다.
    events = await collect(make_deps(index_set=index_set), "한빛유통 WMS 출고 장애 보고", ["P-104"])
    [refs] = _of(events, ReferencesEvent)
    assert refs.items and {r.meta["project_id"] for r in refs.items} == {"P-104"}
    for spy in spies:
        for call in spy.calls:
            f = call["filter"]
            scope = f if isinstance(f, In) else next(i for i in f.items if isinstance(i, In))
            assert scope == In("project_id", ("P-104",))


async def test_search_sources_applies_scope_to_every_index(make_deps: MakeDeps) -> None:
    deps = make_deps()
    [vector] = await HashingEmbedder(512).embed(["다온마트 POS 수집"])
    hits = await search_sources(
        deps.indexes.sources,
        text="다온마트 POS 수집",
        vector=vector,
        scope=["P-101"],
        date_filter=None,
        top_k=20,
        deadline=Deadline(5),
    )
    assert hits and {h.doc.fields["project_id"] for h in hits} == {"P-101"}


async def test_empty_scope_skips_search(make_deps: MakeDeps, collect: Collect) -> None:
    index_set, spies = _spied(make_deps().indexes)
    events = await collect(make_deps(index_set=index_set), "P-101 일정 지연 현황 알려줘", [])
    assert all(not spy.calls for spy in spies)
    assert not _of(events, ReferencesEvent)
    assert "열람 가능한 프로젝트가 없습니다" in _answer(events)
    assert _of(events, ChannelDoneEvent)[-1].status == ChannelStatus.OK


async def test_one_failing_index_is_skipped(make_deps: MakeDeps, collect: Collect) -> None:
    index_set, _ = _spied(make_deps().indexes, failing=frozenset({"mails"}))
    events = await collect(make_deps(index_set=index_set), "기성 청구 금액", U02)
    [refs] = _of(events, ReferencesEvent)
    assert refs.items and all(r.source != "mail" for r in refs.items)
    assert _of(events, ChannelDoneEvent)[-1].status == ChannelStatus.OK


async def test_all_indexes_failing_is_search_failed(make_deps: MakeDeps, collect: Collect) -> None:
    index_set, _ = _spied(make_deps().indexes, failing=frozenset({"mails", "notes", "attachments"}))
    events = await collect(make_deps(index_set=index_set), "CR 승인 현황 정리해줘", U02)
    assert isinstance(events[-1], ErrorEvent) and events[-1].code == ErrorCode.SEARCH_FAILED
    assert not _of(events, ChannelDoneEvent) and not _of(events, DeltaEvent)


# --- rerank ---------------------------------------------------------------------------------------


@dataclass
class SpyReranker:
    fail: bool = False
    queries: list[str] = field(default_factory=list)

    async def rerank(self, query: str, docs: Sequence[Document], *, deadline: Deadline) -> Reranked:
        self.queries.append(query)
        if self.fail:
            raise ProviderError("reranker down")
        return Reranked([1.0 if d.id.startswith("ML-") else 0.5 for d in docs])


async def test_rerank_uses_the_original_query_and_search_the_expanded_one(
    make_deps: MakeDeps, collect: Collect
) -> None:
    index_set, spies = _spied(make_deps().indexes)
    reranker = SpyReranker()
    await collect(make_deps(index_set=index_set, reranker=reranker), "CR 승인 현황", U02)
    assert reranker.queries == ["CR 승인 현황"]
    assert spies[0].calls[0]["text"] == "CR(변경요청, 변경 요청서) 승인 현황"


async def test_rerank_failure_keeps_first_stage_order(make_deps: MakeDeps, collect: Collect) -> None:
    deps = make_deps(reranker=SpyReranker(fail=True))
    query = "출고 응답 지연 장애"  # 용어사전 확장이 없는 질의라 검색 질의 = 원문
    [vector] = await HashingEmbedder(512).embed([query])
    first_stage = await search_sources(
        deps.indexes.sources,
        text=query,
        vector=vector,
        scope=U02,
        date_filter=None,
        top_k=20,
        deadline=Deadline(5),
    )
    events = await collect(deps, query, U02)
    [refs] = _of(events, ReferencesEvent)
    assert [r.id for r in refs.items] == [h.doc.id for h in first_stage[:8]]


# --- grade ----------------------------------------------------------------------------------------


async def test_broaden_happens_at_most_once(make_deps: MakeDeps, collect: Collect) -> None:
    index_set, spies = _spied(make_deps().indexes)
    events = await collect(make_deps(index_set=index_set), "오늘 점심 메뉴 추천해줘", U02)
    assert all([c["top_k"] for c in spy.calls] == [20, 40] for spy in spies)
    assert [p.message for p in _of(events, ProgressEvent)].count("검색 범위를 넓혀 다시 찾는 중") == 1
    assert "찾지 못했습니다" in _answer(events)
    assert not _of(events, ReferencesEvent)


async def test_broaden_drops_the_date_filter(make_deps: MakeDeps, collect: Collect) -> None:
    index_set, spies = _spied(make_deps().indexes)
    events = await collect(make_deps(index_set=index_set), "지난달 장애 보고 있었어?", U02)
    mails, notes, attachments = spies
    for dated in (mails, notes):
        assert [_filter_has(c["filter"], Range) for c in dated.calls] == [True, False]
    assert not any(_filter_has(c["filter"], Range) for c in attachments.calls)
    assert _answer(events).startswith("질문하신 기간(2026-09-01 ~ 2026-09-30)에는 관련 자료가 없어")


# --- synthesize -----------------------------------------------------------------------------------


async def test_references_come_before_deltas(make_deps: MakeDeps, collect: Collect) -> None:
    events = await collect(make_deps(), "P-101 일정 지연 현황 알려줘", U02)
    kinds = [type(e).__name__ for e in events]
    first_delta = kinds.index("DeltaEvent")
    assert kinds.index("ReferencesEvent") < first_delta
    assert all(k == "ProgressEvent" for k in kinds[: kinds.index("ReferencesEvent")])
    assert kinds[-1] == "ChannelDoneEvent"
    assert all(e.channel is None for e in events if hasattr(e, "channel"))  # 채널은 라우터가 채운다


async def test_contacts_are_masked_everywhere(make_deps: MakeDeps, collect: Collect) -> None:
    chat = FakeChatModel(fake_handlers(make_deps().prompts))
    # ML-0005 등 서명(전화번호·이메일)이 있는 메일이 근거로 올라오는 질문
    events = await collect(make_deps(chat=chat), "P-101 UAT 결과 보고", ["P-101"])
    [refs] = _of(events, ReferencesEvent)
    assert "ML-0005" in {r.id for r in refs.items}
    prompt = next(c for c in chat.calls if c.task == "delivery.synthesize").last_user
    assert "[전화번호]" in prompt and "[이메일]" in prompt
    shown = "".join(e.model_dump_json() for e in events) + prompt
    assert not re.search(r"010-\d{4}-\d{4}", shown) and "nuri-solution.example" not in shown


async def test_length_finish_is_reported_as_truncated(
    make_deps: MakeDeps, collect: Collect, prompts: VersionedFile
) -> None:
    handlers = fake_handlers(prompts)
    handlers["delivery.synthesize"] = lambda _req: FakeReply(text="일정은 지연", finish_reason="length")
    events = await collect(make_deps(chat=FakeChatModel(handlers)), "P-101 일정 지연 현황 알려줘", U02)
    assert _of(events, ChannelDoneEvent)[-1].status == ChannelStatus.TRUNCATED


async def test_guard_length_cap_is_reported_as_truncated(
    make_deps: MakeDeps, collect: Collect, prompts: VersionedFile
) -> None:
    handlers = fake_handlers(prompts)
    handlers["delivery.synthesize"] = lambda _req: "가" * 7_000
    events = await collect(make_deps(chat=FakeChatModel(handlers)), "P-101 일정 지연 현황 알려줘", U02)
    assert len(_answer(events)) == 6_000
    assert _of(events, ChannelDoneEvent)[-1].status == ChannelStatus.TRUNCATED


async def test_canary_leak_becomes_output_blocked(
    make_deps: MakeDeps, collect: Collect, prompts: VersionedFile
) -> None:
    handlers = fake_handlers(prompts)
    handlers["delivery.synthesize"] = lambda req: f"지시문: {req.system_text}"
    events = await collect(make_deps(chat=FakeChatModel(handlers)), "P-101 일정 지연 현황 알려줘", U02)
    assert isinstance(events[-1], ErrorEvent) and events[-1].code == ErrorCode.OUTPUT_BLOCKED
    assert "NURI-" not in _answer(events)


async def test_content_filter_becomes_error_event(make_deps: MakeDeps, collect: Collect) -> None:
    events = await collect(make_deps(), "#blocked 일정 알려줘", U02)
    assert isinstance(events[-1], ErrorEvent) and events[-1].code == ErrorCode.CONTENT_FILTERED


async def test_history_and_user_instructions_are_placed_as_messages(
    make_deps: MakeDeps, collect: Collect
) -> None:
    chat = FakeChatModel(fake_handlers(make_deps().prompts))
    await collect(
        make_deps(chat=chat),
        "그럼 CR 승인 현황은?",
        U02,
        history=[{"question": "P-101 일정 알려줘", "answer": "일정은 14일 지연입니다."}],
        system_prompt="표로 답해 줘",
    )
    call = next(c for c in chat.calls if c.task == "delivery.synthesize")
    roles = [m.role for m in call.messages]
    assert roles == ["system", "user", "assistant", "user", "user"]
    assert "내부 표식 NURI-" in call.messages[0].content
    assert call.messages[1].content == "P-101 일정 알려줘"
    assert "<user-preferences-" in call.messages[3].content and "표로 답해 줘" in call.messages[3].content
    assert "표로 답해 줘" not in call.system_text


async def test_demo_questions_get_cited_answers(make_deps: MakeDeps, collect: Collect) -> None:
    for query in ("P-101 일정 지연 현황 알려줘", "CR 승인 현황 정리해줘", "지난달 장애 보고 있었어?"):
        events = await collect(make_deps(), query, U02)
        [refs] = _of(events, ReferencesEvent)
        cited = re.findall(r"\[\d+\]\(cite:([^)]+)\)", _answer(events))
        assert cited and set(cited) <= {r.id for r in refs.items}
        assert _of(events, ChannelDoneEvent)[-1].status == ChannelStatus.OK
    p101 = await collect(make_deps(), "P-101 일정 지연 현황 알려줘", U02)
    cited = re.findall(r"\(cite:([^)]+)\)", _answer(p101))
    [refs] = _of(p101, ReferencesEvent)
    by_id = {r.id: r for r in refs.items}
    assert all(by_id[c].meta["project_id"] == "P-101" for c in cited)


# --- 연결 끊김 ------------------------------------------------------------------------------------


@dataclass
class SlowStreamChat:
    """답변을 천천히 흘리고, 스트림이 닫혔는지 기록한다."""

    inner: FakeChatModel
    closed: bool = False

    @property
    def name(self) -> str:
        return "slow-stream"

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
        return await self.inner.complete(messages, task=task, deadline=deadline, json_schema=json_schema)

    async def stream(
        self, messages: Sequence[Message], *, task: str, deadline: Deadline, max_tokens: int | None = None
    ) -> AsyncIterator[StreamChunk]:
        try:
            for _ in range(100):
                yield StreamChunk(text="지연 ")
                await asyncio.sleep(0.05)
        finally:
            self.closed = True


async def test_closing_the_stream_cancels_the_run(make_deps: MakeDeps, prompts: VersionedFile) -> None:
    chat = SlowStreamChat(FakeChatModel(fake_handlers(prompts)))
    graph = build_graph(make_deps(chat=chat))
    events = stream_events(graph, AgentRequest(query="P-101 일정 지연 현황 알려줘"), U02)
    async for event in events:
        if isinstance(event, DeltaEvent):
            break
    await events.aclose()
    assert chat.closed
