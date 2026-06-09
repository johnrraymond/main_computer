from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


CONTROLLER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
DEFAULT_LOCAL_COOLIFY_ID = "coolify-local"
DEFAULT_LOCAL_COOLIFY_PORT = "8000"


class DeploymentControllerError(ValueError):
    """Raised when a deployment controller registry entry is invalid."""


@dataclass(frozen=True)
class DeploymentController:
    id: str
    kind: str
    name: str
    base_url: str
    roles: tuple[str, ...]
    default_for: tuple[str, ...]
    token_ref: str = ""
    local: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "base_url": self.base_url,
            "roles": list(self.roles),
            "default_for": list(self.default_for),
            "token_ref": self.token_ref,
            "local": self.local,
            "configured": bool(self.base_url),
            "has_token_ref": bool(self.token_ref),
            "has_token_value": bool(self.token_ref and os.environ.get(self.token_ref)),
        }


@dataclass(frozen=True)
class DeploymentControllerRegistry:
    controllers: tuple[DeploymentController, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"controllers": [controller.to_dict() for controller in self.controllers]}

    def get(self, controller_id: object) -> DeploymentController | None:
        clean_id = str(controller_id or "").strip().lower()
        for controller in self.controllers:
            if controller.id == clean_id:
                return controller
        return None

    def defaults_for(self, lane: object) -> list[DeploymentController]:
        clean_lane = normalize_publish_target_lane(lane)
        return [controller for controller in self.controllers if clean_lane in controller.default_for]

    def controllers_for_role(self, role: object) -> list[DeploymentController]:
        clean_role = normalize_publish_target_lane(role)
        return [controller for controller in self.controllers if clean_role in controller.roles]


def deployment_runtime_root(repo_root: Path) -> Path:
    return repo_root / "runtime" / "deployment"


def deployment_controllers_path(repo_root: Path) -> Path:
    return deployment_runtime_root(repo_root) / "controllers.json"


def normalize_publish_target_lane(lane: object) -> str:
    value = str(lane or "").strip().lower().replace("_", "-")
    aliases = {
        "local": "local-prod",
        "prod": "local-prod",
        "production": "remote-prod",
        "remote": "remote-prod",
        "remote-production": "remote-prod",
        "dev": "dev-services",
        "dev-services": "dev-services",
        "local-prod": "local-prod",
        "remote-prod": "remote-prod",
    }
    return aliases.get(value, value)


def validate_controller_id(controller_id: object) -> str:
    value = str(controller_id or "").strip().lower()
    if not CONTROLLER_ID_RE.fullmatch(value):
        raise DeploymentControllerError(
            "Controller id must be 3-64 characters of lowercase letters, numbers, and hyphens."
        )
    return value


def _normalize_url(value: object) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DeploymentControllerError("Controller base_url must be an http(s) URL.")
    return text


def _normalize_string_list(value: object, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        source = [value]
    elif isinstance(value, (list, tuple)):
        source = list(value)
    else:
        raise DeploymentControllerError("Controller roles/default_for must be a list of strings.")
    normalized: list[str] = []
    for item in source:
        lane = normalize_publish_target_lane(item)
        if lane and lane not in normalized:
            normalized.append(lane)
    return tuple(normalized)


def default_local_coolify_base_url() -> str:
    configured_url = os.environ.get("MAIN_COMPUTER_COOLIFY_LOCAL_URL")
    if configured_url:
        return _normalize_url(configured_url)
    port = str(
        os.environ.get("MAIN_COMPUTER_COOLIFY_APP_PORT")
        or os.environ.get("APP_PORT")
        or DEFAULT_LOCAL_COOLIFY_PORT
    ).strip()
    return _normalize_url(f"http://localhost:{port}")


def default_local_coolify_token_ref() -> str:
    return str(os.environ.get("MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_REF") or "MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN").strip()


def default_controller_data() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "controllers": [
            {
                "id": DEFAULT_LOCAL_COOLIFY_ID,
                "kind": "coolify",
                "name": "Local Coolify",
                "base_url": default_local_coolify_base_url(),
                "token_ref": default_local_coolify_token_ref(),
                "roles": ["dev-services", "remote-prod"],
                "default_for": ["remote-prod"],
                "local": True,
            }
        ],
    }


