from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid
from typing import Any


VALID_CHANNELS = {"supervisor", "applications"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def control_root(root: Path | str) -> Path:
    return Path(root).resolve() / "runtime" / "service_control"


def control_channel_dir(root: Path | str, channel: str) -> Path:
    channel = _normalize_channel(channel)
    return control_root(root) / channel


def control_queue_dir(root: Path | str, channel: str) -> Path:
    return control_channel_dir(root, channel) / "queue"


def control_processed_dir(root: Path | str, channel: str) -> Path:
    return control_channel_dir(root, channel) / "processed"


def _normalize_channel(channel: str) -> str:
    normalized = str(channel or "").strip().lower()
    if normalized not in VALID_CHANNELS:
        raise ValueError(f"Unsupported control channel: {channel!r}")
    return normalized


def _safe_slug(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "").strip())
    slug = "-".join(part for part in slug.split("-") if part)
    return slug[:64] or "request"


@dataclass(frozen=True)
class ControlRequest:
    path: Path
    channel: str
    payload: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.payload.get("id") or self.path.stem)

    @property
    def action(self) -> str:
        return str(self.payload.get("action") or "").strip().lower()

    @property
    def target(self) -> str:
        return str(self.payload.get("target") or "").strip().lower()


def enqueue_control_request(
    root: Path | str,
    *,
    channel: str,
    action: str,
    target: str,
    source: str = "",
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_channel = _normalize_channel(channel)
    action_value = str(action or "").strip().lower()
    target_value = str(target or "").strip().lower()
    request_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:12]}"
    payload = {
        "id": request_id,
        "channel": normalized_channel,
        "action": action_value,
        "target": target_value,
        "source": str(source or "unknown"),
        "parameters": dict(parameters or {}),
        "queued_at": _now_iso(),
    }

    queue_dir = control_queue_dir(root, normalized_channel)
    queue_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{request_id}-{_safe_slug(action_value)}-{_safe_slug(target_value)}.json"
    destination = queue_dir / file_name
    tmp = destination.with_suffix(destination.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, destination)
    return {
        "ok": True,
        "request": payload,
        "path": str(destination),
    }


def enqueue_supervisor_action(
    root: Path | str,
    *,
    action: str,
    target: str,
    source: str = "",
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return enqueue_control_request(
        root,
        channel="supervisor",
        action=action,
        target=target,
        source=source,
        parameters=parameters,
    )


def enqueue_applications_action(
    root: Path | str,
    *,
    action: str,
    target: str,
    source: str = "",
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return enqueue_control_request(
        root,
        channel="applications",
        action=action,
        target=target,
        source=source,
        parameters=parameters,
    )


def pending_control_requests(root: Path | str, *, channel: str, limit: int | None = None) -> list[ControlRequest]:
    normalized_channel = _normalize_channel(channel)
    queue_dir = control_queue_dir(root, normalized_channel)
    if not queue_dir.exists():
        return []

    requests: list[ControlRequest] = []
    for path in sorted(queue_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            payload = {
                "id": path.stem,
                "channel": normalized_channel,
                "action": "invalid",
                "target": "",
                "error": str(exc),
            }
        if isinstance(payload, dict):
            requests.append(ControlRequest(path=path, channel=normalized_channel, payload=payload))
        if limit is not None and len(requests) >= limit:
            break
    return requests


def complete_control_request(request: ControlRequest, *, result: dict[str, Any]) -> Path:
    queue_dir = request.path.parent
    channel_dir = queue_dir.parent
    processed_dir = channel_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    payload = dict(request.payload)
    payload["processed_at"] = _now_iso()
    payload["result"] = dict(result)
    destination = processed_dir / request.path.name
    tmp = destination.with_suffix(destination.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, destination)
    try:
        request.path.unlink()
    except FileNotFoundError:
        pass
    return destination


def control_status(root: Path | str) -> dict[str, Any]:
    status: dict[str, Any] = {
        "ok": True,
        "root": str(Path(root).resolve()),
        "control_root": str(control_root(root)),
        "channels": {},
    }
    for channel in sorted(VALID_CHANNELS):
        queue_dir = control_queue_dir(root, channel)
        processed_dir = control_processed_dir(root, channel)
        pending = sorted(path.name for path in queue_dir.glob("*.json")) if queue_dir.exists() else []
        processed = sorted(path.name for path in processed_dir.glob("*.json")) if processed_dir.exists() else []
        status["channels"][channel] = {
            "queue_dir": str(queue_dir),
            "processed_dir": str(processed_dir),
            "pending_count": len(pending),
            "processed_count": len(processed),
            "pending": pending[:20],
            "processed_recent": processed[-20:],
        }
    return status
