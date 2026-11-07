"""Security oracle: no HTTP path may trigger bridge reconciliation execution.

The internal, ledger-verified implementation is intentionally retained; this
oracle exercises the actual legacy and Stable Hub HTTP servers, not mocks of
request routing. No real RPC, contracts, or production wallets are touched.
"""
from __future__ import annotations

import http.client
import json
from pathlib import Path
from threading import Thread

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_topology import load_stable_hub_topology

REMOVED_ROUTE = "/api/hub/v1/credits/bridge-reconciliation/execute"


def _post(port: int, body: bytes) -> tuple[int, bool]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", REMOVED_ROUTE, body=body,
                     headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        status = response.status
        response.read()
        return status, response.will_close
    finally:
        conn.close()


def test_legacy_hub_reconciliation_execute_404_without_ledger_or_chain_effects(tmp_path, monkeypatch):
    config = MainComputerConfig(workspace=tmp_path, model="test-model",
                                hub_root=tmp_path / "hub-runtime", hub_bridge_backend="mock-chain")
    hub = HubHttpServer(("127.0.0.1", 0), config, verbose=False)
    thread = Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    try:
        account = "local-victim-account"
        hub.credit_ledger.issue(account_id=account, credits=10, memo="disposable oracle seed")
        before_account = hub.credit_ledger.get_account(account).as_dict()
        before_reconciliation = hub.credit_ledger.bridge_reconciliation_totals(account)
        before_ledger = hub.credit_ledger.status()
        calls: list[str] = []

        def forbid_chain(*args, **kwargs):
            calls.append("bridge_controller_invoked")
            raise AssertionError("deleted HTTP route executed reconciliation")

        monkeypatch.setattr(hub.credit_bridge_completion, "execute_bridge_reconciliation", forbid_chain)
        for body in (
            json.dumps({"request_id": "forged-terminal-request", "account_id": account,
                        "deposit_id": "0x" + "a" * 64,
                        "expected_bridge_credit_wei": "1000000000000000000"}).encode(),
            b"not-json-at-all",
            b"{}",
        ):
            status, _ = _post(hub.server_port, body)
            assert status == 404
            assert hub.credit_ledger.get_account(account).as_dict() == before_account
            assert hub.credit_ledger.bridge_reconciliation_totals(account) == before_reconciliation
            assert hub.credit_ledger.status() == before_ledger
            assert not calls
    finally:
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=5)


def test_stable_hub_reconciliation_execute_404_closes_unread_http11_body(tmp_path, monkeypatch):
    topology = load_stable_hub_topology(Path(__file__).resolve().parents[1] /
                                        "deploy/hub-topology/dev-topology.json")
    hub = create_stable_hub_server(topology=topology, hub_id="dev-hub1",
                                   bind_host="127.0.0.1", bind_port=0)
    thread = Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    try:
        calls: list[str] = []
        def forbid_chain(*args, **kwargs):
            calls.append("bridge_controller_invoked")
            raise AssertionError("deleted HTTP route invoked bridge controller")
        monkeypatch.setattr(hub.credit_bridge_completion, "execute_bridge_reconciliation", forbid_chain)
        for body in (b'{"request_id":"forged"}', b"not-valid-json"):
            status, will_close = _post(hub.server_port, body)
            assert status == 404
            assert will_close  # unread bytes cannot be interpreted as a new request
            assert not calls
    finally:
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=5)


def test_deleted_route_has_no_runtime_dispatch_client_or_advertisement():
    repo = Path(__file__).resolve().parents[1]
    for file in (
        "main_computer/hub.py",
        "main_computer/stable_hub.py",
        "main_computer/captain_cli.py",
        "tools/worker_browser_acceptance/worker_work_cycle.mjs",
    ):
        assert REMOVED_ROUTE not in (repo / file).read_text(encoding="utf-8"), file
    import main_computer.captain_cli as captain
    assert not hasattr(captain, "send_captain_bridge_refund")


def test_internal_reconciliation_still_exists_for_verified_accounting():
    from main_computer.hub_credit_bridge_completion import HubCreditBridgeCompletionService
    from main_computer.hub_credit_ledger import HubCreditLedger
    assert callable(HubCreditBridgeCompletionService.execute_bridge_reconciliation)
    assert callable(HubCreditLedger.record_bridge_reconciliation)
