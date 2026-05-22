#!/usr/bin/env python3
"""
Golden website path smoke: website-builder debug site -> RAG edit -> patch zip -> Git commit.

This is deliberately a separate smoke from rag_debug_website_golden_path_smoke.py.
The unit of work is exactly one website-builder managed debug site:

    tools/local-platform/debug-website.py list/ensure
    -> runtime/websites/<debug-golden-path-*>
    -> blessed generated-editor RAG pathway
    -> patch zip snapshot
    -> new_patch.py --dry-run against that site root
    -> new_patch.py apply against that site root
    -> WSL Git verification and commit in that site repo

When --site is omitted, the smoke selects the most recently modified
runtime/websites/debug-golden-path-* site.  If none exists, it creates a new
debug-golden-path-* site through the debug website builder and then targets that
site.  It never scans arbitrary website repos and never targets the install hub.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_REPO_ROOT))
if str(SCRIPT_REPO_ROOT / "main_computer") not in sys.path:
    sys.path.insert(0, str(SCRIPT_REPO_ROOT / "main_computer"))

from main_computer.rag_debug_website_golden_path_smoke import (
    AI_BRANCH,
    DEFAULT_LOCKED_HUB_ROOT,
    DEFAULT_WSL_COMMAND,
    DEFAULT_WSL_DISTRIBUTION,
    CommandResult,
    ProgressReporter,
    blessed_artifact_not_ready_reason,
    command_exec,
    command_uses_wsl,
    failed_check,
    get_progress,
    host_path_to_wsl,
    local_platform_env,
    norm_wsl,
    patch_members,
    parse_git,
    repo_root,
    result_json,
    run,
    run_blessed_generated_editor_patch_artifact,
    run_preflight,
    sha256_text,
    shell_quote,
    valid_site_id,
    wsl_exec,
    wsl_shell,
)


MODE = "rag_golden_website_path_smoke"
EDIT_REQUEST = (
    "Update this debug website homepage copy so a user understands the golden "
    "website path: choose one builder-managed debug site, generate a patch zip "
    "snapshot, validate it, apply it to that same site, verify with Git, and "
    "commit only after validation. Keep the existing page structure, metadata, "
    "and non-homepage assets intact."
)
COMMIT_MESSAGE = "Apply golden website path edit"
DEBUG_GOLDEN_PREFIX = "debug-golden-path-"
FORBIDDEN_AI_SITE_IDS = {
    "hub-site",
    "hub-local",
    "hub-dev",
    "hub-prod",
    "zzzzz",
    "zzzzz-dev",
    "zzzzz-prod",
}




@dataclass(frozen=True)
class BuilderSiteCandidate:
    site_id: str
    site_path: Path
    repo_relative_path: str
    modified_ns: int
    source: str


@dataclass(frozen=True)
class BuilderSiteTarget:
    ok: bool
    site_id: str
    builder_root: str
    site_path: str
    site_wsl_path: str | None = None
    websites_root: str | None = None
    reason: str | None = None


def builder_websites_root(builder_root: Path) -> Path:
    return builder_root / "runtime" / "websites"


def is_debug_golden_site_id(site_id: str) -> bool:
    clean = str(site_id or "").strip().lower()
    return bool(valid_site_id(clean)) and clean.startswith(DEBUG_GOLDEN_PREFIX) and clean not in FORBIDDEN_AI_SITE_IDS


def site_git_ceiling_wsl(target: "BuilderSiteTarget") -> str:
    """Return the WSL path that prevents Git from discovering the builder repo.

    A selected debug website lives under runtime/websites/<site>.  The ceiling is
    runtime/websites so Git may find .git in the selected site but may not climb
    into the parent builder/application repository.
    """

    if not target.websites_root:
        raise ValueError("target has no websites_root for Git ceiling")
    return norm_wsl(target.websites_root).rstrip("/")


def site_mtime_ns(site_path: Path) -> int:
    candidates = [
        site_path,
        site_path / "site.json",
        site_path / "index.html",
        site_path / "builder.json",
    ]
    newest = 0
    for candidate in candidates:
        try:
            newest = max(newest, candidate.stat().st_mtime_ns)
        except OSError:
            continue
    return newest


def list_debug_golden_path_sites(builder_root: Path) -> list[BuilderSiteCandidate]:
    """Return builder-managed debug-golden-path sites ordered newest first."""

    websites = builder_websites_root(builder_root)
    if not websites.exists():
        return []
    candidates: list[BuilderSiteCandidate] = []
    for site_path in websites.iterdir():
        if not site_path.is_dir():
            continue
        site_id = site_path.name
        if not site_id.startswith(DEBUG_GOLDEN_PREFIX) or not valid_site_id(site_id):
            continue
        manifest_path = site_path / "site.json"
        if not manifest_path.is_file():
            continue
        candidates.append(
            BuilderSiteCandidate(
                site_id=site_id,
                site_path=site_path,
                repo_relative_path=f"runtime/websites/{site_id}",
                modified_ns=site_mtime_ns(site_path),
                source="runtime/websites",
            )
        )
    return sorted(candidates, key=lambda item: (item.modified_ns, item.site_id), reverse=True)


def select_site_id(
    *,
    builder_root: Path,
    explicit_site: str | None,
    create_if_missing: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Resolve exactly one debug-golden-path site for this smoke.

    The default is intentionally site-scoped: choose the most recent existing
    builder-managed debug-golden-path site.  Creating a new site is only a
    fallback for an empty workbench, not a repo search.
    """

    if explicit_site:
        site_id = explicit_site.strip().lower()
        if not is_debug_golden_site_id(site_id):
            raise ValueError("golden website path smoke only targets debug-golden-path-* sites")
        return site_id, {"source": "explicit", "candidate_count": len(list_debug_golden_path_sites(builder_root))}

    candidates = list_debug_golden_path_sites(builder_root)
    if candidates:
        selected = candidates[0]
        return selected.site_id, {
            "source": "most_recent_debug_golden_path_site",
            "candidate_count": len(candidates),
            "selected_site_path": str(selected.site_path),
            "selected_modified_ns": selected.modified_ns,
        }

    if not create_if_missing:
        raise ValueError("no runtime/websites/debug-golden-path-* sites exist; pass --site or create one first")

    generated = f"{DEBUG_GOLDEN_PREFIX}{os.getpid()}-{int(time.time())}"
    return generated, {
        "source": "generated_no_existing_debug_golden_path_site",
        "candidate_count": 0,
    }


