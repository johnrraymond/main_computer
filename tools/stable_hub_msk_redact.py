"""Explicit MSK record signature redaction, dry-run unless approved.

Usage: python -m tools.stable_hub_msk_redact --topology deploy/hub-topology/dev-topology.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from main_computer.stable_hub_msk import stable_multisession_store_from_topology
from main_computer.stable_hub_topology import load_stable_hub_topology


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Redact old wallet-signed MSK request copies")
    parser.add_argument("--topology", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--yes-i-know-this-mutates-msk-storage", action="store_true")
    args = parser.parse_args(argv)
    if args.execute != args.yes_i_know_this_mutates_msk_storage:
        parser.error("mutation requires BOTH --execute and --yes-i-know-this-mutates-msk-storage")
    topology = load_stable_hub_topology(args.topology)
    store = stable_multisession_store_from_topology(topology)
    if not hasattr(store, "redact_stored_authorizations"):
        parser.error("MSK store does not support redaction")
    result = store.redact_stored_authorizations(execute=args.execute)
    print(json.dumps({"cluster_id": topology.cluster_id, **result}, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
