"""Validator P2P route selection helpers for Mother deployment flows.

The route recorded in Mother evidence is the externally reachable validator
peer endpoint: a controller/host VPN address plus a unique host-level P2P port.
Besu RPC routes are intentionally outside this helper.
"""

from __future__ import annotations

from collections.abc import Mapping
import ipaddress
import json
import re
import urllib.parse
from typing import Any


_DEFAULT_P2P_PORT = 30303
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class MotherDeploymentValidatorRouteError(RuntimeError):
    """Validator route derivation failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentValidatorRouteError:
    return MotherDeploymentValidatorRouteError(code, message)


def _document(private_state: Any) -> Mapping[str, Any]:
    try:
        value = json.loads(private_state.canonical_object_bytes.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_VALIDATOR_ROUTE_PRIVATE_STATE_INVALID", "Mother private state is not canonical JSON") from exc
    if not isinstance(value, Mapping):
        raise _fail("MOTHER_DEPLOY_VALIDATOR_ROUTE_PRIVATE_STATE_INVALID", "Mother private state is not a mapping")
    return value


def _network(private_state: Any, network: str) -> Mapping[str, Any]:
    document = _document(private_state)
    networks = document.get("networks")
    item = networks.get(network) if isinstance(networks, Mapping) else None
    if not isinstance(item, Mapping):
        raise _fail("MOTHER_DEPLOY_VALIDATOR_ROUTE_PRIVATE_STATE_INVALID", f"network {network!r} is missing from Mother private state")
    return item


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_VALIDATOR_ROUTE_INVALID", f"{label} is not a valid identifier")
    return value


def _host_from_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urllib.parse.urlparse(value.strip())
    return parsed.hostname or None


def _valid_host(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or any(ch.isspace() for ch in text) or "/" in text or "\\" in text or "\x00" in text:
        return None
    return text


def _unusable_advertised_host(value: str) -> bool:
    text = value.strip().strip("[]")
    lower = text.rstrip(".").lower()
    if lower in {"", "localhost", "0.0.0.0", "::", "::1"} or lower.startswith("127."):
        return True
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        return False
    return parsed.is_loopback or parsed.is_unspecified


def validator_route_advertised_host(route: Mapping[str, Any]) -> str | None:
    """Return the route host safe to advertise in Besu's P2P enode.

    Besu defaults to 127.0.0.1 when --p2p-host is omitted.  Added nodes
    must fail closed instead of producing loopback or wildcard enodes.
    """

    for key in ("advertised_host", "vpn_ip", "p2p_host", "host"):
        candidate = _valid_host(route.get(key))
        if candidate is not None and not _unusable_advertised_host(candidate):
            return candidate

    for key in ("p2p_endpoint", "endpoint", "enode"):
        parsed = _endpoint_host_port(route.get(key))
        if parsed is None:
            continue
        candidate, _port = parsed
        if not _unusable_advertised_host(candidate):
            return candidate
    return None


def _controller_record(network_doc: Mapping[str, Any], controller_id: str) -> Mapping[str, Any]:
    coolify = network_doc.get("coolify")
    controllers = coolify.get("controllers") if isinstance(coolify, Mapping) else None
    record = controllers.get(controller_id) if isinstance(controllers, Mapping) else None
    if not isinstance(record, Mapping):
        raise _fail("MOTHER_DEPLOY_VALIDATOR_ROUTE_CONTROLLER_MISSING", f"controller {controller_id!r} is missing from Mother private state")
    return record


def _iter_named_records(value: Any) -> list[tuple[str, Mapping[str, Any]]]:
    records: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and isinstance(item, Mapping):
                records.append((key, item))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, Mapping):
                name = item.get("name") or item.get("controller_id") or item.get("id")
                if isinstance(name, str):
                    records.append((name, item))
    return records


def controller_validator_host(private_state: Any, *, network: str, controller_id: str) -> tuple[str, str]:
    """Return the preferred advertised host for validator P2P on a controller.

    The preferred source is a configured VPN address.  A Coolify controller URL
    hostname is only a compatibility fallback for older test/private-state
    fixtures that do not yet carry VPN metadata.
    """

    controller = _identifier(controller_id, "controller_id")
    network_doc = _network(private_state, network)
    controller_record = _controller_record(network_doc, controller)

    for key in ("validator_vpn_ip", "vpn_ip", "private_vpn_ip", "wireguard_ip", "tailscale_ip"):
        candidate = _valid_host(controller_record.get(key))
        if candidate:
            return candidate, f"networks.{network}.coolify.controllers.{controller}.{key}"

    # Some generated placement/import paths keep host VPN metadata outside the
    # controller record.  Accept both list-shaped and mapping-shaped collections.
    search_roots = [
        (network_doc.get("servers"), f"networks.{network}.servers"),
        (network_doc.get("deployment", {}).get("servers") if isinstance(network_doc.get("deployment"), Mapping) else None, f"networks.{network}.deployment.servers"),
        (_document(private_state).get("servers"), "servers"),
        (_document(private_state).get("coolify", {}).get("hosts") if isinstance(_document(private_state).get("coolify"), Mapping) else None, "coolify.hosts"),
    ]
    for root, label in search_roots:
        for key, item in _iter_named_records(root):
            names = {key}
            for name_key in ("name", "controller_id", "coolify_server", "server", "host"):
                value = item.get(name_key)
                if isinstance(value, str):
                    names.add(value)
            if controller not in names:
                continue
            for ip_key in ("validator_vpn_ip", "vpn_ip", "private_vpn_ip", "wireguard_ip", "tailscale_ip"):
                candidate = _valid_host(item.get(ip_key))
                if candidate:
                    return candidate, f"{label}.{key}.{ip_key}"

    fallback = _valid_host(_host_from_url(controller_record.get("url") or controller_record.get("coolify_url")))
    if fallback:
        return fallback, f"networks.{network}.coolify.controllers.{controller}.url.hostname-fallback"

    raise _fail("MOTHER_DEPLOY_VALIDATOR_ROUTE_VPN_IP_MISSING", f"controller {controller!r} has no validator VPN IP")


def _port(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= port <= 65535:
        return port
    return None


def _endpoint_host_port(value: Any) -> tuple[str, int] | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.startswith("enode://"):
        # enode://<id>@<host>:<port>[?...]
        rest = text.split("@", 1)[-1].split("?", 1)[0]
    else:
        rest = text
    if rest.startswith("[") and "]:" in rest:
        host = rest[1:rest.index("]:")]
        port_text = rest.split("]:", 1)[1]
    elif ":" in rest:
        host, port_text = rest.rsplit(":", 1)
    else:
        return None
    port = _port(port_text)
    if port is None:
        return None
    host = _valid_host(host)
    if host is None:
        return None
    return host, port


def validator_route_from_record(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract a recorded validator P2P route from a service/target record."""

    raw = record.get("validator_route")
    if not isinstance(raw, Mapping):
        raw = record.get("p2p_route") if isinstance(record.get("p2p_route"), Mapping) else record

    endpoint = (
        raw.get("p2p_endpoint")
        or raw.get("endpoint")
        or raw.get("enode")
        or record.get("p2p_endpoint")
        or record.get("enode")
    )
    parsed = _endpoint_host_port(endpoint)
    host = _valid_host(raw.get("vpn_ip") or raw.get("advertised_host") or raw.get("host"))
    port = _port(raw.get("p2p_port") or raw.get("advertised_port") or raw.get("host_p2p_port"))

    if parsed is not None:
        parsed_host, parsed_port = parsed
        if host is not None and host != parsed_host:
            return None
        if port is not None and port != parsed_port:
            return None
        host = parsed_host
        port = parsed_port

    if host is None or port is None:
        return None

    controller_id = raw.get("controller_id") or record.get("controller_id")
    route = {
        "kind": "mother-validator-p2p-route.v1",
        "controller_id": str(controller_id) if isinstance(controller_id, str) and controller_id else None,
        "vpn_ip": host,
        "advertised_host": host,
        "p2p_port": port,
        "advertised_port": port,
        "container_p2p_port": _port(raw.get("container_p2p_port")) or port,
        "p2p_endpoint": f"{host}:{port}",
    }
    source = raw.get("source") or raw.get("route_source") or record.get("route_source")
    if isinstance(source, str) and source:
        route["source"] = source
    return route


