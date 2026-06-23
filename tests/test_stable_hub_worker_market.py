from __future__ import annotations

from pathlib import Path

import pytest

from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import (
    InMemoryStableWorkerSessionStore,
    StableHubWorkerMarketDirectory,
    StableHubWorkerSessionError,
    normalize_request_market_constraints,
    normalize_worker_market_profile,
    stable_partition_key_for_work,
)


DEV_TOPOLOGY = Path("deploy/stable-hub-lab/dev-topology.json")


def _owner(*, worker_id: str, connection_id: str, hub_id: str = "dev-hub3") -> dict:
    return {
        "worker_id": worker_id,
        "status": "live",
        "owner_hub_id": hub_id,
        "owner_hub_url": "http://127.0.0.1:8873" if hub_id == "dev-hub3" else "http://127.0.0.1:8871",
        "connection_id": connection_id,
        "lease_epoch": 1,
        "connected_at": "2026-06-23T00:00:00+00:00",
    }


def test_worker_market_profile_normalizes_ring_price_capabilities_and_capacity() -> None:
    profile = normalize_worker_market_profile(
        {
            "ring": "ring-2",
            "price": {"amount": "0.0500", "unit": "CREDIT"},
            "capabilities": ["python", "python", "shell"],
            "max_concurrency": 3,
        }
    )

    assert profile == {
        "rings": ["ring-2"],
        "partitions": ["ring-2"],
        "capabilities": ["python", "shell"],
        "price": {"amount": "0.05", "unit": "credit"},
        "max_concurrency": 3,
        "active_sessions": 0,
    }


def test_request_market_constraints_derive_stable_partition_from_ring() -> None:
    constraints = normalize_request_market_constraints(
        {
            "ring": "ring-2",
            "max_price": {"amount": "0.10", "unit": "credit"},
            "capabilities": ["python"],
        }
    )

    assert constraints["partition"] == "ring-2"
    assert constraints["ring"] == "ring-2"
    assert constraints["max_price"] == {"amount": "0.1", "unit": "credit"}
    assert constraints["capabilities"] == ["python"]
    assert stable_partition_key_for_work({"ring": "ring-2"}) == "ring-2"


def test_worker_market_rejects_bad_ring_names_and_bad_capacity() -> None:
    with pytest.raises(StableHubWorkerSessionError, match="worker market ring"):
        normalize_worker_market_profile({"ring": "bad ring"})

    with pytest.raises(StableHubWorkerSessionError, match="max_concurrency"):
        normalize_worker_market_profile({"ring": "ring-2", "max_concurrency": 0})


def test_market_directory_selects_cheapest_live_worker_for_ring_price_and_capability() -> None:
    topology = load_stable_hub_topology(DEV_TOPOLOGY)
    store = InMemoryStableWorkerSessionStore()
    owner_hub_market = StableHubWorkerMarketDirectory(topology=topology, hub_id="dev-hub3", store=store)
    entry_hub_market = StableHubWorkerMarketDirectory(topology=topology, hub_id="dev-hub1", store=store)

    expensive = owner_hub_market.record_worker_live(
        worker_id="worker-expensive",
        owner=_owner(worker_id="worker-expensive", connection_id="conn_expensive"),
        market_profile={
            "rings": ["ring-2"],
            "price": {"amount": "0.08", "unit": "credit"},
            "capabilities": ["python", "shell"],
            "max_concurrency": 1,
        },
        worker_msk_id="msk_worker_expensive",
        worker_wallet_address="0x" + "12" * 20,
        worker_account_id="acct_expensive",
    )
    cheap = owner_hub_market.record_worker_live(
        worker_id="worker-cheap",
        owner=_owner(worker_id="worker-cheap", connection_id="conn_cheap"),
        market_profile={
            "rings": ["ring-2"],
            "price": {"amount": "0.05", "unit": "credit"},
            "capabilities": ["python"],
            "max_concurrency": 1,
        },
        worker_msk_id="msk_worker_cheap",
        worker_wallet_address="0x" + "34" * 20,
        worker_account_id="acct_cheap",
    )
    owner_hub_market.record_worker_live(
        worker_id="worker-other-ring",
        owner=_owner(worker_id="worker-other-ring", connection_id="conn_other_ring"),
        market_profile={
            "rings": ["ring-3"],
            "price": {"amount": "0.01", "unit": "credit"},
            "capabilities": ["python"],
            "max_concurrency": 1,
        },
        worker_msk_id="msk_worker_other_ring",
        worker_wallet_address="0x" + "56" * 20,
        worker_account_id="acct_other_ring",
    )

    selected = entry_hub_market.select_worker_for_work(
        {
            "ring": "ring-2",
            "max_price": {"amount": "0.10", "unit": "credit"},
            "capabilities": ["python"],
        }
    )

    assert expensive["owner_hub_id"] == "dev-hub3"
    assert cheap["owner_hub_id"] == "dev-hub3"
    assert selected is not None
    assert selected["worker_id"] == "worker-cheap"
    assert selected["owner_hub_id"] == "dev-hub3"
    assert selected["owner_hub_url"] == "http://127.0.0.1:8873"
    assert selected["partition"] == "ring-2"
    assert selected["selection"]["mode"] == "deterministic-price-worker-id"


