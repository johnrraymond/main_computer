from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("hub_mutate_harness", ROOT / "hub_mutate_harness.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
Harness = MODULE.Harness
HarnessError = MODULE.HarnessError


def _args(
    tmp_path: Path,
    operation: str,
    *,
    hub: str | None = None,
    execute: bool = False,
    allow_full_deletion: bool = False,
    force_git: bool = True,
) -> argparse.Namespace:
    if hub is None and operation != "inspect":
        hub = "mainnetc-hub1" if operation == "add-hub" else "mainneta-hub1"
    return argparse.Namespace(
        operation=operation,
        network="mainnet",
        hub=hub,
        repo_root=tmp_path,
        allow_full_deletion=allow_full_deletion,
        force_git=force_git,
        execute_mutations=execute,
        yes_i_know_this_mutates_hub=execute,
    )


def test_parser_exposes_only_frozen_operator_surface() -> None:
    help_text = MODULE._parser().format_help()
    assert "inspect" in help_text
    assert "add-hub" in help_text
    assert "remove-hub" in help_text
    assert "--network" in help_text
    assert "--hub" in help_text
    assert "--allow-full-deletion" in help_text
    assert "--force-git" in help_text
    assert "--no-force-git" in help_text
    assert "--execute-mutations" in help_text
    assert "--yes-i-know-this-mutates-hub" in help_text
    for hidden in (
        "--resume",
        "--run-dir",
        "--operation-id",
        "--coolify-url",
        "--project-uuid",
        "--environment-uuid",
        "--server-uuid",
        "--fdb-contract",
        "--chain-contract",
        "--rpc-url",
        "--cluster-file",
        "--namespace",
    ):
        assert hidden not in help_text
    assert "--repo-root" not in help_text
    assert "tools.hub_control" not in help_text


def test_network_defaults_to_mainnet(tmp_path: Path) -> None:
    args = MODULE._parser().parse_args(["inspect"])
    assert args.network == "mainnet"


def test_inspect_takes_no_hub(tmp_path: Path) -> None:
    Harness(_args(tmp_path, "inspect"))
    with pytest.raises(MODULE.HarnessError, match="does not take --hub"):
        Harness(_args(tmp_path, "inspect", hub="mainneta-hub1"))


def test_mutations_require_hub(tmp_path: Path) -> None:
    with pytest.raises(MODULE.HarnessError, match="requires --hub"):
        Harness(_args(tmp_path, "add-hub", hub=""))
    with pytest.raises(MODULE.HarnessError, match="requires --hub"):
        Harness(_args(tmp_path, "remove-hub", hub=""))


def test_full_deletion_applies_only_to_remove(tmp_path: Path) -> None:
    with pytest.raises(MODULE.HarnessError, match="applies only to remove-hub"):
        Harness(_args(tmp_path, "add-hub", allow_full_deletion=True))


def test_no_force_git_applies_only_to_add_hub(tmp_path: Path) -> None:
    Harness(_args(tmp_path, "add-hub", force_git=False))
    with pytest.raises(MODULE.HarnessError, match="applies only to add-hub"):
        Harness(_args(tmp_path, "remove-hub", force_git=False))
    with pytest.raises(MODULE.HarnessError, match="applies only to add-hub"):
        Harness(_args(tmp_path, "inspect", force_git=False))


def test_mutation_authorization_flags_must_be_paired(tmp_path: Path) -> None:
    args = _args(tmp_path, "add-hub")
    args.execute_mutations = True
    args.yes_i_know_this_mutates_hub = False
    with pytest.raises(MODULE.HarnessError, match="requires both"):
        Harness(args).run()


def test_add_prep_passes_only_logical_hub_identity(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "add-hub", hub="mainnetc-hub1"))
    command = harness.prep_cmd()
    assert command[-5:] == ["add-hub", "prep", "mainnet", "--hub", "mainnetc-hub1"]
    assert "--host" not in command
    assert "--controller" not in command


def test_add_do_forwards_no_force_git_override(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "add-hub", hub="mainnetc-hub1", force_git=False))
    harness.state["operation_id"] = "hub-add-mainnet-abc"

    command = harness.do_cmd()

    assert command[-1] == "--no-force-git"
    assert command[-4:-1] == ["mainnet", "--operation-id", "hub-add-mainnet-abc"]


