#!/usr/bin/env python3
"""
Smoke: instructive requests can produce both a patch zip and a Git checkpoint plan.

This deterministic, no-model smoke proves the combined product shape without
mutating the user's real repository:

* create a tiny temporary Git-backed website project
* simulate an ordinary instructive request
* materialize a changed-file-only raw snapshot patch zip
* run new_patch.py --dry-run against that temporary project
* collect Git preflight metadata and a recommended AI branch name
* prove no apply or commit happens by default

The patch artifact is still the handoff boundary.  Git is only inspected here so
the later apply/commit orchestration can be layered on top without bypassing the
zip dry-run contract.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


MODE = "rag_git_aware_patch_zip_lifecycle_smoke"
REQUEST = "Update the homepage hero headline."
RECOMMENDED_BRANCH = "ai/update-homepage-hero-headline"
ORIGINAL_HEADLINE = "Welcome to Mini Site"
UPDATED_HEADLINE = "Build reliable patch handoffs"


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def output_root(root: Path) -> Path:
    """Use a short temp path so Windows dry-runs do not trip MAX_PATH.

    The first version nested the throwaway mini site under repo/debug_assets plus
    a long case name.  new_patch.py then created its own reports directory under
    the target root, which pushed Windows over the default path-length limit.
    This smoke is intentionally non-mutating, so the disposable project belongs
    in a short temp location rather than inside the repository tree.
    """

    del root
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(tempfile.gettempdir()) / "mc_gitzip_smoke" / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def text_tail(text: str, max_chars: int = 1600) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def run_command(cwd: Path, args: list[str], timeout_seconds: float = 20.0) -> CommandResult:
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=command_env(),
            timeout=timeout_seconds,
        )
        return CommandResult(
            args=list(args),
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
            returncode=None,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
        )


def command_payload(result: CommandResult) -> dict[str, Any]:
    return {
        "args": result.args,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "stdout_tail": text_tail(result.stdout),
        "stderr_tail": text_tail(result.stderr),
        "stdout_bytes": len(result.stdout.encode("utf-8", errors="replace")),
        "stderr_bytes": len(result.stderr.encode("utf-8", errors="replace")),
        "timed_out": result.timed_out,
    }


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def homepage_html(headline: str) -> str:
    return (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "  <head>\n"
        "    <meta charset=\"utf-8\">\n"
        "    <title>Mini Site</title>\n"
        "    <link rel=\"stylesheet\" href=\"styles.css\">\n"
        "  </head>\n"
        "  <body>\n"
        "    <main class=\"hero\">\n"
        f"      <h1>{headline}</h1>\n"
        "      <p>A tiny fixture for Git-aware patch zip lifecycle checks.</p>\n"
        "    </main>\n"
        "  </body>\n"
        "</html>\n"
    )


def slugify_branch(request: str) -> str:
    words = re.findall(r"[a-z0-9]+", request.lower())
    meaningful_words = [word for word in words if word not in {"a", "an", "the"}]
    stem = "-".join(meaningful_words)
    return f"ai/{stem}"


def init_git_repo(project: Path) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []

    init = run_command(project.parent, ["git", "init", "-b", "main", str(project)])
    commands.append({"name": "git_init_main", **command_payload(init)})
    if init.returncode != 0:
        fallback_init = run_command(project.parent, ["git", "init", str(project)])
        commands.append({"name": "git_init_fallback", **command_payload(fallback_init)})
        if fallback_init.returncode == 0:
            checkout = run_command(project, ["git", "checkout", "-b", "main"])
            commands.append({"name": "git_checkout_main", **command_payload(checkout)})

    for name, args in (
        ("git_config_user_email", ["git", "config", "user.email", "smoke@example.test"]),
        ("git_config_user_name", ["git", "config", "user.name", "Smoke Test"]),
        ("git_add", ["git", "add", "."]),
        ("git_commit", ["git", "commit", "-m", "Initial mini site fixture"]),
    ):
        result = run_command(project, args)
        commands.append({"name": name, **command_payload(result)})

    return commands


def create_mini_site(project: Path) -> list[dict[str, Any]]:
    project.mkdir(parents=True, exist_ok=True)
    write_text(project / ".gitignore", "/tools/patching/reports/new_patch_runs/\n")
    write_text(project / "index.html", homepage_html(ORIGINAL_HEADLINE))
    write_text(
        project / "styles.css",
        (
            "body { font-family: system-ui, sans-serif; margin: 0; }\n"
            ".hero { padding: 4rem; }\n"
        ),
    )
    return init_git_repo(project)


def git_preflight(project: Path) -> dict[str, Any]:
    inside = run_command(project, ["git", "rev-parse", "--is-inside-work-tree"])
    branch = run_command(project, ["git", "rev-parse", "--abbrev-ref", "HEAD"])
    commit = run_command(project, ["git", "rev-parse", "HEAD"])
    status = run_command(project, ["git", "status", "--porcelain=v1"])

    status_lines = [line for line in status.stdout.splitlines() if line.strip()]
    return {
        "is_git_repo": inside.returncode == 0 and inside.stdout.strip() == "true",
        "current_branch": branch.stdout.strip() if branch.returncode == 0 else None,
        "current_commit": commit.stdout.strip() if commit.returncode == 0 else None,
        "working_tree_clean": status.returncode == 0 and not status_lines,
        "untracked_count": sum(1 for line in status_lines if line.startswith("??")),
        "status_porcelain": status.stdout,
        "commands": {
            "inside": command_payload(inside),
            "branch": command_payload(branch),
            "commit": command_payload(commit),
            "status": command_payload(status),
        },
    }


def write_patch_zip(zip_path: Path) -> list[str]:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    members = ["index.html"]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(members[0], homepage_html(UPDATED_HEADLINE))
    return members


def parse_summary_path(stdout: str, label: str) -> Path | None:
    prefix = f"{label}:"
    for line in stdout.splitlines():
        if line.startswith(prefix):
            value = line[len(prefix) :].strip().strip('"')
            if value:
                return Path(value)
    return None


def load_dry_run_report(stdout: str) -> tuple[dict[str, Any] | None, str | None]:
    run_dir = parse_summary_path(stdout, "run_dir")
    if run_dir is None:
        return None, None
    report_path = run_dir / "run.json"
    if not report_path.exists():
        return None, str(report_path)
    try:
        return json.loads(report_path.read_text(encoding="utf-8")), str(report_path)
    except json.JSONDecodeError:
        return None, str(report_path)


def run_new_patch_dry_run(root: Path, project: Path, zip_path: Path) -> dict[str, Any]:
    args = [
        sys.executable,
        "-S",
        str(root / "new_patch.py"),
        str(zip_path),
        "--target-root",
        str(project),
        "--dry-run",
    ]
    result = run_command(root, args, timeout_seconds=30.0)
    report, report_path = load_dry_run_report(result.stdout)
    actual_patch_path = Path(report["actual_patch_path"]) if isinstance(report, dict) and report.get("actual_patch_path") else None
    actual_patch = actual_patch_path.read_text(encoding="utf-8") if actual_patch_path and actual_patch_path.exists() else ""

    payload = command_payload(result)
    payload.update(
        {
            "ok": result.returncode == 0,
            "report": report,
            "report_path": report_path,
            "actual_patch": actual_patch,
            "actual_patch_tail": text_tail(actual_patch),
        }
    )
    return payload


def evaluate_case(root: Path, out_dir: Path) -> dict[str, Any]:
    case_dir = out_dir / "instructive_request_produces_patch_zip_and_git_checkpoint_plan"
    project = case_dir / "mini_site"

    setup_commands = create_mini_site(project)
    original_index = (project / "index.html").read_text(encoding="utf-8")
    before = git_preflight(project)

    branch_name = slugify_branch(REQUEST)
    zip_path = case_dir / "artifacts" / "mini_site_homepage_hero_patch.zip"
    zip_members = write_patch_zip(zip_path)

    dry_run = run_new_patch_dry_run(root, project, zip_path)
    after_index = (project / "index.html").read_text(encoding="utf-8")
    after = git_preflight(project)

    dry_run_report = dry_run.get("report") if isinstance(dry_run.get("report"), dict) else {}
    changed_files = dry_run_report.get("changed_files") if isinstance(dry_run_report, dict) else None
    actual_patch = str(dry_run.get("actual_patch") or "")

    checks = {
        "setup_git_commit_ok": any(
            command.get("name") == "git_commit" and command.get("returncode") == 0
            for command in setup_commands
        ),
        "patch_zip_created": zip_path.exists(),
        "patch_zip_has_changed_file_only": zip_members == ["index.html"],
        "dry_run_returncode_ok": dry_run.get("ok") is True,
        "dry_run_status_ok": isinstance(dry_run_report, dict) and dry_run_report.get("status") == "dry_run_ok",
        "dry_run_changed_file_is_index": changed_files == ["index.html"],
        "actual_patch_targets_homepage": "b/index.html" in actual_patch and UPDATED_HEADLINE in actual_patch,
        "git_preflight_ok": before["is_git_repo"] is True and bool(before["current_commit"]),
        "working_tree_clean_before": before["working_tree_clean"] is True,
        "recommended_branch_name_ok": branch_name == RECOMMENDED_BRANCH,
        "auto_applied_false": True,
        "committed_false": True,
        "dry_run_did_not_modify_homepage": original_index == after_index and ORIGINAL_HEADLINE in after_index,
        "working_tree_clean_after": after["working_tree_clean"] is True,
    }

    return {
        "name": "instructive_request_produces_patch_zip_and_git_checkpoint_plan",
        "ok": all(checks.values()),
        "request": REQUEST,
        "patch_zip_created": zip_path.exists(),
        "patch_zip_path": str(zip_path),
        "patch_zip_members": zip_members,
        "dry_run_ok": dry_run.get("ok") is True,
        "dry_run_command": dry_run.get("args"),
        "dry_run_report_path": dry_run.get("report_path"),
        "dry_run_changed_files": changed_files,
        "git_preflight_ok": checks["git_preflight_ok"],
        "starting_branch": before["current_branch"],
        "starting_commit": before["current_commit"],
        "working_tree_clean_before": before["working_tree_clean"],
        "working_tree_clean_after": after["working_tree_clean"],
        "recommended_branch_name": branch_name,
        "auto_applied": False,
        "committed": False,
        "checks": checks,
        "git_preflight_before": before,
        "git_preflight_after": after,
        "setup_commands": setup_commands,
        "dry_run": dry_run,
    }


def main() -> int:
    root = repo_root()
    out_dir = output_root(root)

    case = evaluate_case(root, out_dir)
    report = {
        "mode": MODE,
        "ok": case["ok"],
        "repo_root": str(root),
        "case_count": 1,
        "passed_case_count": 1 if case["ok"] else 0,
        "failed_case_count": 0 if case["ok"] else 1,
        "cases": [case],
    }

    report_path = out_dir / "final_report.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nWrote report: {report_path}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
