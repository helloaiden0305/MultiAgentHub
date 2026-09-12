"""验证 report-generation 经网关路由到 SQLite 配置的实际 Endpoint。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared.control_plane import ControlPlaneStore


CHAIN_ID = "chain-scenario-route-test"


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=90) as client:
        response = await client.post(
            "/api/tool/call",
            headers={"X-API-Key": "xzy-odm-report-agent-key", "X-Chain-ID": CHAIN_ID},
            json={
                "service": "llm-gateway",
                "tool": "chat_completion",
                "arguments": {
                    "model": "report-generation",
                    "messages": [{"role": "user", "content": "仅回复：已路由"}],
                    "max_tokens": 16,
                },
            },
        )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["model"] == "report-generation", result
    assert result["endpoint_id"] == "ark-report-primary", result
    assert result["route_reason"] == "primary_endpoint", result
    assert result["actual_model"], result

    records = ControlPlaneStore(
        PROJECT_ROOT / "data" / "sqlite" / "control_plane.db"
    ).list_llm_call_records()
    record = next(item for item in records if item["chain_id"] == CHAIN_ID)
    assert record["status"] == "completed", record
    assert record["logical_name"] == "report-generation", record
    assert record["endpoint_id"] == "ark-report-primary", record
    assert record["total_tokens"] > 0, record
    print("Scenario route checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
