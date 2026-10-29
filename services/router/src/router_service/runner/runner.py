"""턴 실행기.

단계는 route(plan 만들기) → execute(결과별 실행) → finalize → persist 이고, plan 을 어디서
얻는지(`PlanSource`)만 바뀐다. 일반 질문·강제 도메인·다시 생성이 같은 실행기를 쓴다.

- SINGLE·PARALLEL: 채널 실행(`ChannelRun`)으로 에이전트 스트림을 그대로 전달한다. PARALLEL 은
  두 채널이 모두 ok 일 때만 `summary` 채널로 요약을 덧붙인다.
- HITL: 재질문 제안 + 도메인 버튼. 같은 대화에서 되묻기가 이어지면 버튼만 준다.
- BLOCKED: `error(CONTENT_FILTERED)` 후 `done(blocked)`.

턴 전체에 예산 하나(`Deadline`)를 두고 라우팅에는 그 일부만 준다. 모든 하위 호출이 같은 예산을
나눠 쓴다. `done` 은 저장한 뒤에 보내므로 저장된 턴과 같은 내용이다 — 단, 저장본에서는 연락처를
가린다(`for_storage`). 권한자는 답변을 받는 순간에만 원문을 보고, 원문이 대화 저장소에 쌓이지 않는다.
클라이언트가 끊겨 제너레이터가 닫히면 턴을 `disconnected` 로 기록한다 — 이 쓰기는 취소에 휘말리지
않게 `asyncio.shield` 로 감싼다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from functools import partial

from chatcore.agent_api import MAX_HISTORY_CHARS, AgentRequest, HistoryTurn
from chatcore.auth import Principal
from chatcore.deadline import Deadline
from chatcore.errors import PUBLIC_MESSAGES, AppError, ErrorCode
from chatcore.events import (
    Answer,
    ChannelStatus,
    DoneEvent,
    ErrorEvent,
    Event,
    HitlEvent,
    Outcome,
    ProgressEvent,
    RouteEvent,
    TurnEvent,
    TurnStatus,
)
from chatcore.guard import wrap_user_instructions
from chatcore.llm import ChatModel, Message, Usage, system, user
from chatcore.privacy import redact
from chatcore.prompts import VersionedFile
from router_service.agents import AgentClient
from router_service.routing import Domain
from router_service.runner.channels import ChannelRun, ChannelState, ChannelStream
from router_service.runner.citations import renumber, strip_markers
from router_service.runner.hitl import HitlResponder
from router_service.runner.plan import Plan, PlanSource
from router_service.runner.recommend import Recommender
from router_service.settings import Settings
from router_service.store import TurnRecord, TurnStore
from router_service.streaming import guarded_stream

log = logging.getLogger(__name__)

SUMMARY_CHANNEL = "summary"
HISTORY_ANSWER_CHARS = 2_000


@dataclass(frozen=True)
class TurnContext:
    chat_id: str
    turn_id: str
    turn_no: int
    query: str
    principal: Principal


@dataclass
class _Turn:
    started: float = field(default_factory=time.monotonic)
    plan: Plan | None = None
    channels: dict[str, ChannelState] = field(default_factory=dict)
    hitl: HitlEvent | None = None
    recommendations: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stopped: bool = False
    persisted: bool = False

    def answers(self) -> list[Answer]:
        return renumber([state.answer() for state in self.channels.values()])

    def channel_status(self) -> TurnStatus:
        if self.stopped:
            return TurnStatus.STOPPED
        answered = (ChannelStatus.OK, ChannelStatus.TRUNCATED)
        if any(s.status in answered for name, s in self.channels.items() if name != SUMMARY_CHANNEL):
            return TurnStatus.DONE
        return TurnStatus.ERROR


def to_history(turns: Sequence[TurnRecord]) -> list[HistoryTurn]:
    """저장된 완료 턴을 에이전트 요청용 직전 대화로 바꾼다. 전체 길이 상한을 넘으면 오래된 것부터 뺀다."""
    history: list[HistoryTurn] = []
    for t in turns:
        if not t.payload_json:
            continue
        done = DoneEvent.model_validate_json(t.payload_json)
        answer = strip_markers("\n\n".join(a.text for a in done.answers if a.text))
        history.append(HistoryTurn(question=t.query[:2_000], answer=answer[:HISTORY_ANSWER_CHARS]))
    while sum(len(h.question) + len(h.answer) for h in history) > MAX_HISTORY_CHARS:
        history.pop(0)
    return history


def for_storage(done: DoneEvent) -> DoneEvent:
    """저장용 사본. 답변·근거 카드의 전화번호·메일을 가린다(다시 열면 가려진 채로 보인다)."""
    answers = [
        a.model_copy(
            update={
                "text": redact(a.text),
                "references": [
                    r.model_copy(
                        update={
                            "title": redact(r.title),
                            "snippet": redact(r.snippet),
                            "meta": {k: redact(v) for k, v in r.meta.items()},
                        }
                    )
                    for r in a.references
                ],
            }
        )
        for a in done.answers
    ]
    return done.model_copy(update={"answers": answers})


class TurnRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        store: TurnStore,
        agents: Mapping[Domain, AgentClient],
        chat: ChatModel,
        prompts: VersionedFile,
        hitl: HitlResponder,
        recommender: Recommender,
    ) -> None:
        self._settings = settings
        self._store = store
        self._agents = agents
        self._chat = chat
        self._prompts = prompts
        self._hitl = hitl
        self._recommender = recommender
        self._labels = {c["domain"]: c["label"] for c in prompts.data["domain_choices"]}

    async def run(self, source: PlanSource, ctx: TurnContext) -> AsyncGenerator[Event]:
        turn = _Turn()
        try:
            async with aclosing(self._steps(source, ctx, turn)) as steps:
                async for event in steps:
                    yield event
        finally:
            if not turn.persisted:  # 끝까지 못 갔다 = 클라이언트가 끊었다
                log.info("client disconnected", extra={"turn": ctx.turn_id})
                done = self._done(ctx, turn, TurnStatus.DISCONNECTED)
                await asyncio.shield(self._persist(ctx, turn, done))

    async def _steps(self, source: PlanSource, ctx: TurnContext, turn: _Turn) -> AsyncGenerator[Event]:
        settings = self._settings
        deadline = Deadline(settings.turn_budget_s)
        yield TurnEvent(chat_id=ctx.chat_id, turn_id=ctx.turn_id, turn_no=ctx.turn_no)
        try:
            history = to_history(await self._store.recent_history(ctx.chat_id, settings.history_turns))
            if source.progress_message:
                yield ProgressEvent(message=source.progress_message, percent=5)
            planned = await source.plan(history, deadline.child(settings.routing_budget_s))
            plan = turn.plan = planned.plan
            turn.usage += planned.usage
            yield RouteEvent(
                outcome=plan.outcome, agents=[d.value for d in plan.domains], decided_by=plan.decided_by
            )
            if await self._store.is_cancel_requested(ctx.turn_id):
                status = TurnStatus.STOPPED
            elif plan.outcome is Outcome.HITL:
                streak = await self._store.hitl_streak(ctx.chat_id)
                stage = "domain" if streak >= settings.max_hitl_streak else "rewrite"
                scope = plan.project_ids or ctx.principal.project_ids
                turn.hitl, usage = await self._hitl.respond(stage, plan.query, scope, deadline)
                turn.usage += usage
                yield turn.hitl
                status = TurnStatus.HITL
            elif plan.outcome is Outcome.BLOCKED:
                code = ErrorCode.CONTENT_FILTERED
                yield ErrorEvent(code=code, message=PUBLIC_MESSAGES[code])
                status = TurnStatus.BLOCKED
            else:
                system_prompt = await self._store.get_settings(ctx.principal.sub)
                async with aclosing(
                    self._execute(plan, ctx, history, system_prompt, deadline, turn)
                ) as events:
                    async for event in events:
                        yield event
                status = turn.channel_status()
                if status is TurnStatus.DONE:
                    domains = [d.value for d in plan.domains]
                    turn.recommendations, usage = await self._recommender.recommend(
                        plan.query, domains, turn.answers(), deadline
                    )
                    turn.usage += usage
        except AppError as exc:
            log.warning(
                "turn failed", extra={"turn": ctx.turn_id, "code": exc.code.value, "detail": exc.detail}
            )
            yield ErrorEvent(code=exc.code, message=exc.public_message)
            status = TurnStatus.ERROR
        except Exception:
            log.exception("turn crashed", extra={"turn": ctx.turn_id})
            yield ErrorEvent(code=ErrorCode.INTERNAL, message=PUBLIC_MESSAGES[ErrorCode.INTERNAL])
            status = TurnStatus.ERROR
        done = self._done(ctx, turn, status)
        await self._persist(ctx, turn, done)
        yield done

    def _channels(
        self, streams: Mapping[str, ChannelStream], ctx: TurnContext, deadline: Deadline
    ) -> ChannelRun:
        return ChannelRun(
            streams,
            deadline=deadline,
            should_stop=partial(self._store.is_cancel_requested, ctx.turn_id),
            poll_s=self._settings.cancel_poll_s,
            queue_size=self._settings.parallel_queue_size,
        )

    async def _execute(
        self,
        plan: Plan,
        ctx: TurnContext,
        history: list[HistoryTurn],
        system_prompt: str | None,
        deadline: Deadline,
        turn: _Turn,
    ) -> AsyncGenerator[Event]:
        req = AgentRequest(
            query=plan.query,
            history=history,
            project_ids=plan.project_ids,
            system_prompt=system_prompt or None,
        )
        streams = {
            d.value: partial(self._agents[d].stream, req, ctx.principal, deadline) for d in plan.domains
        }
        run = self._channels(streams, ctx, deadline)
        turn.channels.update(run.states)
        async with aclosing(run.events()) as events:
            async for event in events:
                yield event
        if run.stopped:
            turn.stopped = True
            return
        if plan.outcome is not Outcome.PARALLEL or any(
            s.status is not ChannelStatus.OK for s in run.states.values()
        ):
            return
        answers = [state.answer() for state in run.states.values()]
        summary = self._channels(
            {SUMMARY_CHANNEL: partial(self._summary, plan.query, answers, system_prompt, deadline)},
            ctx,
            deadline,
        )
        turn.channels.update(summary.states)
        async with aclosing(summary.events()) as events:
            async for event in events:
                yield event
        turn.stopped = summary.stopped

    async def _summary(
        self, query: str, answers: list[Answer], system_prompt: str | None, deadline: Deadline
    ) -> AsyncGenerator[Event]:
        yield ProgressEvent(message="요약 작성 중", percent=90)
        blocks = "\n\n".join(
            f"### {self._labels.get(a.channel, a.channel)} 답변\n{strip_markers(a.text)}" for a in answers
        )

        def build(canary: str) -> list[Message]:
            messages = [system(self._prompts.render("summary_system", canary=canary))]
            if system_prompt:
                messages.append(user(wrap_user_instructions(system_prompt)))
            messages.append(user(self._prompts.render("summary_user", query=query, answers=blocks)))
            return messages

        async for event in guarded_stream(
            self._chat, build, task="summary", deadline=deadline, max_chars=self._settings.answer_max_chars
        ):
            yield event

    def _done(self, ctx: TurnContext, turn: _Turn, status: TurnStatus) -> DoneEvent:
        usage = turn.usage
        for state in turn.channels.values():
            usage += state.usage
        return DoneEvent(
            turn_id=ctx.turn_id,
            status=status,
            outcome=turn.plan.outcome if turn.plan else None,
            answers=turn.answers(),
            hitl=turn.hitl,
            recommendations=turn.recommendations,
            usage=usage,
            latency_ms=int((time.monotonic() - turn.started) * 1000),
        )

    async def _persist(self, ctx: TurnContext, turn: _Turn, done: DoneEvent) -> None:
        plan_json = turn.plan.model_dump_json() if turn.plan else None
        stored = await self._store.finish_turn(
            ctx.turn_id, done.status, for_storage(done).model_dump_json(), plan_json
        )
        turn.persisted = True
        if not stored:
            log.warning("turn was already finished; result not stored", extra={"turn": ctx.turn_id})
