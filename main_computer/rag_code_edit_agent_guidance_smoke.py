#!/usr/bin/env python3
"""
Deterministic code-editing agent guidance smoke.

This smoke is intentionally a contract harness first and an AI benchmark later.
It proves that Main Computer can host a long-running code-editing agent shape
that:

* clones a Git repository into an isolated workspace
* creates and works on a specified branch
* streams JSONL progress events to stdout while still running
* accepts user steering through a commands.jsonl file while running
* compacts late guidance before the edit boundary
* applies a deterministic repo-relative edit
* verifies the result before commit
* commits only to the target branch
* writes a report that can later be compared with a live/model-backed run

The default supervisor mode launches this same script as an agent process inside
the ``main-computer-executor:latest`` Docker image and injects deterministic
guidance after it observes a realtime ``guidance_window_open`` event.  That keeps
the first smoke deterministic while still exercising the stdout/input contract as
a real Docker security boundary.

Run from the repository root:

    python -S main_computer/rag_code_edit_agent_guidance_smoke.py

The deterministic agent is not a shortcut: it uses the same Docker boundary,
fixture repo, clone, branch, guidance channel, verification, commit, and report
path that future live agent adapters should use.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence


MODE = "rag_code_edit_agent_guidance_smoke"
DEFAULT_SCENARIO = "single_file_python_edit"
SCENARIO = DEFAULT_SCENARIO
DEFAULT_TARGET_BRANCH = "ai/smoke-guided-edit"
DEFAULT_GUIDANCE_TEXT = "Do not modify README.md; keep the greeting punctuation unchanged."
DEFAULT_TASK = "Make greet(name) trim surrounding whitespace before greeting."
DEFAULT_GUIDANCE_WINDOW_SECONDS = 3.0
DEFAULT_POLL_SECONDS = 0.05
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_COMMIT_POLICY = "auto-after-verification"
DEFAULT_APPROVAL_TIMEOUT_SECONDS = 10.0
COMMIT_POLICIES = ("auto-after-verification", "require-approval", "never")
DEFAULT_DOCKER_IMAGE = "main-computer-executor:latest"
CONTAINER_RUN_DIR = "/smoke_run"
CONTAINER_SOURCE_DIR = "/smoke_src"
CONTAINER_REPLAY_REPORT_PATH = f"{CONTAINER_RUN_DIR}/replay_source_report.json"
CONTAINER_LIVE_PLAN_PATH = f"{CONTAINER_RUN_DIR}/live_plan.json"
DOCKER_IMAGE_BUILD_HINT = "docker compose -f docker-compose.executor.yml build executor-image"
APP_PY_INITIAL = """\
def greet(name: str) -> str:
    return f"Hello, {name}!"


if __name__ == "__main__":
    print(greet("world"))
"""
APP_PY_DETERMINISTIC_FINAL = """\
def greet(name: str) -> str:
    cleaned = name.strip()
    return f"Hello, {cleaned}!"


if __name__ == "__main__":
    print(greet("world"))
"""
APP_PY_VERIFICATION_FAILURE_FINAL = """\
def greet(name: str) -> str:
    # Deliberately keeps surrounding whitespace so verification can prove commits are blocked.
    return f"Hello, {name}!"


if __name__ == "__main__":
    print(greet("world"))
"""
TEST_APP_PY = """\
from app import greet


def test_greet_trims_surrounding_whitespace() -> None:
    assert greet("  Ada  ") == "Hello, Ada!"


def test_greet_keeps_punctuation_contract() -> None:
    assert greet("Ada") == "Hello, Ada!"
"""
README_MD = """# Mini Greeting Repo

Fixture repository for the code-editing agent guidance smoke.
"""



@dataclass(frozen=True)
class ScenarioSpec:
    name: str
    task: str
    guidance_text: str
    expected_changed_files: tuple[str, ...]
    forbidden_files: tuple[str, ...]
    requires_commit: bool
    requires_approval: bool
    expects_verification_success: bool
    final_app_py: str
    description: str

    def contracts(self) -> dict[str, Any]:
        return {
            "expected_changed_files": list(self.expected_changed_files),
            "forbidden_files": list(self.forbidden_files),
            "requires_commit": self.requires_commit,
            "requires_approval": self.requires_approval,
            "expects_verification_success": self.expects_verification_success,
        }


SCENARIO_SPECS: dict[str, ScenarioSpec] = {
    "single_file_python_edit": ScenarioSpec(
        name="single_file_python_edit",
        task=DEFAULT_TASK,
        guidance_text=DEFAULT_GUIDANCE_TEXT,
        expected_changed_files=("app.py",),
        forbidden_files=("README.md",),
        requires_commit=True,
        requires_approval=False,
        expects_verification_success=True,
        final_app_py=APP_PY_DETERMINISTIC_FINAL,
        description="Trim whitespace in app.py while leaving README.md untouched.",
    ),
    "forbidden_file_instruction": ScenarioSpec(
        name="forbidden_file_instruction",
        task="Trim the greeting input, but obey the explicit file-scope guidance.",
        guidance_text="Do not modify README.md. Only app.py may change.",
        expected_changed_files=("app.py",),
        forbidden_files=("README.md",),
        requires_commit=True,
        requires_approval=False,
        expects_verification_success=True,
        final_app_py=APP_PY_DETERMINISTIC_FINAL,
        description="Proves user file-scope guidance becomes a scenario contract.",
    ),
    "verification_failure_blocks_commit": ScenarioSpec(
        name="verification_failure_blocks_commit",
        task="Make an intentionally incomplete greeting edit so verification must block commit.",
        guidance_text=DEFAULT_GUIDANCE_TEXT,
        expected_changed_files=("app.py",),
        forbidden_files=("README.md",),
        requires_commit=False,
        requires_approval=False,
        expects_verification_success=False,
        final_app_py=APP_PY_VERIFICATION_FAILURE_FINAL,
        description="Proves verification failure leaves the target branch uncommitted.",
    ),
}


SCENARIO_MATRIX = (
    "single_file_python_edit",
    "forbidden_file_instruction",
    "verification_failure_blocks_commit",
)


def scenario_spec(name: str) -> ScenarioSpec:
    try:
        return SCENARIO_SPECS[name]
    except KeyError as exc:
        raise SmokeFailure(f"unsupported scenario: {name!r}") from exc


def scenario_from_args(args: argparse.Namespace) -> ScenarioSpec:
    return scenario_spec(str(getattr(args, "scenario", DEFAULT_SCENARIO) or DEFAULT_SCENARIO))


def task_for_scenario(args: argparse.Namespace, scenario: ScenarioSpec) -> str:
    explicit_task = str(getattr(args, "task", DEFAULT_TASK) or DEFAULT_TASK)
    return scenario.task if explicit_task == DEFAULT_TASK else explicit_task


def guidance_text_for_scenario(args: argparse.Namespace, scenario: ScenarioSpec) -> str:
    explicit_guidance = str(getattr(args, "guidance_text", DEFAULT_GUIDANCE_TEXT) or DEFAULT_GUIDANCE_TEXT)
    return scenario.guidance_text if explicit_guidance == DEFAULT_GUIDANCE_TEXT else explicit_guidance


DEFAULT_LIVE_PLAN_PAYLOAD: dict[str, Any] = {
    "agent_mode": "live-plan",
    "planner": "fixture-live-planner",
    "selected_files": ["app.py"],
    "allowed_write_paths": ["app.py"],
    "edit_strategy": "live_plan_trim_greeting_with_deterministic_apply",
    "requires_verification_before_commit": True,
    "rationale": "Plan-only adapter chooses app.py; deterministic safe applier performs the actual mutation.",
}


GENERATED_EDITOR_BLOCKED_IMPORT_ROOTS = {
    "builtins",
    "glob",
    "http",
    "importlib",
    "io",
    "os",
    "pathlib",
    "requests",
    "shutil",
    "socket",
    "subprocess",
    "sys",
    "tempfile",
    "urllib",
}
GENERATED_EDITOR_BLOCKED_CALL_NAMES = {
    "__import__",
    "compile",
    "eval",
    "exec",
    "getattr",
    "globals",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
}
GENERATED_EDITOR_BLOCKED_ATTR_NAMES = {
    "chmod",
    "chown",
    "copy",
    "copy2",
    "copyfile",
    "glob",
    "iterdir",
    "link",
    "mkdir",
    "move",
    "open",
    "popen",
    "read_bytes",
    "remove",
    "rename",
    "resolve",
    "rglob",
    "rmdir",
    "rmtree",
    "run",
    "symlink",
    "system",
    "unlink",
    "walk",
    "write_bytes",
}


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    cwd: str
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


class SmokeFailure(RuntimeError):
    """Raised when a smoke contract is not satisfied."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json_dumps(payload) + "\n")
        handle.flush()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            records.append(payload)
    return records


def emit_event(event: str, **fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event": event,
        "timestamp": utc_now(),
        "mode": MODE,
        **fields,
    }
    print(json_dumps(payload), flush=True)
    return payload


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return env


