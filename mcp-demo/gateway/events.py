"""网关调用事件缓冲区。

第一阶段仅保留最近的结构化服务调用事件，用于服务台观测。事件不记录
请求正文、Prompt 或密钥；服务重启后清空，不能作为生产审计或计费依据。
"""

from collections import deque
from datetime import datetime, timezone
from statistics import mean


class GatewayEventStore:
    """保存最近的内部 MCP 服务调用，并按 chain_id 聚合查询。"""

    def __init__(self, max_events: int = 500):
        self._events: deque[dict] = deque(maxlen=max_events)
        self._max_events = max_events

    @property
    def max_events(self) -> int:
        return self._max_events

    def record(
        self,
        *,
        chain_id: str,
        trace_id: str,
        project_id: str,
        service: str,
        operation: str,
        status: str,
        duration_ms: int,
    ) -> None:
        self._events.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "chain_id": chain_id,
            "trace_id": trace_id,
            "project_id": project_id,
            "service": service,
            "operation": operation,
            "status": status,
            "duration_ms": duration_ms,
        })

    def list_traces(
        self,
        *,
        limit: int = 30,
        status: str | None = None,
        project_id: str | None = None,
        service: str | None = None,
    ) -> list[dict]:
        """按 chain_id 聚合最近调用链，忽略不属于 Agent 对话的独立请求。"""
        grouped: dict[str, list[dict]] = {}
        for event in reversed(self._events):
            chain_id = event["chain_id"]
            if not chain_id.startswith("chain-"):
                continue
            if project_id and event["project_id"] != project_id:
                continue
            if service and event["service"] != service:
                continue
            grouped.setdefault(chain_id, []).append(event)

        traces = []
        for chain_id, events in grouped.items():
            events.reverse()
            trace_status = "failed" if any(item["status"] == "failed" for item in events) else "completed"
            if status and trace_status != status:
                continue
            traces.append({
                "chain_id": chain_id,
                "project_id": events[0]["project_id"],
                "status": trace_status,
                "started_at": events[0]["timestamp"],
                "last_event_at": events[-1]["timestamp"],
                "duration_ms": sum(item["duration_ms"] for item in events),
                "service_calls": len(events),
                "services": list(dict.fromkeys(item["service"] for item in events)),
            })

        traces.sort(key=lambda item: item["last_event_at"], reverse=True)
        return traces[:max(1, min(limit, 100))]

    def get_trace(self, chain_id: str) -> dict | None:
        traces = self.list_traces(limit=100)
        summary = next((item for item in traces if item["chain_id"] == chain_id), None)
        if not summary:
            return None
        summary["events"] = [
            event for event in self._events
            if event["chain_id"] == chain_id
        ]
        return summary

    def overview(self) -> dict:
        traces = self.list_traces(limit=100)
        completed = sum(item["status"] == "completed" for item in traces)
        failed = sum(item["status"] == "failed" for item in traces)
        durations = [item["duration_ms"] for item in traces]
        return {
            "buffer_max_events": self._max_events,
            "stored_events": len(self._events),
            "recent_traces": len(traces),
            "completed_traces": completed,
            "failed_traces": failed,
            "average_trace_duration_ms": round(mean(durations)) if durations else 0,
        }
