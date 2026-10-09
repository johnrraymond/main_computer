"""Fifteen genesis funded Hub admins, counting already-assigned and reserved identities."""
from __future__ import annotations

from copy import deepcopy

import pytest

from tools.mother.common.ethereum_identity import private_key_to_address
from tools.mother.common.hub_admin_pool import (
    POOL_SIZE, HubAdminPoolError, complete_pool, genesis_addresses, inspect_pool,
)
from tools.mother.common.deployment_node_identity_reservation import (
    MotherDeploymentNodeIdentityReservationError, _add_missing_identity,
)
from tools.genesis_native_reserve import MAX_UINT256, reserve_allocation
from tests.test_smoke_besu_qbft_native_mint import fake_artifact


def _key(i: int) -> str:
    return f"0x{i:064x}"


def _identity(i: int) -> dict[str, str]:
    return {"address": private_key_to_address(_key(i)), "private_key": _key(i)}


def _state(assigned: int = 3, reserved: int = 1) -> dict:
    state = {
        "wallets": {"hub_admin_reserve": {
            f"reserve{i:02d}": _identity(50 + i)
            for i in range(1, reserved + 1)
        }},
        "node_seed_material": {
            f"hub{i}": {"wallets": {"hub_admin": _identity(20 + i)}}
            for i in range(1, assigned + 1)
        },
        "validators": {}, "nodes": {},
        "deployment": {"targets": {}},
        "coolify": {"controllers": {"coolify-a": {"enabled": True}}},
    }
    return state


def test_three_assigned_one_reserved_generates_only_eleven_and_remains_stable() -> None:
    state = _state()
    old = deepcopy(state)
    counter = iter(range(101, 112))
    view = inspect_pool(state)
    assert len(view["addresses"]) == 4
    assert view["new_reservations_needed"] == 11
    labels = complete_pool(state, generated_at="2026-10-08T00:00:00Z", key_factory=lambda: _key(next(counter)))
    assert len(labels) == 11
    assert len(genesis_addresses(state)) == 15
    assert len(state["wallets"]["hub_admin_reserve"]) == 12
    for name in old["node_seed_material"]:
        assert state["node_seed_material"][name] == old["node_seed_material"][name]
    assert state["wallets"]["hub_admin_reserve"]["reserve01"] == old["wallets"]["hub_admin_reserve"]["reserve01"]
    frozen = deepcopy(state)
    assert complete_pool(state, generated_at="2026-10-09T00:00:00Z") == ()
    assert state == frozen


def test_existing_assigned_alias_counts_once_and_cannot_be_double_assigned() -> None:
    state = _state()
    existing = state["node_seed_material"]["hub1"]["wallets"]["hub_admin"]
    state["wallets"]["hub_admin_reserve"]["alias"] = deepcopy(existing)
    assert len(inspect_pool(state)["addresses"]) == 4
    state["node_seed_material"]["hub2"]["wallets"]["hub_admin"] = deepcopy(existing)
    with pytest.raises(HubAdminPoolError, match="share one assigned address"):
        inspect_pool(state)


def test_more_than_fifteen_fails_without_changing_state() -> None:
    state = _state(assigned=15, reserved=1)
    before = deepcopy(state)
    with pytest.raises(HubAdminPoolError, match="exceeds 15"):
        complete_pool(state, generated_at="2026-10-08T00:00:00Z")
    assert state == before


def test_assigned_address_without_private_key_refused() -> None:
    state = _state()
    del state["node_seed_material"]["hub1"]["wallets"]["hub_admin"]["private_key"]
    with pytest.raises(HubAdminPoolError, match="original valid private key"):
        inspect_pool(state)


def test_genesis_funds_all_fifteen_plus_five_and_reserve_gets_remainder() -> None:
    state = _state()
    ids = iter(range(101, 112))
    complete_pool(state, generated_at="2026-10-08T00:00:00Z", key_factory=lambda: _key(next(ids)))
    hub_addresses = genesis_addresses(state)
    office = [_identity(i)["address"] for i in range(1, 5)]
    deployer = _identity(5)["address"]
    alloc, profile = reserve_allocation(
        artifact=fake_artifact(),
        captain=office[0], first_officer=office[1],
        second_officer=office[2], third_officer=office[3],
        deployer=deployer, initial_captain_wei=10000 * 10**18,
        initial_deployer_wei=10000 * 10**18,
        max_payout_wei=MAX_UINT256,
        hub_admin_addresses=hub_addresses,
    )
    assert profile["hub_admin_count"] == POOL_SIZE
    assert len(alloc) == 21
    assert all(int(alloc[addr[2:]]["balance"], 16) == 10000 * 10**18 for addr in hub_addresses)
    assert int(alloc[profile["contract"][2:]]["balance"], 16) == MAX_UINT256 - 200000 * 10**18
    assert sum(int(entry["balance"], 16) for entry in alloc.values()) == MAX_UINT256


