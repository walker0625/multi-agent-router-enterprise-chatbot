import base64
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from chatcore.auth import AuthSettings, Principal, TokenIssuer, generate_keypair
from chatcore.events import Event
from chatcore.llm import LLMSettings
from chatcore.sse import iter_events
from sales_agent.api import create_app
from sales_agent.deps import Deps
from sales_agent.privacy import HIDDEN
from sales_agent.settings import Settings


@pytest.fixture
def issuer_and_key(tmp_path: Path) -> tuple[TokenIssuer, Path]:
    private, public = generate_keypair()
    path = tmp_path / "jwt_public.pem"
    path.write_bytes(public)
    return TokenIssuer(private, issuer="nuri-router"), path


@pytest.fixture
def issuer(issuer_and_key: tuple[TokenIssuer, Path]) -> TokenIssuer:
    return issuer_and_key[0]


@pytest.fixture
async def client(issuer_and_key: tuple[TokenIssuer, Path], deps: Deps) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        app_env="test", llm=LLMSettings(provider="fake"), auth=AuthSettings(public_key_path=issuer_and_key[1])
    )
    app = create_app(settings, deps)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://s") as c,
    ):
        yield c


def bearer(issuer: TokenIssuer, principal: Principal, audience: str = "sales-agent") -> dict[str, str]:
    return {"Authorization": f"Bearer {issuer.issue(principal, audience=audience, ttl_s=60)}"}


async def read_stream(client: httpx.AsyncClient, query: str, headers: dict[str, str]) -> list[Event]:
    async with client.stream("POST", "/v1/stream", json={"query": query}, headers=headers) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        return [e async for e in iter_events(resp.aiter_lines())]


async def test_sse_runs_to_completion_in_contract_order(
    client: httpx.AsyncClient, issuer: TokenIssuer, seller: Principal
) -> None:
    events = await read_stream(client, "한빛유통 가격 협상 회의 내용 요약해줘", bearer(issuer, seller))
    kinds = [e.type for e in events]
    first_ref, first_delta = kinds.index("references"), kinds.index("delta")
    assert set(kinds[:first_ref]) == {"progress"}
    assert first_ref < first_delta
    assert kinds.count("references") == 1
    assert set(kinds[first_delta:-1]) == {"delta"}
    assert kinds[-1] == "channel_done"
    text = "".join(e.text for e in events if e.type == "delta")
    assert "(cite:meeting:M-003)" in text


async def test_permission_difference_over_http(
    client: httpx.AsyncClient, issuer: TokenIssuer, seller: Principal, outsider: Principal
) -> None:
    question = "한빛유통 IT전략팀 담당자 연락처 알려줘"
    mine = await read_stream(client, question, bearer(issuer, seller))
    theirs = await read_stream(client, question, bearer(issuer, outsider))
    assert "010-0000-1001" in "".join(e.text for e in mine if e.type == "delta")
    their_text = "".join(e.text for e in theirs if e.type == "delta")
    assert HIDDEN in their_text and "010-0000" not in their_text


@pytest.mark.parametrize("audience", ["router", "delivery-agent"])
async def test_token_for_another_service_is_401(
    client: httpx.AsyncClient, issuer: TokenIssuer, seller: Principal, audience: str
) -> None:
    resp = await client.post(
        "/v1/stream", json={"query": "한빛유통"}, headers=bearer(issuer, seller, audience)
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_missing_token_is_401_and_bad_body_is_422(
    client: httpx.AsyncClient, issuer: TokenIssuer, seller: Principal
) -> None:
    assert (await client.post("/v1/stream", json={"query": "한빛유통"})).status_code == 401
    resp = await client.post("/v1/stream", json={"query": ""}, headers=bearer(issuer, seller))
    assert resp.status_code == 422 and resp.json()["code"] == "INVALID_REQUEST"


async def test_health(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health/live")).json() == {"status": "ok"}
    ready = await client.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["prompts"].startswith("prompts@")


def test_app_creation_does_no_io(tmp_path: Path) -> None:
    # 공개키·데이터는 lifespan 에서 읽는다. 없는 경로여도 앱 생성은 성공한다.
    settings = Settings(
        app_env="test",
        data_dir=tmp_path / "missing",
        auth=AuthSettings(public_key_path=tmp_path / "missing.pem"),
    )
    create_app(settings)


def test_prod_refuses_to_start_without_field_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SALES_FIELD_KEY_B64", raising=False)
    with pytest.raises(ValidationError, match="SALES_FIELD_KEY_B64"):
        Settings(app_env="prod")
    key = SecretStr(base64.urlsafe_b64encode(b"0" * 32).decode())
    assert Settings(app_env="prod", field_key_b64=key).app_env == "prod"
