"""Run the existing contract operator with credentials from verified Mother state.

Hub Control prints this command instead of an inline YAML/key-extraction shell.
Private keys remain confined to this process and its operator child environment.
This module does not grant Coolify mutation authority or alter Mother's state.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit

from tools.mother.common.ethereum_identity import is_address, is_private_key, private_key_to_address
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import read_private_state

ROLES = ("deployer", "captain", "o1", "o2", "o3")
NETWORKS = ("mainnet", "testnet")


class MotherContractDeployError(Exception):
    """A non-secret operator-facing error, never containing a private key."""


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _verified_wallets(*, network: str, state_root: Path) -> tuple[int, str, list[str], int]:
    operation = OperationIdentity(
        operation_id=f"mother-contract-deploy-{network}-inspect",
        request_id="mother-contract-deploy",
        network=network,
        operation_kind="MOTHER-OP-DIAGNOSE",
    )
    paths = MotherPaths(runtime_state_root=state_root).resolve_private_state_paths()
    # Never load the identity YAML directly. This checks Mother metadata and
    # recovery closure first; only the verified canonical object is consumed.
    try:
        current = read_private_state(paths, operation=operation)
        document = json.loads(current.canonical_object_bytes)
    except Exception as exc:
        raise MotherContractDeployError(
            f"verified Mother identity unavailable for {network} ({type(exc).__name__})"
        ) from None
    networks = document.get("networks") if isinstance(document, dict) else None
    net = networks.get(network) if isinstance(networks, dict) else None
    if not isinstance(net, dict):
        raise MotherContractDeployError(f"Mother has no verified {network} network")
    chain_id = net.get("chain_id")
    if type(chain_id) is not int or chain_id <= 0:
        raise MotherContractDeployError(f"Mother {network} has no valid chain ID")
    wallets = net.get("wallets")
    if not isinstance(wallets, dict):
        raise MotherContractDeployError(f"Mother {network} has no verified wallet records")
    addresses: list[str] = []
    deployer_key = ""
    for role in ROLES:
        entry = wallets.get(role)
        if not isinstance(entry, dict):
            raise MotherContractDeployError(f"Mother {network} wallet {role} is missing")
        address = entry.get("address")
        private_key = entry.get("private_key")
        if not is_address(address) or int(address[2:], 16) == 0:
            raise MotherContractDeployError(f"Mother {network} wallet {role} has an invalid address")
        if not is_private_key(private_key):
            raise MotherContractDeployError(f"Mother {network} wallet {role} has no valid signing key")
        try:
            matches = private_key_to_address(private_key).lower() == address.lower()
        except Exception:
            matches = False
        if not matches:
            raise MotherContractDeployError(f"Mother {network} wallet {role} key/address mismatch")
        if role == "deployer":
            deployer_key = private_key
        else:
            addresses.append(address)
    if len({address.lower() for address in [*addresses, wallets["deployer"]["address"]]}) != 5:
        raise MotherContractDeployError(f"Mother {network} wallet roles are not distinct")
    return chain_id, deployer_key, addresses, current.binding.generation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", choices=NETWORKS, required=True)
    parser.add_argument("--chain-id", type=int, required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--container-rpc-url", default="")
    parser.add_argument("--external-docker-network", default="bridge")
    parser.add_argument(
        "--runtime-state-root", type=Path, default=None,
        help="Mother runtime/state root; defaults to this repository's runtime/state",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--yes", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    rpc = str(args.rpc_url).strip()
    parsed = urlsplit(rpc)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise MotherContractDeployError("--rpc-url must be an http(s) URL without embedded credentials")
    container_rpc = args.container_rpc_url.strip() or rpc
    parsed_container = urlsplit(container_rpc)
    if (parsed_container.scheme not in ("http", "https") or not parsed_container.hostname
            or parsed_container.username or parsed_container.password):
        raise MotherContractDeployError("--container-rpc-url must be an http(s) URL without embedded credentials")
    docker_network = str(args.external_docker_network).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", docker_network):
        raise MotherContractDeployError("--external-docker-network must be a Docker network name")
    root = _root()
    state_root = args.runtime_state_root or root / "runtime" / "state"
    chain_id, deployer_key, offices, generation = _verified_wallets(network=args.network, state_root=state_root)
    if chain_id != args.chain_id:
        raise MotherContractDeployError(
            f"Mother {args.network} chain ID {chain_id} differs from requested chain ID {args.chain_id}"
        )
    operator = root / "tools" / "mainnet-operator.py"
    if not operator.is_file():
        raise MotherContractDeployError("contract deployment operator is missing")
    key_env = f"{args.network.upper()}_DEPLOYER_PRIVATE_KEY"
    command = [
        sys.executable, str(operator), "deploy-contracts",
        "--target-environment", args.network,
        "--chain-id", str(chain_id),
        "--rpc-url", rpc,
        "--container-rpc-url", container_rpc,
        "--external-docker-network", docker_network,
        "--private-key-env", key_env,
        "--offices", ",".join(offices),
        "--yes" if args.yes else "--dry-run",
    ]
    # The signing key is not a command-line argument or printed text.
    # It exists only in the child process environment for the existing operator.
    child_env = os.environ.copy()
    child_env[key_env] = deployer_key
    print(f"MOTHER-CONTRACT-DEPLOY: verified network={args.network} generation={generation} chain_id={chain_id}")
    result = subprocess.run(command, cwd=root, env=child_env, check=False)
    return int(result.returncode)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return run(args)
    except MotherContractDeployError as exc:
        print(f"MOTHER-CONTRACT-DEPLOY: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
