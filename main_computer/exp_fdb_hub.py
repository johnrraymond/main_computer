from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from main_computer.config import DEFAULT_HUB_BRIDGE_BACKEND, MainComputerConfig
from main_computer.contract_config import contract_config_path
from main_computer.exp_fdb_credit_ledger import ExperimentalFoundationDbConfig, ExperimentalFoundationDbCreditLedger
from main_computer.exp_fdb_hub_state import (
    ExperimentalFoundationDbEnergyCreditLedger,
    ExperimentalFoundationDbFeedbackStateStore,
    ExperimentalFoundationDbHubState,
    ExperimentalFoundationDbMultiSessionKeyStore,
    ExperimentalFoundationDbQuoteStateStore,
    ExperimentalFoundationDbRegistry,
    ExperimentalFoundationDbRequestStateStore,
    ExperimentalFoundationDbSecureSessionStore,
)
from main_computer.hub import (
    DEFAULT_HUB_PORT,
    HUB_SECURITY_PROFILE,
    HubDispatcher,
    HubHttpServer,
    HubServerHandler,
)
from main_computer.hub_credit_bridge_completion import HubCreditBridgeCompletionService
from main_computer.hub_credit_indexer import HubCreditIndexer


DEFAULT_EXP_FDB_HUB_PORT = DEFAULT_HUB_PORT + 100
DEFAULT_EXP_FDB_NAMESPACE = "main-computer-exp-fdb"
DEFAULT_EXP_FDB_CLUSTER_FILE = Path(".foundationdb") / "docker.cluster"
DEFAULT_EXP_FDB_HUB_ROOT = Path("runtime") / "exp-fdb-hub"
DEFAULT_EXP_FDB_DOCKER_OUTPUT_DIR = Path("runtime") / "scheduler-lab" / "exp-fdb"
DEFAULT_EXP_FDB_DOCKER_COMPOSE_FILE = Path("deploy") / "scheduler-lab" / "docker-compose.worker-lab.yml"
DEFAULT_EXP_FDB_DOCKER_HUB_HOST = "host.docker.internal"
DEFAULT_EXP_FDB_DOCKER_CONTAINER_OUTPUT_DIR = "/lab-output"
DEFAULT_EXP_FDB_SMOKE_SCRIPT = Path("scripts") / "smoke_foundationdb_credit_ledger_primitives.py"
DEFAULT_EXP_FDB_SMOKE_CONTAINER_NAME = "main-computer-foundationdb-smoke"
DEFAULT_EXP_FDB_SMOKE_DOCKER_IMAGE = "foundationdb/foundationdb:7.4.6"
DEFAULT_EXP_FDB_SMOKE_PORT = 4550
DEFAULT_EXP_FDB_SMOKE_NAMESPACE = "main-computer-exp-fdb-autostart-smoke"
DEFAULT_EXP_FDB_SMOKE_START_TIMEOUT_SECONDS = 45.0