def _controller_from_dict(raw: dict[str, Any]) -> DeploymentController:
    if not isinstance(raw, dict):
        raise DeploymentControllerError("Controller entries must be objects.")
    controller_id = validate_controller_id(raw.get("id"))
    kind = str(raw.get("kind") or "coolify").strip().lower()
    if kind != "coolify":
        raise DeploymentControllerError(f"Unsupported deployment controller kind: {kind!r}")
    name = str(raw.get("name") or controller_id).strip() or controller_id
    base_url = _normalize_url(raw.get("base_url"))
    token_ref = str(raw.get("token_ref") or "").strip()
    roles = _normalize_string_list(raw.get("roles"), default=("remote-prod",))
    default_for = _normalize_string_list(raw.get("default_for"), default=())
    return DeploymentController(
        id=controller_id,
        kind=kind,
        name=name,
        base_url=base_url,
        roles=roles,
        default_for=default_for,
        token_ref=token_ref,
        local=bool(raw.get("local")),
    )


def normalize_controller_registry_data(data: object) -> dict[str, Any]:
    source = data if isinstance(data, dict) else {}
    raw_controllers = source.get("controllers")
    if not isinstance(raw_controllers, list):
        raw_controllers = default_controller_data()["controllers"]

    default_local = default_controller_data()["controllers"][0]
    controllers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_controllers:
        controller = _controller_from_dict(raw)
        if controller.id in seen:
            raise DeploymentControllerError(f"Duplicate deployment controller id: {controller.id}")
        seen.add(controller.id)
        payload = controller.to_dict()
        if controller.id == DEFAULT_LOCAL_COOLIFY_ID:
            payload["local"] = True
            if os.environ.get("MAIN_COMPUTER_COOLIFY_LOCAL_URL"):
                payload["base_url"] = default_local["base_url"]
            elif not payload.get("base_url"):
                payload["base_url"] = default_local["base_url"]
            if os.environ.get("MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_REF"):
                payload["token_ref"] = default_local["token_ref"]
            elif not payload.get("token_ref"):
                payload["token_ref"] = default_local["token_ref"]
            payload["roles"] = list(dict.fromkeys([*payload.get("roles", []), *default_local["roles"]]))
            payload["default_for"] = list(dict.fromkeys([*payload.get("default_for", []), *default_local["default_for"]]))
        controllers.append(payload)

    if DEFAULT_LOCAL_COOLIFY_ID not in seen:
        controllers.insert(0, default_local)

    return {"schema_version": 1, "controllers": controllers}


def load_deployment_controller_registry(repo_root: Path) -> DeploymentControllerRegistry:
    path = deployment_controllers_path(repo_root)
    if not path.exists():
        data = default_controller_data()
        save_deployment_controller_registry(repo_root, data)
    else:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DeploymentControllerError(f"Invalid deployment controller registry JSON: {path}") from exc

    normalized = normalize_controller_registry_data(data)
    if not path.exists() or data != normalized:
        save_deployment_controller_registry(repo_root, normalized)
    controllers = tuple(_controller_from_dict(raw) for raw in normalized["controllers"])
    return DeploymentControllerRegistry(controllers=controllers)


def save_deployment_controller_registry(repo_root: Path, data: object) -> DeploymentControllerRegistry:
    normalized = normalize_controller_registry_data(data)
    path = deployment_controllers_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    controllers = tuple(_controller_from_dict(raw) for raw in normalized["controllers"])
    return DeploymentControllerRegistry(controllers=controllers)


