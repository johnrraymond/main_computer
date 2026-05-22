#!/usr/bin/env python3
"""
Smoke: execute read-only Git preflight inside a WSL-home website repo.

This is phase two after rag_wsl_website_git_command_scope_smoke.py.  The phase
one smoke proves that Git commands are planned through the WSL command boundary
and scoped to /home/main-computer/websites.  This smoke actually executes that
boundary against a disposable website fixture repo inside the WSL-home websites
root.

The smoke deliberately does not touch a real website repo.  It creates a
temporary site below:

    /home/main-computer/websites/__rag_git_exec_smoke_<token>

Then it runs the read-only Git preflight commands through:

    wsl.exe --distribution <distro> --cd <fixture> --exec git ...

The temporary fixture needs one seed commit so rev-parse HEAD can prove commit
detection.  That seed commit is confined to the disposable fixture.  The smoke
never applies a patch, never commits a user website, and never calls host Git.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import posixpath
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MODE = "rag_wsl_website_git_execution_smoke"
REQUEST = "Execute read-only Git preflight for a disposable WSL website fixture."
DEFAULT_WSL_COMMAND = os.environ.get("RAG_WSL_COMMAND", "wsl.exe")
DEFAULT_WSL_DISTRIBUTION = os.environ.get("RAG_WSL_DISTRIBUTION", "MainComputerExecutorTest")
DEFAULT_WSL_HOME = os.environ.get("RAG_WSL_HOME", "/home/main-computer")
DEFAULT_WSL_WEBSITES_ROOT = os.environ.get("RAG_WSL_WEBSITES_ROOT", f"{DEFAULT_WSL_HOME}/websites")
DEFAULT_LOCKED_HUB_ROOT = os.environ.get("RAG_WSL_LOCKED_HUB_ROOT", f"{DEFAULT_WSL_HOME}/install/hub")


def repo_relative_website_path(site_id: str) -> Path:
    return Path("runtime") / "websites" / site_id


def script_derived_host_website_path(site_id: str) -> Path:
    return Path(__file__).resolve().parents[1] / repo_relative_website_path(site_id)


def host_mount_negative_target(site_id: str) -> str:
    host_path = script_derived_host_website_path(site_id).resolve()
    normalized = str(host_path).replace("\\", "/")
    if len(normalized) >= 3 and normalized[1:3] == ":/":
        return f"/mnt/{normalized[0].lower()}/{normalized[3:]}"
    relative = repo_relative_website_path(site_id).as_posix()
    return f"/mnt/c/main-computer-fixtures/{Path(__file__).resolve().parents[1].name}/{relative}"


def windows_path_negative_target(site_id: str) -> str:
    host_path = script_derived_host_website_path(site_id).resolve()
    normalized = str(host_path).replace("\\", "/")
    if len(normalized) >= 3 and normalized[1:3] == ":/":
        return f"{normalized[0].upper()}:\\\\" + normalized[3:].replace("/", "\\")
    relative = str(repo_relative_website_path(site_id)).replace("/", "\\")
    return f"C:\\main-computer-fixtures\\{Path(__file__).resolve().parents[1].name}\\{relative}"


@dataclass(frozen=True)
class TargetResolution:
    ok: bool
    input_path: str
    wsl_path: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def text_tail(text: str, max_chars: int = 1600) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def normalize_absolute_wsl_path(value: str) -> str:
    normalized = posixpath.normpath(value.replace("\\", "/"))
    if normalized == ".":
        normalized = ""
    if not normalized.startswith("/"):
        raise ValueError("expected absolute WSL path")
    return normalized


def is_inside_or_equal(path: str, root: str) -> bool:
    root_clean = normalize_absolute_wsl_path(root)
    path_clean = normalize_absolute_wsl_path(path)
    return path_clean == root_clean or path_clean.startswith(root_clean.rstrip("/") + "/")


def is_windows_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return bool(re.match(r"^[A-Za-z]:/", normalized)) or value.startswith("\\\\")


def contains_parent_traversal(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return any(part == ".." for part in normalized.split("/"))


def is_host_mount_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return normalized == "/mnt" or normalized.startswith("/mnt/")


def is_allowed_site_id(site_id: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,78}[a-z0-9]", site_id or ""))


def resolve_website_target(
    value: str,
    *,
    wsl_websites_root: str,
    locked_hub_root: str,
) -> TargetResolution:
    raw = str(value or "").strip()
    if not raw:
        return TargetResolution(ok=False, input_path=raw, reason="empty_target")
    if "\x00" in raw:
        return TargetResolution(ok=False, input_path=raw, reason="nul_rejected")
    if is_windows_path(raw):
        return TargetResolution(ok=False, input_path=raw, reason="windows_path_rejected")
    if contains_parent_traversal(raw):
        return TargetResolution(ok=False, input_path=raw, reason="parent_traversal_rejected")
    if is_host_mount_path(raw):
        return TargetResolution(ok=False, input_path=raw, reason="host_mount_rejected")

    if raw.startswith("/"):
        try:
            wsl_path = normalize_absolute_wsl_path(raw)
        except ValueError:
            return TargetResolution(ok=False, input_path=raw, reason="invalid_wsl_path")
        if wsl_path == locked_hub_root or is_inside_or_equal(wsl_path, locked_hub_root):
            return TargetResolution(ok=False, input_path=raw, reason="hub_install_locked")
        if not is_inside_or_equal(wsl_path, wsl_websites_root):
            return TargetResolution(ok=False, input_path=raw, reason="outside_websites_root")
        rel = wsl_path.removeprefix(wsl_websites_root.rstrip("/") + "/")
        site_id = rel.split("/", 1)[0]
        if not is_allowed_site_id(site_id):
            return TargetResolution(ok=False, input_path=raw, reason="invalid_site_id")
        return TargetResolution(ok=True, input_path=raw, wsl_path=wsl_path)

    normalized = raw.replace("\\", "/").strip("/")
    prefixes = ("runtime/websites/", "websites/")
    site_id: str | None = None
    for prefix in prefixes:
        if normalized.startswith(prefix):
            candidate = normalized[len(prefix) :]
            if "/" not in candidate and candidate:
                site_id = candidate
            break
    if site_id is None and "/" not in normalized and normalized:
        site_id = normalized
    if not site_id:
        return TargetResolution(ok=False, input_path=raw, reason="unsupported_relative_target")
    if not is_allowed_site_id(site_id):
        return TargetResolution(ok=False, input_path=raw, reason="invalid_site_id")

    return TargetResolution(
        ok=True,
        input_path=raw,
        wsl_path=f"{wsl_websites_root.rstrip('/')}/{site_id}",
    )


def build_wsl_exec_command(
    *,
    wsl_command: str,
    distribution: str,
    wsl_cwd: str,
    executable_and_args: list[str],
) -> list[str]:
    if not executable_and_args:
        raise ValueError("missing executable")
    cwd = normalize_absolute_wsl_path(wsl_cwd)
    return [
        wsl_command,
        "--distribution",
        distribution,
        "--cd",
        cwd,
        "--exec",
        *executable_and_args,
    ]


def build_wsl_git_command(
    *,
    target: str,
    git_args: list[str],
    wsl_command: str,
    distribution: str,
    wsl_websites_root: str,
    locked_hub_root: str,
) -> list[str]:
    resolution = resolve_website_target(
        target,
        wsl_websites_root=wsl_websites_root,
        locked_hub_root=locked_hub_root,
    )
    if not resolution.ok or not resolution.wsl_path:
        raise ValueError(f"Unsafe website Git target: {resolution.reason or 'unknown'}")
    if not git_args or git_args[0] == "git":
        raise ValueError("git_args must be the arguments after the git executable")

    return build_wsl_exec_command(
        wsl_command=wsl_command,
        distribution=distribution,
        wsl_cwd=resolution.wsl_path,
        executable_and_args=["git", *git_args],
    )


def planned_git_preflight(
    *,
    target: str,
    wsl_command: str,
    distribution: str,
    wsl_websites_root: str,
    locked_hub_root: str,
) -> dict[str, list[str]]:
    return {
        "inside": build_wsl_git_command(
            target=target,
            git_args=["rev-parse", "--is-inside-work-tree"],
            wsl_command=wsl_command,
            distribution=distribution,
            wsl_websites_root=wsl_websites_root,
            locked_hub_root=locked_hub_root,
        ),
        "branch": build_wsl_git_command(
            target=target,
            git_args=["rev-parse", "--abbrev-ref", "HEAD"],
            wsl_command=wsl_command,
            distribution=distribution,
            wsl_websites_root=wsl_websites_root,
            locked_hub_root=locked_hub_root,
        ),
        "commit": build_wsl_git_command(
            target=target,
            git_args=["rev-parse", "HEAD"],
            wsl_command=wsl_command,
            distribution=distribution,
            wsl_websites_root=wsl_websites_root,
            locked_hub_root=locked_hub_root,
        ),
        "top_level": build_wsl_git_command(
            target=target,
            git_args=["rev-parse", "--show-toplevel"],
            wsl_command=wsl_command,
            distribution=distribution,
            wsl_websites_root=wsl_websites_root,
            locked_hub_root=locked_hub_root,
        ),
        "status": build_wsl_git_command(
            target=target,
            git_args=["status", "--porcelain=v1"],
            wsl_command=wsl_command,
            distribution=distribution,
            wsl_websites_root=wsl_websites_root,
            locked_hub_root=locked_hub_root,
        ),
    }


def command_cd(command: list[str]) -> str | None:
    try:
        index = command.index("--cd")
    except ValueError:
        return None
    if index + 1 >= len(command):
        return None
    return command[index + 1]


def command_exec(command: list[str]) -> str | None:
    try:
        index = command.index("--exec")
    except ValueError:
        return None
    if index + 1 >= len(command):
        return None
    return command[index + 1]


def command_uses_wsl(command: list[str], *, wsl_command: str, distribution: str) -> bool:
    return (
        len(command) >= 7
        and command[0] == wsl_command
        and "--distribution" in command
        and distribution in command
        and "--cd" in command
        and "--exec" in command
    )


def command_uses_wsl_git(command: list[str], *, wsl_command: str, distribution: str) -> bool:
    return command_uses_wsl(command, wsl_command=wsl_command, distribution=distribution) and command_exec(command) == "git"


def command_avoids_local_git(command: list[str]) -> bool:
    return bool(command) and command[0] != "git"


def command_cd_inside_websites(command: list[str], *, wsl_websites_root: str) -> bool:
    cwd = command_cd(command)
    return bool(cwd) and is_inside_or_equal(cwd, wsl_websites_root)


def command_avoids_forbidden_paths(command: list[str], *, locked_hub_root: str) -> bool:
    text = "\n".join(command).replace("\\", "/")
    return (
        locked_hub_root not in text
        and "/mnt/" not in text
        and not re.search(r"[A-Za-z]:/", text)
    )


def run_command(command: list[str], *, timeout_seconds: float) -> CommandResult:
    try:
        proc = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        return CommandResult(command=command, returncode=127, stdout="", stderr=str(exc))
    except OSError as exc:
        return CommandResult(command=command, returncode=126, stdout="", stderr=str(exc))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(command=command, returncode=124, stdout=stdout, stderr=f"timed out\n{stderr}")
    return CommandResult(
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def result_summary(result: CommandResult) -> dict[str, Any]:
    return {
        "command": result.command,
        "returncode": result.returncode,
        "stdout": text_tail(result.stdout.strip(), 2000),
        "stderr_tail": text_tail(result.stderr.strip(), 2000),
        "ok": result.ok,
    }


def shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def fixture_site_id() -> str:
    # Starts and ends with alnum to satisfy site-id validation.
    return f"rag-git-exec-smoke-{os.getpid()}-{int(time.time())}"


def setup_fixture_script(*, fixture_wsl_path: str, wsl_websites_root: str) -> str:
    quoted_fixture = shell_single_quote(fixture_wsl_path)
    quoted_root = shell_single_quote(wsl_websites_root.rstrip("/"))
    html = "<!doctype html><title>WSL Git Smoke</title><h1>Seed homepage</h1>\n"
    quoted_html = shell_single_quote(html)
    return f"""
