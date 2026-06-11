from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable
from main_computer.main_log_hooks import install_main_log_hooks_from_env
from main_computer.main_log_client import emit_main_log_text
from urllib.error import URLError
from urllib.request import Request, urlopen


Runner = Callable[..., subprocess.CompletedProcess[str]]
SleepFunc = Callable[[float], None]
TimeFunc = Callable[[], float]
OutputFunc = Callable[[str], None]
RpcProbeFunc = Callable[[str, int], dict[str, Any]]
ContractCodeProbeFunc = Callable[[str, str], dict[str, Any]]


DEFAULT_HEARTBEAT_INTERVAL_S = 15.0
DEFAULT_LIGHT_CHECK_INTERVAL_S = 60.0
DEFAULT_CHAIN_ID = 42424242
DEFAULT_RPC_URL = "http://127.0.0.1:18545"
DEFAULT_DEV_CHAIN_RUN_ID = "test-machine-dev"
DEFAULT_DEV_CHAIN_ENVIRONMENT = "dev"
DEFAULT_DEV_CHAIN_PORT_STRATEGY = "replace-project"
DEFAULT_DEV_CHAIN_WAIT_TIMEOUT_S = "30"
DEFAULT_DEV_CHAIN_RESET_PROCESS_TIMEOUT_S = 900.0
DEPLOYMENT_CURRENT_RELATIVE_PATH = Path("runtime") / "deployments" / "current.json"
SERVICE_NAME = "main-computer-blockchain-service"
DEPLOYMENT_RUNTIME_SOURCE = "runtime-deployments-current"
EXTERNAL_RUNTIME_SOURCE = "blockchain-service-env"
REQUIRED_CONTRACT_KEYS = ("hub_credit_bridge_escrow",)
DEV_CHAIN_RESET_COMMAND = (
    "python .\\tools\\dev-chain-reset.py --yes --run-id test-machine-dev "
    "--environment dev --port-strategy replace-project"
)
DEV_CHAIN_DIAGNOSIS_COMMAND = "python .\\tools\\dev-chain-diagnosis.py --state .\\runtime\\deployments\\current.json"
DEFAULT_DEV_OFFICES = (
    {
        "office": "O0",
        "title": "Captain",
        "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
    },
    {
        "office": "O1",
        "title": "First Officer",
        "address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
    },
    {
        "office": "O2",
        "title": "Second Officer",
        "address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
    },
    {
        "office": "O3",
        "title": "Third Officer",
        "address": "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
    },
)


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


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return float(default)


def _disabled_flag(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"0", "false", "no", "off", "disabled"}


def _compact_command(command: list[str]) -> str:
    return " ".join(str(part) for part in command)


def _valid_address(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) == 42 and text.startswith("0x") and all(ch in "0123456789abcdefABCDEF" for ch in text[2:]):
        return text
    return None


def _office_records_from_env_values(values: dict[str, str]) -> list[dict[str, str]]:
    offices: list[dict[str, str]] = []
    for index in range(4):
        address = _valid_address(values.get(f"MAIN_COMPUTER_DEV_OFFICE_{index}_ADDRESS"))
        if not address:
            continue
        offices.append(
            {
                "office": f"O{index}",
                "title": values.get(f"MAIN_COMPUTER_DEV_OFFICE_{index}_TITLE") or f"Office {index}",
                "address": address,
            }
        )
    return offices


def _default_dev_office_records() -> list[dict[str, str]]:
    return [dict(office) for office in DEFAULT_DEV_OFFICES]


def _default_blockchain_env_path() -> Path:
    return Path.home() / ".env.blockchain"


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



def _post_json_rpc(rpc_url: str, method: str, params: list[Any]) -> tuple[dict[str, Any] | None, str | None]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
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
        return None, str(exc)
    if not isinstance(payload, dict):
        return None, "JSON-RPC endpoint returned a non-object response"
    return payload, None


