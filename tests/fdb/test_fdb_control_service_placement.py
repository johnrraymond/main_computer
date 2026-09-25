from __future__ import annotations

import pytest

from tools.fdb_control.common.errors import FdbControlError
from tools.fdb_control.common.models import (
    AcceptedClusterState,
    ClusterIdentity,
    CoordinatorEndpoint,
    FdbContext,
    ServicePlacement,
)
from tools.fdb_control.common.privates import resolve_host_binding, resolve_host_fdb_address
from tools.fdb_control.common.service_placement import (
    allocate_fdb_port,
    infer_service_placement,
    parse_service_identity,
    resolve_host_for_placement,
    resolve_host_id_for_placement,
)


def _private_doc() -> dict:
    # This is the canonical Mother private-state shape stored in
    # runtime/state/mother/identity.private.yaml.
    return {
        "schema_version": 1,
        "kind": "main_computer.mother.private_state.v1",
        "networks": {
            "mainnet": {
                "coolify": {
                    "mutation_authority": "observe-only",
                    "controllers": {
                        "coolify-a": {
                            "url": "http://coolify-a.invalid:8000",
                            "api_token": "token-a",
                            "vpn_ip": "10.116.0.3",
                        },
                        "coolify-c": {
                            "url": "http://coolify-c.invalid:8000",
                            "api_token": "token-c",
                            "vpn_ip": "10.116.0.2",
                        },
                    },
                }
            }
        },
    }


def _accepted() -> AcceptedClusterState:
    services = (
        ServicePlacement("mainneta-fdb1", "coolify-a", "10.116.0.3", 4550, "coolify-a", "coolify-a"),
        ServicePlacement("mainneta-fdb2", "coolify-a", "10.116.0.3", 4551, "coolify-a", "coolify-a"),
    )
    return AcceptedClusterState(
        network="mainnet",
        generation=2,
        cluster=ClusterIdentity("main_computer_mainnet", "ac826580a04d022d"),
        services=services,
        coordinators=(CoordinatorEndpoint("mainneta-fdb1", "coolify-a", "10.116.0.3", 4550),),
        redundancy_mode="single",
        storage_engine="ssd",
    )


def test_fdb_context_uses_same_identity_private_file_as_mother(tmp_path) -> None:
    ctx = FdbContext.from_repo(tmp_path)
    assert ctx.private_state_path == tmp_path.resolve() / "runtime" / "state" / "mother" / "identity.private.yaml"


def test_service_identity_infers_placement_token_from_logical_name() -> None:
    parsed = parse_service_identity("mainnet", "mainnetc-fdb3")
    assert parsed.network == "mainnet"
    assert parsed.placement_token == "c"
    assert parsed.ordinal == 3


def test_service_identity_rejects_network_mismatch() -> None:
    with pytest.raises(FdbControlError) as exc:
        parse_service_identity("testnet", "mainnetc-fdb3")
    assert exc.value.envelope.code == "FDB_SERVICE_NETWORK_MISMATCH"


def test_service_identity_requires_placement_for_automatic_add() -> None:
    with pytest.raises(FdbControlError) as exc:
        parse_service_identity("mainnet", "mainnet-fdb3")
    assert exc.value.envelope.code == "FDB_SERVICE_PLACEMENT_REQUIRED"


def test_logical_c_resolves_directly_from_mother_identity_state() -> None:
    resolved = resolve_host_for_placement(_private_doc(), "mainnet", "c")
    assert resolved.controller_id == "coolify-c"
    assert resolved.host_id == "coolify-c"
    assert resolved.source == "runtime/state/mother/identity.private.yaml:networks.mainnet.coolify.controllers.coolify-c"
    assert resolve_host_id_for_placement(_private_doc(), "mainnet", "c") == "coolify-c"


def test_unknown_placement_fails_closed_against_identity_controller_registry() -> None:
    with pytest.raises(FdbControlError) as exc:
        resolve_host_for_placement(_private_doc(), "mainnet", "x")
    assert exc.value.envelope.code == "FDB_SERVICE_PLACEMENT_UNKNOWN"
    assert "coolify-a" in exc.value.envelope.message
    assert "coolify-c" in exc.value.envelope.message


def test_port_allocator_uses_first_free_port_per_logical_controller() -> None:
    accepted = _accepted()
    assert allocate_fdb_port(accepted, "coolify-a") == 4552
    assert allocate_fdb_port(accepted, "coolify-c") == 4550


def test_inferred_service_placement_needs_only_service_identity() -> None:
    placement, resolution = infer_service_placement(
        _private_doc(),
        _accepted(),
        network="mainnet",
        service_id="mainnetc-fdb3",
    )
    assert placement == ServicePlacement(
        "mainnetc-fdb3",
        "coolify-c",
        "10.116.0.2",
        4550,
        "coolify-c",
        "coolify-c",
    )
    assert resolution["controller_id"] == "coolify-c"
    assert resolution["host_id"] == "coolify-c"
    assert resolution["endpoint"] == "10.116.0.2:4550"
    assert resolution["address_source"] == "private-state:networks.mainnet.coolify.controllers.coolify-c.vpn_ip"


def test_binding_comes_from_same_network_controller_record() -> None:
    binding = resolve_host_binding(_private_doc(), "mainnet", "coolify-c")
    assert binding.host_id == "coolify-c"
    assert binding.slot == "coolify-c"
    assert binding.coolify_url == "http://coolify-c.invalid:8000"
    assert binding.token == "token-c"
    assert binding.token_source == "private-state:networks.mainnet.coolify.controllers.coolify-c.api_token"


def test_private_controller_without_fdb_routable_address_fails_closed() -> None:
    doc = _private_doc()
    del doc["networks"]["mainnet"]["coolify"]["controllers"]["coolify-c"]["vpn_ip"]
    with pytest.raises(FdbControlError) as exc:
        resolve_host_fdb_address(doc, "mainnet", "coolify-c")
    assert exc.value.envelope.code == "FDB_PRIVATE_FDB_ADDRESS_MISSING"


def test_missing_network_controller_registry_fails_closed() -> None:
    doc = _private_doc()
    del doc["networks"]["mainnet"]["coolify"]["controllers"]
    with pytest.raises(FdbControlError) as exc:
        resolve_host_for_placement(doc, "mainnet", "c")
    assert exc.value.envelope.code == "FDB_PRIVATE_CONTROLLERS_MISSING"
