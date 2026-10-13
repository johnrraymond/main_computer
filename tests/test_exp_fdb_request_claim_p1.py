"""P1 FDB request claim cannot be overwritten by a competing retry."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from main_computer.exp_fdb_hub_state import ExperimentalFoundationDbRequestStateStore
from main_computer.hub_plex_models import HubRequestRecord
from test_exp_fdb_hold_lifecycle_p0 import DB, FakeFDB


def test_atomic_exp_fdb_claim_preserves_original_record_and_idempotency_index():
    def pack(*parts):
        return ("p1/" + "/".join(map(str, parts))).encode()
    def range_for(*parts):
        prefix = pack(*parts) + b"/"
        return slice(prefix, prefix + b"\xff")
    state = SimpleNamespace(pack=pack, range_for=range_for)
    store = object.__new__(ExperimentalFoundationDbRequestStateStore)
    store.state = state
    store.db = DB()
    store.fdb = FakeFDB()
    original = HubRequestRecord(
        request_id="req_p1_same", client_node_id="requester-1", account_id="wallet-one",
        idempotency_key="retry-identity", model="worker", state="dispatching",
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: store.claim_live_request(original), range(32)))
    assert sum(bool(claimed) for _, claimed in outcomes) == 1
    assert store.get("req_p1_same").account_id == "wallet-one"
    assert store.find_by_idempotency_key(client_node_id="requester-1", idempotency_key="retry-identity") is not None
    # A malicious or mistaken retry cannot rewrite accounting or identity.
    forged = HubRequestRecord(
        request_id="req_p1_same", client_node_id="requester-1", account_id="wallet-two",
        idempotency_key="retry-identity", model="worker", state="dispatching",
    )
    existing, claimed = store.claim_live_request(forged)
    assert claimed is False
    assert existing.account_id == "wallet-one"
    assert store.get("req_p1_same").account_id == "wallet-one"
