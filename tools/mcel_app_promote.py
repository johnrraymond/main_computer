#!/usr/bin/env python3
"""Inspect, execute, or roll back MCEL authority through the generic app pipeline."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
if __package__ in {None, ""}: sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main_computer.mcel_app_promote import AppPromotionError, execute_application_promotion, inspect_application_authority, rehearse_application_promotion, rollback_application_promotion
from main_computer.mcel_application_packages import repository_root

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app", required=True)
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--rehearse", action="store_true")
    g.add_argument("--execute", action="store_true")
    g.add_argument("--rollback", metavar="TRANSACTION")
    p.add_argument("--repo-root", type=Path, default=repository_root())
    p.add_argument("--headed", action="store_true")
    p.add_argument("--json", action="store_true")
    a=p.parse_args(argv)
    try:
        if a.check: r=inspect_application_authority(app_id=a.app,repo_root=a.repo_root)
        elif a.rehearse: r=rehearse_application_promotion(app_id=a.app,repo_root=a.repo_root,headed=a.headed)
        elif a.execute: r=execute_application_promotion(app_id=a.app,repo_root=a.repo_root,headed=a.headed)
        else: r=rollback_application_promotion(a.rollback,app_id=a.app,repo_root=a.repo_root)
    except (AppPromotionError, RuntimeError) as exc:
        print(f"mcel-app-promote-wave9\napp: {a.app}\nstatus: fail\ndiagnostics: 1\n  MCEL_APP_PROMOTION_FAILED: {exc}")
        return 2
    d=r.to_dict()
    if a.json: print(json.dumps(d,indent=2,sort_keys=True,ensure_ascii=False))
    else:
        print("mcel-app-promote-wave9")
        print(f"app: {a.app}")
        print(f"status: {r.status}")
        print(f"diagnostics: {r.diagnostic_count}")
        print("generic_pipeline: pass" if r.valid else "generic_pipeline: fail")
        print("counter_specific_execution_path_required: false")
        if a.check:
            print(f"source_authority: {d.get('sourceAuthority')}")
            print(f"derived_artifact_authority: {d.get('derivedArtifactAuthority')}")
            print(f"promotion_executed: {str(d.get('promotionExecuted')).lower()}")
        else:
            result=d.get('result') or {}
            if a.rehearse:
                print(f"promotion_rehearsal: {result.get('promotionRehearsal')}")
                print(f"post_promotion_truth_status: {result.get('postPromotionTruthStatus')}")
                print(f"rollback_rehearsal: {result.get('rollbackRehearsal')}")
                print(f"rollback_restoration: {result.get('rollbackRestoration')}")
                print(f"live_repository_changed: {str(result.get('liveRepositoryChanged', False)).lower()}")
                print(f"promotion_executed: {str(result.get('promotionExecuted', False)).lower()}")
                print(f"promotion_eligible: {str(result.get('promotionEligible', False)).lower()}")
                if r.output_directory: print(f"reports: {r.output_directory}")
            elif a.execute:
                print(f"transaction: {result.get('transactionId')}")
                print(f"promotion_executed: {str(result.get('promotionExecuted', False)).lower()}")
                print(f"source_authority: {result.get('sourceAuthority')}")
                print(f"derived_artifact_authority: {result.get('derivedArtifactAuthority')}")
                print(f"legacy_package_authority: {result.get('legacyPackageAuthority')}")
                print(f"truth_status: {result.get('truthStatus')}")
                print(f"semantic_fingerprint: {result.get('semanticFingerprint')}")
                print(f"rollback_available: {str(result.get('rollbackAvailable', False)).lower()}")
                if r.output_directory: print(f"reports: {r.output_directory}")
            else:
                print(f"transaction: {result.get('transactionId')}")
                print(f"rollback_executed: {str(result.get('rollbackExecuted', False)).lower()}")
                print(f"restoration: {result.get('restoration')}")
                print(f"source_authority: {result.get('sourceAuthority')}")
                if r.output_directory: print(f"reports: {r.output_directory}")
        for x in r.diagnostics: print(f"  {x.get('code')}: {x.get('summary')}")
    return 0 if r.valid else 2
if __name__=="__main__": raise SystemExit(main())
