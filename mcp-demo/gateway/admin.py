"""内部 MCP 服务网关的服务台管理接口。"""

import hmac
import os

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from shared.llm_gateway.router import ModelRouter

router = APIRouter(prefix="/api/admin", tags=["gateway-console"])

VALID_STATUSES = {"enabled", "disabled"}


class EndpointCreateRequest(BaseModel):
    endpoint_id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    provider: str = Field(default="ark", min_length=2, max_length=40)
    model_id: str = Field(min_length=1, max_length=160)
    base_url_env: str = Field(min_length=3, max_length=80, pattern=r"^[A-Z][A-Z0-9_]*$")
    api_key_env: str = Field(min_length=3, max_length=80, pattern=r"^[A-Z][A-Z0-9_]*$")
    timeout_ms: int = Field(default=60000, ge=1, le=300000)


class EndpointUpdateRequest(BaseModel):
    status: str
    timeout_ms: int = Field(ge=1, le=300000)


class RouteUpdateRequest(BaseModel):
    primary_endpoint_id: str = Field(min_length=3, max_length=80)
    fallback_endpoint_id: str | None = Field(default=None, max_length=80)
    input_token_price: float = Field(default=0, ge=0)
    output_token_price: float = Field(default=0, ge=0)
    retry_limit: int = Field(default=1, ge=0, le=3)
    status: str


class ProjectUpdateRequest(BaseModel):
    status: str
    daily_token_limit: int = Field(ge=0, le=10_000_000)


class SubscriptionUpdateRequest(BaseModel):
    status: str


class FaultInjectionRequest(BaseModel):
    endpoint_id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    mode: str = Field(pattern=r"^(connection_failure|abort_after_first_delta)$")
    times: int = Field(default=1, ge=1, le=5)

SERVICE_LABELS = {
    "llm-gateway": "LLM Service",
    "memory-service": "Memory Service",
    "prompt-hub": "Prompt Hub",
}

SERVICE_DESCRIPTIONS = {
    "llm-gateway": "统一提供模型调用、逻辑模型选择与 Token 使用量返回",
    "memory-service": "统一管理会话记忆与工程上下文",
    "prompt-hub": "统一管理并渲染可复用 Prompt 模板",
}

CAPABILITIES = [
    {
        "name": "统一接入",
        "status": "implemented",
        "description": "内部 MCP 服务网关集中转发共享服务调用。",
    },
    {
        "name": "API Key 身份识别",
        "status": "implemented",
        "description": "API Key 映射项目身份，用于配额、日志和会话隔离。",
    },
    {
        "name": "Token 日配额",
        "status": "implemented",
        "description": "项目维度日 Token 配额；当前演示版使用内存计数，重启后清空。",
    },
    {
        "name": "调用链可观测",
        "status": "implemented",
        "description": "使用 chain_id 串联服务调用，并保留 trace_id、状态和耗时。",
    },
    {
        "name": "MCP 服务注册与健康检查",
        "status": "implemented",
        "description": "服务通过配置注册，并支持实时连通性检查。",
    },
    {
        "name": "逻辑模型名",
        "status": "implemented",
        "description": "业务场景 report-generation 由持久化配置解析为实际模型与 Endpoint。",
    },
    {
        "name": "多模型 / 多 Endpoint 路由",
        "status": "implemented",
        "description": "支持 Endpoint 资源、主备路由、受控重试与自动回退，并保留实际路由结果。",
    },
    {
        "name": "模型 / 场景订阅权限",
        "status": "implemented",
        "description": "网关在上游调用前校验项目订阅，未订阅请求返回 403。",
    },
    {
        "name": "费用与运营报表",
        "status": "implemented",
        "description": "提供 SQLite 调用明细、Token、估算成本与基础运营页面；不作为正式账单。",
    },
    {
        "name": "重试、熔断、Fallback",
        "status": "implemented",
        "description": "对可重试错误执行一次重试，连续失败后暂时熔断，并尝试已配置的备用 Endpoint。",
    },
    {
        "name": "SSE 流式治理 / TTFT",
        "status": "implemented",
        "description": "提供经网关鉴权的内部 SSE 流式通道，记录真实首段 TTFT、总耗时与最终 Endpoint。",
    },
]


def _masked_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}...{api_key[-4:]}"


