#!/usr/bin/env python3
r"""
QEMU Python deterministic-execution smoke.

This smoke deliberately has one execution request and several ways to reach the
same executor:

  default smoke   request -> local backend -> QEMU -> guest Python
                  request -> localhost HTTP -> same backend -> QEMU -> guest Python
                  and require the same canonical result id

  direct          request -> local backend -> QEMU -> guest Python

  server          HTTP /v1/execute -> selected local backend -> QEMU -> guest Python

  client          request -> HTTP server -> server-local backend -> QEMU -> guest Python

The local backend can launch QEMU directly or inside Docker. The request always
records the exact Python executable that created it (sys.executable + SHA-256),
but the guest uses a small Linux Python runtime with the same Python major.minor.
That distinction is explicit in the evidence: a Windows python.exe cannot itself
be executed inside a Linux system-emulation guest.

The guest receives the request through QEMU fw_cfg, has no virtual NIC, executes
one Python source fragment, emits one structured result on the serial console,
and powers off. No SSH, bind-mounted source tree, or guest network is required.

Typical first run on Windows PowerShell:

  python .\tools\qemu_python_execution_smoke.py --prepare-runtime

Then ordinary direct+server-path proof:

  python .\tools\qemu_python_execution_smoke.py

Repeatability hammer (10 direct QEMU runs + 10 through one local server):

  python .\tools\qemu_python_execution_smoke.py --repeat 10

Direct only:

  python .\tools\qemu_python_execution_smoke.py --mode direct

Docker-hosted QEMU (build image once):

  python .\tools\qemu_python_execution_smoke.py `
    --backend docker `
    --build-docker-qemu

Persistent server backed by the machine's local qemu.exe:

  python .\tools\qemu_python_execution_smoke.py `
    --mode server `
    --backend direct `
    --listen 127.0.0.1:9766

Remote/client path (same request format):

  python .\tools\qemu_python_execution_smoke.py `
    --mode client `
    --remote http://127.0.0.1:9766
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Protocol
import urllib.error
import urllib.request


SCHEMA_REQUEST = "main-computer-qemu-python-request-v1"
SCHEMA_RESULT = "main-computer-qemu-python-result-v1"
SCHEMA_EVIDENCE = "main-computer-qemu-python-evidence-v1"
RESULT_MARKER = "MC_QEMU_PYTHON_RESULT_V1:"
DEFAULT_LISTEN = "127.0.0.1:9766"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_QEMU_TIMEOUT_SECONDS = 60.0
DEFAULT_MEMORY_MB = 512
MAX_HTTP_REQUEST_BYTES = 4 * 1024 * 1024
DEFAULT_DOCKER_QEMU_IMAGE = "main-computer-qemu-python-smoke:local"
DEFAULT_GUEST_BUILDER_IMAGE = "main-computer-qemu-python-guest-builder:local"
GUEST_RUNTIME_REVISION = 2

DEFAULT_SOURCE = r'''import json, os
inputs = json.loads(os.environ["MC_INPUTS_JSON"])
text = str(inputs["text"])
score = sum((index + 1) * ord(ch) for index, ch in enumerate(text))
print(json.dumps({"score": score, "text": text}, sort_keys=True, separators=(",", ":")))
'''
DEFAULT_INPUTS = {"text": "main-computer-qemu-python-smoke"}


class SmokeError(RuntimeError):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def current_python_identity() -> dict[str, Any]:
    executable = Path(sys.executable).resolve()
    try:
        executable_sha256 = sha256_file(executable)
    except OSError as exc:
        raise SmokeError(f"could not hash current Python executable {executable}: {exc}") from exc
    return {
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
        "major_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
        "cache_tag": getattr(sys.implementation, "cache_tag", None),
        "executable": str(executable),
        "executable_sha256": executable_sha256,
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "platform": sys.platform,
        "machine": platform.machine(),
    }


def request_identity_payload(request: dict[str, Any]) -> dict[str, Any]:
    # request_id is derived from everything that defines the computation and its
    # claimed origin, but not from transport/backend placement.
    return {
        "schema": request["schema"],
        "language": request["language"],
        "source_sha256": request["source_sha256"],
        "inputs": request["inputs"],
        "python_requirement": request["python_requirement"],
        "origin_python": request["origin_python"],
        "timeout_seconds": request["timeout_seconds"],
        "policy": request["policy"],
    }


def build_execution_request(
    *,
    source: str,
    inputs: dict[str, Any],
    timeout_seconds: float,
    origin_python: dict[str, Any] | None = None,
) -> dict[str, Any]:
    origin = dict(origin_python or current_python_identity())
    request: dict[str, Any] = {
        "schema": SCHEMA_REQUEST,
        "language": "python",
        "source": source,
        "source_sha256": sha256_bytes(source.encode("utf-8")),
        "inputs": inputs,
        "python_requirement": {
            "implementation": origin["implementation"],
            "major_minor": origin["major_minor"],
        },
        "origin_python": origin,
        "timeout_seconds": float(timeout_seconds),
        "policy": {
            "deterministic": True,
            "network": "none",
            "python_isolated": True,
            "python_site_disabled": True,
            "python_hash_seed": "0",
            "timezone": "UTC",
        },
    }
    request["request_id"] = canonical_sha256(request_identity_payload(request))
    return request


def semantic_result_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SCHEMA_RESULT,
        "request_id": result.get("request_id"),
        "source_sha256": result.get("source_sha256"),
        "ok": bool(result.get("ok")),
        "timed_out": bool(result.get("timed_out")),
        "exit_code": result.get("exit_code"),
        "stdout": str(result.get("stdout") or ""),
        "stderr": str(result.get("stderr") or ""),
    }


def attach_result_identity(result: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(result)
    normalized["result_id"] = canonical_sha256(semantic_result_payload(normalized))
    return normalized


def parse_guest_result(output: str, *, request: dict[str, Any]) -> dict[str, Any]:
    matches = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith(RESULT_MARKER):
            matches.append(line[len(RESULT_MARKER) :])
    if not matches:
        raise SmokeError("QEMU serial output did not contain a guest result marker")
    if len(matches) != 1:
        raise SmokeError(f"QEMU serial output contained {len(matches)} guest result markers")
    try:
        payload = json.loads(base64.b64decode(matches[0], validate=True).decode("utf-8"))
    except Exception as exc:
        raise SmokeError(f"could not decode guest result marker: {exc}") from exc
    if payload.get("schema") != SCHEMA_RESULT:
        raise SmokeError(f"unexpected guest result schema: {payload.get('schema')!r}")
    if payload.get("request_id") not in (None, request["request_id"]):
        raise SmokeError("guest result request_id does not match submitted request")
    if payload.get("request_id") is None and payload.get("ok"):
        raise SmokeError("successful guest result omitted request_id")
    return attach_result_identity(payload)


def split_listen(value: str) -> tuple[str, int]:
    host, sep, port_text = value.rpartition(":")
    if not sep or not host:
        raise SmokeError(f"invalid listen address {value!r}; expected host:port")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise SmokeError(f"invalid listen port in {value!r}") from exc
    if not 0 <= port <= 65535:
        raise SmokeError(f"invalid listen port {port}")
    return host, port


def is_loopback_host(host: str) -> bool:
    return host in {"127.0.0.1", "::1", "localhost"}


def command_path(explicit: str | None, names: tuple[str, ...], extra: tuple[str, ...] = ()) -> str:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    for name in names:
        found = shutil.which(name)
        if found:
            candidates.append(found)
    candidates.extend(extra)
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return str(path.resolve())
    shown = explicit or ", ".join(names)
    raise SmokeError(f"required executable was not found: {shown}")


def runtime_paths(runtime_dir: Path) -> tuple[Path, Path, Path]:
    runtime_dir = runtime_dir.resolve()
    kernel = runtime_dir / "vmlinuz"
    initramfs = runtime_dir / "initramfs.cpio.gz"
    metadata = runtime_dir / "runtime.json"
    missing = [str(path) for path in (kernel, initramfs, metadata) if not path.is_file()]
    if missing:
        raise SmokeError(
            "QEMU Python guest runtime is missing: "
            + ", ".join(missing)
            + ". Run this smoke once with --prepare-runtime."
        )
    return kernel, initramfs, metadata


def load_runtime_metadata(runtime_dir: Path) -> dict[str, Any]:
    kernel, initramfs, metadata_path = runtime_paths(runtime_dir)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SmokeError(f"invalid guest runtime metadata {metadata_path}: {exc}") from exc
    actual_kernel = sha256_file(kernel)
    actual_initramfs = sha256_file(initramfs)
    if metadata.get("kernel_sha256") != actual_kernel:
        raise SmokeError("guest runtime kernel SHA-256 does not match runtime.json")
    if metadata.get("initramfs_sha256") != actual_initramfs:
        raise SmokeError("guest runtime initramfs SHA-256 does not match runtime.json")
    return metadata


def prepare_guest_runtime(*, runtime_dir: Path, docker: str | None, force: bool = False) -> dict[str, Any]:
    docker_exe = command_path(docker, ("docker.exe", "docker"))
    context = repo_root() / "docker" / "qemu-python-smoke"
    python_major_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    builder_image = f"{DEFAULT_GUEST_BUILDER_IMAGE}-{python_major_minor.replace('.', '')}"
    runtime_dir = runtime_dir.resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    if not force:
        try:
            metadata = load_runtime_metadata(runtime_dir)
            if (
                metadata.get("python_major_minor") == python_major_minor
                and metadata.get("runtime_revision") == GUEST_RUNTIME_REVISION
            ):
                return {"prepared": False, "reused": True, "runtime": metadata, "runtime_dir": str(runtime_dir)}
        except SmokeError:
            pass

    build = subprocess.run(
        [
            docker_exe,
            "build",
            "-f",
            str(context / "Dockerfile.guest-runtime"),
            "--build-arg",
            f"PYTHON_MAJOR_MINOR={python_major_minor}",
            "-t",
            builder_image,
            str(context),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        raise SmokeError(f"guest runtime Docker build failed:\n{build.stdout}\n{build.stderr}")

    run = subprocess.run(
        [
            docker_exe,
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{runtime_dir}:/out",
            builder_image,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if run.returncode != 0:
        raise SmokeError(f"guest runtime materialization failed:\n{run.stdout}\n{run.stderr}")

    metadata = load_runtime_metadata(runtime_dir)
    if metadata.get("python_major_minor") != python_major_minor:
        raise SmokeError(
            "prepared guest Python does not match current Python major.minor: "
            f"expected {python_major_minor}, got {metadata.get('python_major_minor')}"
        )
    if metadata.get("runtime_revision") != GUEST_RUNTIME_REVISION:
        raise SmokeError(
            "prepared guest runtime revision mismatch: "
            f"expected {GUEST_RUNTIME_REVISION}, got {metadata.get('runtime_revision')}"
        )
    return {"prepared": True, "reused": False, "runtime": metadata, "runtime_dir": str(runtime_dir)}


def build_docker_qemu_image(*, docker: str | None, image: str) -> dict[str, Any]:
    docker_exe = command_path(docker, ("docker.exe", "docker"))
    context = repo_root() / "docker" / "qemu-python-smoke"
    completed = subprocess.run(
        [docker_exe, "build", "-f", str(context / "Dockerfile.qemu-runtime"), "-t", image, str(context)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SmokeError(f"Docker QEMU image build failed:\n{completed.stdout}\n{completed.stderr}")
    inspect = subprocess.run(
        [docker_exe, "image", "inspect", image, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "image": image,
        "image_id": inspect.stdout.strip() if inspect.returncode == 0 else None,
        "built": True,
    }


@dataclass(frozen=True)
class ExecutorConfig:
    backend: str
    runtime_dir: Path
    qemu: str | None
    docker: str | None
    docker_qemu_image: str
    memory_mb: int
    qemu_timeout_seconds: float


class ExecutionBackend(Protocol):
    def execute(self, request: dict[str, Any]) -> dict[str, Any]: ...


class QemuExecutionBackend:
    def __init__(self, config: ExecutorConfig) -> None:
        self.config = config
        self.runtime_metadata = load_runtime_metadata(config.runtime_dir)
        if config.backend == "direct":
            self.qemu_exe = command_path(
                config.qemu or os.environ.get("MAIN_COMPUTER_QEMU_EXE"),
                ("qemu-system-x86_64.exe", "qemu-system-x86_64"),
                extra=(r"C:\Program Files\qemu\qemu-system-x86_64.exe",),
            )
            self.docker_exe = None
        elif config.backend == "docker":
            self.qemu_exe = None
            self.docker_exe = command_path(config.docker, ("docker.exe", "docker"))
        else:
            raise SmokeError(f"unsupported local backend {config.backend!r}")

    @staticmethod
    def qemu_guest_args(*, kernel: str, initramfs: str, request_path: str, memory_mb: int) -> list[str]:
        return [
            "-accel",
            "tcg",
            "-machine",
            "q35",
            "-cpu",
            "max",
            "-smp",
            "1",
            "-m",
            f"{memory_mb}M",
            "-display",
            "none",
            "-monitor",
            "none",
            "-serial",
            "stdio",
            "-no-reboot",
            "-nic",
            "none",
            "-kernel",
            kernel,
            "-initrd",
            initramfs,
            "-append",
            "console=ttyS0 rdinit=/sbin/mc-qemu-init panic=-1",
            "-fw_cfg",
            f"name=opt/main-computer/request,file={request_path}",
        ]

    def _validate_request(self, request: dict[str, Any]) -> None:
        if request.get("schema") != SCHEMA_REQUEST:
            raise SmokeError(f"unsupported request schema {request.get('schema')!r}")
        calculated = canonical_sha256(request_identity_payload(request))
        if request.get("request_id") != calculated:
            raise SmokeError("execution request_id does not match canonical request content")
        if request.get("source_sha256") != sha256_bytes(str(request.get("source") or "").encode("utf-8")):
            raise SmokeError("execution request source_sha256 does not match source")
        required = request.get("python_requirement") or {}
        runtime_major_minor = str(self.runtime_metadata.get("python_major_minor") or "")
        if required.get("major_minor") != runtime_major_minor:
            raise SmokeError(
                "guest runtime Python mismatch: "
                f"request requires {required.get('major_minor')}, runtime provides {runtime_major_minor}. "
                "Re-run with --prepare-runtime --force-prepare-runtime."
            )

    def _direct_command(self, *, request_path: Path) -> list[str]:
        kernel, initramfs, _ = runtime_paths(self.config.runtime_dir)
        assert self.qemu_exe is not None
        return [
            self.qemu_exe,
            *self.qemu_guest_args(
                kernel=str(kernel),
                initramfs=str(initramfs),
                request_path=str(request_path),
                memory_mb=self.config.memory_mb,
            ),
        ]

    def _docker_command(self, *, job_dir: Path) -> list[str]:
        assert self.docker_exe is not None
        runtime_dir = self.config.runtime_dir.resolve()
        job_dir = job_dir.resolve()
        guest_args = self.qemu_guest_args(
            kernel="/mc/runtime/vmlinuz",
            initramfs="/mc/runtime/initramfs.cpio.gz",
            request_path="/mc/job/request.json",
            memory_mb=self.config.memory_mb,
        )
        return [
            self.docker_exe,
            "run",
            "--rm",
            "--network",
            "none",
            "--memory",
            f"{max(self.config.memory_mb + 256, 768)}m",
            "--cpus",
            "2",
            "-v",
            f"{runtime_dir}:/mc/runtime:ro",
            "-v",
            f"{job_dir}:/mc/job:ro",
            self.config.docker_qemu_image,
            *guest_args,
        ]

    def _qemu_witness(self, command: list[str]) -> dict[str, Any]:
        witness: dict[str, Any] = {
            "backend": self.config.backend,
            "runtime": self.runtime_metadata,
            "command": command,
            "network": "none",
            "accelerator": "tcg",
        }
        if self.config.backend == "direct":
            assert self.qemu_exe is not None
            # Do not start a second QEMU process merely to collect a version
            # string.  The executable content hash is the provenance identity,
            # and the real guest launch below is the execution smoke.  This also
            # avoids platform-specific startup hangs in metadata-only invocations.
            witness.update(
                {
                    "qemu_executable": self.qemu_exe,
                    "qemu_executable_sha256": sha256_file(Path(self.qemu_exe)),
                }
            )
        else:
            assert self.docker_exe is not None
            inspect = subprocess.run(
                [self.docker_exe, "image", "inspect", self.config.docker_qemu_image, "--format", "{{.Id}}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            witness.update(
                {
                    "docker_qemu_image": self.config.docker_qemu_image,
                    "docker_qemu_image_id": inspect.stdout.strip() if inspect.returncode == 0 else None,
                }
            )
        return witness

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        self._validate_request(request)
        with tempfile.TemporaryDirectory(prefix="mc-qemu-python-job-") as temp_text:
            job_dir = Path(temp_text)
            request_path = job_dir / "request.json"
            request_path.write_bytes(canonical_json_bytes(request))

            command = (
                self._direct_command(request_path=request_path)
                if self.config.backend == "direct"
                else self._docker_command(job_dir=job_dir)
            )
            witness = self._qemu_witness(command)
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.config.qemu_timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise SmokeError(
                    f"QEMU execution exceeded {self.config.qemu_timeout_seconds}s; "
                    f"stdout={exc.stdout!r} stderr={exc.stderr!r}"
                ) from exc
            elapsed = time.monotonic() - started
            serial_output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
            try:
                result = parse_guest_result(serial_output, request=request)
            except SmokeError as exc:
                raise SmokeError(
                    f"{exc}; QEMU exit={completed.returncode}; serial tail={serial_output[-4000:]!r}"
                ) from exc
            result["evidence"] = {
                "schema": SCHEMA_EVIDENCE,
                "request_id": request["request_id"],
                "result_id": result["result_id"],
                "elapsed_seconds": elapsed,
                "qemu_exit_code": completed.returncode,
                "witness": witness,
            }
            return result


class FakeDeterministicBackend:
    """Test helper kept intentionally tiny; never selected by CLI."""

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        result = {
            "schema": SCHEMA_RESULT,
            "ok": True,
            "timed_out": False,
            "request_id": request["request_id"],
            "source_sha256": request["source_sha256"],
            "exit_code": 0,
            "stdout": "fake\n",
            "stderr": "",
            "guest_python": {"major_minor": request["python_requirement"]["major_minor"]},
        }
        return attach_result_identity(result)


class ExecutionHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], backend: ExecutionBackend, bearer_token: str | None) -> None:
        super().__init__(server_address, ExecutionHTTPRequestHandler)
        self.backend = backend
        self.bearer_token = bearer_token


class ExecutionHTTPRequestHandler(BaseHTTPRequestHandler):
    server: ExecutionHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep stdout machine-readable; server diagnostics go to stderr.
        print(f"qemu-python-server: {fmt % args}", file=sys.stderr)

    def _write_json(self, status: int, payload: Any) -> None:
        body = canonical_json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        token = self.server.bearer_token
        if not token:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {token}"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._write_json(200, {"ok": True, "service": "qemu-python-execution-smoke", "schema": SCHEMA_REQUEST})
            return
        self._write_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/execute":
            self._write_json(404, {"ok": False, "error": "not found"})
            return
        if not self._authorized():
            self._write_json(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._write_json(400, {"ok": False, "error": "invalid Content-Length"})
            return
        if length <= 0 or length > MAX_HTTP_REQUEST_BYTES:
            self._write_json(413, {"ok": False, "error": "invalid request size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            request = payload["request"]
            result = self.server.backend.execute(request)
            self._write_json(200, {"ok": True, "result": result})
        except SmokeError as exc:
            self._write_json(422, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._write_json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


class RemoteExecutionClient:
    def __init__(self, endpoint: str, *, bearer_token: str | None, timeout_seconds: float) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.bearer_token = bearer_token
        self.timeout_seconds = timeout_seconds

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        body = canonical_json_bytes({"request": request})
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        req = urllib.request.Request(
            self.endpoint + "/v1/execute",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_HTTP_REQUEST_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raw = exc.read(MAX_HTTP_REQUEST_BYTES + 1)
            raise SmokeError(f"remote executor HTTP {exc.code}: {raw.decode('utf-8', 'replace')}") from exc
        except Exception as exc:
            raise SmokeError(f"remote executor request failed: {exc}") from exc
        if len(raw) > MAX_HTTP_REQUEST_BYTES:
            raise SmokeError("remote executor response exceeded size limit")
        payload = json.loads(raw.decode("utf-8"))
        if not payload.get("ok"):
            raise SmokeError(f"remote executor rejected request: {payload.get('error')}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise SmokeError("remote executor omitted structured result")
        return result


def create_local_backend(args: argparse.Namespace) -> QemuExecutionBackend:
    return QemuExecutionBackend(
        ExecutorConfig(
            backend=args.backend,
            runtime_dir=Path(args.runtime_dir),
            qemu=args.qemu,
            docker=args.docker,
            docker_qemu_image=args.docker_qemu_image,
            memory_mb=args.memory_mb,
            qemu_timeout_seconds=args.qemu_timeout_seconds,
        )
    )


def _timing_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "max": None, "mean": None}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": sum(values) / len(values),
    }


def run_execution_series(
    *,
    execute: Callable[[dict[str, Any]], dict[str, Any]],
    request: dict[str, Any],
    count: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if count < 1:
        raise SmokeError("--repeat must be at least 1")

    first_success: dict[str, Any] | None = None
    runs: list[dict[str, Any]] = []
    for index in range(1, count + 1):
        started = time.monotonic()
        try:
            result = execute(request)
            wall_seconds = time.monotonic() - started
            evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
            execution_seconds = evidence.get("elapsed_seconds")
            record = {
                "index": index,
                "ok": bool(result.get("ok")),
                "request_id": result.get("request_id"),
                "result_id": result.get("result_id"),
                "wall_seconds": wall_seconds,
                "execution_seconds": execution_seconds,
                "exit_code": result.get("exit_code"),
                "timed_out": bool(result.get("timed_out")),
            }
            if record["ok"] and first_success is None:
                first_success = result
        except Exception as exc:
            wall_seconds = time.monotonic() - started
            record = {
                "index": index,
                "ok": False,
                "request_id": None,
                "result_id": None,
                "wall_seconds": wall_seconds,
                "execution_seconds": None,
                "exit_code": None,
                "timed_out": isinstance(exc, TimeoutError) or "exceeded" in str(exc).lower(),
                "error": f"{type(exc).__name__}: {exc}",
            }
        runs.append(record)
    return first_success, runs


def summarize_execution_series(
    runs: list[dict[str, Any]], *, expected_request_id: str
) -> dict[str, Any]:
    successful = [run for run in runs if run.get("ok")]
    request_ids = sorted({str(run.get("request_id")) for run in successful if run.get("request_id")})
    result_ids = sorted({str(run.get("result_id")) for run in successful if run.get("result_id")})
    wall_values = [float(run["wall_seconds"]) for run in runs if run.get("wall_seconds") is not None]
    execution_values = [
        float(run["execution_seconds"])
        for run in runs
        if run.get("execution_seconds") is not None
    ]
    return {
        "attempted": len(runs),
        "completed": len(successful),
        "failed": len(runs) - len(successful),
        "all_completed": len(successful) == len(runs),
        "request_preserved": bool(runs)
        and len(successful) == len(runs)
        and request_ids == [expected_request_id],
        "result_ids": result_ids,
        "result_identity_consistent": bool(runs)
        and len(successful) == len(runs)
        and len(result_ids) == 1,
        "wall_seconds": _timing_summary(wall_values),
        "execution_seconds": _timing_summary(execution_values),
    }


def run_localhost_roundtrips(
    *,
    backend: ExecutionBackend,
    request: dict[str, Any],
    token: str | None,
    timeout_seconds: float,
    count: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    server = ExecutionHTTPServer(("127.0.0.1", 0), backend, token)
    thread = threading.Thread(target=server.serve_forever, name="qemu-python-smoke-http", daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        client = RemoteExecutionClient(
            f"http://{host}:{port}", bearer_token=token, timeout_seconds=timeout_seconds
        )
        return run_execution_series(execute=client.execute, request=request, count=count)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)


def run_localhost_roundtrip(
    *,
    backend: ExecutionBackend,
    request: dict[str, Any],
    token: str | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    result, runs = run_localhost_roundtrips(
        backend=backend,
        request=request,
        token=token,
        timeout_seconds=timeout_seconds,
        count=1,
    )
    if result is None:
        error = runs[0].get("error") if runs else "unknown localhost execution failure"
        raise SmokeError(str(error))
    return result


def load_source(args: argparse.Namespace) -> str:
    if args.code is not None and args.code_file is not None:
        raise SmokeError("use only one of --code or --code-file")
    if args.code_file is not None:
        return Path(args.code_file).read_text(encoding="utf-8")
    if args.code is not None:
        return args.code
    return DEFAULT_SOURCE


def load_inputs(args: argparse.Namespace) -> dict[str, Any]:
    if args.inputs_json is None:
        return dict(DEFAULT_INPUTS)
    try:
        value = json.loads(args.inputs_json)
    except json.JSONDecodeError as exc:
        raise SmokeError(f"--inputs-json is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SmokeError("--inputs-json must decode to a JSON object")
    return value


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False), flush=True)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=("smoke", "direct", "server", "client"), default="smoke")
    p.add_argument("--backend", choices=("direct", "docker"), default="direct")
    p.add_argument("--qemu", help="Path to qemu-system-x86_64.exe for the direct backend")
    p.add_argument("--docker", help="Path to docker.exe/docker")
    p.add_argument("--docker-qemu-image", default=DEFAULT_DOCKER_QEMU_IMAGE)
    p.add_argument("--build-docker-qemu", action="store_true")
    p.add_argument(
        "--runtime-dir",
        default=str(repo_root() / "runtime" / "qemu-python-smoke"),
        help="Prepared vmlinuz/initramfs/runtime.json directory",
    )
    p.add_argument("--prepare-runtime", action="store_true")
    p.add_argument("--force-prepare-runtime", action="store_true")
    p.add_argument("--memory-mb", type=int, default=DEFAULT_MEMORY_MB)
    p.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    p.add_argument("--qemu-timeout-seconds", type=float, default=DEFAULT_QEMU_TIMEOUT_SECONDS)
    p.add_argument(
        "--repeat",
        type=int,
        default=1,
        help=(
            "Independent executions per path. Smoke mode runs this many direct QEMU "
            "executions and this many executions through one localhost server."
        ),
    )
    p.add_argument("--code")
    p.add_argument("--code-file")
    p.add_argument("--inputs-json")
    p.add_argument("--listen", default=DEFAULT_LISTEN)
    p.add_argument("--remote", default="http://127.0.0.1:9766")
    p.add_argument("--token", default=os.environ.get("MAIN_COMPUTER_QEMU_EXECUTION_TOKEN"))
    p.add_argument(
        "--allow-unauthenticated-nonloopback",
        action="store_true",
        help="Explicitly allow --mode server on a non-loopback interface without --token",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.repeat < 1:
            raise SmokeError("--repeat must be at least 1")

        preparation = None
        if args.prepare_runtime or args.force_prepare_runtime:
            preparation = prepare_guest_runtime(
                runtime_dir=Path(args.runtime_dir),
                docker=args.docker,
                force=args.force_prepare_runtime,
            )
        docker_build = None
        if args.build_docker_qemu:
            docker_build = build_docker_qemu_image(docker=args.docker, image=args.docker_qemu_image)

        if args.mode == "server":
            host, port = split_listen(args.listen)
            if not is_loopback_host(host) and not args.token and not args.allow_unauthenticated_nonloopback:
                raise SmokeError(
                    "refusing unauthenticated non-loopback execution server; pass --token or "
                    "--allow-unauthenticated-nonloopback explicitly"
                )
            backend = create_local_backend(args)
            server = ExecutionHTTPServer((host, port), backend, args.token)
            actual_host, actual_port = server.server_address[:2]
            print_json(
                {
                    "ok": True,
                    "mode": "server",
                    "backend": args.backend,
                    "listen": f"{actual_host}:{actual_port}",
                    "origin_python": current_python_identity(),
                    "preparation": preparation,
                    "docker_build": docker_build,
                }
            )
            server.serve_forever()
            return 0

        request = build_execution_request(
            source=load_source(args),
            inputs=load_inputs(args),
            timeout_seconds=args.timeout_seconds,
        )

        if args.mode == "client":
            client = RemoteExecutionClient(
                args.remote, bearer_token=args.token, timeout_seconds=args.qemu_timeout_seconds + 10.0
            )
            result, runs = run_execution_series(
                execute=client.execute,
                request=request,
                count=args.repeat,
            )
            summary = summarize_execution_series(runs, expected_request_id=request["request_id"])
            ok = bool(summary["all_completed"]) and bool(summary["request_preserved"]) and bool(
                summary["result_identity_consistent"]
            )
            print_json(
                {
                    "ok": ok,
                    "mode": "client",
                    "remote": args.remote,
                    "repeat": args.repeat,
                    "request": request,
                    "result": result,
                    "runs": runs,
                    "summary": summary,
                    "preparation": preparation,
                    "docker_build": docker_build,
                }
            )
            return 0 if ok else 1

        backend = create_local_backend(args)
        direct_result, direct_runs = run_execution_series(
            execute=backend.execute,
            request=request,
            count=args.repeat,
        )
        direct_summary = summarize_execution_series(
            direct_runs, expected_request_id=request["request_id"]
        )

        if args.mode == "direct":
            ok = bool(direct_summary["all_completed"]) and bool(
                direct_summary["request_preserved"]
            ) and bool(direct_summary["result_identity_consistent"])
            print_json(
                {
                    "ok": ok,
                    "mode": "direct",
                    "backend": args.backend,
                    "repeat": args.repeat,
                    "request": request,
                    "result": direct_result,
                    "runs": direct_runs,
                    "summary": direct_summary,
                    "preparation": preparation,
                    "docker_build": docker_build,
                }
            )
            return 0 if ok else 1

        server_result, server_runs = run_localhost_roundtrips(
            backend=backend,
            request=request,
            token=args.token,
            timeout_seconds=args.qemu_timeout_seconds + 10.0,
            count=args.repeat,
        )
        server_summary = summarize_execution_series(
            server_runs, expected_request_id=request["request_id"]
        )
        all_result_ids = sorted(
            {
                str(run["result_id"])
                for run in [*direct_runs, *server_runs]
                if run.get("ok") and run.get("result_id")
            }
        )
        identities_match = (
            bool(direct_summary["all_completed"])
            and bool(server_summary["all_completed"])
            and bool(direct_summary["result_identity_consistent"])
            and bool(server_summary["result_identity_consistent"])
            and len(all_result_ids) == 1
        )
        request_preserved = bool(direct_summary["request_preserved"]) and bool(
            server_summary["request_preserved"]
        )
        ok = identities_match and request_preserved
        print_json(
            {
                "ok": ok,
                "mode": "smoke",
                "backend": args.backend,
                "repeat": args.repeat,
                "origin_python": request["origin_python"],
                "request_id": request["request_id"],
                "direct_result_id": direct_result.get("result_id") if direct_result else None,
                "server_result_id": server_result.get("result_id") if server_result else None,
                "result_ids": all_result_ids,
                "identities_match": identities_match,
                "request_preserved": request_preserved,
                "direct_result": direct_result,
                "server_result": server_result,
                "direct_runs": direct_runs,
                "server_runs": server_runs,
                "direct_summary": direct_summary,
                "server_summary": server_summary,
                "preparation": preparation,
                "docker_build": docker_build,
            }
        )
        return 0 if ok else 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
