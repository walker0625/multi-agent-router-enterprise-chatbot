"""대화·턴 저장소 (SQLite 하나).

- 턴 행은 턴을 시작하는 시점에 `pending` 으로 쓴다. 한 대화에 `pending` 이 둘 생기지 않도록
  부분 유니크 인덱스로 DB 가 보장한다. 같은 파일을 여러 인스턴스가 함께 써도 동시 요청이
  질문·답을 엇갈리게 만들지 못한다.
- 종료 기록은 `status='pending'` 인 행만 바꾸는 조건부 갱신(compare-and-set)이다. 늦게
  도착한 쓰기가 이미 끝났거나 정리된 턴을 덮지 않는다.
- 중단 요청은 행의 플래그로 전달한다. 스트림을 돌리는 인스턴스와 중단 API 를 받은
  인스턴스가 달라도 동작한다.
- 대화 목록 API 와 멀티턴 맥락이 같은 테이블을 읽는다. 기억은 한 벌만 둔다.
- 모든 조회에 소유자 조건을 건다. 남의 대화는 "없음"(404)과 구별되지 않는다.
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel

from chatcore.errors import Conflict, NotFound
from chatcore.events import TurnStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS chats_by_owner ON chats(owner, updated_at);
CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL REFERENCES chats(id),
    turn_no INTEGER NOT NULL,
    status TEXT NOT NULL,
    query TEXT NOT NULL,
    plan_json TEXT,
    payload_json TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(chat_id, turn_no)
);
CREATE UNIQUE INDEX IF NOT EXISTS turns_one_pending ON turns(chat_id) WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS user_settings (
    owner TEXT PRIMARY KEY,
    system_prompt TEXT
);
"""

_TURN_COLUMNS = "t.id, t.chat_id, t.turn_no, t.status, t.query, t.plan_json, t.payload_json, t.created_at"


class ChatRecord(BaseModel):
    id: str
    title: str | None
    created_at: str
    updated_at: str


class TurnRecord(BaseModel):
    id: str
    chat_id: str
    turn_no: int
    status: TurnStatus
    query: str
    plan_json: str | None = None
    payload_json: str | None = None
    created_at: str


