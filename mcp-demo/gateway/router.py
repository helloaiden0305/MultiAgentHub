"""
HTTP 路由 — 将 REST 请求转发到 MCP 共享服务。

端点：
  POST /api/tool/call     — 调用 MCP Tool
  POST /api/prompt/get    — 获取渲染后的 MCP Prompt
  GET  /api/prompt/list   — 列出可用 Prompt
  GET  /api/tool/list     — 列出可用 Tool
  GET  /api/health        — 健康检查（无需认证）
  GET  /api/quota/usage   — 查看当前项目的配额使用情况
"""

import json
import logging
import os
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .mcp_client_manager import MCPCallError

logger = logging.getLogger("gateway.router")

router = APIRouter(prefix="/api")


# ── 请求体模型 ──────────────────────────────────────────


class ToolCallRequest(BaseModel):
    service: str
    tool: str
    arguments: dict = {}


class PromptGetRequest(BaseModel):
    service: str
    prompt: str
    arguments: dict = {}


class StreamChatRequest(BaseModel):
    messages: list[dict]
    model: str
    temperature: float = 0.7
    max_tokens: int = 2000


# ── Tool 端点 ───────────────────────────────────────────


@router.post("/tool/call")
async def call_tool(request: Request, body: ToolCallRequest):
    """调用指定 MCP 服务的 Tool。"""
    trace_id = getattr(request.state, "trace_id", "?")
    chain_id = getattr(request.state, "chain_id", "-") or "-"
    project_id = getattr(request.state, "project_id", "?")
    mcp = request.app.state.mcp
    arguments = dict(body.arguments)
    logical_name: str | None = None

    started = time.monotonic()

    if body.service == "llm-gateway" and body.tool == "chat_completion":
        logical_name = str(arguments.get("model", "")).strip()
        if not logical_name:
            return JSONResponse(status_code=400, content={
                "error": "模型调用必须提供逻辑模型或场景名称",
                "trace_id": trace_id,
            })
        if not request.app.state.control_plane.is_subscribed(project_id, logical_name):
            _record_llm_call(
                request,
                chain_id,
                trace_id,
                project_id,
                logical_name,
                "failed",
                started,
                route_reason="subscription_denied",
            )
            _log_service_call(
                request,
                chain_id,
                trace_id,
                project_id,
                body.service,
                f"subscription:{logical_name}",
                "failed",
                started,
            )
            logger.warning(
                "trace_id=%s project=%s subscription_denied=%s",
                trace_id,
                project_id,
                logical_name,
            )
            return JSONResponse(status_code=403, content={
                "error": "当前项目未订阅该模型或场景",
                "project_id": project_id,
                "logical_name": logical_name,
                "trace_id": trace_id,
            })

        # LLM Service 依据已验证的逻辑场景解析实际 Endpoint。
        arguments["project_id"] = project_id

    try:
        result = await mcp.call_tool(body.service, body.tool, arguments)
    except MCPCallError as e:
        if logical_name:
            _record_llm_call(
                request,
                chain_id,
                trace_id,
                project_id,
                logical_name,
                "failed",
                started,
                route_reason="upstream_call_failed",
            )
        _log_service_call(request, chain_id, trace_id, project_id, body.service, body.tool, "failed", started)
        return JSONResponse(status_code=502, content={"error": str(e), "trace_id": trace_id})
    except BaseException as e:
        real = e.exceptions[0] if hasattr(e, "exceptions") else e
        if logical_name:
            _record_llm_call(
                request,
                chain_id,
                trace_id,
                project_id,
                logical_name,
                "failed",
                started,
                route_reason="upstream_call_failed",
            )
        _log_service_call(request, chain_id, trace_id, project_id, body.service, body.tool, "failed", started)
        logger.error("trace_id=%s chain_id=%s MCP 调用异常: %s", trace_id, chain_id, real)
        return JSONResponse(status_code=502, content={
            "error": f"MCP 服务调用失败: {real}",
            "hint": f"请确认 {body.service} 服务已启动",
            "trace_id": trace_id,
        })

    if body.tool == "chat_completion" and isinstance(result, dict) and "usage" in result:
        total_tokens = result["usage"].get("total_tokens", 0)
        if total_tokens > 0:
            request.app.state.quota.add(project_id, total_tokens)
        if logical_name:
            _record_llm_call(
                request,
                chain_id,
                trace_id,
                project_id,
                logical_name,
                "completed",
                started,
                result=result,
            )

    _log_service_call(request, chain_id, trace_id, project_id, body.service, body.tool, "completed", started)
    return result


