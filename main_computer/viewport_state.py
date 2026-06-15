from __future__ import annotations

import base64
import csv
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import os
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import asdict, replace
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
import uuid

from main_computer.catalog import PRIORITY_PROJECTS
from main_computer.aider_web_context import AiderWebContextStore
from main_computer.aider_agent import (
    AiderAgentConfig,
    AiderActionRequest,
    AiderValidationError,
    append_aider_log,
    parse_file_list,
    prepare_aider_action,
    run_aider_action,
)
from main_computer.config import MainComputerConfig
from main_computer.energy import EnergyCreditLedger
from main_computer.energy_chain import EnergyChainClient
from main_computer.git_tools import GitToolsService
from main_computer.governance import bridge_governance_status
from main_computer.heartbeat import HeartbeatConfig, ensure_heartbeat_service
from main_computer.mathics_bridge import evaluate_mathics_expression
from main_computer.chat_console import (
    ai_response_to_parts,
    build_notebook_ai_messages,
    build_output_cell,
    exact_act_command_source_to_parts,
    mathics_result_to_parts,
    terminal_result_to_parts,
    validate_evaluation_cell,
)
from main_computer.task_manager import TaskManagerService
from main_computer.models import ChatMessage
from main_computer.providers import OllamaProvider
from main_computer.revision import DebugAssetRevisionControl, RevisionControl
from main_computer.router import MainComputer
from main_computer.terminal_suggestions import (
    normalize_terminal_risk,
    parse_terminal_suggestion,
    validate_terminal_command,
)
from main_computer.xlag_contract import xlag_contract_status


VIEWPORT_PID_FILENAME = ".main_computer_viewport.pid"


def viewport_ollama_provider_class() -> type:
    """Return the Ollama provider, honoring legacy patches on main_computer.viewport."""

    viewport_module = sys.modules.get("main_computer.viewport")
    patched = getattr(viewport_module, "OllamaProvider", None) if viewport_module is not None else None
    return patched or OllamaProvider


class GameEditorConflict(RuntimeError):
    """Raised when a guarded Game Editor write would overwrite newer disk data."""


WORKSPACE_TIMESTAMP_EXCLUDED_DIRS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    ".venv",
    "node_modules",
    "revision_control",
    "debug_assets",
    "debug_asset_revisions",
    "energy_credits",
}
WORKSPACE_TIMESTAMP_EXCLUDED_PREFIXES = (
    "diagnostics_output",
    "harness_output",
)
WORKSPACE_TIMESTAMP_EXCLUDED_SUFFIXES = {
    ".log",
    ".pyc",
    ".pyo",
}
WORKSPACE_TIMESTAMP_RELEVANT_SUFFIXES = {
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".py",
    ".toml",
    ".yaml",
    ".yml",
}
WORKSPACE_TIMESTAMP_RELEVANT_FILENAMES = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
}
WORKSPACE_TIMESTAMP_RELEVANT_ROOT_DIRS = {
    "assets",
    "main_computer",
    "static",
    "templates",
}


def _workspace_timestamp_should_skip(path: Path, root: Path) -> bool:
    relative_parts = path.relative_to(root).parts
    if any(part in WORKSPACE_TIMESTAMP_EXCLUDED_DIRS for part in relative_parts):
        return True
    if any(part.startswith(WORKSPACE_TIMESTAMP_EXCLUDED_PREFIXES) for part in relative_parts):
        return True
    return path.is_file() and path.suffix.lower() in WORKSPACE_TIMESTAMP_EXCLUDED_SUFFIXES


def _workspace_timestamp_is_relevant_file(path: Path, root: Path) -> bool:
    if not path.is_file():
        return False
    if _workspace_timestamp_should_skip(path, root):
        return False
    suffix = path.suffix.lower()
    return suffix in WORKSPACE_TIMESTAMP_RELEVANT_SUFFIXES or path.name in WORKSPACE_TIMESTAMP_RELEVANT_FILENAMES


def _workspace_timestamp_relevant_roots(root: Path) -> list[Path]:
    relevant_roots: list[Path] = []
    for name in sorted(WORKSPACE_TIMESTAMP_RELEVANT_ROOT_DIRS):
        candidate = root / name
        if candidate.exists() and candidate.is_dir():
            relevant_roots.append(candidate)
    return relevant_roots


