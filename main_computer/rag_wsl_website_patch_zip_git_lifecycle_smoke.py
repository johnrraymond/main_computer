#!/usr/bin/env python3
"""
Phase-three smoke: combine the patch-zip lifecycle with WSL-scoped website Git.

The smoke creates a disposable website repo under /home/main-computer/websites,
uses Git only through wsl.exe, builds a changed-file-only patch zip, runs
new_patch.py through WSL against that WSL website repo, applies the patch to the
disposable fixture, validates the result with WSL Git, and cleans up.

It never targets the install hub, never targets /mnt/c as the website repo, never
calls host Git, and never creates the final post-validation commit.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import posixpath
import re
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MODE = "rag_wsl_website_patch_zip_git_lifecycle_smoke"
ORIGINAL_HEADLINE = "WSL Website Patch Zip Seed"
UPDATED_HEADLINE = "WSL Git validated patch zip lifecycle"
DEFAULT_WSL_COMMAND = os.environ.get("RAG_WSL_COMMAND", "wsl.exe")
DEFAULT_WSL_DISTRIBUTION = os.environ.get("RAG_WSL_DISTRIBUTION", "MainComputerExecutorTest")
DEFAULT_WSL_HOME = os.environ.get("RAG_WSL_HOME", "/home/main-computer")
DEFAULT_WSL_WEBSITES_ROOT = os.environ.get("RAG_WSL_WEBSITES_ROOT", f"{DEFAULT_WSL_HOME}/websites")
DEFAULT_LOCKED_HUB_ROOT = os.environ.get("RAG_WSL_LOCKED_HUB_ROOT", f"{DEFAULT_WSL_HOME}/install/hub")


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


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


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

def text_tail(text: str, max_chars: int = 2400) -> str:
    return text if len(text) <= max_chars else text[-max_chars:]


def norm_wsl(path: str) -> str:
    value = posixpath.normpath(path.replace("\\", "/"))
    if value == ".":
        value = ""
    if not value.startswith("/"):
        raise ValueError(f"expected absolute WSL path: {path}")
    return value


def inside_or_equal(path: str, root: str) -> bool:
    p = norm_wsl(path)
    r = norm_wsl(root).rstrip("/")
    return p == r or p.startswith(r + "/")


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def is_windows_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return bool(re.match(r"^[A-Za-z]:/", normalized)) or value.startswith("\\\\")


def contains_parent_traversal(value: str) -> bool:
    return any(part == ".." for part in value.replace("\\", "/").split("/"))


def is_host_mount_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return normalized == "/mnt" or normalized.startswith("/mnt/")


def valid_site_id(site_id: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,78}[a-z0-9]", site_id or ""))


def resolve_website_target(value: str, *, websites_root: str, locked_hub_root: str) -> TargetResolution:
    raw = str(value or "").strip()
    if not raw:
        return TargetResolution(False, raw, reason="empty_target")
    if "\x00" in raw:
        return TargetResolution(False, raw, reason="nul_rejected")
    if is_windows_path(raw):
        return TargetResolution(False, raw, reason="windows_path_rejected")
    if contains_parent_traversal(raw):
        return TargetResolution(False, raw, reason="parent_traversal_rejected")
    if is_host_mount_path(raw):
        return TargetResolution(False, raw, reason="host_mount_rejected")

    if raw.startswith("/"):
        try:
            wsl_path = norm_wsl(raw)
        except ValueError:
            return TargetResolution(False, raw, reason="invalid_wsl_path")
        if wsl_path == locked_hub_root or inside_or_equal(wsl_path, locked_hub_root):
            return TargetResolution(False, raw, reason="hub_install_locked")
        if not inside_or_equal(wsl_path, websites_root):
            return TargetResolution(False, raw, reason="outside_websites_root")
        rel = wsl_path.removeprefix(websites_root.rstrip("/") + "/")
        site_id = rel.split("/", 1)[0]
        if not valid_site_id(site_id):
            return TargetResolution(False, raw, reason="invalid_site_id")
        return TargetResolution(True, raw, wsl_path=wsl_path)

    normalized = raw.replace("\\", "/").strip("/")
    site_id = None
    for prefix in ("runtime/websites/", "websites/"):
        if normalized.startswith(prefix):
            candidate = normalized[len(prefix):]
            site_id = candidate if candidate and "/" not in candidate else None
            break
    if site_id is None and normalized and "/" not in normalized:
        site_id = normalized
    if not site_id:
        return TargetResolution(False, raw, reason="unsupported_relative_target")
    if not valid_site_id(site_id):
        return TargetResolution(False, raw, reason="invalid_site_id")
    return TargetResolution(True, raw, wsl_path=f"{websites_root.rstrip('/')}/{site_id}")


def wsl_exec(*, wsl_command: str, distribution: str, cwd: str, argv: list[str]) -> list[str]:
    return [wsl_command, "--distribution", distribution, "--cd", norm_wsl(cwd), "--exec", *argv]


def wsl_git(
    *,
    target: str,
    git_args: list[str],
    wsl_command: str,
    distribution: str,
    websites_root: str,
    locked_hub_root: str,
) -> list[str]:
    resolved = resolve_website_target(target, websites_root=websites_root, locked_hub_root=locked_hub_root)
    if not resolved.ok or not resolved.wsl_path:
        raise ValueError(f"unsafe website target: {resolved.reason}")
    if not git_args or git_args[0] == "git":
        raise ValueError("git_args must not include the git executable")
    return wsl_exec(wsl_command=wsl_command, distribution=distribution, cwd=resolved.wsl_path, argv=["git", *git_args])


def command_cd(command: list[str]) -> str | None:
    try:
        idx = command.index("--cd")
    except ValueError:
        return None
    return command[idx + 1] if idx + 1 < len(command) else None


def command_exec(command: list[str]) -> str | None:
    try:
        idx = command.index("--exec")
    except ValueError:
        return None
    return command[idx + 1] if idx + 1 < len(command) else None


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


def run(command: list[str], *, timeout: float) -> CommandResult:
    try:
        proc = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return CommandResult(command, 127, "", str(exc))
    except OSError as exc:
        return CommandResult(command, 126, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(command, 124, stdout, f"timed out\n{stderr}")
    return CommandResult(command, proc.returncode, proc.stdout, proc.stderr)


def result_json(result: CommandResult) -> dict[str, Any]:
    return {
        "command": result.command,
        "returncode": result.returncode,
        "ok": result.ok,
        "stdout": text_tail(result.stdout.strip()),
        "stderr_tail": text_tail(result.stderr.strip()),
    }


def homepage(headline: str) -> str:
    return (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "  <head><meta charset=\"utf-8\"><title>WSL Patch Zip Lifecycle Smoke</title></head>\n"
        "  <body>\n"
        f"    <h1>{headline}</h1>\n"
        "  </body>\n"
        "</html>\n"
    )


def host_path_to_wsl(path: Path) -> str:
    raw = str(Path(path).resolve())
    normalized = raw.replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/(.*)$", normalized)
    if match:
        return f"/mnt/{match.group(1).lower()}/{match.group(2)}"
    if normalized.startswith("/"):
        return normalized
    raise ValueError(f"cannot translate host path to WSL path: {raw}")


def write_patch_zip() -> tuple[Path, list[str]]:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    zip_path = Path(tempfile.gettempdir()) / "mc_wsl_zip_git_lifecycle_smoke" / stamp / "homepage_patch.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    members = ["index.html"]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(members[0], homepage(UPDATED_HEADLINE))
    return zip_path, members


def fixture_name() -> str:
    return f"rag-zip-git-lifecycle-smoke-{os.getpid()}-{int(time.time())}"


def setup_script(*, fixture: str, websites_root: str) -> str:
    return f"""
