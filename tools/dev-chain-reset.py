#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import secrets
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
DEFAULT_EXTERNAL_CHAIN_SOURCE_KIND = "external-chain-deploy"
DEPLOYMENT_SCHEMA = "main-computer.deployment.v1"
HUB_ADMIN_WALLET_SCHEMA = "main-computer.hub-admin-wallet.v1"
HUB_ADMIN_WALLET_FILENAME = "hub-admin-wallet.json"
SMOKE_CLIENT_WALLET_SCHEMA = "main-computer.smoke-client-wallet.v1"
SMOKE_CLIENT_WALLET_FILENAME = "smoke-client-wallet.json"
NODE_WALLETS_SCHEMA = "main-computer.dev-chain-node-wallets.v1"
NODE_WALLETS_FILENAME = "node-wallets"
PAYOUT_ADMIN_WALLETS_SCHEMA = "main-computer.dev-chain-payout-admin-wallets.v1"
PAYOUT_ADMIN_WALLETS_FILENAME = "payout-admin-wallets"
HUB_CREDIT_BRIDGE_ESCROW_KEY = "hub_credit_bridge_escrow"
HUB_CREDIT_BRIDGE_ESCROW_DEPLOY_CHOICE = "hub-credit-bridge-escrow"
HUB_ADMIN_PREVIEW_ADDRESS = "0x0000000000000000000000000000000000000a11"
SMOKE_CLIENT_PREVIEW_ADDRESS = "0x000000000000000000000000000000000000c11e"
DEFAULT_HUB_ADMIN_FUNDING_WEI = "10000000000000000000"
DEFAULT_SMOKE_CLIENT_FUNDING_WEI = "5000000000000000000"
DEFAULT_NODE_WALLET_FUNDING_WEI = "1000000000000000000"
DEFAULT_PAYOUT_ADMIN_FUNDING_WEI = "2000000000000000000"
DEFAULT_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEFAULT_DEPLOYER_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
# Constructor returns 32 zero bytes using PUSH0 (0x5f), so eth_estimateGas fails on pre-Shanghai chains.
PUSH0_CANARY_INITCODE = "0x5f60005260206000f3"
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
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class HubAdminWallet:
    path: Path
    address: str
    private_key: str | None
    source: str


@dataclass(frozen=True)
class SmokeClientWallet:
    path: Path
    address: str
    private_key: str | None
    source: str


@dataclass(frozen=True)
class GeneratedDevWallet:
    wallet_id: str
    role: str
    address: str
    private_key: str
    source: str = "generated-local-dev"


@dataclass(frozen=True)
class DockerPortOwner:
    container_id: str
    name: str
    ports: str


def log(message: str = "") -> None:
    print(message, flush=True)


def setup_log(message: str) -> None:
    log(f"SETUP: {message}")


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
    parser.add_argument("--service", default="soft-chain", help="Accepted for compatibility; not used by soft deploy.")
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
        "--external-chain",
        action="store_true",
        help="Deploy to an already-running chain instead of creating/replacing an Anvil container.",
    )
    parser.add_argument(
        "--external-docker-network",
        default=None,
        help="Docker network used by the already-running chain. Needed when Foundry runs in Docker.",
    )
    parser.add_argument(
        "--external-chain-container",
        default=None,
        help="Container name for the already-running chain/RPC node, recorded in deployment metadata.",
    )
    parser.add_argument(
        "--source-kind",
        default=None,
        help="Source kind recorded in the sanitized runtime deployment publication.",
    )
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
        choices=("alpha-beta-lockout", "xlag-bridge-reserve", HUB_CREDIT_BRIDGE_ESCROW_DEPLOY_CHOICE),
        action="append",
        default=[],
        help="Deploy only a selected root contract. May be repeated. Defaults to all root contracts.",
    )
    parser.add_argument(
        "--hub-admin-funding-wei",
        default=DEFAULT_HUB_ADMIN_FUNDING_WEI,
        help="Native wei sent from the deployer to the Hub admin wallet before escrow deployment.",
    )
    parser.add_argument(
        "--smoke-client-funding-wei",
        default=DEFAULT_SMOKE_CLIENT_FUNDING_WEI,
        help="Native wei sent from the deployer to the chain-scoped smoke client wallet for Hub client testing.",
    )
    parser.add_argument(
        "--skip-smoke-client-wallet",
        action="store_true",
        help="Do not create, fund, or publish the per-network smoke client wallet.",
    )
    parser.add_argument(
        "--run-scoped-wallets",
        action="store_true",
        help=(
            "Store generated Hub admin/smoke client wallets under the current run directory instead of "
            "reusing the chain-scoped dev wallet. Useful for smokes that need inspectable per-run identities."
        ),
    )
    parser.add_argument(
        "--node-wallet-count",
        type=int,
        default=0,
        help="Generate and fund this many per-run node recipient wallets in the deployment manifest.",
    )
    parser.add_argument(
        "--node-wallet-funding-wei",
        default=DEFAULT_NODE_WALLET_FUNDING_WEI,
        help="Native wei sent from the deployer to each generated node wallet.",
    )
    parser.add_argument(
        "--payout-admin-wallet-count",
        type=int,
        default=0,
        help="Generate and fund this many per-run payout admin wallets for later bridge/payout integration.",
    )
    parser.add_argument(
        "--payout-admin-funding-wei",
        default=DEFAULT_PAYOUT_ADMIN_FUNDING_WEI,
        help="Native wei sent from the deployer to each generated payout admin wallet.",
    )
    parser.add_argument("--max-payout-wei", default="1000000000000000000")
    parser.add_argument("--payout-delay-blocks", default="1")
    parser.add_argument("--reset-delay-blocks", default="1")
    parser.add_argument("--wait-timeout-s", type=float, default=0.0)
    parser.add_argument("--deploy-timeout-s", type=float, default=0.0)
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
    if getattr(args, "external_chain", False) and getattr(args, "external_docker_network", None):
        return str(args.external_docker_network)
    return f"{args.project_name}-soft-{rid}"


def container_name(args: argparse.Namespace, rid: str) -> str:
    if getattr(args, "external_chain", False) and getattr(args, "external_chain_container", None):
        return str(args.external_chain_container)
    return f"{args.project_name}-chain-{rid}"


def container_rpc_url(args: argparse.Namespace, rid: str) -> str:
    if args.container_rpc_url:
        return args.container_rpc_url
    if getattr(args, "external_chain", False):
        return args.host_rpc_url
    return f"http://{container_name(args, rid)}:8545"


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


def selected_deployments(args: argparse.Namespace) -> set[str]:
    return set(args.deploy or ["alpha-beta-lockout", "xlag-bridge-reserve", HUB_CREDIT_BRIDGE_ESCROW_DEPLOY_CHOICE])


def hub_admin_required(args: argparse.Namespace) -> bool:
    return (not args.no_deploy) and HUB_CREDIT_BRIDGE_ESCROW_DEPLOY_CHOICE in selected_deployments(args)


