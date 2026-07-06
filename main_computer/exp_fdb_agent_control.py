from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from main_computer.container_runtime import resolve_container_runtime


DEFAULT_EXP_FDB_AGENT_HUB_URL = "http://host.docker.internal:8870"
DEFAULT_EXP_FDB_AGENT_HOST_HUB_URL = "http://127.0.0.1:8870"
DEFAULT_EXP_FDB_AGENT_IMAGE = "python:3.12-slim"
DEFAULT_EXP_FDB_AGENT_RUNTIME_ROOT = Path("runtime") / "exp-fdb-agent-runs"
DEFAULT_EXP_FDB_AGENT_CONTAINER_PREFIX = "main-computer-exp-fdb-agent"
DEFAULT_EXP_FDB_AGENT_HEARTBEAT_SECONDS = 5.0


def _container_args(*args: object) -> list[str]:
    return resolve_container_runtime(probe=False).container_args(*args)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_root_from_args(value: str | Path | None = None) -> Path:
    return Path(value).resolve() if value else Path.cwd().resolve()


def clean_run_id(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-_.")
    return text[:96] or datetime.now(timezone.utc).strftime("agent-%Y%m%d-%H%M%S")


def make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("agent-%Y%m%d-%H%M%S")


def container_name_for_run(run_id: str, *, prefix: str = DEFAULT_EXP_FDB_AGENT_CONTAINER_PREFIX) -> str:
    clean = clean_run_id(run_id).lower()
    # Docker container names are easier to script when they avoid dots.
    clean = clean.replace(".", "-").replace("_", "-")
    return f"{prefix}-{clean}"[:128].rstrip("-")


@dataclass(frozen=True)
class AgentContainerSpec:
    run_id: str
    repo_root: Path
    run_dir: Path
    hub_url: str = DEFAULT_EXP_FDB_AGENT_HUB_URL
    host_hub_url: str = DEFAULT_EXP_FDB_AGENT_HOST_HUB_URL
    image: str = DEFAULT_EXP_FDB_AGENT_IMAGE
    container_name: str = ""
    worker_ring: int = 2
    pricing_scheme: str = "ring2-fixed"
    max_total_credits: int = 0
    credits_per_job: int = 1
    heartbeat_seconds: float = DEFAULT_EXP_FDB_AGENT_HEARTBEAT_SECONDS
    detach: bool = True
    add_host_gateway: bool = True
    extra_env: dict[str, str] = field(default_factory=dict)
    agent_command: tuple[str, ...] = ()

    def resolved_container_name(self) -> str:
        return self.container_name or container_name_for_run(self.run_id)


def json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def write_run_record(spec: AgentContainerSpec) -> Path:
    spec.run_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "run_id": spec.run_id,
        "created_at": utc_now(),
        "status": "created",
        "target": "exp-fdb-hub",
        "hub_url": spec.hub_url,
        "host_hub_url": spec.host_hub_url,
        "container_name": spec.resolved_container_name(),
        "image": spec.image,
        "worker_ring": spec.worker_ring,
        "pricing_scheme": spec.pricing_scheme,
        "max_total_credits": spec.max_total_credits,
        "credits_per_job": spec.credits_per_job,
        "repo_root": str(spec.repo_root),
        "run_dir": str(spec.run_dir),
        "agent_command": list(spec.agent_command),
    }
    path = spec.run_dir / "agent-run.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def build_docker_run_command(spec: AgentContainerSpec) -> list[str]:
    repo_root = spec.repo_root.resolve()
    run_dir = spec.run_dir.resolve()
    container_name = spec.resolved_container_name()
    command = _container_args(
        "run",
        "--name",
        container_name,
        "--label",
        "main-computer.role=exp-fdb-agent",
        "--label",
        "main-computer.exp-fdb-hub=true",
        "--label",
        f"main-computer.agent.run-id={spec.run_id}",
        "--label",
        f"main-computer.agent.worker-ring={int(spec.worker_ring)}",
    )
    if spec.detach:
        command.append("-d")
    if spec.add_host_gateway:
        command.extend(["--add-host", "host.docker.internal:host-gateway"])

    env = {
        "AGENT_RUN_ID": spec.run_id,
        "MAIN_COMPUTER_AGENT_RUN_ID": spec.run_id,
        "MAIN_COMPUTER_AGENT_RUN_DIR": "/agent-run",
        "MAIN_COMPUTER_AGENT_CONTAINER_NAME": container_name,
        "MAIN_COMPUTER_AGENT_IMAGE": spec.image,
        "MAIN_COMPUTER_EXP_FDB_HUB_URL": spec.hub_url.rstrip("/"),
        "HUB_BASE_URL": spec.hub_url.rstrip("/"),
        "MAIN_COMPUTER_AGENT_TARGET": "exp-fdb-hub",
        "MAIN_COMPUTER_AGENT_WORKER_RING": str(int(spec.worker_ring)),
        "MAIN_COMPUTER_AGENT_PRICING_SCHEME": spec.pricing_scheme,
        "MAIN_COMPUTER_AGENT_MAX_TOTAL_CREDITS": str(int(spec.max_total_credits)),
        "MAIN_COMPUTER_AGENT_CREDITS_PER_JOB": str(int(spec.credits_per_job)),
        "PYTHONPATH": "/workspace",
        "PYTHONUNBUFFERED": "1",
    }
    env.update(spec.extra_env)
    for key, value in sorted(env.items()):
        command.extend(["-e", f"{key}={value}"])

    command.extend(["-v", f"{repo_root}:/workspace:ro"])
    command.extend(["-v", f"{run_dir}:/agent-run"])
    command.extend(["-w", "/workspace"])
    command.append(spec.image)

    runtime_command = [
        "python",
        "-m",
        "main_computer.exp_fdb_agent_runtime",
        "run",
        "--run-id",
        spec.run_id,
        "--run-dir",
        "/agent-run",
        "--hub-url",
        spec.hub_url.rstrip("/"),
        "--heartbeat-seconds",
        str(float(spec.heartbeat_seconds)),
    ]
    if spec.agent_command:
        runtime_command.append("--")
        runtime_command.extend(spec.agent_command)
    command.extend(runtime_command)
    return command


