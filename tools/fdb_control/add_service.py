from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .common.canonical import canonical_json
from .common.cluster_file import parse_cluster_file, render_cluster_file
from .common.coolify import (
    CoolifyClient,
    CoolifyContext,
    create_service,
    deploy_service,
    ensure_environment,
    find_service,
    resolve_context,
    update_service,
    wait_for_running,
)
from .common.coordinator_runtime import apply_coordinator_overlay
from .common.coordinators import coordinator_set_changed, derive_topology_coordinators
from .common.errors import FdbControlError
from .common.hashing import sha256_bytes
from .common.models import (
    AcceptedClusterState,
    AddServiceDeployment,
    AddServicePlan,
    AddServiceRequest,
    BirthPlan,
    FdbContext,
    OperationCommandResult,
    ServicePlacement,
)
from .common.privates import load_private_infrastructure, public_binding_ref, resolve_host_binding
from .common.service_placement import infer_service_placement
from .common.service_descriptors import render_add_service_descriptor, render_birth_service_descriptor
from .common.state import (
    ADD_SERVICE_OP_SCHEMA,
    accepted_state_from_wire,
    accepted_state_to_wire,
    advance_accepted_state,
    read_accepted_state,
    require_operation,
    update_operation,
    write_operation,
)
from .live_inspect import verify_add_service


def build_add_service_plan(accepted: AcceptedClusterState, request: AddServiceRequest) -> AddServicePlan:
    if accepted.retired:
        raise FdbControlError(
            code="FDB_CLUSTER_RETIRED",
            message=f"FDB cluster {accepted.network!r} is retired",
            module_id="FDB-OFM-APP-005",
            retry_class="never",
        )
    if request.network != accepted.network:
        raise ValueError("add-service request network does not match accepted cluster")

    service = request.service
    if any(item.service_id == service.service_id for item in accepted.services):
        raise FdbControlError(
            code="FDB_SERVICE_ALREADY_ACCEPTED",
            message=f"service {service.service_id!r} is already in accepted FDB topology",
            module_id="FDB-OFM-APP-005",
            retry_class="inspect-first",
        )
    endpoint_owner = next((item.service_id for item in accepted.services if item.endpoint == service.endpoint), None)
    if endpoint_owner:
        raise FdbControlError(
            code="FDB_SERVICE_ENDPOINT_COLLISION",
            message=f"endpoint {service.endpoint!r} is already owned by accepted service {endpoint_owner!r}",
            module_id="FDB-OFM-NET-001",
            retry_class="never",
        )

    services = tuple(sorted(accepted.services + (service,), key=lambda item: item.service_id.encode("utf-8")))
    if not accepted.services and accepted.coordinators:
        raise FdbControlError(
            code="FDB_ACCEPTED_EMPTY_COORDINATORS_INVALID",
            message="accepted-empty FDB topology cannot retain coordinators",
            module_id="FDB-OFM-APP-005",
            retry_class="inspect-first",
        )
    target_coordinators = derive_topology_coordinators(services, accepted.coordinators)
    if accepted.coordinators:
        source_cluster_file = render_cluster_file(accepted.cluster, accepted.coordinators)
        cluster_file_contents = source_cluster_file
    else:
        # accepted-empty has no live source connection string.  The first
        # add-service transparently re-births the accepted lineage using the
        # new service as the sole coordinator.
        cluster_file_contents = render_cluster_file(accepted.cluster, target_coordinators)
        source_cluster_file = cluster_file_contents
    return AddServicePlan(
        network=accepted.network,
        cluster=accepted.cluster,
        services=services,
        coordinators=target_coordinators,
        source_coordinators=accepted.coordinators,
        redundancy_mode=accepted.redundancy_mode,
        storage_engine=accepted.storage_engine,
        cluster_file_contents=cluster_file_contents,
        source_cluster_file_contents=source_cluster_file,
        added_service=service,
    )



