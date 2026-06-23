from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


TOPOLOGY_KIND = "main_computer.stable_hub_topology.v1"


class StableHubTopologyError(ValueError):
    """Raised when a stable Hub topology document is malformed."""


@dataclass(frozen=True)
class StableHubNode:
    hub_id: str
    hub_url: str
    public_url: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class StableHubTopology:
    kind: str
    cluster_id: str
    network: dict[str, Any]
    storage: dict[str, Any]
    entry_urls: tuple[str, ...]
    hubs: tuple[StableHubNode, ...]

    def hub_by_id(self, hub_id: str) -> StableHubNode:
        for hub in self.hubs:
            if hub.hub_id == hub_id:
                return hub
        raise StableHubTopologyError(f"Unknown hub_id in topology: {hub_id}")

    def hub_ids(self) -> tuple[str, ...]:
        return tuple(hub.hub_id for hub in self.hubs)

    def concrete_hub_urls(self) -> tuple[str, ...]:
        return tuple(hub.hub_url for hub in self.hubs)



def stable_hub_node_to_dict(node: StableHubNode) -> dict[str, Any]:
    """Return a JSON-serializable representation of a concrete stable Hub node."""

    return {
        "hub_id": node.hub_id,
        "hub_url": node.hub_url,
        "public_url": node.public_url,
        "roles": list(node.roles),
    }


def stable_hub_topology_to_dict(topology: StableHubTopology) -> dict[str, Any]:
    """Return a JSON-serializable representation of a stable Hub topology."""

    return {
        "kind": topology.kind,
        "cluster_id": topology.cluster_id,
        "network": dict(topology.network),
        "storage": dict(topology.storage),
        "entry_urls": list(topology.entry_urls),
        "hubs": [stable_hub_node_to_dict(hub) for hub in topology.hubs],
    }


def _require_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StableHubTopologyError(f"{field} must be an object")
    return value


def _require_non_empty_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StableHubTopologyError(f"{field} must be a non-empty string")
    return value.strip()


def _require_string_list(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise StableHubTopologyError(f"{field} must be a list of strings")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_require_non_empty_string(item, field=f"{field}[{index}]"))
    if not result:
        raise StableHubTopologyError(f"{field} must contain at least one value")
    return tuple(result)


