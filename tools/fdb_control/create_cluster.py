from __future__ import annotations

from typing import Any, Mapping

from .common.canonical import canonical_json
from .common.cluster_file import parse_cluster_file, render_cluster_file
from .common.coolify import CoolifyClient, create_service, deploy_service, ensure_environment, find_service, resolve_context, update_service, wait_for_running
from .common.coordinators import resolve_explicit_coordinators
from .common.errors import FdbControlError
from .common.hashing import sha256_bytes
from .common.models import (
    AcceptedClusterState,
    BirthObservationCheck,
    BirthPlan,
    ClusterIdentity,
    CoordinatorEndpoint,
    CreateClusterDeployment,
    CreateClusterRequest,
    FdbContext,
    InspectionResult,
    OperationCommandResult,
    ServicePlacement,
)
from .common.privates import load_private_infrastructure, public_binding_ref, resolve_host_binding
from .common.service_descriptors import render_birth_service_descriptor
from .common.state import OP_SCHEMA, publish_accepted_state, read_accepted_state, require_operation, update_operation, write_operation
from .live_inspect import verify_birth


def build_birth_plan(request: CreateClusterRequest) -> BirthPlan:
    """Freeze the pure, non-effectful portion of a cluster-birth target."""

    service_ids = [service.service_id for service in request.services]
    if len(set(service_ids)) != len(service_ids):
        raise ValueError("service ids must be unique")
    endpoints = [service.endpoint for service in request.services]
    if len(set(endpoints)) != len(endpoints):
        raise ValueError("service endpoints must be unique")

    coordinators = resolve_explicit_coordinators(request.services, request.coordinator_service_ids)
    cluster_file_contents = render_cluster_file(request.cluster, coordinators)
    parsed = parse_cluster_file(cluster_file_contents)
    if parsed.cluster != request.cluster:
        raise AssertionError("rendered cluster identity did not round-trip")

    return BirthPlan(
        network=request.network,
        cluster=request.cluster,
        services=tuple(sorted(request.services, key=lambda item: item.service_id.encode("utf-8"))),
        coordinators=coordinators,
        redundancy_mode=request.redundancy_mode,
        storage_engine=request.storage_engine,
        cluster_file_contents=cluster_file_contents,
    )


def cross_check_birth_observation(plan: BirthPlan, inspection: InspectionResult) -> BirthObservationCheck:
    if inspection.fdb is None:
        return BirthObservationCheck(
            cluster_identity_matches=None,
            all_services_observed=None,
            coordinator_set_matches=None,
            database_available=None,
            missing_service_endpoints=tuple(service.endpoint for service in plan.services),
            mismatches=("fdb-status-unavailable",),
        )

    fdb = inspection.fdb
    mismatches: list[str] = []
    identity_matches: bool | None = None
    if fdb.connection_string:
        try:
            parsed = parse_cluster_file(fdb.connection_string)
        except ValueError:
            identity_matches = False
            mismatches.append("connection-string-invalid")
        else:
            identity_matches = parsed.cluster == plan.cluster
            if not identity_matches:
                mismatches.append("cluster-identity-mismatch")

    observed_processes = set(fdb.process_addresses)
    missing = tuple(
        sorted(
            (service.endpoint for service in plan.services if service.endpoint not in observed_processes),
            key=lambda item: item.encode("utf-8"),
        )
    )
    all_services = not missing
    if missing:
        mismatches.append("service-participation-incomplete")

    expected_coordinators = tuple(sorted((item.endpoint for item in plan.coordinators), key=lambda item: item.encode("utf-8")))
    coordinator_matches = expected_coordinators == fdb.coordinator_addresses if fdb.coordinator_addresses else None
    if coordinator_matches is False:
        mismatches.append("coordinator-set-mismatch")
    if fdb.database_available is False:
        mismatches.append("database-unavailable")

    return BirthObservationCheck(
        cluster_identity_matches=identity_matches,
        all_services_observed=all_services,
        coordinator_set_matches=coordinator_matches,
        database_available=fdb.database_available,
        missing_service_endpoints=missing,
        mismatches=tuple(mismatches),
    )