def run_debug_website(
    *,
    root: Path,
    builder_root: Path,
    args: list[str],
    timeout: float,
    progress: ProgressReporter | None,
    label: str,
) -> tuple[CommandResult, dict[str, Any]]:
    script = root / "tools" / "local-platform" / "debug-website.py"
    command = [sys.executable, "-S", str(script), *args, "--repo-root", str(builder_root)]
    result = run(
        command,
        timeout=timeout,
        env=local_platform_env(),
        progress=progress,
        label=label,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {}
    return result, payload if isinstance(payload, dict) else {}


def ensure_builder_debug_site(
    *,
    root: Path,
    builder_root: Path,
    site_id: str,
    timeout: float,
    progress: ProgressReporter | None,
) -> tuple[CommandResult, dict[str, Any]]:
    return run_debug_website(
        root=root,
        builder_root=builder_root,
        args=["ensure", "--site", site_id, "--purpose", "golden website path"],
        timeout=timeout,
        progress=progress,
        label="debug website builder ensure",
    )


def resolve_builder_site_target(*, builder_root: Path, site_id: str, site_path: Path) -> BuilderSiteTarget:
    """Validate the single website-builder site target and compute its WSL path."""

    clean_site_id = str(site_id or "").strip().lower()
    if not is_debug_golden_site_id(clean_site_id):
        return BuilderSiteTarget(False, clean_site_id, str(builder_root), str(site_path), reason="not_debug_golden_path_site")

    try:
        builder_root_resolved = builder_root.resolve()
        site_resolved = site_path.resolve()
        websites_resolved = builder_websites_root(builder_root_resolved).resolve()
        site_resolved.relative_to(websites_resolved)
    except (OSError, ValueError):
        return BuilderSiteTarget(False, clean_site_id, str(builder_root), str(site_path), reason="outside_builder_websites_root")

    if site_resolved.name != clean_site_id:
        return BuilderSiteTarget(False, clean_site_id, str(builder_root), str(site_path), reason="site_id_path_mismatch")

    try:
        site_wsl_path = host_path_to_wsl(site_resolved)
        websites_wsl_root = host_path_to_wsl(websites_resolved)
    except ValueError as exc:
        return BuilderSiteTarget(False, clean_site_id, str(builder_root), str(site_path), reason=f"wsl_path_unavailable: {exc}")

    normalized_site = norm_wsl(site_wsl_path)
    normalized_root = norm_wsl(websites_wsl_root).rstrip("/")
    if not normalized_site.startswith(normalized_root + "/"):
        return BuilderSiteTarget(False, clean_site_id, str(builder_root), str(site_path), reason="wsl_path_outside_builder_websites_root")

    return BuilderSiteTarget(
        True,
        clean_site_id,
        str(builder_root_resolved),
        str(site_resolved),
        site_wsl_path=normalized_site,
        websites_root=normalized_root,
    )


def wsl_site_git(
    *,
    target: BuilderSiteTarget,
    git_args: list[str],
    wsl_command: str,
    distribution: str,
) -> list[str]:
    if not target.ok or not target.site_wsl_path:
        raise ValueError(f"unsafe builder site target: {target.reason}")
    if not git_args or git_args[0] == "git":
        raise ValueError("git_args must not include the git executable")
    ceiling = f"GIT_CEILING_DIRECTORIES={site_git_ceiling_wsl(target)}"
    return wsl_exec(
        wsl_command=wsl_command,
        distribution=distribution,
        cwd=target.site_wsl_path,
        argv=["env", ceiling, "git", *git_args],
    )


def site_git_preflight_commands(
    *,
    target: BuilderSiteTarget,
    wsl_command: str,
    distribution: str,
) -> dict[str, list[str]]:
    return {
        "inside": wsl_site_git(target=target, git_args=["rev-parse", "--is-inside-work-tree"], wsl_command=wsl_command, distribution=distribution),
        "branch": wsl_site_git(target=target, git_args=["rev-parse", "--abbrev-ref", "HEAD"], wsl_command=wsl_command, distribution=distribution),
        "commit": wsl_site_git(target=target, git_args=["rev-parse", "HEAD"], wsl_command=wsl_command, distribution=distribution),
        "top_level": wsl_site_git(target=target, git_args=["rev-parse", "--show-toplevel"], wsl_command=wsl_command, distribution=distribution),
        "status": wsl_site_git(target=target, git_args=["status", "--porcelain=v1"], wsl_command=wsl_command, distribution=distribution),
    }


def ensure_site_git_script(*, target: BuilderSiteTarget, ai_branch: str) -> str:
    if not target.site_wsl_path:
        raise ValueError("target has no WSL path")
    site = shell_quote(target.site_wsl_path)
    ceiling = shell_quote(site_git_ceiling_wsl(target))
    return f"""
set -eu
export GIT_CEILING_DIRECTORIES={ceiling}
cd {site}
if [ ! -d .git ]; then
  git init >/dev/null
  git checkout -B main >/dev/null
  printf '%s\n' '/tools/patching/reports/new_patch_runs/' > .gitignore
  git add .gitignore site.json index.html style.css script.js builder.json
  git -c user.name='RAG Smoke' -c user.email='rag-smoke@example.invalid' commit -m 'seed builder debug website workbench' >/dev/null
fi
top="$(git rev-parse --show-toplevel)"
if [ "$top" != "$PWD" ]; then
  printf 'selected site Git top-level escaped: %s\n' "$top" >&2
  exit 24
fi
status="$(git status --porcelain=v1)"
if [ -n "$status" ]; then
  printf '%s\n' "$status" >&2
  echo "builder debug site has uncommitted changes before smoke" >&2
  exit 23
fi
git checkout -B {shell_quote(ai_branch)} >/dev/null
git status --porcelain=v1
""".strip()



def new_patch_command_for_site(
    *,
    root: Path,
    zip_path: Path,
    target: BuilderSiteTarget,
    wsl_command: str,
    distribution: str,
    dry_run: bool,
) -> list[str]:
    if not target.site_wsl_path:
        raise ValueError("target has no WSL path")
    argv = [
        "python3",
        host_path_to_wsl(root / "new_patch.py"),
        host_path_to_wsl(zip_path),
        "--target-root",
        target.site_wsl_path,
    ]
    if dry_run:
        argv.append("--dry-run")
    return wsl_exec(wsl_command=wsl_command, distribution=distribution, cwd=target.site_wsl_path, argv=argv)


def commit_command_for_site(*, target: BuilderSiteTarget, wsl_command: str, distribution: str) -> list[str]:
    if not target.site_wsl_path:
        raise ValueError("target has no WSL path")
    ceiling = shell_quote(site_git_ceiling_wsl(target))
    expected_top = shell_quote(target.site_wsl_path)
    script = (
        "set -eu\n"
        f"export GIT_CEILING_DIRECTORIES={ceiling}\n"
        "top=\"$(git rev-parse --show-toplevel)\"\n"
        f"test \"$top\" = {expected_top}\n"
        "git add index.html\n"
        "git -c user.name='RAG Smoke' -c user.email='rag-smoke@example.invalid' "
        f"commit -m {shell_quote(COMMIT_MESSAGE)} >/dev/null\n"
        "git status --porcelain=v1\n"
    )
    return wsl_exec(wsl_command=wsl_command, distribution=distribution, cwd=target.site_wsl_path, argv=["sh", "-lc", script])


def target_root_ok(command: list[str], *, target: BuilderSiteTarget) -> bool:
    try:
        idx = command.index("--target-root")
    except ValueError:
        return False
    return target.site_wsl_path is not None and idx + 1 < len(command) and norm_wsl(command[idx + 1]) == norm_wsl(target.site_wsl_path)


def git_command_is_site_scoped(command: list[str], *, target: BuilderSiteTarget, wsl_command: str, distribution: str) -> bool:
    if not command:
        return False
    if not command_uses_wsl(command, wsl_command=wsl_command, distribution=distribution):
        return False
    exe = command_exec(command)
    if exe == "sh":
        return True
    if exe == "git":
        return True
    if exe == "env":
        try:
            exec_index = command.index("--exec")
        except ValueError:
            return False
        argv = command[exec_index + 1 :]
        ceiling = f"GIT_CEILING_DIRECTORIES={site_git_ceiling_wsl(target)}"
        return len(argv) >= 3 and argv[0] == "env" and argv[1] == ceiling and argv[2] == "git"
    return False




def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    progress = get_progress(args)
    root = repo_root()
    builder_root = Path(args.builder_root).expanduser().resolve() if args.builder_root else root
    wsl_command = args.wsl_command
    distribution = args.distribution

    site_id, selection = select_site_id(
        builder_root=builder_root,
        explicit_site=args.site,
        create_if_missing=not bool(args.no_create_if_missing),
    )
    if progress is not None:
        progress.log(
            "START golden website path smoke",
            builder_root=str(builder_root),
            site_id=site_id,
            site_selection=selection,
            wsl_command=wsl_command,
            distribution=distribution,
        )

    ensure_result, ensure_payload = ensure_builder_debug_site(
        root=root,
        builder_root=builder_root,
        site_id=site_id,
        timeout=args.timeout_seconds,
        progress=progress,
    )
    site_path = Path(str(ensure_payload.get("site_path") or (builder_websites_root(builder_root) / site_id)))
    target = resolve_builder_site_target(builder_root=builder_root, site_id=site_id, site_path=site_path)
    if progress is not None:
        progress.log(
            "Builder debug site resolved",
            ensure_ok=ensure_result.ok and bool(ensure_payload.get("ok")),
            site_path=str(site_path),
            target_ok=target.ok,
            site_wsl_path=target.site_wsl_path,
            target_reason=target.reason,
        )

    blessed_output = Path(tempfile.gettempdir()) / "mc_golden_website_path_blessed_editor" / time.strftime("%Y%m%d_%H%M%S")
    blessed_output.mkdir(parents=True, exist_ok=True)

    capability = wsl_shell(
        script="set -eu\ncommand -v git\ngit --version\ncommand -v python3\npython3 --version",
        wsl_command=wsl_command,
        distribution=distribution,
        timeout=args.timeout_seconds,
        progress=progress,
        label="WSL capability check",
    )
    setup = wsl_shell(
        script=ensure_site_git_script(target=target, ai_branch=AI_BRANCH),
        wsl_command=wsl_command,
        distribution=distribution,
        timeout=args.timeout_seconds,
        progress=progress,
        label="WSL ensure builder site Git repo",
    ) if ensure_result.ok and bool(ensure_payload.get("ok")) and target.ok and capability.ok else CommandResult(
        [], 127, "", "skipped because builder ensure, target validation, or WSL capability failed"
    )

    blessed_report = (
        run_blessed_generated_editor_patch_artifact(
            root=root,
            source_site_dir=site_path,
            request=EDIT_REQUEST,
            output_root=blessed_output,
            args=args,
        )
        if ensure_result.ok and bool(ensure_payload.get("ok")) and target.ok
        else {"ok": False, "issues": ["builder debug site resolution failed before AI path"]}
    )

    artifact_report = blessed_report.get("artifact_packaging") if isinstance(blessed_report.get("artifact_packaging"), dict) else {}
    full_file_report = blessed_report.get("full_file_promotion") if isinstance(blessed_report.get("full_file_promotion"), dict) else {}
    artifact_path_text = str(artifact_report.get("artifact_path") or "")
    zip_path = Path(artifact_path_text) if artifact_path_text else (blessed_output / "missing_patch_artifact.zip")
    members = patch_members(zip_path) if artifact_path_text and zip_path.is_file() else []
    expected_after_sha = str(full_file_report.get("after_sha256") or "")
    ai_target_file = str(blessed_report.get("selected_target_file") or "")

    commands = site_git_preflight_commands(target=target, wsl_command=wsl_command, distribution=distribution) if target.ok else {}
    before = run_preflight(commands, timeout=args.timeout_seconds, progress=progress, label_prefix="before preflight") if setup.ok else {}
    before_parsed = parse_git(before, fixture=target.site_wsl_path or "") if before else {}

    dry_cmd = new_patch_command_for_site(root=root, zip_path=zip_path, target=target, wsl_command=wsl_command, distribution=distribution, dry_run=True) if target.ok else []
    blessed_not_ready_reason = blessed_artifact_not_ready_reason(blessed_report, setup_ok=setup.ok)
    dry = run(dry_cmd, timeout=args.timeout_seconds, progress=progress, label="new_patch dry-run") if setup.ok and blessed_report.get("ok") is True else CommandResult(
        dry_cmd, 127, "", blessed_not_ready_reason
    )

    after_dry = run_preflight(commands, timeout=args.timeout_seconds, progress=progress, label_prefix="after dry-run preflight") if dry.ok else {}
    after_dry_parsed = parse_git(after_dry, fixture=target.site_wsl_path or "") if after_dry else {}

    apply_cmd = new_patch_command_for_site(root=root, zip_path=zip_path, target=target, wsl_command=wsl_command, distribution=distribution, dry_run=False) if target.ok else []
    applied = run(apply_cmd, timeout=args.timeout_seconds, progress=progress, label="new_patch apply") if dry.ok else CommandResult(apply_cmd, 127, "", "skipped because dry-run failed")

    post_apply = run_preflight(commands, timeout=args.timeout_seconds, progress=progress, label_prefix="post-apply preflight") if applied.ok else {}
    post_apply_parsed = parse_git(post_apply, fixture=target.site_wsl_path or "") if post_apply else {}

    diff_cmd = wsl_site_git(target=target, git_args=["diff", "--", "index.html"], wsl_command=wsl_command, distribution=distribution) if target.ok else []
    diff = run(diff_cmd, timeout=args.timeout_seconds, progress=progress, label="Git diff index.html") if applied.ok else CommandResult(diff_cmd, 127, "", "skipped because apply failed")

    read_index = wsl_shell(
        script=f"set -eu\ncat {shell_quote((target.site_wsl_path or '') + '/index.html')}",
        wsl_command=wsl_command,
        distribution=distribution,
        timeout=args.timeout_seconds,
        progress=progress,
        label="Read applied index.html for SHA verification",
    ) if applied.ok and target.site_wsl_path else CommandResult([], 127, "", "skipped because apply failed")

    file_matches_blessed_output = bool(expected_after_sha and sha256_text(read_index.stdout) == expected_after_sha)

    commit_cmd = commit_command_for_site(target=target, wsl_command=wsl_command, distribution=distribution) if target.ok else []
    committed = run(commit_cmd, timeout=args.timeout_seconds, progress=progress, label="Git commit validated edit") if applied.ok and file_matches_blessed_output else CommandResult(
        commit_cmd, 127, "", "skipped because apply validation failed"
    )

    post_commit = run_preflight(commands, timeout=args.timeout_seconds, progress=progress, label_prefix="post-commit preflight") if committed.ok else {}
    post_commit_parsed = parse_git(post_commit, fixture=target.site_wsl_path or "") if post_commit else {}

    show_commit_cmd = wsl_site_git(
        target=target,
        git_args=["show", "--stat", "--name-only", "--format=%s", "HEAD", "--", "index.html"],
        wsl_command=wsl_command,
        distribution=distribution,
    ) if target.ok else []
    show_commit = run(show_commit_cmd, timeout=args.timeout_seconds, progress=progress, label="Git show committed edit") if committed.ok else CommandResult([], 127, "", "skipped because commit failed")

    show_patch_cmd = wsl_site_git(
        target=target,
        git_args=["show", "--format=", "--patch", "HEAD", "--", "index.html"],
        wsl_command=wsl_command,
        distribution=distribution,
    ) if target.ok else []
    show_patch = run(show_patch_cmd, timeout=args.timeout_seconds, progress=progress, label="Git show committed patch") if committed.ok else CommandResult([], 127, "", "skipped because commit failed")

    status_after_apply = str(post_apply_parsed.get("status_porcelain", ""))
    starting_commit = str(before_parsed.get("commit", ""))
    final_commit = str(post_commit_parsed.get("commit", ""))

    model_call_reports = [
        (blessed_report.get("discovery") or {}).get("model_call"),
        (blessed_report.get("grounding") or {}).get("model_call"),
        (blessed_report.get("patch_proposal") or {}).get("model_call"),
    ]
    model_calls_ok = all(isinstance(item, dict) and item.get("ok") is True for item in model_call_reports)
    git_command_values = [*commands.values(), diff_cmd, commit_cmd, show_commit_cmd, show_patch_cmd]
    new_patch_commands = [dry_cmd, apply_cmd]

    checks = {
        "site_selection_is_site_scoped": selection.get("source") in {"explicit", "most_recent_debug_golden_path_site", "generated_no_existing_debug_golden_path_site"},
        "builder_ensure_ok": ensure_result.ok and bool(ensure_payload.get("ok")),
        "builder_ensure_targeted_selected_site": ensure_payload.get("site_id") == site_id,
        "builder_repo_relative_path_is_site": ensure_payload.get("repo_relative_path") == f"runtime/websites/{site_id}",
        "target_is_builder_runtime_debug_golden_site": target.ok and target.site_id == site_id,
        "target_is_not_install_hub": "install/hub" not in norm_wsl(target.site_wsl_path or ""),
        "wsl_capability_git_and_python_found": capability.ok,
        "site_git_repo_ready": setup.ok,
        "before_preflight_inside_git_repo": bool(before_parsed.get("is_inside_work_tree")),
        "before_preflight_clean": bool(before_parsed.get("working_tree_clean")),
        "blessed_generated_editor_path_ok": blessed_report.get("ok") is True,
        "blessed_path_hit_ai_model_calls": model_calls_ok and int(blessed_report.get("model_stage_count") or 0) >= 3,
        "blessed_terminal_result_promotable": (blessed_report.get("terminal_result") or {}).get("promotable") is True,
        "blessed_selected_target_is_index_html": ai_target_file == "index.html",
        "artifact_packaged_by_blessed_path": bool(artifact_report.get("ok") and artifact_report.get("artifact_ready")),
        "patch_zip_created_with_changed_file_only": zip_path.exists() and len(members) == 1 and members[0].endswith("/index.html"),
        "all_git_commands_use_wsl_executor": all(
            git_command_is_site_scoped(c, target=target, wsl_command=wsl_command, distribution=distribution)
            for c in git_command_values
            if c
        ),
        "site_git_top_level_is_selected_site": bool(before_parsed.get("top_level_matches_fixture")),
        "new_patch_commands_run_through_wsl": all(command_uses_wsl(c, wsl_command=wsl_command, distribution=distribution) and command_exec(c) == "python3" for c in new_patch_commands if c),
        "new_patch_target_roots_are_selected_site": all(target_root_ok(c, target=target) for c in new_patch_commands if c),
        "dry_run_ok": dry.ok,
        "dry_run_changed_one_file": "changed_files: 1" in dry.stdout,
        "dry_run_did_not_modify_worktree": bool(after_dry_parsed.get("working_tree_clean")),
        "apply_ok": applied.ok,
        "post_apply_status_tracks_only_index": status_after_apply.strip() == "M index.html",
        "post_apply_file_matches_blessed_generated_output": file_matches_blessed_output,
        "commit_created_after_validation": committed.ok and file_matches_blessed_output,
        "post_commit_worktree_clean": bool(post_commit_parsed.get("working_tree_clean")),
        "post_commit_hash_changed": bool(starting_commit and final_commit and starting_commit != final_commit),
        "post_commit_branch_still_ai_branch": post_commit_parsed.get("branch") == AI_BRANCH,
        "post_commit_message_recorded": COMMIT_MESSAGE in show_commit.stdout,
        "post_commit_includes_index_html": "index.html" in show_commit.stdout,
        "post_commit_patch_includes_index_diff": "diff --git" in show_patch.stdout and "index.html" in show_patch.stdout,
        "committed_debug_site_true": committed.ok,
        "committed_install_or_hub_repo_false": True,
    }

    failed_checks = [name for name, passed in checks.items() if not passed]
    report = {
        "name": "golden_website_path_site_scoped_rag_edit_zip_apply_git_commit",
        "ok": all(checks.values()),
        "mode": MODE,
        "platform": platform.platform(),
        "builder_root": str(builder_root),
        "site_id": site_id,
        "site_selection": selection,
        "site_path": str(site_path),
        "site_wsl_path": target.site_wsl_path,
        "target": target.__dict__,
        "request": EDIT_REQUEST,
        "ai_branch": AI_BRANCH,
        "wsl_command": wsl_command,
        "wsl_distribution": distribution,
        "debug_website_builder_result": result_json(ensure_result),
        "debug_website_builder_payload": ensure_payload,
        "capability_result": result_json(capability),
        "setup_result": result_json(setup),
        "blessed_generated_editor_report": blessed_report,
        "patch_zip_path": str(zip_path),
        "patch_zip_wsl_path": host_path_to_wsl(zip_path) if zip_path.exists() else "",
        "patch_zip_members": members,
        "blessed_not_ready_reason": blessed_not_ready_reason,
        "before_preflight": {k: result_json(v) for k, v in before.items()},
        "before_parsed_git": before_parsed,
        "dry_run_result": result_json(dry),
        "after_dry_run_preflight": {k: result_json(v) for k, v in after_dry.items()},
        "after_dry_run_parsed_git": after_dry_parsed,
        "apply_result": result_json(applied),
        "post_apply_preflight": {k: result_json(v) for k, v in post_apply.items()},
        "post_apply_parsed_git": post_apply_parsed,
        "post_apply_diff_result": result_json(diff),
        "post_apply_index_html_result": result_json(read_index),
        "commit_result": result_json(committed),
        "post_commit_preflight": {k: result_json(v) for k, v in post_commit.items()},
        "post_commit_parsed_git": post_commit_parsed,
        "post_commit_show_result": result_json(show_commit),
        "post_commit_patch_result": result_json(show_patch),
        "checks": checks,
        "failed_checks": failed_checks,
        "zip_snapshot_requested": True,
        "automatic_edit_pathway": True,
        "blessed_ai_edit_required": True,
        "site_scoped_builder_path": True,
        "defaulted_to_most_recent_debug_site": selection.get("source") == "most_recent_debug_golden_path_site",
        "created_site_when_missing": selection.get("source") == "generated_no_existing_debug_golden_path_site",
        "committed_debug_site": committed.ok,
        "committed_install_or_hub_repo": False,
        "progress_event_count": len(progress.events) if progress is not None else 0,
        "progress_events_tail": progress.events[-25:] if progress is not None else [],
    }
    if progress is not None:
        progress.log(
            "DONE golden website path smoke" if report["ok"] else "FAIL golden website path smoke",
            ok=report["ok"],
            failed_check_count=len(failed_checks),
            failed_checks=failed_checks[:12],
            site_id=site_id,
            site_wsl_path=target.site_wsl_path,
            patch_zip_path=str(zip_path),
        )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the site-scoped golden website RAG path smoke.")
    parser.add_argument("--site", default=None, help="Optional debug-golden-path-* site id. Defaults to the most recent existing debug golden-path site.")
    parser.add_argument("--builder-root", default=None, help="Website-builder repo root. Defaults to this repository root.")
    parser.add_argument("--no-create-if-missing", action="store_true", help="Fail instead of creating a new debug-golden-path-* site when none exists.")
    parser.add_argument("--wsl-command", default=DEFAULT_WSL_COMMAND)
    parser.add_argument("--distribution", default=DEFAULT_WSL_DISTRIBUTION)
    parser.add_argument("--locked-hub-root", default=DEFAULT_LOCKED_HUB_ROOT)
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--ai-timeout-seconds", type=int, default=600)
    parser.add_argument("--model", default=None, help="Ollama model for the blessed generated-editor pathway.")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434/api/generate")
    parser.add_argument("--num-predict", type=int, default=900)
    parser.add_argument("--format-mode", choices=["json", "none"], default="none")
    parser.add_argument("--think-mode", choices=["omit", "false", "true", "low", "medium", "high"], default="false")
    parser.add_argument("--max-index-files", type=int, default=8)
    parser.add_argument("--max-excerpts-per-file", type=int, default=3)
    parser.add_argument("--excerpt-window-lines", type=int, default=3)
    parser.add_argument("--max-excerpt-chars", type=int, default=1200)
    parser.add_argument("--max-file-read-chars", type=int, default=200000)
    parser.add_argument("--max-evidence-chars", type=int, default=16000)
    parser.add_argument("--discovery-repair-attempts", type=int, default=1)
    parser.add_argument("--discovery-repair-source-chars", type=int, default=12000)
    parser.add_argument("--discovery-anchor-option-repair-attempts", type=int, default=1)
    parser.add_argument("--discovery-anchor-option-count", type=int, default=48)
    parser.add_argument("--grounding-repair-attempts", type=int, default=1)
    parser.add_argument("--patch-proposal-repair-attempts", type=int, default=2)
    parser.add_argument("--progress-interval-seconds", type=float, default=15.0)
    parser.add_argument("--quiet", action="store_true", help="Suppress progress stderr and print only final JSON on stdout.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.progress = ProgressReporter(enabled=not bool(args.quiet), interval_seconds=args.progress_interval_seconds)
    try:
        report = evaluate(args)
    except (ValueError, OSError) as exc:
        report = {
            "name": "golden_website_path_site_scoped_rag_edit_zip_apply_git_commit",
            "ok": False,
            "mode": MODE,
            "error": str(exc),
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