def test_remove_full_deletion_flag_is_forwarded_to_prep(tmp_path: Path) -> None:
    harness = Harness(
        _args(tmp_path, "remove-hub", hub="mainneta-hub1", allow_full_deletion=True)
    )
    assert harness.prep_cmd()[-6:] == [
        "remove-hub",
        "prep",
        "mainnet",
        "--hub",
        "mainneta-hub1",
        "--allow-full-deletion",
    ]


def test_same_high_level_command_auto_resumes_unfinished_run(tmp_path: Path) -> None:
    first = Harness(_args(tmp_path, "add-hub", hub="mainnetc-hub1"))
    first.state["last_completed_step"] = "prep"
    first.state["operation_id"] = "hub-add-mainnet-abc"
    first._write_state()
    run_dir = first.run_dir

    second = Harness(_args(tmp_path, "add-hub", hub="mainnetc-hub1", execute=True))
    assert second._resumed_existing_run is True
    assert second.run_dir == run_dir
    assert second.state["operation_id"] == "hub-add-mainnet-abc"


def test_resume_after_only_preinspect_refreshes_preinspect_before_prep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = Harness(_args(tmp_path, "add-hub", hub="mainnetc-hub1"))
    first.state["last_completed_step"] = "pre-inspect"
    first.state["starting_generation"] = 5
    first._write_state()

    resumed = Harness(_args(tmp_path, "add-hub", hub="mainnetc-hub1", execute=True))
    seen: list[str] = []

    def fake_run_step(step: str, _argv: list[str]) -> dict:
        seen.append(step)
        raise RuntimeError("stop-after-first-step")

    monkeypatch.setattr(resumed, "_run_step", fake_run_step)
    monkeypatch.setattr(resumed, "_check_topology_before_prep", lambda: None)
    with pytest.raises(RuntimeError, match="stop-after-first-step"):
        resumed.run()

    assert seen == ["pre-inspect"]


def test_finalize_uses_frozen_prep_target_generation_not_stale_preinspect(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "add-hub", hub="mainnetc-hub1"))
    harness.state.update({"starting_generation": 5, "target_generation": 7})

    harness._validate_step(
        "finalize",
        {
            "status": "finalized",
            "details": {"verified": True, "accepted_generation": 7},
        },
    )


def test_prep_reconciles_stale_preinspect_to_authoritative_frozen_prestate(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "add-hub", hub="mainnetc-hub1"))
    harness.state.update({"starting_generation": 5, "starting_hub_count": 1, "starting_status": "accepted"})

    harness._validate_step(
        "prep",
        {
            "status": "prepared",
            "details": {
                "operation_id": "hub-add-mainnet-new",
                "hub_id": "mainnetc-hub1",
                "controller_id": "coolify-c",
                "host_id": "coolify-c",
                "accepted_generation": 6,
                "accepted_hub_count": 0,
                "accepted_status": "accepted-empty",
                "target_generation": 7,
                "target_hub_count": 1,
                "rebirth": True,
                "full_deletion": False,
                "fdb_contract": {"generation": 8, "sha256": "fdbhash"},
                "chain_contract": {"generation": 6, "sha256": "chainhash"},
            },
        },
    )

    assert harness.state["starting_generation"] == 6
    assert harness.state["starting_hub_count"] == 0
    assert harness.state["starting_status"] == "accepted-empty"
    assert harness.state["target_generation"] == 7


def test_full_deletion_resume_does_not_match_non_destructive_run(tmp_path: Path) -> None:
    first = Harness(_args(tmp_path, "remove-hub", hub="mainneta-hub1"))
    first.state["last_completed_step"] = "prep"
    first._write_state()
    second = Harness(
        _args(tmp_path, "remove-hub", hub="mainneta-hub1", allow_full_deletion=True)
    )
    assert second.run_dir != first.run_dir


