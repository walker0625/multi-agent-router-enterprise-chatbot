import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from chatcore.auth import AuthSettings, Principal, TokenIssuer
from chatcore.events import ChannelDoneEvent, ChannelStatus, DeltaEvent, Event, ReferencesEvent
from chatcore.sse import iter_events
from delivery_agent.api import create_app
from delivery_agent.deps import Deps
from delivery_agent.settings import Settings

MakeDeps = Callable[..., Deps]

USERS = {u["sub"]: u for u in json.loads((Settings().data_dir / "users.json").read_text(encoding="utf-8"))}


def _token(keypair: tuple[bytes, bytes], sub: str, *, audience: str = "delivery-agent") -> str:
    u = USERS[sub]
    principal = Principal(
        sub=u["sub"],
        name=u["name"],
        team=u["team"],
        project_ids=u["project_ids"],
        sales_units=u["sales_units"],
        can_read_contacts=u["can_read_contacts"],
    )
    return TokenIssuer(keypair[0], issuer="nuri-router").issue(principal, audience=audience, ttl_s=60)


async def _post(deps: Deps, token: str, body: dict[str, Any]) -> tuple[int, list[Event] | dict[str, Any]]:
    transport = httpx.ASGITransport(app=create_app(deps=deps))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}
        async with client.stream("POST", "/v1/stream", json=body, headers=headers) as resp:
            if resp.headers["content-type"].startswith("text/event-stream"):
                return resp.status_code, [e async for e in iter_events(resp.aiter_lines())]
            await resp.aread()
            return resp.status_code, resp.json()


async def test_stream_emits_events_in_contract_order(
    make_deps: MakeDeps, keypair: tuple[bytes, bytes]
) -> None:
    status, events = await _post(make_deps(), _token(keypair, "u-02"), {"query": "CR 승인 현황 정리해줘"})
    assert status == 200 and isinstance(events, list)
    kinds = [e.type for e in events]
    refs_at = kinds.index("references")
    assert set(kinds[:refs_at]) == {"progress"}
    assert kinds.count("references") == 1
    assert set(kinds[refs_at + 1 : -1]) == {"delta"} and kinds[-1] == "channel_done"
    assert isinstance(events[-1], ChannelDoneEvent) and events[-1].status == ChannelStatus.OK
    answer = "".join(e.text for e in events if isinstance(e, DeltaEvent))
    assert "(cite:ML-" in answer


async def test_requested_projects_outside_token_scope_are_forbidden(
    make_deps: MakeDeps, keypair: tuple[bytes, bytes]
) -> None:
    body = {"query": "MES 설비 모니터링", "project_ids": ["P-101", "P-104"]}
    status, payload = await _post(make_deps(), _token(keypair, "u-02"), body)
    assert status == 403 and isinstance(payload, dict) and payload["code"] == "FORBIDDEN"


async def test_requested_subset_narrows_the_scope(make_deps: MakeDeps, keypair: tuple[bytes, bytes]) -> None:
    body = {"query": "CR 승인 현황 정리해줘", "project_ids": ["P-102"]}
    status, events = await _post(make_deps(), _token(keypair, "u-02"), body)
    assert status == 200 and isinstance(events, list)
    [refs] = [e for e in events if isinstance(e, ReferencesEvent)]
    assert {r.meta["project_id"] for r in refs.items} == {"P-102"}


async def test_token_for_another_audience_is_rejected(
    make_deps: MakeDeps, keypair: tuple[bytes, bytes]
) -> None:
    token = _token(keypair, "u-02", audience="router")
    status, payload = await _post(make_deps(), token, {"query": "CR 승인 현황"})
    assert status == 401 and isinstance(payload, dict) and payload["code"] == "UNAUTHORIZED"


async def test_missing_token_is_rejected(make_deps: MakeDeps) -> None:
    transport = httpx.ASGITransport(app=create_app(deps=make_deps()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/stream", json={"query": "CR 승인 현황"})
    assert resp.status_code == 401


async def test_user_without_projects_gets_a_notice(make_deps: MakeDeps, keypair: tuple[bytes, bytes]) -> None:
    status, events = await _post(
        make_deps(), _token(keypair, "u-03"), {"query": "P-101 일정 지연 현황 알려줘"}
    )
    assert status == 200 and isinstance(events, list)
    assert not any(isinstance(e, ReferencesEvent) for e in events)
    answer = "".join(e.text for e in events if isinstance(e, DeltaEvent))
    assert answer.startswith("열람 가능한 프로젝트가 없습니다")


async def test_health_ready_reflects_loading(make_deps: MakeDeps) -> None:
    # deps 없이 만든 앱은 lifespan 전이라 아직 적재 전이다. 앱 생성 자체는 I/O 를 하지 않는다.
    for app, ready in ((create_app(), 503), (create_app(deps=make_deps()), 200)):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/health/live")).status_code == 200
            assert (await client.get("/health/ready")).status_code == ready


def test_lifespan_loads_data_and_serves(keypair: tuple[bytes, bytes], tmp_path: Path) -> None:
    key_path = tmp_path / "jwt_public.pem"
    key_path.write_bytes(keypair[1])
    app = create_app(Settings(auth=AuthSettings(public_key_path=key_path)))
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        resp = client.post(
            "/v1/stream",
            json={"query": "지난달 장애 보고 있었어?"},
            headers={"Authorization": f"Bearer {_token(keypair, 'u-02')}"},
        )
    assert resp.status_code == 200
    assert "event: references" in resp.text and "event: channel_done" in resp.text
