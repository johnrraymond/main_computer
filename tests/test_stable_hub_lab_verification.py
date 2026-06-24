from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
from tools.stable_hub_lab.run_lab import (
    build_stable_hub_verification_result,
    _render_stable_hub_verification_text,
)


DEV_TOPOLOGY = Path("deploy/hub-topology/dev-topology.json")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_test_topology(path: Path, *, hub1_url: str, hub3_url: str) -> None:
    document = json.loads(DEV_TOPOLOGY.read_text(encoding="utf-8"))
    for hub in document["hubs"]:
        if hub["hub_id"] == "dev-hub1":
            hub["hub_url"] = hub["public_url"] = hub1_url
        if hub["hub_id"] == "dev-hub3":
            hub["hub_url"] = hub["public_url"] = hub3_url
    document["entry_urls"] = [hub["hub_url"] for hub in document["hubs"]]
    path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")


def _start_server(
    *,
    topology_path: Path,
    hub_id: str,
    bind_port: int,
    msk_store: InMemoryStableMultiSessionKeyStore,
    worker_store: InMemoryStableWorkerSessionStore,
):
    topology = load_stable_hub_topology(topology_path)
    server = create_stable_hub_server(
        topology=topology,
        hub_id=hub_id,
        bind_host="127.0.0.1",
        bind_port=bind_port,
        multisession_key_store=msk_store,
        worker_session_store=worker_store,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_stable_hub_lab_verify_runs_realistic_requester_worker_traffic(tmp_path: Path) -> None:
    hub1_port = _free_port()
    hub3_port = _free_port()
    hub1_url = f"http://127.0.0.1:{hub1_port}"
    hub3_url = f"http://127.0.0.1:{hub3_port}"
    topology_path = tmp_path / "stable-hub-lab-verification-topology.json"
    _write_test_topology(topology_path, hub1_url=hub1_url, hub3_url=hub3_url)

    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    hub1, thread1 = _start_server(
        topology_path=topology_path,
        hub_id="dev-hub1",
        bind_port=hub1_port,
        msk_store=msk_store,
        worker_store=worker_store,
    )
    hub3, thread3 = _start_server(
        topology_path=topology_path,
        hub_id="dev-hub3",
        bind_port=hub3_port,
        msk_store=msk_store,
        worker_store=worker_store,
    )
    try:
        result = build_stable_hub_verification_result(
            topology_path=topology_path,
            requester_wallet_path=tmp_path / "requester-wallet.json",
            worker_wallet_path=tmp_path / "worker-wallet.json",
            timeout=5.0,
        )
    finally:
        hub1.shutdown()
        hub3.shutdown()
        hub1.server_close()
        hub3.server_close()
        thread1.join(timeout=2)
        thread3.join(timeout=2)

    assert result["ok"] is True
    proof = result["proof"]
    assert all(proof.values())
    assert result["actors"]["requester"]["hub_id"] == "dev-hub1"
    assert result["actors"]["worker"]["hub_id"] == "dev-hub3"
    traffic = result["traffic"]
    accepted_response = traffic["accepted_response"]
    assert accepted_response["accepted"] is True
    assert accepted_response["handoff"] == {
        "routed": True,
        "from_hub_id": "dev-hub1",
        "to_hub_id": "dev-hub3",
        "to_hub_url": hub3_url,
        "request_shape": "stable-requester-work",
    }
    assert accepted_response["execution"]["backend"] == "temporal"
    assert accepted_response["continuation_url"].startswith(hub3_url)
    assert traffic["entry_stream_status"] == 409
    assert traffic["rest_worker_heartbeat_status"] == 404

    rendered = _render_stable_hub_verification_text(result)
    assert "Stable Hub lab verification: ok" in rendered
    assert "entry Hub handed off to owner Hub: yes" in rendered
    assert "REST worker heartbeat forbidden: yes" in rendered
