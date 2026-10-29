import pytest

from chatcore.errors import InvalidRequest, OutputBlocked
from chatcore.guard import OutputGuard, wrap_user_instructions
from chatcore.privacy import FieldCipher, pick, redact

CANARY = "NURI-ABCDEF123456"


def test_guard_passes_normal_text_through() -> None:
    g = OutputGuard(CANARY, max_chars=100)
    assert g.feed("안녕하세요. ") + g.feed("일정은 다음과 같습니다.") + g.flush() == (
        "안녕하세요. 일정은 다음과 같습니다."
    )


def test_guard_blocks_canary_split_across_chunks_without_leaking_it() -> None:
    g = OutputGuard(CANARY, max_chars=1000)
    shown = g.feed("규칙은 NURI-AB")
    assert "NURI" not in shown  # 표식의 앞부분일 수 있는 꼬리는 붙잡아 둔다
    with pytest.raises(OutputBlocked):
        g.feed("CDEF123456 입니다")


def test_guard_releases_held_tail_when_it_turns_out_harmless() -> None:
    g = OutputGuard(CANARY, max_chars=1000)
    out = g.feed("모델명 NURI-A") + g.feed("X 입니다") + g.flush()
    assert out == "모델명 NURI-AX 입니다"


def test_guard_truncates_at_max_chars() -> None:
    g = OutputGuard(CANARY, max_chars=5)
    assert g.feed("abcdefgh") == "abcde"
    assert g.truncated
    assert g.feed("more") == ""


def test_user_instructions_boundary_is_unpredictable() -> None:
    a, b = wrap_user_instructions("존댓말로"), wrap_user_instructions("존댓말로")
    assert a != b
    assert "존댓말로" in a


def test_redact_masks_phone_and_email() -> None:
    text = "연락처 010-1234-5678, 02)555-1234, mail kim@example.com"
    out = redact(text)
    assert "1234-5678" not in out and "555-1234" not in out and "@" not in out
    assert out.count("[전화번호]") == 2


def test_pick_is_a_whitelist() -> None:
    assert pick({"name": "a", "phone_enc": "x", "new_field": 1}, ["name", "title"]) == {"name": "a"}


def test_cipher_roundtrip_and_rejects_unknown_format() -> None:
    cipher = FieldCipher(b"0" * 32)
    token = cipher.encrypt("010-0000-0000")
    assert token.startswith("v1.") and "010" not in token
    assert cipher.decrypt(token) == "010-0000-0000"
    with pytest.raises(InvalidRequest):
        cipher.decrypt("plain")
