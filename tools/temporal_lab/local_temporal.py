from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Sequence


DEFAULT_CONTAINER_NAME = "main-computer-temporal-dev"
DEFAULT_IMAGE = "temporalio/temporal:latest"
DEFAULT_NAMESPACE = "scheduler-lab"
DEFAULT_TASK_QUEUE = "scheduler-lab-fake-tokens"
DEFAULT_VOLUME = "main-computer-temporal-dev-data"
DEFAULT_GRPC_PORT = 7233
DEFAULT_UI_PORT = 8233
DEFAULT_DB_FILENAME = "/data/temporal.db"


class TemporalBootstrapError(RuntimeError):
    """Raised when the local Temporal development container cannot be readied."""


@dataclass(frozen=True)
class TemporalConfig:
    container_name: str = DEFAULT_CONTAINER_NAME
    image: str = DEFAULT_IMAGE
    namespace: str = DEFAULT_NAMESPACE
    task_queue: str = DEFAULT_TASK_QUEUE
    volume: str = DEFAULT_VOLUME
    grpc_port: int = DEFAULT_GRPC_PORT
    ui_port: int = DEFAULT_UI_PORT
    db_filename: str = DEFAULT_DB_FILENAME
    bind_host: str = "127.0.0.1"
    persist: bool = False

    @property
    def temporal_address(self) -> str:
        return f"localhost:{self.grpc_port}"

    @property
    def ui_url(self) -> str:
        return f"http://localhost:{self.ui_port}"

    @property
    def container_temporal_address(self) -> str:
        return "127.0.0.1:7233"


def run_command(
    args: Sequence[str],
    *,
    capture: bool = True,
    check: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=check,
        timeout=timeout,
    )


def require_docker() -> None:
    if shutil.which("docker") is None:
        raise TemporalBootstrapError("Docker CLI was not found on PATH.")
    probe = run_command(["docker", "version", "--format", "{{.Server.Version}}"], timeout=10)
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "").strip()
        raise TemporalBootstrapError(f"Docker is not reachable. {detail}".strip())


def docker_container_exists(container_name: str) -> bool:
    result = run_command(
        ["docker", "container", "inspect", container_name, "--format", "{{.Name}}"],
        timeout=10,
    )
    return result.returncode == 0


def docker_container_running(container_name: str) -> bool:
    result = run_command(
        ["docker", "container", "inspect", container_name, "--format", "{{.State.Running}}"],
        timeout=10,
    )
    return result.returncode == 0 and (result.stdout or "").strip().lower() == "true"


def build_run_command(config: TemporalConfig) -> list[str]:
    """Return the docker command used to create the local dev Temporal container.

    The default is intentionally in-memory SQLite. On Docker Desktop for
    Windows, the persisted SQLite dev-server path can fail before Temporal is
    ready with "unable to open database file: out of memory (14)". Persistence
    remains available as an explicit opt-in via ``--persist``.
    """

    command = [
        "docker",
        "run",
        "-d",
        "--name",
        config.container_name,
        "-p",
        f"{config.bind_host}:{config.grpc_port}:7233",
        "-p",
        f"{config.bind_host}:{config.ui_port}:8233",
    ]
    if config.persist:
        command.extend(["-v", f"{config.volume}:/data"])
    command.extend(
        [
            config.image,
            "server",
            "start-dev",
            "--ip",
            "0.0.0.0",
            "--ui-ip",
            "0.0.0.0",
            "--port",
            "7233",
            "--ui-port",
            "8233",
            "--namespace",
            config.namespace,
        ]
    )
    if config.persist:
        command.extend(["--db-filename", config.db_filename])
    return command


