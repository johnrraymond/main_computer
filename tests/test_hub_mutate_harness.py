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

# Redeploy operations deliberately share only the operator harness, not the
# membership-mutation stages. These tests fake the Coolify/Hub boundary.

def _redeploy_args(tmp_path: Path, *, all_hubs: bool, execute: bool = False) -> argparse.Namespace:
    return _args(
        tmp_path,
        "redeploy-hubs" if all_hubs else "redeploy-hub",
        hub="" if all_hubs else "mainneta-hub1",
        execute=execute,
    )


def _redeploy_fixture(monkeypatch: pytest.MonkeyPatch):
    from tools.hub_control import redeploy

    accepted = {
        "schema": "main-computer.hub-accepted.v1",
        "network": "mainnet",
        "generation": 12,
        "hubs": [
            {"hub_id": "mainneta-hub1", "controller_id": "coolify-a"},
            {"hub_id": "mainnetc-hub1", "controller_id": "coolify-c"},
        ],
    }
    revision = "a" * 40
    calls: list[tuple] = []

    def selected(_ctx, _network, hub=""):
        members = accepted["hubs"]
        return accepted, [x for x in members if not hub or x["hub_id"] == hub]

    def resolve(_ctx, _network, member):
        calls.append(("preflight", member["hub_id"]))
        return {
            "hub_id": member["hub_id"],
            "controller_id": member["controller_id"],
            "application_uuid": "app-" + member["hub_id"],
        }

    def force(target):
        calls.append(("force", target["hub_id"]))
        return "deploy-" + target["hub_id"]

    def wait(target, uuid):
        calls.append(("wait", target["hub_id"], uuid))
        return {"waited": True, "commit": revision, "status": "finished"}

    def verify(target):
        calls.append(("verify", target["hub_id"]))

    monkeypatch.setattr(redeploy, "accepted_targets", selected)
    monkeypatch.setattr(redeploy, "git_revision", lambda *_: revision)
    monkeypatch.setattr(redeploy, "resolve_existing_hub", resolve)
    monkeypatch.setattr(redeploy, "force_rebuild", force)
    monkeypatch.setattr(redeploy, "deployment_history", lambda target: [])
    monkeypatch.setattr(redeploy, "wait_for_build", wait)
    monkeypatch.setattr(redeploy, "verify_runtime", verify)
    monkeypatch.setattr(redeploy, "synchronize_bridge_signer", lambda ctx, target: calls.append(("signer", target["hub_id"])))
    monkeypatch.setattr(redeploy, "failed_previous_build", lambda target, uuid: False)
    return calls, accepted, redeploy


def test_redeploy_parser_and_modes(tmp_path: Path) -> None:
    assert MODULE._parser().parse_args(["redeploy-hub", "--hub", "mainneta-hub1"]).operation == "redeploy-hub"
    assert MODULE._parser().parse_args(["redeploy-hubs"]).operation == "redeploy-hubs"
    with pytest.raises(HarnessError, match="requires --hub"):
        Harness(_args(tmp_path, "redeploy-hub", hub=""))
    with pytest.raises(HarnessError, match="does not accept --hub"):
        Harness(_args(tmp_path, "redeploy-hubs", hub="mainneta-hub1"))
    with pytest.raises(HarnessError, match="only to remove-hub"):
        Harness(_args(tmp_path, "redeploy-hubs", hub="", allow_full_deletion=True))


def test_redeploy_reports_no_commit_needed_before_hub_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    calls, _, redeploy = _redeploy_fixture(monkeypatch)
    original_resolve = redeploy.resolve_existing_hub

    def resolve_after_git(ctx, network, member):
        assert "no commit required for redeployment" in capsys.readouterr().out
        return original_resolve(ctx, network, member)

    monkeypatch.setattr(redeploy, "resolve_existing_hub", resolve_after_git)
    assert Harness(_redeploy_args(tmp_path, all_hubs=False)).run() == 0
    assert len([call for call in calls if call[0] == "preflight"]) == 1


def test_redeploy_git_block_stops_before_hub_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, redeploy = _redeploy_fixture(monkeypatch)

    def git_block(_root, _network):
        raise redeploy.RedeployError("HUB_GIT_COMMIT_PUSH_COMMAND: git add -- 'Dockerfile.hub.exp-fdb'")

    monkeypatch.setattr(redeploy, "git_revision", git_block)
    monkeypatch.setattr(redeploy, "resolve_existing_hub", lambda *args: pytest.fail("must check Git first"))
    with pytest.raises(HarnessError, match="HUB_GIT_COMMIT_PUSH_COMMAND"):
        Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True)).run()


