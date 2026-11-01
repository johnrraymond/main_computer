"""MCEL Phase 1: audit paired source compilation; not renderer acceptance."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from main_computer.mcel_application_ir import validate_application_ir
from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/fixtures/mcel_html_pair/application.js'


def run() -> dict:
    report = compile_dsl_application(SOURCE)
    checks = {'pairedCompilation': report.valid}
    if report.valid:
        ir = report.normalized_ir
        surface = ir['surfaces'][0]
        intent = surface['interfaceIntent']
        region_ids = {r['id'] for r in intent['regions']}
        checks['singleSemanticRoot'] = len([r for r in intent['regions'] if r['parentId'] is None]) == 1
        checks['hierarchyPreserved'] = all(r['parentId'] in region_ids for r in intent['regions'] if r['parentId'])
        checks['twoSourceProvenance'] = {Path(f['path']).name for f in ir['provenance']['frontend']['sourceFiles']} == {'application.html','application.js'}
        checks['noAuthoredLayout'] = ir['layouts'] == []
        checks['contentReferenced'] = any(r.get('content',{}).get('ref') == 'state:entries' for r in intent['regions'])
        checks['canonicalValidation'] = validate_application_ir(ir).valid
        change = copy.deepcopy(ir)
        change['surfaces'][0]['interfaceIntent']['regions'][1]['parentId'] = 'interface-region:imaginary'
        checks['canonicalTamperRejected'] = not validate_application_ir(change).valid
        checks['noInventedAuthority'] = ir['effects'] == []
    checks['legacyJavascriptStillCompiles'] = compile_dsl_application(ROOT/'mcel_apps/calculator/application.js').valid
    result={'schema':'mcel.phase1-paired-authoring-frob.v1',
            'status':'PASS_PAIRED_COMPILER_ONLY' if all(checks.values()) else 'FAIL',
            'checks':checks,
            'scope': 'Authoring, compilation, canonical IR, hierarchy, source bindings; not layout synthesis or runtime promotion'}
    return result


def main() -> int:
    result=run()
    print('MCEL_PHASE1_PAIRED_AUTHORING',result['status'])
    for name,ok in result['checks'].items():
        print(f'  {name}: {"PASS" if ok else "FAIL"}')
    out=ROOT/'runtime/reports/mcel-phase1-paired-authoring/report.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return 0 if result['status'].startswith('PASS') else 1


if __name__=='__main__':
    raise SystemExit(main())
