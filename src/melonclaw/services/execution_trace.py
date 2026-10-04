"""执行过程的展示快照与耗时记录，与业务收尾事务分离。"""

import copy
from typing import Any

from melonclaw.output.events import DISPLAY_EVENT_TYPES
from melonclaw.output.formatting import _preview


class ExecutionTrace:
    def __init__(self, preparation_ms: int, timings: dict[str, Any] | None = None,
                 events: list[dict[str, Any]] | None = None) -> None:
        self.events: list[dict[str, Any]] = copy.deepcopy(events) if events else []
        self.timings = copy.deepcopy(timings) if timings else {"preparation_ms": 0, "activities": []}
        self.timings["preparation_ms"] += preparation_ms

    def remember(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "run_activity":
            activities = self.timings["activities"]
            previous = next((item for item in activities if item["id"] == event["id"]), None)
            if previous is not None:
                previous.update(event)
            elif len(activities) < 128:
                activities.append(dict(event))
            return
        if kind not in DISPLAY_EVENT_TYPES:
            return
        if kind in {"model_usage", "context_usage"}:
            key = "call_id" if kind == "model_usage" else "scope"
            previous = next((item for item in self.events if item.get("type") == kind and item.get(key) == event.get(key)), None)
            if previous is not None:
                previous.update(event)
                return
        if kind == "subagent_text":
            for previous in reversed(self.events):
                if previous.get("subagent_id") != event.get("subagent_id"):
                    continue
                if previous.get("type") == kind and previous.get("content_kind") == event.get("content_kind"):
                    previous["text"] = _preview(f"{previous.get('text', '')}{event.get('text', '')}", limit=12000)
                    return
                break
        self.events.append(dict(event))

    def metadata(self) -> dict[str, Any]:
        return {"events": self.events, "timings": self.timings}

    def close(self) -> None:
        for event in self.events:
            if event.get("type") == "model_usage" and event.get("status") == "started":
                event["status"] = "unknown"
        for activity in self.timings["activities"]:
            if activity["status"] == "started":
                activity["status"] = "unknown"
