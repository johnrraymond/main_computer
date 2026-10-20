from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fdb_mutate_harness", ROOT / "fdb_mutate_harness.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
Harness = MODULE.Harness


def _args(
    tmp_path: Path,
    operation: str,
    *,
    service: str | None = None,
    execute: bool = False,
    allow_full_deletion: bool = False,
    project_uuid: str = "",
) -> argparse.Namespace:
    if service is None and operation != "inspect":
        service = "mainnetc-fdb3" if operation == "add-service" else "mainnet-fdb2"
    return argparse.Namespace(
        operation=operation,
        network="mainnet",
        service=service,
        repo_root=tmp_path,
        allow_full_deletion=allow_full_deletion,
        project_uuid=project_uuid,
        execute_mutations=execute,
        yes_i_know_this_mutates_fdb=execute,
    )


def test_operator_parser_exposes_only_high_level_surface() -> None:
    help_text = MODULE._parser().format_help()
    assert "inspect" in help_text
    assert "add-service" in help_text
    assert "remove-service" in help_text
    assert "--allow-full-deletion" in help_text
    assert "--resume" not in help_text
    assert "--run-dir" not in help_text
    assert "--repo-root" not in help_text
    assert "tools.fdb_control" not in help_text


def test_inspect_does_not_require_service(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "inspect"))
    assert harness.operation == "inspect"
    assert harness.service == ""
    assert harness.run_dir is None


def test_mutation_requires_service(tmp_path: Path) -> None:
    with pytest.raises(MODULE.HarnessError, match="requires --service"):
        Harness(_args(tmp_path, "add-service", service=""))


def test_add_harness_passes_only_logical_service_identity_to_prep(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "add-service"))
    command = harness.prep_cmd()
    assert command[-5:] == ["add-service", "prep", "mainnet", "--service", "mainnetc-fdb3"]
    assert "--host" not in command
    assert "--address" not in command
    assert "--port" not in command


def test_remove_harness_passes_only_logical_service_identity_to_prep(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "remove-service"))
    command = harness.prep_cmd()
    assert command[-5:] == ["remove-service", "prep", "mainnet", "--service", "mainnet-fdb2"]
    assert "--host" not in command
    assert "--address" not in command
    assert "--port" not in command


@pytest.mark.parametrize("network,service", [
    ("mainnet", "mainneta-fdb1"),
    ("testnet", "testnetb-fdb1"),
])
def test_explicit_project_uuid_is_forwarded_to_remove_prep(
    tmp_path: Path, network: str, service: str,
) -> None:
    args = _args(
        tmp_path, "remove-service", service=service,
        allow_full_deletion=True, project_uuid="vf1n3kzj0l46ls0fizmjpijj",
    )
    args.network = network
    harness = Harness(args)
    assert harness.prep_cmd()[-5:] == [
        "--service", service, "--allow-full-deletion",
        "--project-uuid", "vf1n3kzj0l46ls0fizmjpijj",
    ]


def test_project_uuid_is_remove_only(tmp_path: Path) -> None:
    with pytest.raises(MODULE.HarnessError, match="applies only to remove-service"):
        Harness(_args(tmp_path, "add-service", project_uuid="vf1n3kzj0l46ls0fizmjpijj"))


def test_project_uuid_unblocks_existing_unfinished_remove_prep(tmp_path: Path) -> None:
    first_args = _args(
        tmp_path, "remove-service", service="testnetb-fdb1", allow_full_deletion=True,
    )
    first_args.network = "testnet"
    first = Harness(first_args)
    first.state["last_completed_step"] = "pre-inspect"
    first._write_state()
    resumed_args = _args(
        tmp_path, "remove-service", service="testnetb-fdb1",
        allow_full_deletion=True, project_uuid="vf1n3kzj0l46ls0fizmjpijj",
    )
    resumed_args.network = "testnet"
    resumed = Harness(resumed_args)
    assert resumed._resumed_existing_run is True
    assert resumed.run_dir == first.run_dir
    assert resumed.prep_cmd()[-2:] == ["--project-uuid", "vf1n3kzj0l46ls0fizmjpijj"]


