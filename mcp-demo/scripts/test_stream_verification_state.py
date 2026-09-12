"""不访问真实模型的流式验证状态单测。"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from gateway.stream_runs import StreamRunStore
from shared.llm_gateway.fault_injection import LocalFaultInjector


def test_fault_injector() -> None:
    injector = LocalFaultInjector()
    rule = injector.configure("ark-report-primary", "connection_failure", 2)
    assert rule["remaining"] == 2
    assert injector.consume("ark-report-primary", "connection_failure") is True
    assert injector.consume("ark-report-primary", "connection_failure") is True
    assert injector.consume("ark-report-primary", "connection_failure") is False
    injector.configure("ark-report-primary", "abort_after_first_delta", 1)
    injector.clear()
    assert injector.snapshot() == []


def test_stream_run_store() -> None:
    store = StreamRunStore()
    store.start("verify-demo", "trace-demo", "odm-report-agent", "report-generation")
    store.record("verify-demo", "delta", {"sequence": 2, "content": "不应写入回放"})
    store.record("verify-demo", "done", {"sequence": 3, "duration_ms": 12})
    store.finish("verify-demo", "completed")
    run = store.get("verify-demo")
    assert run is not None
    assert run["status"] == "completed"
    assert run["events"][0]["content_length"] == len("不应写入回放")
    assert "content" not in run["events"][0]


if __name__ == "__main__":
    test_fault_injector()
    test_stream_run_store()
    print("流式验证状态单测通过")
