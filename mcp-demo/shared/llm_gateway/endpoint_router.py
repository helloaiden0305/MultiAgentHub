"""从控制面解析逻辑场景对应的实际 LLM Endpoint。"""

from __future__ import annotations

import os
from typing import Mapping

from shared.control_plane import ControlPlaneStore


class EndpointResolutionError(RuntimeError):
    """逻辑场景无法解析为可用 Endpoint。"""


class EndpointRouter:
    """仅在 LLM Service 内读取上游连接信息。"""

    def __init__(
        self,
        control_plane: ControlPlaneStore,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._control_plane = control_plane
        self._environ = environ if environ is not None else os.environ

    def resolve(self, logical_name: str) -> dict:
        candidates = self.resolve_candidates(logical_name)
        if not candidates:
            raise EndpointResolutionError("未找到可用的逻辑模型或场景")
        return candidates[0]

    def resolve_candidates(self, logical_name: str) -> list[dict]:
        """返回按主备顺序排列的可用 Endpoint，密钥仅在 LLM Service 内解析。"""
        route = self._control_plane.get_route_candidates(logical_name)
        if not route or route["status"] != "enabled":
            raise EndpointResolutionError("未找到可用的逻辑模型或场景")

        candidates = []
        for role in ("primary", "fallback"):
            endpoint_id = route.get(f"{role}_endpoint_id")
            if not endpoint_id or route.get(f"{role}_status") != "enabled":
                continue
            base_url = self._environ.get(route[f"{role}_base_url_env"], "").strip()
            api_key = self._environ.get(route[f"{role}_api_key_env"], "").strip()
            if not base_url or not api_key or not route.get(f"{role}_model_id"):
                continue
            candidates.append({
                "logical_name": route["logical_name"],
                "endpoint_id": endpoint_id,
                "provider": route[f"{role}_provider"],
                "actual_model": route[f"{role}_model_id"],
                "route_reason": f"{role}_endpoint",
                "base_url": base_url,
                "api_key": api_key,
                "timeout_ms": route[f"{role}_timeout_ms"],
                "retry_limit": max(0, min(3, int(route.get("retry_limit", 1) or 0))),
            })

        if not candidates:
            raise EndpointResolutionError("目标 Endpoint 当前不可用或服务端配置不完整")
        return candidates
