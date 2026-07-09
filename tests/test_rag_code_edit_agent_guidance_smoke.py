from __future__ import annotations

import argparse
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


def _path_text_endswith(path_text: str, suffix: str) -> bool:
    return path_text.replace("\\", "/").endswith(suffix)


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
        "active_constraints": {
            "forbidden_files": ["README.md"],
            "pinned_files": [],
            "required_tests": [],
            "freeform_instructions": ["Do not modify README.md."],
        },
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
            "guidance_compaction_boundary_written": True,
            "avoid_file_command_added_forbidden_file": True,
            "pin_file_command_added_pinned_file": True,
            "request_test_command_added_required_test": True,
            "plan_consumed_active_constraints": True,
            "plan_respects_compacted_forbidden_files": True,
            "plan_respects_compacted_pinned_files": True,
            "required_tests_satisfied": True,
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
    assert plan["active_constraints"]["forbidden_files"] == ["README.md"]
    assert smoke.validate_plan_active_constraints(plan, plan["active_constraints"])["plan_consumed_active_constraints"] is True


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
    assert _path_text_endswith(calls[1][2], "cycle-deterministic/report.json")
    assert _path_text_endswith(calls[1][3], "cycle-deterministic/report.json")

    cycle_report = json.loads((tmp_path / "cycle" / "replay_cycle_report.json").read_text(encoding="utf-8"))
    assert cycle_report["ok"] is True
    assert cycle_report["contracts"]["deterministic_self_check_passed"] is True
    assert cycle_report["contracts"]["replay_self_check_passed"] is True
    assert cycle_report["contracts"]["replay_agent_mode"] is True
    assert cycle_report["contracts"]["report_contract_shape_matches_reference"] is True
    assert _path_text_endswith(cycle_report["replay"]["replay_source_report_path"], "cycle-deterministic/report.json")


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
    assert _path_text_endswith(calls[1][2], "cycle-deterministic/report.json")

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




