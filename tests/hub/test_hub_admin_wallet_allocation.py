"""Hub wallet allocation across the real verified Mother state boundary."""
from __future__ import annotations

import base64
from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from main_computer.hub_admin_runtime import (
    HUB_ADMIN_BUNDLE_ENV, HUB_ADMIN_BUNDLE_SCHEMA, install_hub_admin_bundle,
)
from tools.hub_control import add_hub
from tools.hub_control.common.admin_identity import preview_admin, reserve_admin
from tools.hub_control.common.errors import HubControlError
from tools.hub_control.common.models import HubContext
from tools.hub_control.common.state import ADD_OPERATION_SCHEMA, require_operation, write_operation
from tools.mother.common.ethereum_identity import private_key_to_address
from tools.mother.common.hub_admin_pool import (
    HubAdminPoolError, available_hub_admin, claim_hub_admin, genesis_addresses,
    inspect_pool,
)
from tools.mother.common.models import OperationIdentity
from tools.mother.common.private_state import (
    prepare_private_state_bootstrap, install_verified_private_state, read_private_state,
)
from tools.hub_control.common.admin_identity import _paths


def _key(i: int) -> str:
    return f"0x{i:064x}"


def _wallet(i: int) -> dict[str, str]:
    return {"address": private_key_to_address(_key(i)), "private_key": _key(i)}


def _network(n: int = 15) -> dict:
    return {
        "wallets": {"hub_admin_reserve": {f"reserve{i:02d}": _wallet(i) for i in range(1, n + 1)}},
        "node_seed_material": {},
    }


def _context(tmp_path: Path) -> HubContext:
    return HubContext(
        repo_root=tmp_path,
        hub_state_root=tmp_path / "runtime/state/hub",
        fdb_state_root=tmp_path / "runtime/state/fdb",
        chain_state_root=tmp_path / "runtime/state/chain",
        mother_private_path=tmp_path / "runtime/state/mother/identity.private.yaml",
        mother_metadata_path=tmp_path / "runtime/state/mother/identity.private.meta.json",
    )


def _bootstrap(ctx: HubContext, network_state: dict) -> None:
    op = OperationIdentity("bootstrap-hub-pool", "bootstrap-hub-pool", "mainnet", "MOTHER-OP-UPGRADE-HUB")
    document = {"kind": "main_computer.mother.private_state.v1", "schema_version": 1,
                "networks": {"mainnet": network_state}}
    closure = prepare_private_state_bootstrap(
        _paths(ctx), document, updated_at="2026-10-08T00:00:00Z",
        updated_by_action_id=op.operation_id, operation=op,
    )
    install_verified_private_state(_paths(ctx), closure, None, operation=op)


def test_claim_moves_prefunded_wallet_without_changing_pool_or_other_assignments():
    state = _network()
    before = set(genesis_addresses(state))
    first = claim_hub_admin(state, "mainneta-hub1")
    second = claim_hub_admin(state, "mainnetc-hub1")
    assert first["address"] != second["address"]
    assert claim_hub_admin(state, "mainneta-hub1")["address"] == first["address"]
    assert first["address"] == _wallet(1)["address"]
    assert second["address"] == _wallet(2)["address"]
    assert len(state["wallets"]["hub_admin_reserve"]) == 13
    assert set(genesis_addresses(state)) == before
    assert len(inspect_pool(state)["assigned"]) == 2
    assert not any("private_key" in key for key in first)


def test_claim_exhaustion_and_duplicate_assigned_fail_closed():
    state = _network()
    for i in range(1, 16):
        claim_hub_admin(state, f"hub{i}")
    before = deepcopy(state)
    with pytest.raises(HubAdminPoolError, match="HUB_ADMIN_RESERVE_EXHAUSTED"):
        claim_hub_admin(state, "hub16")
    assert state == before
    state["hubs"]["hub16"] = {"status": "inactive", "hub_admin": deepcopy(state["hubs"]["hub1"]["hub_admin"])}
    with pytest.raises(HubAdminPoolError, match="association does not match its Hub"):
        inspect_pool(state)


def test_preexisting_node_wallet_cannot_be_claimed_by_hub():
    state = _network()
    node_wallet = state["wallets"]["hub_admin_reserve"].pop("reserve01")
    state["node_seed_material"] = {"super1": {"wallets": {"hub_admin": node_wallet}}}
    claim = claim_hub_admin(state, "mainneta-hub1")
    assert claim["address"].lower() != node_wallet["address"].lower()
    assert len(genesis_addresses(state)) == 15


