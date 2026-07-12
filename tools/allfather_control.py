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
DEFAULT_PROBE_SERVICE_PREFIX = "allfather-control-probe"
PROBE_CALLBACK_MARKER = "ALLFATHER_PROBE_RESULT_B64:"
DEFAULT_STATE_ROOT_PREFIX = "/data/main-computer/allfather/control-plane"
DEFAULT_SUPER_IMAGE = "python:3.12-slim"
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
    lines.extend(f"        {line}" for line in command_script.splitlines())
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


def probe_targets_b64(plan: HeadPlan) -> str:
    return base64.b64encode(json.dumps(probe_targets_for_plan(plan), sort_keys=True).encode("utf-8")).decode("ascii")


def probe_server_command_script() -> str:
    return r"""
import base64
import hashlib
import json
import os
import threading
import time
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
    for base in TARGETS:
        clean = str(base or "").rstrip("/")
        if not clean:
            continue
        identity = fetch_json(f"{clean}/identity")
        topology = fetch_json(f"{clean}/topology")
        status = fetch_json(f"{clean}/status")
        healthz = fetch_json(f"{clean}/healthz")
        results.append(
            {
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
                "guard_url": target.get("guard_url"),
                "ok": bool(target.get("ok")),
                "identity_ok": bool(identity.get("ok")),
                "topology_ok": bool(topology.get("ok")),
                "status_ok": bool(status.get("ok")),
                "healthz_ok": bool(healthz.get("ok")),
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
            f"      MC_ALLFATHER_PROBE_TARGETS_B64: {yaml_quote(probe_targets_b64(plan))}",
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
) -> tuple[str, str, dict[str, Any]]:
    service_name = probe_service_name(head)
    service_uuid, existing = hub_service_tool().find_service(client, service_name=service_name, explicit_uuid="", tried=tried)
    if service_uuid:
        compose = render_probe_compose_for_client(plan, head, args, client, callback_service_uuid=service_uuid)
        fdb_tool().update_service(client, service_uuid, service_name, compose, tried)
        return service_uuid, "updated", existing

    service_uuid = fdb_tool().create_service(client, probe_service_payload(plan, head, args, context=context), tried)
    # A newly-created probe cannot know its own Coolify service UUID until after
    # creation. Patch the compose once more so the long-running private probe can
    # publish results back into its own service metadata.
    compose = render_probe_compose_for_client(plan, head, args, client, callback_service_uuid=service_uuid)
    fdb_tool().update_service(client, service_uuid, service_name, compose, tried)
    return service_uuid, "created", existing


def application_records_from_service_detail(detail: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return compact application records embedded in a Coolify service detail payload."""

    body = detail.get("body") if isinstance(detail, Mapping) else {}
    if not isinstance(body, Mapping):
        return []
    applications = body.get("applications")
    if not isinstance(applications, list):
        return []
    records: list[dict[str, str]] = []
    for app in applications:
        if not isinstance(app, Mapping):
            continue
        uuid = str(app.get("uuid") or "").strip()
        name = str(app.get("name") or "").strip()
        if uuid:
            records.append({"uuid": uuid, "name": name})
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
                ("get-allfather-probe-application-logs", f"/api/v1/applications/{quoted_app}/logs"),
            ]
        )
    if service_uuid:
        quoted_service = urllib.parse.quote(service_uuid)
        paths.extend(
            [
                ("get-allfather-probe-service-logs-fallback", f"/api/v1/services/{quoted_service}/logs"),
                ("get-allfather-probe-service-logs-fallback", f"/api/v1/services/{quoted_service}/logs?tail=300"),
                ("get-allfather-probe-service-logs-fallback", f"/api/v1/services/{quoted_service}/application/logs"),
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

    if getattr(args, "dry_run", False):
        return {
            "ok": True,
            "dry_run": True,
            "method": "coolify-patch-probe",
            "token_source": "",
            "head_service_name": head.service_name,
            "probe_service_name": probe_service_name(head),
            "peer_guard_url": head.guard_url,
            "probe_targets": probe_targets_for_plan(plan),
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
            probe_service_uuid, action, probe_existing = sync_probe_service(client, plan, head, args, context, tried)
            if not getattr(args, "no_deploy_probe", False):
                deploy_result = hub_service_tool().trigger_deploy_service(
                    client,
                    service_uuid=probe_service_uuid,
                    force=getattr(args, "force_deploy_probe", False),
                    tried=tried,
                )
            else:
                deploy_result = None
            probe_detail = fetch_service_detail(client, probe_service_uuid, tried)
            probe_applications = application_records_from_service_detail(probe_detail)
            probe_application_uuid = probe_applications[0]["uuid"] if probe_applications else ""
            probe_result = probe_result_from_service_metadata(probe_detail)
            if not probe_result.get("ok"):
                probe_logs = fetch_probe_logs(client, probe_service_uuid, tried, application_uuid=probe_application_uuid)
                probe_result = latest_probe_result(probe_logs)
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
                "probe_targets": probe_targets_for_plan(plan),
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
            "probe_targets": probe_targets_for_plan(plan),
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
    networks: dict[str, list[dict[str, Any]]] = {}
    probe_synced_count = 0
    probe_result_count = 0
    coolify_head_count = 0
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

        for network_key, items in networks_from_probe_result(probe_result if isinstance(probe_result, Mapping) else {}, record).items():
            networks.setdefault(network_key, []).extend(items)

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
    fdb = ensure_mapping_child(network, "foundationdb")
    if not nonempty_private_value(fdb.get("cluster_description")):
        fdb["cluster_description"] = f"main-computer-{clean_node_network_key(network_key)}-allfather"
        generated.append({"kind": "fdb_cluster_description", "network": clean_node_network_key(network_key)})
    if not nonempty_private_value(fdb.get("cluster_id")):
        fdb["cluster_id"] = generated_cluster_id()
        generated.append({"kind": "fdb_cluster_id", "network": clean_node_network_key(network_key)})
    fdb.setdefault("coordinator_policy", "first-node-then-expand")
    fdb.setdefault("reconfigure_after_join", True)
    return fdb


def materialize_private_state_for_add_node(
    state: Mapping[str, Any],
    path: Path,
    network_key: str,
    *,
    ordinal: int,
    no_contracts: bool,
    dry_run: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Ensure add-node has local bootstrap secrets without treating state as topology.

    Missing ``hub_admin`` and first-node ``deployer`` keys are generated into
    ``all_father.private.yaml``.  The file remains a seed/control file: it stores
    secrets and FDB cluster identity, not the list of running super-nodes.
    """

    network_key = clean_node_network_key(network_key)
    mutable_state = json.loads(json.dumps(dict(state)))
    network = ensure_network_private_state(mutable_state, network_key)
    wallets = ensure_mapping_child(network, "wallets")
    generated: list[dict[str, Any]] = []

    materialize_wallet_key(
        wallets,
        "hub_admin",
        reason=f"{network_key} all-father hub_admin bootstrap",
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
    return mutable_state, wallets_for_network, {
        "path": display_path(path),
        "written": bool(generated and not dry_run),
        "dry_run": bool(dry_run),
        "generated": generated,
        "wallets_generated": [item["wallet"] for item in generated if item.get("kind") == "wallet_private_key"],
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
    description = nonempty_private_value(fdb.get("cluster_description")) or f"main-computer-{network_key}-allfather"
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
        "fdb_endpoint": f"{head.guard_publish_host}:{super_host_port(network_key, head, ordinal, testnet_base=DEFAULT_TESTNET_FDB_BASE, mainnet_base=DEFAULT_MAINNET_FDB_BASE)}",
        "fdb_host_port": super_host_port(network_key, head, ordinal, testnet_base=DEFAULT_TESTNET_FDB_BASE, mainnet_base=DEFAULT_MAINNET_FDB_BASE),
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


def fdb_cluster_file(description: str, cluster_id: str, coordinators: Sequence[str]) -> str:
    clean = [str(item).strip() for item in coordinators if str(item).strip()]
    return f"{description}:{cluster_id}@{','.join(clean)}"


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
    ordinals = existing_super_ordinals(services, network_key, head)
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
    return r"""
import base64
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

manifest = json.loads(base64.b64decode(os.environ["MC_ALLFATHER_SUPER_MANIFEST_B64"]).decode("utf-8"))
port = int(os.environ.get("MC_ALLFATHER_GUARD_PORT", "41414"))

def payload(status: str = "running"):
    return {
        "ok": True,
        "service": "main-computer-allfather-super-node",
        "status": status,
        "network_key": manifest.get("network_key"),
        "cell_id": manifest.get("cell_id"),
        "coolify_server": manifest.get("coolify_server"),
        "ordinal": manifest.get("ordinal"),
        "components": manifest.get("components"),
        "desired_counts": manifest.get("desired_counts"),
        "bootstrap": manifest.get("bootstrap"),
        "public_routes": manifest.get("public_routes"),
        "guardrails": manifest.get("guardrails"),
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
            self._send(200, {"ok": True, "cell_id": manifest.get("cell_id")})
        elif path == "/identity":
            self._send(200, payload())
        elif path == "/topology":
            self._send(200, {"ok": True, "network_key": manifest.get("network_key"), "cell_id": manifest.get("cell_id"), "topology": {"source": "self-manifest", "super_node": manifest}})
        elif path in {"/status", "/processes"}:
            self._send(200, payload())
        else:
            self._send(404, {"ok": False, "error": "not-found", "path": path})

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

print(f"all-father super-node guard listening on 0.0.0.0:{port}", flush=True)
ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
""".strip()


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
    guard_host = ports.get("guard_host")
    fdb_host = ports.get("fdb_host")
    p2p_host = ports.get("p2p_host")
    lines = [
        f"name: {service_name}",
        "",
        "services:",
        f"  {service_name}:",
        f"    image: {yaml_quote(image)}",
        "    restart: unless-stopped",
        "    entrypoint: []",
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
            f"      MC_ALLFATHER_SUPER_NODE: {yaml_quote('1')}",
            f"      MC_ALLFATHER_NETWORK: {yaml_quote(str(manifest.get('network_key') or ''))}",
            f"      MC_ALLFATHER_CELL_ID: {yaml_quote(service_name)}",
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
            f"      - {yaml_quote(f'{guard_host}:{ports.get('guard_container')}/tcp')}",
            f"      - {yaml_quote(f'{fdb_host}:{ports.get('fdb_container')}/tcp')}",
            f"      - {yaml_quote(f'{p2p_host}:{ports.get('p2p_container')}/tcp')}",
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
            f"traefik.http.services.{hub}-svc.loadbalancer.server.port={ports.get('hub_container')}",
            f"traefik.http.routers.{rpc}.rule=Host(`{rpc}.greatlibrary.io`)",
            f"traefik.http.routers.{rpc}.entryPoints=https",
            f"traefik.http.routers.{rpc}.tls=true",
            f"traefik.http.services.{rpc}-svc.loadbalancer.server.port={ports.get('rpc_container')}",
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


def add_node(plan: HeadPlan, args: argparse.Namespace) -> dict[str, Any]:
    network_key = clean_node_network_key(args.network)
    if network_key == "mainnet" and not getattr(args, "allow_mainnet", False):
        raise AllfatherControlError("Refusing to add a mainnet node without --allow-mainnet.")
    head = choose_head_for_host(plan, args.host)
    private_state_path = repo_relative_path(args.private_state)
    state = load_yaml_mapping(private_state_path)

    tried: list[dict[str, Any]] = []
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
        existing_nodes = existing_super_inventory_from_services(services, network_key, head)
        ordinal = next_super_ordinal_from_inventory(services, network_key, head)
        context = resolve_super_context(client, args, head, tried)
        service_uuid = ""
        service_action = "planned"
        deployed = False

    state, wallets, private_state_updates = materialize_private_state_for_add_node(
        state,
        private_state_path,
        network_key,
        ordinal=ordinal,
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
        service_uuid, service_action, _existing = sync_super_node_service(
            client,
            manifest,
            args,
            context,
            tried,
            hub_admin_private_key=hub_admin_key,
            deployer_private_key=deployer_key,
        )
        if not getattr(args, "no_deploy", False):
            hub_service_tool().trigger_deploy_service(
                client,
                service_uuid=service_uuid,
                force=getattr(args, "force_deploy", False),
                tried=tried,
            )
            deployed = True

    result = {
        "ok": True,
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
    add_node_parser.add_argument("--super-image", default=DEFAULT_SUPER_IMAGE, help="Image used for the initial super-node bootstrap container.")
    add_node_parser.add_argument("--no-deploy", action="store_true", help="Create/update service but do not trigger deploy.")
    add_node_parser.add_argument("--force-deploy", action="store_true", help="Force Coolify deployment after service sync.")

    for subparser in (plan_parser, write_parser, boot_parser, discover_parser, add_node_parser):
        subparser.add_argument("--json", action="store_true", help="Print detailed compact JSON. Default discover output is an operator summary.")
        subparser.add_argument("--verbose", action="store_true", help="Include raw Coolify diagnostics and large API records in JSON output.")

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
    print(json.dumps(payload, indent=2, sort_keys=True))


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
            print_json(add_node(plan, args))
            return 0
        raise AllfatherControlError(f"Unsupported command: {args.command}")
    except Exception as exc:
        print_json({"ok": False, "error": str(exc), "type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
