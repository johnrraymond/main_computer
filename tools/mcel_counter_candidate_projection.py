#!/usr/bin/env python3
"""Generate and compare an isolated Counter explicit-package candidate."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
if __package__ in {None, ""}: sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main_computer.mcel_counter_candidate_projection import DEFAULT_CANDIDATE_ROOT, DEFAULT_COUNTER_ROOT, DEFAULT_DSL_SOURCE, DEFAULT_FIXTURE_IR, project_counter_candidate

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dsl-source", type=Path, default=DEFAULT_DSL_SOURCE)
    p.add_argument("--fixture-ir", type=Path, default=DEFAULT_FIXTURE_IR)
    p.add_argument("--live-package", type=Path, default=DEFAULT_COUNTER_ROOT)
    p.add_argument("--candidate-root", type=Path, default=DEFAULT_CANDIDATE_ROOT)
    p.add_argument("--write-candidate", action="store_true")
    p.add_argument("--json", action="store_true")
    return p

def main(argv=None):
    a=parser().parse_args(argv)
    r=project_counter_candidate(dsl_source_path=a.dsl_source, fixture_ir_path=a.fixture_ir, live_package_root=a.live_package, candidate_root=a.candidate_root, write_candidate=a.write_candidate)
    d=r.to_dict()
    if a.json: print(json.dumps(d,indent=2,sort_keys=True,ensure_ascii=False))
    else:
        print("mcel-counter-candidate-projection-wave4")
        print("app: contract-counter")
        print(f"status: {r.status}")
        print(f"diagnostics: {r.diagnostic_count}")
        print(f"projection_profile: {d['projectionProfile']}")
        print(f"files_exact: {sum(1 for x in d.get('projections',[]) if x['status']=='exact')}/{len(d.get('projections',[]))}")
        for name, value in d.get("fingerprints",{}).items(): print(f"{name}_fingerprint_status: {value['status']}")
        print(f"roundtrip: {d.get('roundtrip',{}).get('status')}")
        print("promotion_eligible: false")
        if d.get("artifacts"): print(f"candidate: {d['artifacts']['candidateDirectory']}")
        for x in r.diagnostics: print(f"{x.get('code')}: {x.get('semanticPath')}: {x.get('summary')}")
    return 0 if r.valid else 4
if __name__=="__main__": raise SystemExit(main())
