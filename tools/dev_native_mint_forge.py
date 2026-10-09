#!/usr/bin/env python3
"""Run the local Forge specification for validator-only native minting.

This is deliberately local-dev only. It does not call Coolify, mutate genesis,
restart validators, or expose a mint JSON-RPC method. The Forge test uses
``vm.deal`` solely as the stand-in for the future validator-client world-state
mutation after a strictly-greater-than-two-thirds validator quorum authorizes it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

FOUNDRY_IMAGE = "ghcr.io/foundry-rs/foundry:latest"
WEI_PER_NATIVE = 10**18


def repo_root() -> Path:
    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / "contracts" / "foundry.toml").exists():
            return candidate
    raise RuntimeError("could not locate repository root containing contracts/foundry.toml")


def amount_native_to_wei(raw: str) -> int:
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"invalid native amount: {raw}") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError("native amount must be finite and greater than zero")
    wei = value * WEI_PER_NATIVE
    if wei != wei.to_integral_value():
        raise ValueError("native amount has more than 18 decimal places")
    return int(wei)


def docker_mount_path(path: Path) -> str:
    resolved = path.resolve()
    return resolved.as_posix() if os.name == "nt" else str(resolved)


def forge_command(root: Path, *, no_docker: bool) -> tuple[list[str], Path]:
    forge = shutil.which("forge")
    forge_args = [
        "test",
        "--match-contract",
        "MotherNativeMintRulesTest",
        "-vv",
    ]
    if forge:
        return [forge, *forge_args], root / "contracts"

    docker = shutil.which("docker")
    if docker and not no_docker:
        return (
            [
                docker,
                "run",
                "--rm",
                "-e",
                "MOTHER_DEV_MINT_AMOUNT_WEI",
                "-v",
                f"{docker_mount_path(root)}:/workspace",
                "-w",
                "/workspace/contracts",
                "--entrypoint",
                "forge",
                FOUNDRY_IMAGE,
                *forge_args,
            ],
            root,
        )

    raise RuntimeError("forge is not installed and Docker fallback is unavailable")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exercise the validator-only native-mint protocol model under local Forge."
    )
    parser.add_argument(
        "--amount-native",
        default="20",
        help="Native amount exercised by the successful mint test. Default: 20.",
    )
    parser.add_argument(
        "--no-docker",
        action="store_true",
        help="Require a local forge executable instead of using the Foundry Docker image fallback.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved command and amount without running Forge.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = repo_root()

    try:
        amount_wei = amount_native_to_wei(args.amount_native)
        command, cwd = forge_command(root, no_docker=args.no_docker)
    except (RuntimeError, ValueError) as exc:
        print(f"DEV_NATIVE_MINT_FORGE_ERROR: {exc}", file=sys.stderr)
        return 2

    print("DEV_NATIVE_MINT_FORGE")
    print(f"amount_native={args.amount_native}")
    print(f"amount_wei={amount_wei}")
    print("authority=current validator set")
    print("required_approvals=floor(2*N/3)+1 current validators (3/3 for N=3)")
    print("external_mint_rpc=none")
    print("native_state_transition=Forge vm.deal dev-only stand-in")
    print(f"cwd={cwd}")
    print("command=" + subprocess.list2cmdline(command))

    if args.dry_run:
        return 0

    env = os.environ.copy()
    env["MOTHER_DEV_MINT_AMOUNT_WEI"] = str(amount_wei)
    completed = subprocess.run(command, cwd=cwd, env=env, check=False)
    if completed.returncode != 0:
        print(
            f"DEV_NATIVE_MINT_FORGE_ERROR: Forge specification failed with exit={completed.returncode}",
            file=sys.stderr,
        )
        return completed.returncode or 1

    print("DEV_NATIVE_MINT_FORGE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
