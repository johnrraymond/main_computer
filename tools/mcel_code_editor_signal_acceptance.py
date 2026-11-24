"""Code Editor functional + integrated-signal acceptance, with production untouched.

Two independent real semantic Code Editor mounts receive the same deterministic
HTTP inputs and Monaco-compatible text model. A test-only authorization change
is made to the *in-memory HTML*, never to the shared runtime file or canonical
host. The incumbent-vs-semantic functional parity suite is a mandatory gate.
Real Windows source writes, native Monaco and Aider remain separate live gates.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright
from tools import mcel_code_editor_functional_parity_frob as parity
from tools.mcel_phase2b_browser import launch_semantic_browser
from tools.mcel_signal_differential_acceptance import SNAPSHOT, PIXELS, _diff

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=Path('runtime/reports/mcel-code-editor-full-functional/report.json')
KINDS=('condition','display','selected-record','typed-record')
# Exact opt-in is intentionally test-side. Nothing can enable signals in the
# normal Code Editor route by installing this file alone.
GATE_RE=re.compile(r'const integratedAllowed=!isolatedSignalTest && options\.signalSchedulerIntegration===true &&\s*\([^;]{30,900}?\);')

# The parity mock is installed before the browser mount. We intercept the
# captured fetcher so a pending request may be held/faulted deterministically.
TRANSPORT_CONTROL=r'''<script>
(function(){
 const fetcher=window.fetch;
 window.__editorHoldNext=null;
 window.__editorFailNext=null;
 window.__editorHeld=[];
 window.__editorRelease=()=>{let h=window.__editorHeld.splice(0);h.forEach(resolve=>resolve());return h.length;};
 window.fetch=async(url,options)=>{
   const path=String(url),hold=window.__editorHoldNext,fail=window.__editorFailNext;
   if(hold&&path.endsWith(hold)){
     window.__editorHoldNext=null;
     await new Promise(resolve=>window.__editorHeld.push(resolve));
   }
   if(fail&&path.endsWith(fail)){
     window.__editorFailNext=null;
     throw new Error('CODE_EDITOR_SIGNAL_INJECTED_TRANSPORT_FAILURE');
   }
   return fetcher(url,options);
 };
})();
</script>'''


def _html(repo:Path,enabled:bool,*,kinds=None)->str:
    html=parity.candidate_html(repo)
    assert html.count('signalSchedulerIntegration:false')==1,'EDITOR_ISOLATED_REFERENCE_CONTRACT_CHANGED'
    assert html.count('signalSchedulerStartEnabled:false')==1,'EDITOR_ISOLATED_REFERENCE_START_CHANGED'
    if kinds is not None and (not kinds or len(kinds)!=len(set(kinds)) or any(x not in KINDS for x in kinds)):
        raise ValueError('UNAUTHORIZED_CONSUMER_CLASS')
    if enabled:
        html=html.replace('signalSchedulerIntegration:false','signalSchedulerIntegration:true',1)
        html=html.replace('signalSchedulerStartEnabled:false','signalSchedulerStartEnabled:true',1)
        if kinds is not None:
            html=html.replace('signalSchedulerStartEnabled:true',
                 'signalSchedulerCertifiedKinds:'+json.dumps(list(kinds))+',signalSchedulerStartEnabled:true',1)
    assert html.count(parity.FIXTURE_JS)==1,'EDITOR_FIXTURE_CHANGED'
    return html.replace(parity.FIXTURE_JS, parity.FIXTURE_JS+TRANSPORT_CONTROL,1)


def _state(page)->dict:
    obs=page.evaluate(SNAPSHOT)
    obs['transport']=page.evaluate('window.__editorCalls')
    obs['effects']=page.evaluate('window.__editorEffects')
    obs['files']=page.evaluate('window.__editorFiles')
    obs['models']=page.evaluate('window.__editorMonacoMounts')
    obs['writeStatuses']=page.evaluate('''()=>({a:window.__editorPort.documentWriteStatus('src/a.js'),
                                          b:window.__editorPort.documentWriteStatus('src/b.js')})''')
    return obs


def _stable(page):
    page.evaluate('''async()=>{
      await document.fonts.ready;
      await new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done)));
    }''')
    page.wait_for_timeout(80)


def _click(page,intent):parity.click(page,intent)


def _form(page,intent):
    form=page.locator('form[data-intent="intent:'+intent+'"]')
    parity.reveal(page,form)
    return form


def _wait_receipt(page,intent,outcome='completed',n=1):
    page.wait_for_function('''([intent,outcome,n])=>window.__mcelWorkbench.app.receipts()
      .filter(x=>x.intentId==='intent:'+intent && x.disposition===outcome).length>=n''',
      arg=[intent,outcome,n],timeout=9000)


def differential(repo:Path=ROOT,*,profile='full',kinds=None)->dict:
    if profile not in ('smoke','full','class'):raise ValueError('INVALID_PROFILE')
    errors={'incumbent':[],'signals':[]};stages=[];seen=set();total_skips=0;counter=0;epoch=0
    skip_kinds={};pages={}
    with sync_playwright() as pw:
      browser,source=launch_semantic_browser(pw)
      try:
        def mount(role):
            page=browser.new_page(viewport={'width':1272,'height':900},device_scale_factor=1)
            page.set_default_timeout(10000)
            page.on('pageerror',lambda e,r=role:errors[r].append(str(e)[:900]))
            page.set_content(_html(repo,role=='signals',kinds=kinds))
            page.wait_for_function('()=>!!window.__mcelWorkbench?.app')
            assert page.locator('#mcel-workbench-status').inner_text()=='Semantic candidate ready'
            return page
        pages={role:mount(role) for role in ('incumbent','signals')}
        modes={role:page.evaluate('window.__mcelWorkbench.app.signalCandidateReport()') for role,page in pages.items()}
        refused=pages['incumbent'].evaluate('window.__mcelWorkbench.app.setSignalSchedulingMode("certified")')
        if refused.get('status')!='REFUSED':raise RuntimeError('INCUMBENT_CAN_ENABLE_SIGNAL')
        def record(name,*,pixels=True):
            nonlocal total_skips,counter
            for page in pages.values():_stable(page)
            a=_state(pages['incumbent']);b=_state(pages['signals'])
            diff,delta=_diff(a,b)
            for key in ('transport','effects','files','models','writeStatuses'):
                if a[key]!=b[key]:
                    diff+=1
                    if len(delta)<24:delta.append({'path':key,'incumbent':a[key],'candidate':b[key]})
            pix=None
            # Full combined run owns screenshot parity; per-class witnesses
            # still compare the full DOM/state/effect/receipt graph but avoid
            # duplicating expensive pixel reads for every selective subset.
            if pixels and kinds is None:
                left=pages['incumbent'].screenshot(animations='disabled',caret='hide')
                right=pages['signals'].screenshot(animations='disabled',caret='hide')
                pix=pages['incumbent'].evaluate(PIXELS,{
                    'a':base64.b64encode(left).decode(),'b':base64.b64encode(right).decode()})
            signal=b['signal'];skipped=signal.get('statistics',{}).get('skips',0)
            if skipped<counter:raise RuntimeError('SIGNAL_SKIP_REGRESSION')
            total_skips+=skipped-counter;counter=skipped
            for event in signal.get('history',[]):
                for detail in event.get('actualSkipConsumers',[]):
                    identity=(epoch,event['revision'],detail['id'])
                    if identity in seen:continue
                    seen.add(identity)
                    key=detail['id'].split('/',1)[0]
                    skip_kinds[key]=skip_kinds.get(key,0)+1
            issues=[]
            if diff:issues.append('DOM_STATE_RECEIPT_TRANSPORT_OR_EFFECT_DIFFERENCE')
            if pix and pix['count']>512 and pix['maximum']>24:issues.append('MATERIAL_PIXEL_DIFFERENCE')
            stages.append({'stage':name,'status':'PASS' if not issues else 'FAIL',
                'failures':issues,'differences':diff,'differenceSamples':delta[:14],
                'pixelDifferences':pix['count'] if pix else None,
                'pixelMaxChannelDelta':pix['maximum'] if pix else None,
                'actualSkipTotal':skipped,'mode':signal.get('mode'),
                'fallbacks':signal.get('fallbacks'),
                'effects':len(b['effects']),'requests':len(b['transport']),
                'receipts':len(b['receipts'])})
            print('CODE_EDITOR_SIGNAL_PROGRESS',name,stages[-1]['status'],flush=True)
        def for_each(callback):
            for role,page in pages.items():callback(page)
        def click_file(path):
            for page in pages.values():
                page.locator('[data-record-key="'+path+'"] button').click()
                page.locator('[data-fake-monaco-path="'+path+'"]').wait_for()
        def finish():
            signal=pages['signals'].evaluate('window.__mcelWorkbench.app.signalCandidateReport()')
            ref=pages['incumbent'].evaluate('window.__mcelWorkbench.app.signalCandidateReport()')
            registered={kind:sum(item['id'].startswith(kind+'/') for item in signal.get('consumers',[])) for kind in KINDS}
            failures=[x['stage']+':'+','.join(x['failures']) for x in stages if x['status']!='PASS']
            if modes['incumbent'].get('integrationEnabled') or modes['incumbent'].get('mode')!='disabled':failures.append('INVALID_INCUMBENT_REFERENCE')
            if not modes['signals'].get('integrationEnabled') or modes['signals'].get('mode')!='integrated-opt-in':failures.append('SIGNAL_NOT_ENABLED')
            if signal.get('fallbacks') or signal.get('statistics',{}).get('unsafeFallbacks'):failures.append('UNSAFE_FALLBACK')
            if ref['mode']!='disabled' or ref['integrationEnabled']:failures.append('INCUMBENT_NOT_PRESERVED')
            if not signal['integrationEnabled']:failures.append('SIGNALS_NOT_ENABLED_AT_END')
            if total_skips<1:failures.append('NO_REAL_SKIPS')
            if sum(skip_kinds.values())!=total_skips:failures.append('SKIP_WITNESS_ACCOUNTING_MISMATCH')
            if set(skip_kinds).difference(kinds or KINDS):failures.append('UNAUTHORIZED_CONSUMER_CLASS_SKIPPED')
            if any(errors.values()):failures.append('BROWSER_PAGEERROR')
            return {'status':'PASS_DIFFERENTIAL' if not failures else 'FAIL_DIFFERENTIAL',
              'failures':failures,'browser':source,'stages':stages,'actualSkips':total_skips,
              'skipKinds':skip_kinds,'registeredConsumersByClass':registered,'pageErrors':errors,
              'mountModes':{k:{kk:v.get(kk) for kk in ('mode','integrationEnabled','integratedSupported')} for k,v in modes.items()},
              'limitations':['Monaco is an in-memory model stub','Aider and file writes use deterministic fake HTTP',
                             'Test-only integration authorization exists only in rendered isolated HTML',
                             'No production Code Editor scheduler authorization or cutover']}

        record('initial-mount')
        for_each(lambda page:_click(page,'list-files'))
        for page in pages.values():page.locator('[data-record-key="src"] button[data-select-key="src"]').wait_for()
        record('workspace-roots')
        for page in pages.values():
            page.locator('[data-record-key="src"] button[data-select-key="src"]').click()
            page.locator('[data-record-key="src/a.js"] button').wait_for()
        record('source-directory-inventory')
        click_file('src/a.js');record('open-source-a')
        for_each(lambda page:page.locator('[data-fake-monaco-path="src/a.js"]').fill('const a=3;\n'))
        record('dirty-draft-a')
        # Per-class witness: mounted dependency discovery, file opening and an
        # actual document edit suffice; combined full mode covers all effects.
        if profile=='class':return finish()
        click_file('src/b.js');record('open-source-b')
        for_each(lambda page:page.locator('[data-fake-monaco-path="src/b.js"]').fill('const b=4;\n'))
        record('dirty-draft-b')
        for page in pages.values():
            page.locator('[data-mcel-tab-path="src/a.js"]').click()
            page.locator('[data-fake-monaco-path="src/a.js"]').wait_for()
        record('switch-tabs-preserves-dirty-a')
        for page in pages.values():
            page.evaluate('window.__editorFixture.allowConfirm=false')
            page.locator('[data-mcel-document-save]').click()
        record('cancel-save-without-effect')
        for page in pages.values():
            page.evaluate('window.__editorFixture.allowConfirm=true')
            page.locator('[data-mcel-document-save]').click()
            page.wait_for_function('()=>window.__editorEffects.some(x=>x.kind==="save")')
        record('explicit-save-exact-target')
        for page in pages.values():
            page.evaluate('window.__editorFiles["src/b.js"]="const b=99;\\n"')
            page.locator('[data-mcel-tab-path="src/b.js"]').click()
            page.locator('[data-fake-monaco-path="src/b.js"]').wait_for()
            assert page.locator('[data-mcel-document-save]').is_disabled()
        record('external-change-blocks-stale-draft')
        for page in pages.values():
            page.locator('[data-mcel-close-tab="src/b.js"]').click()
            page.locator('[data-fake-monaco-path="src/a.js"]').wait_for()
            page.locator('[data-fake-monaco-path="src/a.js"]').fill('const a=11;\n')
            _click(page,'prepare-reviewed-change')
            _wait_receipt(page,'prepare-reviewed-change')
            assert not [x for x in page.evaluate('window.__editorEffects') if x['kind']=='review']
        record('review-prepare-without-write')
        for page in pages.values():
            _click(page,'apply-reviewed-change')
            page.wait_for_function('()=>window.__editorEffects.some(x=>x.kind==="review")')
        record('review-apply-exact-once')
        for page in pages.values():
            form=_form(page,'preview-aider-plan')
            form.locator('input[name="instruction"]').fill('Summarize this code')
            form.locator('button[type="submit"]').click()
            _wait_receipt(page,'preview-aider-plan')
        record('aider-dry-run-no-write')
        if profile=='full':
            for page in pages.values():
                _click(page,'aider-context');_wait_receipt(page,'aider-context')
                _click(page,'aider-jobs');_wait_receipt(page,'aider-jobs')
            record('aider-context-and-job-inventory')
            for page in pages.values():
                page.evaluate('window.__editorHoldNext="/aider/run"')
                form=_form(page,'run-aider')
                form.locator('input[name="instruction"]').fill('Make a harmless change')
                form.locator('input[name="files"]').fill('')
                form.locator('button[type="submit"]').click()
                page.wait_for_function('()=>window.__editorHeld.length===1')
            record('aider-request-held-before-response')
            # Roll back while the *HTTP operation itself* is unresolved.
            # It must not cancel, replay, or duplicate the pending request.
            result=pages['signals'].evaluate('window.__mcelWorkbench.app.setSignalSchedulingMode("incumbent")')
            if result.get('status')!='ROLLED_BACK':raise RuntimeError('ROLLBACK_REFUSED:'+str(result))
            epoch+=1;counter=0
            record('rollback-during-inflight-aider')
            for page in pages.values():
                assert page.evaluate('window.__editorRelease()')==1
                _wait_receipt(page,'run-aider','accepted-deferred')
                assert page.evaluate('window.__editorPort.documentWriteStatus("src/a.js")')=='aider-pending'
            record('aider-accepted-after-rollback')
            effects=pages['signals'].evaluate('window.__editorEffects.filter(x=>x.kind==="aider-accepted")')
            invocations=pages['signals'].evaluate('window.__editorCalls.filter(x=>x.path.endsWith("/aider/run"))')
            if len(effects)!=1 or len(invocations)!=1:
                stages[-1]['status']='FAIL';stages[-1]['failures'].append('DUPLICATE_AIDER_DISPATCH')
            for page in pages.values():
                _click(page,'aider-jobs');_wait_receipt(page,'aider-jobs',n=2)
                assert page.evaluate('window.__editorPort.documentWriteStatus("src/a.js")')=='aider-pending'
            record('incumbent-exec-keeps-aider-pending')
            for page in pages.values():
                page.evaluate('window.__editorFixture.aiderTerminal=true')
                _click(page,'aider-jobs');_wait_receipt(page,'aider-jobs',n=3)
                page.wait_for_function('()=>window.__editorPort.documentWriteStatus("src/a.js")!=="aider-pending"')
            record('aider-terminal-clears-pending')
            result=pages['signals'].evaluate('window.__mcelWorkbench.app.setSignalSchedulingMode("certified")')
            if result.get('status')!='ENABLED':raise RuntimeError('REENABLE_REFUSED:'+str(result))
            epoch+=1;counter=0
            record('reenable-full-reconciliation')
            for page in pages.values():
                _click(page,'list-component-docs')
                page.locator('[data-record-key="component.one"]').wait_for()
                page.locator('[data-record-key="component.one"] button').click()
                _click(page,'read-component-doc')
                page.wait_for_function('()=>window.__mcelWorkbench.app.readState("state:component-doc-content").includes("<h1>")')
            record('documentation-contents-escaped')
            for page in pages.values():
                _click(page,'inspect-scm-component-contract')
                _wait_receipt(page,'inspect-scm-component-contract')
                _click(page,'inspect-scm-route-contract')
                _wait_receipt(page,'inspect-scm-route-contract')
            record('scm-inspection-read-only')
            for page in pages.values():
                page.locator('[data-record-key="src/studio.html"] button').click()
                page.locator('[data-fake-monaco-path="src/studio.html"]').wait_for()
                _click(page,'check-studio-draft')
                _wait_receipt(page,'check-studio-draft')
            record('studio-source-check')
            for page in pages.values():
                _click(page,'preview-studio-serialization')
                _wait_receipt(page,'preview-studio-serialization')
            record('studio-preview-without-write')
            for page in pages.values():
                result=page.evaluate('''()=>{const a=window.__mcelWorkbench.app;
                  const before=a.signalCandidateReport();a.unmount();
                  return {before:before.consumerCount||0,after:a.signalCandidateReport().consumerCount||0};}''')
                if page==pages['signals'] and result['after']!=0:raise RuntimeError('SIGNAL_CONSUMER_LEAK')
                page.close()
            epoch+=1;counter=0
            pages={role:mount(role) for role in ('incumbent','signals')}
            record('unmount-remount-bootstrap')
            for_each(lambda page:_click(page,'list-files'))
            for page in pages.values():page.locator('[data-record-key="src"] button').wait_for()
            record('remount-followup-inventory')
        return finish()
      finally:
        for page in pages.values():
            try:page.close()
            except Exception:pass
        browser.close()


def accept(repo=ROOT,*,profile='full',skip_parity=False):
    failures=[]
    baseline=parity.run(repo) if not skip_parity else None
    if skip_parity:failures.append('INDEPENDENT_PARITY_NOT_RUN')
    elif baseline.get('status')!='PASS_ISOLATED':failures.append('CODE_EDITOR_FUNCTIONAL_PARITY_INCOMPLETE')
    try:comparison=differential(repo,profile=profile)
    except Exception as exc:comparison={'status':'FAIL_DIFFERENTIAL','stages':[],'actualSkips':0,
                                        'failures':[type(exc).__name__+': '+str(exc)[:1700]]}
    if comparison['status']!='PASS_DIFFERENTIAL':failures.append('SIGNAL_DIFFERENTIAL_INCOMPLETE')
    classes=[]
    if profile=='full' and comparison['status']=='PASS_DIFFERENTIAL':
        consumers=comparison.get('registeredConsumersByClass',{})
        if not any(consumers.values()):failures.append('NO_ELIGIBLE_CONSUMERS')
        for kind in KINDS:
            if not consumers.get(kind):
                classes.append({'class':kind,'status':'NOT_APPLICABLE',
                                'reason':'No Code Editor consumer of this class mounted'})
                continue
            try:
                witness=differential(repo,profile='class',kinds=(kind,))
                valid=witness['status']=='PASS_DIFFERENTIAL' and witness['actualSkips']>0 and set(witness['skipKinds'])=={kind}
                classes.append({'class':kind,'status':'PASS_CERTIFIED_CLASS' if valid else 'BLOCKED',
                    'actualSkips':witness.get('actualSkips',0),'failures':witness.get('failures',[])})
            except Exception as exc:classes.append({'class':kind,'status':'BLOCKED','failures':[str(exc)[:1200]]})
        if any(x['status']=='BLOCKED' for x in classes):failures.append('CLASS_CERTIFICATION_INCOMPLETE')
    def fp(relative):return hashlib.sha256((repo/relative).read_bytes()).hexdigest()
    return {'schema':'mcel.code-editor-full-functional.v1','generatedAt':datetime.now(timezone.utc).isoformat(),
        'status':'PASS_ISOLATED_FUNCTIONAL_WITH_SIGNALS' if not failures else 'BLOCKED',
        'profile':profile,'failures':failures,'functionalParity':baseline,'signalDifferential':comparison,
        'consumerClasses':classes,'fingerprints':{
          'runtimeSha256':fp('main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js'),
          'applicationSha256':fp('mcel_apps/code-editor/application.js')},
        'boundaries':{'productionSignalDefault':'CERTIFIED_WHEN_PROMOTED', 'productionPromotionAuthorized':False,
          'canonicalRoute':'/applications/code-editor',
          'realWindowsSourceWrites':'NOT_TESTED','nativeMonaco':'NOT_TESTED','realAider':'NOT_TESTED',
          'actualHostedRoute':'NOT_TESTED','referenceScheduler':'INCUMBENT',
          'backendTransport':'IN_MEMORY_DETERMINISTIC','sourceWriteAuthority':'SIMULATED_ONLY'}}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=ROOT)
    p.add_argument('--mode',choices=['isolated'],default='isolated')
    p.add_argument('--profile',choices=['smoke','full'],default='full')
    p.add_argument('--signals',choices=['integrated'],default='integrated')
    p.add_argument('--skip-parity',action='store_true',help='Diagnostic only; cannot certify full functionality')
    p.add_argument('--report',type=Path,default=DEFAULT)
    args=p.parse_args(argv)
    repo=args.repo.resolve()
    output=accept(repo,profile=args.profile,skip_parity=args.skip_parity)
    target=args.report if args.report.is_absolute() else repo/args.report
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(output,indent=2,sort_keys=True,ensure_ascii=False)+'\n',encoding='utf8')
    for stage in output['signalDifferential'].get('stages',[]):
        print('CODE_EDITOR_SIGNAL',stage['stage'],stage['status'],
              'differences='+str(stage['differences']),
              'skips='+str(stage['actualSkipTotal']),flush=True)
    diff=output['signalDifferential']
    print('CODE_EDITOR_SIGNAL_OVERALL',diff['status'],'skips='+str(diff['actualSkips']),flush=True)
    for item in output['consumerClasses']:
        print('CODE_EDITOR_SIGNAL_CLASS',item['class'],item['status'],'skips='+str(item.get('actualSkips',0)),flush=True)
    print('CODE_EDITOR_FUNCTIONAL_PARITY',output['functionalParity']['status'] if output['functionalParity'] else 'NOT_RUN',flush=True)
    print('CODE_EDITOR_FUNCTIONAL_OVERALL',output['status'],'report:',target,flush=True)
    if output['failures']:
        print('CODE_EDITOR_ACCEPTANCE_BLOCKERS',output['failures'],diff.get('failures',[]),flush=True)
    return 0 if output['status']=='PASS_ISOLATED_FUNCTIONAL_WITH_SIGNALS' else 1

if __name__=='__main__':raise SystemExit(main())
