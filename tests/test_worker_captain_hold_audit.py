"""Read-only stable-hub hold audit tests. No network, signing or mutation."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer.hub_credit_indexer import wallet_account_id
from tools import worker_captain_hold_audit as audit

U = 10**18
W = "0x" + "a" * 40
A = wallet_account_id(W)


def account(*, available=976*U//1000, held=1024*U//1000, spent=U, completed=3*U):
    return dict(account_id=A, wallet_address=W, owner_address=W,
                available_credit_wei=str(available), held_credit_wei=str(held),
                spent_credit_wei=str(spent), bridge_completed_credit_wei=str(completed))


def fixture(*, holds=None, ac=None):
    ac = account() if ac is None else ac
    balance = dict(ok=True, account_id=A, wallet_address=W, account=ac)
    holds = [dict(hold_id="h-1", status="held", account_id=A, wallet_address=W,
                  request_id="req-1", session_id="session-1", credit_wei=str(1024*U//1000),
                  created_at="2026-10-26T14:00:00Z")] if holds is None else holds
    # Exact Stable Hub GET /api/hub/v1/payout/status response shape.
    ledger = dict(ok=True, payout=dict(ok=True,
        accounts=[dict(ac), account(held=0) | {"account_id":"someone-else", "wallet_address":"0x"+"b"*40}],
        holds=holds))
    return balance, ledger


def test_historical_held_session_is_identified_without_authorizing_refund():
    b, l = fixture()
    result = audit.reconcile_read_only(b, l, wallet_address=W)
    assert result["ok"] and result["readOnly"]
    assert result["ledgerSource"] == "/api/hub/v1/payout/status"
    assert result["activeHoldCount"] == 1
    assert result["heldCreditWei"] == str(1024*U//1000)
    assert result["unexplainedFundingDeltaWei"] == "0"
    assert result["activeHolds"][0]["requestId"] == "req-1"
    assert result["activeHolds"][0]["sessionId"] == "session-1"
    assert result["activeHolds"][0]["sessionState"] == "not_verified"
    assert result["releaseAuthorized"] is False
    assert result["workerCycleCertified"] is False


def test_ignores_other_wallet_holds_and_finalized_holds():
    b, l = fixture()
    l["payout"]["holds"].extend([
        dict(hold_id="h-2", status="held", account_id="someone-else", wallet_address="0x"+"b"*40),
        dict(hold_id="h-3", status="released", account_id=A, wallet_address=W),
        dict(hold_id="h-4", status="charged", account_id=A, wallet_address=W),
    ])
    assert audit.reconcile_read_only(b, l, wallet_address=W)["activeHoldCount"] == 1


def test_no_outstanding_holds_reports_clean_account():
    ac = account(available=2*U, held=0, spent=U)
    b, l = fixture(holds=[], ac=ac)
    report = audit.reconcile_read_only(b, l, wallet_address=W)
    assert report["activeHoldCount"] == 0
    assert report["verdict"] == "no_active_holds"


@pytest.mark.parametrize("alter", [
    lambda b, l: l["payout"]["holds"].clear(),
    lambda b, l: l["payout"]["holds"][0].update(credit_wei="100"),
    lambda b, l: l["payout"]["holds"][0].update(wallet_address="0x"+"b"*40),
    lambda b, l: l["payout"]["holds"][0].update(session_id=""),
    lambda b, l: l["payout"]["holds"][0].update(credit_wei="1.024"),
    lambda b, l: l["payout"]["accounts"][0].update(held_credit_wei="0"),
    lambda b, l: l["payout"].update(accounts=[]),
    lambda b, l: l.pop("payout"),
    lambda b, l: l["payout"].update(holds_disabled=True, holds=[]),
    lambda b, l: b["account"].update(available_credit_wei="xyz"),
    lambda b, l: b.update(account_id="wallet:another"),
    lambda b, l: l["payout"]["holds"][0].update(credit_wei="0"),
])
def test_inconsistent_or_unavailable_ledger_fails_closed(alter):
    b, l = fixture()
    alter(b, l)
    with pytest.raises(audit.HoldAuditError):
        audit.reconcile_read_only(b, l, wallet_address=W)


def test_payout_endpoint_is_used_with_live_captain_scope(monkeypatch):
    b, l = fixture()
    calls = []
    runtime = SimpleNamespace(network="testnet", config=SimpleNamespace(hub_url=audit.TESTNET_HUB),
                              chain_id=42424241, wallet=SimpleNamespace(address=W))
    monkeypatch.setattr(audit, "build_captain_options_parser", lambda: SimpleNamespace(parse_args=lambda _: SimpleNamespace()))
    monkeypatch.setattr(audit, "MainComputerConfig", SimpleNamespace(from_env=lambda: object()))
    monkeypatch.setattr(audit, "_apply_captain_network_defaults", lambda *args, **kw: None)
    monkeypatch.setattr(audit, "build_captain_runtime", lambda *args, **kw: runtime)
    monkeypatch.setattr(audit, "_fetch_hub_credit_balance", lambda *args, **kw: b)
    def fake_get(hub, path, **kw):
        calls.append((hub, path))
        return l
    result = audit.probe_live(repo_root=Path("."), hub_get=fake_get)
    assert result["activeHoldCount"] == 1
    assert calls == [(audit.TESTNET_HUB, "/api/hub/v1/payout/status")]


def test_unavailable_payout_endpoint_is_distinguished_from_captain_setup(monkeypatch):
    b, _ = fixture()
    runtime = SimpleNamespace(network="testnet", config=SimpleNamespace(hub_url=audit.TESTNET_HUB),
                              chain_id=42424241, wallet=SimpleNamespace(address=W))
    monkeypatch.setattr(audit, "build_captain_options_parser", lambda: SimpleNamespace(parse_args=lambda _: SimpleNamespace()))
    monkeypatch.setattr(audit, "MainComputerConfig", SimpleNamespace(from_env=lambda: object()))
    monkeypatch.setattr(audit, "_apply_captain_network_defaults", lambda *args, **kw: None)
    monkeypatch.setattr(audit, "build_captain_runtime", lambda *args, **kw: runtime)
    monkeypatch.setattr(audit, "_fetch_hub_credit_balance", lambda *args, **kw: b)
    with pytest.raises(audit.HoldAuditError, match="hub_payout_status_unavailable"):
        audit.probe_live(repo_root=Path("."), hub_get=lambda *args, **kw: (_ for _ in ()).throw(OSError("offline")))