def prep_inferred(
    ctx: FdbContext,
    network: str,
    service_id: str,
    deployment: AddServiceDeployment | None = None,
    *,
    client_factory=CoolifyClient,
) -> OperationCommandResult:
    """Prepare add-service from logical identity only.

    Placement is derived once from accepted FDB state plus shared private
    infrastructure, then frozen into the ordinary prepared operation.
    """

    accepted = read_accepted_state(ctx, network)
    if accepted is None:
        raise FdbControlError(
            code="FDB_CLUSTER_UNBORN",
            message=f"no accepted FDB cluster exists for {network!r}; use create-cluster first",
            module_id="FDB-OFM-APP-005",
            retry_class="inspect-first",
        )
    private_doc = load_private_infrastructure(ctx)
    service, resolution = infer_service_placement(
        private_doc,
        accepted,
        network=network,
        service_id=service_id,
    )
    result = prep(
        ctx,
        AddServiceRequest(network=network, service=service),
        deployment or AddServiceDeployment(),
        client_factory=client_factory,
        placement_resolution=resolution,
    )
    details = dict(result.details)
    details.update(
        {
            "placement_token": resolution["placement_token"],
            "controller_id": resolution["controller_id"],
            "host_resolution_source": resolution["host_resolution_source"],
            "address_source": resolution["address_source"],
        }
    )
    return OperationCommandResult(result.operation, result.stage, result.status, details)

def prep(
    ctx: FdbContext,
    request: AddServiceRequest,
    deployment: AddServiceDeployment,
    *,
    client_factory=CoolifyClient,
    placement_resolution: Mapping[str, Any] | None = None,
) -> OperationCommandResult:
    accepted = read_accepted_state(ctx, request.network)
    if accepted is None:
        raise FdbControlError(
            code="FDB_CLUSTER_UNBORN",
            message=f"no accepted FDB cluster exists for {request.network!r}; use create-cluster first",
            module_id="FDB-OFM-APP-005",
            retry_class="inspect-first",
        )
    plan = build_add_service_plan(accepted, request)
    rebirth = not accepted.services
    descriptor = _render_add_descriptor(plan, deployment, rebirth=rebirth)

    private_doc = load_private_infrastructure(ctx)
    binding = resolve_host_binding(private_doc, plan.network, plan.added_service.host_id, base_dir=ctx.private_state_path.parent)
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
    existing_uuid, _ = find_service(client, descriptor.service_name)
    if existing_uuid:
        raise FdbControlError(
            code="FDB_ADD_SERVICE_DEPLOYMENT_EXISTS",
            message=(
                f"Coolify service {descriptor.service_name!r} already exists but is not accepted; "
                "inspect or remove the unaccepted deployment before preparing a new add-service operation"
            ),
            module_id="FDB-OFM-APP-005",
            retry_class="inspect-first",
        )

    prestate = accepted_state_to_wire(accepted)
    target = {
        "request": _request_to_wire(request),
        "deployment": _deployment_to_wire(deployment, environment_name=environment_name),
        "coolify_context": _context_to_wire(context),
        "binding": public_binding_ref(binding),
        "placement_resolution": dict(placement_resolution or {}),
        "service_name": descriptor.service_name,
        "compose_sha256": sha256_bytes(descriptor.compose.encode("utf-8")).digest,
        "accepted_prestate": prestate,
        "accepted_prestate_sha256": sha256_bytes(canonical_json(prestate)).digest,
        "source_coordinators": [item.service_id for item in accepted.coordinators],
        "target_coordinators": [item.service_id for item in plan.coordinators],
        "coordinators_changed": coordinator_set_changed(accepted.coordinators, plan.coordinators),
        "rebirth": rebirth,
    }
    operation_id = f"fdb-add-{request.network}-{sha256_bytes(canonical_json(target)).digest[:16]}"
    write_operation(
        ctx,
        {
            "schema": ADD_SERVICE_OP_SCHEMA,
            "operation_id": operation_id,
            "network": request.network,
            "kind": "add-service",
            "stage": "prepared",
            "target": target,
        },
    )
    return OperationCommandResult(
        operation="add-service",
        stage="prep",
        status="prepared",
        details={
            "operation_id": operation_id,
            "service_id": plan.added_service.service_id,
            "service_name": descriptor.service_name,
            "host_id": plan.added_service.host_id,
            "address": plan.added_service.address,
            "port": plan.added_service.port,
            "endpoint": plan.added_service.endpoint,
            "binding_slot": binding.slot,
            "accepted_generation": accepted.generation,
            "target_generation": accepted.generation + 1,
            "source_coordinators": [item.service_id for item in accepted.coordinators],
            "target_coordinators": [item.endpoint for item in plan.coordinators],
            "target_coordinator_services": [item.service_id for item in plan.coordinators],
            "coordinators_changed": coordinator_set_changed(accepted.coordinators, plan.coordinators),
            "rebirth": rebirth,
        },
    )