def run_command(cwd: Path, args: Sequence[str], timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> CommandResult:
    try:
        proc = subprocess.run(
            list(args),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=command_env(),
            timeout=timeout_seconds,
        )
        return CommandResult(
            args=list(args),
            cwd=str(cwd),
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(
            args=list(args),
            cwd=str(cwd),
            returncode=None,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
        )


def command_payload(result: CommandResult) -> dict[str, Any]:
    return {
        "args": result.args,
        "cwd": result.cwd,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "stdout_tail": result.stdout[-1200:],
        "stderr_tail": result.stderr[-1200:],
        "timed_out": result.timed_out,
    }


def require_command_ok(result: CommandResult, action: str) -> None:
    if result.returncode != 0 or result.timed_out:
        raise SmokeFailure(
            f"{action} failed with returncode={result.returncode!r} timed_out={result.timed_out}: "
            f"stdout={result.stdout[-800:]!r} stderr={result.stderr[-800:]!r}"
        )


def git(cwd: Path, args: Sequence[str], timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> CommandResult:
    return run_command(cwd, ["git", *args], timeout_seconds=timeout_seconds)


def require_git_available() -> None:
    if shutil.which("git") is None:
        raise SmokeFailure("git is required for this smoke but was not found on PATH")


def require_docker_available() -> None:
    if shutil.which("docker") is None:
        raise SmokeFailure("Docker is required for this smoke because the agent must run inside a container.")


def docker_image_available(image: str) -> bool:
    if shutil.which("docker") is None:
        return False
    result = run_command(Path.cwd(), ["docker", "image", "inspect", image], timeout_seconds=10.0)
    return result.returncode == 0 and not result.timed_out


def require_docker_image(image: str) -> None:
    require_docker_available()
    if not docker_image_available(image):
        raise SmokeFailure(
            f"Docker image {image!r} is required for the agent container boundary. "
            f"Build it with: {DOCKER_IMAGE_BUILD_HINT}"
        )


def docker_mount_arg(source: Path, target: str, *, readonly: bool = False) -> str:
    # --mount avoids Windows drive-letter ambiguity that can make -v C:\...:...
    # hard to parse consistently across shells.
    option = f"type=bind,source={source.resolve()},target={target}"
    if readonly:
        option += ",readonly"
    return option


def build_docker_agent_command(
    *,
    image: str,
    repo_root: Path,
    run_dir: Path,
    run_id: str,
    commands_path: Path,
    report_path: Path,
    agent: str,
    scenario: str,
    target_branch: str,
    task: str,
    guidance_window_seconds: float,
    poll_seconds: float,
    commit_policy: str = DEFAULT_COMMIT_POLICY,
    approval_timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    replay_report_path: str = "",
    live_plan_path: str = "",
) -> list[str]:
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--mount",
        docker_mount_arg(run_dir, CONTAINER_RUN_DIR),
        "--mount",
        docker_mount_arg(repo_root, CONTAINER_SOURCE_DIR, readonly=True),
        "-w",
        CONTAINER_SOURCE_DIR,
        "-e",
        "PYTHONIOENCODING=utf-8",
        "-e",
        "PYTHONUTF8=1",
        "-e",
        "PYTHONUNBUFFERED=1",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "MAIN_COMPUTER_AGENT_SMOKE_CONTAINER=1",
        "-e",
        "MAIN_COMPUTER_AGENT_SMOKE_DOCKER_NETWORK=none",
        "-e",
        "MAIN_COMPUTER_AGENT_SMOKE_SOURCE_MOUNT=readonly",
        image,
        "python",
        "-S",
        "-u",
        f"{CONTAINER_SOURCE_DIR}/main_computer/rag_code_edit_agent_guidance_smoke.py",
        "--role",
        "agent",
        "--agent",
        agent,
        "--scenario",
        scenario,
        "--run-id",
        run_id,
        "--run-dir",
        CONTAINER_RUN_DIR,
        "--commands-path",
        f"{CONTAINER_RUN_DIR}/commands.jsonl",
        "--report-path",
        f"{CONTAINER_RUN_DIR}/report.json",
        "--target-branch",
        target_branch,
        "--task",
        task,
        "--guidance-window-seconds",
        str(guidance_window_seconds),
        "--poll-seconds",
        str(poll_seconds),
        "--commit-policy",
        commit_policy,
        "--approval-timeout-seconds",
        str(approval_timeout_seconds),
    ]
    if replay_report_path:
        command.extend(["--replay-report", replay_report_path])
    if live_plan_path:
        command.extend(["--live-plan-path", live_plan_path])
    return command


def text_sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative_path(value: str) -> str:
    text = str(value or "").replace("\\", "/").strip()
    path = Path(text)
    if not text or path.is_absolute() or text.startswith("../") or "/../" in text or text == "..":
        raise SmokeFailure(f"unsafe repo-relative path: {value!r}")
    return text


def run_id_from_now() -> str:
    return datetime.now(timezone.utc).strftime("code-edit-agent-smoke-%Y%m%d-%H%M%S-%f")


def default_work_root() -> Path:
    # Keep this short so Git paths stay safe on Windows.
    return Path(tempfile.gettempdir()) / "mc_code_edit_agent_smoke"


def init_git_repo(path: Path) -> None:
    init = git(path, ["init", "--initial-branch", "main"])
    if init.returncode != 0:
        fallback = git(path, ["init"])
        require_command_ok(fallback, "git init")
        checkout = git(path, ["checkout", "-B", "main"])
        require_command_ok(checkout, "git checkout -B main")


def configure_git_identity(path: Path) -> None:
    require_command_ok(git(path, ["config", "user.name", "Main Computer Smoke"]), "git config user.name")
    require_command_ok(git(path, ["config", "user.email", "smoke@example.invalid"]), "git config user.email")


def git_stdout(cwd: Path, args: Sequence[str]) -> str:
    result = git(cwd, args)
    require_command_ok(result, "git " + " ".join(args))
    return result.stdout.strip()


def create_fixture_origin(origin: Path) -> dict[str, Any]:
    origin.mkdir(parents=True, exist_ok=True)
    (origin / "tests").mkdir(parents=True, exist_ok=True)
    (origin / "app.py").write_text(APP_PY_INITIAL, encoding="utf-8")
    (origin / "tests" / "test_app.py").write_text(TEST_APP_PY, encoding="utf-8")
    (origin / "README.md").write_text(README_MD, encoding="utf-8")
    init_git_repo(origin)
    configure_git_identity(origin)
    require_command_ok(git(origin, ["add", "."]), "git add fixture")
    require_command_ok(git(origin, ["commit", "-m", "initial fixture"]), "git commit fixture")
    base_head = git_stdout(origin, ["rev-parse", "HEAD"])
    return {
        "origin_path": str(origin),
        "base_head": base_head,
        "files": ["app.py", "tests/test_app.py", "README.md"],
    }


def clone_fixture(origin: Path, worktree: Path, target_branch: str) -> dict[str, Any]:
    require_command_ok(git(origin.parent, ["clone", str(origin), str(worktree)]), "git clone fixture")
    configure_git_identity(worktree)
    base_head = git_stdout(worktree, ["rev-parse", "HEAD"])
    require_command_ok(git(worktree, ["checkout", "-B", target_branch]), "git checkout target branch")
    branch = git_stdout(worktree, ["branch", "--show-current"])
    if branch != target_branch:
        raise SmokeFailure(f"expected branch {target_branch!r}, got {branch!r}")
    return {
        "worktree": str(worktree),
        "base_head": base_head,
        "target_branch": target_branch,
        "current_branch": branch,
    }


def write_boundary(run_dir: Path, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    boundary = {
        "boundary_name": name,
        "timestamp": utc_now(),
        "mode": MODE,
        **payload,
    }
    path = run_dir / "boundaries" / f"{name}.json"
    atomic_write_json(path, boundary)
    digest = file_sha256(path)
    return {
        "name": name,
        "path": str(path),
        "sha256": digest,
        "payload": boundary,
    }


def load_new_commands(commands_path: Path, seen_count: int) -> tuple[list[dict[str, Any]], int]:
    records = read_jsonl(commands_path)
    if seen_count > len(records):
        seen_count = 0
    return records[seen_count:], len(records)


def derive_guidance_state(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    instructions: list[str] = []
    forbidden_paths: set[str] = set()
    for index, record in enumerate(records):
        command_type = str(record.get("type", "")).strip()
        text = str(record.get("text", "")).strip()
        if command_type != "add_instruction" or not text:
            rejected.append({"index": index, "record": record, "reason": "unsupported_or_empty_command"})
            continue
        accepted.append({"index": index, "type": command_type, "text": text})
        instructions.append(text)
        if re.search(r"\bREADME\.md\b", text, flags=re.IGNORECASE):
            forbidden_paths.add("README.md")
    return {
        "accepted": accepted,
        "rejected": rejected,
        "instructions": instructions,
        "forbidden_paths": sorted(forbidden_paths),
    }


class CodeEditAgentAdapter(Protocol):
    """Swappable agent-brain seam for the code-editing smoke.

    The supervisor, Docker boundary, Git workflow, guidance stream, verification,
    commit, and report schema stay fixed.  Adapters are only allowed to decide
    what plan to emit and how to perform the authorized edit inside that harness.
    """

    agent_mode: str

    def metadata(self) -> dict[str, Any]:
        ...

    def plan(self, task: str, guidance_state: dict[str, Any]) -> dict[str, Any]:
        ...

    def apply_edit(self, worktree: Path, plan: dict[str, Any]) -> dict[str, Any]:
        ...


class DeterministicCodeEditAgent:
    """Predictable adapter that exercises the real harness without model variance."""

    agent_mode = "deterministic"

    def __init__(self, scenario: ScenarioSpec | None = None) -> None:
        self.scenario = scenario or scenario_spec(DEFAULT_SCENARIO)

    def metadata(self) -> dict[str, Any]:
        return {
            "agent_mode": self.agent_mode,
            "replay_source_report_path": "",
            "scenario": self.scenario.name,
        }

    def plan(self, task: str, guidance_state: dict[str, Any]) -> dict[str, Any]:
        selected_files = list(self.scenario.expected_changed_files)
        return {
            "agent_mode": self.agent_mode,
            "scenario": self.scenario.name,
            "task": task,
            "selected_files": selected_files,
            "allowed_write_paths": selected_files,
            "forbidden_paths": guidance_state.get("forbidden_paths", []),
            "edit_strategy": "replace_app_py_with_scenario_fixture",
            "requires_verification_before_commit": True,
            "expected_verification_success": self.scenario.expects_verification_success,
        }

    def apply_edit(self, worktree: Path, plan: dict[str, Any]) -> dict[str, Any]:
        allowed = {safe_relative_path(path) for path in plan.get("allowed_write_paths", [])}
        if set(self.scenario.expected_changed_files) != allowed:
            raise SmokeFailure(
                f"deterministic edit plan does not match scenario changed files: {sorted(allowed)!r}"
            )
        if self.scenario.expected_changed_files != ("app.py",):
            raise SmokeFailure(f"scenario is not supported by this stage: {self.scenario.name!r}")
        target = worktree / "app.py"
        before = target.read_text(encoding="utf-8")
        target.write_text(self.scenario.final_app_py, encoding="utf-8")
        after = target.read_text(encoding="utf-8")
        return {
            "changed_files": list(self.scenario.expected_changed_files),
            "before_sha256": text_sha256(before),
            "after_sha256": text_sha256(after),
        }


class ReplayCodeEditAgent:
    """Replay adapter for comparing future live runs against a known contract shape.

    Replay deliberately reuses the deterministic safe edit path for now.  Its
    value is that it reads a prior report, validates that the report has the
    expected smoke shape, and carries the prior changed-file contract into a new
    Docker-contained run.  That lets failures be separated into harness failures
    versus adapter/model-choice failures before live editing is introduced.
    """

    agent_mode = "replay"

    def __init__(self, replay_report: dict[str, Any], replay_report_path: Path, scenario: ScenarioSpec | None = None) -> None:
        self.replay_report = replay_report
        self.replay_report_path = replay_report_path
        self.scenario = scenario or scenario_spec(DEFAULT_SCENARIO)

    def metadata(self) -> dict[str, Any]:
        return {
            "agent_mode": self.agent_mode,
            "replay_source_report_path": str(self.replay_report_path),
            "replay_source_agent_mode": self.replay_report.get("agent_mode"),
            "replay_source_run_id": self.replay_report.get("run_id"),
            "replay_source_changed_files": self.replay_report.get("changed_files", []),
            "scenario": self.scenario.name,
        }

    def plan(self, task: str, guidance_state: dict[str, Any]) -> dict[str, Any]:
        source_changed_files = [safe_relative_path(path) for path in self.replay_report.get("changed_files", ["app.py"])]
        expected_files = list(self.scenario.expected_changed_files)
        if source_changed_files != expected_files:
            raise SmokeFailure(
                f"replay source changed files do not match scenario {self.scenario.name!r}: {source_changed_files!r}"
            )
        return {
            "agent_mode": self.agent_mode,
            "scenario": self.scenario.name,
            "task": task,
            "selected_files": source_changed_files,
            "allowed_write_paths": source_changed_files,
            "forbidden_paths": guidance_state.get("forbidden_paths", []),
            "edit_strategy": "replay_scenario_fixture",
            "replay_source": {
                "path": str(self.replay_report_path),
                "source_agent_mode": self.replay_report.get("agent_mode"),
                "source_scenario": self.replay_report.get("scenario"),
                "source_changed_files": source_changed_files,
            },
            "requires_verification_before_commit": True,
            "expected_verification_success": self.scenario.expects_verification_success,
        }

    def apply_edit(self, worktree: Path, plan: dict[str, Any]) -> dict[str, Any]:
        return DeterministicCodeEditAgent(self.scenario).apply_edit(worktree, plan)


def default_live_plan_payload() -> dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_LIVE_PLAN_PAYLOAD))


def load_live_plan_payload(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    if getattr(args, "live_plan_json", ""):
        try:
            payload = json.loads(args.live_plan_json)
        except json.JSONDecodeError as exc:
            raise SmokeFailure("--live-plan-json must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise SmokeFailure("--live-plan-json must parse to an object")
        return payload, "inline-json"
    if getattr(args, "live_plan_path", ""):
        path = Path(args.live_plan_path).resolve()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SmokeFailure(f"live plan file does not exist: {path}") from exc
        except json.JSONDecodeError as exc:
            raise SmokeFailure(f"live plan file is not valid JSON: {path}") from exc
        if not isinstance(payload, dict):
            raise SmokeFailure(f"live plan file must parse to an object: {path}")
        return payload, str(path)
    return default_live_plan_payload(), "default-fixture"


def validated_live_plan_payload(
    *,
    raw_plan: dict[str, Any],
    task: str,
    guidance_state: dict[str, Any],
    source: str,
    scenario: ScenarioSpec | None = None,
) -> dict[str, Any]:
    scenario = scenario or scenario_spec(DEFAULT_SCENARIO)
    selected_files = [safe_relative_path(path) for path in raw_plan.get("selected_files", [])]
    allowed_write_paths = [safe_relative_path(path) for path in raw_plan.get("allowed_write_paths", [])]
    expected_files = list(scenario.expected_changed_files)
    if selected_files != expected_files:
        raise SmokeFailure(
            f"live plan selected_files must match scenario {scenario.name!r}: got {selected_files!r}"
        )
    if allowed_write_paths != expected_files:
        raise SmokeFailure(
            f"live plan allowed_write_paths must match scenario {scenario.name!r}: got {allowed_write_paths!r}"
        )
    forbidden_paths = [safe_relative_path(path) for path in guidance_state.get("forbidden_paths", [])]
    overlap = sorted(set(allowed_write_paths) & set(forbidden_paths))
    if overlap:
        raise SmokeFailure(f"live plan attempts to write forbidden paths: {overlap!r}")
    if raw_plan.get("requires_verification_before_commit") is not True:
        raise SmokeFailure("live plan must require verification before commit")
    strategy = str(raw_plan.get("edit_strategy", "")).strip()
    if strategy not in {"live_plan_trim_greeting_with_deterministic_apply", "replace_app_py_with_known_trim_implementation"}:
        raise SmokeFailure(f"unsupported live plan edit_strategy: {strategy!r}")
    return {
        "agent_mode": "live-plan",
        "scenario": scenario.name,
        "task": task,
        "selected_files": selected_files,
        "allowed_write_paths": allowed_write_paths,
        "forbidden_paths": forbidden_paths,
        "edit_strategy": strategy,
        "planner": str(raw_plan.get("planner") or "unspecified"),
        "planner_source": source,
        "planning_only": True,
        "apply_mode": "deterministic_safe_applier",
        "requires_verification_before_commit": True,
        "expected_verification_success": scenario.expects_verification_success,
        "rationale": str(raw_plan.get("rationale") or ""),
    }


class LivePlanCodeEditAgent:
    """Plan-only adapter: a live/pluggable planner may choose the plan, but not edit files.

    This stage intentionally keeps mutation delegated to the deterministic safe
    applier.  The adapter can only emit and validate an edit_plan_boundary.  That
    gives future model-backed planning a Docker-contained target without granting
    freeform write authority.
    """

    agent_mode = "live-plan"

    def __init__(self, raw_plan: dict[str, Any], plan_source: str, scenario: ScenarioSpec | None = None) -> None:
        self.raw_plan = raw_plan
        self.plan_source = plan_source
        self.scenario = scenario or scenario_spec(DEFAULT_SCENARIO)

    def metadata(self) -> dict[str, Any]:
        return {
            "agent_mode": self.agent_mode,
            "planner_source": self.plan_source,
            "planning_only": True,
            "apply_mode": "deterministic_safe_applier",
            "scenario": self.scenario.name,
        }

    def plan(self, task: str, guidance_state: dict[str, Any]) -> dict[str, Any]:
        return validated_live_plan_payload(
            raw_plan=self.raw_plan,
            task=task,
            guidance_state=guidance_state,
            source=self.plan_source,
            scenario=self.scenario,
        )

    def apply_edit(self, worktree: Path, plan: dict[str, Any]) -> dict[str, Any]:
        if plan.get("planning_only") is not True or plan.get("apply_mode") != "deterministic_safe_applier":
            raise SmokeFailure("live-plan adapter may only use the deterministic safe applier at this stage")
        result = DeterministicCodeEditAgent(self.scenario).apply_edit(worktree, plan)
        return {
            **result,
            "applied_by": "deterministic_safe_applier",
            "planned_by": self.agent_mode,
        }



class GeneratedEditorCodeEditAgent:
    """Deterministic generated-editor adapter with no direct worktree mutation.

    This adapter proves the Stage 4 boundary shape without involving a live model:
    it emits a deterministic plan and deterministic editor source, but the editor
    can only return proposed replacements from a capability-style sandbox.  The
    host validates and applies those proposals to the Git worktree.
    """

    agent_mode = "generated-editor"

    def __init__(self, scenario: ScenarioSpec | None = None) -> None:
        self.scenario = scenario or scenario_spec(DEFAULT_SCENARIO)

    def metadata(self) -> dict[str, Any]:
        return {
            "agent_mode": self.agent_mode,
            "editor_source": "deterministic_fixture",
            "planning_only": False,
            "apply_mode": "sandbox_proposal_host_apply",
            "direct_worktree_mutation_allowed": False,
            "scenario": self.scenario.name,
        }

    def plan(self, task: str, guidance_state: dict[str, Any]) -> dict[str, Any]:
        selected_files = list(self.scenario.expected_changed_files)
        return {
            "agent_mode": self.agent_mode,
            "scenario": self.scenario.name,
            "task": task,
            "selected_files": selected_files,
            "allowed_write_paths": selected_files,
            "forbidden_paths": guidance_state.get("forbidden_paths", []),
            "edit_strategy": "generated_editor_scenario_fixture_with_sandbox_proposal",
            "requires_generated_editor": True,
            "requires_verification_before_commit": True,
            "expected_verification_success": self.scenario.expects_verification_success,
            "apply_mode": "sandbox_proposal_host_apply",
        }

    def generate_editor(self, plan: dict[str, Any]) -> str:
        allowed = [safe_relative_path(path) for path in plan.get("allowed_write_paths", [])]
        expected_files = list(self.scenario.expected_changed_files)
        if allowed != expected_files or expected_files != ["app.py"]:
            raise SmokeFailure(f"generated-editor fixture only supports app.py at this stage; got {allowed!r}")
        return deterministic_generated_editor_source(self.scenario.final_app_py)

    def apply_edit(self, worktree: Path, plan: dict[str, Any]) -> dict[str, Any]:
        raise SmokeFailure("generated-editor adapter must not directly mutate the worktree; use sandbox proposal flow")


def deterministic_generated_editor_source(final_app_py: str = APP_PY_DETERMINISTIC_FINAL) -> str:
    return (
        "TARGET = 'app.py'\n"
        f"OLD = {APP_PY_INITIAL!r}\n"
        f"NEW = {final_app_py!r}\n"
        "\n"
        "def edit(files):\n"
        "    text = files.get(TARGET)\n"
        "    if text is None:\n"
        "        return {'status': 'needs_more_evidence', 'proposed_writes': {}, 'reason': 'target_missing'}\n"
        "    if OLD not in text:\n"
        "        return {'status': 'needs_more_evidence', 'proposed_writes': {}, 'reason': 'old_text_not_found'}\n"
        "    updated = text.replace(OLD, NEW, 1)\n"
        "    return {'status': 'done', 'proposed_writes': {TARGET: updated}}\n"
    )


def generated_editor_static_preflight(editor_source: str) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    try:
        tree = ast.parse(editor_source)
    except SyntaxError as exc:
        issues.append({"kind": "syntax_error", "detail": str(exc), "lineno": exc.lineno})
        tree = ast.Module(body=[], type_ignores=[])

    has_edit_function = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "edit":
            has_edit_function = True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif node.module:
                names = [node.module.split(".", 1)[0]]
            issues.append(
                {
                    "kind": "import_not_allowed",
                    "detail": ",".join(sorted(names)) or "import",
                    "lineno": getattr(node, "lineno", None),
                }
            )
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in GENERATED_EDITOR_BLOCKED_CALL_NAMES:
                issues.append(
                    {
                        "kind": "blocked_call",
                        "detail": func.id,
                        "lineno": getattr(node, "lineno", None),
                    }
                )
            if isinstance(func, ast.Attribute):
                attr = func.attr
                value = func.value
                if attr in GENERATED_EDITOR_BLOCKED_ATTR_NAMES:
                    issues.append(
                        {
                            "kind": "blocked_attribute_call",
                            "detail": attr,
                            "lineno": getattr(node, "lineno", None),
                        }
                    )
                if isinstance(value, ast.Name) and value.id in GENERATED_EDITOR_BLOCKED_IMPORT_ROOTS:
                    issues.append(
                        {
                            "kind": "blocked_module_call",
                            "detail": f"{value.id}.{attr}",
                            "lineno": getattr(node, "lineno", None),
                        }
                    )

    if not has_edit_function:
        issues.append({"kind": "missing_edit_function", "detail": "editor must define edit(files)", "lineno": None})

    no_imports = not any(issue["kind"] == "import_not_allowed" for issue in issues)
    no_open_eval_exec_subprocess = not any(
        issue["kind"] in {"blocked_call", "blocked_attribute_call", "blocked_module_call"}
        for issue in issues
    )
    return {
        "ok": not issues,
        "issues": issues,
        "source_sha256": text_sha256(editor_source),
        "generated_editor_present": bool(editor_source.strip()),
        "generated_editor_no_imports": no_imports,
        "generated_editor_no_open_eval_exec_subprocess": no_open_eval_exec_subprocess,
        "has_edit_function": has_edit_function,
    }


def _limited_generated_editor_builtins() -> dict[str, Any]:
    return {
        "bool": bool,
        "dict": dict,
        "Exception": Exception,
        "isinstance": isinstance,
        "len": len,
        "list": list,
        "set": set,
        "sorted": sorted,
        "str": str,
        "tuple": tuple,
    }


def execute_generated_editor_sandbox(editor_source: str, worktree: Path, allowed_paths: Sequence[str]) -> dict[str, Any]:
    safe_allowed_paths = [safe_relative_path(path) for path in allowed_paths]
    capability_files = {
        path: (worktree / path).read_text(encoding="utf-8")
        for path in safe_allowed_paths
    }
    watched_paths = sorted(set(safe_allowed_paths + ["README.md"]))
    watched_before = {
        path: file_sha256(worktree / path)
        for path in watched_paths
        if (worktree / path).exists()
    }

    env: dict[str, Any] = {"__builtins__": _limited_generated_editor_builtins()}
    exec(compile(editor_source, "<generated_editor>", "exec"), env, env)
    edit_func = env.get("edit")
    if not callable(edit_func):
        raise SmokeFailure("generated editor did not define callable edit(files)")

    raw_result = edit_func(dict(capability_files))
    if not isinstance(raw_result, dict):
        raise SmokeFailure("generated editor must return an object")
    status = str(raw_result.get("status", "")).strip()
    proposed_writes_raw = raw_result.get("proposed_writes", {})
    if not isinstance(proposed_writes_raw, dict):
        raise SmokeFailure("generated editor proposed_writes must be an object")

    proposed_writes: dict[str, str] = {}
    for raw_path, raw_text in proposed_writes_raw.items():
        safe_path = safe_relative_path(str(raw_path))
        if not isinstance(raw_text, str):
            raise SmokeFailure(f"generated editor proposed write for {safe_path!r} is not text")
        proposed_writes[safe_path] = raw_text

    watched_after = {
        path: file_sha256(worktree / path)
        for path in watched_paths
        if (worktree / path).exists()
    }
    changed_paths = sorted(
        path
        for path, proposed_text in proposed_writes.items()
        if capability_files.get(path) != proposed_text
    )
    return {
        "ok": status == "done",
        "status": status,
        "reason": str(raw_result.get("reason", "")),
        "allowed_paths": safe_allowed_paths,
        "changed_paths": changed_paths,
        "proposed_writes": proposed_writes,
        "proposed_write_sha256": {path: text_sha256(text) for path, text in proposed_writes.items()},
        "worktree_hashes_before": watched_before,
        "worktree_hashes_after": watched_after,
        "worktree_unchanged_during_sandbox": watched_before == watched_after,
    }


def validate_and_apply_sandbox_proposal(worktree: Path, plan: dict[str, Any], sandbox_result: dict[str, Any]) -> dict[str, Any]:
    allowed_paths = [safe_relative_path(path) for path in plan.get("allowed_write_paths", [])]
    selected_files = [safe_relative_path(path) for path in plan.get("selected_files", [])]
    forbidden_paths = [safe_relative_path(path) for path in plan.get("forbidden_paths", [])]
    proposed_writes = sandbox_result.get("proposed_writes", {})
    if not isinstance(proposed_writes, dict):
        raise SmokeFailure("sandbox result proposed_writes must be an object")

    proposed_paths = sorted(safe_relative_path(path) for path in proposed_writes.keys())
    changed_paths = sorted(safe_relative_path(path) for path in sandbox_result.get("changed_paths", []))
    validations = {
        "sandbox_status_done": sandbox_result.get("status") == "done",
        "sandbox_did_not_mutate_worktree": sandbox_result.get("worktree_unchanged_during_sandbox") is True,
        "proposed_paths_safe": proposed_paths == sorted(set(proposed_paths)),
        "proposed_paths_within_allowed": set(proposed_paths).issubset(set(allowed_paths)),
        "changed_paths_match_selected_files": changed_paths == sorted(selected_files),
        "forbidden_paths_not_proposed": not (set(proposed_paths) & set(forbidden_paths)),
        "requires_verification_before_commit": plan.get("requires_verification_before_commit") is True,
    }
    if not all(validations.values()):
        raise SmokeFailure(f"sandbox proposal failed host validation: {validations!r}")

    before_sha256 = {
        path: file_sha256(worktree / path)
        for path in proposed_paths
    }
    for path in proposed_paths:
        (worktree / path).write_text(str(proposed_writes[path]), encoding="utf-8")
    after_sha256 = {
        path: file_sha256(worktree / path)
        for path in proposed_paths
    }
    applied_changed_files = git_worktree_modified_files(worktree)
    validations["applied_changed_files_match_selected_files"] = applied_changed_files == sorted(selected_files)
    if not validations["applied_changed_files_match_selected_files"]:
        raise SmokeFailure(f"host apply changed unexpected files: {applied_changed_files!r}")

    return {
        "ok": all(validations.values()),
        "validations": validations,
        "changed_files": applied_changed_files,
        "before_sha256_by_path": before_sha256,
        "after_sha256_by_path": after_sha256,
    }


def run_generated_editor_sandbox_apply(
    *,
    run_dir: Path,
    worktree: Path,
    plan: dict[str, Any],
    editor_source: str,
    edit_plan_boundary: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    editor_dir = run_dir / "generated_editor"
    editor_dir.mkdir(parents=True, exist_ok=True)
    editor_source_path = editor_dir / "editor.py"
    editor_source_path.write_text(editor_source, encoding="utf-8")
    generated_editor_boundary = write_boundary(
        run_dir,
        "generated_editor_boundary",
        {
            "boundary_type": "generated_editor_source",
            "editor_source_path": str(editor_source_path),
            "editor_source_sha256": text_sha256(editor_source),
            "generated_editor_present": bool(editor_source.strip()),
            "direct_worktree_mutation_allowed": False,
            "parent_boundaries": [edit_plan_boundary["sha256"]],
            "next_stage": "generated_editor_static_preflight",
        },
    )

    preflight = generated_editor_static_preflight(editor_source)
    static_preflight_boundary = write_boundary(
        run_dir,
        "generated_editor_static_preflight_boundary",
        {
            "boundary_type": "generated_editor_static_preflight",
            "preflight": preflight,
            "contracts": {
                "generated_editor_present": preflight["generated_editor_present"],
                "generated_editor_static_preflight_passed": preflight["ok"],
                "generated_editor_no_imports": preflight["generated_editor_no_imports"],
                "generated_editor_no_open_eval_exec_subprocess": preflight[
                    "generated_editor_no_open_eval_exec_subprocess"
                ],
            },
            "parent_boundaries": [generated_editor_boundary["sha256"]],
            "next_stage": "generated_editor_sandbox",
        },
    )
    if not preflight["ok"]:
        raise SmokeFailure(f"generated editor static preflight failed: {preflight['issues']!r}")

    sandbox_result = execute_generated_editor_sandbox(editor_source, worktree, plan.get("allowed_write_paths", []))
    sandbox_boundary = write_boundary(
        run_dir,
        "generated_editor_sandbox_boundary",
        {
            "boundary_type": "generated_editor_sandbox_result",
            "sandbox": {
                key: value
                for key, value in sandbox_result.items()
                if key != "proposed_writes"
            },
            "proposed_write_paths": sorted(sandbox_result.get("proposed_writes", {}).keys()),
            "contracts": {
                "generated_editor_sandbox_executed": True,
                "generated_editor_writes_only_allowed_paths": set(sandbox_result["proposed_writes"]).issubset(
                    set(plan.get("allowed_write_paths", []))
                ),
                "generated_editor_output_matches_plan": sorted(sandbox_result["changed_paths"])
                == sorted(plan.get("selected_files", [])),
                "generated_editor_did_not_touch_forbidden_files": not (
                    set(sandbox_result["proposed_writes"]) & set(plan.get("forbidden_paths", []))
                ),
                "generated_editor_worktree_unchanged_during_sandbox": sandbox_result[
                    "worktree_unchanged_during_sandbox"
                ],
            },
            "parent_boundaries": [static_preflight_boundary["sha256"]],
            "next_stage": "host_apply",
        },
    )
    if not sandbox_result["ok"]:
        raise SmokeFailure(f"generated editor sandbox did not produce a done proposal: {sandbox_result['status']!r}")

    host_apply = validate_and_apply_sandbox_proposal(worktree, plan, sandbox_result)
    scenario_name = str(plan.get("scenario", DEFAULT_SCENARIO) or DEFAULT_SCENARIO)
    scenario = scenario_spec(scenario_name)
    scenario_expected_sha = text_sha256(scenario.final_app_py)
    host_apply_contracts = {
        "host_validated_sandbox_proposal": host_apply["ok"],
        "host_applied_sandbox_proposal": host_apply["changed_files"] == sorted(plan.get("selected_files", [])),
        "generated_editor_output_matches_scenario_contract": host_apply["changed_files"] == list(scenario.expected_changed_files)
        and host_apply["after_sha256_by_path"].get("app.py") == scenario_expected_sha,
    }
    if scenario.name == DEFAULT_SCENARIO:
        host_apply_contracts["deterministic_safe_apply_can_be_replaced_by_sandbox_apply"] = (
            host_apply["changed_files"] == ["app.py"]
            and host_apply["after_sha256_by_path"].get("app.py") == text_sha256(APP_PY_DETERMINISTIC_FINAL)
        )
    host_apply_boundary = write_boundary(
        run_dir,
        "host_apply_boundary",
        {
            "boundary_type": "host_apply_sandbox_proposal",
            "host_apply": host_apply,
            "contracts": host_apply_contracts,
            "parent_boundaries": [sandbox_boundary["sha256"]],
            "next_stage": "verification",
        },
    )

    changed_files = host_apply["changed_files"]
    before_sha = host_apply["before_sha256_by_path"].get(changed_files[0], "") if changed_files else ""
    after_sha = host_apply["after_sha256_by_path"].get(changed_files[0], "") if changed_files else ""
    generated_contracts = {
        "generated_editor_present": preflight["generated_editor_present"],
        "generated_editor_static_preflight_passed": preflight["ok"],
        "generated_editor_no_imports": preflight["generated_editor_no_imports"],
        "generated_editor_no_open_eval_exec_subprocess": preflight["generated_editor_no_open_eval_exec_subprocess"],
        "generated_editor_sandbox_executed": True,
        "generated_editor_writes_only_allowed_paths": set(sandbox_result["proposed_writes"]).issubset(
            set(plan.get("allowed_write_paths", []))
        ),
        "generated_editor_output_matches_plan": sorted(sandbox_result["changed_paths"])
        == sorted(plan.get("selected_files", [])),
        "generated_editor_did_not_touch_forbidden_files": not (
            set(sandbox_result["proposed_writes"]) & set(plan.get("forbidden_paths", []))
        ),
        "generated_editor_worktree_unchanged_during_sandbox": sandbox_result["worktree_unchanged_during_sandbox"],
        **host_apply_contracts,
    }
    return (
        {
            "changed_files": changed_files,
            "before_sha256": before_sha,
            "after_sha256": after_sha,
            "applied_by": "host_apply_sandbox_proposal",
            "planned_by": "generated-editor",
            "generated_editor": {
                "editor_source_path": str(editor_source_path),
                "editor_source_sha256": text_sha256(editor_source),
                "preflight": preflight,
                "sandbox": {
                    key: value
                    for key, value in sandbox_result.items()
                    if key != "proposed_writes"
                },
                "host_apply": host_apply,
                "contracts": generated_contracts,
            },
        },
        [
            generated_editor_boundary,
            static_preflight_boundary,
            sandbox_boundary,
            host_apply_boundary,
        ],
    )



def load_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SmokeFailure(f"report does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"report is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise SmokeFailure(f"report must parse to an object: {path}")
    return payload


def build_agent_adapter(args: argparse.Namespace) -> CodeEditAgentAdapter:
    scenario = scenario_from_args(args)
    if args.agent == "deterministic":
        return DeterministicCodeEditAgent(scenario)
    if args.agent == "replay":
        if not args.replay_report:
            raise SmokeFailure("--agent replay requires --replay-report")
        replay_report_path = Path(args.replay_report).resolve()
        replay_report = load_report(replay_report_path)
        comparison = compare_report_contract_shape(replay_report, replay_report)
        if not comparison["ok"]:
            raise SmokeFailure(f"replay source report is malformed: {comparison['mismatches']!r}")
        return ReplayCodeEditAgent(replay_report, replay_report_path, scenario)
    if args.agent == "live-plan":
        raw_plan, plan_source = load_live_plan_payload(args)
        return LivePlanCodeEditAgent(raw_plan, plan_source, scenario)
    if args.agent == "generated-editor":
        return GeneratedEditorCodeEditAgent(scenario)
    raise SmokeFailure(f"unsupported agent mode for this smoke stage: {args.agent!r}")


REQUIRED_REPORT_KEYS = (
    "ok",
    "mode",
    "scenario",
    "scenario_contracts",
    "agent_mode",
    "run_id",
    "target_branch",
    "base_head",
    "final_head",
    "main_head",
    "commit",
    "changed_files",
    "guidance_events",
    "forbidden_paths",
    "verification",
    "contracts",
    "failed_contracts",
    "boundaries",
)

REQUIRED_BOUNDARY_NAMES = (
    "bootstrap_boundary",
    "guidance_boundary",
    "edit_plan_boundary",
    "verification_boundary",
    "commit_boundary",
)


def report_contract_shape(report: dict[str, Any]) -> dict[str, Any]:
    contracts = report.get("contracts") if isinstance(report.get("contracts"), dict) else {}
    boundaries = report.get("boundaries") if isinstance(report.get("boundaries"), list) else []
    boundary_names = [
        str(item.get("name") or "")
        for item in boundaries
        if isinstance(item, dict)
    ]
    return {
        "required_keys_present": {key: key in report for key in REQUIRED_REPORT_KEYS},
        "contract_keys": sorted(str(key) for key in contracts.keys()),
        "boundary_names": boundary_names,
        "required_boundaries_present": {
            name: name in boundary_names
            for name in REQUIRED_BOUNDARY_NAMES
        },
        "changed_files": sorted(str(path).replace("\\", "/") for path in report.get("changed_files", [])),
        "forbidden_paths": sorted(str(path).replace("\\", "/") for path in report.get("forbidden_paths", [])),
        "verification_checks": sorted(str(item) for item in report.get("verification", {}).get("checks", []))
        if isinstance(report.get("verification"), dict)
        else [],
    }


def compare_report_contract_shape(
    expected: dict[str, Any],
    actual: dict[str, Any],
    *,
    allow_generated_editor_extensions: bool = False,
) -> dict[str, Any]:
    expected_shape = report_contract_shape(expected)
    actual_shape = report_contract_shape(actual)
    mismatches: list[dict[str, Any]] = []
    for key in sorted(set(expected_shape) | set(actual_shape)):
        expected_value = expected_shape.get(key)
        actual_value = actual_shape.get(key)
        if allow_generated_editor_extensions and key == "boundary_names":
            expected_boundaries = set(expected_value or [])
            actual_boundaries = set(actual_value or [])
            generated_boundaries = {
                "generated_editor_boundary",
                "generated_editor_static_preflight_boundary",
                "generated_editor_sandbox_boundary",
                "host_apply_boundary",
            }
            if not expected_boundaries.issubset(actual_boundaries) or not generated_boundaries.issubset(actual_boundaries):
                mismatches.append(
                    {
                        "field": key,
                        "expected": {
                            "core_subset": sorted(expected_boundaries),
                            "generated_editor_subset": sorted(generated_boundaries),
                        },
                        "actual": sorted(actual_boundaries),
                    }
                )
            continue
        if allow_generated_editor_extensions and key == "contract_keys":
            expected_contracts = set(expected_value or [])
            actual_contracts = set(actual_value or [])
            generated_contracts = {
                "generated_editor_present",
                "generated_editor_static_preflight_passed",
                "generated_editor_no_imports",
                "generated_editor_no_open_eval_exec_subprocess",
                "generated_editor_sandbox_executed",
                "generated_editor_writes_only_allowed_paths",
                "generated_editor_output_matches_plan",
                "generated_editor_did_not_touch_forbidden_files",
                "host_validated_sandbox_proposal",
                "host_applied_sandbox_proposal",
                "deterministic_safe_apply_can_be_replaced_by_sandbox_apply",
            }
            if not expected_contracts.issubset(actual_contracts) or not generated_contracts.issubset(actual_contracts):
                mismatches.append(
                    {
                        "field": key,
                        "expected": {
                            "core_subset": sorted(expected_contracts),
                            "generated_editor_subset": sorted(generated_contracts),
                        },
                        "actual": sorted(actual_contracts),
                    }
                )
            continue
        if expected_value != actual_value:
            mismatches.append(
                {
                    "field": key,
                    "expected": expected_value,
                    "actual": actual_value,
                }
            )
    required_actual = actual_shape.get("required_keys_present", {})
    for key, present in required_actual.items():
        if not present:
            mismatches.append({"field": f"missing_required_key.{key}", "expected": True, "actual": False})
    required_boundaries = actual_shape.get("required_boundaries_present", {})
    for name, present in required_boundaries.items():
        if not present:
            mismatches.append({"field": f"missing_required_boundary.{name}", "expected": True, "actual": False})
    return {
        "ok": not mismatches,
        "expected_shape": expected_shape,
        "actual_shape": actual_shape,
        "mismatches": mismatches,
    }


def verify_worktree(worktree: Path) -> dict[str, Any]:
    verification_code = (
        "import app\n"
        "assert app.greet('  Ada  ') == 'Hello, Ada!'\n"
        "assert app.greet('Ada') == 'Hello, Ada!'\n"
        "assert app.greet('\\tGrace\\n') == 'Hello, Grace!'\n"
    )
    result = run_command(worktree, [sys.executable, "-S", "-c", verification_code])
    return {
        "ok": result.returncode == 0 and not result.timed_out,
        "checks": ["python_import_and_greet_contract"],
        "command": command_payload(result),
    }


def git_changed_files(worktree: Path, base_head: str, final_head: str = "HEAD") -> list[str]:
    result = git(worktree, ["diff", "--name-only", f"{base_head}..{final_head}"])
    require_command_ok(result, "git diff --name-only")
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def git_worktree_modified_files(worktree: Path) -> list[str]:
    result = git(worktree, ["diff", "--name-only"])
    require_command_ok(result, "git diff --name-only")
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def status_porcelain(worktree: Path) -> list[str]:
    result = git(worktree, ["status", "--porcelain"])
    require_command_ok(result, "git status --porcelain")
    return [line.rstrip() for line in result.stdout.splitlines() if line.strip()]


def create_commit(worktree: Path, message: str, files: Sequence[str]) -> dict[str, Any]:
    safe_files = [safe_relative_path(path) for path in files]
    require_command_ok(git(worktree, ["add", *safe_files]), "git add edited files")
    commit = git(worktree, ["commit", "-m", message])
    require_command_ok(commit, "git commit edited files")
    sha = git_stdout(worktree, ["rev-parse", "HEAD"])
    return {
        "created": True,
        "expected": True,
        "sha": sha,
        "message": message,
        "files": safe_files,
        "command": command_payload(commit),
    }


def commit_policy_from_args(args: argparse.Namespace) -> str:
    policy = str(getattr(args, "commit_policy", DEFAULT_COMMIT_POLICY) or DEFAULT_COMMIT_POLICY)
    if policy not in COMMIT_POLICIES:
        raise SmokeFailure(f"unsupported commit policy: {policy!r}")
    return policy


def commit_approval_timeout_from_args(args: argparse.Namespace) -> float:
    try:
        timeout = float(getattr(args, "approval_timeout_seconds", DEFAULT_APPROVAL_TIMEOUT_SECONDS))
    except (TypeError, ValueError) as exc:
        raise SmokeFailure("--approval-timeout-seconds must be a number") from exc
    if timeout < 0:
        raise SmokeFailure("--approval-timeout-seconds must be non-negative")
    return timeout


def summarize_commit_command(record: dict[str, Any], *, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "type": str(record.get("type", "")),
        "id": str(record.get("id", "")),
        "source": str(record.get("source", "")),
        "timestamp": str(record.get("timestamp", "")),
    }


def resolve_commit_policy(
    *,
    args: argparse.Namespace,
    run_dir: Path,
    commands_path: Path,
    seen_count: int,
    poll_seconds: float,
    changed_files_before_commit: Sequence[str],
    base_head: str,
    current_head: str,
    verification_boundary: dict[str, Any],
    event: Any,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Resolve whether verification-passed changes may be committed.

    The approval gate is intentionally agent-side and command-file based so the
    same primitive can be driven by the CLI smoke supervisor or later by the UI.
    """

    policy = commit_policy_from_args(args)
    timeout_seconds = commit_approval_timeout_from_args(args)
    changed_files = [safe_relative_path(path) for path in changed_files_before_commit]
    common: dict[str, Any] = {
        "policy": policy,
        "changed_files_before_commit": changed_files,
        "base_head": base_head,
        "head_before_commit_policy": current_head,
        "commit_not_created_before_approval": current_head == base_head,
    }

    if policy == "auto-after-verification":
        decision = {
            **common,
            "ok": True,
            "action": "commit",
            "decision": "auto_after_verification",
            "waited_for_approval": False,
            "approval_received": False,
            "rejected": False,
            "timed_out": False,
            "commit_expected": True,
            "contracts": {
                "commit_policy_auto_after_verification": True,
                "commit_policy_satisfied": True,
            },
        }
        boundary = write_boundary(
            run_dir,
            "commit_policy_boundary",
            {
                "boundary_type": "commit_policy_decision",
                "decision": decision,
                "parent_boundaries": [verification_boundary["sha256"]],
                "next_stage": "commit",
            },
        )
        return decision, boundary, seen_count

    if policy == "never":
        decision = {
            **common,
            "ok": True,
            "action": "skip_commit",
            "decision": "never",
            "waited_for_approval": False,
            "approval_received": False,
            "rejected": False,
            "timed_out": False,
            "commit_expected": False,
            "contracts": {
                "commit_policy_never_blocks_commit": True,
                "commit_blocked_by_policy": True,
                "commit_policy_satisfied": True,
            },
        }
        boundary = write_boundary(
            run_dir,
            "commit_policy_boundary",
            {
                "boundary_type": "commit_policy_decision",
                "decision": decision,
                "parent_boundaries": [verification_boundary["sha256"]],
                "next_stage": "report_without_commit",
            },
        )
        return decision, boundary, seen_count

    event(
        "commit_approval_waiting",
        commit_policy=policy,
        commands_path=str(commands_path),
        changed_files=changed_files,
        approval_timeout_seconds=timeout_seconds,
    )
    deadline = time.monotonic() + timeout_seconds
    accepted_command: dict[str, Any] | None = None
    accepted_command_index = -1
    rejected_commands: list[dict[str, Any]] = []

    while time.monotonic() <= deadline:
        new_records, seen_count = load_new_commands(commands_path, seen_count)
        for offset, record in enumerate(new_records):
            record_index = seen_count - len(new_records) + offset
            command_type = str(record.get("type", "")).strip()
            if command_type in {"approve_commit", "reject_commit"}:
                accepted_command = record
                accepted_command_index = record_index
                break
            rejected_commands.append(
                {
                    "index": record_index,
                    "record": record,
                    "reason": "not_a_commit_approval_command",
                }
            )
        if accepted_command is not None:
            break
        if timeout_seconds == 0:
            break
        time.sleep(poll_seconds)

    if accepted_command is None:
        decision = {
            **common,
            "ok": False,
            "action": "skip_commit",
            "decision": "approval_timeout",
            "waited_for_approval": True,
            "approval_received": False,
            "rejected": False,
            "timed_out": True,
            "commit_expected": False,
            "rejected_commands": rejected_commands,
            "contracts": {
                "commit_waited_for_approval": True,
                "commit_not_created_before_approval": current_head == base_head,
                "commit_blocked_by_policy": True,
                "commit_policy_satisfied": False,
            },
        }
        boundary = write_boundary(
            run_dir,
            "commit_policy_boundary",
            {
                "boundary_type": "commit_policy_decision",
                "decision": decision,
                "parent_boundaries": [verification_boundary["sha256"]],
                "next_stage": "error",
            },
        )
        return decision, boundary, seen_count

    command_type = str(accepted_command.get("type", "")).strip()
    command_summary = summarize_commit_command(accepted_command, index=accepted_command_index)
    if command_type == "reject_commit":
        event("commit_rejected", approval=command_summary)
        decision = {
            **common,
            "ok": True,
            "action": "skip_commit",
            "decision": "rejected",
            "waited_for_approval": True,
            "approval_received": False,
            "approval": command_summary,
            "rejected": True,
            "timed_out": False,
            "commit_expected": False,
            "rejected_commands": rejected_commands,
            "contracts": {
                "commit_waited_for_approval": True,
                "commit_not_created_before_approval": current_head == base_head,
                "reject_commit_blocks_commit": True,
                "commit_blocked_by_policy": True,
                "commit_policy_satisfied": True,
            },
        }
        boundary = write_boundary(
            run_dir,
            "commit_policy_boundary",
            {
                "boundary_type": "commit_policy_decision",
                "decision": decision,
                "parent_boundaries": [verification_boundary["sha256"]],
                "next_stage": "report_without_commit",
            },
        )
        return decision, boundary, seen_count

    event("commit_approval_received", approval=command_summary)
    decision = {
        **common,
        "ok": True,
        "action": "commit",
        "decision": "approved",
        "waited_for_approval": True,
        "approval_received": True,
        "approval": command_summary,
        "rejected": False,
        "timed_out": False,
        "commit_expected": True,
        "rejected_commands": rejected_commands,
        "contracts": {
            "commit_waited_for_approval": True,
            "commit_not_created_before_approval": current_head == base_head,
            "approval_received_while_running": True,
            "commit_policy_satisfied": True,
        },
    }
    boundary = write_boundary(
        run_dir,
        "commit_policy_boundary",
        {
            "boundary_type": "commit_policy_decision",
            "decision": decision,
            "parent_boundaries": [verification_boundary["sha256"]],
            "next_stage": "commit",
        },
    )
    return decision, boundary, seen_count


def run_agent(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    commands_path = Path(args.commands_path).resolve() if args.commands_path else run_dir / "commands.jsonl"
    report_path = Path(args.report_path).resolve() if args.report_path else run_dir / "report.json"
    target_branch = args.target_branch
    scenario = scenario_from_args(args)
    task = task_for_scenario(args, scenario)
    scenario_contracts = scenario.contracts()

    run_dir.mkdir(parents=True, exist_ok=True)
    commands_path.parent.mkdir(parents=True, exist_ok=True)
    if not commands_path.exists():
        commands_path.write_text("", encoding="utf-8")

    event_log_path = run_dir / "events.jsonl"

    def event(name: str, **fields: Any) -> dict[str, Any]:
        payload = emit_event(name, run_id=args.run_id, **fields)
        append_jsonl(event_log_path, payload)
        return payload

    try:
        require_git_available()
        agent_containerized = os.environ.get("MAIN_COMPUTER_AGENT_SMOKE_CONTAINER") == "1"
        agent_docker_network = os.environ.get("MAIN_COMPUTER_AGENT_SMOKE_DOCKER_NETWORK", "")
        agent_source_mount = os.environ.get("MAIN_COMPUTER_AGENT_SMOKE_SOURCE_MOUNT", "")
        event(
            "run_started",
            role="agent",
            agent_mode=args.agent,
            scenario=scenario.name,
            run_dir=str(run_dir),
            commands_path=str(commands_path),
            containerized=agent_containerized,
            docker_network=agent_docker_network,
            source_mount=agent_source_mount,
        )

        agent_adapter = build_agent_adapter(args)
        agent_adapter_metadata = agent_adapter.metadata()
        event("agent_adapter_selected", **agent_adapter_metadata)

        origin = run_dir / "origin"
        worktree = run_dir / "worktree"

        event("stage_started", stage="fixture_origin")
        fixture = create_fixture_origin(origin)
        event("stage_completed", stage="fixture_origin", base_head=fixture["base_head"])

        event("stage_started", stage="clone_and_branch")
        clone = clone_fixture(origin, worktree, target_branch)
        base_head = clone["base_head"]
        readme_before_sha = file_sha256(worktree / "README.md")
        event("stage_completed", stage="clone_and_branch", branch=target_branch, base_head=base_head)

        bootstrap_boundary = write_boundary(
            run_dir,
            "bootstrap_boundary",
            {
                "boundary_type": "agent_bootstrap",
                "agent_mode": args.agent,
                "adapter": agent_adapter.agent_mode,
                "task": task,
                "scenario": scenario.name,
                "scenario_contracts": scenario_contracts,
                "repo": {
                    "origin_path": str(origin),
                    "worktree": str(worktree),
                    "base_head": base_head,
                    "target_branch": target_branch,
                },
                "scope": {
                    "allowed_roots": ["repo-root"],
                    "allowed_write_paths": list(scenario.expected_changed_files),
                    "forbidden_paths": list(scenario.forbidden_files),
                    "forbidden_path_patterns": ["absolute paths", ".. traversal"],
                },
                "next_stage": "guidance_window",
            },
        )
        event("boundary_committed", boundary="bootstrap_boundary", sha256=bootstrap_boundary["sha256"])

        event(
            "guidance_window_open",
            commands_path=str(commands_path),
            guidance_window_seconds=args.guidance_window_seconds,
            poll_seconds=args.poll_seconds,
        )

        seen_count = 0
        all_guidance_records: list[dict[str, Any]] = []
        guidance_deadline = time.monotonic() + args.guidance_window_seconds
        while time.monotonic() < guidance_deadline:
            new_records, seen_count = load_new_commands(commands_path, seen_count)
            if new_records:
                all_guidance_records.extend(new_records)
                event("guidance_received", new_records=len(new_records), total_records=len(all_guidance_records))
                break
            time.sleep(args.poll_seconds)

        # Poll once more at the boundary so commands written before launch or just
        # after the window closes are still represented deterministically.
        new_records, seen_count = load_new_commands(commands_path, seen_count)
        if new_records:
            all_guidance_records.extend(new_records)
            event("guidance_received", new_records=len(new_records), total_records=len(all_guidance_records))

        guidance_state = derive_guidance_state(all_guidance_records)
        guidance_state["forbidden_paths"] = sorted(
            set(guidance_state.get("forbidden_paths", [])) | set(scenario.forbidden_files)
        )
        guidance_boundary = write_boundary(
            run_dir,
            "guidance_boundary",
            {
                "boundary_type": "guidance_compaction",
                "commands_path": str(commands_path),
                "accepted_guidance": guidance_state["accepted"],
                "rejected_guidance": guidance_state["rejected"],
                "instructions": guidance_state["instructions"],
                "forbidden_paths": guidance_state["forbidden_paths"],
                "active_constraints": {
                    "forbidden_files": guidance_state["forbidden_paths"],
                    "expected_changed_files": list(scenario.expected_changed_files),
                    "requires_commit": scenario.requires_commit,
                    "requires_approval": scenario.requires_approval,
                },
                "next_stage": "edit_plan",
            },
        )
        event(
            "boundary_committed",
            boundary="guidance_boundary",
            sha256=guidance_boundary["sha256"],
            accepted_guidance_count=len(guidance_state["accepted"]),
        )

        plan = agent_adapter.plan(task, guidance_state)
        generated_editor_mode = agent_adapter.agent_mode == "generated-editor"
        edit_plan_boundary = write_boundary(
            run_dir,
            "edit_plan_boundary",
            {
                "boundary_type": "edit_plan",
                "plan": plan,
                "parent_boundaries": [bootstrap_boundary["sha256"], guidance_boundary["sha256"]],
                "next_stage": "generated_editor" if generated_editor_mode else "apply_edit",
            },
        )
        event("boundary_committed", boundary="edit_plan_boundary", sha256=edit_plan_boundary["sha256"])

        generated_editor_boundaries: list[dict[str, Any]] = []
        if generated_editor_mode:
            if not isinstance(agent_adapter, GeneratedEditorCodeEditAgent):
                raise SmokeFailure("generated-editor mode selected a non-generated-editor adapter")
            editor_source = agent_adapter.generate_editor(plan)
            event("stage_started", stage="generated_editor_static_preflight", files=plan["allowed_write_paths"])
            edit_result, generated_editor_boundaries = run_generated_editor_sandbox_apply(
                run_dir=run_dir,
                worktree=worktree,
                plan=plan,
                editor_source=editor_source,
                edit_plan_boundary=edit_plan_boundary,
            )
            for boundary in generated_editor_boundaries:
                event("boundary_committed", boundary=boundary["name"], sha256=boundary["sha256"])
            event(
                "edit_applied",
                files=edit_result["changed_files"],
                after_sha256=edit_result["after_sha256"],
                applied_by=edit_result.get("applied_by"),
            )
        else:
            event("stage_started", stage="apply_edit", files=plan["allowed_write_paths"])
            edit_result = agent_adapter.apply_edit(worktree, plan)
            event("edit_applied", files=edit_result["changed_files"], after_sha256=edit_result["after_sha256"])

        event("stage_started", stage="verification")
        verification = verify_worktree(worktree)
        if verification["ok"]:
            event("verification_passed", checks=verification["checks"])
        else:
            event("verification_failed", command=verification["command"])

        changed_files_before_commit = git_worktree_modified_files(worktree)
        forbidden_changed = [
            path for path in changed_files_before_commit if path in set(guidance_state.get("forbidden_paths", []))
        ]
        readme_after_edit_sha = file_sha256(worktree / "README.md")
        verification_boundary = write_boundary(
            run_dir,
            "verification_boundary",
            {
                "boundary_type": "verification_result",
                "verification": verification,
                "expected_verification_success": scenario.expects_verification_success,
                "verification_outcome_expected": verification["ok"] == scenario.expects_verification_success,
                "changed_files_before_commit": changed_files_before_commit,
                "forbidden_changed": forbidden_changed,
                "readme_unchanged": readme_before_sha == readme_after_edit_sha,
                "next_stage": "commit" if verification["ok"] else "commit_blocked",
            },
        )
        event("boundary_committed", boundary="verification_boundary", sha256=verification_boundary["sha256"])

        if forbidden_changed:
            raise SmokeFailure(f"forbidden files changed before commit: {forbidden_changed!r}")

        if not verification["ok"] and scenario.expects_verification_success:
            raise SmokeFailure("verification failed before commit")

        if verification["ok"]:
            head_before_commit_policy = git_stdout(worktree, ["rev-parse", "HEAD"])
            commit_decision, commit_policy_boundary, seen_count = resolve_commit_policy(
                args=args,
                run_dir=run_dir,
                commands_path=commands_path,
                seen_count=seen_count,
                poll_seconds=args.poll_seconds,
                changed_files_before_commit=changed_files_before_commit,
                base_head=base_head,
                current_head=head_before_commit_policy,
                verification_boundary=verification_boundary,
                event=event,
            )
            event(
                "boundary_committed",
                boundary="commit_policy_boundary",
                sha256=commit_policy_boundary["sha256"],
                commit_policy=commit_decision["policy"],
                decision=commit_decision["decision"],
            )
            if not commit_decision["ok"]:
                raise SmokeFailure(f"commit policy was not satisfied: {commit_decision['decision']}")

            if commit_decision["action"] == "commit":
                event("stage_started", stage="commit", commit_policy=commit_decision["policy"])
                commit = create_commit(
                    worktree,
                    f"smoke: apply {args.agent} guided greeting edit",
                    changed_files_before_commit,
                )
                final_head = git_stdout(worktree, ["rev-parse", "HEAD"])
                main_head = git_stdout(worktree, ["rev-parse", "main"])
                branch = git_stdout(worktree, ["branch", "--show-current"])
                changed_files = git_changed_files(worktree, base_head, final_head)
                repo_status = status_porcelain(worktree)
                event("commit_created", sha=commit["sha"], branch=branch, files=changed_files)
            else:
                event(
                    "commit_blocked_by_policy",
                    commit_policy=commit_decision["policy"],
                    decision=commit_decision["decision"],
                    files=changed_files_before_commit,
                )
                final_head = git_stdout(worktree, ["rev-parse", "HEAD"])
                main_head = git_stdout(worktree, ["rev-parse", "main"])
                branch = git_stdout(worktree, ["branch", "--show-current"])
                changed_files = list(changed_files_before_commit)
                repo_status = status_porcelain(worktree)
                commit = {
                    "created": False,
                    "expected": False,
                    "sha": final_head,
                    "message": "",
                    "files": [],
                    "blocked_by_policy": True,
                    "policy": commit_decision["policy"],
                    "decision": commit_decision["decision"],
                }
        else:
            final_head = git_stdout(worktree, ["rev-parse", "HEAD"])
            main_head = git_stdout(worktree, ["rev-parse", "main"])
            branch = git_stdout(worktree, ["branch", "--show-current"])
            changed_files = list(changed_files_before_commit)
            repo_status = status_porcelain(worktree)
            commit_decision = {
                "ok": True,
                "policy": args.commit_policy,
                "decision": "skipped_verification_failed",
                "action": "none",
                "commit_expected": False,
                "verification_failed": True,
                "contracts": {
                    "commit_policy_satisfied": True,
                    "verification_failed_as_expected": True,
                    "verification_failure_blocks_commit": final_head == base_head,
                    "commit_not_created_after_verification_failure": True,
                },
            }
            commit_policy_boundary = write_boundary(
                run_dir,
                "commit_policy_boundary",
                {
                    "boundary_type": "commit_policy_decision",
                    "decision": "skipped_verification_failed",
                    "parent_boundaries": [verification_boundary["sha256"]],
                    "next_stage": "commit_result",
                },
            )
            event(
                "boundary_committed",
                boundary="commit_policy_boundary",
                sha256=commit_policy_boundary["sha256"],
                commit_policy=commit_decision["policy"],
                decision=commit_decision["decision"],
            )
            event(
                "commit_blocked_by_verification_failure",
                commit_policy=commit_decision["policy"],
                files=changed_files_before_commit,
            )
            commit = {
                "created": False,
                "expected": False,
                "sha": final_head,
                "message": "",
                "files": [],
                "blocked_by_verification_failure": True,
                "policy": commit_decision["policy"],
                "decision": commit_decision["decision"],
            }

        generated_editor_contracts = (
            edit_result.get("generated_editor", {}).get("contracts", {})
            if isinstance(edit_result.get("generated_editor"), dict)
            and isinstance(edit_result.get("generated_editor", {}).get("contracts"), dict)
            else {}
        )
        policy_contracts = (
            commit_decision.get("contracts", {}) if isinstance(commit_decision.get("contracts"), dict) else {}
        )
        scoped_changed_files = changed_files if commit.get("created") else changed_files_before_commit
        expected_changed_files = list(scenario.expected_changed_files)
        effective_requires_commit = scenario.requires_commit and args.commit_policy != "never" and verification["ok"]
        contracts = {
            "agent_adapter_selected": agent_adapter.agent_mode == args.agent,
            "agent_containerized": agent_containerized,
            "docker_network_none": agent_docker_network == "none",
            "docker_source_mount_read_only": agent_source_mount == "readonly",
            "guidance_seen": len(guidance_state["accepted"]) > 0,
            "guidance_integrated_before_edit": len(guidance_state["accepted"]) > 0
            and edit_plan_boundary["payload"]["timestamp"] >= guidance_boundary["payload"]["timestamp"],
            "branch_isolated": branch == target_branch and main_head == base_head,
            "changed_files_scoped": scoped_changed_files == expected_changed_files,
            "forbidden_files_unchanged": all(
                file_sha256(worktree / path) == readme_before_sha if path == "README.md" else not (worktree / path).exists()
                for path in scenario.forbidden_files
            ),
            "verification_outcome_expected": verification["ok"] == scenario.expects_verification_success,
            "scenario_contracts_satisfied": (
                scoped_changed_files == expected_changed_files
                and set(guidance_state.get("forbidden_paths", [])) >= set(scenario.forbidden_files)
                and bool(commit_decision.get("commit_expected")) == effective_requires_commit
            ),
            "report_written": True,
            **policy_contracts,
            **generated_editor_contracts,
        }
        if verification["ok"]:
            contracts["verification_passed"] = True
        else:
            contracts["verification_failed_as_expected"] = not scenario.expects_verification_success
        if commit_decision.get("commit_expected"):
            contracts["commit_created"] = commit["created"] and final_head == commit["sha"] and final_head != base_head
            contracts["repo_clean_after_commit"] = repo_status == []
            if commit_decision.get("decision") == "approved":
                contracts["commit_created_after_approval"] = (
                    commit["created"]
                    and bool(commit_decision.get("approval_received"))
                    and final_head == commit["sha"]
                    and final_head != base_head
                )
        elif commit.get("blocked_by_verification_failure"):
            contracts["verification_failure_blocks_commit"] = (
                not commit["created"]
                and final_head == base_head
                and changed_files_before_commit == expected_changed_files
            )
            contracts["commit_not_created_after_verification_failure"] = True
            contracts["worktree_left_uncommitted_after_verification_failure"] = repo_status != []
        else:
            contracts["commit_blocked_by_policy"] = (
                not commit["created"]
                and bool(commit.get("blocked_by_policy"))
                and final_head == base_head
                and changed_files_before_commit == expected_changed_files
            )
            contracts["worktree_left_uncommitted_by_policy"] = repo_status != []
        failed_contracts = sorted(name for name, ok in contracts.items() if not ok)
        commit_boundary = write_boundary(
            run_dir,
            "commit_boundary",
            {
                "boundary_type": "commit_result",
                "base_head": base_head,
                "final_head": final_head,
                "main_head": main_head,
                "target_branch": target_branch,
                "scenario_contracts": scenario_contracts,
                "commit_policy": commit_decision,
                "commit": commit,
                "changed_files": changed_files,
                "changed_files_before_commit": changed_files_before_commit,
                "contracts": contracts,
                "failed_contracts": failed_contracts,
            },
        )
        event("boundary_committed", boundary="commit_boundary", sha256=commit_boundary["sha256"])

        report = {
            "ok": not failed_contracts,
            "mode": MODE,
            "scenario": scenario.name,
            "scenario_contracts": scenario_contracts,
            "agent_mode": args.agent,
            "agent_adapter": agent_adapter_metadata,
            "edit_plan": plan,
            "edit_result": edit_result,
            "run_id": args.run_id,
            "run_dir": str(run_dir),
            "commands_path": str(commands_path),
            "report_path": str(report_path),
            "origin_path": str(origin),
            "worktree": str(worktree),
            "target_branch": target_branch,
            "commit_policy": commit_decision,
            "base_head": base_head,
            "final_head": final_head,
            "main_head": main_head,
            "commit": commit,
            "changed_files": changed_files,
            "guidance_events": guidance_state["accepted"],
            "rejected_guidance_events": guidance_state["rejected"],
            "forbidden_paths": guidance_state["forbidden_paths"],
            "verification": verification,
            "contracts": contracts,
            "failed_contracts": failed_contracts,
            "boundaries": [
                {key: value for key, value in boundary.items() if key != "payload"}
                for boundary in [
                    bootstrap_boundary,
                    guidance_boundary,
                    edit_plan_boundary,
                    *generated_editor_boundaries,
                    verification_boundary,
                    commit_policy_boundary,
                    commit_boundary,
                ]
            ],
        }
        atomic_write_json(report_path, report)
        event("report_written", report_path=str(report_path), ok=report["ok"])
        if failed_contracts:
            event("run_finished", status="failed", failed_contracts=failed_contracts)
            return 1
        event("run_finished", status="ok", final_head=final_head)
        return 0
    except Exception as exc:
        error_report = {
            "ok": False,
            "mode": MODE,
            "scenario": scenario.name,
            "scenario_contracts": scenario_contracts,
            "agent_mode": args.agent,
            "run_id": args.run_id,
            "run_dir": str(run_dir),
            "commands_path": str(commands_path),
            "report_path": str(report_path),
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        atomic_write_json(report_path, error_report)
        event("run_finished", status="error", error=str(exc), error_type=type(exc).__name__)
        return 1


def parse_child_event(line: str) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"child emitted non-JSON stdout line: {line!r}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("event"), str):
        raise SmokeFailure(f"child emitted malformed event: {payload!r}")
    return payload


def run_supervisor(args: argparse.Namespace) -> int:
    require_docker_image(args.docker_image)
    run_id = args.run_id or run_id_from_now()
    work_root = Path(args.work_root).resolve() if args.work_root else default_work_root()
    run_dir = work_root / run_id
    commands_path = run_dir / "commands.jsonl"
    report_path = run_dir / "report.json"
    supervisor_report_path = run_dir / "supervisor_report.json"
    repo_root = Path(__file__).resolve().parents[1]
    scenario = scenario_from_args(args)
    task = task_for_scenario(args, scenario)
    guidance_text = guidance_text_for_scenario(args, scenario)
    run_dir.mkdir(parents=True, exist_ok=True)
    commands_path.write_text("", encoding="utf-8")

    child_args = build_docker_agent_command(
        image=args.docker_image,
        repo_root=repo_root,
        run_dir=run_dir,
        run_id=run_id,
        commands_path=commands_path,
        report_path=report_path,
        agent=args.agent,
        scenario=args.scenario,
        target_branch=args.target_branch,
        task=task,
        guidance_window_seconds=args.guidance_window_seconds,
        poll_seconds=args.poll_seconds,
        commit_policy=args.commit_policy,
        approval_timeout_seconds=args.approval_timeout_seconds,
        replay_report_path=CONTAINER_REPLAY_REPORT_PATH if args.agent == "replay" else "",
        live_plan_path=CONTAINER_LIVE_PLAN_PATH if args.agent == "live-plan" else "",
    )

    if args.agent == "replay":
        if not args.replay_report:
            raise SmokeFailure("--agent replay requires --replay-report")
        replay_source = Path(args.replay_report).resolve()
        if not replay_source.exists():
            raise SmokeFailure(f"replay report does not exist: {replay_source}")
        shutil.copy2(replay_source, run_dir / "replay_source_report.json")
    if args.agent == "live-plan":
        live_plan_destination = run_dir / "live_plan.json"
        if args.live_plan_path:
            live_plan_source = Path(args.live_plan_path).resolve()
            if not live_plan_source.exists():
                raise SmokeFailure(f"live plan file does not exist: {live_plan_source}")
            shutil.copy2(live_plan_source, live_plan_destination)
        elif args.live_plan_json:
            payload = json.loads(args.live_plan_json)
            if not isinstance(payload, dict):
                raise SmokeFailure("--live-plan-json must parse to an object")
            atomic_write_json(live_plan_destination, payload)
        else:
            atomic_write_json(live_plan_destination, default_live_plan_payload())

    emit_event(
        "supervisor_started",
        run_id=run_id,
        role="supervisor",
        agent_mode=args.agent,
        scenario=scenario.name,
        docker_image=args.docker_image,
        run_dir=str(run_dir),
        commands_path=str(commands_path),
        agent_boundary="docker",
    )
    emit_event(
        "agent_container_launching",
        run_id=run_id,
        docker_image=args.docker_image,
        docker_network="none",
        run_mount=str(run_dir),
        source_mount=str(repo_root),
        source_mount_mode="readonly",
    )

    proc = subprocess.Popen(
        child_args,
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=command_env(),
        bufsize=1,
    )
    assert proc.stdout is not None
    assert proc.stderr is not None

    received_events: list[dict[str, Any]] = []
    guidance_injected = False
    guidance_window_seen_while_running = False
    guidance_received_seen_while_running = False
    guidance_payload = {
        "type": "add_instruction",
        "text": guidance_text,
        "source": "deterministic_smoke_supervisor",
        "id": "guidance-001",
        "timestamp": utc_now(),
    }
    approval_injected = False
    commit_approval_waiting_seen_while_running = False
    commit_approval_received_seen_while_running = False
    approval_payload = {
        "type": "approve_commit",
        "source": "deterministic_smoke_supervisor",
        "id": "approval-001",
        "timestamp": utc_now(),
    }

    for line in proc.stdout:
        stripped = line.strip()
        if not stripped:
            continue
        payload = parse_child_event(stripped)
        payload["_supervisor_received_monotonic"] = time.monotonic()
        payload["_child_running_when_received"] = proc.poll() is None
        received_events.append(payload)
        # Echo the child event so the smoke itself has realtime stdout too.
        print(json_dumps({key: value for key, value in payload.items() if not key.startswith("_")}), flush=True)

        if payload.get("event") == "guidance_window_open" and not guidance_injected:
            guidance_window_seen_while_running = proc.poll() is None
            append_jsonl(commands_path, guidance_payload)
            guidance_injected = True
            emit_event(
                "supervisor_guidance_injected",
                run_id=run_id,
                commands_path=str(commands_path),
                child_running=proc.poll() is None,
                guidance=guidance_payload,
            )
        if payload.get("event") == "guidance_received":
            guidance_received_seen_while_running = proc.poll() is None
        if payload.get("event") == "commit_approval_waiting" and args.commit_policy == "require-approval":
            commit_approval_waiting_seen_while_running = proc.poll() is None
            if not approval_injected:
                approval_payload = {**approval_payload, "timestamp": utc_now()}
                append_jsonl(commands_path, approval_payload)
                approval_injected = True
                emit_event(
                    "supervisor_commit_approval_injected",
                    run_id=run_id,
                    commands_path=str(commands_path),
                    child_running=proc.poll() is None,
                    approval=approval_payload,
                )
        if payload.get("event") == "commit_approval_received":
            commit_approval_received_seen_while_running = proc.poll() is None

    returncode = proc.wait(timeout=args.timeout_seconds)
    stderr = proc.stderr.read()
    agent_report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}

    event_names = [event.get("event") for event in received_events]
    run_started_events = [event for event in received_events if event.get("event") == "run_started"]
    agent_run_started = run_started_events[-1] if run_started_events else {}
    agent_contracts = agent_report.get("contracts", {}) if isinstance(agent_report.get("contracts"), dict) else {}
    agent_scenario_contracts = (
        agent_report.get("scenario_contracts", {}) if isinstance(agent_report.get("scenario_contracts"), dict) else {}
    )
    agent_adapter_report = agent_report.get("agent_adapter", {}) if isinstance(agent_report.get("agent_adapter"), dict) else {}
    commit_report = agent_report.get("commit", {}) if isinstance(agent_report.get("commit"), dict) else {}
    commit_expected = bool(commit_report.get("expected", True))
    actual_commit_created = bool(commit_report.get("created"))
    agent_boundaries = agent_report.get("boundaries", []) if isinstance(agent_report.get("boundaries"), list) else []
    agent_boundary_names = [
        str(boundary.get("name") or "")
        for boundary in agent_boundaries
        if isinstance(boundary, dict)
    ]
    commit_policy_boundary_written = "commit_policy_boundary" in agent_boundary_names
    commit_boundary_written = "commit_boundary" in agent_boundary_names
    commit_blocked_by_policy = (
        not commit_expected
        and not actual_commit_created
        and bool(agent_contracts.get("commit_blocked_by_policy"))
    )
    commit_blocked_by_verification_failure = (
        not commit_expected
        and not actual_commit_created
        and bool(agent_contracts.get("verification_failure_blocks_commit"))
        and bool(commit_report.get("blocked_by_verification_failure"))
    )
    comparison_report_path = args.compare_report or (args.replay_report if args.agent == "replay" else "")
    report_shape_comparison: dict[str, Any] | None = None
    if comparison_report_path:
        report_shape_comparison = compare_report_contract_shape(
            load_report(Path(comparison_report_path)),
            agent_report,
            allow_generated_editor_extensions=args.agent == "generated-editor",
        )

    docker_command_contracts = {
        "agent_containerized": bool(agent_run_started.get("containerized")) and bool(agent_contracts.get("agent_containerized")),
        "docker_network_none": "--network" in child_args
        and "none" in child_args
        and bool(agent_contracts.get("docker_network_none")),
        "docker_run_directory_mounted": docker_mount_arg(run_dir, CONTAINER_RUN_DIR) in child_args
        and report_path.exists(),
        "docker_source_mount_read_only": docker_mount_arg(repo_root, CONTAINER_SOURCE_DIR, readonly=True) in child_args
        and bool(agent_contracts.get("docker_source_mount_read_only")),
    }
    contracts = {
        **docker_command_contracts,
        "child_process_ok": returncode == 0,
        "stdout_jsonl_events": len(received_events) >= 8,
        "stdout_realtime": guidance_window_seen_while_running and guidance_received_seen_while_running,
        "guidance_written_while_running": guidance_injected,
        "guidance_seen_by_agent": bool(agent_contracts.get("guidance_seen")),
        "guidance_integrated_before_edit": bool(agent_contracts.get("guidance_integrated_before_edit")),
        "branch_isolated": bool(agent_contracts.get("branch_isolated")),
        "changed_files_scoped": bool(agent_contracts.get("changed_files_scoped")),
        "forbidden_files_unchanged": bool(agent_contracts.get("forbidden_files_unchanged")),
        "verification_outcome_expected": bool(
            agent_contracts.get("verification_outcome_expected", agent_contracts.get("verification_passed"))
        ),
        "scenario_contracts_present": agent_scenario_contracts == scenario.contracts(),
        "scenario_contracts_satisfied": bool(agent_contracts.get("scenario_contracts_satisfied", True)),
        "commit_policy_boundary_written": commit_policy_boundary_written,
        "commit_boundary_written": commit_boundary_written,
        "commit_policy_satisfied": bool(agent_contracts.get("commit_policy_satisfied")),
        "commit_approval_gate": args.commit_policy != "require-approval"
        or (
            commit_approval_waiting_seen_while_running
            and approval_injected
            and commit_approval_received_seen_while_running
            and bool(agent_contracts.get("commit_waited_for_approval"))
            and bool(agent_contracts.get("commit_not_created_before_approval"))
            and bool(agent_contracts.get("approval_received_while_running"))
            and bool(agent_contracts.get("commit_created_after_approval"))
        ),
        "report_written": report_path.exists() and bool(agent_contracts.get("report_written")),
        "live_plan_deterministic_apply": args.agent != "live-plan"
        or (
            agent_adapter_report.get("planning_only") is True
            and agent_adapter_report.get("apply_mode") == "deterministic_safe_applier"
        ),
        "generated_editor_sandbox_apply": args.agent != "generated-editor"
        or (
            agent_adapter_report.get("apply_mode") == "sandbox_proposal_host_apply"
            and bool(agent_contracts.get("generated_editor_static_preflight_passed"))
            and bool(agent_contracts.get("generated_editor_sandbox_executed"))
            and bool(agent_contracts.get("generated_editor_worktree_unchanged_during_sandbox"))
            and bool(agent_contracts.get("host_validated_sandbox_proposal"))
            and bool(agent_contracts.get("host_applied_sandbox_proposal"))
        ),
        "required_event_order": event_names.index("guidance_window_open") < event_names.index("edit_applied")
        if "guidance_window_open" in event_names and "edit_applied" in event_names
        else False,
    }
    if scenario.expects_verification_success:
        contracts["verification_passed"] = bool(agent_contracts.get("verification_passed"))
    else:
        contracts["verification_failed_as_expected"] = bool(agent_contracts.get("verification_failed_as_expected"))
        contracts["verification_failure_blocks_commit"] = commit_blocked_by_verification_failure
    if commit_expected:
        contracts["commit_created"] = (
            actual_commit_created
            and bool(agent_contracts.get("commit_created"))
            and agent_report.get("final_head") == commit_report.get("sha")
            and agent_report.get("final_head") != agent_report.get("base_head")
        )
    elif commit_blocked_by_verification_failure:
        contracts["commit_not_created_after_verification_failure"] = True
    else:
        contracts["commit_not_created_by_policy"] = commit_blocked_by_policy
    if report_shape_comparison is not None:
        contracts["report_contract_shape_matches_reference"] = bool(report_shape_comparison.get("ok"))
    failed_contracts = sorted(name for name, ok in contracts.items() if not ok)

    supervisor_report = {
        "ok": returncode == 0 and not failed_contracts,
        "mode": MODE,
        "scenario": scenario.name,
        "scenario_contracts": scenario.contracts(),
        "agent_mode": args.agent,
        "agent_boundary": "docker",
        "docker_image": args.docker_image,
        "docker_command": child_args,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "commands_path": str(commands_path),
        "agent_report_path": str(report_path),
        "supervisor_report_path": str(supervisor_report_path),
        "child_returncode": returncode,
        "child_stderr": stderr,
        "child_event_names": event_names,
        "guidance_payload": guidance_payload,
        "approval_payload": approval_payload if args.commit_policy == "require-approval" else None,
        "commit_created": actual_commit_created,
        "commit_expected": commit_expected,
        "commit_blocked_by_policy": commit_blocked_by_policy,
        "commit_blocked_by_verification_failure": commit_blocked_by_verification_failure,
        "commit_policy_boundary_written": commit_policy_boundary_written,
        "commit_boundary_written": commit_boundary_written,
        "contracts": contracts,
        "failed_contracts": failed_contracts,
        "report_shape_comparison": report_shape_comparison,
        "agent_report": agent_report,
    }
    atomic_write_json(supervisor_report_path, supervisor_report)
    if failed_contracts or returncode != 0:
        emit_event(
            "self_check_failed",
            run_id=run_id,
            failed_contracts=failed_contracts,
            child_returncode=returncode,
            supervisor_report_path=str(supervisor_report_path),
            child_stderr_tail=stderr[-1200:],
        )
        return 1

    emit_event(
        "self_check_passed",
        run_id=run_id,
        report_path=str(supervisor_report_path),
        agent_report_path=str(report_path),
        final_head=agent_report.get("final_head"),
        commit_sha=agent_report.get("commit", {}).get("sha"),
        commit_created=actual_commit_created,
        commit_expected=commit_expected,
        commit_blocked_by_policy=commit_blocked_by_policy,
        commit_boundary_written=commit_boundary_written,
        contracts=contracts,
    )
    return 0



def namespace_with(args: argparse.Namespace, **updates: Any) -> argparse.Namespace:
    payload = dict(vars(args))
    payload.update(updates)
    return argparse.Namespace(**payload)


def run_replay_cycle(args: argparse.Namespace) -> int:
    """Run deterministic first, then replay its report through the same Docker harness."""

    scenario = scenario_from_args(args)

    cycle_id = args.run_id or run_id_from_now()
    parent_work_root = Path(args.work_root).resolve() if args.work_root else default_work_root()
    cycle_dir = parent_work_root / cycle_id
    cycle_dir.mkdir(parents=True, exist_ok=True)
    cycle_report_path = cycle_dir / "replay_cycle_report.json"
    deterministic_run_id = f"{cycle_id}-deterministic"
    replay_run_id = f"{cycle_id}-replay"

    emit_event(
        "replay_cycle_started",
        run_id=cycle_id,
        role="supervisor",
        agent_boundary="docker",
        deterministic_run_id=deterministic_run_id,
        replay_run_id=replay_run_id,
        cycle_dir=str(cycle_dir),
    )

    deterministic_args = namespace_with(
        args,
        exercise_replay=False,
        agent="deterministic",
        run_id=deterministic_run_id,
        work_root=str(cycle_dir),
        replay_report="",
        compare_report="",
    )
    emit_event("replay_cycle_stage_started", run_id=cycle_id, stage="deterministic_baseline")
    deterministic_rc = run_supervisor(deterministic_args)
    deterministic_run_dir = cycle_dir / deterministic_run_id
    deterministic_agent_report_path = deterministic_run_dir / "report.json"
    deterministic_supervisor_report_path = deterministic_run_dir / "supervisor_report.json"

    if deterministic_rc != 0 or not deterministic_agent_report_path.exists():
        cycle_report = {
            "ok": False,
            "mode": MODE,
            "scenario": scenario.name,
            "cycle_id": cycle_id,
            "failed_stage": "deterministic_baseline",
            "deterministic_returncode": deterministic_rc,
            "deterministic_agent_report_path": str(deterministic_agent_report_path),
            "deterministic_supervisor_report_path": str(deterministic_supervisor_report_path),
            "replay_returncode": None,
            "contracts": {
                "deterministic_self_check_passed": False,
                "replay_self_check_passed": False,
                "report_contract_shape_matches_reference": False,
            },
        }
        atomic_write_json(cycle_report_path, cycle_report)
        emit_event(
            "replay_cycle_failed",
            run_id=cycle_id,
            failed_stage="deterministic_baseline",
            report_path=str(cycle_report_path),
            deterministic_returncode=deterministic_rc,
        )
        return 1

    emit_event(
        "replay_cycle_stage_completed",
        run_id=cycle_id,
        stage="deterministic_baseline",
        report_path=str(deterministic_agent_report_path),
    )

    replay_args = namespace_with(
        args,
        exercise_replay=False,
        agent="replay",
        run_id=replay_run_id,
        work_root=str(cycle_dir),
        replay_report=str(deterministic_agent_report_path),
        compare_report=str(deterministic_agent_report_path),
    )
    emit_event("replay_cycle_stage_started", run_id=cycle_id, stage="replay")
    replay_rc = run_supervisor(replay_args)
    replay_run_dir = cycle_dir / replay_run_id
    replay_agent_report_path = replay_run_dir / "report.json"
    replay_supervisor_report_path = replay_run_dir / "supervisor_report.json"

    deterministic_agent_report = load_report(deterministic_agent_report_path)
    replay_agent_report = load_report(replay_agent_report_path) if replay_agent_report_path.exists() else {}
    deterministic_supervisor_report = (
        load_report(deterministic_supervisor_report_path) if deterministic_supervisor_report_path.exists() else {}
    )
    replay_supervisor_report = load_report(replay_supervisor_report_path) if replay_supervisor_report_path.exists() else {}
    shape_comparison = compare_report_contract_shape(deterministic_agent_report, replay_agent_report)

    deterministic_contracts = (
        deterministic_supervisor_report.get("contracts", {})
        if isinstance(deterministic_supervisor_report.get("contracts"), dict)
        else {}
    )
    replay_contracts = (
        replay_supervisor_report.get("contracts", {})
        if isinstance(replay_supervisor_report.get("contracts"), dict)
        else {}
    )
    replay_agent_adapter = (
        replay_agent_report.get("agent_adapter", {}) if isinstance(replay_agent_report.get("agent_adapter"), dict) else {}
    )
    contracts = {
        "deterministic_self_check_passed": deterministic_rc == 0 and bool(deterministic_supervisor_report.get("ok")),
        "replay_self_check_passed": replay_rc == 0 and bool(replay_supervisor_report.get("ok")),
        "deterministic_agent_containerized": bool(deterministic_contracts.get("agent_containerized")),
        "replay_agent_containerized": bool(replay_contracts.get("agent_containerized")),
        "replay_agent_mode": replay_agent_report.get("agent_mode") == "replay",
        "replay_recorded_source_report": replay_agent_adapter.get("replay_source_report_path") == CONTAINER_REPLAY_REPORT_PATH,
        "replay_compared_against_deterministic": bool(replay_contracts.get("report_contract_shape_matches_reference")),
        "report_contract_shape_matches_reference": bool(shape_comparison.get("ok")),
        "deterministic_then_replay_order": True,
    }
    failed_contracts = sorted(name for name, ok in contracts.items() if not ok)
    cycle_report = {
        "ok": not failed_contracts,
        "mode": MODE,
        "scenario": scenario.name,
        "cycle_id": cycle_id,
        "run_dir": str(cycle_dir),
        "deterministic": {
            "run_id": deterministic_run_id,
            "returncode": deterministic_rc,
            "agent_report_path": str(deterministic_agent_report_path),
            "supervisor_report_path": str(deterministic_supervisor_report_path),
            "agent_report": deterministic_agent_report,
        },
        "replay": {
            "run_id": replay_run_id,
            "returncode": replay_rc,
            "replay_source_report_path": str(deterministic_agent_report_path),
            "agent_report_path": str(replay_agent_report_path),
            "supervisor_report_path": str(replay_supervisor_report_path),
            "agent_report": replay_agent_report,
        },
        "report_shape_comparison": shape_comparison,
        "contracts": contracts,
        "failed_contracts": failed_contracts,
    }
    atomic_write_json(cycle_report_path, cycle_report)
    if failed_contracts:
        emit_event(
            "replay_cycle_failed",
            run_id=cycle_id,
            failed_contracts=failed_contracts,
            report_path=str(cycle_report_path),
        )
        return 1
    emit_event(
        "replay_cycle_passed",
        run_id=cycle_id,
        report_path=str(cycle_report_path),
        deterministic_report_path=str(deterministic_agent_report_path),
        replay_report_path=str(replay_agent_report_path),
        contracts=contracts,
    )
    return 0


def run_live_plan_cycle(args: argparse.Namespace) -> int:
    """Run deterministic first, then plan-only live adapter with deterministic apply."""

    scenario = scenario_from_args(args)

    cycle_id = args.run_id or run_id_from_now()
    parent_work_root = Path(args.work_root).resolve() if args.work_root else default_work_root()
    cycle_dir = parent_work_root / cycle_id
    cycle_dir.mkdir(parents=True, exist_ok=True)
    cycle_report_path = cycle_dir / "live_plan_cycle_report.json"
    deterministic_run_id = f"{cycle_id}-deterministic"
    live_plan_run_id = f"{cycle_id}-live-plan"

    emit_event(
        "live_plan_cycle_started",
        run_id=cycle_id,
        role="supervisor",
        agent_boundary="docker",
        deterministic_run_id=deterministic_run_id,
        live_plan_run_id=live_plan_run_id,
        cycle_dir=str(cycle_dir),
    )

    deterministic_args = namespace_with(
        args,
        exercise_replay=False,
        exercise_live_plan=False,
        agent="deterministic",
        run_id=deterministic_run_id,
        work_root=str(cycle_dir),
        replay_report="",
        compare_report="",
        live_plan_path="",
        live_plan_json="",
    )
    emit_event("live_plan_cycle_stage_started", run_id=cycle_id, stage="deterministic_baseline")
    deterministic_rc = run_supervisor(deterministic_args)
    deterministic_run_dir = cycle_dir / deterministic_run_id
    deterministic_agent_report_path = deterministic_run_dir / "report.json"
    deterministic_supervisor_report_path = deterministic_run_dir / "supervisor_report.json"

    if deterministic_rc != 0 or not deterministic_agent_report_path.exists():
        cycle_report = {
            "ok": False,
            "mode": MODE,
            "scenario": scenario.name,
            "cycle_id": cycle_id,
            "failed_stage": "deterministic_baseline",
            "deterministic_returncode": deterministic_rc,
            "deterministic_agent_report_path": str(deterministic_agent_report_path),
            "deterministic_supervisor_report_path": str(deterministic_supervisor_report_path),
            "live_plan_returncode": None,
            "contracts": {
                "deterministic_self_check_passed": False,
                "live_plan_self_check_passed": False,
                "report_contract_shape_matches_reference": False,
            },
        }
        atomic_write_json(cycle_report_path, cycle_report)
        emit_event(
            "live_plan_cycle_failed",
            run_id=cycle_id,
            failed_stage="deterministic_baseline",
            report_path=str(cycle_report_path),
            deterministic_returncode=deterministic_rc,
        )
        return 1

    live_plan_args = namespace_with(
        args,
        exercise_replay=False,
        exercise_live_plan=False,
        agent="live-plan",
        run_id=live_plan_run_id,
        work_root=str(cycle_dir),
        replay_report="",
        compare_report=str(deterministic_agent_report_path),
        live_plan_path="",
        live_plan_json=json.dumps(default_live_plan_payload(), sort_keys=True),
    )
    emit_event("live_plan_cycle_stage_started", run_id=cycle_id, stage="live_plan")
    live_plan_rc = run_supervisor(live_plan_args)
    live_plan_run_dir = cycle_dir / live_plan_run_id
    live_plan_agent_report_path = live_plan_run_dir / "report.json"
    live_plan_supervisor_report_path = live_plan_run_dir / "supervisor_report.json"

    deterministic_agent_report = load_report(deterministic_agent_report_path)
    live_plan_agent_report = load_report(live_plan_agent_report_path) if live_plan_agent_report_path.exists() else {}
    deterministic_supervisor_report = (
        load_report(deterministic_supervisor_report_path) if deterministic_supervisor_report_path.exists() else {}
    )
    live_plan_supervisor_report = (
        load_report(live_plan_supervisor_report_path) if live_plan_supervisor_report_path.exists() else {}
    )
    shape_comparison = compare_report_contract_shape(deterministic_agent_report, live_plan_agent_report)

    deterministic_contracts = (
        deterministic_supervisor_report.get("contracts", {})
        if isinstance(deterministic_supervisor_report.get("contracts"), dict)
        else {}
    )
    live_plan_contracts = (
        live_plan_supervisor_report.get("contracts", {})
        if isinstance(live_plan_supervisor_report.get("contracts"), dict)
        else {}
    )
    live_plan_adapter = (
        live_plan_agent_report.get("agent_adapter", {})
        if isinstance(live_plan_agent_report.get("agent_adapter"), dict)
        else {}
    )
    live_plan_edit_plan = (
        live_plan_agent_report.get("edit_plan", {}) if isinstance(live_plan_agent_report.get("edit_plan"), dict) else {}
    )
    contracts = {
        "deterministic_self_check_passed": deterministic_rc == 0 and bool(deterministic_supervisor_report.get("ok")),
        "live_plan_self_check_passed": live_plan_rc == 0 and bool(live_plan_supervisor_report.get("ok")),
        "deterministic_agent_containerized": bool(deterministic_contracts.get("agent_containerized")),
        "live_plan_agent_containerized": bool(live_plan_contracts.get("agent_containerized")),
        "live_plan_agent_mode": live_plan_agent_report.get("agent_mode") == "live-plan",
        "live_plan_planning_only": live_plan_adapter.get("planning_only") is True
        and live_plan_edit_plan.get("planning_only") is True,
        "deterministic_safe_apply_after_live_plan": live_plan_adapter.get("apply_mode") == "deterministic_safe_applier"
        and live_plan_edit_plan.get("apply_mode") == "deterministic_safe_applier",
        "live_plan_compared_against_deterministic": bool(live_plan_contracts.get("report_contract_shape_matches_reference")),
        "report_contract_shape_matches_reference": bool(shape_comparison.get("ok")),
        "deterministic_then_live_plan_order": True,
    }
    failed_contracts = sorted(name for name, ok in contracts.items() if not ok)
    cycle_report = {
        "ok": not failed_contracts,
        "mode": MODE,
        "scenario": scenario.name,
        "cycle_id": cycle_id,
        "run_dir": str(cycle_dir),
        "deterministic": {
            "run_id": deterministic_run_id,
            "returncode": deterministic_rc,
            "agent_report_path": str(deterministic_agent_report_path),
            "supervisor_report_path": str(deterministic_supervisor_report_path),
            "agent_report": deterministic_agent_report,
        },
        "live_plan": {
            "run_id": live_plan_run_id,
            "returncode": live_plan_rc,
            "agent_report_path": str(live_plan_agent_report_path),
            "supervisor_report_path": str(live_plan_supervisor_report_path),
            "agent_report": live_plan_agent_report,
        },
        "report_shape_comparison": shape_comparison,
        "contracts": contracts,
        "failed_contracts": failed_contracts,
    }
    atomic_write_json(cycle_report_path, cycle_report)
    if failed_contracts:
        emit_event(
            "live_plan_cycle_failed",
            run_id=cycle_id,
            failed_contracts=failed_contracts,
            report_path=str(cycle_report_path),
        )
        return 1
    emit_event(
        "live_plan_cycle_passed",
        run_id=cycle_id,
        report_path=str(cycle_report_path),
        deterministic_report_path=str(deterministic_agent_report_path),
        live_plan_report_path=str(live_plan_agent_report_path),
        contracts=contracts,
    )
    return 0

def run_generated_editor_cycle(args: argparse.Namespace) -> int:
    """Run deterministic first, then deterministic generated-editor sandbox apply."""

    scenario = scenario_from_args(args)

    cycle_id = args.run_id or run_id_from_now()
    parent_work_root = Path(args.work_root).resolve() if args.work_root else default_work_root()
    cycle_dir = parent_work_root / cycle_id
    cycle_dir.mkdir(parents=True, exist_ok=True)
    cycle_report_path = cycle_dir / "generated_editor_cycle_report.json"
    deterministic_run_id = f"{cycle_id}-deterministic"
    generated_editor_run_id = f"{cycle_id}-generated-editor"

    emit_event(
        "generated_editor_cycle_started",
        run_id=cycle_id,
        role="supervisor",
        agent_boundary="docker",
        deterministic_run_id=deterministic_run_id,
        generated_editor_run_id=generated_editor_run_id,
        cycle_dir=str(cycle_dir),
    )

    deterministic_args = namespace_with(
        args,
        exercise_replay=False,
        exercise_live_plan=False,
        exercise_generated_editor=False,
        agent="deterministic",
        run_id=deterministic_run_id,
        work_root=str(cycle_dir),
        replay_report="",
        compare_report="",
        live_plan_path="",
        live_plan_json="",
    )
    emit_event("generated_editor_cycle_stage_started", run_id=cycle_id, stage="deterministic_baseline")
    deterministic_rc = run_supervisor(deterministic_args)
    deterministic_run_dir = cycle_dir / deterministic_run_id
    deterministic_agent_report_path = deterministic_run_dir / "report.json"
    deterministic_supervisor_report_path = deterministic_run_dir / "supervisor_report.json"

    if deterministic_rc != 0 or not deterministic_agent_report_path.exists():
        cycle_report = {
            "ok": False,
            "mode": MODE,
            "scenario": scenario.name,
            "cycle_id": cycle_id,
            "failed_stage": "deterministic_baseline",
            "deterministic_returncode": deterministic_rc,
            "deterministic_agent_report_path": str(deterministic_agent_report_path),
            "deterministic_supervisor_report_path": str(deterministic_supervisor_report_path),
            "generated_editor_returncode": None,
            "contracts": {
                "deterministic_self_check_passed": False,
                "generated_editor_self_check_passed": False,
                "report_contract_shape_matches_reference": False,
            },
        }
        atomic_write_json(cycle_report_path, cycle_report)
        emit_event(
            "generated_editor_cycle_failed",
            run_id=cycle_id,
            failed_stage="deterministic_baseline",
            report_path=str(cycle_report_path),
            deterministic_returncode=deterministic_rc,
        )
        return 1

    generated_editor_args = namespace_with(
        args,
        exercise_replay=False,
        exercise_live_plan=False,
        exercise_generated_editor=False,
        agent="generated-editor",
        run_id=generated_editor_run_id,
        work_root=str(cycle_dir),
        replay_report="",
        compare_report=str(deterministic_agent_report_path),
        live_plan_path="",
        live_plan_json="",
    )
    emit_event("generated_editor_cycle_stage_started", run_id=cycle_id, stage="generated_editor")
    generated_editor_rc = run_supervisor(generated_editor_args)
    generated_editor_run_dir = cycle_dir / generated_editor_run_id
    generated_editor_agent_report_path = generated_editor_run_dir / "report.json"
    generated_editor_supervisor_report_path = generated_editor_run_dir / "supervisor_report.json"

    deterministic_agent_report = load_report(deterministic_agent_report_path)
    generated_editor_agent_report = (
        load_report(generated_editor_agent_report_path) if generated_editor_agent_report_path.exists() else {}
    )
    deterministic_supervisor_report = (
        load_report(deterministic_supervisor_report_path) if deterministic_supervisor_report_path.exists() else {}
    )
    generated_editor_supervisor_report = (
        load_report(generated_editor_supervisor_report_path) if generated_editor_supervisor_report_path.exists() else {}
    )
    shape_comparison = compare_report_contract_shape(
        deterministic_agent_report,
        generated_editor_agent_report,
        allow_generated_editor_extensions=True,
    )

    deterministic_contracts = (
        deterministic_supervisor_report.get("contracts", {})
        if isinstance(deterministic_supervisor_report.get("contracts"), dict)
        else {}
    )
    generated_editor_supervisor_contracts = (
        generated_editor_supervisor_report.get("contracts", {})
        if isinstance(generated_editor_supervisor_report.get("contracts"), dict)
        else {}
    )
    generated_editor_agent_contracts = (
        generated_editor_agent_report.get("contracts", {})
        if isinstance(generated_editor_agent_report.get("contracts"), dict)
        else {}
    )
    contracts = {
        "deterministic_self_check_passed": deterministic_rc == 0 and bool(deterministic_supervisor_report.get("ok")),
        "generated_editor_self_check_passed": generated_editor_rc == 0
        and bool(generated_editor_supervisor_report.get("ok")),
        "deterministic_agent_containerized": bool(deterministic_contracts.get("agent_containerized")),
        "generated_editor_agent_containerized": bool(generated_editor_supervisor_contracts.get("agent_containerized")),
        "generated_editor_agent_mode": generated_editor_agent_report.get("agent_mode") == "generated-editor",
        "generated_editor_present": bool(generated_editor_agent_contracts.get("generated_editor_present")),
        "generated_editor_static_preflight_passed": bool(
            generated_editor_agent_contracts.get("generated_editor_static_preflight_passed")
        ),
        "generated_editor_no_imports": bool(generated_editor_agent_contracts.get("generated_editor_no_imports")),
        "generated_editor_no_open_eval_exec_subprocess": bool(
            generated_editor_agent_contracts.get("generated_editor_no_open_eval_exec_subprocess")
        ),
        "generated_editor_sandbox_executed": bool(
            generated_editor_agent_contracts.get("generated_editor_sandbox_executed")
        ),
        "generated_editor_writes_only_allowed_paths": bool(
            generated_editor_agent_contracts.get("generated_editor_writes_only_allowed_paths")
        ),
        "generated_editor_output_matches_plan": bool(
            generated_editor_agent_contracts.get("generated_editor_output_matches_plan")
        ),
        "generated_editor_did_not_touch_forbidden_files": bool(
            generated_editor_agent_contracts.get("generated_editor_did_not_touch_forbidden_files")
        ),
        "generated_editor_worktree_unchanged_during_sandbox": bool(
            generated_editor_agent_contracts.get("generated_editor_worktree_unchanged_during_sandbox")
        ),
        "host_validated_sandbox_proposal": bool(
            generated_editor_agent_contracts.get("host_validated_sandbox_proposal")
        ),
        "host_applied_sandbox_proposal": bool(generated_editor_agent_contracts.get("host_applied_sandbox_proposal")),
        "deterministic_safe_apply_can_be_replaced_by_sandbox_apply": bool(
            generated_editor_agent_contracts.get("deterministic_safe_apply_can_be_replaced_by_sandbox_apply")
        ),
        "generated_editor_compared_against_deterministic": bool(
            generated_editor_supervisor_contracts.get("report_contract_shape_matches_reference")
        ),
        "report_contract_shape_matches_reference": bool(shape_comparison.get("ok")),
        "deterministic_then_generated_editor_order": True,
    }
    failed_contracts = sorted(name for name, ok in contracts.items() if not ok)
    cycle_report = {
        "ok": not failed_contracts,
        "mode": MODE,
        "scenario": scenario.name,
        "cycle_id": cycle_id,
        "run_dir": str(cycle_dir),
        "deterministic": {
            "run_id": deterministic_run_id,
            "returncode": deterministic_rc,
            "agent_report_path": str(deterministic_agent_report_path),
            "supervisor_report_path": str(deterministic_supervisor_report_path),
            "agent_report": deterministic_agent_report,
        },
        "generated_editor": {
            "run_id": generated_editor_run_id,
            "returncode": generated_editor_rc,
            "agent_report_path": str(generated_editor_agent_report_path),
            "supervisor_report_path": str(generated_editor_supervisor_report_path),
            "agent_report": generated_editor_agent_report,
        },
        "report_shape_comparison": shape_comparison,
        "contracts": contracts,
        "failed_contracts": failed_contracts,
    }
    atomic_write_json(cycle_report_path, cycle_report)
    if failed_contracts:
        emit_event(
            "generated_editor_cycle_failed",
            run_id=cycle_id,
            failed_contracts=failed_contracts,
            report_path=str(cycle_report_path),
        )
        return 1
    emit_event(
        "generated_editor_cycle_passed",
        run_id=cycle_id,
        report_path=str(cycle_report_path),
        deterministic_report_path=str(deterministic_agent_report_path),
        generated_editor_report_path=str(generated_editor_agent_report_path),
        contracts=contracts,
    )
    return 0







def run_scenario_matrix(args: argparse.Namespace) -> int:
    """Run a small deterministic scenario matrix through the Docker-contained harness."""

    matrix_id = args.run_id or run_id_from_now()
    parent_work_root = Path(args.work_root).resolve() if args.work_root else default_work_root()
    matrix_dir = parent_work_root / matrix_id
    matrix_dir.mkdir(parents=True, exist_ok=True)
    matrix_report_path = matrix_dir / "scenario_matrix_report.json"

    emit_event(
        "scenario_matrix_started",
        run_id=matrix_id,
        role="supervisor",
        agent_boundary="docker",
        scenarios=list(SCENARIO_MATRIX),
        matrix_dir=str(matrix_dir),
    )

    scenario_results: dict[str, Any] = {}
    failed_contracts: list[str] = []
    for scenario_name in SCENARIO_MATRIX:
        scenario = scenario_spec(scenario_name)
        scenario_run_id = f"{matrix_id}-{scenario_name}"
        scenario_args = namespace_with(
            args,
            exercise_replay=False,
            exercise_live_plan=False,
            exercise_generated_editor=False,
            exercise_scenario_matrix=False,
            agent="deterministic",
            scenario=scenario_name,
            run_id=scenario_run_id,
            work_root=str(matrix_dir),
            replay_report="",
            compare_report="",
            live_plan_path="",
            live_plan_json="",
            commit_policy=DEFAULT_COMMIT_POLICY,
        )
        emit_event(
            "scenario_matrix_stage_started",
            run_id=matrix_id,
            scenario=scenario_name,
            scenario_run_id=scenario_run_id,
            expected_contracts=scenario.contracts(),
        )
        returncode = run_supervisor(scenario_args)
        scenario_run_dir = matrix_dir / scenario_run_id
        agent_report_path = scenario_run_dir / "report.json"
        supervisor_report_path = scenario_run_dir / "supervisor_report.json"
        agent_report = load_report(agent_report_path) if agent_report_path.exists() else {}
        supervisor_report = load_report(supervisor_report_path) if supervisor_report_path.exists() else {}
        agent_contracts = agent_report.get("contracts", {}) if isinstance(agent_report.get("contracts"), dict) else {}
        commit_report = agent_report.get("commit", {}) if isinstance(agent_report.get("commit"), dict) else {}
        verification_report = agent_report.get("verification", {}) if isinstance(agent_report.get("verification"), dict) else {}

        contracts = {
            "self_check_passed": returncode == 0 and bool(supervisor_report.get("ok")),
            "scenario_name_recorded": agent_report.get("scenario") == scenario_name,
            "scenario_contracts_recorded": agent_report.get("scenario_contracts") == scenario.contracts(),
            "scenario_contracts_satisfied": bool(agent_contracts.get("scenario_contracts_satisfied")),
            "expected_changed_files": agent_report.get("changed_files") == list(scenario.expected_changed_files),
            "forbidden_files_unchanged": bool(agent_contracts.get("forbidden_files_unchanged")),
            "verification_outcome_expected": bool(agent_contracts.get("verification_outcome_expected")),
            "commit_expectation_matched": bool(commit_report.get("expected")) == scenario.requires_commit,
        }
        if scenario.expects_verification_success:
            contracts["verification_passed"] = bool(verification_report.get("ok")) and bool(
                agent_contracts.get("verification_passed")
            )
            contracts["commit_created"] = bool(commit_report.get("created")) == scenario.requires_commit
        else:
            contracts["verification_failed_as_expected"] = not bool(verification_report.get("ok")) and bool(
                agent_contracts.get("verification_failed_as_expected")
            )
            contracts["verification_failure_blocks_commit"] = bool(agent_contracts.get("verification_failure_blocks_commit"))
            contracts["commit_not_created_after_verification_failure"] = (
                not bool(commit_report.get("created"))
                and agent_report.get("final_head") == agent_report.get("base_head")
            )

        scenario_failed = sorted(name for name, ok in contracts.items() if not ok)
        if scenario_failed:
            failed_contracts.extend(f"{scenario_name}.{name}" for name in scenario_failed)
        scenario_results[scenario_name] = {
            "run_id": scenario_run_id,
            "returncode": returncode,
            "agent_report_path": str(agent_report_path),
            "supervisor_report_path": str(supervisor_report_path),
            "scenario_contracts": scenario.contracts(),
            "contracts": contracts,
            "failed_contracts": scenario_failed,
            "agent_report": agent_report,
        }

    matrix_contracts = {
        "scenario_matrix_all_passed": not failed_contracts,
        "scenario_matrix_scenarios_exercised": sorted(scenario_results) == sorted(SCENARIO_MATRIX),
        "single_file_python_edit_exercised": "single_file_python_edit" in scenario_results,
        "forbidden_file_instruction_exercised": "forbidden_file_instruction" in scenario_results,
        "verification_failure_blocks_commit_exercised": "verification_failure_blocks_commit" in scenario_results,
    }
    if not matrix_contracts["scenario_matrix_all_passed"]:
        failed_contracts.append("scenario_matrix_all_passed")
    for name, ok in matrix_contracts.items():
        if not ok and name not in failed_contracts:
            failed_contracts.append(name)

    matrix_report = {
        "ok": not failed_contracts,
        "mode": MODE,
        "matrix_id": matrix_id,
        "run_dir": str(matrix_dir),
        "scenarios": list(SCENARIO_MATRIX),
        "scenario_results": scenario_results,
        "contracts": matrix_contracts,
        "failed_contracts": sorted(failed_contracts),
    }
    atomic_write_json(matrix_report_path, matrix_report)
    if failed_contracts:
        emit_event(
            "scenario_matrix_failed",
            run_id=matrix_id,
            failed_contracts=sorted(failed_contracts),
            report_path=str(matrix_report_path),
        )
        return 1

    emit_event(
        "scenario_matrix_passed",
        run_id=matrix_id,
        report_path=str(matrix_report_path),
        contracts=matrix_contracts,
    )
    return 0



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic code-editing agent guidance smoke.")
    parser.add_argument("--role", choices=["supervisor", "agent"], default="supervisor")
    parser.add_argument("--agent", choices=["deterministic", "replay", "live-plan", "generated-editor"], default="deterministic")
    parser.add_argument("--scenario", choices=tuple(SCENARIO_SPECS), default=DEFAULT_SCENARIO)
    parser.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    parser.add_argument("--replay-report", default="", help="Prior agent report to replay through the same harness.")
    parser.add_argument("--live-plan-path", default="", help="JSON live-planning artifact for --agent live-plan.")
    parser.add_argument("--live-plan-json", default="", help="Inline JSON live-planning artifact for --agent live-plan.")
    parser.add_argument("--compare-report", default="", help="Prior report whose contract shape should match this run.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--work-root", default="", help="Supervisor work root. Defaults to a short temp directory.")
    parser.add_argument("--run-dir", default="", help="Agent run directory.")
    parser.add_argument("--commands-path", default="", help="Agent guidance JSONL path.")
    parser.add_argument("--report-path", default="", help="Agent report JSON path.")
    parser.add_argument("--target-branch", default=DEFAULT_TARGET_BRANCH)
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--guidance-text", default=DEFAULT_GUIDANCE_TEXT)
    parser.add_argument("--guidance-window-seconds", type=float, default=DEFAULT_GUIDANCE_WINDOW_SECONDS)
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--commit-policy",
        choices=COMMIT_POLICIES,
        default=DEFAULT_COMMIT_POLICY,
        help="Commit behavior after verification: auto, require approve_commit, or never commit.",
    )
    parser.add_argument(
        "--approval-timeout-seconds",
        type=float,
        default=DEFAULT_APPROVAL_TIMEOUT_SECONDS,
        help="How long an agent waits for approve_commit/reject_commit when --commit-policy require-approval.",
    )
    parser.add_argument(
        "--exercise-replay",
        action="store_true",
        help="Run deterministic first, then replay its report through the same Docker-contained harness.",
    )
    parser.add_argument(
        "--exercise-live-plan",
        action="store_true",
        help="Run deterministic first, then live-plan with deterministic apply through the same Docker-contained harness.",
    )
    parser.add_argument(
        "--exercise-generated-editor",
        action="store_true",
        help="Run deterministic first, then generated-editor sandbox proposal with host apply.",
    )
    parser.add_argument(
        "--exercise-scenario-matrix",
        action="store_true",
        help="Run the first deterministic fixture scenario matrix through the same Docker-contained harness.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.role == "agent":
        if not args.run_id:
            args.run_id = run_id_from_now()
        if not args.run_dir:
            args.run_dir = str(default_work_root() / args.run_id)
        return run_agent(args)
    if args.exercise_scenario_matrix:
        return run_scenario_matrix(args)
    if args.exercise_replay:
        return run_replay_cycle(args)
    if args.exercise_live_plan:
        return run_live_plan_cycle(args)
    if args.exercise_generated_editor:
        return run_generated_editor_cycle(args)
    return run_supervisor(args)


if __name__ == "__main__":
    raise SystemExit(main())