def _record_llm_call(
    request: Request,
    chain_id: str,
    trace_id: str,
    project_id: str,
    logical_name: str,
    status: str,
    started: float,
    *,
    result: dict | None = None,
    route_reason: str = "",
) -> None:
    """写入可运营字段；输入和输出正文始终留在调用链之外。"""
    control_plane = request.app.state.control_plane
    route = control_plane.get_route(logical_name) or {}
    usage = result.get("usage", {}) if result else {}
    input_tokens = int(usage.get("prompt_tokens", 0) or 0)
    output_tokens = int(usage.get("completion_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
    if not total_tokens:
        total_tokens = input_tokens + output_tokens

    input_price = float(route.get("input_token_price", 0) or 0)
    output_price = float(route.get("output_token_price", 0) or 0)
    estimated_cost = (
        input_tokens * input_price + output_tokens * output_price
    ) / 1_000_000

    control_plane.record_llm_call(
        chain_id=chain_id,
        trace_id=trace_id,
        project_id=project_id,
        logical_name=logical_name,
        actual_model=str((result or {}).get("actual_model", route.get("model_id", ""))),
        endpoint_id=str((result or {}).get("endpoint_id", route.get("primary_endpoint_id", ""))),
        route_reason=str((result or {}).get("route_reason", route_reason)),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated_cost=estimated_cost,
        status=status,
        duration_ms=round((time.monotonic() - started) * 1000),
        retry_count=int((result or {}).get("retry_count", 0) or 0),
        fallback_used=bool((result or {}).get("fallback_used", False)),
        attempted_endpoint_ids=",".join((result or {}).get("attempted_endpoint_ids", [])),
        error_type=str((result or {}).get("error_type", "") or ""),
        ttft_ms=(result or {}).get("ttft_ms"),
        streamed=bool((result or {}).get("streamed", False)),
    )


@router.post("/llm/stream")
async def stream_llm_completion(request: Request, body: StreamChatRequest):
    """经网关鉴权、订阅校验后代理 LLM Service 的内部 SSE 响应。"""
    trace_id = getattr(request.state, "trace_id", "?")
    chain_id = getattr(request.state, "chain_id", "-") or "-"
    project_id = getattr(request.state, "project_id", "?")
    logical_name = body.model.strip()
    started = time.monotonic()
    if not logical_name:
        return JSONResponse(status_code=400, content={"error": "必须提供逻辑模型或场景名称"})
    if not request.app.state.control_plane.is_subscribed(project_id, logical_name):
        _record_llm_call(
            request, chain_id, trace_id, project_id, logical_name, "failed", started,
            route_reason="subscription_denied",
        )
        return JSONResponse(status_code=403, content={
            "error": "当前项目未订阅该模型或场景",
            "project_id": project_id,
            "logical_name": logical_name,
            "trace_id": trace_id,
        })

    service_url = request.app.state.config["services"]["llm-gateway"]["url"]
    stream_url = service_url.rsplit("/mcp", 1)[0] + "/api/chat/stream"
    internal_key = (
        os.getenv("LLM_INTERNAL_STREAM_KEY", "").strip()
        or os.getenv("GATEWAY_ADMIN_API_KEY", "").strip()
    )
    if not internal_key:
        return JSONResponse(status_code=503, content={"error": "未配置内部流式服务凭证"})

    request.app.state.stream_runs.start(chain_id, trace_id, project_id, logical_name)

    async def event_stream():
        recorded = False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=8.0)) as client:
                async with client.stream(
                    "POST",
                    stream_url,
                    headers={"X-Internal-Stream-Key": internal_key},
                    json={
                        "messages": body.messages,
                        "project_id": project_id,
                        "model": logical_name,
                        "temperature": body.temperature,
                        "max_tokens": body.max_tokens,
                    },
                ) as upstream:
                    if upstream.status_code != 200:
                        _record_llm_call(
                            request, chain_id, trace_id, project_id, logical_name, "failed", started,
                            route_reason="stream_upstream_rejected",
                        )
                        recorded = True
                        request.app.state.stream_runs.finish(chain_id, "failed")
                        yield "event: error\ndata: {\"error\":\"LLM 流式服务暂不可用\"}\n\n"
                        return
                    current_event = "message"
                    async for line in upstream.aiter_lines():
                        if line.startswith("event:"):
                            current_event = line[6:].strip()
                            continue
                        if not line.startswith("data:"):
                            continue
                        raw_data = line[5:].strip()
                        try:
                            data = json.loads(raw_data)
                        except json.JSONDecodeError:
                            continue
                        data["chain_id"] = chain_id
                        request.app.state.stream_runs.record(chain_id, current_event, data)
                        if current_event == "done":
                            data["streamed"] = True
                            total_tokens = int(data.get("usage", {}).get("total_tokens", 0) or 0)
                            if total_tokens > 0:
                                request.app.state.quota.add(project_id, total_tokens)
                            _record_llm_call(
                                request, chain_id, trace_id, project_id, logical_name, "completed", started,
                                result=data,
                            )
                            _log_service_call(
                                request, chain_id, trace_id, project_id, "llm-gateway", "chat_completion_stream",
                                "completed", started,
                            )
                            recorded = True
                            request.app.state.stream_runs.finish(chain_id, "completed")
                        elif current_event == "error":
                            _record_llm_call(
                                request, chain_id, trace_id, project_id, logical_name, "failed", started,
                                result=data,
                                route_reason="stream_upstream_failed",
                            )
                            _log_service_call(
                                request, chain_id, trace_id, project_id, "llm-gateway", "chat_completion_stream",
                                "failed", started,
                            )
                            recorded = True
                            request.app.state.stream_runs.finish(chain_id, "failed")
                        yield f"event: {current_event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
                        current_event = "message"
        except httpx.HTTPError:
            if not recorded:
                _record_llm_call(
                    request, chain_id, trace_id, project_id, logical_name, "failed", started,
                    route_reason="stream_connection_failed",
                )
                _log_service_call(
                    request, chain_id, trace_id, project_id, "llm-gateway", "chat_completion_stream",
                    "failed", started,
                )
                request.app.state.stream_runs.finish(chain_id, "failed")
                recorded = True
            yield "event: error\ndata: {\"error\":\"LLM 流式连接失败\"}\n\n"
        finally:
            if not recorded:
                _record_llm_call(
                    request, chain_id, trace_id, project_id, logical_name, "failed", started,
                    route_reason="stream_interrupted",
                )
                request.app.state.stream_runs.finish(chain_id, "failed")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Trace-ID": trace_id},
    )


