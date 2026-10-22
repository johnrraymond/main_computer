"""FDB-independent, fail-closed Hub removal membership evidence."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import hub_topology_check as topology
from tools.hub_control import inspect as inspector
from tools.hub_control.common.errors import HubControlError
from tools.hub_control.common.models import HubContext
from tools.hub_control.common.state import accepted_path
from tools.mother.common.ethereum_identity import private_key_to_address
from tools.mother.common.hub_admin_pool import claim_hub_admin

HUB = "mainneta-hub1"
URL = "https://mainneta-hub1.example.invalid"


def fixture_state(tmp_path: Path, *, accepted: bool = True):
    net = {
        "wallets": {"hub_admin_reserve": {
            f"reserve{i}": {"private_key": f"0x{i:064x}", "address": private_key_to_address(f"0x{i:064x}")}
            for i in range(1, 4)
        }},
        "hubs": {},
        "coolify": {"controllers": {"coolify-a": {}}},
    }
    claim_hub_admin(net, HUB)
    net["hubs"][HUB].update(status="active", controller_id="coolify-a", host_id="coolify-a",
                             public_url=URL, application_uuid="app-a")
    ctx = HubContext(repo_root=tmp_path,
                     hub_state_root=tmp_path / "runtime/state/hub",
                     fdb_state_root=tmp_path / "runtime/state/fdb",
                     chain_state_root=tmp_path / "runtime/state/chain",
                     mother_private_path=tmp_path / "private.yaml",
                     mother_metadata_path=tmp_path / "private.meta.json")
    if accepted:
        path = accepted_path(ctx, "mainnet")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema": "main-computer.hub-accepted.v1", "network": "mainnet",
            "generation": 8, "hubs": [{"hub_id": HUB, "controller_id": "coolify-a",
                                         "host_id": "coolify-a", "public_url": URL,
                                         "application_uuid": "app-a"}],
            "fdb_contract": {"generation": 7, "sha256": "old-fdb"},
            "chain_contract": {"generation": 4, "sha256": "old-chain"},
        }), encoding="utf-8")
    return ctx, net


class AppClientFactory:
    def __init__(self, *, unavailable=False, wrong_project=False, wrong_uuid=False):
        self.unavailable = unavailable
        self.wrong_project = wrong_project
        self.wrong_uuid = wrong_uuid

    def __call__(self, _url, _token):
        parent = self
        class Client:
            def request(self, method, path):
                if parent.unavailable:
                    raise TimeoutError("Coolify unreachable")
                app = {"name": "main-computer-" + HUB,
                       "uuid": "app-other" if parent.wrong_uuid else "app-a",
                       "environment_name": "mainnet-hubs",
                       "project_uuid": "wrong-project" if parent.wrong_project else "p"}
                if path == "/api/v1/applications":
                    return SimpleNamespace(ok=True, status=200, body=[app])
                if path.startswith("/api/v1/applications/"):
                    return SimpleNamespace(ok=True, status=200, body=app)
                raise AssertionError(path)
        return Client()


def configure_topology(monkeypatch, net):
    monkeypatch.setattr(topology, "load_private", lambda _ctx: {"networks": {"mainnet": net}})
    monkeypatch.setattr(topology, "controller_coordinates", lambda *a, **kw:
                        {"url": "https://coolify.invalid", "api_token": "test", "project_uuid": "p"})
    monkeypatch.setattr(topology, "load_hub_network_registry", lambda *a:
                        SimpleNamespace(get=lambda _n: SimpleNamespace(hub_url="https://network.invalid", kind="mainnet")))
    def forbidden(*args, **kwargs):
        raise AssertionError("removal membership inspection must not require a live FDB or Chain contract")
    monkeypatch.setattr(topology, "load_current_fdb_contract", forbidden)
    monkeypatch.setattr(topology, "load_current_chain_contract", forbidden)
    return forbidden


def test_removal_membership_is_verified_with_no_fdb_or_runtime(tmp_path, monkeypatch):
    ctx, net = fixture_state(tmp_path)
    forbidden = configure_topology(monkeypatch, net)
    report = topology.observe_topology(ctx, "mainnet", client_factory=AppClientFactory(),
                                       hub_observer=forbidden, membership_only=True)
    assert report["status"] == "PASS"
    assert report["verification_scope"] == "coolify-membership-only"
    assert report["observed_hubs"][0]["runtime_verified"] is False
    assert report["observed_hubs"][0]["coolify_identity_verified"] is True
    assert report["accepted_projection"]["status"] == "current"


@pytest.mark.parametrize("client,expected", [
    (AppClientFactory(unavailable=True), "UNKNOWN"),
    (AppClientFactory(wrong_project=True), "UNKNOWN"),
    (AppClientFactory(wrong_uuid=True), "UNKNOWN"),
])
def test_removal_fails_closed_on_coolify_uncertainty(tmp_path, monkeypatch, client, expected):
    ctx, net = fixture_state(tmp_path)
    configure_topology(monkeypatch, net)
    report = topology.observe_topology(ctx, "mainnet", client_factory=client, membership_only=True)
    assert report["status"] == expected
    assert report["errors"]
    assert "seal_command" not in report


def test_removal_rejects_stale_accepted_application_identity(tmp_path, monkeypatch):
    ctx, net = fixture_state(tmp_path)
    configure_topology(monkeypatch, net)
    receipt_path = accepted_path(ctx, "mainnet")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["hubs"][0]["application_uuid"] = "stale-app-uuid"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    report = topology.observe_topology(ctx, "mainnet", client_factory=AppClientFactory(),
                                       membership_only=True)
    assert report["status"] == "UNKNOWN"
    assert any(item["code"] == "HUB_ACCEPTED_PLACEMENT_MISMATCH" for item in report["errors"])
    assert "seal_command" not in report


def test_remove_only_inspection_requires_fresh_independent_membership_proof(tmp_path, monkeypatch):
    ctx, net = fixture_state(tmp_path)
    configure_topology(monkeypatch, net)
    proof = topology.observe_topology(ctx, "mainnet", client_factory=AppClientFactory(), membership_only=True)
    monkeypatch.setattr(topology, "observe_topology", lambda _ctx, _network, membership_only: proof)
    result = inspector.inspect_network(ctx, "mainnet", membership_only=True)
    assert result["status"] == "accepted"
    assert result["topology_verification"] == {"verified": True,
        "reason": "coolify-membership-verified-runtime-not-checked"}
    assert result["hubs"][0]["verification"]["verified"] is False
    proof["status"] = "UNKNOWN"
    with pytest.raises(HubControlError) as exc:
        inspector.inspect_network(ctx, "mainnet", membership_only=True)
    assert exc.value.code == "HUB_REMOVE_MEMBERSHIP_UNVERIFIED"


def test_remove_only_inspection_accepts_verified_empty_fleet_after_finalize(tmp_path, monkeypatch):
    ctx, net = fixture_state(tmp_path)
    p = accepted_path(ctx, "mainnet")
    receipt = json.loads(p.read_text(encoding="utf-8"))
    receipt.update(generation=9, hubs=[])
    p.write_text(json.dumps(receipt), encoding="utf-8")
    report = {"status": "PASS", "verification_scope": "coolify-membership-only", "errors": [],
              "accepted_projection": {"status": "current", "generation": 9, "hub_ids": []}}
    monkeypatch.setattr(topology, "observe_topology", lambda _ctx, _network, membership_only: report)
    result = inspector.inspect_network(ctx, "mainnet", membership_only=True)
    assert result["status"] == "accepted-empty"
    assert result["empty_topology_verification"]["verified"] is True
