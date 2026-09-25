from __future__ import annotations

import time
from typing import Any, Callable

from .cluster_file import parse_cluster_file
from .coolify import (
    CoolifyClient,
    create_service,
    deploy_service,
    ensure_environment,
    find_service,
    get_service,
    get_service_logs,
    resolve_context,
    service_health_status,
    update_service,
    wait_for_running,
)
from .coordinators import coordinator_set_changed
from .errors import FdbControlError
from .privates import (
    load_private_infrastructure,
    resolve_controller_coordinates,
    resolve_host_binding,
)
from .service_descriptors import (
    COORDINATOR_CONNECTION_PREFIX,
    coordinator_guardian_proof_marker,
    coordinator_guardian_service_name,
    coordinator_guardian_subservice_name,
    coordinator_transition_proof_marker,
    render_coordinator_guardian_descriptor,
    render_coordinator_transition_descriptor,
)

ClientFactory = Callable[[object], CoolifyClient]


def apply_coordinator_overlay(
    ctx: object,
    plan: object,
    *,
    environment_name: str,
    image: str,
    force_deploy: bool,
    operation_id: str,
    client_factory: ClientFactory = CoolifyClient,
) -> dict[str, Any]:
    """Apply/verify a changed topology-derived coordinator overlay.

    An unchanged overlay has no deployment side effect.  When the derived target
    differs, coordinator authority moves before destructive membership work and
    a deterministic guardian remains as the fresh proof surface for the changed
    accepted topology.
    """

    coordinators = tuple(getattr(plan, "coordinators"))
    source_coordinators = tuple(getattr(plan, "source_coordinators", coordinators))
    if not coordinators:
        raise FdbControlError(
            code="FDB_COORDINATOR_TARGET_EMPTY",
            message="coordinator overlay requires at least one target coordinator",
            module_id="FDB-OFM-COORD-002",
            operation_id=operation_id,
            retry_class="never",
        )

    changed = coordinator_set_changed(source_coordinators, coordinators)
    source_connection = str(getattr(plan, "source_cluster_file_contents", getattr(plan, "cluster_file_contents")))
    connection_string = str(getattr(plan, "cluster_file_contents"))
    if not changed:
        return {
            "coordinators_changed": False,
            "connection_string": source_connection,
            "guardian_host_id": None,
            "guardian_service_name": None,
            "guardian_service_uuid": None,
            "guardian_action": "unchanged",
        }

    transition_already_recorded = connection_string != source_connection
    if transition_already_recorded:
        _validate_connection(plan, connection_string)

    guardian_host = sorted(coordinators, key=lambda item: item.service_id.encode("utf-8"))[0].host_id
    private_doc = load_private_infrastructure(ctx)
    binding = resolve_host_binding(
        private_doc,
        getattr(plan, "network"),
        guardian_host,
        base_dir=getattr(ctx, "private_state_path").parent,
    )
    coordinates = resolve_controller_coordinates(private_doc, getattr(plan, "network"), guardian_host)
    client = client_factory(binding)
    context = resolve_context(
        client,
        project_uuid=coordinates["project_uuid"],
        environment_name=str(environment_name or "").strip() or f"{getattr(plan, 'network')}-fdb",
        server_uuid=coordinates["server_uuid"],
        allow_create_environment=False,
    )
    context = ensure_environment(client, context)
    service_name = coordinator_guardian_service_name(getattr(plan, "network"))
    service_uuid, _ = find_service(client, service_name)
    action = "updated" if service_uuid else "created"

    if not transition_already_recorded:
        transition = render_coordinator_transition_descriptor(
            plan,
            service_name=service_name,
            image=image,
        )
        if service_uuid:
            update_service(client, service_uuid, service_name=service_name, compose_b64=transition.compose_b64)
        else:
            service_uuid = create_service(
                client,
                context,
                service_name=service_name,
                description=transition.description,
                compose_b64=transition.compose_b64,
            )
        deploy_service(client, service_uuid, force=force_deploy)
        wait_for_running(client, service_uuid, timeout_s=300.0, poll_s=5.0)
        connection_string = _wait_for_transition(
            client,
            service_uuid,
            coordinator_transition_proof_marker(plan),
            plan=plan,
            operation_id=operation_id,
            timeout_s=300.0,
        )

    guardian = render_coordinator_guardian_descriptor(
        plan,
        connection_string,
        service_name=service_name,
        image=image,
    )
    if service_uuid:
        update_service(client, service_uuid, service_name=service_name, compose_b64=guardian.compose_b64)
    else:
        service_uuid = create_service(
            client,
            context,
            service_name=service_name,
            description=guardian.description,
            compose_b64=guardian.compose_b64,
        )
    deploy_service(client, service_uuid, force=force_deploy)
    wait_for_running(client, service_uuid, timeout_s=300.0, poll_s=5.0)
    _wait_for_marker(
        client,
        service_uuid,
        coordinator_guardian_proof_marker(plan, connection_string),
        operation_id=operation_id,
        timeout_s=300.0,
    )
    return {
        "coordinators_changed": True,
        "connection_string": connection_string,
        "guardian_host_id": guardian_host,
        "guardian_service_name": service_name,
        "guardian_service_uuid": service_uuid,
        "guardian_action": action,
    }


