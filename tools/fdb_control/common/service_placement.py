from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .errors import FdbControlError
from .models import AcceptedClusterState, ServicePlacement
from .privates import resolve_host_fdb_address

_DEFAULT_FDB_PORT = 4550
_SERVICE_SUFFIX_RE = re.compile(r"^(?P<placement>[a-z])?-fdb(?P<ordinal>[1-9][0-9]*)$")


@dataclass(frozen=True, slots=True)
class ParsedServiceIdentity:
    network: str
    service_id: str
    placement_token: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class ResolvedPlacementHost:
    controller_id: str
    host_id: str
    source: str


def parse_service_identity(network: str, service_id: str) -> ParsedServiceIdentity:
    wanted_network = str(network or "").strip()
    wanted_service = str(service_id or "").strip()
    if not wanted_network or not wanted_service:
        raise ValueError("network and service_id must be non-empty")
    if not wanted_service.startswith(wanted_network):
        raise FdbControlError(
            code="FDB_SERVICE_NETWORK_MISMATCH",
            message=f"service {wanted_service!r} does not belong to network {wanted_network!r}",
            module_id="FDB-OFM-DEPLOY-002",
            retry_class="never",
        )
    suffix = wanted_service[len(wanted_network):]
    match = _SERVICE_SUFFIX_RE.fullmatch(suffix)
    if match is None:
        raise FdbControlError(
            code="FDB_SERVICE_ID_INVALID",
            message=(
                f"service {wanted_service!r} must be named like {wanted_network}<placement>-fdb<N>; "
                f"for example {wanted_network}c-fdb3"
            ),
            module_id="FDB-OFM-DEPLOY-002",
            retry_class="never",
        )
    placement = str(match.group("placement") or "").strip()
    if not placement:
        raise FdbControlError(
            code="FDB_SERVICE_PLACEMENT_REQUIRED",
            message=(
                f"service {wanted_service!r} has no placement token; automatic placement requires "
                f"a name such as {wanted_network}c-fdb3"
            ),
            module_id="FDB-OFM-DEPLOY-002",
            retry_class="never",
        )
    return ParsedServiceIdentity(
        network=wanted_network,
        service_id=wanted_service,
        placement_token=placement,
        ordinal=int(match.group("ordinal")),
    )


def _controllers(private_doc: Mapping[str, Any], network: str) -> Mapping[str, Any]:
    networks = private_doc.get("networks")
    network_doc = networks.get(network) if isinstance(networks, Mapping) else None
    coolify = network_doc.get("coolify") if isinstance(network_doc, Mapping) else None
    controllers = coolify.get("controllers") if isinstance(coolify, Mapping) else None
    if not isinstance(controllers, Mapping):
        raise FdbControlError(
            code="FDB_PRIVATE_CONTROLLERS_MISSING",
            message=f"shared private infrastructure has no networks.{network}.coolify.controllers mapping",
            module_id="FDB-OFM-PRIV-002",
            retry_class="never",
        )
    return controllers


def resolve_host_for_placement(
    private_doc: Mapping[str, Any],
    network: str,
    placement_token: str,
) -> ResolvedPlacementHost:
    """Resolve a logical FDB placement token from Mother's shared identity state.

    ``mainnetc-fdb3`` encodes placement token ``c``.  That token resolves to
    the logical Coolify controller ``coolify-c`` under
    ``networks.mainnet.coolify.controllers`` in runtime/state/mother/identity.private.yaml.  No
    physical A/B inventory bridge or deployment-controller side file is used.
    """

    token = str(placement_token or "").strip().lower()
    if not token:
        raise ValueError("placement_token must be non-empty")
    controller_id = f"coolify-{token}"
    controllers = _controllers(private_doc, network)
    raw = controllers.get(controller_id)
    if not isinstance(raw, Mapping):
        known = sorted(
            (str(key) for key, value in controllers.items() if isinstance(value, Mapping)),
            key=lambda item: item.encode("utf-8"),
        )
        raise FdbControlError(
            code="FDB_SERVICE_PLACEMENT_UNKNOWN",
            message=(
                f"placement token {token!r} implies logical controller {controller_id!r}, but "
                f"networks.{network}.coolify.controllers does not contain it; known={known!r}"
            ),
            module_id="FDB-OFM-DEPLOY-002",
            retry_class="never",
        )
    return ResolvedPlacementHost(
        controller_id=controller_id,
        host_id=controller_id,
        source=f"runtime/state/mother/identity.private.yaml:networks.{network}.coolify.controllers.{controller_id}",
    )


def resolve_host_id_for_placement(
    private_doc: Mapping[str, Any],
    network: str,
    placement_token: str,
) -> str:
    return resolve_host_for_placement(private_doc, network, placement_token).host_id


def used_fdb_ports(accepted: AcceptedClusterState, host_id: str) -> set[int]:
    wanted = str(host_id or "").strip()
    return {item.port for item in accepted.services if item.host_id == wanted}


def allocate_fdb_port(
    accepted: AcceptedClusterState,
    host_id: str,
    *,
    default_port: int = _DEFAULT_FDB_PORT,
) -> int:
    if isinstance(default_port, bool) or not isinstance(default_port, int) or not 1 <= default_port <= 65535:
        raise ValueError("default_port must be an integer in 1..65535")
    used = used_fdb_ports(accepted, host_id)
    candidate = default_port
    while candidate in used:
        candidate += 1
        if candidate > 65535:
            raise FdbControlError(
                code="FDB_SERVICE_PORTS_EXHAUSTED",
                message=f"no free FDB service port remains on host {host_id!r}",
                module_id="FDB-OFM-NET-001",
                retry_class="never",
            )
    return candidate


def infer_service_placement(
    private_doc: Mapping[str, Any],
    accepted: AcceptedClusterState,
    *,
    network: str,
    service_id: str,
    default_port: int = _DEFAULT_FDB_PORT,
) -> tuple[ServicePlacement, dict[str, Any]]:
    parsed = parse_service_identity(network, service_id)
    if accepted.network != parsed.network:
        raise FdbControlError(
            code="FDB_SERVICE_NETWORK_MISMATCH",
            message=f"accepted cluster network {accepted.network!r} does not match requested network {parsed.network!r}",
            module_id="FDB-OFM-DEPLOY-002",
            retry_class="never",
        )
    resolved_host = resolve_host_for_placement(
        private_doc,
        parsed.network,
        parsed.placement_token,
    )
    address, address_source = resolve_host_fdb_address(
        private_doc,
        parsed.network,
        resolved_host.host_id,
    )
    port = allocate_fdb_port(accepted, resolved_host.host_id, default_port=default_port)
    placement = ServicePlacement(
        service_id=parsed.service_id,
        host_id=resolved_host.host_id,
        address=address,
        port=port,
        machine_id=resolved_host.host_id,
        zone_id=resolved_host.host_id,
    )
    return placement, {
        "placement_token": parsed.placement_token,
        "service_ordinal": parsed.ordinal,
        "controller_id": resolved_host.controller_id,
        "host_id": resolved_host.host_id,
        "host_resolution_source": resolved_host.source,
        "address": address,
        "address_source": address_source,
        "port": port,
        "endpoint": placement.endpoint,
    }
