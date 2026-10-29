"""요청 단위 시간 예산.

턴 하나에 주어진 예산을 아래 단계로 그대로 넘긴다. 각 호출의 타임아웃은 "자기 상한"과
"남은 예산" 중 작은 값이라, 재시도가 겹쳐도 전체 예산을 넘지 않는다. 예산이 끝나면
`asyncio.timeout` 으로 실제로 취소되므로 기한을 넘긴 작업이 뒤에서 계속 돌지 않는다.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from chatcore.errors import DeadlineExceeded

T = TypeVar("T")


class Deadline:
    def __init__(self, budget_s: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._expires_at = clock() + budget_s

    @classmethod
    def unlimited(cls) -> Deadline:
        return cls(math.inf)

    def remaining(self) -> float:
        return self._expires_at - self._clock()

    def expired(self) -> bool:
        return self.remaining() <= 0

    def check(self) -> None:
        if self.expired():
            raise DeadlineExceeded("deadline already expired")

    def timeout(self, cap: float | None = None) -> float:
        """이번 호출에 쓸 타임아웃(초). 예산이 이미 끝났으면 바로 실패한다."""
        self.check()
        remaining = self.remaining()
        return remaining if cap is None else min(remaining, cap)

    def child(self, cap: float) -> Deadline:
        """하위 단계용 예산. 상위 예산을 넘지 않는다."""
        return Deadline(min(self.remaining(), cap), clock=self._clock)

    async def run(self, awaitable: Awaitable[T], cap: float | None = None) -> T:
        try:
            async with asyncio.timeout(self.timeout(cap)):
                return await awaitable
        except TimeoutError as exc:
            raise DeadlineExceeded("deadline exceeded while waiting") from exc