def test_full_deletion_flag_is_forwarded_only_to_remove_prep(tmp_path: Path) -> None:
    harness = Harness(
        _args(tmp_path, "remove-service", service="mainnet-fdb1", allow_full_deletion=True)
    )
    command = harness.prep_cmd()
    assert command[-6:] == [
        "remove-service",
        "prep",
        "mainnet",
        "--service",
        "mainnet-fdb1",
        "--allow-full-deletion",
    ]


def test_full_deletion_flag_is_rejected_for_add(tmp_path: Path) -> None:
    with pytest.raises(MODULE.HarnessError, match="applies only to remove-service"):
        Harness(_args(tmp_path, "add-service", allow_full_deletion=True))


def test_full_deletion_resume_does_not_match_non_destructive_run(tmp_path: Path) -> None:
    first = Harness(_args(tmp_path, "remove-service", service="mainnet-fdb1"))
    first.state["last_completed_step"] = "prep"
    first._write_state()

    second = Harness(
        _args(tmp_path, "remove-service", service="mainnet-fdb1", allow_full_deletion=True)
    )
    assert second.run_dir != first.run_dir


def test_same_high_level_command_auto_resumes_unfinished_run(tmp_path: Path) -> None:
    first = Harness(_args(tmp_path, "add-service"))
    first.state["last_completed_step"] = "prep"
    first.state["operation_id"] = "fdb-add-mainnet-abc"
    first._write_state()
    run_dir = first.run_dir

    second = Harness(_args(tmp_path, "add-service", execute=True))
    assert second._resumed_existing_run is True
    assert second.run_dir == run_dir
    assert second.state["operation_id"] == "fdb-add-mainnet-abc"
    assert second.state["last_completed_step"] == "prep"


def test_completed_run_is_not_auto_resumed(tmp_path: Path) -> None:
    first = Harness(_args(tmp_path, "remove-service"))
    first.state["last_completed_step"] = "final-inspect"
    first._write_state()
    completed_dir = first.run_dir

    second = Harness(_args(tmp_path, "remove-service"))
    assert second._resumed_existing_run is False
    assert second.run_dir != completed_dir


def test_internal_control_command_is_saved_but_not_printed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = Harness(_args(tmp_path, "add-service"))
    payload = {
        "ok": True,
        "result": {
            "status": "accepted",
            "accepted_generation": 2,
            "cluster": {"description": "main", "cluster_id": "abc"},
            "coordinators": ["10.0.0.1:4550"],
            "services": [],
            "cluster_verification": {"verified": True},
        },
    }

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
    command = harness.inspect_cmd()
    harness._run_step("pre-inspect", command)

    stdout = capsys.readouterr().out
    assert "tools.fdb_control" not in stdout
    assert "=== pre-inspect ===" in stdout
    command_text = (harness.run_dir / "00-pre-inspect.command.txt").read_text(encoding="utf-8")
    assert "tools.fdb_control" in command_text


def test_inspect_prints_human_summary_without_internal_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = Harness(_args(tmp_path, "inspect"))
    payload = {
        "ok": True,
        "result": {
            "status": "accepted",
            "accepted_generation": 3,
            "cluster": {"description": "main_computer_mainnet", "cluster_id": "abc123"},
            "coordinators": ["10.116.0.3:4550"],
            "services": [
                {
                    "service_id": "mainnet-fdb1",
                    "host_id": "coolify-a",
                    "endpoint": "10.116.0.3:4550",
                }
            ],
            "cluster_verification": {"verified": True},
        },
    }

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
    assert harness.run() == 0
    stdout = capsys.readouterr().out
    assert "=== FDB inspection ===" in stdout
    assert "mainnet-fdb1" in stdout
    assert "coordinator" in stdout
    assert "verification:        verified" in stdout
    assert "tools.fdb_control" not in stdout