def test_no_force_git_resume_does_not_match_force_git_run(tmp_path: Path) -> None:
    first = Harness(_args(tmp_path, "add-hub", hub="mainnetc-hub1", force_git=True))
    first.state["last_completed_step"] = "prep"
    first._write_state()

    second = Harness(_args(tmp_path, "add-hub", hub="mainnetc-hub1", force_git=False))

    assert second.run_dir != first.run_dir


def test_completed_run_is_not_resumed(tmp_path: Path) -> None:
    first = Harness(_args(tmp_path, "remove-hub"))
    first.state["last_completed_step"] = "final-inspect"
    first._write_state()
    completed = first.run_dir
    second = Harness(_args(tmp_path, "remove-hub"))
    assert second._resumed_existing_run is False
    assert second.run_dir != completed


def test_preinspect_accepts_verified_accepted_empty_for_add(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "add-hub", hub="mainneta-hub1"))
    harness._validate_step(
        "pre-inspect",
        {
            "status": "accepted-empty",
            "accepted_generation": 4,
            "hubs": [],
            "empty_topology_verification": {"verified": True},
        },
    )
    assert harness.state["starting_generation"] == 4
    assert harness.state["starting_hub_count"] == 0


def test_preinspect_rejects_accepted_empty_for_remove(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "remove-hub", hub="mainneta-hub1"))
    with pytest.raises(MODULE.HarnessError, match="requires an accepted Hub topology"):
        harness._validate_step(
            "pre-inspect",
            {
                "status": "accepted-empty",
                "accepted_generation": 4,
                "hubs": [],
                "empty_topology_verification": {"verified": True},
            },
        )




def test_preinspect_accepts_dependency_drift_for_remove_membership_authority(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "remove-hub", hub="mainneta-hub1"))
    harness._validate_step(
        "pre-inspect",
        {
            "status": "accepted",
            "accepted_generation": 1,
            "fdb_contract": {"generation": 8, "sha256": "fdb8", "status": "current"},
            "chain_contract": {"generation": 6, "sha256": "chain6", "status": "current"},
            "hubs": [
                {
                    "hub_id": "mainneta-hub1",
                    "host_id": "coolify-a",
                    "fdb_contract": {"generation": 8, "sha256": "fdb8"},
                    "chain_contract": {"generation": 5, "sha256": "chain5"},
                    "verification": {
                        "verified": False,
                        "reason": "hub-dependency-contract-stale",
                        "fdb_adoption_verified": True,
                        "chain_adoption_verified": False,
                    },
                }
            ],
            "topology_verification": {
                "verified": False,
                "reason": "one-or-more-accepted-hubs-unverified",
            },
        },
    )
    assert harness.state["starting_generation"] == 1
    assert harness.state["starting_hub_count"] == 1
    drift = harness.state["preexisting_dependency_drift"]
    assert len(drift) == 1
    assert drift[0]["dependency"] == "chain"
    assert drift[0]["adopted_generation"] == 5
    assert drift[0]["current_generation"] == 6


def test_preinspect_still_rejects_non_dependency_remove_failure(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "remove-hub", hub="mainneta-hub1"))
    with pytest.raises(MODULE.HarnessError, match="could not independently verify"):
        harness._validate_step(
            "pre-inspect",
            {
                "status": "accepted",
                "accepted_generation": 1,
                "fdb_contract": {"generation": 8, "sha256": "fdb8", "status": "current"},
                "chain_contract": {"generation": 6, "sha256": "chain6", "status": "current"},
                "hubs": [
                    {
                        "hub_id": "mainneta-hub1",
                        "host_id": "coolify-a",
                        "fdb_contract": {"generation": 8, "sha256": "fdb8"},
                        "chain_contract": {"generation": 5, "sha256": "chain5"},
                        "verification": {"verified": False, "reason": "hub-health-unverified"},
                    }
                ],
                "topology_verification": {"verified": False},
            },
        )