def test_redeploy_single_does_not_touch_other_hubs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, _, _ = _redeploy_fixture(monkeypatch)
    harness = Harness(_redeploy_args(tmp_path, all_hubs=False, execute=True))
    assert harness.run() == 0
    assert [item for item in calls if item[0] == "force"] == [("force", "mainneta-hub1")]
    assert harness.state["last_completed_step"] == "final-inspect"


def test_redeploy_all_sequential_and_preserves_accepted_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, accepted, _ = _redeploy_fixture(monkeypatch)
    original = json.dumps(accepted, sort_keys=True)
    harness = Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True))
    assert harness.run() == 0
    assert [x[0] for x in calls if x[0] != "preflight"] == [
        "signer", "force", "wait", "verify", "signer", "force", "wait", "verify"
    ]
    assert json.dumps(accepted, sort_keys=True) == original
    assert [t["status"] for t in harness.state["redeploy"]["targets"]] == ["verified", "verified"]


def test_redeploy_dry_run_preflights_without_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, _, _ = _redeploy_fixture(monkeypatch)
    harness = Harness(_redeploy_args(tmp_path, all_hubs=True))
    assert harness.run() == 0
    assert [x[0] for x in calls] == ["preflight", "preflight"]
    assert harness.state["redeploy"]["targets"][0]["status"] == "pending"


def test_redeploy_resumes_queued_build_without_resubmission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, _, redeploy = _redeploy_fixture(monkeypatch)
    def stop_after_first_queue(_target, _uuid):
        raise redeploy.RedeployError("transient Coolify error")
    monkeypatch.setattr(redeploy, "wait_for_build", stop_after_first_queue)
    first = Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True))
    with pytest.raises(HarnessError, match="transient Coolify error"):
        first.run()
    assert [x for x in calls if x[0] == "force"] == [("force", "mainneta-hub1")]
    assert first.state["redeploy"]["targets"][0]["status"] == "queued"
    monkeypatch.setattr(redeploy, "wait_for_build", lambda target, uuid: {"commit": "a" * 40, "waited": True})
    resumed = Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True))
    assert resumed._resumed_existing_run
    assert resumed.run() == 0
    assert [x for x in calls if x[0] == "force"] == [
        ("force", "mainneta-hub1"), ("force", "mainnetc-hub1")
    ]


def test_redeploy_legacy_405_receipt_auto_recovers_without_manual_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _, redeploy = _redeploy_fixture(monkeypatch)
    first = Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True))
    # Emulate the old GET/405 failure which saved 'triggering' without a UUID
    # or deployment baseline. Coolify confirms it never queued any build.
    original_force = redeploy.force_rebuild
    def rejected(_target):
        raise redeploy.RedeployError("HTTP 405: This endpoint has changed to POST")
    monkeypatch.setattr(redeploy, "force_rebuild", rejected)
    with pytest.raises(HarnessError, match="HTTP 405"):
        first.run()
    first.state["redeploy"]["targets"][0].pop("baseline_uuids", None)
    first.state["redeploy"]["targets"][0].pop("trigger_started_at", None)
    first._write_state()
    monkeypatch.setattr(redeploy, "force_rebuild", original_force)
    resumed = Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True))
    assert resumed.run() == 0
    assert resumed.state["last_completed_step"] == "final-inspect"
    assert [c for c in calls if c[0] == 'force'] == [
        ('force', 'mainneta-hub1'), ('force', 'mainnetc-hub1')
    ]


def test_redeploy_ambiguous_submission_recovers_existing_build_without_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _, redeploy = _redeploy_fixture(monkeypatch)
    def ambiguous(_target):
        raise redeploy.RedeployError("Coolify accepted POST but response was lost")
    monkeypatch.setattr(redeploy, "force_rebuild", ambiguous)
    first = Harness(_redeploy_args(tmp_path, all_hubs=False, execute=True))
    with pytest.raises(HarnessError, match="response was lost"):
        first.run()
    assert first.state["redeploy"]["targets"][0]["status"] == "triggering"
    monkeypatch.setattr(redeploy, "deployment_history", lambda target: [{
        "deployment_uuid": "deploy-mainneta-hub1",
        "force_rebuild": True, "is_api": True, "restart_only": False, "commit": "a"*40,
    }])
    resumed = Harness(_redeploy_args(tmp_path, all_hubs=False, execute=True))
    assert resumed.run() == 0
    assert not any(c[0] == "force" for c in calls)
    assert resumed.state["redeploy"]["targets"][0]["deployment_uuid"] == "deploy-mainneta-hub1"


