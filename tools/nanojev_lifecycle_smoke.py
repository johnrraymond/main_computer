#!/usr/bin/env python3
"""End-to-end smoke for the managed NanoJev stop/start lifecycle.

The smoke temporarily takes ownership of the normal manager/backend ports and Compose
service, and makes real inference calls. It shuts down any existing manager, stops
(but never removes) the backend container, exercises two manager generations, verifies
idle stop and subsequent restart preserve the container ID, and leaves the backend
stopped. The NanoJev image must be built before running the smoke.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE = ROOT / "docker-compose.nanojev.yml"
DEFAULT_MANAGER = ROOT / "tools" / "nanojev_lifecycle_service.py"
DEFAULT_PROJECT = "main-computer-nanojev"
DEFAULT_IMAGE = "main-computer/nanojev:managed-v2"
DEFAULT_CHECKPOINT = "champion"
DEFAULT_HF_REPO = "johnrraymond/NanoJev-CLEF"


class SmokeError(RuntimeError):
    pass


def emit(event: str, **fields: Any) -> None:
    row = {"event": event, **fields}
    print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)


def http_json(
    url: str,
    *,
    method: str = "GET",
    payload: Any | None = None,
    timeout_seconds: float,
) -> Any:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url=url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


def try_http_json(url: str, *, timeout_seconds: float) -> Any | None:
    try:
        return http_json(url, timeout_seconds=timeout_seconds)
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return None


def run_command(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if check and completed.returncode != 0:
        raise SmokeError(
            f"command failed ({completed.returncode}): {' '.join(args)}\n{completed.stdout.strip()}"
        )
    return completed


def compose_base(args: argparse.Namespace) -> list[str]:
    return [
        args.docker_command,
        "compose",
        "--project-name",
        args.project_name,
        "-f",
        str(args.compose_file),
    ]


def compose_env(args: argparse.Namespace) -> dict[str, str]:
    env = dict(os.environ)
    env["MAIN_COMPUTER_NANOJEV_BIND_PORT"] = str(args.backend_port)
    env["MAIN_COMPUTER_NANOJEV_CHECKPOINT"] = args.checkpoint
    env["MAIN_COMPUTER_NANOJEV_HF_REPO"] = args.hf_repo
    return env


def compose_stop(args: argparse.Namespace) -> subprocess.CompletedProcess[str]:
    stop_timeout = str(max(0, int(round(args.stop_timeout_seconds))))
    result = run_command(
        [*compose_base(args), "stop", "--timeout", stop_timeout, "nanojev"],
        cwd=args.root,
        env=compose_env(args),
    )
    emit(
        "compose_stop",
        exit_code=result.returncode,
        stop_timeout_seconds=args.stop_timeout_seconds,
        output=result.stdout.strip(),
    )
    if result.returncode != 0:
        raise SmokeError(f"NanoJev compose stop failed ({result.returncode}): {result.stdout.strip()}")
    return result


def container_id(args: argparse.Namespace) -> str:
    result = run_command(
        [*compose_base(args), "ps", "-a", "-q", "nanojev"],
        cwd=args.root,
        env=compose_env(args),
    )
    if result.returncode != 0:
        raise SmokeError(f"docker compose ps failed ({result.returncode}): {result.stdout.strip()}")
    return result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""


def container_state(args: argparse.Namespace) -> dict[str, Any]:
    cid = container_id(args)
    if not cid:
        return {"exists": False, "id": None, "status": None, "health": None}
    result = run_command(
        [
            args.docker_command,
            "inspect",
            "--format",
            "{{json .State}}",
            cid,
        ],
        cwd=args.root,
        env=compose_env(args),
    )
    if result.returncode != 0:
        return {"exists": True, "id": cid, "status": "inspect-failed", "health": None}
    try:
        state = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        state = {}
    health = state.get("Health") if isinstance(state, dict) else None
    return {
        "exists": True,
        "id": cid,
        "status": state.get("Status") if isinstance(state, dict) else None,
        "health": health.get("Status") if isinstance(health, dict) else None,
    }


def image_exists(args: argparse.Namespace) -> bool:
    result = run_command(
        [args.docker_command, "image", "inspect", args.image_name],
        cwd=args.root,
        env=compose_env(args),
    )
    return result.returncode == 0


def wait_until(
    description: str,
    predicate: Callable[[], Any | None],
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> Any:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    last: Any = None
    while time.monotonic() <= deadline:
        last = predicate()
        if last:
            return last
        time.sleep(max(0.05, poll_seconds))
    raise SmokeError(f"timed out waiting for {description} after {timeout_seconds:g}s; last={last!r}")


def manager_status(args: argparse.Namespace) -> dict[str, Any] | None:
    # /control/status refreshes backend health before replying. Its client timeout
    # must therefore exceed the manager's backend health request timeout.
    timeout_seconds = max(
        args.control_request_timeout_seconds,
        args.health_request_timeout_seconds + 0.5,
    )
    payload = try_http_json(
        args.manager_url + "/control/status",
        timeout_seconds=timeout_seconds,
    )
    return payload if isinstance(payload, dict) else None


def manager_is_absent(args: argparse.Namespace) -> bool:
    return manager_status(args) is None


def stop_existing_manager(args: argparse.Namespace) -> None:
    status = manager_status(args)
    if status is None:
        emit("preclean_manager", state="absent")
        return
    if status.get("mode") != "lazy-managed":
        raise SmokeError(
            f"{args.manager_url} responds but is not the NanoJev lazy manager: {status!r}"
        )
    emit("preclean_manager", state="shutdown-request", status=status)
    try:
        response = http_json(
            args.manager_url + "/control/shutdown",
            method="POST",
            timeout_seconds=args.shutdown_timeout_seconds,
        )
    except Exception as exc:  # compose_stop below still stops the backend
        emit("preclean_manager_shutdown_error", error=f"{type(exc).__name__}: {exc}")
    else:
        emit("preclean_manager_shutdown_response", response=response)
    wait_until(
        "existing manager to release its HTTP endpoint",
        lambda: True if manager_is_absent(args) else None,
        timeout_seconds=args.manager_exit_timeout_seconds,
        poll_seconds=args.poll_seconds,
    )


def start_manager(args: argparse.Namespace, generation: int) -> subprocess.Popen[Any]:
    command = [
        args.python_command,
        str(args.manager_script),
        "--root",
        str(args.root),
        "--compose-file",
        str(args.compose_file),
        "--project-name",
        args.project_name,
        "--listen-host",
        "127.0.0.1",
        "--listen-port",
        str(args.listen_port),
        "--backend-port",
        str(args.backend_port),
        "--idle-seconds",
        str(args.idle_seconds),
        "--start-timeout-seconds",
        str(args.start_timeout_seconds),
        "--health-poll-seconds",
        str(args.health_poll_seconds),
        "--health-request-timeout-seconds",
        str(args.health_request_timeout_seconds),
        "--stop-timeout-seconds",
        str(args.stop_timeout_seconds),
        "--proxy-timeout-seconds",
        str(args.proxy_timeout_seconds),
        "--sweep-interval-seconds",
        str(args.sweep_interval_seconds),
        "--docker-command",
        args.docker_command,
        "--image-name",
        args.image_name,
        "--checkpoint",
        args.checkpoint,
        "--hf-repo",
        args.hf_repo,
    ]
    emit("manager_start", generation=generation, command=command)
    process = subprocess.Popen(command, cwd=args.root, env=compose_env(args))

    def ready() -> dict[str, Any] | None:
        if process.poll() is not None:
            raise SmokeError(
                f"manager generation {generation} exited during startup with code {process.returncode}"
            )
        status = manager_status(args)
        if not status or status.get("ok") is not True or status.get("mode") != "lazy-managed":
            return None
        return status

    status = wait_until(
        f"manager generation {generation} readiness",
        ready,
        timeout_seconds=args.manager_ready_timeout_seconds,
        poll_seconds=args.poll_seconds,
    )
    emit("manager_ready", generation=generation, pid=process.pid, status=status)
    if status.get("running") is True:
        raise SmokeError(
            f"manager generation {generation} unexpectedly started with backend already ready: {status}"
        )
    return process


def probe_payload(generation: int) -> dict[str, Any]:
    return {
        "states": [
            {
                "id": f"lifecycle-generation-{generation}",
                "state": (
                    "NanoJev lifecycle integration smoke. Evaluate the proposition using ordinary "
                    "semantic judgment. The arithmetic statement two plus two equals four is true."
                ),
                "questions": {
                    "sanity": {
                        "type": "boolean",
                        "instructions": "The arithmetic statement two plus two equals four is true.",
                    }
                },
            }
        ]
    }


def validate_response(response: Any, generation: int) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise SmokeError(f"NanoJev response was not an object: {type(response)!r}")
    states = response.get("states")
    if not isinstance(states, list) or len(states) != 1 or not isinstance(states[0], dict):
        raise SmokeError(f"NanoJev response has unexpected states: {states!r}")
    expected_id = f"lifecycle-generation-{generation}"
    if states[0].get("id") != expected_id:
        raise SmokeError(f"NanoJev response state id mismatch: {states[0].get('id')!r} != {expected_id!r}")
    answers = states[0].get("answers")
    if not isinstance(answers, dict) or not isinstance(answers.get("sanity"), dict):
        raise SmokeError(f"NanoJev response omitted sanity answer: {answers!r}")
    model = response.get("model")
    if not isinstance(model, dict) or model.get("family") != "nanojev-clef":
        raise SmokeError(f"NanoJev response has unexpected model identity: {model!r}")
    return {
        "state_id": expected_id,
        "answer": answers["sanity"],
        "model": model,
    }


def wake_and_respond(args: argparse.Namespace, generation: int) -> dict[str, Any]:
    payload = probe_payload(generation)
    started = time.monotonic()
    emit("request_start", generation=generation, url=args.manager_url + "/api/evaluate")
    try:
        response = http_json(
            args.manager_url + "/api/evaluate",
            method="POST",
            payload=payload,
            timeout_seconds=args.request_timeout_seconds,
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SmokeError(f"NanoJev evaluate returned HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise SmokeError(f"NanoJev evaluate failed: {type(exc).__name__}: {exc}") from exc
    elapsed = time.monotonic() - started
    validated = validate_response(response, generation)
    emit("request_response", generation=generation, elapsed_seconds=elapsed, response=validated)

    status = manager_status(args)
    if not status or status.get("running") is not True or status.get("backend_ready") is not True:
        raise SmokeError(f"manager did not consider backend ready after inference: {status!r}")
    if status.get("dirty") is not True or int(status.get("active_requests", -1)) != 0:
        raise SmokeError(f"manager did not arm idle teardown after inference: {status!r}")

    backend_health = try_http_json(
        args.backend_url + "/api/health",
        timeout_seconds=min(3.0, max(1.0, args.poll_seconds * 8.0)),
    )
    if not isinstance(backend_health, dict) or backend_health.get("ready") is not True:
        raise SmokeError(f"backend health is not ready after inference: {backend_health!r}")

    docker_state = container_state(args)
    if not docker_state["exists"] or docker_state["status"] != "running":
        raise SmokeError(f"NanoJev backend container is not running after inference: {docker_state!r}")

    emit(
        "backend_ready_after_response",
        generation=generation,
        manager_status=status,
        backend_health=backend_health,
        container=docker_state,
    )
    return {
        "request_elapsed_seconds": elapsed,
        "response": validated,
        "manager_status_after_response": status,
        "backend_health_after_response": backend_health,
        "container_after_response": docker_state,
    }


def wait_for_idle_unload(
    args: argparse.Namespace,
    process: subprocess.Popen[Any],
    generation: int,
) -> dict[str, Any]:
    deadline_seconds = args.idle_seconds + args.idle_unload_slack_seconds
    emit(
        "idle_wait_start",
        generation=generation,
        idle_seconds=args.idle_seconds,
        deadline_seconds=deadline_seconds,
    )
    started = time.monotonic()
    last_status: dict[str, Any] | None = None
    last_container: dict[str, Any] | None = None
    while time.monotonic() - started <= deadline_seconds:
        status = manager_status(args)
        if status is None:
            if process.poll() is not None:
                raise SmokeError(
                    "manager exited while waiting for backend idle unload "
                    f"with code {process.returncode}"
                )
            emit(
                "idle_wait_control_probe_retry",
                generation=generation,
                reason="manager process alive but control/status probe did not complete",
            )
            time.sleep(max(0.05, args.poll_seconds))
            continue
        state = container_state(args)
        last_status = status
        last_container = state
        if status.get("running") is False and state["exists"] and state["status"] == "exited":
            backend_health = try_http_json(
                args.backend_url + "/api/health",
                timeout_seconds=min(1.0, max(0.2, args.poll_seconds * 2.0)),
            )
            if backend_health is not None:
                raise SmokeError(
                    f"manager/container report backend unloaded but backend URL still answers: {backend_health!r}"
                )
            elapsed = time.monotonic() - started
            emit(
                "idle_unload_complete",
                generation=generation,
                elapsed_seconds=elapsed,
                manager_status=status,
                container=state,
            )
            return {
                "elapsed_seconds": elapsed,
                "manager_status": status,
                "container": state,
            }
        time.sleep(max(0.05, args.poll_seconds))
    raise SmokeError(
        "idle backend unload timed out; "
        f"last_manager_status={last_status!r}; last_container={last_container!r}"
    )


def shutdown_manager(args: argparse.Namespace, process: subprocess.Popen[Any], generation: int) -> dict[str, Any]:
    response: Any = None
    status = manager_status(args)
    if status is not None:
        emit("manager_shutdown_start", generation=generation, status=status)
        response = http_json(
            args.manager_url + "/control/shutdown",
            method="POST",
            timeout_seconds=args.shutdown_timeout_seconds,
        )
    try:
        process.wait(timeout=args.manager_exit_timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise SmokeError(f"manager generation {generation} did not exit after shutdown") from exc
    if process.returncode != 0:
        raise SmokeError(f"manager generation {generation} exited with code {process.returncode}")
    if not manager_is_absent(args):
        raise SmokeError(f"manager generation {generation} still answers after process exit")
    state = container_state(args)
    if not state["exists"] or state["status"] != "exited":
        raise SmokeError(f"backend was removed or survived running after manager shutdown: {state!r}")
    emit(
        "manager_shutdown_complete",
        generation=generation,
        return_code=process.returncode,
        response=response,
        container=state,
    )
    return {"response": response, "return_code": process.returncode, "container": state}


def cleanup_process(args: argparse.Namespace, process: subprocess.Popen[Any] | None) -> None:
    if process is not None and process.poll() is None:
        try:
            status = manager_status(args)
            if status and status.get("mode") == "lazy-managed":
                http_json(
                    args.manager_url + "/control/shutdown",
                    method="POST",
                    timeout_seconds=args.shutdown_timeout_seconds,
                )
                process.wait(timeout=args.manager_exit_timeout_seconds)
        except Exception as exc:
            emit("cleanup_manager_shutdown_error", error=f"{type(exc).__name__}: {exc}")
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    try:
        compose_stop(args)
    except Exception as exc:
        emit("cleanup_compose_stop_error", error=f"{type(exc).__name__}: {exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NanoJev stop/start lifecycle smoke (uses live model)")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE)
    parser.add_argument("--manager-script", type=Path, default=DEFAULT_MANAGER)
    parser.add_argument("--python-command", default=sys.executable)
    parser.add_argument("--docker-command", default="docker")
    parser.add_argument("--project-name", default=DEFAULT_PROJECT)
    parser.add_argument("--image-name", default=DEFAULT_IMAGE)
    parser.add_argument("--checkpoint", default=os.environ.get("MAIN_COMPUTER_NANOJEV_CHECKPOINT", DEFAULT_CHECKPOINT))
    parser.add_argument("--hf-repo", default=os.environ.get("MAIN_COMPUTER_NANOJEV_HF_REPO", DEFAULT_HF_REPO))
    parser.add_argument("--listen-port", type=int, default=9765)
    parser.add_argument("--backend-port", type=int, default=9766)
    parser.add_argument("--manager-generations", type=int, default=2)
    parser.add_argument("--idle-seconds", type=float, default=3.0)
    parser.add_argument("--sweep-interval-seconds", type=float, default=0.25)
    parser.add_argument("--health-poll-seconds", type=float, default=0.25)
    parser.add_argument("--health-request-timeout-seconds", type=float, default=0.5)
    parser.add_argument("--control-request-timeout-seconds", type=float, default=2.0)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--start-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--proxy-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=360.0)
    parser.add_argument("--manager-ready-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--idle-unload-slack-seconds", type=float, default=15.0)
    parser.add_argument("--shutdown-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--manager-exit-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--stop-timeout-seconds", type=float, default=2.0)
    args = parser.parse_args()
    args.root = args.root.resolve()
    args.compose_file = args.compose_file.resolve()
    args.manager_script = args.manager_script.resolve()
    args.manager_url = f"http://127.0.0.1:{args.listen_port}"
    args.backend_url = f"http://127.0.0.1:{args.backend_port}"
    if args.manager_generations < 1:
        parser.error("--manager-generations must be >= 1")
    if args.listen_port == args.backend_port:
        parser.error("--listen-port and --backend-port must differ")
    for name in (
        "idle_seconds",
        "sweep_interval_seconds",
        "health_poll_seconds",
        "health_request_timeout_seconds",
        "control_request_timeout_seconds",
        "poll_seconds",
        "start_timeout_seconds",
        "proxy_timeout_seconds",
        "request_timeout_seconds",
        "manager_ready_timeout_seconds",
        "idle_unload_slack_seconds",
        "shutdown_timeout_seconds",
        "manager_exit_timeout_seconds",
        "stop_timeout_seconds",
    ):
        if getattr(args, name) < 0:
            parser.error(f"--{name.replace('_', '-')} must be >= 0")
    return args


def main() -> int:
    args = parse_args()
    for path, label in ((args.compose_file, "compose file"), (args.manager_script, "manager script")):
        if not path.is_file():
            raise SmokeError(f"{label} does not exist: {path}")

    emit(
        "smoke_start",
        stops_live_backend=True,
        root=str(args.root),
        manager_url=args.manager_url,
        backend_url=args.backend_url,
        manager_generations=args.manager_generations,
        idle_seconds=args.idle_seconds,
        sweep_interval_seconds=args.sweep_interval_seconds,
        health_request_timeout_seconds=args.health_request_timeout_seconds,
        control_request_timeout_seconds=max(
            args.control_request_timeout_seconds,
            args.health_request_timeout_seconds + 0.5,
        ),
        stop_timeout_seconds=args.stop_timeout_seconds,
    )

    process: subprocess.Popen[Any] | None = None
    generations: list[dict[str, Any]] = []
    try:
        stop_existing_manager(args)
        compose_stop(args)
        pid_file = args.root / ".main_computer_nanojev_manager.pid"
        if pid_file.exists():
            pid_file.unlink()
            emit("stale_manager_pid_file_removed", path=str(pid_file))

        has_image = image_exists(args)
        emit("image_preflight", image=args.image_name, exists=has_image)
        if not has_image:
            raise SmokeError(
                f"NanoJev image {args.image_name!r} is missing. Build it explicitly first."
            )

        previous_id: str | None = container_state(args)["id"]
        for generation in range(1, args.manager_generations + 1):
            before = container_state(args)
            if before["exists"] and before["status"] != "exited":
                raise SmokeError(f"backend must be stopped before manager generation {generation}: {before!r}")
            process = start_manager(args, generation)
            request_result = wake_and_respond(args, generation)
            started_id = request_result["container_after_response"]["id"]
            if previous_id is not None and started_id != previous_id:
                raise SmokeError(f"container ID changed after restart: {previous_id} -> {started_id}")
            idle_result = wait_for_idle_unload(args, process, generation)
            if idle_result["container"]["id"] != started_id:
                raise SmokeError("container ID changed during idle stop")
            shutdown_result = shutdown_manager(args, process, generation)
            if shutdown_result["container"]["id"] != started_id:
                raise SmokeError("container ID changed during manager shutdown")
            previous_id = started_id
            process = None
            generations.append(
                {
                    "generation": generation,
                    "request": request_result,
                    "idle_unload": idle_result,
                    "shutdown": shutdown_result,
                }
            )

        report = {
            "ok": True,
            "schema": "main-computer.nanojev-lifecycle-smoke.v1",
            "manager_generations": args.manager_generations,
            "manager_url": args.manager_url,
            "backend_url": args.backend_url,
            "idle_seconds": args.idle_seconds,
            "generations": generations,
            "final_manager_absent": manager_is_absent(args),
            "final_container": container_state(args),
        }
        emit("smoke_pass", report=report)
        return 0
    except Exception as exc:
        emit("smoke_fail", error=f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        cleanup_process(args, process)


if __name__ == "__main__":
    raise SystemExit(main())
