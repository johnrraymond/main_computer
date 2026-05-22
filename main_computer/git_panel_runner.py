from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any


READ_ONLY_GIT_COMMANDS = {
    "rev-parse",
    "status",
    "diff",
    "log",
    "show",
    "show-ref",
    "for-each-ref",
    "remote",
    "branch",
    "ls-files",
    "config",
    "describe",
}
MUTATING_GIT_COMMANDS = {
    "add",
    "am",
    "apply",
    "bisect",
    "branch",
    "checkout",
    "clean",
    "commit",
    "merge",
    "mv",
    "pull",
    "push",
    "rebase",
    "reset",
    "restore",
    "rm",
    "stash",
    "submodule",
    "switch",
    "tag",
    "worktree",
}


def load_payload(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def split_command(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=os.name != "nt")
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


def is_branch_read_only(args: list[str]) -> bool:
    disallowed = {"-d", "-D", "-m", "-M", "--delete", "--move", "--set-upstream-to", "--unset-upstream"}
    return not any(token in disallowed for token in args[1:])


def is_config_read_only(args: list[str]) -> bool:
    allowed = {"--get", "--get-all", "--get-regexp", "--list", "-l", "--show-origin", "--show-scope"}
    return all(token.startswith("-") and token in allowed for token in args[1:] if token.startswith("-"))


def validate_git_command(argv: list[str], repo: Path, state: dict[str, Any]) -> tuple[list[str], Path, bool]:
    if not argv or Path(argv[0].replace("\\", "/")).name.lower() != "git":
        raise ValueError("Expected a git command")
    git_args, effective_repo = strip_git_global_options(argv[1:], repo)
    if not effective_repo.exists():
        raise ValueError(f"Repository path does not exist: {effective_repo}")
    subcommand = git_subcommand(git_args)
    if not subcommand:
        raise ValueError("Git command is missing a subcommand")
    mutating = subcommand in MUTATING_GIT_COMMANDS
    if subcommand == "branch" and is_branch_read_only(git_args):
        mutating = False
    if subcommand == "config" and is_config_read_only(git_args):
        mutating = False
    if subcommand not in READ_ONLY_GIT_COMMANDS and not mutating:
        raise ValueError(f"Git subcommand is not on the safe allow-list: {subcommand}")
    if mutating and not state.get("allow_mutating_actions"):
        raise ValueError(f"Refusing mutating git command while allow_mutating_actions is false: git {subcommand}")
    return ["git", "-C", str(effective_repo), *git_args], effective_repo, mutating


def validate_python_command(argv: list[str], repo: Path, app_root: Path, state: dict[str, Any]) -> tuple[list[str], Path, bool]:
    exe_name = Path(argv[0].replace("\\", "/")).name.lower() if argv else ""
    if exe_name not in {"python", "python3", "py", "python.exe"} and not exe_name.endswith("python.exe"):
        raise ValueError("Unsupported Python executable")
    if len(argv) < 2:
        raise ValueError("Python command is missing a script")
    script_name = Path(argv[1].replace("\\", "/")).name.lower()
    if script_name == "git_dirty.py":
        if len(argv) < 3 or argv[2] != "plan":
            raise ValueError("Only `git_dirty.py plan` is allowed from panel runners")
        script = app_root / "git_dirty.py"
        if not script.exists():
            raise ValueError(f"git_dirty.py was not found at {script}")
        return [sys.executable, str(script), *argv[2:]], app_root, False
    if script_name == "git-control.py":
        if not state.get("allow_python_git_control"):
            raise ValueError("Refusing git-control.py while allow_python_git_control is false")
        script = app_root / "git-control.py"
        if not script.exists():
            raise ValueError(f"git-control.py was not found at {script}")
        return [sys.executable, str(script), *argv[2:]], app_root, True
    raise ValueError(f"Unsupported Python runner script: {script_name}")


def inspect_repo(repo: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--show-toplevel", "--git-dir", "--git-common-dir", "--is-inside-work-tree"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=30,
    )
    lines = (result.stdout or "").splitlines()
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "top_level": lines[0] if len(lines) > 0 else "",
        "git_dir": lines[1] if len(lines) > 1 else "",
        "git_common_dir": lines[2] if len(lines) > 2 else "",
        "inside_work_tree": lines[3] if len(lines) > 3 else "",
    }


def run_command(argv: list[str], cwd: Path, *, timeout: int = 120) -> dict[str, Any]:
    started = time.time()
    started_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))
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
    return {
        "command": argv,
        "cwd": str(cwd),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "started_at": started_iso,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed": round(time.time() - started, 3),
    }


def main() -> int:
    payload = load_payload(sys.argv[1])
    state = payload.get("state") or {}
    commands = payload.get("commands") or []
    app_root = Path(payload.get("app_root") or ".").resolve()
    repo = resolve_path(state.get("repo") or payload.get("repo_dir") or ".", base=app_root)
    output: dict[str, Any] = {
        "ok": True,
        "mode": "safe-python-panel-runner",
        "runner_version": "MC_GIT_PANEL_RUNNER_V1",
        "repo": str(repo),
        "app_root": str(app_root),
        "state": state,
        "preflight": {},
        "commands": [],
        "logs": [],
    }

    started = time.time()

    def log(message: str, data: dict[str, Any] | None = None) -> None:
        output["logs"].append({"elapsed": round(time.time() - started, 3), "message": message, "data": data or {}})

    try:
        output["preflight"] = inspect_repo(repo)
        log("Repository preflight completed.", output["preflight"])
        if not output["preflight"].get("ok"):
            output["ok"] = False
            output["error"] = "Repository preflight failed."
            print(json.dumps(output, indent=2))
            return 2

        for index, command in enumerate(commands, start=1):
            argv = split_command(command)
            exe = Path(argv[0].replace("\\", "/")).name.lower() if argv else ""
            if exe == "git":
                safe_argv, cwd, mutating = validate_git_command(argv, repo, state)
            else:
                safe_argv, cwd, mutating = validate_python_command(argv, repo, app_root, state)
            log("Command validated.", {"index": index, "command": command, "mutating": mutating, "argv": safe_argv})
            result = run_command(safe_argv, cwd)
            result.update({"input_command": command, "mutating": mutating})
            output["commands"].append(result)
            log("Command finished.", {"index": index, "returncode": result["returncode"]})
            if result["returncode"] != 0:
                output["ok"] = False
                output["error"] = f"Command {index} failed with return code {result['returncode']}."
                break
    except subprocess.TimeoutExpired as exc:
        output["ok"] = False
        output["error"] = f"Command timed out: {exc}"
    except Exception as exc:
        output["ok"] = False
        output["error"] = str(exc)

    output["elapsed"] = round(time.time() - started, 3)
    print(json.dumps(output, indent=2))
    return 0 if output.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
