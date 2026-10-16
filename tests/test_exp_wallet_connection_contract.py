"""New live-worker identity contract for the Experimental Hub.

The authorization service is stubbed at its verified-result boundary; the
production wallet validation is covered separately by the Stable Hub tests.
"""

from __future__ import annotations

import socket
import threading
import time

from main_computer.config import MainComputerConfig
from main_computer.exp_fdb_hub import ExperimentalFoundationDbHubServerHandler
from main_computer.hub_credit_indexer import wallet_account_id
from test_exp_hub_live_session_keepalive import (
    _TestExpLiveSessionHub, _ws_connect, _ws_recv_json, _ws_send_json,
)


def test_exp_two_connections_same_wallet_do_not_replace_each_other(tmp_path, monkeypatch):
    wallet = "0x" + "ab" * 20
    account = wallet_account_id(wallet)
    verified = {
        "wallet_address": wallet,
        "account_id": account,
        "multisession_key_id": "verified-msk-injected-by-test",
    }
    monkeypatch.setattr(
        ExperimentalFoundationDbHubServerHandler,
        "_authorize_worker_route",
        lambda self, **kwargs: dict(verified),
    )
    config = MainComputerConfig(
        workspace=tmp_path, hub_root=tmp_path / "runtime",
        hub_bridge_backend="mock", hub_allow_insecure_dev_network=True,
        hub_network="dev", hub_network_kind="local-dev-chain", chain_id=42424242,
    )
    hub = _TestExpLiveSessionHub(("127.0.0.1", 0), config)
    thread = threading.Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    sockets: list[socket.socket] = []
    slot_order: list[str] = []
    try:
        for _ in range(2):
            sock = _ws_connect("127.0.0.1", hub.server_port, "/api/hub/v1/workers/live-session")
            sockets.append(sock)
            _ws_send_json(sock, {
                "type": "worker.auth", "worker_id": "same-untrusted-label",
                "market": {"rings": ["ring-3"], "models": ["gemma4"],
                           "price": {"amount": "1", "unit": "compute_credit"}},
            })
            accepted = _ws_recv_json(sock)
            assert accepted["type"] == "hub.auth.accepted", accepted
            assert accepted["account_id"] == account
            ping = _ws_recv_json(sock)
            _ws_send_json(sock, {"type": "worker.pong", "ping_id": ping["ping_id"]})
            assert _ws_recv_json(sock)["type"] == "hub.pong.accepted"
            registered = [w["worker_id"] for w in hub.stable_worker_market_directory.list_workers()
                          if w["status"] == "live" and w["worker_id"] not in slot_order]
            assert len(registered) == 1
            slot_order.append(registered[0])

        workers = hub.stable_worker_market_directory.list_workers()
        live = [record for record in workers if record["status"] == "live"]
        assert len(live) == 2
        assert len({record["worker_id"] for record in live}) == 2
        assert {record["worker_account_id"] for record in live} == {account}
        first_id, second_id = slot_order
        _ws_send_json(sockets[0], {"type": "worker.close"})
        sockets[0].close()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            record = hub.stable_worker_market_directory.get_worker(first_id)
            if record is not None and record["status"] == "closed":
                break
            time.sleep(0.03)
        assert record["status"] == "closed"
        assert hub.stable_worker_market_directory.get_worker(second_id)["status"] == "live"
        assert hub.stable_worker_session_directory.get_owner(second_id)["status"] == "live"
    finally:
        for sock in sockets:
            try:
                sock.close()
            except OSError:
                pass
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=2)
