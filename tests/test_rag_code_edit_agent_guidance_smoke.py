from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import main_computer.rag_code_edit_agent_guidance_smoke as smoke
from main_computer.rag_code_edit_agent_guidance_smoke import (
    CONTAINER_LIVE_PLAN_PATH,
    CONTAINER_REPLAY_REPORT_PATH,
    CONTAINER_RUN_DIR,
    CONTAINER_SOURCE_DIR,
    DEFAULT_DOCKER_IMAGE,
    build_docker_agent_command,
    compare_report_contract_shape,
    default_live_plan_payload,
    docker_image_available,
    report_contract_shape,
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


def _sample_agent_report(*, agent_mode: str = "deterministic") -> dict:
    return {
        "ok": True,
        "mode": "rag_code_edit_agent_guidance_smoke",
        "scenario": "trim_greeting_with_midrun_guidance",
        "agent_mode": agent_mode,
        "agent_adapter": {
            "agent_mode": agent_mode,
            "replay_source_report_path": CONTAINER_REPLAY_REPORT_PATH if agent_mode == "replay" else "",
            "replay_source_agent_mode": "deterministic" if agent_mode == "replay" else None,
            "replay_source_run_id": "deterministic-run" if agent_mode == "replay" else None,
            "replay_source_changed_files": ["app.py"] if agent_mode == "replay" else [],
        },
        "run_id": f"{agent_mode}-run",
        "target_branch": "ai/smoke-guided-edit",
        "base_head": "base",
        "final_head": "final",
        "main_head": "base",
        "commit": {"created": True, "sha": "final"},
        "changed_files": ["app.py"],
        "guidance_events": [{"type": "add_instruction", "text": "Do not modify README.md."}],
        "forbidden_paths": ["README.md"],
        "verification": {"ok": True, "checks": ["python_import_and_greet_contract"]},
        "contracts": {
            "agent_adapter_selected": True,
            "agent_containerized": True,
            "branch_isolated": True,
            "changed_files_scoped": True,
            "commit_created": True,
            "docker_network_none": True,
            "docker_source_mount_read_only": True,
            "forbidden_files_unchanged": True,
            "guidance_integrated_before_edit": True,
            "guidance_seen": True,
            "repo_clean_after_commit": True,
            "report_written": True,
            "verification_passed": True,
        },
        "failed_contracts": [],
        "boundaries": [
            {"name": "bootstrap_boundary", "path": "bootstrap.json", "sha256": "1"},
            {"name": "guidance_boundary", "path": "guidance.json", "sha256": "2"},
            {"name": "edit_plan_boundary", "path": "edit.json", "sha256": "3"},
            {"name": "verification_boundary", "path": "verification.json", "sha256": "4"},
            {"name": "commit_boundary", "path": "commit.json", "sha256": "5"},
        ],
    }


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



def test_report_contract_shape_ignores_adapter_mode_but_not_schema() -> None:
    deterministic = _sample_agent_report(agent_mode="deterministic")
    replay = _sample_agent_report(agent_mode="replay")

    deterministic_shape = report_contract_shape(deterministic)
    assert deterministic_shape["changed_files"] == ["app.py"]
    assert deterministic_shape["required_boundaries_present"]["commit_boundary"] is True

    comparison = compare_report_contract_shape(deterministic, replay)
    assert comparison["ok"] is True

    broken = _sample_agent_report(agent_mode="replay")
    broken["changed_files"] = ["README.md"]
    broken_comparison = compare_report_contract_shape(deterministic, broken)
    assert broken_comparison["ok"] is False
    assert any(item["field"] == "changed_files" for item in broken_comparison["mismatches"])


def test_replay_agent_command_shape_passes_replay_report_inside_container(tmp_path: Path) -> None:
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
        agent="replay",
        target_branch="ai/smoke-guided-edit",
        task="Make greet trim whitespace.",
        guidance_window_seconds=1.0,
        poll_seconds=0.02,
        replay_report_path=CONTAINER_REPLAY_REPORT_PATH,
    )

    assert "--agent" in command
    assert command[command.index("--agent") + 1] == "replay"
    assert "--replay-report" in command
    assert command[command.index("--replay-report") + 1] == CONTAINER_REPLAY_REPORT_PATH


def test_live_plan_agent_command_shape_passes_plan_inside_container(tmp_path: Path) -> None:
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
        agent="live-plan",
        target_branch="ai/smoke-guided-edit",
        task="Make greet trim whitespace.",
        guidance_window_seconds=1.0,
        poll_seconds=0.02,
        live_plan_path=CONTAINER_LIVE_PLAN_PATH,
    )

    assert "--agent" in command
    assert command[command.index("--agent") + 1] == "live-plan"
    assert "--live-plan-path" in command
    assert command[command.index("--live-plan-path") + 1] == CONTAINER_LIVE_PLAN_PATH


def test_live_plan_adapter_validates_plan_only_contract() -> None:
    args = smoke.build_parser().parse_args(
        [
            "--role",
            "agent",
            "--agent",
            "live-plan",
            "--live-plan-json",
            json.dumps(default_live_plan_payload()),
        ]
    )
    adapter = smoke.build_agent_adapter(args)
    guidance_state = {"forbidden_paths": ["README.md"]}
    plan = adapter.plan("Make greet trim whitespace.", guidance_state)

    assert adapter.metadata()["planning_only"] is True
    assert adapter.metadata()["apply_mode"] == "deterministic_safe_applier"
    assert plan["agent_mode"] == "live-plan"
    assert plan["planning_only"] is True
    assert plan["apply_mode"] == "deterministic_safe_applier"
    assert plan["allowed_write_paths"] == ["app.py"]
    assert plan["forbidden_paths"] == ["README.md"]