def test_redeploy_uncertain_submission_cannot_guess_with_untrusted_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _, redeploy = _redeploy_fixture(monkeypatch)
    monkeypatch.setattr(redeploy, "force_rebuild", lambda target: (_ for _ in ()).throw(
        redeploy.RedeployError("unknown response")))
    first = Harness(_redeploy_args(tmp_path, all_hubs=False, execute=True))
    with pytest.raises(HarnessError, match="unknown response"):
        first.run()
    monkeypatch.setattr(redeploy, "deployment_history", lambda target: [{
        "deployment_uuid": "someone-else", "force_rebuild": False,
        "is_api": False, "restart_only": True, "commit": "a"*40,
    }])
    with pytest.raises(HarnessError, match="cannot be attributed"):
        Harness(_redeploy_args(tmp_path, all_hubs=False, execute=True)).run()
    assert not any(c[0] == "force" for c in calls)


def test_redeploy_405_receipt_adopts_updated_revision_if_no_build_was_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _, redeploy = _redeploy_fixture(monkeypatch)
    monkeypatch.setattr(redeploy, "force_rebuild", lambda target: (_ for _ in ()).throw(
        redeploy.RedeployError("HTTP 405")))
    first = Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True))
    with pytest.raises(HarnessError, match="HTTP 405"):
        first.run()
    monkeypatch.setattr(redeploy, "git_revision", lambda *_: "b"*40)
    monkeypatch.setattr(redeploy, "force_rebuild", lambda target: (
        calls.append(("force", target["hub_id"])) or "deploy-"+target["hub_id"]
    ))
    monkeypatch.setattr(redeploy, "wait_for_build", lambda target, uuid: {
        "commit": "b"*40, "waited": True,
    })
    assert Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True)).run() == 0
    assert [c for c in calls if c[0] == "force"] == [
        ("force", "mainneta-hub1"), ("force", "mainnetc-hub1")
    ]


def test_redeploy_revision_change_settles_interrupted_build_then_redeploys_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, accepted, redeploy = _redeploy_fixture(monkeypatch)
    old, new = "a" * 40, "b" * 40
    revision = [old]
    monkeypatch.setattr(redeploy, "git_revision", lambda *_: revision[0])
    builds: dict[str, str] = {}
    submissions: list[tuple[str, str, str]] = []

    def force(target):
        uuid = f"{revision[0][0]}-{target['hub_id']}"
        builds[uuid] = revision[0]
        submissions.append((target["hub_id"], revision[0], uuid))
        return uuid

    interrupt = [True]

    def wait(target, uuid):
        calls.append(("wait", target["hub_id"], uuid))
        if target["hub_id"] == "mainnetc-hub1" and interrupt[0]:
            raise KeyboardInterrupt
        return {"commit": builds[uuid], "waited": True}

    monkeypatch.setattr(redeploy, "force_rebuild", force)
    monkeypatch.setattr(redeploy, "wait_for_build", wait)
    first = Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True))
    with pytest.raises(KeyboardInterrupt):
        first.run()
    assert [x["status"] for x in first.state["redeploy"]["targets"]] == ["verified", "queued"]
    assert len(submissions) == 2
    revision[0] = new
    interrupt[0] = False
    resumed = Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True))
    assert resumed._resumed_existing_run
    assert resumed.run() == 0
    assert [x[1] for x in submissions] == [old, old, new, new]
    assert [x["status"] for x in resumed.state["redeploy"]["targets"]] == ["verified", "verified"]
    assert resumed.state["redeploy"]["revision"] == new
    historical = resumed.state["redeploy_history"]
    assert len(historical) == 1
    assert historical[0]["revision"] == old
    assert [x["status"] for x in historical[0]["targets"]] == ["verified", "built"]
    assert accepted["generation"] == 12


