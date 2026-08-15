from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit

import yaml

from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_node_add_prep import build_node_add_prep_transaction
from tools.mother.common.ethereum_identity import private_key_to_address
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
    read_private_state,
)
from tools.mother.common.deployment_node_identity_reservation import (
    MotherDeploymentNodeIdentityReservationError,
    reserve_add_node_identity,
)
from tests.test_mother_deployment_executor import _Response


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "tools" / "mother_deploy.py"


def _operation(name: str) -> OperationIdentity:
    return OperationIdentity(
        operation_id=name,
        request_id=f"{name}-request",
        network="mainnet",
        operation_kind="MOTHER-OP-ADD-NODE",
    )


def _base_document() -> dict[str, Any]:
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
                            "url": "http://127.0.0.1:65531/",
                            "vpn_ip": "10.116.0.3",
                        },
                        "coolify-c": {
                            "api_token": "1|THISISASECRETTOKENVALUECCCCCCCC",
                            "enabled": True,
                            "observed_environments": {},
                            "project_uuid": "project-c",
                            "server_uuid": "server-c",
                            "url": "http://127.0.0.1:65532/",
                            "vpn_ip": "10.116.0.2",
                        },
                    },
                    "mutation_authority": "observe-only",
                },
                "deployment": {
                    "mode": "clean-start",
                    "status": "identity-reserved-awaiting-executor",
                    "targets": {
                        "mainneta-super1": {
                            "controller_ref": "networks.mainnet.coolify.controllers.coolify-a",
                            "desired_environment_name": "mainnet",
                            "desired_service_name": "mainneta-super1",
                            "hub_admin_address": private_key_to_address("0x" + "22" * 32),
                            "hub_admin_private_key_path": "networks.mainnet.node_seed_material.mainneta-super1.wallets.hub_admin.private_key",
                            "key_material_status": "present",
                            "live_resource_uuid": None,
                            "status": "absent-awaiting-redeployment",
                        },
                        "mainnetc-super1": {
                            "controller_ref": "networks.mainnet.coolify.controllers.coolify-c",
                            "desired_environment_name": "mainnet",
                            "desired_service_name": "mainnetc-super1",
                            "hub_admin_address": private_key_to_address("0x" + "44" * 32),
                            "hub_admin_private_key_path": "networks.mainnet.node_seed_material.mainnetc-super1.wallets.hub_admin.private_key",
                            "key_material_status": "present",
                            "live_resource_uuid": None,
                            "status": "absent-awaiting-redeployment",
                        },
                    },
                },
                "foundationdb": {},
                "genesis": {
                    "alloc_accounts": [{"ref": "networks.mainnet.wallets.captain"}],
                    "first_topology_mode": "initial",
                    "qbft": {"blockperiodseconds": 2, "epochlength": 30000},
                    "source": "mother-private",
                },
                "node_seed_material": {
                    "mainneta-super1": {
                        "wallets": {
                            "hub_admin": {
                                "address": private_key_to_address("0x" + "22" * 32),
                                "metadata": {
                                    "address_derivation": "secp256k1-keccak256-eip55",
                                    "generated_at": "2026-08-01T00:00:00Z",
                                    "generated_by": "test",
                                    "reason": "test A1 hub identity",
                                },
                                "private_key": "0x" + "22" * 32,
                            }
                        }
                    },
                    "mainnetc-super1": {
                        "wallets": {
                            "hub_admin": {
                                "address": private_key_to_address("0x" + "44" * 32),
                                "metadata": {
                                    "address_derivation": "secp256k1-keccak256-eip55",
                                    "generated_at": "2026-08-01T00:00:00Z",
                                    "generated_by": "test",
                                    "reason": "test C1 hub identity",
                                },
                                "private_key": "0x" + "44" * 32,
                            }
                        }
                    },
                },
                "nodes": {
                    "mainneta-super1": {
                        "guard_route_reservation": "mainneta-super1.guard",
                        "host": "coolify-a",
                        "hub_route_reservation": "mainneta-super1.hub",
                        "rpc_route_reservation": "mainneta-super1.rpc",
                        "validator_ref": "networks.mainnet.validators.mainneta-super1",
                    },
                    "mainnetc-super1": {
                        "guard_route_reservation": "mainnetc-super1.guard",
                        "host": "coolify-c",
                        "hub_route_reservation": "mainnetc-super1.hub",
                        "rpc_route_reservation": "mainnetc-super1.rpc",
                        "validator_ref": "networks.mainnet.validators.mainnetc-super1",
                    },
                },
                "validators": {
                    "mainneta-super1": {
                        "address": private_key_to_address("0x" + "11" * 32),
                        "private_key": "0x" + "11" * 32,
                    },
                    "mainnetc-super1": {
                        "address": private_key_to_address("0x" + "33" * 32),
                        "private_key": "0x" + "33" * 32,
                    },
                },
                "wallets": {
                    "captain": {
                        "address": private_key_to_address("0x" + "55" * 32),
                        "metadata": {
                            "address_derivation": "secp256k1-keccak256-eip55",
                            "generated_at": "2026-08-01T00:00:00Z",
                            "generated_by": "test",
                            "reason": "test captain",
                        },
                        "private_key": "0x" + "55" * 32,
                    }
                },
            }
        },
        "schema_version": 1,
    }


