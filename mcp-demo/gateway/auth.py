"""
项目级 API Key 认证中间件。

从请求头 X-API-Key 读取密钥，匹配 SQLite 控制面中的项目 Key 哈希。
认证成功后将 project_id 写入 request.state.project_id 供下游使用。
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("gateway.auth")

SKIP_PATHS = {"/api/health", "/docs", "/openapi.json", "/redoc"}


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, control_plane):
        super().__init__(app)
        self._control_plane = control_plane

    async def dispatch(self, request: Request, call_next):
        if request.url.path in SKIP_PATHS:
            request.state.project_id = "__anonymous__"
            return await call_next(request)

        api_key = request.headers.get("X-API-Key", "")
        project = self._control_plane.resolve_project_by_api_key(api_key)
        project_id = project["project_id"] if project else None

        if not project_id or project["status"] != "enabled" or project["key_status"] != "enabled":
            trace_id = getattr(request.state, "trace_id", "?")
            logger.warning("[%s] 认证失败: 无效的 API Key", trace_id)
            return JSONResponse(
                status_code=401,
                content={"error": "无效的 API Key", "hint": "请在请求头中设置 X-API-Key"},
            )

        request.state.project_id = project_id
        logger.info(
            "[%s] 认证通过: project=%s",
            getattr(request.state, "trace_id", "?"),
            project_id,
        )
        return await call_next(request)