def do(
    ctx: FdbContext,
    network: str,
    operation_id: str,
    *,
    client_factory=CoolifyClient,
) -> OperationCommandResult:
    op = require_operation(ctx, network, operation_id)
    if op.get("kind") != "add-service":
        raise ValueError("operation kind is not add-service")
    if op.get("stage") == "finalized":
        return OperationCommandResult("add-service", "do", "already-finalized", {"operation_id": operation_id})

    target = _mapping(op["target"])
    accepted_prestate = accepted_state_from_wire(_mapping(target["accepted_prestate"]))
    current = read_accepted_state(ctx, network)
    if current != accepted_prestate:
        raise FdbControlError(
            code="FDB_ADD_SERVICE_PRESTATE_CHANGED",
            message="accepted FDB state changed after add-service prep",
            module_id="FDB-OFM-APP-005",
            operation_id=operation_id,
            retry_class="inspect-first",
        )

    request = _request_from_wire(target["request"])
    deployment = _deployment_from_wire(target["deployment"])
    plan = _plan_with_runtime_connection(
        build_add_service_plan(accepted_prestate, request),
        _mapping(op.get("deployment_result") or {}),
    )
    rebirth = not accepted_prestate.services
    descriptor = _render_add_descriptor(plan, deployment, rebirth=rebirth)
    if sha256_bytes(descriptor.compose.encode("utf-8")).digest != target.get("compose_sha256"):
        raise FdbControlError(
            code="FDB_PREPARED_DESCRIPTOR_DRIFT",
            message="rendered add-service descriptor no longer matches the prepared operation",
            module_id="FDB-OFM-APP-005",
            operation_id=operation_id,
            retry_class="inspect-first",
        )

    private_doc = load_private_infrastructure(ctx)
    binding = resolve_host_binding(private_doc, plan.network, plan.added_service.host_id, base_dir=ctx.private_state_path.parent)
    client = client_factory(binding)
    context = ensure_environment(client, _context_from_wire(target["coolify_context"]))
    service_uuid, _ = find_service(client, descriptor.service_name)
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
    update_operation(ctx, network, operation_id, stage="deploying", deployment_result=deployment_result)
    wait_for_running(client, service_uuid, timeout_s=300.0, poll_s=5.0)

    # Membership must be proved against the pre-mutation coordinator set before
    # coordinator authority is allowed to move.
    participation = verify_add_service(
        ctx,
        plan,
        service_name=descriptor.service_name,
        service_uuid=service_uuid,
        client_factory=client_factory,
        wait_timeout_s=300.0,
    )
    if not participation.verified:
        raise FdbControlError(
            code="FDB_ADD_SERVICE_PARTICIPATION_NOT_VERIFIED",
            message=f"new FDB service did not prove participation before coordinator reconciliation: {participation.reason}",
            module_id="FDB-OFM-APP-005",
            operation_id=operation_id,
            retry_class="exact-retry",
        )

    if rebirth:
        overlay = {
            "coordinators_changed": True,
            "connection_string": plan.cluster_file_contents,
            "guardian_host_id": None,
            "guardian_service_name": None,
            "guardian_service_uuid": None,
            "guardian_action": "rebirth-not-required",
        }
    else:
        overlay = apply_coordinator_overlay(
            ctx,
            plan,
            environment_name=str(target["deployment"].get("environment_name") or f"{network}-fdb"),
            image=deployment.image,
            force_deploy=deployment.force_deploy,
            operation_id=operation_id,
            client_factory=client_factory,
        )
    deployment_result.update(overlay)
    update_operation(ctx, network, operation_id, stage="deployed", deployment_result=deployment_result)
    return OperationCommandResult(
        operation="add-service",
        stage="do",
        status="deployed",
        details={
            "operation_id": operation_id,
            "service_uuid": service_uuid,
            "service_name": descriptor.service_name,
            "action": action,
            "waited_for_running": True,
            "wait_timeout_seconds": 300,
            "coordinators_changed": bool(overlay["coordinators_changed"]),
            "target_coordinators": [item.endpoint for item in plan.coordinators],
            "target_coordinator_services": [item.service_id for item in plan.coordinators],
            "guardian_service_uuid": overlay["guardian_service_uuid"],
            "rebirth": rebirth,
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
    if op.get("kind") != "add-service":
        raise ValueError("operation kind is not add-service")
    if op.get("stage") == "finalized":
        return OperationCommandResult("add-service", "finalize", "finalized", {"operation_id": operation_id})
    if op.get("stage") != "deployed":
        raise FdbControlError(
            code="FDB_ADD_SERVICE_NOT_DEPLOYED",
            message="add-service finalize requires a completed do stage",
            module_id="FDB-OFM-APP-005",
            operation_id=operation_id,
            retry_class="exact-retry",
        )

    target = _mapping(op["target"])
    accepted_prestate = accepted_state_from_wire(_mapping(target["accepted_prestate"]))
    request = _request_from_wire(target["request"])
    result = _mapping(op.get("deployment_result") or {})
    plan = _plan_with_runtime_connection(build_add_service_plan(accepted_prestate, request), result)
    verification = verify_add_service(
        ctx,
        plan,
        service_name=str(result.get("service_name") or target["service_name"]),
        service_uuid=str(result.get("service_uuid") or "") or None,
        client_factory=client_factory,
        wait_timeout_s=300.0,
    )
    if not verification.verified:
        raise FdbControlError(
            code="FDB_ADD_SERVICE_NOT_VERIFIED",
            message=(
                f"add-service inspection did not prove FDB participation: {verification.reason}; "
                f"status={verification.coolify_status}"
            ),
            module_id="FDB-OFM-APP-005",
            operation_id=operation_id,
            retry_class="exact-retry",
        )

    accepted_target = AcceptedClusterState(
        network=plan.network,
        generation=accepted_prestate.generation + 1,
        cluster=plan.cluster,
        services=plan.services,
        coordinators=plan.coordinators,
        redundancy_mode=plan.redundancy_mode,
        storage_engine=plan.storage_engine,
        retired=False,
    )
    accepted_file = advance_accepted_state(ctx, accepted_prestate, accepted_target)
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
        accepted_generation=accepted_target.generation,
    )
    return OperationCommandResult(
        operation="add-service",
        stage="finalize",
        status="finalized",
        details={
            "operation_id": operation_id,
            "verified": True,
            "coolify_status": verification.coolify_status,
            "accepted_state_path": str(accepted_file),
            "accepted_generation": accepted_target.generation,
            "coordinators_changed": coordinator_set_changed(accepted_prestate.coordinators, plan.coordinators),
            "consumer_contract_changed": coordinator_set_changed(accepted_prestate.coordinators, plan.coordinators),
            "hub_fdb_rectification_required": coordinator_set_changed(accepted_prestate.coordinators, plan.coordinators),
            "rebirth": not accepted_prestate.services,
        },
    )


def plan_from_operation(ctx: FdbContext, network: str, operation_id: str) -> tuple[AddServicePlan, dict[str, Any]]:
    op = require_operation(ctx, network, operation_id)
    if op.get("kind") != "add-service":
        raise ValueError("operation kind is not add-service")
    target = _mapping(op["target"])
    accepted_prestate = accepted_state_from_wire(_mapping(target["accepted_prestate"]))
    request = _request_from_wire(target["request"])
    return _plan_with_runtime_connection(build_add_service_plan(accepted_prestate, request), _mapping(op.get("deployment_result") or {})), op


def plan_from_accepted(accepted: AcceptedClusterState) -> AddServicePlan:
    if len(accepted.services) < 2:
        raise ValueError("multi-service accepted plan requires at least two services")
    # added_service is not semantically used by cluster-state proof; choose one
    # accepted service only to satisfy the typed plan container.
    return AddServicePlan(
        network=accepted.network,
        cluster=accepted.cluster,
        services=accepted.services,
        coordinators=accepted.coordinators,
        source_coordinators=accepted.coordinators,
        redundancy_mode=accepted.redundancy_mode,
        storage_engine=accepted.storage_engine,
        cluster_file_contents=render_cluster_file(accepted.cluster, accepted.coordinators),
        source_cluster_file_contents=render_cluster_file(accepted.cluster, accepted.coordinators),
        added_service=accepted.services[-1],
    )



def _plan_with_runtime_connection(plan: AddServicePlan, deployment_result: Mapping[str, Any]) -> AddServicePlan:
    connection = str(deployment_result.get("connection_string") or "").strip()
    if not connection:
        return plan
    parsed = parse_cluster_file(connection)
    expected = tuple(sorted((item.endpoint for item in plan.coordinators), key=lambda item: item.encode("utf-8")))
    if parsed.coordinator_addresses != expected:
        raise FdbControlError(
            code="FDB_COORDINATOR_TARGET_MISMATCH",
            message="stored add-service coordinator result does not match the frozen target",
            module_id="FDB-OFM-APP-005",
            retry_class="inspect-first",
        )
    return replace(plan, cluster=parsed.cluster, cluster_file_contents=connection)


def _birth_plan_from_add(plan: AddServicePlan) -> BirthPlan:
    if len(plan.services) != 1 or len(plan.coordinators) != 1:
        raise ValueError("accepted-empty add-service rebirth requires exactly one service and one coordinator")
    return BirthPlan(
        network=plan.network,
        cluster=plan.cluster,
        services=plan.services,
        coordinators=plan.coordinators,
        redundancy_mode=plan.redundancy_mode,
        storage_engine=plan.storage_engine,
        cluster_file_contents=plan.cluster_file_contents,
    )


def _render_add_descriptor(
    plan: AddServicePlan,
    deployment: AddServiceDeployment,
    *,
    rebirth: bool,
):
    if rebirth:
        birth = _birth_plan_from_add(plan)
        return render_birth_service_descriptor(
            birth,
            plan.added_service,
            image=deployment.image,
            data_root=deployment.data_root,
        )
    return render_add_service_descriptor(
        plan,
        plan.added_service,
        image=deployment.image,
        data_root=deployment.data_root,
    )

def _request_to_wire(request: AddServiceRequest) -> dict[str, Any]:
    item = request.service
    return {
        "network": request.network,
        "service": {
            "service_id": item.service_id,
            "host_id": item.host_id,
            "address": item.address,
            "port": item.port,
            "machine_id": item.machine_id,
            "zone_id": item.zone_id,
        },
    }


def _request_from_wire(raw: object) -> AddServiceRequest:
    payload = _mapping(raw)
    return AddServiceRequest(network=str(payload["network"]), service=ServicePlacement(**_mapping(payload["service"])))


def _deployment_to_wire(value: AddServiceDeployment, *, environment_name: str) -> dict[str, Any]:
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


def _deployment_from_wire(raw: object) -> AddServiceDeployment:
    return AddServiceDeployment(**_mapping(raw))


def _context_to_wire(value: object) -> dict[str, Any]:
    return {
        "project_uuid": str(getattr(value, "project_uuid")),
        "environment_name": str(getattr(value, "environment_name")),
        "environment_uuid": getattr(value, "environment_uuid"),
        "server_uuid": str(getattr(value, "server_uuid")),
        "destination_uuid": getattr(value, "destination_uuid"),
    }


def _context_from_wire(raw: object) -> CoolifyContext:
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
