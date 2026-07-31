from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from tools.mother.common.deployment_plan import build_starter_deployment_plan
from tools.mother.common.ethereum_identity import private_key_to_address, validate_identity
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
    read_private_state,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "tools" / "mother_identity.py"
TOKEN_A = "1|THISISASECRETTOKENVALUEAAAAAAAA"
TOKEN_C = "1|THISISASECRETTOKENVALUECCCCCCCC"
DEPLOYER_KEY = "0x" + "11" * 32
A_HUB_KEY = "0x" + "22" * 32
STALE_A_HUB_ADDRESS = "0x" + "99" * 20


def _operation(name: str) -> OperationIdentity:
    return OperationIdentity(
        operation_id=name,
        request_id=f"{name}-request",
        network="mainnet",
        operation_kind="MOTHER-OP-IDENTITY-ROTATION",
    )


def _document() -> dict[str, Any]:
    return {
        "kind": "main_computer.mother.private_state.v1",
        "networks": {
            "mainnet": {
                "chain_id": 42424240,
                "coolify": {
                    "controllers": {
                        "coolify-a": {
                            "api_token": TOKEN_A,
                            "enabled": True,
                            "observed_environments": {},
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
                            "hub_admin_address": STALE_A_HUB_ADDRESS,
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
                                "private_key": A_HUB_KEY,
                            }
                        }
                    }
                },
                "nodes": {},
                "validators": {},
                "wallets": {
                    "deployer": {
                        "address": None,
                        "private_key": DEPLOYER_KEY,
                    }
                },
            }
        },
        "schema_version": 1,
    }


def _install(tmp_path: Path) -> tuple[Path, Any]:
    runtime = tmp_path / "runtime" / "state"
    paths = MotherPaths(runtime_state_root=runtime).resolve_private_state_paths()
    operation = _operation("mother-identity-install")
    closure = prepare_private_state_bootstrap(
        paths,
        _document(),
        updated_at="2026-07-31T00:30:00Z",
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)
    return runtime, closure.binding


