#!/usr/bin/env python3
"""Prove one MCEL application's isolated portability through the generic pipeline."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
if __package__ in {None, ""}: sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main_computer.mcel_app_portability import AppPortabilityError, prove_application_portability
from main_computer.mcel_application_packages import repository_root
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app", required=True)
    p.add_argument("--repo-root", type=Path, default=repository_root())
    p.add_argument("--candidate-root", type=Path, default=DEFAULT_CANDIDATE_ROOT)
    p.add_argument("--check", action="store_true")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--write-report", action="store_true")
    p.add_argument("--json", action="store_true")
    a=p.parse_args(argv)
    try:
        r=prove_application_portability(app_id=a.app,repo_root=a.repo_root,candidate_root=a.candidate_root,headed=a.headed,write_report=a.write_report)
    except (AppPortabilityError,RuntimeError) as exc:
        print(f"mcel-app-portability-wave11\napp: {a.app}\nstatus: fail\ndiagnostics: 1\n  MCEL_APP_PORTABILITY_FAILED: {exc}")
        return 2
    d=r.to_dict()
    if a.json:
        print(json.dumps(d,indent=2,sort_keys=True,ensure_ascii=False))
    else:
        warnings=int(((d.get("migrationDebt") or {}).get("opaqueCallbacks") or 0))
        blocking=sum(1 for item in r.diagnostics if item.get("blocking",True))
        print("mcel-app-portability-wave11")
        print(f"app: {a.app}")
        print(f"status: {r.status}")
        print(f"diagnostics: {blocking}")
        print(f"migration_warnings: {warnings}")
        print("generic_pipeline: pass" if r.valid else "generic_pipeline: fail")
        print("counter_specific_execution_path_required: false")
        print(f"authoring_frontend: {d.get('authoringFrontend')}")
        print(f"semantic_compatibility: {d.get('semanticCompatibility')}")
        print(f"candidate_truth_status: {d.get('candidateTruthStatus')}")
        print(f"live_authority: {d.get('liveAuthority')}")
        print(f"promotion_executed: {str(bool(d.get('promotionExecuted'))).lower()}")
        print(f"portable_ir_projection_complete: {str(bool(d.get('portableIrProjectionComplete'))).lower()}")
        print(f"semantic_fingerprint: {d.get('semanticFingerprint')}")
        for x in r.diagnostics:
            if x.get("blocking",True): print(f"  {x.get('code')}: {x.get('summary')}")
        if r.output_directory: print(f"reports: {r.output_directory}")
    return 0 if r.valid else 4
if __name__=="__main__": raise SystemExit(main())