def _validate_http_url(value: str, *, field: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise StableHubTopologyError(f"{field} must be an http(s) URL: {value!r}")


def _validate_unique(values: Sequence[str], *, field: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        duplicate_text = ", ".join(duplicates)
        raise StableHubTopologyError(f"{field} contains duplicate value(s): {duplicate_text}")


def normalize_stable_hub_topology(document: Mapping[str, Any]) -> StableHubTopology:
    """Validate and normalize a stable Hub topology document.

    The topology describes the stable-Hub cluster contract. Entry URLs are blind
    cluster entry points. Hub URLs are concrete execution/home-Hub URLs that can
    own live worker sessions.
    """

    kind = _require_non_empty_string(document.get("kind"), field="kind")
    if kind != TOPOLOGY_KIND:
        raise StableHubTopologyError(f"kind must be {TOPOLOGY_KIND!r}")

    cluster_id = _require_non_empty_string(document.get("cluster_id"), field="cluster_id")
    network = dict(_require_mapping(document.get("network"), field="network"))
    storage = dict(_require_mapping(document.get("storage"), field="storage"))

    network_key = _require_non_empty_string(network.get("network_key"), field="network.network_key")
    network["network_key"] = network_key
    chain_id = _require_non_empty_string(network.get("chain_id"), field="network.chain_id")
    network["chain_id"] = chain_id

    storage_backend = _require_non_empty_string(storage.get("backend"), field="storage.backend")
    storage["backend"] = storage_backend
    if storage_backend != "foundationdb":
        raise StableHubTopologyError("storage.backend must be 'foundationdb' for the dev stable-Hub lab")
    storage_cluster_file = _require_non_empty_string(
        storage.get("cluster_file"),
        field="storage.cluster_file",
    )
    storage["cluster_file"] = storage_cluster_file
    storage_namespace = _require_non_empty_string(storage.get("namespace"), field="storage.namespace")
    storage["namespace"] = storage_namespace

    entry_urls = _require_string_list(document.get("entry_urls"), field="entry_urls")
    for index, url in enumerate(entry_urls):
        _validate_http_url(url, field=f"entry_urls[{index}]")
    _validate_unique(entry_urls, field="entry_urls")

    raw_hubs = document.get("hubs")
    if not isinstance(raw_hubs, Sequence) or isinstance(raw_hubs, (str, bytes, bytearray)):
        raise StableHubTopologyError("hubs must be a list of hub objects")
    if len(raw_hubs) < 2:
        raise StableHubTopologyError("hubs must contain at least two concrete Hub nodes")

    hubs: list[StableHubNode] = []
    for index, raw_hub in enumerate(raw_hubs):
        hub_map = _require_mapping(raw_hub, field=f"hubs[{index}]")
        hub_id = _require_non_empty_string(hub_map.get("hub_id"), field=f"hubs[{index}].hub_id")
        hub_url = _require_non_empty_string(hub_map.get("hub_url"), field=f"hubs[{index}].hub_url")
        _validate_http_url(hub_url, field=f"hubs[{index}].hub_url")
        public_url = _require_non_empty_string(
            hub_map.get("public_url", hub_url),
            field=f"hubs[{index}].public_url",
        )
        _validate_http_url(public_url, field=f"hubs[{index}].public_url")
        roles = _require_string_list(hub_map.get("roles", ["entry", "execution"]), field=f"hubs[{index}].roles")
        if "execution" not in roles:
            raise StableHubTopologyError(f"hubs[{index}].roles must include 'execution'")
        hubs.append(StableHubNode(hub_id=hub_id, hub_url=hub_url, public_url=public_url, roles=roles))

    _validate_unique([hub.hub_id for hub in hubs], field="hubs[].hub_id")
    _validate_unique([hub.hub_url for hub in hubs], field="hubs[].hub_url")

    concrete_urls = {hub.hub_url for hub in hubs}
    missing_entry_urls = [url for url in entry_urls if url not in concrete_urls]
    if missing_entry_urls:
        missing = ", ".join(missing_entry_urls)
        raise StableHubTopologyError(
            "dev stable-Hub lab entry_urls must be concrete hub URLs from hubs[].hub_url; "
            f"missing from hubs: {missing}"
        )

    return StableHubTopology(
        kind=kind,
        cluster_id=cluster_id,
        network=network,
        storage=storage,
        entry_urls=entry_urls,
        hubs=tuple(hubs),
    )


def load_stable_hub_topology(path: str | Path) -> StableHubTopology:
    topology_path = Path(path)
    try:
        raw = json.loads(topology_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StableHubTopologyError(f"Topology file is not valid JSON: {topology_path}") from exc
    if not isinstance(raw, Mapping):
        raise StableHubTopologyError("Topology root must be a JSON object")
    return normalize_stable_hub_topology(raw)


def select_entry_url(
    topology: StableHubTopology,
    *,
    mode: str = "random",
    fixed_index: int | None = None,
    seed: str | None = None,
) -> str:
    """Select an initial blind entry URL.

    Selection is for a new logical session only. Follow-up calls that depend on
    live state must use the concrete hub_url returned by the stable Hub protocol.
    """

    if mode == "fixed":
        if fixed_index is None:
            raise StableHubTopologyError("fixed entry URL selection requires fixed_index")
        if fixed_index < 0 or fixed_index >= len(topology.entry_urls):
            raise StableHubTopologyError(
                f"fixed_index {fixed_index} is out of range for {len(topology.entry_urls)} entry URL(s)"
            )
        return topology.entry_urls[fixed_index]
    if mode == "random":
        rng = random.Random(seed) if seed is not None else random.SystemRandom()
        return rng.choice(topology.entry_urls)
    raise StableHubTopologyError(f"Unknown entry URL selection mode: {mode}")


def build_lab_plan(
    topology: StableHubTopology,
    *,
    worker_entry_index: int = 2,
    requester_entry_index: int = 0,
) -> dict[str, Any]:
    """Build a deterministic local lab plan from the topology.

    The plan intentionally forces the worker and requester to enter through
    different Hubs when enough Hubs are present. This validates the routing
    contract without requiring a fake local load-balancer process.
    """

    if len(topology.entry_urls) < 2:
        raise StableHubTopologyError("stable Hub lab requires at least two entry URLs")
    worker_index = worker_entry_index if worker_entry_index < len(topology.entry_urls) else len(topology.entry_urls) - 1
    requester_index = requester_entry_index if requester_entry_index < len(topology.entry_urls) else 0
    if worker_index == requester_index and len(topology.entry_urls) > 1:
        worker_index = (requester_index + 1) % len(topology.entry_urls)

    worker_entry_url = select_entry_url(topology, mode="fixed", fixed_index=worker_index)
    requester_entry_url = select_entry_url(topology, mode="fixed", fixed_index=requester_index)
    worker_home_hub = next((hub for hub in topology.hubs if hub.hub_url == worker_entry_url), None)
    requester_entry_hub = next((hub for hub in topology.hubs if hub.hub_url == requester_entry_url), None)
    if worker_home_hub is None or requester_entry_hub is None:
        raise StableHubTopologyError("entry URL selection did not map to a concrete Hub")

    return {
        "cluster_id": topology.cluster_id,
        "network_key": topology.network["network_key"],
        "chain_id": topology.network["chain_id"],
        "storage_backend": topology.storage["backend"],
        "fdb_cluster_file": topology.storage["cluster_file"],
        "storage_namespace": topology.storage["namespace"],
        "entry_urls": list(topology.entry_urls),
        "worker_initial_entry": {
            "index": worker_index,
            "hub_id": worker_home_hub.hub_id,
            "hub_url": worker_home_hub.hub_url,
        },
        "requester_initial_entry": {
            "index": requester_index,
            "hub_id": requester_entry_hub.hub_id,
            "hub_url": requester_entry_hub.hub_url,
        },
        "contract": {
            "auth": "multisession-wallet",
            "worker_connection": "long-lived-msk-session",
            "heartbeat": "connection-ping-pong",
            "availability_source": "live-worker-session-owner",
            "routing": "entry-hub-reserves-with-worker-home-hub-then-returns-concrete-execution-hub-url",
        },
    }