def test_verified_mother_successor_survives_retry_and_keeps_secrets_private(tmp_path):
    ctx = _context(tmp_path)
    _bootstrap(ctx, _network())
    before = read_private_state(_paths(ctx), operation=OperationIdentity("read", "read", "mainnet", "MOTHER-OP-UPGRADE-HUB"))
    first = reserve_admin(ctx, network="mainnet", hub_id="mainneta-hub1", operation_id="add-hub-one")
    after = read_private_state(_paths(ctx), operation=OperationIdentity("read", "read", "mainnet", "MOTHER-OP-UPGRADE-HUB"))
    assert after.binding.generation == before.binding.generation + 1
    same = reserve_admin(ctx, network="mainnet", hub_id="mainneta-hub1", operation_id="add-hub-one")
    repeat = read_private_state(_paths(ctx), operation=OperationIdentity("read", "read", "mainnet", "MOTHER-OP-UPGRADE-HUB"))
    assert same == first
    assert repeat.binding == after.binding
    assert "private_key" not in preview_admin(yaml.safe_load(repeat.document_bytes), network="mainnet", hub_id="mainneta-hub1")
    state = yaml.safe_load(repeat.document_bytes)["networks"]["mainnet"]
    assert len(state["wallets"]["hub_admin_reserve"]) == 14
    assert first["address"].lower() == private_key_to_address(first["private_key"]).lower()


def test_add_hub_reserves_before_deployment_and_never_stores_secret_in_operation(tmp_path, monkeypatch):
    monkeypatch.setattr(add_hub, "ensure_bridge_controller", lambda *a, **kw: {"verified": True, "already_authorized": True})
    ctx = _context(tmp_path)
    _bootstrap(ctx, _network())
    operation_id = "hub-add-mainnet-integration"
    write_operation(ctx, {
        "schema": ADD_OPERATION_SCHEMA, "operation_id": operation_id,
        "network": "mainnet", "kind": "add-hub", "stage": "prepared",
        "accepted_prestate": None, "target": {"hub_id": "mainneta-hub1", "network": "mainnet"},
    })
    seen = []
    def deployer(target):
        seen.append((target["_hub_admin_wallet"]["address"], target["hub_admin_address"]))
        assert "private_key" in target["_hub_admin_wallet"]
        net = yaml.safe_load(ctx.mother_private_path.read_text())["networks"]["mainnet"]
        assert net["hubs"]["mainneta-hub1"]["hub_admin"]["associated_hub"] == "mainneta-hub1"
        return {"application_uuid": "app-hub", "action": "created"}
    observer = lambda target: {"verified": True, "hub_admin_verified": True, "reason": "test"}
    result = add_hub.do(ctx, "mainnet", operation_id, deployer=deployer, observer=observer)
    assert result["status"] == "deployed"
    assert seen[0][0] == seen[0][1]
    serialized = json.dumps(require_operation(ctx, "mainnet", operation_id))
    assert _key(1) not in serialized
    assert "private_key" not in serialized
    assert result["details"]["hub_admin_verified"] is True


def test_runtime_installer_validates_hub_binding_and_reports_public_only(tmp_path, monkeypatch):
    payload = {"schema": HUB_ADMIN_BUNDLE_SCHEMA, "network": "mainnet", "hub_id": "hub1", **_wallet(10)}
    monkeypatch.setenv(HUB_ADMIN_BUNDLE_ENV, base64.b64encode(json.dumps(payload).encode()).decode())
    info = install_hub_admin_bundle(hub_id="hub1", network="mainnet", runtime_dir=tmp_path)
    assert info == {"address": payload["address"], "wallet_loaded": True}
    assert HUB_ADMIN_BUNDLE_ENV not in __import__("os").environ
    assert json.loads((tmp_path / "private/hub-admin/admin-wallet.json").read_text())["private_key"] == _key(10)
    assert _key(10) not in str(info)
    monkeypatch.setenv(HUB_ADMIN_BUNDLE_ENV, base64.b64encode(json.dumps(payload).encode()).decode())
    with pytest.raises(RuntimeError, match="unusable"):
        install_hub_admin_bundle(hub_id="another-hub", network="mainnet", runtime_dir=tmp_path)