def _service_sort_key(item: tuple[str, Mapping[str, Any]]) -> tuple[str, str]:
    node, record = item
    return (str(record.get("controller_id") or ""), node)


def used_p2p_ports_for_controller(services: Mapping[str, Any], *, controller_id: str, default_port: int = _DEFAULT_P2P_PORT) -> set[int]:
    """Return host-level P2P ports already consumed by services on a controller.

    Legacy service records without explicit route metadata reserve sequential
    ports in topology order so a later add on the same host does not collide with
    old one-node evidence.
    """

    controller = _identifier(controller_id, "controller_id")
    explicit: set[int] = set()
    legacy: list[tuple[str, Mapping[str, Any]]] = []
    for node, raw in sorted(services.items(), key=lambda pair: str(pair[0])):
        if not isinstance(raw, Mapping) or raw.get("controller_id") != controller:
            continue
        route = validator_route_from_record(raw)
        if route is not None:
            explicit.add(int(route["p2p_port"]))
        else:
            legacy.append((str(node), raw))
    used = set(explicit)
    next_port = int(default_port)
    for _node, _record in sorted(legacy, key=_service_sort_key):
        while next_port in used:
            next_port += 1
        used.add(next_port)
        next_port += 1
    return used


def allocate_candidate_validator_route(
    private_state: Any,
    *,
    network: str,
    controller_id: str,
    existing_services: Mapping[str, Any],
    default_port: int = _DEFAULT_P2P_PORT,
) -> dict[str, Any]:
    """Allocate the candidate's externally reachable validator P2P route."""

    controller = _identifier(controller_id, "controller_id")
    host, source = controller_validator_host(private_state, network=network, controller_id=controller)
    used = used_p2p_ports_for_controller(existing_services, controller_id=controller, default_port=default_port)
    port = int(default_port)
    while port in used:
        port += 1
        if port > 65535:
            raise _fail("MOTHER_DEPLOY_VALIDATOR_ROUTE_PORTS_EXHAUSTED", f"no free validator P2P port remains on {controller}")
    return {
        "kind": "mother-validator-p2p-route.v1",
        "controller_id": controller,
        "vpn_ip": host,
        "advertised_host": host,
        "p2p_port": port,
        "advertised_port": port,
        "container_p2p_port": port,
        "p2p_endpoint": f"{host}:{port}",
        "source": source,
        "allocation": {
            "default_p2p_port": int(default_port),
            "used_p2p_ports_on_controller": sorted(used),
        },
    }


