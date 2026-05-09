from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite


class MessageRepository:
    """负责群消息的异步持久化与查询。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row

        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT,
                group_id INTEGER NOT NULL,
                group_name TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                nickname TEXT NOT NULL,
                content TEXT NOT NULL,
                msg_type TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_group_time
            ON messages(group_id, timestamp);

            CREATE INDEX IF NOT EXISTS idx_messages_timestamp
            ON messages(timestamp);

            CREATE INDEX IF NOT EXISTS idx_messages_type
            ON messages(msg_type);
            """
        )
        await self._ensure_message_id_column()
        await self._conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_group_message_id
            ON messages(group_id, message_id)
            WHERE message_id IS NOT NULL
            """
        )
        await self._conn.commit()

    async def _ensure_message_id_column(self) -> None:
        conn = self._ensure_conn()
        cursor = await conn.execute("PRAGMA table_info(messages)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "message_id" not in columns:
            await conn.execute("ALTER TABLE messages ADD COLUMN message_id TEXT")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def save_message(self, message: dict[str, Any]) -> int:
        conn = self._ensure_conn()
        cursor = await conn.execute(
            """
            INSERT OR IGNORE INTO messages (
                message_id,
                group_id,
                group_name,
                user_id,
                nickname,
                content,
                msg_type,
                raw_json,
                timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.get("message_id"),
                message["group_id"],
                message["group_name"],
                message["user_id"],
                message["nickname"],
                message["content"],
                message["msg_type"],
                json.dumps(message["raw_json"], ensure_ascii=False),
                message["timestamp"],
            ),
        )
        await conn.commit()
        return cursor.lastrowid

    async def search_messages(
        self,
        keyword: str,
        group_ids: list[int] | None = None,
        since: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        conn = self._ensure_conn()
        sql = """
            SELECT id, group_id, group_name, user_id, nickname, content, msg_type, timestamp
            FROM messages
            WHERE content LIKE ?
        """
        params: list[Any] = [f"%{keyword.strip()}%"]

        if group_ids:
            placeholders = ",".join("?" for _ in group_ids)
            sql += f" AND group_id IN ({placeholders})"
            params.extend(group_ids)

        if since:
            sql += " AND timestamp >= ?"
            params.append(since)

        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_messages_since(
        self,
        since: str,
        group_ids: list[int] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        conn = self._ensure_conn()
        sql = """
            SELECT id, group_id, group_name, user_id, nickname, content, msg_type, timestamp
            FROM messages
            WHERE timestamp >= ?
        """
        params: list[Any] = [since]

        if group_ids:
            placeholders = ",".join("?" for _ in group_ids)
            sql += f" AND group_id IN ({placeholders})"
            params.extend(group_ids)

        sql += " ORDER BY timestamp ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_recent_messages(
        self,
        group_ids: list[int] | None = None,
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        conn = self._ensure_conn()
        sql = """
            SELECT id, group_id, group_name, user_id, nickname, content, msg_type, timestamp
            FROM messages
            WHERE 1 = 1
        """
        params: list[Any] = []

        if group_ids:
            placeholders = ",".join("?" for _ in group_ids)
            sql += f" AND group_id IN ({placeholders})"
            params.extend(group_ids)

        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def resolve_groups(self, query: str) -> list[dict[str, Any]]:
        conn = self._ensure_conn()
        text = query.strip()
        if not text:
            return []

        cursor = await conn.execute(
            """
            SELECT DISTINCT group_id, group_name
            FROM messages
            WHERE CAST(group_id AS TEXT) = ? OR group_name LIKE ?
            ORDER BY group_id ASC
            """,
            (text, f"%{text}%"),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def list_recent_groups(self) -> list[dict[str, Any]]:
        conn = self._ensure_conn()
        cursor = await conn.execute(
            """
            SELECT group_id, MAX(group_name) AS group_name
            FROM messages
            GROUP BY group_id
            ORDER BY group_id ASC
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    def _ensure_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("数据库尚未初始化")
        return self._conn