def test_prep_records_inferred_placement_and_dependency_contracts(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "add-hub", hub="mainnetc-hub1"))
    harness.state["starting_generation"] = 4
    harness._validate_step(
        "prep",
        {
            "status": "prepared",
            "details": {
                "operation_id": "hub-add-mainnet-abc",
                "hub_id": "mainnetc-hub1",
                "controller_id": "coolify-c",
                "host_id": "coolify-c",
                "target_generation": 5,
                "target_hub_count": 3,
                "rebirth": False,
                "full_deletion": False,
                "fdb_contract": {"generation": 8, "sha256": "fdbhash"},
                "chain_contract": {"generation": 12, "sha256": "chainhash"},
            },
        },
    )
    assert harness.state["resolved_controller"] == "coolify-c"
    assert harness.state["resolved_host"] == "coolify-c"
    assert harness.state["fdb_contract"]["generation"] == 8
    assert harness.state["chain_contract"]["generation"] == 12


def test_final_inspect_accepts_added_hub_on_frozen_host(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "add-hub", hub="mainnetc-hub1"))
    harness.state.update(
        {
            "starting_generation": 4,
            "target_generation": 5,
            "target_hub_count": 2,
            "resolved_host": "coolify-c",
            "fdb_contract": {"generation": 8, "sha256": "fdbhash"},
            "chain_contract": {"generation": 12, "sha256": "chainhash"},
        }
    )
    harness._validate_step(
        "final-inspect",
        {
            "status": "accepted",
            "accepted_generation": 5,
            "hubs": [
                {"hub_id": "mainneta-hub1", "host_id": "coolify-a"},
                {
                    "hub_id": "mainnetc-hub1",
                    "host_id": "coolify-c",
                    "fdb_contract": {"generation": 8, "sha256": "fdbhash"},
                    "chain_contract": {"generation": 12, "sha256": "chainhash"},
                },
            ],
            "topology_verification": {"verified": True},
        },
    )



def test_final_inspect_accepts_post_finalize_chain_drift_as_rectification(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "add-hub", hub="mainneta-hub1"))
    harness.state.update(
        {
            "starting_generation": 0,
            "target_generation": 1,
            "target_hub_count": 1,
            "resolved_host": "coolify-a",
            "fdb_contract": {"generation": 8, "sha256": "fdb8"},
            "chain_contract": {"generation": 5, "sha256": "chain5"},
        }
    )
    harness._validate_step(
        "final-inspect",
        {
            "status": "accepted",
            "accepted_generation": 1,
            "fdb_contract": {"generation": 8, "sha256": "fdb8", "status": "current"},
            "chain_contract": {"generation": 6, "sha256": "chain6", "status": "current"},
            "hubs": [
                {
                    "hub_id": "mainneta-hub1",
                    "host_id": "coolify-a",
                    "fdb_contract": {"generation": 8, "sha256": "fdb8"},
                    "chain_contract": {"generation": 5, "sha256": "chain5"},
                    "fdb_status": "current",
                    "chain_status": "stale-or-unverified",
                    "verification": {
                        "verified": False,
                        "reason": "hub-dependency-contract-stale",
                        "fdb_adoption_verified": True,
                        "chain_adoption_verified": False,
                    },
                }
            ],
            "topology_verification": {
                "verified": False,
                "reason": "one-or-more-accepted-hubs-unverified",
            },
        },
    )
    assert harness.state["dependency_rectification_required"] == [
        {
            "hub_id": "mainneta-hub1",
            "dependency": "chain",
            "adopted_generation": 5,
            "adopted_sha256": "chain5",
            "current_generation": 6,
            "current_sha256": "chain6",
        }
    ]


def test_final_inspect_rejects_non_drift_verification_failure(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "add-hub", hub="mainneta-hub1"))
    harness.state.update(
        {
            "starting_generation": 0,
            "target_generation": 1,
            "target_hub_count": 1,
            "resolved_host": "coolify-a",
            "fdb_contract": {"generation": 8, "sha256": "fdb8"},
            "chain_contract": {"generation": 5, "sha256": "chain5"},
        }
    )
    with pytest.raises(MODULE.HarnessError, match="could not independently verify"):
        harness._validate_step(
            "final-inspect",
            {
                "status": "accepted",
                "accepted_generation": 1,
                "fdb_contract": {"generation": 8, "sha256": "fdb8", "status": "current"},
                "chain_contract": {"generation": 5, "sha256": "chain5", "status": "current"},
                "hubs": [
                    {
                        "hub_id": "mainneta-hub1",
                        "host_id": "coolify-a",
                        "fdb_contract": {"generation": 8, "sha256": "fdb8"},
                        "chain_contract": {"generation": 5, "sha256": "chain5"},
                        "verification": {"verified": False, "reason": "hub-health-unverified"},
                    }
                ],
                "topology_verification": {"verified": False},
            },
        )


