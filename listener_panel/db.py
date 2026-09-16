"""监听面板数据库：关键词规则 + 命中记录（同步 sqlite3，跨线程/事件循环安全）。"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS hits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    chat_name TEXT DEFAULT '',
    sender_id INTEGER NOT NULL,
    sender_name TEXT DEFAULT '',
    text TEXT DEFAULT '',
    keyword TEXT NOT NULL,
    invited INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0,
    reported INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hits_created ON hits(created_at DESC);
"""


def _row_to_dict(cursor) -> dict:
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, cursor.fetchone())) if cursor.description else {}


class MonitorDB:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------- 关键词 ----------

    def list_keywords(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM keywords ORDER BY id DESC")
        return [dict(r) for r in cur.fetchall()]

    def add_keyword(self, keyword: str) -> bool:
        kw = keyword.strip()
        if not kw:
            return False
        try:
            self.conn.execute(
                "INSERT INTO keywords (keyword, enabled, created_at) VALUES (?, 1, ?)",
                (kw, int(time.time())),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_keyword(self, keyword: str) -> bool:
        cur = self.conn.execute("DELETE FROM keywords WHERE keyword = ?", (keyword,))
        self.conn.commit()
        return cur.rowcount > 0

    def set_enabled(self, keyword: str, enabled: bool) -> bool:
        cur = self.conn.execute(
            "UPDATE keywords SET enabled = ? WHERE keyword = ?", (1 if enabled else 0, keyword)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def active_keywords(self) -> list[str]:
        cur = self.conn.execute("SELECT keyword FROM keywords WHERE enabled = 1 ORDER BY id")
        return [r["keyword"] for r in cur.fetchall()]

    # ---------- 命中记录 ----------

    def add_hit(self, chat_id, chat_name, sender_id, sender_name, text, keyword) -> int:
        cur = self.conn.execute(
            """INSERT INTO hits
               (chat_id, chat_name, sender_id, sender_name, text, keyword, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (chat_id, chat_name, sender_id, sender_name, text, keyword, int(time.time())),
        )
        self.conn.commit()
        return cur.lastrowid

    def mark_hit(self, hit_id: int, invited=False, notified=False, reported=False) -> None:
        sets, params = [], []
        if invited:
            sets.append("invited = 1")
        if notified:
            sets.append("notified = 1")
        if reported:
            sets.append("reported = 1")
        if not sets:
            return
        params.append(hit_id)
        self.conn.execute(f"UPDATE hits SET {', '.join(sets)} WHERE id = ?", params)
        self.conn.commit()

    def list_hits(self, limit: int = 100) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM hits ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in cur.fetchall()]

    def stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) c FROM hits").fetchone()["c"]
        invited = self.conn.execute("SELECT COUNT(*) c FROM hits WHERE invited = 1").fetchone()["c"]
        active = self.conn.execute("SELECT COUNT(*) c FROM keywords WHERE enabled = 1").fetchone()["c"]
        return {"total_hits": total, "invited": invited, "active_keywords": active}