def test_prepared_boundary_prints_same_high_level_command_without_run_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = Harness(_args(tmp_path, "add-service"))
    harness.state.update(
        {
            "starting_generation": 2,
            "resolved_controller": "coolify-c",
            "resolved_host": "coolify-c",
            "resolved_endpoint": "10.116.0.2:4550",
            "target_coordinators": ["10.116.0.3:4550"],
            "coordinators_changed": False,
        }
    )
    harness._print_prepared_boundary()
    stdout = capsys.readouterr().out
    assert "python .\\fdb_mutate_harness.py add-service" in stdout
    assert "--execute-mutations --yes-i-know-this-mutates-fdb" in stdout
    assert "run_dir=" not in stdout
    assert "tools.fdb_control" not in stdout


def test_harness_records_inferred_add_target_from_prep(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "add-service"))
    harness.state["starting_generation"] = 2
    harness._validate_step(
        "prep",
        {
            "status": "prepared",
            "details": {
                "operation_id": "fdb-add-mainnet-abc",
                "service_id": "mainnetc-fdb3",
                "controller_id": "coolify-c",
                "host_id": "physical-b",
                "endpoint": "10.116.0.5:4550",
                "target_generation": 3,
                "target_coordinators": ["10.116.0.3:4550"],
                "coordinators_changed": False,
            },
        },
    )
    assert harness.state["operation_id"] == "fdb-add-mainnet-abc"
    assert harness.state["resolved_controller"] == "coolify-c"
    assert harness.state["resolved_host"] == "physical-b"
    assert harness.state["resolved_endpoint"] == "10.116.0.5:4550"


def test_harness_accepts_frozen_coordinator_transition_on_final_inspect(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "remove-service", service="mainnet-fdb1"))
    harness.state.update(
        {
            "starting_generation": 3,
            "starting_cluster": {"description": "main_computer_mainnet", "cluster_id": "old123"},
            "target_generation": 4,
            "target_coordinators": ["10.116.0.3:4551"],
            "coordinators_changed": True,
        }
    )
    harness._validate_step(
        "final-inspect",
        {
            "status": "accepted",
            "accepted_generation": 4,
            "cluster": {"description": "main_computer_mainnet", "cluster_id": "new456"},
            "coordinators": ["10.116.0.3:4551"],
            "services": [
                {"service_id": "mainnet-fdb2", "host_id": "coolify-a", "endpoint": "10.116.0.3:4551"},
                {"service_id": "mainnetc-fdb3", "host_id": "coolify-c", "endpoint": "10.116.0.2:4550"},
            ],
            "cluster_verification": {"verified": True},
        },
    )
    assert harness.state["final_cluster"]["cluster_id"] == "new456"
    assert harness.state["final_coordinators"] == ["10.116.0.3:4551"]


def test_harness_accepts_verified_empty_topology_after_full_deletion(tmp_path: Path) -> None:
    harness = Harness(
        _args(tmp_path, "remove-service", service="mainnet-fdb1", allow_full_deletion=True)
    )
    harness.state.update(
        {
            "starting_generation": 9,
            "starting_cluster": {"description": "main_computer_mainnet", "cluster_id": "old123"},
            "target_generation": 10,
            "target_coordinators": [],
            "coordinators_changed": True,
            "full_deletion": True,
        }
    )
    harness._validate_step(
        "final-inspect",
        {
            "status": "accepted-empty",
            "accepted_generation": 10,
            "cluster": {"description": "main_computer_mainnet", "cluster_id": "old123"},
            "coordinators": [],
            "services": [],
            "empty_topology_verification": {
                "verified": True,
                "reason": "accepted-empty-topology",
            },
        },
    )
    assert harness.state["final_coordinators"] == []


