#!/usr/bin/env python3
"""Evidence-based Git dirty-state planner.

This tool intentionally asks Git where the repository is. It does not trust
that a .git directory is present, absent, or in the expected place on disk.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ACTION_CATALOG: list[dict[str, Any]] = [
    {"id": "find_repository_root", "label": "Find repository root", "git_name": "detect repo", "kind": "repository", "safe": True, "destructive": False, "reversible": True},
    {"id": "choose_correct_repository_root", "label": "Choose correct repository root", "git_name": "select root", "kind": "repository", "safe": True, "destructive": False, "reversible": True},
    {"id": "start_tracking_this_folder", "label": "Start tracking this folder", "git_name": "git init", "kind": "repository", "safe": True, "destructive": False, "reversible": True},
    {"id": "initialize_repository_here", "label": "Initialize repository here", "git_name": "git init", "kind": "head_fix", "safe": True, "destructive": False, "reversible": True, "requires_user": True, "blocks_progress": True},
    {"id": "create_initial_snapshot", "label": "Create initial snapshot / first commit", "git_name": "first commit", "kind": "head_fix", "safe": True, "destructive": False, "reversible": True, "requires_user": True, "blocks_progress": True},
    {"id": "prepare_commit_snapshot", "label": "Take Snapshot / Commit", "git_name": "commit", "kind": "commit", "safe": True, "destructive": False, "reversible": True, "requires_user": True, "blocks_progress": True},
    {"id": "update_gitignore_before_initial_commit", "label": "Clean up .gitignore before first commit", "git_name": ".gitignore first", "kind": "ignore", "safe": True, "destructive": False, "reversible": True, "requires_user": True, "blocks_progress": True},
    {"id": "secrets_filter", "label": "Secrets / Filter", "git_name": "security filter", "kind": "workflow", "safe": True, "destructive": False, "reversible": True, "requires_user": True, "blocks_progress": True},
    {"id": "keep_parent_repository", "label": "Keep parent repository", "git_name": "use parent repo", "kind": "repository", "safe": True, "destructive": False, "reversible": True},
    {"id": "create_nested_repository_here", "label": "Create nested repository here", "git_name": "nested git init", "kind": "repository", "safe": False, "destructive": False, "reversible": True, "requires": ["choose_correct_repository_root"]},
    {"id": "stop_until_repository_is_clear", "label": "Stop until repository is clear", "git_name": "abort", "kind": "repository", "safe": True, "destructive": False, "reversible": True},
    {"id": "save_current_state", "label": "Save current state", "git_name": "snapshot", "kind": "safety", "safe": True, "destructive": False, "reversible": True},
    {"id": "put_changes_on_shelf", "label": "Put changes on shelf", "git_name": "stash", "kind": "safety", "safe": True, "destructive": False, "reversible": True},
    {"id": "make_safety_branch", "label": "Make safety branch", "git_name": "branch", "kind": "safety", "safe": True, "destructive": False, "reversible": True},
    {"id": "record_current_work_as_commit", "label": "Record current work as commit", "git_name": "commit", "kind": "safety", "safe": True, "destructive": False, "reversible": True},
    {"id": "export_recovery_patch", "label": "Export recovery patch", "git_name": "patch", "kind": "safety", "safe": True, "destructive": False, "reversible": True},
    {"id": "archive_untracked_files", "label": "Archive untracked files", "git_name": "archive", "kind": "safety", "safe": True, "destructive": False, "reversible": True},
    {"id": "list_saved_states", "label": "List saved states", "git_name": "list snapshots", "kind": "safety", "safe": True, "destructive": False, "reversible": True},
    {"id": "restore_saved_state", "label": "Restore saved state", "git_name": "restore snapshot", "kind": "safety", "safe": False, "destructive": True, "reversible": True, "requires": ["save_current_state"]},
    {"id": "restore_shelved_changes", "label": "Restore shelved changes", "git_name": "stash apply/pop", "kind": "safety", "safe": False, "destructive": False, "reversible": True},
    {"id": "measure_dirty_state", "label": "Measure dirty state", "git_name": "status", "kind": "analysis", "safe": True, "destructive": False, "reversible": True},
    {"id": "make_cleanup_plan", "label": "Make cleanup plan", "git_name": "plan", "kind": "analysis", "safe": True, "destructive": False, "reversible": True},
    {"id": "classify_changed_files", "label": "Classify changed files", "git_name": "classify", "kind": "analysis", "safe": True, "destructive": False, "reversible": True},
    {"id": "find_blocking_problems", "label": "Find blocking problems", "git_name": "blockers", "kind": "analysis", "safe": True, "destructive": False, "reversible": True},
    {"id": "rank_cleanup_risk", "label": "Rank cleanup risk", "git_name": "risk score", "kind": "analysis", "safe": True, "destructive": False, "reversible": True},
    {"id": "explain_each_dirty_item", "label": "Explain each dirty item", "git_name": "explain", "kind": "analysis", "safe": True, "destructive": False, "reversible": True},
    {"id": "compare_to_remote_state", "label": "Compare to remote state", "git_name": "upstream compare", "kind": "analysis", "safe": True, "destructive": False, "reversible": True},
    {"id": "find_nested_repositories", "label": "Find nested repositories", "git_name": "nested repos", "kind": "analysis", "safe": True, "destructive": False, "reversible": True},
    {"id": "find_generated_artifacts", "label": "Find generated artifacts", "git_name": "generated files", "kind": "analysis", "safe": True, "destructive": False, "reversible": True},
    {"id": "preserve_local_only_files", "label": "Preserve local-only files", "git_name": "keep untracked", "kind": "preserve", "safe": True, "destructive": False, "reversible": True},
    {"id": "start_tracking_real_work", "label": "Start tracking real work", "git_name": "git add", "kind": "preserve", "safe": True, "destructive": False, "reversible": True},
    {"id": "track_selected_files", "label": "Track selected files", "git_name": "add paths", "kind": "preserve", "safe": True, "destructive": False, "reversible": True},
    {"id": "track_all_safe_source_files", "label": "Track all safe source files", "git_name": "add safe group", "kind": "preserve", "safe": True, "destructive": False, "reversible": True},
    {"id": "keep_changes_unstaged", "label": "Keep changes unstaged", "git_name": "leave as-is", "kind": "preserve", "safe": True, "destructive": False, "reversible": True},
    {"id": "keep_external_remote_as_origin", "label": "Keep external remote as origin", "git_name": "preserve origin", "kind": "remote", "safe": True, "destructive": False, "reversible": True},
    {"id": "add_local_server_remote", "label": "Add local server remote", "git_name": "add local remote", "kind": "remote", "safe": True, "destructive": False, "reversible": True},
    {"id": "switch_origin_to_local_server", "label": "Switch origin to local server", "git_name": "set origin", "kind": "remote", "safe": False, "destructive": False, "reversible": True},
    {"id": "ignore_generated_files", "label": "Ignore generated files", "git_name": ".gitignore", "kind": "ignore", "safe": True, "destructive": False, "reversible": True},
    {"id": "ignore_selected_paths", "label": "Ignore selected paths", "git_name": "gitignore paths", "kind": "ignore", "safe": True, "destructive": False, "reversible": True},
    {"id": "ignore_local_environment_files", "label": "Ignore local environment files", "git_name": "env ignore", "kind": "ignore", "safe": True, "destructive": False, "reversible": True},
    {"id": "ignore_debug_output", "label": "Ignore debug output", "git_name": "debug ignore", "kind": "ignore", "safe": True, "destructive": False, "reversible": True},
    {"id": "separate_real_work_from_noise", "label": "Separate real work from noise", "git_name": "split", "kind": "ignore", "safe": True, "destructive": False, "reversible": True},
    {"id": "unstage_selected_changes", "label": "Unstage selected changes", "git_name": "unstage", "kind": "cleanup", "safe": True, "destructive": False, "reversible": True},
    {"id": "unstage_everything", "label": "Unstage everything", "git_name": "reset staged", "kind": "cleanup", "safe": True, "destructive": False, "reversible": True},
    {"id": "discard_unstaged_tracked_changes", "label": "Discard unstaged tracked changes", "git_name": "restore worktree", "kind": "cleanup", "safe": False, "destructive": True, "reversible": True, "requires": ["save_current_state"]},
    {"id": "discard_selected_file_changes", "label": "Discard selected file changes", "git_name": "restore paths", "kind": "cleanup", "safe": False, "destructive": True, "reversible": True, "requires": ["save_current_state"]},
    {"id": "remove_untracked_generated_files", "label": "Remove generated untracked files", "git_name": "clean generated", "kind": "cleanup", "safe": False, "destructive": True, "reversible": True, "requires": ["save_current_state"]},
    {"id": "remove_selected_untracked_files", "label": "Remove selected untracked files", "git_name": "clean paths", "kind": "cleanup", "safe": False, "destructive": True, "reversible": True, "requires": ["save_current_state"]},
    {"id": "reset_to_last_commit", "label": "Reset to last commit", "git_name": "hard reset", "kind": "cleanup", "safe": False, "destructive": True, "reversible": True, "requires": ["save_current_state"]},
    {"id": "reset_to_remote_branch", "label": "Reset to remote branch", "git_name": "reset upstream", "kind": "cleanup", "safe": False, "destructive": True, "reversible": True, "requires": ["save_current_state"]},
    {"id": "show_merge_conflicts", "label": "Show merge conflicts", "git_name": "conflict status", "kind": "conflict", "safe": True, "destructive": False, "reversible": True},
    {"id": "keep_our_version", "label": "Keep our version", "git_name": "ours", "kind": "conflict", "safe": False, "destructive": True, "reversible": True, "requires": ["save_current_state"]},
    {"id": "keep_their_version", "label": "Keep their version", "git_name": "theirs", "kind": "conflict", "safe": False, "destructive": True, "reversible": True, "requires": ["save_current_state"]},
    {"id": "open_conflict_for_manual_fix", "label": "Open conflict for manual fix", "git_name": "manual resolve", "kind": "conflict", "safe": True, "destructive": False, "reversible": True},
    {"id": "abort_merge_or_rebase", "label": "Abort merge or rebase", "git_name": "abort operation", "kind": "conflict", "safe": False, "destructive": True, "reversible": True, "requires": ["save_current_state"]},
    {"id": "continue_merge_or_rebase", "label": "Continue merge or rebase", "git_name": "continue operation", "kind": "conflict", "safe": False, "destructive": False, "reversible": True},
    {"id": "move_work_to_new_branch", "label": "Move work to new branch", "git_name": "checkout -b", "kind": "workflow", "safe": True, "destructive": False, "reversible": True},
    {"id": "save_work_then_return_to_main", "label": "Save work then return to main", "git_name": "branch + commit/stash", "kind": "workflow", "safe": True, "destructive": False, "reversible": True},
    {"id": "create_clean_worktree_copy", "label": "Create clean worktree copy", "git_name": "git worktree", "kind": "workflow", "safe": True, "destructive": False, "reversible": True},
    {"id": "clone_fresh_copy_for_comparison", "label": "Clone fresh copy for comparison", "git_name": "fresh clone", "kind": "workflow", "safe": True, "destructive": False, "reversible": True},
    {"id": "extract_dirty_state_as_patch_bundle", "label": "Extract dirty state as patch bundle", "git_name": "patch bundle", "kind": "workflow", "safe": True, "destructive": False, "reversible": True},
    {"id": "inspect_configured_remotes", "label": "Inspect configured remotes", "git_name": "remote -v", "kind": "remote", "safe": True, "destructive": False, "reversible": True},
    {"id": "preserve_github_remote", "label": "Preserve GitHub remote", "git_name": "keep GitHub origin", "kind": "remote", "safe": True, "destructive": False, "reversible": True},
    {"id": "use_local_gitea_as_extra_remote", "label": "Use local Gitea as extra remote", "git_name": "add local", "kind": "remote", "safe": True, "destructive": False, "reversible": True},
    {"id": "use_local_gitea_as_primary_remote", "label": "Use local Gitea as primary remote", "git_name": "set origin local", "kind": "remote", "safe": False, "destructive": False, "reversible": True},
    {"id": "push_current_branch_to_local_server", "label": "Push current branch to local server", "git_name": "push local", "kind": "remote", "safe": False, "destructive": False, "reversible": True},
    {"id": "push_current_branch_to_external_server", "label": "Push current branch to external server", "git_name": "push external", "kind": "remote", "safe": False, "destructive": False, "reversible": True},
    {"id": "setup_local_server_repository", "label": "Set up local server repository", "git_name": "create Gitea repo", "kind": "remote", "safe": True, "destructive": False, "reversible": True},
    {"id": "mirror_local_server_to_external", "label": "Mirror local server to external", "git_name": "push mirror", "kind": "remote", "safe": False, "destructive": False, "reversible": True},
    {"id": "apply_one_plan_step", "label": "Apply one plan step", "git_name": "apply step", "kind": "execution", "safe": False, "destructive": False, "reversible": True},
    {"id": "apply_safe_steps_only", "label": "Apply safe steps only", "git_name": "apply safe", "kind": "execution", "safe": True, "destructive": False, "reversible": True},
    {"id": "apply_full_plan_with_confirmations", "label": "Apply full plan with confirmations", "git_name": "apply plan", "kind": "execution", "safe": False, "destructive": True, "reversible": True, "requires": ["save_current_state"]},
    {"id": "cancel_running_action", "label": "Cancel running action", "git_name": "cancel", "kind": "execution", "safe": True, "destructive": False, "reversible": True},
    {"id": "refresh_action_log", "label": "Refresh action log", "git_name": "operation log", "kind": "execution", "safe": True, "destructive": False, "reversible": True},
    {"id": "record_action_result", "label": "Record action result", "git_name": "log result", "kind": "execution", "safe": True, "destructive": False, "reversible": True},
]

STRATEGIES = [
    "do_nothing_until_repo_is_clear",
    "initialize_then_recheck",
    "initialize_repository_here_then_recheck",
    "prepare_gitignore_then_create_initial_snapshot",
    "create_initial_snapshot_then_recheck",
    "snapshot_then_classify",
    "preserve_then_clean_generated_noise",
    "commit_real_work_then_clean_noise",
    "shelve_work_then_test_clean_tree",
    "split_work_into_branch",
    "keep_external_origin_add_local_remote",
    "local_first_external_second",
    "manual_review_required",
]

SOURCE_EXTS = {
    ".py", ".ps1", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss", ".md", ".json",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".txt", ".sql", ".go", ".rs", ".java", ".c",
    ".cpp", ".h", ".hpp", ".cs", ".sh", ".bat", ".dockerfile",
}
GENERATED_PATTERNS = [
    r"(^|/)(__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|node_modules|dist|build|coverage|tmp|temp|\.tmp)(/|$)",
    r"(^|/)(logs?|reports?|harness_output[^/]*|diagnostics_output[^/]*|debug_assets?|debug_asset_revisions|generated_component_docs|smoke-output|debug-output|trace-output)(/|$)",
    r"(^|/)(aider\.log|rag_smoke_logpack_runs|ollama_prompt_space_[^/]*)(/|$)",
    r"(^|/)(contracts?_.*_sol_.*|test_.*_t_sol_.*)\.bin$",
    r"\.(pyc|pyo|log|tmp|bak|swp|cache|coverage|zip|tar|gz|7z|db-journal|comp)$",
    r"(^|/)(debug.*\.txt|.*\.trace|.*\.prof)$",
]
LOCAL_ENV_PATTERNS = [
    r"(^|/)\.env(\..*)?$",
    r"(^|/)\.main_computer_.*\.pid$",
    r"(^|/).*\.pid$",
    r"(^|/)[^/]*\.lock$",
    r"(^|/)runtime(/|$)",
    r"(^|/)main_computer/\.main_computer_browser_profile(/|$)",
    r"(^|/).*secret.*",
    r"(^|/).*token.*",
    r"(^|/).*credential.*",
    r"(^|/)id_rsa$",
    r"(^|/)id_ed25519$",
]
GENERATED_DIR_FAMILY_TOKENS = {
    "artifact",
    "artifacts",
    "build",
    "cache",
    "coverage",
    "debug",
    "diagnostic",
    "diagnostics",
    "dist",
    "generated",
    "harness",
    "log",
    "logs",
    "output",
    "outputs",
    "report",
    "reports",
    "result",
    "results",
    "run",
    "runs",
    "snapshot",
    "snapshots",
    "temp",
    "tmp",
    "trace",
    "traces",
}
CONFIG_NAMES = {".gitignore", "Dockerfile", "docker-compose.yml", "docker-compose.dev.yml", "requirements.txt", "pyproject.toml", "package.json", "package-lock.json"}

COMMIT_IDENTITY_PLACEHOLDER_NAMES = {
    "",
    "your name",
    "you",
    "unknown",
    "none",
    "todo",
    "change me",
    "changeme",
    "example",
}
COMMIT_IDENTITY_PLACEHOLDER_EMAILS = {
    "",
    "you@example.com",
    "your.email@example.com",
    "example@example.com",
    "unknown@example.com",
    "none@example.com",
    "todo@example.com",
    "change-me@example.com",
    "changeme@example.com",
}
EMAIL_SHAPE_RE = re.compile(r"^[^@\s]+@[^@\s]+$")
DEFAULT_COMMIT_MESSAGE = "Take project snapshot"
COMMIT_CARD_SCHEMA_VERSION = 1
SECURITY_POLICY_VERSION = 1
SECURITY_RULES_POLICY_PATH = ".git_dirty_rules.json"
PRIVACY_SCAN_MAX_BYTES = 1024 * 1024
PRIVACY_SCAN_MAX_FINDINGS_PER_FILE = 8
PRIVACY_SCAN_MAX_TOTAL_FINDINGS = 200
PRIVACY_EVIDENCE_EDGE_CHARS = 100
PRIVACY_EVIDENCE_TRUNCATE_THRESHOLD = 202
WINDOWS_USER_PATH_ALLOWED_EXAMPLE_USERS = {"you"}
PRIVATE_KEY_PATTERN = r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
JWT_PATTERN = r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
OPENAI_KEY_PATTERN = r"\bsk-[A-Za-z0-9_-]{20,}\b"
GITHUB_TOKEN_PATTERN = r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"
AWS_ACCESS_KEY_PATTERN = r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
SLACK_TOKEN_PATTERN = r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"
STRIPE_SECRET_PATTERN = r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}\b"
DISCORD_WEBHOOK_PATTERN = r"https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+"
SECRET_ASSIGNMENT_PATTERN = (
    r"(?i)\b(?:api[_-]?key|access[_-]?key|secret|token|password|passwd|pwd|credential|client[_-]?secret|private[_-]?key)\b"
    r"\s*[:=]\s*['\"]?([^'\"\s,;}\]]{6,})"
)
WINDOWS_USER_PATH_PATTERN = (
    r"(?i)(?:"
    r"\b[A-Z]:(?:\\|/)(?:Users|Documents and Settings)(?:\\|/)[^\\/\r\n\s\"'<>|]+(?:[\\/][^\s\"'<>|]+)*"
    r"|%(?:USERPROFILE|APPDATA|LOCALAPPDATA)%(?:[\\/][^\s\"'<>|]+)*"
    r")"
)
UNIX_USER_PATH_PATTERN = r"(?<![\w.:-])(?:file:)?/(?:Users|home)/([^/\s\"\'<>|]+)/[^\s\"\'<>|]+"
TILDE_USER_PATH_PATTERN = r"(?<![\w.-])~(?:/[^\s\"'<>|]+)+"
EMAIL_IN_TEXT_PATTERN = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
HIGH_ENTROPY_PATTERN = r"(?<![A-Za-z0-9_])([A-Za-z0-9+/=_-]{48,})(?![A-Za-z0-9_])"
LOCAL_CONTEXT_PATTERN = (
    r"(?i)(?:"
    r"\b(?:user|username|home|userprofile|appdata|localappdata|gitconfig|environment|env|stack trace|transcript)\b"
    r"|(?:\\|/)(?:Users|home)(?:\\|/)"
    r"|Documents and Settings"
    r")"
)
LOCAL_USER_ASSIGNMENT_PATTERN = r"(?i)\b(?:user|username|user\.name|login|owner)\b\s*[:=]\s*['\"]?([A-Za-z][A-Za-z0-9_.-]{1,63})"

PRIVATE_KEY_RE = re.compile(PRIVATE_KEY_PATTERN)
JWT_RE = re.compile(JWT_PATTERN)
OPENAI_KEY_RE = re.compile(OPENAI_KEY_PATTERN)
GITHUB_TOKEN_RE = re.compile(GITHUB_TOKEN_PATTERN)
AWS_ACCESS_KEY_RE = re.compile(AWS_ACCESS_KEY_PATTERN)
SLACK_TOKEN_RE = re.compile(SLACK_TOKEN_PATTERN)
STRIPE_SECRET_RE = re.compile(STRIPE_SECRET_PATTERN)
DISCORD_WEBHOOK_RE = re.compile(DISCORD_WEBHOOK_PATTERN, re.IGNORECASE)
SECRET_ASSIGNMENT_RE = re.compile(SECRET_ASSIGNMENT_PATTERN)
WINDOWS_USER_PATH_RE = re.compile(WINDOWS_USER_PATH_PATTERN)
WINDOWS_USER_NAME_RE = re.compile(r"(?i)\b[A-Z]:(?:\\|/)(?:Users|Documents and Settings)(?:\\|/)([^\\/\r\n\s\"'<>|]+)")
UNIX_USER_PATH_RE = re.compile(UNIX_USER_PATH_PATTERN)
TILDE_USER_PATH_RE = re.compile(TILDE_USER_PATH_PATTERN)
EMAIL_IN_TEXT_RE = re.compile(EMAIL_IN_TEXT_PATTERN)
HIGH_ENTROPY_RE = re.compile(HIGH_ENTROPY_PATTERN)
LOCAL_CONTEXT_RE = re.compile(LOCAL_CONTEXT_PATTERN)
LOCAL_USER_ASSIGNMENT_RE = re.compile(LOCAL_USER_ASSIGNMENT_PATTERN)


@dataclass
class GitResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def run_git(path: Path, args: list[str], *, check: bool = False) -> GitResult:
    command = ["git", "-C", str(path), *args]
    cp = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    result = GitResult(command=command, returncode=cp.returncode, stdout=cp.stdout, stderr=cp.stderr)
    if check and cp.returncode != 0:
        raise RuntimeError(cp.stderr or cp.stdout or f"git failed: {' '.join(command)}")
    return result


def run_raw_git(args: list[str]) -> GitResult:
    command = ["git", *args]
    cp = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    return GitResult(command=command, returncode=cp.returncode, stdout=cp.stdout, stderr=cp.stderr)


def selected_git_metadata_kind(path: Path) -> str:
    marker = path / ".git"
    if marker.is_dir():
        return "directory"
    if marker.is_file():
        return "file"
    if marker.exists():
        return "other"
    return "missing"


def selected_git_metadata_path(path: Path) -> str:
    marker = path / ".git"
    return str(marker.resolve()) if marker.exists() else ""


def inspect_selected_git_metadata(path: Path) -> dict[str, Any]:
    """Inspect only the selected directory's own .git marker.

    Git's normal discovery walks upward into parents. This helper deliberately
    does not. The project selector needs to know whether the selected directory
    itself is initialized, whether it is merely inside a parent repository, or
    whether a local .git marker is present but broken.
    """

    marker = path / ".git"
    kind = selected_git_metadata_kind(path)
    info: dict[str, Any] = {
        "exists": kind != "missing",
        "kind": kind,
        "path": str(marker.resolve()) if marker.exists() else "",
        "valid": False,
        "resolved_git_dir": "",
        "error": "",
    }
    if kind == "missing":
        return info
    if kind == "other":
        info["error"] = "selected .git marker is neither a directory nor a file"
        return info

    resolved = run_raw_git(["rev-parse", "--resolve-git-dir", str(marker)])
    info["raw_resolve_git_dir"] = asdict_result(resolved)
    if resolved.returncode != 0:
        info["error"] = (resolved.stderr or resolved.stdout or "selected .git marker is not a valid Git directory").strip()
        return info

    value = resolved.stdout.strip()
    resolved_path = Path(value)
    if not resolved_path.is_absolute():
        resolved_path = path / resolved_path
    info["valid"] = True
    info["resolved_git_dir"] = str(resolved_path.resolve())
    return info


def inspect_head(repo_path: Path) -> dict[str, Any]:
    """Return a precise HEAD state for initialized repositories."""

    verify = run_git(repo_path, ["rev-parse", "--verify", "-q", "HEAD"])
    symbolic = run_git(repo_path, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    git_path_head = run_git(repo_path, ["rev-parse", "--git-path", "HEAD"])

    branch_name = symbolic.stdout.strip() if symbolic.returncode == 0 else ""
    info: dict[str, Any] = {
        "has_head": verify.returncode == 0,
        "head_state": "present" if verify.returncode == 0 else ("unborn" if branch_name else "missing"),
        "head_oid": verify.stdout.strip() if verify.returncode == 0 else "",
        "branch_name": branch_name,
        "head_path": resolve_git_path(repo_path, git_path_head.stdout.strip()) if git_path_head.returncode == 0 else "",
        "raw": {
            "verify_head": asdict_result(verify),
            "symbolic_ref": asdict_result(symbolic),
            "git_path_head": asdict_result(git_path_head),
        },
    }
    return info



def git_config_get(repo_path: Path, args: list[str]) -> str:
    result = run_git(repo_path, ["config", *args])
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def classify_git_config_source(local_value: str, global_value: str, effective_value: str) -> str:
    if local_value:
        return "local"
    if global_value and (not effective_value or effective_value == global_value):
        return "global"
    if effective_value:
        return "effective"
    return "missing"


def commit_identity_empty() -> dict[str, Any]:
    return {
        "ready": False,
        "name": "",
        "email": "",
        "name_source": "missing",
        "email_source": "missing",
        "local": {"name": "", "email": ""},
        "global": {"name": "", "email": ""},
        "origins": {"name": "", "email": ""},
        "problems": ["missing_user_name", "missing_user_email"],
        "warnings": [],
        "recommended_scope": "local",
        "setup_commands": [
            'git config --local user.name "Your Name"',
            'git config --local user.email "you@example.com"',
        ],
        "detection_commands": [
            "git config --local --get user.name",
            "git config --local --get user.email",
            "git config --global --get user.name",
            "git config --global --get user.email",
            "git config --show-origin --get user.name",
            "git config --show-origin --get user.email",
        ],
    }


def inspect_commit_identity(repo_path: Path) -> dict[str, Any]:
    """Inspect the Git identity that would be used for a commit.

    Git can read identity from repository-local config, global config, or other
    configured scopes. The planner reports local/global values separately so the
    UI can guide this repository toward a repo-local identity without guessing.
    """

    local_name = git_config_get(repo_path, ["--local", "--get", "user.name"])
    local_email = git_config_get(repo_path, ["--local", "--get", "user.email"])
    global_name = git_config_get(repo_path, ["--global", "--get", "user.name"])
    global_email = git_config_get(repo_path, ["--global", "--get", "user.email"])
    effective_name = git_config_get(repo_path, ["--get", "user.name"])
    effective_email = git_config_get(repo_path, ["--get", "user.email"])
    name_origin = git_config_get(repo_path, ["--show-origin", "--get", "user.name"])
    email_origin = git_config_get(repo_path, ["--show-origin", "--get", "user.email"])

    name_source = classify_git_config_source(local_name, global_name, effective_name)
    email_source = classify_git_config_source(local_email, global_email, effective_email)

    problems: list[str] = []
    warnings: list[str] = []

    if not effective_name:
        problems.append("missing_user_name")
    elif effective_name.strip().lower() in COMMIT_IDENTITY_PLACEHOLDER_NAMES:
        warnings.append("placeholder_user_name")

    if not effective_email:
        problems.append("missing_user_email")
    else:
        lowered_email = effective_email.strip().lower()
        if lowered_email in COMMIT_IDENTITY_PLACEHOLDER_EMAILS:
            warnings.append("placeholder_user_email")
        if not EMAIL_SHAPE_RE.match(effective_email):
            warnings.append("email_missing_at_sign")

    if local_name and global_name and local_name != global_name:
        warnings.append("local_user_name_overrides_global")
    if local_email and global_email and local_email != global_email:
        warnings.append("local_user_email_overrides_global")

    setup_commands: list[str] = []
    if not effective_name:
        setup_commands.append('git config --local user.name "Your Name"')
    if not effective_email:
        setup_commands.append('git config --local user.email "you@example.com"')
    if not setup_commands:
        setup_commands.append("# Git commit identity is already set; no identity setup command is required.")

    return {
        "ready": not problems,
        "name": effective_name,
        "email": effective_email,
        "name_source": name_source,
        "email_source": email_source,
        "local": {"name": local_name, "email": local_email},
        "global": {"name": global_name, "email": global_email},
        "origins": {"name": name_origin, "email": email_origin},
        "problems": sorted(set(problems)),
        "warnings": sorted(set(warnings)),
        "recommended_scope": "local" if problems else "use_existing",
        "setup_commands": setup_commands,
        "detection_commands": [
            "git config --local --get user.name",
            "git config --local --get user.email",
            "git config --global --get user.name",
            "git config --global --get user.email",
            "git config --show-origin --get user.name",
            "git config --show-origin --get user.email",
        ],
    }


def repository_not_ok(
    *,
    path: Path,
    metadata: dict[str, Any],
    raw: dict[str, Any],
    error: str,
    repo_state: str,
    stderr: str = "",
    safe_to_init: bool = False,
    inside_work_tree: bool = False,
    bare: bool = False,
    input_inside_parent_repo: bool = False,
    parent_worktree_root: str = "",
    discovery_method: str = "git-rev-parse",
) -> dict[str, Any]:
    return {
        "ok": False,
        "input_path": str(path),
        "inside_work_tree": inside_work_tree,
        "bare": bare,
        "error": error,
        "stderr": stderr,
        "safe_to_init": safe_to_init,
        "recommended_first_step": "start_tracking_this_folder" if safe_to_init else "stop_until_repository_is_clear",
        "repo_state": repo_state,
        "repository_state": repo_state,
        "selected_path_has_git_metadata": bool(metadata.get("valid")),
        "selected_path_git_metadata_kind": metadata.get("kind", "missing"),
        "selected_path_git_metadata_path": metadata.get("path", ""),
        "selected_path_git_metadata_valid": bool(metadata.get("valid")),
        "selected_path_git_metadata_error": metadata.get("error", ""),
        "selected_path_resolved_git_dir": metadata.get("resolved_git_dir", ""),
        "current_dir_is_git_repo_root": False,
        "input_is_repo_root": False,
        "input_inside_parent_repo": input_inside_parent_repo,
        "parent_worktree_root": parent_worktree_root,
        "worktree_root": "",
        "git_dir": "",
        "git_common_dir": "",
        "has_head": False,
        "head_state": "no-local-git-metadata" if not metadata.get("valid") else "unknown",
        "head_oid": "",
        "branch_name": "",
        "discovery_method": discovery_method,
        "raw": raw,
    }


def detect_repository(input_path: Path) -> dict[str, Any]:
    path = input_path.resolve()
    metadata = inspect_selected_git_metadata(path)
    raw: dict[str, Any] = {"selected_git_metadata": metadata}

    if not path.exists():
        return repository_not_ok(
            path=path,
            metadata=metadata,
            raw=raw,
            error="selected-path-missing",
            repo_state="missing_path",
            stderr=f"Selected path does not exist: {path}",
            safe_to_init=False,
            discovery_method="filesystem",
        )
    if not path.is_dir():
        return repository_not_ok(
            path=path,
            metadata=metadata,
            raw=raw,
            error="selected-path-not-directory",
            repo_state="not_directory",
            stderr=f"Selected path is not a directory: {path}",
            safe_to_init=False,
            discovery_method="filesystem",
        )

    metadata_kind = str(metadata.get("kind") or "missing")
    has_local_metadata = bool(metadata.get("valid"))
    if metadata_kind in {"directory", "file", "other"} and not has_local_metadata:
        return repository_not_ok(
            path=path,
            metadata=metadata,
            raw=raw,
            error="selected-path-git-metadata-invalid",
            repo_state="broken_git_metadata",
            stderr=str(metadata.get("error") or "Selected path has a .git marker, but Git cannot resolve it."),
            safe_to_init=False,
            discovery_method="selected-git-metadata",
        )

    inside = run_git(path, ["rev-parse", "--is-inside-work-tree"])
    bare = run_git(path, ["rev-parse", "--is-bare-repository"])
    top = run_git(path, ["rev-parse", "--show-toplevel"])
    git_dir = run_git(path, ["rev-parse", "--git-dir"])
    common_dir = run_git(path, ["rev-parse", "--git-common-dir"])
    raw = {
        **raw,
        "inside": asdict_result(inside),
        "bare": asdict_result(bare),
        "top": asdict_result(top),
        "git_dir": asdict_result(git_dir),
        "common_dir": asdict_result(common_dir),
    }

    if inside.returncode != 0 or top.returncode != 0:
        return repository_not_ok(
            path=path,
            metadata=metadata,
            raw=raw,
            error="not-a-git-repository",
            repo_state="not_initialized",
            stderr=inside.stderr or top.stderr,
            safe_to_init=not has_local_metadata,
            bare=bare.stdout.strip() == "true" if bare.returncode == 0 else False,
            discovery_method="git-rev-parse",
        )

    worktree_root = Path(top.stdout.strip()).resolve()
    input_is_repo_root = same_path(path, worktree_root)
    input_inside_parent_repo = not input_is_repo_root

    if not has_local_metadata:
        return repository_not_ok(
            path=path,
            metadata=metadata,
            raw=raw,
            error="selected-path-has-no-git-metadata",
            repo_state="inside_parent_repo_only" if input_inside_parent_repo else "not_initialized",
            stderr="",
            safe_to_init=True,
            inside_work_tree=inside.stdout.strip() == "true",
            bare=bare.stdout.strip() == "true" if bare.returncode == 0 else False,
            input_inside_parent_repo=input_inside_parent_repo,
            parent_worktree_root=str(worktree_root) if input_inside_parent_repo else "",
            discovery_method="git-rev-parse-parent" if input_inside_parent_repo else "git-rev-parse-without-local-metadata",
        )

    if not input_is_repo_root:
        return repository_not_ok(
            path=path,
            metadata=metadata,
            raw=raw,
            error="selected-git-metadata-does-not-own-worktree",
            repo_state="broken_git_metadata",
            stderr=f"Selected .git marker resolved, but Git reports worktree root {worktree_root}.",
            safe_to_init=False,
            inside_work_tree=inside.stdout.strip() == "true",
            bare=bare.stdout.strip() == "true" if bare.returncode == 0 else False,
            input_inside_parent_repo=input_inside_parent_repo,
            parent_worktree_root=str(worktree_root),
            discovery_method="git-rev-parse-metadata-mismatch",
        )

    head = inspect_head(worktree_root)
    repo_state = "initialized_has_head" if head.get("has_head") else "initialized_no_head"
    return {
        "ok": True,
        "input_path": str(path),
        "inside_work_tree": inside.stdout.strip() == "true",
        "bare": bare.stdout.strip() == "true" if bare.returncode == 0 else False,
        "worktree_root": str(worktree_root),
        "git_dir": resolve_git_path(path, git_dir.stdout.strip()) if git_dir.returncode == 0 else "",
        "git_common_dir": resolve_git_path(path, common_dir.stdout.strip()) if common_dir.returncode == 0 else "",
        "input_is_repo_root": input_is_repo_root,
        "input_inside_parent_repo": input_inside_parent_repo,
        "selected_path_has_git_metadata": has_local_metadata,
        "selected_path_git_metadata_kind": metadata_kind,
        "selected_path_git_metadata_path": metadata.get("path", ""),
        "selected_path_git_metadata_valid": bool(metadata.get("valid")),
        "selected_path_git_metadata_error": metadata.get("error", ""),
        "selected_path_resolved_git_dir": metadata.get("resolved_git_dir", ""),
        "current_dir_is_git_repo_root": input_is_repo_root and has_local_metadata,
        "parent_worktree_root": "" if input_is_repo_root else str(worktree_root),
        "repo_state": repo_state,
        "repository_state": repo_state,
        "has_head": bool(head.get("has_head")),
        "head_state": head.get("head_state", "unknown"),
        "head_oid": head.get("head_oid", ""),
        "branch_name": head.get("branch_name", ""),
        "discovery_method": "git-rev-parse",
        "raw": {**raw, "head": head.get("raw", {})},
    }


def asdict_result(result: GitResult) -> dict[str, Any]:
    return {"command": result.command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def resolve_git_path(base: Path, value: str) -> str:
    if not value:
        return ""
    p = Path(value)
    if not p.is_absolute():
        p = base / p
    return str(p.resolve())


def same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)


def parse_branch(status_text: str) -> dict[str, Any]:
    branch = {"name": "", "upstream": "", "ahead": 0, "behind": 0}
    for entry in status_text.split("\0"):
        if entry.startswith("# branch.head "):
            branch["name"] = entry.removeprefix("# branch.head ").strip()
        elif entry.startswith("# branch.upstream "):
            branch["upstream"] = entry.removeprefix("# branch.upstream ").strip()
        elif entry.startswith("# branch.ab "):
            match = re.search(r"\+(\d+)\s+-(\d+)", entry)
            if match:
                branch["ahead"] = int(match.group(1))
                branch["behind"] = int(match.group(2))
    return branch



def normalize_repo_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def git_status_label(rec: dict[str, Any]) -> str:
    if rec.get("untracked"):
        return "untracked"
    if rec.get("ignored"):
        return "ignored"
    if rec.get("conflicted"):
        return "conflicted"
    if rec.get("renamed"):
        return "tracked_renamed"
    if rec.get("deleted"):
        return "tracked_deleted"
    if rec.get("staged") or rec.get("unstaged") or rec.get("state") in {"modified", "added", "deleted", "renamed"}:
        return "tracked_changed"
    return str(rec.get("state") or "unknown")


def parse_porcelain_v2(status_text: str) -> list[dict[str, Any]]:
    entries = [item for item in status_text.split("\0") if item and not item.startswith("# ")]
    files: list[dict[str, Any]] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        kind = entry[:1]
        if kind == "?":
            path = entry[2:]
            files.append(file_record(path, state="untracked", status="untracked", untracked=True))
        elif kind == "!":
            path = entry[2:]
            files.append(file_record(path, state="ignored", status="ignored", ignored=True))
        elif kind == "1":
            parts = entry.split(" ", 8)
            xy = parts[1] if len(parts) > 1 else ".."
            path = parts[8] if len(parts) > 8 else entry.split(" ")[-1]
            files.append(file_from_xy(path, xy))
        elif kind == "2":
            parts = entry.split(" ", 9)
            xy = parts[1] if len(parts) > 1 else ".."
            path = parts[9] if len(parts) > 9 else entry.split(" ")[-1]
            old_path = entries[i + 1] if i + 1 < len(entries) else ""
            i += 1
            rec = file_from_xy(path, xy)
            rec["renamed"] = True
            rec["old_path"] = normalize_repo_path(old_path)
            rec["state"] = "renamed"
            rec["status"] = git_status_label(rec)
            files.append(rec)
        elif kind == "u":
            parts = entry.split(" ", 10)
            xy = parts[1] if len(parts) > 1 else "UU"
            path = parts[10] if len(parts) > 10 else entry.split(" ")[-1]
            rec = file_from_xy(path, xy)
            rec["conflicted"] = True
            rec["state"] = "conflicted"
            rec["status"] = "conflicted"
            files.append(rec)
        i += 1
    return files


def file_record(path: str, **overrides: Any) -> dict[str, Any]:
    normalized_path = normalize_repo_path(path)
    rec = {
        "path": normalized_path,
        "state": "clean",
        "staged": False,
        "unstaged": False,
        "untracked": False,
        "ignored": False,
        "deleted": False,
        "renamed": False,
        "conflicted": False,
        "binary": False,
        "large": False,
        "risk": "low",
        "classifications": classify_path(normalized_path),
        "possible_actions": [],
    }
    rec.update(overrides)
    rec["path"] = normalize_repo_path(str(rec.get("path") or normalized_path))
    rec["status"] = str(rec.get("status") or git_status_label(rec))
    rec["risk"] = risk_for_file(rec)
    rec["possible_actions"] = actions_for_file(rec)
    return rec


def file_from_xy(path: str, xy: str) -> dict[str, Any]:
    staged_code = xy[0] if xy else "."
    unstaged_code = xy[1] if len(xy) > 1 else "."
    state = "modified"
    if "D" in xy:
        state = "deleted"
    elif "A" in xy:
        state = "added"
    rec = file_record(
        path,
        state=state,
        staged=staged_code != ".",
        unstaged=unstaged_code != ".",
        deleted="D" in xy,
    )
    rec["xy"] = xy
    rec["status"] = git_status_label(rec)
    return rec


def path_name_tokens(value: str) -> list[str]:
    return [part for part in re.split(r"[^a-z0-9]+", value.lower()) if part]


def path_looks_like_generated_family(path: str) -> bool:
    """Detect generic generated/cache/output directory families.

    This is deliberately path-shape based rather than repo-name based. A folder
    like qa_output_linux/ or browser-cache-chrome/ is likely generated even in a
    repo this planner has never seen before, while a single root file named
    output.py is not enough evidence.
    """

    normalized = path.replace("\\", "/").strip()
    if not normalized:
        return False
    directoryish = normalized.endswith("/") or "/" in normalized
    if not directoryish:
        return False
    first_segment = normalized.strip("/").split("/", 1)[0].strip()
    if not first_segment:
        return False
    tokens = path_name_tokens(first_segment.lstrip("."))
    if not tokens:
        return False
    return any(token in GENERATED_DIR_FAMILY_TOKENS for token in tokens)


def classify_path(path: str) -> list[str]:
    normalized = path.replace("\\", "/")
    lower = normalized.lower()
    labels: list[str] = []
    name = normalized.rsplit("/", 1)[-1]
    suffix = Path(name).suffix.lower()
    if name in CONFIG_NAMES or suffix in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}:
        labels.append("config")
    if suffix in SOURCE_EXTS or name in CONFIG_NAMES:
        labels.append("source")
    if any(re.search(pattern, lower) for pattern in GENERATED_PATTERNS) or path_looks_like_generated_family(normalized):
        labels.append("generated")
    if any(re.search(pattern, lower) for pattern in LOCAL_ENV_PATTERNS):
        labels.append("local-environment")
        labels.append("secret-looking")
    if "test" in lower or lower.startswith("tests/"):
        labels.append("test")
    if not labels:
        labels.append("unknown")
    return sorted(set(labels))


def risk_for_file(rec: dict[str, Any]) -> str:
    labels = set(rec.get("classifications", []))
    if rec.get("conflicted"):
        return "critical"
    if "secret-looking" in labels:
        return "high"
    if rec.get("deleted") or rec.get("renamed"):
        return "medium"
    if rec.get("untracked") and "source" in labels and "generated" not in labels:
        return "medium"
    if "generated" in labels:
        return "low"
    return "medium" if rec.get("staged") or rec.get("unstaged") else "low"


def actions_for_file(rec: dict[str, Any]) -> list[str]:
    labels = set(rec.get("classifications", []))
    actions = ["save_current_state", "explain_each_dirty_item"]
    if rec.get("conflicted"):
        actions += ["show_merge_conflicts", "open_conflict_for_manual_fix", "keep_our_version", "keep_their_version"]
    if rec.get("untracked"):
        if "generated" in labels:
            actions += ["ignore_generated_files", "remove_untracked_generated_files"]
        elif "secret-looking" in labels or "local-environment" in labels:
            actions += ["preserve_local_only_files", "ignore_local_environment_files"]
        else:
            actions += ["preserve_local_only_files", "start_tracking_real_work"]
    if rec.get("staged"):
        actions.append("unstage_selected_changes")
    if rec.get("unstaged") and not rec.get("untracked"):
        actions += ["keep_changes_unstaged", "discard_selected_file_changes"]
    return sorted(set(actions))


def add_numstat(files: list[dict[str, Any]], root: Path) -> None:
    by_path = {f["path"]: f for f in files}
    for args in (["diff", "--numstat"], ["diff", "--cached", "--numstat"]):
        result = run_git(root, args)
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            add_s, del_s, path = parts[0], parts[1], parts[2]
            if path in by_path:
                rec = by_path[path]
                rec["binary"] = add_s == "-" or del_s == "-"
                if add_s.isdigit():
                    rec["additions"] = rec.get("additions", 0) + int(add_s)
                if del_s.isdigit():
                    rec["deletions"] = rec.get("deletions", 0) + int(del_s)


def find_nested_repos(root: Path) -> list[dict[str, str]]:
    nested: list[dict[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        if current == root:
            dirnames[:] = [d for d in dirnames if d != ".git"]
            continue
        if ".git" in dirnames or ".git" in filenames:
            nested.append({"path": str(current.relative_to(root)), "worktree_root": str(current)})
            dirnames[:] = [d for d in dirnames if d != ".git"]
        if len(nested) >= 200:
            break
    return nested


def empty_summary(*, blocking: int = 0) -> dict[str, int]:
    return {
        "blocking": blocking,
        "modified": 0,
        "staged": 0,
        "unstaged": 0,
        "untracked": 0,
        "deleted": 0,
        "renamed": 0,
        "conflicted": 0,
        "generated": 0,
        "source": 0,
        "local_environment": 0,
    }


def repo_state_dirty_thing(detection: dict[str, Any]) -> dict[str, Any]:
    state = str(detection.get("repo_state") or detection.get("repository_state") or "unknown")
    if state == "inside_parent_repo_only":
        return dirty_thing(
            "selected-folder-inside-parent-repository",
            "repo-state",
            detection.get("input_path", ""),
            "high",
            True,
            "Selected folder has no local .git metadata; Git only works because discovery found a parent repository.",
            ["initialize_repository_here", "choose_correct_repository_root", "keep_parent_repository"],
        )
    if state == "not_initialized":
        return dirty_thing(
            "repo-not-initialized",
            "repo-state",
            detection.get("input_path", ""),
            "high",
            True,
            "Selected folder is not initialized as its own Git repository.",
            ["initialize_repository_here"],
        )
    if state == "initialized_no_head":
        return dirty_thing(
            "repo-initialized-without-head",
            "repo-state",
            detection.get("worktree_root") or detection.get("input_path", ""),
            "high",
            True,
            "Repository is initialized but has no first commit yet; push and normal branch work are blocked until HEAD exists.",
            ["update_gitignore_before_initial_commit", "create_initial_snapshot"],
        )
    if state == "broken_git_metadata":
        return dirty_thing(
            "repo-git-metadata-broken",
            "repo-state",
            detection.get("input_path", ""),
            "critical",
            True,
            detection.get("stderr") or detection.get("selected_path_git_metadata_error") or "Selected folder has broken Git metadata.",
            ["stop_until_repository_is_clear"],
        )
    return dirty_thing(
        "repo-state-unknown",
        "repo-state",
        detection.get("input_path", ""),
        "high",
        True,
        detection.get("stderr") or "Git repository state could not be classified.",
        ["stop_until_repository_is_clear"],
    )


def collect_status(input_path: Path) -> dict[str, Any]:
    detection = detect_repository(input_path)
    if not detection["ok"]:
        state = str(detection.get("repo_state") or detection.get("repository_state") or "unknown")
        risk_message = {
            "missing_path": "Selected path does not exist.",
            "not_directory": "Selected path is not a directory.",
            "not_initialized": "Selected folder is not initialized as its own Git repository.",
            "inside_parent_repo_only": "Selected folder has no local .git metadata and is only inside a parent repository.",
            "broken_git_metadata": "Selected folder has a .git marker, but Git cannot use it safely.",
        }.get(state, "Git could not prove this path is a safe worktree.")
        return {
            "ok": True,
            "repo": str(input_path.resolve()),
            "git_detection": detection,
            "repo_state": state,
            "repository_state": state,
            "dirty": True,
            "dirty_score": 80 if detection.get("safe_to_init") else 95,
            "level": "very-dirty" if detection.get("safe_to_init") else "hazardous",
            "summary": empty_summary(blocking=1),
            "files": [],
            "dirty_things": [repo_state_dirty_thing(detection)],
            "risks": [{"level": "high", "message": risk_message}],
            "branch": {
                "name": "",
                "upstream": "",
                "ahead": 0,
                "behind": 0,
                "has_head": False,
                "head_state": detection.get("head_state", "unknown"),
                "repo_state": state,
            },
            "nested_repos": [],
            "git_identity": commit_identity_empty(),
            "commit_identity": commit_identity_empty(),
        }

    root = Path(detection["worktree_root"])
    git_identity = inspect_commit_identity(root)
    status = run_git(root, ["status", "--porcelain=v2", "-z", "--branch", "--untracked-files=all"])
    files = parse_porcelain_v2(status.stdout) if status.returncode == 0 else []
    add_numstat(files, root)
    nested = find_nested_repos(root)
    summary = summarize(files)
    if detection.get("repo_state") == "initialized_no_head":
        summary = dict(summary)
        summary["blocking"] = summary.get("blocking", 0) + 1
    score = dirty_score(summary, detection, nested)
    things = dirty_things_from_files(files, detection, nested)
    risks = risks_from(summary, detection, nested, files)
    branch = parse_branch(status.stdout)
    branch.update({
        "has_head": bool(detection.get("has_head")),
        "head_state": detection.get("head_state", "unknown"),
        "head_oid": detection.get("head_oid", ""),
        "repo_state": detection.get("repo_state", "unknown"),
    })
    return {
        "ok": status.returncode == 0,
        "repo": str(input_path.resolve()),
        "git_detection": detection,
        "repo_state": detection.get("repo_state", "unknown"),
        "repository_state": detection.get("repository_state", detection.get("repo_state", "unknown")),
        "dirty": bool(files or nested or detection.get("input_inside_parent_repo") or not detection.get("has_head")),
        "dirty_score": score,
        "level": level_for_score(score),
        "summary": summary,
        "files": files,
        "dirty_things": things,
        "risks": risks,
        "branch": branch,
        "nested_repos": nested,
        "git_identity": git_identity,
        "commit_identity": git_identity,
        "raw_status_error": "" if status.returncode == 0 else status.stderr,
    }


def summarize(files: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "blocking": 0,
        "modified": 0,
        "staged": 0,
        "unstaged": 0,
        "untracked": 0,
        "deleted": 0,
        "renamed": 0,
        "conflicted": 0,
        "generated": 0,
        "source": 0,
        "local_environment": 0,
    }
    for rec in files:
        if rec.get("staged"): summary["staged"] += 1
        if rec.get("unstaged"): summary["unstaged"] += 1
        if rec.get("untracked"): summary["untracked"] += 1
        if rec.get("deleted"): summary["deleted"] += 1
        if rec.get("renamed"): summary["renamed"] += 1
        if rec.get("conflicted"):
            summary["conflicted"] += 1
            summary["blocking"] += 1
        if rec.get("state") in {"modified", "added", "deleted", "renamed"}:
            summary["modified"] += 1
        labels = set(rec.get("classifications", []))
        if "generated" in labels: summary["generated"] += 1
        if "source" in labels: summary["source"] += 1
        if "local-environment" in labels: summary["local_environment"] += 1
    return summary


def dirty_score(summary: dict[str, int], detection: dict[str, Any], nested: list[dict[str, str]]) -> int:
    if not detection.get("ok"):
        return 80 if detection.get("safe_to_init") else 95
    score = 0
    score += min(summary["staged"] * 6, 24)
    score += min(summary["unstaged"] * 5, 25)
    score += min(summary["untracked"] * 3, 24)
    score += min(summary["deleted"] * 8, 24)
    score += min(summary["renamed"] * 5, 15)
    score += 70 if summary["conflicted"] else 0
    score += min(summary["local_environment"] * 10, 20)
    score += min(len(nested) * 10, 20)
    if detection.get("input_inside_parent_repo"):
        score += 15
    if detection.get("repo_state") == "initialized_no_head" or (detection.get("ok") and not detection.get("has_head")):
        score += 50
    return max(0, min(100, score))


def level_for_score(score: int) -> str:
    if score == 0: return "clean"
    if score <= 20: return "lightly-dirty"
    if score <= 50: return "dirty"
    if score <= 80: return "very-dirty"
    return "hazardous"


def dirty_thing(id_: str, kind: str, path: str, risk: str, blocking: bool, summary: str, actions: list[str]) -> dict[str, Any]:
    return {"id": id_, "kind": kind, "path": path, "risk": risk, "blocking": blocking, "summary": summary, "possible_actions": actions}


def slug(path: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", path).strip("-").lower()
    return value[:80] or "root"


def dirty_things_from_files(files: list[dict[str, Any]], detection: dict[str, Any], nested: list[dict[str, str]]) -> list[dict[str, Any]]:
    things: list[dict[str, Any]] = []
    if detection.get("repo_state") == "initialized_no_head":
        things.append(repo_state_dirty_thing(detection))
    if detection.get("input_inside_parent_repo"):
        things.append(dirty_thing(
            "input-path-inside-parent-repository",
            "repo-state",
            detection.get("input_path", ""),
            "medium",
            False,
            "Selected path is inside a parent Git repository, not necessarily the intended repo root.",
            ["choose_correct_repository_root", "keep_parent_repository", "create_nested_repository_here"],
        ))
    for n in nested:
        things.append(dirty_thing(
            f"nested-repository-{slug(n['path'])}",
            "nested-repository",
            n["path"],
            "medium",
            False,
            "Nested repository/worktree detected. Do not flatten it into the parent cleanup.",
            ["find_nested_repositories", "stop_until_repository_is_clear"],
        ))
    for rec in files:
        kind = "changed-file"
        if rec.get("untracked"): kind = "untracked-file"
        if rec.get("conflicted"): kind = "conflicted-file"
        things.append(dirty_thing(
            f"dirty-{slug(rec['path'])}",
            kind,
            rec["path"],
            rec["risk"],
            bool(rec.get("conflicted")),
            file_summary(rec),
            rec["possible_actions"],
        ))
    return things


def file_summary(rec: dict[str, Any]) -> str:
    labels = ", ".join(rec.get("classifications", []))
    state = rec.get("state", "changed")
    return f"{state} file ({labels})"


def risks_from(summary: dict[str, int], detection: dict[str, Any], nested: list[dict[str, str]], files: list[dict[str, Any]]) -> list[dict[str, str]]:
    risks: list[dict[str, str]] = []
    if detection.get("repo_state") == "initialized_no_head":
        risks.append({"level": "high", "message": "Repository has no HEAD commit yet; push and normal branch work are blocked until an initial snapshot exists."})
    if detection.get("input_inside_parent_repo"):
        risks.append({"level": "medium", "message": "The selected path is inside a parent Git repository; confirm the intended root before cleanup."})
    if summary["conflicted"]:
        risks.append({"level": "critical", "message": "Merge conflicts block normal cleanup and should be resolved or aborted first."})
    if summary["local_environment"]:
        risks.append({"level": "high", "message": "Secret-looking or local environment files are present; avoid staging them accidentally."})
    if summary["untracked"] > 10:
        risks.append({"level": "medium", "message": "Many untracked files are present; classify them before cleaning."})
    if summary["generated"]:
        risks.append({"level": "low", "message": "Generated/debug artifacts appear to be contributing to dirty state."})
    if nested:
        risks.append({"level": "medium", "message": "Nested repositories were detected and should be handled separately."})
    if not risks and files:
        risks.append({"level": "low", "message": "Dirty state is present but no critical blockers were detected."})
    return risks


def action_by_id(action_id: str) -> dict[str, Any]:
    for action in ACTION_CATALOG:
        if action["id"] == action_id:
            return action
    raise KeyError(action_id)


def shell_quote(value: str) -> str:
    text = str(value)
    escaped = text.replace('"', '\\"')
    return f'"{escaped}"'


def join_command(argv: list[str]) -> str:
    return " ".join(shell_quote(part) if part == "" or re.search(r"[\s&|;<>()\[\]{}^]", part) or "\\" in part or "/" in part or ":" in part else part for part in argv)


def command_record(
    *,
    purpose: str,
    template: str,
    command: str,
    argv: list[str] | None = None,
    safe: bool = True,
    destructive: bool = False,
    locked: bool = False,
    requires: list[str] | None = None,
    command_kind: str = "git",
    implemented: bool = True,
    note: str = "",
) -> dict[str, Any]:
    return {
        "purpose": purpose,
        "template": template,
        "command": command,
        "argv": argv or [],
        "kind": command_kind,
        "safe": safe,
        "destructive": destructive,
        "locked": locked,
        "requires": requires or [],
        "implemented": implemented,
        "note": note,
    }


def git_command(
    repo: str,
    args: list[str],
    *,
    purpose: str,
    template: str | None = None,
    safe: bool = True,
    destructive: bool = False,
    locked: bool = False,
    requires: list[str] | None = None,
    implemented: bool = True,
    note: str = "",
) -> dict[str, Any]:
    argv = ["git", "-C", repo, *args]
    return command_record(
        purpose=purpose,
        template=template or join_command(["git", "-C", "<repo>", *args]),
        command=join_command(argv),
        argv=argv,
        safe=safe,
        destructive=destructive,
        locked=locked,
        requires=requires,
        command_kind="git",
        implemented=implemented,
        note=note,
    )


def shell_command(
    command: str,
    *,
    purpose: str,
    template: str,
    safe: bool = True,
    destructive: bool = False,
    locked: bool = False,
    requires: list[str] | None = None,
    implemented: bool = True,
    command_kind: str = "shell",
    note: str = "",
) -> dict[str, Any]:
    return command_record(
        purpose=purpose,
        template=template,
        command=command,
        safe=safe,
        destructive=destructive,
        locked=locked,
        requires=requires,
        command_kind=command_kind,
        implemented=implemented,
        note=note,
    )


def limited_paths(paths: list[str] | None, *, limit: int = 30) -> tuple[list[str], bool]:
    values = list(paths or [])
    return values[:limit], len(values) > limit


def detect_text_newline_style(raw: bytes) -> str:
    """Return a compact newline style label for UI file metadata."""

    if not raw:
        return "none"
    crlf_count = raw.count(b"\r\n")
    lf_count = raw.count(b"\n")
    lone_lf_count = lf_count - crlf_count
    lone_cr_count = raw.count(b"\r") - crlf_count
    styles = []
    if crlf_count:
        styles.append("crlf")
    if lone_lf_count:
        styles.append("lf")
    if lone_cr_count:
        styles.append("cr")
    if len(styles) > 1:
        return "mixed"
    if styles:
        return styles[0]
    return "none"



def attach_gitignore_review_payload(
    item: dict[str, Any],
    repo_root: Path,
    *,
    safe_paths: list[str] | None = None,
    questionable_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Attach the payload needed by the frontend .gitignore review card."""

    safe_paths = safe_paths or []
    questionable_paths = questionable_paths or []
    affected_paths = [*safe_paths, *questionable_paths]
    safe_rules = suggest_gitignore_rules(safe_paths)
    questionable_rules = suggest_questionable_gitignore_rules(
        questionable_paths,
        family_source_paths=affected_paths,
    )

    item["safe_paths"] = safe_paths
    item["questionable_paths"] = questionable_paths
    item["ignore_rules"] = safe_rules
    item["questionable_ignore_rules"] = questionable_rules
    item["ignore_rule_groups"] = {
        "safe": safe_rules,
        "questionable": questionable_rules,
    }
    item["affected_paths"] = affected_paths
    item["gitignore_file"] = read_gitignore_file(repo_root)
    return item

