from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import time
import threading
from typing import Any, Callable, BinaryIO
import uuid

from main_computer.container_runtime import command_display as _container_command_display, legacy_docker_command_override, resolve_container_runtime
from main_computer.executor_models import ExecutorArtifact, ExecutorRequest, ExecutorResult, build_executor_runtime_command


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class DockerPoolLease:
    """A short-lived lease for one Docker execution slot."""

    pool_id: str
    lease_id: str
    slot: int
    image: str
    run_id: str
    label: str
    command_preview: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "pool_id": self.pool_id,
            "lease_id": self.lease_id,
            "slot": self.slot,
            "image": self.image,
            "run_id": self.run_id,
            "label": self.label,
            "command_preview": self.command_preview,
            "created_at": self.created_at,
        }


class DockerInstancePool:
    """Small in-process pool of Docker execution slots.

    Docker commands are still run as isolated ``docker run --rm`` invocations.
    The pool gives the activity pane an explicit request/acquire/release lifecycle
    and prevents multiple AI/tool loops from assuming the same free executor slot.
    """

    def __init__(self, *, size: int = 2, pool_id: str = "rag-docker-pool") -> None:
        self.size = max(1, int(size))
        self.pool_id = str(pool_id or "rag-docker-pool")
        self._leases: dict[str, DockerPoolLease] = {}
        self._condition = threading.Condition()

    def status(self) -> dict[str, Any]:
        with self._condition:
            leases = [lease.as_dict() for lease in self._leases.values()]
            return {
                "pool_id": self.pool_id,
                "size": self.size,
                "active": len(leases),
                "free": max(0, self.size - len(leases)),
                "leases": leases,
            }

    def request(
        self,
        *,
        run_id: str,
        image: str,
        command_preview: str,
        label: str = "verify",
        activity_bus: Any | None = None,
        max_wait_s: float = 30.0,
    ) -> DockerPoolLease:
        """Request and acquire a free Docker slot, recording visible pool events."""

        run = str(run_id or "").strip()
        command_text = _truncate(str(command_preview or "").strip(), 500)
        image_text = str(image or "").strip() or "docker-image"
        label_text = str(label or "verify").strip() or "verify"

        self._record_pool_event(
            activity_bus,
            run_id=run,
            title="Docker pool instance requested",
            message=f"{label_text}: {command_text}",
            status="running",
            data={
                "docker_pool": self.status(),
                "image": image_text,
                "phase": label_text,
                "command_preview": command_text,
                "running_text": f"waiting for a free Docker slot for {label_text}",
                "rag_type": "docker_executor",
            },
        )

        deadline = time.monotonic() + max(0.0, float(max_wait_s))
        with self._condition:
            while True:
                used_slots = {lease.slot for lease in self._leases.values()}
                free_slot = next((slot for slot in range(1, self.size + 1) if slot not in used_slots), None)
                if free_slot is not None:
                    lease = DockerPoolLease(
                        pool_id=self.pool_id,
                        lease_id=uuid.uuid4().hex[:16],
                        slot=free_slot,
                        image=image_text,
                        run_id=run,
                        label=label_text,
                        command_preview=command_text,
                        created_at=datetime.now(tz=timezone.utc).isoformat(),
                    )
                    self._leases[lease.lease_id] = lease
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._record_pool_event(
                        activity_bus,
                        run_id=run,
                        title="Docker pool instance unavailable",
                        message=f"No free Docker slot for {label_text}.",
                        status="failed",
                        severity="warn",
                        data={
                            "docker_pool": self.status(),
                            "image": image_text,
                            "phase": label_text,
                            "command_preview": command_text,
                            "rag_type": "docker_executor",
                        },
                    )
                    raise RuntimeError("No free Docker pool instance is available.")
                self._condition.wait(timeout=min(0.5, remaining))

        self._record_pool_event(
            activity_bus,
            run_id=run,
            title="Docker pool instance acquired",
            message=f"slot={lease.slot}; {label_text}",
            status="running",
            data={
                "docker_pool": self.status(),
                "docker_pool_lease": lease.as_dict(),
                "lease_id": lease.lease_id,
                "slot": lease.slot,
                "image": image_text,
                "phase": label_text,
                "command_preview": command_text,
                "running_text": f"docker slot {lease.slot} running {label_text}: {command_text}",
                "rag_type": "docker_executor",
            },
        )
        return lease

    def release(
        self,
        lease: DockerPoolLease | None,
        *,
        activity_bus: Any | None = None,
        status: str = "completed",
        returncode: int | None = None,
        error: str | None = None,
    ) -> None:
        if lease is None:
            return
        with self._condition:
            self._leases.pop(lease.lease_id, None)
            self._condition.notify()

        ok = str(status or "") not in {"failed", "error"}
        message = f"slot={lease.slot}; {lease.label}"
        if returncode is not None:
            message += f"; returncode={returncode}"
        if error:
            message += f"; {_truncate(error, 220)}"
        self._record_pool_event(
            activity_bus,
            run_id=lease.run_id,
            title="Docker pool instance released",
            message=message,
            status="completed" if ok else "failed",
            severity="info" if ok else "warn",
            data={
                "docker_pool": self.status(),
                "docker_pool_lease": lease.as_dict(),
                "lease_id": lease.lease_id,
                "slot": lease.slot,
                "image": lease.image,
                "phase": lease.label,
                "command_preview": lease.command_preview,
                "returncode": returncode,
                "error": _truncate(error or "", 500),
                "ran_text": f"docker slot {lease.slot} finished {lease.label}",
                "rag_type": "docker_executor",
            },
        )

    def _record_pool_event(
        self,
        activity_bus: Any | None,
        *,
        run_id: str,
        title: str,
        message: str,
        status: str,
        data: dict[str, Any],
        severity: str = "info",
    ) -> None:
        if activity_bus is None:
            return
        try:
            payload = dict(data)
            if run_id:
                payload.setdefault("run_id", run_id)
            activity_bus.record(
                source="executor",
                kind="subprocess",
                time_model="parallel",
                severity=severity,
                title=title,
                message=message,
                status=status,
                tags=["docker", "executor", "subprocess", "pool", "thinking", "rag"],
                data=payload,
                fault=severity in {"warn", "error"},
            )
        except Exception:
            return


