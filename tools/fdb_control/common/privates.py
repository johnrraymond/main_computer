from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import FdbControlError
from .models import FdbContext

_PLACEHOLDER_PREFIX = "<"
_URL_KEYS = ("url", "coolify_url", "api_url", "base_url")
_TOKEN_KEYS = ("api_token", "token", "coolify_token")
_TOKEN_ENV_KEYS = ("api_token_env", "token_env", "coolify_token_env")
_TOKEN_FILE_KEYS = ("api_token_file", "token_file", "coolify_token_file")
_FDB_ADDRESS_KEYS = ("fdb_vpn_ip", "vpn_ip", "private_vpn_ip", "wireguard_ip", "tailscale_ip")


@dataclass(frozen=True, slots=True)
class HostBinding:
    host_id: str
    slot: str
    coolify_url: str
    token: str
    token_source: str


def load_private_infrastructure(ctx: FdbContext) -> dict[str, Any]:
    """Load the same canonical private identity document consumed by Mother.

    FDB Control remains operationally independent of Mother, but the shared
    infrastructure source is runtime/state/mother/identity.private.yaml.  FDB reads
    that document directly and does not import Mother runtime code or Mother
    journals/evidence.
    """

    path = ctx.private_state_path
    if not path.is_file():
        raise FdbControlError(
            code="FDB_PRIVATE_STATE_MISSING",
            message=f"shared private infrastructure file does not exist: {path}",
            module_id="FDB-OFM-PRIV-001",
        )
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - repository environment normally has PyYAML
        raise FdbControlError(
            code="FDB_PRIVATE_STATE_YAML_UNAVAILABLE",
            message="PyYAML is required to read runtime/state/mother/identity.private.yaml",
            module_id="FDB-OFM-PRIV-001",
        ) from exc
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise FdbControlError(
            code="FDB_PRIVATE_STATE_INVALID",
            message=f"could not parse shared private infrastructure file {path}: {exc}",
            module_id="FDB-OFM-PRIV-001",
        ) from exc
    if not isinstance(payload, dict):
        raise FdbControlError(
            code="FDB_PRIVATE_STATE_INVALID",
            message=f"shared private infrastructure file must contain a YAML mapping: {path}",
            module_id="FDB-OFM-PRIV-001",
        )
    return payload


def _controller_record(
    private_doc: Mapping[str, Any],
    network: str,
    host_id: str,
) -> tuple[str, Mapping[str, Any]]:
    network_id = str(network or "").strip()
    wanted = str(host_id or "").strip()
    networks = private_doc.get("networks")
    network_doc = networks.get(network_id) if isinstance(networks, Mapping) else None
    coolify = network_doc.get("coolify") if isinstance(network_doc, Mapping) else None
    controllers = coolify.get("controllers") if isinstance(coolify, Mapping) else None
    if not isinstance(controllers, Mapping):
        raise FdbControlError(
            code="FDB_PRIVATE_CONTROLLERS_MISSING",
            message=f"shared private infrastructure has no networks.{network_id}.coolify.controllers mapping",
            module_id="FDB-OFM-PRIV-002",
        )

    raw = controllers.get(wanted)
    if not isinstance(raw, Mapping):
        raise FdbControlError(
            code="FDB_PRIVATE_HOST_NOT_UNIQUE",
            message=(
                f"expected exactly one networks.{network_id}.coolify.controllers entry named {wanted!r}; "
                "found 0"
            ),
            module_id="FDB-OFM-PRIV-002",
        )
    return wanted, raw


def resolve_host_fdb_address(
    private_doc: Mapping[str, Any],
    network: str,
    host_id: str,
) -> tuple[str, str]:
    controller_id, raw = _controller_record(private_doc, network, host_id)
    for key in _FDB_ADDRESS_KEYS:
        value = _known_text(raw.get(key))
        if not value:
            continue
        text = value.strip().strip("[]")
        lower = text.rstrip(".").lower()
        if lower in {"localhost", "0.0.0.0", "::", "::1"} or lower.startswith("127."):
            continue
        try:
            parsed = ipaddress.ip_address(text)
        except ValueError:
            parsed = None
        if parsed is not None and (parsed.is_loopback or parsed.is_unspecified):
            continue
        if any(ch.isspace() for ch in text) or "/" in text or "\\" in text or "\x00" in text:
            continue
        return text, f"private-state:networks.{network}.coolify.controllers.{controller_id}.{key}"
    raise FdbControlError(
        code="FDB_PRIVATE_FDB_ADDRESS_MISSING",
        message=(
            f"Coolify controller {host_id!r} in network {network!r} has no usable private/VPN address for FDB"
        ),
        module_id="FDB-OFM-PRIV-002",
        retry_class="never",
    )