def test_final_inspect_rejects_accepted_hub_contract_that_differs_from_frozen_target(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "add-hub", hub="mainneta-hub1"))
    harness.state.update(
        {
            "starting_generation": 0,
            "target_generation": 1,
            "target_hub_count": 1,
            "resolved_host": "coolify-a",
            "fdb_contract": {"generation": 8, "sha256": "fdb8"},
            "chain_contract": {"generation": 5, "sha256": "chain5"},
        }
    )
    with pytest.raises(MODULE.HarnessError, match="differs from frozen prep target"):
        harness._validate_step(
            "final-inspect",
            {
                "status": "accepted",
                "accepted_generation": 1,
                "hubs": [
                    {
                        "hub_id": "mainneta-hub1",
                        "host_id": "coolify-a",
                        "fdb_contract": {"generation": 8, "sha256": "fdb8"},
                        "chain_contract": {"generation": 6, "sha256": "chain6"},
                    }
                ],
                "topology_verification": {"verified": True},
            },
        )

def test_final_inspect_accepts_guarded_full_deletion(tmp_path: Path) -> None:
    harness = Harness(
        _args(tmp_path, "remove-hub", hub="mainneta-hub1", allow_full_deletion=True)
    )
    harness.state.update(
        {
            "starting_generation": 4,
            "target_generation": 5,
            "target_hub_count": 0,
            "full_deletion": True,
        }
    )
    harness._validate_step(
        "final-inspect",
        {
            "status": "accepted-empty",
            "accepted_generation": 5,
            "hubs": [],
            "empty_topology_verification": {"verified": True},
        },
    )


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
            "accepted_generation": 4,
            "hubs": [
                {"hub_id": "mainneta-hub1", "host_id": "coolify-a", "status": "running"}
            ],
            "fdb_contract": {"generation": 8, "status": "current"},
            "chain_contract": {"generation": 12, "status": "current"},
            "topology_verification": {"verified": True},
        },
    }

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
    assert harness.run() == 0
    stdout = capsys.readouterr().out
    assert "=== Hub inspection ===" in stdout
    assert "mainneta-hub1" in stdout
    assert "FDB contract:" in stdout
    assert "Chain contract:" in stdout
    assert "tools.hub_control" not in stdout


def test_internal_command_is_saved_but_not_printed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = Harness(_args(tmp_path, "add-hub"))
    payload = {
        "ok": True,
        "result": {
            "status": "accepted",
            "accepted_generation": 4,
            "hubs": [],
            "topology_verification": {"verified": True},
        },
    }

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(MODULE, "_run_with_live_stderr", fake_run)
    harness._run_step("pre-inspect", harness.inspect_cmd())
    stdout = capsys.readouterr().out
    assert "tools.hub_control" not in stdout
    command_text = (harness.run_dir / "00-pre-inspect.command.txt").read_text(encoding="utf-8")
    assert "tools.hub_control" in command_text


def test_preinspect_accepts_verified_unborn_for_first_add(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "add-hub", hub="mainneta-hub1"))
    harness._validate_step(
        "pre-inspect",
        {
            "status": "unborn",
            "accepted_generation": 0,
            "hubs": [],
            "unborn_topology_verification": {"verified": True},
        },
    )
    assert harness.state["starting_generation"] == 0
    assert harness.state["starting_status"] == "unborn"
    assert harness.state["starting_hub_count"] == 0