def _wait_for_transition(
    client: CoolifyClient,
    service_uuid: str,
    marker: str,
    *,
    plan: object,
    operation_id: str,
    timeout_s: float,
) -> str:
    deadline = time.monotonic() + max(0.0, timeout_s)
    last_status = "unknown"
    while True:
        detail = get_service(client, service_uuid)
        last_status = service_health_status(detail)
        logs = ""
        if detail is not None and last_status.startswith("running"):
            logs = get_service_logs(
                client,
                service_uuid,
                sub_service_name=coordinator_guardian_subservice_name(),
                lines=400,
            )
            if _contains_exact_marker(logs, marker):
                connection = _connection_from_logs(logs)
                _validate_connection(plan, connection)
                return connection
        if time.monotonic() >= deadline:
            raise FdbControlError(
                code="FDB_COORDINATOR_TRANSITION_VERIFY_TIMEOUT",
                message=(
                    f"coordinator transition guardian {service_uuid} did not prove the frozen target; "
                    f"last status={last_status}"
                ),
                module_id="FDB-OFM-COORD-002",
                operation_id=operation_id,
                retry_class="inspect-first",
                effect_class="live-fdb",
            )
        time.sleep(5.0)


def _wait_for_marker(
    client: CoolifyClient,
    service_uuid: str,
    marker: str,
    *,
    operation_id: str,
    timeout_s: float,
) -> None:
    deadline = time.monotonic() + max(0.0, timeout_s)
    last_status = "unknown"
    while True:
        detail = get_service(client, service_uuid)
        last_status = service_health_status(detail)
        if detail is not None and last_status.startswith("running"):
            logs = get_service_logs(
                client,
                service_uuid,
                sub_service_name=coordinator_guardian_subservice_name(),
                lines=400,
            )
            if _contains_exact_marker(logs, marker):
                return
        if time.monotonic() >= deadline:
            raise FdbControlError(
                code="FDB_COORDINATOR_GUARDIAN_VERIFY_TIMEOUT",
                message=(
                    f"coordinator guardian {service_uuid} did not prove the frozen topology; "
                    f"last status={last_status}"
                ),
                module_id="FDB-OFM-COORD-002",
                operation_id=operation_id,
                retry_class="inspect-first",
                effect_class="live-fdb",
            )
        time.sleep(5.0)


def _connection_from_logs(logs: str) -> str:
    prefix = COORDINATOR_CONNECTION_PREFIX + " "
    matches = [line.strip()[len(prefix):].strip() for line in str(logs or "").splitlines() if line.strip().startswith(prefix)]
    if not matches or not matches[-1]:
        raise FdbControlError(
            code="FDB_COORDINATOR_CONNECTION_MISSING",
            message="coordinator transition proof did not emit the resulting FDB connection string",
            module_id="FDB-OFM-COORD-002",
            retry_class="inspect-first",
            effect_class="live-fdb",
        )
    return matches[-1]


def _validate_connection(plan: object, connection_string: str) -> None:
    try:
        parsed = parse_cluster_file(connection_string)
    except ValueError as exc:
        raise FdbControlError(
            code="FDB_COORDINATOR_CONNECTION_INVALID",
            message=f"resulting FDB coordinator connection string is invalid: {exc}",
            module_id="FDB-OFM-COORD-002",
            retry_class="inspect-first",
            effect_class="live-fdb",
        ) from exc
    expected_description = getattr(plan, "cluster").description
    if parsed.cluster.description != expected_description:
        raise FdbControlError(
            code="FDB_COORDINATOR_LINEAGE_CHANGED",
            message="coordinator transition changed the FDB cluster description",
            module_id="FDB-OFM-COORD-002",
            retry_class="inspect-first",
            effect_class="live-fdb",
        )
    expected = tuple(sorted((item.endpoint for item in tuple(getattr(plan, "coordinators"))), key=lambda x: x.encode("utf-8")))
    if parsed.coordinator_addresses != expected:
        raise FdbControlError(
            code="FDB_COORDINATOR_TARGET_MISMATCH",
            message=(
                f"resulting coordinator endpoints {parsed.coordinator_addresses!r} do not match frozen target {expected!r}"
            ),
            module_id="FDB-OFM-COORD-002",
            retry_class="inspect-first",
            effect_class="live-fdb",
        )


def _contains_exact_marker(logs: str, marker: str) -> bool:
    expected = str(marker).strip()
    return any(line.strip() == expected for line in str(logs or "").splitlines())
