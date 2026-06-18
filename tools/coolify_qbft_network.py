#!/usr/bin/env python3
"""Plan and render Coolify-managed Besu QBFT network layouts.

This file is intentionally self-contained.  The editable "fstab-like" object is
NETWORK_SEEDS near the top of the file.  Add a new testnet/mainnet environment by
adding a seed entry, then run:

    python tools/coolify_qbft_network.py plan testnet
    python tools/coolify_qbft_network.py write testnet --out runtime/coolify-qbft/testnet

The planner is deliberately safer than an imperative deploy script.  It first
turns a small seed table into a concrete port/host/service plan, validates that
ports are unique, and writes the Compose files that can be pasted into Coolify
Raw Docker Compose resources.  A later action can call Coolify/SSH APIs, but the
layout contract lives here.
"""

from __future__ import annotations

import argparse
import base64
import copy
import importlib.util
import datetime as _dt
import ipaddress
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any


RPC_CONTAINER_PORT = 8545
P2P_CONTAINER_PORT = 30303
DEFAULT_BESU_IMAGE = "hyperledger/besu:latest"
DEFAULT_RPC_API = "ETH,NET,QBFT,WEB3"
DEFAULT_COOLIFY_TOKEN_ENV = "MAIN_COMPUTER_COOLIFY_TOKEN"
DEFAULT_COOLIFY_API_TIMEOUT_S = 25.0
DEFAULT_COOLIFY_API_RETRIES = 1
DEFAULT_COOLIFY_API_RETRY_SLEEP_S = 2.0
DEFAULT_RPC_WAIT_TIMEOUT_S = 0.0
DEFAULT_RPC_POLL_INTERVAL_S = 2.0
DEFAULT_RPC_MIN_PEERS_AUTO = -1
DEFAULT_FOUNDRY_IMAGE = "ghcr.io/foundry-rs/foundry:latest"
DEFAULT_DEPLOY_CONTRACTS_TIMEOUT_S = 0.0
DEFAULT_DEPLOYMENT_SOURCE_KIND = "coolify-qbft-testnet-deploy"
DEFAULT_GENESIS_BASE_FEE_PER_GAS = "0x3b9aca00"
DEFAULT_FUNDED_ACCOUNT_BALANCE = "0x21e19e0c9bab2400000"  # 10000 * 10^18 wei
DEFAULT_SHANGHAI_TIME = 0
DEFAULT_FUNDED_ACCOUNTS = [
    "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
    "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
    "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
    "0x90f79bf6eb2c4f870365e785982e1f101e93b906",
]

LOCAL_COOLIFY_SEEDS = {"test"}
LOCAL_COOLIFY_DEFAULT_URL = "http://127.0.0.1:8000"
LOCAL_COOLIFY_TOKEN_RELATIVE_PATH = Path("runtime") / "coolify-local-docker" / "api-token.txt"
LOCAL_COOLIFY_DEFAULT_ENVIRONMENT = "production"
LOCAL_COOLIFY_RPC_CONTAINER_URL = f"http://mc-qbft-rpc:{RPC_CONTAINER_PORT}"
LOCAL_QBFT_SUBNET_CANDIDATES = (
    "10.241.0.0/24",
    "10.242.0.0/24",
    "10.243.0.0/24",
    "10.244.0.0/24",
    "10.245.0.0/24",
    "172.30.241.0/24",
    "172.31.241.0/24",
    "192.168.241.0/24",
    "192.168.242.0/24",
)
LOCAL_QBFT_SUBNET_PROBE_TIMEOUT_S = 5.0


# Edit this object to add new environments.  It is intentionally a hierarchical
# fstab-like table:
#
# hosts:    the remote machines that can receive Coolify resources
# services: the network nodes/apps and the host each one lives on
#
# Every service receives an explicit host port at build time.  The port remains
# part of the deployment identity even when two services live on different IPs.
# This makes generated plans easy to diff, document, and promote.
NETWORK_SEEDS: dict[str, dict[str, Any]] = {
    "test": {
        "description": "Local Coolify-managed QBFT test network with the same four-validator plus RPC shape as the smoke harness.",
        "environment": "test",
        "chain_id": 42424241,
        "compose_project": "main-computer-qbft-test",
        "docker_network": "mc-qbft-test-network",
        "docker_subnet": "10.241.0.0/24",
        "besu_image": DEFAULT_BESU_IMAGE,
        "runtime_root": "/srv/main-computer/qbft-test/runtime",
        "public_rpc": False,
        "topology_policy": {
            "minimum_validators": 4,
            "minimum_rpc_nodes": 1,
            "validator_warning_below": 4,
            "validator_warning": "Local test topology is below the configured four-validator fault-tolerance target.",
        },
        "hosts": {
            "local-coolify": {
                "ssh": "root@127.0.0.1",
                "address": "127.0.0.1",
                "coolify_url": LOCAL_COOLIFY_DEFAULT_URL,
                "runtime_root": "/srv/main-computer/qbft-test/runtime",
            }
        },
        "services": [
            {
                "id": "validator-1",
                "role": "validator",
                "host": "local-coolify",
                "container_ip": "10.241.0.11",
                "rpc_host_port": 30001,
                "p2p_host_port": 30311,
            },
            {
                "id": "validator-2",
                "role": "validator",
                "host": "local-coolify",
                "container_ip": "10.241.0.12",
                "rpc_host_port": 30002,
                "p2p_host_port": 30312,
            },
            {
                "id": "validator-3",
                "role": "validator",
                "host": "local-coolify",
                "container_ip": "10.241.0.13",
                "rpc_host_port": 30003,
                "p2p_host_port": 30313,
            },
            {
                "id": "validator-4",
                "role": "validator",
                "host": "local-coolify",
                "container_ip": "10.241.0.14",
                "rpc_host_port": 30004,
                "p2p_host_port": 30314,
            },
            {
                "id": "rpc-1",
                "role": "rpc",
                "host": "local-coolify",
                "container_ip": "10.241.0.20",
                "rpc_host_port": 30010,
                "p2p_host_port": 30320,
            },
        ],
    },
    "testnet": {
        "description": "Single-host QBFT rehearsal network managed by Coolify.",
        "environment": "testnet",
        "chain_id": 42424241,
        "compose_project": "main-computer-qbft-testnet",
        "docker_network": "mc-qbft-testnet-network",
        "docker_subnet": "172.28.241.0/24",
        "besu_image": DEFAULT_BESU_IMAGE,
        "runtime_root": "/srv/main-computer/qbft-testnet/runtime",
        "public_rpc": False,
        "topology_policy": {
            "minimum_validators": 4,
            "minimum_rpc_nodes": 1,
            "validator_warning_below": 4,
            "validator_warning": "Testnet topology is below the configured four-validator fault-tolerance target.",
        },
        "hosts": {
            "testnet-a": {
                "ssh": "root@TESTNET_MACHINE_IP",
                "address": "TESTNET_MACHINE_IP",
                "coolify_url": "https://coolify-testnet.example.com",
                "runtime_root": "/srv/main-computer/qbft-testnet/runtime",
            }
        },
        "services": [
            {
                "id": "validator-1",
                "role": "validator",
                "host": "testnet-a",
                "container_ip": "172.28.241.11",
                # Keep validator RPC private; the dedicated rpc-1 service below
                # owns the public operator RPC port when --public-rpc is used.
                "rpc_host_port": 30001,
                "p2p_host_port": 30311,
            },
            {
                "id": "validator-2",
                "role": "validator",
                "host": "testnet-a",
                "container_ip": "172.28.241.12",
                "rpc_host_port": 30002,
                "p2p_host_port": 30312,
            },
            {
                "id": "validator-3",
                "role": "validator",
                "host": "testnet-a",
                "container_ip": "172.28.241.13",
                "rpc_host_port": 30003,
                "p2p_host_port": 30313,
            },
            {
                "id": "validator-4",
                "role": "validator",
                "host": "testnet-a",
                "container_ip": "172.28.241.14",
                "rpc_host_port": 30004,
                "p2p_host_port": 30314,
            },
            {
                "id": "rpc-1",
                "role": "rpc",
                "host": "testnet-a",
                "container_ip": "172.28.241.20",
                "rpc_host_port": 30010,
                "p2p_host_port": 30320,
            },
        ],
    },
    "testnet-split-example": {
        "description": "Example showing the same seed shape with validators split across hosts.",
        "environment": "testnet",
        "chain_id": 42424241,
        "compose_project": "main-computer-qbft-testnet-split",
        "docker_network": "mc-qbft-testnet-network",
        "docker_subnet": "172.28.241.0/24",
        "besu_image": DEFAULT_BESU_IMAGE,
        "runtime_root": "/srv/main-computer/qbft-testnet/runtime",
        "public_rpc": False,
        "hosts": {
            "validator-a": {
                "ssh": "root@VALIDATOR_A_IP",
                "address": "VALIDATOR_A_IP",
                "coolify_url": "https://coolify-a.example.com",
                "runtime_root": "/srv/main-computer/qbft-testnet/runtime",
            },
            "validator-b": {
                "ssh": "root@VALIDATOR_B_IP",
                "address": "VALIDATOR_B_IP",
                "coolify_url": "https://coolify-b.example.com",
                "runtime_root": "/srv/main-computer/qbft-testnet/runtime",
            },
            "rpc-a": {
                "ssh": "root@RPC_A_IP",
                "address": "RPC_A_IP",
                "coolify_url": "https://coolify-rpc.example.com",
                "runtime_root": "/srv/main-computer/qbft-testnet/runtime",
            },
        },
        "services": [
            {"id": "validator-1", "role": "validator", "host": "validator-a", "container_ip": "172.28.241.11", "rpc_host_port": 30001, "p2p_host_port": 30311},
            {"id": "validator-2", "role": "validator", "host": "validator-a", "container_ip": "172.28.241.12", "rpc_host_port": 30002, "p2p_host_port": 30312},
            {"id": "validator-3", "role": "validator", "host": "validator-b", "container_ip": "172.28.241.13", "rpc_host_port": 30003, "p2p_host_port": 30313},
            {"id": "validator-4", "role": "validator", "host": "validator-b", "container_ip": "172.28.241.14", "rpc_host_port": 30004, "p2p_host_port": 30314},
            {"id": "rpc-1", "role": "rpc", "host": "rpc-a", "container_ip": "172.28.241.20", "rpc_host_port": 30010, "p2p_host_port": 30320},
        ],
    },
    "mainnet": {
        "description": "Single-host QBFT mainnet plan managed by Coolify; requires explicit acknowledgement.",
        "environment": "mainnet",
        "chain_id": 42424240,
        "compose_project": "main-computer-qbft-mainnet",
        "docker_network": "mc-qbft-mainnet-network",
        "docker_subnet": "172.28.242.0/24",
        "besu_image": DEFAULT_BESU_IMAGE,
        "runtime_root": "/srv/main-computer/qbft-mainnet/runtime",
        "public_rpc": False,
        "requires_mainnet_ack": True,
        "topology_policy": {
            "minimum_validators": 1,
            "minimum_rpc_nodes": 1,
            "validator_warning_below": 4,
            "validator_warning": "Mainnet is in single-validator bring-up mode; this proves the Hub/RPC/worker path but is not fault-tolerant.",
        },
        "hosts": {
            "mainnet-a": {
                "ssh": "root@MAINNET_MACHINE_IP",
                "address": "MAINNET_MACHINE_IP",
                "coolify_url": "https://coolify-mainnet.example.com",
                "runtime_root": "/srv/main-computer/qbft-mainnet/runtime",
            }
        },
        "services": [
            {"id": "validator-1", "role": "validator", "host": "mainnet-a", "container_ip": "172.28.242.11", "rpc_host_port": 31001, "p2p_host_port": 31311},
            {"id": "rpc-1", "role": "rpc", "host": "mainnet-a", "container_ip": "172.28.242.20", "rpc_host_port": 31010, "p2p_host_port": 31320},
        ],
    },
}


SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")


class PlanError(ValueError):
    """Raised when a network seed cannot be turned into a safe plan."""


class CoolifyError(RuntimeError):
    """Raised when the Coolify API cannot perform the requested sync/deploy."""


@dataclass(frozen=True)
class PlannedHost:
    id: str
    ssh: str
    address: str
    coolify_url: str
    runtime_root: str


@dataclass(frozen=True)
class PlannedService:
    id: str
    role: str
    host: str
    container_ip: str
    rpc_host_port: int
    p2p_host_port: int
    rpc_bind_host: str
    p2p_bind_host: str
    data_path: str
    static_nodes_path: str
    rpc_url_on_host: str
    p2p_advertise: str


@dataclass(frozen=True)
class TopologyPolicy:
    minimum_validators: int
    minimum_rpc_nodes: int
    validator_warning_below: int | None
    validator_warning: str


@dataclass(frozen=True)
class NetworkPlan:
    name: str
    description: str
    environment: str
    chain_id: int
    compose_project: str
    docker_network: str
    docker_subnet: str
    besu_image: str
    public_rpc: bool
    topology_policy: TopologyPolicy
    hosts: tuple[PlannedHost, ...]
    services: tuple[PlannedService, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "environment": self.environment,
            "chain_id": self.chain_id,
            "compose_project": self.compose_project,
            "docker_network": self.docker_network,
            "docker_subnet": self.docker_subnet,
            "besu_image": self.besu_image,
            "public_rpc": self.public_rpc,
            "topology_policy": asdict(self.topology_policy),
            "hosts": [asdict(host) for host in self.hosts],
            "services": [asdict(service) for service in self.services],
            "warnings": list(self.warnings),
            "operator_checks": operator_checks(self),
        }


def safe_id(value: object, *, kind: str) -> str:
    text = str(value or "").strip().lower()
    if not SAFE_ID_RE.match(text):
        raise PlanError(f"Invalid {kind} id {value!r}; use lowercase letters, numbers, '.', '_' or '-'.")
    return text


def require_int(value: object, *, name: str, minimum: int = 1, maximum: int = 65535) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise PlanError(f"{name} must be an integer") from exc
    if number < minimum or number > maximum:
        raise PlanError(f"{name} must be between {minimum} and {maximum}: {number}")
    return number


def parse_topology_policy(seed: dict[str, Any]) -> TopologyPolicy:
    raw = seed.get("topology_policy") or {}
    if not isinstance(raw, dict):
        raise PlanError("seed.topology_policy must be an object when present")

    minimum_validators = require_int(
        raw.get("minimum_validators", 1),
        name="topology_policy.minimum_validators",
        minimum=1,
        maximum=100,
    )
    minimum_rpc_nodes = require_int(
        raw.get("minimum_rpc_nodes", 0),
        name="topology_policy.minimum_rpc_nodes",
        minimum=0,
        maximum=100,
    )

    warning_below_raw = raw.get("validator_warning_below")
    validator_warning_below: int | None
    if warning_below_raw is None:
        validator_warning_below = None
    else:
        validator_warning_below = require_int(
            warning_below_raw,
            name="topology_policy.validator_warning_below",
            minimum=1,
            maximum=100,
        )

    validator_warning = str(raw.get("validator_warning") or "").strip()
    return TopologyPolicy(
        minimum_validators=minimum_validators,
        minimum_rpc_nodes=minimum_rpc_nodes,
        validator_warning_below=validator_warning_below,
        validator_warning=validator_warning,
    )



