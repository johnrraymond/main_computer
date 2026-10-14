"""P2-B: Stable-parity claim selection and single-owner settlement for Experimental FDB.

The fake transaction store serializes writes but is *not* a substitute for the
separate real-FDB, multi-process stress harness.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import pytest

from test_exp_fdb_hold_lifecycle_p0 import DB, ledger

WALLET = "0x" + "ab" * 20


def earning(store, request_id, *, worker="worker-a", credits=1):
    store.issue(account_id="requester", credits=credits)
    hold = store.create_hold(account_id="requester", request_id=request_id, credits=credits)
    charge = store.charge_hold(hold_id=hold["hold"]["hold_id"], charged_credits=credits, worker_node_id=worker)
    return charge["worker_earning"]["earning_id"]


def claims(store):
    return [c for c in store.worker_claim_totals("worker-a")["claims"] if c["status"] in ("claimed", "settled")]


def test_duplicate_explicit_earning_claim_is_rejected():
    store = ledger()
    eid = earning(store, "req-dup")
    first = store.record_worker_claim(worker_node_id="worker-a", earning_ids=[eid], idempotency_key="first")
    with pytest.raises(ValueError, match="already been claimed"):
        store.record_worker_claim(worker_node_id="worker-a", earning_ids=[eid], idempotency_key="second")
    assert [c["claim_id"] for c in claims(store)] == [first["claim"]["claim_id"]]


def test_stable_claim_selection_validation_and_idempotency():
    store = ledger()
    e1 = earning(store, "req-one")
    e2 = earning(store, "req-other", worker="worker-b")
    with pytest.raises(ValueError, match="duplicates"):
        store.record_worker_claim(worker_node_id="worker-a", earning_ids=[e1, e1])
    with pytest.raises(KeyError, match="Unknown worker earning"):
        store.record_worker_claim(worker_node_id="worker-a", earning_ids=["missing"])
    with pytest.raises(ValueError, match="another worker"):
        store.record_worker_claim(worker_node_id="worker-a", earning_ids=[e2])
    with pytest.raises(ValueError, match="mismatch"):
        store.record_worker_claim(worker_node_id="worker-a", earning_ids=[e1], claim_credits=2)
    assert not claims(store)
    first = store.record_worker_claim(worker_node_id="worker-a", earning_ids=[e1], idempotency_key="once")
    again = store.record_worker_claim(worker_node_id="worker-a", earning_ids=[e1], idempotency_key="once")
    assert again["idempotent"] and again["claim"]["claim_id"] == first["claim"]["claim_id"]
    assert len(claims(store)) == 1
    empty = store.record_worker_claim(worker_node_id="worker-a", idempotency_key="claim-new")
    assert empty["claim"] is None


def test_empty_explicit_selection_does_not_claim_all():
    store = ledger()
    earning(store, "req-empty")
    result = store.record_worker_claim(worker_node_id="worker-a", earning_ids=[])
    assert result["claim"] is None
    assert len(store.worker_claim_totals("worker-a")["claimable_earning_ids"]) == 1


def test_no_partial_claim_when_one_earning_already_owned():
    store = ledger()
    e1 = earning(store, "req-claimed")
    e2 = earning(store, "req-unclaimed")
    store.record_worker_claim(worker_node_id="worker-a", earning_ids=[e1], idempotency_key="first")
    with pytest.raises(ValueError, match="already been claimed"):
        store.record_worker_claim(worker_node_id="worker-a", earning_ids=[e1,e2], idempotency_key="second")
    assert store.worker_claim_totals("worker-a")["claimable_earning_ids"] == [e2]
    assert len(claims(store)) == 1


def test_claim_and_bridge_payout_are_exclusive_both_directions():
    store = ledger()
    e1 = earning(store, "req-claim-then-bridge")
    store.record_worker_claim(worker_node_id="worker-a", earning_ids=[e1], idempotency_key="claim")
    with pytest.raises(ValueError, match="non-claimable|already"):
        store.request_bridge_payout(wallet_address=WALLET, worker_node_id="worker-a", earning_ids=[e1], idempotency_key="bridge")
    e2 = earning(store, "req-bridge-then-claim")
    bridge = store.request_bridge_payout(wallet_address=WALLET, worker_node_id="worker-a", earning_ids=[e2], idempotency_key="bridge2")
    assert bridge["payout"]["earning_ids"] == [e2]
    with pytest.raises(ValueError, match="bridge payout|already"):
        store.record_worker_claim(worker_node_id="worker-a", earning_ids=[e2], idempotency_key="claim2")
    assert store.worker_claim_totals("worker-a")["claimable_earning_ids"] == []


def test_parallel_competing_claims_across_ledger_instances():
    db = DB()
    seed = ledger(db)
    eid = earning(seed, "req-race")
    contenders = [ledger(db) for _ in range(32)]
    def attempt(i):
        try:
            return contenders[i].record_worker_claim(worker_node_id="worker-a", earning_ids=[eid], idempotency_key=f"race-{i}")
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(attempt, range(32)))
    assert sum(r is not None and not r["idempotent"] and r["claim"] is not None for r in results) == 1
    assert len(claims(seed)) == 1


def test_legacy_claim_without_ownership_key_remains_exclusive():
    store = ledger()
    eid = earning(store, "req-legacy")
    store.record_worker_claim(worker_node_id="worker-a", earning_ids=[eid], idempotency_key="legacy")
    # Simulate an older FDB schema with only the claim document, no owner key.
    store.db.values.pop(store._key("worker_earning_settlement_owner", eid))
    with pytest.raises(ValueError, match="already been claimed"):
        store.record_worker_claim(worker_node_id="worker-a", earning_ids=[eid], idempotency_key="new-key")
    with pytest.raises(ValueError, match="non-claimable"):
        store.request_bridge_payout(wallet_address=WALLET, worker_node_id="worker-a", earning_ids=[eid])


def test_legacy_pending_bridge_payout_blocks_new_claim():
    store = ledger()
    eid = earning(store, "req-legacy-bridge")
    result = store.request_bridge_payout(wallet_address=WALLET, worker_node_id="worker-a", earning_ids=[eid])
    store.db.values.pop(store._key("worker_earning_settlement_owner", eid))
    with pytest.raises(ValueError, match="bridge payout"):
        store.record_worker_claim(worker_node_id="worker-a", earning_ids=[eid], idempotency_key="new-key")
    assert result["payout"]["earning_ids"] == [eid]


def test_idempotency_key_cannot_be_reused_for_different_earnings_or_amount():
    store = ledger()
    e1 = earning(store, "req-idem-1")
    e2 = earning(store, "req-idem-2")
    store.record_worker_claim(worker_node_id="worker-a", earning_ids=[e1], idempotency_key="idem")
    with pytest.raises(ValueError, match="idempotency_key reused"):
        store.record_worker_claim(worker_node_id="worker-a", earning_ids=[e2], idempotency_key="idem")
    with pytest.raises(ValueError, match="idempotency_key reused"):
        store.record_worker_claim(worker_node_id="worker-a", earning_ids=[e1], idempotency_key="idem", claim_credits=2)


def test_claim_and_bridge_race_has_one_economic_owner():
    db = DB()
    source = ledger(db)
    eid = earning(source, "req-mixed-race")
    contenders = [ledger(db) for _ in range(32)]

    def attempt(i):
        try:
            if i % 2:
                response = contenders[i].request_bridge_payout(
                    wallet_address=WALLET, worker_node_id="worker-a", earning_ids=[eid],
                    idempotency_key=f"bridge-{i}",
                )
                return response["payout"]["payout_id"]
            response = contenders[i].record_worker_claim(
                worker_node_id="worker-a", earning_ids=[eid], idempotency_key=f"claim-{i}",
            )
            return response["claim"]["claim_id"]
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=32) as pool:
        outcomes = list(pool.map(attempt, range(32)))
    assert len([result for result in outcomes if result]) == 1
    snapshot = source._snapshot()
    claim_count = sum(eid in x.get("earning_ids", []) for x in snapshot["worker_claims"])
    payout_count = sum(eid in x.get("earning_ids", []) for x in snapshot["bridge_payouts"])
    assert claim_count + payout_count == 1
    assert source.db.values.get(source._key("worker_earning_settlement_owner", eid))


def test_failed_bridge_payout_keeps_earning_reserved_until_reconciliation():
    store = ledger()
    eid = earning(store, "req-failed-payout")
    payout = store.request_bridge_payout(wallet_address=WALLET, worker_node_id="worker-a", earning_ids=[eid])
    store.fail_bridge_payout(payout_id=payout["payout"]["payout_id"], reason="rpc-outcome-unknown")
    with pytest.raises(ValueError, match="bridge payout|already"):
        store.record_worker_claim(worker_node_id="worker-a", earning_ids=[eid], idempotency_key="unsafe-retry")
