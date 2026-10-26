"""Local deposit records are hints; Hub and live escrow are funding authority."""
from __future__ import annotations

import json
import logging

import pytest

from main_computer.captain_cli import CaptainCliError, run_captain
from main_computer.config import MainComputerConfig
from test_captain_cli import _stub_verified_mother


ESCROW = "0x000000000000000000000000000000000000e5c0"
OLD_ID = "0x" + "a1" * 32
OLD_TX = "0x" + "b1" * 32
NEW_ID = "0x" + "a2" * 32
NEW_TX = "0x" + "b2" * 32


def _setup(tmp_path, monkeypatch, *, hub_error=None, chain_state="absent"):
    _stub_verified_mother(monkeypatch, network="testnet")
    monkeypatch.setattr("main_computer.hub_cli_live_session.hub_uses_live_sessions", lambda *_a, **_kw: False)
    registry = tmp_path / "main_computer" / "config" / "testnet_contracts.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({
        "hub_credit_bridge_escrow": ESCROW, "xlag-bridge-reserve": "0x" + "11" * 20,
    }), encoding="utf-8")
    deployment = tmp_path / "runtime" / "deployments" / "testnet" / "latest.json"
    deployment.parent.mkdir(parents=True)
    deployment.write_text(json.dumps({
        "chain": {"network": "testnet", "chain_id": 42424241,
                  "rpc_url": "https://testnet-rpc.greatlibrary.io"},
    }), encoding="utf-8")
    monkeypatch.setattr("main_computer.captain_cli._get_hub_json", lambda *_a, **_kw: {
        "ok": True, "deployment": {"chain_id": 42424241,
                                 "contract_address": ESCROW,
                                 "bridge_controller_address": "0x" + "33" * 20},
    })

    sent = []
    completed = []

    def send(*_a, **_kw):
        index = len(sent)
        deposit = {
            "deposit_id": (OLD_ID, NEW_ID)[index],
            "transaction_hash": (OLD_TX, NEW_TX)[index],
            "amount_credit_wei": str(10**18),
            "receipt": {"status": "0x1", "blockNumber": "0x100", "logs": []},
        }
        sent.append(deposit)
        return deposit

    def post(_hub, path, payload, *, timeout_s):
        if path == "/api/hub/v1/requests/quote":
            return {"ok": True}
        if path == "/api/hub/v1/credits/wallet-funding/complete":
            completed.append(payload["deposit_id"])
            # First call leaves a saved recovery record. The second invocation
            # asks the Hub for authoritative status of the original deposit.
            if len(completed) == 1:
                raise CaptainCliError("Hub temporarily unavailable")
            if payload["deposit_id"] == OLD_ID:
                detail = hub_error if hub_error is not None else f"Unknown bridge deposit: {OLD_ID}"
                raise CaptainCliError(
                    "Hub request failed for https://testnet-hub.greatlibrary.io"
                    "/api/hub/v1/credits/wallet-funding/complete with HTTP 400: "
                    + json.dumps({"ok": False, "error": detail})
                )
            return {"ok": True, "delta_credit_wei": str(10**18)}
        if path == "/api/hub/v1/requests":
            return {"ok": True}
        raise AssertionError(path)

    monkeypatch.setattr("main_computer.captain_cli.send_captain_bridge_deposit", send)
    monkeypatch.setattr("main_computer.captain_cli._post_hub_json", post)
    monkeypatch.setattr("main_computer.captain_cli._fetch_hub_credit_balance", lambda *_a, **_kw: {
        "ok": True, "account": {"available_credit_wei": "0"},
    })

    class Rpc:
        def __init__(self, url, *, timeout_s):
            assert url == "https://testnet-rpc.greatlibrary.io"

        def chain_id(self):
            if chain_state == "wrong-chain":
                return 42424240
            if chain_state == "down":
                raise RuntimeError("RPC offline")
            return 42424241

        def rpc(self, method, params):
            if method == "eth_getCode":
                return "0x" if chain_state == "missing-escrow" else "0x6000"
            if method == "eth_getTransactionByHash":
                return {"hash": OLD_TX} if chain_state == "pending" else None
            raise AssertionError(method)

        def eth_call(self, *, to, data):
            assert to == ESCROW and data == "0x546fcf39" + OLD_ID[2:]
            words = ("1" if chain_state == "record-exists" else "0") + "0" * 63
            return "0x" + words + "0" * (4 * 64)

        def transaction_receipt(self, hash):
            assert hash == OLD_TX
            return {"status": "0x1", "blockNumber": "0x100"} if chain_state == "receipt" else None

    monkeypatch.setattr("main_computer.captain_cli.JsonRpcClient", Rpc)
    return sent, completed


def _run(tmp_path):
    return run_captain(
        ["smoke", "o3", "What", "is", "4", "+", "9?", "--network", "testnet",
         "--no-bridge-refund", "--poll-seconds", "0", "--json"],
        config=MainComputerConfig(workspace=tmp_path), cwd=tmp_path,
    )


