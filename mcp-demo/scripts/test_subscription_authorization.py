"""验证模型场景订阅鉴权不会请求未授权的上游服务。"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gateway.main import app


REPORT_KEY = "xzy-odm-report-agent-key"
DENIED_PROJECT = "subscription-unit-test"
DENIED_KEY = "xzy-subscription-unit-test-key"
ALLOWED_CHAIN_ID = "chain-subscription-test-allowed"
DENIED_CHAIN_ID = "chain-subscription-test-denied"


class FakeMCP:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def call_tool(self, service: str, tool: str, arguments: dict) -> dict:
        self.calls.append({"service": service, "tool": tool, "arguments": arguments})
        return {"content": "ok", "usage": {"total_tokens": 1}}


def main() -> None:
    control_plane = app.state.control_plane
    original_mcp = app.state.mcp
    fake_mcp = FakeMCP()
    app.state.mcp = fake_mcp
    control_plane.add_project_for_test(DENIED_PROJECT, DENIED_KEY)

    try:
        with TestClient(app) as client:
            allowed = client.post(
                "/api/tool/call",
                headers={"X-API-Key": REPORT_KEY, "X-Chain-ID": ALLOWED_CHAIN_ID},
                json={
                    "service": "llm-gateway",
                    "tool": "chat_completion",
                    "arguments": {
                        "model": "report-generation",
                        "project_id": "forged-project",
                        "messages": [],
                    },
                },
            )
            assert allowed.status_code == 200, allowed.text
            assert len(fake_mcp.calls) == 1
            assert fake_mcp.calls[0]["arguments"]["model"] == "report-generation"
            assert fake_mcp.calls[0]["arguments"]["project_id"] == "odm-report-agent"

            denied = client.post(
                "/api/tool/call",
                headers={"X-API-Key": DENIED_KEY, "X-Chain-ID": DENIED_CHAIN_ID},
                json={
                    "service": "llm-gateway",
                    "tool": "chat_completion",
                    "arguments": {"model": "report-generation", "messages": []},
                },
            )
            assert denied.status_code == 403, denied.text
            assert denied.json()["logical_name"] == "report-generation"
            assert len(fake_mcp.calls) == 1
    finally:
        app.state.mcp = original_mcp
        control_plane.delete_project_for_test(DENIED_PROJECT)
        control_plane.delete_llm_call_records_for_test(ALLOWED_CHAIN_ID)
        control_plane.delete_llm_call_records_for_test(DENIED_CHAIN_ID)

    print("Subscription authorization checks passed.")


if __name__ == "__main__":
    main()
