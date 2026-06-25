#!/usr/bin/env python3
"""Deploy the Hub layer for a multi-hub Coolify topology.

This tool intentionally manages only the Hub containers described by
``deploy/hub-topology/testnet-coolify-deployment.json``.  The shared
FoundationDB layer must already be deployed by ``tools/coolify_fdb_cluster.py``.
The Hub layer mounts the shared FDB runtime directory and reads the committed
cluster file; it does not create, configure, or overwrite FoundationDB.

Coolify API URLs and tokens are supplied at runtime, so the committed placement
file can use stable names such as ``coolify-a`` and ``coolify-b``.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
HUB_SERVICE_TOOL_PATH = Path(__file__).resolve().with_name("coolify_hub_service.py")
FDB_CLUSTER_TOOL_PATH = Path(__file__).resolve().with_name("coolify_fdb_cluster.py")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_module(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hub_tool = _load_module("coolify_hub_service", HUB_SERVICE_TOOL_PATH)
fdb_tool = _load_module("coolify_fdb_cluster", FDB_CLUSTER_TOOL_PATH)

CoolifyClient = hub_tool.CoolifyClient
CoolifyResponse = hub_tool.CoolifyResponse
CoolifyHubDeployError = hub_tool.CoolifyHubDeployError
HubNetworkConfigError = hub_tool.HubNetworkConfigError

DEFAULT_PLACEMENT_PATH = REPO_ROOT / "deploy" / "hub-topology" / "testnet-coolify-deployment.json"
DEFAULT_TIMEOUT_S = hub_tool.DEFAULT_TIMEOUT_S
DEFAULT_RETRIES = hub_tool.DEFAULT_RETRIES
DEFAULT_RETRY_SLEEP_S = hub_tool.DEFAULT_RETRY_SLEEP_S
DEFAULT_TOKEN_ENV = hub_tool.DEFAULT_TOKEN_ENV
DEFAULT_ENVIRONMENT_SUFFIX = "hubs"
TRAEFIK_DYNAMIC_CONFIG_DIR = "/data/coolify/proxy/dynamic"
TRAEFIK_DYNAMIC_CONFIG_IMAGE = "alpine:3.20"


@dataclass(frozen=True)
class CoolifyServerPlacement:
    name: str
    vpn_ip: str


@dataclass(frozen=True)
class HubPlacement:
    hub_id: str
    coolify_server: str
    public_url: str
    runtime_dir: str
    cluster_file_path: str
    namespace: str


@dataclass(frozen=True)
class HubClusterPlacement:
    network_key: str
    topology_path: Path
    topology_container_path: str
    cluster_file_path: str
    namespace: str
    servers: dict[str, CoolifyServerPlacement]
    hubs: tuple[HubPlacement, ...]
    public_entry_urls: tuple[str, ...]
    topology_cluster_id: str


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


def repo_relative_posix(value: str | Path) -> str:
    raw = str(value).replace("\\", "/").strip()
    if raw.startswith("/"):
        fail(f"Repository-relative path expected, got absolute path: {value!r}")
    parts = [part for part in raw.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        fail(f"Repository-relative path must not contain '..': {value!r}")
    return "/".join(parts)


def container_repo_path(value: str | Path) -> str:
    return "/app/" + repo_relative_posix(value)


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


def clean_identifier(value: Any, field: str) -> str:
    clean = clean_required_string(value, field)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", clean):
        raise CoolifyHubDeployError(f"{field} must contain only letters, numbers, dots, underscores, and dashes.")
    return clean


def clean_posix_absolute_path(value: Any, field: str) -> str:
    clean = clean_required_string(value, field).replace("\\", "/")
    if not clean.startswith("/"):
        raise CoolifyHubDeployError(f"{field} must be an absolute POSIX path.")
    parts = [part for part in clean.split("/") if part]
    if any(part == ".." for part in parts):
        raise CoolifyHubDeployError(f"{field} must not contain '..'.")
    return "/" + "/".join(parts)


def posix_dirname(path: str) -> str:
    clean = clean_posix_absolute_path(path, "path")
    parent = clean.rsplit("/", 1)[0]
    return parent or "/"


def yaml_quote(value: Any) -> str:
    text = str(value)
    return json.dumps(text)


def sh_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\\''") + "'"


def service_key(value: str) -> str:
    clean = str(value or "").strip().lower()
    clean = re.sub(r"[^a-z0-9_.-]+", "-", clean).strip("-")
    if not clean:
        raise CoolifyHubDeployError("service name must not be empty.")
    return clean


def router_id(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip()).strip("-")
    return clean or "main-computer-hub"


def host_from_url(value: str, field: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise CoolifyHubDeployError(f"{field} must be a valid URL: {value!r}") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CoolifyHubDeployError(f"{field} must be an http(s) URL with a hostname: {value!r}")
    return parsed.hostname


def shared_entry_hosts(placement: HubClusterPlacement) -> tuple[str, ...]:
    hosts: list[str] = []
    seen: set[str] = set()
    hub_hosts = {
        host_from_url(hub.public_url, f"hubs[{hub.hub_id}].public_url")
        for hub in placement.hubs
    }
    for index, public_entry_url in enumerate(placement.public_entry_urls):
        host = host_from_url(public_entry_url, f"public_entry_urls[{index}]")
        if host in hub_hosts:
            raise CoolifyHubDeployError(
                f"public_entry_urls[{index}] host {host!r} must not duplicate a concrete hub public_url host."
            )
        if host not in seen:
            seen.add(host)
            hosts.append(host)
    return tuple(hosts)


def traefik_dynamic_config_filename(placement: HubClusterPlacement, server_name: str) -> str:
    return f"main-computer-{router_id(placement.network_key)}-hub-public-entry-{router_id(server_name)}.yml"


def traefik_dynamic_config_path(placement: HubClusterPlacement, server_name: str) -> str:
    return f"{TRAEFIK_DYNAMIC_CONFIG_DIR}/{traefik_dynamic_config_filename(placement, server_name)}"


def traefik_dynamic_config_service_key(placement: HubClusterPlacement, server_name: str) -> str:
    return service_key(f"{placement.network_key}-hub-public-entry-config-{server_name}")


def render_server_traefik_dynamic_config(placement: HubClusterPlacement, profile: Any, args: argparse.Namespace, server_name: str) -> str:
    local_hubs = hubs_for_server(placement, server_name)
    if not local_hubs:
        raise CoolifyHubDeployError(f"No hubs are assigned to server {server_name!r}.")
    hosts = shared_entry_hosts(placement)
    if not hosts:
        raise CoolifyHubDeployError(
            "--install-traefik-dynamic-config requires at least one public_entry_urls[] value in the placement."
        )

    middleware_prefix = router_id(f"{placement.network_key}-hub-public-entry")
    lines: list[str] = [
        "# Generated by tools/coolify_hub_cluster.py --install-traefik-dynamic-config.",
        "# Do not edit this file by hand; rerun the Hub deployer instead.",
        "http:",
        "  middlewares:",
        f"    {middleware_prefix}-redirect-to-https:",
        "      redirectScheme:",
        "        scheme: https",
        f"    {middleware_prefix}-gzip:",
        "      compress: {}",
        "  routers:",
    ]
    for host in hosts:
        rid = router_id(host)
        service = f"{rid}-service"
        lines.extend(
            [
                f"    {rid}-http:",
                "      entryPoints:",
                "        - http",
                f"      rule: {yaml_quote(f'Host(`{host}`)')}",
                "      service: noop@internal",
                "      middlewares:",
                f"        - {middleware_prefix}-redirect-to-https",
                f"    {rid}-https:",
                "      entryPoints:",
                "        - https",
                f"      rule: {yaml_quote(f'Host(`{host}`)')}",
                f"      service: {service}",
                "      middlewares:",
                f"        - {middleware_prefix}-gzip",
                "      tls:",
                "        certResolver: letsencrypt",
            ]
        )

    lines.extend(["  services:"])
    for host in hosts:
        rid = router_id(host)
        service = f"{rid}-service"
        lines.extend(
            [
                f"    {service}:",
                "      loadBalancer:",
                "        passHostHeader: true",
                "        healthCheck:",
                f"          path: {yaml_quote(args.health_path)}",
                "          interval: 30s",
                "          timeout: 5s",
                "        servers:",
            ]
        )
        for hub in local_hubs:
            lines.append(f"          - url: {yaml_quote(f'http://{hub_container_key(hub)}:{profile.hub_bind_port}')}")
    return "\n".join(lines) + "\n"


def render_traefik_dynamic_config_installer_script(
    placement: HubClusterPlacement,
    profile: Any,
    args: argparse.Namespace,
    server_name: str,
) -> str:
    config_path = traefik_dynamic_config_path(placement, server_name)
    config = render_server_traefik_dynamic_config(placement, profile, args, server_name).rstrip("\n")
    return "\n".join(
        [
            "set -eu",
            f"mkdir -p {sh_quote(TRAEFIK_DYNAMIC_CONFIG_DIR)}",
            f"cat > {sh_quote(config_path)} <<'TRAEFIKDYNAMICCONFIG'",
            config,
            "TRAEFIKDYNAMICCONFIG",
            f"echo 'Installed Traefik dynamic config: {config_path}'",
            "tail -f /dev/null",
        ]
    )


def load_hub_cluster_placement(path: Path) -> HubClusterPlacement:
    payload = load_json_object(path)
    kind = clean_required_string(payload.get("kind"), "kind")
    if kind != "main_computer.coolify_hub_cluster_placement.v1":
        raise CoolifyHubDeployError(
            f"Unsupported placement kind {kind!r}; expected main_computer.coolify_hub_cluster_placement.v1."
        )

    network_key = clean_identifier(payload.get("network_key"), "network_key")
    topology_rel = repo_relative_posix(payload.get("topology_path") or "deploy/hub-topology/testnet-topology.json")
    topology_path = repo_relative_path(topology_rel)
    topology = load_json_object(topology_path)
    topology_cluster_id = clean_required_string(topology.get("cluster_id"), "topology.cluster_id")

    topology_network = topology.get("network") if isinstance(topology.get("network"), dict) else {}
    topology_network_key = str(topology_network.get("network_key") or "").strip()
    if topology_network_key and topology_network_key != network_key:
        raise CoolifyHubDeployError(
            f"Placement network_key {network_key!r} does not match topology network_key {topology_network_key!r}."
        )

    raw_servers = payload.get("servers")
    if not isinstance(raw_servers, list) or not raw_servers:
        raise CoolifyHubDeployError("servers must be a non-empty list.")
    servers: dict[str, CoolifyServerPlacement] = {}
    for index, item in enumerate(raw_servers):
        if not isinstance(item, dict):
            raise CoolifyHubDeployError(f"servers[{index}] must be an object.")
        name = clean_identifier(item.get("name"), f"servers[{index}].name")
        if name in servers:
            raise CoolifyHubDeployError(f"Duplicate server name {name!r}.")
        servers[name] = CoolifyServerPlacement(
            name=name,
            vpn_ip=clean_required_string(item.get("vpn_ip"), f"servers[{index}].vpn_ip"),
        )

    foundationdb = payload.get("foundationdb")
    if not isinstance(foundationdb, dict):
        raise CoolifyHubDeployError("foundationdb must be an object.")
    cluster_file_path = clean_posix_absolute_path(
        foundationdb.get("cluster_file_path"),
        "foundationdb.cluster_file_path",
    )
    namespace = clean_identifier(foundationdb.get("namespace"), "foundationdb.namespace")

    topology_storage = topology.get("storage") if isinstance(topology.get("storage"), dict) else {}
    topology_namespace = str(topology_storage.get("namespace") or "").strip()
    if topology_namespace and topology_namespace != namespace:
        raise CoolifyHubDeployError(
            f"Placement FDB namespace {namespace!r} does not match topology storage.namespace {topology_namespace!r}."
        )

    raw_topology_hubs = topology.get("hubs")
    if not isinstance(raw_topology_hubs, list) or not raw_topology_hubs:
        raise CoolifyHubDeployError("topology.hubs must be a non-empty list.")
    topology_hub_urls: dict[str, str] = {}
    for index, item in enumerate(raw_topology_hubs):
        if not isinstance(item, dict):
            raise CoolifyHubDeployError(f"topology.hubs[{index}] must be an object.")
        hub_id = clean_identifier(item.get("hub_id"), f"topology.hubs[{index}].hub_id")
        public_url = clean_required_string(item.get("public_url") or item.get("hub_url"), f"topology.hubs[{index}].public_url")
        topology_hub_urls[hub_id] = public_url

    raw_hubs = payload.get("hubs")
    if not isinstance(raw_hubs, list) or not raw_hubs:
        raise CoolifyHubDeployError("hubs must be a non-empty list.")
    hubs: list[HubPlacement] = []
    seen_hubs: set[str] = set()
    seen_urls: set[str] = set()
    for index, item in enumerate(raw_hubs):
        if not isinstance(item, dict):
            raise CoolifyHubDeployError(f"hubs[{index}] must be an object.")
        hub_id = clean_identifier(item.get("hub_id"), f"hubs[{index}].hub_id")
        if hub_id in seen_hubs:
            raise CoolifyHubDeployError(f"Duplicate hub_id {hub_id!r}.")
        seen_hubs.add(hub_id)
        coolify_server = clean_identifier(item.get("coolify_server"), f"hubs[{index}].coolify_server")
        if coolify_server not in servers:
            raise CoolifyHubDeployError(f"Hub {hub_id!r} references unknown coolify_server {coolify_server!r}.")
        public_url = clean_required_string(item.get("public_url"), f"hubs[{index}].public_url")
        host_from_url(public_url, f"hubs[{index}].public_url")
        if public_url in seen_urls:
            raise CoolifyHubDeployError(f"Duplicate hub public_url {public_url!r}.")
        seen_urls.add(public_url)
        topology_url = topology_hub_urls.get(hub_id)
        if not topology_url:
            raise CoolifyHubDeployError(f"Hub {hub_id!r} is not present in topology.hubs.")
        if topology_url != public_url:
            raise CoolifyHubDeployError(
                f"Hub {hub_id!r} placement public_url {public_url!r} does not match topology URL {topology_url!r}."
            )
        runtime_dir = clean_posix_absolute_path(
            item.get("runtime_dir") or posix_dirname(cluster_file_path),
            f"hubs[{index}].runtime_dir",
        )
        hub_cluster_file_path = clean_posix_absolute_path(
            item.get("cluster_file_path") or cluster_file_path,
            f"hubs[{index}].cluster_file_path",
        )
        hub_namespace = clean_identifier(item.get("namespace") or namespace, f"hubs[{index}].namespace")
        if hub_cluster_file_path != cluster_file_path:
            raise CoolifyHubDeployError(
                f"Hub {hub_id!r} cluster_file_path {hub_cluster_file_path!r} must match foundationdb.cluster_file_path {cluster_file_path!r}."
            )
        if hub_namespace != namespace:
            raise CoolifyHubDeployError(
                f"Hub {hub_id!r} namespace {hub_namespace!r} must match foundationdb.namespace {namespace!r}."
            )
        hubs.append(
            HubPlacement(
                hub_id=hub_id,
                coolify_server=coolify_server,
                public_url=public_url,
                runtime_dir=runtime_dir,
                cluster_file_path=hub_cluster_file_path,
                namespace=hub_namespace,
            )
        )

    public_entry_urls_payload = payload.get("public_entry_urls") or topology.get("entry_urls") or []
    if not isinstance(public_entry_urls_payload, list):
        raise CoolifyHubDeployError("public_entry_urls must be a list.")
    public_entry_urls = tuple(clean_required_string(item, "public_entry_urls[]") for item in public_entry_urls_payload)

    return HubClusterPlacement(
        network_key=network_key,
        topology_path=topology_path,
        topology_container_path=container_repo_path(topology_rel),
        cluster_file_path=cluster_file_path,
        namespace=namespace,
        servers=servers,
        hubs=tuple(hubs),
        public_entry_urls=public_entry_urls,
        topology_cluster_id=topology_cluster_id,
    )


def load_network_profile(placement: HubClusterPlacement, args: argparse.Namespace) -> Any:
    registry = hub_tool.load_hub_network_registry(args.network_config)
    profile = registry.get(placement.network_key)
    return profile


def context_args_for_server(args: argparse.Namespace, server_name: str) -> argparse.Namespace:
    server_name_overrides = fdb_tool.parse_binding_map(args.set_coolify_server_name or [], "--set-coolify-server-name")
    server_uuid_overrides = fdb_tool.parse_binding_map(args.set_coolify_server_uuid or [], "--set-coolify-server-uuid")
    environment_uuid_overrides = fdb_tool.parse_binding_map(args.set_coolify_environment_uuid or [], "--set-coolify-environment-uuid")
    project_uuid_overrides = fdb_tool.parse_binding_map(args.set_coolify_project_uuid or [], "--set-coolify-project-uuid")
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
    coolify_urls = fdb_tool.parse_binding_map(args.set_coolify_url or [], "--set-coolify-url")
    url = coolify_urls[server_name]
    token, token_source = fdb_tool.token_for_server(server_name, args)
    client = CoolifyClient(
        url,
        token,
        timeout_s=args.coolify_timeout_s,
        retries=args.coolify_retries,
        retry_sleep_s=args.coolify_retry_sleep_s,
    )
    return client, token_source


def resolve_context_for_server(client: Any, placement: HubClusterPlacement, args: argparse.Namespace, server_name: str, tried: list[dict[str, Any]]) -> dict[str, Any]:
    context_args = context_args_for_server(args, server_name)
    if not str(context_args.coolify_environment_name or "").strip():
        context_args.coolify_environment_name = f"{placement.network_key}-{DEFAULT_ENVIRONMENT_SUFFIX}"
    profile = _ProfileForContext(placement.network_key)
    return hub_tool.resolve_coolify_context(client, profile, context_args, tried)


def hubs_for_server(placement: HubClusterPlacement, server_name: str) -> list[HubPlacement]:
    return [hub for hub in placement.hubs if hub.coolify_server == server_name]


def hub_service_name(placement: HubClusterPlacement, server_name: str) -> str:
    return f"main-computer-{placement.network_key}-hubs-{service_key(server_name)}"


def hub_container_key(hub: HubPlacement) -> str:
    return service_key(hub.hub_id)


def hub_command_parts(profile: Any, placement: HubClusterPlacement, hub: HubPlacement, args: argparse.Namespace) -> list[str]:
    parts = [
        "python",
        "/app/exp-fdb-hub.py",
        "--host",
        str(profile.hub_bind_host),
        "--port",
        str(profile.hub_bind_port),
        "--hub-url",
        hub.public_url,
        "--topology",
        placement.topology_container_path,
        "--hub-id",
        hub.hub_id,
        "--hub-root",
        hub.runtime_dir,
        "--cluster-file",
        hub.cluster_file_path,
        "--namespace",
        hub.namespace,
        "--network-key",
        placement.network_key,
        "--network-display-name",
        str(profile.display_name),
        "--network-kind",
        str(profile.kind),
        "--no-fdb-autostart",
        "--no-activate-cached-native-client",
        "--require-multisession-auth",
        "--bridge-backend",
        hub_tool.hub_bridge_backend(args),
    ]
    if hub_tool.hub_bridge_backend(args) not in {"mock", "mock-chain", "mock-chain-lite"}:
        if hub_tool.hub_enable_bridge_writes(args):
            parts.extend(["--dev-chain-deployment-path", hub_tool.bridge_signer_remote_path(profile, args, runtime_dir=hub.runtime_dir)])
        elif str(getattr(args, "dev_chain_deployment_path", "") or "").strip() or not hub_tool.hub_allow_missing_bridge_signer(profile, args):
            parts.extend(["--dev-chain-deployment-path", hub_tool.dev_chain_deployment_path(profile, args)])
        parts.extend(["--contracts-path", hub_tool.contracts_path(profile, args)])
        if hub_tool.hub_allow_missing_bridge_signer(profile, args):
            parts.append("--allow-missing-bridge-signer")
        if hub_tool.hub_enable_smoke_bridge(args):
            parts.append("--enable-smoke-bridge")
    if profile.chain_id is not None:
        parts.extend(["--chain-id", str(profile.chain_id)])
    runtime_chain_rpc_url = hub_tool.hub_chain_rpc_url(profile, args)
    if runtime_chain_rpc_url:
        parts.extend(["--chain-rpc-url", runtime_chain_rpc_url])
    return parts


def render_hub_command_yaml(parts: list[str]) -> list[str]:
    return [f"      - {yaml_quote(part)}" for part in parts]


def render_server_hub_compose(placement: HubClusterPlacement, profile: Any, args: argparse.Namespace, server_name: str) -> str:
    local_hubs = hubs_for_server(placement, server_name)
    if not local_hubs:
        raise CoolifyHubDeployError(f"No hubs are assigned to server {server_name!r}.")
    build_context = hub_tool.remote_git_build_context(args)
    dockerfile = hub_tool.effective_dockerfile_location(profile, args).lstrip("/") or "Dockerfile.hub.exp-fdb"
    service_name = hub_service_name(placement, server_name)
    lines: list[str] = [
        f"name: {service_name}",
        "",
        "services:",
    ]
    for hub in local_hubs:
        key = hub_container_key(hub)
        host = host_from_url(hub.public_url, f"{hub.hub_id}.public_url")
        rid = router_id(key)
        runtime_bind = f"{hub_tool.remote_runtime_bind_source(hub.runtime_dir)}:{hub.runtime_dir}"
        image = f"main-computer-{placement.network_key}-{key}:remote"
        command = hub_command_parts(profile, placement, hub, args)
        lines.extend(
            [
                f"  {key}:",
                "    build:",
                f"      context: {yaml_quote(build_context)}",
                f"      dockerfile: {yaml_quote(dockerfile)}",
                f"    image: {yaml_quote(image)}",
                "    pull_policy: build",
                "    restart: unless-stopped",
                "    expose:",
                f"      - {yaml_quote(str(profile.hub_bind_port))}",
                "    environment:",
                f"      HUB_HEALTH_PORT: {yaml_quote(str(profile.hub_bind_port))}",
                f"      PORT: {yaml_quote(str(profile.hub_bind_port))}",
                f"      MAIN_COMPUTER_HUB_NETWORK: {yaml_quote(placement.network_key)}",
                f"      MAIN_COMPUTER_HUB_ROOT: {yaml_quote(hub.runtime_dir)}",
                f"      MAIN_COMPUTER_HUB_FDB_NAMESPACE: {yaml_quote(hub.namespace)}",
                f"      FDB_CLUSTER_FILE: {yaml_quote(hub.cluster_file_path)}",
                "    volumes:",
                f"      - {yaml_quote(runtime_bind)}",
                "    labels:",
                "      - \"traefik.enable=true\"",
                f"      - {yaml_quote(f'traefik.http.routers.{rid}.rule=Host(`{host}`)')}",
                f"      - {yaml_quote(f'traefik.http.routers.{rid}.entryPoints=https')}",
                f"      - {yaml_quote(f'traefik.http.routers.{rid}.tls=true')}",
                f"      - {yaml_quote(f'traefik.http.routers.{rid}.tls.certresolver=letsencrypt')}",
                f"      - {yaml_quote(f'traefik.http.services.{rid}.loadbalancer.server.port={profile.hub_bind_port}')}",
                "    command:",
                *render_hub_command_yaml(command),
                "    healthcheck:",
                f'      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:{profile.hub_bind_port}{args.health_path} >/dev/null || exit 1"]',
                "      interval: 30s",
                "      timeout: 5s",
                "      start_period: 30s",
                "      retries: 5",
                "",
            ]
        )

    if getattr(args, "install_traefik_dynamic_config", False):
        installer_key = traefik_dynamic_config_service_key(placement, server_name)
        installer_script = render_traefik_dynamic_config_installer_script(placement, profile, args, server_name)
        lines.extend(
            [
                f"  {installer_key}:",
                f"    image: {yaml_quote(TRAEFIK_DYNAMIC_CONFIG_IMAGE)}",
                "    restart: unless-stopped",
                "    volumes:",
                f"      - {yaml_quote(f'{TRAEFIK_DYNAMIC_CONFIG_DIR}:{TRAEFIK_DYNAMIC_CONFIG_DIR}')}",
                "    command:",
                "      - /bin/sh",
                "      - -euc",
                f"      - {yaml_quote(installer_script)}",
                "",
            ]
        )
    return "\n".join(lines)


def service_payload(placement: HubClusterPlacement, profile: Any, args: argparse.Namespace, *, server_name: str, context: dict[str, Any]) -> dict[str, Any]:
    service_name = hub_service_name(placement, server_name)
    compose = render_server_hub_compose(placement, profile, args, server_name)
    destination_overrides = fdb_tool.parse_binding_map(args.set_coolify_destination_uuid or [], "--set-coolify-destination-uuid")
    destination_uuid = destination_overrides.get(server_name) or args.coolify_destination_uuid
    payload: dict[str, Any] = {
        "server_uuid": context.get("server_uuid"),
        "project_uuid": context.get("project_uuid"),
        "environment_name": context.get("environment_name") or args.coolify_environment_name,
        "environment_uuid": context.get("environment_uuid") or args.coolify_environment_uuid,
        "name": service_name,
        "description": f"Main Computer {placement.network_key} Hub containers on {server_name}",
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "instant_deploy": False,
    }
    if destination_uuid:
        payload["destination_uuid"] = destination_uuid
    return {key: value for key, value in payload.items() if value not in (None, "")}


def create_service(client: Any, payload: dict[str, Any], tried: list[dict[str, Any]]) -> str:
    response = client.request("POST", "/api/v1/services", payload)
    tried.append(
        {
            "operation": "create-hub-service",
            "path": "/api/v1/services",
            "payload_keys": sorted(payload),
            "docker_compose_raw_encoding": "base64",
            "response": hub_tool.response_to_dict(response),
        }
    )
    if not response.ok:
        raise CoolifyHubDeployError(f"Coolify Hub service create failed with HTTP {response.status}: {response.body}")
    uuid = hub_tool.service_uuid_from_body(response.body)
    if not uuid:
        raise CoolifyHubDeployError(f"Coolify Hub service create succeeded but no UUID was returned: {response.body}")
    return uuid


def update_service(client: Any, service_uuid: str, service_name: str, compose: str, tried: list[dict[str, Any]]) -> None:
    encoded = base64.b64encode(compose.encode("utf-8")).decode("ascii")
    update_payloads = [
        {"docker_compose_raw": encoded, "name": service_name},
        {"docker_compose_raw": encoded},
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
                    "operation": "update-hub-service",
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
                        "operation": "update-hub-service",
                        "method": "PUT",
                        "path": path,
                        "payload_keys": sorted(payload),
                        "response": hub_tool.response_to_dict(response),
                    }
                )
                if response.ok:
                    return
            if response.status not in {400, 404, 405, 422}:
                raise CoolifyHubDeployError(f"Coolify Hub service update failed with HTTP {response.status}: {response.body}")
    raise CoolifyHubDeployError("Coolify Hub service update failed on all known endpoints.")


def sync_service_for_server(
    client: Any,
    placement: HubClusterPlacement,
    profile: Any,
    args: argparse.Namespace,
    *,
    server_name: str,
    context: dict[str, Any],
    tried: list[dict[str, Any]],
) -> tuple[str, str, dict[str, Any]]:
    name = hub_service_name(placement, server_name)
    service_uuid, existing = hub_tool.find_service(client, service_name=name, explicit_uuid="", tried=tried)
    compose = render_server_hub_compose(placement, profile, args, server_name)
    if service_uuid:
        update_service(client, service_uuid, name, compose, tried)
        return service_uuid, "updated", existing
    payload = service_payload(placement, profile, args, server_name=server_name, context=context)
    service_uuid = create_service(client, payload, tried)
    return service_uuid, "created", existing


def server_plan(placement: HubClusterPlacement, profile: Any, args: argparse.Namespace, server_name: str) -> dict[str, Any]:
    compose = render_server_hub_compose(placement, profile, args, server_name)
    context_preview = {
        "server_uuid": "<resolved-at-apply>",
        "project_uuid": args.coolify_project_uuid or "<resolved-at-apply>",
        "environment_name": args.coolify_environment_name,
        "environment_uuid": args.coolify_environment_uuid or "<resolved-at-apply>",
    }
    payload = service_payload(placement, profile, args, server_name=server_name, context=context_preview)
    traefik_dynamic_config = None
    if shared_entry_hosts(placement):
        traefik_dynamic_config = {
            "installed": bool(getattr(args, "install_traefik_dynamic_config", False)),
            "container_service": traefik_dynamic_config_service_key(placement, server_name),
            "path": traefik_dynamic_config_path(placement, server_name),
            "contents": render_server_traefik_dynamic_config(placement, profile, args, server_name),
        }
    return {
        "server": server_name,
        "coolify_url": fdb_tool.parse_binding_map(args.set_coolify_url or [], "--set-coolify-url")[server_name],
        "service_name": hub_service_name(placement, server_name),
        "hubs": [
            {
                "hub_id": hub.hub_id,
                "public_url": hub.public_url,
                "runtime_dir": hub.runtime_dir,
                "cluster_file_path": hub.cluster_file_path,
                "namespace": hub.namespace,
            }
            for hub in hubs_for_server(placement, server_name)
        ],
        "docker_compose": compose,
        "traefik_dynamic_config": traefik_dynamic_config,
        "service_payload": {
            **{key: value for key, value in payload.items() if key != "docker_compose_raw"},
            "docker_compose_raw": "<base64>",
            "docker_compose_raw_bytes": len(payload.get("docker_compose_raw", "")),
        },
    }


def plan_result(placement: HubClusterPlacement, profile: Any, args: argparse.Namespace) -> dict[str, Any]:
    coolify_urls = fdb_tool.parse_binding_map(args.set_coolify_url or [], "--set-coolify-url")
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

    if not str(getattr(args, "git_repo", "") or "").strip():
        raise CoolifyHubDeployError("--git-repo is required so the remote Hub services can build the Hub image.")

    return {
        "network_key": placement.network_key,
        "placement_path": str(args.placement),
        "topology_path": str(placement.topology_path),
        "topology_container_path": placement.topology_container_path,
        "topology_cluster_id": placement.topology_cluster_id,
        "coolify_environment_name": args.coolify_environment_name,
        "coolify_project_name": args.coolify_project_name,
        "coolify_project_uuid": args.coolify_project_uuid,
        "hub": {
            "bind_host": profile.hub_bind_host,
            "bind_port": profile.hub_bind_port,
            "dockerfile": hub_tool.effective_dockerfile_location(profile, args),
            "git_context": hub_tool.remote_git_build_context(args),
            "cluster_file_path": placement.cluster_file_path,
            "namespace": placement.namespace,
            "public_entry_urls": list(placement.public_entry_urls),
        },
        "servers": [server_plan(placement, profile, args, server_name) for server_name in sorted(placement.servers)],
        "operator_note": (
            "Apply the shared FDB layer first. This Hub layer mounts the shared runtime directory and reads "
            f"{placement.cluster_file_path!r}; it does not start an FDB sidecar or rewrite fdb.cluster."
        ),
    }


def apply_result(placement: HubClusterPlacement, profile: Any, args: argparse.Namespace) -> dict[str, Any]:
    plan = plan_result(placement, profile, args)
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
            profile,
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
                "coolify_url": fdb_tool.parse_binding_map(args.set_coolify_url or [], "--set-coolify-url")[server_name],
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
    parser = argparse.ArgumentParser(description="Deploy Hub services for a multi-hub Coolify topology.")
    parser.add_argument("action", choices=["plan", "apply"], help="Use plan to render payloads; use apply to call Coolify.")
    parser.add_argument("--placement", type=Path, default=DEFAULT_PLACEMENT_PATH, help="Path to testnet-coolify-deployment.json.")
    parser.add_argument("--network-config", type=Path, default=None, help="Path to hub_networks.json.")

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
    parser.add_argument("--coolify-environment-name", default="", help="Coolify environment name. Defaults to <network>-hubs.")
    parser.add_argument("--coolify-environment-uuid", default="", help="Coolify environment UUID used by all servers unless overridden.")
    parser.add_argument("--set-coolify-environment-uuid", action="append", default=[], help="Per-server environment UUID. Format: <server-name>:<uuid>")
    parser.add_argument("--no-create-environment", action="store_true", help="Fail if the named environment is missing.")
    parser.add_argument("--coolify-server-name", default="", help="Coolify server name resolved on every Coolify API.")
    parser.add_argument("--coolify-server-uuid", default="", help="Coolify server UUID used by all servers unless overridden.")
    parser.add_argument("--set-coolify-server-name", action="append", default=[], help="Per-server Coolify server name. Format: <server-name>:<coolify-server-name>")
    parser.add_argument("--set-coolify-server-uuid", action="append", default=[], help="Per-server Coolify server UUID. Format: <server-name>:<uuid>")
    parser.add_argument("--coolify-destination-uuid", default="", help="Coolify Docker destination UUID used by all servers unless overridden.")
    parser.add_argument("--set-coolify-destination-uuid", action="append", default=[], help="Per-server destination UUID. Format: <server-name>:<uuid>")

    parser.add_argument("--git-repo", default="", help="Git repository URL for remote Hub service builds.")
    parser.add_argument("--git-branch", default="main", help="Git branch to deploy.")
    parser.add_argument("--git-commit-sha", default="", help="Optional exact commit SHA.")
    parser.add_argument("--base-directory", default=hub_tool.DEFAULT_BASE_DIRECTORY)
    parser.add_argument("--dockerfile-location", default="", help="Dockerfile path. Defaults to /Dockerfile.hub.exp-fdb.")
    parser.add_argument("--health-path", default=hub_tool.DEFAULT_HEALTH_PATH)

    parser.add_argument("--hub-chain-rpc-url", default="", help="Override the chain RPC URL passed to each Hub container.")
    parser.add_argument("--bridge-backend", choices=["dev-chain", "credit-bridge-contract", "mock-chain"], default="")
    parser.add_argument("--dev-chain-deployment-path", default="")
    parser.add_argument("--contracts-path", default="")
    parser.add_argument("--allow-missing-bridge-signer", action="store_true")
    parser.add_argument("--enable-smoke-bridge", action="store_true")
    parser.add_argument("--enable-bridge-writes", action="store_true")
    parser.add_argument("--sync-bridge-signer", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--bridge-signer-source-manifest", default="", help=argparse.SUPPRESS)
    parser.add_argument("--bridge-controller-wallet-path", default="", help=argparse.SUPPRESS)
    parser.add_argument("--bridge-signer-env-key", default="", help=argparse.SUPPRESS)
    parser.add_argument("--bridge-signer-remote-path", default="", help=argparse.SUPPRESS)

    parser.add_argument("--coolify-timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--coolify-retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--coolify-retry-sleep-s", type=float, default=DEFAULT_RETRY_SLEEP_S)
    parser.add_argument("--no-deploy", action="store_true", help="Create/update only; do not trigger a service deploy.")
    parser.add_argument(
        "--install-traefik-dynamic-config",
        action="store_true",
        help=(
            "Install per-server Traefik dynamic config for public_entry_urls by adding a small writer service "
            f"that writes to {TRAEFIK_DYNAMIC_CONFIG_DIR}."
        ),
    )
    parser.add_argument("--force-deploy", action="store_true", help="Ask Coolify to force rebuild/redeploy services.")
    parser.add_argument("--dry-run", action="store_true", help="For apply: render the plan without network or Coolify calls.")
    parser.add_argument("--json", action="store_true", help="Print compact machine-readable JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        placement = load_hub_cluster_placement(repo_relative_path(args.placement))
        profile = load_network_profile(placement, args)
        if not str(args.coolify_environment_name or "").strip():
            args.coolify_environment_name = f"{placement.network_key}-{DEFAULT_ENVIRONMENT_SUFFIX}"
        result = (
            {"ok": True, "plan": plan_result(placement, profile, args)}
            if args.action == "plan"
            else apply_result(placement, profile, args)
        )
    except (CoolifyHubDeployError, HubNetworkConfigError) as exc:
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
