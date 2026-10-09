from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.hub_control import add_hub
from tools.hub_control.common.errors import HubControlError
from tools.hub_control.common.models import HubContext
from tools.hub_control.common.state import read_accepted, require_operation, update_operation


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _legacy_hub_control_fixture_without_funded_pool(monkeypatch):
    """Legacy topology tests intentionally use stub Mother state without genesis wallets.

    The real Mother successor and pool are covered in test_hub_admin_wallet_allocation.
    """
    from tools.mother.common.ethereum_identity import private_key_to_address
    key = "0x" + "01".zfill(64)
    address = private_key_to_address(key)
    monkeypatch.setattr(add_hub, "verify_admin_current_funding", lambda *a, **kw: {"verified": True, "block": "latest"})
    monkeypatch.setattr(add_hub, "preview_admin", lambda *a, **kw: {"address": address, "source": "legacy-fixture"})
    monkeypatch.setattr(add_hub, "ensure_bridge_controller", lambda *a, **kw: {"verified": True, "already_authorized": True, "controller": address})
    monkeypatch.setattr(add_hub, "reserve_admin", lambda *a, **kw: {
        "address": address, "private_key": key,
        "private_state_path": "networks.mainnet.hub_admin_assignments.fixture",
    })


def _ctx(tmp_path: Path) -> HubContext:
    return HubContext(
        repo_root=ROOT,
        hub_state_root=tmp_path / "runtime" / "state" / "hub",
        fdb_state_root=tmp_path / "runtime" / "state" / "fdb",
        chain_state_root=tmp_path / "runtime" / "state" / "chain",
        mother_private_path=tmp_path / "runtime" / "state" / "mother" / "identity.private.yaml",
        mother_metadata_path=tmp_path / "runtime" / "state" / "mother" / "identity.private.meta.json",
    )


def _seed_dependencies(ctx: HubContext) -> None:
    ctx.mother_private_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.mother_private_path.write_text(
        """
networks:
  mainnet:
    chain_id: 42424240
    rpc: https://mainnet-rpc.greatlibrary.io
    coolify:
      controllers:
        coolify-a:
          url: http://coolify-a.invalid
          api_token: token-a
          project_uuid: project-a
          server_uuid: server-a
        coolify-c:
          url: http://coolify-c.invalid
          api_token: token-c
          project_uuid: project-c
          server_uuid: server-c
""".strip() + "\n",
        encoding="utf-8",
    )
    ctx.mother_metadata_path.write_text(json.dumps({"generation": 12}), encoding="utf-8")
    fdb = ctx.fdb_state_root / "mainnet" / "accepted.json"
    fdb.parent.mkdir(parents=True, exist_ok=True)
    fdb.write_text(
        json.dumps(
            {
                "schema": "main-computer.fdb.accepted-cluster.v1",
                "network": "mainnet",
                "generation": 8,
                "cluster": {"description": "main_computer_mainnet", "cluster_id": "abc123"},
                "services": [
                    {
                        "service_id": "mainneta-fdb1",
                        "host_id": "coolify-a",
                        "address": "10.116.0.3",
                        "port": 4550,
                        "machine_id": "coolify-a",
                        "zone_id": "coolify-a",
                    }
                ],
                "coordinators": [
                    {
                        "service_id": "mainneta-fdb1",
                        "host_id": "coolify-a",
                        "address": "10.116.0.3",
                        "port": 4550,
                    }
                ],
                "redundancy_mode": "single",
                "storage_engine": "ssd",
                "retired": False,
            }
        ),
        encoding="utf-8",
    )


def _observer(_target, **_kwargs):
    return {
        "verified": True,
        "reason": "hub-fdb-and-chain-consumption-verified",
        "hub_running": True,
        "fdb_adoption_verified": True,
        "chain_adoption_verified": True,
    }


