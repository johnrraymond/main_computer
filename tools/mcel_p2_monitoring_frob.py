"""P2 generated monitoring proof gate; keeps local-browser and live-host evidence separate.

Run: python -m tools.mcel_p2_monitoring_frob [--output PATH]
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
REPORT=ROOT/'runtime/reports/mcel-p2-monitoring/report.json'


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=REPORT)
    args=parser.parse_args(argv)
    statuses={"semanticSource":"FAIL","independentComposition":"FAIL",
              "browserFourModes":"UNVERIFIED","keyedContinuity":"UNVERIFIED",
              "authenticServiceSnapshot":"UNVERIFIED","legacyRuntimeRegression":"UNVERIFIED",
              "windowsHosted":"UNVERIFIED","completeProductionParity":"BLOCKED",
              "consequentialEffects":"NOT_IN_CANDIDATE","humanExpertDesign":"UNVERIFIED",
              "productionPromotion":"BLOCKED"}
    evidence={"limitations":"No real Windows hosted Task Manager route or human acceptance asserted."}
    try:
        ir,plan=compile_generated_candidate()
        verify_composition(ir,plan)
        nodes=ir['surfaces'][0]['nodes']
        read_ops={i['runtimeMethod'] for i in ir['intents'] if i.get('risk')=='external-read'}
        expected={'observeProcesses','observeAllProcesses','observeConnections','observeHardware'}
        if read_ops != expected or len([n for n in nodes if n.get('visibleWhen')])!=9:
            raise ValueError('Incomplete four-mode semantic authoring contract')
        if any(e['risk']!='external-read' for e in ir['effects']):
            raise ValueError('Consequential effect on monitoring-only proof route')
        statuses['semanticSource']='PASS';statuses['independentComposition']='PASS'
        evidence.update({'sourceIrHash':plan['sourceIrHash'],'compositionFingerprint':plan['fingerprint'],
                         'compositionRecipes':[r['recipe'] for r in plan['regions']],
                         'readOperations':sorted(read_ops),'conditionalNodes':9})
        process=subprocess.run([sys.executable,'-m','pytest','--confcutdir=tests','-q',
            'tests/test_mcel_p1_generated_composition.py',
            'tests/test_mcel_p2_monitoring_interactions.py',
            'tests/test_mcel_phase2b_semantic_runtime.py'],cwd=ROOT,capture_output=True,text=True,timeout=120)
        evidence['targetedPytestExitCode']=process.returncode
        evidence['targetedPytestTail']=(process.stdout+'\n'+process.stderr)[-5000:]
        if process.returncode==0 and 'skipped' not in process.stdout:
            for name in ('browserFourModes','keyedContinuity','authenticServiceSnapshot','legacyRuntimeRegression'):
                statuses[name]='PASS'
        elif process.returncode!=0:
            for name in ('browserFourModes','keyedContinuity','authenticServiceSnapshot','legacyRuntimeRegression'):
                statuses[name]='FAIL'
        else:
            evidence['warning']='A suite skipped tests; category-wide claims remain unverified.'
    except Exception as exc:
        evidence['error']=str(exc)
    boundary=('semanticSource','independentComposition','browserFourModes',
              'keyedContinuity','authenticServiceSnapshot','legacyRuntimeRegression')
    outcome='PASS_BOUNDARY_ONLY' if all(statuses[k]=='PASS' for k in boundary) else 'FAIL_OR_UNVERIFIED'
    report={'schema':'mcel.p2-monitoring-frob.v1','outcome':outcome,'statuses':statuses,'evidence':evidence}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps({'outcome':outcome,'statuses':statuses,'report':str(args.output)},indent=2))
    return 0 if outcome=='PASS_BOUNDARY_ONLY' else 1

if __name__=='__main__':raise SystemExit(main())
