#!/usr/bin/env python3
"""Read-only twiddle for Mother operator-directed add/delete path semantics.

This twiddle proves the local repository no longer treats historical
remove/finalize evidence as the current live validator topology during
``add-node prep``. It does not contact Coolify and does not write a transaction.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.deployment_node_add_prep import build_node_add_prep_transaction
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import read_private_state


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _operation() -> OperationIdentity:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return OperationIdentity(
        operation_id=f"operator-directed-path-twiddle-{stamp}",
        request_id="mother-operator-directed-path-twiddle",
        network="mainnet",
        operation_kind="MOTHER-OP-PLAN",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only proof that add-node prep uses operator-directed identity/history "
            "semantics instead of the old fixture/golden topology path."
        )
    )
    parser.add_argument("--runtime-state-root", default="runtime/state")
    parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    parser.add_argument("--node", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--mode", default="reactivate", choices=["initial", "soft", "reactivate"])
    parser.add_argument("--baseline-evidence", required=True)
    parser.add_argument("--baseline-evidence-sha256")
    parser.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    parser.add_argument("--created-at", default="2026-08-11T17:40:00Z")
    args = parser.parse_args(argv)

    paths = MotherPaths(runtime_state_root=Path(args.runtime_state_root)).resolve_private_state_paths()
    private_state = read_private_state(paths, operation=_operation())
    baseline_path = Path(args.baseline_evidence)
    baseline_sha = args.baseline_evidence_sha256 or _sha256_file(baseline_path)

    transaction = build_node_add_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network=args.network,
        target_node=args.node,
        target_host=args.host,
        mode=args.mode,
        baseline_evidence_sha256=baseline_sha,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        created_at=args.created_at,
    )

    current = transaction["current_topology"]
    source = transaction["source_baseline_evidence"]
    summary = transaction["summary"]
    historical_nodes = list(source.get("historical_nodes") or [])
    current_nodes = list(current.get("nodes") or [])

    proof = {
        "clean": True,
        "twiddle": "mother-operator-directed-path",
        "network": args.network,
        "target_node": args.node,
        "target_host": args.host,
        "golden_test_path": "operator-directed add/delete evidence",
        "baseline_evidence_sha256": baseline_sha,
        "baseline_topology_role": source.get("topology_role"),
        "historical_nodes_from_baseline": historical_nodes,
        "current_nodes_used_by_prep": current_nodes,
        "old_baseline_topology_used_as_live": bool(current.get("baseline_topology_used_as_live")),
        "coolify_c_required_by_prep": any(
            str(record.get("controller_id", "")).lower() == "coolify-c"
            for record in dict(current.get("services") or {}).values()
            if isinstance(record, dict)
        ),
        "operator_directed_testing_path": transaction["execution_plan"].get("operator_directed_testing_path") is True,
        "next_phase": summary.get("next_phase"),
        "transaction_was_written": False,
        "network_access_performed": transaction["policy"].get("network_access_performed"),
        "live_mutation_performed": transaction["policy"].get("live_mutation_performed"),
        "assertions": {
            "operator_directed_path_defined": transaction["execution_plan"].get("operator_directed_testing_path") is True,
            "historical_baseline_not_current_topology": source.get("topology_role") != "identity-history-only"
            or not bool(current.get("baseline_topology_used_as_live")),
            "prep_does_not_require_deleted_baseline_services": not any(node in current_nodes for node in historical_nodes),
            "prep_does_not_require_coolify_c_for_coolify_a_target": args.host != "coolify-a" or not any(
                str(record.get("controller_id", "")).lower() == "coolify-c"
                for record in dict(current.get("services") or {}).values()
                if isinstance(record, dict)
            ),
        },
    }
    proof["clean"] = all(proof["assertions"].values())
    if not proof["clean"]:
        print(json.dumps(proof, indent=2, sort_keys=True))
        return 2
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