def ensure_service_validator_route(
    private_state: Any,
    *,
    network: str,
    node: str,
    service: Mapping[str, Any],
    services: Mapping[str, Any],
    default_port: int = _DEFAULT_P2P_PORT,
) -> dict[str, Any]:
    """Return a service's route, deriving a deterministic legacy route if needed."""

    route = validator_route_from_record(service)
    if route is not None:
        return route
    controller = _identifier(service.get("controller_id"), f"{node} controller_id")
    host, source = controller_validator_host(private_state, network=network, controller_id=controller)
    ordered = [
        str(name)
        for name, record in sorted(services.items(), key=lambda pair: str(pair[0]))
        if isinstance(record, Mapping) and record.get("controller_id") == controller
    ]
    port = int(default_port)
    for existing in ordered:
        if existing == node:
            break
        prior = services.get(existing)
        prior_route = validator_route_from_record(prior) if isinstance(prior, Mapping) else None
        if prior_route is not None:
            port = max(port, int(prior_route["p2p_port"]) + 1)
        else:
            port += 1
    return {
        "kind": "mother-validator-p2p-route.v1",
        "controller_id": controller,
        "vpn_ip": host,
        "advertised_host": host,
        "p2p_port": port,
        "advertised_port": port,
        "container_p2p_port": port,
        "p2p_endpoint": f"{host}:{port}",
        "source": f"{source}; legacy-topology-default-allocation",
    }
