"""Pre-birth pool completion on an existing Mother state generation."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from tests.hub.test_hub_admin_wallet_allocation import _bootstrap, _context, _network
from tools.hub_control.common.admin_identity import _paths
from tools.mother.common.hub_admin_pool import genesis_addresses
from tools.mother.common.models import OperationIdentity
from tools.mother.common.private_state import read_private_state


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(root: Path, *, write: bool) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(REPO_ROOT / "tools/mother_identity.py"),
           "prepare-genesis-hub-admins", "--runtime-state-root", str(root),
           "--updated-at", "2026-10-08T21:00:00Z"]
    if write:
        cmd.append("--write")
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)


def test_existing_generation_prep_generates_missing_nine_and_marks_new_genesis(tmp_path: Path):
    ctx = _context(tmp_path)
    state = _network(6)
    state["genesis"] = {"source": "mother-private", "first_topology_mode": "initial"}
    _bootstrap(ctx, state)
    runtime = tmp_path / "runtime/state"
    before = read_private_state(_paths(ctx), operation=OperationIdentity("before", "before", "mainnet", "MOTHER-OP-UPGRADE-HUB"))
    dry = _run(runtime, write=False)
    assert dry.returncode == 0, dry.stderr
    assert "hub admins missing: 9" in dry.stdout
    assert read_private_state(_paths(ctx), operation=OperationIdentity("before", "before", "mainnet", "MOTHER-OP-UPGRADE-HUB")).binding == before.binding
    applied = _run(runtime, write=True)
    assert applied.returncode == 0, applied.stderr
    assert "generated new hub admins: 9" in applied.stdout
    assert "private_key" not in applied.stdout
    current = read_private_state(_paths(ctx), operation=OperationIdentity("after", "after", "mainnet", "MOTHER-OP-UPGRADE-HUB"))
    assert current.binding.generation == before.binding.generation + 1
    network = yaml.safe_load(current.document_bytes)["networks"]["mainnet"]
    assert len(genesis_addresses(network)) == 15
    assert network["genesis"]["hub_admin_pool_count"] == 15
    repeated = _run(runtime, write=True)
    assert repeated.returncode == 0, repeated.stderr
    assert "write performed: no (already prepared)" in repeated.stdout
    assert read_private_state(_paths(ctx), operation=OperationIdentity("after", "after", "mainnet", "MOTHER-OP-UPGRADE-HUB")).binding == current.binding
