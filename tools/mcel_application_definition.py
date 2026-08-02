#!/usr/bin/env python3
"""Generate or check explicit contracts from an MCEL application definition."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
if __package__ in {None, ""}: sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main_computer.mcel_application_definition_normalizer import (
    RESULT_SCHEMA, ApplicationDefinitionNormalizationError, StaleApplicationDefinition,
    build_normalization_plan, check_normalization, write_normalization,
)
from main_computer.mcel_application_packages import repository_root

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True)
    parser.add_argument("--repo-root", type=Path, default=repository_root())
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args=parser.parse_args(argv)
    try:
        plan=build_normalization_plan(args.app,args.repo_root)
        fresh, stale=check_normalization(plan)
        changed=False
        if args.check:
            if not fresh: raise StaleApplicationDefinition("Generated application definition artifacts are stale: " + ", ".join(stale))
            code="application_definition_fresh"
        else:
            changed=bool(write_normalization(plan)); code="application_definition_written" if changed else "application_definition_fresh"
        payload={"schema":RESULT_SCHEMA,"ok":True,"resultCode":code,"mode":"check" if args.check else "write","appId":plan.app_id,"source":plan.definition_reference,"normalizedDefinition":plan.normalized_reference,"generatedContracts":plan.generated_contract_count,"definitionFingerprint":plan.definition_fingerprint,"changed":changed,"staleFiles":[]}
        exit_code=0
    except ApplicationDefinitionNormalizationError as exc:
        payload={"schema":RESULT_SCHEMA,"ok":False,"resultCode":exc.result_code,"mode":"check" if args.check else "write","appId":args.app,"changed":False,"error":str(exc)}
        exit_code=exc.exit_code
    if args.json: print(json.dumps(payload,indent=2,sort_keys=True))
    else:
        print(RESULT_SCHEMA)
        print(f"mode: {payload['mode']}")
        print(f"app: {payload['appId']}")
        if payload.get('source'): print(f"source: {payload['source']}")
        if payload.get('generatedContracts') is not None: print(f"generated_contracts: {payload['generatedContracts']}")
        if payload.get('definitionFingerprint'): print(f"definition_fingerprint: {payload['definitionFingerprint']}")
        print(f"changed: {str(payload.get('changed',False)).lower()}")
        print(f"status: {'pass' if payload['ok'] else 'fail'}")
        if payload.get('error'): print(f"error: {payload['error']}")
    return exit_code
if __name__ == '__main__': raise SystemExit(main())
