from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


DEFAULT_HUB_NETWORKS_PATH = Path(__file__).with_name("config") / "hub_networks.json"


class HubNetworkConfigError(ValueError):
    """Raised when the Hub network registry or selected network is invalid."""


_MISSING = object()


def _clean_text(value: object, *, field: str, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise HubNetworkConfigError(f"Hub network field {field!r} is required.")
        return None
    text = str(value).strip()
    if not text:
        if required:
            raise HubNetworkConfigError(f"Hub network field {field!r} is required.")
        return None
    return text


def _parse_int(value: object, *, field: str, required: bool = True) -> int | None:
    if value is None or value == "":
        if required:
            raise HubNetworkConfigError(f"Hub network field {field!r} is required.")
        return None
    try:
        parsed = int(str(value).strip(), 0)
    except (TypeError, ValueError):
        raise HubNetworkConfigError(f"Hub network field {field!r} must be an integer.") from None
    if parsed < 0:
        raise HubNetworkConfigError(f"Hub network field {field!r} must be non-negative.")
    return parsed


def _parse_port(value: object, *, field: str) -> int:
    parsed = _parse_int(value, field=field, required=True)
    assert parsed is not None
    if parsed <= 0 or parsed > 65535:
        raise HubNetworkConfigError(f"Hub network field {field!r} must be a TCP port from 1 to 65535.")
    return parsed


def _parse_optional_path(value: object, *, field: str) -> Path:
    text = _clean_text(value, field=field, required=True)
    assert text is not None
    return Path(text)


def _normalize_contracts(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise HubNetworkConfigError("Hub network field 'contracts' must be an object.")
    contracts: dict[str, str] = {}
    for key, raw_address in value.items():
        clean_key = str(key).strip()
        if not clean_key:
            raise HubNetworkConfigError("Hub network contract names must not be empty.")
        if raw_address is None or str(raw_address).strip() == "":
            continue
        contracts[clean_key] = str(raw_address).strip()
    return contracts


@dataclass(frozen=True)
class HubNetworkProfile:
    network_key: str
    display_name: str
    kind: str
    chain_id: int | None
    chain_rpc_url: str | None
    hub_host: str
    hub_port: int
    hub_runtime_dir: Path
    native_currency: dict[str, Any]
    contracts: dict[str, str]
    source_path: Path

    @classmethod
    def from_mapping(cls, network_key: str, payload: object, *, source_path: Path) -> "HubNetworkProfile":
        if not isinstance(payload, dict):
            raise HubNetworkConfigError(f"Hub network {network_key!r} must be an object.")
        clean_key = _clean_text(payload.get("network_key", network_key), field=f"networks.{network_key}.network_key")
        assert clean_key is not None
        if clean_key != network_key:
            raise HubNetworkConfigError(
                f"Hub network key mismatch: registry key {network_key!r} has network_key {clean_key!r}."
            )
        display_name = _clean_text(
            payload.get("display_name", network_key),
            field=f"networks.{network_key}.display_name",
        )
        kind = _clean_text(payload.get("kind", "dev"), field=f"networks.{network_key}.kind")
        chain_id = _parse_int(payload.get("chain_id"), field=f"networks.{network_key}.chain_id", required=False)
        chain_rpc_url = _clean_text(
            payload.get("chain_rpc_url"),
            field=f"networks.{network_key}.chain_rpc_url",
            required=False,
        )
        hub_host = _clean_text(
            payload.get("hub_host", "127.0.0.1"),
            field=f"networks.{network_key}.hub_host",
        )
        hub_port = _parse_port(payload.get("hub_port"), field=f"networks.{network_key}.hub_port")
        hub_runtime_dir = _parse_optional_path(
            payload.get("hub_runtime_dir"),
            field=f"networks.{network_key}.hub_runtime_dir",
        )
        native_currency = payload.get("native_currency") or {}
        if not isinstance(native_currency, dict):
            raise HubNetworkConfigError(f"Hub network {network_key!r} native_currency must be an object.")
        return cls(
            network_key=clean_key,
            display_name=display_name or network_key,
            kind=kind or "dev",
            chain_id=chain_id,
            chain_rpc_url=chain_rpc_url,
            hub_host=hub_host or "127.0.0.1",
            hub_port=hub_port,
            hub_runtime_dir=hub_runtime_dir,
            native_currency=dict(native_currency),
            contracts=_normalize_contracts(payload.get("contracts")),
            source_path=source_path,
        )

    def with_overrides(
        self,
        *,
        hub_host: str | None = None,
        hub_port: int | str | None = None,
        hub_runtime_dir: Path | str | None = None,
        chain_rpc_url: str | None = None,
        chain_id: int | str | object = _MISSING,
    ) -> "HubNetworkProfile":
        updates: dict[str, object] = {}
        if hub_host is not None and str(hub_host).strip():
            updates["hub_host"] = str(hub_host).strip()
        if hub_port is not None:
            updates["hub_port"] = _parse_port(hub_port, field="hub_port override")
        if hub_runtime_dir is not None and str(hub_runtime_dir).strip():
            updates["hub_runtime_dir"] = Path(str(hub_runtime_dir).strip())
        if chain_rpc_url is not None and str(chain_rpc_url).strip():
            updates["chain_rpc_url"] = str(chain_rpc_url).strip()
        if chain_id is not _MISSING:
            updates["chain_id"] = _parse_int(chain_id, field="chain_id override", required=False)
        return replace(self, **updates)

    def validate_runnable(self) -> None:
        missing: list[str] = []
        if self.chain_id is None:
            missing.append("chain_id")
        if not self.chain_rpc_url:
            missing.append("chain_rpc_url")
        if missing:
            fields = ", ".join(missing)
            raise HubNetworkConfigError(
                f"Hub network {self.network_key!r} is not runnable until {fields} is configured "
                "or overridden on the command line."
            )

    def as_status_payload(self) -> dict[str, Any]:
        return {
            "network_key": self.network_key,
            "display_name": self.display_name,
            "kind": self.kind,
            "chain_id": self.chain_id,
            "chain_id_hex": hex(self.chain_id) if self.chain_id is not None else None,
            "chain_rpc_url": self.chain_rpc_url,
            "hub_host": self.hub_host,
            "hub_port": self.hub_port,
            "hub_url": f"http://{self.hub_host}:{self.hub_port}",
            "hub_runtime_dir": str(self.hub_runtime_dir),
            "native_currency": dict(self.native_currency),
            "contracts": dict(self.contracts),
            "source_path": str(self.source_path),
        }


@dataclass(frozen=True)
class HubNetworkRegistry:
    version: int
    default_network: str
    networks: dict[str, HubNetworkProfile]
    source_path: Path

    def get(self, network: str | None = None) -> HubNetworkProfile:
        key = (network or self.default_network or "").strip()
        if not key:
            raise HubNetworkConfigError("No Hub network was selected and the registry has no default_network.")
        try:
            return self.networks[key]
        except KeyError:
            available = ", ".join(sorted(self.networks)) or "(none)"
            raise HubNetworkConfigError(f"Unknown Hub network {key!r}. Available networks: {available}.") from None


def resolve_hub_networks_path(path: str | Path | None = None) -> Path:
    if path is not None and str(path).strip():
        return Path(path)
    env_path = os.environ.get("MAIN_COMPUTER_HUB_NETWORKS_FILE") or os.environ.get("MAIN_COMPUTER_HUB_NETWORK_CONFIG")
    if env_path and env_path.strip():
        return Path(env_path.strip())
    return DEFAULT_HUB_NETWORKS_PATH


def load_hub_network_registry(path: str | Path | None = None) -> HubNetworkRegistry:
    resolved_path = resolve_hub_networks_path(path)
    try:
        raw = json.loads(resolved_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HubNetworkConfigError(f"Hub network registry not found: {resolved_path}") from None
    except json.JSONDecodeError as exc:
        raise HubNetworkConfigError(f"Hub network registry is not valid JSON: {resolved_path}: {exc}") from None

    if not isinstance(raw, dict):
        raise HubNetworkConfigError("Hub network registry must be a JSON object.")
    version = _parse_int(raw.get("version", 1), field="version", required=True)
    assert version is not None
    default_network = _clean_text(raw.get("default_network"), field="default_network")
    assert default_network is not None
    raw_networks = raw.get("networks")
    if not isinstance(raw_networks, dict) or not raw_networks:
        raise HubNetworkConfigError("Hub network registry must contain a non-empty 'networks' object.")

    networks: dict[str, HubNetworkProfile] = {}
    for key, payload in raw_networks.items():
        clean_key = str(key).strip()
        if not clean_key:
            raise HubNetworkConfigError("Hub network keys must not be empty.")
        networks[clean_key] = HubNetworkProfile.from_mapping(clean_key, payload, source_path=resolved_path)

    if default_network not in networks:
        raise HubNetworkConfigError(f"Hub network default_network {default_network!r} is not defined in networks.")
    return HubNetworkRegistry(
        version=version,
        default_network=default_network,
        networks=networks,
        source_path=resolved_path,
    )


def env_hub_network_name() -> str | None:
    value = os.environ.get("MAIN_COMPUTER_HUB_NETWORK") or os.environ.get("MAIN_COMPUTER_NETWORK")
    if value and value.strip():
        return value.strip()
    return None


def env_hub_host_override() -> str | None:
    value = os.environ.get("MAIN_COMPUTER_HUB_HOST")
    if value and value.strip():
        return value.strip()
    return None


def env_hub_port_override() -> int | None:
    value = os.environ.get("MAIN_COMPUTER_HUB_PORT")
    if value and value.strip():
        return _parse_port(value, field="MAIN_COMPUTER_HUB_PORT")
    return None


def env_chain_rpc_url_override() -> str | None:
    value = os.environ.get("MAIN_COMPUTER_CHAIN_RPC_URL") or os.environ.get("MAIN_COMPUTER_HUB_CHAIN_RPC_URL")
    if value and value.strip():
        return value.strip()
    return None


def env_chain_id_override() -> int | None:
    value = os.environ.get("MAIN_COMPUTER_CHAIN_ID") or os.environ.get("MAIN_COMPUTER_HUB_CHAIN_ID")
    if value and value.strip():
        return _parse_int(value, field="MAIN_COMPUTER_CHAIN_ID", required=False)
    return None


def env_hub_runtime_dir_override() -> Path | None:
    value = os.environ.get("MAIN_COMPUTER_HUB_RUNTIME_DIR")
    if value and value.strip():
        return Path(value.strip())
    return None