def _management_enabled() -> bool:
    return bool(os.getenv("GATEWAY_ADMIN_API_KEY", "").strip())


def _require_admin(request: Request) -> str:
    expected = os.getenv("GATEWAY_ADMIN_API_KEY", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="未配置本地管理员密钥，控制台当前为只读。")
    provided = request.headers.get("X-Admin-Key", "")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="管理员密钥无效。")
    return "local-console-admin"


def _require_status(status: str) -> str:
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail="状态仅支持 enabled 或 disabled。")
    return status


def _external_gateway(request: Request) -> dict:
    config = request.app.state.config.get("external_gateway", {})
    enabled = bool(config.get("enabled", False))
    return {
        "name": "外部 API Gateway",
        "implementation": str(config.get("type", "apisix")).upper(),
        "status": "connected" if enabled else "reserved",
        "enabled": enabled,
        "description": config.get("description", "未配置外部网关信息。"),
        "current_ingress": "FastAPI 业务服务直连（8500 / 8502）" if not enabled else "由外部 API Gateway 接入",
    }


async def _services(request: Request) -> list[dict]:
    mcp = request.app.state.mcp
    config = request.app.state.config
    records = []
    for name in mcp.service_names:
        health = await mcp.check_health(name)
        records.append({
            "name": name,
            "label": SERVICE_LABELS.get(name, name),
            "description": SERVICE_DESCRIPTIONS.get(name, "提供内部 MCP 共享服务能力"),
            "url": config["services"][name]["url"],
            "protocol": "MCP Streamable HTTP",
            "checked_at": request.app.state.events_timestamp(),
            **health,
        })
    return records


async def _endpoint_runtime_health(request: Request) -> dict[str, dict]:
    """读取 LLM Service 的运行时熔断状态；读取失败不影响控制台其他数据。"""
    endpoint_ids = [item["endpoint_id"] for item in request.app.state.control_plane.list_endpoints()]
    fallback = {
        endpoint_id: {
            "status": "unknown",
            "consecutive_failures": 0,
            "last_error": "",
            "mode": "unavailable",
        }
        for endpoint_id in endpoint_ids
    }
    try:
        result = await request.app.state.mcp.call_tool(
            "llm-gateway", "get_endpoint_runtime_status", {}
        )
    except Exception:
        return fallback
    return result.get("endpoints", fallback) if isinstance(result, dict) else fallback


def _llm_internal_url(request: Request, path: str) -> str:
    service_url = request.app.state.config["services"]["llm-gateway"]["url"]
    return service_url.rsplit("/mcp", 1)[0] + path


async def _fault_injection_request(request: Request, method: str, payload: dict | None = None) -> dict:
    internal_key = (
        os.getenv("LLM_INTERNAL_STREAM_KEY", "").strip()
        or os.getenv("GATEWAY_ADMIN_API_KEY", "").strip()
    )
    if not internal_key:
        raise HTTPException(status_code=503, detail="未配置 LLM Service 内部验证凭证。")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.request(
                method,
                _llm_internal_url(request, "/api/internal/fault-injection"),
                headers={"X-Internal-Stream-Key": internal_key},
                json=payload,
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="LLM Service 本地验证接口不可用。") from exc


@router.get("/overview")
async def overview(request: Request):
    services = await _services(request)
    quota_usage = request.app.state.quota.get_all_usage()
    event_overview = request.app.state.events.overview()
    return {
        "gateway": {
            "name": "内部 MCP 服务网关",
            "implementation": "FastAPI",
            "status": "ok",
            "port": 8000,
        },
        "external_gateway": _external_gateway(request),
        "services": {
            "total": len(services),
            "healthy": sum(item["status"] == "ok" for item in services),
            "unhealthy": sum(item["status"] != "ok" for item in services),
        },
        "projects": {
            "total": len(quota_usage),
            "used_tokens": sum(item["used_tokens"] for item in quota_usage),
            "daily_limit": sum(item["daily_limit"] for item in quota_usage if item["daily_limit"] > 0),
        },
        "traces": event_overview,
        "event_buffer_notice": "仅保留最近内存事件，网关重启后清空。",
    }


@router.get("/external-gateway")
async def external_gateway(request: Request):
    """返回外部 API Gateway 的接入预留状态，不伪造运行健康度。"""
    return _external_gateway(request)


