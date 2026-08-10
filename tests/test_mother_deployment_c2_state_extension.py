from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_c2_state_extension import (
    MotherDeploymentC2StateExtensionError,
    build_c2_state_extension_release,
    execute_c2_state_extension_release,
    inspect_c2_state_extension_release,
    stage_c2_state_extension,
    verify_c2_state_extension_evidence,
    verify_c2_state_extension_release,
    verify_c2_state_extension_transaction,
    write_c2_state_extension_release,
    write_c2_state_extension_transaction,
)
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
    prepare_private_state_successor,
    read_private_state,
    replace_verified_private_state,
    replace_verified_starter_private_state,
)
from tools.mother.common.starter_identity import reserve_starter_identity


def _operation(name: str) -> OperationIdentity:
    return OperationIdentity(
        operation_id=name,
        request_id=f"{name}-request",
        network="mainnet",
        operation_kind="MOTHER-OP-ADD-NODE",
    )


def _base_document() -> dict:
    return {
        "kind": "main_computer.mother.private_state.v1",
        "networks": {
            "mainnet": {
                "chain_id": 42424240,
                "coolify": {
                    "controllers": {
                        "coolify-a": {
                            "api_token": "1|THISISASECRETTOKENVALUEAAAAAAAA",
                            "enabled": True,
                            "observed_environments": {},
                            "project_uuid": "project-a",
                            "server_uuid": "server-a",
                            "url": "http://coolify-a.invalid/",
                        },
                        "coolify-c": {
                            "api_token": "1|THISISASECRETTOKENVALUECCCCCCCC",
                            "enabled": True,
                            "observed_environments": {},
                            "project_uuid": "project-c",
                            "server_uuid": "server-c",
                            "url": "http://coolify-c.invalid/",
                        },
                    },
                    "mutation_authority": "observe-only",
                },
                "deployment": {
                    "mode": "clean-start",
                    "status": "awaiting-offline-plan",
                    "targets": {
                        "mainneta-super1": {
                            "controller_ref": "networks.mainnet.coolify.controllers.coolify-a",
                            "desired_environment_name": "mainnet",
                            "desired_service_name": "mainneta-super1",
                            "hub_admin_address": None,
                            "hub_admin_private_key_path": "networks.mainnet.node_seed_material.mainneta-super1.wallets.hub_admin.private_key",
                            "key_material_status": "missing-from-allfather-source",
                            "live_resource_uuid": None,
                            "status": "absent-awaiting-redeployment",
                        },
                        "mainnetc-super1": {
                            "controller_ref": "networks.mainnet.coolify.controllers.coolify-c",
                            "desired_environment_name": "mainnet",
                            "desired_service_name": "mainnetc-super1",
                            "hub_admin_address": None,
                            "hub_admin_private_key_path": "networks.mainnet.node_seed_material.mainnetc-super1.wallets.hub_admin.private_key",
                            "key_material_status": "missing-from-allfather-source",
                            "live_resource_uuid": None,
                            "status": "absent-awaiting-redeployment",
                        },
                    },
                },
                "foundationdb": {},
                "node_seed_material": {},
                "nodes": {},
                "validators": {},
                "wallets": {},
            }
        },
        "schema_version": 1,
    }


def _mature_state(tmp_path: Path):
    runtime = tmp_path / "runtime" / "state"
    paths = MotherPaths(runtime_state_root=runtime).resolve_private_state_paths()
    op = _operation("bootstrap")
    bootstrap = prepare_private_state_bootstrap(
        paths,
        _base_document(),
        updated_at="2026-08-01T00:00:00Z",
        updated_by_action_id=op.operation_id,
        operation=op,
    )
    install_verified_private_state(paths, bootstrap, None, operation=op)
    current = read_private_state(paths, operation=_operation("read-gen1"))

    keys = iter("0x" + f"{index:064x}" for index in range(1, 20))
    reservation = reserve_starter_identity(
        yaml.safe_load(current.document_bytes),
        generated_at="2026-08-01T00:01:00Z",
        key_factory=lambda: next(keys),
    )
    successor = prepare_private_state_successor(
        current,
        reservation.document,
        updated_at="2026-08-01T00:01:00Z",
        updated_by_action_id="reserve-starter",
        operation=_operation("prepare-gen2"),
    )
    replace_verified_starter_private_state(
        paths,
        successor,
        current.binding,
        operation=_operation("install-gen2"),
    )
    mature = read_private_state(paths, operation=_operation("read-gen2"))
    assert mature.binding.generation == 2

    sentinel = paths.root / "actions" / "existing-history.json"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_bytes(b'{"preserve":true}')
    return paths, mature, sentinel


