from __future__ import annotations

from .models import AcceptedClusterState, DeploymentObservation, DriftReport, FdbStatusObservation


def classify_drift(
    accepted: AcceptedClusterState | None,
    deployment: DeploymentObservation | None,
    fdb: FdbStatusObservation | None,
) -> DriftReport:
    accepted_ids = {service.service_id for service in accepted.services} if accepted else set()
    deployed_ids = {service.service_id for service in deployment.services} if deployment else set()
    participating = set(fdb.process_addresses) if fdb else set()

    missing = tuple(sorted(accepted_ids - deployed_ids, key=lambda item: item.encode("utf-8")))
    unexpected = tuple(sorted(deployed_ids - accepted_ids, key=lambda item: item.encode("utf-8"))) if accepted else ()

    not_participating: list[str] = []
    if accepted and fdb is not None:
        for service in accepted.services:
            if service.endpoint not in participating:
                not_participating.append(service.service_id)

    unknowns: list[str] = []
    if deployment is None:
        unknowns.append("deployment-observation-unavailable")
    if fdb is None:
        unknowns.append("fdb-status-unavailable")
    if accepted is None:
        unknowns.append("accepted-state-unborn-or-unavailable")

    return DriftReport(
        missing_accepted_services=missing,
        unexpected_deployed_services=unexpected,
        accepted_services_not_participating=tuple(sorted(not_participating, key=lambda item: item.encode("utf-8"))),
        unknowns=tuple(unknowns),
    )
