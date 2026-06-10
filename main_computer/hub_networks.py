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


def _parse_optional_path(value: object, *, field: str, required: bool = True) -> Path | None:
    text = _clean_text(value, field=field, required=required)
    if text is None:
        return None
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


def _default_hub_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _first_present(payload: dict[str, object], *keys: str, default: object = None) -> object:
    for key in keys:
        if key in payload:
            return payload.get(key)
    return default


@dataclass(frozen=True)
class HubNetworkProfile:
    network_key: str
    display_name: str
    kind: str
    chain_id: int | None
    chain_rpc_url: str | None
    hub_bind_host: str
    hub_bind_port: int
    hub_public_url: str | None
    hub_runtime_dir: Path
    deployment_manifest_path: Path | None
    native_currency: dict[str, Any]
    contracts: dict[str, str]
    source_path: Path

    @property
    def hub_host(self) -> str:
        """Backward-compatible alias for older callers."""

        return self.hub_bind_host

    @property
    def hub_port(self) -> int:
        """Backward-compatible alias for older callers."""

        return self.hub_bind_port

    @property
    def hub_url(self) -> str:
        return self.hub_public_url or _default_hub_url(self.hub_bind_host, self.hub_bind_port)

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

        bind_host = _clean_text(
            _first_present(payload, "hub_bind_host", "hub_host", default="127.0.0.1"),
            field=f"networks.{network_key}.hub_bind_host",
        )
        assert bind_host is not None
        bind_port = _parse_port(
            _first_present(payload, "hub_bind_port", "hub_port"),
            field=f"networks.{network_key}.hub_bind_port",
        )
        hub_public_url = _clean_text(
            payload.get("hub_public_url"),
            field=f"networks.{network_key}.hub_public_url",
            required=False,
        )
        if hub_public_url is None and ("hub_host" in payload or "hub_port" in payload):
            hub_public_url = _default_hub_url(bind_host, bind_port)

        hub_runtime_dir = _parse_optional_path(
            payload.get("hub_runtime_dir"),
            field=f"networks.{network_key}.hub_runtime_dir",
        )
        assert hub_runtime_dir is not None
        deployment_path = _parse_optional_path(
            payload.get("deployment_manifest_path"),
            field=f"networks.{network_key}.deployment_manifest_path",
            required=False,
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
            hub_bind_host=bind_host or "127.0.0.1",
            hub_bind_port=bind_port,
            hub_public_url=hub_public_url,
            hub_runtime_dir=hub_runtime_dir,
            deployment_manifest_path=deployment_path,
            native_currency=dict(native_currency),
            contracts=_normalize_contracts(payload.get("contracts")),
            source_path=source_path,
        )

    def with_overrides(
        self,
        *,
        hub_host: str | None = None,
        hub_port: int | str | None = None,
        hub_bind_host: str | None = None,
        hub_bind_port: int | str | None = None,
        hub_public_url: str | None = None,
        hub_runtime_dir: Path | str | None = None,
        chain_rpc_url: str | None = None,
        chain_id: int | str | object = _MISSING,
    ) -> "HubNetworkProfile":
        updates: dict[str, object] = {}
        old_default_url = _default_hub_url(self.hub_bind_host, self.hub_bind_port)

        chosen_host = hub_bind_host if hub_bind_host is not None else hub_host
        chosen_port = hub_bind_port if hub_bind_port is not None else hub_port
        if chosen_host is not None and str(chosen_host).strip():
            updates["hub_bind_host"] = str(chosen_host).strip()
        if chosen_port is not None:
            updates["hub_bind_port"] = _parse_port(chosen_port, field="hub_bind_port override")
        if hub_runtime_dir is not None and str(hub_runtime_dir).strip():
            updates["hub_runtime_dir"] = Path(str(hub_runtime_dir).strip())
        if chain_rpc_url is not None and str(chain_rpc_url).strip():
            updates["chain_rpc_url"] = str(chain_rpc_url).strip()
        if chain_id is not _MISSING:
            updates["chain_id"] = _parse_int(chain_id, field="chain_id override", required=False)

        if hub_public_url is not None and str(hub_public_url).strip():
            updates["hub_public_url"] = str(hub_public_url).strip()
        elif any(key in updates for key in ("hub_bind_host", "hub_bind_port")) and (
            self.hub_public_url is None or self.hub_public_url == old_default_url
        ):
            new_host = str(updates.get("hub_bind_host", self.hub_bind_host))
            new_port = int(updates.get("hub_bind_port", self.hub_bind_port))
            updates["hub_public_url"] = _default_hub_url(new_host, new_port)

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
            "hub_bind_host": self.hub_bind_host,
            "hub_bind_port": self.hub_bind_port,
            "hub_public_url": self.hub_public_url,
            "hub_host": self.hub_bind_host,
            "hub_port": self.hub_bind_port,
            "hub_url": self.hub_url,
            "hub_runtime_dir": str(self.hub_runtime_dir),
            "deployment_manifest_path": str(self.deployment_manifest_path) if self.deployment_manifest_path else None,
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


