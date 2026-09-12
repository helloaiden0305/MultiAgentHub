"""SQLite 控制面仓储：项目、Endpoint、订阅、路由与调用明细。"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path
from typing import Any

import yaml


class ControlPlaneStore:
    """第二阶段控制面数据的本地 SQLite 实现。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS projects (
                        project_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL DEFAULT 'enabled',
                        daily_token_limit INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                    );

                    CREATE TABLE IF NOT EXISTS api_keys (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_id TEXT NOT NULL REFERENCES projects(project_id),
                        key_hash TEXT NOT NULL UNIQUE,
                        key_hint TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'enabled',
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_api_keys_project ON api_keys(project_id);

                    CREATE TABLE IF NOT EXISTS endpoints (
                        endpoint_id TEXT PRIMARY KEY,
                        provider TEXT NOT NULL,
                        model_id TEXT NOT NULL,
                        base_url_env TEXT NOT NULL,
                        api_key_env TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'enabled',
                        timeout_ms INTEGER NOT NULL DEFAULT 60000,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                    );

                    CREATE TABLE IF NOT EXISTS logical_routes (
                        logical_name TEXT PRIMARY KEY,
                        display_name TEXT NOT NULL,
                        description TEXT NOT NULL DEFAULT '',
                        primary_endpoint_id TEXT NOT NULL REFERENCES endpoints(endpoint_id),
                        fallback_endpoint_id TEXT REFERENCES endpoints(endpoint_id),
                        input_token_price REAL NOT NULL DEFAULT 0,
                        output_token_price REAL NOT NULL DEFAULT 0,
                        retry_limit INTEGER NOT NULL DEFAULT 1,
                        status TEXT NOT NULL DEFAULT 'enabled',
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                    );

                    CREATE TABLE IF NOT EXISTS project_subscriptions (
                        project_id TEXT NOT NULL REFERENCES projects(project_id),
                        logical_name TEXT NOT NULL REFERENCES logical_routes(logical_name),
                        status TEXT NOT NULL DEFAULT 'enabled',
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        PRIMARY KEY (project_id, logical_name)
                    );

                    CREATE TABLE IF NOT EXISTS llm_call_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chain_id TEXT NOT NULL,
                        trace_id TEXT NOT NULL DEFAULT '',
                        project_id TEXT NOT NULL,
                        logical_name TEXT NOT NULL,
                        actual_model TEXT NOT NULL DEFAULT '',
                        endpoint_id TEXT NOT NULL DEFAULT '',
                        route_reason TEXT NOT NULL DEFAULT '',
                        input_tokens INTEGER NOT NULL DEFAULT 0,
                        output_tokens INTEGER NOT NULL DEFAULT 0,
                        total_tokens INTEGER NOT NULL DEFAULT 0,
                        estimated_cost REAL NOT NULL DEFAULT 0,
                        status TEXT NOT NULL,
                        duration_ms INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_llm_call_records_query
                    ON llm_call_records(project_id, logical_name, created_at);

                    CREATE TABLE IF NOT EXISTS config_audit_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        actor TEXT NOT NULL,
                        action TEXT NOT NULL,
                        resource_type TEXT NOT NULL,
                        resource_id TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    );
                    """
                )
                self._ensure_llm_call_record_columns(conn)
                self._ensure_logical_route_columns(conn)
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _ensure_llm_call_record_columns(conn: sqlite3.Connection) -> None:
        """兼容已存在的本地 SQLite 文件，避免改造时要求删除数据。"""
        existing = {
            row["name"] for row in conn.execute("PRAGMA table_info(llm_call_records)").fetchall()
        }
        additions = {
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
            "fallback_used": "INTEGER NOT NULL DEFAULT 0",
            "attempted_endpoint_ids": "TEXT NOT NULL DEFAULT ''",
            "error_type": "TEXT NOT NULL DEFAULT ''",
            "ttft_ms": "INTEGER",
            "streamed": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, definition in additions.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE llm_call_records ADD COLUMN {name} {definition}")

    @staticmethod
    def _ensure_logical_route_columns(conn: sqlite3.Connection) -> None:
        existing = {
            row["name"] for row in conn.execute("PRAGMA table_info(logical_routes)").fetchall()
        }
        if "retry_limit" not in existing:
            conn.execute(
                "ALTER TABLE logical_routes ADD COLUMN retry_limit INTEGER NOT NULL DEFAULT 1"
            )

    @staticmethod
    def _hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @staticmethod
    def _key_hint(raw_key: str) -> str:
        if len(raw_key) <= 8:
            return "***"
        return f"{raw_key[:4]}...{raw_key[-4:]}"

    def seed_from_gateway_config(
        self,
        config_path: Path,
        chat_model: str,
        endpoint_id: str = "ark-report-primary",
    ) -> dict[str, int]:
        """从现有网关 YAML 初始化控制面，重复执行不会覆盖管理配置。"""
        with config_path.open(encoding="utf-8") as file:
            config = yaml.safe_load(file) or {}

        projects = config.get("projects", {})
        inserted_projects = 0
        inserted_keys = 0
        inserted_subscriptions = 0

        with self._lock:
            conn = self._connect()
            try:
                for project_id, project in projects.items():
                    quota = project.get("quota", {})
                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO projects (project_id, daily_token_limit)
                        VALUES (?, ?)
                        """,
                        (project_id, int(quota.get("daily_tokens", 0))),
                    )
                    inserted_projects += cur.rowcount

                    raw_key = str(project.get("api_key", "")).strip()
                    if raw_key:
                        cur = conn.execute(
                            """
                            INSERT OR IGNORE INTO api_keys (project_id, key_hash, key_hint)
                            VALUES (?, ?, ?)
                            """,
                            (project_id, self._hash_key(raw_key), self._key_hint(raw_key)),
                        )
                        inserted_keys += cur.rowcount

                conn.execute(
                    """
                    INSERT OR IGNORE INTO endpoints (
                        endpoint_id, provider, model_id, base_url_env, api_key_env
                    ) VALUES (?, 'ark', ?, 'ARK_BASE_URL', 'ARK_API_KEY')
                    """,
                    (endpoint_id, chat_model or "ARK_CHAT_MODEL"),
                )
                if chat_model:
                    conn.execute(
                        """
                        UPDATE endpoints
                        SET model_id = ?, updated_at = datetime('now')
                        WHERE endpoint_id = ? AND model_id = 'ARK_CHAT_MODEL'
                        """,
                        (chat_model, endpoint_id),
                    )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO logical_routes (
                        logical_name, display_name, description, primary_endpoint_id
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        "report-generation",
                        "测试报告与问题复盘",
                        "ODM 测试报告与问题复盘助手的稳定调用场景",
                        endpoint_id,
                    ),
                )

                if "odm-report-agent" in projects:
                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO project_subscriptions (project_id, logical_name)
                        VALUES ('odm-report-agent', 'report-generation')
                        """
                    )
                    inserted_subscriptions += cur.rowcount

                conn.commit()
            finally:
                conn.close()

        return {
            "projects": inserted_projects,
            "api_keys": inserted_keys,
            "subscriptions": inserted_subscriptions,
        }

    def resolve_project_by_api_key(self, raw_key: str) -> dict[str, Any] | None:
        """通过 API Key 哈希查找项目，不向调用方返回原始 Key。"""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT p.project_id, p.status, p.daily_token_limit, k.key_hint, k.status AS key_status
                    FROM api_keys k
                    JOIN projects p ON p.project_id = k.project_id
                    WHERE k.key_hash = ?
                    """,
                    (self._hash_key(raw_key),),
                ).fetchone()
            finally:
                conn.close()
        return dict(row) if row else None

    def get_route(self, logical_name: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT r.logical_name, r.display_name, r.description,
                           r.primary_endpoint_id, r.fallback_endpoint_id,
                           r.input_token_price, r.output_token_price, r.retry_limit, r.status,
                           e.provider, e.model_id, e.base_url_env, e.api_key_env,
                           e.timeout_ms, e.status AS endpoint_status
                    FROM logical_routes r
                    JOIN endpoints e ON e.endpoint_id = r.primary_endpoint_id
                    WHERE r.logical_name = ?
                    """,
                    (logical_name,),
                ).fetchone()
            finally:
                conn.close()
        return dict(row) if row else None

    def get_route_candidates(self, logical_name: str) -> dict[str, Any] | None:
        """读取主备 Endpoint 配置，不在此处读取任何上游密钥。"""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT r.logical_name, r.display_name, r.description,
                           r.input_token_price, r.output_token_price, r.retry_limit, r.status,
                           primary_endpoint.endpoint_id AS primary_endpoint_id,
                           primary_endpoint.provider AS primary_provider,
                           primary_endpoint.model_id AS primary_model_id,
                           primary_endpoint.base_url_env AS primary_base_url_env,
                           primary_endpoint.api_key_env AS primary_api_key_env,
                           primary_endpoint.timeout_ms AS primary_timeout_ms,
                           primary_endpoint.status AS primary_status,
                           fallback_endpoint.endpoint_id AS fallback_endpoint_id,
                           fallback_endpoint.provider AS fallback_provider,
                           fallback_endpoint.model_id AS fallback_model_id,
                           fallback_endpoint.base_url_env AS fallback_base_url_env,
                           fallback_endpoint.api_key_env AS fallback_api_key_env,
                           fallback_endpoint.timeout_ms AS fallback_timeout_ms,
                           fallback_endpoint.status AS fallback_status
                    FROM logical_routes r
                    JOIN endpoints primary_endpoint ON primary_endpoint.endpoint_id = r.primary_endpoint_id
                    LEFT JOIN endpoints fallback_endpoint ON fallback_endpoint.endpoint_id = r.fallback_endpoint_id
                    WHERE r.logical_name = ?
                    """,
                    (logical_name,),
                ).fetchone()
            finally:
                conn.close()
        return dict(row) if row else None

    def is_subscribed(self, project_id: str, logical_name: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT 1
                    FROM project_subscriptions
                    WHERE project_id = ? AND logical_name = ? AND status = 'enabled'
                    """,
                    (project_id, logical_name),
                ).fetchone()
            finally:
                conn.close()
        return row is not None

    def record_llm_call(
        self,
        *,
        chain_id: str,
        trace_id: str,
        project_id: str,
        logical_name: str,
        actual_model: str,
        endpoint_id: str,
        route_reason: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        estimated_cost: float,
        status: str,
        duration_ms: int,
        retry_count: int = 0,
        fallback_used: bool = False,
        attempted_endpoint_ids: str = "",
        error_type: str = "",
        ttft_ms: int | None = None,
        streamed: bool = False,
    ) -> int:
        """持久化模型调用治理字段，不记录请求或响应正文。"""
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO llm_call_records (
                        chain_id, trace_id, project_id, logical_name,
                        actual_model, endpoint_id, route_reason,
                        input_tokens, output_tokens, total_tokens,
                        estimated_cost, status, duration_ms, retry_count, fallback_used,
                        attempted_endpoint_ids, error_type, ttft_ms, streamed
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chain_id,
                        trace_id,
                        project_id,
                        logical_name,
                        actual_model,
                        endpoint_id,
                        route_reason,
                        max(0, int(input_tokens)),
                        max(0, int(output_tokens)),
                        max(0, int(total_tokens)),
                        max(0.0, float(estimated_cost)),
                        status,
                        max(0, int(duration_ms)),
                        max(0, int(retry_count)),
                        int(bool(fallback_used)),
                        attempted_endpoint_ids,
                        error_type[:80],
                        max(0, int(ttft_ms)) if ttft_ms is not None else None,
                        int(bool(streamed)),
                    ),
                )
                conn.commit()
                return int(cursor.lastrowid)
            finally:
                conn.close()

    def list_llm_call_records(self, limit: int = 50) -> list[dict[str, Any]]:
        """返回近期调用治理明细，供后续只读运营接口使用。"""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT id, chain_id, trace_id, project_id, logical_name,
                           actual_model, endpoint_id, route_reason,
                           input_tokens, output_tokens, total_tokens,
                           estimated_cost, status, duration_ms, retry_count, fallback_used,
                           attempted_endpoint_ids, error_type, ttft_ms, streamed, created_at
                    FROM llm_call_records
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (max(1, min(int(limit), 200)),),
                ).fetchall()
            finally:
                conn.close()
        return [dict(row) for row in rows]

    def delete_llm_call_records_for_test(self, chain_id: str) -> None:
        """清理指定测试调用链产生的明细，避免模拟调用污染本地运营数据。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM llm_call_records WHERE chain_id = ?", (chain_id,))
                conn.commit()
            finally:
                conn.close()

    def add_project_for_test(
        self,
        project_id: str,
        raw_key: str,
        daily_token_limit: int = 0,
    ) -> None:
        """创建未订阅测试项目，仅供本地集成验证使用。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO projects (project_id, daily_token_limit)
                    VALUES (?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET
                        daily_token_limit = excluded.daily_token_limit,
                        status = 'enabled',
                        updated_at = datetime('now')
                    """,
                    (project_id, daily_token_limit),
                )
                conn.execute(
                    """
                    INSERT INTO api_keys (project_id, key_hash, key_hint)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key_hash) DO UPDATE SET
                        project_id = excluded.project_id,
                        status = 'enabled'
                    """,
                    (project_id, self._hash_key(raw_key), self._key_hint(raw_key)),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_project_for_test(self, project_id: str) -> None:
        """清理本地集成测试项目及其 Key。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM api_keys WHERE project_id = ?", (project_id,))
                conn.execute("DELETE FROM project_subscriptions WHERE project_id = ?", (project_id,))
                conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
                conn.commit()
            finally:
                conn.close()

    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT p.project_id, p.status, p.daily_token_limit, k.key_hint
                    FROM projects p
                    LEFT JOIN api_keys k ON k.project_id = p.project_id
                    ORDER BY p.project_id
                    """
                ).fetchall()
            finally:
                conn.close()
        return [dict(row) for row in rows]

    def list_endpoints(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT endpoint_id, provider, model_id, base_url_env, api_key_env,
                           status, timeout_ms
                    FROM endpoints
                    ORDER BY endpoint_id
                    """
                ).fetchall()
            finally:
                conn.close()
        return [dict(row) for row in rows]

    def list_routes(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT r.logical_name, r.display_name, r.description,
                           r.primary_endpoint_id, r.fallback_endpoint_id,
                           r.input_token_price, r.output_token_price, r.retry_limit, r.status,
                           primary_endpoint.model_id AS primary_model_id,
                           fallback_endpoint.model_id AS fallback_model_id
                    FROM logical_routes r
                    JOIN endpoints primary_endpoint ON primary_endpoint.endpoint_id = r.primary_endpoint_id
                    LEFT JOIN endpoints fallback_endpoint ON fallback_endpoint.endpoint_id = r.fallback_endpoint_id
                    ORDER BY r.logical_name
                    """
                ).fetchall()
            finally:
                conn.close()
        return [dict(row) for row in rows]

    def list_subscriptions(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT s.project_id, s.logical_name, s.status, s.created_at,
                           r.display_name
                    FROM project_subscriptions s
                    JOIN logical_routes r ON r.logical_name = s.logical_name
                    ORDER BY s.project_id, s.logical_name
                    """
                ).fetchall()
            finally:
                conn.close()
        return [dict(row) for row in rows]

    def create_endpoint(
        self,
        *,
        endpoint_id: str,
        provider: str,
        model_id: str,
        base_url_env: str,
        api_key_env: str,
        timeout_ms: int,
        actor: str,
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO endpoints (
                        endpoint_id, provider, model_id, base_url_env, api_key_env, timeout_ms
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (endpoint_id, provider, model_id, base_url_env, api_key_env, max(1, int(timeout_ms))),
                )
                self._audit(conn, actor, "create", "endpoint", endpoint_id, "新增 Endpoint 配置")
                conn.commit()
            finally:
                conn.close()

    def update_endpoint(
        self,
        *,
        endpoint_id: str,
        status: str,
        timeout_ms: int,
        actor: str,
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    UPDATE endpoints
                    SET status = ?, timeout_ms = ?, updated_at = datetime('now')
                    WHERE endpoint_id = ?
                    """,
                    (status, max(1, int(timeout_ms)), endpoint_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Endpoint 不存在")
                self._audit(conn, actor, "update", "endpoint", endpoint_id, "更新启用状态或超时")
                conn.commit()
            finally:
                conn.close()

    def update_route(
        self,
        *,
        logical_name: str,
        primary_endpoint_id: str,
        fallback_endpoint_id: str | None,
        input_token_price: float,
        output_token_price: float,
        status: str,
        actor: str,
        retry_limit: int = 1,
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                endpoint_ids = [primary_endpoint_id]
                if fallback_endpoint_id:
                    endpoint_ids.append(fallback_endpoint_id)
                for endpoint_id in endpoint_ids:
                    exists = conn.execute(
                        "SELECT 1 FROM endpoints WHERE endpoint_id = ?", (endpoint_id,)
                    ).fetchone()
                    if not exists:
                        raise ValueError("路由引用的 Endpoint 不存在")
                cursor = conn.execute(
                    """
                    UPDATE logical_routes
                    SET primary_endpoint_id = ?, fallback_endpoint_id = ?,
                        input_token_price = ?, output_token_price = ?, retry_limit = ?, status = ?,
                        updated_at = datetime('now')
                    WHERE logical_name = ?
                    """,
                    (
                        primary_endpoint_id,
                        fallback_endpoint_id or None,
                        max(0.0, float(input_token_price)),
                        max(0.0, float(output_token_price)),
                        max(0, min(3, int(retry_limit))),
                        status,
                        logical_name,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("逻辑场景不存在")
                self._audit(conn, actor, "update", "route", logical_name, "更新 Endpoint 路由、价格或重试次数")
                conn.commit()
            finally:
                conn.close()

    def update_project(
        self,
        *,
        project_id: str,
        status: str,
        daily_token_limit: int,
        actor: str,
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    UPDATE projects
                    SET status = ?, daily_token_limit = ?, updated_at = datetime('now')
                    WHERE project_id = ?
                    """,
                    (status, max(0, int(daily_token_limit)), project_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError("项目不存在")
                self._audit(conn, actor, "update", "project", project_id, "更新项目状态或日配额")
                conn.commit()
            finally:
                conn.close()

    def set_subscription(
        self,
        *,
        project_id: str,
        logical_name: str,
        status: str,
        actor: str,
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO project_subscriptions (project_id, logical_name, status)
                    VALUES (?, ?, ?)
                    ON CONFLICT(project_id, logical_name) DO UPDATE SET status = excluded.status
                    """,
                    (project_id, logical_name, status),
                )
                self._audit(
                    conn,
                    actor,
                    "update",
                    "subscription",
                    f"{project_id}:{logical_name}",
                    "更新项目场景订阅状态",
                )
                conn.commit()
            finally:
                conn.close()

    def list_audit_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT actor, action, resource_type, resource_id, summary, created_at
                    FROM config_audit_logs
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (max(1, min(int(limit), 200)),),
                ).fetchall()
            finally:
                conn.close()
        return [dict(row) for row in rows]

    @staticmethod
    def _audit(
        conn: sqlite3.Connection,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        summary: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO config_audit_logs (actor, action, resource_type, resource_id, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (actor, action, resource_type, resource_id, summary),
        )
