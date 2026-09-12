"""
统一日志中间件 — 为每个请求分配 Trace ID，记录请求/响应摘要和耗时。

Trace ID 与可选 Chain ID 通过 request.state 传递给下游中间件和路由，
同时写入响应头供调用方关联日志。
"""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("gateway.access")


class LoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = uuid.uuid4().hex[:12]
        chain_id = request.headers.get("X-Chain-ID", "").strip()
        request.state.trace_id = trace_id
        request.state.chain_id = chain_id[:64]

        start = time.monotonic()
        logger.info(
            "trace_id=%s chain_id=%s request=%s %s",
            trace_id, request.state.chain_id or "-", request.method, request.url.path,
        )

        response = await call_next(request)

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "trace_id=%s chain_id=%s status=%d duration_ms=%.0f",
            trace_id, request.state.chain_id or "-", response.status_code, elapsed_ms,
        )

        response.headers["X-Trace-ID"] = trace_id
        if request.state.chain_id:
            response.headers["X-Chain-ID"] = request.state.chain_id
        return response
