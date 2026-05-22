from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen


Runner = Callable[..., subprocess.CompletedProcess[str]]
SleepFunc = Callable[[float], None]
TimeFunc = Callable[[], float]
OutputFunc = Callable[[str], None]
RpcProbeFunc = Callable[[str, int], dict[str, Any]]


DEFAULT_HEARTBEAT_INTERVAL_S = 30.0
DEFAULT_LIGHT_CHECK_INTERVAL_S = 60.0
DEFAULT_CHAIN_ID = 42424242
DEFAULT_RPC_URL = "http://127.0.0.1:8545"
SERVICE_NAME = "main-computer-blockchain-service"
DEV_COMPOSE_SERVICE = "ethereum-dev"
DEV_RUNTIME_SOURCE = "blockchain-service-dev-compose"
EXTERNAL_RUNTIME_SOURCE = "blockchain-service-env"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(value: Any, limit: int = 2000) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _process_stream_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _coerce_int(value: Any, default: int = DEFAULT_CHAIN_ID) -> int:
    if value is None:
        return default
    try:
        return int(str(value).strip(), 0)
    except (TypeError, ValueError):
        return default


def _default_blockchain_env_path() -> Path:
    return Path.home() / ".env.blockchain"


def _deployment_current_path(root: Path) -> Path:
    return root / "runtime" / "deployments" / "current.json"


def _probe_json_rpc_chain_id(rpc_url: str, expected_chain_id: int) -> dict[str, Any]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}).encode("utf-8")
    request = Request(
        rpc_url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=2.0) as response:
            payload = json.loads(response.read(64 * 1024).decode("utf-8", errors="replace"))
    except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "state": "down",
            "message": "blockchain JSON-RPC endpoint did not answer",
            "rpc_url": rpc_url,
            "expected_chain_id": expected_chain_id,
            "error": str(exc),
        }

    result = payload.get("result") if isinstance(payload, dict) else None
    try:
        actual_chain_id = int(str(result), 16)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "state": "invalid-response",
            "message": "blockchain JSON-RPC endpoint returned an invalid eth_chainId result",
            "rpc_url": rpc_url,
            "expected_chain_id": expected_chain_id,
            "payload": payload,
        }

    return {
        "ok": actual_chain_id == expected_chain_id,
        "state": "ready" if actual_chain_id == expected_chain_id else "wrong-chain",
        "message": (
            "blockchain JSON-RPC endpoint is ready"
            if actual_chain_id == expected_chain_id
            else "blockchain JSON-RPC endpoint answered with the wrong chain id"
        ),
        "rpc_url": rpc_url,
        "expected_chain_id": expected_chain_id,
        "chain_id": actual_chain_id,
    }


def load_blockchain_service_state(root: Path | str) -> dict[str, Any]:
    state_path = Path(root).resolve() / "runtime" / "blockchain_service" / "state.json"
    if not state_path.exists():
        return {
            "ok": False,
            "state": "missing",
            "message": "blockchain service state file has not been written",
            "state_path": str(state_path),
        }
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "state": "invalid",
            "message": "blockchain service state file could not be read",
            "state_path": str(state_path),
            "error": str(exc),
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "state": "invalid",
            "message": "blockchain service state file did not contain an object",
            "state_path": str(state_path),
        }
    payload.setdefault("state_path", str(state_path))
    return payload


