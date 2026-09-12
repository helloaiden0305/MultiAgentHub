"""验证 LLM 调用明细的 SQLite 持久化，不访问真实模型。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared.control_plane import ControlPlaneStore


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        store = ControlPlaneStore(Path(temp_dir) / "control_plane.db")
        store.record_llm_call(
            chain_id="chain-record-success",
            trace_id="trace-record-success",
            project_id="odm-report-agent",
            logical_name="report-generation",
            actual_model="ep-record-test",
            endpoint_id="ark-record-test",
            route_reason="primary_endpoint",
            input_tokens=120,
            output_tokens=80,
            total_tokens=200,
            estimated_cost=0.0032,
            status="completed",
            duration_ms=321,
        )
        store.record_llm_call(
            chain_id="chain-record-denied",
            trace_id="trace-record-denied",
            project_id="unsubscribed-test",
            logical_name="report-generation",
            actual_model="",
            endpoint_id="",
            route_reason="subscription_denied",
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            estimated_cost=0,
            status="failed",
            duration_ms=4,
        )

        records = store.list_llm_call_records()
        assert len(records) == 2
        assert records[0]["chain_id"] == "chain-record-denied"
        assert records[0]["route_reason"] == "subscription_denied"
        assert records[1]["endpoint_id"] == "ark-record-test"
        assert records[1]["total_tokens"] == 200
        assert records[1]["estimated_cost"] == 0.0032

    print("LLM call record persistence checks passed.")


if __name__ == "__main__":
    main()
