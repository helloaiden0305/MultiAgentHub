"""验证本地熔断状态的打开、跳过与半开恢复，不访问网络。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared.llm_gateway.resilience import EndpointHealthTracker


def main() -> None:
    now = [100.0]
    tracker = EndpointHealthTracker(
        failure_threshold=2,
        cooldown_seconds=10,
        clock=lambda: now[0],
    )

    assert tracker.can_attempt("primary")
    tracker.record_failure("primary", "connection")
    assert tracker.can_attempt("primary")
    tracker.record_failure("primary", "connection")
    assert not tracker.can_attempt("primary")
    assert tracker.snapshot(["primary"])["primary"]["status"] == "open"

    now[0] += 11
    assert tracker.can_attempt("primary")
    assert tracker.snapshot(["primary"])["primary"]["status"] == "half_open"
    tracker.record_success("primary")
    snapshot = tracker.snapshot(["primary"])["primary"]
    assert snapshot["status"] == "closed"
    assert snapshot["consecutive_failures"] == 0
    print("Resilience checks passed.")


if __name__ == "__main__":
    main()
