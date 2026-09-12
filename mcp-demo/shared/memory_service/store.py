"""
会话记忆 + 用户画像存储 — SQLite。

- memory_messages 表：按 project_id + session_id 隔离的对话记录
- user_facts 表：按 user_id 存取的跨项目用户事实
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class MemoryStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS memory_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_memory_ps_id
                    ON memory_messages (project_id, session_id, id);

                    CREATE TABLE IF NOT EXISTS user_facts (
                        id             INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id        TEXT NOT NULL,
                        fact_key       TEXT NOT NULL,
                        fact_value     TEXT NOT NULL,
                        source_project TEXT NOT NULL DEFAULT '',
                        updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
                        UNIQUE(user_id, fact_key)
                    );
                    CREATE INDEX IF NOT EXISTS idx_user_facts_uid
                    ON user_facts (user_id);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def save_memory(
        self,
        project_id: str,
        session_id: str,
        role: str,
        content: str,
    ) -> dict:
        role = (role or "").strip()
        if role not in ("user", "assistant"):
            return {
                "status": "error",
                "message": 'role 必须是 "user" 或 "assistant"',
            }
        if not (content or "").strip():
            return {"status": "error", "message": "content 不能为空"}

        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO memory_messages (project_id, session_id, role, content)
                    VALUES (?, ?, ?, ?)
                    """,
                    (project_id, session_id, role, content),
                )
                conn.commit()
                row_id = cur.lastrowid
            finally:
                conn.close()

        return {
            "status": "ok",
            "id": row_id,
            "project_id": project_id,
            "session_id": session_id,
        }

    def recall_memory(
        self,
        project_id: str,
        session_id: str,
        last_n: int = 10,
    ) -> dict:
        n = max(1, min(int(last_n), 500))

        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    SELECT role, content, id
                    FROM memory_messages
                    WHERE project_id = ? AND session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (project_id, session_id, n),
                )
                rows = list(cur)
            finally:
                conn.close()

        rows.reverse()
        messages = [{"role": r["role"], "content": r["content"]} for r in rows]

        return {
            "project_id": project_id,
            "session_id": session_id,
            "messages": messages,
            "count": len(messages),
        }

    def clear_memory(self, project_id: str, session_id: str) -> dict:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    DELETE FROM memory_messages
                    WHERE project_id = ? AND session_id = ?
                    """,
                    (project_id, session_id),
                )
                conn.commit()
                deleted = cur.rowcount
            finally:
                conn.close()

        return {
            "status": "ok",
            "project_id": project_id,
            "session_id": session_id,
            "deleted": deleted,
        }

    # ── 用户画像（跨项目） ──────────────────────────────

    def save_user_fact(
        self,
        user_id: str,
        fact_key: str,
        fact_value: str,
        source_project: str = "",
    ) -> dict:
        if not (user_id or "").strip():
            return {"status": "error", "message": "user_id 不能为空"}
        if not (fact_key or "").strip():
            return {"status": "error", "message": "fact_key 不能为空"}
        if not (fact_value or "").strip():
            return {"status": "error", "message": "fact_value 不能为空"}

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO user_facts (user_id, fact_key, fact_value, source_project, updated_at)
                    VALUES (?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(user_id, fact_key) DO UPDATE SET
                        fact_value     = excluded.fact_value,
                        source_project = excluded.source_project,
                        updated_at     = datetime('now')
                    """,
                    (user_id.strip(), fact_key.strip(), fact_value.strip(), source_project),
                )
                conn.commit()
            finally:
                conn.close()

        return {
            "status": "ok",
            "user_id": user_id,
            "fact_key": fact_key,
            "fact_value": fact_value,
        }

    def recall_user_facts(self, user_id: str) -> dict:
        if not (user_id or "").strip():
            return {"user_id": user_id, "facts": [], "count": 0}

        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    SELECT fact_key, fact_value, source_project, updated_at
                    FROM user_facts
                    WHERE user_id = ?
                    ORDER BY updated_at ASC
                    """,
                    (user_id.strip(),),
                )
                rows = list(cur)
            finally:
                conn.close()

        facts = [
            {
                "key": r["fact_key"],
                "value": r["fact_value"],
                "source": r["source_project"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
        return {"user_id": user_id, "facts": facts, "count": len(facts)}

    def delete_user_fact(self, user_id: str, fact_key: str) -> dict:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    DELETE FROM user_facts
                    WHERE user_id = ? AND fact_key = ?
                    """,
                    (user_id.strip(), fact_key.strip()),
                )
                conn.commit()
                deleted = cur.rowcount
            finally:
                conn.close()

        return {
            "status": "ok",
            "user_id": user_id,
            "fact_key": fact_key,
            "deleted": deleted,
        }
