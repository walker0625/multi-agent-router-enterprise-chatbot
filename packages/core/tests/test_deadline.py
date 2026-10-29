import asyncio

import pytest

from chatcore.deadline import Deadline
from chatcore.errors import DeadlineExceeded


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_timeout_is_capped_by_remaining_budget() -> None:
    clock = FakeClock()
    d = Deadline(10, clock=clock)
    assert d.timeout(30) == 10
    clock.now = 7
    assert d.timeout(30) == pytest.approx(3)
    assert d.timeout(1) == 1


def test_expired_deadline_fails_fast() -> None:
    clock = FakeClock()
    d = Deadline(1, clock=clock)
    clock.now = 2
    with pytest.raises(DeadlineExceeded):
        d.timeout(5)


def test_child_never_exceeds_parent() -> None:
    clock = FakeClock()
    parent = Deadline(5, clock=clock)
    assert parent.child(60).remaining() == pytest.approx(5)
    assert parent.child(2).remaining() == pytest.approx(2)


async def test_run_cancels_slow_work() -> None:
    cancelled = asyncio.Event()

    async def slow() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(DeadlineExceeded):
        await Deadline(0.05).run(slow())
    assert cancelled.is_set()