def _probe_json_rpc_contract_code(rpc_url: str, address: str) -> dict[str, Any]:
    payload, error = _post_json_rpc(rpc_url, "eth_getCode", [address, "latest"])
    if error:
        return {
            "ok": False,
            "state": "down",
            "message": "blockchain JSON-RPC endpoint did not answer eth_getCode",
            "rpc_url": rpc_url,
            "address": address,
            "error": error,
        }
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, str):
        return {
            "ok": False,
            "state": "invalid-response",
            "message": "blockchain JSON-RPC endpoint returned an invalid eth_getCode result",
            "rpc_url": rpc_url,
            "address": address,
            "payload": payload,
        }
    has_code = result not in ("", "0x", "0X")
    return {
        "ok": has_code,
        "state": "code-present" if has_code else "missing-code",
        "message": (
            "contract code is present at the configured address"
            if has_code
            else "configured contract address has no deployed code on the connected chain"
        ),
        "rpc_url": rpc_url,
        "address": address,
        "code_size_hex_chars": max(0, len(result) - 2) if result.startswith(("0x", "0X")) else len(result),
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
    """Boot and keep alive the blockchain/dev-chain substrate.

    Production may provide ``~/.env.blockchain``. Local development without an
    external env uses the deployment-owned golden path:

        python tools/dev-chain-reset.py --yes --run-id test-machine-dev --environment dev --port-strategy replace-project

    The resident service owns local dev-chain repair. It does not talk to Docker
    directly for readiness; it waits for the executor service to publish Docker
    readiness, then runs the reset tool and keeps retrying on the heartbeat
    cadence until RPC and required contract code are healthy.
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
        contract_code_probe_func: ContractCodeProbeFunc | None = None,
        heartbeat_interval_s: float = DEFAULT_HEARTBEAT_INTERVAL_S,
        light_check_interval_s: float = DEFAULT_LIGHT_CHECK_INTERVAL_S,
    ) -> None:
        self.root = Path(root).resolve()
        self.runtime_dir = self.root / "runtime" / "blockchain_service"
        self.state_path = self.runtime_dir / "state.json"
        self.docker_command = (docker_command or "docker").strip() or "docker"
        self.python_command = (
            os.environ.get("MAIN_COMPUTER_PYTHON_COMMAND")
            or os.environ.get("PYTHON")
            or sys.executable
            or "python"
        )
        # Kept as an accepted constructor/CLI option for compatibility. The
        # blockchain service no longer starts a Compose-managed dev chain.
        self.compose_file = Path(compose_file) if compose_file else self.root / "docker-compose.dev.yml"
        if not self.compose_file.is_absolute():
            self.compose_file = (self.root / self.compose_file).resolve()
        env_override = blockchain_env_path or os.environ.get("MAIN_COMPUTER_BLOCKCHAIN_ENV")
        self.blockchain_env_path = Path(env_override).expanduser() if env_override else _default_blockchain_env_path()
        self.runner = runner or subprocess.run
        self.sleep = sleep_func or time.sleep
        self.time = time_func or time.monotonic
        self.output = output_func
        self.rpc_probe = rpc_probe_func or _probe_json_rpc_chain_id
        self.contract_code_probe = contract_code_probe_func or _probe_json_rpc_contract_code
        self.heartbeat_interval_s = max(1.0, float(heartbeat_interval_s))
        self.light_check_interval_s = max(self.heartbeat_interval_s, float(light_check_interval_s))
        self._last_state: dict[str, Any] = {}
        self._next_light_check = 0.0
        self._boot_proven_once = False

    def boot(self, *, watch: bool = False, max_watch_loops: int | None = None) -> dict[str, Any]:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._emit("blockchain service boot starting", root=str(self.root), env=str(self.blockchain_env_path))
        state = self._full_boot_reconcile()
        if state.get("ok"):
            self._boot_proven_once = True
            state["boot_proven"] = True
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
            boot_proven = bool(self._boot_proven_once or (state.get("boot_proven") and state.get("ok")))
            state["boot_proven"] = boot_proven
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
                    self._boot_proven_once = True
                    state["boot_proven"] = True
                    self._next_light_check = self.time() + self.light_check_interval_s
            elif self.time() >= self._next_light_check:
                state = self._light_keepalive(dict(self._last_state or state))
                state["service"]["watching"] = True
                self._write_state(state)
                self._emit_boot_result(state, prefix="light keepalive")
                if state.get("ok"):
                    self._boot_proven_once = True
                    state["boot_proven"] = True
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
            offices = _office_records_from_env_values(values)
            environment = (
                values.get("MAIN_COMPUTER_DEPLOYMENT_ENVIRONMENT")
                or values.get("MAIN_COMPUTER_BLOCKCHAIN_ENVIRONMENT")
                or ("dev" if offices else "external")
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
                "environment": environment,
                "offices": offices,
                "contracts": {},
            }

        return self._deployment_current_config()

    def _local_dev_chain_auto_start_enabled(self) -> bool:
        return not _disabled_flag(os.environ.get("MAIN_COMPUTER_DEV_CHAIN_AUTO_START", "1"))

    def _dev_chain_reset_command(self, config: dict[str, Any]) -> list[str]:
        tool = self.root / "tools" / "dev-chain-reset.py"
        rpc_url = (
            os.environ.get("MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL")
            or str(config.get("rpc_url") or DEFAULT_RPC_URL)
        )
        chain_id = _coerce_int(os.environ.get("MAIN_COMPUTER_ENERGY_CHAIN_ID") or config.get("chain_id"), DEFAULT_CHAIN_ID)
        run_id = os.environ.get("MAIN_COMPUTER_DEV_CHAIN_RUN_ID") or DEFAULT_DEV_CHAIN_RUN_ID
        environment = os.environ.get("MAIN_COMPUTER_DEV_CHAIN_ENVIRONMENT") or DEFAULT_DEV_CHAIN_ENVIRONMENT
        port_strategy = os.environ.get("MAIN_COMPUTER_DEV_CHAIN_PORT_STRATEGY") or DEFAULT_DEV_CHAIN_PORT_STRATEGY
        wait_timeout = os.environ.get("MAIN_COMPUTER_DEV_CHAIN_WAIT_TIMEOUT_S") or DEFAULT_DEV_CHAIN_WAIT_TIMEOUT_S

        return [
            self.python_command,
            str(tool),
            "--yes",
            "--run-id",
            str(run_id),
            "--environment",
            str(environment),
            "--port-strategy",
            str(port_strategy),
            "--host-rpc-url",
            str(rpc_url),
            "--chain-id",
            str(chain_id),
            "--wait-timeout-s",
            str(wait_timeout),
        ]

    def _reset_process_timeout_s(self) -> float:
        return max(
            1.0,
            _coerce_float(
                os.environ.get("MAIN_COMPUTER_DEV_CHAIN_RESET_PROCESS_TIMEOUT_S"),
                DEFAULT_DEV_CHAIN_RESET_PROCESS_TIMEOUT_S,
            ),
        )

    def _run_dev_chain_reset(self, config: dict[str, Any], *, trigger: str) -> dict[str, Any]:
        if not self._local_dev_chain_auto_start_enabled():
            return self._component(
                ok=False,
                state="disabled",
                message="local dev-chain auto-start is disabled; blockchain service will only observe RPC",
                setting="MAIN_COMPUTER_DEV_CHAIN_AUTO_START",
                trigger=trigger,
            )

        tool = self.root / "tools" / "dev-chain-reset.py"
        if not tool.exists():
            return self._component(
                ok=False,
                state="missing-reset-tool",
                message="tools/dev-chain-reset.py is missing; local dev-chain cannot be repaired",
                tool=str(tool),
                trigger=trigger,
            )

        command = self._dev_chain_reset_command(config)
        timeout_s = self._reset_process_timeout_s()
        result = self._run(command, timeout=timeout_s)
        output = (result.stderr or "") + ("\n" if result.stderr and result.stdout else "") + (result.stdout or "")
        if result.returncode != 0:
            return self._component(
                ok=False,
                state="reset-retry-pending",
                message="dev-chain reset did not complete yet; blockchain service will retry on the next boot heartbeat",
                trigger=trigger,
                command=command,
                command_display=_compact_command(command),
                returncode=result.returncode,
                error=_truncate(output),
            )

        return self._component(
            ok=True,
            state="reset-succeeded",
            message="dev-chain reset completed; blockchain service will re-check runtime, RPC, and contract code",
            trigger=trigger,
            command=command,
            command_display=_compact_command(command),
            returncode=result.returncode,
            stdout=_truncate(result.stdout or "", 2000),
            stderr=_truncate(result.stderr or "", 2000),
        )

    def _trigger_for_pre_config(self, config: dict[str, Any]) -> str | None:
        if config.get("mode") != "deployment-current":
            return None
        if config.get("ok"):
            return None
        config_state = str(config.get("state") or "")
        if config_state in {
            "missing-deployment-current",
            "invalid-deployment-current",
            "incomplete-deployment-current",
        }:
            return config_state
        return None

    def _trigger_for_runtime_status(
        self,
        *,
        config: dict[str, Any],
        rpc: dict[str, Any],
        contracts: dict[str, Any],
    ) -> str | None:
        if config.get("mode") != "deployment-current" or not config.get("ok"):
            return None
        if not rpc.get("ok"):
            return "rpc-" + str(rpc.get("state") or "down")
        if not contracts.get("ok"):
            contracts_state = str(contracts.get("state") or "not-ready")
            if contracts_state in {"missing-contract-code", "blocked"}:
                return "contracts-" + contracts_state
        return None

    def _docker_delegation_status(self, config: dict[str, Any]) -> dict[str, Any]:
        if config.get("mode") != "deployment-current":
            return self._component(
                ok=True,
                state="not-required",
                message="external blockchain mode does not require local Docker lifecycle",
            )
        return self._component(
            ok=True,
            state="self-managed-retry",
            message="local dev-chain boot is owned by blockchain_service; reset attempts retry until Docker and RPC are ready",
        )

    def _deployment_current_path(self) -> Path:
        return self.root / DEPLOYMENT_CURRENT_RELATIVE_PATH

    def _deployment_current_config(self) -> dict[str, Any]:
        path = self._deployment_current_path()
        if not path.exists():
            return {
                "ok": False,
                "mode": "deployment-current",
                "state": "missing-deployment-current",
                "message": (
                    "runtime/deployments/current.json is missing; the blockchain golden path has not been "
                    f"published. Run: {DEV_CHAIN_RESET_COMMAND}"
                ),
                "deployment_path": str(path),
                "reset_command": DEV_CHAIN_RESET_COMMAND,
                "diagnosis_command": DEV_CHAIN_DIAGNOSIS_COMMAND,
            }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "ok": False,
                "mode": "deployment-current",
                "state": "invalid-deployment-current",
                "message": "runtime/deployments/current.json could not be read as valid JSON",
                "deployment_path": str(path),
                "error": str(exc),
                "reset_command": DEV_CHAIN_RESET_COMMAND,
                "diagnosis_command": DEV_CHAIN_DIAGNOSIS_COMMAND,
            }
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "mode": "deployment-current",
                "state": "invalid-deployment-current",
                "message": "runtime/deployments/current.json must contain a JSON object",
                "deployment_path": str(path),
                "reset_command": DEV_CHAIN_RESET_COMMAND,
                "diagnosis_command": DEV_CHAIN_DIAGNOSIS_COMMAND,
            }

        chain = payload.get("chain") if isinstance(payload.get("chain"), dict) else {}
        deployment_rpc_url = str(chain.get("rpc_url") or chain.get("host_rpc_url") or DEFAULT_RPC_URL)
        env_rpc_url = os.environ.get("MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL")
        rpc_url = env_rpc_url or deployment_rpc_url
        chain_id = _coerce_int(os.environ.get("MAIN_COMPUTER_ENERGY_CHAIN_ID") or chain.get("chain_id"), default=DEFAULT_CHAIN_ID)
        rpc_source = "environment" if env_rpc_url else "deployment-current"
        contracts = payload.get("contracts") or payload.get("deployments") or {}
        if not isinstance(contracts, dict):
            contracts = {}

        missing_required: list[str] = []
        invalid_required: list[str] = []
        for key in REQUIRED_CONTRACT_KEYS:
            record = contracts.get(key)
            address = record.get("address") if isinstance(record, dict) else None
            if address is None:
                missing_required.append(key)
            elif not _valid_address(address):
                invalid_required.append(key)

        if missing_required or invalid_required:
            parts: list[str] = []
            if missing_required:
                parts.append("missing " + ", ".join(missing_required))
            if invalid_required:
                parts.append("invalid address for " + ", ".join(invalid_required))
            return {
                "ok": False,
                "mode": "deployment-current",
                "state": "incomplete-deployment-current",
                "message": (
                    "runtime/deployments/current.json is not a usable golden-path deployment "
                    f"({'; '.join(parts)}). Run: {DEV_CHAIN_RESET_COMMAND}"
                ),
                "deployment_path": str(path),
                "rpc_url": rpc_url,
                "chain_id": chain_id,
                "contracts": contracts,
                "rpc_source": rpc_source,
                "reset_command": DEV_CHAIN_RESET_COMMAND,
                "diagnosis_command": DEV_CHAIN_DIAGNOSIS_COMMAND,
            }

        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        return {
            "ok": True,
            "mode": "deployment-current",
            "state": "configured",
            "message": "using deployment-owned runtime/deployments/current.json golden-path blockchain config",
            "env_path": str(self.blockchain_env_path),
            "deployment_path": str(path),
            "rpc_url": rpc_url,
            "chain_id": chain_id,
            "source": DEPLOYMENT_RUNTIME_SOURCE,
            "deployment_source": source.get("kind") or "unknown",
            "deployment_run_id": payload.get("run_id"),
            "environment": str(payload.get("environment") or "dev"),
            "offices": payload.get("offices") if isinstance(payload.get("offices"), list) else [],
            "contracts": contracts,
            "rpc_source": rpc_source,
            "reset_command": DEV_CHAIN_RESET_COMMAND,
            "diagnosis_command": DEV_CHAIN_DIAGNOSIS_COMMAND,
        }

    def _runtime_config_status(self, config: dict[str, Any]) -> dict[str, Any]:
        if not config.get("ok"):
            return self._component(
                ok=False,
                state="blocked",
                message=str(config.get("message") or "blockchain config is not ready"),
                reset_command=config.get("reset_command"),
                diagnosis_command=config.get("diagnosis_command"),
            )

        return self._component(
            ok=True,
            state="ready",
            message="blockchain runtime config is available; deployment manifests are deploy-owned",
            source=config.get("source"),
            deployment_source=config.get("deployment_source"),
            deployment_path=config.get("deployment_path"),
            environment=str(config.get("environment") or "external"),
            rpc_url=config.get("rpc_url"),
            chain_id=config.get("chain_id"),
            offices=config.get("offices") or [],
            contracts=config.get("contracts") or {},
        )

    def _contract_code_status(self, config: dict[str, Any], rpc: dict[str, Any]) -> dict[str, Any]:
        if not config.get("ok"):
            return self._component(ok=False, state="blocked", message="blockchain config is not ready")
        if config.get("mode") != "deployment-current":
            return self._component(
                ok=True,
                state="not-required",
                message="external blockchain mode does not require deployment-current contract-code checks",
            )
        if not rpc.get("ok"):
            return self._component(
                ok=False,
                state="blocked",
                message="RPC is not ready; deployed contract code could not be verified",
                reset_command=config.get("reset_command"),
                diagnosis_command=config.get("diagnosis_command"),
            )

        rpc_url = str(config.get("rpc_url") or DEFAULT_RPC_URL)
        contracts = config.get("contracts") if isinstance(config.get("contracts"), dict) else {}
        checked: dict[str, Any] = {}
        failures: list[str] = []
        for key in REQUIRED_CONTRACT_KEYS:
            record = contracts.get(key)
            address = _valid_address(record.get("address")) if isinstance(record, dict) else None
            if not address:
                checked[key] = {
                    "ok": False,
                    "state": "missing-address",
                    "message": "required contract address is missing from runtime/deployments/current.json",
                }
                failures.append(key)
                continue
            probe = self.contract_code_probe(rpc_url, address)
            checked[key] = probe
            if not probe.get("ok"):
                failures.append(key)

        if failures:
            return self._component(
                ok=False,
                state="missing-contract-code",
                message=(
                    "configured deployment does not match the connected chain; required contract code is missing "
                    f"for {', '.join(failures)}. Run: {DEV_CHAIN_DIAGNOSIS_COMMAND}"
                ),
                checked_contracts=checked,
                reset_command=config.get("reset_command"),
                diagnosis_command=config.get("diagnosis_command"),
            )

        return self._component(
            ok=True,
            state="ready",
            message="required deployment contract code is present on the connected chain",
            checked_contracts=checked,
        )

    def _reconcile_blockchain_once(
        self,
        phase: str,
        *,
        previous_state: dict[str, Any] | None = None,
        allow_reset: bool = True,
    ) -> dict[str, Any]:
        state = previous_state if previous_state is not None else self._base_state(phase)
        config = self._chain_config()
        dev_chain = self._component(
            ok=True,
            state="not-required",
            message="external blockchain mode or healthy local runtime does not need a dev-chain reset",
        )

        trigger = self._trigger_for_pre_config(config) if allow_reset else None
        if trigger is not None:
            dev_chain = self._run_dev_chain_reset(config, trigger=trigger)
            if dev_chain.get("ok"):
                config = self._chain_config()

        runtime = self._runtime_config_status(config)
        docker = self._docker_delegation_status(config)
        compose = self._component(
            ok=True,
            state="removed",
            message="legacy Compose dev-chain fallback has been removed; runtime/deployments/current.json is the local golden path",
        )

        if config.get("ok"):
            rpc = self.rpc_probe(str(config.get("rpc_url") or DEFAULT_RPC_URL), int(config.get("chain_id") or DEFAULT_CHAIN_ID))
        else:
            rpc = self._component(ok=False, state="blocked", message="blockchain config is not ready")
        contracts = self._contract_code_status(config, rpc)

        post_trigger = self._trigger_for_runtime_status(config=config, rpc=rpc, contracts=contracts) if allow_reset else None
        if trigger is None and post_trigger is not None:
            dev_chain = self._run_dev_chain_reset(config, trigger=post_trigger)
            if dev_chain.get("ok"):
                config = self._chain_config()
                runtime = self._runtime_config_status(config)
                docker = self._docker_delegation_status(config)
                if config.get("ok"):
                    rpc = self.rpc_probe(str(config.get("rpc_url") or DEFAULT_RPC_URL), int(config.get("chain_id") or DEFAULT_CHAIN_ID))
                else:
                    rpc = self._component(ok=False, state="blocked", message="blockchain config is not ready")
                contracts = self._contract_code_status(config, rpc)

        ok = bool(
            config.get("ok")
            and runtime.get("ok")
            and docker.get("ok")
            and compose.get("ok")
            and dev_chain.get("ok")
            and rpc.get("ok")
            and contracts.get("ok")
        )
        boot_proven = bool(ok or self._boot_proven_once)
        service_state = "ready" if ok else ("booted-degraded" if boot_proven else "booting")
        state.update(
            {
                "ok": ok,
                "state": service_state,
                "boot_proven": boot_proven,
                "updated_at": _now_iso(),
                "mode": config.get("mode"),
                "message": (
                    "blockchain service is ready"
                    if ok
                    else (
                        "blockchain service booted earlier in this process and is observing degraded runtime health"
                        if boot_proven
                        else "blockchain service is booting and will retry local dev-chain reset"
                    )
                ),
                "config": config,
                "runtime": runtime,
                "docker": docker,
                "compose": compose,
                "dev_chain": dev_chain,
                "rpc": rpc,
                "contracts": contracts,
                "components": {
                    "config": config,
                    "runtime": runtime,
                    "docker": docker,
                    "compose": compose,
                    "dev_chain": dev_chain,
                    "rpc": rpc,
                    "contracts": contracts,
                },
            }
        )
        state["service"]["state"] = state["state"]
        return state

    def _full_boot_reconcile(self) -> dict[str, Any]:
        return self._reconcile_blockchain_once("booting")

    def _light_keepalive(self, state: dict[str, Any]) -> dict[str, Any]:
        # After this service process proves boot once, light keepalive is
        # observational only. It must not run dev-chain reset repair again until
        # the service is restarted.
        return self._reconcile_blockchain_once("watching", previous_state=state, allow_reset=not self._boot_proven_once)

    def _emit_completed_process_to_main_log(self, result: subprocess.CompletedProcess[str]) -> None:
        command = result.args if isinstance(result.args, list) else []
        fields = {
            "command": " ".join(str(part) for part in command) if command else str(result.args),
            "cwd": str(self.root),
            "returncode": result.returncode,
        }
        if result.stdout:
            emit_main_log_text(
                service=SERVICE_NAME,
                source_service=SERVICE_NAME,
                kind="subprocess-stream",
                stream="stdout",
                message=_process_stream_text(result.stdout),
                timeout_s=0.05,
                **fields,
            )
        if result.stderr:
            emit_main_log_text(
                service=SERVICE_NAME,
                source_service=SERVICE_NAME,
                kind="subprocess-stream",
                stream="stderr",
                message=_process_stream_text(result.stderr),
                timeout_s=0.05,
                **fields,
            )

    def _run(self, command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        try:
            result = self.runner(
                command,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            self._emit_completed_process_to_main_log(result)
            return result
        except subprocess.TimeoutExpired as exc:
            stdout = _process_stream_text(getattr(exc, "stdout", None) or getattr(exc, "output", None))
            stderr = _process_stream_text(getattr(exc, "stderr", None))
            if stderr:
                stderr = f"{stderr.rstrip()}\ncommand timed out after {timeout:g} seconds"
            else:
                stderr = f"command timed out after {timeout:g} seconds"
            result = subprocess.CompletedProcess(command, 124, stdout=stdout, stderr=stderr)
            self._emit_completed_process_to_main_log(result)
            return result
        except (OSError, subprocess.SubprocessError) as exc:
            result = subprocess.CompletedProcess(command, 127, stdout="", stderr=str(exc))
            self._emit_completed_process_to_main_log(result)
            return result

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
    if command != "status":
        install_main_log_hooks_from_env(default_service_name=SERVICE_NAME, root=args.root)

    if command == "status":
        print(json.dumps(load_blockchain_service_state(args.root), indent=2, sort_keys=True))
        return 0

    service = _service_from_args(args)
    state = service.boot(watch=bool(getattr(args, "watch", False)), max_watch_loops=getattr(args, "max_watch_loops", None))
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0 if state.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