@router.post("/tool/list")
async def list_tools_for_service(request: Request, body: dict):
    """列出指定 MCP 服务的所有 Tool。"""
    service = body.get("service", "")
    mcp = request.app.state.mcp
    trace_id = getattr(request.state, "trace_id", "?")

    try:
        return await mcp.list_tools(service)
    except MCPCallError as e:
        return JSONResponse(status_code=502, content={"error": str(e), "trace_id": trace_id})
    except Exception as e:
        return JSONResponse(status_code=502, content={
            "error": f"MCP 服务调用失败: {e}",
            "hint": f"请确认 {service} 服务已启动",
            "trace_id": trace_id,
        })


# ── Prompt 端点 ─────────────────────────────────────────


@router.post("/prompt/get")
async def get_prompt(request: Request, body: PromptGetRequest):
    """获取渲染后的 MCP Prompt（填入参数后的完整提示词）。"""
    trace_id = getattr(request.state, "trace_id", "?")
    chain_id = getattr(request.state, "chain_id", "-") or "-"
    mcp = request.app.state.mcp
    project_id = getattr(request.state, "project_id", "?")
    started = time.monotonic()

    try:
        result = await mcp.get_prompt(body.service, body.prompt, body.arguments)
    except MCPCallError as e:
        _log_service_call(request, chain_id, trace_id, project_id, body.service, body.prompt, "failed", started)
        return JSONResponse(status_code=502, content={"error": str(e), "trace_id": trace_id})
    except Exception as e:
        _log_service_call(request, chain_id, trace_id, project_id, body.service, body.prompt, "failed", started)
        logger.exception("trace_id=%s chain_id=%s MCP 调用异常", trace_id, chain_id)
        return JSONResponse(status_code=502, content={
            "error": f"MCP 服务调用失败: {e}",
            "hint": f"请确认 {body.service} 服务已启动",
            "trace_id": trace_id,
        })

    _log_service_call(request, chain_id, trace_id, project_id, body.service, body.prompt, "completed", started)
    return result


def _log_service_call(
    request: Request,
    chain_id: str,
    trace_id: str,
    project_id: str,
    service: str,
    operation: str,
    status: str,
    started: float,
) -> None:
    duration_ms = round((time.monotonic() - started) * 1000)
    logger.info(
        "chain_id=%s trace_id=%s project=%s service=%s operation=%s status=%s duration_ms=%d",
        chain_id, trace_id, project_id, service, operation, status, duration_ms,
    )
    request.app.state.events.record(
        chain_id=chain_id,
        trace_id=trace_id,
        project_id=project_id,
        service=service,
        operation=operation,
        status=status,
        duration_ms=duration_ms,
    )


@router.get("/prompt/list")
async def list_prompts(request: Request, service: str = "prompt-hub"):
    """列出指定服务的所有可用 Prompt。"""
    mcp = request.app.state.mcp
    trace_id = getattr(request.state, "trace_id", "?")

    try:
        return await mcp.list_prompts(service)
    except MCPCallError as e:
        return JSONResponse(status_code=502, content={"error": str(e), "trace_id": trace_id})
    except Exception as e:
        return JSONResponse(status_code=502, content={
            "error": f"MCP 服务调用失败: {e}",
            "hint": f"请确认 {service} 服务已启动",
            "trace_id": trace_id,
        })


# ── 治理端点 ────────────────────────────────────────────


@router.get("/health")
async def health(request: Request):
    """健康检查 + 各服务连通性检测。"""
    mcp = request.app.state.mcp
    services = {}
    for name in mcp.service_names:
        services[name] = await mcp.check_health(name)
    all_ok = all(s["status"] == "ok" for s in services.values())
    return {"status": "ok" if all_ok else "degraded", "services": services}


@router.get("/quota/usage")
async def quota_usage(request: Request):
    """查看当前项目的 Token 配额使用情况。"""
    project_id = getattr(request.state, "project_id", None)
    if not project_id or project_id == "__anonymous__":
        return JSONResponse(status_code=401, content={"error": "需要认证"})
    return request.app.state.quota.get_usage(project_id)
