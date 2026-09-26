from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .canonical import canonical_json
from .errors import FdbControlError
from .models import AcceptedClusterState, ClusterIdentity, CoordinatorEndpoint, FdbContext, ServicePlacement

OP_SCHEMA = "main-computer.fdb.create-cluster-operation.v1"
ADD_SERVICE_OP_SCHEMA = "main-computer.fdb.add-service-operation.v1"
REMOVE_SERVICE_OP_SCHEMA = "main-computer.fdb.remove-service-operation.v1"
SUPPORTED_OPERATION_SCHEMAS = frozenset({OP_SCHEMA, ADD_SERVICE_OP_SCHEMA, REMOVE_SERVICE_OP_SCHEMA})
ACCEPTED_SCHEMA = "main-computer.fdb.accepted-cluster.v1"


def operation_path(ctx: FdbContext, network: str, operation_id: str) -> Path:
    return _network_root(ctx, network) / "operations" / f"{_safe(operation_id)}.json"


def accepted_path(ctx: FdbContext, network: str) -> Path:
    return _network_root(ctx, network) / "accepted.json"


def write_operation(ctx: FdbContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    operation_id = str(payload.get("operation_id") or "").strip()
    network = str(payload.get("network") or "").strip()
    if payload.get("schema") not in SUPPORTED_OPERATION_SCHEMAS or not operation_id or not network:
        raise ValueError("invalid operation payload")
    path = operation_path(ctx, network, operation_id)
    existing = read_json(path)
    if existing is not None and existing != dict(payload):
        raise FdbControlError(
            code="FDB_OPERATION_CONFLICT",
            message=f"operation {operation_id!r} already exists with different content",
            module_id="FDB-OFM-STATE-002",
            retry_class="inspect-first",
            effect_class="control-state",
        )
    _atomic_write(path, canonical_json(dict(payload)))
    return dict(payload)


def update_operation(ctx: FdbContext, network: str, operation_id: str, **changes: Any) -> dict[str, Any]:
    current = require_operation(ctx, network, operation_id)
    current.update(changes)
    _atomic_write(operation_path(ctx, network, operation_id), canonical_json(current))
    return current


def require_operation(ctx: FdbContext, network: str, operation_id: str) -> dict[str, Any]:
    payload = read_json(operation_path(ctx, network, operation_id))
    if not isinstance(payload, dict) or payload.get("schema") not in SUPPORTED_OPERATION_SCHEMAS:
        raise FdbControlError(
            code="FDB_OPERATION_NOT_FOUND",
            message=f"prepared FDB operation {operation_id!r} was not found for {network!r}",
            module_id="FDB-OFM-STATE-002",
        )
    return payload


def read_accepted_state(ctx: FdbContext, network: str) -> AcceptedClusterState | None:
    payload = read_json(accepted_path(ctx, network))
    if payload is None:
        return None
    if payload.get("schema") != ACCEPTED_SCHEMA:
        raise FdbControlError(
            code="FDB_ACCEPTED_STATE_INVALID",
            message=f"accepted FDB state has unsupported schema for {network!r}",
            module_id="FDB-OFM-STATE-001",
        )
    return accepted_state_from_wire(payload)


def publish_accepted_state(ctx: FdbContext, state: AcceptedClusterState) -> Path:
    path = accepted_path(ctx, state.network)
    existing = read_accepted_state(ctx, state.network)
    if existing is not None and existing != state:
        raise FdbControlError(
            code="FDB_ACCEPTED_STATE_EXISTS",
            message=f"accepted FDB state already exists for {state.network!r}; create-cluster will not overwrite it",
            module_id="FDB-OFM-STATE-001",
            retry_class="inspect-first",
        )
    _atomic_write(path, canonical_json(accepted_state_to_wire(state)))
    return path


def advance_accepted_state(
    ctx: FdbContext,
    expected_current: AcceptedClusterState,
    state: AcceptedClusterState,
) -> Path:
    """Atomically advance one accepted generation after a freshly verified mutation."""

    current = read_accepted_state(ctx, state.network)
    if current != expected_current:
        if current == state:
            return accepted_path(ctx, state.network)
        raise FdbControlError(
            code="FDB_ACCEPTED_STATE_CHANGED",
            message=(
                f"accepted FDB state for {state.network!r} changed after prep; "
                "inspect before retrying the mutation"
            ),
            module_id="FDB-OFM-STATE-001",
            retry_class="inspect-first",
        )
    if state.cluster.description != expected_current.cluster.description:
        raise ValueError("accepted cluster description cannot change during a topology mutation")
    coordinator_changed = state.coordinators != expected_current.coordinators
    cluster_id_changed = state.cluster.cluster_id != expected_current.cluster.cluster_id
    rebirth_from_empty = not expected_current.services and bool(state.services)
    if not state.services:
        if state.coordinators:
            raise ValueError("an empty accepted FDB topology cannot retain coordinators")
        if cluster_id_changed:
            raise ValueError("full FDB deletion preserves the historical cluster identity")
    elif rebirth_from_empty:
        if expected_current.coordinators:
            raise ValueError("accepted-empty prestate cannot retain coordinators")
        if cluster_id_changed:
            raise ValueError("first-service rebirth preserves the accepted historical cluster identity")
    elif coordinator_changed != cluster_id_changed:
        raise ValueError(
            "FDB cluster id must change exactly when coordinator topology changes"
        )
    if state.generation != expected_current.generation + 1:
        raise ValueError("accepted generation must advance by exactly one")
    path = accepted_path(ctx, state.network)
    _atomic_write(path, canonical_json(accepted_state_to_wire(state)))
    return path


def accepted_state_to_wire(state: AcceptedClusterState) -> dict[str, Any]:
    return {
        "schema": ACCEPTED_SCHEMA,
        "network": state.network,
        "generation": state.generation,
        "cluster": {"description": state.cluster.description, "cluster_id": state.cluster.cluster_id},
        "services": [
            {
                "service_id": item.service_id,
                "host_id": item.host_id,
                "address": item.address,
                "port": item.port,
                "machine_id": item.machine_id,
                "zone_id": item.zone_id,
            }
            for item in state.services
        ],
        "coordinators": [
            {"service_id": item.service_id, "host_id": item.host_id, "address": item.address, "port": item.port}
            for item in state.coordinators
        ],
        "redundancy_mode": state.redundancy_mode,
        "storage_engine": state.storage_engine,
        "retired": state.retired,
    }


def accepted_state_from_wire(payload: Mapping[str, Any]) -> AcceptedClusterState:
    if payload.get("schema") != ACCEPTED_SCHEMA:
        raise ValueError("accepted-state snapshot has unsupported schema")
    return AcceptedClusterState(
        network=str(payload["network"]),
        generation=int(payload["generation"]),
        cluster=ClusterIdentity(**payload["cluster"]),
        services=tuple(ServicePlacement(**item) for item in payload["services"]),
        coordinators=tuple(CoordinatorEndpoint(**item) for item in payload["coordinators"]),
        redundancy_mode=str(payload["redundancy_mode"]),
        storage_engine=str(payload["storage_engine"]),
        retired=bool(payload.get("retired", False)),
    )


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise FdbControlError(
            code="FDB_STATE_READ_FAILED",
            message=f"could not read FDB control state {path}: {exc}",
            module_id="FDB-OFM-STATE-001",
        ) from exc
    if not isinstance(payload, dict):
        raise FdbControlError(
            code="FDB_STATE_READ_FAILED",
            message=f"FDB control state is not a JSON object: {path}",
            module_id="FDB-OFM-STATE-001",
        )
    return payload


def _network_root(ctx: FdbContext, network: str) -> Path:
    return ctx.state_root / _safe(network)


def _safe(value: str) -> str:
    clean = str(value or "").strip()
    if not clean or any(part in clean for part in ("/", "\\", "..")):
        raise ValueError(f"unsafe path identifier: {value!r}")
    return clean


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_bytes(payload)
    os.replace(tmp, path)
