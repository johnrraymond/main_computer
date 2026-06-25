#!/usr/bin/env python3
"""Deploy the shared FoundationDB layer for a multi-hub Coolify topology.

This tool intentionally manages only the FoundationDB cluster layer described by
``deploy/hub-topology/testnet-coolify-deployment.json``.  It creates one Coolify
Service per symbolic Coolify host.  Each Service starts the FDB processes that
belong on that host, writes the shared ``fdb.cluster`` file to the configured
host/container path, and runs an idempotent ``fdbcli configure new ...`` loop.

Coolify API URLs and tokens are supplied at runtime, so the committed placement
file can use stable names such as ``coolify-a`` and ``coolify-b``.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import ipaddress
import json
import os
import re
import shlex
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
HUB_SERVICE_TOOL_PATH = Path(__file__).resolve().with_name("coolify_hub_service.py")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_hub_service_module() -> Any:
    if "coolify_hub_service" in sys.modules:
        return sys.modules["coolify_hub_service"]
    spec = importlib.util.spec_from_file_location("coolify_hub_service", HUB_SERVICE_TOOL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {HUB_SERVICE_TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hub_tool = _load_hub_service_module()

CoolifyClient = hub_tool.CoolifyClient
CoolifyResponse = hub_tool.CoolifyResponse
CoolifyHubDeployError = hub_tool.CoolifyHubDeployError

DEFAULT_PLACEMENT_PATH = REPO_ROOT / "deploy" / "hub-topology" / "testnet-coolify-deployment.json"
DEFAULT_TIMEOUT_S = hub_tool.DEFAULT_TIMEOUT_S
DEFAULT_RETRIES = hub_tool.DEFAULT_RETRIES
DEFAULT_RETRY_SLEEP_S = hub_tool.DEFAULT_RETRY_SLEEP_S
DEFAULT_TOKEN_ENV = hub_tool.DEFAULT_TOKEN_ENV
DEFAULT_FDB_IMAGE = hub_tool.DEFAULT_LOCAL_TEST_FDB_IMAGE
DEFAULT_ENVIRONMENT_SUFFIX = "fdb"
FDB_CONFIGURE_ATTEMPTS = 120
FDB_CONFIGURE_SLEEP_S = 2


@dataclass(frozen=True)
class NamedCoolifyBinding:
    name: str
    value: str


@dataclass(frozen=True)
class CoolifyServerPlacement:
    name: str
    vpn_ip: str


@dataclass(frozen=True)
class FoundationDBInstancePlacement:
    id: str
    coolify_server: str
    vpn_ip: str
    port: int
    machine_id: str
    zone_id: str


@dataclass(frozen=True)
class FoundationDBPlacement:
    network_key: str
    image: str
    cluster_description: str
    cluster_id: str
    cluster_file_path: str
    namespace: str
    configure: str
    instances: tuple[FoundationDBInstancePlacement, ...]
    servers: dict[str, CoolifyServerPlacement]
    topology_path: Path
    public_entry_urls: tuple[str, ...]


class _ProfileForContext:
    def __init__(self, network_key: str) -> None:
        self.network_key = network_key


def fail(message: str) -> None:
    raise CoolifyHubDeployError(message)


def repo_relative_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CoolifyHubDeployError(f"File does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CoolifyHubDeployError(f"Could not parse JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CoolifyHubDeployError(f"{path} must contain a JSON object.")
    return data


def clean_required_string(value: Any, field: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise CoolifyHubDeployError(f"{field} must be a non-empty string.")
    return clean


def clean_identifier(value: Any, field: str, *, allow_dots: bool = False) -> str:
    clean = clean_required_string(value, field)
    pattern = r"^[A-Za-z0-9_.-]+$" if allow_dots else r"^[A-Za-z0-9_-]+$"
    if not re.match(pattern, clean):
        raise CoolifyHubDeployError(f"{field} has unsupported characters: {clean!r}.")
    return clean


def clean_cluster_token(value: Any, field: str) -> str:
    clean = clean_required_string(value, field)
    if not re.fullmatch(r"[A-Za-z0-9_]+", clean):
        raise CoolifyHubDeployError(
            f"{field} must contain only ASCII letters, digits, and underscores for FoundationDB cluster strings."
        )
    return clean


def clean_posix_absolute_path(value: Any, field: str) -> str:
    clean = clean_required_string(value, field).replace("\\", "/")
    if not clean.startswith("/"):
        raise CoolifyHubDeployError(f"{field} must be an absolute POSIX path, got {clean!r}.")
    parts = [part for part in clean.split("/") if part]
    if any(part == ".." for part in parts):
        raise CoolifyHubDeployError(f"{field} must not contain '..': {clean!r}.")
    return "/" + "/".join(parts)


def posix_dirname(path: str) -> str:
    clean = clean_posix_absolute_path(path, "path")
    if clean == "/":
        return "/"
    return clean.rsplit("/", 1)[0] or "/"


def sh_quote(value: str) -> str:
    return shlex.quote(str(value))


def yaml_quote(value: str) -> str:
    return json.dumps(str(value))


def service_key(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip()).strip("-")
    return clean or "main-computer-fdb"


def fdb_service_name(network_key: str, server_name: str) -> str:
    return service_key(f"main-computer-{network_key}-fdb-{server_name}")


def split_name_value(raw: str, flag_name: str) -> NamedCoolifyBinding:
    if ":" not in str(raw or ""):
        raise CoolifyHubDeployError(f"{flag_name} must use <name>:<value>, got {raw!r}.")
    name, value = str(raw).split(":", 1)
    clean_name = clean_identifier(name, f"{flag_name} name")
    clean_value = value.strip()
    if not clean_value:
        raise CoolifyHubDeployError(f"{flag_name} for {clean_name!r} has an empty value.")
    return NamedCoolifyBinding(clean_name, clean_value)


def parse_binding_map(values: list[str] | None, flag_name: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in values or []:
        binding = split_name_value(raw, flag_name)
        if binding.name in result:
            raise CoolifyHubDeployError(f"{flag_name} was supplied more than once for {binding.name!r}.")
        result[binding.name] = binding.value
    return result


def token_from_text_file(path: str) -> str:
    clean = str(path or "").strip()
    if not clean:
        return ""
    token = hub_tool.read_token_file(Path(clean))
    if not token:
        raise CoolifyHubDeployError(f"Token file {clean!r} did not contain a token.")
    return token


def token_for_server(server_name: str, args: argparse.Namespace) -> tuple[str, str]:
    explicit_by_server = parse_binding_map(args.set_coolify_token or [], "--set-coolify-token")
    token_env_by_server = parse_binding_map(args.set_coolify_token_env or [], "--set-coolify-token-env")
    token_file_by_server = parse_binding_map(args.set_coolify_token_file or [], "--set-coolify-token-file")

    if server_name in explicit_by_server:
        return explicit_by_server[server_name], f"--set-coolify-token:{server_name}"
    if server_name in token_env_by_server:
        env_name = token_env_by_server[server_name]
        token = str(os.environ.get(env_name) or "").strip()
        if not token:
            raise CoolifyHubDeployError(f"Environment variable {env_name!r} for {server_name!r} is empty or not set.")
        return token, f"env:{env_name}"
    if server_name in token_file_by_server:
        return token_from_text_file(token_file_by_server[server_name]), f"file:{token_file_by_server[server_name]}"
    if str(args.coolify_token or "").strip():
        return str(args.coolify_token).strip(), "--coolify-token"
    if str(args.coolify_token_file or "").strip():
        return token_from_text_file(args.coolify_token_file), f"file:{args.coolify_token_file}"
    token = str(os.environ.get(args.coolify_token_env) or "").strip()
    if token:
        return token, f"env:{args.coolify_token_env}"
    raise CoolifyHubDeployError(
        f"No Coolify token available for {server_name!r}. Pass --set-coolify-token-env, "
        "--set-coolify-token-file, --coolify-token-env, or --coolify-token-file."
    )


def validate_ip(value: str, field: str) -> str:
    clean = clean_required_string(value, field)
    try:
        parsed = ipaddress.ip_address(clean)
    except ValueError as exc:
        raise CoolifyHubDeployError(f"{field} must be an IP address, got {clean!r}.") from exc
    if not parsed.is_private:
        raise CoolifyHubDeployError(f"{field} must be a private/VPN address, got {clean!r}.")
    return clean


def load_fdb_placement(path: Path) -> FoundationDBPlacement:
    data = load_json_object(path)
    kind = clean_required_string(data.get("kind"), "kind")
    if kind != "main_computer.coolify_hub_cluster_placement.v1":
        raise CoolifyHubDeployError(f"Unsupported placement kind {kind!r} in {path}.")

    network_key = clean_identifier(data.get("network_key"), "network_key")
    topology_path = repo_relative_path(clean_required_string(data.get("topology_path"), "topology_path"))
    topology = load_json_object(topology_path)
    topology_network = ((topology.get("network") or {}) if isinstance(topology.get("network"), dict) else {})
    topology_network_key = str(topology_network.get("network_key") or "").strip()
    if topology_network_key and topology_network_key != network_key:
        raise CoolifyHubDeployError(
            f"Placement network_key {network_key!r} does not match topology network_key {topology_network_key!r}."
        )

    server_items = data.get("servers")
    if not isinstance(server_items, list) or not server_items:
        raise CoolifyHubDeployError("servers must be a non-empty array.")
    servers: dict[str, CoolifyServerPlacement] = {}
    for index, item in enumerate(server_items):
        if not isinstance(item, dict):
            raise CoolifyHubDeployError(f"servers[{index}] must be an object.")
        name = clean_identifier(item.get("name"), f"servers[{index}].name")
        if name in servers:
            raise CoolifyHubDeployError(f"Duplicate server name {name!r}.")
        vpn_ip = validate_ip(str(item.get("vpn_ip") or ""), f"servers[{index}].vpn_ip")
        servers[name] = CoolifyServerPlacement(name=name, vpn_ip=vpn_ip)

    fdb = data.get("foundationdb")
    if not isinstance(fdb, dict):
        raise CoolifyHubDeployError("foundationdb must be an object.")
    image = clean_required_string(fdb.get("image") or DEFAULT_FDB_IMAGE, "foundationdb.image")
    cluster_description = clean_cluster_token(fdb.get("cluster_description"), "foundationdb.cluster_description")
    cluster_id = clean_cluster_token(fdb.get("cluster_id"), "foundationdb.cluster_id")
    cluster_file_path = clean_posix_absolute_path(fdb.get("cluster_file_path"), "foundationdb.cluster_file_path")
    namespace = clean_required_string(fdb.get("namespace"), "foundationdb.namespace")
    configure = clean_required_string(fdb.get("configure") or "single memory", "foundationdb.configure")
    if "\n" in configure or "\r" in configure:
        raise CoolifyHubDeployError("foundationdb.configure must be a single fdbcli configure string.")

    network = fdb.get("network") or {}
    if isinstance(network, dict) and network.get("publish_public_ports") not in (False, None):
        raise CoolifyHubDeployError("foundationdb.network.publish_public_ports must be false for the testnet FDB layer.")

    instance_items = fdb.get("instances")
    if not isinstance(instance_items, list) or not instance_items:
        raise CoolifyHubDeployError("foundationdb.instances must be a non-empty array.")

    instances: list[FoundationDBInstancePlacement] = []
    ids: set[str] = set()
    bind_tuples: set[tuple[str, int]] = set()
    for index, item in enumerate(instance_items):
        if not isinstance(item, dict):
            raise CoolifyHubDeployError(f"foundationdb.instances[{index}] must be an object.")
        instance_id = clean_identifier(item.get("id"), f"foundationdb.instances[{index}].id")
        if instance_id in ids:
            raise CoolifyHubDeployError(f"Duplicate FoundationDB instance id {instance_id!r}.")
        ids.add(instance_id)
        server_name = clean_identifier(item.get("coolify_server"), f"foundationdb.instances[{index}].coolify_server")
        server = servers.get(server_name)
        if server is None:
            raise CoolifyHubDeployError(f"FoundationDB instance {instance_id!r} references unknown server {server_name!r}.")
        try:
            port = int(item.get("port"))
        except (TypeError, ValueError) as exc:
            raise CoolifyHubDeployError(f"foundationdb.instances[{index}].port must be an integer.") from exc
        if not 1 <= port <= 65535:
            raise CoolifyHubDeployError(f"foundationdb.instances[{index}].port is outside 1-65535: {port}.")
        bind_tuple = (server.vpn_ip, port)
        if bind_tuple in bind_tuples:
            raise CoolifyHubDeployError(f"Duplicate FDB bind address {server.vpn_ip}:{port}.")
        bind_tuples.add(bind_tuple)
        machine_id = clean_identifier(item.get("machine_id") or server_name, f"foundationdb.instances[{index}].machine_id")
        zone_id = clean_identifier(item.get("zone_id") or server_name, f"foundationdb.instances[{index}].zone_id")
        instances.append(
            FoundationDBInstancePlacement(
                id=instance_id,
                coolify_server=server_name,
                vpn_ip=server.vpn_ip,
                port=port,
                machine_id=machine_id,
                zone_id=zone_id,
            )
        )

    topology_storage = topology.get("storage") if isinstance(topology.get("storage"), dict) else {}
    topology_namespace = str(topology_storage.get("namespace") or "").strip()
    if topology_namespace and topology_namespace != namespace:
        raise CoolifyHubDeployError(
            f"FoundationDB namespace {namespace!r} does not match topology storage.namespace {topology_namespace!r}."
        )

    for index, hub in enumerate(data.get("hubs") or []):
        if not isinstance(hub, dict):
            continue
        hub_cluster_path = str(hub.get("cluster_file_path") or "").strip()
        hub_namespace = str(hub.get("namespace") or "").strip()
        if hub_cluster_path and clean_posix_absolute_path(hub_cluster_path, f"hubs[{index}].cluster_file_path") != cluster_file_path:
            raise CoolifyHubDeployError(
                f"hubs[{index}].cluster_file_path {hub_cluster_path!r} does not match foundationdb.cluster_file_path {cluster_file_path!r}."
            )
        if hub_namespace and hub_namespace != namespace:
            raise CoolifyHubDeployError(
                f"hubs[{index}].namespace {hub_namespace!r} does not match foundationdb.namespace {namespace!r}."
            )

    public_entry_urls = tuple(str(url).strip() for url in data.get("public_entry_urls") or [] if str(url).strip())
    return FoundationDBPlacement(
        network_key=network_key,
        image=image,
        cluster_description=cluster_description,
        cluster_id=cluster_id,
        cluster_file_path=cluster_file_path,
        namespace=namespace,
        configure=configure,
        instances=tuple(instances),
        servers=servers,
        topology_path=topology_path,
        public_entry_urls=public_entry_urls,
    )


def fdb_cluster_contents(placement: FoundationDBPlacement) -> str:
    coordinators = ",".join(f"{instance.vpn_ip}:{instance.port}" for instance in placement.instances)
    return f"{placement.cluster_description}:{placement.cluster_id}@{coordinators}"


def fdb_instance_state_dir(placement: FoundationDBPlacement, instance: FoundationDBInstancePlacement) -> str:
    return f"{posix_dirname(placement.cluster_file_path)}/foundationdb/{instance.id}"


def fdb_server_bootstrap_script(placement: FoundationDBPlacement, instance: FoundationDBInstancePlacement) -> str:
    cluster_contents = fdb_cluster_contents(placement)
    cluster_file = placement.cluster_file_path
    cluster_dir = posix_dirname(cluster_file)
    instance_dir = fdb_instance_state_dir(placement, instance)
    data_dir = f"{instance_dir}/data"
    log_dir = f"{instance_dir}/logs"
    return "\n".join(
        [
            f"echo 'Starting Main Computer FDB instance {instance.id} on {instance.vpn_ip}:{instance.port}'",
            f"mkdir -p {sh_quote(cluster_dir)} {sh_quote(data_dir)} {sh_quote(log_dir)}",
            f"printf '%s\\n' {sh_quote(cluster_contents)} > {sh_quote(cluster_file)}",
            "exec /usr/bin/fdbserver \\",
            f"  --cluster-file {sh_quote(cluster_file)} \\",
            f"  --public-address {sh_quote(f'{instance.vpn_ip}:{instance.port}')} \\",
            f"  --listen-address {sh_quote(f'0.0.0.0:{instance.port}')} \\",
            f"  --datadir {sh_quote(data_dir)} \\",
            f"  --logdir {sh_quote(log_dir)} \\",
            f"  --locality-machineid {sh_quote(instance.machine_id)} \\",
            f"  --locality-zoneid {sh_quote(instance.zone_id)} \\",
            "  --class storage \\",
            "  --knob_disable_posix_kernel_aio 1",
        ]
    )


def fdb_configure_bootstrap_script(placement: FoundationDBPlacement) -> str:
    cluster_contents = fdb_cluster_contents(placement)
    cluster_file = placement.cluster_file_path
    cluster_dir = posix_dirname(cluster_file)
    configure_command = f"configure new {placement.configure}"
    return "\n".join(
        [
            f"mkdir -p {sh_quote(cluster_dir)}",
            f"printf '%s\\n' {sh_quote(cluster_contents)} > {sh_quote(cluster_file)}",
            f"echo 'Using FDB cluster file: {cluster_file}'",
            f"for attempt in $(seq 1 {FDB_CONFIGURE_ATTEMPTS}); do",
            f"  fdbcli -C {sh_quote(cluster_file)} --exec {sh_quote(configure_command)} --timeout 10 >/tmp/main-computer-fdb-configure.log 2>&1 || true",
            f"  if fdbcli -C {sh_quote(cluster_file)} --exec 'status' --timeout 10 >/tmp/main-computer-fdb-status.log 2>&1; then",
            "    echo 'FoundationDB cluster is reachable and configured.'",
            "    tail -f /dev/null",
            "  fi",
            "  if [ \"$attempt\" = " + sh_quote(str(FDB_CONFIGURE_ATTEMPTS)) + " ]; then",
            "    cat /tmp/main-computer-fdb-configure.log >&2 || true",
            "    cat /tmp/main-computer-fdb-status.log >&2 || true",
            "    exit 1",
            "  fi",
            f"  sleep {FDB_CONFIGURE_SLEEP_S}",
            "done",
        ]
    )


def render_server_fdb_compose(placement: FoundationDBPlacement, server_name: str) -> str:
    if server_name not in placement.servers:
        raise CoolifyHubDeployError(f"Unknown server {server_name!r}.")
    instances = [instance for instance in placement.instances if instance.coolify_server == server_name]
    if not instances:
        raise CoolifyHubDeployError(f"No FoundationDB instances are assigned to {server_name!r}.")
    cluster_dir = posix_dirname(placement.cluster_file_path)
    lines: list[str] = [
        f"name: {fdb_service_name(placement.network_key, server_name)}",
        "",
        "services:",
    ]
    for instance in instances:
        key = service_key(instance.id)
        instance_dir = fdb_instance_state_dir(placement, instance)
        lines.extend(
            [
                f"  {key}:",
                f"    image: {yaml_quote(placement.image)}",
                "    restart: unless-stopped",
                "    ports:",
                f"      - {yaml_quote(f'{instance.vpn_ip}:{instance.port}:{instance.port}/tcp')}",
                "    volumes:",
                f"      - {yaml_quote(f'{cluster_dir}:{cluster_dir}')}",
                "    entrypoint:",
                "      - /bin/sh",
                "      - -euc",
                f"      - {yaml_quote(fdb_server_bootstrap_script(placement, instance))}",
                "    healthcheck:",
                f'      test: ["CMD-SHELL", "python3 -c \'import socket; s=socket.create_connection((\\\"127.0.0.1\\\",{instance.port}),timeout=3); s.close()\'"]',
                "      interval: 30s",
                "      timeout: 10s",
                "      start_period: 30s",
                "      retries: 5",
                "",
            ]
        )

    configure_key = service_key(f"{placement.network_key}-fdb-configure")
    lines.extend(
        [
            f"  {configure_key}:",
            f"    image: {yaml_quote(placement.image)}",
            "    restart: unless-stopped",
            "    depends_on:",
            *[f"      - {service_key(instance.id)}" for instance in instances],
            "    volumes:",
            f"      - {yaml_quote(f'{cluster_dir}:{cluster_dir}')}",
            "    entrypoint:",
            "      - /bin/sh",
            "      - -euc",
            f"      - {yaml_quote(fdb_configure_bootstrap_script(placement))}",
            "",
        ]
    )
    return "\n".join(lines)


def service_payload(
    placement: FoundationDBPlacement,
    args: argparse.Namespace,
    *,
    server_name: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    compose = render_server_fdb_compose(placement, server_name)
    payload: dict[str, Any] = {
        "server_uuid": (context or {}).get("server_uuid") or "",
        "project_uuid": (context or {}).get("project_uuid") or "",
        "environment_name": (context or {}).get("environment_name") or args.coolify_environment_name,
        "environment_uuid": (context or {}).get("environment_uuid") or "",
        "name": fdb_service_name(placement.network_key, server_name),
        "description": f"Main Computer {placement.network_key} shared FoundationDB cluster on {server_name}",
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "instant_deploy": False,
    }
    destination_uuid = parse_binding_map(args.set_coolify_destination_uuid or [], "--set-coolify-destination-uuid").get(server_name) or args.coolify_destination_uuid
    if destination_uuid:
        payload["destination_uuid"] = destination_uuid
    return {key: value for key, value in payload.items() if value not in (None, "")}


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(payload)
    if "docker_compose_raw" in redacted:
        redacted["docker_compose_raw"] = "<base64>"
        redacted["docker_compose_raw_bytes"] = len(payload["docker_compose_raw"])
    return redacted


def server_plan(placement: FoundationDBPlacement, args: argparse.Namespace, server_name: str) -> dict[str, Any]:
    server = placement.servers[server_name]
    instances = [instance for instance in placement.instances if instance.coolify_server == server_name]
    return {
        "server": server_name,
        "vpn_ip": server.vpn_ip,
        "coolify_url": parse_binding_map(args.set_coolify_url or [], "--set-coolify-url").get(server_name, ""),
        "service_name": fdb_service_name(placement.network_key, server_name),
        "instances": [
            {
                "id": instance.id,
                "public_address": f"{instance.vpn_ip}:{instance.port}",
                "machine_id": instance.machine_id,
                "zone_id": instance.zone_id,
                "state_dir": fdb_instance_state_dir(placement, instance),
            }
            for instance in instances
        ],
        "service_payload": redact_payload(service_payload(placement, args, server_name=server_name)),
        "docker_compose": render_server_fdb_compose(placement, server_name),
    }


def plan_result(placement: FoundationDBPlacement, args: argparse.Namespace) -> dict[str, Any]:
    coolify_urls = parse_binding_map(args.set_coolify_url or [], "--set-coolify-url")
    missing_urls = sorted(set(placement.servers) - set(coolify_urls))
    extra_urls = sorted(set(coolify_urls) - set(placement.servers))
    if missing_urls:
        raise CoolifyHubDeployError(
            "Missing Coolify API URL mapping for: "
            + ", ".join(missing_urls)
            + ". Pass --set-coolify-url '<name>:<url>' for every placement server."
        )
    if extra_urls:
        raise CoolifyHubDeployError(f"--set-coolify-url references unknown server(s): {', '.join(extra_urls)}.")

    cluster_dir = posix_dirname(placement.cluster_file_path)
    return {
        "network_key": placement.network_key,
        "placement_path": str(args.placement),
        "topology_path": str(placement.topology_path),
        "fdb": {
            "image": placement.image,
            "namespace": placement.namespace,
            "cluster_description": placement.cluster_description,
            "cluster_id": placement.cluster_id,
            "cluster_file_path": placement.cluster_file_path,
            "cluster_dir": cluster_dir,
            "cluster_contents": fdb_cluster_contents(placement),
            "configure": placement.configure,
            "bind_policy": "publish each FDB port on the configured server VPN/private IP only",
        },
        "coolify_environment_name": args.coolify_environment_name,
        "coolify_project_name": args.coolify_project_name,
        "coolify_project_uuid": args.coolify_project_uuid,
        "public_entry_urls": list(placement.public_entry_urls),
        "servers": [server_plan(placement, args, server_name) for server_name in sorted(placement.servers)],
        "operator_note": (
            "Apply this FDB layer before deploying Hub services. Hub services must mount/read the same "
            f"cluster file at {placement.cluster_file_path!r} and use namespace {placement.namespace!r}."
        ),
    }


def context_args_for_server(args: argparse.Namespace, server_name: str) -> argparse.Namespace:
    server_name_overrides = parse_binding_map(args.set_coolify_server_name or [], "--set-coolify-server-name")
    server_uuid_overrides = parse_binding_map(args.set_coolify_server_uuid or [], "--set-coolify-server-uuid")
    environment_uuid_overrides = parse_binding_map(args.set_coolify_environment_uuid or [], "--set-coolify-environment-uuid")
    project_uuid_overrides = parse_binding_map(args.set_coolify_project_uuid or [], "--set-coolify-project-uuid")
    return argparse.Namespace(
        coolify_project_uuid=project_uuid_overrides.get(server_name) or args.coolify_project_uuid,
        coolify_project_name=args.coolify_project_name,
        coolify_environment_name=args.coolify_environment_name,
        coolify_environment_uuid=environment_uuid_overrides.get(server_name) or args.coolify_environment_uuid,
        no_create_environment=args.no_create_environment,
        coolify_server_uuid=server_uuid_overrides.get(server_name) or args.coolify_server_uuid,
        coolify_server_name=server_name_overrides.get(server_name) or args.coolify_server_name,
    )


def client_for_server(server_name: str, args: argparse.Namespace) -> tuple[Any, str]:
    coolify_urls = parse_binding_map(args.set_coolify_url or [], "--set-coolify-url")
    url = coolify_urls[server_name]
    token, token_source = token_for_server(server_name, args)
    client = CoolifyClient(
        url,
        token,
        timeout_s=args.coolify_timeout_s,
        retries=args.coolify_retries,
        retry_sleep_s=args.coolify_retry_sleep_s,
    )
    return client, token_source


def resolve_context_for_server(client: Any, placement: FoundationDBPlacement, args: argparse.Namespace, server_name: str, tried: list[dict[str, Any]]) -> dict[str, Any]:
    context_args = context_args_for_server(args, server_name)
    if not str(context_args.coolify_environment_name or "").strip():
        context_args.coolify_environment_name = f"{placement.network_key}-{DEFAULT_ENVIRONMENT_SUFFIX}"
    profile = _ProfileForContext(placement.network_key)
    return hub_tool.resolve_coolify_context(client, profile, context_args, tried)


def create_service(client: Any, payload: dict[str, Any], tried: list[dict[str, Any]]) -> str:
    response = client.request("POST", "/api/v1/services", payload)
    tried.append(
        {
            "operation": "create-fdb-service",
            "path": "/api/v1/services",
            "payload_keys": sorted(payload),
            "docker_compose_raw_encoding": "base64",
            "response": hub_tool.response_to_dict(response),
        }
    )
    if not response.ok:
        raise CoolifyHubDeployError(f"Coolify FDB service create failed with HTTP {response.status}: {response.body}")
    uuid = hub_tool.service_uuid_from_body(response.body)
    if not uuid:
        raise CoolifyHubDeployError(f"Coolify FDB service create succeeded but no UUID was returned: {response.body}")
    return uuid


def update_service(client: Any, service_uuid: str, service_name: str, compose: str, tried: list[dict[str, Any]]) -> None:
    update_payloads = [
        {"docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"), "name": service_name},
        {"docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii")},
        {"docker_compose": compose, "name": service_name},
        {"compose": compose, "name": service_name},
    ]
    update_paths = [
        f"/api/v1/services/{urllib.parse.quote(service_uuid)}",
        f"/api/v1/services/{urllib.parse.quote(service_uuid)}/compose",
    ]
    for path in update_paths:
        for payload in update_payloads:
            response = client.request("PATCH", path, payload)
            tried.append(
                {
                    "operation": "update-fdb-service",
                    "method": "PATCH",
                    "path": path,
                    "payload_keys": sorted(payload),
                    "response": hub_tool.response_to_dict(response),
                }
            )
            if response.ok:
                return
            if response.status == 405:
                response = client.request("PUT", path, payload)
                tried.append(
                    {
                        "operation": "update-fdb-service",
                        "method": "PUT",
                        "path": path,
                        "payload_keys": sorted(payload),
                        "response": hub_tool.response_to_dict(response),
                    }
                )
                if response.ok:
                    return
            if response.status not in {400, 404, 405, 422}:
                raise CoolifyHubDeployError(f"Coolify FDB service update failed with HTTP {response.status}: {response.body}")
    raise CoolifyHubDeployError("Coolify FDB service update failed on all known endpoints.")


def sync_service_for_server(
    client: Any,
    placement: FoundationDBPlacement,
    args: argparse.Namespace,
    *,
    server_name: str,
    context: dict[str, Any],
    tried: list[dict[str, Any]],
) -> tuple[str, str, dict[str, Any]]:
    name = fdb_service_name(placement.network_key, server_name)
    service_uuid, existing = hub_tool.find_service(client, service_name=name, explicit_uuid="", tried=tried)
    compose = render_server_fdb_compose(placement, server_name)
    if service_uuid:
        update_service(client, service_uuid, name, compose, tried)
        return service_uuid, "updated", existing
    payload = service_payload(placement, args, server_name=server_name, context=context)
    service_uuid = create_service(client, payload, tried)
    return service_uuid, "created", existing


def apply_result(placement: FoundationDBPlacement, args: argparse.Namespace) -> dict[str, Any]:
    plan = plan_result(placement, args)
    if args.dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}

    phases: list[dict[str, Any]] = []
    for server_name in sorted(placement.servers):
        tried: list[dict[str, Any]] = []
        client, token_source = client_for_server(server_name, args)
        version = client.request("GET", "/api/v1/version")
        tried.append({"operation": "coolify-version", "response": hub_tool.response_to_dict(version)})
        if not version.ok:
            raise CoolifyHubDeployError(
                f"Coolify API version check failed for {server_name!r} with HTTP {version.status}: {version.body}"
            )
        context = resolve_context_for_server(client, placement, args, server_name, tried)
        service_uuid, action, existing = sync_service_for_server(
            client,
            placement,
            args,
            server_name=server_name,
            context=context,
            tried=tried,
        )
        deploy_result = None
        if not args.no_deploy:
            deploy_result = hub_tool.trigger_deploy_service(client, service_uuid=service_uuid, force=args.force_deploy, tried=tried)
        phases.append(
            {
                "server": server_name,
                "coolify_url": parse_binding_map(args.set_coolify_url or [], "--set-coolify-url")[server_name],
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

    return {"ok": True, "plan": plan, "phases": phases}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy shared FoundationDB services for a Coolify hub topology.")
    parser.add_argument("action", choices=["plan", "apply"], help="Use plan to render payloads; use apply to call Coolify.")
    parser.add_argument("--placement", type=Path, default=DEFAULT_PLACEMENT_PATH, help="Path to testnet-coolify-deployment.json.")
    parser.add_argument(
        "--set-coolify-url",
        action="append",
        default=[],
        help="Bind a symbolic placement server to a Coolify API base URL. Format: <server-name>:<coolify-base-url>",
    )

    parser.add_argument("--coolify-token", default="", help="One Coolify token for every server. Prefer token env/file options.")
    parser.add_argument("--coolify-token-env", default=DEFAULT_TOKEN_ENV, help="Default env var containing a Coolify token.")
    parser.add_argument("--coolify-token-file", default="", help="Default file containing a Coolify token.")
    parser.add_argument("--set-coolify-token", action="append", default=[], help="Per-server token. Format: <server-name>:<token>")
    parser.add_argument("--set-coolify-token-env", action="append", default=[], help="Per-server token env var. Format: <server-name>:<ENV_VAR>")
    parser.add_argument("--set-coolify-token-file", action="append", default=[], help="Per-server token file. Format: <server-name>:<path>")

    parser.add_argument("--coolify-project-uuid", default="", help="Coolify project UUID used by all servers unless overridden.")
    parser.add_argument("--coolify-project-name", default="", help="Coolify project name resolved on every server.")
    parser.add_argument("--set-coolify-project-uuid", action="append", default=[], help="Per-server project UUID. Format: <server-name>:<uuid>")
    parser.add_argument("--coolify-environment-name", default="", help="Coolify environment name. Defaults to <network>-fdb.")
    parser.add_argument("--coolify-environment-uuid", default="", help="Coolify environment UUID used by all servers unless overridden.")
    parser.add_argument("--set-coolify-environment-uuid", action="append", default=[], help="Per-server environment UUID. Format: <server-name>:<uuid>")
    parser.add_argument("--no-create-environment", action="store_true", help="Fail if the named environment is missing.")
    parser.add_argument("--coolify-server-name", default="", help="Coolify server name resolved on every Coolify API.")
    parser.add_argument("--coolify-server-uuid", default="", help="Coolify server UUID used by all servers unless overridden.")
    parser.add_argument("--set-coolify-server-name", action="append", default=[], help="Per-server Coolify server name. Format: <server-name>:<coolify-server-name>")
    parser.add_argument("--set-coolify-server-uuid", action="append", default=[], help="Per-server Coolify server UUID. Format: <server-name>:<uuid>")
    parser.add_argument("--coolify-destination-uuid", default="", help="Coolify Docker destination UUID used by all servers unless overridden.")
    parser.add_argument("--set-coolify-destination-uuid", action="append", default=[], help="Per-server destination UUID. Format: <server-name>:<uuid>")

    parser.add_argument("--coolify-timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--coolify-retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--coolify-retry-sleep-s", type=float, default=DEFAULT_RETRY_SLEEP_S)
    parser.add_argument("--no-deploy", action="store_true", help="Create/update only; do not trigger a service deploy.")
    parser.add_argument("--force-deploy", action="store_true", help="Ask Coolify to force rebuild/redeploy services.")
    parser.add_argument("--dry-run", action="store_true", help="For apply: render the plan without network or Coolify calls.")
    parser.add_argument("--json", action="store_true", help="Print compact machine-readable JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        placement = load_fdb_placement(repo_relative_path(args.placement))
        if not str(args.coolify_environment_name or "").strip():
            # Apply the same default before planning so service payloads are stable
            # and the apply path resolves the same environment name.
            args.coolify_environment_name = f"{placement.network_key}-{DEFAULT_ENVIRONMENT_SUFFIX}"
        result = {"ok": True, "plan": plan_result(placement, args)} if args.action == "plan" else apply_result(placement, args)
    except CoolifyHubDeployError as exc:
        result = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
        if args.json:
            print(json.dumps(result, sort_keys=True))
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
