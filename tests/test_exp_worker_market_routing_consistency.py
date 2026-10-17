"""Experimental requester must ignore stale shared market workers."""
from pathlib import Path
from types import SimpleNamespace

from main_computer.exp_fdb_hub import ExperimentalFoundationDbHubServerHandler
from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import (
    InMemoryStableWorkerSessionStore,
    StableHubWorkerMarketDirectory,
    StableHubWorkerSessionDirectory,
)


def test_exp_selection_skips_closed_owner_with_stale_cheaper_market_record() -> None:
    topology = load_stable_hub_topology(Path("deploy/hub-topology/dev-topology.json"))
    store = InMemoryStableWorkerSessionStore()
    owners = StableHubWorkerSessionDirectory(topology=topology, hub_id="dev-hub3", store=store)
    market = StableHubWorkerMarketDirectory(topology=topology, hub_id="dev-hub3", store=store)
    for worker_id, price in (("conn_exp_stale", "0.01"), ("conn_exp_valid", "0.02")):
        owner = owners.record_connected(
            worker_id=worker_id, connection_id=worker_id,
            multisession_key_id="msk", wallet_address="0x" + "12" * 20,
            account_id="acct",
        )
        market.record_worker_live(
            worker_id=worker_id, owner=owner,
            market_profile={"ring": "ring-2", "price": {"amount": price, "unit": "credit"}},
            worker_msk_id="msk", worker_wallet_address="0x" + "12" * 20,
            worker_account_id="acct",
        )
    owners.record_closed(worker_id="conn_exp_stale", connection_id="conn_exp_stale")
    assert market.get_worker("conn_exp_stale")["status"] == "live"
    fake = SimpleNamespace(server=SimpleNamespace(
        stable_worker_market_directory=market,
        stable_worker_session_directory=owners,
        stable_topology=topology,
    ))
    selected = ExperimentalFoundationDbHubServerHandler._exp_select_worker_for_work(
        fake, {"ring": "ring-2"},
    )
    assert selected is not None
    assert selected["worker_id"] == "conn_exp_valid"