def deployment_manifest_path(network_key: str, *, repo_root: str | Path | None = None) -> Path:
    """Return the network-scoped deployment manifest path for runtime defaults."""

    root = Path(repo_root) if repo_root is not None else Path.cwd()
    return root / "runtime" / "deployments" / network_key / "latest.json"


def _resolve_profile_deployment_manifest_path(
    profile: HubNetworkProfile,
    *,
    repo_root: str | Path | None = None,
) -> Path:
    if profile.deployment_manifest_path is None:
        return deployment_manifest_path(profile.network_key, repo_root=repo_root)
    path = profile.deployment_manifest_path
    if path.is_absolute():
        return path
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    return root / path


def load_network_deployment_manifest(
    network_key: str,
    *,
    repo_root: str | Path | None = None,
    manifest_path: str | Path | None = None,
    required: bool = False,
) -> tuple[Path, dict[str, Any]] | None:
    """Load a network-scoped deployment manifest if it exists.

    The source registry owns stable network identity.  The runtime deployment
    manifest owns deploy-time values such as the actual RPC URL for a Coolify
    surface.  This loader deliberately does not fall back to current.json because
    current.json is an active pointer and can refer to a different network.
    """

    path = Path(manifest_path) if manifest_path is not None and str(manifest_path).strip() else deployment_manifest_path(network_key, repo_root=repo_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise HubNetworkConfigError(f"Hub network deployment manifest not found: {path}") from None
        return None
    except json.JSONDecodeError as exc:
        raise HubNetworkConfigError(f"Hub network deployment manifest is not valid JSON: {path}: {exc}") from None
    if not isinstance(payload, dict):
        raise HubNetworkConfigError(f"Hub network deployment manifest root is not an object: {path}")
    return path, payload


def _manifest_chain_values(manifest: dict[str, Any], *, manifest_path: Path | None = None) -> tuple[int | None, str | None]:
    label = str(manifest_path or "deployment manifest")
    if str(manifest.get("schema")) != "main-computer.deployment.v1":
        raise HubNetworkConfigError(f"Hub network deployment manifest has unexpected schema: {label}")
    chain = manifest.get("chain")
    if not isinstance(chain, dict):
        raise HubNetworkConfigError(f"Hub network deployment manifest is missing chain object: {label}")
    chain_id = _parse_int(chain.get("chain_id"), field=f"{label}.chain.chain_id", required=False)
    rpc_url = _clean_text(chain.get("rpc_url") or chain.get("host_rpc_url"), field=f"{label}.chain.rpc_url", required=False)
    return chain_id, rpc_url


def profile_with_deployment_manifest_defaults(
    profile: HubNetworkProfile,
    manifest: dict[str, Any],
    *,
    manifest_path: str | Path | None = None,
) -> HubNetworkProfile:
    """Fill missing runnable fields from a matching deployment manifest.

    A concrete source-registry value remains authoritative.  If both the profile
    and manifest specify chain_id or chain_rpc_url, they must agree.  If the
    profile intentionally leaves a field blank, the manifest may supply it.
    """

    path = Path(manifest_path) if manifest_path is not None and str(manifest_path).strip() else None
    manifest_environment = str(manifest.get("environment") or "").strip()
    if manifest_environment != profile.network_key:
        label = f": {path}" if path else ""
        raise HubNetworkConfigError(
            f"Hub network deployment manifest environment {manifest_environment!r} does not match "
            f"selected network {profile.network_key!r}{label}"
        )

    manifest_chain_id, manifest_rpc_url = _manifest_chain_values(manifest, manifest_path=path)

    if profile.chain_id is not None and manifest_chain_id is not None and int(profile.chain_id) != int(manifest_chain_id):
        label = f" in {path}" if path else ""
        raise HubNetworkConfigError(
            f"Hub network deployment manifest chain_id {manifest_chain_id} does not match "
            f"selected network {profile.network_key!r} chain_id {profile.chain_id}{label}"
        )
    if profile.chain_rpc_url and manifest_rpc_url and str(profile.chain_rpc_url) != str(manifest_rpc_url):
        label = f" in {path}" if path else ""
        raise HubNetworkConfigError(
            f"Hub network deployment manifest RPC URL {manifest_rpc_url!r} does not match "
            f"selected network {profile.network_key!r} RPC URL {profile.chain_rpc_url!r}{label}"
        )

    return profile.with_overrides(
        chain_id=profile.chain_id if profile.chain_id is not None else manifest_chain_id,
        chain_rpc_url=profile.chain_rpc_url or manifest_rpc_url,
    )


def resolve_profile_runtime_defaults(
    profile: HubNetworkProfile,
    *,
    repo_root: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> HubNetworkProfile:
    """Fill missing runnable profile values from its deployment manifest.

    Local profiles such as dev/test already have concrete loopback RPC values and
    are not affected by stale runtime files.  Dynamic profiles can still fill
    missing values from their network-scoped manifest path.
    """

    if profile.chain_id is not None and profile.chain_rpc_url and not manifest_path:
        return profile

    path = (
        Path(manifest_path)
        if manifest_path is not None and str(manifest_path).strip()
        else _resolve_profile_deployment_manifest_path(profile, repo_root=repo_root)
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if manifest_path:
            raise HubNetworkConfigError(f"Hub network deployment manifest not found: {path}") from None
        return profile
    except json.JSONDecodeError as exc:
        raise HubNetworkConfigError(f"Hub network deployment manifest is not valid JSON: {path}: {exc}") from None
    if not isinstance(payload, dict):
        raise HubNetworkConfigError(f"Hub network deployment manifest root is not an object: {path}")
    return profile_with_deployment_manifest_defaults(profile, payload, manifest_path=path)


def env_hub_network_name() -> str | None:
    value = os.environ.get("MAIN_COMPUTER_HUB_NETWORK") or os.environ.get("MAIN_COMPUTER_NETWORK")
    if value and value.strip():
        return value.strip()
    return None


def env_hub_host_override() -> str | None:
    value = os.environ.get("MAIN_COMPUTER_HUB_BIND_HOST") or os.environ.get("MAIN_COMPUTER_HUB_HOST")
    if value and value.strip():
        return value.strip()
    return None


def env_hub_port_override() -> int | None:
    value = os.environ.get("MAIN_COMPUTER_HUB_BIND_PORT") or os.environ.get("MAIN_COMPUTER_HUB_PORT")
    if value and value.strip():
        return _parse_port(value, field="MAIN_COMPUTER_HUB_BIND_PORT")
    return None


def env_hub_public_url_override() -> str | None:
    value = os.environ.get("MAIN_COMPUTER_HUB_PUBLIC_URL")
    if value and value.strip():
        return value.strip()
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
