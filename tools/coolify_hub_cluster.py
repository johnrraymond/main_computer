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
import shlex
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
HUB_SERVICE_TOOL_PATH = Path(__file__).resolve().with_name("coolify_hub_service.py")
FDB_CLUSTER_TOOL_PATH = Path(__file__).resolve().with_name("coolify_fdb_cluster.py")
DEPLOY_PACKET_TOOL_PATH = Path(__file__).resolve().with_name("deploy_packet.py")
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
packet_tool = _load_module("deploy_packet", DEPLOY_PACKET_TOOL_PATH)

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
TRAEFIK_DYNAMIC_CONFIG_REFRESH_S = 300


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
    packet_topology_contents: str = ""
    packet_fdb_cluster_contents: str = ""


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


def coolify_domain_with_backend_port(value: str, backend_port: int, field: str) -> str:
    """Return the Coolify Service Stack domain value for a concrete Hub URL.

    Coolify's Service Stack domain field uses the URL scheme/host for the public
    route and the optional port suffix to identify the container port that should
    receive traffic.  The Hub public_url values are intentionally plain public
    URLs, so the deployer adds the Hub bind port when reconciling Coolify
    domains.
    """

    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise CoolifyHubDeployError(f"{field} must be a valid URL: {value!r}") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CoolifyHubDeployError(f"{field} must be an http(s) URL with a hostname: {value!r}")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}:{int(backend_port)}"


def hub_service_domains(placement: HubClusterPlacement, profile: Any, server_name: str) -> dict[str, dict[str, str]]:
    return {
        hub_container_key(hub): {
            "domain": coolify_domain_with_backend_port(
                hub.public_url,
                int(profile.hub_bind_port),
                f"hubs[{hub.hub_id}].public_url",
            )
        }
        for hub in hubs_for_server(placement, server_name)
    }


def hub_application_name(placement: HubClusterPlacement, hub: HubPlacement) -> str:
    # hub_id already carries the network prefix (for example mainnet-hub1).
    return f"main-computer-{hub_container_key(hub)}"


def hub_application_profile(profile: Any, hub: HubPlacement) -> Any:
    return replace(profile, hub_public_url=hub.public_url, hub_runtime_dir=Path(hub.runtime_dir))


def application_args_for_hub(args: argparse.Namespace, context: dict[str, Any], hub: HubPlacement) -> argparse.Namespace:
    values = dict(vars(args))
    values.update(
        {
            "coolify_project_uuid": context.get("project_uuid") or values.get("coolify_project_uuid", ""),
            "coolify_server_uuid": context.get("server_uuid") or values.get("coolify_server_uuid", ""),
            "coolify_environment_name": context.get("environment_name") or values.get("coolify_environment_name", ""),
            "coolify_environment_uuid": context.get("environment_uuid") or values.get("coolify_environment_uuid", ""),
            "coolify_application_uuid": "",
            "github_app_uuid": values.get("github_app_uuid", ""),
            "deploy_key_uuid": values.get("deploy_key_uuid", ""),
            "no_create_storage": bool(values.get("no_create_storage", False)),
            "hub_implementation": getattr(hub_tool, "HUB_IMPLEMENTATION_EXP_FDB", "exp-fdb"),
            "hub_runtime_dir": hub.runtime_dir,
            "fdb_cluster_file": hub.cluster_file_path,
            "fdb_namespace": hub.namespace,
            "replace_regular_hub": False,
        }
    )
    return argparse.Namespace(**values)


def hub_application_start_command(placement: HubClusterPlacement, profile: Any, hub: HubPlacement, args: argparse.Namespace) -> str:
    command = hub_command_parts(profile, placement, hub, args)
    return " ".join(hub_tool.command_token(part) for part in command)