def _iter_workspace_timestamp_files(root: Path) -> list[Path]:
    relevant: list[Path] = []
    if not root.exists() or not root.is_dir():
        return relevant

    for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if child.is_file() and _workspace_timestamp_is_relevant_file(child, root):
            relevant.append(child)

    for scan_root in _workspace_timestamp_relevant_roots(root):
        for directory, dirnames, filenames in os.walk(scan_root):
            current_dir = Path(directory)
            dirnames[:] = [
                name
                for name in dirnames
                if not _workspace_timestamp_should_skip(current_dir / name, root)
            ]
            for filename in filenames:
                candidate = current_dir / filename
                if _workspace_timestamp_is_relevant_file(candidate, root):
                    relevant.append(candidate)
    return relevant


def _control_root_path(default_root: Path) -> Path:
    value = os.environ.get("MAIN_COMPUTER_CONTROL_ROOT", "").strip()
    if not value:
        return default_root
    try:
        return Path(value).expanduser().resolve()
    except Exception:
        return Path(value).expanduser()


def _viewport_pid_path(root: Path) -> Path:
    return root / VIEWPORT_PID_FILENAME


def _write_viewport_pid_file(path: Path, pid: int) -> None:
    path.write_text(f"{int(pid)}\n", encoding="utf-8")


