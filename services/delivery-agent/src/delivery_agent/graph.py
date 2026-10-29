"""수행 에이전트 그래프 — understand → retrieve → rerank → grade (→ broaden → retrieve) → synthesize.

같은 질문이 실행 경로에 따라 다르게 처리되지 않도록 그래프를 하나만 둔다. 노드는 진행 상황·근거
카드·답변 조각을 `get_stream_writer()` 로 흘리고, `stream_events` 는 `stream_mode="custom"` 출력을
그대로 내보낸다.

- 용어사전 확장 질의는 검색 재현율에만 쓴다. 리랭크와 답변은 사용자가 실제로 쓴 원문 질의로 한다.
- 권한 범위 필터(`project_id`)는 모든 인덱스 검색에 붙는다. 범위가 비면 검색하지 않고 바로 안내한다.
- 인덱스별 점수는 서로 비교할 수 없어서 1차 후보는 순위끼리 번갈아 합친다.
- 근거가 약하면 기간 필터를 빼고 후보 수를 늘려 한 번만 다시 찾는다. 그래도 약하면 지어내지 않고
  찾지 못했다고 답한다.
- 시간 예산은 요청마다 하나(`Deadline`)이고 단계마다 상한을 `child()` 로 나눠 준다.
"""

from __future__ import annotations

import asyncio
import logging
import operator
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from datetime import date
from itertools import zip_longest
from typing import Annotated, Any, Literal, TypedDict, cast

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import StreamWriter
from pydantic import BaseModel

from chatcore.agent_api import AgentRequest
from chatcore.deadline import Deadline
from chatcore.errors import PUBLIC_MESSAGES, AppError, ErrorCode, SearchFailed
from chatcore.events import (
    ChannelDoneEvent,
    ChannelStatus,
    DeltaEvent,
    ErrorEvent,
    ProgressEvent,
    Reference,
    ReferencesEvent,
)
from chatcore.filters import Filter, Range, all_of, in_
from chatcore.guard import OutputGuard, canary_instruction, make_canary, wrap_user_instructions
from chatcore.llm import Message, Usage, assistant, system, user
from chatcore.llm.types import FinishReason
from chatcore.search import Document, Hit, Reranker
from delivery_agent.deps import Deps
from delivery_agent.glossary import GlossaryExpander
from delivery_agent.indexes import Source
from delivery_agent.intent import Intent, IntentClassifier

log = logging.getLogger(__name__)

REQUEST_BUDGET_S = 50.0
UNDERSTAND_CAP_S = 8.0
SEARCH_CAP_S = 10.0
RERANK_CAP_S = 10.0
SEARCH_TOP_K = 20
MAX_CANDIDATES = 30
MAX_EVIDENCE = 8
ANSWER_MAX_CHARS = 6_000
SNIPPET_CHARS = 160

_SOURCE_LABEL = {"mail": "메일", "note": "회의 메모", "attachment": "첨부"}
_TITLE_FIELD = {"mail": "subject", "note": "title", "attachment": "filename"}

Verdict = Literal["answer", "broaden", "not_found", "no_scope"]


@dataclass(frozen=True)
class RunContext:
    """요청 하나 동안 바뀌지 않는 값. 그래프 상태가 아니라 실행 컨텍스트로 넘긴다."""

    request: AgentRequest
    scope: tuple[str, ...]
    deadline: Deadline


@dataclass(frozen=True)
class Ranked:
    doc: Document
    score: float | None  # 리랭크 점수. 리랭크가 실패해 1차 순서를 쓰면 None


class State(TypedDict):
    expanded_query: str
    intent: Intent
    date_from: date | None
    date_to: date | None
    top_k: int
    use_dates: bool
    retried: bool
    candidates: list[Hit]
    ranked: list[Ranked]
    verdict: Verdict | None
    usage: Annotated[Usage, operator.add]


def initial_state(query: str) -> State:
    return State(
        expanded_query=query,
        intent=Intent.OTHER,
        date_from=None,
        date_to=None,
        top_k=SEARCH_TOP_K,
        use_dates=True,
        retried=False,
        candidates=[],
        ranked=[],
        verdict=None,
        usage=Usage(),
    )


