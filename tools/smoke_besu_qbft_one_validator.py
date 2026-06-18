#!/usr/bin/env python3
"""Run and monitor a local four-validator plus non-validator RPC Besu QBFT smoke lab.

This is intentionally self-contained enough to operate as the local QBFT
testnet harness. It proves that local Besu containers can generate a QBFT
genesis, start four validator nodes from it, peer over a private Docker network,
expose validator JSON-RPC for inspection, start one non-validator RPC node for
hub/tool traffic, produce blocks, and publish the same deployment-runtime shape
as the Anvil dev-chain reset path when contracts are deployed.

Default container names:
  smoke-besu-qbft-genesis
  smoke-besu-qbft-validator-1
  smoke-besu-qbft-validator-2
  smoke-besu-qbft-validator-3
  smoke-besu-qbft-validator-4
  smoke-besu-qbft-rpc

Default Docker network:
  smoke-besu-qbft-network

Default runtime directory:
  runtime/smoke-besu-qbft-four-validators

Default JSON-RPC host ports:
  validator inspection RPC: 30001, 30002, 30003, 30004
  non-validator RPC node: http://127.0.0.1:30010

Common workflows:
  python tools/smoke_besu_qbft_one_validator.py up
  python tools/smoke_besu_qbft_one_validator.py monitor
  python tools/smoke_besu_qbft_one_validator.py check
  python tools/smoke_besu_qbft_one_validator.py deploy
  python tools/smoke_besu_qbft_one_validator.py down

The default command is "up", so this also starts the lab and leaves it running:
  python tools/smoke_besu_qbft_one_validator.py

For old disposable smoke-test behavior:
  python tools/smoke_besu_qbft_one_validator.py smoke
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import ipaddress
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


GENESIS_CONTAINER = "smoke-besu-qbft-genesis"
VALIDATOR_COUNT = 4
VALIDATOR_CONTAINER_PREFIX = "smoke-besu-qbft-validator"
RPC_NODE_CONTAINER = "smoke-besu-qbft-rpc"
DOCKER_NETWORK = "smoke-besu-qbft-network"
DEFAULT_DOCKER_SUBNET = "172.28.241.0/24"
DEFAULT_IMAGE = "hyperledger/besu:latest"
DEFAULT_RUNTIME_DIR = Path("runtime") / "smoke-besu-qbft-four-validators"
DEFAULT_CHAIN_ID = 42424241
DEFAULT_PORT_BASE = 30000
DEFAULT_PORT_OFFSET = 1
DEFAULT_PUBLIC_RPC_PORT = 30010
DEFAULT_DEPLOYMENT_ENVIRONMENT = "test"
DEFAULT_DEPLOYMENT_PROJECT_NAME = "main-computer-qbft-testnet"
DEFAULT_DEPLOYMENT_SOURCE_KIND = "qbft-smoke-testnet-deploy"
DEFAULT_FOUNDRY_IMAGE = "ghcr.io/foundry-rs/foundry:latest"
DEFAULT_DEPLOYER_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEFAULT_FUNDED_ACCOUNT_BALANCE = "0x21e19e0c9bab2400000"  # 10000 * 10^18 wei
DEFAULT_GENESIS_BASE_FEE_PER_GAS = "0x3b9aca00"  # 1 gwei; London/EIP-1559 active from genesis.
DEFAULT_SHANGHAI_TIME = 0  # Enables PUSH0-era bytecode from genesis for modern Solidity/Foundry deploys.
DEFAULT_FUNDED_ACCOUNTS = [
    "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
    "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
    "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
    "0x90f79bf6eb2c4f870365e785982e1f101e93b906",
]
P2P_PORT = 30303
RPC_CONTAINER_PORT = 8545
METADATA_FILE = "smoke-lab.json"
COMMANDS = {"up", "monitor", "check", "down", "restart", "smoke", "deploy"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def validator_container(index: int) -> str:
    return f"{VALIDATOR_CONTAINER_PREFIX}-{index}"


def validator_containers() -> list[str]:
    return [validator_container(index) for index in range(1, VALIDATOR_COUNT + 1)]


def all_smoke_containers() -> list[str]:
    return [*validator_containers(), RPC_NODE_CONTAINER, GENESIS_CONTAINER]


def run(command: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(command))
    return subprocess.run(
        command,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        run(["docker", "version", "--format", "{{.Server.Version}}"], capture=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return True


def docker_rm_force(name: str) -> None:
    run(["docker", "rm", "-f", name], check=False, capture=True)


def docker_network_rm(name: str) -> None:
    run(["docker", "network", "rm", name], check=False, capture=True)


def docker_network_create(name: str, *, subnet: str) -> None:
    run(["docker", "network", "create", "--subnet", subnet, name])


def docker_logs_tail(name: str, lines: int = 80) -> str:
    result = run(["docker", "logs", "--tail", str(lines), name], check=False, capture=True)
    return result.stdout or ""


def resolve_runtime_dir(args: argparse.Namespace) -> Path:
    configured = Path(args.runtime_dir)
    if configured.is_absolute():
        return configured.resolve()
    return (repo_root() / configured).resolve()


def resolve_rpc_ports(args: argparse.Namespace) -> list[int]:
    """Return deterministic host RPC ports for the validator containers.

    The default follows the local smoke-test convention:
    validator-1 uses 30000 + offset, validator-2 uses that port + 1, and so on.
    --rpc-port remains available only as an explicit compatibility override for
    the first validator port.
    """
    if args.rpc_port is not None:
        first_port = args.rpc_port
    else:
        if args.port_base < 1 or args.port_base > 65535:
            raise ValueError(f"--port-base must be between 1 and 65535, got {args.port_base}")
        if args.port_offset < 0:
            raise ValueError(f"--port-offset must be >= 0, got {args.port_offset}")
        first_port = args.port_base + args.port_offset

    ports = [first_port + index for index in range(VALIDATOR_COUNT)]
    for port in ports:
        if port < 1 or port > 65535:
            raise ValueError(f"Computed RPC port must be between 1 and 65535, got {port}")
    if len(set(ports)) != len(ports):
        raise ValueError(f"Computed RPC ports must be unique, got {ports}")
    return ports


def resolve_public_rpc_port(args: argparse.Namespace) -> int:
    port = int(args.public_rpc_port)
    if port < 1 or port > 65535:
        raise ValueError(f"--public-rpc-port must be between 1 and 65535, got {port}")
    return port


def rpc_urls_for_ports(ports: list[int]) -> list[str]:
    return [f"http://127.0.0.1:{port}" for port in ports]


def rpc_url_for_port(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def port_formula(args: argparse.Namespace) -> str:
    if args.rpc_port is None:
        return f"{args.port_base} + offset {args.port_offset}, then + validator index"
    return "explicit --rpc-port override, then + validator index"


def assert_host_port_available(port: int, *, host: str = "127.0.0.1") -> None:
    """Fail early with a useful message if Docker cannot bind a smoke RPC port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as exc:
            raise RuntimeError(
                f"Host port {host}:{port} is already in use. "
                "Use --port-offset N to select another deterministic smoke port range "
                "(validator-1 effective port = 30000 + offset), or stop the process/container using it."
            ) from exc


