"""개인정보 보호 — 필드 화이트리스트, 본문 마스킹, 필드 암호화.

마스킹은 검색 결과가 서비스 안으로 들어오는 경계에서 출처와 상관없이 한 번 적용한다.
그래야 LLM 프롬프트·참조 카드·로그 어디에도 원문이 흘러가지 않는다. 원문 연락처는
암호화된 필드로만 보관하고, 권한 클레임이 있는 요청에서만 복호화한다.
"""

from __future__ import annotations

import base64
import os
import re
from collections.abc import Iterable, Mapping
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from chatcore.errors import InvalidRequest

_PHONE = re.compile(r"(?<!\d)(?:\+82[-. ]?)?0\d{1,2}[-. )]?\d{3,4}[-. ]?\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

PHONE_MASK = "[전화번호]"
EMAIL_MASK = "[이메일]"


def redact(text: str) -> str:
    return _EMAIL.sub(EMAIL_MASK, _PHONE.sub(PHONE_MASK, text))


def pick(fields: Mapping[str, Any], allowed: Iterable[str]) -> dict[str, Any]:
    """허용한 필드만 남긴다. 새 필드가 생겨도 자동으로 나가지 않는다."""
    return {k: fields[k] for k in allowed if k in fields}


class FieldCipher:
    """AES-GCM 필드 암호화. 토큰 형식: `v1.<base64url(nonce|ciphertext)>`."""

    def __init__(self, key: bytes) -> None:
        if len(key) not in (16, 24, 32):
            raise ValueError("AES key must be 16, 24 or 32 bytes")
        self._aead = AESGCM(key)

    @classmethod
    def from_b64(cls, key_b64: str) -> FieldCipher:
        return cls(base64.urlsafe_b64decode(key_b64))

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        sealed = self._aead.encrypt(nonce, plaintext.encode(), None)
        return "v1." + base64.urlsafe_b64encode(nonce + sealed).decode()

    def decrypt(self, token: str) -> str:
        version, _, body = token.partition(".")
        if version != "v1" or not body:
            raise InvalidRequest("unsupported cipher token")
        raw = base64.urlsafe_b64decode(body)
        return self._aead.decrypt(raw[:12], raw[12:], None).decode()