def _clear_viewport_pid_file(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


APPLICATION_ROUTE_PREFIXES = ("/applications", "/apps", "/app")
APPLICATION_ROUTE_NAMES = {
    "webgl",
    "calculator",
    "document",
    "spreadsheet",
    "onlyoffice",
    "task-manager",
    "terminal",
    "chat-console",
    "email",
    "git-tools",
    "code-editor",
    "file-explorer",
    "game-editor",
    "website-builder",
    "mcel-lab",
    "worker",
    "wallet",
}
APPLICATION_ROUTE_ALIASES = {
    "layout-builder": "game-editor",
    "web-test-bed": "mcel-lab",
}
APPLICATION_ROUTE_DEFAULT = "calculator"
APPLICATION_TASK_MANAGER_TABS = {
    "server-processes",
    "all-processes",
    "connections",
    "hardware",
}
APPLICATION_WEBSITE_BUILDER_ROUTE = "website-builder"
APPLICATION_WEBSITE_BUILDER_SITE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")


def _application_route_parts(path: str) -> list[str]:
    route_path = urlsplit(path).path.rstrip("/") or "/"
    return [part for part in route_path.split("/") if part]


def _website_builder_route_site_id(path: str) -> str | None:
    parts = _application_route_parts(path)
    if len(parts) != 3:
        return None
    if f"/{parts[0]}" not in APPLICATION_ROUTE_PREFIXES:
        return None
    candidate = APPLICATION_ROUTE_ALIASES.get(parts[1], parts[1])
    if candidate != APPLICATION_WEBSITE_BUILDER_ROUTE:
        return None
    site_id = parts[2].strip()
    if not APPLICATION_WEBSITE_BUILDER_SITE_RE.fullmatch(site_id):
        return None
    return site_id


def _is_website_builder_application_route(path: str) -> bool:
    route_path = urlsplit(path).path.rstrip("/") or "/"
    for prefix in APPLICATION_ROUTE_PREFIXES:
        if route_path == f"{prefix}/{APPLICATION_WEBSITE_BUILDER_ROUTE}":
            return True
    return _website_builder_route_site_id(path) is not None


def _application_route_target(path: str) -> str | None:
    route_path = urlsplit(path).path.rstrip("/") or "/"
    if route_path in APPLICATION_ROUTE_PREFIXES:
        return APPLICATION_ROUTE_DEFAULT
    parts = _application_route_parts(path)
    if len(parts) not in {2, 3}:
        return None
    if f"/{parts[0]}" not in APPLICATION_ROUTE_PREFIXES:
        return None
    candidate = parts[1]
    candidate = APPLICATION_ROUTE_ALIASES.get(candidate, candidate)
    if candidate not in APPLICATION_ROUTE_NAMES:
        return None
    if len(parts) == 3:
        if candidate == APPLICATION_WEBSITE_BUILDER_ROUTE:
            return candidate if _website_builder_route_site_id(path) else None
        if candidate == "game-editor":
            return None
        if candidate != "task-manager" or parts[2] not in APPLICATION_TASK_MANAGER_TABS:
            return None
    return candidate


from main_computer.viewport_pages import (
    APPLICATIONS_INDEX_HTML,
    DEBUG_GRAPHICAL_INDEX_HTML,
    DEBUG_TEXT_INDEX_HTML,
    ENERGY_INDEX_HTML,
    GRAPHICAL_INDEX_HTML,
    REVISION_INDEX_HTML,
    TEXT_INDEX_HTML,
)



def _aider_job_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _aider_job_log_excerpt(value: str, limit: int = 4000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[truncated after {limit} characters]"


def _aider_job_tail(value: str, limit: int = 12000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return f"[truncated to last {limit} characters]\n" + text[-limit:]


def _aider_job_run_excerpt(request: AiderActionRequest, result: Any) -> str:
    stdout = str(getattr(result, "stdout", "") or "")
    stderr = str(getattr(result, "stderr", "") or "")
    error = str(getattr(result, "error", "") or "")
    if stdout.strip():
        return stdout
    if stderr.strip():
        return stderr
    if error:
        return error
    if getattr(result, "ok", False) and request.dry_run:
        return "Dry run completed. No changes were applied."
    if getattr(result, "ok", False):
        return "Aider completed."
    return "Aider failed."


class AiderActionJobRegistry:
    """Track background Aider runs so browser refreshes can reattach."""

    def __init__(self, server: "ViewportServer") -> None:
        self.server = server
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._max_finished_jobs = 80

    def start_run(
        self,
        *,
        request: AiderActionRequest,
        aider_history: dict[str, str],
        prepared: Any,
    ) -> dict[str, Any]:
        archive_id = str(aider_history.get("archive_id") or "").strip()
        session_id = str(aider_history.get("session_id") or "").strip()
        if not archive_id:
            raise AiderValidationError("Aider archive id is required before starting a background run.")
        job_id = uuid.uuid4().hex[:12]
        now = _aider_job_now()
        response_file = self.server.debug_root / "aider_responses" / f"{job_id}.txt"
        response_file.parent.mkdir(parents=True, exist_ok=True)
        job = {
            "id": job_id,
            "kind": "run",
            "status": "running",
            "archive_id": archive_id,
            "session_id": session_id,
            "repo_dir": str(getattr(prepared, "repo_dir", request.repo_dir)),
            "files": list(request.files),
            "file_count": len(request.files),
            "instruction": request.instruction,
            "dry_run": bool(request.dry_run),
            "fallback": bool(request.fallback or self.server.aider_config.fallback),
            "command": list(getattr(prepared, "command", [])),
            "timeout_seconds": int(getattr(prepared, "timeout_seconds", request.timeout_seconds or self.server.aider_config.timeout_seconds)),
            "started_at": now,
            "updated_at": now,
            "finished_at": None,
            "result": None,
            "error": None,
            "response_file": str(response_file),
            "output_excerpt": "",
            "stdout_excerpt": "",
            "stderr_excerpt": "",
        }
        with self._lock:
            self._jobs[job_id] = job
            self._prune_finished_locked()
        thread = threading.Thread(
            target=self._run_aider_job,
            args=(job_id, request, dict(aider_history)),
            daemon=True,
            name=f"aider-run-{job_id}",
        )
        thread.start()
        return self._public_job(job)

    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public_job(job) for job in sorted(self._jobs.values(), key=lambda item: str(item.get("started_at", "")), reverse=True)]

    def status_for_archive(self, archive_id: str) -> list[dict[str, Any]]:
        archive_id = str(archive_id or "").strip()
        if not archive_id:
            return []
        with self._lock:
            jobs = [job for job in self._jobs.values() if str(job.get("archive_id") or "") == archive_id]
            return [self._public_job(job) for job in sorted(jobs, key=lambda item: str(item.get("started_at", "")), reverse=True)]

    def _run_aider_job(self, job_id: str, request: AiderActionRequest, aider_history: dict[str, str]) -> None:
        try:
            with self._lock:
                job = self._jobs.get(job_id)
                response_file = Path(str(job.get("response_file"))) if job and job.get("response_file") else None

            def on_output(stream_name: str, text: str) -> None:
                self._append_job_output(job_id, stream_name, text)

            result = run_aider_action(
                request,
                self.server.aider_config,
                output_callback=on_output,
                response_file=response_file,
            )
            result_payload = asdict(result)
            now = _aider_job_now()
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job["status"] = "complete" if result.ok else "failed"
                    job["result"] = result_payload
                    job["error"] = result.error
                    job["returncode"] = result.returncode
                    job["duration_ms"] = result.duration_ms
                    job["timed_out"] = result.timed_out
                    job["first_output_ms"] = result.first_output_ms
                    job["first_output_stream"] = result.first_output_stream
                    job["stdout_excerpt"] = _aider_job_tail(result.stdout)
                    job["stderr_excerpt"] = _aider_job_tail(result.stderr)
                    job["output_excerpt"] = _aider_job_tail(
                        "\n".join(part for part in [result.stdout, result.stderr, result.error or ""] if part)
                    )
                    job["finished_at"] = now
                    job["updated_at"] = now
                    self._prune_finished_locked()
            self._write_run_log(job_id, request, result, aider_history)
            self.server.aider_web_context.append_entry_to_archive(
                str(aider_history.get("archive_id") or ""),
                kind="run",
                repo_dir=result.repo_dir,
                files=request.files,
                instruction=request.instruction,
                dry_run=request.dry_run,
                ok=result.ok,
                route="/api/applications/aider/run",
                returncode=result.returncode,
                duration_ms=result.duration_ms,
                result_excerpt=_aider_job_log_excerpt(_aider_job_run_excerpt(request, result), limit=1200),
                metadata={
                    "timed_out": result.timed_out,
                    "error": result.error,
                    "aider_history": aider_history,
                    "job_id": job_id,
                    "fallback": bool(request.fallback or self.server.aider_config.fallback),
                    "first_output_ms": result.first_output_ms,
                    "first_output_stream": result.first_output_stream,
                },
            )
        except Exception as exc:
            now = _aider_job_now()
            error = str(exc)
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job["status"] = "failed"
                    job["error"] = error
                    job["finished_at"] = now
                    job["updated_at"] = now
                    self._prune_finished_locked()
            try:
                self.server.aider_web_context.append_entry_to_archive(
                    str(aider_history.get("archive_id") or ""),
                    kind="run",
                    repo_dir=request.repo_dir,
                    files=request.files,
                    instruction=request.instruction,
                    dry_run=request.dry_run,
                    ok=False,
                    route="/api/applications/aider/run",
                    returncode=None,
                    duration_ms=None,
                    result_excerpt=error,
                    metadata={"error": error, "aider_history": aider_history, "job_id": job_id},
                )
            except Exception as append_exc:
                self.server.signal("aider-job-context-append-error", job_id=job_id, error=append_exc)
            try:
                append_aider_log(
                    self.server.debug_root / "aider.log",
                    "run_error",
                    repo_dir=request.repo_dir,
                    files=request.files,
                    instruction=request.instruction,
                    dry_run=request.dry_run,
                    aider_history=aider_history,
                    job_id=job_id,
                    error=error,
                )
            except Exception as log_exc:
                self.server.signal("aider-job-log-error", job_id=job_id, error=log_exc)

    def _append_job_output(self, job_id: str, stream_name: str, text: str) -> None:
        if not text:
            return
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            key = "stderr_excerpt" if stream_name == "stderr" else "stdout_excerpt"
            job[key] = _aider_job_tail(str(job.get(key) or "") + text)
            job["output_excerpt"] = _aider_job_tail(str(job.get("output_excerpt") or "") + text)
            job["updated_at"] = _aider_job_now()

    def _write_run_log(self, job_id: str, request: AiderActionRequest, result: Any, aider_history: dict[str, str]) -> None:
        try:
            append_aider_log(
                self.server.debug_root / "aider.log",
                "run",
                repo_dir=result.repo_dir,
                git_root=result.git_root,
                files=request.files,
                instruction=request.instruction,
                model=request.model or self.server.aider_config.default_model,
                dry_run=request.dry_run,
                timeout_seconds=result.timeout_seconds,
                command=result.command,
                ok=result.ok,
                aider_history=aider_history,
                job_id=job_id,
                returncode=result.returncode,
                duration_ms=result.duration_ms,
                timed_out=result.timed_out,
                error=result.error,
                fallback=bool(request.fallback or self.server.aider_config.fallback),
                first_output_ms=result.first_output_ms,
                first_output_stream=result.first_output_stream,
                stdout_chars=len(result.stdout),
                stderr_chars=len(result.stderr),
                stdout_excerpt=_aider_job_log_excerpt(result.stdout),
                stderr_excerpt=_aider_job_log_excerpt(result.stderr),
            )
        except Exception as exc:
            self.server.signal("aider-job-log-error", error=exc)

    def _public_job(self, job: dict[str, Any]) -> dict[str, Any]:
        return dict(job)

    def _prune_finished_locked(self) -> None:
        finished = [
            job for job in self._jobs.values()
            if str(job.get("status") or "") in {"complete", "failed", "cancelled"}
        ]
        if len(finished) <= self._max_finished_jobs:
            return
        finished.sort(key=lambda item: str(item.get("finished_at") or item.get("updated_at") or ""))
        remove_count = len(finished) - self._max_finished_jobs
        for job in finished[:remove_count]:
            self._jobs.pop(str(job.get("id") or ""), None)

__all__ = [name for name in globals() if not name.startswith('__')]
