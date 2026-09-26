from __future__ import annotations

import re
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


def resolve_public_url(
    ctx: HubContext,
    private: Mapping[str, Any],
    *,
    network: str,
    hub_id: str,
    accepted_hubs: list[dict[str, Any]],
) -> str:
    net = network_doc(private, network)
    hub = net.get("hub")
    instances = hub.get("instances") if isinstance(hub, Mapping) else None
    item = instances.get(hub_id) if isinstance(instances, Mapping) else None
    if isinstance(item, Mapping):
        for key in ("public_url", "hub_url"):
            value = str(item.get(key) or "").strip()
            if value:
                return value.rstrip("/")

    # During the first-Hub transition the checked-in network registry is the
    # migration fallback for the single public entry URL. Later expansion must
    # have an identity-specific public URL rather than silently duplicating it.
    profile = load_hub_network_registry(ctx.repo_root / "main_computer" / "config" / "hub_networks.json").get(network)
    fallback = str(profile.hub_url or "").strip().rstrip("/")
    if not accepted_hubs and fallback:
        return fallback
    raise HubControlError(
        "HUB_PUBLIC_URL_MISSING",
        f"no authoritative public URL is available for {hub_id!r}; add networks.{network}.hub.instances.{hub_id}.public_url before expanding Hub membership",
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
