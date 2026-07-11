#!/usr/bin/env python3
"""Create or update Coolify Hub application resources for public networks.

This script manages only the Hub application service. The Besu/QBFT chain
resources are handled by the network deployer. For testnet, RPC and public Hub
checks are post-deploy health checks by default, not blockers for creating or
updating the Coolify application.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
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
DEFAULT_DOCKERFILE_LOCATION = "/Dockerfile.hub.exp-fdb"
DEFAULT_EXP_FDB_DOCKERFILE_LOCATION = DEFAULT_DOCKERFILE_LOCATION
DEFAULT_BASE_DIRECTORY = "/"
DEFAULT_HEALTH_PATH = "/api/hub/status"
DEFAULT_JSON_RPC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "MainComputerHubDeployer/1.0"
)
DEFAULT_LOCAL_TEST_NETWORK = "test"
DEFAULT_LOCAL_COOLIFY_URL = "http://127.0.0.1:8000"
DEFAULT_LOCAL_COOLIFY_PROJECT_NAME = "Main Computer Local Smoke"
DEFAULT_LOCAL_COOLIFY_ENVIRONMENT_NAME = "production"
DEFAULT_LOCAL_COOLIFY_SERVER_NAME = "localhost"
DEFAULT_LOCAL_TEST_HUB_RUNTIME_DIR = "/srv/main-computer/hub/test-exp-fdb"
DEFAULT_LOCAL_TEST_CONTAINER_RPC_URL = "http://host.docker.internal:30010"
DEFAULT_LOCAL_TEST_BUILD_CONTEXT_DIRNAME = "hub-src"
DEFAULT_LOCAL_TEST_FDB_SERVICE_KEY = "main-computer-test-hub-fdb"
DEFAULT_LOCAL_TEST_FDB_IMAGE = "foundationdb/foundationdb:7.4.6"
DEFAULT_HUB_RUNTIME_ENV_FILENAME = "hub-runtime.env"
DEFAULT_LOCAL_TEST_FDB_PORT = 4550
DEFAULT_LOCAL_TEST_FDB_CLUSTER_CONTENTS = f"docker:docker@{DEFAULT_LOCAL_TEST_FDB_SERVICE_KEY}:{DEFAULT_LOCAL_TEST_FDB_PORT}"
DEFAULT_REMOTE_SIDECAR_FDB_NETWORKS = {"testnet"}
DEFAULT_LOCAL_TEST_SOURCE_DIR = REPO_ROOT
DEFAULT_HUB_BRIDGE_BACKEND = "dev-chain"
DEFAULT_LOCAL_TEST_DEPLOYMENTS_CONTAINER_DIR = "/app/runtime/deployments"
DEFAULT_LOCAL_TEST_DEV_CHAIN_DEPLOYMENT_CONTAINER_PATH = f"{DEFAULT_LOCAL_TEST_DEPLOYMENTS_CONTAINER_DIR}/test/latest.json"
DEFAULT_LOCAL_TEST_RUNTIME_HOST_DIR = REPO_ROOT / "runtime" / "hub" / "test-exp-fdb"
DEFAULT_LOCAL_COOLIFY_TOKEN_FILE = REPO_ROOT / "runtime" / "coolify-local-docker" / "api-token.txt"
DEFAULT_APPLICATIONS_SERVICE_ENV_FILE = REPO_ROOT / "runtime" / "applications_service" / "applications.env"
DEFAULT_BRIDGE_SIGNER_ENV_KEY = "MAIN_COMPUTER_BRIDGE_SIGNER_BUNDLE_B64"
DEFAULT_BRIDGE_SIGNER_RELATIVE_PATH = "private/bridge-signer/bridge-signer-bundle.json"
DEFAULT_REMOTE_CONTRACTS_RELATIVE_PATH = "public/contracts.json"
BRIDGE_SIGNER_SCHEMA = "main-computer.bridge-signer.v1"
HUB_IMPLEMENTATION_REGULAR = "regular"
HUB_IMPLEMENTATION_EXP_FDB = "exp-fdb"
HUB_IMPLEMENTATION_CHOICES = (HUB_IMPLEMENTATION_EXP_FDB,)


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



def parse_key_value_text(raw: object) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in str(raw or "").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#") or "=" not in clean:
            continue
        key, value = clean.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def read_key_value_file(path: Path) -> dict[str, str]:
    try:
        return parse_key_value_text(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}


def read_token_text(raw: object) -> str:
    text = str(raw or "").strip()
    values = parse_key_value_text(text)
    if values.get("token"):
        return values["token"].strip()
    if text and "\n" not in text and "=" not in text and not text.lstrip().startswith("#"):
        return text
    return ""


def read_token_file(path: Path) -> str:
    try:
        return read_token_text(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""


def applications_service_env_file(args: argparse.Namespace | None = None) -> Path:
    explicit = str(getattr(args, "applications_service_env_file", "") or "").strip()
    if explicit:
        path = Path(explicit)
        return path if path.is_absolute() else REPO_ROOT / path
    env_path = str(os.environ.get("MAIN_COMPUTER_APPLICATIONS_ENV_FILE") or "").strip()
    if env_path:
        path = Path(env_path)
        return path if path.is_absolute() else REPO_ROOT / path
    return DEFAULT_APPLICATIONS_SERVICE_ENV_FILE


def applications_service_env_values(args: argparse.Namespace | None = None) -> dict[str, str]:
    return read_key_value_file(applications_service_env_file(args))


def local_coolify_state_dir(args: argparse.Namespace | None = None) -> Path:
    explicit = str(getattr(args, "local_coolify_state_dir", "") or "").strip()
    if explicit:
        path = Path(explicit)
        return path if path.is_absolute() else REPO_ROOT / path
    env_dir = str(os.environ.get("MAIN_COMPUTER_COOLIFY_STATE_DIR") or "").strip()
    if env_dir:
        path = Path(env_dir)
        return path if path.is_absolute() else REPO_ROOT / path
    app_env = applications_service_env_values(args)
    app_state = str(app_env.get("COOLIFY_LOCAL_STATE") or "").strip()
    if app_state:
        path = Path(app_state)
        return path if path.is_absolute() else REPO_ROOT / path
    return REPO_ROOT / "runtime" / "coolify-local-docker"


def local_coolify_token_file(args: argparse.Namespace | None = None) -> Path:
    explicit = str(getattr(args, "local_coolify_token_file", "") or "").strip()
    if explicit:
        path = Path(explicit)
        return path if path.is_absolute() else REPO_ROOT / path
    env_file = str(os.environ.get("MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE") or "").strip()
    if env_file:
        path = Path(env_file)
        return path if path.is_absolute() else REPO_ROOT / path
    return local_coolify_state_dir(args) / "api-token.txt"


def local_coolify_url(args: argparse.Namespace | None = None) -> str:
    explicit = str(getattr(args, "coolify_url", "") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    env_url = str(os.environ.get("MAIN_COMPUTER_COOLIFY_LOCAL_URL") or "").strip()
    if env_url:
        return env_url.rstrip("/")
    token_values = read_key_value_file(local_coolify_token_file(args))
    dashboard = str(token_values.get("dashboard") or "").strip()
    if dashboard:
        return dashboard.rstrip("/")
    app_env = applications_service_env_values(args)
    app_port = str(app_env.get("APP_PORT") or "").strip()
    if app_port:
        return f"http://127.0.0.1:{app_port}"
    return DEFAULT_LOCAL_COOLIFY_URL


def resolve_token(args: argparse.Namespace) -> tuple[str, str]:
    explicit = str(getattr(args, "coolify_token", "") or "").strip()
    if explicit:
        return explicit, "--coolify-token"

    if is_local_test_args(args):
        local_env = str(os.environ.get("MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN") or "").strip()
        if local_env:
            return local_env, "env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN"

    env_name = str(getattr(args, "coolify_token_env", "") or DEFAULT_TOKEN_ENV).strip()
    if env_name:
        value = os.environ.get(env_name)
        if value and value.strip():
            return value.strip(), f"env:{env_name}"

    token_file = str(getattr(args, "coolify_token_file", "") or "").strip()
    if token_file:
        path = Path(token_file)
        if not path.is_absolute():
            path = REPO_ROOT / path
        value = read_token_file(path)
        if value:
            return value, f"file:{path}"

    if is_local_test_args(args):
        local_path = local_coolify_token_file(args)
        value = read_token_file(local_path)
        if value:
            return value, f"local-file:{local_path}"

    raise CoolifyHubDeployError(
        f"Coolify token is required. Set {env_name or DEFAULT_TOKEN_ENV}, pass --coolify-token-file, "
        "or use an existing Website Builder/local Coolify state via MAIN_COMPUTER_COOLIFY_STATE_DIR "
        "or runtime/applications_service/applications.env before `apply test`."
    )

def client_from_args(args: argparse.Namespace) -> CoolifyClient:
    token, _source = resolve_token(args)
    coolify_url = local_coolify_url(args) if is_local_test_args(args) else args.coolify_url
    return CoolifyClient(
        coolify_url,
        token,
        timeout_s=args.coolify_timeout_s,
        retries=args.coolify_retries,
        retry_sleep_s=args.coolify_retry_sleep_s,
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
        raise CoolifyHubDeployError(f"JSON-RPC {method} failed for {url}: {type(exc).__name__}: {exc}") from exc
    if "error" in payload:
        raise CoolifyHubDeployError(f"{method} returned JSON-RPC error: {payload['error']}")
    return payload.get("result")


def verify_rpc(profile: HubNetworkProfile, args: argparse.Namespace) -> dict[str, Any]:
    if not profile.chain_rpc_url:
        raise CoolifyHubDeployError(f"Hub network {profile.network_key!r} has no chain_rpc_url.")
    expected = hex(int(profile.chain_id or 0))
    user_agent = str(getattr(args, "rpc_user_agent", DEFAULT_JSON_RPC_USER_AGENT) or "").strip()
    chain_id = str(json_rpc(profile.chain_rpc_url, "eth_chainId", timeout_s=args.rpc_timeout_s, user_agent=user_agent))
    if chain_id.lower() != expected.lower():
        raise CoolifyHubDeployError(
            f"RPC chain id mismatch for {profile.network_key}: expected {expected}, got {chain_id}."
        )
    block_hex = str(json_rpc(profile.chain_rpc_url, "eth_blockNumber", timeout_s=args.rpc_timeout_s, user_agent=user_agent))
    block_number = int(block_hex, 16)
    return {"ok": True, "rpc_url": profile.chain_rpc_url, "chain_id": chain_id, "block_number": block_number}


def is_local_test_profile(profile: HubNetworkProfile | None) -> bool:
    return bool(profile is not None and profile.network_key == DEFAULT_LOCAL_TEST_NETWORK and profile.kind == "test")


def uses_fdb_sidecar_service(profile: HubNetworkProfile | None, args: argparse.Namespace | None = None) -> bool:
    """Return true when Coolify should manage Hub and FDB in one service stack.

    Local ``test`` already uses this shape so uncommitted sources can be staged
    into a raw Docker Compose service. Remote ``testnet`` uses the same FDB
    sidecar pattern because a Dockerfile Application can only start the Hub
    container; it cannot create the required FoundationDB coordinator or the
    sidecar-facing fdb.cluster file.
    """

    if profile is None:
        return False
    implementation = hub_implementation(args) if args is not None else HUB_IMPLEMENTATION_EXP_FDB
    if implementation != HUB_IMPLEMENTATION_EXP_FDB:
        return False
    return is_local_test_profile(profile) or str(profile.network_key).strip().lower() in DEFAULT_REMOTE_SIDECAR_FDB_NETWORKS


def is_remote_fdb_sidecar_profile(profile: HubNetworkProfile | None, args: argparse.Namespace | None = None) -> bool:
    return bool(uses_fdb_sidecar_service(profile, args) and not is_local_test_profile(profile))


def is_local_test_args(args: argparse.Namespace | None) -> bool:
    return str(getattr(args, "network", "") or "").strip().lower() == DEFAULT_LOCAL_TEST_NETWORK


def apply_local_test_defaults(args: argparse.Namespace, profile: HubNetworkProfile) -> None:
    if not is_local_test_profile(profile):
        return
    if not str(getattr(args, "coolify_url", "") or "").strip():
        args.coolify_url = local_coolify_url(args)
    if not str(getattr(args, "coolify_project_name", "") or "").strip() and not str(getattr(args, "coolify_project_uuid", "") or "").strip():
        args.coolify_project_name = str(os.environ.get("MAIN_COMPUTER_COOLIFY_LOCAL_PROJECT") or DEFAULT_LOCAL_COOLIFY_PROJECT_NAME)
    if not str(getattr(args, "coolify_environment_name", "") or "").strip() and not str(getattr(args, "coolify_environment_uuid", "") or "").strip():
        args.coolify_environment_name = str(os.environ.get("MAIN_COMPUTER_COOLIFY_LOCAL_ENVIRONMENT") or DEFAULT_LOCAL_COOLIFY_ENVIRONMENT_NAME)
    if not str(getattr(args, "coolify_server_name", "") or "").strip() and not str(getattr(args, "coolify_server_uuid", "") or "").strip():
        args.coolify_server_name = str(os.environ.get("MAIN_COMPUTER_COOLIFY_LOCAL_SERVER") or DEFAULT_LOCAL_COOLIFY_SERVER_NAME)
    if not str(getattr(args, "hub_runtime_dir", "") or "").strip():
        args.hub_runtime_dir = str(os.environ.get("MAIN_COMPUTER_HUB_TEST_RUNTIME_DIR") or DEFAULT_LOCAL_TEST_HUB_RUNTIME_DIR)


def coolify_deploy_profile(profile: HubNetworkProfile, args: argparse.Namespace) -> HubNetworkProfile:
    apply_local_test_defaults(args, profile)
    if is_local_test_profile(profile):
        # Local ``test`` is operator-facing localhost in hub_networks.json, but a
        # Coolify container must bind on all interfaces.  Keep the public Hub URL
        # and RPC check URL local to the operator, only changing the in-container
        # bind host.
        return replace(profile, hub_bind_host="0.0.0.0")
    return profile


def validate_coolify_profile(profile: HubNetworkProfile) -> None:
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
            f"Hub network {profile.network_key!r} is not deployable until these fields are set: "
            + ", ".join(missing)
        )
    if profile.kind not in {"test", "testnet", "mainnet"}:
        raise CoolifyHubDeployError(
            f"Refusing to deploy unsupported Hub network {profile.network_key!r} with kind {profile.kind!r}."
        )
    if profile.hub_bind_host != "0.0.0.0":
        raise CoolifyHubDeployError(
            f"Coolify Hub network {profile.network_key!r} must bind inside the container on 0.0.0.0, got {profile.hub_bind_host!r}."
        )


def validate_hub_deploy_args(profile: HubNetworkProfile, args: argparse.Namespace) -> None:
    if not is_local_test_profile(profile) and not str(getattr(args, "git_repo", "") or "").strip():
        raise CoolifyHubDeployError("--git-repo is required for remote testnet/mainnet Hub application deploys.")
    implementation = hub_implementation(args)
    namespace = exp_fdb_namespace(profile, args)
    if not namespace.strip():
        raise CoolifyHubDeployError("Experimental FDB Hub namespace must not be empty.")
    runtime_dir = str(
        getattr(args, "hub_runtime_dir", "")
        or hub_state_mount_path(profile.network_key, implementation=implementation)
    )
    cluster_file = exp_fdb_cluster_file_path(profile, args, runtime_dir=runtime_dir)
    if not cluster_file.strip():
        raise CoolifyHubDeployError("Experimental FDB Hub cluster file path must not be empty.")
    if hub_enable_bridge_writes(args) and hub_enable_smoke_bridge(args):
        raise CoolifyHubDeployError("--enable-bridge-writes and --enable-smoke-bridge are mutually exclusive.")


def hub_implementation(args: argparse.Namespace | None) -> str:
    value = str(getattr(args, "hub_implementation", HUB_IMPLEMENTATION_EXP_FDB) or HUB_IMPLEMENTATION_EXP_FDB).strip().lower()
    if value == HUB_IMPLEMENTATION_REGULAR:
        raise CoolifyHubDeployError("The regular Hub implementation has been deprecated; use exp-fdb.")
    if value not in HUB_IMPLEMENTATION_CHOICES:
        raise CoolifyHubDeployError(
            f"Unknown Hub implementation {value!r}; expected one of {', '.join(HUB_IMPLEMENTATION_CHOICES)}."
        )
    return value


def is_exp_fdb_hub(args: argparse.Namespace | None) -> bool:
    return hub_implementation(args) == HUB_IMPLEMENTATION_EXP_FDB


def hub_service_name(
    network: str,
    *,
    implementation: str = HUB_IMPLEMENTATION_EXP_FDB,
    replace_regular_hub: bool = False,
) -> str:
    # The FDB-backed Hub is now the only hosted Hub implementation.  Keep the
    # public Coolify application name stable instead of using the old
    # side-by-side experimental service name.
    return f"main-computer-{network}-hub"


def hub_state_mount_path(network: str, *, implementation: str = HUB_IMPLEMENTATION_EXP_FDB) -> str:
    if str(network or "").strip().lower() == DEFAULT_LOCAL_TEST_NETWORK:
        return DEFAULT_LOCAL_TEST_HUB_RUNTIME_DIR
    return f"/data/main-computer/hub/{network}-exp-fdb"


def hub_volume_name(network: str, *, implementation: str = HUB_IMPLEMENTATION_EXP_FDB) -> str:
    return f"{network}_exp_fdb_hub_state"


def container_posix_path(value: str) -> str:
    return str(value or "").replace("\\", "/")


def container_posix_dirname(value: str) -> str:
    """Return the parent directory of a Linux/container path.

    The deployer runs on Windows for the operator, but these paths are consumed
    inside Linux containers.  Do not use pathlib.Path here because it treats
    ``/data/foo/bar`` differently depending on the host OS.
    """

    clean = container_posix_path(value).rstrip("/")
    if not clean:
        return "."
    parent = clean.rsplit("/", 1)[0] if "/" in clean else "."
    return parent or "/"


def exp_fdb_cluster_file_path(profile: HubNetworkProfile, args: argparse.Namespace, *, runtime_dir: str) -> str:
    explicit = str(getattr(args, "fdb_cluster_file", "") or "").strip()
    if explicit:
        return container_posix_path(explicit)
    clean_runtime_dir = container_posix_path(runtime_dir).rstrip("/")
    return f"{clean_runtime_dir}/fdb.cluster"


def exp_fdb_namespace(profile: HubNetworkProfile, args: argparse.Namespace) -> str:
    explicit = str(getattr(args, "fdb_namespace", "") or "").strip()
    if explicit:
        return explicit
    return f"main-computer-{profile.network_key}-exp-fdb"


def hub_chain_rpc_url(profile: HubNetworkProfile, args: argparse.Namespace) -> str:
    explicit = str(getattr(args, "hub_chain_rpc_url", "") or "").strip()
    if explicit:
        return explicit
    if is_local_test_profile(profile):
        return str(os.environ.get("MAIN_COMPUTER_HUB_TEST_CONTAINER_RPC_URL") or DEFAULT_LOCAL_TEST_CONTAINER_RPC_URL)
    return str(profile.chain_rpc_url or "")



def hub_bridge_backend(args: argparse.Namespace | None = None) -> str:
    explicit = str(getattr(args, "bridge_backend", "") or "").strip().lower()
    if explicit:
        return explicit
    env_value = str(os.environ.get("MAIN_COMPUTER_HUB_BRIDGE_BACKEND") or "").strip().lower()
    return env_value or DEFAULT_HUB_BRIDGE_BACKEND


def hub_allow_missing_bridge_signer(profile: HubNetworkProfile, args: argparse.Namespace | None = None) -> bool:
    """Return whether this Hub should boot with public contracts but no private signer manifest."""

    if hub_bridge_backend(args) in {"mock", "mock-chain", "mock-chain-lite"}:
        return False
    if hub_enable_bridge_writes(args):
        return False
    if bool(getattr(args, "allow_missing_bridge_signer", False)):
        return True
    if str(os.environ.get("MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return True

    # An explicit public contracts file is enough for remote read/status-only
    # contract startup.  Do not require the private signer bundle unless bridge
    # writes were explicitly enabled above.
    if str(getattr(args, "contracts_path", "") or "").strip():
        return True

    # Public remote testnet deployments intentionally carry only contract
    # addresses in the image.  They should expose health/status without private
    # admin wallet metadata, while bridge write paths stay disabled in the Hub.
    return profile.network_key == "testnet" or profile.kind == "testnet"


def hub_enable_smoke_bridge(args: argparse.Namespace | None = None) -> bool:
    """Return whether admin-only smoke bridge wallet paths may be loaded.

    This is intentionally never inferred from testnet/mainnet profile defaults.
    Normal deployed user/requester/worker paths must not depend on smoke_client.
    """

    if hub_bridge_backend(args) in {"mock", "mock-chain", "mock-chain-lite"}:
        return False
    if bool(getattr(args, "enable_smoke_bridge", False)):
        return True
    return str(os.environ.get("MAIN_COMPUTER_HUB_ENABLE_SMOKE_BRIDGE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def dev_chain_deployment_path(profile: HubNetworkProfile, args: argparse.Namespace, *, container_path: bool = True) -> str:
    explicit = str(getattr(args, "dev_chain_deployment_path", "") or "").strip()
    if explicit:
        if container_path:
            return container_posix_path(explicit)
        return explicit
    if is_local_test_profile(profile) and container_path:
        return DEFAULT_LOCAL_TEST_DEV_CHAIN_DEPLOYMENT_CONTAINER_PATH
    return f"/app/runtime/deployments/{profile.network_key}/latest.json"


def contracts_path(profile: HubNetworkProfile, args: argparse.Namespace, *, container_path: bool = True) -> str:
    explicit = str(getattr(args, "contracts_path", "") or "").strip()
    if explicit:
        return container_posix_path(explicit) if container_path else explicit
    return f"/app/main_computer/config/{profile.network_key}_contracts.json"



def hub_enable_bridge_writes(args: argparse.Namespace | None = None) -> bool:
    """Return whether the remote Hub should require a non-smoke bridge signer bundle."""

    if hub_bridge_backend(args) in {"mock", "mock-chain", "mock-chain-lite"}:
        return False
    if bool(getattr(args, "no_bridge_writes", False)):
        return False
    if bool(getattr(args, "enable_bridge_writes", False)):
        return True
    return str(os.environ.get("MAIN_COMPUTER_HUB_ENABLE_BRIDGE_WRITES") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def bridge_signer_sync_requested(args: argparse.Namespace | None = None) -> bool:
    """Return whether the standard deploy path should push signer material through Coolify."""

    if hub_bridge_backend(args) in {"mock", "mock-chain", "mock-chain-lite"}:
        return False
    if bool(getattr(args, "no_bridge_writes", False)):
        return False
    return bool(getattr(args, "sync_bridge_signer", False)) or hub_enable_bridge_writes(args)


def bridge_signer_env_key(args: argparse.Namespace | None = None) -> str:
    explicit = str(getattr(args, "bridge_signer_env_key", "") or "").strip()
    return explicit or DEFAULT_BRIDGE_SIGNER_ENV_KEY


def bridge_signer_remote_path(profile: HubNetworkProfile, args: argparse.Namespace, *, runtime_dir: str) -> str:
    explicit = str(getattr(args, "bridge_signer_remote_path", "") or "").strip()
    if explicit:
        return container_posix_path(explicit)
    return container_posix_path(f"{runtime_dir.rstrip('/')}/{DEFAULT_BRIDGE_SIGNER_RELATIVE_PATH}")


def remote_contracts_path(profile: HubNetworkProfile, args: argparse.Namespace, *, runtime_dir: str) -> str:
    """Return the runtime-mounted public contract config path used by bridge-writer Hubs."""

    explicit = str(getattr(args, "remote_contracts_path", "") or "").strip()
    if explicit:
        return container_posix_path(explicit)
    return container_posix_path(f"{runtime_dir.rstrip('/')}/{DEFAULT_REMOTE_CONTRACTS_RELATIVE_PATH}")


def hub_contracts_path_for_runtime(profile: HubNetworkProfile, args: argparse.Namespace, *, runtime_dir: str) -> str:
    """Return the contracts path that the remote Hub process should read.

    Bridge-writer Hubs must not read the static config baked into the Git image:
    that file can lag behind the local cutover metadata.  The deployer stages a
    runtime contract config beside the private signer bundle and points Hubs at
    that mounted file instead.
    """

    if hub_enable_bridge_writes(args):
        return remote_contracts_path(profile, args, runtime_dir=runtime_dir)
    return contracts_path(profile, args)


def bridge_signer_plan(profile: HubNetworkProfile, args: argparse.Namespace, *, runtime_dir: str) -> dict[str, Any]:
    return {
        "sync_requested": bridge_signer_sync_requested(args),
        "bridge_writes_enabled": hub_enable_bridge_writes(args),
        "env_key": bridge_signer_env_key(args),
        "remote_path": bridge_signer_remote_path(profile, args, runtime_dir=runtime_dir),
        "source_manifest": str(bridge_signer_source_manifest_path(profile, args)),
        "wallet_source": str(getattr(args, "bridge_controller_wallet_path", "") or ""),
    }


def bridge_signer_source_manifest_path(profile: HubNetworkProfile, args: argparse.Namespace) -> Path:
    explicit = str(getattr(args, "bridge_signer_source_manifest", "") or "").strip()
    path = Path(explicit) if explicit else REPO_ROOT / "runtime" / "deployments" / profile.network_key / "latest.json"
    return path if path.is_absolute() else REPO_ROOT / path


def _source_manifest_has_hub_admin(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and isinstance(payload.get("hub_admin"), dict)


def bridge_signer_source_manifest_available(profile: HubNetworkProfile, args: argparse.Namespace) -> bool:
    """Return whether the default/private source manifest is present enough to infer signer sync."""

    path = bridge_signer_source_manifest_path(profile, args)
    return path.is_file() and _source_manifest_has_hub_admin(path)


def apply_bridge_signer_defaults(profile: HubNetworkProfile, args: argparse.Namespace) -> None:
    """Default contract Hub deploys into signer-enabled mode when local signer metadata exists."""

    if hub_bridge_backend(args) in {"mock", "mock-chain", "mock-chain-lite"}:
        return
    if bool(getattr(args, "no_bridge_writes", False)):
        return
    if hub_enable_smoke_bridge(args):
        return
    if bool(getattr(args, "allow_missing_bridge_signer", False)):
        return
    if hub_enable_bridge_writes(args):
        args.sync_bridge_signer = True
        return
    if bridge_signer_source_manifest_available(profile, args):
        args.enable_bridge_writes = True
        args.sync_bridge_signer = True
        if not str(getattr(args, "bridge_signer_source_manifest", "") or "").strip():
            args.bridge_signer_source_manifest = str(Path("runtime") / "deployments" / profile.network_key / "latest.json")


def _looks_like_address(value: object) -> bool:
    return bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", str(value or "").strip()))


def _looks_like_private_key(value: object) -> bool:
    return bool(re.fullmatch(r"0x[0-9a-fA-F]{64}", str(value or "").strip()))


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CoolifyHubDeployError(f"missing {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CoolifyHubDeployError(f"invalid {label} JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise CoolifyHubDeployError(f"{label} JSON must be an object: {path}")
    return payload


def _resolve_local_runtime_path(path_text: str, *, manifest_path: Path) -> Path:
    raw = Path(str(path_text or "").strip())
    if raw.is_absolute():
        return raw
    candidates = [
        REPO_ROOT / raw,
        manifest_path.parent / raw,
    ]
    if raw.parts and raw.parts[0] != "runtime":
        candidates.append(REPO_ROOT / "runtime" / raw)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _contract_record_from_payload(payload: dict[str, Any], key: str) -> dict[str, Any]:
    contracts = payload.get("contracts") if isinstance(payload.get("contracts"), dict) else {}
    raw = contracts.get(key) if isinstance(contracts, dict) else None
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        return {"address": raw}
    raw = payload.get(key)
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        return {"address": raw}
    return {}


def _build_bridge_signer_bundle_payload(profile: HubNetworkProfile, args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = bridge_signer_source_manifest_path(profile, args)
    manifest = _load_json_object(manifest_path, label="bridge signer source manifest")

    hub_admin = manifest.get("hub_admin")
    if not isinstance(hub_admin, dict):
        raise CoolifyHubDeployError(f"bridge signer source manifest is missing hub_admin metadata: {manifest_path}")
    controller_address = str(hub_admin.get("address") or "").strip()
    if not _looks_like_address(controller_address):
        raise CoolifyHubDeployError(f"invalid hub_admin.address in bridge signer source manifest: {controller_address!r}")

    explicit_wallet = str(getattr(args, "bridge_controller_wallet_path", "") or "").strip()
    wallet_path_text = explicit_wallet or str(hub_admin.get("wallet_path") or "").strip()
    inline_private_key = str(hub_admin.get("private_key") or "").strip()
    wallet_source = "hub_admin.private_key"
    if wallet_path_text:
        wallet_path = _resolve_local_runtime_path(wallet_path_text, manifest_path=manifest_path)
        if wallet_path.exists():
            wallet = _load_json_object(wallet_path, label="bridge controller wallet")
            wallet_address = str(wallet.get("address") or "").strip()
            private_key = str(wallet.get("private_key") or "").strip()
            if not _looks_like_address(wallet_address):
                raise CoolifyHubDeployError(f"bridge controller wallet file does not contain a valid address: {wallet_path}")
            if wallet_address.lower() != controller_address.lower():
                raise CoolifyHubDeployError(
                    f"bridge controller wallet address does not match hub_admin.address: {wallet_address} != {controller_address}"
                )
            if not _looks_like_private_key(private_key):
                raise CoolifyHubDeployError(f"bridge controller wallet file does not contain a valid private_key: {wallet_path}")
            wallet_source = str(wallet_path)
        elif inline_private_key:
            private_key = inline_private_key
        else:
            raise CoolifyHubDeployError(f"missing bridge controller wallet file: {wallet_path}")
    elif inline_private_key:
        private_key = inline_private_key
    else:
        raise CoolifyHubDeployError(f"bridge signer source manifest is missing hub_admin.wallet_path or hub_admin.private_key: {manifest_path}")
    if not _looks_like_private_key(private_key):
        raise CoolifyHubDeployError(f"hub_admin.private_key in bridge signer source manifest is not a valid private key: {manifest_path}")

    chain = manifest.get("chain") if isinstance(manifest.get("chain"), dict) else {}
    escrow = _contract_record_from_payload(manifest, "hub_credit_bridge_escrow")
    escrow_address = str(escrow.get("address") or "").strip()
    if not _looks_like_address(escrow_address):
        raise CoolifyHubDeployError(f"bridge signer source manifest is missing hub_credit_bridge_escrow.address: {manifest_path}")
    contract_controller = str(escrow.get("bridge_controller_address") or "").strip()
    if contract_controller and contract_controller.lower() != controller_address.lower():
        raise CoolifyHubDeployError(
            f"hub_admin.address does not match hub_credit_bridge_escrow.bridge_controller_address: {controller_address} != {contract_controller}"
        )

    chain_id = profile.chain_id
    raw_chain_id = escrow.get("chain_id") or chain.get("chain_id")
    if chain_id is None and raw_chain_id not in (None, ""):
        try:
            chain_id = int(raw_chain_id, 0) if isinstance(raw_chain_id, str) else int(raw_chain_id)
        except (TypeError, ValueError) as exc:
            raise CoolifyHubDeployError(f"invalid chain id in bridge signer source manifest: {raw_chain_id!r}") from exc
    chain_rpc_url = hub_chain_rpc_url(profile, args) or str(
        manifest.get("chain_rpc_url")
        or chain.get("rpc_url")
        or chain.get("host_rpc_url")
        or chain.get("container_rpc_url")
        or ""
    ).strip()
    if not chain_rpc_url:
        raise CoolifyHubDeployError("bridge signer bundle requires a chain RPC URL.")

    return {
        "schema": BRIDGE_SIGNER_SCHEMA,
        "network": profile.network_key,
        "chain_id": chain_id,
        "chain_rpc_url": chain_rpc_url,
        "contracts": {
            "hub_credit_bridge_escrow": {
                "address": escrow_address,
                "bridge_controller_address": controller_address,
            }
        },
        "bridge_controller": {
            "address": controller_address,
            "private_key": private_key,
        },
        "source": {
            "manifest_path": str(manifest_path),
            "wallet_path": wallet_source,
            "run_id": str(manifest.get("run_id") or ""),
            "schema": str(manifest.get("schema") or ""),
        },
    }


def build_bridge_signer_bundle(profile: HubNetworkProfile, args: argparse.Namespace) -> dict[str, Any]:
    """Build a private bridge signer bundle and a redacted summary for Coolify env sync."""

    bundle = _build_bridge_signer_bundle_payload(profile, args)
    raw = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return {
        "ok": True,
        "env_key": bridge_signer_env_key(args),
        "bundle_b64": encoded,
        "bundle_sha256": hashlib.sha256(raw).hexdigest(),
        "bundle_bytes": len(raw),
        "source_manifest": bundle["source"]["manifest_path"],
        "wallet_path": bundle["source"]["wallet_path"],
        "network": bundle["network"],
        "chain_id": bundle["chain_id"],
        "escrow_address": bundle["contracts"]["hub_credit_bridge_escrow"]["address"],
        "bridge_controller_address": bundle["bridge_controller"]["address"],
    }


def local_test_deployments_host_dir(args: argparse.Namespace | None = None) -> Path:
    source_root = local_test_source_dir(args)
    return source_root / "runtime" / "deployments"


def local_test_deployments_bind_source(args: argparse.Namespace | None = None) -> str:
    return docker_desktop_host_bind_source(local_test_deployments_host_dir(args))

def command_token(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # Coolify's application start_command validator rejects shell quoting in some
    # local builds.  Keep command tokens single-word and shell-neutral.
    return re.sub(r"[^A-Za-z0-9_./:=@+,%#-]+", "-", text).strip() or "value"


def hub_command_parts(profile: HubNetworkProfile, runtime_dir: str, args: argparse.Namespace) -> list[str]:
    parts = [
        "python",
        "/app/exp-fdb-hub.py",
    ]
    runtime_env_file = hub_runtime_env_file_path(profile, args, runtime_dir=runtime_dir)
    if runtime_env_file:
        parts.extend(["--runtime-env-file", runtime_env_file])
    parts.extend([
        "--host",
        profile.hub_bind_host,
        "--port",
        str(profile.hub_bind_port),
        "--hub-url",
        profile.hub_url,
        "--hub-root",
        container_posix_path(runtime_dir),
        "--cluster-file",
        exp_fdb_cluster_file_path(profile, args, runtime_dir=runtime_dir),
        "--namespace",
        exp_fdb_namespace(profile, args),
        "--network-key",
        profile.network_key,
        "--network-display-name",
        command_token(profile.display_name),
        "--network-kind",
        profile.kind,
        "--no-fdb-autostart",
        "--no-activate-cached-native-client",
        "--require-multisession-auth",
        "--bridge-backend",
        hub_bridge_backend(args),
    ])
    if hub_bridge_backend(args) not in {"mock", "mock-chain", "mock-chain-lite"}:
        allow_missing_bridge_signer = hub_allow_missing_bridge_signer(profile, args)
        explicit_deployment_path = str(getattr(args, "dev_chain_deployment_path", "") or "").strip()
        if hub_enable_bridge_writes(args):
            parts.extend(["--dev-chain-deployment-path", bridge_signer_remote_path(profile, args, runtime_dir=runtime_dir)])
            parts.append("--strict-bridge-signer")
        elif explicit_deployment_path or not allow_missing_bridge_signer:
            parts.extend(["--dev-chain-deployment-path", dev_chain_deployment_path(profile, args)])
        parts.extend(["--contracts-path", hub_contracts_path_for_runtime(profile, args, runtime_dir=runtime_dir)])
        if allow_missing_bridge_signer:
            parts.append("--allow-missing-bridge-signer")
        if (not hub_enable_bridge_writes(args)) and hub_enable_smoke_bridge(args):
            parts.append("--enable-smoke-bridge")
    if profile.chain_id is not None:
        parts.extend(["--chain-id", str(profile.chain_id)])
    runtime_chain_rpc_url = hub_chain_rpc_url(profile, args)
    if runtime_chain_rpc_url:
        parts.extend(["--chain-rpc-url", runtime_chain_rpc_url])
    return parts


def hub_start_command(profile: HubNetworkProfile, runtime_dir: str, args: argparse.Namespace | None = None) -> str:
    """Return the full exp-FDB Hub command for diagnostics/local compose bootstrap."""

    assert args is not None
    parts = (
        hub_launcher_command_parts(profile, runtime_dir, args)
        if is_local_test_profile(profile)
        else hub_command_parts(profile, runtime_dir, args)
    )
    return " ".join(command_token(part) for part in parts)


def hub_launcher_command_parts(profile: HubNetworkProfile, runtime_dir: str, args: argparse.Namespace) -> list[str]:
    parts = [
        "python",
        "/app/run-exp-fdb-hub.py",
        "--network",
        profile.network_key,
        "--port",
        str(profile.hub_bind_port),
    ]
    runtime_env_file = hub_runtime_env_file_path(profile, args, runtime_dir=runtime_dir)
    if runtime_env_file:
        parts.extend(["--runtime-env-file", runtime_env_file])
    return parts


def hub_launcher_start_command(profile: HubNetworkProfile, runtime_dir: str, args: argparse.Namespace | None = None) -> str:
    """Return the short Coolify Application start command.

    Coolify stores application start_command in a narrow database column and
    some Dockerfile application paths may still run the image CMD.  Keep this
    value short and let /app/run-exp-fdb-hub.py reconstruct the full command
    from the selected network and runtime environment.
    """

    assert args is not None
    parts = [
        "python",
        "/app/run-exp-fdb-hub.py",
        "--network",
        profile.network_key,
        "--port",
        str(profile.hub_bind_port),
    ]
    runtime_env_file = hub_runtime_env_file_path(profile, args, runtime_dir=runtime_dir)
    if runtime_env_file:
        parts.extend(["--runtime-env-file", runtime_env_file])
    return " ".join(command_token(part) for part in parts)


def default_dockerfile_location(profile: HubNetworkProfile, args: argparse.Namespace | None = None) -> str:
    return DEFAULT_EXP_FDB_DOCKERFILE_LOCATION


def effective_dockerfile_location(profile: HubNetworkProfile, args: argparse.Namespace) -> str:
    return str(getattr(args, "dockerfile_location", "") or default_dockerfile_location(profile, args))


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


def hub_public_status_url(profile: HubNetworkProfile, args: argparse.Namespace) -> str:
    """Return the externally reachable Hub status URL.

    Coolify application domain payloads include the backend container port so the
    reverse proxy can target the right upstream. The public readiness probe must
    use the browser-facing Hub URL instead; probing ``https://host:<backend-port>``
    times out when that backend port is only exposed inside the Coolify network.
    """

    return str(profile.hub_url or "").strip().rstrip("/") + str(args.health_path or DEFAULT_HEALTH_PATH)


def application_payload(
    profile: HubNetworkProfile,
    args: argparse.Namespace,
    *,
    service_name: str,
    runtime_dir: str,
) -> dict[str, Any]:
    description = f"Main Computer {profile.network_key} experimental FDB Hub"

    payload: dict[str, Any] = {
        "name": service_name,
        "description": description,
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
        "start_command": hub_launcher_start_command(profile, runtime_dir, args),
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


def storage_payload(profile: HubNetworkProfile, *, runtime_dir: str, args: argparse.Namespace | None = None) -> dict[str, Any]:
    implementation = hub_implementation(args)
    return {
        "type": "persistent",
        "name": hub_volume_name(profile.network_key, implementation=implementation),
        "mount_path": container_posix_path(runtime_dir),
        "host_path": container_posix_path(runtime_dir),
    }



def sh_quote(value: object) -> str:
    return shlex.quote(str(value))


def yaml_quote(value: object) -> str:
    return json.dumps(str(value))



def local_test_source_dir(args: argparse.Namespace | None = None) -> Path:
    explicit = str(getattr(args, "local_source_dir", "") or "").strip()
    if explicit:
        path = Path(explicit)
        return path if path.is_absolute() else REPO_ROOT / path
    env_value = str(os.environ.get("MAIN_COMPUTER_HUB_TEST_SOURCE_DIR") or "").strip()
    if env_value:
        path = Path(env_value)
        return path if path.is_absolute() else REPO_ROOT / path
    return DEFAULT_LOCAL_TEST_SOURCE_DIR


def docker_desktop_host_bind_source(path: Path) -> str:
    """Return a host path usable by Docker Compose running inside local Coolify.

    Local Coolify runs the deployment from its Linux container while talking to
    Docker Desktop.  Windows paths must therefore be translated to Docker
    Desktop's Linux-side /run/desktop/mnt/host/<drive>/... form.
    """

    resolved = path.resolve()
    text = resolved.as_posix()
    drive = str(getattr(resolved, "drive", "") or "").rstrip(":").lower()
    if drive and re.match(r"^[a-z]$", drive):
        # Path.as_posix() on Windows starts with "C:/...".
        suffix = text[2:] if len(text) >= 2 and text[1] == ":" else text
        return f"/run/desktop/mnt/host/{drive}{suffix}"
    return text


def local_test_runtime_host_dir(args: argparse.Namespace | None = None) -> Path:
    explicit = str(getattr(args, "local_hub_runtime_host_dir", "") or "").strip()
    if explicit:
        path = Path(explicit)
        return path if path.is_absolute() else REPO_ROOT / path
    env_value = str(os.environ.get("MAIN_COMPUTER_HUB_TEST_RUNTIME_HOST_DIR") or "").strip()
    if env_value:
        path = Path(env_value)
        return path if path.is_absolute() else REPO_ROOT / path
    return DEFAULT_LOCAL_TEST_RUNTIME_HOST_DIR


def local_test_runtime_bind_source(args: argparse.Namespace | None = None) -> str:
    return docker_desktop_host_bind_source(local_test_runtime_host_dir(args))


def docker_compose_build_context(args: argparse.Namespace) -> str:
    # Website Builder local Coolify deploys stage a relative build context into
    # /data/coolify/services/<uuid> before triggering deployment.  Do the same
    # for the Hub.  Remote Git build contexts make local docker compose/buildx
    # misread the Dockerfile payload and fail with "dockerfile line greater than
    # max allowed size of 65535".
    return f"./{DEFAULT_LOCAL_TEST_BUILD_CONTEXT_DIRNAME}"



def remote_git_build_context(args: argparse.Namespace) -> str:
    """Return a Docker Compose Git build context for remote Coolify services."""

    repo = str(getattr(args, "git_repo", "") or "").strip()
    if not repo:
        raise CoolifyHubDeployError("--git-repo is required for remote exp-FDB service deploys.")
    if repo.startswith("https://github.com/") and not repo.endswith(".git"):
        repo = f"{repo}.git"
    ref = str(getattr(args, "git_commit_sha", "") or getattr(args, "git_branch", "") or "main").strip() or "main"
    base_dir = str(getattr(args, "base_directory", "") or "").strip()
    if base_dir in {"", "/"}:
        suffix = ref
    else:
        suffix = f"{ref}:{base_dir.strip('/')}"
    return f"{repo}#{suffix}"


def remote_runtime_bind_source(runtime_dir: str) -> str:
    # Remote Coolify runs on a Linux Docker host. Use the same absolute path on
    # host and container so the operator's --fdb-cluster-file path is also the
    # path where the bootstrap writes fdb.cluster.
    return container_posix_path(runtime_dir).rstrip("/")


def hub_runtime_env_file_path(profile: HubNetworkProfile, args: argparse.Namespace, *, runtime_dir: str) -> str:
    """Return the container-visible Hub runtime env file path.

    The local `test` profile gets a default host-editable file under the Hub
    runtime mount so dev/test can prove the operational model before hosted
    networks opt in.  Hosted testnet/mainnet remain unchanged unless an
    operator supplies --runtime-env-file explicitly.
    """

    explicit = str(getattr(args, "runtime_env_file", "") or "").strip()
    if explicit:
        return container_posix_path(explicit)
    if is_local_test_profile(profile):
        return container_posix_path(f"{runtime_dir.rstrip('/')}/{DEFAULT_HUB_RUNTIME_ENV_FILENAME}")
    return ""


def hub_runtime_env_defaults(profile: HubNetworkProfile, args: argparse.Namespace, *, runtime_dir: str) -> dict[str, str]:
    """Environment defaults passed to the Hub launcher before hub-runtime.env overrides."""

    values: dict[str, str] = {
        "MAIN_COMPUTER_HUB_NETWORK": profile.network_key,
        "MAIN_COMPUTER_HUB_PORT": str(profile.hub_bind_port),
        "MAIN_COMPUTER_HUB_URL": profile.hub_url,
        "MAIN_COMPUTER_HUB_ROOT": container_posix_path(runtime_dir),
        "MAIN_COMPUTER_HUB_FDB_CLUSTER_FILE": exp_fdb_cluster_file_path(profile, args, runtime_dir=runtime_dir),
        "MAIN_COMPUTER_HUB_FDB_NAMESPACE": exp_fdb_namespace(profile, args),
        "MAIN_COMPUTER_HUB_BRIDGE_BACKEND": hub_bridge_backend(args),
        "MAIN_COMPUTER_HUB_CHAIN_ID": str(profile.chain_id or ""),
        "MAIN_COMPUTER_HUB_CONTRACTS_PATH": hub_contracts_path_for_runtime(profile, args, runtime_dir=runtime_dir),
    }
    chain_rpc = hub_chain_rpc_url(profile, args)
    if chain_rpc:
        values["MAIN_COMPUTER_HUB_CHAIN_RPC_URL"] = chain_rpc
    if hub_bridge_backend(args) not in {"mock", "mock-chain", "mock-chain-lite"}:
        if hub_enable_bridge_writes(args):
            values["MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH"] = bridge_signer_remote_path(
                profile,
                args,
                runtime_dir=runtime_dir,
            )
        else:
            explicit_deployment_path = str(getattr(args, "dev_chain_deployment_path", "") or "").strip()
            if explicit_deployment_path or not hub_allow_missing_bridge_signer(profile, args):
                values["MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH"] = dev_chain_deployment_path(profile, args)
        if hub_allow_missing_bridge_signer(profile, args):
            values["MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER"] = "true"
        if hub_enable_smoke_bridge(args):
            values["MAIN_COMPUTER_HUB_ENABLE_SMOKE_BRIDGE"] = "true"
    if hub_enable_bridge_writes(args):
        values["MAIN_COMPUTER_HUB_ENABLE_BRIDGE_WRITES"] = "true"
        values["MAIN_COMPUTER_HUB_ENABLE_SMOKE_BRIDGE"] = "false"
        values["MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER"] = "false"
    runtime_env_file = hub_runtime_env_file_path(profile, args, runtime_dir=runtime_dir)
    if runtime_env_file:
        values["MAIN_COMPUTER_HUB_RUNTIME_ENV_FILE"] = runtime_env_file
    return {key: value for key, value in values.items() if str(value).strip()}


def fdb_sidecar_service_key(profile: HubNetworkProfile, *, service_name: str) -> str:
    if is_local_test_profile(profile):
        return DEFAULT_LOCAL_TEST_FDB_SERVICE_KEY
    return f"{service_name.replace('_', '-')}-fdb"


def fdb_sidecar_cluster_contents(profile: HubNetworkProfile, *, service_name: str) -> str:
    return f"docker:docker@{fdb_sidecar_service_key(profile, service_name=service_name)}:{DEFAULT_LOCAL_TEST_FDB_PORT}"


def local_test_fdb_cluster_contents() -> str:
    return DEFAULT_LOCAL_TEST_FDB_CLUSTER_CONTENTS


def hub_fdb_bootstrap_script(
    profile: HubNetworkProfile,
    args: argparse.Namespace,
    *,
    service_name: str,
    runtime_dir: str,
) -> str:
    """Return the shell wrapper that seeds FDB and execs the Hub.

    The wrapper is intentionally idempotent. It writes the cluster file that the
    Hub is configured to read, asks FDB to configure a single in-memory database
    until the coordinator is accepting commands, then execs the Hub process.
    """

    cluster_file = exp_fdb_cluster_file_path(profile, args, runtime_dir=runtime_dir)
    cluster_contents = fdb_sidecar_cluster_contents(profile, service_name=service_name)
    command_parts = (
        hub_launcher_command_parts(profile, runtime_dir, args)
        if is_local_test_profile(profile)
        else hub_command_parts(profile, runtime_dir, args)
    )
    command = " ".join(command_token(part) for part in command_parts)
    pre_start_lines: list[str] = []
    runtime_env_file = hub_runtime_env_file_path(profile, args, runtime_dir=runtime_dir)
    if runtime_env_file:
        runtime_env_dir = container_posix_dirname(runtime_env_file)
        pre_start_lines.extend(
            [
                f"mkdir -p {sh_quote(runtime_env_dir)}",
                f"touch {sh_quote(runtime_env_file)}",
            ]
            if is_local_test_profile(profile)
            else [f"mkdir -p {sh_quote(runtime_env_dir)}"]
        )
    if hub_enable_bridge_writes(args):
        signer_path = bridge_signer_remote_path(profile, args, runtime_dir=runtime_dir)
        signer_dir = container_posix_dirname(signer_path)
        signer_env = bridge_signer_env_key(args)
        pre_start_lines.extend(
            [
                f"mkdir -p {sh_quote(signer_dir)}",
                f"if [ -z \"${{{signer_env}:-}}\" ]; then echo 'missing required bridge signer bundle env {signer_env}' >&2; exit 1; fi",
                "python - <<'PY'",
                "import sys",
                "from main_computer import hub_bridge_backend",
                "if not hasattr(hub_bridge_backend, 'BridgeSignerHubBridgeBackend'):",
                "    sys.stderr.write('remote image is missing non-smoke bridge-signer backend support; commit and push the bridge-signer patch to the configured Git branch before deploying with --enable-bridge-writes\\n')",
                "    raise SystemExit(1)",
                "PY",
                "umask 077",
                f"printf '%s' \"${{{signer_env}}}\" | base64 -d > {sh_quote(signer_path)}.tmp",
                f"mv {sh_quote(signer_path)}.tmp {sh_quote(signer_path)}",
                f"chmod 600 {sh_quote(signer_path)}",
                f"unset {signer_env}",
            ]
        )
    return "\n".join(
        [
            "set -eu",
            f"mkdir -p {sh_quote(runtime_dir)}",
            f"printf '%s\\n' {sh_quote(cluster_contents)} > {sh_quote(cluster_file)}",
            *pre_start_lines,
            "for attempt in $(seq 1 90); do",
            f"  fdbcli -C {sh_quote(cluster_file)} --exec 'configure new single memory' --timeout 10 >/tmp/main-computer-fdb-configure.log 2>&1 || true",
            f"  if fdbcli -C {sh_quote(cluster_file)} --exec 'status' --timeout 10 >/tmp/main-computer-fdb-status.log 2>&1; then",
            "    break",
            "  fi",
            "  if [ \"$attempt\" = \"90\" ]; then",
            "    cat /tmp/main-computer-fdb-configure.log >&2 || true",
            "    cat /tmp/main-computer-fdb-status.log >&2 || true",
            "    exit 1",
            "  fi",
            "  sleep 1",
            "done",
            f"exec {command}",
        ]
    )


def local_test_hub_bootstrap_script(profile: HubNetworkProfile, args: argparse.Namespace, *, runtime_dir: str) -> str:
    """Return the shell wrapper used by the local Coolify test Hub service."""

    return hub_fdb_bootstrap_script(
        profile,
        args,
        service_name=hub_service_name(profile.network_key, implementation=hub_implementation(args)),
        runtime_dir=runtime_dir,
    )


def render_local_test_hub_compose(profile: HubNetworkProfile, args: argparse.Namespace, *, service_name: str, runtime_dir: str) -> str:
    runtime_dir = container_posix_path(runtime_dir).rstrip("/")
    build_context = docker_compose_build_context(args)
    dockerfile = effective_dockerfile_location(profile, args).lstrip("/") or "Dockerfile.hub.exp-fdb"
    service_key = service_name.replace("_", "-")
    fdb_service_key = DEFAULT_LOCAL_TEST_FDB_SERVICE_KEY
    runtime_bind = f"{local_test_runtime_bind_source(args)}:{runtime_dir}"
    deployments_bind = f"{local_test_deployments_bind_source(args)}:{DEFAULT_LOCAL_TEST_DEPLOYMENTS_CONTAINER_DIR}:ro"
    image = f"{service_name}:local"
    bootstrap_script = local_test_hub_bootstrap_script(profile, args, runtime_dir=runtime_dir)
    lines: list[str] = [
        f"name: {service_name}",
        "",
        "services:",
        f"  {fdb_service_key}:",
        f"    image: {yaml_quote(DEFAULT_LOCAL_TEST_FDB_IMAGE)}",
        "    restart: unless-stopped",
        "    environment:",
        f"      FDB_PORT: {yaml_quote(str(DEFAULT_LOCAL_TEST_FDB_PORT))}",
        f"      FDB_COORDINATOR_PORT: {yaml_quote(str(DEFAULT_LOCAL_TEST_FDB_PORT))}",
        "      FDB_NETWORKING_MODE: \"container\"",
        f"      FDB_CLUSTER_FILE_CONTENTS: {yaml_quote(local_test_fdb_cluster_contents())}",
        "    expose:",
        f"      - {yaml_quote(str(DEFAULT_LOCAL_TEST_FDB_PORT))}",
        "",
        f"  {service_key}:",
        "    build:",
        f"      context: {yaml_quote(build_context)}",
        f"      dockerfile: {yaml_quote(dockerfile)}",
        f"    image: {yaml_quote(image)}",
        "    pull_policy: build",
        "    restart: unless-stopped",
        "    depends_on:",
        f"      - {fdb_service_key}",
        "    ports:",
        f"      - {yaml_quote(f'127.0.0.1:{profile.hub_bind_port}:{profile.hub_bind_port}')}",
        "    environment:",
        f"      HUB_HEALTH_PORT: {yaml_quote(str(profile.hub_bind_port))}",
        *[
            f"      {key}: {yaml_quote(value)}"
            for key, value in hub_runtime_env_defaults(profile, args, runtime_dir=runtime_dir).items()
        ],
        f"      FDB_CLUSTER_FILE_CONTENTS: {yaml_quote(local_test_fdb_cluster_contents())}",
        "    extra_hosts:",
        "      - host.docker.internal:host-gateway",
        "    volumes:",
        f"      - {yaml_quote(runtime_bind)}",
        f"      - {yaml_quote(deployments_bind)}",
        "    command:",
        "      - \"sh\"",
        "      - \"-lc\"",
        f"      - {yaml_quote(bootstrap_script)}",
        "",
    ]
    return "\n".join(lines)


def render_remote_fdb_sidecar_hub_compose(profile: HubNetworkProfile, args: argparse.Namespace, *, service_name: str, runtime_dir: str) -> str:
    """Render the remote testnet Hub+FDB service stack.

    Unlike a Coolify Dockerfile Application, this raw Compose service owns both
    the Hub container and the FDB sidecar. That lets the same operator command
    create the FDB coordinator, write fdb.cluster, and start the Hub.
    """

    runtime_dir = container_posix_path(runtime_dir).rstrip("/")
    build_context = remote_git_build_context(args)
    dockerfile = effective_dockerfile_location(profile, args).lstrip("/") or "Dockerfile.hub.exp-fdb"
    service_key = service_name.replace("_", "-")
    fdb_service_key = fdb_sidecar_service_key(profile, service_name=service_name)
    cluster_contents = fdb_sidecar_cluster_contents(profile, service_name=service_name)
    runtime_bind = f"{remote_runtime_bind_source(runtime_dir)}:{runtime_dir}"
    image = f"{service_name}:remote"
    bootstrap_script = hub_fdb_bootstrap_script(profile, args, service_name=service_name, runtime_dir=runtime_dir)
    host = ""
    try:
        parsed = urllib.parse.urlsplit(profile.hub_url)
        host = str(parsed.hostname or "").strip()
    except ValueError:
        host = ""
    router_id = re.sub(r"[^A-Za-z0-9_-]+", "-", service_key).strip("-") or "main-computer-hub"
    lines: list[str] = [
        f"name: {service_name}",
        "",
        "services:",
        f"  {fdb_service_key}:",
        f"    image: {yaml_quote(DEFAULT_LOCAL_TEST_FDB_IMAGE)}",
        "    restart: unless-stopped",
        "    environment:",
        f"      FDB_PORT: {yaml_quote(str(DEFAULT_LOCAL_TEST_FDB_PORT))}",
        f"      FDB_COORDINATOR_PORT: {yaml_quote(str(DEFAULT_LOCAL_TEST_FDB_PORT))}",
        "      FDB_NETWORKING_MODE: \"container\"",
        f"      FDB_CLUSTER_FILE_CONTENTS: {yaml_quote(cluster_contents)}",
        "    expose:",
        f"      - {yaml_quote(str(DEFAULT_LOCAL_TEST_FDB_PORT))}",
        "",
        f"  {service_key}:",
        "    build:",
        f"      context: {yaml_quote(build_context)}",
        f"      dockerfile: {yaml_quote(dockerfile)}",
        f"    image: {yaml_quote(image)}",
        "    pull_policy: build",
        "    restart: unless-stopped",
        "    depends_on:",
        f"      - {fdb_service_key}",
        "    expose:",
        f"      - {yaml_quote(str(profile.hub_bind_port))}",
        "    environment:",
        f"      HUB_HEALTH_PORT: {yaml_quote(str(profile.hub_bind_port))}",
        f"      PORT: {yaml_quote(str(profile.hub_bind_port))}",
        f"      MAIN_COMPUTER_HUB_NETWORK: {yaml_quote(profile.network_key)}",
        f"      MAIN_COMPUTER_HUB_CONTRACTS_PATH: {yaml_quote(hub_contracts_path_for_runtime(profile, args, runtime_dir=runtime_dir))}",
        *(
            [f"      MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER: {yaml_quote('true')}"]
            if hub_allow_missing_bridge_signer(profile, args)
            else []
        ),
        *(
            [f"      MAIN_COMPUTER_HUB_ENABLE_SMOKE_BRIDGE: {yaml_quote('true')}"]
            if (not hub_enable_bridge_writes(args)) and hub_enable_smoke_bridge(args)
            else []
        ),
        *(
            [
                f"      MAIN_COMPUTER_HUB_ENABLE_BRIDGE_WRITES: {yaml_quote('true')}",
                f"      {bridge_signer_env_key(args)}: ${{{bridge_signer_env_key(args)}:?missing bridge signer bundle}}",
            ]
            if hub_enable_bridge_writes(args)
            else []
        ),
        f"      MAIN_COMPUTER_HUB_ROOT: {yaml_quote(runtime_dir)}",
        f"      MAIN_COMPUTER_HUB_FDB_NAMESPACE: {yaml_quote(exp_fdb_namespace(profile, args))}",
        f"      FDB_CLUSTER_FILE_CONTENTS: {yaml_quote(cluster_contents)}",
        "    volumes:",
        f"      - {yaml_quote(runtime_bind)}",
    ]
    if host:
        lines.extend(
            [
                "    labels:",
                "      - \"traefik.enable=true\"",
                f"      - {yaml_quote(f'traefik.http.routers.{router_id}.rule=Host(`{host}`)')}",
                f"      - {yaml_quote(f'traefik.http.routers.{router_id}.entryPoints=https')}",
                f"      - {yaml_quote(f'traefik.http.routers.{router_id}.tls=true')}",
                f"      - {yaml_quote(f'traefik.http.routers.{router_id}.tls.certresolver=letsencrypt')}",
                f"      - {yaml_quote(f'traefik.http.services.{router_id}.loadbalancer.server.port={profile.hub_bind_port}')}",
            ]
        )
    lines.extend(
        [
            "    command:",
            "      - \"sh\"",
            "      - \"-lc\"",
            f"      - {yaml_quote(bootstrap_script)}",
            "    healthcheck:",
            f'      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:{profile.hub_bind_port}{args.health_path} >/dev/null || exit 1"]',
            "      interval: 30s",
            "      timeout: 5s",
            "      start_period: 20s",
            "      retries: 3",
            "",
        ]
    )
    return "\n".join(lines)



def base64_text(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def render_fdb_sidecar_hub_compose(profile: HubNetworkProfile, args: argparse.Namespace, *, service_name: str, runtime_dir: str) -> str:
    if is_local_test_profile(profile):
        return render_local_test_hub_compose(profile, args, service_name=service_name, runtime_dir=runtime_dir)
    return render_remote_fdb_sidecar_hub_compose(profile, args, service_name=service_name, runtime_dir=runtime_dir)


def fdb_sidecar_service_payload(profile: HubNetworkProfile, args: argparse.Namespace, *, service_name: str, runtime_dir: str) -> dict[str, Any]:
    compose = render_fdb_sidecar_hub_compose(profile, args, service_name=service_name, runtime_dir=runtime_dir)
    payload: dict[str, Any] = {
        "server_uuid": args.coolify_server_uuid,
        "project_uuid": args.coolify_project_uuid,
        "environment_name": args.coolify_environment_name,
        "environment_uuid": args.coolify_environment_uuid,
        "name": service_name,
        "description": f"Main Computer {profile.network_key} experimental FDB Hub service",
        "docker_compose_raw": base64_text(compose),
        "instant_deploy": False,
    }
    if args.coolify_destination_uuid:
        payload["destination_uuid"] = args.coolify_destination_uuid
    return {key: value for key, value in payload.items() if value not in (None, "")}


def local_test_service_payload(profile: HubNetworkProfile, args: argparse.Namespace, *, service_name: str, runtime_dir: str) -> dict[str, Any]:
    return fdb_sidecar_service_payload(profile, args, service_name=service_name, runtime_dir=runtime_dir)


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


def project_environments_path(project_uuid: str) -> str:
    return f"/api/v1/projects/{urllib.parse.quote(project_uuid)}/environments"


def list_project_environments(
    client: CoolifyClient,
    *,
    project_uuid: str,
    tried: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    path = project_environments_path(project_uuid)
    response, environments = list_resources(client, path, "environments")
    tried.append(
        {
            "operation": "list-environments",
            "path": path,
            "response": response_to_dict(response),
            "count": len(environments),
        }
    )
    if not response.ok:
        raise CoolifyHubDeployError(f"Could not list Coolify environments for project {project_uuid!r}.")
    return environments


def select_environment_by_exact_name(items: list[dict[str, Any]], name: str) -> tuple[str, list[dict[str, Any]]]:
    clean = str(name or "").strip().lower()
    matches = [
        item
        for item in items
        if item_name(item).lower() == clean or str(item.get("name") or "").strip().lower() == clean
    ]
    if len(matches) == 1:
        return item_uuid(matches[0]), matches
    if len(matches) > 1:
        return "", matches
    return "", []


def environment_response_uuid(body: Any) -> str:
    if isinstance(body, dict):
        uuid = item_uuid(body)
        if uuid:
            return uuid
        environment = body.get("environment")
        if isinstance(environment, dict):
            uuid = item_uuid(environment)
            if uuid:
                return uuid
        data = body.get("data")
        if isinstance(data, dict):
            uuid = item_uuid(data)
            if uuid:
                return uuid
    return ""


def create_project_environment(
    client: CoolifyClient,
    *,
    project_uuid: str,
    environment_name: str,
    tried: list[dict[str, Any]],
) -> dict[str, Any]:
    path = project_environments_path(project_uuid)
    payload = {"name": environment_name}
    response = client.request("POST", path, payload)
    tried.append(
        {
            "operation": "create-environment",
            "path": path,
            "payload": payload,
            "response": response_to_dict(response),
        }
    )
    if response.ok:
        return {
            "source": "created",
            "environment_name": environment_name,
            "environment_uuid": environment_response_uuid(response.body),
            "response": response_to_dict(response),
        }
    if response.status in {409, 422}:
        return {
            "source": "create-race-or-existing",
            "environment_name": environment_name,
            "environment_uuid": "",
            "response": response_to_dict(response),
        }
    raise CoolifyHubDeployError(
        f"Coolify environment create failed with HTTP {response.status}: {response.body}"
    )


def ensure_project_environment(
    client: CoolifyClient,
    profile: HubNetworkProfile,
    args: argparse.Namespace,
    tried: list[dict[str, Any]],
) -> dict[str, Any]:
    if args.coolify_environment_uuid:
        if not str(args.coolify_environment_name or "").strip():
            args.coolify_environment_name = profile.network_key
        return {
            "source": "explicit_uuid",
            "environment_name": args.coolify_environment_name,
            "environment_uuid": args.coolify_environment_uuid,
        }

    environment_name = str(args.coolify_environment_name or "").strip() or profile.network_key
    args.coolify_environment_name = environment_name
    environments = list_project_environments(client, project_uuid=args.coolify_project_uuid, tried=tried)
    uuid, matches = select_environment_by_exact_name(environments, environment_name)
    if uuid:
        args.coolify_environment_uuid = uuid
        return {
            "source": "existing",
            "environment_name": environment_name,
            "environment_uuid": uuid,
            "matches": [item_summary(item) for item in matches],
        }
    if len(matches) > 1:
        raise CoolifyHubDeployError(
            f"Multiple Coolify environments named {environment_name!r} already exist in project "
            f"{args.coolify_project_uuid!r}; pass --coolify-environment-uuid."
        )

    if getattr(args, "no_create_environment", False):
        raise CoolifyHubDeployError(
            f"Coolify environment {environment_name!r} does not exist in project {args.coolify_project_uuid!r}. "
            "Create it in Coolify or rerun without --no-create-environment."
        )

    created = create_project_environment(
        client,
        project_uuid=args.coolify_project_uuid,
        environment_name=environment_name,
        tried=tried,
    )
    if created.get("environment_uuid"):
        args.coolify_environment_uuid = str(created["environment_uuid"])
        return created

    # Some Coolify versions return only a message after create. Re-list to get
    # the environment UUID before creating the application.
    environments = list_project_environments(client, project_uuid=args.coolify_project_uuid, tried=tried)
    uuid, matches = select_environment_by_exact_name(environments, environment_name)
    if uuid:
        args.coolify_environment_uuid = uuid
        created["environment_uuid"] = uuid
        created["matches"] = [item_summary(item) for item in matches]
        return created
    raise CoolifyHubDeployError(
        f"Created Coolify environment {environment_name!r}, but could not confirm it in project "
        f"{args.coolify_project_uuid!r}."
    )


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
    if not args.coolify_project_uuid:
        raise CoolifyHubDeployError("Coolify project is required. Pass --coolify-project-uuid or --coolify-project-name.")

    environment = ensure_project_environment(client, profile, args, tried)

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
    if not args.coolify_server_uuid:
        raise CoolifyHubDeployError("Coolify server is required. Pass --coolify-server-uuid or --coolify-server-name.")
    return {
        "project_uuid": args.coolify_project_uuid,
        "server_uuid": args.coolify_server_uuid,
        "environment_name": args.coolify_environment_name,
        "environment_uuid": args.coolify_environment_uuid,
        "environment": environment,
    }


def service_uuid_from_body(body: Any) -> str:
    if isinstance(body, dict):
        for key in ("uuid", "service_uuid", "id"):
            value = str(body.get(key) or "").strip()
            if value:
                return value
        service = body.get("service")
        if isinstance(service, dict):
            return service_uuid_from_body(service)
    return ""


def run_local_command(command: list[str], *, timeout_s: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout_s)


def docker_ps_names() -> list[str]:
    try:
        result = run_local_command(["docker", "ps", "--format", "{{.Names}}"], timeout_s=15.0)
    except Exception:
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def local_coolify_container_name(args: argparse.Namespace | None = None) -> str:
    """Return the running local Coolify container name used for service staging."""

    app_env = applications_service_env_values(args)
    for key in (
        "COOLIFY_CONTAINER",
        "COOLIFY_CONTAINER_NAME",
        "COOLIFY_LOCAL_CONTAINER",
        "COOLIFY_SERVICE_CONTAINER",
        "APP_CONTAINER_NAME",
        "CONTAINER_PREFIX",
    ):
        value = str(os.environ.get(key) or app_env.get(key) or "").strip()
        if value:
            return value

    names = docker_ps_names()
    preferred = [
        "mc-coolify-main_computer",
        "mc-coolify-main-computer",
        "coolify",
    ]
    lowered = {name.lower(): name for name in names}
    for name in preferred:
        if name.lower() in lowered:
            return lowered[name.lower()]

    def is_main_coolify_container(name: str) -> bool:
        clean = name.lower()
        if "coolify" not in clean:
            return False
        blocked = ("redis", "db", "postgres", "database", "realtime", "soketi", "proxy", "traefik")
        return not any(part in clean for part in blocked)

    candidates = [name for name in names if is_main_coolify_container(name)]
    return candidates[0] if len(candidates) == 1 else ""


def hub_build_context_sources(args: argparse.Namespace | None = None) -> tuple[Path, list[Path], list[Path]]:
    source_root = local_test_source_dir(args)
    files = [
        source_root / "Dockerfile.hub.exp-fdb",
        source_root / "pyproject.toml",
        source_root / "requirements.txt",
        source_root / "exp-fdb-hub.py",
        source_root / "run-exp-fdb-hub.py",
    ]
    dirs = [
        source_root / "main_computer",
    ]
    return source_root, files, dirs


def relative_to_source(path: Path, source_root: Path) -> str:
    try:
        return path.relative_to(source_root).as_posix()
    except ValueError:
        return path.name


def copy_hub_build_context(destination: Path, args: argparse.Namespace | None = None) -> dict[str, Any]:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    source_root, files, dirs = hub_build_context_sources(args)
    copied: list[str] = []
    missing: list[str] = []

    for source in files:
        if source.is_file():
            shutil.copy2(source, destination / source.name)
            copied.append(relative_to_source(source, source_root))
        else:
            missing.append(relative_to_source(source, source_root))

    def ignore_dir(_directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            if name in {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv"}:
                ignored.add(name)
            elif name.endswith((".pyc", ".pyo")):
                ignored.add(name)
        return ignored

    for source in dirs:
        if source.is_dir():
            shutil.copytree(source, destination / source.name, ignore=ignore_dir)
            copied.append(relative_to_source(source, source_root) + "/")
        else:
            missing.append(relative_to_source(source, source_root) + "/")

    if missing:
        raise CoolifyHubDeployError("Missing Hub build context source(s): " + ", ".join(missing))
    return {"copied": copied, "destination": str(destination), "source_root": str(source_root)}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stage_local_test_hub_build_context(args: argparse.Namespace, *, service_uuid: str) -> dict[str, Any]:
    """Stage the Hub build context into Coolify's local service workspace.

    Coolify's raw Docker Compose services deploy from /data/coolify/services/<uuid>.
    Local deploys must therefore use a relative build context that we copy into
    that workspace before triggering /deploy.
    """

    result: dict[str, Any] = {
        "ok": False,
        "service_uuid": service_uuid,
        "context": f"./{DEFAULT_LOCAL_TEST_BUILD_CONTEXT_DIRNAME}",
        "issues": [],
    }
    if not service_uuid:
        result["issues"].append("missing service UUID")
        return result

    container = local_coolify_container_name(args)
    result["container"] = container
    if not container:
        result["issues"].append("could not identify the running local Coolify container for build context staging")
        return result

    target_dir = f"/data/coolify/services/{service_uuid}/{DEFAULT_LOCAL_TEST_BUILD_CONTEXT_DIRNAME}"
    result["target_dir"] = target_dir

    with tempfile.TemporaryDirectory(prefix="main-computer-hub-coolify-context-") as temp_root:
        temp_context = Path(temp_root) / DEFAULT_LOCAL_TEST_BUILD_CONTEXT_DIRNAME
        copied = copy_hub_build_context(temp_context, args)
        result["source_context"] = copied

        mkdir = run_local_command(
            ["docker", "exec", "--user", "root", container, "sh", "-lc", f"rm -rf {target_dir!r} && mkdir -p {target_dir!r}"],
            timeout_s=30.0,
        )
        commands: list[dict[str, Any]] = [
            {
                "op": "prepare-target",
                "returncode": mkdir.returncode,
                "stdout": mkdir.stdout[-1200:],
                "stderr": mkdir.stderr[-1200:],
            }
        ]
        if mkdir.returncode != 0:
            result["commands"] = commands
            result["issues"].append("failed to prepare Coolify service build context directory: " + (mkdir.stderr or mkdir.stdout)[-1200:])
            return result

        copy = run_local_command(["docker", "cp", str(temp_context) + "/.", f"{container}:{target_dir}/"], timeout_s=120.0)
        commands.append(
            {
                "op": "copy-context",
                "returncode": copy.returncode,
                "stdout": copy.stdout[-1200:],
                "stderr": copy.stderr[-1200:],
            }
        )
        if copy.returncode != 0:
            result["commands"] = commands
            result["issues"].append("failed to copy Hub build context into Coolify service workspace: " + (copy.stderr or copy.stdout)[-1200:])
            return result

    verify_script = "\n".join(
        [
            "set -eu",
            f"cd {target_dir!r}",
            "test -f Dockerfile.hub.exp-fdb",
            "test -f pyproject.toml",
            "test -f exp-fdb-hub.py",
            "test -f run-exp-fdb-hub.py",
            "test -d main_computer",
            "sha256sum Dockerfile.hub.exp-fdb pyproject.toml exp-fdb-hub.py run-exp-fdb-hub.py",
        ]
    )
    verify = run_local_command(["docker", "exec", "--user", "root", container, "sh", "-lc", verify_script], timeout_s=30.0)
    commands.append(
        {
            "op": "verify-context",
            "returncode": verify.returncode,
            "stdout": verify.stdout[-2000:],
            "stderr": verify.stderr[-1200:],
        }
    )
    result["commands"] = commands
    if verify.returncode != 0:
        result["issues"].append("failed to verify staged Hub build context: " + (verify.stderr or verify.stdout)[-1200:])
        return result

    source_root = local_test_source_dir(args)
    expected = {
        "Dockerfile.hub.exp-fdb": sha256_file(source_root / "Dockerfile.hub.exp-fdb"),
        "pyproject.toml": sha256_file(source_root / "pyproject.toml"),
        "exp-fdb-hub.py": sha256_file(source_root / "exp-fdb-hub.py"),
        "run-exp-fdb-hub.py": sha256_file(source_root / "run-exp-fdb-hub.py"),
    }
    staged: dict[str, str] = {}
    for line in verify.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            staged[Path(parts[1].strip().lstrip("*")).name] = parts[0]
    mismatched = [name for name, digest in expected.items() if staged.get(name) != digest]
    result["expected_sha256"] = expected
    result["staged_sha256"] = staged
    if mismatched:
        result["issues"].append("staged Hub build context digest mismatch for: " + ", ".join(mismatched))
        return result

    result["ok"] = True
    result["message"] = f"staged Hub build context into {target_dir}"
    return result


def ensure_local_test_runtime_host_dir(args: argparse.Namespace) -> dict[str, Any]:
    host_dir = local_test_runtime_host_dir(args)
    host_dir.mkdir(parents=True, exist_ok=True)
    cluster_file = host_dir / "fdb.cluster"
    profile = coolify_deploy_profile(load_profile(args), args)
    deployments_dir = local_test_deployments_host_dir(args)
    local_manifest = deployments_dir / profile.network_key / "latest.json"
    return {
        "ok": True,
        "host_dir": str(host_dir),
        "bind_source": local_test_runtime_bind_source(args),
        "container_cluster_file": exp_fdb_cluster_file_path(
            profile,
            args,
            runtime_dir=str(getattr(args, "hub_runtime_dir", "") or DEFAULT_LOCAL_TEST_HUB_RUNTIME_DIR),
        ),
        "cluster_file_present": cluster_file.is_file(),
        "cluster_file": str(cluster_file),
        "deployments_host_dir": str(deployments_dir),
        "deployments_bind_source": local_test_deployments_bind_source(args),
        "dev_chain_deployment_path": dev_chain_deployment_path(profile, args),
        "dev_chain_deployment_file": str(local_manifest),
        "dev_chain_deployment_file_present": local_manifest.is_file(),
    }


def list_services(client: CoolifyClient) -> tuple[CoolifyResponse, list[dict[str, Any]]]:
    response = client.request("GET", "/api/v1/services")
    return response, body_items(response.body, "services")


def find_service(client: CoolifyClient, *, service_name: str, explicit_uuid: str, tried: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    clean_explicit = str(explicit_uuid or "").strip()
    if clean_explicit:
        return clean_explicit, {"source": "explicit_uuid", "uuid": clean_explicit}
    response, services = list_services(client)
    tried.append({"operation": "list-services", "response": response_to_dict(response), "count": len(services)})
    if not response.ok:
        return "", {"source": "api_error", "response": response_to_dict(response)}
    uuid, matches = select_by_exact_name(services, service_name)
    if uuid:
        return uuid, {"source": "name", "uuid": uuid, "matches": [item_summary(item) for item in matches]}
    if len(matches) > 1:
        raise CoolifyHubDeployError(
            f"Multiple Coolify services named {service_name!r} already exist; pass --coolify-application-uuid."
        )
    return "", {"source": "missing", "matches": []}


def create_local_test_service(client: CoolifyClient, profile: HubNetworkProfile, args: argparse.Namespace, *, service_name: str, runtime_dir: str, tried: list[dict[str, Any]]) -> str:
    payload = local_test_service_payload(profile, args, service_name=service_name, runtime_dir=runtime_dir)
    response = client.request("POST", "/api/v1/services", payload)
    tried.append(
        {
            "operation": "create-service",
            "path": "/api/v1/services",
            "payload_keys": sorted(payload),
            "docker_compose_raw_encoding": "base64",
            "response": response_to_dict(response),
        }
    )
    if not response.ok:
        raise CoolifyHubDeployError(f"Coolify service create failed with HTTP {response.status}: {response.body}")
    uuid = service_uuid_from_body(response.body)
    if not uuid:
        raise CoolifyHubDeployError(f"Coolify service create succeeded but no UUID was returned: {response.body}")
    return uuid


def update_local_test_service(client: CoolifyClient, profile: HubNetworkProfile, args: argparse.Namespace, *, service_uuid: str, service_name: str, runtime_dir: str, tried: list[dict[str, Any]]) -> None:
    compose = render_fdb_sidecar_hub_compose(profile, args, service_name=service_name, runtime_dir=runtime_dir)
    update_payloads = [
        {"docker_compose_raw": base64_text(compose), "name": service_name},
        {"docker_compose_raw": base64_text(compose)},
        {"docker_compose": compose, "name": service_name},
        {"compose": compose, "name": service_name},
    ]
    update_paths = [f"/api/v1/services/{urllib.parse.quote(service_uuid)}", f"/api/v1/services/{urllib.parse.quote(service_uuid)}/compose"]
    for path in update_paths:
        for payload in update_payloads:
            response = client.request("PATCH", path, payload)
            tried.append({"operation": "update-service", "method": "PATCH", "path": path, "payload_keys": sorted(payload), "response": response_to_dict(response)})
            if response.ok:
                return
            if response.status == 405:
                response = client.request("PUT", path, payload)
                tried.append({"operation": "update-service", "method": "PUT", "path": path, "payload_keys": sorted(payload), "response": response_to_dict(response)})
                if response.ok:
                    return
            if response.status not in {400, 404, 405, 422}:
                raise CoolifyHubDeployError(f"Coolify service update failed with HTTP {response.status}: {response.body}")
    raise CoolifyHubDeployError("Coolify service update failed on all known endpoints.")


def trigger_deploy_service(client: CoolifyClient, *, service_uuid: str, force: bool, tried: list[dict[str, Any]]) -> dict[str, Any]:
    query = urllib.parse.urlencode({"uuid": service_uuid, "force": "true" if force else "false"})
    paths = [
        f"/api/v1/deploy?{query}",
        f"/api/v1/services/{urllib.parse.quote(service_uuid)}/start",
        f"/api/v1/services/{urllib.parse.quote(service_uuid)}/restart",
        f"/api/v1/services/{urllib.parse.quote(service_uuid)}/deploy",
    ]
    for path in paths:
        method = "GET" if path.startswith("/api/v1/deploy?") else "POST"
        response = client.request(method, path)
        tried.append({"operation": "deploy-service", "method": method, "path": path, "response": response_to_dict(response)})
        if response.ok:
            return response_to_dict(response)
    raise CoolifyHubDeployError("Coolify service deploy failed on all known endpoints.")


def sync_local_test_service(client: CoolifyClient, profile: HubNetworkProfile, args: argparse.Namespace, *, service_name: str, runtime_dir: str, tried: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    service_uuid, existing = find_service(client, service_name=service_name, explicit_uuid=args.coolify_application_uuid, tried=tried)
    if service_uuid:
        update_local_test_service(client, profile, args, service_uuid=service_uuid, service_name=service_name, runtime_dir=runtime_dir, tried=tried)
        return service_uuid, "updated", existing
    service_uuid = create_local_test_service(client, profile, args, service_name=service_name, runtime_dir=runtime_dir, tried=tried)
    return service_uuid, "created", existing


def sync_fdb_sidecar_service(client: CoolifyClient, profile: HubNetworkProfile, args: argparse.Namespace, *, service_name: str, runtime_dir: str, tried: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    return sync_local_test_service(
        client,
        profile,
        args,
        service_name=service_name,
        runtime_dir=runtime_dir,
        tried=tried,
    )



def _redact_sensitive_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in {"value", "private_key", "bundle_b64", "secret"}:
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact_sensitive_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_payload(item) for item in value]
    return value


def redacted_response_to_dict(response: CoolifyResponse) -> dict[str, Any]:
    payload = response_to_dict(response)
    payload["body"] = _redact_sensitive_payload(payload.get("body"))
    return payload


def _env_item_key(item: dict[str, Any]) -> str:
    for key in ("key", "name", "variable"):
        text = str(item.get(key) or "").strip()
        if text:
            return text
    return ""


def _env_item_uuid(item: dict[str, Any]) -> str:
    return str(item.get("uuid") or item.get("id") or "").strip()


def list_service_envs(client: CoolifyClient, *, service_uuid: str, tried: list[dict[str, Any]]) -> tuple[CoolifyResponse, list[dict[str, Any]]]:
    path = f"/api/v1/services/{urllib.parse.quote(service_uuid)}/envs"
    response = client.request("GET", path)
    items = body_items(response.body, "envs", "environment_variables", "variables")
    tried.append({"operation": "list-service-envs", "path": path, "response": redacted_response_to_dict(response), "count": len(items)})
    return response, items


def sync_service_env_var(
    client: CoolifyClient,
    *,
    service_uuid: str,
    key: str,
    value: str,
    tried: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create or update a Coolify Service runtime env variable without leaking its value to diagnostics."""

    clean_key = str(key or "").strip()
    if not clean_key:
        raise CoolifyHubDeployError("bridge signer env key must not be empty.")
    list_response, env_items = list_service_envs(client, service_uuid=service_uuid, tried=tried)
    existing_uuid = ""
    for item in env_items:
        if _env_item_key(item) == clean_key:
            existing_uuid = _env_item_uuid(item)
            break

    base_payloads = [
        {
            "key": clean_key,
            "value": value,
            "is_build_time": False,
            "is_runtime": True,
            "is_literal": True,
            "is_multiline": False,
        },
        {
            "key": clean_key,
            "value": value,
            "is_build_time": False,
            "is_preview": False,
            "is_literal": True,
            "is_multiline": False,
        },
        {"key": clean_key, "value": value},
    ]
    value_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
    value_bytes = len(value.encode("utf-8"))

    def try_update_env(update_paths: list[str], *, reason: str) -> dict[str, Any] | None:
        seen_paths: set[str] = set()
        for path in update_paths:
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            for payload in base_payloads:
                response = client.request("PATCH", path, payload)
                tried.append(
                    {
                        "operation": "update-service-env",
                        "reason": reason,
                        "method": "PATCH",
                        "path": path,
                        "payload_keys": sorted(payload),
                        "response": redacted_response_to_dict(response),
                    }
                )
                if response.ok:
                    return {"ok": True, "action": "updated", "env_key": clean_key, "value_sha256": value_hash, "value_bytes": value_bytes}
                if response.status == 405:
                    response = client.request("PUT", path, payload)
                    tried.append(
                        {
                            "operation": "update-service-env",
                            "reason": reason,
                            "method": "PUT",
                            "path": path,
                            "payload_keys": sorted(payload),
                            "response": redacted_response_to_dict(response),
                        }
                    )
                    if response.ok:
                        return {"ok": True, "action": "updated", "env_key": clean_key, "value_sha256": value_hash, "value_bytes": value_bytes}
                if response.status not in {400, 404, 405, 422}:
                    raise CoolifyHubDeployError(f"Coolify service env update failed with HTTP {response.status}: {response.body}")
        return None

    create_path = f"/api/v1/services/{urllib.parse.quote(service_uuid)}/envs"
    key_update_path = f"{create_path}/{urllib.parse.quote(clean_key)}"
    if list_response.ok and existing_uuid:
        uuid_update_path = f"{create_path}/{urllib.parse.quote(existing_uuid)}"
        result = try_update_env([uuid_update_path, key_update_path, create_path], reason="listed-existing-env")
        if result is not None:
            return result

    saw_conflict = False
    for payload in base_payloads:
        response = client.request("POST", create_path, payload)
        tried.append(
            {
                "operation": "create-service-env",
                "method": "POST",
                "path": create_path,
                "payload_keys": sorted(payload),
                "response": redacted_response_to_dict(response),
            }
        )
        if response.ok:
            return {"ok": True, "action": "created", "env_key": clean_key, "value_sha256": value_hash, "value_bytes": value_bytes}
        if response.status == 409:
            saw_conflict = True
            result = try_update_env([key_update_path, create_path], reason="create-conflict")
            if result is not None:
                return result
            continue
        if response.status not in {400, 404, 405, 422}:
            raise CoolifyHubDeployError(f"Coolify service env create failed with HTTP {response.status}: {response.body}")

    if saw_conflict:
        raise CoolifyHubDeployError("Coolify service env exists, but update failed on all known endpoints.")
    raise CoolifyHubDeployError("Coolify service env sync failed on all known endpoints.")



