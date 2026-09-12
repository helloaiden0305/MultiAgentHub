"""流式验证的短期事件回放缓存，不保存模型回答正文。"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone


class StreamRunStore:
    def __init__(self, max_runs: int = 80, max_events: int = 120) -> None:
        self.max_runs = max_runs
        self.max_events = max_events
        self._runs: OrderedDict[str, dict] = OrderedDict()

    def start(self, chain_id: str, trace_id: str, project_id: str, logical_name: str) -> None:
        self._runs[chain_id] = {
            "chain_id": chain_id,
            "trace_id": trace_id,
            "project_id": project_id,
            "logical_name": logical_name,
            "status": "running",
            "started_at": self._now(),
            "finished_at": None,
            "events": [],
        }
        self._runs.move_to_end(chain_id)
        while len(self._runs) > self.max_runs:
            self._runs.popitem(last=False)

    def record(self, chain_id: str, event: str, data: dict) -> None:
        run = self._runs.get(chain_id)
        if run is None:
            return
        item = {"event": event, "at": self._now()}
        item.update(self._sanitize(data))
        run["events"].append(item)
        if len(run["events"]) > self.max_events:
            del run["events"][:-self.max_events]

    def finish(self, chain_id: str, status: str) -> None:
        run = self._runs.get(chain_id)
        if run is None:
            return
        run["status"] = status
        run["finished_at"] = self._now()

    def get(self, chain_id: str) -> dict | None:
        run = self._runs.get(chain_id)
        return deepcopy(run) if run else None

    def list(self, limit: int = 20) -> list[dict]:
        items = list(self._runs.values())[-limit:]
        return [
            {
                "chain_id": item["chain_id"],
                "logical_name": item["logical_name"],
                "status": item["status"],
                "started_at": item["started_at"],
                "finished_at": item["finished_at"],
                "event_count": len(item["events"]),
            }
            for item in reversed(items)
        ]

    @staticmethod
    def _sanitize(data: dict) -> dict:
        result = {key: value for key, value in data.items() if key != "content"}
        if "content" in data:
            result["content_length"] = len(str(data["content"]))
        return result

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
