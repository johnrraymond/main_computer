from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .canonical import canonical_bytes
from .errors import HubControlError
from .models import HubContext

ACCEPTED_SCHEMA = "main-computer.hub-accepted.v1"
ADD_OPERATION_SCHEMA = "main-computer.hub.add-operation.v1"
REMOVE_OPERATION_SCHEMA = "main-computer.hub.remove-operation.v1"


def network_root(ctx: HubContext, network: str) -> Path:
    clean = _safe(network)
    return ctx.hub_state_root / clean


def accepted_path(ctx: HubContext, network: str) -> Path:
    return network_root(ctx, network) / "accepted.json"


def operation_path(ctx: HubContext, network: str, operation_id: str) -> Path:
    return network_root(ctx, network) / "operations" / f"{_safe(operation_id)}.json"


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HubControlError("HUB_STATE_READ_FAILED", f"could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HubControlError("HUB_STATE_READ_FAILED", f"state file is not a JSON object: {path}")
    return value


def read_accepted(ctx: HubContext, network: str) -> dict[str, Any] | None:
    payload = read_json(accepted_path(ctx, network))
    if payload is None:
        return None
    if payload.get("schema") != ACCEPTED_SCHEMA or payload.get("network") != network:
        raise HubControlError("HUB_ACCEPTED_STATE_INVALID", f"accepted Hub state has unsupported schema/network for {network!r}")
    generation = payload.get("generation")
    hubs = payload.get("hubs")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise HubControlError("HUB_ACCEPTED_STATE_INVALID", "accepted Hub generation must be a positive integer")
    if not isinstance(hubs, list):
        raise HubControlError("HUB_ACCEPTED_STATE_INVALID", "accepted Hub hubs must be a list")
    return payload


def write_operation(ctx: HubContext, payload: Mapping[str, Any]) -> None:
    schema = payload.get("schema")
    if schema not in {ADD_OPERATION_SCHEMA, REMOVE_OPERATION_SCHEMA}:
        raise ValueError("unsupported Hub operation schema")
    operation_id = str(payload.get("operation_id") or "").strip()
    network = str(payload.get("network") or "").strip()
    if not operation_id or not network:
        raise ValueError("Hub operation requires operation_id and network")
    path = operation_path(ctx, network, operation_id)
    existing = read_json(path)
    if existing is not None and existing != dict(payload):
        raise HubControlError("HUB_OPERATION_CONFLICT", f"Hub operation {operation_id!r} already exists with different content")
    _atomic_write(path, canonical_bytes(dict(payload)) + b"\n")


def require_operation(ctx: HubContext, network: str, operation_id: str) -> dict[str, Any]:
    payload = read_json(operation_path(ctx, network, operation_id))
    if payload is None or payload.get("schema") not in {ADD_OPERATION_SCHEMA, REMOVE_OPERATION_SCHEMA}:
        raise HubControlError("HUB_OPERATION_NOT_FOUND", f"prepared Hub operation {operation_id!r} was not found for {network!r}")
    return payload


def update_operation(ctx: HubContext, network: str, operation_id: str, **changes: Any) -> dict[str, Any]:
    payload = require_operation(ctx, network, operation_id)
    payload.update(changes)
    _atomic_write(operation_path(ctx, network, operation_id), canonical_bytes(payload) + b"\n")
    return payload


def publish_first_accepted(ctx: HubContext, payload: Mapping[str, Any]) -> Path:
    network = str(payload.get("network") or "")
    if payload.get("schema") != ACCEPTED_SCHEMA:
        raise ValueError("invalid Hub accepted schema")
    path = accepted_path(ctx, network)
    existing = read_accepted(ctx, network)
    if existing is not None:
        if existing == dict(payload):
            return path
        raise HubControlError("HUB_ACCEPTED_STATE_EXISTS", f"accepted Hub state already exists for {network!r}")
    if payload.get("generation") != 1:
        raise ValueError("first accepted Hub generation must be 1")
    hubs = payload.get("hubs")
    if not isinstance(hubs, list) or len(hubs) != 1:
        raise ValueError("first accepted Hub topology must contain exactly one Hub")
    _atomic_write(path, canonical_bytes(dict(payload)) + b"\n")
    return path


def advance_accepted(ctx: HubContext, expected: Mapping[str, Any], payload: Mapping[str, Any]) -> Path:
    network = str(payload.get("network") or "")
    current = read_accepted(ctx, network)
    if current == dict(payload):
        return accepted_path(ctx, network)
    if current != dict(expected):
        raise HubControlError("HUB_ACCEPTED_STATE_CHANGED", "accepted Hub topology changed after prep; inspect before retrying")
    if payload.get("generation") != int(expected.get("generation", 0)) + 1:
        raise ValueError("accepted Hub generation must advance by exactly one")
    path = accepted_path(ctx, network)
    _atomic_write(path, canonical_bytes(dict(payload)) + b"\n")
    return path


def _safe(value: str) -> str:
    clean = str(value or "").strip()
    if not clean or any(x in clean for x in ("/", "\\", "..")):
        raise ValueError(f"unsafe identifier: {value!r}")
    return clean


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)
