"""서비스 간 인증.

라우터만 ES256 개인키로 토큰을 발급하고, 다른 서비스는 공개키로 검증만 한다 — 검증하는
쪽이 토큰을 만들어 낼 수 없다. 토큰마다 `aud` 를 서비스 이름 하나로 묶어서, 웹이 받은
라우터용 토큰을 도메인 에이전트에 직접 들고 가도 통과하지 않는다. 라우터는 하위 호출마다
그 에이전트용 단기 토큰을 새로 발급한다.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Header
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from chatcore.errors import Unauthorized

ALGORITHM = "ES256"


class Principal(BaseModel):
    """토큰이 운반하는 사용자와 권한 범위."""

    sub: str
    name: str
    team: str = ""
    project_ids: list[str] = Field(default_factory=list)  # 수행(delivery) 자료 열람 범위
    sales_units: list[str] = Field(default_factory=list)  # 비공개 영업 회의록 열람 단위
    can_read_contacts: bool = False  # 고객 담당자 연락처 원문 열람


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTH_", extra="ignore")

    issuer: str = "nuri-router"
    public_key_path: Path = Path(".devkeys/jwt_public.pem")
    private_key_path: Path | None = None  # 라우터만 가진다
    user_token_ttl_s: int = 3600
    service_token_ttl_s: int = 120


def generate_keypair() -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private_pem, public_pem


class TokenIssuer:
    def __init__(self, private_key_pem: bytes, *, issuer: str) -> None:
        self._key = private_key_pem
        self._issuer = issuer

    def issue(self, principal: Principal, *, audience: str, ttl_s: int) -> str:
        now = int(time.time())
        claims = {
            "iss": self._issuer,
            "sub": principal.sub,
            "aud": audience,
            "iat": now,
            "exp": now + ttl_s,
            "jti": uuid.uuid4().hex,
            "ctx": principal.model_dump(exclude={"sub"}),
        }
        return jwt.encode(claims, self._key, algorithm=ALGORITHM)


class TokenVerifier:
    def __init__(self, public_key_pem: bytes, *, issuer: str, audience: str, leeway_s: int = 5) -> None:
        self._key = public_key_pem
        self._issuer = issuer
        self._audience = audience
        self._leeway_s = leeway_s

    def verify(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self._key,
                algorithms=[ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway_s,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
            return Principal(sub=claims["sub"], **claims.get("ctx", {}))
        except (jwt.PyJWTError, ValidationError, TypeError) as exc:
            raise Unauthorized(f"token rejected: {type(exc).__name__}") from exc


def bearer_token(authorization: str | None = Header(default=None)) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("missing bearer token")
    return token.strip()
