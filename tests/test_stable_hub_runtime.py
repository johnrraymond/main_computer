from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import urlopen

from main_computer.stable_hub import build_hub_identity, create_stable_hub_server
from main_computer.stable_hub_topology import load_stable_hub_topology, stable_hub_topology_to_dict
from tools.stable_hub_lab.run_lab import _child_command


DEV_TOPOLOGY = Path("deploy/hub-topology/dev-topology.json")


def test_stable_hub_identity_resolves_concrete_hub_from_topology() -> None:
    topology = load_stable_hub_topology(DEV_TOPOLOGY)

    identity = build_hub_identity(topology, "dev-hub2")

    assert identity["ok"] is True
    assert identity["hub_id"] == "dev-hub2"
    assert identity["hub_url"] == "http://127.0.0.1:8872"
    assert identity["cluster_id"] == "main-computer-dev-stable-hub"
    assert identity["storage"]["cluster_file"] == ".foundationdb/docker.cluster"
    assert identity["storage"]["namespace"] == "main-computer-stable-hub-dev"
    assert identity["contract"]["auth"] == "multisession-wallet"
    assert identity["contract"]["worker_connection"] == "long-lived-msk-session"
    assert [peer["hub_id"] for peer in identity["peer_hubs"]] == ["dev-hub1", "dev-hub3"]


def test_stable_hub_topology_serializes_for_api() -> None:
    topology = load_stable_hub_topology(DEV_TOPOLOGY)

    serialized = stable_hub_topology_to_dict(topology)

    assert serialized["kind"] == "main_computer.stable_hub_topology.v1"
    assert serialized["cluster_id"] == "main-computer-dev-stable-hub"
    assert serialized["entry_urls"] == [
        "http://127.0.0.1:8871",
        "http://127.0.0.1:8872",
        "http://127.0.0.1:8873",
    ]
    assert [hub["hub_id"] for hub in serialized["hubs"]] == ["dev-hub1", "dev-hub2", "dev-hub3"]


def test_stable_hub_serves_health_and_identity() -> None:
    topology = load_stable_hub_topology(DEV_TOPOLOGY)
    server = create_stable_hub_server(
        topology=topology,
        hub_id="dev-hub1",
        bind_host="127.0.0.1",
        bind_port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        with urlopen(f"{base_url}/health", timeout=2) as response:  # noqa: S310 - local test server
            health = json.loads(response.read().decode("utf-8"))
        with urlopen(f"{base_url}/api/hub/v1/hub-identity", timeout=2) as response:  # noqa: S310
            identity = json.loads(response.read().decode("utf-8"))
        with urlopen(f"{base_url}/api/hub/v1/topology", timeout=2) as response:  # noqa: S310
            topology_payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert health["ok"] is True
    assert health["hub_id"] == "dev-hub1"
    assert identity["hub_id"] == "dev-hub1"
    assert identity["contract"]["heartbeat"] == "connection-ping-pong"
    assert topology_payload["topology"]["cluster_id"] == "main-computer-dev-stable-hub"


def test_stable_hub_lab_child_command_uses_stable_hub_module_not_exp_hub() -> None:
    command = _child_command(DEV_TOPOLOGY, "dev-hub3")

    assert "-m" in command
    assert "main_computer.stable_hub" in command
    assert "exp-fdb-hub.py" not in " ".join(command)
    assert "dev-hub3" in command