def test_market_directory_excludes_closed_over_budget_missing_capability_and_full_workers() -> None:
    topology = load_stable_hub_topology(DEV_TOPOLOGY)
    store = InMemoryStableWorkerSessionStore()
    market = StableHubWorkerMarketDirectory(topology=topology, hub_id="dev-hub3", store=store)

    market.record_worker_live(
        worker_id="worker-over-budget",
        owner=_owner(worker_id="worker-over-budget", connection_id="conn_over_budget"),
        market_profile={
            "rings": ["ring-2"],
            "price": {"amount": "0.50", "unit": "credit"},
            "capabilities": ["python"],
            "max_concurrency": 1,
        },
        worker_msk_id="msk_over_budget",
        worker_wallet_address="0x" + "78" * 20,
        worker_account_id="acct_over_budget",
    )
    market.record_worker_live(
        worker_id="worker-missing-capability",
        owner=_owner(worker_id="worker-missing-capability", connection_id="conn_missing_capability"),
        market_profile={
            "rings": ["ring-2"],
            "price": {"amount": "0.01", "unit": "credit"},
            "capabilities": ["shell"],
            "max_concurrency": 1,
        },
        worker_msk_id="msk_missing_capability",
        worker_wallet_address="0x" + "9a" * 20,
        worker_account_id="acct_missing_capability",
    )
    market.record_worker_live(
        worker_id="worker-full",
        owner=_owner(worker_id="worker-full", connection_id="conn_full"),
        market_profile={
            "rings": ["ring-2"],
            "price": {"amount": "0.01", "unit": "credit"},
            "capabilities": ["python"],
            "max_concurrency": 1,
            "active_sessions": 1,
        },
        worker_msk_id="msk_full",
        worker_wallet_address="0x" + "bc" * 20,
        worker_account_id="acct_full",
    )
    market.record_worker_live(
        worker_id="worker-closed",
        owner=_owner(worker_id="worker-closed", connection_id="conn_closed"),
        market_profile={
            "rings": ["ring-2"],
            "price": {"amount": "0.01", "unit": "credit"},
            "capabilities": ["python"],
            "max_concurrency": 1,
        },
        worker_msk_id="msk_closed",
        worker_wallet_address="0x" + "de" * 20,
        worker_account_id="acct_closed",
    )
    market.record_worker_closed(worker_id="worker-closed", connection_id="conn_closed")

    selected = market.select_worker_for_work(
        {
            "ring": "ring-2",
            "max_price": {"amount": "0.10", "unit": "credit"},
            "capabilities": ["python"],
        }
    )

    assert selected is None