def read_gitignore_file(repo_path: Path) -> dict[str, Any]:
    """Return .gitignore presence, metadata, and readable text lines for the UI."""

    ignore_path = repo_path / ".gitignore"
    info: dict[str, Any] = {
        "path": ".gitignore",
        "absolute_path": str(ignore_path.resolve()),
        "exists": ignore_path.is_file(),
        "size": 0,
        "mtime": None,
        "line_count": 0,
        "sha256": "",
        "newline": "unknown",
        "lines": [],
        "content": "",
        "content_read": False,
        "note": "",
    }
    if not ignore_path.exists():
        info["newline"] = "none"
        info["note"] = ".gitignore does not exist."
        return info
    if not ignore_path.is_file():
        info["error"] = ".gitignore exists but is not a regular file"
        info["note"] = "Could not read .gitignore because it is not a regular file."
        return info
    try:
        stat = ignore_path.stat()
        info["size"] = stat.st_size
        info["mtime"] = stat.st_mtime
    except OSError as exc:
        info["error"] = f"could_not_stat_gitignore:{type(exc).__name__}"
        info["note"] = "Could not stat .gitignore before reading contents."
        return info
    try:
        raw = ignore_path.read_bytes()
    except OSError as exc:
        info["error"] = f"could_not_read_gitignore:{type(exc).__name__}"
        info["note"] = "Could not read .gitignore contents."
        return info
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        info["error"] = f"could_not_decode_gitignore:{type(exc).__name__}"
        info["newline"] = detect_text_newline_style(raw)
        info["sha256"] = hashlib.sha256(raw).hexdigest()
        info["note"] = "Could not decode .gitignore contents as UTF-8 text."
        return info

    lines = content.splitlines()
    info.update({
        "content_read": True,
        "content": content,
        "line_count": len(lines),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "newline": detect_text_newline_style(raw),
        "lines": [
            {
                "number": index,
                "text": line,
                "blank": not line.strip(),
                "comment": line.lstrip().startswith("#"),
            }
            for index, line in enumerate(lines, 1)
        ],
        "note": ".gitignore content was read by git_dirty.py.",
    })
    return info

