"""요청 컨텍스트와 턴 예산.

도구 실행기는 이 객체를 명시적 인자로 받는다. 요청별 권한(principal·범위 필터)을 프로세스
공용 슬롯이나 ContextVar 로 넘기지 않으므로 동시에 도는 두 요청의 권한이 섞일 수 없다.
메모 캐시도 요청마다 새로 만들어, 한 턴 안의 같은 검색만 재사용하고 다른 사용자의 결과는
재사용하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from chatcore.auth import Principal
from chatcore.deadline import Deadline
from chatcore.errors import BudgetExceeded
from chatcore.filters import Filter
from chatcore.search import Hit

MAX_TOOL_CALLS = 4
MAX_SEARCHES = 8

# (인덱스 이름, 질의, 필터). 필터 노드는 frozen dataclass 라 그대로 키가 된다.
MemoKey = tuple[str, str, Filter | None]


@dataclass
class TurnBudget:
    """한 턴의 상한: 시간(deadline), 도구 호출 수, 실제 검색 수(메모 적중은 세지 않는다)."""

    deadline: Deadline
    max_tool_calls: int = MAX_TOOL_CALLS
    max_searches: int = MAX_SEARCHES
    tool_calls: int = 0
    searches: int = 0

    @property
    def exhausted(self) -> bool:
        return self.tool_calls >= self.max_tool_calls or self.searches >= self.max_searches

    def spend_search(self) -> None:
        if self.searches >= self.max_searches:
            raise BudgetExceeded(f"search cap {self.max_searches} reached")
        self.searches += 1


@dataclass
class RequestContext:
    principal: Principal
    meeting_scope: Filter  # 회의록·첨부 열람 범위
    budget: TurnBudget
    memo: dict[MemoKey, list[Hit]] = field(default_factory=dict)
    vectors: dict[str, list[float]] = field(default_factory=dict)  # 질의 임베딩(인텐트끼리 공유)