def test_generated_editor_structured_steering_constraints_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "structured-steering-agent"
    run_dir.mkdir()
    commands_path = run_dir / "commands.jsonl"
    commands_path.write_text(
        "\n".join(json.dumps(command) for command in smoke.guidance_commands_for_scenario(
            smoke.scenario_spec("structured_steering_constraints"),
            "Keep greeting punctuation unchanged.",
        ))
        + "\n",
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
            "--scenario",
            "structured_steering_constraints",
            "--run-id",
            "structured-steering-test",
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
    constraints = report["active_constraints"]
    plan = report["edit_plan"]
    generated_editor = report["edit_result"]["generated_editor"]
    contracts = report["contracts"]

    assert constraints == {
        "forbidden_files": ["README.md"],
        "pinned_files": ["app.py"],
        "required_tests": ["python_import_and_greet_contract"],
        "freeform_instructions": ["Keep greeting punctuation unchanged."],
    }
    assert report["guidance_events"] == [
        {"index": 0, "id": "avoid-readme", "type": "avoid_file", "path": "README.md"},
        {"index": 1, "id": "pin-app", "type": "pin_file", "path": "app.py"},
        {"index": 2, "id": "test-greet", "type": "request_test", "name": "python_import_and_greet_contract"},
        {
            "index": 3,
            "id": "freeform-001",
            "type": "add_instruction",
            "text": "Keep greeting punctuation unchanged.",
        },
    ]
    assert report["rejected_guidance_events"] == []
    assert constraints["freeform_instructions"] == ["Keep greeting punctuation unchanged."]
    assert "Keep greeting punctuation unchanged." not in constraints["forbidden_files"]
    assert "Keep greeting punctuation unchanged." not in constraints["pinned_files"]

    assert plan["active_constraints"] == constraints
    assert plan["selected_files"] == ["app.py"]
    assert plan["allowed_write_paths"] == ["app.py"]
    assert "README.md" not in plan["selected_files"]
    assert "README.md" not in plan["allowed_write_paths"]

    assert generated_editor["sandbox"]["allowed_paths"] == ["app.py"]
    assert generated_editor["sandbox"]["changed_paths"] == ["app.py"]
    assert generated_editor["host_apply"]["changed_files"] == ["app.py"]
    assert generated_editor["host_apply"]["validations"]["host_apply_rechecked_active_constraints"] is True
    assert report["verification"]["checks"] == ["python_import_and_greet_contract"]

    for contract_name in [
        "guidance_compaction_boundary_written",
        "avoid_file_command_added_forbidden_file",
        "pin_file_command_added_pinned_file",
        "request_test_command_added_required_test",
        "plan_consumed_active_constraints",
        "plan_respects_compacted_forbidden_files",
        "plan_respects_compacted_pinned_files",
        "generated_editor_consumed_active_constraints",
        "generated_editor_respects_compacted_constraints",
        "host_apply_rechecked_active_constraints",
        "required_tests_satisfied",
    ]:
        assert contracts[contract_name] is True



def test_ai_restart_live_ring3_probe_prompt_is_compact_and_goal_locked() -> None:
    directive = "Runtime directive: strip names before formatting."
    prompt = smoke.ai_restart_live_ring3_probe_user_prompt(
        goal_directive=directive,
        round_type="request_verify",
        sample_index=2,
        sample_count=3,
        prior_result_ids=["live-ri-001", "live-ri-002"],
    )
    payload = json.loads(prompt)
    contract = smoke.ai_restart_directive_contract(directive)

    assert len(prompt) < 1800
    assert smoke.TEST_APP_PY not in prompt
    assert payload["stage"] == "ring3_live_request_verify"
    assert payload["result_id"] == "live-rv-002"
    assert payload["goal_directive_sha256"] == contract["directive_sha256"]
    assert payload["goal_directive"]["directive_sha256"] == contract["directive_sha256"]
    assert payload["required_keys"] == [
        "goal_directive_sha256",
        "hub_reliability_score",
        "result_id",
        "risks",
        "round_type",
        "selected_files",
        "summary",
    ]
    assert "Do not return only summary/risks" in payload["copy_contract"]
    assert payload["required_response"]["goal_directive_sha256"] == contract["directive_sha256"]
    assert payload["required_response"]["result_id"] == "live-rv-002"
    assert payload["required_response"]["round_type"] == "request_verify"


def test_ai_restart_live_ring3_probe_uses_host_envelope_when_model_omits_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directive = "Runtime directive: strip names before formatting."
    contract = smoke.ai_restart_directive_contract(directive)
    calls: list[str] = []

    def fake_call_live_ai_json(**kwargs: object) -> tuple[dict[str, object], dict[str, object]]:
        stage = str(kwargs["stage"])
        calls.append(stage)
        round_type = "_".join(stage.removeprefix("ring3_live_").split("_")[:-1])
        sample_index = int(stage.rsplit("_", 1)[1])
        metadata = {
            "stage": stage,
            "provider": "ollama",
            "model": "gemma4:26b",
            "uses_live_ai": True,
            "content_sha256": f"sha-{stage}",
            "payload_keys": [],
        }
        if round_type == "request_inquiry":
            return {"summary": "model gave useful inquiry text", "risks": []}, metadata
        return {
            "goal_directive_sha256": contract["directive_sha256"],
            "hub_reliability_score": 1.0,
            "result_id": smoke.ai_restart_live_ring3_probe_result_id(round_type, sample_index),
            "risks": [],
            "round_type": round_type,
            "selected_files": ["app.py"],
            "summary": "model echoed host fields",
        }, metadata

    monkeypatch.setattr(smoke, "call_live_ai_json", fake_call_live_ai_json)
    args = argparse.Namespace(
        ai_restart_live_ring3_probe=True,
        scripted_ai_smoke=False,
        ring3_inquiry_count=3,
        ring3_check_count=3,
        ring3_verify_count=3,
        ring3_merge_count=3,
        ai_provider="ollama",
        ai_model="gemma4:26b",
        ai_command="",
        ai_timeout_seconds=300.0,
    )

    summary = smoke.run_ai_restart_live_ring3_probe(
        args=args,
        run_id="host-envelope-probe-test",
        run_dir=tmp_path,
        goal_directive=directive,
    )

    assert len(calls) == 12
    assert summary["ok"] is True
    assert summary["finished_live_ai_calls"] == 12
    assert summary["failed_live_ai_calls"] == 0
    assert summary["host_goal_bound_live_ai_calls"] == 12
    assert summary["model_goal_acknowledged_live_ai_calls"] == 9
    assert summary["model_result_id_echo_count"] == 9
    assert summary["model_contract_failure_count"] == 6
    assert summary["contracts"]["ai_restart_live_ring3_probe_all_host_envelopes_bound_goal"] is True
    assert summary["contracts"]["ai_restart_live_ring3_probe_all_host_result_ids_present"] is True
    assert summary["model_echo_contracts"]["ai_restart_live_ring3_probe_model_echoed_goal_on_all_calls"] is False
    assert summary["records"][0]["result_id"] == "live-ri-001"
    assert summary["records"][0]["host_envelope"]["goal_directive_sha256"] == contract["directive_sha256"]
    assert summary["records"][0]["model_contract_failures"] == [
        "missing_or_wrong_goal_directive_sha256",
        "missing_result_id",
    ]


def test_ollama_json_adapter_reports_empty_thinking_without_dumping_raw_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_thinking = "PRIVATE_CHAIN_OF_THOUGHT " * 100

    def fake_open_url_json(url: str, payload: dict[str, object], *, timeout_seconds: float, headers: dict[str, str] | None = None) -> dict[str, object]:
        return {
            "model": "gemma4:26b",
            "done": True,
            "done_reason": "length",
            "total_duration": 123,
            "prompt_eval_count": 42,
            "eval_count": 1024,
            "message": {
                "role": "assistant",
                "content": "",
                "thinking": raw_thinking,
            },
        }

    monkeypatch.setattr(smoke, "_open_url_json", fake_open_url_json)

    with pytest.raises(smoke.AIResponseContractFailure) as exc_info:
        smoke.call_ollama_ai_json(
            system_prompt="Return JSON.",
            user_prompt="{}",
            model="gemma4:26b",
            timeout_seconds=1,
        )

    message = str(exc_info.value)
    diagnostics = exc_info.value.diagnostics
    assert "thinking_present=True" in message
    assert "thinking_char_count=" in message
    assert "PRIVATE_CHAIN_OF_THOUGHT" not in message
    assert diagnostics["ai_response_failure_kind"] == "truncated_response"
    assert diagnostics["done_reason"] == "length"
    assert diagnostics["thinking_present"] is True
    assert diagnostics["thinking_char_count"] == len(raw_thinking)
    assert diagnostics["content_char_count"] == 0


def _write_fake_ai_command(tmp_path: Path, final_app_py: str | None = None) -> str:
    script_path = tmp_path / "fake_ai_provider.py"
    if final_app_py is None:
        final_app_py = """def greet(name: str) -> str:
    cleaned = name.strip()
    return f"Hello, {cleaned}!"


if __name__ == "__main__":
    print(greet("world"))
"""
    script_path.write_text(
        f"""
import json
import sys

request = json.loads(sys.stdin.read())
user = json.loads(request["user"])
stage = user.get("stage")
goal_directive_sha256 = user.get("goal_directive", {{}}).get("directive_sha256", "")
if str(stage).startswith("ring3_live_"):
    response = {{
        "result_id": user.get("result_id", "live-rx-000"),
        "round_type": user.get("round_type", ""),
        "selected_files": ["app.py"],
        "goal_directive_sha256": goal_directive_sha256,
        "hub_reliability_score": 1.0,
        "summary": "fake command provider completed " + str(stage),
        "risks": [],
    }}
elif stage == "planning":
    active_constraints = user["active_constraints"]
    response = {{
        "selected_files": ["app.py"],
        "allowed_write_paths": ["app.py"],
        "active_constraints_ack": active_constraints,
        "required_tests": active_constraints.get("required_tests", []),
        "goal_directive_sha256": goal_directive_sha256,
        "rationale": "fake command provider selected the pinned app.py file",
    }}
else:
    response = {{
        "final_app_py": {final_app_py!r},
        "goal_directive_sha256": goal_directive_sha256,
        "rationale": "fake command provider returned final app.py for " + str(stage),
    }}
print(json.dumps(response))
""",
        encoding="utf-8",
    )
    return subprocess.list2cmdline([sys.executable, "-S", str(script_path)])


def _run_ai_restart_recovery(
    *,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scripted_ai_smoke: bool,
) -> dict[str, object]:
    run_dir = tmp_path / ("scripted_ai_restart_run" if scripted_ai_smoke else "live_ai_restart_run")
    run_dir.mkdir()
    directive = "Runtime directive: make greet(name) strip leading/trailing whitespace before greeting and preserve punctuation."
    commands_path = run_dir / "commands.jsonl"
    commands_path.write_text(
        "\n".join(
            json.dumps(command)
            for command in smoke.guidance_commands_for_scenario(
                smoke.scenario_spec(smoke.AI_RESTART_RECOVERY_SCENARIO),
                "Keep greeting punctuation unchanged.",
                ai_restart_directive=directive,
            )
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MAIN_COMPUTER_AGENT_SMOKE_CONTAINER", "1")
    monkeypatch.setenv("MAIN_COMPUTER_AGENT_SMOKE_DOCKER_NETWORK", "none")
    monkeypatch.setenv("MAIN_COMPUTER_AGENT_SMOKE_SOURCE_MOUNT", "readonly")

    first_argv = [
        "--role",
        "agent",
        "--use-ai",
        "--ai-restart-directive",
        directive,
        "--scenario",
        smoke.AI_RESTART_RECOVERY_SCENARIO,
        "--run-id",
        "ai-restart-recovery-test",
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
        "--stop-after",
        "guidance_compaction",
    ]
    second_argv = [
        "--role",
        "agent",
        "--use-ai",
        "--restart",
        "--ai-restart-directive",
        directive,
        "--scenario",
        smoke.AI_RESTART_RECOVERY_SCENARIO,
        "--run-id",
        "ai-restart-recovery-test",
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
        "--inject-bad-ai-result",
        "forbidden_file_write",
    ]
    if scripted_ai_smoke:
        first_argv.append("--scripted-ai-smoke")
        second_argv.append("--scripted-ai-smoke")

    first_args = smoke.build_parser().parse_args(first_argv)

    assert smoke.run_agent(first_args) == 0
    partial_report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    run_state = json.loads((run_dir / "run_state.json").read_text(encoding="utf-8"))
    assert partial_report["partial"] is True
    assert partial_report["stopped_after"] == "guidance_compaction"
    assert run_state["next_stage"] == "edit_plan"
    assert (run_dir / "boundaries" / "guidance_boundary.json").exists()
    assert not (run_dir / "boundaries" / "edit_plan_boundary.json").exists()

    second_args = smoke.build_parser().parse_args(second_argv)

    assert smoke.run_agent(second_args) == 0

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    contracts = report["contracts"]
    boundary_names = [boundary["name"] for boundary in report["boundaries"]]
    recovery = report["edit_result"]["generated_editor"]["recovery"]

    assert report["agent_mode"] == "ai-generated-editor"
    assert report["goal_directive"]["directive"] == directive
    assert report["goal_directive"]["directive_sha256"] == smoke.text_sha256(directive)
    assert report["edit_plan"]["task"] == directive
    assert report["edit_plan"]["goal_directive"]["acknowledged"] is True
    assert report["restart"]["enabled"] is True
    assert report["restart"]["resumed_from_stage"] == "edit_plan"
    assert report["restart"]["reused_boundaries"] == ["bootstrap_boundary", "guidance_boundary"]
    assert report["active_constraints"]["forbidden_files"] == ["README.md"]
    assert report["changed_files"] == ["app.py"]
    assert (Path(report["worktree"]) / "README.md").read_text(encoding="utf-8") == smoke.README_MD

    assert "ai_generated_editor_attempt_1_boundary" in boundary_names
    assert "ai_generated_editor_attempt_1_sandbox_boundary" in boundary_names
    assert "host_apply_rejection_boundary" in boundary_names
    assert "host_apply_boundary" in boundary_names
    assert recovery["attempts"] == 2
    assert recovery["injected_bad_result"] == "forbidden_file_write"
    assert recovery["rejected_paths"] == ["README.md"]

    for contract_name in [
        "restart_loaded_existing_run_dir",
        "restart_resumed_after_guidance_compaction",
        "restart_preserved_active_constraints",
        "restart_reused_completed_boundaries",
        "ai_attempt_1_recorded",
        "bad_ai_result_injected",
        "host_apply_rejected_forbidden_file_from_ai_output",
        "no_files_changed_after_rejected_ai_attempt",
        "ai_retry_consumed_rejection_feedback",
        "ai_attempt_2_recorded",
        "ai_retry_removed_forbidden_file",
        "host_apply_accepted_corrected_ai_output",
        "ai_plan_acknowledged_goal_directive",
        "ai_restart_goal_directive_present",
        "ai_restart_goal_directive_persisted_through_guidance",
        "ai_restart_goal_directive_sha256_recorded",
        "ai_restart_goal_directive_matches_task",
        "ai_attempt_1_acknowledged_goal_directive",
        "ai_attempt_2_acknowledged_goal_directive",
        "host_apply_rechecked_active_constraints",
        "verification_passed",
        "commit_created",
        "final_report_records_restart_and_recovery",
    ]:
        assert contracts[contract_name] is True

    return report


def test_scripted_ai_restart_recovery_harness_offline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = _run_ai_restart_recovery(monkeypatch=monkeypatch, tmp_path=tmp_path, scripted_ai_smoke=True)

    assert report["edit_plan"]["scripted_ai_smoke"] is True
    assert report["edit_plan"]["uses_live_ai"] is False
    assert report["contracts"]["ai_attempt_1_used_scripted_ai_smoke"] is True
    assert report["contracts"]["ai_attempt_2_used_scripted_ai_smoke"] is True
    assert "ai_attempt_1_used_live_ai" not in report["contracts"]
    assert "ai_attempt_2_used_live_ai" not in report["contracts"]
    assert report["edit_result"]["generated_editor"]["recovery"]["retry_ai_editor"]["metadata"]["uses_live_ai"] is False


def test_ai_restart_recovery_smoke_script_has_simple_direct_surface(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "direct-ai-restart-recovery-smoke"
    directive = "CLI directive: trim greet input whitespace while keeping punctuation stable."

    assert smoke.main([
        "--ai-restart-recovery-smoke",
        "--scripted-ai-smoke",
        "--ai-restart-directive",
        directive,
        "--run-dir",
        str(run_dir),
    ]) == 0

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "ai_restart_recovery_smoke_summary.json").read_text(encoding="utf-8"))

    assert report["ok"] is True
    assert summary["ok"] is True
    assert summary["live_ai_call_count"] == 0
    assert summary["goal_directive"]["directive"] == directive
    assert report["goal_directive"]["directive"] == directive
    assert report["edit_plan"]["task"] == directive
    assert report["contracts"]["ai_restart_goal_directive_present"] is True
    assert report["contracts"]["ai_plan_acknowledged_goal_directive"] is True
    assert report["agent_mode"] == "ai-generated-editor"
    assert report["scenario"] == smoke.AI_RESTART_RECOVERY_SCENARIO
    assert report["commands_path"].endswith("commands.jsonl")
    assert report["runtime"]["local_agent_smoke_allowed"] is True
    assert report["runtime"]["containerized"] is False
    assert report["contracts"]["local_agent_smoke_explicitly_allowed"] is True
    assert report["contracts"]["local_agent_ai_network_exception_declared"] is True
    assert report["contracts"]["bad_ai_result_injected"] is True
    assert report["contracts"]["host_apply_rejected_forbidden_file_from_ai_output"] is True
    assert report["contracts"]["host_apply_accepted_corrected_ai_output"] is True
    assert report["changed_files"] == ["app.py"]
    assert report["failed_contracts"] == []


def test_ai_restart_recovery_smoke_live_command_surface_records_three_ai_calls(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "direct-live-ai-command-restart-recovery-smoke"
    directive = "CLI live directive: update greet so names are stripped before formatting the hello message."
    ai_command = _write_fake_ai_command(tmp_path)

    assert smoke.main([
        "--ai-restart-recovery-smoke",
        "--ai-provider",
        "command",
        "--ai-command",
        ai_command,
        "--ai-restart-directive",
        directive,
        "--run-dir",
        str(run_dir),
    ]) == 0

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "ai_restart_recovery_smoke_summary.json").read_text(encoding="utf-8"))
    ai_calls = smoke.read_jsonl(run_dir / "ai_calls.jsonl")

    assert report["ok"] is True
    assert summary["ok"] is True
    assert summary["live_ai_call_count"] == 3
    assert summary["goal_directive"]["directive"] == directive
    assert report["goal_directive"]["directive"] == directive
    assert report["edit_plan"]["task"] == directive
    assert report["contracts"]["ai_restart_goal_directive_present"] is True
    assert report["contracts"]["ai_plan_acknowledged_goal_directive"] is True
    assert report["contracts"]["ai_attempt_1_acknowledged_goal_directive"] is True
    assert report["contracts"]["ai_attempt_2_acknowledged_goal_directive"] is True
    assert summary["ai_call_summary"]["finished_live_stages"] == [
        "planning",
        "editor_generation",
        "editor_generation_retry",
    ]
    assert [record["event"] for record in ai_calls].count("ai_call_started") == 3
    assert [record["event"] for record in ai_calls].count("ai_call_finished") == 3
    assert report["contracts"]["live_ai_call_count_at_least_3"] is True
    assert report["contracts"]["live_ai_touched_planning_editor_and_retry"] is True
    assert report["contracts"]["ai_attempt_1_used_live_ai"] is True
    assert report["contracts"]["ai_attempt_2_used_live_ai"] is True
    assert report["failed_contracts"] == []


def test_ai_restart_recovery_smoke_live_ring3_probe_records_actual_provider_calls(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "direct-live-ai-command-restart-ring3-probe-smoke"
    directive = "CLI live directive: update greet so names are stripped before formatting the hello message."
    ai_command = _write_fake_ai_command(tmp_path)

    assert smoke.main([
        "--ai-restart-recovery-smoke",
        "--ai-provider",
        "command",
        "--ai-command",
        ai_command,
        "--ai-restart-live-ring3-probe",
        "--ai-restart-directive",
        directive,
        "--run-dir",
        str(run_dir),
    ]) == 0

    summary = json.loads((run_dir / "ai_restart_recovery_smoke_summary.json").read_text(encoding="utf-8"))
    probe = json.loads((run_dir / "ai_restart_live_ring3_probe.json").read_text(encoding="utf-8"))
    ai_calls = smoke.read_jsonl(run_dir / "ai_calls.jsonl")

    assert summary["ok"] is True
    assert probe["ok"] is True
    assert summary["ai_restart_live_ring3_probe"]["ok"] is True
    assert probe["expected_live_ai_calls"] == 12
    assert probe["finished_live_ai_calls"] == 12
    assert probe["failed_live_ai_calls"] == 0
    assert probe["goal_acknowledged_live_ai_calls"] == 12
    assert probe["stage_counts"]["request_inquiry"]["finished"] == 3
    assert probe["stage_counts"]["request_check"]["finished"] == 3
    assert probe["stage_counts"]["request_verify"]["finished"] == 3
    assert probe["stage_counts"]["request_merge"]["finished"] == 3
    assert summary["expected_live_ai_call_count"] == 15
    assert summary["live_ai_call_count"] == 15
    finished_stages = summary["ai_call_summary"]["finished_live_stages"]
    assert "ring3_live_request_inquiry_001" in finished_stages
    assert "ring3_live_request_check_001" in finished_stages
    assert "ring3_live_request_verify_001" in finished_stages
    assert "ring3_live_request_merge_001" in finished_stages
    assert "planning" in finished_stages
    assert "editor_generation" in finished_stages
    assert "editor_generation_retry" in finished_stages
    assert [record["event"] for record in ai_calls].count("ai_call_started") == 15
    assert [record["event"] for record in ai_calls].count("ai_call_finished") == 15



def test_ai_restart_recovery_smoke_accepts_live_ai_output_that_satisfies_goal_without_reference_sha(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "direct-live-ai-command-restart-nonreference-final-smoke"
    directive = "CLI live directive: update greet so names are stripped before formatting the hello message."
    nonreference_final = """def greet(name: str) -> str:
    return "Hello, " + name.strip() + "!"


if __name__ == "__main__":
    print(greet("world"))
"""
    ai_command = _write_fake_ai_command(tmp_path, final_app_py=nonreference_final)

    assert smoke.main([
        "--ai-restart-recovery-smoke",
        "--ai-provider",
        "command",
        "--ai-command",
        ai_command,
        "--ai-restart-directive",
        directive,
        "--run-dir",
        str(run_dir),
    ]) == 0

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    contracts = report["contracts"]

    assert report["ok"] is True
    diagnostics = report["edit_result"]["generated_editor"]["diagnostics"]
    assert diagnostics["generated_editor_output_matches_scenario_reference_sha"] is False
    assert contracts["generated_editor_output_satisfies_runtime_goal_contract"] is True
    assert contracts["generated_editor_output_matches_scenario_contract"] is True
    assert report["failed_contracts"] == []



def test_ai_restart_recovers_from_bad_generated_editor_result_with_live_ai(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if not smoke.live_ai_smoke_configured():
        pytest.skip(
            "set MAIN_COMPUTER_RUN_LIVE_AI_SMOKE=1 and configure "
            "MAIN_COMPUTER_AI_SMOKE_PROVIDER=openai|ollama|command to run the live AI smoke"
        )

    report = _run_ai_restart_recovery(monkeypatch=monkeypatch, tmp_path=tmp_path, scripted_ai_smoke=False)

    assert report["edit_plan"]["uses_live_ai"] is True
    assert report["edit_plan"]["ai_backend"] != "scripted-local-smoke"
    assert report["contracts"]["ai_attempt_1_used_live_ai"] is True
    assert report["contracts"]["ai_attempt_2_used_live_ai"] is True
    assert report["contracts"]["live_ai_call_count_at_least_3"] is True
    assert report["contracts"]["live_ai_touched_planning_editor_and_retry"] is True
    assert report["ai_call_summary"]["finished_live_call_count"] >= 3
    recovery = report["edit_result"]["generated_editor"]["recovery"]
    assert recovery["retry_ai_editor"]["metadata"]["uses_live_ai"] is True
    assert recovery["retry_ai_editor"]["metadata"]["provider"] in {"openai", "ollama", "command"}



def test_ring3_poisoning_smoke_simple_direct_surface(tmp_path: Path) -> None:
    run_dir = tmp_path / "direct-ring3-poisoning-smoke"

    assert smoke.main([
        "--ring3-poisoning-smoke",
        "--run-dir",
        str(run_dir),
    ]) == 0

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "ring3_poisoning_smoke_summary.json").read_text(encoding="utf-8"))

    assert report["ok"] is True
    assert summary["ok"] is True
    assert summary["live_ai_calls"] is False
    assert report["agent_mode"] == "ring3-poisoning-consensus"
    assert report["scenario"] == smoke.RING3_POISONING_CONSENSUS_SCENARIO
    assert report["changed_files"] == ["app.py"]
    assert report["runtime"]["local_agent_smoke_allowed"] is True
    assert report["runtime"]["containerized"] is False
    assert report["contracts"]["local_agent_smoke_explicitly_allowed"] is True
    assert report["contracts"]["local_agent_no_ai_network_exception_needed"] is True
    assert "local_agent_ai_network_exception_declared" not in report["contracts"]

    boundary_names = [boundary["name"] for boundary in report["boundaries"]]
    assert "ring3_worker_results_boundary" in boundary_names
    assert "ring3_worker_policy_boundary" in boundary_names
    assert "host_apply_boundary" in boundary_names

    consensus = report["ring3_worker_consensus"]
    assert consensus["trust_level"] == "ring3_untrusted"
    assert consensus["tainted"] is True
    assert consensus["worker_result_count"] == 3
    assert consensus["selected_worker_id"] == "ring3-safe-worker-c"
    assert consensus["rejected_worker_ids"] == ["ring3-poisoned-worker-a", "ring3-weak-worker-b"]

    validations = {entry["worker_id"]: entry for entry in consensus["worker_validations"]}
    poisoned = validations["ring3-poisoned-worker-a"]
    weak = validations["ring3-weak-worker-b"]
    safe = validations["ring3-safe-worker-c"]
    assert poisoned["accepted"] is False
    assert "forbidden_file_write" in poisoned["rejection_reasons"]
    assert "path_traversal" in poisoned["rejection_reasons"]
    assert "absolute_path" in poisoned["rejection_reasons"]
    assert "test_modification_not_authorized" in poisoned["rejection_reasons"]
    assert "ring3_shell_authority_denied" in poisoned["rejection_reasons"]
    assert "constraint_override_attempt" in poisoned["rejection_reasons"]
    assert "verification_authority_claim" in poisoned["rejection_reasons"]
    assert weak["accepted"] is False
    assert weak["rejection_reasons"] == ["behavioral_contract_failed"]
    assert safe["accepted"] is True
    assert safe["rejection_reasons"] == []

    for contract_name in [
        "restart_loaded_existing_run_dir",
        "restart_resumed_after_guidance_compaction",
        "ring3_worker_outputs_marked_tainted",
        "ring3_tainted_result_never_gets_authority",
        "ring3_forbidden_file_write_rejected",
        "ring3_path_traversal_rejected",
        "ring3_absolute_path_rejected",
        "ring3_test_weakening_rejected",
        "ring3_shell_authority_denied",
        "ring3_constraint_override_rejected",
        "ring3_required_test_override_rejected",
        "ring3_verification_authority_claim_ignored",
        "ring3_behavioral_contract_rejected_weak_candidate",
        "no_worktree_mutation_after_poisoned_worker_rejection",
        "ring3_consensus_selected_single_policy_verified_candidate",
        "host_policy_selected_only_verified_candidate",
        "host_apply_rechecked_active_constraints",
        "verification_passed",
        "commit_created",
    ]:
        assert report["contracts"][contract_name] is True

    assert report["failed_contracts"] == []
    assert (Path(report["worktree"]) / "README.md").read_text(encoding="utf-8") == smoke.README_MD



def test_ring3_evidence_compaction_smoke_expands_forks_and_compacts_with_audit_reasoning(tmp_path: Path) -> None:
    run_dir = tmp_path / "direct-ring3-evidence-compaction-smoke"

    assert smoke.main([
        "--ring3-evidence-compaction-smoke",
        "--ring3-inquiry-count",
        "5",
        "--ring3-check-count",
        "4",
        "--ring3-verify-count",
        "5",
        "--ring3-merge-count",
        "4",
        "--ring3-fork-count",
        "4",
        "--ring3-observation-count",
        "3",
        "--run-dir",
        str(run_dir),
    ]) == 0

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "ring3_evidence_compaction_smoke_summary.json").read_text(encoding="utf-8"))

    assert report["ok"] is True
    assert summary["ok"] is True
    assert summary["live_ai_calls"] is False
    assert report["agent_mode"] == "ring3-evidence-compaction"
    assert report["scenario"] == smoke.RING3_EVIDENCE_COMPACTION_SCENARIO
    assert report["changed_files"] == ["app.py"]

    evidence = report["ring3_evidence_compaction"]
    metrics = evidence["ring3_metrics"]
    call_graph = evidence["ring3_call_graph"]
    workflow_trace = evidence["ring3_agent_workflow_trace"]
    assert evidence["identity_model"] == "anonymous_dispatch_with_hub_result_feedback"
    assert evidence["node_identity_available_to_agent"] is False
    assert evidence["default_hub_reliability_score"] == 1.0
    assert evidence["parallel_counts"] == {
        "request_inquiry": 5,
        "request_check": 4,
        "request_verify": 5,
        "request_merge": 4,
        "candidate_fork": 4,
        "fork_observation": 3,
    }
    assert evidence["selected_candidate_path_id"] == "path-rm-001"
    assert evidence["selected_result_lineage"] == ["rs-ri-001", "rs-ri-003", "rs-rv-001", "rs-rm-001"]
    assert set(evidence["rejected_result_ids"]) >= {"rs-ri-002", "rs-rv-003", "rs-rm-003"}
    assert evidence["compacted_state"]["state_type"] == "compact_agent_state"
    assert evidence["compacted_state"]["known_good_state"]["changed_files"] == ["app.py"]
    assert evidence["compacted_state"]["known_good_state"]["verification"]["success"] is True
    assert evidence["compacted_state"]["remaining_uncertainty"] == []

    assert metrics["format"] == "main_computer_ring3_evidence_metrics_v1"
    assert metrics["deterministic"] is True
    assert metrics["actual_live_ai_calls"] == 0
    assert metrics["modeled_ai_response_samples"] == 14
    assert metrics["modeled_ai_dispatch_requests"] == 18
    assert metrics["modeled_check_requests"] == 4
    assert metrics["host_fork_trials"] == 4
    assert metrics["host_verification_observations"] == 3
    assert metrics["total_modeled_expansion_units"] == 25
    assert len(metrics["per_boundary"]) == 8
    assert {
        boundary["boundary"] for boundary in metrics["per_boundary"]
    } >= {
        "ring3_inquiry_batch_boundary",
        "ring3_check_request_boundary",
        "ring3_verify_batch_boundary",
        "ring3_merge_candidate_boundary",
        "ring3_candidate_fork_boundary",
        "ring3_fork_observation_boundary",
        "ring3_candidate_path_compaction_boundary",
        "ring3_hub_feedback_boundary",
    }
    assert metrics["compaction"]["observed_compaction_ratio"] == {"in": 3, "out": 1, "label": "3:1"}
    assert metrics["compaction"]["policy_compaction_ratio"] == {"in": 7, "out": 1, "label": "7:1"}

    assert call_graph["format"] == "main_computer_ring3_call_graph_v1"
    assert call_graph["deterministic"] is True
    assert call_graph["summary"]["result_count"] == 14
    assert call_graph["summary"]["selected_candidate_path_id"] == "path-rm-001"
    assert call_graph["summary"]["selected_result_lineage"] == evidence["selected_result_lineage"]
    assert any(node["id"] == "ring3_candidate_path_compaction_boundary" for node in call_graph["nodes"])
    assert any(
        edge["from"] == "ring3_hub_feedback_boundary"
        and edge["to"] == "host_apply_boundary"
        and edge["kind"] == "authorizes_compacted_state_for"
        for edge in call_graph["edges"]
    )

    assert workflow_trace["format"] == "main_computer_ring3_open_ended_agent_workflow_v1"
    assert workflow_trace["reference_pattern"] == "website_builder_multi_endpoint_open_ended_smoke"
    assert workflow_trace["deterministic"] is True
    assert workflow_trace["uses_live_ai"] is False
    workflow_intents = {step["intent"] for step in workflow_trace["workflow_steps"]}
    assert workflow_intents >= {
        "grounded_info_answer",
        "promotable_edit_artifact",
        "validated_host_apply",
        "reject_stale_payload",
        "reject_unsafe_payload",
    }
    assert all(workflow_trace["contracts"].values())

    assert summary["ring3_metrics"]["modeled_ai_response_samples"] == 14
    assert summary["ring3_metrics"]["modeled_ai_dispatch_requests"] == 18
    assert summary["ring3_call_graph_summary"]["selected_candidate_path_id"] == "path-rm-001"
    assert summary["ring3_agent_workflow_step_count"] == 5
    assert Path(summary["ring3_metrics_path"]).exists()
    assert Path(summary["ring3_call_graph_path"]).exists()
    assert Path(summary["ring3_agent_workflow_trace_path"]).exists()

    feedback = evidence["hub_feedback"]
    assert feedback["identity_model"] == "hub_result_id_not_node_id"
    assert feedback["node_identity_available_to_agent"] is False
    assert feedback["default_reliability_score"] == 1.0
    rejected_feedback_ids = {entry["result_id"] for entry in feedback["rejected_results"]}
    assert rejected_feedback_ids >= {"rs-ri-002", "rs-rv-003", "rs-rm-003"}
    assert feedback["accepted_result_ids"] == evidence["selected_result_lineage"]

    reasoning = evidence["stage_reasoning"]
    reasoning_stages = {entry["stage"] for entry in reasoning}
    assert reasoning_stages >= {
        "initial_state",
        "ring3_inquiry_batch",
        "ring3_check_request",
        "ring3_verify_batch",
        "ring3_merge_candidate",
        "ring3_candidate_fork",
        "ring3_fork_observation",
        "ring3_candidate_path_compaction",
        "ring3_hub_feedback",
    }
    for entry in reasoning:
        assert entry["reasoning_type"] == "auditable_summary"
        assert set(entry) >= {"inputs", "observations", "decision", "rejected_result_ids", "uncertainty", "next_stage"}

    boundary_names = [boundary["name"] for boundary in report["boundaries"]]
    for boundary_name in [
        "ring3_inquiry_batch_boundary",
        "ring3_check_request_boundary",
        "ring3_verify_batch_boundary",
        "ring3_merge_candidate_boundary",
        "ring3_candidate_fork_boundary",
        "ring3_fork_observation_boundary",
        "ring3_candidate_path_compaction_boundary",
        "ring3_hub_feedback_boundary",
        "host_apply_boundary",
    ]:
        assert boundary_name in boundary_names

    for contract_name in [
        "ring3_parallel_inquiry_count_respected",
        "ring3_parallel_check_count_respected",
        "ring3_parallel_verify_count_respected",
        "ring3_parallel_merge_count_respected",
        "ring3_parallel_fork_count_respected",
        "ring3_parallel_observation_count_respected",
        "ring3_results_have_hub_result_ids",
        "ring3_results_default_reliability_score_1_0",
        "ring3_check_request_includes_inquiry_result_ids",
        "ring3_merge_candidates_preserve_source_lineage",
        "ring3_candidate_forks_created_from_surviving_paths",
        "ring3_compaction_output_matches_initial_state_shape",
        "ring3_hub_feedback_preserves_rejected_result_ids",
        "stage_reasoning_emitted_for_every_ring3_compaction_stage",
        "stage_reasoning_records_inputs_observations_decision_uncertainty_and_next_stage",
        "ring3_metrics_emit_call_budget_by_boundary",
        "ring3_metrics_count_actual_live_ai_calls_zero",
        "ring3_metrics_emit_compaction_ratios",
        "ring3_call_graph_emits_boundaries_results_paths",
        "ring3_call_graph_preserves_selected_lineage",
        "ring3_agent_workflow_has_grounded_answer_surface",
        "ring3_agent_workflow_has_promotable_edit_surface",
        "ring3_agent_workflow_has_validated_apply_surface",
        "ring3_agent_workflow_rejects_stale_payload",
        "ring3_agent_workflow_rejects_unsafe_payload",
        "ring3_agent_workflow_is_deterministic_no_live_ai",
        "host_apply_used_compacted_state_only",
        "verification_passed",
        "commit_created",
    ]:
        assert report["contracts"][contract_name] is True

    assert report["failed_contracts"] == []
    assert (Path(report["worktree"]) / "README.md").read_text(encoding="utf-8") == smoke.README_MD

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
    assert _path_text_endswith(calls[1][2], "cycle-deterministic/report.json")

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




