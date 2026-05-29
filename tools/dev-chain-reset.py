#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


def ensure_repo_root_on_sys_path() -> Path:
    """Make direct execution from tools/ resolve the in-repo package.

    Running ``python tools/dev-chain-reset.py`` puts the tools directory at
    ``sys.path[0]``.  Add the repository root before importing ``main_computer``
    so the documented Windows/PowerShell command works without requiring users
    to preconfigure PYTHONPATH.
    """

    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / "main_computer").is_dir():
            root_text = str(candidate)
            if root_text not in sys.path:
                sys.path.insert(0, root_text)
            return candidate
    return current


ensure_repo_root_on_sys_path()

from main_computer.prod_lock import require_unlocked_production_state


FOUNDRY_IMAGE = "ghcr.io/foundry-rs/foundry:latest"
DEFAULT_MNEMONIC = "test test test test test test test test test test test junk"
DEFAULT_CHAIN_ID = 42424242
DEFAULT_HOST_RPC_URL = "http://127.0.0.1:18545"
DEFAULT_PROJECT_NAME = "main-computer-dev"
DEFAULT_DEPLOYMENT_ENVIRONMENT = "dev"
DEPLOYMENT_SCHEMA = "main-computer.deployment.v1"
DEFAULT_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEFAULT_OFFICE_KEYS = [
    {
        "office": "O0",
        "title": "Captain",
        "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "private_key": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    },
    {
        "office": "O1",
        "title": "First Officer",
        "address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "private_key": "0x59c6995e998f97a5a0044966f094538eeb8b1416d61b7aae62a49a6c8f6a3c11",
    },
    {
        "office": "O2",
        "title": "Second Officer",
        "address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "private_key": "0x5de4111a07a2c0d7c97dfca3fafa4b6e8f4a9cdf764a29a7e1a8cedcf74f4a05",
    },
    {
        "office": "O3",
        "title": "Third Officer",
        "address": "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
        "private_key": "0x7c85211829426c7553fd53dfebede1b7a8129cf96fbb0d8f7c109e363d7f29e8",
    },
]


@dataclass(frozen=True)
class DeploymentSpec:
    key: str
    target: str
    constructor_args: list[str]


@dataclass(frozen=True)
class DockerPortOwner:
    container_id: str
    name: str
    ports: str


def log(message: str = "") -> None:
    print(message, flush=True)


def repo_root() -> Path:
    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (
            (candidate / "new_patch.py").exists()
            or (candidate / "pyproject.toml").exists()
            or (candidate / "contracts").is_dir()
            or (candidate / ".git").exists()
        ):
            return candidate
    return current


def docker_executable() -> str:
    return shutil.which("docker") or "docker"


def docker_mount_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name == "nt":
        return resolved.as_posix()
    return str(resolved)


def run_id_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create an isolated local Anvil state machine and deploy the root Main Computer contracts."
    )
    parser.add_argument("--yes", action="store_true", help="Run the deploy. Without --yes, only --dry-run is allowed.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands and write preview deploy outputs.")
    parser.add_argument("--run-id", default=None, help="Stable soft-chroot run id under runtime/dev-chain/runs/<run-id>.")
    parser.add_argument("--compose-file", default="docker-compose.dev.yml", help="Accepted for compatibility; not used by soft deploy.")
    parser.add_argument("--project-name", default=DEFAULT_PROJECT_NAME)
    parser.add_argument(
        "--environment",
        default=DEFAULT_DEPLOYMENT_ENVIRONMENT,
        help="Deployment environment name for the production-shaped runtime publication.",
    )
    parser.add_argument("--service", default="ethereum-dev", help="Accepted for compatibility; not used by soft deploy.")
    parser.add_argument("--chain-id", type=int, default=DEFAULT_CHAIN_ID)
    parser.add_argument("--host-rpc-url", default=DEFAULT_HOST_RPC_URL)
    parser.add_argument(
        "--host-port",
        type=int,
        default=None,
        help="Shorthand for changing the host RPC port while keeping the host-rpc-url host/scheme.",
    )
    parser.add_argument("--container-rpc-url", default=None)
    parser.add_argument(
        "--port-strategy",
        choices=("replace-project", "replace-any", "auto", "fail"),
        default="replace-project",
        help=(
            "How to handle an occupied host RPC port. replace-project removes stale "
            "main-computer dev-chain containers on the port; replace-any removes any "
            "Docker container on the port; auto picks the next free host port; fail "
            "stops with diagnostics."
        ),
    )
    parser.add_argument("--foundry-image", default=FOUNDRY_IMAGE)
    parser.add_argument("--private-key", default=DEFAULT_PRIVATE_KEY)
    parser.add_argument("--offices", default=None, help="Comma-separated list of exactly four office addresses.")
    parser.add_argument("--accounts", type=int, default=4, help="Anvil account pool size. Must be greater than one.")
    parser.add_argument("--balance", default="10000", help="Initial Anvil balance per account.")
    parser.add_argument("--mnemonic", default=DEFAULT_MNEMONIC)
    parser.add_argument(
        "--deploy",
        choices=("alpha-beta-lockout", "xlag-bridge-reserve"),
        action="append",
        default=[],
        help="Deploy only a selected root contract. May be repeated. Defaults to all root contracts.",
    )
    parser.add_argument("--max-payout-wei", default="1000000000000000000")
    parser.add_argument("--payout-delay-blocks", default="1")
    parser.add_argument("--reset-delay-blocks", default="1")
    parser.add_argument("--wait-timeout-s", type=float, default=30.0)
    parser.add_argument("--deploy-timeout-s", type=float, default=120.0)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument(
        "--deployment-output-dir",
        type=Path,
        default=None,
        help="Override runtime/deployments output root for tests or unusual layouts.",
    )
    parser.add_argument("--no-deploy", action="store_true", help="Start/describe the soft chain but skip contract deployment.")
    return parser