def test_add_hub_prep_from_unborn_discovers_both_dependency_contracts(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)

    result = add_hub.prep(
        ctx,
        "mainnet",
        "mainneta-hub1",
        chain_verifier=lambda contract: {
            "verified": True,
            "reason": "test-chain-proof",
            "chain_id": contract.payload["chain_id"],
        },
        deployment_inspector=lambda _target: {"present": False, "application_uuid": None},
    )

    details = result["details"]
    assert result["status"] == "prepared"
    assert details["target_generation"] == 1
    assert details["target_hub_count"] == 1
    assert details["rebirth"] is True
    assert details["controller_id"] == "coolify-a"
    assert details["host_id"] == "coolify-a"
    assert details["fdb_contract"]["generation"] == 8
    assert details["chain_contract"]["generation"] == 12
    op = require_operation(ctx, "mainnet", details["operation_id"])
    assert op["target"]["environment_name"] == "mainnet-hubs"
    assert (ctx.fdb_state_root / "mainnet" / "consumer-contract.json").is_file()
    assert (ctx.chain_state_root / "mainnet" / "consumer-contract.json").is_file()


def test_first_hub_uses_enumerated_identity_and_keeps_network_ingress_separate(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)

    prepared = add_hub.prep(
        ctx,
        "mainnet",
        "mainneta-hub1",
        chain_verifier=lambda _contract: {"verified": True, "reason": "test-chain-proof"},
        deployment_inspector=lambda _target: {"present": False},
    )

    op = require_operation(ctx, "mainnet", prepared["details"]["operation_id"])
    target = op["target"]
    assert target["public_url"] == "https://mainneta-hub1.greatlibrary.io"
    assert target["network_ingress_url"] == "https://mainnet-hub.greatlibrary.io"
    assert target["serve_network_ingress"] is True
    assert target["topology"]["entry_urls"] == ["https://mainnet-hub.greatlibrary.io"]
    assert target["topology"]["hubs"] == [
        {
            "hub_id": "mainneta-hub1",
            "hub_url": "https://mainneta-hub1.greatlibrary.io",
            "public_url": "https://mainneta-hub1.greatlibrary.io",
            "roles": ["entry", "execution"],
        }
    ]


