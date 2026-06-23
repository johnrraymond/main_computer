from __future__ import annotations

import copy
from pathlib import Path

import pytest

from main_computer.stable_hub_topology import (
    StableHubTopologyError,
    build_lab_plan,
    load_stable_hub_topology,
    normalize_stable_hub_topology,
    select_entry_url,
)


DEV_TOPOLOGY = Path("deploy/stable-hub-lab/dev-topology.json")


def _document() -> dict:
    topology = load_stable_hub_topology(DEV_TOPOLOGY)
    return {
        "kind": topology.kind,
        "cluster_id": topology.cluster_id,
        "network": dict(topology.network),
        "storage": dict(topology.storage),
        "entry_urls": list(topology.entry_urls),
        "hubs": [
            {
                "hub_id": hub.hub_id,
                "hub_url": hub.hub_url,
                "public_url": hub.public_url,
                "roles": list(hub.roles),
            }
            for hub in topology.hubs
        ],
    }


def test_stable_hub_topology_loads_dev_network() -> None:
    topology = load_stable_hub_topology(DEV_TOPOLOGY)

    assert topology.kind == "main_computer.stable_hub_topology.v1"
    assert topology.cluster_id == "main-computer-dev-stable-hub"
    assert topology.network["network_key"] == "dev"
    assert topology.network["chain_id"] == "42424242"
    assert topology.storage["backend"] == "foundationdb"
    assert topology.storage["cluster_file"] == ".foundationdb/docker.cluster"
    assert topology.storage["namespace"] == "main-computer-stable-hub-dev"
    assert topology.hub_ids() == ("dev-hub1", "dev-hub2", "dev-hub3")


def test_stable_hub_topology_requires_concrete_hub_ids() -> None:
    document = _document()
    document["hubs"][0]["hub_id"] = ""

    with pytest.raises(StableHubTopologyError, match="hub_id"):
        normalize_stable_hub_topology(document)


def test_stable_hub_topology_rejects_duplicate_hub_ids() -> None:
    document = _document()
    document["hubs"][1]["hub_id"] = document["hubs"][0]["hub_id"]

    with pytest.raises(StableHubTopologyError, match="duplicate"):
        normalize_stable_hub_topology(document)


def test_stable_hub_topology_rejects_missing_fdb_cluster_file_field() -> None:
    document = _document()
    del document["storage"]["cluster_file"]

    with pytest.raises(StableHubTopologyError, match="storage.cluster_file"):
        normalize_stable_hub_topology(document)


def test_stable_hub_topology_selects_fixed_entry_url_for_lab() -> None:
    topology = load_stable_hub_topology(DEV_TOPOLOGY)

    assert select_entry_url(topology, mode="fixed", fixed_index=0) == "http://127.0.0.1:8871"
    assert select_entry_url(topology, mode="fixed", fixed_index=2) == "http://127.0.0.1:8873"


def test_stable_hub_topology_distinguishes_entry_urls_from_concrete_hub_urls() -> None:
    topology = load_stable_hub_topology(DEV_TOPOLOGY)
    plan = build_lab_plan(topology, worker_entry_index=2, requester_entry_index=0)

    assert plan["worker_initial_entry"]["hub_id"] == "dev-hub3"
    assert plan["worker_initial_entry"]["hub_url"] == "http://127.0.0.1:8873"
    assert plan["requester_initial_entry"]["hub_id"] == "dev-hub1"
    assert plan["requester_initial_entry"]["hub_url"] == "http://127.0.0.1:8871"
    assert plan["contract"]["routing"].startswith("entry-hub-reserves")


def test_stable_hub_lab_rejects_entry_url_that_is_not_a_concrete_hub_url() -> None:
    document = _document()
    document["entry_urls"].append("http://127.0.0.1:8999")

    with pytest.raises(StableHubTopologyError, match="entry_urls must be concrete hub URLs"):
        normalize_stable_hub_topology(document)
