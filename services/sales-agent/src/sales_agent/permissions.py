"""영업 자료 열람 범위.

비공개 회의록은 토큰의 `sales_units` 에 속한 단위만 본다. 회의 첨부는 적재할 때 부모
회의록의 `unit`·`confidential` 을 물려받았으므로 같은 필터를 그대로 건다. `sales_units` 가
비면 빈 `In` 이 되어 비공개 쪽은 아무것도 통과하지 않는다 — 범위가 비었다고 조건을 빼면
전체가 열린다.

고객사·담당자 기본 정보는 공개라 범위 필터가 없다. 연락처 원문 공개 여부는 `privacy` 가
`can_read_contacts` 클레임으로 정한다.
"""

from __future__ import annotations

from chatcore.auth import Principal
from chatcore.filters import And, Eq, Filter, Or, in_


def meeting_scope(principal: Principal) -> Filter:
    return Or(
        (
            Eq("confidential", False),
            And((Eq("confidential", True), in_("unit", principal.sales_units))),
        )
    )