def test_prepared_boundary_reports_core_and_advertised_chain_preflight(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = Harness(_args(tmp_path, "add-hub", hub="mainneta-hub1", execute=True))
    harness.state.update(
        {
            "starting_generation": 0,
            "target_hub_count": 1,
            "resolved_controller": "coolify-a",
            "resolved_host": "coolify-a",
            "fdb_contract": {"generation": 8, "sha256": "fdb"},
            "chain_contract": {"generation": 12, "sha256": "chain"},
            "chain_preflight": {
                "verified": True,
                "chain_id": 42424240,
                "required_contracts_verified": True,
                "optional_stale_contracts": ["hub_credit_bridge_escrow", "alpha-beta-lockout"],
            },
            "rebirth": True,
        }
    )

    harness._print_prepared_boundary()
    stdout = capsys.readouterr().out
    assert "RPC:                 verified" in stdout
    assert "chain ID:            42424240" in stdout
    assert "core requirements:   verified" in stdout
    assert "advertised stale:    hub_credit_bridge_escrow, alpha-beta-lockout" in stdout
    assert "FDB preflight:         contract verified" in stdout


def test_do_summary_reports_exact_coolify_deployment_identity(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = Harness(_args(tmp_path, "add-hub", hub="mainneta-hub1", execute=True))
    harness._print_step_result(
        "do",
        {
            "status": "deployed",
            "details": {
                "deployment_action": "created",
                "deployment_uuid": "dep-123",
                "deployment_status": "finished",
                "deployment_commit": "abc123",
                "hub_running": True,
                "fdb_adoption_verified": True,
                "chain_adoption_verified": True,
            },
        },
    )
    stdout = capsys.readouterr().out
    assert "deployment UUID:      dep-123" in stdout
    assert "deployment status:    finished" in stdout
    assert "deployment commit:    abc123" in stdout
    assert "FDB adoption:         verified" in stdout
    assert "Chain adoption:       verified" in stdout


def test_run_step_enables_hub_progress_for_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(_args(tmp_path, "add-hub"))
    payload = {
        "ok": True,
        "result": {
            "status": "accepted",
            "accepted_generation": 4,
            "hubs": [],
            "topology_verification": {"verified": True},
        },
    }
    seen_env: dict[str, str] = {}

    def fake_run(*_args, **kwargs):
        seen_env.update(kwargs["env"])
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(MODULE, "_run_with_live_stderr", fake_run)
    harness._run_step("pre-inspect", harness.inspect_cmd())

    assert seen_env["MAIN_COMPUTER_HUB_PROGRESS"] == "1"
    assert seen_env["PYTHONUNBUFFERED"] == "1"


def test_live_stderr_runner_tees_diagnostics_and_captures_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = (
        "import json,sys; "
        "print('progress-one', file=sys.stderr, flush=True); "
        "print(json.dumps({'ok': True, 'result': {'status': 'done'}}))"
    )

    proc = MODULE._run_with_live_stderr(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=os.environ.copy(),
    )

    captured = capsys.readouterr()
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["result"]["status"] == "done"
    assert proc.stderr == "progress-one\n"
    assert captured.out == "progress-one\n"


def test_bridge_contract_preflight_failure_reopens_add_hub_prep_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = Harness(_args(tmp_path, "add-hub", hub="mainneta-hub1", execute=True))
    harness.state.update(
        {
            "last_completed_step": "prep",
            "operation_id": "old-operation",
            "target_generation": 10,
            "target_hub_count": 2,
            "resolved_controller": "coolify-a",
            "resolved_host": "coolify-a",
            "public_url": "https://mainneta-hub1.greatlibrary.io",
            "network_ingress_url": "https://mainnet-hub.greatlibrary.io",
            "fdb_contract": {"generation": 8, "sha256": "fdb"},
            "chain_contract": {"generation": 6, "sha256": "old-chain"},
            "chain_preflight": {"verified": True},
        }
    )
    harness._write_state()
    payload = {
        "ok": False,
        "error": {
            "code": "HUB_BRIDGE_ESCROW_NOT_LIVE",
            "message": "HubCreditBridgeEscrow has no code",
        },
    }

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=2, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(MODULE, "_run_with_live_stderr", fake_run)

    with pytest.raises(HarnessError):
        harness._run_step("do", harness.do_cmd())

    assert harness.state["last_completed_step"] == "pre-inspect"
    assert harness.state["operation_id"] is None
    assert harness.state["target_generation"] is None
    assert harness.state["chain_contract"] is None
    persisted = json.loads((harness.run_dir / "harness-state.json").read_text(encoding="utf-8"))
    assert persisted["last_completed_step"] == "pre-inspect"
    assert "invalidated the frozen add-hub prep" in capsys.readouterr().out


def _topology_report(status: str) -> dict:
    report = {
        "network": "mainnet",
        "status": status,
        "errors": [],
        "accepted_projection": {
            "status": "stale" if status == "DRIFT" else "current",
            "stale_hubs": ["mainneta-hub1", "mainnetc-hub1"] if status == "DRIFT" else [],
            "unprojected_hubs": [],
        },
        "missing_hubs": [],
        "extra_hubs": [],
    }
    if status == "DRIFT":
        report["seal_command"] = "python .\\tools\\hub_topology_seal.py --network mainnet --apply-state"
    if status == "UNKNOWN":
        report["errors"] = [{"code": "HUB_COOLIFY_INVENTORY_UNAVAILABLE", "message": "controller unreachable"}]
    return report


@pytest.mark.parametrize("operation", ["add-hub", "remove-hub"])
def test_prep_topology_drift_offers_direct_seal_without_preinspect_or_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], operation: str,
) -> None:
    harness = Harness(_args(tmp_path, operation, execute=True))
    runs = []

    def fake_run(argv, **kwargs):
        runs.append(argv)
        assert argv[0] == sys.executable
        assert argv[1] == str(tmp_path / "tools" / "hub_topology_check.py")
        assert argv[-4:] == ["--network", "mainnet", "--repo-root", str(tmp_path)]
        return subprocess.CompletedProcess(argv, 1, json.dumps(_topology_report("DRIFT")), "")

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
    monkeypatch.setattr(harness, "_run_step", lambda *_: pytest.fail("pre-inspect must not run on drift"))
    with pytest.raises(HarnessError, match="independently verified Hub topology DRIFT") as exc:
        harness.run()
    assert len(runs) == 1
    assert "stale accepted Hubs: mainneta-hub1, mainnetc-hub1" in str(exc.value)
    assert "python .\\tools\\hub_topology_seal.py --network mainnet --apply-state" in str(exc.value)
    assert "Hub topology: DRIFT" in capsys.readouterr().out
    assert harness.state["last_completed_step"] is None
    assert json.loads((harness.run_dir / "00-topology-check.json").read_text())["status"] == "DRIFT"