def prep(
    ctx: FdbContext,
    request: CreateClusterRequest,
    deployment: CreateClusterDeployment,
    *,
    client_factory=CoolifyClient,
) -> OperationCommandResult:
    """Prepare one initial-service birth without mutating Coolify or FoundationDB."""

    if read_accepted_state(ctx, request.network) is not None:
        raise FdbControlError(
            code="FDB_CLUSTER_ALREADY_BORN",
            message=f"accepted FDB state already exists for {request.network!r}",
            module_id="FDB-OFM-APP-004",
            retry_class="inspect-first",
        )
    plan = build_birth_plan(request)
    _require_supported_initial_birth(plan)
    descriptor = render_birth_service_descriptor(plan, plan.services[0], image=deployment.image, data_root=deployment.data_root)

    private_doc = load_private_infrastructure(ctx)
    binding = resolve_host_binding(private_doc, plan.network, plan.services[0].host_id, base_dir=ctx.private_state_path.parent)
    client = client_factory(binding)
    environment_name = deployment.environment_name.strip() or f"{request.network}-fdb"
    context = resolve_context(
        client,
        project_uuid=deployment.project_uuid,
        project_name=deployment.project_name,
        environment_name=environment_name,
        environment_uuid=deployment.environment_uuid,
        server_uuid=deployment.server_uuid,
        server_name=deployment.server_name,
        destination_uuid=deployment.destination_uuid,
        allow_create_environment=False,
    )

    target = {
        "request": _request_to_wire(request),
        "deployment": _deployment_to_wire(deployment, environment_name=environment_name),
        "coolify_context": _context_to_wire(context),
        "binding": public_binding_ref(binding),
        "service_name": descriptor.service_name,
        "compose_sha256": sha256_bytes(descriptor.compose.encode("utf-8")).digest,
    }
    operation_id = f"fdb-create-{request.network}-{sha256_bytes(canonical_json(target)).digest[:16]}"
    payload = {
        "schema": OP_SCHEMA,
        "operation_id": operation_id,
        "network": request.network,
        "kind": "create-cluster",
        "stage": "prepared",
        "target": target,
    }
    write_operation(ctx, payload)
    return OperationCommandResult(
        operation="create-cluster",
        stage="prep",
        status="prepared",
        details={"operation_id": operation_id, "service_name": descriptor.service_name, "target": target},
    )


def do(
    ctx: FdbContext,
    network: str,
    operation_id: str,
    *,
    client_factory=CoolifyClient,
) -> OperationCommandResult:
    op = require_operation(ctx, network, operation_id)
    if op.get("kind") != "create-cluster":
        raise ValueError("operation kind is not create-cluster")
    if op.get("stage") == "finalized":
        return OperationCommandResult("create-cluster", "do", "already-finalized", {"operation_id": operation_id})

    request = _request_from_wire(_mapping(op["target"])["request"])
    deployment = _deployment_from_wire(_mapping(op["target"])["deployment"])
    plan = build_birth_plan(request)
    _require_supported_initial_birth(plan)
    descriptor = render_birth_service_descriptor(plan, plan.services[0], image=deployment.image, data_root=deployment.data_root)
    if sha256_bytes(descriptor.compose.encode("utf-8")).digest != _mapping(op["target"]).get("compose_sha256"):
        raise FdbControlError(
            code="FDB_PREPARED_DESCRIPTOR_DRIFT",
            message="rendered service descriptor no longer matches the prepared operation",
            module_id="FDB-OFM-APP-004",
            operation_id=operation_id,
            retry_class="inspect-first",
        )

    private_doc = load_private_infrastructure(ctx)
    binding = resolve_host_binding(private_doc, plan.network, plan.services[0].host_id, base_dir=ctx.private_state_path.parent)
    client = client_factory(binding)
    frozen_context = _context_from_wire(_mapping(op["target"])["coolify_context"])
    context = ensure_environment(client, frozen_context)

    service_uuid, _existing = find_service(client, descriptor.service_name)
    action = "updated"
    if service_uuid:
        update_service(client, service_uuid, service_name=descriptor.service_name, compose_b64=descriptor.compose_b64)
    else:
        action = "created"
        service_uuid = create_service(
            client,
            context,
            service_name=descriptor.service_name,
            description=descriptor.description,
            compose_b64=descriptor.compose_b64,
        )
    deploy_service(client, service_uuid, force=deployment.force_deploy)
    deployment_result = {
        "service_uuid": service_uuid,
        "service_name": descriptor.service_name,
        "action": action,
        "coolify_context": _context_to_wire(context),
    }
    update_operation(
        ctx,
        network,
        operation_id,
        stage="deploying",
        deployment_result=deployment_result,
    )

    # Coolify service creation/start is asynchronous. Do not claim the birth
    # deployment exists until Coolify reports that the service has actually
    # materialized into a running state. This is deployment proof only; FDB
    # correctness is verified independently through the observer proof.
    wait_for_running(client, service_uuid, timeout_s=300.0, poll_s=5.0)

    update_operation(
        ctx,
        network,
        operation_id,
        stage="deployed",
        deployment_result=deployment_result,
    )
    return OperationCommandResult(
        operation="create-cluster",
        stage="do",
        status="deployed",
        details={
            "operation_id": operation_id,
            "service_uuid": service_uuid,
            "service_name": descriptor.service_name,
            "action": action,
            "waited_for_running": True,
            "wait_timeout_seconds": 300,
        },
    )


