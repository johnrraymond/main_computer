from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import itertools
import re


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_.-]+", "-", text)
    text = text.strip("-_.")
    return text or "activity"


def _as_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = re.split(r"[,\s]+", str(value))
    tags: list[str] = []
    for item in raw_items:
        tag = _slug(item)
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= 12:
            break
    return tags


def _classify_signal(name: str, fields: dict[str, Any]) -> tuple[str, str, str, bool, list[str]]:
    seed = f"{name} {' '.join(str(value) for value in fields.values())}".lower()
    severity = "info"
    fault = False
    if any(word in seed for word in ("error", "failed", "failure", "exception", "traceback")):
        severity = "error"
        fault = True
    elif any(word in seed for word in ("warn", "rejected", "not-found", "stalled", "visible-window")):
        severity = "warn"
        fault = True

    tags = ["signal"]
    kind = "event"
    time_model = "snapshot"

    if name.startswith("server-"):
        kind = "subprocess"
        time_model = "parallel"
        tags.extend(["server", "subprocess"])
    elif "heartbeat" in name:
        kind = "heartbeat"
        time_model = "time_series"
        tags.append("heartbeat")
    elif name.startswith("route-"):
        kind = "route"
        time_model = "static_fixture"
        tags.extend(["route", "fixture"])
    elif name.startswith("api-"):
        kind = "api"
        time_model = "snapshot"
        tags.append("api")

    if "executor" in seed:
        tags.extend(["executor", "docker", "subprocess"])
        kind = "subprocess"
        time_model = "parallel"
    if "rag" in seed or "retrieval" in seed:
        tags.extend(["rag", "thinking", "local-ai"])
        kind = "ai"
        time_model = "parallel"
    if "thinking" in seed or "local-ai" in seed or "ollama" in seed:
        tags.extend(["ai", "thinking", "local-ai"])
        kind = "ai"
        time_model = "parallel"
    if "docker" in seed or "container" in seed:
        tags.extend(["docker", "executor", "subprocess"])
        kind = "subprocess"
        time_model = "parallel"
    if "aider" in seed:
        tags.append("aider")
        if "run" in seed or "job" in seed or "subprocess" in seed:
            kind = "subprocess"
            time_model = "parallel"
    if "terminal" in seed:
        tags.extend(["terminal", "subprocess"])
        kind = "subprocess"
        time_model = "parallel"
    if "vlc" in seed:
        tags.extend(["vlc", "stream"])
        kind = "stream"
        time_model = "parallel"
    if "task" in seed or "process" in seed:
        tags.append("task-manager")
    if "debug" in seed:
        tags.append("debug")

    return kind, time_model, severity, fault, tags


@dataclass
class ActivityFilter:
    id: str
    label: str
    match: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "match": dict(self.match)}