set -eu
fixture={shell_quote(fixture)}
websites_root={shell_quote(websites_root.rstrip("/"))}
case "$fixture" in
  "$websites_root"/rag-zip-git-lifecycle-smoke-*) ;;
  *) echo "refusing unsafe fixture path: $fixture" >&2; exit 22 ;;
esac
rm -rf "$fixture"
mkdir -p "$fixture"
cd "$fixture"
git init >/dev/null
git checkout -B main >/dev/null
printf %s {shell_quote("/tools/patching/reports/new_patch_runs/\n")} > .gitignore
printf %s {shell_quote(homepage(ORIGINAL_HEADLINE))} > index.html
git add .gitignore index.html
git -c user.name='RAG Smoke' -c user.email='rag-smoke@example.invalid' commit -m 'seed disposable website fixture' >/dev/null
git status --porcelain=v1
""".strip()


def cleanup_script(*, fixture: str, websites_root: str, keep: bool) -> str:
    if keep:
        return f"set -eu\nfixture={shell_quote(fixture)}\ntest -d \"$fixture\"\nprintf '%s\\n' \"$fixture\""
    return f"""
set -eu
fixture={shell_quote(fixture)}
websites_root={shell_quote(websites_root.rstrip("/"))}
case "$fixture" in
  "$websites_root"/rag-zip-git-lifecycle-smoke-*) ;;
  *) echo "refusing unsafe cleanup path: $fixture" >&2; exit 22 ;;
