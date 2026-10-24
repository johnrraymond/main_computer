"""The optional Hub redeploy reminder is the final Mother add-node output."""

import json
from pathlib import Path
from types import SimpleNamespace

import mother_mutate_harness as mother
import pytest


def _harness(tmp_path: Path, operation: str = "add-node", network: str = "testnet"):
    harness = mother.Harness.__new__(mother.Harness)
    harness.repo_root = tmp_path
    harness.run_dir = tmp_path / "harness-runs" / "test"
    harness.args = SimpleNamespace(
        operation=operation,
        network=network,
        start_at="detect-topology",
        runtime_state_root=str(tmp_path / "runtime" / "state"),
    )
    harness.state = {}
    calls = []

    def successful_cleanup(step, argv):
        calls.append((step, argv))
        return {"status": "pass", "summary": {"clean": True}}

    harness.run = successful_cleanup
    # Simulate only the Mother orchestration, not Coolify or blockchain mutation.
    harness.ensure_validator_set_preflight_for_resume = lambda: None
    harness.ensure_snap_preflight_for_resume = lambda: None
    harness.route_after_identity = lambda: "single-node"
    harness.run_steps = lambda steps, start: (
        harness.step_pristine_cleanup()
        if mother.PRISTINE_CLEANUP_STEP in steps else None
    )
    return harness, calls


def _accepted(tmp_path: Path, network: str, hubs):
    path = tmp_path / "runtime" / "state" / "hub" / network / "accepted.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": "main-computer.hub-accepted.v1",
        "network": network,
        "generation": 1,
        "hubs": hubs,
    }), encoding="utf-8")
    return path


@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_reminder_is_last_output_after_completed_harness(tmp_path, capsys, network):
    _accepted(tmp_path, network, [{"hub_id": f"{network}b-hub1"}])
    harness, calls = _harness(tmp_path, network=network)
    harness.run_all()
    out = capsys.readouterr().out
    command = ("python hub_mutate_harness.py redeploy-hubs --network " + network +
               " --execute-mutations --yes-i-know-this-mutates-hub")
    assert len(calls) == 1
    assert calls[0][0] == mother.PRISTINE_CLEANUP_STEP
    assert calls[0][1][1].endswith("mother_pristine_cleanup.py")
    assert out.index("=== harness complete ===") < out.index("logs=")
    assert out.index("logs=") < out.index("=== Hub redeploy reminder")
    assert out.rstrip().endswith(command)
    assert harness.state["pristine_cleanup_result"]["clean"] is True


def test_pristine_cleanup_itself_does_not_print_reminder(tmp_path, capsys):
    _accepted(tmp_path, "testnet", [{"hub_id": "testnetb-hub1"}])
    harness, calls = _harness(tmp_path)
    harness.step_pristine_cleanup()
    assert len(calls) == 1
    assert "Hub redeploy" not in capsys.readouterr().out
    harness.emit_hub_redeploy_reminder()
    assert "Hub redeploy reminder" in capsys.readouterr().out


@pytest.mark.parametrize("present", [False, True])
def test_add_node_without_accepted_hubs_does_not_remind(tmp_path, capsys, present):
    if present:
        _accepted(tmp_path, "testnet", [])
    harness, calls = _harness(tmp_path)
    harness.run_all()
    assert len(calls) == 1
    out = capsys.readouterr().out
    assert "=== harness complete ===" in out
    assert "redeploy-hubs" not in out
    assert out.rstrip().endswith(f"logs={harness.run_dir}")


def test_remove_node_never_emits_reminder(tmp_path, capsys):
    _accepted(tmp_path, "testnet", [{"hub_id": "testnetb-hub1"}])
    harness, calls = _harness(tmp_path, operation="remove-node")
    harness.run_all()
    assert len(calls) == 1
    assert "redeploy-hubs" not in capsys.readouterr().out


def test_invalid_accepted_hub_state_does_not_fail_harness(tmp_path, capsys):
    path = tmp_path / "runtime" / "state" / "hub" / "testnet" / "accepted.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not valid JSON", encoding="utf-8")
    harness, calls = _harness(tmp_path)
    harness.run_all()
    output = capsys.readouterr()
    assert len(calls) == 1
    assert "=== harness complete ===" in output.out
    assert "Hub redeploy reminder unavailable:" in output.err
    assert "redeploy-hubs" not in output.out


def test_failed_pristine_cleanup_emits_no_reminder(tmp_path, capsys):
    _accepted(tmp_path, "testnet", [{"hub_id": "testnetb-hub1"}])
    harness, calls = _harness(tmp_path)
    harness.run = lambda _step, _argv: {"status": "failed", "summary": {"clean": False}}
    harness.run_all()
    assert "redeploy-hubs" not in capsys.readouterr().out


def test_interrupted_harness_emits_no_reminder(tmp_path, capsys):
    _accepted(tmp_path, "testnet", [{"hub_id": "testnetb-hub1"}])
    harness, _calls = _harness(tmp_path)

    def interrupted(*_args):
        raise RuntimeError("simulated Mother failure")

    harness.run_steps = interrupted
    with pytest.raises(RuntimeError, match="simulated Mother failure"):
        harness.run_all()
    assert "redeploy-hubs" not in capsys.readouterr().out
