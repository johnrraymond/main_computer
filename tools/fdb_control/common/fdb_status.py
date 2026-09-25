from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import FdbStatusObservation


def normalize_status_json(payload: Mapping[str, Any]) -> FdbStatusObservation:
    """Normalize documented FoundationDB status-json fields without guessing missing facts."""

    cluster = _mapping(payload.get("cluster"))
    client = _mapping(payload.get("client"))
    database_status = _mapping(client.get("database_status"))
    configuration = _mapping(cluster.get("configuration"))
    recovery_state = _mapping(cluster.get("recovery_state"))
    fault_tolerance = _mapping(cluster.get("fault_tolerance"))

    process_addresses: list[str] = []
    processes = _mapping(cluster.get("processes"))
    for item in processes.values():
        process = _mapping(item)
        address = _optional_text(process.get("address"))
        if address:
            process_addresses.append(address)

    coordinator_addresses: list[str] = []
    coordinators = _mapping(client.get("coordinators"))
    for item in _sequence(coordinators.get("coordinators")):
        coordinator = _mapping(item)
        address = _optional_text(coordinator.get("address"))
        if address:
            coordinator_addresses.append(address)

    database_available = _optional_bool(database_status.get("available"))
    if database_available is None:
        database_available = _optional_bool(cluster.get("database_available"))

    return FdbStatusObservation(
        database_available=database_available,
        database_healthy=_optional_bool(database_status.get("healthy")),
        connection_string=_optional_text(cluster.get("connection_string")),
        process_addresses=tuple(process_addresses),
        coordinator_addresses=tuple(coordinator_addresses),
        coordinator_quorum_reachable=_optional_bool(coordinators.get("quorum_reachable")),
        redundancy_mode=_optional_text(configuration.get("redundancy_mode")),
        storage_engine=_optional_text(configuration.get("storage_engine")),
        recovery_state=_optional_text(recovery_state.get("name")),
        full_replication=_optional_bool(cluster.get("full_replication")),
        max_zone_failures_without_losing_availability=_optional_int(
            fault_tolerance.get("max_zone_failures_without_losing_availability")
        ),
        raw=payload,
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> tuple[object, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return ()


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    return clean or None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