def _install_state(tmp_path: Path):
    runtime = tmp_path / "runtime" / "state"
    paths = MotherPaths(runtime_state_root=runtime).resolve_private_state_paths()
    op = _operation("bootstrap")
    closure = prepare_private_state_bootstrap(
        paths,
        _base_document(),
        updated_at="2026-08-01T00:00:00Z",
        updated_by_action_id=op.operation_id,
        operation=op,
    )
    install_verified_private_state(paths, closure, None, operation=op)
    return paths, read_private_state(paths, operation=_operation("read"))


def test_add_node_reserve_identity_creates_a2_sections_and_advances_once(tmp_path: Path) -> None:
    paths, private_state = _install_state(tmp_path)
    keys = iter(("0x" + "66" * 32, "0x" + "77" * 32))

    result = reserve_add_node_identity(
        paths,
        private_state,
        network="mainnet",
        node="mainneta-super2",
        host="coolify-a",
        execute=True,
        generated_at="2026-08-14T21:46:00Z",
        operation=_operation("reserve-a2"),
        key_factory=lambda: next(keys),
    )

    assert result["status"] == "pass"
    assert result["private_state_updated"] is True
    assert result["private_key_material_in_output"] is False
    assert result["generated_labels"] == ["validator:mainneta-super2", "hub-admin:mainneta-super2"]
    assert "66" * 32 not in json.dumps(result)
    assert "77" * 32 not in json.dumps(result)

    updated = read_private_state(paths, operation=_operation("read-updated"))
    assert updated.binding.generation == private_state.binding.generation + 1
    document = yaml.safe_load(updated.document_bytes)
    network = document["networks"]["mainnet"]

    assert network["nodes"]["mainneta-super2"] == {
        "guard_route_reservation": "mainneta-super2.guard",
        "host": "coolify-a",
        "hub_route_reservation": "mainneta-super2.hub",
        "rpc_route_reservation": "mainneta-super2.rpc",
        "validator_ref": "networks.mainnet.validators.mainneta-super2",
    }
    assert network["deployment"]["targets"]["mainneta-super2"]["controller_ref"] == "networks.mainnet.coolify.controllers.coolify-a"
    assert network["deployment"]["targets"]["mainneta-super2"]["desired_service_name"] == "mainneta-super2"
    assert network["validators"]["mainneta-super2"]["address"] == private_key_to_address("0x" + "66" * 32)
    assert network["node_seed_material"]["mainneta-super2"]["wallets"]["hub_admin"]["address"] == private_key_to_address("0x" + "77" * 32)