def upsert_deployment_controller(repo_root: Path, controller: dict[str, Any]) -> DeploymentControllerRegistry:
    new_controller = _controller_from_dict(controller)
    registry = load_deployment_controller_registry(repo_root)
    data = registry.to_dict()
    replaced = False
    for index, existing in enumerate(data["controllers"]):
        if existing["id"] == new_controller.id:
            existing_local = bool(existing.get("local"))
            payload = new_controller.to_dict()
            if existing_local:
                payload["local"] = True
            data["controllers"][index] = payload
            replaced = True
            break
    if not replaced:
        data["controllers"].append(new_controller.to_dict())
    return save_deployment_controller_registry(repo_root, data)


def publish_target_defaults(repo_root: Path) -> dict[str, Any]:
    registry = load_deployment_controller_registry(repo_root)
    remote_defaults = registry.defaults_for("remote-prod")
    remote_default = next((controller for controller in remote_defaults if not controller.local), None)
    return {
        # Local-prod is served by the local Docker platform, not by the local
        # Coolify smoke controller. Keep this empty unless a site/user
        # explicitly selects a controller for that lane.
        "local_prod_controller_id": "",
        # Publish now runs a saved command template. Do not silently default new
        # sites to the local Coolify rehearsal controller or the old Coolify deploy API flow.
        "remote_prod_controller_id": remote_default.id if remote_default else "",
    }


def site_publish_targets(site: dict[str, Any] | None, repo_root: Path) -> dict[str, Any]:
    defaults = publish_target_defaults(repo_root)
    source = site if isinstance(site, dict) else {}
    raw_targets = source.get("publish_targets")
    if not isinstance(raw_targets, dict):
        raw_targets = {}

    local_prod = raw_targets.get("local_prod") if isinstance(raw_targets.get("local_prod"), dict) else {}
    remote_prod = raw_targets.get("remote_prod") if isinstance(raw_targets.get("remote_prod"), dict) else {}
    site_id = str(source.get("id") or "").strip().lower()
    default_local_domain = f"{site_id}.localhost" if site_id else ""

    return {
        "local_prod": {
            "controller_id": str(local_prod.get("controller_id") or defaults["local_prod_controller_id"]),
            "project": str(local_prod.get("project") or site_id),
            "environment": str(local_prod.get("environment") or "local-prod"),
            "domain": str(local_prod.get("domain") or default_local_domain),
            "accepted_at": str(local_prod.get("accepted_at") or ""),
        },
        "remote_prod": {
            "controller_id": str(remote_prod.get("controller_id") or defaults["remote_prod_controller_id"]),
            "project": str(remote_prod.get("project") or site_id),
            "environment": str(remote_prod.get("environment") or "production"),
            "domain": str(remote_prod.get("domain") or ""),
            "publish_mode": str(remote_prod.get("publish_mode") or ("local_server" if remote_prod.get("use_local_server") else "scp")),
            "use_local_server": bool(remote_prod.get("use_local_server") or str(remote_prod.get("publish_mode") or "").strip().lower() == "local_server"),
            "site_slug": str(remote_prod.get("site_slug") or remote_prod.get("project") or site_id),
            "source_path": str(remote_prod.get("source_path") or (f"runtime/websites/{site_id}" if site_id else "")),
            "remote_host": str(remote_prod.get("remote_host") or ""),
            "remote_root": str(remote_prod.get("remote_root") or "/srv/main-computer/sites"),
            "ssh_password_file": str(remote_prod.get("ssh_password_file") or (f"runtime/websites/{site_id}/ssh_password.local" if site_id else "")),
            "resource_uuid": str(remote_prod.get("resource_uuid") or ""),
            "service_uuid": str(remote_prod.get("service_uuid") or ""),
            "application_uuid": str(remote_prod.get("application_uuid") or ""),
            "uuid": str(remote_prod.get("uuid") or ""),
            "accepted_at": str(remote_prod.get("accepted_at") or ""),
        },
    }