def test_generated_editor_verification_failure_scenario_reports_expected_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "generated-editor-verification-failure-agent"
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
            "--scenario",
            "verification_failure_blocks_commit",
            "--run-id",
            "generated-editor-verification-failure-test",
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
    contracts = report["contracts"]
    generated_editor = report["edit_result"]["generated_editor"]

    assert report["scenario"] == "verification_failure_blocks_commit"
    assert report["verification"]["ok"] is False
    assert report["commit"]["created"] is False
    assert report["commit"]["blocked_by_verification_failure"] is True
    assert report["final_head"] == report["base_head"]
    assert report["changed_files"] == ["app.py"]

    assert contracts["generated_editor_present"] is True
    assert contracts["generated_editor_sandbox_executed"] is True
    assert contracts["generated_editor_output_matches_scenario_contract"] is True
    assert contracts["verification_failed_as_expected"] is True
    assert contracts["verification_failure_blocks_commit"] is True
    assert contracts["commit_not_created_after_verification_failure"] is True
    assert "deterministic_safe_apply_can_be_replaced_by_sandbox_apply" not in contracts

    assert generated_editor["host_apply"]["changed_files"] == ["app.py"]
    assert generated_editor["host_apply"]["after_sha256_by_path"]["app.py"] == smoke.text_sha256(
        smoke.APP_PY_VERIFICATION_FAILURE_FINAL
    )


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
    assert matrix_report["contracts"]["structured_steering_constraints_exercised"] is True
    assert matrix_report["contracts"]["verification_failure_blocks_commit_exercised"] is True
    failure_result = matrix_report["scenario_results"]["verification_failure_blocks_commit"]
    assert failure_result["contracts"]["verification_failed_as_expected"] is True
    assert failure_result["contracts"]["verification_failure_blocks_commit"] is True