def _turn(row: Any) -> TurnRecord:
    keys = ("id", "chat_id", "turn_no", "status", "query", "plan_json", "payload_json", "created_at")
    return TurnRecord.model_validate(dict(zip(keys, row, strict=True)))


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TurnStore:
    def __init__(self, path: str, *, pending_ttl_s: float, clock: Callable[[], datetime] = _utcnow) -> None:
        self._path = path
        self._pending_ttl_s = pending_ttl_s
        self._clock = clock
        self._db: aiosqlite.Connection | None = None
        # 연결 하나를 여러 코루틴이 같이 쓰므로, 여러 문장으로 된 트랜잭션이 섞이지 않게 직렬화한다.
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: 암묵적 BEGIN 을 끄고 트랜잭션 경계를 직접 정한다.
        self._db = await aiosqlite.connect(self._path, isolation_level=None)
        await self._db.execute("PRAGMA busy_timeout = 5000")
        await self._db.execute("PRAGMA foreign_keys = ON")
        if self._path != ":memory:":
            await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.executescript(_SCHEMA)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "store is not open"
        return self._db

    def _now(self) -> str:
        return self._clock().isoformat(timespec="milliseconds")

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[aiosqlite.Connection]:
        # BEGIN IMMEDIATE: 쓰기 잠금을 먼저 잡아 다른 프로세스와의 확인→삽입 사이 경쟁을 없앤다.
        async with self._lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
            except BaseException:
                await self.db.execute("ROLLBACK")
                raise
            await self.db.execute("COMMIT")

    async def _one(self, sql: str, params: tuple[Any, ...]) -> Any:
        async with self._lock, self.db.execute(sql, params) as cur:
            return await cur.fetchone()

    async def _all(self, sql: str, params: tuple[Any, ...]) -> list[Any]:
        async with self._lock, self.db.execute(sql, params) as cur:
            return list(await cur.fetchall())

    async def _write(self, sql: str, params: tuple[Any, ...]) -> int:
        async with self._lock, self.db.execute(sql, params) as cur:
            return cur.rowcount

    async def ping(self) -> None:
        await self._one("SELECT 1", ())

    # ---- 대화 ----

    async def create_chat(self, owner: str, title: str | None) -> ChatRecord:
        now = self._now()
        chat = ChatRecord(id=uuid.uuid4().hex, title=title, created_at=now, updated_at=now)
        await self._write(
            "INSERT INTO chats (id, owner, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (chat.id, owner, title, now, now),
        )
        return chat

    async def list_chats(self, owner: str) -> list[ChatRecord]:
        rows = await self._all(
            "SELECT id, title, created_at, updated_at FROM chats "
            "WHERE owner = ? AND deleted = 0 ORDER BY updated_at DESC",
            (owner,),
        )
        return [ChatRecord(id=r[0], title=r[1], created_at=r[2], updated_at=r[3]) for r in rows]

    async def get_chat(self, owner: str, chat_id: str) -> ChatRecord:
        row = await self._one(
            "SELECT id, title, created_at, updated_at FROM chats WHERE id = ? AND owner = ? AND deleted = 0",
            (chat_id, owner),
        )
        if row is None:
            raise NotFound(f"chat {chat_id} not found for owner")
        return ChatRecord(id=row[0], title=row[1], created_at=row[2], updated_at=row[3])

    async def list_turns(self, owner: str, chat_id: str) -> list[TurnRecord]:
        rows = await self._all(
            f"SELECT {_TURN_COLUMNS} FROM turns t JOIN chats c ON c.id = t.chat_id "
            "WHERE t.chat_id = ? AND c.owner = ? AND c.deleted = 0 AND t.deleted = 0 ORDER BY t.turn_no",
            (chat_id, owner),
        )
        return [_turn(r) for r in rows]

    async def rename(self, owner: str, chat_id: str, title: str) -> None:
        changed = await self._write(
            "UPDATE chats SET title = ?, updated_at = ? WHERE id = ? AND owner = ? AND deleted = 0",
            (title, self._now(), chat_id, owner),
        )
        if not changed:
            raise NotFound(f"chat {chat_id} not found for owner")

    async def soft_delete_chat(self, owner: str, chat_id: str) -> None:
        changed = await self._write(
            "UPDATE chats SET deleted = 1, updated_at = ? WHERE id = ? AND owner = ? AND deleted = 0",
            (self._now(), chat_id, owner),
        )
        if not changed:
            raise NotFound(f"chat {chat_id} not found for owner")

    # ---- 턴 ----

    async def get_turn(self, owner: str, chat_id: str, turn_id: str) -> TurnRecord:
        row = await self._one(
            f"SELECT {_TURN_COLUMNS} FROM turns t JOIN chats c ON c.id = t.chat_id "
            "WHERE t.id = ? AND t.chat_id = ? AND c.owner = ? AND c.deleted = 0 AND t.deleted = 0",
            (turn_id, chat_id, owner),
        )
        if row is None:
            raise NotFound(f"turn {turn_id} not found for owner")
        return _turn(row)

    async def soft_delete_turn(self, owner: str, chat_id: str, turn_id: str) -> None:
        changed = await self._write(
            "UPDATE turns SET deleted = 1, updated_at = ? WHERE id = ? AND chat_id = ? AND deleted = 0 "
            "AND chat_id IN (SELECT id FROM chats WHERE owner = ? AND deleted = 0)",
            (self._now(), turn_id, chat_id, owner),
        )
        if not changed:
            raise NotFound(f"turn {turn_id} not found for owner")

    async def begin_turn(self, chat_id: str, owner: str, query: str) -> TurnRecord:
        now = self._now()
        stale_before = (self._clock() - timedelta(seconds=self._pending_ttl_s)).isoformat(
            timespec="milliseconds"
        )
        turn_id = uuid.uuid4().hex
        try:
            async with self._tx() as db:
                async with db.execute(
                    "SELECT 1 FROM chats WHERE id = ? AND owner = ? AND deleted = 0", (chat_id, owner)
                ) as cur:
                    if await cur.fetchone() is None:
                        raise NotFound(f"chat {chat_id} not found for owner")
                # 예산 시간을 넘긴 pending 은 끝내지 못한 턴이다(프로세스 종료 등). 정리하고 진행한다.
                await db.execute(
                    "UPDATE turns SET status = 'error', updated_at = ? "
                    "WHERE chat_id = ? AND status = 'pending' AND created_at < ?",
                    (now, chat_id, stale_before),
                )
                async with db.execute(
                    "SELECT COALESCE(MAX(turn_no), 0) + 1 FROM turns WHERE chat_id = ?", (chat_id,)
                ) as cur:
                    row = await cur.fetchone()
                    turn_no = int(row[0]) if row else 1
                await db.execute(
                    "INSERT INTO turns (id, chat_id, turn_no, status, query, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'pending', ?, ?, ?)",
                    (turn_id, chat_id, turn_no, query, now, now),
                )
                # 제목 없이 만든 대화는 첫 질문을 제목으로 쓴다.
                await db.execute(
                    "UPDATE chats SET updated_at = ?, title = COALESCE(title, ?) WHERE id = ?",
                    (now, query[:40], chat_id),
                )
        except sqlite3.IntegrityError as exc:
            raise Conflict(f"chat {chat_id} already has a pending turn") from exc
        return TurnRecord(
            id=turn_id,
            chat_id=chat_id,
            turn_no=turn_no,
            status=TurnStatus.PENDING,
            query=query,
            created_at=now,
        )

    async def finish_turn(
        self, turn_id: str, status: TurnStatus, payload_json: str, plan_json: str | None
    ) -> bool:
        changed = await self._write(
            "UPDATE turns SET status = ?, payload_json = ?, plan_json = ?, updated_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (status.value, payload_json, plan_json, self._now(), turn_id),
        )
        return changed == 1

    async def recent_history(self, chat_id: str, limit: int) -> list[TurnRecord]:
        """완료(`done`)·미삭제 턴 중 최근 `limit` 개를 오래된 순서로."""
        rows = await self._all(
            f"SELECT {_TURN_COLUMNS} FROM turns t "
            "WHERE t.chat_id = ? AND t.status = 'done' AND t.deleted = 0 ORDER BY t.turn_no DESC LIMIT ?",
            (chat_id, limit),
        )
        return [_turn(r) for r in reversed(rows)]

    async def hitl_streak(self, chat_id: str) -> int:
        """끝난 턴 중 가장 최근부터 연속된 HITL 턴 수."""
        rows = await self._all(
            "SELECT status FROM turns WHERE chat_id = ? AND deleted = 0 AND status != 'pending' "
            "ORDER BY turn_no DESC LIMIT 20",
            (chat_id,),
        )
        streak = 0
        for (status,) in rows:
            if status != TurnStatus.HITL.value:
                break
            streak += 1
        return streak

    async def request_cancel(
        self, turn_id: str, owner: str, chat_id: str
    ) -> Literal["stopping", "already_finished"]:
        turn = await self.get_turn(owner, chat_id, turn_id)
        changed = await self._write(
            "UPDATE turns SET cancel_requested = 1, updated_at = ? WHERE id = ? AND status = 'pending'",
            (self._now(), turn.id),
        )
        return "stopping" if changed else "already_finished"

    async def is_cancel_requested(self, turn_id: str) -> bool:
        row = await self._one("SELECT cancel_requested FROM turns WHERE id = ?", (turn_id,))
        return bool(row and row[0])

    # ---- 사용자 설정 ----

    async def get_settings(self, owner: str) -> str | None:
        row = await self._one("SELECT system_prompt FROM user_settings WHERE owner = ?", (owner,))
        return row[0] if row else None

    async def put_settings(self, owner: str, system_prompt: str | None) -> None:
        await self._write(
            "INSERT INTO user_settings (owner, system_prompt) VALUES (?, ?) "
            "ON CONFLICT(owner) DO UPDATE SET system_prompt = excluded.system_prompt",
            (owner, system_prompt),
        )
