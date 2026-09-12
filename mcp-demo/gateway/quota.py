"""
按项目 Token 配额计量和限流。

使用内存计数器跟踪每个项目每天的 Token 使用量。
超出配额时返回 429 Too Many Requests。

生产环境可改用 Redis 计数器以支持多实例部署。
"""

import datetime
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("gateway.quota")

SKIP_PATHS = {"/api/health", "/docs", "/openapi.json", "/redoc"}


class QuotaTracker:
    """内存级 Token 配额跟踪器，按项目 × 日期计量。"""

    def __init__(self, projects: dict):
        self._limits: dict[str, int] = {}
        for pid, cfg in projects.items():
            self._limits[pid] = cfg.get("quota", {}).get("daily_tokens", 0)
        self._usage: dict[str, dict[str, int]] = {}

    def check(self, project_id: str) -> bool:
        """检查项目是否还有剩余配额。limit<=0 表示不限制。"""
        limit = self._limits.get(project_id, 0)
        if limit <= 0:
            return True
        today = datetime.date.today().isoformat()
        used = self._usage.get(project_id, {}).get(today, 0)
        return used < limit

    def add(self, project_id: str, tokens: int):
        """记录 Token 消耗。"""
        if tokens <= 0:
            return
        today = datetime.date.today().isoformat()
        if project_id not in self._usage:
            self._usage[project_id] = {}
        self._usage[project_id][today] = self._usage[project_id].get(today, 0) + tokens
        logger.info("quota_add | project=%s tokens=+%d", project_id, tokens)

    def set_limit(self, project_id: str, daily_tokens: int) -> None:
        """控制面修改配额后同步当前网关实例，无需重启。"""
        self._limits[project_id] = max(0, int(daily_tokens))

    def get_usage(self, project_id: str) -> dict:
        """获取项目当天的配额使用情况。"""
        today = datetime.date.today().isoformat()
        limit = self._limits.get(project_id, 0)
        used = self._usage.get(project_id, {}).get(today, 0)
        return {
            "project_id": project_id,
            "date": today,
            "used_tokens": used,
            "daily_limit": limit,
            "remaining": max(0, limit - used) if limit > 0 else -1,
        }

    def get_all_usage(self) -> list[dict]:
        """返回全部已配置项目的当天用量，供本地服务台只读展示。"""
        return [self.get_usage(project_id) for project_id in self._limits]


class QuotaMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, tracker: QuotaTracker):
        super().__init__(app)
        self._tracker = tracker

    async def dispatch(self, request: Request, call_next):
        if request.url.path in SKIP_PATHS:
            return await call_next(request)

        project_id = getattr(request.state, "project_id", None)
        if project_id and project_id != "__anonymous__" and not self._tracker.check(project_id):
            usage = self._tracker.get_usage(project_id)
            trace_id = getattr(request.state, "trace_id", "?")
            logger.warning("[%s] 配额已用尽: project=%s", trace_id, project_id)
            return JSONResponse(
                status_code=429,
                content={"error": "Token 配额已用尽", **usage},
            )

        return await call_next(request)