def finalize(
    ctx: FdbContext,
    network: str,
    operation_id: str,
    *,
    client_factory=CoolifyClient,
) -> OperationCommandResult:
    op = require_operation(ctx, network, operation_id)
    if op.get("stage") == "finalized":
        return OperationCommandResult("create-cluster", "finalize", "finalized", {"operation_id": operation_id})
    if op.get("stage") != "deployed":
        raise FdbControlError(
            code="FDB_CREATE_NOT_DEPLOYED",
            message="create-cluster finalize requires a completed do stage",
            module_id="FDB-OFM-APP-004",
            operation_id=operation_id,
            retry_class="exact-retry",
        )

    request = _request_from_wire(_mapping(op["target"])["request"])
    plan = build_birth_plan(request)
    result = _mapping(op.get("deployment_result"))
    verification = verify_birth(
        ctx,
        plan,
        service_name=str(result.get("service_name") or _mapping(op["target"])["service_name"]),
        service_uuid=str(result.get("service_uuid") or "") or None,
        client_factory=client_factory,
        wait_timeout_s=300.0,
    )
    if not verification.verified:
        raise FdbControlError(
            code="FDB_BIRTH_NOT_VERIFIED",
            message=f"birth inspection did not prove the cluster: {verification.reason}; status={verification.coolify_status}",
            module_id="FDB-OFM-APP-004",
            operation_id=operation_id,
            retry_class="exact-retry",
        )

    accepted = AcceptedClusterState(
        network=plan.network,
        generation=1,
        cluster=plan.cluster,
        services=plan.services,
        coordinators=plan.coordinators,
        redundancy_mode=plan.redundancy_mode,
        storage_engine=plan.storage_engine,
        retired=False,
    )
    accepted_file = publish_accepted_state(ctx, accepted)
    update_operation(
        ctx,
        network,
        operation_id,
        stage="finalized",
        verification={
            "verified": True,
            "service_uuid": verification.coolify_service_uuid,
            "status": verification.coolify_status,
            "reason": verification.reason,
        },
        accepted_state_path=str(accepted_file),
    )
    return OperationCommandResult(
        operation="create-cluster",
        stage="finalize",
        status="finalized",
        details={
            "operation_id": operation_id,
            "verified": True,
            "coolify_status": verification.coolify_status,
            "accepted_state_path": str(accepted_file),
        },
    )


