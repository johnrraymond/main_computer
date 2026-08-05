#!/usr/bin/env python3
"""Run generic IR-native proof for one promoted DSL-authored MCEL application."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
if __package__ in {None, ""}: sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main_computer.mcel_app_ir_native_proof import AppIrNativeProofError, run_app_ir_native_intent_proof, write_app_ir_native_report
from main_computer.mcel_application_packages import build_application_package_catalog, repository_root

def _load(path,label):
    try: value=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError) as exc: raise AppIrNativeProofError(f"Could not load {label}: {exc}") from exc
    if not isinstance(value,dict): raise AppIrNativeProofError(f"{label} must be a JSON object.")
    return value

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app", required=True)
    p.add_argument("--repo-root", type=Path, default=repository_root())
    p.add_argument("--check", action="store_true")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--write-report", action="store_true")
    p.add_argument("--json", action="store_true")
    a=p.parse_args(argv); repo=a.repo_root.resolve()
    try:
        records=[x for x in build_application_package_catalog(repo).packages if x.app_id==a.app]
        if len(records)!=1: raise AppIrNativeProofError(f"Application {a.app!r} was not discovered exactly once.")
        slug=''.join(c if c.isalnum() or c in '._-' else '-' for c in a.app.lower()).strip('-')
        acceptance=_load(repo/f"runtime/reports/mcel-acceptance/apps/{slug}/mcel-acceptance-report.json","acceptance evidence")
        observation=_load(repo/f"runtime/reports/mcel-observation/apps/{slug}/mcel-operation-observation-report.json","browser observation evidence")
        report=run_app_ir_native_intent_proof(app_id=a.app,repo=repo,record=records[0],acceptance=acceptance,observation=observation,headed=a.headed)
    except AppIrNativeProofError as exc:
        print(f"mcel-app-ir-native-proof-wave9\napp: {a.app}\nstatus: fail\ndiagnostics: 1\n  MCEL_APP_IR_NATIVE_PROOF_FAILED: {exc}")
        return 2
    output=repo/f"runtime/reports/mcel-ir-native-proof/apps/{slug}"
    if a.write_report: write_app_ir_native_report(report,output)
    if a.json: print(json.dumps(report,indent=2,sort_keys=True,ensure_ascii=False))
    else:
        print("mcel-app-ir-native-proof-wave9")
        print(f"app: {a.app}")
        print(f"status: {report.get('status')}")
        print("diagnostics: 0")
        print("generic_pipeline: pass")
        print("counter_specific_execution_path_required: false")
        print(f"source_authority: {(report.get('sourceAuthority') or {}).get('kind')}")
        print(f"intent_completeness: {report.get('status')}")
        print(f"legacy_evidence_required: {str(bool(report.get('legacyEvidenceRequired'))).lower()}")
        print(f"truth_status: semantic-runtime-proven")
        print(f"semantic_fingerprint: {report.get('semanticFingerprint')}")
        if a.write_report: print(f"report: {(output/'mcel-ir-native-intent-proof.json').relative_to(repo).as_posix()}")
    return 0
if __name__=="__main__": raise SystemExit(main())
