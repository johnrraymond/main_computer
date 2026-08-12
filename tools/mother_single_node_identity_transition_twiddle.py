#!/usr/bin/env python
"""Local twiddle for the operator-directed empty-topology add-node identity boundary.

The twiddle performs no network access and no mutation. It proves that identity
evidence whose current topology is empty routes to single-node bootstrap rather
than the old replica-sync / validator-admission path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from tools.mother.common.deployment_node_add_identity import _identity_after_install_routing


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--identity-evidence", required=True)
    parser.add_argument("--identity-evidence-sha256")
    args = parser.parse_args(argv)

    path = Path(args.identity_evidence)
    raw = path.read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()
    if args.identity_evidence_sha256 and actual_sha != args.identity_evidence_sha256:
        raise SystemExit(
            "MOTHER_SINGLE_NODE_IDENTITY_TWIDDLE_SHA_MISMATCH: "
            f"expected {args.identity_evidence_sha256} got {actual_sha}"
        )
    document = json.loads(raw.decode("utf-8"))
    route = _identity_after_install_routing(document)
    legacy_next_phase = document.get("next_phase")
    clean = (
        document.get("status") == "pass"
        and document.get("identity_install_performed") is True
        and document.get("identity_install_proven") is True
        and document.get("current_topology", {}).get("validator_count") == 0
        and document.get("prepared_post_add_topology", {}).get("validator_count") == 1
        and route.get("next_phase") == f"add-node-single-node-bootstrap-{document.get('network')}"
        and route.get("replica_sync_required") is False
        and route.get("validator_admission_required") is False
    )
    result = {
        "twiddle": "mother-single-node-identity-transition",
        "clean": clean,
        "network": document.get("network"),
        "target_node": document.get("target", {}).get("node"),
        "target_host": document.get("target", {}).get("controller_id"),
        "created_service_uuid": document.get("target", {}).get("created_service_uuid"),
        "identity_evidence_sha256": actual_sha,
        "current_validator_count": document.get("current_topology", {}).get("validator_count"),
        "prepared_post_add_validator_count": document.get("prepared_post_add_topology", {}).get("validator_count"),
        "legacy_evidence_next_phase": legacy_next_phase,
        "corrected_next_phase": route.get("next_phase"),
        "single_node_bootstrap_required": route.get("single_node_bootstrap_required"),
        "replica_sync_required": route.get("replica_sync_required"),
        "validator_admission_required": route.get("validator_admission_required"),
        "remaining_phases": route.get("remaining_phases"),
        "assertions": {
            "empty_current_topology_detected": document.get("current_topology", {}).get("validator_count") == 0,
            "single_node_target_detected": document.get("prepared_post_add_topology", {}).get("validator_count") == 1,
            "old_replica_sync_path_not_selected": route.get("replica_sync_required") is False,
            "old_validator_admission_path_not_selected": route.get("validator_admission_required") is False,
            "single_node_bootstrap_selected": route.get("single_node_bootstrap_required") is True,
        },
        "network_access_performed": False,
        "live_mutation_performed": False,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