def plan_from_operation(ctx: FdbContext, network: str, operation_id: str) -> tuple[BirthPlan, dict[str, Any]]:
    op = require_operation(ctx, network, operation_id)
    request = _request_from_wire(_mapping(op["target"])["request"])
    return build_birth_plan(request), op


def _require_supported_initial_birth(plan: BirthPlan) -> None:
    if len(plan.services) != 1:
        raise FdbControlError(
            code="FDB_INITIAL_BIRTH_REQUIRES_ONE_SERVICE",
            message="this implementation wave births exactly one initial FDB service; add-service expands it afterward",
            module_id="FDB-OFM-FDB-004",
        )
    if plan.redundancy_mode != "single":
        raise FdbControlError(
            code="FDB_INITIAL_BIRTH_REQUIRES_SINGLE_REDUNDANCY",
            message="one-service birth must use FoundationDB single redundancy",
            module_id="FDB-OFM-FDB-004",
        )
    if len(plan.coordinators) != 1 or plan.coordinators[0].service_id != plan.services[0].service_id:
        raise FdbControlError(
            code="FDB_INITIAL_BIRTH_COORDINATOR_INVALID",
            message="the initial FDB service must be the sole coordinator during one-service birth",
            module_id="FDB-OFM-FDB-004",
        )


def _request_to_wire(request: CreateClusterRequest) -> dict[str, Any]:
    return {
        "network": request.network,
        "cluster": {"description": request.cluster.description, "cluster_id": request.cluster.cluster_id},
        "services": [
            {
                "service_id": item.service_id,
                "host_id": item.host_id,
                "address": item.address,
                "port": item.port,
                "machine_id": item.machine_id,
                "zone_id": item.zone_id,
            }
            for item in request.services
        ],
        "coordinator_service_ids": list(request.coordinator_service_ids),
        "redundancy_mode": request.redundancy_mode,
        "storage_engine": request.storage_engine,
    }


def _request_from_wire(raw: object) -> CreateClusterRequest:
    payload = _mapping(raw)
    cluster = _mapping(payload["cluster"])
    return CreateClusterRequest(
        network=str(payload["network"]),
        cluster=ClusterIdentity(description=str(cluster["description"]), cluster_id=str(cluster["cluster_id"])),
        services=tuple(ServicePlacement(**_mapping(item)) for item in _sequence(payload["services"])),
        coordinator_service_ids=tuple(str(item) for item in _sequence(payload["coordinator_service_ids"])),
        redundancy_mode=str(payload["redundancy_mode"]),
        storage_engine=str(payload["storage_engine"]),
    )


def _deployment_to_wire(value: CreateClusterDeployment, *, environment_name: str) -> dict[str, Any]:
    return {
        "project_uuid": value.project_uuid,
        "project_name": value.project_name,
        "environment_name": environment_name,
        "environment_uuid": value.environment_uuid,
        "server_uuid": value.server_uuid,
        "server_name": value.server_name,
        "destination_uuid": value.destination_uuid,
        "image": value.image,
        "data_root": value.data_root,
        "force_deploy": value.force_deploy,
    }


def _deployment_from_wire(raw: object) -> CreateClusterDeployment:
    return CreateClusterDeployment(**_mapping(raw))


def _context_to_wire(value: object) -> dict[str, Any]:
    return {
        "project_uuid": str(getattr(value, "project_uuid")),
        "environment_name": str(getattr(value, "environment_name")),
        "environment_uuid": getattr(value, "environment_uuid"),
        "server_uuid": str(getattr(value, "server_uuid")),
        "destination_uuid": getattr(value, "destination_uuid"),
    }


def _context_from_wire(raw: object):
    from .common.coolify import CoolifyContext

    payload = _mapping(raw)
    return CoolifyContext(
        project_uuid=str(payload["project_uuid"]),
        environment_name=str(payload["environment_name"]),
        environment_uuid=str(payload.get("environment_uuid") or "") or None,
        server_uuid=str(payload["server_uuid"]),
        destination_uuid=str(payload.get("destination_uuid") or "") or None,
    )


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("expected mapping")
    return dict(value)


def _sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("expected sequence")
    return tuple(value)
