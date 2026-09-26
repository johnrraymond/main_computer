from __future__ import annotations

import time
from dataclasses import dataclass

from .common.coolify import CoolifyClient, find_service, get_service, get_service_logs, service_health_status
from .common.coordinators import coordinator_set_changed
from .common.models import BirthPlan, FdbContext
from .common.privates import load_private_infrastructure, resolve_host_binding
from .common.service_descriptors import birth_observer_subservice_name, birth_proof_marker


@dataclass(frozen=True, slots=True)
class LiveBirthVerification:
    verified: bool
    service_id: str
    host_id: str
    coolify_service_name: str
    coolify_service_uuid: str | None
    coolify_status: str
    reason: str


def verify_birth(
    ctx: FdbContext,
    plan: BirthPlan,
    *,
    service_name: str,
    service_uuid: str | None = None,
    client_factory=CoolifyClient,
    wait_timeout_s: float = 0.0,
) -> LiveBirthVerification:
    """Verify birth using the observer's exact FDB proof marker.

    Coolify service status is used only to establish that the deployment is
    present/running. The actual FDB birth claim comes from the observer
    sub-service, whose startup loop runs the full status-json assertion and
    writes a deterministic marker only after that assertion succeeds.
    """

    if len(plan.services) != 1:
        raise ValueError("birth verification currently requires exactly one initial service")
    service = plan.services[0]
    private_doc = load_private_infrastructure(ctx)
    binding = resolve_host_binding(private_doc, plan.network, service.host_id, base_dir=ctx.private_state_path.parent)
    client = client_factory(binding)
    resolved_uuid = str(service_uuid or "").strip()
    if not resolved_uuid:
        resolved_uuid, _ = find_service(client, service_name)
    if not resolved_uuid:
        return LiveBirthVerification(False, service.service_id, service.host_id, service_name, None, "missing", "coolify-service-missing")

    marker = birth_proof_marker(plan, service)
    observer_name = birth_observer_subservice_name(service.service_id)
    deadline = time.monotonic() + max(0.0, wait_timeout_s)
    last_status = "unknown"

    while True:
        detail = get_service(client, resolved_uuid)
        last_status = service_health_status(detail)
        if detail is None:
            reason = "coolify-service-missing"
        elif not last_status.startswith("running"):
            reason = "coolify-service-not-running"
        else:
            logs = get_service_logs(
                client,
                resolved_uuid,
                sub_service_name=observer_name,
                lines=200,
            )
            if _contains_exact_marker(logs, marker):
                return LiveBirthVerification(
                    True,
                    service.service_id,
                    service.host_id,
                    service_name,
                    resolved_uuid,
                    last_status,
                    "fdb-observer-proof-satisfied",
                )
            reason = "fdb-observer-proof-not-yet-observed"

        if time.monotonic() >= deadline:
            return LiveBirthVerification(
                False,
                service.service_id,
                service.host_id,
                service_name,
                resolved_uuid,
                last_status,
                reason,
            )
        time.sleep(5.0)


def _contains_exact_marker(logs: str, marker: str) -> bool:
    expected = str(marker).strip()
    return any(line.strip() == expected for line in str(logs or "").splitlines())

@dataclass(frozen=True, slots=True)
class LiveAddServiceVerification:
    verified: bool
    service_id: str
    host_id: str
    coolify_service_name: str
    coolify_service_uuid: str | None
    coolify_status: str
    reason: str


@dataclass(frozen=True, slots=True)
class LiveClusterVerification:
    verified: bool
    proof_service_id: str | None
    coolify_service_uuid: str | None
    coolify_status: str
    reason: str