def resolved_run_id(args: argparse.Namespace) -> str:
    raw = args.run_id or run_id_stamp()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", raw):
        raise ValueError("run id must start with a letter/number and contain only letters, numbers, dot, dash, or underscore")
    return raw


def validate_environment_name(value: str) -> str:
    raw = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", raw):
        raise ValueError("deployment environment must start with a letter/number and contain only letters, numbers, dot, dash, or underscore")
    return raw


def network_name(args: argparse.Namespace, rid: str) -> str:
    return f"{args.project_name}-soft-{rid}"


def container_name(args: argparse.Namespace, rid: str) -> str:
    return f"{args.project_name}-chain-{rid}"


def container_rpc_url(args: argparse.Namespace, rid: str) -> str:
    return args.container_rpc_url or f"http://{container_name(args, rid)}:8545"


def parse_offices(value: str | None) -> list[str]:
    if value is None or not str(value).strip():
        return [item["address"] for item in DEFAULT_OFFICE_KEYS]
    offices = [part.strip() for part in value.split(",") if part.strip()]
    if len(offices) != 4:
        raise ValueError("offices must contain exactly four addresses")
    for office in offices:
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", office):
            raise ValueError(f"invalid office address: {office}")
    return offices


def office_arg(offices: list[str]) -> str:
    return "[" + ",".join(offices) + "]"


def deployment_specs(args: argparse.Namespace) -> list[DeploymentSpec]:
    offices = parse_offices(args.offices)
    office_constructor_arg = office_arg(offices)
    selected = set(args.deploy or ["alpha-beta-lockout", "xlag-bridge-reserve"])

    specs: list[DeploymentSpec] = []
    if "alpha-beta-lockout" in selected:
        specs.append(
            DeploymentSpec(
                key="alpha-beta-lockout",
                target="AlphaBetaLockout.sol:AlphaBetaLockout",
                constructor_args=[office_constructor_arg],
            )
        )
    if "xlag-bridge-reserve" in selected:
        specs.append(
            DeploymentSpec(
                key="xlag-bridge-reserve",
                target="src/XLagBridgeReserve.sol:XLagBridgeReserve",
                constructor_args=[
                    office_constructor_arg,
                    str(args.max_payout_wei),
                    str(args.payout_delay_blocks),
                    str(args.reset_delay_blocks),
                ],
            )
        )
    return specs


def network_create_command(args: argparse.Namespace, rid: str) -> list[str]:
    return [docker_executable(), "network", "create", network_name(args, rid)]


