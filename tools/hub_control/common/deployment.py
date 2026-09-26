from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.parse
import urllib.request
from dataclasses import replace
from typing import Any, Mapping

from main_computer.hub_networks import load_hub_network_registry
from tools import coolify_hub_service as legacy

from .canonical import canonical_bytes
from .errors import HubControlError
from .models import DependencyContract, HubContext, HubPlacement
from .privates import controller_coordinates

FDB_CLUSTER_ENV = "MAIN_COMPUTER_HUB_CONTROL_FDB_CLUSTER_CONTENTS"
TOPOLOGY_B64_ENV = "MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_B64"
FDB_CONTRACT_ENV = "MAIN_COMPUTER_HUB_CONTROL_FDB_CONTRACT_SHA256"
CHAIN_CONTRACT_ENV = "MAIN_COMPUTER_HUB_CONTROL_CHAIN_CONTRACT_SHA256"
BOOTSTRAP_COMMAND = "python /app/run-exp-fdb-hub.py"


def render_runtime_topology(
    ctx: HubContext,
    *,
    network: str,
    placement: HubPlacement,
    accepted_hubs: list[dict[str, Any]],
    fdb_contract: DependencyContract,
    chain_contract: DependencyContract,
) -> dict[str, Any]:
    profile = load_hub_network_registry(ctx.repo_root / "main_computer" / "config" / "hub_networks.json").get(network)
    hubs: list[dict[str, Any]] = []
    for item in accepted_hubs:
        if not isinstance(item, Mapping):
            continue
        hub_id = str(item.get("hub_id") or "").strip()
        public_url = str(item.get("public_url") or item.get("hub_url") or "").strip().rstrip("/")
        if hub_id and public_url:
            hubs.append({"hub_id": hub_id, "hub_url": public_url, "public_url": public_url, "roles": ["entry", "execution"]})
    hubs.append(
        {
            "hub_id": placement.hub_id,
            "hub_url": placement.public_url,
            "public_url": placement.public_url,
            "roles": ["entry", "execution"],
        }
    )
    hubs.sort(key=lambda item: str(item["hub_id"]).encode("utf-8"))
    return {
        "kind": "main_computer.stable_hub_topology.v1",
        "cluster_id": f"main-computer-{network}-hubs",
        "network": {
            "network_key": network,
            "display_name": profile.display_name,
            "kind": profile.kind,
            "chain_id": str(chain_contract.payload["chain_id"]),
            "chain_rpc_url": str(chain_contract.payload["rpc_url"]),
        },
        "storage": {
            "backend": "foundationdb",
            "cluster_file": placement.cluster_file_path,
            "namespace": str(fdb_contract.payload["namespace"]),
            "api_version": int(fdb_contract.payload.get("api_version", 740)),
        },
        "entry_urls": [placement.public_url],
        "hubs": hubs,
    }


def deployment_target(
    ctx: HubContext,
    private: Mapping[str, Any],
    *,
    network: str,
    placement: HubPlacement,
    fdb_contract: DependencyContract,
    chain_contract: DependencyContract,
    accepted_hubs: list[dict[str, Any]],
) -> dict[str, Any]:
    coords = controller_coordinates(private, network, placement.controller_id, base_dir=ctx.mother_private_path.parent)
    topology = render_runtime_topology(
        ctx,
        network=network,
        placement=placement,
        accepted_hubs=accepted_hubs,
        fdb_contract=fdb_contract,
        chain_contract=chain_contract,
    )
    profile = load_hub_network_registry(ctx.repo_root / "main_computer" / "config" / "hub_networks.json").get(network)
    return {
        "controller_id": placement.controller_id,
        "host_id": placement.host_id,
        "coolify": coords,
        "application_name": placement.application_name,
        "legacy_application_name": f"main-computer-{network}-hub",
        "public_url": placement.public_url,
        "runtime_dir": placement.runtime_dir,
        "cluster_file_path": placement.cluster_file_path,
        "topology_path": placement.topology_path,
        "hub_bind_port": int(profile.hub_bind_port),
        "network_display_name": profile.display_name,
        "network_kind": profile.kind,
        "topology": topology,
        "fdb_contract": fdb_contract.payload,
        "chain_contract": chain_contract.payload,
        "git_repository": "https://github.com/johnrraymond/main_computer",
        "git_branch": "main",
        "dockerfile_location": "/Dockerfile.hub.exp-fdb",
    }


