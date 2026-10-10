"""Identity movement is authoritative in Mother private state, never accepted.json."""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import yaml

from tools.mother.common.ethereum_identity import private_key_to_address
from tools.mother.common.hub_admin_pool import (
    available_hub_admin, claim_hub_admin, release_hub_admin, inspect_pool,
    HubAdminPoolError,
)
from tools.mother.common.models import OperationIdentity
from tools.mother.common.private_state import (
    prepare_private_state_bootstrap, install_verified_private_state, read_private_state,
)
from tools.hub_control.common.admin_identity import _paths, reserve_admin, transition_hub_identity
from tools.hub_control.common.models import HubContext
from tools import hub_topology_seal as seal, hub_topology_check as check

A = "mainneta-hub1"
C = "mainnetc-hub1"

def wallet(i):
    key = f"0x{i:064x}"
    return {"private_key": key, "address": private_key_to_address(key)}


def state():
    return {"hubs": {}, "node_seed_material": {}, "wallets": {
        "hub_admin_reserve": {"reserve01": wallet(1), "reserve02": wallet(2), "reserve03": wallet(3)}
    }}


def test_release_preserves_association_and_reclaim_prefers_historical_wallet():
    net = state()
    net["unrelated_private_note"] = {"value": "untouched"}
    selected = claim_hub_admin(net, A)
    address = selected["address"]
    assert net["hubs"][A]["hub_admin"]["associated_hub"] == A
    assert release_hub_admin(net, A)["address"] == address
    assert net["hubs"][A].get("hub_admin") is None
    found = [(key, val) for key, val in net["wallets"]["hub_admin_reserve"].items() if val.get("associated_hub") == A]
    assert len(found) == 1 and found[0][1]["address"] == address
    assert release_hub_admin(net, A) is None
    # C must never consume A's wallet, even though it is in reserve.
    assert claim_hub_admin(net, C)["address"] != address
    assert claim_hub_admin(net, A)["address"] == address
    assert len(inspect_pool(net, enforce_birth_size=False)["addresses"]) == 3
    assert net["unrelated_private_note"] == {"value": "untouched"}


def test_no_wallet_stealing_when_reserve_has_only_other_hub_association():
    net = state()
    for label in ("reserve02", "reserve03"):
        del net["wallets"]["hub_admin_reserve"][label]
    net["wallets"]["hub_admin_reserve"]["reserve01"]["associated_hub"] = A
    with pytest.raises(HubAdminPoolError, match="HUB_ADMIN_RESERVE_EXHAUSTED"):
        claim_hub_admin(net, C)
    assert available_hub_admin(net, A)["address"] == wallet(1)["address"]


def test_wallet_association_collision_fails_closed():
    net = state()
    net["wallets"]["hub_admin_reserve"]["reserve01"]["associated_hub"] = A
    net["wallets"]["hub_admin_reserve"]["reserve02"]["associated_hub"] = A
    with pytest.raises(HubAdminPoolError, match="multiple wallets associated"):
        inspect_pool(net, enforce_birth_size=False)


def context(tmp_path):
    return HubContext(
        repo_root=tmp_path,
        hub_state_root=tmp_path / "runtime/state/hub",
        fdb_state_root=tmp_path / "runtime/state/fdb",
        chain_state_root=tmp_path / "runtime/state/chain",
        mother_private_path=tmp_path / "runtime/state/mother/identity.private.yaml",
        mother_metadata_path=tmp_path / "runtime/state/mother/identity.private.meta.json",
    )


def bootstrap(ctx, net):
    identity = OperationIdentity("wallet-test-bootstrap", "wallet-test-bootstrap", "mainnet", "MOTHER-OP-UPGRADE-HUB")
    doc = {"kind": "main_computer.mother.private_state.v1", "schema_version": 1,
           "networks": {"mainnet": net}}
    closure = prepare_private_state_bootstrap(_paths(ctx), doc, updated_at="2026-10-08T00:00:00Z",
                                              updated_by_action_id=identity.operation_id, operation=identity)
    install_verified_private_state(_paths(ctx), closure, None, operation=identity)


def read_net(ctx):
    identity = OperationIdentity("wallet-test-read", "wallet-test-read", "mainnet", "MOTHER-OP-UPGRADE-HUB")
    return yaml.safe_load(read_private_state(_paths(ctx), operation=identity).document_bytes)["networks"]["mainnet"]


