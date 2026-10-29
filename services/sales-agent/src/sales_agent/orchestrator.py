"""영업 에이전트 턴 실행 — 무상태 도구 호출 루프.

호스팅 에이전트(thread/run)에 대화 상태를 맡기지 않고 `ChatModel.complete(tools=...)` 로 직접
루프를 돈다. 요청마다 `RequestContext`(principal·범위 필터·예산·메모)를 새로 만들어 도구
실행기에 명시적 인자로 넘기므로 동시 요청끼리 권한이나 메모가 섞이지 않는다.

1. 계획: 모델이 도구를 부르면 실행해 결과를 tool 메시지로 붙이고 다시 부른다. 라운드·도구
   호출·검색 수에 상한이 있고, 상한에 닿으면 남은 호출은 "예산 초과" 결과로 답한 뒤 바로
   답변 단계로 넘어간다.
2. 답변: 실제로 쓴 결과의 근거 카드를 먼저 보내고, 답을 `OutputGuard` 로 걸러 스트리밍한다.

오류는 코드와 공개 메시지만 `ErrorEvent` 로 내보내고 원인은 로그에만 남긴다. 클라이언트가
끊겨 제너레이터가 취소·종료되면 진행 중인 모델 스트림을 닫고 더 이상 이벤트를 만들지 않는다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from chatcore.agent_api import AgentRequest
from chatcore.auth import Principal
from chatcore.deadline import Deadline
from chatcore.errors import PUBLIC_MESSAGES, AppError, ErrorCode
from chatcore.events import (
    ChannelDoneEvent,
    ChannelStatus,
    DeltaEvent,
    ErrorEvent,
    Event,
    ProgressEvent,
    Reference,
    ReferencesEvent,
)
from chatcore.guard import OutputGuard, make_canary
from chatcore.llm import Message, Usage
from sales_agent.context import RequestContext, TurnBudget
from sales_agent.deps import Deps
from sales_agent.permissions import meeting_scope
from sales_agent.retrieval import Retriever
from sales_agent.tools import ToolOutcome, execute, search_tool

MAX_ROUNDS = 3
TURN_BUDGET_S = 50.0
MAX_ANSWER_CHARS = 6_000

log = logging.getLogger(__name__)


@dataclass
class _Evidence:
    """도구 호출마다의 결과와, 그 결과에서 만든 근거 카드(같은 id 는 한 장)."""

    payloads: list[dict[str, Any]] = field(default_factory=list)
    cards: dict[str, Reference] = field(default_factory=dict)

    def add(self, outcome: ToolOutcome) -> None:
        if outcome.evidence is not None:
            self.payloads.append(outcome.evidence)
        for card in outcome.references:
            self.cards.setdefault(card.id, card)


async def run_turn(
    req: AgentRequest, principal: Principal, deps: Deps, *, today: date | None = None
) -> AsyncGenerator[Event]:
    started = time.monotonic()
    day = today or date.today()
    canary = make_canary()
    ctx = RequestContext(principal, meeting_scope(principal), TurnBudget(Deadline(TURN_BUDGET_S)))
    retriever = Retriever(deps.indexes, deps.embedder, deps.cipher)
    evidence = _Evidence()
    usage = Usage()
    outcome = "disconnected"  # 끝까지 가지 못하고 닫히면 이 값이 남는다
    try:
        yield ProgressEvent(message="질문 분석 중", percent=10)
        messages = deps.prompts.plan_messages(req, canary=canary, today=day)
        tool = search_tool(deps.prompts.tool_description)
        for _ in range(MAX_ROUNDS):
            completion = await deps.chat.complete(
                messages, task="sales.plan", deadline=ctx.budget.deadline, tools=[tool]
            )
            usage += completion.usage
            if not completion.tool_calls:
                break
            if ctx.budget.tool_calls == 0:
                yield ProgressEvent(message="영업 자료 검색 중", percent=40)
            messages.append(
                Message(role="assistant", content=completion.text, tool_calls=completion.tool_calls)
            )
            for call in completion.tool_calls:
                result = await execute(call, ctx, retriever)
                messages.append(Message(role="tool", tool_call_id=call.id, content=result.content))
                evidence.add(result)
            if ctx.budget.exhausted:
                break

        yield ProgressEvent(message="답변 작성 중", percent=75)
        if evidence.cards:
            yield ReferencesEvent(items=list(evidence.cards.values()))
        guard = OutputGuard(canary, max_chars=MAX_ANSWER_CHARS)
        finish = "stop"
        chunks = deps.chat.stream(
            deps.prompts.answer_messages(req, canary=canary, today=day, evidence=evidence.payloads),
            task="sales.answer",
            deadline=ctx.budget.deadline,
        )
        try:
            async for chunk in chunks:
                if chunk.text and (text := guard.feed(chunk.text)):
                    yield DeltaEvent(text=text)
                if chunk.usage is not None:
                    usage += chunk.usage
                if chunk.finish_reason is not None:
                    finish = chunk.finish_reason
                if guard.truncated:
                    break  # 더 받아도 내보낼 수 없다
        finally:
            await chunks.aclose()  # 중간에 빠져나오거나 취소되면 공급자 스트림을 바로 닫는다
        if tail := guard.flush():
            yield DeltaEvent(text=tail)
        status = ChannelStatus.TRUNCATED if guard.truncated or finish == "length" else ChannelStatus.OK
        outcome = status.value
        yield ChannelDoneEvent(status=status, usage=usage)
    except AppError as exc:
        outcome = exc.code.value
        log.warning("sales turn failed", extra={"code": exc.code.value, "detail": exc.detail})
        yield ErrorEvent(code=exc.code, message=exc.public_message)
    except Exception:
        outcome = ErrorCode.INTERNAL.value
        log.exception("sales turn crashed")
        yield ErrorEvent(code=ErrorCode.INTERNAL, message=PUBLIC_MESSAGES[ErrorCode.INTERNAL])
    finally:
        log.info(
            "sales turn",
            extra={
                "sub": principal.sub,
                "outcome": outcome,
                "prompts": deps.prompts.tag,
                "tool_calls": ctx.budget.tool_calls,
                "searches": ctx.budget.searches,
                "references": len(evidence.cards),
                "latency_ms": int((time.monotonic() - started) * 1000),
            },
        )