def assert_host_ports_available(ports: list[int]) -> None:
    if len(set(ports)) != len(ports):
        raise RuntimeError(f"Host RPC ports must be unique, got {ports}")
    for port in ports:
        assert_host_port_available(port)


def docker_ipv4_network(docker_subnet: str) -> ipaddress.IPv4Network:
    try:
        network = ipaddress.ip_network(docker_subnet, strict=False)
    except ValueError as exc:
        raise ValueError(f"--docker-subnet must be a valid CIDR subnet, got {docker_subnet!r}") from exc

    if network.version != 4:
        raise ValueError(f"--docker-subnet must be IPv4, got {docker_subnet!r}")
    if network.num_addresses < 21:
        raise ValueError(
            "--docker-subnet must have room for smoke validator IPs .11-.14 "
            f"and the non-validator RPC IP .20, got {docker_subnet!r}"
        )
    return network


def validator_ips(docker_subnet: str) -> list[str]:
    network = docker_ipv4_network(docker_subnet)
    base = int(network.network_address)
    ips = [str(ipaddress.ip_address(base + 10 + index)) for index in range(1, VALIDATOR_COUNT + 1)]
    for ip in ips:
        if ipaddress.ip_address(ip) not in network:
            raise ValueError(f"Computed validator IP {ip} was outside --docker-subnet {docker_subnet!r}")
    return ips


def rpc_node_ip(docker_subnet: str) -> str:
    network = docker_ipv4_network(docker_subnet)
    ip = str(ipaddress.ip_address(int(network.network_address) + 20))
    if ipaddress.ip_address(ip) not in network:
        raise ValueError(f"Computed non-validator RPC IP {ip} was outside --docker-subnet {docker_subnet!r}")
    return ip



def genesis_alloc() -> dict[str, dict[str, str]]:
    """Fund deterministic local deployer/office accounts in the QBFT genesis.

    The dev Anvil chain has deterministic funded accounts. The local QBFT
    testnet needs the same property so the existing Dockerized Foundry deployer
    can publish contracts without a special funding path.
    """

    alloc: dict[str, dict[str, str]] = {}
    for address in DEFAULT_FUNDED_ACCOUNTS:
        clean = address.lower().removeprefix("0x")
        alloc[clean] = {"balance": DEFAULT_FUNDED_ACCOUNT_BALANCE}
    return alloc

def write_qbft_config(
    path: Path,
    *,
    chain_id: int,
    block_period_seconds: int,
    request_timeout_seconds: int,
) -> None:
    config = {
        "genesis": {
            "config": {
                "chainId": chain_id,
                "berlinBlock": 0,
                "londonBlock": 0,
                "shanghaiTime": DEFAULT_SHANGHAI_TIME,
                "qbft": {
                    "blockperiodseconds": block_period_seconds,
                    "epochlength": 30000,
                    "requesttimeoutseconds": request_timeout_seconds,
                },
            },
            "nonce": "0x0",
            "timestamp": "0x58ee40ba",
            "gasLimit": "0x47b760",
            "difficulty": "0x1",
            "baseFeePerGas": DEFAULT_GENESIS_BASE_FEE_PER_GAS,
            "mixHash": "0x63746963616c2062797a616e74696e65206661756c7420746f6c6572616e6365",
            "coinbase": "0x0000000000000000000000000000000000000000",
            "alloc": genesis_alloc(),
        },
        "blockchain": {
            "nodes": {
                "generate": True,
                "count": VALIDATOR_COUNT,
            }
        },
    }
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def remove_path(path: Path) -> None:
    """Remove a generated smoke-test path whether it is a file or directory."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def file_fingerprint(path: Path, *, length: int = 16) -> str:
    """Return a short stable fingerprint for generated runtime file names."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest[:length]


def genesis_scoped_data_dir(node_dir: Path, genesis: Path) -> Path:
    """Return a fresh Besu data path scoped to the exact generated genesis.

    The QBFT genesis is regenerated when the smoke lab starts. Besu persists the
    genesis block in its RocksDB data directory and refuses to start when a new
    genesis is paired with an old database. Use a genesis-scoped data directory
    so a stale stable ``data`` directory cannot poison a regenerated lab.
    """
    return node_dir / f"data-{file_fingerprint(genesis)}"


def container_path_for_runtime_child(runtime_dir: Path, child: Path) -> str:
    relative = child.resolve().relative_to(runtime_dir.resolve()).as_posix()
    return f"/smoke/{relative}"


def write_active_data_dir_marker(node_dir: Path, data_dir: Path) -> None:
    (node_dir / "active-data-dir.txt").write_text(data_dir.name + "\n", encoding="utf-8")


def active_container_data_path(runtime_dir: Path, node_name: str) -> str:
    node_dir = runtime_dir / node_name
    marker = node_dir / "active-data-dir.txt"
    if not marker.exists():
        raise RuntimeError(f"Missing active Besu data-dir marker: {marker}")
    data_dir_name = marker.read_text(encoding="utf-8").strip()
    if not data_dir_name or "/" in data_dir_name or "\\" in data_dir_name or data_dir_name in {".", ".."}:
        raise RuntimeError(f"Invalid active Besu data-dir marker in {marker}: {data_dir_name!r}")
    return container_path_for_runtime_child(runtime_dir, node_dir / data_dir_name)


def prepare_runtime(runtime_dir: Path) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)

    # Besu intentionally refuses to generate into an existing --to directory.
    # Clean the stable inspection directory and any leftovers from older smoke
    # runs before asking Besu to generate fresh output.
    for child in runtime_dir.glob("networkFiles*"):
        remove_path(child)

    for index in range(1, VALIDATOR_COUNT + 1):
        remove_path(runtime_dir / f"validator-{index}")

    remove_path(runtime_dir / "rpc-node")
    remove_path(runtime_dir / "genesis.json")
    remove_path(runtime_dir / "static-nodes.json")
    remove_path(runtime_dir / "static-nodes-all.json")
    remove_path(runtime_dir / METADATA_FILE)