def test_prep_topology_unknown_fails_closed_without_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(_args(tmp_path, "add-hub", execute=True))
    monkeypatch.setattr(
        MODULE.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 2, json.dumps(_topology_report("UNKNOWN")), ""),
    )
    monkeypatch.setattr(harness, "_run_step", lambda *_: pytest.fail("pre-inspect must not run on unknown"))
    with pytest.raises(HarnessError, match="cannot be independently verified") as exc:
        harness.run()
    assert "--apply-state" not in str(exc.value)
    assert "HUB_COOLIFY_INVENTORY_UNAVAILABLE" in str(exc.value)


def test_prep_topology_pass_allows_preinspect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    harness = Harness(_args(tmp_path, "add-hub"))
    monkeypatch.setattr(
        MODULE.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, json.dumps(_topology_report("PASS")), ""),
    )
    steps = []

    def fake_step(step, argv):
        steps.append(step)
        raise RuntimeError("stop at pre-inspect")

    monkeypatch.setattr(harness, "_run_step", fake_step)
    with pytest.raises(RuntimeError, match="stop at pre-inspect"):
        harness.run()
    assert steps == ["pre-inspect"]
    assert "Hub topology: PASS" in capsys.readouterr().out


def test_prep_topology_is_rechecked_on_resumed_preinspect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = Harness(_args(tmp_path, "add-hub"))
    first.state["last_completed_step"] = "pre-inspect"
    first._write_state()
    resumed = Harness(_args(tmp_path, "add-hub", execute=True))
    assert resumed._resumed_existing_run
    calls = []
    monkeypatch.setattr(
        MODULE.subprocess, "run",
        lambda argv, **kw: (calls.append(argv) or subprocess.CompletedProcess(
            argv, 1, json.dumps(_topology_report("DRIFT")), ""
        )),
    )
    monkeypatch.setattr(resumed, "_run_step", lambda *_: pytest.fail("stale pre-inspect must not run"))
    with pytest.raises(HarnessError, match="Hub topology DRIFT"):
        resumed.run()
    assert len(calls) == 1
    assert resumed.state["last_completed_step"] == "pre-inspect"


