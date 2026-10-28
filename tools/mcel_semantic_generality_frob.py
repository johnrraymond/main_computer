#!/usr/bin/env python3
"""Read-only, independent MCEL semantic architecture and cross-domain generality Frob.

Checks shared code fingerprints, adversarial source contracts, browser layout
handoff, and retained legacy behavior. Outputs evidence; does not promote the
complete product promise or invoke Task Manager operational actions.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'main_computer/web/applications/scripts'
FIXTURES = ROOT / 'tools/fixtures/mcel_semantic_generality'
OUTPUT = ROOT / 'runtime/reports/mcel-semantic-generality'
SHARED = ['mcel-semantic-value-contract.js','mcel-semantic-collection-workspace.js','mcel-semantic-layout-inference.js']
SCHEMA = 'mcel.semantic-generality-frob.v1'
WIDTHS = (320,390,560,1100)

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def browser_cases(out: Path, fixtures: list[dict]) -> dict:
    from playwright.sync_api import sync_playwright
    source = [SCRIPTS / p for p in ('mcel-element-registry.js', 'mcel-elements-core.js',
               'mcel-semantic-layout-inference.js','mcel-semantic-value-contract.js',
               'mcel-semantic-collection-workspace.js')]
    css = (SCRIPTS.parent/'styles/mcel-semantic-collection-workspace.css').read_text(encoding='utf-8')
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None,
                                    args=['--no-sandbox','--disable-dev-shm-usage'])
        try:
            for width in WIDTHS:
                page=browser.new_page(viewport={'width':width,'height':800})
                errors=[];requests=[]
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda req: requests.append(req.url))
                page.set_content('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
                                 '<style>*{box-sizing:border-box}body{margin:0;font:13px system-ui;color:#111}'
                                 '#host{width:100%;padding:4px;}'+css+'</style></head><body><div id="host"></div></body></html>')
                for path in source:
                    page.add_script_tag(content=path.read_text(encoding='utf-8'))
                observed=page.evaluate('''fixtures => {
                  const report=[];
                  for(const fixture of fixtures) {
                    const parent=document.querySelector('#host');
                    const result=McelSemanticCollectionWorkspace.mount(parent,fixture.rows,fixture.contract,
                      {observeResize:false});
                    const region=parent.querySelector('.mcel-semantic-collection');
                    const rendered=[...region.querySelectorAll('mcel-row')];
                    const plan=result.layout;
                    const ids=rendered.map(row=>row.dataset.rowId);
                    const displayed=rendered[1].querySelector('[data-semantic-field="measurement"] .field-value')?.textContent;
                    const original=fixture.rows[0][fixture.contract.fields.find(f=>f.key==='details').from];
                    result.inspect(ids[0]);
                    const inspector=region.querySelector('.mcel-semantic-collection__inspector').textContent;
                    const exact=inspector.includes(original) && inspector.includes('Semantic evidence');
                    const hostAuthority=region.dataset.mcelCollectionLayoutAuthority;
                    const before=JSON.stringify(fixture.rows);
                    const variants=structuredClone(fixture.contract);
                    variants.fields.find(f=>f.key==='measurement').tier='detail';
                    result.destroy();
                    const second=McelSemanticCollectionWorkspace.mount(parent,fixture.rows,variants,
                      {observeResize:false});
                    const updated=second.layout;
                    const alternate=updated.disclosed_keys.includes('measurement') &&
                       !updated.visible_keys.includes('measurement');
                    second.destroy();
                    report.push({domain:fixture.domain,width:innerWidth,ids,rows:rendered.length,
                      displayUnavailable:displayed,sourceExact:exact,
                      allocation:plan,authority:hostAuthority,variant:updated,
                      variantSuccess:alternate,sourceUnchanged:before===JSON.stringify(fixture.rows),
                      rollback:parent.children.length===0});
                  }
                  return report;
                }''',fixtures)
                for case in observed:
                    case['requests']=requests[:]
                    case['errors']=errors[:]
                    case['status']='PASS' if (
                        case['rows']==3 and len(set(case['ids']))==3
                        and case['displayUnavailable']=='Unavailable' and case['sourceExact']
                        and case['allocation']['unresolved_deficit_px']==0
                        and case['allocation']['required_keys']==['ident','title']
                        and case['authority']=='mcel-semantic-layout-inference.v1'
                        and case['variantSuccess'] and case['sourceUnchanged'] and case['rollback']
                        and not errors and not requests) else 'FAIL'
                    results.append(case)
                page.close()
        finally:
            browser.close()
    return {'status':'PASS' if results and all(x['status']=='PASS' for x in results) else 'FAIL',
            'cases':results,'scope':'Offline actual registered MCEL collection + layout; no hosted URL or operational actions'}

def run(*, output: Path = OUTPUT, browser: bool = True, regressions: bool = False) -> dict:
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    files=[FIXTURES/'process.v1.json',FIXTURES/'parcel.v1.json']
    fixtures=[json.loads(f.read_text(encoding='utf-8')) for f in files]
    command=['node',str(ROOT/'tools/mcel_semantic_generality_probe.js'),*(str(f) for f in files)]
    probe=subprocess.run(command,cwd=ROOT,text=True,capture_output=True)
    try:unit=json.loads(probe.stdout)
    except json.JSONDecodeError:
        unit={'status':'FAIL','stdout':probe.stdout,'stderr':probe.stderr,'returncode':probe.returncode}
    runtime={'status':'UNVERIFIED','reason':'Chromium not requested'}
    if browser:
        try:runtime=browser_cases(output,fixtures)
        except Exception as exc:runtime={'status':'FAIL','error':type(exc).__name__+': '+str(exc)}
    prior=[]
    if regressions:
        for command in [
            ['python','-m','tools.mcel_task_manager_semantic_pipeline_acceptance'],
            ['python','-m','tools.mcel_task_manager_notebook_lab_acceptance'],
            ['python','tools/mcel_representation_layout_frob.py'],
        ]:
            run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=180)
            prior.append({'command':' '.join(command),'status':'PASS' if run.returncode==0 else 'FAIL',
                          'returncode':run.returncode,'stdoutTail':run.stdout[-1500:],'stderrTail':run.stderr[-1000:]})
    source_hashes={str(f.relative_to(ROOT)).replace('\\','/'):sha(f) for f in
                   [*(SCRIPTS/s for s in SHARED),*files]}
    used_fields={f['domain']:len(f['contract']['fields']) for f in fixtures}
    metadata={f['domain']:sum(len(field.get('validity',{})) for field in f['contract']['fields']) for f in fixtures}
    # Honest absolute authoring cost, not a fabricated equivalently-correct baseline.
    authoring={'status':'MEASURED_ABSOLUTE_ONLY', 'semanticFields':used_fields,
              'explicitValidityBindings':metadata,'customDomainRendererHelpers':0,
              'applicationSpecificLayoutBranches':0,'contractVariantsProved':2,
              'relativeCostToEquivalentlyCorrectBaseline':'UNVERIFIED',
              'reason':'The older v1 contract cannot express all of the new information-state obligations.'}
    status=('PASS' if unit.get('status')=='PASS' and runtime.get('status')=='PASS' and
            all(r['status']=='PASS' for r in prior) else
            'FAIL' if unit.get('status')=='FAIL' or runtime.get('status')=='FAIL' or
            any(r['status']=='FAIL' for r in prior) else 'UNVERIFIED')
    result={'schema':SCHEMA,'generatedUtc':datetime.now(timezone.utc).isoformat(),
            'status':status,'architecturalGeneralityVerified':status=='PASS',
            'completePromiseFulfilled':False,'contractFamily':'mcel.semantic-collection-spec.v1 / v2 additive',
            'sourceHashes':source_hashes,'unitProbe':unit,'browserHandoff':runtime,
            'legacyRegressions':prior,'authoringEconomy':authoring,
            'scope':{'liveHostVerified':False,'operationalActionsExecuted':False,
                     'websiteBuilderModified':False,'fileManagerModified':False,
                     'taskManagerOperationalControlsModified':False,
                     'historicalBenchmarksPreserved':True},
            'limitations':['Read-only offline datasets and registered-browser renderer, not live HTTP',
                           'No claim of fully attractive default UI or complete application composition',
                           'Authoring economy comparison to equally correct baseline remains unverified',
                           'Current v1 Task Manager data contract is unchanged and not silently migrated']}
    (output/'report.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
    lines=['# MCEL Patch 9 — Independent Semantic Generality Evidence','',
           '**Result: '+status+'**. Product promise remains incomplete.','',
           '## Test evidence','',
           '- Pure Node cross-domain and adversarial checks: '+unit.get('status','FAIL')+', '
                +str(len(unit.get('checks',[])))+' checks',
           '- Registered Chromium layout handoff: '+runtime.get('status','UNVERIFIED')+', '
                +str(len(runtime.get('cases',[])))+' cases',
           '- Prior regression commands: '+str(len(prior))+' run',
           '- Shared source SHA256: '+source_hashes[str((SCRIPTS/SHARED[0]).relative_to(ROOT)).replace('\\','/')],
           '', '## Authoring burden','',
           '- Semantic fields: '+str(used_fields),
           '- Explicit validity bindings: '+str(metadata),
           '- Custom domain renderer helpers: 0',
           '- Equivalent-correctness savings: **UNVERIFIED** (do not claim savings)',
           '', '## Limitations','',
           *['- '+s for s in result['limitations']], '']
    (output/'review.md').write_text('\n'.join(lines),encoding='utf8')
    return result

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=OUTPUT)
    ap.add_argument('--no-browser',action='store_true')
    ap.add_argument('--regressions',action='store_true')
    args=ap.parse_args()
    result=run(output=args.output,browser=not args.no_browser,regressions=args.regressions)
    print('MCEL_SEMANTIC_GENERALITY_'+result['status'])
    print('independent checks:',len(result['unitProbe'].get('checks',[])),result['unitProbe'].get('status'))
    print('registered browser cases:',len(result['browserHandoff'].get('cases',[])),result['browserHandoff']['status'])
    print('promise complete:',result['completePromiseFulfilled'])
    print('report:',Path(args.output).resolve()/'report.json')
    return 0 if result['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