def test_failed_deployment_retry_does_not_consume_second_wallet(tmp_path, monkeypatch):
    monkeypatch.setattr(add_hub, "ensure_bridge_controller", lambda *a, **kw: {"verified": True, "already_authorized": True})
    ctx = _context(tmp_path)
    _bootstrap(ctx, _network())
    operation_id = "hub-add-retry-wallet"
    write_operation(ctx, {
        "schema": ADD_OPERATION_SCHEMA, "operation_id": operation_id,
        "network": "mainnet", "kind": "add-hub", "stage": "prepared",
        "accepted_prestate": None, "target": {"hub_id": "mainneta-hub1", "network": "mainnet"},
    })
    first_address = []
    def fail(target):
        first_address.append(target["hub_admin_address"])
        raise HubControlError("HUB_DEPLOY_RETRY", "synthetic failed deployment")
    with pytest.raises(HubControlError, match="synthetic"):
        add_hub.do(ctx, "mainnet", operation_id, deployer=fail)
    state = yaml.safe_load(ctx.mother_private_path.read_bytes())["networks"]["mainnet"]
    assert len(state["wallets"]["hub_admin_reserve"]) == 14
    result = add_hub.do(
        ctx, "mainnet", operation_id,
        deployer=lambda target: {"application_uuid": "app-hub", "action": "reused"},
        observer=lambda target: {"verified": True, "hub_admin_verified": True},
    )
    assert result["details"]["hub_admin_address"] == first_address[0]
    final_state = yaml.safe_load(ctx.mother_private_path.read_bytes())["networks"]["mainnet"]
    assert len(final_state["wallets"]["hub_admin_reserve"]) == 14


def test_observer_requires_exact_installed_admin_address(monkeypatch):
    from tools.hub_control.common import deployment

    target = {
        "public_url": "https://example.invalid",
        "hub_id": "hub1",
        "hub_admin_address": _wallet(1)["address"],
        "fdb_contract": {"namespace": "network", "sha256": "fdb"},
        "chain_contract": {"chain_id": 42424240, "rpc_url": "https://rpc.example.invalid", "sha256": "chain"},
        "cluster_file_path": "/data/cluster",
        "bridge_signer_required": False,
    }
    installed = _wallet(2)["address"]
    def fake_get_json(url, **kwargs):
        if url.endswith("/health"):
            return {"ok": True}
        if url.endswith("/hub-identity"):
            return {
                "hub_id": "hub1", "hub_admin": {"address": installed, "wallet_loaded": True},
                "network": {"chain_id": 42424240, "chain_rpc_url": "https://rpc.example.invalid"},
                "storage": {"backend": "foundationdb", "cluster_file": "/data/cluster", "namespace": "network"},
            }
        return {"network": {"chain_id": 42424240, "chain_rpc_url": "https://rpc.example.invalid"}}
    monkeypatch.setattr(deployment, "_get_json", fake_get_json)
    monkeypatch.setattr(deployment.time, "sleep", lambda _: None)
    rejected = deployment.observe_hub(target, wait_timeout_s=0)
    assert rejected["verified"] is False
    assert rejected["hub_admin_verified"] is False
    assert rejected["last_error"]["failed_checks"] == ["hub_admin_address"]
    installed = _wallet(1)["address"]
    accepted = deployment.observe_hub(target, wait_timeout_s=0)
    assert accepted["verified"] is True
    assert accepted["hub_admin_verified"] is True


def test_add_hub_prep_completes_six_to_fifteen_through_verified_private_state(tmp_path):
    from tools.hub_control.common.admin_identity import prepare_admin_pool
    ctx = _context(tmp_path)
    state = _network(6)
    before_addresses = set(inspect_pool(state)["addresses"])
    _bootstrap(ctx, state)
    prior = read_private_state(
        _paths(ctx), operation=OperationIdentity("pool-read", "pool-read", "mainnet", "MOTHER-OP-UPGRADE-HUB"),
    )
    summary = prepare_admin_pool(ctx, network="mainnet", hub_id="mainneta-hub1")
    assert summary["existing"] == 6
    assert summary["generated"] == 9
    assert summary["total"] == 15
    current = read_private_state(
        _paths(ctx), operation=OperationIdentity("pool-read", "pool-read", "mainnet", "MOTHER-OP-UPGRADE-HUB"),
    )
    assert current.binding.generation == prior.binding.generation + 1
    network_state = yaml.safe_load(current.document_bytes)["networks"]["mainnet"]
    assert len(genesis_addresses(network_state)) == 15
    assert before_addresses <= set(genesis_addresses(network_state))
    assert "private_key" not in str(summary)
    repeated = prepare_admin_pool(ctx, network="mainnet", hub_id="mainneta-hub1")
    assert repeated["generated"] == 0
    assert read_private_state(
        _paths(ctx), operation=OperationIdentity("pool-read", "pool-read", "mainnet", "MOTHER-OP-UPGRADE-HUB"),
    ).binding == current.binding