@router.get("/services")
async def services(request: Request):
    return {"services": await _services(request)}


@router.post("/services/health-check")
async def health_check(request: Request):
    return {"services": await _services(request)}


@router.get("/projects")
async def projects(request: Request):
    items = []
    for project in request.app.state.control_plane.list_projects():
        usage = request.app.state.quota.get_usage(project["project_id"])
        daily_limit = int(project["daily_token_limit"])
        used_tokens = int(usage["used_tokens"])
        items.append({
            "project_id": project["project_id"],
            "api_key_hint": project["key_hint"] or "-",
            "status": project["status"],
            "used_tokens": used_tokens,
            "daily_limit": daily_limit,
            "remaining": max(0, daily_limit - used_tokens) if daily_limit > 0 else -1,
        })
    return {
        "projects": items,
        "subscriptions": request.app.state.control_plane.list_subscriptions(),
        "management_enabled": _management_enabled(),
        "notice": "API Key 已脱敏；管理员密钥未配置时，本页仅支持只读查看。",
    }


@router.get("/traces")
async def traces(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    status: str | None = Query(default=None, pattern="^(completed|failed)$"),
    project_id: str | None = None,
    service: str | None = None,
):
    return {
        "traces": request.app.state.events.list_traces(
            limit=limit,
            status=status,
            project_id=project_id,
            service=service,
        ),
        "notice": "仅保留最近内存事件，网关重启后清空。",
    }


@router.get("/traces/{chain_id}")
async def trace_detail(request: Request, chain_id: str):
    trace = request.app.state.events.get_trace(chain_id)
    if not trace:
        raise HTTPException(status_code=404, detail="未找到该调用链，可能已超出内存保留范围。")
    return trace


@router.get("/usage")
async def usage(request: Request, limit: int = Query(default=50, ge=1, le=200)):
    """返回 SQLite 中持久化的 LLM 调用运营明细，不包含请求与响应正文。"""
    records = request.app.state.control_plane.list_llm_call_records(limit=limit)
    return {
        "summary": {
            "requests": len(records),
            "completed": sum(item["status"] == "completed" for item in records),
            "failed": sum(item["status"] == "failed" for item in records),
            "total_tokens": sum(int(item["total_tokens"]) for item in records),
            "estimated_cost": sum(float(item["estimated_cost"]) for item in records),
        },
        "records": records,
        "notice": "数据来自 SQLite 调用明细；不保存提示词或模型回答。当前价格配置为 0 时，估算成本显示为 0。",
    }


@router.get("/resources")
async def resources(request: Request):
    records = request.app.state.control_plane.list_llm_call_records(limit=100)
    latest_by_scene: dict[str, dict] = {}
    for record in records:
        latest_by_scene.setdefault(record["logical_name"], record)
    return {
        "endpoints": request.app.state.control_plane.list_endpoints(),
        "routes": request.app.state.control_plane.list_routes(),
        "latest_by_scene": latest_by_scene,
        "runtime_health": await _endpoint_runtime_health(request),
        "management_enabled": _management_enabled(),
        "notice": "当前 Demo 从 SQLite 读取配置，熔断状态保存在 LLM Service 内存；真实场景中由 Redis / 配置中心提供运行时配置。",
    }


@router.get("/reliability")
async def reliability(request: Request, limit: int = Query(default=100, ge=1, le=200)):
    """返回 Endpoint 运行状态与最近回退/重试明细，不保存模型正文。"""
    records = request.app.state.control_plane.list_llm_call_records(limit=limit)
    runtime_health = await _endpoint_runtime_health(request)
    endpoint_summary: dict[str, dict] = {}
    for item in records:
        endpoint_id = item["endpoint_id"] or "unknown"
        summary = endpoint_summary.setdefault(endpoint_id, {
            "endpoint_id": endpoint_id,
            "requests": 0,
            "failed": 0,
            "fallbacks": 0,
            "retries": 0,
            "average_duration_ms": 0,
            "_duration_total": 0,
        })
        summary["requests"] += 1
        summary["failed"] += int(item["status"] == "failed")
        summary["fallbacks"] += int(bool(item.get("fallback_used")))
        summary["retries"] += int(item.get("retry_count", 0) or 0)
        summary["_duration_total"] += int(item["duration_ms"] or 0)
    for summary in endpoint_summary.values():
        summary["average_duration_ms"] = round(summary.pop("_duration_total") / summary["requests"])
        summary["runtime"] = runtime_health.get(summary["endpoint_id"], {
            "status": "unknown", "consecutive_failures": 0, "mode": "unavailable"
        })
    return {
        "runtime_health": runtime_health,
        "routes": request.app.state.control_plane.list_routes(),
        "endpoint_summary": list(endpoint_summary.values()),
        "recent_records": records[:30],
        "notice": "连续 3 次可重试失败后暂时熔断 30 秒；演示版状态驻留在 LLM Service 内存，生产环境应使用 Redis 或专用健康检查组件。",
    }


