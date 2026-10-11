"""The harness offers evidence forwarding only for the exact binding error.

No command is executed by the guidance; normal fail-closed behavior is preserved.
"""

import subprocess
import sys
from pathlib import Path

import pytest

import mother_mutate_harness as mod


ERROR = (
    "MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_PRIVATE_STATE_CHANGED: "
    "Mother private-state binding no longer matches topology evidence\n"
)


def _harness(tmp_path: Path, *, network: str = "mainnet") -> mod.Harness:
    args = mod.build_parser().parse_args([
        "add-node", "--network", network,
        "--runtime-state-root", str(tmp_path / "runtime" / "state"),
        "--baseline-evidence", "old.json",
        "--baseline-evidence-sha256", "a" * 64,
    ])
    return mod.Harness(args)


@pytest.mark.parametrize("channel", ["stderr", "stdout"])
def test_exact_binding_error_offers_command_without_running_it(tmp_path, monkeypatch, capsys, channel):
    harness = _harness(tmp_path)
    calls = []

    def fake_run(argv, cwd, text, capture_output):
        calls.append((argv, cwd, text, capture_output))
        return subprocess.CompletedProcess(
            argv, 2,
            stdout=ERROR if channel == "stdout" else "",
            stderr=ERROR if channel == "stderr" else "",
        )

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    with pytest.raises(SystemExit) as error:
        harness.run("detect-topology", harness.cmd("detect-mother-topology-staleness"))

    assert error.value.code == 2
    assert len(calls) == 1  # No forward script is run automatically.
    output = capsys.readouterr().out
    assert "MOTHER_MUTATE_HARNESS_PRIVATE_STATE_BINDING_FORWARD_AVAILABLE" in output
    assert mod.quote_command([
        sys.executable,
        str(harness.repo_root / "tools" / "mother_forward_topology_evidence_binding.py"),
        "--network", "mainnet", "--runtime-state-root", harness.args.runtime_state_root,
    ]) in output
    assert "without any explicit --baseline-evidence" in output
    assert "Live topology validation and any necessary reseal remain separate steps." in output
    assert "No live mutation was performed" in output
    assert "detect-topology failed with exit code 2" in output
    assert (harness.run_dir / "00-detect-topology.stderr.txt").is_file()


@pytest.mark.parametrize("step,stderr", [
    ("detect-topology", "MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_STALE: old evidence\n"),
    ("detect-topology", "MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_PRIVATE_STATE_CHANGED_BUT_DIFFERENT: not exact\n"),
    ("detect-topology", "unrelated message mentions MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_PRIVATE_STATE_CHANGED: incident\n"),
    ("preflight-paranoia", ERROR),
])
def test_unrelated_failures_do_not_offer_forward(tmp_path, monkeypatch, capsys, step, stderr):
    harness = _harness(tmp_path)
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 2, stdout="", stderr=stderr),
    )
    with pytest.raises(SystemExit) as error:
        harness.run(step, ["fake", "read-only"])
    assert error.value.code == 2
    output = capsys.readouterr().out
    assert "PRIVATE_STATE_BINDING_FORWARD_AVAILABLE" not in output
    assert "mother_forward_topology_evidence_binding.py" not in output


def test_non_mainnet_fails_closed_without_unsupported_command(tmp_path, monkeypatch, capsys):
    harness = _harness(tmp_path, network="testnet")
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 2, stdout="", stderr=ERROR),
    )
    with pytest.raises(SystemExit) as error:
        harness.run("detect-topology", ["fake", "detect"])
    assert error.value.code == 2
    output = capsys.readouterr().out
    assert "evidence-forwarding script supports mainnet only" in output
    assert "mother_forward_topology_evidence_binding.py" not in output
