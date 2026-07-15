#!/usr/bin/env python3
"""Bootstrap and query the all-father control plane.

The all-father private state is intentionally only a seed/control file.  It tells
this tool which Coolify hosts participate and how to talk to those Coolify
instances.  It does not describe the mainnet/testnet workload topology.

The first deployment step is therefore only the control surface:

* push one all-father guard/head container to each participating Coolify host
* give each head the list of peer head guard endpoints
* do not infer or start Hub, FoundationDB, QBFT, hub_admin, or contract work

After the heads answer, live topology is discovered from Coolify and guard
responses before any add/remove/cutover operation is attempted.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import hashlib
import re
import secrets
import time
import textwrap
import zlib
from datetime import datetime, timezone
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FDB_CLUSTER_TOOL_PATH = Path(__file__).resolve().with_name("coolify_fdb_cluster.py")
HUB_SERVICE_TOOL_PATH = Path(__file__).resolve().with_name("coolify_hub_service.py")

DEFAULT_PRIVATE_STATE_PATH = REPO_ROOT / "runtime" / "state" / "all_father.private.yaml"
DEFAULT_DOCKERFILE = "docker/allfather/Dockerfile"
DEFAULT_IMAGE = "python:3.12-slim"
DEFAULT_SERVICE_PREFIX = "allfather-head"
DEFAULT_CONTROL_ENVIRONMENT = "allfather-control"
DEFAULT_COOLIFY_PROJECT_NAME = "My first project"
DEFAULT_GUARD_CONTAINER_PORT = 41414
DEFAULT_GUARD_HOST_BASE = 41400
DEFAULT_PROBE_CONTAINER_PORT = 41415
DEFAULT_PROBE_INTERVAL_S = 15.0
DEFAULT_REMOVE_DELETE_WAIT_S = 45.0
DEFAULT_REMOVE_DELETE_POLL_S = 2.0
DEFAULT_ADD_NODE_READY_WAIT_S = 1800.0
DEFAULT_ADD_NODE_READY_POLL_S = 5.0
DEFAULT_ADD_NODE_PREFLIGHT_WAIT_S = 45.0
DEFAULT_ADD_NODE_PREFLIGHT_POLL_S = 2.0
DEFAULT_ADD_NODE_PREFLIGHT_STABLE_S = 6.0
DEFAULT_PROBE_SERVICE_PREFIX = "allfather-control-probe"
PROBE_CALLBACK_MARKER = "ALLFATHER_PROBE_RESULT_B64:"
DEFAULT_STATE_ROOT_PREFIX = "/data/main-computer/allfather/control-plane"
DEFAULT_SUPER_BASE_SOURCE_IMAGE = "hyperledger/besu:latest"
DEFAULT_SUPER_BASE_IMAGE = "main-computer/allfather-super-base:besu-fdb-web3-solc-contracts-20260715"
DEFAULT_SUPER_IMAGE = DEFAULT_SUPER_BASE_IMAGE
DEFAULT_SUPER_BASE_BUILDER_IMAGE = "docker:27-cli"
DEFAULT_SUPER_BASE_BUILDER_PREFIX = "allfather-super-base-builder"
DEFAULT_SUPER_BASE_BUILDER_CONTAINER_PORT = 41616
DEFAULT_SUPER_BASE_BUILDER_HOST_BASE = 41700
DEFAULT_SUPER_ENVIRONMENT = "allfather-supernodes"
DEFAULT_SUPER_STATE_ROOT_PREFIX = "/data/main-computer/allfather/supernodes"
DEFAULT_SUPER_GUARD_CONTAINER_PORT = 41414
DEFAULT_SUPER_HUB_CONTAINER_PORT = 8785
DEFAULT_SUPER_RPC_CONTAINER_PORT = 8545
DEFAULT_SUPER_FDB_CONTAINER_PORT = 4550
DEFAULT_SUPER_P2P_CONTAINER_PORT = 30303
DEFAULT_TESTNET_SUPER_GUARD_BASE = 41500
DEFAULT_MAINNET_SUPER_GUARD_BASE = 41600
DEFAULT_TESTNET_FDB_BASE = 44550
DEFAULT_MAINNET_FDB_BASE = 44650
DEFAULT_TESTNET_P2P_BASE = 45300
DEFAULT_MAINNET_P2P_BASE = 46300
SUPPORTED_NODE_NETWORKS = ("testnet", "mainnet")
PRIVATE_PLACEHOLDER_RE = re.compile(r"^\s*(?:<[^>]+>|TODO|TBD|CHANGEME|REPLACE_ME)\s*$", re.IGNORECASE)
PRIVATE_STATE_GENERATOR = "tools/allfather_control.py:add-node"
FDB_CLUSTER_ID_RE = re.compile(r"^[A-Za-z0-9_:-]{4,64}$")
FDB_CLUSTER_DESCRIPTION_RE = re.compile(r"^[A-Za-z0-9_]+$")

ALLFATHER_CONTRACT_SOURCE_FILES = {
    "AlphaBetaLockout.sol": REPO_ROOT / "contracts" / "AlphaBetaLockout.sol",
    "src/HubCreditBridgeEscrow.sol": REPO_ROOT / "contracts" / "src" / "HubCreditBridgeEscrow.sol",
    "src/XLagBridgeReserve.sol": REPO_ROOT / "contracts" / "src" / "XLagBridgeReserve.sol",
}


def allfather_contract_sources_b64() -> dict[str, str]:
    """Return contract sources embedded into the self-contained super-node image."""

    payload: dict[str, str] = {}
    for contract_path, source_path in ALLFATHER_CONTRACT_SOURCE_FILES.items():
        payload[contract_path] = base64.b64encode(source_path.read_text(encoding="utf-8").encode("utf-8")).decode("ascii")
    return payload


def allfather_contract_artifact_builder_script() -> str:
    """Return the build-context script that compiles all-father Solidity artifacts once.

    The managed super-base builder writes this script into the Docker build context.
    The resulting base image contains /opt/allfather-contracts/contracts-artifacts.json,
    so runtime add-node resumes do not keep invoking solc inside the live super-node.
    """

    return r"""
from __future__ import annotations
import base64
import json
import subprocess
import sys
from pathlib import Path

REQUIRED_TARGETS = (
    ("AlphaBetaLockout.sol", "AlphaBetaLockout"),
    ("src/XLagBridgeReserve.sol", "XLagBridgeReserve"),
    ("src/HubCreditBridgeEscrow.sol", "HubCreditBridgeEscrow"),
)

def artifact_is_valid(compiled: dict) -> bool:
    contracts = compiled.get("contracts")
    if not isinstance(contracts, dict):
        return False
    for source_name, contract_name in REQUIRED_TARGETS:
        contract = ((contracts.get(source_name) or {}).get(contract_name) or {})
        bytecode = (((contract.get("evm") or {}).get("bytecode") or {}).get("object") or "")
        abi = contract.get("abi")
        if not isinstance(abi, list) or not str(bytecode).strip():
            return False
    return True

def main() -> int:
    source_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    sources_b64 = json.loads(source_path.read_text(encoding="utf-8"))
    sources = {
        name: {"content": base64.b64decode(encoded).decode("utf-8")}
        for name, encoded in sources_b64.items()
    }
    standard_input = {
        "language": "Solidity",
        "sources": sources,
        "settings": {
            "optimizer": {"enabled": True, "runs": 200},
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
        },
    }
    proc = subprocess.run(
        ["solc", "--standard-json"],
        input=json.dumps(standard_input),
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write((proc.stderr or proc.stdout or "solc --standard-json failed").strip() + "\n")
        return proc.returncode or 1
    compiled = json.loads(proc.stdout or "{}")
    errors = [
        item
        for item in compiled.get("errors", [])
        if isinstance(item, dict) and item.get("severity") == "error"
    ]
    if errors:
        sys.stderr.write("; ".join(str(item.get("formattedMessage") or item.get("message") or item) for item in errors) + "\n")
        return 1
    if not artifact_is_valid(compiled):
        sys.stderr.write("compiled all-father contract artifact set is incomplete\n")
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(compiled, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"compiled all-father contract artifacts: {output_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
""".lstrip()



class AllfatherControlError(ValueError):
    """Raised when the all-father control surface cannot be planned or applied."""


@dataclass(frozen=True)
class CoolifyHostSeed:
    slot: str
    name: str
    url: str = ""
    guard_host: str = ""
    public_ip: str = ""
    vpn_ip: str = ""
    token_source: str = ""
    metadata: dict[str, Any] | None = None

    def publish_host(self) -> str:
        return self.guard_host or self.vpn_ip or self.public_ip or ""


@dataclass(frozen=True)
class HeadNode:
    head_id: str
    service_name: str
    coolify_server: str
    slot: str
    guard_container_port: int
    guard_host_port: int
    guard_publish_host: str
    guard_url: str
    state_root: str
    peers: tuple[dict[str, Any], ...]

    def peer_summary(self) -> list[dict[str, Any]]:
        return [dict(peer) for peer in self.peers]


@dataclass(frozen=True)
class HeadPlan:
    kind: str
    private_state_path: str
    heads: tuple[HeadNode, ...]
    desired_counts: dict[str, int]
    guardrails: dict[str, Any]

    def to_dict(self, *, include_compose: bool = False, dockerfile: str = DEFAULT_DOCKERFILE, image: str = DEFAULT_IMAGE) -> dict[str, Any]:
        heads_payload: list[dict[str, Any]] = []
        for head in self.heads:
            payload = asdict(head)
            payload["peers"] = head.peer_summary()
            payload["manifest"] = head_manifest(self, head)
            payload["compose"] = render_head_compose(self, head, dockerfile=dockerfile, image=image) if include_compose else None
            if not include_compose:
                payload.pop("compose", None)
            heads_payload.append(payload)
        return {
            "kind": self.kind,
            "private_state_path": self.private_state_path,
            "desired_counts": dict(self.desired_counts),
            "guardrails": dict(self.guardrails),
            "heads": heads_payload,
            "topology": {
                "source": "coolify-host-seed-only",
                "discovery_mode": "allfather-head-peer-advertise-v1",
                "guard_urls": [head.guard_url for head in self.heads],
                "note": (
                    "This is only the all-father control surface. It does not infer "
                    "or deploy mainnet/testnet Hub, FDB, QBFT, hub_admin, or contracts."
                ),
            },
        }


def _load_module(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AllfatherControlError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fdb_tool() -> Any:
    return _load_module("coolify_fdb_cluster_for_allfather_control", FDB_CLUSTER_TOOL_PATH)


def hub_service_tool() -> Any:
    return _load_module("coolify_hub_service_for_allfather_control", HUB_SERVICE_TOOL_PATH)


def repo_relative_path(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def safe_id(value: str, *, field: str = "id") -> str:
    clean = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip()).strip("-").lower()
    if not clean:
        raise AllfatherControlError(f"{field} must not be empty.")
    if clean.startswith(".") or ".." in clean:
        raise AllfatherControlError(f"{field} contains an unsafe path-like value: {value!r}")
    return clean


def sanitize_fdb_cluster_description(value: str) -> str:
    """Return a FoundationDB cluster-file description token.

    FoundationDB cluster file descriptions may use alphanumeric characters and
    underscores.  Do not reuse the more permissive service-name sanitizer here:
    hyphens make fdbcli reject the cluster file as an invalid connection string.
    """

    clean = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip())
    clean = re.sub(r"_+", "_", clean).strip("_")
    if not clean:
        clean = "main_computer_allfather"
    if not FDB_CLUSTER_DESCRIPTION_RE.match(clean):
        raise AllfatherControlError(f"Could not build a valid FoundationDB cluster description from {value!r}.")
    return clean


def fdb_cluster_description_for_network(network_key: str) -> str:
    return sanitize_fdb_cluster_description(f"main_computer_{clean_node_network_key(network_key)}_allfather")


def docker_container_name_token(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip()).strip("-").lower()
    clean = re.sub(r"-+", "-", clean)
    return clean or "allfather-super"


def super_container_name_from_manifest(manifest: Mapping[str, Any]) -> str:
    service_name = docker_container_name_token(str(manifest.get("cell_id") or "allfather-super"))
    deployment_id = docker_container_name_token(str(manifest.get("deployment_id") or "deployment"))
    # Docker allows long names, but keep this readable and below common UI limits.
    return f"{service_name}-{deployment_id}"[:120].rstrip("-.")


def new_super_deployment_id() -> str:
    return f"{int(time.time())}-{secrets.token_hex(4)}"


def host_slot_index(slot: str) -> int:
    clean = str(slot or "").strip().upper()
    if len(clean) == 1 and "A" <= clean <= "Z":
        return ord(clean) - ord("A")
    try:
        return max(0, int(clean) - 1)
    except Exception:
        return 0


def super_base_builder_service_name(head: HeadNode) -> str:
    return f"{DEFAULT_SUPER_BASE_BUILDER_PREFIX}-{docker_container_name_token(head.coolify_server or head.slot or 'host')}"


def super_base_builder_host_port(head: HeadNode) -> int:
    return DEFAULT_SUPER_BASE_BUILDER_HOST_BASE + host_slot_index(head.slot)


def super_base_builder_url(head: HeadNode) -> str:
    host = str(head.guard_publish_host or "").strip()
    if not host:
        return ""
    return f"http://{host}:{super_base_builder_host_port(head)}"


def shell_single_quote(value: Any) -> str:
    return "'" + str(value).replace("'", "'\''") + "'"


def private_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text or PRIVATE_PLACEHOLDER_RE.match(text):
            return ""
        return text
    return str(value).strip()


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is present in the project test env.
        raise AllfatherControlError("PyYAML is required to read all-father private state YAML.") from exc

    if not path.exists():
        raise AllfatherControlError(f"All-father private state file does not exist: {display_path(path)}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AllfatherControlError(f"Could not read or parse {display_path(path)}: {exc}") from exc

    if not isinstance(loaded, dict):
        raise AllfatherControlError(f"All-father private state must contain a YAML mapping: {display_path(path)}")
    return loaded




def dump_yaml_mapping(state: Mapping[str, Any]) -> str:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is present in the project test env.
        raise AllfatherControlError("PyYAML is required to write all-father private state YAML.") from exc

    return yaml.safe_dump(dict(state), sort_keys=False, allow_unicode=True)


def write_yaml_mapping(path: Path, state: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_yaml_mapping(state), encoding="utf-8")
    except OSError as exc:
        raise AllfatherControlError(f"Could not write all-father private state file {display_path(path)}: {exc}") from exc


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generated_private_key() -> str:
    # Ethereum private keys are 32-byte secp256k1 scalars.  We do not invent an
    # address here because that requires chain-specific crypto dependencies; the
    # runtime deployer can derive the address from the key when needed.
    while True:
        key = "0x" + secrets.token_hex(32)
        if int(key[2:], 16) != 0:
            return key


def generated_cluster_id() -> str:
    return secrets.token_hex(8)


def nonempty_private_value(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or PRIVATE_PLACEHOLDER_RE.match(raw):
        return ""
    return raw

def _host_field(payload: Mapping[str, Any], names: Iterable[str]) -> str:
    for name in names:
        value = private_value(payload.get(name))
        if value:
            return value
    return ""


def _token_source(slot: str, payload: Mapping[str, Any]) -> str:
    for key in ("api_token", "token", "coolify_token"):
        if private_value(payload.get(key)):
            return f"private-state:coolify.hosts.{slot}.{key}"
    for key in ("api_token_env", "token_env", "coolify_token_env"):
        value = private_value(payload.get(key))
        if value:
            return f"private-state:coolify.hosts.{slot}.{key}->env:{value}"
    for key in ("api_token_file", "token_file", "coolify_token_file"):
        value = private_value(payload.get(key))
        if value:
            return f"private-state:coolify.hosts.{slot}.{key}->file:{value}"
    return ""


def coolify_hosts_from_private_state(state: Mapping[str, Any]) -> list[CoolifyHostSeed]:
    coolify = state.get("coolify")
    if not isinstance(coolify, Mapping):
        raise AllfatherControlError("all_father.private.yaml must contain a top-level coolify mapping.")

    hosts_payload = coolify.get("hosts")
    if not isinstance(hosts_payload, Mapping) or not hosts_payload:
        raise AllfatherControlError("all_father.private.yaml must contain coolify.hosts entries.")

    hosts: list[CoolifyHostSeed] = []
    seen: set[str] = set()
    for slot, payload in hosts_payload.items():
        if not isinstance(payload, Mapping):
            continue
        name = _host_field(payload, ("name", "server", "server_name", "coolify_server"))
        if not name:
            continue
        name = safe_id(name, field=f"coolify.hosts.{slot}.name")
        if name in seen:
            raise AllfatherControlError(f"Duplicate Coolify host name in all-father private state: {name}")
        seen.add(name)
        hosts.append(
            CoolifyHostSeed(
                slot=str(slot),
                name=name,
                url=_host_field(payload, ("url", "api_url", "coolify_url", "base_url")),
                guard_host=_host_field(payload, ("guard_host", "guard_publish_host", "allfather_guard_host")),
                public_ip=_host_field(payload, ("public_ip", "ip", "host_ip")),
                vpn_ip=_host_field(payload, ("vpn_ip", "private_ip", "tailscale_ip", "zerotier_ip")),
                token_source=_token_source(str(slot), payload),
                metadata={
                    key: value
                    for key, value in payload.items()
                    if key
                    in {
                        "uuid",
                        "server_uuid",
                        "destination_uuid",
                        "environment_uuid",
                        "project_uuid",
                        "project_name",
                    }
                },
            )
        )

    if not hosts:
        raise AllfatherControlError("No usable Coolify hosts were found in coolify.hosts.")
    return sorted(hosts, key=lambda host: host.name)


def load_private_hosts(path: Path) -> list[CoolifyHostSeed]:
    return coolify_hosts_from_private_state(load_yaml_mapping(path))


def guard_url_for_host(host: CoolifyHostSeed, guard_port: int) -> str:
    target = host.publish_host() or host.name
    return f"http://{target}:{guard_port}"


def build_head_plan(
    hosts: Sequence[CoolifyHostSeed],
    *,
    private_state_path: Path,
    guard_host_base: int = DEFAULT_GUARD_HOST_BASE,
    guard_container_port: int = DEFAULT_GUARD_CONTAINER_PORT,
    state_root_prefix: str = DEFAULT_STATE_ROOT_PREFIX,
    image: str = DEFAULT_IMAGE,
) -> HeadPlan:
    if not hosts:
        raise AllfatherControlError("At least one Coolify host is required to bootstrap all-father heads.")

    raw_heads: list[dict[str, Any]] = []
    for index, host in enumerate(sorted(hosts, key=lambda item: item.name)):
        host_port = guard_host_base + index
        raw_heads.append(
            {
                "head_id": safe_id(f"allfather-head-{host.name}", field="head_id"),
                "service_name": safe_id(f"{DEFAULT_SERVICE_PREFIX}-{host.name}", field="service_name"),
                "coolify_server": host.name,
                "slot": host.slot,
                "guard_container_port": guard_container_port,
                "guard_host_port": host_port,
                "guard_publish_host": host.publish_host(),
                "guard_url": guard_url_for_host(host, host_port),
                "state_root": f"{state_root_prefix.rstrip('/')}/{host.name}",
            }
        )

    heads: list[HeadNode] = []
    for raw in raw_heads:
        peers = tuple(
            {
                "head_id": peer["head_id"],
                "coolify_server": peer["coolify_server"],
                "guard_host_port": peer["guard_host_port"],
                "guard_publish_host": peer["guard_publish_host"],
                "guard_url": peer["guard_url"],
                "state_root": peer["state_root"],
            }
            for peer in raw_heads
            if peer["head_id"] != raw["head_id"]
        )
        heads.append(HeadNode(**raw, peers=peers))

    return HeadPlan(
        kind="main_computer.allfather_control_plane_plan.v1",
        private_state_path=display_path(private_state_path),
        heads=tuple(heads),
        desired_counts={
            "allfather_heads": len(heads),
            "super_nodes": 0,
            "foundationdb": 0,
            "hub": 0,
            "qbft_validator_rpc": 0,
            "hub_admin": 0,
            "contracts": 0,
        },
        guardrails={
            "private_state_is_topology": False,
            "topology_source": "live-coolify-and-guard-discovery",
            "hub_admin_requires_live_qbft_validator_rpc": True,
            "contracts_require_live_qbft_validator_rpc": True,
            "hub_public_cutover_requires_live_qbft_validator_rpc": True,
            "bootstrap_heads_deploys_workloads": False,
        },
    )


def manifest_b64(manifest: Mapping[str, Any]) -> str:
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


def head_manifest(plan: HeadPlan, head: HeadNode) -> dict[str, Any]:
    return {
        "kind": "main_computer.allfather_container.v1",
        "control_plane_kind": "main_computer.allfather_head.v1",
        "network_key": "control-plane",
        "set_id": "allfather-control",
        "deployment_phase": "heads",
        "cell_id": head.head_id,
        "coolify_server": head.coolify_server,
        "vpn_ip": head.guard_publish_host,
        "state_root": head.state_root,
        "host_port_offset": 0,
        "desired_counts": {
            "heads": plan.desired_counts["allfather_heads"],
            "processes": 0,
            "foundationdb": 0,
            "hub": 0,
            "qbft": 0,
            "hub_admin": 0,
            "contracts": 0,
        },
        "active_counts": {
            "heads": plan.desired_counts["allfather_heads"],
            "processes": 0,
            "foundationdb": 0,
            "hub": 0,
            "qbft": 0,
            "hub_admin": 0,
            "contracts": 0,
        },
        "set_desired_counts": dict(plan.desired_counts),
        "identity": {
            "service": "main-computer-allfather-head",
            "role": "control-plane",
            "capabilities": [
                "process-guard",
                "allfather-control-plane",
                "coolify-host-discovery",
                "live-topology-discovery",
            ],
            "ports": [
                {
                    "name": "allfather-head-guard",
                    "group": "process-guard",
                    "kind": "guard-http",
                    "protocol": "tcp",
                    "container_port": head.guard_container_port,
                    "host_port": head.guard_host_port,
                    "publish_host": head.guard_publish_host,
                    "published": True,
                    "visibility": "private-or-operator",
                    "notes": "Guard/head control API: /healthz, /identity, /topology, /status, /processes.",
                }
            ],
        },
        "guard": {
            "name": f"{head.head_id}-guard",
            "container_port": head.guard_container_port,
            "host_port": head.guard_host_port,
            "publish_host": head.guard_publish_host,
            "tick_s": 10.0,
            "restart_budget_per_tick": 1,
            "initial_desired_up": True,
            "initial_drained": False,
            "head_only": True,
        },
        "topology": {
            "source": "coolify-host-seed-only",
            "discovery_mode": "allfather-head-peer-advertise-v1",
            "guard_urls": [node.guard_url for node in plan.heads],
            "peer_hosts": head.peer_summary(),
            "note": (
                "This head exists to form the all-father control surface. Mainnet/testnet topology "
                "must be discovered from live guards and explicit add/remove operations."
            ),
        },
        "guardrails": dict(plan.guardrails),
        "processes": [],
    }



def head_server_command_script() -> str:
    return r"""
import base64
import json
import os
import time
import textwrap
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("MC_ALLFATHER_GUARD_PORT", "41414"))
MANIFEST_B64 = os.environ.get("MC_ALLFATHER_MANIFEST_B64", "")
try:
    MANIFEST = json.loads(base64.b64decode(MANIFEST_B64).decode("utf-8")) if MANIFEST_B64 else {}
except Exception as exc:
    MANIFEST = {"ok": False, "manifest_error": f"{type(exc).__name__}: {exc}"}

STARTED_AT = time.time()


def payload_for_path(path: str) -> tuple[int, dict]:
    base = {
        "ok": True,
        "service": "main-computer-allfather-head",
        "head_only": True,
        "network_key": "control-plane",
        "cell_id": os.environ.get("MC_ALLFATHER_CELL_ID", ""),
        "guard_port": PORT,
        "uptime_s": round(time.time() - STARTED_AT, 3),
    }
    if path == "/healthz":
        return 200, {"ok": True, "status": "healthy", "head_only": True}
    if path == "/identity":
        identity = dict(MANIFEST.get("identity") or {})
        return 200, {**base, **identity, "manifest": MANIFEST}
    if path == "/topology":
        return 200, {
            **base,
            "topology": MANIFEST.get("topology") or {},
            "peers": MANIFEST.get("peer_hosts") or [],
            "desired_counts": MANIFEST.get("set_desired_counts") or {},
        }
    if path == "/status":
        return 200, {
            **base,
            "status": "head-ready",
            "desired_counts": MANIFEST.get("desired_counts") or {},
            "active_counts": MANIFEST.get("active_counts") or {},
            "guardrails": MANIFEST.get("guardrails") or {},
        }
    if path == "/processes":
        return 200, {**base, "processes": MANIFEST.get("processes") or []}
    return 404, {**base, "ok": False, "error": "not-found", "path": path}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        status, payload = payload_for_path(self.path.split("?", 1)[0])
        self._send(status, payload)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in {"/up", "/down", "/drain", "/wake"}:
            self._send(202, {
                "ok": True,
                "accepted": True,
                "head_only": True,
                "operation": path.strip("/"),
                "note": "control-plane head accepted the request but no workload is managed by this bootstrap container",
            })
            return
        self._send(404, {"ok": False, "error": "not-found", "path": path})

    def log_message(self, fmt: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


print(f"all-father control head listening on 0.0.0.0:{PORT}", flush=True)
HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
""".strip()


def yaml_quote(value: Any) -> str:
    return json.dumps(str(value))


def render_head_compose(
    plan: HeadPlan,
    head: HeadNode,
    *,
    dockerfile: str = DEFAULT_DOCKERFILE,
    image: str = DEFAULT_IMAGE,
) -> str:
    # The control head must be bootstrappable through Coolify raw compose with no
    # repository build context and no private image registry.  It therefore uses
    # a public Python image and an inline stdlib HTTP guard server.  The
    # Dockerfile argument is accepted for CLI compatibility but intentionally not
    # rendered in this control-plane compose.
    manifest = head_manifest(plan, head)
    port_spec = (
        f"{head.guard_publish_host}:{head.guard_host_port}:{head.guard_container_port}/tcp"
        if head.guard_publish_host
        else f"{head.guard_host_port}:{head.guard_container_port}/tcp"
    )
    parent = str(Path(head.state_root).parent).replace("\\", "/")
    command_script = head_server_command_script()
    lines = [
        f"name: {head.service_name}",
        "",
        "services:",
        f"  {head.service_name}:",
        f"    image: {yaml_quote(image)}",
        "    restart: unless-stopped",
        "    command:",
        "      - python",
        "      - -u",
        "      - -c",
        "      - |",
    ]
    compose_command_script = command_script.replace("$", "$$")
    lines.extend(f"        {line}" for line in compose_command_script.splitlines())
    lines.extend(
        [
            "    environment:",
            f"      MC_ALLFATHER_CONTROL_PLANE: {yaml_quote('1')}",
            f"      MC_ALLFATHER_HEAD_ONLY: {yaml_quote('1')}",
            f"      MC_ALLFATHER_NETWORK: {yaml_quote('control-plane')}",
            f"      MC_ALLFATHER_SET_ID: {yaml_quote('allfather-control')}",
            f"      MC_ALLFATHER_CELL_ID: {yaml_quote(head.head_id)}",
            f"      MC_ALLFATHER_GUARD_PORT: {yaml_quote(head.guard_container_port)}",
            f"      MC_ALLFATHER_GUARD_HOST_PORT: {yaml_quote(head.guard_host_port)}",
            f"      MC_ALLFATHER_STATE_ROOT: {yaml_quote(head.state_root)}",
            f"      MC_ALLFATHER_DESIRED_COUNTS: {yaml_quote(json.dumps(manifest['desired_counts'], sort_keys=True))}",
            f"      MC_ALLFATHER_PEER_GUARDS: {yaml_quote(','.join(peer['guard_url'] for peer in head.peers))}",
            f"      MC_ALLFATHER_GUARDRAILS: {yaml_quote(json.dumps(plan.guardrails, sort_keys=True))}",
            f"      MC_ALLFATHER_MANIFEST_B64: {yaml_quote(manifest_b64(manifest))}",
            "    ports:",
            f"      - {yaml_quote(port_spec)}",
            "    volumes:",
            f"      - {yaml_quote(f'{parent}:{parent}')}",
            "    healthcheck:",
            "      test:",
            "        - CMD-SHELL",
            f"        - {yaml_quote(f'python -c \"import urllib.request; urllib.request.urlopen(\\\"http://127.0.0.1:{head.guard_container_port}/healthz\\\", timeout=3).read()\"')}",
            "      interval: 10s",
            "      timeout: 5s",
            "      start_period: 10s",
            "      retries: 6",
            "",
        ]
    )
    return "\n".join(lines)


def probe_service_name(head: HeadNode) -> str:
    return safe_id(f"{DEFAULT_PROBE_SERVICE_PREFIX}-{head.coolify_server}", field="probe_service_name")


def probe_targets_for_plan(plan: HeadPlan) -> list[str]:
    return [head.guard_url for head in plan.heads]


def probe_target_records_for_plan(
    plan: HeadPlan,
    *,
    super_inventory: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return private guard targets for the Coolify-managed probe.

    Head targets keep the control surface observable.  Super-node targets let
    discover report whether each container is actually answering its private
    guard API and which internal functions are running/pending.  These URLs are
    still remote/VPN-only and are only called from the Coolify-managed probe.
    """

    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for head in plan.heads:
        url = str(head.guard_url or "").rstrip("/")
        if not url or url in seen:
            continue
        seen.add(url)
        targets.append(
            {
                "kind": "head",
                "guard_url": url,
                "service_name": head.service_name,
                "head_id": head.head_id,
                "coolify_server": head.coolify_server,
                "host_slot": head.slot,
                "scope": "remote-vpn-peer-only",
            }
        )

    for node in super_inventory or []:
        if not isinstance(node, Mapping):
            continue
        url = str(node.get("guard_url") or "").rstrip("/")
        if not url or url in seen:
            continue
        seen.add(url)
        targets.append(
            {
                "kind": "super-node",
                "guard_url": url,
                "service_name": str(node.get("service_name") or ""),
                "network_key": str(node.get("network_key") or ""),
                "coolify_server": str(node.get("coolify_server") or ""),
                "host_slot": str(node.get("host_slot") or ""),
                "host_prefix": str(node.get("host_prefix") or ""),
                "ordinal": node.get("ordinal"),
                "components": node.get("components") if isinstance(node.get("components"), Mapping) else {},
                "scope": "remote-vpn-super-only",
            }
        )
    return targets


def probe_targets_b64(
    plan: HeadPlan,
    *,
    super_inventory: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    return base64.b64encode(
        json.dumps(probe_target_records_for_plan(plan, super_inventory=super_inventory), sort_keys=True).encode("utf-8")
    ).decode("ascii")


def probe_server_command_script() -> str:
    return r"""
import base64
import hashlib
import json
import os
import threading
import time
import textwrap
import zlib
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("MC_ALLFATHER_PROBE_PORT", "41415"))
INTERVAL_S = float(os.environ.get("MC_ALLFATHER_PROBE_INTERVAL_S", "15"))
TIMEOUT_S = float(os.environ.get("MC_ALLFATHER_PROBE_TIMEOUT_S", "4"))
STATE_DIR = Path(os.environ.get("MC_ALLFATHER_PROBE_STATE_DIR", "/state"))
TARGETS_B64 = os.environ.get("MC_ALLFATHER_PROBE_TARGETS_B64", "")
CELL_ID = os.environ.get("MC_ALLFATHER_CELL_ID", "")
COOLIFY_SERVER = os.environ.get("MC_ALLFATHER_COOLIFY_SERVER", "")
CALLBACK_API_URL = os.environ.get("MC_ALLFATHER_PROBE_CALLBACK_API_URL", "").strip().rstrip("/")
CALLBACK_TOKEN = os.environ.get("MC_ALLFATHER_PROBE_CALLBACK_TOKEN", "").strip()
CALLBACK_SERVICE_UUID = os.environ.get("MC_ALLFATHER_PROBE_CALLBACK_SERVICE_UUID", "").strip()
CALLBACK_INTERVAL_S = float(os.environ.get("MC_ALLFATHER_PROBE_CALLBACK_INTERVAL_S", "15"))
CALLBACK_MARKER = "ALLFATHER_PROBE_RESULT_B64:"
LAST_CALLBACK_DIGEST = ""
LAST_CALLBACK_AT = 0.0
STARTED_AT = time.time()

try:
    TARGETS = json.loads(base64.b64decode(TARGETS_B64).decode("utf-8")) if TARGETS_B64 else []
except Exception as exc:
    TARGETS = []
    TARGETS_ERROR = f"{type(exc).__name__}: {exc}"
else:
    TARGETS_ERROR = ""

STATE_DIR.mkdir(parents=True, exist_ok=True)
LATEST = {
    "ok": False,
    "service": "main-computer-allfather-control-probe",
    "cell_id": CELL_ID,
    "coolify_server": COOLIFY_SERVER,
    "targets": [],
    "targets_error": TARGETS_ERROR,
    "started_at": STARTED_AT,
    "updated_at": None,
}


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            raw = response.read()
    except Exception as exc:
        return {"ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "error": f"invalid-json: {exc}",
            "body_preview": raw[:240].decode("utf-8", "replace"),
        }
    if isinstance(payload, dict):
        payload.setdefault("ok", True)
        payload.setdefault("url", url)
        return payload
    return {"ok": False, "url": url, "error": "json payload was not an object"}


def run_probe_once() -> dict:
    results = []
    for target in TARGETS:
        if isinstance(target, dict):
            meta = dict(target)
            clean = str(meta.get("guard_url") or meta.get("url") or "").rstrip("/")
        else:
            meta = {"kind": "legacy", "guard_url": str(target or "").rstrip("/")}
            clean = str(target or "").rstrip("/")
        if not clean:
            continue
        identity = fetch_json(f"{clean}/identity")
        topology = fetch_json(f"{clean}/topology")
        status = fetch_json(f"{clean}/status")
        healthz = fetch_json(f"{clean}/healthz")
        results.append(
            {
                **meta,
                "guard_url": clean,
                "ok": bool(identity.get("ok") or topology.get("ok") or status.get("ok") or healthz.get("ok")),
                "identity": identity,
                "topology": topology,
                "status": status,
                "healthz": healthz,
            }
        )
    return {
        "ok": any(item.get("ok") for item in results),
        "service": "main-computer-allfather-control-probe",
        "cell_id": CELL_ID,
        "coolify_server": COOLIFY_SERVER,
        "transport": "coolify-patch-probe",
        "public_guard_routes": False,
        "ssh_used": False,
        "direct_vpn_used": False,
        "targets_error": TARGETS_ERROR,
        "target_count": len(TARGETS),
        "targets": results,
        "updated_at": time.time(),
        "uptime_s": round(time.time() - STARTED_AT, 3),
    }


def callback_payload(result: dict) -> dict:
    # Compact the probe result before publishing it back into Coolify metadata.

    compact_targets = []
    for target in result.get("targets") or []:
        if not isinstance(target, dict):
            continue
        identity = target.get("identity") if isinstance(target.get("identity"), dict) else {}
        topology = target.get("topology") if isinstance(target.get("topology"), dict) else {}
        status = target.get("status") if isinstance(target.get("status"), dict) else {}
        healthz = target.get("healthz") if isinstance(target.get("healthz"), dict) else {}
        compact_targets.append(
            {
                "kind": target.get("kind") or target.get("target_kind") or "",
                "guard_url": target.get("guard_url"),
                "service_name": target.get("service_name") or identity.get("cell_id") or status.get("cell_id") or "",
                "head_id": target.get("head_id") or "",
                "cell_id": identity.get("cell_id") or status.get("cell_id") or topology.get("cell_id") or "",
                "coolify_server": target.get("coolify_server") or identity.get("coolify_server") or status.get("coolify_server") or "",
                "network_key": target.get("network_key") or identity.get("network_key") or status.get("network_key") or topology.get("network_key") or "",
                "ordinal": target.get("ordinal") or identity.get("ordinal") or status.get("ordinal") or "",
                "components": status.get("components") if isinstance(status.get("components"), dict) else target.get("components") if isinstance(target.get("components"), dict) else {},
                "functions": status.get("functions") if isinstance(status.get("functions"), dict) else identity.get("functions") if isinstance(identity.get("functions"), dict) else {},
                "ok": bool(target.get("ok")),
                "identity_ok": bool(identity.get("ok")),
                "topology_ok": bool(topology.get("ok")),
                "status_ok": bool(status.get("ok")),
                "healthz_ok": bool(healthz.get("ok")),
                "phase": status.get("phase") or status.get("status") or healthz.get("phase") or healthz.get("status") or "",
                "status_text": status.get("status") or status.get("phase") or "",
                "image": status.get("image") or healthz.get("image") or "",
                "identity_network_key": identity.get("network_key", ""),
                "topology_network_key": topology.get("network_key", ""),
                "status_network_key": status.get("network_key", ""),
                "error": identity.get("error") or topology.get("error") or status.get("error") or healthz.get("error") or "",
            }
        )
    return {
        "ok": bool(result.get("ok")),
        "service": result.get("service"),
        "cell_id": result.get("cell_id"),
        "coolify_server": result.get("coolify_server"),
        "transport": result.get("transport"),
        "public_guard_routes": False,
        "ssh_used": False,
        "direct_vpn_used": False,
        "target_count": result.get("target_count"),
        "targets_error": result.get("targets_error"),
        "targets": compact_targets,
        "updated_at": result.get("updated_at"),
        "uptime_s": result.get("uptime_s"),
    }


def publish_to_coolify_metadata(result: dict) -> None:
    # Use the Coolify API as the operator return path. The probe remains private.

    global LAST_CALLBACK_DIGEST, LAST_CALLBACK_AT
    if not (CALLBACK_API_URL and CALLBACK_TOKEN and CALLBACK_SERVICE_UUID):
        return

    now = time.time()
    compact = callback_payload(result)
    encoded = base64.b64encode(json.dumps(compact, sort_keys=True).encode("utf-8")).decode("ascii")
    digest = hashlib.sha256(encoded.encode("ascii")).hexdigest()
    if digest == LAST_CALLBACK_DIGEST and (now - LAST_CALLBACK_AT) < max(5.0, CALLBACK_INTERVAL_S):
        return

    description = (
        "Main Computer all-father private control probe. "
        "This service is intentionally left running for diagnostics and has no public route.\n\n"
        + CALLBACK_MARKER
        + encoded
    )
    payload = json.dumps({"description": description}).encode("utf-8")
    request = urllib.request.Request(
        f"{CALLBACK_API_URL}/api/v1/services/{CALLBACK_SERVICE_UUID}",
        data=payload,
        method="PATCH",
        headers={
            "Accept": "application/json,text/plain,*/*",
            "Authorization": f"Bearer {CALLBACK_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            response.read()
        LAST_CALLBACK_DIGEST = digest
        LAST_CALLBACK_AT = now
    except Exception as exc:
        print("ALLFATHER_PROBE_CALLBACK_ERROR " + repr(exc), flush=True)


def write_latest(result: dict) -> None:
    global LATEST
    LATEST = result
    tmp = STATE_DIR / "latest-result.json.tmp"
    final = STATE_DIR / "latest-result.json"
    tmp.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(final)
    print("ALLFATHER_PROBE_RESULT " + json.dumps(result, sort_keys=True), flush=True)
    publish_to_coolify_metadata(result)


def probe_loop() -> None:
    while True:
        try:
            write_latest(run_probe_once())
        except Exception as exc:
            write_latest(
                {
                    "ok": False,
                    "service": "main-computer-allfather-control-probe",
                    "cell_id": CELL_ID,
                    "coolify_server": COOLIFY_SERVER,
                    "transport": "coolify-patch-probe",
                    "public_guard_routes": False,
                    "ssh_used": False,
                    "direct_vpn_used": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "updated_at": time.time(),
                }
            )
        time.sleep(max(1.0, INTERVAL_S))


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send(200, {
                "ok": True,
                "service": "main-computer-allfather-control-probe",
                "status": "running",
                "last_probe_ok": bool(LATEST.get("ok")),
                "target_count": len(TARGETS),
            })
            return
        if path == "/result":
            self._send(200, dict(LATEST))
            return
        self._send(404, {"ok": False, "error": "not-found", "path": path})

    def log_message(self, fmt: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


threading.Thread(target=probe_loop, daemon=True).start()
print(f"all-father control probe listening on 0.0.0.0:{PORT}", flush=True)
ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
""".strip()


def render_probe_compose(
    plan: HeadPlan,
    head: HeadNode,
    *,
    image: str = DEFAULT_IMAGE,
    probe_container_port: int = DEFAULT_PROBE_CONTAINER_PORT,
    probe_interval_s: float = DEFAULT_PROBE_INTERVAL_S,
    callback_api_url: str = "",
    callback_token: str = "",
    callback_service_uuid: str = "",
    super_inventory: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Render the private Coolify-managed probe service.

    The probe intentionally has no host ports, FQDNs, or Traefik labels. It is a
    long-running diagnostic service that Coolify manages on the remote host; it
    queries the private guard URLs from that remote context and writes JSON to
    its logs and /state/latest-result.json.
    """

    service_name = probe_service_name(head)
    command_script = probe_server_command_script()
    state_dir = f"{head.state_root.rstrip('/')}/probe"
    lines = [
        f"name: {service_name}",
        "",
        "services:",
        f"  {service_name}:",
        f"    image: {yaml_quote(image)}",
        "    restart: unless-stopped",
        "    command:",
        "      - python",
        "      - -u",
        "      - -c",
        "      - |",
    ]
    lines.extend(f"        {line}" for line in command_script.splitlines())
    lines.extend(
        [
            "    environment:",
            f"      MC_ALLFATHER_PROBE: {yaml_quote('1')}",
            f"      MC_ALLFATHER_CELL_ID: {yaml_quote(head.head_id)}",
            f"      MC_ALLFATHER_COOLIFY_SERVER: {yaml_quote(head.coolify_server)}",
            f"      MC_ALLFATHER_PROBE_PORT: {yaml_quote(probe_container_port)}",
            f"      MC_ALLFATHER_PROBE_INTERVAL_S: {yaml_quote(probe_interval_s)}",
            f"      MC_ALLFATHER_PROBE_TIMEOUT_S: {yaml_quote(4)}",
            f"      MC_ALLFATHER_PROBE_TARGETS_B64: {yaml_quote(probe_targets_b64(plan, super_inventory=super_inventory))}",
            f"      MC_ALLFATHER_PROBE_STATE_DIR: {yaml_quote('/state')}",
            f"      MC_ALLFATHER_PROBE_CALLBACK_API_URL: {yaml_quote(callback_api_url)}",
            f"      MC_ALLFATHER_PROBE_CALLBACK_TOKEN: {yaml_quote(callback_token)}",
            f"      MC_ALLFATHER_PROBE_CALLBACK_SERVICE_UUID: {yaml_quote(callback_service_uuid)}",
            f"      MC_ALLFATHER_PROBE_CALLBACK_INTERVAL_S: {yaml_quote(probe_interval_s)}",
            "    expose:",
            f"      - {yaml_quote(probe_container_port)}",
            "    volumes:",
            f"      - {yaml_quote(f'{state_dir}:/state')}",
            "    healthcheck:",
            "      test:",
            "        - CMD-SHELL",
            f"        - {yaml_quote(f'python -c \"import urllib.request; urllib.request.urlopen(\\\"http://127.0.0.1:{probe_container_port}/healthz\\\", timeout=3).read()\"')}",
            "      interval: 10s",
            "      timeout: 5s",
            "      start_period: 10s",
            "      retries: 6",
            "",
        ]
    )
    return "\n".join(lines)


def render_probe_compose_for_client(
    plan: HeadPlan,
    head: HeadNode,
    args: argparse.Namespace,
    client: Any,
    *,
    callback_service_uuid: str = "",
    super_inventory: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Render probe compose with the Coolify metadata callback wired in."""

    return render_probe_compose(
        plan,
        head,
        image=getattr(args, "probe_image", getattr(args, "image", DEFAULT_IMAGE)),
        probe_container_port=getattr(args, "probe_container_port", DEFAULT_PROBE_CONTAINER_PORT),
        probe_interval_s=getattr(args, "probe_interval_s", DEFAULT_PROBE_INTERVAL_S),
        callback_api_url=str(getattr(client, "base_url", "") or ""),
        callback_token=str(getattr(client, "token", "") or ""),
        callback_service_uuid=callback_service_uuid,
        super_inventory=super_inventory,
    )


def probe_service_payload(
    plan: HeadPlan,
    head: HeadNode,
    args: argparse.Namespace,
    *,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    service_name = probe_service_name(head)
    compose = render_probe_compose(
        plan,
        head,
        image=getattr(args, "probe_image", getattr(args, "image", DEFAULT_IMAGE)),
        probe_container_port=getattr(args, "probe_container_port", DEFAULT_PROBE_CONTAINER_PORT),
        probe_interval_s=getattr(args, "probe_interval_s", DEFAULT_PROBE_INTERVAL_S),
        super_inventory=super_inventory,
    )
    payload = {
        "server_uuid": (context or {}).get("server_uuid") or "",
        "project_uuid": (context or {}).get("project_uuid") or "",
        "environment_name": (context or {}).get("environment_name") or getattr(args, "coolify_environment_name", "") or DEFAULT_CONTROL_ENVIRONMENT,
        "environment_uuid": (context or {}).get("environment_uuid") or "",
        "name": service_name,
        "description": (
            f"Main Computer all-father private control probe for Coolify host {head.coolify_server}. "
            "This service is intentionally left running for diagnostics and has no public route."
        ),
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "instant_deploy": False,
    }
    destination_uuid = destination_uuid_for_host(head, args)
    if destination_uuid:
        payload["destination_uuid"] = destination_uuid
    return {key: value for key, value in payload.items() if value not in (None, "")}


def sync_probe_service(
    client: Any,
    plan: HeadPlan,
    head: HeadNode,
    args: argparse.Namespace,
    context: Mapping[str, Any],
    tried: list[dict[str, Any]],
    *,
    super_inventory: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    service_name = probe_service_name(head)
    service_uuid, existing = hub_service_tool().find_service(client, service_name=service_name, explicit_uuid="", tried=tried)
    if service_uuid:
        compose = render_probe_compose_for_client(plan, head, args, client, callback_service_uuid=service_uuid, super_inventory=super_inventory)
        fdb_tool().update_service(client, service_uuid, service_name, compose, tried)
        return service_uuid, "updated", existing

    service_uuid = fdb_tool().create_service(client, probe_service_payload(plan, head, args, context=context, super_inventory=super_inventory), tried)
    # A newly-created probe cannot know its own Coolify service UUID until after
    # creation. Patch the compose once more so the long-running private probe can
    # publish results back into its own service metadata.
    compose = render_probe_compose_for_client(plan, head, args, client, callback_service_uuid=service_uuid)
    fdb_tool().update_service(client, service_uuid, service_name, compose, tried)
    return service_uuid, "created", existing


def application_records_from_service_detail(detail: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return compact application records embedded in a Coolify service detail payload.

    Coolify service payloads have changed shape across releases.  Some responses
    expose service applications as ``applications`` while others use
    ``service_applications`` or nest application-like records inside service
    containers.  Keep this intentionally schema-tolerant so log polling does not
    silently miss the real child application UUID.
    """

    body = detail.get("body") if isinstance(detail, Mapping) else {}
    if not isinstance(body, Mapping):
        return []

    records: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_record(value: Mapping[str, Any]) -> None:
        uuid = str(value.get("uuid") or "").strip()
        name = str(value.get("name") or value.get("service_name") or "").strip()
        if uuid and uuid not in seen:
            seen.add(uuid)
            records.append({"uuid": uuid, "name": name})

    def walk(value: Any, *, under_application_key: bool = False) -> None:
        if isinstance(value, Mapping):
            if under_application_key:
                add_record(value)
            for key, nested in value.items():
                clean_key = str(key or "").lower()
                nested_under_application_key = under_application_key or clean_key in {
                    "applications",
                    "service_applications",
                    "serviceapplications",
                    "application",
                    "service_application",
                    "serviceapplication",
                }
                walk(nested, under_application_key=nested_under_application_key)
        elif isinstance(value, list):
            for nested in value:
                walk(nested, under_application_key=under_application_key)

    walk(body)
    return records


def fetch_probe_logs(
    client: Any,
    service_uuid: str,
    tried: list[dict[str, Any]],
    *,
    application_uuid: str = "",
) -> dict[str, Any]:
    if not service_uuid and not application_uuid:
        return {"ok": False, "source": "missing-service-or-application-uuid", "body": None}

    # Coolify's public API exposes runtime logs on the application resource, not
    # on the parent service.  Keep the older service paths as a fallback for
    # version differences, but try the application UUID first when the service
    # detail gives us one.
    paths: list[tuple[str, str]] = []
    if application_uuid:
        quoted_app = urllib.parse.quote(application_uuid)
        paths.extend(
            [
                ("get-allfather-probe-application-logs", f"/api/v1/applications/{quoted_app}/logs?lines=500"),
                ("get-allfather-probe-application-logs", f"/api/v1/applications/{quoted_app}/logs?tail=500"),
                ("get-allfather-probe-application-logs", f"/api/v1/applications/{quoted_app}/logs"),
            ]
        )
    if service_uuid:
        quoted_service = urllib.parse.quote(service_uuid)
        if application_uuid:
            quoted_app = urllib.parse.quote(application_uuid)
            paths.extend(
                [
                    ("get-allfather-probe-service-application-logs", f"/api/v1/services/{quoted_service}/applications/{quoted_app}/logs?lines=500"),
                    ("get-allfather-probe-service-application-logs", f"/api/v1/services/{quoted_service}/applications/{quoted_app}/logs?tail=500"),
                    ("get-allfather-probe-service-application-logs", f"/api/v1/services/{quoted_service}/applications/{quoted_app}/logs"),
                    ("get-allfather-probe-service-application-logs", f"/api/v1/services/{quoted_service}/application/{quoted_app}/logs?lines=500"),
                    ("get-allfather-probe-service-application-logs", f"/api/v1/services/{quoted_service}/application/{quoted_app}/logs"),
                ]
            )
        paths.extend(
            [
                ("get-allfather-probe-service-logs-fallback", f"/api/v1/services/{quoted_service}/logs?lines=500"),
                ("get-allfather-probe-service-logs-fallback", f"/api/v1/services/{quoted_service}/logs?tail=500"),
                ("get-allfather-probe-service-logs-fallback", f"/api/v1/services/{quoted_service}/logs"),
                ("get-allfather-probe-service-logs-fallback", f"/api/v1/services/{quoted_service}/application/logs?lines=500"),
                ("get-allfather-probe-service-logs-fallback", f"/api/v1/services/{quoted_service}/application/logs"),
                ("get-allfather-probe-service-logs-fallback", f"/api/v1/services/{quoted_service}/applications/logs?lines=500"),
                ("get-allfather-probe-service-logs-fallback", f"/api/v1/services/{quoted_service}/applications/logs"),
                ("get-allfather-probe-service-logs-fallback", f"/api/v1/services/{quoted_service}/docker/logs?lines=500"),
                ("get-allfather-probe-service-logs-fallback", f"/api/v1/services/{quoted_service}/docker/logs"),
            ]
        )

    for operation, path in paths:
        response = client.request("GET", path)
        tried.append({"operation": operation, "path": path, "response": hub_service_tool().response_to_dict(response)})
        if response.ok:
            return {"ok": True, "source": path, "body": response.body}
        if response.status not in {400, 404, 405, 422}:
            return {"ok": False, "source": path, "error": f"HTTP {response.status}", "body": response.body}
    return {"ok": False, "source": "coolify-api", "error": "no known Coolify logs endpoint returned probe logs", "body": None}


def _strings_from_nested(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for nested in value.values():
            yield from _strings_from_nested(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _strings_from_nested(nested)


def probe_results_from_logs_body(body: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for text in _strings_from_nested(body):
        for line in text.splitlines():
            if "ALLFATHER_PROBE_RESULT " not in line:
                continue
            raw = line.split("ALLFATHER_PROBE_RESULT ", 1)[1].strip()
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            if isinstance(parsed, dict):
                results.append(parsed)
    return results


def latest_probe_result(logs: Mapping[str, Any]) -> dict[str, Any]:
    results = probe_results_from_logs_body(logs.get("body") if isinstance(logs, Mapping) else None)
    if not results:
        return {"ok": False, "source": "coolify-probe-logs", "error": "no ALLFATHER_PROBE_RESULT entry found"}
    return {"ok": True, "source": "coolify-probe-logs", "result": results[-1], "result_count": len(results)}


def probe_result_from_service_metadata(detail: Mapping[str, Any]) -> dict[str, Any]:
    """Read the probe result callback from the Coolify service description.

    Logs are not available through every Coolify install/API version. The probe
    therefore also writes a compact base64 JSON result into its own service
    description using PROBE_CALLBACK_MARKER.
    """

    body = detail.get("body") if isinstance(detail, Mapping) else {}
    if not isinstance(body, Mapping):
        return {"ok": False, "source": "coolify-service-description", "error": "service detail body was not an object"}
    description = str(body.get("description") or "")
    if PROBE_CALLBACK_MARKER not in description:
        return {"ok": False, "source": "coolify-service-description", "error": "no ALLFATHER_PROBE_RESULT_B64 entry found"}
    encoded = description.split(PROBE_CALLBACK_MARKER, 1)[1].strip().split()[0]
    try:
        result = json.loads(base64.b64decode(encoded).decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "source": "coolify-service-description", "error": f"invalid metadata callback: {type(exc).__name__}: {exc}"}
    if not isinstance(result, Mapping):
        return {"ok": False, "source": "coolify-service-description", "error": "metadata callback was not an object"}
    return {"ok": True, "source": "coolify-service-description", "result": dict(result), "result_count": 1}


def probe_result_targets(probe_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = probe_result.get("result") if isinstance(probe_result, Mapping) else None
    if not isinstance(result, Mapping):
        return []
    return [dict(item) for item in (result.get("targets") or []) if isinstance(item, Mapping)]


def probe_result_service_names(probe_result: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for target in probe_result_targets(probe_result):
        name = str(target.get("service_name") or target.get("cell_id") or "").strip()
        if name:
            names.add(name)
    return names


def expected_super_target_names(targets: Sequence[Mapping[str, Any]]) -> set[str]:
    names: set[str] = set()
    for target in targets:
        if not isinstance(target, Mapping):
            continue
        if str(target.get("kind") or "") != "super-node":
            continue
        name = str(target.get("service_name") or "").strip()
        if name:
            names.add(name)
    return names


def probe_result_covers_expected_super_targets(
    probe_result: Mapping[str, Any],
    targets: Sequence[Mapping[str, Any]],
) -> bool:
    """Return true once a probe result has at least mentioned every super target.

    A private super-node guard might be down, in which case the target result is
    still useful because it carries the connection error.  The readiness check
    therefore requires the target record to be present, not necessarily healthy.
    """

    expected = expected_super_target_names(targets)
    if not expected:
        return bool(probe_result.get("ok"))
    return expected <= probe_result_service_names(probe_result)


def wait_for_probe_metadata_result(
    client: Any,
    service_uuid: str,
    tried: list[dict[str, Any]],
    *,
    expected_targets: Sequence[Mapping[str, Any]],
    wait_s: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Poll the probe service description until the callback covers super targets."""

    deadline = time.time() + max(0.0, float(wait_s or 0.0))
    last_detail: dict[str, Any] = {}
    last_result: dict[str, Any] = {"ok": False, "source": "coolify-service-description", "error": "probe metadata callback was not observed"}
    first = True
    while first or time.time() < deadline:
        first = False
        last_detail = fetch_service_detail(client, service_uuid, tried)
        last_result = probe_result_from_service_metadata(last_detail)
        if last_result.get("ok") and probe_result_covers_expected_super_targets(last_result, expected_targets):
            return last_detail, last_result
        if time.time() >= deadline:
            break
        time.sleep(min(2.0, max(0.25, deadline - time.time())))
    return last_detail, last_result


def context_args_for_host(args: argparse.Namespace, host: HeadNode) -> argparse.Namespace:
    context_args = fdb_tool().context_args_for_server(args, host.coolify_server)
    if not str(context_args.coolify_project_uuid or "").strip() and not str(context_args.coolify_project_name or "").strip():
        context_args.coolify_project_name = DEFAULT_COOLIFY_PROJECT_NAME
    if not str(context_args.coolify_environment_name or "").strip():
        context_args.coolify_environment_name = DEFAULT_CONTROL_ENVIRONMENT
    return context_args


def resolve_context(client: Any, args: argparse.Namespace, host: HeadNode, tried: list[dict[str, Any]]) -> dict[str, Any]:
    profile = fdb_tool()._ProfileForContext("allfather-control")
    return hub_service_tool().resolve_coolify_context(client, profile, context_args_for_host(args, host), tried)


def service_payload(
    plan: HeadPlan,
    head: HeadNode,
    args: argparse.Namespace,
    *,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    compose = render_head_compose(
        plan,
        head,
        dockerfile=getattr(args, "dockerfile", DEFAULT_DOCKERFILE),
        image=getattr(args, "image", DEFAULT_IMAGE),
    )
    payload = {
        "server_uuid": (context or {}).get("server_uuid") or "",
        "project_uuid": (context or {}).get("project_uuid") or "",
        "environment_name": (context or {}).get("environment_name") or getattr(args, "coolify_environment_name", "") or DEFAULT_CONTROL_ENVIRONMENT,
        "environment_uuid": (context or {}).get("environment_uuid") or "",
        "name": head.service_name,
        "description": (
            f"Main Computer all-father control head for Coolify host {head.coolify_server}. "
            "This service bootstraps the guard/control surface only."
        ),
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "instant_deploy": False,
    }
    destination_uuid = destination_uuid_for_host(head, args)
    if destination_uuid:
        payload["destination_uuid"] = destination_uuid
    return {key: value for key, value in payload.items() if value not in (None, "")}


def destination_uuid_for_host(head: HeadNode, args: argparse.Namespace) -> str:
    per_server = fdb_tool().parse_binding_map(getattr(args, "set_coolify_destination_uuid", []) or [], "--set-coolify-destination-uuid")
    return str(per_server.get(head.coolify_server) or getattr(args, "coolify_destination_uuid", "") or "").strip()


def explicit_service_uuid_for_host(head: HeadNode, args: argparse.Namespace) -> str:
    per_server = fdb_tool().parse_binding_map(getattr(args, "set_coolify_service_uuid", []) or [], "--set-coolify-service-uuid")
    return str(per_server.get(head.coolify_server) or getattr(args, "coolify_service_uuid", "") or "").strip()


def redact_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    redacted = dict(payload)
    if "docker_compose_raw" in redacted:
        raw = str(payload["docker_compose_raw"])
        redacted["docker_compose_raw"] = "<base64>"
        redacted["docker_compose_raw_bytes"] = len(raw)
    return redacted


def dry_run_payload(plan: HeadPlan, args: argparse.Namespace) -> dict[str, Any]:
    # Dry-run is intentionally offline. It should render the control-plane
    # payloads from the synced host seed without requiring live token env vars or
    # proving that a Coolify API is reachable.
    return {
        "ok": True,
        "dry_run": True,
        "operation": "bootstrap-heads",
        "coolify_project_name": getattr(args, "coolify_project_name", DEFAULT_COOLIFY_PROJECT_NAME) or DEFAULT_COOLIFY_PROJECT_NAME,
        "plan": plan.to_dict(include_compose=getattr(args, "include_compose", False), dockerfile=args.dockerfile, image=args.image),
        "coolify_payloads": [
            {
                "head_id": head.head_id,
                "server": head.coolify_server,
                "service_name": head.service_name,
                "guard_url": head.guard_url,
                "payload": redact_payload(service_payload(plan, head, args)),
            }
            for head in plan.heads
        ],
    }


def sync_head_service(client: Any, plan: HeadPlan, head: HeadNode, args: argparse.Namespace, context: Mapping[str, Any], tried: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    explicit_uuid = explicit_service_uuid_for_host(head, args)
    service_uuid = explicit_uuid
    existing: dict[str, Any] = {}
    if not service_uuid:
        service_uuid, existing = hub_service_tool().find_service(client, service_name=head.service_name, explicit_uuid="", tried=tried)
    if service_uuid:
        compose = render_head_compose(plan, head, dockerfile=args.dockerfile, image=args.image)
        fdb_tool().update_service(client, service_uuid, head.service_name, compose, tried)
        return service_uuid, "updated", existing
    payload = service_payload(plan, head, args, context=context)
    service_uuid = fdb_tool().create_service(client, payload, tried)
    return service_uuid, "created", existing


def apply_bootstrap_heads(plan: HeadPlan, args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "dry_run", False):
        return dry_run_payload(plan, args)

    phases: list[dict[str, Any]] = []
    for head in plan.heads:
        tried: list[dict[str, Any]] = []
        operator_log(args, f"coolify: checking API and inventory on {head.coolify_server}")
        client, token_source = fdb_tool().client_for_server(head.coolify_server, args)
        version = client.request("GET", "/api/v1/version")
        tried.append({"operation": "coolify-version", "response": hub_service_tool().response_to_dict(version)})
        if not version.ok:
            raise AllfatherControlError(
                f"Coolify API version check failed for {head.coolify_server!r} with HTTP {version.status}: {version.body}"
            )
        try:
            context = resolve_context(client, args, head, tried)
        except Exception as exc:
            project_name = getattr(args, "coolify_project_name", DEFAULT_COOLIFY_PROJECT_NAME) or DEFAULT_COOLIFY_PROJECT_NAME
            if "project" in str(exc).lower():
                raise AllfatherControlError(
                    f"Could not resolve the Coolify control project {project_name!r} on {head.coolify_server!r}. "
                    "The all-father bootstrap uses Coolify's default project name and does not require a project argument."
                ) from exc
            raise
        service_uuid, action, existing = sync_head_service(client, plan, head, args, context, tried)
        deploy_result = None
        if not getattr(args, "no_deploy", False):
            deploy_result = hub_service_tool().trigger_deploy_service(
                client,
                service_uuid=service_uuid,
                force=getattr(args, "force_deploy", False),
                tried=tried,
            )
        phases.append(
            {
                "head_id": head.head_id,
                "server": head.coolify_server,
                "service_name": head.service_name,
                "guard_url": head.guard_url,
                "token_source": token_source,
                "context": context,
                "service_uuid": service_uuid,
                "service_action": action,
                "existing": existing,
                "deployed": deploy_result is not None,
                "deploy_result": deploy_result,
                "tried": tried,
            }
        )
    return {"ok": True, "operation": "bootstrap-heads", "plan": plan.to_dict(), "phases": phases}


def fetch_json(url: str, *, timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - operator supplied control URL.
            body = response.read()
    except Exception as exc:
        return {"ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "url": url, "error": f"invalid-json: {exc}", "body_preview": body[:200].decode("utf-8", "replace")}
    payload.setdefault("ok", True)
    payload.setdefault("url", url)
    return payload


def _nested_values(value: Any) -> Iterable[Any]:
    if isinstance(value, Mapping):
        for nested in value.values():
            yield nested
            yield from _nested_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield nested
            yield from _nested_values(nested)


def _coerce_operator_url(raw: Any) -> str:
    text = str(raw or "").strip().strip(",")
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return text.rstrip("/")
    if re.match(r"^[A-Za-z0-9_.-]+(?::\d+)?(?:/.*)?$", text):
        return f"https://{text}".rstrip("/")
    return ""


def operator_urls_from_service_record(record: Mapping[str, Any]) -> list[str]:
    """Extract public/operator HTTP routes from a Coolify service record.

    VPN guard URLs are intentionally not synthesized here.  The local operator
    process cannot assume it can route to 10.x addresses; those addresses are
    for head-to-head traffic inside the remote control network.
    """

    urls: list[str] = []
    seen: set[str] = set()
    useful_keys = {
        "url",
        "urls",
        "fqdn",
        "fqdns",
        "domain",
        "domains",
        "public_url",
        "public_urls",
        "operator_url",
        "operator_urls",
        "service_url",
        "service_urls",
    }

    def add(candidate: Any) -> None:
        if isinstance(candidate, str):
            # Coolify fields are sometimes comma/newline separated.
            pieces = re.split(r"[\s,]+", candidate)
            for piece in pieces:
                url = _coerce_operator_url(piece)
                if url and url not in seen:
                    seen.add(url)
                    urls.append(url)
        elif isinstance(candidate, list):
            for item in candidate:
                add(item)
        elif isinstance(candidate, Mapping):
            for item in candidate.values():
                add(item)

    for key, value in record.items():
        if str(key) in useful_keys or str(key).lower() in useful_keys:
            add(value)

    for nested in _nested_values(record):
        if isinstance(nested, Mapping):
            for key, value in nested.items():
                if str(key).lower() in useful_keys:
                    add(value)

    return urls


def response_body_mapping(response: Any) -> dict[str, Any]:
    body = getattr(response, "body", None)
    if isinstance(body, Mapping):
        return dict(body)
    if isinstance(body, list):
        return {"items": body}
    if isinstance(body, str):
        try:
            parsed = json.loads(body)
        except Exception:
            return {"raw": body}
        if isinstance(parsed, Mapping):
            return dict(parsed)
        if isinstance(parsed, list):
            return {"items": parsed}
    return {}


def service_record_from_existing(existing: Mapping[str, Any]) -> dict[str, Any]:
    matches = existing.get("matches") if isinstance(existing, Mapping) else None
    if isinstance(matches, list) and matches:
        first = matches[0]
        if isinstance(first, Mapping):
            return dict(first)
    return {}


def fetch_service_detail(client: Any, service_uuid: str, tried: list[dict[str, Any]]) -> dict[str, Any]:
    if not service_uuid:
        return {"ok": False, "source": "missing-service-uuid"}
    paths = [
        f"/api/v1/services/{urllib.parse.quote(service_uuid)}",
        f"/api/v1/services/{urllib.parse.quote(service_uuid)}/show",
    ]
    for path in paths:
        response = client.request("GET", path)
        tried.append({"operation": "get-allfather-head-service", "path": path, "response": hub_service_tool().response_to_dict(response)})
        if response.ok:
            body = response_body_mapping(response)
            return {"ok": True, "source": path, "body": body}
        if response.status == 404:
            continue
    return {"ok": False, "source": "coolify-api", "error": "service detail endpoint did not return a usable record"}


def discover_head_via_coolify(plan: HeadPlan, head: HeadNode, args: argparse.Namespace) -> dict[str, Any]:
    tried: list[dict[str, Any]] = []
    token_source = ""
    service_uuid = ""
    existing: dict[str, Any] = {}
    detail: dict[str, Any] = {}
    operator_urls: list[str] = []

    try:
        client, token_source = fdb_tool().client_for_server(head.coolify_server, args)
        version = client.request("GET", "/api/v1/version")
        tried.append({"operation": "coolify-version", "response": hub_service_tool().response_to_dict(version)})
        if not version.ok:
            return {
                "ok": False,
                "method": "coolify-api",
                "token_source": token_source,
                "service_uuid": "",
                "operator_urls": [],
                "tried": tried,
                "error": f"Coolify API version check failed with HTTP {version.status}",
            }
        service_uuid, existing = hub_service_tool().find_service(client, service_name=head.service_name, explicit_uuid=explicit_service_uuid_for_host(head, args), tried=tried)
        service_record = service_record_from_existing(existing)
        if service_uuid:
            detail = fetch_service_detail(client, service_uuid, tried)
            body = detail.get("body") if isinstance(detail, Mapping) else {}
            if isinstance(body, Mapping):
                service_record = {**service_record, **dict(body)}
        operator_urls = operator_urls_from_service_record(service_record)
    except Exception as exc:
        return {
            "ok": False,
            "method": "coolify-api",
            "token_source": token_source,
            "service_uuid": service_uuid,
            "operator_urls": operator_urls,
            "tried": tried,
            "error": f"{type(exc).__name__}: {exc}",
        }

    coolify_ok = bool(service_uuid)
    return {
        "ok": coolify_ok,
        "method": "coolify-api",
        "token_source": token_source,
        "service_uuid": service_uuid,
        "existing": existing,
        "service_detail": detail,
        "operator_urls": operator_urls,
        "tried": tried,
    }


def fetch_head_payloads(operator_urls: Sequence[str], *, timeout_s: float) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for operator_url in operator_urls:
        base = str(operator_url or "").rstrip("/")
        identity = fetch_json(f"{base}/identity", timeout_s=timeout_s)
        topology = fetch_json(f"{base}/topology", timeout_s=timeout_s)
        status = fetch_json(f"{base}/status", timeout_s=timeout_s)
        attempts.append({"operator_url": base, "identity": identity, "topology": topology, "status": status})
        if identity.get("ok") or topology.get("ok") or status.get("ok"):
            return identity, topology, status, {"ok": True, "operator_url": base, "attempts": attempts}
    return (
        {"ok": False, "error": "no operator URL returned identity", "attempts": attempts},
        {"ok": False, "error": "no operator URL returned topology", "attempts": attempts},
        {"ok": False, "error": "no operator URL returned status", "attempts": attempts},
        {"ok": False, "operator_url": "", "attempts": attempts},
    )


def direct_vpn_disabled_payload(head: HeadNode) -> dict[str, Any]:
    return {
        "ok": False,
        "guard_url": head.guard_url,
        "scope": "remote-vpn-peer-only",
        "reason": (
            "Direct local HTTP discovery is disabled because this guard URL is for the remote VPN/control network. "
            "Use Coolify/operator discovery from the local machine, or rerun with --allow-direct-vpn from a host that can route to it."
        ),
    }


def sync_and_query_probe_for_head(plan: HeadPlan, head: HeadNode, args: argparse.Namespace) -> dict[str, Any]:
    """Create/update the long-running Coolify-managed probe and read any visible result.

    This is the normal operator discovery path. It does not use SSH, does not
    expose the guard through Traefik, and does not curl VPN addresses from the
    local machine. The probe runs inside the remote Coolify host/network and is
    intentionally left running for diagnosis until a future finalization action
    removes or disables it.
    """

    tried: list[dict[str, Any]] = []
    token_source = ""
    head_service_uuid = ""
    probe_service_uuid = ""
    head_existing: dict[str, Any] = {}
    probe_existing: dict[str, Any] = {}
    head_detail: dict[str, Any] = {}
    probe_detail: dict[str, Any] = {}
    probe_logs: dict[str, Any] = {}
    probe_result: dict[str, Any] = {"ok": False, "error": "probe was not queried"}
    super_inventory: list[dict[str, Any]] = []
    super_inventory_error = ""

    if getattr(args, "dry_run", False):
        return {
            "ok": True,
            "dry_run": True,
            "method": "coolify-patch-probe",
            "token_source": "",
            "head_service_name": head.service_name,
            "probe_service_name": probe_service_name(head),
            "peer_guard_url": head.guard_url,
            "probe_targets": probe_target_records_for_plan(plan),
            "super_inventory": [],
            "super_inventory_error": "",
            "probe_compose": render_probe_compose(
                plan,
                head,
                image=getattr(args, "probe_image", getattr(args, "image", DEFAULT_IMAGE)),
                probe_container_port=getattr(args, "probe_container_port", DEFAULT_PROBE_CONTAINER_PORT),
                probe_interval_s=getattr(args, "probe_interval_s", DEFAULT_PROBE_INTERVAL_S),
            ) if getattr(args, "include_probe_compose", False) else None,
            "public_guard_routes": False,
            "ssh_used": False,
            "direct_vpn_used": False,
            "probe_left_running": True,
        }

    try:
        client, token_source = fdb_tool().client_for_server(head.coolify_server, args)
        version = client.request("GET", "/api/v1/version")
        tried.append({"operation": "coolify-version", "response": hub_service_tool().response_to_dict(version)})
        if not version.ok:
            return {
                "ok": False,
                "method": "coolify-patch-probe",
                "token_source": token_source,
                "tried": tried,
                "error": f"Coolify API version check failed with HTTP {version.status}",
                "public_guard_routes": False,
                "ssh_used": False,
                "direct_vpn_used": False,
            }

        context = resolve_context(client, args, head, tried)

        try:
            service_inventory_items = service_items_for_client(client, tried)
            super_inventory = all_super_inventory_from_services(service_inventory_items, head)
        except Exception as exc:
            super_inventory_error = f"{type(exc).__name__}: {exc}"

        head_service_uuid, head_existing = hub_service_tool().find_service(
            client,
            service_name=head.service_name,
            explicit_uuid=explicit_service_uuid_for_host(head, args),
            tried=tried,
        )
        if head_service_uuid:
            head_detail = fetch_service_detail(client, head_service_uuid, tried)

        if getattr(args, "no_sync_probe", False):
            probe_result = {"ok": False, "error": "probe sync disabled by --no-sync-probe"}
        else:
            probe_service_uuid, action, probe_existing = sync_probe_service(
                client,
                plan,
                head,
                args,
                context,
                tried,
                super_inventory=super_inventory,
            )
            if not getattr(args, "no_deploy_probe", False):
                deploy_result = hub_service_tool().trigger_deploy_service(
                    client,
                    service_uuid=probe_service_uuid,
                    force=getattr(args, "force_deploy_probe", False),
                    tried=tried,
                )
            else:
                deploy_result = None
            expected_targets = probe_target_records_for_plan(plan, super_inventory=super_inventory)
            probe_detail, probe_result = wait_for_probe_metadata_result(
                client,
                probe_service_uuid,
                tried,
                expected_targets=expected_targets,
                wait_s=float(getattr(args, "probe_result_wait_s", 20.0)),
            )
            probe_applications = application_records_from_service_detail(probe_detail)
            probe_application_uuid = probe_applications[0]["uuid"] if probe_applications else ""
            if not probe_result.get("ok") or not probe_result_covers_expected_super_targets(probe_result, expected_targets):
                probe_logs = fetch_probe_logs(client, probe_service_uuid, tried, application_uuid=probe_application_uuid)
                log_result = latest_probe_result(probe_logs)
                if log_result.get("ok"):
                    probe_result = log_result
            else:
                probe_logs = {"ok": True, "source": "coolify-service-description", "body": None}
            return {
                "ok": True,
                "method": "coolify-patch-probe",
                "token_source": token_source,
                "head_service_name": head.service_name,
                "head_service_uuid": head_service_uuid,
                "head_existing": head_existing,
                "head_detail": head_detail,
                "probe_service_name": probe_service_name(head),
                "probe_service_uuid": probe_service_uuid,
                "probe_action": action,
                "probe_existing": probe_existing,
                "probe_deployed": deploy_result is not None,
                "probe_deploy_result": deploy_result,
                "probe_detail": probe_detail,
                "probe_application_uuid": probe_application_uuid,
                "probe_applications": probe_applications,
                "probe_logs": probe_logs,
                "probe_result": probe_result,
                "probe_targets": expected_targets,
                "super_inventory": super_inventory,
                "super_inventory_error": super_inventory_error,
                "probe_left_running": True,
                "finalization_action": "future: stop/remove probes after control discovery is proven stable",
                "public_guard_routes": False,
                "ssh_used": False,
                "direct_vpn_used": False,
                "tried": tried,
            }

        return {
            "ok": bool(head_service_uuid),
            "method": "coolify-patch-probe",
            "token_source": token_source,
            "head_service_name": head.service_name,
            "head_service_uuid": head_service_uuid,
            "head_existing": head_existing,
            "head_detail": head_detail,
            "probe_service_name": probe_service_name(head),
            "probe_service_uuid": probe_service_uuid,
            "probe_result": probe_result,
            "probe_targets": probe_target_records_for_plan(plan, super_inventory=super_inventory),
            "super_inventory": super_inventory,
            "super_inventory_error": super_inventory_error,
            "probe_left_running": False,
            "public_guard_routes": False,
            "ssh_used": False,
            "direct_vpn_used": False,
            "tried": tried,
        }

    except Exception as exc:
        return {
            "ok": False,
            "method": "coolify-patch-probe",
            "token_source": token_source,
            "head_service_name": head.service_name,
            "head_service_uuid": head_service_uuid,
            "probe_service_name": probe_service_name(head),
            "probe_service_uuid": probe_service_uuid,
            "probe_result": probe_result,
            "super_inventory": super_inventory,
            "super_inventory_error": super_inventory_error,
            "public_guard_routes": False,
            "ssh_used": False,
            "direct_vpn_used": False,
            "tried": tried,
            "error": f"{type(exc).__name__}: {exc}",
        }




def _small_mapping(value: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    """Return a small stable subset of a noisy API mapping."""

    return {key: value[key] for key in keys if key in value and value[key] not in (None, "", [], {})}


def compact_coolify_response(response: Any) -> dict[str, Any]:
    """Summarize a Coolify response without embedding full service records or compose text."""

    if not isinstance(response, Mapping):
        return {"summary": str(type(response).__name__)}
    compact = _small_mapping(response, ["method", "path", "status", "ok", "error"])
    body = response.get("body")
    if isinstance(body, Mapping):
        compact_body = _small_mapping(
            body,
            [
                "uuid",
                "name",
                "human_name",
                "status",
                "fqdn",
                "required_fqdn",
                "server_status",
                "message",
                "error",
                "docs",
            ],
        )
        if "applications" in body and isinstance(body.get("applications"), list):
            compact_body["applications"] = [
                _small_mapping(app, ["uuid", "name", "status", "fqdn", "ports"])
                for app in body.get("applications", [])[:5]
                if isinstance(app, Mapping)
            ]
            if len(body.get("applications") or []) > 5:
                compact_body["applications_truncated"] = True
        if "server" in body and isinstance(body.get("server"), Mapping):
            compact_body["server"] = _small_mapping(body["server"], ["uuid", "name", "ip", "status"])
        if compact_body:
            compact["body"] = compact_body
    elif isinstance(body, list):
        compact["body"] = {"items": len(body)}
    elif isinstance(body, str) and body:
        compact["body"] = {"preview": body[:200], "truncated": len(body) > 200}
    return compact


def compact_tried(tried: Any) -> list[dict[str, Any]]:
    """Summarize attempted Coolify API calls.

    This helper is intentionally reserved for explicit verbose/diagnostic use.
    Default discovery output must not include the per-attempt list because even
    compact service records are noisy during repeated probe/log endpoint checks.
    """

    if not isinstance(tried, list):
        return []
    compact: list[dict[str, Any]] = []
    for item in tried:
        if not isinstance(item, Mapping):
            continue
        out = _small_mapping(item, ["operation", "path", "action"])
        response = item.get("response")
        if isinstance(response, Mapping):
            out["response"] = compact_coolify_response(response)
        elif response is not None:
            out["response"] = {"summary": str(type(response).__name__)}
        compact.append(out)
    return compact


def summarize_coolify_attempts(tried: Any) -> dict[str, Any]:
    """Return a small status summary for default discovery output."""

    if not isinstance(tried, list):
        return {"attempt_count": 0, "failed_count": 0, "operations": []}

    operations: list[str] = []
    failed_count = 0
    last_error: dict[str, Any] = {}

    for item in tried:
        if not isinstance(item, Mapping):
            continue
        operation = str(item.get("operation") or item.get("action") or "").strip()
        if operation and operation not in operations:
            operations.append(operation)
        response = item.get("response")
        response_ok = True
        if isinstance(response, Mapping):
            response_ok = bool(response.get("ok", True))
            if not response_ok:
                failed_count += 1
                last_error = {
                    "operation": operation,
                    "path": response.get("path"),
                    "status": response.get("status"),
                }
                body = response.get("body")
                if isinstance(body, Mapping):
                    message = body.get("message") or body.get("error")
                    if message:
                        last_error["message"] = message
        elif response is not None:
            response_ok = False
            failed_count += 1
            last_error = {"operation": operation, "error": str(type(response).__name__)}

    summary: dict[str, Any] = {
        "attempt_count": len(tried),
        "failed_count": failed_count,
        "operations": operations[:12],
    }
    if len(operations) > 12:
        summary["operations_truncated"] = True
    if last_error:
        summary["last_error"] = last_error
    return summary


def compact_probe_result(probe_result: Any) -> dict[str, Any]:
    """Keep probe-result status without dumping every target payload."""

    if not isinstance(probe_result, Mapping):
        return {"ok": False, "error": "no probe result"}
    compact = _small_mapping(probe_result, ["ok", "source", "error"])
    result = probe_result.get("result")
    if isinstance(result, Mapping):
        compact["result"] = _small_mapping(
            result,
            [
                "ok",
                "service",
                "cell_id",
                "coolify_server",
                "transport",
                "public_guard_routes",
                "ssh_used",
                "direct_vpn_used",
                "target_count",
                "targets_error",
                "updated_at",
                "uptime_s",
            ],
        )
        targets = result.get("targets")
        if isinstance(targets, list):
            compact["result"]["targets"] = [
                {
                    "guard_url": target.get("guard_url"),
                    "ok": bool(target.get("ok")),
                    "identity_ok": bool((target.get("identity") or {}).get("ok")) if isinstance(target.get("identity"), Mapping) else False,
                    "topology_ok": bool((target.get("topology") or {}).get("ok")) if isinstance(target.get("topology"), Mapping) else False,
                    "status_ok": bool((target.get("status") or {}).get("ok")) if isinstance(target.get("status"), Mapping) else False,
                    "healthz_ok": bool((target.get("healthz") or {}).get("ok")) if isinstance(target.get("healthz"), Mapping) else False,
                }
                for target in targets
                if isinstance(target, Mapping)
            ]
    return compact


def compact_probe_record(probe: Mapping[str, Any]) -> dict[str, Any]:
    """Compact per-head probe details for default discovery output."""

    compact = _small_mapping(
        probe,
        [
            "ok",
            "dry_run",
            "method",
            "token_source",
            "head_service_name",
            "head_service_uuid",
            "probe_service_name",
            "probe_service_uuid",
            "probe_application_uuid",
            "probe_action",
            "probe_deployed",
            "probe_left_running",
            "finalization_action",
            "public_guard_routes",
            "ssh_used",
            "direct_vpn_used",
            "error",
        ],
    )
    compact["probe_result"] = compact_probe_result(probe.get("probe_result"))
    compact["probe_targets"] = list(probe.get("probe_targets") or [])
    if "probe_logs" in probe and isinstance(probe.get("probe_logs"), Mapping):
        compact["probe_logs"] = _small_mapping(probe["probe_logs"], ["ok", "source", "error"])
    if "tried" in probe:
        compact["coolify_api"] = summarize_coolify_attempts(probe.get("tried"))
    if probe.get("probe_compose") is not None:
        # This is only present when the operator explicitly asks for probe compose.
        compact["probe_compose"] = probe.get("probe_compose")
    return compact

def super_statuses_from_probe_result(probe_result: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return super-node guard observations keyed by service/cell name."""

    statuses: dict[str, dict[str, Any]] = {}
    result = probe_result.get("result") if isinstance(probe_result, Mapping) else None
    if not isinstance(result, Mapping):
        return statuses

    for target in result.get("targets") or []:
        if not isinstance(target, Mapping):
            continue
        kind = str(target.get("kind") or "").strip()
        service_name = str(target.get("service_name") or target.get("cell_id") or "").strip()
        if kind != "super-node" and "-super" not in service_name:
            continue
        if not service_name:
            continue
        functions = target.get("functions") if isinstance(target.get("functions"), Mapping) else {}
        statuses[service_name] = {
            "observed": True,
            "ok": bool(target.get("ok")),
            "guard_url": target.get("guard_url"),
            "source": "coolify-private-probe",
            "identity_ok": bool(target.get("identity_ok")),
            "topology_ok": bool(target.get("topology_ok")),
            "status_ok": bool(target.get("status_ok")),
            "healthz_ok": bool(target.get("healthz_ok")),
            "error": target.get("error") or "",
            "functions": dict(functions),
        }
    return statuses


def enrich_super_inventory_with_probe_status(
    nodes: Sequence[Mapping[str, Any]],
    probe_result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    statuses = super_statuses_from_probe_result(probe_result)
    enriched: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        copied = dict(node)
        status = statuses.get(str(copied.get("service_name") or ""))
        if status:
            copied["internal_status"] = status
        else:
            copied.setdefault(
                "internal_status",
                {
                    "observed": False,
                    "ok": False,
                    "source": "coolify-private-probe",
                    "reason": "private super-node guard status not observed yet",
                },
            )
        enriched.append(copied)
    return enriched


def networks_from_probe_result(probe_result: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    networks: dict[str, list[dict[str, Any]]] = {}
    result = probe_result.get("result") if isinstance(probe_result, Mapping) else None
    if not isinstance(result, Mapping):
        return networks
    for target in result.get("targets") or []:
        if not isinstance(target, Mapping):
            continue
        for key in ("identity", "topology", "status"):
            payload = target.get(key)
            if not isinstance(payload, Mapping):
                continue
            network_key = str(payload.get("network_key") or "").strip()
            if network_key and network_key not in {"control-plane", "allfather-control"}:
                networks.setdefault(network_key, []).append(dict(record))
    return networks


def discover_from_heads(plan: HeadPlan, args: argparse.Namespace) -> dict[str, Any]:
    heads: list[dict[str, Any]] = []
    networks: dict[str, Any] = {}
    probe_synced_count = 0
    probe_result_count = 0
    coolify_head_count = 0
    coolify_super_node_count = 0
    errors: list[str] = []

    for head in plan.heads:
        probe = sync_and_query_probe_for_head(plan, head, args)
        if probe.get("ok"):
            probe_synced_count += 1
        if probe.get("head_service_uuid"):
            coolify_head_count += 1
        probe_result = probe.get("probe_result") if isinstance(probe.get("probe_result"), Mapping) else {}
        if isinstance(probe_result, Mapping) and probe_result.get("ok"):
            probe_result_count += 1

        record = {
            "head_id": head.head_id,
            "server": head.coolify_server,
            "peer_guard_url": head.guard_url,
            "peer_guard_url_scope": "remote-vpn-peer-only",
            "operator_transport": "coolify-patch-probe",
            "public_guard_routes": False,
            "ssh_used": False,
            "direct_vpn_used": False,
            "probe": probe if getattr(args, "verbose", False) else compact_probe_record(probe),
        }
        heads.append(record)

        if not probe.get("ok"):
            errors.append(f"{head.head_id}: {probe.get('error') or 'Coolify probe sync/query failed'}")

        enriched_super_inventory = enrich_super_inventory_with_probe_status(
            [node for node in (probe.get("super_inventory") or []) if isinstance(node, Mapping)],
            probe_result if isinstance(probe_result, Mapping) else {},
        )
        for node in enriched_super_inventory:
            add_super_inventory_to_networks(networks, node)
            coolify_super_node_count += 1
        if probe.get("super_inventory_error"):
            errors.append(f"{head.head_id}: Coolify super-node inventory failed: {probe.get('super_inventory_error')}")

        for network_key, items in networks_from_probe_result(probe_result if isinstance(probe_result, Mapping) else {}, record).items():
            add_probe_records_to_networks(networks, network_key, items)

    all_probes_synced = probe_synced_count == len(plan.heads) if plan.heads else False
    topology_ready = probe_result_count > 0
    ok = bool(plan.heads) and all_probes_synced and topology_ready
    if ok:
        reason = ""
    elif not plan.heads:
        reason = "no all-father heads are planned"
    elif not all_probes_synced:
        reason = "one or more Coolify-managed discovery probes could not be synced"
    else:
        reason = "Coolify probes are synced, but no probe result has been observed yet"
    return {
        "ok": ok,
        "operation": "discover",
        "operator_transport": "coolify-patch-probe",
        "topology_source": "coolify-api-plus-private-probe-logs",
        "public_guard_routes": False,
        "ssh_used": False,
        "direct_vpn_used": False,
        "probe_services_left_running": True,
        "finalization_action": "not run; probes remain active for diagnosis",
        "summary": {
            "planned_heads": len(plan.heads),
            "coolify_seen_heads": coolify_head_count,
            "probe_services_synced": probe_synced_count,
            "probe_results_observed": probe_result_count,
            "coolify_seen_super_nodes": coolify_super_node_count,
            **super_internal_status_counts(networks),
            "topology_ready": topology_ready,
            "vpn_guard_urls_are_local_operator_urls": False,
        },
        "reason": reason,
        "errors": errors,
        "heads": heads,
        "networks": networks,
        "guardrails": plan.guardrails,
    }


def short_error_from_probe(probe: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return one compact diagnostic without leaking API paths or full Coolify records."""

    probe_result = probe.get("probe_result")
    if isinstance(probe_result, Mapping) and not probe_result.get("ok", False):
        error = str(probe_result.get("error") or "").strip()
        source = str(probe_result.get("source") or "").strip()
        if error:
            return {"source": source or "probe_result", "message": error}

    probe_logs = probe.get("probe_logs")
    if isinstance(probe_logs, Mapping) and not probe_logs.get("ok", False):
        error = str(probe_logs.get("error") or "").strip()
        source = str(probe_logs.get("source") or "").strip()
        if error:
            return {"source": source or "probe_logs", "message": error}

    coolify_api = probe.get("coolify_api")
    if isinstance(coolify_api, Mapping):
        last_error = coolify_api.get("last_error")
        if isinstance(last_error, Mapping):
            message = str(last_error.get("message") or "Coolify API request failed").strip()
            status = last_error.get("status")
            return {
                "source": "coolify-api",
                "message": message,
                "status": status,
                "operation": last_error.get("operation"),
            }

    return None


def compact_discover_for_operator(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Default discover output: enough for operators, no per-request/API spam."""

    heads: list[dict[str, Any]] = []
    for head in payload.get("heads") or []:
        if not isinstance(head, Mapping):
            continue
        probe = head.get("probe") if isinstance(head.get("probe"), Mapping) else {}
        probe_result = probe.get("probe_result") if isinstance(probe.get("probe_result"), Mapping) else {}
        probe_logs = probe.get("probe_logs") if isinstance(probe.get("probe_logs"), Mapping) else {}
        coolify_api = probe.get("coolify_api") if isinstance(probe.get("coolify_api"), Mapping) else {}

        heads.append(
            {
                "head_id": head.get("head_id"),
                "server": head.get("server"),
                "operator_transport": head.get("operator_transport"),
                "peer_guard_url_scope": head.get("peer_guard_url_scope"),
                "head_service": {
                    "name": probe.get("head_service_name"),
                    "uuid": probe.get("head_service_uuid"),
                },
                "probe_service": {
                    "name": probe.get("probe_service_name"),
                    "uuid": probe.get("probe_service_uuid"),
                    "deployed": bool(probe.get("probe_deployed")),
                    "left_running": bool(probe.get("probe_left_running")),
                },
                "probe_result_observed": bool(probe_result.get("ok")),
                "probe_logs_available": bool(probe_logs.get("ok")),
                "probe_target_count": len(probe.get("probe_targets") or []),
                "coolify_api": {
                    "attempt_count": coolify_api.get("attempt_count", 0),
                    "failed_count": coolify_api.get("failed_count", 0),
                },
                "last_error": short_error_from_probe(probe),
                "public_guard_routes": False,
                "ssh_used": False,
                "direct_vpn_used": False,
            }
        )

    return {
        "ok": bool(payload.get("ok")),
        "operation": payload.get("operation"),
        "operator_transport": payload.get("operator_transport"),
        "reason": payload.get("reason") or "",
        "summary": payload.get("summary") or {},
        "heads": heads,
        "networks": payload.get("networks") or {},
        "guardrails": payload.get("guardrails") or {},
        "probe_services_left_running": bool(payload.get("probe_services_left_running")),
        "finalization_action": payload.get("finalization_action"),
        "public_guard_routes": False,
        "ssh_used": False,
        "direct_vpn_used": False,
    }



def clean_node_network_key(value: object) -> str:
    network = str(value or "").strip().lower()
    if network not in SUPPORTED_NODE_NETWORKS:
        raise AllfatherControlError(f"add-node network must be one of {', '.join(SUPPORTED_NODE_NETWORKS)}; got {value!r}.")
    return network


def host_letter_for_head(head: HeadNode) -> str:
    slot = str(head.slot or "").strip().lower()
    if re.fullmatch(r"[a-z]+", slot):
        return slot
    # Fall back to the final alphabetic suffix if a migrated host name was used.
    match = re.search(r"([a-z])$", str(head.coolify_server or "").lower())
    return match.group(1) if match else "x"


def super_prefix(network_key: str, head: HeadNode) -> str:
    return f"{clean_node_network_key(network_key)}{host_letter_for_head(head)}"


def super_service_name(network_key: str, head: HeadNode, ordinal: int) -> str:
    if ordinal < 1:
        raise AllfatherControlError("super-node ordinal must be >= 1")
    return f"{super_prefix(network_key, head)}-super{ordinal}"


def super_component_names(network_key: str, head: HeadNode, ordinal: int) -> dict[str, str]:
    prefix = super_prefix(network_key, head)
    return {
        "super": f"{prefix}-super{ordinal}",
        "hub": f"{prefix}-hub{ordinal}",
        "fdb": f"{prefix}-fdb{ordinal}",
        "validator_rpc": f"{prefix}-validator-rpc{ordinal}",
        "guard": f"{prefix}-guard{ordinal}",
        "rpc_route": f"{prefix}-rpc{ordinal}",
    }


def _network_base(network_key: str, testnet_base: int, mainnet_base: int) -> int:
    return mainnet_base if clean_node_network_key(network_key) == "mainnet" else testnet_base


def super_host_port(network_key: str, head: HeadNode, ordinal: int, *, testnet_base: int, mainnet_base: int) -> int:
    # Ports are host-local and deterministic. Host A gets base+0.., host B gets
    # base+100.., etc. The operator never supplies the ordinal; it is derived
    # from Coolify inventory.
    letter = host_letter_for_head(head)
    host_offset = max(0, ord(letter[0]) - ord("a")) * 100 if letter else 0
    return _network_base(network_key, testnet_base, mainnet_base) + host_offset + max(0, ordinal - 1)


def private_state_for_args(args: argparse.Namespace) -> dict[str, Any]:
    return load_yaml_mapping(repo_relative_path(args.private_state))


def _merge_wallet_record(base: Mapping[str, Any] | None, override: Mapping[str, Any] | None) -> dict[str, Any]:
    """Merge wallet records while ignoring empty placeholder overrides."""

    merged: dict[str, Any] = {}
    for source in (base, override):
        if not isinstance(source, Mapping):
            continue
        for key, value in source.items():
            if value is None or value == "":
                continue
            if isinstance(value, str) and PRIVATE_PLACEHOLDER_RE.match(value):
                continue
            merged[str(key)] = value
    return merged


def _wallet_mapping_from_state_path(state: Mapping[str, Any], *path: str) -> dict[str, Any]:
    value: Any = state
    for segment in path:
        if not isinstance(value, Mapping):
            return {}
        value = value.get(segment)
    return dict(value) if isinstance(value, Mapping) else {}


def network_wallets_from_private_state(state: Mapping[str, Any], network_key: str) -> dict[str, Any]:
    """Return wallet material for a network with global defaults as fallback.

    ``all_father.private.yaml`` is not topology, but it can carry bootstrap
    wallet material.  Older private-state files often put deployer/admin keys
    under ``wallets.defaults`` instead of ``networks.<network>.wallets``.  The
    add-node path should use those defaults instead of forcing the operator to
    duplicate keys by hand.
    """

    network_key = clean_node_network_key(network_key)
    default_wallets = _wallet_mapping_from_state_path(state, "wallets", "defaults")
    network_wallets = _wallet_mapping_from_state_path(state, "networks", network_key, "wallets")

    wallet_names = sorted(set(default_wallets.keys()) | set(network_wallets.keys()))
    merged: dict[str, Any] = {}
    for wallet_name in wallet_names:
        record = _merge_wallet_record(
            default_wallets.get(wallet_name) if isinstance(default_wallets.get(wallet_name), Mapping) else None,
            network_wallets.get(wallet_name) if isinstance(network_wallets.get(wallet_name), Mapping) else None,
        )
        if record:
            merged[wallet_name] = record
    return merged


def wallet_record(wallets: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = wallets.get(name)
    return dict(value) if isinstance(value, Mapping) else {}


def wallet_private_key(wallets: Mapping[str, Any], name: str) -> str:
    record = wallet_record(wallets, name)
    return str(record.get("private_key") or "").strip()


def wallet_address(wallets: Mapping[str, Any], name: str) -> str:
    record = wallet_record(wallets, name)
    return str(record.get("address") or "").strip()




def ensure_mapping_child(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        value = {}
        parent[key] = value
    return value


def ensure_network_private_state(state: dict[str, Any], network_key: str) -> dict[str, Any]:
    networks = ensure_mapping_child(state, "networks")
    network = ensure_mapping_child(networks, clean_node_network_key(network_key))
    ensure_mapping_child(network, "wallets")
    ensure_mapping_child(network, "foundationdb")
    return network


def materialize_wallet_key(
    wallets: dict[str, Any],
    wallet_name: str,
    *,
    reason: str,
    generated: list[dict[str, Any]],
) -> None:
    record = wallets.get(wallet_name)
    if not isinstance(record, dict):
        record = {}
        wallets[wallet_name] = record
    if nonempty_private_value(record.get("private_key")):
        return
    record["private_key"] = generated_private_key()
    record.setdefault("address", None)
    metadata = ensure_mapping_child(record, "metadata")
    metadata.update(
        {
            "generated_by": PRIVATE_STATE_GENERATOR,
            "generated_at": utc_now_iso(),
            "reason": reason,
            "address_derivation": "runtime-derive-from-private-key",
        }
    )
    generated.append({"kind": "wallet_private_key", "wallet": wallet_name, "reason": reason})


def materialize_fdb_identity(
    network: dict[str, Any],
    network_key: str,
    *,
    generated: list[dict[str, Any]],
) -> dict[str, Any]:
    network_key = clean_node_network_key(network_key)
    fdb = ensure_mapping_child(network, "foundationdb")
    raw_description = nonempty_private_value(fdb.get("cluster_description"))
    if raw_description:
        safe_description = sanitize_fdb_cluster_description(raw_description)
        if safe_description != raw_description:
            fdb["cluster_description"] = safe_description
            generated.append(
                {
                    "kind": "fdb_cluster_description_normalized",
                    "network": network_key,
                    "from": raw_description,
                    "to": safe_description,
                }
            )
    else:
        fdb["cluster_description"] = fdb_cluster_description_for_network(network_key)
        generated.append({"kind": "fdb_cluster_description", "network": network_key})
    if not nonempty_private_value(fdb.get("cluster_id")):
        fdb["cluster_id"] = generated_cluster_id()
        generated.append({"kind": "fdb_cluster_id", "network": network_key})
    fdb.setdefault("coordinator_policy", "first-node-then-expand")
    fdb.setdefault("reconfigure_after_join", True)
    return fdb


def materialize_private_state_for_add_node(
    state: Mapping[str, Any],
    path: Path,
    network_key: str,
    *,
    ordinal: int,
    cell_id: str,
    no_contracts: bool,
    dry_run: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Ensure add-node has local bootstrap secrets without treating state as topology.

    A node-scoped ``hub_admin`` key is generated for every new super-node, while
    the first-node ``deployer`` key remains network-scoped because it deploys
    network-level contracts once.  The file remains a seed/control file: it
    stores secrets and FDB cluster identity, not the list of running super-nodes.
    """

    network_key = clean_node_network_key(network_key)
    mutable_state = json.loads(json.dumps(dict(state)))
    network = ensure_network_private_state(mutable_state, network_key)
    wallets = ensure_mapping_child(network, "wallets")
    node_seed_material = ensure_mapping_child(network, "node_seed_material")
    node_seed = ensure_mapping_child(node_seed_material, str(cell_id or f"super{ordinal}"))
    node_wallets = ensure_mapping_child(node_seed, "wallets")
    generated: list[dict[str, Any]] = []

    materialize_wallet_key(
        node_wallets,
        "hub_admin",
        reason=f"{network_key} {cell_id} node hub_admin bootstrap",
        generated=generated,
    )
    if ordinal == 1 and not no_contracts:
        materialize_wallet_key(
            wallets,
            "deployer",
            reason=f"{network_key} first-node contract bootstrap",
            generated=generated,
        )

    fdb = materialize_fdb_identity(network, network_key, generated=generated)

    if generated and not dry_run:
        write_yaml_mapping(path, mutable_state)

    wallets_for_network = network_wallets_from_private_state(mutable_state, network_key)
    node_hub_admin = _wallet_mapping_from_state_path(
        mutable_state,
        "networks",
        network_key,
        "node_seed_material",
        str(cell_id or f"super{ordinal}"),
        "wallets",
        "hub_admin",
    )
    if node_hub_admin:
        wallets_for_network = dict(wallets_for_network)
        wallets_for_network["hub_admin"] = node_hub_admin
    return mutable_state, wallets_for_network, {
        "path": display_path(path),
        "written": bool(generated and not dry_run),
        "dry_run": bool(dry_run),
        "generated": generated,
        "wallets_generated": [item["wallet"] for item in generated if item.get("kind") == "wallet_private_key"],
        "node_hub_admin_cell_id": str(cell_id or f"super{ordinal}"),
        "node_hub_admin_private_key_present": bool(wallet_private_key(wallets_for_network, "hub_admin")),
        "node_seed_material_path": f"networks.{network_key}.node_seed_material.{cell_id or f'super{ordinal}'}.wallets.hub_admin",
        "fdb_identity_generated": any(str(item.get("kind", "")).startswith("fdb_") for item in generated),
        "fdb_cluster_description": str(fdb.get("cluster_description") or ""),
        "fdb_cluster_id_present": bool(nonempty_private_value(fdb.get("cluster_id"))),
        "note": "Private keys and FDB cluster identity are seed material, not topology.",
    }


def fdb_identity_from_private_state(state: Mapping[str, Any], network_key: str) -> dict[str, Any]:
    network_key = clean_node_network_key(network_key)
    network = _wallet_mapping_from_state_path(state, "networks", network_key)
    fdb = network.get("foundationdb") if isinstance(network, Mapping) else {}
    if not isinstance(fdb, Mapping):
        fdb = {}
    raw_description = nonempty_private_value(fdb.get("cluster_description"))
    description = sanitize_fdb_cluster_description(raw_description) if raw_description else fdb_cluster_description_for_network(network_key)
    cluster_id = nonempty_private_value(fdb.get("cluster_id")) or "missing-cluster-id"
    return {
        "cluster_description": description,
        "cluster_id": cluster_id,
        "coordinator_policy": str(fdb.get("coordinator_policy") or "first-node-then-expand"),
        "reconfigure_after_join": bool(fdb.get("reconfigure_after_join", True)),
    }

def require_wallet_material_for_add_node(network_key: str, ordinal: int, wallets: Mapping[str, Any], *, no_contracts: bool) -> dict[str, Any]:
    """Return wallet bootstrap intent for add-node.

    ``add-node`` materializes missing bootstrap keys before this function runs, so
    missing keys are not an operator blocker.  Contract work is still deferred
    until at least one validator-RPC is live.
    """

    hub_admin_key = wallet_private_key(wallets, "hub_admin")
    hub_admin_address = wallet_address(wallets, "hub_admin")
    contracts_requested = ordinal == 1 and not no_contracts
    deployer_key = wallet_private_key(wallets, "deployer")
    return {
        "hub_admin_address": hub_admin_address,
        "hub_admin_private_key": hub_admin_key,
        "hub_admin_private_key_present": bool(hub_admin_key),
        "hub_admin_create_requested": False,
        "hub_admin_key_source": "private-state-or-generated",
        "deployer_address": wallet_address(wallets, "deployer"),
        "deployer_private_key": deployer_key,
        "deployer_private_key_present": bool(deployer_key),
        "contracts_requested": contracts_requested,
        "contracts_deferred_until_hub_admin_ready": False,
        "contracts_deferred_until_deployer_ready": contracts_requested and not bool(deployer_key),
    }


def choose_head_for_host(plan: HeadPlan, host: str) -> HeadNode:
    wanted = str(host or "").strip().lower()
    if not wanted:
        raise AllfatherControlError("--host is required for add-node.")
    matches = [
        head
        for head in plan.heads
        if wanted in {str(head.coolify_server).lower(), str(head.slot).lower(), str(head.head_id).lower()}
    ]
    if not matches:
        known = ", ".join(head.coolify_server for head in plan.heads)
        raise AllfatherControlError(f"Unknown all-father Coolify host {host!r}; known hosts: {known}")
    if len(matches) > 1:
        raise AllfatherControlError(f"Host selector {host!r} matched more than one all-father head.")
    return matches[0]


def service_items_for_client(client: Any, tried: list[dict[str, Any]]) -> list[dict[str, Any]]:
    response, services = hub_service_tool().list_services(client)
    tried.append({"operation": "list-services", "response": hub_service_tool().response_to_dict(response), "count": len(services)})
    if not response.ok:
        raise AllfatherControlError(f"Could not list Coolify services with HTTP {response.status}: {response.body}")
    return services


def service_name_from_item(item: Mapping[str, Any]) -> str:
    for key in ("name", "human_name"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    applications = item.get("applications")
    if isinstance(applications, list):
        for app in applications:
            if isinstance(app, Mapping):
                value = str(app.get("name") or "").strip()
                if value:
                    return value
    return ""


def existing_super_ordinals(services: Iterable[Mapping[str, Any]], network_key: str, head: HeadNode) -> list[int]:
    prefix = re.escape(super_prefix(network_key, head))
    pattern = re.compile(rf"^{prefix}-super([1-9][0-9]*)$")
    ordinals: list[int] = []
    for item in services:
        name = service_name_from_item(item)
        match = pattern.match(name)
        if match:
            ordinals.append(int(match.group(1)))
    return sorted(set(ordinals))


def missing_super_ordinal_gaps(ordinals: Iterable[int]) -> list[int]:
    unique = sorted({int(item) for item in ordinals if int(item) > 0})
    if not unique:
        return []
    expected = set(range(1, max(unique) + 1))
    return sorted(expected.difference(unique))


def require_contiguous_super_ordinals(ordinals: Iterable[int], network_key: str, head: HeadNode) -> list[int]:
    """Return sorted ordinals, rejecting impossible host-local gaps.

    all-father add/remove never accepts an ordinal from the operator.  That only
    stays safe if the live Coolify inventory for one network+host is contiguous:
    super1, super2, ... superN.  If Coolify still reports a just-deleted service
    or a previous run left only super2, creating super3 would make recovery
    worse, so add-node refuses and asks the operator to shrink back to pristine.
    """

    unique = sorted({int(item) for item in ordinals if int(item) > 0})
    gaps = missing_super_ordinal_gaps(unique)
    if gaps:
        found = ", ".join(str(item) for item in unique)
        missing = ", ".join(str(item) for item in gaps)
        raise AllfatherControlError(
            f"Non-contiguous {clean_node_network_key(network_key)} super-node inventory on {head.coolify_server}: "
            f"found ordinal(s) [{found}], missing [{missing}]. Refusing to add another node because all-father "
            "ordinals must remain contiguous. Run remove-node for this network+host until the inventory is pristine, "
            "then run add-node again."
        )
    return unique


def fdb_seed_identity_present(state: Mapping[str, Any], network_key: str) -> bool:
    network_key = clean_node_network_key(network_key)
    networks = state.get("networks")
    network = networks.get(network_key) if isinstance(networks, Mapping) else {}
    fdb = network.get("foundationdb") if isinstance(network, Mapping) else {}
    if not isinstance(fdb, Mapping):
        return False
    return bool(nonempty_private_value(fdb.get("cluster_description")) and nonempty_private_value(fdb.get("cluster_id")))


def require_fdb_seed_for_existing_super_nodes(
    state: Mapping[str, Any],
    private_state_path: Path,
    network_key: str,
    head: HeadNode,
    existing_nodes: Sequence[Mapping[str, Any]],
) -> None:
    if not existing_nodes or fdb_seed_identity_present(state, network_key):
        return
    names = ", ".join(str(item.get("service_name") or "?") for item in existing_nodes)
    raise AllfatherControlError(
        f"Coolify reports existing {clean_node_network_key(network_key)} super-node(s) on {head.coolify_server} "
        f"({names}), but {display_path(private_state_path)} has no FoundationDB seed identity for that network. "
        "This usually means a just-deleted service is still visible in Coolify inventory or private state was cleaned "
        "before Coolify deletion fully settled. Do not create the next node yet; run discover or remove-node again and "
        "wait for the stale service to disappear, then run add-node again."
    )


def super_inventory_entry(network_key: str, head: HeadNode, ordinal: int, *, source: str = "coolify-inventory") -> dict[str, Any]:
    components = super_component_names(network_key, head, ordinal)
    return {
        "source": source,
        "network_key": clean_node_network_key(network_key),
        "coolify_server": head.coolify_server,
        "host_slot": head.slot,
        "host_prefix": super_prefix(network_key, head),
        "ordinal": ordinal,
        "service_name": components["super"],
        "components": components,
        "vpn_ip": head.guard_publish_host,
        "guard_url": f"http://{head.guard_publish_host}:{super_host_port(network_key, head, ordinal, testnet_base=DEFAULT_TESTNET_SUPER_GUARD_BASE, mainnet_base=DEFAULT_MAINNET_SUPER_GUARD_BASE)}",
        "guard_host_port": super_host_port(network_key, head, ordinal, testnet_base=DEFAULT_TESTNET_SUPER_GUARD_BASE, mainnet_base=DEFAULT_MAINNET_SUPER_GUARD_BASE),
        "fdb_endpoint": f"{head.guard_publish_host}:{super_host_port(network_key, head, ordinal, testnet_base=DEFAULT_TESTNET_FDB_BASE, mainnet_base=DEFAULT_MAINNET_FDB_BASE)}",
        "fdb_host_port": super_host_port(network_key, head, ordinal, testnet_base=DEFAULT_TESTNET_FDB_BASE, mainnet_base=DEFAULT_MAINNET_FDB_BASE),
        "p2p_endpoint": f"{head.guard_publish_host}:{super_host_port(network_key, head, ordinal, testnet_base=DEFAULT_TESTNET_P2P_BASE, mainnet_base=DEFAULT_MAINNET_P2P_BASE)}",
        "p2p_host_port": super_host_port(network_key, head, ordinal, testnet_base=DEFAULT_TESTNET_P2P_BASE, mainnet_base=DEFAULT_MAINNET_P2P_BASE),
    }


def existing_super_inventory_from_services(
    services: Iterable[Mapping[str, Any]],
    network_key: str,
    head: HeadNode,
) -> list[dict[str, Any]]:
    return [
        super_inventory_entry(network_key, head, ordinal)
        for ordinal in existing_super_ordinals(services, network_key, head)
    ]


def service_uuid_from_item(item: Mapping[str, Any]) -> str:
    value = str(item.get("uuid") or item.get("id") or "").strip()
    return value


def service_status_from_item(item: Mapping[str, Any]) -> str:
    for key in ("status", "application_status", "service_status"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    applications = item.get("applications")
    if isinstance(applications, list):
        statuses = [
            str(app.get("status") or "").strip()
            for app in applications
            if isinstance(app, Mapping) and str(app.get("status") or "").strip()
        ]
        if statuses:
            if len(set(statuses)) == 1:
                return statuses[0]
            return ",".join(statuses)
    return ""


def matching_service_items(services: Iterable[Mapping[str, Any]], service_name: str) -> list[Mapping[str, Any]]:
    clean = str(service_name or "").strip()
    return [
        item
        for item in services
        if isinstance(item, Mapping) and service_name_from_item(item) == clean
    ]


def service_item_summaries(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        summaries.append(
            {
                "name": service_name_from_item(item),
                "uuid": service_uuid_from_item(item),
                "status": service_status_from_item(item),
                "application": first_application_summary_from_item(item),
            }
        )
    return summaries


def wait_for_add_node_slot_preflight(
    client: Any,
    *,
    network_key: str,
    head: HeadNode,
    service_name: str,
    expected_existing_ordinals: Sequence[int],
    tried: list[dict[str, Any]],
    wait_s: float,
    poll_s: float,
    stable_s: float,
) -> dict[str, Any]:
    """Verify the target Coolify service slot is stably clean before add-node deploys.

    Coolify deletes can briefly report stale inventory and then disappear, while
    old Docker containers from a failed deployment may still exist.  We cannot use
    SSH or a local direct Docker/VPN probe here, so this API preflight makes the
    boundary explicit: the intended service name must be absent, and the lower
    ordinals we are joining after must remain unchanged for a small stable window.
    """

    wait_s = max(0.0, float(wait_s or 0.0))
    poll_s = max(0.5, float(poll_s or DEFAULT_ADD_NODE_PREFLIGHT_POLL_S))
    stable_s = max(0.0, float(stable_s or 0.0))
    expected_ordinals = sorted({int(item) for item in expected_existing_ordinals if int(item) > 0})
    deadline = time.monotonic() + wait_s
    stable_since: float | None = None
    attempts = 0
    last_services: list[Mapping[str, Any]] = []
    last_ordinals: list[int] = []
    last_matches: list[Mapping[str, Any]] = []
    last_reason = "not checked yet"

    while True:
        attempts += 1
        last_services = service_items_for_client(client, tried)
        try:
            last_ordinals = require_contiguous_super_ordinals(existing_super_ordinals(last_services, network_key, head), network_key, head)
        except AllfatherControlError as exc:
            return {
                "enabled": True,
                "ready": False,
                "terminal": True,
                "reason": str(exc),
                "attempt_count": attempts,
                "wait_s": wait_s,
                "poll_s": poll_s,
                "stable_s": stable_s,
                "service_name": service_name,
                "expected_existing_ordinals": expected_ordinals,
                "observed_ordinals": existing_super_ordinals(last_services, network_key, head),
                "matches": service_item_summaries(matching_service_items(last_services, service_name)),
                "public_guard_routes": False,
                "ssh_used": False,
                "direct_vpn_used": False,
            }

        last_matches = matching_service_items(last_services, service_name)
        if last_ordinals != expected_ordinals:
            stable_since = None
            last_reason = (
                f"Coolify super-node inventory changed while preparing {service_name!r}: "
                f"expected existing ordinal(s) {expected_ordinals}, observed {last_ordinals}."
            )
        elif last_matches:
            stable_since = None
            last_reason = (
                f"Coolify still reports target service {service_name!r}; refusing to create/deploy over a stale slot."
            )
        else:
            now = time.monotonic()
            if stable_since is None:
                stable_since = now
            stable_for = now - stable_since
            if stable_for >= stable_s or wait_s <= 0:
                return {
                    "enabled": True,
                    "ready": True,
                    "terminal": False,
                    "reason": "target Coolify service slot is stably clean",
                    "attempt_count": attempts,
                    "wait_s": wait_s,
                    "poll_s": poll_s,
                    "stable_s": stable_s,
                    "stable_for_s": round(stable_for, 3),
                    "service_name": service_name,
                    "expected_existing_ordinals": expected_ordinals,
                    "observed_ordinals": last_ordinals,
                    "matches": [],
                    "public_guard_routes": False,
                    "ssh_used": False,
                    "direct_vpn_used": False,
                    "services": list(last_services),
                }
            last_reason = f"target service slot is clean but has only been stable for {stable_for:.1f}s"

        if time.monotonic() >= deadline:
            break
        time.sleep(min(poll_s, max(0.0, deadline - time.monotonic())))

    return {
        "enabled": True,
        "ready": False,
        "terminal": False,
        "reason": last_reason,
        "attempt_count": attempts,
        "wait_s": wait_s,
        "poll_s": poll_s,
        "stable_s": stable_s,
        "service_name": service_name,
        "expected_existing_ordinals": expected_ordinals,
        "observed_ordinals": last_ordinals,
        "matches": service_item_summaries(last_matches),
        "public_guard_routes": False,
        "ssh_used": False,
        "direct_vpn_used": False,
        "services": list(last_services),
    }


def require_synced_super_service_ready_for_deploy(
    client: Any,
    *,
    service_name: str,
    service_uuid: str,
    tried: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify the service we are about to deploy is the only Coolify match."""

    services = service_items_for_client(client, tried)
    matches = matching_service_items(services, service_name)
    summaries = service_item_summaries(matches)
    if len(matches) != 1:
        raise AllfatherControlError(
            f"Refusing to deploy {service_name!r}: Coolify inventory has {len(matches)} matching service records "
            f"after service sync. Matches: {summaries}. Remove stale/duplicate services before add-node deploys."
        )
    observed_uuid = service_uuid_from_item(matches[0])
    if observed_uuid and service_uuid and observed_uuid != service_uuid:
        raise AllfatherControlError(
            f"Refusing to deploy {service_name!r}: Coolify returned service UUID {service_uuid!r}, "
            f"but live inventory now reports {observed_uuid!r}."
        )
    return {
        "service_name": service_name,
        "service_uuid": observed_uuid or service_uuid,
        "match_count": len(matches),
        "status": service_status_from_item(matches[0]),
        "matches": summaries,
        "public_guard_routes": False,
        "ssh_used": False,
        "direct_vpn_used": False,
    }


def first_application_summary_from_item(item: Mapping[str, Any]) -> dict[str, Any]:
    applications = item.get("applications")
    if not isinstance(applications, list):
        return {}
    for app in applications:
        if not isinstance(app, Mapping):
            continue
        summary = _small_mapping(app, ["uuid", "name", "status", "fqdn", "ports"])
        if summary:
            return summary
    return {}


def super_inventory_from_service_items(
    services: Iterable[Mapping[str, Any]],
    network_key: str,
    head: HeadNode,
) -> list[dict[str, Any]]:
    """Return live super-node inventory for one network+host from Coolify services.

    This is intentionally based on Coolify's service inventory, not on
    all_father.private.yaml.  It lets discover show super-nodes that have been
    created by add-node even before the new super-node guard reports its own
    internal topology.
    """

    prefix = re.escape(super_prefix(network_key, head))
    pattern = re.compile(rf"^{prefix}-super([1-9][0-9]*)$")
    entries: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in services:
        if not isinstance(item, Mapping):
            continue
        name = service_name_from_item(item)
        match = pattern.match(name)
        if not match:
            continue
        ordinal = int(match.group(1))
        if ordinal in seen:
            continue
        seen.add(ordinal)
        entry = super_inventory_entry(network_key, head, ordinal, source="coolify-service-list")
        entry["service_uuid"] = service_uuid_from_item(item)
        entry["status"] = service_status_from_item(item)
        app_summary = first_application_summary_from_item(item)
        if app_summary:
            entry["application"] = app_summary
        entry["observed_by"] = head.head_id
        entry["topology_source"] = "coolify-service-inventory"
        entries.append(entry)
    return sorted(entries, key=lambda item: int(item.get("ordinal") or 0))


def all_super_inventory_from_services(
    services: Iterable[Mapping[str, Any]],
    head: HeadNode,
) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    service_list = list(services)
    for network_key in SUPPORTED_NODE_NETWORKS:
        inventory.extend(super_inventory_from_service_items(service_list, network_key, head))
    return sorted(
        inventory,
        key=lambda item: (
            str(item.get("network_key") or ""),
            str(item.get("coolify_server") or ""),
            int(item.get("ordinal") or 0),
        ),
    )


def add_super_inventory_to_networks(networks: dict[str, Any], node: Mapping[str, Any]) -> None:
    network_key = str(node.get("network_key") or "").strip()
    host = str(node.get("coolify_server") or "").strip()
    if not network_key or not host:
        return
    network = networks.setdefault(network_key, {"hosts": {}, "super_node_count": 0})
    if not isinstance(network, dict):
        # Preserve old probe-only shape defensively by wrapping it.
        network = {"probe_records": network, "hosts": {}, "super_node_count": 0}
        networks[network_key] = network
    hosts = network.setdefault("hosts", {})
    if not isinstance(hosts, dict):
        hosts = {}
        network["hosts"] = hosts
    host_payload = hosts.setdefault(
        host,
        {
            "host_slot": node.get("host_slot"),
            "host_prefix": node.get("host_prefix"),
            "super_nodes": [],
            "super_node_count": 0,
        },
    )
    if not isinstance(host_payload, dict):
        host_payload = {"super_nodes": [], "super_node_count": 0}
        hosts[host] = host_payload
    nodes = host_payload.setdefault("super_nodes", [])
    if not isinstance(nodes, list):
        nodes = []
        host_payload["super_nodes"] = nodes
    existing_names = {str(item.get("service_name") or "") for item in nodes if isinstance(item, Mapping)}
    if str(node.get("service_name") or "") not in existing_names:
        nodes.append(dict(node))
        nodes.sort(key=lambda item: int(item.get("ordinal") or 0) if isinstance(item, Mapping) else 0)
    host_payload["super_node_count"] = len(nodes)
    network["super_node_count"] = sum(
        int(payload.get("super_node_count") or 0)
        for payload in hosts.values()
        if isinstance(payload, Mapping)
    )


def add_probe_records_to_networks(networks: dict[str, Any], network_key: str, records: Sequence[Mapping[str, Any]]) -> None:
    if not network_key:
        return
    network = networks.setdefault(network_key, {"hosts": {}, "super_node_count": 0})
    if not isinstance(network, dict):
        network = {"probe_records": [], "hosts": {}, "super_node_count": 0}
        networks[network_key] = network
    probe_records = network.setdefault("probe_records", [])
    if isinstance(probe_records, list):
        probe_records.extend(dict(item) for item in records)


def super_internal_status_counts(networks: Mapping[str, Any]) -> dict[str, int]:
    observed = 0
    healthy = 0
    for network in networks.values():
        if not isinstance(network, Mapping):
            continue
        hosts = network.get("hosts")
        if not isinstance(hosts, Mapping):
            continue
        for host_payload in hosts.values():
            if not isinstance(host_payload, Mapping):
                continue
            for node in host_payload.get("super_nodes") or []:
                if not isinstance(node, Mapping):
                    continue
                internal = node.get("internal_status")
                if not isinstance(internal, Mapping):
                    continue
                if internal.get("observed"):
                    observed += 1
                if internal.get("ok"):
                    healthy += 1
    return {"super_nodes_internal_observed": observed, "super_nodes_internal_healthy": healthy}


def fdb_cluster_file(description: str, cluster_id: str, coordinators: Sequence[str]) -> str:
    clean = [str(item).strip() for item in coordinators if str(item).strip()]
    safe_description = sanitize_fdb_cluster_description(description)
    return f"{safe_description}:{cluster_id}@{','.join(clean)}"


def fdb_plan_for_super_node(
    network_key: str,
    head: HeadNode,
    ordinal: int,
    *,
    existing_nodes: Sequence[Mapping[str, Any]],
    private_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the FDB cluster intent for adding one super-node.

    The first node initializes the cluster identity.  Later nodes join the
    existing cluster file first, then request a coordinator reconfiguration after
    the new fdbserver is healthy.  This prevents a second node from accidentally
    booting an isolated FDB cluster.
    """

    identity = fdb_identity_from_private_state(private_state, network_key)
    new_node = super_inventory_entry(network_key, head, ordinal, source="new-node")
    existing_endpoints = [
        str(node.get("fdb_endpoint") or "").strip()
        for node in existing_nodes
        if str(node.get("fdb_endpoint") or "").strip()
    ]
    current_coordinators = existing_endpoints or [new_node["fdb_endpoint"]]
    target_coordinators = sorted(set(current_coordinators + [new_node["fdb_endpoint"]]))

    first_node = not existing_endpoints
    action = "initialize-new-cluster" if first_node else "join-existing-cluster"
    join_cluster_file = fdb_cluster_file(identity["cluster_description"], identity["cluster_id"], current_coordinators)
    target_cluster_file = fdb_cluster_file(identity["cluster_description"], identity["cluster_id"], target_coordinators)
    return {
        "action": action,
        "first_node": first_node,
        "cluster_description": identity["cluster_description"],
        "cluster_id_present": identity["cluster_id"] != "missing-cluster-id",
        "cluster_file": join_cluster_file,
        "join_cluster_file": join_cluster_file,
        "target_cluster_file_after_reconfigure": target_cluster_file,
        "existing_nodes": list(existing_nodes),
        "new_node": new_node,
        "current_coordinators": current_coordinators,
        "target_coordinators": target_coordinators,
        "coordinator_reconfigure_required": not first_node and target_cluster_file != join_cluster_file,
        "reconfigure_after_join": bool(identity.get("reconfigure_after_join", True)),
        "guardrail": "Do not initialize an isolated FDB cluster when existing network nodes are present.",
    }


def next_super_ordinal_from_inventory(services: Iterable[Mapping[str, Any]], network_key: str, head: HeadNode) -> int:
    ordinals = require_contiguous_super_ordinals(existing_super_ordinals(services, network_key, head), network_key, head)
    return (max(ordinals) + 1) if ordinals else 1


def super_manifest(
    network_key: str,
    head: HeadNode,
    ordinal: int,
    *,
    wallets: Mapping[str, Any],
    private_state: Mapping[str, Any],
    existing_nodes: Sequence[Mapping[str, Any]],
    no_contracts: bool,
    publish_routes: bool,
) -> dict[str, Any]:
    names = super_component_names(network_key, head, ordinal)
    wallet_material = require_wallet_material_for_add_node(network_key, ordinal, wallets, no_contracts=no_contracts)
    contracts_requested = bool(wallet_material["contracts_requested"])
    fdb_plan = fdb_plan_for_super_node(
        network_key,
        head,
        ordinal,
        existing_nodes=existing_nodes,
        private_state=private_state,
    )
    return {
        "kind": "main_computer.allfather_super_node.v1",
        "network_key": clean_node_network_key(network_key),
        "cell_id": names["super"],
        "coolify_server": head.coolify_server,
        "host_slot": head.slot,
        "host_prefix": super_prefix(network_key, head),
        "ordinal": ordinal,
        "state_root": f"{DEFAULT_SUPER_STATE_ROOT_PREFIX}/{clean_node_network_key(network_key)}/{head.coolify_server}/{names['super']}",
        "components": names,
        "desired_counts": {
            "super_nodes": 1,
            "hub": 1,
            "foundationdb": 1,
            "qbft_validator_rpc": 1,
            "guard": 1,
            "hub_admin": 1,
            "contracts": 1 if contracts_requested else 0,
        },
        "bootstrap": {
            "hub_admin_requested": True,
            "hub_admin_private_key_present": bool(wallet_material["hub_admin_private_key_present"]),
            "hub_admin_create_requested": bool(wallet_material["hub_admin_create_requested"]),
            "hub_admin_key_source": wallet_material["hub_admin_key_source"],
            "hub_admin_deferred_until_live_validator_rpc": True,
            "contracts_requested": contracts_requested,
            "contracts_deferred_until_live_validator_rpc": True,
            "contracts_deferred_until_hub_admin_ready": bool(wallet_material["contracts_deferred_until_hub_admin_ready"]),
            "no_contracts": bool(no_contracts),
            "hub_public_cutover_deferred": True,
        },
        "public_routes": {
            "enabled": bool(publish_routes),
            "hub": f"https://{names['hub']}.greatlibrary.io",
            "rpc": f"https://{names['rpc_route']}.greatlibrary.io",
            "only_hub_and_rpc_are_public": True,
        },
        "ports": {
            "guard_container": DEFAULT_SUPER_GUARD_CONTAINER_PORT,
            "guard_host": super_host_port(network_key, head, ordinal, testnet_base=DEFAULT_TESTNET_SUPER_GUARD_BASE, mainnet_base=DEFAULT_MAINNET_SUPER_GUARD_BASE),
            "hub_container": DEFAULT_SUPER_HUB_CONTAINER_PORT,
            "rpc_container": DEFAULT_SUPER_RPC_CONTAINER_PORT,
            "fdb_container": DEFAULT_SUPER_FDB_CONTAINER_PORT,
            "fdb_host": super_host_port(network_key, head, ordinal, testnet_base=DEFAULT_TESTNET_FDB_BASE, mainnet_base=DEFAULT_MAINNET_FDB_BASE),
            "p2p_container": DEFAULT_SUPER_P2P_CONTAINER_PORT,
            "p2p_host": super_host_port(network_key, head, ordinal, testnet_base=DEFAULT_TESTNET_P2P_BASE, mainnet_base=DEFAULT_MAINNET_P2P_BASE),
        },
        "foundationdb": fdb_plan,
        "wallets": {
            "hub_admin": {
                "address": wallet_material["hub_admin_address"],
                "private_key_present": bool(wallet_material["hub_admin_private_key"]),
                "create_requested": bool(wallet_material["hub_admin_create_requested"]),
                "key_source": wallet_material["hub_admin_key_source"],
                "scope": "node",
                "cell_id": names["super"],
            },
            "deployer": {
                "address": wallet_material["deployer_address"],
                "private_key_present": bool(wallet_material["deployer_private_key"]),
            },
        },
        "guardrails": {
            "private_state_is_topology": False,
            "hub_admin_requires_live_qbft_validator_rpc": True,
            "contracts_require_live_qbft_validator_rpc": True,
            "bootstrap_heads_deploys_workloads": False,
            "public_guard_routes": False,
            "ssh_used": False,
        },
    }


def super_server_command_script() -> str:
    contract_sources_b64 = allfather_contract_sources_b64()
    script = r"""
from __future__ import annotations
import base64
import json
import os
import shutil
import signal
import re
import socket
import subprocess
import threading
import time
import textwrap
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import urllib.request
import urllib.error

manifest = json.loads(base64.b64decode(os.environ["MC_ALLFATHER_SUPER_MANIFEST_B64"]).decode("utf-8"))
port = int(os.environ.get("MC_ALLFATHER_GUARD_PORT", "41414"))
components = manifest.get("components") if isinstance(manifest.get("components"), dict) else {}
ports = manifest.get("ports") if isinstance(manifest.get("ports"), dict) else {}
bootstrap = manifest.get("bootstrap") if isinstance(manifest.get("bootstrap"), dict) else {}
fdb_plan = manifest.get("foundationdb") if isinstance(manifest.get("foundationdb"), dict) else {}
public_routes = manifest.get("public_routes") if isinstance(manifest.get("public_routes"), dict) else {}
state_root = Path(str(manifest.get("state_root") or "/data/main-computer/allfather/supernodes/unknown"))
network_key = str(manifest.get("network_key") or "")
cell_id = str(manifest.get("cell_id") or "")
ordinal = int(manifest.get("ordinal") or 0)
vpn_ip = str(manifest.get("vpn_ip") or (fdb_plan.get("new_node") or {}).get("vpn_ip") or "").strip()
CONTRACT_SOURCES_B64 = __ALLFATHER_CONTRACT_SOURCES_B64__
BOOTSTRAP_MARKER_BYTECODE = "0x6001600c60003960016000f300"

started_at = time.time()
lock = threading.RLock()
stop_requested = False
children = {}
children_started_at = {}
component_state = {}

child_log_echo_last = {}

def super_log(message: str) -> None:
    try:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(f"{ts} [allfather-super] {message}", flush=True)
    except Exception:
        pass

def rate_limited_super_log(key: str, message: str, interval_s: float = 30.0) -> None:
    now = time.time()
    with lock:
        last = float(child_log_echo_last.get(key) or 0.0)
        if now - last < float(interval_s):
            return
        child_log_echo_last[key] = now
    super_log(message)

def child_log_echo_names() -> set[str]:
    raw = os.environ.get("MC_ALLFATHER_STDOUT_CHILD_LOGS", "validator_rpc,hub")
    return {item.strip() for item in raw.split(",") if item.strip()}

def child_log_line_should_echo(name: str, line: str) -> bool:
    lower = str(line or "").lower()
    if not lower.strip():
        return False
    if name == "validator_rpc":
        return any(
            marker in lower
            for marker in (
                "qbft",
                "bft",
                "produced empty block",
                "imported #",
                "blockchain sync",
                "json-rpc",
                "json rpc",
                "rpc-http",
                "p2p",
                "peer",
                "enode",
                "mining",
                "validator",
                "error",
                "exception",
                "warn",
            )
        )
    if name == "hub":
        return any(marker in lower for marker in ("bootstrap", "listening", "health", "error", "exception", "warn"))
    return any(marker in lower for marker in ("error", "exception", "warn"))

def echo_child_log_line(name: str, line: str) -> None:
    if name not in child_log_echo_names():
        return
    if not child_log_line_should_echo(name, line):
        return
    lower = line.lower()
    if "produced empty block" in lower:
        rate_limited_super_log(f"child-log:{name}:produced-empty-block", f"[{name}] {line.rstrip()}", interval_s=float(os.environ.get("MC_ALLFATHER_BESU_BLOCK_LOG_INTERVAL_S", "30")))
        return
    if "get /healthz" in lower or "get /status" in lower or "get /identity" in lower or "get /topology" in lower:
        return
    print(f"[allfather-child:{name}] {line.rstrip()}", flush=True)


def now_s() -> float:
    return round(time.time() - started_at, 3)

def state(component_key: str, **updates) -> None:
    with lock:
        current = component_state.setdefault(component_key, {})
        current.update(updates)
        current["updated_uptime_s"] = now_s()

def command_exists(name: str) -> bool:
    return bool(shutil.which(name))

def port_open(host: str, check_port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, int(check_port)), timeout=timeout):
            return True
    except Exception:
        return False

def http_json_ok(url: str, timeout: float = 1.0) -> bool:
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        payload = json.loads(raw.decode("utf-8"))
        return isinstance(payload, dict) and bool(payload.get("ok", True))
    except Exception:
        return False

def rpc_json_call(url: str, method: str, params: list | None = None, timeout: float = 1.0) -> tuple[bool, object, str]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": list(params or []),
    }
    raw = json.dumps(payload).encode("utf-8")
    try:
        request = urllib.request.Request(
            url,
            data=raw,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
        decoded = json.loads(body.decode("utf-8"))
        if not isinstance(decoded, dict):
            return False, None, "json-rpc response was not an object"
        if decoded.get("error"):
            return False, decoded.get("error"), "json-rpc error"
        return True, decoded.get("result"), ""
    except Exception as exc:
        return False, None, f"{type(exc).__name__}: {exc}"

def rpc_block_number(url: str, timeout: float = 1.0) -> tuple[bool, int | None, str]:
    ok, result, error = rpc_json_call(url, "eth_blockNumber", timeout=timeout)
    if not ok:
        return False, None, error
    try:
        if isinstance(result, str):
            return True, int(result, 16), ""
        if isinstance(result, int):
            return True, int(result), ""
    except Exception as exc:
        return False, None, f"invalid eth_blockNumber result: {type(exc).__name__}: {exc}"
    return False, None, f"invalid eth_blockNumber result: {result!r}"

def latest_besu_log_block_number(log_text: str) -> int | None:
    latest: int | None = None
    for match in re.finditer(r"Produced empty block #(\d+)", str(log_text or "")):
        try:
            latest = int(match.group(1))
        except Exception:
            pass
    return latest

def tail_log(name: str, max_bytes: int = 4000) -> str:
    path = state_root / "logs" / f"{name}.log"
    try:
        if not path.exists():
            return ""
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - int(max_bytes)))
            return handle.read().decode("utf-8", "replace")[-int(max_bytes):]
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"

def ensure_dirs() -> None:
    for sub in ("foundationdb/data", "foundationdb/logs", "qbft/data", "qbft/config", "hub", "logs"):
        (state_root / sub).mkdir(parents=True, exist_ok=True)
    (state_root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def write_fdb_cluster_file() -> Path:
    ensure_dirs()
    cluster = str(fdb_plan.get("cluster_file") or fdb_plan.get("join_cluster_file") or "").strip()
    if not cluster:
        cluster = f"{cell_id}:0000000000000000@127.0.0.1:{ports.get('fdb_container') or 4550}"
    cluster_path = state_root / "foundationdb" / "fdb.cluster"
    cluster_path.write_text(cluster + "\n", encoding="utf-8")
    try:
        Path("/etc/foundationdb").mkdir(parents=True, exist_ok=True)
        Path("/etc/foundationdb/fdb.cluster").write_text(cluster + "\n", encoding="utf-8")
    except Exception:
        pass
    return cluster_path

def child_running(name: str) -> bool:
    proc = children.get(name)
    return proc is not None and proc.poll() is None

def child_exit(name: str):
    proc = children.get(name)
    if proc is None:
        return None
    code = proc.poll()
    return None if code is None else int(code)

def child_uptime_s(name: str) -> float | None:
    started = children_started_at.get(name)
    if started is None:
        return None
    return round(max(0.0, time.time() - float(started)), 3)

def child_log_path_for(name: str) -> Path:
    log_dir = state_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{name}.log"

def rotate_child_log(name: str) -> Path:
    log_path = child_log_path_for(name)
    previous_path = log_path.with_name(f"{name}.previous.log")
    try:
        if log_path.exists():
            if previous_path.exists():
                previous_path.unlink()
            log_path.replace(previous_path)
    except Exception:
        pass
    return log_path

def pipe_child_output(name: str, pipe, log_path: Path) -> None:
    try:
        with open(log_path, "ab", buffering=0) as handle:
            while True:
                chunk = pipe.readline()
                if not chunk:
                    break
                handle.write(chunk)
                try:
                    line = chunk.decode("utf-8", "replace")
                except Exception:
                    line = repr(chunk)
                echo_child_log_line(name, line)
    except Exception as exc:
        super_log(f"child_log_pipe_error name={name} error={type(exc).__name__}: {exc}")

def start_child(name: str, command: list[str], *, cwd: str | None = None, env_extra: dict | None = None) -> bool:
    if child_running(name):
        return True
    env = os.environ.copy()
    env["MC_ALLFATHER_PROCESS_NAME"] = name
    env["MC_ALLFATHER_NETWORK"] = network_key
    env["MC_ALLFATHER_CELL_ID"] = cell_id
    if env_extra:
        env.update({str(k): str(v) for k, v in env_extra.items()})
    try:
        log_path = rotate_child_log(name)
        super_log("child_start name=" + name + " command=" + " ".join(str(part) for part in command[:4]))
        proc = subprocess.Popen(
            command,
            cwd=cwd or str(state_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        children[name] = proc
        children_started_at[name] = time.time()
        threading.Thread(target=pipe_child_output, args=(name, proc.stdout, log_path), daemon=True).start()
        return True
    except Exception as exc:
        state(name, desired=True, running=False, status="start-failed", last_error=f"{type(exc).__name__}: {exc}", command=command)
        super_log(f"child_start_failed name={name} error={type(exc).__name__}: {exc}")
        return False

def run_once(name: str, command: list[str], timeout: int = 30) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            command,
            cwd=str(state_root),
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode == 0, (proc.stdout or "")[-1200:]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def normalize_private_key(value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    if clean.startswith("0x"):
        clean = clean[2:]
    if len(clean) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in clean):
        return ""
    return "0x" + clean.lower()

def env_private_key(name: str) -> str:
    return normalize_private_key(os.environ.get(name, ""))

def deterministic_private_key(label: str) -> str:
    import hashlib
    digest = hashlib.sha256(f"{network_key}:{cell_id}:{label}".encode("utf-8")).hexdigest()
    if set(digest) == {"0"}:
        digest = "1" + digest[1:]
    return "0x" + digest

def derive_address(private_key: str) -> str:
    key = normalize_private_key(private_key)
    if not key:
        return ""
    try:
        from eth_account import Account
        return str(Account.from_key(key).address)
    except Exception:
        return ""

def bootstrap_wallets() -> dict:
    hub_admin_key = env_private_key("MC_ALLFATHER_HUB_ADMIN_PRIVATE_KEY")
    deployer_key = env_private_key("MC_ALLFATHER_DEPLOYER_PRIVATE_KEY")
    wallets = {
        "hub_admin": {"private_key_present": bool(hub_admin_key), "address": derive_address(hub_admin_key), "scope": "node"},
        "deployer": {"private_key_present": bool(deployer_key), "address": derive_address(deployer_key), "scope": "network"},
    }
    office_keys = []
    for index in range(4):
        if index == 0 and hub_admin_key:
            office_keys.append(hub_admin_key)
        elif index == 1 and deployer_key:
            office_keys.append(deployer_key)
        else:
            office_keys.append(deterministic_private_key(f"governance-office-{index}"))
    wallets["governance_offices"] = [
        {"index": index, "address": derive_address(key), "runtime_key_source": "hub-admin" if index == 0 and hub_admin_key else "deployer" if index == 1 and deployer_key else "deterministic-node-seed"}
        for index, key in enumerate(office_keys)
    ]
    return wallets

def bootstrap_alloc_addresses() -> list[str]:
    wallets = bootstrap_wallets()
    addresses = []
    for key in ("hub_admin", "deployer"):
        address = str(wallets.get(key, {}).get("address") or "").strip()
        if address:
            addresses.append(address)
    for record in wallets.get("governance_offices") or []:
        address = str(record.get("address") or "").strip() if isinstance(record, dict) else ""
        if address:
            addresses.append(address)
    seen = set()
    unique = []
    for address in addresses:
        clean = address.lower()
        if clean not in seen:
            seen.add(clean)
            unique.append(address)
    return unique

def fund_genesis_accounts(genesis_path: Path) -> None:
    try:
        payload = json.loads(genesis_path.read_text(encoding="utf-8"))
    except Exception:
        return
    alloc = payload.setdefault("alloc", {})
    if not isinstance(alloc, dict):
        alloc = {}
        payload["alloc"] = alloc
    for address in bootstrap_alloc_addresses():
        clean = str(address).strip()
        if clean.startswith("0x"):
            clean = clean[2:]
        if len(clean) == 40:
            alloc.setdefault(clean.lower(), {"balance": "0x3635C9ADC5DEA00000"})
    genesis_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def json_rpc(method: str, params: list | None = None, timeout: float = 3.0):
    rpc_port = int(ports.get("rpc_container") or 8545)
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{rpc_port}",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def local_enode() -> str:
    try:
        payload = json_rpc("admin_nodeInfo", timeout=2.0)
        result = payload.get("result") if isinstance(payload, dict) else {}
        return str(result.get("enode") or "").strip() if isinstance(result, dict) else ""
    except Exception:
        return ""

def advertised_p2p_host() -> str:
    return str(vpn_ip or "127.0.0.1").strip() or "127.0.0.1"

def advertised_p2p_port() -> int:
    for value in (ports.get("p2p_host"), ports.get("p2p_container")):
        try:
            port_value = int(value)
        except Exception:
            continue
        if port_value > 0:
            return port_value
    return 30303

def split_host_port(endpoint: str) -> tuple[str, int | None]:
    clean = str(endpoint or "").strip()
    if not clean:
        return "", None
    if clean.startswith("[") and "]:" in clean:
        host, _, port_text = clean[1:].partition("]:")
        try:
            return host, int(port_text)
        except Exception:
            return host, None
    if ":" not in clean:
        return clean, None
    host, port_text = clean.rsplit(":", 1)
    try:
        return host, int(port_text)
    except Exception:
        return host, None

def rewrite_enode_endpoint(enode: str, host: str | None = None, port: int | None = None) -> str:
    clean = str(enode or "").strip()
    if not clean or "@" not in clean:
        return clean
    target_host = str(host or advertised_p2p_host()).strip()
    try:
        target_port = int(port if port is not None else advertised_p2p_port())
    except Exception:
        target_port = advertised_p2p_port()
    if not target_host or target_port <= 0:
        return clean
    suffix = ""
    base = clean
    if "?" in clean:
        base, suffix = clean.split("?", 1)
        suffix = "?" + suffix
    prefix, _, _endpoint = base.partition("@")
    return f"{prefix}@{target_host}:{target_port}{suffix}"

def normalize_bootnodes_for_inventory_node(bootnodes: list[str], node: dict) -> list[str]:
    endpoint = str(node.get("p2p_endpoint") or "").strip() if isinstance(node, dict) else ""
    host, port = split_host_port(endpoint)
    if not host and isinstance(node, dict):
        host = str(node.get("vpn_ip") or "").strip()
    if port is None and isinstance(node, dict):
        try:
            port = int(node.get("p2p_host_port"))
        except Exception:
            port = None
    normalized = []
    for item in bootnodes:
        clean = str(item or "").strip()
        if not clean:
            continue
        normalized.append(rewrite_enode_endpoint(clean, host or None, port))
    return normalized

def qbft_bootstrap_payload() -> dict:
    genesis = state_root / "qbft" / "config" / "genesis.json"
    bootnodes = []
    enode = local_enode()
    if enode:
        bootnodes.append(rewrite_enode_endpoint(enode))
    genesis_payload = {}
    try:
        genesis_payload = json.loads(genesis.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {
        "ok": bool(genesis_payload),
        "network_key": network_key,
        "cell_id": cell_id,
        "qbft": {
            "genesis": genesis_payload,
            "bootnodes": bootnodes,
            "rpc_port": int(ports.get("rpc_container") or 8545),
            "p2p_port": advertised_p2p_port(),
            "p2p_host": advertised_p2p_host(),
        },
    }

def fetch_shared_qbft_config() -> tuple[bool, str, dict, list[str]]:
    for node in fdb_plan.get("existing_nodes") or []:
        if not isinstance(node, dict):
            continue
        guard_url = str(node.get("guard_url") or "").rstrip("/")
        if not guard_url:
            continue
        try:
            request = urllib.request.Request(f"{guard_url}/qbft/bootstrap", headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=4.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            qbft = payload.get("qbft") if isinstance(payload, dict) else {}
            genesis = qbft.get("genesis") if isinstance(qbft, dict) else {}
            bootnodes = qbft.get("bootnodes") if isinstance(qbft, dict) else []
            if isinstance(genesis, dict) and genesis:
                normalized_bootnodes = normalize_bootnodes_for_inventory_node([str(item) for item in bootnodes if str(item).strip()], node)
                return True, f"shared-qbft-config-from-{node.get('service_name') or guard_url}", genesis, normalized_bootnodes
        except Exception:
            continue
    return False, "blocked-awaiting-shared-qbft-genesis", {}, []

def refresh_existing_joiner_bootnodes(config_dir: Path) -> None:
    if ordinal == 1 or str(fdb_plan.get("action") or "") == "initialize-new-cluster":
        return
    ok, _reason, _shared_genesis, bootnodes = fetch_shared_qbft_config()
    if not ok or not bootnodes:
        return
    (config_dir / "bootnodes.json").write_text(json.dumps({"bootnodes": bootnodes}, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def write_node_key(key_file: Path, *, label: str = "validator") -> None:
    if key_file.exists():
        return
    key_file.write_text(deterministic_private_key(label)[2:] + "\n", encoding="utf-8")

def deployment_state_path(kind: str) -> Path:
    ensure_dirs()
    path = state_root / "bootstrap"
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{kind}.json"

def ensure_hub_admin(validator_ready: bool) -> bool:
    desired = bool(bootstrap.get("hub_admin_requested", True))
    marker = deployment_state_path("hub_admin")
    if not desired:
        state("hub_admin", desired=False, running=False, status="disabled")
        return True
    if not validator_ready:
        state("hub_admin", desired=True, running=False, status="deferred-until-live-validator-rpc", private_key_present=bool(env_private_key("MC_ALLFATHER_HUB_ADMIN_PRIVATE_KEY")))
        return False
    if marker.exists():
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        state("hub_admin", desired=True, running=True, status="bootstrapped", private_key_present=True, address=payload.get("address"), scope="node", completed=True)
        return True
    key = env_private_key("MC_ALLFATHER_HUB_ADMIN_PRIVATE_KEY")
    address = derive_address(key)
    if not key or not address:
        state("hub_admin", desired=True, running=False, status="missing-or-invalid-hub-admin-key", private_key_present=bool(key), scope="node")
        return False
    payload = {
        "schema": "main-computer.allfather.hub-admin-bootstrap.v1",
        "network_key": network_key,
        "cell_id": cell_id,
        "address": address,
        "scope": "node",
        "created_at_uptime_s": now_s(),
        "validator_rpc": f"http://127.0.0.1:{int(ports.get('rpc_container') or 8545)}",
    }
    marker.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    state("hub_admin", desired=True, running=True, status="bootstrapped", private_key_present=True, address=address, scope="node", completed=True)
    return True

REQUIRED_CONTRACT_ARTIFACT_TARGETS = (
    ("AlphaBetaLockout.sol", "AlphaBetaLockout"),
    ("src/XLagBridgeReserve.sol", "XLagBridgeReserve"),
    ("src/HubCreditBridgeEscrow.sol", "HubCreditBridgeEscrow"),
)

def contract_standard_input() -> dict:
    sources = {
        name: {"content": base64.b64decode(encoded).decode("utf-8")}
        for name, encoded in CONTRACT_SOURCES_B64.items()
    }
    return {
        "language": "Solidity",
        "sources": sources,
        "settings": {
            "optimizer": {"enabled": True, "runs": 200},
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
        },
    }

def compiled_contract_artifacts_valid(compiled: object) -> bool:
    if not isinstance(compiled, dict):
        return False
    contracts = compiled.get("contracts")
    if not isinstance(contracts, dict):
        return False
    for source_name, contract_name in REQUIRED_CONTRACT_ARTIFACT_TARGETS:
        contract_data = ((contracts.get(source_name) or {}).get(contract_name) or {})
        abi = contract_data.get("abi")
        bytecode = (((contract_data.get("evm") or {}).get("bytecode") or {}).get("object") or "")
        if not isinstance(abi, list) or not str(bytecode).strip():
            return False
    return True

def load_contract_artifacts_from(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        super_log(f"contracts: prebuilt artifact unreadable path={path} error={type(exc).__name__}: {exc}")
        return None
    if not compiled_contract_artifacts_valid(payload):
        super_log(f"contracts: prebuilt artifact invalid path={path}")
        return None
    return payload

def compile_contracts_with_solc() -> dict:
    standard_input = contract_standard_input()
    try:
        proc = subprocess.run(
            ["solc", "--standard-json"],
            input=json.dumps(standard_input),
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("solc binary is missing from the all-father super-node image") from exc
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "solc --standard-json failed").strip())
    try:
        compiled = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"solc returned invalid JSON: {exc}") from exc
    errors = [
        item
        for item in compiled.get("errors", [])
        if isinstance(item, dict) and item.get("severity") == "error"
    ]
    if errors:
        message = "; ".join(str(item.get("formattedMessage") or item.get("message") or item) for item in errors)
        raise RuntimeError(message)
    if not compiled_contract_artifacts_valid(compiled):
        raise RuntimeError("solc returned incomplete all-father contract artifacts")
    return compiled

def compile_contracts() -> dict:
    artifact_candidates: list[Path] = []
    configured = str(os.environ.get("MC_ALLFATHER_CONTRACT_ARTIFACTS_PATH") or "").strip()
    if configured:
        artifact_candidates.append(Path(configured))
    artifact_candidates.append(Path("/opt/allfather-contracts/contracts-artifacts.json"))
    artifact_candidates.append(deployment_state_path("contracts-artifacts-cache"))
    for artifact_path in artifact_candidates:
        compiled = load_contract_artifacts_from(artifact_path)
        if compiled is not None:
            rate_limited_super_log(
                "contracts:artifact-source",
                f"contracts: using prebuilt artifacts path={artifact_path}",
                interval_s=300.0,
            )
            return compiled
    super_log("contracts: prebuilt artifacts unavailable; compiling contracts with solc fallback")
    compiled = compile_contracts_with_solc()
    cache_path = deployment_state_path("contracts-artifacts-cache")
    try:
        cache_path.write_text(json.dumps(compiled, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception as exc:
        super_log(f"contracts: artifact cache write failed path={cache_path} error={type(exc).__name__}: {exc}")
    return compiled

class PendingContractDeployment(Exception):
    pass

class ContractReceiptPending(Exception):
    pass

def is_contract_receipt_pending_error(exc: Exception) -> bool:
    message = str(exc).lower()
    name = type(exc).__name__.lower()
    return (
        "transactionnotfound" in name
        or "not found" in message
        or "not in the chain" in message
        or "transaction receipt" in message and "not" in message
    )

def read_contract_progress() -> dict:
    path = deployment_state_path("contracts-progress")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("schema", "main-computer.allfather.contract-deployment-progress.v1")
    payload.setdefault("network_key", network_key)
    payload.setdefault("cell_id", cell_id)
    contracts = payload.get("contracts")
    if not isinstance(contracts, dict):
        contracts = {}
        payload["contracts"] = contracts
    return payload

def write_contract_progress(payload: dict) -> None:
    path = deployment_state_path("contracts-progress")
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def contract_gas_limit() -> int:
    try:
        return max(21000, int(os.environ.get("MC_ALLFATHER_CONTRACT_GAS_LIMIT", "8000000")))
    except Exception:
        return 8000000

def contract_max_gas_price_wei() -> int:
    # Besu's JSON-RPC tx fee cap is based on gas_limit * gas_price.  The private
    # all-father bootstrap chain reports eth_gasPrice=0 and allows zero-price
    # transactions, so the default must not invent a non-zero deploy cost for
    # freshly generated zero-balance bootstrap wallets.
    try:
        return max(0, int(os.environ.get("MC_ALLFATHER_MAX_CONTRACT_GAS_PRICE_WEI", "1000000000")))
    except Exception:
        return 1000000000

def cap_contract_gas_price(gas_price: object) -> int:
    try:
        value = max(0, int(gas_price or 0))
    except Exception:
        value = 0
    cap = contract_max_gas_price_wei()
    if cap >= 0:
        value = min(value, cap)
    return max(value, 0)

def contract_gas_price(w3) -> int:
    values = []
    try:
        values.append(int(w3.eth.gas_price or 0))
    except Exception:
        pass
    try:
        latest = w3.eth.get_block("latest")
        if isinstance(latest, dict):
            base_fee = latest.get("baseFeePerGas")
        else:
            base_fee = getattr(latest, "baseFeePerGas", None)
        if base_fee is not None:
            values.append(int(base_fee) + 1)
    except Exception:
        pass
    try:
        values.append(int(os.environ.get("MC_ALLFATHER_MIN_CONTRACT_GAS_PRICE_WEI", "0")))
    except Exception:
        values.append(0)
    return cap_contract_gas_price(max([value for value in values if value >= 0] or [0]))

def bumped_contract_gas_price(w3, previous_gas_price: object = None) -> int:
    gas_price = contract_gas_price(w3)
    try:
        previous = int(previous_gas_price or 0)
    except Exception:
        previous = 0
    # On the private bootstrap chain a zero-price transaction is valid and is the
    # only viable choice for zero-balance bootstrap wallets.  Do not "replace" a
    # zero-price retry by reintroducing the old non-zero gas price from stale
    # progress; that recreates the unmineable pending transaction.
    if gas_price > 0 and previous > 0:
        gas_price = max(gas_price, int(previous * 125 // 100) + 1)
    return cap_contract_gas_price(gas_price)

def contract_deployer_balance(w3, address: str) -> int | None:
    try:
        return int(w3.eth.get_balance(address, "latest"))
    except TypeError:
        try:
            return int(w3.eth.get_balance(address))
        except Exception:
            return None
    except Exception:
        return None

def contract_upfront_cost(gas_limit: object, gas_price: object) -> int | None:
    try:
        gas = max(0, int(gas_limit or 0))
        price = max(0, int(gas_price or 0))
    except Exception:
        return None
    return gas * price

def contract_balance_shortfall(balance: object, upfront_cost: object) -> int | None:
    try:
        return max(0, int(upfront_cost or 0) - int(balance or 0))
    except Exception:
        return None

def contract_transaction_has_unpayable_upfront_cost(tx_lookup: dict) -> bool:
    if not isinstance(tx_lookup, dict) or not tx_lookup.get("observed") or not tx_lookup.get("pending"):
        return False
    gas_price = contract_int(tx_lookup.get("gas_price"))
    if gas_price is None or gas_price <= 0:
        return False
    shortfall = contract_int(tx_lookup.get("balance_shortfall"))
    if shortfall is not None and shortfall > 0:
        return True
    upfront_cost = contract_int(tx_lookup.get("upfront_cost"))
    balance = contract_int(tx_lookup.get("balance"))
    return upfront_cost is not None and upfront_cost > 0 and balance is not None and balance < upfront_cost

def contract_progress_stale_transactions(record: object) -> list:
    if not isinstance(record, dict):
        return []
    items = record.get("stale_transactions")
    return list(items) if isinstance(items, list) else []

def contract_progress_has_unmineable_transaction(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    if str(record.get("last_stale_reason") or "") == "receipt-missing-and-transaction-upfront-cost-exceeds-balance":
        return True
    if str(record.get("status") or "") == "insufficient-balance":
        return True
    candidates = []
    last_tx_lookup = record.get("last_tx_lookup")
    if isinstance(last_tx_lookup, dict):
        candidates.append(last_tx_lookup)
    for stale in contract_progress_stale_transactions(record):
        if isinstance(stale, dict):
            tx_lookup = stale.get("tx_lookup")
            if isinstance(tx_lookup, dict):
                candidates.append(tx_lookup)
            if str(stale.get("reason") or "") == "receipt-missing-and-transaction-upfront-cost-exceeds-balance":
                return True
    return any(contract_transaction_has_unpayable_upfront_cost(candidate) for candidate in candidates)

def contract_progress_recovery_deployer_label(progress_key: str, record: object) -> str:
    if not isinstance(record, dict):
        return ""
    existing = str(record.get("recovery_deployer_label") or "").strip()
    if existing:
        return existing
    if not contract_progress_has_unmineable_transaction(record):
        return ""
    # A lower-fee zero-gas transaction cannot reliably replace an already-known
    # higher-fee nonce-0 tx from the same sender.  Recover by changing sender
    # identity for not-yet-deployed bootstrap contracts; this avoids the poisoned
    # account/nonce while keeping deterministic, non-secret runtime material.
    stale_count = len(contract_progress_stale_transactions(record))
    return f"contract-deployer-recovery:{progress_key}:{stale_count}"

def contract_progress_recovery_reason(record: object) -> str:
    if not isinstance(record, dict):
        return ""
    if str(record.get("last_stale_reason") or "") == "receipt-missing-and-transaction-upfront-cost-exceeds-balance":
        return "unmineable-pending-transaction"
    if str(record.get("status") or "") == "insufficient-balance":
        return "insufficient-deployer-balance"
    if contract_progress_has_unmineable_transaction(record):
        return "unmineable-transaction-history"
    return ""

def transaction_fee_cap_exceeded_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "transaction fee cap exceeded" in message

def contract_stale_pending_s() -> float:
    try:
        return max(1.0, float(os.environ.get("MC_ALLFATHER_CONTRACT_STALE_PENDING_S", "120")))
    except Exception:
        return 120.0

def contract_visible_pending_replace_s() -> float:
    try:
        return max(1.0, float(os.environ.get("MC_ALLFATHER_CONTRACT_VISIBLE_PENDING_REPLACE_S", "120")))
    except Exception:
        return 120.0

def contract_visible_pending_replace_blocks() -> int:
    try:
        return max(1, int(os.environ.get("MC_ALLFATHER_CONTRACT_VISIBLE_PENDING_REPLACE_BLOCKS", "10")))
    except Exception:
        return 10

def contract_dropped_pending_blocks() -> int:
    try:
        return max(1, int(os.environ.get("MC_ALLFATHER_CONTRACT_DROPPED_PENDING_BLOCKS", "5")))
    except Exception:
        return 5

def contract_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            return float(stripped)
        return float(value)
    except Exception:
        return None

def contract_submission_age_s(existing: dict) -> tuple[float | None, str]:
    submitted_unix = contract_float(existing.get("submitted_at_unix_s"))
    if submitted_unix is not None:
        return round(max(0.0, time.time() - submitted_unix), 3), "submitted_at_unix_s"
    submitted_uptime = contract_float(existing.get("submitted_at_uptime_s"))
    if submitted_uptime is not None:
        current_uptime = now_s()
        # Progress files survive container restarts; process uptime does not.  Older
        # records only stored submitted_at_uptime_s, so after a redeploy that value
        # can be in the "future" relative to the new process.  Do not report age=0
        # forever in that case.
        if submitted_uptime <= current_uptime + 1.0:
            return round(max(0.0, current_uptime - submitted_uptime), 3), "submitted_at_uptime_s"
        return None, "invalid-future-submitted-at-uptime"
    return None, "missing-submission-time"

def contract_submitted_block_delta(existing: dict, current_block: object) -> int | None:
    submitted_block = contract_int(existing.get("submitted_at_block_number"))
    current = contract_int(current_block)
    if submitted_block is None or current is None:
        return None
    return max(0, int(current) - int(submitted_block))

def contract_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith(("0x", "0X")):
                return int(stripped, 16)
            return int(stripped)
        return int(value)
    except Exception:
        return None

def install_web3_poa_middleware(w3) -> bool:
    # Besu QBFT blocks have >32-byte extraData.  Web3.py's default block
    # formatter treats that like a pre-merge mainnet block and raises
    # ExtraDataLengthError unless the proof-of-authority middleware is installed.
    # Keep this best-effort and version-tolerant so the supervisor works across
    # web3.py 6.x/7.x package layouts.
    candidates = (
        ("web3.middleware", "geth_poa_middleware"),
        ("web3.middleware", "ExtraDataToPOAMiddleware"),
        ("web3.middleware.geth_poa", "geth_poa_middleware"),
        ("web3.middleware.proof_of_authority", "ExtraDataToPOAMiddleware"),
    )
    for module_name, attr_name in candidates:
        try:
            module = __import__(module_name, fromlist=[attr_name])
            middleware = getattr(module, attr_name)
        except Exception:
            continue
        try:
            w3.middleware_onion.inject(middleware, layer=0)
            return True
        except ValueError:
            return True
        except TypeError:
            try:
                w3.middleware_onion.inject(middleware(), layer=0)
                return True
            except ValueError:
                return True
            except Exception:
                pass
        except Exception:
            try:
                w3.middleware_onion.add(middleware)
                return True
            except Exception:
                try:
                    w3.middleware_onion.add(middleware())
                    return True
                except Exception:
                    pass
    return False

def contract_chain_diagnostics(w3) -> dict:
    info: dict = {"error": ""}
    try:
        info["block_number"] = contract_int(w3.eth.block_number)
    except Exception as exc:
        info["block_number_error"] = f"{type(exc).__name__}: {exc}"
    try:
        gas_price = w3.eth.gas_price
        info["gas_price"] = contract_int(gas_price)
    except Exception as exc:
        info["gas_price_error"] = f"{type(exc).__name__}: {exc}"
    try:
        latest = w3.eth.get_block("latest")
        if isinstance(latest, dict):
            base_fee = latest.get("baseFeePerGas")
        else:
            base_fee = getattr(latest, "baseFeePerGas", None)
        info["latest_base_fee_per_gas"] = contract_int(base_fee)
    except Exception as exc:
        info["latest_block_error"] = f"{type(exc).__name__}: {exc}"
    return info

def transaction_field(tx: object, name: str) -> object:
    if isinstance(tx, dict):
        return tx.get(name)
    return getattr(tx, name, None)

def inspect_contract_transaction(w3, tx_hash: str) -> dict:
    try:
        tx = w3.eth.get_transaction(tx_hash)
    except Exception as exc:
        if is_contract_receipt_pending_error(exc):
            return {"observed": False, "not_found": True, "error": ""}
        return {"observed": False, "not_found": False, "error": f"{type(exc).__name__}: {exc}"}
    if not tx:
        return {"observed": False, "not_found": True, "error": ""}
    block_number = transaction_field(tx, "blockNumber")
    block_hash = transaction_field(tx, "blockHash")
    nonce = transaction_field(tx, "nonce")
    try:
        nonce = int(nonce) if nonce is not None else None
    except Exception:
        pass
    sender = str(transaction_field(tx, "from") or "")
    gas = contract_int(transaction_field(tx, "gas"))
    gas_price = contract_int(transaction_field(tx, "gasPrice"))
    balance = contract_deployer_balance(w3, sender) if sender else None
    upfront_cost = contract_upfront_cost(gas, gas_price)
    return {
        "observed": True,
        "not_found": False,
        "pending": block_number in (None, "", "0x0") and not block_hash,
        "block_number": contract_int(block_number),
        "nonce": nonce,
        "from": sender,
        "gas": gas,
        "gas_price": gas_price,
        "max_fee_per_gas": contract_int(transaction_field(tx, "maxFeePerGas")),
        "max_priority_fee_per_gas": contract_int(transaction_field(tx, "maxPriorityFeePerGas")),
        "balance": balance,
        "upfront_cost": upfront_cost,
        "balance_shortfall": contract_balance_shortfall(balance, upfront_cost),
        "error": "",
    }

def contract_transaction_is_stale(tx_lookup: dict, pending_age_s: object, submitted_block_delta: object = None, age_source: str = "") -> bool:
    if tx_lookup.get("observed") or tx_lookup.get("error") or not tx_lookup.get("not_found"):
        return False
    try:
        age = float(pending_age_s)
    except Exception:
        age = None
    if age is not None and age >= contract_stale_pending_s():
        return True
    try:
        block_delta = int(submitted_block_delta)
    except Exception:
        block_delta = 0
    if block_delta >= contract_dropped_pending_blocks():
        return True
    # A persisted uptime timestamp from a prior container process is unusable after
    # redeploy.  If Besu also no longer knows about the tx, fail fast into
    # same-nonce resubmission rather than waiting until the new process uptime
    # catches the old process uptime.
    return str(age_source or "") == "invalid-future-submitted-at-uptime"

def contract_transaction_is_visible_pending_replaceable(tx_lookup: dict, pending_age_s: object, pending_block_delta: object, age_source: str = "") -> bool:
    if not tx_lookup.get("observed") or not tx_lookup.get("pending") or tx_lookup.get("error"):
        return False
    try:
        block_delta = int(pending_block_delta)
    except Exception:
        block_delta = 0
    if block_delta < contract_visible_pending_replace_blocks():
        return False
    try:
        age = float(pending_age_s)
    except Exception:
        age = None
    if age is not None:
        return age >= contract_visible_pending_replace_s()
    return str(age_source or "") == "invalid-future-submitted-at-uptime"

def mark_contract_transaction_stale(existing: dict, progress_key: str, tx_hash: str, pending_age_s: object, receipt_check_count: int, tx_lookup: dict, *, reason: str, pending_block_delta: object = None, submitted_block_delta: object = None, age_source: str = "", chain_info: dict | None = None) -> None:
    stale_transactions = existing.setdefault("stale_transactions", [])
    if not isinstance(stale_transactions, list):
        stale_transactions = []
        existing["stale_transactions"] = stale_transactions
    stale_record = {
        "transaction_hash": tx_hash,
        "stale_at_uptime_s": now_s(),
        "pending_age_s": pending_age_s,
        "receipt_check_count": receipt_check_count,
        "reason": reason,
        "tx_lookup": tx_lookup,
    }
    if pending_block_delta is not None:
        stale_record["pending_block_delta"] = pending_block_delta
    if submitted_block_delta is not None:
        stale_record["submitted_block_delta"] = submitted_block_delta
    if age_source:
        stale_record["pending_age_source"] = age_source
    if chain_info:
        stale_record["chain"] = chain_info
    stale_transactions.append(stale_record)
    existing["last_stale_transaction_hash"] = tx_hash
    existing["last_stale_pending_age_s"] = pending_age_s
    existing["last_stale_reason"] = reason
    if submitted_block_delta is not None:
        existing["last_stale_submitted_block_delta"] = submitted_block_delta
    if age_source:
        existing["last_stale_pending_age_source"] = age_source
    existing["status"] = "stale-resubmit-required"
    nonce = tx_lookup.get("nonce")
    if nonce is None:
        nonce = existing.get("nonce")
    if nonce is not None:
        existing["replacement_nonce"] = nonce
    existing.pop("transaction_hash", None)
    existing.pop("pending_transaction_hash", None)
    if reason == "receipt-missing-and-transaction-visible-pending-too-long":
        super_log(f"contracts: visible pending transaction stuck contract={progress_key} tx={tx_hash} pending_age_s={pending_age_s} pending_block_delta={pending_block_delta}; replacing nonce={nonce}")
    elif reason == "receipt-missing-and-transaction-upfront-cost-exceeds-balance":
        super_log(f"contracts: pending transaction cannot be mined contract={progress_key} tx={tx_hash} balance={tx_lookup.get('balance')} upfront_cost={tx_lookup.get('upfront_cost')} shortfall={tx_lookup.get('balance_shortfall')}; resubmitting nonce={nonce}")
    else:
        super_log(f"contracts: stale pending transaction contract={progress_key} tx={tx_hash} pending_age_s={pending_age_s}; resubmitting")

def signed_transaction_raw_and_hash(w3, account, tx: dict) -> tuple[bytes, str]:
    signed = account.sign_transaction(tx)
    raw = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction")
    tx_hash = getattr(signed, "hash", None)
    if not tx_hash:
        tx_hash = w3.keccak(raw)
    return raw, w3.to_hex(tx_hash)

def receipt_contract_address(receipt) -> str:
    if isinstance(receipt, dict):
        value = receipt.get("contractAddress")
    else:
        value = getattr(receipt, "contractAddress", None)
    return str(value or "")

def receipt_status_value(receipt) -> object:
    if isinstance(receipt, dict):
        return receipt.get("status")
    return getattr(receipt, "status", None)

def wait_for_contract_deployment_receipt(w3, tx_hash: str, contract_name: str, timeout_s: int) -> dict:
    # Keep the supervisor loop responsive.  A long blocking receipt wait
    # freezes hub/guard status updates and makes add-node look hung while contracts
    # are merely waiting to be mined.  Poll once per supervisor tick instead.
    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
    except Exception as exc:
        if is_contract_receipt_pending_error(exc):
            raise ContractReceiptPending(f"{contract_name} transaction receipt not available yet for {tx_hash}") from exc
        raise
    if not receipt:
        raise ContractReceiptPending(f"{contract_name} transaction receipt not available yet for {tx_hash}")
    status = receipt_status_value(receipt)
    if status is not None:
        try:
            failed = int(status) == 0
        except Exception:
            failed = str(status).lower() in {"0x0", "false", "failed"}
        if failed:
            raise RuntimeError(f"{contract_name} deployment transaction failed for {tx_hash}")
    address = receipt_contract_address(receipt)
    if not address:
        raise RuntimeError(f"{contract_name} deployment did not return a contract address")
    return {"address": address, "transaction_hash": w3.to_hex(tx_hash), "target": contract_name}

def deploy_contract(w3, account, compiled: dict, source_name: str, contract_name: str, args: list, progress: dict, progress_key: str) -> dict:
    contracts_progress = progress.setdefault("contracts", {})
    existing = contracts_progress.get(progress_key)
    if isinstance(existing, dict):
        if existing.get("address"):
            return dict(existing)
        if existing.get("transaction_hash"):
            tx_hash = str(existing.get("transaction_hash"))
            try:
                receipt_check_count = int(existing.get("receipt_check_count") or 0) + 1
            except Exception:
                receipt_check_count = 1
            existing["receipt_check_count"] = receipt_check_count
            existing["last_receipt_check_uptime_s"] = now_s()
            pending_age_s, pending_age_source = contract_submission_age_s(existing)
            tx_lookup = inspect_contract_transaction(w3, tx_hash)
            chain_info = contract_chain_diagnostics(w3)
            current_block = chain_info.get("block_number")
            submitted_block_delta = contract_submitted_block_delta(existing, current_block)
            pending_block_delta = None
            if tx_lookup.get("observed") and tx_lookup.get("pending"):
                if current_block is not None and existing.get("first_pending_block_number") is None:
                    existing["first_pending_block_number"] = current_block
                if current_block is not None:
                    existing["last_pending_block_number"] = current_block
                first_pending_block = contract_int(existing.get("first_pending_block_number"))
                if current_block is not None and first_pending_block is not None:
                    pending_block_delta = max(0, int(current_block) - int(first_pending_block))
                    existing["pending_block_delta"] = pending_block_delta
            existing["last_tx_lookup"] = tx_lookup
            existing["last_chain_diagnostics"] = chain_info
            existing["pending_age_source"] = pending_age_source
            if submitted_block_delta is not None:
                existing["submitted_block_delta"] = submitted_block_delta
            write_contract_progress(progress)
            if contract_transaction_is_stale(tx_lookup, pending_age_s, submitted_block_delta, pending_age_source):
                mark_contract_transaction_stale(
                    existing,
                    progress_key,
                    tx_hash,
                    pending_age_s,
                    receipt_check_count,
                    tx_lookup,
                    reason="receipt-missing-and-transaction-not-observed",
                    pending_block_delta=pending_block_delta,
                    submitted_block_delta=submitted_block_delta,
                    age_source=pending_age_source,
                    chain_info=chain_info,
                )
                write_contract_progress(progress)
            elif contract_transaction_has_unpayable_upfront_cost(tx_lookup):
                mark_contract_transaction_stale(
                    existing,
                    progress_key,
                    tx_hash,
                    pending_age_s,
                    receipt_check_count,
                    tx_lookup,
                    reason="receipt-missing-and-transaction-upfront-cost-exceeds-balance",
                    pending_block_delta=pending_block_delta,
                    submitted_block_delta=submitted_block_delta,
                    age_source=pending_age_source,
                    chain_info=chain_info,
                )
                write_contract_progress(progress)
            elif contract_transaction_is_visible_pending_replaceable(tx_lookup, pending_age_s, pending_block_delta, pending_age_source):
                mark_contract_transaction_stale(
                    existing,
                    progress_key,
                    tx_hash,
                    pending_age_s,
                    receipt_check_count,
                    tx_lookup,
                    reason="receipt-missing-and-transaction-visible-pending-too-long",
                    pending_block_delta=pending_block_delta,
                    submitted_block_delta=submitted_block_delta,
                    age_source=pending_age_source,
                    chain_info=chain_info,
                )
                write_contract_progress(progress)
            else:
                rate_limited_super_log(
                    f"contracts:{progress_key}:pending",
                    f"contracts: waiting receipt contract={progress_key} tx={tx_hash} checks={receipt_check_count} pending_age_s={pending_age_s} age_source={pending_age_source} tx_observed={str(bool(tx_lookup.get('observed'))).lower()} tx_pending={str(bool(tx_lookup.get('pending'))).lower()} submitted_block_delta={submitted_block_delta} pending_block_delta={pending_block_delta} block={chain_info.get('block_number')} gasPrice={tx_lookup.get('gas_price')} chainGasPrice={chain_info.get('gas_price')} baseFee={chain_info.get('latest_base_fee_per_gas')} deployerBalance={tx_lookup.get('balance')} upfrontCost={tx_lookup.get('upfront_cost')} balanceShortfall={tx_lookup.get('balance_shortfall')}",
                    interval_s=float(os.environ.get("MC_ALLFATHER_CONTRACT_LOG_INTERVAL_S", "30")),
                )
                state(
                    "contracts",
                    desired=True,
                    running=False,
                    status="waiting-contract-receipt",
                    pending_contract=progress_key,
                    pending_transaction_hash=tx_hash,
                    pending_age_s=pending_age_s,
                    pending_age_source=pending_age_source,
                    submitted_block_delta=submitted_block_delta,
                    receipt_check_count=receipt_check_count,
                    tx_observed=bool(tx_lookup.get("observed")),
                    tx_pending=bool(tx_lookup.get("pending")),
                    tx_lookup_error=str(tx_lookup.get("error") or ""),
                    tx_gas=tx_lookup.get("gas"),
                    tx_gas_price=tx_lookup.get("gas_price"),
                    tx_max_fee_per_gas=tx_lookup.get("max_fee_per_gas"),
                    tx_max_priority_fee_per_gas=tx_lookup.get("max_priority_fee_per_gas"),
                    deployer_balance=tx_lookup.get("balance"),
                    tx_upfront_cost=tx_lookup.get("upfront_cost"),
                    tx_balance_shortfall=tx_lookup.get("balance_shortfall"),
                    chain_block_number=chain_info.get("block_number"),
                    chain_gas_price=chain_info.get("gas_price"),
                    latest_base_fee_per_gas=chain_info.get("latest_base_fee_per_gas"),
                    pending_block_delta=pending_block_delta,
                    visible_pending_replace_s=contract_visible_pending_replace_s(),
                    visible_pending_replace_blocks=contract_visible_pending_replace_blocks(),
                    dropped_pending_blocks=contract_dropped_pending_blocks(),
                    stale_pending_s=contract_stale_pending_s(),
                    deployed_contract_count=len([item for item in contracts_progress.values() if isinstance(item, dict) and item.get("address")]),
                )
                try:
                    deployed = wait_for_contract_deployment_receipt(w3, tx_hash, contract_name, int(os.environ.get("MC_ALLFATHER_CONTRACT_RECEIPT_TIMEOUT_S", "300")))
                except ContractReceiptPending as exc:
                    state(
                        "contracts",
                        desired=True,
                        running=False,
                        status="deployment-pending",
                        pending_contract=progress_key,
                        pending_transaction_hash=tx_hash,
                        pending_age_s=pending_age_s,
                        pending_age_source=pending_age_source,
                        submitted_block_delta=submitted_block_delta,
                        receipt_check_count=receipt_check_count,
                        tx_observed=bool(tx_lookup.get("observed")),
                        tx_pending=bool(tx_lookup.get("pending")),
                        tx_lookup_error=str(tx_lookup.get("error") or ""),
                        tx_gas_price=tx_lookup.get("gas_price"),
                        tx_max_fee_per_gas=tx_lookup.get("max_fee_per_gas"),
                        tx_max_priority_fee_per_gas=tx_lookup.get("max_priority_fee_per_gas"),
                        chain_block_number=chain_info.get("block_number"),
                        chain_gas_price=chain_info.get("gas_price"),
                        latest_base_fee_per_gas=chain_info.get("latest_base_fee_per_gas"),
                        pending_block_delta=pending_block_delta,
                        visible_pending_replace_s=contract_visible_pending_replace_s(),
                        visible_pending_replace_blocks=contract_visible_pending_replace_blocks(),
                        dropped_pending_blocks=contract_dropped_pending_blocks(),
                        stale_pending_s=contract_stale_pending_s(),
                        last_error=f"{type(exc).__name__}: {exc}",
                    )
                    raise PendingContractDeployment(f"{progress_key} transaction is still pending: {tx_hash}") from exc
                deployed["target"] = f"{source_name}:{contract_name}"
                contracts_progress[progress_key] = deployed
                write_contract_progress(progress)
                super_log(f"contracts: deployed contract={progress_key} address={deployed.get('address')} tx={tx_hash}")
                return deployed

    contract_data = compiled["contracts"][source_name][contract_name]
    abi = contract_data["abi"]
    bytecode = "0x" + contract_data["evm"]["bytecode"]["object"]
    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    previous_record = contracts_progress.get(progress_key)
    if isinstance(previous_record, dict):
        stale_transactions = list(previous_record.get("stale_transactions") or [])
        previous_gas_price = previous_record.get("gasPrice")
        try:
            attempt = int(previous_record.get("attempt") or 1) + 1
        except Exception:
            attempt = len(stale_transactions) + 1
    else:
        stale_transactions = []
        previous_gas_price = None
        attempt = 1
    recovery_deployer_label = contract_progress_recovery_deployer_label(progress_key, previous_record)
    recovery_deployer_reason = contract_progress_recovery_reason(previous_record)
    deployer_account = account
    deployer_key_source = "configured-deployer"
    if recovery_deployer_label:
        try:
            from eth_account import Account as EthAccount
            deployer_account = EthAccount.from_key(deterministic_private_key(recovery_deployer_label))
            deployer_key_source = "unmineable-tx-recovery"
            previous_gas_price = None
            if not isinstance(previous_record, dict) or previous_record.get("recovery_deployer_label") != recovery_deployer_label:
                super_log(f"contracts: rotating deployer contract={progress_key} old_deployer={account.address} new_deployer={deployer_account.address} reason={recovery_deployer_reason or 'unmineable-transaction'} label={recovery_deployer_label}")
        except Exception as exc:
            super_log(f"contracts: failed to rotate deployer contract={progress_key} label={recovery_deployer_label} error={type(exc).__name__}: {exc}")
            deployer_account = account
            deployer_key_source = "configured-deployer"
            recovery_deployer_label = ""
            recovery_deployer_reason = ""
    gas_price = bumped_contract_gas_price(w3, previous_gas_price)
    if deployer_key_source == "unmineable-tx-recovery":
        gas_price = contract_gas_price(w3)
    gas_limit = contract_gas_limit()
    deployer_balance = contract_deployer_balance(w3, deployer_account.address)
    upfront_cost = contract_upfront_cost(gas_limit, gas_price)
    balance_shortfall = contract_balance_shortfall(deployer_balance, upfront_cost)
    if gas_price > 0 and balance_shortfall is not None and balance_shortfall > 0:
        contracts_progress[progress_key] = {
            "target": f"{source_name}:{contract_name}",
            "status": "insufficient-balance",
            "deployer": deployer_account.address,
            "configured_deployer": account.address,
            "deployer_key_source": deployer_key_source,
            "recovery_deployer_label": recovery_deployer_label,
            "recovery_deployer_reason": recovery_deployer_reason,
            "deployer_balance": deployer_balance,
            "gas": gas_limit,
            "gasPrice": gas_price,
            "upfront_cost": upfront_cost,
            "balance_shortfall": balance_shortfall,
            "attempt": attempt,
            "stale_transactions": stale_transactions,
            "updated_at_unix_s": time.time(),
        }
        write_contract_progress(progress)
        super_log(f"contracts: deployer balance insufficient contract={progress_key} deployer={deployer_account.address} balance={deployer_balance} upfront_cost={upfront_cost} shortfall={balance_shortfall} gas={gas_limit} gasPrice={gas_price}; retry with zero gas price or fund deployer")
        raise RuntimeError(f"{progress_key} deployer balance {deployer_balance} is below upfront cost {upfront_cost} at gasPrice {gas_price}")
    submission_chain_info = contract_chain_diagnostics(w3)
    submitted_at_block_number = submission_chain_info.get("block_number")
    replacement_nonce = None
    if isinstance(previous_record, dict):
        replacement_nonce = contract_int(previous_record.get("replacement_nonce"))
    if replacement_nonce is not None:
        nonce = replacement_nonce
    else:
        try:
            nonce = w3.eth.get_transaction_count(deployer_account.address, "pending")
        except TypeError:
            nonce = w3.eth.get_transaction_count(deployer_account.address)
    tx = contract.constructor(*args).build_transaction(
        {
            "from": deployer_account.address,
            "nonce": nonce,
            "gas": gas_limit,
            "gasPrice": gas_price,
            "chainId": int(w3.eth.chain_id),
        }
    )
    raw, tx_hash = signed_transaction_raw_and_hash(w3, deployer_account, tx)
    contracts_progress[progress_key] = {
        "target": f"{source_name}:{contract_name}",
        "transaction_hash": tx_hash,
        "nonce": nonce,
        "gasPrice": gas_price,
        "max_gas_price_wei": contract_max_gas_price_wei(),
        "gas": gas_limit,
        "deployer": deployer_account.address,
        "configured_deployer": account.address,
        "deployer_key_source": deployer_key_source,
        "recovery_deployer_label": recovery_deployer_label,
        "recovery_deployer_reason": recovery_deployer_reason,
        "deployer_balance": deployer_balance,
        "upfront_cost": upfront_cost,
        "balance_shortfall": balance_shortfall,
        "status": "submitted",
        "submitted_at_uptime_s": now_s(),
        "submitted_at_unix_s": time.time(),
        "submitted_at_block_number": submitted_at_block_number,
        "attempt": attempt,
        "replaces_nonce": replacement_nonce,
        "stale_transactions": stale_transactions,
    }
    write_contract_progress(progress)
    super_log(f"contracts: submitted contract={progress_key} tx={tx_hash} nonce={nonce} gasPrice={gas_price} deployer={deployer_account.address} deployerKeySource={deployer_key_source}")
    try:
        sent_hash = w3.eth.send_raw_transaction(raw)
        tx_hash = w3.to_hex(sent_hash)
        contracts_progress[progress_key]["transaction_hash"] = tx_hash
        write_contract_progress(progress)
        super_log(f"contracts: accepted contract={progress_key} tx={tx_hash}")
    except ValueError as exc:
        message = str(exc)
        if transaction_fee_cap_exceeded_error(exc):
            contracts_progress[progress_key]["status"] = "fee-cap-exceeded"
            contracts_progress[progress_key]["last_error"] = f"{type(exc).__name__}: {exc}"
            contracts_progress[progress_key]["gasPrice"] = contract_gas_price(w3)
            contracts_progress[progress_key].pop("transaction_hash", None)
            contracts_progress[progress_key].pop("pending_transaction_hash", None)
            write_contract_progress(progress)
            super_log(f"contracts: transaction fee cap exceeded contract={progress_key} attempted_gasPrice={gas_price} maxGasPrice={contract_max_gas_price_wei()}; retrying with capped gas price")
            raise PendingContractDeployment(f"{progress_key} transaction fee cap exceeded; retrying with capped gas price") from exc
        if "Known transaction" not in message and "already known" not in message:
            raise
        super_log(f"contracts: transaction already known contract={progress_key} tx={tx_hash}")
    state(
        "contracts",
        desired=True,
        running=False,
        status="waiting-contract-receipt",
        pending_contract=progress_key,
        pending_transaction_hash=tx_hash,
        pending_age_s=0.0,
        pending_age_source="submitted_at_unix_s",
        submitted_block_delta=0,
        submitted_at_block_number=submitted_at_block_number,
        receipt_check_count=1,
        deployed_contract_count=len([item for item in contracts_progress.values() if isinstance(item, dict) and item.get("address")]),
    )
    try:
        deployed = wait_for_contract_deployment_receipt(w3, tx_hash, contract_name, int(os.environ.get("MC_ALLFATHER_CONTRACT_RECEIPT_TIMEOUT_S", "300")))
    except ContractReceiptPending as exc:
        state(
            "contracts",
            desired=True,
            running=False,
            status="deployment-pending",
            pending_contract=progress_key,
            pending_transaction_hash=tx_hash,
            pending_age_s=0.0,
            pending_age_source="submitted_at_unix_s",
            submitted_block_delta=0,
            submitted_at_block_number=submitted_at_block_number,
            receipt_check_count=1,
            last_error=f"{type(exc).__name__}: {exc}",
        )
        rate_limited_super_log(
            f"contracts:{progress_key}:submitted-pending",
            f"contracts: receipt pending contract={progress_key} tx={tx_hash}",
            interval_s=float(os.environ.get("MC_ALLFATHER_CONTRACT_LOG_INTERVAL_S", "30")),
        )
        raise PendingContractDeployment(f"{progress_key} transaction is still pending: {tx_hash}") from exc
    deployed["target"] = f"{source_name}:{contract_name}"
    contracts_progress[progress_key] = deployed
    write_contract_progress(progress)
    super_log(f"contracts: deployed contract={progress_key} address={deployed.get('address')} tx={tx_hash}")
    return deployed

def ensure_contracts(validator_ready: bool, hub_admin_ready: bool) -> bool:
    desired = bool(bootstrap.get("contracts_requested"))
    marker = deployment_state_path("contracts")
    if not desired:
        state("contracts", desired=False, running=False, status="not-required-existing-network" if ordinal != 1 else "disabled")
        return True
    if not validator_ready:
        state("contracts", desired=True, running=False, status="deferred-until-live-validator-rpc")
        return False
    if not hub_admin_ready:
        state("contracts", desired=True, running=False, status="deferred-until-hub-admin")
        return False
    if marker.exists():
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        state("contracts", desired=True, running=True, status="deployed", deployment=payload, contract_count=len(payload.get("contracts") or {}), completed=True)
        return True
    deployer_key = env_private_key("MC_ALLFATHER_DEPLOYER_PRIVATE_KEY")
    if not deployer_key:
        state("contracts", desired=True, running=False, status="missing-deployer-private-key")
        return False
    try:
        from eth_account import Account
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{int(ports.get('rpc_container') or 8545)}", request_kwargs={"timeout": 10}))
        install_web3_poa_middleware(w3)
        if not w3.is_connected():
            state("contracts", desired=True, running=False, status="waiting-validator-rpc-json-rpc")
            return False
        account = Account.from_key(deployer_key)
        wallets = bootstrap_wallets()
        hub_admin_address = str(wallets.get("hub_admin", {}).get("address") or account.address)
        offices = [str(item.get("address")) for item in wallets.get("governance_offices") or [] if isinstance(item, dict) and str(item.get("address") or "").strip()]
        if len(set(offices)) < 4:
            offices = [account.address, hub_admin_address, derive_address(deterministic_private_key("governance-office-2")), derive_address(deterministic_private_key("governance-office-3"))]
        compiled = compile_contracts()
        progress = read_contract_progress()
        deployments = {}
        for key, source_name, contract_name, constructor_args in [
            ("alpha-beta-lockout", "AlphaBetaLockout.sol", "AlphaBetaLockout", [offices[:4]]),
            ("xlag-bridge-reserve", "src/XLagBridgeReserve.sol", "XLagBridgeReserve", [offices[:4], int(os.environ.get("MC_ALLFATHER_MAX_PAYOUT_WEI", "1000000000000000000")), 1, 1]),
            ("hub_credit_bridge_escrow", "src/HubCreditBridgeEscrow.sol", "HubCreditBridgeEscrow", [hub_admin_address]),
        ]:
            deployments[key] = deploy_contract(w3, account, compiled, source_name, contract_name, constructor_args, progress, key)
        payload = {
            "schema": "main-computer.allfather.contract-deployment.v1",
            "network_key": network_key,
            "cell_id": cell_id,
            "chain": {"chain_id": int(w3.eth.chain_id), "rpc_url": f"http://127.0.0.1:{int(ports.get('rpc_container') or 8545)}"},
            "hub_admin": {"address": hub_admin_address, "scope": "node"},
            "offices": offices[:4],
            "contracts": deployments,
            "created_at_uptime_s": now_s(),
        }
        marker.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            deployment_state_path("contracts-progress").unlink()
        except FileNotFoundError:
            pass
        state("contracts", desired=True, running=True, status="deployed", deployment=payload, contract_count=len(deployments), completed=True)
        return True
    except PendingContractDeployment:
        return False
    except Exception as exc:
        state("contracts", desired=True, running=False, status="deployment-failed", last_error=f"{type(exc).__name__}: {exc}")
        return False


def ensure_fdb() -> bool:
    fdb_port = int(ports.get("fdb_container") or 4550)
    cluster_file = write_fdb_cluster_file()
    if not command_exists("fdbserver"):
        state("foundationdb", name=components.get("fdb"), desired=True, running=False, status="missing-fdbserver", port=fdb_port, cluster_file_present=cluster_file.exists())
        return False
    public_address = str((fdb_plan.get("new_node") or {}).get("fdb_endpoint") or f"{vpn_ip or '127.0.0.1'}:{ports.get('fdb_host') or fdb_port}")
    command = [
        "fdbserver",
        "--cluster-file", str(cluster_file),
        "--datadir", str(state_root / "foundationdb" / "data"),
        "--logdir", str(state_root / "foundationdb" / "logs"),
        "--listen-address", f"0.0.0.0:{fdb_port}",
        "--public-address", public_address,
        "--class", "storage",
        "--locality-machineid", cell_id or "allfather-super",
        "--locality-zoneid", str(manifest.get("coolify_server") or manifest.get("host_slot") or "zone-a"),
    ]
    start_child("foundationdb", command)
    running = child_running("foundationdb")
    listening = port_open("127.0.0.1", fdb_port)
    configured = False
    configure_output = ""
    if running and str(fdb_plan.get("action") or "") == "initialize-new-cluster" and command_exists("fdbcli"):
        marker = state_root / "foundationdb" / ".configured"
        if marker.exists():
            configured = True
        else:
            ok, configure_output = run_once("fdb-configure", ["fdbcli", "-C", str(cluster_file), "--exec", "configure new single ssd"], timeout=25)
            if ok or "already" in configure_output.lower() or "database already exists" in configure_output.lower():
                marker.write_text(str(time.time()) + "\n", encoding="utf-8")
                configured = True
    elif running and str(fdb_plan.get("action") or "") != "initialize-new-cluster":
        configured = True
    status = "running" if running and (listening or configured) else "starting"
    if running and str(fdb_plan.get("action") or "") == "initialize-new-cluster" and not configured:
        status = "running-unconfigured"
    state(
        "foundationdb",
        name=components.get("fdb"),
        desired=True,
        running=running,
        status=status,
        port=fdb_port,
        bootstrap_action=fdb_plan.get("action"),
        cluster_file_present=cluster_file.exists(),
        configured=configured,
        listening=listening,
        public_address=public_address,
        last_exit_code=child_exit("foundationdb"),
        last_configure_output=configure_output,
    )
    return running and (configured or listening)

def write_qbft_config() -> tuple[bool, str, Path | None, Path | None]:
    ensure_dirs()
    config_dir = state_root / "qbft" / "config"
    genesis = config_dir / "genesis.json"
    key_file = config_dir / "key"
    if genesis.exists() and key_file.exists():
        refresh_existing_joiner_bootnodes(config_dir)
        return True, "existing-qbft-config", genesis, key_file
    if ordinal != 1 and str(fdb_plan.get("action") or "") != "initialize-new-cluster":
        ok, reason, shared_genesis, bootnodes = fetch_shared_qbft_config()
        if not ok:
            return False, reason, None, None
        genesis.write_text(json.dumps(shared_genesis, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_node_key(key_file, label="validator-rpc-joiner")
        (config_dir / "bootnodes.json").write_text(json.dumps({"bootnodes": bootnodes}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return True, reason, genesis, key_file
    if not command_exists("besu"):
        return False, "missing-besu", None, None

    chain_id = 20260000 + (1 if network_key == "mainnet" else 2)
    operator_config = {
        "genesis": {
            "config": {
                "chainId": chain_id,
                "berlinBlock": 0,
                "qbft": {
                    "blockperiodseconds": 2,
                    "emptyblockperiodseconds": 0,
                    "epochlength": 30000,
                    "requesttimeoutseconds": 4,
                },
            },
            "nonce": "0x0",
            "timestamp": "0x58ee40ba",
            "gasLimit": "0x1fffffffffffff",
            "difficulty": "0x1",
            "mixHash": "0x63746963616c2062797a616e74696e65206661756c7420746f6c6572616e6365",
            "coinbase": "0x0000000000000000000000000000000000000000",
            "alloc": {},
        },
        "blockchain": {"nodes": {"generate": True, "count": 1}},
    }
    operator_file = config_dir / "qbft-config.json"
    operator_file.write_text(json.dumps(operator_config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_dir = config_dir / "operator-output"
    out_dir.mkdir(parents=True, exist_ok=True)
    ok, output = run_once(
        "besu-qbft-generate",
        [
            "besu",
            "operator",
            "generate-blockchain-config",
            "--config-file", str(operator_file),
            "--to", str(out_dir),
            "--private-key-file-name", "key",
        ],
        timeout=60,
    )
    if not ok:
        return False, "qbft-config-generation-failed: " + output, None, None

    generated_genesis = out_dir / "genesis.json"
    generated_keys = sorted(out_dir.glob("keys/*/key"))
    if not generated_genesis.exists() or not generated_keys:
        return False, "qbft-config-generation-missing-output", None, None
    genesis.write_text(generated_genesis.read_text(encoding="utf-8"), encoding="utf-8")
    fund_genesis_accounts(genesis)
    key_file.write_text(generated_keys[0].read_text(encoding="utf-8").strip() + "\n", encoding="utf-8")
    return True, "generated-single-validator-qbft-config", genesis, key_file

def ensure_validator_rpc(fdb_ready: bool) -> bool:
    rpc_port = int(ports.get("rpc_container") or 8545)
    p2p_container_port = int(ports.get("p2p_container") or 30303)
    p2p_port = advertised_p2p_port()
    if not fdb_ready:
        state("validator_rpc", name=components.get("validator_rpc"), desired=True, running=False, status="waiting-foundationdb", rpc_port=rpc_port, p2p_port=p2p_port, besu_binary_present=command_exists("besu"))
        return False
    ok, reason, genesis, key_file = write_qbft_config()
    if not ok or genesis is None or key_file is None:
        state("validator_rpc", name=components.get("validator_rpc"), desired=True, running=False, status=reason, rpc_port=rpc_port, p2p_port=p2p_port, besu_binary_present=command_exists("besu"))
        return False
    data_path = state_root / "qbft" / "data"
    data_path.mkdir(parents=True, exist_ok=True)
    p2p_host = advertised_p2p_host()
    bootnodes = []
    bootnodes_file = state_root / "qbft" / "config" / "bootnodes.json"
    if bootnodes_file.exists():
        try:
            bootnodes_payload = json.loads(bootnodes_file.read_text(encoding="utf-8"))
            bootnodes = [str(item).strip() for item in bootnodes_payload.get("bootnodes") or [] if str(item).strip()]
        except Exception:
            bootnodes = []
    command = [
        "besu",
        "--data-path", str(data_path),
        "--genesis-file", str(genesis),
        "--node-private-key-file", str(key_file),
        "--rpc-http-enabled=true",
        "--rpc-http-host=0.0.0.0",
        f"--rpc-http-port={rpc_port}",
        "--rpc-http-api=ETH,NET,WEB3,QBFT,ADMIN,TXPOOL",
        "--rpc-http-cors-origins=*",
        "--host-allowlist=*",
        f"--p2p-port={p2p_port}",
        f"--p2p-host={p2p_host}",
        "--sync-min-peers=0",
        "--min-gas-price=0",
        "--logging=INFO",
    ]
    if bootnodes:
        command.append("--bootnodes=" + ",".join(bootnodes))
    start_child("validator_rpc", command)
    running = child_running("validator_rpc")
    rpc_url = f"http://127.0.0.1:{rpc_port}"
    port_listening = port_open("127.0.0.1", rpc_port)
    json_rpc_ok, block_number, block_error = rpc_block_number(rpc_url, timeout=1.0)
    log_tail = "" if json_rpc_ok else tail_log("validator_rpc")
    log_block_number = latest_besu_log_block_number(log_tail)
    log_block_production_ok = bool(log_block_number is not None and int(log_block_number) > 0)
    shutdown_observed = "Shutting down BFT event processor" in log_tail or "BesuCommand-Shutdown-Hook" in log_tail
    observed_block_number = block_number if block_number is not None else log_block_number
    block_production_ok = bool(json_rpc_ok and block_number is not None and int(block_number) > 0)
    if running and block_production_ok:
        status = "running"
    elif running and log_block_production_ok and not json_rpc_ok:
        status = "waiting-validator-json-rpc-after-block-production"
    elif running and json_rpc_ok:
        status = "waiting-qbft-block-production"
    elif running and port_listening:
        status = "waiting-validator-json-rpc"
    elif running:
        status = "starting"
    else:
        status = "stopped"
    state(
        "validator_rpc",
        name=components.get("validator_rpc"),
        desired=True,
        running=running,
        status=status,
        rpc_port=rpc_port,
        p2p_port=p2p_port,
        p2p_host=p2p_host,
        p2p_container_port=p2p_container_port,
        besu_binary_present=command_exists("besu"),
        qbft_config=reason,
        genesis_file_present=genesis.exists(),
        node_key_present=key_file.exists(),
        rpc_http_ok=json_rpc_ok,
        json_rpc_ok=json_rpc_ok,
        rpc_port_listening=port_listening,
        block_number=observed_block_number,
        rpc_block_number=block_number,
        log_block_number=log_block_number,
        block_production_ok=block_production_ok,
        log_block_production_ok=log_block_production_ok,
        block_production_required=True,
        block_production_error=block_error,
        shutdown_observed=shutdown_observed,
        bootnode_count=len(bootnodes),
        last_exit_code=child_exit("validator_rpc"),
        child_uptime_s=child_uptime_s("validator_rpc"),
        log_tail="" if block_production_ok else log_tail,
    )
    return running and block_production_ok

def write_bootstrap_hub_script() -> Path:
    ensure_dirs()
    script = state_root / "hub" / "allfather-bootstrap-hub.py"
    script.write_bytes(base64.b64decode("aW1wb3J0IGpzb24KaW1wb3J0IG9zCmZyb20gaHR0cC5zZXJ2ZXIgaW1wb3J0IEJhc2VIVFRQUmVxdWVzdEhhbmRsZXIsIFRocmVhZGluZ0hUVFBTZXJ2ZXIKZnJvbSB1cmxsaWIucGFyc2UgaW1wb3J0IHVybHBhcnNlCnBvcnQgPSBpbnQob3MuZW52aXJvbi5nZXQoIk1DX0FMTEZBVEhFUl9IVUJfUE9SVCIsICI4Nzg1IikpCmNlbGxfaWQgPSBvcy5lbnZpcm9uLmdldCgiTUNfQUxMRkFUSEVSX0NFTExfSUQiLCAiIikKbmV0d29yayA9IG9zLmVudmlyb24uZ2V0KCJNQ19BTExGQVRIRVJfTkVUV09SSyIsICIiKQpjbGFzcyBIYW5kbGVyKEJhc2VIVFRQUmVxdWVzdEhhbmRsZXIpOgogICAgZGVmIF9zZW5kKHNlbGYsIHN0YXR1cywgcGF5bG9hZCk6CiAgICAgICAgcmF3ID0ganNvbi5kdW1wcyhwYXlsb2FkLCBzb3J0X2tleXM9VHJ1ZSkuZW5jb2RlKCJ1dGYtOCIpCiAgICAgICAgc2VsZi5zZW5kX3Jlc3BvbnNlKHN0YXR1cykKICAgICAgICBzZWxmLnNlbmRfaGVhZGVyKCJDb250ZW50LVR5cGUiLCAiYXBwbGljYXRpb24vanNvbiIpCiAgICAgICAgc2VsZi5zZW5kX2hlYWRlcigiQ29udGVudC1MZW5ndGgiLCBzdHIobGVuKHJhdykpKQogICAgICAgIHNlbGYuZW5kX2hlYWRlcnMoKQogICAgICAgIHNlbGYud2ZpbGUud3JpdGUocmF3KQogICAgZGVmIGRvX0dFVChzZWxmKToKICAgICAgICBwYXRoID0gdXJscGFyc2Uoc2VsZi5wYXRoKS5wYXRoCiAgICAgICAgaWYgcGF0aCBpbiB7Ii8iLCAiL2hlYWx0aHoiLCAiL2FwaS9odWIvdjEvaGVhbHRoIn06CiAgICAgICAgICAgIHNlbGYuX3NlbmQoMjAwLCB7CiAgICAgICAgICAgICAgICAib2siOiBUcnVlLAogICAgICAgICAgICAgICAgInNlcnZpY2UiOiAibWFpbi1jb21wdXRlci1hbGxmYXRoZXItYm9vdHN0cmFwLWh1YiIsCiAgICAgICAgICAgICAgICAibmV0d29ya19rZXkiOiBuZXR3b3JrLAogICAgICAgICAgICAgICAgImNlbGxfaWQiOiBjZWxsX2lkLAogICAgICAgICAgICAgICAgImJvb3RzdHJhcF9odWIiOiBUcnVlLAogICAgICAgICAgICAgICAgImZ1bGxfbWFpbl9jb21wdXRlcl9odWIiOiBGYWxzZSwKICAgICAgICAgICAgICAgICJyZWFzb24iOiAiaW5saW5lIENvb2xpZnkgc3VwZXItbm9kZSBpbWFnZSBoYXMgbm90IGJ1bmRsZWQgdGhlIGZ1bGwgTWFpbiBDb21wdXRlciBodWIgcnVudGltZSB5ZXQiCiAgICAgICAgICAgIH0pCiAgICAgICAgZWxzZToKICAgICAgICAgICAgc2VsZi5fc2VuZCg0MDQsIHsib2siOiBGYWxzZSwgImVycm9yIjogIm5vdC1mb3VuZCIsICJwYXRoIjogcGF0aH0pCiAgICBkZWYgbG9nX21lc3NhZ2Uoc2VsZiwgZm10LCAqYXJncyk6CiAgICAgICAgcHJpbnQoIltodWItYm9vdHN0cmFwXSAiICsgKGZtdCAlIGFyZ3MpLCBmbHVzaD1UcnVlKQpUaHJlYWRpbmdIVFRQU2VydmVyKCgiMC4wLjAuMCIsIHBvcnQpLCBIYW5kbGVyKS5zZXJ2ZV9mb3JldmVyKCkK"))
    return script

def ensure_hub(validator_ready: bool) -> bool:
    hub_port = int(ports.get("hub_container") or 8785)
    if not validator_ready:
        state("hub", name=components.get("hub"), desired=True, running=False, status="pending-validator-rpc", port=hub_port, public_cutover_deferred=bool(bootstrap.get("hub_public_cutover_deferred", True)))
        return False
    script = write_bootstrap_hub_script()
    start_child(
        "hub",
        ["python", "-u", str(script)],
        env_extra={
            "MC_ALLFATHER_HUB_PORT": str(hub_port),
            "MC_ALLFATHER_NETWORK": network_key,
            "MC_ALLFATHER_CELL_ID": cell_id,
        },
    )
    running = child_running("hub")
    health_ok = http_json_ok(f"http://127.0.0.1:{hub_port}/api/hub/v1/health", timeout=0.7)
    status = "running-bootstrap-listener" if running and health_ok else "starting" if running else "stopped"
    state(
        "hub",
        name=components.get("hub"),
        desired=True,
        running=running,
        status=status,
        port=hub_port,
        health_ok=health_ok,
        bootstrap_hub=True,
        full_main_computer_hub=False,
        public_cutover_deferred=bool(bootstrap.get("hub_public_cutover_deferred", True)),
        last_exit_code=child_exit("hub"),
    )
    return running and health_ok

def update_bootstrap(validator_ready: bool) -> None:
    hub_admin_ready = ensure_hub_admin(validator_ready)
    ensure_contracts(validator_ready, hub_admin_ready)

def converge_once() -> None:
    ensure_dirs()
    fdb_ready = ensure_fdb()
    validator_ready = ensure_validator_rpc(fdb_ready)
    ensure_hub(validator_ready)
    update_bootstrap(validator_ready)

def supervisor_loop() -> None:
    while not stop_requested:
        try:
            converge_once()
        except Exception as exc:
            state("supervisor", desired=True, running=True, status="error", last_error=f"{type(exc).__name__}: {exc}")
        time.sleep(float(os.environ.get("MC_ALLFATHER_SUPERVISOR_TICK_S", "5")))

def function_statuses() -> dict:
    with lock:
        functions = {
            "guard": {
                "name": components.get("guard"),
                "desired": True,
                "running": True,
                "status": "running",
                "port": port,
            },
            "foundationdb": {
                "name": components.get("fdb"),
                "desired": True,
                "running": False,
                "status": "pending-supervisor",
                "port": ports.get("fdb_container"),
                "bootstrap_action": fdb_plan.get("action"),
                "cluster_file_present": bool(fdb_plan.get("cluster_file")),
            },
            "validator_rpc": {
                "name": components.get("validator_rpc"),
                "desired": True,
                "running": False,
                "status": "pending-supervisor",
                "rpc_port": ports.get("rpc_container"),
                "p2p_port": ports.get("p2p_container"),
                "besu_binary_present": bool(shutil.which("besu")),
            },
            "hub": {
                "name": components.get("hub"),
                "desired": True,
                "running": False,
                "status": "pending-validator-rpc",
                "port": ports.get("hub_container"),
                "public_cutover_deferred": bool(bootstrap.get("hub_public_cutover_deferred", True)),
            },
            "hub_admin": {
                "desired": bool(bootstrap.get("hub_admin_requested", True)),
                "running": False,
                "status": "deferred-until-live-validator-rpc",
                "private_key_present": bool(bootstrap.get("hub_admin_private_key_present")),
                "scope": "node",
            },
            "contracts": {
                "desired": bool(bootstrap.get("contracts_requested")),
                "running": False,
                "status": "deferred-until-live-validator-rpc" if bootstrap.get("contracts_requested") else "disabled",
            },
        }
        for key, updates in component_state.items():
            if key in functions and isinstance(updates, dict):
                functions[key].update(updates)
            elif isinstance(updates, dict):
                functions[key] = dict(updates)
        return functions

def payload(status: str = "running"):
    functions = function_statuses()
    supervisor_healthy = all(bool(functions.get(name, {}).get("running")) for name in ("foundationdb", "validator_rpc", "hub"))
    return {
        "ok": True,
        "service": "main-computer-allfather-super-node",
        "status": status,
        "supervisor_healthy": supervisor_healthy,
        "network_key": manifest.get("network_key"),
        "cell_id": manifest.get("cell_id"),
        "coolify_server": manifest.get("coolify_server"),
        "ordinal": manifest.get("ordinal"),
        "components": manifest.get("components"),
        "functions": functions,
        "desired_counts": manifest.get("desired_counts"),
        "bootstrap": manifest.get("bootstrap"),
        "public_routes": public_routes,
        "guardrails": manifest.get("guardrails"),
        "uptime_s": now_s(),
    }

class Handler(BaseHTTPRequestHandler):
    def _send(self, status_code, body):
        raw = json.dumps(body, sort_keys=True).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/healthz":
            self._send(200, {"ok": True, "cell_id": manifest.get("cell_id"), "supervisor": function_statuses()})
        elif path == "/identity":
            self._send(200, payload())
        elif path == "/topology":
            self._send(200, {"ok": True, "network_key": manifest.get("network_key"), "cell_id": manifest.get("cell_id"), "topology": {"source": "self-manifest", "super_node": manifest}})
        elif path == "/qbft/bootstrap":
            self._send(200, qbft_bootstrap_payload())
        elif path in {"/status", "/processes"}:
            self._send(200, payload())
        else:
            self._send(404, {"ok": False, "error": "not-found", "path": path})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/wake":
            converge_once()
            self._send(200, {"ok": True, "wake": "all", "functions": function_statuses()})
        else:
            self._send(404, {"ok": False, "error": "not-found", "path": path})

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

def shutdown(*_args):
    global stop_requested
    stop_requested = True
    with lock:
        for name, proc in list(children.items()):
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        deadline = time.time() + 10
        for name, proc in list(children.items()):
            if proc is not None and proc.poll() is None:
                try:
                    proc.wait(timeout=max(0.1, deadline - time.time()))
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)
ensure_dirs()
threading.Thread(target=supervisor_loop, daemon=True).start()
print(f"all-father super-node guard/supervisor listening on 0.0.0.0:{port}", flush=True)
ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
"""
    return script.replace("__ALLFATHER_CONTRACT_SOURCES_B64__", json.dumps(contract_sources_b64, sort_keys=True)).strip()



def super_node_entrypoint_wrapper_script() -> str:
    """Return a PID 1 wrapper that exposes diagnostics before the real guard binds.

    The real super-node guard/supervisor still owns FDB, Besu, Hub, hub_admin,
    and contract bootstrap.  The wrapper binds the private guard port immediately
    and runs the real guard on a loopback child port, proxying requests once the
    child is ready.  If the child exits before binding, Coolify probes still get
    a structured ``guard-startup-failed`` status instead of a blind connection
    refusal.
    """

    return r"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import textwrap
import zlib
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

STARTED_AT = time.time()
PUBLIC_GUARD_PORT = int(os.environ.get("MC_ALLFATHER_GUARD_PORT", "41414"))
CHILD_GUARD_PORT = int(os.environ.get("MC_ALLFATHER_GUARD_CHILD_PORT") or str(PUBLIC_GUARD_PORT + 1))
GUARD_SCRIPT = os.environ.get("MC_ALLFATHER_GUARD_SCRIPT", "/usr/local/bin/allfather-super-guard.py")
CHILD_RESTART_S = float(os.environ.get("MC_ALLFATHER_GUARD_CHILD_RESTART_S", "5"))
PROXY_TIMEOUT_S = float(os.environ.get("MC_ALLFATHER_GUARD_PROXY_TIMEOUT_S", "3"))

stop_requested = False
state_lock = threading.RLock()
child_proc = None
child_started_at = 0.0
last_exit_code = None
last_error = ""
log_tail = []


def now_s() -> float:
    return round(time.time() - STARTED_AT, 3)


def append_log(line: str) -> None:
    text = str(line or "").rstrip()
    if not text:
        return
    print(text, flush=True)
    with state_lock:
        log_tail.append(text)
        del log_tail[:-80]


def child_port_open(timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", CHILD_GUARD_PORT), timeout=timeout):
            return True
    except Exception:
        return False


def child_status() -> tuple[bool, int | None, float, str, list[str]]:
    with state_lock:
        proc = child_proc
        code = proc.poll() if proc is not None else last_exit_code
        running = proc is not None and code is None
        return running, code, child_started_at, last_error, list(log_tail)


def read_child_output(proc: subprocess.Popen) -> None:
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            try:
                line = raw.decode("utf-8", "replace")
            except Exception:
                line = str(raw)
            append_log("[guard-child] " + line.rstrip())
    except Exception as exc:
        append_log(f"[guard-wrapper] child log reader failed: {type(exc).__name__}: {exc}")


def child_loop() -> None:
    global child_proc, child_started_at, last_exit_code, last_error
    while not stop_requested:
        env = dict(os.environ)
        env["MC_ALLFATHER_GUARD_PORT"] = str(CHILD_GUARD_PORT)
        cmd = [sys.executable, "-u", GUARD_SCRIPT]
        append_log(f"[guard-wrapper] starting child guard on 127.0.0.1:{CHILD_GUARD_PORT}: {' '.join(cmd)}")
        try:
            proc = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except Exception as exc:
            with state_lock:
                child_proc = None
                last_exit_code = None
                last_error = f"{type(exc).__name__}: {exc}"
            append_log(f"[guard-wrapper] failed to start child guard: {last_error}")
            time.sleep(CHILD_RESTART_S)
            continue

        with state_lock:
            child_proc = proc
            child_started_at = time.time()
            last_exit_code = None
            last_error = ""

        threading.Thread(target=read_child_output, args=(proc,), daemon=True).start()
        code = proc.wait()
        with state_lock:
            last_exit_code = code
            last_error = f"child guard exited with code {code}"
            child_proc = None
        append_log(f"[guard-wrapper] child guard exited with code {code}")
        if stop_requested:
            break
        time.sleep(CHILD_RESTART_S)


def proxy_to_child(method: str, path: str, body: bytes | None = None) -> tuple[int, bytes, str]:
    url = f"http://127.0.0.1:{CHILD_GUARD_PORT}{path}"
    request = urllib.request.Request(url, data=body, method=method, headers={"Accept": "application/json"})
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=PROXY_TIMEOUT_S) as response:
            raw = response.read()
            return int(response.status), raw, response.headers.get("Content-Type", "application/json")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return int(exc.code), raw, exc.headers.get("Content-Type", "application/json")
    except Exception as exc:
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc


def diagnostic_payload(path: str, *, proxy_error: str = "") -> dict:
    running, code, started, error, tail = child_status()
    child_ready = child_port_open()
    status = "guard-child-ready" if child_ready else "guard-starting" if running else "guard-startup-failed"
    reason = "" if child_ready else error or proxy_error or "child guard has not accepted connections yet"
    return {
        "ok": False,
        "service": "main-computer-allfather-super-node",
        "status": status,
        "error": f"guard-startup-failed: {reason}" if status == "guard-startup-failed" else reason,
        "wrapper": {
            "ok": True,
            "public_guard_port": PUBLIC_GUARD_PORT,
            "child_guard_port": CHILD_GUARD_PORT,
            "child_running": running,
            "child_ready": child_ready,
            "child_exit_code": code,
            "child_started_uptime_s": round(started - STARTED_AT, 3) if started else None,
            "proxy_error": proxy_error,
            "log_tail": tail[-40:],
        },
        "functions": {
            "guard": {
                "desired": True,
                "running": True,
                "status": "diagnostic-wrapper",
                "port": PUBLIC_GUARD_PORT,
            },
            "supervisor": {
                "desired": True,
                "running": False,
                "status": status,
                "last_error": reason,
                "last_exit_code": code,
                "log_tail": tail[-40:],
            },
        },
        "path": path,
        "uptime_s": now_s(),
    }


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, payload: dict) -> None:
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _proxy_or_diag(self, method: str) -> None:
        path = self.path or "/"
        parsed_path = urlparse(path).path
        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length) if length > 0 else b""
        try:
            status_code, raw, content_type = proxy_to_child(method, path, body)
        except Exception as exc:
            self._send_json(200, diagnostic_payload(parsed_path, proxy_error=f"{type(exc).__name__}: {exc}"))
            return
        self.send_response(status_code)
        self.send_header("Content-Type", content_type or "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._proxy_or_diag("GET")

    def do_POST(self):
        self._proxy_or_diag("POST")

    def log_message(self, fmt, *args):
        print("[guard-wrapper] %s - %s" % (self.address_string(), fmt % args), flush=True)


def shutdown(*_args) -> None:
    global stop_requested
    stop_requested = True
    with state_lock:
        proc = child_proc
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

threading.Thread(target=child_loop, daemon=True).start()
print(f"[guard-wrapper] listening on 0.0.0.0:{PUBLIC_GUARD_PORT}; child guard port is 127.0.0.1:{CHILD_GUARD_PORT}", flush=True)
ThreadingHTTPServer(("0.0.0.0", PUBLIC_GUARD_PORT), Handler).serve_forever()
""".strip()





def dockerfile_payload_install_run(payloads: Mapping[str, str]) -> str:
    """Return a Dockerfile RUN step that writes compressed base64 payloads without heredocs.

    Coolify/BuildKit rejected the earlier generated Dockerfile at the giant
    ``RUN python - <<'PY'`` heredoc with ``unterminated heredoc``.  Keep every
    Dockerfile line short and avoid heredocs entirely by appending wrapped
    compressed base64 chunks through portable shell ``printf`` calls, then decoding
    them with Python inside the image.
    """

    lines = ["RUN set -eux; \\"]
    items = list(payloads.items())
    for index, (target, payload_b64) in enumerate(items):
        tmp_path = f"/tmp/allfather-payload-{index}.b64"
        chunks = [payload_b64[i : i + 76] for i in range(0, len(payload_b64), 76)] or [""]
        lines.append("    { \\")
        for chunk in chunks:
            lines.append(f"        printf '%s\\n' '{chunk}'; \\")
        lines.append(f"    }} > {tmp_path}; \\")
        lines.append(
            "    python -c 'import base64, pathlib, zlib; "
            f"pathlib.Path(\"{target}\").write_bytes(zlib.decompress(base64.b64decode(open(\"{tmp_path}\", \"rb\").read())))'; \\"
        )
        lines.append(f"    chmod 0755 {target}; \\")
        suffix = "; \\" if index < len(items) - 1 else ""
        lines.append(f"    rm -f {tmp_path}{suffix}")
    return "\n".join(lines)


def super_base_dockerfile_inline(source_image: str = DEFAULT_SUPER_BASE_SOURCE_IMAGE) -> str:
    """Return the heavy dependency base Dockerfile.

    This is built by a managed Coolify service through the host Docker socket.
    Normal super-node deployments must not reinstall apt/FDB/Python/web3/solc on
    every deploy; they should inherit from the resulting local image tag.
    """

    base = str(source_image or DEFAULT_SUPER_BASE_SOURCE_IMAGE).strip() or DEFAULT_SUPER_BASE_SOURCE_IMAGE
    return f"""
FROM {base}

USER root

RUN set -eux; \\
    if ! command -v apt-get >/dev/null 2>&1; then \\
        echo "The all-father super-node base image currently requires a Debian/Ubuntu Besu base so FoundationDB server .deb packages can be installed." >&2; \\
        exit 1; \\
    fi; \\
    apt-get update; \\
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \\
        bash build-essential ca-certificates curl python3 python3-dev python3-pip python3-venv procps netcat-openbsd; \\
    if [ "$(dpkg --print-architecture)" = "amd64" ]; then \\
        curl -fsSL -o /tmp/foundationdb-clients.deb \\
            "https://github.com/apple/foundationdb/releases/download/7.4.6/foundationdb-clients_7.4.6-1_amd64.deb"; \\
        curl -fsSL -o /tmp/foundationdb-server.deb \\
            "https://github.com/apple/foundationdb/releases/download/7.4.6/foundationdb-server_7.4.6-1_amd64.deb"; \\
    elif [ "$(dpkg --print-architecture)" = "arm64" ]; then \\
        curl -fsSL -o /tmp/foundationdb-clients.deb \\
            "https://github.com/apple/foundationdb/releases/download/7.4.6/foundationdb-clients_7.4.6-1_arm64.deb"; \\
        curl -fsSL -o /tmp/foundationdb-server.deb \\
            "https://github.com/apple/foundationdb/releases/download/7.4.6/foundationdb-server_7.4.6-1_arm64.deb"; \\
    else \\
        echo "Unsupported FoundationDB architecture: $(dpkg --print-architecture)" >&2; \\
        exit 1; \\
    fi; \\
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \\
        /tmp/foundationdb-clients.deb /tmp/foundationdb-server.deb; \\
    rm -f /tmp/foundationdb-clients.deb /tmp/foundationdb-server.deb; \\
    rm -rf /var/lib/apt/lists/*; \\
    python3 -m venv /opt/allfather-super-venv; \\
    /opt/allfather-super-venv/bin/python -m pip install --no-cache-dir --upgrade pip setuptools wheel; \\
    /opt/allfather-super-venv/bin/python -m pip install --no-cache-dir "foundationdb==7.4.6" "web3==6.20.4"; \\
    curl -fsSL -o /usr/local/bin/solc \\
        "https://github.com/ethereum/solidity/releases/download/v0.8.24/solc-static-linux"; \\
    chmod 0755 /usr/local/bin/solc; \\
    solc --version; \\
    mkdir -p /opt/allfather-contracts; \\
    ln -sf /opt/allfather-super-venv/bin/python /usr/local/bin/python; \\
    ln -sf /opt/allfather-super-venv/bin/pip /usr/local/bin/pip; \\
    if [ -x /usr/sbin/fdbserver ]; then \\
        ln -sf /usr/sbin/fdbserver /usr/local/bin/fdbserver; \\
    elif [ -x /usr/bin/fdbserver ]; then \\
        ln -sf /usr/bin/fdbserver /usr/local/bin/fdbserver; \\
    else \\
        echo "FoundationDB server binary was not found after package install" >&2; \\
        exit 1; \\
    fi; \\
    if [ -x /usr/bin/fdbcli ]; then \\
        ln -sf /usr/bin/fdbcli /usr/local/bin/fdbcli; \\
    elif [ -x /usr/sbin/fdbcli ]; then \\
        ln -sf /usr/sbin/fdbcli /usr/local/bin/fdbcli; \\
    else \\
        echo "FoundationDB CLI binary was not found after package install" >&2; \\
        exit 1; \\
    fi; \\
    if ! command -v besu >/dev/null 2>&1; then \\
        echo "Besu binary is required in the all-father super-node base image" >&2; \\
        exit 1; \\
    fi; \\
    besu --version; \\
    fdbserver --version; \\
    fdbcli --version

COPY allfather-contract-sources-b64.json /opt/allfather-contracts/contract-sources-b64.json
COPY build-allfather-contract-artifacts.py /opt/allfather-contracts/build-contract-artifacts.py

RUN set -eux; \\
    python3 /opt/allfather-contracts/build-contract-artifacts.py \\
        /opt/allfather-contracts/contract-sources-b64.json \\
        /opt/allfather-contracts/contracts-artifacts.json; \\
    test -s /opt/allfather-contracts/contracts-artifacts.json

ENV MC_ALLFATHER_IMAGE_KIND=besu-qbft-fdb-allfather-super-base \\
    MC_ALLFATHER_IMAGE_CAPABILITIES=python-venv,web3,fdb,solc,besu,qbft

EXPOSE {DEFAULT_SUPER_GUARD_CONTAINER_PORT} {DEFAULT_SUPER_HUB_CONTAINER_PORT} {DEFAULT_SUPER_RPC_CONTAINER_PORT} {DEFAULT_SUPER_FDB_CONTAINER_PORT} {DEFAULT_SUPER_P2P_CONTAINER_PORT}

ENV PATH="/opt/allfather-super-venv/bin:$PATH"
""".strip()


def super_node_dockerfile_inline(base_image: str, guard_script: str | None = None) -> str:
    """Return the small per-node Dockerfile for the all-father super-node image.

    The heavy dependency layer is built by the managed
    allfather-super-base-builder Coolify service.  Per-node deployments only bake
    in the current generated guard/supervisor payload, which keeps deploy jobs
    below Coolify worker timeouts and avoids manual host-side image builds.
    """

    base = str(base_image or DEFAULT_SUPER_BASE_IMAGE).strip() or DEFAULT_SUPER_BASE_IMAGE
    guard_script_b64 = base64.b64encode(zlib.compress((guard_script or super_server_command_script()).encode("utf-8"))).decode("ascii")
    wrapper_script_b64 = base64.b64encode(zlib.compress(super_node_entrypoint_wrapper_script().encode("utf-8"))).decode("ascii")
    payload_install = dockerfile_payload_install_run(
        {
            "/usr/local/bin/allfather-super-guard.py": guard_script_b64,
            "/usr/local/bin/allfather-super-entrypoint.py": wrapper_script_b64,
        }
    )
    return f"""
FROM {base}

USER root

{payload_install}

ENV MC_ALLFATHER_IMAGE_KIND=besu-qbft-fdb-allfather-super \\
    MC_ALLFATHER_IMAGE_CAPABILITIES=guard,supervisor,hub-bootstrap,hub-admin-bootstrap,contract-deploy,fdb,validator-rpc,besu,qbft,traefik-targets \\
    MC_ALLFATHER_IMAGE_ENTRYPOINT=allfather-super-entrypoint

EXPOSE {DEFAULT_SUPER_GUARD_CONTAINER_PORT} {DEFAULT_SUPER_HUB_CONTAINER_PORT} {DEFAULT_SUPER_RPC_CONTAINER_PORT} {DEFAULT_SUPER_FDB_CONTAINER_PORT} {DEFAULT_SUPER_P2P_CONTAINER_PORT}

ENV PATH="/opt/allfather-super-venv/bin:$PATH"

ENTRYPOINT ["/opt/allfather-super-venv/bin/python", "-u", "/usr/local/bin/allfather-super-entrypoint.py"]
""".strip()



def escape_compose_interpolation(text: str) -> str:
    """Escape Docker Compose interpolation inside literal inline payloads.

    ``dockerfile_inline`` is still parsed as part of the Compose file before it
    is handed to Docker BuildKit.  A Dockerfile naturally contains shell and ARG
    references like ``$arch`` and ``${FDB_VERSION}``; without escaping, Compose
    treats them as Compose variables and Coolify logs noisy "variable is not
    set" warnings before building the wrong Dockerfile.  Compose converts ``$$``
    back to ``$`` for the builder.
    """

    return text.replace("$", "$$")


def yaml_block_scalar(text: str, indent: int) -> list[str]:
    prefix = " " * indent
    return [f"{prefix}{line}" if line else prefix for line in text.splitlines()]


def shell_write_text_file_command(path: str, text: str) -> list[str]:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    wrapped = textwrap.wrap(encoded, 76)
    lines = [f"cat > {shell_single_quote(path)}.b64 <<'ALLFATHER_B64'"]
    lines.extend(wrapped)
    lines.append("ALLFATHER_B64")
    lines.append(f"base64 -d {shell_single_quote(path)}.b64 > {shell_single_quote(path)}")
    lines.append(f"rm -f {shell_single_quote(path)}.b64")
    return lines


def super_base_builder_command_script(
    *,
    target_image: str = DEFAULT_SUPER_BASE_IMAGE,
    source_image: str = DEFAULT_SUPER_BASE_SOURCE_IMAGE,
    force_rebuild: bool = False,
) -> str:
    dockerfile = super_base_dockerfile_inline(source_image)
    contract_sources_json = json.dumps(allfather_contract_sources_b64(), sort_keys=True)
    contract_builder_script = allfather_contract_artifact_builder_script()
    write_dockerfile = "\n".join(shell_write_text_file_command("/work/allfather-super-base.Dockerfile", dockerfile))
    write_contract_sources = "\n".join(shell_write_text_file_command("/work/allfather-contract-sources-b64.json", contract_sources_json + "\n"))
    write_contract_builder = "\n".join(shell_write_text_file_command("/work/build-allfather-contract-artifacts.py", contract_builder_script))
    force = "1" if force_rebuild else "0"
    return f"""set -eu
TARGET_IMAGE={shell_single_quote(target_image)}
SOURCE_IMAGE={shell_single_quote(source_image)}
FORCE_REBUILD={shell_single_quote(force)}
STATUS_PORT={shell_single_quote(str(DEFAULT_SUPER_BASE_BUILDER_CONTAINER_PORT))}
mkdir -p /work/www
log() {{
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"
    printf '%s allfather-super-base-builder: %s\\n' "$ts" "$*"
}}
write_status() {{
    phase="$1"
    ok="$2"
    error="${{3:-}}"
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"
    health_ok=true
    if [ "$phase" = "failed" ]; then
        health_ok=false
    fi
    cat > /work/www/status <<EOF
{{"ok": $ok, "service": "main-computer-allfather-super-base-builder", "status": "$phase", "phase": "$phase", "image": "$TARGET_IMAGE", "source_image": "$SOURCE_IMAGE", "error": "$error", "updated_at": "$ts", "public_guard_routes": false, "ssh_used": false, "direct_vpn_used": false}}
EOF
    cat > /work/www/healthz <<EOF
{{"ok": $health_ok, "service": "main-computer-allfather-super-base-builder", "status": "$phase", "phase": "$phase", "image": "$TARGET_IMAGE", "source_image": "$SOURCE_IMAGE", "error": "$error", "updated_at": "$ts", "public_guard_routes": false, "ssh_used": false, "direct_vpn_used": false}}
EOF
    cp /work/www/status /work/www/identity
    cp /work/www/status /work/www/topology
}}
start_httpd_candidate() {{
    label="$1"
    shift
    "$@" >>/work/httpd.log 2>&1 &
    server_pid="$!"
    sleep 1
    if kill -0 "$server_pid" >/dev/null 2>&1; then
        log "status_http=running method=$label pid=$server_pid port=$STATUS_PORT"
        return 0
    fi
    log "status_http=failed method=$label port=$STATUS_PORT"
    return 1
}}
start_status_server() {{
    : > /work/httpd.log
    if command -v httpd >/dev/null 2>&1; then
        start_httpd_candidate httpd httpd -f -p "0.0.0.0:$STATUS_PORT" -h /work/www && return 0
    fi
    if command -v busybox >/dev/null 2>&1; then
        start_httpd_candidate busybox-httpd busybox httpd -f -p "0.0.0.0:$STATUS_PORT" -h /work/www && return 0
    fi
    if command -v apk >/dev/null 2>&1; then
        apk add --no-cache busybox-extras >>/work/httpd.log 2>&1 || true
        if command -v httpd >/dev/null 2>&1; then
            start_httpd_candidate apk-httpd httpd -f -p "0.0.0.0:$STATUS_PORT" -h /work/www && return 0
        fi
        if command -v busybox >/dev/null 2>&1; then
            start_httpd_candidate apk-busybox-httpd busybox httpd -f -p "0.0.0.0:$STATUS_PORT" -h /work/www && return 0
        fi
    fi
    echo "no status HTTP server available in builder image" >>/work/httpd.log
    log "status_http=unavailable port=$STATUS_PORT"
    return 1
}}
write_status starting false ""
log "phase=starting target=$TARGET_IMAGE"
start_status_server || true
{write_dockerfile}
{write_contract_sources}
{write_contract_builder}
if [ "$FORCE_REBUILD" != "1" ] && docker image inspect "$TARGET_IMAGE" >/dev/null 2>&1; then
    echo "Base image already exists: $TARGET_IMAGE" > /work/build.log
    log "phase=ready target=$TARGET_IMAGE already_exists=true"
    write_status ready true ""
else
    log "phase=building target=$TARGET_IMAGE source=$SOURCE_IMAGE"
    write_status building false ""
    set +e
    docker build -t "$TARGET_IMAGE" -f /work/allfather-super-base.Dockerfile /work > /work/build.log 2>&1
    rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then
        log "phase=ready target=$TARGET_IMAGE built=true"
        write_status ready true ""
    else
        tail -120 /work/build.log > /work/build-tail.log 2>/dev/null || true
        error="$(tr '\\n"' '  ' < /work/build-tail.log | cut -c1-1200)"
        log "phase=failed target=$TARGET_IMAGE rc=$rc"
        write_status failed false "$error"
    fi
fi
touch /work/build.log
tail -f /work/build.log /dev/null
""".strip()


def render_super_base_builder_compose(
    head: HeadNode,
    *,
    target_image: str = DEFAULT_SUPER_BASE_IMAGE,
    source_image: str = DEFAULT_SUPER_BASE_SOURCE_IMAGE,
    builder_image: str = DEFAULT_SUPER_BASE_BUILDER_IMAGE,
    force_rebuild: bool = False,
) -> str:
    service_name = super_base_builder_service_name(head)
    private_bind_host = str(head.guard_publish_host or "").strip()
    host_port = super_base_builder_host_port(head)
    container_port = DEFAULT_SUPER_BASE_BUILDER_CONTAINER_PORT
    state_dir = f"{head.state_root.rstrip('/')}/super-base-builder"
    healthcheck_command = "test -f /work/www/healthz && grep -q '\"ok\": true' /work/www/healthz"
    command_script = super_base_builder_command_script(
        target_image=target_image,
        source_image=source_image,
        force_rebuild=force_rebuild,
    )
    def private_port_mapping() -> str:
        if private_bind_host:
            return f"{private_bind_host}:{host_port}:{container_port}/tcp"
        return f"{host_port}:{container_port}/tcp"
    lines = [
        f"name: {service_name}",
        "",
        "services:",
        f"  {service_name}:",
        f"    image: {yaml_quote(builder_image)}",
        "    restart: unless-stopped",
        "    command:",
        "      - /bin/sh",
        "      - -lc",
        "      - |",
    ]
    compose_command_script = command_script.replace("$", "$$")
    lines.extend(f"        {line}" for line in compose_command_script.splitlines())
    lines.extend(
        [
            "    environment:",
            f"      MC_ALLFATHER_SUPER_BASE_BUILDER: {yaml_quote('1')}",
            f"      MC_ALLFATHER_SUPER_BASE_TARGET_IMAGE: {yaml_quote(target_image)}",
            f"      MC_ALLFATHER_SUPER_BASE_SOURCE_IMAGE: {yaml_quote(source_image)}",
            f"      MC_ALLFATHER_SUPER_BASE_FORCE_REBUILD: {yaml_quote('1' if force_rebuild else '0')}",
            "    expose:",
            f"      - {yaml_quote(container_port)}",
            "    ports:",
            f"      - {yaml_quote(private_port_mapping())}",
            "    volumes:",
            f"      - {yaml_quote('/var/run/docker.sock:/var/run/docker.sock')}",
            f"      - {yaml_quote(f'{state_dir}:/work')}",
            "    healthcheck:",
            "      test:",
            "        - CMD-SHELL",
            f"        - {yaml_quote(healthcheck_command)}",
            "      interval: 10s",
            "      timeout: 5s",
            "      start_period: 5s",
            "      retries: 6",
            "",
        ]
    )
    return "\n".join(lines)


def super_base_builder_service_payload(
    head: HeadNode,
    args: argparse.Namespace,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    service_name = super_base_builder_service_name(head)
    compose = render_super_base_builder_compose(
        head,
        target_image=getattr(args, "super_image", DEFAULT_SUPER_IMAGE),
        source_image=getattr(args, "super_base_source_image", DEFAULT_SUPER_BASE_SOURCE_IMAGE),
        builder_image=getattr(args, "super_base_builder_image", DEFAULT_SUPER_BASE_BUILDER_IMAGE),
        force_rebuild=bool(getattr(args, "force_super_base_rebuild", False)),
    )
    payload = {
        "server_uuid": (context or {}).get("server_uuid") or "",
        "project_uuid": (context or {}).get("project_uuid") or "",
        "environment_name": (context or {}).get("environment_name") or getattr(args, "coolify_environment_name", "") or DEFAULT_SUPER_ENVIRONMENT,
        "environment_uuid": (context or {}).get("environment_uuid") or "",
        "name": service_name,
        "description": (
            "Main Computer managed all-father super-node base-image builder. "
            "Runs through Coolify and the host Docker socket; no SSH or manual host build is required."
        ),
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "instant_deploy": False,
    }
    destination_uuid = destination_uuid_for_host(head, args)
    if destination_uuid:
        payload["destination_uuid"] = destination_uuid
    return {key: value for key, value in payload.items() if value not in (None, "")}


def sync_super_base_builder_service(
    client: Any,
    head: HeadNode,
    args: argparse.Namespace,
    context: Mapping[str, Any],
    tried: list[dict[str, Any]],
) -> tuple[str, str, dict[str, Any]]:
    service_name = super_base_builder_service_name(head)
    service_uuid, existing = hub_service_tool().find_service(client, service_name=service_name, explicit_uuid="", tried=tried)
    compose = render_super_base_builder_compose(
        head,
        target_image=getattr(args, "super_image", DEFAULT_SUPER_IMAGE),
        source_image=getattr(args, "super_base_source_image", DEFAULT_SUPER_BASE_SOURCE_IMAGE),
        builder_image=getattr(args, "super_base_builder_image", DEFAULT_SUPER_BASE_BUILDER_IMAGE),
        force_rebuild=bool(getattr(args, "force_super_base_rebuild", False)),
    )
    if service_uuid:
        fdb_tool().update_service(client, service_uuid, service_name, compose, tried)
        return service_uuid, "updated", existing
    service_uuid = fdb_tool().create_service(client, super_base_builder_service_payload(head, args, context), tried)
    return service_uuid, "created", existing


def probe_target_for_super_base_builder(head: HeadNode) -> dict[str, Any]:
    return {
        "kind": "super-base-builder",
        "guard_url": super_base_builder_url(head),
        "service_name": super_base_builder_service_name(head),
        "coolify_server": head.coolify_server,
        "host_slot": head.slot,
        "scope": "remote-vpn-super-base-builder-only",
    }


def probe_result_payload_mapping(probe_result: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the actual probe result payload, accepting raw and metadata-wrapped forms."""

    if not isinstance(probe_result, Mapping):
        return {}
    nested = probe_result.get("result")
    if isinstance(nested, Mapping):
        return nested
    return probe_result


def probe_result_target_by_service(probe_result: Mapping[str, Any], service_name: str) -> dict[str, Any]:
    payload = probe_result_payload_mapping(probe_result)
    for item in payload.get("targets") or []:
        if isinstance(item, Mapping) and str(item.get("service_name") or "") == service_name:
            return dict(item)
    return {}


def operator_log(args: argparse.Namespace, message: str) -> None:
    """Emit concise operator progress to stderr without corrupting JSON stdout."""

    if bool(getattr(args, "quiet", False)):
        return
    command = str(getattr(args, "command", "") or "allfather").strip()
    prefix = f"[allfather {command}]"
    print(f"{prefix} {message}", file=sys.stderr, flush=True)


def operator_log_interval_s(args: argparse.Namespace) -> float:
    try:
        value = float(getattr(args, "operator_log_interval_s", 15.0))
    except (TypeError, ValueError):
        return 15.0
    return max(5.0, value)


def compact_wait_target_status(target: Mapping[str, Any]) -> str:
    if not isinstance(target, Mapping) or not target:
        return "not-observed"
    phase = str(target.get("phase") or target.get("status_text") or "").strip() or "unknown"
    status_ok = "ok" if bool(target.get("status_ok")) else "not-ready"
    healthz_ok = "ok" if bool(target.get("healthz_ok")) else "not-healthy"
    error = str(target.get("error") or "").strip()
    pieces = [f"phase={phase}", f"status={status_ok}", f"healthz={healthz_ok}"]
    if error:
        pieces.append(f"error={error[:180]}")
    return " ".join(pieces)


def super_base_builder_target_is_live_building(target: Mapping[str, Any]) -> bool:
    if not isinstance(target, Mapping) or not target:
        return False
    phase = str(target.get("phase") or target.get("status_text") or target.get("status") or "").strip().lower()
    if phase not in {"building", "starting"}:
        return False
    return bool(target.get("healthz_ok") or target.get("ok"))


def super_base_builder_status_from_logs_body(body: Any, target_image: str) -> dict[str, Any]:
    """Extract ready/failed state from managed base-builder Coolify logs."""

    target = str(target_image or "").strip()
    found_ready = False
    found_failed = False
    failed_tail = ""
    for text in _strings_from_nested(body):
        lines = text.splitlines()
        for line in lines:
            clean = line.strip()
            if not clean:
                continue
            if (
                ("allfather-super-base-builder: phase=ready" in clean and (not target or f"target={target}" in clean))
                or (target and f"Base image already exists: {target}" in clean)
                or (target and f"naming to docker.io/{target} done" in clean)
            ):
                found_ready = True
            if "allfather-super-base-builder: phase=failed" in clean and (not target or f"target={target}" in clean):
                found_failed = True
                failed_tail = clean[-1200:]
    if found_failed:
        return {"observed": True, "ready": False, "failed": True, "error": failed_tail or "base-builder reported failed"}
    if found_ready:
        return {"observed": True, "ready": True, "failed": False, "error": ""}
    return {"observed": False, "ready": False, "failed": False, "error": ""}


def fetch_super_base_builder_log_status(
    client: Any,
    service_uuid: str,
    service_name: str,
    target_image: str,
    tried: list[dict[str, Any]],
) -> dict[str, Any]:
    """Poll Coolify service/application logs for the managed base-builder state."""

    if not service_uuid:
        return {"observed": False, "ready": False, "failed": False, "source": "missing-service-uuid"}
    detail = fetch_service_detail(client, service_uuid, tried)

    # Some Coolify versions embed recent service/application log fragments or
    # status strings in the service detail.  Check that body first so a ready
    # builder is not ignored merely because the logs endpoint shape changed.
    detail_status = super_base_builder_status_from_logs_body(detail.get("body"), target_image)
    if bool(detail_status.get("ready")) or bool(detail_status.get("failed")):
        detail_status["source"] = "coolify-service-detail"
        detail_status["application_uuid"] = ""
        return detail_status

    applications = application_records_from_service_detail(detail)
    application_uuids: list[str] = []
    for app in applications:
        uuid = str(app.get("uuid") or "").strip()
        name = str(app.get("name") or "")
        if uuid and service_name and service_name in name:
            application_uuids.insert(0, uuid)
        elif uuid:
            application_uuids.append(uuid)
    # De-duplicate while preserving the service-name-preferred ordering.
    application_uuids = list(dict.fromkeys(application_uuids))

    log_errors: list[str] = []
    candidates = application_uuids or [""]
    for application_uuid in candidates:
        logs = fetch_probe_logs(client, service_uuid, tried, application_uuid=application_uuid)
        if not logs.get("ok"):
            error = str(logs.get("error") or logs.get("source") or "").strip()
            if error:
                log_errors.append(error)
            continue
        status = super_base_builder_status_from_logs_body(logs.get("body"), target_image)
        status["source"] = logs.get("source") or "coolify-logs"
        status["application_uuid"] = application_uuid
        if bool(status.get("ready")) or bool(status.get("failed")) or bool(status.get("observed")):
            return status

    return {
        "observed": False,
        "ready": False,
        "failed": False,
        "source": "coolify-logs",
        "application_uuid": application_uuids[0] if application_uuids else "",
        "error": "; ".join(log_errors[-3:]),
    }


def wait_for_super_base_builder_ready(
    plan: HeadPlan,
    head: HeadNode,
    client: Any,
    args: argparse.Namespace,
    context: Mapping[str, Any],
    tried: list[dict[str, Any]],
    *,
    wait_s: float,
    builder_service_uuid: str = "",
) -> dict[str, Any]:
    service_name = super_base_builder_service_name(head)
    target = probe_target_for_super_base_builder(head)
    deadline = time.time() + max(0.0, float(wait_s or 0.0))
    log_interval = operator_log_interval_s(args)
    builder_log_poll_interval = min(max(5.0, log_interval), 15.0)

    probe_service_uuid = ""
    probe_action = ""
    probe_started = False
    last_probe_result: dict[str, Any] = {}
    last_target: dict[str, Any] = {}
    last_builder_log_status: dict[str, Any] = {}
    last_log_signature = ""
    last_log_at = 0.0
    last_builder_log_poll_at = 0.0
    first = True

    def ready_result(observed_by: str) -> dict[str, Any]:
        return {
            "enabled": True,
            "ready": True,
            "reason": "managed super base image is ready",
            "service_name": service_name,
            "service_uuid": builder_service_uuid,
            "probe_service_uuid": probe_service_uuid,
            "probe_action": probe_action,
            "target_image": getattr(args, "super_image", DEFAULT_SUPER_IMAGE),
            "source_image": getattr(args, "super_base_source_image", DEFAULT_SUPER_BASE_SOURCE_IMAGE),
            "status_url": target.get("guard_url"),
            "observed_by": observed_by,
            "builder_log_status": last_builder_log_status,
            "observed_target": last_target,
            "public_guard_routes": False,
            "ssh_used": False,
            "direct_vpn_used": False,
        }

    while first or time.time() < deadline:
        now = time.time()
        first = False

        # Primary path: the managed builder is a Coolify service whose own logs
        # say whether the host-local base image is ready.  Do not block this
        # stage on an HTTP probe of the builder sidecar; the sidecar may have
        # completed successfully even if its optional status listener is absent.
        if builder_service_uuid and (now - last_builder_log_poll_at) >= builder_log_poll_interval:
            last_builder_log_poll_at = now
            last_builder_log_status = fetch_super_base_builder_log_status(
                client,
                builder_service_uuid,
                service_name,
                getattr(args, "super_image", DEFAULT_SUPER_IMAGE),
                tried,
            )
            if bool(last_builder_log_status.get("ready")):
                operator_log(args, f"base-image: ready ({getattr(args, 'super_image', DEFAULT_SUPER_IMAGE)}; observed={last_builder_log_status.get('source') or 'builder-logs'})")
                return ready_result("builder-logs")
            if bool(last_builder_log_status.get("failed")):
                error = str(last_builder_log_status.get("error") or "managed super base image builder reported failed")
                operator_log(args, f"base-image: failed in builder logs; {error[:240]}")
                return {
                    "enabled": True,
                    "ready": False,
                    "reason": error,
                    "service_name": service_name,
                    "service_uuid": builder_service_uuid,
                    "probe_service_uuid": probe_service_uuid,
                    "probe_action": probe_action,
                    "target_image": getattr(args, "super_image", DEFAULT_SUPER_IMAGE),
                    "source_image": getattr(args, "super_base_source_image", DEFAULT_SUPER_BASE_SOURCE_IMAGE),
                    "status_url": target.get("guard_url"),
                    "observed_by": "builder-logs",
                    "builder_log_status": last_builder_log_status,
                    "public_guard_routes": False,
                    "ssh_used": False,
                    "direct_vpn_used": False,
                }

        # Secondary path: if the builder exposes its private status listener, the
        # normal probe can observe it.  This is useful but must not be the gate
        # that makes a ready builder look stuck.
        if target.get("guard_url"):
            if not probe_started:
                probe_service_uuid, probe_action, _probe_existing = sync_probe_service(
                    client,
                    plan,
                    head,
                    args,
                    context,
                    tried,
                    super_inventory=[target],
                )
                operator_log(
                    args,
                    f"base-image: using private probe service {probe_service_uuid or '<unknown>'} ({probe_action or 'synced'}); waiting up to {float(wait_s or 0.0):.0f}s",
                )
                hub_service_tool().trigger_deploy_service(
                    client,
                    service_uuid=probe_service_uuid,
                    force=True,
                    tried=tried,
                )
                probe_started = True
            detail = fetch_service_detail(client, probe_service_uuid, tried)
            last_probe_result = probe_result_from_service_metadata(detail)
            last_target = probe_result_target_by_service(last_probe_result, service_name)
            if last_target and bool(last_target.get("status_ok")) and bool(last_target.get("healthz_ok")):
                operator_log(args, f"base-image: ready ({getattr(args, 'super_image', DEFAULT_SUPER_IMAGE)}; observed=probe)")
                return ready_result("private-probe")

        signature_parts = []
        if last_builder_log_status:
            source = str(last_builder_log_status.get("source") or "builder-logs")
            if bool(last_builder_log_status.get("observed")):
                signature_parts.append(f"{source}: observed-not-ready")
            else:
                error = str(last_builder_log_status.get("error") or "").strip()
                signature_parts.append(f"{source}: not-observed" + (f" ({error[:120]})" if error else ""))
        else:
            signature_parts.append("builder-logs: not-polled")
        if last_target:
            signature_parts.append(f"probe: {compact_wait_target_status(last_target)}")
        elif probe_started:
            signature_parts.append("probe: not-observed")
        signature = "; ".join(signature_parts)
        now = time.time()
        if signature != last_log_signature or (now - last_log_at) >= log_interval:
            remaining = max(0.0, deadline - now)
            operator_log(args, f"base-image: {signature}; remaining={remaining:.0f}s")
            last_log_signature = signature
            last_log_at = now

        if time.time() >= deadline:
            break
        time.sleep(min(2.0, max(0.25, deadline - time.time())))

    error = str(last_builder_log_status.get("error") or last_target.get("error") or "")
    operator_log(args, f"base-image: not ready before timeout; last={last_log_signature or compact_wait_target_status(last_target)}")
    return {
        "enabled": True,
        "ready": False,
        "reason": error or "managed super base image was not ready before timeout",
        "service_name": service_name,
        "service_uuid": builder_service_uuid,
        "probe_service_uuid": probe_service_uuid,
        "probe_action": probe_action,
        "target_image": getattr(args, "super_image", DEFAULT_SUPER_IMAGE),
        "source_image": getattr(args, "super_base_source_image", DEFAULT_SUPER_BASE_SOURCE_IMAGE),
        "status_url": target.get("guard_url"),
        "observed_target": last_target,
        "builder_log_status": last_builder_log_status,
        "wait_s": float(wait_s or 0.0),
        "public_guard_routes": False,
        "ssh_used": False,
        "direct_vpn_used": False,
    }


def ensure_super_base_image(
    plan: HeadPlan,
    head: HeadNode,
    client: Any,
    args: argparse.Namespace,
    context: Mapping[str, Any],
    tried: list[dict[str, Any]],
) -> dict[str, Any]:
    if bool(getattr(args, "no_super_base_ensure", False)):
        return {
            "enabled": False,
            "ready": None,
            "reason": "disabled by --no-super-base-ensure",
            "public_guard_routes": False,
            "ssh_used": False,
            "direct_vpn_used": False,
        }
    target_image = getattr(args, "super_image", DEFAULT_SUPER_IMAGE)
    operator_log(args, f"base-image: ensuring {target_image} via managed Coolify builder on {head.coolify_server}")
    service_uuid, action, existing = sync_super_base_builder_service(client, head, args, context, tried)
    operator_log(args, f"base-image: builder service {action} uuid={service_uuid or '<unknown>'}")
    deploy_response = None
    force_base = bool(getattr(args, "force_super_base_rebuild", False))
    full_wait_s = float(getattr(args, "super_base_wait_s", DEFAULT_ADD_NODE_READY_WAIT_S))
    wait_result: dict[str, Any] | None = None

    # Do not blindly redeploy the managed builder every add-node invocation.
    # If the previous builder is already ready, continue immediately.  If it is
    # already live and building, wait on that build instead of restarting it and
    # pushing node creation farther out.
    if service_uuid and not force_base:
        precheck_wait_s = float(getattr(args, "super_base_predeploy_wait_s", 20.0))
        operator_log(args, f"base-image: checking existing builder before deploy; wait up to {precheck_wait_s:.0f}s")
        precheck = wait_for_super_base_builder_ready(
            plan,
            head,
            client,
            args,
            context,
            tried,
            wait_s=precheck_wait_s,
            builder_service_uuid=service_uuid,
        )
        if bool(precheck.get("ready")):
            operator_log(args, f"base-image: existing builder ready; skipping builder deploy")
            wait_result = precheck
        elif super_base_builder_target_is_live_building(precheck.get("observed_target") if isinstance(precheck.get("observed_target"), Mapping) else {}):
            operator_log(args, "base-image: existing builder is already building; continuing without restart")
            wait_result = wait_for_super_base_builder_ready(
                plan,
                head,
                client,
                args,
                context,
                tried,
                wait_s=full_wait_s,
                builder_service_uuid=service_uuid,
            )

    if wait_result is None:
        if not bool(getattr(args, "no_deploy", False)):
            operator_log(args, f"base-image: triggering builder deploy force={str(force_base).lower()}")
            deploy_response = hub_service_tool().trigger_deploy_service(
                client,
                service_uuid=service_uuid,
                force=force_base,
                tried=tried,
            )
        wait_result = wait_for_super_base_builder_ready(
            plan,
            head,
            client,
            args,
            context,
            tried,
            wait_s=full_wait_s,
            builder_service_uuid=service_uuid,
        )
    wait_result.update(
        {
            "service_uuid": service_uuid,
            "service_action": action,
            "existing": existing if bool(getattr(args, "verbose", False)) else "<hidden; pass --verbose>",
            "deploy_response": deploy_response if bool(getattr(args, "verbose", False)) else ("<hidden; pass --verbose>" if deploy_response else None),
        }
    )
    return wait_result



def render_super_node_compose(
    manifest: Mapping[str, Any],
    *,
    image: str = DEFAULT_SUPER_IMAGE,
    hub_admin_private_key: str = "",
    deployer_private_key: str = "",
    publish_routes: bool = False,
) -> str:
    ports = manifest.get("ports") if isinstance(manifest.get("ports"), Mapping) else {}
    components = manifest.get("components") if isinstance(manifest.get("components"), Mapping) else {}
    service_name = str(manifest.get("cell_id") or components.get("super") or "")
    command_script = super_server_command_script()
    manifest_b64_value = base64.b64encode(json.dumps(dict(manifest), sort_keys=True).encode("utf-8")).decode("ascii")
    fdb_plan = manifest.get("foundationdb") if isinstance(manifest.get("foundationdb"), Mapping) else {}
    fdb_plan_b64_value = base64.b64encode(json.dumps(dict(fdb_plan), sort_keys=True).encode("utf-8")).decode("ascii")
    state_root = str(manifest.get("state_root") or f"{DEFAULT_SUPER_STATE_ROOT_PREFIX}/{service_name}").rstrip("/")
    fdb_new_node = fdb_plan.get("new_node") if isinstance(fdb_plan.get("new_node"), Mapping) else {}
    private_bind_host = str(manifest.get("vpn_ip") or fdb_new_node.get("vpn_ip") or "").strip()
    guard_host = ports.get("guard_host")
    fdb_host = ports.get("fdb_host")
    p2p_host = ports.get("p2p_host")
    p2p_container_bind = ports.get("p2p_host") or ports.get("p2p_container")
    def private_port_mapping(host_port: Any, container_port: Any, protocol: str = "tcp") -> str:
        clean_protocol = str(protocol or "tcp").strip().lower()
        if private_bind_host:
            return f"{private_bind_host}:{host_port}:{container_port}/{clean_protocol}"
        return f"{host_port}:{container_port}/{clean_protocol}"
    dockerfile_inline = escape_compose_interpolation(super_node_dockerfile_inline(image, guard_script=command_script))
    lines = [
        f"name: {service_name}",
        "",
        "services:",
        f"  {service_name}:",
        "    # Build-only service: do not set image here. Coolify runs a pull phase",
        "    # before build, and a local generated image name causes pull access",
        "    # denied instead of letting the inline Dockerfile build.",
        "    build:",
        "      context: .",
        "      dockerfile_inline: |",
    ]
    lines.extend(yaml_block_scalar(dockerfile_inline, 8))
    lines.extend([
        f"    container_name: {yaml_quote(super_container_name_from_manifest(manifest))}",
        "    restart: unless-stopped",
        "    # Coolify's Compose validator accepts a null service entrypoint.",
        "    # The built image overrides the inherited Besu entrypoint and starts",
        "    # /usr/local/bin/allfather-super-guard.py so the private guard binds.",
        "    entrypoint: null",
    ])
    lines.extend(
        [
            "    environment:",
            f"      MC_ALLFATHER_SUPER_NODE: {yaml_quote('1')}",
            f"      MC_ALLFATHER_SUPER_BASE_IMAGE: {yaml_quote(image)}",
            f"      MC_ALLFATHER_COMPONENTS: {yaml_quote('guard,hub,fdb,validator-rpc')}",
            f"      MC_ALLFATHER_RUNTIME_MODE: {yaml_quote('guard-first')}",
            f"      MC_ALLFATHER_NETWORK: {yaml_quote(str(manifest.get('network_key') or ''))}",
            f"      MC_ALLFATHER_CELL_ID: {yaml_quote(service_name)}",
            f"      MC_ALLFATHER_DEPLOYMENT_ID: {yaml_quote(str(manifest.get('deployment_id') or ''))}",
            f"      MC_ALLFATHER_GUARD_PORT: {yaml_quote(str(ports.get('guard_container') or DEFAULT_SUPER_GUARD_CONTAINER_PORT))}",
            f"      MC_ALLFATHER_SUPER_MANIFEST_B64: {yaml_quote(manifest_b64_value)}",
            f"      MC_ALLFATHER_FDB_PLAN_B64: {yaml_quote(fdb_plan_b64_value)}",
            f"      MC_ALLFATHER_FDB_BOOTSTRAP_ACTION: {yaml_quote(str(fdb_plan.get('action') or ''))}",
            f"      MC_ALLFATHER_FDB_CLUSTER_FILE: {yaml_quote(str(fdb_plan.get('cluster_file') or ''))}",
            f"      MC_ALLFATHER_FDB_TARGET_CLUSTER_FILE: {yaml_quote(str(fdb_plan.get('target_cluster_file_after_reconfigure') or ''))}",
            f"      MC_ALLFATHER_FDB_RECONFIGURE_AFTER_JOIN: {yaml_quote('1' if fdb_plan.get('coordinator_reconfigure_required') else '0')}",
            f"      MC_ALLFATHER_HUB_ADMIN_PRIVATE_KEY: {yaml_quote(hub_admin_private_key)}",
            f"      MC_ALLFATHER_DEPLOYER_PRIVATE_KEY: {yaml_quote(deployer_private_key)}",
            f"      MC_ALLFATHER_BOOTSTRAP_HUB_ADMIN: {yaml_quote('1')}",
            f"      MC_ALLFATHER_HUB_ADMIN_CREATE_IF_MISSING: {yaml_quote('1' if manifest.get('bootstrap', {}).get('hub_admin_create_requested') else '0')}",
            f"      MC_ALLFATHER_HUB_ADMIN_DEFER_UNTIL_QBFT: {yaml_quote('1')}",
            f"      MC_ALLFATHER_BOOTSTRAP_CONTRACTS: {yaml_quote('1' if manifest.get('bootstrap', {}).get('contracts_requested') else '0')}",
            f"      MC_ALLFATHER_CONTRACTS_DEFER_UNTIL_QBFT: {yaml_quote('1')}",
            f"      MC_ALLFATHER_CONTRACTS_DEFER_UNTIL_HUB_ADMIN: {yaml_quote('1' if manifest.get('bootstrap', {}).get('contracts_deferred_until_hub_admin_ready') else '0')}",
            "    expose:",
            f"      - {yaml_quote(str(ports.get('guard_container') or DEFAULT_SUPER_GUARD_CONTAINER_PORT))}",
            f"      - {yaml_quote(str(ports.get('hub_container') or DEFAULT_SUPER_HUB_CONTAINER_PORT))}",
            f"      - {yaml_quote(str(ports.get('rpc_container') or DEFAULT_SUPER_RPC_CONTAINER_PORT))}",
            "    ports:",
            f"      - {yaml_quote(private_port_mapping(guard_host, ports.get('guard_container')))}",
            f"      - {yaml_quote(private_port_mapping(fdb_host, ports.get('fdb_container')))}",
            f"      - {yaml_quote(private_port_mapping(p2p_host, p2p_container_bind, 'tcp'))}",
            f"      - {yaml_quote(private_port_mapping(p2p_host, p2p_container_bind, 'udp'))}",
            "    volumes:",
            f"      - {yaml_quote(f'{state_root}:{state_root}')}",
            "    healthcheck:",
            "      test:",
            "        - CMD-SHELL",
            f"        - {yaml_quote(f'python -c \"import urllib.request; urllib.request.urlopen(\\\"http://127.0.0.1:{ports.get('guard_container') or DEFAULT_SUPER_GUARD_CONTAINER_PORT}/healthz\\\", timeout=3).read()\"')}",
            "      interval: 10s",
            "      timeout: 5s",
            "      start_period: 10s",
            "      retries: 6",
        ]
    )
    if publish_routes:
        hub = components.get("hub")
        rpc = components.get("rpc_route")
        labels = [
            "traefik.enable=true",
            f"traefik.http.routers.{hub}.rule=Host(`{hub}.greatlibrary.io`)",
            f"traefik.http.routers.{hub}.entryPoints=https",
            f"traefik.http.routers.{hub}.tls=true",
            f"traefik.http.routers.{hub}.tls.certresolver=letsencrypt",
            f"traefik.http.services.{hub}-svc.loadbalancer.server.port={ports.get('hub_container')}",
            f"traefik.http.routers.{rpc}.rule=Host(`{rpc}.greatlibrary.io`)",
            f"traefik.http.routers.{rpc}.entryPoints=https",
            f"traefik.http.routers.{rpc}.tls=true",
            f"traefik.http.routers.{rpc}.tls.certresolver=letsencrypt",
            f"traefik.http.services.{rpc}-svc.loadbalancer.server.port={ports.get('rpc_container')}",
            "coolify.managed=true",
        ]
        lines.append("    labels:")
        lines.extend(f"      - {yaml_quote(label)}" for label in labels)
    lines.append("")
    return "\n".join(lines)


def redact_super_compose(compose: str) -> str:
    redacted = []
    for line in compose.splitlines():
        if "MC_ALLFATHER_HUB_ADMIN_PRIVATE_KEY:" in line:
            redacted.append(re.sub(r": .*$", ": <redacted>", line))
        elif "MC_ALLFATHER_DEPLOYER_PRIVATE_KEY:" in line:
            redacted.append(re.sub(r": .*$", ": <redacted>", line))
        else:
            redacted.append(line)
    return "\n".join(redacted)


def super_context_args_for_head(args: argparse.Namespace, head: HeadNode) -> argparse.Namespace:
    context_args = fdb_tool().context_args_for_server(args, head.coolify_server)
    if not str(context_args.coolify_project_uuid or "").strip() and not str(context_args.coolify_project_name or "").strip():
        context_args.coolify_project_name = DEFAULT_COOLIFY_PROJECT_NAME
    if not str(context_args.coolify_environment_name or "").strip():
        context_args.coolify_environment_name = DEFAULT_SUPER_ENVIRONMENT
    return context_args


def resolve_super_context(client: Any, args: argparse.Namespace, head: HeadNode, tried: list[dict[str, Any]]) -> dict[str, Any]:
    profile = fdb_tool()._ProfileForContext("allfather-super")
    return hub_service_tool().resolve_coolify_context(client, profile, super_context_args_for_head(args, head), tried)


def super_service_payload(
    manifest: Mapping[str, Any],
    args: argparse.Namespace,
    *,
    context: Mapping[str, Any],
    hub_admin_private_key: str,
    deployer_private_key: str,
) -> dict[str, Any]:
    service_name = str(manifest.get("cell_id") or "")
    compose = render_super_node_compose(
        manifest,
        image=getattr(args, "super_image", DEFAULT_SUPER_IMAGE),
        hub_admin_private_key=hub_admin_private_key,
        deployer_private_key=deployer_private_key,
        publish_routes=bool(getattr(args, "publish_routes", False)),
    )
    payload = {
        "server_uuid": (context or {}).get("server_uuid") or "",
        "project_uuid": (context or {}).get("project_uuid") or "",
        "environment_name": (context or {}).get("environment_name") or getattr(args, "coolify_environment_name", "") or DEFAULT_SUPER_ENVIRONMENT,
        "environment_uuid": (context or {}).get("environment_uuid") or "",
        "name": service_name,
        "description": (
            f"Main Computer all-father super-node {service_name}. "
            "Contains hub, fdb, validator-rpc, guard, and bootstrap intent. Guard is private; Hub/RPC routes require cutover."
        ),
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "instant_deploy": False,
    }
    destination_uuid = destination_uuid_for_host(HeadNode(
        head_id=str(manifest.get("cell_id") or ""),
        service_name=service_name,
        coolify_server=str(manifest.get("coolify_server") or ""),
        slot=str(manifest.get("host_slot") or ""),
        guard_container_port=DEFAULT_SUPER_GUARD_CONTAINER_PORT,
        guard_host_port=0,
        guard_publish_host="",
        guard_url="",
        state_root=str(manifest.get("state_root") or ""),
        peers=(),
    ), args)
    if destination_uuid:
        payload["destination_uuid"] = destination_uuid
    return {key: value for key, value in payload.items() if value not in (None, "")}


def sync_super_node_service(
    client: Any,
    manifest: Mapping[str, Any],
    args: argparse.Namespace,
    context: Mapping[str, Any],
    tried: list[dict[str, Any]],
    *,
    hub_admin_private_key: str,
    deployer_private_key: str,
) -> tuple[str, str, dict[str, Any]]:
    service_name = str(manifest.get("cell_id") or "")
    service_uuid, existing = hub_service_tool().find_service(client, service_name=service_name, explicit_uuid="", tried=tried)
    compose = render_super_node_compose(
        manifest,
        image=getattr(args, "super_image", DEFAULT_SUPER_IMAGE),
        hub_admin_private_key=hub_admin_private_key,
        deployer_private_key=deployer_private_key,
        publish_routes=bool(getattr(args, "publish_routes", False)),
    )
    if service_uuid:
        fdb_tool().update_service(client, service_uuid, service_name, compose, tried)
        return service_uuid, "updated", existing
    payload = super_service_payload(
        manifest,
        args,
        context=context,
        hub_admin_private_key=hub_admin_private_key,
        deployer_private_key=deployer_private_key,
    )
    service_uuid = fdb_tool().create_service(client, payload, tried)
    return service_uuid, "created", existing


def redact_add_node_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    redacted = dict(payload)
    if "compose" in redacted:
        redacted["compose"] = redact_super_compose(str(redacted["compose"]))
    if "coolify_payload" in redacted and isinstance(redacted["coolify_payload"], Mapping):
        redacted["coolify_payload"] = redact_payload(redacted["coolify_payload"])
    return redacted



def generated_wallet_record(record: Any) -> bool:
    if not isinstance(record, Mapping):
        return False
    metadata = record.get("metadata")
    return isinstance(metadata, Mapping) and str(metadata.get("generated_by") or "") == PRIVATE_STATE_GENERATOR


def cleanup_private_state_for_remove_node(
    state: Mapping[str, Any],
    path: Path,
    network_key: str,
    *,
    remaining_count: int,
    dry_run: bool,
    keep_seed_material: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Clean generated seed material when removing the last node in a network.

    The all-father private file is not topology.  This cleanup only runs when the
    live Coolify inventory will have no super-nodes left for the network.  It
    removes first-node seed material that add-node generated so the next add can
    rebuild a pristine testnet/mainnet bootstrap.
    """

    network_key = clean_node_network_key(network_key)
    mutable_state = json.loads(json.dumps(dict(state)))
    result: dict[str, Any] = {
        "path": display_path(path),
        "dry_run": bool(dry_run),
        "written": False,
        "remaining_node_count": int(remaining_count),
        "pristine_requested": not bool(keep_seed_material),
        "seed_material_cleaned": False,
        "removed": [],
        "preserved": [],
        "note": "Private state cleanup only removes seed material when the network has no remaining super-nodes.",
    }

    if keep_seed_material:
        result["note"] = "Generated seed material was preserved because --keep-seed-material was passed."
        return mutable_state, result
    if remaining_count > 0:
        result["note"] = "Generated seed material was preserved because other super-nodes remain in the network."
        return mutable_state, result

    networks = mutable_state.get("networks")
    if not isinstance(networks, dict):
        return mutable_state, result
    network = networks.get(network_key)
    if not isinstance(network, dict):
        return mutable_state, result

    wallets = network.get("wallets")
    if isinstance(wallets, dict):
        for wallet_name in ("hub_admin", "deployer"):
            record = wallets.get(wallet_name)
            if generated_wallet_record(record):
                wallets.pop(wallet_name, None)
                result["removed"].append({"kind": "wallet_private_key", "wallet": wallet_name})
            elif isinstance(record, Mapping) and nonempty_private_value(record.get("private_key")):
                result["preserved"].append({"kind": "wallet_private_key", "wallet": wallet_name, "reason": "not generated by add-node"})
        if not wallets:
            network.pop("wallets", None)

    node_seed_material = network.get("node_seed_material")
    if isinstance(node_seed_material, dict):
        network.pop("node_seed_material", None)
        result["removed"].append({"kind": "node_seed_material", "network": network_key})

    fdb = network.get("foundationdb")
    if isinstance(fdb, dict):
        removed_fdb_keys = []
        for key in ("cluster_description", "cluster_id", "coordinator_policy", "reconfigure_after_join"):
            if key in fdb:
                fdb.pop(key, None)
                removed_fdb_keys.append(key)
        if removed_fdb_keys:
            result["removed"].append({"kind": "foundationdb_seed", "keys": removed_fdb_keys})
        if not fdb:
            network.pop("foundationdb", None)

    if not network:
        networks.pop(network_key, None)
        result["removed"].append({"kind": "network_seed", "network": network_key})
    if isinstance(networks, dict) and not networks:
        mutable_state.pop("networks", None)

    result["seed_material_cleaned"] = bool(result["removed"])
    if result["seed_material_cleaned"] and not dry_run:
        write_yaml_mapping(path, mutable_state)
        result["written"] = True
    return mutable_state, result


def delete_coolify_service(client: Any, *, service_uuid: str, service_name: str, tried: list[dict[str, Any]]) -> dict[str, Any]:
    if not service_uuid:
        raise AllfatherControlError(f"Cannot delete Coolify service {service_name!r}: missing service UUID.")

    encoded = urllib.parse.quote(str(service_uuid), safe="")
    attempts = [
        ("DELETE", f"/api/v1/services/{encoded}"),
        ("DELETE", f"/api/v1/services/{encoded}?deleteConfigurations=true"),
        ("POST", f"/api/v1/services/{encoded}/delete"),
    ]
    last_response: dict[str, Any] | None = None
    for method, path in attempts:
        response = client.request(method, path)
        response_dict = hub_service_tool().response_to_dict(response)
        tried.append(
            {
                "operation": "delete-super-service",
                "method": method,
                "path": path,
                "service_name": service_name,
                "service_uuid": service_uuid,
                "response": response_dict,
            }
        )
        last_response = response_dict
        if response.ok:
            return response_dict
        if response.status not in {400, 404, 405, 422}:
            raise AllfatherControlError(f"Coolify service delete failed with HTTP {response.status}: {response.body}")
    raise AllfatherControlError(f"Coolify service delete failed on all known endpoints for {service_name!r}: {last_response}")


def wait_for_coolify_service_absent(
    client: Any,
    *,
    service_name: str,
    tried: list[dict[str, Any]],
    wait_s: float,
    poll_s: float,
) -> dict[str, Any]:
    """Poll Coolify inventory until a deleted service no longer appears.

    Coolify can accept a delete request before the service list stops returning
    the deleted record.  Returning from remove-node during that window lets the
    next add-node see stale super1 and incorrectly allocate super2.  This helper
    makes removal settle before private seed cleanup and before operators re-add.
    """

    wait_s = max(0.0, float(wait_s))
    poll_s = max(0.1, float(poll_s))
    deadline = time.monotonic() + wait_s
    attempts = 0
    last_services: list[dict[str, Any]] = []
    last_matches: list[dict[str, Any]] = []

    while True:
        attempts += 1
        last_services = service_items_for_client(client, tried)
        last_matches = [item for item in last_services if service_name_from_item(item) == service_name]
        if not last_matches:
            return {
                "confirmed_absent": True,
                "attempt_count": attempts,
                "wait_s": wait_s,
                "service_name": service_name,
                "services": last_services,
            }
        if wait_s <= 0 or time.monotonic() >= deadline:
            return {
                "confirmed_absent": False,
                "attempt_count": attempts,
                "wait_s": wait_s,
                "service_name": service_name,
                "last_status": service_status_from_item(last_matches[0]),
                "last_uuid": service_uuid_from_item(last_matches[0]),
                "services": last_services,
            }
        sleep_for = min(poll_s, max(0.1, deadline - time.monotonic()))
        time.sleep(sleep_for)



def super_inventory_node_from_manifest(manifest: Mapping[str, Any], head: HeadNode) -> dict[str, Any]:
    """Build the expected live-inventory record for the newly-created super-node."""

    network_key = str(manifest.get("network_key") or "").strip()
    ordinal = int(manifest.get("ordinal") or 0)
    node = super_inventory_entry(network_key, head, ordinal, source="add-node-target")
    fdb_node = (manifest.get("foundationdb") or {}).get("new_node") if isinstance(manifest.get("foundationdb"), Mapping) else {}
    if isinstance(fdb_node, Mapping):
        node.update(dict(fdb_node))
    node["service_name"] = str(manifest.get("cell_id") or node.get("service_name") or "")
    node["components"] = dict(manifest.get("components") if isinstance(manifest.get("components"), Mapping) else node.get("components") or {})
    return node


def replace_or_append_super_inventory_node(nodes: Sequence[Mapping[str, Any]], target: Mapping[str, Any]) -> list[dict[str, Any]]:
    target_name = str(target.get("service_name") or "").strip()
    merged: list[dict[str, Any]] = []
    target_seen = False
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        copied = dict(node)
        if target_name and str(copied.get("service_name") or "") == target_name:
            combined = dict(target)
            combined.update(copied)
            merged.append(combined)
            target_seen = True
        else:
            merged.append(copied)
    if not target_seen:
        merged.append(dict(target))
    return merged


def _component_snapshot(functions: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = functions.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _component_status(functions: Mapping[str, Any], key: str) -> str:
    return str(_component_snapshot(functions, key).get("status") or "").strip()


def _component_ok(functions: Mapping[str, Any], key: str, statuses: set[str], *, running: bool | None = None) -> bool:
    component = _component_snapshot(functions, key)
    status = str(component.get("status") or "").strip()
    if status not in statuses:
        return False
    if running is not None and bool(component.get("running")) is not running:
        return False
    return True


def add_node_super_ready_check(internal_status: Mapping[str, Any] | None, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return whether the new super-node has reached the add-node completion signal.

    This is deliberately based on the remote Coolify-managed private probe output,
    not on local direct VPN calls.  The first node waits for contract deployment;
    later nodes require their node-scoped hub_admin but treat contracts as
    intentionally not required.
    """

    if not isinstance(internal_status, Mapping) or not internal_status:
        return {"ready": False, "terminal": False, "reason": "private super-node guard status not observed yet", "components": {}}
    if not bool(internal_status.get("observed")):
        return {"ready": False, "terminal": False, "reason": str(internal_status.get("reason") or "private super-node guard status not observed yet"), "components": {}}
    functions_value = internal_status.get("functions")
    functions = functions_value if isinstance(functions_value, Mapping) else {}
    components = {
        "foundationdb": _component_status(functions, "foundationdb"),
        "validator_rpc": _component_status(functions, "validator_rpc"),
        "hub": _component_status(functions, "hub"),
        "hub_admin": _component_status(functions, "hub_admin"),
        "contracts": _component_status(functions, "contracts"),
    }

    error = str(internal_status.get("error") or "").strip()
    if error and not functions:
        return {"ready": False, "terminal": "guard-startup-failed" in error, "reason": error, "components": components}

    fdb = _component_snapshot(functions, "foundationdb")
    if not (
        str(fdb.get("status") or "") == "running"
        and bool(fdb.get("running"))
        and bool(fdb.get("configured"))
        and bool(fdb.get("listening"))
    ):
        return {"ready": False, "terminal": False, "reason": f"foundationdb not ready: {components['foundationdb'] or 'missing'}", "components": components}

    validator = _component_snapshot(functions, "validator_rpc")
    if not (
        str(validator.get("status") or "") == "running"
        and bool(validator.get("running"))
        and bool(validator.get("rpc_http_ok"))
        and bool(validator.get("block_production_ok"))
    ):
        block_number = validator.get("block_number")
        if bool(validator.get("log_block_production_ok")) and not bool(validator.get("rpc_http_ok")):
            reason = f"validator_rpc produced blocks but JSON-RPC is not reachable: {components['validator_rpc'] or 'missing'}"
        else:
            reason = f"validator_rpc not producing blocks: {components['validator_rpc'] or 'missing'}"
        if block_number is not None:
            reason += f" block_number={block_number}"
        error = str(validator.get("block_production_error") or "").strip()
        if error:
            reason += f" error={error}"
        if bool(validator.get("shutdown_observed")):
            reason += " shutdown_observed=true"
        return {"ready": False, "terminal": False, "reason": reason, "components": components}

    hub = _component_snapshot(functions, "hub")
    if not (
        str(hub.get("status") or "") in {"running", "running-bootstrap-listener"}
        and bool(hub.get("running"))
        and bool(hub.get("health_ok"))
    ):
        return {"ready": False, "terminal": False, "reason": f"hub not ready: {components['hub'] or 'missing'}", "components": components}

    hub_admin = _component_snapshot(functions, "hub_admin")
    if not (
        str(hub_admin.get("status") or "") == "bootstrapped"
        and (bool(hub_admin.get("completed")) or bool(hub_admin.get("running")))
    ):
        terminal = str(hub_admin.get("status") or "") in {"missing-or-invalid-hub-admin-key"}
        return {"ready": False, "terminal": terminal, "reason": f"hub_admin not bootstrapped: {components['hub_admin'] or 'missing'}", "components": components}

    contracts_requested = bool((manifest.get("bootstrap") or {}).get("contracts_requested")) if isinstance(manifest.get("bootstrap"), Mapping) else False
    contracts = _component_snapshot(functions, "contracts")
    contracts_status = str(contracts.get("status") or "").strip()
    if contracts_requested:
        if not (contracts_status == "deployed" and (bool(contracts.get("completed")) or bool(contracts.get("running")))):
            terminal = contracts_status in {"missing-deployer-private-key", "deployment-failed"}
            return {"ready": False, "terminal": terminal, "reason": f"contracts not deployed: {contracts_status or 'missing'}", "components": components}
    elif contracts_status not in {"not-required-existing-network", "disabled"}:
        return {"ready": False, "terminal": False, "reason": f"contracts not settled: {contracts_status or 'missing'}", "components": components}

    return {
        "ready": True,
        "terminal": False,
        "reason": "super-node runtime ready",
        "components": components,
        "contracts_requested": contracts_requested,
    }


def wait_for_add_node_ready(
    plan: HeadPlan,
    head: HeadNode,
    manifest: Mapping[str, Any],
    client: Any,
    args: argparse.Namespace,
    context: Mapping[str, Any],
    tried: list[dict[str, Any]],
    *,
    service_uuid: str,
) -> dict[str, Any]:
    """Wait for add-node completion through Coolify inventory and private probes."""

    wait_s = max(0.0, float(getattr(args, "deploy_wait_s", DEFAULT_ADD_NODE_READY_WAIT_S) or 0.0))
    poll_s = max(0.5, float(getattr(args, "deploy_poll_s", DEFAULT_ADD_NODE_READY_POLL_S) or DEFAULT_ADD_NODE_READY_POLL_S))
    service_name = str(manifest.get("cell_id") or "").strip()
    if wait_s <= 0:
        return {
            "enabled": False,
            "ready": None,
            "reason": "disabled by --deploy-wait-s 0",
            "wait_s": wait_s,
            "service_name": service_name,
            "public_guard_routes": False,
            "ssh_used": False,
            "direct_vpn_used": False,
        }

    target_node = super_inventory_node_from_manifest(manifest, head)
    if service_uuid:
        target_node["service_uuid"] = service_uuid

    attempts = 0
    deadline = time.monotonic() + wait_s
    probe_service_uuid = ""
    probe_action = ""
    probe_deployed = False
    last_status = ""
    last_internal_status: dict[str, Any] = {}
    last_ready_check: dict[str, Any] = {"ready": False, "reason": "not checked yet"}
    last_probe_result: dict[str, Any] = {}
    last_probe_logs: dict[str, Any] = {}
    last_service_uuid = service_uuid
    last_log_signature = ""
    last_log_at = 0.0
    log_interval = operator_log_interval_s(args)

    while True:
        attempts += 1
        remaining = max(0.0, deadline - time.monotonic())
        services = service_items_for_client(client, tried)
        inventory = all_super_inventory_from_services(services, head)
        inventory = replace_or_append_super_inventory_node(inventory, target_node)
        target_inventory = next((node for node in inventory if str(node.get("service_name") or "") == service_name), dict(target_node))
        last_status = str(target_inventory.get("status") or "").strip()
        last_service_uuid = str(target_inventory.get("service_uuid") or last_service_uuid or "")

        if not probe_service_uuid:
            probe_service_uuid, probe_action, _probe_existing = sync_probe_service(
                client,
                plan,
                head,
                args,
                context,
                tried,
                super_inventory=inventory,
            )
            deploy_response = hub_service_tool().trigger_deploy_service(
                client,
                service_uuid=probe_service_uuid,
                force=True,
                tried=tried,
            )
            probe_deployed = bool(deploy_response)

        expected_targets = probe_target_records_for_plan(plan, super_inventory=inventory)
        probe_detail, probe_result = wait_for_probe_metadata_result(
            client,
            probe_service_uuid,
            tried,
            expected_targets=expected_targets,
            wait_s=min(max(0.0, remaining), poll_s),
        )
        last_probe_result = probe_result
        if not probe_result.get("ok") or not probe_result_covers_expected_super_targets(probe_result, expected_targets):
            probe_applications = application_records_from_service_detail(probe_detail)
            probe_application_uuid = probe_applications[0]["uuid"] if probe_applications else ""
            last_probe_logs = fetch_probe_logs(client, probe_service_uuid, tried, application_uuid=probe_application_uuid)
            log_result = latest_probe_result(last_probe_logs)
            if log_result.get("ok"):
                last_probe_result = log_result

        statuses = super_statuses_from_probe_result(last_probe_result)
        last_internal_status = statuses.get(service_name, {})
        last_ready_check = add_node_super_ready_check(last_internal_status, manifest)
        components = last_ready_check.get("components") if isinstance(last_ready_check.get("components"), Mapping) else {}
        component_summary = ",".join(f"{key}={value or '-'}" for key, value in components.items()) if components else "components=not-observed"
        signature = f"coolify={last_status or 'unknown'} reason={last_ready_check.get('reason') or 'not-ready'} {component_summary}"
        now = time.monotonic()
        if signature != last_log_signature or (now - last_log_at) >= log_interval:
            remaining_log = max(0.0, deadline - now)
            operator_log(args, f"node-wait: {signature}; remaining={remaining_log:.0f}s")
            last_log_signature = signature
            last_log_at = now
        if bool(last_ready_check.get("ready")):
            operator_log(args, f"node-wait: ready {service_name}")
            return {
                "enabled": True,
                "ready": True,
                "reason": last_ready_check.get("reason"),
                "wait_s": wait_s,
                "poll_s": poll_s,
                "attempt_count": attempts,
                "service_name": service_name,
                "service_uuid": last_service_uuid,
                "coolify_status": last_status,
                "probe_service_uuid": probe_service_uuid,
                "probe_action": probe_action,
                "probe_deployed": probe_deployed,
                "private_probe_observed": True,
                "readiness": last_ready_check,
                "internal_status": last_internal_status,
                "public_guard_routes": False,
                "ssh_used": False,
                "direct_vpn_used": False,
            }

        status_lower = last_status.lower()
        # During Coolify create/deploy/recreate, inventory can transiently report the
        # previous application status as "exited" even though the new image/container
        # is still building, unpacking, or starting.  Treat hard failures as terminal,
        # but let "exited" flow through the normal wait/timeout path so a slow cold
        # deploy does not return a false blocker while Docker is still working.
        terminal_service_state = any(token in status_lower for token in ("dead", "failed"))
        if bool(last_ready_check.get("terminal")) or terminal_service_state:
            operator_log(args, f"node-wait: terminal for {service_name}: {last_ready_check.get('reason') or last_status}")
            return {
                "enabled": True,
                "ready": False,
                "terminal": True,
                "reason": last_ready_check.get("reason") or f"service reached terminal status: {last_status}",
                "wait_s": wait_s,
                "poll_s": poll_s,
                "attempt_count": attempts,
                "service_name": service_name,
                "service_uuid": last_service_uuid,
                "coolify_status": last_status,
                "probe_service_uuid": probe_service_uuid,
                "probe_action": probe_action,
                "probe_deployed": probe_deployed,
                "private_probe_observed": bool(last_internal_status),
                "readiness": last_ready_check,
                "internal_status": last_internal_status,
                "public_guard_routes": False,
                "ssh_used": False,
                "direct_vpn_used": False,
            }

        if remaining <= 0:
            break

    operator_log(args, f"node-wait: timed out for {service_name}: {last_ready_check.get('reason') or 'not ready'}")
    return {
        "enabled": True,
        "ready": False,
        "terminal": False,
        "reason": last_ready_check.get("reason") or "timed out waiting for remote add-node readiness signal",
        "wait_s": wait_s,
        "poll_s": poll_s,
        "attempt_count": attempts,
        "service_name": service_name,
        "service_uuid": last_service_uuid,
        "coolify_status": last_status,
        "probe_service_uuid": probe_service_uuid,
        "probe_action": probe_action,
        "probe_deployed": probe_deployed,
        "private_probe_observed": bool(last_internal_status),
        "readiness": last_ready_check,
        "internal_status": last_internal_status,
        "last_probe_result_ok": bool(last_probe_result.get("ok")) if isinstance(last_probe_result, Mapping) else False,
        "last_probe_logs_ok": bool(last_probe_logs.get("ok")) if isinstance(last_probe_logs, Mapping) else False,
        "public_guard_routes": False,
        "ssh_used": False,
        "direct_vpn_used": False,
    }

def remove_node(plan: HeadPlan, args: argparse.Namespace) -> dict[str, Any]:
    network_key = clean_node_network_key(args.network)
    if network_key == "mainnet" and not getattr(args, "allow_mainnet", False):
        raise AllfatherControlError("Refusing to remove a mainnet node without --allow-mainnet.")
    head = choose_head_for_host(plan, args.host)
    operator_log(args, f"start: network={network_key} host={head.coolify_server} slot={head.slot}")
    private_state_path = repo_relative_path(args.private_state)
    state = load_yaml_mapping(private_state_path)

    tried: list[dict[str, Any]] = []
    service_uuid = ""
    target_status = ""
    services: list[dict[str, Any]]
    token_source = "dry-run:no-api"
    deleted = False
    delete_response: dict[str, Any] | None = None

    if getattr(args, "dry_run", False) and getattr(args, "existing_count", None) is not None:
        existing_count = max(0, int(args.existing_count))
        if existing_count < 1:
            raise AllfatherControlError(f"No {network_key} super-node exists on {head.coolify_server}; nothing to remove.")
        services = [{"name": super_service_name(network_key, head, item), "uuid": f"dry-run-{item}", "status": "dry-run"} for item in range(1, existing_count + 1)]
    else:
        client, token_source = fdb_tool().client_for_server(head.coolify_server, args)
        version = client.request("GET", "/api/v1/version")
        tried.append({"operation": "coolify-version", "response": hub_service_tool().response_to_dict(version)})
        if not version.ok:
            raise AllfatherControlError(f"Coolify API version check failed for {head.coolify_server!r} with HTTP {version.status}: {version.body}")
        services = service_items_for_client(client, tried)

    ordinals = existing_super_ordinals(services, network_key, head)
    if not ordinals:
        raise AllfatherControlError(f"No {network_key} super-node exists on {head.coolify_server}; nothing to remove.")
    ordinal = max(ordinals)
    service_name = super_service_name(network_key, head, ordinal)
    remaining_count = len(ordinals) - 1

    target_item: Mapping[str, Any] | None = None
    for item in services:
        if service_name_from_item(item) == service_name:
            target_item = item
            break
    if target_item is None:
        raise AllfatherControlError(f"Could not find Coolify service record for {service_name!r}.")

    service_uuid = service_uuid_from_item(target_item)
    target_status = service_status_from_item(target_item)
    if not service_uuid:
        raise AllfatherControlError(f"Cannot remove {service_name}: Coolify service UUID is missing.")

    delete_confirmed_absent = False
    delete_wait_result: dict[str, Any] | None = None

    if getattr(args, "dry_run", False):
        _new_state, private_state_updates = cleanup_private_state_for_remove_node(
            state,
            private_state_path,
            network_key,
            remaining_count=remaining_count,
            dry_run=True,
            keep_seed_material=bool(getattr(args, "keep_seed_material", False)),
        )
    else:
        delete_response = delete_coolify_service(client, service_uuid=service_uuid, service_name=service_name, tried=tried)
        deleted = True
        delete_wait_result = wait_for_coolify_service_absent(
            client,
            service_name=service_name,
            tried=tried,
            wait_s=float(getattr(args, "delete_wait_s", DEFAULT_REMOVE_DELETE_WAIT_S)),
            poll_s=float(getattr(args, "delete_poll_s", DEFAULT_REMOVE_DELETE_POLL_S)),
        )
        delete_confirmed_absent = bool(delete_wait_result.get("confirmed_absent"))
        if not delete_confirmed_absent:
            raise AllfatherControlError(
                f"Coolify accepted deletion for {service_name!r}, but the service is still present in live inventory "
                f"after {delete_wait_result.get('wait_s')}s. Private seed material was not cleaned. Do not run add-node yet; "
                "run remove-node again or rerun discover until the stale service disappears."
            )
        remaining_count = len(existing_super_ordinals(delete_wait_result.get("services", []), network_key, head))
        _new_state, private_state_updates = cleanup_private_state_for_remove_node(
            state,
            private_state_path,
            network_key,
            remaining_count=remaining_count,
            dry_run=False,
            keep_seed_material=bool(getattr(args, "keep_seed_material", False)),
        )

    result = {
        "ok": True,
        "operation": "remove-node",
        "network": network_key,
        "host": head.coolify_server,
        "host_slot": head.slot,
        "ordinal": ordinal,
        "service_name": service_name,
        "service_uuid": service_uuid,
        "service_status_before_remove": target_status,
        "removed_last_host_node": remaining_count == 0,
        "remaining_host_super_nodes": remaining_count,
        "network_pristine_after_remove": remaining_count == 0,
        "private_state_updates": private_state_updates,
        "service_deleted": deleted,
        "delete_confirmed_absent": delete_confirmed_absent,
        "delete_wait": (
            {key: value for key, value in (delete_wait_result or {}).items() if key != "services"}
            if (getattr(args, "verbose", False) or delete_wait_result)
            else None
        ),
        "delete_response": delete_response if getattr(args, "verbose", False) else ("<hidden; pass --verbose>" if delete_response else None),
        "public_guard_routes": False,
        "ssh_used": False,
        "direct_vpn_used": False,
        "private_state_is_topology": False,
        "token_source": token_source,
        "tried": tried if getattr(args, "verbose", False) else summarize_coolify_attempts(tried),
        "note": (
            "Removed the highest-numbered super-node for this network+host. "
            "No renumbering is performed. Generated seed material is cleaned only when this removal leaves the network empty."
        ),
    }
    return result


def add_node(plan: HeadPlan, args: argparse.Namespace) -> dict[str, Any]:
    network_key = clean_node_network_key(args.network)
    if network_key == "mainnet" and not getattr(args, "allow_mainnet", False):
        raise AllfatherControlError("Refusing to add a mainnet node without --allow-mainnet.")
    head = choose_head_for_host(plan, args.host)
    private_state_path = repo_relative_path(args.private_state)
    state = load_yaml_mapping(private_state_path)

    tried: list[dict[str, Any]] = []
    preflight: dict[str, Any] = {
        "enabled": False,
        "ready": None,
        "reason": "dry-run" if getattr(args, "dry_run", False) else "not run yet",
        "public_guard_routes": False,
        "ssh_used": False,
        "direct_vpn_used": False,
    }
    synced_service_predeploy: dict[str, Any] = {
        "enabled": False,
        "ready": None,
        "reason": "not run yet",
        "public_guard_routes": False,
        "ssh_used": False,
        "direct_vpn_used": False,
    }
    super_base: dict[str, Any] = {
        "enabled": False,
        "ready": None,
        "reason": "dry-run" if getattr(args, "dry_run", False) else "not run yet",
        "public_guard_routes": False,
        "ssh_used": False,
        "direct_vpn_used": False,
    }
    if getattr(args, "dry_run", False) and getattr(args, "existing_count", None) is not None:
        existing_count = max(0, int(args.existing_count))
        ordinal = existing_count + 1
        services = [{"name": super_service_name(network_key, head, item)} for item in range(1, existing_count + 1)]
        existing_nodes = [super_inventory_entry(network_key, head, item, source="dry-run-existing-count") for item in range(1, existing_count + 1)]
        token_source = "dry-run:no-api"
        context: dict[str, Any] = {}
        service_uuid = ""
        service_action = "planned"
        deployed = False
    else:
        client, token_source = fdb_tool().client_for_server(head.coolify_server, args)
        version = client.request("GET", "/api/v1/version")
        tried.append({"operation": "coolify-version", "response": hub_service_tool().response_to_dict(version)})
        if not version.ok:
            raise AllfatherControlError(f"Coolify API version check failed for {head.coolify_server!r} with HTTP {version.status}: {version.body}")
        services = service_items_for_client(client, tried)
        ordinals = require_contiguous_super_ordinals(existing_super_ordinals(services, network_key, head), network_key, head)
        context = resolve_super_context(client, args, head, tried)
        resume_existing = False
        resume_reason = ""
        resume_probe: dict[str, Any] = {}
        resume_ordinal = max(ordinals) if ordinals else 0
        if resume_ordinal > 0:
            resume_cell_id = super_service_name(network_key, head, resume_ordinal)
            existing_nodes_for_probe = existing_super_inventory_from_services(services, network_key, head)
            try:
                resume_probe_uuid, _resume_probe_action, _resume_probe_existing = sync_probe_service(
                    client,
                    plan,
                    head,
                    args,
                    context,
                    tried,
                    super_inventory=existing_nodes_for_probe,
                )
                if resume_probe_uuid:
                    hub_service_tool().trigger_deploy_service(
                        client,
                        service_uuid=resume_probe_uuid,
                        force=True,
                        tried=tried,
                    )
                    _probe_detail, probe_result = wait_for_probe_metadata_result(
                        client,
                        resume_probe_uuid,
                        tried,
                        expected_targets=probe_target_records_for_plan(plan, super_inventory=existing_nodes_for_probe),
                        wait_s=10.0,
                    )
                    statuses = super_statuses_from_probe_result(probe_result)
                    resume_status = statuses.get(resume_cell_id, {})
                    resume_manifest = {"bootstrap": {"contracts_requested": bool(resume_ordinal == 1 and not getattr(args, "no_contracts", False))}}
                    resume_check = add_node_super_ready_check(resume_status, resume_manifest)
                    resume_probe = {
                        "service_name": resume_cell_id,
                        "probe_service_uuid": resume_probe_uuid,
                        "observed": bool(resume_status),
                        "ready": bool(resume_check.get("ready")),
                        "terminal": bool(resume_check.get("terminal")),
                        "reason": resume_check.get("reason"),
                        "components": resume_check.get("components"),
                    }
                    if resume_status and not bool(resume_check.get("ready")):
                        resume_existing = True
                        resume_reason = str(resume_check.get("reason") or "existing super-node is not ready")
            except Exception as exc:
                resume_probe = {
                    "service_name": resume_cell_id,
                    "observed": False,
                    "ready": False,
                    "reason": f"{type(exc).__name__}: {exc}",
                }

        if resume_existing:
            ordinal = resume_ordinal
            planned_cell_id = super_service_name(network_key, head, ordinal)
            operator_log(args, f"resume: existing {planned_cell_id} is not ready ({resume_reason}); updating and redeploying it")
            existing_nodes = [
                super_inventory_entry(network_key, head, item, source="resume-existing-lower-ordinal")
                for item in ordinals
                if item < ordinal
            ]
            require_fdb_seed_for_existing_super_nodes(state, private_state_path, network_key, head, existing_nodes)
            preflight = {
                "enabled": True,
                "ready": True,
                "reason": f"resuming existing incomplete service {planned_cell_id}: {resume_reason}",
                "service_name": planned_cell_id,
                "expected_existing_ordinals": [item for item in ordinals if item < ordinal],
                "observed_ordinals": ordinals,
                "resume_probe": resume_probe,
                "public_guard_routes": False,
                "ssh_used": False,
                "direct_vpn_used": False,
                "terminal": False,
            }
        else:
            ordinal = (max(ordinals) + 1) if ordinals else 1
            planned_cell_id = super_service_name(network_key, head, ordinal)
            operator_log(args, f"preflight: waiting for clean slot {planned_cell_id}; existing_ordinals={ordinals or []}")
            preflight = wait_for_add_node_slot_preflight(
                client,
                network_key=network_key,
                head=head,
                service_name=planned_cell_id,
                expected_existing_ordinals=ordinals,
                tried=tried,
                wait_s=float(getattr(args, "preflight_wait_s", DEFAULT_ADD_NODE_PREFLIGHT_WAIT_S)),
                poll_s=float(getattr(args, "preflight_poll_s", DEFAULT_ADD_NODE_PREFLIGHT_POLL_S)),
                stable_s=float(getattr(args, "preflight_stable_s", DEFAULT_ADD_NODE_PREFLIGHT_STABLE_S)),
            )
            if not bool(preflight.get("ready")):
                operator_log(args, f"preflight: blocked for {planned_cell_id}: {preflight.get('reason')}")
                raise AllfatherControlError(
                    f"Host {head.coolify_server} is not ready to add {planned_cell_id}: {preflight.get('reason')}"
                )
            operator_log(args, f"preflight: clean slot confirmed for {planned_cell_id}")
            services = list(preflight.get("services") or services)
            ordinals = require_contiguous_super_ordinals(existing_super_ordinals(services, network_key, head), network_key, head)
            existing_nodes = existing_super_inventory_from_services(services, network_key, head)
            require_fdb_seed_for_existing_super_nodes(state, private_state_path, network_key, head, existing_nodes)
            ordinal = (max(ordinals) + 1) if ordinals else 1
            if super_service_name(network_key, head, ordinal) != planned_cell_id:
                raise AllfatherControlError(
                    f"Coolify inventory changed while preparing add-node on {head.coolify_server}: planned {planned_cell_id}, "
                    f"but the next service would now be {super_service_name(network_key, head, ordinal)}. Rerun discover/remove-node before add-node."
                )
        service_uuid = ""
        service_action = "planned"
        deployed = False

    cell_id = super_service_name(network_key, head, ordinal)
    state, wallets, private_state_updates = materialize_private_state_for_add_node(
        state,
        private_state_path,
        network_key,
        ordinal=ordinal,
        cell_id=cell_id,
        no_contracts=bool(getattr(args, "no_contracts", False)),
        dry_run=bool(getattr(args, "dry_run", False)),
    )

    manifest = super_manifest(
        network_key,
        head,
        ordinal,
        wallets=wallets,
        private_state=state,
        existing_nodes=existing_nodes,
        no_contracts=bool(getattr(args, "no_contracts", False)),
        publish_routes=bool(getattr(args, "publish_routes", False)),
    )
    manifest["deployment_id"] = "dry-run" if getattr(args, "dry_run", False) else new_super_deployment_id()
    hub_admin_key = wallet_private_key(wallets, "hub_admin")
    deployer_key = wallet_private_key(wallets, "deployer") if manifest["bootstrap"]["contracts_requested"] else ""
    compose = render_super_node_compose(
        manifest,
        image=getattr(args, "super_image", DEFAULT_SUPER_IMAGE),
        hub_admin_private_key=hub_admin_key,
        deployer_private_key=deployer_key,
        publish_routes=bool(getattr(args, "publish_routes", False)),
    )

    if not getattr(args, "dry_run", False):
        operator_log(args, f"plan: next node {cell_id}; base_image={getattr(args, 'super_image', DEFAULT_SUPER_IMAGE)}")
        if not getattr(args, "no_deploy", False):
            super_base = ensure_super_base_image(
                plan,
                head,
                client,
                args,
                context,
                tried,
            )
            if not bool(super_base.get("ready")):
                operator_log(args, f"base-image: blocked: {super_base.get('reason')}")
                raise AllfatherControlError(
                    f"Managed super base image is not ready on {head.coolify_server}: {super_base.get('reason')}"
                )
        operator_log(args, f"node-service: syncing Coolify service {cell_id}")
        service_uuid, service_action, _existing = sync_super_node_service(
            client,
            manifest,
            args,
            context,
            tried,
            hub_admin_private_key=hub_admin_key,
            deployer_private_key=deployer_key,
        )
        operator_log(args, f"node-service: {service_action} uuid={service_uuid or '<unknown>'}")
        synced_service_predeploy = require_synced_super_service_ready_for_deploy(
            client,
            service_name=manifest["cell_id"],
            service_uuid=service_uuid,
            tried=tried,
        )
        synced_service_predeploy["enabled"] = True
        synced_service_predeploy["ready"] = True
        synced_service_predeploy["reason"] = "single matching Coolify service is ready for deploy"
        deploy_wait: dict[str, Any] = {
            "enabled": False,
            "ready": None,
            "reason": "deployment was not triggered",
            "public_guard_routes": False,
            "ssh_used": False,
            "direct_vpn_used": False,
        }
        if not getattr(args, "no_deploy", False):
            force_node = bool(getattr(args, "force_deploy", False))
            operator_log(args, f"node-deploy: triggering deploy force={str(force_node).lower()}; waiting up to {float(getattr(args, 'deploy_wait_s', DEFAULT_ADD_NODE_READY_WAIT_S) or 0.0):.0f}s")
            hub_service_tool().trigger_deploy_service(
                client,
                service_uuid=service_uuid,
                force=force_node,
                tried=tried,
            )
            deployed = True
            deploy_wait = wait_for_add_node_ready(
                plan,
                head,
                manifest,
                client,
                args,
                context,
                tried,
                service_uuid=service_uuid,
            )
    else:
        deploy_wait = {
            "enabled": False,
            "ready": None,
            "reason": "dry-run",
            "public_guard_routes": False,
            "ssh_used": False,
            "direct_vpn_used": False,
        }

    add_node_ready = not bool(deploy_wait.get("enabled")) or bool(deploy_wait.get("ready"))
    result = {
        "ok": add_node_ready,
        "operation": "add-node",
        "network": network_key,
        "host": head.coolify_server,
        "host_slot": head.slot,
        "ordinal": ordinal,
        "service_name": manifest["cell_id"],
        "component_names": manifest["components"],
        "contracts_requested": bool(manifest["bootstrap"]["contracts_requested"]),
        "hub_admin_requested": True,
        "hub_admin_create_requested": bool(manifest["bootstrap"].get("hub_admin_create_requested")),
        "hub_admin_private_key_required_for_node_add": False,
        "hub_admin_scope": "node",
        "private_state_updates": private_state_updates,
        "fdb": {
            "action": manifest["foundationdb"]["action"],
            "cluster_file_present": bool(manifest["foundationdb"].get("cluster_file")),
            "coordinator_reconfigure_required": bool(manifest["foundationdb"].get("coordinator_reconfigure_required")),
            "existing_node_count": len(manifest["foundationdb"].get("existing_nodes") or []),
            "target_coordinator_count": len(manifest["foundationdb"].get("target_coordinators") or []),
        },
        "hub_public_cutover_deferred": True,
        "public_guard_routes": False,
        "public_routes_enabled": bool(getattr(args, "publish_routes", False)),
        "ssh_used": False,
        "direct_vpn_used": False,
        "private_state_is_topology": False,
        "service_uuid": service_uuid,
        "service_action": service_action,
        "deployed": deployed,
        "ready": add_node_ready,
        "reason": "" if add_node_ready else str(deploy_wait.get("reason") or "add-node deploy wait did not reach readiness"),
        "preflight": {key: value for key, value in preflight.items() if key != "services"},
        "super_base": super_base,
        "predeploy_service_check": synced_service_predeploy,
        "deploy_wait": deploy_wait,
        "token_source": token_source,
        "manifest": manifest,
        "compose": compose if getattr(args, "include_compose", False) else "<hidden; pass --include-compose>",
        "tried": tried if getattr(args, "verbose", False) else summarize_coolify_attempts(tried),
    }
    # Never print private keys. Even --verbose keeps secret-bearing compose lines redacted.
    return redact_add_node_payload(result)



def write_heads(plan: HeadPlan, out_dir: Path, *, dockerfile: str = DEFAULT_DOCKERFILE, image: str = DEFAULT_IMAGE) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    plan_path = out_dir / "allfather-heads-plan.json"
    plan_path.write_text(json.dumps(plan.to_dict(include_compose=False), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    written.append(plan_path)
    for head in plan.heads:
        manifest_path = out_dir / f"{head.head_id}.manifest.json"
        manifest_path.write_text(json.dumps(head_manifest(plan, head), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(manifest_path)
        compose_path = out_dir / f"{head.head_id}.compose.yml"
        compose_path.write_text(render_head_compose(plan, head, dockerfile=dockerfile, image=image) + "\n", encoding="utf-8")
        written.append(compose_path)
    return written


def add_remote_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--private-state",
        type=Path,
        default=DEFAULT_PRIVATE_STATE_PATH,
        help="All-father private state YAML. This seeds Coolify host access only; it is not treated as network topology.",
    )
    parser.add_argument("--set-coolify-url", action="append", default=[], help="Bind host to Coolify API URL. Format: <host>:<url>")
    parser.add_argument("--coolify-token", default="", help="One Coolify token for every host. Prefer private-state token references.")
    parser.add_argument("--coolify-token-env", default=fdb_tool().DEFAULT_TOKEN_ENV, help="Default env var containing a Coolify token.")
    parser.add_argument("--coolify-token-file", default="", help="Default file containing a Coolify token.")
    parser.add_argument("--set-coolify-token", action="append", default=[], help="Per-host token. Format: <host>:<token>")
    parser.add_argument("--set-coolify-token-env", action="append", default=[], help="Per-host token env var. Format: <host>:<ENV_VAR>")
    parser.add_argument("--set-coolify-token-file", action="append", default=[], help="Per-host token file. Format: <host>:<path>")

    parser.add_argument("--coolify-project-uuid", default="", help="Coolify project UUID used by all hosts unless overridden.")
    parser.add_argument(
        "--coolify-project-name",
        default=DEFAULT_COOLIFY_PROJECT_NAME,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--set-coolify-project-uuid", action="append", default=[], help="Per-host project UUID. Format: <host>:<uuid>")
    parser.add_argument("--coolify-environment-name", default=DEFAULT_CONTROL_ENVIRONMENT)
    parser.add_argument("--coolify-environment-uuid", default="")
    parser.add_argument("--set-coolify-environment-uuid", action="append", default=[], help="Per-host environment UUID. Format: <host>:<uuid>")
    parser.add_argument("--no-create-environment", action="store_true")
    parser.add_argument("--coolify-server-uuid", default="")
    parser.add_argument("--coolify-server-name", default="")
    parser.add_argument("--set-coolify-server-uuid", action="append", default=[], help="Per-host Coolify server UUID. Format: <host>:<uuid>")
    parser.add_argument("--set-coolify-server-name", action="append", default=[], help="Per-host Coolify server name. Format: <host>:<name>")
    parser.add_argument("--coolify-destination-uuid", default="")
    parser.add_argument("--set-coolify-destination-uuid", action="append", default=[], help="Per-host destination UUID. Format: <host>:<uuid>")
    parser.add_argument("--coolify-service-uuid", default="")
    parser.add_argument("--set-coolify-service-uuid", action="append", default=[], help="Per-host existing service UUID. Format: <host>:<uuid>")
    parser.add_argument("--coolify-timeout-s", type=float, default=30.0)
    parser.add_argument("--coolify-retries", type=int, default=2)
    parser.add_argument("--coolify-retry-sleep-s", type=float, default=1.0)


def add_common_head_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--guard-host-base", type=int, default=DEFAULT_GUARD_HOST_BASE)
    parser.add_argument("--guard-container-port", type=int, default=DEFAULT_GUARD_CONTAINER_PORT)
    parser.add_argument("--state-root-prefix", default=DEFAULT_STATE_ROOT_PREFIX)
    parser.add_argument("--dockerfile", default=DEFAULT_DOCKERFILE)
    parser.add_argument("--image", default=DEFAULT_IMAGE)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap and query the all-father control plane.", allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan-heads", help="Render the guard/head control-plane plan without deploying.")
    add_remote_args(plan_parser)
    add_common_head_args(plan_parser)
    plan_parser.add_argument("--include-compose", action="store_true", help="Include rendered compose in the JSON output.")

    write_parser = subparsers.add_parser("write-heads", help="Write guard/head compose and manifest files.")
    add_remote_args(write_parser)
    add_common_head_args(write_parser)
    write_parser.add_argument("--out", type=Path, default=REPO_ROOT / "runtime" / "coolify-allfather" / "heads")

    boot_parser = subparsers.add_parser("bootstrap-heads", help="Create/update the guard/head services on every Coolify host.")
    add_remote_args(boot_parser)
    add_common_head_args(boot_parser)
    boot_parser.add_argument("--dry-run", action="store_true", help="Render remote payloads without calling Coolify.")
    boot_parser.add_argument("--include-compose", action="store_true", help="Include rendered compose in the dry-run JSON.")
    boot_parser.add_argument("--no-deploy", action="store_true", help="Create/update services but do not trigger deploy.")
    boot_parser.add_argument("--force-deploy", action="store_true", help="Force Coolify deployment after service sync.")

    discover_parser = subparsers.add_parser("discover", help="Query live all-father heads and merge the visible topology.")
    add_remote_args(discover_parser)
    add_common_head_args(discover_parser)
    discover_parser.add_argument("--timeout-s", type=float, default=5.0, help="Reserved for direct/debug transports; normal discovery uses Coolify probes.")
    discover_parser.add_argument("--dry-run", action="store_true", help="Render the probe payloads without changing Coolify.")
    discover_parser.add_argument("--include-probe-compose", action="store_true", help="Include rendered probe compose in dry-run output.")
    discover_parser.add_argument("--probe-image", default=DEFAULT_IMAGE)
    discover_parser.add_argument("--probe-container-port", type=int, default=DEFAULT_PROBE_CONTAINER_PORT)
    discover_parser.add_argument("--probe-interval-s", type=float, default=DEFAULT_PROBE_INTERVAL_S)
    discover_parser.add_argument("--probe-result-wait-s", type=float, default=20.0, help="Seconds to wait for the private probe callback to include newly discovered super-node targets.")
    discover_parser.add_argument("--no-sync-probe", action="store_true", help="Do not create/update the long-running Coolify probe service.")
    discover_parser.add_argument("--no-deploy-probe", action="store_true", help="Create/update the probe service but do not trigger deployment.")
    discover_parser.add_argument("--force-deploy-probe", action="store_true", help="Force deployment of the probe service after sync.")
    discover_parser.add_argument(
        "--allow-direct-vpn",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    add_node_parser = subparsers.add_parser("add-node", help="Add one all-father super-node to mainnet or testnet on one Coolify host.")
    add_remote_args(add_node_parser)
    add_common_head_args(add_node_parser)
    add_node_parser.add_argument("network", choices=SUPPORTED_NODE_NETWORKS, help="Network to grow: testnet or mainnet.")
    add_node_parser.add_argument("--host", required=True, help="Coolify host name or all-father host slot, for example coolify-a or A.")
    add_node_parser.add_argument("--allow-mainnet", action="store_true", help="Required before adding a mainnet super-node.")
    add_node_parser.add_argument("--dry-run", action="store_true", help="Plan and render the next super-node without creating/updating Coolify.")
    add_node_parser.add_argument("--existing-count", type=int, default=None, help=argparse.SUPPRESS)
    add_node_parser.add_argument("--no-contracts", action="store_true", help="Do not request first-node contract bootstrap.")
    add_node_parser.add_argument("--publish-routes", action="store_true", help="Publish Hub/RPC Traefik routes immediately. Default defers public cutover.")
    add_node_parser.add_argument("--include-compose", action="store_true", help="Include rendered compose with private keys redacted.")
    add_node_parser.add_argument("--super-image", default=DEFAULT_SUPER_IMAGE, help="Managed all-father super-node dependency base image used by the generated per-node image.")
    add_node_parser.add_argument("--super-base-source-image", default=DEFAULT_SUPER_BASE_SOURCE_IMAGE, help="Besu/QBFT source image used by the managed Coolify super-base-builder service.")
    add_node_parser.add_argument("--super-base-builder-image", default=DEFAULT_SUPER_BASE_BUILDER_IMAGE, help=argparse.SUPPRESS)
    add_node_parser.add_argument("--super-base-wait-s", type=float, default=DEFAULT_ADD_NODE_READY_WAIT_S, help="Seconds to wait for the managed Coolify super-base-builder service to make the dependency image available.")
    add_node_parser.add_argument("--force-super-base-rebuild", action="store_true", help="Force the managed super-base-builder service to rebuild the dependency base image.")
    add_node_parser.add_argument("--no-super-base-ensure", action="store_true", help="Skip the managed super-base-builder check. Use only when the dependency image is already guaranteed on the host.")
    add_node_parser.add_argument("--no-deploy", action="store_true", help="Create/update service but do not trigger deploy.")
    add_node_parser.add_argument("--force-deploy", action="store_true", help="Force Coolify deployment after service sync.")
    add_node_parser.add_argument("--preflight-wait-s", type=float, default=DEFAULT_ADD_NODE_PREFLIGHT_WAIT_S, help="Seconds to wait for Coolify inventory to show the target super-node service slot is stably clean before add-node creates or deploys.")
    add_node_parser.add_argument("--preflight-poll-s", type=float, default=DEFAULT_ADD_NODE_PREFLIGHT_POLL_S, help=argparse.SUPPRESS)
    add_node_parser.add_argument("--preflight-stable-s", type=float, default=DEFAULT_ADD_NODE_PREFLIGHT_STABLE_S, help=argparse.SUPPRESS)
    add_node_parser.add_argument("--deploy-wait-s", type=float, default=DEFAULT_ADD_NODE_READY_WAIT_S, help="Seconds to wait remotely for the new super-node guard/FDB/RPC/Hub/hub_admin/contracts readiness signal after deploy. Set 0 to return immediately after triggering deploy.")
    add_node_parser.add_argument("--deploy-poll-s", type=float, default=DEFAULT_ADD_NODE_READY_POLL_S, help=argparse.SUPPRESS)

    remove_node_parser = subparsers.add_parser("remove-node", help="Remove the last all-father super-node from mainnet or testnet on one Coolify host.")
    add_remote_args(remove_node_parser)
    add_common_head_args(remove_node_parser)
    remove_node_parser.add_argument("network", choices=SUPPORTED_NODE_NETWORKS, help="Network to shrink: testnet or mainnet.")
    remove_node_parser.add_argument("--host", required=True, help="Coolify host name or all-father host slot, for example coolify-a or A.")
    remove_node_parser.add_argument("--allow-mainnet", action="store_true", help="Required before removing a mainnet super-node.")
    remove_node_parser.add_argument("--dry-run", action="store_true", help="Plan the removal without deleting the Coolify service or writing private state.")
    remove_node_parser.add_argument("--existing-count", type=int, default=None, help=argparse.SUPPRESS)
    remove_node_parser.add_argument("--delete-wait-s", type=float, default=DEFAULT_REMOVE_DELETE_WAIT_S, help="Seconds to wait for Coolify inventory to stop reporting the deleted service before cleaning seed material.")
    remove_node_parser.add_argument("--delete-poll-s", type=float, default=DEFAULT_REMOVE_DELETE_POLL_S, help=argparse.SUPPRESS)
    remove_node_parser.add_argument("--keep-seed-material", action="store_true", help="Do not clean generated first-node keys/FDB identity when the network becomes empty.")

    for subparser in (plan_parser, write_parser, boot_parser, discover_parser, add_node_parser, remove_node_parser):
        subparser.add_argument("--json", action="store_true", help="Print detailed compact JSON. Default discover output is an operator summary.")
        subparser.add_argument("--verbose", action="store_true", help="Include raw Coolify diagnostics and large API records in JSON output.")
        subparser.add_argument("--quiet", action="store_true", help="Suppress operator progress logs on stderr.")
        subparser.add_argument("--operator-log-interval-s", type=float, default=15.0, help=argparse.SUPPRESS)

    return parser.parse_args(argv)


def build_plan_from_args(args: argparse.Namespace) -> HeadPlan:
    private_state_path = repo_relative_path(args.private_state)
    hosts = load_private_hosts(private_state_path)
    return build_head_plan(
        hosts,
        private_state_path=private_state_path,
        guard_host_base=args.guard_host_base,
        guard_container_port=args.guard_container_port,
        state_root_prefix=args.state_root_prefix,
        image=args.image,
    )


def print_json(payload: Mapping[str, Any]) -> None:
    raw = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    for offset in range(0, len(raw), 16384):
        chunk = raw[offset : offset + 16384]
        while True:
            try:
                sys.stdout.write(chunk)
                sys.stdout.flush()
                break
            except BlockingIOError:
                time.sleep(0.01)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan = build_plan_from_args(args)
        if args.command == "plan-heads":
            print_json(plan.to_dict(include_compose=getattr(args, "include_compose", False), dockerfile=args.dockerfile, image=args.image))
            return 0
        if args.command == "write-heads":
            written = write_heads(plan, args.out, dockerfile=args.dockerfile, image=args.image)
            print_json({"written": [str(path) for path in written]})
            return 0
        if args.command == "bootstrap-heads":
            print_json(apply_bootstrap_heads(plan, args))
            return 0
        if args.command == "discover":
            payload = discover_from_heads(plan, args)
            if getattr(args, "json", False) or getattr(args, "verbose", False) or getattr(args, "dry_run", False):
                print_json(payload)
            else:
                print_json(compact_discover_for_operator(payload))
            return 0
        if args.command == "add-node":
            payload = add_node(plan, args)
            print_json(payload)
            return 0 if bool(payload.get("ok", True)) else 1
        if args.command == "remove-node":
            print_json(remove_node(plan, args))
            return 0
        raise AllfatherControlError(f"Unsupported command: {args.command}")
    except Exception as exc:
        print_json({"ok": False, "error": str(exc), "type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