def normalize_gitignore_save_path(raw_path: str = ".gitignore") -> str:
    """Return the only .gitignore path this UI is allowed to write."""

    cleaned = str(raw_path or ".gitignore").replace("\\", "/").strip()
    if cleaned in {"", "./.gitignore"}:
        cleaned = ".gitignore"
    candidate = Path(cleaned)
    if candidate.is_absolute() or cleaned != ".gitignore" or ".." in candidate.parts:
        raise ValueError("Only the selected project root .gitignore can be saved.")
    return ".gitignore"


def normalize_gitignore_save_lines(raw_lines: Any) -> list[str]:
    if not isinstance(raw_lines, list):
        raise ValueError(".gitignore lines must be provided as a JSON array.")
    lines: list[str] = []
    for item in raw_lines:
        line = str(item)
        if "\x00" in line or "\n" in line or "\r" in line:
            raise ValueError(".gitignore line entries must not contain embedded newlines or NUL bytes.")
        lines.append(line)
    return lines


def gitignore_newline_text(requested: str = "existing", existing: str = "lf") -> str:
    requested = str(requested or "existing").lower()
    existing = str(existing or "lf").lower()
    if requested == "existing":
        requested = existing
    if requested in {"unknown", "none", "mixed"}:
        requested = "lf"
    if requested not in {"lf", "crlf", "cr"}:
        raise ValueError("Unsupported .gitignore newline style.")
    return {"lf": "\n", "crlf": "\r\n", "cr": "\r"}[requested]


def write_gitignore_file(
    repo_path: Path,
    lines: Any,
    *,
    path: str = ".gitignore",
    newline: str = "existing",
) -> dict[str, Any]:
    """Safely write the selected project root .gitignore and return fresh file metadata."""

    relative_path = normalize_gitignore_save_path(path)
    clean_lines = normalize_gitignore_save_lines(lines)
    root = Path(repo_path).resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("Selected project root does not exist.")
    ignore_path = (root / relative_path).resolve()
    if ignore_path.parent != root or ignore_path.name != ".gitignore":
        raise ValueError("Resolved .gitignore path escaped the selected project root.")
    before = read_gitignore_file(root)
    newline_text = gitignore_newline_text(newline, str(before.get("newline") or "lf"))
    content = ""
    if clean_lines:
        content = newline_text.join(clean_lines) + newline_text
    ignore_path.write_text(content, encoding="utf-8", newline="")
    return read_gitignore_file(root)


def normalize_ignore_candidate(raw: str) -> str:
    normalized = str(raw).replace("\\", "/").strip()
    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        return ""
    if normalized.endswith("/"):
        normalized = normalized.rstrip("/") + "/"
    return normalized


def top_directory_segment(rule: str) -> str:
    normalized = rule.strip("/")
    if not normalized:
        return ""
    if "/" not in normalized and not rule.endswith("/"):
        return ""
    return normalized.split("/", 1)[0]


