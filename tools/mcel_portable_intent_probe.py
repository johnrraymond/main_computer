#!/usr/bin/env python3
"""Real independent-package Chromium/domain-effect probe; no host app HTML/CSS.

A dedicated temporary workspace and explicit broker supply the only source-write
capabilities. Optional external certification requires live, explicit loopback
Ollama and a real isolated Mathics worker; simulated providers never certify.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
from functools import partial
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO=Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:sys.path.insert(0,str(REPO))

from main_computer.mcel_standalone_package import build_standalone_package
from main_computer.mcel_evidence_provenance import build_repository_provenance
from main_computer.mcel_portable_certification import generated_binding
from main_computer.mcel_portable_workspace import PortableWorkspaceBroker
from main_computer.mcel_portable_calculator_external import (
    EXTERNAL_INTENTS, PortableCalculatorExternalAuthority, _sha,
)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass


def source_drift_diagnostics(start: dict, end: dict, *, max_paths: int = 40) -> dict:
    """Explain, never waive, changed source provenance between proof boundaries.

    Report relative paths only, not private contents or file hashes. The
    canonical source-bound refusal remains mandatory even for one changed file.
    """
    old = start.get('fileDigests', {})
    new = end.get('fileDigests', {})
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())
    modified = sorted(p for p in old.keys() & new.keys() if old[p] != new[p])
    return {
        'status': 'source-modified-during-proof',
        'previousFingerprint': start.get('fingerprint'),
        'currentFingerprint': end.get('fingerprint'),
        'provenanceMetadataChanged': any(start.get(k) != end.get(k)
            for k in ('schema', 'algorithm', 'scope', 'selectionMethod', 'sourceRoots')),
        'fileChanges': {
            kind: {'count': len(paths), 'paths': paths[:max_paths],
                   'additionalPathsOmitted': max(0, len(paths) - max_paths)}
            for kind, paths in (('added', added), ('removed', removed), ('modified', modified))
        },
    }


def seed_reference_file(workspace: Path) -> Path:
    """Seed byte-for-byte browser proof input, including on Windows.

    Path.write_text() uses platform newline translation; that would make
    Chromium receive CRLF even though the proof requires the LF fixture.
    """
    original = workspace / 'example.txt'
    original.write_bytes(b'original\n')
    return original


def run(repo_root:Path, *, authority: PortableCalculatorExternalAuthority | None = None) -> dict:
    start_with_digests=build_repository_provenance(repo_root, include_file_digests=True)
    start_provenance={k:v for k,v in start_with_digests.items() if k!='fileDigests'}
    evidence={"schema":"mcel.portable-intent-execution.v1","scope":"independent-package-domain-operations",
              "liveProviderProofRequested":bool(authority),
              "passed":False,"fullSemanticRuntimeProven":False,"applications":[],"missingExternalProviderProof":[]}
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory(prefix='mcel-portable-') as temporary:
        temp=Path(temporary);output=temp/'packages';workspace=temp/'workspace';workspace.mkdir()
        original=seed_reference_file(workspace)
        for app in ('calculator','code-editor'):
            manifest=build_standalone_package(repo_root,app,output,portable=True)
            evidence['applications'].append({'appId':app,'packageFingerprint':manifest['source']['packageFingerprint'],
                                             'sourceBindings':generated_binding(manifest, output / app),
                                             'declaredIntents':manifest['declaredIntents'],'executed':[],
                                             'refusals':[],'safetyRefusals':[]})
        server=ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=str(output)))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        broker=PortableWorkspaceBroker(workspace, external=authority.handlers() if authority else None)
        try:
            with sync_playwright() as playwright:
                import os, shutil
                executable = (os.environ.get('MCEL_CHROMIUM_EXECUTABLE') or os.environ.get('CHROMIUM_EXECUTABLE')
                              or shutil.which('chromium') or shutil.which('msedge'))
                browser=playwright.chromium.launch(headless=True, executable_path=executable,
                    args=['--no-sandbox','--disable-dev-shm-usage'] if executable else [])
                try:
                    for row in evidence['applications']:
                        app=row['appId'];width=1140 if app=='calculator' else 1220
                        page=browser.new_page(viewport={'width':width,'height':920})
                        page.expose_function('__mcel_portable_capability',lambda intent,payload: broker.request(intent,payload))
                        try:
                            page.goto(f'http://127.0.0.1:{server.server_address[1]}/{app}/index.html',wait_until='networkidle',timeout=20000)
                            page.wait_for_function('window.McelStandalonePackageHost?.ready===true',timeout=20000)
                            outcome=page.evaluate('''async ({live}) => {
                              const h=window.McelStandalonePackageHost;
                              const seen=[];const reject=[];const safetyReject=[];const externalInputReject=[];
                              const externalReceipts=[];const semanticChecks=[];let localValidated=false;let step='startup';
                              async function run(name,payload){step=name;const r=await h.dispatch(name,payload);seen.push(name);return r;}
                              async function refused(name,payload,expected,security=false){
                                step='refusal:'+name;let error='';
                                try{await h.dispatch(name,payload);}catch(e){error=String(e?.message||e);}
                                if(!error)throw Error('unwanted success '+name);
                                if(!error.startsWith('MCEL_PORTABLE_REFUSED:') || !error.includes(expected))
                                  throw Error('unexpected refusal '+name+': '+error);
                                (security?safetyReject:reject).push(name);
                              }
                              try {
                              if(h.appId==='calculator'){
                                await run('clearExpression');await run('enterToken',{token:'2'});
                                const math=await run('evaluateExpression',{expression:'3 m + 40 cm'});
                                if(math.receipt.displayValue!=='3.4 m')throw Error('unit result not verified');
                                await run('switchMode',{mode:'graphing'});
                                const graph=await run('drawGraph',{expression:'x*2',width:50});
                                if(graph.receipt.samples.length!==51)throw Error('graph result invalid');
                                await run('resetGraph');
                                for(const n of ['askModelForExpression','askModelForGraphExpression',
                                  'askModelForMathicsExpression','evaluateMathics','askResultQuestion']) await refused(n,{},'missing authorized capability '+n);
                                localValidated=true;
                                if(live){
                                  h.configureCapabilities({request:(intent,payload)=>window.__mcel_portable_capability(intent,payload)});
                                  for(const n of ['askModelForExpression','askModelForGraphExpression',
                                    'askModelForMathicsExpression','evaluateMathics','askResultQuestion']){
                                    await refused(n,{},'missing-or-invalid-',true);
                                    externalInputReject.push(n);
                                  }
                                  const exec=async (name,payload)=>{
                                    const r=await run(name,payload);
                                    if(r.receipt?.evidence?.schema!=='mcel.external-provider-receipt.v1'
                                        ||r.receipt.evidence.intent!==name || r.receipt.evidence.verifiedExecution!==true)
                                      throw Error('provider receipt invalid '+name);
                                    externalReceipts.push({intent:name,receipt:r.receipt});
                                    return r;
                                  };
                                  const arithmetic=await exec('askModelForExpression',{
                                    prompt:'Write an arithmetic expression for two plus three; output only the expression.'});
                                  const calc=globalThis.MainComputerCalculatorCore;
                                  if(!calc)throw Error('pure Calculator core absent');
                                  const numeric=calc.evaluateCalculatorExpression(arithmetic.receipt.text);
                                  if(numeric.ok!==true||numeric.value!==5||h.inspect().domain.expression!==arithmetic.receipt.text)
                                    throw Error('AI arithmetic semantic assertion failed');
                                  semanticChecks.push('askModelForExpression');
                                  const aiGraph=await exec('askModelForGraphExpression',{
                                    prompt:'Return a graph expression for twice x; output only the expression.'});
                                  const sampled=calc.sampleCalculatorGraphExpression(aiGraph.receipt.text,
                                    {xMin:0,xMax:6,yMin:-10,yMax:10},60);
                                  const atThree=sampled.samples?.find(s=>Math.abs(s.x-3)<1e-9);
                                  if(sampled.ok!==true||!atThree||Math.abs(atThree.y-6)>1e-7
                                      ||h.inspect().domain.graphExpression!==aiGraph.receipt.text)
                                    throw Error('AI graph semantic assertion failed');
                                  semanticChecks.push('askModelForGraphExpression');
                                  const symbolic=await exec('askModelForMathicsExpression',{
                                    prompt:'Write a Mathics expression to expand (x + 1) squared.'});
                                  if(h.inspect().domain.mathicsExpression!==symbolic.receipt.text)
                                    throw Error('AI symbolic state assertion failed');
                                  semanticChecks.push('askModelForMathicsExpression');
                                  const symMath=await exec('evaluateMathics',{expression:'2 + 3'});
                                  if(symMath.receipt.text.trim()!=='5'||h.inspect().domain.mathicsResult!=='5')
                                    throw Error('Mathics arithmetic semantic assertion failed');
                                  semanticChecks.push('evaluateMathics');
                                  const answer=await exec('askResultQuestion',{
                                    question:'What is 2 plus 3? Answer with only the numeral.'});
                                  if(!/^5\\.?$/.test(answer.receipt.text.trim())
                                      ||h.inspect().domain.answer!==answer.receipt.text)
                                    throw Error('AI result question semantic assertion failed');
                                  semanticChecks.push('askResultQuestion');
                                }
                              }else{
                                h.configureCapabilities({request:(intent,payload)=>window.__mcel_portable_capability(intent,payload)});
                                await run('inspectWorkspace');
                                const opened=await run('openFile',{path:'example.txt'});
                                if(opened.receipt.content!=='original\\n')throw Error('open bytes wrong: '+JSON.stringify(opened.receipt.content));
                                await run('editDraft',{text:'saved\\n'});
                                const saved=await run('saveFile');
                                if(saved.receipt.transaction.changed!==true)throw Error('save receipt invalid');
                                await run('editDraft',{text:'uncommitted'});await run('discardDraft');
                                await run('editDraft',{text:'reviewed\\n'});
                                const preview=await run('previewAiderPlan');
                                await refused('applyReviewedPatch',{handle:preview.receipt.handle,reviewed:false,approved:false},'reviewed-patch-approval-required',true);
                                await run('applyReviewedPatch',{handle:preview.receipt.handle,reviewed:true,approved:true});
                                await run('closeFile');
                              }
                              return {ready:h.ready,portableMode:h.portableMode,seen,rejected:reject,safetyRejected:safetyReject,
                                externalInputReject,externalReceipts,semanticChecks,localValidated,state:h.inspect().domain};
                              } catch (e) {
                                return {ready:h.ready,portableMode:h.portableMode,seen,rejected:reject,safetyRejected:safetyReject,
                                  externalInputReject,externalReceipts,semanticChecks,localValidated,
                                  failedAt:step,error:String(e?.message||e),state:h.inspect().domain};
                              }
                            }''', {'live': bool(authority)})
                            row['executed']=outcome['seen'];row['refusals']=outcome['rejected'];
                            row['safetyRefusals']=outcome['safetyRejected'];row['ready']=outcome['ready'];
                            row['portableMode']=outcome['portableMode']
                            if app == 'calculator':
                                row['localValidated']=outcome.get('localValidated',False)
                                # Report the final browser-observed domain state;
                                # the independent auditor correlates it to receipts.
                                state=outcome.get('state') or {}
                                row['calculatorFinalState']={key:state.get(key) for key in (
                                    'expression','graphExpression','mathicsExpression','mathicsResult','answer')}

                            if app == 'calculator' and authority:
                                row['externalReceipts']=outcome.get('externalReceipts',[])
                                row['externalSemanticChecks']=outcome.get('semanticChecks',[])
                                row['externalInputRefusals']=outcome.get('externalInputReject',[])
                                row['externalModelAttempts']=list(authority.model_attempts)
                                # Reconcile browser output against the independent
                                # Python-side live backend receipt inventory.
                                records=authority.records
                                seen_receipts=row['externalReceipts']
                                matching=(len(records)==5 and len(seen_receipts)==5
                                  and all(r['intent']==b['intent'] and
                                    r['backend']==b['receipt'].get('provider') and
                                    r['outputSha256']==_sha(b['receipt'].get('text')) and
                                    b['receipt'].get('evidence',{}).get('inputSha256')==r['inputSha256'] and
                                    b['receipt'].get('evidence',{}).get('outputSha256')==r['outputSha256']
                                    for r,b in zip(records,seen_receipts)))
                                # Check the model-generated Mathics expression by
                                # actual execution and compare canonical Mathics
                                # output against the fixture's known expansion.
                                symbolic_ok=False
                                if matching:
                                    try:
                                        symbolic_ok=(authority._mathics(records[2]['text'])
                                          == authority._mathics('Expand[(x + 1)^2]'))
                                    except Exception as exc:
                                        row['symbolicVerificationError']=str(exc)
                                row['externalProviderVerified']=bool(matching and symbolic_ok)
                                row['externalProviderReceipts']=[{k:v for k,v in r.items() if k!='text'} for r in records]

                            if outcome.get('error'):
                                row['status']='refused';row['failedAt']=outcome.get('failedAt')
                                row['error']=outcome['error']
                                continue
                            if app=='code-editor':
                                actual=original.read_bytes()
                                row['sourceTextAfterProof']=actual.decode('utf-8')
                                row['filesystemEffects']=list(broker.effects)
                                if actual!=b'reviewed\n' or len(broker.effects)!=2:
                                    row['status']='refused';row['failedAt']='filesystem-effect-verification'
                                    row['error']='real filesystem effects did not match expected bytes'
                                    continue
                            else:
                                evidence['missingExternalProviderProof']=[] if authority else outcome['rejected']
                            row['status']='pass'
                        except Exception as exc:
                            row['status']='refused';row['error']=str(exc)
                        finally:page.close()
                finally:browser.close()
        finally:server.shutdown();server.server_close();thread.join(timeout=5)
    if authority:
        calc=next((r for r in evidence['applications'] if r.get('appId')=='calculator'),{})
        evidence['missingExternalProviderProof']=sorted(set(EXTERNAL_INTENTS)-set(calc.get('externalSemanticChecks',[])))
    evidence['generatedAt']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    evidence['repositoryProvenance']=start_provenance
    result=finalize_evidence(evidence, certified_runtime=type(authority) is PortableCalculatorExternalAuthority)
    # A repository modified while the browser was running cannot yield a
    # source-bound passing report; refuse even when all fixtures succeeded.
    end_with_digests=build_repository_provenance(repo_root, include_file_digests=True)
    end_provenance={k:v for k,v in end_with_digests.items() if k!='fileDigests'}
    if end_provenance != start_provenance:
        result.update(status='refused',passed=False,fullSemanticRuntimeProven=False,
                      externalProviderProven=False,reason='repository-changed-during-proof',
                      repositoryDrift=source_drift_diagnostics(start_with_digests, end_with_digests))
    return result


def finalize_evidence(evidence: dict, *, certified_runtime: bool = False) -> dict:
    """Fail closed: only an actual Chromium proof plus correlated live receipts
    can promote the 19-intent bounded semantic-fixture certification.
    """
    local = {
        'calculator': {'clearExpression', 'enterToken', 'evaluateExpression',
                       'switchMode', 'drawGraph', 'resetGraph'},
        'code-editor': {'inspectWorkspace', 'openFile', 'editDraft', 'saveFile',
                        'discardDraft', 'previewAiderPlan', 'applyReviewedPatch', 'closeFile'},
    }
    external = set(EXTERNAL_INTENTS)
    rows = evidence.get('applications', [])
    ids = [row.get('appId') for row in rows]
    live = evidence.get('liveProviderProofRequested') is True and certified_runtime
    valid = []
    external_ok = False
    for row in rows:
        app = row.get('appId')
        observed = row.get('executed', [])
        row['successfulCallCount'] = len(observed)
        row['distinctExecutedIntents'] = len(set(observed))
        if app not in local:
            valid.append(False)
            continue
        if app == 'calculator' and live:
            # Keep the six locally observed intents certified even if a later
            # model/Mathics call refuses. External failure is not a regression
            # in local computation, but never qualifies for full certification.
            accepted=(row.get('ready') is True and row.get('portableMode') is True
                      and row.get('localValidated') is True
                      and local[app].issubset(set(observed))
                      and set(observed).issubset(local[app] | external)
                      and set(row.get('refusals',[]))==external
                      and len(row.get('refusals',[]))==5)
        else:
            accepted=(row.get('status')=='pass' and row.get('ready') is True
                      and row.get('portableMode') is True
                      and set(observed)==local[app]
                      and len(row.get('refusals',[]))==(5 if app=='calculator' else 0))
        if app == 'calculator':
            accepted = (accepted and set(row.get('refusals', [])) == external)
            if live:
                # Input refusals remain separate from ungranted-capability refusals.
                external_ok=(row.get('status')=='pass' and set(observed)==local[app] | external
                    and row.get('externalProviderVerified') is True
                    and len(row.get('externalProviderReceipts', []))==5
                    and set(row.get('externalSemanticChecks', []))==external
                    and len(row.get('externalSemanticChecks', []))==5
                    and set(row.get('externalInputRefusals', []))==external
                    and len(row.get('externalInputRefusals', []))==5
                    and set(row.get('safetyRefusals', []))==external
                    and len(row.get('safetyRefusals', []))==5)
                # Preserve the independent local verdict if external proof fails.
            else:
                accepted = accepted and not row.get('safetyRefusals')
        else:
            effects = row.get('filesystemEffects', [])
            accepted = (accepted and row.get('refusals') == []
                        and row.get('safetyRefusals') == ['applyReviewedPatch']
                        and row.get('sourceTextAfterProof') == 'reviewed\n'
                        and [effect.get('intent') for effect in effects]
                            == ['saveFile', 'applyReviewedPatch'])
        valid.append(accepted)
    local_passed = (len(ids) == len(local) and set(ids) == set(local)
                    and len(valid) == 2 and all(valid)
                    and (live or (set(evidence.get('missingExternalProviderProof',[]))==external
                         and len(evidence.get('missingExternalProviderProof',[]))==5)))
    evidence['portableLocalAndWorkspacePassed'] = local_passed
    evidence['externalProviderProven'] = bool(live and external_ok and local_passed
        and evidence.get('missingExternalProviderProof',[])==[])
    evidence['passed'] = evidence['externalProviderProven']
    evidence['fullSemanticRuntimeProven'] = evidence['passed']
    evidence['status'] = ('pass' if evidence['passed'] else
                          'partial-pass' if local_passed else 'refused')
    evidence['reason'] = ('bounded-live-19-intent-semantic-fixtures-passed' if evidence['passed']
                          else 'external-model-mathics-result-capabilities-not-proven' if local_passed
                          else 'portable-local-or-workspace-operation-refused')
    return evidence



def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root',type=Path,default=REPO)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--json',action='store_true')
    parser.add_argument('--authorize-external',action='store_true',
                        help='Explicitly grant live, local Ollama and Mathics3 for the five external intents')
    parser.add_argument('--model',help='Exact local Ollama model identity; mandatory for live grant')
    parser.add_argument('--ollama-url',default='http://127.0.0.1:11434')
    parser.add_argument('--external-timeout-s',type=float,default=45.0)
    args=parser.parse_args()
    try:
        if args.model and not args.authorize_external:
            raise ValueError('model-supplied-without-explicit-external-authorization')
        if args.authorize_external and not args.model:
            raise ValueError('explicit-model-required-for-external-authorization')
        authority=(PortableCalculatorExternalAuthority(repo_root=args.repo_root,
                model=args.model,ollama_url=args.ollama_url,timeout_s=args.external_timeout_s)
                if args.authorize_external else None)
        result=run(args.repo_root.resolve(),authority=authority)
    except Exception as exc:result={'schema':'mcel.portable-intent-execution.v1','status':'refused',
                                 'passed':False,'fullSemanticRuntimeProven':False,'error':str(exc)}
    text=json.dumps(result,indent=2,sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(text+'\n',encoding='utf-8')
    if args.json:
        print(text)
    else:
        print(result['status'],result.get('reason',result.get('error','')))
        if result.get('reason') == 'repository-changed-during-proof':
            drift = result.get('repositoryDrift') or {}
            for kind, detail in (drift.get('fileChanges') or {}).items():
                if detail.get('count'):
                    print(f"  {kind}: {detail['count']} source path(s)")
                    for path in detail.get('paths', [])[:10]:
                        print(f"    {path}")
                    omitted = max(0, detail['count'] - min(10, len(detail.get('paths', []))))
                    if omitted:
                        print(f"    ... and {omitted} more; see the saved proof JSON")
            if drift.get('provenanceMetadataChanged'):
                print('  provenance selection metadata changed during execution')
    return 0 if (result.get('passed') if args.authorize_external else result.get('portableLocalAndWorkspacePassed')) is True else 4


if __name__=='__main__':raise SystemExit(main())