def test_add_node_reserve_identity_existing_identity_is_noop(tmp_path: Path) -> None:
    paths, private_state = _install_state(tmp_path)
    first_keys = iter(("0x" + "66" * 32, "0x" + "77" * 32))
    reserve_add_node_identity(
        paths,
        private_state,
        network="mainnet",
        node="mainneta-super2",
        host="coolify-a",
        execute=True,
        generated_at="2026-08-14T21:46:00Z",
        operation=_operation("reserve-a2"),
        key_factory=lambda: next(first_keys),
    )
    updated = read_private_state(paths, operation=_operation("read-updated"))

    second = reserve_add_node_identity(
        paths,
        updated,
        network="mainnet",
        node="mainneta-super2",
        host="coolify-a",
        execute=True,
        generated_at="2026-08-14T21:47:00Z",
        operation=_operation("reserve-a2-again"),
        key_factory=lambda: "0x" + "88" * 32,
    )

    reread = read_private_state(paths, operation=_operation("read-reread"))
    assert reread.binding == updated.binding
    assert second["identity_already_reserved"] is True
    assert second["private_state_update_required"] is False
    assert second["private_state_updated"] is False
    assert second["validator_address"] == private_key_to_address("0x" + "66" * 32)


def test_add_node_reserve_identity_fails_on_partial_existing_node(tmp_path: Path) -> None:
    paths, private_state = _install_state(tmp_path)
    document = yaml.safe_load(private_state.document_bytes)
    document["networks"]["mainnet"]["nodes"]["mainneta-super2"] = {
        "guard_route_reservation": "mainneta-super2.guard",
        "host": "coolify-a",
        "hub_route_reservation": "mainneta-super2.hub",
        "rpc_route_reservation": "mainneta-super2.rpc",
        "validator_ref": "networks.mainnet.validators.mainneta-super2",
    }

    # Install a deliberately partial successor to ensure the reservation command
    # refuses to silently repair ambiguous hand-edited state.
    from tools.mother.common.private_state import prepare_private_state_successor, replace_verified_private_state

    successor = prepare_private_state_successor(
        private_state,
        document,
        updated_at="2026-08-14T21:45:00Z",
        updated_by_action_id="partial",
        operation=_operation("partial-successor"),
    )
    replace_verified_private_state(paths, successor, private_state.binding, operation=_operation("install-partial"))
    partial = read_private_state(paths, operation=_operation("read-partial"))

    try:
        reserve_add_node_identity(
            paths,
            partial,
            network="mainnet",
            node="mainneta-super2",
            host="coolify-a",
            execute=False,
            generated_at="2026-08-14T21:46:00Z",
            operation=_operation("reserve-partial"),
            key_factory=lambda: "0x" + "66" * 32,
        )
    except MotherDeploymentNodeIdentityReservationError as exc:
        assert exc.code == "MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_CONFLICT"
    else:
        raise AssertionError("partial identity reservation was accepted")


