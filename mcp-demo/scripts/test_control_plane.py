"""第二阶段第一点：SQLite 控制面初始化与基础查询验证。"""

from __future__ import annotations

import tempfile
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared.control_plane import ControlPlaneStore


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        store = ControlPlaneStore(Path(temp_dir) / "control_plane.db")
        result = store.seed_from_gateway_config(
            PROJECT_ROOT / "gateway" / "config.yaml",
            chat_model="ep-control-plane-test",
        )
        assert result["projects"] == 1
        assert result["api_keys"] == 1
        assert result["subscriptions"] == 1

        # 初始化必须幂等，避免服务重启覆盖后续控制台修改。
        repeated = store.seed_from_gateway_config(
            PROJECT_ROOT / "gateway" / "config.yaml",
            chat_model="ep-control-plane-test",
        )
        assert repeated == {"projects": 0, "api_keys": 0, "subscriptions": 0}

        project = store.resolve_project_by_api_key("xzy-odm-report-agent-key")
        assert project is not None
        assert project["project_id"] == "odm-report-agent"
        assert project["key_hint"] != "xzy-odm-report-agent-key"
        assert store.is_subscribed("odm-report-agent", "report-generation")

        route = store.get_route("report-generation")
        assert route is not None
        assert route["primary_endpoint_id"] == "ark-report-primary"
        assert route["model_id"] == "ep-control-plane-test"

        endpoints = store.list_endpoints()
        assert len(endpoints) == 1
        assert endpoints[0]["api_key_env"] == "ARK_API_KEY"

        store.add_project_for_test("unsubscribed-test", "xzy-unsubscribed-test-key")
        assert store.resolve_project_by_api_key("xzy-unsubscribed-test-key") is not None
        assert not store.is_subscribed("unsubscribed-test", "report-generation")
        store.delete_project_for_test("unsubscribed-test")
        assert store.resolve_project_by_api_key("xzy-unsubscribed-test-key") is None

    print("Control plane SQLite initialization checks passed.")


if __name__ == "__main__":
    main()
