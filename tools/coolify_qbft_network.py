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
Raw Docker Compose resources.  A later action can call Coolify APIs, but the
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
import secrets
import socket
import ssl
import subprocess
import sys
import time
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
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
DEFAULT_CHAIN_OBSERVATION_TIMEOUT_S = 8.0
DEFAULT_QBFT_JSON_RPC_TIMEOUT_S = 8.0
DEFAULT_MUTATE_RPC_WAIT_TIMEOUT_S = 300.0
DEFAULT_VALIDATOR_RETIRE_MEMBERSHIP_TIMEOUT_S = 300.0
DEFAULT_MUTATE_STABLE_CHAIN_SUCCESSES = 2
DEFAULT_JSON_RPC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "MainComputerQbftDeployer/1.0"
)
TRAEFIK_DYNAMIC_CONFIG_DIR = "/data/coolify/proxy/dynamic"
TRAEFIK_DYNAMIC_CONFIG_IMAGE = "alpine:3.20"
RETIRED_QBFT_CLEANUP_IMAGE = "docker:27-cli"
QBFT_HELPER_PARK_IMAGE = TRAEFIK_DYNAMIC_CONFIG_IMAGE
QBFT_HELPER_PARK_SERVICE_KEYS = (
    "qbft-bootstrap",
    "qbft-runtime-import",
    "qbft-validator-key-init",
    "qbft-validator-vote",
    "qbft-static-refresh",
    "qbft-retire-orphan-cleanup",
)
TRAEFIK_DYNAMIC_CONFIG_REFRESH_S = 300
QBFT_CONFIG_TRANSFER_IMAGE = "python:3.12-alpine"
QBFT_CONFIG_EXPORT_IMAGE = "python:3.12-alpine"
DEFAULT_QBFT_CONFIG_EXPORT_PORT = 38173
DEFAULT_QBFT_CONFIG_EXPORT_TIMEOUT_S = 240.0
DEFAULT_QBFT_CONFIG_EXPORT_TRANSPORT = "public-entry"
QBFT_CONSENSUS_TWIDDLE_IMAGE = "python:3.12-alpine"
DEFAULT_QBFT_CONSENSUS_TWIDDLE_PORT = 38174
DEFAULT_QBFT_CONSENSUS_TWIDDLE_TIMEOUT_S = 180.0
QBFT_CONFIG_EXPORT_PUBLIC_PATH_PREFIX = "/__main-computer/qbft-config"
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
DEFAULT_ANVIL_ACCOUNT_SET = {address.lower() for address in DEFAULT_FUNDED_ACCOUNTS}
DEPLOYMENT_ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEPLOYMENT_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