def test_add_node_reserve_identity_repairs_missing_deployment_target_only(tmp_path: Path) -> None:
    paths, private_state = _install_state(tmp_path)
    document = yaml.safe_load(private_state.document_bytes)
    network = document["networks"]["mainnet"]
    node = "mainneta-super2"
    validator_key = "0x" + "66" * 32
    hub_key = "0x" + "77" * 32

    network["nodes"][node] = {
        "guard_route_reservation": f"{node}.guard",
        "host": "coolify-a",
        "hub_route_reservation": f"{node}.hub",
        "rpc_route_reservation": f"{node}.rpc",
        "validator_ref": f"networks.mainnet.validators.{node}",
    }
    network["validators"][node] = {
        "address": private_key_to_address(validator_key),
        "private_key": validator_key,
    }
    network["node_seed_material"][node] = {
        "wallets": {
            "hub_admin": {
                "address": private_key_to_address(hub_key),
                "metadata": {
                    "address_derivation": "secp256k1-keccak256-eip55",
                    "generated_at": "2026-08-14T21:54:30Z",
                    "generated_by": "test-partial",
                    "reason": "test partial Hub admin identity",
                },
                "private_key": hub_key,
            }
        }
    }

    from tools.mother.common.private_state import prepare_private_state_successor, replace_verified_private_state

    successor = prepare_private_state_successor(
        private_state,
        document,
        updated_at="2026-08-14T21:55:00Z",
        updated_by_action_id="partial-missing-target",
        operation=_operation("partial-missing-target-successor"),
    )
    replace_verified_private_state(paths, successor, private_state.binding, operation=_operation("install-partial-missing-target"))
    partial = read_private_state(paths, operation=_operation("read-partial-missing-target"))

    def unexpected_key_generation() -> str:
        raise AssertionError("missing-target repair must not generate new key material")

    result = reserve_add_node_identity(
        paths,
        partial,
        network="mainnet",
        node=node,
        host="coolify-a",
        execute=True,
        generated_at="2026-08-14T21:56:00Z",
        operation=_operation("repair-missing-target"),
        key_factory=unexpected_key_generation,
        refresh_topology_evidence=False,
    )

    assert result["status"] == "pass"
    assert result["identity_already_reserved"] is False
    assert result["partial_identity_repaired"] is True
    assert result["private_state_update_required"] is True
    assert result["private_state_updated"] is True
    assert result["generated_labels"] == []
    assert result["validator_address"] == private_key_to_address(validator_key)
    assert result["hub_admin_address"] == private_key_to_address(hub_key)

    repaired = read_private_state(paths, operation=_operation("read-repaired-missing-target"))
    assert repaired.binding.generation == partial.binding.generation + 1
    repaired_doc = yaml.safe_load(repaired.document_bytes)
    repaired_target = repaired_doc["networks"]["mainnet"]["deployment"]["targets"][node]
    assert repaired_target == {
        "controller_ref": "networks.mainnet.coolify.controllers.coolify-a",
        "desired_environment_name": "mainnet",
        "desired_service_name": node,
        "hub_admin_address": private_key_to_address(hub_key),
        "hub_admin_private_key_path": f"networks.mainnet.node_seed_material.{node}.wallets.hub_admin.private_key",
        "key_material_status": "present",
        "live_resource_uuid": None,
        "status": "absent-awaiting-redeployment",
    }
    assert repaired_doc["networks"]["mainnet"]["validators"][node]["private_key"] == validator_key


def test_add_node_reserve_identity_cli_executes_and_redacts_secrets(tmp_path: Path) -> None:
    paths, _private_state = _install_state(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "add-node",
            "reserve-identity",
            "mainnet",
            "--node",
            "mainneta-super2",
            "--host",
            "coolify-a",
            "--runtime-state-root",
            str(paths.root.parent),
            "--generated-at",
            "2026-08-14T21:46:00Z",
            "--no-refresh-topology-evidence",
            "--execute",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=45,
    )

    assert completed.returncode == 0, completed.stderr
    output = json.loads(completed.stdout)
    assert output["status"] == "pass"
    assert output["private_state_updated"] is True
    assert output["private_key_material_in_output"] is False
    assert '"private_key"' not in completed.stdout
    updated = read_private_state(paths, operation=_operation("read-cli-updated"))
    assert "mainneta-super2" in yaml.safe_load(updated.document_bytes)["networks"]["mainnet"]["validators"]