def docker_inspect_status(container_name: str) -> dict[str, Any]:
    result = subprocess.run(
        _container_args("inspect", container_name),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return {"exists": False, "container_name": container_name, "error": result.stderr.strip()}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"exists": False, "container_name": container_name, "error": str(exc)}
    info = payload[0] if isinstance(payload, list) and payload else {}
    state = info.get("State", {}) if isinstance(info, dict) else {}
    config = info.get("Config", {}) if isinstance(info, dict) else {}
    return {
        "exists": True,
        "container_name": container_name,
        "id": str(info.get("Id", ""))[:12],
        "image": info.get("Image", ""),
        "status": state.get("Status", ""),
        "running": bool(state.get("Running", False)),
        "exit_code": state.get("ExitCode"),
        "labels": config.get("Labels", {}),
    }


def run_command(command: Sequence[str], *, dry_run: bool = False) -> int:
    print(" ".join(str(part) for part in command))
    if dry_run:
        return 0
    completed = subprocess.run(list(command), check=False)
    return int(completed.returncode)


def start_agent(args: argparse.Namespace) -> int:
    repo_root = repo_root_from_args(args.repo_root)
    run_id = clean_run_id(args.run_id or make_run_id())
    runtime_root = Path(args.runtime_root or DEFAULT_EXP_FDB_AGENT_RUNTIME_ROOT)
    if not runtime_root.is_absolute():
        runtime_root = repo_root / runtime_root
    run_dir = runtime_root / run_id
    agent_command = tuple(str(item) for item in (args.agent_command or []))
    if agent_command and agent_command[0] == "--":
        agent_command = agent_command[1:]
    extra_env = dict(item.split("=", 1) for item in args.env if "=" in item)
    spec = AgentContainerSpec(
        run_id=run_id,
        repo_root=repo_root,
        run_dir=run_dir,
        hub_url=args.hub_url,
        host_hub_url=args.host_hub_url,
        image=args.image,
        container_name=args.container_name or "",
        worker_ring=args.worker_ring,
        pricing_scheme=args.pricing_scheme,
        max_total_credits=args.max_total_credits,
        credits_per_job=args.credits_per_job,
        heartbeat_seconds=args.heartbeat_seconds,
        detach=not args.foreground,
        add_host_gateway=not args.no_host_gateway,
        extra_env=extra_env,
        agent_command=agent_command,
    )
    record_path = spec.run_dir / "agent-run.json"
    if not args.dry_run:
        record_path = write_run_record(spec)
    command = build_docker_run_command(spec)
    print(f"Agent run: {run_id}")
    print(f"Run record: {record_path}")
    if args.dry_run:
        print("Dry run: no run record was written and no container was started.")
    return run_command(command, dry_run=args.dry_run)


def shutdown_agent(args: argparse.Namespace) -> int:
    run_id = clean_run_id(args.run_id)
    container_name = args.container_name or container_name_for_run(run_id)
    stop_command = _container_args("stop", "--time", str(int(args.timeout_seconds)), container_name)
    code = run_command(stop_command, dry_run=args.dry_run)
    if code != 0:
        return code
    if args.remove:
        return run_command(_container_args("rm", container_name), dry_run=args.dry_run)
    return 0


