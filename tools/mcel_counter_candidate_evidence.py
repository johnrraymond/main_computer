#!/usr/bin/env python3
"""Run independent evidence against the isolated DSL-generated Counter candidate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_computer.mcel_counter_candidate_evidence import (  # noqa: E402
    DEFAULT_REPORT_ROOT,
    REPOSITORY_ROOT,
    run_counter_candidate_evidence,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--no-write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    result = run_counter_candidate_evidence(
        repo_root=args.repo_root,
        report_root=args.report_root,
        headed=args.headed,
        write_report=not args.no_write_report,
    )
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        candidate = payload.get("candidate") or {}
        stages = payload.get("stages") or {}
        effects = payload.get("effectAccounting") or {}
        authority = payload.get("authority") or {}
        print("mcel-counter-candidate-evidence-wave5")
        print("app: contract-counter")
        print(f"status: {payload.get('status')}")
        print(f"diagnostics: {result.diagnostic_count}")
        print(f"package: {stages.get('packageValidation', {}).get('status', 'not-run')}")
        print(f"acceptance: {stages.get('acceptance', {}).get('status', 'not-run')}")
        print(f"browser_observation: {stages.get('browserObservation', {}).get('status', 'not-run')}")
        print(f"effect_accounting: {effects.get('status', 'not-run')}")
        print(f"application_proof: {stages.get('applicationProof', {}).get('status', 'not-run')}")
        print(f"truth_status: {payload.get('truthStatus')}")
        print(f"repository_binding: {stages.get('repositoryBinding', {}).get('status', 'not-run')}")
        print(f"semantic_fingerprint: {candidate.get('semanticFingerprint')}")
        print(f"source_binding_fingerprint: {candidate.get('sourceBindingFingerprint')}")
        print(f"evidence_reused: {str(authority.get('evidenceReused')).lower()}")
        print(f"live_application_changed: {str(authority.get('liveApplicationChanged')).lower()}")
        print(f"candidate_promoted: {str(authority.get('candidatePromoted')).lower()}")
        if result.output_directory:
            try:
                display = result.output_directory.resolve().relative_to(args.repo_root.resolve()).as_posix()
            except ValueError:
                display = str(result.output_directory.resolve())
            print(f"reports: {display}")
        for diagnostic in result.diagnostics:
            print(f"  {diagnostic.get('code')}: {diagnostic.get('summary')}")
    if args.check and not result.valid:
        return 1
    return 0 if result.valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