def _topology_binding_for_test(private_state) -> dict[str, Any]:
    return {
        "generation": int(private_state.binding.generation),
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


class _PresentServicesOpener:
    def __init__(self, services: dict[str, tuple[str, str]]) -> None:
        self.services = services
        self.requests: list[dict[str, str | None]] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        self.requests.append({"method": request.get_method(), "host": parsed.hostname, "path": parsed.path})
        assert request.get_method() == "GET"
        if parsed.path == "/api/v1/services":
            return _Response(
                [
                    {"uuid": uuid, "name": name, "status": status}
                    for uuid, (name, status) in sorted(self.services.items())
                ]
            )
        if parsed.path.startswith("/api/v1/services/"):
            uuid = parsed.path.rsplit("/", 1)[-1]
            if uuid not in self.services:
                return _Response({"message": "not found"}, status=404)
            name, status = self.services[uuid]
            return _Response({"uuid": uuid, "name": name, "status": status})
        raise AssertionError(f"unexpected GET path: {parsed.path}")


def _write_a1_topology(paths, private_state, *, completed_at: str = "2026-08-14T21:50:00Z") -> tuple[Path, str]:
    validator = private_key_to_address("0x" + "11" * 32)
    evidence = {
        "kind": "main_computer.mother.deployment_node_add_single_node_chain_and_hub_proof_evidence.v1",
        "schema_version": 1,
        "completed_at": completed_at,
        "status": "pass",
        "failure": None,
        "mother_binding": _topology_binding_for_test(private_state),
        "network": "mainnet",
        "mode": "initial",
        "target": {
            "node": "mainneta-super1",
            "controller_id": "coolify-a",
            "service_uuid": "svc-a1",
            "created_service_uuid": "svc-a1",
            "validator_address": validator,
        },
        "final_topology": {
            "source": "operator-directed-single-node-chain-and-hub-proof",
            "chain_id": 42424240,
            "genesis_sha256": "a" * 64,
            "nodes": ["mainneta-super1"],
            "services": {
                "mainneta-super1": {
                    "node": "mainneta-super1",
                    "controller_id": "coolify-a",
                    "service_uuid": "svc-a1",
                    "service_status": "running:healthy",
                    "readiness_source": "deployment-node-add-single-node-bootstrap-proof",
                    "last_observed_at": completed_at,
                    "validator_route": {
                        "kind": "mother-validator-p2p-route.v1",
                        "controller_id": "coolify-a",
                        "vpn_ip": "10.116.0.3",
                        "p2p_port": 30303,
                        "p2p_endpoint": "10.116.0.3:30303",
                        "advertised_host": "10.116.0.3",
                        "advertised_port": 30303,
                        "container_p2p_port": 30303,
                        "source": "networks.mainnet.coolify.controllers.coolify-a.vpn_ip",
                        "allocation": {"default_p2p_port": 30303, "used_p2p_ports_on_controller": []},
                    },
                    "vpn_ip": "10.116.0.3",
                    "p2p_port": 30303,
                    "p2p_endpoint": "10.116.0.3:30303",
                }
            },
            "validator_count": 1,
            "validator_set": [validator],
            "baseline_topology_used_as_live": False,
        },
        "summary": {
            "clean": True,
            "complete": True,
            "current_topology_marked_by_evidence": True,
            "final_nodes": ["mainneta-super1"],
            "final_validator_count": 1,
            "final_validator_set": [validator],
            "live_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
        },
        "policy": {
            "network_access_performed": False,
            "live_mutation_performed": False,
            "finalize_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_http_endpoint_created": False,
            "public_endpoint_created": False,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
            "chain_mutation_performed": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
        },
        "live_mutation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": "add-node-single-node-finalized-mainnet",
    }
    payload = canonical_json(evidence)
    path = paths.root / "evidence" / "deployment-node-add-single-node-chain-and-hub-proof" / f"{completed_at.replace(':', '').replace('-', '')}-a1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def test_reserve_identity_auto_refreshes_latest_topology_for_successor_binding(tmp_path: Path) -> None:
    paths, private_state = _install_state(tmp_path)
    old_path, old_sha = _write_a1_topology(paths, private_state)
    keys = iter(("0x" + "66" * 32, "0x" + "77" * 32))
    opener = _PresentServicesOpener({"svc-a1": ("mainneta-super1", "running:healthy")})

    result = reserve_add_node_identity(
        paths,
        private_state,
        network="mainnet",
        node="mainneta-super2",
        host="coolify-a",
        execute=True,
        generated_at="2026-08-14T21:54:30Z",
        operation=_operation("reserve-a2-with-refresh"),
        key_factory=lambda: next(keys),
        refresh_topology_evidence=True,
        opener=opener,
    )

    assert result["private_state_updated"] is True
    assert result["topology_refresh"]["performed"] is True
    assert result["topology_refresh"]["source"] == "auto"
    assert result["topology_refresh"]["source_evidence"] == {"path": str(old_path), "sha256": old_sha}
    refreshed_path = Path(result["refreshed_topology_evidence"])
    refreshed_sha = result["refreshed_topology_evidence_sha256"]
    assert refreshed_path.parent.name == "deployment-node-add-post-admission-observe"
    refreshed = json.loads(refreshed_path.read_text(encoding="utf-8"))
    updated = read_private_state(paths, operation=_operation("read-refreshed-state"))

    assert refreshed["mother_binding"] == _topology_binding_for_test(updated)
    assert refreshed["source_topology_evidence"]["sha256"] == old_sha
    assert refreshed["final_topology"]["nodes"] == ["mainneta-super1"]
    assert refreshed["final_topology"]["services"]["mainneta-super1"]["p2p_endpoint"] == "10.116.0.3:30303"

    prep = build_node_add_prep_transaction(
        paths,
        updated,
        refreshed_path,
        network="mainnet",
        target_node="mainneta-super2",
        target_host="coolify-a",
        mode="soft",
        baseline_evidence_sha256=refreshed_sha,
        created_at="2026-08-14T21:55:00Z",
        now=None,
    )
    assert prep["target"]["node"] == "mainneta-super2"
    assert prep["target"]["validator_address"] == private_key_to_address("0x" + "66" * 32).lower()
    assert prep["target"]["validator_route"]["p2p_port"] == 30304
    assert prep["target"]["validator_route"]["p2p_endpoint"] == "10.116.0.3:30304"


def test_reserve_identity_prefers_newest_auto_topology_candidate(tmp_path: Path) -> None:
    paths, private_state = _install_state(tmp_path)
    older_path, _older_sha = _write_a1_topology(paths, private_state, completed_at="2026-08-14T21:00:00Z")
    newer_path, newer_sha = _write_a1_topology(paths, private_state, completed_at="2026-08-14T21:50:00Z")
    keys = iter(("0x" + "66" * 32, "0x" + "77" * 32))

    result = reserve_add_node_identity(
        paths,
        private_state,
        network="mainnet",
        node="mainneta-super2",
        host="coolify-a",
        execute=False,
        generated_at="2026-08-14T21:54:30Z",
        operation=_operation("reserve-a2-dry-refresh"),
        key_factory=lambda: next(keys),
        refresh_topology_evidence=True,
        opener=_PresentServicesOpener({"svc-a1": ("mainneta-super1", "running:healthy")}),
    )

    assert result["topology_refresh"]["performed"] is False
    assert result["topology_refresh"]["source_evidence"] == {"path": str(newer_path), "sha256": newer_sha}
    assert result["topology_refresh"]["source_evidence"]["path"] != str(older_path)
    assert result["topology_refresh"]["write_skipped"] == "execute=false"



def test_existing_reserved_identity_refreshes_from_recoverable_predecessor_topology(tmp_path: Path) -> None:
    paths, private_state = _install_state(tmp_path)
    old_path, old_sha = _write_a1_topology(paths, private_state)
    keys = iter(("0x" + "66" * 32, "0x" + "77" * 32))

    first = reserve_add_node_identity(
        paths,
        private_state,
        network="mainnet",
        node="mainneta-super2",
        host="coolify-a",
        execute=True,
        generated_at="2026-08-14T21:54:30Z",
        operation=_operation("reserve-a2-before-refresh"),
        key_factory=lambda: next(keys),
        refresh_topology_evidence=False,
    )
    assert first["private_state_updated"] is True

    updated = read_private_state(paths, operation=_operation("read-existing-reserved-state"))
    result = reserve_add_node_identity(
        paths,
        updated,
        network="mainnet",
        node="mainneta-super2",
        host="coolify-a",
        execute=True,
        generated_at="2026-08-14T21:56:00Z",
        operation=_operation("refresh-existing-a2"),
        refresh_topology_evidence=True,
        opener=_PresentServicesOpener({"svc-a1": ("mainneta-super1", "running:healthy")}),
    )

    assert result["identity_already_reserved"] is True
    assert result["private_state_updated"] is False
    assert result["topology_refresh"]["performed"] is True
    assert result["topology_refresh"]["source"] == "auto"
    assert result["topology_refresh"]["source_evidence"] == {"path": str(old_path), "sha256": old_sha}
    refreshed_path = Path(result["refreshed_topology_evidence"])
    refreshed = json.loads(refreshed_path.read_text(encoding="utf-8"))
    assert refreshed["predecessor_binding"] == _topology_binding_for_test(private_state)
    assert refreshed["successor_binding"] == _topology_binding_for_test(updated)
    assert refreshed["mother_binding"] == _topology_binding_for_test(updated)
    assert refreshed["final_topology"]["nodes"] == ["mainneta-super1"]
    assert refreshed["summary"]["topology_current"] is True