def hub_application_payload(
    placement: HubClusterPlacement,
    profile: Any,
    args: argparse.Namespace,
    *,
    hub: HubPlacement,
    server_name: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    app_profile = hub_application_profile(profile, hub)
    app_args = application_args_for_hub(args, context, hub)
    name = hub_application_name(placement, hub)
    payload = hub_tool.application_payload(app_profile, app_args, service_name=name, runtime_dir=hub.runtime_dir)
    payload.update(
        {
            "name": name,
            "description": f"Main Computer {placement.network_key} Hub {hub.hub_id} on {server_name}",
            "domains": coolify_domain_with_backend_port(hub.public_url, int(profile.hub_bind_port), f"hubs[{hub.hub_id}].public_url"),
            "ports_exposes": str(profile.hub_bind_port),
            "start_command": hub_application_start_command(placement, profile, hub, app_args),
            "health_check_enabled": True,
            "health_check_path": args.health_path,
            "instant_deploy": False,
        }
    )
    return {key: value for key, value in payload.items() if value not in (None, "")}


def hub_application_update_payload(
    placement: HubClusterPlacement,
    profile: Any,
    args: argparse.Namespace,
    *,
    hub: HubPlacement,
    server_name: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    payload = hub_application_payload(placement, profile, args, hub=hub, server_name=server_name, context=context)
    for key in ("project_uuid", "server_uuid", "environment_name", "environment_uuid", "git_repository"):
        payload.pop(key, None)
    return payload


def hub_application_endpoint(args: argparse.Namespace) -> str:
    if str(getattr(args, "github_app_uuid", "") or "").strip():
        return "/api/v1/applications/private-github-app"
    if str(getattr(args, "deploy_key_uuid", "") or "").strip():
        return "/api/v1/applications/private-deploy-key"
    return "/api/v1/applications/public"


def _create_payload_with_auth(payload: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    result = dict(payload)
    if str(getattr(args, "github_app_uuid", "") or "").strip():
        result["github_app_uuid"] = str(args.github_app_uuid).strip()
    if str(getattr(args, "deploy_key_uuid", "") or "").strip():
        result["private_key_uuid"] = str(args.deploy_key_uuid).strip()
    return result


def _minimal_application_create_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "project_uuid",
        "server_uuid",
        "environment_name",
        "environment_uuid",
        "github_app_uuid",
        "private_key_uuid",
        "git_repository",
        "git_branch",
        "build_pack",
        "ports_exposes",
        "destination_uuid",
        "name",
        "description",
        "git_commit_sha",
        "base_directory",
        "dockerfile_location",
    }
    return {key: value for key, value in payload.items() if key in allowed and value not in (None, "")}


def _application_create_payload_variants(payload: dict[str, Any], args: argparse.Namespace) -> list[tuple[str, dict[str, Any]]]:
    full = _create_payload_with_auth(payload, args)
    start_command_create_payload = {
        key: value
        for key, value in full.items()
        if key
        not in {
            "domains",
            "health_check_enabled",
            "health_check_path",
            "health_check_port",
            "health_check_host",
            "health_check_method",
            "health_check_return_code",
            "health_check_scheme",
            "health_check_response_text",
            "health_check_interval",
            "health_check_timeout",
            "health_check_retries",
            "health_check_start_period",
            "instant_deploy",
        }
    }
    post_create_payload = {
        key: value
        for key, value in full.items()
        if key
        not in {
            "domains",
            "start_command",
            "health_check_enabled",
            "health_check_path",
            "health_check_port",
            "health_check_host",
            "health_check_method",
            "health_check_return_code",
            "health_check_scheme",
            "health_check_response_text",
            "health_check_interval",
            "health_check_timeout",
            "health_check_retries",
            "health_check_start_period",
            "instant_deploy",
        }
    }
    minimal = _minimal_application_create_payload(full)
    variants: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for name, candidate in (
        ("full", full),
        ("domains-and-health-deferred", start_command_create_payload),
        ("post-create-fields-deferred", post_create_payload),
        ("minimal", minimal),
    ):
        clean = {key: value for key, value in candidate.items() if value not in (None, "")}
        fingerprint = json.dumps(clean, sort_keys=True, default=str)
        if fingerprint not in seen:
            seen.add(fingerprint)
            variants.append((name, clean))
    return variants


def _application_create_attempt_summary(variant: str, payload: dict[str, Any], response: Any) -> dict[str, Any]:
    return {
        "variant": variant,
        "payload_keys": sorted(payload),
        "domains": payload.get("domains"),
        "build_pack": payload.get("build_pack"),
        "ports_exposes": payload.get("ports_exposes"),
        "base_directory": payload.get("base_directory"),
        "dockerfile_location": payload.get("dockerfile_location"),
        "has_start_command": "start_command" in payload,
        "has_health_check": any(str(key).startswith("health_check_") for key in payload),
        "response": hub_tool.response_to_dict(response),
    }


def create_hub_application(client: Any, payload: dict[str, Any], args: argparse.Namespace, tried: list[dict[str, Any]]) -> str:
    endpoint = hub_application_endpoint(args)
    application_name = str(payload.get("name") or "").strip()
    failures: list[dict[str, Any]] = []
    for variant, create_payload in _application_create_payload_variants(payload, args):
        response = client.request("POST", endpoint, create_payload)
        attempt = {
            "operation": "create-hub-application",
            "path": endpoint,
            **_application_create_attempt_summary(variant, create_payload, response),
        }
        tried.append(attempt)
        if response.ok:
            uuid = hub_tool.item_uuid(response.body) if isinstance(response.body, dict) else ""
            if not uuid and isinstance(response.body, dict) and isinstance(response.body.get("application"), dict):
                uuid = hub_tool.item_uuid(response.body["application"])
            if not uuid:
                raise CoolifyHubDeployError(
                    f"Coolify Hub application create succeeded with variant {variant!r} but no UUID was returned: {response.body}"
                )
            return uuid

        failures.append(attempt)
        if response.status >= 500 and application_name:
            application_uuid, existing = hub_tool.find_application(
                client,
                service_name=application_name,
                explicit_uuid="",
                tried=tried,
            )
            if application_uuid:
                tried.append(
                    {
                        "operation": "create-hub-application-recovered-existing-after-server-error",
                        "application_name": application_name,
                        "application_uuid": application_uuid,
                        "existing": existing,
                        "failed_variant": variant,
                    }
                )
                return application_uuid
        if response.status not in {400, 409, 422, 500}:
            break

    raise CoolifyHubDeployError(
        "Coolify Hub application create failed on all payload variants: "
        + json.dumps(
            [
                {
                    "variant": item.get("variant"),
                    "payload_keys": item.get("payload_keys"),
                    "domains": item.get("domains"),
                    "build_pack": item.get("build_pack"),
                    "ports_exposes": item.get("ports_exposes"),
                    "base_directory": item.get("base_directory"),
                    "dockerfile_location": item.get("dockerfile_location"),
                    "has_start_command": item.get("has_start_command"),
                    "has_health_check": item.get("has_health_check"),
                    "response": item.get("response"),
                }
                for item in failures
            ],
            sort_keys=True,
        )
    )


def _clean_payload_subset(payload: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: payload[key] for key in keys if key in payload and payload[key] not in (None, "")}


def _application_update_attempt_summary(label: str, method: str, path: str, payload: dict[str, Any], response: Any) -> dict[str, Any]:
    return {
        "variant": label,
        "method": method,
        "path": path,
        "payload_keys": sorted(payload),
        "domains": payload.get("domains"),
        "fqdn": payload.get("fqdn"),
        "ports_exposes": payload.get("ports_exposes"),
        "has_start_command": "start_command" in payload,
        "has_health_check": any(str(key).startswith("health_check_") for key in payload),
        "response": hub_tool.response_to_dict(response),
    }


def _try_hub_application_update_variants(
    client: Any,
    application_uuid: str,
    variants: list[tuple[str, dict[str, Any]]],
    tried: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    path = f"/api/v1/applications/{urllib.parse.quote(application_uuid)}"
    failures: list[dict[str, Any]] = []
    seen: set[str] = set()
    for label, raw_payload in variants:
        payload = {key: value for key, value in raw_payload.items() if value not in (None, "")}
        if not payload:
            continue
        fingerprint = json.dumps(payload, sort_keys=True, default=str)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        for method in ("PATCH", "PUT"):
            response = client.request(method, path, payload)
            attempt = {
                "operation": "update-hub-application",
                **_application_update_attempt_summary(label, method, path, payload, response),
            }
            tried.append(attempt)
            if response.ok:
                return {
                    "ok": True,
                    "variant": label,
                    "method": method,
                    "path": path,
                    "payload_keys": sorted(payload),
                    "domains": payload.get("domains") or payload.get("fqdn"),
                    "has_start_command": "start_command" in payload,
                }, failures
            failures.append(attempt)
            if response.status == 405:
                break
            if response.status not in {400, 404, 405, 422, 500}:
                return None, failures
    return None, failures


def update_hub_application(client: Any, application_uuid: str, payload: dict[str, Any], tried: list[dict[str, Any]]) -> dict[str, Any]:
    domain = str(payload.get("domains") or "").strip()
    fqdn_domain = domain
    start_command = str(payload.get("start_command") or "").strip()
    port = str(payload.get("ports_exposes") or "").strip()

    all_in_one_variants = [
        ("full", payload),
        (
            "runtime-and-routing",
            _clean_payload_subset(
                payload,
                (
                    "name",
                    "description",
                    "domains",
                    "ports_exposes",
                    "start_command",
                    "health_check_enabled",
                    "health_check_path",
                ),
            ),
        ),
        (
            "routing-and-command",
            _clean_payload_subset(payload, ("domains", "ports_exposes", "start_command")),
        ),
    ]
    all_in_one_result, all_in_one_failures = _try_hub_application_update_variants(
        client,
        application_uuid,
        all_in_one_variants,
        tried,
    )
    if all_in_one_result is not None:
        return {
            "ok": True,
            "strategy": "single-update",
            "domains": all_in_one_result.get("domains"),
            "results": [all_in_one_result],
        }

    domain_variants: list[tuple[str, dict[str, Any]]] = []
    if domain:
        domain_variants.extend(
            [
                ("domains-and-port", {"domains": domain, "ports_exposes": port}),
                ("domains-only", {"domains": domain}),
                # Some Coolify versions read the value as fqdn but write it as
                # domains; keep fqdn as a last-resort compatibility probe so
                # the failure report distinguishes field-name rejection from
                # server-side crashes.
                ("fqdn-only", {"fqdn": fqdn_domain}),
            ]
        )
    domain_result, domain_failures = _try_hub_application_update_variants(
        client,
        application_uuid,
        domain_variants,
        tried,
    )
    if domain and domain_result is None:
        raise CoolifyHubDeployError(
            "Coolify Hub application domain update failed on all payload variants: "
            + json.dumps(
                [
                    {
                        "variant": item.get("variant"),
                        "method": item.get("method"),
                        "path": item.get("path"),
                        "payload_keys": item.get("payload_keys"),
                        "domains": item.get("domains"),
                        "fqdn": item.get("fqdn"),
                        "ports_exposes": item.get("ports_exposes"),
                        "response": item.get("response"),
                    }
                    for item in [*all_in_one_failures, *domain_failures]
                ],
                sort_keys=True,
            )
        )

    command_result = None
    command_failures: list[dict[str, Any]] = []
    if start_command:
        command_variants = [
            ("start-command-and-port", {"start_command": start_command, "ports_exposes": port}),
            ("start-command-only", {"start_command": start_command}),
        ]
        command_result, command_failures = _try_hub_application_update_variants(
            client,
            application_uuid,
            command_variants,
            tried,
        )

    health_result = None
    if payload.get("health_check_enabled") or payload.get("health_check_path"):
        health_payload = _clean_payload_subset(
            payload,
            (
                "health_check_enabled",
                "health_check_path",
                "health_check_port",
                "health_check_host",
                "health_check_method",
                "health_check_return_code",
                "health_check_scheme",
                "health_check_response_text",
                "health_check_interval",
                "health_check_timeout",
                "health_check_retries",
                "health_check_start_period",
            ),
        )
        health_result, _health_failures = _try_hub_application_update_variants(
            client,
            application_uuid,
            [("health-only", health_payload)],
            tried,
        )

    command_warning: dict[str, Any] | None = None
    if start_command and command_result is None:
        command_failure_summary = [
            {
                "variant": item.get("variant"),
                "method": item.get("method"),
                "path": item.get("path"),
                "payload_keys": item.get("payload_keys"),
                "has_start_command": item.get("has_start_command"),
                "response": item.get("response"),
            }
            for item in command_failures
        ]
        if domain_result is None:
            # When there was no successful routing/domain update to preserve,
            # keep start command failures fatal.  Otherwise the caller could
            # deploy an application that is neither correctly routed nor
            # guaranteed to boot as the requested Hub.
            raise CoolifyHubDeployError(
                "Coolify Hub application start command update failed on all payload variants: "
                + json.dumps(command_failure_summary, sort_keys=True)
            )

        # Some Coolify versions accept the split domain/port PATCH but crash
        # when start_command is updated on an existing Dockerfile application.
        # Keep the already-accepted domain:port reconciliation and make the
        # skipped command update explicit in the result instead of failing the
        # whole Hub pass after routing has been repaired.
        command_warning = {
            "operation": "start-command-update",
            "ok": False,
            "message": (
                "Coolify accepted the Hub domain/port update but rejected all "
                "start_command update variants. The deployer kept the routing "
                "change and reported this warning instead of aborting after the "
                "domain was already reconciled."
            ),
            "failures": command_failure_summary,
        }

    result = {
        "ok": True,
        "strategy": "split-updates",
        "domains": domain_result.get("domains") if domain_result else "",
        "results": [
            item
            for item in (domain_result, command_result, health_result)
            if item is not None
        ],
    }
    if command_warning is not None:
        result["command_update_failed"] = True
        result["warnings"] = [command_warning]
    return result


def hub_status_url(hub: HubPlacement, health_path: str) -> str:
    base = str(hub.public_url or "").strip().rstrip("/")
    path = str(health_path or "/api/hub/status").strip() or "/api/hub/status"
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def wait_for_hub_ready(
    placement: HubClusterPlacement,
    profile: Any,
    args: argparse.Namespace,
    *,
    hub: HubPlacement,
    tried: list[dict[str, Any]],
) -> dict[str, Any]:
    timeout_s = float(getattr(args, "hub_wait_timeout_s", 300.0))
    if timeout_s <= 0:
        return {"ok": True, "skipped": True, "reason": "hub_wait_timeout_s <= 0", "hub_id": hub.hub_id}
    if bool(getattr(args, "no_wait_hubs", False)):
        return {"ok": True, "skipped": True, "reason": "--no-wait-hubs", "hub_id": hub.hub_id}

    status_url = hub_status_url(hub, getattr(args, "health_path", "/api/hub/status"))
    deadline = time.monotonic() + timeout_s
    poll_s = float(getattr(args, "hub_wait_poll_s", 5.0))
    status_timeout_s = float(getattr(args, "hub_status_timeout_s", 5.0))
    user_agent = str(getattr(args, "hub_status_user_agent", hub_tool.DEFAULT_JSON_RPC_USER_AGENT) or "").strip()
    last_error: object = None
    attempts = 0

    while time.monotonic() < deadline:
        attempts += 1
        try:
            request = hub_tool.hub_status_request(status_url, user_agent=user_agent)
            with urllib.request.urlopen(request, timeout=status_timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
            network = payload.get("network") if isinstance(payload, dict) else {}
            if isinstance(network, dict):
                network_key = network.get("network_key") or network.get("network")
                chain_id = network.get("chain_id")
                if network_key == placement.network_key and int(chain_id) == int(profile.chain_id or -1):
                    result = {
                        "ok": True,
                        "hub_id": hub.hub_id,
                        "status_url": status_url,
                        "attempts": attempts,
                        "status": payload,
                    }
                    tried.append({"operation": "wait-hub-ready", **result})
                    return result
                last_error = f"unexpected Hub status network={network_key!r} chain_id={chain_id!r}"
            else:
                last_error = "Hub status response has no network object"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        tried.append(
            {
                "operation": "wait-hub-ready",
                "hub_id": hub.hub_id,
                "status_url": status_url,
                "attempt": attempts,
                "ok": False,
                "last_error": str(last_error),
            }
        )
        time.sleep(max(0.0, poll_s))

    raise CoolifyHubDeployError(f"Hub {hub.hub_id!r} did not become ready at {status_url}: {last_error}")


def sync_hub_application(
    client: Any,
    placement: HubClusterPlacement,
    profile: Any,
    args: argparse.Namespace,
    *,
    server_name: str,
    hub: HubPlacement,
    context: dict[str, Any],
    tried: list[dict[str, Any]],
) -> dict[str, Any]:
    app_args = application_args_for_hub(args, context, hub)
    app_profile = hub_application_profile(profile, hub)
    name = hub_application_name(placement, hub)
    application_uuid, existing = hub_tool.find_application(client, service_name=name, explicit_uuid="", tried=tried)
    if application_uuid:
        update_result = update_hub_application(
            client,
            application_uuid,
            hub_application_update_payload(placement, profile, args, hub=hub, server_name=server_name, context=context),
            tried,
        )
        action = "updated"
    else:
        create_payload = hub_application_payload(placement, profile, args, hub=hub, server_name=server_name, context=context)
        application_uuid = create_hub_application(
            client,
            create_payload,
            app_args,
            tried,
        )
        update_result = update_hub_application(
            client,
            application_uuid,
            hub_application_update_payload(placement, profile, args, hub=hub, server_name=server_name, context=context),
            tried,
        )
        update_result = {**update_result, "created": True}
        action = "created"
    storage = hub_tool.ensure_storage(client, app_profile, app_args, application_uuid=application_uuid, runtime_dir=hub.runtime_dir, tried=tried)
    signer_sync_result = None
    if hub_tool.bridge_signer_sync_requested(app_args):
        signer_sync_result = hub_tool.sync_bridge_signer_application_env(
            client,
            app_profile,
            app_args,
            application_uuid=application_uuid,
            tried=tried,
        )
    deploy_result = None
    ready_result = None
    if not args.no_deploy:
        deploy_result = hub_tool.trigger_deploy(client, application_uuid=application_uuid, force=args.force_deploy, tried=tried)
        ready_result = wait_for_hub_ready(placement, profile, args, hub=hub, tried=tried)
    return {
        "ok": True,
        "hub_id": hub.hub_id,
        "server": server_name,
        "application_name": name,
        "application_uuid": application_uuid,
        "application_action": action,
        "existing": existing,
        "update_result": update_result,
        "storage": storage,
        "bridge_signer_sync": signer_sync_result,
        "deployed": deploy_result is not None,
        "deploy_result": deploy_result,
        "ready_result": ready_result,
        "domains": coolify_domain_with_backend_port(hub.public_url, int(profile.hub_bind_port), f"hubs[{hub.hub_id}].public_url"),
    }


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


def traefik_sidecar_enabled(args: argparse.Namespace) -> bool:
    """Return whether the public-entry Traefik sidecar should be rendered.

    The sidecar is enabled by default because it keeps the shared public entry
    hostname aligned with the active packet.  Operators can explicitly disable it
    with --no-traefik-sidecar.
    """

    return not bool(getattr(args, "no_traefik_sidecar", False))


def render_server_traefik_dynamic_config(placement: HubClusterPlacement, profile: Any, args: argparse.Namespace, server_name: str) -> str:
    local_hubs = hubs_for_server(placement, server_name)
    if not local_hubs:
        raise CoolifyHubDeployError(f"No hubs are assigned to server {server_name!r}.")
    hosts = shared_entry_hosts(placement)
    if not hosts:
        raise CoolifyHubDeployError(
            "Traefik sidecar requires at least one public_entry_urls[] value in the placement."
        )

    middleware_prefix = router_id(f"{placement.network_key}-hub-public-entry")
    lines: list[str] = [
        "# Generated by tools/coolify_hub_cluster.py Traefik sidecar.",
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
                "        servers:",
            ]
        )
        for hub in local_hubs:
            lines.append(f"          - url: {yaml_quote(f'http://{hub_container_key(hub)}:{profile.hub_bind_port}')}")
    return "\n".join(lines) + "\n"


def render_traefik_dynamic_config_writer_script(
    placement: HubClusterPlacement,
    profile: Any,
    args: argparse.Namespace,
    server_name: str,
) -> str:
    config_path = traefik_dynamic_config_path(placement, server_name)
    config = render_server_traefik_dynamic_config(placement, profile, args, server_name).rstrip("\n")
    refresh_s = max(30, int(TRAEFIK_DYNAMIC_CONFIG_REFRESH_S))
    return "\n".join(
        [
            "set -eu",
            f"CONFIG_PATH={sh_quote(config_path)}",
            f"CONFIG_DIR={sh_quote(TRAEFIK_DYNAMIC_CONFIG_DIR)}",
            f"REFRESH_SECONDS={refresh_s}",
            "write_config() {",
            '  mkdir -p "$$CONFIG_DIR"',
            '  tmp="$${CONFIG_PATH}.tmp"',
            "  cat > \"$$tmp\" <<'TRAEFIKDYNAMICCONFIG'",
            config,
            "TRAEFIKDYNAMICCONFIG",
            '  mv "$$tmp" "$$CONFIG_PATH"',
            '  echo "Installed Traefik dynamic config: $$CONFIG_PATH"',
            "}",
            "write_config",
            'while true; do sleep "$$REFRESH_SECONDS"; write_config; done',
        ]
    )


def render_traefik_dynamic_config_cleanup_script(placement: HubClusterPlacement, server_name: str) -> str:
    config_path = traefik_dynamic_config_path(placement, server_name)
    refresh_s = max(30, int(TRAEFIK_DYNAMIC_CONFIG_REFRESH_S))
    return "\n".join(
        [
            "set -eu",
            f"CONFIG_PATH={sh_quote(config_path)}",
            f"REFRESH_SECONDS={refresh_s}",
            'rm -f "$$CONFIG_PATH"',
            'echo "Removed stale Traefik dynamic config: $$CONFIG_PATH"',
            'while true; do sleep "$$REFRESH_SECONDS"; rm -f "$$CONFIG_PATH"; done',
        ]
    )


def append_traefik_dynamic_config_service(
    lines: list[str],
    placement: HubClusterPlacement,
    profile: Any,
    args: argparse.Namespace,
    server_name: str,
    *,
    local_hubs: list[HubPlacement],
) -> None:
    installer_key = traefik_dynamic_config_service_key(placement, server_name)
    config_path = traefik_dynamic_config_path(placement, server_name)
    if local_hubs:
        installer_script = render_traefik_dynamic_config_writer_script(placement, profile, args, server_name)
        healthcheck = (
            f"test -s {shlex.quote(config_path)} "
            f"&& grep -Fq -- {shlex.quote(shared_entry_hosts(placement)[0])} {shlex.quote(config_path)}"
        )
    else:
        installer_script = render_traefik_dynamic_config_cleanup_script(placement, server_name)
        healthcheck = f"test ! -e {shlex.quote(config_path)}"

    lines.extend(
        [
            f"  {installer_key}:",
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
            f"      - {yaml_quote(installer_script)}",
            "    healthcheck:",
            f'      test: ["CMD-SHELL", {yaml_quote(healthcheck)}]',
            "      interval: 30s",
            "      timeout: 5s",
            "      start_period: 10s",
            "      retries: 5",
            "",
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


def packet_path_for_network_arg(network: str) -> Path:
    return packet_tool.packet_path_for_network(packet_tool.clean_identifier(network, "network"))


def load_hub_cluster_placement_from_args(args: argparse.Namespace) -> HubClusterPlacement:
    if getattr(args, "packet", None):
        return load_hub_cluster_placement_from_packet(repo_relative_path(args.packet))
    network = str(getattr(args, "network", "") or "").strip()
    if network:
        return load_hub_cluster_placement_from_packet(packet_path_for_network_arg(network))
    return load_hub_cluster_placement(repo_relative_path(args.placement))


def load_hub_cluster_placement_from_packet(path: Path) -> HubClusterPlacement:
    packet = packet_tool.load_packet(path)
    source = packet.get("source") if isinstance(packet.get("source"), dict) else {}
    placement_path = repo_relative_path(packet_tool.clean_required_string(source.get("placement_path"), "source.placement_path"))
    placement = load_hub_cluster_placement(placement_path)
    enabled_hubs = packet_tool.packet_enabled_hub_ids(packet)
    hubs = tuple(hub for hub in placement.hubs if hub.hub_id in enabled_hubs)
    if not hubs:
        raise CoolifyHubDeployError("Deploy packet must enable at least one Hub.")
    return HubClusterPlacement(
        network_key=placement.network_key,
        topology_path=placement.topology_path,
        topology_container_path=packet_tool.packet_hub_topology_path(packet),
        cluster_file_path=placement.cluster_file_path,
        namespace=placement.namespace,
        servers=placement.servers,
        hubs=hubs,
        public_entry_urls=placement.public_entry_urls,
        topology_cluster_id=placement.topology_cluster_id,
        packet_topology_contents=packet_tool.packet_hub_topology_json(packet),
        packet_fdb_cluster_contents=packet_tool.packet_fdb_cluster_contents(packet),
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
    url, _url_source = fdb_tool.coolify_url_for_server(server_name, args)
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


def render_packet_hub_start_script(placement: HubClusterPlacement, hub: HubPlacement, command: list[str]) -> str:
    topology_contents = placement.packet_topology_contents.rstrip("\n")
    fdb_cluster_contents = placement.packet_fdb_cluster_contents.rstrip("\n")
    topology_dir = fdb_tool.posix_dirname(placement.topology_container_path)
    cluster_dir = fdb_tool.posix_dirname(hub.cluster_file_path)
    lines = [
        "set -eu",
        f"mkdir -p {sh_quote(topology_dir)} {sh_quote(cluster_dir)}",
        f"cat > {sh_quote(placement.topology_container_path)} <<'MAINCOMPUTERTOPOLOGY'",
        topology_contents,
        "MAINCOMPUTERTOPOLOGY",
        f"printf '%s\\n' {sh_quote(fdb_cluster_contents)} > {sh_quote(hub.cluster_file_path)}",
        "exec " + shlex.join(command),
    ]
    return "\n".join(lines)


def hub_container_command_parts(placement: HubClusterPlacement, hub: HubPlacement, command: list[str]) -> list[str]:
    if not placement.packet_topology_contents:
        return command
    return ["/bin/sh", "-euc", render_packet_hub_start_script(placement, hub, command)]


def render_disabled_hub_compose(placement: HubClusterPlacement, profile: Any, args: argparse.Namespace, server_name: str) -> str:
    marker_key = service_key(f"{placement.network_key}-hubs-disabled")
    script_lines = [
        "set -eu",
        f"echo 'No Hub instances are enabled for {placement.network_key} on {server_name}.'",
    ]
    if placement.packet_topology_contents:
        topology_dir = fdb_tool.posix_dirname(placement.topology_container_path)
        cluster_dir = fdb_tool.posix_dirname(placement.cluster_file_path)
        script_lines.extend(
            [
                f"mkdir -p {sh_quote(topology_dir)} {sh_quote(cluster_dir)}",
                f"cat > {sh_quote(placement.topology_container_path)} <<'MAINCOMPUTERTOPOLOGY'",
                placement.packet_topology_contents.rstrip("\n"),
                "MAINCOMPUTERTOPOLOGY",
                f"printf '%s\\n' {sh_quote(placement.packet_fdb_cluster_contents.rstrip(chr(10)))} > {sh_quote(placement.cluster_file_path)}",
            ]
        )
    script_lines.append("tail -f /dev/null")
    script = "\n".join(script_lines)
    lines = [
        f"name: {hub_service_name(placement, server_name)}",
        "",
        "services:",
        f"  {marker_key}:",
        "    image: alpine:3.20",
        "    restart: unless-stopped",
        "    command:",
        "      - /bin/sh",
        "      - -euc",
        f"      - {yaml_quote(script)}",
        "    healthcheck:",
        "      test: [\"CMD-SHELL\", \"true\"]",
        "      interval: 30s",
        "      timeout: 5s",
        "      start_period: 5s",
        "      retries: 3",
        "",
    ]
    if traefik_sidecar_enabled(args) and shared_entry_hosts(placement):
        append_traefik_dynamic_config_service(lines, placement, profile, args, server_name, local_hubs=[])
    return "\n".join(lines)



def render_server_hub_compose(placement: HubClusterPlacement, profile: Any, args: argparse.Namespace, server_name: str) -> str:
    local_hubs = hubs_for_server(placement, server_name)
    if not local_hubs:
        return render_disabled_hub_compose(placement, profile, args, server_name)
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
        command = hub_container_command_parts(placement, hub, hub_command_parts(profile, placement, hub, args))
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

    if traefik_sidecar_enabled(args) and shared_entry_hosts(placement):
        append_traefik_dynamic_config_service(lines, placement, profile, args, server_name, local_hubs=local_hubs)
    return "\n".join(lines)


def service_payload(placement: HubClusterPlacement, profile: Any, args: argparse.Namespace, *, server_name: str, context: dict[str, Any]) -> dict[str, Any]:
    service_name = hub_service_name(placement, server_name)
    compose = render_server_hub_compose(placement, profile, args, server_name)
    destination_overrides = fdb_tool.parse_binding_map(args.set_coolify_destination_uuid or [], "--set-coolify-destination-uuid")
    destination_uuid = destination_overrides.get(server_name) or args.coolify_destination_uuid
    domains = hub_service_domains(placement, profile, server_name)
    payload: dict[str, Any] = {
        "server_uuid": context.get("server_uuid"),
        "project_uuid": context.get("project_uuid"),
        "environment_name": context.get("environment_name") or args.coolify_environment_name,
        "environment_uuid": context.get("environment_uuid") or args.coolify_environment_uuid,
        "name": service_name,
        "description": f"Main Computer {placement.network_key} Hub containers on {server_name}",
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "docker_compose_domains": domains,
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


def update_service(
    client: Any,
    service_uuid: str,
    service_name: str,
    compose: str,
    tried: list[dict[str, Any]],
    *,
    domains: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    encoded = base64.b64encode(compose.encode("utf-8")).decode("ascii")
    clean_domains = domains or {}
    update_payloads: list[dict[str, Any]] = []
    if clean_domains:
        # Coolify Service Stack domains are stored as docker_compose_domains,
        # keyed by compose service name.  Include them before the compose-only
        # fallbacks so deployments can recreate the per-service Domains UI
        # entries that operators previously had to set by hand.
        update_payloads.extend(
            [
                {"docker_compose_raw": encoded, "docker_compose_domains": clean_domains, "name": service_name},
                {"docker_compose_raw": encoded, "docker_compose_domains": clean_domains},
                {"docker_compose": compose, "docker_compose_domains": clean_domains, "name": service_name},
            ]
        )
    update_payloads.extend(
        [
            {"docker_compose_raw": encoded, "name": service_name},
            {"docker_compose_raw": encoded},
            {"docker_compose": compose, "name": service_name},
            {"compose": compose, "name": service_name},
        ]
    )
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
                    "domain_payload": payload.get("docker_compose_domains", None),
                    "response": hub_tool.response_to_dict(response),
                }
            )
            if response.ok:
                return {
                    "ok": True,
                    "path": path,
                    "method": "PATCH",
                    "domains_included": "docker_compose_domains" in payload,
                }
            if response.status == 405:
                response = client.request("PUT", path, payload)
                tried.append(
                    {
                        "operation": "update-hub-service",
                        "method": "PUT",
                        "path": path,
                        "payload_keys": sorted(payload),
                        "domain_payload": payload.get("docker_compose_domains", None),
                        "response": hub_tool.response_to_dict(response),
                    }
                )
                if response.ok:
                    return {
                        "ok": True,
                        "path": path,
                        "method": "PUT",
                        "domains_included": "docker_compose_domains" in payload,
                    }
            if response.status not in {400, 404, 405, 422}:
                raise CoolifyHubDeployError(f"Coolify Hub service update failed with HTTP {response.status}: {response.body}")
    raise CoolifyHubDeployError("Coolify Hub service update failed on all known endpoints.")



def _body_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _body_list(value: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in (*keys, "data", "items", "applications", "resources"):
            item = value.get(key)
            if isinstance(item, list):
                return [entry for entry in item if isinstance(entry, dict)]
    return []


def _application_names(application: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("name", "human_name", "service_name", "description"):
        value = application.get(key)
        if isinstance(value, str) and value.strip():
            names.add(value.strip())
    # Coolify often displays Service Stack applications as:
    #   Mainnet Hub1 (main-computer-mainnet-mainnet-hub1:remote)
    # Keep both the UI name and the compose/image fragments available for matching.
    for key in ("image", "docker_image", "container_name"):
        value = application.get(key)
        if isinstance(value, str) and value.strip():
            names.update(part.strip() for part in re.split(r"[:/@]", value) if part.strip())
    return names


def _match_application_for_service(applications: list[dict[str, Any]], service_key: str) -> dict[str, Any] | None:
    clean = service_key.strip()
    clean_lower = clean.lower()
    for application in applications:
        names = _application_names(application)
        if any(name == clean for name in names):
            return application
        if any(name.lower() == clean_lower for name in names):
            return application
    for application in applications:
        # Fallback for Coolify display names such as "Mainnet Hub1" when the
        # compose service is "mainnet-hub1".
        haystack = " ".join(sorted(_application_names(application))).lower().replace("_", "-")
        if clean_lower in haystack:
            return application
    return None


def _service_applications_from_body(body: Any) -> list[dict[str, Any]]:
    data = _body_dict(body)
    applications = _body_list(data, "applications")
    if applications:
        return applications
    service = data.get("service")
    if isinstance(service, dict):
        applications = _body_list(service, "applications")
        if applications:
            return applications
    return []


def load_service_applications(client: Any, service_uuid: str, tried: list[dict[str, Any]]) -> list[dict[str, Any]]:
    paths = [
        f"/api/v1/services/{urllib.parse.quote(service_uuid)}",
        f"/api/v1/services/{urllib.parse.quote(service_uuid)}/applications",
    ]
    for path in paths:
        response = client.request("GET", path)
        applications = _service_applications_from_body(response.body)
        tried.append(
            {
                "operation": "load-hub-service-applications",
                "method": "GET",
                "path": path,
                "response": hub_tool.response_to_dict(response),
                "application_count": len(applications),
                "applications": [hub_tool.item_summary(item) for item in applications],
            }
        )
        if response.ok and applications:
            return applications
        if not response.ok and response.status not in {404, 405}:
            raise CoolifyHubDeployError(
                f"Coolify Hub service application lookup failed with HTTP {response.status}: {response.body}"
            )
    return []


def application_uuid(application: dict[str, Any]) -> str:
    for key in ("uuid", "application_uuid", "id"):
        value = str(application.get(key) or "").strip()
        if value:
            return value
    return ""


def current_application_domains(application: dict[str, Any]) -> str:
    for key in ("fqdn", "domains", "domain", "urls"):
        value = application.get(key)
        if isinstance(value, list):
            return ",".join(str(item).strip() for item in value if str(item).strip())
        text = str(value or "").strip()
        if text:
            return text
    return ""


def reconcile_application_domain(
    client: Any,
    *,
    application: dict[str, Any],
    service_key: str,
    desired_domain: str,
    tried: list[dict[str, Any]],
) -> dict[str, Any]:
    uuid = application_uuid(application)
    if not uuid:
        raise CoolifyHubDeployError(f"Coolify application for {service_key!r} has no uuid: {application!r}")

    current = current_application_domains(application)
    if current == desired_domain:
        return {
            "service_key": service_key,
            "application_uuid": uuid,
            "domain": desired_domain,
            "changed": False,
            "reason": "already-current",
        }

    # Coolify reads the value back as "fqdn" but writes it as "domains".
    # Keep the request narrow to avoid accidentally rewriting unrelated app fields.
    payload = {"domains": desired_domain}
    response = client.request("PATCH", f"/api/v1/applications/{urllib.parse.quote(uuid)}", payload)
    tried.append(
        {
            "operation": "reconcile-hub-application-domain",
            "method": "PATCH",
            "path": f"/api/v1/applications/{urllib.parse.quote(uuid)}",
            "service_key": service_key,
            "payload": payload,
            "previous_domains": current,
            "response": hub_tool.response_to_dict(response),
        }
    )
    if not response.ok:
        raise CoolifyHubDeployError(
            f"Coolify domain update failed for {service_key!r} application {uuid!r} "
            f"with HTTP {response.status}: {response.body}"
        )
    return {
        "service_key": service_key,
        "application_uuid": uuid,
        "domain": desired_domain,
        "changed": True,
        "previous_domains": current,
    }


def reconcile_service_application_domains(
    client: Any,
    *,
    service_uuid: str,
    domains: dict[str, dict[str, str]],
    tried: list[dict[str, Any]],
) -> dict[str, Any]:
    clean_domains = {
        str(service_key): str(spec.get("domain") or "").strip()
        for service_key, spec in (domains or {}).items()
        if isinstance(spec, dict) and str(spec.get("domain") or "").strip()
    }
    if not clean_domains:
        return {"ok": True, "changed": False, "results": [], "skipped": "no-domain-targets"}

    applications = load_service_applications(client, service_uuid, tried)
    if not applications:
        raise CoolifyHubDeployError(
            f"Coolify service {service_uuid!r} did not expose application rows; cannot reconcile Hub domains."
        )

    results: list[dict[str, Any]] = []
    missing: list[str] = []
    for service_key, desired_domain in clean_domains.items():
        application = _match_application_for_service(applications, service_key)
        if application is None:
            missing.append(service_key)
            continue
        results.append(
            reconcile_application_domain(
                client,
                application=application,
                service_key=service_key,
                desired_domain=desired_domain,
                tried=tried,
            )
        )

    if missing:
        raise CoolifyHubDeployError(
            "Coolify service application lookup did not find expected Hub service(s): "
            + ", ".join(sorted(missing))
        )

    changed = any(bool(item.get("changed")) for item in results)
    return {
        "ok": True,
        "changed": changed,
        "results": results,
    }


def sync_service_for_server(
    client: Any,
    placement: HubClusterPlacement,
    profile: Any,
    args: argparse.Namespace,
    *,
    server_name: str,
    context: dict[str, Any],
    tried: list[dict[str, Any]],
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    name = hub_service_name(placement, server_name)
    service_uuid, existing = hub_tool.find_service(client, service_name=name, explicit_uuid="", tried=tried)
    compose = render_server_hub_compose(placement, profile, args, server_name)
    domains = hub_service_domains(placement, profile, server_name)
    if service_uuid:
        update_result = update_service(client, service_uuid, name, compose, tried, domains=domains)
        return service_uuid, "updated", existing, update_result
    payload = service_payload(placement, profile, args, server_name=server_name, context=context)
    service_uuid = create_service(client, payload, tried)
    return service_uuid, "created", existing, {
        "ok": True,
        "path": "/api/v1/services",
        "method": "POST",
        "domains_included": bool(payload.get("docker_compose_domains")),
    }


def server_plan(placement: HubClusterPlacement, profile: Any, args: argparse.Namespace, server_name: str) -> dict[str, Any]:
    context_preview = {
        "server_uuid": "<resolved-at-apply>",
        "project_uuid": args.coolify_project_uuid or "<resolved-at-apply>",
        "environment_name": args.coolify_environment_name,
        "environment_uuid": args.coolify_environment_uuid or "<resolved-at-apply>",
    }
    local_hubs = hubs_for_server(placement, server_name)
    applications = [
        {
            "hub_id": hub.hub_id,
            "application_name": hub_application_name(placement, hub),
            "public_url": hub.public_url,
            "runtime_dir": hub.runtime_dir,
            "cluster_file_path": hub.cluster_file_path,
            "namespace": hub.namespace,
            "application_payload": hub_application_payload(
                placement,
                profile,
                args,
                hub=hub,
                server_name=server_name,
                context=context_preview,
            ),
        }
        for hub in local_hubs
    ]
    traefik_dynamic_config = None
    if shared_entry_hosts(placement):
        traefik_dynamic_config = {
            "installed": traefik_sidecar_enabled(args),
            "container_service": traefik_dynamic_config_service_key(placement, server_name),
            "path": traefik_dynamic_config_path(placement, server_name),
            "action": "preview-only",
            "contents": render_server_traefik_dynamic_config(placement, profile, args, server_name) if local_hubs else "",
            "note": "Hub Application mode previews but does not install the old Service Stack public-entry sidecar.",
        }
    return {
        "server": server_name,
        "coolify_url": fdb_tool.coolify_url_for_server(server_name, args)[0],
        "coolify_url_source": fdb_tool.coolify_url_for_server(server_name, args)[1],
        "resource_kind": "applications",
        "service_name": hub_service_name(placement, server_name),
        "legacy_service_stack_name": hub_service_name(placement, server_name),
        "hubs": [
            {
                "hub_id": hub.hub_id,
                "public_url": hub.public_url,
                "runtime_dir": hub.runtime_dir,
                "cluster_file_path": hub.cluster_file_path,
                "namespace": hub.namespace,
            }
            for hub in local_hubs
        ],
        "coolify_service_domains": hub_service_domains(placement, profile, server_name),
        "applications": applications,
        "traefik_dynamic_config": traefik_dynamic_config,
        "operator_note": (
            "Each Hub is deployed as a first-class Coolify Application so the normal Application domains field "
            "can be set directly. No Hub Service Stack or service-stack sub-application domain metadata is used."
        ),
    }

def plan_result(placement: HubClusterPlacement, profile: Any, args: argparse.Namespace) -> dict[str, Any]:
    hub_tool.apply_bridge_signer_defaults(profile, args)
    fdb_tool.validate_coolify_url_bindings(placement.servers, args)

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
            "Apply the shared FDB layer first. Hubs deploy as separate Coolify Applications, each mounting "
            f"the shared runtime directory and reading {placement.cluster_file_path!r}. The Hub stage no longer "
            "creates a Coolify Service Stack for Hub containers."
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
        application_results: list[dict[str, Any]] = []
        for hub in hubs_for_server(placement, server_name):
            application_results.append(
                sync_hub_application(
                    client,
                    placement,
                    profile,
                    args,
                    server_name=server_name,
                    hub=hub,
                    context=context,
                    tried=tried,
                )
            )
        phases.append(
            {
                "server": server_name,
                "coolify_url": fdb_tool.coolify_url_for_server(server_name, args)[0],
                "coolify_url_source": fdb_tool.coolify_url_for_server(server_name, args)[1],
                "token_source": token_source,
                "context": context,
                "resource_kind": "applications",
                "applications": application_results,
                "tried": tried,
            }
        )

    return {"ok": True, "plan": plan, "phases": phases}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy Hub services for a multi-hub Coolify topology.")
    parser.add_argument(
        "action",
        choices=["list-components", "prep-packet", "plan", "apply"],
        help="Use prep-packet to select a Hub/FDB generation; use plan/apply to deploy Hub services.",
    )
    parser.add_argument("network", nargs="?", default="", help="Network key, e.g. testnet. For plan/apply this reads deploy/packets/<network>-packet.json.")
    parser.add_argument("--placement", type=Path, default=DEFAULT_PLACEMENT_PATH, help="Path to <network>-coolify-deployment.json for list/prep, or legacy direct placement when no network/packet is supplied.")
    parser.add_argument("--packet", type=Path, default=None, help="Override packet path. Defaults to deploy/packets/<network>-packet.json for plan/apply when network is supplied.")
    parser.add_argument(
        "--private-state",
        type=Path,
        default=None,
        help="Private state YAML with coolify.hosts.<slot>.name/url/api_token. Defaults to runtime/state/main_computer.private.yaml when present.",
    )
    parser.add_argument("--topology", type=Path, default=None, help="Optional topology path override for prep-packet/list-components.")
    parser.add_argument("--hubs", default="", help="Comma-separated Hub ids to enable for prep-packet.")
    parser.add_argument("--fdb", default="", help="Comma-separated FoundationDB instance ids to enable for prep-packet.")
    parser.add_argument("--generation", default="", help="Optional deploy packet generation id.")
    parser.add_argument("--intent", default="", help="Optional human-readable operator intent stored in the deploy packet.")
    parser.add_argument("--out", default="", help="Output path for prep-packet. Defaults to deploy/packets/<network>-packet.json.")
    parser.add_argument("--no-archive", action="store_true", help="Do not archive an existing different packet before writing.")
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
    parser.add_argument("--no-bridge-writes", action="store_true", help="Do not infer or sync bridge signer material even when a local hub_admin manifest exists.")
    parser.add_argument("--sync-bridge-signer", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--bridge-signer-source-manifest", default="", help=argparse.SUPPRESS)
    parser.add_argument("--bridge-controller-wallet-path", default="", help=argparse.SUPPRESS)
    parser.add_argument("--bridge-signer-env-key", default="", help=argparse.SUPPRESS)
    parser.add_argument("--bridge-signer-remote-path", default="", help=argparse.SUPPRESS)

    parser.add_argument("--coolify-timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--coolify-retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--coolify-retry-sleep-s", type=float, default=DEFAULT_RETRY_SLEEP_S)
    parser.add_argument("--hub-wait-timeout-s", type=float, default=300.0, help="Maximum seconds to wait for each Hub to become ready before deploying the next Hub.")
    parser.add_argument("--hub-wait-poll-s", type=float, default=5.0, help="Seconds between per-Hub readiness checks.")
    parser.add_argument("--hub-status-timeout-s", type=float, default=5.0, help="HTTP timeout for each Hub readiness check.")
    parser.add_argument("--hub-status-user-agent", default=hub_tool.DEFAULT_JSON_RPC_USER_AGENT, help="User-Agent for Hub readiness checks.")
    parser.add_argument("--no-wait-hubs", action="store_true", help="Do not wait for each Hub to become ready before deploying the next Hub.")
    parser.add_argument("--no-deploy", action="store_true", help="Create/update only; do not trigger a service deploy.")
    parser.add_argument(
        "--no-traefik-sidecar",
        action="store_true",
        help="Disable the default public-entry Traefik sidecar for public_entry_urls.",
    )
    parser.add_argument(
        "--install-traefik-dynamic-config",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--force-deploy", action="store_true", help="Ask Coolify to force rebuild/redeploy services.")
    parser.add_argument("--dry-run", action="store_true", help="For apply: render the plan without network or Coolify calls.")
    parser.add_argument("--json", action="store_true", help="Print compact machine-readable JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.action in {"list-components", "prep-packet"}:
            network = packet_tool.clean_identifier(args.network or "", "network")
            if args.placement == DEFAULT_PLACEMENT_PATH and network != "testnet":
                placement_path = packet_tool.default_placement_path(network)
            else:
                placement_path = repo_relative_path(args.placement or packet_tool.default_placement_path(network))
            topology_path = repo_relative_path(args.topology) if args.topology else None
            if args.action == "list-components":
                result = packet_tool.list_components_result(network, placement_path, topology_path)
            else:
                result = packet_tool.prep_packet_result(
                    argparse.Namespace(
                        network=network,
                        placement=placement_path,
                        topology=topology_path or "",
                        hubs=args.hubs,
                        fdb=args.fdb,
                        generation=args.generation,
                        intent=args.intent,
                        out=args.out,
                        no_archive=args.no_archive,
                    )
                )
        else:
            placement = load_hub_cluster_placement_from_args(args)
            profile = load_network_profile(placement, args)
            if not str(args.coolify_environment_name or "").strip():
                args.coolify_environment_name = f"{placement.network_key}-{DEFAULT_ENVIRONMENT_SUFFIX}"
            result = (
                {"ok": True, "plan": plan_result(placement, profile, args)}
                if args.action == "plan"
                else apply_result(placement, profile, args)
            )
    except (CoolifyHubDeployError, HubNetworkConfigError, packet_tool.DeployPacketError) as exc:
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