def list_application_envs(client: CoolifyClient, *, application_uuid: str, tried: list[dict[str, Any]]) -> tuple[CoolifyResponse, list[dict[str, Any]]]:
    path = f"/api/v1/applications/{urllib.parse.quote(application_uuid)}/envs"
    response = client.request("GET", path)
    items = body_items(response.body, "envs", "environment_variables", "variables")
    tried.append({"operation": "list-application-envs", "path": path, "response": redacted_response_to_dict(response), "count": len(items)})
    return response, items


def sync_application_env_var(
    client: CoolifyClient,
    *,
    application_uuid: str,
    key: str,
    value: str,
    tried: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create or update a Coolify Application runtime env variable without leaking its value to diagnostics."""

    clean_key = str(key or "").strip()
    if not clean_key:
        raise CoolifyHubDeployError("bridge signer env key must not be empty.")
    list_response, env_items = list_application_envs(client, application_uuid=application_uuid, tried=tried)
    existing_uuid = ""
    for item in env_items:
        if _env_item_key(item) == clean_key:
            existing_uuid = _env_item_uuid(item)
            break

    base_payloads = [
        {"key": clean_key, "value": value},
        {
            "key": clean_key,
            "value": value,
            "is_build_time": False,
            "is_runtime": True,
            "is_literal": True,
            "is_multiline": False,
        },
        {
            "key": clean_key,
            "value": value,
            "is_build_time": False,
            "is_preview": False,
            "is_literal": True,
            "is_multiline": False,
        },
    ]
    value_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
    value_bytes = len(value.encode("utf-8"))
    create_path = f"/api/v1/applications/{urllib.parse.quote(application_uuid)}/envs"
    key_update_path = f"{create_path}/{urllib.parse.quote(clean_key)}"

    def try_update_env(update_paths: list[str], *, reason: str) -> dict[str, Any] | None:
        seen_paths: set[str] = set()
        for path in update_paths:
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            for payload in base_payloads:
                response = client.request("PATCH", path, payload)
                tried.append(
                    {
                        "operation": "update-application-env",
                        "reason": reason,
                        "method": "PATCH",
                        "path": path,
                        "payload_keys": sorted(payload),
                        "response": redacted_response_to_dict(response),
                    }
                )
                if response.ok:
                    return {"ok": True, "action": "updated", "env_key": clean_key, "value_sha256": value_hash, "value_bytes": value_bytes}
                if response.status == 405:
                    response = client.request("PUT", path, payload)
                    tried.append(
                        {
                            "operation": "update-application-env",
                            "reason": reason,
                            "method": "PUT",
                            "path": path,
                            "payload_keys": sorted(payload),
                            "response": redacted_response_to_dict(response),
                        }
                    )
                    if response.ok:
                        return {"ok": True, "action": "updated", "env_key": clean_key, "value_sha256": value_hash, "value_bytes": value_bytes}
                if response.status not in {400, 404, 405, 409, 422}:
                    raise CoolifyHubDeployError(f"Coolify application env update failed with HTTP {response.status}: {response.body}")
        return None

    if list_response.ok and existing_uuid:
        uuid_update_path = f"{create_path}/{urllib.parse.quote(existing_uuid)}"
        result = try_update_env([uuid_update_path, key_update_path, create_path], reason="listed-existing-env")
        if result is not None:
            return result

    saw_conflict = False
    for payload in base_payloads:
        response = client.request("POST", create_path, payload)
        tried.append(
            {
                "operation": "create-application-env",
                "method": "POST",
                "path": create_path,
                "payload_keys": sorted(payload),
                "response": redacted_response_to_dict(response),
            }
        )
        if response.ok:
            return {"ok": True, "action": "created", "env_key": clean_key, "value_sha256": value_hash, "value_bytes": value_bytes}
        if response.status == 409:
            saw_conflict = True
            result = try_update_env([key_update_path, create_path], reason="create-conflict")
            if result is not None:
                return result
            continue
        if response.status not in {400, 404, 405, 422}:
            raise CoolifyHubDeployError(f"Coolify application env create failed with HTTP {response.status}: {response.body}")

    if saw_conflict:
        raise CoolifyHubDeployError("Coolify application env exists, but update failed on all known endpoints.")
    raise CoolifyHubDeployError("Coolify application env sync failed on all known endpoints.")


def sync_hub_runtime_application_envs(
    client: CoolifyClient,
    profile: HubNetworkProfile,
    args: argparse.Namespace,
    *,
    application_uuid: str,
    runtime_dir: str,
    tried: list[dict[str, Any]],
) -> dict[str, Any]:
    """Sync launcher/runtime environment so Hub Apps work even when Coolify ignores start_command."""

    values = hub_runtime_env_defaults(profile, args, runtime_dir=runtime_dir)
    results: list[dict[str, Any]] = []
    for key in sorted(values):
        sync = sync_application_env_var(
            client,
            application_uuid=application_uuid,
            key=key,
            value=values[key],
            tried=tried,
        )
        results.append(
            {
                "key": key,
                "action": sync["action"],
                "value_sha256": sync["value_sha256"],
                "value_bytes": sync["value_bytes"],
            }
        )
    return {
        "ok": True,
        "count": len(results),
        "keys": [item["key"] for item in results],
        "envs": results,
    }


def sync_bridge_signer_application_env(
    client: CoolifyClient,
    profile: HubNetworkProfile,
    args: argparse.Namespace,
    *,
    application_uuid: str,
    tried: list[dict[str, Any]],
) -> dict[str, Any]:
    bundle = build_bridge_signer_bundle(profile, args)
    sync = sync_application_env_var(
        client,
        application_uuid=application_uuid,
        key=bundle["env_key"],
        value=bundle["bundle_b64"],
        tried=tried,
    )
    return {
        "ok": True,
        "env_key": bundle["env_key"],
        "action": sync["action"],
        "bundle_sha256": bundle["bundle_sha256"],
        "bundle_bytes": bundle["bundle_bytes"],
        "source_manifest": bundle["source_manifest"],
        "wallet_path": bundle["wallet_path"],
        "escrow_address": bundle["escrow_address"],
        "bridge_controller_address": bundle["bridge_controller_address"],
        "coolify_env_value_sha256": sync["value_sha256"],
        "coolify_env_value_bytes": sync["value_bytes"],
    }


def sync_bridge_signer_env(
    client: CoolifyClient,
    profile: HubNetworkProfile,
    args: argparse.Namespace,
    *,
    service_uuid: str,
    tried: list[dict[str, Any]],
) -> dict[str, Any]:
    bundle = build_bridge_signer_bundle(profile, args)
    sync = sync_service_env_var(
        client,
        service_uuid=service_uuid,
        key=bundle["env_key"],
        value=bundle["bundle_b64"],
        tried=tried,
    )
    return {
        "ok": True,
        "env_key": bundle["env_key"],
        "action": sync["action"],
        "bundle_sha256": bundle["bundle_sha256"],
        "bundle_bytes": bundle["bundle_bytes"],
        "source_manifest": bundle["source_manifest"],
        "wallet_path": bundle["wallet_path"],
        "escrow_address": bundle["escrow_address"],
        "bridge_controller_address": bundle["bridge_controller_address"],
        "coolify_env_value_sha256": sync["value_sha256"],
        "coolify_env_value_bytes": sync["value_bytes"],
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
    name = hub_volume_name(profile.network_key, implementation=hub_implementation(args))
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
    payload = storage_payload(profile, runtime_dir=runtime_dir, args=args)
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


def hub_status_request(status_url: str, *, user_agent: str = DEFAULT_JSON_RPC_USER_AGENT) -> urllib.request.Request:
    headers = {"Accept": "application/json"}
    clean_user_agent = str(user_agent or "").strip()
    if clean_user_agent:
        headers["User-Agent"] = clean_user_agent
    return urllib.request.Request(status_url, headers=headers, method="GET")


def wait_for_hub(profile: HubNetworkProfile, args: argparse.Namespace) -> dict[str, Any]:
    if args.hub_wait_timeout_s <= 0:
        return {"ok": True, "skipped": True, "reason": "hub_wait_timeout_s <= 0"}
    status_url = hub_public_status_url(profile, args)
    deadline = time.monotonic() + args.hub_wait_timeout_s
    last_error: object = None
    user_agent = str(getattr(args, "hub_status_user_agent", DEFAULT_JSON_RPC_USER_AGENT) or "").strip()
    while time.monotonic() < deadline:
        try:
            request = hub_status_request(status_url, user_agent=user_agent)
            with urllib.request.urlopen(request, timeout=args.hub_status_timeout_s) as response:
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


def load_profile(args: argparse.Namespace, *, validate_deploy_args: bool = True) -> HubNetworkProfile:
    registry = load_hub_network_registry(args.network_config)
    raw_profile = registry.get(args.network)
    profile = coolify_deploy_profile(raw_profile, args)
    verify_chain_rpc_url = str(getattr(args, "verify_chain_rpc_url", "") or "").strip()
    if verify_chain_rpc_url:
        profile = replace(profile, chain_rpc_url=verify_chain_rpc_url)
    validate_coolify_profile(profile)
    if validate_deploy_args:
        validate_hub_deploy_args(profile, args)
    return profile


def plan_result(profile: HubNetworkProfile, args: argparse.Namespace) -> dict[str, Any]:
    apply_bridge_signer_defaults(profile, args)
    implementation = hub_implementation(args)
    runtime_dir = args.hub_runtime_dir or hub_state_mount_path(profile.network_key, implementation=implementation)
    service_name = args.coolify_application_name or hub_service_name(
        profile.network_key,
        implementation=implementation,
        replace_regular_hub=bool(getattr(args, "replace_regular_hub", False)),
    )
    result: dict[str, Any] = {
        "network": profile.network_key,
        "hub_implementation": implementation,
        "service_name": service_name,
        "runtime_dir": runtime_dir,
        "runtime_env_file": hub_runtime_env_file_path(profile, args, runtime_dir=runtime_dir),
        "volume_name": hub_volume_name(profile.network_key, implementation=implementation),
        "public_url": profile.hub_url,
        "chain_rpc_url": profile.chain_rpc_url,
        "hub_chain_rpc_url": hub_chain_rpc_url(profile, args),
        "chain_id": profile.chain_id,
        "bridge_backend": hub_bridge_backend(args),
        "dev_chain_deployment_path": (
            bridge_signer_remote_path(profile, args, runtime_dir=runtime_dir)
            if hub_enable_bridge_writes(args)
            else dev_chain_deployment_path(profile, args)
        ),
        "contracts_path": hub_contracts_path_for_runtime(profile, args, runtime_dir=runtime_dir),
        "contracts_source_path": contracts_path(profile, args, container_path=False),
        "bridge_signer": bridge_signer_plan(profile, args, runtime_dir=runtime_dir),
        "application_payload": application_payload(profile, args, service_name=service_name, runtime_dir=runtime_dir),
        "hub_start_command": hub_start_command(profile, runtime_dir, args),
        "storage_payload": storage_payload(profile, runtime_dir=runtime_dir, args=args),
    }
    if implementation == HUB_IMPLEMENTATION_EXP_FDB:
        result["fdb_cluster_file"] = exp_fdb_cluster_file_path(profile, args, runtime_dir=runtime_dir)
        result["fdb_namespace"] = exp_fdb_namespace(profile, args)
        result["replace_regular_hub"] = bool(getattr(args, "replace_regular_hub", False))
        result["operator_note"] = (
            "Experimental FDB Hub deploys with --no-fdb-autostart; mount a valid FoundationDB cluster file "
            f"at {result['fdb_cluster_file']!r} before applying or starting the application."
        )
        if uses_fdb_sidecar_service(profile, args):
            result["operator_note"] = (
                f"{profile.network_key} exp-FDB deploy starts a FoundationDB sidecar in the same Coolify service, "
                f"writes {result['fdb_cluster_file']!r} at container startup, configures single-memory FDB if needed, "
                "then starts the Hub. No manual fdb.cluster seed file is required."
            )
            result["sidecar_fdb"] = {
                "service": fdb_sidecar_service_key(profile, service_name=service_name),
                "image": DEFAULT_LOCAL_TEST_FDB_IMAGE,
                "port": DEFAULT_LOCAL_TEST_FDB_PORT,
                "cluster_contents": fdb_sidecar_cluster_contents(profile, service_name=service_name),
                "cluster_file_written_by": "Hub container bootstrap command",
            }
            compose = render_fdb_sidecar_hub_compose(profile, args, service_name=service_name, runtime_dir=runtime_dir)
            service_payload = fdb_sidecar_service_payload(profile, args, service_name=service_name, runtime_dir=runtime_dir)
            result["coolify_resource_kind"] = "service"
            result["service_payload"] = {
                **{key: value for key, value in service_payload.items() if key != "docker_compose_raw"},
                "docker_compose_raw": "<base64>",
                "docker_compose_raw_bytes": len(service_payload.get("docker_compose_raw", "")),
            }
            result["docker_compose"] = compose
            if is_local_test_profile(profile):
                result["local_fdb"] = result["sidecar_fdb"]
                source_root, source_files, source_dirs = hub_build_context_sources(args)
                result["local_build_context"] = {
                    "compose_context": docker_compose_build_context(args),
                    "staged_service_path": f"/data/coolify/services/<service-uuid>/{DEFAULT_LOCAL_TEST_BUILD_CONTEXT_DIRNAME}",
                    "source_root": str(source_root),
                    "source_files": [relative_to_source(path, source_root) for path in source_files if path.exists()],
                    "source_dirs": [relative_to_source(path, source_root) + "/" for path in source_dirs if path.exists()],
                    "commit_required": False,
                    "note": "Local test deploy stages this source tree into the Coolify service workspace before deploy.",
                }
                result["local_runtime_bind"] = {
                    "host_dir": str(local_test_runtime_host_dir(args)),
                    "bind_source": local_test_runtime_bind_source(args),
                    "container_dir": runtime_dir,
                }
                result["local_deployments_bind"] = {
                    "host_dir": str(local_test_deployments_host_dir(args)),
                    "bind_source": local_test_deployments_bind_source(args),
                    "container_dir": DEFAULT_LOCAL_TEST_DEPLOYMENTS_CONTAINER_DIR,
                    "deployment_path": dev_chain_deployment_path(profile, args),
                }
            else:
                result["remote_build_context"] = {
                    "compose_context": remote_git_build_context(args),
                    "commit_required": True,
                    "note": "Remote testnet service builds from the configured Git repository/branch.",
                }
                result["remote_runtime_bind"] = {
                    "host_dir": remote_runtime_bind_source(runtime_dir),
                    "container_dir": runtime_dir,
                    "cluster_file": result["fdb_cluster_file"],
                }
    return result


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


def verify_result(profile: HubNetworkProfile, args: argparse.Namespace) -> dict[str, Any]:
    """Verify the Hub-facing view of a chain without mutating Coolify.

    This is intentionally narrower than ``apply``: it checks the chain RPC URL
    the Hub profile will use and, unless skipped, the public Hub status endpoint.
    It does not create/update Coolify resources, sync bridge signer material, or
    trigger deploys.
    """

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

    hub_mode = hub_health_check_mode(profile, args)
    if hub_mode != "skip":
        try:
            hub = wait_for_hub(profile, args)
            phases.append({"phase": "wait-hub", "mode": hub_mode, "result": hub})
        except CoolifyHubDeployError as exc:
            warning = warning_payload("wait-hub", hub_mode, exc)
            phases.append({"phase": "wait-hub", "mode": hub_mode, "result": warning})
            if hub_mode == "require":
                raise
            warnings.append(warning)
    else:
        phases.append({"phase": "wait-hub", "mode": hub_mode, "result": {"ok": True, "skipped": True}})

    return {
        "ok": True,
        "action": "verify",
        "network": profile.network_key,
        "chain_rpc_url": profile.chain_rpc_url,
        "hub_url": profile.hub_url,
        "chain_id": profile.chain_id,
        "warnings": warnings,
        "phases": phases,
    }


def verify(args: argparse.Namespace) -> dict[str, Any]:
    profile = load_profile(args, validate_deploy_args=False)
    return verify_result(profile, args)


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

    application_uuid = ""
    application_action = ""
    deploy_result: dict[str, Any] | None = None

    if uses_fdb_sidecar_service(profile, args):
        service_uuid, service_action, existing = sync_fdb_sidecar_service(
            client,
            profile,
            args,
            service_name=plan["service_name"],
            runtime_dir=plan["runtime_dir"],
            tried=tried,
        )
        application_uuid = service_uuid
        application_action = service_action
        result_payload: dict[str, Any] = {
            "ok": True,
            "service_uuid": service_uuid,
            "service_action": service_action,
            "existing": existing,
            "sidecar_fdb": plan.get("sidecar_fdb", {}),
            "tried": tried,
        }
        if is_remote_fdb_sidecar_profile(profile, args):
            _, legacy_application = find_application(client, service_name=plan["service_name"], explicit_uuid="", tried=tried)
            if legacy_application.get("source") != "missing":
                legacy_warning = {
                    "phase": "legacy-application",
                    "mode": "warn",
                    "ok": False,
                    "message": (
                        "An existing Coolify Application with this Hub name was found, but "
                        "remote exp-FDB now deploys as a Coolify Service with an FDB sidecar. "
                        "Stop/delete the old Application after the Service is healthy."
                    ),
                    "legacy_application": legacy_application,
                }
                warnings.append(legacy_warning)
                result_payload["legacy_application_warning"] = legacy_warning
        phases.append({"phase": "coolify-service", "result": result_payload})
        if bridge_signer_sync_requested(args):
            if not is_remote_fdb_sidecar_profile(profile, args):
                signer_sync_result = {
                    "ok": True,
                    "skipped": True,
                    "reason": "bridge signer sync is only required for remote FDB sidecar services",
                }
            else:
                signer_sync_result = sync_bridge_signer_env(
                    client,
                    profile,
                    args,
                    service_uuid=service_uuid,
                    tried=tried,
                )
            phases.append({"phase": "sync-bridge-signer", "result": signer_sync_result})
        if is_local_test_profile(profile):
            runtime_host = ensure_local_test_runtime_host_dir(args)
            phases.append({"phase": "local-runtime-host-dir", "result": runtime_host})
            staging = stage_local_test_hub_build_context(args, service_uuid=service_uuid)
            phases.append({"phase": "stage-local-build-context", "result": staging})
            if not staging.get("ok"):
                issues = "; ".join(str(item) for item in staging.get("issues", []) if str(item).strip())
                raise CoolifyHubDeployError("Could not stage local Hub build context before deploy: " + (issues or "unknown staging failure"))
        if not args.no_deploy:
            deploy_result = trigger_deploy_service(client, service_uuid=service_uuid, force=args.force_deploy, tried=tried)
            phases.append({"phase": "deploy", "result": deploy_result})
    else:
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
        "coolify_resource_kind": "service" if uses_fdb_sidecar_service(profile, args) else "application",
        "deployed": deploy_result is not None,
        "plan": plan,
        "warnings": warnings,
        "phases": phases,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy Main Computer Hub applications into Coolify.")
    parser.add_argument("action", choices=["plan", "apply", "verify"], help="Use plan for local payload rendering, apply for Coolify create/update, or verify for read-only Hub checks.")
    parser.add_argument("network", choices=["test", "testnet", "mainnet"], help="Hub network to deploy. `test` targets the local Coolify/Besu-QBFT surface.")

    parser.add_argument("--network-config", type=Path, default=None, help="Path to hub_networks.json.")
    parser.add_argument("--hub-runtime-dir", default="", help="Container path for persistent Hub runtime state.")
    parser.add_argument(
        "--runtime-env-file",
        default="",
        help=(
            "Container-visible strict KEY=VALUE Hub runtime env file. For local `test`, "
            "defaults to <hub-runtime-dir>/hub-runtime.env and is bind-mounted from the local runtime host dir."
        ),
    )
    parser.add_argument(
        "--hub-implementation",
        choices=HUB_IMPLEMENTATION_CHOICES,
        default=HUB_IMPLEMENTATION_EXP_FDB,
        help="Hub implementation to deploy. exp-fdb is the only supported hosted Hub implementation.",
    )
    parser.add_argument(
        "--replace-regular-hub",
        action="store_true",
        help=(
            "Deprecated no-op. The exp-FDB Hub now always uses the normal "
            "main-computer-<network>-hub Coolify application name."
        ),
    )
    parser.add_argument(
        "--fdb-cluster-file",
        default="",
        help=(
            "Container path to the FoundationDB cluster file for --hub-implementation exp-fdb. "
            "Defaults to <hub-runtime-dir>/fdb.cluster."
        ),
    )
    parser.add_argument(
        "--fdb-namespace",
        default="",
        help="FoundationDB tuple namespace for --hub-implementation exp-fdb. Defaults to main-computer-<network>-exp-fdb.",
    )

    parser.add_argument("--coolify-url", default="", help="Coolify base URL.")
    parser.add_argument("--coolify-token", default="", help="Coolify bearer token. Prefer --coolify-token-env.")
    parser.add_argument("--coolify-token-env", default=DEFAULT_TOKEN_ENV, help="Environment variable containing the Coolify token.")
    parser.add_argument("--coolify-token-file", default="", help="File containing the Coolify token.")
    parser.add_argument(
        "--local-coolify-token-file",
        default="",
        help=(
            "Local Coolify token file for `apply test`. Defaults to MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE, "
            "then <local-coolify-state-dir>/api-token.txt."
        ),
    )
    parser.add_argument(
        "--local-coolify-state-dir",
        default="",
        help=(
            "Existing local Coolify state dir for `apply test`. Defaults to MAIN_COMPUTER_COOLIFY_STATE_DIR, "
            "then COOLIFY_LOCAL_STATE from runtime/applications_service/applications.env, then runtime/coolify-local-docker."
        ),
    )
    parser.add_argument(
        "--local-source-dir",
        default="",
        help=(
            "Local source tree to stage for `apply test`. Defaults to MAIN_COMPUTER_HUB_TEST_SOURCE_DIR, "
            "then the current repository root. Uncommitted files in this tree are included."
        ),
    )
    parser.add_argument(
        "--local-hub-runtime-host-dir",
        default="",
        help=(
            "Host directory bind-mounted into the local test Hub runtime. Defaults to "
            "MAIN_COMPUTER_HUB_TEST_RUNTIME_HOST_DIR, then runtime/hub/test-exp-fdb."
        ),
    )
    parser.add_argument(
        "--applications-service-env-file",
        default="",
        help="Applications service env file used to discover the Website Builder/local Coolify target for `apply test`.",
    )
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
        "--no-create-environment",
        action="store_true",
        help="Fail if the named Coolify environment is missing instead of creating it.",
    )
    parser.add_argument(
        "--coolify-server-uuid",
        default="",
        help="Coolify server UUID. If omitted with --coolify-server-name, apply infers it only when exactly one server exists.",
    )
    parser.add_argument("--coolify-server-name", default="", help="Coolify server name to resolve exactly.")
    parser.add_argument("--coolify-destination-uuid", default="", help="Destination UUID if the server has multiple Docker destinations.")

    parser.add_argument("--git-repo", default="", help="Git repository URL for remote testnet/mainnet application deploys. Not required for local `test`, which stages the local working tree.")
    parser.add_argument("--git-branch", default="main", help="Git branch to deploy.")
    parser.add_argument("--git-commit-sha", default="", help="Optional exact commit SHA.")
    parser.add_argument("--github-app-uuid", default="", help="Use private GitHub App create endpoint.")
    parser.add_argument("--deploy-key-uuid", default="", help="Use private deploy-key create endpoint.")
    parser.add_argument("--base-directory", default=DEFAULT_BASE_DIRECTORY)
    parser.add_argument("--dockerfile-location", default="", help="Dockerfile path. Defaults to /Dockerfile.hub.exp-fdb.")
    parser.add_argument("--health-path", default=DEFAULT_HEALTH_PATH)
    parser.add_argument(
        "--hub-chain-rpc-url",
        default="",
        help=(
            "Override the chain RPC URL passed to the Hub container. "
            "For local `test`, defaults to http://host.docker.internal:30010 while the operator RPC check still uses the test profile URL."
        ),
    )
    parser.add_argument(
        "--verify-chain-rpc-url",
        default="",
        help=(
            "Override the chain RPC URL used by the read-only verify action. "
            "This lets chain deployment discovery verify the Hub against the currently observed RPC surface."
        ),
    )
    parser.add_argument(
        "--bridge-backend",
        choices=["dev-chain", "credit-bridge-contract", "mock-chain"],
        default="",
        help="Hub bridge backend. Defaults to dev-chain/contract mode; use mock-chain only for explicit lab/fake-chain runs.",
    )
    parser.add_argument(
        "--dev-chain-deployment-path",
        default="",
        help=(
            "Container path to the private deployment metadata used by dev-chain signing mode. "
            "Defaults to /app/runtime/deployments/<network>/latest.json; local `test` bind-mounts runtime/deployments there."
        ),
    )
    parser.add_argument(
        "--contracts-path",
        default="",
        help=(
            "Container path to public contract-address config. Defaults to "
            "/app/main_computer/config/<network>_contracts.json."
        ),
    )
    parser.add_argument(
        "--remote-contracts-path",
        default="",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--allow-missing-bridge-signer",
        action="store_true",
        help=(
            "Allow public-contract Hub startup without a private dev-chain signer manifest. "
            "Bridge write operations remain disabled until signer metadata is configured."
        ),
    )
    parser.add_argument(
        "--enable-smoke-bridge",
        action="store_true",
        help=(
            "Enable explicit admin-only smoke bridge mode. This may load smoke_client wallet metadata "
            "from a private deployment manifest and must not be used for normal testnet/mainnet traffic."
        ),
    )
    parser.add_argument(
        "--enable-bridge-writes",
        action="store_true",
        help=(
            "Enable the non-smoke bridge signer path. The deployer syncs the bridge controller signer "
            "through Coolify service environment variables and the Hub fails closed if the signer is absent."
        ),
    )
    parser.add_argument(
        "--no-bridge-writes",
        action="store_true",
        help="Do not infer or sync bridge signer material even when a local hub_admin manifest exists.",
    )
    parser.add_argument(
        "--sync-bridge-signer",
        action="store_true",
        help=(
            "Sync bridge controller signer material to the Coolify service env store without enabling smoke mode. "
            "This is implied by --enable-bridge-writes."
        ),
    )
    parser.add_argument(
        "--bridge-signer-source-manifest",
        default="",
        help="Local private deployment manifest used to build the bridge signer bundle. Defaults to runtime/deployments/<network>/latest.json.",
    )
    parser.add_argument(
        "--bridge-controller-wallet-path",
        default="",
        help="Local bridge controller wallet JSON override. Defaults to hub_admin.wallet_path in the source manifest.",
    )
    parser.add_argument(
        "--bridge-signer-env-key",
        default="",
        help=f"Coolify Service env variable name used for the base64 signer bundle. Defaults to {DEFAULT_BRIDGE_SIGNER_ENV_KEY}.",
    )
    parser.add_argument(
        "--bridge-signer-remote-path",
        default="",
        help=(
            "Container path where the Hub bootstrap decodes the signer bundle. "
            f"Defaults to <hub-runtime-dir>/{DEFAULT_BRIDGE_SIGNER_RELATIVE_PATH}."
        ),
    )

    parser.add_argument("--rpc-timeout-s", type=float, default=8.0)
    parser.add_argument(
        "--rpc-user-agent",
        default=os.environ.get("MAIN_COMPUTER_RPC_USER_AGENT", DEFAULT_JSON_RPC_USER_AGENT),
        help=(
            "User-Agent used for JSON-RPC preflight requests. "
            "Some HTTPS RPC edges reject Python urllib's default identity."
        ),
    )
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
    parser.add_argument(
        "--hub-status-user-agent",
        default=os.environ.get("MAIN_COMPUTER_HUB_STATUS_USER_AGENT", DEFAULT_JSON_RPC_USER_AGENT),
        help=(
            "User-Agent used for public Hub status checks. "
            "Some HTTPS edges reject Python urllib's default identity."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Render the plan without network or Coolify calls.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.action == "plan":
            profile = load_profile(args)
            result = {"ok": True, "plan": plan_result(profile, args)}
        elif args.action == "verify":
            result = verify(args)
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