def _client(target: Mapping[str, Any], client_factory=legacy.CoolifyClient):
    coolify = target["coolify"]
    return client_factory(str(coolify["url"]), str(coolify["api_token"]))


def inspect_deployment(target: Mapping[str, Any], *, client_factory=legacy.CoolifyClient) -> dict[str, Any]:
    client = _client(target, client_factory)
    tried: list[dict[str, Any]] = []
    try:
        uuid, detail = legacy.find_application(
            client,
            service_name=str(target["application_name"]),
            explicit_uuid="",
            tried=tried,
        )
        if uuid:
            return {"application_uuid": uuid, "present": True, "resolution": {"source": "hub-control-name", **detail}}
        legacy_name = str(target.get("legacy_application_name") or "").strip()
        if legacy_name and legacy_name != str(target["application_name"]):
            legacy_uuid, legacy_detail = legacy.find_application(
                client,
                service_name=legacy_name,
                explicit_uuid="",
                tried=tried,
            )
            if legacy_uuid:
                return {
                    "application_uuid": legacy_uuid,
                    "present": True,
                    "migration_candidate": True,
                    "resolution": {"source": "legacy-hub-application", "legacy_name": legacy_name, **legacy_detail},
                }
    except Exception as exc:
        raise HubControlError("HUB_COOLIFY_INSPECT_FAILED", str(exc)) from exc
    return {"application_uuid": None, "present": False, "resolution": {"source": "missing"}}


def _domain(public_url: str, port: int) -> str:
    try:
        parsed = urllib.parse.urlsplit(public_url)
    except ValueError:
        return public_url
    if not parsed.hostname or parsed.port is not None:
        return public_url
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return urllib.parse.urlunsplit((parsed.scheme, f"{host}:{port}", parsed.path, parsed.query, parsed.fragment))


def _application_payload(target: Mapping[str, Any]) -> dict[str, Any]:
    coolify = target["coolify"]
    return {
        "name": str(target["application_name"]),
        "description": f"Main Computer {target['application_name']} Hub Control deployment",
        "project_uuid": str(coolify["project_uuid"]),
        "server_uuid": str(coolify["server_uuid"]),
        "environment_name": str(target.get("network") or "mainnet"),
        "git_repository": str(target["git_repository"]),
        "git_branch": str(target["git_branch"]),
        "build_pack": "dockerfile",
        "base_directory": "/",
        "dockerfile_location": str(target["dockerfile_location"]),
        "ports_exposes": str(target["hub_bind_port"]),
        "domains": _domain(str(target["public_url"]), int(target["hub_bind_port"])),
        "start_command": BOOTSTRAP_COMMAND,
        "health_check_enabled": True,
        "health_check_path": "/api/hub/v1/health",
        "instant_deploy": False,
    }


def _ensure_storage(client: Any, target: Mapping[str, Any], application_uuid: str, tried: list[dict[str, Any]]) -> None:
    path = f"/api/v1/applications/{urllib.parse.quote(application_uuid)}/storages"
    response = client.request("GET", path)
    items = legacy.body_items(response.body, "storages", "persistent_storages") if response.ok else []
    mount = str(target["runtime_dir"])
    name = f"{str(target['application_name']).replace('_', '-')}-data"
    for item in items:
        if str(item.get("mount_path") or item.get("mountPath") or "") == mount:
            return
    payload = {"type": "persistent", "name": name, "mount_path": mount, "host_path": mount}
    response = client.request("POST", path, payload)
    tried.append({"operation": "create-storage", "status": response.status})
    if not response.ok:
        raise HubControlError("HUB_COOLIFY_STORAGE_FAILED", f"persistent Hub storage create failed: HTTP {response.status}: {response.body}")


