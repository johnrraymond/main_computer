"""Configuration and explicit connection modes for the future SLH UI.

Profiles from hub_networks are reused; never substitute a guessed public
network address.  This layer intentionally does not open any LAN listener.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import base64
import ipaddress
import re
from pathlib import Path
from urllib.parse import urlsplit

from main_computer.hub_networks import load_hub_network_registry

MARKET_NETWORKS = ("mainnet", "testnet", "test", "dev")
_DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
_IMAGE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9./:_-]*\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class SLHConfigError(ValueError):
    pass


def public_https_url(raw: str, *, allow_local_http: bool = False) -> str:
    u = urlsplit(raw)
    if u.scheme not in ({"https", "http"} if allow_local_http else {"https"}):
        raise SLHConfigError("External endpoint requires HTTPS")
    if u.username or u.password or not u.hostname or u.query or u.fragment or u.path not in ("", "/"):
        raise SLHConfigError("External endpoint must be an origin without secrets, paths or query")
    if u.scheme == "http" and (not allow_local_http or u.hostname not in {"localhost", "127.0.0.1", "::1"}):
        raise SLHConfigError("HTTP is permitted for loopback lab only")
    if u.hostname in {"localhost", "127.0.0.1", "::1"} and not allow_local_http:
        raise SLHConfigError("Public endpoint must not be loopback")
    try:
        ip = ipaddress.ip_address(u.hostname)
        if not allow_local_http and (ip.is_private or ip.is_loopback or ip.is_link_local):
            raise SLHConfigError("Public endpoint must not be a private address")
    except ValueError as exc:
        if isinstance(exc, SLHConfigError):
            raise
    return raw.rstrip("/")


@dataclass(frozen=True)
class ConnectionProfile:
    key: str
    route: str  # marketplace | local_slh | external_seh
    url: str | None
    client_wallet_required: bool
    slh_managed_marketplace_wallet: bool
    device_trust_required: bool
    status: str  # configured | disabled | unimplemented

    def to_dict(self) -> dict:
        return asdict(self)


def load_connection_profiles(
    *,
    network_registry_path: Path | None = None,
    slh_local_url: str | None = None,
    seh_public_url: str | None = None,
) -> list[ConnectionProfile]:
    # Preserve current deployment registry selection and its base-url metadata.
    if network_registry_path is None:
        registry = load_hub_network_registry()
    else:
        registry = load_hub_network_registry(network_registry_path)
    profiles = [
        ConnectionProfile("slh", "local_slh", slh_local_url, False, True, True, "unimplemented"),
        ConnectionProfile("seh", "external_seh", public_https_url(seh_public_url) if seh_public_url else None,
                          False, True, True, "unimplemented"),
    ]
    for name in MARKET_NETWORKS:
        hub = registry.networks.get(name)
        # A registry may intentionally omit a network on a development host.
        profiles.append(ConnectionProfile(name, "marketplace", hub.hub_url if hub else None,
                                          True, False, False, "configured" if hub else "disabled"))
    return profiles


@dataclass(frozen=True)
class SEHTarget:
    """Deploy-only configuration. Never put a Coolify token or SLH secrets here."""
    target_id: str
    organization_id: str
    slh_id: str
    public_url: str
    coolify_url: str
    project_uuid: str
    environment_uuid: str
    server_uuid: str
    image: str
    image_digest: str
    service_uuid: str | None = None
    internal_port: int = 8080
    local_lab: bool = False
    slh_public_key_b64: str = ""  # Public enrollment identity; no private key on SEH

    @classmethod
    def from_dict(cls, data: dict) -> "SEHTarget":
        if not isinstance(data, dict):
            raise SLHConfigError("Target must be a JSON object")
        allowed = set(cls.__dataclass_fields__)
        if set(data) - allowed:
            raise SLHConfigError("Unexpected target fields (no secrets permitted): " + ", ".join(sorted(set(data)-allowed)))
        try:
            t = cls(**data)
        except TypeError as e:
            raise SLHConfigError(f"Missing/invalid target fields: {e}") from e
        for name in ("target_id", "organization_id", "slh_id", "project_uuid", "environment_uuid", "server_uuid"):
            if not _ID.fullmatch(str(getattr(t, name))):
                raise SLHConfigError(f"Invalid {name}")
        if t.service_uuid is not None and not _ID.fullmatch(str(t.service_uuid)):
            raise SLHConfigError("Invalid service_uuid")
        if not _IMAGE.fullmatch(t.image) or '@' in t.image or not _DIGEST.fullmatch(t.image_digest):
            raise SLHConfigError("SEH image must have a separate pinned sha256 digest")
        if not isinstance(t.internal_port, int) or not 1024 <= t.internal_port <= 65535:
            raise SLHConfigError("Invalid internal port")
        if t.slh_public_key_b64:
            from .authenticator import load_public_key
            try:
                material = base64.b64decode(t.slh_public_key_b64,validate=True).decode('ascii')
                load_public_key(material)
            except (ValueError, UnicodeError) as exc:
                raise SLHConfigError('Invalid SEH SLH public signing key') from exc
        public_https_url(t.public_url)
        public_https_url(t.coolify_url, allow_local_http=t.local_lab)
        if t.public_url == t.coolify_url:
            raise SLHConfigError("Public relay and Coolify management origins must be distinct")
        return t

    def to_dict(self) -> dict:
        return asdict(self)
