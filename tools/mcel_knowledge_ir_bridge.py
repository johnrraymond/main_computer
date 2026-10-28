#!/usr/bin/env python3
"""Read-only MCEL Patch 12 compile/resolve/collection projection witness.

Examples:
 python -m tools.mcel_knowledge_ir_bridge compile --app-id test-app --observation task-manager.processes
 python -m tools.mcel_knowledge_ir_bridge resolve --app-id test-app --observation file-explorer.entries --snapshot tools/fixtures/mcel_computer_knowledge/expanded_computer_snapshot.v1.json
 python -m tools.mcel_knowledge_ir_bridge collection --app-id test-app --observation file-explorer.entries --snapshot tools/fixtures/mcel_computer_knowledge/expanded_computer_snapshot.v1.json
"""
import argparse
import json
from pathlib import Path
from main_computer.mcel_knowledge.ir_bridge import compile_knowledge_ir, resolve_knowledge, collection_projection
from main_computer.mcel_knowledge import SemanticCatalogError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("compile", "resolve", "collection"))
    parser.add_argument("--app-id", default="computer-observations")
    parser.add_argument("--observation", action="append", required=True)
    parser.add_argument("--bindings", type=Path, help="JSON overlay: observation id -> source/property bindings")
    parser.add_argument("--snapshot", type=Path, help="Saved source-shaped JSON; required for resolve/collection")
    parser.add_argument("--output", type=Path, help="Explicit output JSON; defaults to stdout")
    args = parser.parse_args(argv)
    try:
        overlay = json.loads(args.bindings.read_text(encoding="utf-8")) if args.bindings else {}
        bridge = compile_knowledge_ir(args.app_id, observation_ids=args.observation, bindings=overlay)
        if args.mode == "compile":
            result = bridge
        else:
            if args.snapshot is None:
                parser.error("resolve/collection require --snapshot")
            snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
            result = resolve_knowledge(snapshot, bridge)
            if args.mode == "collection":
                if len(args.observation) != 1:
                    parser.error("collection requires exactly one --observation")
                result = collection_projection(result, args.observation[0])
        payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
            print(f"MCEL_KNOWLEDGE_IR_{args.mode.upper()}_PASS {args.output}")
        else:
            print(payload, end="")
        return 0
    except (SemanticCatalogError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f"MCEL_KNOWLEDGE_IR_{args.mode.upper()}_REFUSED: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
