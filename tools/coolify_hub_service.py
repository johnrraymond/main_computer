#!/usr/bin/env python3
"""Create or update Coolify Hub application resources for public networks.

This script manages only the Hub application service. The Besu/QBFT chain
resources are handled by the network deployer. For testnet, RPC and public Hub
checks are post-deploy health checks by default, not blockers for creating or
updating the Coolify application.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main_computer.hub_networks import HubNetworkConfigError, HubNetworkProfile, load_hub_network_registry  # noqa: E402


DEFAULT_TOKEN_ENV = "MAIN_COMPUTER_COOLIFY_TOKEN"
DEFAULT_TIMEOUT_S = 25.0
DEFAULT_RETRIES = 1
DEFAULT_RETRY_SLEEP_S = 2.0
DEFAULT_DOCKERFILE_LOCATION = "/Dockerfile.hub"
DEFAULT_BASE_DIRECTORY = "/"
DEFAULT_HEALTH_PATH = "/api/hub/status"


class CoolifyHubDeployError(RuntimeError):
    """Raised when the Hub service cannot be safely planned or applied."""


@dataclass(frozen=True)
class CoolifyResponse:
    ok: bool
    status: int
    method: str
    path: str
    body: Any


class CoolifyClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        retries: int = DEFAULT_RETRIES,
        retry_sleep_s: float = DEFAULT_RETRY_SLEEP_S,
    ) -> None:
        clean = str(base_url or "").strip().rstrip("/")
        if not clean.startswith(("http://", "https://")):
            raise CoolifyHubDeployError(f"Coolify URL must start with http:// or https://, got {base_url!r}.")
        self.base_url = clean
        self.token = str(token or "").strip()
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
                        path=api_path,
                        body=parse_response_body(raw),
                    )
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                return CoolifyResponse(
                    ok=False,
                    status=int(exc.code),
                    method=method.upper(),
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
            path=api_path,
            body={
                "error": "request_failed",
                "message": f"Coolify API request failed: {url}: {last_error}",
                "error_type": type(last_error).__name__ if last_error is not None else "unknown",
            },
        )


def parse_response_body(raw: str) -> Any:
    if not raw:
        return ""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def response_to_dict(response: CoolifyResponse) -> dict[str, Any]:
    return {
        "ok": response.ok,
        "status": response.status,
        "method": response.method,
        "path": response.path,
        "body": response.body,
    }


def body_items(body: Any, *preferred_keys: str) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if not isinstance(body, dict):
        return []
    for key in (*preferred_keys, "data", "items", "applications", "resources", "projects", "servers", "environments"):
        value = body.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def item_uuid(item: dict[str, Any]) -> str:
    for key in ("uuid", "id", "application_uuid", "project_uuid", "server_uuid"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def item_name(item: dict[str, Any]) -> str:
    for key in ("name", "description", "fqdn", "urls"):
        value = item.get(key)
        if isinstance(value, list) and value:
            return str(value[0]).strip()
        text = str(value or "").strip()
        if text:
            return text
    return ""


def item_summary(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "uuid",
        "id",
        "name",
        "description",
        "fqdn",
        "urls",
        "status",
        "git_repository",
        "git_branch",
        "project_uuid",
        "server_uuid",
        "environment_name",
    )
    summary = {key: item.get(key) for key in keys if item.get(key) not in (None, "")}
    if "uuid" not in summary:
        uuid = item_uuid(item)
        if uuid:
            summary["uuid"] = uuid
    if "name" not in summary:
        name = item_name(item)
        if name:
            summary["name"] = name
    return summary


def resolve_token(args: argparse.Namespace) -> tuple[str, str]:
    explicit = str(getattr(args, "coolify_token", "") or "").strip()
    if explicit:
        return explicit, "--coolify-token"
    env_name = str(getattr(args, "coolify_token_env", "") or DEFAULT_TOKEN_ENV).strip()
    if env_name:
        value = os.environ.get(env_name)
        if value and value.strip():
            return value.strip(), f"env:{env_name}"
    token_file = str(getattr(args, "coolify_token_file", "") or "").strip()
    if token_file:
        value = Path(token_file).read_text(encoding="utf-8").strip()
        if value:
            return value, f"file:{token_file}"
    raise CoolifyHubDeployError(
        f"Coolify token is required. Set {env_name or DEFAULT_TOKEN_ENV} or pass --coolify-token-file."
    )


def client_from_args(args: argparse.Namespace) -> CoolifyClient:
    token, _source = resolve_token(args)
    return CoolifyClient(
        args.coolify_url,
        token,
        timeout_s=args.coolify_timeout_s,
        retries=args.coolify_retries,
        retry_sleep_s=args.coolify_retry_sleep_s,
    )


def json_rpc(url: str, method: str, params: list[Any] | None = None, *, timeout_s: float = 8.0) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise CoolifyHubDeployError(f"JSON-RPC {method} failed for {url}: {type(exc).__name__}: {exc}") from exc
    if "error" in payload:
        raise CoolifyHubDeployError(f"{method} returned JSON-RPC error: {payload['error']}")
    return payload.get("result")


def verify_rpc(profile: HubNetworkProfile, args: argparse.Namespace) -> dict[str, Any]:
    if not profile.chain_rpc_url:
        raise CoolifyHubDeployError(f"Hub network {profile.network_key!r} has no chain_rpc_url.")
    expected = hex(int(profile.chain_id or 0))
    chain_id = str(json_rpc(profile.chain_rpc_url, "eth_chainId", timeout_s=args.rpc_timeout_s))
    if chain_id.lower() != expected.lower():
        raise CoolifyHubDeployError(
            f"RPC chain id mismatch for {profile.network_key}: expected {expected}, got {chain_id}."
        )
    block_hex = str(json_rpc(profile.chain_rpc_url, "eth_blockNumber", timeout_s=args.rpc_timeout_s))
    block_number = int(block_hex, 16)
    return {"ok": True, "rpc_url": profile.chain_rpc_url, "chain_id": chain_id, "block_number": block_number}


def validate_remote_profile(profile: HubNetworkProfile) -> None:
    missing: list[str] = []
    if profile.chain_id is None:
        missing.append("chain_id")
    if not profile.chain_rpc_url:
        missing.append("chain_rpc_url")
    if not profile.hub_public_url:
        missing.append("hub_public_url")
    if not profile.hub_bind_port:
        missing.append("hub_bind_port")
    if missing:
        raise CoolifyHubDeployError(
            f"Hub network {profile.network_key!r} is not remotely deployable until these fields are set: "
            + ", ".join(missing)
        )
    if profile.kind not in {"testnet", "mainnet"}:
        raise CoolifyHubDeployError(
            f"Refusing to deploy non-remote Hub network {profile.network_key!r} with kind {profile.kind!r}."
        )
    if profile.hub_bind_host != "0.0.0.0":
        raise CoolifyHubDeployError(
            f"Remote Hub network {profile.network_key!r} must bind inside the container on 0.0.0.0, got {profile.hub_bind_host!r}."
        )


def hub_service_name(network: str) -> str:
    return f"main-computer-{network}-hub"


def hub_state_mount_path(network: str) -> str:
    return f"/data/main-computer/hub/{network}"


def hub_volume_name(network: str) -> str:
    return f"{network}_hub_state"


def hub_start_command(profile: HubNetworkProfile, runtime_dir: str) -> str:
    return " ".join(
        [
            "--network",
            shell_word(profile.network_key),
            "--host",
            shell_word(profile.hub_bind_host),
            "--port",
            shell_word(str(profile.hub_bind_port)),
            "--hub-runtime-dir",
            shell_word(runtime_dir),
        ]
    )


def shell_word(value: str) -> str:
    text = str(value)
    if not text or any(ch.isspace() for ch in text):
        return json.dumps(text)
    return text


def default_dockerfile_location(profile: HubNetworkProfile) -> str:
    if profile.network_key in {"testnet", "mainnet"}:
        return f"/Dockerfile.hub.{profile.network_key}"
    return DEFAULT_DOCKERFILE_LOCATION


def effective_dockerfile_location(profile: HubNetworkProfile, args: argparse.Namespace) -> str:
    return str(getattr(args, "dockerfile_location", "") or default_dockerfile_location(profile))


def coolify_domain_with_backend_port(profile: HubNetworkProfile) -> str:
    """Return the Coolify domain value, with the backend container port when needed.

    Coolify strips the port from generated public Host rules but uses it to set the
    reverse-proxy upstream port. The public Hub URL remains profile.hub_public_url.
    """
    public_url = str(profile.hub_public_url or "").strip().rstrip("/")
    if not public_url:
        return public_url
    try:
        parsed = urllib.parse.urlsplit(public_url)
    except ValueError:
        return public_url
    if parsed.port is not None or not parsed.hostname:
        return public_url
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{profile.hub_bind_port}"
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def application_payload(
    profile: HubNetworkProfile,
    args: argparse.Namespace,
    *,
    service_name: str,
    runtime_dir: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": service_name,
        "description": f"Main Computer {profile.network_key} Hub",
        "project_uuid": args.coolify_project_uuid,
        "server_uuid": args.coolify_server_uuid,
        "environment_name": args.coolify_environment_name,
        "git_repository": args.git_repo,
        "git_branch": args.git_branch,
        "build_pack": "dockerfile",
        "base_directory": args.base_directory,
        "dockerfile_location": effective_dockerfile_location(profile, args),
        "ports_exposes": str(profile.hub_bind_port),
        "domains": coolify_domain_with_backend_port(profile),
        "start_command": hub_start_command(profile, runtime_dir),
        "health_check_enabled": True,
        "health_check_path": args.health_path,
        "instant_deploy": False,
    }
    if args.coolify_environment_uuid:
        payload["environment_uuid"] = args.coolify_environment_uuid
    if args.coolify_destination_uuid:
        payload["destination_uuid"] = args.coolify_destination_uuid
    if args.git_commit_sha:
        payload["git_commit_sha"] = args.git_commit_sha
    return {key: value for key, value in payload.items() if value not in (None, "")}


def application_update_payload(
    profile: HubNetworkProfile,
    args: argparse.Namespace,
    *,
    service_name: str,
    runtime_dir: str,
) -> dict[str, Any]:
    payload = application_payload(profile, args, service_name=service_name, runtime_dir=runtime_dir)
    for key in ("project_uuid", "server_uuid", "environment_name", "environment_uuid", "git_repository"):
        payload.pop(key, None)
    return payload


def storage_payload(profile: HubNetworkProfile, *, runtime_dir: str) -> dict[str, Any]:
    return {"type": "persistent", "name": hub_volume_name(profile.network_key), "mount_path": runtime_dir, "host_path": runtime_dir}


def storage_matches(item: dict[str, Any], *, name: str, mount_path: str) -> bool:
    candidates = {
        str(item.get("name") or "").strip(),
        str(item.get("mount_path") or "").strip(),
        str(item.get("host_path") or "").strip(),
        str(item.get("destination") or "").strip(),
        str(item.get("resource_path") or "").strip(),
    }
    return name in candidates or mount_path in candidates


def select_by_exact_name(items: list[dict[str, Any]], name: str) -> tuple[str, list[dict[str, Any]]]:
    clean = name.strip().lower()
    matches = [item for item in items if item_name(item).lower() == clean or str(item.get("name") or "").strip().lower() == clean]
    if len(matches) == 1:
        return item_uuid(matches[0]), matches
    if len(matches) > 1:
        return "", matches
    return "", []


def list_applications(client: CoolifyClient) -> tuple[CoolifyResponse, list[dict[str, Any]]]:
    response = client.request("GET", "/api/v1/applications")
    return response, body_items(response.body, "applications")


def list_resources(client: CoolifyClient, path: str, *preferred_keys: str) -> tuple[CoolifyResponse, list[dict[str, Any]]]:
    response = client.request("GET", path)
    return response, body_items(response.body, *preferred_keys)


def resolve_exact_resource_uuid(
    client: CoolifyClient,
    *,
    path: str,
    preferred_keys: tuple[str, ...],
    resource_kind: str,
    explicit_uuid: str,
    explicit_name: str,
    tried: list[dict[str, Any]],
    infer_if_single: bool = False,
) -> str:
    clean_uuid = str(explicit_uuid or "").strip()
    if clean_uuid:
        return clean_uuid
    clean_name = str(explicit_name or "").strip()
    if not clean_name and not infer_if_single:
        return ""
    response, items = list_resources(client, path, *preferred_keys)
    tried.append(
        {
            "operation": f"list-{resource_kind}s",
            "path": path,
            "response": response_to_dict(response),
            "count": len(items),
            "resolver": "name" if clean_name else "single",
        }
    )
    if not response.ok:
        target = clean_name if clean_name else "the only available resource"
        raise CoolifyHubDeployError(f"Could not list Coolify {resource_kind}s to resolve {target!r}.")
    if clean_name:
        uuid, matches = select_by_exact_name(items, clean_name)
        if uuid:
            return uuid
        if len(matches) > 1:
            raise CoolifyHubDeployError(f"Multiple Coolify {resource_kind}s named {clean_name!r}; pass the UUID explicitly.")
        raise CoolifyHubDeployError(f"No Coolify {resource_kind} named {clean_name!r} was returned by the API.")
    candidates = [item for item in items if item_uuid(item)]
    if len(candidates) == 1:
        return item_uuid(candidates[0])
    if len(candidates) > 1:
        summaries = [item_summary(item) for item in candidates]
        raise CoolifyHubDeployError(
            f"Multiple Coolify {resource_kind}s were returned; pass --coolify-{resource_kind}-uuid "
            f"or --coolify-{resource_kind}-name. Matches: {summaries}"
        )
    raise CoolifyHubDeployError(
        f"No Coolify {resource_kind} with a UUID was returned by the API; pass --coolify-{resource_kind}-uuid explicitly."
    )


def resolve_coolify_context(client: CoolifyClient, profile: HubNetworkProfile, args: argparse.Namespace, tried: list[dict[str, Any]]) -> dict[str, Any]:
    if not str(args.coolify_environment_name or "").strip():
        args.coolify_environment_name = profile.network_key
    args.coolify_project_uuid = resolve_exact_resource_uuid(
        client,
        path="/api/v1/projects",
        preferred_keys=("projects",),
        resource_kind="project",
        explicit_uuid=args.coolify_project_uuid,
        explicit_name=args.coolify_project_name,
        tried=tried,
    )
    args.coolify_server_uuid = resolve_exact_resource_uuid(
        client,
        path="/api/v1/servers",
        preferred_keys=("servers",),
        resource_kind="server",
        explicit_uuid=args.coolify_server_uuid,
        explicit_name=args.coolify_server_name,
        tried=tried,
        infer_if_single=True,
    )
    if not args.coolify_project_uuid:
        raise CoolifyHubDeployError("Coolify project is required. Pass --coolify-project-uuid or --coolify-project-name.")
    if not args.coolify_server_uuid:
        raise CoolifyHubDeployError("Coolify server is required. Pass --coolify-server-uuid or --coolify-server-name.")
    if not args.coolify_environment_name and not args.coolify_environment_uuid:
        raise CoolifyHubDeployError("Coolify environment is required. Pass --coolify-environment-name or --coolify-environment-uuid.")
    return {
        "project_uuid": args.coolify_project_uuid,
        "server_uuid": args.coolify_server_uuid,
        "environment_name": args.coolify_environment_name,
        "environment_uuid": args.coolify_environment_uuid,
    }


def find_application(client: CoolifyClient, *, service_name: str, explicit_uuid: str, tried: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    if explicit_uuid:
        return explicit_uuid, {"source": "explicit_uuid", "uuid": explicit_uuid}
    response, apps = list_applications(client)
    tried.append({"operation": "list-applications", "response": response_to_dict(response), "count": len(apps)})
    if not response.ok:
        raise CoolifyHubDeployError("Could not list Coolify applications before create; refusing to create blindly.")
    uuid, matches = select_by_exact_name(apps, service_name)
    if uuid:
        return uuid, {"source": "name", "uuid": uuid, "matches": [item_summary(item) for item in matches]}
    if len(matches) > 1:
        raise CoolifyHubDeployError(
            f"Multiple Coolify applications named {service_name!r} already exist; pass --coolify-application-uuid."
        )
    return "", {"source": "missing", "matches": []}


def choose_endpoint(args: argparse.Namespace) -> str:
    if args.github_app_uuid:
        return "/api/v1/applications/private-github-app"
    if args.deploy_key_uuid:
        return "/api/v1/applications/private-deploy-key"
    return "/api/v1/applications/public"


def create_application(client: CoolifyClient, profile: HubNetworkProfile, args: argparse.Namespace, *, service_name: str, runtime_dir: str, tried: list[dict[str, Any]]) -> str:
    endpoint = choose_endpoint(args)
    payload = application_payload(profile, args, service_name=service_name, runtime_dir=runtime_dir)
    if args.github_app_uuid:
        payload["github_app_uuid"] = args.github_app_uuid
    if args.deploy_key_uuid:
        payload["private_key_uuid"] = args.deploy_key_uuid
    response = client.request("POST", endpoint, payload)
    tried.append({"operation": "create-application", "path": endpoint, "payload_keys": sorted(payload), "response": response_to_dict(response)})
    if not response.ok:
        raise CoolifyHubDeployError(f"Coolify application create failed with HTTP {response.status}: {response.body}")
    uuid = item_uuid(response.body) if isinstance(response.body, dict) else ""
    if not uuid and isinstance(response.body, dict) and isinstance(response.body.get("application"), dict):
        uuid = item_uuid(response.body["application"])
    if not uuid:
        raise CoolifyHubDeployError(f"Coolify application create succeeded but no UUID was returned: {response.body}")
    return uuid


def update_application(client: CoolifyClient, profile: HubNetworkProfile, args: argparse.Namespace, *, application_uuid: str, service_name: str, runtime_dir: str, tried: list[dict[str, Any]]) -> None:
    payload = application_update_payload(profile, args, service_name=service_name, runtime_dir=runtime_dir)
    paths = [f"/api/v1/applications/{urllib.parse.quote(application_uuid)}"]
    methods = ["PATCH", "PUT"]
    for path in paths:
        for method in methods:
            response = client.request(method, path, payload)
            tried.append({"operation": "update-application", "method": method, "path": path, "payload_keys": sorted(payload), "response": response_to_dict(response)})
            if response.ok:
                return
            if response.status not in {404, 405, 422}:
                raise CoolifyHubDeployError(f"Coolify application update failed with HTTP {response.status}: {response.body}")
    raise CoolifyHubDeployError("Coolify application update failed on all known endpoints.")


def ensure_storage(client: CoolifyClient, profile: HubNetworkProfile, args: argparse.Namespace, *, application_uuid: str, runtime_dir: str, tried: list[dict[str, Any]]) -> dict[str, Any]:
    name = hub_volume_name(profile.network_key)
    list_path = f"/api/v1/applications/{urllib.parse.quote(application_uuid)}/storages"
    response = client.request("GET", list_path)
    storages = body_items(response.body, "storages", "persistent_storages") if response.ok else []
    tried.append({"operation": "list-storage", "path": list_path, "response": response_to_dict(response), "count": len(storages)})
    if response.ok:
        matches = [item for item in storages if storage_matches(item, name=name, mount_path=runtime_dir)]
        if len(matches) == 1:
            return {"ok": True, "source": "existing", "storage": item_summary(matches[0])}
        if len(matches) > 1:
            raise CoolifyHubDeployError(f"Multiple persistent storages match {name!r}/{runtime_dir!r}; refusing to guess.")
    if args.no_create_storage:
        return {"ok": False, "source": "skipped", "message": "Persistent storage create skipped by --no-create-storage."}
    payload = storage_payload(profile, runtime_dir=runtime_dir)
    response = client.request("POST", list_path, payload)
    tried.append({"operation": "create-storage", "path": list_path, "payload": payload, "response": response_to_dict(response)})
    if response.ok:
        return {"ok": True, "source": "created", "response": response_to_dict(response)}
    raise CoolifyHubDeployError(
        "Could not confirm/create persistent Hub storage through the Coolify API. "
        "Create the storage in the Coolify UI, then rerun so the script can confirm it. "
        f"Last response: HTTP {response.status}: {response.body}"
    )


def trigger_deploy(client: CoolifyClient, *, application_uuid: str, force: bool, tried: list[dict[str, Any]]) -> dict[str, Any]:
    query = urllib.parse.urlencode({"uuid": application_uuid, "force": "true" if force else "false"})
    paths = [
        f"/api/v1/deploy?{query}",
        f"/api/v1/applications/{urllib.parse.quote(application_uuid)}/start",
        f"/api/v1/applications/{urllib.parse.quote(application_uuid)}/restart",
    ]
    for path in paths:
        method = "GET" if path.startswith("/api/v1/deploy?") else "POST"
        response = client.request(method, path)
        tried.append({"operation": "deploy", "method": method, "path": path, "response": response_to_dict(response)})
        if response.ok:
            return response_to_dict(response)
    raise CoolifyHubDeployError("Coolify deploy failed on all known endpoints.")


def wait_for_hub(profile: HubNetworkProfile, args: argparse.Namespace) -> dict[str, Any]:
    if args.hub_wait_timeout_s <= 0:
        return {"ok": True, "skipped": True, "reason": "hub_wait_timeout_s <= 0"}
    status_url = profile.hub_url.rstrip("/") + args.health_path
    deadline = time.monotonic() + args.hub_wait_timeout_s
    last_error: object = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(status_url, timeout=args.hub_status_timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
            network = payload.get("network") if isinstance(payload, dict) else {}
            if isinstance(network, dict):
                network_key = network.get("network_key") or network.get("network")
                chain_id = network.get("chain_id")
                if network_key == profile.network_key and int(chain_id) == int(profile.chain_id or -1):
                    return {"ok": True, "status_url": status_url, "status": payload}
                last_error = f"unexpected Hub status network={network_key!r} chain_id={chain_id!r}"
            else:
                last_error = "Hub status response has no network object"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(args.hub_wait_poll_s)
    raise CoolifyHubDeployError(f"Hub status did not become ready at {status_url}: {last_error}")


def load_profile(args: argparse.Namespace) -> HubNetworkProfile:
    registry = load_hub_network_registry(args.network_config)
    profile = registry.get(args.network)
    validate_remote_profile(profile)
    return profile


def plan_result(profile: HubNetworkProfile, args: argparse.Namespace) -> dict[str, Any]:
    runtime_dir = args.hub_runtime_dir or hub_state_mount_path(profile.network_key)
    service_name = args.coolify_application_name or hub_service_name(profile.network_key)
    return {
        "network": profile.network_key,
        "service_name": service_name,
        "runtime_dir": runtime_dir,
        "volume_name": hub_volume_name(profile.network_key),
        "public_url": profile.hub_url,
        "chain_rpc_url": profile.chain_rpc_url,
        "chain_id": profile.chain_id,
        "application_payload": application_payload(profile, args, service_name=service_name, runtime_dir=runtime_dir),
        "storage_payload": storage_payload(profile, runtime_dir=runtime_dir),
    }


def check_mode_for_profile(profile: HubNetworkProfile, mode: str, *, testnet_default: str = "warn", mainnet_default: str = "require") -> str:
    clean = str(mode or "auto").strip().lower()
    if clean != "auto":
        return clean
    return mainnet_default if profile.kind == "mainnet" else testnet_default


def rpc_check_mode(profile: HubNetworkProfile, args: argparse.Namespace) -> str:
    if getattr(args, "skip_rpc_check", False):
        return "skip"
    return check_mode_for_profile(profile, args.rpc_check, testnet_default="warn", mainnet_default="require")


def hub_health_check_mode(profile: HubNetworkProfile, args: argparse.Namespace) -> str:
    if getattr(args, "no_wait_hub", False):
        return "skip"
    return check_mode_for_profile(profile, args.hub_health_check, testnet_default="warn", mainnet_default="require")


def warning_payload(phase: str, mode: str, exc: BaseException) -> dict[str, Any]:
    return {"phase": phase, "mode": mode, "ok": False, "error": str(exc), "error_type": type(exc).__name__}


def apply(args: argparse.Namespace) -> dict[str, Any]:
    profile = load_profile(args)
    plan = plan_result(profile, args)
    if args.dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}

    phases: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    rpc_mode = rpc_check_mode(profile, args)
    if rpc_mode != "skip":
        try:
            rpc = verify_rpc(profile, args)
            phases.append({"phase": "verify-rpc", "mode": rpc_mode, "result": rpc})
        except CoolifyHubDeployError as exc:
            warning = warning_payload("verify-rpc", rpc_mode, exc)
            phases.append({"phase": "verify-rpc", "mode": rpc_mode, "result": warning})
            if rpc_mode == "require":
                raise
            warnings.append(warning)
    else:
        phases.append({"phase": "verify-rpc", "mode": rpc_mode, "result": {"ok": True, "skipped": True}})

    client = client_from_args(args)
    token, token_source = resolve_token(args)
    version = client.request("GET", "/api/v1/version")
    phases.append({"phase": "coolify-version", "result": response_to_dict(version), "token_source": token_source, "token_seen": bool(token)})
    if not version.ok:
        raise CoolifyHubDeployError(f"Coolify API version check failed with HTTP {version.status}: {version.body}")

    tried: list[dict[str, Any]] = []
    context = resolve_coolify_context(client, profile, args, tried)
    phases.append({"phase": "coolify-context", "result": context})

    application_uuid, existing = find_application(client, service_name=plan["service_name"], explicit_uuid=args.coolify_application_uuid, tried=tried)
    if application_uuid:
        update_application(client, profile, args, application_uuid=application_uuid, service_name=plan["service_name"], runtime_dir=plan["runtime_dir"], tried=tried)
        application_action = "updated"
    else:
        application_uuid = create_application(client, profile, args, service_name=plan["service_name"], runtime_dir=plan["runtime_dir"], tried=tried)
        application_action = "created"

    storage = ensure_storage(client, profile, args, application_uuid=application_uuid, runtime_dir=plan["runtime_dir"], tried=tried)
    phases.append(
        {
            "phase": "coolify-application",
            "result": {
                "ok": True,
                "application_uuid": application_uuid,
                "application_action": application_action,
                "existing": existing,
                "storage": storage,
                "tried": tried,
            },
        }
    )

    deploy_result: dict[str, Any] | None = None
    if not args.no_deploy:
        deploy_result = trigger_deploy(client, application_uuid=application_uuid, force=args.force_deploy, tried=tried)
        phases.append({"phase": "deploy", "result": deploy_result})

    hub_mode = hub_health_check_mode(profile, args)
    if hub_mode != "skip" and not args.no_deploy:
        try:
            hub = wait_for_hub(profile, args)
            phases.append({"phase": "wait-hub", "mode": hub_mode, "result": hub})
        except CoolifyHubDeployError as exc:
            warning = warning_payload("wait-hub", hub_mode, exc)
            phases.append({"phase": "wait-hub", "mode": hub_mode, "result": warning})
            if hub_mode == "require":
                raise
            warnings.append(warning)
    elif not args.no_deploy:
        phases.append({"phase": "wait-hub", "mode": hub_mode, "result": {"ok": True, "skipped": True}})

    return {
        "ok": True,
        "network": profile.network_key,
        "application_uuid": application_uuid,
        "application_action": application_action,
        "deployed": deploy_result is not None,
        "plan": plan,
        "warnings": warnings,
        "phases": phases,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy Main Computer Hub applications into Coolify.")
    parser.add_argument("action", choices=["plan", "apply"], help="Use plan for local payload rendering or apply for Coolify create/update.")
    parser.add_argument("network", choices=["testnet", "mainnet"], help="Remote Hub network to deploy.")

    parser.add_argument("--network-config", type=Path, default=None, help="Path to hub_networks.json.")
    parser.add_argument("--hub-runtime-dir", default="", help="Container path for persistent Hub runtime state.")

    parser.add_argument("--coolify-url", default="", help="Coolify base URL.")
    parser.add_argument("--coolify-token", default="", help="Coolify bearer token. Prefer --coolify-token-env.")
    parser.add_argument("--coolify-token-env", default=DEFAULT_TOKEN_ENV, help="Environment variable containing the Coolify token.")
    parser.add_argument("--coolify-token-file", default="", help="File containing the Coolify token.")
    parser.add_argument("--coolify-timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--coolify-retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--coolify-retry-sleep-s", type=float, default=DEFAULT_RETRY_SLEEP_S)

    parser.add_argument("--coolify-application-uuid", default="", help="Existing Coolify application UUID to update.")
    parser.add_argument("--coolify-application-name", default="", help="Application name. Defaults to main-computer-<network>-hub.")
    parser.add_argument("--coolify-project-uuid", default="", help="Coolify project UUID.")
    parser.add_argument("--coolify-project-name", default="", help="Project name to resolve exactly.")
    parser.add_argument("--coolify-environment-name", default="", help="Coolify environment name.")
    parser.add_argument("--coolify-environment-uuid", default="", help="Coolify environment UUID if known.")
    parser.add_argument(
        "--coolify-server-uuid",
        default="",
        help="Coolify server UUID. If omitted with --coolify-server-name, apply infers it only when exactly one server exists.",
    )
    parser.add_argument("--coolify-server-name", default="", help="Coolify server name to resolve exactly.")
    parser.add_argument("--coolify-destination-uuid", default="", help="Destination UUID if the server has multiple Docker destinations.")

    parser.add_argument("--git-repo", required=True, help="Git repository URL for public repo mode, or owner/repo for GitHub App mode.")
    parser.add_argument("--git-branch", default="main", help="Git branch to deploy.")
    parser.add_argument("--git-commit-sha", default="", help="Optional exact commit SHA.")
    parser.add_argument("--github-app-uuid", default="", help="Use private GitHub App create endpoint.")
    parser.add_argument("--deploy-key-uuid", default="", help="Use private deploy-key create endpoint.")
    parser.add_argument("--base-directory", default=DEFAULT_BASE_DIRECTORY)
    parser.add_argument("--dockerfile-location", default="", help="Dockerfile path. Defaults to /Dockerfile.hub.<network> for mainnet/testnet.")
    parser.add_argument("--health-path", default=DEFAULT_HEALTH_PATH)

    parser.add_argument("--rpc-timeout-s", type=float, default=8.0)
    parser.add_argument(
        "--rpc-check",
        choices=["auto", "require", "warn", "skip"],
        default="auto",
        help="RPC verification mode. auto warns on testnet and requires on mainnet.",
    )
    parser.add_argument("--skip-rpc-check", action="store_true", help="Legacy alias for --rpc-check skip.")
    parser.add_argument("--no-create-storage", action="store_true", help="Do not create missing persistent storage.")
    parser.add_argument("--no-deploy", action="store_true", help="Create/update only; do not trigger deploy.")
    parser.add_argument("--force-deploy", action="store_true", help="Ask Coolify to force rebuild/redeploy.")
    parser.add_argument(
        "--hub-health-check",
        choices=["auto", "require", "warn", "skip"],
        default="auto",
        help="Hub public status check mode. auto warns on testnet and requires on mainnet.",
    )
    parser.add_argument("--no-wait-hub", action="store_true", help="Legacy alias for --hub-health-check skip.")
    parser.add_argument("--hub-wait-timeout-s", type=float, default=120.0)
    parser.add_argument("--hub-wait-poll-s", type=float, default=5.0)
    parser.add_argument("--hub-status-timeout-s", type=float, default=8.0)
    parser.add_argument("--dry-run", action="store_true", help="Render the plan without network or Coolify calls.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.action == "plan":
            profile = load_profile(args)
            result = {"ok": True, "plan": plan_result(profile, args)}
        else:
            result = apply(args)
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