def generate_network_files(runtime_dir: Path, *, image: str) -> Path:
    """Generate QBFT genesis/key material without using a mounted --to path.

    Besu refuses to run when the --to directory already exists. On Docker
    Desktop for Windows, generating directly into the bind-mounted smoke
    directory can still trip that guard. Generate inside the fresh container's
    /tmp directory first, then copy the completed networkFiles directory back to
    the mounted runtime directory.
    """
    stable_dir = runtime_dir / "networkFiles"
    remove_path(stable_dir)

    shell_script = (
        "set -eu; "
        "rm -rf /tmp/smoke-networkFiles /smoke/networkFiles; "
        "if command -v besu >/dev/null 2>&1; then BESU=besu; else BESU=/opt/besu/bin/besu; fi; "
        "\"$BESU\" operator generate-blockchain-config "
        "--config-file=/smoke/qbftConfigFile.json "
        "--to=/tmp/smoke-networkFiles "
        "--private-key-file-name=key; "
        "cp -R /tmp/smoke-networkFiles /smoke/networkFiles"
    )

    run(
        [
            "docker",
            "run",
            "--rm",
            "--name",
            GENESIS_CONTAINER,
            "--entrypoint",
            "/bin/sh",
            "-v",
            f"{runtime_dir.resolve()}:/smoke",
            image,
            "-c",
            shell_script,
        ]
    )

    if not stable_dir.exists():
        raise RuntimeError(f"Besu generation completed but did not create expected output: {stable_dir}")
    return stable_dir


def normalize_node_public_key(raw_value: str, *, source: Path) -> str:
    value = raw_value.strip().lower()
    if value.startswith("0x"):
        value = value[2:]
    if value.startswith("04") and len(value) == 130:
        value = value[2:]
    if len(value) != 128:
        raise RuntimeError(
            f"Expected Besu node public key in {source} to be 128 hex characters "
            f"after normalization, got {len(value)}"
        )
    try:
        int(value, 16)
    except ValueError as exc:
        raise RuntimeError(f"Besu node public key in {source} was not hex") from exc
    return value


def install_validator_files(
    network_files: Path,
    runtime_dir: Path,
    *,
    docker_subnet: str,
) -> list[dict[str, str]]:
    genesis = network_files / "genesis.json"
    keys_dir = network_files / "keys"
    if not genesis.exists():
        raise RuntimeError(f"Besu did not create expected genesis file: {genesis}")
    if not keys_dir.exists():
        raise RuntimeError(f"Besu did not create expected keys directory: {keys_dir}")

    key_dirs = sorted(path for path in keys_dir.iterdir() if path.is_dir())
    if len(key_dirs) != VALIDATOR_COUNT:
        raise RuntimeError(
            f"Expected {VALIDATOR_COUNT} generated validator key directories under {keys_dir}, "
            f"found {len(key_dirs)}"
        )

    runtime_genesis = runtime_dir / "genesis.json"
    shutil.copy2(genesis, runtime_genesis)

    ips = validator_ips(docker_subnet)
    validators: list[dict[str, str]] = []
    for index, (key_dir, ip_address) in enumerate(zip(key_dirs, ips), start=1):
        validator_dir = runtime_dir / f"validator-{index}"
        validator_dir.mkdir(parents=True, exist_ok=True)
        data_dir = genesis_scoped_data_dir(validator_dir, runtime_genesis)
        remove_path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=False)

        private_key = key_dir / "key"
        public_key = key_dir / "key.pub"
        if not private_key.exists():
            raise RuntimeError(f"Missing Besu private key: {private_key}")
        if not public_key.exists():
            raise RuntimeError(f"Missing Besu public key: {public_key}")

        shutil.copy2(private_key, data_dir / "key")
        shutil.copy2(public_key, data_dir / "key.pub")
        write_active_data_dir_marker(validator_dir, data_dir)

        address = key_dir.name.lower()
        node_public_key = normalize_node_public_key(public_key.read_text(encoding="utf-8"), source=public_key)
        container = validator_container(index)
        container_data_path = container_path_for_runtime_child(runtime_dir, data_dir)
        validators.append(
            {
                "index": str(index),
                "address": address,
                "container": container,
                "ip_address": ip_address,
                "data_dir": data_dir.relative_to(runtime_dir).as_posix(),
                "container_data_path": container_data_path,
                "enode": f"enode://{node_public_key}@{ip_address}:{P2P_PORT}",
            }
        )

    all_static_nodes = [validator["enode"] for validator in validators]
    (runtime_dir / "static-nodes-all.json").write_text(
        json.dumps(all_static_nodes, indent=2) + "\n",
        encoding="utf-8",
    )

    for validator in validators:
        validator_dir = runtime_dir / f"validator-{validator['index']}"
        other_nodes = [node for node in all_static_nodes if node != validator["enode"]]
        (validator_dir / "static-nodes.json").write_text(
            json.dumps(other_nodes, indent=2) + "\n",
            encoding="utf-8",
        )

    return validators


def install_rpc_node_files(runtime_dir: Path, *, validators: list[dict[str, str]]) -> None:
    """Create non-validator RPC node runtime files.

    The RPC node intentionally receives the same genesis file and validator
    static-node list, but no validator private key. It joins the QBFT network as
    a regular peer and exposes the stable RPC endpoint that hub/dev tooling
    should use.
    """
    runtime_genesis = runtime_dir / "genesis.json"
    if not runtime_genesis.exists():
        raise RuntimeError(f"Missing runtime genesis file before creating RPC node data dir: {runtime_genesis}")

    rpc_node_dir = runtime_dir / "rpc-node"
    rpc_node_dir.mkdir(parents=True, exist_ok=True)
    data_dir = genesis_scoped_data_dir(rpc_node_dir, runtime_genesis)
    remove_path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=False)
    write_active_data_dir_marker(rpc_node_dir, data_dir)

    static_nodes = [validator["enode"] for validator in validators]
    (rpc_node_dir / "static-nodes.json").write_text(
        json.dumps(static_nodes, indent=2) + "\n",
        encoding="utf-8",
    )