def test_new_controller_hub_gets_its_own_enumerated_url_and_ingress_alias(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    first = add_hub.prep(
        ctx,
        "mainnet",
        "mainneta-hub1",
        chain_verifier=lambda _contract: {"verified": True, "reason": "test-chain-proof"},
        deployment_inspector=lambda _target: {"present": False},
    )
    first_id = first["details"]["operation_id"]
    add_hub.do(ctx, "mainnet", first_id, deployer=lambda _target: {"application_uuid": "app-a", "action": "created"}, observer=_observer)
    add_hub.finalize(ctx, "mainnet", first_id, observer=_observer)

    second = add_hub.prep(
        ctx,
        "mainnet",
        "mainnetc-hub1",
        chain_verifier=lambda _contract: {"verified": True, "reason": "test-chain-proof"},
        deployment_inspector=lambda _target: {"present": False},
    )
    target = require_operation(ctx, "mainnet", second["details"]["operation_id"])["target"]
    assert target["public_url"] == "https://mainnetc-hub1.greatlibrary.io"
    assert target["network_ingress_url"] == "https://mainnet-hub.greatlibrary.io"
    assert target["serve_network_ingress"] is True
    assert [hub["public_url"] for hub in target["topology"]["hubs"]] == [
        "https://mainneta-hub1.greatlibrary.io",
        "https://mainnetc-hub1.greatlibrary.io",
    ]


def test_second_hub_on_same_controller_does_not_claim_shared_ingress_host_rule(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    first = add_hub.prep(
        ctx,
        "mainnet",
        "mainneta-hub1",
        chain_verifier=lambda _contract: {"verified": True, "reason": "test-chain-proof"},
        deployment_inspector=lambda _target: {"present": False},
    )
    first_id = first["details"]["operation_id"]
    add_hub.do(ctx, "mainnet", first_id, deployer=lambda _target: {"application_uuid": "app-a1", "action": "created"}, observer=_observer)
    add_hub.finalize(ctx, "mainnet", first_id, observer=_observer)

    second = add_hub.prep(
        ctx,
        "mainnet",
        "mainneta-hub2",
        chain_verifier=lambda _contract: {"verified": True, "reason": "test-chain-proof"},
        deployment_inspector=lambda _target: {"present": False},
    )
    target = require_operation(ctx, "mainnet", second["details"]["operation_id"])["target"]
    assert target["public_url"] == "https://mainneta-hub2.greatlibrary.io"
    assert target["serve_network_ingress"] is False


def test_unborn_add_hub_round_trip_accepts_generation_one_after_both_proofs(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    prepared = add_hub.prep(
        ctx,
        "mainnet",
        "mainneta-hub1",
        chain_verifier=lambda _contract: {"verified": True, "reason": "test-chain-proof"},
        deployment_inspector=lambda _target: {"present": False},
    )
    operation_id = prepared["details"]["operation_id"]

    deployed = add_hub.do(
        ctx,
        "mainnet",
        operation_id,
        deployer=lambda _target: {"application_uuid": "app-1", "action": "created"},
        observer=_observer,
    )
    assert deployed["status"] == "deployed"
    assert deployed["details"]["hub_running"] is True
    assert deployed["details"]["fdb_adoption_verified"] is True
    assert deployed["details"]["chain_adoption_verified"] is True

    proof = add_hub.inspect_operation(ctx, "mainnet", operation_id, observer=_observer)
    assert proof["hub_add_verification"]["verified"] is True

    finalized = add_hub.finalize(ctx, "mainnet", operation_id, observer=_observer)
    assert finalized["status"] == "finalized"
    assert finalized["details"]["accepted_generation"] == 1

    accepted = read_accepted(ctx, "mainnet")
    assert accepted is not None
    assert accepted["generation"] == 1
    assert accepted["hubs"][0]["hub_id"] == "mainneta-hub1"
    assert accepted["hubs"][0]["host_id"] == "coolify-a"
    assert accepted["fdb_contract"]["generation"] == 8
    assert accepted["chain_contract"]["generation"] == 12


def test_first_birth_can_freeze_legacy_hub_application_as_migration_candidate(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    result = add_hub.prep(
        ctx,
        "mainnet",
        "mainneta-hub1",
        chain_verifier=lambda _contract: {"verified": True, "reason": "test-chain-proof"},
        deployment_inspector=lambda _target: {
            "present": True,
            "application_uuid": "legacy-app-uuid",
            "migration_candidate": True,
            "resolution": {"source": "legacy-hub-application"},
        },
    )
    operation_id = result["details"]["operation_id"]
    from tools.hub_control.common.state import require_operation
    op = require_operation(ctx, "mainnet", operation_id)
    assert op["target"]["application_uuid"] == "legacy-app-uuid"
    assert op["target"]["migration_candidate"] is True
    assert result["details"]["legacy_migration"] is True


def test_network_inspect_after_first_birth_proves_both_edges(tmp_path: Path) -> None:
    from tools.hub_control.inspect import inspect_network

    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    prepared = add_hub.prep(
        ctx,
        "mainnet",
        "mainneta-hub1",
        chain_verifier=lambda _contract: {"verified": True, "reason": "test-chain-proof"},
        deployment_inspector=lambda _target: {"present": False},
    )
    operation_id = prepared["details"]["operation_id"]
    add_hub.do(
        ctx,
        "mainnet",
        operation_id,
        deployer=lambda _target: {"application_uuid": "app-1", "action": "created"},
        observer=_observer,
    )
    add_hub.finalize(ctx, "mainnet", operation_id, observer=_observer)

    result = inspect_network(ctx, "mainnet", observer=_observer)
    assert result["status"] == "accepted"
    assert result["accepted_generation"] == 1
    assert result["topology_verification"]["verified"] is True
    assert result["hubs"][0]["fdb_status"] == "current"
    assert result["hubs"][0]["chain_status"] == "current"


def test_add_hub_do_persists_actionable_runtime_failure_evidence(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    prepared = add_hub.prep(
        ctx,
        "mainnet",
        "mainneta-hub1",
        chain_verifier=lambda _contract: {"verified": True, "reason": "test-chain-proof"},
        deployment_inspector=lambda _target: {"present": False},
    )
    operation_id = prepared["details"]["operation_id"]
    deployment_result = {
        "application_uuid": "app-1",
        "action": "created",
        "deployment_uuid": "dep-1",
        "deployment_status": "finished",
        "deployment_commit": "abc123",
        "deployment_waited": True,
    }
    verification = {
        "verified": False,
        "reason": "hub-runtime-verification-timeout",
        "hub_running": True,
        "fdb_adoption_verified": False,
        "chain_adoption_verified": True,
        "last_error": {"failed_checks": ["fdb_namespace"], "endpoint_errors": {}},
    }

    with pytest.raises(HubControlError) as exc_info:
        add_hub.do(
            ctx,
            "mainnet",
            operation_id,
            deployer=lambda _target: deployment_result,
            observer=lambda _target: verification,
        )

    assert exc_info.value.code == "HUB_ADD_NOT_VERIFIED"
    assert "fdb_namespace" in exc_info.value.message
    operation = require_operation(ctx, "mainnet", operation_id)
    assert operation["stage"] == "prepared"
    assert operation["last_deployment_result"]["deployment_uuid"] == "dep-1"
    assert operation["last_verification"]["hub_running"] is True
    assert operation["last_verification"]["chain_adoption_verified"] is True


def test_add_hub_prep_refuses_existing_application_in_chain_environment(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)

    with pytest.raises(HubControlError) as exc_info:
        add_hub.prep(
            ctx,
            "mainnet",
            "mainneta-hub1",
            chain_verifier=lambda _contract: {"verified": True, "reason": "test-chain-proof"},
            deployment_inspector=lambda _target: {
                "present": True,
                "application_uuid": "app-wrong-env",
                "environment_name": "mainnet",
                "desired_environment_name": "mainnet-hubs",
                "placement_mismatch": True,
                "resolution": {"source": "hub-control-name"},
            },
        )

    assert exc_info.value.code == "HUB_COOLIFY_ENVIRONMENT_MISMATCH"
    assert "mainnet-hubs" in exc_info.value.message


def _resume_prep(ctx: HubContext, hub_id: str = "mainneta-hub1") -> dict:
    return add_hub.prep(
        ctx, "mainnet", hub_id,
        chain_verifier=lambda _contract: {"verified": True, "reason": "current-chain"},
        deployment_inspector=lambda _target: {"present": False, "application_uuid": None},
    )


def test_add_hub_prep_reuses_wallet_claim_from_failed_do(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    first = _resume_prep(ctx)
    operation_id = first["details"]["operation_id"]
    old = require_operation(ctx, "mainnet", operation_id)
    # A failed do already reserved the wallet, persisted its public identity,
    # and recorded failure evidence.  Never erase any of those records.
    assigned = "networks.mainnet.hub_admin_assignments.mainneta-hub1"
    updated_target = dict(old["target"], hub_admin_address=first["details"]["hub_admin_candidate_address"],
                          hub_admin_private_state_path=assigned)
    update_operation(ctx, "mainnet", operation_id, target=updated_target,
                     last_deployment_result={"status": "failed", "marker": "preserve-me"})
    frozen = require_operation(ctx, "mainnet", operation_id)
    retry = _resume_prep(ctx)
    assert retry["details"]["operation_id"] == operation_id
    assert require_operation(ctx, "mainnet", operation_id) == frozen
    assert retry["status"] == "prepared"


def test_add_hub_prep_refuses_changed_frozen_target(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    first = _resume_prep(ctx)
    operation_id = first["details"]["operation_id"]
    original = require_operation(ctx, "mainnet", operation_id)
    target = dict(original["target"], rpc_url="https://untrusted.invalid")
    update_operation(ctx, "mainnet", operation_id, target=target)
    frozen = require_operation(ctx, "mainnet", operation_id)
    with pytest.raises(HubControlError) as excinfo:
        _resume_prep(ctx)
    assert require_operation(ctx, "mainnet", operation_id) == frozen


def test_add_hub_prep_refuses_wallet_assignment_mismatch(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    first = _resume_prep(ctx)
    operation_id = first["details"]["operation_id"]
    original = require_operation(ctx, "mainnet", operation_id)
    target = dict(original["target"], hub_admin_address="0x" + "23" * 20,
                  hub_admin_private_state_path="networks.mainnet.hub_admin_assignments.mainneta-hub1")
    update_operation(ctx, "mainnet", operation_id, target=target)
    frozen = require_operation(ctx, "mainnet", operation_id)
    with pytest.raises(HubControlError) as excinfo:
        _resume_prep(ctx)
    assert require_operation(ctx, "mainnet", operation_id) == frozen


def test_add_hub_prep_refuses_accepted_prestate_change_on_id_collision(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    first = _resume_prep(ctx)
    operation_id = first["details"]["operation_id"]
    update_operation(ctx, "mainnet", operation_id, accepted_prestate={"generation": 9})
    with pytest.raises(HubControlError) as excinfo:
        _resume_prep(ctx)


def test_add_hub_prep_refuses_finalized_record_reuse(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    first = _resume_prep(ctx)
    operation_id = first["details"]["operation_id"]
    update_operation(ctx, "mainnet", operation_id, stage="finalized")
    with pytest.raises(HubControlError) as excinfo:
        _resume_prep(ctx)

    assert excinfo.value.code == "HUB_OPERATION_CONFLICT"


def _prepared_mainneta(ctx: HubContext, *, inspector=None) -> str:
    return add_hub.prep(
        ctx, "mainnet", "mainneta-hub1",
        chain_verifier=lambda _contract: {"verified": True, "reason": "test-chain-proof"},
        deployment_inspector=inspector or (lambda _target: {"present": False}),
    )["details"]["operation_id"]


def test_repeat_prep_reuses_identical_add_hub_operation(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    operation_id = _prepared_mainneta(ctx)
    before = require_operation(ctx, "mainnet", operation_id)
    assert _prepared_mainneta(ctx) == operation_id
    assert require_operation(ctx, "mainnet", operation_id) == before


def test_repeat_prep_preserves_hub_admin_committed_during_failed_do(tmp_path: Path) -> None:
    from tools.hub_control.common.state import update_operation

    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    operation_id = _prepared_mainneta(ctx)
    original = require_operation(ctx, "mainnet", operation_id)
    address = add_hub.preview_admin({}, network="mainnet", hub_id="mainneta-hub1")["address"]
    assigned_target = dict(original["target"])
    assigned_target.update({
        "hub_admin_address": address,
        "hub_admin_private_state_path": "networks.mainnet.hub_admin_assignments.mainneta-hub1",
    })
    update_operation(
        ctx, "mainnet", operation_id, target=assigned_target,
        last_deployment_result={"status": "failed", "error": "synthetic remote failure"},
    )
    prepared_again = _prepared_mainneta(ctx)
    assert prepared_again == operation_id
    saved = require_operation(ctx, "mainnet", operation_id)
    assert saved["stage"] == "prepared"
    assert saved["target"]["hub_admin_address"] == address
    assert saved["target"]["hub_admin_private_state_path"] == assigned_target["hub_admin_private_state_path"]
    assert saved["last_deployment_result"] == {"status": "failed", "error": "synthetic remote failure"}
    assert "private_key" not in json.dumps(saved)


def test_repeat_prep_can_adopt_newly_discovered_same_hub_application(tmp_path: Path) -> None:
    from tools.hub_control.common.state import update_operation

    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    operation_id = _prepared_mainneta(ctx)
    original = require_operation(ctx, "mainnet", operation_id)
    assigned_target = dict(original["target"])
    assigned_target["hub_admin_address"] = add_hub.preview_admin({}, network="mainnet", hub_id="mainneta-hub1")["address"]
    update_operation(ctx, "mainnet", operation_id, target=assigned_target)

    resumed = _prepared_mainneta(ctx, inspector=lambda _target: {
        "present": True, "application_uuid": "app-created-during-previous-do",
        "resolution": {"source": "hub-control-name"},
    })
    saved = require_operation(ctx, "mainnet", resumed)
    assert saved["target"]["application_uuid"] == "app-created-during-previous-do"
    assert saved["target"]["hub_admin_address"] == assigned_target["hub_admin_address"]


def test_repeat_prep_refuses_changed_frozen_target_without_mutating_receipt(tmp_path: Path) -> None:
    from tools.hub_control.common.state import update_operation

    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    operation_id = _prepared_mainneta(ctx)
    original = require_operation(ctx, "mainnet", operation_id)
    tampered = dict(original["target"])
    tampered["dockerfile_location"] = "/different.Dockerfile"
    update_operation(ctx, "mainnet", operation_id, target=tampered)
    before = require_operation(ctx, "mainnet", operation_id)
    with pytest.raises(HubControlError) as exc:
        _prepared_mainneta(ctx)
    assert exc.value.code == "HUB_OPERATION_CONFLICT"
    assert require_operation(ctx, "mainnet", operation_id) == before


def test_repeat_prep_refuses_changed_admin_identity(tmp_path: Path) -> None:
    from tools.hub_control.common.state import update_operation

    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    operation_id = _prepared_mainneta(ctx)
    original = require_operation(ctx, "mainnet", operation_id)
    target = dict(original["target"])
    target["hub_admin_address"] = "0x" + "ab" * 20
    update_operation(ctx, "mainnet", operation_id, target=target)
    with pytest.raises(HubControlError) as exc:
        _prepared_mainneta(ctx)
    assert exc.value.code == "HUB_OPERATION_CONFLICT"
    assert require_operation(ctx, "mainnet", operation_id)["target"]["hub_admin_address"] == target["hub_admin_address"]


def test_repeat_prep_preserves_already_deployed_stage(tmp_path: Path) -> None:
    from tools.hub_control.common.state import update_operation

    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    operation_id = _prepared_mainneta(ctx)
    update_operation(ctx, "mainnet", operation_id, stage="deployed", deployment_result={"application_uuid": "app-a"})
    before = require_operation(ctx, "mainnet", operation_id)
    assert _prepared_mainneta(ctx) == operation_id
    assert require_operation(ctx, "mainnet", operation_id) == before


def test_repeat_prep_rejects_different_accepted_prestate_even_with_same_operation_id(tmp_path: Path) -> None:
    from tools.hub_control.common.state import update_operation

    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    operation_id = _prepared_mainneta(ctx)
    update_operation(ctx, "mainnet", operation_id, accepted_prestate={"generation": 0, "hubs": []})
    with pytest.raises(HubControlError) as exc:
        _prepared_mainneta(ctx)
    assert exc.value.code == "HUB_OPERATION_CONFLICT"


def test_repeat_prep_rejects_replacement_of_frozen_coolify_application(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    operation_id = _prepared_mainneta(ctx, inspector=lambda _target: {
        "present": True, "application_uuid": "app-original",
    })
    with pytest.raises(HubControlError) as exc:
        _prepared_mainneta(ctx, inspector=lambda _target: {
            "present": True, "application_uuid": "app-different",
        })
    assert exc.value.code == "HUB_OPERATION_CONFLICT"
    assert require_operation(ctx, "mainnet", operation_id)["target"]["application_uuid"] == "app-original"


def test_add_hub_retry_after_failed_do_preserves_assigned_identity(tmp_path: Path, monkeypatch) -> None:
    from tools.mother.common.ethereum_identity import private_key_to_address
    key = "0x" + "01".zfill(64)
    monkeypatch.setattr(add_hub, "reserve_admin", lambda *a, **kw: {
        "address": private_key_to_address(key), "private_key": key,
        "private_state_path": "networks.mainnet.hub_admin_assignments.mainneta-hub1",
    })
    ctx = _ctx(tmp_path)
    _seed_dependencies(ctx)
    first = _resume_prep(ctx)
    operation_id = first["details"]["operation_id"]

    def failing_deployer(_target):
        raise HubControlError("HUB_DEPLOY_TEST_FAILURE", "simulated failure after wallet claim")

    with pytest.raises(HubControlError) as exc:
        add_hub.do(ctx, "mainnet", operation_id, deployer=failing_deployer)
    assert exc.value.code == "HUB_DEPLOY_TEST_FAILURE"
    claimed = require_operation(ctx, "mainnet", operation_id)
    expected_address = claimed["target"]["hub_admin_address"]
    assert claimed["stage"] == "prepared"
    assert claimed["target"]["hub_admin_private_state_path"] == "networks.mainnet.hub_admin_assignments.mainneta-hub1"
    assert "private_key" not in json.dumps(claimed)

    retry = _resume_prep(ctx)
    assert retry["details"]["operation_id"] == operation_id
    assert require_operation(ctx, "mainnet", operation_id) == claimed
    deployed = add_hub.do(
        ctx, "mainnet", operation_id,
        deployer=lambda target: {"application_uuid": "hub-a", "action": "created", "bridge_signer": {"address": target["hub_admin_address"]}},
        observer=_observer,
    )
    assert deployed["status"] == "deployed"
    assert deployed["details"]["hub_admin_address"] == expected_address
    assert require_operation(ctx, "mainnet", operation_id)["stage"] == "deployed"
