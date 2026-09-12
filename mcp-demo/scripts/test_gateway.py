"""验证报告复盘业务使用的 HTTP Gateway 基础链路。"""

import asyncio
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared.control_plane import ControlPlaneStore

GATEWAY_URL = "http://127.0.0.1:8000"
API_KEY = "xzy-odm-report-agent-key"
PROJECT_ID = "odm-report-agent"
SESSION_ID = "gateway-report-test"
CHAIN_ID = "chain-gateway-report-test"
UNSUBSCRIBED_PROJECT = "gateway-unsubscribed-test"
UNSUBSCRIBED_KEY = "xzy-gateway-unsubscribed-test-key"
DENIED_CHAIN_ID = "chain-gateway-subscription-denied-test"


async def main():
    control_plane = ControlPlaneStore(PROJECT_ROOT / "data" / "sqlite" / "control_plane.db")
    control_plane.add_project_for_test(UNSUBSCRIBED_PROJECT, UNSUBSCRIBED_KEY)
    headers = {"X-API-Key": API_KEY, "X-Chain-ID": CHAIN_ID}
    client = httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30)
    try:
        health = await client.get("/api/health", headers={"X-Chain-ID": CHAIN_ID})
        assert health.status_code == 200, health.text
        assert health.json()["status"] == "ok", health.text
        assert health.headers.get("X-Chain-ID") == CHAIN_ID, health.headers

        unauthorized = await client.post("/api/tool/call", json={
            "service": "memory-service",
            "tool": "recall_memory",
            "arguments": {"project_id": PROJECT_ID, "session_id": SESSION_ID},
        })
        assert unauthorized.status_code == 401, unauthorized.text

        templates = await client.post(
            "/api/tool/call",
            headers=headers,
            json={"service": "prompt-hub", "tool": "list_prompt_templates", "arguments": {}},
        )
        assert templates.status_code == 200, templates.text
        assert templates.json()["count"] == 1, templates.text
        assert templates.json()["templates"][0]["name"] == "odm_report_agent"

        prompt = await client.post(
            "/api/prompt/get",
            headers=headers,
            json={
                "service": "prompt-hub",
                "prompt": "odm_report_agent",
                "arguments": {"topic": "生成蓝牙问题复盘", "style": "结构化"},
            },
        )
        assert prompt.status_code == 200, prompt.text
        assert "蓝牙问题复盘" in prompt.json()["messages"][0]["content"]

        saved = await client.post(
            "/api/tool/call",
            headers=headers,
            json={
                "service": "memory-service",
                "tool": "save_memory",
                "arguments": {
                    "project_id": PROJECT_ID,
                    "session_id": SESSION_ID,
                    "role": "user",
                    "content": "报告复盘网关测试消息",
                },
            },
        )
        assert saved.status_code == 200, saved.text

        recalled = await client.post(
            "/api/tool/call",
            headers=headers,
            json={
                "service": "memory-service",
                "tool": "recall_memory",
                "arguments": {"project_id": PROJECT_ID, "session_id": SESSION_ID},
            },
        )
        assert recalled.status_code == 200, recalled.text
        assert recalled.json()["count"] == 1, recalled.text

        cleared = await client.post(
            "/api/tool/call",
            headers=headers,
            json={
                "service": "memory-service",
                "tool": "clear_memory",
                "arguments": {"project_id": PROJECT_ID, "session_id": SESSION_ID},
            },
        )
        assert cleared.status_code == 200, cleared.text

        quota = await client.get("/api/quota/usage", headers=headers)
        assert quota.status_code == 200, quota.text
        assert quota.json()["project_id"] == PROJECT_ID, quota.text
        assert health.headers.get("X-Trace-ID"), health.headers

        overview = await client.get("/api/admin/overview", headers=headers)
        assert overview.status_code == 200, overview.text
        assert overview.json()["gateway"]["name"] == "内部 MCP 服务网关", overview.text
        assert overview.json()["services"]["total"] == 3, overview.text

        external_gateway = await client.get("/api/admin/external-gateway", headers=headers)
        assert external_gateway.status_code == 200, external_gateway.text
        assert external_gateway.json()["implementation"] == "APISIX", external_gateway.text
        assert external_gateway.json()["status"] == "reserved", external_gateway.text

        services = await client.get("/api/admin/services", headers=headers)
        assert services.status_code == 200, services.text
        assert {item["name"] for item in services.json()["services"]} == {
            "llm-gateway", "memory-service", "prompt-hub",
        }, services.text

        projects = await client.get("/api/admin/projects", headers=headers)
        assert projects.status_code == 200, projects.text
        assert any(
            item["project_id"] == PROJECT_ID for item in projects.json()["projects"]
        ), projects.text
        assert "xzy-odm-report-agent-key" not in projects.text, projects.text

        models = await client.get("/api/admin/models", headers=headers)
        assert models.status_code == 200, models.text
        assert models.json()["default_model"] == "doubao-pro", models.text

        resources = await client.get("/api/admin/resources", headers=headers)
        assert resources.status_code == 200, resources.text
        assert resources.json()["routes"][0]["logical_name"] == "report-generation", resources.text
        assert resources.json()["routes"][0]["primary_endpoint_id"] == "ark-report-primary", resources.text

        capabilities = await client.get("/api/admin/capabilities", headers=headers)
        assert capabilities.status_code == 200, capabilities.text
        assert any(item["name"] == "统一接入" for item in capabilities.json()["capabilities"]), capabilities.text
        assert all(
            item["status"] == "implemented"
            for item in capabilities.json()["capabilities"]
            if item["name"] in {"逻辑模型名", "多模型 / 多 Endpoint 路由", "模型 / 场景订阅权限", "费用与运营报表"}
        ), capabilities.text

        traces = await client.get("/api/admin/traces", headers=headers)
        assert traces.status_code == 200, traces.text
        matching = next(item for item in traces.json()["traces"] if item["chain_id"] == CHAIN_ID)
        assert matching["service_calls"] >= 4, matching

        detail = await client.get(f"/api/admin/traces/{CHAIN_ID}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert len(detail.json()["events"]) >= 4, detail.text

        usage = await client.get("/api/admin/usage", headers=headers)
        assert usage.status_code == 200, usage.text
        assert {"summary", "records", "notice"} <= usage.json().keys(), usage.text

        denied = await client.post(
            "/api/tool/call",
            headers={"X-API-Key": UNSUBSCRIBED_KEY, "X-Chain-ID": DENIED_CHAIN_ID},
            json={
                "service": "llm-gateway",
                "tool": "chat_completion",
                "arguments": {"model": "report-generation", "messages": []},
            },
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["logical_name"] == "report-generation", denied.text
    finally:
        await client.aclose()
        control_plane.delete_project_for_test(UNSUBSCRIBED_PROJECT)
        control_plane.delete_llm_call_records_for_test(DENIED_CHAIN_ID)

    print("Gateway report-agent and console API checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
