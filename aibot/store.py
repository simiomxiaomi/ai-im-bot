"""SQLite 持久化：消息流水、对话记忆、运行开关。

所有方法同步执行（WAL 模式 + 短事务），调用方可在 asyncio 中直接使用，
单条语句耗时在微秒级，不会造成可感知阻塞。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import IncomingMessage

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL    NOT NULL,
    platform      TEXT    NOT NULL,
    session_type  TEXT    NOT NULL,
    session_id    TEXT    NOT NULL,
    session_name  TEXT    DEFAULT '',
    message_id    TEXT    DEFAULT '',
    sender_id     TEXT    DEFAULT '',
    sender_name   TEXT    DEFAULT '',
    direction     TEXT    NOT NULL,           -- in / out
    text          TEXT    DEFAULT '',
    allowed       INTEGER DEFAULT 1,
    reason        TEXT    DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_messages_ts ON messages(ts);
CREATE INDEX IF NOT EXISTS ix_messages_session ON messages(session_type, session_id, ts);
CREATE UNIQUE INDEX IF NOT EXISTS ux_messages_dedupe
    ON messages(platform, message_id) WHERE direction = 'in' AND message_id <> '';

CREATE TABLE IF NOT EXISTS memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    session_key TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_memory_key ON memory(session_key, id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, db_path: Path, history_limit: int = 200) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._history_limit = max(history_limit, 20)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.commit()

    # ------------------------------------------------------------------ 消息

    def seen(self, platform: str, message_id: str) -> bool:
        if not message_id:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM messages WHERE platform=? AND message_id=? AND direction='in' LIMIT 1",
                (platform, message_id),
            ).fetchone()
        return row is not None

    def record_incoming(self, msg: IncomingMessage, allowed: bool, reason: str) -> None:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO messages(ts, platform, session_type, session_id, session_name,"
                    " message_id, sender_id, sender_name, direction, text, allowed, reason)"
                    " VALUES(?,?,?,?,?,?,?,?, 'in', ?,?,?)",
                    (
                        msg.timestamp,
                        msg.platform,
                        msg.session_type,
                        msg.session_id,
                        msg.session_name,
                        msg.message_id,
                        msg.sender_id,
                        msg.sender_name,
                        msg.text,
                        int(allowed),
                        reason,
                    ),
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                pass  # 重复消息

    def record_outgoing(
        self,
        platform: str,
        session_type: str,
        session_id: str,
        text: str,
        session_name: str = "",
        reason: str = "",
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages(ts, platform, session_type, session_id, session_name,"
                " direction, text, allowed, reason) VALUES(?,?,?,?,?, 'out', ?, 1, ?)",
                (time.time(), platform, session_type, session_id, session_name, text, reason),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def recent_messages(self, limit: int = 50, since_id: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE id > ? ORDER BY id DESC LIMIT ?",
                (since_id, max(1, min(limit, 500))),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ------------------------------------------------------------------ 记忆

    def add_turn(self, session_key: str, user_text: str, assistant_text: str) -> None:
        now = time.time()
        with self._lock:
            self._conn.executemany(
                "INSERT INTO memory(ts, session_key, role, content) VALUES(?,?,?,?)",
                [
                    (now, session_key, "user", user_text),
                    (now, session_key, "assistant", assistant_text),
                ],
            )
            # 只保留最近 N 条，避免数据库无限增长
            self._conn.execute(
                "DELETE FROM memory WHERE session_key=? AND id NOT IN "
                "(SELECT id FROM memory WHERE session_key=? ORDER BY id DESC LIMIT ?)",
                (session_key, session_key, self._history_limit),
            )
            self._conn.commit()

    def history(self, session_key: str, limit: int = 8) -> list[dict[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM memory WHERE session_key=? ORDER BY id DESC LIMIT ?",
                (session_key, max(0, limit) * 2),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def reset_session(self, session_key: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM memory WHERE session_key=?", (session_key,))
            self._conn.commit()
            return cur.rowcount

    def clear_all_memory(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM memory")
            self._conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------ 开关

    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_bool(self, key: str, default: bool) -> bool:
        raw = self.get_setting(key, "1" if default else "0")
        return raw.lower() in {"1", "true", "yes", "on"}

    # ------------------------------------------------------------------ 统计

    def stats(self) -> dict[str, Any]:
        midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self._lock:
            row = self._conn.execute(
                "SELECT "
                " SUM(CASE WHEN direction='in' THEN 1 ELSE 0 END) AS incoming,"
                " SUM(CASE WHEN direction='out' THEN 1 ELSE 0 END) AS outgoing,"
                " SUM(CASE WHEN direction='in' AND allowed=0 THEN 1 ELSE 0 END) AS blocked"
                " FROM messages WHERE ts >= ?",
                (midnight,),
            ).fetchone()
            sessions = self._conn.execute(
                "SELECT COUNT(DISTINCT session_type || ':' || session_id) AS n FROM messages"
                " WHERE ts >= ?",
                (midnight,),
            ).fetchone()
            total = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()
        return {
            "today_incoming": int(row["incoming"] or 0),
            "today_outgoing": int(row["outgoing"] or 0),
            "today_blocked": int(row["blocked"] or 0),
            "today_sessions": int(sessions["n"] or 0),
            "total_messages": int(total["n"] or 0),
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