def _run(runtime: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(CLI),
            "reserve-starter",
            *args,
            "--runtime-state-root",
            str(runtime),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _read(runtime: Path):
    paths = MotherPaths(runtime_state_root=runtime).resolve_private_state_paths()
    return read_private_state(paths, operation=_operation("mother-identity-read"))


def test_dry_run_reports_exact_offline_reservations_without_writes(tmp_path: Path) -> None:
    runtime, binding = _install(tmp_path)
    before = sorted((path.relative_to(runtime), path.read_bytes()) for path in runtime.rglob("*") if path.is_file())
    result = _run(runtime)
    after = sorted((path.relative_to(runtime), path.read_bytes()) for path in runtime.rglob("*") if path.is_file())

    assert result.returncode == 0, result.stderr
    assert before == after
    assert "changes required: yes" in result.stdout
    assert "validator:mainneta-super1" in result.stdout
    assert "validator:mainnetc-super1" in result.stdout
    assert "hub-admin:mainnetc-super1" in result.stdout
    assert "would reserve nodes: mainneta-super1, mainnetc-super1" in result.stdout
    assert "would add Mother-owned first genesis: yes" in result.stdout
    assert "write performed: no (dry-run)" in result.stdout
    assert TOKEN_A not in result.stdout
    assert TOKEN_C not in result.stdout
    assert DEPLOYER_KEY not in result.stdout
    assert binding.generation == 1


def test_write_installs_generation_two_complete_starter_identity(tmp_path: Path) -> None:
    runtime, old_binding = _install(tmp_path)
    result = _run(runtime, "--write", "--updated-at", "2026-07-31T01:00:00Z")

    assert result.returncode == 0, result.stderr
    assert "write performed: yes" in result.stdout
    assert "stable read: passed" in result.stdout
    assert "installed generation: 2" in result.stdout
    assert TOKEN_A not in result.stdout
    assert TOKEN_C not in result.stdout
    assert DEPLOYER_KEY not in result.stdout

    state = _read(runtime)
    assert state.binding.generation == 2
    assert state.metadata.previous_content_hash == old_binding.content_hash
    assert {
        item.relative_path for item in state.recovery_objects
    } >= {
        "predecessor/generation-00000001/identity.private.yaml",
        "predecessor/generation-00000001/identity.private.meta.json",
        "predecessor/generation-00000001/private-recovery/manifest.json",
    }

    document = json.loads(state.canonical_object_bytes.decode("utf-8"))
    network = document["networks"]["mainnet"]
    assert set(network["wallets"]) == {"deployer", "captain", "o1", "o2", "o3"}
    for role, identity in network["wallets"].items():
        assert identity["address"].lower() == private_key_to_address(identity["private_key"]).lower(), role
    for node, identity in network["validators"].items():
        validate_identity(identity, path=f"validators.{node}")
    assert set(network["nodes"]) == {"mainneta-super1", "mainnetc-super1"}
    assert network["nodes"]["mainneta-super1"]["host"] == "coolify-a"
    assert network["nodes"]["mainnetc-super1"]["host"] == "coolify-c"
    assert network["deployment"]["targets"]["mainneta-super1"]["hub_admin_address"] == private_key_to_address(A_HUB_KEY)
    assert network["deployment"]["targets"]["mainneta-super1"]["hub_admin_address"] != STALE_A_HUB_ADDRESS
    c_hub = network["node_seed_material"]["mainnetc-super1"]["wallets"]["hub_admin"]
    assert c_hub["address"].lower() == private_key_to_address(c_hub["private_key"]).lower()
    assert all(target["key_material_status"] == "present" for target in network["deployment"]["targets"].values())
    assert network["genesis"] == {
        "alloc_accounts": [{"ref": "networks.mainnet.wallets.captain"}],
        "first_topology_mode": "initial",
        "qbft": {"blockperiodseconds": 2, "epochlength": 30000},
        "source": "mother-private",
    }

    plan = build_starter_deployment_plan(state)
    assert plan["summary"]["blocker_codes"] == [
        "MOTHER_DEPLOY_EXECUTOR_NOT_IMPLEMENTED",
        "MOTHER_DEPLOY_MUTATION_AUTHORITY_DISABLED",
    ]
    assert plan["summary"]["blocker_count"] == 2


def test_second_write_is_idempotent_and_does_not_create_generation_three(tmp_path: Path) -> None:
    runtime, _binding = _install(tmp_path)
    first = _run(runtime, "--write")
    assert first.returncode == 0, first.stderr
    before = _read(runtime)

    second = _run(runtime, "--write")
    after = _read(runtime)
    assert second.returncode == 0, second.stderr
    assert "changes required: no" in second.stdout
    assert "write performed: no (already reserved)" in second.stdout
    assert after.binding == before.binding
    assert after.binding.generation == 2


def test_starter_rotation_refuses_unrelated_durable_mother_state(tmp_path: Path) -> None:
    runtime, old_binding = _install(tmp_path)
    extra = runtime / "mother" / "actions" / "existing-action.json"
    extra.parent.mkdir(parents=True)
    extra.write_text("{}", encoding="utf-8")

    result = _run(runtime, "--write")
    assert result.returncode == 4
    assert "MOTHER_STATE_PRIVATE_STATE_CONFLICT" in result.stderr
    assert "non-starter durable state" in result.stderr
    assert _read(runtime).binding == old_binding


def test_known_ethereum_identity_vector() -> None:
    assert private_key_to_address(A_HUB_KEY) == "0x1563915e194D8CfBA1943570603F7606A3115508"


def test_fresh_allfather_initializer_installs_complete_starter_generation_one(tmp_path: Path) -> None:
    import yaml

    source = tmp_path / "all_father.private.yaml"
    runtime = tmp_path / "fresh" / "runtime" / "state"
    source.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "kind": "main_computer.all_father.private_state.v1",
                "coolify": {
                    "hosts": {
                        "A": {
                            "name": "coolify-a",
                            "droplet_hostname": "testnet",
                            "public_ip": "198.199.75.153",
                            "vpn_ip": "10.116.0.3",
                            "url": "http://198.199.75.153:8000/",
                            "api_token": TOKEN_A,
                            "project_name": "My first project",
                        },
                        "C": {
                            "name": "coolify-c",
                            "droplet_hostname": "vnode1",
                            "public_ip": "159.203.184.182",
                            "vpn_ip": "10.116.0.2",
                            "url": "http://159.203.184.182:8000/",
                            "api_token": TOKEN_C,
                            "project_name": "My first project",
                        },
                    }
                },
                "networks": {
                    "mainnet": {
                        "huddle": {
                            "hub_admins": {
                                "active": {
                                    "mainneta-super1": {
                                        "host_slot": "A",
                                        "coolify_server": "coolify-a",
                                        "address": STALE_A_HUB_ADDRESS,
                                        "private_key_path": "networks.mainnet.node_seed_material.mainneta-super1.wallets.hub_admin.private_key",
                                    },
                                    "mainnetc-super1": {
                                        "host_slot": "C",
                                        "coolify_server": "coolify-c",
                                        "address": "0x" + "33" * 20,
                                        "private_key_path": "networks.mainnet.node_seed_material.mainnetc-super1.wallets.hub_admin.private_key",
                                    },
                                }
                            }
                        },
                        "wallets": {
                            "deployer": {
                                "address": None,
                                "private_key": DEPLOYER_KEY,
                            }
                        },
                        "foundationdb": {
                            "cluster_description": "main_computer_mainnet_allfather",
                            "cluster_id": "59482a4a14b28bd4",
                            "coordinator_policy": "first-node-then-expand",
                            "reconfigure_after_join": True,
                        },
                        "node_seed_material": {
                            "mainneta-super1": {
                                "wallets": {
                                    "hub_admin": {
                                        "address": None,
                                        "private_key": A_HUB_KEY,
                                    }
                                }
                            }
                        },
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "mother_initialize_from_allfather.py"),
            "--source",
            str(source),
            "--runtime-state-root",
            str(runtime),
            "--updated-at",
            "2026-07-31T01:30:00Z",
            "--write",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "installed generation: 1" in result.stdout
    assert "starter identities generated:" in result.stdout
    assert TOKEN_A not in result.stdout
    assert TOKEN_C not in result.stdout
    assert not (runtime / "mother-bootstrap.private.yaml").exists()

    state = _read(runtime)
    plan = build_starter_deployment_plan(state)
    assert state.binding.generation == 1
    assert plan["summary"]["blocker_codes"] == [
        "MOTHER_DEPLOY_EXECUTOR_NOT_IMPLEMENTED",
        "MOTHER_DEPLOY_MUTATION_AUTHORITY_DISABLED",
    ]
