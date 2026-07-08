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
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any



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
KEYGEN_NETWORKS = ("testnet", "mainnet")
DEFAULT_PRIVATE_STATE_RELATIVE_PATH = Path("runtime") / "state" / "main_computer.private.yaml"
LOCAL_SECRETS_RELATIVE_PATH = Path("local.secrets")
DEFAULT_KEYGEN_ROLES = ("deployer", "captain", "o1", "o2", "o3", "hub_admin", "escrow_owner")
SECP256K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


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


def default_private_state_path() -> Path:
    return repo_root() / DEFAULT_PRIVATE_STATE_RELATIVE_PATH


def default_local_secrets_path() -> Path:
    return repo_root() / LOCAL_SECRETS_RELATIVE_PATH


def load_private_state(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ValueError("PyYAML is required to read the private state YAML") from exc

    if not path.exists():
        raise ValueError(f"private state file does not exist: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"private state must contain a YAML mapping: {path}")
    return data


def write_private_state(path: Path, state: dict[str, Any]) -> None:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ValueError("PyYAML is required to write the private state YAML") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


KECCAKF_ROUND_CONSTANTS = (
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
)
KECCAKF_ROTATION_OFFSETS = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def rotate_left_64(value: int, shift: int) -> int:
    shift %= 64
    return ((value << shift) | (value >> (64 - shift))) & 0xFFFFFFFFFFFFFFFF


def keccakf1600(state: list[int]) -> None:
    for round_constant in KECCAKF_ROUND_CONSTANTS:
        # Theta
        column = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        for x in range(5):
            delta = column[(x - 1) % 5] ^ rotate_left_64(column[(x + 1) % 5], 1)
            for y in range(5):
                state[x + 5 * y] ^= delta

        # Rho and Pi
        moved = [0] * 25
        for x in range(5):
            for y in range(5):
                moved[y + 5 * ((2 * x + 3 * y) % 5)] = rotate_left_64(
                    state[x + 5 * y],
                    KECCAKF_ROTATION_OFFSETS[x][y],
                )

        # Chi
        for y in range(5):
            row = [moved[x + 5 * y] for x in range(5)]
            for x in range(5):
                state[x + 5 * y] = row[x] ^ ((~row[(x + 1) % 5]) & row[(x + 2) % 5] & 0xFFFFFFFFFFFFFFFF)

        # Iota
        state[0] ^= round_constant


def keccak256(data: bytes) -> bytes:
    """Return Ethereum Keccak-256 without shelling out to openssl.

    Python's hashlib.sha3_256 implements the standardized SHA3 padding, not
    the legacy Keccak padding used by Ethereum addresses.
    """
    rate_bytes = 136
    state = [0] * 25
    offset = 0
    while offset + rate_bytes <= len(data):
        block = data[offset : offset + rate_bytes]
        for index in range(rate_bytes // 8):
            state[index] ^= int.from_bytes(block[index * 8 : (index + 1) * 8], "little")
        keccakf1600(state)
        offset += rate_bytes

    tail = bytearray(data[offset:])
    tail.append(0x01)
    if len(tail) < rate_bytes:
        tail.extend(b"\x00" * (rate_bytes - len(tail)))
    tail[-1] ^= 0x80
    for index in range(rate_bytes // 8):
        state[index] ^= int.from_bytes(tail[index * 8 : (index + 1) * 8], "little")
    keccakf1600(state)

    output = bytearray()
    while len(output) < 32:
        for index in range(rate_bytes // 8):
            output.extend(state[index].to_bytes(8, "little"))
            if len(output) >= 32:
                break
        if len(output) < 32:
            keccakf1600(state)
    return bytes(output[:32])


def checksum_address(address_hex: str) -> str:
    raw = address_hex.lower().removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]{40}", raw):
        raise ValueError(f"invalid address hex for checksum: {address_hex}")
    hashed = keccak256(raw.encode("ascii")).hex()
    return "0x" + "".join(char.upper() if int(hashed[index], 16) >= 8 else char for index, char in enumerate(raw))


def private_key_to_address(private_key: str) -> str:
    if not is_private_key(private_key):
        raise ValueError("private key must be a 32-byte hex string")
    secret_int = int(private_key[2:], 16)
    if secret_int <= 0 or secret_int >= SECP256K1_ORDER:
        raise ValueError("private key is outside the secp256k1 scalar range")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ValueError("cryptography is required to derive Ethereum addresses from private keys") from exc

    key = ec.derive_private_key(secret_int, ec.SECP256K1())
    public_bytes = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    digest = keccak256(public_bytes[1:])
    return checksum_address(digest[-20:].hex())


def generate_private_key() -> str:
    while True:
        raw = secrets.token_bytes(32)
        value = int.from_bytes(raw, byteorder="big")
        if 0 < value < SECP256K1_ORDER:
            return "0x" + raw.hex()


def parse_roles(value: str | None) -> list[str]:
    if value is None or not str(value).strip():
        return list(DEFAULT_KEYGEN_ROLES)
    roles = [part.strip() for part in str(value).split(",") if part.strip()]
    if not roles:
        raise ValueError("--roles must contain at least one role")
    invalid = [role for role in roles if role not in DEFAULT_KEYGEN_ROLES]
    if invalid:
        raise ValueError(f"unknown key role(s): {', '.join(invalid)}")
    return roles


def network_wallets(state: dict[str, Any], network: str) -> dict[str, Any]:
    networks = state.setdefault("networks", {})
    if not isinstance(networks, dict):
        raise ValueError("private state networks section must be a mapping")
    network_state = networks.setdefault(network, {})
    if not isinstance(network_state, dict):
        raise ValueError(f"networks.{network} must be a mapping")
    wallets = network_state.setdefault("wallets", {})
    if wallets is None:
        wallets = {}
        network_state["wallets"] = wallets
    if not isinstance(wallets, dict):
        raise ValueError(f"networks.{network}.wallets must be a mapping")
    return wallets


def prepare_network_keys(state: dict[str, Any], *, network: str, roles: list[str]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    wallets = network_wallets(state, network)
    generated: list[dict[str, str]] = []
    existing: list[dict[str, str]] = []
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    for role in roles:
        entry = wallets.setdefault(role, {})
        if entry is None:
            entry = {}
            wallets[role] = entry
        if not isinstance(entry, dict):
            raise ValueError(f"networks.{network}.wallets.{role} must be a mapping")

        address = entry.get("address")
        private_key = entry.get("private_key")

        if private_key:
            if not is_private_key(private_key):
                raise ValueError(f"networks.{network}.wallets.{role}.private_key is not a 32-byte hex private key")
            derived_address = private_key_to_address(private_key)
            if address:
                if not is_address(address):
                    raise ValueError(f"networks.{network}.wallets.{role}.address is not an Ethereum address")
                if str(address).lower() != derived_address.lower():
                    raise ValueError(
                        f"networks.{network}.wallets.{role} address does not match its private_key; "
                        "refusing to continue"
                    )
            entry["address"] = derived_address
            existing.append({"role": role, "address": derived_address})
            continue

        if address:
            if not is_address(address):
                raise ValueError(f"networks.{network}.wallets.{role}.address is not an Ethereum address")
            raise ValueError(
                f"networks.{network}.wallets.{role} already has an address but no private_key; "
                "import the matching private key or clear the address before generating"
            )

        generated_key = generate_private_key()
        generated_address = private_key_to_address(generated_key)
        entry["address"] = generated_address
        entry["private_key"] = generated_key
        entry.setdefault("source", f"mainnet-operator prepare-keys {network}")
        entry.setdefault("created_at", timestamp)
        generated.append({"role": role, "address": generated_address})

    return generated, existing



def prepared_private_key_values(state: dict[str, Any], *, network: str, roles: list[str]) -> list[str]:
    wallets = network_wallets(state, network)
    values: list[str] = []
    seen: set[str] = set()
    for role in roles:
        entry = wallets.get(role)
        if not isinstance(entry, dict):
            continue
        private_key = str(entry.get("private_key") or "").strip()
        if is_private_key(private_key) and private_key not in seen:
            seen.add(private_key)
            values.append(private_key)
    return values


def append_local_secrets(path: Path, values: list[str]) -> int:
    """Append missing secret values to local.secrets without printing them."""

    filtered: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or "\n" in text or "\r" in text:
            continue
        if text not in seen:
            seen.add(text)
            filtered.append(text)
    if not filtered:
        return 0

    existing_text = ""
    existing_values: set[str] = set()
    if path.exists():
        existing_text = path.read_text(encoding="utf-8")
        existing_values = {line.strip() for line in existing_text.splitlines() if line.strip()}

    missing = [value for value in filtered if value not in existing_values]
    if not missing:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        if existing_text and not existing_text.endswith("\n"):
            handle.write("\n")
        for value in missing:
            handle.write(value + "\n")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return len(missing)


def key_summary_path(network: str) -> Path:
    return repo_root() / "runtime" / "deployments" / network / "key-material" / f"{network}-wallets.public.json"


def write_public_key_summary(path: Path, *, network: str, generated: list[dict[str, str]], existing: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "network": network,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "generated_roles": generated,
        "existing_roles": existing,
        "private_keys_included": False,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prepare_keys(args: argparse.Namespace) -> int:
    if args.network not in KEYGEN_NETWORKS:
        raise ValueError(f"--network must be one of: {', '.join(KEYGEN_NETWORKS)}")
    roles = parse_roles(args.roles)
    state_path = args.state or default_private_state_path()

    state = load_private_state(state_path)
    generated, existing = prepare_network_keys(state, network=args.network, roles=roles)

    write_private_state(state_path, state)
    local_secrets_path = args.local_secrets_path or default_local_secrets_path()
    secret_count = append_local_secrets(
        local_secrets_path,
        prepared_private_key_values(state, network=args.network, roles=roles),
    )
    summary_path = args.summary_path or key_summary_path(args.network)
    write_public_key_summary(summary_path, network=args.network, generated=generated, existing=existing)

    print(f"MAINNET-OPERATOR: prepared keys for network={args.network}")
    print(f"MAINNET-OPERATOR: private state updated: {state_path}")
    print(f"MAINNET-OPERATOR: local.secrets updated: {local_secrets_path} ({secret_count} entries appended)")
    print(f"MAINNET-OPERATOR: public key summary written: {summary_path}")
    if generated:
        print("MAINNET-OPERATOR: generated roles:")
        for item in generated:
            print(f"  {item['role']}: {item['address']}")
    if existing:
        print("MAINNET-OPERATOR: existing roles verified:")
        for item in existing:
            print(f"  {item['role']}: {item['address']}")
    print("MAINNET-OPERATOR: private keys were not printed")
    return 0


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

    prepare = subparsers.add_parser(
        "prepare-keys",
        help="Generate missing pre-deploy wallet keys into the private state before a network deploy.",
    )
    prepare.add_argument("--network", choices=KEYGEN_NETWORKS, required=True)
    prepare.add_argument(
        "--state",
        type=Path,
        default=None,
        help="Private YAML path. Defaults to runtime/state/main_computer.private.yaml.",
    )
    prepare.add_argument(
        "--roles",
        default=None,
        help="Comma-separated key roles to prepare. Defaults to deployer,captain,o1,o2,o3,hub_admin,escrow_owner.",
    )
    prepare.add_argument(
        "--summary-path",
        type=Path,
        default=None,
        help="Address-only summary path. Defaults to runtime/deployments/<network>/key-material/<network>-wallets.public.json.",
    )
    prepare.add_argument(
        "--local-secrets-path",
        type=Path,
        default=None,
        help="Local secret denylist path. Defaults to local.secrets at the repository root.",
    )

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


def describe_file_not_found(exc: FileNotFoundError, *, command: str | None = None) -> list[str]:
    operation = f" during {command}" if command else ""
    lines = [f"ERROR: file or executable not found while running mainnet-operator{operation}"]
    filename = getattr(exc, "filename", None) or getattr(exc, "filename2", None)
    if filename:
        lines.append(f"  missing: {filename}")
    if getattr(exc, "errno", None) is not None:
        lines.append(f"  errno: {exc.errno}")
    if getattr(exc, "winerror", None) is not None:
        lines.append(f"  winerror: {exc.winerror}")
    if getattr(exc, "strerror", None):
        lines.append(f"  reason: {exc.strerror}")
    lines.append(f"  exception: {exc.__class__.__name__}: {exc}")
    return lines


def describe_operator_exception(exc: Exception, *, command: str | None = None) -> list[str]:
    if isinstance(exc, FileNotFoundError):
        return describe_file_not_found(exc, command=command)

    cause = getattr(exc, "__cause__", None)
    if isinstance(cause, FileNotFoundError):
        lines = [f"ERROR: {exc}"]
        lines.append("Caused by:")
        lines.extend(f"  {line}" for line in describe_file_not_found(cause, command=command))
        return lines

    context = getattr(exc, "__context__", None)
    if isinstance(context, FileNotFoundError):
        lines = [f"ERROR: {exc}"]
        lines.append("Context:")
        lines.extend(f"  {line}" for line in describe_file_not_found(context, command=command))
        return lines

    return [f"ERROR: {exc}"]


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
        if args.command == "prepare-keys":
            return prepare_keys(args)
        parser.error(f"unknown command: {args.command}")
        return 2
    except Exception as exc:  # noqa: BLE001 - operator-facing script
        for line in describe_operator_exception(exc, command=getattr(args, "command", None)):
            print(line, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
