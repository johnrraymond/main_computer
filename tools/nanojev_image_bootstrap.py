"""Single-flight NanoJev Docker source/version provisioning from ./start.

Build only on changed source inputs. Safely replace stopped stale containers;
leave live ones untouched and notify the lazy manager for idle-time activation.
All transitions are evidenced in runtime/start_stop and never run inference.
"""
from __future__ import annotations

import argparse
import hashlib
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
SOURCE_LABEL = "org.main-computer.nanojev.source-sha256"
# Every local file copied by docker/nanojev/Dockerfile. Dockerfile and Compose
# configuration themselves also invalidate the built image/container contract.
IMAGE_INPUTS = (
    "docker/nanojev/Dockerfile",
    "docker/nanojev/download_release.py",
    "docker/nanojev/entrypoint.py",
    "docker/nanojev/clef_service.py",
    "tools/nanojev_three_backbone_clef_sized_live_train_smoke.py",
    "tools/nanojev_three_backbone_clef_sized_live_train.py",
    "tools/nanojev_three_backbone_clef_tinystories_structured_supervision_train.py",
    "tools/nanojev_objective_api.py",
    "docker-compose.nanojev.yml",
)


def source_fingerprint(root: Path, compose_file: Path) -> str:
    digest = hashlib.sha256()
    for relative in IMAGE_INPUTS:
        path = compose_file if relative == "docker-compose.nanojev.yml" else root / relative
        if not path.is_file():
            raise RuntimeError(f"NanoJev build source missing: {path}")
        content = path.read_bytes()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


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


def _inspect_field(docker: str, args: list[str], root: Path, *, timeout: float = 30.0) -> str:
    try:
        result = subprocess.run([docker, *args], cwd=root, capture_output=True,
                                text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"NanoJev Docker version inspection unavailable: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"NanoJev Docker inspection failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def _image_source_fingerprint(docker: str, image: str, root: Path) -> str:
    return _inspect_field(docker, ["image", "inspect", "--format",
                '{{ index .Config.Labels "' + SOURCE_LABEL + '" }}', image], root)


def _image_id(docker: str, image: str, root: Path) -> str:
    return _inspect_field(docker, ["image", "inspect", "--format", "{{.Id}}", image], root)


def _container_image_id(docker: str, container: str, root: Path) -> str:
    return _inspect_field(docker, ["inspect", "--format", "{{.Image}}", container], root)


def _container_running(docker: str, container: str, root: Path) -> bool:
    value = _inspect_field(docker, ["inspect", "--format", "{{.State.Running}}", container], root)
    if value not in {"true", "false"}:
        raise RuntimeError(f"Unexpected NanoJev container running state: {value!r}")
    return value == "true"


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
    *, image: str = IMAGE, create_timeout: float = 180.0,
) -> tuple[str, str]:
    existing = _container_id(docker, compose_file, project_name, root)
    stale = False
    if existing:
        stale = _container_image_id(docker, existing, root) != _image_id(docker, image, root)
        if not stale:
            return "already-present", existing
        if _container_running(docker, existing, root):
            # Never interrupt a job using the old image. The manager owns
            # the subsequent transition after active_requests reaches zero.
            return "pending-activation", existing
    command = [docker, "compose", "--project-name", project_name, "-f", str(compose_file),
               "create", "--no-build", "--pull", "never"]
    if stale:
        command.append("--force-recreate")
    else:
        command.append("--no-recreate")
    command.append("nanojev")
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"[{_utc_now()}] Running: {command!r}\n")
        log.flush()
        try:
            result = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=create_timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"NanoJev Compose container creation failed: {exc}; see {log_path}") from exc
    if result.returncode != 0:
        current = _container_id(docker, compose_file, project_name, root)
        if not stale and current:
            return "already-present", current
        raise RuntimeError(f"NanoJev Compose create exited {result.returncode}; see {log_path}")
    container = _container_id(docker, compose_file, project_name, root)
    if not container:
        raise RuntimeError("NanoJev Compose create succeeded but no container was found")
    if _container_image_id(docker, container, root) != _image_id(docker, image, root):
        raise RuntimeError("NanoJev container uses the wrong image after Compose create")
    return ("recreated" if stale else "created"), container