def verify_add_service(
    ctx: FdbContext,
    plan: object,
    *,
    service_name: str,
    service_uuid: str | None = None,
    client_factory=CoolifyClient,
    wait_timeout_s: float = 0.0,
) -> LiveAddServiceVerification:
    """Verify one add-service through the new service observer's FDB proof.

    accepted-empty -> one-service add is a first-service rebirth.  That service
    uses the birth descriptor/proof because there is no live source coordinator
    set to join through.
    """

    from .common.service_descriptors import add_service_proof_marker

    service = getattr(plan, "added_service")
    source_coordinators = tuple(getattr(plan, "source_coordinators", getattr(plan, "coordinators")))
    if not source_coordinators:
        birth_plan = BirthPlan(
            network=getattr(plan, "network"),
            cluster=getattr(plan, "cluster"),
            services=tuple(getattr(plan, "services")),
            coordinators=tuple(getattr(plan, "coordinators")),
            redundancy_mode=getattr(plan, "redundancy_mode"),
            storage_engine=getattr(plan, "storage_engine"),
            cluster_file_contents=str(getattr(plan, "cluster_file_contents")),
        )
        birth = verify_birth(
            ctx,
            birth_plan,
            service_name=service_name,
            service_uuid=service_uuid,
            client_factory=client_factory,
            wait_timeout_s=wait_timeout_s,
        )
        return LiveAddServiceVerification(
            birth.verified,
            birth.service_id,
            birth.host_id,
            birth.coolify_service_name,
            birth.coolify_service_uuid,
            birth.coolify_status,
            "fdb-add-service-proof-satisfied" if birth.verified else f"fdb-add-service-rebirth:{birth.reason}",
        )
    private_doc = load_private_infrastructure(ctx)
    binding = resolve_host_binding(private_doc, plan.network, service.host_id, base_dir=ctx.private_state_path.parent)
    client = client_factory(binding)
    resolved_uuid = str(service_uuid or "").strip()
    if not resolved_uuid:
        resolved_uuid, _ = find_service(client, service_name)
    if not resolved_uuid:
        return LiveAddServiceVerification(False, service.service_id, service.host_id, service_name, None, "missing", "coolify-service-missing")

    marker = add_service_proof_marker(plan, service)
    observer_name = birth_observer_subservice_name(service.service_id)
    deadline = time.monotonic() + max(0.0, wait_timeout_s)
    last_status = "unknown"
    while True:
        detail = get_service(client, resolved_uuid)
        last_status = service_health_status(detail)
        if detail is None:
            reason = "coolify-service-missing"
        elif not last_status.startswith("running"):
            reason = "coolify-service-not-running"
        else:
            logs = get_service_logs(client, resolved_uuid, sub_service_name=observer_name, lines=200)
            if _contains_exact_marker(logs, marker):
                source_coordinators = tuple(getattr(plan, "source_coordinators", getattr(plan, "coordinators")))
                if coordinator_set_changed(source_coordinators, tuple(getattr(plan, "coordinators"))):
                    guardian = verify_coordinator_guardian(ctx, plan, client_factory=client_factory)
                    if not guardian.verified:
                        reason = f"coordinator-guardian-not-verified:{guardian.reason}"
                    else:
                        return LiveAddServiceVerification(
                            True,
                            service.service_id,
                            service.host_id,
                            service_name,
                            resolved_uuid,
                            last_status,
                            "fdb-add-service-proof-satisfied",
                        )
                else:
                    return LiveAddServiceVerification(
                        True,
                        service.service_id,
                        service.host_id,
                        service_name,
                        resolved_uuid,
                        last_status,
                        "fdb-add-service-proof-satisfied",
                    )
            reason = "fdb-add-service-proof-not-yet-observed"
        if time.monotonic() >= deadline:
            return LiveAddServiceVerification(
                False,
                service.service_id,
                service.host_id,
                service_name,
                resolved_uuid,
                last_status,
                reason,
            )
        time.sleep(5.0)



