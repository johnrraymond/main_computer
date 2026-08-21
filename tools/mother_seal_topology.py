#!/usr/bin/env python3
"""Read-only live Mother topology sealing utility."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.deployment_topology_rectification import (
    MotherDeploymentTopologyRectificationError,
    seal_live_current_topology,
)
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import read_private_state

DEFAULT_RUNTIME_STATE_ROOT = Path("runtime/state")


def _operation(command: str, network: str, operation_id: str | None) -> OperationIdentity:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return OperationIdentity(
        operation_id=operation_id or f"mother-seal-topology-{command}-{network}-{stamp}",
        request_id=f"mother-seal-topology-cli-{command}",
        network=network,
        operation_kind="MOTHER-OP-PLAN",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Seal a read-only Mother topology baseline from exact live Coolify "
            "primary service bindings. Requires --use-live-topology."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    parser.add_argument("--operation-id")
    parser.add_argument("--topology-evidence", required=True)
    parser.add_argument("--acknowledge-topology-evidence-sha256", required=True)
    parser.add_argument("--use-live-topology", action="store_true", help="required acknowledgement to seal live service bindings")
    parser.add_argument("--max-age-seconds", type=int, default=86400)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    parser.add_argument("--write-evidence", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    operation = _operation("seal-live-current-topology", args.network, args.operation_id)
    paths = MotherPaths(runtime_state_root=Path(args.runtime_state_root)).resolve_private_state_paths()
    try:
        private_state = read_private_state(paths, operation=operation)
        result = seal_live_current_topology(
            paths,
            private_state,
            Path(args.topology_evidence),
            network=args.network,
            acknowledged_topology_evidence_sha256=args.acknowledge_topology_evidence_sha256,
            use_live_topology=args.use_live_topology,
            max_age_seconds=args.max_age_seconds,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            write_evidence=args.write_evidence,
            operation=operation,
        )
    except MotherDeploymentTopologyRectificationError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