def family_prefix_candidates(name: str) -> list[str]:
    if not name or name.startswith("."):
        return []
    delimiter_positions = [match.start() for match in re.finditer(r"[_-]", name)]
    prefixes: list[str] = []
    for pos in delimiter_positions[1:]:
        prefix = name[:pos]
        if len(prefix) < 8 or not re.search(r"[_-]", prefix):
            continue
        tokens = path_name_tokens(prefix)
        if not any(token in GENERATED_DIR_FAMILY_TOKENS for token in tokens):
            continue
        prefixes.append(prefix)
    return prefixes


def infer_directory_family_rules(rules: list[str], *, min_family_size: int = 3) -> dict[str, str]:
    """Map repeated generated directory names to maintainable wildcard rules.

    Example: ["harness_output/", "harness_output_a/", "harness_output_b/"]
    becomes {"harness_output": "harness_output*/", ...}. The inference is
    generic: it looks for repeated sibling directory names that share a stable
    generated/cache/output prefix, not for project-specific names.
    """

    directory_names = {top_directory_segment(rule) for rule in rules}
    directory_names.discard("")
    candidate_prefixes: set[str] = set()
    for name in directory_names:
        candidate_prefixes.update(family_prefix_candidates(name))

    families: list[tuple[str, set[str]]] = []
    for prefix in candidate_prefixes:
        members = {
            name for name in directory_names
            if name == prefix or name.startswith(prefix + "_") or name.startswith(prefix + "-")
        }
        if len(members) >= min_family_size:
            families.append((prefix, members))

    families.sort(key=lambda item: (-len(item[1]), len(item[0]), item[0]))
    covered: set[str] = set()
    family_by_dir: dict[str, str] = {}
    for prefix, members in families:
        if members.issubset(covered):
            continue
        for member in sorted(members):
            family_by_dir[member] = prefix + "*/"
        covered.update(members)
    return family_by_dir


def suffix_looks_like_run_id(value: str) -> bool:
    """Return true for timestamp/date/opaque run-id suffixes.

    These can justify a review-only wildcard suggestion like
    ``ollama_prompt_space*/`` when repeated sibling directories are observed, but
    they are intentionally kept out of the safe pile because the prefix is not
    inherently generated/cache/output language.
    """

    lowered = value.lower().strip("_-")
    if not lowered:
        return False
    if re.search(r"(?:19|20)\d{6}(?:[_-]?\d{4,6})?", lowered):
        return True
    if re.fullmatch(r"[0-9a-f]{7,}", lowered):
        return True
    if re.fullmatch(r"\d{6,}", lowered):
        return True
    return False


def questionable_family_prefix_candidates(name: str) -> list[str]:
    if not name or name.startswith("."):
        return []
    prefixes: list[str] = []
    for match in re.finditer(r"[_-]", name):
        pos = match.start()
        prefix = name[:pos]
        suffix = name[pos + 1:]
        if len(prefix) < 5 or not suffix_looks_like_run_id(suffix):
            continue
        tokens = path_name_tokens(prefix)
        if any(token in GENERATED_DIR_FAMILY_TOKENS for token in tokens):
            continue
        prefixes.append(prefix)
    return prefixes


def infer_questionable_directory_family_rules(rules: list[str], *, min_family_size: int = 2) -> dict[str, str]:
    """Map repeated run-id directory names to review-only wildcard rules.

    This catches generic timestamped/hashed run folders without pretending the
    wildcard is automatically safe. Example:
    ["ollama_prompt_space_20260501/", "ollama_prompt_space_20260502/"] can suggest
    ``ollama_prompt_space*/`` in the questionable pile.
    """

    directory_names = {top_directory_segment(rule) for rule in rules}
    directory_names.discard("")
    candidate_prefixes: set[str] = set()
    for name in directory_names:
        candidate_prefixes.update(questionable_family_prefix_candidates(name))

    families: list[tuple[str, set[str]]] = []
    for prefix in candidate_prefixes:
        members = {
            name for name in directory_names
            if name == prefix or name.startswith(prefix + "_") or name.startswith(prefix + "-")
        }
        if len(members) >= min_family_size:
            families.append((prefix, members))

    families.sort(key=lambda item: (-len(item[1]), -len(item[0]), item[0]))
    covered: set[str] = set()
    family_by_dir: dict[str, str] = {}
    for prefix, members in families:
        if members.issubset(covered):
            continue
        for member in sorted(members):
            family_by_dir[member] = prefix + "*/"
        covered.update(members)
    return family_by_dir


def ignore_candidate_requires_review(path: str, labels: set[str] | None = None) -> bool:
    """Return true for ignore candidates that need their own review pile.

    Generated/cache/output families with obvious names can be suggested as safe
    ignore rules. Local secrets/runtime files and ambiguous timestamped or
    opaque run folders should not be mixed into that safe pile, even when they
    also look generated. This keeps rules like ``ollama_prompt_space*/`` and
    their exact observed members separate from ordinary cache/build output.
    """

    labels = labels or set()
    if "local-environment" in labels or "secret-looking" in labels:
        return True
    rule = normalize_ignore_candidate(path)
    top = top_directory_segment(rule)
    if top and questionable_family_prefix_candidates(top):
        return True
    return False


def ignore_review_path(path: str, labels: set[str] | None = None) -> str:
    """Return the review path shown on the .gitignore card.

    The commit basket needs full file paths so users can stage exact files. The
    ignore card is different: when full status expands an untracked generated
    directory into child files, the review target should still be the generated
    directory itself (for example ``__pycache__/``), not one incidental file
    inside it.
    """

    rule = normalize_ignore_candidate(path)
    if not rule:
        return ""
    labels = labels or set()
    if "generated" in labels:
        top = top_directory_segment(rule)
        if top:
            return top.rstrip("/") + "/"
    return rule


def normalized_ignore_rules(paths: list[str] | None) -> list[str]:
    normalized_rules: list[str] = []
    seen_normalized: set[str] = set()
    for raw in paths or []:
        rule = normalize_ignore_candidate(str(raw))
        if not rule or rule in seen_normalized:
            continue
        seen_normalized.add(rule)
        normalized_rules.append(rule)
    return normalized_rules


def suggest_gitignore_rules(paths: list[str] | None, *, limit: int = 80) -> list[str]:
    """Return conservative, repo-relative .gitignore rules for known-safe noise.

    Exact rules remain the default. When the planner sees repeated generated
    sibling directories with a stable prefix, it collapses them into a reviewed
    wildcard such as harness_output*/ or diagnostics_output*/. That keeps the
    recommendation maintainable without broad extension ignores that could hide
    real source/config files.
    """
    normalized_rules = normalized_ignore_rules(paths)
    family_by_dir = infer_directory_family_rules(normalized_rules)
    rules: list[str] = []
    seen: set[str] = set()
    for rule in normalized_rules:
        family_rule = family_by_dir.get(top_directory_segment(rule))
        candidate = family_rule or rule
        if candidate in seen:
            continue
        seen.add(candidate)
        rules.append(candidate)
        if len(rules) >= limit:
            break
    return rules


def suggest_questionable_gitignore_rules(paths: list[str] | None, *, family_source_paths: list[str] | None = None, limit: int = 80) -> list[str]:
    """Return review-only .gitignore candidates.

    This pile is for entries that may be right but should not be mixed with the
    safe generated/cache pile: secret-looking local state, runtime folders, and
    broad wildcard guesses for timestamped/opaque run directories.
    """

    exact_rules = normalized_ignore_rules(paths)
    family_source_rules = normalized_ignore_rules(family_source_paths if family_source_paths is not None else paths)
    family_by_dir = infer_questionable_directory_family_rules(family_source_rules)

    rules: list[str] = []
    seen: set[str] = set()

    for rule in exact_rules:
        if rule in seen:
            continue
        seen.add(rule)
        rules.append(rule)
        if len(rules) >= limit:
            return rules

    for rule in family_source_rules:
        candidate = family_by_dir.get(top_directory_segment(rule))
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        rules.append(candidate)
        if len(rules) >= limit:
            break
    return rules




def safe_repo_file_path(root: Path, relative_path: str) -> Path | None:
    if not relative_path or "\x00" in relative_path:
        return None
    try:
        full = (root / relative_path).resolve()
        full.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return full


def detect_secrets_availability() -> dict[str, Any]:
    """Report whether the backend can run detect-secrets.

    This is only an integration/status probe. git_dirty.py does not execute
    detect-secrets or scan file contents while building the plan.
    """

    executable = shutil.which("detect-secrets")
    if executable:
        return {
            "available": True,
            "status": "available",
            "engine": "detect-secrets",
            "method": "executable",
            "executable": executable,
            "install_hint": "",
        }
    if importlib.util.find_spec("detect_secrets") is not None:
        return {
            "available": True,
            "status": "available",
            "engine": "detect-secrets",
            "method": "python_package",
            "executable": "",
            "install_hint": "",
        }
    return {
        "available": False,
        "status": "unavailable",
        "engine": "detect-secrets",
        "method": "not_found",
        "executable": "",
        "install_hint": "Install detect-secrets in the backend environment or disable the detect_secrets rule intentionally.",
    }


def security_rule_catalog() -> list[dict[str, Any]]:
    """Return the authoritative security/privacy rule catalog.

    This catalog intentionally describes rule intent, defaults, and blocking
    policy only. It does not expose scanner implementation details. A backend
    can choose how to execute an enabled rule and return normalized findings
    with matching rule_id values.
    """

    return [
        {
            "id": "windows_user_paths",
            "label": "Windows user paths",
            "description": "Flags hard-coded Windows profile paths such as C:\\Users\\name\\..., C:/Users/name/..., and USERPROFILE/APPDATA references.",
            "recommended": True,
            "severity": "review",
            "engine": "builtin",
            "blocks_commit_when_finding": True,
        },
        {
            "id": "unix_user_paths",
            "label": "Unix/macOS user paths",
            "description": "Flags hard-coded local profile paths such as /home/name/..., /Users/name/..., file:/home/name/..., and ~/...",
            "recommended": True,
            "severity": "review",
            "engine": "builtin",
            "blocks_commit_when_finding": True,
        },
        {
            "id": "user_names",
            "label": "User names in local context",
            "description": "Flags local user names found in paths, environment dumps, Git config origins, stack traces, logs, and command transcripts.",
            "recommended": True,
            "severity": "review",
            "engine": "builtin",
            "blocks_commit_when_finding": True,
        },
        {
            "id": "secrets",
            "label": "API keys and secrets",
            "description": "Flags likely API keys, tokens, passwords, private keys, webhook URLs, and high-entropy secret-looking values.",
            "recommended": True,
            "severity": "critical",
            "engine": "builtin",
            "blocks_commit_when_finding": True,
        },
        {
            "id": "detect_secrets",
            "label": "detect-secrets integration",
            "description": "Uses the backend detect-secrets integration as an additional security/privacy filter when available.",
            "recommended": True,
            "severity": "critical",
            "engine": "detect-secrets",
            "blocks_commit_when_finding": True,
        },
    ]


def security_rule_by_id() -> dict[str, dict[str, Any]]:
    return {str(rule["id"]): rule for rule in security_rule_catalog()}


