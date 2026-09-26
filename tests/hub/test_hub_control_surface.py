from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tools.hub_control", "--repo-root", str(tmp_path), "--json", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_internal_inspect_reports_unborn_without_accepted_state(tmp_path: Path) -> None:
    proc = _run(tmp_path, "inspect", "mainnet")
    payload = json.loads(proc.stdout)
    assert proc.returncode == 0
    assert payload["ok"] is True
    assert payload["result"]["status"] == "unborn"
    assert payload["result"]["accepted_generation"] == 0
    assert payload["result"]["unborn_topology_verification"]["verified"] is True


def test_internal_inspect_reports_accepted_empty(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "state" / "hub" / "mainnet" / "accepted.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema": "main-computer.hub-accepted.v1",
                "network": "mainnet",
                "generation": 3,
                "hubs": [],
                "fdb_contract": {"generation": 8, "sha256": "fdb"},
                "chain_contract": {"generation": 12, "sha256": "chain"},
            }
        ),
        encoding="utf-8",
    )
    proc = _run(tmp_path, "inspect", "mainnet")
    payload = json.loads(proc.stdout)
    assert proc.returncode == 0
    result = payload["result"]
    assert result["status"] == "accepted-empty"
    assert result["accepted_generation"] == 3
    assert result["empty_topology_verification"]["verified"] is True


def test_internal_add_surface_is_no_longer_reported_as_implementation_pending(tmp_path: Path) -> None:
    proc = _run(tmp_path, "add-hub", "prep", "mainnet", "--hub", "mainneta-hub1")
    payload = json.loads(proc.stdout)
    assert proc.returncode == 2
    assert payload["ok"] is False
    assert payload["error"]["code"] != "HUB_CONTROL_MUTATION_IMPLEMENTATION_PENDING"
    assert "coolify_hub_cluster" not in proc.stdout


def test_internal_remove_prep_accepts_full_deletion_acknowledgement(tmp_path: Path) -> None:
    proc = _run(
        tmp_path,
        "remove-hub",
        "prep",
        "mainnet",
        "--hub",
        "mainneta-hub1",
        "--allow-full-deletion",
    )
    payload = json.loads(proc.stdout)
    assert proc.returncode == 2
    assert payload["error"]["code"] == "HUB_CONTROL_REMOVE_IMPLEMENTATION_PENDING"