def apply_deployment(target: Mapping[str, Any], *, client_factory=legacy.CoolifyClient) -> dict[str, Any]:
    target = dict(target)
    target.setdefault("network", str(target.get("chain_contract", {}).get("network") or "mainnet"))
    client = _client(target, client_factory)
    tried: list[dict[str, Any]] = []
    try:
        frozen_uuid = str(target.get("application_uuid") or "").strip()
        if frozen_uuid:
            app_uuid, _detail = frozen_uuid, {"source": "frozen-prep", "uuid": frozen_uuid}
        else:
            app_uuid, _detail = legacy.find_application(
                client,
                service_name=str(target["application_name"]),
                explicit_uuid="",
                tried=tried,
            )
        payload = _application_payload(target)
        action = "migrated" if frozen_uuid and target.get("migration_candidate") else "updated"
        if not app_uuid:
            response = client.request("POST", "/api/v1/applications/public", payload)
            if not response.ok:
                raise HubControlError("HUB_COOLIFY_CREATE_FAILED", f"Hub application create failed: HTTP {response.status}: {response.body}")
            app_uuid = legacy.item_uuid(response.body) if isinstance(response.body, dict) else ""
            if not app_uuid and isinstance(response.body, dict) and isinstance(response.body.get("application"), dict):
                app_uuid = legacy.item_uuid(response.body["application"])
            if not app_uuid:
                raise HubControlError("HUB_COOLIFY_CREATE_FAILED", "Hub application create succeeded without returning a UUID")
            action = "created"
        else:
            update = {k: v for k, v in payload.items() if k not in {"project_uuid", "server_uuid", "environment_name", "git_repository"}}
            response = client.request("PATCH", f"/api/v1/applications/{urllib.parse.quote(app_uuid)}", update)
            if not response.ok and response.status not in {405}:
                raise HubControlError("HUB_COOLIFY_UPDATE_FAILED", f"Hub application update failed: HTTP {response.status}: {response.body}")
            if not response.ok:
                response = client.request("PUT", f"/api/v1/applications/{urllib.parse.quote(app_uuid)}", update)
                if not response.ok:
                    raise HubControlError("HUB_COOLIFY_UPDATE_FAILED", f"Hub application update failed: HTTP {response.status}: {response.body}")

        _ensure_storage(client, target, app_uuid, tried)
        topology_b64 = base64.b64encode(canonical_bytes(target["topology"])).decode("ascii")
        env_values = {
            FDB_CLUSTER_ENV: str(target["fdb_contract"]["connection_string"]),
            TOPOLOGY_B64_ENV: topology_b64,
            "MAIN_COMPUTER_HUB_NETWORK": str(target["network"]),
            "MAIN_COMPUTER_HUB_PORT": str(target["hub_bind_port"]),
            "MAIN_COMPUTER_HUB_URL": str(target["public_url"]),
            "MAIN_COMPUTER_HUB_ROOT": str(target["runtime_dir"]),
            "MAIN_COMPUTER_HUB_FDB_CLUSTER_FILE": str(target["cluster_file_path"]),
            "MAIN_COMPUTER_HUB_FDB_NAMESPACE": str(target["fdb_contract"]["namespace"]),
            "MAIN_COMPUTER_HUB_CHAIN_ID": str(target["chain_contract"]["chain_id"]),
            "MAIN_COMPUTER_HUB_CHAIN_RPC_URL": str(target["chain_contract"]["rpc_url"]),
            "MAIN_COMPUTER_HUB_CONTRACTS_PATH": f"/app/main_computer/config/{target['network']}_contracts.json",
            "MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER": "true",
            "MAIN_COMPUTER_HUB_BRIDGE_BACKEND": "dev-chain",
            FDB_CONTRACT_ENV: str(target["fdb_contract"]["sha256"]),
            CHAIN_CONTRACT_ENV: str(target["chain_contract"]["sha256"]),
            "MAIN_COMPUTER_HUB_CONTROL_HUB_ID": str(target["hub_id"]),
            "MAIN_COMPUTER_HUB_CONTROL_PUBLIC_URL": str(target["public_url"]),
            "MAIN_COMPUTER_HUB_CONTROL_RUNTIME_DIR": str(target["runtime_dir"]),
            "MAIN_COMPUTER_HUB_CONTROL_CLUSTER_FILE": str(target["cluster_file_path"]),
            "MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_PATH": str(target["topology_path"]),
            "MAIN_COMPUTER_HUB_CONTROL_NETWORK": str(target["network"]),
            "MAIN_COMPUTER_HUB_CONTROL_NETWORK_DISPLAY_NAME": str(target["network_display_name"]),
            "MAIN_COMPUTER_HUB_CONTROL_NETWORK_KIND": str(target["network_kind"]),
            "MAIN_COMPUTER_HUB_CONTROL_CHAIN_ID": str(target["chain_contract"]["chain_id"]),
            "MAIN_COMPUTER_HUB_CONTROL_CHAIN_RPC_URL": str(target["chain_contract"]["rpc_url"]),
            "MAIN_COMPUTER_HUB_CONTROL_FDB_NAMESPACE": str(target["fdb_contract"]["namespace"]),
            "MAIN_COMPUTER_HUB_CONTROL_FDB_API_VERSION": str(target["fdb_contract"].get("api_version", 740)),
        }
        for key, value in env_values.items():
            legacy.sync_application_env_var(client, application_uuid=app_uuid, key=key, value=value, tried=tried)
        legacy.trigger_deploy(client, application_uuid=app_uuid, force=True, tried=tried)
        return {"application_uuid": app_uuid, "action": action, "environment_variables": sorted(env_values)}
    except HubControlError:
        raise
    except Exception as exc:
        raise HubControlError("HUB_COOLIFY_DEPLOY_FAILED", str(exc)) from exc