def test_harness_rejects_missing_cluster_id_change_when_coordinators_changed(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "remove-service", service="mainnet-fdb1"))
    harness.state.update(
        {
            "starting_generation": 3,
            "target_generation": 4,
            "starting_cluster": {"description": "main_computer_mainnet", "cluster_id": "same123"},
            "target_coordinators": ["10.116.0.3:4551"],
            "coordinators_changed": True,
        }
    )

    with pytest.raises(MODULE.HarnessError, match="cluster id must change"):
        harness._validate_step(
            "final-inspect",
            {
                "status": "accepted",
                "accepted_generation": 4,
                "cluster": {"description": "main_computer_mainnet", "cluster_id": "same123"},
                "coordinators": ["10.116.0.3:4551"],
                "services": [
                    {"service_id": "mainnet-fdb2", "host_id": "coolify-a", "endpoint": "10.116.0.3:4551"},
                ],
                "cluster_verification": {"verified": True},
            },
        )


def test_stage_summary_prints_preinspect_evidence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = Harness(_args(tmp_path, "remove-service"))
    harness._print_step_result(
        "pre-inspect",
        {
            "status": "accepted",
            "accepted_generation": 5,
            "services": [
                {"service_id": "mainnet-fdb1"},
                {"service_id": "mainnet-fdb2"},
            ],
            "coordinators": ["10.116.0.3:4550"],
            "cluster_verification": {"verified": True},
        },
    )
    stdout = capsys.readouterr().out
    assert "status:              accepted" in stdout
    assert "accepted generation: 5" in stdout
    assert "services:            2" in stdout
    assert "coordinators:        10.116.0.3:4550" in stdout
    assert "verification:        verified" in stdout