def test_add_node_claims_an_unassigned_genesis_funded_admin() -> None:
    state = _state()
    ids = iter(range(101, 112))
    complete_pool(state, generated_at="2026-10-08T00:00:00Z", key_factory=lambda: _key(next(ids)))
    expected = set(genesis_addresses(state))
    before = deepcopy(state["node_seed_material"])
    document = {"kind": "main_computer.mother.private_state.v1", "schema_version": 1,
                "networks": {"mainnet": state}}
    successor, _validator, hub = _add_missing_identity(
        document, network="mainnet", node="hub4", host="coolify-a",
        generated_at="2026-10-08T00:00:00Z", key_factory=lambda: _key(1000),
    )
    after = successor["networks"]["mainnet"]
    assert hub.lower() in expected
    assert len(genesis_addresses(after)) == 15
    assert before.items() <= after["node_seed_material"].items()
    assert after["deployment"]["targets"]["hub4"]["hub_admin_address"] == hub
    assert hub.lower() in {item.lower() for item in genesis_addresses(after)}
    assert len(after["wallets"]["hub_admin_reserve"]) == 11


def test_pool_exhaustion_does_not_generate_unfunded_new_wallet() -> None:
    state = _state(assigned=15, reserved=0)
    document = {"kind": "main_computer.mother.private_state.v1", "schema_version": 1,
                "networks": {"mainnet": state}}
    with pytest.raises(MotherDeploymentNodeIdentityReservationError, match="all 15 funded"):
        _add_missing_identity(
            document, network="mainnet", node="hub16", host="coolify-a",
            generated_at="2026-10-08T00:00:00Z", key_factory=lambda: _key(1000),
        )


def test_starter_reservation_three_assigned_one_reserved_creates_only_eleven() -> None:
    from tools.mother.common.starter_identity import reserve_starter_identity
    from tests.test_mother_identity_cli import _document

    document = _document()
    network = document["networks"]["mainnet"]
    targets = network["deployment"]["targets"]
    third_name = "mainneta-super3"
    third = deepcopy(targets["mainneta-super1"])
    third["desired_service_name"] = third_name
    third["hub_admin_private_key_path"] = (
        f"networks.mainnet.node_seed_material.{third_name}.wallets.hub_admin.private_key"
    )
    targets[third_name] = third
    second = "mainnetc-super1"
    second_key = _key(71)
    third_key = _key(72)
    network["node_seed_material"][second] = {"wallets": {"hub_admin": {
        "address": private_key_to_address(second_key), "private_key": second_key,
    }}}
    network["node_seed_material"][third_name] = {"wallets": {"hub_admin": {
        "address": private_key_to_address(third_key), "private_key": third_key,
    }}}
    targets[second]["hub_admin_address"] = private_key_to_address(second_key)
    targets[third_name]["hub_admin_address"] = private_key_to_address(third_key)
    existing_reserved = _identity(51)
    network["wallets"]["hub_admin_reserve"] = {"reserve01": existing_reserved}
    keys = iter(range(200, 240))
    result = reserve_starter_identity(
        document, generated_at="2026-10-08T00:00:00Z", key_factory=lambda: _key(next(keys)),
    )
    resolved = result.document["networks"]["mainnet"]
    assert len([x for x in result.generated_labels if x.startswith("hub-admin-reserve:")]) == 11
    assert len(genesis_addresses(resolved)) == 15
    assert resolved["wallets"]["hub_admin_reserve"]["reserve01"] == existing_reserved
    for node in ("mainneta-super1", second, third_name):
        before = network["node_seed_material"][node]["wallets"]["hub_admin"]
        after = resolved["node_seed_material"][node]["wallets"]["hub_admin"]
        assert after["private_key"] == before["private_key"]
        assert after["address"] == private_key_to_address(before["private_key"])
    assert not any(x.startswith("hub-admin:") for x in result.generated_labels)
