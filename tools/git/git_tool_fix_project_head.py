#!/usr/bin/env python3
"""Disk-backed, allow-listed runner for first-commit and unborn-HEAD fixes.

The browser never executes shell text directly. It sends a payload containing
planner-generated command lines; this runner validates each line, normalizes the
repository path, and then executes only the small Git/Python command vocabulary
needed for repository initialization, .gitignore review, and the first commit.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from typing import Any


READ_ONLY_GIT = {
    "branch",
    "check-ignore",
    "config",
    "diff",
    "ls-files",
    "remote",
    "rev-parse",
    "show",
    "status",
    "symbolic-ref",
}
HEAD_FIX_GIT = {
    "add",
    "commit",
    "init",
}
PYTHON_SCRIPTS = {"git_dirty.py"}
MAX_CAPTURED_OUTPUT_CHARS = 12000


def load_payload(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def split_command(command: str) -> list[str]:
    try:
        return shlex.split(str(command or ""), posix=os.name != "nt")
    except ValueError as exc:
        raise ValueError(f"Cannot parse command safely: {exc}") from exc


def clean_path_token(value: str) -> str:
    cleaned = str(value or ".").strip()
    for _ in range(4):
        previous = cleaned
        cleaned = cleaned.strip().replace('\\"', '"')
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
            cleaned = cleaned[1:-1].strip()
        if cleaned == previous:
            break
    return cleaned or "."


def resolve_path(value: str, *, base: Path) -> Path:
    candidate = Path(clean_path_token(value))
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def strip_git_global_options(args: list[str], repo: Path) -> tuple[list[str], Path]:
    remaining = list(args)
    effective_repo = repo
    out: list[str] = []
    i = 0
    while i < len(remaining):
        token = remaining[i]
        if token == "-C":
            if i + 1 >= len(remaining):
                raise ValueError("git -C requires a path")
            effective_repo = resolve_path(remaining[i + 1], base=repo)
            i += 2
            continue
        if token.startswith("-C") and len(token) > 2:
            effective_repo = resolve_path(token[2:], base=repo)
            i += 1
            continue
        out = remaining[i:]
        break
    return out, effective_repo


def git_subcommand(args: list[str]) -> str:
    for token in args:
        if token.startswith("-"):
            continue
        return token.lower()
    return ""


def action_tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[:/\s]+", str(value or "")) if token}


def allows_mutating_git(action_key: str, subcommand: str) -> bool:
    tokens = action_tokens(action_key)
    if subcommand == "init":
        return bool(tokens & {"initialize_repository_here", "start_tracking_this_folder"})
    if subcommand == "add":
        return bool(tokens & {
            "create_initial_snapshot",
            "initial-snapshot-required",
            "start_tracking_real_work",
            "track_selected_files",
            "track_all_safe_source_files",
        })
    if subcommand == "commit":
        return bool(tokens & {
            "create_initial_snapshot",
            "initial-snapshot-required",
            "record_current_work_as_commit",
        })
    return False


def allowed_failure(args: list[str], returncode: int) -> bool:
    if returncode == 0:
        return False
    subcommand = git_subcommand(args)
    if subcommand == "check-ignore":
        return True
    if subcommand == "config" and "--get" in args:
        return True
    if subcommand == "rev-parse" and "--verify" in args:
        return True
    if subcommand == "symbolic-ref":
        return True
    return False


def python_plan_refresh_command(argv: list[str]) -> bool:
    if len(argv) < 3:
        return False
    script_name = Path(str(argv[1]).replace("\\", "/")).name.lower()
    return script_name == "git_dirty.py" and argv[2] == "plan"


def allowed_python_refresh_failure(argv: list[str], returncode: int, *, mutating_success_count: int) -> bool:
    """Return True when a post-action UI refresh failed after the HEAD fix succeeded.

    Planner payloads may include a final `git_dirty.py plan --json` command so the UI can
    refresh after a first-commit operation. That refresh can be large and environment-sensitive;
    it should be reported as a warning, not treated as failure of the already-completed HEAD fix.
    """

    return returncode != 0 and mutating_success_count > 0 and python_plan_refresh_command(argv)


def truncate_output(value: str, *, limit: int = MAX_CAPTURED_OUTPUT_CHARS) -> str:
    text = value or ""
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n... <truncated {omitted} chars> ...\n{text[-tail:]}"


def inspect_head(repo: Path) -> dict[str, Any]:
    verify = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "-q", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=30,
    )
    symbolic = subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=30,
    )
    return {
        "has_head": verify.returncode == 0,
        "head_state": "present" if verify.returncode == 0 else ("unborn" if symbolic.stdout.strip() else "missing"),
        "branch_name": symbolic.stdout.strip(),
        "head_oid": verify.stdout.strip(),
        "raw": {
            "verify_returncode": verify.returncode,
            "verify_stderr": verify.stderr,
            "symbolic_returncode": symbolic.returncode,
            "symbolic_stderr": symbolic.stderr,
        },
    }


def inspect_repo(repo: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--show-toplevel", "--git-dir", "--git-common-dir", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=30,
    )
    lines = (result.stdout or "").splitlines()
    info = {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "top_level": lines[0] if len(lines) > 0 else "",
        "git_dir": lines[1] if len(lines) > 1 else "",
        "git_common_dir": lines[2] if len(lines) > 2 else "",
        "inside_work_tree": lines[3] if len(lines) > 3 else "",
    }
    if info["ok"]:
        info.update(inspect_head(repo))
    else:
        info.update({"has_head": False, "head_state": "not_initialized"})
    return info


def staged_changes_exist(repo: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--quiet", "--exit-code"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=30,
    )
    return result.returncode == 1


def validate_git_command(argv: list[str], repo: Path, action_key: str) -> tuple[list[str], Path, bool]:
    if not argv or Path(argv[0].replace("\\", "/")).name.lower() != "git":
        raise ValueError("Expected a git command")
    git_args, effective_repo = strip_git_global_options(argv[1:], repo)
    if not effective_repo.exists():
        raise ValueError(f"Repository path does not exist: {effective_repo}")
    subcommand = git_subcommand(git_args)
    if not subcommand:
        raise ValueError("Git command is missing a subcommand")
    mutating = subcommand in HEAD_FIX_GIT
    if subcommand not in READ_ONLY_GIT and subcommand not in HEAD_FIX_GIT:
        raise ValueError(f"Git subcommand is not allowed for HEAD repair: {subcommand}")
    if mutating and not allows_mutating_git(action_key, subcommand):
        raise ValueError(f"Refusing git {subcommand} for action {action_key or '<unknown>'}")
    if subcommand == "commit":
        if "-m" not in git_args and "--message" not in git_args:
            raise ValueError("First-commit command must include an explicit message")
        if not staged_changes_exist(effective_repo):
            raise ValueError("Refusing to commit: no staged changes are present after the review/stage commands")
    return ["git", "-C", str(effective_repo), *git_args], effective_repo, mutating


def validate_python_command(argv: list[str], repo: Path, app_root: Path) -> tuple[list[str], Path, bool]:
    exe_name = Path(argv[0].replace("\\", "/")).name.lower() if argv else ""
    if exe_name not in {"python", "python3", "py", "python.exe"} and not exe_name.endswith("python.exe"):
        raise ValueError("Unsupported Python executable")
    if len(argv) < 2:
        raise ValueError("Python command is missing a script")
    script_name = Path(argv[1].replace("\\", "/")).name.lower()
    if script_name not in PYTHON_SCRIPTS:
        raise ValueError(f"Unsupported Python runner script: {script_name}")
    if len(argv) < 3 or argv[2] != "plan":
        raise ValueError("Only `git_dirty.py plan` is allowed from the HEAD-fix runner")
    script = app_root / "git_dirty.py"
    if not script.exists():
        raise ValueError(f"git_dirty.py was not found at {script}")
    return [sys.executable, str(script), *argv[2:]], app_root, False


def run_command(argv: list[str], cwd: Path, *, timeout: int = 180) -> dict[str, Any]:
    started = time.time()
    result = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=timeout,
    )
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    return {
        "argv": argv,
        "cwd": str(cwd),
        "returncode": result.returncode,
        "stdout": truncate_output(stdout),
        "stderr": truncate_output(stderr),
        "stdout_chars": len(stdout),
        "stderr_chars": len(stderr),
        "stdout_truncated": len(stdout) > MAX_CAPTURED_OUTPUT_CHARS,
        "stderr_truncated": len(stderr) > MAX_CAPTURED_OUTPUT_CHARS,
        "elapsed": round(time.time() - started, 3),
    }


def main() -> int:
    payload = load_payload(sys.argv[1])
    state = payload.get("state") or {}
    commands = payload.get("commands") or []
    app_root = Path(payload.get("app_root") or ".").resolve()
    repo = resolve_path(state.get("repo") or payload.get("repo_dir") or ".", base=app_root)
    action_key = str(payload.get("action_key") or state.get("action_id") or "")

    output: dict[str, Any] = {
        "ok": True,
        "mode": "git-tool-fix-project-head",
        "runner_version": "MC_GIT_HEAD_FIX_RUNNER_V1",
        "action_key": action_key,
        "repo": str(repo),
        "app_root": str(app_root),
        "preflight": inspect_repo(repo),
        "commands": [],
        "logs": [],
    }

    started = time.time()

    def log(message: str, data: dict[str, Any] | None = None) -> None:
        output["logs"].append({"elapsed": round(time.time() - started, 3), "message": message, "data": data or {}})

    mutating_success_count = 0

    try:
        tokens = action_tokens(action_key)
        if "create_initial_snapshot" in tokens and output["preflight"].get("has_head"):
            raise ValueError("Refusing to create an initial snapshot because HEAD already exists")
        if "update_gitignore_before_initial_commit" in tokens and output["preflight"].get("has_head"):
            raise ValueError("Refusing initial .gitignore cleanup action because HEAD already exists; use normal dirty cleanup planning instead")
        if not isinstance(commands, list) or not commands:
            raise ValueError("HEAD-fix payload did not include runnable command lines")

        for index, command in enumerate(commands, start=1):
            argv = split_command(command)
            exe = Path(argv[0].replace("\\", "/")).name.lower() if argv else ""
            if exe == "git":
                safe_argv, cwd, mutating = validate_git_command(argv, repo, action_key)
                git_args = safe_argv[3:]
            else:
                safe_argv, cwd, mutating = validate_python_command(argv, repo, app_root)
                git_args = []
            log("Command validated.", {"index": index, "mutating": mutating, "argv": safe_argv})
            result = run_command(safe_argv, cwd)
            result.update({"input_command": command, "mutating": mutating})
            if exe == "git" and allowed_failure(git_args, int(result["returncode"])):
                result["allowed_failure"] = True
                log("Command returned a tolerated diagnostic non-zero status.", {"index": index, "returncode": result["returncode"]})
            elif exe != "git" and allowed_python_refresh_failure(
                safe_argv,
                int(result["returncode"]),
                mutating_success_count=mutating_success_count,
            ):
                result["allowed_failure"] = True
                result["refresh_warning"] = True
                output.setdefault("warnings", []).append(
                    f"Command {index} post-action git_dirty.py plan refresh failed with return code {result['returncode']}; HEAD-fix verification uses postflight Git state."
                )
                log("Post-action refresh command failed but did not fail the completed HEAD repair.", {"index": index, "returncode": result["returncode"]})
            output["commands"].append(result)
            if result["returncode"] != 0 and not result.get("allowed_failure"):
                output["ok"] = False
                output["error"] = f"Command {index} failed with return code {result['returncode']}."
                break
            if mutating and int(result["returncode"]) == 0:
                mutating_success_count += 1
            log("Command finished.", {"index": index, "returncode": result["returncode"]})
    except subprocess.TimeoutExpired as exc:
        output["ok"] = False
        output["error"] = f"Command timed out: {exc}"
    except Exception as exc:
        output["ok"] = False
        output["error"] = str(exc)

    output["postflight"] = inspect_repo(repo) if repo.exists() else {"ok": False, "error": "repo path missing"}
    if output.get("ok") and "create_initial_snapshot" in action_tokens(action_key) and not output["postflight"].get("has_head"):
        output["ok"] = False
        output["error"] = "Initial snapshot commands completed without creating HEAD."
    output["elapsed"] = round(time.time() - started, 3)
    print(json.dumps(output, indent=2))
    return 0 if output.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