def activate_pending_image(
    *, root: Path, compose_file: Path, project_name: str, docker: str, image: str,
    stop_timeout: float = 30.0,
) -> str:
    """Called by the lazy manager only with its transitions locked and zero jobs.

    A second process cannot race another bootstrap generation. The manager
    decides when it is safe to stop, never the background image builder.
    """
    runtime = root / "runtime" / "start_stop"
    with single_flight(runtime / LOCK_NAME) as acquired:
        if not acquired:
            raise RuntimeError("NanoJev image bootstrap is currently changing the image")
        status_path = runtime / STATUS_NAME
        status = read_status(root)
        if not status or status.get("state") != "pending-activation" or status.get("image") != image:
            return "no-pending-update"
        desired = source_fingerprint(root, compose_file)
        if desired != status.get("source_sha256") or _image_source_fingerprint(docker, image, root) != desired:
            raise RuntimeError("NanoJev pending image no longer matches checked source inputs; rerun start.bat")
        log_path = Path(str(status["log_path"]))
        current = _container_id(docker, compose_file, project_name, root)
        if current and _container_image_id(docker, current, root) != _image_id(docker, image, root):
            # Stop is allowed ONLY after the manager verifies zero in-flight
            # requests, including requests still completing their HTTP reply.
            command = [docker, "compose", "--project-name", project_name, "-f", str(compose_file),
                       "stop", "--timeout", str(max(1, int(stop_timeout))), "nanojev"]
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"[{_utc_now()}] Safe idle NanoJev image activation: {command!r}\n")
                log.flush()
                result = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=stop_timeout + 30.0, check=False)
            if result.returncode != 0:
                raise RuntimeError(f"NanoJev idle stop failed ({result.returncode}); see {log_path}")
        container_result, container_id = _ensure_container(docker, compose_file, project_name, root, log_path, image=image)
        if container_result == "pending-activation":
            raise RuntimeError("NanoJev container was still running during safe activation")
        preserved = {k: v for k, v in status.items() if k not in {"schema", "updated_at", "pid"}}
        _write_status(status_path, **{**preserved, "state": "ready", "container_result": container_result,
                                       "container_id": container_id, "finished_at": _utc_now()})
        return container_result


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
        try:
            fingerprint = source_fingerprint(root, compose_file)
        except Exception as exc:
            _write_status(status_path, state="failed", image=image, error=str(exc),
                          started_at=started, finished_at=_utc_now())
            return "failed"
        common = {"image": image, "source_sha256": fingerprint,
                  "started_at": started, "log_path": str(log_path)}
        _write_status(status_path, state="checking", **common)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"[{_utc_now()}] Checking NanoJev image {image} and Compose container\n")
        try:
            image_present = _inspect_image(docker, image, root)
            if image_present and _image_source_fingerprint(docker, image, root) == fingerprint:
                image_result = "already-current"
            else:
                _write_status(status_path, state="building", **common)
                command = [docker, "compose", "--project-name", project_name, "-f", str(compose_file),
                           "build", "--progress", "plain", "nanojev"]
                # Never pipe an unbounded build into memory; keep a durable trace.
                with log_path.open("a", encoding="utf-8") as log:
                    log.write(f"[{_utc_now()}] Running: {command!r}\n")
                    log.flush()
                    try:
                        build_env = dict(os.environ)
                        build_env["MAIN_COMPUTER_NANOJEV_SOURCE_SHA256"] = fingerprint
                        result = subprocess.run(command, cwd=root, env=build_env,
                                                stdout=log, stderr=subprocess.STDOUT,
                                                timeout=build_timeout, check=False)
                    except subprocess.TimeoutExpired as exc:
                        raise RuntimeError(f"NanoJev Docker image build timed out after {build_timeout:g}s") from exc
                if result.returncode != 0:
                    raise RuntimeError(f"NanoJev Docker build exited {result.returncode}; see {log_path}")
                if (not _inspect_image(docker, image, root)
                        or _image_source_fingerprint(docker, image, root) != fingerprint):
                    raise RuntimeError("NanoJev build finished but did not produce the expected source fingerprint")
                image_result = "built"
            if source_fingerprint(root, compose_file) != fingerprint:
                raise RuntimeError("NanoJev source files changed during image bootstrap; retry start.bat")
            _write_status(status_path, state="creating", image_result=image_result, **common)
            container_result, container_id = _ensure_container(
                docker, compose_file, project_name, root, log_path, image=image,
            )
            state = "pending-activation" if container_result == "pending-activation" else "ready"
            _write_status(status_path, state=state, result=image_result,
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
