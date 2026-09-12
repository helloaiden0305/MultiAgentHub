"""验证第二阶段控制面管理操作，不访问真实模型或本地运行数据库。"""

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
        store.seed_from_gateway_config(
            PROJECT_ROOT / "gateway" / "config.yaml",
            chat_model="ep-control-plane-management-test",
        )
        store.create_endpoint(
            endpoint_id="ark-report-secondary",
            provider="ark",
            model_id="ep-control-plane-secondary-test",
            base_url_env="SECONDARY_BASE_URL",
            api_key_env="SECONDARY_API_KEY",
            timeout_ms=45000,
            actor="test-admin",
        )
        store.update_endpoint(
            endpoint_id="ark-report-secondary",
            status="disabled",
            timeout_ms=50000,
            actor="test-admin",
        )
        endpoints = {item["endpoint_id"]: item for item in store.list_endpoints()}
        assert endpoints["ark-report-secondary"]["status"] == "disabled"
        assert endpoints["ark-report-secondary"]["timeout_ms"] == 50000

        store.update_route(
            logical_name="report-generation",
            primary_endpoint_id="ark-report-primary",
            fallback_endpoint_id="ark-report-secondary",
            input_token_price=1.5,
            output_token_price=2.5,
            retry_limit=2,
            status="enabled",
            actor="test-admin",
        )
        route = store.list_routes()[0]
        assert route["fallback_endpoint_id"] == "ark-report-secondary"
        assert route["input_token_price"] == 1.5
        assert route["retry_limit"] == 2

        store.update_project(
            project_id="odm-report-agent",
            status="disabled",
            daily_token_limit=321,
            actor="test-admin",
        )
        project = store.list_projects()[0]
        assert project["status"] == "disabled"
        assert project["daily_token_limit"] == 321

        store.set_subscription(
            project_id="odm-report-agent",
            logical_name="report-generation",
            status="disabled",
            actor="test-admin",
        )
        assert not store.is_subscribed("odm-report-agent", "report-generation")
        assert len(store.list_audit_logs()) == 5

    print("Control plane management checks passed.")


if __name__ == "__main__":
    main()