def test_redeploy_revision_change_does_not_submit_if_old_build_uncertain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _, redeploy = _redeploy_fixture(monkeypatch)
    revision = ["a" * 40]
    monkeypatch.setattr(redeploy, "git_revision", lambda *_: revision[0])
    wait_fail = [True]

    def wait(target, uuid):
        if target["hub_id"] == "mainnetc-hub1" and wait_fail[0]:
            raise redeploy.RedeployError("Coolify poll lost connection")
        return {"commit": "a" * 40, "waited": True}

    monkeypatch.setattr(redeploy, "wait_for_build", wait)
    first = Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True))
    with pytest.raises(HarnessError, match="Coolify poll lost connection"):
        first.run()
    assert first.state["redeploy"]["targets"][1]["status"] == "queued"
    calls_before = len([x for x in calls if x[0] == "force"])
    revision[0] = "b" * 40
    with pytest.raises(HarnessError, match="Coolify poll lost connection"):
        Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True)).run()
    assert len([x for x in calls if x[0] == "force"]) == calls_before
    assert first.state["redeploy"]["revision"] == "a" * 40


def test_redeploy_revision_change_dry_run_does_not_touch_prior_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _, redeploy = _redeploy_fixture(monkeypatch)
    first = Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True))
    def interrupt(target, uuid):
        if target["hub_id"] == "mainnetc-hub1":
            raise KeyboardInterrupt
        return {"commit": "a" * 40, "waited": True}
    monkeypatch.setattr(redeploy, "wait_for_build", interrupt)
    with pytest.raises(KeyboardInterrupt):
        first.run()
    monkeypatch.setattr(redeploy, "git_revision", lambda *_: "b" * 40)
    def no_wait(*_args, **_kwargs):
        pytest.fail("dry-run must not settle in-flight deployments")
    monkeypatch.setattr(redeploy, "wait_for_build", no_wait)
    resumed = Harness(_redeploy_args(tmp_path, all_hubs=True))
    assert resumed.run() == 0
    assert resumed.state["redeploy"]["revision"] == "a" * 40
    assert resumed.state["redeploy"]["targets"][1]["status"] == "queued"
    assert "redeploy_history" not in resumed.state


def test_redeploy_fail_first_hub_stops_before_second(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, _, redeploy = _redeploy_fixture(monkeypatch)
    monkeypatch.setattr(redeploy, "verify_runtime", lambda target: (_ for _ in ()).throw(redeploy.RedeployError("not healthy")))
    with pytest.raises(HarnessError, match="not healthy"):
        Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True)).run()
    assert [x for x in calls if x[0] == "force"] == [("force", "mainneta-hub1")]


def test_redeploy_freezes_topology_and_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, accepted, redeploy = _redeploy_fixture(monkeypatch)
    first = Harness(_redeploy_args(tmp_path, all_hubs=True))
    assert first.run() == 0
    accepted["generation"] += 1
    with pytest.raises(HarnessError, match="authority changed"):
        Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True)).run()
    assert not [x for x in calls if x[0] == "force"]


def test_redeploy_rejects_wrong_commit_and_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, _, redeploy = _redeploy_fixture(monkeypatch)
    monkeypatch.setattr(redeploy, "wait_for_build", lambda target, uuid: {"commit": "b" * 40, "waited": True})
    with pytest.raises(HarnessError, match="not requested revision"):
        Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True)).run()
    assert [x for x in calls if x[0] == "force"] == [("force", "mainneta-hub1")]


def test_redeploy_helpers_force_only_build_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.hub_control import redeploy
    from tools.coolify_hub_service import CoolifyResponse
    requests = []

    class Client:
        def request(self, method, path):
            requests.append((method, path))
            return CoolifyResponse(
                ok=True, status=200, method=method, path=path,
                body={"deployments": [{"deployment_uuid": "build-abc"}]},
            )

    monkeypatch.setattr(redeploy, "_client", lambda target: Client())
    uuid = redeploy.force_rebuild({"hub_id": "mainneta-hub1", "application_uuid": "app-abc"})
    assert uuid == "build-abc"
    assert requests == [("POST", "/api/v1/deploy?uuid=app-abc&force=true")]