_DEFAULT_DOCKER_INSTANCE_POOL = DockerInstancePool()


def default_docker_instance_pool() -> DockerInstancePool:
    return _DEFAULT_DOCKER_INSTANCE_POOL


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_UPLOAD_ID_RE = re.compile(r"^upload_[a-f0-9]{16}$")
_JOB_ID_RE = re.compile(r"^[a-f0-9]{16}$")


@dataclass(frozen=True)
class UploadRecord:
    id: str
    filename: str
    mime_type: str
    size: int
    sha256: str
    created_at: str
    container_path: str
    host_path: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size": self.size,
            "sha256": self.sha256,
            "created_at": self.created_at,
            "container_path": self.container_path,
            "host_path": self.host_path,
        }


class DockerExecutor:
    """Small Docker-backed Linux execution boundary.

    This class deliberately wraps Docker as a backend service primitive. It does
    not expose Docker sockets, host paths, or arbitrary mount choices to callers.
    """

    backend_name = "docker"

    def __init__(
        self,
        *,
        image: str,
        runtime_root: Path | str,
        enabled: bool = False,
        max_timeout_s: float = 120.0,
        max_upload_bytes: int = 2 * 1024 * 1024 * 1024,
        max_output_chars: int = 128_000,
        runner: Runner | None = None,
        docker_command: str | None = None,
    ) -> None:
        self.image = str(image or "main-computer-executor:latest")
        self.runtime_root = Path(runtime_root).resolve()
        self.enabled = bool(enabled)
        self.max_timeout_s = max(1.0, float(max_timeout_s))
        self.max_upload_bytes = max(1, int(max_upload_bytes))
        self.max_output_chars = max(1000, int(max_output_chars))
        self._runner = runner or subprocess.run
        self.container_runtime = resolve_container_runtime(
            cwd=self.runtime_root,
            container_command=legacy_docker_command_override(docker_command),
            probe=False,
        )
        self.docker_command = _container_command_display(self.container_runtime.container_command)

        self.inputs_root = self.runtime_root / "inputs"
        self.outputs_root = self.runtime_root / "outputs"
        self.jobs_root = self.runtime_root / "jobs"
        self._ensure_roots()

    def _ensure_roots(self) -> None:
        for path in (self.runtime_root, self.inputs_root, self.outputs_root, self.jobs_root):
            path.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict[str, Any]:
        container_command = list(self.container_runtime.container_command)
        executable = container_command[0] if container_command else "docker"
        docker_path = shutil.which(executable) or (executable if Path(executable).exists() else None)
        docker_available = False
        docker_error = None
        if docker_path:
            try:
                result = self._runner(
                    self.container_runtime.container_args("version", "--format", "{{.Server.Version}}"),
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                docker_available = result.returncode == 0
                if result.returncode != 0:
                    docker_error = _truncate((result.stderr or result.stdout or "").strip(), 1000)
            except Exception as exc:  # pragma: no cover - defensive status surface
                docker_error = str(exc)
        else:
            docker_error = f"{self.docker_command} executable was not found on PATH"

        return {
            "ok": self.enabled and docker_available,
            "backend": self.backend_name,
            "enabled": self.enabled,
            "image": self.image,
            "runtime_root": str(self.runtime_root),
            "inputs_root": str(self.inputs_root),
            "outputs_root": str(self.outputs_root),
            "jobs_root": str(self.jobs_root),
            "max_timeout_s": self.max_timeout_s,
            "max_upload_bytes": self.max_upload_bytes,
            "docker_path": docker_path,
            "docker_available": docker_available,
            "docker_error": docker_error,
            "container_runtime": self.container_runtime.as_dict(),
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
                error="Docker executor is disabled. Set MAIN_COMPUTER_EXECUTOR_ENABLED=1 to enable it.",
                backend=self.backend_name,
            )

        self._ensure_roots()
        _validate_input_ids(request.input_ids)

        job_id = uuid.uuid4().hex[:16]
        job_output = self.outputs_root / job_id
        job_workspace = self.jobs_root / job_id / "workspace"
        job_output.mkdir(parents=True, exist_ok=False)
        job_workspace.mkdir(parents=True, exist_ok=True)
        _make_host_mount_writable(job_output)
        _make_host_mount_writable(job_workspace)

        docker_cmd = self._build_docker_command(
            request=request,
            job_id=job_id,
            job_output=job_output,
            job_workspace=job_workspace,
        )

        started = time.monotonic()
        try:
            effective_timeout_s = min(float(request.timeout_s), self.max_timeout_s)
            completed = self._runner(
                docker_cmd,
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
                error=f"{self.docker_command} executable was not found.",
                backend=self.backend_name,
            )

    def _build_docker_command(
        self,
        *,
        request: ExecutorRequest,
        job_id: str,
        job_output: Path,
        job_workspace: Path,
    ) -> list[str]:
        command = self.container_runtime.container_args(
            "run",
            "--rm",
            "--name",
            f"main-computer-exec-{job_id}",
            "--network",
            "bridge" if request.network else "none",
            "--memory",
            "2g",
            "--cpus",
            "2",
            "--pids-limit",
            "256",
            "--security-opt",
            "no-new-privileges:true",
            "--cap-drop",
            "ALL",
            "-v",
            f"{self.inputs_root}:/inputs:ro",
            "-v",
            f"{job_output}:/outputs:rw",
            "-v",
            f"{job_workspace}:/workspace:rw",
            "-w",
            "/workspace",
        )
        for key, value in sorted(request.env.items()):
            command.extend(["-e", f"{key}={value}"])
        effective_timeout_s = min(float(request.timeout_s), self.max_timeout_s)
        command.append(self.image)
        command.extend(build_executor_runtime_command(request, timeout_s=effective_timeout_s))
        return command

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
        if not _JOB_ID_RE.match(str(job_id or "")):
            raise ValueError("Invalid job id.")
        root = (self.outputs_root / job_id).resolve()
        root.relative_to(self.outputs_root.resolve())
        return root


def _safe_filename(filename: str) -> str:
    name = Path(str(filename or "upload.bin")).name.strip()
    name = re.sub(r"[\x00-\x1f\\/:*?\"<>|]+", "_", name)
    name = name.strip(" ._")
    return name[:180] or "upload.bin"


def _validate_input_ids(input_ids: list[str]) -> None:
    for input_id in input_ids:
        if not _UPLOAD_ID_RE.match(str(input_id or "")):
            raise ValueError(f"Invalid input id: {input_id!r}")


def _safe_posix_relative_path(value: str) -> Path:
    raw = str(value or "").replace("\\", "/")
    posix = PurePosixPath(raw)
    if posix.is_absolute():
        raise ValueError("Artifact path must be relative.")
    parts = [part for part in posix.parts if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("Artifact path must not traverse directories.")
    for part in parts:
        if not _SAFE_ID_RE.match(part):
            # Allow ordinary names with spaces, but reject controls/separators.
            if any(ch in part for ch in "\x00\r\n/\\"):
                raise ValueError("Artifact path contains an unsafe segment.")
    return Path(*parts)


def _make_host_mount_writable(path: Path) -> None:
    try:
        os.chmod(path, 0o777)
    except OSError:
        pass


def _truncate(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[truncated after {limit} characters]"


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")
