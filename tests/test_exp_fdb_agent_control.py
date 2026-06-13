from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from main_computer.exp_fdb_agent_control import (
    AgentContainerSpec,
    build_docker_run_command,
    container_name_for_run,
)
from main_computer.exp_fdb_agent_runtime import hub_health_url


ROOT = Path(__file__).resolve().parents[1]


def test_exp_fdb_agent_root_entrypoint_targets_control_module() -> None:
    entrypoint = (ROOT / "exp-fdb-agent.py").read_text(encoding="utf-8")

    assert "main_computer.exp_fdb_agent_control" in entrypoint
    assert "raise SystemExit(main())" in entrypoint


def test_exp_fdb_agent_control_script_exposes_start_shutdown_status_logs() -> None:
    module = (ROOT / "main_computer" / "exp_fdb_agent_control.py").read_text(encoding="utf-8")

    assert "DEFAULT_EXP_FDB_AGENT_HUB_URL" in module
    assert "http://host.docker.internal:8870" in module
    assert "http://127.0.0.1:8870" in module
    assert "runtime/exp-fdb-agent-runs" in module or 'Path("runtime") / "exp-fdb-agent-runs"' in module
    assert "start" in module
    assert "shutdown" in module
    assert "status" in module
    assert "logs" in module
    assert "main-computer.exp-fdb-hub=true" in module
    assert "main-computer.agent.worker-ring" in module
    assert "MAIN_COMPUTER_AGENT_MAX_TOTAL_CREDITS" in module
    assert "MAIN_COMPUTER_AGENT_CREDITS_PER_JOB" in module


def test_docker_run_command_mounts_repo_readonly_and_run_dir_writable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = tmp_path / "runtime" / "exp-fdb-agent-runs" / "run-123"
    spec = AgentContainerSpec(
        run_id="run-123",
        repo_root=repo,
        run_dir=run_dir,
        max_total_credits=50,
        credits_per_job=2,
        agent_command=("python", "-c", "print('agent-child')"),
    )

    command = build_docker_run_command(spec)
    joined = " ".join(command)

    assert command[:2] == ["docker", "run"]
    assert "--name" in command
    assert container_name_for_run("run-123") in command
    assert "main-computer.role=exp-fdb-agent" in command
    assert "main-computer.exp-fdb-hub=true" in command
    assert "main-computer.agent.run-id=run-123" in command
    assert f"{repo.resolve()}:/workspace:ro" in command
    assert f"{run_dir.resolve()}:/agent-run" in command
    assert "HUB_BASE_URL=http://host.docker.internal:8870" in command
    assert "MAIN_COMPUTER_AGENT_WORKER_RING=2" in command
    assert "MAIN_COMPUTER_AGENT_MAX_TOTAL_CREDITS=50" in command
    assert "MAIN_COMPUTER_AGENT_CREDITS_PER_JOB=2" in command
    assert "python -m main_computer.exp_fdb_agent_runtime run" in joined
    assert "-- python -c print('agent-child')" in joined


def test_agent_runtime_writes_durable_state_and_signal_events() -> None:
    runtime = (ROOT / "main_computer" / "exp_fdb_agent_runtime.py").read_text(encoding="utf-8")

    assert "agent-run.json" in runtime
    assert "agent-state.json" in runtime
    assert "agent-events.jsonl" in runtime
    assert "signal.SIGTERM" in runtime
    assert "agent.shutdown" in runtime
    assert "/api/hub/v1/health" in runtime


def test_hub_health_url_joins_exp_fdb_hub_health_endpoint() -> None:
    assert hub_health_url("http://host.docker.internal:8870/") == "http://host.docker.internal:8870/api/hub/v1/health"


def test_export_script_keeps_exp_fdb_agent_entrypoint_in_snapshots() -> None:
    script = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")

    assert '"exp-fdb-hub.py"' in script
    assert '"exp-fdb-agent.py"' in script
