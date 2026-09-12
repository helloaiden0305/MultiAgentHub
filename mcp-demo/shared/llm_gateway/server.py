"""
LLM 网关 MCP Server — 统一的大模型调用入口。

暴露 Tool：
  - chat_completion : 对话补全（自动路由到配置的模型）
  - embedding       : 文本向量化
  - list_models     : 列出可用模型

启动：uv run shared/llm_gateway/server.py
端口：9001
传输：Streamable HTTP → http://127.0.0.1:9001/mcp
"""

import hmac
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Mount, Route

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from doubao import OpenAICompatibleClient, UpstreamCallError
from endpoint_router import EndpointResolutionError, EndpointRouter
from fault_injection import LocalFaultInjector, VALID_FAULT_MODES
from resilience import EndpointHealthTracker
from router import ModelRouter
from shared.control_plane import ControlPlaneStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("llm-gateway")

# ── 初始化 ──────────────────────────────────────────────

server = FastMCP("llm-gateway", host="127.0.0.1", port=9001)

default_client = OpenAICompatibleClient(
    api_key=os.environ["ARK_API_KEY"],
    base_url=os.environ["ARK_BASE_URL"],
)

router = ModelRouter()
control_plane = ControlPlaneStore(PROJECT_ROOT / "data" / "sqlite" / "control_plane.db")
endpoint_router = EndpointRouter(control_plane)
endpoint_health = EndpointHealthTracker()
fault_injector = LocalFaultInjector()

CHAT_ENDPOINT = os.environ["ARK_CHAT_MODEL"]
EMBEDDING_ENDPOINT = os.environ["ARK_EMBEDDING_MODEL"]


def _resolve_routes(model: str) -> tuple[list[dict], dict]:
    """解析逻辑场景的主备 Endpoint，兼容旧的静态模型调用。"""
    if model == "report-generation":
        try:
            routes = endpoint_router.resolve_candidates(model)
        except EndpointResolutionError as error:
            raise RuntimeError(f"逻辑场景路由失败: {error}") from error
        model_cfg = router.get_model_config(router.resolve("auto"))
        return routes, model_cfg

    model_name = router.resolve(model)
    return [{
        "endpoint_id": "legacy-ark-chat",
        "actual_model": CHAT_ENDPOINT,
        "route_reason": "legacy_static_config",
        "client": default_client,
        "retry_limit": 1,
    }], router.get_model_config(model_name)


