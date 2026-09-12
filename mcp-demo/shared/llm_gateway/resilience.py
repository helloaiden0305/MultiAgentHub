"""LLM Endpoint 的本地运行时健康状态。

演示版将状态保存在进程内存中，便于说明熔断与恢复的决策过程；
生产环境应将等价状态下沉到 Redis 或专用的健康检查组件。
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class EndpointRuntimeState:
    status: str = "closed"
    consecutive_failures: int = 0
    last_error: str = ""
    last_success_at: float | None = None
    open_until: float | None = None


class EndpointHealthTracker:
    """基于连续失败次数的轻量熔断器。"""

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        clock=time.monotonic,
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self._clock = clock
        self._states: dict[str, EndpointRuntimeState] = {}

    def _state(self, endpoint_id: str) -> EndpointRuntimeState:
        return self._states.setdefault(endpoint_id, EndpointRuntimeState())

    def can_attempt(self, endpoint_id: str) -> bool:
        state = self._state(endpoint_id)
        now = self._clock()
        if state.status != "open":
            return True
        if state.open_until is not None and now >= state.open_until:
            state.status = "half_open"
            return True
        return False

    def record_success(self, endpoint_id: str) -> None:
        state = self._state(endpoint_id)
        state.status = "closed"
        state.consecutive_failures = 0
        state.last_error = ""
        state.last_success_at = self._clock()
        state.open_until = None

    def record_failure(self, endpoint_id: str, error: str) -> None:
        state = self._state(endpoint_id)
        state.consecutive_failures += 1
        state.last_error = error[:240]
        if state.status == "half_open" or state.consecutive_failures >= self.failure_threshold:
            state.status = "open"
            state.open_until = self._clock() + self.cooldown_seconds

    def snapshot(self, endpoint_ids: list[str] | None = None) -> dict[str, dict]:
        ids = endpoint_ids if endpoint_ids is not None else list(self._states)
        now = self._clock()
        result: dict[str, dict] = {}
        for endpoint_id in ids:
            state = self._state(endpoint_id)
            status = state.status
            if status == "open" and state.open_until is not None and now >= state.open_until:
                status = "half_open"
            result[endpoint_id] = {
                "status": status,
                "consecutive_failures": state.consecutive_failures,
                "last_error": state.last_error,
                "last_success_at": state.last_success_at,
                "open_until": state.open_until,
                "mode": "local_memory_demo",
            }
        return result