def load_project_security_policy(root: Path) -> dict[str, Any]:
    policy_path = SECURITY_RULES_POLICY_PATH
    payload: dict[str, Any] = {
        "exists": False,
        "project_policy_path": policy_path,
        "rules": {},
        "errors": [],
        "unknown_rules": [],
    }
    full = safe_repo_file_path(root, policy_path)
    if full is None or not full.exists():
        return payload
    payload["exists"] = True
    try:
        raw = json.loads(full.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        payload["errors"].append(f"could_not_read_policy:{type(exc).__name__}")
        return payload
    if not isinstance(raw, dict):
        payload["errors"].append("policy_json_must_be_object")
        return payload
    if raw.get("policy_version", SECURITY_POLICY_VERSION) != SECURITY_POLICY_VERSION:
        payload["errors"].append("unsupported_policy_version")
    raw_rules = raw.get("rules", {})
    if raw_rules is None:
        raw_rules = {}
    if not isinstance(raw_rules, dict):
        payload["errors"].append("policy_rules_must_be_object")
        return payload

    known = set(security_rule_by_id())
    overrides: dict[str, bool] = {}
    unknown: list[str] = []
    for rule_id, enabled in raw_rules.items():
        rule_key = str(rule_id)
        if rule_key not in known:
            unknown.append(rule_key)
            continue
        if isinstance(enabled, bool):
            overrides[rule_key] = enabled
        else:
            payload["errors"].append(f"rule_override_must_be_boolean:{rule_key}")
    payload["rules"] = overrides
    payload["unknown_rules"] = sorted(unknown)
    return payload


def enabled_security_rule_ids(root: Path, policy: dict[str, Any] | None = None) -> set[str]:
    policy = policy if policy is not None else load_project_security_policy(root)
    overrides = dict(policy.get("rules") or {})
    enabled: set[str] = set()
    for rule in security_rule_catalog():
        rule_id = str(rule.get("id") or "")
        recommended = bool(rule.get("recommended", True))
        is_enabled = bool(overrides[rule_id]) if rule_id in overrides else recommended
        if is_enabled:
            enabled.add(rule_id)
    return enabled


def _security_policy_output(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "exists": bool(policy.get("exists")),
        "errors": list(policy.get("errors") or []),
        "unknown_rules": list(policy.get("unknown_rules") or []),
    }


def _security_rule_base_items(root: Path, policy: dict[str, Any]) -> list[dict[str, Any]]:
    overrides = dict(policy.get("rules") or {})
    detect_secrets_status = detect_secrets_availability()
    rules: list[dict[str, Any]] = []
    for spec in security_rule_catalog():
        rule_id = str(spec.get("id") or "")
        recommended = bool(spec.get("recommended", True))
        saved = rule_id in overrides
        enabled = bool(overrides[rule_id]) if saved else recommended
        engine = str(spec.get("engine") or "builtin")
        availability = (
            dict(detect_secrets_status)
            if rule_id == "detect_secrets"
            else {
                "available": True,
                "status": "available",
                "engine": engine,
                "method": "builtin",
                "executable": "",
                "install_hint": "",
            }
        )
        available = bool(availability.get("available"))
        item = {
            "id": rule_id,
            "label": spec.get("label", rule_id),
            "description": spec.get("description", ""),
            "enabled": enabled,
            "recommended": recommended,
            "source": "saved" if saved else "default",
            "engine": engine,
            "severity": spec.get("severity", "review"),
            "available": available,
            "availability": availability,
            "availability_status": str(availability.get("status") or ("available" if available else "unavailable")),
            "install_hint": str(availability.get("install_hint") or ""),
            "blocks_commit_when_finding": bool(spec.get("blocks_commit_when_finding", True)),
        }
        if not enabled:
            item["disabled_reason"] = "disabled_by_project_policy" if saved else "disabled_by_recommended_default"
        if enabled and not available:
            item["unavailable_reason"] = "enabled_rule_engine_unavailable"
        rules.append(item)
    return rules


def security_rule_catalog_output(root: Path, *, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return rule availability and project policy state, without scan results."""

    policy = policy if policy is not None else load_project_security_policy(root)
    return {
        "kind": "security_rule_catalog",
        "policy_version": SECURITY_POLICY_VERSION,
        "project_policy_path": SECURITY_RULES_POLICY_PATH,
        "policy": _security_policy_output(policy),
        "rules": _security_rule_base_items(root, policy),
    }


def security_rules_output(
    root: Path,
    *,
    findings: list[dict[str, Any]] | None = None,
    ran_rule_ids: set[str] | None = None,
    policy: dict[str, Any] | None = None,
    scan_status: str = "not_run",
    execution_owner: str = "external_ui",
) -> dict[str, Any]:
    """Return scan-result state for security rules.

    git_dirty.py owns rule intent and project enablement state. It does not
    inspect candidate file contents. Actual security/content scanning is
    performed by the UI/backend after the user explicitly starts that scan.
    """

    policy = policy if policy is not None else load_project_security_policy(root)
    findings = list(findings or [])
    ran_rule_ids = set(ran_rule_ids or set())

    finding_counts: dict[str, int] = {}
    blocking_counts: dict[str, int] = {}
    for finding in findings:
        rule_id = str(finding.get("rule_id") or "")
        if not rule_id:
            continue
        finding_counts[rule_id] = finding_counts.get(rule_id, 0) + 1
        if finding.get("blocks_commit"):
            blocking_counts[rule_id] = blocking_counts.get(rule_id, 0) + 1

    rules: list[dict[str, Any]] = []
    for item in _security_rule_base_items(root, policy):
        rule_id = str(item.get("id") or "")
        enabled = bool(item.get("enabled"))
        available = bool(item.get("available", True))
        ran = bool(enabled and available and rule_id in ran_rule_ids)
        item = dict(item)
        item.update({
            "ran": ran,
            "scan_state": "unavailable" if enabled and not available else ("ran" if ran else "pending"),
            "finding_count": int(finding_counts.get(rule_id, 0)) if enabled else 0,
            "blocking_finding_count": int(blocking_counts.get(rule_id, 0)) if enabled else 0,
        })
        rules.append(item)

    enabled_rules = [rule for rule in rules if rule.get("enabled")]
    available_rules = [rule for rule in rules if rule.get("available")]
    unavailable_enabled_rules = [
        str(rule.get("id") or "")
        for rule in enabled_rules
        if not rule.get("available", True)
    ]
    total_findings = sum(int(rule.get("finding_count") or 0) for rule in rules)
    blocking_findings = sum(int(rule.get("blocking_finding_count") or 0) for rule in rules)
    if unavailable_enabled_rules or blocking_findings:
        gate_status = "blocked"
    elif scan_status in {"passed", "clean"}:
        gate_status = "passed"
    else:
        gate_status = "pending"

    return {
        "kind": "security_rule_scan",
        "status": scan_status,
        "gate_status": gate_status,
        "execution_owner": execution_owner,
        "git_dirty_content_scan": False,
        "policy_version": SECURITY_POLICY_VERSION,
        "project_policy_path": SECURITY_RULES_POLICY_PATH,
        "policy": _security_policy_output(policy),
        "rules": rules,
        "summary": {
            "rule_count": len(rules),
            "available_rule_count": len(available_rules),
            "enabled_rule_count": len(enabled_rules),
            "finding_count": total_findings,
            "blocking_finding_count": blocking_findings,
            "unavailable_enabled_rule_count": len(unavailable_enabled_rules),
            "unavailable_enabled_rules": unavailable_enabled_rules,
        },
    }


def rule_blocks_commit(rule_id: str) -> bool:
    rule = security_rule_by_id().get(rule_id)
    if not rule:
        return False
    return bool(rule.get("blocks_commit_when_finding", True))


def security_finding_blocks_commit(rule_id: str, severity: str) -> bool:
    if not rule_blocks_commit(rule_id):
        return False
    return severity in {"critical", "review"}


def redact_middle(value: str, *, keep_start: int = 4, keep_end: int = 4) -> str:
    value = value.strip()
    if not value:
        return ""
    if len(value) <= keep_start + keep_end + 6:
        return value[:1] + "…REDACTED…"
    return f"{value[:keep_start]}…REDACTED…{value[-keep_end:]}"


def format_privacy_evidence(value: str) -> str:
    """Return browser-safe review evidence without exposing arbitrarily long values.

    Values up to 202 characters are emitted in full. Longer values keep the
    first and last 100 characters with a literal ASCII ellipsis between them.
    """

    value = str(value or "").strip()
    if len(value) > PRIVACY_EVIDENCE_TRUNCATE_THRESHOLD:
        return f"{value[:PRIVACY_EVIDENCE_EDGE_CHARS]}...{value[-PRIVACY_EVIDENCE_EDGE_CHARS:]}"
    return value


def redact_email(value: str) -> str:
    user, sep, domain = value.partition("@")
    if not sep:
        return redact_middle(value)
    if not user:
        return "…@" + domain
    return f"{user[:1]}…@{domain}"


def redact_privacy_match(kind: str, value: str) -> str:
    if kind in {"email", "local_user_email"}:
        return redact_email(value)
    if kind == "windows_user_path":
        redacted = re.sub(r"(?i)(\b[A-Z]:(?:\\|/)(?:Users|Documents and Settings)(?:\\|/))[^\\/ \r\n\"'<>|]+", r"\1…", value)
        return re.sub(r"(?i)%(?:USERPROFILE|APPDATA|LOCALAPPDATA)%", "%…%", redacted)
    if kind == "unix_user_path":
        redacted = re.sub(r"((?:file:)?/(?:Users|home)/)[^/\s\"'<>|]+", r"\1…", value)
        return re.sub(r"~(?=/)", "~…", redacted)
    if kind == "local_user_name":
        return redact_middle(value, keep_start=1, keep_end=0)
    return redact_middle(value)


def privacy_finding(
    path: str,
    line_no: int,
    kind: str,
    severity: str,
    value: str,
    *,
    rule_id: str,
) -> dict[str, Any]:
    evidence_redacted = redact_privacy_match(kind, value)
    evidence = format_privacy_evidence(value)
    return {
        "rule_id": rule_id,
        "path": path,
        "line": line_no,
        "kind": kind,
        "severity": severity,
        "evidence": evidence,
        "evidence_truncated": evidence != str(value or "").strip(),
        "evidence_length": len(str(value or "").strip()),
        "evidence_redacted": evidence_redacted,
        "snippet": evidence,
        "blocks_commit": security_finding_blocks_commit(rule_id, severity),
    }


def extract_windows_user_name(value: str) -> str:
    match = WINDOWS_USER_NAME_RE.search(value)
    return match.group(1) if match else ""


def is_allowed_windows_user_path_example(value: str) -> bool:
    user_name = extract_windows_user_name(value).strip().casefold()
    return bool(user_name and user_name in WINDOWS_USER_PATH_ALLOWED_EXAMPLE_USERS)


def should_flag_windows_user_path(value: str) -> bool:
    value = str(value or "")
    if re.search(r"(?i)%(?:USERPROFILE|APPDATA|LOCALAPPDATA)%", value):
        return True
    if is_allowed_windows_user_path_example(value):
        return False
    return bool(extract_windows_user_name(value))


def extract_unix_user_name(value: str) -> str:
    match = UNIX_USER_PATH_RE.search(value)
    return match.group(1) if match else ""


def add_finding_once(findings: list[dict[str, Any]], finding: dict[str, Any], seen: set[tuple[str, str, str, int]]) -> None:
    key = (
        str(finding.get("rule_id") or ""),
        str(finding.get("kind") or ""),
        str(finding.get("evidence_redacted") or ""),
        int(finding.get("line") or 0),
    )
    if key in seen:
        return
    seen.add(key)
    findings.append(finding)


def scan_privacy_line(path: str, line_no: int, line: str, enabled_rule_ids: set[str]) -> list[dict[str, Any]]:
    """Do not scan content inside git_dirty.py.

    This function is intentionally a no-op. It remains only as a compatibility
    shim for older callers; the UI/backend owns all file-content scans.
    """

    return []


def scan_file_privacy(root: Path, rec: dict[str, Any], enabled_rule_ids: set[str]) -> list[dict[str, Any]]:
    """Do not open or inspect candidate files inside git_dirty.py.

    git_dirty.py may report repository status, paths, classifications derived
    from path names, and rule catalog/policy state. The actual file-content
    scan must be kicked off by the user in the UI/backend.
    """

    return []

def file_privacy_risk(findings: list[dict[str, Any]]) -> str:
    severities = {str(f.get("severity") or "") for f in findings}
    if "critical" in severities:
        return "critical"
    if "review" in severities:
        return "review"
    return "clean"


def scan_privacy_for_files(root: Path, files: list[dict[str, Any]]) -> dict[str, Any]:
    """Prepare external security-scan state without reading candidate files.

    The old implementation opened each dirty file and searched contents. That
    crossed the intended boundary. git_dirty.py now only emits the enabled rule
    set and the files that may need a UI/backend scan.
    """

    policy = load_project_security_policy(root)
    enabled_rule_ids_set = enabled_security_rule_ids(root, policy)
    enabled_rule_ids = sorted(enabled_rule_ids_set)
    candidate_paths = [
        str(rec.get("path") or "")
        for rec in files
        if str(rec.get("path") or "") and not rec.get("deleted")
    ]

    return {
        "status": "pending_external_scan",
        "execution_owner": "external_ui",
        "git_dirty_content_scan": False,
        "by_path": {},
        "findings": [],
        "candidate_paths": candidate_paths,
        "security_rules": security_rules_output(
            root,
            findings=[],
            ran_rule_ids=set(),
            policy=policy,
            scan_status="pending_external_scan",
            execution_owner="external_ui",
        ),
        "summary": {
            "critical": 0,
            "review": 0,
            "blocking": 0,
            "clean": 0,
            "scanned_files": 0,
            "files_with_findings": 0,
            "candidate_files": len(candidate_paths),
            "enabled_rule_count": len(enabled_rule_ids),
            "enabled_rules": enabled_rule_ids,
            "requires_user_scan": bool(enabled_rule_ids and candidate_paths),
            "git_dirty_content_scan": False,
            "status": "pending_external_scan",
        },
        "note": "git_dirty.py did not read candidate file contents. Run the UI/backend security scan to populate findings before commit.",
    }


def secrets_filter_payload(
    root: Path,
    files: list[dict[str, Any]],
    *,
    findings: list[dict[str, Any]] | None = None,
    ran_rule_ids: set[str] | None = None,
    scan_status: str = "pending_external_scan",
) -> dict[str, Any]:
    """Return the standalone Secrets / Filter workflow-card model.

    The card is a rule-review and status surface. The backend owns catalog,
    project-policy merge, availability checks, and eventual scan results.
    """

    policy = load_project_security_policy(root)
    findings = list(findings or [])
    rules_state = security_rules_output(
        root,
        findings=findings,
        ran_rule_ids=ran_rule_ids or set(),
        policy=policy,
        scan_status=scan_status,
        execution_owner="external_ui",
    )
    candidate_paths = [
        str(rec.get("path") or "")
        for rec in files
        if str(rec.get("path") or "") and not rec.get("deleted")
    ]
    summary = dict(rules_state.get("summary") or {})
    summary["candidate_file_count"] = len(candidate_paths)
    summary["findings_by_rule_count"] = int(summary.get("finding_count") or 0)
    requires_user_scan = bool(summary.get("enabled_rule_count") and candidate_paths and scan_status == "pending_external_scan")
    unavailable_enabled_rules = list(summary.get("unavailable_enabled_rules") or [])
    gate_status = str(rules_state.get("gate_status") or "pending")
    if scan_status == "pending_external_scan" and requires_user_scan:
        gate_status = "pending"
    elif unavailable_enabled_rules:
        gate_status = "blocked"
    summary["gate_status"] = gate_status
    summary["requires_user_scan"] = requires_user_scan

    merged_rules = list(rules_state.get("rules") or [])
    saved_policy_exists = bool(policy.get("exists"))
    saved_rules = merged_rules if saved_policy_exists else []
    saved_summary = dict(summary) if saved_policy_exists else {
        "rule_count": 0,
        "available_rule_count": 0,
        "enabled_rule_count": 0,
        "finding_count": 0,
        "blocking_finding_count": 0,
        "unavailable_enabled_rule_count": 0,
        "unavailable_enabled_rules": [],
        "candidate_file_count": len(candidate_paths),
        "findings_by_rule_count": 0,
        "gate_status": "pending",
        "requires_user_scan": False,
    }
    pending_result = {
        "mode": "pending",
        "label": "No filter scan has run yet",
        "status": scan_status,
        "gate_status": gate_status,
        "execution_owner": "external_ui",
        "git_dirty_content_scan": False,
        "candidate_paths": candidate_paths,
        "findings": findings,
        "findings_by_rule": [],
        "pending_message": "Review rule choices, save them, then run either a draft selected-rule scan or the full saved policy filter check before commit.",
        "note": "git_dirty.py provides merged rule state and availability only; the UI/backend performs scans after user review.",
    }

    return {
        "kind": "secrets_filter",
        "title": "SECRETS / FILTER",
        "subtitle": "Security/privacy filtering before commit",
        "policy_path": SECURITY_RULES_POLICY_PATH,
        "policy_version": SECURITY_POLICY_VERSION,
        "policy": _security_policy_output(policy),
        "rules": merged_rules,
        "saved_rules": saved_rules,
        "saved_summary": saved_summary,
        "saved_policy_exists": saved_policy_exists,
        "security_rules": rules_state,
        "summary": summary,
        "scan": pending_result,
        "scan_result": pending_result,
        "actions": [
            {"id": "merge_rule_choices", "label": "Merge rule choices", "implemented": True},
            {"id": "update_saved_rule_choices", "label": "Save saved rule choice", "implemented": True},
            {"id": "run_selected_rules", "label": "Run selected rules only", "implemented": True},
            {"id": "run_saved_filter_check", "label": "Run full saved filter check", "implemented": True},
        ],
    }

def commit_file_item(
    root: Path,
    rec: dict[str, Any],
    *,
    selected: bool = False,
    privacy_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    path = normalize_repo_path(str(rec.get("path") or ""))
    full = safe_repo_file_path(root, path)
    mtime = 0.0
    modified = ""
    if full is not None and full.exists():
        try:
            mtime = full.stat().st_mtime
            modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
        except OSError:
            mtime = 0.0
            modified = ""
    labels = list(rec.get("classifications") or [])
    findings = list(privacy_findings or [])
    return {
        "path": path,
        "status": str(rec.get("status") or git_status_label(rec)),
        "untracked": bool(rec.get("untracked")),
        "staged": bool(rec.get("staged")),
        "unstaged": bool(rec.get("unstaged")),
        "classifications": labels,
        "risk": risk_for_file(rec),
        "privacy_risk": file_privacy_risk(findings),
        "privacy_findings_count": len(findings),
        "blocking_security_findings_count": sum(1 for finding in findings if finding.get("blocks_commit")),
        "privacy_findings": findings[:3],
        "selected_by_default": bool(selected),
        "mtime": mtime,
        "modified": modified,
    }


def commit_identity_display_source(identity: dict[str, Any]) -> str:
    name_source = str(identity.get("name_source") or "missing")
    email_source = str(identity.get("email_source") or "missing")
    if name_source == email_source:
        return name_source
    if name_source == "missing" and email_source == "missing":
        return "missing"
    return "mixed"


def commit_identity_scope_value(identity: dict[str, Any]) -> str:
    """Return the initial identity-scope choice for the editable commit card."""

    if not identity.get("ready"):
        return "save_local"
    return "use_existing"


def commit_card_field(
    label: str,
    value: Any,
    *,
    editable: bool = True,
    source: str = "git_dirty_initial",
    placeholder: str = "",
    input_type: str = "text",
    options: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    field = {
        "label": label,
        "value": value,
        "editable": editable,
        "source": source,
        "placeholder": placeholder,
        "input_type": input_type,
    }
    if options is not None:
        field["options"] = options
    return field


def commit_card_paths(items: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("path") or "") for item in items if item.get("path")]


def commit_card_privacy_decisions(
    review: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    decisions: dict[str, dict[str, Any]] = {}
    for item in review:
        path = str(item.get("path") or "")
        if not path:
            continue
        decisions[path] = {
            "decision": "needs_user_review",
            "editable": True,
            "allowed_decisions": ["exclude_from_commit", "include_after_review", "open_file_detail"],
            "privacy_risk": item.get("privacy_risk", "review"),
            "privacy_findings_count": int(item.get("privacy_findings_count") or 0),
        }
    for item in blocked:
        path = str(item.get("path") or "")
        if not path:
            continue
        decisions[path] = {
            "decision": "must_fix_before_commit",
            "editable": False,
            "allowed_decisions": ["open_file_detail"],
            "privacy_risk": item.get("privacy_risk", "critical"),
            "privacy_findings_count": int(item.get("privacy_findings_count") or 0),
        }
    return decisions


def commit_card_gate_index(commit_review: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(gate.get("id") or ""): gate for gate in commit_review.get("gates") or []}


def commit_card_step(
    order: int,
    step_id: str,
    label: str,
    gate: dict[str, Any] | None,
    *,
    selected: bool = False,
    locked: bool = False,
    required: bool = True,
    why: str = "",
) -> dict[str, Any]:
    ready = bool(gate.get("ready")) if gate is not None else False
    mark = "✓" if ready else "!"
    if locked:
        mark = "🔒" if not ready else "✓"
    return {
        "order": order,
        "id": step_id,
        "label": label,
        "ready": ready,
        "required": bool(gate.get("required", required)) if gate is not None else required,
        "locked": locked,
        "mark": mark,
        "selected": selected,
        "center_panel": step_id,
        "why": why,
    }


def commit_card_fast_sanity_contract() -> dict[str, Any]:
    return {
        "cost": "client_only",
        "purpose": "Validate user edits against the initial git_dirty snapshot without rerunning the expensive planner.",
        "checks": [
            {
                "id": "branch_present",
                "kind": "required_text",
                "field": "editable_state.branch",
                "blocks": ["repo_branch", "create_commit"],
                "message": "Branch is required.",
            },
            {
                "id": "commit_message_present",
                "kind": "required_text",
                "field": "editable_state.commit_message",
                "blocks": ["create_commit"],
                "message": "Commit message is required.",
            },
            {
                "id": "git_user_name_present",
                "kind": "required_text",
                "field": "editable_state.git_user_name",
                "blocks": ["identity", "create_commit"],
                "message": "Git user.name is required.",
            },
            {
                "id": "git_user_email_present",
                "kind": "required_text",
                "field": "editable_state.git_user_email",
                "blocks": ["identity", "create_commit"],
                "message": "Git user.email is required.",
            },
            {
                "id": "git_user_email_shape",
                "kind": "regex",
                "field": "editable_state.git_user_email",
                "pattern": EMAIL_SHAPE_RE.pattern,
                "blocks": ["identity", "create_commit"],
                "message": "Git user.email must look like an email address.",
            },
            {
                "id": "selected_files_present",
                "kind": "non_empty_list",
                "field": "editable_state.selected_paths",
                "blocks": ["file_basket", "create_commit"],
                "message": "Select at least one file before staging.",
            },
            {
                "id": "critical_paths_not_selected",
                "kind": "set_disjoint",
                "left": "editable_state.selected_paths",
                "right": "blocked_paths",
                "blocks": ["secrets_filter", "create_commit"],
                "message": "Blocked Secrets / Filter findings cannot be selected until fixed.",
            },
            {
                "id": "gitignore_reviewed_when_required",
                "kind": "boolean_true_when",
                "field": "editable_state.gitignore_reviewed",
                "when": "gitignore_gate_initially_blocked",
                "blocks": ["gitignore", "create_commit"],
                "message": ".gitignore review is required before staging this first commit.",
            },
            {
                "id": "stage_preview_confirmed",
                "kind": "boolean_true",
                "field": "editable_state.stage_preview_confirmed",
                "blocks": ["stage_preview", "create_commit"],
                "message": "Stage preview must be reviewed before creating the commit.",
            },
        ],
        "rebuild_triggers": [
            {
                "id": "gitignore_changed",
                "when": ".gitignore or .git/info/exclude is edited on disk",
                "reason": "Ignore rules change the candidate file set and generated/runtime exclusions.",
                "command_template": "python git_dirty.py plan --repo <repo> --json",
            },
            {
                "id": "privacy_relevant_file_changed",
                "when": "Any selected/review/blocked file content changes on disk",
                "reason": "Privacy findings and clean/review/critical buckets may no longer match the initial scan.",
                "command_template": "python git_dirty.py plan --repo <repo> --json",
            },
            {
                "id": "git_status_changed",
                "when": "Files are created, deleted, renamed, staged, unstaged, or reverted",
                "reason": "The file basket and stage preview must reflect current Git reality.",
                "command_template": "python git_dirty.py plan --repo <repo> --json",
            },
            {
                "id": "repo_identity_saved",
                "when": "The wizard writes git config --local user.name/user.email",
                "reason": "Git identity source/readiness should be refreshed after a disk-backed config write.",
                "command_template": "python git_dirty.py plan --repo <repo> --json",
            },
            {
                "id": "branch_ref_changed",
                "when": "The wizard runs a branch rename or symbolic-ref command",
                "reason": "HEAD/branch metadata should be refreshed after changing refs.",
                "command_template": "python git_dirty.py plan --repo <repo> --json",
            },
        ],
        "no_rebuild_needed_for": [
            "Editing the branch text before running a branch command.",
            "Editing the commit message.",
            "Editing name/email fields before saving or committing.",
            "Toggling review-file inclusion decisions against the initial file basket.",
            "Checking local stage-preview confirmation boxes.",
        ],
    }


def commit_card_payload(commit_review: dict[str, Any]) -> dict[str, Any]:
    """Return a UI-ready opened commit-card model initialized from git_dirty facts.

    The UI can keep the `editable_state` as its quick mutable copy. Simple
    user edits should run `fast_sanity` locally; only the listed rebuild
    triggers need a fresh git_dirty plan.
    """

    identity = commit_review.get("commit_identity") or commit_identity_empty()
    head = commit_review.get("head") or {}
    branch = str((commit_review.get("branch") or {}).get("current") or head.get("branch") or head.get("default_branch") or "main")
    source = commit_identity_display_source(identity)
    candidate_groups = commit_review.get("candidate_groups") or {}
    selected = list(candidate_groups.get("selected_by_default") or [])
    review = list(candidate_groups.get("review_before_selecting") or [])
    blocked = list(candidate_groups.get("blocked_possible_secrets") or [])
    excluded = list(candidate_groups.get("excluded_generated_runtime") or [])
    selected_paths = commit_card_paths(selected)
    review_paths = commit_card_paths(review)
    blocked_paths = commit_card_paths(blocked)
    excluded_paths = commit_card_paths(excluded)
    blockers = [str(item) for item in commit_review.get("commit_blockers") or []]
    locked_reason = str(commit_review.get("locked_reason") or "")
    gitignore_blocked = "gitignore_review_required" in blockers or bool(locked_reason)
    commit_message = str(commit_review.get("commit_message") or DEFAULT_COMMIT_MESSAGE)
    identity_scope = commit_identity_scope_value(identity)
    privacy_summary = dict((commit_review.get("privacy_scan") or {}).get("summary") or {})
    security_rules = dict(commit_review.get("security_rules") or (commit_review.get("privacy_scan") or {}).get("security_rules") or {})
    gates = commit_card_gate_index(commit_review)

    editable_state = {
        "branch": branch,
        "commit_message": commit_message,
        "git_user_name": str(identity.get("name") or ""),
        "git_user_email": str(identity.get("email") or ""),
        "identity_scope": identity_scope,
        "selected_paths": selected_paths,
        "review_paths": review_paths,
        "blocked_paths": blocked_paths,
        "excluded_paths": excluded_paths,
        "privacy_decisions": commit_card_privacy_decisions(review, blocked),
        "gitignore_reviewed": not gitignore_blocked,
        "stage_preview_confirmed": False,
    }

    status_strip = {
        "head": str(head.get("head_state") or "unknown"),
        "branch": branch,
        "identity": source,
        "commit_ready": "yes" if commit_review.get("commit_ready") else "no",
    }

    config_fields = {
        "branch": commit_card_field("Branch", branch, placeholder="master"),
        "commit_message": commit_card_field("Commit message", commit_message, placeholder=DEFAULT_COMMIT_MESSAGE),
        "git_user_name": commit_card_field("Name", editable_state["git_user_name"], placeholder="Your Name"),
        "git_user_email": commit_card_field("Email", editable_state["git_user_email"], placeholder="you@example.com", input_type="email"),
        "identity_scope": commit_card_field(
            "Scope",
            identity_scope,
            input_type="radio",
            options=[
                {
                    "value": "use_existing",
                    "label": "use existing identity",
                    "description": "Use the effective Git identity from the initial git_dirty snapshot without writing repo config.",
                },
                {
                    "value": "save_local",
                    "label": "save local repo identity",
                    "description": "Preview git config --local commands and save the edited name/email for this repo.",
                },
            ],
        ),
    }

    secrets_blocked = bool(security_scan_pending := privacy_summary.get("requires_user_scan")) or bool(privacy_summary.get("blocking", 0)) or bool(blocked)
    gate_summary_ready = not gitignore_blocked and not secrets_blocked
    gate_summary_text = (
        ".gitignore and Secrets / Filter gates are passing."
        if gate_summary_ready
        else "Review upstream .gitignore or Secrets / Filter gates before staging."
    )

    left_steps = [
        commit_card_step(1, "repo_branch", "Repo / Branch", gates.get("repo_head"), selected=True, why=f"HEAD is {status_strip['head']}; branch is {branch}."),
        commit_card_step(2, "identity", "Identity", gates.get("commit_identity"), why=f"Ready from {source} config." if identity.get("ready") else "Git commit identity is missing or incomplete."),
        commit_card_step(3, "gate_summary", "Gate summary", {"ready": gate_summary_ready, "required": True}, why=gate_summary_text),
        commit_card_step(4, "file_basket", "File basket", gates.get("file_basket"), why=f"{len(selected_paths)} files selected by default."),
        commit_card_step(5, "stage_preview", "Stage preview", gates.get("stage_preview"), why="Stage selected files and review the cached diff/stat before committing."),
        commit_card_step(6, "create_commit", "Create commit", {"ready": bool(commit_review.get("commit_ready")), "required": True}, locked=not bool(commit_review.get("commit_ready")), why="Locked until required checks pass." if not commit_review.get("commit_ready") else "Ready to create a local commit."),
    ]

    center_panels = {
        "repo_branch": {
            "title": "Repo / Branch",
            "fields": {
                "head_state": commit_card_field("HEAD state", status_strip["head"], editable=False),
                "branch": config_fields["branch"],
            },
            "validation_messages": [
                "This commit will create the first HEAD." if head.get("needs_first_commit") else "HEAD already exists.",
            ],
            "actions": [
                {"id": "use_current_branch", "label": "Use current branch", "implemented": False},
                {"id": "rename_first_branch_to_main", "label": "Rename first branch to main", "implemented": False},
                {"id": "preview_branch_command", "label": "Preview branch command", "implemented": True},
            ],
            "commands": [
                "git branch --show-current",
                f"git symbolic-ref HEAD refs/heads/{branch}",
            ],
            "explanation": "Branch text can be edited locally. Rebuild only after a branch/ref command is actually run.",
        },
        "identity": {
            "title": "Commit Identity",
            "fields": {
                "git_user_name": config_fields["git_user_name"],
                "git_user_email": config_fields["git_user_email"],
                "identity_scope": config_fields["identity_scope"],
            },
            "detected_source": source,
            "local": identity.get("local") or {"name": "", "email": ""},
            "global": identity.get("global") or {"name": "", "email": ""},
            "warnings": list(identity.get("warnings") or []),
            "problems": list(identity.get("problems") or []),
            "actions": [
                {"id": "preview_identity_commands", "label": "Preview identity commands", "implemented": True},
                {"id": "mark_identity_reviewed", "label": "Mark identity reviewed", "implemented": False},
            ],
            "commands": [
                join_command(["git", "config", "--local", "user.name", editable_state["git_user_name"] or "Your Name"]),
                join_command(["git", "config", "--local", "user.email", editable_state["git_user_email"] or "you@example.com"]),
            ],
            "explanation": "Name/email edits are cheap client-side changes until the user saves local repo identity or creates a commit.",
        },
        "gate_summary": {
            "title": "Gate Summary",
            "gates": {
                "gitignore": {
                    "state": "blocked" if gitignore_blocked else "passed",
                    "label": ".gitignore",
                    "summary": locked_reason or ("Generated/runtime files still need ignore review." if gitignore_blocked else ".gitignore gate is passing."),
                    "step_id": "update_gitignore_before_initial_commit",
                },
                "secrets_filter": {
                    "state": "blocked" if secrets_blocked else "passed",
                    "label": "Secrets / Filter",
                    "summary": (
                        "Secrets / Filter needs review before commit."
                        if secrets_blocked
                        else "Secrets / Filter gate is passing."
                    ),
                    "step_id": "secrets_filter",
                },
            },
            "actions": [
                {"id": "open_gitignore_card", "label": "Open .gitignore card", "implemented": False},
                {"id": "open_secrets_filter_card", "label": "Open Secrets / Filter card", "implemented": False},
                {"id": "rerun_planner", "label": "Re-run planner", "implemented": True},
            ],
            "validation_messages": [
                message
                for message in [
                    locked_reason if gitignore_blocked else "",
                    "Secrets / Filter review is required before staging." if secrets_blocked else "",
                ]
                if message
            ],
            "explanation": "This step links to upstream cards instead of duplicating their rule editors.",
        },
        "file_basket": {
            "title": "File Basket",
            "counts": {
                "selected_by_default": len(selected),
                "needs_review": len(review),
                "blocked": len(blocked),
                "excluded_generated_runtime": len(excluded),
            },
            "actions": [
                {"id": "select_clean_files", "label": "Select clean files", "implemented": False},
                {"id": "clear_selection", "label": "Clear selection", "implemented": False},
                {"id": "show_review_files", "label": "Show review files", "implemented": False},
                {"id": "hide_generated_runtime_files", "label": "Hide generated/runtime files", "implemented": False},
            ],
            "explanation": "The right pane owns file rows; this center step controls basket-level decisions.",
        },
        "stage_preview": {
            "title": "Stage Preview",
            "commands": [
                "git add -- <selected files>",
                "git status --short",
                "git diff --cached --stat",
                "git diff --cached --check",
            ],
            "checklist": [
                {"id": "staged_only_selected_files", "label": "I staged only the selected files", "checked": False},
                {"id": "reviewed_staged_diff", "label": "I reviewed the staged diff/stat", "checked": False},
                {"id": "upstream_gates_passing_or_accepted", "label": "Upstream gates are passing or explicitly accepted", "checked": False},
            ],
            "validation_messages": ["Stage preview is required before commit."],
            "explanation": "These confirmations are quick UI checks until Git staging actually changes.",
        },
        "create_commit": {
            "title": "Create Commit",
            "fields": {
                "commit_message": config_fields["commit_message"],
            },
            "blocked_by": blockers,
            "actions": [
                {
                    "id": "create_local_commit",
                    "label": "Create local commit",
                    "enabled": bool(commit_review.get("commit_ready")),
                    "implemented": False,
                },
            ],
            "commands": [
                join_command(["git", "commit", "-m", commit_message]),
                "python git_dirty.py plan --json",
            ],
            "explanation": "Local commit only; no push and no remote setup.",
        },
    }

    return {
        "schema_version": COMMIT_CARD_SCHEMA_VERSION,
        "title": "TAKE SNAPSHOT / COMMIT",
        "subtitle": "Local commit only · No push · No remote setup",
        "initial_source": "git_dirty.py plan",
        "status_strip": status_strip,
        "config_strip": {
            "title": "CONFIG STRIP",
            "fields": config_fields,
        },
        "editable_state": editable_state,
        "left_pane": {
            "title": "COMMIT STEPS",
            "steps": left_steps,
        },
        "center_pane": {
            "title": "SELECTED WORK AREA",
            "selected_step": "gate_summary",
            "panels": center_panels,
        },
        "right_pane": {
            "title": "COMMIT FILE BASKET",
            "sort": "newest_edit_first",
            "groups_source": "commit_review.candidate_groups",
            "group_order": [
                "selected_by_default",
                "review_before_selecting",
                "blocked_possible_secrets",
                "excluded_generated_runtime",
            ],
            "counts": {
                "selected_by_default": len(selected),
                "review_before_selecting": len(review),
                "blocked_possible_secrets": len(blocked),
                "excluded_generated_runtime": len(excluded),
            },
        },
        "fast_sanity": commit_card_fast_sanity_contract(),
        "stubbed_actions_ready_for_ui": True,
    }



def commit_review_payload(
    root: Path,
    detection: dict[str, Any],
    files: list[dict[str, Any]],
    *,
    default_paths: list[str] | None = None,
    mode: str = "normal_commit",
    locked_reason: str = "",
    commit_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    identity = commit_identity or commit_identity_empty()
    default_set = set(default_paths or [])
    privacy_scan = scan_privacy_for_files(root, files)
    findings_by_path = privacy_scan.get("by_path", {})
    security_scan_pending = bool((privacy_scan.get("summary") or {}).get("requires_user_scan"))
    selected: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for rec in files:
        path = str(rec.get("path") or "")
        if not path:
            continue
        labels = set(rec.get("classifications") or [])
        findings = list(findings_by_path.get(path) or [])
        privacy_risk = file_privacy_risk(findings)
        blocking_findings = any(finding.get("blocks_commit") for finding in findings)
        default_selected = path in default_set and privacy_risk == "clean" and not blocking_findings and not security_scan_pending
        item = commit_file_item(root, rec, selected=default_selected, privacy_findings=findings)

        if blocking_findings or privacy_risk == "critical" or "secret-looking" in labels:
            blocked.append(item)
        elif security_scan_pending and (rec.get("untracked") or rec.get("staged") or rec.get("unstaged")):
            review.append(item)
        elif privacy_risk == "review" or "local-environment" in labels:
            review.append(item)
        elif "generated" in labels:
            excluded.append(item)
        elif default_selected:
            selected.append(item)
        elif rec.get("untracked") or rec.get("staged") or rec.get("unstaged"):
            review.append(item)

    sort_key = lambda item: (float(item.get("mtime") or 0), item.get("path") or "")
    for group in (selected, review, blocked, excluded):
        group.sort(key=sort_key, reverse=True)

    privacy_summary = dict(privacy_scan.get("summary") or {})
    privacy_summary["clean"] = len(selected)
    privacy_summary["blocked_files"] = len(blocked)
    privacy_summary["review_files"] = len(review)
    privacy_summary["excluded"] = len(excluded)
    privacy_summary["blocking"] = int(privacy_summary.get("blocking") or 0)

    commit_blockers: list[str] = []
    commit_blockers.extend(identity.get("problems") or [])
    if locked_reason:
        commit_blockers.append("gitignore_review_required")
    if security_scan_pending:
        commit_blockers.append("security_scan_required")
    if privacy_summary.get("critical", 0):
        commit_blockers.append("critical_privacy_findings")
    if privacy_summary.get("blocking", 0):
        commit_blockers.append("blocking_security_findings")
    if blocked:
        commit_blockers.append("path_risk_review_required")
    if not selected:
        commit_blockers.append("no_selected_files")
    commit_blockers.append("stage_preview_required")
    commit_blockers = sorted(set(str(item) for item in commit_blockers if item))

    branch_name = detection.get("branch_name") or ""
    payload = {
        "mode": mode,
        "locked_reason": locked_reason,
        "commit_ready": False,
        "commit_blockers": commit_blockers,
        "gates": [
            {"id": "repo_head", "label": "Repo / HEAD", "ready": bool(detection.get("ok")), "required": True},
            {"id": "commit_identity", "label": "Commit identity", "ready": bool(identity.get("ready")), "required": True},
            {"id": "gitignore_gate", "label": ".gitignore gate", "ready": not bool(locked_reason), "required": True},
            {"id": "secrets_filter_gate", "label": "Secrets / Filter gate", "ready": not bool(security_scan_pending or privacy_summary.get("blocking", 0) or blocked), "required": True},
            {"id": "file_basket", "label": "File basket", "ready": bool(selected), "required": True},
            {"id": "stage_preview", "label": "Stage preview", "ready": False, "required": True},
        ],
        "head": {
            "has_head": bool(detection.get("has_head")),
            "head_state": detection.get("head_state", "unknown"),
            "branch": branch_name,
            "default_branch": branch_name or "main",
            "needs_first_commit": not bool(detection.get("has_head")),
        },
        "branch": {
            "current": branch_name,
            "default": branch_name or "main",
            "needs_confirmation": not bool(detection.get("has_head")),
        },
        "commit_identity": identity,
        "security_rules": privacy_scan.get("security_rules", security_rules_output(root)),
        "sort": "mtime_desc",
        "candidate_groups": {
            "selected_by_default": selected,
            "review_before_selecting": review,
            "blocked_possible_secrets": blocked,
            "excluded_generated_runtime": excluded,
        },
        "privacy_scan": {
            "status": privacy_scan.get("status", "pending_external_scan"),
            "execution_owner": privacy_scan.get("execution_owner", "external_ui"),
            "git_dirty_content_scan": False,
            "candidate_paths": privacy_scan.get("candidate_paths", []),
            "summary": privacy_summary,
            "findings": privacy_scan.get("findings", []),
            "security_rules": privacy_scan.get("security_rules", security_rules_output(root)),
            "note": "git_dirty.py did not read candidate file contents. Run the UI/backend security scan to populate findings before commit. Disabled rules remain visible and do not run.",
        },
    }
    payload["commit_message"] = DEFAULT_COMMIT_MESSAGE
    payload["opened_commit_card"] = commit_card_payload(payload)
    return payload



def repo_for_commands(status: dict[str, Any], fallback: Path) -> str:
    detection = status.get("git_detection", {})
    if detection.get("ok") and detection.get("worktree_root"):
        return str(Path(detection["worktree_root"]).resolve())
    return str(fallback.resolve())


def snapshot_dir_for_commands(status: dict[str, Any], fallback: Path, plan_id: str) -> str:
    detection = status.get("git_detection", {})
    base = detection.get("git_common_dir") or detection.get("git_dir")
    if base:
        return str(Path(base).resolve() / "git-dirty" / "snapshots" / plan_id)
    return str(fallback.resolve() / ".git" / "git-dirty" / "snapshots" / plan_id)


def script_for_commands(status: dict[str, Any], fallback: Path) -> str:
    repo = repo_for_commands(status, fallback)
    candidate = Path(repo) / "git_dirty.py"
    if candidate.exists():
        return str(candidate.resolve())
    return str(Path(__file__).resolve())


def add_paths_args(paths: list[str] | None) -> tuple[list[str], bool]:
    selected, truncated = limited_paths(paths)
    return ["--", *selected], truncated


def commands_for_step(item: dict[str, Any], status: dict[str, Any], input_path: Path, plan_id: str) -> list[dict[str, Any]]:
    action_id = item["id"]
    repo = repo_for_commands(status, input_path)
    script = script_for_commands(status, input_path)
    detection = status.get("git_detection", {})
    paths, truncated = limited_paths(item.get("paths"))
    path_args = ["--", *paths] if paths else []
    path_note = "Path list truncated; rerun with selected paths for a narrower command." if truncated else ""
    snapshot_dir = snapshot_dir_for_commands(status, input_path, plan_id)
    commands: list[dict[str, Any]] = []

    if action_id == "find_repository_root":
        commands.append(git_command(
            str(input_path.resolve()),
            ["rev-parse", "--show-toplevel", "--git-dir", "--git-common-dir", "--is-inside-work-tree"],
            purpose="Ask Git to prove the repository root and git directory.",
            template="git -C <input_path> rev-parse --show-toplevel --git-dir --git-common-dir --is-inside-work-tree",
        ))
    elif action_id == "choose_correct_repository_root":
        commands.append(git_command(
            str(input_path.resolve()),
            ["rev-parse", "--show-toplevel"],
            purpose="Show the parent repository Git detected so the operator can confirm or reject it.",
            template="git -C <input_path> rev-parse --show-toplevel",
        ))
    elif action_id in {"start_tracking_this_folder", "initialize_repository_here"}:
        commands.append(command_record(
            purpose="Show the disk-backed backend runner that should execute the validated fix payload.",
            template="# python tools/git/git_tool_fix_project_head.py <validated-payload.json>",
            command="# Backend safety runner:\n# python tools/git/git_tool_fix_project_head.py <validated-payload.json>",
            safe=True,
            command_kind="comment",
            implemented=True,
        ))
        commands.append(git_command(
            str(input_path.resolve()),
            ["init"],
            purpose="Initialize this folder as a Git repository.",
            template="git -C <input_path> init",
        ))
        commands.append(git_command(
            str(input_path.resolve()),
            ["status", "--short", "--branch"],
            purpose="Confirm repository status after initialization.",
            template="git -C <input_path> status --short --branch",
        ))
        commands.append(shell_command(
            join_command([sys.executable, script, "plan", "--repo", str(input_path.resolve()), "--json"]),
            purpose="Re-run the planner after Git initialization.",
            template="python git_dirty.py plan --repo <input_path> --json",
            command_kind="python",
        ))
    elif action_id == "update_gitignore_before_initial_commit":
        ignore_rules = item.get("ignore_rules") or suggest_gitignore_rules(item.get("paths"))
        questionable_rules = item.get("questionable_ignore_rules") or []
        rendered_rules = "\n".join(ignore_rules[:80]) if ignore_rules else "<no safe generated/cache rules inferred>"
        rendered_questionable = "\n".join(questionable_rules[:80]) if questionable_rules else "<none>"
        commands.append(git_command(
            repo,
            ["rev-parse", "--show-toplevel", "--git-dir", "--git-common-dir", "--is-inside-work-tree"],
            purpose="Confirm the selected repository root and Git metadata before editing ignore rules.",
            template="git -C <repo> rev-parse --show-toplevel --git-dir --git-common-dir --is-inside-work-tree",
        ))
        commands.append(git_command(
            repo,
            ["status", "--short", "--branch"],
            purpose="Review the unborn-HEAD worktree before deciding what should be ignored.",
            template="git -C <repo> status --short --branch",
        ))
        commands.append(git_command(
            repo,
            ["ls-files", "--others", "--exclude-standard"],
            purpose="List current untracked first-commit candidates after existing ignore rules are applied.",
            template="git -C <repo> ls-files --others --exclude-standard",
        ))
        commands.append(git_command(
            repo,
            ["check-ignore", "-v", "--", *paths] if paths else ["check-ignore", "-v", "--", "<generated/runtime paths>"],
            purpose="Confirm whether generated/runtime paths are already covered by ignore rules.",
            template="git -C <repo> check-ignore -v -- <generated/runtime paths>",
            note=path_note or "A non-zero exit is expected while one or more generated/runtime paths are not ignored yet.",
        ))
        commands.append(command_record(
            purpose="Suggested conservative .gitignore entries to review before the first commit.",
            template="<edit .gitignore with reviewed generated/runtime rules>",
            command="# Suggested safe .gitignore entries:\n" + rendered_rules + "\n\n# Questionable/review-only .gitignore candidates:\n" + rendered_questionable,
            safe=True,
            command_kind="comment",
            implemented=True,
            note="Review these rules before adding them to .gitignore; repeated generated directory families may be collapsed to wildcard directory rules, while local/secret-looking and timestamp-family guesses are kept in the questionable pile.",
        ))
        commands.append(git_command(
            repo,
            ["diff", "--", ".gitignore"],
            purpose="After editing .gitignore, review the exact ignore-file diff before re-planning.",
            template="git -C <repo> diff -- .gitignore",
        ))
        commands.append(git_command(
            repo,
            ["status", "--short", "--ignored", "--", *paths] if paths else ["status", "--short", "--ignored"],
            purpose="After editing .gitignore, verify that noise paths are ignored and no real source/config was hidden.",
            template="git -C <repo> status --short --ignored -- <generated/runtime paths>",
            note=path_note,
        ))
        commands.append(shell_command(
            join_command([sys.executable, script, "plan", "--repo", repo, "--json"]),
            purpose="Re-run the planner after .gitignore changes so the first commit uses a clean candidate set.",
            template="python git_dirty.py plan --repo <repo> --json",
            command_kind="python",
        ))
    elif action_id == "secrets_filter":
        commands.append(command_record(
            purpose="Review merged security/privacy rule state before commit.",
            template="# Secrets / Filter card uses backend-owned merged rule state from git_dirty.py.",
            command="# Secrets / Filter card: review built-in rules and detect-secrets availability/status before commit.",
            safe=True,
            command_kind="comment",
            implemented=True,
            note="Part 1 does not save policy or run content scans from this command list. The backend scan should run only after the user reviews rule choices.",
        ))
        commands.append(shell_command(
            join_command([sys.executable, script, "plan", "--repo", repo, "--json"]),
            purpose="Refresh the planner output with the current .git_dirty_rules.json policy merge and detect-secrets availability.",
            template="python git_dirty.py plan --repo <repo> --json",
            command_kind="python",
        ))
    elif action_id in {"create_initial_snapshot", "prepare_commit_snapshot"}:
        commands.append(command_record(
            purpose="Show the disk-backed backend runner that should execute the validated first-commit payload.",
            template="# python tools/git/git_tool_fix_project_head.py <validated-payload.json>",
            command="# Backend safety runner:\n# python tools/git/git_tool_fix_project_head.py <validated-payload.json>",
            safe=True,
            command_kind="comment",
            implemented=True,
        ))
        commands.append(git_command(
            repo,
            ["rev-parse", "--show-toplevel", "--git-dir", "--git-common-dir", "--is-inside-work-tree"],
            purpose="Confirm the selected repository root and Git metadata before creating HEAD.",
            template="git -C <repo> rev-parse --show-toplevel --git-dir --git-common-dir --is-inside-work-tree",
        ))
        commands.append(git_command(
            repo,
            ["status", "--short", "--branch"],
            purpose="Review the unborn-HEAD worktree before creating the first commit.",
            template="git -C <repo> status --short --branch",
        ))
        commands.append(git_command(
            repo,
            ["ls-files", "--others", "--exclude-standard"],
            purpose="List untracked paths that are still first-commit candidates after .gitignore cleanup.",
            template="git -C <repo> ls-files --others --exclude-standard",
        ))
        identity = status.get("git_identity") or commit_identity_empty()
        commands.extend([
            git_command(
                repo,
                ["config", "--get", "user.name"],
                purpose="Check the effective Git author name that commit will use.",
                template="git -C <repo> config --get user.name",
                note="This is Git's effective value after local/global precedence is applied.",
            ),
            git_command(
                repo,
                ["config", "--get", "user.email"],
                purpose="Check the effective Git author email that commit will use.",
                template="git -C <repo> config --get user.email",
                note="This is Git's effective value after local/global precedence is applied.",
            ),
            git_command(
                repo,
                ["config", "--local", "--get", "user.name"],
                purpose="Check the repository-local Git author name.",
                template="git -C <repo> config --local --get user.name",
                implemented=False,
                note="Repository-local identity is preferred for this wizard.",
            ),
            git_command(
                repo,
                ["config", "--local", "--get", "user.email"],
                purpose="Check the repository-local Git author email.",
                template="git -C <repo> config --local --get user.email",
                implemented=False,
                note="Repository-local identity is preferred for this wizard.",
            ),
            git_command(
                repo,
                ["config", "--global", "--get", "user.name"],
                purpose="Check the global Git author name fallback.",
                template="git -C <repo> config --global --get user.name",
                implemented=False,
                note="Shown for context; the wizard should not silently change global config.",
            ),
            git_command(
                repo,
                ["config", "--global", "--get", "user.email"],
                purpose="Check the global Git author email fallback.",
                template="git -C <repo> config --global --get user.email",
                implemented=False,
                note="Shown for context; the wizard should not silently change global config.",
            ),
            git_command(
                repo,
                ["config", "--show-origin", "--get", "user.name"],
                purpose="Show where Git user.name is coming from.",
                template="git -C <repo> config --show-origin --get user.name",
                implemented=False,
            ),
            git_command(
                repo,
                ["config", "--show-origin", "--get", "user.email"],
                purpose="Show where Git user.email is coming from.",
                template="git -C <repo> config --show-origin --get user.email",
                implemented=False,
            ),
        ])
        if not identity.get("ready"):
            commands.append(command_record(
                purpose="Set missing commit identity for this repository only before committing.",
                template='git -C <repo> config --local user.name "Your Name"\ngit -C <repo> config --local user.email "you@example.com"',
                command="\n".join(identity.get("setup_commands") or [
                    'git config --local user.name "Your Name"',
                    'git config --local user.email "you@example.com"',
                ]),
                safe=True,
                command_kind="git",
                implemented=False,
                note="Replace placeholders with the user-approved commit identity. The wizard should keep this repo-local by default.",
            ))
        if paths:
            commands.append(git_command(
                repo,
                ["add", "--", *paths],
                purpose="Stage reviewed source/config paths for the initial snapshot.",
                template="git -C <repo> add -- <reviewed source/config paths>",
                implemented=not truncated,
                note=path_note or "Generated/runtime noise should be ignored before this command is run.",
            ))
        else:
            commands.append(git_command(
                repo,
                ["add", "--", "<reviewed source/config paths>"],
                purpose="Stage reviewed source/config paths for the initial snapshot.",
                template="git -C <repo> add -- <reviewed source/config paths>",
                implemented=False,
                note="No concrete source/config paths were selected yet; choose reviewed paths before running.",
            ))
        commands.append(git_command(
            repo,
            ["diff", "--cached", "--stat"],
            purpose="Review the staged initial snapshot summary.",
            template="git -C <repo> diff --cached --stat",
        ))
        commands.append(git_command(
            repo,
            ["diff", "--cached", "--name-status"],
            purpose="Review the exact staged file list before creating HEAD.",
            template="git -C <repo> diff --cached --name-status",
        ))
        commands.append(git_command(
            repo,
            ["commit", "-m", "Initial snapshot"],
            purpose="Create the first commit so HEAD exists.",
            template='git -C <repo> commit -m "Initial snapshot"',
            safe=True,
            destructive=False,
            note="Run only after reviewing the staged initial snapshot.",
        ))
        commands.append(shell_command(
            join_command([sys.executable, script, "plan", "--repo", repo, "--json"]),
            purpose="Re-run the planner after HEAD exists.",
            template="python git_dirty.py plan --repo <repo> --json",
            command_kind="python",
        ))
    elif action_id == "make_cleanup_plan":
        commands.append(shell_command(
            join_command([sys.executable, script, "plan", "--repo", str(input_path.resolve()), "--json"]),
            purpose="Collect a fresh plan after the previous step changes repository reality.",
            template="python git_dirty.py plan --repo <input_path> --json",
            command_kind="python",
        ))
    elif action_id == "stop_until_repository_is_clear":
        commands.append(command_record(
            purpose="No command is safe until the repository root ambiguity is resolved.",
            template="<no command>",
            command="<no command>",
            safe=True,
            command_kind="none",
            implemented=True,
        ))
    elif action_id == "save_current_state":
        commands.append(shell_command(
            join_command([sys.executable, script, "snapshot", "--repo", repo, "--json"]),
            purpose="Create a reversible dirty snapshot owned by git_dirty.py.",
            template="python git_dirty.py snapshot --repo <repo> --json",
            command_kind="python",
            note="The snapshot command saves porcelain status, staged/unstaged binary patches, the untracked list, and an archive of untracked file contents.",
        ))
    elif action_id == "classify_changed_files":
        commands.extend([
            git_command(repo, ["status", "--porcelain=v2", "-z", "--branch"], purpose="Collect stable dirty status for classification.", template="git -C <repo> status --porcelain=v2 -z --branch"),
            git_command(repo, ["diff", "--numstat"], purpose="Measure unstaged tracked line changes.", template="git -C <repo> diff --numstat"),
            git_command(repo, ["diff", "--cached", "--numstat"], purpose="Measure staged tracked line changes.", template="git -C <repo> diff --cached --numstat"),
            git_command(repo, ["ls-files", "--others", "--exclude-standard", "-z"], purpose="List untracked files for source/generated/local-only classification.", template="git -C <repo> ls-files --others --exclude-standard -z"),
        ])
    elif action_id in {"start_tracking_real_work", "track_selected_files", "track_all_safe_source_files"}:
        args, truncated_paths = add_paths_args(item.get("paths"))
        commands.append(git_command(
            repo,
            ["add", *args] if args else ["add", "--", "<selected-paths>"],
            purpose="Stage reviewed source/config files as real work.",
            template="git -C <repo> add -- <paths>",
            note=path_note or ("No concrete paths were selected yet." if not args else ""),
        ))
        if truncated_paths:
            commands[-1]["path_count"] = len(item.get("paths") or [])
    elif action_id == "record_current_work_as_commit":
        commands.append(git_command(
            repo,
            ["diff", "--cached", "--stat"],
            purpose="Review staged changes before recording work.",
            template="git -C <repo> diff --cached --stat",
        ))
        commands.append(git_command(
            repo,
            ["commit", "-m", "Save dirty work before cleanup"],
            purpose="Commit reviewed staged work before cleanup.",
            template='git -C <repo> commit -m "Save dirty work before cleanup"',
            safe=True,
            destructive=False,
            note="Only run after reviewing staged changes.",
        ))
    elif action_id == "keep_changes_unstaged":
        commands.append(git_command(
            repo,
            ["diff", "--", *paths] if paths else ["diff"],
            purpose="Review unstaged tracked changes without modifying them.",
            template="git -C <repo> diff -- <paths>",
            note=path_note,
        ))
    elif action_id in {"ignore_generated_files", "ignore_local_environment_files", "ignore_selected_paths", "ignore_debug_output"}:
        commands.append(git_command(
            repo,
            ["status", "--short", "--", *paths] if paths else ["status", "--short"],
            purpose="Verify the files that should be ignored or preserved locally.",
            template="git -C <repo> status --short -- <paths>",
            note="Edit .gitignore or .git/info/exclude after review; this step intentionally avoids auto-editing ignore files.",
        ))
        commands.append(git_command(
            repo,
            ["check-ignore", "-v", "--", *paths] if paths else ["check-ignore", "-v", "--", "<paths>"],
            purpose="Confirm whether ignore rules already cover these paths.",
            template="git -C <repo> check-ignore -v -- <paths>",
            note=path_note,
        ))
    elif action_id == "remove_untracked_generated_files":
        commands.append(git_command(
            repo,
            ["clean", "-dn", "--", *paths] if paths else ["clean", "-dn"],
            purpose="Preview generated untracked files that would be removed.",
            template="git -C <repo> clean -dn -- <paths>",
            safe=True,
            destructive=False,
            note=path_note,
        ))
        commands.append(git_command(
            repo,
            ["clean", "-f", "--", *paths] if paths else ["clean", "-f"],
            purpose="Remove only reviewed generated untracked files after the snapshot step.",
            template="git -C <repo> clean -f -- <paths>",
            safe=False,
            destructive=True,
            locked=True,
            requires=["save_current_state"],
            note="Locked by default. Run only after Save current state succeeds.",
        ))
    elif action_id == "find_nested_repositories":
        commands.append(git_command(
            repo,
            ["status", "--porcelain=v2", "--ignored"],
            purpose="Inspect parent status while nested repositories are handled separately.",
            template="git -C <repo> status --porcelain=v2 --ignored",
        ))
    elif action_id == "compare_to_remote_state":
        commands.append(git_command(repo, ["status", "-sb"], purpose="Show branch ahead/behind summary.", template="git -C <repo> status -sb"))
        commands.append(git_command(repo, ["fetch", "--all", "--dry-run"], purpose="Preview remote updates without changing local refs.", template="git -C <repo> fetch --all --dry-run"))
    elif action_id == "inspect_configured_remotes":
        commands.append(git_command(repo, ["remote", "-v"], purpose="List configured remotes before push or mirror work.", template="git -C <repo> remote -v"))
    elif action_id == "show_merge_conflicts":
        commands.append(git_command(repo, ["diff", "--name-only", "--diff-filter=U"], purpose="List conflicted files.", template="git -C <repo> diff --name-only --diff-filter=U"))
    elif action_id == "discard_selected_file_changes":
        commands.append(git_command(repo, ["restore", "--worktree", "--", *paths] if paths else ["restore", "--worktree", "--", "<paths>"], purpose="Discard selected tracked-file changes after snapshot.", template="git -C <repo> restore --worktree -- <paths>", safe=False, destructive=True, locked=True, requires=["save_current_state"], note=path_note))
    elif action_id == "unstage_selected_changes":
        commands.append(git_command(repo, ["restore", "--staged", "--", *paths] if paths else ["restore", "--staged", "--", "<paths>"], purpose="Move selected staged changes back to unstaged.", template="git -C <repo> restore --staged -- <paths>", safe=True, destructive=False, note=path_note))
    elif action_id == "unstage_everything":
        commands.append(git_command(repo, ["restore", "--staged", "--", "."], purpose="Move all staged changes back to unstaged.", template="git -C <repo> restore --staged -- .", safe=True, destructive=False))
    elif action_id == "preserve_local_only_files":
        commands.append(git_command(repo, ["status", "--short", "--", *paths] if paths else ["status", "--short"], purpose="Review local-only files that should not be staged.", template="git -C <repo> status --short -- <paths>", note=path_note))
    else:
        commands.append(git_command(
            repo if detection.get("ok") else str(input_path.resolve()),
            ["status", "--short"],
            purpose="Gather current Git status before deciding this step.",
            template="git -C <repo> status --short",
            note="Generic evidence command for this plan step.",
        ))
    return commands

def step(order: int, action_id: str, title: str, why: str, *, paths: list[str] | None = None, requires: list[str] | None = None, locked: bool = False) -> dict[str, Any]:
    action = action_by_id(action_id)
    data = {
        "order": order,
        "id": action_id,
        "title": title,
        "label": action["label"],
        "git_name": action["git_name"],
        "kind": action["kind"],
        "why": why,
        "safe": action.get("safe", True),
        "destructive": action.get("destructive", False),
        "reversible": action.get("reversible", True),
        "requires": requires if requires is not None else action.get("requires", []),
        "locked": locked,
        "state": action.get("state", "planned"),
        "requires_user": bool(action.get("requires_user", True)),
        "blocks_progress": bool(action.get("blocks_progress", False)),
    }
    if paths is not None:
        data["paths"] = paths
    if data["destructive"] and "save_current_state" not in data["requires"]:
        data["requires"] = [*data["requires"], "save_current_state"]
        data["locked"] = True
    return data


def make_plan(input_path: Path, *, include_actions: bool = False) -> dict[str, Any]:
    status = collect_status(input_path)
    detection = status["git_detection"]
    plan_id = "dirty-plan-" + time.strftime("%Y%m%d-%H%M%S")
    steps: list[dict[str, Any]] = []
    strategy = "snapshot_then_classify"

    repo_state = str(detection.get("repo_state") or detection.get("repository_state") or "unknown")

    if not detection.get("ok"):
        if detection.get("safe_to_init") and repo_state in {"not_initialized", "inside_parent_repo_only"}:
            strategy = "initialize_repository_here_then_recheck"
            title = "Initialize repository here"
            why = "The selected folder has no usable local .git metadata, so the deterministic next repair is to initialize this folder and re-run planning."
            if repo_state == "inside_parent_repo_only":
                why = "Git discovery found a parent repository, but the selected folder has no local .git metadata; initialize here only after confirming this folder should be its own repo."
            steps.append(step(0, "initialize_repository_here", title, why))
            steps.append(step(1, "make_cleanup_plan", "Re-run dirty planning", "After initialization, collect fresh dirty-state facts."))
        else:
            strategy = "do_nothing_until_repo_is_clear"
            steps.append(step(0, "find_repository_root", "Establish repository reality", "Git could not prove this path is a safe worktree."))
            steps.append(step(1, "stop_until_repository_is_clear", "Stop until repository is clear", "The selected path is not safe to initialize automatically."))
    elif repo_state == "initialized_no_head":
        files = status["files"]
        source_untracked = [
            f["path"] for f in files
            if f.get("untracked")
            and ("source" in f.get("classifications", []) or "config" in f.get("classifications", []))
            and "generated" not in f.get("classifications", [])
            and "secret-looking" not in f.get("classifications", [])
        ]
        gitignore_file = read_gitignore_file(Path(detection.get("worktree_root") or input_path))
        first_commit_candidate_groups = {
            "source_config_test": source_untracked,
        }
        safe_ignore_candidates: list[str] = []
        questionable_ignore_candidates: list[str] = []
        all_ignore_candidates: list[str] = []
        for f in files:
            if not f.get("untracked"):
                continue
            labels = set(f.get("classifications", []))
            if not ({"generated", "local-environment", "secret-looking"} & labels):
                continue
            ignore_path = ignore_review_path(f["path"], labels)
            if not ignore_path:
                continue
            all_ignore_candidates.append(ignore_path)
            if ignore_candidate_requires_review(ignore_path, labels):
                questionable_ignore_candidates.append(ignore_path)
            elif "generated" in labels:
                safe_ignore_candidates.append(ignore_path)

        if all_ignore_candidates:
            strategy = "prepare_gitignore_then_create_initial_snapshot"
            ignore_step = step(
                0,
                "update_gitignore_before_initial_commit",
                "Clean up .gitignore before first commit",
                "Repository metadata exists but HEAD is unborn, and generated/runtime noise is still untracked. Clean up ignore rules first so the first commit contains only reviewed source/config work.",
                paths=all_ignore_candidates,
            )
            safe_rules = suggest_gitignore_rules(safe_ignore_candidates)
            questionable_rules = suggest_questionable_gitignore_rules(
                questionable_ignore_candidates,
                family_source_paths=all_ignore_candidates,
            )
            ignore_step["safe_paths"] = safe_ignore_candidates
            ignore_step["questionable_paths"] = questionable_ignore_candidates
            ignore_step["ignore_rules"] = safe_rules
            ignore_step["questionable_ignore_rules"] = questionable_rules
            ignore_step["ignore_rule_groups"] = {
                "safe": safe_rules,
                "questionable": questionable_rules,
            }
            ignore_step["affected_paths"] = all_ignore_candidates
            ignore_step["source_config_test_candidates"] = source_untracked
            ignore_step["first_commit_candidate_groups"] = first_commit_candidate_groups
            ignore_step["gitignore_file"] = gitignore_file
            steps.append(ignore_step)
            filter_step = step(
                1,
                "secrets_filter",
                "Secrets / Filter",
                "Review security/privacy rule switches before commit. The backend owns policy merge, availability, and the later filter scan.",
                paths=source_untracked or None,
                requires=["update_gitignore_before_initial_commit"],
                locked=True,
            )
            filter_step["source_config_test_candidates"] = source_untracked
            filter_step["first_commit_candidate_groups"] = first_commit_candidate_groups
            filter_step["gitignore_file"] = gitignore_file
            filter_step["secrets_filter"] = secrets_filter_payload(
                Path(detection.get("worktree_root") or input_path),
                files,
            )
            steps.append(filter_step)
            commit_step = step(
                2,
                "prepare_commit_snapshot",
                "Take Snapshot / Commit",
                "Prepare the reviewed local commit after .gitignore cleanup and the Secrets / Filter review are complete. If HEAD is still unborn, creating HEAD is part of this commit wizard.",
                paths=source_untracked or None,
                requires=["update_gitignore_before_initial_commit", "secrets_filter"],
                locked=True,
            )
            commit_step["source_config_test_candidates"] = source_untracked
            commit_step["first_commit_candidate_groups"] = first_commit_candidate_groups
            commit_step["gitignore_file"] = gitignore_file
            commit_step["secrets_filter"] = filter_step["secrets_filter"]
            commit_step["commit_review"] = commit_review_payload(
                Path(detection.get("worktree_root") or input_path),
                detection,
                files,
                default_paths=source_untracked,
                mode="first_commit",
                locked_reason="Review .gitignore and Secrets / Filter before staging commit candidates.",
                commit_identity=status.get("git_identity"),
            )
            steps.append(commit_step)
        else:
            strategy = "create_initial_snapshot_then_recheck"
            ignore_step = step(
                0,
                "update_gitignore_before_initial_commit",
                "Clean up .gitignore before first commit",
                "Generated/runtime ignore cleanup is already satisfied for this first-commit candidate set. No .gitignore changes are needed before the Secrets / Filter review.",
                paths=[],
            )
            ignore_step.update({
                "state": "completed",
                "completed": True,
                "requires_user": False,
                "blocks_progress": False,
                "safe_paths": [],
                "questionable_paths": [],
                "ignore_rules": [],
                "questionable_ignore_rules": [],
                "ignore_rule_groups": {"safe": [], "questionable": []},
                "affected_paths": [],
                "source_config_test_candidates": source_untracked,
                "first_commit_candidate_groups": first_commit_candidate_groups,
                "gitignore_file": gitignore_file,
                "gitignore_success": {
                    "status": "passed",
                    "label": ".gitignore cleanup already satisfied",
                    "message": "No generated/runtime ignore cleanup is currently blocking the first commit.",
                    "affected_path_count": 0,
                    "suggested_rule_count": 0,
                },
            })
            steps.append(ignore_step)
            filter_step = step(
                1,
                "secrets_filter",
                "Secrets / Filter",
                "Review security/privacy rule switches before the first commit. The backend owns policy merge, availability, and the later filter scan.",
                paths=source_untracked or None,
                requires=["update_gitignore_before_initial_commit"],
            )
            filter_step["source_config_test_candidates"] = source_untracked
            filter_step["first_commit_candidate_groups"] = first_commit_candidate_groups
            filter_step["gitignore_file"] = gitignore_file
            filter_step["secrets_filter"] = secrets_filter_payload(
                Path(detection.get("worktree_root") or input_path),
                files,
            )
            steps.append(filter_step)
            initial_step = step(
                2,
                "create_initial_snapshot",
                "Create first Git commit",
                "Repository metadata exists, HEAD is unborn, generated/runtime ignore cleanup is satisfied, and the Secrets / Filter review should be completed before staging.",
                paths=source_untracked or None,
                requires=["update_gitignore_before_initial_commit", "secrets_filter"],
                locked=True,
            )
            initial_step["source_config_test_candidates"] = source_untracked
            initial_step["first_commit_candidate_groups"] = first_commit_candidate_groups
            initial_step["gitignore_file"] = gitignore_file
            initial_step["secrets_filter"] = filter_step["secrets_filter"]
            initial_step["commit_review"] = commit_review_payload(
                Path(detection.get("worktree_root") or input_path),
                detection,
                files,
                default_paths=source_untracked,
                mode="first_commit",
                locked_reason="Review Secrets / Filter before staging commit candidates.",
                commit_identity=status.get("git_identity"),
            )
            steps.append(initial_step)
            steps.append(step(3, "make_cleanup_plan", "Re-run dirty planning", "After the first commit creates HEAD, collect fresh dirty-state facts."))
    else:
        order = 0
        steps.append(step(order, "find_repository_root", "Establish repository reality", "Git identified the actual worktree root, git-dir, and common-dir."))
        order += 1
        if detection.get("input_inside_parent_repo"):
            strategy = "manual_review_required"
            steps.append(step(order, "choose_correct_repository_root", "Confirm the intended repository root", "The selected path is inside a parent Git repository; choose whether to use the parent or create a nested repo."))
            order += 1
        if status["summary"]["conflicted"]:
            strategy = "manual_review_required"
            steps.append(step(order, "show_merge_conflicts", "Show merge conflicts", "Conflicts block normal cleanup decisions. Resolve, abort, or inspect manually first."))
            order += 1
        if status["dirty_score"] > 0:
            steps.append(step(order, "save_current_state", "Save current state", "Create a reversible snapshot before cleanup, especially before any destructive step."))
            order += 1
            steps.append(step(order, "classify_changed_files", "Classify changed files", "Separate source/config work from generated artifacts, local-only files, and risks."))
            order += 1

        files = status["files"]
        source_untracked = [f["path"] for f in files if f.get("untracked") and "source" in f.get("classifications", []) and "generated" not in f.get("classifications", []) and "secret-looking" not in f.get("classifications", [])]
        generated = [f["path"] for f in files if "generated" in f.get("classifications", []) and f.get("untracked")]
        local_env = [f["path"] for f in files if "local-environment" in f.get("classifications", [])]
        staged = [f["path"] for f in files if f.get("staged")]
        unstaged = [f["path"] for f in files if f.get("unstaged") and not f.get("untracked")]

        repo_root_for_review = Path(detection.get("worktree_root") or input_path)

        if local_env:
            local_env_step = step(
                order,
                "ignore_local_environment_files",
                "Protect local environment files",
                "Secret-looking or local environment files should be preserved locally and ignored, not staged.",
                paths=local_env,
            )
            attach_gitignore_review_payload(
                local_env_step,
                repo_root_for_review,
                safe_paths=[],
                questionable_paths=local_env,
            )
            steps.append(local_env_step)
            order += 1

        if source_untracked:
            source_untracked_set = set(source_untracked)
            source_untracked_files = [
                f for f in files
                if f.get("path") in source_untracked_set
            ]

            track_step = step(
                order,
                "start_tracking_real_work",
                "Start tracking real work",
                "Untracked source/config files look intentional and should be reviewed for tracking.",
                paths=source_untracked,
            )
            track_step["commit_review"] = commit_review_payload(
                repo_root_for_review,
                detection,
                source_untracked_files,
                default_paths=[],
                mode="track_untracked",
                locked_reason="",
                commit_identity=status.get("git_identity"),
            )
            steps.append(track_step)
            order += 1

        if staged:
            staged_set = set(staged)
            staged_files = [
                f for f in files
                if f.get("path") in staged_set
            ]

            commit_step = step(
                order,
                "record_current_work_as_commit",
                "Consider recording staged work",
                "Staged files may represent intentional work already selected for commit.",
                paths=staged,
            )
            commit_step["commit_review"] = commit_review_payload(
                repo_root_for_review,
                detection,
                staged_files,
                default_paths=staged,
                mode="normal_commit",
                locked_reason="",
                commit_identity=status.get("git_identity"),
            )
            steps.append(commit_step)
            order += 1
        if unstaged:
            steps.append(step(order, "keep_changes_unstaged", "Keep or review unstaged tracked changes", "Tracked edits should be reviewed before revert or staging.", paths=unstaged))
            order += 1
        if generated:
            strategy = "preserve_then_clean_generated_noise" if strategy == "snapshot_then_classify" else strategy
            generated_step = step(
                order,
                "ignore_generated_files",
                "Ignore generated files",
                "Generated/debug files are likely noise and should usually be ignored.",
                paths=generated,
            )
            attach_gitignore_review_payload(
                generated_step,
                repo_root_for_review,
                safe_paths=generated,
                questionable_paths=[],
            )
            steps.append(generated_step)
            order += 1
            steps.append(step(order, "remove_untracked_generated_files", "Remove generated untracked files", "After saving current state, generated untracked files can be cleaned.", paths=generated, requires=["save_current_state"], locked=True))
            order += 1
        if status["nested_repos"]:
            steps.append(step(order, "find_nested_repositories", "Handle nested repositories separately", "Nested repos/worktrees should not be cleaned as ordinary parent files."))
            order += 1
        if status["branch"].get("upstream"):
            steps.append(step(order, "compare_to_remote_state", "Compare to remote state", "Ahead/behind information affects whether cleanup should commit, stash, or push."))
            order += 1
        steps.append(step(order, "inspect_configured_remotes", "Inspect configured remotes", "Confirm whether origin is GitHub/GitLab/local Gitea before changing remotes."))

    repo_root_for_policy = Path(detection.get("worktree_root") or input_path)
    snapshot_listing = list_snapshots(input_path) if detection.get("ok") else {"ok": False, "snapshots": [], "latest": None}
    protection = {
        "state_protected": bool(snapshot_listing.get("latest")),
        "latest_snapshot": snapshot_listing.get("latest"),
        "snapshot_count": snapshot_listing.get("count", 0),
        "note": "Create a fresh snapshot before destructive cleanup. Existing snapshots are shown as evidence, not automatic permission.",
    }

    next_step = next((item for item in steps if item.get("state") != "completed"), steps[0] if steps else None)
    next_action = {
        "id": next_step.get("id", ""),
        "label": next_step.get("label", ""),
        "kind": next_step.get("kind", ""),
        "state": next_step.get("state", "planned"),
        "requires_user": bool(next_step.get("requires_user", True)),
        "blocks_progress": bool(next_step.get("blocks_progress", False)),
    } if next_step else None

    plan = {
        "ok": True,
        "plan_id": plan_id,
        "next_action": next_action,
        "repo": {
            "input_path": str(input_path.resolve()),
            "git_detection": detection,
            "repo_state": detection.get("repo_state", "unknown"),
            "repository_state": detection.get("repository_state", detection.get("repo_state", "unknown")),
            "has_head": bool(detection.get("has_head")),
            "head_state": detection.get("head_state", "unknown"),
            "next_action": next_action,
            "git_identity": status.get("git_identity", commit_identity_empty()),
            "commit_identity": status.get("commit_identity", commit_identity_empty()),
        },
        "commit_identity": status.get("commit_identity", commit_identity_empty()),
        "security_rule_catalog": security_rule_catalog_output(repo_root_for_policy),
        "dirty_score": status["dirty_score"],
        "level": status["level"],
        "summary": status["summary"],
        "protection": protection,
        "recommended_strategy": strategy,
        "available_strategies": STRATEGIES,
        "dirty_things": status["dirty_things"],
        "risks": status["risks"],
        "steps": steps,
        "status": status,
    }
    for plan_step in steps:
        plan_step["commands"] = commands_for_step(plan_step, status, input_path, plan_id)
    if include_actions:
        plan["actions"] = ACTION_CATALOG
    return plan


def print_text_status(payload: dict[str, Any]) -> None:
    det = payload["git_detection"]
    print(f"Repo input: {payload['repo']}")
    print(f"Git worktree: {'yes' if det.get('ok') else 'no'}")
    print(f"Repository state: {det.get('repo_state') or det.get('repository_state') or 'unknown'}")
    print(f"HEAD state: {det.get('head_state') or 'unknown'}")
    if det.get("ok"):
        print(f"Root: {det.get('worktree_root')}")
        if det.get("input_inside_parent_repo"):
            print("Warning: input path is inside a parent repository.")
    else:
        print(f"Reason: {det.get('error')}")
        if det.get("parent_worktree_root"):
            print(f"Parent Git root discovered: {det.get('parent_worktree_root')}")
    print(f"Dirty score: {payload['dirty_score']} / 100 ({payload['level']})")
    print("Summary:", json.dumps(payload["summary"], sort_keys=True))


def command_as_shell_line(command: dict[str, Any]) -> str:
    rendered = str(command.get("command") or "").strip()
    if not rendered:
        rendered = "# empty command"
    if command.get("locked"):
        reason = ", ".join(command.get("requires") or []) or "required safety step"
        return f"# LOCKED until {reason}:\n# {rendered}"
    if not command.get("implemented", True):
        return f"# NOT IMPLEMENTED YET:\n# {rendered}"
    return rendered


def print_shell_fence(commands: list[dict[str, Any]], *, indent: str = "    ") -> None:
    print(f"{indent}```shell")
    for command in commands:
        for line in command_as_shell_line(command).splitlines():
            print(f"{indent}{line}")
    print(f"{indent}```")


def print_text_plan(plan: dict[str, Any]) -> None:
    print(f"Plan: {plan['plan_id']}")
    print(f"Strategy: {plan['recommended_strategy']}")
    print(f"Dirty score: {plan['dirty_score']} / 100 ({plan['level']})")
    summary = plan.get("summary", {})
    print("Summary:", json.dumps(summary, sort_keys=True))
    print()
    for item in plan["steps"]:
        lock = " [locked]" if item.get("locked") else ""
        git_name = item.get("git_name", "")
        print(f"{item['order']:>2}. {item['label']} ({git_name}){lock}")
        print(f"    {item['why']}")
        paths = item.get("paths") or []
        if paths:
            shown = paths[:8]
            print(f"    Paths ({len(paths)}):")
            for path in shown:
                print(f"      - {path}")
            if len(paths) > len(shown):
                print(f"      ... {len(paths) - len(shown)} more")
        commands = item.get("commands") or []
        if commands:
            print("    Commands:")
            print_shell_fence(commands, indent="    ")
            print("    Command notes:")
            for index, command in enumerate(commands, start=1):
                command_lock = " [locked]" if command.get("locked") else ""
                command_impl = "" if command.get("implemented", True) else " [not implemented]"
                print(f"      {index}. {command.get('purpose') or 'Run command.'}{command_lock}{command_impl}")
                if command.get("template") and command.get("template") != command.get("command"):
                    print(f"         template: {command.get('template')}")
                if command.get("requires"):
                    print(f"         requires: {', '.join(command['requires'])}")
                if command.get("note"):
                    print(f"         note: {command['note']}")
        print()




def snapshot_base_dir_from_detection(detection: dict[str, Any], fallback: Path) -> Path:
    base = detection.get("git_common_dir") or detection.get("git_dir")
    if base:
        return Path(base).resolve() / "git-dirty" / "snapshots"
    return fallback.resolve() / ".git" / "git-dirty" / "snapshots"


def safe_snapshot_id(value: str | None = None) -> str:
    raw = value or ("dirty-snapshot-" + time.strftime("%Y%m%d-%H%M%S"))
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip(".-")
    if not cleaned:
        cleaned = "dirty-snapshot-" + time.strftime("%Y%m%d-%H%M%S")
    if cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
        raise ValueError("Snapshot id must be a safe single path segment.")
    return cleaned[:120]


def ensure_under_directory(path: Path, base: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(base.resolve())
    return resolved


def write_text_file(path: Path, content: str) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", errors="replace")
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": file_sha256(path)}


def write_json_file(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return write_text_file(path, json.dumps(payload, indent=2, sort_keys=True))


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_untracked_paths(root: Path, zlist_text: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for raw in zlist_text.split("\0"):
        rel = raw.strip()
        if not rel:
            continue
        rel_path = Path(rel)
        if rel_path.is_absolute() or any(part == ".." for part in rel_path.parts):
            files.append({"path": rel, "ok": False, "reason": "unsafe-path"})
            continue
        full = (root / rel_path).resolve()
        try:
            full.relative_to(root.resolve())
        except ValueError:
            files.append({"path": rel, "ok": False, "reason": "outside-root"})
            continue
        if not full.exists():
            files.append({"path": rel, "ok": False, "reason": "missing"})
            continue
        if full.is_dir():
            # git ls-files --others normally returns files. Keep this explicit.
            files.append({"path": rel, "ok": False, "reason": "directory-entry"})
            continue
        files.append({"path": rel, "ok": True, "full_path": str(full), "size": full.stat().st_size})
    return files


def archive_untracked_files(root: Path, snapshot_dir: Path, zlist_text: str) -> dict[str, Any]:
    archive_path = snapshot_dir / "untracked-files.tar.gz"
    entries = safe_untracked_paths(root, zlist_text)
    archived: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    with tarfile.open(archive_path, "w:gz") as tar:
        for entry in entries:
            if not entry.get("ok"):
                skipped.append(entry)
                continue
            rel = entry["path"]
            full = Path(str(entry["full_path"]))
            tar.add(full, arcname=rel, recursive=False)
            archived.append({"path": rel, "size": entry.get("size", 0)})
    return {
        "path": str(archive_path),
        "bytes": archive_path.stat().st_size,
        "sha256": file_sha256(archive_path),
        "archived": archived,
        "skipped": skipped,
        "count": len(archived),
    }


def create_snapshot(input_path: Path, *, snapshot_id: str | None = None) -> dict[str, Any]:
    detection = detect_repository(input_path)
    if not detection.get("ok"):
        return {
            "ok": False,
            "error": "Cannot create a dirty snapshot because Git could not prove this path is a worktree.",
            "git_detection": detection,
        }

    root = Path(str(detection["worktree_root"])).resolve()
    sid = safe_snapshot_id(snapshot_id)
    base = snapshot_base_dir_from_detection(detection, root)
    snapshot_dir = ensure_under_directory(base / sid, base)
    if snapshot_dir.exists() and any(snapshot_dir.iterdir()):
        return {
            "ok": False,
            "error": f"Snapshot already exists: {sid}",
            "snapshot_id": sid,
            "snapshot_dir": str(snapshot_dir),
        }
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    command_results: dict[str, dict[str, Any]] = {}
    written: dict[str, dict[str, Any]] = {}

    status_result = run_git(root, ["status", "--porcelain=v2", "-z", "--branch"])
    unstaged_result = run_git(root, ["diff", "--binary"])
    staged_result = run_git(root, ["diff", "--cached", "--binary"])
    untracked_result = run_git(root, ["ls-files", "--others", "--exclude-standard", "-z"])

    command_results["status"] = asdict_result(status_result)
    command_results["unstaged_diff"] = asdict_result(unstaged_result)
    command_results["staged_diff"] = asdict_result(staged_result)
    command_results["untracked"] = asdict_result(untracked_result)

    written["status_porscelain_v2_z"] = write_text_file(snapshot_dir / "status.porcelain-v2-z", status_result.stdout)
    written["unstaged_patch"] = write_text_file(snapshot_dir / "unstaged.patch", unstaged_result.stdout)
    written["staged_patch"] = write_text_file(snapshot_dir / "staged.patch", staged_result.stdout)
    written["untracked_zlist"] = write_text_file(snapshot_dir / "untracked.zlist", untracked_result.stdout)
    archive_info = archive_untracked_files(root, snapshot_dir, untracked_result.stdout)

    status_payload = collect_status(root)
    manifest = {
        "version": 1,
        "snapshot_id": sid,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repo": str(root),
        "git_detection": detection,
        "dirty_score": status_payload.get("dirty_score"),
        "level": status_payload.get("level"),
        "summary": status_payload.get("summary", {}),
        "files": written,
        "untracked_archive": archive_info,
        "commands": command_results,
        "restore": {
            "default_is_dry_run": True,
            "tracked_restore_commands": [
                "git apply --index staged.patch",
                "git apply unstaged.patch",
            ],
            "untracked_restore": "extract untracked-files.tar.gz without overwriting existing files unless explicitly requested",
        },
    }
    written["manifest"] = write_json_file(snapshot_dir / "manifest.json", manifest)

    return {
        "ok": True,
        "snapshot_id": sid,
        "snapshot_dir": str(snapshot_dir),
        "repo": str(root),
        "manifest": manifest,
        "written": written,
    }


def snapshot_manifest_path(input_path: Path, snapshot_id: str) -> tuple[dict[str, Any], Path, Path]:
    detection = detect_repository(input_path)
    if not detection.get("ok"):
        raise ValueError("Cannot locate snapshots because Git could not prove this path is a worktree.")
    root = Path(str(detection["worktree_root"])).resolve()
    base = snapshot_base_dir_from_detection(detection, root)
    sid = safe_snapshot_id(snapshot_id)
    snapshot_dir = ensure_under_directory(base / sid, base)
    return detection, snapshot_dir, snapshot_dir / "manifest.json"


def list_snapshots(input_path: Path) -> dict[str, Any]:
    detection = detect_repository(input_path)
    if not detection.get("ok"):
        return {
            "ok": False,
            "error": "Cannot list dirty snapshots because Git could not prove this path is a worktree.",
            "git_detection": detection,
            "snapshots": [],
        }
    root = Path(str(detection["worktree_root"])).resolve()
    base = snapshot_base_dir_from_detection(detection, root)
    snapshots: list[dict[str, Any]] = []
    if base.exists():
        for child in sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True):
            manifest_path = child / "manifest.json"
            entry: dict[str, Any] = {
                "snapshot_id": child.name,
                "snapshot_dir": str(child),
                "manifest_exists": manifest_path.exists(),
                "modified_at": child.stat().st_mtime,
            }
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    entry.update({
                        "created_at": manifest.get("created_at"),
                        "repo": manifest.get("repo"),
                        "dirty_score": manifest.get("dirty_score"),
                        "level": manifest.get("level"),
                        "summary": manifest.get("summary", {}),
                        "untracked_count": (manifest.get("untracked_archive") or {}).get("count", 0),
                    })
                except Exception as exc:
                    entry["manifest_error"] = str(exc)
            snapshots.append(entry)
    return {
        "ok": True,
        "repo": str(root),
        "git_detection": detection,
        "snapshot_base": str(base),
        "snapshots": snapshots,
        "count": len(snapshots),
        "latest": snapshots[0] if snapshots else None,
    }


def restore_snapshot(
    input_path: Path,
    *,
    snapshot_id: str,
    apply: bool = False,
    overwrite_untracked: bool = False,
) -> dict[str, Any]:
    detection, snapshot_dir, manifest_path = snapshot_manifest_path(input_path, snapshot_id)
    if not manifest_path.exists():
        return {
            "ok": False,
            "error": f"Snapshot manifest not found: {snapshot_id}",
            "snapshot_id": snapshot_id,
            "snapshot_dir": str(snapshot_dir),
        }
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = Path(str(detection["worktree_root"])).resolve()
    staged_patch = snapshot_dir / "staged.patch"
    unstaged_patch = snapshot_dir / "unstaged.patch"
    archive_path = snapshot_dir / "untracked-files.tar.gz"
    commands = [
        {
            "purpose": "Restore staged tracked changes from the snapshot.",
            "argv": ["git", "-C", str(root), "apply", "--index", str(staged_patch)],
            "command": join_command(["git", "-C", str(root), "apply", "--index", str(staged_patch)]),
            "locked": not apply,
        },
        {
            "purpose": "Restore unstaged tracked changes from the snapshot.",
            "argv": ["git", "-C", str(root), "apply", str(unstaged_patch)],
            "command": join_command(["git", "-C", str(root), "apply", str(unstaged_patch)]),
            "locked": not apply,
        },
        {
            "purpose": "Restore archived untracked files without overwriting existing files by default.",
            "argv": [],
            "command": f"extract {shell_quote(str(archive_path))} into {shell_quote(str(root))}",
            "locked": not apply,
        },
    ]
    if not apply:
        return {
            "ok": True,
            "applied": False,
            "dry_run": True,
            "snapshot_id": snapshot_id,
            "snapshot_dir": str(snapshot_dir),
            "repo": str(root),
            "manifest": manifest,
            "commands": commands,
            "note": "Restore is a dry run by default. Re-run with --apply to restore conservatively.",
        }

    results: list[dict[str, Any]] = []
    for patch_path, args in [
        (staged_patch, ["apply", "--index", str(staged_patch)]),
        (unstaged_patch, ["apply", str(unstaged_patch)]),
    ]:
        if patch_path.exists() and patch_path.stat().st_size > 0:
            result = run_git(root, args)
            results.append(asdict_result(result))
            if result.returncode != 0:
                return {
                    "ok": False,
                    "applied": False,
                    "error": result.stderr or result.stdout or f"Could not apply {patch_path.name}",
                    "snapshot_id": snapshot_id,
                    "repo": str(root),
                    "results": results,
                    "manifest": manifest,
                }

    restored: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    if archive_path.exists():
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                member_path = Path(member.name)
                if member_path.is_absolute() or any(part == ".." for part in member_path.parts):
                    skipped.append({"path": member.name, "reason": "unsafe-path"})
                    continue
                target = (root / member_path).resolve()
                try:
                    target.relative_to(root)
                except ValueError:
                    skipped.append({"path": member.name, "reason": "outside-root"})
                    continue
                if target.exists() and not overwrite_untracked:
                    skipped.append({"path": member.name, "reason": "exists"})
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    skipped.append({"path": member.name, "reason": "not-file"})
                    continue
                with source, target.open("wb") as out:
                    shutil.copyfileobj(source, out)
                restored.append({"path": member.name, "bytes": target.stat().st_size})

    return {
        "ok": True,
        "applied": True,
        "snapshot_id": snapshot_id,
        "snapshot_dir": str(snapshot_dir),
        "repo": str(root),
        "results": results,
        "restored_untracked": restored,
        "skipped_untracked": skipped,
        "manifest": manifest,
    }


def command_status(args: argparse.Namespace) -> int:
    payload = collect_status(Path(args.repo))
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_text_status(payload)
    return 0 if payload.get("ok") else 1


def command_plan(args: argparse.Namespace) -> int:
    payload = make_plan(Path(args.repo), include_actions=args.include_actions)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_text_plan(payload)
    return 0


def command_actions(args: argparse.Namespace) -> int:
    payload = {"ok": True, "actions": ACTION_CATALOG, "strategies": STRATEGIES}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for action in ACTION_CATALOG:
            print(f"{action['label']} ({action['git_name']})  id={action['id']}")
    return 0


def print_snapshot_result(payload: dict[str, Any]) -> None:
    if payload.get("ok"):
        print(f"Snapshot: {payload.get('snapshot_id')}")
        print(f"Directory: {payload.get('snapshot_dir')}")
        archive = ((payload.get("manifest") or {}).get("untracked_archive") or {})
        print(f"Untracked files archived: {archive.get('count', 0)}")
    else:
        print(f"Snapshot failed: {payload.get('error')}")


def command_snapshot(args: argparse.Namespace) -> int:
    payload = create_snapshot(Path(args.repo), snapshot_id=args.id)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_snapshot_result(payload)
    return 0 if payload.get("ok") else 1


def command_list_snapshots(args: argparse.Namespace) -> int:
    payload = list_snapshots(Path(args.repo))
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        if not payload.get("ok"):
            print(f"List snapshots failed: {payload.get('error')}")
            return 1
        print(f"Snapshot base: {payload.get('snapshot_base')}")
        for item in payload.get("snapshots", []):
            created = item.get("created_at") or "<unknown time>"
            level = item.get("level") or "<unknown level>"
            print(f"- {item['snapshot_id']}  {created}  {level}")
    return 0 if payload.get("ok") else 1


def command_restore(args: argparse.Namespace) -> int:
    payload = restore_snapshot(
        Path(args.repo),
        snapshot_id=args.snapshot,
        apply=args.apply,
        overwrite_untracked=args.overwrite_untracked,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        if payload.get("ok") and payload.get("applied"):
            print(f"Restored snapshot: {payload.get('snapshot_id')}")
            print(f"Untracked restored: {len(payload.get('restored_untracked', []))}")
            print(f"Untracked skipped: {len(payload.get('skipped_untracked', []))}")
        elif payload.get("ok"):
            print(f"Restore preview for snapshot: {payload.get('snapshot_id')}")
            print("No files were changed. Re-run with --apply to restore conservatively.")
            print_shell_fence(payload.get("commands", []), indent="")
        else:
            print(f"Restore failed: {payload.get('error')}")
    return 0 if payload.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evidence-based Git dirty-state checker and cleanup planner.")
    sub = parser.add_subparsers(dest="command")
    for name in ("status", "plan"):
        child = sub.add_parser(name)
        child.add_argument("--repo", default=".", help="Path to inspect. Git decides the actual root.")
        child.add_argument("--json", action="store_true")
        if name == "plan":
            child.add_argument("--include-actions", action="store_true")

    snapshot = sub.add_parser("snapshot", help="Create a reversible dirty-state snapshot.")
    snapshot.add_argument("--repo", default=".", help="Path to inspect. Git decides the actual root.")
    snapshot.add_argument("--id", default="", help="Optional snapshot id. Must be a safe single path segment.")
    snapshot.add_argument("--json", action="store_true")

    list_snap = sub.add_parser("list-snapshots", help="List dirty-state snapshots for this repo.")
    list_snap.add_argument("--repo", default=".", help="Path to inspect. Git decides the actual root.")
    list_snap.add_argument("--json", action="store_true")

    restore = sub.add_parser("restore", help="Preview or apply a conservative restore from a dirty snapshot.")
    restore.add_argument("--repo", default=".", help="Path to inspect. Git decides the actual root.")
    restore.add_argument("--snapshot", required=True, help="Snapshot id to restore.")
    restore.add_argument("--apply", action="store_true", help="Actually restore tracked patches and archived untracked files.")
    restore.add_argument("--overwrite-untracked", action="store_true", help="Allow archived untracked files to overwrite existing files during --apply.")
    restore.add_argument("--json", action="store_true")

    actions = sub.add_parser("actions")
    actions.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "status"):
        if args.command is None:
            setattr(args, "repo", ".")
            setattr(args, "json", False)
        return command_status(args)
    if args.command == "plan":
        return command_plan(args)
    if args.command == "actions":
        return command_actions(args)
    if args.command == "snapshot":
        return command_snapshot(args)
    if args.command == "list-snapshots":
        return command_list_snapshots(args)
    if args.command == "restore":
        return command_restore(args)
    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