def test_stage_summary_prints_full_deletion_effects(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = Harness(
        _args(tmp_path, "remove-service", service="mainnet-fdb1", allow_full_deletion=True)
    )
    harness.state["full_deletion"] = True
    harness._print_step_result(
        "do",
        {
            "status": "removed",
            "details": {
                "full_deletion": True,
                "coolify_service_deleted": True,
                "endpoint_exclusion_cleared": False,
                "coordinators_changed": True,
            },
        },
    )
    stdout = capsys.readouterr().out
    assert "status:              removed" in stdout
    assert "FDB drain:           skipped (explicit final-copy deletion)" in stdout
    assert "Coolify deletion:    complete" in stdout
    assert "coordinator change:  yes" in stdout
    assert "endpoint exclusion:" not in stdout


def test_stage_summary_prints_operation_proof(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = Harness(_args(tmp_path, "remove-service"))
    harness._print_step_result(
        "operation-inspect",
        {
            "remove_service_verification": {
                "verified": True,
                "reason": "fdb-remove-service-proof-satisfied",
                "target_status": "missing",
                "helper_status": "running:healthy",
            }
        },
    )
    stdout = capsys.readouterr().out
    assert "mutation proof:       verified" in stdout
    assert "proof reason:         fdb-remove-service-proof-satisfied" in stdout
    assert "target status:        missing" in stdout
    assert "helper status:        running:healthy" in stdout


def test_stage_summary_prints_finalize_authority_transition(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = Harness(_args(tmp_path, "remove-service"))
    harness._print_step_result(
        "finalize",
        {
            "status": "finalized",
            "details": {
                "verified": True,
                "accepted_generation": 6,
                "consumer_contract_changed": True,
                "hub_fdb_rectification_required": True,
            },
        },
    )
    stdout = capsys.readouterr().out
    assert "status:              finalized" in stdout
    assert "accepted generation: 6" in stdout
    assert "accepted authority:  advanced" in stdout
    assert "consumer contract:   changed" in stdout
    assert "Hub rectification:   required" in stdout


def test_add_harness_accepts_verified_accepted_empty_preinspect(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "add-service", service="mainneta-fdb1"))
    harness._validate_step(
        "pre-inspect",
        {
            "status": "accepted-empty",
            "accepted_generation": 6,
            "cluster": {"description": "main_computer_mainnet", "cluster_id": "old123"},
            "services": [],
            "coordinators": [],
            "empty_topology_verification": {
                "verified": True,
                "reason": "accepted-empty-topology",
            },
        },
    )
    assert harness.state["starting_generation"] == 6
    assert harness.state["starting_status"] == "accepted-empty"
    assert harness.state["starting_coordinators"] == []


def test_add_harness_accepts_rebirth_with_preserved_historical_cluster_id(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "add-service", service="mainneta-fdb1"))
    harness.state.update(
        {
            "starting_generation": 6,
            "starting_status": "accepted-empty",
            "starting_cluster": {"description": "main_computer_mainnet", "cluster_id": "old123"},
            "target_generation": 7,
            "target_coordinators": ["10.116.0.3:4550"],
            "coordinators_changed": True,
            "rebirth": True,
            "resolved_host": "coolify-a",
            "resolved_endpoint": "10.116.0.3:4550",
        }
    )
    harness._validate_step(
        "final-inspect",
        {
            "status": "accepted",
            "accepted_generation": 7,
            "cluster": {"description": "main_computer_mainnet", "cluster_id": "old123"},
            "coordinators": ["10.116.0.3:4550"],
            "services": [
                {"service_id": "mainneta-fdb1", "host_id": "coolify-a", "endpoint": "10.116.0.3:4550"},
            ],
            "birth_verification": {"verified": True},
        },
    )
    assert harness.state["final_cluster"]["cluster_id"] == "old123"
    assert harness.state["final_coordinators"] == ["10.116.0.3:4550"]

# Regression: a prior accepted removal can advance the topology between an
# old harness pre-inspect and a newly prepared add-service rebirth.  The frozen
# low-level operation (generation 2 -> 3) is authoritative, not the old
# pre-inspect snapshot (generation 1).
def _make_committed_rebirth(tmp_path: Path, network: str, *, committed: bool = True):
    from tools.fdb_control.common.models import (
        AcceptedClusterState, ClusterIdentity, CoordinatorEndpoint, FdbContext, ServicePlacement,
    )
    from tools.fdb_control.common.state import (
        ADD_SERVICE_OP_SCHEMA, accepted_state_to_wire, publish_accepted_state,
        write_operation, update_operation,
    )

    service_id = "testnetb-fdb1" if network == "testnet" else "mainneta-fdb1"
    host_id = "coolify-b" if network == "testnet" else "coolify-a"
    endpoint = "10.124.0.3" if network == "testnet" else "10.116.0.3"
    cluster = ClusterIdentity(f"main_computer_{network}", "18b4bd9aedc84312")
    prestate = AcceptedClusterState(
        network, 2, cluster, (), (), "single", "ssd", False,
    )
    placement = ServicePlacement(service_id, host_id, endpoint, 4550, "machine-b", "zone-b")
    coordinator = CoordinatorEndpoint(service_id, host_id, endpoint, 4550)
    accepted = AcceptedClusterState(
        network, 3, cluster, (placement,), (coordinator,), "single", "ssd", False,
    )
    op_id = f"fdb-add-{network}-rebirthproof"
    ctx = FdbContext.from_repo(tmp_path)
    write_operation(ctx, {
        "schema": ADD_SERVICE_OP_SCHEMA, "operation_id": op_id,
        "network": network, "kind": "add-service",
        "stage": "prepared",
        "target": {
            "accepted_prestate": accepted_state_to_wire(prestate),
            "request": {"network": network, "service": {
                "service_id": service_id, "host_id": host_id, "address": endpoint,
                "port": 4550, "machine_id": "machine-b", "zone_id": "zone-b",
            }},
        },
    })
    if committed:
        publish_accepted_state(ctx, accepted)
        update_operation(
            ctx, network, op_id, stage="finalized", accepted_generation=3,
            verification={"verified": True, "reason": "fdb-add-service-proof-satisfied"},
        )
    else:
        publish_accepted_state(ctx, prestate)
    args = _args(tmp_path, "add-service", service=service_id, execute=True)
    args.network = network
    harness = Harness(args)
    harness.state.update({
        "operation_id": op_id,
        "starting_generation": 1,  # stale pre-inspect, before remove-service
        "starting_status": "accepted",
        "starting_cluster": {"description": cluster.description, "cluster_id": "old-cache"},
        "starting_coordinators": ["stale:4550"],
        "target_generation": 3,
        "target_coordinators": [f"{endpoint}:4550"],
        "coordinators_changed": True,
        "rebirth": True,
        "resolved_host": host_id,
        "resolved_endpoint": f"{endpoint}:4550",
        "last_completed_step": "operation-inspect",
    })
    harness._write_state()
    return harness, ctx, accepted


@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_resumed_finalized_rebirth_reconciles_generation_without_new_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, network: str,
) -> None:
    from tools.fdb_control.common.state import accepted_state_to_wire, read_accepted_state, require_operation

    first, ctx, accepted = _make_committed_rebirth(tmp_path, network)
    args = _args(tmp_path, "add-service", service=first.service, execute=True)
    args.network = network
    resumed = Harness(args)
    assert resumed._resumed_existing_run
    assert resumed.run_dir == first.run_dir

    def final_inspect_only(step, _argv):
        assert step == "final-inspect", "a finalized operation must never redeploy or re-finalize"
        state = read_accepted_state(ctx, network)
        assert state == accepted
        wire = accepted_state_to_wire(state)
        return {
            "status": "accepted", "accepted_generation": state.generation,
            "cluster": wire["cluster"],
            "coordinators": [f"{entry.address}:{entry.port}" for entry in state.coordinators],
            "services": [
                {"service_id": entry.service_id, "host_id": entry.host_id,
                 "endpoint": entry.endpoint} for entry in state.services
            ],
            "cluster_verification": {"verified": True},
        }

    monkeypatch.setattr(resumed, "_run_step", final_inspect_only)
    assert resumed.run() == 0
    assert resumed.state["starting_generation"] == 2
    assert resumed.state["starting_status"] == "accepted-empty"
    assert resumed.state["last_completed_step"] == "final-inspect"
    assert read_accepted_state(ctx, network) == accepted
    assert require_operation(ctx, network, resumed.state["operation_id"])["stage"] == "finalized"
    evidence = json.loads((resumed.run_dir / "04-finalize.recovery.json").read_text())
    assert evidence["accepted_generation"] == 3
    assert evidence["operation_id"] == resumed.state["operation_id"]