LOCAL_COOLIFY_SEEDS = {"test"}
DEFAULT_GENERATED_OFFICE_ENVIRONMENTS = {"test", "testnet"}
LOCAL_COOLIFY_DEFAULT_URL = "http://127.0.0.1:8000"
LOCAL_COOLIFY_TOKEN_RELATIVE_PATH = Path("runtime") / "coolify-local-docker" / "api-token.txt"
PRIVATE_STATE_RELATIVE_PATH = Path("runtime") / "state" / "main_computer.private.yaml"
HUB_SERVICE_TOOL_PATH = Path(__file__).resolve().with_name("coolify_hub_service.py")
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
        "description": "Low-resource single-Besu QBFT rehearsal network managed by Coolify.",
        "environment": "testnet",
        "chain_id": 42424241,
        "compose_project": "main-computer-qbft-testnet",
        "docker_network": "mc-qbft-testnet-network",
        "docker_subnet": "172.28.241.0/24",
        "besu_image": DEFAULT_BESU_IMAGE,
        "runtime_root": "/srv/main-computer/qbft-testnet/runtime",
        "public_rpc": False,
        "topology_policy": {
            "minimum_validators": 1,
            "minimum_rpc_nodes": 0,
            "validator_warning_below": 4,
            "validator_warning": (
                "Remote testnet is in single-Besu bring-up mode for the current test machine; "
                "it proves the Hub/RPC/contract path but is not fault-tolerant."
            ),
        },
        "hosts": {
            "a": {
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
                "host": "a",
                "container_ip": "172.28.241.11",
                # The low-resource remote testnet intentionally has no dedicated
                # RPC sidecar; validator-1 owns the operator RPC port.
                "rpc_host_port": 30010,
                "p2p_host_port": 30311,
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
VALIDATOR_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


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
    project_name: str = ""
    project_uuid: str = ""
    server_name: str = ""
    server_uuid: str = ""
    destination_uuid: str = ""
    environment_uuid: str = ""
    api_token: str = ""
    api_token_env: str = ""
    api_token_file: str = ""
    service_uuid: str = ""


@dataclass(frozen=True)
class PlannedService:
    id: str
    role: str
    roles: tuple[str, ...]
    host: str
    container_ip: str
    rpc_host_port: int | None
    p2p_host_port: int | None
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
    external_rpc_url: str = ""

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
            **({"external_rpc_url": self.external_rpc_url} if self.external_rpc_url else {}),
            "rpc_public_entry": rpc_public_entry_plan(self),
            "topology_policy": asdict(self.topology_policy),
            "hosts": [planned_host_to_dict(host) for host in self.hosts],
            "services": [asdict(service) for service in self.services],
            "warnings": list(self.warnings),
            "operator_checks": operator_checks(self),
        }


def planned_host_to_dict(host: PlannedHost) -> dict[str, Any]:
    payload = asdict(host)
    if payload.get("api_token"):
        payload["api_token"] = "<redacted>"
    return {key: value for key, value in payload.items() if value not in ("", None)}


def safe_id(value: object, *, kind: str) -> str:
    text = str(value or "").strip().lower()
    if not SAFE_ID_RE.match(text):
        raise PlanError(f"Invalid {kind} id {value!r}; use lowercase letters, numbers, '.', '_' or '-'.")
    return text


def split_selected_ids(value: object, *, kind: str) -> tuple[str, ...]:
    """Return a duplicate-free tuple of comma-separated logical ids."""

    if value in (None, ""):
        return ()
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = []
        for item in value:
            raw_items.extend(str(item).split(","))
    else:
        raw_items = [str(value)]

    selected: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        item = str(raw or "").strip()
        if not item:
            continue
        clean = safe_id(item, kind=kind)
        if clean in seen:
            raise PlanError(f"Duplicate {kind} id in selection: {clean}")
        seen.add(clean)
        selected.append(clean)
    return tuple(selected)


def normalize_validator_address(value: object, *, field: str = "validator address") -> str:
    text = str(value or "").strip()
    if text and not text.startswith("0x"):
        text = f"0x{text}"
    if not VALIDATOR_ADDRESS_RE.match(text):
        raise PlanError(f"Invalid {field}: {value!r}")
    return text.lower()


def normalize_validator_address_map(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for raw_key, raw_address in value.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        try:
            result[safe_id(key, kind="runtime_dir")] = normalize_validator_address(raw_address, field=f"validator address for {key}")
        except PlanError:
            continue
    return result


def normalize_validator_public_key(value: object, *, field: str = "validator public key") -> str:
    text = str(value or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if text.startswith("04") and len(text) == 130:
        text = text[2:]
    if not re.fullmatch(r"[0-9a-f]{128}", text):
        raise PlanError(f"Invalid {field}: {value!r}")
    return text


def normalize_validator_public_key_map(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for raw_key, raw_key_value in value.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        try:
            result[safe_id(key, kind="runtime_dir")] = normalize_validator_public_key(raw_key_value, field=f"validator public key for {key}")
        except PlanError:
            continue
    return result


def normalize_runtime_static_nodes_map(value: object) -> dict[str, list[str]]:
    """Normalize exported per-runtime static-node files.

    The exporter only emits non-secret ``static-nodes.json`` contents. Mutation
    code uses this to verify the active mounted runtime, not merely the generated
    compose payload, before any QBFT vote is submitted.
    """

    if not isinstance(value, Mapping):
        return {}
    result: dict[str, list[str]] = {}
    for raw_key, raw_nodes in value.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        runtime_dir = safe_id(key, kind="runtime_dir")
        nodes: list[str] = []
        if isinstance(raw_nodes, str):
            try:
                parsed = json.loads(raw_nodes)
            except json.JSONDecodeError:
                parsed = []
        else:
            parsed = raw_nodes
        if isinstance(parsed, list):
            for item in parsed:
                node = str(item or "").strip()
                if node.startswith("enode://") and node not in nodes:
                    nodes.append(node)
        result[runtime_dir] = nodes
    return result


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


def private_value_is_known(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and not (value.strip().startswith("<") and value.strip().endswith(">"))
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def load_private_state_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError as exc:
        raise PlanError("PyYAML is required to read runtime/state/main_computer.private.yaml.") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise PlanError(f"Private state must contain a YAML mapping: {path}")
    return data


def mapping_get(mapping: Mapping[str, Any], *keys: str) -> Any:
    node: Any = mapping
    for key in keys:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


def first_known(*values: Any) -> str:
    for value in values:
        if private_value_is_known(value):
            return str(value).strip()
    return ""


def private_state_path_for(root: Path, private_state_path: str | Path | None) -> Path:
    if private_state_path is None or str(private_state_path).strip() == "":
        return root / PRIVATE_STATE_RELATIVE_PATH
    path = Path(private_state_path)
    return path if path.is_absolute() else root / path


def container_ip_for_private_instance(subnet: ipaddress._BaseNetwork, *, role: str, index: int) -> str:
    base = int(subnet.network_address)
    offset = (20 + index) if role == "rpc" else (11 + index)
    candidate = ipaddress.ip_address(base + offset)
    if candidate not in subnet:
        raise PlanError(f"docker_subnet {subnet} is too small for generated QBFT container IP offset {offset}.")
    return str(candidate)


def private_state_seed(
    network_name: str,
    *,
    root: Path | None = None,
    private_state_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Return a seed built from manual private-state QBFT instances, if present."""

    root = root or repo_root()
    path = private_state_path_for(root, private_state_path)
    state = load_private_state_yaml(path)
    network = mapping_get(state, "networks", network_name)
    instances = mapping_get(state, "networks", network_name, "qbft", "instances")
    if not isinstance(network, Mapping) or not isinstance(instances, Mapping) or not instances:
        return None

    default_seed = copy.deepcopy(NETWORK_SEEDS.get(network_name, {}))
    chain_id = network.get("chain_id", default_seed.get("chain_id"))
    if not private_value_is_known(chain_id):
        raise PlanError(f"networks.{network_name}.chain_id is required when private-state qbft.instances is present.")

    environment = first_known(network.get("kind"), default_seed.get("environment"), network_name).lower()
    docker_subnet_text = first_known(
        mapping_get(network, "qbft", "docker_subnet"),
        default_seed.get("docker_subnet"),
        "172.28.241.0/24",
    )
    try:
        subnet = ipaddress.ip_network(docker_subnet_text, strict=False)
    except ValueError as exc:
        raise PlanError(f"networks.{network_name}.qbft.docker_subnet is invalid: {docker_subnet_text!r}") from exc

    coolify_state = state.get("coolify") if isinstance(state.get("coolify"), Mapping) else {}
    coolify_hosts = coolify_state.get("hosts") if isinstance(coolify_state, Mapping) and isinstance(coolify_state.get("hosts"), Mapping) else {}
    local_test = coolify_state.get("local_test") if isinstance(coolify_state, Mapping) and isinstance(coolify_state.get("local_test"), Mapping) else {}

    slot_to_host_id: dict[str, str] = {}
    hosts: dict[str, dict[str, Any]] = {}
    services: list[dict[str, Any]] = []
    role_indexes = {"validator": 0, "rpc": 0}

    for raw_instance_id, raw_instance in instances.items():
        instance_id = safe_id(raw_instance_id, kind="service")
        if not isinstance(raw_instance, Mapping):
            raise PlanError(f"networks.{network_name}.qbft.instances.{raw_instance_id} must be a mapping.")

        raw_roles = raw_instance.get("roles")
        if not isinstance(raw_roles, list) or not raw_roles:
            raise PlanError(f"networks.{network_name}.qbft.instances.{raw_instance_id}.roles must list validator and/or rpc.")
        roles = tuple(dict.fromkeys(str(role).strip().lower() for role in raw_roles if str(role).strip()))
        bad_roles = [role for role in roles if role not in {"validator", "rpc"}]
        if bad_roles:
            raise PlanError(f"networks.{network_name}.qbft.instances.{raw_instance_id}.roles has unsupported role(s): {bad_roles}")
        if not roles:
            raise PlanError(f"networks.{network_name}.qbft.instances.{raw_instance_id}.roles must not be empty.")

        role = "validator" if "validator" in roles else "rpc"
        coolify_slot = str(raw_instance.get("coolify_host") or "").strip()
        if not coolify_slot:
            raise PlanError(f"networks.{network_name}.qbft.instances.{raw_instance_id}.coolify_host is required.")
        host_payload: Mapping[str, Any]
        if coolify_slot == "local_test":
            host_payload = local_test if isinstance(local_test, Mapping) else {}
        else:
            maybe_host = coolify_hosts.get(coolify_slot) if isinstance(coolify_hosts, Mapping) else None
            if not isinstance(maybe_host, Mapping):
                raise PlanError(
                    f"networks.{network_name}.qbft.instances.{raw_instance_id}.coolify_host references missing coolify.hosts.{coolify_slot}."
                )
            host_payload = maybe_host

        host_id = slot_to_host_id.setdefault(coolify_slot, safe_id(coolify_slot, kind="host"))
        if host_id not in hosts:
            public_ip = first_known(host_payload.get("public_ip"), host_payload.get("host"), host_payload.get("address"), host_payload.get("vpn_ip"))
            host_name = first_known(host_payload.get("name"), coolify_slot)
            hosts[host_id] = {
                "ssh": first_known(host_payload.get("ssh")),
                "address": public_ip,
                "coolify_url": first_known(host_payload.get("url"), host_payload.get("coolify_url")),
                "runtime_root": first_known(
                    host_payload.get("runtime_root"),
                    default_seed.get("runtime_root"),
                    f"/srv/main-computer/qbft-{network_name}/runtime",
                ),
                "project_name": first_known(host_payload.get("project_name"), coolify_state.get("project_name") if isinstance(coolify_state, Mapping) else ""),
                "project_uuid": first_known(host_payload.get("project_uuid"), coolify_state.get("project_uuid") if isinstance(coolify_state, Mapping) else ""),
                "server_name": first_known(host_payload.get("server_name"), coolify_state.get("server_name") if isinstance(coolify_state, Mapping) else ""),
                "server_uuid": first_known(host_payload.get("server_uuid"), coolify_state.get("server_uuid") if isinstance(coolify_state, Mapping) else ""),
                "destination_uuid": first_known(host_payload.get("destination_uuid"), coolify_state.get("destination_uuid") if isinstance(coolify_state, Mapping) else ""),
                "environment_uuid": first_known(host_payload.get("environment_uuid"), coolify_state.get("environment_uuid") if isinstance(coolify_state, Mapping) else ""),
                "api_token": first_known(host_payload.get("api_token")),
                "api_token_env": first_known(host_payload.get("api_token_env")),
                "api_token_file": first_known(host_payload.get("api_token_file")),
                "service_uuid": first_known(host_payload.get("qbft_service_uuid"), host_payload.get("service_uuid")),
            }

        rpc_host_port = raw_instance.get("rpc_host_port")
        if "rpc" in roles and not private_value_is_known(rpc_host_port):
            raise PlanError(f"networks.{network_name}.qbft.instances.{raw_instance_id}.rpc_host_port is required for rpc role.")
        p2p_host_port = raw_instance.get("p2p_host_port")
        if "validator" in roles and not private_value_is_known(p2p_host_port):
            raise PlanError(f"networks.{network_name}.qbft.instances.{raw_instance_id}.p2p_host_port is required for validator role.")

        index = role_indexes[role]
        role_indexes[role] += 1
        services.append(
            {
                "id": instance_id,
                "role": role,
                "roles": list(roles),
                "host": host_id,
                "container_ip": first_known(raw_instance.get("container_ip"), container_ip_for_private_instance(subnet, role=role, index=index)),
                **({"rpc_host_port": rpc_host_port} if private_value_is_known(rpc_host_port) else {}),
                **({"p2p_host_port": p2p_host_port} if private_value_is_known(p2p_host_port) else {}),
            }
        )

    external_rpc_url = first_known(network.get("rpc"), network.get("rpc_url"), default_seed.get("external_rpc_url"))
    qbft_public_rpc = mapping_get(network, "qbft", "public_rpc")
    if private_value_is_known(qbft_public_rpc):
        public_rpc = bool(qbft_public_rpc)
    else:
        public_rpc = bool(default_seed.get("public_rpc", False) or external_rpc_url)

    seed = copy.deepcopy(default_seed)
    seed.update(
        {
            "description": first_known(network.get("display_name"), default_seed.get("description"), f"Private-state QBFT topology for {network_name}"),
            "environment": environment,
            "chain_id": chain_id,
            "compose_project": first_known(default_seed.get("compose_project"), f"main-computer-qbft-{network_name}"),
            "docker_network": first_known(default_seed.get("docker_network"), f"mc-qbft-{network_name}-network"),
            "docker_subnet": str(subnet),
            "besu_image": first_known(mapping_get(network, "qbft", "besu_image"), default_seed.get("besu_image"), DEFAULT_BESU_IMAGE),
            "runtime_root": first_known(default_seed.get("runtime_root"), f"/srv/main-computer/qbft-{network_name}/runtime"),
            "public_rpc": public_rpc,
            "external_rpc_url": external_rpc_url,
            "hosts": hosts,
            "services": services,
            "source": f"private-state:{path.as_posix()}",
        }
    )
    policy = dict(seed.get("topology_policy") or {})
    policy.setdefault("minimum_validators", 1)
    policy.setdefault("minimum_rpc_nodes", 0)
    seed["topology_policy"] = policy
    return seed


def load_seed_with_private_state(
    name_or_path: str,
    *,
    private_state_path: str | Path | None = None,
) -> tuple[str, dict[str, Any]]:
    name, seed = load_seed(name_or_path)
    if Path(name_or_path).is_file():
        return name, seed
    private_seed = private_state_seed(name, private_state_path=private_state_path)
    if private_seed is not None:
        return name, private_seed
    return name, seed


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
    private_state_path: str | Path | None = None,
    instances: str | tuple[str, ...] | list[str] | None = None,
) -> NetworkPlan:
    name, seed = load_seed_with_private_state(seed_name_or_path, private_state_path=private_state_path)
    selected_instances = split_selected_ids(instances, kind="instance") if instances not in (None, "") else ()

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
            project_name=str(raw_host.get("project_name") or "").strip(),
            project_uuid=str(raw_host.get("project_uuid") or "").strip(),
            server_name=str(raw_host.get("server_name") or "").strip(),
            server_uuid=str(raw_host.get("server_uuid") or "").strip(),
            destination_uuid=str(raw_host.get("destination_uuid") or "").strip(),
            environment_uuid=str(raw_host.get("environment_uuid") or "").strip(),
            api_token=str(raw_host.get("api_token") or "").strip(),
            api_token_env=str(raw_host.get("api_token_env") or "").strip(),
            api_token_file=str(raw_host.get("api_token_file") or "").strip(),
            service_uuid=str(raw_host.get("service_uuid") or "").strip(),
        )

    effective_public_rpc = bool(seed.get("public_rpc", False) if public_rpc is None else public_rpc)
    p2p_bind_host = "0.0.0.0"

    raw_services = seed.get("services")
    if not isinstance(raw_services, list) or not raw_services:
        raise PlanError("seed.services must be a non-empty list")

    if selected_instances:
        raw_services_by_id: dict[str, dict[str, Any]] = {}
        for raw in raw_services:
            if not isinstance(raw, dict):
                raise PlanError("Each service entry must be an object")
            service_id = safe_id(raw.get("id"), kind="service")
            raw_services_by_id[service_id] = raw
        missing = [item for item in selected_instances if item not in raw_services_by_id]
        if missing:
            available = ", ".join(sorted(raw_services_by_id)) or "<none>"
            raise PlanError(
                f"Unknown --instances selection for {name!r}: {', '.join(missing)}. "
                f"Available instances: {available}"
            )
        raw_services = [raw_services_by_id[item] for item in selected_instances]

    has_dedicated_rpc = any(
        isinstance(raw, dict) and str(raw.get("role") or "").strip().lower() == "rpc"
        for raw in raw_services
    )

    seen_ids: set[str] = set()
    seen_global_ports: dict[tuple[str, int], str] = {}
    seen_container_ips: set[str] = set()
    services: list[PlannedService] = []
    for raw in raw_services:
        if not isinstance(raw, dict):
            raise PlanError("Each service entry must be an object")
        service_id = safe_id(raw.get("id"), kind="service")
        if service_id in seen_ids:
            raise PlanError(f"Duplicate service id: {service_id}")
        seen_ids.add(service_id)

        raw_roles = raw.get("roles")
        if isinstance(raw_roles, list):
            roles = tuple(dict.fromkeys(str(item).strip().lower() for item in raw_roles if str(item).strip()))
            role = "validator" if "validator" in roles else ("rpc" if "rpc" in roles else "")
        else:
            role = str(raw.get("role") or "").strip().lower()
            roles = (role,) if role else ()
        if role not in {"validator", "rpc"}:
            raise PlanError(f"Unsupported role for {service_id}: {role!r}")
        bad_roles = [item for item in roles if item not in {"validator", "rpc"}]
        if bad_roles:
            raise PlanError(f"Unsupported roles for {service_id}: {bad_roles!r}")
        if not roles:
            roles = (role,)

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

        raw_rpc_port = raw.get("rpc_host_port")
        rpc_port = require_int(raw_rpc_port, name=f"{service_id}.rpc_host_port") if raw_rpc_port not in (None, "") else None
        raw_p2p_port = raw.get("p2p_host_port")
        p2p_port = require_int(raw_p2p_port, name=f"{service_id}.p2p_host_port") if raw_p2p_port not in (None, "") else None
        if "validator" in roles and p2p_port is None:
            raise PlanError(f"{service_id}.p2p_host_port is required when roles includes validator")
        if "rpc" in roles and rpc_port is None:
            raise PlanError(f"{service_id}.rpc_host_port is required when roles includes rpc")
        service_ports: list[tuple[int, str]] = []
        if rpc_port is not None:
            service_ports.append((rpc_port, "rpc"))
        if p2p_port is not None and service_should_consider_p2p_host_port_for_collision(role):
            service_ports.append((p2p_port, "p2p"))
        for port, purpose in service_ports:
            port_key = (host_id, int(port))
            if port_key in seen_global_ports:
                raise PlanError(
                    f"Host port {port} on host {host_id} is reused by {service_id}.{purpose}; "
                    f"already assigned to {seen_global_ports[port_key]}"
                )
            seen_global_ports[port_key] = f"{service_id}.{purpose}"

        runtime_root = hosts[host_id].runtime_root
        role_runtime_dir = "rpc-node" if role == "rpc" else service_id
        service_public_rpc = bool(
            rpc_port is not None
            and effective_public_rpc
            and ("rpc" in roles or (role == "validator" and not has_dedicated_rpc))
        )
        service_rpc_bind_host = "0.0.0.0" if service_public_rpc else ("127.0.0.1" if rpc_port is not None else "")
        service_rpc_host = hosts[host_id].address if service_public_rpc and hosts[host_id].address else "127.0.0.1"
        services.append(
            PlannedService(
                id=service_id,
                role=role,
                roles=roles,
                host=host_id,
                container_ip=container_ip,
                rpc_host_port=rpc_port,
                p2p_host_port=p2p_port,
                rpc_bind_host=service_rpc_bind_host,
                p2p_bind_host=p2p_bind_host,
                data_path=f"{runtime_root}/{role_runtime_dir}/data",
                static_nodes_path=f"{runtime_root}/{role_runtime_dir}/static-nodes.json",
                rpc_url_on_host=f"http://{service_rpc_host}:{rpc_port}" if rpc_port is not None else "",
                p2p_advertise=f"{hosts[host_id].address or host_id}:{p2p_port}" if p2p_port is not None else "",
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
        external_rpc_url=str(seed.get("external_rpc_url") or "").strip(),
        topology_policy=topology_policy,
        hosts=tuple(
            sorted(
                (
                    host
                    for host in hosts.values()
                    if not selected_instances or host.id in {service.host for service in services}
                ),
                key=lambda item: item.id,
            )
        ),
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
    that node; in that case the first validator with a published RPC host port is
    the RPC endpoint.
    """

    rpc_nodes = [service for service in plan.services if service.role == "rpc" and service.rpc_host_port is not None]
    if rpc_nodes:
        return rpc_nodes[0]
    rpc_role_nodes = [service for service in plan.services if "rpc" in service.roles and service.rpc_host_port is not None]
    if rpc_role_nodes:
        return rpc_role_nodes[0]
    validators = [service for service in plan.services if service.role == "validator" and service.rpc_host_port is not None]
    if validators:
        return validators[0]
    raise PlanError("Plan has no service with a published JSON-RPC host port")


def yaml_quote(value: object) -> str:
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def shell_single_quote(value: object) -> str:
    return "'" + str(value).replace("'", "'\\''") + "'"


def compose_service_key(value: object, *, fallback: str = "service") -> str:
    clean = str(value or "").strip().lower()
    clean = re.sub(r"[^a-z0-9_.-]+", "-", clean).strip("-")
    if not clean:
        clean = fallback
    if not re.match(r"^[a-z0-9][a-z0-9_.-]*$", clean):
        raise PlanError(f"Invalid compose service key derived from {value!r}: {clean!r}")
    return clean


def traefik_router_id(value: object, *, fallback: str = "main-computer-rpc") -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip()).strip("-")
    return clean or fallback


def canonical_rpc_hostname(plan: NetworkPlan) -> str:
    """Return the hostname for networks.<network>.rpc, or an empty string."""

    url = str(plan.external_rpc_url or "").strip()
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise PlanError(f"external_rpc_url must be a valid http(s) URL: {url!r}") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PlanError(f"external_rpc_url must be a valid http(s) URL with a hostname: {url!r}")
    return parsed.hostname


def rpc_public_entry_enabled(plan: NetworkPlan) -> bool:
    return bool(canonical_rpc_hostname(plan))


def rpc_public_entry_dynamic_config_filename(plan: NetworkPlan, host_id: str) -> str:
    return (
        f"main-computer-{traefik_router_id(plan.name).lower()}"
        f"-rpc-public-entry-{traefik_router_id(host_id).lower()}.yml"
    )


def rpc_public_entry_dynamic_config_path(plan: NetworkPlan, host_id: str) -> str:
    return f"{TRAEFIK_DYNAMIC_CONFIG_DIR}/{rpc_public_entry_dynamic_config_filename(plan, host_id)}"


def rpc_public_entry_service_key(plan: NetworkPlan, host_id: str) -> str:
    return compose_service_key(f"{plan.name}-rpc-public-entry-config-{host_id}")


def local_rpc_entry_services(plan: NetworkPlan, host_id: str) -> list[PlannedService]:
    host_id = safe_id(host_id, kind="host")
    return [
        service
        for service in services_for_host(plan, host_id)
        if "rpc" in service.roles and service.rpc_host_port is not None
    ]


def rpc_public_entry_backend_url(plan: NetworkPlan, host: PlannedHost, service: PlannedService) -> str:
    del plan
    if service.rpc_bind_host != "0.0.0.0":
        raise PlanError(
            f"RPC public-entry backend {service.id!r} on host {host.id!r} is not published on 0.0.0.0. "
            "Set networks.<network>.qbft.public_rpc=true or declare networks.<network>.rpc in private state."
        )
    address = str(host.address or "").strip()
    if not address:
        raise PlanError(
            f"Host {host.id!r} needs an address/public_ip before it can render "
            f"the RPC public-entry backend for service {service.id!r}."
        )
    if ":" in address and not address.startswith("[") and not re.match(r"^[A-Za-z0-9.-]+$", address):
        address = f"[{address}]"
    return f"http://{address}:{service.rpc_host_port}"


def rpc_public_entry_plan(plan: NetworkPlan) -> dict[str, Any]:
    hostname = canonical_rpc_hostname(plan)
    if not hostname:
        return {"enabled": False}
    hosts = host_by_id(plan)
    host_entries: dict[str, Any] = {}
    for host in plan.hosts:
        local_rpcs = local_rpc_entry_services(plan, host.id)
        host_entries[host.id] = {
            "action": "write" if local_rpcs else "remove",
            "container_service": rpc_public_entry_service_key(plan, host.id),
            "path": rpc_public_entry_dynamic_config_path(plan, host.id),
            "rpc_instances": [service.id for service in local_rpcs],
            "backends": [rpc_public_entry_backend_url(plan, hosts[host.id], service) for service in local_rpcs],
        }
    return {
        "enabled": True,
        "url": plan.external_rpc_url,
        "hostname": hostname,
        "dynamic_config_dir": TRAEFIK_DYNAMIC_CONFIG_DIR,
        "hosts": host_entries,
    }




def mutation_action_values(args: argparse.Namespace) -> dict[str, str]:
    values = {
        "add": str(getattr(args, "add", "") or "").strip(),
        "retire": str(getattr(args, "retire", "") or "").strip(),
        "promote": str(getattr(args, "promote", "") or "").strip(),
        "demote": str(getattr(args, "demote", "") or "").strip(),
    }
    return {key: value for key, value in values.items() if value}


def parse_mutation_role_targets(value: str, *, kind: str) -> dict[str, str]:
    targets: dict[str, str] = {}
    for raw in split_selected_ids(value, kind=kind):
        if ":" not in raw:
            raise PlanError(f"--{kind} entries must use instance:role syntax; got {raw!r}.")
        instance_id, role = raw.split(":", 1)
        instance_id = safe_id(instance_id, kind="instance")
        role = str(role or "").strip().lower()
        if role not in {"validator", "rpc", "validator-only", "rpc-only"}:
            raise PlanError(
                f"--{kind} target for {instance_id!r} must be validator, rpc, validator-only, or rpc-only; got {role!r}."
            )
        targets[instance_id] = role
    return targets


def planned_service_by_id(plan: NetworkPlan) -> dict[str, PlannedService]:
    return {service.id: service for service in plan.services}


def mutation_kind_for_services(action: str, services: list[PlannedService], role_targets: dict[str, str] | None = None) -> str:
    role_targets = role_targets or {}
    has_validator = any("validator" in service.roles for service in services)
    has_rpc = any("rpc" in service.roles for service in services)
    if action == "promote":
        has_validator = any(role_targets.get(service.id) in {"validator", "validator-only"} for service in services)
        has_rpc = any(role_targets.get(service.id) in {"rpc", "rpc-only"} for service in services)
    elif action == "demote":
        has_validator = any(
            "validator" in service.roles and role_targets.get(service.id) in {"rpc", "rpc-only"}
            for service in services
        )
        has_rpc = any(
            "rpc" in service.roles and role_targets.get(service.id) in {"validator", "validator-only"}
            for service in services
        )
    if has_validator and has_rpc:
        suffix = "validator-rpc"
    elif has_validator:
        suffix = "validator"
    elif has_rpc:
        suffix = "rpc"
    else:
        suffix = "instance"
    if len(services) > 1:
        suffix = f"mixed-{suffix}" if suffix != "instance" else "mixed"
    return f"{action}-{suffix}"


def mutation_requires_consensus_change(action: str, services: list[PlannedService], role_targets: dict[str, str] | None = None) -> bool:
    role_targets = role_targets or {}
    if action in {"add", "retire"}:
        return any("validator" in service.roles for service in services)
    if action == "promote":
        return any(role_targets.get(service.id) in {"validator", "validator-only"} for service in services)
    if action == "demote":
        return any("validator" in service.roles and role_targets.get(service.id) in {"rpc", "rpc-only"} for service in services)
    return False


def mutation_affects_rpc(action: str, services: list[PlannedService], role_targets: dict[str, str] | None = None) -> bool:
    role_targets = role_targets or {}
    if action in {"add", "retire"}:
        return any("rpc" in service.roles and service.rpc_host_port is not None for service in services)
    if action == "promote":
        return any(role_targets.get(service.id) in {"rpc", "rpc-only"} for service in services)
    if action == "demote":
        return any("rpc" in service.roles and role_targets.get(service.id) in {"validator", "validator-only"} for service in services)
    return False


def mutation_rpc_services(plan: NetworkPlan, action: str, services: list[PlannedService], role_targets: dict[str, str] | None = None) -> list[PlannedService]:
    del plan
    role_targets = role_targets or {}
    result: list[PlannedService] = []
    for service in services:
        if action in {"add", "retire"} and "rpc" in service.roles and service.rpc_host_port is not None:
            result.append(service)
        elif action == "promote" and role_targets.get(service.id) in {"rpc", "rpc-only"} and service.rpc_host_port is not None:
            result.append(service)
        elif action == "demote" and "rpc" in service.roles and role_targets.get(service.id) in {"validator", "validator-only"} and service.rpc_host_port is not None:
            result.append(service)
    return result


def mutation_public_rpc_entry_hosts(plan: NetworkPlan, rpc_services: list[PlannedService]) -> list[str]:
    """Return public-entry host ids whose rendered local RPC pool would change.

    This deliberately reads from the public-entry plan instead of assuming an RPC
    backend's host is the entry owner.  Today those hosts usually match because
    the entry sidecar is rendered per Coolify host; keeping the lookup separate
    makes the packet model ready for an explicit entry-owner topology later.
    """

    if not rpc_services or not rpc_public_entry_enabled(plan):
        return []
    rpc_ids = {service.id for service in rpc_services}
    public_entry = rpc_public_entry_plan(plan)
    hosts_payload = public_entry.get("hosts") if isinstance(public_entry, dict) else {}
    affected: list[str] = []
    if isinstance(hosts_payload, dict):
        for host_id, payload in hosts_payload.items():
            if not isinstance(payload, dict):
                continue
            instances = set(str(item) for item in payload.get("rpc_instances") or [])
            if rpc_ids.intersection(instances):
                affected.append(str(host_id))
    return sorted(dict.fromkeys(affected))


def mutation_phases(action: str, *, requires_consensus_change: bool, affects_rpc: bool) -> list[str]:
    phases = ["preflight"]
    if action in {"add", "promote"}:
        if affects_rpc:
            phases.extend(["slurp-current-config", "seed-new-node-bootstrap"])
        phases.append("deploy-service")
        if affects_rpc:
            phases.extend(["wait-direct-rpc", "verify-chain-id", "commit-full-network-topology"])
        if requires_consensus_change:
            phases.extend(["verify-node-identity", "propose-validator-vote", "wait-validator-set", "verify-block-production"])
        if affects_rpc:
            phases.extend(["update-public-rpc-entry", "verify-public-rpc"])
        return phases

    if action in {"retire", "demote"}:
        if affects_rpc:
            phases.extend(["remove-public-rpc-entry", "verify-public-rpc"])
        if requires_consensus_change:
            phases.extend(
                [
                    "verify-current-validator-set",
                    "verify-quorum-after-removal",
                    "propose-validator-vote",
                    "wait-validator-set-removal",
                    "verify-block-production",
                ]
            )
        phases.extend(["stop-coolify-service", "optionally-mark-private-state-retired"])
        return phases

    raise PlanError(f"Unsupported mutation action: {action!r}")


def build_mutation_packet(plan: NetworkPlan, args: argparse.Namespace) -> dict[str, Any]:
    values = mutation_action_values(args)
    if not values:
        raise PlanError("mutate requires one of --add, --retire, --promote, or --demote.")
    if len(values) > 1:
        raise PlanError("mutate accepts exactly one mutation intent per packet.")

    action, raw_value = next(iter(values.items()))
    service_catalog = planned_service_by_id(plan)
    role_targets: dict[str, str] = {}
    if action in {"add", "retire"}:
        instance_ids = list(split_selected_ids(raw_value, kind="instance"))
    else:
        role_targets = parse_mutation_role_targets(raw_value, kind=action)
        instance_ids = list(role_targets)

    missing = [instance_id for instance_id in instance_ids if instance_id not in service_catalog]
    if missing:
        available = ", ".join(sorted(service_catalog)) or "<none>"
        raise PlanError(
            f"Unknown mutate instance selection for {plan.name!r}: {', '.join(missing)}. "
            f"Available instances: {available}"
        )
    if not instance_ids:
        raise PlanError(f"--{action} must select at least one instance.")

    services = [service_catalog[instance_id] for instance_id in instance_ids]
    host_ids = sorted(dict.fromkeys(service.host for service in services))
    hosts = host_by_id(plan)
    coolify_services = [project_service_name(plan, host_id) for host_id in host_ids]
    rpc_services = mutation_rpc_services(plan, action, services, role_targets)
    rpc_backends = [
        rpc_public_entry_backend_url(plan, hosts[service.host], service)
        for service in rpc_services
        if service.host in hosts
    ]
    affected_public_rpc_entry_hosts = mutation_public_rpc_entry_hosts(plan, rpc_services)
    affected_public_rpc_entries = [plan.external_rpc_url] if affected_public_rpc_entry_hosts and plan.external_rpc_url else []
    requires_consensus = mutation_requires_consensus_change(action, services, role_targets)
    affects_rpc = mutation_affects_rpc(action, services, role_targets)
    phases = mutation_phases(action, requires_consensus_change=requires_consensus, affects_rpc=affects_rpc)

    required_ack: list[str] = []
    if requires_consensus:
        required_ack.append("consensus-validator-change")
        if plan.environment == "mainnet" or plan.name == "mainnet":
            required_ack.append("mainnet-consensus-change")

    acknowledged: list[str] = []
    if bool(getattr(args, "ack_consensus_change", False)):
        acknowledged.append("consensus-validator-change")
    if bool(getattr(args, "ack_mainnet_consensus_change", False)):
        acknowledged.append("mainnet-consensus-change")

    observed_state = build_mutation_observed_state(plan, args)

    packet: dict[str, Any] = {
        "ok": bool(observed_state.get("ok", True)) if bool(getattr(args, "observe_chain", False)) else True,
        "mode": "plan",
        "network": plan.name,
        "environment": plan.environment,
        "mutation": mutation_kind_for_services(action, services, role_targets),
        "intent": {
            "action": action,
            "value": raw_value,
            **({"role_targets": role_targets} if role_targets else {}),
        },
        "affected_instances": instance_ids,
        "affected_coolify_hosts": host_ids,
        "affected_coolify_services": coolify_services,
        "affected_rpc_backends": rpc_backends,
        "affected_public_rpc_entries": affected_public_rpc_entries,
        "affected_public_rpc_entry_hosts": affected_public_rpc_entry_hosts,
        "requires_consensus_change": requires_consensus,
        "requires_ack": required_ack,
        "acknowledged": acknowledged,
        "phases": phases,
        "observed_state": observed_state,
        "execution": {
            "implemented": False,
            "no_mutation_performed": True,
            "message": "mutate planner is read-only in this version; mutate --apply is intentionally refused.",
        },
        "warnings": list(plan.warnings),
    }
    if plan.external_rpc_url:
        packet["canonical_rpc_url"] = plan.external_rpc_url
    if rpc_public_entry_enabled(plan):
        packet["rpc_public_entry"] = {
            "enabled": True,
            "hostname": canonical_rpc_hostname(plan),
            "affected_hosts": affected_public_rpc_entry_hosts,
        }
    return packet


def write_mutation_packet_if_requested(packet: dict[str, Any], args: argparse.Namespace) -> None:
    packet_path = str(getattr(args, "packet", "") or "").strip()
    if not packet_path:
        return
    path = Path(packet_path)
    if path.is_dir():
        raise PlanError(f"--packet must be a file path, not a directory: {packet_path!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
    packet["packet_path"] = str(path)


def args_copy_with(args: argparse.Namespace, **overrides: Any) -> argparse.Namespace:
    """Return an argparse-like namespace copy with selected attributes replaced."""

    values = dict(vars(args))
    values.update(overrides)
    return argparse.Namespace(**values)


def plan_with_only_services(plan: NetworkPlan, service_ids: list[str] | tuple[str, ...] | set[str]) -> NetworkPlan:
    """Return a rendering/execution view containing only the requested services.

    Mutate planning intentionally loads the whole private-state catalog so it can
    name future instances.  A mutation executor must not hand that whole catalog
    to a host-scoped Coolify service, otherwise ``mutate --add rpc-2`` could also
    start unrelated future validators declared in private state.  This helper is
    the narrow target-state view used for the host compose pushed by rpc-only
    mutation execution.
    """

    wanted = {safe_id(service_id, kind="service") for service_id in service_ids}
    selected_services = tuple(service for service in plan.services if service.id in wanted)
    found = {service.id for service in selected_services}
    missing = sorted(wanted - found)
    if missing:
        raise PlanError(f"Cannot build mutation execution plan; missing service(s): {', '.join(missing)}")
    if not selected_services:
        raise PlanError("Cannot build mutation execution plan without services.")
    host_ids = {service.host for service in selected_services}
    selected_hosts = tuple(host for host in plan.hosts if host.id in host_ids)
    return replace(plan, hosts=selected_hosts, services=selected_services)


def plan_for_host_without_services(plan: NetworkPlan, host_id: str, retired_service_ids: set[str]) -> NetworkPlan:
    """Return a host-scoped target-state plan with retired services omitted.

    This is the retire-side sibling of ``plan_with_only_services``.  It never
    widens execution beyond the affected Coolify host, and it allows an empty
    service set so a host that only contained the retired RPC node can be
    redeployed with a cleanup-only compose.
    """

    host_id = safe_id(host_id, kind="host")
    retired = {safe_id(service_id, kind="service") for service_id in retired_service_ids}
    hosts = host_by_id(plan)
    if host_id not in hosts:
        raise PlanError(f"Cannot build retire target plan for unknown host: {host_id}")
    selected_services = tuple(
        service
        for service in plan.services
        if service.host == host_id and service.id not in retired
    )
    return replace(plan, hosts=(hosts[host_id],), services=selected_services)


def validator_add_execution_service_ids(
    plan: NetworkPlan,
    target_service: PlannedService,
    discovery: Mapping[str, Any],
) -> list[str]:
    """Return the host-scoped compose service set for a validator-only add.

    ``mutate --add <validator>`` must be narrow: it should not start unrelated
    future RPC declarations merely because they share the target host.  The only
    time this executor preserves all same-host services is when the selected
    live config source is already the target host; in that case replacing the
    host compose with only the new validator would delete/stop services that are
    known to be live and needed for the current topology.
    """

    target_host = target_service.host
    selected_source_host = (
        safe_id(str(discovery.get("selected_source_host") or discovery.get("source_host") or ""), kind="host")
        if str(discovery.get("selected_source_host") or discovery.get("source_host") or "").strip()
        else ""
    )

    if selected_source_host == target_host:
        return [service.id for service in services_for_host(plan, target_host)]

    return [target_service.id]


def services_by_host(services: list[PlannedService] | tuple[PlannedService, ...]) -> dict[str, list[PlannedService]]:
    grouped: dict[str, list[PlannedService]] = {}
    for service in services:
        grouped.setdefault(service.host, []).append(service)
    return grouped


def mutation_apply_is_supported_rpc_add(packet: dict[str, Any], services: list[PlannedService]) -> bool:
    if packet.get("requires_consensus_change"):
        return False
    intent = packet.get("intent") if isinstance(packet.get("intent"), dict) else {}
    if intent.get("action") != "add":
        return False
    return bool(services) and all("rpc" in service.roles and "validator" not in service.roles for service in services)


def mutation_apply_is_supported_rpc_retire(packet: dict[str, Any], services: list[PlannedService]) -> bool:
    if packet.get("requires_consensus_change"):
        return False
    intent = packet.get("intent") if isinstance(packet.get("intent"), dict) else {}
    if intent.get("action") != "retire":
        return False
    return bool(services) and all("rpc" in service.roles and "validator" not in service.roles for service in services)


def mutation_apply_is_supported_validator_add(packet: dict[str, Any], services: list[PlannedService]) -> bool:
    intent = packet.get("intent") if isinstance(packet.get("intent"), dict) else {}
    if intent.get("action") != "add":
        return False
    return bool(services) and all("validator" in service.roles and "rpc" not in service.roles for service in services)


def mutation_apply_is_supported_validator_retire(packet: dict[str, Any], services: list[PlannedService]) -> bool:
    intent = packet.get("intent") if isinstance(packet.get("intent"), dict) else {}
    if intent.get("action") != "retire":
        return False
    return bool(services) and all("validator" in service.roles and "rpc" not in service.roles for service in services)


def mutation_consensus_acknowledged(packet: dict[str, Any]) -> bool:
    required = [str(item) for item in packet.get("requires_ack") or []]
    acknowledged = {str(item) for item in packet.get("acknowledged") or []}
    return all(item in acknowledged for item in required)


def mutate_rpc_wait_args(args: argparse.Namespace, *, rpc_url: str) -> argparse.Namespace:
    timeout_s = float(getattr(args, "rpc_timeout_s", DEFAULT_RPC_WAIT_TIMEOUT_S))
    if timeout_s <= 0:
        timeout_s = DEFAULT_MUTATE_RPC_WAIT_TIMEOUT_S
    return args_copy_with(args, rpc_url=rpc_url, rpc_timeout_s=timeout_s)


def mutate_rpc_retire_public_wait_args(args: argparse.Namespace, *, rpc_url: str) -> argparse.Namespace:
    """Build canonical-RPC wait args for the safe RPC-only retire executor.

    Retiring a dedicated RPC node can leave the canonical entry pointing at a
    single validator node.  In that healthy topology, ``net_peerCount`` may be
    zero even while chain id matches and blocks advance.  Keep explicit operator
    peer thresholds, but make the automatic threshold zero for retire
    verification so the retire executor does not falsely fail after a successful
    routing/compose update.
    """

    wait_args = mutate_rpc_wait_args(args, rpc_url=rpc_url)
    requested_min_peers = int(getattr(args, "rpc_min_peers", DEFAULT_RPC_MIN_PEERS_AUTO))
    if requested_min_peers < 0:
        wait_args = args_copy_with(wait_args, rpc_min_peers=0)
    return wait_args


def default_mutation_observed_state() -> dict[str, Any]:
    return {
        "ok": True,
        "private_state": "loaded",
        "coolify": "not-inspected",
        "direct_rpc": "not-inspected",
        "consensus": "not-inspected",
        "public_rpc": "not-inspected",
    }


def json_rpc_hex_to_int(value: object, *, field: str) -> int:
    text = str(value or "").strip()
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(text)
    except ValueError as exc:
        raise RuntimeError(f"{field} returned non-integer hex value: {text!r}") from exc


def chain_observation_timeout_from_args(args: argparse.Namespace) -> float:
    value = float(getattr(args, "chain_observation_timeout_s", DEFAULT_CHAIN_OBSERVATION_TIMEOUT_S))
    return DEFAULT_CHAIN_OBSERVATION_TIMEOUT_S if value <= 0 else value


def chain_observation_rpc_candidates(plan: NetworkPlan, args: argparse.Namespace) -> list[dict[str, str]]:
    """Return the RPC surfaces that are safe to inspect for current chain state.

    Unlike apply readiness, mutation planning often builds the full private-state
    catalog so it can name future instances.  Direct host-port URLs in that full
    catalog can point at nodes that intentionally do not exist yet.  Chain
    observation therefore prefers the canonical network RPC entry, falling back to
    direct candidates only when no canonical/explicit RPC URL is available.
    """

    explicit = str(getattr(args, "rpc_url", "") or "").strip()
    if explicit:
        return [{"url": explicit, "source": "explicit"}]

    configured = str(getattr(plan, "external_rpc_url", "") or "").strip()
    if configured:
        return [{"url": configured, "source": "configured-network-rpc"}]

    return rpc_probe_url_candidates(plan, args)


def observe_rpc_endpoint(
    plan: NetworkPlan,
    candidate: Mapping[str, str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    url = str(candidate.get("url") or "").strip()
    source = str(candidate.get("source") or "unknown").strip() or "unknown"
    if not url:
        return {
            "ok": False,
            "url": url,
            "source": source,
            "error": "empty RPC URL",
        }

    timeout_s = chain_observation_timeout_from_args(args)
    rpc_user_agent = str(getattr(args, "rpc_user_agent", DEFAULT_JSON_RPC_USER_AGENT) or "").strip()
    expected_chain_id = hex(plan.chain_id)

    try:
        chain_id = str(json_rpc(url, "eth_chainId", timeout_s=timeout_s, user_agent=rpc_user_agent))
        block_hex = str(json_rpc(url, "eth_blockNumber", timeout_s=timeout_s, user_agent=rpc_user_agent))
        peer_hex = str(json_rpc(url, "net_peerCount", timeout_s=timeout_s, user_agent=rpc_user_agent))
        validators_raw = json_rpc(
            url,
            "qbft_getValidatorsByBlockNumber",
            ["latest"],
            timeout_s=timeout_s,
            user_agent=rpc_user_agent,
        )
        block_number = json_rpc_hex_to_int(block_hex, field="eth_blockNumber")
        peer_count = json_rpc_hex_to_int(peer_hex, field="net_peerCount")
        validators = [str(item) for item in validators_raw] if isinstance(validators_raw, list) else []
        chain_id_matches = chain_id.lower() == expected_chain_id.lower()
        payload: dict[str, Any] = {
            "ok": chain_id_matches,
            "url": url,
            "source": source,
            "chain_id": chain_id,
            "expected_chain_id": expected_chain_id,
            "chain_id_matches_plan": chain_id_matches,
            "block_number": block_number,
            "block_number_hex": block_hex,
            "peer_count": peer_count,
            "peer_count_hex": peer_hex,
            "validators": validators,
            "validator_count": len(validators),
        }
        if not chain_id_matches:
            payload["error"] = f"expected chain id {expected_chain_id}, got {chain_id}"
        return payload
    except Exception as exc:  # noqa: BLE001 - operator packet should capture endpoint failure.
        return {
            "ok": False,
            "url": url,
            "source": source,
            "expected_chain_id": expected_chain_id,
            "error": f"{type(exc).__name__}: {exc}",
        }


def rpc_probe_status(probes: list[dict[str, Any]], sources: set[str], *, absent: str = "not-inspected") -> str:
    matching = [probe for probe in probes if str(probe.get("source") or "") in sources]
    if not matching:
        return absent
    return "ok" if any(bool(probe.get("ok")) for probe in matching) else "error"


def observe_chain_state(plan: NetworkPlan, args: argparse.Namespace) -> dict[str, Any]:
    candidates = chain_observation_rpc_candidates(plan, args)
    if not candidates:
        return {
            "ok": False,
            "network": plan.name,
            "environment": plan.environment,
            "expected_chain_id": hex(plan.chain_id),
            "direct_rpc": "not-configured",
            "canonical_rpc": "not-configured",
            "consensus": "not-inspected",
            "probes": [],
            "errors": ["No RPC URL is available for chain observation."],
        }

    probes = [observe_rpc_endpoint(plan, candidate, args) for candidate in candidates]
    successful = [probe for probe in probes if bool(probe.get("ok"))]
    consensus_probe = successful[0] if successful else None
    errors = [str(probe.get("error")) for probe in probes if probe.get("error")]
    direct_status = rpc_probe_status(probes, {"direct-host-port"}, absent="not-inspected")
    canonical_absent = "not-configured" if not str(plan.external_rpc_url or "").strip() else "not-inspected"
    canonical_status = rpc_probe_status(probes, {"configured-network-rpc"}, absent=canonical_absent)
    explicit_status = rpc_probe_status(probes, {"explicit"}, absent="not-inspected")

    chain: dict[str, Any] = {}
    if consensus_probe is not None:
        chain = {
            "source_url": consensus_probe.get("url"),
            "source": consensus_probe.get("source"),
            "chain_id": consensus_probe.get("chain_id"),
            "expected_chain_id": consensus_probe.get("expected_chain_id"),
            "chain_id_matches_plan": consensus_probe.get("chain_id_matches_plan"),
            "block_number": consensus_probe.get("block_number"),
            "block_number_hex": consensus_probe.get("block_number_hex"),
            "peer_count": consensus_probe.get("peer_count"),
            "peer_count_hex": consensus_probe.get("peer_count_hex"),
            "validators": consensus_probe.get("validators", []),
            "validator_count": consensus_probe.get("validator_count", 0),
        }

    return {
        "ok": bool(consensus_probe),
        "network": plan.name,
        "environment": plan.environment,
        "expected_chain_id": hex(plan.chain_id),
        "direct_rpc": direct_status,
        "canonical_rpc": canonical_status,
        "explicit_rpc": explicit_status,
        "consensus": "ok" if consensus_probe is not None else "error",
        "chain": chain,
        "probes": probes,
        **({"errors": errors} if errors else {}),
    }


def mutate_stable_chain_timeout_s(args: argparse.Namespace) -> float:
    """Return a bounded post-mutation stability timeout.

    Mutation apply paths call this after asynchronous Coolify deploy/redeploy
    requests.  ``--rpc-timeout-s 0`` is the existing automatic sentinel; for
    post-mutation stability checks it means "use the mutate timeout", not "probe
    once and return immediately".
    """

    value = float(getattr(args, "rpc_timeout_s", DEFAULT_RPC_WAIT_TIMEOUT_S))
    return DEFAULT_MUTATE_RPC_WAIT_TIMEOUT_S if value <= 0 else value


def wait_for_stable_observable_chain(
    plan: NetworkPlan,
    args: argparse.Namespace,
    *,
    expected_validator_count: int | None = None,
    required_validators_present: tuple[str, ...] = (),
    required_validators_absent: tuple[str, ...] = (),
    require_block_advance: bool = True,
    stable_successes: int = DEFAULT_MUTATE_STABLE_CHAIN_SUCCESSES,
    label: str = "stable-observable-chain",
) -> dict[str, Any]:
    """Wait until the canonical observable chain is healthy for consecutive probes.

    A Coolify deploy API success only means the deploy was accepted.  Traefik,
    Besu, and one-shot sidecars can still be restarting for a short window after
    the API returns.  This helper prevents mutation executors from returning
    success on a single lucky probe while the public RPC route is still flapping.
    """

    timeout_s = mutate_stable_chain_timeout_s(args)
    poll_s = float(getattr(args, "rpc_poll_interval_s", DEFAULT_RPC_POLL_INTERVAL_S) or DEFAULT_RPC_POLL_INTERVAL_S)
    if poll_s <= 0:
        poll_s = DEFAULT_RPC_POLL_INTERVAL_S
    stable_successes = max(1, int(stable_successes))
    deadline = None if timeout_s <= 0 else time.monotonic() + timeout_s
    observe_args = args_copy_with(args, rpc_url="")
    observations: list[dict[str, Any]] = []
    first_acceptable_block: int | None = None
    consecutive = 0
    attempt = 0
    last_errors: list[str] = []
    required_present = {address.lower() for address in required_validators_present}
    required_absent = {address.lower() for address in required_validators_absent}

    operator_log(
        args,
        f"{label} start",
        timeout_s=timeout_s,
        stable_successes=stable_successes,
        expected_validator_count=expected_validator_count,
        require_block_advance=require_block_advance,
    )

    while deadline is None or time.monotonic() < deadline:
        attempt += 1
        observation = observe_chain_state(plan, observe_args)
        observations.append(observation)
        if len(observations) > 8:
            observations = observations[-8:]

        errors: list[str] = []
        chain = observation.get("chain") if isinstance(observation.get("chain"), dict) else {}
        if not observation.get("ok", False):
            errors.append(str(observation.get("errors") or observation.get("error") or "chain observation failed"))

        block_number: int | None = None
        validators: list[str] = []
        if isinstance(chain, dict):
            try:
                block_number = int(chain.get("block_number"))
            except (TypeError, ValueError):
                block_number = None
            validators = [str(item) for item in chain.get("validators") or []]

        if block_number is None:
            errors.append("block number was not available")
        elif block_number < 1:
            errors.append(f"block_number {block_number} < 1")

        validator_set = {address.lower() for address in validators}
        if expected_validator_count is not None and len(validators) != expected_validator_count:
            errors.append(f"validator_count {len(validators)} != expected {expected_validator_count}")
        missing_validators = sorted(required_present - validator_set)
        if missing_validators:
            errors.append(f"required validator(s) missing: {', '.join(missing_validators)}")
        still_present = sorted(required_absent & validator_set)
        if still_present:
            errors.append(f"retired validator(s) still present: {', '.join(still_present)}")

        block_advanced = False
        if block_number is not None:
            if first_acceptable_block is None and not errors:
                first_acceptable_block = block_number
            if first_acceptable_block is not None:
                block_advanced = block_number > first_acceptable_block
            if require_block_advance and not block_advanced:
                errors.append(
                    f"block has not advanced beyond first acceptable block {first_acceptable_block}"
                )

        if errors:
            consecutive = 0
            last_errors = errors
            operator_log(
                args,
                f"{label} retry",
                attempt=attempt,
                consecutive=consecutive,
                block_number=block_number,
                error="; ".join(errors),
            )
        else:
            consecutive += 1
            last_errors = []
            operator_log(
                args,
                f"{label} probe",
                attempt=attempt,
                consecutive=consecutive,
                block_number=block_number,
                validator_count=len(validators),
                block_advanced=block_advanced,
            )
            if consecutive >= stable_successes:
                operator_log(
                    args,
                    f"{label} done",
                    attempt=attempt,
                    block_number=block_number,
                    validator_count=len(validators),
                    stable_successes=consecutive,
                )
                return {
                    "ok": True,
                    "label": label,
                    "attempts": attempt,
                    "stable_successes": consecutive,
                    "required_stable_successes": stable_successes,
                    "first_acceptable_block": first_acceptable_block,
                    "block_advanced": block_advanced,
                    "chain": chain,
                    "observation": observation,
                    "observations": observations,
                }

        time.sleep(poll_s)

    return {
        "ok": False,
        "label": label,
        "attempts": attempt,
        "stable_successes": consecutive,
        "required_stable_successes": stable_successes,
        "first_acceptable_block": first_acceptable_block,
        "error": "; ".join(last_errors) if last_errors else "stable observable chain state was not reached before timeout",
        "observations": observations,
    }


def wait_for_qbft_peer_count(
    plan: NetworkPlan,
    args: argparse.Namespace,
    *,
    min_peer_count: int,
    label: str = "wait-qbft-peer-count",
    stable_successes: int = DEFAULT_MUTATE_STABLE_CHAIN_SUCCESSES,
    require_block_advance: bool = True,
) -> dict[str, Any]:
    """Wait until the observable QBFT RPC reports stable peers and block progress.

    A single peer-count flicker is not safe enough for a validator-set mutation:
    the failed cross-host add showed peer_count=1 once, then the add vote wedged
    the chain at the validator-set transition.  Require consecutive acceptable
    probes and, by default, a block advance while the peer is present.
    """

    min_peer_count = max(0, int(min_peer_count))
    stable_successes = max(1, int(stable_successes))
    timeout_s = max(chain_observation_timeout_from_args(args), DEFAULT_MUTATE_RPC_WAIT_TIMEOUT_S)
    poll_s = float(getattr(args, "rpc_poll_interval_s", DEFAULT_RPC_POLL_INTERVAL_S) or DEFAULT_RPC_POLL_INTERVAL_S)
    if poll_s <= 0:
        poll_s = DEFAULT_RPC_POLL_INTERVAL_S
    deadline = time.monotonic() + timeout_s
    observe_args = args_copy_with(args, rpc_url="")
    observations: list[dict[str, Any]] = []
    attempt = 0
    consecutive = 0
    first_acceptable_block: int | None = None
    last_error = ""

    operator_log(
        args,
        f"{label} start",
        min_peer_count=min_peer_count,
        timeout_s=timeout_s,
        stable_successes=stable_successes,
        require_block_advance=require_block_advance,
    )
    while time.monotonic() <= deadline:
        attempt += 1
        observation = observe_chain_state(plan, observe_args)
        observations.append(observation)
        if len(observations) > 8:
            observations = observations[-8:]

        chain = observation.get("chain") if isinstance(observation.get("chain"), dict) else {}
        peer_count: int | None = None
        block_number: int | None = None
        if isinstance(chain, dict):
            try:
                peer_count = int(chain.get("peer_count"))
            except (TypeError, ValueError):
                peer_count = None
            try:
                block_number = int(chain.get("block_number"))
            except (TypeError, ValueError):
                block_number = None

        errors: list[str] = []
        if not observation.get("ok"):
            errors.append(str(observation.get("errors") or observation.get("error") or "chain observation failed"))
        if peer_count is None:
            errors.append("peer_count was not available")
        elif peer_count < min_peer_count:
            errors.append(f"peer_count {peer_count} < required {min_peer_count}")
        if block_number is None:
            errors.append("block_number was not available")

        block_advanced = False
        if not errors and block_number is not None:
            if first_acceptable_block is None:
                first_acceptable_block = block_number
            block_advanced = block_number > first_acceptable_block
            if require_block_advance and not block_advanced:
                errors.append(f"block has not advanced beyond first acceptable peer block {first_acceptable_block}")

        if errors:
            consecutive = 0
            last_error = "; ".join(errors)
            operator_log(args, f"{label} retry", attempt=attempt, peer_count=peer_count, block_number=block_number, error=last_error)
        else:
            consecutive += 1
            last_error = ""
            operator_log(args, f"{label} probe", attempt=attempt, consecutive=consecutive, peer_count=peer_count, block_number=block_number, block_advanced=block_advanced)
            if consecutive >= stable_successes:
                operator_log(args, f"{label} done", attempt=attempt, peer_count=peer_count, block_number=block_number)
                return {
                    "ok": True,
                    "label": label,
                    "attempts": attempt,
                    "stable_successes": consecutive,
                    "required_stable_successes": stable_successes,
                    "min_peer_count": min_peer_count,
                    "peer_count": peer_count,
                    "first_acceptable_block": first_acceptable_block,
                    "block_advanced": block_advanced,
                    "chain": chain,
                    "observation": observation,
                    "observations": observations,
                }

        time.sleep(poll_s)

    return {
        "ok": False,
        "label": label,
        "attempts": attempt,
        "stable_successes": consecutive,
        "required_stable_successes": stable_successes,
        "min_peer_count": min_peer_count,
        "first_acceptable_block": first_acceptable_block,
        "error": last_error or f"stable peer_count did not reach {min_peer_count} before timeout",
        "observations": observations,
    }

def build_mutation_observed_state(plan: NetworkPlan, args: argparse.Namespace) -> dict[str, Any]:
    if not bool(getattr(args, "observe_chain", False)):
        return default_mutation_observed_state()

    observation = observe_chain_state(plan, args)
    return {
        "ok": bool(observation.get("ok")),
        "private_state": "loaded",
        "coolify": "not-inspected",
        "direct_rpc": observation.get("direct_rpc", "not-inspected"),
        "consensus": observation.get("consensus", "not-inspected"),
        "public_rpc": observation.get("canonical_rpc", "not-inspected"),
        "canonical_rpc": observation.get("canonical_rpc", "not-inspected"),
        "explicit_rpc": observation.get("explicit_rpc", "not-inspected"),
        "chain": observation.get("chain", {}),
        "rpc_probes": observation.get("probes", []),
        **({"errors": observation.get("errors", [])} if observation.get("errors") else {}),
    }


def candidate_qbft_config_source_hosts(plan: NetworkPlan, target_services: list[PlannedService], args: argparse.Namespace) -> list[str]:
    forced = str(getattr(args, "config_root_host", "") or "").strip()
    if forced:
        return [safe_id(forced, kind="host")]
    target_ids = {service.id for service in target_services}
    target_hosts = {service.host for service in target_services}

    # Prefer hosts that are not being mutated.  A newly introduced host can have
    # planned services in private state before it has any live QBFT runtime
    # material, so it should not be treated as the *first* config source merely
    # because it appears in the future topology.
    #
    # Still return the remaining known hosts as fallbacks.  A mutation can target
    # a host that already has a live service, for example adding validator-1 on
    # host A while validator-rpc-1 on the same host is the only reliable runtime
    # config source.  Returning only non-target hosts makes a transient/export
    # failure on another host fatal even though a usable same-host source exists.
    preferred: list[str] = []
    fallback: list[str] = []
    for service in plan.services:
        if service.id in target_ids:
            continue
        if service.host not in fallback:
            fallback.append(service.host)
        if service.host not in target_hosts and service.host not in preferred:
            preferred.append(service.host)

    ordered: list[str] = []
    for host_id in [*preferred, *fallback]:
        if host_id not in ordered:
            ordered.append(host_id)
    return ordered


def qbft_config_export_source_service_lookup(plan: NetworkPlan, args: argparse.Namespace, host_id: str) -> dict[str, Any]:
    """Resolve the source host's normal QBFT Coolify service UUID, if possible."""

    host_id = safe_id(host_id, kind="host")
    service_name = project_service_name(plan, host_id)
    host = host_by_id(plan).get(host_id)
    configured_uuid = str(host.service_uuid if host else "").strip()
    context: dict[str, Any] = {
        "ok": bool(configured_uuid),
        "service_name": service_name,
        "configured_service_uuid": configured_uuid,
        "service_uuid": configured_uuid,
        "source": "private-state" if configured_uuid else "unresolved",
    }
    if bool(getattr(args, "dry_run", False)) or bool(getattr(args, "no_deploy", False)):
        context["skipped_live_lookup"] = True
        return context

    tried: list[dict[str, Any]] = []
    try:
        lookup_args = args_copy_with(args, host=host_id, _coolify_host_id=host_id)
        client, token, token_source = coolify_client_from_args(lookup_args, plan, host_id=host_id)
        service_uuid, lookup_context = coolify_find_service_by_name(
            client=client,
            args=lookup_args,
            service_name=service_name,
            tried=tried,
        )
        context.update(
            {
                "ok": bool(service_uuid or configured_uuid),
                "service_uuid": service_uuid or configured_uuid,
                "source": "coolify-service-name" if service_uuid else context["source"],
                "lookup": lookup_context,
                "tried": tried,
                "token_source": token_source,
            }
        )
        return context
    except Exception as exc:  # noqa: BLE001 - non-fatal; unprefixed/bind candidates can still be tried.
        context.update(
            {
                "ok": bool(configured_uuid),
                "source": context["source"] if configured_uuid else "lookup-error",
                "error": f"{type(exc).__name__}: {exc}",
                "tried": tried,
            }
        )
        return context


def qbft_config_export_volume_prefixes_from_lookup(lookup: Mapping[str, Any]) -> list[str]:
    prefixes: list[str] = []
    for raw in [lookup.get("service_uuid"), lookup.get("configured_service_uuid")]:
        prefix = str(raw or "").strip()
        if not prefix or prefix in prefixes:
            continue
        if SAFE_ID_RE.match(prefix):
            prefixes.append(prefix)
    return prefixes


def export_qbft_config_from_host_via_direct_port(plan: NetworkPlan, args: argparse.Namespace, host_id: str) -> dict[str, Any]:
    hosts = host_by_id(plan)
    if host_id not in hosts:
        raise PlanError(f"Unknown config source host: {host_id}")
    port = qbft_config_export_port(args)
    token = qbft_config_export_token()
    service_name = qbft_config_export_service_name(plan, host_id)
    source_service_lookup = qbft_config_export_source_service_lookup(plan, args, host_id)
    volume_prefixes = qbft_config_export_volume_prefixes_from_lookup(source_service_lookup)
    compose = render_qbft_config_exporter_compose(
        plan,
        host_id,
        token=token,
        port=port,
        volume_prefixes=volume_prefixes,
    )
    export_args = args_copy_with(
        args,
        host=host_id,
        coolify_service_name=service_name,
        _compose_override=compose,
    )
    sync_result = coolify_sync(plan, export_args, deploy=not bool(getattr(args, "no_deploy", False)))
    source_url = qbft_config_export_url(hosts[host_id], port=port, token=token)
    if bool(getattr(args, "dry_run", False)) or bool(getattr(args, "no_deploy", False)):
        return {
            "ok": True,
            "dry_run": bool(getattr(args, "dry_run", False)),
            "source_host": host_id,
            "source_url": source_url,
            "service_name": service_name,
            "source_service_lookup": source_service_lookup,
            "volume_prefixes": volume_prefixes,
            "sync": sync_result,
            "message": "QBFT config export planned; live slurp skipped.",
        }
    if not sync_result.get("ok"):
        return {
            "ok": False,
            "source_host": host_id,
            "source_url": source_url,
            "service_name": service_name,
            "source_service_lookup": source_service_lookup,
            "volume_prefixes": volume_prefixes,
            "sync": sync_result,
            "missing_files": ["export-service-deploy-failed"],
        }
    # Give Coolify/Docker enough time to pull/start the tiny HTTP exporter before fetching.
    # This is intentionally separate from the Coolify API timeout; service deployment is queued/asynchronous.
    deadline = time.time() + qbft_config_export_timeout_s(args)
    last_error = ""
    while time.time() < deadline:
        try:
            payload = fetch_json_url(source_url, timeout_s=min(10.0, max(2.0, qbft_config_export_timeout_s(args) / 12.0)))
            if not isinstance(payload, Mapping):
                raise RuntimeError(f"exporter returned non-object payload: {type(payload).__name__}")
            result = normalize_qbft_config_bundle(payload, source_host=host_id, source_url=source_url)
            result["service_name"] = service_name
            result["source_service_lookup"] = source_service_lookup
            result["volume_prefixes"] = volume_prefixes
            result["sync"] = sync_result
            return result
        except Exception as exc:  # noqa: BLE001 - return diagnostic for operator packet.
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(2.0)
    return {
        "ok": False,
        "source_host": host_id,
        "source_url": source_url,
        "service_name": service_name,
        "source_service_lookup": source_service_lookup,
        "volume_prefixes": volume_prefixes,
        "sync": sync_result,
        "missing_files": ["export-fetch-timeout"],
        "error": last_error,
    }


def export_qbft_config_from_host_via_public_entry(plan: NetworkPlan, args: argparse.Namespace, host_id: str) -> dict[str, Any]:
    """Slurp QBFT config through a Coolify-managed temporary public-entry tool.

    This is Coolify-native and does not use SSH.  It creates/updates a small
    source-host Coolify service that:
      * mounts candidate QBFT runtime volumes / bind roots,
      * serves a tokenized non-secret config bundle on an internal export port,
      * writes a separate Traefik dynamic config file for a temporary HTTP/HTTPS path.

    The operator fetches that path through the host's normal public entrypoint.
    HTTPS-to-source-IP with the canonical Host header is tried first so the
    workflow does not require plain HTTP to be open on the machine.  A cleanup
    compose removes the temporary dynamic config after the bundle is fetched.
    """

    hosts = host_by_id(plan)
    if host_id not in hosts:
        raise PlanError(f"Unknown config source host: {host_id}")
    if not rpc_public_entry_enabled(plan):
        return {
            "ok": False,
            "source_host": host_id,
            "transport": "public-entry",
            "missing_files": ["rpc-public-entry-not-enabled"],
            "error": "Public-entry config slurp requires networks.<network>.rpc.",
        }

    port = qbft_config_export_port(args)
    token = qbft_config_export_token()
    host = hosts[host_id]
    hostname = canonical_rpc_hostname(plan)
    fetch_candidates = qbft_config_export_public_entry_fetch_candidates(plan, host, token=token)
    public_url = fetch_candidates[0]["url"] if fetch_candidates else qbft_config_export_public_entry_url(host, token=token)
    service_name = qbft_config_export_service_name(plan, host_id)
    source_service_lookup = qbft_config_export_source_service_lookup(plan, args, host_id)
    volume_prefixes = qbft_config_export_volume_prefixes_from_lookup(source_service_lookup)
    compose = render_qbft_config_exporter_compose(
        plan,
        host_id,
        token=token,
        port=port,
        public_entry=True,
        volume_prefixes=volume_prefixes,
    )
    cleanup_compose = render_qbft_config_export_cleanup_compose(plan, host_id)

    export_args = args_copy_with(
        args,
        host=host_id,
        coolify_service_name=service_name,
        _compose_override=compose,
    )
    cleanup_args = args_copy_with(
        args,
        host=host_id,
        coolify_service_name=service_name,
        _compose_override=cleanup_compose,
    )

    sync_result = coolify_sync(plan, export_args, deploy=not bool(getattr(args, "no_deploy", False)))
    if bool(getattr(args, "dry_run", False)) or bool(getattr(args, "no_deploy", False)):
        return {
            "ok": True,
            "dry_run": bool(getattr(args, "dry_run", False)),
            "source_host": host_id,
            "source_url": public_url,
            "fetch_candidates": [
                {key: value for key, value in candidate.items() if key != "headers"}
                for candidate in fetch_candidates
            ],
            "host_header": hostname,
            "transport": "public-entry",
            "service_name": service_name,
            "source_service_lookup": source_service_lookup,
            "volume_prefixes": volume_prefixes,
            "sync": sync_result,
            "message": "QBFT config public-entry export planned; live slurp skipped.",
        }
    if not sync_result.get("ok"):
        return {
            "ok": False,
            "source_host": host_id,
            "source_url": public_url,
            "host_header": hostname,
            "transport": "public-entry",
            "service_name": service_name,
            "source_service_lookup": source_service_lookup,
            "volume_prefixes": volume_prefixes,
            "sync": sync_result,
            "missing_files": ["public-entry-export-deploy-failed"],
        }

    deadline = time.time() + qbft_config_export_timeout_s(args)
    last_error = ""
    attempts: list[dict[str, Any]] = []
    result: dict[str, Any] | None = None
    while time.time() < deadline:
        for candidate in fetch_candidates:
            candidate_url = str(candidate.get("url") or "")
            try:
                payload = fetch_json_url(
                    candidate_url,
                    timeout_s=min(10.0, max(2.0, qbft_config_export_timeout_s(args) / 12.0)),
                    headers=candidate.get("headers") if isinstance(candidate.get("headers"), Mapping) else None,
                    insecure_https=bool(candidate.get("insecure_https")),
                )
                if not isinstance(payload, Mapping):
                    raise RuntimeError(f"exporter returned non-object payload: {type(payload).__name__}")
                result = normalize_qbft_config_bundle(payload, source_host=host_id, source_url=candidate_url)
                result["host_header"] = hostname
                result["transport"] = "public-entry"
                result["service_name"] = service_name
                result["source_service_lookup"] = source_service_lookup
                result["volume_prefixes"] = volume_prefixes
                result["sync"] = sync_result
                result["fetch_candidate"] = {key: value for key, value in candidate.items() if key != "headers"}
                break
            except Exception as exc:  # noqa: BLE001 - return diagnostic for operator packet.
                last_error = f"{type(exc).__name__}: {exc}"
                attempts.append(
                    {
                        "label": candidate.get("label"),
                        "url": candidate_url,
                        "insecure_https": bool(candidate.get("insecure_https")),
                        "error": last_error,
                    }
                )
        if result is not None:
            break
        time.sleep(2.0)

    if result is not None:
        result["fetch_attempts"] = attempts[-8:]
        if result.get("ok"):
            result["cleanup"] = coolify_sync(plan, cleanup_args, deploy=True)
        else:
            result["cleanup_skipped"] = {
                "reason": "config-export-left-running-until-bundle-ok",
                "message": "Temporary QBFT config-export was left deployed because the fetched bundle was not valid.",
                "service_name": service_name,
            }
        return result
    return {
        "ok": False,
        "source_host": host_id,
        "source_url": public_url,
        "host_header": hostname,
        "transport": "public-entry",
        "service_name": service_name,
        "source_service_lookup": source_service_lookup,
        "volume_prefixes": volume_prefixes,
        "sync": sync_result,
        "cleanup_skipped": {
            "reason": "config-export-left-running-after-fetch-timeout",
            "message": "Temporary QBFT config-export was left deployed for host-side inspection.",
            "service_name": service_name,
        },
        "fetch_candidates": [
            {key: value for key, value in candidate.items() if key != "headers"}
            for candidate in fetch_candidates
        ],
        "fetch_attempts": attempts[-12:],
        "missing_files": ["public-entry-export-fetch-timeout"],
        "error": last_error,
    }


def export_qbft_config_from_host(plan: NetworkPlan, args: argparse.Namespace, host_id: str) -> dict[str, Any]:
    transport = qbft_config_export_transport(args)
    if transport == "direct-port":
        result = export_qbft_config_from_host_via_direct_port(plan, args, host_id)
        result["transport"] = "direct-port"
        return result
    result = export_qbft_config_from_host_via_public_entry(plan, args, host_id)
    result["transport"] = "public-entry"
    return result


def discover_qbft_config_bundle(
    plan: NetworkPlan,
    args: argparse.Namespace,
    target_services: list[PlannedService],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    source_hosts = candidate_qbft_config_source_hosts(plan, target_services, args)
    if not source_hosts:
        raise PlanError("No existing QBFT config source host is available for this mutation.")
    exports: list[dict[str, Any]] = []
    for host_id in source_hosts:
        exports.append(export_qbft_config_from_host(plan, args, host_id))
    if bool(getattr(args, "dry_run", False)) or bool(getattr(args, "no_deploy", False)):
        placeholder_bundle = {
            "ok": True,
            "source_host": source_hosts[0],
            "lineage_hash": "dry-run",
            "files": {
                "genesis_json": "{\"dry_run\":true}\n",
                "static_nodes_all_json": "[]\n",
                "network_metadata_json": "{\"dry_run\":true}\n",
            },
            "sha256": {},
        }
        return placeholder_bundle, {
            "ok": True,
            "dry_run": bool(getattr(args, "dry_run", False)),
            "candidate_hosts": source_hosts,
            "exports": exports,
            "message": "QBFT config source discovery planned; live bundle not fetched.",
        }
    selected = select_qbft_config_source(exports, forced_host=str(getattr(args, "config_root_host", "") or ""))
    return selected, {
        "ok": True,
        "candidate_hosts": source_hosts,
        "selected_source_host": selected.get("source_host"),
        "selected_lineage_hash": selected.get("lineage_hash"),
        "exports": [
            {
                key: value
                for key, value in item.items()
                if key not in {"files", "sync"}
            }
            for item in exports
        ],
    }



def operator_reachable_rpc_services(plan: NetworkPlan) -> list[PlannedService]:
    """Return services whose RPC host port should be reachable from the operator machine."""

    hosts = host_by_id(plan)
    services: list[PlannedService] = []
    for service in plan.services:
        if service.rpc_host_port is None or not service.rpc_url_on_host:
            continue
        if is_local_coolify_plan(plan):
            services.append(service)
            continue
        host = hosts.get(service.host)
        if service.rpc_bind_host == "0.0.0.0" and host is not None and host.address:
            services.append(service)
    return services


def qbft_consensus_rpc_candidates(plan: NetworkPlan, args: argparse.Namespace) -> list[dict[str, str]]:
    """Return RPC endpoints suitable for QBFT consensus JSON-RPC calls.

    Consensus votes should be sent to a validator RPC when the topology exposes
    one.  The public RPC hostname is still a good read surface, but it can be a
    Traefik/public-entry route that is being updated during mutations.  For
    apply-time QBFT work, prefer direct validator-RPC host ports and keep the
    configured public RPC only as an automatic fallback.  An explicit
    ``--rpc-url`` remains an operator override and is tried by itself.
    """

    explicit = str(getattr(args, "rpc_url", "") or "").strip()
    if explicit:
        return [{"url": explicit, "source": "explicit"}]

    candidates: list[dict[str, str]] = []
    reachable_services = operator_reachable_rpc_services(plan)

    for service in reachable_services:
        if "validator" in service.roles and "rpc" in service.roles:
            candidates.append(
                {
                    "url": service.rpc_url_on_host,
                    "source": "direct-validator-rpc",
                    "service": service.id,
                    "host": service.host,
                }
            )

    for service in reachable_services:
        if "validator" in service.roles and "rpc" not in service.roles:
            candidates.append(
                {
                    "url": service.rpc_url_on_host,
                    "source": "direct-validator",
                    "service": service.id,
                    "host": service.host,
                }
            )

    for service in reachable_services:
        if "rpc" in service.roles and "validator" not in service.roles:
            candidates.append(
                {
                    "url": service.rpc_url_on_host,
                    "source": "direct-rpc",
                    "service": service.id,
                    "host": service.host,
                }
            )

    configured = str(getattr(plan, "external_rpc_url", "") or "").strip()
    if configured:
        candidates.append({"url": configured, "source": "configured-network-rpc"})

    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for candidate in candidates:
        url = str(candidate.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append({key: str(value) for key, value in candidate.items() if str(value or "").strip()})
    if not unique:
        raise PlanError("QBFT validator voting requires --rpc-url, networks.<network>.rpc, or an operator-reachable validator RPC port.")
    return unique



def qbft_validator_vote_rpc_candidates(
    plan: NetworkPlan,
    args: argparse.Namespace,
    *,
    exclude_services: set[str] | list[str] | tuple[str, ...] = (),
) -> list[dict[str, str]]:
    """Return RPC endpoints eligible to publish QBFT validator votes.

    Read-only QBFT methods can safely fall back to public RPC or RPC-only nodes.
    Validator-set proposals cannot: a vote only counts when it is proposed by an
    active validator node.  In automatic mode, keep the list to operator-reachable
    validator services and never fall back to rpc-only services or the public
    Traefik route.  ``--rpc-url`` remains an explicit operator override.
    """

    explicit = str(getattr(args, "rpc_url", "") or "").strip()
    if explicit:
        return [{"url": explicit, "source": "explicit"}]

    excluded = {safe_id(item, kind="service") for item in exclude_services if str(item or "").strip()}
    candidates: list[dict[str, str]] = []
    for service in operator_reachable_rpc_services(plan):
        if service.id in excluded:
            continue
        if "validator" not in service.roles:
            continue
        candidates.append(
            {
                "url": service.rpc_url_on_host,
                "source": "direct-validator-rpc" if "rpc" in service.roles else "direct-validator",
                "service": service.id,
                "host": service.host,
            }
        )

    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for candidate in candidates:
        url = str(candidate.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append({key: str(value) for key, value in candidate.items() if str(value or "").strip()})
    if not unique:
        raise PlanError(
            "QBFT validator voting requires an operator-reachable validator RPC endpoint. "
            "Automatic voting will not use rpc-only nodes or public RPC because those proposals do not represent validator votes."
        )
    return unique


def qbft_vote_rpc_url(plan: NetworkPlan, args: argparse.Namespace) -> str:
    """Return the first automatic QBFT RPC candidate.

    Kept for callers/tests that need the selected URL only; consensus reads and
    votes should use qbft_json_rpc_candidates so they can fall back cleanly.
    """

    return qbft_consensus_rpc_candidates(plan, args)[0]["url"]


def qbft_rpc_timeout_s(args: argparse.Namespace) -> float:
    """Return a positive per-call timeout for QBFT consensus JSON-RPC operations.

    ``--rpc-timeout-s`` uses ``0`` as an automatic sentinel for the broader
    wait-rpc flow.  Passing that sentinel directly to ``urllib`` means a
    non-blocking socket timeout on Windows, which can fail immediately with
    ``WinError 10035`` before a healthy RPC endpoint has a chance to answer.
    Consensus reads/votes are one-shot JSON-RPC calls, so they must always use a
    real positive timeout unless the operator provided a stricter explicit one.
    """

    value = float(getattr(args, "rpc_timeout_s", DEFAULT_RPC_WAIT_TIMEOUT_S))
    return DEFAULT_QBFT_JSON_RPC_TIMEOUT_S if value <= 0 else value


def qbft_json_rpc_candidates(
    plan: NetworkPlan,
    args: argparse.Namespace,
    method: str,
    params: list[Any] | None = None,
    *,
    candidates: list[dict[str, str]] | tuple[dict[str, str], ...] | None = None,
) -> dict[str, Any]:
    rpc_candidates = list(candidates) if candidates is not None else qbft_consensus_rpc_candidates(plan, args)
    timeout_s = qbft_rpc_timeout_s(args)
    user_agent = str(getattr(args, "rpc_user_agent", DEFAULT_JSON_RPC_USER_AGENT) or DEFAULT_JSON_RPC_USER_AGENT)
    attempts: list[dict[str, str]] = []
    for candidate in rpc_candidates:
        url = str(candidate.get("url") or "").strip()
        source = str(candidate.get("source") or "unknown").strip() or "unknown"
        try:
            result = json_rpc(
                url,
                method,
                params,
                timeout_s=timeout_s,
                user_agent=user_agent,
            )
            return {
                "ok": True,
                "result": result,
                "rpc_url": url,
                "source": source,
                "candidate": candidate,
                "attempts": attempts + [{"ok": True, **candidate}],
            }
        except Exception as exc:  # noqa: BLE001 - preserve fallback diagnostics for operator output.
            attempts.append(
                {
                    "ok": False,
                    **candidate,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    summary = "; ".join(f"{item.get('source')} {item.get('url')}: {item.get('error')}" for item in attempts)
    return {
        "ok": False,
        "error": f"JSON-RPC {method} failed for all QBFT RPC candidates: {summary}",
        "attempts": attempts,
    }


def current_qbft_validators(plan: NetworkPlan, args: argparse.Namespace, *, block: str = "latest") -> list[str]:
    response = qbft_json_rpc_candidates(plan, args, "qbft_getValidatorsByBlockNumber", [block])
    if not response.get("ok"):
        raise RuntimeError(str(response.get("error") or "qbft_getValidatorsByBlockNumber failed"))
    validators = response.get("result")
    if not isinstance(validators, list):
        raise RuntimeError(f"qbft_getValidatorsByBlockNumber returned {type(validators).__name__}, expected list")
    return [normalize_validator_address(item, field="qbft validator") for item in validators]


def propose_qbft_validator_vote(
    plan: NetworkPlan,
    args: argparse.Namespace,
    validator_address: str,
    proposal: bool,
    *,
    exclude_services: set[str] | list[str] | tuple[str, ...] = (),
    candidates: list[dict[str, str]] | tuple[dict[str, str], ...] | None = None,
) -> dict[str, Any]:
    address = normalize_validator_address(validator_address)
    vote_candidates = list(candidates) if candidates is not None else qbft_validator_vote_rpc_candidates(
        plan,
        args,
        exclude_services=exclude_services,
    )
    response = qbft_json_rpc_candidates(
        plan,
        args,
        "qbft_proposeValidatorVote",
        [address, bool(proposal)],
        candidates=vote_candidates,
    )
    if not response.get("ok"):
        return {
            "ok": False,
            "validator_address": address,
            "proposal": bool(proposal),
            "error": str(response.get("error") or "qbft_proposeValidatorVote failed"),
            "attempts": response.get("attempts") or [],
        }
    result = response.get("result")
    return {
        "ok": bool(result is True),
        "rpc_url": response.get("rpc_url"),
        "source": response.get("source"),
        "candidate": response.get("candidate"),
        "attempts": response.get("attempts") or [],
        "validator_address": address,
        "proposal": bool(proposal),
        "result": result,
    }


def validator_mutation_membership_wait_args(args: argparse.Namespace) -> argparse.Namespace:
    """Use mutation-scale membership waits for add/retire consensus changes."""

    timeout_s = max(chain_observation_timeout_from_args(args), DEFAULT_MUTATE_RPC_WAIT_TIMEOUT_S)
    return args_copy_with(args, chain_observation_timeout_s=timeout_s)


def qbft_vote_retry_timeout_s(args: argparse.Namespace) -> float:
    """Return the total retry window for apply-time QBFT validator votes."""

    return max(mutate_stable_chain_timeout_s(args), chain_observation_timeout_from_args(args))


def propose_qbft_validator_vote_until_ok(
    plan: NetworkPlan,
    args: argparse.Namespace,
    validator_address: str,
    proposal: bool,
    *,
    exclude_services: set[str] | list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Retry QBFT validator votes while Coolify/Besu RPC surfaces are settling.

    Starting or changing a Coolify compose can briefly interrupt the direct
    validator RPC port even when the node comes back healthy a few moments later.
    A single immediate ``qbft_proposeValidatorVote`` attempt makes add-validator
    brittle, so keep retrying until the vote is accepted or the validator set is
    already in the requested state.
    """

    address = normalize_validator_address(validator_address)
    timeout_s = qbft_vote_retry_timeout_s(args)
    poll_s = float(getattr(args, "rpc_poll_interval_s", DEFAULT_RPC_POLL_INTERVAL_S) or DEFAULT_RPC_POLL_INTERVAL_S)
    if poll_s <= 0:
        poll_s = DEFAULT_RPC_POLL_INTERVAL_S
    deadline = time.monotonic() + timeout_s
    attempt = 0
    attempts: list[dict[str, Any]] = []
    last_error = ""

    try:
        candidates = qbft_validator_vote_rpc_candidates(plan, args, exclude_services=exclude_services)
    except Exception as exc:  # noqa: BLE001 - return structured operator diagnostics.
        return {
            "ok": False,
            "validator_address": address,
            "proposal": bool(proposal),
            "error": f"{type(exc).__name__}: {exc}",
            "attempts": [],
            "vote_retry_timeout_s": timeout_s,
        }

    operator_log(
        args,
        "qbft vote retry start",
        validator_address=address,
        proposal=bool(proposal),
        timeout_s=timeout_s,
        candidates=",".join(str(candidate.get("url") or "") for candidate in candidates),
    )

    while time.monotonic() <= deadline:
        attempt += 1

        try:
            validators = current_qbft_validators(plan, args)
            present = address in validators
            if present == bool(proposal):
                return {
                    "ok": True,
                    "validator_address": address,
                    "proposal": bool(proposal),
                    "skipped": True,
                    "reason": "validator-set-already-at-requested-state",
                    "validators": validators,
                    "validator_count": len(validators),
                    "attempts": attempts,
                    "vote_retry_attempts": attempt,
                    "vote_retry_timeout_s": timeout_s,
                }
        except Exception as exc:  # noqa: BLE001 - readiness may flap during redeploy.
            last_error = f"{type(exc).__name__}: {exc}"
            attempts.append({"attempt": attempt, "stage": "read-validator-set", "ok": False, "error": last_error})

        vote_result = propose_qbft_validator_vote(
            plan,
            args,
            address,
            bool(proposal),
            exclude_services=exclude_services,
            candidates=candidates,
        )
        attempts.append({"attempt": attempt, "stage": "propose-validator-vote", **vote_result})
        if vote_result.get("ok"):
            return {
                **vote_result,
                "attempts": attempts,
                "vote_retry_attempts": attempt,
                "vote_retry_timeout_s": timeout_s,
            }

        last_error = str(vote_result.get("error") or "qbft_proposeValidatorVote did not return ok")
        operator_log(
            args,
            "qbft vote retry",
            attempt=attempt,
            validator_address=address,
            proposal=bool(proposal),
            error=last_error,
        )
        time.sleep(poll_s)

    return {
        "ok": False,
        "validator_address": address,
        "proposal": bool(proposal),
        "error": last_error or "QBFT validator vote was not accepted before retry timeout",
        "attempts": attempts,
        "vote_retry_attempts": attempt,
        "vote_retry_timeout_s": timeout_s,
    }


def propose_qbft_validator_vote_to_all(
    plan: NetworkPlan,
    args: argparse.Namespace,
    validator_address: str,
    proposal: bool,
    *,
    exclude_services: set[str] | list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Submit the same QBFT vote to every automatic validator RPC candidate."""

    address = normalize_validator_address(validator_address)
    try:
        candidates = qbft_validator_vote_rpc_candidates(plan, args, exclude_services=exclude_services)
    except Exception as exc:  # noqa: BLE001 - return structured operator diagnostics.
        return {
            "ok": False,
            "validator_address": address,
            "proposal": bool(proposal),
            "error": f"{type(exc).__name__}: {exc}",
            "attempts": [],
            "votes_submitted": 0,
        }

    attempts: list[dict[str, Any]] = []
    votes_submitted = 0
    for candidate in candidates:
        response = qbft_json_rpc_candidates(
            plan,
            args,
            "qbft_proposeValidatorVote",
            [address, bool(proposal)],
            candidates=[candidate],
        )
        attempt = {
            "candidate": candidate,
            "ok": bool(response.get("ok") and response.get("result") is True),
            "result": response.get("result"),
            "rpc_url": response.get("rpc_url") or candidate.get("url"),
            "source": response.get("source") or candidate.get("source"),
        }
        if response.get("ok") and response.get("result") is True:
            votes_submitted += 1
        else:
            attempt["error"] = str(response.get("error") or "qbft_proposeValidatorVote did not return true")
            attempt["attempts"] = response.get("attempts") or []
        attempts.append(attempt)

    return {
        "ok": votes_submitted > 0,
        "validator_address": address,
        "proposal": bool(proposal),
        "votes_submitted": votes_submitted,
        "candidate_count": len(candidates),
        "attempts": attempts,
    }



def wait_for_qbft_validator_membership(
    plan: NetworkPlan,
    args: argparse.Namespace,
    validator_address: str,
    *,
    should_contain: bool,
) -> dict[str, Any]:
    address = normalize_validator_address(validator_address)
    timeout_s = chain_observation_timeout_from_args(args)
    poll_s = float(getattr(args, "rpc_poll_interval_s", DEFAULT_RPC_POLL_INTERVAL_S))
    if poll_s <= 0:
        poll_s = DEFAULT_RPC_POLL_INTERVAL_S
    deadline = time.time() + timeout_s
    last_validators: list[str] = []
    last_error = ""
    while time.time() <= deadline:
        try:
            validators = current_qbft_validators(plan, args, block="latest")
            last_validators = validators
            present = address in validators
            if present == should_contain:
                return {
                    "ok": True,
                    "validator_address": address,
                    "should_contain": should_contain,
                    "validators": validators,
                    "validator_count": len(validators),
                }
        except Exception as exc:  # noqa: BLE001 - return operator-facing diagnostics.
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(poll_s)
    return {
        "ok": False,
        "validator_address": address,
        "should_contain": should_contain,
        "validators": last_validators,
        "validator_count": len(last_validators),
        "error": last_error or "validator set membership did not reach the requested state before timeout",
    }


def validator_retire_membership_wait_args(args: argparse.Namespace) -> argparse.Namespace:
    """Use a retire-specific membership timeout long enough for async Coolify deploys.

    ``coolify_sync(... deploy=True)`` only proves Coolify accepted the deploy request.
    The temporary QBFT vote sidecar may still need to pull/start and then poll each
    internal validator RPC for up to its own 180 second window.  The generic
    observe-chain timeout defaults to 8 seconds, which is too short for this
    consensus-changing executor and can leave the service compose in its temporary
    sidecar state after a false timeout.
    """

    timeout_s = max(chain_observation_timeout_from_args(args), DEFAULT_VALIDATOR_RETIRE_MEMBERSHIP_TIMEOUT_S)
    return args_copy_with(args, chain_observation_timeout_s=timeout_s)


def restore_validator_retire_vote_topology(
    plan: NetworkPlan,
    args: argparse.Namespace,
    target_service: PlannedService,
) -> dict[str, Any]:
    """Redeploy the pre-retire host compose after a failed temporary vote phase."""

    restore_plan = plan_for_host_without_services(plan, target_service.host, set())
    restore_args = args_copy_with(
        args,
        host=target_service.host,
        no_bootstrap=True,
        _include_rpc_public_entry=True,
        _validator_vote=None,
        _retire_cleanup_services=(),
    )
    return coolify_sync(restore_plan, restore_args, deploy=True)


def validator_address_from_export(export: Mapping[str, Any], service: PlannedService) -> str:
    addresses = normalize_validator_address_map(export.get("validator_addresses"))
    runtime_dir = service_runtime_dir(service)
    for key in (runtime_dir, service.id):
        if key in addresses:
            return addresses[key]
    available = ", ".join(sorted(addresses)) or "<none>"
    raise PlanError(f"Config export did not include a validator address for {service.id!r}; available address files: {available}")


def fetch_validator_address_from_host(plan: NetworkPlan, args: argparse.Namespace, host_id: str, service: PlannedService) -> tuple[str, dict[str, Any]]:
    deadline = time.time() + qbft_config_export_timeout_s(args)
    last_export: dict[str, Any] = {}
    last_error = ""
    while True:
        export = export_qbft_config_from_host(plan, args, host_id)
        last_export = export
        try:
            if export.get("ok"):
                return validator_address_from_export(export, service), export
            last_error = str(export.get("error") or export.get("missing_files") or "config export not ok")
        except Exception as exc:  # noqa: BLE001 - keep retrying until the address sidecar has written the file.
            last_error = f"{type(exc).__name__}: {exc}"
        if bool(getattr(args, "dry_run", False)) or bool(getattr(args, "no_deploy", False)) or time.time() >= deadline:
            break
        time.sleep(2.0)
    raise PlanError(f"Unable to fetch validator address for {service.id!r} from host {host_id!r}: {last_error}; export={json.dumps({k: v for k, v in last_export.items() if k != 'files'}, sort_keys=True)}")




def apply_rpc_add_mutation(plan: NetworkPlan, args: argparse.Namespace, packet: dict[str, Any]) -> dict[str, Any]:
    """Execute the safe subset of mutate: adding rpc-only instances.

    This deliberately does not change validator sets and does not edit private
    state.  The executor first slurps the current non-secret QBFT runtime config
    from an existing host, seeds the new node with that material, starts the new
    RPC unrouted, waits for direct health, and only then publishes the host-local
    public RPC entry.
    """

    service_catalog = planned_service_by_id(plan)
    target_ids = [str(item) for item in packet.get("affected_instances") or []]
    target_services = [service_catalog[service_id] for service_id in target_ids if service_id in service_catalog]
    if not mutation_apply_is_supported_rpc_add(packet, target_services):
        return {
            **packet,
            "ok": False,
            "mode": "apply",
            "error": (
                "mutate --apply currently supports only rpc-only adds. "
                "Validator changes remain planner-only until the consensus executor lands."
            ),
            "execution": {
                "implemented": False,
                "no_mutation_performed": True,
                "message": "Unsupported mutation executor path.",
            },
        }

    if bool(getattr(args, "observe_chain", False)) and not bool(packet.get("ok", False)):
        return {
            **packet,
            "mode": "apply",
            "error": "preflight chain observation failed; refusing to mutate.",
            "execution": {
                "implemented": True,
                "no_mutation_performed": True,
                "message": "RPC-only add executor refused because observed_state.ok is false.",
            },
        }

    dry_run = bool(getattr(args, "dry_run", False))
    no_deploy = bool(getattr(args, "no_deploy", False))
    phases: list[dict[str, Any]] = []
    hosts = host_by_id(plan)

    try:
        runtime_bundle, discovery = discover_qbft_config_bundle(plan, args, target_services)
    except PlanError as exc:
        return {
            **packet,
            "ok": False,
            "mode": "apply",
            "error": str(exc),
            "execution": {
                "implemented": True,
                "dry_run": dry_run,
                "no_mutation_performed": dry_run,
                "message": "RPC-only add refused while selecting/slurping the current QBFT config source.",
            },
            "apply_phases": phases,
        }
    phases.append({"phase": "slurp-current-config", "result": discovery})
    if not discovery.get("ok"):
        return {
            **packet,
            "ok": False,
            "mode": "apply",
            "execution": {
                "implemented": True,
                "dry_run": dry_run,
                "no_mutation_performed": dry_run,
                "message": "RPC-only add failed while slurping current QBFT config.",
            },
            "apply_phases": phases,
        }

    runtime_static_refresh = build_runtime_static_refresh_from_bundle(plan, runtime_bundle or {})
    for host_id, host_services in services_by_host(target_services).items():
        execution_service_ids = rpc_add_execution_service_ids_for_host(plan, host_id, host_services, runtime_bundle or {})
        host_plan = plan_with_only_services(plan, execution_service_ids)
        preserved_service_ids = [service_id for service_id in execution_service_ids if service_id not in {service.id for service in host_services}]
        if preserved_service_ids:
            phases.append(
                {
                    "phase": "preserve-host-services",
                    "host": host_id,
                    "result": {
                        "ok": True,
                        "target_services": [service.id for service in host_services],
                        "preserved_services": preserved_service_ids,
                        "execution_services": execution_service_ids,
                    },
                }
            )
        deploy_args = args_copy_with(
            args,
            host=host_id,
            no_bootstrap=True,
            _runtime_import_bundle=runtime_bundle,
            _qbft_static_refresh=runtime_static_refresh if preserved_service_ids else None,
            _include_rpc_public_entry=False,
        )
        sync_result = coolify_sync(host_plan, deploy_args, deploy=not no_deploy)
        phases.append({"phase": "seed-new-node-bootstrap", "host": host_id, "result": sync_result})
        phases.append({"phase": "deploy-service", "host": host_id, "result": sync_result})
        if not sync_result.get("ok"):
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": dry_run,
                    "message": f"RPC-only add failed while updating Coolify service on host {host_id}.",
                },
                "apply_phases": phases,
            }

        if dry_run or bool(getattr(args, "skip_wait_rpc", False)) or no_deploy:
            phases.append(
                {
                    "phase": "wait-direct-rpc",
                    "host": host_id,
                    "result": {
                        "ok": True,
                        "dry_run": dry_run,
                        "skipped": bool(getattr(args, "skip_wait_rpc", False)),
                    },
                }
            )
        else:
            for service in host_services:
                backend_url = rpc_public_entry_backend_url(plan, hosts[service.host], service)
                wait_args = mutate_rpc_wait_args(args, rpc_url=backend_url)
                try:
                    wait_result = wait_for_rpc(plan, wait_args)
                except Exception as exc:  # noqa: BLE001 - return a structured mutation packet instead of stderr-only failure.
                    wait_result = {
                        "ok": False,
                        "rpc_url": backend_url,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                phases.append({"phase": "wait-direct-rpc", "host": host_id, "instance": service.id, "result": wait_result})
                if not wait_result.get("ok", False):
                    return {
                        **packet,
                        "ok": False,
                        "mode": "apply",
                        "execution": {
                            "implemented": True,
                            "dry_run": dry_run,
                            "no_mutation_performed": dry_run,
                            "message": (
                                f"RPC-only add failed while waiting for {service.id} direct RPC. "
                                "The public RPC entry was not enabled for this host."
                            ),
                        },
                        "apply_phases": phases,
                    }

        publish_args = args_copy_with(
            args,
            host=host_id,
            no_bootstrap=True,
            _runtime_import_bundle=runtime_bundle,
            _qbft_static_refresh=runtime_static_refresh if preserved_service_ids else None,
            _include_rpc_public_entry=True,
        )
        publish_result = coolify_sync(host_plan, publish_args, deploy=not no_deploy)
        phases.append({"phase": "commit-full-network-topology", "host": host_id, "result": publish_result})
        phases.append({"phase": "update-public-rpc-entry", "host": host_id, "result": publish_result})
        if not publish_result.get("ok"):
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": dry_run,
                    "message": f"RPC-only add verified direct RPC but failed to enable public RPC entry on host {host_id}.",
                },
                "apply_phases": phases,
            }

    if rpc_public_entry_enabled(plan):
        if dry_run:
            phases.append({"phase": "verify-public-rpc", "result": {"ok": True, "dry_run": True}})
        else:
            public_result = wait_for_stable_observable_chain(
                plan,
                args,
                label="verify-public-rpc",
            )
            phases.append({"phase": "verify-public-rpc", "result": public_result})
            if not public_result.get("ok", False):
                return {
                    **packet,
                    "ok": False,
                    "mode": "apply",
                    "execution": {
                        "implemented": True,
                        "dry_run": dry_run,
                        "no_mutation_performed": dry_run,
                        "message": "RPC-only add deployed direct RPC but canonical public RPC verification failed.",
                    },
                    "apply_phases": phases,
                }

    return {
        **packet,
        "ok": True,
        "mode": "apply",
        "execution": {
            "implemented": True,
            "dry_run": dry_run,
            "no_mutation_performed": dry_run,
            "message": (
                "RPC-only add dry-run completed without mutating Coolify."
                if dry_run
                else (
                    "RPC-only add applied: current config slurped, new RPC bootstrapped unrouted, "
                    "direct RPC verified, public entry enabled, and canonical RPC checked."
                )
            ),
        },
        "apply_phases": phases,
    }


def apply_rpc_retire_mutation(plan: NetworkPlan, args: argparse.Namespace, packet: dict[str, Any]) -> dict[str, Any]:
    """Execute the safe subset of mutate: retiring rpc-only instances.

    This refuses all validator/consensus changes.  The executor redeploys each
    affected Coolify host with the retired RPC instance omitted from the host
    target-state, verifies the canonical public RPC surface, and checks that the
    retired direct RPC backend no longer answers.
    """

    service_catalog = planned_service_by_id(plan)
    target_ids = [str(item) for item in packet.get("affected_instances") or []]
    target_services = [service_catalog[service_id] for service_id in target_ids if service_id in service_catalog]
    if not mutation_apply_is_supported_rpc_retire(packet, target_services):
        return {
            **packet,
            "ok": False,
            "mode": "apply",
            "error": (
                "mutate --apply currently supports only rpc-only adds and rpc-only retires. "
                "Validator changes remain planner-only until the consensus executor lands."
            ),
            "execution": {
                "implemented": False,
                "no_mutation_performed": True,
                "message": "Unsupported mutation executor path.",
            },
        }

    dry_run = bool(getattr(args, "dry_run", False))
    no_deploy = bool(getattr(args, "no_deploy", False))
    phases: list[dict[str, Any]] = []
    retired_ids = {service.id for service in target_services}

    if dry_run or no_deploy:
        phases.append(
            {
                "phase": "observe-chain",
                "result": {
                    "ok": True,
                    "dry_run": dry_run,
                    "skipped": True,
                    "reason": "dry-run" if dry_run else "no-deploy",
                },
            }
        )
    else:
        preflight_result = observe_chain_state(plan, args_copy_with(args, rpc_url=""))
        phases.append({"phase": "observe-chain", "result": preflight_result})
        if not preflight_result.get("ok", False):
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "error": "preflight canonical chain observation failed; refusing to retire RPC node.",
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": True,
                    "message": "RPC-only retire executor refused because canonical public RPC preflight failed.",
                },
                "apply_phases": phases,
            }

    for host_id, retiring_host_services in services_by_host(target_services).items():
        host_plan = plan_for_host_without_services(plan, host_id, retired_ids)
        remaining_service_ids = [service.id for service in host_plan.services]
        deploy_args = args_copy_with(
            args,
            host=host_id,
            no_bootstrap=True,
            _include_rpc_public_entry=True,
            _retire_cleanup_services=tuple(retiring_host_services),
        )
        if remaining_service_ids:
            sync_result = coolify_sync(host_plan, deploy_args, deploy=not no_deploy)
        else:
            cleanup_compose = render_retire_cleanup_compose_for_host(plan, host_id, retiring_host_services)
            cleanup_args = args_copy_with(deploy_args, _compose_override=cleanup_compose)
            sync_result = coolify_sync(host_plan, cleanup_args, deploy=not no_deploy)
            sync_result = {
                **sync_result,
                "cleanup_only_compose": True,
                "remaining_instances": [],
            }

        phases.append(
            {
                "phase": "remove-public-rpc-entry",
                "host": host_id,
                "retired_instances": [service.id for service in retiring_host_services],
                "remaining_instances": remaining_service_ids,
                "result": sync_result,
            }
        )
        phases.append(
            {
                "phase": "redeploy-service-without-target",
                "host": host_id,
                "retired_instances": [service.id for service in retiring_host_services],
                "remaining_instances": remaining_service_ids,
                "result": sync_result,
            }
        )
        phases.append(
            {
                "phase": "stop-coolify-service",
                "host": host_id,
                "retired_instances": [service.id for service in retiring_host_services],
                "result": {
                    "ok": bool(sync_result.get("ok")),
                    "method": "docker-label-cleanup-sidecar",
                    "message": (
                        "Retired instance is omitted from the redeployed host compose and a one-shot Docker-label "
                        "cleanup sidecar is included to remove orphan containers for the retired service."
                    ),
                },
            }
        )
        if not sync_result.get("ok"):
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": dry_run,
                    "message": f"RPC-only retire failed while redeploying host {host_id} without the retired instance.",
                },
                "apply_phases": phases,
            }

    if rpc_public_entry_enabled(plan):
        if dry_run or no_deploy:
            phases.append(
                {
                    "phase": "verify-public-rpc",
                    "result": {
                        "ok": True,
                        "dry_run": dry_run,
                        "skipped": True,
                        "reason": "dry-run" if dry_run else "no-deploy",
                    },
                }
            )
        else:
            try:
                public_wait_args = mutate_rpc_retire_public_wait_args(args, rpc_url=str(plan.external_rpc_url or ""))
                public_result = wait_for_stable_observable_chain(
                    plan,
                    public_wait_args,
                    label="verify-public-rpc",
                )
            except Exception as exc:  # noqa: BLE001 - capture operator-facing failure in packet.
                public_result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            phases.append({"phase": "verify-public-rpc", "result": public_result})
            if not public_result.get("ok", False):
                return {
                    **packet,
                    "ok": False,
                    "mode": "apply",
                    "execution": {
                        "implemented": True,
                        "dry_run": dry_run,
                        "no_mutation_performed": dry_run,
                        "message": "RPC-only retire redeployed the target host but canonical public RPC verification failed.",
                    },
                    "apply_phases": phases,
                }

    if dry_run or no_deploy or bool(getattr(args, "skip_wait_rpc", False)):
        phases.append(
            {
                "phase": "verify-retired-direct-rpc-unreachable",
                "result": {
                    "ok": True,
                    "dry_run": dry_run,
                    "skipped": True,
                    "reason": (
                        "dry-run"
                        if dry_run
                        else "no-deploy"
                        if no_deploy
                        else "skip-wait-rpc"
                    ),
                },
            }
        )
    else:
        retired_result = verify_retired_rpc_unreachable(plan, args, target_services)
        phases.append({"phase": "verify-retired-direct-rpc-unreachable", "result": retired_result})
        if not retired_result.get("ok", False):
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": dry_run,
                    "message": (
                        "RPC-only retire updated host compose and canonical RPC stayed healthy, "
                        "but the retired direct RPC backend still answered."
                    ),
                },
                "apply_phases": phases,
            }

    phases.append(
        {
            "phase": "commit-topology",
            "result": {
                "ok": True,
                "retired_instances": sorted(retired_ids),
                "state_note": (
                    "This executor redeploys Coolify target compose with retired instances omitted; "
                    "it does not edit private state or encode file deletion semantics."
                ),
            },
        }
    )

    return {
        **packet,
        "ok": True,
        "mode": "apply",
        "execution": {
            "implemented": True,
            "dry_run": dry_run,
            "no_mutation_performed": dry_run,
            "message": (
                "RPC-only retire dry-run completed without mutating Coolify."
                if dry_run
                else (
                    "RPC-only retire applied: retired RPC omitted from host compose, public RPC verified, "
                    "and retired direct RPC checked as unreachable."
                )
            ),
        },
        "apply_phases": phases,
    }




def apply_validator_retire_mutation(plan: NetworkPlan, args: argparse.Namespace, packet: dict[str, Any]) -> dict[str, Any]:
    """Retire one validator-only node without touching validator-RPC services.

    The safe sequence is vote first, wait until QBFT removes the validator from
    the validator set, and only then redeploy the affected Coolify host with the
    retired service omitted.  With two validators, the target validator must also
    vote for its own removal, so this executor deploys a temporary host-local
    vote sidecar before removing the service.
    """

    service_catalog = planned_service_by_id(plan)
    target_ids = [str(item) for item in packet.get("affected_instances") or []]
    target_services = [service_catalog[service_id] for service_id in target_ids if service_id in service_catalog]
    if not mutation_apply_is_supported_validator_retire(packet, target_services):
        return {
            **packet,
            "ok": False,
            "mode": "apply",
            "error": (
                "mutate --apply validator retire currently supports only validator-only targets. "
                "Validator-RPC and mixed consensus changes remain planner-only."
            ),
            "execution": {
                "implemented": False,
                "no_mutation_performed": True,
                "message": "Unsupported validator retire executor path.",
            },
        }
    if len(target_services) != 1:
        return {
            **packet,
            "ok": False,
            "mode": "apply",
            "error": "validator retire apply currently supports exactly one validator-only instance per mutation.",
            "execution": {"implemented": True, "no_mutation_performed": True, "message": "Refused multi-validator retire."},
        }
    if not mutation_consensus_acknowledged(packet):
        return {
            **packet,
            "ok": False,
            "mode": "apply",
            "error": "Consensus-changing mutate --apply requires --ack-consensus-change.",
            "execution": {"implemented": True, "no_mutation_performed": True, "message": "Missing consensus acknowledgement."},
        }

    target_service = target_services[0]
    dry_run = bool(getattr(args, "dry_run", False))
    no_deploy = bool(getattr(args, "no_deploy", False))
    phases: list[dict[str, Any]] = []
    retired_ids = {target_service.id}

    if dry_run or no_deploy:
        current_validators: list[str] = []
        target_address = "0x0000000000000000000000000000000000000000"
        phases.append(
            {
                "phase": "verify-current-validator-set",
                "result": {"ok": True, "dry_run": dry_run, "skipped": True, "reason": "dry-run" if dry_run else "no-deploy"},
            }
        )
        phases.append(
            {
                "phase": "verify-node-identity",
                "host": target_service.host,
                "instance": target_service.id,
                "result": {"ok": True, "dry_run": dry_run, "skipped": True, "validator_address": target_address},
            }
        )
    else:
        try:
            current_validators = current_qbft_validators(plan, args)
        except Exception as exc:  # noqa: BLE001
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "error": f"Unable to read current QBFT validator set: {type(exc).__name__}: {exc}",
                "execution": {"implemented": True, "dry_run": dry_run, "no_mutation_performed": True, "message": "Validator retire preflight failed."},
                "apply_phases": phases,
            }
        phases.append(
            {
                "phase": "verify-current-validator-set",
                "result": {"ok": True, "validators": current_validators, "validator_count": len(current_validators)},
            }
        )
        try:
            target_address, address_export = fetch_validator_address_from_host(plan, args, target_service.host, target_service)
        except Exception as exc:  # noqa: BLE001
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "error": f"Unable to fetch retired validator address: {type(exc).__name__}: {exc}",
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": True,
                    "message": "Validator retire refused because the target validator address could not be verified.",
                },
                "apply_phases": phases,
            }
        phases.append(
            {
                "phase": "verify-node-identity",
                "host": target_service.host,
                "instance": target_service.id,
                "result": {
                    "ok": True,
                    "validator_address": target_address,
                    "export": {key: value for key, value in address_export.items() if key not in {"files", "sync"}},
                },
            }
        )

        if target_address in current_validators:
            remaining_validators = [address for address in current_validators if address != target_address]
            minimum_validators = int(plan.topology_policy.minimum_validators)
            if len(remaining_validators) < minimum_validators:
                return {
                    **packet,
                    "ok": False,
                    "mode": "apply",
                    "error": (
                        f"Refusing validator retire because it would leave {len(remaining_validators)} validator(s), "
                        f"below topology_policy.minimum_validators={minimum_validators}."
                    ),
                    "execution": {
                        "implemented": True,
                        "dry_run": dry_run,
                        "no_mutation_performed": True,
                        "message": "Validator retire refused before voting because quorum policy would be violated.",
                    },
                    "apply_phases": phases,
                }
            if not remaining_validators:
                return {
                    **packet,
                    "ok": False,
                    "mode": "apply",
                    "error": "Refusing validator retire because it would leave zero validators.",
                    "execution": {
                        "implemented": True,
                        "dry_run": dry_run,
                        "no_mutation_performed": True,
                        "message": "Validator retire refused before voting because it would halt consensus.",
                    },
                    "apply_phases": phases,
                }

            direct_vote_result = propose_qbft_validator_vote_to_all(
                plan,
                args,
                target_address,
                False,
                exclude_services={target_service.id},
            )
            phases.append({"phase": "propose-validator-vote", "result": direct_vote_result})

            vote_services = validator_retire_vote_services_for_host(plan, target_service.host, target_service)
            vote_service_ids = [service.id for service in vote_services]
            vote_plan = plan_with_only_services(plan, vote_service_ids)
            vote_payload = {
                "validator_address": target_address,
                "proposal": False,
                "services": [{"id": service.id} for service in vote_services],
            }
            vote_args = args_copy_with(
                args,
                host=target_service.host,
                no_bootstrap=True,
                _include_rpc_public_entry=True,
                _validator_vote=vote_payload,
            )
            sidecar_result = coolify_sync(vote_plan, vote_args, deploy=not no_deploy)
            phases.append(
                {
                    "phase": "propose-validator-vote-sidecar",
                    "host": target_service.host,
                    "services": vote_service_ids,
                    "result": sidecar_result,
                }
            )
            if not sidecar_result.get("ok"):
                restore_result = restore_validator_retire_vote_topology(plan, args, target_service)
                phases.append(
                    {
                        "phase": "restore-pre-retire-topology",
                        "host": target_service.host,
                        "reason": "vote-sidecar-deploy-failed",
                        "result": restore_result,
                    }
                )
                return {
                    **packet,
                    "ok": False,
                    "mode": "apply",
                    "execution": {
                        "implemented": True,
                        "dry_run": dry_run,
                        "no_mutation_performed": False,
                        "message": (
                            "Validator retire failed while deploying the host-local QBFT vote sidecar; "
                            "the pre-retire topology restore was attempted."
                        ),
                    },
                    "apply_phases": phases,
                }

            membership_wait_args = validator_retire_membership_wait_args(args)
            membership_result = wait_for_qbft_validator_membership(
                plan,
                membership_wait_args,
                target_address,
                should_contain=False,
            )
            phases.append({"phase": "wait-validator-set-removal", "result": membership_result})
            if not membership_result.get("ok"):
                restore_result = restore_validator_retire_vote_topology(plan, args, target_service)
                phases.append(
                    {
                        "phase": "restore-pre-retire-topology",
                        "host": target_service.host,
                        "reason": "validator-set-removal-timeout",
                        "result": restore_result,
                    }
                )
                return {
                    **packet,
                    "ok": False,
                    "mode": "apply",
                    "execution": {
                        "implemented": True,
                        "dry_run": dry_run,
                        "no_mutation_performed": False,
                        "message": (
                            "Validator retire votes were submitted but the validator set still contained the target "
                            "before the retire timeout; the pre-retire topology restore was attempted."
                        ),
                    },
                    "apply_phases": phases,
                }
        else:
            phases.append(
                {
                    "phase": "propose-validator-vote",
                    "result": {
                        "ok": True,
                        "skipped": True,
                        "reason": "target-validator-address-not-in-current-validator-set",
                        "validator_address": target_address,
                        "proposal": False,
                    },
                }
            )
            phases.append(
                {
                    "phase": "wait-validator-set-removal",
                    "result": {
                        "ok": True,
                        "skipped": True,
                        "validator_address": target_address,
                        "validators": current_validators,
                        "validator_count": len(current_validators),
                    },
                }
            )

    host_plan = plan_for_host_without_services(plan, target_service.host, retired_ids)
    remaining_service_ids = [service.id for service in host_plan.services]
    deploy_args = args_copy_with(
        args,
        host=target_service.host,
        no_bootstrap=True,
        _include_rpc_public_entry=True,
        _retire_cleanup_services=tuple(target_services),
        _validator_vote=None,
    )
    if dry_run or no_deploy:
        sync_result = {
            "ok": True,
            "dry_run": dry_run,
            "skipped": True,
            "reason": "dry-run" if dry_run else "no-deploy",
            "remaining_instances": remaining_service_ids,
        }
    elif remaining_service_ids:
        sync_result = coolify_sync(host_plan, deploy_args, deploy=True)
    else:
        cleanup_compose = render_retire_cleanup_compose_for_host(plan, target_service.host, target_services)
        cleanup_args = args_copy_with(deploy_args, _compose_override=cleanup_compose)
        sync_result = coolify_sync(host_plan, cleanup_args, deploy=True)
        sync_result = {
            **sync_result,
            "cleanup_only_compose": True,
            "remaining_instances": [],
        }

    phases.append(
        {
            "phase": "stop-coolify-service",
            "host": target_service.host,
            "retired_instances": [target_service.id],
            "remaining_instances": remaining_service_ids,
            "result": {
                "ok": bool(sync_result.get("ok")),
                "method": "redeploy-without-target-and-docker-label-cleanup-sidecar",
                "sync": sync_result,
            },
        }
    )
    if not sync_result.get("ok"):
        return {
            **packet,
            "ok": False,
            "mode": "apply",
            "execution": {
                "implemented": True,
                "dry_run": dry_run,
                "no_mutation_performed": dry_run,
                "message": f"Validator retire removed the target from QBFT but failed while redeploying host {target_service.host} without the retired service.",
            },
            "apply_phases": phases,
        }

    if dry_run or no_deploy:
        block_result = {"ok": True, "dry_run": dry_run, "skipped": True}
    else:
        block_result = wait_for_stable_observable_chain(
            plan,
            args,
            expected_validator_count=len(remaining_validators) if "remaining_validators" in locals() else None,
            required_validators_absent=(target_address,),
            label="verify-block-production",
        )
    phases.append({"phase": "verify-block-production", "result": block_result})
    if not block_result.get("ok", False):
        return {
            **packet,
            "ok": False,
            "mode": "apply",
            "execution": {
                "implemented": True,
                "dry_run": dry_run,
                "no_mutation_performed": False,
                "message": "Validator retire removed the target but canonical chain verification failed afterward.",
            },
            "apply_phases": phases,
        }

    if dry_run or no_deploy or bool(getattr(args, "skip_wait_rpc", False)) or target_service.rpc_host_port is None:
        phases.append(
            {
                "phase": "verify-retired-direct-rpc-unreachable",
                "result": {
                    "ok": True,
                    "dry_run": dry_run,
                    "skipped": True,
                    "reason": (
                        "no-retired-rpc-host-port"
                        if target_service.rpc_host_port is None
                        else "dry-run"
                        if dry_run
                        else "no-deploy"
                        if no_deploy
                        else "skip-wait-rpc"
                    ),
                },
            }
        )
    else:
        retired_result = verify_retired_rpc_unreachable(plan, args, target_services)
        phases.append({"phase": "verify-retired-direct-rpc-unreachable", "result": retired_result})
        if not retired_result.get("ok", False):
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": False,
                    "message": "Validator retire completed consensus removal, but the retired direct RPC backend still answered.",
                },
                "apply_phases": phases,
            }

    phases.append(
        {
            "phase": "commit-topology",
            "result": {
                "ok": True,
                "retired_instances": [target_service.id],
                "validator_address": target_address,
                "state_note": (
                    "This executor votes the validator out and redeploys the host without the service; "
                    "it does not edit private state, so the same instance can be re-added by running mutate --add."
                ),
            },
        }
    )
    return {
        **packet,
        "ok": True,
        "mode": "apply",
        "execution": {
            "implemented": True,
            "dry_run": dry_run,
            "no_mutation_performed": dry_run,
            "message": (
                "Validator retire dry-run completed without mutating Coolify or QBFT."
                if dry_run
                else "Validator retire applied: QBFT removal verified, host redeployed without the validator, and block production checked."
            ),
        },
        "apply_phases": phases,
    }



