from collections.abc import AsyncIterator, Callable
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from chatcore.events import DoneEvent, Event, HitlEvent, RouteEvent, TurnStatus
from chatcore.sse import iter_events
from router_service.app import create_app
from router_service.deps import Deps
from router_service.runtime import Runtime
from router_service.settings import Settings


@pytest.fixture
async def app(make_settings: Callable[..., Settings], deps: Deps) -> AsyncIterator[FastAPI]:
    application = create_app(make_settings(), deps)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def login(client: httpx.AsyncClient, user_id: str) -> dict[str, str]:
    resp = await client.post("/v1/auth/demo-login", json={"user_id": user_id})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['token']}"}


async def new_chat(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    resp = await client.post("/v1/chats", json={}, headers=headers)
    assert resp.status_code == 201
    return resp.json()["id"]


async def sse(client: httpx.AsyncClient, path: str, headers: dict[str, str], body: Any = None) -> list[Event]:
    async with client.stream("POST", path, json=body, headers=headers) as resp:
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
        return [e async for e in iter_events(resp.aiter_lines())]


async def test_login_chat_and_stream_to_the_end(client: httpx.AsyncClient) -> None:
    users = (await client.get("/v1/auth/demo-users")).json()
    assert [u["sub"] for u in users] == ["u-01", "u-02", "u-03", "u-04"]
    headers = await login(client, "u-02")
    me = (await client.get("/v1/me", headers=headers)).json()
    assert me["sub"] == "u-02" and me["project_ids"] == ["P-101", "P-102", "P-103"]
    assert me["projects"][0]["name"] == "한빛유통 물류센터 WMS 고도화"

    chat_id = await new_chat(client, headers)
    events = await sse(
        client, f"/v1/chats/{chat_id}/turns/stream", headers, {"query": "P-101 일정 지연 현황 알려줘"}
    )
    assert [e.type for e in events][:3] == ["turn", "progress", "route"] and events[-1].type == "done"
    done = cast(DoneEvent, events[-1])
    assert done.status is TurnStatus.DONE

    detail = (await client.get(f"/v1/chats/{chat_id}", headers=headers)).json()
    assert detail["title"] == "P-101 일정 지연 현황 알려줘"
    assert DoneEvent.model_validate(detail["turns"][0]["payload"]) == done
    assert [c["id"] for c in (await client.get("/v1/chats", headers=headers)).json()] == [chat_id]


async def test_other_users_chat_is_not_found(client: httpx.AsyncClient) -> None:
    owner, other = await login(client, "u-01"), await login(client, "u-04")
    chat_id = await new_chat(client, owner)
    assert (await client.get(f"/v1/chats/{chat_id}", headers=other)).status_code == 404
    assert (await client.patch(f"/v1/chats/{chat_id}", json={"title": "x"}, headers=other)).status_code == 404
    assert (await client.delete(f"/v1/chats/{chat_id}", headers=other)).status_code == 404
    resp = await client.post(
        f"/v1/chats/{chat_id}/turns/stream", json={"query": "견적 알려줘"}, headers=other
    )
    assert resp.status_code == 404 and resp.json()["code"] == "NOT_FOUND"
    assert (await client.get(f"/v1/chats/{chat_id}", headers=owner)).status_code == 200


async def test_project_ids_outside_token_scope_is_forbidden(client: httpx.AsyncClient) -> None:
    headers = await login(client, "u-01")  # P-101 만 볼 수 있다
    chat_id = await new_chat(client, headers)
    body = {"query": "P-102 일정 알려줘", "project_ids": ["P-102"]}
    resp = await client.post(f"/v1/chats/{chat_id}/turns/stream", json=body, headers=headers)
    assert resp.status_code == 403 and resp.json()["code"] == "FORBIDDEN"
    assert (await client.get(f"/v1/chats/{chat_id}", headers=headers)).json()["turns"] == []


async def test_concurrent_turn_in_same_chat_is_conflict(client: httpx.AsyncClient, app: FastAPI) -> None:
    headers = await login(client, "u-02")
    chat_id = await new_chat(client, headers)
    runtime = cast(Runtime, app.state.runtime)
    await runtime.store.begin_turn(chat_id, "u-02", "진행 중인 질문")
    resp = await client.post(f"/v1/chats/{chat_id}/turns/stream", json={"query": "또 질문"}, headers=headers)
    assert resp.status_code == 409 and resp.json()["code"] == "CONFLICT"


async def test_hitl_then_domain_button(client: httpx.AsyncClient) -> None:
    headers = await login(client, "u-02")
    chat_id = await new_chat(client, headers)
    path = f"/v1/chats/{chat_id}/turns/stream"
    events = await sse(client, path, headers, {"query": "그거 어떻게 됐어?"})
    assert [e.type for e in events] == ["turn", "progress", "route", "hitl", "done"]
    hitl = cast(HitlEvent, events[3])
    # 웹은 범위를 고르지 않으면 project_ids 를 null 로 보낸다(= 토큰 범위 전체).
    body = {"query": "그거 어떻게 됐어?", "domain": hitl.choices[1].domain, "project_ids": None}
    events = await sse(client, path, headers, body)
    route = cast(RouteEvent, events[1])
    assert route.decided_by == "forced" and route.agents == ["sales"]


async def test_regenerate_replays_and_hides_old_turn_and_stop(client: httpx.AsyncClient) -> None:
    headers = await login(client, "u-02")
    chat_id = await new_chat(client, headers)
    events = await sse(
        client, f"/v1/chats/{chat_id}/turns/stream", headers, {"query": "P-101 일정 지연 현황 알려줘"}
    )
    old_id = cast(DoneEvent, events[-1]).turn_id
    stop = await client.post(f"/v1/chats/{chat_id}/turns/{old_id}/stop", headers=headers)
    assert stop.json() == {"status": "already_finished"}

    events = await sse(client, f"/v1/chats/{chat_id}/turns/{old_id}/regenerate", headers)
    route = cast(RouteEvent, events[1])
    assert route.decided_by == "replay" and route.agents == ["delivery"]
    turns = (await client.get(f"/v1/chats/{chat_id}", headers=headers)).json()["turns"]
    assert [(t["turn_no"], t["status"]) for t in turns] == [(2, "done")]


async def test_auth_and_settings(client: httpx.AsyncClient, app: FastAPI) -> None:
    assert (await client.get("/v1/me")).status_code == 401
    runtime = cast(Runtime, app.state.runtime)
    wrong_aud = runtime.issuer.issue(runtime.users["u-01"].principal(), audience="sales-agent", ttl_s=60)
    assert (await client.get("/v1/me", headers={"Authorization": f"Bearer {wrong_aud}"})).status_code == 401

    headers = await login(client, "u-01")
    too_long = await client.put("/v1/me/settings", json={"system_prompt": "가" * 2_001}, headers=headers)
    assert too_long.status_code == 422
    await client.put("/v1/me/settings", json={"system_prompt": "존댓말로 짧게"}, headers=headers)
    assert (await client.get("/v1/me/settings", headers=headers)).json() == {"system_prompt": "존댓말로 짧게"}
    await client.put("/v1/me/settings", json={"system_prompt": None}, headers=headers)  # 지우기
    assert (await client.get("/v1/me/settings", headers=headers)).json() == {"system_prompt": None}


async def test_health(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health/live")).json() == {"status": "ok"}
    ready = await client.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["checks"] == {"store": "ok"}  # 하위 에이전트는 fake


async def test_demo_login_can_be_disabled(make_settings: Callable[..., Settings], deps: Deps) -> None:
    application = create_app(make_settings(demo_login_enabled=False), deps)
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as c,
    ):
        assert (await c.get("/v1/auth/demo-users")).status_code == 404
        assert (await c.post("/v1/auth/demo-login", json={"user_id": "u-01"})).status_code == 404


def test_prod_refuses_to_start_with_demo_login(make_settings: Callable[..., Settings], deps: Deps) -> None:
    with pytest.raises(ValidationError, match="데모 로그인"):
        create_app(make_settings(app_env="prod", demo_login_enabled=True), deps)
    assert make_settings(app_env="prod", demo_login_enabled=False).app_env == "prod"
