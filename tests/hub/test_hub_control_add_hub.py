from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.hub_control import add_hub
from tools.hub_control.common.errors import HubControlError
from tools.hub_control.common.models import HubContext
from tools.hub_control.common.state import read_accepted, require_operation


ROOT = Path(__file__).resolve().parents[2]


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