def apply_validator_add_mutation(plan: NetworkPlan, args: argparse.Namespace, packet: dict[str, Any]) -> dict[str, Any]:
    """Execute the first consensus-changing mutate subset: adding one validator-only node.

    This intentionally handles only the one-validator bring-up topology first.
    With one current validator, one successful QBFT vote is enough to add the new
    validator.  The vote must come from an existing validator endpoint; automatic
    consensus votes never fall back to rpc-only nodes or public RPC.
    """

    service_catalog = planned_service_by_id(plan)
    target_ids = [str(item) for item in packet.get("affected_instances") or []]
    target_services = [service_catalog[service_id] for service_id in target_ids if service_id in service_catalog]
    if not mutation_apply_is_supported_validator_add(packet, target_services):
        return {
            **packet,
            "ok": False,
            "mode": "apply",
            "error": (
                "mutate --apply validator add currently supports only validator-only targets. "
                "Validator-RPC and mixed consensus changes remain planner-only until their executor lands."
            ),
            "execution": {
                "implemented": False,
                "no_mutation_performed": True,
                "message": "Unsupported validator mutation executor path.",
            },
        }
    if len(target_services) != 1:
        return {
            **packet,
            "ok": False,
            "mode": "apply",
            "error": "validator add apply currently supports exactly one validator-only instance per mutation.",
            "execution": {"implemented": True, "no_mutation_performed": True, "message": "Refused multi-validator add."},
        }
    if not mutation_consensus_acknowledged(packet):
        return {
            **packet,
            "ok": False,
            "mode": "apply",
            "error": "Consensus-changing mutate --apply requires --ack-consensus-change.",
            "execution": {"implemented": True, "no_mutation_performed": True, "message": "Missing consensus acknowledgement."},
        }

    target_service = target_services[0]
    dry_run = bool(getattr(args, "dry_run", False))
    no_deploy = bool(getattr(args, "no_deploy", False))
    phases: list[dict[str, Any]] = []

    try:
        runtime_bundle, discovery = discover_qbft_config_bundle(plan, args, target_services)
    except PlanError as exc:
        return {
            **packet,
            "ok": False,
            "mode": "apply",
            "error": str(exc),
            "execution": {
                "implemented": True,
                "dry_run": dry_run,
                "no_mutation_performed": True,
                "message": "Validator add refused while selecting/slurping the current QBFT config source.",
            },
            "apply_phases": phases,
        }
    phases.append({"phase": "slurp-current-config", "result": discovery})
    if not discovery.get("ok"):
        return {
            **packet,
            "ok": False,
            "mode": "apply",
            "execution": {
                "implemented": True,
                "dry_run": dry_run,
                "no_mutation_performed": True,
                "message": "Validator add failed while slurping current QBFT config.",
            },
            "apply_phases": phases,
        }

    if dry_run or no_deploy:
        current_validators = []
        phases.append(
            {
                "phase": "verify-current-validator-set",
                "result": {"ok": True, "dry_run": dry_run, "skipped": True, "reason": "dry-run" if dry_run else "no-deploy"},
            }
        )
    else:
        try:
            current_validators = current_qbft_validators(plan, args)
        except Exception as exc:  # noqa: BLE001
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "error": f"Unable to read current QBFT validator set: {type(exc).__name__}: {exc}",
                "execution": {"implemented": True, "dry_run": dry_run, "no_mutation_performed": True, "message": "Validator add preflight failed."},
                "apply_phases": phases,
            }
        phases.append(
            {
                "phase": "verify-current-validator-set",
                "result": {"ok": True, "validators": current_validators, "validator_count": len(current_validators)},
            }
        )
        if len(current_validators) != 1:
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "error": (
                    "validator add apply currently supports only the one-current-validator bootstrap case. "
                    f"Current validator count is {len(current_validators)}; multi-validator vote fanout is not implemented yet."
                ),
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": True,
                    "message": "Validator add refused because one canonical RPC vote would not be enough.",
                },
                "apply_phases": phases,
            }

    host_id = target_service.host
    deploy_service_ids = validator_add_execution_service_ids(plan, target_service, discovery)
    host_plan = plan_with_only_services(plan, deploy_service_ids)
    phases.append(
        {
            "phase": "select-deploy-service-set",
            "host": host_id,
            "result": {
                "ok": True,
                "services": deploy_service_ids,
                "target": target_service.id,
                "selected_source_host": discovery.get("selected_source_host") or discovery.get("source_host") or "",
            },
        }
    )
    deploy_args = args_copy_with(
        args,
        host=host_id,
        no_bootstrap=True,
        _runtime_import_bundle=runtime_bundle,
        _include_rpc_public_entry=False,
        _park_inactive_qbft_helpers=False,
    )
    sync_result = coolify_sync(host_plan, deploy_args, deploy=not no_deploy)
    phases.append({"phase": "seed-new-node-bootstrap", "host": host_id, "result": sync_result})
    phases.append({"phase": "deploy-service", "host": host_id, "result": sync_result})
    if not sync_result.get("ok"):
        return {
            **packet,
            "ok": False,
            "mode": "apply",
            "execution": {
                "implemented": True,
                "dry_run": dry_run,
                "no_mutation_performed": dry_run,
                "message": f"Validator add failed while updating Coolify service on host {host_id}.",
            },
            "apply_phases": phases,
        }

    if dry_run or no_deploy:
        validator_address = "0x0000000000000000000000000000000000000000"
        phases.append(
            {
                "phase": "verify-node-identity",
                "host": host_id,
                "instance": target_service.id,
                "result": {
                    "ok": True,
                    "dry_run": dry_run,
                    "skipped": True,
                    "reason": "dry-run" if dry_run else "no-deploy",
                    "message": "Validator address export not fetched during dry-run/no-deploy.",
                },
            }
        )
    else:
        try:
            validator_address, address_export = fetch_validator_address_from_host(plan, args, host_id, target_service)
        except Exception as exc:  # noqa: BLE001
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "error": f"Unable to fetch new validator address: {type(exc).__name__}: {exc}",
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": False,
                    "message": "Validator service was deployed but validator address export failed; consensus vote was not submitted.",
                },
                "apply_phases": phases,
            }
        phases.append(
            {
                "phase": "verify-node-identity",
                "host": host_id,
                "instance": target_service.id,
                "result": {
                    "ok": True,
                    "validator_address": validator_address,
                    "export": {key: value for key, value in address_export.items() if key not in {"files", "sync"}},
                },
            }
        )

        try:
            target_public_key = validator_public_key_from_export(address_export, target_service)
            static_refresh = build_post_add_static_refresh(plan, runtime_bundle, target_service, target_public_key)
        except Exception as exc:  # noqa: BLE001
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "error": f"Unable to prepare post-add static-node topology: {type(exc).__name__}: {exc}",
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": False,
                    "message": "Validator service was deployed and identified, but static-node refresh could not be prepared; consensus vote was not submitted.",
                },
                "apply_phases": phases,
            }
        phases.append(
            {
                "phase": "prepare-post-add-static-topology",
                "result": {
                    "ok": True,
                    "target": target_service.id,
                    "target_enode": static_refresh.get("target_enode"),
                    "static_node_count": len(static_refresh.get("static_nodes") or []),
                },
            }
        )

        static_verify_services_by_host: dict[str, list[PlannedService]] = {host_id: [target_service]}

        target_static_args = args_copy_with(
            args,
            host=host_id,
            no_bootstrap=True,
            _qbft_static_refresh=static_refresh,
            _include_rpc_public_entry=False,
            _park_inactive_qbft_helpers=False,
        )
        target_static_result = coolify_sync(host_plan, target_static_args, deploy=not no_deploy)
        phases.append({"phase": "refresh-target-static-topology", "host": host_id, "result": target_static_result})
        if not target_static_result.get("ok"):
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": False,
                    "message": "Validator add deployed the node but failed while refreshing target-host static-node topology; consensus vote was not submitted.",
                },
                "apply_phases": phases,
            }

        source_host_id = str(runtime_bundle.get("source_host") or discovery.get("selected_source_host") or discovery.get("source_host") or "")
        source_host_id = safe_id(source_host_id, kind="host") if source_host_id else ""
        source_refresh_services = live_validator_services_from_export(
            plan,
            runtime_bundle,
            current_validators if "current_validators" in locals() else [],
            source_host=source_host_id,
        )
        source_refresh_ids = [service.id for service in source_refresh_services]
        if source_host_id and source_refresh_ids:
            static_verify_services_by_host[source_host_id] = list(source_refresh_services)
        if source_host_id and (source_host_id != host_id or set(source_refresh_ids) != set(deploy_service_ids)):
            if not source_refresh_ids:
                return {
                    **packet,
                    "ok": False,
                    "mode": "apply",
                    "execution": {
                        "implemented": True,
                        "dry_run": dry_run,
                        "no_mutation_performed": False,
                        "message": "Validator add could not identify the live source validator service to refresh with the new validator enode; consensus vote was not submitted.",
                    },
                    "apply_phases": phases,
                }
            source_refresh_plan = plan_with_only_services(plan, source_refresh_ids)
            source_static_args = args_copy_with(
                args,
                host=source_host_id,
                no_bootstrap=True,
                _qbft_static_refresh=static_refresh,
                _include_rpc_public_entry=True,
                _park_inactive_qbft_helpers=False,
            )
            source_static_result = coolify_sync(source_refresh_plan, source_static_args, deploy=not no_deploy)
            phases.append({"phase": "refresh-source-static-topology", "host": source_host_id, "services": source_refresh_ids, "result": source_static_result})
            if not source_static_result.get("ok"):
                return {
                    **packet,
                    "ok": False,
                    "mode": "apply",
                    "execution": {
                        "implemented": True,
                        "dry_run": dry_run,
                        "no_mutation_performed": False,
                        "message": "Validator add failed while refreshing the existing validator host with the new validator enode; consensus vote was not submitted.",
                    },
                    "apply_phases": phases,
                }

        runtime_topology_result = verify_post_add_static_runtime_topology(
            plan,
            args,
            static_refresh=static_refresh,
            services_by_host=static_verify_services_by_host,
        )
        phases.append({"phase": "verify-active-static-topology", "result": runtime_topology_result})
        if not runtime_topology_result.get("ok"):
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": False,
                    "message": "Validator add refreshed static-node topology but active runtime files did not prove the post-add topology; consensus vote was not submitted.",
                },
                "apply_phases": phases,
            }

        pre_vote_result = wait_for_stable_observable_chain(
            plan,
            args,
            expected_validator_count=len(current_validators) if "current_validators" in locals() else None,
            label="verify-pre-vote-static-topology",
        )
        phases.append({"phase": "verify-pre-vote-static-topology", "result": pre_vote_result})
        if not pre_vote_result.get("ok"):
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": False,
                    "message": "Validator add refreshed static-node topology but the existing chain was not stable before the QBFT vote; consensus vote was not submitted.",
                },
                "apply_phases": phases,
            }

    if dry_run or no_deploy:
        phases.append(
            {
                "phase": "wait-new-validator-peer",
                "result": {
                    "ok": True,
                    "dry_run": dry_run,
                    "skipped": True,
                    "reason": "dry-run" if dry_run else "no-deploy",
                    "min_peer_count": 1,
                },
            }
        )
        phases.append(
            {
                "phase": "propose-validator-vote",
                "result": {"ok": True, "dry_run": dry_run, "skipped": True, "proposal": True, "validator_address": validator_address},
            }
        )
        phases.append(
            {
                "phase": "wait-validator-set",
                "result": {"ok": True, "dry_run": dry_run, "skipped": True, "validator_address": validator_address},
            }
        )
    else:
        peer_result = wait_for_qbft_peer_count(
            plan,
            args,
            min_peer_count=1,
            label="wait-new-validator-peer",
        )
        phases.append({"phase": "wait-new-validator-peer", "result": peer_result})
        if not peer_result.get("ok"):
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": False,
                    "message": "Validator add deployed the node and fetched its address, but the new validator did not peer before the QBFT vote.",
                },
                "apply_phases": phases,
            }
        vote_result = propose_qbft_validator_vote_until_ok(
            plan,
            args,
            validator_address,
            True,
            exclude_services={target_service.id},
        )
        phases.append({"phase": "propose-validator-vote", "result": vote_result})
        if not vote_result.get("ok"):
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": False,
                    "message": "Validator add deployed the node but QBFT add-validator vote failed.",
                },
                "apply_phases": phases,
            }
        membership_result = wait_for_qbft_validator_membership(
            plan,
            validator_mutation_membership_wait_args(args),
            validator_address,
            should_contain=True,
        )
        phases.append({"phase": "wait-validator-set", "result": membership_result})
        if not membership_result.get("ok"):
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": False,
                    "message": "Validator add vote was submitted but the validator set did not include the new validator before timeout.",
                },
                "apply_phases": phases,
            }

    if dry_run or no_deploy:
        phases.append({"phase": "verify-block-production", "result": {"ok": True, "dry_run": dry_run, "skipped": True}})
    else:
        block_result = wait_for_stable_observable_chain(
            plan,
            args,
            expected_validator_count=len(current_validators) + 1 if "current_validators" in locals() else None,
            required_validators_present=(validator_address,),
            label="verify-block-production",
        )
        phases.append({"phase": "verify-block-production", "result": block_result})
        if not block_result.get("ok", False):
            return {
                **packet,
                "ok": False,
                "mode": "apply",
                "execution": {
                    "implemented": True,
                    "dry_run": dry_run,
                    "no_mutation_performed": False,
                    "message": "Validator add reached the validator set but canonical chain verification failed afterward.",
                },
                "apply_phases": phases,
            }

    phases.append(
        {
            "phase": "commit-topology",
            "result": {
                "ok": True,
                "added_instances": [target_service.id],
                "validator_address": validator_address,
                "state_note": (
                    "This executor deploys the validator declared in private state and votes it into QBFT; "
                    "it does not edit private state."
                ),
            },
        }
    )
    return {
        **packet,
        "ok": True,
        "mode": "apply",
        "execution": {
            "implemented": True,
            "dry_run": dry_run,
            "no_mutation_performed": dry_run,
            "message": (
                "Validator add dry-run completed without mutating Coolify or QBFT."
                if dry_run
                else "Validator add applied: runtime config imported, validator key/address initialized, QBFT add vote submitted, validator set verified, and block production checked."
            ),
        },
        "apply_phases": phases,
    }




