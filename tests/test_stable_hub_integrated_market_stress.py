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
    build_stable_hub_integrated_market_stress_result,
    _render_stable_hub_integrated_market_stress_text,
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


def test_stable_hub_integrated_market_stress_includes_payout_entropy(tmp_path: Path) -> None:
    hub1_port = _free_port()
    hub3_port = _free_port()
    topology_path = tmp_path / "stable-topology.json"
    _write_test_topology(
        topology_path,
        hub1_url=f"http://127.0.0.1:{hub1_port}",
        hub3_url=f"http://127.0.0.1:{hub3_port}",
    )
    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    servers = [
        _start_server(
            topology_path=topology_path,
            hub_id="dev-hub1",
            bind_port=hub1_port,
            msk_store=msk_store,
            worker_store=worker_store,
        ),
        _start_server(
            topology_path=topology_path,
            hub_id="dev-hub3",
            bind_port=hub3_port,
            msk_store=msk_store,
            worker_store=worker_store,
        ),
    ]

    try:
        result = build_stable_hub_integrated_market_stress_result(
            topology_path=topology_path,
            requester_wallet_path=tmp_path / "requester-wallet.json",
            worker_wallet_path=tmp_path / "worker-wallet.json",
            timeout=5.0,
        )
    finally:
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)

    assert result["ok"] is True
    proof = result["proof"]
    assert proof["remote_handoff_succeeded_before_reconnect"] is True
    assert proof["worker_result_charged_payout_hold"] is True
    assert proof["worker_failure_released_payout_hold"] is True
    assert proof["duplicate_worker_result_was_idempotent"] is True
    assert proof["worker_disconnected_and_reconnected"] is True
    assert proof["reconnect_handoff_used_new_owner_hub"] is True
    assert proof["market_price_constraint_rejected_too_low_request"] is True
    assert proof["worker_earnings_created_only_for_successes"] is True
    assert proof["payout_claim_idempotency"] is True
    assert proof["settlement_and_bridge_path_completed"] is True
    assert proof["no_double_charge_on_duplicate_result"] is True
    assert proof["accepted_sessions_linked_to_payout_path"] is True
    assert result["metrics"]["invariant_violations"] == 0

    rendered = _render_stable_hub_integrated_market_stress_text(result)
    assert "Stable Hub integrated market stress: ok" in rendered
    assert "worker result charged payout hold: yes" in rendered
    assert "worker disconnected and reconnected: yes" in rendered


def test_stable_hub_integrated_market_stress_filters_prior_payout_status(tmp_path: Path) -> None:
    hub1_port = _free_port()
    hub3_port = _free_port()
    topology_path = tmp_path / "stable-topology.json"
    _write_test_topology(
        topology_path,
        hub1_url=f"http://127.0.0.1:{hub1_port}",
        hub3_url=f"http://127.0.0.1:{hub3_port}",
    )
    msk_store = InMemoryStableMultiSessionKeyStore()
    worker_store = InMemoryStableWorkerSessionStore()
    servers = [
        _start_server(
            topology_path=topology_path,
            hub_id="dev-hub1",
            bind_port=hub1_port,
            msk_store=msk_store,
            worker_store=worker_store,
        ),
        _start_server(
            topology_path=topology_path,
            hub_id="dev-hub3",
            bind_port=hub3_port,
            msk_store=msk_store,
            worker_store=worker_store,
        ),
    ]

    try:
        first = build_stable_hub_integrated_market_stress_result(
            topology_path=topology_path,
            requester_wallet_path=tmp_path / "requester-wallet-1.json",
            worker_wallet_path=tmp_path / "worker-wallet-1.json",
            timeout=5.0,
        )
        second = build_stable_hub_integrated_market_stress_result(
            topology_path=topology_path,
            requester_wallet_path=tmp_path / "requester-wallet-2.json",
            worker_wallet_path=tmp_path / "worker-wallet-2.json",
            timeout=5.0,
        )
    finally:
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["metrics"]["holds"] == 3
    assert second["metrics"]["charges"] == 2
    assert second["metrics"]["worker_earnings"] == 2
    assert second["proof"]["worker_earnings_created_only_for_successes"] is True
    assert second["proof"]["no_double_charge_on_duplicate_result"] is True
