from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .common.drift import classify_drift
from .common.fdb_status import normalize_status_json
from .common.models import (
    AcceptedClusterState,
    DeploymentObservation,
    FdbContext,
    InspectionRequest,
    InspectionResult,
)

AcceptedReader = Callable[[FdbContext, str], AcceptedClusterState | None]
DeploymentReader = Callable[[FdbContext, str], DeploymentObservation | None]
StatusReader = Callable[[FdbContext, str], Mapping[str, Any] | None]


def run(
    ctx: FdbContext,
    request: InspectionRequest,
    *,
    accepted_reader: AcceptedReader,
    deployment_reader: DeploymentReader,
    status_reader: StatusReader,
) -> InspectionResult:
    """Build the complete currently-provable read-only cluster view.

    The first implementation wave deliberately injects the three readers.  This
    makes the observation logic independently testable before the live Coolify
    and fdbcli adapters are wired in later waves.
    """

    accepted = accepted_reader(ctx, request.network)
    deployment = deployment_reader(ctx, request.network)
    raw_status = status_reader(ctx, request.network)
    fdb = normalize_status_json(raw_status) if raw_status is not None else None
    drift = classify_drift(accepted, deployment, fdb)

    blocked = ["FDB_OPEN_TRANSACTION_PROBE"]
    allowed = ["inspect"]
    if accepted is None and deployment is not None and not deployment.services and fdb is None:
        allowed.append("create-cluster")
    elif accepted is not None and not accepted.retired:
        allowed.extend(("add-service", "consumer-contract", "reconcile"))

    return InspectionResult(
        network=request.network,
        accepted=accepted,
        deployment=deployment,
        fdb=fdb,
        drift=drift,
        transaction_probe="unavailable:FDB_OPEN_TRANSACTION_PROBE",
        blocked_capabilities=tuple(blocked),
        allowed_actions=tuple(allowed),
    )