def network_inspect_command(args: argparse.Namespace, rid: str) -> list[str]:
    return [docker_executable(), "network", "inspect", network_name(args, rid)]


def container_remove_command(args: argparse.Namespace, rid: str) -> list[str]:
    return [docker_executable(), "rm", "-f", container_name(args, rid)]


def anvil_command(args: argparse.Namespace, rid: str) -> list[str]:
    return [
        docker_executable(),
        "run",
        "--rm",
        "-d",
        "--name",
        container_name(args, rid),
        "--network",
        network_name(args, rid),
        "-p",
        f"{host_rpc_bind_host(args.host_rpc_url)}:{host_rpc_port(args.host_rpc_url)}:8545",
        "--entrypoint",
        "anvil",
        args.foundry_image,
        "--host",
        "0.0.0.0",
        "--port",
        "8545",
        "--chain-id",
        str(args.chain_id),
        "--accounts",
        str(args.accounts),
        "--balance",
        str(args.balance),
        "--mnemonic",
        str(args.mnemonic),
    ]


def host_rpc_endpoint(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"host rpc url must be http(s): {url}")
    if not parsed.hostname:
        raise ValueError(f"host rpc url must include a host: {url}")
    if parsed.port is None:
        raise ValueError(f"host rpc url must include a port: {url}")
    return parsed.hostname, parsed.port


def host_rpc_port(url: str) -> str:
    return str(host_rpc_endpoint(url)[1])


def host_rpc_bind_host(url: str) -> str:
    host, _port = host_rpc_endpoint(url)
    if host == "localhost":
        return "127.0.0.1"
    return host


def host_rpc_url_with_port(url: str, port: int) -> str:
    if port <= 0 or port > 65535:
        raise ValueError(f"invalid host RPC port: {port}")
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise ValueError(f"host rpc url must include a host: {url}")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        netloc = f"{auth}@{netloc}"
    netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path or "", parsed.query, parsed.fragment))


def docker_deploy_command(args: argparse.Namespace, spec: DeploymentSpec, contracts_root: Path, rid: str) -> list[str]:
    root = repo_root()
    return [
        docker_executable(),
        "run",
        "--rm",
        "--network",
        network_name(args, rid),
        "-v",
        f"{docker_mount_path(root)}:/workspace",
        "-w",
        "/workspace/contracts",
        "--entrypoint",
        "forge",
        args.foundry_image,
        "create",
        spec.target,
        "--rpc-url",
        container_rpc_url(args, rid),
        "--private-key",
        args.private_key,
        "--broadcast",
        "--json",
        "--constructor-args",
        *spec.constructor_args,
    ]


def display_command(command: list[str]) -> str:
    return " ".join(command)


def run_command(
    command: list[str],
    *,
    timeout_s: float | None = None,
    check: bool = True,
    echo: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
    )
    if echo and completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if echo and completed.stderr:
        print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n", file=sys.stderr)
    if check and completed.returncode != 0:
        raise RuntimeError(f"command failed: {display_command(command)}")
    return completed


def docker_ps_command() -> list[str]:
    return [docker_executable(), "ps", "-a", "--format", "{{.ID}}\t{{.Names}}\t{{.Ports}}"]


def parse_docker_port_owners(output: str) -> list[DockerPortOwner]:
    owners: list[DockerPortOwner] = []
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        container_id, name, ports = (part.strip() for part in parts)
        if container_id and name:
            owners.append(DockerPortOwner(container_id=container_id, name=name, ports=ports))
    return owners


def port_owner_publishes_host_port(owner: DockerPortOwner, port: int) -> bool:
    needle = f":{port}->"
    bare = f"{port}->"
    return any(segment.strip().startswith(bare) or needle in segment for segment in owner.ports.split(","))


def project_chain_prefix(args: argparse.Namespace) -> str:
    return f"{args.project_name}-chain-"


def is_project_chain_container(args: argparse.Namespace, owner: DockerPortOwner) -> bool:
    return owner.name.startswith(project_chain_prefix(args))


