from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("hub_mutate_harness", ROOT / "hub_mutate_harness.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
Harness = MODULE.Harness


def _args(
    tmp_path: Path,
    operation: str,
    *,
    hub: str | None = None,
    execute: bool = False,
    allow_full_deletion: bool = False,
) -> argparse.Namespace:
    if hub is None and operation != "inspect":
        hub = "mainnetc-hub1" if operation == "add-hub" else "mainneta-hub1"
    return argparse.Namespace(
        operation=operation,
        network="mainnet",
        hub=hub,
        repo_root=tmp_path,
        allow_full_deletion=allow_full_deletion,
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


def test_full_deletion_resume_does_not_match_non_destructive_run(tmp_path: Path) -> None:
    first = Harness(_args(tmp_path, "remove-hub", hub="mainneta-hub1"))
    first.state["last_completed_step"] = "prep"
    first._write_state()
    second = Harness(
        _args(tmp_path, "remove-hub", hub="mainneta-hub1", allow_full_deletion=True)
    )
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
        }
    )
    harness._validate_step(
        "final-inspect",
        {
            "status": "accepted",
            "accepted_generation": 5,
            "hubs": [
                {"hub_id": "mainneta-hub1", "host_id": "coolify-a"},
                {"hub_id": "mainnetc-hub1", "host_id": "coolify-c"},
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

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
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
