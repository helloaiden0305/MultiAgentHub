"""
ODM 测试报告 / 问题复盘助手 — Web 界面入口。

启动前确保网关与各共享服务已运行：
    cd mcp-demo
    uvicorn gateway.main:app --port 8000

启动本 Web 应用：
    cd mcp-demo
    uv run -m uvicorn projects.odm_report_agent.web_app:app --port 8502

浏览器打开 http://127.0.0.1:8502
"""

import json
import uuid
import sys
from pathlib import Path

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("odm-report-agent.web")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import AgentExecutionError, OdmReportAgent
from gateway_client import GatewayClient

API_KEY = "xzy-odm-report-agent-key"
PROJECT_ID = "odm-report-agent"
DEFAULT_USER = "demo-user"
ALLOWED_STYLES = frozenset({"结构化", "简洁", "详细", "复盘报告", "风险说明"})

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="XZY ODM 测试报告 / 问题复盘助手")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

sessions: dict[str, dict] = {}


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class StyleRequest(BaseModel):
    style: str
    session_id: str | None = None


class SessionRequest(BaseModel):
    session_id: str


def _get_or_create_session(session_id: str | None) -> str:
    if session_id and session_id in sessions:
        return session_id
    sid = session_id or f"session-{uuid.uuid4().hex[:8]}"
    sessions[sid] = {"user_id": DEFAULT_USER, "style": "结构化"}
    return sid


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.post("/api/chat")
async def chat(req: ChatRequest):
    sid = _get_or_create_session(req.session_id)
    sess = sessions[sid]
    chain_id = f"chain-{uuid.uuid4().hex[:12]}"

    try:
        async with GatewayClient(API_KEY, chain_id=chain_id) as gw:
            agent = OdmReportAgent(
                gw,
                user_id=sess["user_id"],
                session_id=sid,
                chain_id=chain_id,
                style=sess["style"],
            )
            result = await agent.handle_message(req.message)
    except AgentExecutionError as error:
        logger.warning("chat 链路失败: chain_id=%s", chain_id)
        return JSONResponse(status_code=502, content={
            "error": "生成失败，请检查调用轨迹中的服务状态后重试。",
            "session_id": sid,
            "chain_id": chain_id,
            "trace": error.trace,
        })
    except BaseException as e:
        real = e.exceptions[0] if hasattr(e, "exceptions") else e
        logger.error("chat 处理失败: %s", real)
        return JSONResponse(status_code=502, content={
            "error": "生成失败，请稍后重试。",
            "session_id": sid,
            "chain_id": chain_id,
            "trace": {
                "status": "failed",
                "chain_id": chain_id,
                "total_duration_ms": 0,
                "steps": [],
            },
        })

    return {
        "reply": result["reply"],
        "session_id": sid,
        "chain_id": chain_id,
        "trace": result["trace"],
    }


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    """浏览器流式入口：业务服务负责会话，网关负责 LLM 鉴权与治理。"""
    sid = _get_or_create_session(req.session_id)
    sess = sessions[sid]
    chain_id = f"chain-{uuid.uuid4().hex[:12]}"

    async def event_stream():
        yield f"event: session\ndata: {json.dumps({'session_id': sid, 'chain_id': chain_id})}\n\n"
        try:
            async with GatewayClient(API_KEY, chain_id=chain_id) as gw:
                agent = OdmReportAgent(
                    gw,
                    user_id=sess["user_id"],
                    session_id=sid,
                    chain_id=chain_id,
                    style=sess["style"],
                )
                async for event, data in agent.handle_message_stream(req.message):
                    yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
        except Exception:
            logger.exception("chat stream failed: chain_id=%s", chain_id)
            yield "event: error\ndata: {\"error\":\"生成失败，请稍后重试。\"}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/style")
async def set_style(req: StyleRequest):
    if req.style not in ALLOWED_STYLES:
        return {"error": f"可选输出风格: {', '.join(sorted(ALLOWED_STYLES))}"}
    sid = _get_or_create_session(req.session_id)
    sessions[sid]["style"] = req.style
    return {"style": req.style, "session_id": sid}


@app.post("/api/clear")
async def clear_memory(req: SessionRequest):
    sid = req.session_id
    if sid not in sessions:
        return {"ok": False, "msg": "会话不存在"}

    async with GatewayClient(API_KEY) as gw:
        await gw.call_tool("memory-service", "clear_memory", {
            "project_id": PROJECT_ID,
            "session_id": sid,
        })
    return {"ok": True, "msg": "会话记忆已清空"}


@app.post("/api/profile")
async def get_profile(req: SessionRequest):
    user_id = sessions.get(req.session_id, {}).get("user_id", DEFAULT_USER)

    async with GatewayClient(API_KEY) as gw:
        data = await gw.call_tool("memory-service", "recall_user_facts", {
            "user_id": user_id,
        })
    return {"facts": data.get("facts", [])}
