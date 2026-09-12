"""验证逻辑场景到 Endpoint 的控制面解析，不调用外部模型。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared.control_plane import ControlPlaneStore
from shared.llm_gateway.endpoint_router import EndpointRouter


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        store = ControlPlaneStore(Path(temp_dir) / "control_plane.db")
        store.seed_from_gateway_config(
            PROJECT_ROOT / "gateway" / "config.yaml",
            chat_model="ep-endpoint-router-test",
        )
        route = EndpointRouter(store, {
            "ARK_BASE_URL": "https://example.test/api/v3",
            "ARK_API_KEY": "test-key-not-logged",
        }).resolve("report-generation")

        assert route["logical_name"] == "report-generation"
        assert route["endpoint_id"] == "ark-report-primary"
        assert route["actual_model"] == "ep-endpoint-router-test"
        assert route["route_reason"] == "primary_endpoint"
        assert route["base_url"] == "https://example.test/api/v3"
        assert route["api_key"] == "test-key-not-logged"

        store.create_endpoint(
            endpoint_id="deepseek-fallback-test",
            provider="deepseek",
            model_id="deepseek-chat",
            base_url_env="DEEPSEEK_BASE_URL",
            api_key_env="DEEPSEEK_API_KEY",
            timeout_ms=45000,
            actor="test",
        )
        store.update_route(
            logical_name="report-generation",
            primary_endpoint_id="ark-report-primary",
            fallback_endpoint_id="deepseek-fallback-test",
            input_token_price=0,
            output_token_price=0,
            status="enabled",
            actor="test",
        )
        routes = EndpointRouter(store, {
            "ARK_BASE_URL": "https://example.test/api/v3",
            "ARK_API_KEY": "ark-test-key",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
            "DEEPSEEK_API_KEY": "deepseek-test-key",
        }).resolve_candidates("report-generation")
        assert [item["endpoint_id"] for item in routes] == [
            "ark-report-primary", "deepseek-fallback-test"
        ]
        assert routes[1]["route_reason"] == "fallback_endpoint"

    print("Endpoint router checks passed.")


if __name__ == "__main__":
    main()
