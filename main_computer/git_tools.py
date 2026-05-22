from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
import threading
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from main_computer.git_commit import GitCommitJobManager


class GitToolsService:
    """Repository-local git and patch façade for the applications surface.

    This service is intentionally conservative. It exposes auditable read paths
    and guarded patch execution built on top of the internal patch harness
    living under tools/patching.
    """

    GIT_SERVER_SERVICE = "gitea"
    GIT_SERVER_COMPOSE_FILE = "docker-compose.gitea.yml"
    GIT_SERVER_COMPOSE_PROJECT = "main-computer-gitea"
    GIT_SERVER_WEB_URL = "http://localhost:3000/"
    GIT_SERVER_SSH_AVAILABLE = False
    GIT_SERVER_LOCAL_REMOTE_NAME = "local-gitea"
    GIT_SERVER_LOCAL_USER = "local"
    GIT_SERVER_LOCAL_EMAIL = "local@main-computer.local"
    GIT_SERVER_LOCAL_PASSWORD_ENV = "MAIN_COMPUTER_GIT_SERVER_LOCAL_PASSWORD"
    GIT_SERVER_LOCAL_PASSWORD_DEFAULT = "local-main-computer-change-me"

    def __init__(self, repo_root: Path, *, load_patch_service: bool = True) -> None:
        self.repo_root = repo_root.resolve()
        self.tools_root = self.repo_root / "tools"
        self.patching_root = self.tools_root / "patching"
        self._patch_service = None
        self._patch_layout = None
        self._patch_import_error: str | None = None
        self._operation_state_lock = threading.RLock()
        self._operation_run_lock = threading.Lock()
        self._operation_active: dict[str, Any] | None = None
        self._operation_history: list[dict[str, Any]] = []
        self._operation_process: subprocess.Popen[str] | None = None
        self._secrets_scan_lock = threading.RLock()
        self._secrets_scan_jobs: dict[str, dict[str, Any]] = {}
        self._commit_jobs = GitCommitJobManager(self.repo_root)
        if load_patch_service:
            self._load_patch_service()

    @property
    def patching_available(self) -> bool:
        return self._patch_service is not None and self._patch_layout is not None

    def git_operation_status(self) -> dict[str, Any]:
        with self._operation_state_lock:
            active = self._operation_snapshot(self._operation_active) if self._operation_active else None
            history = [self._operation_snapshot(item) for item in self._operation_history[-25:]]
        return {"ok": True, "active": active, "history": history, "busy": active is not None}

    def cancel_git_operation(self) -> dict[str, Any]:
        with self._operation_state_lock:
            active = self._operation_active
            if not active:
                return {"ok": True, "cancelled": False, "message": "No Git operation is currently running.", "active": None}
            active["cancel_requested"] = True
            active["status"] = "cancelling"
            self._operation_log("Cancel requested.")
            process = self._operation_process
            pid = process.pid if process and process.poll() is None else None
        if process and process.poll() is None:
            try:
                process.terminate()
            except Exception as exc:
                self._operation_log("Cancel terminate failed.", {"error": str(exc)})
        return {"ok": True, "cancelled": True, "pid": pid, "active": self.git_operation_status()["active"]}

    def run_git_operation(self, kind: str, label: str, callback: Any, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._operation_run_lock.acquire(blocking=False):
            return {
                "ok": False,
                "busy": True,
                "error": "Another Git operation is already running. Cancel it or wait for it to finish.",
                "operation": self.git_operation_status().get("active"),
            }
        operation = {
            "id": uuid.uuid4().hex[:12],
            "kind": str(kind or "git-operation"),
            "label": str(label or kind or "Git operation"),
            "status": "running",
            "ok": None,
            "started_at": time.time(),
            "finished_at": None,
            "elapsed": 0.0,
            "cancel_requested": False,
            "payload": payload or {},
            "logs": [],
            "result": None,
            "process": None,
        }
        with self._operation_state_lock:
            self._operation_active = operation
        self._operation_log("Operation started.")
        try:
            result = callback()
            if not isinstance(result, dict):
                result = {"ok": True, "result": result}
            with self._operation_state_lock:
                cancelled = bool(operation.get("cancel_requested"))
            if cancelled and result.get("ok") is not True:
                result.setdefault("error", "Operation cancelled.")
            status = "cancelled" if cancelled and not result.get("ok") else ("succeeded" if result.get("ok") else "failed")
            self._finish_git_operation(operation, status, result)
            return {**result, "operation": self._operation_snapshot(operation)}
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
            self._finish_git_operation(operation, "cancelled" if operation.get("cancel_requested") else "failed", result)
            return {**result, "operation": self._operation_snapshot(operation)}
        finally:
            with self._operation_state_lock:
                self._operation_active = None
                self._operation_process = None
            self._operation_run_lock.release()

    def _finish_git_operation(self, operation: dict[str, Any], status: str, result: dict[str, Any]) -> None:
        with self._operation_state_lock:
            operation["status"] = status
            operation["ok"] = bool(result.get("ok"))
            operation["finished_at"] = time.time()
            operation["elapsed"] = round(operation["finished_at"] - operation["started_at"], 3)
            operation["result"] = self._redact_step(result) if hasattr(self, "_redact_step") else result
            operation["process"] = None
            self._operation_log(f"Operation {status}.")
            self._operation_history.append(self._operation_snapshot(operation))
            self._operation_history = self._operation_history[-50:]

    def _operation_log(self, message: str, data: dict[str, Any] | None = None) -> None:
        with self._operation_state_lock:
            operation = self._operation_active
            if not operation:
                return
            now = time.time()
            operation["elapsed"] = round(now - operation["started_at"], 3)
            entry = {
                "time": now,
                "elapsed": operation["elapsed"],
                "message": message,
                "data": self._redact_step(data or {}) if hasattr(self, "_redact_step") else (data or {}),
            }
            operation.setdefault("logs", []).append(entry)
            operation["logs"] = operation["logs"][-200:]

    def _operation_snapshot(self, operation: dict[str, Any] | None) -> dict[str, Any] | None:
        if not operation:
            return None
        snapshot = dict(operation)
        snapshot["elapsed"] = round((snapshot.get("finished_at") or time.time()) - snapshot.get("started_at", time.time()), 3)
        if snapshot.get("process") and isinstance(snapshot["process"], dict):
            snapshot["process"] = dict(snapshot["process"])
        return self._redact_step(snapshot) if hasattr(self, "_redact_step") else snapshot

    def _operation_cancel_requested(self) -> bool:
        with self._operation_state_lock:
            return bool(self._operation_active and self._operation_active.get("cancel_requested"))

    def _run_command(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout: int | None = None,
        not_found_stderr: str | None = None,
    ) -> dict[str, Any]:
        self._operation_log("Starting subprocess.", {"command": command, "cwd": str(cwd)})
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            return {"command": command, "returncode": 127, "stdout": "", "stderr": not_found_stderr or f"{command[0]} is not available."}
        started = time.time()
        started_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))
        with self._operation_state_lock:
            if self._operation_active is not None:
                self._operation_process = process
                self._operation_active["process"] = {"pid": process.pid, "command": command}
        try:
            while True:
                if self._operation_cancel_requested():
                    self._operation_log("Cancelling subprocess.", {"pid": process.pid})
                    try:
                        process.terminate()
                    except Exception:
                        pass
                    try:
                        stdout, stderr = process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                        except Exception:
                            pass
                        stdout, stderr = process.communicate()
                    return {
                        "command": command,
                        "returncode": 130,
                        "stdout": stdout or "",
                        "stderr": (stderr or "") + "\nCommand cancelled by user.",
                        "started_at": started_iso,
                        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }

                elapsed = time.time() - started
                if timeout is not None and elapsed >= timeout:
                    self._operation_log("Subprocess timed out.", {"timeout": timeout})
                    try:
                        process.terminate()
                    except Exception:
                        pass
                    try:
                        stdout, stderr = process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                        except Exception:
                            pass
                        stdout, stderr = process.communicate()
                    return {
                        "command": command,
                        "returncode": 124,
                        "stdout": stdout or "",
                        "stderr": (stderr or "") + f"\nCommand timed out after {timeout} seconds.",
                        "started_at": started_iso,
                        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }

                communicate_timeout = 0.2
                if timeout is not None:
                    communicate_timeout = max(0.01, min(communicate_timeout, timeout - elapsed))
                try:
                    stdout, stderr = process.communicate(timeout=communicate_timeout)
                except subprocess.TimeoutExpired:
                    continue

                returncode = process.returncode
                self._operation_log("Subprocess finished.", {"returncode": returncode})
                return {
                    "command": command,
                    "returncode": returncode,
                    "stdout": stdout or "",
                    "stderr": stderr or "",
                    "started_at": started_iso,
                    "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
        finally:
            with self._operation_state_lock:
                if self._operation_process is process:
                    self._operation_process = None
                if self._operation_active is not None:
                    self._operation_active["process"] = None

    def capabilities(self) -> dict[str, Any]:
        git_control_script = self.repo_root / "git-control.py"
        git_control_available = git_control_script.exists()
        return {
            "git_status": True,
            "git_recent_commits": True,
            "git_server": self.git_server_capabilities(),
            "git_control_cli": {
                "available": git_control_available,
                "script": str(git_control_script),
                "plan_command": "python git-control.py --plan",
                "direct_git_command": "python git-control.py --git <git-args>",
                "doc_shim_command": "python git-control.py --doc-shim <git-command>",
                "shim_rerun_command": "python git-control.py --run-shim <shim-id>",
                "list_shims_command": "python git-control.py --list-shims",
                "delete_shim_command": "python git-control.py --delete-shim <shim-id>",
                "extract_ai_output_command": "python git-control.py --extract-shims-from <file>",
                "ordain_shim_command": "python git-control.py --ordain-shim <shim-id>",
                "unordain_shim_command": "python git-control.py --unordain-shim <shim-id>",
                "ai_brief_command": "python git-control.py --ai-brief --prompt <request>",
                "recommendation_values": ["good", "not-recommended"],
                "shim_storage": ".git/git-control",
                "shim_format": "metadata-rich .shim files with #-comments, ordination metadata, and git-control shim-code",
            },
            "patch_list": self.patching_available,
            "patch_read": self.patching_available,
            "patch_apply": self.patching_available,
            "patch_reverse": self.patching_available,
            "patch_dry_run_preview": self.patching_available,
            "patch_dry_run_read": self.patching_available,
            "planned_git_commands": [
                {
                    "name": "git_control_plan",
                    "implemented": git_control_available,
                    "summary": "human-facing plan and computed-sum entrypoint via python git-control.py --plan",
                },
                {
                    "name": "arbitrary_git_args",
                    "implemented": git_control_available,
                    "summary": "direct arbitrary git argument execution via python git-control.py --git <git-args>",
                },
                {
                    "name": "plan_shim",
                    "implemented": git_control_available,
                    "summary": "every python git-control.py --plan run creates a first-class documentation shim with included inspection shims",
                },
                {
                    "name": "documentation_shims",
                    "implemented": git_control_available,
                    "summary": "metadata-only git command documentation shims via python git-control.py --doc-shim <git-command>",
                },
                {
                    "name": "rerunnable_shims",
                    "implemented": git_control_available,
                    "summary": "durable .shim files stored under .git/git-control/shims with #-comment metadata plus shim-doc/shim-include/git directives",
                },
                {
                    "name": "git_console",
                    "implemented": git_control_available,
                    "summary": "UI console can extract python git-control.py commands and shim-code from AI output, create shims, view/delete/ordain shims, and rerun stored shim commands",
                },
                {
                    "name": "ai_generated_shims",
                    "implemented": git_control_available,
                    "summary": "AI prompts load ordained shims as context and ask the model to return shim-code with ordination-recommendation good or not-recommended",
                },
                {
                    "name": "ordained_context",
                    "implemented": git_control_available,
                    "summary": "human-ordained shims are included in future AI briefs through python git-control.py --ai-brief",
                },
            ],
            "patch_import_error": self._patch_import_error,
        }

    def repo_path(self, raw: str | None) -> Path:
        cleaned = self._clean_git_panel_path_token(str(raw or ".").strip() or ".")
        candidate = Path(cleaned)
        if not candidate.is_absolute():
            candidate = self.repo_root / candidate
        resolved = candidate.resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise ValueError("Requested git tools repository does not exist.")
        return resolved

    def _clean_git_panel_path_token(self, value: str) -> str:
        cleaned = str(value or ".").strip()
        for _ in range(4):
            previous = cleaned
            cleaned = cleaned.strip().replace('\\"', '"')
            if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
                cleaned = cleaned[1:-1].strip()
            if cleaned == previous:
                break
        return cleaned or "."

    def git_server_capabilities(self) -> dict[str, Any]:
        compose_file = self.repo_root / self.GIT_SERVER_COMPOSE_FILE
        compose_text = ""
        if compose_file.exists():
            try:
                compose_text = compose_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                compose_text = ""
        installed = f"{self.GIT_SERVER_SERVICE}:" in compose_text
        compose_command = self._git_server_compose_command_text()
        return {
            "available": installed,
            "service": self.GIT_SERVER_SERVICE,
            "profile": "",
            "compose_project": self.GIT_SERVER_COMPOSE_PROJECT,
            "compose_file": str(compose_file),
            "web_url": self.GIT_SERVER_WEB_URL,
            "ssh_available": self.GIT_SERVER_SSH_AVAILABLE,
            "start_command": f"{compose_command} up -d {self.GIT_SERVER_SERVICE}",
            "stop_command": f"{compose_command} stop {self.GIT_SERVER_SERVICE}",
            "status_command": f"{compose_command} ps {self.GIT_SERVER_SERVICE}",
            "clone_examples": [
                "http://localhost:3000/<owner>/<repo>.git",
            ],
            "managed_server_options": [
                {
                    "name": "shared",
                    "summary": "Standalone machine-wide local Gitea Docker stack shared by every Main Computer mode.",
                    "service": self.GIT_SERVER_SERVICE,
                    "profile": "",
                    "compose_project": self.GIT_SERVER_COMPOSE_PROJECT,
                    "compose_file": str(compose_file),
                    "web_url": "http://localhost:3000/",
                    "ssh_available": False,
                    "start_command": f"{compose_command} up -d {self.GIT_SERVER_SERVICE}",
                    "stop_command": f"{compose_command} stop {self.GIT_SERVER_SERVICE}",
                    "status_command": f"{compose_command} ps {self.GIT_SERVER_SERVICE}",
                    "logs_command": f"{compose_command} logs --tail 120 {self.GIT_SERVER_SERVICE}",
                    "clone_examples": [
                        "http://localhost:3000/<owner>/<repo>.git",
                    ],
                    "persistent": True,
                    "data_volume": "main-computer-applications_gitea-data",
                },
            ],
            "default_local_remote": {
                "remote": self.GIT_SERVER_LOCAL_REMOTE_NAME,
                "owner": self.GIT_SERVER_LOCAL_USER,
                "protocol": "http",
                "url_template": "http://localhost:3000/local/<repo>.git",
                "configure_endpoint": "/api/applications/git/server/remote/configure",
                "prefunk_endpoint": "/api/applications/git/server/target-prefunk",
                "setup_endpoint": "/api/applications/git/server/setup-local",
                "push_endpoint": "/api/applications/git/server/push-local",
                "mirror_setup_endpoint": "/api/applications/git/server/mirror/setup",
                "preserve_origin_by_default": True,
            },
            "external_remote_options": [
                {
                    "name": "direct-origin",
                    "summary": "Point this local checkout directly at GitHub, GitLab, Gitea Cloud, or another HTTPS/SSH Git host.",
                    "command": "git remote set-url origin <external-url>",
                },
                {
                    "name": "direct-upstream",
                    "summary": "Keep origin as-is and add an upstream remote for the external Git host.",
                    "command": "git remote add upstream <external-url>",
                },
                {
                    "name": "local-plus-external",
                    "summary": "Keep a local Gitea remote while also keeping or adding a public service remote.",
                    "command": "git remote add local-gitea http://localhost:3000/local/<repo>.git && git remote add upstream <external-url>",
                },
            ],
            "remote_command_presets": [
                {
                    "name": "add-remote",
                    "summary": "Add the local Gitea repository as a new git remote.",
                    "command": "git remote add <remote> http://localhost:3000/<owner>/<repo>.git",
                },
                {
                    "name": "set-url",
                    "summary": "Point an existing git remote at the local Gitea repository.",
                    "command": "git remote set-url <remote> http://localhost:3000/<owner>/<repo>.git",
                },
                {
                    "name": "push-head",
                    "summary": "Push the current branch to the selected local Gitea remote and set upstream tracking.",
                    "command": "git push -u <remote> HEAD",
                },
                {
                    "name": "fetch",
                    "summary": "Fetch refs from the selected local Gitea remote.",
                    "command": "git fetch <remote>",
                },
                {
                    "name": "show-remotes",
                    "summary": "Show configured local git remotes.",
                    "command": "git remote -v",
                },
            ],
        }

    def git_server_status(self) -> dict[str, Any]:
        status = self._git_server_base_status()
        if not status["compose_file_exists"] or not status["docker_available"]:
            return status

        ps = self._run_docker_compose(["ps", self.GIT_SERVER_SERVICE], allow_failure=True)
        status["ps"] = ps
        running = self._compose_service_is_running(ps)
        status["running"] = bool(running)
        status["state"] = "running" if running else "stopped-or-not-created"
        status["ok"] = True
        return status

    def _docker_unavailable_payload(self, base_status: dict[str, Any], *, action: str = "") -> dict[str, Any]:
        compose_file = str(base_status.get("compose_file") or (self.repo_root / "docker-compose.dev.yml"))
        return {
            **base_status,
            "ok": False,
            "action": action or None,
            "error": "Docker CLI is not available in the environment running Main Computer.",
            "reason": "docker-cli-unavailable",
            "requires_docker_cli": True,
            "can_configure_local_remote_without_docker": True,
            "manual_remote_note": "Use Local Server plus Apply Command can still configure the local git remote. Starting the standalone Gitea stack, creating the local Gitea repo, and Push to Local Server require Docker.",
            "next_actions": [
                "Run Main Computer from the host/WSL environment where docker is installed and on PATH.",
                "If Main Computer is running inside a container, install/mount the Docker CLI and Docker socket or expose a valid DOCKER_HOST.",
                f"Verify from the same shell/process with: {self._git_server_compose_command_text()} ps {self.GIT_SERVER_SERVICE}",
                "Until Docker is available, use Apply Command only to configure remotes; Push to Local Server cannot start or prepare Gitea.",
            ],
        }

    def git_server_action(self, action: str) -> dict[str, Any]:
        cleaned = str(action or "").strip().lower().replace("_", "-")
        commands = {
            "status": ["ps", self.GIT_SERVER_SERVICE],
            "start": ["up", "-d", self.GIT_SERVER_SERVICE],
            "stop": ["stop", self.GIT_SERVER_SERVICE],
            "restart": ["restart", self.GIT_SERVER_SERVICE],
            "logs": ["logs", "--tail", "120", self.GIT_SERVER_SERVICE],
            "pull": ["pull", self.GIT_SERVER_SERVICE],
        }
        if cleaned not in commands:
            raise ValueError("Unsupported git server action. Use status, start, stop, restart, logs, or pull.")

        base_status = self._git_server_base_status()
        if not base_status["compose_file_exists"]:
            return {**base_status, "action": cleaned, "ok": False, "error": "docker-compose.gitea.yml is missing."}
        if not base_status["docker_available"]:
            return self._docker_unavailable_payload(base_status, action=cleaned)

        result = self._run_docker_compose(commands[cleaned], allow_failure=True, timeout=90)
        payload = {
            "ok": result["returncode"] == 0,
            "action": cleaned,
            "command": result["command"],
            "result": result,
            "service": self.GIT_SERVER_SERVICE,
            "profile": "",
            "compose_project": self.GIT_SERVER_COMPOSE_PROJECT,
            "web_url": self.GIT_SERVER_WEB_URL,
            "clone_examples": self.git_server_capabilities()["clone_examples"],
        }
        if cleaned != "logs":
            payload["status"] = self.git_server_status()
        if result["returncode"] != 0:
            payload["error"] = result["stderr"] or result["stdout"] or "Docker compose command failed."
        return payload

    def git_server_remote_url(self, *, owner: str, repo_name: str, protocol: str = "http") -> str:
        cleaned_owner = self._clean_git_server_remote_segment(owner, "owner")
        cleaned_repo = self._clean_git_server_remote_segment(repo_name, "repository")
        cleaned_protocol = str(protocol or "http").strip().lower()
        if cleaned_protocol == "http":
            return f"{self.GIT_SERVER_WEB_URL.rstrip('/')}/{cleaned_owner}/{cleaned_repo}.git"
        if cleaned_protocol == "ssh":
            raise ValueError("Local Gitea SSH is disabled for the standalone shared Gitea stack; use HTTP localhost:3000.")
        raise ValueError("Git server remote protocol must be http.")

    def _ensure_git_root_for_remote_setup(self, repo: Path) -> tuple[Path | None, dict[str, Any]]:
        top = self._run_git(repo, ["rev-parse", "--show-toplevel"], check=False)
        if top["returncode"] == 0:
            return Path(top["stdout"].strip()).resolve(), {"created": False, "top": top}

        init = self._run_git(repo, ["init"], check=False)
        if init["returncode"] != 0:
            return None, {
                "created": False,
                "top": top,
                "init": init,
                "error": init["stderr"] or init["stdout"] or top["stderr"] or "Could not initialize a git repository.",
            }

        top_after = self._run_git(repo, ["rev-parse", "--show-toplevel"], check=False)
        if top_after["returncode"] != 0:
            return None, {
                "created": True,
                "top": top_after,
                "init": init,
                "error": top_after["stderr"] or top_after["stdout"] or "Git repository was initialized but git root could not be resolved.",
            }
        return Path(top_after["stdout"].strip()).resolve(), {"created": True, "top": top_after, "init": init}


    def _parse_local_gitea_remote_url(self, url: str) -> dict[str, str] | None:
        raw = str(url or "").strip()
        if not raw:
            return None

        http_base = re.escape(self.GIT_SERVER_WEB_URL.rstrip("/"))
        http_match = re.match(rf"^{http_base}/([^/]+)/([^/]+?)(?:\.git)?/?$", raw, flags=re.IGNORECASE)
        if http_match:
            owner, repo_name = http_match.groups()
            return {
                "protocol": "http",
                "owner": owner,
                "repo": repo_name,
                "url": raw,
            }

        ssh_match = re.match(
            r"^ssh://git@localhost:2222/([^/]+)/([^/]+?)(?:\.git)?/?$",
            raw,
            flags=re.IGNORECASE,
        )
        if ssh_match:
            owner, repo_name = ssh_match.groups()
            return {
                "protocol": "ssh",
                "owner": owner,
                "repo": repo_name,
                "url": raw,
            }

        return None

    def _local_gitea_target_from_remotes(self, remotes: list[dict[str, str]]) -> dict[str, Any] | None:
        for remote in remotes:
            if remote.get("name") != self.GIT_SERVER_LOCAL_REMOTE_NAME:
                continue
            parsed = self._parse_local_gitea_remote_url(remote.get("fetch") or remote.get("push") or "")
            if not parsed:
                continue
            if parsed["protocol"] == "ssh":
                return {
                    "remote": self.GIT_SERVER_LOCAL_REMOTE_NAME,
                    "owner": parsed["owner"],
                    "repo": parsed["repo"],
                    "protocol": "http",
                    "url": self.git_server_remote_url(owner=parsed["owner"], repo_name=parsed["repo"], protocol="http"),
                    "legacy_url": parsed["url"],
                    "source": "detected-legacy-ssh-local-gitea-remote",
                    "saved": False,
                    "configurable": True,
                }
            return {
                "remote": self.GIT_SERVER_LOCAL_REMOTE_NAME,
                "owner": parsed["owner"],
                "repo": parsed["repo"],
                "protocol": parsed["protocol"],
                "url": parsed["url"],
                "source": "detected-from-git-remote",
                "saved": True,
                "configurable": True,
            }
        return None

    def git_server_target_prefunk(self, repo_dir: str | None = ".") -> dict[str, Any]:
        """Read-only Local Gitea target probe for the selected project.

        This intentionally avoids git status/log/dirty-plan work. It only checks
        whether the selected path is a Git worktree, whether HEAD exists, and
        whether the fixed local-gitea remote is already configured.
        """

        repo = self.repo_path(repo_dir)
        top = self._run_git(repo, ["rev-parse", "--show-toplevel"], check=False)
        if top["returncode"] != 0:
            return {
                "ok": True,
                "repo_dir": str(repo),
                "is_git_repo": False,
                "has_head": False,
                "git_root": "",
                "remote": self.GIT_SERVER_LOCAL_REMOTE_NAME,
                "target": {
                    "remote": self.GIT_SERVER_LOCAL_REMOTE_NAME,
                    "owner": self.GIT_SERVER_LOCAL_USER,
                    "repo": "",
                    "protocol": "http",
                    "url": "",
                    "source": "not-a-git-repo",
                    "saved": False,
                    "configurable": False,
                    "pushable": False,
                },
                "reason": "git-init-required",
                "error": top["stderr"] or "Selected project is not inside a git worktree.",
            }

        git_root = Path(top["stdout"].strip()).resolve()
        head = self._run_git(git_root, ["rev-parse", "--verify", "HEAD"], check=False)
        remotes = self.git_remotes(git_root)
        detected = self._local_gitea_target_from_remotes(remotes)
        if detected:
            target = detected
        else:
            repo_name = self._clean_git_server_remote_segment(git_root.name, "repository")
            target = {
                "remote": self.GIT_SERVER_LOCAL_REMOTE_NAME,
                "owner": self.GIT_SERVER_LOCAL_USER,
                "repo": repo_name,
                "protocol": "http",
                "url": self.git_server_remote_url(
                    owner=self.GIT_SERVER_LOCAL_USER,
                    repo_name=repo_name,
                    protocol="http",
                ),
                "source": "suggested-from-git-root",
                "saved": False,
                "configurable": True,
            }
        target["pushable"] = head["returncode"] == 0
        return {
            "ok": True,
            "repo_dir": str(repo),
            "is_git_repo": True,
            "has_head": head["returncode"] == 0,
            "git_root": str(git_root),
            "remote": self.GIT_SERVER_LOCAL_REMOTE_NAME,
            "remotes": remotes,
            "target": target,
        }

    def configure_git_server_remote(
        self,
        *,
        repo_dir: str | None = ".",
        remote: str = "origin",
        owner: str = "local",
        repo_name: str = "",
        protocol: str = "http",
    ) -> dict[str, Any]:
        repo = self.repo_path(repo_dir)
        remote_name = self._clean_git_remote_name(remote)
        resolved_repo_name = repo_name or repo.name
        url = self.git_server_remote_url(owner=owner, repo_name=resolved_repo_name, protocol=protocol)

        git_root, git_init = self._ensure_git_root_for_remote_setup(repo)
        if git_root is None:
            return {
                "ok": False,
                "error": git_init.get("error") or "Directory is not inside a git worktree and could not be initialized.",
                "repo_dir": str(repo),
                "remote": remote_name,
                "url": url,
                "result": git_init.get("top"),
                "git_init": git_init,
            }
        existing = self._run_git(git_root, ["remote", "get-url", remote_name], check=False)
        action = "set-url" if existing["returncode"] == 0 else "add"
        args = ["remote", "set-url", remote_name, url] if action == "set-url" else ["remote", "add", remote_name, url]
        result = self._run_git(git_root, args, check=False)
        remotes = self._run_git(git_root, ["remote", "-v"], check=False)

        payload = {
            "ok": result["returncode"] == 0,
            "action": action,
            "remote": remote_name,
            "owner": self._clean_git_server_remote_segment(owner, "owner"),
            "repo": self._clean_git_server_remote_segment(resolved_repo_name, "repository"),
            "protocol": str(protocol or "http").strip().lower(),
            "url": url,
            "repo_dir": str(repo),
            "git_root": str(git_root),
            "command": result["command"],
            "result": result,
            "remotes": remotes,
        }
        if result["returncode"] != 0:
            payload["error"] = result["stderr"] or result["stdout"] or "Git remote configuration failed."
        return payload

    def git_remotes(self, repo_dir: str | Path | None = ".") -> list[dict[str, str]]:
        repo = self.repo_path(str(repo_dir or "."))
        top = self._run_git(repo, ["rev-parse", "--show-toplevel"], check=False)
        if top["returncode"] != 0:
            return []
        git_root = Path(top["stdout"].strip()).resolve()
        result = self._run_git(git_root, ["remote", "-v"], check=False)
        remotes: dict[tuple[str, str], str] = {}
        for line in result["stdout"].splitlines():
            match = re.match(r"^(\S+)\s+(\S+)\s+\((fetch|push)\)$", line.strip())
            if not match:
                continue
            name, url, direction = match.groups()
            remotes[(name, direction)] = url
        names = sorted({name for name, _direction in remotes})
        return [
            {
                "name": name,
                "fetch": remotes.get((name, "fetch"), ""),
                "push": remotes.get((name, "push"), ""),
            }
            for name in names
        ]

    def setup_local_git_server(
        self,
        *,
        repo_dir: str | None = ".",
        remote: str = "local-gitea",
        owner: str = "local",
        repo_name: str = "",
        protocol: str = "http",
        switch_origin: bool = False,
    ) -> dict[str, Any]:
        """Make the standalone shared local Gitea stack ready for this repository.

        This starts or verifies the standalone machine-wide Gitea Docker stack, ensures the local Gitea user exists,
        creates the matching Gitea repository when absent, and configures a
        clean local git remote. It deliberately does not store a password or
        token in `.git/config`; push uses a temporary auth header instead.
        """

        repo = self.repo_path(repo_dir)
        resolved_repo_name = repo_name or repo.name
        remote_name = self._clean_git_remote_name("origin" if switch_origin else self.GIT_SERVER_LOCAL_REMOTE_NAME)
        owner_name = self._clean_git_server_remote_segment(owner or self.GIT_SERVER_LOCAL_USER, "owner")
        clean_repo_name = self._clean_git_server_remote_segment(resolved_repo_name, "repository")
        steps: list[dict[str, Any]] = []

        base_status = self._git_server_base_status()
        target_prefunk = self.git_server_target_prefunk(str(repo))
        steps.append({"name": "prefunk-local-gitea-target", **self._redact_step(target_prefunk)})
        if not target_prefunk.get("is_git_repo"):
            return {
                **base_status,
                "ok": False,
                "reason": target_prefunk.get("reason", "git-init-required"),
                "error": "Selected project is not a Git repository yet. Initialize Git or select a Git repo before configuring Local Gitea.",
                "repo_dir": str(repo),
                "target": target_prefunk.get("target"),
                "steps": steps,
            }
        if not base_status["compose_file_exists"]:
            return {**base_status, "ok": False, "error": "docker-compose.gitea.yml is missing.", "steps": steps}
        if not base_status["docker_available"]:
            return {**self._docker_unavailable_payload(base_status, action="setup-local"), "steps": steps}

        start = self._run_docker_compose(
            ["up", "-d", self.GIT_SERVER_SERVICE],
            allow_failure=True,
            timeout=120,
        )
        steps.append({"name": "start-git-server", "ok": start["returncode"] == 0, "result": start})
        if start["returncode"] != 0:
            return {
                **base_status,
                "ok": False,
                "error": start["stderr"] or start["stdout"] or "Could not start the local Git server.",
                "steps": steps,
            }

        wait = self._wait_for_gitea_http(timeout_s=90)
        steps.append({"name": "wait-for-gitea", **wait})
        if not wait.get("ok"):
            self._operation_log(
                "Gitea did not become HTTP-ready.",
                {
                    "reason": wait.get("reason"),
                    "error": wait.get("error"),
                    "attempts": wait.get("attempts"),
                    "ps_returncode": (wait.get("ps") or {}).get("returncode") if isinstance(wait.get("ps"), dict) else None,
                    "logs_returncode": (wait.get("logs") or {}).get("returncode") if isinstance(wait.get("logs"), dict) else None,
                },
            )
            return {
                **self.git_server_status(),
                "ok": False,
                "error": wait.get("error", "Gitea did not become reachable."),
                "reason": wait.get("reason", "gitea-not-ready"),
                "diagnostics": {
                    "ps": wait.get("ps"),
                    "logs": wait.get("logs"),
                    "next_actions": wait.get("next_actions", []),
                },
                "steps": steps,
            }

        user = self._ensure_gitea_local_user()
        steps.append({"name": "ensure-local-user", **self._redact_step(user)})
        if not user.get("ok"):
            return {
                **self.git_server_status(),
                "ok": False,
                "error": user.get("error", "Could not ensure the local Gitea user."),
                "steps": steps,
            }

        token = self._create_gitea_access_token(self.GIT_SERVER_LOCAL_USER)
        steps.append({"name": "create-api-token", **self._redact_step(token)})
        if not token.get("ok"):
            return {
                **self.git_server_status(),
                "ok": False,
                "error": token.get("error", "Could not create a temporary Gitea API token."),
                "steps": steps,
            }

        repo_result = self._ensure_gitea_repo(
            token=str(token["token"]),
            owner=owner_name,
            repo_name=clean_repo_name,
        )
        steps.append({"name": "ensure-gitea-repo", **self._redact_step(repo_result)})
        if not repo_result.get("ok"):
            return {
                **self.git_server_status(),
                "ok": False,
                "error": repo_result.get("error", "Could not create or verify the Gitea repository."),
                "steps": steps,
            }

        remote_result = self.configure_git_server_remote(
            repo_dir=str(repo),
            remote=remote_name,
            owner=owner_name,
            repo_name=clean_repo_name,
            protocol=protocol,
        )
        steps.append({"name": "configure-local-remote", **self._redact_step(remote_result)})

        payload = {
            **self.git_server_status(),
            "ok": bool(remote_result.get("ok")),
            "mode": "switch-origin" if switch_origin else "add-local-remote",
            "action": remote_result.get("action"),
            "remote": remote_result.get("remote", remote_name),
            "owner": owner_name,
            "repo": clean_repo_name,
            "protocol": protocol,
            "url": remote_result.get("url") or self.git_server_remote_url(
                owner=owner_name,
                repo_name=clean_repo_name,
                protocol=protocol,
            ),
            "repo_created": repo_result.get("created", False),
            "repo_exists": repo_result.get("exists", False),
            "repo_web_url": f"{self.GIT_SERVER_WEB_URL.rstrip('/')}/{owner_name}/{clean_repo_name}",
            "git_root": remote_result.get("git_root"),
            "remotes": remote_result.get("remotes"),
            "target": self.git_server_target_prefunk(str(repo)).get("target"),
            "steps": steps,
            "credentials": {
                "username": self.GIT_SERVER_LOCAL_USER,
                "password_env": self.GIT_SERVER_LOCAL_PASSWORD_ENV,
                "password_default": self.GIT_SERVER_LOCAL_PASSWORD_DEFAULT,
                "note": "Local development credentials only; push actions use a temporary API token and do not store it in the remote URL.",
            },
        }
        if not remote_result.get("ok"):
            payload["error"] = remote_result.get("error", "Local git remote configuration failed.")
        return payload

    def push_local_git_server(
        self,
        *,
        repo_dir: str | None = ".",
        remote: str = "local-gitea",
        owner: str = "local",
        repo_name: str = "",
        protocol: str = "http",
        switch_origin: bool = False,
    ) -> dict[str, Any]:
        target_prefunk = self.git_server_target_prefunk(repo_dir)
        if not target_prefunk.get("is_git_repo"):
            return {
                **self._git_server_base_status(),
                "ok": False,
                "reason": target_prefunk.get("reason", "git-init-required"),
                "error": "Selected project is not a Git repository yet. Initialize Git or select a Git repo before pushing to Local Gitea.",
                "repo_dir": str(self.repo_path(repo_dir)),
                "target": target_prefunk.get("target"),
                "push": None,
            }
        if not target_prefunk.get("has_head"):
            return {
                **self._git_server_base_status(),
                "ok": False,
                "reason": "initial-snapshot-required",
                "error": "Selected Git repository has no HEAD yet. Create an initial commit before pushing to Local Gitea.",
                "repo_dir": str(self.repo_path(repo_dir)),
                "git_root": target_prefunk.get("git_root"),
                "target": target_prefunk.get("target"),
                "push": None,
            }

        setup = self.setup_local_git_server(
            repo_dir=repo_dir,
            remote=remote,
            owner=owner,
            repo_name=repo_name,
            protocol=protocol,
            switch_origin=switch_origin,
        )
        if not setup.get("ok"):
            return {**setup, "push": None}

        if str(protocol or "http").strip().lower() != "http":
            return {
                **setup,
                "ok": False,
                "error": "Automatic push uses the HTTP local Gitea remote so no secret is stored in .git/config. Choose HTTP protocol.",
                "push": None,
            }

        repo = self.repo_path(repo_dir)
        top = self._run_git(repo, ["rev-parse", "--show-toplevel"], check=False)
        if top["returncode"] != 0:
            return {**setup, "ok": False, "error": top["stderr"] or "Directory is not inside a git worktree.", "push": top}

        token = self._create_gitea_access_token(self.GIT_SERVER_LOCAL_USER)
        if not token.get("ok"):
            return {**setup, "ok": False, "error": token.get("error", "Could not create temporary push token."), "push": None}

        git_root = Path(top["stdout"].strip()).resolve()
        auth = base64.b64encode(f"{self.GIT_SERVER_LOCAL_USER}:{token['token']}".encode("utf-8")).decode("ascii")
        remote_name = self._clean_git_remote_name(setup.get("remote") or ("origin" if switch_origin else remote))
        push_result = self._run_command(
            [
                "git",
                "-c",
                f"http.extraHeader=Authorization: Basic {auth}",
                "push",
                "-u",
                remote_name,
                "HEAD",
            ],
            cwd=git_root,
            timeout=180,
        )
        push_result["command"] = ["git", "-c", "http.extraHeader=Authorization: Basic <redacted>", "push", "-u", remote_name, "HEAD"]
        return {
            **setup,
            "ok": push_result["returncode"] == 0,
            "push": push_result,
            "error": "" if push_result["returncode"] == 0 else (push_result["stderr"] or push_result["stdout"] or "Git push to local Gitea failed."),
        }

    def configure_external_git_remote(
        self,
        *,
        repo_dir: str | None = ".",
        remote: str = "origin",
        url: str = "",
        add_if_missing: bool = True,
    ) -> dict[str, Any]:
        repo = self.repo_path(repo_dir)
        remote_name = self._clean_git_remote_name(remote)
        clean_url = self._clean_external_git_url(url)
        top = self._run_git(repo, ["rev-parse", "--show-toplevel"], check=False)
        if top["returncode"] != 0:
            return {
                "ok": False,
                "error": top["stderr"] or "Directory is not inside a git worktree.",
                "repo_dir": str(repo),
                "remote": remote_name,
                "url": clean_url,
                "result": top,
            }

        git_root = Path(top["stdout"].strip()).resolve()
        existing = self._run_git(git_root, ["remote", "get-url", remote_name], check=False)
        action = "set-url" if existing["returncode"] == 0 else "add"
        if action == "add" and not add_if_missing:
            return {
                "ok": False,
                "error": f"Remote {remote_name!r} does not exist.",
                "repo_dir": str(repo),
                "git_root": str(git_root),
                "remote": remote_name,
                "url": clean_url,
            }
        args = ["remote", "set-url", remote_name, clean_url] if action == "set-url" else ["remote", "add", remote_name, clean_url]
        result = self._run_git(git_root, args, check=False)
        remotes = self._run_git(git_root, ["remote", "-v"], check=False)
        return {
            "ok": result["returncode"] == 0,
            "action": action,
            "remote": remote_name,
            "url": clean_url,
            "repo_dir": str(repo),
            "git_root": str(git_root),
            "command": result["command"],
            "result": result,
            "remotes": remotes,
            "error": "" if result["returncode"] == 0 else (result["stderr"] or result["stdout"] or "External remote configuration failed."),
        }

    def plan_gitea_push_mirror(
        self,
        *,
        owner: str = "local",
        repo_name: str = "",
        external_url: str = "",
        external_username: str = "",
    ) -> dict[str, Any]:
        clean_owner = self._clean_git_server_remote_segment(owner or self.GIT_SERVER_LOCAL_USER, "owner")
        clean_repo = self._clean_git_server_remote_segment(repo_name or self.repo_root.name, "repository")
        clean_url = self._clean_external_git_url(external_url)
        username = str(external_username or "").strip()
        return {
            "ok": True,
            "mode": "gitea-push-mirror-plan",
            "owner": clean_owner,
            "repo": clean_repo,
            "repo_web_url": f"{self.GIT_SERVER_WEB_URL.rstrip('/')}/{clean_owner}/{clean_repo}",
            "external_url": clean_url,
            "external_username": username,
            "warning": "Gitea push mirrors require destination credentials and may force-push to the external repository. Use a dedicated external personal access token.",
            "steps": [
                "Use Set Up Local Server or Push to Local Server first so the local Gitea repository exists.",
                "Enter the external HTTPS repository URL.",
                "Enter the external service username and personal access token/password.",
                "Click Set Up Server → External Mirror to create the push mirror from the Git page.",
            ],
        }

    def setup_gitea_push_mirror(
        self,
        *,
        owner: str = "local",
        repo_name: str = "",
        external_url: str = "",
        external_username: str = "",
        external_password: str = "",
        interval: str = "8h",
        sync_on_commit: bool = True,
    ) -> dict[str, Any]:
        clean_owner = self._clean_git_server_remote_segment(owner or self.GIT_SERVER_LOCAL_USER, "owner")
        clean_repo = self._clean_git_server_remote_segment(repo_name or self.repo_root.name, "repository")
        clean_url = self._clean_external_git_url(external_url)
        parsed = urllib.parse.urlsplit(clean_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Automatic Gitea push mirror setup requires an HTTP or HTTPS external URL.")
        clean_username = str(external_username or "").strip()
        clean_password = str(external_password or "")
        if not clean_password:
            raise ValueError("External mirror token/password is required to set up a server push mirror.")
        clean_interval = str(interval or "8h").strip() or "8h"

        setup = self.setup_local_git_server(
            repo_dir=".",
            remote=self.GIT_SERVER_LOCAL_REMOTE_NAME,
            owner=clean_owner,
            repo_name=clean_repo,
            protocol="http",
            switch_origin=False,
        )
        steps: list[dict[str, Any]] = [{"name": "setup-local-gitea-repo", **self._redact_step(setup)}]
        if not setup.get("ok"):
            return {**setup, "ok": False, "mode": "gitea-push-mirror-setup", "steps": steps}

        token = self._create_gitea_access_token(self.GIT_SERVER_LOCAL_USER)
        steps.append({"name": "create-api-token", **self._redact_step(token)})
        if not token.get("ok"):
            return {
                **setup,
                "ok": False,
                "mode": "gitea-push-mirror-setup",
                "error": token.get("error", "Could not create a temporary Gitea API token."),
                "steps": steps,
            }

        owner_q = urllib.parse.quote(clean_owner, safe="")
        repo_q = urllib.parse.quote(clean_repo, safe="")
        headers = {"Authorization": f"token {token['token']}"}
        list_response = self._gitea_json_request(
            "GET",
            f"/api/v1/repos/{owner_q}/{repo_q}/push_mirrors",
            headers=headers,
            allow_statuses={200, 404},
        )
        steps.append({"name": "list-push-mirrors", **self._redact_step(list_response)})
        mirrors = list_response.get("json") if list_response.get("status") == 200 else []
        if isinstance(mirrors, list):
            for mirror in mirrors:
                if isinstance(mirror, dict) and str(mirror.get("remote_address") or "") == clean_url:
                    return {
                        **setup,
                        "ok": True,
                        "mode": "gitea-push-mirror-setup",
                        "mirror_exists": True,
                        "mirror_created": False,
                        "owner": clean_owner,
                        "repo": clean_repo,
                        "external_url": clean_url,
                        "external_username": clean_username,
                        "steps": steps,
                        "warning": "Existing Gitea push mirror found. Push mirrors may force-push to the external repository.",
                    }

        create_payload = {
            "remote_address": clean_url,
            "remote_username": clean_username,
            "remote_password": clean_password,
            "interval": clean_interval,
            "sync_on_commit": bool(sync_on_commit),
        }
        create = self._gitea_json_request(
            "POST",
            f"/api/v1/repos/{owner_q}/{repo_q}/push_mirrors",
            payload=create_payload,
            headers=headers,
            allow_statuses={200, 201, 204, 409, 422},
        )
        steps.append({"name": "create-push-mirror", **self._redact_step(create)})
        ok = create.get("status") in {200, 201, 204}
        if not ok and create.get("status") in {409, 422}:
            verify = self._gitea_json_request(
                "GET",
                f"/api/v1/repos/{owner_q}/{repo_q}/push_mirrors",
                headers=headers,
                allow_statuses={200, 404},
            )
            steps.append({"name": "verify-push-mirror", **self._redact_step(verify)})
            verify_mirrors = verify.get("json") if verify.get("status") == 200 else []
            ok = isinstance(verify_mirrors, list) and any(
                isinstance(mirror, dict) and str(mirror.get("remote_address") or "") == clean_url
                for mirror in verify_mirrors
            )

        payload = {
            **setup,
            "ok": ok,
            "mode": "gitea-push-mirror-setup",
            "mirror_exists": ok and create.get("status") not in {200, 201, 204},
            "mirror_created": create.get("status") in {200, 201, 204},
            "owner": clean_owner,
            "repo": clean_repo,
            "external_url": clean_url,
            "external_username": clean_username,
            "interval": clean_interval,
            "sync_on_commit": bool(sync_on_commit),
            "steps": steps,
            "warning": "Gitea push mirrors may force-push to the external repository. The external credential is sent to local Gitea for the mirror configuration; it is not stored in .git/config.",
        }
        if not ok:
            payload["error"] = create.get("error") or "Could not create or verify the Gitea push mirror."
        return self._redact_step(payload)

    def git_projects(self) -> dict[str, Any]:
        registry = self._load_git_project_registry()
        self._ensure_main_project_record(registry)
        current = self._current_project_record(registry)
        return {
            "ok": True,
            "app_root": str(self.repo_root),
            "current_project_id": registry.get("current_project_id"),
            "projects": registry.get("projects", []),
            "archived_projects": registry.get("archived_projects", []),
            "current_project": current,
        }

    def add_git_project(self, path: str, *, name: str = "", select: bool = True) -> dict[str, Any]:
        project_path = self.repo_path(path)
        registry = self._load_git_project_registry()
        self._ensure_main_project_record(registry)
        record = self._project_record_for_path(project_path, name=name)
        existing = self._find_project_record(registry, project_id=record["id"], path=str(project_path))
        if existing:
            existing.update({k: v for k, v in record.items() if k not in {"id", "locked", "archived", "selected"}})
            record = {**existing, "archived": False}
        else:
            record = {**record, "archived": False}
        registry["archived_projects"] = self._remove_matching_project_records(registry.get("archived_projects", []), record)
        projects = self._remove_matching_project_records(registry.get("projects", []), record)
        projects.append(record)
        registry["projects"] = projects
        if select:
            registry["current_project_id"] = record["id"]
        self._save_git_project_registry(registry)
        return {**self.git_projects(), "project": record}

    def select_git_project(self, project_id: str = "", path: str = "") -> dict[str, Any]:
        registry = self._load_git_project_registry()
        self._ensure_main_project_record(registry)
        record = self._find_project_record(registry, project_id=project_id, path=path)
        if not record:
            raise ValueError("Selected project is not registered.")
        if record.get("archived"):
            raise ValueError("Archived projects must be restored before selection.")
        registry["current_project_id"] = record["id"]
        self._save_git_project_registry(registry)
        return {**self.git_projects(), "project": record}

    def archive_git_project(self, project_id: str = "", path: str = "") -> dict[str, Any]:
        registry = self._load_git_project_registry()
        self._ensure_main_project_record(registry)
        record = self._find_project_record(registry, project_id=project_id, path=path)
        if not record:
            raise ValueError("Project is not registered.")
        if record.get("vip") or not record.get("can_archive", True):
            raise ValueError("The Main Computer project is VIP and cannot be archived.")
        registry["projects"] = [item for item in registry.get("projects", []) if item.get("id") != record.get("id")]
        record = {**record, "archived": True, "selected": False}
        if not any(item.get("id") == record["id"] for item in registry.get("archived_projects", [])):
            registry.setdefault("archived_projects", []).append(record)
        if registry.get("current_project_id") == record.get("id"):
            fallback = self._find_project_record(registry, project_id="default-mct-worktree")
            registry["current_project_id"] = fallback["id"] if fallback and fallback.get("id") != record.get("id") else "main-computer"
        self._save_git_project_registry(registry)
        return {**self.git_projects(), "project": record}

    def restore_git_project(self, project_id: str = "", path: str = "", *, select: bool = False) -> dict[str, Any]:
        registry = self._load_git_project_registry()
        self._ensure_main_project_record(registry)
        record = self._find_project_record(registry, project_id=project_id, path=path)
        if not record:
            raise ValueError("Archived project is not registered.")
        record = {**record, "archived": False}
        record.pop("selected", None)
        registry["archived_projects"] = self._remove_matching_project_records(registry.get("archived_projects", []), record)
        projects = self._remove_matching_project_records(registry.get("projects", []), record)
        projects.append(record)
        registry["projects"] = projects
        if select:
            registry["current_project_id"] = record["id"]
        self._save_git_project_registry(registry)
        return {**self.git_projects(), "project": record}

    def lock_git_project(self, project_id: str = "", path: str = "", *, locked: bool = True) -> dict[str, Any]:
        registry = self._load_git_project_registry()
        self._ensure_main_project_record(registry)
        record = self._find_project_record(registry, project_id=project_id, path=path)
        if not record:
            raise ValueError("Project is not registered.")
        record["locked"] = bool(locked)
        self._save_git_project_registry(registry)
        return {**self.git_projects(), "project": record}

    def inspect_git_project(self, project_id: str = "", path: str = "") -> dict[str, Any]:
        registry = self._load_git_project_registry()
        self._ensure_main_project_record(registry)
        record = self._find_project_record(registry, project_id=project_id, path=path) or self._current_project_record(registry)
        if not record:
            raise ValueError("No project is selected.")
        project_path = self.repo_path(record["path"])
        git = self._inspect_git_project_state(project_path)
        dirty_plan = self._run_dirty_plan(project_path)
        wizard = self._wizard_from_dirty_plan(dirty_plan, git=git, project=record)
        blocking = []
        if record.get("locked"):
            blocking.append({"action": "mutating-git-actions", "reason": "project-locked"})
        if git.get("is_git_repo") and not git.get("has_head"):
            blocking.append({"action": "push-local-server", "reason": "initial-snapshot-required"})
        elif not git.get("is_git_repo"):
            blocking.append({"action": "push-local-server", "reason": "git-init-required"})
        inspection = {
            "ok": True,
            "app_root": str(self.repo_root),
            "project": record,
            "selected_project": str(project_path),
            "is_main_computer_project": bool(record.get("vip")),
            "self_project": bool(record.get("vip")),
            "locked": bool(record.get("locked")),
            "archived": bool(record.get("archived")),
            "git": git,
            "dirty_plan": dirty_plan,
            "wizard": wizard,
            "blocking": blocking,
        }
        record["last_inspection"] = self._project_inspection_summary(inspection)
        self._save_git_project_registry(registry)
        return inspection

    def _project_registry_path(self) -> Path:
        git_dir = self.repo_root / ".git"
        if git_dir.exists() and git_dir.is_dir():
            return git_dir / "git-tools" / "projects.json"
        return self.repo_root / ".main-computer" / "git-tools-projects.json"

    def _load_git_project_registry(self) -> dict[str, Any]:
        path = self._project_registry_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        else:
            data = {}
        data.setdefault("projects", [])
        data.setdefault("archived_projects", [])
        return data

    def _save_git_project_registry(self, registry: dict[str, Any]) -> None:
        path = self._project_registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(registry, indent=2, sort_keys=True), encoding="utf-8")

    def _ensure_main_project_record(self, registry: dict[str, Any]) -> dict[str, Any]:
        main = self._main_project_record()
        projects = registry.setdefault("projects", [])
        existing = next((item for item in projects if item.get("id") == main["id"]), None)
        if existing:
            existing.update({**main, "locked": bool(existing.get("locked", True))})
            main = existing
        else:
            projects.insert(0, main)
        self._ensure_default_work_project_record(registry)
        active_ids = {str(item.get("id") or "") for item in registry.get("projects", [])}
        if str(registry.get("current_project_id") or "") not in active_ids:
            registry["current_project_id"] = "default-mct-worktree" if "default-mct-worktree" in active_ids else main["id"]
        return main

    def _default_work_project_record(self) -> dict[str, Any]:
        default_path = Path(os.environ.get("MAIN_COMPUTER_DEFAULT_MCT_WORKTREE") or (Path.home() / "mct"))
        return {
            "id": "default-mct-worktree",
            "name": "mct",
            "path": str(default_path),
            "kind": "external",
            "vip": False,
            "locked": False,
            "archived": False,
            "can_archive": True,
            "description": "Default editable copy for Git Tools work.",
        }

    def _ensure_default_work_project_record(self, registry: dict[str, Any]) -> dict[str, Any]:
        default = self._default_work_project_record()
        projects = registry.setdefault("projects", [])
        archived_default = next(
            (
                item
                for item in registry.setdefault("archived_projects", [])
                if self._same_project_record_identity(item, default)
            ),
            None,
        )
        if archived_default:
            registry["projects"] = self._remove_matching_project_records(projects, default)
            return archived_default
        existing = next((item for item in projects if item.get("id") == default["id"]), None)
        if existing:
            existing.update({**default, "locked": bool(existing.get("locked", False))})
            default = existing
        elif not any(self._same_path_string(str(item.get("path", "")), default["path"]) for item in projects):
            insert_at = 1 if projects and projects[0].get("id") == "main-computer" else len(projects)
            projects.insert(insert_at, default)
        return default

    def _main_project_record(self) -> dict[str, Any]:
        return {
            "id": "main-computer",
            "name": self.repo_root.name,
            "path": str(self.repo_root),
            "kind": "main-computer",
            "vip": True,
            "locked": True,
            "archived": False,
            "can_archive": False,
            "description": "Main Computer project. VIP project cannot be archived and starts locked.",
        }

    def _project_record_for_path(self, path: Path, *, name: str = "") -> dict[str, Any]:
        resolved = path.resolve()
        is_main = self._same_path(resolved, self.repo_root)
        if is_main:
            return self._main_project_record()
        digest = hashlib.sha1(str(resolved).lower().encode("utf-8")).hexdigest()[:16]
        return {
            "id": f"project-{digest}",
            "name": name.strip() or resolved.name,
            "path": str(resolved),
            "kind": "external",
            "vip": False,
            "locked": False,
            "archived": False,
            "can_archive": True,
            "description": "",
        }

    def _same_path_string(self, left: str, right: str) -> bool:
        def normalize(value: str) -> str:
            return str(value or "").replace("/", "\\").rstrip("\\").lower()
        return normalize(left) == normalize(right)

    def _same_path(self, left: Path, right: Path) -> bool:
        try:
            return left.resolve() == right.resolve()
        except Exception:
            return self._same_path_string(str(left), str(right))

    def _same_project_record_identity(self, item: dict[str, Any], record: dict[str, Any]) -> bool:
        left_id = str(item.get("id") or "")
        right_id = str(record.get("id") or "")
        if left_id and right_id and left_id == right_id:
            return True
        left_path = str(item.get("path") or "")
        right_path = str(record.get("path") or "")
        return bool(left_path and right_path and self._same_path_string(left_path, right_path))

    def _remove_matching_project_records(self, records: list[dict[str, Any]], record: dict[str, Any]) -> list[dict[str, Any]]:
        return [item for item in records if not self._same_project_record_identity(item, record)]

    def _find_project_record(self, registry: dict[str, Any], *, project_id: str = "", path: str = "") -> dict[str, Any] | None:
        candidates = list(registry.get("projects", [])) + list(registry.get("archived_projects", []))
        if project_id:
            found = next((item for item in candidates if item.get("id") == project_id), None)
            if found:
                return found
        if path:
            try:
                resolved = self.repo_path(path)
            except Exception:
                resolved = Path(path)
            for item in candidates:
                try:
                    if self._same_path(Path(str(item.get("path", ""))), resolved):
                        return item
                except Exception:
                    continue
        return None

    def _current_project_record(self, registry: dict[str, Any]) -> dict[str, Any] | None:
        current_id = str(registry.get("current_project_id") or "default-mct-worktree")
        projects = list(registry.get("projects", []))
        current = next((item for item in projects if item.get("id") == current_id), None)
        if current:
            return current
        default = next((item for item in projects if item.get("id") == "default-mct-worktree"), None)
        if default:
            return default
        main = next((item for item in projects if item.get("id") == "main-computer"), None)
        return main or self._ensure_main_project_record(registry)

    def _selected_git_metadata_kind(self, path: Path) -> str:
        marker = path / ".git"
        if marker.is_dir():
            return "directory"
        if marker.is_file():
            return "file"
        if marker.exists():
            return "other"
        return "missing"

    def _inspect_git_project_state(self, path: Path) -> dict[str, Any]:
        selected = path.resolve()
        metadata_kind = self._selected_git_metadata_kind(selected)
        has_local_metadata = metadata_kind in {"directory", "file"}
        top = self._run_git(selected, ["rev-parse", "--show-toplevel"], check=False)
        if top["returncode"] != 0:
            return {
                "is_git_repo": False,
                "has_head": False,
                "git_root": "",
                "branch": "",
                "error": top["stderr"] or top["stdout"] or "Selected folder is not a Git repository.",
                "selected_path_has_git_metadata": has_local_metadata,
                "selected_path_git_metadata_kind": metadata_kind,
                "current_dir_is_git_repo_root": False,
                "input_inside_parent_repo": False,
                "parent_git_root": "",
                "raw": {"top": top},
            }
        git_root = Path(top["stdout"].strip()).resolve()
        current_dir_is_git_repo_root = selected == git_root
        if not current_dir_is_git_repo_root or not has_local_metadata:
            return {
                "is_git_repo": False,
                "has_head": False,
                "git_root": "",
                "branch": "",
                "error": "Selected folder has no .git directory or .git file; initialize this folder before creating HEAD.",
                "selected_path_has_git_metadata": has_local_metadata,
                "selected_path_git_metadata_kind": metadata_kind,
                "current_dir_is_git_repo_root": False,
                "input_inside_parent_repo": not current_dir_is_git_repo_root,
                "parent_git_root": str(git_root),
                "raw": {"top": top},
            }
        head = self._run_git(git_root, ["rev-parse", "--verify", "HEAD"], check=False)
        branch = self._run_git(git_root, ["branch", "--show-current"], check=False)
        remotes = self._run_git(git_root, ["remote", "-v"], check=False)
        status = self._run_git(git_root, ["status", "--short", "--branch"], check=False)
        return {
            "is_git_repo": True,
            "has_head": head["returncode"] == 0,
            "git_root": str(git_root),
            "branch": branch["stdout"].strip() or ("unknown" if head["returncode"] == 0 else ""),
            "head": head["stdout"].strip() if head["returncode"] == 0 else "",
            "remotes_text": remotes["stdout"],
            "short_status": status["stdout"],
            "selected_path_has_git_metadata": has_local_metadata,
            "selected_path_git_metadata_kind": metadata_kind,
            "current_dir_is_git_repo_root": True,
            "input_inside_parent_repo": False,
            "parent_git_root": "",
            "raw": {"top": top, "head": head, "branch": branch, "remotes": remotes, "status": status},
        }

    def _read_gitignore_file(self, repo: Path) -> dict[str, Any]:
        import git_dirty as dirty

        payload = dirty.read_gitignore_file(Path(repo))
        payload["ok"] = not bool(payload.get("error"))
        payload["relative_path"] = payload.get("path", ".gitignore")
        return payload

    def save_project_gitignore(
        self,
        *,
        project_id: str = "",
        path: str = "",
        gitignore_path: str = ".gitignore",
        lines: Any = None,
        newline: str = "existing",
    ) -> dict[str, Any]:
        import git_dirty as dirty

        registry = self._load_git_project_registry()
        self._ensure_main_project_record(registry)
        record = self._find_project_record(registry, project_id=project_id, path=path) or self._current_project_record(registry)
        if not record:
            raise ValueError("No project is selected.")
        if record.get("archived"):
            raise ValueError("Archived projects must be restored before saving .gitignore.")
        if record.get("locked"):
            raise ValueError("Unlock the selected project before saving .gitignore.")
        project_path = self.repo_path(str(record.get("path") or "."))
        git = self._inspect_git_project_state(project_path)
        if git.get("input_inside_parent_repo"):
            raise ValueError("Refusing to save .gitignore for a folder inside a different parent Git repository.")
        project_root = Path(git.get("git_root") or project_path).resolve()
        if project_root != project_path.resolve():
            raise ValueError("Selected project path must be the Git repository root before saving .gitignore.")
        payload = dirty.write_gitignore_file(
            project_root,
            [] if lines is None else lines,
            path=gitignore_path,
            newline=newline,
        )
        payload["ok"] = not bool(payload.get("error"))
        payload["relative_path"] = payload.get("path", ".gitignore")
        return {
            "ok": payload["ok"],
            "project": record,
            "gitignore_file": payload,
        }

    def _run_dirty_plan(self, path: Path) -> dict[str, Any]:
        script = self.repo_root / "git_dirty.py"
        if not script.exists():
            return {"ok": False, "error": "git_dirty.py is not available.", "steps": []}
        result = self._run_command(
            [sys.executable, str(script), "plan", "--repo", str(path), "--json", "--include-actions"],
            cwd=self.repo_root,
            timeout=60,
            not_found_stderr="Python executable is not available.",
        )
        if result["returncode"] != 0:
            return {"ok": False, "error": result["stderr"] or result["stdout"] or "git_dirty.py plan failed.", "result": result, "steps": []}
        try:
            payload = json.loads(result["stdout"] or "{}")
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"git_dirty.py returned invalid JSON: {exc}", "result": result, "steps": []}
        payload.setdefault("ok", True)
        payload["command_result"] = result
        return payload

    def _wizard_from_dirty_plan(self, dirty_plan: dict[str, Any], *, git: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
        steps = []
        planner_head_fix_ids = {
            "initialize_repository_here",
            "update_gitignore_before_initial_commit",
            "secrets_filter",
            "create_initial_snapshot",
            "prepare_commit_snapshot",
        }
        has_planner_head_fix_step = False
        for item in dirty_plan.get("steps") or []:
            commands = []
            for command in item.get("commands") or []:
                commands.append({
                    "command": command.get("command", ""),
                    "purpose": command.get("purpose", ""),
                    "template": command.get("template", ""),
                    "locked": bool(command.get("locked")),
                    "requires": command.get("requires", []),
                    "safe": bool(command.get("safe", True)),
                    "destructive": bool(command.get("destructive", False)),
                    "implemented": bool(command.get("implemented", True)),
                })
            item_state = str(item.get("state") or ("locked" if item.get("locked") else "planned"))
            read_only_step = self._dirty_step_is_read_only(item)
            step_payload = {
                "id": item.get("action_id") or item.get("id") or item.get("label", "").lower().replace(" ", "-"),
                "order": item.get("order", len(steps)),
                "label": item.get("label", "Unnamed step"),
                "kind": item.get("kind", item.get("git_name", "")),
                "why": item.get("why", ""),
                "locked": bool(item.get("locked")) or (bool(project.get("locked")) and not read_only_step),
                "requires": item.get("requires", []),
                "paths": item.get("paths", []),
                "commands": commands,
                "state": "blocked" if bool(project.get("locked")) and not read_only_step else item_state,
            }
            for key in (
                "safe_paths",
                "questionable_paths",
                "affected_paths",
                "ignore_rules",
                "questionable_ignore_rules",
                "ignore_rule_groups",
                "source_config_test_candidates",
                "first_commit_candidate_groups",
                "gitignore_file",
                "gitignore_success",
                "secrets_filter",
                "commit_review",
            ):
                if key in item:
                    step_payload[key] = item.get(key)
            if step_payload["id"] in planner_head_fix_ids:
                has_planner_head_fix_step = True
            steps.append(step_payload)
        if git.get("is_git_repo") and not git.get("has_head") and not has_planner_head_fix_step:
            steps.append({
                "id": "initial-snapshot-required",
                "order": len(steps),
                "label": "Review Initial Snapshot",
                "kind": "initial-snapshot",
                "why": "The selected Git repository has no HEAD commit, so push is blocked until an initial snapshot is reviewed and created.",
                "locked": bool(project.get("locked")),
                "requires": ["project-unlocked", "dirty-plan-reviewed"],
                "paths": [],
                "commands": [],
                "state": "blocked",
            })
        steps.append({
            "id": "push-local-gitea",
            "order": len(steps),
            "label": "Push to Local Gitea",
            "kind": "publish",
            "why": "Publish the selected project to the local Gitea remote after prerequisites pass.",
            "locked": bool(project.get("locked")) or not git.get("has_head"),
            "requires": ["has-head", "local-remote-configured"],
            "paths": [],
            "commands": [],
            "state": "blocked" if bool(project.get("locked")) or not git.get("has_head") else "planned",
        })
        return {
            "ok": bool(dirty_plan.get("ok", False)),
            "plan_id": dirty_plan.get("plan_id", ""),
            "strategy": dirty_plan.get("recommended_strategy", ""),
            "dirty_score": dirty_plan.get("dirty_score", 0),
            "level": dirty_plan.get("level", "unknown"),
            "summary": dirty_plan.get("summary", {}),
            "steps": steps,
        }

    def _dirty_step_is_read_only(self, step: dict[str, Any]) -> bool:
        if str(step.get("state") or "") == "completed" or bool(step.get("completed")):
            return True
        action = str(step.get("action_id") or step.get("id") or "")
        if action in {"find_repository_root", "classify_changed_files", "inspect_configured_remotes", "compare_to_remote_state", "show_merge_conflicts"}:
            return True
        commands = step.get("commands") or []
        return bool(commands) and all(command.get("safe", True) and not command.get("destructive", False) and command.get("command_kind") != "git" for command in commands)

    def _project_inspection_summary(self, inspection: dict[str, Any]) -> dict[str, Any]:
        dirty = inspection.get("dirty_plan") or {}
        git = inspection.get("git") or {}
        return {
            "inspected_at": time.time(),
            "is_git_repo": bool(git.get("is_git_repo")),
            "has_head": bool(git.get("has_head")),
            "dirty_score": dirty.get("dirty_score", 0),
            "level": dirty.get("level", "unknown"),
            "summary": dirty.get("summary", {}),
            "blocking": inspection.get("blocking", []),
        }

    def git_status(self, repo_dir: str | None = ".") -> dict[str, Any]:
        repo = self.repo_path(repo_dir)
        top = self._run_git(repo, ["rev-parse", "--show-toplevel"], check=False)
        if top["returncode"] != 0:
            return {
                "ok": False,
                "repo_dir": str(repo),
                "is_git_repo": False,
                "has_head": False,
                "error": top["stderr"] or "Directory is not inside a git worktree.",
                "capabilities": self.capabilities(),
            }

        git_root = Path(top["stdout"].strip()).resolve()
        branch = self._run_git(repo, ["branch", "--show-current"], check=False)
        head = self._run_git(git_root, ["rev-parse", "--verify", "HEAD"], check=False)
        status = self._run_git(repo, ["status", "--short", "--branch"], check=False)
        recent = self._run_git(repo, ["log", "--oneline", "-n", "5"], check=False)
        lines = [line for line in status["stdout"].splitlines() if line.strip()]
        changed = [line for line in lines if not line.startswith("##") and not line.startswith("??")]
        untracked = [line for line in lines if line.startswith("??")]
        ahead, behind = self._ahead_behind(lines[0] if lines and lines[0].startswith("##") else "")

        patching = self.list_patches() if self.patching_available else {
            "ok": False,
            "error": self._patch_import_error or "Patch harness is unavailable.",
            "incoming": [],
            "applied": [],
            "archive": [],
            "dry_runs": [],
            "counts": {"incoming": 0, "applied": 0, "archive": 0, "dry_runs": 0},
        }

        return {
            "ok": True,
            "repo_dir": str(repo),
            "git_root": str(git_root),
            "is_git_repo": True,
            "has_head": head["returncode"] == 0,
            "branch": branch["stdout"].strip() or "detached-or-unknown",
            "ahead": ahead,
            "behind": behind,
            "dirty": bool(changed or untracked),
            "changed_count": len(changed),
            "untracked_count": len(untracked),
            "short_status": status["stdout"],
            "recent_commits": [line for line in recent["stdout"].splitlines() if line.strip()],
            "remotes": self.git_remotes(repo),
            "patching": patching,
            "capabilities": self.capabilities(),
        }

    def list_git_shims(self) -> dict[str, Any]:
        return self._run_git_control(["--list-shims"])

    def read_git_shim(self, shim_id: str) -> dict[str, Any]:
        cleaned = str(shim_id or "").strip()
        if not cleaned:
            raise ValueError("Shim id is required.")
        return self._run_git_control(["--read-shim", cleaned])

    def run_git_shim(self, shim_id: str) -> dict[str, Any]:
        cleaned = str(shim_id or "").strip()
        if not cleaned:
            raise ValueError("Shim id is required.")
        return self._run_git_control(["--run-shim", cleaned], allow_failure=True)

    def delete_git_shim(self, shim_id: str) -> dict[str, Any]:
        cleaned = str(shim_id or "").strip()
        if not cleaned:
            raise ValueError("Shim id is required.")
        return self._run_git_control(["--delete-shim", cleaned])

    def ordain_git_shim(self, shim_id: str) -> dict[str, Any]:
        cleaned = str(shim_id or "").strip()
        if not cleaned:
            raise ValueError("Shim id is required.")
        return self._run_git_control(["--ordain-shim", cleaned])

    def unordain_git_shim(self, shim_id: str) -> dict[str, Any]:
        cleaned = str(shim_id or "").strip()
        if not cleaned:
            raise ValueError("Shim id is required.")
        return self._run_git_control(["--unordain-shim", cleaned])

    def git_ai_brief(self, prompt: str = "") -> dict[str, Any]:
        args = ["--ai-brief"]
        if prompt:
            args.extend(["--prompt", prompt])
        return self._run_git_control(args)

    def ask_git_ai(self, prompt: str, *, chat_callable: Any) -> dict[str, Any]:
        brief = self.git_ai_brief(prompt)
        response = chat_callable(brief["prompt"])
        content = getattr(response, "content", str(response))
        extracted = self.extract_git_console_shims(content)
        return {
            "ok": True,
            "content": content,
            "provider": getattr(response, "provider", ""),
            "model": getattr(response, "model", ""),
            "prompt": brief["prompt"],
            "computed_sum": brief.get("computed_sum"),
            "ordained_context": brief.get("ordained_context"),
            "extracted": extracted,
            "shims": extracted.get("shims", []),
        }

    def extract_git_console_shims(self, ai_output: str) -> dict[str, Any]:
        text = str(ai_output or "")
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".txt") as handle:
            handle.write(text)
            temp_name = handle.name
        try:
            return self._run_git_control(["--extract-shims-from", temp_name])
        finally:
            try:
                os.unlink(temp_name)
            except OSError:
                pass

    def _git_project_action_uses_secrets_filter_runner(self, action_key: str) -> bool:
        value = str(action_key or "")
        if "secrets_filter" not in value:
            return False
        supported = {"merge_rule_choices", "save_rule_choices", "update_saved_rule_choices", "run_selected_rules", "run_saved_filter_check"}
        return any(token in supported for token in re.split(r"[:/\s]+", value))

    def _git_project_secrets_filter_action_id(self, action_key: str, state: dict[str, Any]) -> str:
        explicit = str(state.get("secrets_filter_action") or state.get("filter_action") or "").strip()
        if explicit:
            return explicit
        tokens = [token for token in re.split(r"[:/\s]+", str(action_key or "")) if token]
        for token in reversed(tokens):
            if token in {"merge_rule_choices", "save_rule_choices", "update_saved_rule_choices", "run_selected_rules", "run_saved_filter_check"}:
                return token
        return ""

    def _normalize_git_project_security_rule_choices(self, raw_choices: Any) -> tuple[dict[str, bool], list[str]]:
        import git_dirty as dirty

        if not isinstance(raw_choices, dict):
            raw_choices = {}
        warnings: list[str] = []
        known_rules = [str(rule.get("id") or "") for rule in dirty.security_rule_catalog()]
        known_set = set(known_rules)
        choices: dict[str, bool] = {}
        for rule_id, enabled in raw_choices.items():
            rule_key = str(rule_id or "").strip()
            if not rule_key:
                continue
            if rule_key not in known_set:
                warnings.append(f"ignored_unknown_rule:{rule_key}")
                continue
            choices[rule_key] = bool(enabled)
        for rule in dirty.security_rule_catalog():
            rule_id = str(rule.get("id") or "")
            if rule_id and rule_id not in choices:
                choices[rule_id] = bool(rule.get("recommended", True))
                warnings.append(f"defaulted_missing_rule:{rule_id}")
        return {rule_id: bool(choices[rule_id]) for rule_id in known_rules if rule_id in choices}, warnings

    def _git_project_security_policy_payload(self, choices: dict[str, bool]) -> dict[str, Any]:
        import git_dirty as dirty

        ordered: dict[str, bool] = {}
        for rule in dirty.security_rule_catalog():
            rule_id = str(rule.get("id") or "")
            if rule_id:
                ordered[rule_id] = bool(choices.get(rule_id, rule.get("recommended", True)))
        return {
            "policy_version": dirty.SECURITY_POLICY_VERSION,
            "rules": ordered,
        }

    def _write_git_project_security_policy(self, repo: Path, choices: dict[str, bool]) -> Path:
        import git_dirty as dirty

        policy_path = dirty.safe_repo_file_path(repo, dirty.SECURITY_RULES_POLICY_PATH)
        if policy_path is None:
            raise ValueError("Security rule policy path is not safe.")
        policy_path.write_text(
            json.dumps(self._git_project_security_policy_payload(choices), indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return policy_path

    def _git_project_secrets_candidate_paths(self, state: dict[str, Any]) -> list[str]:
        raw = state.get("candidate_paths")
        if raw is None:
            raw = (state.get("secrets_filter") or {}).get("candidate_paths") if isinstance(state.get("secrets_filter"), dict) else None
        if raw is None:
            raw = (state.get("scan") or {}).get("candidate_paths") if isinstance(state.get("scan"), dict) else None
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            raw = []
        paths: list[str] = []
        seen: set[str] = set()
        for item in raw[:1000]:
            value = str(item or "").replace("\\", "/").strip().lstrip("/")
            if not value or "\x00" in value or value.startswith("../") or "/../" in value:
                continue
            if value not in seen:
                seen.add(value)
                paths.append(value)
        return paths

    def _git_project_secrets_candidate_records(self, candidate_paths: list[str]) -> list[dict[str, Any]]:
        return [{"path": path, "deleted": False} for path in candidate_paths if path]

    def _git_project_transient_security_policy(self, choices: dict[str, bool]) -> dict[str, Any]:
        import git_dirty as dirty

        return {
            "exists": False,
            "project_policy_path": dirty.SECURITY_RULES_POLICY_PATH,
            "rules": dict(choices),
            "errors": [],
            "unknown_rules": [],
        }

    def _git_project_secrets_findings_by_rule(self, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for finding in findings:
            rule_id = str(finding.get("rule_id") or "unknown")
            grouped.setdefault(rule_id, []).append(finding)
        return [
            {
                "rule_id": rule_id,
                "finding_count": len(items),
                "findings": items,
            }
            for rule_id, items in sorted(grouped.items())
        ]

    def _git_project_security_finding(
        self,
        *,
        path: str,
        line_no: int,
        rule_id: str,
        kind: str,
        severity: str,
        value: str,
    ) -> dict[str, Any]:
        import git_dirty as dirty

        finding = dirty.privacy_finding(
            path,
            line_no,
            kind,
            severity,
            value,
            rule_id=rule_id,
        )
        finding["evidence"] = dirty.format_privacy_evidence(value)
        finding["evidence_truncated"] = finding["evidence"] != str(value or "").strip()
        finding["evidence_length"] = len(str(value or "").strip())
        return finding

    def _git_project_add_security_finding(
        self,
        findings: list[dict[str, Any]],
        seen: set[tuple[str, str, str, int, str]],
        finding: dict[str, Any],
    ) -> None:
        key = (
            str(finding.get("rule_id") or ""),
            str(finding.get("kind") or ""),
            str(finding.get("evidence_redacted") or ""),
            int(finding.get("line") or 0),
            str(finding.get("path") or ""),
        )
        if key in seen:
            return
        seen.add(key)
        findings.append(finding)

    def _git_project_emit_secrets_scan_event(self, emit: Any, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("type", "event")
        payload.setdefault("time", time.time())
        emit(payload)

    def _git_project_run_security_filter_scan_stream(
        self,
        repo: Path,
        *,
        candidate_paths: list[str],
        policy: dict[str, Any],
        mode: str,
        label: str,
        emit: Any,
    ) -> dict[str, Any]:
        import git_dirty as dirty

        preflight = dirty.security_rules_output(
            repo,
            findings=[],
            ran_rule_ids=set(),
            policy=policy,
            scan_status="running",
            execution_owner="backend",
        )
        enabled_available = {
            str(rule.get("id") or "")
            for rule in preflight.get("rules", [])
            if rule.get("enabled") and rule.get("available", True)
        }
        enabled_unavailable = [
            str(rule.get("id") or "")
            for rule in preflight.get("rules", [])
            if rule.get("enabled") and not rule.get("available", True)
        ]
        builtin_rule_ids = enabled_available & {"windows_user_paths", "unix_user_paths", "user_names", "secrets"}
        findings: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, int, str]] = set()
        file_results: list[dict[str, Any]] = []
        self._git_project_emit_secrets_scan_event(emit, {
            "type": "started",
            "mode": mode,
            "label": label,
            "candidate_file_count": len(candidate_paths),
            "enabled_rule_ids": sorted(enabled_available),
            "unavailable_rule_ids": enabled_unavailable,
        })
        for rule_id in sorted(builtin_rule_ids):
            self._git_project_emit_secrets_scan_event(emit, {"type": "rule_status", "rule_id": rule_id, "status": "running"})
        before_count = 0
        for index, relative_path in enumerate(candidate_paths, 1):
            result = self._git_project_scan_builtin_security_file(
                repo,
                relative_path,
                builtin_rule_ids,
                findings,
                seen,
            )
            file_results.append(result)
            new_findings = findings[before_count:]
            before_count = len(findings)
            self._git_project_emit_secrets_scan_event(emit, {
                "type": "file_scanned",
                "path": relative_path,
                "index": index,
                "total": len(candidate_paths),
                "status": result.get("status", "scanned"),
                "finding_count": len(new_findings),
            })
            for finding in new_findings:
                self._git_project_emit_secrets_scan_event(emit, {"type": "finding", "finding": finding})
            if len(findings) >= dirty.PRIVACY_SCAN_MAX_TOTAL_FINDINGS:
                self._git_project_emit_secrets_scan_event(emit, {
                    "type": "truncated",
                    "reason": "max_total_findings",
                    "finding_count": len(findings),
                })
                break
        ran_rule_ids = set(builtin_rule_ids)
        for rule_id in sorted(builtin_rule_ids):
            self._git_project_emit_secrets_scan_event(emit, {
                "type": "rule_status",
                "rule_id": rule_id,
                "status": "done",
                "finding_count": sum(1 for item in findings if item.get("rule_id") == rule_id),
            })
        detect_result: dict[str, Any] = {"status": "not_requested", "ran": False}
        if "detect_secrets" in enabled_available:
            self._git_project_emit_secrets_scan_event(emit, {"type": "rule_status", "rule_id": "detect_secrets", "status": "running"})
            before_count = len(findings)
            detect_result = self._git_project_run_detect_secrets_scan(repo, candidate_paths, findings, seen)
            if detect_result.get("ran"):
                ran_rule_ids.add("detect_secrets")
            for finding in findings[before_count:]:
                self._git_project_emit_secrets_scan_event(emit, {"type": "finding", "finding": finding})
            self._git_project_emit_secrets_scan_event(emit, {
                "type": "rule_status",
                "rule_id": "detect_secrets",
                "status": "done" if detect_result.get("ran") else str(detect_result.get("status") or "unavailable"),
                "finding_count": sum(1 for item in findings if item.get("rule_id") == "detect_secrets"),
            })
        elif "detect_secrets" in enabled_unavailable:
            self._git_project_emit_secrets_scan_event(emit, {"type": "rule_status", "rule_id": "detect_secrets", "status": "unavailable"})
        blocking_findings = sum(1 for finding in findings if finding.get("blocks_commit"))
        scan_status = "blocked" if enabled_unavailable or blocking_findings else "passed"
        rules_state = dirty.security_rules_output(
            repo,
            findings=findings,
            ran_rule_ids=ran_rule_ids,
            policy=policy,
            scan_status=scan_status,
            execution_owner="backend",
        )
        summary = dict(rules_state.get("summary") or {})
        summary.update({
            "candidate_file_count": len(candidate_paths),
            "scanned_file_count": sum(1 for item in file_results if item.get("status") == "scanned"),
            "skipped_file_count": sum(1 for item in file_results if str(item.get("status") or "").startswith("skipped")),
            "missing_file_count": sum(1 for item in file_results if item.get("status") == "missing"),
            "enabled_unavailable_rules": enabled_unavailable,
            "detect_secrets_status": detect_result.get("status", ""),
        })
        scan_result = {
            "mode": mode,
            "label": label,
            "status": scan_status,
            "gate_status": rules_state.get("gate_status", "pending"),
            "execution_owner": "backend",
            "git_dirty_content_scan": False,
            "candidate_paths": candidate_paths,
            "rules": list(rules_state.get("rules") or []),
            "summary": summary,
            "findings": findings,
            "findings_by_rule": self._git_project_secrets_findings_by_rule(findings),
            "file_results": file_results[:200],
            "detect_secrets": detect_result,
            "pending_message": "",
            "note": "Backend filter scan result. Values are emitted as matched so the review pane can show the exact item.",
        }
        self._git_project_emit_secrets_scan_event(emit, {
            "type": "finished",
            "mode": mode,
            "label": label,
            "status": scan_status,
            "gate_status": scan_result.get("gate_status"),
            "finding_count": len(findings),
            "blocking_finding_count": blocking_findings,
            "scan_result": scan_result,
        })
        return scan_result

    def _git_project_scan_builtin_security_file(
        self,
        repo: Path,
        relative_path: str,
        enabled_rule_ids: set[str],
        findings: list[dict[str, Any]],
        seen: set[tuple[str, str, str, int, str]],
    ) -> dict[str, Any]:
        import git_dirty as dirty

        full = dirty.safe_repo_file_path(repo, relative_path)
        if full is None or not full.exists() or not full.is_file():
            return {"path": relative_path, "status": "missing"}
        try:
            size = full.stat().st_size
        except OSError:
            return {"path": relative_path, "status": "stat_failed"}
        if size > dirty.PRIVACY_SCAN_MAX_BYTES:
            return {"path": relative_path, "status": "skipped_large", "bytes": size}
        try:
            data = full.read_bytes()
        except OSError:
            return {"path": relative_path, "status": "read_failed"}
        if b"\x00" in data[:4096]:
            return {"path": relative_path, "status": "skipped_binary", "bytes": size}
        text = data.decode("utf-8", errors="replace")
        status = {"path": relative_path, "status": "scanned", "bytes": size}
        for line_no, line in enumerate(text.splitlines(), 1):
            if len(findings) >= dirty.PRIVACY_SCAN_MAX_TOTAL_FINDINGS:
                status["truncated"] = True
                return status
            if "windows_user_paths" in enabled_rule_ids:
                for match in dirty.WINDOWS_USER_PATH_RE.finditer(line):
                    matched_path = match.group(0)
                    if not dirty.should_flag_windows_user_path(matched_path):
                        continue
                    self._git_project_add_security_finding(
                        findings,
                        seen,
                        self._git_project_security_finding(
                            path=relative_path,
                            line_no=line_no,
                            rule_id="windows_user_paths",
                            kind="windows_user_path",
                            severity="review",
                            value=matched_path,
                        ),
                    )
            if "unix_user_paths" in enabled_rule_ids:
                for regex, kind in ((dirty.UNIX_USER_PATH_RE, "unix_user_path"), (dirty.TILDE_USER_PATH_RE, "tilde_user_path")):
                    for match in regex.finditer(line):
                        self._git_project_add_security_finding(
                            findings,
                            seen,
                            self._git_project_security_finding(
                                path=relative_path,
                                line_no=line_no,
                                rule_id="unix_user_paths",
                                kind=kind,
                                severity="review",
                                value=match.group(0),
                            ),
                        )
            if "user_names" in enabled_rule_ids and dirty.LOCAL_CONTEXT_RE.search(line):
                for match in dirty.LOCAL_USER_ASSIGNMENT_RE.finditer(line):
                    self._git_project_add_security_finding(
                        findings,
                        seen,
                        self._git_project_security_finding(
                            path=relative_path,
                            line_no=line_no,
                            rule_id="user_names",
                            kind="local_user_name",
                            severity="review",
                            value=match.group(1),
                        ),
                    )
                for match in dirty.EMAIL_IN_TEXT_RE.finditer(line):
                    self._git_project_add_security_finding(
                        findings,
                        seen,
                        self._git_project_security_finding(
                            path=relative_path,
                            line_no=line_no,
                            rule_id="user_names",
                            kind="local_user_email",
                            severity="review",
                            value=match.group(0),
                        ),
                    )
                for regex in (dirty.WINDOWS_USER_NAME_RE, dirty.UNIX_USER_PATH_RE):
                    for match in regex.finditer(line):
                        user_value = match.group(1) if match.lastindex else match.group(0)
                        if regex is dirty.WINDOWS_USER_NAME_RE and dirty.is_allowed_windows_user_path_example(match.group(0)):
                            continue
                        self._git_project_add_security_finding(
                            findings,
                            seen,
                            self._git_project_security_finding(
                                path=relative_path,
                                line_no=line_no,
                                rule_id="user_names",
                                kind="local_user_name",
                                severity="review",
                                value=user_value,
                            ),
                        )
            if "secrets" in enabled_rule_ids:
                secret_patterns = (
                    (dirty.PRIVATE_KEY_RE, "private_key"),
                    (dirty.JWT_RE, "jwt"),
                    (dirty.OPENAI_KEY_RE, "openai_key"),
                    (dirty.GITHUB_TOKEN_RE, "github_token"),
                    (dirty.AWS_ACCESS_KEY_RE, "aws_access_key"),
                    (dirty.SLACK_TOKEN_RE, "slack_token"),
                    (dirty.STRIPE_SECRET_RE, "stripe_secret"),
                    (dirty.DISCORD_WEBHOOK_RE, "discord_webhook"),
                    (dirty.HIGH_ENTROPY_RE, "high_entropy_token"),
                )
                for regex, kind in secret_patterns:
                    for match in regex.finditer(line):
                        value = match.group(1) if kind == "high_entropy_token" and match.lastindex else match.group(0)
                        self._git_project_add_security_finding(
                            findings,
                            seen,
                            self._git_project_security_finding(
                                path=relative_path,
                                line_no=line_no,
                                rule_id="secrets",
                                kind=kind,
                                severity="critical",
                                value=value,
                            ),
                        )
                for match in dirty.SECRET_ASSIGNMENT_RE.finditer(line):
                    self._git_project_add_security_finding(
                        findings,
                        seen,
                        self._git_project_security_finding(
                            path=relative_path,
                            line_no=line_no,
                            rule_id="secrets",
                            kind="secret_assignment",
                            severity="critical",
                            value=match.group(1),
                        ),
                    )
        return status

    def _git_project_read_file_line(self, repo: Path, relative_path: str, line_no: int) -> str:
        import git_dirty as dirty

        if line_no <= 0:
            return ""
        full = dirty.safe_repo_file_path(repo, relative_path)
        if full is None or not full.exists() or not full.is_file():
            return ""
        try:
            with full.open("r", encoding="utf-8", errors="replace") as handle:
                for current, line in enumerate(handle, 1):
                    if current == line_no:
                        return line.rstrip("\r\n")
        except OSError:
            return ""
        return ""

    def _git_project_run_detect_secrets_scan(
        self,
        repo: Path,
        candidate_paths: list[str],
        findings: list[dict[str, Any]],
        seen: set[tuple[str, str, str, int, str]],
    ) -> dict[str, Any]:
        import git_dirty as dirty

        availability = dirty.detect_secrets_availability()
        if not availability.get("available"):
            return {"status": "unavailable", "availability": availability, "ran": False}
        executable = str(availability.get("executable") or "").strip()
        command: list[str]
        if executable:
            command = [executable, "scan", "--all-files", *candidate_paths[:500]]
        else:
            command = [sys.executable, "-m", "detect_secrets", "scan", "--all-files", *candidate_paths[:500]]
        result = self._run_command(
            command,
            cwd=repo,
            timeout=180,
            not_found_stderr="detect-secrets is not available.",
        )
        if result.get("returncode") != 0:
            return {
                "status": "failed",
                "ran": False,
                "availability": availability,
                "returncode": result.get("returncode"),
                "stderr": result.get("stderr", ""),
            }
        try:
            payload = json.loads(result.get("stdout") or "{}")
        except json.JSONDecodeError as exc:
            return {"status": "failed", "ran": False, "availability": availability, "error": f"invalid detect-secrets JSON: {exc}"}
        results = payload.get("results") if isinstance(payload, dict) else {}
        if isinstance(results, dict):
            for path, items in results.items():
                if not isinstance(items, list):
                    continue
                rel = str(path or "").replace("\\", "/")
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    line_no = int(item.get("line_number") or 0)
                    kind = str(item.get("type") or "detect_secrets")
                    evidence = self._git_project_read_file_line(repo, rel, line_no) or kind
                    self._git_project_add_security_finding(
                        findings,
                        seen,
                        self._git_project_security_finding(
                            path=rel,
                            line_no=line_no,
                            rule_id="detect_secrets",
                            kind=kind,
                            severity="critical",
                            value=evidence,
                        ),
                    )
        return {"status": "ran", "ran": True, "availability": availability, "returncode": result.get("returncode")}

    def _git_project_run_security_filter_scan(
        self,
        repo: Path,
        *,
        candidate_paths: list[str],
        policy: dict[str, Any],
        mode: str,
        label: str,
    ) -> dict[str, Any]:
        import git_dirty as dirty

        preflight = dirty.security_rules_output(
            repo,
            findings=[],
            ran_rule_ids=set(),
            policy=policy,
            scan_status="running",
            execution_owner="backend",
        )
        enabled_available = {
            str(rule.get("id") or "")
            for rule in preflight.get("rules", [])
            if rule.get("enabled") and rule.get("available", True)
        }
        enabled_unavailable = [
            str(rule.get("id") or "")
            for rule in preflight.get("rules", [])
            if rule.get("enabled") and not rule.get("available", True)
        ]
        builtin_rule_ids = enabled_available & {"windows_user_paths", "unix_user_paths", "user_names", "secrets"}
        findings: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, int, str]] = set()
        file_results: list[dict[str, Any]] = []
        for relative_path in candidate_paths:
            file_results.append(
                self._git_project_scan_builtin_security_file(
                    repo,
                    relative_path,
                    builtin_rule_ids,
                    findings,
                    seen,
                )
            )
            if len(findings) >= dirty.PRIVACY_SCAN_MAX_TOTAL_FINDINGS:
                break
        ran_rule_ids = set(builtin_rule_ids)
        detect_result: dict[str, Any] = {"status": "not_requested", "ran": False}
        if "detect_secrets" in enabled_available:
            detect_result = self._git_project_run_detect_secrets_scan(repo, candidate_paths, findings, seen)
            if detect_result.get("ran"):
                ran_rule_ids.add("detect_secrets")
        blocking_findings = sum(1 for finding in findings if finding.get("blocks_commit"))
        scan_status = "blocked" if enabled_unavailable or blocking_findings else "passed"
        rules_state = dirty.security_rules_output(
            repo,
            findings=findings,
            ran_rule_ids=ran_rule_ids,
            policy=policy,
            scan_status=scan_status,
            execution_owner="backend",
        )
        summary = dict(rules_state.get("summary") or {})
        summary.update({
            "candidate_file_count": len(candidate_paths),
            "scanned_file_count": sum(1 for item in file_results if item.get("status") == "scanned"),
            "skipped_file_count": sum(1 for item in file_results if str(item.get("status") or "").startswith("skipped")),
            "missing_file_count": sum(1 for item in file_results if item.get("status") == "missing"),
            "enabled_unavailable_rules": enabled_unavailable,
            "detect_secrets_status": detect_result.get("status", ""),
        })
        return {
            "mode": mode,
            "label": label,
            "status": scan_status,
            "gate_status": rules_state.get("gate_status", "pending"),
            "execution_owner": "backend",
            "git_dirty_content_scan": False,
            "candidate_paths": candidate_paths,
            "rules": list(rules_state.get("rules") or []),
            "summary": summary,
            "findings": findings,
            "findings_by_rule": self._git_project_secrets_findings_by_rule(findings),
            "file_results": file_results[:200],
            "detect_secrets": detect_result,
            "pending_message": "",
            "note": "Backend filter scan result. Evidence is redacted before it reaches the browser.",
        }

    def _git_project_secrets_filter_model(
        self,
        repo: Path,
        *,
        candidate_paths: list[str],
        draft_policy: dict[str, Any] | None = None,
        scan_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import git_dirty as dirty

        candidate_records = self._git_project_secrets_candidate_records(candidate_paths)
        saved_policy = dirty.load_project_security_policy(repo)
        model = dirty.secrets_filter_payload(repo, candidate_records)
        if saved_policy.get("exists"):
            saved_state = dirty.security_rules_output(
                repo,
                findings=[],
                ran_rule_ids=set(),
                policy=saved_policy,
                scan_status="pending_external_scan",
                execution_owner="backend",
            )
            model["saved_rules"] = list(saved_state.get("rules") or [])
            model["saved_summary"] = {
                **dict(saved_state.get("summary") or {}),
                "candidate_file_count": len(candidate_paths),
            }
            model["saved_policy_exists"] = True
        else:
            model["saved_rules"] = []
            model["saved_summary"] = {
                "rule_count": 0,
                "available_rule_count": 0,
                "enabled_rule_count": 0,
                "finding_count": 0,
                "blocking_finding_count": 0,
                "unavailable_enabled_rule_count": 0,
                "unavailable_enabled_rules": [],
                "candidate_file_count": len(candidate_paths),
                "gate_status": "pending",
                "requires_user_scan": False,
            }
            model["saved_policy_exists"] = False
        if draft_policy is not None:
            draft_state = dirty.security_rules_output(
                repo,
                findings=[],
                ran_rule_ids=set(),
                policy=draft_policy,
                scan_status="pending_external_scan",
                execution_owner="backend",
            )
            draft_rules = []
            for rule in draft_state.get("rules") or []:
                item = dict(rule)
                if str(item.get("id") or "") in dict(draft_policy.get("rules") or {}):
                    item["source"] = "draft"
                draft_rules.append(item)
            model["rules"] = draft_rules
            model["summary"] = {
                **dict(draft_state.get("summary") or {}),
                "candidate_file_count": len(candidate_paths),
            }
            model["draft_policy"] = {
                "exists": False,
                "rules": dict(draft_policy.get("rules") or {}),
            }
        if scan_result is not None:
            model["scan_result"] = scan_result
            model["scan"] = scan_result
        return model

    def _git_project_secrets_scan_worker_script(self) -> str:
        return """from __future__ import annotations
import json
import pathlib
import sys

payload_path = pathlib.Path(sys.argv[1])
payload = json.loads(payload_path.read_text(encoding="utf-8"))
sys.path.insert(0, payload["app_root"])

from main_computer.git_tools import GitToolsService

service = GitToolsService(pathlib.Path(payload["app_root"]), load_patch_service=False)
repo = pathlib.Path(payload["repo"])

def emit(event):
    sys.stdout.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\\n")
    sys.stdout.flush()

service._git_project_run_security_filter_scan_stream(
    repo,
    candidate_paths=list(payload.get("candidate_paths") or []),
    policy=dict(payload.get("policy") or {}),
    mode=str(payload.get("mode") or "scan"),
    label=str(payload.get("label") or "Secrets / Filter scan"),
    emit=emit,
)
"""

    def _append_git_project_secrets_scan_event(self, job_id: str, event: dict[str, Any]) -> dict[str, Any]:
        with self._secrets_scan_lock:
            job = self._secrets_scan_jobs.get(job_id)
            if job is None:
                return event
            seq = int(job.get("next_seq") or 1)
            payload = dict(event)
            payload["seq"] = seq
            payload.setdefault("job_id", job_id)
            payload.setdefault("time", time.time())
            job["next_seq"] = seq + 1
            job.setdefault("events", []).append(payload)
            job["updated_at"] = time.time()
            if payload.get("type") in {"finished", "error"}:
                job["status"] = "finished" if payload.get("type") == "finished" else "failed"
                job["finished_at"] = time.time()
                if payload.get("scan_result"):
                    job["result"] = payload.get("scan_result")
            return payload

    def _run_git_project_secrets_scan_worker_thread(self, job_id: str, payload: dict[str, Any]) -> None:
        temp_path: Path | None = None
        process: subprocess.Popen[str] | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
                json.dump(payload, handle)
                temp_path = Path(handle.name)
            process = subprocess.Popen(
                [sys.executable, "-S", "-u", "-c", self._git_project_secrets_scan_worker_script(), str(temp_path)],
                cwd=payload.get("app_root") or str(self.repo_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            with self._secrets_scan_lock:
                job = self._secrets_scan_jobs.get(job_id)
                if job is not None:
                    job["process"] = process
                    job["pid"] = process.pid
            assert process.stdout is not None
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    event = {"type": "worker_output", "message": line}
                if isinstance(event, dict):
                    self._append_git_project_secrets_scan_event(job_id, event)
            stderr = ""
            returncode = process.wait(timeout=10)
            with self._secrets_scan_lock:
                job = self._secrets_scan_jobs.get(job_id)
                already_done = bool(job and job.get("status") in {"finished", "failed", "cancelled"})
            if returncode != 0 and not already_done:
                self._append_git_project_secrets_scan_event(job_id, {
                    "type": "error",
                    "status": "failed",
                    "message": "Secrets / Filter scan worker failed.",
                    "returncode": returncode,
                    "stderr": stderr[-4000:],
                })
            elif not already_done:
                self._append_git_project_secrets_scan_event(job_id, {
                    "type": "finished",
                    "status": "passed",
                    "gate_status": "passed",
                    "finding_count": 0,
                    "message": "Secrets / Filter scan worker finished without a final event.",
                })
        except Exception as exc:
            self._append_git_project_secrets_scan_event(job_id, {
                "type": "error",
                "status": "failed",
                "message": str(exc),
            })
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            with self._secrets_scan_lock:
                job = self._secrets_scan_jobs.get(job_id)
                if job is not None and job.get("status") == "running":
                    job["status"] = "finished"
                    job["finished_at"] = time.time()
                    job["updated_at"] = time.time()
                    job["process"] = None

    def _start_git_project_secrets_filter_scan_job(
        self,
        *,
        repo: Path,
        candidate_paths: list[str],
        policy: dict[str, Any],
        mode: str,
        label: str,
        choices: dict[str, bool],
        warnings: list[str],
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:16]
        payload = {
            "app_root": str(self.repo_root),
            "repo": str(repo),
            "candidate_paths": candidate_paths,
            "policy": policy,
            "mode": mode,
            "label": label,
        }
        initial_event = {
            "type": "queued",
            "mode": mode,
            "label": label,
            "status": "queued",
            "gate_status": "pending",
            "candidate_file_count": len(candidate_paths),
        }
        with self._secrets_scan_lock:
            self._secrets_scan_jobs[job_id] = {
                "id": job_id,
                "status": "running",
                "mode": mode,
                "label": label,
                "repo": str(repo),
                "candidate_paths": candidate_paths,
                "choices": dict(choices),
                "warnings": list(warnings),
                "events": [],
                "next_seq": 1,
                "started_at": time.time(),
                "updated_at": time.time(),
                "finished_at": None,
                "result": None,
                "process": None,
            }
        self._append_git_project_secrets_scan_event(job_id, initial_event)
        thread = threading.Thread(
            target=self._run_git_project_secrets_scan_worker_thread,
            args=(job_id, payload),
            daemon=True,
            name=f"secrets-filter-scan-{job_id}",
        )
        with self._secrets_scan_lock:
            job = self._secrets_scan_jobs.get(job_id)
            if job is not None:
                job["thread"] = thread
        thread.start()
        running_result = {
            "mode": mode,
            "label": label,
            "status": "running",
            "gate_status": "pending",
            "execution_owner": "backend",
            "candidate_paths": candidate_paths,
            "findings": [],
            "findings_by_rule": [],
            "summary": {
                "candidate_file_count": len(candidate_paths),
                "finding_count": 0,
                "blocking_finding_count": 0,
            },
            "pending_message": "Scan started. Results will stream into this pane as the backend subprocess reports events.",
        }
        draft_policy = self._git_project_transient_security_policy(choices) if mode == "draft_selected_rules" and choices else None
        model = self._git_project_secrets_filter_model(
            repo,
            candidate_paths=candidate_paths,
            draft_policy=draft_policy,
            scan_result=running_result,
        )
        return {
            "ok": True,
            "mode": "secrets-filter-stream",
            "action": "run_selected_rules" if mode == "draft_selected_rules" else "run_saved_filter_check",
            "repo": str(repo),
            "warnings": warnings,
            "scan_job_id": job_id,
            "stream_url": f"/api/applications/git/project/secrets-filter/stream?job_id={job_id}",
            "secrets_filter": model,
            "scan_result": running_result,
        }

    def git_project_secrets_filter_job_events(self, job_id: str, after_seq: int = 0) -> dict[str, Any]:
        with self._secrets_scan_lock:
            job = self._secrets_scan_jobs.get(str(job_id or ""))
            if job is None:
                return {"ok": False, "error": "Unknown Secrets / Filter scan job.", "events": [], "done": True}
            events = [
                dict(event)
                for event in job.get("events", [])
                if int(event.get("seq") or 0) > int(after_seq or 0)
            ]
            done = str(job.get("status") or "") in {"finished", "failed", "cancelled"}
            return {
                "ok": True,
                "job_id": job_id,
                "status": job.get("status"),
                "mode": job.get("mode"),
                "label": job.get("label"),
                "events": events,
                "done": done,
                "result": job.get("result"),
            }

    def _repo_path_token_is_missing_or_current_dir(self, value: Any) -> bool:
        cleaned = self._clean_git_panel_path_token(str(value or "").strip())
        return not cleaned or cleaned == "."

    def _resolve_commit_job_repo_dir(self, payload: dict[str, Any]) -> tuple[Path, str, dict[str, Any] | None]:
        """Resolve the repo that a selected-file commit job should mutate.

        Commit execution must target the selected Git Tools project, not the
        Main Computer app root merely because a browser payload omitted
        ``repo_dir`` or sent ``.``.  The resolver prefers explicit project
        identity, then explicit project path, then explicit repo path, and only
        then falls back to the current selected project registry entry.
        """

        registry = self._load_git_project_registry()
        self._ensure_main_project_record(registry)

        project_id = str(payload.get("project_id") or "").strip()
        project_path = str(payload.get("project_path") or payload.get("selected_project") or "").strip()
        raw_repo_dir = payload.get("repo_dir") if "repo_dir" in payload else payload.get("repo")
        repo_dir = str(raw_repo_dir or "").strip()

        record: dict[str, Any] | None = None
        source = ""

        if project_id:
            record = self._find_project_record(registry, project_id=project_id)
            if record is None:
                raise ValueError(f"Commit job project_id is not registered: {project_id}")
            if record.get("archived"):
                raise ValueError(f"Commit job project_id is archived and must be restored before use: {project_id}")
            record_path = str(record.get("path") or "")
            for label, token in (("project_path", project_path), ("repo_dir", repo_dir)):
                if not token or (label == "repo_dir" and self._repo_path_token_is_missing_or_current_dir(token)):
                    continue
                try:
                    resolved_token = self.repo_path(token)
                except Exception:
                    resolved_token = Path(token)
                if not self._same_path(Path(record_path), resolved_token):
                    raise ValueError(
                        "Commit target mismatch: "
                        f"payload project_id {project_id} maps to {record_path}, "
                        f"but payload {label} is {token}."
                    )
            repo_token = record_path
            source = f"payload.project_id:{project_id}"
        elif project_path:
            record = self._find_project_record(registry, path=project_path)
            repo_token = str(record.get("path") if record else project_path)
            source = "payload.project_path"
        elif not self._repo_path_token_is_missing_or_current_dir(repo_dir):
            record = self._find_project_record(registry, path=repo_dir)
            repo_token = str(record.get("path") if record else repo_dir)
            source = "payload.repo_dir"
        else:
            record = self._current_project_record(registry)
            if record is None:
                raise ValueError("No Git Tools project is selected for the commit job.")
            if record.get("vip"):
                raise ValueError(
                    "Commit job did not include an explicit project path and the current Git Tools "
                    "project is the Main Computer app root; refusing to default selected-file commit "
                    "execution to the app repository."
                )
            repo_token = str(record.get("path") or "")
            source = f"current_project:{record.get('id') or '(unknown)'}"

        if record and record.get("archived"):
            raise ValueError("Archived projects must be restored before commit jobs.")
        if not repo_token:
            raise ValueError("Commit job target project path is empty.")

        resolved = self.repo_path(repo_token)
        return resolved, source, record

    def start_git_project_commit_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Start a selected-file commit job owned by the backend runner."""
        if not isinstance(payload, dict):
            raise ValueError("Commit job payload must be a JSON object.")
        normalized = dict(payload)
        repo, repo_source, record = self._resolve_commit_job_repo_dir(normalized)
        normalized["repo_dir"] = str(repo)
        normalized["repo_dir_source"] = repo_source
        normalized["app_root"] = str(self.repo_root)
        if record:
            normalized["project_id"] = str(record.get("id") or normalized.get("project_id") or "")
            normalized["project_path"] = str(record.get("path") or normalized.get("project_path") or "")
        return self._commit_jobs.start_job(normalized)

    def git_project_commit_job_events(self, job_id: str, after_seq: int = 0) -> dict[str, Any]:
        return self._commit_jobs.job_events(job_id, after_seq=after_seq)

    def cancel_git_project_commit_job(self, job_id: str) -> dict[str, Any]:
        return self._commit_jobs.cancel_job(job_id)

    def _run_git_project_secrets_filter_action(
        self,
        *,
        action_key: str,
        label: str,
        repo_dir: str,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        import git_dirty as dirty

        action_id = self._git_project_secrets_filter_action_id(action_key, state)
        if action_id == "save_rule_choices":
            action_id = "merge_rule_choices"
        if action_id not in {"merge_rule_choices", "update_saved_rule_choices", "run_selected_rules", "run_saved_filter_check"}:
            raise ValueError("Unsupported Secrets / Filter action.")
        repo = self.repo_path(str(state.get("repo") or repo_dir or "."))
        candidate_paths = self._git_project_secrets_candidate_paths(state)
        choices, warnings = self._normalize_git_project_security_rule_choices(state.get("rule_choices", {}))
        if action_id == "update_saved_rule_choices":
            saved_policy_for_update = dirty.load_project_security_policy(repo)
            changed_rule_id = str(state.get("changed_rule_id") or "").strip()
            if changed_rule_id:
                base_choices = dict(saved_policy_for_update.get("rules") or {})
                if changed_rule_id in dirty.security_rule_by_id():
                    changed_enabled = state.get("changed_rule_enabled")
                    if isinstance(changed_enabled, str):
                        changed_enabled = changed_enabled.strip().lower() in {"1", "true", "yes", "on"}
                    base_choices[changed_rule_id] = bool(changed_enabled)
                    choices, update_warnings = self._normalize_git_project_security_rule_choices(base_choices)
                    warnings.extend(item for item in update_warnings if item not in warnings)
                else:
                    warnings.append(f"ignored_unknown_rule:{changed_rule_id}")
        run_payload = {
            "action_key": action_key,
            "action": action_id,
            "label": label,
            "repo_dir": repo_dir,
            "repo": str(repo),
            "candidate_file_count": len(candidate_paths),
            "rule_count": len(choices),
        }

        if action_id == "run_selected_rules":
            draft_policy = self._git_project_transient_security_policy(choices)
            return self._start_git_project_secrets_filter_scan_job(
                repo=repo,
                candidate_paths=candidate_paths,
                policy=draft_policy,
                mode="draft_selected_rules",
                label="Draft selected-rule scan",
                choices=choices,
                warnings=warnings,
            )

        if action_id == "run_saved_filter_check":
            saved_policy = dirty.load_project_security_policy(repo)
            if not saved_policy.get("exists"):
                scan_result = {
                    "mode": "no_saved_policy",
                    "label": "No saved policy yet",
                    "status": "pending",
                    "gate_status": "pending",
                    "execution_owner": "backend",
                    "git_dirty_content_scan": False,
                    "candidate_paths": candidate_paths,
                    "findings": [],
                    "findings_by_rule": [],
                    "summary": {
                        "candidate_file_count": len(candidate_paths),
                        "finding_count": 0,
                        "blocking_finding_count": 0,
                    },
                    "pending_message": "Merge rule choices before running the full saved filter check.",
                    "note": "The full saved check is not allowed to fall back to default draft rules.",
                }
                model = self._git_project_secrets_filter_model(
                    repo,
                    candidate_paths=candidate_paths,
                    draft_policy=self._git_project_transient_security_policy(choices) if choices else None,
                    scan_result=scan_result,
                )
                return {
                    "ok": True,
                    "mode": "secrets-filter",
                    "action": action_id,
                    "repo": str(repo),
                    "warnings": warnings,
                    "secrets_filter": model,
                    "scan_result": scan_result,
                }
            return self._start_git_project_secrets_filter_scan_job(
                repo=repo,
                candidate_paths=candidate_paths,
                policy=saved_policy,
                mode="full_saved_policy",
                label="Full saved policy scan",
                choices=choices,
                warnings=warnings,
            )

        def callback() -> dict[str, Any]:
            if action_id in {"merge_rule_choices", "update_saved_rule_choices"}:
                policy_path = self._write_git_project_security_policy(repo, choices)
                saved_policy = dirty.load_project_security_policy(repo)
                instant_update = action_id == "update_saved_rule_choices"
                scan_result = {
                    "mode": "policy_saved",
                    "label": "Saved policy updated; scan not run" if instant_update else "Policy merged; scan not run",
                    "status": "pending",
                    "gate_status": "pending",
                    "execution_owner": "backend",
                    "git_dirty_content_scan": False,
                    "candidate_paths": candidate_paths,
                    "findings": [],
                    "findings_by_rule": [],
                    "summary": {
                        "candidate_file_count": len(candidate_paths),
                        "finding_count": 0,
                        "blocking_finding_count": 0,
                    },
                    "pending_message": (
                        "Saved rule choice was written to .git_dirty_rules.json immediately. Run the full saved filter check before commit."
                        if instant_update
                        else "Rule choices were merged into .git_dirty_rules.json. Run the full saved filter check before commit."
                    ),
                    "note": (
                        "Right-pane saved switches persist immediately; saved rule IDs are authoritative for future backend scans."
                        if instant_update
                        else "Merge writes the current draft choices; saved rule IDs are authoritative for future backend scans."
                    ),
                }
                model = self._git_project_secrets_filter_model(
                    repo,
                    candidate_paths=candidate_paths,
                    draft_policy=saved_policy,
                    scan_result=scan_result,
                )
                return {
                    "ok": True,
                    "mode": "secrets-filter",
                    "action": action_id,
                    "repo": str(repo),
                    "policy_path": str(policy_path),
                    "policy": self._git_project_security_policy_payload(choices),
                    "warnings": warnings,
                    "secrets_filter": model,
                    "scan_result": scan_result,
                }

            if action_id == "run_selected_rules":
                draft_policy = self._git_project_transient_security_policy(choices)
                scan_result = self._git_project_run_security_filter_scan(
                    repo,
                    candidate_paths=candidate_paths,
                    policy=draft_policy,
                    mode="draft_selected_rules",
                    label="Draft selected-rule scan",
                )
                scan_result["pending_message"] = "Draft scan used the current left-side checkbox choices. It was not saved and does not unblock commit."
                model = self._git_project_secrets_filter_model(
                    repo,
                    candidate_paths=candidate_paths,
                    draft_policy=draft_policy,
                    scan_result=scan_result,
                )
                return {
                    "ok": True,
                    "mode": "secrets-filter",
                    "action": action_id,
                    "repo": str(repo),
                    "warnings": warnings,
                    "secrets_filter": model,
                    "scan_result": scan_result,
                }

            saved_policy = dirty.load_project_security_policy(repo)
            scan_result = self._git_project_run_security_filter_scan(
                repo,
                candidate_paths=candidate_paths,
                policy=saved_policy,
                mode="full_saved_policy",
                label="Full saved policy scan",
            )
            scan_result["pending_message"] = "Full saved filter check used .git_dirty_rules.json after backend catalog merge. This is the commit gate result."
            draft_policy = self._git_project_transient_security_policy(choices) if choices else None
            model = self._git_project_secrets_filter_model(
                repo,
                candidate_paths=candidate_paths,
                draft_policy=draft_policy,
                scan_result=scan_result,
            )
            return {
                "ok": True,
                "mode": "secrets-filter",
                "action": action_id,
                "repo": str(repo),
                "warnings": warnings,
                "secrets_filter": model,
                "scan_result": scan_result,
            }

        return self.run_git_operation(
            "git-project-secrets-filter",
            label,
            callback,
            payload=run_payload,
        )

    def run_git_project_panel_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run one Git Tools wizard card through a disk-backed runner."""
        if not isinstance(payload, dict):
            raise ValueError("Panel action payload must be a JSON object.")
        action_key = str(payload.get("action_key", "") or "").strip()[:160]
        label = str(payload.get("label", "") or action_key or "Git panel action").strip()[:200]
        repo_dir = str(payload.get("repo_dir", ".") or ".")
        state = self._normalize_git_project_panel_state(payload.get("state", {}), repo_dir=repo_dir)
        if self._git_project_action_uses_secrets_filter_runner(action_key):
            return self._run_git_project_secrets_filter_action(
                action_key=action_key,
                label=label,
                repo_dir=repo_dir,
                state=state,
            )
        commands = self._sanitize_git_project_panel_commands(payload.get("commands", []))

        run_payload = {
            "action_key": action_key,
            "label": label,
            "repo_dir": repo_dir,
            "command_count": len(commands),
            "state": state,
        }

        def callback() -> dict[str, Any]:
            if self._git_project_action_uses_head_fix_runner(action_key):
                return self._run_git_project_head_fix_runner(
                    action_key=action_key,
                    label=label,
                    repo_dir=repo_dir,
                    commands=commands,
                    state=state,
                )
            return self._run_git_project_panel_action_runner(
                action_key=action_key,
                label=label,
                repo_dir=repo_dir,
                commands=commands,
                state=state,
            )

        return self.run_git_operation(
            "git-project-panel-action",
            label,
            callback,
            payload=run_payload,
        )

    def _normalize_git_project_panel_state(self, state: Any, *, repo_dir: str = ".") -> dict[str, Any]:
        if isinstance(state, str):
            try:
                state = json.loads(state or "{}")
            except json.JSONDecodeError:
                state = {"raw_state": state, "state_parse_error": "invalid-json"}
        if not isinstance(state, dict):
            state = {}
        normalized = dict(state)
        normalized.setdefault("repo", repo_dir or ".")
        normalized.setdefault("allow_mutating_actions", False)
        normalized.setdefault("allow_python_git_control", False)
        normalized.setdefault("line_endings", "lf")
        normalized.setdefault("runner_version", "MC_GIT_PANEL_RUNNER_V1")
        normalized["allow_mutating_actions"] = bool(normalized.get("allow_mutating_actions"))
        normalized["allow_python_git_control"] = bool(normalized.get("allow_python_git_control"))
        return normalized

    def _sanitize_git_project_panel_commands(self, commands: Any) -> list[str]:
        if not isinstance(commands, list):
            raise ValueError("Panel action commands must be a list.")
        cleaned: list[str] = []
        for item in commands[:20]:
            line = str(item or "").strip()
            if not line or line.startswith("#"):
                continue
            if "\n" in line or "\r" in line:
                raise ValueError("Panel action commands must be single-line command strings.")
            if len(line) > 4000:
                raise ValueError("Panel action command is too long.")
            first = ""
            try:
                parts = shlex.split(line, posix=os.name != "nt")
                first = Path(parts[0].replace("\\", "/")).name.lower() if parts else ""
            except ValueError:
                first = line.split(maxsplit=1)[0].lower() if line.split() else ""
            if first not in {"git", "python", "python3", "py", "python.exe"} and not first.endswith("python.exe"):
                raise ValueError(f"Unsupported panel action command executable: {first or line!r}")
            cleaned.append(line)
        if not cleaned:
            raise ValueError("Panel action did not include any runnable command lines.")
        return cleaned

    def _git_project_action_uses_head_fix_runner(self, action_key: str) -> bool:
        value = str(action_key or "")
        supported = {
            "initial-snapshot-required",
            "initialize_repository_here",
            "start_tracking_this_folder",
            "update_gitignore_before_initial_commit",
            "create_initial_snapshot",
            "prepare_commit_snapshot",
            "start_tracking_real_work",
            "track_selected_files",
            "track_all_safe_source_files",
            "record_current_work_as_commit",
        }
        return any(token in supported for token in re.split(r"[:/\s]+", value))

    def _git_project_head_fix_script(self) -> Path:
        script = self.repo_root / "tools" / "git" / "git_tool_fix_project_head.py"
        if not script.exists():
            raise ValueError(f"Git HEAD fixer script was not found at {script}")
        return script

    def _run_git_project_head_fix_runner(
        self,
        *,
        action_key: str,
        label: str,
        repo_dir: str,
        commands: list[str],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        repo = self.repo_path(str(state.get("repo") or repo_dir or "."))
        state = {**state, "repo": str(repo)}
        self._operation_log("Preparing disk-backed Git HEAD fixer.", {"action_key": action_key, "commands": len(commands), "repo": str(repo)})
        with tempfile.TemporaryDirectory(prefix="mc-git-head-fix-") as temp_dir:
            payload_path = Path(temp_dir) / "head_fix_payload.json"
            payload = {
                "action_key": action_key,
                "label": label,
                "repo_dir": repo_dir,
                "commands": commands,
                "state": state,
                "app_root": str(self.repo_root),
            }
            payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")
            result = self._run_command(
                [sys.executable, str(self._git_project_head_fix_script()), str(payload_path)],
                cwd=self.repo_root,
                timeout=180,
                not_found_stderr="Python executable is not available for the Git HEAD fixer.",
            )
        parsed: dict[str, Any] = {}
        stdout = result.get("stdout", "") or ""
        if stdout.strip():
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = {"raw_stdout": stdout}
        ok = result.get("returncode") == 0 and parsed.get("ok") is not False
        return {
            "ok": bool(ok),
            "mode": "git-tool-fix-project-head",
            "action_key": action_key,
            "label": label,
            "repo": str(repo),
            "returncode": result.get("returncode"),
            "stdout": stdout,
            "stderr": result.get("stderr", ""),
            "runner_result": parsed,
            "commands": commands,
            "state": state,
            "error": "" if ok else (parsed.get("error") or result.get("stderr") or "Git HEAD fixer failed."),
        }

    def _git_project_panel_runner_script(self) -> str:
        return (self.repo_root / "main_computer" / "git_panel_runner.py").read_text(encoding="utf-8")

    def _run_git_project_panel_action_runner(
        self,
        *,
        action_key: str,
        label: str,
        repo_dir: str,
        commands: list[str],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        repo = self.repo_path(str(state.get("repo") or repo_dir or "."))
        state = {**state, "repo": str(repo)}
        self._operation_log("Preparing disk-backed Git panel runner.", {"action_key": action_key, "commands": len(commands), "repo": str(repo)})
        with tempfile.TemporaryDirectory(prefix="mc-git-panel-runner-") as temp_dir:
            temp = Path(temp_dir)
            runner_path = temp / "panel_runner.py"
            payload_path = temp / "panel_payload.json"
            runner_path.write_text(self._git_project_panel_runner_script(), encoding="utf-8", newline="\n")
            payload = {
                "action_key": action_key,
                "label": label,
                "repo_dir": repo_dir,
                "commands": commands,
                "state": state,
                "app_root": str(self.repo_root),
            }
            payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")
            result = self._run_command(
                [sys.executable, str(runner_path), str(payload_path)],
                cwd=self.repo_root,
                timeout=180,
                not_found_stderr="Python executable is not available for the Git panel runner.",
            )
        parsed: dict[str, Any] = {}
        stdout = result.get("stdout", "") or ""
        if stdout.strip():
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = {"raw_stdout": stdout}
        ok = result.get("returncode") == 0 and parsed.get("ok") is not False
        return {
            "ok": bool(ok),
            "mode": "safe-python-panel-runner",
            "action_key": action_key,
            "label": label,
            "repo": str(repo),
            "returncode": result.get("returncode"),
            "stdout": stdout,
            "stderr": result.get("stderr", ""),
            "runner_result": parsed,
            "commands": commands,
            "state": state,
            "error": "" if ok else (parsed.get("error") or result.get("stderr") or "Panel runner failed."),
        }

    def run_git_console_command(self, command_text: str, repo_dir: str | None = ".") -> dict[str, Any]:
        args = self._git_console_command_args(command_text)
        if args[:1] == ["--git-dirty"]:
            return self._run_git_dirty_console(args[1:], repo_dir=repo_dir)
        if self._is_git_remote_add_command(args):
            return self._run_idempotent_git_remote_add(args[3], args[4], source_command=command_text, repo_dir=repo_dir)
        if args[:1] == ["--git"]:
            return self._run_direct_git_console(args[1:], repo_dir=repo_dir)
        result = self._run_git_control(args, allow_failure=True)
        return self._normalize_git_console_result(result)

    def plan_git_control(self, prompt: str = "") -> dict[str, Any]:
        args = ["--plan"]
        if prompt:
            args.extend(["--prompt", prompt])
        return self._run_git_control(args)

    def list_patches(self) -> dict[str, Any]:
        if not self.patching_available:
            return {
                "ok": False,
                "error": self._patch_import_error or "Patch harness is unavailable.",
                "incoming": [],
                "applied": [],
                "archive": [],
                "dry_runs": [],
                "counts": {"incoming": 0, "applied": 0, "archive": 0, "dry_runs": 0},
            }

        assert self._patch_layout is not None
        incoming = self._list_patch_dir(self._patch_layout.incoming_root)
        applied = self._list_patch_dir(self._patch_layout.applied_root)
        archive = self._list_patch_dir(self._patch_layout.archive_root)
        dry_runs = self.list_dry_runs(limit=10)["runs"]

        return {
            "ok": True,
            "incoming": incoming,
            "applied": applied,
            "archive": archive,
            "dry_runs": dry_runs,
            "counts": {
                "incoming": len(incoming),
                "applied": len(applied),
                "archive": len(archive),
                "dry_runs": len(dry_runs),
            },
            "roots": {
                "incoming": str(self._patch_layout.incoming_root),
                "applied": str(self._patch_layout.applied_root),
                "archive": str(self._patch_layout.archive_root),
                "dry_runs": str(self._patch_layout.dry_runs_root),
            },
        }

    def read_patch(self, patch_name: str) -> dict[str, Any]:
        patch_path = self._resolve_patch_path(patch_name)
        text = patch_path.read_text(encoding="utf-8", errors="surrogateescape")
        lines = text.splitlines()
        return {
            "ok": True,
            "name": patch_path.name,
            "path": str(patch_path),
            "chars": len(text),
            "line_count": len(lines),
            "preview": "\n".join(lines[:200]),
        }

    def list_dry_runs(self, *, limit: int = 20) -> dict[str, Any]:
        if not self.patching_available:
            return {
                "ok": False,
                "error": self._patch_import_error or "Patch harness is unavailable.",
                "runs": [],
            }

        assert self._patch_layout is not None
        root = self._patch_layout.dry_runs_root
        runs: list[dict[str, Any]] = []
        if root.exists():
            for path in sorted(root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
                if not path.is_dir():
                    continue
                manifest_path = path / "manifest.json"
                manifest = self._safe_json_read(manifest_path) if manifest_path.exists() else {}
                changes = manifest.get("changes", []) if isinstance(manifest, dict) else []
                skipped = manifest.get("skipped_already_applied", []) if isinstance(manifest, dict) else []
                runs.append(
                    {
                        "name": path.name,
                        "path": str(path),
                        "manifest_path": str(manifest_path),
                        "mtime": path.stat().st_mtime,
                        "change_count": len(changes) if isinstance(changes, list) else 0,
                        "skipped_count": len(skipped) if isinstance(skipped, list) else 0,
                    }
                )
                if len(runs) >= limit:
                    break

        return {
            "ok": True,
            "root": str(root),
            "runs": runs,
        }

    def read_dry_run(self, run_name: str) -> dict[str, Any]:
        if not self.patching_available:
            return {
                "ok": False,
                "error": self._patch_import_error or "Patch harness is unavailable.",
            }

        run_dir = self._resolve_dry_run_dir(run_name)
        manifest_path = run_dir / "manifest.json"
        manifest = self._safe_json_read(manifest_path) if manifest_path.exists() else {}
        preview_files = []
        files_root = run_dir / "files"
        if files_root.exists():
            for path in sorted(files_root.rglob("*")):
                if path.is_file():
                    preview_files.append(
                        {
                            "relative_path": path.relative_to(files_root).as_posix(),
                            "path": str(path),
                            "bytes": path.stat().st_size,
                        }
                    )

        deletions = []
        deletions_root = run_dir / "deletions"
        if deletions_root.exists():
            for path in sorted(deletions_root.rglob("*")):
                if path.is_file():
                    deletions.append(
                        {
                            "relative_path": path.relative_to(deletions_root).as_posix(),
                            "path": str(path),
                            "preview": path.read_text(encoding="utf-8", errors="surrogateescape"),
                        }
                    )

        return {
            "ok": True,
            "name": run_dir.name,
            "path": str(run_dir),
            "manifest_path": str(manifest_path),
            "manifest": manifest,
            "preview_files": preview_files,
            "deletions": deletions,
        }

    def apply_patch(
        self,
        *,
        patch_name: str,
        target_root: str | None = ".",
        dry_run: bool = True,
        reverse: bool = False,
        strict_root: bool = False,
    ) -> dict[str, Any]:
        if not self.patching_available:
            raise RuntimeError(self._patch_import_error or "Patch harness is unavailable.")

        assert self._patch_service is not None
        assert self._patch_layout is not None

        patch_path = self._resolve_patch_path(patch_name)
        target = self.repo_path(target_root)
        log_file = self._patch_layout.default_log_file(patch_path.name)
        json_report = self._patch_layout.default_json_report(log_file)
        backup_dir = None if dry_run else self._patch_layout.default_backup_dir(log_file.stem)
        dry_run_output_dir = self._patch_layout.default_dry_run_dir(patch_path.name) if dry_run else None

        result = self._patch_service.run_from_file(
            target_root=target,
            patch_file=patch_path,
            dry_run=dry_run,
            reverse=reverse,
            strict_root=strict_root,
            backup_dir=backup_dir,
            log_file=log_file,
            json_report=json_report,
            dry_run_output_dir=dry_run_output_dir,
        )

        return {
            "ok": result.status.value == "success",
            "patch_name": patch_path.name,
            "patch_path": str(patch_path),
            "target_root": str(target),
            "dry_run": dry_run,
            "reverse": reverse,
            "strict_root": strict_root,
            "log_file": str(log_file),
            "json_report": str(json_report),
            "backup_dir": str(backup_dir) if backup_dir is not None else None,
            "dry_run_output_dir": str(dry_run_output_dir) if dry_run_output_dir is not None else "",
            "result": result.to_dict(),
        }

    def _local_gitea_password(self) -> str:
        return os.environ.get(self.GIT_SERVER_LOCAL_PASSWORD_ENV, self.GIT_SERVER_LOCAL_PASSWORD_DEFAULT)

    def _docker_compose_exec(self, args: list[str], *, allow_failure: bool = False, timeout: int = 60) -> dict[str, Any]:
        return self._run_docker_compose(
            ["exec", "-T", "--user", "git", self.GIT_SERVER_SERVICE, *args],
            allow_failure=allow_failure,
            timeout=timeout,
        )

    def _compose_service_is_running(self, ps: dict[str, Any] | None) -> bool:
        if not ps or ps.get("returncode") != 0:
            return False
        combined = f"{ps.get('stdout', '')}\n{ps.get('stderr', '')}".lower()
        if not combined.strip():
            return False
        if "running" in combined or " up " in f" {combined} ":
            return True
        return False

    def _git_server_logs_tail(self, *, limit: int = 160) -> dict[str, Any]:
        return self._run_docker_compose(["logs", "--tail", str(limit), self.GIT_SERVER_SERVICE], allow_failure=True, timeout=45)

    def _gitea_unreachable_error(
        self,
        last_error: str,
        ps: dict[str, Any] | None,
        logs: dict[str, Any] | None,
    ) -> str:
        ps_text = f"{(ps or {}).get('stdout', '')}\n{(ps or {}).get('stderr', '')}".strip()
        log_text = f"{(logs or {}).get('stdout', '')}\n{(logs or {}).get('stderr', '')}".strip()
        if ps and not self._compose_service_is_running(ps):
            detail = "The Gitea container is not running after Docker reported it started."
            if log_text:
                tail = "\n".join(log_text.splitlines()[-12:])
                return f"{detail} Last HTTP error: {last_error}\n\nLast Gitea log lines:\n{tail}"
            if ps_text:
                return f"{detail} Last HTTP error: {last_error}\n\nCompose ps:\n{ps_text}"
            return f"{detail} Last HTTP error: {last_error}"
        if log_text:
            tail = "\n".join(log_text.splitlines()[-12:])
            return f"{last_error}\n\nLast Gitea log lines:\n{tail}"
        return last_error

    def _wait_for_gitea_http(self, *, timeout_s: int = 60) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        last_error = ""
        attempts = 0
        last_ps: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            attempts += 1
            try:
                request = urllib.request.Request(self.GIT_SERVER_WEB_URL, method="GET")
                with urllib.request.urlopen(request, timeout=4) as response:
                    if 200 <= response.status < 500:
                        return {
                            "ok": True,
                            "attempts": attempts,
                            "status": response.status,
                            "web_url": self.GIT_SERVER_WEB_URL,
                            "ps": last_ps,
                        }
            except Exception as exc:  # pragma: no cover - real Docker/network path
                last_error = str(exc)

            ps = self._run_docker_compose(["ps", self.GIT_SERVER_SERVICE], allow_failure=True, timeout=15)
            last_ps = ps
            if not self._compose_service_is_running(ps) and attempts >= 2:
                logs = self._git_server_logs_tail(limit=240)
                return {
                    "ok": False,
                    "attempts": attempts,
                    "web_url": self.GIT_SERVER_WEB_URL,
                    "error": self._gitea_unreachable_error(last_error, ps, logs),
                    "reason": "container-not-running",
                    "ps": ps,
                    "logs": logs,
                    "next_actions": [
                        "Inspect diagnostics.logs.stdout and diagnostics.logs.stderr in Git Server Output.",
                        "Run the Git Server Logs action for the full Gitea startup log.",
                        "If logs mention a bad app.ini/database/config value, reset the main-computer-gitea_gitea-data Docker volume before retrying.",
                    ],
                }
            time.sleep(2)

        logs = self._git_server_logs_tail(limit=240)
        return {
            "ok": False,
            "attempts": attempts,
            "web_url": self.GIT_SERVER_WEB_URL,
            "error": self._gitea_unreachable_error(last_error or "Timed out waiting for Gitea.", last_ps, logs),
            "reason": "http-timeout",
            "ps": last_ps,
            "logs": logs,
            "next_actions": [
                "Run the Git Server Logs action and inspect the Gitea startup output.",
                "Verify the standalone main-computer-gitea stack published localhost:3000 and that no other process is using the port.",
            ],
        }

    def _ensure_gitea_local_user(self) -> dict[str, Any]:
        username = self.GIT_SERVER_LOCAL_USER
        password = self._local_gitea_password()
        email = self.GIT_SERVER_LOCAL_EMAIL

        listing = self._docker_compose_exec(["gitea", "admin", "user", "list"], allow_failure=True, timeout=60)
        combined = f"{listing.get('stdout', '')}\n{listing.get('stderr', '')}"
        user_exists = re.search(rf"(^|\s){re.escape(username)}(\s|$)", combined, flags=re.MULTILINE) is not None

        if user_exists:
            password_result = self._docker_compose_exec(
                [
                    "gitea",
                    "admin",
                    "user",
                    "change-password",
                    "--username",
                    username,
                    "--password",
                    password,
                    "--must-change-password=false",
                ],
                allow_failure=True,
                timeout=60,
            )
            return {
                "ok": password_result["returncode"] == 0,
                "action": "change-password",
                "user": username,
                "listing": listing,
                "result": password_result,
                "error": "" if password_result["returncode"] == 0 else (password_result["stderr"] or password_result["stdout"]),
            }

        create = self._docker_compose_exec(
            [
                "gitea",
                "admin",
                "user",
                "create",
                "--username",
                username,
                "--password",
                password,
                "--email",
                email,
                "--admin",
                "--must-change-password=false",
            ],
            allow_failure=True,
            timeout=60,
        )
        already_exists = create["returncode"] != 0 and "already exists" in f"{create.get('stdout', '')}\n{create.get('stderr', '')}".lower()
        ok = create["returncode"] == 0 or already_exists
        if already_exists:
            password_result = self._docker_compose_exec(
                [
                    "gitea",
                    "admin",
                    "user",
                    "change-password",
                    "--username",
                    username,
                    "--password",
                    password,
                    "--must-change-password=false",
                ],
                allow_failure=True,
                timeout=60,
            )
            ok = password_result["returncode"] == 0
            create = {**create, "password_result": password_result}
        return {
            "ok": ok,
            "action": "create" if create.get("returncode") == 0 else "reuse-existing",
            "user": username,
            "listing": listing,
            "result": create,
            "error": "" if ok else (create["stderr"] or create["stdout"] or "Could not create local Gitea user."),
        }

    def _create_gitea_access_token(self, username: str) -> dict[str, Any]:
        password = self._local_gitea_password()
        token_name = f"main-computer-ui-{int(time.time())}"
        payload = {"name": token_name, "scopes": ["all"]}
        auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        response = self._gitea_json_request(
            "POST",
            f"/api/v1/users/{urllib.parse.quote(username)}/tokens",
            payload=payload,
            headers={"Authorization": f"Basic {auth}"},
            allow_statuses={200, 201, 409},
        )
        if response.get("ok") and isinstance(response.get("json"), dict):
            token = response["json"].get("sha1") or response["json"].get("token")
            if token:
                return {"ok": True, "token": token, "token_name": token_name, "status": response.get("status")}
        return {
            "ok": False,
            "token_name": token_name,
            "status": response.get("status"),
            "error": response.get("error") or "Gitea did not return an access token.",
            "response": response,
        }

    def _ensure_gitea_repo(self, *, token: str, owner: str, repo_name: str) -> dict[str, Any]:
        owner_q = urllib.parse.quote(owner, safe="")
        repo_q = urllib.parse.quote(repo_name, safe="")
        existing = self._gitea_json_request(
            "GET",
            f"/api/v1/repos/{owner_q}/{repo_q}",
            headers={"Authorization": f"token {token}"},
            allow_statuses={200, 404},
        )
        if existing.get("status") == 200:
            return {"ok": True, "exists": True, "created": False, "repo": existing.get("json")}

        create = self._gitea_json_request(
            "POST",
            "/api/v1/user/repos",
            payload={
                "name": repo_name,
                "auto_init": False,
                "private": False,
                "default_branch": "main",
                "description": "Local Main Computer repository managed from the Git tools page.",
            },
            headers={"Authorization": f"token {token}"},
            allow_statuses={200, 201, 409, 422},
        )
        if create.get("status") in {200, 201, 409, 422}:
            verify = self._gitea_json_request(
                "GET",
                f"/api/v1/repos/{owner_q}/{repo_q}",
                headers={"Authorization": f"token {token}"},
                allow_statuses={200, 404},
            )
            if verify.get("status") == 200:
                return {
                    "ok": True,
                    "exists": True,
                    "created": create.get("status") in {200, 201},
                    "repo": verify.get("json"),
                    "create_status": create.get("status"),
                }

        return {
            "ok": False,
            "exists": False,
            "created": False,
            "error": create.get("error") or existing.get("error") or "Could not create Gitea repository.",
            "existing": existing,
            "create": create,
        }

    def _gitea_json_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        allow_statuses: set[int] | None = None,
        timeout: int = 20,
    ) -> dict[str, Any]:
        allow = allow_statuses or {200, 201, 204}
        body = None
        request_headers = {"Accept": "application/json", **(headers or {})}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        url = f"{self.GIT_SERVER_WEB_URL.rstrip('/')}{path}"
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                parsed: Any = {}
                if raw.strip():
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError:
                        parsed = {"raw": raw}
                return {"ok": response.status in allow, "status": response.status, "json": parsed, "url": url}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            parsed: Any = {}
            if raw.strip():
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = {"raw": raw}
            ok = exc.code in allow
            message = ""
            if isinstance(parsed, dict):
                message = str(parsed.get("message") or parsed.get("error") or "")
            return {"ok": ok, "status": exc.code, "json": parsed, "url": url, "error": message or raw or str(exc)}
        except Exception as exc:  # pragma: no cover - real Docker/network path
            return {"ok": False, "status": 0, "url": url, "error": str(exc)}

    def _redact_step(self, value: Any) -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                lowered = key.lower()
                if "token" in lowered or "password" in lowered or "authorization" in lowered:
                    redacted[key] = "<redacted>"
                else:
                    redacted[key] = self._redact_step(item)
            return redacted
        if isinstance(value, list):
            return [self._redact_step(item) for item in value]
        if isinstance(value, str):
            password = self._local_gitea_password()
            if password and password in value:
                return value.replace(password, "<redacted>")
        return value

    def _clean_external_git_url(self, raw: str) -> str:
        cleaned = str(raw or "").strip()
        if not cleaned:
            raise ValueError("External Git remote URL is required.")
        if any(ord(ch) < 32 for ch in cleaned) or "\n" in cleaned or "\r" in cleaned:
            raise ValueError("External Git remote URL must be a single line.")
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", cleaned):
            parsed = urllib.parse.urlsplit(cleaned)
            if parsed.scheme not in {"http", "https", "ssh", "git"}:
                raise ValueError("External Git remote URL must use http, https, ssh, or git.")
            if not parsed.netloc:
                raise ValueError("External Git remote URL is missing a host.")
            return cleaned
        if re.match(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:.+$", cleaned):
            return cleaned
        raise ValueError("External Git remote URL must look like https://host/owner/repo.git or git@host:owner/repo.git.")

    def _git_server_base_status(self) -> dict[str, Any]:
        compose_file = self.repo_root / self.GIT_SERVER_COMPOSE_FILE
        capabilities = self.git_server_capabilities()
        return {
            "ok": True,
            "service": self.GIT_SERVER_SERVICE,
            "profile": "",
            "compose_project": self.GIT_SERVER_COMPOSE_PROJECT,
            "compose_file": str(compose_file),
            "compose_file_exists": compose_file.exists(),
            "docker_available": shutil.which("docker") is not None,
            "configured": capabilities["available"],
            "state": "unavailable",
            "running": False,
            "web_url": self.GIT_SERVER_WEB_URL,
            "ssh_available": self.GIT_SERVER_SSH_AVAILABLE,
            "clone_examples": capabilities["clone_examples"],
            "remote_command_presets": capabilities["remote_command_presets"],
            "commands": {
                "start": capabilities["start_command"],
                "stop": capabilities["stop_command"],
                "status": capabilities["status_command"],
                "logs": f"{self._git_server_compose_command_text()} logs --tail 120 {self.GIT_SERVER_SERVICE}",
            },
        }

    def _git_server_compose_command_text(self) -> str:
        return f"docker compose --project-name {self.GIT_SERVER_COMPOSE_PROJECT} -f {self.GIT_SERVER_COMPOSE_FILE}"

    def _run_docker_compose(
        self,
        args: list[str],
        *,
        allow_failure: bool = False,
        timeout: int = 30,
    ) -> dict[str, Any]:
        compose_file = self.repo_root / self.GIT_SERVER_COMPOSE_FILE
        command = [
            "docker",
            "compose",
            "--project-name",
            self.GIT_SERVER_COMPOSE_PROJECT,
            "-f",
            str(compose_file),
            *args,
        ]
        result = self._run_command(command, cwd=self.repo_root, timeout=timeout, not_found_stderr="Docker CLI is not available.")
        if result["returncode"] != 0 and not allow_failure:
            raise RuntimeError(result["stderr"].strip() or "docker compose command failed")
        return result

    def _clean_git_remote_name(self, raw: str) -> str:
        cleaned = str(raw or "origin").strip() or "origin"
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", cleaned):
            raise ValueError("Git remote name may only contain letters, numbers, dot, underscore, dash, or slash.")
        if cleaned.startswith(("-", "/", ".")) or cleaned.endswith(("/", ".")) or ".." in cleaned:
            raise ValueError("Git remote name is not safe.")
        return cleaned

    def _clean_git_server_remote_segment(self, raw: str, label: str) -> str:
        cleaned = re.sub(r"\s+", "-", str(raw or "").strip())
        if not cleaned:
            raise ValueError(f"Git server remote {label} is required.")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", cleaned):
            raise ValueError(f"Git server remote {label} may only contain letters, numbers, dot, underscore, or dash.")
        if cleaned.startswith(("-", ".")) or cleaned.endswith(".") or ".." in cleaned:
            raise ValueError(f"Git server remote {label} is not safe.")
        return cleaned

    def _is_git_remote_add_command(self, args: list[str]) -> bool:
        return len(args) == 5 and args[:3] == ["--git", "remote", "add"]

    def _normalize_git_console_result(self, result: dict[str, Any]) -> dict[str, Any]:
        if result.get("ok"):
            return result
        if result.get("error"):
            return result
        nested = result.get("result") if isinstance(result.get("result"), dict) else {}
        message = (
            str(result.get("stderr") or "").strip()
            or str(result.get("stdout") or "").strip()
            or str(nested.get("stderr") or "").strip()
            or str(nested.get("stdout") or "").strip()
            or "Git console command failed."
        )
        return {**result, "error": message}

    def _run_idempotent_git_remote_add(self, remote: str, url: str, *, source_command: str = "", repo_dir: str | None = ".") -> dict[str, Any]:
        remote_name = self._clean_git_remote_name(remote)
        remote_url = str(url or "").strip()
        if not remote_url:
            raise ValueError("Remote URL is required.")

        repo = self.repo_path(repo_dir)
        git_root, git_init = self._ensure_git_root_for_remote_setup(repo)
        if git_root is None:
            return {
                "ok": False,
                "mode": "idempotent-git-remote-add",
                "action": "failed",
                "error": git_init.get("error") or "Directory is not inside a git worktree and could not be initialized.",
                "repo": str(repo),
                "remote": remote_name,
                "url": remote_url,
                "result": git_init.get("top"),
                "git_init": git_init,
            }
        existing = self._run_git(git_root, ["remote", "get-url", remote_name], check=False)
        if existing["returncode"] == 0:
            existing_url = existing["stdout"].strip()
            if existing_url == remote_url:
                action = "already-configured"
                result = {
                    "command": ["git", "remote", "add", remote_name, remote_url],
                    "returncode": 0,
                    "stdout": f"Remote {remote_name!r} already points at {remote_url}. Nothing changed.\n",
                    "stderr": "",
                }
            else:
                action = "set-url"
                result = self._run_git(git_root, ["remote", "set-url", remote_name, remote_url], check=False)
        else:
            action = "add"
            result = self._run_git(git_root, ["remote", "add", remote_name, remote_url], check=False)

        remotes = self._run_git(git_root, ["remote", "-v"], check=False)
        ok = result["returncode"] == 0
        payload = {
            "ok": ok,
            "mode": "idempotent-git-remote-add",
            "action": action,
            "repo": str(git_root),
            "repo_dir": str(repo),
            "git_init": git_init,
            "remote": remote_name,
            "url": remote_url,
            "command": result["command"],
            "source_command": source_command,
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "result": result,
            "existing_remote": existing,
            "remotes": remotes,
        }
        if not ok:
            payload["error"] = result["stderr"] or result["stdout"] or "Git remote configuration failed."
        return payload

    def _run_git_control(self, args: list[str], *, allow_failure: bool = False) -> dict[str, Any]:
        script = self.repo_root / "git-control.py"
        if not script.exists():
            raise RuntimeError("git-control.py is not available at the repository root.")
        result = self._run_command([sys.executable, str(script), "--json", "--repo", str(self.repo_root), *args], cwd=self.repo_root)
        try:
            payload = json.loads(result["stdout"] or "{}")
        except json.JSONDecodeError:
            payload = {
                "ok": False,
                "error": "git-control.py returned non-JSON output.",
                "stdout": result["stdout"],
                "stderr": result["stderr"],
            }
        payload.setdefault("returncode", result["returncode"])
        payload.setdefault("stdout", result["stdout"])
        payload.setdefault("stderr", result["stderr"])
        if result["returncode"] != 0 and not allow_failure:
            error = payload.get("error") or result["stderr"].strip() or "git-control.py command failed."
            raise RuntimeError(str(error))
        return payload

    def _run_direct_git_console(self, git_args: list[str], repo_dir: str | None = ".") -> dict[str, Any]:
        if not git_args:
            raise ValueError("A plain git command needs git arguments.")
        repo = self.repo_path(repo_dir)
        result = self._run_git(repo, git_args, check=False)
        return {
            "ok": result["returncode"] == 0,
            "mode": "direct-git-fallback",
            "warning": "git-control.py is missing; ran the plain git command directly and did not save a shim.",
            "repo": str(repo),
            "command": result["command"],
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "result": result,
        }

    def _run_git_dirty_console(self, args: list[str], repo_dir: str | None = ".") -> dict[str, Any]:
        script = self.repo_root / "git_dirty.py"
        if not script.exists():
            raise RuntimeError("git_dirty.py is not available at the repository root.")
        result = self._run_command([sys.executable, str(script), *args], cwd=self.repo_root, timeout=60)
        return {
            "ok": result["returncode"] == 0,
            "mode": "direct-git-dirty",
            "repo": str(self.repo_path(repo_dir)),
            "command": result["command"],
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "result": result,
        }

    def _git_console_command_args(self, command_text: str) -> list[str]:
        text = str(command_text or "").strip()
        if not text:
            raise ValueError("Git console command is required.")
        first_line = next(
            (
                line.strip()
                for line in text.splitlines()
                if line.strip() and not line.strip().startswith(("#", "//"))
            ),
            "",
        )
        if not first_line:
            raise ValueError("Git console command is required.")

        # Accept plain git commands for fast de novo UI use. Comment lines in
        # UI command boxes are documentation and are skipped here.
        try:
            parts = shlex.split(first_line, posix=os.name != "nt")
        except ValueError:
            parts = first_line.split()
        if not parts:
            raise ValueError("Git console command is required.")

        executable = Path(parts[0].replace("\\", "/")).name.lower()
        if executable == "git":
            if len(parts) < 2:
                raise ValueError("A plain git command needs git arguments.")
            return ["--git", *parts[1:]]

        if executable in {"python", "python.exe", "python3", "python3.exe", "py"} and len(parts) >= 2:
            script_name = Path(parts[1].replace("\\", "/")).name.lower()
            if script_name == "git-control.py":
                args = parts[2:]
                if not args:
                    raise ValueError("python git-control.py command is missing arguments.")
                return args
            if script_name == "git_dirty.py":
                args = parts[2:]
                if not args:
                    raise ValueError("python git_dirty.py command is missing arguments.")
                return ["--git-dirty", *args]

        raise ValueError("Git console accepts `git ...`, `python git-control.py ...`, or `python git_dirty.py ...` commands.")

    def _resolve_patch_path(self, patch_name: str) -> Path:
        if not self.patching_available:
            raise RuntimeError(self._patch_import_error or "Patch harness is unavailable.")
        assert self._patch_layout is not None
        return self._patch_layout.resolve_patch_file(patch_name, cwd=self.repo_root)

    def _resolve_dry_run_dir(self, run_name: str) -> Path:
        assert self._patch_layout is not None
        candidate = Path(run_name)
        if not candidate.is_absolute():
            candidate = self._patch_layout.dry_runs_root / run_name
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self._patch_layout.dry_runs_root.resolve())
        except ValueError as exc:
            raise ValueError("Dry-run preview path must stay inside tools/patching/reports/dry_runs.") from exc
        if not resolved.exists() or not resolved.is_dir():
            raise ValueError("Requested dry-run preview does not exist.")
        return resolved

    def _list_patch_dir(self, root: Path) -> list[dict[str, Any]]:
        if not root.exists():
            return []
        items: list[dict[str, Any]] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".patch", ".diff", ".md", ".txt"}:
                continue
            stat = path.stat()
            items.append(
                {
                    "name": path.name,
                    "relative_path": path.relative_to(root).as_posix(),
                    "path": str(path),
                    "bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
        return items

    def _load_patch_service(self) -> None:
        patch_package_root = self.patching_root
        if not patch_package_root.exists():
            self._patch_import_error = f"Missing patching root: {patch_package_root}"
            return

        if str(patch_package_root) not in sys.path:
            sys.path.insert(0, str(patch_package_root))

        try:
            from smart_patch_harness.api import PatchService
            from smart_patch_harness.layout import RepositoryLayout
        except Exception as exc:  # pragma: no cover - surfaced to API callers
            self._patch_import_error = f"Patch harness is unavailable: {exc}"
            self._patch_service = None
            self._patch_layout = None
            return

        self._patch_service = PatchService()
        self._patch_layout = RepositoryLayout.from_package_file(
            patch_package_root / "smart_patch_harness" / "__init__.py"
        )
        self._patch_import_error = None

    def _run_git(self, repo: Path, args: list[str], *, check: bool) -> dict[str, Any]:
        result = self._run_command(["git", *args], cwd=repo)
        if check and result["returncode"] != 0:
            raise RuntimeError(result["stderr"].strip() or "git command failed")
        return result

    def _safe_json_read(self, path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8", errors="surrogateescape"))
        except Exception:
            return {}

    def _ahead_behind(self, header: str) -> tuple[int, int]:
        ahead = behind = 0
        if "ahead " in header:
            try:
                ahead = int(header.split("ahead ", 1)[1].split("]", 1)[0].split(",", 1)[0].strip())
            except Exception:
                ahead = 0
        if "behind " in header:
            try:
                behind = int(header.split("behind ", 1)[1].split("]", 1)[0].split(",", 1)[0].strip())
            except Exception:
                behind = 0
        return ahead, behind
