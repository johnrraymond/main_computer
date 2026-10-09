"""One-shot, single-flight NanoJev image and stopped-container provisioning.

Dispatched from ./start in a separate process. Builds the image only if absent,
then creates the Compose container only if absent, without starting it. Existing
containers are never recreated or stopped. Results live in runtime/start_stop.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Iterator

IMAGE = "main-computer/nanojev:managed-v2"
STATUS_NAME = "nanojev-image-bootstrap.json"
LOCK_NAME = "nanojev-image-bootstrap.lock"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_status(root: Path) -> dict[str, object] | None:
    try:
        payload = json.loads((root / "runtime" / "start_stop" / STATUS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


@contextmanager
def single_flight(lock_path: Path) -> Iterator[bool]:
    """Cross-process advisory lock; kernel releases it if the worker dies."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\x00")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_status(status_path: Path, **fields: object) -> None:
    payload = {
        "schema": "main-computer.nanojev.image-bootstrap.v1",
        "updated_at": _utc_now(),
        "pid": os.getpid(),
        **fields,
    }
    # Per-PID temporary name avoids collisions with any unrelated readers.
    temporary = status_path.with_name(f".{status_path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, status_path)
    finally:
        temporary.unlink(missing_ok=True)


def _inspect_image(docker: str, image: str, root: Path, *, timeout: float = 30.0) -> bool:
    try:
        result = subprocess.run(
            [docker, "image", "inspect", image],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Docker image inspection unavailable: {exc}") from exc
    if result.returncode == 0:
        return True
    detail = (result.stderr + "\n" + result.stdout).strip()
    if "No such image:" in detail or "No such object:" in detail:
        return False
    raise RuntimeError(f"Docker image inspection failed ({result.returncode}): {detail}")


def _container_id(docker: str, compose_file: Path, project_name: str, root: Path, *, timeout: float = 30.0) -> str:
    command = [docker, "compose", "--project-name", project_name, "-f", str(compose_file),
               "ps", "-a", "-q", "nanojev"]
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"NanoJev container inspection unavailable: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"NanoJev container inspection failed ({result.returncode}): "
                           f"{(result.stderr or result.stdout).strip()}")
    ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(ids) > 1:
        raise RuntimeError(f"Expected one NanoJev Compose container; found {len(ids)}")
    return ids[0] if ids else ""


def _ensure_container(
    docker: str, compose_file: Path, project_name: str, root: Path, log_path: Path,
    *, create_timeout: float = 180.0,
) -> tuple[str, str]:
    existing = _container_id(docker, compose_file, project_name, root)
    if existing:
        return "already-present", existing
    # `create` does NOT start the service, and --no-recreate preserves one that
    # appeared since our inspection (e.g. a concurrent game wake-up).
    command = [docker, "compose", "--project-name", project_name, "-f", str(compose_file),
               "create", "--no-build", "--no-recreate", "--pull", "never", "nanojev"]
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"[{_utc_now()}] Running: {command!r}\n")
        log.flush()
        try:
            result = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=create_timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"NanoJev Compose container creation failed: {exc}; see {log_path}") from exc
    if result.returncode != 0:
        # A lazy manager request can create the same container concurrently.
        # If it now exists, leave it alone and treat the race as successful.
        existing = _container_id(docker, compose_file, project_name, root)
        if existing:
            return "already-present", existing
        raise RuntimeError(f"NanoJev Compose create exited {result.returncode}; see {log_path}")
    container = _container_id(docker, compose_file, project_name, root)
    if not container:
        raise RuntimeError("NanoJev Compose create succeeded but no container was found")
    return "created", container


def run_bootstrap(
    *, root: Path, compose_file: Path, project_name: str = "main-computer-nanojev",
    docker: str = "docker", image: str = IMAGE, build_timeout: float = 3600.0,
) -> str:
    runtime = root / "runtime" / "start_stop"
    runtime.mkdir(parents=True, exist_ok=True)
    status_path = runtime / STATUS_NAME
    with single_flight(runtime / LOCK_NAME) as acquired:
        if not acquired:
            return "already-running"
        started = _utc_now()
        # Unique build logs survive repeated ./start invocations.
        log_path = runtime / f"nanojev-image-bootstrap-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}.log"
        common = {"image": image, "started_at": started, "log_path": str(log_path)}
        _write_status(status_path, state="checking", **common)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"[{_utc_now()}] Checking NanoJev image {image} and Compose container\n")
        try:
            if _inspect_image(docker, image, root):
                image_result = "already-present"
            else:
                _write_status(status_path, state="building", **common)
                command = [docker, "compose", "--project-name", project_name, "-f", str(compose_file),
                           "build", "--progress", "plain", "nanojev"]
                # Never pipe an unbounded build into memory; keep a durable trace.
                with log_path.open("w", encoding="utf-8") as log:
                    log.write(f"[{_utc_now()}] Running: {command!r}\n")
                    log.flush()
                    try:
                        result = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                                timeout=build_timeout, check=False)
                    except subprocess.TimeoutExpired as exc:
                        raise RuntimeError(f"NanoJev Docker image build timed out after {build_timeout:g}s") from exc
                if result.returncode != 0:
                    raise RuntimeError(f"NanoJev Docker build exited {result.returncode}; see {log_path}")
                if not _inspect_image(docker, image, root):
                    raise RuntimeError(f"NanoJev build completed but image {image!r} is not present")
                image_result = "built"
            _write_status(status_path, state="creating", image_result=image_result, **common)
            container_result, container_id = _ensure_container(
                docker, compose_file, project_name, root, log_path,
            )
            _write_status(status_path, state="ready", result=image_result,
                          container_result=container_result, container_id=container_id,
                          finished_at=_utc_now(), **common)
            return "ready"
        except Exception as exc:
            _write_status(status_path, state="failed", error=str(exc), finished_at=_utc_now(), **common)
            return "failed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Asynchronous, single-flight NanoJev image and container bootstrap")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--compose-file", required=True, type=Path)
    parser.add_argument("--project-name", default="main-computer-nanojev")
    parser.add_argument("--docker-command", default="docker")
    parser.add_argument("--image-name", default=IMAGE)
    parser.add_argument("--build-timeout-seconds", type=float, default=3600.0)
    args = parser.parse_args(argv)
    result = run_bootstrap(root=args.root.resolve(), compose_file=args.compose_file.resolve(),
                           project_name=args.project_name, docker=args.docker_command,
                           image=args.image_name, build_timeout=max(1.0, args.build_timeout_seconds))
    print(f"NANOJEV_IMAGE_BOOTSTRAP: {result}", flush=True)
    return 1 if result == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
