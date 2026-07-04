from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _json_events(stdout: str) -> list[dict]:
    events: list[dict] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            events.append(payload)
    return events


def test_deterministic_code_edit_agent_guidance_smoke_contracts(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is required for the deterministic code-editing agent smoke")

    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            sys.executable,
            "-S",
            "main_computer/rag_code_edit_agent_guidance_smoke.py",
            "--work-root",
            str(tmp_path),
            "--guidance-window-seconds",
            "1.0",
            "--poll-seconds",
            "0.02",
        ],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    events = _json_events(proc.stdout)
    names = [event.get("event") for event in events]
    assert "guidance_window_open" in names
    assert "supervisor_guidance_injected" in names
    assert "guidance_received" in names
    assert "edit_applied" in names
    assert "commit_created" in names
    assert names.index("guidance_window_open") < names.index("edit_applied")

    passed = [event for event in events if event.get("event") == "self_check_passed"]
    assert len(passed) == 1
    contracts = passed[0]["contracts"]
    assert contracts["stdout_realtime"] is True
    assert contracts["guidance_written_while_running"] is True
    assert contracts["guidance_seen_by_agent"] is True
    assert contracts["guidance_integrated_before_edit"] is True
    assert contracts["branch_isolated"] is True
    assert contracts["changed_files_scoped"] is True
    assert contracts["forbidden_files_unchanged"] is True
    assert contracts["verification_passed"] is True
    assert contracts["commit_created"] is True

    supervisor_report = json.loads(Path(passed[0]["report_path"]).read_text(encoding="utf-8"))
    agent_report = supervisor_report["agent_report"]
    assert agent_report["changed_files"] == ["app.py"]
    assert agent_report["forbidden_paths"] == ["README.md"]
    assert agent_report["target_branch"] == "ai/smoke-guided-edit"
    assert agent_report["main_head"] == agent_report["base_head"]
