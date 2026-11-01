"""Phase 2 public DSL authoring/authority contract Frob.

The Frob does NOT perform browser execution, external I/O, or promote an app.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from main_computer.mcel_application_ir import validate_application_ir
from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'tools/fixtures/mcel_phase2_reference_apps'


def run() -> dict:
    checked = {}
    failures = []
    compiled = {}
    for name in ('file-manager', 'task-manager', 'code-editor', 'calculator'):
        path = FIX/name/'application.js'
        report = compile_dsl_application(path)
        compiled[name] = report
        checked[f'{name}.compiles'] = report.valid
        if not report.valid:
            failures.append({'app':name,'diagnostics':list(report.diagnostics)})
            continue
        ir = report.normalized_ir
        checked[f'{name}.noAuthoredLayout'] = ir['layouts'] == []
        checked[f'{name}.twoSourceProvenance'] = {Path(x['path']).name for x in ir['provenance']['frontend']['sourceFiles']} == {'application.js','application.html'}
        checked[f'{name}.canonicalIRValid'] = validate_application_ir(ir).valid
        checked[f'{name}.effectsGoverned'] = all(x.get('authority') and x.get('owner') and x.get('risk') for x in ir['effects'])
        checked[f'{name}.noVocabularyPins'] = not any('_v1' in (FIX/name/'application.html').read_text() or '_v1' in (FIX/name/'application.js').read_text() for _ in [0])
    if all(x.valid for x in compiled.values()):
        task = compiled['task-manager'].normalized_ir
        editor = compiled['code-editor'].normalized_ir
        checked['taskManager.consequentialRiskRetained'] = any(x['risk'] == 'consequential' for x in task['effects'])
        checked['codeEditor.sourceWriteRiskRetained'] = any(x['risk'] == 'source-write' for x in editor['effects'])
        checked['dynamicSelectionDeclared'] = any('collectionSelection' in x for x in task['intents'])
        checked['typedReadResultsDeclared'] = any(x.get('resultBindings') for x in compiled['file-manager'].normalized_ir['intents'])
        ir = copy.deepcopy(compiled['file-manager'].normalized_ir)
        intent = next(x for x in ir['intents'] if x.get('resultBindings'))
        intent['resultBindings'][0]['target']['ref'] = 'state:selected'
        checked['authorityTamperingRejected'] = not validate_application_ir(ir).valid
    checked['oldHostBoundStillCompiles'] = compile_dsl_application(ROOT/'mcel_apps/calculator/application.js').valid
    checked['phase1StillCompiles'] = compile_dsl_application(ROOT/'tools/fixtures/mcel_html_pair/application.js').valid
    result = {
        'schema':'mcel.phase2-operational-dsl-frob.v1',
        'status':'PASS_AUTHORING_CONTRACTS_ONLY' if all(checked.values()) else 'FAIL',
        'checks':checked,
        'failures':failures,
        'runtimeStatus':'NOT_VERIFIED',
        'fourAppParity':'NOT_VERIFIED',
        'limitations':[
            'Capability declarations do not register or grant actual runtime backends.',
            'Dynamic selection membership is a validated IR obligation, not yet enforced by the live renderer.',
            'Capability result-to-derived-state mapping is not yet executed by the common browser runtime.',
            'Code Editor document opening does not yet reconcile observed data into editable draft and dirty state.',
            'Long-lived subscriptions, cancellation lifecycle, and real app parity require subsequent implementation.',
        ],
    }
    return result


def main() -> int:
    result=run()
    print('MCEL_PHASE2_OPERATIONAL_DSL', result['status'])
    for key, ok in result['checks'].items():
        print(f'  {key}: {"PASS" if ok else "FAIL"}')
    print('  browser runtime:', result['runtimeStatus'])
    print('  four-app parity:', result['fourAppParity'])
    path = ROOT/'runtime/reports/mcel-phase2-operational-dsl/report.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return 0 if result['status'].startswith('PASS') else 1


if __name__ == '__main__':
    raise SystemExit(main())
