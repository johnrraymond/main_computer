from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("native_mint_mutate_harness", ROOT / "native_mint_mutate_harness.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
Harness = MODULE.Harness


def _args(tmp_path: Path, operation: str = "native-mint", *, execute: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        operation=operation,
        network="mainnet",
        to="deployer" if operation in {"native-mint", "native-mint-open"} else None,
        amount_wei="1000" if operation == "native-mint" else None,
        amount_native=None,
        wei_per_block="100" if operation == "native-mint-open" else None,
        native_per_block=None,
        blocks=3 if operation == "native-mint-open" else 2,
        activation_lead_blocks=180,
        close_lead_blocks=30,
        repo_root=tmp_path,
        execute_mutations=execute,
        yes_i_know_this_mutates_target_host=execute,
    )


def test_native_mint_prep_command_maps_one_block_pulse(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "native-mint"))
    cmd = harness.prep_cmd()
    assert cmd[-9:] == [
        "prep", "mint", "mainnet", "--to", "deployer", "--amount-wei", "1000",
        "--activation-lead-blocks", "180",
    ]


def test_native_mint_open_requires_finite_window(tmp_path: Path) -> None:
    args = _args(tmp_path, "native-mint-open")
    args.blocks = 1
    with pytest.raises(MODULE.HarnessError, match="--blocks >= 2"):
        Harness(args)


def test_native_mint_open_command_has_automatic_expiration_input(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "native-mint-open"))
    cmd = harness.prep_cmd()
    assert "open" in cmd
    assert cmd[cmd.index("--blocks") + 1] == "3"
    assert cmd[cmd.index("--wei-per-block") + 1] == "100"


def test_native_mint_close_maps_to_close_prep(tmp_path: Path) -> None:
    harness = Harness(_args(tmp_path, "native-mint-close"))
    cmd = harness.prep_cmd()
    assert cmd[-5:] == ["prep", "close", "mainnet", "--close-lead-blocks", "30"]


def test_mutation_requires_both_acknowledgements(tmp_path: Path) -> None:
    args = _args(tmp_path, "native-mint", execute=True)
    args.yes_i_know_this_mutates_target_host = False
    with pytest.raises(MODULE.HarnessError, match="requires both"):
        Harness(args).run()


def test_same_request_auto_resumes_unfinished_run(tmp_path: Path) -> None:
    first = Harness(_args(tmp_path, "native-mint"))
    first.state["operation_id"] = "native-mint-test"
    first.state["last_completed_step"] = "prep"
    first._write_state()
    second = Harness(_args(tmp_path, "native-mint", execute=True))
    assert second._resumed_existing_run is True
    assert second.run_dir == first.run_dir
    assert second.state["operation_id"] == "native-mint-test"


def test_non_json_child_failure_surfaces_stderr_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(_args(tmp_path, "native-mint"))

    def fake_run(*_args, **_kwargs):
        import subprocess
        return subprocess.CompletedProcess(
            args=["python"],
            returncode=1,
            stdout="",
            stderr="Traceback line\nValueError: unknown Mother operation kind: 'MOTHER-OP-NATIVE-MINT'\n",
        )

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
    with pytest.raises(MODULE.HarnessError, match="unknown Mother operation kind"):
        harness._invoke("pre-inspect", harness.inspect_cmd(), live_stderr=False)
