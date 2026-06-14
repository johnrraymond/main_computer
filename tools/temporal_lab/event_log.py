from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_EVENT_LOG_PATH = Path("runtime") / "temporal_lab" / "events.jsonl"


class TemporalLabEventLogError(ValueError):
    """Raised when an event log entry cannot be written safely."""


def normalize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise TemporalLabEventLogError("event must be a mapping")
    normalized = dict(event)
    event_name = normalized.get("event")
    request_id = normalized.get("request_id")
    if not isinstance(event_name, str) or not event_name.strip():
        raise TemporalLabEventLogError("event must include a non-empty event name")
    if not isinstance(request_id, str) or not request_id.strip():
        raise TemporalLabEventLogError("event must include a non-empty request_id")
    normalized["event"] = event_name.strip()
    normalized["request_id"] = request_id.strip()
    return normalized


def append_jsonl_event(path: str | Path, event: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_event(event)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(normalized, sort_keys=True, separators=(",", ":")) + "\n")


def append_jsonl_events(path: str | Path, events: Iterable[Mapping[str, Any]]) -> int:
    count = 0
    for event in events:
        append_jsonl_event(path, event)
        count += 1
    return count


def read_jsonl_events(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    events: list[dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                events.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise TemporalLabEventLogError(
                    f"Invalid JSONL event at {target}:{line_number}: {exc}"
                ) from exc
    return events


def fake_token_text(seq: int) -> str:
    if seq < 1:
        raise TemporalLabEventLogError("token sequence must be >= 1")
    return f"tok-{seq:03d}"
