"""网关服务台 Web 入口，只代理内部 MCP 服务网关的只读管理接口。"""

import os
import json
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

GATEWAY_BASE_URL = os.getenv("GATEWAY_BASE_URL", "http://127.0.0.1:8000")
GATEWAY_API_KEY = os.getenv("GATEWAY_CONSOLE_API_KEY", "xzy-odm-report-agent-key")
GATEWAY_ADMIN_API_KEY = os.getenv("GATEWAY_ADMIN_API_KEY", "")
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="XZY 网关服务台")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


async def _gateway_request(method: str, path: str, *, admin: bool = False, **kwargs):
    headers = {"X-API-Key": GATEWAY_API_KEY}
    if admin and GATEWAY_ADMIN_API_KEY:
        headers["X-Admin-Key"] = GATEWAY_ADMIN_API_KEY
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.request(method, f"{GATEWAY_BASE_URL}{path}", headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="无法读取内部 MCP 服务网关数据，请确认 8000 服务已启动。",
        ) from exc


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/overview")
async def overview():
    return await _gateway_request("GET", "/api/admin/overview")


@app.get("/api/external-gateway")
async def external_gateway():
    return await _gateway_request("GET", "/api/admin/external-gateway")


@app.get("/api/services")
async def services():
    return await _gateway_request("GET", "/api/admin/services")


@app.post("/api/services/health-check")
async def health_check():
    return await _gateway_request("POST", "/api/admin/services/health-check")


@app.get("/api/projects")
async def projects():
    return await _gateway_request("GET", "/api/admin/projects")


@app.get("/api/traces")
async def traces(
    limit: int = Query(default=30, ge=1, le=100),
    status: str | None = Query(default=None, pattern="^(completed|failed)$"),
    project_id: str | None = None,
    service: str | None = None,
):
    params = {"limit": limit}
    if status:
        params["status"] = status
    if project_id:
        params["project_id"] = project_id
    if service:
        params["service"] = service
    return await _gateway_request("GET", "/api/admin/traces", params=params)


@app.get("/api/traces/{chain_id}")
async def trace_detail(chain_id: str):
    return await _gateway_request("GET", f"/api/admin/traces/{chain_id}")


@app.get("/api/usage")
async def usage(limit: int = Query(default=50, ge=1, le=200)):
    return await _gateway_request("GET", "/api/admin/usage", params={"limit": limit})


@app.get("/api/models")
async def models():
    return await _gateway_request("GET", "/api/admin/models")


@app.get("/api/capabilities")
async def capabilities():
    return await _gateway_request("GET", "/api/admin/capabilities")


@app.get("/api/resources")
async def resources():
    return await _gateway_request("GET", "/api/admin/resources")


@app.get("/api/reliability")
async def reliability():
    return await _gateway_request("GET", "/api/admin/reliability")


@app.get("/api/stream-runs")
async def stream_runs():
    return await _gateway_request("GET", "/api/admin/stream-runs")


@app.get("/api/stream-runs/{chain_id}")
async def stream_run_detail(chain_id: str):
    return await _gateway_request("GET", f"/api/admin/stream-runs/{chain_id}")


@app.post("/api/fault-injection")
async def configure_fault_injection(body: dict):
    return await _gateway_request("POST", "/api/admin/fault-injection", admin=True, json=body)


@app.delete("/api/fault-injection")
async def clear_fault_injection():
    return await _gateway_request("DELETE", "/api/admin/fault-injection", admin=True)


@app.post("/api/stream-verification")
async def stream_verification():
    """服务台发起短流式请求，密钥只保留在 8500 服务端。"""
    chain_id = f"verify-{uuid.uuid4().hex[:12]}"

    async def event_stream():
        yield f"event: verification\ndata: {json.dumps({'chain_id': chain_id}, ensure_ascii=False)}\n\n"
        headers = {"X-API-Key": GATEWAY_API_KEY, "X-Chain-ID": chain_id}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=8.0)) as client:
                async with client.stream(
                    "POST",
                    f"{GATEWAY_BASE_URL}/api/llm/stream",
                    headers=headers,
                    json={
                        "messages": [{"role": "user", "content": "请仅回复：流式验证成功。"}],
                        "model": "report-generation",
                        "temperature": 0,
                        "max_tokens": 20,
                    },
                ) as response:
                    if response.status_code != 200:
                        yield "event: error\ndata: {\"error\":\"内部网关拒绝了流式验证请求\"}\n\n"
                        return
                    async for line in response.aiter_lines():
                        if line:
                            yield f"{line}\n"
                        else:
                            yield "\n"
        except httpx.HTTPError:
            yield "event: error\ndata: {\"error\":\"无法连接内部 MCP 服务网关\"}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/resources/endpoints")
async def create_endpoint(body: dict):
    return await _gateway_request("POST", "/api/admin/resources/endpoints", admin=True, json=body)


@app.patch("/api/resources/endpoints/{endpoint_id}")
async def update_endpoint(endpoint_id: str, body: dict):
    return await _gateway_request(
        "PATCH", f"/api/admin/resources/endpoints/{endpoint_id}", admin=True, json=body
    )


@app.patch("/api/resources/routes/{logical_name}")
async def update_route(logical_name: str, body: dict):
    return await _gateway_request(
        "PATCH", f"/api/admin/resources/routes/{logical_name}", admin=True, json=body
    )


@app.patch("/api/projects/{project_id}")
async def update_project(project_id: str, body: dict):
    return await _gateway_request("PATCH", f"/api/admin/projects/{project_id}", admin=True, json=body)


@app.put("/api/projects/{project_id}/subscriptions/{logical_name}")
async def update_subscription(project_id: str, logical_name: str, body: dict):
    return await _gateway_request(
        "PUT",
        f"/api/admin/projects/{project_id}/subscriptions/{logical_name}",
        admin=True,
        json=body,
    )