async def stream_chat_completion_events(
    *,
    messages: list[dict],
    project_id: str,
    model: str,
    temperature: float,
    max_tokens: int,
):
    """将上游流式内容转为内部 SSE 事件，首个 delta 记录真实 TTFT。"""
    routes, model_cfg = _resolve_routes(model)
    effective_max_tokens = min(max_tokens, model_cfg.get("max_tokens", max_tokens))
    started = time.monotonic()
    retry_count = 0
    retry_limit = int(routes[0].get("retry_limit", 1))
    attempted_endpoint_ids: list[str] = []
    last_error: UpstreamCallError | None = None
    sequence = 0

    def emit(event: str, **data: object) -> tuple[str, dict]:
        nonlocal sequence
        sequence += 1
        return event, {
            "sequence": sequence,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            **data,
        }

    for index, route in enumerate(routes):
        endpoint_id = route["endpoint_id"]
        if model == "report-generation" and not endpoint_health.can_attempt(endpoint_id):
            yield emit(
                "circuit_open",
                endpoint_id=endpoint_id,
                reason="连续可重试失败，当前 Endpoint 暂不接收流量",
            )
            if index + 1 < len(routes):
                yield emit("fallback", from_endpoint_id=endpoint_id, to_endpoint_id=routes[index + 1]["endpoint_id"])
            continue
        attempted_endpoint_ids.append(endpoint_id)
        endpoint_client = route.get("client") or OpenAICompatibleClient(
            route["api_key"], route["base_url"], int(route["timeout_ms"])
        )
        emitted_delta = False
        ttft_ms: int | None = None
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        try:
            for attempt in range(retry_limit + 1):
                try:
                    yield emit(
                        "attempt",
                        endpoint_id=endpoint_id,
                        actual_model=route["actual_model"],
                        attempt=attempt + 1,
                        max_attempts=retry_limit + 1,
                        fallback_used=index > 0,
                    )
                    if fault_injector.consume(endpoint_id, "connection_failure"):
                        yield emit(
                            "fault_injected",
                            endpoint_id=endpoint_id,
                            mode="connection_failure",
                            message="本地验证：在上游调用前模拟连接失败",
                        )
                        raise UpstreamCallError(
                            "本地验证模拟连接失败",
                            retryable=True,
                            error_type="fault_injected_connection",
                        )
                    async for item in endpoint_client.stream_chat_completion(
                        model=route["actual_model"],
                        messages=messages,
                        temperature=temperature,
                        max_tokens=effective_max_tokens,
                    ):
                        if item["type"] == "usage":
                            usage = item["usage"]
                            continue
                        if not emitted_delta:
                            emitted_delta = True
                            ttft_ms = round((time.monotonic() - started) * 1000)
                            yield emit(
                                "meta",
                                model=model,
                                actual_model=route["actual_model"],
                                endpoint_id=endpoint_id,
                                route_reason=route["route_reason"],
                                project_id=project_id,
                                ttft_ms=ttft_ms,
                                retry_count=retry_count,
                                fallback_used=index > 0,
                                attempted_endpoint_ids=attempted_endpoint_ids,
                            )
                        yield emit("delta", content=item["content"])
                        if fault_injector.consume(endpoint_id, "abort_after_first_delta"):
                            if model == "report-generation":
                                endpoint_health.record_failure(endpoint_id, "fault_injected_stream_abort")
                            yield emit(
                                "fault_injected",
                                endpoint_id=endpoint_id,
                                mode="abort_after_first_delta",
                                message="本地验证：首段返回后模拟上游流中断",
                            )
                            yield emit(
                                "error",
                                error="流式响应中断，已返回的内容已保留",
                                error_type="fault_injected_stream_abort",
                                retry_count=retry_count,
                                attempted_endpoint_ids=attempted_endpoint_ids,
                                duration_ms=round((time.monotonic() - started) * 1000),
                            )
                            return
                    if model == "report-generation":
                        endpoint_health.record_success(endpoint_id)
                    yield emit(
                        "done",
                        model=model,
                        actual_model=route["actual_model"],
                        endpoint_id=endpoint_id,
                        route_reason=route["route_reason"],
                        project_id=project_id,
                        usage=usage,
                        ttft_ms=ttft_ms if emitted_delta else round((time.monotonic() - started) * 1000),
                        duration_ms=round((time.monotonic() - started) * 1000),
                        retry_count=retry_count,
                        fallback_used=index > 0,
                        attempted_endpoint_ids=attempted_endpoint_ids,
                    )
                    return
                except UpstreamCallError as error:
                    last_error = error
                    if model == "report-generation":
                        endpoint_health.record_failure(endpoint_id, error.error_type)
                    if emitted_delta or not error.retryable or attempt >= retry_limit:
                        break
                    retry_count += 1
                    yield emit(
                        "retry",
                        endpoint_id=endpoint_id,
                        next_attempt=attempt + 2,
                        error_type=error.error_type,
                        retry_count=retry_count,
                    )
            if emitted_delta:
                yield emit(
                    "error",
                    error="流式响应中断，已返回的内容已保留",
                    error_type=last_error.error_type if last_error else "upstream_error",
                    retry_count=retry_count,
                    attempted_endpoint_ids=attempted_endpoint_ids,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
                return
            if index + 1 < len(routes):
                yield emit("fallback", from_endpoint_id=endpoint_id, to_endpoint_id=routes[index + 1]["endpoint_id"])
        finally:
            if endpoint_client is not default_client:
                await endpoint_client.close()

    yield emit(
        "error",
        error="模型服务暂不可用",
        error_type=last_error.error_type if last_error else "all_endpoints_unavailable",
        retry_count=retry_count,
        attempted_endpoint_ids=attempted_endpoint_ids,
        duration_ms=round((time.monotonic() - started) * 1000),
    )


# ── Tools ───────────────────────────────────────────────

@server.tool()
async def chat_completion(
    messages: list[dict],
    project_id: str,
    model: str = "auto",
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> dict:
    """统一的 LLM 对话接口，自动路由到配置的模型。

    Args:
        messages: 对话消息列表，格式 [{"role": "user", "content": "..."}]
        project_id: 调用方项目标识（用于日志和配额追踪）
        model: 模型选择 — "auto" 使用默认模型，或指定 "doubao-pro" / "doubao-lite"
        temperature: 生成温度，0-1 之间
        max_tokens: 最大生成 token 数
    """
    retry_count = 0
    attempted_endpoint_ids: list[str] = []
    fallback_used = False
    if model == "report-generation":
        try:
            routes = endpoint_router.resolve_candidates(model)
        except EndpointResolutionError as error:
            raise RuntimeError(f"逻辑场景路由失败: {error}") from error
        model_name = router.resolve("auto")
        model_cfg = router.get_model_config(model_name)
    else:
        model_name = router.resolve(model)
        model_cfg = router.get_model_config(model_name)
        routes = [{
            "endpoint_id": "legacy-ark-chat",
            "actual_model": CHAT_ENDPOINT,
            "route_reason": "legacy_static_config",
            "client": default_client,
            "retry_limit": 1,
        }]

    effective_max_tokens = min(max_tokens, model_cfg.get("max_tokens", max_tokens))
    retry_limit = int(routes[0].get("retry_limit", 1))

    logger.info(
        "chat_completion | project=%s logical_model=%s endpoint=%s max_tokens=%d",
        project_id, model, routes[0]["endpoint_id"], effective_max_tokens,
    )

    result = None
    final_route = None
    last_error: UpstreamCallError | None = None
    for index, route in enumerate(routes):
        endpoint_id = route["endpoint_id"]
        if model == "report-generation" and not endpoint_health.can_attempt(endpoint_id):
            logger.warning("endpoint circuit open | endpoint=%s", endpoint_id)
            continue
        attempted_endpoint_ids.append(endpoint_id)
        endpoint_client = route.get("client") or OpenAICompatibleClient(
            route["api_key"], route["base_url"], int(route["timeout_ms"])
        )
        try:
            for attempt in range(retry_limit + 1):
                try:
                    result = await endpoint_client.chat_completion(
                        model=route["actual_model"],
                        messages=messages,
                        temperature=temperature,
                        max_tokens=effective_max_tokens,
                    )
                    final_route = route
                    if model == "report-generation":
                        endpoint_health.record_success(endpoint_id)
                    break
                except UpstreamCallError as error:
                    last_error = error
                    if model == "report-generation":
                        endpoint_health.record_failure(endpoint_id, error.error_type)
                    if not error.retryable or attempt >= retry_limit:
                        break
                    retry_count += 1
                    logger.info("retrying endpoint | endpoint=%s attempt=%d", endpoint_id, attempt + 1)
            if result is not None:
                fallback_used = index > 0
                break
        finally:
            if endpoint_client is not default_client:
                await endpoint_client.close()

    if result is None or final_route is None:
        detail = last_error.error_type if last_error else "all_endpoints_unavailable"
        raise RuntimeError(f"模型服务暂不可用: {detail}")

    result["model"] = model
    result["actual_model"] = final_route["actual_model"]
    result["endpoint_id"] = final_route["endpoint_id"]
    result["route_reason"] = final_route["route_reason"]
    result["project_id"] = project_id
    result["retry_count"] = retry_count
    result["fallback_used"] = fallback_used
    result["attempted_endpoint_ids"] = attempted_endpoint_ids
    return result


@server.tool()
async def embedding(
    texts: list[str],
    project_id: str,
) -> dict:
    """统一的文本向量化接口。

    Args:
        texts: 要向量化的文本列表
        project_id: 调用方项目标识
    """
    logger.info("embedding | project=%s texts=%d", project_id, len(texts))

    result = await default_client.embedding(
        model=EMBEDDING_ENDPOINT,
        texts=texts,
    )
    result["project_id"] = project_id
    return result


@server.tool()
async def list_models() -> dict:
    """列出所有可用的 LLM 模型及其配置信息。"""
    return {"models": router.list_models()}


@server.tool()
async def get_endpoint_runtime_status() -> dict:
    """返回 Endpoint 运行时熔断状态，仅供内部服务台读取。"""
    endpoint_ids = [item["endpoint_id"] for item in control_plane.list_endpoints()]
    return {
        "endpoints": endpoint_health.snapshot(endpoint_ids),
        "notice": "演示版状态保存在 LLM Service 内存；真实场景中由 Redis 或健康检查组件共享。",
    }


def _is_internal_stream_request(request: Request) -> bool:
    """流式端点仅接受网关的服务间凭证，不能被浏览器直接调用。"""
    expected = (
        os.getenv("LLM_INTERNAL_STREAM_KEY", "").strip()
        or os.getenv("GATEWAY_ADMIN_API_KEY", "").strip()
    )
    provided = request.headers.get("X-Internal-Stream-Key", "")
    return bool(expected) and hmac.compare_digest(provided, expected)


async def stream_chat_completion_http(request: Request):
    """内部 SSE 接口：只承接已认证 HTTP 网关发起的模型请求。"""
    if not _is_internal_stream_request(request):
        return JSONResponse({"error": "内部流式服务认证失败"}, status_code=401)
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "请求体必须是 JSON"}, status_code=400)

    messages = payload.get("messages")
    project_id = str(payload.get("project_id", "")).strip()
    model = str(payload.get("model", "")).strip()
    if not isinstance(messages, list) or not project_id or not model:
        return JSONResponse({"error": "messages、project_id、model 为必填字段"}, status_code=422)

    async def event_stream():
        try:
            async for event, data in stream_chat_completion_events(
                messages=messages,
                project_id=project_id,
                model=model,
                temperature=float(payload.get("temperature", 0.7)),
                max_tokens=max(1, int(payload.get("max_tokens", 2000))),
            ):
                yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
        except Exception:
            logger.exception("stream request failed")
            yield "event: error\ndata: {\"error\":\"模型流式服务异常\"}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def fault_injection_http(request: Request):
    """服务间受保护的本地故障验证接口，不对浏览器暴露。"""
    if not _is_internal_stream_request(request):
        return JSONResponse({"error": "内部流式服务认证失败"}, status_code=401)
    if request.method == "GET":
        return JSONResponse({"rules": fault_injector.snapshot(), "scope": "llm_service_local_memory"})
    if request.method == "DELETE":
        fault_injector.clear()
        return JSONResponse({"ok": True})
    try:
        payload = await request.json()
        endpoint_id = str(payload.get("endpoint_id", "")).strip()
        mode = str(payload.get("mode", "")).strip()
        times = int(payload.get("times", 1))
        if mode not in VALID_FAULT_MODES:
            raise ValueError("不支持的故障类型")
        rule = fault_injector.configure(endpoint_id, mode, times)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    return JSONResponse({"ok": True, "rule": rule, "scope": "llm_service_local_memory"})


# MCP 仍使用 /mcp；专用 SSE 端点只接受内部网关的服务间认证。
mcp_http_app = server.streamable_http_app()


@asynccontextmanager
async def app_lifespan(_: Starlette):
    async with mcp_http_app.router.lifespan_context(mcp_http_app):
        yield


app = Starlette(routes=[
    Route("/api/chat/stream", stream_chat_completion_http, methods=["POST"]),
    Route("/api/internal/fault-injection", fault_injection_http, methods=["GET", "POST", "DELETE"]),
    Mount("/", app=mcp_http_app),
], lifespan=app_lifespan)


# ── 入口 ────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("LLM 网关 MCP Server 启动中...")
    logger.info("  Chat Endpoint:      %s", CHAT_ENDPOINT)
    logger.info("  Embedding Endpoint: %s", EMBEDDING_ENDPOINT)
    logger.info("  默认模型:           %s", router.default_model)
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=9001)
