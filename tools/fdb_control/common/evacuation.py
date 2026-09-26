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
    *,
    allow_full_deletion: bool = False,
) -> tuple[ServicePlacement, ...]:
    """Return the surviving topology for one explicit service contraction.

    Normal `remove-service` contracts an accepted `single`-redundancy topology by
    exactly one service and preserves at least one live service.  The explicit
    `allow_full_deletion` acknowledgement opens only the final 1 -> 0 transition.
    That destructive path does not claim FDB evacuation safety because there is
    no surviving process to receive the final copy.
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
    if len(accepted.services) == 1 and not allow_full_deletion:
        raise FdbControlError(
            code="FDB_REMOVE_SERVICE_FULL_DELETION_REQUIRES_ACK",
            message=(
                "removing the final accepted FDB service requires explicit "
                "--allow-full-deletion acknowledgement"
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
