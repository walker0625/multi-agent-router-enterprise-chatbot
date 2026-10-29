"""담당자 정보 공개 규칙.

- 담당자 필드는 화이트리스트(`CONTACT_FIELDS`, `chatcore.privacy.pick`)로만 내보낸다.
  인덱스에 필드가 늘어도(암호문 필드 포함) 자동으로 나가지 않는다.
- 연락처 원문은 `can_read_contacts` 클레임이 있는 요청에서만 복호화한다. 권한이 없으면
  복호화를 아예 하지 않고 `[비공개]` 를 넣는다.

본문 마스킹은 `retrieval` 이 검색 경계에서 한 번 한다. 복호화한 연락처는 마스킹 대상인
본문과 따로 붙으므로 권한자에게는 원문이 그대로 간다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from chatcore.auth import Principal
from chatcore.privacy import FieldCipher

HIDDEN = "[비공개]"

CONTACT_FIELDS = ("account_id", "account_name", "name", "title", "department", "role", "note")


def contact_channels(fields: Mapping[str, Any], principal: Principal, cipher: FieldCipher) -> tuple[str, str]:
    """(전화, 메일). 권한이 없으면 둘 다 `[비공개]`."""
    if not principal.can_read_contacts:
        return HIDDEN, HIDDEN
    return cipher.decrypt(fields["phone_enc"]), cipher.decrypt(fields["email_enc"])