def deployment_specs(args: argparse.Namespace, hub_admin_address: str | None = None) -> list[DeploymentSpec]:
    offices = parse_offices(args.offices)
    office_constructor_arg = office_arg(offices)
    selected = selected_deployments(args)
    bridge_controller_address = hub_admin_address or HUB_ADMIN_PREVIEW_ADDRESS

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
    if HUB_CREDIT_BRIDGE_ESCROW_DEPLOY_CHOICE in selected:
        specs.append(
            DeploymentSpec(
                key=HUB_CREDIT_BRIDGE_ESCROW_KEY,
                target="src/HubCreditBridgeEscrow.sol:HubCreditBridgeEscrow",
                constructor_args=[bridge_controller_address],
                metadata={
                    "chain_id": args.chain_id,
                    "payment_asset": "native",
                    "approval_required": False,
                    "bridge_controller_address": bridge_controller_address,
                },
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


def docker_run_base(args: argparse.Namespace, *, use_network: bool = False, rid: str | None = None) -> list[str]:
    command = [docker_executable(), "run", "--rm"]
    if use_network:
        if not rid:
            raise ValueError("rid is required for networked Docker commands")
        command.extend(["--network", network_name(args, rid)])
    return command


def docker_deploy_command(args: argparse.Namespace, spec: DeploymentSpec, contracts_root: Path, rid: str) -> list[str]:
    root = repo_root()
    command = docker_run_base(args, use_network=True, rid=rid)
    command.extend(
        [
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
    )
    return command


def docker_cast_command(
    args: argparse.Namespace,
    cast_args: list[str],
    *,
    root: Path | None = None,
    rid: str | None = None,
    use_network: bool = False,
) -> list[str]:
    actual_root = root or repo_root()
    command = docker_run_base(args, use_network=use_network, rid=rid)
    command.extend(
        [
            "-v",
            f"{docker_mount_path(actual_root)}:/workspace",
            "-w",
            "/workspace/contracts",
            "--entrypoint",
            "cast",
            args.foundry_image,
            *cast_args,
        ]
    )
    return command


def hub_admin_fund_command(args: argparse.Namespace, rid: str, wallet: HubAdminWallet, root: Path) -> list[str]:
    return docker_cast_command(
        args,
        [
            "send",
            wallet.address,
            "--value",
            str(args.hub_admin_funding_wei),
            "--rpc-url",
            container_rpc_url(args, rid),
            "--private-key",
            args.private_key,
            "--json",
        ],
        root=root,
        rid=rid,
        use_network=True,
    )


def is_address(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", value) is not None


def is_private_key(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", value) is not None


def metadata_path(path: Path, root: Path) -> str:
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        return resolved.relative_to(root_resolved).as_posix()
    except ValueError:
        return str(resolved)


def hub_admin_wallet_filename(chain_id: int) -> str:
    return f"hub-admin-wallet-{int(chain_id)}.json"


def hub_admin_wallet_path(args: argparse.Namespace, root: Path, rid: str | None = None) -> Path:
    env_name = validate_environment_name(args.environment)
    base = deployment_output_root(args, root) / env_name
    if getattr(args, "run_scoped_wallets", False) and rid:
        base = base / "runs" / rid
    return base / hub_admin_wallet_filename(args.chain_id)


def legacy_hub_admin_wallet_path(args: argparse.Namespace, root: Path) -> Path:
    return deployment_output_root(args, root) / HUB_ADMIN_WALLET_FILENAME


def load_hub_admin_wallet(path: Path, chain_id: int) -> HubAdminWallet | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Hub admin wallet JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid Hub admin wallet record: {path}")
    if payload.get("schema") != HUB_ADMIN_WALLET_SCHEMA:
        raise ValueError(f"unexpected Hub admin wallet schema in {path}")
    wallet_chain_id = payload.get("chain_id")
    if int(wallet_chain_id) != int(chain_id):
        raise ValueError(f"Hub admin wallet chain_id {wallet_chain_id!r} does not match requested chain_id {chain_id}")
    address = payload.get("address")
    private_key = payload.get("private_key")
    if not is_address(address):
        raise ValueError(f"invalid Hub admin wallet address in {path}")
    if not is_private_key(private_key):
        raise ValueError(f"invalid Hub admin private key in {path}")
    return HubAdminWallet(path=path, address=str(address), private_key=str(private_key), source=str(payload.get("source") or "loaded-local-dev"))


def derive_address_for_private_key(args: argparse.Namespace, root: Path, private_key: str) -> str:
    completed = run_command(
        docker_cast_command(args, ["wallet", "address", "--private-key", private_key], root=root),
        check=True,
        echo=False,
        progress_label="derive wallet address with cast",
        progress_interval_s=5.0,
    )
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    match = re.search(r"0x[0-9a-fA-F]{40}", output)
    if not match:
        raise RuntimeError("could not derive Hub admin address from generated private key")
    return match.group(0)


def write_hub_admin_wallet(path: Path, *, chain_id: int, address: str, private_key: str) -> HubAdminWallet:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": HUB_ADMIN_WALLET_SCHEMA,
        "chain_id": chain_id,
        "address": address,
        "private_key": private_key,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "source": "generated-local-dev",
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp_path, 0o600)
    tmp_path.replace(path)
    if os.name != "nt":
        os.chmod(path, 0o600)
    return HubAdminWallet(path=path, address=address, private_key=private_key, source="generated-local-dev")


def create_hub_admin_wallet(args: argparse.Namespace, root: Path, path: Path) -> HubAdminWallet:
    setup_log("creating run-scoped Hub admin wallet private key")
    while True:
        private_key = "0x" + secrets.token_hex(32)
        if int(private_key, 16) != 0:
            break
    setup_log("deriving run-scoped Hub admin wallet address")
    address = derive_address_for_private_key(args, root, private_key)
    if address.lower() == str(DEFAULT_OFFICE_KEYS[0]["address"]).lower():
        raise RuntimeError("generated Hub admin wallet unexpectedly matched the deployer address")
    setup_log(f"derived run-scoped Hub admin wallet address={address}")
    wallet = write_hub_admin_wallet(path, chain_id=args.chain_id, address=address, private_key=private_key)
    log(f"Created Hub admin wallet: {metadata_path(path, root)} ({address})")
    return wallet


def resolve_hub_admin_wallet(
    args: argparse.Namespace,
    root: Path,
    *,
    create_missing: bool,
    rid: str | None = None,
) -> HubAdminWallet | None:
    if not hub_admin_required(args):
        return None

    path = hub_admin_wallet_path(args, root, rid)
    existing = load_hub_admin_wallet(path, args.chain_id)
    if existing is not None:
        log(f"Loaded Hub admin wallet: {metadata_path(path, root)} ({existing.address})")
        return existing

    # Older dev-chain resets stored a single wallet at runtime/deployments/hub-admin-wallet.json.
    # Do not let that dev wallet block testnet/mainnet deploys; reuse it only when it matches
    # the requested chain id, and migrate it into the chain-scoped location. Run-scoped smokes
    # intentionally skip this migration so every run gets a distinct Hub admin/bridge controller.
    legacy_path = legacy_hub_admin_wallet_path(args, root)
    if not getattr(args, "run_scoped_wallets", False) and legacy_path != path and legacy_path.exists():
        try:
            legacy_wallet = load_hub_admin_wallet(legacy_path, args.chain_id)
        except ValueError as exc:
            if "does not match requested chain_id" not in str(exc):
                raise
            log(f"Ignoring legacy Hub admin wallet for a different chain: {metadata_path(legacy_path, root)}")
        else:
            if legacy_wallet is not None:
                migrated = write_hub_admin_wallet(
                    path,
                    chain_id=args.chain_id,
                    address=legacy_wallet.address,
                    private_key=str(legacy_wallet.private_key),
                )
                log(
                    "Migrated legacy Hub admin wallet to chain-scoped path: "
                    f"{metadata_path(path, root)} ({migrated.address})"
                )
                return migrated

    if not create_missing:
        return HubAdminWallet(path=path, address=HUB_ADMIN_PREVIEW_ADDRESS, private_key=None, source="dry-run-preview")
    return create_hub_admin_wallet(args, root, path)



def smoke_client_wallet_filename(chain_id: int) -> str:
    return f"smoke-client-wallet-{int(chain_id)}.json"


def smoke_client_wallet_path(args: argparse.Namespace, root: Path, rid: str | None = None) -> Path:
    env_name = validate_environment_name(args.environment)
    base = deployment_output_root(args, root) / env_name
    if getattr(args, "run_scoped_wallets", False) and rid:
        base = base / "runs" / rid
    return base / smoke_client_wallet_filename(args.chain_id)


def load_smoke_client_wallet(path: Path, chain_id: int) -> SmokeClientWallet | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid smoke client wallet JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid smoke client wallet record: {path}")
    if payload.get("schema") != SMOKE_CLIENT_WALLET_SCHEMA:
        raise ValueError(f"unexpected smoke client wallet schema in {path}")
    wallet_chain_id = payload.get("chain_id")
    if int(wallet_chain_id) != int(chain_id):
        raise ValueError(f"smoke client wallet chain_id {wallet_chain_id!r} does not match requested chain_id {chain_id}")
    address = payload.get("address")
    private_key = payload.get("private_key")
    if not is_address(address):
        raise ValueError(f"invalid smoke client wallet address in {path}")
    if not is_private_key(private_key):
        raise ValueError(f"invalid smoke client private key in {path}")
    return SmokeClientWallet(path=path, address=str(address), private_key=str(private_key), source=str(payload.get("source") or "loaded-local-dev"))


def write_smoke_client_wallet(path: Path, *, chain_id: int, address: str, private_key: str) -> SmokeClientWallet:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SMOKE_CLIENT_WALLET_SCHEMA,
        "chain_id": chain_id,
        "address": address,
        "private_key": private_key,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "source": "generated-local-smoke-client",
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp_path, 0o600)
    tmp_path.replace(path)
    if os.name != "nt":
        os.chmod(path, 0o600)
    return SmokeClientWallet(path=path, address=address, private_key=private_key, source="generated-local-smoke-client")


def create_smoke_client_wallet(args: argparse.Namespace, root: Path, path: Path) -> SmokeClientWallet:
    setup_log("creating run-scoped requester/smoke-client wallet private key")
    while True:
        private_key = "0x" + secrets.token_hex(32)
        if int(private_key, 16) != 0:
            break
    setup_log("deriving run-scoped requester/smoke-client wallet address")
    address = derive_address_for_private_key(args, root, private_key)
    if address.lower() == str(DEFAULT_OFFICE_KEYS[0]["address"]).lower():
        raise RuntimeError("generated smoke client wallet unexpectedly matched the deployer address")
    setup_log(f"derived run-scoped requester/smoke-client wallet address={address}")
    wallet = write_smoke_client_wallet(path, chain_id=args.chain_id, address=address, private_key=private_key)
    log(f"Created smoke client wallet: {metadata_path(path, root)} ({address})")
    return wallet


def resolve_smoke_client_wallet(
    args: argparse.Namespace,
    root: Path,
    *,
    create_missing: bool,
    rid: str | None = None,
) -> SmokeClientWallet | None:
    if getattr(args, "skip_smoke_client_wallet", False):
        return None

    path = smoke_client_wallet_path(args, root, rid)
    existing = load_smoke_client_wallet(path, args.chain_id)
    if existing is not None:
        log(f"Loaded smoke client wallet: {metadata_path(path, root)} ({existing.address})")
        return existing

    if not create_missing:
        return SmokeClientWallet(path=path, address=SMOKE_CLIENT_PREVIEW_ADDRESS, private_key=None, source="dry-run-preview")
    return create_smoke_client_wallet(args, root, path)


def public_hub_admin_record(value: object, root: Path) -> dict | None:
    if not isinstance(value, dict):
        return None
    address = value.get("address")
    wallet_path = value.get("wallet_path")
    if not is_address(address):
        return None
    record = {
        "address": address,
        "wallet_path": str(wallet_path or ""),
    }
    if value.get("source"):
        record["source"] = value.get("source")
    if value.get("funding_wei"):
        record["funding_wei"] = str(value.get("funding_wei"))
    return record



def public_smoke_client_record(value: object, root: Path) -> dict | None:
    del root
    if not isinstance(value, dict):
        return None
    address = value.get("address")
    wallet_path = value.get("wallet_path")
    if not is_address(address):
        return None
    record = {
        "address": address,
        "wallet_path": str(wallet_path or ""),
    }
    if value.get("source"):
        record["source"] = value.get("source")
    if value.get("funding_wei"):
        record["funding_wei"] = str(value.get("funding_wei"))
    return record


def hub_admin_payload(wallet: HubAdminWallet | None, root: Path, args: argparse.Namespace) -> dict | None:
    if wallet is None:
        return None
    return {
        "address": wallet.address,
        "wallet_path": metadata_path(wallet.path, root),
        "source": wallet.source,
        "funding_wei": str(args.hub_admin_funding_wei),
    }



def smoke_client_payload(wallet: SmokeClientWallet | None, root: Path, args: argparse.Namespace) -> dict | None:
    if wallet is None:
        return None
    return {
        "address": wallet.address,
        "wallet_path": metadata_path(wallet.path, root),
        "source": wallet.source,
        "funding_wei": str(args.smoke_client_funding_wei),
    }


def smoke_client_fund_command(args: argparse.Namespace, rid: str, wallet: SmokeClientWallet, root: Path) -> list[str]:
    return docker_cast_command(
        args,
        [
            "send",
            wallet.address,
            "--value",
            str(args.smoke_client_funding_wei),
            "--rpc-url",
            container_rpc_url(args, rid),
            "--private-key",
            args.private_key,
            "--json",
        ],
        root=root,
        rid=rid,
        use_network=True,
    )


def fund_smoke_client_wallet(args: argparse.Namespace, rid: str, wallet: SmokeClientWallet | None, root: Path) -> None:
    if wallet is None or wallet.private_key is None:
        return
    log(f"Funding smoke client wallet {wallet.address} with {args.smoke_client_funding_wei} wei")
    run_command(
        smoke_client_fund_command(args, rid, wallet, root),
        timeout_s=args.deploy_timeout_s,
        progress_label=f"fund smoke client wallet {wallet.address}",
        progress_interval_s=5.0,
    )


def fund_hub_admin_wallet(args: argparse.Namespace, rid: str, wallet: HubAdminWallet | None, root: Path) -> None:
    if wallet is None or wallet.private_key is None:
        return
    log(f"Funding Hub admin wallet {wallet.address} with {args.hub_admin_funding_wei} wei")
    run_command(
        hub_admin_fund_command(args, rid, wallet, root),
        timeout_s=args.deploy_timeout_s,
        progress_label=f"fund Hub admin wallet {wallet.address}",
        progress_interval_s=5.0,
    )


def generated_wallets_filename(kind: str, chain_id: int) -> str:
    return f"{kind}-{int(chain_id)}.json"


def generated_wallets_path(args: argparse.Namespace, root: Path, rid: str, *, kind: str) -> Path:
    env_name = validate_environment_name(args.environment)
    return deployment_output_root(args, root) / env_name / "runs" / rid / generated_wallets_filename(kind, args.chain_id)


def generated_wallet_manifest_schema(kind: str) -> str:
    if kind == NODE_WALLETS_FILENAME:
        return NODE_WALLETS_SCHEMA
    if kind == PAYOUT_ADMIN_WALLETS_FILENAME:
        return PAYOUT_ADMIN_WALLETS_SCHEMA
    raise ValueError(f"unknown generated wallet kind: {kind}")


def create_generated_dev_wallets(
    args: argparse.Namespace,
    root: Path,
    *,
    kind: str,
    role: str,
    count: int,
) -> list[GeneratedDevWallet]:
    total = max(0, int(count or 0))
    setup_log(f"creating {total} run-scoped {role} wallet(s) for manifest kind={kind}")
    wallets: list[GeneratedDevWallet] = []
    for index in range(total):
        wallet_id = f"{role}-{index + 1:04d}"
        setup_log(f"generating {role} wallet {index + 1}/{total} wallet_id={wallet_id}")
        while True:
            private_key = "0x" + secrets.token_hex(32)
            if int(private_key, 16) != 0:
                break
        setup_log(f"deriving {role} wallet {index + 1}/{total} wallet_id={wallet_id}")
        address = derive_address_for_private_key(args, root, private_key)
        setup_log(f"derived {role} wallet {index + 1}/{total} wallet_id={wallet_id} address={address}")
        wallets.append(
            GeneratedDevWallet(
                wallet_id=wallet_id,
                role=role,
                address=address,
                private_key=private_key,
            )
        )
    setup_log(f"created {len(wallets)} run-scoped {role} wallet(s)")
    return wallets


def write_generated_wallet_manifest(
    path: Path,
    *,
    schema: str,
    chain_id: int,
    run_id: str,
    wallets: list[GeneratedDevWallet],
) -> None:
    setup_log(f"writing generated wallet manifest path={path} wallets={len(wallets)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": schema,
        "chain_id": chain_id,
        "run_id": run_id,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "wallets": [
            {
                "wallet_id": wallet.wallet_id,
                "role": wallet.role,
                "address": wallet.address,
                "private_key": wallet.private_key,
                "source": wallet.source,
            }
            for wallet in wallets
        ],
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp_path, 0o600)
    tmp_path.replace(path)
    if os.name != "nt":
        os.chmod(path, 0o600)


def resolve_generated_wallets(
    args: argparse.Namespace,
    root: Path,
    rid: str,
    *,
    kind: str,
    role: str,
    count: int,
    create_missing: bool,
) -> tuple[Path, list[GeneratedDevWallet]]:
    path = generated_wallets_path(args, root, rid, kind=kind)
    if path.exists():
        setup_log(f"loading existing {role} wallet manifest path={path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid generated wallet manifest JSON: {path}") from exc
        if not isinstance(payload, dict) or payload.get("schema") != generated_wallet_manifest_schema(kind):
            raise ValueError(f"unexpected generated wallet manifest schema in {path}")
        wallets: list[GeneratedDevWallet] = []
        for item in payload.get("wallets", []):
            if not isinstance(item, dict):
                continue
            address = item.get("address")
            private_key = item.get("private_key")
            if not is_address(address) or not is_private_key(private_key):
                raise ValueError(f"invalid generated wallet in {path}: {item}")
            wallets.append(
                GeneratedDevWallet(
                    wallet_id=str(item.get("wallet_id") or f"{role}-{len(wallets) + 1:04d}"),
                    role=str(item.get("role") or role),
                    address=str(address),
                    private_key=str(private_key),
                    source=str(item.get("source") or "generated-local-dev"),
                )
            )
        log(f"Loaded {len(wallets)} {role} wallet(s): {metadata_path(path, root)}")
        return path, wallets

    if not create_missing or int(count or 0) <= 0:
        setup_log(f"no {role} wallets requested or creation disabled count={int(count or 0)}")
        return path, []
    setup_log(f"creating missing {role} wallet manifest path={path} count={int(count or 0)}")
    wallets = create_generated_dev_wallets(args, root, kind=kind, role=role, count=count)
    write_generated_wallet_manifest(
        path,
        schema=generated_wallet_manifest_schema(kind),
        chain_id=args.chain_id,
        run_id=rid,
        wallets=wallets,
    )
    log(f"Created {len(wallets)} {role} wallet(s): {metadata_path(path, root)}")
    return path, wallets


def generated_wallets_payload(path: Path, wallets: list[GeneratedDevWallet], root: Path, *, funding_wei: str) -> dict:
    return {
        "wallet_path": metadata_path(path, root),
        "count": len(wallets),
        "funding_wei": str(funding_wei),
        "wallets": [
            {
                "wallet_id": wallet.wallet_id,
                "role": wallet.role,
                "address": wallet.address,
                "private_key": wallet.private_key,
                "source": wallet.source,
            }
            for wallet in wallets
        ],
    }


def public_generated_wallets_record(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    wallets = []
    for item in value.get("wallets", []):
        if not isinstance(item, dict):
            continue
        address = item.get("address")
        if not is_address(address):
            continue
        wallets.append(
            {
                "wallet_id": item.get("wallet_id"),
                "role": item.get("role"),
                "address": address,
                "source": item.get("source"),
            }
        )
    if not wallets and not value.get("wallet_path"):
        return None
    return {
        "wallet_path": str(value.get("wallet_path") or ""),
        "count": int(value.get("count", len(wallets)) or len(wallets)),
        "funding_wei": str(value.get("funding_wei", "")),
        "wallets": wallets,
    }


def generated_wallet_fund_command(
    args: argparse.Namespace,
    rid: str,
    wallet: GeneratedDevWallet,
    root: Path,
    *,
    funding_wei: str,
) -> list[str]:
    return docker_cast_command(
        args,
        [
            "send",
            wallet.address,
            "--value",
            str(funding_wei),
            "--rpc-url",
            container_rpc_url(args, rid),
            "--private-key",
            args.private_key,
            "--json",
        ],
        root=root,
        rid=rid,
        use_network=True,
    )


def fund_generated_wallets(
    args: argparse.Namespace,
    rid: str,
    wallets: list[GeneratedDevWallet],
    root: Path,
    *,
    funding_wei: str,
    label: str,
) -> None:
    if not wallets:
        return
    setup_log(f"funding {len(wallets)} {label} wallet(s) with {funding_wei} wei each")
    for wallet in wallets:
        log(f"Funding {label} wallet {wallet.wallet_id} {wallet.address} with {funding_wei} wei")
        run_command(
            generated_wallet_fund_command(args, rid, wallet, root, funding_wei=funding_wei),
            timeout_s=args.deploy_timeout_s,
            progress_label=f"fund {label} wallet {wallet.wallet_id} {wallet.address}",
            progress_interval_s=5.0,
        )


def display_command(command: list[str]) -> str:
    return " ".join(command)


def process_timeout_arg(timeout_s: float | None) -> float | None:
    """Return a subprocess timeout, treating zero/negative values as unbounded."""
    if timeout_s is None:
        return None
    timeout = float(timeout_s)
    if timeout <= 0:
        return None
    return timeout


def run_command(
    command: list[str],
    *,
    timeout_s: float | None = None,
    check: bool = True,
    echo: bool = True,
    progress_label: str | None = None,
    progress_interval_s: float = 10.0,
) -> subprocess.CompletedProcess[str]:
    timeout = process_timeout_arg(timeout_s)
    if not progress_label:
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    else:
        started_at = time.monotonic()
        deadline = None if timeout is None else started_at + timeout
        next_progress_at = started_at + max(1.0, float(progress_interval_s))
        process = subprocess.Popen(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout = ""
        stderr = ""
        while True:
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                process.kill()
                stdout, stderr = process.communicate()
                raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
            poll_timeout = 0.5
            if deadline is not None:
                poll_timeout = min(poll_timeout, max(0.01, deadline - now))
            try:
                stdout, stderr = process.communicate(timeout=poll_timeout)
                break
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                if now >= next_progress_at:
                    setup_log(f"still running: {progress_label} elapsed_s={int(now - started_at)}")
                    next_progress_at = now + max(1.0, float(progress_interval_s))
        completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    if echo and completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if echo and completed.stderr:
        print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n", file=sys.stderr)
    if check and completed.returncode != 0:
        raise RuntimeError(f"command failed: {display_command(command)}")
    return completed


def print_output(text: str | None, *, stream=None) -> None:
    if not text:
        return
    print(text, end="" if text.endswith("\n") else "\n", file=stream or sys.stdout)


def is_transient_forge_block_fetch_warning(line: str) -> bool:
    stripped = line.strip()
    return (
        " ERROR alloy_provider::blocks: failed to fetch block number=" in stripped
        and " err=error sending request for url " in stripped
    )


def split_transient_forge_warnings(stderr: str | None) -> tuple[list[str], list[str]]:
    transient: list[str] = []
    remaining: list[str] = []
    for line in (stderr or "").splitlines():
        if is_transient_forge_block_fetch_warning(line):
            transient.append(line)
        else:
            remaining.append(line)
    return transient, remaining


def code_byte_count(code: object) -> int:
    clean = str(code or "").removeprefix("0x")
    if not clean or clean == "0":
        return 0
    return max(len(clean) // 2, 0)


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


def timeout_deadline(timeout_s: float | None) -> float | None:
    if timeout_s is None:
        return None
    timeout = float(timeout_s)
    if timeout <= 0:
        return None
    return time.monotonic() + timeout


def deadline_expired(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def wait_for_rpc(url: str, chain_id: int, timeout_s: float) -> None:
    deadline = timeout_deadline(timeout_s)
    last_error: Exception | None = None
    next_status_at = time.monotonic() + 5.0
    while not deadline_expired(deadline):
        try:
            actual = rpc(url, "eth_chainId")
            actual_id = int(str(actual), 16)
            if actual_id != chain_id:
                raise RuntimeError(f"wrong chain id: expected {chain_id}, got {actual_id}")
            setup_log(f"RPC ready at {url} chain_id={chain_id}")
            return
        except Exception as exc:  # noqa: BLE001 - diagnostic retry loop
            last_error = exc
            now = time.monotonic()
            if now >= next_status_at:
                setup_log(f"still waiting for RPC at {url}; last_error={exc}")
                next_status_at = now + 5.0
            time.sleep(0.5)
    raise RuntimeError(f"RPC did not become ready at {url}: {last_error}")



def parse_rpc_int(value: object, *, field: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 16 if value.startswith("0x") else 10)
        except ValueError as exc:
            raise RuntimeError(f"{field} was not a valid integer: {value!r}") from exc
    raise RuntimeError(f"{field} was not a valid integer: {value!r}")


def latest_block_base_fee_per_gas(url: str) -> int:
    block = rpc(url, "eth_getBlockByNumber", ["latest", False])
    if not isinstance(block, dict):
        raise RuntimeError(f"eth_getBlockByNumber returned an unexpected payload: {block!r}")
    if "baseFeePerGas" not in block:
        raise RuntimeError(
            "external chain is not EIP-1559/London-capable: latest block has no baseFeePerGas. "
            "Regenerate or reconfigure the chain with London active at genesis instead of using legacy transactions."
        )
    return parse_rpc_int(block["baseFeePerGas"], field="latest block baseFeePerGas")


def require_eip1559_chain(url: str) -> int:
    base_fee = latest_block_base_fee_per_gas(url)
    if base_fee <= 0:
        raise RuntimeError(
            f"external chain reported baseFeePerGas={base_fee}; Main Computer test/main networks must use a real "
            "EIP-1559 base fee, not a zero-base-fee compatibility shortcut."
        )
    log(f"EIP-1559 preflight passed: latest baseFeePerGas={base_fee} wei")
    return base_fee


def require_push0_chain(url: str, *, from_address: str = DEFAULT_DEPLOYER_ADDRESS) -> int:
    """Require a Shanghai-or-newer execution fork by estimating a tiny PUSH0 contract creation.

    Solidity 0.8.20+ defaults to Shanghai bytecode and emits PUSH0. A chain can be
    EIP-1559/London-capable while still rejecting that opcode, so external deploys
    must prove both transaction-fee and opcode compatibility before publishing the
    golden deployment runtime.
    """

    try:
        estimate = rpc(
            url,
            "eth_estimateGas",
            [{"from": from_address, "data": PUSH0_CANARY_INITCODE}],
        )
    except RuntimeError as exc:
        message = str(exc)
        if "0x5f" in message or "PUSH0" in message.upper() or "invalid opcode" in message.lower():
            raise RuntimeError(
                "external chain is not Shanghai/PUSH0-capable: eth_estimateGas rejected a PUSH0 canary. "
                "Regenerate or reconfigure the chain with Shanghai active at genesis instead of compiling "
                "contracts for an older EVM."
            ) from exc
        raise
    gas = parse_rpc_int(estimate, field="PUSH0 canary gas estimate")
    log(f"Shanghai/PUSH0 preflight passed: PUSH0 canary estimate={gas} gas")
    return gas


def is_final_external_chain_preflight_error(exc: Exception) -> bool:
    message = str(exc)
    return (
        "external chain is not EIP-1559/London-capable" in message
        or "external chain reported baseFeePerGas=" in message
        or "external chain is not Shanghai/PUSH0-capable" in message
    )


def require_modern_external_chain(url: str, *, wait_timeout_s: float = 0.0) -> tuple[int, int]:
    """Wait until the external chain passes EIP-1559 and PUSH0 preflights.

    A slow QBFT network can answer one JSON-RPC request and then miss the next one
    while validators are still settling. Treat transport timeouts as "not ready
    yet" and retry indefinitely when wait_timeout_s <= 0.
    """

    deadline = timeout_deadline(wait_timeout_s)
    attempt = 0
    last_error: Exception | None = None
    while not deadline_expired(deadline):
        attempt += 1
        try:
            base_fee = require_eip1559_chain(url)
            push0_gas = require_push0_chain(url)
            return base_fee, push0_gas
        except Exception as exc:  # noqa: BLE001 - operator-facing retry loop
            if is_final_external_chain_preflight_error(exc):
                raise
            last_error = exc
            if attempt == 1 or attempt % 10 == 0:
                log(f"Waiting for external chain preflight via {url}: {exc}")
            time.sleep(1.0)
    raise RuntimeError(f"external chain did not pass preflight via {url}: {last_error}")


def rpc(url: str, method: str, params: list | None = None, *, timeout_s: float = 30.0) -> object:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode("utf-8")
    request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout_s) as response:
        data = json.loads(response.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError(data["error"])
    return data["result"]


def verify_deployed_contract_code(args: argparse.Namespace, rid: str, spec: DeploymentSpec, address: str) -> int:
    # Forge runs inside the Docker network and therefore uses container_rpc_url(...).
    # This Python process runs on the host, so post-deploy verification must use the
    # host-published RPC URL. Using the Docker-only container hostname from here can
    # stall on host DNS/connect retries and look like a hung deploy.
    del rid
    url = args.host_rpc_url
    deadline = timeout_deadline(args.wait_timeout_s)
    last_error: Exception | None = None
    log(f"Verifying {spec.key}.code via {url}")
    while True:
        try:
            code = rpc(url, "eth_getCode", [address, "latest"], timeout_s=1.0)
            byte_count = code_byte_count(code)
            if byte_count > 0:
                log(f"PASS: {spec.key}.code: code bytes={byte_count}")
                return byte_count
            last_error = RuntimeError("eth_getCode returned empty code")
        except Exception as exc:  # noqa: BLE001 - deployment verification retry loop
            last_error = exc
        if deadline_expired(deadline):
            break
        time.sleep(0.25)
    raise RuntimeError(
        f"{spec.key} deployed to {address}, but no contract code was readable at that address via {url}: {last_error}. "
        "Run `python .\\tools\\dev-chain-diagnosis.py --state .\\runtime\\deployments\\current.json` after fixing the RPC."
    )


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
    hub_admin: dict | None = None,
    smoke_client: dict | None = None,
    node_wallets: dict | None = None,
    payout_admin_wallets: dict | None = None,
) -> dict:
    chain_payload = {
        "chain_id": args.chain_id,
        "host_rpc_url": args.host_rpc_url,
        "rpc_url": args.host_rpc_url,
        "container_rpc_url": container_rpc_url(args, rid),
        "network": network_name(args, rid),
        "container": container_name(args, rid),
    }
    if not getattr(args, "external_chain", False):
        chain_payload["accounts"] = args.accounts
        chain_payload["mnemonic"] = args.mnemonic

    return {
        "schema": DEPLOYMENT_SCHEMA,
        "environment": validate_environment_name(args.environment),
        "project_name": args.project_name,
        "source_kind": args.source_kind or (DEFAULT_EXTERNAL_CHAIN_SOURCE_KIND if getattr(args, "external_chain", False) else "dev-chain-reset"),
        "run_id": rid,
        "dry_run": dry_run,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "chain": chain_payload,
        "offices": office_records(args),
        "deployer": {
            "address": DEFAULT_OFFICE_KEYS[0]["address"] if args.private_key == DEFAULT_PRIVATE_KEY else None,
            "private_key": args.private_key,
        },
        "hub_admin": hub_admin,
        "smoke_client": smoke_client,
        "node_wallets": node_wallets,
        "payout_admin_wallets": payout_admin_wallets,
        "deployments": deployments,
    }


def public_deployment_payload(payload: dict) -> dict:
    """Return the sanitized deployment document consumed by the app.

    The legacy dev-chain payload keeps deterministic local keys so developer
    scripts can operate. The production-shaped deployment publication must not
    carry signing material because it is configuration/readback, not authority.
    """

    chain = dict(payload.get("chain") or {})
    root = repo_root()
    public_hub_admin = public_hub_admin_record(payload.get("hub_admin"), root)
    public_smoke_client = public_smoke_client_record(payload.get("smoke_client"), root)
    public_node_wallets = public_generated_wallets_record(payload.get("node_wallets"))
    public_payout_admin_wallets = public_generated_wallets_record(payload.get("payout_admin_wallets"))
    public_payload = {
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
            "kind": payload.get("source_kind") or "dev-chain-reset",
            "project_name": payload.get("project_name") or DEFAULT_PROJECT_NAME,
        },
    }
    if public_hub_admin is not None:
        public_payload["hub_admin"] = public_hub_admin
    if public_smoke_client is not None:
        public_payload["smoke_client"] = public_smoke_client
    if public_node_wallets is not None:
        public_payload["node_wallets"] = public_node_wallets
    if public_payout_admin_wallets is not None:
        public_payload["payout_admin_wallets"] = public_payout_admin_wallets
    return public_payload


def public_contract_records(deployments: dict) -> dict:
    records: dict[str, dict] = {}
    for key, value in deployments.items():
        if not isinstance(value, dict):
            continue
        record = {
            "target": value.get("target"),
            "constructor_args": value.get("constructor_args"),
            "address": value.get("address"),
            "transaction_hash": value.get("transaction_hash"),
        }
        for optional_key in ("chain_id", "payment_asset", "approval_required", "bridge_controller_address"):
            if optional_key in value:
                record[optional_key] = value.get(optional_key)
        records[str(key)] = record
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
    hub_credit_bridge_escrow = deployments.get("hub_credit_bridge_escrow", {})
    if xlag.get("address"):
        lines.append(f"MAIN_COMPUTER_XLAG_CONTRACT_ADDRESS={xlag['address']}")
    if alpha.get("address"):
        lines.append(f"MAIN_COMPUTER_ALPHA_BETA_LOCKOUT_CONTRACT_ADDRESS={alpha['address']}")
    if hub_credit_bridge_escrow.get("address"):
        lines.append(f"MAIN_COMPUTER_HUB_CREDIT_BRIDGE_ESCROW_ADDRESS={hub_credit_bridge_escrow['address']}")
    smoke_client = payload.get("smoke_client") if isinstance(payload.get("smoke_client"), dict) else {}
    if smoke_client.get("address"):
        lines.append(f"MAIN_COMPUTER_SMOKE_CLIENT_ADDRESS={smoke_client['address']}")
    if smoke_client.get("wallet_path"):
        lines.append(f"MAIN_COMPUTER_SMOKE_CLIENT_WALLET_PATH={smoke_client['wallet_path']}")
    node_wallets = payload.get("node_wallets") if isinstance(payload.get("node_wallets"), dict) else {}
    for index, wallet in enumerate(node_wallets.get("wallets", []) if isinstance(node_wallets.get("wallets"), list) else []):
        if isinstance(wallet, dict) and wallet.get("address"):
            lines.append(f"MAIN_COMPUTER_DEV_NODE_{index}_ADDRESS={wallet['address']}")
    payout_admin_wallets = payload.get("payout_admin_wallets") if isinstance(payload.get("payout_admin_wallets"), dict) else {}
    for index, wallet in enumerate(payout_admin_wallets.get("wallets", []) if isinstance(payout_admin_wallets.get("wallets"), list) else []):
        if isinstance(wallet, dict) and wallet.get("address"):
            lines.append(f"MAIN_COMPUTER_DEV_PAYOUT_ADMIN_{index}_ADDRESS={wallet['address']}")
    for index, office in enumerate(payload.get("offices", [])):
        lines.append(f"MAIN_COMPUTER_DEV_OFFICE_{index}_ADDRESS={office['address']}")
        if office.get("private_key"):
            lines.append(f"MAIN_COMPUTER_DEV_OFFICE_{index}_PRIVATE_KEY={office['private_key']}")
    return "\n".join(lines) + "\n"


def deployment_record(spec: DeploymentSpec, *, address: str | None, transaction_hash: str | None) -> dict:
    record = {
        "target": spec.target,
        "constructor_args": spec.constructor_args,
        "address": address,
        "transaction_hash": transaction_hash,
    }
    if spec.metadata:
        record.update(spec.metadata)
    return record


def planned_deployments(args: argparse.Namespace, hub_admin_address: str | None = None) -> dict[str, dict]:
    return {
        spec.key: deployment_record(spec, address=None, transaction_hash=None)
        for spec in deployment_specs(args, hub_admin_address)
    }


def deployed_contracts(args: argparse.Namespace, rid: str, hub_admin_address: str | None = None) -> dict[str, dict]:
    contracts_root = repo_root() / "contracts"
    result: dict[str, dict] = {}
    for spec in deployment_specs(args, hub_admin_address):
        setup_log(f"deploying contract {spec.key} from {spec.target}")
        command = docker_deploy_command(args, spec, contracts_root, rid)
        completed = run_command(
            command,
            timeout_s=args.deploy_timeout_s,
            check=False,
            echo=False,
            progress_label=f"deploy contract {spec.key}",
            progress_interval_s=10.0,
        )
        if completed.returncode != 0:
            print_output(completed.stdout)
            print_output(completed.stderr, stream=sys.stderr)
            raise RuntimeError(f"command failed: {display_command(command)}")

        transient_warnings, stderr_lines = split_transient_forge_warnings(completed.stderr)
        print_output(completed.stdout)
        if stderr_lines:
            print_output("\n".join(stderr_lines), stream=sys.stderr)

        output = (completed.stdout or "") + "\n" + (completed.stderr or "")
        address = parse_deployment_address(output)
        tx_hash = parse_transaction_hash(output)
        if not address:
            raise RuntimeError(f"could not parse deployed address for {spec.key}")

        verify_deployed_contract_code(args, rid, spec, address)
        setup_log(f"deployed contract {spec.key} at {address}")
        if transient_warnings:
            log(
                f"NOTE: forge emitted {len(transient_warnings)} transient block-fetch warning(s) while deploying "
                f"{spec.key}; the deployed contract code was verified after the warning."
            )

        result[spec.key] = deployment_record(spec, address=address, transaction_hash=tx_hash)
    return result


def validate_args(args: argparse.Namespace) -> None:
    if args.accounts <= 1:
        raise ValueError("--accounts must be greater than one for the local office key pool")
    if int(str(args.hub_admin_funding_wei), 0) <= 0:
        raise ValueError("--hub-admin-funding-wei must be greater than zero")
    if not getattr(args, "skip_smoke_client_wallet", False) and int(str(args.smoke_client_funding_wei), 0) <= 0:
        raise ValueError("--smoke-client-funding-wei must be greater than zero")
    if int(getattr(args, "node_wallet_count", 0) or 0) < 0:
        raise ValueError("--node-wallet-count must be zero or greater")
    if int(getattr(args, "payout_admin_wallet_count", 0) or 0) < 0:
        raise ValueError("--payout-admin-wallet-count must be zero or greater")
    if int(getattr(args, "node_wallet_count", 0) or 0) > 0 and int(str(args.node_wallet_funding_wei), 0) <= 0:
        raise ValueError("--node-wallet-funding-wei must be greater than zero when node wallets are generated")
    if int(getattr(args, "payout_admin_wallet_count", 0) or 0) > 0 and int(str(args.payout_admin_funding_wei), 0) <= 0:
        raise ValueError("--payout-admin-funding-wei must be greater than zero when payout admin wallets are generated")
    parse_offices(args.offices)
    validate_environment_name(args.environment)
    host_rpc_endpoint(args.host_rpc_url)
    if args.host_port is not None:
        args.host_rpc_url = host_rpc_url_with_port(args.host_rpc_url, args.host_port)
    if getattr(args, "external_chain", False):
        if not str(args.container_rpc_url or "").strip():
            args.container_rpc_url = args.host_rpc_url
        if not str(args.external_docker_network or "").strip():
            raise ValueError("--external-docker-network is required with --external-chain")
        if not str(args.external_chain_container or "").strip():
            args.external_chain_container = None


def print_plan(
    args: argparse.Namespace,
    rid: str,
    hub_admin: HubAdminWallet | None = None,
    smoke_client: SmokeClientWallet | None = None,
    node_wallets: list[GeneratedDevWallet] | None = None,
    payout_admin_wallets: list[GeneratedDevWallet] | None = None,
) -> None:
    root = repo_root()
    node_wallets = list(node_wallets or [])
    payout_admin_wallets = list(payout_admin_wallets or [])
    run_dir = output_root(args, root) / "runs" / rid
    log(f"Repository root: {root}")
    log(f"Run id: {rid}")
    log(f"Run directory: {run_dir}")
    log(f"Host RPC URL: {args.host_rpc_url}")
    log(f"Container RPC URL: {container_rpc_url(args, rid)}")
    if getattr(args, "external_chain", False):
        log("Chain mode: external already-running chain")
        log(f"External Docker network: {network_name(args, rid)}")
        log(f"External chain container: {container_name(args, rid)}")
    else:
        log(f"Host port strategy: {args.port_strategy}")
    if hub_admin is not None:
        log(f"Hub admin wallet: {metadata_path(hub_admin.path, root)}")
        log(f"Hub admin address: {hub_admin.address}")
    if smoke_client is not None:
        log(f"Smoke client wallet: {metadata_path(smoke_client.path, root)}")
        log(f"Smoke client address: {smoke_client.address}")
    if node_wallets:
        log(f"Generated node wallets: {len(node_wallets)}")
        log(f"First node wallet: {node_wallets[0].address}")
    if payout_admin_wallets:
        log(f"Generated payout admin wallets: {len(payout_admin_wallets)}")
        log(f"First payout admin wallet: {payout_admin_wallets[0].address}")
    log()
    log("Planned commands:")
    if getattr(args, "external_chain", False):
        log("# using existing external chain/RPC; no Anvil container will be started or replaced")
    else:
        log("$ " + display_command(network_inspect_command(args, rid)) + " || " + display_command(network_create_command(args, rid)))
        log("$ " + display_command(container_remove_command(args, rid)) + "  # ignored if container does not exist")
        log("$ " + display_command(anvil_command(args, rid)))
    if not args.no_deploy:
        if hub_admin is not None:
            log("$ " + display_command(hub_admin_fund_command(args, rid, hub_admin, root)))
        if smoke_client is not None:
            log("$ " + display_command(smoke_client_fund_command(args, rid, smoke_client, root)))
        for wallet in node_wallets:
            log("$ " + display_command(generated_wallet_fund_command(args, rid, wallet, root, funding_wei=args.node_wallet_funding_wei)))
        for wallet in payout_admin_wallets:
            log("$ " + display_command(generated_wallet_fund_command(args, rid, wallet, root, funding_wei=args.payout_admin_funding_wei)))
        for spec in deployment_specs(args, hub_admin.address if hub_admin else None):
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
        setup_log(f"starting dev-chain reset for run_id={rid}")
        require_unlocked_production_state(
            root,
            output_root(args, root),
            deployment_output_root(args, root),
            action="run dev-chain reset",
        )
        if args.port_strategy == "auto" and not args.dry_run and not getattr(args, "external_chain", False):
            setup_log(f"choosing an available host RPC port from {args.host_rpc_url}")
            prepare_host_rpc_port(args)
            setup_log(f"selected host RPC URL {args.host_rpc_url}")

        create_admin_wallet = bool(args.yes and not args.dry_run)
        setup_log("resolving run-scoped Hub admin wallet")
        hub_admin = resolve_hub_admin_wallet(args, root, create_missing=create_admin_wallet, rid=rid)
        setup_log("resolving run-scoped requester/smoke-client wallet")
        smoke_client = resolve_smoke_client_wallet(args, root, create_missing=create_admin_wallet, rid=rid)
        setup_log(f"resolving run-scoped node wallet manifest count={args.node_wallet_count}")
        node_wallets_path, node_wallets = resolve_generated_wallets(
            args,
            root,
            rid,
            kind=NODE_WALLETS_FILENAME,
            role="node",
            count=args.node_wallet_count,
            create_missing=create_admin_wallet,
        )
        setup_log(f"resolving run-scoped payout admin wallet manifest count={args.payout_admin_wallet_count}")
        payout_admin_wallets_path, payout_admin_wallets = resolve_generated_wallets(
            args,
            root,
            rid,
            kind=PAYOUT_ADMIN_WALLETS_FILENAME,
            role="payout-admin",
            count=args.payout_admin_wallet_count,
            create_missing=create_admin_wallet,
        )
        print_plan(args, rid, hub_admin, smoke_client, node_wallets, payout_admin_wallets)
        setup_log(
            "plan ready: "
            f"node_wallets={len(node_wallets)} payout_admin_wallets={len(payout_admin_wallets)} "
            f"external_chain={bool(getattr(args, 'external_chain', False))} no_deploy={bool(args.no_deploy)}"
        )

        if args.dry_run:
            setup_log("dry run only; writing preview deployment metadata")
            payload = deploy_payload(
                args=args,
                rid=rid,
                dry_run=True,
                deployments=planned_deployments(args, hub_admin.address if hub_admin else None),
                hub_admin=hub_admin_payload(hub_admin, root, args),
                smoke_client=smoke_client_payload(smoke_client, root, args),
                node_wallets=generated_wallets_payload(node_wallets_path, node_wallets, root, funding_wei=args.node_wallet_funding_wei),
                payout_admin_wallets=generated_wallets_payload(
                    payout_admin_wallets_path,
                    payout_admin_wallets,
                    root,
                    funding_wei=args.payout_admin_funding_wei,
                ),
            )
            write_outputs(args, rid, payload)
            return 0

        if not args.yes:
            log()
            log("Refusing to run without --yes. Use --dry-run to preview.")
            return 2

        if getattr(args, "external_chain", False):
            setup_log(f"checking external chain RPC at {args.host_rpc_url}")
            wait_for_rpc(args.host_rpc_url, args.chain_id, args.wait_timeout_s)
            setup_log("checking external chain fork compatibility")
            require_modern_external_chain(args.host_rpc_url, wait_timeout_s=args.wait_timeout_s)
        else:
            setup_log(f"ensuring Docker network {network_name(args, rid)}")
            ensure_network(args, rid)
            setup_log(f"replacing dev-chain container {container_name(args, rid)} if it already exists")
            remove_existing_container(args, rid)
            if args.port_strategy != "auto":
                setup_log(f"preparing requested host RPC port for {args.host_rpc_url}")
                prepare_host_rpc_port(args)
            setup_log(f"starting local Anvil container {container_name(args, rid)}")
            run_command(anvil_command(args, rid))
            setup_log(f"waiting for local dev-chain RPC at {args.host_rpc_url}")
            wait_for_rpc(args.host_rpc_url, args.chain_id, args.wait_timeout_s)

        deployments: dict[str, dict]
        if args.no_deploy:
            setup_log("contract deployment disabled with --no-deploy")
            deployments = {}
        else:
            setup_log("funding hub admin and smoke requester wallets")
            fund_hub_admin_wallet(args, rid, hub_admin, root)
            fund_smoke_client_wallet(args, rid, smoke_client, root)
            fund_generated_wallets(
                args,
                rid,
                node_wallets,
                root,
                funding_wei=args.node_wallet_funding_wei,
                label="node",
            )
            fund_generated_wallets(
                args,
                rid,
                payout_admin_wallets,
                root,
                funding_wei=args.payout_admin_funding_wei,
                label="payout admin",
            )
            setup_log("deploying dev-chain contracts")
            deployments = deployed_contracts(args, rid, hub_admin.address if hub_admin else None)

        setup_log("writing run deployment metadata and current dev-chain pointer")
        payload = deploy_payload(
            args=args,
            rid=rid,
            dry_run=False,
            deployments=deployments,
            hub_admin=hub_admin_payload(hub_admin, root, args),
            smoke_client=smoke_client_payload(smoke_client, root, args),
            node_wallets=generated_wallets_payload(node_wallets_path, node_wallets, root, funding_wei=args.node_wallet_funding_wei),
            payout_admin_wallets=generated_wallets_payload(
                payout_admin_wallets_path,
                payout_admin_wallets,
                root,
                funding_wei=args.payout_admin_funding_wei,
            ),
        )
        write_outputs(args, rid, payload)
        setup_log(f"dev-chain reset complete for run_id={rid}")
        return 0
    except Exception as exc:  # noqa: BLE001 - operator-facing script
        log()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
