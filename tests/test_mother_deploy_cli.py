from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from tools.mother.common.deployment_plan import (
    MotherDeploymentPlanError,
    build_starter_deployment_plan,
)
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
    read_private_state,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "tools" / "mother_deploy.py"
TOKEN_A = "1|THISISASECRETTOKENVALUEAAAAAAAA"
TOKEN_C = "1|THISISASECRETTOKENVALUECCCCCCCC"
PRIVATE_KEY = "0x" + "11" * 32


def _operation(name: str, *, kind: str = "MOTHER-OP-DIAGNOSE") -> OperationIdentity:
    return OperationIdentity(
        operation_id=name,
        request_id=f"{name}-request",
        network="mainnet",
        operation_kind=kind,
    )


def _document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "main_computer.mother.private_state.v1",
        "networks": {
            "mainnet": {
                "chain_id": 42424240,
                "coolify": {
                    "controllers": {
                        "coolify-a": {
                            "api_token": TOKEN_A,
                            "enabled": True,
                            "observed_environments": {
                                "hub-site": {
                                    "available_for_mainnet_nodes": False,
                                    "environment_uuid": "hub-environment",
                                    "reserved_for": "existing-hub-service",
                                }
                            },
                            "project_uuid": "project-a",
                            "server_uuid": "server-a",
                            "url": "http://127.0.0.1:65531/",
                        },
                        "coolify-c": {
                            "api_token": TOKEN_C,
                            "enabled": True,
                            "observed_environments": {},
                            "project_uuid": "project-c",
                            "server_uuid": "server-c",
                            "url": "http://127.0.0.1:65532/",
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
                            "hub_admin_address": "0x" + "22" * 20,
                            "hub_admin_private_key_path": "networks.mainnet.node_seed_material.mainneta-super1.wallets.hub_admin.private_key",
                            "key_material_status": "present",
                            "live_resource_uuid": None,
                            "status": "absent-awaiting-redeployment",
                        },
                        "mainnetc-super1": {
                            "controller_ref": "networks.mainnet.coolify.controllers.coolify-c",
                            "desired_environment_name": "mainnet",
                            "desired_service_name": "mainnetc-super1",
                            "hub_admin_address": "0x" + "33" * 20,
                            "hub_admin_private_key_path": "networks.mainnet.node_seed_material.mainnetc-super1.wallets.hub_admin.private_key",
                            "key_material_status": "missing-from-allfather-source",
                            "live_resource_uuid": None,
                            "status": "absent-awaiting-redeployment",
                        },
                    },
                },
                "foundationdb": {},
                "node_seed_material": {
                    "mainneta-super1": {
                        "wallets": {
                            "hub_admin": {
                                "address": None,
                                "private_key": PRIVATE_KEY,
                            }
                        }
                    }
                },
                "nodes": {},
                "validators": {},
                "wallets": {"deployer": {"address": None, "private_key": PRIVATE_KEY}},
            }
        },
    }


def _install(tmp_path: Path, document: dict[str, Any] | None = None) -> Path:
    runtime = tmp_path / "runtime" / "state"
    paths = MotherPaths(runtime_state_root=runtime).resolve_private_state_paths()
    operation = _operation("mother-deploy-install")
    closure = prepare_private_state_bootstrap(
        paths,
        document or _document(),
        updated_at="2026-07-31T00:30:00Z",
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)
    return runtime


def _read(runtime: Path):
    paths = MotherPaths(runtime_state_root=runtime).resolve_private_state_paths()
    return read_private_state(paths, operation=_operation("mother-deploy-read"))


