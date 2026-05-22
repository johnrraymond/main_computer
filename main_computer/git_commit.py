from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import shlex
import signal
import subprocess
import threading
import time
import uuid
from typing import Any, Callable


FINAL_JOB_STATUSES = {"finished", "failed", "cancelled"}


class GitCommitError(RuntimeError):
    """Raised when the guarded commit runner refuses to continue."""


class GitCommitCancelled(GitCommitError):
    """Raised when the user cancels the active commit job."""


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "checked"}
    return bool(value)


def _split_z(value: str) -> list[str]:
    return [item for item in str(value or "").split("\0") if item]


def _display_command(command: list[str], cwd: Path) -> str:
    rendered = " ".join(shlex.quote(str(part)) for part in command)
    return f"(cd {shlex.quote(str(cwd))} && {rendered})"


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            except Exception:
                process.terminate()
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except Exception:
                process.terminate()
    except Exception:
        try:
            process.terminate()
        except Exception:
            pass


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except Exception:
                process.kill()
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


class GitCommitRunner:
    """Small auditable Git commit executor for selected-file commits.

    Dry-run mode never mutates the Git index. Real mode stages selected paths,
    verifies that the cached set exactly matches the intended set, and only then
    calls ``git commit``. User cancellation is best-effort and intentionally does
    not auto-reset the repository; post-cancel Git state is reported instead.
    """

    def __init__(
        self,
        app_root: Path,
        *,
        emit: Callable[[dict[str, Any]], None],
        cancel_event: threading.Event,
        set_process: Callable[[subprocess.Popen[str] | None, list[str] | None], None],
    ) -> None:
        self.app_root = Path(app_root).resolve()
        self.emit = emit
        self.cancel_event = cancel_event
        self.set_process = set_process
        self.git_root: Path | None = None

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        dry_run = _as_bool(payload.get("dry_run"), default=True)
        one_at_a_time = _as_bool(payload.get("one_at_a_time"), default=False)
        confirm_real_commit = _as_bool(payload.get("confirm_real_commit"), default=False)
        message = self._clean_commit_message(payload.get("message") or payload.get("commit_message") or "")
        requested_branch = str(payload.get("branch") or "").strip()
        identity = self._payload_identity(payload)
        blocked_paths = self._normalize_blocked_paths(payload.get("blocked_paths") or [])
        if not dry_run and not confirm_real_commit:
            raise GitCommitError("Real commit mode requires confirm_real_commit=true.")
        if not message:
            raise GitCommitError("Commit message is required.")

        repo = self._resolve_repo_dir(str(payload.get("repo_dir") or payload.get("repo") or "."))
        self.emit({"type": "phase", "phase": "resolve_repo", "message": f"Resolving Git worktree from {repo}."})
        top = self._git(repo, ["rev-parse", "--show-toplevel"], check=True, phase="resolve_repo")
        git_root = Path(top["stdout"].strip()).resolve()
        self.git_root = git_root
        self.emit({"type": "repo", "phase": "resolve_repo", "repo": str(git_root), "message": f"Git root: {git_root}"})

        paths = self._validate_selected_paths(git_root, payload.get("paths") or payload.get("selected_paths") or [])
        if not paths:
            raise GitCommitError("At least one selected file path is required.")
        blocked_selected = sorted(set(paths) & blocked_paths)
        if blocked_selected:
            raise GitCommitError(f"Selected paths include hard-blocked files: {', '.join(blocked_selected)}")
        self._reject_unmerged_paths(git_root, paths)

        actual_branch = self._current_branch(git_root)
        if requested_branch and actual_branch and requested_branch != actual_branch:
            raise GitCommitError(f"Selected branch changed before commit: expected {requested_branch!r}, current {actual_branch!r}.")
        if requested_branch and not actual_branch:
            raise GitCommitError("Current Git branch could not be verified; refusing selected-file commit.")
        branch = actual_branch or requested_branch or "(detached-or-unknown)"

        identity = self._resolve_identity(git_root, identity)
        self.emit({
            "type": "summary",
            "phase": "validated",
            "message": f"{len(paths)} selected file{'s' if len(paths) != 1 else ''} validated.",
            "selected_files": paths,
            "branch": branch,
            "identity": identity,
            "dry_run": dry_run,
            "one_at_a_time": one_at_a_time,
        })

        pre_staged = self._cached_names(git_root)
        if dry_run:
            return self._dry_run_result(
                git_root,
                paths=paths,
                message=message,
                branch=branch,
                identity=identity,
                one_at_a_time=one_at_a_time,
                pre_staged=pre_staged,
            )

        if pre_staged:
            pre_staged_set = set(pre_staged)
            selected_set = set(paths)
            if not one_at_a_time and pre_staged_set.issubset(selected_set):
                if pre_staged_set == selected_set:
                    message_text = "The Git index already contains exactly the selected files; refreshing them and continuing the commit retry."
                else:
                    missing = sorted(selected_set - pre_staged_set)
                    message_text = (
                        "The Git index already contains a subset of the selected files from a prior attempt; "
                        f"refreshing all selected files and continuing the commit retry. Missing staged selections: {', '.join(missing)}"
                    )
                self.emit({
                    "type": "line",
                    "level": "warning",
                    "phase": "inspect_index",
                    "message": message_text,
                    "staged_files": pre_staged,
                    "selected_files": paths,
                })
            else:
                unexpected = sorted(pre_staged_set - selected_set)
                detail = unexpected or pre_staged
                raise GitCommitError(
                    "The Git index already has staged files outside the selected-file commit. "
                    "Unstage them before using the selected-file commit runner: "
                    + ", ".join(detail)
                )

        if one_at_a_time:
            return self._commit_one_at_a_time(git_root, paths=paths, message=message, branch=branch, identity=identity)
        return self._commit_selected_set(git_root, paths=paths, message=message, branch=branch, identity=identity)

    def _resolve_repo_dir(self, raw: str) -> Path:
        cleaned = str(raw or ".").strip() or "."
        candidate = Path(cleaned)
        if not candidate.is_absolute():
            candidate = self.app_root / candidate
        resolved = candidate.resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise GitCommitError(f"Repository path does not exist: {resolved}")
        return resolved

    def _clean_commit_message(self, value: Any) -> str:
        message = str(value or "").replace("\x00", "").strip()
        if len(message) > 10000:
            raise GitCommitError("Commit message is too long.")
        return message

    def _payload_identity(self, payload: dict[str, Any]) -> dict[str, str]:
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        name = str(payload.get("git_user_name") or identity.get("name") or "").strip()
        email = str(payload.get("git_user_email") or identity.get("email") or "").strip()
        return {"name": name, "email": email}

    def _normalize_blocked_paths(self, raw_paths: Any) -> set[str]:
        if not isinstance(raw_paths, list):
            return set()
        blocked: set[str] = set()
        for item in raw_paths:
            value = str(item or "").strip().replace("\\", "/")
            value = re.sub(r"^\./+", "", value)
            if value:
                blocked.add(value)
        return blocked

    def _validate_selected_paths(self, git_root: Path, raw_paths: Any) -> list[str]:
        if not isinstance(raw_paths, list):
            raise GitCommitError("Selected paths must be a JSON list.")
        selected: list[str] = []
        seen: set[str] = set()
        for raw in raw_paths:
            path = self._normalize_repo_path(raw)
            if path in seen:
                continue
            full = (git_root / path).resolve()
            try:
                full.relative_to(git_root)
            except ValueError as exc:
                raise GitCommitError(f"Selected path escapes the Git root: {path}") from exc
            if full.exists() and full.is_dir():
                raise GitCommitError(f"Directories cannot be committed by this runner; select files instead: {path}")
            if not full.exists() and not self._path_tracked(git_root, path):
                raise GitCommitError(f"Selected path does not exist and is not tracked by Git: {path}")
            selected.append(path)
            seen.add(path)
        return selected

    def _normalize_repo_path(self, raw: Any) -> str:
        value = str(raw or "").strip().replace("\\", "/")
        value = re.sub(r"^\./+", "", value)
        if not value:
            raise GitCommitError("Selected path is empty.")
        if value.startswith("/") or value.startswith("//") or value.startswith("\\\\") or re.match(r"^[A-Za-z]:[\\/]", value):
            raise GitCommitError(f"Absolute paths are not allowed: {raw}")
        pure = PurePosixPath(value)
        if pure.is_absolute():
            raise GitCommitError(f"Absolute paths are not allowed: {raw}")
        parts = pure.parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise GitCommitError(f"Unsafe selected path: {raw}")
        if ".git" in parts:
            raise GitCommitError(f"Git metadata paths are not allowed: {raw}")
        normalized = "/".join(parts)
        if normalized in {"", "."}:
            raise GitCommitError(f"Unsafe selected path: {raw}")
        return normalized

    def _path_tracked(self, git_root: Path, path: str) -> bool:
        result = self._git(git_root, ["ls-files", "--error-unmatch", "--", path], check=False, phase="validate_paths")
        return result["returncode"] == 0

    def _reject_unmerged_paths(self, git_root: Path, paths: list[str]) -> None:
        result = self._git(git_root, ["ls-files", "-u", "-z", "--", *paths], check=False, phase="validate_paths")
        if result["stdout"]:
            raise GitCommitError("Selected paths include unmerged conflict entries; resolve conflicts before committing.")

    def _current_branch(self, git_root: Path) -> str:
        result = self._git(git_root, ["branch", "--show-current"], check=False, phase="verify_branch")
        return result["stdout"].strip()

    def _resolve_identity(self, git_root: Path, identity: dict[str, str]) -> dict[str, str]:
        name = str(identity.get("name") or "").strip()
        email = str(identity.get("email") or "").strip()
        if not name:
            name = self._git(git_root, ["config", "--get", "user.name"], check=False, phase="verify_identity")["stdout"].strip()
        if not email:
            email = self._git(git_root, ["config", "--get", "user.email"], check=False, phase="verify_identity")["stdout"].strip()
        if not name:
            raise GitCommitError("Git user.name is required before committing.")
        if not email or "@" not in email:
            raise GitCommitError("Git user.email is required and must look like an email address before committing.")
        identity = {"name": name, "email": email}
        self.emit({"type": "identity", "phase": "verify_identity", "identity": identity, "message": f"Identity: {name} <{email}>."})
        return identity

    def _dry_run_result(
        self,
        git_root: Path,
        *,
        paths: list[str],
        message: str,
        branch: str,
        identity: dict[str, str],
        one_at_a_time: bool,
        pre_staged: list[str],
    ) -> dict[str, Any]:
        self.emit({"type": "line", "level": "info", "phase": "dry_run", "message": "Dry run started: no Git index changes and no commit will be created."})
        self.emit({"type": "line", "level": "info", "phase": "dry_run", "message": f"Commit message: {message}"})
        self.emit({"type": "line", "level": "info", "phase": "dry_run", "message": f"Branch: {branch}"})
        self.emit({"type": "line", "level": "info", "phase": "dry_run", "message": f"Identity: {identity['name']} <{identity['email']}>"})
        if pre_staged:
            if not one_at_a_time and set(pre_staged).issubset(set(paths)):
                message_text = (
                    "Current index already has staged selected files; real selected-file commit mode would "
                    "refresh all selected files and continue the retry."
                )
            else:
                message_text = (
                    "Current index already has staged files outside this selected-file commit; real mode would "
                    "refuse to start until those unrelated staged files are unstaged."
                )
            self.emit({
                "type": "line",
                "level": "warning",
                "phase": "dry_run",
                "message": message_text,
                "staged_files": pre_staged,
            })
        if one_at_a_time:
            self.emit({"type": "line", "level": "info", "phase": "dry_run", "message": f"Would create {len(paths)} separate commit(s), one selected file per commit."})
            for index, path in enumerate(paths, start=1):
                self._check_cancel()
                self.emit({"type": "progress", "phase": "dry_run", "current": index, "total": len(paths), "path": path, "message": f"Would stage, verify, and commit {index}/{len(paths)}: {path}"})
        else:
            self.emit({"type": "line", "level": "info", "phase": "dry_run", "message": f"Would stage {len(paths)} selected file(s) and create one commit."})
            for index, path in enumerate(paths, start=1):
                self.emit({"type": "progress", "phase": "dry_run", "current": index, "total": len(paths), "path": path, "message": f"Stage candidate {index}/{len(paths)}: {path}"})
        state = self._git_state(git_root)
        self.emit({"type": "git_state", "phase": "dry_run", "message": "Current Git state after dry run.", "git_state": state})
        self.emit({"type": "complete", "phase": "dry_run", "ok": True, "message": "Dry run complete."})
        return {
            "ok": True,
            "dry_run": True,
            "one_at_a_time": one_at_a_time,
            "repo": str(git_root),
            "branch": branch,
            "identity": identity,
            "selected_files": paths,
            "would_stage": paths,
            "would_commit": True,
            "pre_existing_staged_files": pre_staged,
            "git_state": state,
            "commit_hash": None,
            "commits": [],
        }

    def _commit_selected_set(
        self,
        git_root: Path,
        *,
        paths: list[str],
        message: str,
        branch: str,
        identity: dict[str, str],
    ) -> dict[str, Any]:
        self.emit({"type": "phase", "phase": "staging", "message": f"Staging {len(paths)} selected file(s) for one commit."})
        self._git(git_root, ["add", "--", *paths], check=True, phase="staging")
        staged = self._cached_names(git_root)
        staged_set = set(staged)
        selected_set = set(paths)
        unexpected = sorted(staged_set - selected_set)
        if unexpected:
            raise GitCommitError(
                "Staged set contains files outside the selected files. "
                f"selected={sorted(paths)!r}; staged={sorted(staged)!r}; unexpected={unexpected!r}. No automatic reset was run."
            )
        if not staged:
            raise GitCommitError("No staged changes remain after refreshing selected files; nothing to commit.")
        skipped = sorted(selected_set - staged_set)
        if skipped:
            self.emit({
                "type": "line",
                "level": "warning",
                "phase": "verify_staged_set",
                "message": (
                    "Only selected files with staged changes will be committed; unchanged selected files were skipped: "
                    + ", ".join(skipped)
                ),
                "staged_files": staged,
                "skipped_selected_files": skipped,
            })
        else:
            self.emit({"type": "line", "level": "success", "phase": "verify_staged_set", "message": "Staged set exactly matches selected files.", "staged_files": staged})
        self._check_cached_diff_advisory(git_root)
        self.emit({"type": "phase", "phase": "committing", "message": "Creating one Git commit for the selected set."})
        commit = self._git(git_root, self._commit_args(identity, message), check=True, phase="committing")
        commit_hash = self._head_hash(git_root)
        state = self._git_state(git_root)
        self.emit({"type": "complete", "phase": "completed", "ok": True, "commit_hash": commit_hash, "message": f"Commit created: {commit_hash}"})
        return {
            "ok": True,
            "dry_run": False,
            "one_at_a_time": False,
            "repo": str(git_root),
            "branch": branch,
            "identity": identity,
            "selected_files": paths,
            "staged_files": staged,
            "stdout": commit["stdout"],
            "stderr": commit["stderr"],
            "commit_hash": commit_hash,
            "commits": [{"hash": commit_hash, "paths": staged}],
            "git_state": state,
        }

    def _commit_one_at_a_time(
        self,
        git_root: Path,
        *,
        paths: list[str],
        message: str,
        branch: str,
        identity: dict[str, str],
    ) -> dict[str, Any]:
        commits: list[dict[str, Any]] = []
        self.emit({"type": "phase", "phase": "one_at_a_time", "message": f"Creating {len(paths)} commit(s), one selected file per commit."})
        for index, path in enumerate(paths, start=1):
            self._check_cancel()
            staged_before = self._cached_names(git_root)
            if staged_before:
                raise GitCommitError(
                    "The Git index is not empty before the next one-at-a-time commit. "
                    f"Staged files: {', '.join(staged_before)}. No automatic reset was run."
                )
            self.emit({"type": "progress", "phase": "staging", "current": index, "total": len(paths), "path": path, "message": f"Staging {index}/{len(paths)}: {path}"})
            self._git(git_root, ["add", "--", path], check=True, phase="staging")
            staged = self._cached_names(git_root)
            if staged != [path]:
                raise GitCommitError(
                    "One-at-a-time staged set does not exactly match the current selected file. "
                    f"current={path!r}; staged={staged!r}. No automatic reset was run."
                )
            self.emit({"type": "line", "level": "success", "phase": "verify_staged_set", "message": f"Staged set exactly matches {path}.", "staged_files": staged})
            self._check_cached_diff_advisory(git_root)
            self._check_cancel()
            self.emit({"type": "phase", "phase": "committing", "current": index, "total": len(paths), "path": path, "message": f"Creating commit {index}/{len(paths)} for {path}."})
            commit = self._git(git_root, self._commit_args(identity, message), check=True, phase="committing")
            commit_hash = self._head_hash(git_root)
            commits.append({"hash": commit_hash, "paths": [path], "stdout": commit["stdout"], "stderr": commit["stderr"]})
            self.emit({"type": "commit", "phase": "committing", "current": index, "total": len(paths), "path": path, "commit_hash": commit_hash, "message": f"Commit {index}/{len(paths)} created: {commit_hash} ({path})"})
        state = self._git_state(git_root)
        self.emit({"type": "complete", "phase": "completed", "ok": True, "commit_hash": commits[-1]["hash"] if commits else "", "message": f"Created {len(commits)} one-at-a-time commit(s)."})
        return {
            "ok": True,
            "dry_run": False,
            "one_at_a_time": True,
            "repo": str(git_root),
            "branch": branch,
            "identity": identity,
            "selected_files": paths,
            "commit_hash": commits[-1]["hash"] if commits else "",
            "commits": commits,
            "git_state": state,
        }

    def _check_cached_diff_advisory(self, git_root: Path) -> dict[str, Any]:
        """Run git's staged diff check as an advisory warning, not a commit blocker.

        ``git diff --cached --check`` is useful feedback, but Git itself does not
        require a clean whitespace check before ``git commit``.  The selected-file
        runner has already verified repo safety, identity, unmerged paths, and the
        exact staged path set, so whitespace/style findings should be streamed to
        the UI without preventing the requested commit.
        """

        result = self._git(git_root, ["diff", "--cached", "--check"], check=False, phase="verify_staged_set")
        if result["returncode"] == 0:
            return result
        detail = result["stderr"].strip() or result["stdout"].strip() or "git diff --cached --check reported advisory warnings."
        self.emit({
            "type": "line",
            "level": "warning",
            "phase": "verify_staged_set",
            "message": "Advisory only: git diff --cached --check reported whitespace/style warnings; continuing to git commit.",
            "returncode": result["returncode"],
            "stdout": result["stdout"][-4000:],
            "stderr": result["stderr"][-4000:],
            "detail": detail[-4000:],
        })
        return result

    def _commit_args(self, identity: dict[str, str], message: str) -> list[str]:
        return [
            "-c",
            f"user.name={identity['name']}",
            "-c",
            f"user.email={identity['email']}",
            "commit",
            "-m",
            message,
        ]

    def _head_hash(self, git_root: Path) -> str:
        return self._git(git_root, ["rev-parse", "HEAD"], check=True, phase="completed")["stdout"].strip()

    def _cached_names(self, git_root: Path) -> list[str]:
        result = self._git(git_root, ["diff", "--cached", "--name-only", "-z"], check=True, phase="inspect_index")
        return sorted(_split_z(result["stdout"]))

    def _git_state(self, git_root: Path) -> dict[str, Any]:
        staged = self._cached_names(git_root)
        unstaged = _split_z(self._git(git_root, ["diff", "--name-only", "-z"], check=False, phase="inspect_state")["stdout"])
        branch = self._current_branch(git_root)
        return {
            "repo": str(git_root),
            "branch": branch,
            "staged": sorted(staged),
            "unstaged": sorted(unstaged),
            "untracked": [],
            "untracked_skipped": True,
            "untracked_note": "Untracked-file scan skipped to avoid expensive repository-wide git ls-files --others.",
            "recovery_hint": "Use git reset -- <path> to unstage files if cancellation or failure left staged changes.",
        }

    def _check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise GitCommitCancelled("Commit job cancelled by user.")

    def _git(self, cwd: Path, args: list[str], *, check: bool, phase: str) -> dict[str, Any]:
        self._check_cancel()
        command = ["git", *args]
        display = _display_command(command, cwd)
        self.emit({"type": "command_start", "phase": phase, "command": command, "message": f"Running: {display}"})
        creationflags = 0
        preexec_fn = None
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            preexec_fn = os.setsid
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
                preexec_fn=preexec_fn,
            )
        except FileNotFoundError as exc:
            raise GitCommitError("Git executable was not found.") from exc
        self.set_process(process, command)
        stdout = ""
        stderr = ""
        try:
            while True:
                if self.cancel_event.is_set():
                    self.emit({"type": "cancel_requested", "phase": phase, "message": "Cancellation requested; terminating active Git process."})
                    _terminate_process_tree(process)
                    try:
                        stdout, stderr = process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        _kill_process_tree(process)
                        stdout, stderr = process.communicate()
                    self.emit({
                        "type": "command_cancelled",
                        "phase": phase,
                        "returncode": process.returncode,
                        "stdout": stdout or "",
                        "stderr": stderr or "",
                        "message": "Active Git command was stopped.",
                    })
                    raise GitCommitCancelled("Commit job cancelled by user.")
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    # Keep draining stdout/stderr while the command runs.  Waiting
                    # with poll() before reading can deadlock when Git emits more
                    # than the OS pipe buffer, for example a large staged file list.
                    continue
        finally:
            self.set_process(None, None)
        result = {
            "command": command,
            "returncode": int(process.returncode or 0),
            "stdout": stdout or "",
            "stderr": stderr or "",
        }
        level = "success" if result["returncode"] == 0 else "error"
        self.emit({
            "type": "command_finish",
            "level": level,
            "phase": phase,
            "command": command,
            "returncode": result["returncode"],
            "stdout": result["stdout"][-4000:],
            "stderr": result["stderr"][-4000:],
            "message": f"Finished ({result['returncode']}): {display}",
        })
        for source, text in (("stdout", result["stdout"]), ("stderr", result["stderr"])):
            for line in text.splitlines()[:80]:
                if line.strip():
                    self.emit({"type": "line", "level": "info" if source == "stdout" else "warning", "phase": phase, "message": line})
        if check and result["returncode"] != 0:
            detail = result["stderr"].strip() or result["stdout"].strip() or f"git {' '.join(args)} failed"
            raise GitCommitError(detail)
        return result


