from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools.stable_hub_lab.run_lab import build_validate_only_result


DEV_TOPOLOGY = Path("deploy/stable-hub-lab/dev-topology.json")


def test_stable_hub_lab_uses_dev_topology_and_reuses_exp_fdb_cluster_file() -> None:
    result = build_validate_only_result(topology_path=DEV_TOPOLOGY)
    plan = result["plan"]

    assert result["ok"] is True
    assert plan["fdb_cluster_file"] == ".foundationdb/docker.cluster"
    assert plan["storage_namespace"] == "main-computer-stable-hub-dev"
    assert plan["worker_initial_entry"]["hub_id"] == "dev-hub3"
    assert plan["requester_initial_entry"]["hub_id"] == "dev-hub1"


def test_stable_hub_lab_contract_is_msk_and_live_session_first() -> None:
    result = build_validate_only_result(topology_path=DEV_TOPOLOGY)
    contract = result["plan"]["contract"]

    assert contract["auth"] == "multisession-wallet"
    assert contract["worker_connection"] == "long-lived-msk-session"
    assert contract["heartbeat"] == "connection-ping-pong"
    assert contract["availability_source"] == "live-worker-session-owner"


def test_stable_hub_lab_is_separate_from_exp_hub_and_scheduler_lab() -> None:
    lab_source = Path("tools/stable_hub_lab/run_lab.py").read_text(encoding="utf-8")

    assert "from tools.scheduler_lab" not in lab_source
    assert "import tools.scheduler_lab" not in lab_source


def test_stable_hub_lab_validate_only_cli_outputs_contract() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.stable_hub_lab.run_lab",
            "--topology",
            str(DEV_TOPOLOGY),
            "--validate-only",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Stable Hub lab topology validation: ok" in completed.stdout
    assert "Worker initial entry: dev-hub3 http://127.0.0.1:8873" in completed.stdout
    assert "Requester initial entry: dev-hub1 http://127.0.0.1:8871" in completed.stdout
    assert "worker_connection: long-lived-msk-session" in completed.stdout


def test_stable_hub_lab_exposes_cluster_runner_flags() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.stable_hub_lab.run_lab",
            "--help",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--serve-cluster" in completed.stdout
    assert "--check-cluster" in completed.stdout
    assert "--check-after-start" in completed.stdout
