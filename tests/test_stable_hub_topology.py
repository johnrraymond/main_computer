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


DEV_TOPOLOGY = Path("deploy/hub-topology/dev-topology.json")
SMOKE_TOPOLOGY = Path("deploy/hub-topology/smoke-topology.json")
TEST_TOPOLOGY = Path("deploy/hub-topology/test-topology.json")
TESTNET_TOPOLOGY = Path("deploy/hub-topology/testnet-topology.json")


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


def test_stable_hub_topology_allows_public_entry_alias_that_is_not_a_concrete_hub_url() -> None:
    document = _document()
    document["entry_urls"] = ["https://testnet-hub.greatlibrary.io"]

    topology = normalize_stable_hub_topology(document)

    assert topology.entry_urls == ("https://testnet-hub.greatlibrary.io",)
    assert "https://testnet-hub.greatlibrary.io" not in topology.concrete_hub_urls()


def test_neutral_hub_topology_directory_contains_local_dev_smoke_qbft_test_and_public_testnet_topologies() -> None:
    assert DEV_TOPOLOGY.exists()
    assert SMOKE_TOPOLOGY.exists()
    assert TEST_TOPOLOGY.exists()
    assert TESTNET_TOPOLOGY.exists()

    smoke_topology = load_stable_hub_topology(SMOKE_TOPOLOGY)
    test_topology = load_stable_hub_topology(TEST_TOPOLOGY)
    testnet_topology = load_stable_hub_topology(TESTNET_TOPOLOGY)

    assert smoke_topology.network["network_key"] == "dev"
    assert smoke_topology.network["network_kind"] == "smoke"
    assert smoke_topology.network["chain_rpc_url"] == "http://127.0.0.1:18545"
    assert smoke_topology.hub_ids() == ("smoke-hub1", "smoke-hub2", "smoke-hub3")

    assert test_topology.cluster_id == "main-computer-test-qbft-hub"
    assert test_topology.network["network_key"] == "test"
    assert test_topology.network["network_kind"] == "test"
    assert test_topology.network["chain_id"] == "42424241"
    assert test_topology.network["chain_rpc_url"] == "http://127.0.0.1:30010"
    assert test_topology.storage["namespace"] == "main-computer-hub-test"
    assert test_topology.hub_ids() == ("test-hub1", "test-hub2", "test-hub3")
    assert test_topology.entry_urls == (
        "http://127.0.0.1:8780",
        "http://127.0.0.1:8781",
        "http://127.0.0.1:8782",
    )

    assert testnet_topology.cluster_id == "main-computer-testnet-hub"
    assert testnet_topology.network == {
        "network_key": "testnet",
        "network_display_name": "Main Computer Testnet",
        "network_kind": "testnet",
    }
    assert "chain_id" not in testnet_topology.network
    assert "chain_rpc_url" not in testnet_topology.network
    assert testnet_topology.storage["cluster_file"] == "runtime/hub/testnet/fdb.cluster"
    assert testnet_topology.storage["namespace"] == "main-computer-testnet-exp-fdb-stable-live-sessions"
    assert testnet_topology.entry_urls == ("https://testnet-hub.greatlibrary.io",)
    assert testnet_topology.hub_ids() == ("testnet-hub1", "testnet-hub2", "testnet-hub3")
    assert testnet_topology.concrete_hub_urls() == (
        "https://testnet-hub1.greatlibrary.io",
        "https://testnet-hub2.greatlibrary.io",
        "https://testnet-hub3.greatlibrary.io",
    )