def interleave(lists: Sequence[Sequence[Hit]], limit: int) -> list[Hit]:
    """각 인덱스의 1위, 2위, … 순으로 번갈아 합친다."""
    merged = [hit for group in zip_longest(*lists) for hit in group if hit is not None]
    return merged[:limit]


async def search_sources(
    sources: Sequence[Source],
    *,
    text: str,
    vector: Sequence[float],
    scope: Sequence[str],
    date_filter: Filter | None,
    top_k: int,
    deadline: Deadline,
) -> list[Hit]:
    """세 인덱스를 병렬로 검색한다. 일부 실패는 빼고 진행하고, 전부 실패하면 `SearchFailed`."""
    scope_filter = in_("project_id", scope)

    async def one(source: Source) -> list[Hit]:
        f = all_of(scope_filter, date_filter if source.dated else None)
        return await deadline.run(source.index.search(text=text, vector=vector, filter=f, top_k=top_k))

    results = await asyncio.gather(*(one(s) for s in sources), return_exceptions=True)
    ranked: list[list[Hit]] = []
    for source, result in zip(sources, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("index search failed", extra={"index": source.index.name, "error": repr(result)})
        else:
            ranked.append(result)
    if not ranked:
        raise SearchFailed("all delivery indexes failed")
    return interleave(ranked, MAX_CANDIDATES)


async def rerank_hits(
    reranker: Reranker, query: str, hits: Sequence[Hit], *, deadline: Deadline, keep: int
) -> tuple[list[Ranked], Usage]:
    """원문 질의로 후보를 다시 매긴다. 실패하면 1차 순서를 그대로 쓴다."""
    docs = [h.doc for h in hits]
    if not docs:
        return [], Usage()
    try:
        result = await deadline.run(reranker.rerank(query, docs, deadline=deadline))
    except Exception as exc:
        log.warning("rerank failed; keeping first-stage order", extra={"error": repr(exc)})
        return [Ranked(d, None) for d in docs[:keep]], Usage()
    scores = result.scores
    order = sorted(range(len(docs)), key=lambda i: scores[i], reverse=True)
    return [Ranked(docs[i], scores[i]) for i in order[:keep]], result.usage


def grade_evidence(ranked: Sequence[Ranked], *, retried: bool, min_score: float) -> Verdict:
    """품질 점수는 리랭크 최고 점수다. 리랭크가 실패했으면 점수로 거를 수 없어 그대로 답한다."""
    top = ranked[0].score if ranked else None
    if ranked and (top is None or top >= min_score):
        return "answer"
    return "not_found" if retried else "broaden"


class _Nodes:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        self.classifier = IntentClassifier(deps.chat, deps.prompts)
        self.expander = GlossaryExpander(deps.glossary, deps.chat, deps.prompts)

    async def understand(self, state: State, runtime: Runtime[RunContext]) -> dict[str, Any]:
        get_stream_writer()(ProgressEvent(message="질문 분석 중", percent=10))
        ctx = runtime.context
        step = ctx.deadline.child(UNDERSTAND_CAP_S)
        query = ctx.request.query
        (intent, intent_usage), expansion = await asyncio.gather(
            self.classifier.classify(query, today=self.deps.today(), deadline=step),
            self.expander.expand(query, step),
        )
        return {
            "intent": intent.intent,
            "date_from": intent.date_from,
            "date_to": intent.date_to,
            "expanded_query": expansion.text,
            "usage": intent_usage + expansion.usage,
        }

    async def retrieve(self, state: State, runtime: Runtime[RunContext]) -> dict[str, Any]:
        ctx = runtime.context
        if not ctx.scope:
            return {"verdict": "no_scope"}
        if not state["retried"]:
            get_stream_writer()(ProgressEvent(message="프로젝트 자료 검색 중", percent=35))
        step = ctx.deadline.child(SEARCH_CAP_S)
        query = state["expanded_query"]
        try:
            [vector] = await step.run(self.deps.embedder.embed([query]))
        except AppError as exc:
            raise SearchFailed(f"query embedding failed: {exc.detail}") from exc
        date_from, date_to = state["date_from"], state["date_to"]
        dated = state["use_dates"] and (date_from is not None or date_to is not None)
        hits = await search_sources(
            self.deps.indexes.sources,
            text=query,
            vector=vector,
            scope=ctx.scope,
            date_filter=Range("date", gte=date_from, lte=date_to) if dated else None,
            top_k=state["top_k"],
            deadline=step,
        )
        return {"candidates": hits}

    async def rerank(self, state: State, runtime: Runtime[RunContext]) -> dict[str, Any]:
        if not state["retried"]:
            get_stream_writer()(ProgressEvent(message="관련도 정렬 중", percent=60))
        ctx = runtime.context
        ranked, usage = await rerank_hits(
            self.deps.reranker,
            ctx.request.query,
            state["candidates"],
            deadline=ctx.deadline.child(RERANK_CAP_S),
            keep=MAX_EVIDENCE,
        )
        return {"ranked": ranked, "usage": usage}

    def grade(self, state: State) -> dict[str, Any]:
        return {
            "verdict": grade_evidence(
                state["ranked"], retried=state["retried"], min_score=self.deps.min_score
            )
        }

    def broaden(self, state: State) -> dict[str, Any]:
        get_stream_writer()(ProgressEvent(message="검색 범위를 넓혀 다시 찾는 중", percent=60))
        return {"use_dates": False, "top_k": state["top_k"] * 2, "retried": True}

    async def synthesize(self, state: State, runtime: Runtime[RunContext]) -> dict[str, Any]:
        write = get_stream_writer()
        ctx = runtime.context
        write(ProgressEvent(message="답변 작성 중", percent=80))
        verdict = state["verdict"]
        if verdict in ("no_scope", "not_found"):
            write(DeltaEvent(text=self.deps.prompts.render(f"{verdict}_answer").strip()))
            write(ChannelDoneEvent(status=ChannelStatus.OK, usage=state["usage"]))
            self._trace(state, ChannelStatus.OK)
            return {}
        write(ReferencesEvent(items=[self._reference(r.doc) for r in state["ranked"]]))
        canary = make_canary()
        guard = OutputGuard(canary, max_chars=ANSWER_MAX_CHARS)
        messages = self._messages(state, ctx, canary)
        finish, usage = await ctx.deadline.run(self._stream_answer(messages, guard, ctx.deadline, write))
        status = ChannelStatus.TRUNCATED if finish == "length" or guard.truncated else ChannelStatus.OK
        write(ChannelDoneEvent(status=status, usage=state["usage"] + usage))
        self._trace(state, status)
        return {"usage": usage}

    async def _stream_answer(
        self, messages: list[Message], guard: OutputGuard, deadline: Deadline, write: StreamWriter
    ) -> tuple[FinishReason | None, Usage]:
        finish: FinishReason | None = None
        usage = Usage()
        chunks = self.deps.chat.stream(messages, task="delivery.synthesize", deadline=deadline)
        try:
            async for chunk in chunks:
                if chunk.text and (shown := guard.feed(chunk.text)):
                    write(DeltaEvent(text=shown))
                if guard.truncated:
                    break  # 상한을 넘었으면 남은 생성은 받지 않는다
                if chunk.finish_reason is not None:
                    finish, usage = chunk.finish_reason, chunk.usage or Usage()
        finally:
            await chunks.aclose()
        if not guard.truncated and (tail := guard.flush()):
            write(DeltaEvent(text=tail))
        return finish, usage

    def _messages(self, state: State, ctx: RunContext, canary: str) -> list[Message]:
        prompts = self.deps.prompts
        request = ctx.request
        messages = [system(f"{prompts.render('synthesize_system').strip()}\n{canary_instruction(canary)}")]
        for turn in request.history:
            messages += [user(turn.question), assistant(turn.answer)]
        if request.system_prompt:
            messages.append(user(wrap_user_instructions(request.system_prompt)))
        messages.append(
            user(
                prompts.render(
                    "synthesize_user",
                    today=self.deps.today().isoformat(),
                    intent=state["intent"].value,
                    note=self._note(state),
                    evidence=self._evidence(state["ranked"]),
                    question=request.query,
                )
            )
        )
        return messages

    def _note(self, state: State) -> str:
        """기간 필터를 빼고 다시 찾은 결과면, 답변이 그 기간의 자료인 것처럼 말하지 않게 알린다."""
        date_from, date_to = state["date_from"], state["date_to"]
        if not state["retried"] or (date_from is None and date_to is None):
            return ""
        return self.deps.prompts.render(
            "broadened_note",
            date_from=date_from.isoformat() if date_from else "",
            date_to=date_to.isoformat() if date_to else "",
        )

    def _evidence(self, ranked: Sequence[Ranked]) -> str:
        blocks = []
        for n, r in enumerate(ranked, 1):
            f = r.doc.fields
            project = f"{f['project_id']} {self.deps.indexes.project_names[f['project_id']]}"
            blocks.append(
                f"[{n}] ({r.doc.id}) {_SOURCE_LABEL[f['source']]} | {f['date']} | {project}\n{r.doc.text}"
            )
        return "\n\n".join(blocks)

    def _reference(self, doc: Document) -> Reference:
        f = doc.fields
        return Reference(
            id=doc.id,
            source=f["source"],
            title=f[_TITLE_FIELD[f["source"]]],
            snippet=doc.text.partition("\n")[2][:SNIPPET_CHARS],
            date=f["date"],
            meta={
                "project_id": f["project_id"],
                "project_name": self.deps.indexes.project_names[f["project_id"]],
            },
        )

    def _trace(self, state: State, status: ChannelStatus) -> None:
        ranked = state["ranked"]
        log.info(
            "delivery answer",
            extra={
                "intent": state["intent"].value,
                "verdict": state["verdict"],
                "retried": state["retried"],
                "candidates": len(state["candidates"]),
                "top_score": ranked[0].score if ranked else None,
                "status": status.value,
                "prompts": self.deps.prompts.tag,
            },
        )


def _after_retrieve(state: State) -> str:
    return "synthesize" if state["verdict"] == "no_scope" else "rerank"


def _after_grade(state: State) -> str:
    return "broaden" if state["verdict"] == "broaden" else "synthesize"


def build_graph(deps: Deps) -> CompiledStateGraph[State, RunContext, State, State]:
    nodes = _Nodes(deps)
    graph = StateGraph(State, context_schema=RunContext)
    graph.add_node("understand", nodes.understand)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("rerank", nodes.rerank)
    graph.add_node("grade", nodes.grade)
    graph.add_node("broaden", nodes.broaden)
    graph.add_node("synthesize", nodes.synthesize)
    graph.add_edge(START, "understand")
    graph.add_edge("understand", "retrieve")
    graph.add_conditional_edges("retrieve", _after_retrieve, ["rerank", "synthesize"])
    graph.add_edge("rerank", "grade")
    graph.add_conditional_edges("grade", _after_grade, ["broaden", "synthesize"])
    graph.add_edge("broaden", "retrieve")
    graph.add_edge("synthesize", END)
    return graph.compile()


async def stream_events(
    graph: CompiledStateGraph[State, RunContext, State, State],
    request: AgentRequest,
    scope: Sequence[str],
) -> AsyncGenerator[BaseModel]:
    """그래프를 한 번 실행하며 이벤트를 내보낸다. 실패는 `error` 이벤트 하나로 바꾸고 끝낸다.

    소비자가 이 제너레이터를 닫으면(클라이언트 연결 끊김) 그래프 실행과 진행 중인 LLM·검색 호출이
    함께 취소된다.
    """
    context = RunContext(request=request, scope=tuple(scope), deadline=Deadline(REQUEST_BUDGET_S))
    # astream 은 async generator 다(타입 힌트만 AsyncIterator). 닫히면 실행 중인 노드가 취소된다.
    run = cast(
        AsyncGenerator[BaseModel],
        graph.astream(initial_state(request.query), context=context, stream_mode="custom"),
    )
    try:
        async with aclosing(run) as events:
            async for event in events:
                yield event
    except AppError as exc:
        log.warning("delivery stream failed", extra={"code": exc.code.value, "detail": exc.detail})
        yield ErrorEvent(code=exc.code, message=exc.public_message)
    except Exception:
        log.exception("delivery stream crashed")
        yield ErrorEvent(code=ErrorCode.INTERNAL, message=PUBLIC_MESSAGES[ErrorCode.INTERNAL])