def _optional_int_arg(value: object, *, flag: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = int(text, 0)
    except ValueError:
        raise SystemExit(f"{flag} must be an integer, got {text!r}") from None
    if parsed < 0:
        raise SystemExit(f"{flag} must be non-negative")
    return parsed


class ExperimentalFoundationDbHubServerHandler(HubServerHandler):
    """Hub request handler with opt-in exp multi-hub access logging.

    Scheduler-lab Docker runs can generate thousands of expected 503 worker
    route responses while probing overload behavior.  Keep those request access
    logs off by default so stderr remains readable; set EXP_FDB_HUB_ACCESS_LOGS=1
    when raw per-request HTTP logs are needed.
    """

    def _exp_fdb_access_log_enabled(self) -> bool:
        raw = str(os.environ.get("EXP_FDB_HUB_ACCESS_LOGS", "0")).strip().lower()
        return raw in {"1", "true", "yes", "on", "all"}

    def log_message(self, format: str, *args: object) -> None:
        if not getattr(self.server, "verbose", False):
            return
        if not self._exp_fdb_access_log_enabled():
            return
        try:
            message = format % args
        except Exception:
            message = f"{format} {args!r}"
        port = getattr(self.server, "server_port", "?")
        client = self.client_address[0] if self.client_address else "-"
        print(
            f"[exp-fdb-hub:{port}] {client} - - [{self.log_date_time_string()}] {message}",
            file=sys.stderr,
            flush=True,
        )


class ExperimentalFoundationDbHubHttpServer(HubHttpServer):
    """Manual-only Hub clone that keeps shared hub state in FoundationDB."""

    def __init__(
        self,
        server_address: tuple[str, int],
        config: MainComputerConfig,
        *,
        fdb_config: ExperimentalFoundationDbConfig,
        verbose: bool = True,
    ) -> None:
        super().__init__(server_address, config, verbose=verbose)
        diagnostics_value = str(os.environ.get("HUB_WORKER_ROUTE_DIAGNOSTICS", "0")).strip().lower()
        self.worker_route_diagnostics = verbose and diagnostics_value in {"1", "true", "yes", "on"}
        self.RequestHandlerClass = ExperimentalFoundationDbHubServerHandler
        self.fdb_state = ExperimentalFoundationDbHubState(fdb_config)
        self.registry = ExperimentalFoundationDbRegistry(
            self.fdb_state,
            root=self.hub_root,
            allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
        )
        self.energy_ledger = ExperimentalFoundationDbEnergyCreditLedger(
            self.fdb_state,
            root=self.hub_root / "energy_credits",
        )
        self.credit_ledger = ExperimentalFoundationDbCreditLedger(fdb_config)
        self.credit_indexer = HubCreditIndexer(self.credit_ledger)
        self.credit_bridge_completion = HubCreditBridgeCompletionService(self.credit_ledger, config)
        self.request_store = ExperimentalFoundationDbRequestStateStore(self.fdb_state)
        self.quote_store = ExperimentalFoundationDbQuoteStateStore(self.fdb_state)
        self.feedback_store = ExperimentalFoundationDbFeedbackStateStore(self.fdb_state)
        self.secure_session_store = ExperimentalFoundationDbSecureSessionStore(self.fdb_state)
        self.multisession_key_store = ExperimentalFoundationDbMultiSessionKeyStore(self.fdb_state)
        self.dispatcher = HubDispatcher(
            self.registry,
            self.energy_ledger,
            timeout_s=config.hub_timeout_s,
            allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
            credit_ledger=self.credit_ledger,
            default_credits_per_request=config.hub_credits_per_request,
            request_store=self.request_store,
            quote_store=self.quote_store,
            secure_session_store=self.secure_session_store,
            feedback_store=self.feedback_store,
        )




def _default_dev_chain_deployment_path(*, repo_root: Path, network_key: str) -> Path:
    clean_network = str(network_key or "dev").strip() or "dev"
    return repo_root / "runtime" / "deployments" / clean_network / "latest.json"


def _default_contracts_path(*, repo_root: Path, network_key: str) -> Path:
    return contract_config_path(str(network_key or "dev").strip() or "dev", repo_root=repo_root)


def _hub_bridge_backend_from_args(args: argparse.Namespace, base: MainComputerConfig) -> str:
    return str(args.bridge_backend or base.hub_bridge_backend or DEFAULT_HUB_BRIDGE_BACKEND).strip().lower() or DEFAULT_HUB_BRIDGE_BACKEND

def build_experimental_config(args: argparse.Namespace, *, port: int) -> tuple[MainComputerConfig, ExperimentalFoundationDbConfig]:
    base = MainComputerConfig.from_env()
    repo_root = _repo_root_from_args(args)
    hub_root = Path(args.hub_root) if args.hub_root else DEFAULT_EXP_FDB_HUB_ROOT
    if not hub_root.is_absolute():
        hub_root = repo_root / hub_root

    hub_url = args.hub_url or f"http://{args.host}:{port}"
    network_key = str(getattr(args, "network_key", "exp-fdb") or "exp-fdb").strip() or "exp-fdb"
    bridge_backend = _hub_bridge_backend_from_args(args, base)
    allow_missing_bridge_signer = bool(getattr(args, "allow_missing_bridge_signer", False)) or base.hub_allow_missing_bridge_signer
    enable_smoke_bridge = bool(getattr(args, "enable_smoke_bridge", False)) or base.hub_enable_smoke_bridge
    dev_chain_deployment_path = Path(args.dev_chain_deployment_path) if args.dev_chain_deployment_path else base.hub_dev_chain_deployment_path
    if (
        dev_chain_deployment_path is None
        and bridge_backend not in {"mock", "mock-chain", "mock-chain-lite"}
        and not allow_missing_bridge_signer
    ):
        dev_chain_deployment_path = _default_dev_chain_deployment_path(repo_root=repo_root, network_key=network_key)
    if dev_chain_deployment_path is not None and not dev_chain_deployment_path.is_absolute():
        dev_chain_deployment_path = repo_root / dev_chain_deployment_path
    contracts_path = Path(args.contracts_path) if getattr(args, "contracts_path", None) else base.hub_contracts_path
    if contracts_path is None and bridge_backend not in {"mock", "mock-chain", "mock-chain-lite"}:
        contracts_path = _default_contracts_path(repo_root=repo_root, network_key=network_key)
    if contracts_path is not None and not contracts_path.is_absolute():
        contracts_path = repo_root / contracts_path
    ring_config_path = Path(args.ring_config_path) if getattr(args, "ring_config_path", None) else base.hub_ring_config_path
    if ring_config_path is not None and not ring_config_path.is_absolute():
        ring_config_path = repo_root / ring_config_path

    network_display_name = (
        str(getattr(args, "network_display_name", "Experimental FDB Hub") or "Experimental FDB Hub").strip()
        or "Experimental FDB Hub"
    )
    network_kind = str(getattr(args, "network_kind", "experimental") or "experimental").strip() or "experimental"
    chain_id_arg = _optional_int_arg(getattr(args, "chain_id", ""), flag="--chain-id")
    chain_id = chain_id_arg if chain_id_arg is not None else base.chain_id
    chain_rpc_url_arg = str(getattr(args, "chain_rpc_url", "") or "").strip()
    chain_rpc_url = chain_rpc_url_arg or base.chain_rpc_url

    config = replace(
        base,
        hub_root=hub_root,
        hub_bind_host=args.host,
        hub_bind_port=port,
        hub_url=hub_url,
        hub_network=network_key,
        hub_network_display_name=network_display_name,
        hub_network_kind=network_kind,
        hub_allow_insecure_dev_network=True,
        hub_bridge_backend=bridge_backend,
        hub_dev_chain_deployment_path=dev_chain_deployment_path,
        hub_contracts_path=contracts_path,
        hub_allow_missing_bridge_signer=allow_missing_bridge_signer,
        hub_enable_smoke_bridge=enable_smoke_bridge,
        hub_ring_config_path=ring_config_path,
        chain_id=chain_id,
        chain_id_source="arg" if chain_id_arg is not None else base.chain_id_source,
        chain_rpc_url=chain_rpc_url,
        chain_rpc_url_source="arg" if chain_rpc_url_arg else base.chain_rpc_url_source,
    )

    cluster_file = _cluster_file_from_args(args, repo_root=repo_root)

    fdb_config = ExperimentalFoundationDbConfig(
        cluster_file=cluster_file,
        namespace=args.namespace,
        api_version=args.api_version,
        repo_root=repo_root,
        activate_native_client=not args.no_activate_cached_native_client,
    )
    return config, fdb_config


def parse_ports(value: str | int | Sequence[int] | None, *, default: int = DEFAULT_EXP_FDB_HUB_PORT) -> list[int]:
    if value is None or value == "":
        return [int(default)]
    if isinstance(value, int):
        raw_parts = [str(value)]
    elif isinstance(value, str):
        raw_parts = [part.strip() for part in value.split(",")]
    else:
        raw_parts = [str(part).strip() for part in value]
    ports: list[int] = []
    for raw in raw_parts:
        if not raw:
            continue
        try:
            port = int(raw)
        except Exception as exc:
            raise SystemExit(f"invalid port value {raw!r}") from exc
        if port <= 0 or port > 65535:
            raise SystemExit(f"invalid port value {port}; expected 1..65535")
        if port not in ports:
            ports.append(port)
    if not ports:
        ports.append(int(default))
    return ports


def docker_hub_base_urls(args: argparse.Namespace, live_ports: Sequence[int]) -> list[str]:
    ports = parse_ports(args.docker_ports, default=live_ports[0]) if args.docker_ports else list(live_ports)
    host = str(args.docker_hub_host or DEFAULT_EXP_FDB_DOCKER_HUB_HOST).strip()
    if not host:
        host = DEFAULT_EXP_FDB_DOCKER_HUB_HOST
    return [f"http://{host}:{port}" for port in ports]



def _repo_root_from_args(args: argparse.Namespace) -> Path:
    return Path(args.repo_root).resolve() if args.repo_root else Path.cwd().resolve()


def _cluster_file_from_args(args: argparse.Namespace, *, repo_root: Path) -> Path:
    cluster_file = Path(args.cluster_file)
    if not cluster_file.is_absolute():
        cluster_file = repo_root / cluster_file
    return cluster_file.resolve()


def _docker_container_running(docker_command: str, container_name: str) -> bool | None:
    try:
        result = subprocess.run(
            [docker_command, "inspect", "--format={{.State.Running}}", container_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None

    if result.returncode != 0:
        return False
    return result.stdout.strip().lower() == "true"


def _should_autostart_foundationdb_smoke(args: argparse.Namespace, *, repo_root: Path, cluster_file: Path) -> bool:
    if args.no_fdb_autostart:
        return False
    if not cluster_file.exists():
        return True

    default_cluster_file = (repo_root / DEFAULT_EXP_FDB_CLUSTER_FILE).resolve()
    if cluster_file != default_cluster_file:
        return False

    running = _docker_container_running(args.fdb_docker_command, args.fdb_container_name)
    return running is False


def ensure_foundationdb_smoke_loaded(args: argparse.Namespace) -> None:
    """Start/reuse the local smoke FoundationDB Docker cluster before hub startup.

    This keeps the manual exp-FDB command self-contained for the default local
    Docker cluster path.  The smoke uses its own namespace and clears that
    namespace afterwards; it does not clear the experiment namespace.
    """

    repo_root = _repo_root_from_args(args)
    cluster_file = _cluster_file_from_args(args, repo_root=repo_root)
    if not _should_autostart_foundationdb_smoke(args, repo_root=repo_root, cluster_file=cluster_file):
        return

    smoke_script = repo_root / DEFAULT_EXP_FDB_SMOKE_SCRIPT
    if not smoke_script.exists():
        raise SystemExit(f"FoundationDB smoke script not found: {smoke_script}")

    command = [
        sys.executable,
        str(smoke_script),
        "--cluster-file",
        str(cluster_file),
        "--api-version",
        str(args.api_version),
        "--namespace",
        DEFAULT_EXP_FDB_SMOKE_NAMESPACE,
        "--concurrent-holds",
        "11",
        "--workers",
        "2",
        "--fdb-container-name",
        str(args.fdb_container_name),
        "--fdb-port",
        str(int(args.fdb_port)),
        "--fdb-docker-image",
        str(args.fdb_docker_image),
        "--docker-command",
        str(args.fdb_docker_command),
        "--docker-start-timeout",
        str(float(args.fdb_docker_start_timeout)),
        "--keep-container",
        "--reuse-container",
    ]
    if args.fdb_docker_platform:
        command.extend(["--docker-platform", str(args.fdb_docker_platform)])

    print("FoundationDB Docker cluster is not loaded; starting/reusing the local smoke container.")
    print(f"FDB smoke container: {args.fdb_container_name}")
    print(f"FDB cluster file: {cluster_file}")
    print("FDB bootstrap command:")
    print("  " + " ".join(command))
    result = subprocess.run(command, cwd=str(repo_root))
    if result.returncode != 0:
        raise SystemExit(result.returncode)



def create_exp_fdb_hub_server(args: argparse.Namespace, *, port: int) -> ExperimentalFoundationDbHubHttpServer:
    config, fdb_config = build_experimental_config(args, port=port)
    if not fdb_config.cluster_file.exists():
        raise SystemExit(
            f"FoundationDB cluster file not found: {fdb_config.cluster_file}\n"
            "Start the local FDB container first, for example:\n"
            "  python scripts/smoke_foundationdb_credit_ledger_primitives.py --keep-container"
        )

    server = ExperimentalFoundationDbHubHttpServer(
        (args.host, port),
        config,
        fdb_config=fdb_config,
        verbose=not args.noverbose,
    )
    fdb_health = server.credit_ledger.health_check()
    state_health = server.fdb_state.health_check()

    print(f"Experimental FDB hub server: http://{args.host}:{server.server_port}")
    print("Manual-only: this entry point is not part of normal Main Computer startup.")
    print(f"Hub runtime: {server.hub_root}")
    print(f"Hub admin/control site: http://{args.host}:{server.server_port}/admin")
    print(f"Hub security: high-security={config.hub_high_security} profile={HUB_SECURITY_PROFILE}; local experimental mode allows insecure dev network")
    print(f"Worker route diagnostics: {'on' if server.worker_route_diagnostics else 'off'} (set HUB_WORKER_ROUTE_DIAGNOSTICS=1 to enable per-stage logging)")
    print(f"FDB cluster file: {fdb_config.cluster_file}")
    print(f"FDB namespace: {fdb_config.namespace}")
    ring_status = server.ring_admission_config.public_status()
    print(
        "Ring admission config: "
        f"path={ring_status.get('ring_config_path')} "
        f"default_min_ring={ring_status.get('ring_config_default_min_ring')} "
        f"allowlisted_wallet_count={ring_status.get('ring_config_allowlisted_wallet_count')} "
        f"hash={ring_status.get('ring_config_hash')}"
    )
    print(f"FDB credit ledger health: {fdb_health}")
    print(f"FDB hub state health: {state_health}")
    bridge_backend_status = getattr(server.bridge_backend, "status", lambda: {"backend": config.hub_bridge_backend})()
    print(f"Hub bridge backend: {bridge_backend_status}")
    return server


def launch_scheduler_lab_docker(args: argparse.Namespace, *, hub_base_urls: Sequence[str]) -> subprocess.Popen[bytes]:
    repo_root = _repo_root_from_args(args)
    compose_file = Path(args.docker_compose_file or DEFAULT_EXP_FDB_DOCKER_COMPOSE_FILE)
    if not compose_file.is_absolute():
        compose_file = repo_root / compose_file
    compose_file = compose_file.resolve()
    if not compose_file.exists():
        raise SystemExit(f"scheduler-lab docker compose file not found: {compose_file}")

    output_dir = Path(args.docker_output_dir or DEFAULT_EXP_FDB_DOCKER_OUTPUT_DIR)
    if not output_dir.is_absolute():
        output_dir = repo_root / output_dir
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    hub_urls_arg = ",".join(str(url).rstrip("/") for url in hub_base_urls)
    container_output = DEFAULT_EXP_FDB_DOCKER_CONTAINER_OUTPUT_DIR
    total_nodes = int(args.nodes if args.nodes is not None else args.docker_total)
    env = os.environ.copy()
    env.update(
        {
            "HUB_BASE_URLS": hub_urls_arg,
            "HUB_BASE_URL": str(hub_base_urls[0]).rstrip("/"),
            "LAB_OUTPUT_DIR": container_output,
            "LAB_NODE_LIST": f"{container_output}/{total_nodes}-exp-fdb-nodes.jsonl",
            "LAB_OUTPUT_DIR_HOST": str(output_dir),
            "GENERATE_NODE_LIST": "1",
            "LAB_ROLE": str(args.docker_role),
            "LAB_TOTAL": str(total_nodes),
            "LAB_NODES": str(total_nodes),
            "LAB_WORKTIME": str(args.worktime or ""),
            "LAB_FUNDED": str(args.funded),
            "LAB_REQUEST_STARTUP_MODE": str(args.request_startup_mode),
            "LAB_REQUEST_STARTUP_SPREAD_SECONDS": str(float(args.request_startup_spread_seconds)),
            "LAB_EXECUTION_MODE": str(args.lab_execution),
            "LAB_WARM": str(args.warm or ""),
            "B2B_FAILURES": str(int(args.b2bfailures)),
            "HTTP_TIMEOUT_SECONDS": str(float(args.http_timeout_seconds)),
            "LEASE_SECONDS": str(float(args.lease_seconds)),
        }
    )
    if args.docker_duration_seconds is not None:
        env["LAB_DURATION_SECONDS"] = str(float(args.docker_duration_seconds))
    else:
        # Compose files may use their own ${LAB_DURATION_SECONDS:-300} fallback.
        # Pass an explicit nonnumeric sentinel so the worker-lab process can
        # bypass compose defaults and derive from LAB_WORKTIME/default minimum.
        env["LAB_DURATION_SECONDS"] = "auto"
    if args.forced_alive is not None:
        env["FORCED_ALIVE_SECONDS"] = str(float(args.forced_alive))
    else:
        # Same pattern for compose defaults: the worker-lab treats this as
        # "derive from the resolved observation window".
        env["FORCED_ALIVE_SECONDS"] = "duration"

    if args.docker_workers is not None:
        env["LAB_WORKERS"] = str(args.docker_workers)
    else:
        env.pop("LAB_WORKERS", None)
    if args.docker_requesters is not None:
        env["LAB_REQUESTERS"] = str(args.docker_requesters)
    else:
        env.pop("LAB_REQUESTERS", None)

    command = [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "--profile",
        "worker-lab",
        "up",
        "--abort-on-container-exit",
        "--exit-code-from",
        "worker-lab",
    ]
    if not args.no_docker_build:
        command.append("--build")
    command.append("worker-lab")

    print("Starting scheduler lab Docker side with the dedicated lightweight worker-lab compose stack.")
    print(f"Compose file: {compose_file}")
    print(f"Host output dir: {output_dir}")
    print("Non-sticky hub URLs advertised to Docker:")
    for url in hub_base_urls:
        print(f"  {url}")
    print(f"Scheduler lab nodes: {total_nodes}")
    print(f"Scheduler lab assumed already-funded accounts: {args.funded:g}%")
    print(f"Scheduler lab execution mode: {args.lab_execution}")
    print(f"Scheduler lab request startup: {args.request_startup_mode} over {float(args.request_startup_spread_seconds):g}s")
    print(f"Scheduler lab warm-up delay: {args.warm or 'none'}")
    print(f"Scheduler lab b2b transport failure limit: {int(args.b2bfailures)}")
    if args.docker_duration_seconds is None:
        print("Scheduler lab duration seconds: derived by worker-lab from worktime/default minimum")
    else:
        print(f"Scheduler lab duration seconds: {float(args.docker_duration_seconds):g}")
    if args.forced_alive is None:
        print("Scheduler lab forced-alive grace seconds: derived from resolved worker-lab observation duration")
    else:
        print(f"Scheduler lab forced-alive grace seconds: {float(args.forced_alive):g}")
    print(f"Scheduler lab HTTP timeout seconds: {float(args.http_timeout_seconds):g}")
    if args.worktime:
        print(f"Scheduler lab worker result runtime: {args.worktime} (seconds; sigma is standard deviation)")
    print(f"Scheduler lab lease seconds: {float(args.lease_seconds):g}")
    print("Docker command:")
    print("  " + " ".join(command))
    return subprocess.Popen(command, cwd=str(repo_root), env=env)


def serve_exp_fdb_hubs(args: argparse.Namespace) -> int:
    live_ports = parse_ports(args.ports if args.ports else args.port, default=DEFAULT_EXP_FDB_HUB_PORT)
    ensure_foundationdb_smoke_loaded(args)
    servers = [create_exp_fdb_hub_server(args, port=port) for port in live_ports]
    threads: list[threading.Thread] = []
    docker_process: subprocess.Popen[bytes] | None = None
    try:
        for server in servers:
            thread = threading.Thread(target=server.serve_forever, name=f"exp-fdb-hub-{server.server_port}", daemon=True)
            thread.start()
            threads.append(thread)
        print(f"Experimental FDB hub ports listening: {', '.join(str(port) for port in live_ports)}")
        if args.docker:
            hub_urls = docker_hub_base_urls(args, live_ports)
            docker_process = launch_scheduler_lab_docker(args, hub_base_urls=hub_urls)
            return_code = docker_process.wait()
            return int(return_code)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("\nExperimental FDB hub stopped.")
            return 0
    finally:
        if docker_process is not None and docker_process.poll() is None:
            docker_process.terminate()
            try:
                docker_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                docker_process.kill()
        for server in servers:
            server.shutdown()
        for thread in threads:
            thread.join(timeout=5)
        for server in servers:
            server.server_close()


def serve_exp_fdb_hub(args: argparse.Namespace) -> None:
    raise SystemExit(serve_exp_fdb_hubs(args))

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exp-fdb-hub.py",
        description=(
            "Start a manual-only clone of the Main Computer hub that uses the local "
            "FoundationDB Docker cluster for the compute-credit ledger, worker registry, request queue, quote store, sessions, and payout state."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host/interface to bind.")
    parser.add_argument("-ports", "--ports", default=None, help="Comma-separated experimental hub ports to bind, for example 8870,8871,8872. Defaults to 8870.")
    parser.add_argument("--port", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--hub-url", help="Public URL advertised for this experimental hub. Defaults per port when omitted.")
    parser.add_argument("--network-key", default="exp-fdb", help="Hub network key advertised by /api/hub/status.")
    parser.add_argument("--network-display-name", default="Experimental FDB Hub", help="Hub network display name advertised by /api/hub/status.")
    parser.add_argument("--network-kind", default="experimental", help="Hub network kind advertised by /api/hub/status.")
    parser.add_argument("--chain-id", default="", help="Chain ID advertised by /api/hub/status. Defaults to MAIN_COMPUTER_CHAIN_ID/base config.")
    parser.add_argument("--chain-rpc-url", default="", help="Chain RPC URL advertised by /api/hub/status. Defaults to MAIN_COMPUTER_CHAIN_RPC_URL/base config.")
    parser.add_argument("--hub-root", type=Path, default=DEFAULT_EXP_FDB_HUB_ROOT, help="Separate runtime root for the experimental hub.")
    parser.add_argument("--cluster-file", type=Path, default=DEFAULT_EXP_FDB_CLUSTER_FILE, help="FoundationDB cluster file written by the FDB smoke.")
    parser.add_argument("--bridge-backend", choices=["mock-chain", "dev-chain", "credit-bridge-contract"], default=None, help="Hub bridge backend for bridge confirm endpoints. Defaults to dev-chain/contract-backed mode; use mock-chain only for explicit labs.")
    parser.add_argument("--dev-chain-deployment-path", type=Path, default=None, help="Private deployment metadata JSON used for bridge signing wallet paths. Defaults to runtime/deployments/<network-key>/latest.json in contract mode unless signer-disabled startup is allowed.")
    parser.add_argument("--contracts-path", type=Path, default=None, help="Public contract discovery JSON. Defaults to main_computer/config/<network-key>_contracts.json when contract mode is selected.")
    parser.add_argument(
        "--allow-missing-bridge-signer",
        action="store_true",
        help="Allow contract-aware Hub startup from public contract config when private bridge signer metadata is not mounted.",
    )
    parser.add_argument(
        "--enable-smoke-bridge",
        action="store_true",
        help="Enable explicit admin-only smoke bridge mode that may load smoke_client wallet metadata from a private deployment manifest.",
    )
    parser.add_argument("--ring-config-path", type=Path, default=None, help="JSON ring admission config path. Bad explicit configs fail startup.")
    parser.add_argument("--namespace", default=DEFAULT_EXP_FDB_NAMESPACE, help="FDB tuple namespace for this experiment.")
    parser.add_argument("--api-version", type=int, default=740, help="FoundationDB API version to request.")
    parser.add_argument("--repo-root", type=Path, help="Repository root. Defaults to the current working directory.")
    parser.add_argument("-docker", "--docker", action="store_true", help="After starting the requested exp hub ports, run the lightweight scheduler-lab Docker stack with the same advertised hub-port list.")
    parser.add_argument("--docker-compose-file", type=Path, default=DEFAULT_EXP_FDB_DOCKER_COMPOSE_FILE, help="Scheduler-lab Docker Compose file to run when --docker is set.")
    parser.add_argument("--docker-ports", default="", help="Comma-separated ports advertised to Docker workers. May include intentionally dead ports not present in --ports.")
    parser.add_argument("--docker-hub-host", default=DEFAULT_EXP_FDB_DOCKER_HUB_HOST, help="Host name Docker containers use to reach the host exp hubs.")
    parser.add_argument("--docker-role", choices=["all", "workers", "requesters"], default="all", help="Scheduler lab role to run in Docker.")
    parser.add_argument("--docker-total", type=int, default=120, help="Total generated scheduler lab nodes. Kept for compatibility; --nodes is preferred.")
    parser.add_argument("--nodes", type=int, default=None, help="Total generated scheduler lab nodes for Docker experiments, for example --nodes 1000.")
    parser.add_argument("--docker-workers", type=int, default=None, help="Optional worker-capable generated node count.")
    parser.add_argument("--docker-requesters", type=int, default=None, help="Optional requester-capable generated node count.")
    parser.add_argument(
        "--docker-duration-seconds",
        type=float,
        default=None,
        help="Explicit scheduler lab Docker observation duration. If omitted, the worker-lab derives max(900, 3*worktime_mu + worktime_sigma).",
    )
    parser.add_argument(
        "--worktime",
        default="",
        help="Optional Docker worker result-runtime distribution in seconds, e.g. --worktime 100mu,30sigma where sigma is standard deviation.",
    )
    parser.add_argument(
        "--funded",
        type=float,
        default=0.0,
        help="Percent of generated Docker lab node accounts to assume are already funded in FDB; accepts 90 or 0.9.",
    )
    parser.add_argument(
        "--request-startup-mode",
        choices=["auto", "natural", "surge"],
        default="auto",
        help="How Docker lab request-capable nodes begin traffic. auto surges when --funded is nonzero.",
    )
    parser.add_argument(
        "--request-startup-spread-seconds",
        type=float,
        default=0.0,
        help="Legacy async-lab request-surge spread. Process mode uses --warm for node readiness; use 0 for an immediate wall.",
    )
    parser.add_argument("--lab-execution", choices=["process", "async"], default="process", help="Docker lab execution model. process starts one OS child process per node.")
    parser.add_argument("--warm", default="", help="Docker node warm-up delay distribution in seconds before first hub contact, e.g. --warm 2mu,1sigma.")
    parser.add_argument("--b2bfailures", type=int, default=10, help="Consecutive transport failures before each node process self-terminates after --forced-alive has elapsed. 0 disables.")
    parser.add_argument(
        "--forced-alive",
        type=float,
        default=None,
        help="Optional seconds each Docker node must stay alive before --b2bfailures can self-terminate it. If omitted, the worker-lab keeps nodes alive for the resolved observation duration.",
    )
    parser.add_argument("--http-timeout-seconds", type=float, default=1.0, help="Short per-attempt hub HTTP timeout used by Docker node processes.")
    parser.add_argument("--lease-seconds", type=float, default=180.0, help="Lease duration advertised by Docker workers when polling. Increase this above expected --worktime.")
    parser.add_argument("--docker-output-dir", type=Path, default=DEFAULT_EXP_FDB_DOCKER_OUTPUT_DIR, help="Repository-relative output directory for scheduler lab events.")
    parser.add_argument("--no-docker-build", action="store_true", help="Do not pass --build to docker compose up.")
    parser.add_argument(
        "--no-fdb-autostart",
        action="store_true",
        help=(
            "Do not automatically start/reuse the local FoundationDB smoke Docker container "
            "when the default .foundationdb/docker.cluster path is missing or not loaded."
        ),
    )
    parser.add_argument("--fdb-container-name", default=DEFAULT_EXP_FDB_SMOKE_CONTAINER_NAME, help="Local FoundationDB smoke container name to start/reuse before hub startup.")
    parser.add_argument("--fdb-port", type=int, default=DEFAULT_EXP_FDB_SMOKE_PORT, help="Host/container TCP port for the local FoundationDB smoke database.")
    parser.add_argument("--fdb-docker-image", default=DEFAULT_EXP_FDB_SMOKE_DOCKER_IMAGE, help="FoundationDB Docker image used when auto-starting the local smoke database.")
    parser.add_argument("--fdb-docker-command", default="docker", help="Docker CLI executable used when auto-starting the local FoundationDB smoke database.")
    parser.add_argument("--fdb-docker-platform", default=None, help="Optional Docker platform for the auto-started FoundationDB smoke container, for example linux/amd64.")
    parser.add_argument("--fdb-docker-start-timeout", type=float, default=DEFAULT_EXP_FDB_SMOKE_START_TIMEOUT_SECONDS, help="Seconds to wait for the auto-started FoundationDB smoke database to become configurable.")
    parser.add_argument(
        "--no-activate-cached-native-client",
        action="store_true",
        help="Do not add .foundationdb/native-client to PATH/DLL search path before importing FDB.",
    )
    parser.add_argument(
        "-noverbose",
        "--noverbose",
        action="store_true",
        help="Suppress hub request logging.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.funded < 0:
        raise SystemExit("--funded must be >= 0")
    if args.funded <= 1:
        args.funded *= 100.0
    if args.funded > 100:
        raise SystemExit("--funded must be <= 100")
    if args.request_startup_spread_seconds < 0:
        raise SystemExit("--request-startup-spread-seconds must be >= 0")
    if args.b2bfailures < 0:
        raise SystemExit("--b2bfailures must be >= 0")
    if args.docker_duration_seconds is not None and args.docker_duration_seconds <= 0:
        raise SystemExit("--docker-duration-seconds must be > 0")
    if args.forced_alive is not None and args.forced_alive < 0:
        raise SystemExit("--forced-alive must be >= 0")
    if args.http_timeout_seconds <= 0:
        raise SystemExit("--http-timeout-seconds must be > 0")
    if args.fdb_port <= 0 or args.fdb_port > 65535:
        raise SystemExit("--fdb-port must be 1..65535")
    if args.fdb_docker_start_timeout <= 0:
        raise SystemExit("--fdb-docker-start-timeout must be > 0")
    if args.request_startup_mode == "auto":
        args.request_startup_mode = "surge" if args.funded > 0 else "natural"
    return serve_exp_fdb_hubs(args)


if __name__ == "__main__":
    raise SystemExit(main())