def parse_target_address(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    if text.startswith("[") and "]" in text:
        return text[1:text.index("]")]
    if ":" in text and text.count(":") == 1:
        return text.split(":", 1)[0]
    return text


def load_seed(name_or_path: str) -> tuple[str, dict[str, Any]]:
    if name_or_path in NETWORK_SEEDS:
        return name_or_path, copy.deepcopy(NETWORK_SEEDS[name_or_path])
    path = Path(name_or_path)
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        name = str(data.get("name") or path.stem).strip().lower()
        return safe_id(name, kind="network"), data
    raise PlanError(f"Unknown network seed {name_or_path!r}. Known seeds: {', '.join(sorted(NETWORK_SEEDS))}")


def validate_runtime_root(value: object, *, host_id: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text.startswith("/"):
        raise PlanError(f"runtime_root for host {host_id!r} must be an absolute POSIX path")
    if "/../" in f"{text}/" or text in {"", "/"}:
        raise PlanError(f"runtime_root for host {host_id!r} is unsafe: {text!r}")
    return text.rstrip("/")


def build_plan(
    seed_name_or_path: str,
    *,
    besu_image: str | None = None,
    allow_mainnet: bool = False,
    public_rpc: bool | None = None,
    single_host: str | None = None,
    target_address: str | None = None,
    coolify_url: str | None = None,
    runtime_root: str | None = None,
) -> NetworkPlan:
    name, seed = load_seed(seed_name_or_path)

    if seed.get("requires_mainnet_ack") and not allow_mainnet:
        raise PlanError("Mainnet seed requires --allow-mainnet. Review the plan before real mainnet use.")

    chain_id = require_int(seed.get("chain_id"), name="chain_id", maximum=2**63 - 1)
    topology_policy = parse_topology_policy(seed)
    environment = str(seed.get("environment") or "test").strip().lower()
    compose_project = safe_id(seed.get("compose_project") or f"main-computer-qbft-{name}", kind="compose_project")
    docker_network = safe_id(seed.get("docker_network") or f"{compose_project}-network", kind="docker_network")
    docker_subnet = str(seed.get("docker_subnet") or "").strip()
    try:
        subnet = ipaddress.ip_network(docker_subnet, strict=False)
    except ValueError as exc:
        raise PlanError(f"docker_subnet is invalid: {docker_subnet!r}") from exc

    raw_hosts = seed.get("hosts")
    if not isinstance(raw_hosts, dict) or not raw_hosts:
        raise PlanError("seed.hosts must be a non-empty object")

    if single_host:
        host_keys = list(raw_hosts)
        if len(host_keys) != 1:
            raise PlanError("--single-host can only override seeds with exactly one host")
        host_key = host_keys[0]
        target_host = parse_target_address(single_host)
        raw_hosts = copy.deepcopy(raw_hosts)
        raw_hosts[host_key]["ssh"] = single_host
        raw_hosts[host_key]["address"] = target_address or target_host
        if coolify_url:
            raw_hosts[host_key]["coolify_url"] = coolify_url
        elif not str(raw_hosts[host_key].get("coolify_url") or "").strip() or "example.com" in str(raw_hosts[host_key].get("coolify_url")):
            raw_hosts[host_key]["coolify_url"] = f"http://{target_host}:8000"

    default_runtime_root = validate_runtime_root(runtime_root or seed.get("runtime_root") or "/srv/main-computer/qbft/runtime", host_id="default")
    hosts: dict[str, PlannedHost] = {}
    for raw_host_id, raw_host in raw_hosts.items():
        host_id = safe_id(raw_host_id, kind="host")
        if not isinstance(raw_host, dict):
            raise PlanError(f"host {host_id!r} must be an object")
        runtime_root = validate_runtime_root(raw_host.get("runtime_root") or default_runtime_root, host_id=host_id)
        hosts[host_id] = PlannedHost(
            id=host_id,
            ssh=str(raw_host.get("ssh") or "").strip(),
            address=str(raw_host.get("address") or "").strip(),
            coolify_url=str(raw_host.get("coolify_url") or "").strip(),
            runtime_root=runtime_root,
        )

    effective_public_rpc = bool(seed.get("public_rpc", False) if public_rpc is None else public_rpc)
    p2p_bind_host = "0.0.0.0"

    raw_services = seed.get("services")
    if not isinstance(raw_services, list) or not raw_services:
        raise PlanError("seed.services must be a non-empty list")
    has_dedicated_rpc = any(
        isinstance(raw, dict) and str(raw.get("role") or "").strip().lower() == "rpc"
        for raw in raw_services
    )

    seen_ids: set[str] = set()
    seen_global_ports: dict[int, str] = {}
    seen_container_ips: set[str] = set()
    services: list[PlannedService] = []
    for raw in raw_services:
        if not isinstance(raw, dict):
            raise PlanError("Each service entry must be an object")
        service_id = safe_id(raw.get("id"), kind="service")
        if service_id in seen_ids:
            raise PlanError(f"Duplicate service id: {service_id}")
        seen_ids.add(service_id)

        role = str(raw.get("role") or "").strip().lower()
        if role not in {"validator", "rpc"}:
            raise PlanError(f"Unsupported role for {service_id}: {role!r}")

        host_id = safe_id(raw.get("host"), kind="host")
        if host_id not in hosts:
            raise PlanError(f"Service {service_id} refers to unknown host {host_id!r}")

        container_ip = str(raw.get("container_ip") or "").strip()
        try:
            parsed_ip = ipaddress.ip_address(container_ip)
        except ValueError as exc:
            raise PlanError(f"container_ip for {service_id} is invalid: {container_ip!r}") from exc
        if parsed_ip not in subnet:
            raise PlanError(f"container_ip for {service_id} is not in docker_subnet {docker_subnet}: {container_ip}")
        if container_ip in seen_container_ips:
            raise PlanError(f"Duplicate container_ip: {container_ip}")
        seen_container_ips.add(container_ip)

        rpc_port = require_int(raw.get("rpc_host_port"), name=f"{service_id}.rpc_host_port")
        p2p_port = require_int(raw.get("p2p_host_port"), name=f"{service_id}.p2p_host_port")
        for port, purpose in [(rpc_port, "rpc"), (p2p_port, "p2p")]:
            if port in seen_global_ports:
                raise PlanError(
                    f"Host port {port} is reused by {service_id}.{purpose}; "
                    f"already assigned to {seen_global_ports[port]}"
                )
            seen_global_ports[port] = f"{service_id}.{purpose}"

        runtime_root = hosts[host_id].runtime_root
        role_runtime_dir = "rpc-node" if role == "rpc" else service_id
        service_public_rpc = bool(effective_public_rpc and (role == "rpc" or (role == "validator" and not has_dedicated_rpc)))
        service_rpc_bind_host = "0.0.0.0" if service_public_rpc else "127.0.0.1"
        service_rpc_host = hosts[host_id].address if service_public_rpc and hosts[host_id].address else "127.0.0.1"
        services.append(
            PlannedService(
                id=service_id,
                role=role,
                host=host_id,
                container_ip=container_ip,
                rpc_host_port=rpc_port,
                p2p_host_port=p2p_port,
                rpc_bind_host=service_rpc_bind_host,
                p2p_bind_host=p2p_bind_host,
                data_path=f"{runtime_root}/{role_runtime_dir}/data",
                static_nodes_path=f"{runtime_root}/{role_runtime_dir}/static-nodes.json",
                rpc_url_on_host=f"http://{service_rpc_host}:{rpc_port}",
                p2p_advertise=f"{hosts[host_id].address or host_id}:{p2p_port}",
            )
        )

    validators = [service for service in services if service.role == "validator"]
    rpc_nodes = [service for service in services if service.role == "rpc"]
    if not validators:
        raise PlanError("QBFT seed must define at least one validator")
    if len(validators) < topology_policy.minimum_validators:
        raise PlanError(
            f"Seed {name!r} violates topology_policy.minimum_validators="
            f"{topology_policy.minimum_validators}; found {len(validators)} validators"
        )
    if len(rpc_nodes) < topology_policy.minimum_rpc_nodes:
        raise PlanError(
            f"Seed {name!r} violates topology_policy.minimum_rpc_nodes="
            f"{topology_policy.minimum_rpc_nodes}; found {len(rpc_nodes)} rpc nodes"
        )

    warnings: list[str] = []
    image = str(besu_image or seed.get("besu_image") or DEFAULT_BESU_IMAGE).strip()
    if image.endswith(":latest"):
        warnings.append("BESU image is using ':latest'. Pin a known-good Besu image tag before persistent remote use.")
    if effective_public_rpc:
        warnings.append("public_rpc=true binds RPC ports to 0.0.0.0. Add TLS/rate-limit/method policy before opening firewalls.")
    if (
        topology_policy.validator_warning_below is not None
        and len(validators) < topology_policy.validator_warning_below
    ):
        warnings.append(
            topology_policy.validator_warning
            or (
                f"Topology has fewer than {topology_policy.validator_warning_below} validators; "
                "review the seed topology_policy before persistent use."
            )
        )
    if not rpc_nodes:
        warnings.append("No dedicated non-validator RPC node is configured; the first validator is also the operator RPC target.")
    if len({service.host for service in services}) > 1:
        warnings.append(
            "Services span multiple hosts. Generate validator enodes after keys exist and publish p2p ports between hosts."
        )

    return NetworkPlan(
        name=name,
        description=str(seed.get("description") or ""),
        environment=environment,
        chain_id=chain_id,
        compose_project=compose_project,
        docker_network=docker_network,
        docker_subnet=docker_subnet,
        besu_image=image,
        public_rpc=effective_public_rpc,
        topology_policy=topology_policy,
        hosts=tuple(sorted(hosts.values(), key=lambda item: item.id)),
        services=tuple(sorted(services, key=lambda item: item.id)),
        warnings=tuple(warnings),
    )


def normalize_ipv4_subnet(value: object) -> ipaddress.IPv4Network:
    try:
        network = ipaddress.ip_network(str(value or "").strip(), strict=False)
    except ValueError as exc:
        raise PlanError(f"docker_subnet is invalid: {value!r}") from exc
    if network.version != 4:
        raise PlanError(f"docker_subnet must be IPv4: {value!r}")
    return network


def docker_network_ipv4_subnets(*, timeout_s: float = LOCAL_QBFT_SUBNET_PROBE_TIMEOUT_S) -> list[ipaddress.IPv4Network]:
    """Return Docker bridge/custom IPv4 subnets visible to the host Docker daemon.

    Local ``test`` deployment must not hand Coolify a subnet that Docker already
    considers occupied.  Coolify accepts the service update asynchronously, so a
    bad subnet otherwise fails later inside a queued CoolifyTask and leaves the
    operator waiting on an RPC port that will never open.
    """

    try:
        listing = subprocess.run(
            ["docker", "network", "ls", "-q"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if listing.returncode != 0:
        return []

    network_ids = [line.strip() for line in listing.stdout.splitlines() if line.strip()]
    if not network_ids:
        return []

    try:
        inspected = subprocess.run(
            ["docker", "network", "inspect", *network_ids],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if inspected.returncode != 0:
        return []

    try:
        payload = json.loads(inspected.stdout or "[]")
    except json.JSONDecodeError:
        return []

    networks: list[ipaddress.IPv4Network] = []
    for item in payload if isinstance(payload, list) else []:
        ipam = item.get("IPAM") if isinstance(item, dict) else None
        configs = ipam.get("Config") if isinstance(ipam, dict) else None
        if not isinstance(configs, list):
            continue
        for config in configs:
            if not isinstance(config, dict):
                continue
            subnet_text = str(config.get("Subnet") or "").strip()
            if not subnet_text:
                continue
            try:
                network = ipaddress.ip_network(subnet_text, strict=False)
            except ValueError:
                continue
            if network.version == 4:
                networks.append(network)
    return networks


def choose_non_overlapping_subnet(
    preferred: ipaddress.IPv4Network,
    existing: list[ipaddress.IPv4Network],
    *,
    candidates: tuple[str, ...] = LOCAL_QBFT_SUBNET_CANDIDATES,
) -> ipaddress.IPv4Network | None:
    seen: set[str] = set()
    candidate_networks: list[ipaddress.IPv4Network] = []
    for value in (str(preferred), *candidates):
        try:
            candidate = normalize_ipv4_subnet(value)
        except PlanError:
            continue
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        candidate_networks.append(candidate)

    for candidate in candidate_networks:
        if not any(candidate.overlaps(network) for network in existing):
            return candidate
    return None


def plan_with_docker_subnet(plan: NetworkPlan, docker_subnet: str) -> NetworkPlan:
    new_subnet = normalize_ipv4_subnet(docker_subnet)
    old_subnet = normalize_ipv4_subnet(plan.docker_subnet)
    if str(new_subnet) == str(old_subnet):
        return plan
    if new_subnet.num_addresses < 21:
        raise PlanError(f"docker_subnet must have room for validator/RPC static IP offsets .11-.20: {docker_subnet!r}")

    new_services: list[PlannedService] = []
    for service in plan.services:
        old_ip = ipaddress.ip_address(service.container_ip)
        offset = int(old_ip) - int(old_subnet.network_address)
        if offset < 0 or offset >= old_subnet.num_addresses:
            raise PlanError(f"container_ip for {service.id} is not in docker_subnet {plan.docker_subnet}: {service.container_ip}")
        new_ip = ipaddress.ip_address(int(new_subnet.network_address) + offset)
        if new_ip not in new_subnet:
            raise PlanError(f"replacement container_ip for {service.id} would fall outside {docker_subnet!r}")
        new_services.append(replace(service, container_ip=str(new_ip)))

    return replace(plan, docker_subnet=str(new_subnet), services=tuple(sorted(new_services, key=lambda item: item.id)))


def prepare_local_qbft_subnet(plan: NetworkPlan, args: argparse.Namespace) -> tuple[NetworkPlan, dict[str, Any]]:
    """Repair the local Coolify QBFT subnet before sending compose to Coolify."""

    if not is_local_coolify_plan(plan):
        return plan, {"ok": True, "skipped": True, "reason": "not-local-coolify"}

    override = str(getattr(args, "docker_subnet", "") or "").strip()
    existing = docker_network_ipv4_subnets()
    existing_text = [str(item) for item in existing]

    if override:
        updated = plan_with_docker_subnet(plan, override)
        selected = normalize_ipv4_subnet(updated.docker_subnet)
        overlaps = [str(item) for item in existing if selected.overlaps(item)]
        if overlaps:
            return updated, {
                "ok": False,
                "requested_subnet": str(selected),
                "overlaps": overlaps,
                "existing_subnets": existing_text,
                "message": f"Requested local QBFT Docker subnet {selected} overlaps existing Docker network subnet(s): {', '.join(overlaps)}",
            }
        return updated, {
            "ok": True,
            "changed": str(selected) != plan.docker_subnet,
            "selected_subnet": str(selected),
            "source": "operator-override",
            "existing_subnets": existing_text,
        }

    preferred = normalize_ipv4_subnet(plan.docker_subnet)
    overlaps = [item for item in existing if preferred.overlaps(item)]
    if not overlaps:
        return plan, {
            "ok": True,
            "changed": False,
            "selected_subnet": str(preferred),
            "source": "seed",
            "existing_subnets": existing_text,
        }

    selected = choose_non_overlapping_subnet(preferred, existing)
    if selected is None:
        return plan, {
            "ok": False,
            "requested_subnet": str(preferred),
            "overlaps": [str(item) for item in overlaps],
            "existing_subnets": existing_text,
            "candidates": list(LOCAL_QBFT_SUBNET_CANDIDATES),
            "message": "No non-overlapping local QBFT Docker subnet was available from the built-in candidate list.",
        }

    updated = plan_with_docker_subnet(plan, str(selected))
    return updated, {
        "ok": True,
        "changed": True,
        "selected_subnet": str(selected),
        "previous_subnet": str(preferred),
        "source": "auto-repair",
        "overlaps": [str(item) for item in overlaps],
        "existing_subnets": existing_text,
    }


def host_by_id(plan: NetworkPlan) -> dict[str, PlannedHost]:
    return {host.id: host for host in plan.hosts}


def services_for_host(plan: NetworkPlan, host_id: str) -> list[PlannedService]:
    return [service for service in plan.services if service.host == host_id]


def rpc_target_service(plan: NetworkPlan) -> PlannedService:
    """Return the service that operator tooling should use for JSON-RPC.

    Prefer a dedicated non-validator RPC node.  Low-resource testnets may omit
    that node; in that case the first validator is the RPC endpoint.
    """

    rpc_nodes = [service for service in plan.services if service.role == "rpc"]
    if rpc_nodes:
        return rpc_nodes[0]
    validators = [service for service in plan.services if service.role == "validator"]
    if validators:
        return validators[0]
    raise PlanError("Plan has no service that can serve JSON-RPC")


def yaml_quote(value: object) -> str:
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def service_runtime_dir(service: PlannedService) -> str:
    return "rpc-node" if service.role == "rpc" else service.id



def genesis_alloc() -> dict[str, dict[str, str]]:
    return {address.lower().removeprefix("0x"): {"balance": DEFAULT_FUNDED_ACCOUNT_BALANCE} for address in DEFAULT_FUNDED_ACCOUNTS}


def qbft_config(plan: NetworkPlan) -> dict[str, Any]:
    validators = [service for service in plan.services if service.role == "validator"]
    return {
        "genesis": {
            "config": {
                "chainId": plan.chain_id,
                "berlinBlock": 0,
                "londonBlock": 0,
                "shanghaiTime": DEFAULT_SHANGHAI_TIME,
                "qbft": {
                    "blockperiodseconds": 2,
                    "epochlength": 30000,
                    "requesttimeoutseconds": 4,
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
                "count": len(validators),
            }
        },
    }


def managed_volume_name(plan: NetworkPlan, host_id: str) -> str:
    return safe_id(f"{plan.compose_project}-{host_id}-runtime", kind="volume")


def render_json_heredoc(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def escape_compose_interpolation(value: str) -> str:
    """Escape shell dollars before embedding scripts in Docker Compose YAML.

    Docker Compose performs ${VAR}, $VAR, ${#VAR}, and $(...) interpolation while
    parsing YAML.  The qbft-bootstrap script must receive those dollar
    expressions at container runtime, so every dollar in the block scalar needs
    to be doubled for Compose and restored by Compose before /bin/sh sees it.
    """

    return value.replace("$", "$$")


def render_static_nodes_shell(all_nodes: list[str], output_path: str, *, exclude: str | None = None) -> list[str]:
    keep = [node for node in all_nodes if node != exclude]
    if not keep:
        return [f"printf '[]\\n' > {output_path}"]
    lines = [f"{{", "  printf '[\\n'"]
    for index, node in enumerate(keep):
        suffix = "," if index < len(keep) - 1 else ""
        lines.append(f"  printf '  \"%s\"{suffix}\\n' \"${node}\"")
    lines.extend(["  printf ']\\n'", f"}} > {output_path}"])
    return lines


def render_bootstrap_shell(plan: NetworkPlan) -> str:
    validators = [service for service in plan.services if service.role == "validator"]
    rpc_nodes = [service for service in plan.services if service.role == "rpc"]
    if not validators:
        raise PlanError("Cannot render bootstrap service without validators.")

    config = render_json_heredoc(qbft_config(plan))
    lines: list[str] = [
        "set -eu",
        "echo \"Main Computer QBFT bootstrap starting for " + plan.name + "\"",
        "mkdir -p /smoke",
        f"EXPECTED_QBFT_VALIDATOR_COUNT={len(validators)}",
        f"EXPECTED_QBFT_RPC_COUNT={len(rpc_nodes)}",
        "REGENERATE_QBFT_TOPOLOGY=false",
        "if [ -f /smoke/network-metadata.json ] && [ \"${QBFT_RESET_CHAIN:-false}\" != \"true\" ]; then",
        "  EXISTING_QBFT_VALIDATOR_COUNT=$(grep -c '\"id\": \"validator-' /smoke/network-metadata.json || true)",
        "  EXISTING_QBFT_RPC_COUNT=$(grep -c '\"id\": \"rpc-' /smoke/network-metadata.json || true)",
        "  EXISTING_QBFT_METADATA_MATCH=false",
        "  if grep -q '\"network\": \"" + plan.name + "\"' /smoke/network-metadata.json && grep -q '\"docker_network\": \"" + plan.docker_network + "\"' /smoke/network-metadata.json && grep -q '\"docker_subnet\": \"" + plan.docker_subnet + "\"' /smoke/network-metadata.json; then",
        "    EXISTING_QBFT_METADATA_MATCH=true",
        "  fi",
        "  if [ \"$EXISTING_QBFT_METADATA_MATCH\" = \"true\" ] && [ \"$EXISTING_QBFT_VALIDATOR_COUNT\" = \"$EXPECTED_QBFT_VALIDATOR_COUNT\" ] && [ \"$EXISTING_QBFT_RPC_COUNT\" = \"$EXPECTED_QBFT_RPC_COUNT\" ]; then",
        "    echo \"Existing QBFT network metadata matches desired topology; reusing persistent genesis/key material.\"",
        "    exit 0",
        "  fi",
        "  if [ \"$EXISTING_QBFT_METADATA_MATCH\" = \"true\" ] && [ \"$EXISTING_QBFT_VALIDATOR_COUNT\" = \"$EXPECTED_QBFT_VALIDATOR_COUNT\" ] && [ \"$EXISTING_QBFT_RPC_COUNT\" = \"0\" ] && [ \"$EXPECTED_QBFT_RPC_COUNT\" != \"0\" ] && [ -f /smoke/genesis.json ] && [ -f /smoke/static-nodes-all.json ]; then",
        "    echo \"Existing validator topology matches; adding dedicated RPC runtime material without regenerating validator keys.\"",
    ]
    for service in rpc_nodes:
        runtime_dir = service_runtime_dir(service)
        lines.extend(
            [
                f"    mkdir -p /smoke/{runtime_dir}/data",
                f"    cp /smoke/static-nodes-all.json /smoke/{runtime_dir}/static-nodes.json",
            ]
        )
    lines.extend(
        [
            "    tmp=/smoke/network-metadata.json.tmp",
            "    head -n -1 /smoke/network-metadata.json > \"$tmp\"",
            "    printf ',\\n' >> \"$tmp\"",
            "    printf '  \"rpc_nodes\": [\\n' >> \"$tmp\"",
        ]
    )
    for index, service in enumerate(rpc_nodes, start=1):
        comma = "," if index < len(rpc_nodes) else ""
        lines.append(f"    printf '    {{\"id\": \"{service.id}\", \"ip\": \"{service.container_ip}\"}}{comma}\\n' >> \"$tmp\"")
    lines.extend(
        [
            "    printf '  ]\\n' >> \"$tmp\"",
            "    printf '}\\n' >> \"$tmp\"",
            "    mv \"$tmp\" /smoke/network-metadata.json",
            "    exit 0",
            "  fi",
            "  echo \"Existing QBFT network metadata does not match desired topology; regenerating genesis/key material.\"",
            "  REGENERATE_QBFT_TOPOLOGY=true",
            "fi",
            "if [ -f /smoke/genesis.json ] && [ \"${QBFT_RESET_CHAIN:-false}\" != \"true\" ] && [ \"$REGENERATE_QBFT_TOPOLOGY\" != \"true\" ]; then",
            "  echo \"genesis.json exists but metadata is missing; refusing to guess state. Set QBFT_RESET_CHAIN=true to regenerate.\" >&2",
            "  exit 23",
            "fi",
            "rm -rf /tmp/qbft-networkFiles /smoke/networkFiles",
            "cat > /smoke/qbftConfigFile.json <<'JSON'",
            config,
            "JSON",
            "if command -v besu >/dev/null 2>&1; then BESU=besu; else BESU=/opt/besu/bin/besu; fi",
            "\"$BESU\" operator generate-blockchain-config --config-file=/smoke/qbftConfigFile.json --to=/tmp/qbft-networkFiles --private-key-file-name=key",
            f"set -- $(find /tmp/qbft-networkFiles/keys -mindepth 1 -maxdepth 1 -type d | sort)",
            f"if [ \"$#\" -ne {len(validators)} ]; then echo \"expected {len(validators)} validator key directories, got $#\" >&2; exit 24; fi",
        ]
    )
    for index, _service in enumerate(validators, start=1):
        lines.append(f"KEY_DIR_{index}=\"${index}\"")

    lines.extend(
        [
            "normalize_pubkey() {",
            "  pub=$(tr -d '\\r\\n ' < \"$1\")",
            "  pub=${pub#0x}",
            "  case \"$pub\" in 04*) if [ \"${#pub}\" -eq 130 ]; then pub=${pub#04}; fi;; esac",
            "  case \"$pub\" in (*[!0-9a-fA-F]*|'') echo \"invalid public key in $1\" >&2; exit 25;; esac",
            "  if [ \"${#pub}\" -ne 128 ]; then echo \"expected 128 hex chars in $1, got ${#pub}\" >&2; exit 26; fi",
            "  printf '%s' \"$pub\"",
            "}",
        ]
    )

    for index, service in enumerate(validators, start=1):
        lines.extend(
            [
                f"mkdir -p /smoke/{service.id}/data",
                f"cp \"$KEY_DIR_{index}/key\" /smoke/{service.id}/data/key",
                f"cp \"$KEY_DIR_{index}/key.pub\" /smoke/{service.id}/data/key.pub",
                f"PUB_{index}=$(normalize_pubkey \"$KEY_DIR_{index}/key.pub\")",
                f"ENODE_{index}=\"enode://${{PUB_{index}}}@{service.container_ip}:{P2P_CONTAINER_PORT}\"",
            ]
        )

    enode_names = [f"ENODE_{index}" for index in range(1, len(validators) + 1)]
    lines.extend(render_static_nodes_shell(enode_names, "/smoke/static-nodes-all.json"))
    for index, service in enumerate(validators, start=1):
        lines.extend(render_static_nodes_shell(enode_names, f"/smoke/{service.id}/static-nodes.json", exclude=f"ENODE_{index}"))

    for service in rpc_nodes:
        runtime_dir = service_runtime_dir(service)
        lines.append(f"mkdir -p /smoke/{runtime_dir}/data")
        lines.extend(render_static_nodes_shell(enode_names, f"/smoke/{runtime_dir}/static-nodes.json"))

    lines.extend(
        [
            "cp /tmp/qbft-networkFiles/genesis.json /smoke/genesis.json",
            "cat > /smoke/network-metadata.json <<JSON",
            "{",
            f"  \"schema\": \"main-computer.coolify-qbft-network.v1\",",
            f"  \"network\": \"{plan.name}\",",
            f"  \"chain_id\": {plan.chain_id},",
            f"  \"compose_project\": \"{plan.compose_project}\",",
            f"  \"docker_network\": \"{plan.docker_network}\",",
            f"  \"docker_subnet\": \"{plan.docker_subnet}\",",
            "  \"validators\": [",
        ]
    )
    for index, service in enumerate(validators, start=1):
        comma = "," if index < len(validators) else ""
        lines.append(f"    {{\"id\": \"{service.id}\", \"ip\": \"{service.container_ip}\", \"enode\": \"$ENODE_{index}\"}}{comma}")
    lines.extend(
        [
            "  ],",
            "  \"rpc_nodes\": [",
        ]
    )
    for index, service in enumerate(rpc_nodes, start=1):
        comma = "," if index < len(rpc_nodes) else ""
        lines.append(f"    {{\"id\": \"{service.id}\", \"ip\": \"{service.container_ip}\"}}{comma}")
    lines.extend(
        [
            "  ]",
            "}",
            "JSON",
            "echo \"Main Computer QBFT bootstrap complete.\"",
        ]
    )
    return "\n".join(lines)


def service_should_publish_p2p(plan: NetworkPlan, service: PlannedService) -> bool:
    return service.role == "validator" and len({item.host for item in plan.services}) > 1


def service_should_publish_rpc(plan: NetworkPlan, service: PlannedService) -> bool:
    del plan
    return service.role == "rpc" or service.rpc_bind_host != ""


def render_volume_mount(plan: NetworkPlan, host: PlannedHost, *, managed_volume: bool) -> tuple[str, list[str]]:
    if managed_volume:
        volume_name = managed_volume_name(plan, host.id)
        return volume_name, [f"      - {yaml_quote(f'{volume_name}:/smoke')}"]
    return "", [
        "      - type: bind",
        f"        source: {yaml_quote(host.runtime_root)}",
        "        target: /smoke",
        "        is_directory: true",
    ]


def render_besu_service_shell(plan: NetworkPlan, service: PlannedService) -> str:
    runtime_dir = service_runtime_dir(service)
    required_files = [
        "/smoke/genesis.json",
        f"/smoke/{runtime_dir}/static-nodes.json",
    ]
    if service.role == "validator":
        required_files.append(f"/smoke/{runtime_dir}/data/key")

    ready_test = " && ".join(f"[ -f {shlex.quote(path)} ]" for path in required_files)
    lines: list[str] = [
        "set -eu",
        f"echo \"Starting {service.id}; waiting for QBFT bootstrap files.\"",
        "i=0",
        "while [ \"$i\" -lt 120 ]; do",
        f"  if {ready_test}; then break; fi",
        "  i=$((i + 1))",
        f"  if [ \"$i\" -eq 1 ] || [ $((i % 10)) -eq 0 ]; then echo \"waiting for QBFT bootstrap files for {service.id} ($i/120)\"; fi",
        "  sleep 2",
        "done",
    ]
    for path in required_files:
        lines.append(f"[ -f {shlex.quote(path)} ] || {{ echo \"missing required QBFT bootstrap file: {path}\" >&2; exit 30; }}")
    lines.extend(
        [
            "if command -v besu >/dev/null 2>&1; then BESU=besu; else BESU=/opt/besu/bin/besu; fi",
            'exec "$BESU" \\',
        ]
    )
    args = [
        f"--data-path=/smoke/{runtime_dir}/data",
        "--genesis-file=/smoke/genesis.json",
        f"--network-id={plan.chain_id}",
        "--rpc-http-enabled=true",
        "--rpc-http-host=0.0.0.0",
        f"--rpc-http-port={RPC_CONTAINER_PORT}",
        f"--rpc-http-api={DEFAULT_RPC_API}",
        "--host-allowlist=*",
        "--rpc-http-cors-origins=all",
        "--profile=ENTERPRISE",
        "--min-gas-price=0",
        f"--p2p-port={P2P_CONTAINER_PORT}",
        f"--p2p-host={service.container_ip}",
        "--nat-method=NONE",
        "--discovery-enabled=false",
        f"--static-nodes-file=/smoke/{runtime_dir}/static-nodes.json",
    ]
    for index, arg in enumerate(args):
        suffix = " \\" if index < len(args) - 1 else ""
        lines.append(f"  {shlex.quote(arg)}{suffix}")
    return "\n".join(lines)


def render_compose_for_host(
    plan: NetworkPlan,
    host_id: str,
    *,
    include_bootstrap: bool = True,
    managed_volume: bool = True,
) -> str:
    hosts = host_by_id(plan)
    if host_id not in hosts:
        raise PlanError(f"Unknown host for compose rendering: {host_id}")
    host = hosts[host_id]
    services = services_for_host(plan, host_id)
    if not services:
        raise PlanError(f"No services are assigned to host {host_id!r}")

    if include_bootstrap and len({item.host for item in plan.services}) > 1:
        raise PlanError("bootstrap-in-compose currently supports single-host deployments only; split-host requires explicit key distribution.")

    lines: list[str] = [
        f"name: {plan.compose_project}-{host_id}",
        "",
        "services:",
    ]

    volume_name, volume_lines = render_volume_mount(plan, host, managed_volume=managed_volume)
    rpc_target = rpc_target_service(plan)
    if include_bootstrap:
        bootstrap_script = escape_compose_interpolation(render_bootstrap_shell(plan))
        lines.extend(
            [
                "  qbft-bootstrap:",
                f"    image: {yaml_quote(plan.besu_image)}",
                "    restart: \"no\"",
                "    exclude_from_hc: true",
                "    environment:",
                "      - QBFT_RESET_CHAIN=${QBFT_RESET_CHAIN:-false}",
                "    entrypoint:",
                "      - /bin/sh",
                "      - -ec",
                "      - |-",
            ]
        )
        lines.extend([f"        {line}" if line else "" for line in bootstrap_script.splitlines()])
        lines.extend(
            [
                "    volumes:",
                *volume_lines,
            ]
        )
        lines.append("")

    for service in services:
        runtime_dir = service_runtime_dir(service)
        service_name = service.id.replace("_", "-")
        lines.extend(
            [
                f"  {service_name}:",
                f"    image: {yaml_quote(plan.besu_image)}",
                "    restart: unless-stopped",
            ]
        )
        port_lines: list[str] = []
        if service.role == "rpc" or service.rpc_bind_host == "0.0.0.0":
            port_lines.append(f"      - {yaml_quote(f'{service.rpc_bind_host}:{service.rpc_host_port}:{RPC_CONTAINER_PORT}')}")
        elif service.role == "validator":
            port_lines.append(f"      - {yaml_quote(f'{service.rpc_bind_host}:{service.rpc_host_port}:{RPC_CONTAINER_PORT}')}")
        if service_should_publish_p2p(plan, service):
            port_lines.append(f"      - {yaml_quote(f'{service.p2p_bind_host}:{service.p2p_host_port}:{P2P_CONTAINER_PORT}')}")
        if port_lines:
            lines.append("    ports:")
            lines.extend(port_lines)
        if include_bootstrap:
            lines.extend(
                [
                    "    depends_on:",
                    "      qbft-bootstrap:",
                    "        condition: service_completed_successfully",
                ]
            )
        besu_shell = escape_compose_interpolation(render_besu_service_shell(plan, service))
        lines.extend(
            [
                "    volumes:",
                *volume_lines,
                "    entrypoint:",
                "      - /bin/sh",
                "      - -ec",
                "      - |-",
            ]
        )
        lines.extend([f"        {line}" if line else "" for line in besu_shell.splitlines()])
        lines.extend(
            [
                "    networks:",
                "      qbft:",
                f"        ipv4_address: {service.container_ip}",
            ]
        )
        if service.id == rpc_target.id:
            lines.extend(
                [
                    "        aliases:",
                    "          - mc-qbft-rpc",
                ]
            )
        lines.append("")

    lines.extend(
        [
            "networks:",
            "  qbft:",
            f"    name: {plan.docker_network}",
            "    driver: bridge",
            "    ipam:",
            "      config:",
            f"        - subnet: {plan.docker_subnet}",
            "",
        ]
    )
    if managed_volume:
        lines.extend(
            [
                "volumes:",
                f"  {volume_name}:",
                f"    name: {volume_name}",
                "",
            ]
        )
    return "\n".join(lines)


def operator_checks(plan: NetworkPlan) -> dict[str, Any]:
    ports = sorted({service.rpc_host_port for service in plan.services} | {service.p2p_host_port for service in plan.services})
    grep_ports = "|".join(str(port) for port in ports)
    first_rpc = rpc_target_service(plan)
    return {
        "preflight_ports": ports,
        "preflight_command": f"sudo ss -tulpn | grep -E ':({grep_ports})\\\\b' || true",
        "first_rpc_url": first_rpc.rpc_url_on_host,
        "chain_id_probe": (
            f"curl -s {first_rpc.rpc_url_on_host} "
            "-H 'content-type: application/json' "
            "--data '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"eth_chainId\",\"params\":[]}'"
        ),
        "block_probe": (
            f"curl -s {first_rpc.rpc_url_on_host} "
            "-H 'content-type: application/json' "
            "--data '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"eth_blockNumber\",\"params\":[]}'"
        ),
    }


def render_commands(plan: NetworkPlan) -> str:
    checks = operator_checks(plan)
    lines = [
        f"# Operator checks for {plan.name}",
        "",
        "# Run on every planned host before deploying the Coolify Compose resource:",
        checks["preflight_command"],
        "",
        "# Create runtime roots before bootstrapping genesis/keys:",
    ]
    for host in plan.hosts:
        lines.append(f"ssh {host.ssh or host.id} 'mkdir -p {host.runtime_root}'")
    lines.extend(
        [
            "",
            "# After Coolify deploy, verify the operator RPC endpoint on its host:",
            checks["chain_id_probe"],
            checks["block_probe"],
            "",
            "# Keep RPC private at first. From the operator workstation, tunnel it:",
        ]
    )
    first_rpc = rpc_target_service(plan)
    rpc_host = host_by_id(plan)[first_rpc.host]
    lines.append(f"ssh -L {first_rpc.rpc_host_port}:127.0.0.1:{first_rpc.rpc_host_port} {rpc_host.ssh or rpc_host.id}")
    lines.append("")
    return "\n".join(lines)


def write_outputs(plan: NetworkPlan, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plan.json").write_text(json.dumps(plan.to_dict(), indent=2) + "\n", encoding="utf-8")
    (out_dir / "operator-commands.txt").write_text(render_commands(plan), encoding="utf-8")
    for host in plan.hosts:
        if services_for_host(plan, host.id):
            (out_dir / f"docker-compose.{host.id}.yml").write_text(render_compose_for_host(plan, host.id), encoding="utf-8")



def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def is_local_coolify_plan(plan: NetworkPlan) -> bool:
    return plan.name in LOCAL_COOLIFY_SEEDS


def set_arg_default(args: argparse.Namespace, name: str, value: object) -> None:
    current = getattr(args, name, "")
    if current is None or str(current).strip() == "":
        setattr(args, name, value)


def load_local_coolify_helper(root: Path) -> object:
    script = root / "tools" / "local-prod" / "coolify-local-docker.py"
    if not script.exists():
        raise CoolifyError(f"missing local Coolify helper: {script}")
    spec = importlib.util.spec_from_file_location("main_computer_local_coolify_docker", script)
    if spec is None or spec.loader is None:
        raise CoolifyError(f"failed to load local Coolify helper: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_local_coolify_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def applications_coolify_runtime_config(root: Path) -> dict[str, object]:
    """Return the startup-managed local Coolify runtime, when present.

    The local QBFT ``test`` deployer should reuse the same local Coolify stack
    that the applications/Website Builder startup golden path already owns.  It
    must not spin up a second fallback stack on 127.0.0.1:8000 just because the
    repo-local helper defaults point there.
    """

    env_path = root / "runtime" / "applications_service" / "applications.env"
    if not env_path.is_file():
        return {}
    values = parse_local_coolify_env_text(env_path.read_text(encoding="utf-8", errors="replace"))
    mapping = {
        "project_name": values.get("COOLIFY_COMPOSE_PROJECT"),
        "state_dir": values.get("COOLIFY_LOCAL_STATE"),
        "app_port": values.get("APP_PORT"),
        "soketi_port": values.get("SOKETI_PORT"),
        "soketi_terminal_port": values.get("SOKETI_TERMINAL_PORT"),
        "network_name": values.get("COOLIFY_NETWORK_NAME"),
        "container_prefix": values.get("COOLIFY_CONTAINER_NAME"),
    }
    return {key: value for key, value in mapping.items() if value not in (None, "")}


def apply_applications_coolify_runtime(root: Path, adapter: object) -> dict[str, object]:
    config = applications_coolify_runtime_config(root)
    if not config:
        return {}
    runtime_config = getattr(adapter, "_RUNTIME_CONFIG", None)
    if isinstance(runtime_config, dict):
        runtime_config.update(config)
    setattr(adapter, "_MAIN_COMPUTER_APPLICATIONS_RUNTIME_CONFIG", dict(config))
    return dict(config)


def local_coolify_token_path(root: Path, adapter: object | None = None) -> Path:
    if adapter is not None:
        token_file = getattr(adapter, "api_token_file", None)
        if callable(token_file):
            try:
                return Path(token_file(root)).resolve()
            except Exception:
                pass
    return (root / LOCAL_COOLIFY_TOKEN_RELATIVE_PATH).resolve()


def configure_local_coolify_defaults(plan: NetworkPlan, args: argparse.Namespace, *, adapter: object | None = None) -> dict[str, Any]:
    """Bind the local test seed to the repo-local Coolify bootstrap contract.

    Hosted testnet/mainnet still use explicit remote Coolify credentials.  The
    local ``test`` seed follows the Website Builder Local Server convention:
    local Coolify lives at 127.0.0.1:8000 and its API token is stored under
    runtime/coolify-local-docker/api-token.txt.
    """

    if not is_local_coolify_plan(plan):
        return {}

    root = repo_root()
    token_path = local_coolify_token_path(root, adapter)
    dashboard_url = LOCAL_COOLIFY_DEFAULT_URL
    if adapter is not None:
        adapter_dashboard_url = getattr(adapter, "dashboard_url", None)
        if callable(adapter_dashboard_url):
            try:
                dashboard_url = str(adapter_dashboard_url(root) or dashboard_url)
            except Exception:
                dashboard_url = LOCAL_COOLIFY_DEFAULT_URL
    set_arg_default(args, "coolify_url", dashboard_url)
    set_arg_default(args, "coolify_token_file", str(token_path))
    if not str(getattr(args, "coolify_token", "") or "").strip() and str(getattr(args, "coolify_token_file", "") or "").strip() == str(token_path):
        # Prefer the local token-file contract over an unrelated remote token
        # that may be present in MAIN_COMPUTER_COOLIFY_TOKEN.
        setattr(args, "coolify_token_env", "")
    set_arg_default(args, "coolify_environment", LOCAL_COOLIFY_DEFAULT_ENVIRONMENT)
    set_arg_default(args, "coolify_service_name", plan.compose_project)
    if str(getattr(args, "foundry_docker_network", "") or "").strip() in {"", "bridge"}:
        setattr(args, "foundry_docker_network", plan.docker_network)
    return {
        "local": True,
        "coolify_url": str(getattr(args, "coolify_url", "")),
        "token_file": str(token_path),
        "coolify_environment": str(getattr(args, "coolify_environment", "")),
        "coolify_service_name": str(getattr(args, "coolify_service_name", "")),
        "foundry_docker_network": str(getattr(args, "foundry_docker_network", "")),
    }


def ensure_local_coolify_context(plan: NetworkPlan, args: argparse.Namespace, *, require_infra: bool = True) -> dict[str, Any]:
    if not is_local_coolify_plan(plan):
        return {}
    if bool(getattr(args, "_local_coolify_context_ready", False)):
        return dict(getattr(args, "_local_coolify_context", {}) or {})

    root = repo_root()
    adapter = load_local_coolify_helper(root)
    runtime_config = apply_applications_coolify_runtime(root, adapter)
    context = configure_local_coolify_defaults(plan, args, adapter=adapter)
    context["repo_root"] = str(root)
    if runtime_config:
        context["applications_runtime"] = runtime_config

    if bool(getattr(args, "dry_run", False)) or not require_infra:
        setattr(args, "_local_coolify_context_ready", True)
        setattr(args, "_local_coolify_context", context)
        return context

    ensure_infra_status = getattr(adapter, "ensure_infra_status", None)
    if not callable(ensure_infra_status):
        raise CoolifyError("local Coolify helper does not expose ensure_infra_status(root)")

    infra_ok, infra_detail = ensure_infra_status(root)
    if not infra_ok:
        dashboard = str(getattr(args, "coolify_url", "") or "").strip() or LOCAL_COOLIFY_DEFAULT_URL
        raise CoolifyError(
            "local Coolify is not ready for QBFT test deployment. "
            "The local test deployer now reuses the startup-managed Coolify stack instead of starting a second fallback stack. "
            f"Start/repair the Main Computer local startup golden path, then retry apply test --all. "
            f"dashboard={dashboard}; detail={infra_detail}"
        )
    context["infra"] = infra_detail

    ensure_api_token = getattr(adapter, "ensure_api_token", None)
    read_api_token = getattr(adapter, "read_api_token", None)
    if callable(ensure_api_token):
        token_ok, token_detail, token = ensure_api_token(root)
    elif callable(read_api_token):
        token = str(read_api_token(root) or "").strip()
        token_ok = bool(token)
        token_detail = "local Coolify API token file is present" if token_ok else "local Coolify API token is missing"
    else:
        raise CoolifyError("local Coolify helper does not expose ensure_api_token/read_api_token")
    token = str(token or "").strip()
    if not token_ok or not token:
        raise CoolifyError(token_detail or "local Coolify API token is missing")
    context["token"] = token_detail
    # The local Website Builder prepare path uses the token returned by the
    # startup-managed Coolify helper directly.  Preserve that same proven token
    # for the later Coolify client construction instead of falling back to the
    # hosted MAIN_COMPUTER_COOLIFY_TOKEN gate.
    setattr(args, "_local_coolify_token", token)
    setattr(args, "_local_coolify_token_source", f"local-helper:{local_coolify_token_path(root, adapter)}")

    target_func = getattr(adapter, "local_deploy_target_from_db", None)
    if not callable(target_func):
        raise CoolifyError("local Coolify helper does not expose local_deploy_target_from_db(root)")
    target_ok, target_detail, target = target_func(root)
    if not target_ok:
        raise CoolifyError(target_detail)
    context["target"] = target_detail
    if isinstance(target, dict):
        set_arg_default(args, "coolify_server_uuid", target.get("server_uuid", ""))
        set_arg_default(args, "coolify_destination_uuid", target.get("destination_uuid", ""))

    find_project = getattr(adapter, "find_local_project_uuid_via_api", None)
    if not callable(find_project):
        raise CoolifyError("local Coolify helper does not expose find_local_project_uuid_via_api(root, token)")
    project_ok, project_detail, project_uuid = find_project(root, str(token))
    if not project_ok or not project_uuid:
        raise CoolifyError(project_detail or "local Coolify project is missing")
    set_arg_default(args, "coolify_project_uuid", project_uuid)
    context["project"] = project_detail

    ensure_environment = getattr(adapter, "ensure_project_environment_via_api_or_db", None)
    if callable(ensure_environment):
        env_ok, env_detail = ensure_environment(root, str(token), project_uuid)
        if not env_ok:
            raise CoolifyError(env_detail)
        context["environment"] = env_detail

    setattr(args, "_local_coolify_context_ready", True)
    setattr(args, "_local_coolify_context", context)
    return context


def redact_secret(value: str, *, visible: int = 4) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= visible * 2:
        return "***"
    return f"{text[:visible]}...{text[-visible:]}"


def should_log(args: argparse.Namespace | None) -> bool:
    if args is None:
        return False
    if bool(getattr(args, "quiet", False)):
        return False
    action = str(getattr(args, "action", "") or "")
    return action in {"apply", "coolify-sync", "coolify-check", "coolify-discover", "wait-rpc", "deploy-contracts"}


def operator_log(args: argparse.Namespace | None, message: str, **fields: Any) -> None:
    """Emit human-readable operator progress to stdout while avoiding secrets."""

    if not should_log(args):
        return
    timestamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [f"[coolify-qbft] {timestamp}", message]
    safe_fields: list[str] = []
    for key, value in fields.items():
        if value is None or value == "":
            continue
        key_text = str(key)
        if "token" in key_text.lower() or "secret" in key_text.lower() or "password" in key_text.lower():
            value = redact_secret(str(value))
        safe_fields.append(f"{key_text}={value}")
    if safe_fields:
        parts.append(" ".join(safe_fields))
    print(" ".join(parts), flush=True)


def resolve_coolify_token(args: argparse.Namespace) -> tuple[str, str]:
    if getattr(args, "coolify_token", ""):
        return str(args.coolify_token), "direct"
    token_env = str(getattr(args, "coolify_token_env", "") or DEFAULT_COOLIFY_TOKEN_ENV)
    if token_env and os.environ.get(token_env):
        return str(os.environ[token_env]), f"env:{token_env}"
    token_file = str(getattr(args, "coolify_token_file", "") or "").strip()
    if token_file:
        path = Path(token_file)
        if path.is_file():
            token = path.read_text(encoding="utf-8").strip()
            if token.startswith("token="):
                token = token.split("=", 1)[1].strip()
            if token:
                return token, f"file:{path}"
    local_token = str(getattr(args, "_local_coolify_token", "") or "").strip()
    if local_token:
        return local_token, str(getattr(args, "_local_coolify_token_source", "") or "local-helper")
    raise CoolifyError(
        "Coolify token is required. Pass --coolify-token, --coolify-token-env, "
        f"or set {DEFAULT_COOLIFY_TOKEN_ENV}."
    )


@dataclass(frozen=True)
class CoolifyResponse:
    ok: bool
    status: int
    method: str
    url: str
    path: str
    body: Any


class CoolifyClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: float = DEFAULT_COOLIFY_API_TIMEOUT_S,
        retries: int = DEFAULT_COOLIFY_API_RETRIES,
        retry_sleep_s: float = DEFAULT_COOLIFY_API_RETRY_SLEEP_S,
    ) -> None:
        clean = str(base_url or "").strip().rstrip("/")
        if not clean.startswith(("http://", "https://")):
            raise CoolifyError(f"Coolify URL must be http(s): {base_url!r}")
        self.base_url = clean
        self.token = token
        self.timeout_s = float(timeout_s)
        self.retries = max(0, int(retries))
        self.retry_sleep_s = max(0.0, float(retry_sleep_s))

    def request(self, method: str, path: str, payload: Any | None = None) -> CoolifyResponse:
        api_path = path if path.startswith("/") else f"/{path}"
        url = self.base_url + api_path
        data = None
        headers = {
            "Accept": "application/json,text/plain,*/*",
            "Authorization": f"Bearer {self.token}",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        attempts = self.retries + 1
        last_error: BaseException | None = None
        for attempt in range(1, attempts + 1):
            request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                    return CoolifyResponse(
                        ok=200 <= int(response.status) < 300,
                        status=int(response.status),
                        method=method.upper(),
                        url=self.base_url,
                        path=api_path,
                        body=parse_response_body(raw),
                    )
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                return CoolifyResponse(
                    ok=False,
                    status=int(exc.code),
                    method=method.upper(),
                    url=self.base_url,
                    path=api_path,
                    body=parse_response_body(raw),
                )
            except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(self.retry_sleep_s)

        return CoolifyResponse(
            ok=False,
            status=0,
            method=method.upper(),
            url=self.base_url,
            path=api_path,
            body={
                "error": "request_failed",
                "message": f"Coolify API request failed: {url}: {last_error}",
                "error_type": type(last_error).__name__ if last_error is not None else "unknown",
                "attempts": attempts,
                "timeout_s": self.timeout_s,
            },
        )


def parse_response_body(raw: str) -> Any:
    text = str(raw or "")
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def response_to_dict(response: CoolifyResponse, *, token: str = "", token_source: str = "") -> dict[str, Any]:
    result = {
        "ok": bool(response.ok),
        "status": response.status,
        "method": response.method,
        "url": response.url,
        "path": response.path,
        "body": response.body,
    }
    if token:
        result["token"] = redact_secret(token)
    if token_source:
        result["token_source"] = token_source
    return result


def coolify_client_from_args(args: argparse.Namespace, plan: NetworkPlan | None = None) -> tuple[CoolifyClient, str, str]:
    url = str(getattr(args, "coolify_url", "") or "").strip()
    if not url and plan is not None and plan.hosts:
        url = next((host.coolify_url for host in plan.hosts if host.coolify_url), "")
    if not url:
        raise CoolifyError("Coolify URL is required. Pass --coolify-url or use --single-host.")
    token, token_source = resolve_coolify_token(args)
    return CoolifyClient(
        url,
        token,
        timeout_s=float(getattr(args, "coolify_timeout_s", DEFAULT_COOLIFY_API_TIMEOUT_S)),
        retries=int(getattr(args, "coolify_retries", DEFAULT_COOLIFY_API_RETRIES)),
        retry_sleep_s=float(getattr(args, "coolify_retry_sleep_s", DEFAULT_COOLIFY_API_RETRY_SLEEP_S)),
    ), token, token_source


def coolify_check(args: argparse.Namespace, plan: NetworkPlan | None = None) -> dict[str, Any]:
    if plan is not None:
        ensure_local_coolify_context(plan, args, require_infra=not bool(getattr(args, "dry_run", False)))
    client, token, token_source = coolify_client_from_args(args, plan)
    operator_log(
        args,
        "coolify-check start",
        url=client.base_url,
        path="/api/v1/version",
        timeout_s=client.timeout_s,
        retries=client.retries,
    )
    response = client.request("GET", "/api/v1/version")
    operator_log(args, "coolify-check result", ok=response.ok, status=response.status)
    result = response_to_dict(response, token=token, token_source=token_source)
    if plan is not None:
        result["network"] = plan.name
    return result


def coolify_body_items(body: Any, *preferred_keys: str) -> list[dict[str, Any]]:
    """Normalize Coolify list responses across v4 API response shapes."""

    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if not isinstance(body, dict):
        return []
    for key in (*preferred_keys, "data", "items", "projects", "servers", "services", "environments", "resources"):
        value = body.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def coolify_item_uuid(item: dict[str, Any]) -> str:
    for key in ("uuid", "id", "service_uuid", "project_uuid", "server_uuid"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def coolify_item_name(item: dict[str, Any]) -> str:
    for key in ("name", "description", "fqdn", "ip"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def coolify_item_summary(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "uuid",
        "id",
        "name",
        "description",
        "ip",
        "fqdn",
        "status",
        "environment_name",
        "project_uuid",
        "server_uuid",
        "destination_uuid",
    )
    summary = {key: item.get(key) for key in keys if item.get(key) not in (None, "")}
    if not summary.get("uuid"):
        uuid = coolify_item_uuid(item)
        if uuid:
            summary["uuid"] = uuid
    if not summary.get("name"):
        name = coolify_item_name(item)
        if name:
            summary["name"] = name
    return summary


def coolify_list(
    client: CoolifyClient,
    args: argparse.Namespace,
    path: str,
    *,
    label: str,
    preferred_keys: tuple[str, ...] = (),
) -> tuple[CoolifyResponse, list[dict[str, Any]]]:
    operator_log(args, "coolify-discover request", label=label, path=path)
    response = client.request("GET", path)
    items = coolify_body_items(response.body, *preferred_keys) if response.ok else []
    operator_log(args, "coolify-discover result", label=label, ok=response.ok, status=response.status, count=len(items))
    return response, items


def choose_coolify_uuid(
    *,
    explicit_uuid: str,
    explicit_name: str,
    items: list[dict[str, Any]],
    kind: str,
    args: argparse.Namespace,
) -> tuple[str, dict[str, Any]]:
    """Choose a Coolify resource UUID by explicit UUID, name, or singleton list."""

    clean_uuid = str(explicit_uuid or "").strip()
    if clean_uuid:
        return clean_uuid, {"source": "explicit_uuid", "kind": kind, "uuid": clean_uuid}

    clean_name = str(explicit_name or "").strip().lower()
    if clean_name:
        matches = [item for item in items if coolify_item_name(item).lower() == clean_name]
        if len(matches) == 1:
            uuid = coolify_item_uuid(matches[0])
            return uuid, {"source": "explicit_name", "kind": kind, "uuid": uuid, "name": coolify_item_name(matches[0])}
        return "", {
            "source": "explicit_name",
            "kind": kind,
            "name": explicit_name,
            "matches": [coolify_item_summary(item) for item in matches],
            "message": f"Expected one Coolify {kind} named {explicit_name!r}; found {len(matches)}.",
        }

    if len(items) == 1:
        uuid = coolify_item_uuid(items[0])
        return uuid, {"source": "single_discovered", "kind": kind, "uuid": uuid, "name": coolify_item_name(items[0])}

    if len(items) == 0:
        return "", {"source": "discovery", "kind": kind, "message": f"No Coolify {kind}s were returned by the API."}

    return "", {
        "source": "discovery",
        "kind": kind,
        "message": f"Multiple Coolify {kind}s exist; pass --coolify-{kind}-uuid or --coolify-{kind}-name.",
        "candidates": [coolify_item_summary(item) for item in items],
    }


def choose_coolify_environment(
    *,
    explicit_uuid: str,
    environment_name: str,
    environments: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[str, dict[str, Any]]:
    """Choose an environment UUID by explicit UUID, exact name, or empty default.

    Unlike projects/servers, environments are selected by the desired deployment
    environment name.  The default name comes from the network plan, typically
    "test".  If no matching environment exists, callers may create it.
    """

    clean_uuid = str(explicit_uuid or "").strip()
    clean_name = str(environment_name or "").strip()
    if clean_uuid:
        match = next((item for item in environments if coolify_item_uuid(item) == clean_uuid), None)
        return clean_uuid, {
            "source": "explicit_uuid",
            "kind": "environment",
            "uuid": clean_uuid,
            **({"name": coolify_item_name(match)} if match else {}),
        }

    matches = [item for item in environments if coolify_item_name(item).lower() == clean_name.lower()]
    if len(matches) == 1:
        uuid = coolify_item_uuid(matches[0])
        return uuid, {"source": "exact_name", "kind": "environment", "uuid": uuid, "name": coolify_item_name(matches[0])}
    if len(matches) > 1:
        return "", {
            "source": "exact_name",
            "kind": "environment",
            "name": clean_name,
            "matches": [coolify_item_summary(item) for item in matches],
            "message": f"Expected one Coolify environment named {clean_name!r}; found {len(matches)}.",
        }
    return "", {
        "source": "missing",
        "kind": "environment",
        "name": clean_name,
        "message": f"Coolify environment {clean_name!r} does not exist in the selected project.",
    }


def ensure_coolify_environment(
    *,
    client: CoolifyClient,
    args: argparse.Namespace,
    project_uuid: str,
    environment_name: str,
    explicit_environment_uuid: str,
    tried: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Find or create the Coolify project environment used for the service.

    Coolify's service-create endpoint returns "Environment not found" when the
    project environment does not already exist.  The project API exposes
    /projects/{uuid}/environments for listing/creating environments, so use that
    before attempting service creation.
    """

    clean_project_uuid = str(project_uuid or "").strip()
    clean_environment_name = str(environment_name or "").strip() or "test"
    clean_environment_uuid = str(explicit_environment_uuid or "").strip()
    if not clean_project_uuid:
        return "", {"source": "missing-project", "kind": "environment", "name": clean_environment_name}

    env_path = f"/api/v1/projects/{urllib.parse.quote(clean_project_uuid)}/environments"
    response, environments = coolify_list(
        client,
        args,
        env_path,
        label="environments",
        preferred_keys=("environments",),
    )
    tried.append({"operation": "list-environments", "response": response_to_dict(response), "count": len(environments)})
    context: dict[str, Any] = {
        "environment_name": clean_environment_name,
        "environment_uuid": clean_environment_uuid,
        "list_response": response_to_dict(response),
        "count": len(environments),
    }
    if not response.ok:
        context["selection"] = {"source": "api_error", "response": response_to_dict(response)}
        return clean_environment_uuid, context

    selected_uuid, selection = choose_coolify_environment(
        explicit_uuid=clean_environment_uuid,
        environment_name=clean_environment_name,
        environments=environments,
        args=args,
    )
    context["selection"] = selection
    if selected_uuid:
        context["environment_uuid"] = selected_uuid
        return selected_uuid, context

    if bool(getattr(args, "no_coolify_create_environment", False)):
        context["create_skipped"] = True
        return "", context

    operator_log(args, "coolify-discover create-environment start", project_uuid=clean_project_uuid, environment=clean_environment_name)
    create_response = client.request("POST", env_path, {"name": clean_environment_name})
    operator_log(args, "coolify-discover create-environment result", ok=create_response.ok, status=create_response.status)
    tried.append(
        {
            "operation": "create-environment",
            "path": env_path,
            "payload_keys": ["name"],
            "response": response_to_dict(create_response),
        }
    )
    context["create_response"] = response_to_dict(create_response)

    if create_response.ok:
        selected_uuid = coolify_service_uuid_from_body(create_response.body)
        if selected_uuid:
            context["environment_uuid"] = selected_uuid
            context["selection"] = {
                "source": "created",
                "kind": "environment",
                "uuid": selected_uuid,
                "name": clean_environment_name,
            }
            return selected_uuid, context

    if create_response.status == 409:
        # Race-safe/idempotent path: if Coolify says it already exists, list
        # again and select by name.
        response, environments = coolify_list(
            client,
            args,
            env_path,
            label="environments-after-conflict",
            preferred_keys=("environments",),
        )
        tried.append({"operation": "list-environments-after-conflict", "response": response_to_dict(response), "count": len(environments)})
        if response.ok:
            selected_uuid, selection = choose_coolify_environment(
                explicit_uuid=clean_environment_uuid,
                environment_name=clean_environment_name,
                environments=environments,
                args=args,
            )
            context["selection"] = selection
            if selected_uuid:
                context["environment_uuid"] = selected_uuid
                return selected_uuid, context

    # Older/self-hosted builds sometimes expose a get-by-name path even when the
    # list/create response shape is sparse.  Try it as a final exact lookup.
    get_path = f"/api/v1/projects/{urllib.parse.quote(clean_project_uuid)}/{urllib.parse.quote(clean_environment_name)}"
    get_response = client.request("GET", get_path)
    tried.append({"operation": "get-environment", "path": get_path, "response": response_to_dict(get_response)})
    context["get_response"] = response_to_dict(get_response)
    if get_response.ok and isinstance(get_response.body, dict):
        selected_uuid = coolify_item_uuid(get_response.body)
        if selected_uuid:
            context["environment_uuid"] = selected_uuid
            context["selection"] = {
                "source": "get-by-name",
                "kind": "environment",
                "uuid": selected_uuid,
                "name": coolify_item_name(get_response.body) or clean_environment_name,
            }
            return selected_uuid, context

    return "", context


def coolify_discover(args: argparse.Namespace, plan: NetworkPlan | None = None) -> dict[str, Any]:
    if plan is not None:
        ensure_local_coolify_context(plan, args, require_infra=not bool(getattr(args, "dry_run", False)))
    client, token, token_source = coolify_client_from_args(args, plan)
    result: dict[str, Any] = {
        "ok": False,
        "url": client.base_url,
        "token": redact_secret(token),
        "token_source": token_source,
    }
    version = client.request("GET", "/api/v1/version")
    result["version"] = response_to_dict(version)
    if not version.ok:
        result["stage"] = "version"
        return result

    projects_response, projects = coolify_list(client, args, "/api/v1/projects", label="projects", preferred_keys=("projects",))
    servers_response, servers = coolify_list(client, args, "/api/v1/servers", label="servers", preferred_keys=("servers",))
    services_response, services = coolify_list(client, args, "/api/v1/services", label="services", preferred_keys=("services",))
    project_uuid, project_selection = choose_coolify_uuid(
        explicit_uuid=str(getattr(args, "coolify_project_uuid", "") or ""),
        explicit_name=str(getattr(args, "coolify_project_name", "") or ""),
        items=projects,
        kind="project",
        args=args,
    ) if projects_response.ok else ("", {"source": "api_error"})
    environments_response: CoolifyResponse | None = None
    environments: list[dict[str, Any]] = []
    if project_uuid:
        environments_response, environments = coolify_list(
            client,
            args,
            f"/api/v1/projects/{urllib.parse.quote(project_uuid)}/environments",
            label="environments",
            preferred_keys=("environments",),
        )
    result.update(
        {
            "ok": bool(projects_response.ok and servers_response.ok),
            "projects": [coolify_item_summary(item) for item in projects],
            "project_selection": project_selection,
            "servers": [coolify_item_summary(item) for item in servers],
            "services": [coolify_item_summary(item) for item in services],
            "environments": [coolify_item_summary(item) for item in environments],
            "responses": {
                "projects": response_to_dict(projects_response),
                "servers": response_to_dict(servers_response),
                "services": response_to_dict(services_response),
                **({"environments": response_to_dict(environments_response)} if environments_response is not None else {}),
            },
        }
    )
    return result


def coolify_service_matches_name(item: dict[str, Any], service_name: str) -> bool:
    clean_name = str(service_name or "").strip().lower()
    if not clean_name:
        return False
    candidates = [
        str(item.get(key) or "").strip().lower()
        for key in ("name", "description", "fqdn")
        if str(item.get(key) or "").strip()
    ]
    item_name = coolify_item_name(item).strip().lower()
    if item_name:
        candidates.append(item_name)
    for candidate in candidates:
        if candidate == clean_name:
            return True
        # Coolify UI/API display strings may decorate the service name with the
        # server/destination, for example: "service-name (localhost)".
        if candidate.startswith(f"{clean_name} ") or candidate.startswith(f"{clean_name}("):
            return True
    return False


def coolify_find_service_by_name(
    *,
    client: CoolifyClient,
    args: argparse.Namespace,
    service_name: str,
    tried: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Find an existing Coolify service by exact name before creating one.

    Service creation must be idempotent.  Creating a new service while a same-name
    service already exists leaves two Coolify resource UUIDs managing the same
    QBFT ports and container names.  Treat duplicate names as unsafe instead of
    guessing which resource should own the deployment.
    """

    clean_name = str(service_name or "").strip()
    response, services = coolify_list(client, args, "/api/v1/services", label="services", preferred_keys=("services",))
    tried.append({"operation": "list-services-for-existing-service", "response": response_to_dict(response), "count": len(services)})
    context: dict[str, Any] = {
        "service_name": clean_name,
        "list_response": response_to_dict(response),
        "count": len(services),
    }
    if not response.ok:
        context["selection"] = {"source": "api_error", "response": response_to_dict(response)}
        return "", context

    matches = [item for item in services if coolify_service_matches_name(item, clean_name)]
    context["matches"] = [coolify_item_summary(item) for item in matches]
    if len(matches) == 1:
        uuid = coolify_item_uuid(matches[0])
        context["selection"] = {
            "source": "exact_service_name",
            "kind": "service",
            "uuid": uuid,
            "name": coolify_item_name(matches[0]),
        }
        return uuid, context

    if len(matches) > 1:
        context["selection"] = {
            "source": "duplicate_service_name",
            "kind": "service",
            "name": clean_name,
            "matches": [coolify_item_summary(item) for item in matches],
            "message": f"Expected at most one Coolify service named {clean_name!r}; found {len(matches)}.",
        }
        return "", context

    context["selection"] = {
        "source": "missing",
        "kind": "service",
        "name": clean_name,
        "message": f"No Coolify service named {clean_name!r} was returned by the API.",
    }
    return "", context


def resolve_coolify_create_context(
    *,
    plan: NetworkPlan,
    args: argparse.Namespace,
    client: CoolifyClient,
    tried: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, Any]]:
    project_uuid = str(getattr(args, "coolify_project_uuid", "") or "").strip()
    server_uuid = str(getattr(args, "coolify_server_uuid", "") or "").strip()
    destination_uuid = str(getattr(args, "coolify_destination_uuid", "") or "").strip()
    environment_name = str(getattr(args, "coolify_environment", "") or plan.environment).strip() or "test"
    environment_uuid = str(getattr(args, "coolify_environment_uuid", "") or "").strip()
    context: dict[str, Any] = {
        "project_uuid": project_uuid,
        "server_uuid": server_uuid,
        "destination_uuid": destination_uuid,
        "environment_name": environment_name,
        "environment_uuid": environment_uuid,
    }

    if not project_uuid:
        response, projects = coolify_list(client, args, "/api/v1/projects", label="projects", preferred_keys=("projects",))
        tried.append({"operation": "list-projects", "response": response_to_dict(response), "count": len(projects)})
        if response.ok:
            project_uuid, selection = choose_coolify_uuid(
                explicit_uuid="",
                explicit_name=str(getattr(args, "coolify_project_name", "") or ""),
                items=projects,
                kind="project",
                args=args,
            )
            context["project_selection"] = selection
            context["project_uuid"] = project_uuid
        else:
            context["project_selection"] = {"source": "api_error", "response": response_to_dict(response)}

    if not server_uuid:
        response, servers = coolify_list(client, args, "/api/v1/servers", label="servers", preferred_keys=("servers",))
        tried.append({"operation": "list-servers", "response": response_to_dict(response), "count": len(servers)})
        if response.ok:
            server_uuid, selection = choose_coolify_uuid(
                explicit_uuid="",
                explicit_name=str(getattr(args, "coolify_server_name", "") or ""),
                items=servers,
                kind="server",
                args=args,
            )
            context["server_selection"] = selection
            context["server_uuid"] = server_uuid
        else:
            context["server_selection"] = {"source": "api_error", "response": response_to_dict(response)}

    if project_uuid:
        environment_uuid, environment_context = ensure_coolify_environment(
            client=client,
            args=args,
            project_uuid=project_uuid,
            environment_name=environment_name,
            explicit_environment_uuid=environment_uuid,
            tried=tried,
        )
        context["environment"] = environment_context
        context["environment_uuid"] = str(environment_uuid or "").strip()

    payload = {
        "project_uuid": str(project_uuid or "").strip(),
        "server_uuid": str(server_uuid or "").strip(),
        "destination_uuid": str(destination_uuid or "").strip(),
        "environment_name": environment_name,
        "environment_uuid": str(context.get("environment_uuid") or "").strip(),
    }
    return payload, context


def base64_compose(compose: str) -> str:
    return base64.b64encode(compose.encode("utf-8")).decode("ascii")


def coolify_service_uuid_from_body(body: Any) -> str:
    if isinstance(body, dict):
        for key in ("uuid", "service_uuid", "id"):
            value = str(body.get(key) or "").strip()
            if value:
                return value
        service = body.get("service")
        if isinstance(service, dict):
            return coolify_service_uuid_from_body(service)
    return ""


def project_service_name(plan: NetworkPlan, host_id: str) -> str:
    return safe_id(f"{plan.compose_project}-{host_id}", kind="service")


def single_host_id(plan: NetworkPlan) -> str:
    hosts_with_services = [host.id for host in plan.hosts if services_for_host(plan, host.id)]
    if len(hosts_with_services) != 1:
        raise PlanError("This action currently requires a single-host plan. Split-host automation needs explicit key distribution.")
    return hosts_with_services[0]


def infer_external_rpc_url(plan: NetworkPlan, args: argparse.Namespace) -> str:
    explicit = str(getattr(args, "rpc_url", "") or "").strip()
    if explicit:
        return explicit
    rpc_node = rpc_target_service(plan)
    if is_local_coolify_plan(plan):
        return rpc_node.rpc_url_on_host
    if rpc_node.rpc_bind_host == "0.0.0.0":
        return rpc_node.rpc_url_on_host
    raise PlanError(
        "RPC is not externally reachable from the operator machine. Pass --public-rpc "
        "or --rpc-url after exposing the non-validator RPC through Coolify."
    )


def json_rpc(url: str, method: str, params: list[Any] | None = None, *, timeout_s: float = 8.0) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if "error" in payload:
        raise RuntimeError(f"{method} returned JSON-RPC error: {payload['error']}")
    return payload.get("result")


def default_rpc_min_peers(plan: NetworkPlan) -> int:
    """Return the default peer readiness requirement for the operator RPC node.

    A one-node dev chain can legitimately have zero peers.  Any topology with a
    dedicated RPC node or more than one Besu service should prove at least one
    peer before contracts are deployed, otherwise the RPC process may be up while
    the QBFT network is not actually connected or producing blocks.
    """

    besu_services = [service for service in plan.services if service.role in {"validator", "rpc"}]
    return 1 if len(besu_services) > 1 else 0


def rpc_min_peers_from_args(plan: NetworkPlan, args: argparse.Namespace) -> int:
    value = int(getattr(args, "rpc_min_peers", DEFAULT_RPC_MIN_PEERS_AUTO))
    if value >= 0:
        return value
    return default_rpc_min_peers(plan)


def wait_for_rpc(plan: NetworkPlan, args: argparse.Namespace) -> dict[str, Any]:
    rpc_url = infer_external_rpc_url(plan, args)
    expected_chain_id = hex(plan.chain_id)
    timeout_s = float(getattr(args, "rpc_timeout_s", DEFAULT_RPC_WAIT_TIMEOUT_S))
    poll_interval_s = float(getattr(args, "rpc_poll_interval_s", DEFAULT_RPC_POLL_INTERVAL_S))
    min_peers = rpc_min_peers_from_args(plan, args)
    require_block_advance = not bool(getattr(args, "no_rpc_require_block_advance", False))
    deadline = None if timeout_s <= 0 else time.monotonic() + timeout_s
    last_error: object = None
    last_chain_id = ""
    last_block_number: int | None = None
    last_peer_count: int | None = None
    first_observed_block: int | None = None
    block_advanced = False
    attempt = 0
    operator_log(
        args,
        "wait-rpc start",
        rpc_url=rpc_url,
        expected_chain_id=expected_chain_id,
        timeout_s=timeout_s,
        min_peers=min_peers,
        require_block_advance=require_block_advance,
    )
    while deadline is None or time.monotonic() < deadline:
        attempt += 1
        try:
            chain_id = str(json_rpc(rpc_url, "eth_chainId", timeout_s=8.0))
            last_chain_id = chain_id
            if chain_id.lower() != expected_chain_id.lower():
                raise RuntimeError(f"expected chain id {expected_chain_id}, got {chain_id}")
            block_hex = str(json_rpc(rpc_url, "eth_blockNumber", timeout_s=8.0))
            block_number = int(block_hex, 16)
            peer_hex = str(json_rpc(rpc_url, "net_peerCount", timeout_s=8.0))
            peer_count = int(peer_hex, 16)
            last_block_number = block_number
            last_peer_count = peer_count
            if first_observed_block is None:
                first_observed_block = block_number
            if block_number > first_observed_block:
                block_advanced = True
            operator_log(
                args,
                "wait-rpc probe",
                attempt=attempt,
                chain_id=chain_id,
                block_number=block_number,
                peer_count=peer_count,
                first_observed_block=first_observed_block,
                block_advanced=block_advanced,
            )

            not_ready_reasons: list[str] = []
            if block_number < 1:
                not_ready_reasons.append(f"block_number {block_number} < 1")
            if peer_count < min_peers:
                not_ready_reasons.append(f"peer_count {peer_count} < required {min_peers}")
            if require_block_advance and not block_advanced:
                not_ready_reasons.append(
                    f"block has not advanced beyond first observed block {first_observed_block}"
                )

            if not not_ready_reasons:
                operator_log(
                    args,
                    "wait-rpc done",
                    block_number=block_number,
                    peer_count=peer_count,
                    first_observed_block=first_observed_block,
                    block_advanced=block_advanced,
                )
                return {
                    "ok": True,
                    "rpc_url": rpc_url,
                    "chain_id": chain_id,
                    "block_number": block_number,
                    "peer_count": peer_count,
                    "first_observed_block": first_observed_block,
                    "block_advanced": block_advanced,
                    "min_peers": min_peers,
                }

            last_error = "; ".join(not_ready_reasons)
            operator_log(args, "wait-rpc not-ready", attempt=attempt, reason=last_error)
        except Exception as exc:  # noqa: BLE001 - operator-facing retry loop
            last_error = str(exc)
            operator_log(args, "wait-rpc retry", attempt=attempt, error=last_error)
        time.sleep(poll_interval_s)
    raise CoolifyError(
        f"Timed out waiting for RPC {rpc_url}; "
        f"last_chain_id={last_chain_id!r}; "
        f"last_block_number={last_block_number!r}; "
        f"last_peer_count={last_peer_count!r}; "
        f"first_observed_block={first_observed_block!r}; "
        f"block_advanced={block_advanced!r}; "
        f"min_peers={min_peers!r}; "
        f"last_error={last_error!r}"
    )


def process_timeout_arg(timeout_s: float | None) -> float | None:
    """Return a subprocess timeout, treating zero/negative values as unbounded."""
    if timeout_s is None:
        return None
    timeout = float(timeout_s)
    if timeout <= 0:
        return None
    return timeout


def infer_container_rpc_url(plan: NetworkPlan, args: argparse.Namespace, host_rpc_url: str) -> str:
    explicit = str(getattr(args, "container_rpc_url", "") or "").strip()
    if explicit:
        return explicit
    if is_local_coolify_plan(plan):
        return LOCAL_COOLIFY_RPC_CONTAINER_URL
    return host_rpc_url


def safe_subprocess_run(command: list[str], *, dry_run: bool = False, timeout_s: float | None = None) -> dict[str, Any]:
    if dry_run:
        return {"ok": True, "dry_run": True, "command": command}
    resolved_timeout = process_timeout_arg(timeout_s)
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=resolved_timeout)
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "command": command,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "ERROR: timed out\n",
            "timeout_s": timeout_s,
        }
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": command,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "timeout_s": resolved_timeout,
    }


def deployment_source_kind(plan: NetworkPlan, args: argparse.Namespace) -> str:
    explicit = str(getattr(args, "deployment_source_kind", "") or "").strip()
    if explicit:
        return explicit
    return f"coolify-qbft-{plan.environment}-deploy"


def deploy_contracts(plan: NetworkPlan, args: argparse.Namespace) -> dict[str, Any]:
    configure_local_coolify_defaults(plan, args)
    rpc_url = infer_external_rpc_url(plan, args)
    container_rpc_url = infer_container_rpc_url(plan, args, rpc_url)
    operator_log(args, "deploy-contracts start", rpc_url=rpc_url, container_rpc_url=container_rpc_url)
    run_id = str(getattr(args, "deployment_run_id", "") or f"coolify-qbft-{plan.name}-{_dt.datetime.now(_dt.timezone.utc).strftime('%Y%m%d%H%M%S')}")
    deployment_output = str(getattr(args, "deployment_output_dir", "") or (Path("runtime") / "deployments"))
    project_name = str(getattr(args, "deployment_project_name", "") or plan.compose_project)
    environment = str(getattr(args, "deployment_environment", "") or plan.environment)
    source_kind = deployment_source_kind(plan, args)
    command = [
        sys.executable,
        str(repo_root() / "tools" / "dev-chain-reset.py"),
        "--yes",
        "--external-chain",
        "--run-id",
        run_id,
        "--project-name",
        project_name,
        "--environment",
        environment,
        "--source-kind",
        source_kind,
        "--chain-id",
        str(plan.chain_id),
        "--host-rpc-url",
        rpc_url,
        "--container-rpc-url",
        container_rpc_url,
        "--wait-timeout-s",
        str(float(getattr(args, "rpc_timeout_s", DEFAULT_RPC_WAIT_TIMEOUT_S))),
        "--deploy-timeout-s",
        str(float(getattr(args, "deploy_contracts_timeout_s", DEFAULT_DEPLOY_CONTRACTS_TIMEOUT_S))),
        "--external-docker-network",
        str(getattr(args, "foundry_docker_network", "") or "bridge"),
        "--external-chain-container",
        "mc-qbft-rpc",
        "--deployment-output-dir",
        deployment_output,
        "--foundry-image",
        str(getattr(args, "foundry_image", "") or DEFAULT_FOUNDRY_IMAGE),
    ]
    operator_log(args, "deploy-contracts command", command=" ".join(command))
    result = safe_subprocess_run(
        command,
        dry_run=bool(getattr(args, "dry_run", False)),
        timeout_s=float(getattr(args, "deploy_contracts_timeout_s", DEFAULT_DEPLOY_CONTRACTS_TIMEOUT_S)),
    )
    result.update({"rpc_url": rpc_url, "container_rpc_url": container_rpc_url, "run_id": run_id, "deployment_output_dir": deployment_output})
    operator_log(args, "deploy-contracts result", ok=result.get("ok"), returncode=result.get("returncode", "dry-run"))
    return result


def coolify_sync(plan: NetworkPlan, args: argparse.Namespace, *, deploy: bool = False) -> dict[str, Any]:
    local_context = ensure_local_coolify_context(plan, args, require_infra=not bool(getattr(args, "dry_run", False)))
    host_id = str(getattr(args, "host", "") or single_host_id(plan))
    operator_log(args, "coolify-sync render-compose start", network=plan.name, host=host_id)
    compose = render_compose_for_host(
        plan,
        host_id,
        include_bootstrap=not bool(getattr(args, "no_bootstrap", False)),
        managed_volume=not bool(getattr(args, "bind_runtime_root", False)),
    )
    compose_b64 = base64_compose(compose)
    service_name = str(getattr(args, "coolify_service_name", "") or project_service_name(plan, host_id))
    service_uuid = str(getattr(args, "coolify_service_uuid", "") or "").strip()
    operator_log(
        args,
        "coolify-sync render-compose done",
        service_name=service_name,
        service_uuid=service_uuid or "new",
        compose_bytes=len(compose.encode("utf-8")),
        compose_base64_bytes=len(compose_b64),
        deploy=deploy,
    )

    if bool(getattr(args, "dry_run", False)):
        operator_log(args, "coolify-sync dry-run", service_name=service_name)
        return {
            "ok": True,
            "dry_run": True,
            "action": "coolify-sync",
            "service_name": service_name,
            "service_uuid": service_uuid,
            "compose": compose,
            "compose_base64_bytes": len(compose_b64),
            **({"local_coolify": local_context} if local_context else {}),
        }

    client, token, token_source = coolify_client_from_args(args, plan)
    operator_log(args, "coolify-sync check-api start", url=client.base_url)
    version = client.request("GET", "/api/v1/version")
    operator_log(args, "coolify-sync check-api result", ok=version.ok, status=version.status)
    if not version.ok:
        return {"ok": False, "stage": "version", "response": response_to_dict(version, token=token, token_source=token_source)}

    tried: list[dict[str, Any]] = []
    create_context: dict[str, Any] = {}
    existing_service_context: dict[str, Any] = {}
    if not service_uuid:
        existing_service_uuid, existing_service_context = coolify_find_service_by_name(
            client=client,
            args=args,
            service_name=service_name,
            tried=tried,
        )
        if existing_service_uuid:
            service_uuid = existing_service_uuid
            operator_log(args, "coolify-sync existing-service selected", service_name=service_name, service_uuid=service_uuid)
        else:
            selection = existing_service_context.get("selection") if isinstance(existing_service_context, dict) else {}
            source = selection.get("source") if isinstance(selection, dict) else ""
            if source == "duplicate_service_name":
                operator_log(args, "coolify-sync duplicate-service-name", service_name=service_name)
                return {
                    "ok": False,
                    "stage": "duplicate-service-name",
                    "service_name": service_name,
                    "message": (
                        f"Multiple Coolify services named {service_name!r} already exist. "
                        "Refusing to create or guess. Delete the duplicate service(s), or rerun with "
                        "--coolify-service-uuid for the exact service to update."
                    ),
                    "context": {"existing_service": existing_service_context},
                    "tried": tried,
                }
            if source == "api_error":
                operator_log(args, "coolify-sync service-discovery failed", service_name=service_name)
                return {
                    "ok": False,
                    "stage": "service-discovery",
                    "service_name": service_name,
                    "message": (
                        "Could not list existing Coolify services before creation. Refusing to create blindly; "
                        "rerun with --coolify-service-uuid if you know the exact service to update."
                    ),
                    "context": {"existing_service": existing_service_context},
                    "tried": tried,
                }

    if not service_uuid:
        tried.append(
            {
                "operation": "create-service-safety-policy",
                "mode": "coolify-api-service-name-only",
                "message": (
                    "Duplicate Coolify service prevention is handled with /api/v1/services name lookup. "
                    "The create path does not require SSH or local known_hosts access."
                ),
            }
        )

    if not service_uuid:
        create_refs, create_context = resolve_coolify_create_context(plan=plan, args=args, client=client, tried=tried)
        create_context["existing_service"] = existing_service_context
        project_uuid = str(create_refs.get("project_uuid") or "").strip()
        server_uuid = str(create_refs.get("server_uuid") or "").strip()
        environment_uuid = str(create_refs.get("environment_uuid") or "").strip()
        if not project_uuid or not server_uuid or not environment_uuid:
            operator_log(
                args,
                "coolify-sync create-service missing refs",
                project_uuid=bool(project_uuid),
                server_uuid=bool(server_uuid),
                environment_uuid=bool(environment_uuid),
            )
            return {
                "ok": False,
                "stage": "missing-create-context",
                "message": (
                    "Coolify service creation requires project_uuid, server_uuid, and an environment. "
                    "The script tried to discover or create the environment. If discovery returned multiple choices, rerun with "
                    "--coolify-project-uuid/--coolify-server-uuid/--coolify-environment-uuid or the corresponding --*-name flags."
                ),
                "context": create_context,
                "tried": tried,
            }

        operator_log(
            args,
            "coolify-sync create-service start",
            service_name=service_name,
            project_uuid=project_uuid,
            server_uuid=server_uuid,
            environment=create_refs.get("environment_name"),
            environment_uuid=create_refs.get("environment_uuid"),
        )
        create_payload = {
            "server_uuid": server_uuid,
            "project_uuid": project_uuid,
            "environment_name": str(create_refs.get("environment_name") or plan.environment),
            "environment_uuid": str(create_refs.get("environment_uuid") or ""),
            "name": service_name,
            "description": f"Main Computer {plan.name} QBFT network generated by tools/coolify_qbft_network.py",
            # Coolify v4.1 rejects docker_compose and expects docker_compose_raw to be base64 encoded.
            "docker_compose_raw": compose_b64,
            "instant_deploy": False,
        }
        destination_uuid = str(create_refs.get("destination_uuid") or "").strip()
        if destination_uuid:
            create_payload["destination_uuid"] = destination_uuid
        response = client.request("POST", "/api/v1/services", create_payload)
        operator_log(args, "coolify-sync create-service result", ok=response.ok, status=response.status)
        tried.append(
            {
                "operation": "create-service",
                "payload_keys": sorted(create_payload.keys()),
                "docker_compose_raw_encoding": "base64",
                "response": response_to_dict(response),
            }
        )
        if response.ok:
            service_uuid = coolify_service_uuid_from_body(response.body)
        elif response.status not in {400, 404, 405, 422}:
            return {"ok": False, "stage": "create-service", "context": create_context, "tried": tried}

    if not service_uuid:
        return {
            "ok": False,
            "stage": "missing-service-uuid",
            "message": (
                "Could not create or identify a Coolify service by API. If create-service still failed, create one "
                "Docker Compose Empty resource once, then rerun with --coolify-service-uuid. The same command will "
                "update/deploy it afterwards."
            ),
            "context": create_context,
            "tried": tried,
        }

    operator_log(args, "coolify-sync update-service start", service_uuid=service_uuid)
    update_payloads = [
        {"docker_compose_raw": compose_b64, "name": service_name},
        {"docker_compose_raw": compose_b64},
        {"docker_compose": compose, "name": service_name},
        {"compose": compose, "name": service_name},
    ]
    update_paths = [f"/api/v1/services/{service_uuid}", f"/api/v1/services/{service_uuid}/compose"]
    update_ok = False
    for path in update_paths:
        for payload in update_payloads:
            encoding = "base64" if "docker_compose_raw" in payload else "plain"
            operator_log(args, "coolify-sync update-service request", method="PATCH", path=path, encoding=encoding)
            response = client.request("PATCH", path, payload)
            operator_log(args, "coolify-sync update-service result", ok=response.ok, status=response.status, path=path)
            tried.append(
                {
                    "operation": "update-service",
                    "path": path,
                    "payload_keys": sorted(payload.keys()),
                    "docker_compose_raw_encoding": encoding,
                    "response": response_to_dict(response),
                }
            )
            if response.ok:
                update_ok = True
                break
            if response.status == 405:
                operator_log(args, "coolify-sync update-service request", method="PUT", path=path, encoding=encoding)
                response = client.request("PUT", path, payload)
                operator_log(args, "coolify-sync update-service result", ok=response.ok, status=response.status, path=path)
                tried.append(
                    {
                        "operation": "update-service-put",
                        "path": path,
                        "payload_keys": sorted(payload.keys()),
                        "docker_compose_raw_encoding": encoding,
                        "response": response_to_dict(response),
                    }
                )
                if response.ok:
                    update_ok = True
                    break
        if update_ok:
            break

    result_context = create_context or ({"existing_service": existing_service_context} if existing_service_context else {})
    if not update_ok:
        return {"ok": False, "stage": "update-service", "service_uuid": service_uuid, "context": result_context, "tried": tried}

    deploy_result: dict[str, Any] | None = None
    if deploy:
        operator_log(args, "coolify-sync deploy-service start", service_uuid=service_uuid)
        deploy_paths = [
            f"/api/v1/deploy?uuid={urllib.parse.quote(service_uuid)}&force=true",
            f"/api/v1/services/{service_uuid}/start",
            f"/api/v1/services/{service_uuid}/restart",
            f"/api/v1/services/{service_uuid}/deploy",
        ]
        for path in deploy_paths:
            method = "GET" if path.startswith("/api/v1/deploy?") else "POST"
            operator_log(args, "coolify-sync deploy-service request", method=method, path=path)
            response = client.request(method, path)
            operator_log(args, "coolify-sync deploy-service result", ok=response.ok, status=response.status, path=path)
            tried.append({"operation": "deploy", "path": path, "response": response_to_dict(response)})
            if response.ok:
                deploy_result = response_to_dict(response)
                break
        if deploy_result is None:
            return {"ok": False, "stage": "deploy-service", "service_uuid": service_uuid, "context": result_context, "tried": tried}

    operator_log(args, "coolify-sync done", service_uuid=service_uuid, deploy_requested=bool(deploy_result))
    return {
        "ok": True,
        "service_uuid": service_uuid,
        "service_name": service_name,
        "deployed": bool(deploy_result),
        "deploy_requested": bool(deploy_result),
        "deploy_result": deploy_result,
        "context": result_context,
        "tried": tried,
        **({"local_coolify": local_context} if local_context else {}),
    }


def apply_network(plan: NetworkPlan, args: argparse.Namespace) -> dict[str, Any]:
    configure_local_coolify_defaults(plan, args)
    if len({service.host for service in plan.services}) > 1:
        raise PlanError("apply currently supports single-host plans. Split-host deployment needs explicit shared genesis/key distribution.")
    operator_log(args, "apply start", network=plan.name, all=bool(getattr(args, "all", False)), dry_run=bool(getattr(args, "dry_run", False)))
    phases: list[dict[str, Any]] = []
    if bool(getattr(args, "dry_run", False)):
        operator_log(args, "apply phase skipped", phase="coolify-check", reason="dry-run")
        phases.append({"phase": "coolify-check", "result": {"ok": True, "dry_run": True, "url": str(getattr(args, "coolify_url", "") or "")}})
    else:
        operator_log(args, "apply phase start", phase="coolify-check")
        check_result = coolify_check(args, plan)
        phases.append({"phase": "coolify-check", "result": check_result})
        operator_log(args, "apply phase result", phase="coolify-check", ok=check_result.get("ok"), status=check_result.get("status"))
        if not check_result.get("ok"):
            return {"ok": False, "network": plan.name, "phases": phases}

    if is_local_coolify_plan(plan):
        operator_log(args, "apply phase start", phase="local-qbft-subnet")
        if bool(getattr(args, "dry_run", False)):
            subnet_result = {"ok": True, "dry_run": True, "selected_subnet": plan.docker_subnet, "source": "dry-run"}
        else:
            plan, subnet_result = prepare_local_qbft_subnet(plan, args)
        phases.append({"phase": "local-qbft-subnet", "result": subnet_result})
        operator_log(
            args,
            "apply phase result",
            phase="local-qbft-subnet",
            ok=subnet_result.get("ok"),
            selected_subnet=subnet_result.get("selected_subnet") or subnet_result.get("requested_subnet"),
            changed=subnet_result.get("changed"),
            source=subnet_result.get("source"),
        )
        if not subnet_result.get("ok"):
            return {"ok": False, "network": plan.name, "phases": phases}

    operator_log(args, "apply phase start", phase="coolify-sync")
    sync_result = coolify_sync(plan, args, deploy=not bool(getattr(args, "no_deploy", False)))
    phases.append({"phase": "coolify-sync", "result": sync_result})
    operator_log(args, "apply phase result", phase="coolify-sync", ok=sync_result.get("ok"), stage=sync_result.get("stage", "done"))
    if not sync_result.get("ok"):
        return {"ok": False, "network": plan.name, "phases": phases}
    if bool(getattr(args, "dry_run", False)):
        operator_log(args, "apply done", ok=True, dry_run=True)
        return {"ok": True, "dry_run": True, "network": plan.name, "phases": phases}
    if not bool(getattr(args, "skip_wait_rpc", False)):
        operator_log(args, "apply phase start", phase="wait-rpc")
        wait_result = wait_for_rpc(plan, args)
        phases.append({"phase": "wait-rpc", "result": wait_result})
        operator_log(args, "apply phase result", phase="wait-rpc", ok=wait_result.get("ok"), block_number=wait_result.get("block_number"))
    if bool(getattr(args, "deploy_contracts", False)) or bool(getattr(args, "all", False)):
        operator_log(args, "apply phase start", phase="deploy-contracts")
        contract_result = deploy_contracts(plan, args)
        phases.append({"phase": "deploy-contracts", "result": contract_result})
        operator_log(args, "apply phase result", phase="deploy-contracts", ok=contract_result.get("ok"), returncode=contract_result.get("returncode"))
        if not contract_result.get("ok"):
            return {"ok": False, "network": plan.name, "phases": phases}
    operator_log(args, "apply done", ok=True)
    return {"ok": True, "network": plan.name, "phases": phases}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan, render, and apply Coolify-managed Besu QBFT network layouts.")
    parser.add_argument(
        "action",
        nargs="?",
        default="docs",
        choices=[
            "docs",
            "help",
            "quickstart",
            "list",
            "plan",
            "compose",
            "commands",
            "write",
            "validate",
            "coolify-check",
            "coolify-discover",
            "coolify-sync",
            "wait-rpc",
            "deploy-contracts",
            "apply",
        ],
        help="Action to run. Omit this to print the step-by-step deployment runbook.",
    )
    parser.add_argument("network", nargs="?", default="testnet", help="Seed name from NETWORK_SEEDS or a JSON seed path.")
    parser.add_argument("--host", default="", help="Host id for compose rendering. Defaults to the first host with services.")
    parser.add_argument("--out", default="", help="Output directory for the write action.")
    parser.add_argument("--besu-image", default="", help="Override the Besu image tag in the seed.")
    parser.add_argument("--public-rpc", action="store_true", help="Bind the non-validator RPC host port to 0.0.0.0 for operator access.")
    parser.add_argument("--allow-mainnet", action="store_true", help="Allow planning from a seed marked requires_mainnet_ack, such as mainnet.")

    parser.add_argument("--single-host", default="", help="Override a single-host seed with this SSH target/address, e.g. root@157.245.92.74.")
    parser.add_argument("--target-address", default="", help="Override the public host/IP used in the plan.")
    parser.add_argument("--runtime-root", default="", help="Override runtime root for all hosts in the seed.")

    parser.add_argument("--coolify-url", default="", help="Coolify base URL, e.g. http://157.245.92.74:8000.")
    parser.add_argument("--coolify-token", default="", help="Coolify bearer token. Prefer --coolify-token-env when possible.")
    parser.add_argument("--coolify-token-env", default=DEFAULT_COOLIFY_TOKEN_ENV, help="Environment variable containing the Coolify token.")
    parser.add_argument("--coolify-token-file", default="", help="File containing the Coolify token.")
    parser.add_argument("--coolify-timeout-s", type=float, default=DEFAULT_COOLIFY_API_TIMEOUT_S)
    parser.add_argument("--coolify-retries", type=int, default=DEFAULT_COOLIFY_API_RETRIES, help="Retry transient Coolify API connection/timeout failures this many times.")
    parser.add_argument("--coolify-retry-sleep-s", type=float, default=DEFAULT_COOLIFY_API_RETRY_SLEEP_S, help="Seconds to sleep between transient Coolify API retries.")

    parser.add_argument("--coolify-service-uuid", default="", help="Existing Coolify service UUID to update/deploy.")
    parser.add_argument("--coolify-service-name", default="", help="Coolify service name to create/use.")
    parser.add_argument("--coolify-project-uuid", default="", help="Project UUID for API service creation when no service UUID exists.")
    parser.add_argument("--coolify-project-name", default="", help="Project name to auto-select when discovering project UUID.")
    parser.add_argument("--coolify-server-uuid", default="", help="Server UUID for API service creation when no service UUID exists.")
    parser.add_argument("--coolify-server-name", default="", help="Server name to auto-select when discovering server UUID.")
    parser.add_argument("--coolify-destination-uuid", default="", help="Destination UUID for API service creation when no service UUID exists.")
    parser.add_argument("--coolify-environment", default="", help="Environment name for API service creation. Defaults to plan.environment.")
    parser.add_argument("--coolify-environment-uuid", default="", help="Environment UUID for API service creation when the name is ambiguous.")
    parser.add_argument("--no-coolify-create-environment", action="store_true", help="Do not create the Coolify environment if it is missing.")

    parser.add_argument("--dry-run", action="store_true", help="Render/show intended changes without mutating Coolify or deploying contracts.")
    parser.add_argument("--no-bootstrap", action="store_true", help="Do not include qbft-bootstrap service in generated compose.")
    parser.add_argument("--bind-runtime-root", action="store_true", help="Use host bind mount with Coolify is_directory instead of a named volume.")
    parser.add_argument("--no-deploy", action="store_true", help="For coolify-sync/apply, update the service but do not trigger a deployment.")

    parser.add_argument("--rpc-url", default="", help="Externally reachable non-validator RPC URL. Inferred from --public-rpc when possible.")
    parser.add_argument("--container-rpc-url", default="", help="RPC URL reachable from the Foundry deployment container. Local Coolify test defaults to http://mc-qbft-rpc:8545.")
    parser.add_argument("--rpc-timeout-s", type=float, default=DEFAULT_RPC_WAIT_TIMEOUT_S)
    parser.add_argument("--rpc-poll-interval-s", type=float, default=DEFAULT_RPC_POLL_INTERVAL_S)
    parser.add_argument(
        "--rpc-min-peers",
        type=int,
        default=DEFAULT_RPC_MIN_PEERS_AUTO,
        help="Minimum net_peerCount required before RPC is ready. Default: 0 for one Besu node, 1 for multi-node topologies.",
    )
    parser.add_argument(
        "--no-rpc-require-block-advance",
        action="store_true",
        help="Do not require eth_blockNumber to advance across successful probes before deploy-contracts.",
    )
    parser.add_argument("--skip-wait-rpc", action="store_true", help="Skip wait-rpc phase in apply.")

    parser.add_argument("--all", action="store_true", help="For apply: deploy Compose, wait for RPC, and deploy contracts.")
    parser.add_argument("--deploy-contracts", action="store_true", help="For apply: deploy contracts after RPC is healthy.")
    parser.add_argument("--deployment-run-id", default="", help="Override the contract deployment run id.")
    parser.add_argument("--deployment-project-name", default="", help="Override contract deployment project name.")
    parser.add_argument("--deployment-environment", default="", help="Override contract deployment environment.")
    parser.add_argument("--deployment-source-kind", default="", help="Override the source.kind recorded in the deployment manifest.")
    parser.add_argument("--deployment-output-dir", default="", help="Override runtime deployment output directory.")
    parser.add_argument("--foundry-image", default=DEFAULT_FOUNDRY_IMAGE)
    parser.add_argument("--docker-subnet", default="", help="Override the local test QBFT Docker subnet before rendering Coolify compose.")
    parser.add_argument("--foundry-docker-network", default="bridge", help="Docker network for local Foundry container; bridge works for public RPC URLs.")
    parser.add_argument("--deploy-contracts-timeout-s", type=float, default=DEFAULT_DEPLOY_CONTRACTS_TIMEOUT_S)
    parser.add_argument("--quiet", action="store_true", help="Suppress operator progress logs; final JSON is still printed.")
    return parser.parse_args(argv)


def build_plan_from_args(args: argparse.Namespace) -> NetworkPlan:
    return build_plan(
        args.network,
        besu_image=args.besu_image or None,
        allow_mainnet=bool(args.allow_mainnet),
        public_rpc=True if args.public_rpc else None,
        single_host=args.single_host or None,
        target_address=args.target_address or None,
        coolify_url=args.coolify_url or None,
        runtime_root=args.runtime_root or None,
    )


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2))


def render_operator_runbook() -> str:
    """Return the no-surprises operator runbook printed by the docs action."""

    return textwrap.dedent(
        r"""
        Main Computer Coolify QBFT network runbook
        =========================================

        This tool renders and deploys a Besu QBFT network into a Coolify Docker
        Compose service. Run this command with no arguments any time you forget
        the exact sequence.

        Recommended server size
        -----------------------
        Minimum for Coolify plus the default four-validator testnet and one dedicated RPC node: 4 vCPU, 8 GB RAM.
        The mainnet seed is intentionally lighter for bring-up: one validator plus one dedicated RPC node on its own box.
        Smaller 2 vCPU / 4 GB boxes may work for short rehearsals, but tiny 1-2 GB boxes can publish Docker ports
        while Besu is still starved or unhealthy.

        1. Prepare the remote Linux server
        ----------------------------------
        From your Windows machine:

            ssh root@<SERVER_IP>

        On the server, install/verify Docker:

            curl -fsSL https://get.docker.com | sh
            systemctl enable --now docker
            docker version

        If you use UFW or a cloud firewall, allow SSH, Coolify, HTTP/HTTPS,
        and the public testnet RPC port:

            ufw allow OpenSSH
            ufw allow 80/tcp
            ufw allow 443/tcp
            ufw allow 8000/tcp
            ufw allow 6001/tcp
            ufw allow 6002/tcp
            ufw allow 30010/tcp
            ufw --force enable

        2. Install Coolify on the remote server
        ---------------------------------------
        Still on the server, run the official quick installer:

            curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash

        Then open Coolify in your browser:

            http://<SERVER_IP>:8000

        Create the first admin account immediately after installation.

        3. Create a Coolify API token
        -----------------------------
        In the Coolify UI, create a new API token. Keep it private. If you paste
        it into chat/logs, rotate it before using the server for anything real.

        In PowerShell, store the token in an environment variable:

            $env:MAIN_COMPUTER_COOLIFY_TOKEN = "paste-your-token-here"

        4. Set the target IP and Coolify URL locally
        -------------------------------------------
        In your repo checkout on Windows:

            $hostIp = "<SERVER_IP>"
            $coolifyUrl = "http://<SERVER_IP>:8000"

        Use exactly one http:// prefix. Do not write http://http://... and do
        not put :8000 after a trailing slash.

        5. Check that the Coolify API is reachable
        ------------------------------------------

            python .\tools\coolify_qbft_network.py coolify-check testnet `
              --coolify-url $coolifyUrl `
              --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN

        Expected: JSON with "ok": true.

        6. Discover Coolify project/server context
        ------------------------------------------

            python .\tools\coolify_qbft_network.py coolify-discover testnet `
              --single-host root@$hostIp `
              --coolify-url $coolifyUrl `
              --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN

        If Coolify has one project/server/environment, the tool can usually
        select it automatically. If discovery reports ambiguity, copy the UUIDs
        it prints and pass them to apply with --coolify-project-uuid,
        --coolify-server-uuid, and --coolify-environment.

        7. Deploy the selected QBFT network
        ------------------------------------

            python .\tools\coolify_qbft_network.py apply testnet --all `
              --single-host root@$hostIp `
              --coolify-url $coolifyUrl `
              --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN `
              --coolify-service-name main-computer-qbft-testnet-testnet-a `
              --public-rpc

        This creates or updates the Coolify Docker Compose service, deploys it,
        waits for RPC, then deploys contracts when --all is present.

        The testnet public RPC URL will be:

            http://<SERVER_IP>:30010

        Expected chain id:

            0x28757b1

        Contract deployment now publishes this Coolify surface as the first-class
        testnet environment, not the local loopback test environment:

            runtime/deployments/testnet/latest.json

        After contracts deploy, run the Hub and smoke against the same network key:

            python -m main_computer.cli hub --network testnet
            python .\scripts\smoke_hub_network_client.py --network testnet

        8. Verify on the remote server
        ------------------------------

            ssh root@<SERVER_IP>

            docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' \
              | grep -E 'qbft|validator|rpc|besu|30010' || true

            curl -v --max-time 10 http://127.0.0.1:30010 \
              -H 'content-type: application/json' \
              --data '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}'

        Expected response:

            {"jsonrpc":"2.0","id":1,"result":"0x28757b1"}

        9. Safe reruns
        --------------

        Use the same --coolify-service-name on reruns. The tool now refuses to
        blindly create another same-purpose service when it detects duplicate
        Coolify services returned by the Coolify API.

        If you already know the service UUID, you can pin the target explicitly:

            python .\tools\coolify_qbft_network.py apply testnet --all `
              --single-host root@$hostIp `
              --coolify-url $coolifyUrl `
              --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN `
              --coolify-service-uuid <COOLIFY_SERVICE_UUID> `
              --public-rpc

        10. Useful commands
        -------------------

        Render compose without deploying:

            python .\tools\coolify_qbft_network.py compose testnet `
              --single-host root@$hostIp `
              --public-rpc

        Print the planned topology:

            python .\tools\coolify_qbft_network.py plan testnet `
              --single-host root@$hostIp `
              --public-rpc

        Show this runbook again:

            python .\tools\coolify_qbft_network.py docs
        """
    ).strip() + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.action in {"docs", "help", "quickstart"}:
        print(render_operator_runbook())
        return 0

    if args.action == "list":
        print_json({"seeds": sorted(NETWORK_SEEDS)})
        return 0

    try:
        plan = build_plan_from_args(args)
        if args.action == "validate":
            print_json({"ok": True, "network": plan.name, "warnings": list(plan.warnings)})
            return 0
        if args.action == "plan":
            print_json(plan.to_dict())
            return 0
        if args.action == "commands":
            print(render_commands(plan))
            return 0
        if args.action == "compose":
            host_id = args.host or next(host.id for host in plan.hosts if services_for_host(plan, host.id))
            print(
                render_compose_for_host(
                    plan,
                    host_id,
                    include_bootstrap=not bool(args.no_bootstrap),
                    managed_volume=not bool(args.bind_runtime_root),
                )
            )
            return 0
        if args.action == "write":
            out_dir = Path(args.out or Path("runtime") / "coolify-qbft" / plan.name)
            write_outputs(plan, out_dir)
            print_json({"ok": True, "out": str(out_dir), "network": plan.name})
            return 0
        if args.action == "coolify-check":
            result = coolify_check(args, plan)
            print_json(result)
            return 0 if result.get("ok") else 1
        if args.action == "coolify-discover":
            result = coolify_discover(args, plan)
            print_json(result)
            return 0 if result.get("ok") else 1
        if args.action == "coolify-sync":
            result = coolify_sync(plan, args, deploy=not bool(args.no_deploy))
            print_json(result)
            return 0 if result.get("ok") else 1
        if args.action == "wait-rpc":
            print_json(wait_for_rpc(plan, args))
            return 0
        if args.action == "deploy-contracts":
            result = deploy_contracts(plan, args)
            print_json(result)
            return 0 if result.get("ok") else 1
        if args.action == "apply":
            result = apply_network(plan, args)
            print_json(result)
            return 0 if result.get("ok") else 1
    except (PlanError, CoolifyError, TimeoutError, socket.timeout) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Unsupported action: {args.action}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