def _canary_file(paths) -> Path:
    path = paths.root / "evidence" / "deployment-validator-rpc-canary" / "canary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(
        {
            "kind": "test.canary",
            "status": "pass",
            "chain_state": "exact-cross-validator-verified",
        }
    )
    path.write_bytes(payload)
    return path


def _mock_canary(monkeypatch, canary_path: Path) -> None:
    import tools.mother.common.deployment_c2_state_extension as module

    file_sha = hashlib.sha256(canary_path.read_bytes()).hexdigest()

    def fake_verify(*args, **kwargs):
        return {
            "clean": True,
            "chain_state": "exact-cross-validator-verified",
            "next_phase": "validator-rpc-canary-execution-complete",
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_performed": False,
            "evidence_path": str(canary_path),
            "evidence_sha256": "ab" * 32,
            "evidence_file_sha256": file_sha,
        }

    monkeypatch.setattr(module, "verify_validator_rpc_canary_evidence", fake_verify)


def _keys():
    values = iter(("0x" + "a1" * 32, "0x" + "b2" * 32))
    return lambda: next(values)


def test_c2_state_extension_advances_once_preserves_history_and_compiles_soft_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, mature, sentinel = _mature_state(tmp_path)
    canary = _canary_file(paths)
    _mock_canary(monkeypatch, canary)

    stage = stage_c2_state_extension(
        paths,
        mature,
        canary,
        created_at="2026-08-09T20:00:00Z",
        now=datetime(2026, 8, 9, 20, 0, 0, tzinfo=timezone.utc),
        operation=_operation("stage-c2"),
        key_factory=_keys(),
    )
    assert stage.transaction["predecessor_binding"]["generation"] == 2
    assert stage.transaction["successor_binding"]["generation"] == 3
    assert stage.transaction["reservation"]["private_key_material_in_transaction"] is False
    assert "a1" * 32 not in json.dumps(stage.transaction)
    assert "b2" * 32 not in json.dumps(stage.transaction)

    tx_path, tx_sha = write_c2_state_extension_transaction(
        paths,
        stage,
        operation=_operation("write-c2"),
    )
    verified_tx = verify_c2_state_extension_transaction(
        paths,
        mature,
        tx_path,
        now=datetime(2026, 8, 9, 20, 0, 30, tzinfo=timezone.utc),
        operation=_operation("verify-c2"),
    )
    assert verified_tx["clean"] is True
    assert verified_tx["transaction_sha256"] == tx_sha
    assert verified_tx["validator_mutation_count"] == 0

    release = build_c2_state_extension_release(
        paths,
        mature,
        tx_path,
        acknowledge_transaction_sha256=tx_sha,
        created_at="2026-08-09T20:01:00Z",
        now=datetime(2026, 8, 9, 20, 1, 0, tzinfo=timezone.utc),
        operation=_operation("release-c2"),
    )
    release_path, release_sha = write_c2_state_extension_release(
        paths,
        release,
        operation=_operation("write-release"),
    )
    verified_release = verify_c2_state_extension_release(
        paths,
        mature,
        release_path,
        now=datetime(2026, 8, 9, 20, 1, 30, tzinfo=timezone.utc),
        operation=_operation("verify-release"),
    )
    assert verified_release["clean"] is True
    assert verified_release["release_sha256"] == release_sha
    assert verified_release["private_state_update_authorized"] is True
    assert verified_release["network_access_authorized"] is False

    inspection = inspect_c2_state_extension_release(
        paths,
        mature,
        release_path,
        acknowledge_release_sha256=release_sha,
        now=datetime(2026, 8, 9, 20, 1, 40, tzinfo=timezone.utc),
        operation=_operation("inspect-release"),
    )
    assert inspection["release_already_claimed"] is False
    assert inspection["private_state_updated"] is False
    assert sentinel.read_bytes() == b'{"preserve":true}'

    result = execute_c2_state_extension_release(
        paths,
        mature,
        release_path,
        acknowledge_release_sha256=release_sha,
        now=datetime(2026, 8, 9, 20, 2, 0, tzinfo=timezone.utc),
        operation=_operation("execute-release"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["generation_advanced_exactly_once"] is True
    assert result["summary"]["predecessor_recovery_preserved"] is True
    assert result["summary"]["existing_state_outside_c2_preserved"] is True
    assert result["summary"]["service_mutation_count"] == 0
    assert result["summary"]["validator_vote_performed"] is False
    assert sentinel.read_bytes() == b'{"preserve":true}'

    current = read_private_state(paths, operation=_operation("read-gen3"))
    assert current.binding.generation == 3
    document = yaml.safe_load(current.document_bytes)
    assert document["networks"]["mainnet"]["nodes"]["mainnetc-super2"]["host"] == "coolify-c"
    assert (
        document["networks"]["mainnet"]["deployment"]["targets"]["mainnetc-super2"]["status"]
        == "absent-awaiting-redeployment"
    )

    verified_evidence = verify_c2_state_extension_evidence(
        paths,
        current,
        Path(result["evidence"]["path"]),
        now=datetime(2026, 8, 9, 20, 2, 10, tzinfo=timezone.utc),
        operation=_operation("verify-evidence"),
    )
    assert verified_evidence["clean"] is True
    assert verified_evidence["c2_deployment_plan_compiles"] is True
    assert verified_evidence["c2_mode"] == "soft"
    assert verified_evidence["next_phase"] == "preflight-c2-standby"


def test_stage_refuses_existing_c2_reservation(tmp_path: Path, monkeypatch) -> None:
    paths, mature, _ = _mature_state(tmp_path)
    canary = _canary_file(paths)
    _mock_canary(monkeypatch, canary)
    document = yaml.safe_load(mature.document_bytes)
    document["networks"]["mainnet"]["validators"]["mainnetc-super2"] = {
        "address": "0x" + "11" * 20,
        "private_key": "0x" + "22" * 32,
    }
    successor = prepare_private_state_successor(
        mature,
        document,
        updated_at="2026-08-09T19:00:00Z",
        updated_by_action_id="foreign-c2",
        operation=_operation("foreign-c2"),
    )
    replace_verified_private_state(
        paths,
        successor,
        mature.binding,
        operation=_operation("foreign-c2-install"),
    )
    current = read_private_state(paths, operation=_operation("read-foreign-c2"))
    with pytest.raises(MotherDeploymentC2StateExtensionError) as exc:
        stage_c2_state_extension(
            paths,
            current,
            canary,
            created_at="2026-08-09T20:00:00Z",
            operation=_operation("stage-refuse"),
            key_factory=_keys(),
        )
    assert exc.value.code == "MOTHER_DEPLOY_C2_STATE_EXTENSION_ALREADY_RESERVED"



def test_general_private_state_replacer_recovers_interrupted_partial_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, mature, sentinel = _mature_state(tmp_path)
    document = yaml.safe_load(mature.document_bytes)
    document["networks"]["mainnet"]["deployment"]["status"] = "test-successor"
    successor = prepare_private_state_successor(
        mature,
        document,
        updated_at="2026-08-09T19:30:00Z",
        updated_by_action_id="interrupted-test",
        operation=_operation("prepare-interrupted"),
    )

    import tools.mother.common.private_state as private_state_module

    original_replace = private_state_module.os.replace
    calls = {"count": 0, "raised": False}

    def interrupted_replace(src, dst):
        calls["count"] += 1
        if calls["count"] == 2 and not calls["raised"]:
            calls["raised"] = True
            raise KeyboardInterrupt("simulated process interruption")
        return original_replace(src, dst)

    monkeypatch.setattr(private_state_module.os, "replace", interrupted_replace)

    with pytest.raises(KeyboardInterrupt):
        replace_verified_private_state(
            paths,
            successor,
            mature.binding,
            operation=_operation("interrupt-swap"),
        )

    recovered = read_private_state(paths, operation=_operation("recover-interrupted"))
    assert recovered.binding == mature.binding
    assert sentinel.read_bytes() == b'{"preserve":true}'
    assert not (paths.root / "private-state-transitions").exists()

def test_cli_registers_c2_state_extension_commands() -> None:
    from tools import mother_deploy

    parser = mother_deploy._parser()
    commands = {
        "stage-c2-state-extension",
        "verify-c2-state-extension-transaction",
        "release-c2-state-extension",
        "verify-c2-state-extension-release",
        "apply-c2-state-extension",
        "verify-c2-state-extension-evidence",
    }
    for command in commands:
        args = parser.parse_args(
            [command]
            + (
                ["--canary-evidence", "x"]
                if command == "stage-c2-state-extension"
                else ["--transaction", "x"]
                if command == "verify-c2-state-extension-transaction"
                else [
                    "--transaction",
                    "x",
                    "--acknowledge-c2-state-extension-transaction-sha256",
                    "00",
                ]
                if command == "release-c2-state-extension"
                else ["--release", "x"]
                if command == "verify-c2-state-extension-release"
                else ["--release", "x", "--acknowledge-release-sha256", "00"]
                if command == "apply-c2-state-extension"
                else ["--evidence", "x"]
            )
        )
        assert args.command == command
