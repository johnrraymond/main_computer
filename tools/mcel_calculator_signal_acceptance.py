"""Fail-closed Calculator scheduler certification; never authorizes production default.

The canonical promoted semantic Calculator is mounted twice in isolated Chromium
with identical deterministic service transports. The incumbent is the reference;
the signal mount alone is explicitly authorized using a test-only runtime marker.
All existing independent legacy-versus-semantic parity is a *separate mandatory*
gate: a green scheduler comparison cannot conceal missing incumbent functionality.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools import mcel_calculator_functional_parity_frob as legacy
from tools.mcel_phase2b_browser import launch_semantic_browser
from tools.mcel_signal_differential_acceptance import SNAPSHOT, PIXELS, _diff

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=Path('runtime/reports/mcel-calculator-full-functional/report.json')
KINDS=('condition','display','selected-record','typed-record')

# This snapshot uses the shared, fully dynamic semantic renderer rather than
# comparing only calculator-specific state fields.
SNAPSHOT_CALCULATOR=(SNAPSHOT.replace("getElementById('mcel-workbench')", "getElementById('mcel-expanded-root')")
 .replace('window.__mcelSlh?.app||window.__mcelWorkbench?.app','window.__mcelExpanded?.app'))

# Self-contained memory-only transport: no real Mathics, chat, or file request.
TRANSPORT=r'''() => {
  const store=new Map();
  Object.defineProperty(window,'localStorage',{configurable:true,value:{
    getItem:k=>store.get(String(k))??null,
    setItem:(k,v)=>store.set(String(k),String(v)),
    removeItem:k=>store.delete(String(k)),clear:()=>store.clear(),
    key:i=>[...store.keys()][i]??null,get length(){return store.size;}}});
  window.__calcSignalCalls=[];
  window.__calcSignalFailNext=null;
  window.__calcSignalHoldNext=null;
  window.__calcSignalHolds=[];
  window.__calcSignalRelease=()=>{
    const holds=window.__calcSignalHolds.splice(0);
    holds.forEach(fn=>fn());return holds.length;
  };
  window.fetch=async (url,options={})=>{
    const path=String(url),body=JSON.parse(options.body||'{}');
    window.__calcSignalCalls.push({path,body});
    if(window.__calcSignalHoldNext&&path.endsWith(window.__calcSignalHoldNext)){
      window.__calcSignalHoldNext=null;
      await new Promise(resolve=>window.__calcSignalHolds.push(resolve));
    }
    if(window.__calcSignalFailNext&&path.endsWith(window.__calcSignalFailNext)){
      window.__calcSignalFailNext=null;
      throw Error('CALCULATOR_SIGNAL_INJECTED_TRANSPORT_FAILURE');
    }
    const data=path.endsWith('/mathics/prepare')?{ok:true,ready:true}
      :path.endsWith('/mathics/evaluate')?{ok:true,result_text:body.expression==='3+3'?'6':'4'}
      :path.endsWith('/mathics/ask')?{ok:true,expression:'Integrate[x^2,x]'}
      :path.endsWith('/qa')?{ok:true,answer:'The result is four.'}
      :path==='/api/chat'?{ok:true,content:'2+2'}
      :{ok:false,error:'UNEXPECTED_SERVICE_ENDPOINT'};
    return {ok:data.ok,status:data.ok?200:404,json:async()=>data};
  };
}'''


def _mount_html(repo:Path,enable:bool,*,kinds=None,marker=True)->str:
    """Literal opt-in only on a local test copy. Actual canonical host untouched."""
    html=render_expanded_page(repo,promoted=True)
    needle="capabilities:port,presentation:'expert'"
    assert html.count(needle)==1,'CALCULATOR_MOUNT_CONTRACT_CHANGED'
    if kinds is not None and (not kinds or len(set(kinds))!=len(kinds) or any(k not in KINDS for k in kinds)):
        raise ValueError('UNAUTHORIZED_CALCULATOR_CONSUMER_KIND')
    opts=(f",signalSchedulerIntegration:{str(enable).lower()},"
          f"signalSchedulerStartEnabled:{str(enable).lower()}")
    if kinds is not None:
        opts+=",signalSchedulerCertifiedKinds:"+json.dumps(list(kinds))
    html=html.replace(needle,needle+opts,1)
    if marker:
        assert html.count('</head>')==1
        html=html.replace('</head>',
            '<script>window.__MCEL_CALCULATOR_SIGNAL_ACCEPTANCE__=true;</script></head>',1)
    return html


def _state(page):
    snapshot=page.evaluate(SNAPSHOT_CALCULATOR)
    snapshot['transport']=page.evaluate('window.__calcSignalCalls')
    return snapshot


def _settle(page):
    page.evaluate('''async()=>{
      await document.fonts.ready;
      await new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done)));
    }''')
    page.wait_for_timeout(80)


def _form(page,intent):
    return page.locator(f'form[data-intent="intent:{intent}"]')


def _mode(page,mode):
    legacy._select_mode(page,mode)


def _wait_state(page,name,desired):
    page.wait_for_function('''([name,value])=>window.__mcelExpanded.app.readState(name)===value''',
                           arg=[name,desired],timeout=9000)


def signal_differential(repo:Path=ROOT,*,profile='full',certified_kinds=None)->dict:
    if profile not in ('smoke','full'):raise ValueError('INVALID_PROFILE')
    errors={'incumbent':[],'signals':[]}
    stages=[];seen=set();skips=0;by_class={};generation=0;counter=0;pages={}
    with sync_playwright() as pw:
      browser,browser_source=launch_semantic_browser(pw)
      try:
        def mount(role):
          page=browser.new_page(viewport={'width':1180,'height':900},device_scale_factor=1)
          page.set_default_timeout(10000)
          page.on('pageerror',lambda e,r=role:errors[r].append(str(e)[:800]))
          page.route('**/*',lambda route:route.abort())
          page.evaluate(TRANSPORT)
          page.set_content(_mount_html(repo,role=='signals',kinds=certified_kinds))
          page.wait_for_function('()=>!!window.__mcelExpanded?.app')
          assert page.locator('#mcel-expanded-diagnostic').inner_text()=='Semantic Calculator ready'
          return page
        pages={role:mount(role) for role in ('incumbent','signals')}
        initial={role:page.evaluate('window.__mcelExpanded.app.signalCandidateReport()') for role,page in pages.items()}
        unauthorized=pages['incumbent'].evaluate('window.__mcelExpanded.app.setSignalSchedulingMode("certified")')
        def record(name,*,pixels=True):
          nonlocal skips,counter
          for page in pages.values():_settle(page)
          a=_state(pages['incumbent']);b=_state(pages['signals'])
          n,delta=_diff(a,b)
          if a['transport']!=b['transport']:
            n+=1;delta.append({'path':'transport','incumbent':a['transport'],'candidate':b['transport']})
          pix=None
          if pixels:
            left=pages['incumbent'].screenshot(animations='disabled',caret='hide')
            right=pages['signals'].screenshot(animations='disabled',caret='hide')
            pix=pages['incumbent'].evaluate(PIXELS,{'a':base64.b64encode(left).decode(),
                                                    'b':base64.b64encode(right).decode()})
          report=b['signal'];current=report.get('statistics',{}).get('skips',0)
          assert current>=counter,'SIGNAL_SKIP_COUNTER_REGRESSION'
          skips+=current-counter;counter=current
          for event in report.get('history',[]):
            for detail in event.get('actualSkipConsumers',[]):
              marker=(generation,event['revision'],detail['id'])
              if marker not in seen:
                seen.add(marker)
                cls=detail['id'].split('/',1)[0]
                by_class[cls]=by_class.get(cls,0)+1
          failures=[]
          if n:failures.append('STATE_DOM_RECEIPT_OR_TRANSPORT_DIFFERENCE')
          if pix and pix['count']>512 and pix['maximum']>24:failures.append('MATERIAL_PIXEL_DIFFERENCE')
          print('CALCULATOR_SIGNAL_PROGRESS stage='+name,flush=True)
          stages.append({'stage':name,'status':'PASS' if not failures else 'FAIL',
            'failures':failures,'differences':n,'differenceSamples':delta[:12],
            'pixelDifferences':pix['count'] if pix else None,
            'pixelMaxChannelDelta':pix['maximum'] if pix else None,
            'actualSkipTotal':current,'mode':report.get('mode'),
            'fallbacks':report.get('fallbacks'),
            'receipts':len(b['receipts']),'requests':len(b['transport'])})
        print('CALCULATOR_SIGNAL_PROGRESS initial-mount',flush=True)
        record('initial-mount')
        for expression,answer in [('2+3','5'),('7*(3+2)','35'),('3 m + 40 cm','3.4 m')]:
          for page in pages.values():
            form=_form(page,'evaluate');form.locator('input[name="expression"]').fill(expression)
            form.locator('button[type="submit"]').click()
            _wait_state(page,'state:arithmetic-result',answer)
          record('arithmetic:'+expression)
        for page in pages.values():
          pad=page.locator('[data-mcel-expression-pad]')
          pad.locator('button[data-pad-action="clear"]').click()
          for token in ('2','+','3'):pad.locator('button[data-pad-action="'+token+'"]').click()
          pad.locator('button[data-pad-action="submit"]').click()
          _wait_state(page,'state:arithmetic-result','5')
        record('keypad-clear-edit-submit')
        for page in pages.values():
          before=page.evaluate('window.__mcelExpanded.app.readState("state:arithmetic-result")')
          form=_form(page,'evaluate')
          form.locator('input[name="expression"]').fill('3 m + 2 s')
          form.locator('button[type="submit"]').click()
          page.wait_for_function("()=>window.__mcelExpanded.app.receipts().at(-1)?.disposition==='failed'")
          assert page.evaluate('window.__mcelExpanded.app.readState("state:arithmetic-result")')==before
        record('invalid-units-preserves-last-good')
        for page in pages.values():
          _mode(page,'graphing')
          form=_form(page,'draw-graph')
          for field,value in [('expression','x^2'),('xMin','-2'),('xMax','2'),('yMin','-2'),('yMax','5')]:
            form.locator(f'input[name="{field}"]').fill(value)
          form.locator('button[type="submit"]').click()
          page.wait_for_function('()=>window.__mcelExpanded.app.readState("state:graph-points")?.length>100')
          assert page.locator('[data-mcel-plot] svg path').count()>0
        record('graph-draw-with-range')
        for page in pages.values():
          editor=page.locator('[data-mcel-expression-editor]')
          editor.locator('button[data-editor-token="sin()"]') .click()
          page.wait_for_function('()=>window.__mcelExpanded.app.readState("state:graph-edited")?.expression?.includes("sin()")')
        record('graph-expression-template')
        for page in pages.values():
          form=_form(page,'draw-graph')
          form.locator('button[type="reset"]').click()
          page.wait_for_function('()=>window.__mcelExpanded.app.readState("state:graph-x-min")===-10')
          page.wait_for_function('()=>window.__mcelExpanded.app.readState("state:graph-points")?.length>100')
        record('graph-reset-and-redraw')
        if profile=='full':
          print('CALCULATOR_SIGNAL_PROGRESS full-start',flush=True)
          for page in pages.values():
            _mode(page,'symbolic')
            form=_form(page,'evaluate-symbolic')
            form.locator('input[name="expression"]').fill('2+2')
            form.locator('button[type="submit"]').click()
            _wait_state(page,'state:symbolic-result','4')
          record('symbolic-evaluation-and-preparation')
          for page in pages.values():
            page.evaluate('window.__calcSignalFailNext="/mathics/evaluate"')
            form=_form(page,'evaluate-symbolic')
            form.locator('input[name="expression"]').fill('1+bad')
            form.locator('button[type="submit"]').click()
            page.wait_for_function("()=>window.__mcelExpanded.app.receipts().at(-1)?.disposition==='failed'")
          record('symbolic-transport-failure')
          for page in pages.values():
            form=_form(page,'evaluate-symbolic')
            form.locator('input[name="expression"]').fill('3+3')
            form.locator('button[type="submit"]').click()
            _wait_state(page,'state:symbolic-result','6')
          record('symbolic-recovery')
          # A delayed obsolete symbolic reply must not overwrite newer input.
          # Test this independently under both schedulers: equal wrong output
          # is not a successful differential comparison.
          stale_results={}
          for role,page in pages.items():
            page.evaluate('window.__calcSignalHoldNext="/mathics/evaluate"')
            form=_form(page,'evaluate-symbolic')
            form.locator('input[name="expression"]').fill('2+2')
            form.locator('button[type="submit"]').click()
            page.wait_for_function('()=>window.__calcSignalHolds.length===1')
            form.locator('input[name="expression"]').fill('3+3')
            form.locator('button[type="submit"]').click()
            _wait_state(page,'state:symbolic-result','6')
            page.evaluate('window.__calcSignalRelease()')
            page.wait_for_timeout(140)
            stale_results[role]=page.evaluate('window.__mcelExpanded.app.readState("state:symbolic-result")')
          record('stale-symbolic-completion-after-new-result')
          if any(v!='6' for v in stale_results.values()):
            stages[-1]['status']='FAIL'
            stages[-1]['failures'].append('STALE_SYMBOLIC_RESULT:'+repr(stale_results))
          for page in pages.values():
            form=_form(page,'evaluate-symbolic')
            form.locator('[data-mcel-clear-intent]').click()
            _wait_state(page,'state:symbolic-result','Mathics ready.')
          record('symbolic-clear')
          for page in pages.values():
            _mode(page,'help')
            page.wait_for_function('()=>!!window.MainComputerChatConsole?.getActiveEmbeddedContext?.()')
          record('embedded-help-context')
          for page in pages.values():_mode(page,'basic')
          record('restore-arithmetic-mode')
          # Rollback a live in-memory Calculator, without remounting or losing state.
          r=pages['signals'].evaluate('window.__mcelExpanded.app.setSignalSchedulingMode("incumbent")')
          assert r['status']=='ROLLED_BACK',r
          generation+=1;counter=0
          record('rollback-to-incumbent')
          for page in pages.values():
            form=_form(page,'evaluate')
            form.locator('input[name="expression"]').fill('9+9')
            form.locator('button[type="submit"]').click()
            _wait_state(page,'state:arithmetic-result','18')
          record('incumbent-exec-after-rollback')
          r=pages['signals'].evaluate('window.__mcelExpanded.app.setSignalSchedulingMode("certified")')
          assert r['status']=='ENABLED',r
          generation+=1;counter=0
          record('reenable-full-reconciliation')
          for page in pages.values():
            form=_form(page,'evaluate')
            form.locator('input[name="expression"]').fill('12/3')
            form.locator('button[type="submit"]').click()
            _wait_state(page,'state:arithmetic-result','4')
          record('post-reenable-evaluate')
          for role,page in pages.items():
            app=page.evaluate('''()=>{const a=window.__mcelExpanded.app;
              const before=a.signalCandidateReport();a.unmount();
              const after=a.signalCandidateReport();
              return {before:before.consumerCount||0,after:after.consumerCount||0};}''')
            if role=='signals' and app['after']!=0:raise RuntimeError('SIGNAL_CONSUMER_DISPOSAL_LEAK')
            page.close()
          generation+=1;counter=0
          pages={role:mount(role) for role in ('incumbent','signals')}
          record('unmount-remount-bootstrap')
          for page in pages.values():
            form=_form(page,'evaluate')
            form.locator('input[name="expression"]').fill('10-4')
            form.locator('button[type="submit"]').click()
            _wait_state(page,'state:arithmetic-result','6')
          record('remount-followup-evaluation')
        signal=pages['signals'].evaluate('window.__mcelExpanded.app.signalCandidateReport()')
        reference=pages['incumbent'].evaluate('window.__mcelExpanded.app.signalCandidateReport()')
        registered={kind:sum(x['id'].startswith(kind+'/') for x in signal.get('consumers',[])) for kind in KINDS}
        failures=[s['stage']+':'+','.join(s['failures']) for s in stages if s['status']=='FAIL']
        if initial['incumbent']['integrationEnabled'] or initial['incumbent']['mode']!='disabled':
          failures.append('INCUMBENT_REFERENCE_NOT_DISABLED')
        if unauthorized.get('status')!='REFUSED':failures.append('INCUMBENT_UNAUTHORIZED_ENABLE')
        if initial['signals']['mode']!='integrated-opt-in' or not initial['signals']['integrationEnabled']:
          failures.append('SIGNALS_NOT_ACTIVATED')
        if reference['integrationEnabled'] or reference['mode']!='disabled':failures.append('REFERENCE_SWITCHED')
        if signal['mode']!='integrated-opt-in' or not signal['integrationEnabled']:
          failures.append('SIGNALS_NOT_ACTIVE_AT_FINISH')
        if signal['fallbacks'] or signal.get('statistics',{}).get('unsafeFallbacks'):
          failures.append('SIGNAL_UNSAFE_FALLBACK')
        if skips<1:failures.append('NO_ACTUAL_SKIP_WITNESSES')
        authorized=set(certified_kinds or KINDS)
        if any(k not in authorized for k in by_class):failures.append('UNCERTIFIED_CLASS_SKIPPED')
        if sum(by_class.values())!=skips:failures.append('SKIP_ACCOUNTING_MISMATCH')
        if any(errors.values()):failures.append('BROWSER_PAGEERROR')
        return {'status':'PASS_DIFFERENTIAL' if not failures else 'FAIL_DIFFERENTIAL',
          'failures':failures,'stages':stages,'browser':browser_source,'actualSkips':skips,
          'skipKinds':by_class,'registeredConsumersByClass':registered,'errors':errors,
          'mountModes':{k:{key:r.get(key) for key in ('mode','integrationEnabled','integratedSupported')}
                        for k,r in initial.items()},
          'endModes':{k:{key:r.get(key) for key in ('mode','integrationEnabled','fallbacks')}
                      for k,r in [('incumbent',reference),('signals',signal)]},
          'limitations':['Deterministic in-memory optional services, not real Mathics or AI providers',
                         'Canonical hosted route, Windows authenticated transport and human design not verified',
                         'Calculator production scheduler default remains incumbent']}
      finally:
        for page in pages.values():
          try:page.close()
          except Exception:pass
        browser.close()


def accept(repo:Path=ROOT,*,profile='full',legacy_baseline=True)->dict:
    failures=[]
    parity=legacy.assess(repo,browser=True) if legacy_baseline else None
    if not legacy_baseline:failures.append('INDEPENDENT_FUNCTIONAL_PARITY_NOT_RUN')
    elif parity['status']!='PASS_ISOLATED':failures.append('LEGACY_CALCULATOR_PARITY_INCOMPLETE')
    try:
        differential=signal_differential(repo,profile=profile)
    except Exception as e:
        differential={'status':'FAIL_DIFFERENTIAL','actualSkips':0,'stages':[],
                      'failures':[f'{type(e).__name__}: {str(e)[:1000]}']}
    if differential['status']!='PASS_DIFFERENTIAL':failures.append('SIGNAL_DIFFERENTIAL_INCOMPLETE')
    classes=[]
    if profile=='full' and differential['status']=='PASS_DIFFERENTIAL':
        registered=differential.get('registeredConsumersByClass',{})
        if not any(registered.values()):failures.append('NO_ELIGIBLE_CONSUMERS')
        for kind in KINDS:
            if not registered.get(kind):
                classes.append({'class':kind,'status':'NOT_APPLICABLE',
                                'reason':'No mounted Calculator consumer of this class'})
                continue
            try:
                witness=signal_differential(repo,profile='smoke',certified_kinds=(kind,))
                good=(witness['status']=='PASS_DIFFERENTIAL' and witness['actualSkips']>0 and
                      set(witness['skipKinds'])=={kind})
                classes.append({'class':kind,'status':'PASS_CERTIFIED_CLASS' if good else 'BLOCKED',
                                'actualSkips':witness.get('actualSkips',0),
                                'failures':witness.get('failures',[]),'skipKinds':witness.get('skipKinds',{})})
            except Exception as e:
                classes.append({'class':kind,'status':'BLOCKED','failures':[f'{type(e).__name__}: {e}']})
        if any(x['status']=='BLOCKED' for x in classes):failures.append('CLASS_CERTIFICATION_INCOMPLETE')
    r=repo/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js'
    app=repo/'tools/fixtures/mcel_calculator_expanded/application.js'
    return {'schema':'mcel.calculator-full-functional.v1',
      'generatedAt':datetime.now(timezone.utc).isoformat(),'profile':profile,
      'status':'PASS_ISOLATED_FUNCTIONAL_WITH_SIGNALS' if not failures else 'BLOCKED',
      'failures':failures,'legacyParity':parity,'signalDifferential':differential,
      'consumerClasses':classes,'fingerprints':{'runtimeSha256':hashlib.sha256(r.read_bytes()).hexdigest(),
        'applicationSha256':hashlib.sha256(app.read_bytes()).hexdigest()},
      'boundaries':{'canonicalRoute':'/applications/calculator','productionSignalDefault':'DISABLED',
        'productionPromotionAuthorized':False,'liveWindowsHost':'NOT_TESTED',
        'liveMathicsAndModelProviders':'NOT_TESTED','referenceScheduler':'INCUMBENT',
        'backendTransport':'IN_MEMORY_DETERMINISTIC',
        'legacyCalculatorParityStatus':parity['status'] if parity else 'NOT_RUN'}}


def main(argv=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=ROOT)
    parser.add_argument('--mode',choices=('isolated',),default='isolated')
    parser.add_argument('--profile',choices=('smoke','full'),default='full')
    parser.add_argument('--signals',choices=('integrated',),default='integrated')
    parser.add_argument('--report',type=Path,default=DEFAULT)
    parser.add_argument('--skip-legacy',action='store_true',help='Diagnostic only; cannot certify')
    args=parser.parse_args(argv)
    repo=args.repo.resolve()
    result=accept(repo,profile=args.profile,legacy_baseline=not args.skip_legacy)
    report=args.report if args.report.is_absolute() else repo/args.report
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps(result,indent=2,sort_keys=True,ensure_ascii=False)+'\n',encoding='utf8')
    for stage in result['signalDifferential'].get('stages',[]):
        print('CALCULATOR_SIGNAL',stage['stage'],stage['status'],
              'differences='+str(stage['differences']),
              'skips='+str(stage['actualSkipTotal']),flush=True)
    d=result['signalDifferential']
    print('CALCULATOR_SIGNAL_OVERALL',d['status'],'skips='+str(d['actualSkips']),flush=True)
    for cls in result['consumerClasses']:
        print('CALCULATOR_SIGNAL_CLASS',cls['class'],cls['status'],
              'skips='+str(cls.get('actualSkips',0)),flush=True)
    print('CALCULATOR_FUNCTIONAL_PARITY',result['boundaries']['legacyCalculatorParityStatus'],flush=True)
    if result['legacyParity']:
        summary=result['legacyParity'].get('summary',{})
        print('CALCULATOR_FUNCTIONAL_GAPS',
              'missing='+','.join(summary.get('missingFeatures',[])),
              'unverified='+','.join(summary.get('unverifiedFeatures',[])),flush=True)
    print('CALCULATOR_FUNCTIONAL_OVERALL',result['status'],'report:',str(report),flush=True)
    return 0 if result['status']=='PASS_ISOLATED_FUNCTIONAL_WITH_SIGNALS' else 1

if __name__=='__main__':raise SystemExit(main())