def test_verified_private_state_claim_activate_release_reclaim_cycle(tmp_path):
    ctx = context(tmp_path)
    bootstrap(ctx, state())
    first = reserve_admin(ctx, network="mainnet", hub_id=A, operation_id="first-add")
    address = first["address"]
    assert first["private_state_path"] == f"networks.mainnet.hubs.{A}.hub_admin"
    transition_hub_identity(ctx, network="mainnet", hub_id=A, operation_id="activate", status="active",
                            placement={"controller_id": "coolify-a", "application_uuid": "app-a"})
    transition_hub_identity(ctx, network="mainnet", hub_id=A, operation_id="remove", status="inactive")
    net = read_net(ctx)
    assert net["hubs"][A]["status"] == "inactive"
    assert "hub_admin" not in net["hubs"][A]
    assert len([row for row in net["wallets"]["hub_admin_reserve"].values() if row.get("associated_hub") == A]) == 1
    assert reserve_admin(ctx, network="mainnet", hub_id=A, operation_id="readd")["address"] == address


def test_verified_seal_returns_missing_hub_wallet_to_reserve(tmp_path):
    ctx = context(tmp_path)
    net = state()
    claim_hub_admin(net, A)
    net["hubs"][A].update({"status": "active", "controller_id": "coolify-a", "application_uuid": "old-app"})
    bootstrap(ctx, net)
    before = read_net(ctx)
    def observation(_ctx, _network):
        current = read_net(ctx)
        return {"network": "mainnet", "status": "DRIFT", "errors": [],
                "mother_state_sha256": check._digest({
                    "hubs": current.get("hubs", {}),
                    "hub_admin_reserve": current["wallets"].get("hub_admin_reserve") or {},
                }),
                "state_hubs_active": [A], "state_hubs_inactive": [],
                "locked_hub_admins": [{"hub_id": A, "address": before["hubs"][A]["hub_admin"]["address"]}],
                "reserved_associations": [], "legacy_assignments_pending": [],
                "observed_hubs": [], "missing_hubs": [A], "extra_hubs": [], "inventories": []}
    result = seal.seal_topology(ctx, "mainnet", observe=observation, apply_state=True)
    assert result["ok"] and result["state_applied"] and result["wallets_released"]
    after = read_net(ctx)
    assert after["hubs"][A]["status"] == "inactive"
    assert not after["hubs"][A].get("hub_admin")
    assert any(x.get("associated_hub") == A and x["address"] == before["hubs"][A]["hub_admin"]["address"]
               for x in after["wallets"]["hub_admin_reserve"].values())

class NoAppFactory:
    def __init__(self, *, fail=False):
        self.fail = fail

    def __call__(self, _url, _token):
        fail = self.fail
        class Client:
            def request(self, method, path):
                if fail:
                    raise TimeoutError("controller unavailable")
                assert method == "GET" and path == "/api/v1/applications"
                return SimpleNamespace(ok=True, status=200, body=[])
        return Client()


def test_topology_check_uses_hub_state_and_preserves_reserve_association(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    net = state()
    net["wallets"]["hub_admin_reserve"]["reserve01"]["associated_hub"] = A
    net["coolify"] = {"controllers": {"coolify-a": {}}}
    monkeypatch.setattr(check, "load_private", lambda _ctx: {"networks": {"mainnet": net}})
    monkeypatch.setattr(check, "controller_coordinates", lambda *args, **kwargs: {"url": "http://x", "api_token": "x", "project_uuid": "p"})
    monkeypatch.setattr(check, "load_current_fdb_contract", lambda *args: SimpleNamespace(payload={}))
    monkeypatch.setattr(check, "load_current_chain_contract", lambda *args: SimpleNamespace(payload={}))
    monkeypatch.setattr(check, "load_hub_network_registry", lambda *_: SimpleNamespace(get=lambda _: SimpleNamespace(hub_url="https://shared.invalid", kind="mainnet")))
    report = check.observe_topology(ctx, "mainnet", client_factory=NoAppFactory())
    assert report["status"] == "PASS"
    assert report["reserved_associations"] == [{"hub_id": A, "address": wallet(1)["address"]}]
    assert report["locked_hub_admins"] == []
    claim_hub_admin(net, A)
    net["hubs"][A]["status"] = "active"
    report = check.observe_topology(ctx, "mainnet", client_factory=NoAppFactory())
    assert report["status"] == "DRIFT" and report["missing_hubs"] == [A]
    report = check.observe_topology(ctx, "mainnet", client_factory=NoAppFactory(fail=True))
    assert report["status"] == "UNKNOWN"


def test_seal_empty_mother_topology_reprojects_stale_legacy_accepted_without_recreating_wallets(tmp_path):
    from tools.hub_control.common.state import accepted_path, read_accepted
    ctx = context(tmp_path)
    net = state()
    bootstrap(ctx, net)
    path = accepted_path(ctx, "mainnet")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": "main-computer.hub-accepted.v1", "network": "mainnet", "generation": 10,
        "cluster_id": "main-computer-mainnet-hubs", "hubs": [{"hub_id": A}, {"hub_id": C}],
        "fdb_contract": {"generation": 8, "sha256": "x"},
        "chain_contract": {"generation": 8, "sha256": "y"},
    }))
    def observe(_ctx, _network):
        current = read_net(ctx)
        return {"network": "mainnet", "status": "PASS", "errors": [],
                "mother_state_sha256": check._digest({
                    "hubs": current.get("hubs", {}),
                    "hub_admin_reserve": current["wallets"].get("hub_admin_reserve") or {},
                }),
                "state_hubs_active": [], "state_hubs_inactive": [],
                "locked_hub_admins": [], "reserved_associations": [],
"observed_hubs": [],
                "missing_hubs": [], "extra_hubs": [], "inventories": []}
    report = seal.seal_topology(ctx, "mainnet", observe=observe, apply_state=True)
    assert report["accepted_projection_updated"] is True
    assert report["state_applied"] is False
    assert read_accepted(ctx, "mainnet")["generation"] == 11
    assert read_accepted(ctx, "mainnet")["hubs"] == []
    assert all(not item.get("associated_hub") for item in read_net(ctx)["wallets"]["hub_admin_reserve"].values())
    assert seal.seal_topology(ctx, "mainnet", observe=observe, apply_state=True)["accepted_projection_updated"] is False


