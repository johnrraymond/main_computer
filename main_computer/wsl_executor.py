from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time
from typing import Any, BinaryIO, Callable
import uuid

from main_computer.docker_executor import (
    UploadRecord,
    _decode_timeout_output,
    _safe_filename,
    _safe_posix_relative_path,
    _truncate,
    _validate_input_ids,
)
from main_computer.executor_models import ExecutorArtifact, ExecutorRequest, ExecutorResult, build_executor_runtime_command


Runner = Callable[..., subprocess.CompletedProcess[str]]


class WslExecutor:
    """WSL-backed Linux execution boundary implementing the shared executor API.

    This backend is intended as a dev/test alternative to Docker first. It uses a
    named WSL distro such as MainComputerExecutorTest or MainComputerExecutor and
    preserves the same caller-facing paths as Docker:

        /inputs/<upload_id>/payload.bin
        /workspace
        /outputs

    WSL v1 deliberately does not claim Docker-equivalent network or cgroup
    isolation. The status payload exposes that limitation so callers and tests can
    decide whether WSL is acceptable for a scenario.
    """

    backend_name = "wsl"

    def __init__(
        self,
        *,
        distribution: str = "MainComputerExecutorTest",
        wsl_command: str = "wsl.exe",
        runtime_root: Path | str,
        enabled: bool = False,
        max_timeout_s: float = 120.0,
        max_upload_bytes: int = 2 * 1024 * 1024 * 1024,
        max_output_chars: int = 128_000,
        runner: Runner | None = None,
    ) -> None:
        self.distribution = str(distribution or "MainComputerExecutorTest").strip() or "MainComputerExecutorTest"
        self.wsl_command = str(wsl_command or "wsl.exe").strip() or "wsl.exe"
        self.runtime_root = Path(runtime_root).resolve()
        self.enabled = bool(enabled)
        self.max_timeout_s = max(1.0, float(max_timeout_s))
        self.max_upload_bytes = max(1, int(max_upload_bytes))
        self.max_output_chars = max(1000, int(max_output_chars))
        self._runner = runner or subprocess.run

        self.inputs_root = self.runtime_root / "inputs"
        self.outputs_root = self.runtime_root / "outputs"
        self.jobs_root = self.runtime_root / "jobs"
        self._ensure_roots()

    def _ensure_roots(self) -> None:
        for path in (self.runtime_root, self.inputs_root, self.outputs_root, self.jobs_root):
            path.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict[str, Any]:
        wsl_path = _which_or_path(self.wsl_command)
        wsl_available = bool(wsl_path)
        distribution_available = False
        entrypoint_available = False
        entrypoint_contract_ok = False
        wsl_error = None
        probe_stdout = ""

        if wsl_available:
            try:
                self._ensure_roots()
                status_workspace = self.jobs_root / "_status" / "workspace"
                status_output = self.outputs_root / "_status"
                status_workspace.mkdir(parents=True, exist_ok=True)
                status_output.mkdir(parents=True, exist_ok=True)
                _make_host_writable(status_workspace)
                _make_host_writable(status_output)

                workspace_wsl = _host_path_to_wsl(status_workspace)
                outputs_wsl = _host_path_to_wsl(status_output)
                probe_script = "\n".join(
                    [
                        "set -e",
                        "rm -rf /outputs /workspace",
                        f"ln -s {shlex.quote(outputs_wsl)} /outputs",
                        f"ln -s {shlex.quote(workspace_wsl)} /workspace",
                        "echo main-computer-wsl-ok",
                        "test -x /usr/local/bin/main-computer-exec",
                        "echo entrypoint-ok",
                        (
                            "/usr/local/bin/main-computer-exec run "
                            "--cwd /workspace "
                            "--timeout-ms 5000 "
                            "--artifact-dir /outputs "
                            "-- 'echo main-computer-exec-ready' "
                            "| grep -q main-computer-exec-ready"
                        ),
                        "echo entrypoint-contract-ok",
                    ]
                )
                result = self._runner(
                    [
                        self.wsl_command,
                        "--distribution",
                        self.distribution,
                        "--exec",
                        "/bin/sh",
                        "-lc",
                        probe_script,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                )
                probe_stdout = result.stdout or ""
                distribution_available = "main-computer-wsl-ok" in probe_stdout
                entrypoint_available = "entrypoint-ok" in probe_stdout
                entrypoint_contract_ok = result.returncode == 0 and "entrypoint-contract-ok" in probe_stdout
                if result.returncode != 0:
                    wsl_error = _truncate((result.stderr or result.stdout or "").strip(), 1000)
            except Exception as exc:  # pragma: no cover - defensive status surface
                wsl_error = str(exc)
        else:
            wsl_error = f"{self.wsl_command} executable was not found on PATH"

        return {
            "ok": self.enabled and distribution_available and entrypoint_contract_ok,
            "backend": self.backend_name,
            "enabled": self.enabled,
            "distribution": self.distribution,
            "wsl_command": self.wsl_command,
            "runtime_root": str(self.runtime_root),
            "inputs_root": str(self.inputs_root),
            "outputs_root": str(self.outputs_root),
            "jobs_root": str(self.jobs_root),
            "max_timeout_s": self.max_timeout_s,
            "max_upload_bytes": self.max_upload_bytes,
            "wsl_path": wsl_path,
            "wsl_available": wsl_available,
            "distribution_available": distribution_available,
            "entrypoint_available": entrypoint_available,
            "entrypoint_contract_ok": entrypoint_contract_ok,
            "network_isolation": False,
            "isolation_warning": "WSL backend v1 does not provide Docker-equivalent network/cgroup isolation.",
            "wsl_error": wsl_error,
        }

    def save_upload(
        self,
        *,
        filename: str,
        stream: BinaryIO,
        content_length: int,
        mime_type: str | None = None,
    ) -> UploadRecord:
        if content_length < 0:
            raise ValueError("Content-Length is required.")
        if content_length > self.max_upload_bytes:
            raise ValueError(f"Upload exceeds max size of {self.max_upload_bytes} bytes.")

        upload_id = f"upload_{uuid.uuid4().hex[:16]}"
        upload_dir = self.inputs_root / upload_id
        upload_dir.mkdir(parents=True, exist_ok=False)

        safe_filename = _safe_filename(filename)
        payload_path = upload_dir / "payload.bin"
        digest = hashlib.sha256()
        remaining = content_length
        written = 0

        with payload_path.open("wb") as handle:
            while remaining > 0:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                handle.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                remaining -= len(chunk)
                if written > self.max_upload_bytes:
                    raise ValueError(f"Upload exceeds max size of {self.max_upload_bytes} bytes.")

        if written != content_length:
            raise ValueError(f"Expected {content_length} upload bytes but received {written}.")

        guessed_type = mime_type or mimetypes.guess_type(safe_filename)[0] or "application/octet-stream"
        record = UploadRecord(
            id=upload_id,
            filename=safe_filename,
            mime_type=guessed_type,
            size=written,
            sha256=digest.hexdigest(),
            created_at=datetime.now(tz=timezone.utc).isoformat(),
            container_path=f"/inputs/{upload_id}/payload.bin",
            host_path=str(payload_path),
        )
        (upload_dir / "metadata.json").write_text(json.dumps(record.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return record

    def list_uploads(self, *, limit: int = 200) -> list[dict[str, Any]]:
        uploads: list[dict[str, Any]] = []
        for metadata_path in sorted(self.inputs_root.glob("upload_*/metadata.json"), reverse=True):
            try:
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                uploads.append(data)
            if len(uploads) >= limit:
                break
        return uploads

    def run(self, request: ExecutorRequest) -> ExecutorResult:
        if not self.enabled:
            return ExecutorResult(
                ok=False,
                job_id="",
                command=request.command,
                cwd=request.cwd,
                exit_code=None,
                stdout="",
                stderr="",
                timed_out=False,
                duration_ms=0,
                error="WSL executor is disabled. Set MAIN_COMPUTER_EXECUTOR_ENABLED=1 and MAIN_COMPUTER_EXECUTOR_BACKEND=wsl to enable it.",
                backend=self.backend_name,
            )

        self._ensure_roots()
        _validate_input_ids(request.input_ids)

        job_id = uuid.uuid4().hex[:16]
        job_output = self.outputs_root / job_id
        job_workspace = self.jobs_root / job_id / "workspace"
        job_output.mkdir(parents=True, exist_ok=False)
        job_workspace.mkdir(parents=True, exist_ok=True)
        _make_host_writable(job_output)
        _make_host_writable(job_workspace)

        wsl_cmd = self._build_wsl_command(
            request=request,
            job_output=job_output,
            job_workspace=job_workspace,
        )

        started = time.monotonic()
        try:
            effective_timeout_s = min(float(request.timeout_s), self.max_timeout_s)
            completed = self._runner(
                wsl_cmd,
                cwd=str(self.runtime_root),
                capture_output=True,
                text=True,
                timeout=effective_timeout_s + 5.0,
                check=False,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            artifacts = self._collect_artifacts(job_id)
            runtime_timed_out = completed.returncode == 124
            return ExecutorResult(
                ok=completed.returncode == 0,
                job_id=job_id,
                command=request.command,
                cwd=request.cwd,
                exit_code=completed.returncode,
                stdout=_truncate(completed.stdout or "", self.max_output_chars),
                stderr=_truncate(completed.stderr or "", self.max_output_chars),
                timed_out=runtime_timed_out,
                duration_ms=duration_ms,
                artifacts=artifacts,
                error=f"Command timed out after {effective_timeout_s:g} seconds." if runtime_timed_out else None,
                backend=self.backend_name,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            stdout = _decode_timeout_output(exc.stdout)
            stderr = _decode_timeout_output(exc.stderr)
            artifacts = self._collect_artifacts(job_id)
            return ExecutorResult(
                ok=False,
                job_id=job_id,
                command=request.command,
                cwd=request.cwd,
                exit_code=None,
                stdout=_truncate(stdout, self.max_output_chars),
                stderr=_truncate(stderr, self.max_output_chars),
                timed_out=True,
                duration_ms=duration_ms,
                artifacts=artifacts,
                error=f"Command timed out after {request.timeout_s:g} seconds.",
                backend=self.backend_name,
            )
        except FileNotFoundError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            return ExecutorResult(
                ok=False,
                job_id=job_id,
                command=request.command,
                cwd=request.cwd,
                exit_code=None,
                stdout="",
                stderr=str(exc),
                timed_out=False,
                duration_ms=duration_ms,
                artifacts=[],
                error="wsl.exe executable was not found.",
                backend=self.backend_name,
            )

    def _build_wsl_command(
        self,
        *,
        request: ExecutorRequest,
        job_output: Path,
        job_workspace: Path,
    ) -> list[str]:
        inputs_wsl = _host_path_to_wsl(self.inputs_root)
        outputs_wsl = _host_path_to_wsl(job_output)
        workspace_wsl = _host_path_to_wsl(job_workspace)

        lines = [
            "set -e",
            "rm -rf /inputs /outputs /workspace",
            f"ln -s {shlex.quote(inputs_wsl)} /inputs",
            f"ln -s {shlex.quote(outputs_wsl)} /outputs",
            f"ln -s {shlex.quote(workspace_wsl)} /workspace",
        ]
        for key, value in sorted(request.env.items()):
            lines.append(f"export {key}={shlex.quote(str(value))}")
        effective_timeout_s = min(float(request.timeout_s), self.max_timeout_s)
        runtime_argv = build_executor_runtime_command(request, timeout_s=effective_timeout_s)
        lines.append("exec " + " ".join(shlex.quote(part) for part in runtime_argv))
        shell_script = "\n".join(lines)
        return [
            self.wsl_command,
            "--distribution",
            self.distribution,
            "--exec",
            "/bin/sh",
            "-lc",
            shell_script,
        ]

    def _collect_artifacts(self, job_id: str) -> list[ExecutorArtifact]:
        job_root = self._safe_job_root(job_id)
        artifacts: list[ExecutorArtifact] = []
        if not job_root.exists():
            return artifacts
        for path in sorted(job_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(job_root).as_posix()
            artifacts.append(
                ExecutorArtifact(
                    name=path.name,
                    relative_path=relative,
                    size=path.stat().st_size,
                    download_url=f"/api/executor/artifacts/{job_id}/{relative}",
                )
            )
            if len(artifacts) >= 500:
                break
        return artifacts

    def artifact_path(self, job_id: str, relative_path: str) -> Path:
        job_root = self._safe_job_root(job_id)
        rel = _safe_posix_relative_path(relative_path)
        target = (job_root / rel).resolve()
        target.relative_to(job_root)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(relative_path)
        return target

    def _safe_job_root(self, job_id: str) -> Path:
        if not re.match(r"^[a-f0-9]{16}$", str(job_id or "")):
            raise ValueError("Invalid job id.")
        root = (self.outputs_root / job_id).resolve()
        root.relative_to(self.outputs_root.resolve())
        return root


def _host_path_to_wsl(path: Path) -> str:
    raw = str(Path(path).resolve())
    normalized = raw.replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/(.*)$", normalized)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2)
        return f"/mnt/{drive}/{rest}"
    if normalized.startswith("//"):
        raise ValueError(f"UNC paths are not supported for WSL executor runtime paths: {raw}")
    return normalized


def _which_or_path(command: str) -> str | None:
    found = shutil.which(command)
    if found:
        return found
    candidate = Path(command)
    if candidate.exists():
        return str(candidate)
    return None


def _make_host_writable(path: Path) -> None:
    try:
        os.chmod(path, 0o777)
    except OSError:
        pass
