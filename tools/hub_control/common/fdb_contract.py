from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .canonical import canonical_bytes, sha256_json
from .errors import HubControlError
from .models import DependencyContract, HubContext

SCHEMA = "main-computer.fdb.consumer-contract.v1"


def _namespace(network: str) -> str:
    return f"main-computer-{network}-exp-fdb-stable-live-sessions"


def _contract_path(ctx: HubContext, network: str) -> Path:
    return ctx.fdb_state_root / network / "consumer-contract.json"


def _accepted_path(ctx: HubContext, network: str) -> Path:
    return ctx.fdb_state_root / network / "accepted.json"


def load_current_fdb_contract(ctx: HubContext, network: str, *, publish: bool = False) -> DependencyContract:
    accepted_path = _accepted_path(ctx, network)
    if not accepted_path.is_file():
        raise HubControlError("HUB_FDB_UNBORN", f"no accepted FDB state exists for {network!r}")
    try:
        accepted = json.loads(accepted_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HubControlError("HUB_FDB_STATE_INVALID", f"could not read accepted FDB state: {exc}") from exc
    if not isinstance(accepted, dict) or accepted.get("schema") != "main-computer.fdb.accepted-cluster.v1":
        raise HubControlError("HUB_FDB_STATE_INVALID", "accepted FDB state has unsupported schema")
    services = accepted.get("services")
    coordinators = accepted.get("coordinators")
    if not isinstance(services, list) or not services or not isinstance(coordinators, list) or not coordinators:
        raise HubControlError("HUB_FDB_UNAVAILABLE", "accepted FDB topology is empty; a Hub cannot be born without usable FDB")
    cluster = accepted.get("cluster")
    if not isinstance(cluster, dict):
        raise HubControlError("HUB_FDB_STATE_INVALID", "accepted FDB state is missing cluster identity")
    endpoints = sorted(
        [f"{str(item.get('address') or '').strip()}:{int(item.get('port'))}" for item in coordinators if isinstance(item, dict)],
        key=lambda item: item.encode("utf-8"),
    )
    if not endpoints or any(item.startswith(":") for item in endpoints):
        raise HubControlError("HUB_FDB_STATE_INVALID", "accepted FDB coordinator endpoints are invalid")
    connection = f"{cluster.get('description')}:{cluster.get('cluster_id')}@{','.join(endpoints)}"
    core: dict[str, Any] = {
        "schema": SCHEMA,
        "network": network,
        "generation": int(accepted.get("generation")),
        "status": "available",
        "cluster": {
            "description": str(cluster.get("description") or ""),
            "cluster_id": str(cluster.get("cluster_id") or ""),
        },
        "connection_string": connection,
        "namespace": _namespace(network),
        "api_version": 740,
        "transport": {"tls": False},
    }
    digest = sha256_json(core)
    payload = {**core, "sha256": digest}
    if publish:
        path = _contract_path(ctx, network)
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = canonical_bytes(payload) + b"\n"
        if not path.is_file() or path.read_bytes() != serialized:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(serialized)
            tmp.replace(path)
    return DependencyContract("fdb", network, int(core["generation"]), digest, payload)
