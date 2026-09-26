from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Mapping

from .add_service import plan_from_accepted
from .common.canonical import canonical_json
from .common.cluster_file import parse_cluster_file, render_cluster_file
from .common.coolify import (
    CoolifyClient,
    CoolifyContext,
    create_service,
    delete_service,
    deploy_service,
    ensure_environment,
    find_service,
    get_service,
    get_service_logs,
    resolve_context,
    service_health_status,
    update_service,
    wait_for_missing,
    wait_for_running,
)
from .common.coordinator_runtime import apply_coordinator_overlay
from .common.coordinators import coordinator_set_changed, derive_topology_coordinators
from .common.errors import FdbControlError
from .common.evacuation import (
    removal_complete_proof_marker,
    removal_drain_proof_marker,
    require_supported_removal,
)
from .common.hashing import sha256_bytes
from .common.models import (
    AcceptedClusterState,
    BirthPlan,
    FdbContext,
    OperationCommandResult,
    RemoveServiceDeployment,
    RemoveServicePlan,
    RemoveServiceRequest,
)
from .common.privates import load_private_infrastructure, public_binding_ref, resolve_host_binding
from .common.service_descriptors import (
    coordinator_guardian_service_name,
    remove_helper_subservice_name,
    render_remove_service_helper_descriptor,
)
from .common.state import (
    REMOVE_SERVICE_OP_SCHEMA,
    accepted_state_from_wire,
    accepted_state_to_wire,
    advance_accepted_state,
    read_accepted_state,
    require_operation,
    update_operation,
    write_operation,
)
from .live_inspect import verify_accepted_cluster, verify_birth, verify_remove_service


def build_remove_service_plan(
    accepted: AcceptedClusterState,
    request: RemoveServiceRequest,
) -> RemoveServicePlan:
    if accepted.retired:
        raise FdbControlError(
            code="FDB_CLUSTER_RETIRED",
            message=f"FDB cluster {accepted.network!r} is retired",
            module_id="FDB-OFM-APP-006",
            retry_class="never",
        )
    if request.network != accepted.network:
        raise ValueError("remove-service request network does not match accepted cluster")
    target = next((item for item in accepted.services if item.service_id == request.service_id), None)
    if target is None:
        raise FdbControlError(
            code="FDB_REMOVE_SERVICE_NOT_ACCEPTED",
            message=f"service {request.service_id!r} is not in accepted FDB topology",
            module_id="FDB-OFM-APP-006",
            retry_class="inspect-first",
        )
    survivors = require_supported_removal(
        accepted,
        target,
        allow_full_deletion=request.allow_full_deletion,
    )
    target_coordinators = (
        derive_topology_coordinators(survivors, accepted.coordinators)
        if survivors
        else ()
    )
    source_cluster_file = render_cluster_file(accepted.cluster, accepted.coordinators)
    return RemoveServicePlan(
        network=accepted.network,
        cluster=accepted.cluster,
        services=survivors,
        coordinators=target_coordinators,
        source_coordinators=accepted.coordinators,
        redundancy_mode=accepted.redundancy_mode,
        storage_engine=accepted.storage_engine,
        cluster_file_contents=source_cluster_file,
        source_cluster_file_contents=source_cluster_file,
        removed_service=target,
    )