def _run(runtime: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(CLI),
            "plan",
            *args,
            "--runtime-state-root",
            str(runtime),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_plan_is_secret_safe_offline_and_orders_initial_then_soft(tmp_path: Path) -> None:
    runtime = _install(tmp_path)
    result = _run(runtime)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert payload["kind"] == "main_computer.mother.deployment_plan.v1"
    assert payload["mother_binding"]["generation"] == 1
    assert payload["network"] == "mainnet"
    assert payload["chain_id"] == 42424240
    assert payload["policy"] == {
        "legacy_allfather_executor_invoked": False,
        "legacy_qbft_executor_invoked": False,
        "live_mutation_performed": False,
        "network_access_performed": False,
        "secrets_in_output": False,
    }
    assert [(item["node"], item["mode"]) for item in payload["sequence"]] == [
        ("mainneta-super1", "initial"),
        ("mainnetc-super1", "soft"),
    ]
    assert TOKEN_A not in result.stdout
    assert TOKEN_C not in result.stdout
    assert PRIVATE_KEY not in result.stdout
    assert payload["sequence"][0]["controller"]["has_api_token"] is True
    assert payload["summary"]["plan_valid"] is True
    assert payload["summary"]["ready_for_execution"] is False


def test_plan_reports_current_identity_and_executor_blockers(tmp_path: Path) -> None:
    runtime = _install(tmp_path)
    payload = json.loads(_run(runtime).stdout)

    codes = set(payload["summary"]["blocker_codes"])
    assert {
        "MOTHER_DEPLOY_EXECUTOR_NOT_IMPLEMENTED",
        "MOTHER_DEPLOY_FIRST_GENESIS_MISSING",
        "MOTHER_DEPLOY_HUB_ADMIN_KEY_MISSING",
        "MOTHER_DEPLOY_KEY_MATERIAL_INCOMPLETE",
        "MOTHER_DEPLOY_MUTATION_AUTHORITY_DISABLED",
        "MOTHER_DEPLOY_NODE_RESERVATION_MISSING",
        "MOTHER_DEPLOY_VALIDATOR_IDENTITY_MISSING",
    } <= codes

    first_codes = {item["code"] for item in payload["sequence"][0]["blockers"]}
    second_codes = {item["code"] for item in payload["sequence"][1]["blockers"]}
    assert "MOTHER_DEPLOY_FIRST_GENESIS_MISSING" in first_codes
    assert "MOTHER_DEPLOY_HUB_ADMIN_KEY_MISSING" not in first_codes
    assert "MOTHER_DEPLOY_HUB_ADMIN_KEY_MISSING" in second_codes
    assert "MOTHER_DEPLOY_KEY_MATERIAL_INCOMPLETE" in second_codes


def test_selected_second_target_keeps_soft_mode(tmp_path: Path) -> None:
    runtime = _install(tmp_path)
    result = _run(runtime, "--node", "mainnetc-super1")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert [(item["node"], item["mode"]) for item in payload["sequence"]] == [
        ("mainnetc-super1", "soft")
    ]


def test_require_ready_returns_one_without_mutating(tmp_path: Path) -> None:
    runtime = _install(tmp_path)
    before = sorted((path.relative_to(runtime), path.read_bytes()) for path in runtime.rglob("*") if path.is_file())
    result = _run(runtime, "--require-ready")
    after = sorted((path.relative_to(runtime), path.read_bytes()) for path in runtime.rglob("*") if path.is_file())
    assert result.returncode == 1
    assert before == after


def test_unknown_target_is_rejected(tmp_path: Path) -> None:
    runtime = _install(tmp_path)
    result = _run(runtime, "--node", "mainnetd-super1")
    assert result.returncode == 2
    assert "MOTHER_DEPLOY_TARGET_UNKNOWN" in result.stderr
    assert result.stdout == ""


def test_malformed_controller_reference_is_rejected(tmp_path: Path) -> None:
    document = _document()
    document["networks"]["mainnet"]["deployment"]["targets"]["mainneta-super1"]["controller_ref"] = (
        "networks.mainnet.coolify.controllers.coolify-a.extra"
    )
    runtime = _install(tmp_path, document)
    result = _run(runtime)
    assert result.returncode == 2
    assert "MOTHER_DEPLOY_CONTROLLER_REF_INVALID" in result.stderr


def test_clean_start_contradiction_is_rejected(tmp_path: Path) -> None:
    document = _document()
    document["networks"]["mainnet"]["deployment"]["targets"]["mainneta-super1"]["live_resource_uuid"] = "already-live"
    runtime = _install(tmp_path, document)
    result = _run(runtime)
    assert result.returncode == 2
    assert "MOTHER_DEPLOY_CLEAN_START_CONTRADICTION" in result.stderr


def test_direct_planner_does_not_import_or_call_network_clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _install(tmp_path)
    state = _read(runtime)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("deployment planning must not perform network access")

    monkeypatch.setattr("socket.create_connection", forbidden)
    plan = build_starter_deployment_plan(state)
    assert plan["policy"]["network_access_performed"] is False