@router.get("/stream-runs")
async def stream_runs(request: Request, limit: int = Query(default=20, ge=1, le=80)):
    """短期回放流式决策事件，正文不进入服务台缓存。"""
    return {
        "runs": request.app.state.stream_runs.list(limit=limit),
        "notice": "仅保留网关进程内的近期事件；delta 正文不写入回放缓存。",
    }


@router.get("/stream-runs/{chain_id}")
async def stream_run_detail(request: Request, chain_id: str):
    run = request.app.state.stream_runs.get(chain_id)
    if not run:
        raise HTTPException(status_code=404, detail="未找到该流式验证记录，可能已重启或超出内存保留范围。")
    return run


@router.get("/fault-injection")
async def fault_injection_status(request: Request):
    _require_admin(request)
    return await _fault_injection_request(request, "GET")


@router.post("/fault-injection")
async def configure_fault_injection(request: Request, body: FaultInjectionRequest):
    _require_admin(request)
    result = await _fault_injection_request(request, "POST", body.model_dump())
    return {**result, "notice": "仅用于本地服务台验证；规则保存在 LLM Service 内存并在消耗后自动清除。"}


@router.delete("/fault-injection")
async def clear_fault_injection(request: Request):
    _require_admin(request)
    return await _fault_injection_request(request, "DELETE")


@router.post("/resources/endpoints")
async def create_endpoint(request: Request, body: EndpointCreateRequest):
    actor = _require_admin(request)
    try:
        request.app.state.control_plane.create_endpoint(actor=actor, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.patch("/resources/endpoints/{endpoint_id}")
async def update_endpoint(request: Request, endpoint_id: str, body: EndpointUpdateRequest):
    actor = _require_admin(request)
    try:
        request.app.state.control_plane.update_endpoint(
            endpoint_id=endpoint_id,
            status=_require_status(body.status),
            timeout_ms=body.timeout_ms,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.patch("/resources/routes/{logical_name}")
async def update_route(request: Request, logical_name: str, body: RouteUpdateRequest):
    actor = _require_admin(request)
    try:
        request.app.state.control_plane.update_route(
            logical_name=logical_name,
            primary_endpoint_id=body.primary_endpoint_id,
            fallback_endpoint_id=body.fallback_endpoint_id,
            input_token_price=body.input_token_price,
            output_token_price=body.output_token_price,
            retry_limit=body.retry_limit,
            status=_require_status(body.status),
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.patch("/projects/{project_id}")
async def update_project(request: Request, project_id: str, body: ProjectUpdateRequest):
    actor = _require_admin(request)
    try:
        request.app.state.control_plane.update_project(
            project_id=project_id,
            status=_require_status(body.status),
            daily_token_limit=body.daily_token_limit,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    request.app.state.quota.set_limit(project_id, body.daily_token_limit)
    return {"ok": True}


@router.put("/projects/{project_id}/subscriptions/{logical_name}")
async def update_subscription(
    request: Request,
    project_id: str,
    logical_name: str,
    body: SubscriptionUpdateRequest,
):
    actor = _require_admin(request)
    try:
        request.app.state.control_plane.set_subscription(
            project_id=project_id,
            logical_name=logical_name,
            status=_require_status(body.status),
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/models")
async def models():
    model_router = ModelRouter()
    return {
        "models": model_router.list_models(),
        "default_model": model_router.default_model,
        "provider": "Ark / OpenAI Compatible",
        "routing_status": "implemented",
        "notice": "资源与路由配置请在服务台“模型服务”页查看；真实场景中由 Redis / 配置中心提供运行时配置。",
    }


@router.get("/capabilities")
async def capabilities():
    return {"capabilities": CAPABILITIES}