class BlockchainService:
    """Boot and keep alive the blockchain substrate.

    Production should provide ``~/.env.blockchain``. Until that exists, this
    service defaults to the checked-in dev Anvil chain:

        docker compose -f docker-compose.dev.yml up -d ethereum-dev

    The service also publishes runtime/deployments/current.json so the viewport
    reads the chain that this service is responsible for, instead of a stale
    dev-chain run.
    """

    def __init__(
        self,
        *,
        root: Path | str,
        docker_command: str = "docker",
        compose_file: Path | str | None = None,
        blockchain_env_path: Path | str | None = None,
        runner: Runner | None = None,
        sleep_func: SleepFunc | None = None,
        time_func: TimeFunc | None = None,
        output_func: OutputFunc | None = print,
        rpc_probe_func: RpcProbeFunc | None = None,
        heartbeat_interval_s: float = DEFAULT_HEARTBEAT_INTERVAL_S,
        light_check_interval_s: float = DEFAULT_LIGHT_CHECK_INTERVAL_S,
    ) -> None:
        self.root = Path(root).resolve()
        self.runtime_dir = self.root / "runtime" / "blockchain_service"
        self.state_path = self.runtime_dir / "state.json"
        self.docker_command = (docker_command or "docker").strip() or "docker"
        self.compose_file = Path(compose_file) if compose_file else self.root / "docker-compose.dev.yml"
        if not self.compose_file.is_absolute():
            self.compose_file = (self.root / self.compose_file).resolve()
        self.compose_project = (
            os.environ.get("MAIN_COMPUTER_BLOCKCHAIN_COMPOSE_PROJECT")
            or os.environ.get("MAIN_COMPUTER_DEV_COMPOSE_PROJECT")
            or ""
        ).strip()
        env_override = blockchain_env_path or os.environ.get("MAIN_COMPUTER_BLOCKCHAIN_ENV")
        self.blockchain_env_path = Path(env_override).expanduser() if env_override else _default_blockchain_env_path()
        self.runner = runner or subprocess.run
        self.sleep = sleep_func or time.sleep
        self.time = time_func or time.monotonic
        self.output = output_func
        self.rpc_probe = rpc_probe_func or _probe_json_rpc_chain_id
        self.heartbeat_interval_s = max(1.0, float(heartbeat_interval_s))
        self.light_check_interval_s = max(self.heartbeat_interval_s, float(light_check_interval_s))
        self._last_state: dict[str, Any] = {}
        self._next_light_check = 0.0

    def boot(self, *, watch: bool = False, max_watch_loops: int | None = None) -> dict[str, Any]:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._emit("blockchain service boot starting", root=str(self.root), env=str(self.blockchain_env_path))
        state = self._full_boot_reconcile()
        state["service"]["watching"] = bool(watch)
        self._write_state(state)
        self._emit_boot_result(state, prefix="initial boot")
        if watch:
            return self.watch(max_watch_loops=max_watch_loops)
        return state

    def watch(self, *, max_watch_loops: int | None = None) -> dict[str, Any]:
        loops = 0
        state = self._last_state or self._base_state("watching")
        self._next_light_check = self.time() + self.light_check_interval_s

        while max_watch_loops is None or loops < max_watch_loops:
            loops += 1
            state = dict(self._last_state or state)
            boot_proven = bool(state.get("boot_proven") and state.get("ok"))
            state["updated_at"] = _now_iso()
            state.setdefault("service", {})["watching"] = True
            state["service"]["state"] = "watching" if boot_proven else "booting"
            self._write_state(state)

            if not boot_proven:
                self._emit("blockchain boot incomplete; retrying", attempt=loops, retry_interval_s=self.heartbeat_interval_s)
                state = self._full_boot_reconcile()
                state["service"]["watching"] = True
                state["service"]["state"] = "watching" if state.get("ok") else "booting"
                self._write_state(state)
                self._emit_boot_result(state, prefix="boot retry")
                if state.get("ok"):
                    self._next_light_check = self.time() + self.light_check_interval_s
            elif self.time() >= self._next_light_check:
                state = self._light_keepalive(dict(self._last_state or state))
                state["service"]["watching"] = True
                self._write_state(state)
                self._emit_boot_result(state, prefix="light keepalive")
                if state.get("ok"):
                    self._next_light_check = self.time() + self.light_check_interval_s

            if max_watch_loops is not None and loops >= max_watch_loops:
                break
            self.sleep(self.heartbeat_interval_s)
        return self._last_state or state

    def _base_state(self, state: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "ok": False,
            "state": state,
            "boot_proven": False,
            "updated_at": _now_iso(),
            "state_path": str(self.state_path),
            "service": {
                "name": SERVICE_NAME,
                "pid": os.getpid(),
                "state": state,
                "watching": False,
            },
        }

    def _chain_config(self) -> dict[str, Any]:
        if self.blockchain_env_path.exists():
            try:
                values = _parse_env_text(self.blockchain_env_path.read_text(encoding="utf-8"))
            except OSError as exc:
                return {
                    "ok": False,
                    "mode": "external",
                    "state": "env-read-failed",
                    "message": "could not read .env.blockchain",
                    "env_path": str(self.blockchain_env_path),
                    "error": str(exc),
                }
            rpc_url = (
                values.get("MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL")
                or values.get("ENERGY_CHAIN_RPC_URL")
                or values.get("BLOCKCHAIN_RPC_URL")
                or values.get("RPC_URL")
                or DEFAULT_RPC_URL
            )
            chain_id = _coerce_int(
                values.get("MAIN_COMPUTER_ENERGY_CHAIN_ID") or values.get("ENERGY_CHAIN_ID") or values.get("CHAIN_ID"),
                default=DEFAULT_CHAIN_ID,
            )
            return {
                "ok": True,
                "mode": "external",
                "state": "configured",
                "message": ".env.blockchain is present; using configured blockchain RPC",
                "env_path": str(self.blockchain_env_path),
                "rpc_url": rpc_url,
                "chain_id": chain_id,
                "source": EXTERNAL_RUNTIME_SOURCE,
            }

        rpc_url = os.environ.get("MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL") or DEFAULT_RPC_URL
        chain_id = _coerce_int(os.environ.get("MAIN_COMPUTER_ENERGY_CHAIN_ID"), default=DEFAULT_CHAIN_ID)
        return {
            "ok": True,
            "mode": "dev-compose",
            "state": "configured",
            "message": ".env.blockchain is absent; using checked-in dev ethereum-dev chain",
            "env_path": str(self.blockchain_env_path),
            "rpc_url": rpc_url,
            "chain_id": chain_id,
            "source": DEV_RUNTIME_SOURCE,
            "compose_file": str(self.compose_file),
            "compose_project": self.compose_project or None,
            "compose_service": DEV_COMPOSE_SERVICE,
            "rpc_source": "environment" if os.environ.get("MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL") else "default",
        }

    def _publish_runtime_config(self, config: dict[str, Any]) -> dict[str, Any]:
        if not config.get("ok"):
            return self._component(ok=False, state="blocked", message="blockchain config is not ready")

        path = _deployment_current_path(self.root)
        payload = {
            "schema_version": 1,
            "run_id": str(config.get("mode") or "blockchain"),
            "source": config.get("source"),
            "published_at": _now_iso(),
            "chain": {
                "rpc_url": config.get("rpc_url"),
                "host_rpc_url": config.get("rpc_url"),
                "chain_id": config.get("chain_id"),
                "mode": config.get("mode"),
            },
            "deployments": {},
            "contracts": {},
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except OSError as exc:
            return self._component(
                ok=False,
                state="write-failed",
                message="could not publish blockchain runtime config",
                path=str(path),
                error=str(exc),
            )

        return self._component(
            ok=True,
            state="ready",
            message="blockchain runtime config was published",
            path=str(path),
            source=config.get("source"),
            rpc_url=config.get("rpc_url"),
            chain_id=config.get("chain_id"),
        )

    def _reconcile_dev_chain_without_resetting_running_default(self, config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Use an already-running default dev chain before touching compose.

        The development blockchain is stateful enough that a normal application
        refresh should not reset it. If the configured/default RPC endpoint is
        already answering with the expected chain id, this method reports the
        chain ready and deliberately skips docker compose. Compose is only used
        when the default RPC endpoint is not already up.
        """

        rpc_url = str(config.get("rpc_url") or DEFAULT_RPC_URL)
        chain_id = int(config.get("chain_id") or DEFAULT_CHAIN_ID)
        rpc = self.rpc_probe(rpc_url, chain_id)
        if rpc.get("ok"):
            docker = self._component(
                ok=True,
                state="not-touched",
                message="default dev blockchain RPC is already up; docker was not touched",
                compose_action="skipped",
                reused_existing_rpc=True,
                rpc_url=rpc_url,
            )
            compose = self._component(
                ok=True,
                state="already-running",
                message="default dev blockchain RPC is already up; ethereum-dev compose start was skipped",
                compose_file=str(self.compose_file),
                compose_service=DEV_COMPOSE_SERVICE,
                compose_action="skipped",
                started=False,
                reused_existing_rpc=True,
                rpc_url=rpc_url,
            )
            return docker, compose, rpc

        docker = self._reconcile_docker_engine()
        if docker.get("ok"):
            compose = self._reconcile_dev_compose_stack()
        else:
            compose = self._component(
                ok=False,
                state="blocked",
                message="docker is not ready; ethereum-dev was not started",
                compose_file=str(self.compose_file),
                compose_service=DEV_COMPOSE_SERVICE,
                compose_action="blocked",
            )
        rpc = self.rpc_probe(rpc_url, chain_id)
        return docker, compose, rpc

    def _full_boot_reconcile(self) -> dict[str, Any]:
        state = self._base_state("booting")
        config = self._chain_config()
        runtime = self._publish_runtime_config(config)

        if not config.get("ok"):
            docker = self._component(ok=False, state="blocked", message="blockchain config is not ready")
            compose = self._component(ok=False, state="blocked", message="blockchain config is not ready")
            rpc = self._component(ok=False, state="blocked", message="blockchain config is not ready")
        elif config.get("mode") == "dev-compose":
            docker, compose, rpc = self._reconcile_dev_chain_without_resetting_running_default(config)
        else:
            docker = self._component(ok=True, state="not-required", message="external blockchain mode does not require local docker")
            compose = self._component(ok=True, state="not-required", message="external blockchain mode does not start ethereum-dev")
            rpc = self.rpc_probe(str(config.get("rpc_url") or DEFAULT_RPC_URL), int(config.get("chain_id") or DEFAULT_CHAIN_ID))
        ok = bool(config.get("ok") and runtime.get("ok") and docker.get("ok") and compose.get("ok") and rpc.get("ok"))
        state.update(
            {
                "ok": ok,
                "state": "ready" if ok else "down",
                "boot_proven": ok,
                "mode": config.get("mode"),
                "message": "blockchain service is ready" if ok else "blockchain service needs attention",
                "config": config,
                "runtime": runtime,
                "docker": docker,
                "compose": compose,
                "rpc": rpc,
                "components": {
                    "config": config,
                    "runtime": runtime,
                    "docker": docker,
                    "compose": compose,
                    "rpc": rpc,
                },
            }
        )
        state["service"]["state"] = state["state"]
        return state

    def _light_keepalive(self, state: dict[str, Any]) -> dict[str, Any]:
        config = self._chain_config()
        runtime = self._publish_runtime_config(config)
        if not config.get("ok"):
            docker = self._component(ok=False, state="blocked", message="blockchain config is not ready")
            compose = self._component(ok=False, state="blocked", message="blockchain config is not ready")
            rpc = self._component(ok=False, state="blocked", message="blockchain config is not ready")
        elif config.get("mode") == "dev-compose":
            docker, compose, rpc = self._reconcile_dev_chain_without_resetting_running_default(config)
        else:
            docker = self._component(ok=True, state="not-required", message="external blockchain mode does not require local docker")
            compose = self._component(ok=True, state="not-required", message="external blockchain mode does not start ethereum-dev")
            rpc = self.rpc_probe(str(config.get("rpc_url") or DEFAULT_RPC_URL), int(config.get("chain_id") or DEFAULT_CHAIN_ID))
        ok = bool(config.get("ok") and runtime.get("ok") and compose.get("ok") and rpc.get("ok"))
        state.update(
            {
                "ok": ok,
                "state": "ready" if ok else "down",
                "boot_proven": ok,
                "updated_at": _now_iso(),
                "mode": config.get("mode"),
                "message": "blockchain service is ready" if ok else "blockchain service needs attention",
                "config": config,
                "runtime": runtime,
                "docker": docker,
                "compose": compose,
                "rpc": rpc,
                "components": {
                    "config": config,
                    "runtime": runtime,
                    "docker": docker,
                    "compose": compose,
                    "rpc": rpc,
                },
            }
        )
        return state

    def _reconcile_docker_engine(self) -> dict[str, Any]:
        version = self._run([self.docker_command, "version"], timeout=12)
        if version.returncode != 0:
            return self._component(
                ok=False,
                state="down",
                message="docker engine is not responding",
                returncode=version.returncode,
                stdout=_truncate(version.stdout),
                error=_truncate(version.stderr) or "docker version failed",
            )
        compose = self._run([self.docker_command, "compose", "version"], timeout=12)
        if compose.returncode != 0:
            return self._component(
                ok=False,
                state="compose-missing",
                message="docker compose is not available",
                returncode=compose.returncode,
                stdout=_truncate(compose.stdout),
                error=_truncate(compose.stderr) or "docker compose version failed",
            )
        return self._component(ok=True, state="ready", message="docker engine and compose are available")

    def _compose_command(self, *args: str) -> list[str]:
        command = [self.docker_command, "compose"]
        if self.compose_project:
            command.extend(["--project-name", self.compose_project])
        command.extend(["-f", str(self.compose_file), *args])
        return command

    def _reconcile_dev_compose_stack(self) -> dict[str, Any]:
        if not self.compose_file.exists():
            return self._component(
                ok=False,
                state="missing-compose-file",
                message="docker-compose.dev.yml is missing",
                compose_file=str(self.compose_file),
                compose_project=self.compose_project or None,
            )
        up = self._run(self._compose_command("up", "-d", DEV_COMPOSE_SERVICE), timeout=120)
        if up.returncode != 0:
            return self._component(
                ok=False,
                state="start-failed",
                message="docker compose failed to start ethereum-dev",
                command=up.args if isinstance(up.args, list) else self._compose_command("up", "-d", DEV_COMPOSE_SERVICE),
                returncode=up.returncode,
                stdout=_truncate(up.stdout),
                error=_truncate(up.stderr) or "docker compose up failed",
                compose_file=str(self.compose_file),
                compose_project=self.compose_project or None,
            )
        return self._check_dev_compose_stack(started=True)

    def _check_dev_compose_stack(self, *, started: bool = False) -> dict[str, Any]:
        ps = self._run(self._compose_command("ps", "--services", "--status", "running"), timeout=30)
        services = sorted({line.strip() for line in str(ps.stdout or "").splitlines() if line.strip()})
        ok = ps.returncode == 0 and DEV_COMPOSE_SERVICE in services
        return self._component(
            ok=ok,
            state="ready" if ok else "starting",
            message="ethereum-dev compose service is running" if ok else "ethereum-dev compose service is not running yet",
            compose_file=str(self.compose_file),
            compose_project=self.compose_project or None,
            compose_service=DEV_COMPOSE_SERVICE,
            started=started,
            running_services=services,
            returncode=ps.returncode,
            stdout=_truncate(ps.stdout),
            error=_truncate(ps.stderr),
        )

    def _run(self, command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner(
                command,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _process_stream_text(getattr(exc, "stdout", None) or getattr(exc, "output", None))
            stderr = _process_stream_text(getattr(exc, "stderr", None))
            if stderr:
                stderr = f"{stderr.rstrip()}\ncommand timed out after {timeout:g} seconds"
            else:
                stderr = f"command timed out after {timeout:g} seconds"
            return subprocess.CompletedProcess(command, 124, stdout=stdout, stderr=stderr)
        except (OSError, subprocess.SubprocessError) as exc:
            return subprocess.CompletedProcess(command, 127, stdout="", stderr=str(exc))

    def _component(self, *, ok: bool, state: str, message: str, **extra: Any) -> dict[str, Any]:
        return {"ok": bool(ok), "state": state, "message": message, "checked_at": _now_iso(), **extra}

    def _write_state(self, state: dict[str, Any]) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        state["state_path"] = str(self.state_path)
        state["updated_at"] = _now_iso()
        self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._last_state = state

    def _emit(self, message: str, **fields: Any) -> None:
        if self.output is None:
            return
        suffix = ""
        if fields:
            suffix = " " + " ".join(f"{key}={value}" for key, value in fields.items() if value not in (None, ""))
        try:
            self.output(f"[{_now_iso()}] {SERVICE_NAME}: {message}{suffix}", flush=True)  # type: ignore[misc]
        except TypeError:
            self.output(f"[{_now_iso()}] {SERVICE_NAME}: {message}{suffix}")

    def _emit_boot_result(self, state: dict[str, Any], *, prefix: str) -> None:
        if state.get("ok"):
            self._emit(f"{prefix} complete; blockchain is ready", mode=state.get("mode"))
        else:
            self._emit(f"{prefix} incomplete; blockchain still needs attention", mode=state.get("mode"), state=state.get("state"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Boot and keep alive the Main Computer blockchain service.")
    parser.add_argument("--root", default=".", help="Repository/build root. Defaults to the current directory.")
    parser.add_argument("--docker-command", default=os.environ.get("MAIN_COMPUTER_DOCKER_COMMAND", "docker"))
    parser.add_argument("--compose-file", default=os.environ.get("MAIN_COMPUTER_BLOCKCHAIN_COMPOSE_FILE"))
    parser.add_argument("--blockchain-env", default=os.environ.get("MAIN_COMPUTER_BLOCKCHAIN_ENV"))
    parser.add_argument(
        "--heartbeat-interval-s",
        type=float,
        default=float(os.environ.get("MAIN_COMPUTER_BLOCKCHAIN_SERVICE_HEARTBEAT_S", DEFAULT_HEARTBEAT_INTERVAL_S)),
    )
    parser.add_argument(
        "--light-check-interval-s",
        type=float,
        default=float(os.environ.get("MAIN_COMPUTER_BLOCKCHAIN_SERVICE_LIGHT_CHECK_S", DEFAULT_LIGHT_CHECK_INTERVAL_S)),
    )

    subparsers = parser.add_subparsers(dest="command")
    boot = subparsers.add_parser("boot", help="Reconcile blockchain once, optionally remaining resident.")
    boot.add_argument("--watch", action="store_true", help="Remain alive, retrying boot on the heartbeat cadence until ready.")
    boot.add_argument("--max-watch-loops", type=int, default=None, help=argparse.SUPPRESS)

    status = subparsers.add_parser("status", help="Print the last blockchain-service state JSON.")
    status.add_argument("--json", action="store_true", help="Kept for compatibility; status always prints JSON.")
    return parser


def _service_from_args(args: argparse.Namespace) -> BlockchainService:
    return BlockchainService(
        root=args.root,
        docker_command=args.docker_command,
        compose_file=args.compose_file,
        blockchain_env_path=args.blockchain_env,
        heartbeat_interval_s=args.heartbeat_interval_s,
        light_check_interval_s=args.light_check_interval_s,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command or "boot"

    if command == "status":
        print(json.dumps(load_blockchain_service_state(args.root), indent=2, sort_keys=True))
        return 0

    service = _service_from_args(args)
    state = service.boot(watch=bool(getattr(args, "watch", False)), max_watch_loops=getattr(args, "max_watch_loops", None))
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0 if state.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
