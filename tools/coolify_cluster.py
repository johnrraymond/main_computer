#!/usr/bin/env python3
r"""Orchestrate packet-backed Coolify Hub/FDB cluster deploys.

This is the operator-facing control surface for a Hub/FDB deploy generation.  It
keeps the layer-specific deployers narrow:

* ``deploy_packet.py`` builds and validates the local candidate packet.
* ``coolify_fdb_cluster.py`` owns FoundationDB service rendering/apply.
* ``coolify_hub_cluster.py`` owns Hub service rendering/apply.
* this script owns the lookahead/preflight and the cross-layer workflow.

The normal shape is:

    python .\tools\coolify_cluster.py apply testnet \
      --hubs testnet-hub1,testnet-hub2,testnet-hub3 \
      --fdb testnet-fdb1,testnet-fdb2,testnet-fdb3 \
      --git-repo https://github.com/johnrraymond/main_computer
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_PACKET_TOOL_PATH = Path(__file__).resolve().with_name("deploy_packet.py")
FDB_CLUSTER_TOOL_PATH = Path(__file__).resolve().with_name("coolify_fdb_cluster.py")
HUB_CLUSTER_TOOL_PATH = Path(__file__).resolve().with_name("coolify_hub_cluster.py")


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


packet_tool = _load_module("deploy_packet", DEPLOY_PACKET_TOOL_PATH)
fdb_tool = _load_module("coolify_fdb_cluster", FDB_CLUSTER_TOOL_PATH)
hub_cluster_tool = _load_module("coolify_hub_cluster", HUB_CLUSTER_TOOL_PATH)

CoolifyHubDeployError = fdb_tool.CoolifyHubDeployError
HubNetworkConfigError = hub_cluster_tool.HubNetworkConfigError

DEFAULT_TOKEN_ENV = fdb_tool.DEFAULT_TOKEN_ENV
DEFAULT_TIMEOUT_S = fdb_tool.DEFAULT_TIMEOUT_S
DEFAULT_RETRIES = fdb_tool.DEFAULT_RETRIES
DEFAULT_RETRY_SLEEP_S = fdb_tool.DEFAULT_RETRY_SLEEP_S
DEFAULT_PRIVATE_STATE_PATH = fdb_tool.DEFAULT_PRIVATE_STATE_PATH

STATE_PROJECT_NAME_KEYS = ("project_name", "coolify_project_name")
STATE_PROJECT_UUID_KEYS = ("project_uuid", "coolify_project_uuid")
STATE_SERVER_NAME_KEYS = ("server_name", "coolify_server_name")
STATE_SERVER_UUID_KEYS = ("server_uuid", "coolify_server_uuid")
STATE_DESTINATION_UUID_KEYS = ("destination_uuid", "coolify_destination_uuid")
STATE_ENVIRONMENT_NAME_KEYS = ("environment_name", "coolify_environment_name")
STATE_FDB_ENVIRONMENT_NAME_KEYS = ("fdb_environment_name", "coolify_fdb_environment_name")
STATE_HUB_ENVIRONMENT_NAME_KEYS = ("hub_environment_name", "coolify_hub_environment_name")
STATE_ENVIRONMENT_UUID_KEYS = ("environment_uuid", "coolify_environment_uuid")


class CoolifyClusterError(RuntimeError):
    """Raised for orchestration/preflight errors."""


@dataclass(frozen=True)
class PreflightIssue:
    level: str
    code: str
    message: str
    remediation: str = ""

    def as_dict(self) -> dict[str, str]:
        data = {"level": self.level, "code": self.code, "message": self.message}
        if self.remediation:
            data["remediation"] = self.remediation
        return data


def repo_relative_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def repo_relative_display(value: str | Path) -> str:
    path = Path(value)
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except Exception:
        return path.as_posix().replace("\\", "/")


def clean_network(value: str) -> str:
    return packet_tool.clean_identifier(value, "network")


def default_placement_path(network: str) -> Path:
    return packet_tool.default_placement_path(clean_network(network))


def packet_path_for_network(network: str) -> Path:
    return packet_tool.packet_path_for_network(clean_network(network))


def packet_path_for_args(args: argparse.Namespace) -> Path:
    if getattr(args, "packet", None):
        return repo_relative_path(args.packet)
    return packet_path_for_network(args.network)


def placement_path_for_args(args: argparse.Namespace) -> Path:
    if getattr(args, "placement", None):
        return repo_relative_path(args.placement)
    return default_placement_path(args.network)


def topology_path_for_args(args: argparse.Namespace) -> Path | None:
    raw = getattr(args, "topology", None)
    return repo_relative_path(raw) if raw else None


def private_state_path_for_args(args: argparse.Namespace) -> Path:
    if getattr(args, "private_state", None):
        return repo_relative_path(args.private_state)
    return DEFAULT_PRIVATE_STATE_PATH


def comma_list(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def selected_hub_ids(args: argparse.Namespace) -> list[str]:
    return packet_tool.split_csv(args.hubs, "--hubs")


def selected_fdb_ids(args: argparse.Namespace) -> list[str]:
    return packet_tool.split_csv(args.fdb, "--fdb")


def binding_map(values: list[str] | None, flag_name: str) -> dict[str, str]:
    return fdb_tool.parse_binding_map(values or [], flag_name)


def _state_value_is_known(value: Any) -> bool:
    return fdb_tool.private_state_value_is_known(value)


def _state_text(value: Any) -> str:
    return str(value).strip() if _state_value_is_known(value) else ""


def _state_field(payload: Mapping[str, Any], keys: tuple[str, ...]) -> tuple[str, str]:
    for key in keys:
        value = _state_text(payload.get(key))
        if value:
            return value, key
    return "", ""


def _token_field_status(payload: Mapping[str, Any], source_prefix: str) -> tuple[bool, str, str]:
    token, key = _state_field(payload, fdb_tool.PRIVATE_STATE_TOKEN_KEYS)
    if token:
        return True, f"{source_prefix}.{key}", ""

    env_name, key = _state_field(payload, fdb_tool.PRIVATE_STATE_TOKEN_ENV_KEYS)
    if env_name:
        if str(os.environ.get(env_name) or "").strip():
            return True, f"{source_prefix}.{key}->env:{env_name}", ""
        return False, f"{source_prefix}.{key}", f"Environment variable {env_name!r} is empty or not set."

    token_file, key = _state_field(payload, fdb_tool.PRIVATE_STATE_TOKEN_FILE_KEYS)
    if token_file:
        token_path = repo_relative_path(token_file)
        if token_path.exists() and token_path.read_text(encoding="utf-8").strip():
            return True, f"{source_prefix}.{key}->file:{repo_relative_display(token_path)}", ""
        return False, f"{source_prefix}.{key}", f"Token file {repo_relative_display(token_path)!r} is missing or empty."

    return False, source_prefix, "No api_token, api_token_env, or api_token_file is set."


def _load_yaml_state(path: Path) -> tuple[dict[str, Any] | None, str]:
    if not path.exists():
        return None, "missing"
    try:
        import yaml
    except ImportError:
        return None, "PyYAML is required to read the private state YAML."
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"could not parse YAML: {exc}"
    if loaded is None:
        return {}, ""
    if not isinstance(loaded, dict):
        return None, "private state file must contain a YAML mapping"
    return loaded, ""


def _private_host_payloads_by_name(state: Mapping[str, Any]) -> dict[str, tuple[str, Mapping[str, Any], str]]:
    coolify = state.get("coolify")
    if not isinstance(coolify, Mapping):
        return {}

    by_name: dict[str, tuple[str, Mapping[str, Any], str]] = {}
    hosts = coolify.get("hosts")
    if isinstance(hosts, Mapping):
        for slot, payload in hosts.items():
            if not isinstance(payload, Mapping):
                continue
            name = _state_text(payload.get("name"))
            if name:
                by_name[name] = (str(slot), payload, f"coolify.hosts.{slot}")

    local_test = coolify.get("local_test")
    if isinstance(local_test, Mapping):
        name = _state_text(local_test.get("name"))
        if name:
            by_name[name] = ("local_test", local_test, "coolify.local_test")

    return by_name


def _state_mapping_at(state: Mapping[str, Any], path: tuple[str, ...]) -> Mapping[str, Any]:
    node: Any = state
    for part in path:
        if not isinstance(node, Mapping):
            return {}
        node = node.get(part)
    return node if isinstance(node, Mapping) else {}


def _private_default_sources(state: Mapping[str, Any], network: str) -> list[tuple[Mapping[str, Any], str]]:
    """Return state mappings in precedence order for cluster-level Coolify defaults."""

    return [
        (_state_mapping_at(state, ("networks", network, "coolify")), f"networks.{network}.coolify"),
        (_state_mapping_at(state, ("coolify", "networks", network)), f"coolify.networks.{network}"),
        (_state_mapping_at(state, ("coolify", "defaults")), "coolify.defaults"),
        (_state_mapping_at(state, ("coolify",)), "coolify"),
    ]


def _default_state_field(state: Mapping[str, Any], network: str, keys: tuple[str, ...]) -> tuple[str, str]:
    for payload, prefix in _private_default_sources(state, network):
        value, key = _state_field(payload, keys)
        if value:
            return value, f"{prefix}.{key}"
    return "", ""


def _append_binding_if_missing(values: list[str] | None, flag_name: str, server_name: str, value: str) -> list[str]:
    existing = binding_map(values or [], flag_name)
    if server_name in existing or not str(value or "").strip():
        return list(values or [])
    return list(values or []) + [f"{server_name}:{value}"]


def _consistent_host_field(
    private_hosts: Mapping[str, tuple[str, Mapping[str, Any], str]],
    required_servers: list[str],
    keys: tuple[str, ...],
) -> tuple[str, str, str]:
    found: list[tuple[str, str, str]] = []
    for server_name in required_servers:
        host = private_hosts.get(server_name)
        if host is None:
            continue
        _slot, payload, source_prefix = host
        value, key = _state_field(payload, keys)
        if value:
            found.append((server_name, value, f"{source_prefix}.{key}"))
    if not found:
        return "", "", ""
    values = {value for _server_name, value, _source in found}
    if len(values) == 1:
        server_name, value, source = found[0]
        return value, source, ""
    detail = ", ".join(f"{server_name}={value!r}" for server_name, value, _source in found)
    return "", "", detail


def resolve_private_state_defaults(args: argparse.Namespace, packet: Mapping[str, Any]) -> argparse.Namespace:
    """Apply private-state Coolify context defaults to a copy of ``args``.

    CLI flags remain highest precedence.  Private state only fills missing global
    values and per-server override bindings.
    """

    resolved = argparse.Namespace(**vars(args))
    context_sources: dict[str, str] = {}
    context_warnings: list[dict[str, str]] = []
    setattr(resolved, "_coolify_context_sources", context_sources)
    setattr(resolved, "_coolify_context_warnings", context_warnings)

    if args.no_private_state:
        return resolved

    state_path = private_state_path_for_args(args)
    state, state_error = _load_yaml_state(state_path)
    setattr(resolved, "_private_state_loaded", state is not None)
    setattr(resolved, "_private_state_error", state_error)
    if state is None:
        return resolved

    required_servers = required_server_names(packet)
    private_hosts = _private_host_payloads_by_name(state)
    setattr(resolved, "_private_state_hosts", private_hosts)

    if not str(resolved.coolify_project_uuid or "").strip() and not str(resolved.coolify_project_name or "").strip():
        value, source = _default_state_field(state, args.network, STATE_PROJECT_UUID_KEYS)
        if value:
            resolved.coolify_project_uuid = value
            context_sources["coolify_project_uuid"] = f"private-state:{source}"
        else:
            value, source = _default_state_field(state, args.network, STATE_PROJECT_NAME_KEYS)
            if value:
                resolved.coolify_project_name = value
                context_sources["coolify_project_name"] = f"private-state:{source}"
            else:
                value, source, conflict = _consistent_host_field(private_hosts, required_servers, STATE_PROJECT_NAME_KEYS)
                if value:
                    resolved.coolify_project_name = value
                    context_sources["coolify_project_name"] = f"private-state:{source}"
                elif conflict:
                    context_warnings.append(
                        {
                            "code": "conflicting_private_state_project_name",
                            "message": "Host-level project_name values differ; use a shared coolify.project_name or per-host project_uuid.",
                            "detail": conflict,
                        }
                    )

    if not str(resolved.coolify_fdb_environment_name or "").strip() and not str(resolved.coolify_environment_name or "").strip():
        value, source = _default_state_field(state, args.network, STATE_FDB_ENVIRONMENT_NAME_KEYS)
        if value:
            resolved.coolify_fdb_environment_name = value
            context_sources["coolify_fdb_environment_name"] = f"private-state:{source}"
    if not str(resolved.coolify_hub_environment_name or "").strip() and not str(resolved.coolify_environment_name or "").strip():
        value, source = _default_state_field(state, args.network, STATE_HUB_ENVIRONMENT_NAME_KEYS)
        if value:
            resolved.coolify_hub_environment_name = value
            context_sources["coolify_hub_environment_name"] = f"private-state:{source}"
    if not str(resolved.coolify_environment_name or "").strip():
        value, source = _default_state_field(state, args.network, STATE_ENVIRONMENT_NAME_KEYS)
        if value:
            resolved.coolify_environment_name = value
            context_sources["coolify_environment_name"] = f"private-state:{source}"

    explicit_project_uuids = binding_map(resolved.set_coolify_project_uuid or [], "--set-coolify-project-uuid")
    explicit_server_names = binding_map(resolved.set_coolify_server_name or [], "--set-coolify-server-name")
    explicit_server_uuids = binding_map(resolved.set_coolify_server_uuid or [], "--set-coolify-server-uuid")
    explicit_destination_uuids = binding_map(resolved.set_coolify_destination_uuid or [], "--set-coolify-destination-uuid")
    explicit_environment_uuids = binding_map(resolved.set_coolify_environment_uuid or [], "--set-coolify-environment-uuid")

    for server_name in required_servers:
        host = private_hosts.get(server_name)
        if host is None:
            continue
        _slot, payload, source_prefix = host

        if not str(resolved.coolify_project_uuid or "").strip() and server_name not in explicit_project_uuids:
            value, key = _state_field(payload, STATE_PROJECT_UUID_KEYS)
            if value:
                resolved.set_coolify_project_uuid = _append_binding_if_missing(
                    resolved.set_coolify_project_uuid,
                    "--set-coolify-project-uuid",
                    server_name,
                    value,
                )
                context_sources[f"set_coolify_project_uuid.{server_name}"] = f"private-state:{source_prefix}.{key}"

        if not str(resolved.coolify_server_uuid or "").strip() and server_name not in explicit_server_uuids:
            value, key = _state_field(payload, STATE_SERVER_UUID_KEYS)
            if value:
                resolved.set_coolify_server_uuid = _append_binding_if_missing(
                    resolved.set_coolify_server_uuid,
                    "--set-coolify-server-uuid",
                    server_name,
                    value,
                )
                context_sources[f"set_coolify_server_uuid.{server_name}"] = f"private-state:{source_prefix}.{key}"
        if not str(resolved.coolify_server_name or "").strip() and server_name not in explicit_server_names:
            value, key = _state_field(payload, STATE_SERVER_NAME_KEYS)
            if value:
                resolved.set_coolify_server_name = _append_binding_if_missing(
                    resolved.set_coolify_server_name,
                    "--set-coolify-server-name",
                    server_name,
                    value,
                )
                context_sources[f"set_coolify_server_name.{server_name}"] = f"private-state:{source_prefix}.{key}"

        if not str(resolved.coolify_destination_uuid or "").strip() and server_name not in explicit_destination_uuids:
            value, key = _state_field(payload, STATE_DESTINATION_UUID_KEYS)
            if value:
                resolved.set_coolify_destination_uuid = _append_binding_if_missing(
                    resolved.set_coolify_destination_uuid,
                    "--set-coolify-destination-uuid",
                    server_name,
                    value,
                )
                context_sources[f"set_coolify_destination_uuid.{server_name}"] = f"private-state:{source_prefix}.{key}"

        if not str(resolved.coolify_environment_uuid or "").strip() and server_name not in explicit_environment_uuids:
            value, key = _state_field(payload, STATE_ENVIRONMENT_UUID_KEYS)
            if value:
                resolved.set_coolify_environment_uuid = _append_binding_if_missing(
                    resolved.set_coolify_environment_uuid,
                    "--set-coolify-environment-uuid",
                    server_name,
                    value,
                )
                context_sources[f"set_coolify_environment_uuid.{server_name}"] = f"private-state:{source_prefix}.{key}"

    return resolved


def required_server_names(packet: Mapping[str, Any]) -> list[str]:
    hosts = packet.get("hosts")
    if isinstance(hosts, Mapping):
        return sorted(str(name) for name in hosts)
    return []


def packet_summary(packet: Mapping[str, Any], packet_path: Path) -> dict[str, Any]:
    hubs = [hub for hub in packet.get("hubs", []) if isinstance(hub, Mapping)]
    fdb_instances = []
    foundationdb = packet.get("foundationdb")
    if isinstance(foundationdb, Mapping):
        fdb_instances = [item for item in foundationdb.get("instances", []) if isinstance(item, Mapping)]
    return {
        "path": repo_relative_display(packet_path),
        "network_key": packet.get("network_key"),
        "generation": packet.get("generation"),
        "checksum": (packet.get("checksums") or {}).get("packet_sha256") if isinstance(packet.get("checksums"), Mapping) else "",
        "enabled_hubs": [hub.get("hub_id") for hub in hubs if hub.get("enabled")],
        "enabled_fdb": [item.get("id") for item in fdb_instances if item.get("enabled")],
        "hosts": packet.get("hosts", {}),
        "warnings": packet.get("warnings", []),
    }


def build_candidate_packet(args: argparse.Namespace) -> dict[str, Any]:
    if not str(getattr(args, "hubs", "") or "").strip():
        raise CoolifyClusterError("--hubs is required for cluster preflight/plan/apply.")
    if not str(getattr(args, "fdb", "") or "").strip():
        raise CoolifyClusterError("--fdb is required for cluster preflight/plan/apply.")

    network = clean_network(args.network)
    return packet_tool.build_packet(
        network=network,
        placement_path=placement_path_for_args(args),
        topology_path=topology_path_for_args(args),
        selected_hubs=selected_hub_ids(args),
        selected_fdb=selected_fdb_ids(args),
        generation=args.generation or None,
        intent=args.intent or "",
    )


def fdb_args_for_cluster(args: argparse.Namespace, packet_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        action=args.action,
        network=args.network,
        placement=placement_path_for_args(args),
        packet=packet_path,
        private_state=Path("__main_computer_no_private_state__.yaml") if args.no_private_state else args.private_state,
        set_coolify_url=args.set_coolify_url or [],
        coolify_token=args.coolify_token,
        coolify_token_env=args.coolify_token_env,
        coolify_token_file=args.coolify_token_file,
        set_coolify_token=args.set_coolify_token or [],
        set_coolify_token_env=args.set_coolify_token_env or [],
        set_coolify_token_file=args.set_coolify_token_file or [],
        coolify_project_uuid=args.coolify_project_uuid,
        coolify_project_name=args.coolify_project_name,
        set_coolify_project_uuid=args.set_coolify_project_uuid or [],
        coolify_environment_name=args.coolify_fdb_environment_name or args.coolify_environment_name or f"{args.network}-fdb",
        coolify_environment_uuid=args.coolify_environment_uuid,
        set_coolify_environment_uuid=args.set_coolify_environment_uuid or [],
        no_create_environment=args.no_create_environment,
        coolify_server_name=args.coolify_server_name,
        coolify_server_uuid=args.coolify_server_uuid,
        set_coolify_server_name=args.set_coolify_server_name or [],
        set_coolify_server_uuid=args.set_coolify_server_uuid or [],
        coolify_destination_uuid=args.coolify_destination_uuid,
        set_coolify_destination_uuid=args.set_coolify_destination_uuid or [],
        coolify_timeout_s=args.coolify_timeout_s,
        coolify_retries=args.coolify_retries,
        coolify_retry_sleep_s=args.coolify_retry_sleep_s,
        no_deploy=args.no_deploy,
        force_deploy=args.force_deploy,
        dry_run=args.dry_run,
        json=args.json,
    )


def hub_args_for_cluster(args: argparse.Namespace, packet_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        action=args.action,
        network=args.network,
        placement=placement_path_for_args(args),
        packet=packet_path,
        private_state=Path("__main_computer_no_private_state__.yaml") if args.no_private_state else args.private_state,
        topology=topology_path_for_args(args),
        hubs=args.hubs,
        fdb=args.fdb,
        generation=args.generation,
        intent=args.intent,
        out=str(packet_path),
        no_archive=args.no_archive,
        network_config=args.network_config,
        set_coolify_url=args.set_coolify_url or [],
        coolify_token=args.coolify_token,
        coolify_token_env=args.coolify_token_env,
        coolify_token_file=args.coolify_token_file,
        set_coolify_token=args.set_coolify_token or [],
        set_coolify_token_env=args.set_coolify_token_env or [],
        set_coolify_token_file=args.set_coolify_token_file or [],
        coolify_project_uuid=args.coolify_project_uuid,
        coolify_project_name=args.coolify_project_name,
        set_coolify_project_uuid=args.set_coolify_project_uuid or [],
        coolify_environment_name=args.coolify_hub_environment_name or args.coolify_environment_name or f"{args.network}-hubs",
        coolify_environment_uuid=args.coolify_environment_uuid,
        set_coolify_environment_uuid=args.set_coolify_environment_uuid or [],
        no_create_environment=args.no_create_environment,
        coolify_server_name=args.coolify_server_name,
        coolify_server_uuid=args.coolify_server_uuid,
        set_coolify_server_name=args.set_coolify_server_name or [],
        set_coolify_server_uuid=args.set_coolify_server_uuid or [],
        coolify_destination_uuid=args.coolify_destination_uuid,
        set_coolify_destination_uuid=args.set_coolify_destination_uuid or [],
        git_repo=args.git_repo,
        git_branch=args.git_branch,
        git_commit_sha=args.git_commit_sha,
        base_directory=args.base_directory,
        dockerfile_location=args.dockerfile_location,
        health_path=args.health_path,
        hub_chain_rpc_url=args.hub_chain_rpc_url,
        bridge_backend=args.bridge_backend,
        dev_chain_deployment_path=args.dev_chain_deployment_path,
        contracts_path=args.contracts_path,
        allow_missing_bridge_signer=args.allow_missing_bridge_signer,
        enable_smoke_bridge=args.enable_smoke_bridge,
        enable_bridge_writes=args.enable_bridge_writes,
        sync_bridge_signer=args.sync_bridge_signer,
        bridge_signer_source_manifest=args.bridge_signer_source_manifest,
        bridge_controller_wallet_path=args.bridge_controller_wallet_path,
        bridge_signer_env_key=args.bridge_signer_env_key,
        bridge_signer_remote_path=args.bridge_signer_remote_path,
        coolify_timeout_s=args.coolify_timeout_s,
        coolify_retries=args.coolify_retries,
        coolify_retry_sleep_s=args.coolify_retry_sleep_s,
        no_deploy=args.no_deploy,
        no_traefik_sidecar=args.no_traefik_sidecar,
        force_deploy=args.force_deploy,
        dry_run=args.dry_run,
        json=args.json,
    )


def preflight_issues(args: argparse.Namespace, packet: Mapping[str, Any]) -> list[PreflightIssue]:
    issues: list[PreflightIssue] = []
    packet_path = packet_path_for_args(args)

    if args.fdb_only and args.hubs_only:
        issues.append(
            PreflightIssue(
                "error",
                "conflicting_layer_selection",
                "--fdb-only and --hubs-only cannot both be set.",
                "Remove one of the layer-selection flags.",
            )
        )

    if not args.fdb_only and not str(args.git_repo or "").strip():
        issues.append(
            PreflightIssue(
                "error",
                "missing_git_repo",
                "--git-repo is required for the Hub layer.",
                'Add --git-repo "https://github.com/johnrraymond/main_computer" or pass --fdb-only.',
            )
        )

    if packet_path.exists() and packet_path.is_dir():
        issues.append(
            PreflightIssue(
                "error",
                "packet_path_is_directory",
                f"Packet path is a directory: {repo_relative_display(packet_path)}",
                "Remove the directory or pass --packet to a JSON file path.",
            )
        )
    if packet_path.parent.exists() and not packet_path.parent.is_dir():
        issues.append(
            PreflightIssue(
                "error",
                "packet_parent_is_not_directory",
                f"Packet parent is not a directory: {repo_relative_display(packet_path.parent)}",
                "Replace that path with a directory or pass --packet to another path.",
            )
        )

    explicit_urls = binding_map(args.set_coolify_url or [], "--set-coolify-url")
    explicit_tokens = binding_map(args.set_coolify_token or [], "--set-coolify-token")
    explicit_token_envs = binding_map(args.set_coolify_token_env or [], "--set-coolify-token-env")
    explicit_token_files = binding_map(args.set_coolify_token_file or [], "--set-coolify-token-file")

    required_servers = required_server_names(packet)
    private_hosts: dict[str, tuple[str, Mapping[str, Any], str]] = {}
    private_state_available = False

    if args.no_private_state:
        issues.append(
            PreflightIssue(
                "warning",
                "private_state_disabled",
                "Private state lookup is disabled by --no-private-state.",
                "Make sure every required host has explicit URL and token flags.",
            )
        )
    else:
        state_path = private_state_path_for_args(args)
        state, state_error = _load_yaml_state(state_path)
        if state is None:
            issues.append(
                PreflightIssue(
                    "error",
                    "missing_or_invalid_private_state",
                    f"Could not use private state file {repo_relative_display(state_path)}: {state_error}.",
                    "Create runtime/state/main_computer.private.yaml with coolify.hosts entries for each Coolify host, or pass --no-private-state with explicit --set-coolify-url and token flags.",
                )
            )
        else:
            private_state_available = True
            private_hosts = _private_host_payloads_by_name(state)
            if not isinstance(state.get("coolify"), Mapping):
                issues.append(
                    PreflightIssue(
                        "error",
                        "missing_private_state_coolify_section",
                        f"{repo_relative_display(state_path)} does not contain a coolify mapping.",
                        "Add a coolify.hosts section with entries named coolify-a/coolify-b.",
                    )
                )
            elif not private_hosts:
                issues.append(
                    PreflightIssue(
                        "error",
                        "missing_private_state_hosts",
                        f"{repo_relative_display(state_path)} has no usable coolify.hosts entries.",
                        "Add coolify.hosts.<slot>.name, url, and api_token/api_token_env/api_token_file entries.",
                    )
                )

    for server_name in required_servers:
        if server_name not in explicit_urls:
            if args.no_private_state:
                issues.append(
                    PreflightIssue(
                        "error",
                        "missing_explicit_coolify_url",
                        f"No Coolify API URL is available for {server_name!r}.",
                        f'Pass --set-coolify-url "{server_name}:http://<host>:8000" or remove --no-private-state.',
                    )
                )
            elif not private_state_available:
                continue
            elif server_name not in private_hosts:
                issues.append(
                    PreflightIssue(
                        "error",
                        "missing_private_state_host",
                        f"Private state has no Coolify host entry with name {server_name!r}.",
                        f"Add a coolify.hosts slot with name: {server_name}, url, and token fields.",
                    )
                )
            else:
                _slot, payload, source_prefix = private_hosts[server_name]
                url, _key = _state_field(payload, fdb_tool.PRIVATE_STATE_URL_KEYS)
                if not url:
                    issues.append(
                        PreflightIssue(
                            "error",
                            "missing_private_state_url",
                            f"Private state entry {source_prefix} for {server_name!r} has no URL.",
                            f"Add {source_prefix}.url or pass --set-coolify-url for {server_name}.",
                        )
                    )

        has_explicit_token = (
            server_name in explicit_tokens
            or server_name in explicit_token_envs
            or server_name in explicit_token_files
            or bool(str(args.coolify_token or "").strip())
            or bool(str(args.coolify_token_file or "").strip())
            or bool(str(os.environ.get(args.coolify_token_env) or "").strip())
        )
        if has_explicit_token:
            continue

        if args.no_private_state:
            issues.append(
                PreflightIssue(
                    "error",
                    "missing_explicit_coolify_token",
                    f"No Coolify API token is available for {server_name!r}.",
                    f"Pass --set-coolify-token-env {server_name}:<ENV_VAR>, --set-coolify-token-file, or remove --no-private-state.",
                )
            )
        elif server_name not in private_hosts:
            # The missing host error above already explains the remediation.
            continue
        else:
            _slot, payload, source_prefix = private_hosts[server_name]
            ok, source, detail = _token_field_status(payload, source_prefix)
            if not ok:
                issues.append(
                    PreflightIssue(
                        "error",
                        "missing_private_state_token",
                        f"Private state entry {source_prefix} for {server_name!r} has no usable token. {detail}",
                        f"Add {source_prefix}.api_token, {source_prefix}.api_token_env, or {source_prefix}.api_token_file.",
                    )
                )

    project_uuid_overrides = binding_map(args.set_coolify_project_uuid or [], "--set-coolify-project-uuid")
    missing_project_for = [
        server_name
        for server_name in required_servers
        if not str(args.coolify_project_uuid or "").strip()
        and not str(args.coolify_project_name or "").strip()
        and server_name not in project_uuid_overrides
    ]
    if missing_project_for:
        issues.append(
            PreflightIssue(
                "error",
                "missing_coolify_project",
                "Coolify project is required before apply can resolve/create services for: "
                + ", ".join(missing_project_for),
                "Add coolify.project_name or coolify.project_uuid to runtime/state/main_computer.private.yaml, "
                "add per-host project_uuid values under coolify.hosts, or pass --coolify-project-name/--coolify-project-uuid.",
            )
        )

    server_name_overrides = binding_map(args.set_coolify_server_name or [], "--set-coolify-server-name")
    server_uuid_overrides = binding_map(args.set_coolify_server_uuid or [], "--set-coolify-server-uuid")
    missing_server_selector_for = [
        server_name
        for server_name in required_servers
        if not str(args.coolify_server_uuid or "").strip()
        and not str(args.coolify_server_name or "").strip()
        and server_name not in server_uuid_overrides
        and server_name not in server_name_overrides
    ]
    if missing_server_selector_for:
        issues.append(
            PreflightIssue(
                "warning",
                "coolify_server_may_need_inference",
                "No explicit Coolify server name/UUID is configured for: " + ", ".join(missing_server_selector_for),
                "This is OK only when each target Coolify API has exactly one server. Otherwise add server_name/server_uuid to the matching coolify.hosts slot or pass server flags.",
            )
        )

    destination_uuid_overrides = binding_map(args.set_coolify_destination_uuid or [], "--set-coolify-destination-uuid")
    missing_destination_for = [
        server_name
        for server_name in required_servers
        if not str(args.coolify_destination_uuid or "").strip()
        and server_name not in destination_uuid_overrides
    ]
    if missing_destination_for:
        issues.append(
            PreflightIssue(
                "warning",
                "coolify_destination_may_need_default",
                "No explicit Coolify destination UUID is configured for: " + ", ".join(missing_destination_for),
                "This is OK only when Coolify can infer the destination. Otherwise add destination_uuid to the matching coolify.hosts slot or pass destination flags.",
            )
        )

    for item in getattr(args, "_coolify_context_warnings", []) or []:
        issues.append(
            PreflightIssue(
                "warning",
                str(item.get("code") or "private_state_context_warning"),
                str(item.get("message") or ""),
                str(item.get("detail") or ""),
            )
        )

    for warning in packet.get("warnings", []) if isinstance(packet.get("warnings"), list) else []:
        issues.append(
            PreflightIssue(
                "warning",
                "packet_warning",
                str(warning),
                "Review the selected --hubs/--fdb ids before applying.",
            )
        )

    return issues


def preflight_result(args: argparse.Namespace, packet: Mapping[str, Any]) -> dict[str, Any]:
    resolved_args = args if hasattr(args, "_coolify_context_sources") else resolve_private_state_defaults(args, packet)
    packet_path = packet_path_for_args(resolved_args)
    issues = preflight_issues(resolved_args, packet)
    problems = [issue for issue in issues if issue.level == "error"]
    result = {
        "ok": not problems,
        "network_key": resolved_args.network,
        "packet": packet_summary(packet, packet_path),
        "preflight": {
            "ok": not problems,
            "problems": [issue.as_dict() for issue in problems],
            "warnings": [issue.as_dict() for issue in issues if issue.level != "error"],
            "context_sources": getattr(resolved_args, "_coolify_context_sources", {}) or {},
            "next_commands": next_commands(resolved_args, packet_path) if not problems else remediation_commands(resolved_args),
        },
    }
    return result


def remediation_commands(args: argparse.Namespace) -> list[str]:
    commands = [
        "python .\\tools\\coolify_cluster.py list-components " + args.network,
    ]
    if not args.no_private_state:
        commands.append("python .\\tools\\sync_private_state.py --write --no-live-check")
        commands.append(
            "Edit runtime\\state\\main_computer.private.yaml and add coolify.project_name plus "
            "coolify.hosts entries for each required Coolify host."
        )
    else:
        commands.append(
            'Re-run with --coolify-project-name "Main Computer", --set-coolify-url "<name>:http://<host>:8000", '
            "and token flags, or remove --no-private-state."
        )
    return commands


def next_commands(args: argparse.Namespace, packet_path: Path) -> list[str]:
    base = [
        "python .\\tools\\coolify_cluster.py plan "
        + args.network
        + " --hubs "
        + args.hubs
        + " --fdb "
        + args.fdb
    ]
    if str(args.git_repo or "").strip():
        base[0] += f' --git-repo "{args.git_repo}"'
    if args.no_private_state:
        base[0] += " --no-private-state"
    if getattr(args, "private_state", None):
        base[0] += f' --private-state "{args.private_state}"'
    if getattr(args, "no_traefik_sidecar", False):
        base[0] += " --no-traefik-sidecar"
    apply_command = (
        "python .\\tools\\coolify_cluster.py apply "
        + args.network
        + " --hubs "
        + args.hubs
        + " --fdb "
        + args.fdb
        + (f' --git-repo "{args.git_repo}"' if str(args.git_repo or "").strip() else "")
    )
    if args.no_private_state:
        apply_command += " --no-private-state"
    if getattr(args, "private_state", None):
        apply_command += f' --private-state "{args.private_state}"'
    if getattr(args, "no_traefik_sidecar", False):
        apply_command += " --no-traefik-sidecar"
    base.append(apply_command)
    return base


def write_candidate_packet(args: argparse.Namespace, packet: dict[str, Any]) -> dict[str, Any]:
    return packet_tool.write_packet(packet, packet_path_for_args(args), archive=not args.no_archive)


def run_fdb_stage(args: argparse.Namespace, packet_path: Path) -> dict[str, Any] | None:
    if args.hubs_only:
        return None
    stage_args = fdb_args_for_cluster(args, packet_path)
    placement = fdb_tool.load_fdb_placement_from_packet(packet_path)
    if args.action == "apply":
        return fdb_tool.apply_result(placement, stage_args)
    return {"ok": True, "plan": fdb_tool.plan_result(placement, stage_args)}


def run_hub_stage(args: argparse.Namespace, packet_path: Path) -> dict[str, Any] | None:
    if args.fdb_only:
        return None
    stage_args = hub_args_for_cluster(args, packet_path)
    placement = hub_cluster_tool.load_hub_cluster_placement_from_packet(packet_path)
    profile = hub_cluster_tool.load_network_profile(placement, stage_args)
    if args.action == "apply":
        return hub_cluster_tool.apply_result(placement, profile, stage_args)
    return {"ok": True, "plan": hub_cluster_tool.plan_result(placement, profile, stage_args)}


def cluster_plan_or_apply_result(args: argparse.Namespace, packet: dict[str, Any]) -> dict[str, Any]:
    resolved_args = resolve_private_state_defaults(args, packet)
    check = preflight_result(resolved_args, packet)
    if not check["ok"]:
        return check

    write = write_candidate_packet(resolved_args, packet)
    packet_path = packet_path_for_args(resolved_args)
    fdb = run_fdb_stage(resolved_args, packet_path)
    hub = run_hub_stage(resolved_args, packet_path)

    return {
        "ok": True,
        "action": resolved_args.action,
        "network_key": resolved_args.network,
        "packet": packet_summary(packet, packet_path),
        "write": write,
        "preflight": check["preflight"],
        "stages": {
            "foundationdb": fdb,
            "hub": hub,
        },
        "operator_note": (
            "The candidate packet was written before rendering/applying stages. "
            "Promote it to deploy/packets/<network>-deployed.json only after remote verification succeeds."
        ),
    }


def list_components_result(args: argparse.Namespace) -> dict[str, Any]:
    network = clean_network(args.network)
    placement_path = placement_path_for_args(args)
    topology_path = topology_path_for_args(args)
    return packet_tool.list_components_result(network, placement_path, topology_path)


def print_human_result(result: Mapping[str, Any]) -> None:
    print(json.dumps(result, indent=2, sort_keys=True))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare, preflight, plan, and apply packet-backed Coolify Hub/FDB cluster deploys.",
        allow_abbrev=False,
    )
    parser.add_argument("action", choices=["list-components", "preflight", "plan", "apply"])
    parser.add_argument("network", help="Network key, for example testnet or mainnet.")
    parser.add_argument("--placement", type=Path, default=None, help="Placement JSON. Defaults to deploy/hub-topology/<network>-coolify-deployment.json.")
    parser.add_argument("--topology", type=Path, default=None, help="Optional topology JSON override.")
    parser.add_argument("--packet", type=Path, default=None, help="Override packet path. Defaults to deploy/packets/<network>-packet.json.")
    parser.add_argument("--hubs", "-hubs", default="", help="Comma-separated Hub ids to enable.")
    parser.add_argument("--fdb", "-fdb", default="", help="Comma-separated FoundationDB instance ids to enable.")
    parser.add_argument("--generation", default="", help="Optional packet generation id.")
    parser.add_argument("--intent", default="", help="Optional human-readable operator intent stored in the packet.")
    parser.add_argument("--no-archive", action="store_true", help="Do not archive an existing different packet before writing.")
    parser.add_argument("--fdb-only", action="store_true", help="Only run the FoundationDB stage after packet prep.")
    parser.add_argument("--hubs-only", action="store_true", help="Only run the Hub stage after packet prep.")

    parser.add_argument("--private-state", type=Path, default=None, help="Private state YAML. Defaults to runtime/state/main_computer.private.yaml.")
    parser.add_argument("--no-private-state", action="store_true", help="Disable default private-state lookup and require explicit Coolify URL/token flags.")

    parser.add_argument("--set-coolify-url", action="append", default=[], help="Per-server Coolify API URL. Format: <server-name>:<url>")
    parser.add_argument("--coolify-token", default="", help="One Coolify token for every server. Prefer token env/file/private-state options.")
    parser.add_argument("--coolify-token-env", default=DEFAULT_TOKEN_ENV, help="Default env var containing a Coolify token.")
    parser.add_argument("--coolify-token-file", default="", help="Default file containing a Coolify token.")
    parser.add_argument("--set-coolify-token", action="append", default=[], help="Per-server token. Format: <server-name>:<token>")
    parser.add_argument("--set-coolify-token-env", action="append", default=[], help="Per-server token env var. Format: <server-name>:<ENV_VAR>")
    parser.add_argument("--set-coolify-token-file", action="append", default=[], help="Per-server token file. Format: <server-name>:<path>")

    parser.add_argument("--coolify-project-uuid", default="", help="Coolify project UUID used by all servers unless overridden.")
    parser.add_argument("--coolify-project-name", default="", help="Coolify project name resolved on every server.")
    parser.add_argument("--set-coolify-project-uuid", action="append", default=[], help="Per-server project UUID. Format: <server-name>:<uuid>")
    parser.add_argument("--coolify-environment-name", default="", help="Shared environment name override for both stages.")
    parser.add_argument("--coolify-fdb-environment-name", default="", help="FoundationDB environment name. Defaults to <network>-fdb.")
    parser.add_argument("--coolify-hub-environment-name", default="", help="Hub environment name. Defaults to <network>-hubs.")
    parser.add_argument("--coolify-environment-uuid", default="", help="Coolify environment UUID used by all servers unless overridden.")
    parser.add_argument("--set-coolify-environment-uuid", action="append", default=[], help="Per-server environment UUID. Format: <server-name>:<uuid>")
    parser.add_argument("--no-create-environment", action="store_true", help="Fail if the named environment is missing.")
    parser.add_argument("--coolify-server-name", default="", help="Coolify server name resolved on every Coolify API.")
    parser.add_argument("--coolify-server-uuid", default="", help="Coolify server UUID used by all servers unless overridden.")
    parser.add_argument("--set-coolify-server-name", action="append", default=[], help="Per-server Coolify server name. Format: <server-name>:<coolify-server-name>")
    parser.add_argument("--set-coolify-server-uuid", action="append", default=[], help="Per-server Coolify server UUID. Format: <server-name>:<uuid>")
    parser.add_argument("--coolify-destination-uuid", default="", help="Coolify Docker destination UUID used by all servers unless overridden.")
    parser.add_argument("--set-coolify-destination-uuid", action="append", default=[], help="Per-server destination UUID. Format: <server-name>:<uuid>")

    parser.add_argument("--network-config", type=Path, default=None, help="Path to hub_networks.json.")
    parser.add_argument("--git-repo", default="", help="Git repository URL for remote Hub service builds.")
    parser.add_argument("--git-branch", default="main", help="Git branch to deploy.")
    parser.add_argument("--git-commit-sha", default="", help="Optional exact commit SHA.")
    parser.add_argument("--base-directory", default=hub_cluster_tool.hub_tool.DEFAULT_BASE_DIRECTORY)
    parser.add_argument("--dockerfile-location", default="", help="Dockerfile path. Defaults to /Dockerfile.hub.exp-fdb.")
    parser.add_argument("--health-path", default=hub_cluster_tool.hub_tool.DEFAULT_HEALTH_PATH)
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
    parser.add_argument("--no-deploy", action="store_true", help="Create/update only; do not trigger deploys.")
    parser.add_argument(
        "--no-traefik-sidecar",
        action="store_true",
        help="Disable the default public-entry Traefik sidecar for Hub public_entry_urls.",
    )
    parser.add_argument("--install-traefik-dynamic-config", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--force-deploy", action="store_true", help="Ask Coolify to force rebuild/redeploy services.")
    parser.add_argument("--dry-run", action="store_true", help="For apply: render plans without Coolify calls.")
    parser.add_argument("--json", action="store_true", help="Print compact machine-readable JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        args.network = clean_network(args.network)
        if args.action == "list-components":
            result = list_components_result(args)
        else:
            packet = build_candidate_packet(args)
            if args.action == "preflight":
                result = preflight_result(args, packet)
            else:
                result = cluster_plan_or_apply_result(args, packet)
    except (CoolifyClusterError, CoolifyHubDeployError, HubNetworkConfigError, packet_tool.DeployPacketError) as exc:
        result = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
        print(json.dumps(result, sort_keys=True) if getattr(args, "json", False) else json.dumps(result, indent=2, sort_keys=True))
        return 2

    print(json.dumps(result, sort_keys=True) if args.json else json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