def test_topology_check_detects_stale_accepted_receipt_and_offers_reseal(tmp_path, monkeypatch):
    from tools.hub_control.common.state import accepted_path, read_accepted
    ctx = context(tmp_path)
    net = state()
    net["coolify"] = {"controllers": {"coolify-a": {}}}
    monkeypatch.setattr(check, "load_private", lambda _ctx: {"networks": {"mainnet": net}})
    monkeypatch.setattr(check, "controller_coordinates", lambda *args, **kwargs: {"url": "http://x", "api_token": "x", "project_uuid": "p"})
    monkeypatch.setattr(check, "load_current_fdb_contract", lambda *args: SimpleNamespace(payload={}))
    monkeypatch.setattr(check, "load_current_chain_contract", lambda *args: SimpleNamespace(payload={}))
    monkeypatch.setattr(check, "load_hub_network_registry", lambda *_: SimpleNamespace(get=lambda _: SimpleNamespace(hub_url="https://shared.invalid", kind="mainnet")))
    path = accepted_path(ctx, "mainnet")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": "main-computer.hub-accepted.v1", "network": "mainnet",
                                "generation": 9, "hubs": [{"hub_id": A}]}))
    report = check.observe_topology(ctx, "mainnet", client_factory=NoAppFactory())
    assert report["status"] == "DRIFT"  # Mother and Coolify are empty!
    assert report["missing_hubs"] == report["extra_hubs"] == []
    assert report["accepted_projection"]["stale_hubs"] == [A]
    assert report["accepted_projection"]["unprojected_hubs"] == []
    assert report["seal_command"] == r"python .\tools\hub_topology_seal.py --network mainnet --apply-state"
    assert read_accepted(ctx, "mainnet")["hubs"] == [{"hub_id": A}]  # read-only

    report = check.observe_topology(ctx, "mainnet", client_factory=NoAppFactory(fail=True))
    assert report["status"] == "UNKNOWN"
    assert "seal_command" not in report

    # After explicit seal: the same verified inventory should report PASS.
    path.write_text(json.dumps({"schema": "main-computer.hub-accepted.v1", "network": "mainnet",
                                "generation": 10, "hubs": []}))
    report = check.observe_topology(ctx, "mainnet", client_factory=NoAppFactory())
    assert report["status"] == "PASS"
    assert report["accepted_projection"]["status"] == "current"
    assert "seal_command" not in report


