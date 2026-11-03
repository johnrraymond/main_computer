"""Patch preservation gates that do not require a running Windows Main Computer."""
from __future__ import annotations

from datetime import datetime, timezone

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
from main_computer.task_manager import TaskManagerService
import main_computer.task_manager as task_module

ROOT = Path(__file__).resolve().parents[1]


def test_direct_port_preservation_contracts():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the browser-port preservation gate")
    completed = subprocess.run([node, str(ROOT / "tools/mcel_migration_preservation_gate.js")],
                               cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert len(result["checks"]) == 4


def test_official_ir_declares_consequential_task_effects():
    ir = compile_semantic_app("task-manager")
    intents = {v["id"]: v for v in ir["intents"]}
    for name in ("terminate-process", "kill-process", "start-server", "restart-server",
                 "shutdown-server", "create-schedule", "delete-schedule", "run-schedule"):
        assert intents["intent:" + name]["risk"] == "consequential"
    assert "capability:task-control" in {v["id"] for v in ir["capabilities"]}
    assert "capability:task-scheduling" in {v["id"] for v in ir["capabilities"]}
    assert {v["id"] for v in compile_semantic_app("file-manager")["intents"]} >= {
        "intent:preview-entry", "intent:open-with", "intent:enter-directory"}


def test_generation_mismatch_blocks_process_termination(tmp_path):
    if task_module.psutil is None:
        pytest.skip("psutil is required for generation-attested process operations")
    service = TaskManagerService(tmp_path, default_port=18765)
    fake_process = Mock()
    fake_process.create_time.return_value = 1000.0
    with patch.object(task_module.psutil, "Process", return_value=fake_process):
        with pytest.raises(ValueError, match="generation mismatch"):
            service.perform_action(action="terminate_pid", pid=24001, confirm=True,
                                   expected_created_at="1970-01-01T00:00:01+00:00")
        fake_process.terminate.assert_not_called()
        correct = service._format_epoch(1000.0)
        result = service.perform_action(action="terminate_pid", pid=24001, confirm=True,
                                        expected_created_at=correct)
    fake_process.terminate.assert_called_once_with()
    # The service returns the verified OS generation at the canonical
    # millisecond precision expected by the semantic capability port.
    assert datetime.fromisoformat(result["verified_created_at"]).timestamp() == (
        datetime.fromisoformat(correct).timestamp())
    assert result["verified_created_at"] == datetime.fromtimestamp(
        1000.0, timezone.utc).isoformat(timespec="milliseconds")
    assert result["pid"] == 24001
