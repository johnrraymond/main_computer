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
import re
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
DEFAULT_IMAGE = "main-computer-allfather-head"
DEFAULT_CONTROL_ENVIRONMENT = "allfather-control"
DEFAULT_COOLIFY_PROJECT_NAME = "My first project"
DEFAULT_GUARD_CONTAINER_PORT = 41414
DEFAULT_GUARD_HOST_BASE = 41400
DEFAULT_STATE_ROOT_PREFIX = "/data/main-computer/allfather/control-plane"
PRIVATE_PLACEHOLDER_RE = re.compile(r"^\s*(?:<[^>]+>|TODO|TBD|CHANGEME|REPLACE_ME)\s*$", re.IGNORECASE)


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
                "service_name": safe_id(f"{image}-{host.name}", field="service_name"),
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


def yaml_quote(value: Any) -> str:
    return json.dumps(str(value))


def render_head_compose(
    plan: HeadPlan,
    head: HeadNode,
    *,
    dockerfile: str = DEFAULT_DOCKERFILE,
    image: str = DEFAULT_IMAGE,
) -> str:
    manifest = head_manifest(plan, head)
    port_spec = (
        f"{head.guard_publish_host}:{head.guard_host_port}:{head.guard_container_port}/tcp"
        if head.guard_publish_host
        else f"{head.guard_host_port}:{head.guard_container_port}/tcp"
    )
    parent = str(Path(head.state_root).parent).replace("\\", "/")
    lines = [
        f"name: {head.service_name}",
        "",
        "services:",
        f"  {head.service_name}:",
        "    build:",
        "      context: .",
        f"      dockerfile: {yaml_quote(dockerfile)}",
        f"    image: {yaml_quote(f'{image}:latest')}",
        "    restart: unless-stopped",
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
        f"        - {yaml_quote(f'python /opt/main-computer/allfather/healthcheck.py http://127.0.0.1:{head.guard_container_port}/healthz')}",
        "      interval: 10s",
        "      timeout: 5s",
        "      start_period: 10s",
        "      retries: 6",
        "",
    ]
    return "\n".join(lines)


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


def discover_from_heads(plan: HeadPlan, args: argparse.Namespace) -> dict[str, Any]:
    heads: list[dict[str, Any]] = []
    networks: dict[str, list[dict[str, Any]]] = {}
    for head in plan.heads:
        base = head.guard_url.rstrip("/")
        identity = fetch_json(f"{base}/identity", timeout_s=args.timeout_s)
        topology = fetch_json(f"{base}/topology", timeout_s=args.timeout_s)
        status = fetch_json(f"{base}/status", timeout_s=args.timeout_s)
        record = {
            "head_id": head.head_id,
            "server": head.coolify_server,
            "guard_url": head.guard_url,
            "identity": identity,
            "topology": topology,
            "status": status,
        }
        heads.append(record)

        for payload in (identity, topology, status):
            network_key = str(payload.get("network_key") or "").strip()
            if network_key and network_key not in {"control-plane", "allfather-control"}:
                networks.setdefault(network_key, []).append(record)
    return {
        "ok": True,
        "operation": "discover",
        "topology_source": "live-guard-http",
        "heads": heads,
        "networks": networks,
        "guardrails": plan.guardrails,
    }


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
    discover_parser.add_argument("--timeout-s", type=float, default=5.0)

    for subparser in (plan_parser, write_parser, boot_parser, discover_parser):
        subparser.add_argument("--json", action="store_true", help="Print JSON output. Human output is used only for write-heads.")

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
            print_json(discover_from_heads(plan, args))
            return 0
        raise AllfatherControlError(f"Unsupported command: {args.command}")
    except Exception as exc:
        print_json({"ok": False, "error": str(exc), "type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