@pytest.mark.parametrize("mutation", ["generation", "service", "proof", "wrong-network"])
def test_finalized_recovery_fails_closed_for_inconsistent_authority(tmp_path: Path, mutation: str) -> None:
    from tools.fdb_control.common.models import AcceptedClusterState, FdbContext
    from tools.fdb_control.common.state import (
        accepted_path, accepted_state_to_wire, read_accepted_state,
        require_operation, update_operation,
    )

    first, ctx, accepted = _make_committed_rebirth(tmp_path, "testnet")
    op_id = first.state["operation_id"]
    if mutation == "generation":
        state = accepted_state_to_wire(accepted)
        state["generation"] = 4
        accepted_path(ctx, "testnet").write_text(json.dumps(state))
    elif mutation == "service":
        state = accepted_state_to_wire(accepted)
        state["services"][0]["address"] = "10.124.0.99"
        accepted_path(ctx, "testnet").write_text(json.dumps(state))
    elif mutation == "proof":
        update_operation(ctx, "testnet", op_id, verification={"verified": False})
    elif mutation == "wrong-network":
        from tools.fdb_control.common.state import operation_path
        op_path = operation_path(ctx, "testnet", op_id)
        op = json.loads(op_path.read_text())
        op["network"] = "mainnet"
        op_path.write_text(json.dumps(op))

    resumed_args = _args(tmp_path, "add-service", service="testnetb-fdb1", execute=True)
    resumed_args.network = "testnet"
    resumed = Harness(resumed_args)
    with pytest.raises(MODULE.HarnessError):
        resumed.run()
    assert resumed.state["last_completed_step"] == "operation-inspect"
    assert not (resumed.run_dir / "04-finalize.recovery.json").exists()