def apply_mutation(plan: NetworkPlan, args: argparse.Namespace, packet: dict[str, Any]) -> dict[str, Any]:
    intent = packet.get("intent") if isinstance(packet.get("intent"), dict) else {}
    service_catalog = planned_service_by_id(plan)
    target_ids = [str(item) for item in packet.get("affected_instances") or []]
    target_services = [service_catalog[service_id] for service_id in target_ids if service_id in service_catalog]
    if intent.get("action") == "add" and mutation_apply_is_supported_validator_add(packet, target_services):
        return apply_validator_add_mutation(plan, args, packet)
    if intent.get("action") == "retire" and mutation_apply_is_supported_validator_retire(packet, target_services):
        return apply_validator_retire_mutation(plan, args, packet)
    if intent.get("action") == "retire":
        return apply_rpc_retire_mutation(plan, args, packet)
    return apply_rpc_add_mutation(plan, args, packet)



def mutate_network(plan: NetworkPlan, args: argparse.Namespace) -> dict[str, Any]:
    packet = build_mutation_packet(plan, args)
    write_mutation_packet_if_requested(packet, args)
    if bool(getattr(args, "apply", False)):
        return apply_mutation(plan, args, packet)
    return packet


def render_rpc_public_entry_dynamic_config(
    plan: NetworkPlan,
    host_id: str,
    *,
    config_export: Mapping[str, Any] | None = None,
) -> str:
    host_id = safe_id(host_id, kind="host")
    hosts = host_by_id(plan)
    if host_id not in hosts:
        raise PlanError(f"Unknown host for RPC public-entry rendering: {host_id}")
    hostname = canonical_rpc_hostname(plan)
    if not hostname:
        raise PlanError("RPC public-entry sidecar requires networks.<network>.rpc / external_rpc_url.")
    local_rpcs = local_rpc_entry_services(plan, host_id)
    if not local_rpcs:
        raise PlanError(f"No rpc-role services are assigned to host {host_id!r}.")
    rid = traefik_router_id(f"{plan.name}-{hostname}")
    middleware_prefix = traefik_router_id(f"{plan.name}-rpc-public-entry")
    service_name = f"{rid}-service"
    export_enabled = bool(config_export)
    export_token = str((config_export or {}).get("token") or "").strip()
    export_port = int((config_export or {}).get("port") or DEFAULT_QBFT_CONFIG_EXPORT_PORT)
    export_service_name = f"{rid}-config-export-service"
    export_router_id = f"{rid}-config-export-http"
    export_https_router_id = f"{rid}-config-export-https"
    export_path = qbft_config_export_public_path(export_token) if export_enabled else ""
    lines: list[str] = [
        "# Generated by tools/coolify_qbft_network.py RPC public-entry sidecar.",
        "# Do not edit this file by hand; rerun the QBFT deployer instead.",
        "http:",
        "  middlewares:",
        f"    {middleware_prefix}-redirect-to-https:",
        "      redirectScheme:",
        "        scheme: https",
        "  routers:",
    ]
    if export_enabled:
        lines.extend(
            [
                f"    {export_router_id}:",
                "      entryPoints:",
                "        - http",
                f"      rule: {yaml_quote(f'Host(`{hostname}`) && Path(`{export_path}`)')}",
                "      priority: 10000",
                f"      service: {export_service_name}",
                f"    {export_https_router_id}:",
                "      entryPoints:",
                "        - https",
                f"      rule: {yaml_quote(f'Host(`{hostname}`) && Path(`{export_path}`)')}",
                "      priority: 10000",
                f"      service: {export_service_name}",
                "      tls:",
                "        certResolver: letsencrypt",
            ]
        )
    lines.extend(
        [
            f"    {rid}-http:",
            "      entryPoints:",
            "        - http",
            f"      rule: {yaml_quote(f'Host(`{hostname}`)')}",
            "      service: noop@internal",
            "      middlewares:",
            f"        - {middleware_prefix}-redirect-to-https",
            f"    {rid}-https:",
            "      entryPoints:",
            "        - https",
            f"      rule: {yaml_quote(f'Host(`{hostname}`)')}",
            f"      service: {service_name}",
            "      tls:",
            "        certResolver: letsencrypt",
            "  services:",
        ]
    )
    if export_enabled:
        host = hosts[host_id]
        lines.extend(
            [
                f"    {export_service_name}:",
                "      loadBalancer:",
                "        passHostHeader: true",
                "        servers:",
                f"          - url: {yaml_quote(f'http://{host.address}:{export_port}')}",
            ]
        )
    lines.extend(
        [
            f"    {service_name}:",
            "      loadBalancer:",
            "        passHostHeader: true",
            "        servers:",
        ]
    )
    host = hosts[host_id]
    for service in local_rpcs:
        lines.append(f"          - url: {yaml_quote(rpc_public_entry_backend_url(plan, host, service))}")
    return "\n".join(lines) + "\n"



def render_rpc_public_entry_writer_script(
    plan: NetworkPlan,
    host_id: str,
    *,
    config_export: Mapping[str, Any] | None = None,
) -> str:
    config_path = rpc_public_entry_dynamic_config_path(plan, host_id)
    config_tmp_path = f"{config_path}.tmp"
    config = render_rpc_public_entry_dynamic_config(plan, host_id, config_export=config_export).rstrip("\n")
    refresh_s = max(30, int(TRAEFIK_DYNAMIC_CONFIG_REFRESH_S))
    return "\n".join(
        [
            "set -eu",
            "write_config() {",
            f"  mkdir -p {shell_single_quote(TRAEFIK_DYNAMIC_CONFIG_DIR)}",
            f"  cat > {shell_single_quote(config_tmp_path)} <<'TRAEFIKDYNAMICCONFIG'",
            config,
            "TRAEFIKDYNAMICCONFIG",
            f"  mv {shell_single_quote(config_tmp_path)} {shell_single_quote(config_path)}",
            f"  echo {shell_single_quote(f'Installed RPC Traefik dynamic config: {config_path}')}",
            "}",
            "write_config",
            f"while true; do sleep {refresh_s}; write_config; done",
        ]
    )


def render_rpc_public_entry_cleanup_script(plan: NetworkPlan, host_id: str) -> str:
    config_path = rpc_public_entry_dynamic_config_path(plan, host_id)
    refresh_s = max(30, int(TRAEFIK_DYNAMIC_CONFIG_REFRESH_S))
    return "\n".join(
        [
            "set -eu",
            f"rm -f {shell_single_quote(config_path)}",
            f"echo {shell_single_quote(f'Removed stale RPC Traefik dynamic config: {config_path}')}",
            f"while true; do sleep {refresh_s}; rm -f {shell_single_quote(config_path)}; done",
        ]
    )


def append_rpc_public_entry_sidecar(
    lines: list[str],
    plan: NetworkPlan,
    host_id: str,
    *,
    config_export: Mapping[str, Any] | None = None,
) -> None:
    if not rpc_public_entry_enabled(plan):
        return

    host_id = safe_id(host_id, kind="host")
    local_rpcs = local_rpc_entry_services(plan, host_id)
    config_path = rpc_public_entry_dynamic_config_path(plan, host_id)
    hostname = canonical_rpc_hostname(plan)
    service_key = rpc_public_entry_service_key(plan, host_id)
    if local_rpcs:
        installer_script = render_rpc_public_entry_writer_script(plan, host_id, config_export=config_export)
        first_backend = rpc_public_entry_backend_url(plan, host_by_id(plan)[host_id], local_rpcs[0])
        healthcheck = (
            f"test -s {shlex.quote(config_path)} "
            f"&& grep -Fq -- {shlex.quote(hostname)} {shlex.quote(config_path)} "
            f"&& grep -Fq -- {shlex.quote(first_backend)} {shlex.quote(config_path)}"
        )
    else:
        installer_script = render_rpc_public_entry_cleanup_script(plan, host_id)
        healthcheck = f"test ! -e {shlex.quote(config_path)}"

    lines.extend(
        [
            f"  {service_key}:",
            f"    image: {yaml_quote(TRAEFIK_DYNAMIC_CONFIG_IMAGE)}",
            "    init: true",
            "    restart: unless-stopped",
            "    labels:",
            "      - \"traefik.enable=false\"",
            "    volumes:",
            f"      - {yaml_quote(f'{TRAEFIK_DYNAMIC_CONFIG_DIR}:{TRAEFIK_DYNAMIC_CONFIG_DIR}')}",
            "    command:",
            "      - /bin/sh",
            "      - -euc",
            "      - |-",
            *[f"        {line}" if line else "" for line in installer_script.splitlines()],
            "    healthcheck:",
            f'      test: ["CMD-SHELL", {yaml_quote(healthcheck)}]',
            "      interval: 30s",
            "      timeout: 5s",
            "      start_period: 10s",
            "      retries: 5",
            "",
        ]
    )


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


def legacy_managed_volume_name(plan: NetworkPlan) -> str:
    """Return the pre-host-split runtime volume name used by early testnet deploys."""

    return safe_id(f"{plan.compose_project}-runtime", kind="volume")


def coolify_prefixed_volume_name(prefix: str, volume_name: str) -> str:
    """Return Coolify's compose-project-prefixed named volume spelling.

    Coolify deploys service compose files under the service resource UUID as the
    compose project.  Even when the rendered volume declares ``name:
    main-computer-qbft-...-runtime``, Docker may retain already-created runtime
    material under ``<service_uuid>_<volume_name>`` from earlier deploys.  The
    config exporter must try those live prefixed volumes before an unprefixed
    name, otherwise Docker Compose can create a fresh empty lookalike and the
    slurp fails even though the real runtime config exists on the host.
    """

    prefix = str(prefix or "").strip()
    volume_name = str(volume_name or "").strip()
    if not prefix or not volume_name:
        return ""
    if not SAFE_ID_RE.match(prefix):
        raise PlanError(f"Unsafe Coolify volume prefix: {prefix!r}")
    return f"{prefix}_{volume_name}"


