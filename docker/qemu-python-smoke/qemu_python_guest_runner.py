#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from typing import Any

MARKER = "MC_QEMU_PYTHON_RESULT_V1:"
REQUEST_PATHS = (
    Path("/sys/firmware/qemu_fw_cfg/by_name/opt/main-computer/request/raw"),
    Path("/sys/firmware/qemu_fw_cfg/by_name/opt/main-computer/request"),
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def read_request() -> dict[str, Any]:
    for path in REQUEST_PATHS:
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if raw:
            return json.loads(raw.decode("utf-8"))
    raise RuntimeError("QEMU fw_cfg execution request was not found")


def python_identity() -> dict[str, Any]:
    executable = Path(sys.executable).resolve()
    try:
        executable_sha256 = sha256_bytes(executable.read_bytes())
    except OSError:
        executable_sha256 = None
    return {
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
        "major_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
        "cache_tag": getattr(sys.implementation, "cache_tag", None),
        "executable": str(executable),
        "executable_sha256": executable_sha256,
        "platform": sys.platform,
        "machine": platform.machine(),
    }


def emit(payload: dict[str, Any]) -> None:
    encoded = base64.b64encode(canonical_json_bytes(payload)).decode("ascii")
    print(MARKER + encoded, flush=True)


def main() -> int:
    try:
        request = read_request()
        source = str(request["source"])
        inputs = request.get("inputs", {})
        timeout_seconds = float(request.get("timeout_seconds", 30.0))
        expected_source_sha256 = str(request.get("source_sha256") or "")
        actual_source_sha256 = sha256_bytes(source.encode("utf-8"))
        if expected_source_sha256 and expected_source_sha256 != actual_source_sha256:
            raise RuntimeError("source SHA-256 does not match execution request")

        required = request.get("python_requirement") or {}
        guest_python = python_identity()
        required_major_minor = str(required.get("major_minor") or "")
        if required_major_minor and required_major_minor != guest_python["major_minor"]:
            raise RuntimeError(
                "guest Python major.minor mismatch: "
                f"required {required_major_minor}, got {guest_python['major_minor']}"
            )

        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": "/tmp",
            "TMPDIR": "/tmp",
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "LC_ALL": "C",
            "LANG": "C",
            "TZ": "UTC",
            "SOURCE_DATE_EPOCH": "0",
            "MC_INPUTS_JSON": json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            "MC_EXECUTION_REQUEST_ID": str(request.get("request_id") or ""),
        }

        with tempfile.TemporaryDirectory(prefix="mc-qemu-python-") as temp_dir:
            try:
                completed = subprocess.run(
                    [sys.executable, "-I", "-S", "-c", source],
                    cwd=temp_dir,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    check=False,
                )
                result = {
                    "schema": "main-computer-qemu-python-result-v1",
                    "ok": completed.returncode == 0,
                    "timed_out": False,
                    "request_id": request.get("request_id"),
                    "source_sha256": actual_source_sha256,
                    "exit_code": int(completed.returncode),
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                    "guest_python": guest_python,
                }
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
                result = {
                    "schema": "main-computer-qemu-python-result-v1",
                    "ok": False,
                    "timed_out": True,
                    "request_id": request.get("request_id"),
                    "source_sha256": actual_source_sha256,
                    "exit_code": None,
                    "stdout": stdout,
                    "stderr": stderr,
                    "guest_python": guest_python,
                }

        emit(result)
        return 0
    except Exception as exc:
        emit(
            {
                "schema": "main-computer-qemu-python-result-v1",
                "ok": False,
                "timed_out": False,
                "request_id": None,
                "exit_code": None,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
                "guest_python": python_identity(),
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
