"""A failed Hub acknowledgement must not cause a second on-chain payment."""
from __future__ import annotations

import json

import pytest

from main_computer.config import MainComputerConfig
from main_computer.captain_cli import CaptainCliError, run_captain
from test_captain_cli import _stub_verified_mother, _write_mainnet_contract_registry


def test_captain_reuses_submitted_deposit_when_hub_completion_fails(tmp_path, monkeypatch):
    _stub_verified_mother(monkeypatch)
    monkeypatch.setattr("main_computer.hub_cli_live_session.hub_uses_live_sessions", lambda *_a, **_k: False)
    escrow = "0x" + "22" * 20
    _write_mainnet_contract_registry(tmp_path, escrow)
    monkeypatch.setattr("main_computer.captain_cli._get_hub_json", lambda *_a, **_k: {
        "ok": True, "deployment": {"chain_id": 42424240, "contract_address": escrow,
                                  "bridge_controller_address": "0x" + "33" * 20},
    })
    deployment_path = tmp_path / "runtime" / "deployments" / "mainnet" / "latest.json"
    deployment_path.parent.mkdir(parents=True)
    deployment_path.write_text(json.dumps({
        "chain": {"network": "mainnet", "chain_id": 42424240, "rpc_url": "https://rpc.example"},
        "contracts": {
            "xlag-bridge-reserve": {"address": "0x" + "11"*20},
            "hub_credit_bridge_escrow": {
                "address": escrow, "bridge_controller_address": "0x" + "33"*20,
            },
        },
        "offices": [{"office": "O0", "title": "Captain", "address": "0xE81Bc6ef15b991F1083db9c1a0899Ef9AE00516F"}],
    }), encoding="utf-8")
    sent = []
    completions = []

    def broadcast(*_args, **_kwargs):
        sent.append(True)
        return {
            "deposit_id": "0x" + "aa" * 32,
            "transaction_hash": "0x" + "bb" * 32,
            "amount_credit_wei": str(10**18),
            "receipt": {"blockNumber": "0x10", "logs": []},
        }

    def post(_hub, path, payload, *, timeout_s):
        if path == "/api/hub/v1/requests/quote":
            return {"ok": True}
        if path == "/api/hub/v1/credits/wallet-funding/complete":
            completions.append(payload)
            if len(completions) == 1:
                raise CaptainCliError("Hub completion temporarily unavailable")
            return {"ok": True, "delta_credit_wei": str(10**18)}
        if path == "/api/hub/v1/requests":
            return {"ok": True}
        raise AssertionError(path)

    monkeypatch.setattr("main_computer.captain_cli.send_captain_bridge_deposit", broadcast)
    monkeypatch.setattr("main_computer.captain_cli._post_hub_json", post)
    monkeypatch.setattr("main_computer.captain_cli._fetch_hub_credit_balance", lambda *_a, **_k: {
        "ok": True, "account": {"available_credit_wei": str(10**18)},
    })
    args = ["smoke", "captain", "Report", "ready", "--no-chain", "--poll-seconds", "0", "--no-bridge-refund"]
    config = MainComputerConfig(workspace=tmp_path)
    with pytest.raises(CaptainCliError, match="temporarily unavailable"):
        run_captain(args, config=config, cwd=tmp_path)
    pending = list((tmp_path / "runtime" / "state" / "captain" / "pending-bridge-deposits").glob("*.json"))
    assert len(pending) == 1
    assert len(sent) == 1
    assert run_captain(args, config=config, cwd=tmp_path) == 0
    assert len(sent) == 1
    assert len(completions) == 2
    assert completions[0]["deposit_id"] == completions[1]["deposit_id"]
    assert completions[0]["tx_hash"] == completions[1]["tx_hash"]
    assert not pending[0].exists()
