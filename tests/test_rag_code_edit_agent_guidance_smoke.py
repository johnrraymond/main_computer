from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import main_computer.rag_code_edit_agent_guidance_smoke as smoke
from main_computer.rag_code_edit_agent_guidance_smoke import (
    CONTAINER_LIVE_PLAN_PATH,
    CONTAINER_REPLAY_REPORT_PATH,
    CONTAINER_RUN_DIR,
    CONTAINER_SOURCE_DIR,
    DEFAULT_DOCKER_IMAGE,
    DEFAULT_COMMIT_POLICY,
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
        "scenario": "single_file_python_edit",
        "scenario_contracts": smoke.scenario_spec("single_file_python_edit").contracts(),
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
            "verification_outcome_expected": True,
            "scenario_contracts_satisfied": True,
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
        scenario=smoke.DEFAULT_SCENARIO,
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
    assert "--commit-policy" in command
    assert command[command.index("--commit-policy") + 1] == DEFAULT_COMMIT_POLICY



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
        scenario=smoke.DEFAULT_SCENARIO,
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
        scenario=smoke.DEFAULT_SCENARIO,
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



def test_generated_editor_static_preflight_blocks_escape_attempts() -> None:
    source = (
        "import os\n"
        "def edit(files):\n"
        "    open('app.py', 'w').write('bad')\n"
        "    os.system('echo bad')\n"
        "    return {'status': 'done', 'proposed_writes': {}}\n"
    )

    preflight = smoke.generated_editor_static_preflight(source)

    assert preflight["ok"] is False
    assert preflight["generated_editor_present"] is True
    assert preflight["generated_editor_no_imports"] is False
    assert preflight["generated_editor_no_open_eval_exec_subprocess"] is False
    issue_kinds = {issue["kind"] for issue in preflight["issues"]}
    assert "import_not_allowed" in issue_kinds
    assert "blocked_call" in issue_kinds
    assert "blocked_module_call" in issue_kinds


def test_generated_editor_agent_uses_sandbox_proposal_then_host_apply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "generated-editor-agent"
    run_dir.mkdir()
    commands_path = run_dir / "commands.jsonl"
    commands_path.write_text(
        json.dumps({"type": "add_instruction", "text": "Do not modify README.md.", "id": "guidance-001"}) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MAIN_COMPUTER_AGENT_SMOKE_CONTAINER", "1")
    monkeypatch.setenv("MAIN_COMPUTER_AGENT_SMOKE_DOCKER_NETWORK", "none")
    monkeypatch.setenv("MAIN_COMPUTER_AGENT_SMOKE_SOURCE_MOUNT", "readonly")

    args = smoke.build_parser().parse_args(
        [
            "--role",
            "agent",
            "--agent",
            "generated-editor",
            "--run-id",
            "generated-editor-test",
            "--run-dir",
            str(run_dir),
            "--commands-path",
            str(commands_path),
            "--report-path",
            str(run_dir / "report.json"),
            "--guidance-window-seconds",
            "0",
            "--poll-seconds",
            "0.01",
        ]
    )

    assert smoke.run_agent(args) == 0

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    boundary_names = [boundary["name"] for boundary in report["boundaries"]]
    assert report["agent_mode"] == "generated-editor"
    assert report["changed_files"] == ["app.py"]
    assert report["forbidden_paths"] == ["README.md"]
    assert "generated_editor_boundary" in boundary_names
    assert "generated_editor_static_preflight_boundary" in boundary_names
    assert "generated_editor_sandbox_boundary" in boundary_names
    assert "host_apply_boundary" in boundary_names

    contracts = report["contracts"]
    assert contracts["generated_editor_present"] is True
    assert contracts["generated_editor_static_preflight_passed"] is True
    assert contracts["generated_editor_no_imports"] is True
    assert contracts["generated_editor_no_open_eval_exec_subprocess"] is True
    assert contracts["generated_editor_sandbox_executed"] is True
    assert contracts["generated_editor_writes_only_allowed_paths"] is True
    assert contracts["generated_editor_output_matches_plan"] is True
    assert contracts["generated_editor_did_not_touch_forbidden_files"] is True
    assert contracts["generated_editor_worktree_unchanged_during_sandbox"] is True
    assert contracts["host_validated_sandbox_proposal"] is True
    assert contracts["host_applied_sandbox_proposal"] is True
    assert contracts["deterministic_safe_apply_can_be_replaced_by_sandbox_apply"] is True

    generated_editor = report["edit_result"]["generated_editor"]
    assert generated_editor["sandbox"]["worktree_unchanged_during_sandbox"] is True
    assert generated_editor["host_apply"]["changed_files"] == ["app.py"]
    assert generated_editor["host_apply"]["after_sha256_by_path"]["app.py"] == smoke.text_sha256(
        smoke.APP_PY_DETERMINISTIC_FINAL
    )




def test_agent_commit_policy_never_blocks_commit_but_keeps_verified_edit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "commit-never-agent"
    run_dir.mkdir()
    commands_path = run_dir / "commands.jsonl"
    commands_path.write_text(
        json.dumps({"type": "add_instruction", "text": "Do not modify README.md.", "id": "guidance-001"}) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MAIN_COMPUTER_AGENT_SMOKE_CONTAINER", "1")
    monkeypatch.setenv("MAIN_COMPUTER_AGENT_SMOKE_DOCKER_NETWORK", "none")
    monkeypatch.setenv("MAIN_COMPUTER_AGENT_SMOKE_SOURCE_MOUNT", "readonly")

    args = smoke.build_parser().parse_args(
        [
            "--role",
            "agent",
            "--agent",
            "deterministic",
            "--run-id",
            "commit-never-test",
            "--run-dir",
            str(run_dir),
            "--commands-path",
            str(commands_path),
            "--report-path",
            str(run_dir / "report.json"),
            "--guidance-window-seconds",
            "0",
            "--poll-seconds",
            "0.01",
            "--commit-policy",
            "never",
        ]
    )

    assert smoke.run_agent(args) == 0

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    boundary_names = [boundary["name"] for boundary in report["boundaries"]]
    assert "commit_policy_boundary" in boundary_names
    assert "commit_boundary" in boundary_names
    assert report["commit_policy"]["policy"] == "never"
    assert report["commit"]["created"] is False
    assert report["final_head"] == report["base_head"]
    assert report["changed_files"] == ["app.py"]
    assert report["contracts"]["commit_policy_never_blocks_commit"] is True
    assert report["contracts"]["commit_blocked_by_policy"] is True
    assert report["contracts"]["worktree_left_uncommitted_by_policy"] is True
    assert "commit_created" not in report["contracts"]


def test_supervisor_commit_policy_never_reports_no_actual_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The supervisor must not treat a successful commit boundary as a commit."""

    run_id = "supervisor-never"
    run_dir = tmp_path / run_id
    report_path = run_dir / "report.json"

    def fake_require_docker_image(image: str) -> None:
        return None

    class FakeProc:
        def __init__(self) -> None:
            events = [
                {"event": "run_started", "containerized": True},
                {"event": "agent_adapter_selected"},
                {"event": "stage_started", "stage": "fixture_origin"},
                {"event": "guidance_window_open"},
                {"event": "guidance_received"},
                {"event": "boundary_committed", "boundary": "guidance_boundary"},
                {"event": "edit_applied", "files": ["app.py"]},
                {"event": "verification_passed"},
                {"event": "commit_blocked_by_policy", "commit_policy": "never"},
                {"event": "run_finished", "status": "ok"},
            ]
            self.stdout = io.StringIO("\n".join(smoke.json_dumps(event) for event in events) + "\n")
            self.stderr = io.StringIO("")
            self.returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = 0
            return 0

    def fake_popen(*args, **kwargs) -> FakeProc:
        run_dir.mkdir(parents=True, exist_ok=True)
        report = _sample_agent_report(agent_mode="deterministic")
        report["run_id"] = run_id
        report["base_head"] = "base"
        report["final_head"] = "base"
        report["main_head"] = "base"
        report["commit_policy"] = {"policy": "never", "decision": "never"}
        report["commit"] = {
            "created": False,
            "expected": False,
            "sha": "base",
            "blocked_by_policy": True,
            "policy": "never",
            "decision": "never",
        }
        report["contracts"].pop("commit_created")
        report["contracts"].pop("repo_clean_after_commit")
        report["contracts"].update(
            {
                "commit_policy_never_blocks_commit": True,
                "commit_blocked_by_policy": True,
                "commit_policy_satisfied": True,
                "worktree_left_uncommitted_by_policy": True,
            }
        )
        report["boundaries"].insert(
            -1,
            {"name": "commit_policy_boundary", "path": "commit_policy.json", "sha256": "policy"},
        )
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return FakeProc()

    monkeypatch.setattr(smoke, "require_docker_image", fake_require_docker_image)
    monkeypatch.setattr(smoke.subprocess, "Popen", fake_popen)

    args = smoke.build_parser().parse_args(
        [
            "--work-root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--guidance-window-seconds",
            "0.1",
            "--poll-seconds",
            "0.01",
            "--commit-policy",
            "never",
        ]
    )

    assert smoke.run_supervisor(args) == 0

    supervisor_report = json.loads((run_dir / "supervisor_report.json").read_text(encoding="utf-8"))
    contracts = supervisor_report["contracts"]
    assert supervisor_report["ok"] is True
    assert supervisor_report["commit_created"] is False
    assert supervisor_report["commit_expected"] is False
    assert supervisor_report["commit_blocked_by_policy"] is True
    assert supervisor_report["commit_policy_boundary_written"] is True
    assert supervisor_report["commit_boundary_written"] is True
    assert contracts["commit_not_created_by_policy"] is True
    assert contracts["commit_boundary_written"] is True
    assert "commit_created" not in contracts


def test_agent_commit_policy_require_approval_waits_then_commits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "commit-approval-agent"
    run_dir.mkdir()
    commands_path = run_dir / "commands.jsonl"
    commands_path.write_text(
        json.dumps({"type": "add_instruction", "text": "Do not modify README.md.", "id": "guidance-001"}) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MAIN_COMPUTER_AGENT_SMOKE_CONTAINER", "1")
    monkeypatch.setenv("MAIN_COMPUTER_AGENT_SMOKE_DOCKER_NETWORK", "none")
    monkeypatch.setenv("MAIN_COMPUTER_AGENT_SMOKE_SOURCE_MOUNT", "readonly")

    args = smoke.build_parser().parse_args(
        [
            "--role",
            "agent",
            "--agent",
            "deterministic",
            "--run-id",
            "commit-approval-test",
            "--run-dir",
            str(run_dir),
            "--commands-path",
            str(commands_path),
            "--report-path",
            str(run_dir / "report.json"),
            "--guidance-window-seconds",
            "0",
            "--poll-seconds",
            "0.01",
            "--commit-policy",
            "require-approval",
            "--approval-timeout-seconds",
            "5",
        ]
    )

    result: dict[str, int] = {}

    def run() -> None:
        result["returncode"] = smoke.run_agent(args)

    thread = threading.Thread(target=run)
    thread.start()
    events_path = run_dir / "events.jsonl"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if events_path.exists():
            events = _json_events(events_path.read_text(encoding="utf-8"))
            if any(event.get("event") == "commit_approval_waiting" for event in events):
                smoke.append_jsonl(
                    commands_path,
                    {
                        "type": "approve_commit",
                        "id": "approval-001",
                        "source": "pytest",
                        "timestamp": smoke.utc_now(),
                    },
                )
                break
        time.sleep(0.01)
    else:
        pytest.fail("agent did not emit commit_approval_waiting")

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert result["returncode"] == 0

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["commit_policy"]["policy"] == "require-approval"
    assert report["commit_policy"]["decision"] == "approved"
    assert report["commit"]["created"] is True
    assert report["final_head"] != report["base_head"]
    assert report["contracts"]["commit_waited_for_approval"] is True
    assert report["contracts"]["commit_not_created_before_approval"] is True
    assert report["contracts"]["approval_received_while_running"] is True
    assert report["contracts"]["commit_created_after_approval"] is True


def test_generated_editor_cycle_orchestrates_deterministic_then_generated_editor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []

    generated_contracts = {
        "generated_editor_present": True,
        "generated_editor_static_preflight_passed": True,
        "generated_editor_no_imports": True,
        "generated_editor_no_open_eval_exec_subprocess": True,
        "generated_editor_sandbox_executed": True,
        "generated_editor_writes_only_allowed_paths": True,
        "generated_editor_output_matches_plan": True,
        "generated_editor_did_not_touch_forbidden_files": True,
        "generated_editor_worktree_unchanged_during_sandbox": True,
        "host_validated_sandbox_proposal": True,
        "host_applied_sandbox_proposal": True,
        "deterministic_safe_apply_can_be_replaced_by_sandbox_apply": True,
    }

    def fake_run_supervisor(args) -> int:
        calls.append((args.agent, args.run_id, args.compare_report))
        run_dir = Path(args.work_root) / args.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        report = _sample_agent_report(agent_mode=args.agent)
        report["run_id"] = args.run_id
        report["report_path"] = str(run_dir / "report.json")
        if args.agent == "generated-editor":
            report["agent_adapter"] = {
                "agent_mode": "generated-editor",
                "editor_source": "deterministic_fixture",
                "planning_only": False,
                "apply_mode": "sandbox_proposal_host_apply",
                "direct_worktree_mutation_allowed": False,
            }
            report["edit_plan"] = {
                "agent_mode": "generated-editor",
                "selected_files": ["app.py"],
                "allowed_write_paths": ["app.py"],
                "forbidden_paths": ["README.md"],
                "requires_generated_editor": True,
                "apply_mode": "sandbox_proposal_host_apply",
            }
            report["contracts"].update(generated_contracts)
            report["boundaries"] = [
                {"name": "bootstrap_boundary", "path": "bootstrap.json", "sha256": "1"},
                {"name": "guidance_boundary", "path": "guidance.json", "sha256": "2"},
                {"name": "edit_plan_boundary", "path": "edit.json", "sha256": "3"},
                {"name": "generated_editor_boundary", "path": "generated_editor.json", "sha256": "4"},
                {
                    "name": "generated_editor_static_preflight_boundary",
                    "path": "generated_editor_static_preflight.json",
                    "sha256": "5",
                },
                {"name": "generated_editor_sandbox_boundary", "path": "generated_editor_sandbox.json", "sha256": "6"},
                {"name": "host_apply_boundary", "path": "host_apply.json", "sha256": "7"},
                {"name": "verification_boundary", "path": "verification.json", "sha256": "8"},
                {"name": "commit_boundary", "path": "commit.json", "sha256": "9"},
            ]
        (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        supervisor_report = {
            "ok": True,
            "contracts": {
                "agent_containerized": True,
                "report_contract_shape_matches_reference": args.agent == "generated-editor",
                "generated_editor_sandbox_apply": args.agent == "generated-editor",
            },
        }
        (run_dir / "supervisor_report.json").write_text(json.dumps(supervisor_report), encoding="utf-8")
        return 0

    monkeypatch.setattr(smoke, "run_supervisor", fake_run_supervisor)
    args = smoke.build_parser().parse_args(
        [
            "--exercise-generated-editor",
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

    assert smoke.run_generated_editor_cycle(args) == 0
    assert [call[0] for call in calls] == ["deterministic", "generated-editor"]
    assert calls[0][2] == ""
    assert calls[1][2].endswith("cycle-deterministic/report.json")

    cycle_report = json.loads((tmp_path / "cycle" / "generated_editor_cycle_report.json").read_text(encoding="utf-8"))
    assert cycle_report["ok"] is True
    assert cycle_report["contracts"]["deterministic_self_check_passed"] is True
    assert cycle_report["contracts"]["generated_editor_self_check_passed"] is True
    assert cycle_report["contracts"]["generated_editor_agent_mode"] is True
    assert cycle_report["contracts"]["generated_editor_static_preflight_passed"] is True
    assert cycle_report["contracts"]["generated_editor_sandbox_executed"] is True
    assert cycle_report["contracts"]["generated_editor_worktree_unchanged_during_sandbox"] is True
    assert cycle_report["contracts"]["host_validated_sandbox_proposal"] is True
    assert cycle_report["contracts"]["host_applied_sandbox_proposal"] is True
    assert cycle_report["contracts"]["deterministic_safe_apply_can_be_replaced_by_sandbox_apply"] is True
    assert cycle_report["contracts"]["report_contract_shape_matches_reference"] is True



def test_agent_verification_failure_scenario_blocks_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "verification-failure-agent"
    run_dir.mkdir()
    commands_path = run_dir / "commands.jsonl"
    commands_path.write_text(
        json.dumps({"type": "add_instruction", "text": "Do not modify README.md.", "id": "guidance-001"}) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MAIN_COMPUTER_AGENT_SMOKE_CONTAINER", "1")
    monkeypatch.setenv("MAIN_COMPUTER_AGENT_SMOKE_DOCKER_NETWORK", "none")
    monkeypatch.setenv("MAIN_COMPUTER_AGENT_SMOKE_SOURCE_MOUNT", "readonly")

    args = smoke.build_parser().parse_args(
        [
            "--role",
            "agent",
            "--agent",
            "deterministic",
            "--scenario",
            "verification_failure_blocks_commit",
            "--run-id",
            "verification-failure-test",
            "--run-dir",
            str(run_dir),
            "--commands-path",
            str(commands_path),
            "--report-path",
            str(run_dir / "report.json"),
            "--guidance-window-seconds",
            "0",
            "--poll-seconds",
            "0.01",
        ]
    )

    assert smoke.run_agent(args) == 0

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    boundary_names = [boundary["name"] for boundary in report["boundaries"]]
    assert report["scenario"] == "verification_failure_blocks_commit"
    assert report["scenario_contracts"] == smoke.scenario_spec("verification_failure_blocks_commit").contracts()
    assert report["verification"]["ok"] is False
    assert report["commit"]["created"] is False
    assert report["commit"]["blocked_by_verification_failure"] is True
    assert report["final_head"] == report["base_head"]
    assert report["changed_files"] == ["app.py"]
    assert "verification_boundary" in boundary_names
    assert "commit_policy_boundary" in boundary_names
    assert "commit_boundary" in boundary_names
    assert report["contracts"]["verification_failed_as_expected"] is True
    assert report["contracts"]["verification_failure_blocks_commit"] is True
    assert report["contracts"]["commit_not_created_after_verification_failure"] is True
    assert report["contracts"]["scenario_contracts_satisfied"] is True
    assert "commit_created" not in report["contracts"]


def test_scenario_matrix_orchestrates_initial_scenarios(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_run_supervisor(args) -> int:
        calls.append((args.scenario, args.run_id))
        scenario = smoke.scenario_spec(args.scenario)
        run_dir = Path(args.work_root) / args.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        base_head = "base"
        final_head = "final" if scenario.requires_commit else base_head
        report = _sample_agent_report(agent_mode="deterministic")
        report["run_id"] = args.run_id
        report["scenario"] = scenario.name
        report["scenario_contracts"] = scenario.contracts()
        report["changed_files"] = list(scenario.expected_changed_files)
        report["forbidden_paths"] = list(scenario.forbidden_files)
        report["base_head"] = base_head
        report["final_head"] = final_head
        report["main_head"] = base_head
        report["verification"] = {
            "ok": scenario.expects_verification_success,
            "checks": ["python_import_and_greet_contract"],
        }
        report["commit"] = {
            "created": scenario.requires_commit,
            "expected": scenario.requires_commit,
            "sha": final_head,
            "files": list(scenario.expected_changed_files) if scenario.requires_commit else [],
        }
        report["contracts"].update(
            {
                "scenario_contracts_satisfied": True,
                "verification_outcome_expected": True,
                "forbidden_files_unchanged": True,
            }
        )
        if scenario.expects_verification_success:
            report["contracts"]["verification_passed"] = True
            report["contracts"]["commit_created"] = scenario.requires_commit
        else:
            report["contracts"].pop("verification_passed", None)
            report["contracts"].pop("commit_created", None)
            report["contracts"].pop("repo_clean_after_commit", None)
            report["contracts"].update(
                {
                    "verification_failed_as_expected": True,
                    "verification_failure_blocks_commit": True,
                    "commit_not_created_after_verification_failure": True,
                }
            )
            report["commit"]["blocked_by_verification_failure"] = True
        (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        (run_dir / "supervisor_report.json").write_text(
            json.dumps({"ok": True, "contracts": {"scenario_contracts_satisfied": True}}),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(smoke, "run_supervisor", fake_run_supervisor)
    args = smoke.build_parser().parse_args(
        [
            "--exercise-scenario-matrix",
            "--work-root",
            str(tmp_path),
            "--run-id",
            "matrix",
            "--guidance-window-seconds",
            "0.1",
            "--poll-seconds",
            "0.01",
        ]
    )

    assert smoke.run_scenario_matrix(args) == 0

    assert [scenario for scenario, _run_id in calls] == list(smoke.SCENARIO_MATRIX)
    matrix_report = json.loads((tmp_path / "matrix" / "scenario_matrix_report.json").read_text(encoding="utf-8"))
    assert matrix_report["ok"] is True
    assert matrix_report["contracts"]["scenario_matrix_all_passed"] is True
    assert matrix_report["contracts"]["single_file_python_edit_exercised"] is True
    assert matrix_report["contracts"]["forbidden_file_instruction_exercised"] is True
    assert matrix_report["contracts"]["verification_failure_blocks_commit_exercised"] is True
    failure_result = matrix_report["scenario_results"]["verification_failure_blocks_commit"]
    assert failure_result["contracts"]["verification_failed_as_expected"] is True
    assert failure_result["contracts"]["verification_failure_blocks_commit"] is True