def tcp_ready(host: str, port: int, timeout_seconds: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def wait_for_temporal(config: TemporalConfig, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        if not docker_container_running(config.container_name):
            logs = run_command(["docker", "logs", "--tail", "80", config.container_name], timeout=10)
            detail = (logs.stderr or logs.stdout or "").strip()
            raise TemporalBootstrapError(
                f"Temporal container {config.container_name!r} is not running.\n{detail}"
            )

        # TCP readiness is intentionally checked before CLI readiness because
        # it gives a clear local-port failure if Docker's bind failed.
        if not tcp_ready("127.0.0.1", config.grpc_port, timeout_seconds=1.0):
            time.sleep(0.5)
            continue

        describe = run_command(
            [
                "docker",
                "exec",
                config.container_name,
                "temporal",
                "operator",
                "namespace",
                "describe",
                "--namespace",
                config.namespace,
                "--address",
                config.container_temporal_address,
            ],
            timeout=10,
        )
        if describe.returncode == 0:
            return
        last_error = (describe.stderr or describe.stdout or "").strip()
        time.sleep(1.0)

    logs = run_command(["docker", "logs", "--tail", "120", config.container_name], timeout=10)
    detail = "\n".join(
        part
        for part in [last_error, (logs.stderr or logs.stdout or "").strip()]
        if part
    )
    raise TemporalBootstrapError(
        f"Timed out waiting for Temporal namespace {config.namespace!r} at "
        f"{config.temporal_address}.\n{detail}"
    )


def print_ready(config: TemporalConfig) -> None:
    payload = {
        "status": "ready",
        "container": config.container_name,
        "image": config.image,
        "namespace": config.namespace,
        "task_queue": config.task_queue,
        "temporal_address": config.temporal_address,
        "ui_url": config.ui_url,
        "bind_host": config.bind_host,
        "persistent": config.persist,
    }
    if config.persist:
        payload["volume"] = config.volume
        payload["db_filename"] = config.db_filename
    print(json.dumps(payload, indent=2, sort_keys=True))
    print()
    print("Shell environment:")
    print(f'  export TEMPORAL_ADDRESS="{config.temporal_address}"')
    print(f'  export TEMPORAL_NAMESPACE="{config.namespace}"')
    print(f'  export TEMPORAL_TASK_QUEUE="{config.task_queue}"')
    print()
    print("PowerShell environment:")
    print(f'  $env:TEMPORAL_ADDRESS="{config.temporal_address}"')
    print(f'  $env:TEMPORAL_NAMESPACE="{config.namespace}"')
    print(f'  $env:TEMPORAL_TASK_QUEUE="{config.task_queue}"')


def create_container(config: TemporalConfig) -> None:
    created = run_command(build_run_command(config), timeout=120)
    if created.returncode != 0:
        detail = (created.stderr or created.stdout or "").strip()
        raise TemporalBootstrapError(f"Failed to create Temporal container: {detail}")


def remove_container(container_name: str) -> None:
    rm = run_command(["docker", "rm", "-f", container_name], capture=False)
    if rm.returncode != 0:
        raise TemporalBootstrapError(f"Failed to remove container {container_name!r}.")


def up(config: TemporalConfig, *, pull: bool, timeout_seconds: float) -> None:
    require_docker()
    if pull:
        pull_result = run_command(["docker", "pull", config.image], capture=False)
        if pull_result.returncode != 0:
            raise TemporalBootstrapError(f"docker pull failed for image {config.image!r}.")

    if docker_container_exists(config.container_name):
        if not docker_container_running(config.container_name):
            # Recreate stopped containers instead of restarting them. This lets
            # the script recover from an older failed bootstrap whose container
            # was created with a now-bad command, such as persisted SQLite.
            remove_container(config.container_name)
            create_container(config)
    else:
        create_container(config)

    wait_for_temporal(config, timeout_seconds=timeout_seconds)
    print_ready(config)


def status(config: TemporalConfig) -> None:
    require_docker()
    exists = docker_container_exists(config.container_name)
    running = exists and docker_container_running(config.container_name)
    ready = running and tcp_ready("127.0.0.1", config.grpc_port, timeout_seconds=1.0)
    payload = {
        "container": config.container_name,
        "exists": exists,
        "running": running,
        "tcp_ready": ready,
        "temporal_address": config.temporal_address,
        "ui_url": config.ui_url,
        "namespace": config.namespace,
        "task_queue": config.task_queue,
        "persistent": config.persist,
    }
    if config.persist:
        payload["volume"] = config.volume
        payload["db_filename"] = config.db_filename
    print(json.dumps(payload, indent=2, sort_keys=True))


def down(config: TemporalConfig, *, delete_data: bool) -> None:
    require_docker()
    if docker_container_exists(config.container_name):
        remove_container(config.container_name)
    if delete_data:
        volume_rm = run_command(["docker", "volume", "rm", "-f", config.volume], capture=False)
        if volume_rm.returncode != 0:
            raise TemporalBootstrapError(f"Failed to remove volume {config.volume!r}.")
    print(
        json.dumps(
            {
                "status": "stopped",
                "container": config.container_name,
                "volume_deleted": bool(delete_data),
                "volume": config.volume,
            },
            indent=2,
            sort_keys=True,
        )
    )


def logs(config: TemporalConfig, *, follow: bool, tail: str) -> int:
    require_docker()
    args = ["docker", "logs", "--tail", tail]
    if follow:
        args.append("-f")
    args.append(config.container_name)
    return run_command(args, capture=False).returncode


def env(config: TemporalConfig) -> None:
    print_ready(config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Bring up a local Temporal development server container for scheduler-lab experiments."
        )
    )
    parser.add_argument(
        "command",
        choices=["up", "status", "down", "logs", "env"],
        help="Action to run.",
    )
    parser.add_argument("--container-name", default=os.getenv("TEMPORAL_DEV_CONTAINER", DEFAULT_CONTAINER_NAME))
    parser.add_argument("--image", default=os.getenv("TEMPORAL_DOCKER_IMAGE", DEFAULT_IMAGE))
    parser.add_argument("--namespace", default=os.getenv("TEMPORAL_NAMESPACE", DEFAULT_NAMESPACE))
    parser.add_argument("--task-queue", default=os.getenv("TEMPORAL_TASK_QUEUE", DEFAULT_TASK_QUEUE))
    parser.add_argument("--volume", default=os.getenv("TEMPORAL_DEV_VOLUME", DEFAULT_VOLUME))
    parser.add_argument("--grpc-port", type=int, default=int(os.getenv("TEMPORAL_GRPC_PORT", str(DEFAULT_GRPC_PORT))))
    parser.add_argument("--ui-port", type=int, default=int(os.getenv("TEMPORAL_UI_PORT", str(DEFAULT_UI_PORT))))
    parser.add_argument(
        "--bind-host",
        default=os.getenv("TEMPORAL_BIND_HOST", "127.0.0.1"),
        help="Host IP for Docker port bindings. Defaults to 127.0.0.1 for local-only exposure.",
    )
    parser.add_argument(
        "--public-bind",
        action="store_true",
        help="Bind Temporal dev ports to 0.0.0.0 instead of localhost. Avoid unless isolated.",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help=(
            "Persist Temporal dev-server SQLite state in a Docker volume. "
            "Off by default because it can fail on Docker Desktop/Windows."
        ),
    )
    parser.add_argument("--pull", action="store_true", help="Pull the Temporal image before starting.")
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--delete-data", action="store_true", help="Remove the Docker volume on down.")
    parser.add_argument("--follow", action="store_true", help="Follow logs.")
    parser.add_argument("--tail", default="160", help="Number of log lines for logs command.")
    return parser


def config_from_args(args: argparse.Namespace) -> TemporalConfig:
    bind_host = "0.0.0.0" if args.public_bind else args.bind_host
    return TemporalConfig(
        container_name=args.container_name,
        image=args.image,
        namespace=args.namespace,
        task_queue=args.task_queue,
        volume=args.volume,
        grpc_port=args.grpc_port,
        ui_port=args.ui_port,
        bind_host=bind_host,
        persist=bool(args.persist),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = config_from_args(args)
    try:
        if args.command == "up":
            up(config, pull=args.pull, timeout_seconds=args.timeout_seconds)
            return 0
        if args.command == "status":
            status(config)
            return 0
        if args.command == "down":
            down(config, delete_data=args.delete_data)
            return 0
        if args.command == "logs":
            return logs(config, follow=args.follow, tail=args.tail)
        if args.command == "env":
            env(config)
            return 0
    except TemporalBootstrapError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