def test_redeploy_helpers_never_fallback_to_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.hub_control import redeploy
    from tools.coolify_hub_service import CoolifyResponse
    requests = []

    class Client:
        def request(self, method, path):
            requests.append((method, path))
            return CoolifyResponse(ok=False, status=502, method=method, path=path, body={})

    monkeypatch.setattr(redeploy, "_client", lambda target: Client())
    with pytest.raises(redeploy.RedeployError, match="HTTP 502"):
        redeploy.force_rebuild({"hub_id": "mainneta-hub1", "application_uuid": "app-abc"})
    assert requests == [("POST", "/api/v1/deploy?uuid=app-abc&force=true")]


def test_redeploy_coolify_method_rejection_never_retries_get(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.hub_control import redeploy
    from tools.coolify_hub_service import CoolifyResponse
    requests = []

    class Client:
        def request(self, method, path):
            requests.append((method, path))
            return CoolifyResponse(
                ok=False, status=405, method=method, path=path,
                body={"message": "This endpoint has changed to a POST request."},
            )

    monkeypatch.setattr(redeploy, "_client", lambda target: Client())
    with pytest.raises(redeploy.RedeployError, match="HTTP 405"):
        redeploy.force_rebuild({"hub_id": "mainneta-hub1", "application_uuid": "app-abc"})
    assert requests == [("POST", "/api/v1/deploy?uuid=app-abc&force=true")]


def test_redeploy_helper_rejects_absent_app_before_mutation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tools.hub_control import redeploy
    from tools.hub_control.common.models import HubContext
    monkeypatch.setattr(redeploy, "load_private", lambda ctx: {})
    monkeypatch.setattr(redeploy, "controller_coordinates", lambda *args, **kwargs: {"url": "http://coolify", "api_token": "redacted"})
    monkeypatch.setattr(redeploy, "load_current_fdb_contract", lambda *args: argparse.Namespace(payload={}))
    monkeypatch.setattr(redeploy, "load_current_chain_contract", lambda *args: argparse.Namespace(payload={}))
    monkeypatch.setattr(redeploy, "inspect_deployment", lambda target: {"present": False})
    with pytest.raises(redeploy.RedeployError, match="refusing to create"):
        redeploy.resolve_existing_hub(HubContext.from_repo(tmp_path), "mainnet", {
            "hub_id": "mainneta-hub1", "controller_id": "coolify-a", "public_url": "https://a.example",
        })


def test_redeploy_helper_rejects_nonmembers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.hub_control import redeploy
    monkeypatch.setattr(redeploy, "read_accepted", lambda ctx, network: {
        "schema": "main-computer.hub-accepted.v1", "network": network,
        "generation": 1, "hubs": [{"hub_id": "mainneta-hub1"}],
    })
    from tools.hub_control.common.models import HubContext
    with pytest.raises(redeploy.RedeployError, match="not in the accepted"):
        redeploy.accepted_targets(HubContext.from_repo(tmp_path), "mainnet", "mainnetc-hub1")


def test_redeploy_helper_requires_pushed_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.hub_control import redeploy
    monkeypatch.setattr(redeploy, "_git_source_status", lambda *args, **kwargs: {"checked": True, "dirty": False})

    def fake_run(argv, **kwargs):
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "b" * 40 + "\trefs/heads/main\n", "")

    monkeypatch.setattr(redeploy.subprocess, "run", fake_run)
    with pytest.raises(redeploy.RedeployError, match="not pushed"):
        redeploy.git_revision(tmp_path, "mainnet")


def test_redeploy_dirty_source_emits_existing_git_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from tools.hub_control import redeploy
    monkeypatch.setattr(redeploy, "_git_source_status", lambda *args, **kwargs: {
        "checked": True, "dirty": True,
        "paths": [
            {"status": " M", "path": "Dockerfile.hub.exp-fdb"},
            {"status": " M", "path": "main_computer/captain_cli.py"},
            {"status": " M", "path": "main_computer/hub_cli_live_session.py"},
        ],
    })
    with pytest.raises(redeploy.RedeployError, match="HUB_GIT_COMMIT_PUSH_COMMAND"):
        redeploy.git_revision(tmp_path, "mainnet")
    stderr = capsys.readouterr().err
    assert "HUB_GIT_COMMIT_PUSH_COMMAND:" in stderr
    assert (
        "git add -- 'Dockerfile.hub.exp-fdb' 'main_computer/captain_cli.py' "
        "'main_computer/hub_cli_live_session.py'; "
        "if ($LASTEXITCODE -eq 0) { git commit -m 'Update Hub deployment source' }; "
        "if ($LASTEXITCODE -eq 0) { git push origin HEAD }"
    ) in stderr


