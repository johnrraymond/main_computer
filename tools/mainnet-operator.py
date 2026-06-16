#!/usr/bin/env python3
"""Mainnet-named operator entrypoint for intentional mainnet mutation.

This is a migration bridge: it keeps using the existing contract deployment
machinery, but moves the operator-facing command out of the dev-* namespace and
adds mainnet-specific authority checks before delegating.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


MAINNET_ENVIRONMENT = "mainnet"
MAINNET_CHAIN_ID = 42424240
DEFAULT_PROJECT_NAME = "main-computer-qbft-mainnet"
DEFAULT_SOURCE_KIND = "mainnet-operator-deploy"
DEFAULT_FOUNDRY_IMAGE = "ghcr.io/foundry-rs/foundry:latest"
DEFAULT_EXTERNAL_DOCKER_NETWORK = "bridge"
DEFAULT_EXTERNAL_CHAIN_CONTAINER = "mc-qbft-rpc"
DEFAULT_DEPLOYMENT_OUTPUT_DIR = Path("runtime") / "deployments"

DEFAULT_DEV_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEFAULT_DEV_OFFICES = {
    "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
    "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
    "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
    "0x90f79bf6eb2c4f870365e785982e1f101e93b906",
}


def ensure_repo_root_on_sys_path() -> Path:
    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / "main_computer").is_dir():
            text = str(candidate)
            if text not in sys.path:
                sys.path.insert(0, text)
            return candidate
    return current


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


def load_dev_chain_reset_module():
    path = repo_root() / "tools" / "dev-chain-reset.py"
    spec = importlib.util.spec_from_file_location("dev_chain_reset_for_mainnet_operator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def log(message: str = "") -> None:
    print(message, flush=True)


def is_address(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", value) is not None


def is_private_key(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", value) is not None


def parse_offices(value: str) -> list[str]:
    offices = [part.strip() for part in str(value or "").split(",") if part.strip()]
    if len(offices) != 4:
        raise ValueError("--offices must contain exactly four office addresses")
    invalid = [office for office in offices if not is_address(office)]
    if invalid:
        raise ValueError(f"invalid office address: {invalid[0]}")
    default_matches = [office for office in offices if office.lower() in DEFAULT_DEV_OFFICES]
    if default_matches:
        raise ValueError(
            "refusing mainnet deploy with default Anvil office address "
            f"{default_matches[0]}; provide rotated Captain through Third Officer addresses"
        )
    return offices


def validate_env_name(value: str) -> str:
    name = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError("--private-key-env must be a valid environment variable name")
    return name


def validate_rpc_url(value: str) -> str:
    rpc_url = str(value or "").strip()
    parsed = urlsplit(rpc_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("--rpc-url must be an absolute http(s) URL")
    return rpc_url


def resolve_private_key_env(env_name: str) -> str:
    private_key = str(os.environ.get(env_name, "") or "").strip()
    if not private_key:
        raise ValueError(f"environment variable {env_name} is not set or is empty")
    if not is_private_key(private_key):
        raise ValueError(f"environment variable {env_name} must contain a 0x-prefixed 32-byte private key")
    if private_key.lower() == DEFAULT_DEV_PRIVATE_KEY.lower():
        raise ValueError(f"refusing mainnet deploy with default Anvil deployer key from {env_name}")
    return private_key


def run_id_stamp() -> str:
    return f"mainnet-operator-{dt.datetime.now(dt.UTC).strftime('%Y%m%d%H%M%S')}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mainnet-named operator surface for intentional Main Computer mainnet mutations."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    deploy = subparsers.add_parser(
        "deploy-contracts",
        help="Deploy root contracts to the Main Computer mainnet through the existing deploy machinery.",
    )
    deploy.add_argument("--rpc-url", required=True, help="Mainnet RPC URL.")
    deploy.add_argument(
        "--container-rpc-url",
        default=None,
        help="RPC URL visible from the Foundry container. Defaults to --rpc-url.",
    )
    deploy.add_argument("--chain-id", type=int, default=MAINNET_CHAIN_ID)
    deploy.add_argument("--offices", required=True, help="Comma-separated Captain,First,Second,Third Officer addresses.")
    deploy.add_argument(
        "--private-key-env",
        required=True,
        help="Environment variable containing the deployer private key.",
    )
    deploy.add_argument("--run-id", default=None)
    deploy.add_argument("--project-name", default=DEFAULT_PROJECT_NAME)
    deploy.add_argument("--deployment-output-dir", type=Path, default=DEFAULT_DEPLOYMENT_OUTPUT_DIR)
    deploy.add_argument("--external-docker-network", default=DEFAULT_EXTERNAL_DOCKER_NETWORK)
    deploy.add_argument("--external-chain-container", default=DEFAULT_EXTERNAL_CHAIN_CONTAINER)
    deploy.add_argument("--foundry-image", default=DEFAULT_FOUNDRY_IMAGE)
    deploy.add_argument("--wait-timeout-s", type=float, default=0.0)
    deploy.add_argument("--deploy-timeout-s", type=float, default=0.0)
    deploy.add_argument("--yes", action="store_true", help="Actually run the delegated deployment.")
    deploy.add_argument("--dry-run", action="store_true", help="Print the delegated deployment command without running it.")

    return parser


def validate_deploy_contracts_args(args: argparse.Namespace) -> list[str]:
    if bool(args.yes) == bool(args.dry_run):
        raise ValueError("choose exactly one of --dry-run or --yes")
    if int(args.chain_id) != MAINNET_CHAIN_ID:
        raise ValueError(f"mainnet operator deploy-contracts requires --chain-id {MAINNET_CHAIN_ID}")
    args.rpc_url = validate_rpc_url(args.rpc_url)
    if args.container_rpc_url is not None:
        args.container_rpc_url = validate_rpc_url(args.container_rpc_url)
    env_name = validate_env_name(args.private_key_env)
    resolve_private_key_env(env_name)
    args.private_key_env = env_name
    if not str(args.external_docker_network or "").strip():
        raise ValueError("--external-docker-network is required")
    if not str(args.external_chain_container or "").strip():
        raise ValueError("--external-chain-container is required")
    return parse_offices(args.offices)


def dev_chain_reset_argv(args: argparse.Namespace, offices: list[str]) -> list[str]:
    run_id = str(args.run_id or run_id_stamp())
    argv = [
        "--external-chain",
        "--run-id",
        run_id,
        "--project-name",
        str(args.project_name),
        "--environment",
        MAINNET_ENVIRONMENT,
        "--source-kind",
        DEFAULT_SOURCE_KIND,
        "--chain-id",
        str(MAINNET_CHAIN_ID),
        "--host-rpc-url",
        str(args.rpc_url),
        "--container-rpc-url",
        str(args.container_rpc_url or args.rpc_url),
        "--wait-timeout-s",
        str(float(args.wait_timeout_s)),
        "--deploy-timeout-s",
        str(float(args.deploy_timeout_s)),
        "--external-docker-network",
        str(args.external_docker_network),
        "--external-chain-container",
        str(args.external_chain_container),
        "--deployment-output-dir",
        str(args.deployment_output_dir),
        "--foundry-image",
        str(args.foundry_image),
        "--offices",
        ",".join(offices),
        "--private-key-env",
        str(args.private_key_env),
    ]
    if args.dry_run:
        argv.insert(0, "--dry-run")
    else:
        argv.insert(0, "--yes")
    return argv


def display_delegated_command(argv: list[str]) -> str:
    return " ".join([sys.executable, str(repo_root() / "tools" / "dev-chain-reset.py"), *argv])


def deploy_contracts(args: argparse.Namespace) -> int:
    offices = validate_deploy_contracts_args(args)
    argv = dev_chain_reset_argv(args, offices)
    log("Mainnet deploy-contracts delegation:")
    log(display_delegated_command(argv))
    if args.dry_run:
        log()
        log("Dry run only; delegated command was not executed.")
        return 0
    module = load_dev_chain_reset_module()
    return int(module.main(argv))


def main(argv: list[str] | None = None) -> int:
    ensure_repo_root_on_sys_path()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "deploy-contracts":
            return deploy_contracts(args)
        parser.error(f"unsupported command {args.command!r}")
        return 2
    except Exception as exc:  # noqa: BLE001 - operator-facing script
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