def test_open_battery_deterministic_pathway_exercises_open_ended_endstates(tmp_path: Path) -> None:
    run_dir = tmp_path / "open-battery"

    assert smoke.main([
        "--open-battery",
        "--run-dir",
        str(run_dir),
        "--poll-seconds",
        "0.01",
    ]) == 0

    report = json.loads((run_dir / "open_battery_report.json").read_text(encoding="utf-8"))

    assert report["ok"] is True
    assert report["deterministic"] is True
    assert report["uses_live_ai"] is False
    assert report["failed_contracts"] == []
    assert report["contracts"]["open_battery_all_cases_passed"] is True
    assert report["contracts"]["open_battery_all_target_endstates_reached"] is True
    assert report["contracts"]["open_battery_rag_artifacts_written"] is True
    assert report["contracts"]["open_battery_retrieval_traces_written"] is True
    assert report["contracts"]["open_battery_pathway_traces_written"] is True
    assert report["contracts"]["open_battery_every_case_executed_required_pathway_stages"] is True
    assert report["contracts"]["open_battery_minimum_pathway_depth_exercised"] is True
    assert report["contracts"]["open_battery_agent_backed_pathways_exercised"] is True
    assert report["contracts"]["open_battery_strict_suspicious_node_mode"] is True
    assert report["contracts"]["open_battery_suspicious_nodes_exercised"] is True
    assert report["contracts"]["open_battery_untrusted_nodes_never_authoritative"] is True
    assert report["contracts"]["open_battery_host_policy_retrieved_for_every_case"] is True
    assert report["contracts"]["open_battery_trust_boundaries_checked_for_every_case"] is True
    assert report["contracts"]["open_battery_all_target_endstates_exercised"] is True
    assert report["contracts"]["open_battery_byzantine_result_selection_exercised"] is True
    assert report["contracts"]["open_battery_byzantine_round_1_three_results_returned"] is True
    assert report["contracts"]["open_battery_byzantine_round_1_payload_hashes_recorded"] is True
    assert report["contracts"]["open_battery_byzantine_round_2_all_results_sent_to_all_reviewers"] is True
    assert report["contracts"]["open_battery_byzantine_round_2_full_result_payloads_sent_to_all_reviewers"] is True
    assert report["contracts"]["open_battery_byzantine_round_2_input_payload_hashes_match_round_1"] is True
    assert report["contracts"]["open_battery_byzantine_final_round_received_all_round_2_reviews"] is True
    assert report["contracts"]["open_battery_byzantine_final_input_review_hashes_match_round_2"] is True
    assert report["contracts"]["open_battery_byzantine_boundary_payload_hashes_recorded"] is True
    assert report["contracts"]["open_battery_byzantine_agreed_result_hash_matches_round_1"] is True
    assert report["contracts"]["open_battery_byzantine_survivor_result_hashes_match_round_1"] is True
    assert report["contracts"]["open_battery_byzantine_boundary_agreed_result_hash_recorded"] is True
    assert report["contracts"]["open_battery_byzantine_round_2_each_reviewer_rejects_at_most_one"] is True
    assert report["contracts"]["open_battery_byzantine_final_rejects_at_most_one"] is True
    assert report["contracts"]["open_battery_byzantine_final_uses_simple_majority_rejection"] is True
    assert report["contracts"]["open_battery_byzantine_rejection_derivation_recorded"] is True
    assert report["contracts"]["open_battery_byzantine_rejected_result_derived_from_simple_majority"] is True
    assert report["contracts"]["open_battery_byzantine_boundary_exposes_rejection_derivation"] is True
    assert report["contracts"]["open_battery_byzantine_survivor_ranking_completion_recorded"] is True
    assert report["contracts"]["open_battery_byzantine_first_place_votes_derived_from_completed_survivor_rankings"] is True
    assert report["contracts"]["open_battery_byzantine_boundary_exposes_survivor_ranking_completion"] is True
    assert report["contracts"]["open_battery_byzantine_survivors_ranked"] is True
    assert report["contracts"]["open_battery_byzantine_clear_winner_selected_when_present"] is True
    assert report["contracts"]["open_battery_byzantine_clear_winner_derived_from_first_place_votes"] is True
    assert report["contracts"]["open_battery_byzantine_boundary_exposes_clear_winner_derivation"] is True
    assert report["contracts"]["open_battery_byzantine_tie_uses_host_seeded_random_survivor_selection"] is True
    assert report["contracts"]["open_battery_byzantine_tie_random_pool_has_two_candidates"] is True
    assert report["contracts"]["open_battery_byzantine_tie_random_choice_from_ranked_survivor_pair"] is True
    assert report["contracts"]["open_battery_byzantine_tie_random_pool_derived_from_survivor_rankings"] is True
    assert report["contracts"]["open_battery_byzantine_boundary_exposes_random_pool_derivation"] is True
    assert report["contracts"]["open_battery_byzantine_boundary_exposes_random_survivor_pair"] is True
    assert report["contracts"]["open_battery_byzantine_tie_random_path_exercised"] is True
    assert report["contracts"]["open_battery_byzantine_agreed_result_is_original_worker_result"] is True
    assert report["contracts"]["open_battery_byzantine_agreed_result_is_survivor"] is True
    assert report["contracts"]["open_battery_byzantine_malicious_result_not_selected_when_majority_rejects_it"] is True
    assert report["contracts"]["open_battery_byzantine_boundary_emits_single_result"] is True

    expected_endstates = {
        "answer_only",
        "needs_clarification",
        "proposal_created",
        "proposal_rejected_unsafe",
        "proposal_rejected_stale",
        "applied_verified",
        "applied_verification_failed",
        "retry_required",
        "retry_succeeded",
        "already_satisfied",
        "diagnostic_failure",
    }
    assert set(report["target_endstates"].values()) == expected_endstates
    assert set(report["observed_endstates"].values()) == expected_endstates

    for case_id in report["cases"]:
        case_dir = run_dir / case_id
        assert (case_dir / "run_state_manifest.json").exists()
        assert (case_dir / "run_state_rag_corpus.json").exists()
        assert (case_dir / "run_state_retrieval_trace.json").exists()
        assert (case_dir / "open_pathway_trace.json").exists()
        assert (case_dir / "open_agent_decision.json").exists()
        assert (case_dir / "byzantine_round_1_results.json").exists()
        assert (case_dir / "byzantine_round_2_reviews.json").exists()
        assert (case_dir / "byzantine_final_selection.json").exists()
        assert (case_dir / "byzantine_agreement_trace.json").exists()
        pathway_trace = json.loads((case_dir / "open_pathway_trace.json").read_text(encoding="utf-8"))
        decision = json.loads((case_dir / "open_agent_decision.json").read_text(encoding="utf-8"))
        retrieval_trace = json.loads((case_dir / "run_state_retrieval_trace.json").read_text(encoding="utf-8"))
        corpus = json.loads((case_dir / "run_state_rag_corpus.json").read_text(encoding="utf-8"))
        round_1 = json.loads((case_dir / "byzantine_round_1_results.json").read_text(encoding="utf-8"))
        round_2 = json.loads((case_dir / "byzantine_round_2_reviews.json").read_text(encoding="utf-8"))
        final_selection = json.loads((case_dir / "byzantine_final_selection.json").read_text(encoding="utf-8"))
        suspicious_docs = [
            doc for doc in corpus["documents"]
            if doc.get("kind") == "suspicious_node_output"
        ]

        assert pathway_trace["summary"]["stage_count"] >= 8
        assert pathway_trace["summary"]["missing_required_stages"] == []
        assert "prompt_ingested" in pathway_trace["summary"]["observed_stages"]
        assert "suspicious_node_evidence_injected" in pathway_trace["summary"]["observed_stages"]
        assert "trust_boundary_evaluated" in pathway_trace["summary"]["observed_stages"]
        assert "host_authority_bound" in pathway_trace["summary"]["observed_stages"]
        assert "byzantine_round_1_results_returned" in pathway_trace["summary"]["observed_stages"]
        assert "byzantine_round_2_reviews_completed" in pathway_trace["summary"]["observed_stages"]
        assert "byzantine_final_selection_recorded" in pathway_trace["summary"]["observed_stages"]
        assert "final_endstate_recorded" in pathway_trace["summary"]["observed_stages"]
        assert suspicious_docs
        assert all(doc["trusted"] is False and doc["tainted"] is True for doc in suspicious_docs)
        assert retrieval_trace["suspicious_node_doc_selected"] is True
        assert retrieval_trace["selected_untrusted_doc_ids"]
        assert retrieval_trace["host_policy_doc_selected"] is True
        assert decision["ok"] is True
        assert decision["observed_endstate"] == decision["target_endstate"]
        assert decision["authority_resolution"]["host_authority"] == "authoritative"
        assert decision["authority_resolution"]["suspicious_node_authority"] == "ignored"
        assert decision["authority_resolution"]["model_output_is_policy_source"] is False
        assert decision["contracts"]["retrieval_trace_recorded"] is True
        assert decision["contracts"]["no_live_ai_calls"] is True
        assert decision["contracts"]["strict_suspicious_node_mode"] is True
        assert decision["contracts"]["suspicious_node_context_retrieved"] is True
        assert decision["contracts"]["suspicious_node_not_authoritative"] is True
        assert decision["contracts"]["host_policy_context_retrieved"] is True
        assert decision["contracts"]["retrieved_docs_carry_trust_labels"] is True
        assert decision["contracts"]["pathway_trace_recorded"] is True
        assert decision["contracts"]["required_pathway_stages_executed"] is True
        assert decision["contracts"]["observed_endstate_matches_target"] is True
        assert decision["contracts"]["case_report_written"] is True
        assert decision["contracts"]["byzantine_round_1_three_results_returned"] is True
        assert decision["contracts"]["byzantine_round_1_payload_hashes_recorded"] is True
        assert decision["contracts"]["byzantine_round_2_all_results_sent_to_all_reviewers"] is True
        assert decision["contracts"]["byzantine_round_2_full_result_payloads_sent_to_all_reviewers"] is True
        assert decision["contracts"]["byzantine_round_2_input_payload_hashes_match_round_1"] is True
        assert decision["contracts"]["byzantine_final_round_received_all_round_2_reviews"] is True
        assert decision["contracts"]["byzantine_final_input_review_hashes_match_round_2"] is True
        assert decision["contracts"]["byzantine_boundary_payload_hashes_recorded"] is True
        assert decision["contracts"]["byzantine_agreed_result_hash_matches_round_1"] is True
        assert decision["contracts"]["byzantine_survivor_result_hashes_match_round_1"] is True
        assert decision["contracts"]["byzantine_boundary_agreed_result_hash_recorded"] is True
        assert decision["contracts"]["byzantine_round_2_each_reviewer_rejects_at_most_one"] is True
        assert decision["contracts"]["byzantine_final_rejects_at_most_one"] is True
        assert decision["contracts"]["byzantine_rejection_derivation_recorded"] is True
        assert decision["contracts"]["byzantine_rejected_result_derived_from_simple_majority"] is True
        assert decision["contracts"]["byzantine_boundary_exposes_rejection_derivation"] is True
        assert decision["contracts"]["byzantine_survivor_ranking_completion_recorded"] is True
        assert decision["contracts"]["byzantine_first_place_votes_derived_from_completed_survivor_rankings"] is True
        assert decision["contracts"]["byzantine_boundary_exposes_survivor_ranking_completion"] is True
        assert decision["contracts"]["byzantine_agreed_result_is_original_worker_result"] is True
        assert decision["contracts"]["byzantine_agreed_result_is_survivor"] is True
        assert decision["contracts"]["byzantine_boundary_emits_single_result"] is True
        assert decision["contracts"]["byzantine_boundary_exposes_random_survivor_pair"] is True
        assert decision["contracts"]["byzantine_clear_winner_derived_from_first_place_votes"] is True
        assert decision["contracts"]["byzantine_boundary_exposes_clear_winner_derivation"] is True
        assert decision["contracts"]["byzantine_tie_random_pool_derived_from_survivor_rankings"] is True
        assert decision["contracts"]["byzantine_boundary_exposes_random_pool_derivation"] is True
        assert len(round_1["results"]) == 3
        assert len(round_2["reviews"]) == 3
        assert final_selection["input_reviewers"] == [review["reviewer"] for review in round_2["reviews"]]
        assert final_selection["input_reviews"] == round_2["reviews"]
        assert all(len(review["input_result_ids"]) == 3 for review in round_2["reviews"])
        assert all(len(review["input_results"]) == 3 for review in round_2["reviews"])
        assert all(
            {result["result_id"] for result in review["input_results"]}
            == {result["result_id"] for result in round_1["results"]}
            for review in round_2["reviews"]
        )
        assert all(review["input_results"] == round_1["results"] for review in round_2["reviews"])
        expected_round_1_hashes = smoke.open_battery_payload_sha256_by_id(round_1["results"], id_key="result_id")
        expected_round_1_set_hash = smoke.open_battery_payload_set_sha256(expected_round_1_hashes)
        assert round_1["result_sha256_by_id"] == expected_round_1_hashes
        assert round_1["results_set_sha256"] == expected_round_1_set_hash
        assert round_2["input_result_sha256_by_id"] == expected_round_1_hashes
        assert round_2["input_results_set_sha256"] == expected_round_1_set_hash
        assert all(review["input_result_sha256_by_id"] == expected_round_1_hashes for review in round_2["reviews"])
        assert all(review["input_results_set_sha256"] == expected_round_1_set_hash for review in round_2["reviews"])
        assert final_selection["round_1_result_sha256_by_id"] == expected_round_1_hashes
        assert final_selection["round_1_results_set_sha256"] == expected_round_1_set_hash
        expected_round_2_hashes = smoke.open_battery_payload_sha256_by_id(round_2["reviews"], id_key="reviewer")
        expected_round_2_set_hash = smoke.open_battery_payload_set_sha256(expected_round_2_hashes)
        assert round_2["review_sha256_by_reviewer"] == expected_round_2_hashes
        assert round_2["reviews_set_sha256"] == expected_round_2_set_hash
        assert final_selection["input_review_sha256_by_reviewer"] == expected_round_2_hashes
        assert final_selection["input_reviews_set_sha256"] == expected_round_2_set_hash
        agreed_result_id = final_selection["agreed_result_id"]
        assert final_selection["agreed_result_sha256"] == expected_round_1_hashes[agreed_result_id]
        assert smoke.open_battery_payload_sha256(final_selection["agreed_result"]) == final_selection["agreed_result_sha256"]
        assert final_selection["surviving_result_sha256_by_id"] == {
            result_id: expected_round_1_hashes[result_id]
            for result_id in final_selection["surviving_results"]
        }
        assert set(final_selection["host_random_survivor_sha256_by_id"]) == set(final_selection["host_random_survivor_pool"])
        assert all(
            final_selection["host_random_survivor_sha256_by_id"][result_id] == expected_round_1_hashes[result_id]
            for result_id in final_selection["host_random_survivor_pool"]
        )
        assert decision["byzantine_agreement"]["agreed_result_sha256"] == final_selection["agreed_result_sha256"]
        assert decision["byzantine_agreement"]["surviving_result_sha256_by_id"] == final_selection["surviving_result_sha256_by_id"]
        assert decision["byzantine_agreement"]["survivor_ranking_completion_derivation"] == final_selection["survivor_ranking_completion_derivation"]
        assert decision["byzantine_agreement"]["clear_majority_derivation"] == final_selection["clear_majority_derivation"]
        assert decision["byzantine_agreement"]["host_random_survivor_pool_derivation"] == final_selection["host_random_survivor_pool_derivation"]
        assert all(len(value) == 64 for value in expected_round_1_hashes.values())
        assert all(len(value) == 64 for value in expected_round_2_hashes.values())
        assert all(review["reject_count"] <= 1 for review in round_2["reviews"])
        assert final_selection["agreed_result_id"] in final_selection["round_1_result_ids"]
        assert final_selection["agreed_result_id"] in final_selection["surviving_results"]
        rejection_derivation = final_selection["rejection_derivation"]
        assert rejection_derivation["rule"] == "simple_majority_reject_at_most_one"
        assert rejection_derivation["rejection_votes"] == final_selection["rejection_votes"]
        assert rejection_derivation["majority_threshold"] == final_selection["majority_threshold"]
        assert rejection_derivation["result_ids"] == final_selection["round_1_result_ids"]
        assert rejection_derivation["rejected_result"] == final_selection["rejected_result"]
        assert rejection_derivation["survivors"] == final_selection["surviving_results"]
        assert rejection_derivation["rejected_result_count"] == len(final_selection["rejected_results"])
        ranking_completion = final_selection["survivor_ranking_completion_derivation"]
        assert ranking_completion["rule"] == "preserve_observed_survivor_order_append_omitted_final_survivors"
        assert ranking_completion["final_survivors"] == final_selection["surviving_results"]
        assert ranking_completion["first_place_votes"] == final_selection["first_place_votes"]
        assert len(ranking_completion["records"]) == len(round_2["reviews"])
        assert all(
            set(record["completed_survivor_ranking"]) == set(final_selection["surviving_results"])
            for record in ranking_completion["records"]
        )
        assert all(
            (
                record["first_place_source"] == "observed_survivor_ranking"
                and record["first_place_result"] == record["observed_survivor_ranking"][0]
            )
            or (
                record["first_place_source"] == "no_observed_final_survivor"
                and record["first_place_result"] == ""
            )
            for record in ranking_completion["records"]
        )
        assert final_selection["consensus"] is True
        assert decision["byzantine_agreement"]["agreed_result_id"] == final_selection["agreed_result_id"]
        assert decision["byzantine_agreement"]["rejection_derivation"] == rejection_derivation
        assert decision["byzantine_agreement"]["survivor_ranking_completion_derivation"] == ranking_completion

    clear_selection = json.loads((run_dir / "proposal_created" / "byzantine_final_selection.json").read_text(encoding="utf-8"))
    assert clear_selection["selection_method"] == "clear_majority"
    assert clear_selection["rejected_result"] == "r3"
    clear_rejection = clear_selection["rejection_derivation"]
    assert clear_rejection["rejected_result"] == "r3"
    assert clear_rejection["rejected_vote_count"] == 2
    assert clear_rejection["majority_rejection_candidates"] == ["r3"]
    assert clear_rejection["survivors"] == clear_selection["surviving_results"]
    assert clear_rejection["outcome"] == "single_simple_majority_rejection"
    assert clear_selection["agreed_result_id"] == "r2"
    assert clear_selection["agreed_result"]["malicious"] is False
    clear_ranking_completion = clear_selection["survivor_ranking_completion_derivation"]
    assert clear_ranking_completion["completion_count"] == 1
    malicious_completion = [
        record
        for record in clear_ranking_completion["records"]
        if record["reviewer"] == "reviewer_3_malicious"
    ][0]
    assert malicious_completion["omitted_final_survivors"] == ["r1"]
    assert malicious_completion["completed_survivor_ranking"] == ["r2", "r1"]
    assert malicious_completion["completion_applied"] is True
    clear_derivation = clear_selection["clear_majority_derivation"]
    assert clear_derivation["winning_result"] == clear_selection["agreed_result_id"]
    assert clear_derivation["winning_vote_count"] == clear_selection["first_place_votes"][clear_selection["agreed_result_id"]]
    assert clear_derivation["winning_vote_count"] >= clear_selection["majority_threshold"]
    assert clear_derivation["first_place_votes"] == clear_selection["first_place_votes"]
    assert clear_derivation["clear_winners"] == clear_selection["clear_winners"]
    assert clear_derivation["survivor_rankings"] == clear_selection["survivor_rankings"]

    clear_decision = json.loads((run_dir / "proposal_created" / "open_agent_decision.json").read_text(encoding="utf-8"))
    assert clear_decision["byzantine_agreement"]["rejection_derivation"] == clear_rejection
    assert clear_decision["byzantine_agreement"]["survivor_ranking_completion_derivation"] == clear_ranking_completion
    assert clear_decision["byzantine_agreement"]["clear_majority_derivation"] == clear_derivation
    assert clear_decision["byzantine_agreement"]["clear_majority_winning_result"] == "r2"
    assert clear_decision["byzantine_agreement"]["clear_majority_winning_vote_count"] == clear_derivation["winning_vote_count"]
    assert report["case_reports"]["proposal_created"]["byzantine_agreement"]["rejection_derivation"] == clear_rejection
    assert report["case_reports"]["proposal_created"]["byzantine_agreement"]["survivor_ranking_completion_derivation"] == clear_ranking_completion
    assert report["case_reports"]["proposal_created"]["byzantine_agreement"]["clear_majority_derivation"] == clear_derivation

    tie_selection = json.loads((run_dir / "answer_only" / "byzantine_final_selection.json").read_text(encoding="utf-8"))
    assert tie_selection["selection_method"] == "host_seeded_random_among_ranked_survivor_pair"
    assert tie_selection["rejected_result"] == ""
    tie_rejection = tie_selection["rejection_derivation"]
    assert tie_rejection["rejected_result"] == ""
    assert tie_rejection["rejected_result_count"] == 0
    assert tie_rejection["majority_rejection_candidates"] == []
    assert tie_rejection["survivors"] == tie_selection["surviving_results"]
    assert tie_rejection["outcome"] == "no_simple_majority_rejection"
    assert len(tie_selection["surviving_results"]) == 3
    assert len(tie_selection["host_random_survivor_pool"]) == 2
    assert set(tie_selection["host_random_survivor_pool"]).issubset(set(tie_selection["surviving_results"]))
    assert tie_selection["agreed_result_id"] in tie_selection["host_random_survivor_pool"]
    assert tie_selection["host_random_pool_size"] == 2
    tie_ranking_completion = tie_selection["survivor_ranking_completion_derivation"]
    assert tie_ranking_completion["completion_count"] == 0
    assert tie_ranking_completion["final_survivors"] == tie_selection["surviving_results"]
    assert tie_ranking_completion["first_place_votes"] == tie_selection["first_place_votes"]
    derivation = tie_selection["host_random_survivor_pool_derivation"]
    assert derivation["selected_pool"] == tie_selection["host_random_survivor_pool"]
    assert derivation["ranked_survivors"][:2] == tie_selection["host_random_survivor_pool"]
    assert set(derivation["score_by_result"]) == set(tie_selection["surviving_results"])
    assert tie_selection["host_random_ranked_survivors"] == derivation["ranked_survivors"]
    assert tie_selection["host_random_score_by_result"] == derivation["score_by_result"]
    assert all(
        set(score) == {"first_place_votes", "rank_position_total", "rank_order"}
        for score in derivation["score_by_result"].values()
    )
    assert tie_selection["host_random_seed"]
    assert tie_selection["host_random_seed_sha256"] == tie_selection["host_random_seed"]

    tie_decision = json.loads((run_dir / "answer_only" / "open_agent_decision.json").read_text(encoding="utf-8"))
    assert tie_decision["byzantine_agreement"]["rejection_derivation"] == tie_rejection
    assert tie_decision["byzantine_agreement"]["survivor_ranking_completion_derivation"] == tie_ranking_completion
    assert tie_decision["byzantine_agreement"]["host_random_survivor_pool"] == tie_selection["host_random_survivor_pool"]
    assert tie_decision["byzantine_agreement"]["host_random_survivor_sha256_by_id"] == tie_selection["host_random_survivor_sha256_by_id"]
    assert tie_decision["byzantine_agreement"]["host_random_survivor_pool_derivation"] == tie_selection["host_random_survivor_pool_derivation"]
    assert tie_decision["byzantine_agreement"]["host_random_pool_size"] == 2
    assert tie_decision["byzantine_agreement"]["agreed_result_id"] in tie_decision["byzantine_agreement"]["host_random_survivor_pool"]

    tie_case_report = report["case_reports"]["answer_only"]
    assert tie_case_report["byzantine_agreement"]["rejection_derivation"] == tie_rejection
    assert tie_case_report["byzantine_agreement"]["survivor_ranking_completion_derivation"] == tie_ranking_completion
    assert tie_case_report["byzantine_agreement"]["host_random_survivor_pool"] == tie_selection["host_random_survivor_pool"]
    assert tie_case_report["byzantine_agreement"]["host_random_survivor_pool_derivation"] == tie_selection["host_random_survivor_pool_derivation"]
    assert tie_case_report["byzantine_agreement"]["host_random_pool_size"] == 2

    retry_decision = json.loads((run_dir / "retry_succeeded" / "open_agent_decision.json").read_text(encoding="utf-8"))
    assert retry_decision["observed_endstate"] == "retry_succeeded"
    assert retry_decision["contracts"]["host_rejection_recorded"] is True
    assert retry_decision["contracts"]["verification_passed_after_retry"] is True

    failed_decision = json.loads((run_dir / "applied_verification_failed" / "open_agent_decision.json").read_text(encoding="utf-8"))
    assert failed_decision["observed_endstate"] == "applied_verification_failed"
    assert failed_decision["contracts"]["verification_failed"] is True
    assert failed_decision["contracts"]["commit_blocked"] is True