def validator_static_enode_endpoint(service: PlannedService) -> str:
    """Return the peer address other hosts should use for a validator enode.

    Container IPs only work inside one Docker bridge on one host.  Remote hosts
    must use the Coolify host's reachable address and the published P2P port.
    A validator without a published P2P port falls back to the container address
    for local/single-host deployments.
    """

    if service.p2p_host_port is not None and service.p2p_advertise:
        return service.p2p_advertise
    return f"{service.container_ip}:{P2P_CONTAINER_PORT}"


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
    enode_names = [f"ENODE_{index}" for index in range(1, len(validators) + 1)]

    refresh_lines: list[str] = [
        "normalize_pubkey() {",
        "  pub=$(tr -d '\\r\\n ' < \"$1\")",
        "  pub=${pub#0x}",
        "  case \"$pub\" in 04*) if [ \"${#pub}\" -eq 130 ]; then pub=${pub#04}; fi;; esac",
        "  case \"$pub\" in (*[!0-9a-fA-F]*|'') echo \"invalid public key in $1\" >&2; exit 25;; esac",
        "  if [ \"${#pub}\" -ne 128 ]; then echo \"expected 128 hex chars in $1, got ${#pub}\" >&2; exit 26; fi",
        "  printf '%s' \"$pub\"",
        "}",
        "refresh_static_runtime_config() {",
    ]
    for index, service in enumerate(validators, start=1):
        refresh_lines.extend(
            [
                f"  [ -f /smoke/{service.id}/data/key.pub ] || {{ echo \"missing validator public key for static enode refresh: /smoke/{service.id}/data/key.pub\" >&2; exit 27; }}",
                f"  PUB_{index}=$(normalize_pubkey /smoke/{service.id}/data/key.pub)",
                f"  ENODE_{index}=\"enode://${{PUB_{index}}}@{validator_static_enode_endpoint(service)}\"",
            ]
        )
    refresh_lines.extend(f"  {line}" for line in render_static_nodes_shell(enode_names, "/smoke/static-nodes-all.json"))
    for index, service in enumerate(validators, start=1):
        refresh_lines.extend(f"  {line}" for line in render_static_nodes_shell(enode_names, f"/smoke/{service.id}/static-nodes.json", exclude=f"ENODE_{index}"))
    for service in rpc_nodes:
        runtime_dir = service_runtime_dir(service)
        refresh_lines.append(f"  mkdir -p /smoke/{runtime_dir}/data")
        refresh_lines.extend(f"  {line}" for line in render_static_nodes_shell(enode_names, f"/smoke/{runtime_dir}/static-nodes.json"))
    refresh_lines.extend(
        [
            "  cat > /smoke/network-metadata.json <<JSON",
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
        refresh_lines.append(
            f"    {{\"id\": \"{service.id}\", \"ip\": \"{service.container_ip}\", \"p2p\": \"{validator_static_enode_endpoint(service)}\", \"enode\": \"$ENODE_{index}\"}}{comma}"
        )
    refresh_lines.extend(
        [
            "  ],",
            "  \"rpc_nodes\": [",
        ]
    )
    for index, service in enumerate(rpc_nodes, start=1):
        comma = "," if index < len(rpc_nodes) else ""
        refresh_lines.append(f"    {{\"id\": \"{service.id}\", \"ip\": \"{service.container_ip}\"}}{comma}")
    refresh_lines.extend(
        [
            "  ]",
            "}",
            "JSON",
            "}",
        ]
    )

    lines: list[str] = [
        "set -eu",
        "echo \"Main Computer QBFT bootstrap starting for " + plan.name + "\"",
        "mkdir -p /smoke",
        f"EXPECTED_QBFT_VALIDATOR_COUNT={len(validators)}",
        f"EXPECTED_QBFT_RPC_COUNT={len(rpc_nodes)}",
        "REGENERATE_QBFT_TOPOLOGY=false",
        *refresh_lines,
        "if [ -f /smoke/network-metadata.json ] && [ \"${QBFT_RESET_CHAIN:-false}\" != \"true\" ]; then",
        "  EXISTING_QBFT_VALIDATOR_COUNT=$(grep -c '\"id\": \"validator-' /smoke/network-metadata.json || true)",
        "  EXISTING_QBFT_RPC_COUNT=$(grep -c '\"id\": \"rpc-' /smoke/network-metadata.json || true)",
        "  EXISTING_QBFT_METADATA_MATCH=false",
        "  if grep -q '\"network\": \"" + plan.name + "\"' /smoke/network-metadata.json && grep -q '\"docker_network\": \"" + plan.docker_network + "\"' /smoke/network-metadata.json && grep -q '\"docker_subnet\": \"" + plan.docker_subnet + "\"' /smoke/network-metadata.json; then",
        "    EXISTING_QBFT_METADATA_MATCH=true",
        "  fi",
        "  if [ \"$EXISTING_QBFT_METADATA_MATCH\" = \"true\" ] && [ \"$EXISTING_QBFT_VALIDATOR_COUNT\" = \"$EXPECTED_QBFT_VALIDATOR_COUNT\" ] && [ \"$EXISTING_QBFT_RPC_COUNT\" = \"$EXPECTED_QBFT_RPC_COUNT\" ]; then",
        "    echo \"Existing QBFT network metadata matches desired topology; refreshing static enodes and reusing persistent genesis/key material.\"",
        "    refresh_static_runtime_config",
        "    exit 0",
        "  fi",
        "  if [ \"$EXISTING_QBFT_METADATA_MATCH\" = \"true\" ] && [ \"$EXISTING_QBFT_VALIDATOR_COUNT\" = \"$EXPECTED_QBFT_VALIDATOR_COUNT\" ] && [ \"$EXISTING_QBFT_RPC_COUNT\" = \"0\" ] && [ \"$EXPECTED_QBFT_RPC_COUNT\" != \"0\" ] && [ -f /smoke/genesis.json ] && [ -f /smoke/static-nodes-all.json ]; then",
        "    echo \"Existing validator topology matches; adding dedicated RPC runtime material without regenerating validator keys.\"",
        "    refresh_static_runtime_config",
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
    for index, _service in enumerate(validators, start=1):
        lines.append(f"KEY_DIR_{index}=\"${index}\"")

    for index, service in enumerate(validators, start=1):
        lines.extend(
            [
                f"mkdir -p /smoke/{service.id}/data",
                f"cp \"$KEY_DIR_{index}/key\" /smoke/{service.id}/data/key",
                f"cp \"$KEY_DIR_{index}/key.pub\" /smoke/{service.id}/data/key.pub",
                f"\"$BESU\" --data-path=/smoke/{service.id}/data public-key export-address --to=/smoke/{service.id}/validator-address.tmp",
                f"tr -d '\\r\\n ' < /smoke/{service.id}/validator-address.tmp > /smoke/{service.id}/validator-address",
                f"rm -f /smoke/{service.id}/validator-address.tmp",
            ]
        )

    lines.extend(
        [
            "cp /tmp/qbft-networkFiles/genesis.json /smoke/genesis.json",
            "refresh_static_runtime_config",
            "echo \"Main Computer QBFT bootstrap complete.\"",
        ]
    )
    return "\n".join(lines)

def service_should_consider_p2p_host_port_for_collision(role: str) -> bool:
    return role == "validator"


def service_should_publish_p2p(plan: NetworkPlan, service: PlannedService) -> bool:
    del plan
    return service.role == "validator" and service.p2p_host_port is not None


def service_should_publish_rpc(plan: NetworkPlan, service: PlannedService) -> bool:
    del plan
    return service.rpc_host_port is not None and service.rpc_bind_host != ""


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


QBFT_RUNTIME_CONFIG_FILES = {
    "genesis_json": "/smoke/genesis.json",
    "static_nodes_all_json": "/smoke/static-nodes-all.json",
    "network_metadata_json": "/smoke/network-metadata.json",
}


def qbft_config_export_service_name(plan: NetworkPlan, host_id: str) -> str:
    return safe_id(f"{plan.compose_project}-{host_id}-config-export", kind="service")


def qbft_config_export_token() -> str:
    return secrets.token_urlsafe(18).replace("-", "").replace("_", "")


def qbft_config_export_port(args: argparse.Namespace) -> int:
    return require_int(
        getattr(args, "config_export_port", DEFAULT_QBFT_CONFIG_EXPORT_PORT),
        name="config_export_port",
        minimum=1024,
        maximum=65535,
    )


def qbft_config_export_timeout_s(args: argparse.Namespace) -> float:
    value = getattr(args, "config_export_timeout_s", DEFAULT_QBFT_CONFIG_EXPORT_TIMEOUT_S)
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise PlanError(f"config_export_timeout_s must be a number of seconds, got {value!r}") from exc
    if timeout <= 0:
        raise PlanError("config_export_timeout_s must be greater than 0.")
    return timeout


def qbft_config_export_url(host: PlannedHost, *, port: int, token: str) -> str:
    if not host.address:
        raise PlanError(f"Host {host.id!r} does not have a public address for QBFT config export.")
    return f"http://{host.address}:{port}/{token}.json"


def qbft_config_export_public_path(token: str) -> str:
    token = str(token or "").strip().strip("/")
    if not token or "/" in token or ".." in token:
        raise PlanError("QBFT config export token is invalid.")
    return f"{QBFT_CONFIG_EXPORT_PUBLIC_PATH_PREFIX}/{token}.json"


def qbft_config_export_public_entry_url(host: PlannedHost, *, token: str) -> str:
    if not host.address:
        raise PlanError(f"Host {host.id!r} does not have a public address for QBFT config export.")
    return f"http://{host.address}{qbft_config_export_public_path(token)}"


def qbft_config_export_public_entry_fetch_candidates(plan: NetworkPlan, host: PlannedHost, *, token: str) -> list[dict[str, Any]]:
    """Return operator fetch URLs for the temporary public-entry export path.

    Prefer HTTPS to the source host address with the canonical Host header.  This
    still flows through Coolify/Traefik, but it does not depend on plain HTTP
    being open on the machine.  Certificate verification is disabled for that
    IP-address candidate because the certificate is issued for the canonical
    hostname, not the raw host IP.
    """

    path = qbft_config_export_public_path(token)
    hostname = canonical_rpc_hostname(plan)
    candidates: list[dict[str, Any]] = []
    if host.address:
        candidates.append(
            {
                "url": f"https://{host.address}{path}",
                "headers": {"Host": hostname},
                "insecure_https": True,
                "label": "source-host-https-ip-with-host-header",
            }
        )
    if hostname:
        candidates.append(
            {
                "url": f"https://{hostname}{path}",
                "headers": {},
                "insecure_https": False,
                "label": "canonical-https-hostname",
            }
        )
    if host.address:
        candidates.append(
            {
                "url": f"http://{host.address}{path}",
                "headers": {"Host": hostname},
                "insecure_https": False,
                "label": "source-host-http-ip-with-host-header",
            }
        )
    if hostname:
        candidates.append(
            {
                "url": f"http://{hostname}{path}",
                "headers": {},
                "insecure_https": False,
                "label": "canonical-http-hostname",
            }
        )
    return candidates


def qbft_config_export_transport(args: argparse.Namespace) -> str:
    raw = str(getattr(args, "config_export_transport", DEFAULT_QBFT_CONFIG_EXPORT_TRANSPORT) or DEFAULT_QBFT_CONFIG_EXPORT_TRANSPORT).strip().lower()
    if raw not in {"public-entry", "direct-port"}:
        raise PlanError("--config-export-transport must be one of: public-entry, direct-port")
    return raw


def docker_volume_data_path(volume_name: str) -> str:
    """Return the Docker host path for a local named volume's data directory."""

    volume_name = str(volume_name or "").strip()
    if not volume_name or "/" in volume_name or "\\" in volume_name or volume_name in {".", ".."}:
        raise PlanError(f"Unsafe Docker volume name for host bind: {volume_name!r}")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", volume_name):
        raise PlanError(f"Unsafe Docker volume name for host bind: {volume_name!r}")
    return f"/var/lib/docker/volumes/{volume_name}/_data"


def qbft_config_export_mount_sources(
    plan: NetworkPlan,
    host_id: str,
    *,
    volume_prefixes: list[str] | tuple[str, ...] = (),
) -> list[dict[str, str]]:
    """Return ordered host-local places that may contain current QBFT runtime material.

    Coolify stores service volumes as Docker named volumes prefixed with the
    source service UUID, for example
    ``pr243..._main-computer-qbft-testnet-a-runtime``.  A separate Coolify
    service cannot reliably mount that name as a Compose named volume because
    Coolify rewrites service volume sources under the config-export project,
    creating empty shadow volumes.  For discovered source-service volumes,
    bind-mount the Docker volume data path read-only instead.  That keeps the
    slurp fatal when the source volume is genuinely absent, without creating an
    empty lookalike volume.
    """

    host_id = safe_id(host_id, kind="host")
    hosts = host_by_id(plan)
    if host_id not in hosts:
        raise PlanError(f"Unknown host for config export: {host_id}")
    host = hosts[host_id]
    candidates: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, label: str, source: str, target: str, *, display_source: str | None = None) -> None:
        source = str(source or "").strip()
        if not source:
            return
        key = (kind, source)
        if key in seen:
            return
        seen.add(key)
        item = {"kind": kind, "label": label, "source": source, "target": target}
        if display_source:
            item["display_source"] = str(display_source)
        candidates.append(item)

    managed = managed_volume_name(plan, host_id)
    prefixes: list[str] = []
    for raw_prefix in [*list(volume_prefixes or ()), host.service_uuid]:
        prefix = str(raw_prefix or "").strip()
        if not prefix or prefix in prefixes:
            continue
        if not SAFE_ID_RE.match(prefix):
            raise PlanError(f"Unsafe Coolify volume prefix: {prefix!r}")
        prefixes.append(prefix)

    for index, prefix in enumerate(prefixes, start=1):
        volume_name = coolify_prefixed_volume_name(prefix, managed)
        add(
            "bind-existing",
            f"coolify-prefixed-managed-host-volume-{index}",
            docker_volume_data_path(volume_name),
            f"/sources/coolify-prefixed-managed-host-volume-{index}",
            display_source=volume_name,
        )

    add("bind", "host-runtime-root", host.runtime_root, "/sources/host-runtime-root")
    return candidates

def qbft_config_export_dynamic_config_path(plan: NetworkPlan, host_id: str) -> str:
    host_id = safe_id(host_id, kind="host")
    filename = safe_id(f"{plan.compose_project}-{host_id}-qbft-config-export", kind="dynamic_config")
    return f"{TRAEFIK_DYNAMIC_CONFIG_DIR}/{filename}.yml"


def render_qbft_config_export_public_entry_dynamic_config(plan: NetworkPlan, host_id: str, *, token: str, port: int) -> str:
    host_id = safe_id(host_id, kind="host")
    hosts = host_by_id(plan)
    if host_id not in hosts:
        raise PlanError(f"Unknown host for QBFT config export public-entry rendering: {host_id}")
    hostname = canonical_rpc_hostname(plan)
    if not hostname:
        raise PlanError("QBFT config export public-entry requires networks.<network>.rpc / external_rpc_url.")
    export_path = qbft_config_export_public_path(token)
    rid = traefik_router_id(f"{plan.name}-{hostname}-qbft-config-export")
    service_name = f"{rid}-service"
    host = hosts[host_id]
    return "\n".join(
        [
            "# Generated by tools/coolify_qbft_network.py temporary QBFT config export sidecar.",
            "# Non-secret runtime config only. Remove by redeploying the cleanup compose.",
            "http:",
            "  routers:",
            f"    {rid}-http:",
            "      entryPoints:",
            "        - http",
            f"      rule: {yaml_quote(f'Host(`{hostname}`) && Path(`{export_path}`)')}",
            "      priority: 10000",
            f"      service: {service_name}",
            f"    {rid}-https:",
            "      entryPoints:",
            "        - https",
            f"      rule: {yaml_quote(f'Host(`{hostname}`) && Path(`{export_path}`)')}",
            "      priority: 10000",
            f"      service: {service_name}",
            "      tls:",
            "        certResolver: letsencrypt",
            "  services:",
            f"    {service_name}:",
            "      loadBalancer:",
            "        passHostHeader: true",
            "        servers:",
            f"          - url: {yaml_quote(f'http://{host.address}:{port}')}",
            "",
        ]
    )


def render_qbft_config_export_public_entry_writer_script(plan: NetworkPlan, host_id: str, *, token: str, port: int) -> str:
    config_path = qbft_config_export_dynamic_config_path(plan, host_id)
    config_tmp_path = f"{config_path}.tmp"
    config = render_qbft_config_export_public_entry_dynamic_config(plan, host_id, token=token, port=port).rstrip("\n")
    refresh_s = max(30, int(TRAEFIK_DYNAMIC_CONFIG_REFRESH_S))
    return "\n".join(
        [
            "set -eu",
            "write_config() {",
            f"  mkdir -p {shell_single_quote(TRAEFIK_DYNAMIC_CONFIG_DIR)}",
            f"  cat > {shell_single_quote(config_tmp_path)} <<'TRAEFIKDYNAMICCONFIG'",
            config,
            "TRAEFIKDYNAMICCONFIG",
            f"  mv {shell_single_quote(config_tmp_path)} {shell_single_quote(config_path)}",
            f"  echo {shell_single_quote(f'Installed temporary QBFT config export Traefik dynamic config: {config_path}')}",
            "}",
            "write_config",
            f"while true; do sleep {refresh_s}; write_config; done",
        ]
    )


def render_qbft_config_export_public_entry_cleanup_script(plan: NetworkPlan, host_id: str) -> str:
    config_path = qbft_config_export_dynamic_config_path(plan, host_id)
    refresh_s = max(30, int(TRAEFIK_DYNAMIC_CONFIG_REFRESH_S))
    return "\n".join(
        [
            "set -eu",
            f"rm -f {shell_single_quote(config_path)}",
            f"echo {shell_single_quote(f'Removed temporary QBFT config export Traefik dynamic config: {config_path}')}",
            f"while true; do sleep {refresh_s}; rm -f {shell_single_quote(config_path)}; done",
        ]
    )


def render_qbft_config_exporter_shell(
    plan: NetworkPlan,
    host_id: str,
    config_export: Mapping[str, Any],
) -> str:
    """Render shell for a Coolify-native config exporter sidecar.

    The sidecar is deployed inside the source host's normal QBFT service stack
    and exposed through that host's generated RPC public-entry Traefik config.
    It scans the same candidate runtime sources as the standalone exporter and
    serves only non-secret runtime material.
    """

    token = str(config_export.get("token") or "").strip()
    if not token:
        raise PlanError("QBFT config exporter sidecar requires a token.")
    source_payload = [
        {"label": item["label"], "path": item["target"], "kind": item["kind"], "source": item.get("display_source", item["source"])}
        for item in qbft_config_export_mount_sources(
            plan,
            host_id,
            volume_prefixes=list(config_export.get("volume_prefixes") or ()),
        )
    ]
    lines: list[str] = [
        "set -eu",
        "mkdir -p /serve",
        f"TOKEN={shlex.quote(token)}",
        f"PUBLIC_EXPORT_PATH={shlex.quote(qbft_config_export_public_path(token))}",
        f"NETWORK={shlex.quote(plan.name)}",
        f"HOST_ID={shlex.quote(host_id)}",
        "json_escape() {",
        "  sed -e 's/\\\\/\\\\\\\\/g' -e 's/\"/\\\\\"/g' -e ':a;N;$!ba;s/\\n/\\\\n/g'",
        "}",
        "json_string() { printf '%s' \"$1\" | json_escape; }",
        "file_b64() {",
        "  if base64 --help 2>/dev/null | grep -q -- '-w'; then base64 -w 0 \"$1\"; else base64 \"$1\" | tr -d '\\n'; fi",
        "}",
        "file_sha256() { sha256sum \"$1\" | awk '{print $1}'; }",
        "SOURCE_COUNT=0",
    ]
    for index, source in enumerate(source_payload):
        lines.extend(
            [
                f"SRC_LABEL_{index}={shlex.quote(str(source.get('label') or ''))}",
                f"SRC_PATH_{index}={shlex.quote(str(source.get('path') or ''))}",
                f"SRC_KIND_{index}={shlex.quote(str(source.get('kind') or ''))}",
                f"SRC_SOURCE_{index}={shlex.quote(str(source.get('source') or ''))}",
                f"SOURCE_COUNT={index + 1}",
            ]
        )
    lines.extend(
        [
            "OK=false",
            "SOURCE_MOUNT=",
            "MISSING_JSON=",
            "GENESIS_B64=",
            "STATIC_B64=",
            "METADATA_B64=",
            "GENESIS_SHA=",
            "STATIC_SHA=",
            "METADATA_SHA=",
            "VALIDATOR_ADDRESSES_JSON=",
            "VALIDATOR_PUBLIC_KEYS_JSON=",
            "i=0",
            "while [ \"$i\" -lt \"$SOURCE_COUNT\" ]; do",
            "  eval label=\"\\${SRC_LABEL_$i}\"",
            "  eval root=\"\\${SRC_PATH_$i}\"",
            "  missing=\"\"",
            "  [ -f \"$root/genesis.json\" ] || missing=\"$missing /smoke/genesis.json\"",
            "  [ -f \"$root/static-nodes-all.json\" ] || missing=\"$missing /smoke/static-nodes-all.json\"",
            "  [ -f \"$root/network-metadata.json\" ] || missing=\"$missing /smoke/network-metadata.json\"",
            "  if [ -z \"$missing\" ]; then",
            "    OK=true",
            "    SOURCE_MOUNT=\"$label\"",
            "    GENESIS_B64=$(file_b64 \"$root/genesis.json\")",
            "    STATIC_B64=$(file_b64 \"$root/static-nodes-all.json\")",
            "    METADATA_B64=$(file_b64 \"$root/network-metadata.json\")",
            "    GENESIS_SHA=$(file_sha256 \"$root/genesis.json\")",
            "    STATIC_SHA=$(file_sha256 \"$root/static-nodes-all.json\")",
            "    METADATA_SHA=$(file_sha256 \"$root/network-metadata.json\")",
            "    VALIDATOR_ADDRESSES_JSON=",
            "    for addr_file in \"$root\"/*/validator-address; do",
            "      [ -f \"$addr_file\" ] || continue",
            "      runtime_dir=$(basename \"$(dirname \"$addr_file\")\")",
            "      runtime_root=$(dirname \"$addr_file\")",
            "      addr=$(tr -d '\\r\\n ' < \"$addr_file\")",
            "      case \"$addr\" in 0x*) ;; *) addr=\"0x$addr\" ;; esac",
            "      if printf '%s' \"$addr\" | grep -Eq '^0x[0-9a-fA-F]{40}$'; then",
            "        key_esc=$(json_string \"$runtime_dir\")",
            "        addr_esc=$(json_string \"$addr\")",
            "        if [ -z \"$VALIDATOR_ADDRESSES_JSON\" ]; then VALIDATOR_ADDRESSES_JSON=\"\\\"$key_esc\\\":\\\"$addr_esc\\\"\"; else VALIDATOR_ADDRESSES_JSON=\"$VALIDATOR_ADDRESSES_JSON,\\\"$key_esc\\\":\\\"$addr_esc\\\"\"; fi",
            "      fi",
            "      if [ -f \"$runtime_root/data/key.pub\" ]; then",
            "        pub=$(tr -d '\\r\\n ' < \"$runtime_root/data/key.pub\")",
            "        pub=${pub#0x}",
            "        case \"$pub\" in 04*) if [ \"${#pub}\" -eq 130 ]; then pub=${pub#04}; fi;; esac",
            "        if printf '%s' \"$pub\" | grep -Eq '^[0-9a-fA-F]{128}$'; then",
            "          key_esc=$(json_string \"$runtime_dir\")",
            "          pub_esc=$(json_string \"$pub\")",
            "          if [ -z \"$VALIDATOR_PUBLIC_KEYS_JSON\" ]; then VALIDATOR_PUBLIC_KEYS_JSON=\"\\\"$key_esc\\\":\\\"$pub_esc\\\"\"; else VALIDATOR_PUBLIC_KEYS_JSON=\"$VALIDATOR_PUBLIC_KEYS_JSON,\\\"$key_esc\\\":\\\"$pub_esc\\\"\"; fi",
            "        fi",
            "      fi",
            "    done",
            "    break",
            "  fi",
            "  for item in $missing; do",
            "    entry=\"$label:$item\"",
            "    esc=$(json_string \"$entry\")",
            "    if [ -z \"$MISSING_JSON\" ]; then MISSING_JSON=\"\\\"$esc\\\"\"; else MISSING_JSON=\"$MISSING_JSON,\\\"$esc\\\"\"; fi",
            "  done",
            "  i=$((i + 1))",
            "done",
            "source_mount_esc=$(json_string \"$SOURCE_MOUNT\")",
            "EXPORT_FILE=\"/serve/$TOKEN.json\"",
            "PUBLIC_EXPORT_FILE=\"/serve$PUBLIC_EXPORT_PATH\"",
            "cat > \"$EXPORT_FILE\" <<JSON",
            "{",
            "  \"ok\": $OK,",
            "  \"network\": \"$(json_string \"$NETWORK\")\",",
            "  \"host\": \"$(json_string \"$HOST_ID\")\",",
            "  \"source_mount\": \"$source_mount_esc\",",
            "  \"missing_files\": [$MISSING_JSON],",
            "  \"files_b64\": {",
            "    \"genesis_json\": \"$GENESIS_B64\",",
            "    \"static_nodes_all_json\": \"$STATIC_B64\",",
            "    \"network_metadata_json\": \"$METADATA_B64\"",
            "  },",
            "  \"sha256\": {",
            "    \"genesis_json\": \"$GENESIS_SHA\",",
            "    \"static_nodes_all_json\": \"$STATIC_SHA\",",
            "    \"network_metadata_json\": \"$METADATA_SHA\"",
            "  },",
            "  \"validator_addresses\": {$VALIDATOR_ADDRESSES_JSON},",
            "  \"validator_public_keys\": {$VALIDATOR_PUBLIC_KEYS_JSON}",
            "}",
            "JSON",
            "mkdir -p \"$(dirname \"$PUBLIC_EXPORT_FILE\")\"",
            "cp -f \"$EXPORT_FILE\" \"$PUBLIC_EXPORT_FILE\"",
            "printf '%s\\n' \"{\\\"ok\\\":$OK,\\\"host\\\":\\\"$HOST_ID\\\",\\\"source_mount\\\":\\\"$source_mount_esc\\\",\\\"missing_files\\\":[$MISSING_JSON]}\"",
            "cd /serve",
            "exec python3 -m http.server 8080 --bind 0.0.0.0",
        ]
    )
    return "\n".join(lines)


def render_qbft_config_exporter_compose(
    plan: NetworkPlan,
    host_id: str,
    *,
    token: str,
    port: int,
    public_entry: bool = False,
    volume_prefixes: list[str] | tuple[str, ...] = (),
) -> str:
    """Render a temporary config-export service for existing host runtime material.

    The exporter exposes only non-secret QBFT runtime material: genesis,
    static-nodes-all, and network metadata.  Validator key files are never read
    or included.  The URL path includes a per-run token so accidental probes
    do not receive the bundle.

    The service scans discovered Coolify-prefixed runtime volumes, the
    current host-specific volume, the old hostless single-host volume, and the
    configured bind runtime root.  Missing candidates are reported as
    empty/uninitialized instead of failing the export service deployment.  It
    uses a tiny Python image so the generated shell can write the JSON bundle
    and then serve it with ``python3 -m http.server``.
    """

    host_id = safe_id(host_id, kind="host")
    if host_id not in host_by_id(plan):
        raise PlanError(f"Unknown host for config export: {host_id}")
    sources = qbft_config_export_mount_sources(plan, host_id, volume_prefixes=volume_prefixes)
    source_payload = [
        {"label": item["label"], "path": item["target"], "kind": item["kind"], "source": item.get("display_source", item["source"])}
        for item in sources
    ]
    payload_script = "\n".join(
        [
            "set -eu",
            "mkdir -p /serve",
            f"TOKEN={shlex.quote(token)}",
            f"PUBLIC_EXPORT_PATH={shlex.quote(qbft_config_export_public_path(token))}",
            f"NETWORK={shlex.quote(plan.name)}",
            f"HOST_ID={shlex.quote(host_id)}",
            f"SOURCES_JSON={shlex.quote(json.dumps(source_payload, sort_keys=True))}",
            "json_escape() {",
            "  sed -e 's/\\\\/\\\\\\\\/g' -e 's/\"/\\\\\"/g' -e ':a;N;$!ba;s/\\n/\\\\n/g'",
            "}",
            "json_string() {",
            "  printf '%s' \"$1\" | json_escape",
            "}",
            "file_b64() {",
            "  if base64 --help 2>/dev/null | grep -q -- '-w'; then base64 -w 0 \"$1\"; else base64 \"$1\" | tr -d '\\n'; fi",
            "}",
            "file_sha256() {",
            "  sha256sum \"$1\" | awk '{print $1}'",
            "}",
            "source_field() {",
            "  python3 - <<'PY' \"$SOURCES_JSON\" \"$1\" \"$2\" 2>/dev/null || true",
            "import json, sys",
            "sources = json.loads(sys.argv[1])",
            "idx = int(sys.argv[2])",
            "key = sys.argv[3]",
            "try:",
            "    print(sources[idx].get(key, ''))",
            "except Exception:",
            "    pass",
            "PY",
            "}",
            "# Alpine normally has no Python.  If python3 is absent, fall back to a",
            "# shell-friendly source table generated by the deployer below.",
            "SOURCE_COUNT=0",
        ]
    )
    for index, source in enumerate(source_payload):
        payload_script += "\n" + "\n".join(
            [
                f"SRC_LABEL_{index}={shlex.quote(str(source.get('label') or ''))}",
                f"SRC_PATH_{index}={shlex.quote(str(source.get('path') or ''))}",
                f"SRC_KIND_{index}={shlex.quote(str(source.get('kind') or ''))}",
                f"SRC_SOURCE_{index}={shlex.quote(str(source.get('source') or ''))}",
                f"SOURCE_COUNT={index + 1}",
            ]
        )
    payload_script += "\n" + "\n".join(
        [
            "OK=false",
            "SOURCE_MOUNT=",
            "MISSING_JSON=",
            "GENESIS_B64=",
            "STATIC_B64=",
            "METADATA_B64=",
            "GENESIS_SHA=",
            "STATIC_SHA=",
            "METADATA_SHA=",
            "VALIDATOR_ADDRESSES_JSON=",
            "VALIDATOR_PUBLIC_KEYS_JSON=",
            "RUNTIME_STATIC_NODES_JSON='{}'",
            "i=0",
            "while [ \"$i\" -lt \"$SOURCE_COUNT\" ]; do",
            "  eval label=\"\\${SRC_LABEL_$i}\"",
            "  eval root=\"\\${SRC_PATH_$i}\"",
            "  missing=\"\"",
            "  [ -f \"$root/genesis.json\" ] || missing=\"$missing /smoke/genesis.json\"",
            "  [ -f \"$root/static-nodes-all.json\" ] || missing=\"$missing /smoke/static-nodes-all.json\"",
            "  [ -f \"$root/network-metadata.json\" ] || missing=\"$missing /smoke/network-metadata.json\"",
            "  if [ -z \"$missing\" ]; then",
            "    OK=true",
            "    SOURCE_MOUNT=\"$label\"",
            "    GENESIS_B64=$(file_b64 \"$root/genesis.json\")",
            "    STATIC_B64=$(file_b64 \"$root/static-nodes-all.json\")",
            "    METADATA_B64=$(file_b64 \"$root/network-metadata.json\")",
            "    GENESIS_SHA=$(file_sha256 \"$root/genesis.json\")",
            "    STATIC_SHA=$(file_sha256 \"$root/static-nodes-all.json\")",
            "    METADATA_SHA=$(file_sha256 \"$root/network-metadata.json\")",
            "    VALIDATOR_ADDRESSES_JSON=",
            "    VALIDATOR_PUBLIC_KEYS_JSON=",
            "    for addr_file in \"$root\"/*/validator-address; do",
            "      [ -f \"$addr_file\" ] || continue",
            "      runtime_dir=$(basename \"$(dirname \"$addr_file\")\")",
            "      runtime_root=$(dirname \"$addr_file\")",
            "      addr=$(tr -d '\\r\\n ' < \"$addr_file\")",
            "      case \"$addr\" in 0x*) ;; *) addr=\"0x$addr\" ;; esac",
            "      if printf '%s' \"$addr\" | grep -Eq '^0x[0-9a-fA-F]{40}$'; then",
            "        key_esc=$(json_string \"$runtime_dir\")",
            "        addr_esc=$(json_string \"$addr\")",
            "        if [ -z \"$VALIDATOR_ADDRESSES_JSON\" ]; then VALIDATOR_ADDRESSES_JSON=\"\\\"$key_esc\\\":\\\"$addr_esc\\\"\"; else VALIDATOR_ADDRESSES_JSON=\"$VALIDATOR_ADDRESSES_JSON,\\\"$key_esc\\\":\\\"$addr_esc\\\"\"; fi",
            "      fi",
            "      if [ -f \"$runtime_root/data/key.pub\" ]; then",
            "        pub=$(tr -d '\\r\\n ' < \"$runtime_root/data/key.pub\")",
            "        pub=${pub#0x}",
            "        case \"$pub\" in 04*) if [ \"${#pub}\" -eq 130 ]; then pub=${pub#04}; fi;; esac",
            "        if printf '%s' \"$pub\" | grep -Eq '^[0-9a-fA-F]{128}$'; then",
            "          key_esc=$(json_string \"$runtime_dir\")",
            "          pub_esc=$(json_string \"$pub\")",
            "          if [ -z \"$VALIDATOR_PUBLIC_KEYS_JSON\" ]; then VALIDATOR_PUBLIC_KEYS_JSON=\"\\\"$key_esc\\\":\\\"$pub_esc\\\"\"; else VALIDATOR_PUBLIC_KEYS_JSON=\"$VALIDATOR_PUBLIC_KEYS_JSON,\\\"$key_esc\\\":\\\"$pub_esc\\\"\"; fi",
            "        fi",
            "      fi",
            "    done",
            "    RUNTIME_STATIC_NODES_JSON=$(python3 - \"$root\" <<'PY'",
            "import json, os, sys",
            "root = sys.argv[1]",
            "result = {}",
            "try:",
            "    names = sorted(os.listdir(root))",
            "except OSError:",
            "    names = []",
            "for name in names:",
            "    path = os.path.join(root, name, 'static-nodes.json')",
            "    if not os.path.isfile(path):",
            "        continue",
            "    try:",
            "        with open(path, encoding='utf-8') as fh:",
            "            parsed = json.load(fh)",
            "    except Exception:",
            "        continue",
            "    if isinstance(parsed, list):",
            "        nodes = []",
            "        for item in parsed:",
            "            node = str(item).strip()",
            "            if node.startswith('enode://') and node not in nodes:",
            "                nodes.append(node)",
            "        result[name] = nodes",
            "print(json.dumps(result, sort_keys=True))",
            "PY",
            "    )",
            "    break",
            "  fi",
            "  for item in $missing; do",
            "    entry=\"$label:$item\"",
            "    esc=$(json_string \"$entry\")",
            "    if [ -z \"$MISSING_JSON\" ]; then MISSING_JSON=\"\\\"$esc\\\"\"; else MISSING_JSON=\"$MISSING_JSON,\\\"$esc\\\"\"; fi",
            "  done",
            "  i=$((i + 1))",
            "done",
            "source_mount_esc=$(json_string \"$SOURCE_MOUNT\")",
            "EXPORT_FILE=\"/serve/$TOKEN.json\"",
            "PUBLIC_EXPORT_FILE=\"/serve$PUBLIC_EXPORT_PATH\"",
            "cat > \"$EXPORT_FILE\" <<JSON",
            "{",
            "  \"ok\": $OK,",
            "  \"network\": \"$(json_string \"$NETWORK\")\",",
            "  \"host\": \"$(json_string \"$HOST_ID\")\",",
            "  \"source_mount\": \"$source_mount_esc\",",
            "  \"missing_files\": [$MISSING_JSON],",
            "  \"files_b64\": {",
            "    \"genesis_json\": \"$GENESIS_B64\",",
            "    \"static_nodes_all_json\": \"$STATIC_B64\",",
            "    \"network_metadata_json\": \"$METADATA_B64\"",
            "  },",
            "  \"sha256\": {",
            "    \"genesis_json\": \"$GENESIS_SHA\",",
            "    \"static_nodes_all_json\": \"$STATIC_SHA\",",
            "    \"network_metadata_json\": \"$METADATA_SHA\"",
            "  },",
            "  \"validator_addresses\": {$VALIDATOR_ADDRESSES_JSON},",
            "  \"validator_public_keys\": {$VALIDATOR_PUBLIC_KEYS_JSON},",
            "  \"runtime_static_nodes\": $RUNTIME_STATIC_NODES_JSON",
            "}",
            "JSON",
            "mkdir -p \"$(dirname \"$PUBLIC_EXPORT_FILE\")\"",
            "cp -f \"$EXPORT_FILE\" \"$PUBLIC_EXPORT_FILE\"",
            "printf '%s\\n' \"{\\\"ok\\\":$OK,\\\"host\\\":\\\"$HOST_ID\\\",\\\"source_mount\\\":\\\"$source_mount_esc\\\",\\\"missing_files\\\":[$MISSING_JSON]}\"",
            "cd /serve",
            "exec python3 -m http.server 8080 --bind 0.0.0.0",
        ]
    )
    payload_script = escape_compose_interpolation(payload_script)
    lines = [
        f"name: {plan.compose_project}-{host_id}-config-export",
        "",
        "services:",
        "  qbft-config-export:",
        f"    image: {yaml_quote(QBFT_CONFIG_EXPORT_IMAGE)}",
        "    restart: unless-stopped",
        "    ports:",
        f"      - {yaml_quote(f'0.0.0.0:{port}:8080')}",
        "    volumes:",
    ]
    for source in sources:
        if source["kind"] == "volume":
            lines.append(f"      - {yaml_quote(f'{source['source']}:{source['target']}:ro')}")
        elif source["kind"] in {"bind", "bind-existing"}:
            create_host_path = "false" if source["kind"] == "bind-existing" else "true"
            lines.extend(
                [
                    "      - type: bind",
                    f"        source: {yaml_quote(source['source'])}",
                    f"        target: {source['target']}",
                    "        read_only: true",
                    "        bind:",
                    f"          create_host_path: {create_host_path}",
                ]
            )
    lines.extend(
        [
            "    entrypoint:",
            "      - /bin/sh",
            "      - -ec",
            "      - |-",
            *[f"        {line}" if line else "" for line in payload_script.splitlines()],
            "",
        ]
    )
    if public_entry:
        writer_script = escape_compose_interpolation(
            render_qbft_config_export_public_entry_writer_script(plan, host_id, token=token, port=port)
        )
        healthcheck_path = qbft_config_export_dynamic_config_path(plan, host_id)
        export_path = qbft_config_export_public_path(token)
        lines.extend(
            [
                "  qbft-config-export-public-entry:",
                f"    image: {yaml_quote(TRAEFIK_DYNAMIC_CONFIG_IMAGE)}",
                "    init: true",
                "    restart: unless-stopped",
                "    labels:",
                "      - \"traefik.enable=false\"",
                "    volumes:",
                f"      - {yaml_quote(f'{TRAEFIK_DYNAMIC_CONFIG_DIR}:{TRAEFIK_DYNAMIC_CONFIG_DIR}')}",
                "    command:",
                "      - /bin/sh",
                "      - -euc",
                "      - |-",
                *[f"        {line}" if line else "" for line in writer_script.splitlines()],
                "    healthcheck:",
                f'      test: ["CMD-SHELL", {yaml_quote(f"test -s {shlex.quote(healthcheck_path)} && grep -Fq -- {shlex.quote(export_path)} {shlex.quote(healthcheck_path)}")}]',
                "      interval: 30s",
                "      timeout: 5s",
                "      start_period: 10s",
                "      retries: 5",
                "",
            ]
        )
    volume_sources = [source["source"] for source in sources if source["kind"] == "volume"]
    if volume_sources:
        lines.append("volumes:")
        for volume_name in volume_sources:
            lines.extend(
                [
                    f"  {volume_name}:",
                    f"    name: {volume_name}",
                ]
            )
        lines.append("")
    return "\n".join(lines)


def render_qbft_config_export_cleanup_compose(plan: NetworkPlan, host_id: str) -> str:
    host_id = safe_id(host_id, kind="host")
    cleanup_script = escape_compose_interpolation(render_qbft_config_export_public_entry_cleanup_script(plan, host_id))
    return "\n".join(
        [
            f"name: {plan.compose_project}-{host_id}-config-export",
            "",
            "services:",
            "  qbft-config-export-public-entry-cleanup:",
            f"    image: {yaml_quote(TRAEFIK_DYNAMIC_CONFIG_IMAGE)}",
            "    init: true",
            "    restart: unless-stopped",
            "    labels:",
            "      - \"traefik.enable=false\"",
            "    volumes:",
            f"      - {yaml_quote(f'{TRAEFIK_DYNAMIC_CONFIG_DIR}:{TRAEFIK_DYNAMIC_CONFIG_DIR}')}",
            "    command:",
            "      - /bin/sh",
            "      - -euc",
            "      - |-",
            *[f"        {line}" if line else "" for line in cleanup_script.splitlines()],
            "",
        ]
    )


def normalize_qbft_config_bundle(payload: Mapping[str, Any], *, source_host: str, source_url: str) -> dict[str, Any]:
    files = payload.get("files") if isinstance(payload.get("files"), Mapping) else {}
    files_b64 = payload.get("files_b64") if isinstance(payload.get("files_b64"), Mapping) else {}
    if not files and files_b64:
        decoded_files: dict[str, str] = {}
        for key, value in files_b64.items():
            try:
                decoded_files[str(key)] = base64.b64decode(str(value or ""), validate=True).decode("utf-8", errors="replace")
            except Exception:
                decoded_files[str(key)] = ""
        files = decoded_files
    sha256 = payload.get("sha256") if isinstance(payload.get("sha256"), Mapping) else {}
    validator_addresses = normalize_validator_address_map(payload.get("validator_addresses"))
    validator_public_keys = normalize_validator_public_key_map(payload.get("validator_public_keys"))
    runtime_static_nodes = normalize_runtime_static_nodes_map(payload.get("runtime_static_nodes"))
    missing = [str(item) for item in payload.get("missing_files") or []]
    source_mount = str(payload.get("source_mount") or "")
    raw_candidates = payload.get("source_candidates") if isinstance(payload.get("source_candidates"), list) else []
    source_candidates = [item for item in raw_candidates if isinstance(item, Mapping)]

    base = {
        "source_host": source_host,
        "source_url": source_url,
        "source_mount": source_mount,
        "source_candidates": source_candidates,
        "sha256": {str(key): str(value) for key, value in sha256.items()},
        "validator_addresses": validator_addresses,
        "validator_public_keys": validator_public_keys,
        "runtime_static_nodes": runtime_static_nodes,
    }
    if not bool(payload.get("ok")):
        return {
            "ok": False,
            **base,
            "missing_files": missing,
        }
    required = sorted(QBFT_RUNTIME_CONFIG_FILES)
    missing_keys = [key for key in required if not str(files.get(key) or "").strip()]
    if missing_keys:
        return {
            "ok": False,
            **base,
            "missing_files": missing + missing_keys,
        }
    return {
        "ok": True,
        **base,
        "files": {key: str(files.get(key) or "") for key in required},
        "lineage_hash": str(sha256.get("genesis_json") or ""),
    }

def fetch_json_url(
    url: str,
    *,
    timeout_s: float,
    headers: Mapping[str, str] | None = None,
    insecure_https: bool = False,
) -> Any:
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update({str(key): str(value) for key, value in headers.items()})
    request = urllib.request.Request(url, headers=request_headers)
    parsed = urllib.parse.urlsplit(url)
    context = ssl._create_unverified_context() if insecure_https and parsed.scheme == "https" else None
    if context is None:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
    else:
        with urllib.request.urlopen(request, timeout=timeout_s, context=context) as response:
            raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def select_qbft_config_source(exports: list[dict[str, Any]], *, forced_host: str = "") -> dict[str, Any]:
    forced_host = safe_id(forced_host, kind="host") if forced_host else ""
    usable = [item for item in exports if bool(item.get("ok"))]
    if forced_host:
        forced = [item for item in usable if item.get("source_host") == forced_host]
        if not forced:
            missing = [item for item in exports if item.get("source_host") == forced_host]
            detail = missing[0].get("missing_files") if missing else []
            raise PlanError(f"--config-root-host {forced_host!r} did not provide usable QBFT config material; missing={detail}")
        return forced[0]
    if not usable:
        missing_summary = {str(item.get("source_host")): item.get("missing_files", []) for item in exports}
        raise PlanError(
            "No existing QBFT runtime config source was found. "
            f"Missing/unusable hosts: {json.dumps(missing_summary, sort_keys=True)}"
        )
    lineage_to_hosts: dict[str, list[str]] = {}
    for item in usable:
        lineage = str(item.get("lineage_hash") or "")
        lineage_to_hosts.setdefault(lineage, []).append(str(item.get("source_host") or ""))
    if len(lineage_to_hosts) > 1:
        raise PlanError(
            "Multiple QBFT genesis/config lineages were found; refusing to guess. "
            f"Rerun with --config-root-host. Lineages: {json.dumps(lineage_to_hosts, sort_keys=True)}"
        )
    return usable[0]


def render_qbft_runtime_import_shell(
    plan: NetworkPlan,
    services: list[PlannedService],
    bundle: Mapping[str, Any],
) -> str:
    files = bundle.get("files") if isinstance(bundle.get("files"), Mapping) else {}
    if not files:
        raise PlanError("QBFT runtime import requires exported config files.")
    payload = {
        "network": plan.name,
        "chain_id": plan.chain_id,
        "source_host": bundle.get("source_host", ""),
        "sha256": bundle.get("sha256", {}),
        "files": {key: str(files.get(key) or "") for key in QBFT_RUNTIME_CONFIG_FILES},
        "runtime_dirs": sorted(dict.fromkeys(service_runtime_dir(service) for service in services)),
    }
    payload_json = json.dumps(payload, sort_keys=True)
    return "\n".join(
        [
            "set -eu",
            "mkdir -p /smoke",
            "python3 - <<'PY'",
            "import hashlib, json, os, shutil",
            f"payload = json.loads({json.dumps(payload_json)})",
            "files = payload.get('files') or {}",
            "required = {",
            "  'genesis_json': '/smoke/genesis.json',",
            "  'static_nodes_all_json': '/smoke/static-nodes-all.json',",
            "  'network_metadata_json': '/smoke/network-metadata.json',",
            "}",
            "for key, path in required.items():",
            "    text = files.get(key) or ''",
            "    if not text.strip():",
            "        raise SystemExit(f'missing runtime config payload key: {key}')",
            "    data = text.encode('utf-8')",
            "    expected = (payload.get('sha256') or {}).get(key)",
            "    digest = hashlib.sha256(data).hexdigest()",
            "    if expected and expected != digest:",
            "        raise SystemExit(f'sha256 mismatch for {key}: expected {expected}, got {digest}')",
            "    os.makedirs(os.path.dirname(path), exist_ok=True)",
            "    tmp = path + '.tmp'",
            "    with open(tmp, 'wb') as fh:",
            "        fh.write(data)",
            "    os.replace(tmp, path)",
            "for runtime_dir in payload.get('runtime_dirs') or []:",
            "    if not runtime_dir or '/' in runtime_dir or runtime_dir.startswith('.'):",
            "        raise SystemExit(f'unsafe runtime dir: {runtime_dir!r}')",
            "    os.makedirs(f'/smoke/{runtime_dir}/data', exist_ok=True)",
            "    shutil.copyfile('/smoke/static-nodes-all.json', f'/smoke/{runtime_dir}/static-nodes.json')",
            "print(json.dumps({'ok': True, 'source_host': payload.get('source_host'), 'runtime_dirs': payload.get('runtime_dirs'), 'genesis_sha256': (payload.get('sha256') or {}).get('genesis_json')}, sort_keys=True))",
            "PY",
        ]
    )




def runtime_bundle_static_nodes(bundle: Mapping[str, Any]) -> list[str]:
    files = bundle.get("files") if isinstance(bundle.get("files"), Mapping) else {}
    raw = str(files.get("static_nodes_all_json") or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PlanError(f"Exported static_nodes_all_json is not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise PlanError("Exported static_nodes_all_json must be a JSON list.")
    nodes: list[str] = []
    for item in parsed:
        text = str(item or "").strip()
        if text and text.startswith("enode://") and text not in nodes:
            nodes.append(text)
    return nodes


def runtime_bundle_metadata(bundle: Mapping[str, Any]) -> dict[str, Any]:
    files = bundle.get("files") if isinstance(bundle.get("files"), Mapping) else {}
    raw = str(files.get("network_metadata_json") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def build_runtime_static_refresh_from_bundle(plan: NetworkPlan, bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Build a static-refresh payload from already-live runtime metadata.

    RPC-only mutations must preserve live same-host validators without changing
    the QBFT validator set.  Runtime import writes ``static-nodes-all.json`` to
    every selected runtime directory; when validators are included in the
    preserved host service set, follow it with a static refresh so each validator
    excludes its own enode while RPC-only nodes keep the full peer list.
    """

    nodes = runtime_bundle_static_nodes(bundle)
    metadata = runtime_bundle_metadata(bundle)
    if not metadata:
        metadata = {
            "schema": "main-computer.coolify-qbft-network.v1",
            "network": plan.name,
            "chain_id": plan.chain_id,
            "compose_project": plan.compose_project,
            "docker_network": plan.docker_network,
            "docker_subnet": plan.docker_subnet,
            "validators": [],
            "rpc_nodes": [],
        }
    metadata["network"] = plan.name
    metadata["chain_id"] = plan.chain_id
    metadata["compose_project"] = plan.compose_project
    metadata["docker_network"] = plan.docker_network
    metadata["docker_subnet"] = plan.docker_subnet

    service_enodes: dict[str, str] = {}
    validators = metadata.get("validators") if isinstance(metadata.get("validators"), list) else []
    for item in validators:
        if not isinstance(item, Mapping):
            continue
        service_id = str(item.get("id") or "").strip()
        enode = str(item.get("enode") or "").strip()
        if service_id and enode:
            service_enodes[service_id] = enode
            if enode not in nodes:
                nodes.append(enode)

    return {
        "static_nodes": nodes,
        "network_metadata_json": json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        "service_enodes": service_enodes,
    }


def rpc_add_execution_service_ids_for_host(
    plan: NetworkPlan,
    host_id: str,
    target_services: list[PlannedService] | tuple[PlannedService, ...],
    runtime_bundle: Mapping[str, Any],
) -> list[str]:
    """Return the host target-state for an RPC-only add.

    Keep the mutation narrow, but do not clobber already-live same-host validators
    that are present in the exported runtime metadata.  This is the recovery path
    for a partial ``mutate --add rpc-*`` run where the host compose was rewritten
    with only the new RPC node while a validator on that host remained in the
    on-chain QBFT validator set.
    """

    host_id = safe_id(host_id, kind="host")
    ordered: list[str] = []
    metadata = runtime_bundle_metadata(runtime_bundle)
    validators = metadata.get("validators") if isinstance(metadata.get("validators"), list) else []
    live_ids = {
        str(item.get("id") or "").strip()
        for item in validators
        if isinstance(item, Mapping) and str(item.get("id") or "").strip()
    }
    for service in services_for_host(plan, host_id):
        if service.id in live_ids and "validator" in service.roles and service.id not in ordered:
            ordered.append(service.id)
    for service in target_services:
        if service.host == host_id and service.id not in ordered:
            ordered.append(service.id)
    return ordered


def validator_public_key_from_export(export: Mapping[str, Any], service: PlannedService) -> str:
    public_keys = normalize_validator_public_key_map(export.get("validator_public_keys"))
    for key in (service_runtime_dir(service), service.id):
        value = public_keys.get(safe_id(key, kind="runtime_dir"))
        if value:
            return value
    raise PlanError(f"Validator public key for {service.id!r} was not exported from host {service.host!r}.")


def validator_enode_from_public_key(service: PlannedService, public_key: str) -> str:
    return f"enode://{normalize_validator_public_key(public_key)}@{validator_static_enode_endpoint(service)}"


def live_validator_services_from_export(
    plan: NetworkPlan,
    export: Mapping[str, Any],
    current_validators: list[str] | tuple[str, ...],
    *,
    source_host: str = "",
) -> list[PlannedService]:
    addresses = normalize_validator_address_map(export.get("validator_addresses"))
    current = {normalize_validator_address(address) for address in current_validators}
    services: list[PlannedService] = []
    for service in plan.services:
        if service.role != "validator":
            continue
        if source_host and service.host != source_host:
            continue
        for key in (service_runtime_dir(service), service.id):
            address = addresses.get(safe_id(key, kind="runtime_dir"))
            if address and address in current:
                services.append(service)
                break
    if services:
        return services

    # Older live services can be missing validator-address metadata in the
    # config-export sidecar even though network-metadata.json identifies the
    # active validator runtime.  Validator add currently supports only the
    # one-current-validator bootstrap case, so falling back to metadata/service
    # identity is safer than skipping the source-host refresh and voting with an
    # unrefreshed static-node file.
    metadata = runtime_bundle_metadata(export)
    raw_validators = metadata.get("validators") if isinstance(metadata.get("validators"), list) else []
    metadata_ids = {
        str(item.get("id") or "").strip()
        for item in raw_validators
        if isinstance(item, Mapping) and str(item.get("id") or "").strip()
    }
    if metadata_ids:
        for service in plan.services:
            if service.role != "validator":
                continue
            if source_host and service.host != source_host:
                continue
            if service.id in metadata_ids and service not in services:
                services.append(service)
    if services:
        return services

    if source_host and len(current) == 1:
        return [service for service in plan.services if service.role == "validator" and service.host == source_host]
    return services


def build_post_add_static_refresh(
    plan: NetworkPlan,
    bundle: Mapping[str, Any],
    target_service: PlannedService,
    target_public_key: str,
) -> dict[str, Any]:
    """Build static-node material for the active post-add topology.

    The existing runtime bundle is treated as the live pre-add source of truth.
    Private state can contain future or retired validators, so this intentionally
    appends only the target validator's public enode to the exported live static
    node set.
    """

    old_nodes = runtime_bundle_static_nodes(bundle)
    target_enode = validator_enode_from_public_key(target_service, target_public_key)
    all_nodes = list(dict.fromkeys([*old_nodes, target_enode]))

    metadata = runtime_bundle_metadata(bundle)
    if not metadata:
        metadata = {
            "schema": "main-computer.coolify-qbft-network.v1",
            "network": plan.name,
            "chain_id": plan.chain_id,
            "compose_project": plan.compose_project,
            "docker_network": plan.docker_network,
            "docker_subnet": plan.docker_subnet,
            "validators": [],
            "rpc_nodes": [],
        }
    validators = metadata.get("validators") if isinstance(metadata.get("validators"), list) else []
    kept_validators = [item for item in validators if isinstance(item, Mapping) and str(item.get("id") or "") != target_service.id]
    kept_validators.append(
        {
            "id": target_service.id,
            "ip": target_service.container_ip,
            "p2p": validator_static_enode_endpoint(target_service),
            "enode": target_enode,
        }
    )
    metadata["validators"] = kept_validators
    metadata["network"] = plan.name
    metadata["chain_id"] = plan.chain_id
    metadata["compose_project"] = plan.compose_project
    metadata["docker_network"] = plan.docker_network
    metadata["docker_subnet"] = plan.docker_subnet

    service_enodes: dict[str, str] = {}
    for item in kept_validators:
        if not isinstance(item, Mapping):
            continue
        service_id = str(item.get("id") or "").strip()
        enode = str(item.get("enode") or "").strip()
        if service_id and enode:
            service_enodes[service_id] = enode

    return {
        "static_nodes": all_nodes,
        "target_enode": target_enode,
        "network_metadata_json": json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        "service_enodes": service_enodes,
    }


def verify_post_add_static_runtime_topology(
    plan: NetworkPlan,
    args: argparse.Namespace,
    *,
    static_refresh: Mapping[str, Any],
    services_by_host: Mapping[str, list[PlannedService] | tuple[PlannedService, ...]],
) -> dict[str, Any]:
    """Re-export active runtime files and prove static-node topology before voting.

    This is intentionally stricter than peer_count.  A peer can appear because
    the new node dials the old validator while the old validator's mounted
    ``static-nodes.json`` still omits the new enode.  The QBFT add vote is only
    safe after the source and target runtime files both carry the intended
    post-add static topology.
    """

    expected_nodes = [str(item).strip() for item in static_refresh.get("static_nodes") or [] if str(item).strip()]
    service_enodes = {str(key): str(value) for key, value in (static_refresh.get("service_enodes") or {}).items()}
    target_enode = str(static_refresh.get("target_enode") or "").strip()
    if not target_enode:
        return {"ok": False, "error": "post-add static topology has no target enode"}
    if len(expected_nodes) < 2:
        return {
            "ok": False,
            "error": "post-add static topology must contain at least two enodes before a validator-add vote",
            "static_node_count": len(expected_nodes),
            "expected_nodes": expected_nodes,
        }

    host_results: dict[str, Any] = {}
    errors: list[str] = []
    for raw_host_id, raw_services in services_by_host.items():
        host_id = safe_id(str(raw_host_id), kind="host")
        services = list(raw_services or [])
        try:
            export = export_qbft_config_from_host(plan, args, host_id)
        except Exception as exc:  # noqa: BLE001
            export = {"ok": False, "source_host": host_id, "error": f"{type(exc).__name__}: {exc}"}
        summary = {key: value for key, value in export.items() if key not in {"files", "sync"}}
        host_entry: dict[str, Any] = {
            "ok": bool(export.get("ok")),
            "export": summary,
            "services": [service.id for service in services],
        }
        if not export.get("ok"):
            errors.append(f"{host_id}: active runtime export failed: {export.get('error') or export.get('missing_files')}")
            host_results[host_id] = host_entry
            continue

        all_nodes = runtime_bundle_static_nodes(export)
        runtime_static_nodes = normalize_runtime_static_nodes_map(export.get("runtime_static_nodes"))
        metadata = runtime_bundle_metadata(export)
        metadata_validators = metadata.get("validators") if isinstance(metadata.get("validators"), list) else []
        metadata_validator_ids = [
            str(item.get("id") or "").strip()
            for item in metadata_validators
            if isinstance(item, Mapping) and str(item.get("id") or "").strip()
        ]

        missing_all = [node for node in expected_nodes if node not in all_nodes]
        if missing_all:
            errors.append(f"{host_id}: static-nodes-all.json is missing expected post-add enodes")
        service_checks: dict[str, Any] = {}
        for service in services:
            runtime_dir = service_runtime_dir(service)
            actual_nodes = runtime_static_nodes.get(runtime_dir)
            own_enode = service_enodes.get(service.id, "")
            expected_runtime_nodes = [node for node in expected_nodes if node != own_enode]
            service_missing = [node for node in expected_runtime_nodes if actual_nodes is None or node not in actual_nodes]
            service_errors: list[str] = []
            if actual_nodes is None:
                service_errors.append(f"{runtime_dir}/static-nodes.json was not exported")
            if service_missing:
                service_errors.append("runtime static-nodes.json is missing expected peer enodes")
            if own_enode and actual_nodes is not None and own_enode in actual_nodes:
                service_errors.append("runtime static-nodes.json still includes the service's own enode")
            if service_errors:
                errors.append(f"{host_id}/{service.id}: " + "; ".join(service_errors))
            service_checks[service.id] = {
                "runtime_dir": runtime_dir,
                "ok": not service_errors,
                "actual_static_node_count": None if actual_nodes is None else len(actual_nodes),
                "expected_static_node_count": len(expected_runtime_nodes),
                "missing_expected_enodes": service_missing,
                "self_enode_present": bool(own_enode and actual_nodes is not None and own_enode in actual_nodes),
            }

        required_metadata_ids = [service_id for service_id in service_enodes if service_id not in metadata_validator_ids]
        if required_metadata_ids:
            errors.append(f"{host_id}: network-metadata.json is missing validators {required_metadata_ids}")

        host_entry.update(
            {
                "static_nodes_all_count": len(all_nodes),
                "missing_static_nodes_all": missing_all,
                "runtime_static_nodes": {
                    key: {"count": len(value), "nodes": value}
                    for key, value in sorted(runtime_static_nodes.items())
                },
                "metadata_validator_ids": metadata_validator_ids,
                "missing_metadata_validator_ids": required_metadata_ids,
                "service_checks": service_checks,
                "ok": not (missing_all or required_metadata_ids or any(not item.get("ok") for item in service_checks.values())),
            }
        )
        host_results[host_id] = host_entry

    return {
        "ok": not errors,
        "expected_static_node_count": len(expected_nodes),
        "target_enode": target_enode,
        "service_enodes": service_enodes,
        "hosts": host_results,
        "errors": errors,
    }


def render_qbft_static_refresh_shell(
    services: list[PlannedService] | tuple[PlannedService, ...],
    static_refresh: Mapping[str, Any],
) -> str:
    nodes = [str(item) for item in static_refresh.get("static_nodes") or [] if str(item or "").strip()]
    if not nodes:
        raise PlanError("QBFT static refresh requires at least one enode.")
    service_enodes = {str(key): str(value) for key, value in (static_refresh.get("service_enodes") or {}).items()}
    runtime_dirs: list[dict[str, Any]] = []
    for service in services:
        runtime_dir = service_runtime_dir(service)
        exclude = service_enodes.get(service.id, "") if service.role == "validator" else ""
        runtime_dirs.append({"runtime_dir": runtime_dir, "exclude": exclude})
    payload = {
        "static_nodes": nodes,
        "runtime_dirs": runtime_dirs,
        "network_metadata_json": str(static_refresh.get("network_metadata_json") or ""),
    }
    payload_json = json.dumps(payload, sort_keys=True)
    return "\n".join(
        [
            "set -eu",
            "mkdir -p /smoke",
            "python3 - <<'PY'",
            "import json, os",
            f"payload = json.loads({json.dumps(payload_json)})",
            "nodes = [str(item).strip() for item in payload.get('static_nodes') or [] if str(item).strip()]",
            "if not nodes:",
            "    raise SystemExit('static refresh has no enodes')",
            "def write_nodes(path, keep):",
            "    os.makedirs(os.path.dirname(path), exist_ok=True)",
            "    tmp = path + '.tmp'",
            "    with open(tmp, 'w', encoding='utf-8') as fh:",
            "        json.dump(list(dict.fromkeys(keep)), fh, indent=2)",
            "        fh.write('\\n')",
            "    os.replace(tmp, path)",
            "write_nodes('/smoke/static-nodes-all.json', nodes)",
            "for item in payload.get('runtime_dirs') or []:",
            "    runtime_dir = str(item.get('runtime_dir') or '').strip()",
            "    if not runtime_dir or '/' in runtime_dir or runtime_dir.startswith('.'):",
            "        raise SystemExit(f'unsafe runtime dir: {runtime_dir!r}')",
            "    exclude = str(item.get('exclude') or '').strip()",
            "    keep = [node for node in nodes if node != exclude]",
            "    write_nodes(f'/smoke/{runtime_dir}/static-nodes.json', keep)",
            "metadata = str(payload.get('network_metadata_json') or '')",
            "if metadata.strip():",
            "    json.loads(metadata)",
            "    tmp = '/smoke/network-metadata.json.tmp'",
            "    with open(tmp, 'w', encoding='utf-8') as fh:",
            "        fh.write(metadata)",
            "    os.replace(tmp, '/smoke/network-metadata.json')",
            "print(json.dumps({'ok': True, 'runtime_dirs': [item.get('runtime_dir') for item in payload.get('runtime_dirs') or []], 'static_node_count': len(nodes)}, sort_keys=True))",
            "PY",
        ]
    )


def render_validator_key_init_shell(plan: NetworkPlan, services: list[PlannedService] | tuple[PlannedService, ...]) -> str:
    """Return a one-shot Besu script that creates validator node keys and address files.

    Live validator adds import the existing genesis/static-node material instead of
    running the full bootstrap path.  Validator nodes still need stable node keys
    before Besu starts so the executor can propose the corresponding QBFT validator
    address.  This sidecar uses Besu's operator key generation path, writes
    ``validator-address`` next to the runtime directory, and leaves existing keys
    untouched on re-runs.
    """

    validators = [service for service in services if service.role == "validator"]
    if not validators:
        return "set -eu\necho 'No validator key initialization required.'"
    one_validator_plan = replace(plan, services=(validators[0],))
    config = render_json_heredoc(qbft_config(one_validator_plan))
    lines: list[str] = [
        "set -eu",
        "if command -v besu >/dev/null 2>&1; then BESU=besu; else BESU=/opt/besu/bin/besu; fi",
        "mkdir -p /tmp/qbft-validator-key-init",
        "cat > /tmp/qbft-validator-key-init/qbftConfigFile.json <<'JSON'",
        config,
        "JSON",
    ]
    for service in validators:
        runtime_dir = service_runtime_dir(service)
        safe_runtime = shlex.quote(runtime_dir)
        key_tmp = f"/tmp/qbft-validator-key-init/{service.id}"
        lines.extend(
            [
                f"mkdir -p /smoke/{safe_runtime}/data",
                f"if [ ! -f /smoke/{safe_runtime}/data/key ]; then",
                f"  rm -rf {shlex.quote(key_tmp)}",
                f"  \"$BESU\" operator generate-blockchain-config --config-file=/tmp/qbft-validator-key-init/qbftConfigFile.json --to={shlex.quote(key_tmp)} --private-key-file-name=key --public-key-file-name=key.pub",
                f"  key_dir=$(find {shlex.quote(key_tmp)}/keys -mindepth 1 -maxdepth 1 -type d | sort | head -n 1)",
                "  [ -n \"$key_dir\" ] || { echo 'validator key generation produced no key directory' >&2; exit 41; }",
                f"  cp \"$key_dir/key\" /smoke/{safe_runtime}/data/key",
                f"  cp \"$key_dir/key.pub\" /smoke/{safe_runtime}/data/key.pub",
                "else",
                f"  \"$BESU\" --data-path=/smoke/{safe_runtime}/data public-key export --to=/smoke/{safe_runtime}/data/key.pub",
                "fi",
                f"\"$BESU\" --data-path=/smoke/{safe_runtime}/data public-key export-address --to=/smoke/{safe_runtime}/validator-address.tmp",
                f"tr -d '\\r\\n ' < /smoke/{safe_runtime}/validator-address.tmp > /smoke/{safe_runtime}/validator-address",
                f"rm -f /smoke/{safe_runtime}/validator-address.tmp",
                f"addr=$(cat /smoke/{safe_runtime}/validator-address)",
                f"case \"$addr\" in 0x*) ;; *) addr=\"0x$addr\"; printf '%s' \"$addr\" > /smoke/{safe_runtime}/validator-address ;; esac",
                f"echo \"Validator address for {service.id}: $(cat /smoke/{safe_runtime}/validator-address)\"",
            ]
        )
    lines.append("echo 'QBFT validator key initialization complete.'")
    return "\n".join(lines)



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



def validator_retire_vote_services_for_host(
    plan: NetworkPlan,
    host_id: str,
    target_service: PlannedService,
) -> list[PlannedService]:
    """Return the narrow host-local service set needed to vote out a validator.

    Keep the target validator running long enough to publish its own removal vote,
    and preserve host-local validator/RPC services that can also vote.  Avoid
    pulling in unrelated future validator-only declarations.
    """

    host_id = safe_id(host_id, kind="host")
    services: list[PlannedService] = [
        service
        for service in services_for_host(plan, host_id)
        if service.id != target_service.id and "validator" in service.roles and "rpc" in service.roles
    ]
    services.append(target_service)
    return services


def render_qbft_validator_vote_sidecar_shell(vote: Mapping[str, Any]) -> str:
    address = normalize_validator_address(vote.get("validator_address"))
    proposal = bool(vote.get("proposal"))
    raw_services = vote.get("services") if isinstance(vote.get("services"), list) else []
    urls: list[str] = []
    for item in raw_services:
        if not isinstance(item, Mapping):
            continue
        service_name = str(item.get("service_name") or "").strip()
        if not service_name:
            continue
        urls.append(f"http://{service_name}:{RPC_CONTAINER_PORT}")
    urls = list(dict.fromkeys(urls))
    if not urls:
        raise PlanError("validator vote sidecar requires at least one internal validator RPC service URL")
    payload = {
        "validator_address": address,
        "proposal": proposal,
        "urls": urls,
        "timeout_s": 180.0,
        "poll_s": 2.0,
    }
    payload_json = json.dumps(payload, sort_keys=True)
    return "\n".join(
        [
            "set -eu",
            "cat > /tmp/qbft-validator-vote.json <<'JSON_QBFT_VALIDATOR_VOTE'",
            payload_json,
            "JSON_QBFT_VALIDATOR_VOTE",
            "python3 - <<'PY_QBFT_VALIDATOR_VOTE'",
            "import json",
            "import time",
            "import urllib.request",
            "",
            "with open('/tmp/qbft-validator-vote.json', 'r', encoding='utf-8') as fh:",
            "    cfg = json.load(fh)",
            "address = cfg['validator_address']",
            "proposal = bool(cfg['proposal'])",
            "deadline = time.time() + float(cfg.get('timeout_s') or 180.0)",
            "poll_s = float(cfg.get('poll_s') or 2.0)",
            "failed = []",
            "for url in cfg['urls']:",
            "    last_error = ''",
            "    ok = False",
            "    while time.time() <= deadline:",
            "        body = json.dumps({",
            "            'jsonrpc': '2.0',",
            "            'id': 1,",
            "            'method': 'qbft_proposeValidatorVote',",
            "            'params': [address, proposal],",
            "        }).encode('utf-8')",
            "        request = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})",
            "        try:",
            "            with urllib.request.urlopen(request, timeout=8.0) as response:",
            "                payload = json.loads(response.read().decode('utf-8'))",
            "            if payload.get('result') is True:",
            "                print(f'qbft vote ok url={url} address={address} proposal={proposal}')",
            "                ok = True",
            "                break",
            "            last_error = f'unexpected response {payload!r}'",
            "        except Exception as exc:",
            "            last_error = f'{type(exc).__name__}: {exc}'",
            "        time.sleep(poll_s)",
            "    if not ok:",
            "        failed.append({'url': url, 'error': last_error})",
            "if failed:",
            "    raise SystemExit('qbft validator vote sidecar failed: ' + json.dumps(failed, sort_keys=True))",
            "print('qbft validator vote sidecar complete')",
            "PY_QBFT_VALIDATOR_VOTE",
            "echo 'qbft validator vote sidecar complete; parking healthy until next compose repush'",
            "while true; do sleep 3600; done",
        ]
    )


def append_qbft_validator_vote_sidecar(
    lines: list[str],
    plan: NetworkPlan,
    host_id: str,
    vote: Mapping[str, Any] | None,
) -> None:
    if not vote:
        return
    raw_services = vote.get("services") if isinstance(vote.get("services"), list) else []
    services: list[dict[str, str]] = []
    for item in raw_services:
        if not isinstance(item, Mapping):
            continue
        service_id = safe_id(item.get("id"), kind="service")
        services.append({"id": service_id, "service_name": service_id.replace("_", "-")})
    vote_payload = {**dict(vote), "services": services}
    vote_script = escape_compose_interpolation(render_qbft_validator_vote_sidecar_shell(vote_payload))
    lines.extend(
        [
            "  qbft-validator-vote:",
            f"    image: {yaml_quote(QBFT_CONFIG_TRANSFER_IMAGE)}",
            "    restart: \"no\"",
            "    exclude_from_hc: true",
            "    healthcheck:",
            "      test: [\"CMD-SHELL\", \"true\"]",
            "      interval: 30s",
            "      timeout: 5s",
            "      start_period: 5s",
            "      retries: 3",
        ]
    )
    if services:
        lines.append("    depends_on:")
        for item in services:
            lines.extend(
                [
                    f"      {item['service_name']}:",
                    "        condition: service_started",
                ]
            )
    lines.extend(
        [
            "    entrypoint:",
            "      - /bin/sh",
            "      - -ec",
            "      - |-",
        ]
    )
    lines.extend([f"        {line}" if line else "" for line in vote_script.splitlines()])
    lines.extend(
        [
            "    networks:",
            "      - qbft",
            "",
        ]
    )


def append_parked_qbft_helper_services(lines: list[str], active_helper_service_keys: set[str]) -> None:
    """Keep inactive helper service names running so Coolify steady-state stays healthy.

    Coolify can keep previously generated compose service keys visible in the stack
    after mutation repushes.  Instead of leaving old one-shot helper containers in
    an exited state, steady-state compose includes harmless parked replacements.
    Real helper work still gets repushed with the same service name when needed.
    """

    for service_key in QBFT_HELPER_PARK_SERVICE_KEYS:
        if service_key in active_helper_service_keys:
            continue
        lines.extend(
            [
                f"  {service_key}:",
                f"    image: {yaml_quote(QBFT_HELPER_PARK_IMAGE)}",
                "    restart: unless-stopped",
                "    exclude_from_hc: true",
                "    labels:",
                "      - \"traefik.enable=false\"",
                "    command:",
                "      - /bin/sh",
                "      - -ec",
                "      - |-",
                f"        echo {shell_single_quote(f'{service_key} parked healthy; repush compose to run this helper when needed')}",
                "        while true; do sleep 3600; done",
                "    healthcheck:",
                "      test: [\"CMD-SHELL\", \"true\"]",
                "      interval: 30s",
                "      timeout: 5s",
                "      start_period: 5s",
                "      retries: 3",
                "",
            ]
        )



def render_compose_for_host(
    plan: NetworkPlan,
    host_id: str,
    *,
    include_bootstrap: bool = True,
    managed_volume: bool = True,
    runtime_import_bundle: Mapping[str, Any] | None = None,
    include_rpc_public_entry: bool = True,
    config_export: Mapping[str, Any] | None = None,
    retire_cleanup_services: list[PlannedService] | tuple[PlannedService, ...] = (),
    validator_vote: Mapping[str, Any] | None = None,
    static_refresh: Mapping[str, Any] | None = None,
    park_inactive_helpers: bool = True,
) -> str:
    host_id = safe_id(host_id, kind="host")
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
    active_helper_service_keys: set[str] = set()

    volume_name, volume_lines = render_volume_mount(plan, host, managed_volume=managed_volume)
    try:
        rpc_target = rpc_target_service(plan)
    except PlanError:
        rpc_target = None
    config_export = config_export or None
    config_export_volume_names: list[str] = []
    if config_export is not None:
        export_sources = qbft_config_export_mount_sources(
            plan,
            host_id,
            volume_prefixes=list(config_export.get("volume_prefixes") or ()),
        )
        export_volume_lines: list[str] = []
        for source in export_sources:
            if source["kind"] == "volume":
                export_volume_lines.append(f"      - {yaml_quote(f'{source['source']}:{source['target']}:ro')}")
                config_export_volume_names.append(str(source["source"]))
            elif source["kind"] in {"bind", "bind-existing"}:
                create_host_path = "false" if source["kind"] == "bind-existing" else "true"
                export_volume_lines.extend(
                    [
                        "      - type: bind",
                        f"        source: {yaml_quote(source['source'])}",
                        f"        target: {source['target']}",
                        "        read_only: true",
                        "        bind:",
                        f"          create_host_path: {create_host_path}",
                    ]
                )
        export_script = escape_compose_interpolation(render_qbft_config_exporter_shell(plan, host_id, config_export))
        export_port = int(config_export.get("port") or DEFAULT_QBFT_CONFIG_EXPORT_PORT)
        lines.extend(
            [
                "  qbft-config-export:",
                f"    image: {yaml_quote(QBFT_CONFIG_EXPORT_IMAGE)}",
                "    restart: unless-stopped",
                "    labels:",
                "      - \"traefik.enable=false\"",
                "    ports:",
                f"      - {yaml_quote(f'0.0.0.0:{export_port}:8080')}",
                "    volumes:",
                *export_volume_lines,
                "    entrypoint:",
                "      - /bin/sh",
                "      - -ec",
                "      - |-",
            ]
        )
        lines.extend([f"        {line}" if line else "" for line in export_script.splitlines()])
        lines.append("")
    include_runtime_import = runtime_import_bundle is not None
    include_validator_key_init = include_runtime_import and any(service.role == "validator" for service in services)
    if include_runtime_import:
        active_helper_service_keys.add("qbft-runtime-import")
        import_script = escape_compose_interpolation(render_qbft_runtime_import_shell(plan, services, runtime_import_bundle or {}))
        lines.extend(
            [
                "  qbft-runtime-import:",
                f"    image: {yaml_quote(QBFT_CONFIG_TRANSFER_IMAGE)}",
                "    restart: \"no\"",
                "    exclude_from_hc: true",
                "    entrypoint:",
                "      - /bin/sh",
                "      - -ec",
                "      - |-",
            ]
        )
        lines.extend([f"        {line}" if line else "" for line in import_script.splitlines()])
        lines.extend(
            [
                "    volumes:",
                *volume_lines,
                "",
            ]
        )
    if include_validator_key_init:
        active_helper_service_keys.add("qbft-validator-key-init")
        key_init_script = escape_compose_interpolation(render_validator_key_init_shell(plan, services))
        lines.extend(
            [
                "  qbft-validator-key-init:",
                f"    image: {yaml_quote(plan.besu_image)}",
                "    restart: \"no\"",
                "    exclude_from_hc: true",
                "    depends_on:",
                "      qbft-runtime-import:",
                "        condition: service_completed_successfully",
                "    entrypoint:",
                "      - /bin/sh",
                "      - -ec",
                "      - |-",
            ]
        )
        lines.extend([f"        {line}" if line else "" for line in key_init_script.splitlines()])
        lines.extend(
            [
                "    volumes:",
                *volume_lines,
                "",
            ]
        )

    if static_refresh:
        active_helper_service_keys.add("qbft-static-refresh")
        static_refresh_script = escape_compose_interpolation(render_qbft_static_refresh_shell(services, static_refresh))
        lines.extend(
            [
                "  qbft-static-refresh:",
                f"    image: {yaml_quote(QBFT_CONFIG_TRANSFER_IMAGE)}",
                "    restart: \"no\"",
                "    exclude_from_hc: true",
            ]
        )
        if include_runtime_import or include_validator_key_init:
            lines.append("    depends_on:")
            if include_runtime_import:
                lines.extend(
                    [
                        "      qbft-runtime-import:",
                        "        condition: service_completed_successfully",
                    ]
                )
            if include_validator_key_init:
                lines.extend(
                    [
                        "      qbft-validator-key-init:",
                        "        condition: service_completed_successfully",
                    ]
                )
        lines.extend(
            [
                "    entrypoint:",
                "      - /bin/sh",
                "      - -ec",
                "      - |-",
            ]
        )
        lines.extend([f"        {line}" if line else "" for line in static_refresh_script.splitlines()])
        lines.extend(
            [
                "    volumes:",
                *volume_lines,
                "",
            ]
        )

    if include_bootstrap:
        active_helper_service_keys.add("qbft-bootstrap")
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
        if service_should_publish_rpc(plan, service):
            port_lines.append(f"      - {yaml_quote(f'{service.rpc_bind_host}:{service.rpc_host_port}:{RPC_CONTAINER_PORT}')}")
        if service_should_publish_p2p(plan, service):
            port_lines.append(f"      - {yaml_quote(f'{service.p2p_bind_host}:{service.p2p_host_port}:{P2P_CONTAINER_PORT}')}")
        if port_lines:
            lines.append("    ports:")
            lines.extend(port_lines)
        if include_bootstrap or include_runtime_import or include_validator_key_init or static_refresh:
            lines.append("    depends_on:")
            if include_bootstrap:
                lines.extend(
                    [
                        "      qbft-bootstrap:",
                        "        condition: service_completed_successfully",
                    ]
                )
            if include_runtime_import:
                lines.extend(
                    [
                        "      qbft-runtime-import:",
                        "        condition: service_completed_successfully",
                    ]
                )
            if include_validator_key_init:
                lines.extend(
                    [
                        "      qbft-validator-key-init:",
                        "        condition: service_completed_successfully",
                    ]
                )
            if static_refresh:
                lines.extend(
                    [
                        "      qbft-static-refresh:",
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
        if rpc_target is not None and service.id == rpc_target.id:
            lines.extend(
                [
                    "        aliases:",
                    "          - mc-qbft-rpc",
                ]
            )
        lines.append("")

    if validator_vote:
        active_helper_service_keys.add("qbft-validator-vote")
    append_qbft_validator_vote_sidecar(lines, plan, host_id, validator_vote)

    if retire_cleanup_services:
        active_helper_service_keys.add("qbft-retire-orphan-cleanup")
    append_retired_qbft_service_cleanup(lines, retire_cleanup_services)

    if park_inactive_helpers:
        append_parked_qbft_helper_services(lines, active_helper_service_keys)

    if include_rpc_public_entry:
        append_rpc_public_entry_sidecar(lines, plan, host_id, config_export=config_export)

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
        volume_names = list(dict.fromkeys([volume_name, *config_export_volume_names]))
        lines.append("volumes:")
        for item in volume_names:
            lines.extend(
                [
                    f"  {item}:",
                    f"    name: {item}",
                ]
            )
        lines.append("")
    return "\n".join(lines)


def render_retired_qbft_service_cleanup_shell(retired_services: list[PlannedService] | tuple[PlannedService, ...]) -> str:
    """Return a Docker-socket cleanup script for retired host-local QBFT services.

    Coolify sometimes deploys a compose target-state without removing containers
    for services that disappeared from the compose file.  The cleanup container
    runs inside the same Coolify compose project, discovers that project label
    from its own container, and force-removes only containers for the retired
    service names.  The host-port fallback catches the same orphan if Coolify's
    deploy strategy changed the compose project label.
    """

    specs: list[tuple[str, str]] = []
    for service in retired_services:
        service_name = service.id.replace("_", "-")
        rpc_port = "" if service.rpc_host_port is None else str(service.rpc_host_port)
        specs.append((service_name, rpc_port))
    spec_lines = "\n".join(f"{name}|{port}" for name, port in specs)
    return "\n".join(
        [
            "set -eu",
            f"rpc_container_port={RPC_CONTAINER_PORT}",
            "self_id=${HOSTNAME:-}",
            "compose_project=''",
            "if [ -n \"$self_id\" ]; then",
            "  compose_project=$(docker inspect -f '{{ index .Config.Labels \"com.docker.compose.project\" }}' \"$self_id\" 2>/dev/null || true)",
            "fi",
            "echo \"QBFT retire cleanup compose_project=${compose_project:-unknown}\"",
            "cat > /tmp/retired-qbft-services.txt <<'EOF_RETIRED_QBFT_SERVICES'",
            spec_lines,
            "EOF_RETIRED_QBFT_SERVICES",
            "while IFS='|' read -r compose_service rpc_host_port; do",
            "  [ -n \"$compose_service\" ] || continue",
            "  echo \"Scanning for retired QBFT service $compose_service on RPC host port ${rpc_host_port:-unknown}\"",
            "  docker ps -a -q --filter \"label=com.docker.compose.service=$compose_service\" | while read -r container_id; do",
            "    [ -n \"$container_id\" ] || continue",
            "    container_project=$(docker inspect -f '{{ index .Config.Labels \"com.docker.compose.project\" }}' \"$container_id\" 2>/dev/null || true)",
            "    published_rpc=$(docker port \"$container_id\" \"${rpc_container_port}/tcp\" 2>/dev/null || true)",
            "    remove=false",
            "    if [ -n \"$compose_project\" ] && [ \"$container_project\" = \"$compose_project\" ]; then",
            "      remove=true",
            "    fi",
            "    if [ -n \"$rpc_host_port\" ]; then",
            "      case \"$published_rpc\" in",
            "        *\":$rpc_host_port\"*) remove=true ;;",
            "      esac",
            "    fi",
            "    if [ \"$remove\" = true ]; then",
            "      echo \"Removing retired QBFT container $container_id service=$compose_service project=${container_project:-unknown} published_rpc=${published_rpc:-none}\"",
            "      docker rm -f \"$container_id\"",
            "    else",
            "      echo \"Keeping container $container_id service=$compose_service project=${container_project:-unknown} published_rpc=${published_rpc:-none}\"",
            "    fi",
            "  done",
            "done < /tmp/retired-qbft-services.txt",
            "echo 'QBFT retire orphan cleanup complete.'",
            "echo 'QBFT retire orphan cleanup parked healthy until next compose repush.'",
            "while true; do sleep 3600; done",
        ]
    )


def append_retired_qbft_service_cleanup(
    lines: list[str],
    retired_services: list[PlannedService] | tuple[PlannedService, ...],
) -> None:
    """Append a one-shot cleanup service that removes orphan retired containers."""

    if not retired_services:
        return
    cleanup_script = escape_compose_interpolation(render_retired_qbft_service_cleanup_shell(retired_services))
    lines.extend(
        [
            "  qbft-retire-orphan-cleanup:",
            f"    image: {yaml_quote(RETIRED_QBFT_CLEANUP_IMAGE)}",
            "    restart: \"no\"",
            "    exclude_from_hc: true",
            "    healthcheck:",
            "      test: [\"CMD-SHELL\", \"true\"]",
            "      interval: 30s",
            "      timeout: 5s",
            "      start_period: 5s",
            "      retries: 3",
            "    labels:",
            "      - \"traefik.enable=false\"",
            "    volumes:",
            "      - \"/var/run/docker.sock:/var/run/docker.sock\"",
            "    entrypoint:",
            "      - /bin/sh",
            "      - -ec",
            "      - |-",
        ]
    )
    lines.extend([f"        {line}" if line else "" for line in cleanup_script.splitlines()])
    lines.append("")


def render_retire_cleanup_compose_for_host(
    plan: NetworkPlan,
    host_id: str,
    retired_services: list[PlannedService] | tuple[PlannedService, ...] = (),
) -> str:
    """Render a cleanup-only compose for an affected host with no remaining QBFT nodes.

    Raw snapshot/new_patch delivery cannot encode deletions by omission, and
    Coolify itself may or may not remove orphan containers during redeploy.  This
    compose gives the host-scoped Coolify service a concrete target state after
    retiring its last QBFT node: keep only a tiny sidecar that removes this host's
    Traefik RPC dynamic config.  The old node is intentionally absent from the
    rendered service list.
    """

    host_id = safe_id(host_id, kind="host")
    hosts = host_by_id(plan)
    if host_id not in hosts:
        raise PlanError(f"Unknown host for retire cleanup rendering: {host_id}")

    cleanup_plan = replace(plan, hosts=(hosts[host_id],), services=())
    lines: list[str] = [
        f"name: {plan.compose_project}-{host_id}",
        "",
        "services:",
    ]
    before_len = len(lines)
    append_rpc_public_entry_sidecar(lines, cleanup_plan, host_id)
    append_retired_qbft_service_cleanup(lines, retired_services)
    if len(lines) == before_len:
        # Compose requires at least one service.  When there is no public RPC
        # sidecar to clean up, keep a tiny inert service so updating the Coolify
        # Docker Compose resource still replaces the old target-state.
        lines.extend(
            [
                "  qbft-retire-cleanup:",
                f"    image: {yaml_quote(TRAEFIK_DYNAMIC_CONFIG_IMAGE)}",
                "    init: true",
                "    restart: unless-stopped",
                "    labels:",
                "      - \"traefik.enable=false\"",
                "    command:",
                "      - /bin/sh",
                "      - -euc",
                "      - |-",
                "        echo 'QBFT retire cleanup target state installed; no public-entry config was enabled.'",
                "        while true; do sleep 300; done",
                "",
            ]
        )
    return "\n".join(lines)


def verify_retired_rpc_unreachable(plan: NetworkPlan, args: argparse.Namespace, services: list[PlannedService]) -> dict[str, Any]:
    """Return ok only when every retired RPC backend no longer answers JSON-RPC."""

    hosts = host_by_id(plan)
    rpc_user_agent = str(getattr(args, "rpc_user_agent", DEFAULT_JSON_RPC_USER_AGENT) or "").strip()
    timeout_s = min(8.0, max(2.0, float(getattr(args, "chain_observation_timeout_s", DEFAULT_CHAIN_OBSERVATION_TIMEOUT_S))))
    checks: list[dict[str, Any]] = []
    for service in services:
        host = hosts.get(service.host)
        if host is None:
            checks.append(
                {
                    "ok": False,
                    "instance": service.id,
                    "error": f"unknown host {service.host!r}",
                }
            )
            continue
        try:
            url = rpc_public_entry_backend_url(plan, host, service)
            chain_id = json_rpc(url, "eth_chainId", timeout_s=timeout_s, user_agent=rpc_user_agent)
            checks.append(
                {
                    "ok": False,
                    "instance": service.id,
                    "url": url,
                    "error": "retired RPC backend still responds to eth_chainId",
                    "chain_id": chain_id,
                }
            )
        except Exception as exc:  # noqa: BLE001 - unreachable is the expected retired state.
            checks.append(
                {
                    "ok": True,
                    "instance": service.id,
                    "url": rpc_public_entry_backend_url(plan, host, service) if host is not None else "",
                    "unreachable": True,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {"ok": all(bool(item.get("ok")) for item in checks), "checks": checks}




def operator_checks(plan: NetworkPlan) -> dict[str, Any]:
    ports = sorted(
        {service.p2p_host_port for service in plan.services if service.p2p_host_port is not None}
        | {service.rpc_host_port for service in plan.services if service.rpc_host_port is not None}
    )
    grep_ports = "|".join(str(port) for port in ports)
    first_rpc = rpc_target_service(plan)
    rpc_url = str(plan.external_rpc_url or first_rpc.rpc_url_on_host).strip()
    return {
        "preflight_ports": ports,
        "preflight_command": f"sudo ss -tulpn | grep -E ':({grep_ports})\\\\b' || true",
        "first_rpc_url": rpc_url,
        "chain_id_probe": (
            f"curl -s {rpc_url} "
            "-H 'content-type: application/json' "
            "--data '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"eth_chainId\",\"params\":[]}'"
        ),
        "block_probe": (
            f"curl -s {rpc_url} "
            "-H 'content-type: application/json' "
            "--data '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"eth_blockNumber\",\"params\":[]}'"
        ),
    }


def render_commands(plan: NetworkPlan) -> str:
    checks = operator_checks(plan)
    lines = [
        f"# Operator checks for {plan.name}",
        "",
        "# Coolify deploys are API-driven. Do not use SSH as the deploy path.",
        "# Use private state for Coolify URL/token/server/project context.",
        "",
        "# Optional host-side port sanity check, to run from whatever host admin shell you already use:",
        checks["preflight_command"],
        "",
        "# After Coolify deploy, verify the operator RPC endpoint:",
        checks["chain_id_probe"],
        checks["block_probe"],
        "",
    ]
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
            f"Start/repair the Main Computer local startup golden path, then retry apply test. "
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
    return action in {"apply", "coolify-sync", "coolify-check", "coolify-discover", "discover-topology", "wait-rpc", "deploy-contracts"}


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


def read_token_file(path_text: str) -> str:
    path = Path(path_text)
    if not path.is_file():
        return ""
    token = path.read_text(encoding="utf-8").strip()
    if token.startswith("token="):
        token = token.split("=", 1)[1].strip()
    return token


def coolify_host_for_action(plan: NetworkPlan | None, host_id: str | None = None) -> PlannedHost | None:
    if plan is None or not plan.hosts:
        return None
    if host_id:
        hosts = host_by_id(plan)
        return hosts.get(host_id)
    return next((host for host in plan.hosts if services_for_host(plan, host.id)), plan.hosts[0])


def resolve_coolify_token(
    args: argparse.Namespace,
    plan: NetworkPlan | None = None,
    *,
    host_id: str | None = None,
) -> tuple[str, str]:
    if getattr(args, "coolify_token", ""):
        return str(args.coolify_token), "direct"

    explicit_env = str(getattr(args, "coolify_token_env", "") or "").strip()
    env_candidates = [explicit_env] if explicit_env and explicit_env != DEFAULT_COOLIFY_TOKEN_ENV else []
    host = coolify_host_for_action(plan, host_id)
    if host is not None and host.api_token_env:
        env_candidates.append(host.api_token_env)
    env_candidates.append(DEFAULT_COOLIFY_TOKEN_ENV)
    for token_env in dict.fromkeys(env_candidates):
        if token_env and os.environ.get(token_env):
            return str(os.environ[token_env]), f"env:{token_env}"

    token_file = str(getattr(args, "coolify_token_file", "") or "").strip()
    if token_file:
        token = read_token_file(token_file)
        if token:
            return token, f"file:{token_file}"
    if host is not None and host.api_token_file:
        token = read_token_file(host.api_token_file)
        if token:
            return token, f"file:{host.api_token_file}"
    if host is not None and host.api_token:
        return host.api_token, f"private-state:coolify.hosts.{host.id}.api_token"

    local_token = str(getattr(args, "_local_coolify_token", "") or "").strip()
    if local_token:
        return local_token, str(getattr(args, "_local_coolify_token_source", "") or "local-helper")
    raise CoolifyError(
        "Coolify token is required. Pass --coolify-token, --coolify-token-env, "
        f"set {DEFAULT_COOLIFY_TOKEN_ENV}, or define coolify.hosts.<slot>.api_token/api_token_env in private state."
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
    """Return an operator-safe Coolify response summary.

    The old deploy/apply output included raw Coolify API bodies.  Some endpoints
    return full project/server/service graphs, including nested settings and
    tokens.  Keep the request/status/error shape but never echo the full body in
    operator JSON.
    """

    return coolify_response_summary(response, token=token, token_source=token_source)


def coolify_response_summary(response: CoolifyResponse, *, token: str = "", token_source: str = "") -> dict[str, Any]:
    """Return an operator-safe response summary without echoing large Coolify API bodies.

    Discovery endpoints can return entire server/application/service graphs, including
    nested settings and tokens.  The topology report only needs request success,
    status, path, and a small body shape/error summary.
    """

    body = response.body
    result: dict[str, Any] = {
        "ok": bool(response.ok),
        "status": response.status,
        "method": response.method,
        "url": response.url,
        "path": response.path,
    }
    if isinstance(body, Mapping):
        result["body_type"] = "object"
        result["body_keys"] = sorted(str(key) for key in body.keys())[:40]
        for key in ("error", "message", "detail"):
            value = body.get(key)
            if isinstance(value, (str, int, float, bool)) or value is None:
                result[key] = value
        for key in ("errors", "data"):
            value = body.get(key)
            if isinstance(value, list):
                result[f"{key}_count"] = len(value)
            elif isinstance(value, Mapping):
                result[f"{key}_keys"] = sorted(str(item) for item in value.keys())[:40]
    elif isinstance(body, list):
        result["body_type"] = "array"
        result["body_count"] = len(body)
    elif body not in (None, ""):
        result["body_type"] = type(body).__name__
        if isinstance(body, (str, int, float, bool)):
            text = str(body)
            result["body_preview"] = text if len(text) <= 200 else text[:197] + "..."
    else:
        result["body_type"] = "empty"
    if token:
        result["token"] = redact_secret(token)
    if token_source:
        result["token_source"] = token_source
    return result


def coolify_client_from_args(
    args: argparse.Namespace,
    plan: NetworkPlan | None = None,
    *,
    host_id: str | None = None,
) -> tuple[CoolifyClient, str, str]:
    url = str(getattr(args, "coolify_url", "") or "").strip()
    selected_host_id = host_id or str(getattr(args, "_coolify_host_id", "") or "")
    host = coolify_host_for_action(plan, selected_host_id)
    if not url and host is not None:
        url = host.coolify_url
    if not url:
        raise CoolifyError("Coolify URL is required. Pass --coolify-url or define coolify.hosts.<slot>.url in private state.")
    token, token_source = resolve_coolify_token(args, plan, host_id=selected_host_id)
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


def _load_hub_service_module() -> Any:
    if "coolify_hub_service_for_qbft_verify" in sys.modules:
        return sys.modules["coolify_hub_service_for_qbft_verify"]
    spec = importlib.util.spec_from_file_location("coolify_hub_service_for_qbft_verify", HUB_SERVICE_TOOL_PATH)
    if spec is None or spec.loader is None:
        raise CoolifyError(f"Could not import Hub service verifier from {HUB_SERVICE_TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _decode_possible_compose_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    if "services:" in text or "\nservices:" in text:
        return text
    try:
        decoded = base64.b64decode(text, validate=True).decode("utf-8", errors="replace")
    except Exception:
        return ""
    return decoded if "services:" in decoded or "\nservices:" in decoded else ""


def coolify_compose_text_from_payload(payload: Any) -> str:
    """Best-effort extraction of a raw Docker Compose document from Coolify API payloads."""

    seen: set[int] = set()

    def walk(value: Any) -> str:
        marker = id(value)
        if marker in seen:
            return ""
        seen.add(marker)
        if isinstance(value, str):
            return _decode_possible_compose_text(value)
        if isinstance(value, list):
            for item in value:
                found = walk(item)
                if found:
                    return found
            return ""
        if not isinstance(value, Mapping):
            return ""

        preferred_keys = (
            "docker_compose_raw",
            "docker_compose",
            "compose",
            "docker_compose_file",
            "dockerCompose",
            "dockerComposeRaw",
        )
        for key in preferred_keys:
            if key in value:
                found = walk(value.get(key))
                if found:
                    return found
        for nested_key in ("service", "data", "resource", "application", "settings"):
            if nested_key in value:
                found = walk(value.get(nested_key))
                if found:
                    return found
        return ""

    return walk(payload)


def compose_service_keys(compose: str) -> list[str]:
    if not str(compose or "").strip():
        return []
    try:
        import yaml
    except ImportError:
        yaml = None  # type: ignore[assignment]
    if yaml is not None:
        try:
            loaded = yaml.safe_load(compose)
            services = loaded.get("services") if isinstance(loaded, Mapping) else None
            if isinstance(services, Mapping):
                return [str(key) for key in services]
        except Exception:
            pass

    keys: list[str] = []
    in_services = False
    for raw_line in compose.splitlines():
        if raw_line.startswith("services:"):
            in_services = True
            continue
        if in_services and raw_line and not raw_line.startswith((" ", "\t")):
            break
        if in_services:
            match = re.match(r"^  ([A-Za-z0-9_.-]+):\s*(?:#.*)?$", raw_line)
            if match:
                keys.append(match.group(1))
    return keys


def coolify_service_detail(
    client: CoolifyClient,
    args: argparse.Namespace,
    service_uuid: str,
) -> dict[str, Any]:
    service_uuid = str(service_uuid or "").strip()
    if not service_uuid:
        return {"ok": False, "reason": "missing-service-uuid", "tried": []}

    tried: list[dict[str, Any]] = []
    for path in (
        f"/api/v1/services/{urllib.parse.quote(service_uuid)}",
        f"/api/v1/services/{urllib.parse.quote(service_uuid)}/compose",
    ):
        response = client.request("GET", path)
        tried.append({"path": path, "response": coolify_response_summary(response)})
        if not response.ok:
            continue
        compose = coolify_compose_text_from_payload(response.body)
        if compose:
            return {
                "ok": True,
                "source": path,
                "compose": compose,
                "service_keys": compose_service_keys(compose),
                "tried": tried,
            }
    return {"ok": False, "reason": "compose-not-returned-by-coolify-api", "tried": tried}


def instance_catalog(plan: NetworkPlan) -> dict[str, dict[str, Any]]:
    hosts = host_by_id(plan)
    return {
        service.id: {
            "id": service.id,
            "roles": list(service.roles),
            "role": service.role,
            "host": service.host,
            "coolify_host": service.host,
            "container_ip": service.container_ip,
            "rpc_host_port": service.rpc_host_port,
            "p2p_host_port": service.p2p_host_port,
            "rpc_url_on_host": service.rpc_url_on_host,
            "coolify_url": hosts[service.host].coolify_url if service.host in hosts else "",
        }
        for service in plan.services
    }


def discover_coolify_topology(plan: NetworkPlan, args: argparse.Namespace) -> dict[str, Any]:
    available = instance_catalog(plan)
    deployed: set[str] = set()
    host_results: dict[str, Any] = {}
    warnings: list[str] = []

    for host in plan.hosts:
        local_services = list(services_for_host(plan, host.id))
        expected_service_name = project_service_name(plan, host.id)
        host_result: dict[str, Any] = {
            "ok": False,
            "host_id": host.id,
            "coolify_url": host.coolify_url,
            "service_name": expected_service_name,
            "expected_instances": [service.id for service in local_services],
            "found": False,
            "deployed_instances": [],
            "warnings": [],
        }
        host_results[host.id] = host_result
        try:
            client, token, token_source = coolify_client_from_args(args, plan, host_id=host.id)
            host_result["token_source"] = token_source
            host_result["token_seen"] = bool(token)

            version = client.request("GET", "/api/v1/version")
            host_result["version"] = coolify_response_summary(version)
            if not version.ok:
                host_result["stage"] = "version"
                continue

            services_response, services = coolify_list(
                client,
                args,
                "/api/v1/services",
                label=f"services:{host.id}",
                preferred_keys=("services",),
            )
            host_result["services_response"] = coolify_response_summary(services_response)
            if not services_response.ok:
                host_result["stage"] = "services"
                continue

            matches = [item for item in services if coolify_service_matches_name(item, expected_service_name)]
            host_result["matches"] = [coolify_item_summary(item) for item in matches]
            if len(matches) > 1:
                host_result["stage"] = "duplicate-service-name"
                host_result["warnings"].append(f"Multiple Coolify services named {expected_service_name!r} were returned.")
                warnings.append(f"Host {host.id}: multiple Coolify services named {expected_service_name!r}.")
                continue
            if not matches:
                host_result["ok"] = True
                host_result["found"] = False
                host_result["stage"] = "missing"
                continue

            match = matches[0]
            service_uuid = coolify_item_uuid(match)
            host_result["ok"] = True
            host_result["found"] = True
            host_result["service_uuid"] = service_uuid
            host_result["service"] = coolify_item_summary(match)

            compose = coolify_compose_text_from_payload(match)
            detail: dict[str, Any] = {}
            if compose:
                detail = {"ok": True, "source": "services-list-item", "compose": compose, "service_keys": compose_service_keys(compose), "tried": []}
            elif service_uuid:
                detail = coolify_service_detail(client, args, service_uuid)
            host_result["detail"] = {key: value for key, value in detail.items() if key != "compose"}

            service_keys = set(detail.get("service_keys") or [])
            if service_keys:
                host_result["compose_service_keys"] = sorted(service_keys)
                deployed_here = [
                    service.id
                    for service in local_services
                    if service.id.replace("_", "-") in service_keys
                ]
                host_result["deployed_instances"] = deployed_here
            else:
                # Compatibility path for already-deployed services that predate
                # richer labels/detail endpoints: a matching host stack proves the
                # host-level QBFT service exists, but not which internal compose
                # services are present. Infer only when the host has exactly one
                # configured instance; otherwise report the internal topology as
                # unknown rather than accidentally treating every available node as
                # deployed.
                if len(local_services) == 1:
                    host_result["deployed_instances"] = [local_services[0].id]
                    host_result["deployed_instance_source"] = "single-instance-host-stack-match-without-compose-detail"
                    host_result["warnings"].append(
                        "Coolify did not return compose detail; inferring the single configured instance on this host is deployed."
                    )
                    warnings.append(
                        f"Host {host.id}: compose detail unavailable for {expected_service_name}; inferred the single configured instance."
                    )
                else:
                    host_result["deployed_instances"] = []
                    host_result["unknown_instances"] = [service.id for service in local_services]
                    host_result["deployed_instance_source"] = "unknown-without-compose-detail"
                    host_result["warnings"].append(
                        "Coolify did not return compose detail; multiple configured instances share this host, so deployed instances are unknown."
                    )
                    warnings.append(
                        f"Host {host.id}: compose detail unavailable for {expected_service_name}; multiple configured instances make per-node discovery unknown."
                    )
            deployed.update(host_result["deployed_instances"])
        except Exception as exc:  # noqa: BLE001 - read-only operator report should include per-host errors
            host_result["stage"] = "exception"
            host_result["error"] = str(exc)
            host_result["error_type"] = type(exc).__name__

    return {
        "ok": all(item.get("ok") for item in host_results.values()),
        "hosts": host_results,
        "available_instances": available,
        "observed_deployed_instances": sorted(deployed),
        "observed_missing_instances": sorted(set(available) - deployed),
        "warnings": warnings,
    }


def discover_rpc_topology(plan: NetworkPlan, args: argparse.Namespace) -> dict[str, Any]:
    rpc_candidates = rpc_probe_url_candidates(plan, args)
    if not rpc_candidates:
        try:
            infer_external_rpc_url(plan, args)
        except PlanError as exc:
            return {"ok": True, "attempted": False, "reason": str(exc)}
        return {"ok": True, "attempted": False, "reason": "No RPC probe URL candidates were available."}

    result: dict[str, Any] = {
        "ok": False,
        "attempted": True,
        "rpc_candidates": rpc_candidates,
        "rpc_url": rpc_candidates[0]["url"],
    }
    rpc_timeout = float(getattr(args, "rpc_timeout_s", 8.0) or 8.0)
    rpc_user_agent = str(getattr(args, "rpc_user_agent", DEFAULT_JSON_RPC_USER_AGENT) or "").strip()
    errors: list[dict[str, str]] = []
    for candidate in rpc_candidates:
        rpc_url = candidate["url"]
        rpc_source = candidate["source"]
        try:
            chain_id = str(json_rpc(rpc_url, "eth_chainId", timeout_s=rpc_timeout, user_agent=rpc_user_agent))
            block_hex = str(json_rpc(rpc_url, "eth_blockNumber", timeout_s=rpc_timeout, user_agent=rpc_user_agent))
            peer_hex = str(json_rpc(rpc_url, "net_peerCount", timeout_s=rpc_timeout, user_agent=rpc_user_agent))
            result.update(
                {
                    "ok": True,
                    "rpc_url": rpc_url,
                    "rpc_url_source": rpc_source,
                    "chain_id": chain_id,
                    "expected_chain_id": hex(plan.chain_id),
                    "chain_id_matches": chain_id.lower() == hex(plan.chain_id).lower(),
                    "block_number": int(block_hex, 16),
                    "peer_count": int(peer_hex, 16),
                }
            )
            try:
                validators = json_rpc(
                    rpc_url,
                    "qbft_getValidatorsByBlockNumber",
                    ["latest"],
                    timeout_s=rpc_timeout,
                    user_agent=rpc_user_agent,
                )
                result["qbft_validators"] = validators if isinstance(validators, list) else []
                result["consensus_topology"] = {
                    "observed": True,
                    "validator_addresses": result["qbft_validators"],
                    "validator_instance_mapping": {
                        "available": False,
                        "reason": "validator address metadata is not yet recorded in the QBFT service catalog",
                    },
                }
            except Exception as exc:  # noqa: BLE001
                result["consensus_topology"] = {
                    "observed": False,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            return result
        except Exception as exc:  # noqa: BLE001
            errors.append({"url": rpc_url, "source": rpc_source, "error": str(exc), "error_type": type(exc).__name__})

    result.update({"ok": False, "errors": errors, "error": errors[-1]["error"] if errors else "unknown RPC probe failure"})
    return result


def verify_hub_from_qbft_discovery(plan: NetworkPlan, args: argparse.Namespace, rpc_topology: Mapping[str, Any]) -> dict[str, Any]:
    if not bool(getattr(args, "verify_hub", False)):
        return {"ok": True, "skipped": True, "reason": "--verify-hub was not provided"}

    hub_module = _load_hub_service_module()
    argv = ["verify", plan.name]
    hub_network_config = str(getattr(args, "hub_network_config", "") or "").strip()
    if hub_network_config:
        argv.extend(["--network-config", hub_network_config])

    rpc_url = str(rpc_topology.get("rpc_url") or getattr(args, "rpc_url", "") or "").strip()
    if rpc_url:
        argv.extend(["--verify-chain-rpc-url", rpc_url])

    argv.extend(["--rpc-check", str(getattr(args, "hub_rpc_check", "warn") or "warn")])
    argv.extend(["--hub-health-check", str(getattr(args, "hub_health_check", "warn") or "warn")])
    argv.extend(["--hub-wait-timeout-s", str(float(getattr(args, "hub_wait_timeout_s", 30.0)))])
    argv.extend(["--hub-wait-poll-s", str(float(getattr(args, "hub_wait_poll_s", 5.0)))])
    argv.extend(["--hub-status-timeout-s", str(float(getattr(args, "hub_status_timeout_s", 8.0)))])

    try:
        hub_args = hub_module.parse_args(argv)
        result = hub_module.verify(hub_args)
        return {"ok": bool(result.get("ok")), "skipped": False, "argv": argv, "result": result}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "skipped": False, "argv": argv, "error": str(exc), "error_type": type(exc).__name__}


def discover_topology_stage_summary(phase: str, result: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"phase": phase, "ok": bool(result.get("ok"))}
    if "skipped" in result:
        summary["skipped"] = bool(result.get("skipped"))
    if result.get("reason"):
        summary["reason"] = result.get("reason")
    if result.get("warnings"):
        summary["warnings"] = result.get("warnings")
    if phase == "discover-coolify-topology":
        hosts = result.get("hosts") if isinstance(result.get("hosts"), Mapping) else {}
        summary["hosts"] = {
            str(host_id): {
                "ok": bool(host_result.get("ok")) if isinstance(host_result, Mapping) else False,
                "found": bool(host_result.get("found")) if isinstance(host_result, Mapping) else False,
                "stage": host_result.get("stage") if isinstance(host_result, Mapping) else "unknown",
                "deployed_instances": host_result.get("deployed_instances", []) if isinstance(host_result, Mapping) else [],
                "service_name": host_result.get("service_name") if isinstance(host_result, Mapping) else "",
            }
            for host_id, host_result in hosts.items()
        }
        summary["observed_deployed_instances"] = result.get("observed_deployed_instances", [])
        summary["observed_missing_instances"] = result.get("observed_missing_instances", [])
    elif phase == "discover-rpc-topology":
        summary.update(
            {
                "attempted": bool(result.get("attempted")),
                "rpc_url": result.get("rpc_url", ""),
                "rpc_url_source": result.get("rpc_url_source", ""),
                "rpc_candidates": result.get("rpc_candidates", []),
                "chain_id_matches": result.get("chain_id_matches"),
                "block_number": result.get("block_number"),
                "peer_count": result.get("peer_count"),
                "consensus_observed": bool((result.get("consensus_topology") or {}).get("observed"))
                if isinstance(result.get("consensus_topology"), Mapping)
                else False,
            }
        )
    elif phase == "verify-hub":
        summary["argv"] = result.get("argv", [])
        if isinstance(result.get("result"), Mapping):
            hub_result = result.get("result") or {}
            summary["hub_ok"] = bool(hub_result.get("ok"))
            summary["hub_warnings"] = hub_result.get("warnings", [])
    return summary


def discover_topology(plan: NetworkPlan, args: argparse.Namespace) -> dict[str, Any]:
    operator_log(args, "discover-topology start", network=plan.name)
    coolify_topology = discover_coolify_topology(plan, args)
    rpc_topology = discover_rpc_topology(plan, args)
    hub_verification = verify_hub_from_qbft_discovery(plan, args, rpc_topology)
    stages = [
        discover_topology_stage_summary("discover-coolify-topology", coolify_topology),
        discover_topology_stage_summary("discover-rpc-topology", rpc_topology),
        discover_topology_stage_summary("verify-hub", hub_verification),
    ]
    ok = bool(coolify_topology.get("ok")) and bool(rpc_topology.get("ok")) and bool(hub_verification.get("ok"))
    return {
        "ok": ok,
        "action": "discover-topology",
        "network": plan.name,
        "available_instances": coolify_topology.get("available_instances", {}),
        "coolify_topology": {
            "hosts": coolify_topology.get("hosts", {}),
            "warnings": coolify_topology.get("warnings", []),
        },
        "observed_deployed_instances": coolify_topology.get("observed_deployed_instances", []),
        "observed_missing_instances": coolify_topology.get("observed_missing_instances", []),
        "rpc_topology": rpc_topology,
        "consensus_topology": rpc_topology.get("consensus_topology", {"observed": False}),
        "hub_verification": hub_verification,
        "stages": stages,
    }




def qbft_consensus_twiddle_hosts(plan: NetworkPlan, args: argparse.Namespace) -> list[str]:
    """Return Coolify host ids that should receive the read-only consensus twiddle."""

    hosts_by_id = host_by_id(plan)
    explicit = str(getattr(args, "twiddle_hosts", "") or "").strip()
    if explicit:
        requested = list(split_selected_ids(explicit, kind="host"))
    else:
        raw_instances = list(split_selected_ids(str(getattr(args, "instances", "") or ""), kind="instance"))
        if raw_instances:
            services = planned_service_by_id(plan)
            requested = []
            for instance_id in raw_instances:
                service = services.get(instance_id)
                if service is not None:
                    requested.append(service.host)
        else:
            requested = [host.id for host in plan.hosts]

    selected: list[str] = []
    seen: set[str] = set()
    for host_id in requested:
        clean = safe_id(host_id, kind="host")
        if clean not in hosts_by_id:
            raise PlanError(f"Unknown --twiddle-hosts host id: {host_id!r}")
        if clean not in seen:
            seen.add(clean)
            selected.append(clean)
    return selected


def qbft_consensus_twiddle_service_name(plan: NetworkPlan, host_id: str) -> str:
    host_id = safe_id(host_id, kind="host")
    return f"{project_service_name(plan, host_id)}-consensus-twiddle"


def qbft_consensus_twiddle_url(plan: NetworkPlan, host_id: str, token: str, port: int) -> str:
    hosts = host_by_id(plan)
    host = hosts.get(safe_id(host_id, kind="host"))
    if host is None:
        raise PlanError(f"Unknown twiddle host: {host_id!r}")
    address = str(host.address or "").strip()
    if not address:
        raise PlanError(f"Host {host_id!r} has no public address for twiddle result fetch")
    return f"http://{address}:{int(port)}/{urllib.parse.quote(token)}.json"


def plan_without_host_service_uuids(plan: NetworkPlan) -> NetworkPlan:
    """Return a plan copy that cannot accidentally update the normal QBFT services.

    coolify_sync normally falls back to PlannedHost.service_uuid for the host's
    managed QBFT service.  The consensus twiddle is an auxiliary service and must
    be discovered/created by its own service name instead.
    """

    return replace(plan, hosts=tuple(replace(host, service_uuid="") for host in plan.hosts))


def render_qbft_consensus_twiddle_shell(plan: NetworkPlan, host_id: str, token: str) -> str:
    """Return the shell/Python script that inspects host-local Docker consensus state.

    The script is deliberately read-only: it calls docker inspect/list commands,
    reads mounted Docker volume files through a read-only bind mount, and refuses
    to emit private-key-like file contents.
    """

    host_id = safe_id(host_id, kind="host")
    service_ids = [service.id for service in services_for_host(plan, host_id)]
    cfg = {
        "network": plan.name,
        "environment": plan.environment,
        "compose_project": plan.compose_project,
        "host_id": host_id,
        "service_ids": service_ids,
        "token": token,
    }
    cfg_json = json.dumps(cfg, sort_keys=True)
    return "\n".join(
        [
            "set -eu",
            "mkdir -p /out",
            "cat > /tmp/qbft-consensus-twiddle.json <<'JSON_QBFT_CONSENSUS_TWIDDLE'",
            cfg_json,
            "JSON_QBFT_CONSENSUS_TWIDDLE",
            "python3 - <<'PY_QBFT_CONSENSUS_TWIDDLE'",
            "import datetime as _dt",
            "import json",
            "import os",
            "import re",
            "import subprocess",
            "import sys",
            "from pathlib import Path",
            "",
            "with open('/tmp/qbft-consensus-twiddle.json', 'r', encoding='utf-8') as fh:",
            "    cfg = json.load(fh)",
            "",
            "def run(cmd, timeout=20):",
            "    try:",
            "        completed = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)",
            "        return {",
            "            'ok': completed.returncode == 0,",
            "            'returncode': completed.returncode,",
            "            'cmd': cmd,",
            "            'stdout': completed.stdout,",
            "            'stderr': completed.stderr,",
            "        }",
            "    except Exception as exc:",
            "        return {'ok': False, 'returncode': None, 'cmd': cmd, 'stdout': '', 'stderr': f'{type(exc).__name__}: {exc}'}",
            "",
            "def split_lines(value):",
            "    return [line.strip() for line in str(value or '').splitlines() if line.strip()]",
            "",
            "def marker_match(value, markers):",
            "    lower = str(value or '').lower()",
            "    return any(marker and marker.lower() in lower for marker in markers)",
            "",
            "def read_text_limited(path, limit=65536):",
            "    with open(path, 'rb') as fh:",
            "        raw = fh.read(limit + 1)",
            "    truncated = len(raw) > limit",
            "    raw = raw[:limit]",
            "    return raw.decode('utf-8', errors='replace'), truncated",
            "",
            "def is_private_like(rel_path):",
            "    rel = rel_path.replace('\\\\', '/').lower()",
            "    base = rel.rsplit('/', 1)[-1]",
            "    if base in {'key', 'nodekey', 'private-key', 'private.key', 'jwt.hex'}:",
            "        return True",
            "    if base.endswith(('.key', '.pem')):",
            "        return True",
            "    return any(part in {'keystore', 'secrets', 'secret', 'passwords'} for part in rel.split('/'))",
            "",
            "def is_safe_consensus_file(rel_path):",
            "    rel = rel_path.replace('\\\\', '/').lower()",
            "    base = rel.rsplit('/', 1)[-1]",
            "    exact = {",
            "        'genesis.json',",
            "        'static-nodes.json',",
            "        'static-nodes-all.json',",
            "        'network-metadata.json',",
            "        'validator-address',",
            "        'validator-address.txt',",
            "        'validator-addresses.json',",
            "    }",
            "    if base in exact:",
            "        return True",
            "    if base.endswith(('.json', '.toml', '.txt', '.pub', '.properties')) and ('/smoke/' in '/' + rel or rel.startswith('smoke/')):",
            "        return True",
            "    if base.endswith('.pub'):",
            "        return True",
            "    return False",
            "",
            "def file_record(path, root):",
            "    rel = os.path.relpath(path, root).replace('\\\\', '/')",
            "    try:",
            "        stat = os.stat(path)",
            "    except OSError as exc:",
            "        return {'path': rel, 'ok': False, 'error': f'{type(exc).__name__}: {exc}'}",
            "    private_like = is_private_like(rel)",
            "    safe = is_safe_consensus_file(rel)",
            "    record = {",
            "        'path': rel,",
            "        'ok': True,",
            "        'size': stat.st_size,",
            "        'private_like': private_like,",
            "        'content_included': bool(safe and not private_like),",
            "    }",
            "    if safe and not private_like:",
            "        try:",
            "            content, truncated = read_text_limited(path)",
            "            record['content'] = content",
            "            record['truncated'] = truncated",
            "            if rel.lower().endswith('.json'):",
            "                try:",
            "                    record['json'] = json.loads(content)",
            "                except Exception:",
            "                    pass",
            "        except Exception as exc:",
            "            record['ok'] = False",
            "            record['error'] = f'{type(exc).__name__}: {exc}'",
            "    return record",
            "",
            "apk = run(['/sbin/apk', 'add', '--no-cache', 'docker-cli'], timeout=120)",
            "markers = [cfg.get('compose_project', ''), 'main-computer-qbft', 'qbft', 'besu']",
            "markers.extend(str(item) for item in cfg.get('service_ids') or [])",
            "containers = []",
            "docker_available = apk['ok']",
            "if docker_available:",
            "    ids_result = run(['docker', 'ps', '-a', '--format', '{{.ID}}'])",
            "    container_ids = split_lines(ids_result.get('stdout'))",
            "    if container_ids:",
            "        inspect_result = run(['docker', 'inspect', *container_ids], timeout=30)",
            "        if inspect_result['ok']:",
            "            try:",
            "                for item in json.loads(inspect_result['stdout']):",
            "                    labels = ((item.get('Config') or {}).get('Labels') or {})",
            "                    compose_service = str(labels.get('com.docker.compose.service') or '')",
            "                    if compose_service == 'qbft-consensus-twiddle':",
            "                        continue",
            "                    name = str(item.get('Name') or '').lstrip('/')",
            "                    image = str(((item.get('Config') or {}).get('Image')) or '')",
            "                    state = item.get('State') or {}",
            "                    ports = item.get('NetworkSettings', {}).get('Ports') or {}",
            "                    blob = ' '.join([name, image, json.dumps(labels, sort_keys=True), json.dumps(ports, sort_keys=True)])",
            "                    if marker_match(blob, markers):",
            "                        containers.append({",
            "                            'id': str(item.get('Id') or '')[:12],",
            "                            'name': name,",
            "                            'image': image,",
            "                            'status': state.get('Status'),",
            "                            'running': bool(state.get('Running')),",
            "                            'labels': {",
            "                                key: labels.get(key)",
            "                                for key in sorted(labels)",
            "                                if key.startswith('com.docker.compose') or key.startswith('coolify') or key.startswith('main-computer')",
            "                            },",
            "                            'ports': ports,",
            "                        })",
            "            except Exception as exc:",
            "                containers.append({'ok': False, 'error': f'container inspect parse failed: {type(exc).__name__}: {exc}'})",
            "else:",
            "    ids_result = {'ok': False, 'stderr': 'docker-cli was not installed'}",
            "",
            "volumes = []",
            "volume_files = []",
            "if docker_available:",
            "    volume_result = run(['docker', 'volume', 'ls', '--format', '{{.Name}}'])",
            "    for volume_name in split_lines(volume_result.get('stdout')):",
            "        if not marker_match(volume_name, markers):",
            "            continue",
            "        root = Path('/host-docker-volumes') / volume_name / '_data'",
            "        volume_record = {'name': volume_name, 'mount_probe': str(root), 'mount_readable': root.is_dir()}",
            "        volumes.append(volume_record)",
            "        if not root.is_dir():",
            "            continue",
            "        found_for_volume = []",
            "        skipped_dirs = {'database', 'caches', 'cache', 'logs', 'lost+found'}",
            "        for dirpath, dirnames, filenames in os.walk(root):",
            "            rel_dir = os.path.relpath(dirpath, root).replace('\\\\', '/')",
            "            depth = 0 if rel_dir == '.' else rel_dir.count('/') + 1",
            "            dirnames[:] = [d for d in dirnames if d not in skipped_dirs and depth < 5]",
            "            for filename in filenames:",
            "                rel_path = os.path.relpath(os.path.join(dirpath, filename), root).replace('\\\\', '/')",
            "                if not is_safe_consensus_file(rel_path) and not is_private_like(rel_path):",
            "                    continue",
            "                found_for_volume.append(file_record(os.path.join(dirpath, filename), str(root)))",
            "                if len(found_for_volume) >= 200:",
            "                    break",
            "            if len(found_for_volume) >= 200:",
            "                break",
            "        volume_files.append({'volume': volume_name, 'files': found_for_volume})",
            "else:",
            "    volume_result = {'ok': False, 'stderr': 'docker-cli was not installed'}",
            "",
            "static_node_files = []",
            "for bundle in volume_files:",
            "    for item in bundle.get('files') or []:",
            "        if str(item.get('path') or '').endswith(('static-nodes.json', 'static-nodes-all.json')):",
            "            entry = {'volume': bundle.get('volume'), 'path': item.get('path'), 'content_included': item.get('content_included')}",
            "            if isinstance(item.get('json'), list):",
            "                entry['count'] = len(item['json'])",
            "                entry['enodes'] = item['json']",
            "            static_node_files.append(entry)",
            "",
            "warnings = []",
            "if docker_available and not containers:",
            "    warnings.append('no QBFT/Besu-looking containers found on this host')",
            "if docker_available and not volumes:",
            "    warnings.append('no QBFT/Besu-looking Docker volumes found on this host')",
            "if volumes and not static_node_files:",
            "    warnings.append('QBFT-looking volumes exist, but no static-nodes files were found')",
            "",
            "payload = {",
            "    'ok': True,",
            "    'action': 'twiddle-consensus',",
            "    'generated_at': _dt.datetime.now(_dt.timezone.utc).isoformat(),",
            "    'network': cfg.get('network'),",
            "    'environment': cfg.get('environment'),",
            "    'host_id': cfg.get('host_id'),",
            "    'compose_project': cfg.get('compose_project'),",
            "    'service_ids_for_host': cfg.get('service_ids') or [],",
            "    'docker_cli_install': {key: apk.get(key) for key in ('ok', 'returncode', 'stderr')},",
            "    'docker_available': docker_available,",
            "    'containers': containers,",
            "    'volumes': volumes,",
            "    'volume_files': volume_files,",
            "    'static_node_files': static_node_files,",
            "    'warnings': warnings,",
            "}",
            "out_path = Path('/out') / (cfg['token'] + '.json')",
            "out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')",
            "print(json.dumps({'ok': True, 'wrote': str(out_path), 'warnings': warnings}, sort_keys=True))",
            "PY_QBFT_CONSENSUS_TWIDDLE",
            "python3 - <<'PY_QBFT_CONSENSUS_TWIDDLE_HTTP'",
            "import json",
            "from pathlib import Path",
            "from http.server import BaseHTTPRequestHandler, HTTPServer",
            "",
            "with open('/tmp/qbft-consensus-twiddle.json', 'r', encoding='utf-8') as fh:",
            "    cfg = json.load(fh)",
            "token = cfg['token']",
            "payload_path = Path('/out') / (token + '.json')",
            "",
            "class Handler(BaseHTTPRequestHandler):",
            "    def do_GET(self):",
            "        if self.path != '/' + token + '.json':",
            "            self.send_response(404)",
            "            self.end_headers()",
            "            self.wfile.write(b'not found')",
            "            return",
            "        data = payload_path.read_bytes()",
            "        self.send_response(200)",
            "        self.send_header('Content-Type', 'application/json')",
            "        self.send_header('Content-Length', str(len(data)))",
            "        self.end_headers()",
            "        self.wfile.write(data)",
            "    def log_message(self, fmt, *args):",
            "        print('twiddle-http ' + (fmt % args))",
            "",
            "HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()",
            "PY_QBFT_CONSENSUS_TWIDDLE_HTTP",
        ]
    )


def render_qbft_consensus_twiddle_compose(plan: NetworkPlan, host_id: str, token: str, port: int) -> str:
    host_id = safe_id(host_id, kind="host")
    port = int(port)
    if port <= 0 or port > 65535:
        raise PlanError("--twiddle-port must be between 1 and 65535")
    script = escape_compose_interpolation(render_qbft_consensus_twiddle_shell(plan, host_id, token))
    lines = [
        f"name: {plan.compose_project}-{host_id}-consensus-twiddle",
        "",
        "services:",
        "  qbft-consensus-twiddle:",
        f"    image: {yaml_quote(QBFT_CONSENSUS_TWIDDLE_IMAGE)}",
        "    restart: unless-stopped",
        "    exclude_from_hc: true",
        "    labels:",
        "      - \"traefik.enable=false\"",
        "      - \"main-computer.qbft-consensus-twiddle=true\"",
        "    ports:",
        f"      - {yaml_quote(f'0.0.0.0:{port}:8080')}",
        "    volumes:",
        "      - \"/var/run/docker.sock:/var/run/docker.sock\"",
        "      - \"/var/lib/docker/volumes:/host-docker-volumes:ro\"",
        "    entrypoint:",
        "      - /bin/sh",
        "      - -ec",
        "      - |-",
    ]
    lines.extend([f"        {line}" if line else "" for line in script.splitlines()])
    return "\n".join(lines) + "\n"


def run_coolify_consensus_twiddle(plan: NetworkPlan, args: argparse.Namespace) -> dict[str, Any]:
    """Run a Coolify-native, read-only consensus-state inspection twiddle per host."""

    configure_local_coolify_defaults(plan, args)
    host_ids = qbft_consensus_twiddle_hosts(plan, args)
    token_by_host = {host_id: secrets.token_urlsafe(18) for host_id in host_ids}
    port = int(getattr(args, "twiddle_port", DEFAULT_QBFT_CONSENSUS_TWIDDLE_PORT))
    timeout_s = float(getattr(args, "twiddle_timeout_s", DEFAULT_QBFT_CONSENSUS_TWIDDLE_TIMEOUT_S))
    dry_run = bool(getattr(args, "dry_run", False))
    no_deploy = bool(getattr(args, "no_deploy", False))
    aux_plan = plan_without_host_service_uuids(plan)
    phases: list[dict[str, Any]] = []
    results: dict[str, Any] = {}
    operator_log(args, "twiddle-consensus start", network=plan.name, hosts=",".join(host_ids), port=port)

    for host_id in host_ids:
        token = token_by_host[host_id]
        service_name = qbft_consensus_twiddle_service_name(plan, host_id)
        result_url = qbft_consensus_twiddle_url(plan, host_id, token, port)
        compose = render_qbft_consensus_twiddle_compose(plan, host_id, token, port)
        twiddle_args = args_copy_with(
            args,
            host=host_id,
            coolify_service_name=service_name,
            coolify_service_uuid="",
            _compose_override=compose,
        )
        sync_result = coolify_sync(aux_plan, twiddle_args, deploy=not no_deploy)
        phase = {
            "phase": "deploy-consensus-twiddle",
            "host": host_id,
            "service_name": service_name,
            "result_url": result_url,
            "result": sync_result,
        }
        phases.append(phase)
        if dry_run or no_deploy:
            results[host_id] = {
                "ok": bool(sync_result.get("ok")),
                "dry_run": dry_run,
                "no_deploy": no_deploy,
                "service_name": service_name,
                "result_url": result_url,
                "compose": compose if dry_run else "",
                "message": "Consensus twiddle rendered but not fetched because deployment was skipped.",
            }
            continue
        if not sync_result.get("ok"):
            results[host_id] = {
                "ok": False,
                "service_name": service_name,
                "result_url": result_url,
                "error": "Coolify sync/deploy failed before twiddle result could be fetched.",
                "sync": sync_result,
            }
            continue

        deadline = time.time() + timeout_s
        last_error = ""
        payload: Any = None
        while time.time() < deadline:
            try:
                payload = fetch_json_url(result_url, timeout_s=min(10.0, max(2.0, timeout_s / 12.0)))
                if not isinstance(payload, Mapping):
                    raise RuntimeError(f"twiddle returned non-object payload: {type(payload).__name__}")
                break
            except Exception as exc:  # noqa: BLE001 - return all fetch diagnostics in JSON.
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(2.0)
        if isinstance(payload, Mapping):
            results[host_id] = {
                "ok": bool(payload.get("ok")),
                "service_name": service_name,
                "result_url": result_url,
                "payload": payload,
            }
            phases.append({"phase": "fetch-consensus-twiddle", "host": host_id, "result": {"ok": bool(payload.get("ok")), "result_url": result_url}})
        else:
            results[host_id] = {
                "ok": False,
                "service_name": service_name,
                "result_url": result_url,
                "error": last_error or "twiddle result fetch timed out",
            }
            phases.append({"phase": "fetch-consensus-twiddle", "host": host_id, "result": {"ok": False, "result_url": result_url, "error": last_error}})

    ok = all(bool(item.get("ok")) for item in results.values()) if results else False
    return {
        "ok": ok,
        "action": "twiddle-consensus",
        "network": plan.name,
        "hosts": host_ids,
        "port": port,
        "dry_run": dry_run,
        "mode": "coolify-read-only-diagnostic-sidecar",
        "warning": (
            "This twiddle is read-only with respect to consensus files and Docker objects, but it creates/updates "
            "temporary Coolify diagnostic services and exposes tokenized JSON result endpoints on the selected host port."
        ),
        "results": results,
        "phases": phases,
    }

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
    host_id: str | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    host = coolify_host_for_action(plan, host_id)
    project_uuid = str(getattr(args, "coolify_project_uuid", "") or (host.project_uuid if host else "") or "").strip()
    server_uuid = str(getattr(args, "coolify_server_uuid", "") or (host.server_uuid if host else "") or "").strip()
    destination_uuid = str(getattr(args, "coolify_destination_uuid", "") or (host.destination_uuid if host else "") or "").strip()
    environment_name = str(getattr(args, "coolify_environment", "") or plan.environment).strip() or "test"
    environment_uuid = str(getattr(args, "coolify_environment_uuid", "") or (host.environment_uuid if host else "") or "").strip()
    context: dict[str, Any] = {
        "project_uuid": project_uuid,
        "server_uuid": server_uuid,
        "destination_uuid": destination_uuid,
        "environment_name": environment_name,
        "environment_uuid": environment_uuid,
        **({"host": host.id} if host else {}),
    }

    if not project_uuid:
        response, projects = coolify_list(client, args, "/api/v1/projects", label="projects", preferred_keys=("projects",))
        tried.append({"operation": "list-projects", "response": response_to_dict(response), "count": len(projects)})
        if response.ok:
            project_uuid, selection = choose_coolify_uuid(
                explicit_uuid="",
                explicit_name=str(getattr(args, "coolify_project_name", "") or (host.project_name if host else "") or ""),
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
                explicit_name=str(getattr(args, "coolify_server_name", "") or (host.server_name if host else "") or ""),
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


def rpc_direct_url_candidates(plan: NetworkPlan) -> list[str]:
    """Return operator-reachable host-port RPC URLs derived from the service plan."""

    hosts = host_by_id(plan)
    candidates: list[str] = []
    for service in plan.services:
        if service.rpc_host_port is None or not service.rpc_url_on_host:
            continue
        if is_local_coolify_plan(plan):
            candidates.append(service.rpc_url_on_host)
            continue
        host = hosts.get(service.host)
        if service.rpc_bind_host == "0.0.0.0" and host is not None and host.address:
            candidates.append(service.rpc_url_on_host)
    return list(dict.fromkeys(candidates))


def rpc_probe_url_candidates(plan: NetworkPlan, args: argparse.Namespace) -> list[dict[str, str]]:
    """Return RPC URLs to probe, preferring generated host-port URLs for apply.

    networks.<network>.rpc is the stable public/user-facing RPC surface, but it can
    legitimately lag behind a just-created Coolify service while DNS/proxy/domain
    routing is being wired.  The deploy readiness check should first prove the
    node's published host port, then fall back to the configured public URL.
    """

    explicit = str(getattr(args, "rpc_url", "") or "").strip()
    if explicit:
        return [{"url": explicit, "source": "explicit"}]

    candidates: list[dict[str, str]] = []
    for url in rpc_direct_url_candidates(plan):
        candidates.append({"url": url, "source": "direct-host-port"})
    configured = str(getattr(plan, "external_rpc_url", "") or "").strip()
    if configured:
        candidates.append({"url": configured, "source": "configured-network-rpc"})

    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for candidate in candidates:
        url = str(candidate.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append({"url": url, "source": str(candidate.get("source") or "unknown")})
    return unique


def infer_external_rpc_url(plan: NetworkPlan, args: argparse.Namespace) -> str:
    explicit = str(getattr(args, "rpc_url", "") or "").strip()
    if explicit:
        return explicit
    configured = str(getattr(plan, "external_rpc_url", "") or "").strip()
    if configured:
        return configured
    candidates = rpc_direct_url_candidates(plan)
    if candidates:
        return candidates[0]
    raise PlanError(
        "RPC is not externally reachable from the operator machine. Pass --rpc-url "
        "or declare networks.<network>.rpc / a public RPC host port in private state."
    )


def rpc_url_with_default_port(url: str) -> str:
    """Return an http(s) RPC URL with an explicit port for dev-chain-reset.py.

    The contract deployment helper validates that --host-rpc-url includes a port.
    User-facing HTTPS URLs commonly omit :443, so normalize those only for the
    deployment subprocess while preserving the operator-facing configured RPC URL.
    """

    clean = str(url or "").strip()
    parsed = urllib.parse.urlsplit(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.port is not None:
        return clean

    port = 443 if parsed.scheme == "https" else 80
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
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path or "", parsed.query, parsed.fragment))


def contract_deployment_rpc_candidates(plan: NetworkPlan, args: argparse.Namespace) -> list[dict[str, str]]:
    """Return ordered RPC candidates for hosted contract deployment.

    Direct host-port RPC is fastest when the operator machine can reach it, but
    hosted firewalls may expose only the canonical HTTPS RPC.  Keep direct first,
    then fall back to the configured public RPC with an explicit default port.
    """

    explicit = str(getattr(args, "rpc_url", "") or "").strip()
    if explicit:
        return [{"url": rpc_url_with_default_port(explicit), "source": "explicit"}]

    candidates: list[dict[str, str]] = []
    for url in rpc_direct_url_candidates(plan):
        candidates.append({"url": rpc_url_with_default_port(url), "source": "direct-host-port"})
    configured = str(getattr(plan, "external_rpc_url", "") or "").strip()
    if configured:
        candidates.append({"url": rpc_url_with_default_port(configured), "source": "configured-network-rpc"})

    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for candidate in candidates:
        url = str(candidate.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append({"url": url, "source": str(candidate.get("source") or "unknown")})
    return unique


def select_contract_deployment_rpc_url(plan: NetworkPlan, args: argparse.Namespace) -> str:
    """Select a reachable RPC URL for deploy-contracts.

    For non-dry-run deployments, probe inferred candidates before handing one to
    dev-chain-reset.py.  This prevents a blocked direct host-port URL from causing
    a long downstream wait when the canonical HTTPS endpoint is already healthy.
    """

    candidates = contract_deployment_rpc_candidates(plan, args)
    if not candidates:
        # Reuse the operator-facing error from the legacy single-URL helper.
        return rpc_url_with_default_port(infer_external_rpc_url(plan, args))

    explicit = str(getattr(args, "rpc_url", "") or "").strip()
    if explicit or bool(getattr(args, "dry_run", False)):
        return candidates[0]["url"]

    expected_chain_id = hex(plan.chain_id).lower()
    rpc_user_agent = str(getattr(args, "rpc_user_agent", DEFAULT_JSON_RPC_USER_AGENT) or "").strip()
    probe_timeout_s = min(5.0, max(0.5, chain_observation_timeout_from_args(args)))
    errors: list[str] = []
    for candidate in candidates:
        url = str(candidate.get("url") or "").strip()
        source = str(candidate.get("source") or "unknown").strip() or "unknown"
        try:
            chain_id = str(json_rpc(url, "eth_chainId", timeout_s=probe_timeout_s, user_agent=rpc_user_agent))
            if chain_id.lower() != expected_chain_id:
                raise RuntimeError(f"expected chain id {expected_chain_id}, got {chain_id}")
            operator_log(args, "deploy-contracts rpc selected", rpc_url=url, rpc_url_source=source)
            return url
        except Exception as exc:  # noqa: BLE001 - capture every candidate failure for the operator.
            errors.append(f"{source} {url}: {type(exc).__name__}: {exc}")
            operator_log(args, "deploy-contracts rpc candidate failed", rpc_url=url, rpc_url_source=source, error=exc)

    raise PlanError(
        "No inferred deploy-contracts RPC URL is reachable from the operator machine. "
        "Pass --rpc-url explicitly or fix the public/direct RPC surface. "
        f"Candidate errors: {' | '.join(errors)}"
    )


def json_rpc(
    url: str,
    method: str,
    params: list[Any] | None = None,
    *,
    timeout_s: float = 8.0,
    user_agent: str = DEFAULT_JSON_RPC_USER_AGENT,
) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    clean_user_agent = str(user_agent or "").strip()
    if clean_user_agent:
        headers["User-Agent"] = clean_user_agent
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise RuntimeError(f"JSON-RPC {method} failed for {url}: {type(exc).__name__}: {exc}") from exc
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
    rpc_candidates = rpc_probe_url_candidates(plan, args)
    if not rpc_candidates:
        # Reuse the operator-facing error from the legacy single-URL helper.
        infer_external_rpc_url(plan, args)

    expected_chain_id = hex(plan.chain_id)
    timeout_s = float(getattr(args, "rpc_timeout_s", DEFAULT_RPC_WAIT_TIMEOUT_S))
    poll_interval_s = float(getattr(args, "rpc_poll_interval_s", DEFAULT_RPC_POLL_INTERVAL_S))
    min_peers = rpc_min_peers_from_args(plan, args)
    require_block_advance = not bool(getattr(args, "no_rpc_require_block_advance", False))
    rpc_user_agent = str(getattr(args, "rpc_user_agent", DEFAULT_JSON_RPC_USER_AGENT) or "").strip()
    deadline = None if timeout_s <= 0 else time.monotonic() + timeout_s
    last_error: object = None
    last_chain_id = ""
    last_block_number: int | None = None
    last_peer_count: int | None = None
    first_observed_block_by_url: dict[str, int] = {}
    block_advanced_by_url: dict[str, bool] = {}
    attempt = 0
    operator_log(
        args,
        "wait-rpc start",
        rpc_urls=",".join(candidate["url"] for candidate in rpc_candidates),
        expected_chain_id=expected_chain_id,
        timeout_s=timeout_s,
        min_peers=min_peers,
        require_block_advance=require_block_advance,
    )
    while deadline is None or time.monotonic() < deadline:
        attempt += 1
        attempt_errors: list[str] = []
        for candidate in rpc_candidates:
            rpc_url = candidate["url"]
            rpc_source = candidate["source"]
            try:
                chain_id = str(json_rpc(rpc_url, "eth_chainId", timeout_s=8.0, user_agent=rpc_user_agent))
                last_chain_id = chain_id
                if chain_id.lower() != expected_chain_id.lower():
                    raise RuntimeError(f"expected chain id {expected_chain_id}, got {chain_id}")
                block_hex = str(json_rpc(rpc_url, "eth_blockNumber", timeout_s=8.0, user_agent=rpc_user_agent))
                block_number = int(block_hex, 16)
                peer_hex = str(json_rpc(rpc_url, "net_peerCount", timeout_s=8.0, user_agent=rpc_user_agent))
                peer_count = int(peer_hex, 16)
                last_block_number = block_number
                last_peer_count = peer_count
                if rpc_url not in first_observed_block_by_url:
                    first_observed_block_by_url[rpc_url] = block_number
                    block_advanced_by_url[rpc_url] = False
                if block_number > first_observed_block_by_url[rpc_url]:
                    block_advanced_by_url[rpc_url] = True
                first_observed_block = first_observed_block_by_url[rpc_url]
                block_advanced = block_advanced_by_url[rpc_url]
                operator_log(
                    args,
                    "wait-rpc probe",
                    attempt=attempt,
                    rpc_url=rpc_url,
                    rpc_url_source=rpc_source,
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
                        rpc_url=rpc_url,
                        rpc_url_source=rpc_source,
                        block_number=block_number,
                        peer_count=peer_count,
                        first_observed_block=first_observed_block,
                        block_advanced=block_advanced,
                    )
                    return {
                        "ok": True,
                        "rpc_url": rpc_url,
                        "rpc_url_source": rpc_source,
                        "rpc_candidates": rpc_candidates,
                        "chain_id": chain_id,
                        "block_number": block_number,
                        "peer_count": peer_count,
                        "first_observed_block": first_observed_block,
                        "block_advanced": block_advanced,
                        "min_peers": min_peers,
                    }

                last_error = "; ".join(not_ready_reasons)
                attempt_errors.append(f"{rpc_source} {rpc_url}: {last_error}")
            except Exception as exc:  # noqa: BLE001 - operator-facing retry loop
                last_error = str(exc)
                attempt_errors.append(f"{rpc_source} {rpc_url}: {last_error}")
        operator_log(args, "wait-rpc retry", attempt=attempt, error=" | ".join(attempt_errors))
        time.sleep(poll_interval_s)

    first_observed_blocks = {url: value for url, value in first_observed_block_by_url.items()}
    block_advanced = {url: value for url, value in block_advanced_by_url.items()}
    raise CoolifyError(
        f"Timed out waiting for RPC candidates {rpc_candidates!r}; "
        f"last_chain_id={last_chain_id!r}; "
        f"last_block_number={last_block_number!r}; "
        f"last_peer_count={last_peer_count!r}; "
        f"first_observed_blocks={first_observed_blocks!r}; "
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


def should_generate_offices_for_deployment(plan: NetworkPlan, args: argparse.Namespace) -> bool:
    explicit_yes = bool(getattr(args, "generate_offices", False))
    explicit_no = bool(getattr(args, "no_generate_offices", False))
    if explicit_yes and explicit_no:
        raise PlanError("--generate-offices and --no-generate-offices cannot be combined")
    if explicit_yes:
        return True
    if explicit_no:
        return False
    return plan.environment in DEFAULT_GENERATED_OFFICE_ENVIRONMENTS


def clean_deployment_private_key_env(args: argparse.Namespace) -> str:
    env_name = str(getattr(args, "deployment_private_key_env", "") or "").strip()
    if not env_name:
        return ""
    if not DEPLOYMENT_ENV_VAR_RE.fullmatch(env_name):
        raise PlanError("--deployment-private-key-env must be a valid environment variable name")
    return env_name


def clean_deployment_offices(args: argparse.Namespace) -> str:
    raw = str(getattr(args, "deployment_offices", "") or "").strip()
    if not raw:
        return ""
    offices = [part.strip() for part in raw.split(",") if part.strip()]
    if len(offices) != 4:
        raise PlanError("--deployment-offices must contain exactly four comma-separated addresses")
    invalid = [office for office in offices if DEPLOYMENT_ADDRESS_RE.fullmatch(office) is None]
    if invalid:
        raise PlanError(f"--deployment-offices contains invalid address(es): {', '.join(invalid)}")
    default_anvil = [office for office in offices if office.lower() in DEFAULT_ANVIL_ACCOUNT_SET]
    if default_anvil:
        raise PlanError("--deployment-offices must not contain default Anvil addresses for a hosted deployment")
    return ",".join(offices)


def is_mainnet_deployment(plan: NetworkPlan, environment: str) -> bool:
    return plan.environment == "mainnet" or environment == "mainnet" or plan.chain_id == 42424240


def validate_hosted_contract_deployment_authority(
    plan: NetworkPlan,
    args: argparse.Namespace,
    *,
    environment: str,
    generate_offices: bool,
    private_key_env: str,
    offices: str,
) -> None:
    if not is_mainnet_deployment(plan, environment):
        return
    if bool(getattr(args, "dry_run", False)):
        return
    if not private_key_env:
        raise PlanError(
            "mainnet deploy-contracts requires --deployment-private-key-env so the deployer key "
            "is read from an environment variable instead of the dev-chain default"
        )
    if not generate_offices and not offices:
        raise PlanError(
            "mainnet deploy-contracts requires --deployment-offices with four explicit non-Anvil "
            "office addresses, unless --generate-offices is intentionally set"
        )


def deploy_contracts(plan: NetworkPlan, args: argparse.Namespace) -> dict[str, Any]:
    configure_local_coolify_defaults(plan, args)
    rpc_url = select_contract_deployment_rpc_url(plan, args)
    container_rpc_url = infer_container_rpc_url(plan, args, rpc_url)
    operator_log(args, "deploy-contracts start", rpc_url=rpc_url, container_rpc_url=container_rpc_url)
    run_id = str(getattr(args, "deployment_run_id", "") or f"coolify-qbft-{plan.name}-{_dt.datetime.now(_dt.timezone.utc).strftime('%Y%m%d%H%M%S')}")
    deployment_output = str(getattr(args, "deployment_output_dir", "") or (Path("runtime") / "deployments"))
    project_name = str(getattr(args, "deployment_project_name", "") or plan.compose_project)
    environment = str(getattr(args, "deployment_environment", "") or plan.environment)
    source_kind = deployment_source_kind(plan, args)
    private_key_env = clean_deployment_private_key_env(args)
    offices = clean_deployment_offices(args)
    generate_offices = should_generate_offices_for_deployment(plan, args)
    validate_hosted_contract_deployment_authority(
        plan,
        args,
        environment=environment,
        generate_offices=generate_offices,
        private_key_env=private_key_env,
        offices=offices,
    )
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
    if private_key_env:
        command.extend(["--private-key-env", private_key_env])
    if offices:
        command.extend(["--offices", offices])
    if generate_offices:
        command.append("--generate-offices")
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
    raw_host_id = str(getattr(args, "host", "") or "")
    host_id = safe_id(raw_host_id, kind="host") if raw_host_id else single_host_id(plan)
    operator_log(args, "coolify-sync render-compose start", network=plan.name, host=host_id)
    compose_override = str(getattr(args, "_compose_override", "") or "")
    if compose_override:
        compose = compose_override
    else:
        compose = render_compose_for_host(
            plan,
            host_id,
            include_bootstrap=not bool(getattr(args, "no_bootstrap", False)),
            managed_volume=not bool(getattr(args, "bind_runtime_root", False)),
            runtime_import_bundle=getattr(args, "_runtime_import_bundle", None),
            include_rpc_public_entry=bool(getattr(args, "_include_rpc_public_entry", True)),
            config_export=getattr(args, "_config_export", None),
            retire_cleanup_services=tuple(getattr(args, "_retire_cleanup_services", ()) or ()),
            validator_vote=getattr(args, "_validator_vote", None),
            static_refresh=getattr(args, "_qbft_static_refresh", None),
            park_inactive_helpers=bool(getattr(args, "_park_inactive_qbft_helpers", True)),
        )
    compose_b64 = base64_compose(compose)
    service_name = str(getattr(args, "coolify_service_name", "") or project_service_name(plan, host_id))
    host = host_by_id(plan).get(host_id)
    service_uuid = str(getattr(args, "coolify_service_uuid", "") or (host.service_uuid if host else "") or "").strip()
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

    setattr(args, "_coolify_host_id", host_id)
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
        create_refs, create_context = resolve_coolify_create_context(plan=plan, args=args, client=client, tried=tried, host_id=host_id)
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
    operator_log(args, "apply start", network=plan.name, dry_run=bool(getattr(args, "dry_run", False)))
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
    if bool(getattr(args, "deploy_contracts", False)):
        operator_log(args, "apply phase start", phase="deploy-contracts")
        contract_result = deploy_contracts(plan, args)
        phases.append({"phase": "deploy-contracts", "result": contract_result})
        operator_log(args, "apply phase result", phase="deploy-contracts", ok=contract_result.get("ok"), returncode=contract_result.get("returncode"))
        if not contract_result.get("ok"):
            return {"ok": False, "network": plan.name, "phases": phases}
    operator_log(args, "apply done", ok=True)
    return {"ok": True, "network": plan.name, "phases": phases}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan, render, and apply Coolify-managed Besu QBFT network layouts.", allow_abbrev=False)
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
            "discover-topology",
            "twiddle-consensus",
            "observe-chain",
            "coolify-sync",
            "wait-rpc",
            "deploy-contracts",
            "apply",
            "mutate",
        ],
        help="Action to run. Omit this to print the step-by-step deployment runbook.",
    )
    parser.add_argument("network", nargs="?", default="testnet", help="Seed name from NETWORK_SEEDS or a JSON seed path.")
    parser.add_argument(
        "--private-state",
        default="",
        help="Private YAML path to use for networks.<network>.qbft.instances. Defaults to runtime/state/main_computer.private.yaml.",
    )
    parser.add_argument(
        "--instances",
        default="",
        help=(
            "Comma-separated logical QBFT instance ids to deploy/discover from private state. "
            "The Coolify host is inferred from each selected instance."
        ),
    )
    parser.add_argument(
        "--add",
        default="",
        help="For mutate: comma-separated logical QBFT instance ids to add safely.",
    )
    parser.add_argument(
        "--retire",
        default="",
        help="For mutate: comma-separated logical QBFT instance ids to retire safely.",
    )
    parser.add_argument(
        "--promote",
        default="",
        help="For mutate: comma-separated instance:role promotions, e.g. rpc-1:validator.",
    )
    parser.add_argument(
        "--demote",
        default="",
        help="For mutate: comma-separated instance:role demotions, e.g. validator-rpc-1:rpc-only.",
    )
    parser.add_argument("--plan", action="store_true", help="For mutate: emit a read-only mutation packet. This is the default.")
    parser.add_argument("--apply", action="store_true", help="For mutate: execute the packet. Currently refused until executor support lands.")
    parser.add_argument("--packet", default="", help="For mutate: optional JSON path to write the read-only mutation packet.")
    parser.add_argument("--observe-chain", action="store_true", help="For mutate: include read-only chain/RPC observation in the mutation packet.")
    parser.add_argument("--chain-observation-timeout-s", type=float, default=DEFAULT_CHAIN_OBSERVATION_TIMEOUT_S, help="Per-call JSON-RPC timeout for observe-chain / mutate --observe-chain.")
    parser.add_argument("--ack-consensus-change", action="store_true", help="For future mutate --apply validator changes.")
    parser.add_argument("--ack-mainnet-consensus-change", action="store_true", help="For future mutate --apply mainnet validator changes.")
    parser.add_argument("--config-root-host", default="", help="For mutate --apply: force the QBFT runtime config/genesis source host when multiple lineages are found.")
    parser.add_argument("--config-export-port", type=int, default=DEFAULT_QBFT_CONFIG_EXPORT_PORT, help="Temporary host port used to slurp non-secret QBFT runtime config from the selected source host.")
    parser.add_argument("--config-export-timeout-s", type=float, default=DEFAULT_QBFT_CONFIG_EXPORT_TIMEOUT_S, help="Seconds to wait for the temporary QBFT config export service to become reachable.")
    parser.add_argument("--config-export-transport", default=DEFAULT_QBFT_CONFIG_EXPORT_TRANSPORT, choices=["public-entry", "direct-port"], help="Transport for slurping current QBFT runtime config. public-entry redeploys the source QBFT stack with a temporary tokenized public-entry route; direct-port uses the older temporary host port.")
    parser.add_argument("--host", default="", help="Optional host id for compose rendering/debug. Normal deploys infer this from --instances.")
    parser.add_argument("--out", default="", help="Output directory for the write action.")
    parser.add_argument("--besu-image", default="", help="Override the Besu image tag in the seed.")
    parser.add_argument("--public-rpc", action="store_true", help="Bind the non-validator RPC host port to 0.0.0.0 for operator access.")
    parser.add_argument("--allow-mainnet", action="store_true", help="Allow planning from a seed marked requires_mainnet_ack, such as mainnet.")

    parser.add_argument("--single-host", default="", help=argparse.SUPPRESS)
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
        "--rpc-user-agent",
        default=DEFAULT_JSON_RPC_USER_AGENT,
        help=(
            "User-Agent used for operator JSON-RPC checks. "
            "Some HTTPS RPC edges reject Python urllib\'s default identity."
        ),
    )
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
    parser.add_argument(
        "--verify-hub",
        action="store_true",
        help="For discover-topology, also run the Hub verifier against the observed chain RPC surface.",
    )
    parser.add_argument("--hub-network-config", default="", help="Optional hub_networks.json path for --verify-hub.")
    parser.add_argument(
        "--hub-rpc-check",
        choices=["require", "warn", "skip"],
        default="warn",
        help="Hub verifier RPC check mode used by --verify-hub.",
    )
    parser.add_argument(
        "--hub-health-check",
        choices=["require", "warn", "skip"],
        default="warn",
        help="Hub public status check mode used by --verify-hub.",
    )
    parser.add_argument("--hub-wait-timeout-s", type=float, default=30.0)
    parser.add_argument("--hub-wait-poll-s", type=float, default=5.0)
    parser.add_argument("--hub-status-timeout-s", type=float, default=8.0)
    parser.add_argument(
        "--twiddle-hosts",
        default="",
        help="For twiddle-consensus: comma-separated Coolify host ids to inspect. Defaults to --instances hosts, or all plan hosts.",
    )
    parser.add_argument(
        "--twiddle-port",
        type=int,
        default=DEFAULT_QBFT_CONSENSUS_TWIDDLE_PORT,
        help="Temporary direct host port used by the Coolify consensus twiddle HTTP result endpoint.",
    )
    parser.add_argument(
        "--twiddle-timeout-s",
        type=float,
        default=DEFAULT_QBFT_CONSENSUS_TWIDDLE_TIMEOUT_S,
        help="Seconds to wait for each Coolify consensus twiddle result endpoint.",
    )

    parser.add_argument("--deploy-contracts", action="store_true", help="For apply: deploy contracts after RPC is healthy.")
    parser.add_argument("--deployment-run-id", default="", help="Override the contract deployment run id.")
    parser.add_argument("--deployment-project-name", default="", help="Override contract deployment project name.")
    parser.add_argument("--deployment-environment", default="", help="Override contract deployment environment.")
    parser.add_argument("--deployment-source-kind", default="", help="Override the source.kind recorded in the deployment manifest.")
    parser.add_argument(
        "--generate-offices",
        action="store_true",
        help=(
            "Generate/load non-default Ring 0 office wallets for this contract deployment. "
            "Defaults on for test/testnet and off for mainnet unless explicitly requested."
        ),
    )
    parser.add_argument(
        "--no-generate-offices",
        action="store_true",
        help="Do not generate Ring 0 office wallets; use explicit --offices or dev-chain-reset defaults instead.",
    )
    parser.add_argument("--deployment-output-dir", default="", help="Override runtime deployment output directory.")
    parser.add_argument(
        "--deployment-private-key-env",
        default="",
        help="Environment variable containing the contract deployer private key; required for non-dry-run mainnet deploy-contracts.",
    )
    parser.add_argument(
        "--deployment-offices",
        default="",
        help="Four comma-separated Ring 0 office addresses for contract constructors; mainnet rejects default Anvil addresses.",
    )
    parser.add_argument("--foundry-image", default=DEFAULT_FOUNDRY_IMAGE)
    parser.add_argument("--docker-subnet", default="", help="Override the local test QBFT Docker subnet before rendering Coolify compose.")
    parser.add_argument("--foundry-docker-network", default="bridge", help="Docker network for local Foundry container; bridge works for public RPC URLs.")
    parser.add_argument("--deploy-contracts-timeout-s", type=float, default=DEFAULT_DEPLOY_CONTRACTS_TIMEOUT_S)
    parser.add_argument("--quiet", action="store_true", help="Suppress operator progress logs; final JSON is still printed.")
    return parser.parse_args(argv)


def build_plan_from_args(args: argparse.Namespace) -> NetworkPlan:
    # mutate needs the full private-state instance catalog so it can name the
    # target and affected host/service resources even when the current apply
    # selection is narrower.
    instances = None if getattr(args, "action", "") == "mutate" else (getattr(args, "instances", "") or None)
    return build_plan(
        args.network,
        besu_image=args.besu_image or None,
        allow_mainnet=bool(args.allow_mainnet),
        public_rpc=True if args.public_rpc else None,
        single_host=args.single_host or None,
        target_address=args.target_address or None,
        coolify_url=args.coolify_url or None,
        runtime_root=args.runtime_root or None,
        private_state_path=args.private_state or None,
        instances=instances,
    )


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2))


def render_operator_runbook() -> str:
    """Return the no-surprises operator runbook printed by the docs action."""

    return textwrap.dedent(
        r"""
        Main Computer Coolify QBFT network runbook
        =========================================

        This tool renders and deploys Besu/QBFT nodes into Coolify Docker Compose
        services through the Coolify HTTP API. Remote Coolify deploys do not use
        SSH; URL, token, project, server, destination, and environment context
        should come from runtime/state/main_computer.private.yaml.

        Normal private-state deploy
        ---------------------------

        A private-state QBFT instance is a logical node declaration:

            networks:
              testnet:
                rpc: https://testnet-rpc.greatlibrary.io
                qbft:
                  instances:
                    validator-rpc-1:
                      coolify_host: A
                      roles: [rpc, validator]
                      rpc_host_port: 30010
                      p2p_host_port: 30321

        Deploy just that one-node service topology:

            python .\tools\coolify_qbft_network.py apply testnet `
              --instances validator-rpc-1

        Contract deployment is explicit and separate:

            python .\tools\coolify_qbft_network.py deploy-contracts testnet

        The deployer infers the Coolify host from
        networks.testnet.qbft.instances.validator-rpc-1.coolify_host. Do not pass
        an SSH target and do not pass --host for the normal deploy path.

        Read-only checks
        ----------------

            python .\tools\coolify_qbft_network.py plan testnet `
              --instances validator-rpc-1

            python .\tools\coolify_qbft_network.py compose testnet `
              --instances validator-rpc-1

            python .\tools\coolify_qbft_network.py discover-topology testnet `
              --instances validator-rpc-1

        Optional Hub verification
        -------------------------

            python .\tools\coolify_qbft_network.py discover-topology testnet `
              --instances validator-rpc-1 `
              --verify-hub

        Useful notes
        ------------

        * --instances selects logical QBFT instances from private state.
        * The selected Coolify host is inferred from each instance.
        * networks.<network>.rpc is used as the default external RPC URL when set.
        * --host is only a compose/debug override.
        * --single-host is deprecated compatibility plumbing and is not the
          remote Coolify deploy path.
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
        if args.action == "discover-topology":
            result = discover_topology(plan, args)
            print_json(result)
            return 0 if result.get("ok") else 1
        if args.action == "twiddle-consensus":
            result = run_coolify_consensus_twiddle(plan, args)
            print_json(result)
            return 0 if result.get("ok") else 1
        if args.action == "observe-chain":
            result = observe_chain_state(plan, args)
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
        if args.action == "mutate":
            result = mutate_network(plan, args)
            print_json(result)
            return 0 if result.get("ok") else 1
    except (PlanError, CoolifyError, TimeoutError, socket.timeout) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Unsupported action: {args.action}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