def test_hub_chain_drift_logs_warning_but_retains_record_and_refuses_new_payment(
    tmp_path, monkeypatch, caplog, capsys,
):
    sent, completed = _setup(tmp_path, monkeypatch)
    with pytest.raises(CaptainCliError, match="temporarily unavailable"):
        _run(tmp_path)
    assert len(sent) == 1
    pending_dir = tmp_path / "runtime/state/captain/pending-bridge-deposits"
    pending = list(pending_dir.glob("*.json"))
    assert len(pending) == 1
    original = pending[0].read_bytes()
    with caplog.at_level(logging.WARNING):
        with pytest.raises(CaptainCliError, match="refusing another payment"):
            _run(tmp_path)
    assert len(sent) == 1  # No second deposit, even when RPC says absent.
    assert completed == [OLD_ID, OLD_ID]
    assert pending[0].read_bytes() == original
    log = (pending_dir / "accounting-drift.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1
    event = json.loads(log[0])
    assert event["event"] == "LOCAL_ACCOUNTING_DRIFT"
    assert event["network"] == "testnet"
    assert event["hub"] == "unknown"
    assert event["deposit_id"] == OLD_ID
    assert event["transaction_hash"] == OLD_TX
    assert event["action"] == "retain_pending_record_and_refuse_another_payment_until_reconciled"
    assert "LOCAL_ACCOUNTING_DRIFT" in caplog.text
    assert not capsys.readouterr().out.strip()


@pytest.mark.parametrize("chain_state", [
    "record-exists", "receipt", "pending", "missing-escrow", "wrong-chain", "down",
])
def test_ambiguous_or_current_deposit_never_causes_another_payment(tmp_path, monkeypatch, chain_state):
    sent, completed = _setup(tmp_path, monkeypatch, chain_state=chain_state)
    with pytest.raises(CaptainCliError, match="temporarily unavailable"):
        _run(tmp_path)
    with pytest.raises(CaptainCliError, match="refusing another payment"):
        _run(tmp_path)
    assert len(sent) == 1
    assert completed == [OLD_ID, OLD_ID]
    pending_dir = tmp_path / "runtime/state/captain/pending-bridge-deposits"
    assert len(list(pending_dir.glob("*.json"))) == 1
    assert not (pending_dir / "accounting-drift.jsonl").exists()


def test_unknown_other_deposit_is_not_drift_authority(tmp_path, monkeypatch):
    sent, completed = _setup(
        tmp_path, monkeypatch, hub_error="Unknown bridge deposit: " + "0x" + "ff" * 32,
    )
    with pytest.raises(CaptainCliError, match="temporarily unavailable"):
        _run(tmp_path)
    with pytest.raises(CaptainCliError, match="Unknown bridge deposit"):
        _run(tmp_path)
    assert len(sent) == 1 and completed == [OLD_ID, OLD_ID]


def test_new_smoke_id_cannot_evade_same_wallet_pending_record(tmp_path, monkeypatch):
    sent, completed = _setup(tmp_path, monkeypatch)
    with pytest.raises(CaptainCliError, match="temporarily unavailable"):
        _run(tmp_path)
    assert len(sent) == 1
    # Same wallet, new smoke ID: the old deposit must block new funding even
    # though the derived recovery filename is different.
    with pytest.raises(CaptainCliError, match="another smoke ID; refusing another payment"):
        run_captain(
            ["smoke", "o3", "New", "smoke", "request", "--network", "testnet",
             "--smoke-id", "completely-new-browser-run", "--no-bridge-refund",
             "--poll-seconds", "0", "--json"],
            config=MainComputerConfig(workspace=tmp_path), cwd=tmp_path,
        )
    assert len(sent) == 1
    assert completed == [OLD_ID]
    assert len(list((tmp_path / "runtime/state/captain/pending-bridge-deposits").glob('*.json'))) == 1


def test_pending_records_for_different_wallet_are_not_confused(tmp_path):
    from main_computer.captain_cli import _require_no_other_captain_pending_deposits
    folder = tmp_path / 'pending-bridge-deposits'
    folder.mkdir()
    (folder / 'old.json').write_text(json.dumps({
        'wallet_address': '0x' + '1' * 40, 'deposit_id': OLD_ID,
        'transaction_hash': OLD_TX,
    }), encoding='utf-8')
    _require_no_other_captain_pending_deposits(
        folder / 'new.json', wallet_address='0x' + '2' * 40,
    )


def test_corrupt_pending_record_blocks_new_payment(tmp_path):
    from main_computer.captain_cli import _require_no_other_captain_pending_deposits
    folder = tmp_path / 'pending-bridge-deposits'
    folder.mkdir()
    (folder / 'corrupt.json').write_text('not-json', encoding='utf-8')
    with pytest.raises(CaptainCliError, match='unreadable pending Captain bridge record'):
        _require_no_other_captain_pending_deposits(
            folder / 'new.json', wallet_address='0x' + '2' * 40,
        )