def start_validator(
    runtime_dir: Path,
    *,
    image: str,
    index: int,
    rpc_port: int,
    chain_id: int,
    docker_subnet: str,
    container_data_path: str,
) -> None:
    container = validator_container(index)
    ip_address = validator_ips(docker_subnet)[index - 1]
    run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "--network",
            DOCKER_NETWORK,
            "--ip",
            ip_address,
            "-p",
            f"127.0.0.1:{rpc_port}:{RPC_CONTAINER_PORT}",
            "-v",
            f"{runtime_dir.resolve()}:/smoke",
            image,
            f"--data-path={container_data_path}",
            "--genesis-file=/smoke/genesis.json",
            f"--network-id={chain_id}",
            "--rpc-http-enabled=true",
            "--rpc-http-host=0.0.0.0",
            f"--rpc-http-port={RPC_CONTAINER_PORT}",
            "--rpc-http-api=ETH,NET,QBFT,WEB3",
            "--host-allowlist=*",
            "--rpc-http-cors-origins=all",
            "--profile=ENTERPRISE",
            "--min-gas-price=0",
            f"--p2p-port={P2P_PORT}",
            f"--p2p-host={ip_address}",
            "--nat-method=NONE",
            "--discovery-enabled=false",
            f"--static-nodes-file=/smoke/validator-{index}/static-nodes.json",
        ]
    )


def start_rpc_node(
    runtime_dir: Path,
    *,
    image: str,
    rpc_port: int,
    chain_id: int,
    docker_subnet: str,
    container_data_path: str,
) -> None:
    ip_address = rpc_node_ip(docker_subnet)
    run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            RPC_NODE_CONTAINER,
            "--network",
            DOCKER_NETWORK,
            "--ip",
            ip_address,
            "-p",
            f"127.0.0.1:{rpc_port}:{RPC_CONTAINER_PORT}",
            "-v",
            f"{runtime_dir.resolve()}:/smoke",
            image,
            f"--data-path={container_data_path}",
            "--genesis-file=/smoke/genesis.json",
            f"--network-id={chain_id}",
            "--rpc-http-enabled=true",
            "--rpc-http-host=0.0.0.0",
            f"--rpc-http-port={RPC_CONTAINER_PORT}",
            "--rpc-http-api=ETH,NET,QBFT,WEB3",
            "--host-allowlist=*",
            "--rpc-http-cors-origins=all",
            "--profile=ENTERPRISE",
            "--min-gas-price=0",
            f"--p2p-port={P2P_PORT}",
            f"--p2p-host={ip_address}",
            "--nat-method=NONE",
            "--discovery-enabled=false",
            "--static-nodes-file=/smoke/rpc-node/static-nodes.json",
        ]
    )


def rpc_call(url: str, method: str, params: list[Any] | None = None, *, timeout_seconds: float = 2.0) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if "error" in payload:
        raise RuntimeError(f"{method} returned JSON-RPC error: {payload['error']}")
    return payload.get("result")


def try_rpc_call(url: str, method: str, params: list[Any] | None = None, *, timeout_seconds: float = 2.0) -> tuple[bool, Any]:
    try:
        return True, rpc_call(url, method, params, timeout_seconds=timeout_seconds)
    except Exception as exc:
        return False, exc


