import base64
import hashlib
import hmac
import json
import time

import pytest

from chatcore.auth import Principal, TokenIssuer, TokenVerifier, generate_keypair
from chatcore.errors import Unauthorized

PRIVATE, PUBLIC = generate_keypair()
ISSUER = TokenIssuer(PRIVATE, issuer="nuri-router")
ALICE = Principal(sub="u-01", name="김하늘", team="영업1팀", sales_units=["S1"], can_read_contacts=True)


def test_roundtrip_carries_scope_claims() -> None:
    token = ISSUER.issue(ALICE, audience="sales-agent", ttl_s=60)
    p = TokenVerifier(PUBLIC, issuer="nuri-router", audience="sales-agent").verify(token)
    assert p == ALICE


def test_token_for_one_service_is_rejected_by_another() -> None:
    token = ISSUER.issue(ALICE, audience="router", ttl_s=60)
    with pytest.raises(Unauthorized):
        TokenVerifier(PUBLIC, issuer="nuri-router", audience="sales-agent").verify(token)


def test_expired_token_rejected() -> None:
    token = ISSUER.issue(ALICE, audience="router", ttl_s=-60)
    with pytest.raises(Unauthorized):
        TokenVerifier(PUBLIC, issuer="nuri-router", audience="router", leeway_s=0).verify(token)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def test_verifier_key_cannot_mint_tokens() -> None:
    # 공개키만 가진 서비스가 그 키를 HS256 비밀처럼 써서 서명을 흉내 내도 통과하지 않는다.
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(
        json.dumps(
            {"iss": "nuri-router", "sub": "u-99", "aud": "router", "iat": int(time.time()), "exp": 9999999999}
        ).encode()
    )
    signature = _b64(hmac.new(PUBLIC, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    forged = f"{header}.{payload}.{signature}"
    with pytest.raises(Unauthorized):
        TokenVerifier(PUBLIC, issuer="nuri-router", audience="router").verify(forged)


def test_token_signed_by_other_key_rejected() -> None:
    other_private, _ = generate_keypair()
    token = TokenIssuer(other_private, issuer="nuri-router").issue(ALICE, audience="router", ttl_s=60)
    with pytest.raises(Unauthorized):
        TokenVerifier(PUBLIC, issuer="nuri-router", audience="router").verify(token)