def test_topology_check_rejects_invalid_accepted_receipt_without_reseal(tmp_path, monkeypatch):
    from tools.hub_control.common.state import accepted_path
    ctx = context(tmp_path)
    net = state()
    net["coolify"] = {"controllers": {"coolify-a": {}}}
    monkeypatch.setattr(check, "load_private", lambda _ctx: {"networks": {"mainnet": net}})
    monkeypatch.setattr(check, "controller_coordinates", lambda *args, **kwargs: {"url": "http://x", "api_token": "x", "project_uuid": "p"})
    monkeypatch.setattr(check, "load_current_fdb_contract", lambda *args: SimpleNamespace(payload={}))
    monkeypatch.setattr(check, "load_current_chain_contract", lambda *args: SimpleNamespace(payload={}))
    monkeypatch.setattr(check, "load_hub_network_registry", lambda *_: SimpleNamespace(get=lambda _: SimpleNamespace(hub_url="https://shared.invalid", kind="mainnet")))
    path = accepted_path(ctx, "mainnet")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"invalid":true}')
    report = check.observe_topology(ctx, "mainnet", client_factory=NoAppFactory())
    assert report["status"] == "UNKNOWN"
    assert any(x["code"] == "HUB_ACCEPTED_PROJECTION_UNVERIFIED" for x in report["errors"])
    assert "seal_command" not in report


def test_stale_projection_detection_seal_roundtrip(tmp_path, monkeypatch):
    from tools.hub_control.common.state import accepted_path, read_accepted
    ctx = context(tmp_path)
    net = state()
    net["coolify"] = {"controllers": {"coolify-a": {}}}
    bootstrap(ctx, net)
    path = accepted_path(ctx, "mainnet")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": "main-computer.hub-accepted.v1", "network": "mainnet",
                                "generation": 9, "hubs": [{"hub_id": A}],
                                "fdb_contract": {"generation": 8},
                                "chain_contract": {"generation": 8}}))
    monkeypatch.setattr(check, "load_private", lambda _ctx: {"networks": {"mainnet": read_net(ctx)}})
    monkeypatch.setattr(check, "controller_coordinates", lambda *args, **kwargs: {"url": "http://x", "api_token": "x", "project_uuid": "p"})
    monkeypatch.setattr(check, "load_current_fdb_contract", lambda *args: SimpleNamespace(payload={}))
    monkeypatch.setattr(check, "load_current_chain_contract", lambda *args: SimpleNamespace(payload={}))
    monkeypatch.setattr(check, "load_hub_network_registry", lambda *_: SimpleNamespace(get=lambda _: SimpleNamespace(hub_url="https://shared.invalid", kind="mainnet")))
    observe = lambda _ctx, _network: check.observe_topology(_ctx, _network, client_factory=NoAppFactory())
    assert observe(ctx, "mainnet")["status"] == "DRIFT"
    before = read_net(ctx)
    result = seal.seal_topology(ctx, "mainnet", observe=observe, apply_state=True)
    assert result["accepted_projection_updated"] is True
    assert result["state_applied"] is False
    assert read_accepted(ctx, "mainnet")["hubs"] == []
    assert read_net(ctx) == before
    assert observe(ctx, "mainnet")["status"] == "PASS"


def test_projection_only_seal_allows_empty_receipt_but_rejects_state_change(tmp_path):
    from tools.hub_control.common.state import accepted_path, read_accepted
    ctx = context(tmp_path)
    net = state()
    bootstrap(ctx, net)
    path = accepted_path(ctx, "mainnet")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": "main-computer.hub-accepted.v1", "network": "mainnet", "generation": 10,
        "hubs": [{"hub_id": A}, {"hub_id": C}],
        "fdb_contract": {"generation": 8}, "chain_contract": {"generation": 8},
    }))

    def empty(_ctx, _network):
        return {"network": "mainnet", "status": "DRIFT", "errors": [],
                "accepted_projection": {"status": "stale", "stale_hubs": [A, C], "unprojected_hubs": []},
                "mother_state_sha256": check._digest({"hubs": {}, "hub_admin_reserve": net["wallets"]["hub_admin_reserve"]}),
                "state_hubs_active": [], "state_hubs_inactive": [], "observed_hubs": [],
                "missing_hubs": [], "extra_hubs": [], "admin_location_drift": [],
                "locked_hub_admins": [], "reserved_associations": [], "inventories": []}

    before = read_net(ctx)
    result = seal.seal_topology(ctx, "mainnet", observe=empty, apply_state=True, projection_only=True)
    assert result["accepted_projection_updated"] and not result["state_applied"]
    assert read_accepted(ctx, "mainnet")["hubs"] == []
    assert read_net(ctx) == before

    with pytest.raises(ValueError, match="PROJECTION_ONLY_REFUSED"):
        seal.seal_topology(ctx, "mainnet", observe=lambda c, n: {
            **empty(c, n), "state_hubs_active": [A], "missing_hubs": [A],
        }, apply_state=True, projection_only=True)
