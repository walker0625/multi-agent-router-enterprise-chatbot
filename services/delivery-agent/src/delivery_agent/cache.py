"""작은 TTL·LRU 캐시.

같은 질의가 짧은 시간에 다시 오면(재시도·새로고침·병렬 채널) LLM 판정을 다시 부르지 않는다.
오래된 판정이 남지 않도록 TTL 로 끊고, 메모리가 늘지 않도록 가장 오래 안 쓴 항목부터 버린다.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable


class TTLCache[K: Hashable, V]:
    def __init__(self, *, maxsize: int, ttl_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._maxsize = maxsize
        self._ttl_s = ttl_s
        self._clock = clock
        self._items: OrderedDict[K, tuple[float, V]] = OrderedDict()

    def get(self, key: K) -> V | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= self._clock():
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return value

    def put(self, key: K, value: V) -> None:
        self._items[key] = (self._clock() + self._ttl_s, value)
        self._items.move_to_end(key)
        while len(self._items) > self._maxsize:
            self._items.popitem(last=False)
