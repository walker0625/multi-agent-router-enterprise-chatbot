"""턴 스트리밍·다시 생성·중단.

권한 검사와 턴 시작(`pending` 행 쓰기)은 스트림을 열기 전에 끝낸다. 그래서 범위 밖 요청(403),
남의 대화(404), 같은 대화의 동시 요청(409)은 SSE 가 아니라 HTTP 오류로 돌아간다. 스트림이
시작된 뒤의 실패는 `error` 이벤트로 보낸다.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints

from chatcore.sse import event_stream_response
from router_service.api.deps import PrincipalDep, RuntimeDep, check_scope
from router_service.routing import Domain
from router_service.runner import CascadeSource, ForcedSource, Plan, PlanSource, ReplaySource, TurnContext

router = APIRouter(prefix="/v1/chats/{chat_id}/turns", tags=["turns"])

Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000)]


class TurnRequest(BaseModel):
    query: Query
    domain: Domain | None = None  # 지정하면 라우팅하지 않는다(HITL 도메인 버튼 등)
    project_ids: list[str] | None = Field(default=None, max_length=50)  # null·빈 목록 = 토큰 범위 전체


class StopResponse(BaseModel):
    status: Literal["stopping", "already_finished"]


@router.post("/stream")
async def stream_turn(
    chat_id: str, body: TurnRequest, principal: PrincipalDep, rt: RuntimeDep
) -> StreamingResponse:
    project_ids = body.project_ids or []
    check_scope(project_ids, principal)
    turn = await rt.store.begin_turn(chat_id, principal.sub, body.query)
    source: PlanSource = (
        ForcedSource(body.domain, body.query, project_ids)
        if body.domain
        else CascadeSource(rt.cascade, body.query, project_ids)
    )
    ctx = TurnContext(
        chat_id=chat_id, turn_id=turn.id, turn_no=turn.turn_no, query=body.query, principal=principal
    )
    return event_stream_response(rt.runner.run(source, ctx))


@router.post("/{turn_id}/regenerate")
async def regenerate(
    chat_id: str, turn_id: str, principal: PrincipalDep, rt: RuntimeDep
) -> StreamingResponse:
    old = await rt.store.get_turn(principal.sub, chat_id, turn_id)
    plan = Plan.model_validate_json(old.plan_json) if old.plan_json else None
    project_ids = plan.project_ids if plan else []
    check_scope(project_ids, principal)  # 저장 뒤 권한이 줄었을 수 있다
    turn = await rt.store.begin_turn(chat_id, principal.sub, old.query)
    await rt.store.soft_delete_turn(principal.sub, chat_id, old.id)
    # 라우팅 전에 끝난 턴에는 저장된 plan 이 없다. 그때만 다시 라우팅한다.
    source: PlanSource = ReplaySource(plan) if plan else CascadeSource(rt.cascade, old.query, project_ids)
    ctx = TurnContext(
        chat_id=chat_id, turn_id=turn.id, turn_no=turn.turn_no, query=old.query, principal=principal
    )
    return event_stream_response(rt.runner.run(source, ctx))


@router.post("/{turn_id}/stop")
async def stop_turn(chat_id: str, turn_id: str, principal: PrincipalDep, rt: RuntimeDep) -> StopResponse:
    return StopResponse(status=await rt.store.request_cancel(turn_id, principal.sub, chat_id))