def test_open_battery_case_filter_runs_single_endstate(tmp_path: Path) -> None:
    run_dir = tmp_path / "single-open-battery-case"

    assert smoke.main([
        "--open-battery",
        "--open-battery-case",
        "proposal_rejected_stale",
        "--run-dir",
        str(run_dir),
    ]) == 0

    report = json.loads((run_dir / "open_battery_report.json").read_text(encoding="utf-8"))

    assert report["ok"] is True
    assert report["cases"] == ["proposal_rejected_stale"]
    assert report["target_endstates"] == {"proposal_rejected_stale": "proposal_rejected_stale"}
    assert report["observed_endstates"] == {"proposal_rejected_stale": "proposal_rejected_stale"}
    decision = json.loads((run_dir / "proposal_rejected_stale" / "open_agent_decision.json").read_text(encoding="utf-8"))
    pathway_trace = json.loads((run_dir / "proposal_rejected_stale" / "open_pathway_trace.json").read_text(encoding="utf-8"))
    retrieval_trace = json.loads((run_dir / "proposal_rejected_stale" / "run_state_retrieval_trace.json").read_text(encoding="utf-8"))
    final_selection = json.loads((run_dir / "proposal_rejected_stale" / "byzantine_final_selection.json").read_text(encoding="utf-8"))
    assert decision["contracts"]["stale_proposal_rejected"] is True
    assert decision["contracts"]["boundary_mismatch_detected"] is True
    assert decision["contracts"]["required_pathway_stages_executed"] is True
    assert decision["contracts"]["suspicious_node_context_retrieved"] is True
    assert decision["contracts"]["suspicious_node_not_authoritative"] is True
    assert decision["contracts"]["host_policy_context_retrieved"] is True
    assert retrieval_trace["selected_untrusted_doc_ids"]
    assert retrieval_trace["host_policy_doc_selected"] is True
    assert final_selection["selection_method"] == "clear_majority"
    assert final_selection["rejected_result"] == "r3"
    assert final_selection["rejection_derivation"]["rejected_result"] == "r3"
    assert final_selection["rejection_derivation"]["survivors"] == final_selection["surviving_results"]
    assert final_selection["agreed_result"]["target_endstate"] == "proposal_rejected_stale"
    assert "trust_boundary_evaluated" in pathway_trace["summary"]["observed_stages"]
    assert "byzantine_final_selection_recorded" in pathway_trace["summary"]["observed_stages"]
    assert "boundary_freshness_checked" in pathway_trace["summary"]["observed_stages"]
    assert "host_rejection_recorded" in pathway_trace["summary"]["observed_stages"]