def test_prep_topology_not_rechecked_during_prepared_operation_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = Harness(_args(tmp_path, "add-hub"))
    first.state.update({"last_completed_step": "prep", "operation_id": "existing-op"})
    first._write_state()
    resumed = Harness(_args(tmp_path, "add-hub", execute=True))
    monkeypatch.setattr(
        MODULE.subprocess, "run", lambda *a, **kw: pytest.fail("prepared operation must not recheck topology"),
    )
    monkeypatch.setattr(resumed, "_run_step", lambda *_: (_ for _ in ()).throw(RuntimeError("stop at do")))
    with pytest.raises(RuntimeError, match="stop at do"):
        resumed.run()


def test_prep_topology_rejects_conflicting_exit_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(_args(tmp_path, "add-hub"))
    monkeypatch.setattr(
        MODULE.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, json.dumps(_topology_report("DRIFT")), ""),
    )
    with pytest.raises(HarnessError, match="status/exit disagreement") as exc:
        harness.run()
    assert "--apply-state" not in str(exc.value)


def _empty_receipt_drift() -> dict:
    value = _topology_report("DRIFT")
    value.update({
        "state_hubs_active": [], "state_hubs_inactive": [], "observed_hubs": [],
        "locked_hub_admins": [], "admin_location_drift": [],
    })
    return value


@pytest.mark.parametrize("execute", [False, True])
def test_empty_topology_drift_never_seals_from_add_hub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    execute: bool,
) -> None:
    """Even an authorized first-Hub rebirth is diagnostic-only at this gate."""
    harness = Harness(_args(tmp_path, "add-hub", execute=execute))
    commands = []

    def fake_run(argv, **kwargs):
        commands.append(argv)
        assert argv[1] == str(tmp_path / "tools" / "hub_topology_check.py")
        return subprocess.CompletedProcess(argv, 1, json.dumps(_empty_receipt_drift()), "")

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
    monkeypatch.setattr(harness, "_run_step", lambda *_: pytest.fail("pre-inspect/deployment must not run"))
    with pytest.raises(HarnessError, match="independently verified Hub topology DRIFT") as exc:
        harness.run()
    assert commands == [harness.topology_check_cmd()]  # not Mother reseal or Hub seal
    assert "stale accepted Hubs: mainneta-hub1, mainnetc-hub1" in str(exc.value)
    assert r"python .\tools\hub_topology_seal.py --network mainnet --apply-state" in str(exc.value)
    assert "Hub topology: DRIFT" in capsys.readouterr().out
    assert harness.state["last_completed_step"] is None
    assert not (harness.run_dir / "00-topology-check-after-repair.json").exists()


def test_empty_topology_recheck_after_explicit_operator_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A separate, explicit operator seal is observed on the next invocation."""
    initial = Harness(_args(tmp_path, "add-hub", execute=True))
    responses = iter(("DRIFT", "PASS"))
    seen = []

    def fake_run(argv, **kwargs):
        status = next(responses)
        seen.append(status)
        return subprocess.CompletedProcess(argv, {"DRIFT": 1, "PASS": 0}[status],
                                           json.dumps(_empty_receipt_drift() if status == "DRIFT" else _topology_report("PASS")), "")

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
    monkeypatch.setattr(initial, "_run_step", lambda *_: pytest.fail("must stop on DRIFT"))
    with pytest.raises(HarnessError, match="Run the reseal explicitly"):
        initial.run()

    resumed = Harness(_args(tmp_path, "add-hub", execute=True))
    monkeypatch.setattr(resumed, "_run_step", lambda step, *_: (_ for _ in ()).throw(RuntimeError(f"entered {step}")))
    with pytest.raises(RuntimeError, match="entered pre-inspect"):
        resumed.run()
    assert seen == ["DRIFT", "PASS"]
