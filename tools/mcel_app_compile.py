#!/usr/bin/env python3
"""Compile a DSL-authored MCEL application through the generic app pipeline."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
if __package__ in {None, ""}: sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main_computer.mcel_app_compile import AppCompileError, compile_application
from main_computer.mcel_application_packages import repository_root
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app", required=True)
    p.add_argument("--repo-root", type=Path, default=repository_root())
    p.add_argument("--compare-ir", type=Path)
    p.add_argument("--write-candidate", action="store_true")
    p.add_argument("--candidate-root", type=Path, default=DEFAULT_CANDIDATE_ROOT)
    p.add_argument("--timeout-ms", type=int, default=1000)
    p.add_argument("--json", action="store_true")
    a=p.parse_args(argv)
    try:
        r=compile_application(app_id=a.app,repo_root=a.repo_root,compare_ir_path=a.compare_ir,write_candidate=a.write_candidate,candidate_root=a.candidate_root,timeout_ms=a.timeout_ms)
    except AppCompileError as exc:
        print(f"mcel-app-compile-wave9\napp: {a.app}\nstatus: fail\ndiagnostics: 1\n  MCEL_APP_COMPILE_FAILED: {exc}")
        return 2
    d=r.to_dict(include_ir=a.json)
    if a.json: print(json.dumps(d,indent=2,sort_keys=True,ensure_ascii=False))
    else:
        print("mcel-app-compile-wave9")
        print(f"app: {a.app}")
        print(f"status: {r.status}")
        blocking=sum(1 for item in r.diagnostics if item.get("blocking",True))
        warnings=sum(1 for item in r.diagnostics if not item.get("blocking",True))
        print(f"diagnostics: {blocking}")
        print(f"migration_warnings: {warnings}")
        print("generic_pipeline: pass" if r.valid else "generic_pipeline: fail")
        print("counter_specific_execution_path_required: false")
        print(f"semantic_fingerprint: {d.get('semanticFingerprint')}")
        print(f"source_binding_fingerprint: {d.get('sourceBindingFingerprint')}")
        for x in r.diagnostics:
            if x.get("blocking",True): print(f"  {x.get('code')}: {x.get('summary')}")
    return 0 if r.valid else 3
if __name__=="__main__": raise SystemExit(main())
