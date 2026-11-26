"""The deployment producer and persisted topology must agree on public identity."""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from urllib.request import urlopen

import pytest

from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
from main_computer.stable_hub_topology import (
    StableHubTopologyError, load_stable_hub_topology,
    normalize_stable_hub_topology, stable_hub_topology_to_dict,
)
from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
from tools.hub_control.common.deployment import render_runtime_topology
from tools.hub_control.common.models import DependencyContract, HubContext, HubPlacement


@pytest.fixture(params=[("mainnet", "42424240", "mainneta-hub1"),
                        ("testnet", "42424241", "testnetb-hub1")])
def deployment(request):
    network, chain_id, hub_id = request.param
    directory = f"/data/main-computer/hub/{hub_id}"
    return render_runtime_topology(
        HubContext.from_repo(Path(__file__).resolve().parents[1]), network=network,
        placement=HubPlacement(hub_id, "controller", "host",
                               f"https://{hub_id}.greatlibrary.io", directory,
                               f"{directory}/fdb.cluster", f"{directory}/topology.json", hub_id),
        accepted_hubs=[],
        fdb_contract=DependencyContract("fdb", network, 1, "fixture", {"namespace": f"fixture-{network}"}),
        chain_contract=DependencyContract("chain", network, 1, "fixture", {
            "chain_id": chain_id, "rpc_url": f"https://{network}-rpc.greatlibrary.io"}),
    )


def test_producer_writes_canonical_kind(deployment):
    assert deployment["network"]["network_kind"] == deployment["network"]["network_key"]
    assert "kind" not in deployment["network"]
    assert deployment["kind"] == "main_computer.stable_hub_topology.v1"


@pytest.mark.parametrize("persisted_misnamed_field", [False, True])
def test_real_http_identity_from_persisted_topology(deployment, persisted_misnamed_field, tmp_path):
    expected = copy.deepcopy(deployment)
    if persisted_misnamed_field:
        deployment["network"]["kind"] = deployment["network"].pop("network_kind")
    path = tmp_path / "topology.json"
    original = json.dumps(deployment)
    path.write_text(original, encoding="utf-8")
    topology = load_stable_hub_topology(path)
    assert stable_hub_topology_to_dict(topology) == expected
    assert path.read_text(encoding="utf-8") == original
    hub = create_stable_hub_server(
        topology=topology, hub_id=topology.hub_ids()[0], bind_host="127.0.0.1", bind_port=0,
        multisession_key_store=InMemoryStableMultiSessionKeyStore(),
        worker_session_store=InMemoryStableWorkerSessionStore(),
    )
    thread = threading.Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{hub.server_port}/api/hub/v1/hub-identity", timeout=3) as response:
            identity = json.load(response)
        assert identity["network"] == {key: expected["network"][key] for key in
                                       ("network_key", "network_kind", "chain_id")}
        assert identity["cluster_id"] == expected["cluster_id"]
        assert identity["hub_id"] == topology.hub_ids()[0]
        assert "storage" not in identity
    finally:
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=3)


def test_conflicting_kind_is_rejected(deployment):
    deployment["network"]["kind"] = "dev"
    with pytest.raises(StableHubTopologyError, match="conflicting network.kind"):
        normalize_stable_hub_topology(deployment)


def test_normalization_does_not_infer_kind_from_key_or_chain_id(deployment):
    deployment["network"].pop("network_kind")
    topology = normalize_stable_hub_topology(deployment)
    assert "network_kind" not in topology.network
