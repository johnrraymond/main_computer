#!/usr/bin/env python3
"""Production-shaped contract deployment operator.

This tool is intentionally named for the mainnet workflow, but it can target
lower-risk dev/test environments so the exact operator path can be validated
before it is used against mainnet.  Mainnet remains fail-closed: no implicit
authority, no default Anvil identities, and no private key passed directly on
the outer command line.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


MAINNET_CHAIN_ID = 42424240
DEFAULT_DEV_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEFAULT_DEV_OFFICES = [
    "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
    "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
    "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
    "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
]
DEFAULT_DEV_OFFICES_ARG = ",".join(DEFAULT_DEV_OFFICES)
DEPLOY_CHOICES = ("alpha-beta-lockout", "xlag-bridge-reserve", "hub-credit-bridge-escrow")
TARGET_ENVIRONMENTS = ("dev", "testnet", "mainnet")


def repo_root() -> Path:
    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / "main_computer").is_dir() and (candidate / "tools" / "dev-chain-reset.py").is_file():
            return candidate
    return current.parent


def is_address(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", value) is not None


def is_private_key(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", value) is not None


def normalize_address(value: str) -> str:
    raw = str(value or "").strip()
    if not is_address(raw):
        raise ValueError(f"invalid address: {value}")
    return raw


def parse_offices(value: str) -> list[str]:
    raw = str(value or "").strip()
    if raw.lower() in {"default-anvil", "anvil-defaults"}:
        return list(DEFAULT_DEV_OFFICES)
    offices = [normalize_address(part) for part in raw.split(",") if part.strip()]
    if len(offices) != 4:
        raise ValueError("--offices must contain exactly four addresses, or default-anvil for explicit devnet practice")
    return offices


def offices_arg(offices: list[str]) -> str:
    return ",".join(offices)


def is_default_dev_private_key(private_key: str) -> bool:
    return str(private_key).lower() == DEFAULT_DEV_PRIVATE_KEY.lower()


def uses_default_dev_office(offices: list[str]) -> bool:
    defaults = {item.lower() for item in DEFAULT_DEV_OFFICES}
    return any(office.lower() in defaults for office in offices)


def validate_env_var_name(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("--private-key-env is required")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw):
        raise ValueError("--private-key-env must be a valid environment variable name")
    return raw


def private_key_from_env(name: str) -> str:
    env_name = validate_env_var_name(name)
    value = os.environ.get(env_name)
    if value is None or not value.strip():
        raise ValueError(f"--private-key-env {env_name} is not set or is empty")
    private_key = value.strip()
    if not is_private_key(private_key):
        raise ValueError(f"--private-key-env {env_name} does not contain a 32-byte hex private key")
    return private_key


def validate_deploy_args(args: argparse.Namespace) -> list[str]:
    if args.target_environment not in TARGET_ENVIRONMENTS:
        raise ValueError(f"--target-environment must be one of: {', '.join(TARGET_ENVIRONMENTS)}")

    if args.chain_id <= 0:
        raise ValueError("--chain-id must be greater than zero")

    if not str(args.rpc_url or "").strip():
        raise ValueError("--rpc-url is required")

    if not str(args.external_docker_network or "").strip():
        raise ValueError("--external-docker-network is required so the Foundry container can reach the target RPC")

    offices = parse_offices(args.offices)
    args.offices = offices_arg(offices)

    env_name = validate_env_var_name(args.private_key_env)
    deployer_key = private_key_from_env(env_name)

    is_mainnet_target = args.target_environment == "mainnet" or args.chain_id == MAINNET_CHAIN_ID
    if args.target_environment == "mainnet" and args.chain_id != MAINNET_CHAIN_ID:
        raise ValueError(f"mainnet target requires --chain-id {MAINNET_CHAIN_ID}")
    if args.target_environment != "mainnet" and args.chain_id == MAINNET_CHAIN_ID:
        raise ValueError(f"chain id {MAINNET_CHAIN_ID} is reserved for --target-environment mainnet")

    has_dev_authority = is_default_dev_private_key(deployer_key) or uses_default_dev_office(offices)
    if is_mainnet_target and has_dev_authority:
        raise ValueError("refusing mainnet deploy with default Anvil deployer key or office addresses")
    if not is_mainnet_target and has_dev_authority and not args.allow_dev_authority:
        raise ValueError(
            "dev/test validation with default Anvil authority requires --allow-dev-authority; "
            "mainnet never allows it"
        )

    if args.dry_run and args.yes:
        raise ValueError("choose either --dry-run or --yes, not both")
    if not args.dry_run and not args.yes:
        raise ValueError("use --dry-run to preview or --yes to execute")

    return offices


def dev_chain_reset_command(args: argparse.Namespace) -> list[str]:
    root = repo_root()
    command = [
        sys.executable,
        str(root / "tools" / "dev-chain-reset.py"),
        "--external-chain",
        "--environment",
        args.target_environment,
        "--chain-id",
        str(args.chain_id),
        "--host-rpc-url",
        args.rpc_url,
        "--container-rpc-url",
        args.container_rpc_url or args.rpc_url,
        "--external-docker-network",
        args.external_docker_network,
        "--source-kind",
        args.source_kind or f"mainnet-operator-{args.target_environment}-deploy",
        "--private-key-env",
        args.private_key_env,
        "--offices",
        args.offices,
        "--deployment-output-dir",
        str(args.deployment_output_dir),
        "--wait-timeout-s",
        str(args.wait_timeout_s),
        "--deploy-timeout-s",
        str(args.deploy_timeout_s),
    ]
    if args.run_id:
        command.extend(["--run-id", args.run_id])
    if args.external_chain_container:
        command.extend(["--external-chain-container", args.external_chain_container])
    for deploy in args.deploy:
        command.extend(["--deploy", deploy])
    if args.foundry_image:
        command.extend(["--foundry-image", args.foundry_image])
    if args.dry_run:
        command.append("--dry-run")
    if args.yes:
        command.append("--yes")
    return command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Mainnet-named contract deployment operator. Use --target-environment dev/testnet "
            "to validate the production-shaped path before applying it to mainnet."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    deploy = subparsers.add_parser("deploy-contracts", help="Deploy the root contract set through the operator path.")
    deploy.add_argument(
        "--target-environment",
        choices=TARGET_ENVIRONMENTS,
        required=True,
        help="Deployment environment to write under runtime/deployments/<target-environment>.",
    )
    deploy.add_argument("--rpc-url", required=True, help="Host RPC URL for the already-running target chain.")
    deploy.add_argument("--container-rpc-url", default=None, help="RPC URL reachable from the Foundry Docker container.")
    deploy.add_argument(
        "--external-docker-network",
        required=True,
        help="Docker network used by the target chain/RPC node so Foundry can reach it.",
    )
    deploy.add_argument("--external-chain-container", default=None, help="Optional RPC container name for metadata.")
    deploy.add_argument("--chain-id", type=int, required=True)
    deploy.add_argument(
        "--offices",
        required=True,
        help="Comma-separated Captain/First/Second/Third Officer addresses. Use default-anvil only with --allow-dev-authority on dev/test targets.",
    )
    deploy.add_argument("--private-key-env", required=True, help="Environment variable containing the deployer private key.")
    deploy.add_argument(
        "--allow-dev-authority",
        action="store_true",
        help="Permit default Anvil deployer/offices for dev/test validation only. Ignored as an error for mainnet.",
    )
    deploy.add_argument("--run-id", default=None)
    deploy.add_argument("--deployment-output-dir", type=Path, default=Path("runtime/deployments"))
    deploy.add_argument("--source-kind", default=None)
    deploy.add_argument("--foundry-image", default=None)
    deploy.add_argument("--deploy", choices=DEPLOY_CHOICES, action="append", default=[])
    deploy.add_argument("--wait-timeout-s", type=float, default=0.0)
    deploy.add_argument("--deploy-timeout-s", type=float, default=0.0)
    mode = deploy.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--yes", action="store_true")

    return parser


def display_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def deploy_contracts(args: argparse.Namespace) -> int:
    validate_deploy_args(args)
    command = dev_chain_reset_command(args)

    print("MAINNET-OPERATOR: deployment path validated")
    print(f"MAINNET-OPERATOR: target_environment={args.target_environment} chain_id={args.chain_id}")
    print("MAINNET-OPERATOR: delegated command:")
    print(display_command(command))

    if args.dry_run:
        print("MAINNET-OPERATOR: dry run only; delegated command was not executed")
        return 0

    completed = run_command(command)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "deploy-contracts":
            return deploy_contracts(args)
        parser.error(f"unknown command: {args.command}")
        return 2
    except Exception as exc:  # noqa: BLE001 - operator-facing script
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