esac
rm -rf "$fixture"
test ! -e "$fixture"
""".strip()


def wsl_shell(*, script: str, wsl_command: str, distribution: str, timeout: float) -> CommandResult:
    return run(
        wsl_exec(wsl_command=wsl_command, distribution=distribution, cwd="/", argv=["sh", "-lc", script]),
        timeout=timeout,
    )


def new_patch_command(
    *,
    root: Path,
    zip_path: Path,
    fixture: str,
    wsl_command: str,
    distribution: str,
    dry_run: bool,
) -> list[str]:
    argv = ["python3", host_path_to_wsl(root / "new_patch.py"), host_path_to_wsl(zip_path), "--target-root", fixture]
    if dry_run:
        argv.append("--dry-run")
    return wsl_exec(wsl_command=wsl_command, distribution=distribution, cwd=fixture, argv=argv)


def git_preflight_commands(
    *,
    fixture: str,
    wsl_command: str,
    distribution: str,
    websites_root: str,
    locked_hub_root: str,
) -> dict[str, list[str]]:
    common = {
        "target": fixture,
        "wsl_command": wsl_command,
        "distribution": distribution,
        "websites_root": websites_root,
        "locked_hub_root": locked_hub_root,
    }
    return {
        "inside": wsl_git(git_args=["rev-parse", "--is-inside-work-tree"], **common),
        "branch": wsl_git(git_args=["rev-parse", "--abbrev-ref", "HEAD"], **common),
        "commit": wsl_git(git_args=["rev-parse", "HEAD"], **common),
        "top_level": wsl_git(git_args=["rev-parse", "--show-toplevel"], **common),
        "status": wsl_git(git_args=["status", "--porcelain=v1"], **common),
    }


def parse_git(results: dict[str, CommandResult], *, fixture: str) -> dict[str, Any]:
    inside = results.get("inside")
    branch = results.get("branch")
    commit = results.get("commit")
    top = results.get("top_level")
    status = results.get("status")
    inside_text = (inside.stdout if inside else "").strip().lower()
    branch_text = (branch.stdout if branch else "").strip()
    commit_text = (commit.stdout if commit else "").strip()
    top_text = (top.stdout if top else "").strip()
    status_text = status.stdout if status else ""
    return {
        "is_inside_work_tree": inside_text == "true",
        "branch": branch_text,
        "branch_detected": bool(branch_text) and branch_text != "HEAD",
        "commit": commit_text,
        "commit_detected": bool(re.fullmatch(r"[0-9a-fA-F]{40}", commit_text)),
        "top_level": top_text,
        "top_level_matches_fixture": top_text.startswith("/") and norm_wsl(top_text) == norm_wsl(fixture),
        "status_porcelain": status_text,
        "working_tree_clean": status_text.strip() == "",
    }


def run_preflight(commands: dict[str, list[str]], *, timeout: float) -> dict[str, CommandResult]:
    return {name: run(command, timeout=timeout) for name, command in commands.items()}


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    root = repo_root()
    wsl_command = args.wsl_command
    distribution = args.distribution
    websites_root = norm_wsl(args.websites_root)
    locked_hub_root = norm_wsl(args.locked_hub_root)
    fixture = f"{websites_root.rstrip('/')}/{fixture_name()}"

    selected = resolve_website_target(fixture, websites_root=websites_root, locked_hub_root=locked_hub_root)
    capability = wsl_shell(
        script="set -eu\ncommand -v git\ngit --version\ncommand -v python3\npython3 --version",
        wsl_command=wsl_command,
        distribution=distribution,
        timeout=args.timeout_seconds,
    )
    setup = wsl_shell(
        script=setup_script(fixture=fixture, websites_root=websites_root),
        wsl_command=wsl_command,
        distribution=distribution,
        timeout=args.timeout_seconds,
    ) if capability.ok else CommandResult([], 127, "", "skipped because WSL Git/Python capability check failed")

    zip_path, patch_members = write_patch_zip()
    commands = git_preflight_commands(
        fixture=fixture,
        wsl_command=wsl_command,
        distribution=distribution,
        websites_root=websites_root,
        locked_hub_root=locked_hub_root,
    )

    before = run_preflight(commands, timeout=args.timeout_seconds) if setup.ok else {}
    before_parsed = parse_git(before, fixture=fixture) if before else {}

    dry_cmd = new_patch_command(root=root, zip_path=zip_path, fixture=fixture, wsl_command=wsl_command, distribution=distribution, dry_run=True)
    dry = run(dry_cmd, timeout=args.timeout_seconds) if setup.ok else CommandResult(dry_cmd, 127, "", "skipped because fixture setup failed")
    after_dry = run_preflight(commands, timeout=args.timeout_seconds) if dry.ok else {}
    after_dry_parsed = parse_git(after_dry, fixture=fixture) if after_dry else {}

    apply_cmd = new_patch_command(root=root, zip_path=zip_path, fixture=fixture, wsl_command=wsl_command, distribution=distribution, dry_run=False)
    applied = run(apply_cmd, timeout=args.timeout_seconds) if dry.ok else CommandResult(apply_cmd, 127, "", "skipped because dry-run failed")

    post = run_preflight(commands, timeout=args.timeout_seconds) if applied.ok else {}
    post_parsed = parse_git(post, fixture=fixture) if post else {}

    diff_cmd = wsl_git(
        target=fixture,
        git_args=["diff", "--", "index.html"],
        wsl_command=wsl_command,
        distribution=distribution,
        websites_root=websites_root,
        locked_hub_root=locked_hub_root,
    )
    diff = run(diff_cmd, timeout=args.timeout_seconds) if applied.ok else CommandResult(diff_cmd, 127, "", "skipped because apply failed")

    read_index = wsl_shell(
        script=f"set -eu\ncat {shell_quote(fixture + '/index.html')}",
        wsl_command=wsl_command,
        distribution=distribution,
        timeout=args.timeout_seconds,
    ) if applied.ok else CommandResult([], 127, "", "skipped because apply failed")

    cleanup = wsl_shell(
        script=cleanup_script(fixture=fixture, websites_root=websites_root, keep=args.keep_fixture),
        wsl_command=wsl_command,
        distribution=distribution,
        timeout=args.timeout_seconds,
    ) if setup.ok else CommandResult([], 127, "", "skipped because fixture setup failed")

    negative = {
        "install_hub": locked_hub_root,
        "host_mount": host_mount_negative_target("landing-site"),
        "windows_path": windows_path_negative_target("landing-site"),
        "traversal": "runtime/websites/../install/hub",
        "install_other": f"{posixpath.dirname(locked_hub_root)}/other-project",
    }
    negative_resolutions = {
        name: resolve_website_target(value, websites_root=websites_root, locked_hub_root=locked_hub_root)
        for name, value in negative.items()
    }

    git_command_values = [*commands.values(), diff_cmd]
    new_patch_commands = [dry_cmd, apply_cmd]
    status_after_apply = str(post_parsed.get("status_porcelain", ""))

    def target_root_ok(command: list[str]) -> bool:
        try:
            idx = command.index("--target-root")
        except ValueError:
            return False
        return idx + 1 < len(command) and norm_wsl(command[idx + 1]) == norm_wsl(fixture)

    checks = {
        "selected_target_resolves_inside_wsl_websites": selected.ok and selected.wsl_path == fixture,
        "patch_zip_created_with_changed_file_only": zip_path.exists() and patch_members == ["index.html"],
        "wsl_capability_git_and_python_found": capability.ok,
        "setup_disposable_fixture_repo_ok": setup.ok,
        "all_git_commands_use_wsl_executor": all(command_uses_wsl_git(c, wsl_command=wsl_command, distribution=distribution) for c in git_command_values),
        "no_git_commands_call_local_git": all(c and c[0] != "git" for c in git_command_values),
        "all_git_command_cwds_inside_wsl_websites": all(command_cd(c) and inside_or_equal(command_cd(c) or "", websites_root) for c in git_command_values),
        "new_patch_commands_run_through_wsl": all(command_uses_wsl(c, wsl_command=wsl_command, distribution=distribution) and command_exec(c) == "python3" for c in new_patch_commands),
        "new_patch_target_roots_are_wsl_website": all(target_root_ok(c) for c in new_patch_commands),
        "before_preflight_inside_git_repo": bool(before_parsed.get("is_inside_work_tree")),
        "before_preflight_branch_detected": bool(before_parsed.get("branch_detected")),
        "before_preflight_commit_detected": bool(before_parsed.get("commit_detected")),
        "before_preflight_clean": bool(before_parsed.get("working_tree_clean")),
        "dry_run_ok": dry.ok,
        "dry_run_changed_one_file": "changed_files: 1" in dry.stdout,
        "dry_run_diff_targets_index": "b/index.html" in dry.stdout and UPDATED_HEADLINE in dry.stdout,
        "dry_run_did_not_modify_worktree": bool(after_dry_parsed.get("working_tree_clean")),
        "apply_ok": applied.ok,
        "apply_reports_copied": "applied: copied replacement files into the target root." in applied.stdout,
        "post_apply_status_tracks_only_index": status_after_apply.strip() == "M index.html",
        "post_apply_diff_mentions_headline": ORIGINAL_HEADLINE in diff.stdout and UPDATED_HEADLINE in diff.stdout,
        "post_apply_file_contains_updated_headline": UPDATED_HEADLINE in read_index.stdout and ORIGINAL_HEADLINE not in read_index.stdout,
        "install_hub_rejected": negative_resolutions["install_hub"].reason == "hub_install_locked",
        "host_mount_rejected": negative_resolutions["host_mount"].reason == "host_mount_rejected",
        "windows_path_rejected": negative_resolutions["windows_path"].reason == "windows_path_rejected",
        "parent_traversal_rejected": negative_resolutions["traversal"].reason == "parent_traversal_rejected",
        "install_directory_rejected_for_non_hub_too": negative_resolutions["install_other"].reason == "outside_websites_root",
        "cleanup_ok": cleanup.ok,
        "committed_user_repo_false": True,
        "final_commit_created_false": True,
    }

    return {
        "name": "wsl_git_and_patch_zip_lifecycle_apply_to_disposable_website",
        "ok": all(checks.values()),
        "mode": MODE,
        "platform": platform.platform(),
        "wsl_command": wsl_command,
        "wsl_distribution": distribution,
        "wsl_websites_root": websites_root,
        "locked_hub_root": locked_hub_root,
        "fixture_wsl_path": fixture,
        "selected_resolution": selected.__dict__,
        "patch_zip_path": str(zip_path),
        "patch_zip_wsl_path": host_path_to_wsl(zip_path),
        "patch_zip_members": patch_members,
        "capability_result": result_json(capability),
        "setup_result": result_json(setup),
        "before_preflight": {k: result_json(v) for k, v in before.items()},
        "before_parsed_git": before_parsed,
        "dry_run_result": result_json(dry),
        "after_dry_run_preflight": {k: result_json(v) for k, v in after_dry.items()},
        "after_dry_run_parsed_git": after_dry_parsed,
        "apply_result": result_json(applied),
        "post_apply_preflight": {k: result_json(v) for k, v in post.items()},
        "post_apply_parsed_git": post_parsed,
        "post_apply_diff_result": result_json(diff),
        "post_apply_index_html_result": result_json(read_index),
        "cleanup_result": result_json(cleanup),
        "negative_resolutions": {k: v.__dict__ for k, v in negative_resolutions.items()},
        "checks": checks,
        "executed_wsl_git": bool(before) and all(r.ok for r in before.values()),
        "dry_run_executed": dry.ok,
        "applied_to_disposable_fixture": applied.ok,
        "applied_to_user_repo": False,
        "committed_user_repo": False,
        "final_commit_created": False,
        "fixture_seed_commit_created": setup.ok,
        "keep_fixture": bool(args.keep_fixture),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wsl-command", default=DEFAULT_WSL_COMMAND)
    parser.add_argument("--distribution", default=DEFAULT_WSL_DISTRIBUTION)
    parser.add_argument("--websites-root", default=DEFAULT_WSL_WEBSITES_ROOT)
    parser.add_argument("--locked-hub-root", default=DEFAULT_LOCKED_HUB_ROOT)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--keep-fixture", action="store_true")
    args = parser.parse_args()

    case = evaluate(args)
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