def resolve_host_binding(
    private_doc: Mapping[str, Any],
    network: str,
    host_id: str,
    *,
    base_dir: Path | None = None,
) -> HostBinding:
    wanted, raw = _controller_record(private_doc, network, host_id)
    url = _first_known(raw, _URL_KEYS)
    if not url:
        raise FdbControlError(
            code="FDB_PRIVATE_HOST_URL_MISSING",
            message=f"Coolify controller {wanted!r} in network {network!r} has no URL in shared privates",
            module_id="FDB-OFM-PRIV-002",
        )
    prefix = f"networks.{network}.coolify.controllers.{wanted}"
    token, source = _resolve_token(raw, prefix, base_dir=base_dir)
    if not token:
        raise FdbControlError(
            code="FDB_PRIVATE_HOST_TOKEN_MISSING",
            message=f"Coolify controller {wanted!r} in network {network!r} has no usable API token in shared privates",
            module_id="FDB-OFM-PRIV-002",
        )
    return HostBinding(
        host_id=wanted,
        slot=wanted,
        coolify_url=url.rstrip("/"),
        token=token,
        token_source=source,
    )



def resolve_controller_coordinates(
    private_doc: Mapping[str, Any],
    network: str,
    host_id: str,
) -> dict[str, str]:
    """Return Mother-canonical non-secret Coolify deployment coordinates."""

    controller_id, raw = _controller_record(private_doc, network, host_id)
    project_uuid = _known_text(raw.get("project_uuid"))
    server_uuid = _known_text(raw.get("server_uuid"))
    if not project_uuid or not server_uuid:
        missing = [name for name, value in (("project_uuid", project_uuid), ("server_uuid", server_uuid)) if not value]
        raise FdbControlError(
            code="FDB_PRIVATE_CONTROLLER_COORDINATES_MISSING",
            message=(
                f"Coolify controller {controller_id!r} in network {network!r} is missing "
                + ", ".join(missing)
            ),
            module_id="FDB-OFM-PRIV-002",
            retry_class="never",
        )
    return {
        "controller_id": controller_id,
        "project_uuid": project_uuid,
        "server_uuid": server_uuid,
        "project_name_hint": _known_text(raw.get("project_name_hint")),
    }

def public_binding_ref(binding: HostBinding) -> dict[str, str]:
    return {
        "host_id": binding.host_id,
        "slot": binding.slot,
        "coolify_url": binding.coolify_url,
        "token_source": binding.token_source,
    }


def _known_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or (text.startswith(_PLACEHOLDER_PREFIX) and text.endswith(">")):
        return ""
    return text


def _first_known(payload: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _known_text(payload.get(key))
        if value:
            return value
    return ""


def _resolve_token(payload: Mapping[str, Any], prefix: str, *, base_dir: Path | None) -> tuple[str, str]:
    token = _first_known(payload, _TOKEN_KEYS)
    if token:
        key = next(key for key in _TOKEN_KEYS if _known_text(payload.get(key)))
        return token, f"private-state:{prefix}.{key}"

    env_name = _first_known(payload, _TOKEN_ENV_KEYS)
    if env_name:
        value = str(os.environ.get(env_name) or "").strip()
        if not value:
            raise FdbControlError(
                code="FDB_PRIVATE_TOKEN_ENV_EMPTY",
                message=f"private token env var {env_name!r} is unset or empty",
                module_id="FDB-OFM-PRIV-002",
            )
        key = next(key for key in _TOKEN_ENV_KEYS if _known_text(payload.get(key)))
        return value, f"private-state:{prefix}.{key}->env:{env_name}"

    raw_file = _first_known(payload, _TOKEN_FILE_KEYS)
    if raw_file:
        path = Path(raw_file)
        if not path.is_absolute() and base_dir is not None:
            path = base_dir / path
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise FdbControlError(
                code="FDB_PRIVATE_TOKEN_FILE_UNREADABLE",
                message=f"could not read Coolify token file {path}: {exc}",
                module_id="FDB-OFM-PRIV-002",
            ) from exc
        if not value:
            raise FdbControlError(
                code="FDB_PRIVATE_TOKEN_FILE_EMPTY",
                message=f"Coolify token file is empty: {path}",
                module_id="FDB-OFM-PRIV-002",
            )
        key = next(key for key in _TOKEN_FILE_KEYS if _known_text(payload.get(key)))
        return value, f"private-state:{prefix}.{key}->file:{path}"
    return "", ""
