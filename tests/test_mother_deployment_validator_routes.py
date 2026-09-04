from __future__ import annotations

import json
from types import SimpleNamespace

from tools.mother.common.deployment_validator_routes import (
    allocate_candidate_validator_route,
    ensure_service_validator_route,
    validator_route_advertised_host,
)


def _private_state() -> SimpleNamespace:
    document = {
        "networks": {
            "mainnet": {
                "coolify": {
                    "controllers": {
                        "coolify-a": {
                            "enabled": True,
                            "url": "http://coolify-a.invalid",
                            "vpn_ip": "10.116.0.3",
                        },
                        "coolify-c": {
                            "enabled": True,
                            "url": "http://coolify-c.invalid",
                            "vpn_ip": "10.116.0.4",
                        },
                    }
                }
            }
        }
    }
    return SimpleNamespace(canonical_object_bytes=json.dumps(document, sort_keys=True).encode("utf-8"))


def test_candidate_route_reuses_host_vpn_but_allocates_next_free_port_on_same_host() -> None:
    existing = {
        "mainneta-super1": {
            "node": "mainneta-super1",
            "controller_id": "coolify-a",
            "service_uuid": "svc-a1",
            "validator_route": {
                "vpn_ip": "10.116.0.3",
                "p2p_port": 30303,
                "p2p_endpoint": "10.116.0.3:30303",
            },
        }
    }

    route = allocate_candidate_validator_route(
        _private_state(),
        network="mainnet",
        controller_id="coolify-a",
        existing_services=existing,
    )

    assert route["vpn_ip"] == "10.116.0.3"
    assert route["p2p_port"] == 30304
    assert route["p2p_endpoint"] == "10.116.0.3:30304"
    assert route["container_p2p_port"] == 30304
    assert route["allocation"]["used_p2p_ports_on_controller"] == [30303]


def test_candidate_route_uses_default_port_on_different_empty_host() -> None:
    existing = {
        "mainneta-super1": {
            "node": "mainneta-super1",
            "controller_id": "coolify-a",
            "service_uuid": "svc-a1",
            "validator_route": {
                "vpn_ip": "10.116.0.3",
                "p2p_port": 30303,
                "p2p_endpoint": "10.116.0.3:30303",
            },
        }
    }

    route = allocate_candidate_validator_route(
        _private_state(),
        network="mainnet",
        controller_id="coolify-c",
        existing_services=existing,
    )

    assert route["vpn_ip"] == "10.116.0.4"
    assert route["p2p_port"] == 30303
    assert route["p2p_endpoint"] == "10.116.0.4:30303"


def test_legacy_service_without_route_reserves_default_port() -> None:
    existing = {
        "mainneta-super1": {
            "node": "mainneta-super1",
            "controller_id": "coolify-a",
            "service_uuid": "svc-a1",
        }
    }

    route = allocate_candidate_validator_route(
        _private_state(),
        network="mainnet",
        controller_id="coolify-a",
        existing_services=existing,
    )

    assert route["vpn_ip"] == "10.116.0.3"
    assert route["p2p_port"] == 30304
    assert route["p2p_endpoint"] == "10.116.0.3:30304"


def test_existing_service_route_is_returned_exactly_for_bootnode_selection() -> None:
    services = {
        "mainneta-super1": {
            "node": "mainneta-super1",
            "controller_id": "coolify-a",
            "service_uuid": "svc-a1",
            "validator_route": {
                "vpn_ip": "10.116.0.3",
                "p2p_port": 30304,
                "p2p_endpoint": "10.116.0.3:30304",
            },
        }
    }

    route = ensure_service_validator_route(
        _private_state(),
        network="mainnet",
        node="mainneta-super1",
        service=services["mainneta-super1"],
        services=services,
    )

    assert route["vpn_ip"] == "10.116.0.3"
    assert route["p2p_port"] == 30304
    assert route["p2p_endpoint"] == "10.116.0.3:30304"


def test_validator_route_advertised_host_rejects_loopback_and_wildcard() -> None:
    assert validator_route_advertised_host({"advertised_host": "127.0.0.1"}) is None
    assert validator_route_advertised_host({"advertised_host": "0.0.0.0"}) is None
    assert validator_route_advertised_host({"p2p_endpoint": "127.0.0.1:30303"}) is None


def test_validator_route_advertised_host_accepts_route_sources() -> None:
    assert validator_route_advertised_host({"advertised_host": "10.116.0.3"}) == "10.116.0.3"
    assert validator_route_advertised_host({"vpn_ip": "10.116.0.4"}) == "10.116.0.4"
    assert validator_route_advertised_host({"p2p_endpoint": "10.116.0.5:30305"}) == "10.116.0.5"
    assert validator_route_advertised_host({"enode": "enode://" + "a" * 128 + "@10.116.0.6:30306"}) == "10.116.0.6"