def wait_for_rpc(url: str, *, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            rpc_call(url, "eth_chainId")
            return
        except (OSError, urllib.error.URLError, RuntimeError) as exc:
            last_error = exc
            time.sleep(1)
    raise TimeoutError(f"Timed out waiting for Besu JSON-RPC at {url}: {last_error}")


def wait_for_block(url: str, *, timeout_seconds: int) -> int:
    deadline = time.monotonic() + timeout_seconds
    last_block = 0
    while time.monotonic() < deadline:
        value = rpc_call(url, "eth_blockNumber")
        last_block = int(value, 16)
        if last_block >= 1:
            return last_block
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for QBFT block production; last block was {last_block}")


def wait_for_peers(urls: list[str], *, minimum_peers: int, timeout_seconds: int) -> list[int]:
    deadline = time.monotonic() + timeout_seconds
    last_counts: list[int] = []
    while time.monotonic() < deadline:
        counts = []
        for url in urls:
            try:
                counts.append(int(rpc_call(url, "net_peerCount"), 16))
            except Exception:
                counts.append(-1)
        last_counts = counts
        if counts and all(count >= minimum_peers for count in counts):
            return counts
        time.sleep(1)
    raise TimeoutError(
        f"Timed out waiting for each requested node to have at least {minimum_peers} peers; "
        f"last peer counts were {last_counts}"
    )


def verify_chain_ids(urls: list[str], *, expected_chain_id: int) -> list[str]:
    chain_ids: list[str] = []
    for url in urls:
        chain_id_hex = rpc_call(url, "eth_chainId")
        chain_id = int(chain_id_hex, 16)
        if chain_id != expected_chain_id:
            raise RuntimeError(f"{url} expected chain ID {expected_chain_id}, got {chain_id} ({chain_id_hex})")
        chain_ids.append(chain_id_hex)
    return chain_ids


def verify_validator_set(url: str, *, expected_addresses: list[str]) -> list[str]:
    validators = rpc_call(url, "qbft_getValidatorsByBlockNumber", ["latest"])
    if not isinstance(validators, list):
        raise RuntimeError(f"qbft_getValidatorsByBlockNumber returned non-list result: {validators!r}")

    expected = {address.lower() for address in expected_addresses}
    actual = {str(address).lower() for address in validators}
    if actual != expected:
        raise RuntimeError(
            "QBFT validator set mismatch: "
            f"expected {sorted(expected)}, got {sorted(actual)}"
        )
    return [str(address) for address in validators]


def load_metadata(runtime_dir: Path) -> dict[str, Any] | None:
    path = runtime_dir / METADATA_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_metadata(
    runtime_dir: Path,
    *,
    args: argparse.Namespace,
    rpc_ports: list[int],
    public_rpc_port: int,
    validators: list[dict[str, str]],
) -> None:
    metadata = {
        "version": 1,
        "created_at_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "docker_network": DOCKER_NETWORK,
        "docker_subnet": args.docker_subnet,
        "image": args.image,
        "chain_id": args.chain_id,
        "rpc_ports": rpc_ports,
        "rpc_urls": rpc_urls_for_ports(rpc_ports),
        "validator_rpc_ports": rpc_ports,
        "validator_rpc_urls": rpc_urls_for_ports(rpc_ports),
        "public_rpc_port": public_rpc_port,
        "public_rpc_url": rpc_url_for_port(public_rpc_port),
        "rpc_node": {
            "container": RPC_NODE_CONTAINER,
            "ip_address": rpc_node_ip(args.docker_subnet),
            "rpc_port": public_rpc_port,
            "rpc_url": rpc_url_for_port(public_rpc_port),
            "role": "non-validator-rpc",
        },
        "port_formula": port_formula(args),
        "block_period_seconds": args.block_period_seconds,
        "request_timeout_seconds": args.request_timeout_seconds,
        "validators": validators,
    }
    (runtime_dir / METADATA_FILE).write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def metadata_rpc_ports(args: argparse.Namespace, runtime_dir: Path) -> list[int]:
    metadata = load_metadata(runtime_dir)
    if metadata and isinstance(metadata.get("rpc_ports"), list):
        try:
            ports = [int(port) for port in metadata["rpc_ports"]]
        except (TypeError, ValueError):
            ports = []
        if len(ports) == VALIDATOR_COUNT:
            return ports
    return resolve_rpc_ports(args)


def metadata_public_rpc_port(args: argparse.Namespace, runtime_dir: Path) -> int:
    metadata = load_metadata(runtime_dir)
    if metadata:
        try:
            return int(metadata.get("public_rpc_port", args.public_rpc_port))
        except (TypeError, ValueError):
            pass
    return resolve_public_rpc_port(args)


def metadata_validators(runtime_dir: Path) -> list[dict[str, str]]:
    metadata = load_metadata(runtime_dir)
    if not metadata:
        return []
    validators = metadata.get("validators")
    if not isinstance(validators, list):
        return []
    return [validator for validator in validators if isinstance(validator, dict)]


def best_rpc_url(urls: list[str]) -> str | None:
    for url in urls:
        ok, _ = try_rpc_call(url, "eth_chainId", timeout_seconds=1.0)
        if ok:
            return url
    return None


def short_hash(value: Any) -> str:
    text = str(value or "")
    if len(text) <= 18:
        return text
    return f"{text[:10]}…{text[-8:]}"


def hex_int(value: Any, *, default: int = 0) -> int:
    if not isinstance(value, str):
        return default
    try:
        return int(value, 16)
    except ValueError:
        return default


def collect_node_status(url: str, *, label: str | None = None) -> dict[str, Any]:
    status: dict[str, Any] = {"url": url, "label": label or url, "up": False}
    ok, chain_id = try_rpc_call(url, "eth_chainId", timeout_seconds=1.5)
    if not ok:
        status["error"] = str(chain_id)
        return status
    status["up"] = True
    status["chain_id"] = chain_id

    ok, block_number = try_rpc_call(url, "eth_blockNumber", timeout_seconds=1.5)
    status["block_number"] = hex_int(block_number) if ok else None

    ok, peer_count = try_rpc_call(url, "net_peerCount", timeout_seconds=1.5)
    status["peer_count"] = hex_int(peer_count, default=-1) if ok else -1

    return status


def collect_chain_status(urls: list[str], *, labels: list[str] | None = None) -> dict[str, Any]:
    if labels is None:
        labels = urls
    node_statuses = [collect_node_status(url, label=label) for url, label in zip(urls, labels)]
    rpc_url = best_rpc_url(urls)
    if rpc_url is None:
        return {"rpc_url": None, "nodes": node_statuses}

    chain_id_hex = rpc_call(rpc_url, "eth_chainId")
    block_number_hex = rpc_call(rpc_url, "eth_blockNumber")
    block = rpc_call(rpc_url, "eth_getBlockByNumber", ["latest", False])
    validators = rpc_call(rpc_url, "qbft_getValidatorsByBlockNumber", ["latest"])

    timestamp = None
    if isinstance(block, dict):
        timestamp_value = block.get("timestamp")
        if isinstance(timestamp_value, str):
            timestamp = _dt.datetime.fromtimestamp(int(timestamp_value, 16), tz=_dt.UTC).isoformat()

    return {
        "rpc_url": rpc_url,
        "chain_id_hex": chain_id_hex,
        "chain_id": int(chain_id_hex, 16),
        "block_number": int(block_number_hex, 16),
        "block_hash": block.get("hash") if isinstance(block, dict) else None,
        "parent_hash": block.get("parentHash") if isinstance(block, dict) else None,
        "timestamp": timestamp,
        "validators": validators if isinstance(validators, list) else [],
        "nodes": node_statuses,
    }


def print_chain_summary(status: dict[str, Any], *, verbose: bool = False) -> None:
    if status.get("rpc_url") is None:
        print("No smoke-lab JSON-RPC endpoint is reachable.")
        for node in status.get("nodes", []):
            print(f"  {node['url']}: down ({node.get('error', 'unknown error')})")
        return

    nodes = status.get("nodes", [])
    peer_counts = [node.get("peer_count", "down") if node.get("up") else "down" for node in nodes]
    up_count = sum(1 for node in nodes if node.get("up"))
    print(
        "block={block} hash={hash} peers={peers} validators={validators} up={up}/{total} rpc={rpc}".format(
            block=status.get("block_number"),
            hash=short_hash(status.get("block_hash")),
            peers=peer_counts,
            validators=len(status.get("validators", [])),
            up=up_count,
            total=len(nodes),
            rpc=status.get("rpc_url"),
        )
    )
    if status.get("timestamp"):
        print(f"  block_time_utc: {status['timestamp']}")
    if verbose:
        for node in nodes:
            label = node.get("label", node.get("url", "node"))
            if node.get("up"):
                print(
                    f"  {label}: url={node['url']} "
                    f"block={node.get('block_number')} peers={node.get('peer_count')} "
                    f"chain_id={node.get('chain_id')}"
                )
            else:
                print(f"  {label}: url={node['url']} down error={node.get('error')}")



def default_deployment_run_id() -> str:
    return "qbft-testnet-" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def resolve_deployment_output_dir(args: argparse.Namespace) -> Path:
    configured = Path(args.deployment_output_dir)
    if configured.is_absolute():
        return configured.resolve()
    return (repo_root() / configured).resolve()


def smoke_deployment_output_dir(runtime_dir: Path) -> Path:
    return runtime_dir / "deployments"


def deployment_command(args: argparse.Namespace, *, runtime_dir: Path, public_rpc_url: str, chain_id: int) -> list[str]:
    run_id = args.deployment_run_id or default_deployment_run_id()
    command = [
        sys.executable,
        str(repo_root() / "tools" / "dev-chain-reset.py"),
        "--yes",
        "--external-chain",
        "--run-id",
        run_id,
        "--project-name",
        args.deployment_project_name,
        "--environment",
        args.deployment_environment,
        "--source-kind",
        DEFAULT_DEPLOYMENT_SOURCE_KIND,
        "--chain-id",
        str(chain_id),
        "--host-rpc-url",
        public_rpc_url,
        "--container-rpc-url",
        f"http://{RPC_NODE_CONTAINER}:{RPC_CONTAINER_PORT}",
        "--external-docker-network",
        DOCKER_NETWORK,
        "--external-chain-container",
        RPC_NODE_CONTAINER,
        "--output-dir",
        str(smoke_deployment_output_dir(runtime_dir)),
        "--deployment-output-dir",
        str(resolve_deployment_output_dir(args)),
        "--foundry-image",
        args.foundry_image,
        "--generate-offices",
        "--private-key",
        args.private_key,
        "--hub-admin-funding-wei",
        str(args.hub_admin_funding_wei),
        "--max-payout-wei",
        str(args.max_payout_wei),
        "--payout-delay-blocks",
        str(args.payout_delay_blocks),
        "--reset-delay-blocks",
        str(args.reset_delay_blocks),
        "--wait-timeout-s",
        str(args.timeout_seconds),
        "--deploy-timeout-s",
        str(args.deploy_timeout_seconds),
    ]
    for deployment in args.deploy or []:
        command.extend(["--deploy", deployment])
    return command



def funded_deployer_balance_wei(public_rpc_url: str) -> int:
    balance = rpc_call(public_rpc_url, "eth_getBalance", [DEFAULT_FUNDED_ACCOUNTS[0], "latest"], timeout_seconds=2.0)
    return hex_int(balance, default=0)

def deploy_testnet(args: argparse.Namespace) -> int:
    if not docker_available():
        print("Docker is required for the QBFT testnet contract deployment.", file=sys.stderr)
        return 2

    runtime_dir = resolve_runtime_dir(args)
    public_rpc_port = metadata_public_rpc_port(args, runtime_dir)
    public_rpc_url = rpc_url_for_port(public_rpc_port)
    metadata = load_metadata(runtime_dir) or {}
    chain_id = int(metadata.get("chain_id", args.chain_id))

    try:
        wait_for_rpc(public_rpc_url, timeout_seconds=args.timeout_seconds)
        verify_chain_ids([public_rpc_url], expected_chain_id=chain_id)
        deployer_balance = funded_deployer_balance_wei(public_rpc_url)
    except Exception as exc:  # noqa: BLE001 - operator-facing script
        print(f"QBFT testnet RPC is not ready for deployment: {exc}", file=sys.stderr)
        print("Start it first with: python tools/smoke_besu_qbft_one_validator.py up", file=sys.stderr)
        return 1

    if deployer_balance <= 0:
        print(
            "The QBFT testnet deployer has no native balance. Restart the smoke lab so the updated "
            "genesis funds the deterministic dev deployer:",
            file=sys.stderr,
        )
        print("  python tools/smoke_besu_qbft_one_validator.py down", file=sys.stderr)
        print("  python tools/smoke_besu_qbft_one_validator.py up", file=sys.stderr)
        return 1

    command = deployment_command(args, runtime_dir=runtime_dir, public_rpc_url=public_rpc_url, chain_id=chain_id)
    print("Deploying Main Computer contracts to the local QBFT testnet.")
    print(f"  app RPC: {public_rpc_url}")
    print(f"  Docker RPC: http://{RPC_NODE_CONTAINER}:{RPC_CONTAINER_PORT}")
    print(f"  chain_id: {chain_id}")
    print(f"  deployer_balance_wei: {deployer_balance}")
    print(f"  publication: {resolve_deployment_output_dir(args) / args.deployment_environment / 'latest.json'}")
    print()
    completed = run(command, check=False)
    if completed.returncode == 0:
        print()
        print("QBFT testnet deployment published the app-facing runtime manifest.")
        print(f"  {resolve_deployment_output_dir(args) / args.deployment_environment / 'latest.json'}")
        print()
        print("Run the Hub against it with:")
        print("  python -m main_computer.cli hub --network test")
    return completed.returncode


def stop_smoke_containers() -> None:
    for container in all_smoke_containers():
        docker_rm_force(container)


def down(args: argparse.Namespace) -> int:
    if shutil.which("docker") is None:
        print("Docker CLI is not installed; nothing to stop.")
        return 0
    stop_smoke_containers()
    docker_network_rm(DOCKER_NETWORK)
    print(
        "Stopped smoke containers/network: "
        + ", ".join([*all_smoke_containers(), DOCKER_NETWORK])
    )
    return 0


def start_lab(args: argparse.Namespace, *, cleanup_on_failure: bool = False) -> int:
    if not docker_available():
        print("Docker is required for this smoke lab, but the Docker CLI/daemon was not available.", file=sys.stderr)
        return 2

    runtime_dir = resolve_runtime_dir(args)
    rpc_ports = resolve_rpc_ports(args)
    rpc_urls = rpc_urls_for_ports(rpc_ports)
    public_rpc_port = resolve_public_rpc_port(args)
    public_rpc_url = rpc_url_for_port(public_rpc_port)

    stop_smoke_containers()
    docker_network_rm(DOCKER_NETWORK)
    assert_host_ports_available([*rpc_ports, public_rpc_port])
    prepare_runtime(runtime_dir)
    write_qbft_config(
        runtime_dir / "qbftConfigFile.json",
        chain_id=args.chain_id,
        block_period_seconds=args.block_period_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
    )

    started: list[str] = []
    try:
        network_files = generate_network_files(runtime_dir, image=args.image)
        validators = install_validator_files(network_files, runtime_dir, docker_subnet=args.docker_subnet)
        install_rpc_node_files(runtime_dir, validators=validators)

        docker_network_create(DOCKER_NETWORK, subnet=args.docker_subnet)
        for index, rpc_port in enumerate(rpc_ports, start=1):
            start_validator(
                runtime_dir,
                image=args.image,
                index=index,
                rpc_port=rpc_port,
                chain_id=args.chain_id,
                docker_subnet=args.docker_subnet,
                container_data_path=validators[index - 1]["container_data_path"],
            )
            started.append(validator_container(index))

        for url in rpc_urls:
            wait_for_rpc(url, timeout_seconds=args.timeout_seconds)

        chain_id_hex_values = verify_chain_ids(rpc_urls, expected_chain_id=args.chain_id)
        peer_counts = wait_for_peers(rpc_urls, minimum_peers=VALIDATOR_COUNT - 1, timeout_seconds=args.timeout_seconds)
        block_number = wait_for_block(rpc_urls[0], timeout_seconds=args.timeout_seconds)
        qbft_validators_latest = verify_validator_set(
            rpc_urls[0],
            expected_addresses=[validator["address"] for validator in validators],
        )

        start_rpc_node(
            runtime_dir,
            image=args.image,
            rpc_port=public_rpc_port,
            chain_id=args.chain_id,
            docker_subnet=args.docker_subnet,
            container_data_path=active_container_data_path(runtime_dir, "rpc-node"),
        )
        started.append(RPC_NODE_CONTAINER)
        wait_for_rpc(public_rpc_url, timeout_seconds=args.timeout_seconds)
        verify_chain_ids([public_rpc_url], expected_chain_id=args.chain_id)
        public_rpc_peer_count = wait_for_peers(
            [public_rpc_url],
            minimum_peers=1,
            timeout_seconds=args.timeout_seconds,
        )[0]
        public_rpc_block_number = wait_for_block(public_rpc_url, timeout_seconds=args.timeout_seconds)

        write_metadata(
            runtime_dir,
            args=args,
            rpc_ports=rpc_ports,
            public_rpc_port=public_rpc_port,
            validators=validators,
        )

        print()
        print("Besu QBFT four-validator plus non-validator RPC smoke lab is running.")
        print(f"  docker_network: {DOCKER_NETWORK}")
        print(f"  docker_subnet: {args.docker_subnet}")
        print(f"  validator_rpc_ports: {rpc_ports} ({port_formula(args)})")
        print(f"  non_validator_rpc_url: {public_rpc_url}")
        print(f"  chain_id: {args.chain_id} ({chain_id_hex_values[0]})")
        print(f"  validator_block_number: {block_number}")
        print(f"  public_rpc_block_number: {public_rpc_block_number}")
        print(f"  validator_peer_counts: {peer_counts}")
        print(f"  public_rpc_peer_count: {public_rpc_peer_count}")
        print(f"  qbft_validators_latest: {qbft_validators_latest}")
        print(f"  static_nodes_file: {runtime_dir / 'static-nodes-all.json'}")
        print(f"  metadata_file: {runtime_dir / METADATA_FILE}")
        print(f"  runtime_dir: {runtime_dir}")
        print("  non-validator RPC node:")
        print(f"    container: {RPC_NODE_CONTAINER}")
        print(f"    rpc_url: {public_rpc_url}")
        print(f"    ip_address: {rpc_node_ip(args.docker_subnet)}")
        print("  validators:")
        for validator, rpc_url in zip(validators, rpc_urls):
            print(f"    validator-{validator['index']}:")
            print(f"      container: {validator['container']}")
            print(f"      rpc_url: {rpc_url}")
            print(f"      ip_address: {validator['ip_address']}")
            print(f"      address: {validator['address']}")

        print()
        print("Use this RPC endpoint for hub/dev tooling:")
        print(f"  {public_rpc_url}")
        print("Deploy contracts and publish runtime/deployments/dev/latest.json:")
        print("  python tools/smoke_besu_qbft_one_validator.py deploy")
        print("Monitor blocks:")
        print("  python tools/smoke_besu_qbft_one_validator.py monitor")
        print("Stop the lab:")
        print("  python tools/smoke_besu_qbft_one_validator.py down")

        if args.deploy_contracts:
            print()
            return deploy_testnet(args)
        return 0

    except Exception as exc:
        print()
        print(f"Smoke lab failed: {exc}", file=sys.stderr)
        print(
            "Tip: rerun with down to remove smoke containers, use --port-offset N or --public-rpc-port if a host port is busy, "
            f"use --docker-subnet if the Docker subnet conflicts, or delete {runtime_dir / 'networkFiles'} "
            "if you want to inspect a clean generation.",
            file=sys.stderr,
        )
        for container in started:
            print()
            print(f"Last logs from {container}:", file=sys.stderr)
            print(docker_logs_tail(container), file=sys.stderr)
        if cleanup_on_failure:
            stop_smoke_containers()
            docker_network_rm(DOCKER_NETWORK)
        return 1


def smoke(args: argparse.Namespace) -> int:
    code = start_lab(args, cleanup_on_failure=True)
    if code != 0:
        return code
    if args.keep_running:
        print()
        print("Containers left running because --keep-running was used with smoke.")
        return 0
    stop_smoke_containers()
    docker_network_rm(DOCKER_NETWORK)
    print()
    print("Disposable smoke completed and cleaned up.")
    return 0


def check(args: argparse.Namespace) -> int:
    runtime_dir = resolve_runtime_dir(args)
    rpc_ports = metadata_rpc_ports(args, runtime_dir)
    rpc_urls = rpc_urls_for_ports(rpc_ports)
    public_rpc_port = metadata_public_rpc_port(args, runtime_dir)
    public_rpc_url = rpc_url_for_port(public_rpc_port)
    all_rpc_urls = [public_rpc_url, *rpc_urls]
    labels = ["non-validator-rpc", *[f"validator-{index}" for index in range(1, len(rpc_urls) + 1)]]

    try:
        status = collect_chain_status(all_rpc_urls, labels=labels)
        print_chain_summary(status, verbose=True)

        if status.get("rpc_url") is None:
            return 1

        public_status = (status.get("nodes") or [{}])[0]
        if not public_status.get("up"):
            print(
                f"Non-validator RPC endpoint is not reachable: {public_rpc_url} "
                f"({public_status.get('error', 'unknown error')})",
                file=sys.stderr,
            )
            return 1

        expected_chain_id = args.chain_id
        if load_metadata(runtime_dir) and isinstance(load_metadata(runtime_dir), dict):
            metadata = load_metadata(runtime_dir) or {}
            expected_chain_id = int(metadata.get("chain_id", args.chain_id))

        actual_chain_id = int(status["chain_id"])
        if actual_chain_id != expected_chain_id:
            print(f"Chain ID mismatch: expected {expected_chain_id}, got {actual_chain_id}", file=sys.stderr)
            return 1

        validators = metadata_validators(runtime_dir)
        if validators:
            expected_addresses = [str(validator.get("address", "")).lower() for validator in validators]
            actual_addresses = [str(address).lower() for address in status.get("validators", [])]
            if sorted(expected_addresses) != sorted(actual_addresses):
                print("QBFT validator set mismatch.", file=sys.stderr)
                print(f"  expected: {sorted(expected_addresses)}", file=sys.stderr)
                print(f"  actual:   {sorted(actual_addresses)}", file=sys.stderr)
                return 1

        print("QBFT smoke lab check passed.")
        return 0
    except Exception as exc:
        print(f"QBFT smoke lab check failed: {exc}", file=sys.stderr)
        return 1


def monitor(args: argparse.Namespace) -> int:
    runtime_dir = resolve_runtime_dir(args)
    rpc_ports = metadata_rpc_ports(args, runtime_dir)
    rpc_urls = rpc_urls_for_ports(rpc_ports)
    public_rpc_port = metadata_public_rpc_port(args, runtime_dir)
    public_rpc_url = rpc_url_for_port(public_rpc_port)
    all_rpc_urls = [public_rpc_url, *rpc_urls]
    labels = ["non-validator-rpc", *[f"validator-{index}" for index in range(1, len(rpc_urls) + 1)]]

    print("Main Computer QBFT smoke monitor")
    print(f"  runtime_dir: {runtime_dir}")
    print(f"  non_validator_rpc_url: {public_rpc_url}")
    print(f"  validator_rpc_urls: {', '.join(rpc_urls)}")
    print("  press Ctrl+C to stop monitoring")
    print()

    last_block: int | None = None
    try:
        while True:
            try:
                status = collect_chain_status(all_rpc_urls, labels=labels)
                block_number = status.get("block_number")
                if args.verbose or block_number != last_block:
                    print_chain_summary(status, verbose=args.verbose)
                    last_block = block_number if isinstance(block_number, int) else last_block
                if args.once:
                    return 0 if status.get("rpc_url") is not None else 1
            except Exception as exc:
                print(f"monitor error: {exc}", file=sys.stderr)
                if args.once:
                    return 1
            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        print()
        print("Stopped monitoring.")
        return 0


def restart(args: argparse.Namespace) -> int:
    down(args)
    return start_lab(args)


def parse_args(argv: list[str]) -> argparse.Namespace:
    remaining = list(argv)
    command = "up"

    if remaining and remaining[0] in COMMANDS:
        command = remaining.pop(0)
    elif "--down" in remaining:
        # Compatibility with the original smoke helper.
        command = "down"
        remaining = [item for item in remaining if item != "--down"]

    parser = argparse.ArgumentParser(
        description="Run, monitor, or stop a self-contained four-validator plus non-validator RPC Besu QBFT smoke lab in Docker.",
        epilog=(
            "Commands: up, deploy, monitor, check, down, restart, smoke. "
            "Default command: up. Examples: "
            "python tools/smoke_besu_qbft_one_validator.py up; "
            "python tools/smoke_besu_qbft_one_validator.py deploy; "
            "python tools/smoke_besu_qbft_one_validator.py monitor; "
            "python tools/smoke_besu_qbft_one_validator.py down."
        ),
    )
    parser.add_argument("--image", default=DEFAULT_IMAGE, help=f"Besu Docker image to use. Default: {DEFAULT_IMAGE}")
    parser.add_argument(
        "--runtime-dir",
        default=str(DEFAULT_RUNTIME_DIR),
        help=f"Runtime output directory. Default: {DEFAULT_RUNTIME_DIR}",
    )
    parser.add_argument("--chain-id", type=int, default=DEFAULT_CHAIN_ID, help=f"Smoke chain ID. Default: {DEFAULT_CHAIN_ID}")
    parser.add_argument("--port-base", type=int, default=DEFAULT_PORT_BASE, help=f"Host RPC port base. Default: {DEFAULT_PORT_BASE}")
    parser.add_argument(
        "--port-offset",
        type=int,
        default=DEFAULT_PORT_OFFSET,
        help=(
            f"Host RPC port offset for validator-1; effective default validator ports are "
            f"{DEFAULT_PORT_BASE + DEFAULT_PORT_OFFSET}-"
            f"{DEFAULT_PORT_BASE + DEFAULT_PORT_OFFSET + VALIDATOR_COUNT - 1}. "
            f"The non-validator RPC node defaults to {DEFAULT_PUBLIC_RPC_PORT}."
        ),
    )
    parser.add_argument(
        "--rpc-port",
        type=int,
        default=None,
        help="Exact host RPC port override for validator-1. Other validators use the next ports.",
    )
    parser.add_argument(
        "--public-rpc-port",
        "--rpc-node-port",
        dest="public_rpc_port",
        type=int,
        default=DEFAULT_PUBLIC_RPC_PORT,
        help=(
            "Host RPC port for the dedicated non-validator RPC node. "
            f"Default: {DEFAULT_PUBLIC_RPC_PORT}."
        ),
    )
    parser.add_argument(
        "--docker-subnet",
        default=DEFAULT_DOCKER_SUBNET,
        help=f"Docker subnet for deterministic validator IPs. Default: {DEFAULT_DOCKER_SUBNET}",
    )
    parser.add_argument("--block-period-seconds", type=int, default=2, help="QBFT block period. Default: 2")
    parser.add_argument("--request-timeout-seconds", type=int, default=4, help="QBFT request timeout. Default: 4")
    parser.add_argument("--timeout-seconds", type=int, default=120, help="RPC/peer/block production wait timeout. Default: 120")
    parser.add_argument(
        "--keep-running",
        action="store_true",
        help="With smoke, leave validators running after verification. The up command always leaves them running.",
    )
    parser.add_argument(
        "--deploy-contracts",
        action="store_true",
        help="After up/restart, deploy the Main Computer contracts and publish runtime/deployments/dev/latest.json.",
    )
    parser.add_argument("--foundry-image", default=DEFAULT_FOUNDRY_IMAGE, help=f"Foundry Docker image for contract deployment. Default: {DEFAULT_FOUNDRY_IMAGE}")
    parser.add_argument("--private-key", default=DEFAULT_DEPLOYER_PRIVATE_KEY, help="Deployer private key funded in the QBFT genesis.")
    parser.add_argument("--deployment-run-id", default=None, help="Run id passed to the deployment publication. Defaults to qbft-testnet-<timestamp>.")
    parser.add_argument("--deployment-project-name", default=DEFAULT_DEPLOYMENT_PROJECT_NAME)
    parser.add_argument("--deployment-environment", default=DEFAULT_DEPLOYMENT_ENVIRONMENT)
    parser.add_argument("--deployment-output-dir", default=str(Path("runtime") / "deployments"))
    parser.add_argument(
        "--deploy",
        choices=("alpha-beta-lockout", "xlag-bridge-reserve", "hub-credit-bridge-escrow"),
        action="append",
        default=[],
        help="Deploy only a selected root contract. May be repeated. Defaults to all root contracts.",
    )
    parser.add_argument("--hub-admin-funding-wei", default="10000000000000000000")
    parser.add_argument("--max-payout-wei", default="1000000000000000000")
    parser.add_argument("--payout-delay-blocks", default="1")
    parser.add_argument("--reset-delay-blocks", default="1")
    parser.add_argument("--deploy-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--interval-seconds", type=float, default=1.0, help="Monitor polling interval. Default: 1.0")
    parser.add_argument("--verbose", action="store_true", help="Print per-validator monitor details.")
    parser.add_argument("--once", action="store_true", help="For monitor, print one status sample and exit.")

    args = parser.parse_args(remaining)
    args.command = command
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "up":
        return start_lab(args)
    if args.command == "monitor":
        return monitor(args)
    if args.command == "check":
        return check(args)
    if args.command == "down":
        return down(args)
    if args.command == "restart":
        return restart(args)
    if args.command == "smoke":
        return smoke(args)
    if args.command == "deploy":
        return deploy_testnet(args)

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