def docker_port_owners_for_host_port(args: argparse.Namespace, port: int) -> list[DockerPortOwner]:
    completed = run_command(docker_ps_command(), check=False, echo=False)
    if completed.returncode != 0:
        return []
    return [
        owner
        for owner in parse_docker_port_owners(completed.stdout or "")
        if port_owner_publishes_host_port(owner, port)
    ]


def host_port_accepts_tcp(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def host_port_is_free(args: argparse.Namespace, port: int) -> bool:
    host = host_rpc_bind_host(args.host_rpc_url)
    return not docker_port_owners_for_host_port(args, port) and not host_port_accepts_tcp(host, port)


def wait_for_host_port_free(args: argparse.Namespace, port: int, timeout_s: float = 5.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if host_port_is_free(args, port):
            return
        time.sleep(0.25)
    raise RuntimeError(f"host RPC port {port} did not become free")


def remove_docker_port_owner(owner: DockerPortOwner) -> None:
    log(f"Removing Docker container on host RPC port: {owner.name} ({owner.container_id})")
    run_command([docker_executable(), "rm", "-f", owner.container_id])


def set_next_free_host_rpc_port(args: argparse.Namespace, *, max_attempts: int = 100) -> None:
    _host, start_port = host_rpc_endpoint(args.host_rpc_url)
    for port in range(start_port, min(65535, start_port + max_attempts) + 1):
        if host_port_is_free(args, port):
            if port != start_port:
                log(f"Host RPC port {start_port} is occupied; using {port} instead.")
                args.host_rpc_url = host_rpc_url_with_port(args.host_rpc_url, port)
            return
    raise RuntimeError(f"could not find a free host RPC port starting at {start_port}")


def prepare_host_rpc_port(args: argparse.Namespace) -> None:
    port = int(host_rpc_port(args.host_rpc_url))

    if args.port_strategy == "auto":
        set_next_free_host_rpc_port(args)
        return

    owners = docker_port_owners_for_host_port(args, port)
    project_owners = [owner for owner in owners if is_project_chain_container(args, owner)]
    foreign_owners = [owner for owner in owners if not is_project_chain_container(args, owner)]

    if args.port_strategy == "fail" and owners:
        names = ", ".join(owner.name for owner in owners)
        raise RuntimeError(
            f"host RPC port {port} is already published by Docker container(s): {names}. "
            "Use --port-strategy replace-project, --port-strategy replace-any, --port-strategy auto, "
            "or choose --host-port."
        )

    owners_to_remove: list[DockerPortOwner] = []
    if args.port_strategy == "replace-project":
        owners_to_remove = project_owners
    elif args.port_strategy == "replace-any":
        owners_to_remove = owners

    for owner in owners_to_remove:
        remove_docker_port_owner(owner)

    if owners_to_remove:
        wait_for_host_port_free(args, port)

    remaining_foreign = [] if args.port_strategy == "replace-any" else foreign_owners
    if remaining_foreign:
        names = ", ".join(owner.name for owner in remaining_foreign)
        raise RuntimeError(
            f"host RPC port {port} is already published by non-project Docker container(s): {names}. "
            "Use --port-strategy replace-any to remove them, --port-strategy auto to pick another port, "
            "or choose --host-port."
        )

    host = host_rpc_bind_host(args.host_rpc_url)
    if host_port_accepts_tcp(host, port):
        raise RuntimeError(
            f"host RPC port {port} is already listening on {host}, but no removable project Docker "
            "container was found. Stop that process, use --port-strategy auto, or choose --host-port."
        )


def ensure_network(args: argparse.Namespace, rid: str) -> None:
    inspect = run_command(network_inspect_command(args, rid), check=False)
    if inspect.returncode == 0:
        log(f"Network already exists; reusing {network_name(args, rid)}")
        return
    run_command(network_create_command(args, rid))


def remove_existing_container(args: argparse.Namespace, rid: str) -> None:
    result = run_command(container_remove_command(args, rid), check=False)
    if result.returncode == 0:
        log(f"Removed existing soft-chain container {container_name(args, rid)}")


def wait_for_rpc(url: str, chain_id: int, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            actual = rpc(url, "eth_chainId")
            actual_id = int(str(actual), 16)
            if actual_id != chain_id:
                raise RuntimeError(f"wrong chain id: expected {chain_id}, got {actual_id}")
            return
        except Exception as exc:  # noqa: BLE001 - diagnostic retry loop
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"RPC did not become ready at {url}: {last_error}")


def rpc(url: str, method: str, params: list | None = None) -> object:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode("utf-8")
    request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=3) as response:
        data = json.loads(response.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError(data["error"])
    return data["result"]


def parse_deployment_address(output: str) -> str | None:
    try:
        payload = json.loads(output)
        if isinstance(payload, dict):
            value = payload.get("deployedTo") or payload.get("contractAddress")
            if isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
                return value
    except json.JSONDecodeError:
        pass
    match = re.search(r'"(?:deployedTo|contractAddress)"\s*:\s*"(0x[0-9a-fA-F]{40})"', output)
    if match:
        return match.group(1)
    match = re.search(r"Deployed to:\s*(0x[0-9a-fA-F]{40})", output)
    if match:
        return match.group(1)
    match = re.search(r"\b(0x[0-9a-fA-F]{40})\b", output)
    return match.group(1) if match else None


def parse_transaction_hash(output: str) -> str | None:
    try:
        payload = json.loads(output)
        if isinstance(payload, dict):
            value = payload.get("transactionHash") or payload.get("txHash")
            if isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
                return value
    except json.JSONDecodeError:
        pass
    match = re.search(r'"(?:transactionHash|txHash)"\s*:\s*"(0x[0-9a-fA-F]{64})"', output)
    if match:
        return match.group(1)
    match = re.search(r"\b(0x[0-9a-fA-F]{64})\b", output)
    return match.group(1) if match else None


def output_root(args: argparse.Namespace, root: Path) -> Path:
    return args.output_dir if args.output_dir is not None else root / "runtime" / "dev-chain"


def office_records(args: argparse.Namespace) -> list[dict[str, str | None]]:
    offices = parse_offices(args.offices)
    records: list[dict[str, str | None]] = []
    default_by_address = {item["address"].lower(): item for item in DEFAULT_OFFICE_KEYS}
    for index, address in enumerate(offices):
        known = default_by_address.get(address.lower(), {})
        records.append(
            {
                "office": f"O{index}",
                "title": str(known.get("title") or f"Office {index}"),
                "address": address,
                "private_key": known.get("private_key") if args.offices is None else None,
            }
        )
    return records


def deploy_payload(
    *,
    args: argparse.Namespace,
    rid: str,
    dry_run: bool,
    deployments: dict[str, dict],
) -> dict:
    return {
        "schema": DEPLOYMENT_SCHEMA,
        "environment": validate_environment_name(args.environment),
        "project_name": args.project_name,
        "run_id": rid,
        "dry_run": dry_run,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "chain": {
            "chain_id": args.chain_id,
            "host_rpc_url": args.host_rpc_url,
            "rpc_url": args.host_rpc_url,
            "container_rpc_url": container_rpc_url(args, rid),
            "network": network_name(args, rid),
            "container": container_name(args, rid),
            "accounts": args.accounts,
            "mnemonic": args.mnemonic,
        },
        "offices": office_records(args),
        "deployer": {
            "address": DEFAULT_OFFICE_KEYS[0]["address"] if args.private_key == DEFAULT_PRIVATE_KEY else None,
            "private_key": args.private_key,
        },
        "deployments": deployments,
    }


def public_deployment_payload(payload: dict) -> dict:
    """Return the sanitized deployment document consumed by the app.

    The legacy dev-chain payload keeps deterministic local keys so developer
    scripts can operate. The production-shaped deployment publication must not
    carry signing material because it is configuration/readback, not authority.
    """

    chain = dict(payload.get("chain") or {})
    return {
        "schema": DEPLOYMENT_SCHEMA,
        "environment": str(payload.get("environment") or DEFAULT_DEPLOYMENT_ENVIRONMENT),
        "run_id": payload.get("run_id"),
        "dry_run": bool(payload.get("dry_run")),
        "created_at": payload.get("created_at"),
        "chain": {
            "chain_id": chain.get("chain_id"),
            "rpc_url": chain.get("rpc_url") or chain.get("host_rpc_url"),
            "host_rpc_url": chain.get("host_rpc_url") or chain.get("rpc_url"),
            "container_rpc_url": chain.get("container_rpc_url"),
            "network": chain.get("network"),
            "container": chain.get("container"),
        },
        "contracts": public_contract_records(payload.get("deployments") or {}),
        "deployments": public_contract_records(payload.get("deployments") or {}),
        "offices": public_office_records(payload.get("offices") or []),
        "source": {
            "kind": "dev-chain-reset",
            "project_name": payload.get("project_name") or DEFAULT_PROJECT_NAME,
        },
    }


def public_contract_records(deployments: dict) -> dict:
    records: dict[str, dict] = {}
    for key, value in deployments.items():
        if not isinstance(value, dict):
            continue
        records[str(key)] = {
            "target": value.get("target"),
            "constructor_args": value.get("constructor_args"),
            "address": value.get("address"),
            "transaction_hash": value.get("transaction_hash"),
        }
    return records


def public_office_records(offices: list | tuple) -> list[dict]:
    records: list[dict] = []
    for index, value in enumerate(offices):
        if not isinstance(value, dict):
            continue
        records.append(
            {
                "office": value.get("office") or f"O{index}",
                "title": value.get("title"),
                "address": value.get("address"),
            }
        )
    return records


def env_payload(payload: dict) -> str:
    lines = [
        f"MAIN_COMPUTER_DEV_CHAIN_RUN_ID={payload['run_id']}",
        f"MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL={payload['chain']['host_rpc_url']}",
        f"MAIN_COMPUTER_ENERGY_CHAIN_ID={payload['chain']['chain_id']}",
        f"MAIN_COMPUTER_XLAG_CHAIN_ID={payload['chain']['chain_id']}",
    ]
    deployments = payload.get("deployments", {})
    xlag = deployments.get("xlag-bridge-reserve", {})
    alpha = deployments.get("alpha-beta-lockout", {})
    if xlag.get("address"):
        lines.append(f"MAIN_COMPUTER_XLAG_CONTRACT_ADDRESS={xlag['address']}")
    if alpha.get("address"):
        lines.append(f"MAIN_COMPUTER_ALPHA_BETA_LOCKOUT_CONTRACT_ADDRESS={alpha['address']}")
    for index, office in enumerate(payload.get("offices", [])):
        lines.append(f"MAIN_COMPUTER_DEV_OFFICE_{index}_ADDRESS={office['address']}")
        if office.get("private_key"):
            lines.append(f"MAIN_COMPUTER_DEV_OFFICE_{index}_PRIVATE_KEY={office['private_key']}")
    return "\n".join(lines) + "\n"


def planned_deployments(args: argparse.Namespace) -> dict[str, dict]:
    return {
        spec.key: {
            "target": spec.target,
            "constructor_args": spec.constructor_args,
            "address": None,
            "transaction_hash": None,
        }
        for spec in deployment_specs(args)
    }


def deployed_contracts(args: argparse.Namespace, rid: str) -> dict[str, dict]:
    contracts_root = repo_root() / "contracts"
    result: dict[str, dict] = {}
    for spec in deployment_specs(args):
        command = docker_deploy_command(args, spec, contracts_root, rid)
        completed = run_command(command, timeout_s=args.deploy_timeout_s)
        output = (completed.stdout or "") + "\n" + (completed.stderr or "")
        address = parse_deployment_address(output)
        tx_hash = parse_transaction_hash(output)
        if not address:
            raise RuntimeError(f"could not parse deployed address for {spec.key}")
        result[spec.key] = {
            "target": spec.target,
            "constructor_args": spec.constructor_args,
            "address": address,
            "transaction_hash": tx_hash,
        }
    return result


def validate_args(args: argparse.Namespace) -> None:
    if args.accounts <= 1:
        raise ValueError("--accounts must be greater than one for the local office key pool")
    parse_offices(args.offices)
    validate_environment_name(args.environment)
    host_rpc_endpoint(args.host_rpc_url)
    if args.host_port is not None:
        args.host_rpc_url = host_rpc_url_with_port(args.host_rpc_url, args.host_port)


def print_plan(args: argparse.Namespace, rid: str) -> None:
    root = repo_root()
    run_dir = output_root(args, root) / "runs" / rid
    log(f"Repository root: {root}")
    log(f"Run id: {rid}")
    log(f"Run directory: {run_dir}")
    log(f"Host RPC URL: {args.host_rpc_url}")
    log(f"Container RPC URL: {container_rpc_url(args, rid)}")
    log(f"Host port strategy: {args.port_strategy}")
    log()
    log("Planned commands:")
    log("$ " + display_command(network_inspect_command(args, rid)) + " || " + display_command(network_create_command(args, rid)))
    log("$ " + display_command(container_remove_command(args, rid)) + "  # ignored if container does not exist")
    log("$ " + display_command(anvil_command(args, rid)))
    if not args.no_deploy:
        for spec in deployment_specs(args):
            log("$ " + display_command(docker_deploy_command(args, spec, root / "contracts", rid)))



def deployment_output_root(args: argparse.Namespace, root: Path) -> Path:
    return args.deployment_output_dir if args.deployment_output_dir is not None else root / "runtime" / "deployments"


def write_outputs(args: argparse.Namespace, rid: str, payload: dict) -> None:
    root = repo_root()
    base = output_root(args, root)
    run_dir = base / "runs" / rid
    run_dir.mkdir(parents=True, exist_ok=True)

    deploy_json = run_dir / "deploy.json"
    deploy_env = run_dir / "deploy.env"
    latest_json = base / "latest.json"
    latest_env = base / "latest.env"

    json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    env_text = env_payload(payload)

    deploy_json.write_text(json_text, encoding="utf-8")
    deploy_env.write_text(env_text, encoding="utf-8")
    latest_json.parent.mkdir(parents=True, exist_ok=True)
    latest_json.write_text(json_text, encoding="utf-8")
    latest_env.write_text(env_text, encoding="utf-8")

    log(f"Wrote {deploy_json}")
    log(f"Wrote {deploy_env}")
    log(f"Wrote {latest_json}")
    log(f"Wrote {latest_env}")

    public_payload = public_deployment_payload(payload)
    public_json = json.dumps(public_payload, indent=2, sort_keys=True) + "\n"
    env_name = validate_environment_name(args.environment)
    deploy_base = deployment_output_root(args, root)
    env_base = deploy_base / env_name
    env_run_dir = env_base / "runs" / rid
    env_run_dir.mkdir(parents=True, exist_ok=True)

    env_deploy_json = env_run_dir / "deployment.json"
    env_latest_json = env_base / "latest.json"
    current_json = deploy_base / "current.json"
    env_deploy_json.write_text(public_json, encoding="utf-8")
    env_latest_json.write_text(public_json, encoding="utf-8")
    current_json.parent.mkdir(parents=True, exist_ok=True)
    current_json.write_text(public_json, encoding="utf-8")

    log(f"Wrote {env_deploy_json}")
    log(f"Wrote {env_latest_json}")
    log(f"Wrote {current_json}")



def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        validate_args(args)
        rid = resolved_run_id(args)
        root = repo_root()
        require_unlocked_production_state(
            root,
            output_root(args, root),
            deployment_output_root(args, root),
            action="run dev-chain reset",
        )
        if args.port_strategy == "auto" and not args.dry_run:
            prepare_host_rpc_port(args)
        print_plan(args, rid)

        if args.dry_run:
            payload = deploy_payload(args=args, rid=rid, dry_run=True, deployments=planned_deployments(args))
            write_outputs(args, rid, payload)
            return 0

        if not args.yes:
            log()
            log("Refusing to run without --yes. Use --dry-run to preview.")
            return 2

        ensure_network(args, rid)
        remove_existing_container(args, rid)
        if args.port_strategy != "auto":
            prepare_host_rpc_port(args)
        run_command(anvil_command(args, rid))
        wait_for_rpc(args.host_rpc_url, args.chain_id, args.wait_timeout_s)

        deployments: dict[str, dict]
        if args.no_deploy:
            deployments = {}
        else:
            deployments = deployed_contracts(args, rid)

        payload = deploy_payload(args=args, rid=rid, dry_run=False, deployments=deployments)
        write_outputs(args, rid, payload)
        return 0
    except Exception as exc:  # noqa: BLE001 - operator-facing script
        log()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
