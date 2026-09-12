"""验证管理接口在无管理员密钥时只读，配置管理员密钥后可更新临时控制面。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gateway.main import app
from shared.control_plane import ControlPlaneStore


def main() -> None:
    original_store = app.state.control_plane
    original_admin_key = os.environ.get("GATEWAY_ADMIN_API_KEY")
    with tempfile.TemporaryDirectory() as temp_dir:
        test_store = ControlPlaneStore(Path(temp_dir) / "control_plane.db")
        test_store.seed_from_gateway_config(
            PROJECT_ROOT / "gateway" / "config.yaml",
            chat_model="ep-admin-api-test",
        )
        app.state.control_plane = test_store
        os.environ.pop("GATEWAY_ADMIN_API_KEY", None)
        headers = {"X-API-Key": "xzy-odm-report-agent-key"}
        try:
            with TestClient(app) as client:
                readonly = client.get("/api/admin/resources", headers=headers)
                assert readonly.status_code == 200, readonly.text
                assert not readonly.json()["management_enabled"]

                denied = client.patch(
                    "/api/admin/projects/odm-report-agent",
                    headers=headers,
                    json={"status": "enabled", "daily_token_limit": 123},
                )
                assert denied.status_code == 503, denied.text

                os.environ["GATEWAY_ADMIN_API_KEY"] = "admin-api-test-key"
                updated = client.patch(
                    "/api/admin/projects/odm-report-agent",
                    headers={**headers, "X-Admin-Key": "admin-api-test-key"},
                    json={"status": "enabled", "daily_token_limit": 123},
                )
                assert updated.status_code == 200, updated.text
                assert test_store.list_projects()[0]["daily_token_limit"] == 123

                route_updated = client.patch(
                    "/api/admin/resources/routes/report-generation",
                    headers={**headers, "X-Admin-Key": "admin-api-test-key"},
                    json={
                        "primary_endpoint_id": "ark-report-primary",
                        "fallback_endpoint_id": None,
                        "input_token_price": 0,
                        "output_token_price": 0,
                        "retry_limit": 2,
                        "status": "enabled",
                    },
                )
                assert route_updated.status_code == 200, route_updated.text
                assert test_store.list_routes()[0]["retry_limit"] == 2
        finally:
            app.state.control_plane = original_store
            app.state.quota.set_limit("odm-report-agent", 100000)
            if original_admin_key is None:
                os.environ.pop("GATEWAY_ADMIN_API_KEY", None)
            else:
                os.environ["GATEWAY_ADMIN_API_KEY"] = original_admin_key

    print("Admin management API checks passed.")


if __name__ == "__main__":
    main()
