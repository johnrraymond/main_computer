"""Read-only Hub destination resolution for the Viewport's Worker/requester boundary.

The Worker settings file owns network selection.  This module never persists
settings, starts a Hub, chooses a fallback network, or authorizes payment.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from main_computer.dev_test_hub import validate_dev_test_route
from main_computer.hub_networks import HubNetworkRegistry, load_hub_network_registry


class HubInterfaceError(ValueError):
    """The selected Worker network cannot safely serve the requested operation."""


@dataclass(frozen=True)
class HubDestination:
    network: str
    hub_url: str
    protocol: str
    hub_kind: str

    @property
    def legacy_overflow_supported(self) -> bool:
        return self.network in {"dev", "test"}

    def diagnostics(self) -> dict[str, str | bool]:
        return {"selected_network": self.network, "hub_url": self.hub_url,
                "hub_kind": self.hub_kind, "protocol": self.protocol,
                "legacy_overflow_supported": self.legacy_overflow_supported}


def selected_worker_network(settings: Mapping[str, Any]) -> str:
    """Match the Worker supervisor's sanitized effective auto-connect selection.

    A missing selection is 'none', never the hub registry's mainnet default.
    The Worker settings sanitizer treats an explicit non-'none' auto-connect
    setting as authoritative over an older selectedNetwork value.
    """
    selected = str(settings.get("selectedNetwork", settings.get("selected_network", "none")) or "none").strip().lower()
    auto = settings.get("workerAutoConnectNetwork", settings.get("worker_auto_connect_network"))
    if auto is not None and str(auto).strip().lower() not in {"", "none"}:
        selected = str(auto).strip().lower()
    if selected not in {"none", "slh", "dev", "test", "testnet", "mainnet"}:
        raise HubInterfaceError(f"Invalid selected Worker network: {selected!r}.")
    return selected


def resolve_hub_destination(
    settings: Mapping[str, Any], *, registry: HubNetworkRegistry | None = None,
) -> HubDestination:
    """Resolve a destination from saved Worker selection, not request payloads.

    The registry supplies the canonical URL for each network.  Stale Worker
    registration URLs and cached MSKs do not override network selection.
    """
    network = selected_worker_network(settings)
    if network == "none":
        return HubDestination("none", "", "none", "none")
    if network == "slh":
        url = str(settings.get("managedSLHUrl") or "").strip().rstrip("/")
        return HubDestination("slh", url, "managed-slh", "secure-local-hub")
    registry = registry or load_hub_network_registry()
    try:
        profile = registry.get(network)
    except (KeyError, ValueError) as exc:
        raise HubInterfaceError(f"Selected Worker network {network!r} has no Hub profile.") from exc
    url = str(profile.hub_url or "").strip().rstrip("/")
    if not url:
        raise HubInterfaceError(f"Selected Worker network {network!r} has no Hub URL.")
    if network in {"dev", "test"}:
        validate_dev_test_route(url)
        return HubDestination(network, url, "dev-test", "dev-test-hub")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise HubInterfaceError(f"Selected {network} Stable Hub must have a valid HTTPS endpoint.")
    return HubDestination(network, url, "stable-hub-paid", "stable-hub")


def require_legacy_overflow(destination: HubDestination) -> None:
    """Never route the Dev-Test safe-chat simulation to a production Hub."""
    if not destination.legacy_overflow_supported:
        raise HubInterfaceError(
            f"The Dev-Test safe-chat endpoint does not execute real requester work on "
            f"selected network {destination.network!r}. No Hub fallback or simulated paid result was used."
        )