def test_redeploy_helper_blocks_uncommitted_hub_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.hub_control import redeploy
    monkeypatch.setattr(redeploy, "_git_source_status", lambda *args, **kwargs: {
        "checked": True, "dirty": True,
        "paths": [{"path": "Dockerfile.hub.exp-fdb"}],
    })
    with pytest.raises(redeploy.RedeployError, match="Dockerfile.hub.exp-fdb"):
        redeploy.git_revision(tmp_path, "mainnet")


def test_redeploy_definite_http405_rejection_keeps_resume_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _, redeploy = _redeploy_fixture(monkeypatch)
    def reject(_target):
        raise redeploy.BuildSubmissionRejected("HTTP 405 use POST")
    monkeypatch.setattr(redeploy, "force_rebuild", reject)
    first = Harness(_redeploy_args(tmp_path, all_hubs=False, execute=True))
    with pytest.raises(HarnessError, match="HTTP 405"):
        first.run()
    assert first.state["redeploy"]["targets"][0]["status"] == "pending"
    assert not any(call[0] == "force" for call in calls)


def test_redeploy_history_api_is_read_only_and_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.hub_control import redeploy
    from tools.coolify_hub_service import CoolifyResponse
    calls = []

    class Client:
        def request(self, method, path):
            calls.append((method, path))
            skip = 0 if "skip=0" in path else 50
            body = [{"deployment_uuid": f"build-{skip+i}", "created_at": "2026-10-10T16:00:00Z"}
                    for i in range(50 if skip == 0 else 1)]
            return CoolifyResponse(ok=True, status=200, method=method, path=path, body=body)

    monkeypatch.setattr(redeploy, "_client", lambda target: Client())
    result = redeploy.deployment_history({"hub_id": "mainneta-hub1", "application_uuid": "app-A"})
    assert len(result) == 51
    assert calls == [
        ("GET", "/api/v1/deployments/applications/app-A?skip=0&take=50"),
        ("GET", "/api/v1/deployments/applications/app-A?skip=50&take=50"),
    ]



def test_redeploy_history_accepts_actual_coolify_deployments_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """Coolify returns {deployments: [...]} despite the generated OpenAPI list schema."""
    from tools.hub_control import redeploy
    from tools.coolify_hub_service import CoolifyResponse
    calls = []

    class Client:
        def request(self, method, path):
            calls.append((method, path))
            return CoolifyResponse(
                ok=True, status=200, method=method, path=path,
                body={"deployments": [{
                    "deployment_uuid": "prior-build", "created_at": "2026-10-10T12:00:00Z",
                    "force_rebuild": True, "is_api": True, "restart_only": False,
                }]},
            )

    monkeypatch.setattr(redeploy, "_client", lambda target: Client())
    target = {"hub_id": "mainneta-hub1", "application_uuid": "app-A"}
    records = redeploy.deployment_history(target)
    assert [row["deployment_uuid"] for row in records] == ["prior-build"]
    assert redeploy.reconcile_build_submission(
        target, baseline_uuids=None, started_after="2026-10-10T16:00:00Z",
        revision="a" * 40,
    ) is None
    assert all(method == "GET" for method, _ in calls)


def test_redeploy_history_deployments_envelope_rejects_malformed_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.hub_control import redeploy
    from tools.coolify_hub_service import CoolifyResponse

    class Client:
        def request(self, method, path):
            return CoolifyResponse(ok=True, status=200, method=method, path=path,
                                   body={"deployments": [{"not_a_deployment_uuid": "abc"}]})

    monkeypatch.setattr(redeploy, "_client", lambda target: Client())
    with pytest.raises(redeploy.RedeployError, match="omits UUID"):
        redeploy.deployment_history({"hub_id": "mainneta-hub1", "application_uuid": "app-A"})


