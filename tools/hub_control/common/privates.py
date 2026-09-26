from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .errors import HubControlError
from .models import HubContext


def load_private(ctx: HubContext) -> dict[str, Any]:
    path = ctx.mother_private_path
    if not path.is_file():
        raise HubControlError("HUB_PRIVATE_STATE_MISSING", f"shared private infrastructure file does not exist: {path}")
    try:
        import yaml
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HubControlError("HUB_PRIVATE_STATE_INVALID", f"could not parse shared private infrastructure {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HubControlError("HUB_PRIVATE_STATE_INVALID", f"shared private infrastructure must be a mapping: {path}")
    return value


def network_doc(private: Mapping[str, Any], network: str) -> Mapping[str, Any]:
    networks = private.get("networks")
    value = networks.get(network) if isinstance(networks, Mapping) else None
    if not isinstance(value, Mapping):
        raise HubControlError("HUB_PRIVATE_NETWORK_MISSING", f"shared private infrastructure has no networks.{network} mapping")
    return value


def controller_doc(private: Mapping[str, Any], network: str, controller_id: str) -> Mapping[str, Any]:
    net = network_doc(private, network)
    coolify = net.get("coolify")
    controllers = coolify.get("controllers") if isinstance(coolify, Mapping) else None
    raw = controllers.get(controller_id) if isinstance(controllers, Mapping) else None
    if not isinstance(raw, Mapping):
        raise HubControlError(
            "HUB_PRIVATE_CONTROLLER_MISSING",
            f"shared private infrastructure has no networks.{network}.coolify.controllers.{controller_id}",
        )
    return raw


def resolve_controller(private: Mapping[str, Any], network: str, placement_token: str) -> tuple[str, Mapping[str, Any]]:
    controller_id = f"coolify-{str(placement_token).strip().lower()}"
    raw = controller_doc(private, network, controller_id)
    return controller_id, raw


def controller_coordinates(private: Mapping[str, Any], network: str, controller_id: str, *, base_dir: Path | None = None) -> dict[str, str]:
    raw = controller_doc(private, network, controller_id)

    def first(*keys: str) -> str:
        for key in keys:
            value = str(raw.get(key) or "").strip()
            if value and not (value.startswith("<") and value.endswith(">")):
                return value
        return ""

    url = first("url", "coolify_url", "api_url", "base_url").rstrip("/")
    token = first("api_token", "token", "coolify_token")
    if not token:
        env_name = first("api_token_env", "token_env", "coolify_token_env")
        if env_name:
            token = str(os.environ.get(env_name) or "").strip()
    if not token:
        raw_file = first("api_token_file", "token_file", "coolify_token_file")
        if raw_file:
            path = Path(raw_file)
            if not path.is_absolute() and base_dir is not None:
                path = base_dir / path
            try:
                token = path.read_text(encoding="utf-8").strip()
            except OSError:
                token = ""
    project_uuid = first("project_uuid")
    server_uuid = first("server_uuid")
    missing = [name for name, value in (("url", url), ("api_token", token), ("project_uuid", project_uuid), ("server_uuid", server_uuid)) if not value]
    if missing:
        raise HubControlError(
            "HUB_PRIVATE_CONTROLLER_INCOMPLETE",
            f"networks.{network}.coolify.controllers.{controller_id} is missing " + ", ".join(missing),
        )
    return {
        "url": url,
        "api_token": token,
        "project_uuid": project_uuid,
        "server_uuid": server_uuid,
        "project_name_hint": first("project_name_hint"),
    }


def private_generation(ctx: HubContext) -> int:
    path = ctx.mother_metadata_path
    if not path.is_file():
        return 1
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        generation = int(payload.get("generation", 1)) if isinstance(payload, dict) else 1
    except Exception:
        return 1
    return max(1, generation)