def verify_coordinator_guardian(
    ctx: FdbContext,
    plan: object,
    *,
    client_factory=CoolifyClient,
) -> LiveClusterVerification:
    from .common.service_descriptors import (
        coordinator_guardian_proof_marker,
        coordinator_guardian_service_name,
        coordinator_guardian_subservice_name,
    )

    coordinators = tuple(getattr(plan, "coordinators"))
    if not coordinators:
        return LiveClusterVerification(False, None, None, "missing", "coordinator-target-empty")
    guardian_host = sorted(coordinators, key=lambda item: item.service_id.encode("utf-8"))[0].host_id
    private_doc = load_private_infrastructure(ctx)
    binding = resolve_host_binding(private_doc, plan.network, guardian_host, base_dir=ctx.private_state_path.parent)
    client = client_factory(binding)
    service_name = coordinator_guardian_service_name(plan.network)
    service_uuid, _ = find_service(client, service_name)
    if not service_uuid:
        return LiveClusterVerification(False, None, None, "missing", "coordinator-guardian-missing")
    detail = get_service(client, service_uuid)
    status = service_health_status(detail)
    if detail is None or not status.startswith("running"):
        return LiveClusterVerification(False, None, service_uuid, status, "coordinator-guardian-not-running")
    marker = coordinator_guardian_proof_marker(plan, str(getattr(plan, "cluster_file_contents")))
    logs = get_service_logs(
        client,
        service_uuid,
        sub_service_name=coordinator_guardian_subservice_name(),
        lines=400,
    )
    if not _contains_exact_marker(logs, marker):
        return LiveClusterVerification(False, None, service_uuid, status, "coordinator-guardian-proof-not-observed")
    return LiveClusterVerification(True, None, service_uuid, status, "fdb-coordinator-guardian-proof-satisfied")

def verify_accepted_cluster(
    ctx: FdbContext,
    plan: object,
    *,
    client_factory=CoolifyClient,
) -> LiveClusterVerification:
    """Find a current-topology proof emitted by any accepted service observer.

    The newest add-service observer emits a proof over the whole accepted
    service set. We search accepted services without assuming which service was
    added last.
    """

    # Newer accepted generations keep one deterministic coordinator guardian.
    # If it exists, it is authoritative for fresh topology proof.  If absent,
    # fall back to legacy per-service observer markers so pre-migration accepted
    # state remains inspectable.
    guardian = verify_coordinator_guardian(ctx, plan, client_factory=client_factory)
    if guardian.coolify_service_uuid is not None:
        return guardian

    from .common.service_descriptors import cluster_state_proof_marker

    marker = cluster_state_proof_marker(plan)
    private_doc = load_private_infrastructure(ctx)
    best_status = "missing"
    for service in reversed(tuple(getattr(plan, "services"))):
        binding = resolve_host_binding(private_doc, plan.network, service.host_id, base_dir=ctx.private_state_path.parent)
        client = client_factory(binding)
        service_name = f"main-computer-{service.service_id}"
        service_uuid, _ = find_service(client, service_name)
        if not service_uuid:
            continue
        detail = get_service(client, service_uuid)
        status = service_health_status(detail)
        best_status = status
        if not status.startswith("running"):
            continue
        logs = get_service_logs(
            client,
            service_uuid,
            sub_service_name=birth_observer_subservice_name(service.service_id),
            lines=200,
        )
        if _contains_exact_marker(logs, marker):
            return LiveClusterVerification(
                True,
                service.service_id,
                service_uuid,
                status,
                "fdb-cluster-state-proof-satisfied",
            )
    return LiveClusterVerification(
        False,
        None,
        None,
        best_status,
        "fdb-cluster-state-proof-not-observed",
    )


@dataclass(frozen=True, slots=True)
class LiveRemoveServiceVerification:
    verified: bool
    service_id: str
    host_id: str
    target_service_name: str
    target_service_uuid: str
    target_status: str
    helper_service_name: str
    helper_service_uuid: str | None
    helper_status: str
    reason: str


