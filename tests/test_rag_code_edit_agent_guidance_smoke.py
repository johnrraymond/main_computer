from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from main_computer.rag_code_edit_agent_guidance_smoke import (
    CONTAINER_RUN_DIR,
    CONTAINER_SOURCE_DIR,
    DEFAULT_DOCKER_IMAGE,
    build_docker_agent_command,
    docker_image_available,
)


def _json_events(stdout: str) -> list[dict]:
    events: list[dict] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            events.append(payload)
    return events


def test_docker_agent_command_shape_contract(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    command = build_docker_agent_command(
        image=DEFAULT_DOCKER_IMAGE,
        repo_root=repo_root,
        run_dir=run_dir,
        run_id="test-run",
        commands_path=run_dir / "commands.jsonl",
        report_path=run_dir / "report.json",
        agent="deterministic",
        target_branch="ai/smoke-guided-edit",
        task="Make greet trim whitespace.",
        guidance_window_seconds=1.0,
        poll_seconds=0.02,
    )

    assert command[:3] == ["docker", "run", "--rm"]
    assert "--network" in command
    assert command[command.index("--network") + 1] == "none"
    mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]
    assert any(f"target={CONTAINER_RUN_DIR}" in mount and "readonly" not in mount for mount in mounts)
    assert any(f"target={CONTAINER_SOURCE_DIR}" in mount and "readonly" in mount for mount in mounts)
    assert "MAIN_COMPUTER_AGENT_SMOKE_CONTAINER=1" in command
    assert "MAIN_COMPUTER_AGENT_SMOKE_DOCKER_NETWORK=none" in command
    assert "MAIN_COMPUTER_AGENT_SMOKE_SOURCE_MOUNT=readonly" in command
    assert f"{CONTAINER_SOURCE_DIR}/main_computer/rag_code_edit_agent_guidance_smoke.py" in command
    assert f"{CONTAINER_RUN_DIR}/commands.jsonl" in command
    assert f"{CONTAINER_RUN_DIR}/report.json" in command


def test_deterministic_code_edit_agent_guidance_smoke_contracts(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required because the agent must run inside a container")
    if not docker_image_available(DEFAULT_DOCKER_IMAGE):
        pytest.skip(f"{DEFAULT_DOCKER_IMAGE} is not built; run docker compose -f docker-compose.executor.yml build executor-image")

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
        timeout=45,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    events = _json_events(proc.stdout)
    names = [event.get("event") for event in events]
    assert "agent_container_launching" in names
    assert "guidance_window_open" in names
    assert "supervisor_guidance_injected" in names
    assert "guidance_received" in names
    assert "edit_applied" in names
    assert "commit_created" in names
    assert names.index("guidance_window_open") < names.index("edit_applied")

    passed = [event for event in events if event.get("event") == "self_check_passed"]
    assert len(passed) == 1
    contracts = passed[0]["contracts"]
    assert contracts["agent_containerized"] is True
    assert contracts["docker_network_none"] is True
    assert contracts["docker_run_directory_mounted"] is True
    assert contracts["docker_source_mount_read_only"] is True
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
    assert supervisor_report["agent_boundary"] == "docker"
    assert supervisor_report["docker_image"] == DEFAULT_DOCKER_IMAGE
    agent_report = supervisor_report["agent_report"]
    assert agent_report["changed_files"] == ["app.py"]
    assert agent_report["forbidden_paths"] == ["README.md"]
    assert agent_report["target_branch"] == "ai/smoke-guided-edit"
    assert agent_report["main_head"] == agent_report["base_head"]