set -eu
fixture={quoted_fixture}
websites_root={quoted_root}
case "$fixture" in
  "$websites_root"/rag-git-exec-smoke-*) ;;
  *) echo "refusing unsafe fixture path: $fixture" >&2; exit 22 ;;
esac
rm -rf "$fixture"
mkdir -p "$fixture"
cd "$fixture"
git init >/dev/null
git checkout -B main >/dev/null
printf %s {quoted_html} > index.html
git add index.html
git -c user.name='RAG Smoke' -c user.email='rag-smoke@example.invalid' commit -m 'seed website fixture' >/dev/null
git status --porcelain=v1
""".strip()


def cleanup_fixture_script(*, fixture_wsl_path: str, wsl_websites_root: str, keep_fixture: bool) -> str:
    quoted_fixture = shell_single_quote(fixture_wsl_path)
    quoted_root = shell_single_quote(wsl_websites_root.rstrip("/"))
    if keep_fixture:
        return f"""
set -eu
fixture={quoted_fixture}
test -d "$fixture"
printf '%s\\n' "$fixture"
""".strip()
    return f"""
set -eu
fixture={quoted_fixture}
websites_root={quoted_root}
case "$fixture" in
  "$websites_root"/rag-git-exec-smoke-*) ;;
  *) echo "refusing unsafe cleanup path: $fixture" >&2; exit 22 ;;