def test_hub_admin_current_funding_gate_blocks_insufficient_balance(monkeypatch):
    from tools.hub_control.common.admin_identity import verify_admin_current_funding
    from tools.hub_control.common.models import DependencyContract
    from tools.hub_control.common import chain_contract
    contract = DependencyContract("chain", "mainnet", 1, "0" * 64, {"rpc_url": "https://rpc.invalid"})
    wallet = _wallet(1)["address"]
    monkeypatch.setattr(chain_contract, "_rpc", lambda url, method, params, **kw: "0x0")
    with pytest.raises(HubControlError) as error:
        verify_admin_current_funding(contract, wallet)
    assert error.value.code == "HUB_ADMIN_INSUFFICIENT_FUNDS"


def test_hub_admin_current_funding_gate_requires_both_contracts(monkeypatch):
    from tools.hub_control.common.admin_identity import verify_admin_current_funding
    from tools.hub_control.common.models import DependencyContract
    from tools.hub_control.common import chain_contract
    contract = DependencyContract("chain", "mainnet", 1, "0" * 64, {"rpc_url": "https://rpc.invalid"})
    wallet = _wallet(1)["address"]
    expected = hex(10_000 * 10**18)
    seen = []
    def fake_rpc(url, method, params, **kw):
        seen.append((method, params))
        return expected if method == "eth_getBalance" else "0x6000"
    monkeypatch.setattr(chain_contract, "_rpc", fake_rpc)
    result = verify_admin_current_funding(contract, wallet)
    assert result["verified"] is True
    assert result["block"] == "latest"
    assert result["balance_wei"] == 10_000 * 10**18
    assert len(seen) == 3
    assert all(params[-1] == "latest" for _, params in seen)
    monkeypatch.setattr(chain_contract, "_rpc", lambda url, method, params, **kw:
                        expected if method == "eth_getBalance" else "0x")
    with pytest.raises(HubControlError) as error:
        verify_admin_current_funding(contract, wallet)
    assert error.value.code == "HUB_REQUIRED_CONTRACT_MISSING"


def test_hub_admin_current_funding_rejects_null_latest_balance(monkeypatch):
    from tools.hub_control.common.admin_identity import verify_admin_current_funding
    from tools.hub_control.common.models import DependencyContract
    from tools.hub_control.common import chain_contract
    contract = DependencyContract("chain", "mainnet", 1, "0" * 64, {"rpc_url": "https://rpc.invalid"})
    monkeypatch.setattr(chain_contract, "_rpc", lambda url, method, params, **kw: None)
    with pytest.raises(HubControlError) as error:
        verify_admin_current_funding(contract, _wallet(1)["address"])
    assert error.value.code == "HUB_ADMIN_BALANCE_UNVERIFIED"


def test_hub_admin_current_funding_accepts_more_than_initial_allocation(monkeypatch):
    from tools.hub_control.common.admin_identity import verify_admin_current_funding
    from tools.hub_control.common.models import DependencyContract
    from tools.hub_control.common import chain_contract
    contract = DependencyContract("chain", "mainnet", 1, "0" * 64, {"rpc_url": "https://rpc.invalid"})
    more = 10_001 * 10**18
    monkeypatch.setattr(chain_contract, "_rpc", lambda url, method, params, **kw:
                        hex(more) if method == "eth_getBalance" else "0x6000")
    result = verify_admin_current_funding(contract, _wallet(1)["address"])
    assert result["verified"] is True
    assert result["balance_wei"] == more


def test_add_hub_pool_allocation_accepts_arbitrary_size_and_reuses_assigned():
    for size in (1, 3, 20):
        state = _network(size)
        assert len(inspect_pool(state, enforce_birth_size=False)["addresses"]) == size
        seen = []
        for i in range(size):
            claimed = claim_hub_admin(state, f"hub-{i}")
            seen.append(claimed["address"])
        assert len(set(seen)) == size
        assert len(state["wallets"]["hub_admin_reserve"]) == 0
        assert available_hub_admin(state, "hub-0")["address"] == seen[0]
        with pytest.raises(HubAdminPoolError, match="HUB_ADMIN_RESERVE_EXHAUSTED"):
            available_hub_admin(state, "another-hub")