def test_redeploy_history_failure_does_not_claim_no_build(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.hub_control import redeploy
    from tools.coolify_hub_service import CoolifyResponse

    class Client:
        def request(self, method, path):
            return CoolifyResponse(ok=False, status=503, method=method, path=path,
                                   body={"message": "unavailable"})

    monkeypatch.setattr(redeploy, "_client", lambda target: Client())
    with pytest.raises(redeploy.RedeployError, match="no additional build requested"):
        redeploy.deployment_history({"hub_id": "mainneta-hub1", "application_uuid": "app-A"})


def test_redeploy_retired_failed_build_retries_only_on_next_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _, redeploy = _redeploy_fixture(monkeypatch)
    def failed_wait(*_args):
        raise redeploy.RedeployError("Coolify build failed")
    monkeypatch.setattr(redeploy, "wait_for_build", failed_wait)
    first = Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True))
    with pytest.raises(HarnessError, match="Coolify build failed"):
        first.run()
    assert [x for x in calls if x[0] == "force"] == [("force", "mainneta-hub1")]
    monkeypatch.setattr(redeploy, "failed_previous_build", lambda target, uuid: uuid == "deploy-mainneta-hub1")
    monkeypatch.setattr(redeploy, "wait_for_build", lambda target, uuid: {
        "waited": True, "commit": "a" * 40, "status": "finished",
    })
    second = Harness(_redeploy_args(tmp_path, all_hubs=True, execute=True))
    assert second.run() == 0
    assert [x for x in calls if x[0] == "force"] == [
        ("force", "mainneta-hub1"), ("force", "mainneta-hub1"), ("force", "mainnetc-hub1"),
    ]
    assert second.state["redeploy"]["targets"][0]["last_failed_deployment_uuid"] == "deploy-mainneta-hub1"


def test_redeploy_previous_build_is_read_only_and_requires_terminal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.hub_control import redeploy
    from tools.coolify_hub_service import CoolifyResponse
    seen = []
    class Client:
        def request(self, method, path, *_args):
            seen.append((method, path))
            return CoolifyResponse(ok=True, status=200, method=method, path=path, body={"status": "failed"})
    monkeypatch.setattr(redeploy, "_client", lambda target: Client())
    assert redeploy.failed_previous_build({"hub_id": "a"}, "example") is True
    assert seen == [("GET", "/api/v1/deployments/example")]


def test_redeploy_console_build_wait_enables_existing_progress_without_leaking_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.hub_control import redeploy
    monkeypatch.delenv("MAIN_COMPUTER_HUB_PROGRESS", raising=False)
    monkeypatch.setattr(redeploy, "_client", lambda target: object())
    def waiter(client, uuid):
        assert os.environ.get("MAIN_COMPUTER_HUB_PROGRESS") == "1"
        return {"waited": True, "commit": "a" * 40}
    monkeypatch.setattr(redeploy, "_wait_for_coolify_deployment", waiter)
    assert redeploy.wait_for_build({"hub_id": "a"}, "uuid")["waited"]
    assert "MAIN_COMPUTER_HUB_PROGRESS" not in os.environ


def test_redeploy_signer_sync_uses_only_verified_mother_and_one_coolify_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from tools.hub_control import redeploy
    from tools.hub_control.common.models import HubContext
    from tools.hub_control.common import admin_identity, deployment
    from tools.mother.common import private_state
    from tools import coolify_hub_service

    hub = "mainneta-hub1"
    address = "0x" + "a" * 40
    network_state = {"networks": {"mainnet": {"hubs": {hub: {"hub_admin": {
        "address": address, "private_key": "0x" + "b" * 64,
    }}}}}}
    import yaml
    monkeypatch.setattr(private_state, "read_private_state", lambda *_args, **_kwargs: SimpleNamespace(
        document_bytes=yaml.safe_dump(network_state).encode(),
    ))
    captured = []
    monkeypatch.setattr(deployment, "_build_bridge_signer_for_deployment", lambda target: (
        captured.append(("build", target["_hub_admin_wallet"]["address"])) or {
            "bundle_b64": "encoded-value", "bundle_sha256": "hash",
        }
    ))
    monkeypatch.setattr(deployment, "_verify_live_bridge_signer_contract", lambda *args: captured.append(("verified",)))
    monkeypatch.setattr(redeploy, "_build_bridge_signer_for_deployment", deployment._build_bridge_signer_for_deployment)
    monkeypatch.setattr(redeploy, "_verify_live_bridge_signer_contract", deployment._verify_live_bridge_signer_contract)
    monkeypatch.setattr(redeploy, "ensure_bridge_controller", lambda _ctx, *, network, target, admin_address: (
        captured.append(("authorize", network, admin_address, target["_hub_admin_wallet"]["address"])) or
        {"verified": True, "already_authorized": False}
    ))
    monkeypatch.setattr(redeploy, "_client", lambda target: object())
    monkeypatch.setattr(coolify_hub_service, "sync_application_env_var", lambda client, **kwargs: (
        captured.append(("sync", kwargs["key"], kwargs["value"])) or {"ok": True}
    ))
    target = {"network": "mainnet", "hub_id": hub, "bridge_signer_required": True,
              "hub_admin_address": address, "application_uuid": "app-a"}
    redeploy.synchronize_bridge_signer(HubContext.from_repo(tmp_path), target)
    assert captured == [
        ("build", address), ("authorize", "mainnet", address, address),
        ("verified",),
        ("sync", "MAIN_COMPUTER_BRIDGE_SIGNER_BUNDLE_B64", "encoded-value"),
    ]
    with pytest.raises(redeploy.RedeployError, match="disagrees with Mother"):
        redeploy.synchronize_bridge_signer(HubContext.from_repo(tmp_path), {**target, "hub_admin_address": "0x"+"c"*40})