def prep(
    ctx: FdbContext,
    request: RemoveServiceRequest,
    deployment: RemoveServiceDeployment,
    *,
    client_factory=CoolifyClient,
) -> OperationCommandResult:
    accepted = read_accepted_state(ctx, request.network)
    if accepted is None:
        raise FdbControlError(
            code="FDB_CLUSTER_UNBORN",
            message=f"no accepted FDB cluster exists for {request.network!r}",
            module_id="FDB-OFM-APP-006",
            retry_class="inspect-first",
        )
    plan = build_remove_service_plan(accepted, request)

    # Read-only preflight: the currently accepted topology must still have an
    # FDB observer proof before a destructive contraction can be prepared.
    if len(accepted.services) == 1:
        birth_plan = BirthPlan(
            network=accepted.network,
            cluster=accepted.cluster,
            services=accepted.services,
            coordinators=accepted.coordinators,
            redundancy_mode=accepted.redundancy_mode,
            storage_engine=accepted.storage_engine,
            cluster_file_contents=render_cluster_file(accepted.cluster, accepted.coordinators),
        )
        current_proof = verify_accepted_cluster(
            ctx,
            birth_plan,
            client_factory=client_factory,
        )
        if not current_proof.verified:
            current_proof = verify_birth(
                ctx,
                birth_plan,
                service_name=f"main-computer-{accepted.services[0].service_id}",
                client_factory=client_factory,
            )
    else:
        current_proof = verify_accepted_cluster(
            ctx,
            plan_from_accepted(accepted),
            client_factory=client_factory,
        )
    if not current_proof.verified:
        raise FdbControlError(
            code="FDB_REMOVE_SERVICE_PRESTATE_NOT_VERIFIED",
            message=(
                "accepted FDB topology could not be independently verified before removal: "
                f"{current_proof.reason}; status={current_proof.coolify_status}"
            ),
            module_id="FDB-OFM-APP-006",
            retry_class="inspect-first",
        )

    private_doc = load_private_infrastructure(ctx)
    binding = resolve_host_binding(private_doc, plan.network, plan.removed_service.host_id, base_dir=ctx.private_state_path.parent)
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

    target_service_name = f"main-computer-{plan.removed_service.service_id}"
    target_uuid, target_detail = find_service(client, target_service_name)
    if not target_uuid:
        raise FdbControlError(
            code="FDB_REMOVE_SERVICE_TARGET_MISSING",
            message=(
                f"accepted service {plan.removed_service.service_id!r} has no Coolify deployment; "
                "temporary absence is not intentional removal"
            ),
            module_id="FDB-OFM-APP-006",
            retry_class="inspect-first",
        )
    target_status = service_health_status(target_detail)
    if not target_status.startswith("running"):
        raise FdbControlError(
            code="FDB_REMOVE_SERVICE_TARGET_NOT_RUNNING",
            message=(
                f"accepted service {plan.removed_service.service_id!r} is not running; "
                f"status={target_status}"
            ),
            module_id="FDB-OFM-APP-006",
            retry_class="inspect-first",
        )

    prestate = accepted_state_to_wire(accepted)
    seed = {
        "request": _request_to_wire(request),
        "deployment": _deployment_to_wire(deployment, environment_name=environment_name),
        "coolify_context": _context_to_wire(context),
        "binding": public_binding_ref(binding),
        "target_service_name": target_service_name,
        "target_service_uuid": target_uuid,
        "accepted_prestate": prestate,
        "accepted_prestate_sha256": sha256_bytes(canonical_json(prestate)).digest,
        "source_coordinators": [item.service_id for item in accepted.coordinators],
        "target_coordinators": [item.service_id for item in plan.coordinators],
        "coordinators_changed": coordinator_set_changed(accepted.coordinators, plan.coordinators),
        "endpoint_exclusion_cleared": True,
    }
    operation_id = f"fdb-remove-{request.network}-{sha256_bytes(canonical_json(seed)).digest[:16]}"
    full_deletion = not plan.services
    helper_service_name = ""
    if not full_deletion:
        helper_service_name = (
            f"main-computer-{request.network}-remove-{request.service_id}-"
            f"{operation_id.rsplit('-', 1)[-1][:8]}"
        )
        helper_uuid, _ = find_service(client, helper_service_name)
        if helper_uuid:
            raise FdbControlError(
                code="FDB_REMOVE_SERVICE_HELPER_EXISTS",
                message=(
                    f"removal helper {helper_service_name!r} already exists; inspect the prior removal "
                    "before preparing another operation"
                ),
                module_id="FDB-OFM-APP-006",
                retry_class="inspect-first",
            )
    target = dict(seed)
    target["full_deletion"] = full_deletion
    target["helper_service_name"] = helper_service_name
    target["drain_proof"] = None if full_deletion else removal_drain_proof_marker(plan)
    target["complete_proof"] = None if full_deletion else removal_complete_proof_marker(plan)

    write_operation(
        ctx,
        {
            "schema": REMOVE_SERVICE_OP_SCHEMA,
            "operation_id": operation_id,
            "network": request.network,
            "kind": "remove-service",
            "stage": "prepared",
            "target": target,
        },
    )
    return OperationCommandResult(
        operation="remove-service",
        stage="prep",
        status="prepared",
        details={
            "operation_id": operation_id,
            "service_id": plan.removed_service.service_id,
            "host_id": plan.removed_service.host_id,
            "service_uuid": target_uuid,
            "service_endpoint": plan.removed_service.endpoint,
            "binding_slot": binding.slot,
            "accepted_generation": accepted.generation,
            "target_generation": accepted.generation + 1,
            "source_coordinators": [item.service_id for item in accepted.coordinators],
            "target_coordinators": [item.endpoint for item in plan.coordinators],
            "target_coordinator_services": [item.service_id for item in plan.coordinators],
            "coordinators_changed": coordinator_set_changed(accepted.coordinators, plan.coordinators),
            "full_deletion": full_deletion,
            "safe_withdrawal": (
                "explicit-full-deletion-no-drain"
                if full_deletion
                else "coordinator-first-then-fdbcli-exclude-blocking"
            ),
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
    if op.get("kind") != "remove-service":
        raise ValueError("operation kind is not remove-service")
    if op.get("stage") == "finalized":
        return OperationCommandResult("remove-service", "do", "already-finalized", {"operation_id": operation_id})
    if op.get("stage") == "removed":
        return OperationCommandResult("remove-service", "do", "removed", {"operation_id": operation_id})

    target = _mapping(op["target"])
    accepted_prestate = accepted_state_from_wire(_mapping(target["accepted_prestate"]))
    current = read_accepted_state(ctx, network)
    if current != accepted_prestate:
        raise FdbControlError(
            code="FDB_REMOVE_SERVICE_PRESTATE_CHANGED",
            message="accepted FDB state changed after remove-service prep",
            module_id="FDB-OFM-APP-006",
            operation_id=operation_id,
            retry_class="inspect-first",
        )
    request = _request_from_wire(target["request"])
    deployment = _deployment_from_wire(target["deployment"])
    plan = _plan_with_runtime_connection(
        build_remove_service_plan(accepted_prestate, request),
        _mapping(op.get("deployment_result") or {}),
    )

    private_doc = load_private_infrastructure(ctx)
    binding = resolve_host_binding(private_doc, plan.network, plan.removed_service.host_id, base_dir=ctx.private_state_path.parent)
    client = client_factory(binding)
    context = ensure_environment(client, _context_from_wire(target["coolify_context"]))

    frozen_target_uuid = str(target["target_service_uuid"])
    target_detail = get_service(client, frozen_target_uuid)
    if target_detail is not None:
        actual_name = str(target_detail.get("name") or target_detail.get("service_name") or "")
        if actual_name and actual_name != str(target["target_service_name"]):
            raise FdbControlError(
                code="FDB_REMOVE_SERVICE_TARGET_IDENTITY_CHANGED",
                message="the frozen Coolify UUID now names a different service",
                module_id="FDB-OFM-APP-006",
                operation_id=operation_id,
                retry_class="inspect-first",
            )
    elif op.get("stage") == "prepared":
        raise FdbControlError(
            code="FDB_REMOVE_SERVICE_TARGET_MISSING",
            message="target Coolify service disappeared before safe FDB exclusion completed",
            module_id="FDB-OFM-APP-006",
            operation_id=operation_id,
            retry_class="inspect-first",
        )

    if not plan.services:
        if not request.allow_full_deletion:
            raise FdbControlError(
                code="FDB_REMOVE_SERVICE_FULL_DELETION_REQUIRES_ACK",
                message="final-service deletion lost its explicit full-deletion acknowledgement",
                module_id="FDB-OFM-APP-006",
                operation_id=operation_id,
                retry_class="never",
            )

        # There is nowhere to evacuate the final FDB copy.  The explicit
        # acknowledgement authorizes destructive deletion of the exact frozen
        # service instead of pretending a blocking exclude can make it safe.
        deployment_result = {
            "full_deletion": True,
            "target_service_uuid": frozen_target_uuid,
            "target_service_name": str(target["target_service_name"]),
            "coolify_context": _context_to_wire(context),
            "coordinators_changed": bool(accepted_prestate.coordinators),
            "connection_string": "",
            "guardian_service_uuid": None,
            "guardian_service_name": None,
        }
        update_operation(ctx, network, operation_id, stage="removing", deployment_result=deployment_result)
        if get_service(client, frozen_target_uuid) is not None:
            delete_service(client, frozen_target_uuid)
        wait_for_missing(client, frozen_target_uuid, timeout_s=300.0, poll_s=5.0)

        guardian_name = coordinator_guardian_service_name(network)
        guardian_uuid, _ = find_service(client, guardian_name)
        if guardian_uuid:
            delete_service(client, guardian_uuid)
            wait_for_missing(client, guardian_uuid, timeout_s=300.0, poll_s=5.0)
            deployment_result["guardian_service_uuid"] = guardian_uuid
            deployment_result["guardian_service_name"] = guardian_name

        update_operation(ctx, network, operation_id, stage="removed", deployment_result=deployment_result)
        return OperationCommandResult(
            operation="remove-service",
            stage="do",
            status="removed",
            details={
                "operation_id": operation_id,
                "service_id": plan.removed_service.service_id,
                "service_uuid": frozen_target_uuid,
                "service_endpoint": plan.removed_service.endpoint,
                "full_deletion": True,
                "full_deletion_authorized": True,
                "safe_withdrawal_verified": False,
                "coolify_service_deleted": True,
                "endpoint_exclusion_cleared": False,
                "coordinators_changed": bool(accepted_prestate.coordinators),
                "target_coordinators": [],
            },
        )

    # Coordinator authority is a derived overlay.  If the retiring service is
    # part of that overlay, move authority to the frozen surviving set before
    # any exclusion or deletion is allowed.
    overlay = apply_coordinator_overlay(
        ctx,
        plan,
        environment_name=str(target["deployment"].get("environment_name") or f"{network}-fdb"),
        image=deployment.image,
        force_deploy=deployment.force_deploy,
        operation_id=operation_id,
        client_factory=client_factory,
    )
    plan = _plan_with_runtime_connection(plan, overlay)
    helper_name = str(target["helper_service_name"])
    descriptor = render_remove_service_helper_descriptor(
        plan,
        helper_service_name=helper_name,
        image=deployment.image,
    )

    helper_uuid, _ = find_service(client, helper_name)
    action = "updated"
    if helper_uuid:
        update_service(client, helper_uuid, service_name=descriptor.service_name, compose_b64=descriptor.compose_b64)
    else:
        action = "created"
        helper_uuid = create_service(
            client,
            context,
            service_name=descriptor.service_name,
            description=descriptor.description,
            compose_b64=descriptor.compose_b64,
        )
    deploy_service(client, helper_uuid, force=deployment.force_deploy)
    wait_for_running(client, helper_uuid, timeout_s=300.0, poll_s=5.0)
    deployment_result = {
        "helper_service_uuid": helper_uuid,
        "helper_service_name": helper_name,
        "helper_action": action,
        "target_service_uuid": frozen_target_uuid,
        "target_service_name": str(target["target_service_name"]),
        "coolify_context": _context_to_wire(context),
        **overlay,
    }
    update_operation(ctx, network, operation_id, stage="evacuating", deployment_result=deployment_result)

    _wait_for_helper_marker(
        client,
        helper_uuid,
        removal_drain_proof_marker(plan),
        timeout_s=300.0,
        failure_code="FDB_REMOVE_SERVICE_DRAIN_TIMEOUT",
        operation_id=operation_id,
    )
    update_operation(ctx, network, operation_id, stage="drained", deployment_result=deployment_result)

    # The blocking FDB exclusion is the safety boundary.  Only after it has
    # completed do we remove the exact frozen Coolify service UUID.
    if get_service(client, frozen_target_uuid) is not None:
        delete_service(client, frozen_target_uuid)
    wait_for_missing(client, frozen_target_uuid, timeout_s=300.0, poll_s=5.0)
    update_operation(ctx, network, operation_id, stage="removing", deployment_result=deployment_result)

    _wait_for_helper_marker(
        client,
        helper_uuid,
        removal_complete_proof_marker(plan),
        timeout_s=300.0,
        failure_code="FDB_REMOVE_SERVICE_VERIFY_TIMEOUT",
        operation_id=operation_id,
    )
    update_operation(ctx, network, operation_id, stage="removed", deployment_result=deployment_result)
    return OperationCommandResult(
        operation="remove-service",
        stage="do",
        status="removed",
        details={
            "operation_id": operation_id,
            "service_id": plan.removed_service.service_id,
            "service_uuid": frozen_target_uuid,
            "service_endpoint": plan.removed_service.endpoint,
            "helper_service_uuid": helper_uuid,
            "safe_withdrawal_verified": True,
            "coolify_service_deleted": True,
            "endpoint_exclusion_cleared": True,
            "coordinators_changed": bool(overlay["coordinators_changed"]),
            "target_coordinators": [item.service_id for item in plan.coordinators],
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
    if op.get("kind") != "remove-service":
        raise ValueError("operation kind is not remove-service")
    if op.get("stage") == "finalized":
        return OperationCommandResult("remove-service", "finalize", "finalized", {"operation_id": operation_id})
    if op.get("stage") != "removed":
        raise FdbControlError(
            code="FDB_REMOVE_SERVICE_NOT_REMOVED",
            message="remove-service finalize requires a completed safe withdrawal/removal do stage",
            module_id="FDB-OFM-APP-006",
            operation_id=operation_id,
            retry_class="exact-retry",
        )

    target = _mapping(op["target"])
    accepted_prestate = accepted_state_from_wire(_mapping(target["accepted_prestate"]))
    current = read_accepted_state(ctx, network)
    if current != accepted_prestate:
        raise FdbControlError(
            code="FDB_REMOVE_SERVICE_PRESTATE_CHANGED",
            message="accepted FDB state changed before remove-service finalize",
            module_id="FDB-OFM-APP-006",
            operation_id=operation_id,
            retry_class="inspect-first",
        )
    request = _request_from_wire(target["request"])
    result = _mapping(op.get("deployment_result") or {})
    plan = _plan_with_runtime_connection(build_remove_service_plan(accepted_prestate, request), result)
    verification = verify_remove_service(
        ctx,
        plan,
        target_service_name=str(target["target_service_name"]),
        target_service_uuid=str(target["target_service_uuid"]),
        helper_service_name=str(result.get("helper_service_name") or target["helper_service_name"]),
        helper_service_uuid=str(result.get("helper_service_uuid") or "") or None,
        client_factory=client_factory,
        wait_timeout_s=300.0,
    )
    if not verification.verified:
        raise FdbControlError(
            code="FDB_REMOVE_SERVICE_NOT_VERIFIED",
            message=(
                f"remove-service inspection did not prove the contraction: {verification.reason}; "
                f"helper_status={verification.helper_status}"
            ),
            module_id="FDB-OFM-APP-006",
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

    # The ordinary helper is operational scaffolding, not an FDB member.  A
    # full deletion has no helper because there is no surviving FDB process to
    # evacuate into or verify through.
    private_doc = load_private_infrastructure(ctx)
    binding = resolve_host_binding(private_doc, plan.network, plan.removed_service.host_id, base_dir=ctx.private_state_path.parent)
    client = client_factory(binding)
    if verification.helper_service_uuid:
        delete_service(client, verification.helper_service_uuid)
        wait_for_missing(client, verification.helper_service_uuid, timeout_s=300.0, poll_s=5.0)

    update_operation(
        ctx,
        network,
        operation_id,
        stage="finalized",
        verification={
            "verified": True,
            "reason": verification.reason,
            "target_service_missing": True,
            "helper_status": verification.helper_status,
        },
        accepted_state_path=str(accepted_file),
        accepted_generation=accepted_target.generation,
    )
    return OperationCommandResult(
        operation="remove-service",
        stage="finalize",
        status="finalized",
        details={
            "operation_id": operation_id,
            "verified": True,
            "accepted_state_path": str(accepted_file),
            "accepted_generation": accepted_target.generation,
            "removed_service_id": plan.removed_service.service_id,
            "removed_endpoint": plan.removed_service.endpoint,
            "full_deletion": not plan.services,
            "endpoint_exclusion_cleared": bool(plan.services),
            "coordinators_changed": coordinator_set_changed(accepted_prestate.coordinators, plan.coordinators),
            "consumer_contract_changed": coordinator_set_changed(accepted_prestate.coordinators, plan.coordinators),
            "hub_fdb_rectification_required": coordinator_set_changed(accepted_prestate.coordinators, plan.coordinators),
        },
    )


def plan_from_operation(ctx: FdbContext, network: str, operation_id: str) -> tuple[RemoveServicePlan, dict[str, Any]]:
    op = require_operation(ctx, network, operation_id)
    if op.get("kind") != "remove-service":
        raise ValueError("operation kind is not remove-service")
    target = _mapping(op["target"])
    accepted_prestate = accepted_state_from_wire(_mapping(target["accepted_prestate"]))
    request = _request_from_wire(target["request"])
    return _plan_with_runtime_connection(build_remove_service_plan(accepted_prestate, request), _mapping(op.get("deployment_result") or {})), op



def _plan_with_runtime_connection(plan: RemoveServicePlan, deployment_result: Mapping[str, Any]) -> RemoveServicePlan:
    connection = str(deployment_result.get("connection_string") or "").strip()
    if not connection:
        return plan
    parsed = parse_cluster_file(connection)
    expected = tuple(sorted((item.endpoint for item in plan.coordinators), key=lambda item: item.encode("utf-8")))
    if parsed.coordinator_addresses != expected:
        raise FdbControlError(
            code="FDB_COORDINATOR_TARGET_MISMATCH",
            message="stored remove-service coordinator result does not match the frozen target",
            module_id="FDB-OFM-APP-006",
            retry_class="inspect-first",
        )
    return replace(plan, cluster=parsed.cluster, cluster_file_contents=connection)

def _wait_for_helper_marker(
    client: CoolifyClient,
    helper_uuid: str,
    marker: str,
    *,
    timeout_s: float,
    failure_code: str,
    operation_id: str,
) -> None:
    deadline = time.monotonic() + max(0.0, timeout_s)
    last_status = "unknown"
    while True:
        detail = get_service(client, helper_uuid)
        last_status = service_health_status(detail)
        if detail is None:
            raise FdbControlError(
                code="FDB_REMOVE_SERVICE_HELPER_MISSING",
                message="removal helper disappeared before producing its FDB proof",
                module_id="FDB-OFM-FDB-007",
                operation_id=operation_id,
                retry_class="inspect-first",
            )
        logs = get_service_logs(
            client,
            helper_uuid,
            sub_service_name=remove_helper_subservice_name(),
            lines=400,
        )
        if _contains_exact_marker(logs, marker):
            return
        if time.monotonic() >= deadline:
            raise FdbControlError(
                code=failure_code,
                message=(
                    f"removal helper {helper_uuid} did not produce required FDB proof before timeout; "
                    f"last status={last_status}"
                ),
                module_id="FDB-OFM-FDB-007",
                operation_id=operation_id,
                retry_class="exact-retry",
            )
        time.sleep(5.0)


def _contains_exact_marker(logs: str, marker: str) -> bool:
    expected = str(marker).strip()
    return any(line.strip() == expected for line in str(logs or "").splitlines())


def _request_to_wire(value: RemoveServiceRequest) -> dict[str, Any]:
    return {
        "network": value.network,
        "service_id": value.service_id,
        "allow_full_deletion": value.allow_full_deletion,
    }


def _request_from_wire(raw: object) -> RemoveServiceRequest:
    payload = _mapping(raw)
    return RemoveServiceRequest(
        network=str(payload["network"]),
        service_id=str(payload["service_id"]),
        allow_full_deletion=bool(payload.get("allow_full_deletion", False)),
    )


def _deployment_to_wire(value: RemoveServiceDeployment, *, environment_name: str) -> dict[str, Any]:
    return {
        "project_uuid": value.project_uuid,
        "project_name": value.project_name,
        "environment_name": environment_name,
        "environment_uuid": value.environment_uuid,
        "server_uuid": value.server_uuid,
        "server_name": value.server_name,
        "destination_uuid": value.destination_uuid,
        "image": value.image,
        "force_deploy": value.force_deploy,
    }


def _deployment_from_wire(raw: object) -> RemoveServiceDeployment:
    return RemoveServiceDeployment(**_mapping(raw))


def _context_to_wire(value: CoolifyContext) -> dict[str, Any]:
    return {
        "project_uuid": value.project_uuid,
        "environment_name": value.environment_name,
        "environment_uuid": value.environment_uuid or "",
        "server_uuid": value.server_uuid,
        "destination_uuid": value.destination_uuid or "",
    }


def _context_from_wire(raw: object) -> CoolifyContext:
    value = _mapping(raw)
    return CoolifyContext(
        project_uuid=str(value["project_uuid"]),
        environment_name=str(value["environment_name"]),
        environment_uuid=str(value.get("environment_uuid") or "") or None,
        server_uuid=str(value["server_uuid"]),
        destination_uuid=str(value.get("destination_uuid") or "") or None,
    )


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("expected mapping")
    return dict(value)
