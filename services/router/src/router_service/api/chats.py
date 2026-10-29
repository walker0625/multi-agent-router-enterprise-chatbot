"""대화 목록·관리. 모든 조회는 토큰 사용자 소유로 한정되고, 남의 대화는 404 다."""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from chatcore.events import DoneEvent, TurnStatus
from router_service.api.deps import PrincipalDep, RuntimeDep
from router_service.store import ChatRecord

router = APIRouter(prefix="/v1/chats", tags=["chats"])


class CreateChatBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)


class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class TurnView(BaseModel):
    id: str
    turn_no: int
    status: TurnStatus
    query: str
    created_at: str
    payload: DoneEvent | None  # 끝난 턴만 있다


class ChatDetail(ChatRecord):
    turns: list[TurnView]


@router.post("", status_code=201)
async def create_chat(body: CreateChatBody, principal: PrincipalDep, rt: RuntimeDep) -> ChatRecord:
    return await rt.store.create_chat(principal.sub, body.title)


@router.get("")
async def list_chats(principal: PrincipalDep, rt: RuntimeDep) -> list[ChatRecord]:
    return await rt.store.list_chats(principal.sub)


@router.get("/{chat_id}")
async def get_chat(chat_id: str, principal: PrincipalDep, rt: RuntimeDep) -> ChatDetail:
    chat = await rt.store.get_chat(principal.sub, chat_id)
    turns = [
        TurnView(
            id=t.id,
            turn_no=t.turn_no,
            status=t.status,
            query=t.query,
            created_at=t.created_at,
            payload=DoneEvent.model_validate_json(t.payload_json) if t.payload_json else None,
        )
        for t in await rt.store.list_turns(principal.sub, chat_id)
    ]
    return ChatDetail(**chat.model_dump(), turns=turns)


@router.patch("/{chat_id}")
async def rename_chat(chat_id: str, body: RenameBody, principal: PrincipalDep, rt: RuntimeDep) -> ChatRecord:
    await rt.store.rename(principal.sub, chat_id, body.title)
    return await rt.store.get_chat(principal.sub, chat_id)


@router.delete("/{chat_id}", status_code=204)
async def delete_chat(chat_id: str, principal: PrincipalDep, rt: RuntimeDep) -> Response:
    await rt.store.soft_delete_chat(principal.sub, chat_id)
    return Response(status_code=204)