def test_prepared_generation_rejects_tampered_target(tmp_path: Path) -> None:
    first, _, _ = _make_committed_rebirth(tmp_path, "testnet", committed=False)
    first.state["target_generation"] = 4
    with pytest.raises(MODULE.HarnessError, match="target generation mismatch"):
        first._bind_prepared_generation()
    assert first.state["starting_generation"] == 1


def test_resumed_finalized_full_removal_checks_accepted_empty_without_repeating_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.fdb_control.common.models import (
        AcceptedClusterState, ClusterIdentity, CoordinatorEndpoint, FdbContext, ServicePlacement,
    )
    from tools.fdb_control.common.state import (
        REMOVE_SERVICE_OP_SCHEMA, accepted_state_to_wire, publish_accepted_state,
        read_accepted_state, write_operation, update_operation,
    )

    ctx = FdbContext.from_repo(tmp_path)
    cluster = ClusterIdentity("main_computer_testnet", "old_cluster_id")
    placement = ServicePlacement("testnetb-fdb1", "coolify-b", "10.124.0.3", 4550, "machine-b", "zone-b")
    coordinator = CoordinatorEndpoint("testnetb-fdb1", "coolify-b", "10.124.0.3", 4550)
    prestate = AcceptedClusterState("testnet", 4, cluster, (placement,), (coordinator,), "single", "ssd")
    empty = AcceptedClusterState("testnet", 5, cluster, (), (), "single", "ssd")
    op_id = "fdb-remove-testnet-finalcopy"
    write_operation(ctx, {
        "schema": REMOVE_SERVICE_OP_SCHEMA, "operation_id": op_id, "network": "testnet",
        "kind": "remove-service", "stage": "prepared",
        "target": {
            "accepted_prestate": accepted_state_to_wire(prestate),
            "request": {"network": "testnet", "service_id": "testnetb-fdb1", "allow_full_deletion": True},
        },
    })
    publish_accepted_state(ctx, empty)
    update_operation(ctx, "testnet", op_id, stage="finalized", accepted_generation=5,
                     verification={"verified": True, "reason": "fdb-full-deletion-proof-satisfied"})
    args = _args(tmp_path, "remove-service", service="testnetb-fdb1", execute=True, allow_full_deletion=True)
    args.network = "testnet"
    first = Harness(args)
    first.state.update({
        "operation_id": op_id, "starting_generation": 3, "starting_status": "accepted",
        "starting_cluster": {"description": cluster.description, "cluster_id": cluster.cluster_id},
        "target_generation": 5, "full_deletion": True,
        "target_coordinators": [], "coordinators_changed": True,
        "resolved_host": "coolify-b", "resolved_endpoint": "10.124.0.3:4550",
        "last_completed_step": "operation-inspect",
    })
    first._write_state()
    resumed = Harness(args)

    def read_only_final(step, _argv):
        assert step == "final-inspect"
        assert read_accepted_state(ctx, "testnet") == empty
        return {
            "status": "accepted-empty", "accepted_generation": 5,
            "cluster": {"description": cluster.description, "cluster_id": cluster.cluster_id},
            "services": [], "coordinators": [],
            "empty_topology_verification": {"verified": True, "reason": "accepted-empty-topology"},
        }

    monkeypatch.setattr(resumed, "_run_step", read_only_final)
    assert resumed.run() == 0
    assert resumed.state["starting_generation"] == 4
    assert resumed.state["last_completed_step"] == "final-inspect"
    assert read_accepted_state(ctx, "testnet") == empty


def test_prepared_not_finalized_fails_if_accepted_state_has_changed(tmp_path: Path) -> None:
    from tools.fdb_control.common.state import accepted_path
    first, ctx, _ = _make_committed_rebirth(tmp_path, "testnet", committed=False)
    accepted_path(ctx, "testnet").unlink()
    with pytest.raises(MODULE.HarnessError, match="accepted FDB topology changed"):
        first._bind_prepared_generation()
    assert first.state["starting_generation"] == 1
