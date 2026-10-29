import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from chatcore.errors import Conflict, NotFound
from chatcore.events import TurnStatus
from router_service.store import TurnStore


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def store(tmp_path: Path, clock: Clock) -> AsyncIterator[TurnStore]:
    s = TurnStore(str(tmp_path / "t.db"), pending_ttl_s=60, clock=clock)
    await s.open()
    try:
        yield s
    finally:
        await s.close()


async def test_finish_is_compare_and_set(store: TurnStore) -> None:
    chat = await store.create_chat("u-01", None)
    turn = await store.begin_turn(chat.id, "u-01", "질문")
    assert await store.finish_turn(turn.id, TurnStatus.DONE, '{"a": 1}', None)
    assert not await store.finish_turn(turn.id, TurnStatus.DISCONNECTED, '{"a": 2}', None)
    [saved] = await store.list_turns("u-01", chat.id)
    assert saved.status is TurnStatus.DONE and saved.payload_json == '{"a": 1}'


async def test_second_pending_turn_in_same_chat_conflicts(store: TurnStore) -> None:
    chat = await store.create_chat("u-01", None)
    results = await asyncio.gather(
        store.begin_turn(chat.id, "u-01", "첫 질문"),
        store.begin_turn(chat.id, "u-01", "둘째 질문"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, Conflict) for r in results) == 1
    other = await store.create_chat("u-01", None)
    await store.begin_turn(other.id, "u-01", "다른 대화는 막지 않는다")


async def test_pending_turn_from_another_connection_conflicts(
    store: TurnStore, tmp_path: Path, clock: Clock
) -> None:
    # 같은 DB 파일을 쓰는 다른 인스턴스에서도 DB 가 하나의 pending 만 허락한다.
    chat = await store.create_chat("u-01", None)
    await store.begin_turn(chat.id, "u-01", "첫 질문")
    other = TurnStore(str(tmp_path / "t.db"), pending_ttl_s=60, clock=clock)
    await other.open()
    try:
        with pytest.raises(Conflict):
            await other.begin_turn(chat.id, "u-01", "둘째 질문")
    finally:
        await other.close()


async def test_stale_pending_is_cleaned_up(store: TurnStore, clock: Clock) -> None:
    chat = await store.create_chat("u-01", None)
    stale = await store.begin_turn(chat.id, "u-01", "멈춘 질문")
    clock.now += timedelta(seconds=61)
    fresh = await store.begin_turn(chat.id, "u-01", "새 질문")
    assert fresh.turn_no == 2
    statuses = {t.id: t.status for t in await store.list_turns("u-01", chat.id)}
    assert statuses == {stale.id: TurnStatus.ERROR, fresh.id: TurnStatus.PENDING}
    assert not await store.finish_turn(stale.id, TurnStatus.DONE, "{}", None)  # 정리된 턴은 덮지 않는다


async def test_owner_filter_hides_other_users_chats(store: TurnStore) -> None:
    chat = await store.create_chat("u-01", "내 대화")
    turn = await store.begin_turn(chat.id, "u-01", "질문")
    assert await store.list_chats("u-02") == []
    for call in (
        store.get_chat("u-02", chat.id),
        store.get_turn("u-02", chat.id, turn.id),
        store.begin_turn(chat.id, "u-02", "남의 대화"),
        store.rename("u-02", chat.id, "바꿈"),
        store.soft_delete_chat("u-02", chat.id),
        store.soft_delete_turn("u-02", chat.id, turn.id),
        store.request_cancel(turn.id, "u-02", chat.id),
    ):
        with pytest.raises(NotFound):
            await call
    assert await store.list_turns("u-02", chat.id) == []


async def test_hitl_streak_counts_latest_consecutive_hitl(store: TurnStore) -> None:
    chat = await store.create_chat("u-01", None)

    async def finish(status: TurnStatus) -> None:
        turn = await store.begin_turn(chat.id, "u-01", "q")
        await store.finish_turn(turn.id, status, "{}", None)

    assert await store.hitl_streak(chat.id) == 0
    await finish(TurnStatus.HITL)
    await finish(TurnStatus.HITL)
    assert await store.hitl_streak(chat.id) == 2
    await finish(TurnStatus.DONE)
    assert await store.hitl_streak(chat.id) == 0
    await store.begin_turn(chat.id, "u-01", "진행 중")  # pending 은 세지 않는다
    assert await store.hitl_streak(chat.id) == 0


async def test_history_has_only_done_and_undeleted_turns(store: TurnStore) -> None:
    chat = await store.create_chat("u-01", None)
    ids = []
    for status in (TurnStatus.DONE, TurnStatus.HITL, TurnStatus.DONE, TurnStatus.DONE):
        turn = await store.begin_turn(chat.id, "u-01", f"q{len(ids) + 1}")
        await store.finish_turn(turn.id, status, "{}", None)
        ids.append(turn.id)
    await store.soft_delete_turn("u-01", chat.id, ids[2])
    history = await store.recent_history(chat.id, 5)
    assert [t.query for t in history] == ["q1", "q4"]
    assert [t.query for t in await store.recent_history(chat.id, 1)] == ["q4"]


async def test_cancel_flag_and_chat_title(store: TurnStore) -> None:
    chat = await store.create_chat("u-01", None)
    turn = await store.begin_turn(chat.id, "u-01", "새솔물류 견적 협상 내용 알려줘")
    assert (await store.get_chat("u-01", chat.id)).title == "새솔물류 견적 협상 내용 알려줘"
    assert not await store.is_cancel_requested(turn.id)
    assert await store.request_cancel(turn.id, "u-01", chat.id) == "stopping"
    assert await store.is_cancel_requested(turn.id)
    await store.finish_turn(turn.id, TurnStatus.STOPPED, "{}", None)
    assert await store.request_cancel(turn.id, "u-01", chat.id) == "already_finished"


async def test_settings_roundtrip(store: TurnStore) -> None:
    assert await store.get_settings("u-01") is None
    await store.put_settings("u-01", "존댓말로 짧게")
    await store.put_settings("u-01", "표로 정리")
    assert await store.get_settings("u-01") == "표로 정리"
    assert await store.get_settings("u-02") is None