def _get_json(url: str, *, timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "MainComputerHubControl/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise HubControlError("HUB_OBSERVER_INVALID", f"Hub observer returned non-object JSON from {url}")
    return payload


def observe_hub(target: Mapping[str, Any], *, wait_timeout_s: float = 300.0, request_timeout_s: float = 12.0) -> dict[str, Any]:
    base = str(target["public_url"]).rstrip("/")
    deadline = time.monotonic() + max(0.0, wait_timeout_s)
    last_error: object = None
    while True:
        try:
            identity = _get_json(base + "/api/hub/v1/hub-identity", timeout_s=request_timeout_s)
            status = _get_json(base + "/api/hub/v1/status", timeout_s=request_timeout_s)
            expected_fdb = target["fdb_contract"]
            expected_chain = target["chain_contract"]
            network = identity.get("network") if isinstance(identity.get("network"), Mapping) else {}
            storage = identity.get("storage") if isinstance(identity.get("storage"), Mapping) else {}
            status_network = status.get("network") if isinstance(status.get("network"), Mapping) else {}
            checks = {
                "hub_identity": identity.get("hub_id") == target["hub_id"],
                "fdb_backend": storage.get("backend") == "foundationdb",
                "fdb_cluster_file": str(storage.get("cluster_file") or "") == str(target["cluster_file_path"]),
                "fdb_namespace": str(storage.get("namespace") or "") == str(expected_fdb["namespace"]),
                "chain_id": int(network.get("chain_id")) == int(expected_chain["chain_id"]),
                "chain_rpc": str(network.get("chain_rpc_url") or "").rstrip("/") == str(expected_chain["rpc_url"]).rstrip("/"),
                "status_chain_id": int(status_network.get("chain_id")) == int(expected_chain["chain_id"]),
                "status_rpc": str(status_network.get("chain_rpc_url") or "").rstrip("/") == str(expected_chain["rpc_url"]).rstrip("/"),
            }
            if all(checks.values()):
                return {
                    "verified": True,
                    "reason": "hub-fdb-and-chain-consumption-verified",
                    "hub_running": True,
                    "fdb_adoption_verified": True,
                    "chain_adoption_verified": True,
                    "checks": checks,
                    "identity": identity,
                    "status": status,
                }
            last_error = checks
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        if time.monotonic() >= deadline:
            return {
                "verified": False,
                "reason": "hub-runtime-verification-timeout",
                "hub_running": False,
                "fdb_adoption_verified": False,
                "chain_adoption_verified": False,
                "last_error": last_error,
            }
        time.sleep(5.0)
