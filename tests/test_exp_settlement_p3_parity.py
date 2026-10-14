"""P3 settlement parity: Stable lifecycle oracle, Experimental FDB economic safety.

The FDB transaction fake checks record-level transitions. A live FoundationDB
multi-process run and blockchain reconciliation remain separate acceptance gates.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import (
    InMemoryStableWorkerSessionStore, StableHubPayoutLedgerDirectory,
)
from test_exp_fdb_hold_lifecycle_p0 import DB, ledger

WALLET = "0x" + "ab" * 20
WORKER = "worker-parity"


def exp_earned(store, suffix="one"):
    store.issue(account_id="requester", credits=2)
    hold = store.create_hold(account_id="requester", request_id=f"req-p3-{suffix}", credits=1)
    result = store.charge_hold(
        hold_id=hold["hold"]["hold_id"], charged_credits=1, worker_node_id=WORKER,
    )
    return result["worker_earning"]["earning_id"]


def settled_exp_batch(store, suffix="one", *, payout_rail="hub-internal-bridge-preparation", tx_hash=""):
    eid = exp_earned(store, suffix)
    claim = store.record_worker_claim(worker_node_id=WORKER, earning_ids=[eid], idempotency_key=f"c-{suffix}")["claim"]
    batch = store.create_worker_settlement_batch(
        worker_node_id=WORKER, claim_ids=[claim["claim_id"]], idempotency_key=f"b-{suffix}",
    )["batch"]
    settled = store.settle_worker_settlement_batch(
        batch_id=batch["batch_id"], settlement_reference=f"prepared-{suffix}",
        payout_rail=payout_rail, settlement_tx_hash=tx_hash, idempotency_key=f"s-{suffix}",
    )
    assert settled["batch"]["status"] == "settled"
    return eid, claim, settled["batch"]


def payout(store, batch, *, key="p3-payout", wallet=WALLET):
    return store.request_bridge_payout(
        wallet_address=wallet, worker_node_id=WORKER,
        batch_id=batch["batch_id"], idempotency_key=key,
    )


def test_batch_backed_payout_is_one_to_one_idempotent_and_wallet_is_credited_once():
    store = ledger()
    eid, claim, batch = settled_exp_batch(store)
    first = payout(store, batch)
    assert not first["idempotent"]
    p = first["payout"]
    assert p["batch_id"] == batch["batch_id"]
    assert p["earning_ids"] == [eid]
    assert p["credit_wei"] == str(batch["total_credit_wei_published"])
    assert p["metadata"]["dust_credit_wei"] == str(batch["dust_credit_wei"])
    assert payout(store, batch)["payout"]["payout_id"] == p["payout_id"]
    assert payout(store, batch)["idempotent"]
    with pytest.raises(ValueError, match="conflicting request"):
        payout(store, batch, key="second-key")
    with pytest.raises(ValueError, match="conflicting request"):
        payout(store, batch, wallet="0x" + "cc" * 20)
    confirmed = store.confirm_bridge_payout(
        payout_id=p["payout_id"], metadata={"settlement_reference": "receipt-1", "settlement_tx_hash": "0xabc"},
    )
    assert confirmed["payout"]["status"] == "confirmed"
    assert store.confirm_bridge_payout(payout_id=p["payout_id"], metadata={"settlement_reference": "receipt-1"})["idempotent"]
    with pytest.raises(ValueError, match="bridge_confirmation_settlement_reference_conflict"):
        store.confirm_bridge_payout(payout_id=p["payout_id"], metadata={"settlement_reference": "receipt-2"})
    with pytest.raises(ValueError, match="bridge_confirmation_settlement_tx_hash_conflict"):
        store.confirm_bridge_payout(payout_id=p["payout_id"], metadata={"settlement_tx_hash": "0xdef"})
    store.fail_bridge_payout(payout_id=p["payout_id"], reason="late callback")
    wallet = next(item for item in store._snapshot()["mock_chain_wallets"] if item["wallet_address"] == WALLET)
    assert int(wallet["available_credit_wei"]) == int(batch["total_credit_wei_published"])
    assert sum(x.get("payout_origin") == "settled-batch" for x in store._snapshot()["bridge_payouts"]) == 1


def test_exp_external_chain_receipt_never_initiates_second_bridge_payment():
    store = ledger()
    _, _, external = settled_exp_batch(store, payout_rail="xlag-bridge-reserve", tx_hash="0xabc")
    with pytest.raises(ValueError, match="external payment evidence"):
        payout(store, external)
    assert store._snapshot()["bridge_payouts"] == []


def test_batch_must_belong_to_worker_be_settled_and_not_reuse_key():
    store = ledger()
    _, _, batch = settled_exp_batch(store, "first")
    first = payout(store, batch)
    _, _, batch2 = settled_exp_batch(store, "second")
    with pytest.raises(ValueError, match="idempotency_key reused"):
        payout(store, batch2)
    assert len(store._snapshot()["bridge_payouts"]) == 1
    with pytest.raises(ValueError, match="another worker"):
        store.request_bridge_payout(wallet_address=WALLET, worker_node_id="other-worker", batch_id=batch2["batch_id"])
    with pytest.raises(ValueError, match="without account_id"):
        store.request_bridge_payout(wallet_address=WALLET, worker_node_id=WORKER, batch_id=batch2["batch_id"], credits=1)
    with pytest.raises(ValueError, match="without account_id"):
        store.request_bridge_payout(wallet_address=WALLET, worker_node_id=WORKER, batch_id=batch2["batch_id"], earning_ids=["eid"])
    assert first["payout"]["payout_id"]


def test_competing_batch_payout_creators_get_one_record():
    db = DB()
    seed = ledger(db)
    _, _, batch = settled_exp_batch(seed)
    contenders = [ledger(db) for _ in range(24)]
    def create(index):
        try:
            return payout(contenders[index], batch, key=f"competing-{index}")
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=24) as pool:
        outcomes = list(pool.map(create, range(24)))
    assert sum(r is not None for r in outcomes) == 1
    assert len(seed._snapshot()["bridge_payouts"]) == 1
    owner = seed.db.values.get(seed._key("worker_settlement_bridge_owner", batch["batch_id"]))
    assert owner is not None


def test_legacy_direct_payout_reuse_of_key_with_different_earnings_is_rejected():
    store = ledger()
    e1 = exp_earned(store, "legacy1")
    e2 = exp_earned(store, "legacy2")
    first = store.request_bridge_payout(wallet_address=WALLET, worker_node_id=WORKER, earning_ids=[e1], idempotency_key="same")
    assert store.request_bridge_payout(wallet_address=WALLET, worker_node_id=WORKER, earning_ids=[e1], idempotency_key="same")["idempotent"]
    with pytest.raises(ValueError, match="idempotency_key reused"):
        store.request_bridge_payout(wallet_address=WALLET, worker_node_id=WORKER, earning_ids=[e2], idempotency_key="same")
    assert len(store._snapshot()["bridge_payouts"]) == 1
    assert first["payout"]["payout_origin"] == "legacy-direct"


def test_settlement_replay_rejects_conflicting_reference_key_and_rail():
    store = ledger()
    _, _, batch = settled_exp_batch(store)
    assert store.settle_worker_settlement_batch(
        batch_id=batch["batch_id"], settlement_reference="prepared-one",
        idempotency_key="s-one", payout_rail="hub-internal-bridge-preparation",
    )["idempotent"]
    for overrides, match in [
        ({"settlement_reference": "another"}, "different reference"),
        ({"idempotency_key": "another"}, "settlement_idempotency_key_conflict"),
        ({"payout_rail": "other"}, "settlement_payout_rail_conflict"),
        ({"settlement_tx_hash": "0xabc"}, "different tx hash"),
    ]:
        with pytest.raises(ValueError, match=match):
            store.settle_worker_settlement_batch(batch_id=batch["batch_id"], **overrides)



def test_unconfirmed_batch_payout_remains_reserved_after_failed_or_unknown_outcome():
    store = ledger()
    _, _, batch = settled_exp_batch(store)
    original = payout(store, batch)["payout"]
    failed = store.fail_bridge_payout(payout_id=original["payout_id"], reason="rpc-timeout")
    assert failed["payout"]["status"] == "failed"
    retry = payout(store, batch)
    assert retry["idempotent"]
    assert retry["payout"]["payout_id"] == original["payout_id"]
    assert retry["payout"]["status"] == "failed"
    with pytest.raises(ValueError, match="Cannot confirm"):
        store.confirm_bridge_payout(payout_id=original["payout_id"])
    assert len(store._snapshot()["bridge_payouts"]) == 1


def test_account_payout_cannot_spend_active_hold_or_erase_reservations():
    store = ledger()
    store.issue(account_id="account-with-hold", owner_address=WALLET, credits=2)
    hold = store.create_hold(account_id="account-with-hold", request_id="req-p3-held", credits=1)
    assert store.get_account("account-with-hold").held_credits == 1
    with pytest.raises(ValueError, match="Insufficient Hub bridged credits"):
        store.request_bridge_payout(wallet_address=WALLET, account_id="account-with-hold", credits=2)
    payout_record = store.request_bridge_payout(wallet_address=WALLET, account_id="account-with-hold", credits=1)
    assert payout_record["payout"]["credit_wei"] == str(10**18)
    account = store.get_account("account-with-hold")
    assert account.held_credits == 1
    assert account.available_credits == 0
    # A previously accepted work reservation is still chargeable.
    charged = store.charge_hold(hold_id=hold["hold"]["hold_id"], charged_credits=1, worker_node_id=WORKER)
    assert charged["charge"]


def test_stable_and_exp_worker_batch_bridge_economic_invariants():
    # Use Stable as the reference for the claimed -> settled -> bridge -> paid ordering.
    stable = StableHubPayoutLedgerDirectory(
        topology=load_stable_hub_topology(Path("deploy/hub-topology/dev-topology.json")),
        hub_id="dev-hub3", store=InMemoryStableWorkerSessionStore(),
    )
    stable.fund_account(account_id="acct-p3", wallet_address=WALLET, credits="1", replace=True)
    hold = stable.create_hold(
        account_id="acct-p3", wallet_address=WALLET, request_id="req-p3-stable",
        session_id="sess_p3_stable", run_id="run_p3_stable", worker_id=WORKER,
        selected_price={"amount": "1", "unit": "credit"},
        requester_max_price={"amount": "1", "unit": "credit"}, partition="ring-1",
    )
    charge = stable.charge_hold(
        hold_id=hold["hold_id"], session_id="sess_p3_stable", request_id="req-p3-stable",
        worker_id=WORKER, result={"ok": True},
    )
    sc = stable.record_worker_claim(worker_id=WORKER, earning_ids=[charge["worker_earning"]["earning_id"]])
    sb = stable.create_worker_settlement_batch(worker_id=WORKER, claim_ids=[sc["claim_id"]])
    stable.settle_worker_settlement_batch(batch_id=sb["batch_id"], settlement_reference="prepared")
    sp = stable.request_bridge_payout(worker_id=WORKER, batch_id=sb["batch_id"], idempotency_key="bridge")
    stable.confirm_bridge_payout(bridge_payout_id=sp["payout_id"], settlement_reference="receipt")

    exp = ledger()
    eid, claim, batch = settled_exp_batch(exp)
    ep = payout(exp, batch)
    exp.confirm_bridge_payout(payout_id=ep["payout"]["payout_id"], metadata={"settlement_reference": "receipt"})
    assert sc["status"] == claim["status"] == "claimed"
    assert sb["status"] == "batched"  # Stable names the pre-settlement batch state batched.
    assert batch["status"] == "settled"
    assert sp["credit_wei"] == ep["payout"]["credit_wei"]
    assert stable.status()["bridge_payouts"][0]["status"] == exp.bridge_payout_status(ep["payout"]["payout_id"])["payout"]["status"] == "confirmed"
    assert len(stable.status()["bridge_payouts"]) == len(exp._snapshot()["bridge_payouts"]) == 1


def test_inherited_hub_payout_endpoint_accepts_batch_id():
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from types import SimpleNamespace
    from urllib.request import Request, urlopen

    from main_computer.hub import HubServerHandler

    store = ledger()
    _, _, batch = settled_exp_batch(store)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HubServerHandler)
    server.credit_ledger = store
    server.registry = SimpleNamespace(status=lambda: {"workers": []})
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/hub/v1/bridge/payouts"
        body = {"wallet_address": WALLET, "worker_node_id": WORKER,
                "batch_id": batch["batch_id"], "idempotency_key": "http-idem"}
        def submit():
            request = Request(url, data=json.dumps(body).encode(), method="POST",
                              headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=3) as response:
                assert response.status == 200
                return json.load(response)
        created = submit()
        repeated = submit()
        assert created["ok"]
        assert created["payout"]["batch_id"] == batch["batch_id"]
        assert repeated["idempotent"]
        assert repeated["payout"]["payout_id"] == created["payout"]["payout_id"]
        assert len(store._snapshot()["bridge_payouts"]) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_added_external_execution_proof_fences_pending_batch_confirmation():
    store = ledger()
    _, _, batch = settled_exp_batch(store)
    created = payout(store, batch)["payout"]

    @store.fdb.transactional
    def mutate(tr):
        payload = store._read_dict(tr, "worker_settlement_batch", batch["batch_id"])
        payload["settlement_tx_hash"] = "0x" + "12" * 32
        store._write_dict(tr, "worker_settlement_batch", batch["batch_id"], payload)

    mutate(store.db)
    with pytest.raises(ValueError, match="reconciliation required"):
        store.confirm_bridge_payout(payout_id=created["payout_id"])
    assert store.bridge_payout_status(created["payout_id"])["payout"]["status"] == "pending"
    assert store._snapshot()["mock_chain_wallets"] == []
