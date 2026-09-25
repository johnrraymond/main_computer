from __future__ import annotations

from pathlib import Path

import pytest

from tools.fdb_control.common.cluster_file import parse_cluster_file
from tools.fdb_control.common.errors import FdbControlError
from tools.fdb_control.common.models import (
    ClusterIdentity,
    CreateClusterRequest,
    DeploymentObservation,
    FdbContext,
    InspectionRequest,
    ServiceObservation,
    ServicePlacement,
)
from tools.fdb_control.create_cluster import build_birth_plan, cross_check_birth_observation, prep
from tools.fdb_control.inspect import run as inspect_run


def _services() -> tuple[ServicePlacement, ...]:
    return (
        ServicePlacement("mainnet-fdb1", "coolify-a", "10.116.0.3", 4550, "coolify-a", "coolify-a"),
        ServicePlacement("mainnet-fdb2", "coolify-a", "10.116.0.3", 4551, "coolify-a", "coolify-a"),
        ServicePlacement("mainnet-fdb3", "coolify-c", "10.124.0.3", 4550, "coolify-c", "coolify-c"),
    )


def _request() -> CreateClusterRequest:
    return CreateClusterRequest(
        network="mainnet",
        cluster=ClusterIdentity("main_computer_mainnet", "ac826580a04d022d"),
        services=_services(),
        coordinator_service_ids=("mainnet-fdb1", "mainnet-fdb3"),
        redundancy_mode="double",
        storage_engine="ssd",
    )


def _ctx() -> FdbContext:
    return FdbContext.from_repo(Path(__file__).resolve().parents[2])


def test_birth_plan_allows_multiple_services_on_one_host() -> None:
    plan = build_birth_plan(_request())

    assert [service.host_id for service in plan.services].count("coolify-a") == 2
    assert [coordinator.service_id for coordinator in plan.coordinators] == ["mainnet-fdb1", "mainnet-fdb3"]
    parsed = parse_cluster_file(plan.cluster_file_contents)
    assert parsed.coordinator_addresses == ("10.116.0.3:4550", "10.124.0.3:4550")
    assert "10.116.0.3:4551" not in parsed.coordinator_addresses


def test_birth_and_inspection_cross_verify_same_cluster_facts() -> None:
    plan = build_birth_plan(_request())
    deployment = DeploymentObservation(
        services=tuple(
            ServiceObservation(
                service_id=service.service_id,
                host_id=service.host_id,
                endpoint=service.endpoint,
                deployment_present=True,
                runtime_running=True,
            )
            for service in plan.services
        )
    )
    status = {
        "cluster": {
            "database_available": True,
            "connection_string": plan.cluster_file_contents,
            "full_replication": True,
            "configuration": {"redundancy_mode": "double", "storage_engine": "ssd"},
            "fault_tolerance": {"max_zone_failures_without_losing_availability": 1},
            "recovery_state": {"name": "fully_recovered"},
            "processes": {
                service.service_id: {"address": service.endpoint}
                for service in plan.services
            },
        },
        "client": {
            "database_status": {"available": True, "healthy": True},
            "coordinators": {
                "quorum_reachable": True,
                "coordinators": [
                    {"address": coordinator.endpoint, "reachable": True}
                    for coordinator in plan.coordinators
                ],
            },
        },
    }

    result = inspect_run(
        _ctx(),
        InspectionRequest("mainnet"),
        accepted_reader=lambda _ctx, _network: None,
        deployment_reader=lambda _ctx, _network: deployment,
        status_reader=lambda _ctx, _network: status,
    )
    check = cross_check_birth_observation(plan, result)

    assert check.cluster_identity_matches is True
    assert check.all_services_observed is True
    assert check.coordinator_set_matches is True
    assert check.database_available is True
    assert check.missing_service_endpoints == ()
    assert check.mismatches == ()
    assert result.fdb is not None
    assert result.fdb.max_zone_failures_without_losing_availability == 1
    assert result.transaction_probe == "unavailable:FDB_OPEN_TRANSACTION_PROBE"


def test_inspection_keeps_missing_status_unknown_instead_of_claiming_health() -> None:
    result = inspect_run(
        _ctx(),
        InspectionRequest("mainnet"),
        accepted_reader=lambda _ctx, _network: None,
        deployment_reader=lambda _ctx, _network: DeploymentObservation(services=()),
        status_reader=lambda _ctx, _network: None,
    )

    assert result.fdb is None
    assert "fdb-status-unavailable" in result.drift.unknowns
    assert result.allowed_actions == ("inspect", "create-cluster")


def test_initial_live_birth_rejects_double_redundancy() -> None:
    from tools.fdb_control.common.errors import FdbControlError
    from tools.fdb_control.common.models import CreateClusterDeployment
    from tools.fdb_control.create_cluster import prep

    with pytest.raises(FdbControlError) as exc:
        prep(_ctx(), _request(), CreateClusterDeployment())

    assert exc.value.code == "FDB_INITIAL_BIRTH_REQUIRES_ONE_SERVICE"


def test_birth_rejects_duplicate_endpoint_even_when_services_have_different_ids() -> None:
    services = _services()
    duplicate = ServicePlacement("mainnet-fdb4", "coolify-b", "10.116.0.3", 4550, "coolify-b", "coolify-b")
    request = CreateClusterRequest(
        network="mainnet",
        cluster=ClusterIdentity("main_computer_mainnet", "ac826580a04d022d"),
        services=services + (duplicate,),
        coordinator_service_ids=("mainnet-fdb1",),
        redundancy_mode="double",
        storage_engine="ssd",
    )

    with pytest.raises(ValueError, match="service endpoints must be unique"):
        build_birth_plan(request)


def test_new_control_surface_does_not_import_mother() -> None:
    root = Path(__file__).resolve().parents[2] / "tools" / "fdb_control"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    assert "tools.mother" not in source