class ActivityBus:
    """Bounded in-memory activity bus for visible machine work.

    This is intentionally small: it records evidence and state transitions that are
    safe to show in the UI. It does not attempt to expose private model reasoning.
    """

    def __init__(self, workspace: Path | str, *, max_events: int = 600) -> None:
        self.workspace = Path(workspace)
        self.max_events = int(max_events)
        self._events: deque[dict[str, Any]] = deque(maxlen=self.max_events)
        self._counter = itertools.count(1)
        self._filters: dict[str, ActivityFilter] = {
            "live": ActivityFilter("live", "Live", {}),
            "faults": ActivityFilter("faults", "Faults", {"fault": True, "severity": ["warn", "error"]}),
            "subprocesses": ActivityFilter("subprocesses", "Subprocesses", {"kind": ["subprocess"], "tags": ["subprocess", "aider", "terminal", "executor", "docker", "vlc"]}),
            "streams": ActivityFilter("streams", "Streams", {"kind": ["stream"], "tags": ["stream", "viewport", "vlc", "webgl"]}),
            "ai": ActivityFilter("ai", "AI", {"tags": ["ai", "local-ai", "rag", "thinking", "ollama", "docker", "executor"]}),
            "rag": ActivityFilter("rag", "RAG", {"tags": ["rag", "retrieval", "context-inventory", "context-brief", "grounded-plan"]}),
            "thinking": ActivityFilter("thinking", "Thinking", {"tags": ["thinking", "model-call", "local-ai", "ollama"]}),
            "docker": ActivityFilter("docker", "Docker", {"tags": ["docker", "executor", "subprocess"], "kind": ["subprocess", "ai"]}),
            "fixtures": ActivityFilter("fixtures", "Fixtures", {"time_model": ["static_fixture"]}),
            "snapshots": ActivityFilter("snapshots", "Snapshots", {"time_model": ["snapshot"]}),
            "meta": ActivityFilter("meta", "Meta", {"tags": ["meta", "fixture", "snapshot", "subprocess", "stream", "ai", "rag"]}),
        }
        self.record(
            source="activity-bus",
            kind="fixture",
            time_model="static_fixture",
            severity="info",
            title="Activity bus initialized",
            message="Machine activity records are being collected in a bounded memory buffer.",
            tags=["activity", "fixture", "meta"],
        )

    def normalize(self, event: dict[str, Any]) -> dict[str, Any]:
        source = str(event.get("source") or "backend").strip() or "backend"
        kind = str(event.get("kind") or "event").strip() or "event"
        time_model = str(event.get("time_model") or event.get("timeModel") or "snapshot").strip() or "snapshot"
        severity = str(event.get("severity") or "info").strip().lower() or "info"
        if severity not in {"debug", "info", "warn", "error"}:
            severity = "info"
        tags = _as_tags(event.get("tags"))
        title = str(event.get("title") or event.get("event") or kind).strip() or kind
        message = str(event.get("message") or "").strip()
        fault = bool(event.get("fault")) or severity in {"warn", "error"}
        return {
            "id": str(event.get("id") or f"activity-{next(self._counter):06d}"),
            "ts": str(event.get("ts") or event.get("timestamp") or _now_iso()),
            "source": source,
            "kind": kind,
            "time_model": time_model,
            "severity": severity,
            "title": title,
            "message": message,
            "status": str(event.get("status") or ""),
            "tags": tags,
            "fault": fault,
            "data": event.get("data") if isinstance(event.get("data"), dict) else {},
        }

    def record(self, **event: Any) -> dict[str, Any]:
        normalized = self.normalize(dict(event))
        self._events.appendleft(normalized)
        return normalized

    def record_signal(self, name: str, fields: dict[str, Any] | None = None) -> dict[str, Any]:
        clean_fields = dict(fields or {})
        kind, time_model, severity, fault, tags = _classify_signal(name, clean_fields)
        message_parts = []
        data: dict[str, Any] = {}
        for key, value in clean_fields.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                data[key] = value
                message_parts.append(f"{key}={value}")
            else:
                data[key] = str(value)
                message_parts.append(f"{key}={value}")
        return self.record(
            source="viewport-server",
            kind=kind,
            time_model=time_model,
            severity=severity,
            title=name.replace("-", " ").title(),
            message=" ".join(message_parts)[:600],
            tags=tags,
            fault=fault,
            data=data,
        )

    def register_filter(self, filter_payload: dict[str, Any]) -> dict[str, Any]:
        filter_id = _slug(filter_payload.get("id") or filter_payload.get("label"))
        label = str(filter_payload.get("label") or filter_id).strip() or filter_id
        match = filter_payload.get("match") if isinstance(filter_payload.get("match"), dict) else {}
        record = ActivityFilter(filter_id, label, dict(match))
        self._filters[filter_id] = record
        self.record(
            source="activity-bus",
            kind="filter",
            time_model="snapshot",
            severity="info",
            title="Activity filter registered",
            message=label,
            tags=["activity", "filter", "ai"],
            data={"filter": record.as_dict()},
        )
        return record.as_dict()

    def events(self, *, limit: int = 120, filter_id: str = "live") -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), self.max_events))
        selected = list(self._events)
        if filter_id and filter_id != "live":
            selected = [event for event in selected if self._event_matches_filter(event, filter_id)]
        return selected[:limit]

    def _event_matches_filter(self, event: dict[str, Any], filter_id: str) -> bool:
        record = self._filters.get(filter_id)
        if record is None:
            return True
        match = record.match or {}
        if match.get("fault") and not event.get("fault"):
            return False
        for key in ("source", "kind", "severity", "time_model", "status"):
            expected = match.get(key)
            if not expected:
                continue
            values = expected if isinstance(expected, list) else [expected]
            if str(event.get(key) or "") not in {str(value) for value in values}:
                return False
        expected_tags = match.get("tags")
        if expected_tags:
            tags = set(_as_tags(event.get("tags")))
            expected = set(_as_tags(expected_tags))
            haystack = set(_slug(f"{event.get('source')} {event.get('kind')} {event.get('title')} {event.get('message')}").split("-"))
            if not any(tag in tags or tag in haystack for tag in expected):
                return False
        text = match.get("text")
        if text:
            haystack = f"{event.get('source')} {event.get('kind')} {event.get('title')} {event.get('message')} {' '.join(event.get('tags') or [])}".lower()
            if str(text).lower() not in haystack:
                return False
        return True

    def snapshot(self, server: Any | None = None) -> dict[str, Any]:
        latest = self._events[0] if self._events else None
        heartbeat = {
            "source": "heartbeat",
            "kind": "heartbeat",
            "time_model": "time_series",
            "status": "observed",
            "last_seen": _now_iso(),
        }
        server_summary: dict[str, Any] = {}
        if server is not None:
            server_summary = {
                "provider": getattr(server, "provider_name", ""),
                "workspace": str(getattr(getattr(server, "config", None), "workspace", self.workspace)),
                "debug_root": str(getattr(server, "debug_root", self.workspace)),
                "verbose": bool(getattr(server, "verbose", False)),
            }
        return {
            "ok": True,
            "generated_at": _now_iso(),
            "event_count": len(self._events),
            "latest_signal": latest,
            "heartbeat": heartbeat,
            "filters": [record.as_dict() for record in self._filters.values()],
            "events": self.events(limit=360),
            "server": server_summary,
            "meta_model": self.meta_model(server),
        }

    def meta_model(self, server: Any | None = None) -> dict[str, Any]:
        workspace = str(self.workspace)
        if server is not None:
            workspace = str(getattr(getattr(server, "config", None), "workspace", self.workspace))
        return {
            "generated_at": _now_iso(),
            "workspace": workspace,
            "surfaces": [
                {"id": "applications.launcher", "type": "left-panel", "time_model": "static_fixture"},
                {"id": "applications.workspace", "type": "stage", "time_model": "snapshot"},
                {"id": "machine.activity.dock", "type": "right-dock", "time_model": "time_series"},
            ],
            "activity_time_models": ["static_fixture", "snapshot", "parallel", "time_series"],
            "compute_elements": [
                {"id": "heartbeat", "kind": "heartbeat", "time_model": "time_series", "default_visible": True},
                {"id": "task-manager", "kind": "snapshot", "time_model": "snapshot"},
                {"id": "terminal", "kind": "subprocess", "time_model": "parallel"},
                {"id": "aider", "kind": "subprocess", "time_model": "parallel"},
                {"id": "rag", "kind": "ai", "time_model": "parallel", "status": "planned"},
                {"id": "local-ai", "kind": "ai", "time_model": "parallel", "status": "planned"},
                {"id": "docker-executor", "kind": "subprocess", "time_model": "parallel", "status": "planned"},
                {"id": "vlc-driver", "kind": "stream", "time_model": "parallel", "status": "planned"},
                {"id": "application-fixtures", "kind": "fixture", "time_model": "static_fixture"},
            ],
            "fault_model": {
                "visible_window": "warn",
                "stream_stalled": "error",
                "process_exit": "error",
                "missing_first_frame": "warn",
                "frontend_error": "error",
            },
            "filters": [record.as_dict() for record in self._filters.values()],
            "event_count": len(self._events),
        }