def test_redeploy_rejects_failed_onchain_authorization_before_coolify_or_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    import yaml
    from tools.hub_control import redeploy
    from tools.hub_control.common.errors import HubControlError
    from tools.hub_control.common.models import HubContext
    from tools.mother.common import private_state

    hub = "mainneta-hub1"
    address = "0x" + "a" * 40
    state = {"networks": {"mainnet": {"hubs": {hub: {"hub_admin": {
        "address": address, "private_key": "0x" + "b" * 64,
    }}}}}}
    monkeypatch.setattr(private_state, "read_private_state", lambda *_args, **_kwargs: SimpleNamespace(
        document_bytes=yaml.safe_dump(state).encode(),
    ))
    calls = []
    monkeypatch.setattr(redeploy, "_build_bridge_signer_for_deployment", lambda _t: {
        "bundle_b64": "bundle", "bridge_controller_address": address,
    })
    def reject(_ctx, *, network, target, admin_address):
        calls.append(("authorize", network, admin_address))
        raise HubControlError("HUB_BRIDGE_OWNER_MISMATCH", "deployer is not escrow owner")
    monkeypatch.setattr(redeploy, "ensure_bridge_controller", reject)
    monkeypatch.setattr(redeploy, "_verify_live_bridge_signer_contract", lambda *args: pytest.fail("must not verify"))
    monkeypatch.setattr(redeploy, "_client", lambda _t: pytest.fail("must not touch Coolify"))
    target = {"network": "mainnet", "hub_id": hub, "bridge_signer_required": True,
              "hub_admin_address": address, "application_uuid": "app-a"}
    with pytest.raises(HubControlError, match="deployer is not escrow owner"):
        redeploy.synchronize_bridge_signer(HubContext.from_repo(tmp_path), target)
    assert calls == [("authorize", "mainnet", address)]


def test_redeploy_refuses_unverified_bridge_authorization_before_coolify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    import yaml
    from tools.hub_control import redeploy
    from tools.hub_control.common.models import HubContext
    from tools.mother.common import private_state

    hub = "mainneta-hub1"
    address = "0x" + "a" * 40
    state = {"networks": {"mainnet": {"hubs": {hub: {"hub_admin": {
        "address": address, "private_key": "0x" + "b" * 64,
    }}}}}}
    monkeypatch.setattr(private_state, "read_private_state", lambda *_args, **_kwargs: SimpleNamespace(
        document_bytes=yaml.safe_dump(state).encode(),
    ))
    monkeypatch.setattr(redeploy, "_build_bridge_signer_for_deployment", lambda _t: {"bundle_b64": "bundle"})
    monkeypatch.setattr(redeploy, "ensure_bridge_controller", lambda *_args, **_kwargs: {"verified": False})
    monkeypatch.setattr(redeploy, "_verify_live_bridge_signer_contract", lambda *args: pytest.fail("must not verify"))
    monkeypatch.setattr(redeploy, "_client", lambda _t: pytest.fail("must not touch Coolify"))
    target = {"network": "mainnet", "hub_id": hub, "bridge_signer_required": True,
              "hub_admin_address": address, "application_uuid": "app-a"}
    with pytest.raises(redeploy.RedeployError, match="authorization not verified"):
        redeploy.synchronize_bridge_signer(HubContext.from_repo(tmp_path), target)