class GitCommitJobManager:
    """Threaded job manager that exposes commit execution as streamable events."""

    def __init__(self, app_root: Path) -> None:
        self.app_root = Path(app_root).resolve()
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def start_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Commit job payload must be a JSON object.")
        with self._lock:
            active = next((job for job in self._jobs.values() if job.get("status") not in FINAL_JOB_STATUSES), None)
            if active:
                return {
                    "ok": False,
                    "busy": True,
                    "error": "Another commit job is already running.",
                    "job_id": active.get("id"),
                    "status": active.get("status"),
                }
            job_id = uuid.uuid4().hex[:12]
            job = {
                "id": job_id,
                "status": "starting",
                "started_at": time.time(),
                "finished_at": None,
                "events": [],
                "seq": 0,
                "cancel_event": threading.Event(),
                "cancel_requested": False,
                "process": None,
                "process_command": None,
                "result": None,
                "error": "",
            }
            self._jobs[job_id] = job
        self._append_event(job, {"type": "job_started", "status": "starting", "message": "Commit job accepted by backend.", "job_id": job_id})
        thread = threading.Thread(target=self._run_job, args=(job, dict(payload)), name=f"git-commit-job-{job_id}", daemon=True)
        with self._lock:
            job["thread"] = thread
        thread.start()
        return {
            "ok": True,
            "job_id": job_id,
            "status": "starting",
            "stream_url": f"/api/applications/git/project/commit/stream?job_id={job_id}",
        }

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(str(job_id or ""))
            if not job:
                return {"ok": False, "error": "Unknown commit job.", "job_id": job_id}
            if job.get("status") in FINAL_JOB_STATUSES:
                return {"ok": True, "cancelled": False, "message": "Commit job is already finished.", "job_id": job_id, "status": job.get("status")}
            job["cancel_requested"] = True
            job["cancel_event"].set()
            process = job.get("process")
        self._append_event(job, {"type": "cancel_requested", "status": job.get("status"), "message": "Cancellation requested by user.", "job_id": job_id})
        if isinstance(process, subprocess.Popen) and process.poll() is None:
            _terminate_process_tree(process)
        return {"ok": True, "cancelled": True, "job_id": job_id, "status": job.get("status"), "message": "Cancellation requested."}

    def job_events(self, job_id: str, *, after_seq: int = 0) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(str(job_id or ""))
            if not job:
                return {"ok": False, "error": "Unknown commit job.", "job_id": job_id, "events": [], "done": True}
            events = [dict(event) for event in job.get("events", []) if int(event.get("seq") or 0) > int(after_seq or 0)]
            done = job.get("status") in FINAL_JOB_STATUSES
            return {
                "ok": True,
                "job_id": job_id,
                "status": job.get("status"),
                "events": events,
                "done": done,
                "result": job.get("result"),
                "error": job.get("error", ""),
            }

    def _run_job(self, job: dict[str, Any], payload: dict[str, Any]) -> None:
        cancel_event: threading.Event = job["cancel_event"]

        def emit(event: dict[str, Any]) -> None:
            self._append_event(job, event)

        def set_process(process: subprocess.Popen[str] | None, command: list[str] | None) -> None:
            with self._lock:
                job["process"] = process
                job["process_command"] = list(command) if command else None

        with self._lock:
            job["status"] = "running"
        self._append_event(job, {"type": "job_status", "status": "running", "message": "Commit job running.", "job_id": job["id"]})
        runner = GitCommitRunner(self.app_root, emit=emit, cancel_event=cancel_event, set_process=set_process)
        try:
            result = runner.run(payload)
        except GitCommitCancelled as exc:
            state = {}
            if runner.git_root is not None:
                try:
                    state = runner._git_state(runner.git_root)
                    self._append_event(job, {"type": "git_state", "phase": "cancelled", "message": "Git state after cancellation.", "git_state": state})
                except Exception as state_exc:
                    state = {"error": str(state_exc)}
            result = {"ok": False, "cancelled": True, "error": str(exc), "git_state": state}
            with self._lock:
                job["status"] = "cancelled"
                job["result"] = result
                job["error"] = str(exc)
                job["finished_at"] = time.time()
            self._append_event(job, {"type": "cancelled", "status": "cancelled", "message": "Commit job cancelled by user.", "job_id": job["id"], "git_state": state})
            return
        except Exception as exc:
            state = {}
            if runner.git_root is not None:
                try:
                    state = runner._git_state(runner.git_root)
                    self._append_event(job, {"type": "git_state", "phase": "failed", "message": "Git state after failure.", "git_state": state})
                except Exception as state_exc:
                    state = {"error": str(state_exc)}
            result = {"ok": False, "error": str(exc), "git_state": state}
            with self._lock:
                job["status"] = "failed"
                job["result"] = result
                job["error"] = str(exc)
                job["finished_at"] = time.time()
            self._append_event(job, {"type": "failed", "status": "failed", "level": "error", "message": str(exc), "job_id": job["id"], "git_state": state})
            return
        with self._lock:
            job["status"] = "finished"
            job["result"] = result
            job["finished_at"] = time.time()
        self._append_event(job, {"type": "finished", "status": "finished", "ok": bool(result.get("ok")), "message": "Commit job finished.", "job_id": job["id"], "result": result})

    def _append_event(self, job: dict[str, Any], event: dict[str, Any]) -> None:
        with self._lock:
            job["seq"] = int(job.get("seq") or 0) + 1
            payload = {
                "seq": job["seq"],
                "time": _utc_now(),
                "job_id": job["id"],
                **event,
            }
            job.setdefault("events", []).append(payload)
            job["events"] = job["events"][-1000:]
