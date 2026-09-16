"""SQLite 消息持久化（aiosqlite 全异步）。"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from .models import MessageRecord

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    chat_type TEXT NOT NULL,
    user_id INTEGER,
    username TEXT,
    message_type TEXT NOT NULL,
    content TEXT,
    media_file_id TEXT,
    timestamp TEXT NOT NULL,
    ai_response TEXT,
    feishu_sent INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages (chat_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages (user_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages (timestamp);

CREATE TABLE IF NOT EXISTS baobei (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER,
    username TEXT,
    content TEXT,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_baobei_timestamp ON baobei (timestamp);
"""


class Database:
    """封装 SQLite 读写。所有方法均为 async，内部自带互斥锁防止并发写冲突。"""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        """初始化：建目录 + 建表。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.executescript(_SCHEMA)
                await db.commit()
        logger.info("数据库初始化完成: %s", self.db_path)

    async def close(self) -> None:
        logger.info("数据库已关闭")

    async def insert_message(self, record: MessageRecord) -> int:
        """插入一条消息，返回自增 id。"""
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cur = await db.execute(
                    """
                    INSERT INTO messages
                        (chat_id, chat_type, user_id, username, message_type,
                         content, media_file_id, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.chat_id,
                        record.chat_type,
                        record.user_id,
                        record.username,
                        record.message_type,
                        record.content,
                        record.media_file_id,
                        record.timestamp,
                    ),
                )
                await db.commit()
                return int(cur.lastrowid)

    async def update_ai_response(self, msg_id: int, response: str) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE messages SET ai_response = ? WHERE id = ?",
                    (response, msg_id),
                )
                await db.commit()

    async def update_feishu_sent(self, msg_id: int, sent: bool = True) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE messages SET feishu_sent = ? WHERE id = ?",
                    (1 if sent else 0, msg_id),
                )
                await db.commit()

    async def get_message(self, msg_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM messages WHERE id = ?", (msg_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def recent_messages(self, limit: int = 50) -> list[dict]:
        """最近消息（倒序）。"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def insert_baobei(self, chat_id: int, user_id, username, content: str, timestamp: str) -> int:
        """记录一次报备，返回自增 id。"""
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cur = await db.execute(
                    """
                    INSERT INTO baobei (chat_id, user_id, username, content, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (chat_id, user_id, username, content, timestamp),
                )
                await db.commit()
                return int(cur.lastrowid)

    async def count_baobei(self) -> int:
        """报备总次数。"""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM baobei")
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    # ---------- 统计 ----------

    async def count_by_day(self, days: int = 7) -> list[dict]:
        """按天统计消息数。"""
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """
                SELECT substr(timestamp, 1, 10) AS day, COUNT(*) AS cnt
                FROM messages
                WHERE timestamp >= ?
                GROUP BY day
                ORDER BY day DESC
                """,
                (since,),
            )
            rows = await cur.fetchall()
            return [{"day": r[0], "count": r[1]} for r in rows]

    async def count_by_chat(self, limit: int = 20) -> list[dict]:
        """按群统计消息数。"""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """
                SELECT chat_id, chat_type, COUNT(*) AS cnt
                FROM messages
                GROUP BY chat_id
                ORDER BY cnt DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cur.fetchall()
            return [
                {"chat_id": r[0], "chat_type": r[1], "count": r[2]} for r in rows
            ]

    async def count_by_user(self, limit: int = 20) -> list[dict]:
        """按用户统计消息数。"""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """
                SELECT user_id, username, COUNT(*) AS cnt
                FROM messages
                GROUP BY user_id
                ORDER BY cnt DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cur.fetchall()
            return [
                {"user_id": r[0], "username": r[1], "count": r[2]} for r in rows
            ]