esac
rm -rf "$fixture"
test ! -e "$fixture"
""".strip()


def parse_preflight_results(results: dict[str, CommandResult], *, fixture_wsl_path: str) -> dict[str, Any]:
    inside = results.get("inside")
    branch = results.get("branch")
    commit = results.get("commit")
    top_level = results.get("top_level")
    status = results.get("status")

    inside_text = (inside.stdout if inside else "").strip().lower()
    branch_text = (branch.stdout if branch else "").strip()
    commit_text = (commit.stdout if commit else "").strip()
    top_level_text = (top_level.stdout if top_level else "").strip()
    status_text = (status.stdout if status else "")

    return {
        "is_inside_work_tree": inside_text == "true",
        "branch": branch_text,
        "branch_detected": bool(branch_text) and branch_text != "HEAD",
        "commit": commit_text,
        "commit_detected": bool(re.fullmatch(r"[0-9a-fA-F]{40}", commit_text)),
        "top_level": top_level_text,
        "top_level_matches_fixture": normalize_absolute_wsl_path(top_level_text) == normalize_absolute_wsl_path(fixture_wsl_path)
        if top_level_text.startswith("/")
        else False,
        "status_porcelain": status_text,
        "working_tree_clean": status_text.strip() == "",
    }


def evaluate_case(args: argparse.Namespace) -> dict[str, Any]:
    wsl_command = args.wsl_command
    distribution = args.distribution
    wsl_websites_root = normalize_absolute_wsl_path(args.websites_root)
    locked_hub_root = normalize_absolute_wsl_path(args.locked_hub_root)

    site_id = fixture_site_id()
    fixture_wsl_path = f"{wsl_websites_root.rstrip('/')}/{site_id}"
    target = fixture_wsl_path

    selected_resolution = resolve_website_target(
        target,
        wsl_websites_root=wsl_websites_root,
        locked_hub_root=locked_hub_root,
    )
    negative_targets = {
        "install_hub": locked_hub_root,
        "host_mount": host_mount_negative_target("landing-site"),
        "windows_path": windows_path_negative_target("landing-site"),
        "traversal": "runtime/websites/../install/hub",
        "install_other": f"{posixpath.dirname(locked_hub_root)}/other-project",
    }
    negative_resolutions = {
        name: resolve_website_target(
            target_value,
            wsl_websites_root=wsl_websites_root,
            locked_hub_root=locked_hub_root,
        )
        for name, target_value in negative_targets.items()
    }

    capability_command = build_wsl_exec_command(
        wsl_command=wsl_command,
        distribution=distribution,
        wsl_cwd="/",
        executable_and_args=["sh", "-lc", "command -v git && git --version"],
    )
    capability_result = run_command(capability_command, timeout_seconds=args.timeout_seconds)

    setup_command = build_wsl_exec_command(
        wsl_command=wsl_command,
        distribution=distribution,
        wsl_cwd="/",
        executable_and_args=["sh", "-lc", setup_fixture_script(fixture_wsl_path=fixture_wsl_path, wsl_websites_root=wsl_websites_root)],
    )
    setup_result = run_command(setup_command, timeout_seconds=args.timeout_seconds) if capability_result.ok else CommandResult(
        command=setup_command,
        returncode=127,
        stdout="",
        stderr="skipped because WSL Git capability check failed",
    )

    commands = planned_git_preflight(
        target=target,
        wsl_command=wsl_command,
        distribution=distribution,
        wsl_websites_root=wsl_websites_root,
        locked_hub_root=locked_hub_root,
    )

    command_results: dict[str, CommandResult] = {}
    if setup_result.ok:
        for name, command in commands.items():
            command_results[name] = run_command(command, timeout_seconds=args.timeout_seconds)

    cleanup_command = build_wsl_exec_command(
        wsl_command=wsl_command,
        distribution=distribution,
        wsl_cwd="/",
        executable_and_args=["sh", "-lc", cleanup_fixture_script(fixture_wsl_path=fixture_wsl_path, wsl_websites_root=wsl_websites_root, keep_fixture=args.keep_fixture)],
    )
    cleanup_result = run_command(cleanup_command, timeout_seconds=args.timeout_seconds) if setup_result.ok else CommandResult(
        command=cleanup_command,
        returncode=127,
        stdout="",
        stderr="skipped because fixture setup failed",
    )

    parsed = parse_preflight_results(command_results, fixture_wsl_path=fixture_wsl_path) if command_results else {
        "is_inside_work_tree": False,
        "branch": "",
        "branch_detected": False,
        "commit": "",
        "commit_detected": False,
        "top_level": "",
        "top_level_matches_fixture": False,
        "status_porcelain": "",
        "working_tree_clean": False,
    }

    command_values = list(commands.values())
    checks = {
        "selected_target_resolves_inside_wsl_websites": selected_resolution.ok and selected_resolution.wsl_path == fixture_wsl_path,
        "wsl_command_available_and_git_found": capability_result.ok,
        "setup_disposable_fixture_repo_ok": setup_result.ok,
        "preflight_command_count": len(command_values) == 5,
        "all_commands_use_wsl_executor": all(
            command_uses_wsl_git(command, wsl_command=wsl_command, distribution=distribution)
            for command in command_values
        ),
        "no_commands_call_local_git": all(command_avoids_local_git(command) for command in command_values),
        "all_command_cwds_inside_wsl_websites": all(
            command_cd_inside_websites(command, wsl_websites_root=wsl_websites_root)
            for command in command_values
        ),
        "commands_avoid_host_mount_windows_and_locked_hub_paths": all(
            command_avoids_forbidden_paths(command, locked_hub_root=locked_hub_root)
            for command in command_values
        ),
        "all_preflight_commands_executed_ok": bool(command_results) and all(result.ok for result in command_results.values()),
        "is_inside_work_tree_true": parsed["is_inside_work_tree"],
        "branch_detected": parsed["branch_detected"],
        "commit_detected": parsed["commit_detected"],
        "working_tree_clean_detected": parsed["working_tree_clean"],
        "top_level_matches_fixture": parsed["top_level_matches_fixture"],
        "install_hub_rejected": negative_resolutions["install_hub"].reason == "hub_install_locked",
        "host_mount_rejected": negative_resolutions["host_mount"].reason == "host_mount_rejected",
        "windows_path_rejected": negative_resolutions["windows_path"].reason == "windows_path_rejected",
        "parent_traversal_rejected": negative_resolutions["traversal"].reason == "parent_traversal_rejected",
        "install_directory_rejected_for_non_hub_too": negative_resolutions["install_other"].reason == "outside_websites_root",
        "cleanup_ok": cleanup_result.ok,
        "auto_applied_false": True,
        "committed_user_repo_false": True,
    }

    return {
        "name": "wsl_website_git_preflight_executes_inside_disposable_website_repo",
        "ok": all(checks.values()),
        "request": REQUEST,
        "platform": platform.platform(),
        "wsl_command": wsl_command,
        "wsl_distribution": distribution,
        "wsl_websites_root": wsl_websites_root,
        "locked_hub_root": locked_hub_root,
        "fixture_site_id": site_id,
        "fixture_wsl_path": fixture_wsl_path,
        "selected_resolution": selected_resolution.__dict__,
        "planned_commands": commands,
        "capability_result": result_summary(capability_result),
        "setup_result": result_summary(setup_result),
        "command_results": {
            name: result_summary(result)
            for name, result in command_results.items()
        },
        "cleanup_result": result_summary(cleanup_result),
        "negative_resolutions": {
            name: resolution.__dict__
            for name, resolution in negative_resolutions.items()
        },
        "parsed_git_preflight": parsed,
        "checks": checks,
        "executed": bool(command_results),
        "auto_applied": False,
        "committed": False,
        "committed_user_repo": False,
        "fixture_seed_commit_created": setup_result.ok,
        "keep_fixture": bool(args.keep_fixture),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wsl-command", default=DEFAULT_WSL_COMMAND)
    parser.add_argument("--distribution", default=DEFAULT_WSL_DISTRIBUTION)
    parser.add_argument("--websites-root", default=DEFAULT_WSL_WEBSITES_ROOT)
    parser.add_argument("--locked-hub-root", default=DEFAULT_LOCKED_HUB_ROOT)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument(
        "--keep-fixture",
        action="store_true",
        help="Leave the disposable WSL fixture repo in place for manual inspection.",
    )
    args = parser.parse_args()

    case = evaluate_case(args)
    report = {
        "mode": MODE,
        "ok": case["ok"],
        "case_count": 1,
        "passed_case_count": 1 if case["ok"] else 0,
        "failed_case_count": 0 if case["ok"] else 1,
        "cases": [case],
    }

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
