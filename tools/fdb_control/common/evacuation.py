from __future__ import annotations

import json

from .errors import FdbControlError
from .hashing import sha256_bytes
from .models import AcceptedClusterState, ServicePlacement

REMOVE_DRAIN_PROOF_PREFIX = "FDB_REMOVE_SERVICE_DRAINED_V1"
REMOVE_COMPLETE_PROOF_PREFIX = "FDB_REMOVE_SERVICE_PROOF_V1"


def require_supported_removal(
    accepted: AcceptedClusterState,
    target: ServicePlacement,
) -> tuple[ServicePlacement, ...]:
    """Return the surviving topology for a safe single-redundancy contraction.

    `remove-service` may contract any accepted `single`-redundancy topology by
    exactly one service.  If the target currently carries coordinator authority,
    the operation must first move that derived overlay to a safe surviving set.
    The operation never removes the final service; whole-cluster retirement
    belongs to `retire-cluster`.  Live safety is still proved by coordinator
    transition proof when needed, blocking FDB exclusion, and post-removal proof
    before accepted authority advances.
    """

    if accepted.redundancy_mode != "single":
        raise FdbControlError(
            code="FDB_REMOVE_SERVICE_REDUNDANCY_UNSUPPORTED",
            message=(
                "remove-service currently supports redundancy_mode='single'; "
                f"observed {accepted.redundancy_mode!r}"
            ),
            module_id="FDB-OFM-FDB-007",
            retry_class="never",
        )
    if len(accepted.services) < 2:
        raise FdbControlError(
            code="FDB_REMOVE_SERVICE_TOPOLOGY_UNSUPPORTED",
            message=(
                "remove-service must leave at least one accepted FDB service; "
                "use retire-cluster to retire the final service"
            ),
            module_id="FDB-OFM-FDB-007",
            retry_class="never",
        )
    survivors = tuple(item for item in accepted.services if item.service_id != target.service_id)
    if len(survivors) != len(accepted.services) - 1:
        raise ValueError("remove-service safety calculation did not remove exactly one service")
    return survivors


def removal_drain_proof_marker(plan: object) -> str:
    target = getattr(plan, "removed_service")
    payload = _payload(
        {
            "cluster_file": str(getattr(plan, "cluster_file_contents")),
            "removed_endpoint": target.endpoint,
            "removed_service_id": target.service_id,
            "phase": "drained",
        }
    )
    return f"{REMOVE_DRAIN_PROOF_PREFIX} {sha256_bytes(payload).digest}"


def removal_complete_proof_marker(plan: object) -> str:
    target = getattr(plan, "removed_service")
    services = tuple(getattr(plan, "services"))
    coordinators = tuple(getattr(plan, "coordinators"))
    payload = _payload(
        {
            "cluster_file": str(getattr(plan, "cluster_file_contents")),
            "coordinators": [item.endpoint for item in coordinators],
            "remaining_services": [
                {"service_id": item.service_id, "endpoint": item.endpoint}
                for item in sorted(services, key=lambda value: value.service_id.encode("utf-8"))
            ],
            "removed_endpoint": target.endpoint,
            "removed_service_id": target.service_id,
            "phase": "removed",
        }
    )
    return f"{REMOVE_COMPLETE_PROOF_PREFIX} {sha256_bytes(payload).digest}"


def _payload(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
