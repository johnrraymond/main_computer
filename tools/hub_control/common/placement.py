from __future__ import annotations

import re
import urllib.parse
from typing import Any, Mapping

from main_computer.hub_networks import load_hub_network_registry

from .errors import HubControlError
from .models import HubContext, HubPlacement
from .privates import network_doc, resolve_controller

_HUB_RE = re.compile(r"^(?P<placement>[a-z])-hub(?P<ordinal>[1-9][0-9]*)$")


def parse_hub_identity(network: str, hub_id: str) -> tuple[str, int]:
    network = str(network or "").strip()
    hub_id = str(hub_id or "").strip()
    if not network or not hub_id.startswith(network):
        raise HubControlError("HUB_ID_NETWORK_MISMATCH", f"Hub {hub_id!r} does not belong to network {network!r}")
    suffix = hub_id[len(network):]
    match = _HUB_RE.fullmatch(suffix)
    if match is None:
        raise HubControlError(
            "HUB_ID_INVALID",
            f"Hub {hub_id!r} must be named like {network}<placement>-hub<N>; for example {network}c-hub1",
        )
    return str(match.group("placement")), int(match.group("ordinal"))


def _normalize_public_url(value: object) -> str:
    return str(value or "").strip().rstrip("/")


def _derive_enumerated_hub_url(network: str, hub_id: str, ingress_url: str) -> str:
    """Derive an enumerated Hub identity from the network ingress hostname.

    Public network profiles intentionally advertise a stable ingress alias such
    as ``https://mainnet-hub.greatlibrary.io``.  That alias is for initial
    discovery/admission only; it is not the identity of an individual Hub.
    Enumerated Hubs use their Hub id as the first DNS label, for example
    ``https://mainnetc-hub1.greatlibrary.io``.

    If the ingress URL does not have the canonical ``<network>-hub.<domain>``
    shape, inference is deliberately refused and an explicit per-Hub override
    is required instead.
    """
    ingress_url = _normalize_public_url(ingress_url)
    if not ingress_url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(ingress_url)
        hostname = str(parsed.hostname or "").strip().lower()
        port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not hostname:
        return ""
    labels = hostname.split(".")
    if len(labels) < 2 or labels[0] != f"{network}-hub".lower():
        return ""
    netloc = f"{hub_id}.{'.'.join(labels[1:])}"
    if port is not None:
        netloc += f":{port}"
    path = str(parsed.path or "").rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme, netloc, path, "", "")).rstrip("/")


def _validate_instance_public_url(
    *,
    network: str,
    hub_id: str,
    public_url: str,
    ingress_url: str,
    accepted_hubs: list[dict[str, Any]],
) -> str:
    public_url = _normalize_public_url(public_url)
    ingress_url = _normalize_public_url(ingress_url)
    if ingress_url and public_url == ingress_url:
        raise HubControlError(
            "HUB_PUBLIC_URL_IS_NETWORK_INGRESS",
            f"Hub {hub_id!r} resolves to network ingress {ingress_url!r}; "
            "enumerated Hubs must have identity-specific public URLs",
        )
    for item in accepted_hubs:
        if not isinstance(item, Mapping):
            continue
        other_id = str(item.get("hub_id") or "").strip()
        other_url = _normalize_public_url(item.get("public_url") or item.get("hub_url"))
        if other_id and other_id != hub_id and other_url and public_url == other_url:
            raise HubControlError(
                "HUB_PUBLIC_URL_DUPLICATE",
                f"Hub {hub_id!r} resolves to {public_url!r}, which is already assigned to accepted Hub {other_id!r}",
            )
    return public_url


def resolve_public_url(
    ctx: HubContext,
    private: Mapping[str, Any],
    *,
    network: str,
    hub_id: str,
    accepted_hubs: list[dict[str, Any]],
) -> str:
    profile = load_hub_network_registry(ctx.repo_root / "main_computer" / "config" / "hub_networks.json").get(network)
    ingress_url = _normalize_public_url(profile.hub_url)

    # Explicit private state remains authoritative for exceptional placements,
    # but it may never alias an enumerated Hub onto the network ingress URL.
    net = network_doc(private, network)
    hub = net.get("hub")
    instances = hub.get("instances") if isinstance(hub, Mapping) else None
    item = instances.get(hub_id) if isinstance(instances, Mapping) else None
    if isinstance(item, Mapping):
        for key in ("public_url", "hub_url"):
            value = _normalize_public_url(item.get(key))
            if value:
                return _validate_instance_public_url(
                    network=network,
                    hub_id=hub_id,
                    public_url=value,
                    ingress_url=ingress_url,
                    accepted_hubs=accepted_hubs,
                )

    derived = _derive_enumerated_hub_url(network, hub_id, ingress_url)
    if derived:
        return _validate_instance_public_url(
            network=network,
            hub_id=hub_id,
            public_url=derived,
            ingress_url=ingress_url,
            accepted_hubs=accepted_hubs,
        )

    raise HubControlError(
        "HUB_PUBLIC_URL_MISSING",
        f"no authoritative enumerated public URL is available for {hub_id!r}; "
        f"configure a canonical {network}-hub network ingress or add "
        f"networks.{network}.hub.instances.{hub_id}.public_url",
    )


def infer_hub_placement(
    ctx: HubContext,
    private: Mapping[str, Any],
    *,
    network: str,
    hub_id: str,
    accepted_hubs: list[dict[str, Any]],
) -> tuple[HubPlacement, dict[str, Any]]:
    token, ordinal = parse_hub_identity(network, hub_id)
    controller_id, _raw = resolve_controller(private, network, token)
    public_url = resolve_public_url(ctx, private, network=network, hub_id=hub_id, accepted_hubs=accepted_hubs)
    runtime_dir = f"/data/main-computer/hub/{hub_id}"
    placement = HubPlacement(
        hub_id=hub_id,
        controller_id=controller_id,
        host_id=controller_id,
        public_url=public_url,
        runtime_dir=runtime_dir,
        cluster_file_path=f"{runtime_dir}/fdb.cluster",
        topology_path=f"{runtime_dir}/hub-topology.json",
        application_name=f"main-computer-{hub_id}",
    )
    return placement, {
        "placement_token": token,
        "hub_ordinal": ordinal,
        "controller_id": controller_id,
        "host_id": controller_id,
        "public_url": public_url,
        "runtime_dir": runtime_dir,
        "cluster_file_path": placement.cluster_file_path,
        "topology_path": placement.topology_path,
        "application_name": placement.application_name,
    }