def test_open_battery_retrieves_but_never_trusts_suspicious_node_context(tmp_path: Path) -> None:
    run_dir = tmp_path / "strict-open-battery"

    assert smoke.main([
        "--open-battery",
        "--open-battery-case",
        "proposal_created",
        "--run-dir",
        str(run_dir),
    ]) == 0

    decision = json.loads((run_dir / "proposal_created" / "open_agent_decision.json").read_text(encoding="utf-8"))
    proposal = json.loads((run_dir / "proposal_created" / "proposal.json").read_text(encoding="utf-8"))
    review = json.loads((run_dir / "proposal_created" / "host_policy_review.json").read_text(encoding="utf-8"))
    final_selection = json.loads((run_dir / "proposal_created" / "byzantine_final_selection.json").read_text(encoding="utf-8"))

    assert decision["contracts"]["suspicious_node_context_retrieved"] is True
    assert decision["contracts"]["suspicious_node_not_authoritative"] is True
    assert decision["contracts"]["model_output_not_policy_source"] is True
    assert decision["authority_resolution"]["selected_suspicious_doc_ids"]
    assert final_selection["rejected_result"] == "r3"
    assert final_selection["agreed_result"]["malicious"] is False
    assert final_selection["agreed_result"]["suspicious_context_authority"] == "ignored"
    assert proposal["host_apply_authority"] == "pending"
    assert proposal["applied"] is False
    assert review["accepted"] is True
    assert review["host_policy_enforced"] is True