def test_replay_cycle_orchestrates_deterministic_then_replay(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, str, str]] = []

    def fake_run_supervisor(args) -> int:
        calls.append((args.agent, args.run_id, args.replay_report, args.compare_report))
        run_dir = Path(args.work_root) / args.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        report = _sample_agent_report(agent_mode=args.agent)
        report["run_id"] = args.run_id
        report["report_path"] = str(run_dir / "report.json")
        if args.agent == "replay":
            report["agent_adapter"]["replay_source_report_path"] = CONTAINER_REPLAY_REPORT_PATH
            report["agent_adapter"]["replay_source_run_id"] = calls[0][1]
        (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        supervisor_report = {
            "ok": True,
            "contracts": {
                "agent_containerized": True,
                "report_contract_shape_matches_reference": args.agent == "replay",
            },
        }
        (run_dir / "supervisor_report.json").write_text(json.dumps(supervisor_report), encoding="utf-8")
        return 0

    monkeypatch.setattr(smoke, "run_supervisor", fake_run_supervisor)
    args = smoke.build_parser().parse_args(
        [
            "--exercise-replay",
            "--work-root",
            str(tmp_path),
            "--run-id",
            "cycle",
            "--guidance-window-seconds",
            "0.1",
            "--poll-seconds",
            "0.01",
        ]
    )

    assert smoke.run_replay_cycle(args) == 0
    assert [call[0] for call in calls] == ["deterministic", "replay"]
    assert calls[0][2] == ""
    assert calls[0][3] == ""
    assert calls[1][2].endswith("cycle-deterministic/report.json")
    assert calls[1][3].endswith("cycle-deterministic/report.json")

    cycle_report = json.loads((tmp_path / "cycle" / "replay_cycle_report.json").read_text(encoding="utf-8"))
    assert cycle_report["ok"] is True
    assert cycle_report["contracts"]["deterministic_self_check_passed"] is True
    assert cycle_report["contracts"]["replay_self_check_passed"] is True
    assert cycle_report["contracts"]["replay_agent_mode"] is True
    assert cycle_report["contracts"]["report_contract_shape_matches_reference"] is True
    assert cycle_report["replay"]["replay_source_report_path"].endswith("cycle-deterministic/report.json")


def test_live_plan_cycle_orchestrates_deterministic_then_live_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_run_supervisor(args) -> int:
        calls.append((args.agent, args.run_id, args.compare_report))
        run_dir = Path(args.work_root) / args.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        report = _sample_agent_report(agent_mode=args.agent)
        report["run_id"] = args.run_id
        report["report_path"] = str(run_dir / "report.json")
        report["edit_plan"] = {
            "agent_mode": args.agent,
            "planning_only": args.agent == "live-plan",
            "apply_mode": "deterministic_safe_applier" if args.agent == "live-plan" else "deterministic",
        }
        if args.agent == "live-plan":
            report["agent_adapter"] = {
                "agent_mode": "live-plan",
                "planner_source": CONTAINER_LIVE_PLAN_PATH,
                "planning_only": True,
                "apply_mode": "deterministic_safe_applier",
            }
        (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        supervisor_report = {
            "ok": True,
            "contracts": {
                "agent_containerized": True,
                "report_contract_shape_matches_reference": args.agent == "live-plan",
                "live_plan_deterministic_apply": args.agent == "live-plan",
            },
        }
        (run_dir / "supervisor_report.json").write_text(json.dumps(supervisor_report), encoding="utf-8")
        return 0

    monkeypatch.setattr(smoke, "run_supervisor", fake_run_supervisor)
    args = smoke.build_parser().parse_args(
        [
            "--exercise-live-plan",
            "--work-root",
            str(tmp_path),
            "--run-id",
            "cycle",
            "--guidance-window-seconds",
            "0.1",
            "--poll-seconds",
            "0.01",
        ]
    )

    assert smoke.run_live_plan_cycle(args) == 0
    assert [call[0] for call in calls] == ["deterministic", "live-plan"]
    assert calls[0][2] == ""
    assert calls[1][2].endswith("cycle-deterministic/report.json")

    cycle_report = json.loads((tmp_path / "cycle" / "live_plan_cycle_report.json").read_text(encoding="utf-8"))
    assert cycle_report["ok"] is True
    assert cycle_report["contracts"]["deterministic_self_check_passed"] is True
    assert cycle_report["contracts"]["live_plan_self_check_passed"] is True
    assert cycle_report["contracts"]["live_plan_agent_mode"] is True
    assert cycle_report["contracts"]["live_plan_planning_only"] is True
    assert cycle_report["contracts"]["deterministic_safe_apply_after_live_plan"] is True
    assert cycle_report["contracts"]["report_contract_shape_matches_reference"] is True



def test_replay_cycle_smoke_contracts(tmp_path: Path) -> None:
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
            "--exercise-replay",
        ],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    events = _json_events(proc.stdout)
    passed = [event for event in events if event.get("event") == "replay_cycle_passed"]
    assert len(passed) == 1
    contracts = passed[0]["contracts"]
    assert contracts["deterministic_self_check_passed"] is True
    assert contracts["replay_self_check_passed"] is True
    assert contracts["deterministic_agent_containerized"] is True
    assert contracts["replay_agent_containerized"] is True
    assert contracts["replay_agent_mode"] is True
    assert contracts["replay_recorded_source_report"] is True
    assert contracts["replay_compared_against_deterministic"] is True
    assert contracts["report_contract_shape_matches_reference"] is True


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
