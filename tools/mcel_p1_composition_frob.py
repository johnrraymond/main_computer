"""P1 composition proof Frob (read-only, local mock browser; live Windows unverified).

Run: python -m tools.mcel_p1_composition_frob
The report separates source/compile/pure synthesis/browser evidence from hosted/live.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from main_computer.mcel_semantic_generated_host import compile_generated_candidate
from main_computer.mcel_knowledge.semantic_composition import verify_composition

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'runtime/reports/mcel-p1-composition/report.json'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=DEFAULT)
    args=parser.parse_args(argv)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    statuses={"authoring":"FAIL", "synthesis":"FAIL", "browserControlled":"UNVERIFIED",
              "hostedWindows":"UNVERIFIED", "authenticLiveProducer":"UNVERIFIED",
              "humanDesign":"UNVERIFIED", "productionPromotion":"BLOCKED"}
    detail={"scope":"No live service or production route tested by this Frob"}
    try:
        ir,composition=compile_generated_candidate()
        verify_composition(ir,composition)
        statuses['authoring']='PASS'
        statuses['synthesis']='PASS'
        detail.update({"appId":ir['application']['appId'], "sourceIrHash":composition['sourceIrHash'],
                       "compositionFingerprint":composition['fingerprint'],
                       "derivedRecipes":[r['recipe'] for r in composition['regions']],
                       "capabilities":[c['id'] for c in ir['capabilities']],
                       "effects":[e['id'] for e in ir['effects']]})
        process=subprocess.run([sys.executable,'-m','pytest','--confcutdir=tests','-q',
            'tests/test_mcel_p1_generated_composition.py'],cwd=ROOT,capture_output=True,text=True,timeout=90)
        detail['browserTestExitCode']=process.returncode
        detail['browserTestTail']=(process.stdout+'\n'+process.stderr)[-4000:]
        statuses['browserControlled']='PASS' if process.returncode==0 else 'FAIL'
    except Exception as exc:
        detail['error']=str(exc)
    outcome='PASS_BOUNDARY_ONLY' if all(statuses[k]=='PASS' for k in ('authoring','synthesis','browserControlled')) else 'FAIL'
    report={'schema':'mcel.p1-composition-frob.v1','outcome':outcome,'statuses':statuses,'evidence':detail,
            'scopeNote':'A controlled browser proof cannot establish live Windows process identity or Task Manager production parity.'}
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps({'outcome':outcome,'statuses':statuses,'report':str(args.output)},indent=2))
    return 0 if outcome=='PASS_BOUNDARY_ONLY' else 1


if __name__=='__main__':raise SystemExit(main())