def status_agent(args: argparse.Namespace) -> int:
    run_id = clean_run_id(args.run_id)
    container_name = args.container_name or container_name_for_run(run_id)
    status = docker_inspect_status(container_name)
    print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True, default=json_default))
    return 0 if status.get("exists") else 1


def logs_agent(args: argparse.Namespace) -> int:
    run_id = clean_run_id(args.run_id)
    container_name = args.container_name or container_name_for_run(run_id)
    command = _container_args("logs")
    if args.follow:
        command.append("--follow")
    command.extend(["--tail", str(int(args.tail)), container_name])
    return run_command(command, dry_run=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exp-fdb-agent.py",
        description=(
            "Start, stop, and inspect one disposable agent container targeted at the manual exp-FDB Hub setup. "
            "The durable run record lives under runtime/exp-fdb-agent-runs; the container is only the agent body."
        ),
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    start = subparsers.add_parser("start", help="Create a run record and start the matching agent container.")
    start.add_argument("--run-id", default="", help="AgentRun id. Defaults to an agent-YYYYMMDD-HHMMSS id.")
    start.add_argument("--repo-root", type=Path, default=None, help="Repository root mounted read-only at /workspace.")
    start.add_argument("--runtime-root", type=Path, default=DEFAULT_EXP_FDB_AGENT_RUNTIME_ROOT, help="Host runtime root for agent run records.")
    start.add_argument("--hub-url", default=DEFAULT_EXP_FDB_AGENT_HUB_URL, help="Hub URL visible inside the agent container.")
    start.add_argument("--host-hub-url", default=DEFAULT_EXP_FDB_AGENT_HOST_HUB_URL, help="Hub URL visible from the host/operator.")
    start.add_argument("--image", default=DEFAULT_EXP_FDB_AGENT_IMAGE, help="Container image used for the agent body.")
    start.add_argument("--container-name", default="", help="Override Docker container name.")
    start.add_argument("--worker-ring", type=int, default=2, help="Locked worker ring policy for jobs created by this run.")
    start.add_argument("--pricing-scheme", default="ring2-fixed", help="Locked pricing scheme label for this run.")
    start.add_argument("--max-total-credits", type=int, default=0, help="Maximum credits this run may spend; 0 means no spend is authorized by this launcher.")
    start.add_argument("--credits-per-job", type=int, default=1, help="Fixed credits offered per generated worker job.")
    start.add_argument("--heartbeat-seconds", type=float, default=DEFAULT_EXP_FDB_AGENT_HEARTBEAT_SECONDS)
    start.add_argument("--env", action="append", default=[], metavar="NAME=VALUE", help="Extra environment variable for the container.")
    start.add_argument("--foreground", action="store_true", help="Do not pass -d to docker run.")
    start.add_argument("--no-host-gateway", action="store_true", help="Do not add host.docker.internal:host-gateway.")
    start.add_argument("--dry-run", action="store_true", help="Print the docker command without running it.")
    start.add_argument("agent_command", nargs=argparse.REMAINDER, help="Optional command after -- for the runtime to supervise.")

    shutdown = subparsers.add_parser("shutdown", aliases=["stop"], help="Stop an agent container by run id.")
    shutdown.add_argument("run_id")
    shutdown.add_argument("--container-name", default="", help="Override Docker container name.")
    shutdown.add_argument("--timeout-seconds", type=int, default=20, help="Docker stop grace period.")
    shutdown.add_argument("--remove", action="store_true", help="Remove the stopped container after shutdown.")
    shutdown.add_argument("--dry-run", action="store_true", help="Print docker commands without running them.")

    status = subparsers.add_parser("status", help="Inspect an agent container by run id.")
    status.add_argument("run_id")
    status.add_argument("--container-name", default="", help="Override Docker container name.")

    logs = subparsers.add_parser("logs", help="Show agent container logs by run id.")
    logs.add_argument("run_id")
    logs.add_argument("--container-name", default="", help="Override Docker container name.")
    logs.add_argument("--follow", action="store_true")
    logs.add_argument("--tail", type=int, default=120)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "start":
        return start_agent(args)
    if args.action in {"shutdown", "stop"}:
        return shutdown_agent(args)
    if args.action == "status":
        return status_agent(args)
    if args.action == "logs":
        return logs_agent(args)
    raise SystemExit(f"unsupported action: {args.action}")


if __name__ == "__main__":
    raise SystemExit(main())