def verify_remove_service(
    ctx: FdbContext,
    plan: object,
    *,
    target_service_name: str,
    target_service_uuid: str,
    helper_service_name: str,
    helper_service_uuid: str | None = None,
    client_factory=CoolifyClient,
    wait_timeout_s: float = 0.0,
) -> LiveRemoveServiceVerification:
    """Verify that the exact target deployment is gone and FDB poststate proof exists."""

    from .common.evacuation import removal_complete_proof_marker
    from .common.service_descriptors import remove_helper_subservice_name

    target = getattr(plan, "removed_service")
    private_doc = load_private_infrastructure(ctx)
    binding = resolve_host_binding(private_doc, plan.network, target.host_id, base_dir=ctx.private_state_path.parent)
    client = client_factory(binding)

    if not tuple(getattr(plan, "services")):
        from .common.service_descriptors import coordinator_guardian_service_name

        deadline = time.monotonic() + max(0.0, wait_timeout_s)
        last_target_status = "unknown"
        while True:
            target_detail = get_service(client, target_service_uuid)
            last_target_status = service_health_status(target_detail)
            guardian_uuid, _ = find_service(client, coordinator_guardian_service_name(plan.network))
            if target_detail is None and not guardian_uuid:
                return LiveRemoveServiceVerification(
                    True,
                    target.service_id,
                    target.host_id,
                    target_service_name,
                    target_service_uuid,
                    "missing",
                    "",
                    None,
                    "not-required",
                    "fdb-full-deletion-proof-satisfied",
                )
            reason = (
                "target-coolify-service-still-present"
                if target_detail is not None
                else "coordinator-guardian-still-present"
            )
            if time.monotonic() >= deadline:
                return LiveRemoveServiceVerification(
                    False,
                    target.service_id,
                    target.host_id,
                    target_service_name,
                    target_service_uuid,
                    last_target_status,
                    "",
                    None,
                    "not-required",
                    reason,
                )
            time.sleep(5.0)

    resolved_helper_uuid = str(helper_service_uuid or "").strip()
    if not resolved_helper_uuid:
        resolved_helper_uuid, _ = find_service(client, helper_service_name)
    deadline = time.monotonic() + max(0.0, wait_timeout_s)
    last_helper_status = "missing"
    last_target_status = "unknown"
    marker = removal_complete_proof_marker(plan)

    while True:
        target_detail = get_service(client, target_service_uuid)
        last_target_status = service_health_status(target_detail)
        if target_detail is not None:
            reason = "target-coolify-service-still-present"
        elif not resolved_helper_uuid:
            reason = "removal-helper-missing"
        else:
            helper_detail = get_service(client, resolved_helper_uuid)
            last_helper_status = service_health_status(helper_detail)
            if helper_detail is None:
                reason = "removal-helper-missing"
            elif not last_helper_status.startswith("running"):
                reason = "removal-helper-not-running"
            else:
                logs = get_service_logs(
                    client,
                    resolved_helper_uuid,
                    sub_service_name=remove_helper_subservice_name(),
                    lines=400,
                )
                if _contains_exact_marker(logs, marker):
                    source_coordinators = tuple(getattr(plan, "source_coordinators", getattr(plan, "coordinators")))
                    if coordinator_set_changed(source_coordinators, tuple(getattr(plan, "coordinators"))):
                        guardian = verify_coordinator_guardian(ctx, plan, client_factory=client_factory)
                        if not guardian.verified:
                            reason = f"coordinator-guardian-not-verified:{guardian.reason}"
                        else:
                            return LiveRemoveServiceVerification(
                                True, target.service_id, target.host_id, target_service_name, target_service_uuid,
                                "missing", helper_service_name, resolved_helper_uuid, last_helper_status,
                                "fdb-remove-service-proof-satisfied",
                            )
                    else:
                        return LiveRemoveServiceVerification(
                            True,
                            target.service_id,
                            target.host_id,
                            target_service_name,
                            target_service_uuid,
                            "missing",
                            helper_service_name,
                            resolved_helper_uuid,
                            last_helper_status,
                            "fdb-remove-service-proof-satisfied",
                        )
                reason = "fdb-remove-service-proof-not-yet-observed"
        if time.monotonic() >= deadline:
            return LiveRemoveServiceVerification(
                False,
                target.service_id,
                target.host_id,
                target_service_name,
                target_service_uuid,
                last_target_status,
                helper_service_name,
                resolved_helper_uuid or None,
                last_helper_status,
                reason,
            )
        time.sleep(5.0)
