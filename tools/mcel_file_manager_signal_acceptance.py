"""File Explorer functional + signal scheduler acceptance, explicitly not a rollout.

Existing independent legacy-versus-semantic parity is mandatory evidence. The
additional differential mounts the real semantic File Manager twice, with
separate deterministic transports backed by real temporary files. Only the
second page opts into the shared runtime's integrated signal scheduler.

No application's source, canonical route, rollout policy, or production host
capability is modified by the acceptance runner. Windows-host and cross-app
file-consumption acceptance remain separate gates.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

from playwright.sync_api import sync_playwright
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools import mcel_file_manager_functional_parity_frob as legacy
from tools.mcel_phase2b_browser import launch_semantic_browser
from tools.mcel_signal_differential_acceptance import SNAPSHOT, PIXELS, _diff

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = Path('runtime/reports/mcel-file-manager-full-functional/report.json')
KINDS = ('condition', 'display', 'selected-record', 'typed-record')
APPLICABLE = ('display', 'selected-record', 'typed-record')


def _mount_html(repo: Path, enable_signals: bool, *, certified_kinds=None) -> str:
    """Only the isolated test copy opts in; canonical host stays disabled."""
    html = render_semantic_page('file-manager', repo)
    assert html.count('signalSchedulerIntegration:false') == 1, 'HOST_DEFAULT_CHANGED'
    assert html.count('signalSchedulerStartEnabled:false') == 1, 'HOST_START_DEFAULT_CHANGED'
    if enable_signals:
        html = html.replace('signalSchedulerIntegration:false', 'signalSchedulerIntegration:true', 1)
        html = html.replace('signalSchedulerStartEnabled:false', 'signalSchedulerStartEnabled:true', 1)
        if certified_kinds is not None:
            if not certified_kinds or len(set(certified_kinds))!=len(certified_kinds) or any(k not in KINDS for k in certified_kinds):
                raise ValueError('UNAUTHORIZED_CONSUMER_CLASS')
            html=html.replace('signalSchedulerStartEnabled:true',
                'signalSchedulerCertifiedKinds:'+json.dumps(list(certified_kinds))+',signalSchedulerStartEnabled:true',1)
    needle = 'const port=McelSemanticReadPorts.create({appId:ir.application.appId});'
    assert html.count(needle) == 1, 'SEMANTIC_PORT_MOUNT_CHANGED'
    html = html.replace(needle,
        'const port=(window.__fmMountedPort=McelSemanticReadPorts.create({'
        'appId:ir.application.appId,navigate:(route,meta)=>{'
        'window.__fmNavigated={route,meta};return true;}}));', 1)
    return html


def _install_transport(page, fixture: dict):
    legacy.inject_mock(page, fixture)
    # The real port still performs authorization, request formation, and
    # response normalization. This wrapper controls only the test transport.
    page.evaluate('''() => {
      const native=window.fetch;
      window.__fmTestHolds=[];
      window.__fmTestHoldNext=null;
      window.__fmTestFailNext=null;
      window.__fmTestRelease=async()=>{
        const holds=window.__fmTestHolds.splice(0);
        for(const finish of holds)finish();
        await Promise.resolve();return holds.length;
      };
      window.fetch=async (url,opts)=>{
        const key=String(url);
        const hold=window.__fmTestHoldNext;
        if(hold && key.endsWith(hold)){
          window.__fmTestHoldNext=null;
          await new Promise(resolve=>window.__fmTestHolds.push(resolve));
        }
        const fail=window.__fmTestFailNext;
        if(fail && key.endsWith(fail)){
          window.__fmTestFailNext=null;
          throw new Error('FILE_MANAGER_ISOLATED_INJECTED_TRANSPORT_FAILURE');
        }
        return native(url,opts);
      };
    }''')


def _observed(page):
    snap=page.evaluate(SNAPSHOT)
    # The generic differential covers DOM, computed presentation, geometry,
    # selection/focus, semantic state and operation receipt dispositions.
    snap['transport']=page.evaluate('''() => window.__fmCalls.map(x=>({path:x.path,body:x.body}))''')
    snap['navigation']=page.evaluate('window.__fmNavigated||null')
    return snap


def _click(page, selector: str, *, timeout=10000):
    page.locator(selector).first.click(timeout=timeout)


def signal_differential(repo: Path=ROOT, *, profile: str='full',certified_kinds=None) -> dict:
    if profile not in ('smoke', 'full'):
        raise ValueError('INVALID_PROFILE')
    errors={'incumbent':[], 'signals':[]};stages=[]
    modes={};fall={};browser_source=None
    with tempfile.TemporaryDirectory(prefix='mcel-fm-signal-') as temp:
      fixture=legacy.fixture_payloads(Path(temp))
      with sync_playwright() as pw:
        browser,browser_source=launch_semantic_browser(pw)
        pages={}
        try:
          def mount(role):
            page=browser.new_page(viewport={'width':1120,'height':900},device_scale_factor=1)
            page.set_default_timeout(10000)
            page.on('pageerror', lambda e,r=role: errors[r].append(str(e)))
            _install_transport(page,fixture)
            page.set_content(_mount_html(repo,role=='signals',certified_kinds=certified_kinds))
            page.wait_for_function('()=>!!window.__mcelWorkbench?.app')
            page.locator('[data-record-key="README.md"]').wait_for()
            page.wait_for_function('''()=>window.__mcelWorkbench.app.readState('state:selected-root')==='workspace' ''')
            return page
          for role in ('incumbent','signals'): pages[role]=mount(role)
          for role,page in pages.items():
            r=page.evaluate('window.__mcelWorkbench.app.signalCandidateReport()')
            modes[role]={'mode':r['mode'],'integrationEnabled':r['integrationEnabled'],
                         'integratedSupported':r['integratedSupported']}
          denied=pages['incumbent'].evaluate('window.__mcelWorkbench.app.setSignalSchedulingMode("certified")')
          modes['incumbent']['enableAttempt']=denied.get('status')
          skipped_before=0;generation=0;skips=0;by_class={};seen=set()
          def record(phase,*,check_pixels=True):
            nonlocal skipped_before,skips
            for page in pages.values():
              page.mouse.move(0,0)
              page.evaluate('''async()=>{await document.fonts.ready;
                await new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done)))}''')
              page.wait_for_timeout(60)
            a=_observed(pages['incumbent']);b=_observed(pages['signals'])
            count,delta=_diff(a,b)
            # _diff covers rows, focus, selection, state, receipts, transport,
            # but does not look at the test-navigation callback itself.
            if a['navigation']!=b['navigation']:
              count+=1;delta.append({'path':'navigation','incumbent':a['navigation'],'candidate':b['navigation']})
            pix=None
            if check_pixels:
              import base64
              p1=pages['incumbent'].screenshot(animations='disabled',caret='hide')
              p2=pages['signals'].screenshot(animations='disabled',caret='hide')
              pix=pages['incumbent'].evaluate(PIXELS,{'a':base64.b64encode(p1).decode(),
                                                        'b':base64.b64encode(p2).decode()})
            micro=bool(pix and 0<pix['count']<=512 and pix['maximum']<=24)
            signal=b['signal'];current=signal.get('statistics',{}).get('skips',0)
            if current<skipped_before: raise RuntimeError('SIGNAL_SKIP_COUNTER_REGRESSED')
            skips+=current-skipped_before;skipped_before=current
            for event in signal.get('history',[]):
              for detail in event.get('actualSkipConsumers',[]):
                marker=(generation,event['revision'],detail['id'])
                if marker in seen:continue
                seen.add(marker)
                kind=detail['id'].split('/',1)[0]
                by_class[kind]=by_class.get(kind,0)+1
            failures=[]
            if count:failures.append('SEMANTIC_DOM_STATE_TRANSPORT_OR_RECEIPT_DIFF')
            if pix and pix['count'] and not micro:failures.append('MATERIAL_PIXEL_DIFFERENCE')
            stages.append({'stage':phase,'status':'PASS' if not failures else 'FAIL',
                'failures':failures,'differences':count,'differenceSamples':delta[:12],
                'pixelDifferences':pix['count'] if pix else None,
                'pixelMaxChannelDelta':pix['maximum'] if pix else None,
                'actualSkipTotal':current,'mode':signal.get('mode'),
                'fallbacks':signal.get('fallbacks'),
                'requests':len(b['transport']),'receipts':len(b['receipts'])})
          record('initial-mount')
          for role,page in pages.items():
            _click(page,'[data-mcel-inspect-folder="docs"]')
            page.locator('[data-mcel-selected-details="docs"]').wait_for()
          record('inspect-directory-metadata')
          for page in pages.values():
            _click(page,'[data-record-key="docs"] button[data-select-key="docs"]')
            page.locator('[data-record-key="docs/guide.txt"]').wait_for()
          record('enter-observed-directory')
          for page in pages.values():
            _click(page,'[data-mcel-parent-directory]')
            page.locator('[data-record-key="README.md"]').wait_for()
          record('navigate-to-parent')
          for page in pages.values():
            page.locator('form[data-intent="intent:search"] input[name="query"]').fill('README')
            page.locator('form[data-intent="intent:search"] button[type="submit"]').click()
            page.wait_for_function("()=>window.__fmCalls.some(x=>x.path.endsWith('/search'))")
          record('search-current-root')
          for page in pages.values():
            _click(page,'[data-record-key="README.md"] button')
            page.get_by_role('button',name='Preview Entry').click()
            page.wait_for_function("()=>window.__mcelWorkbench.app.readState('state:preview')?.readable===true")
          record('read-selected-file')
          for page in pages.values():
            page.get_by_role('button',name='Open With').click()
            page.wait_for_function('()=>!!window.__fmNavigated?.route')
          record('suggested-app-handoff-route')
          for page in pages.values():
            _click(page,'[data-record-key="scratch"] button')
            page.locator('[data-record-key="scratch.txt"]').wait_for()
          record('switch-root-clears-stale-preview')
          for page in pages.values():
            _click(page,'[data-record-key="scratch.txt"] button')
            page.get_by_role('button',name='Preview Entry').click()
            page.wait_for_function("()=>window.__mcelWorkbench.app.readState('state:preview')?.content==='scratch'")
          record('read-second-root')
          # Adapter admission and forged path protection, not an app-only
          # visibility comparison. Exact results must match and fail closed.
          authorization={}
          for role,page in pages.items():
            authorization[role]=page.evaluate('''async()=>{
              const port=window.__fmMountedPort;
              const req=p=>({appId:'semantic-file-manager',intentId:'intent:preview-entry',
                effectId:'effect:preview-entry.request',capabilityId:'capability:filesystem-read',
                operation:'preview',runtimeMethod:'readPreview',risk:'external-read',
                arguments:{rootId:'scratch',relativePath:p}});
              return [await port.authorize(req('../secret.txt')),
                await port.authorize(req('not-listed.txt')),
                await port.authorize(req('scratch.txt'))];
            }''')
          record('authorization-refuses-forged-paths',check_pixels=False)
          if authorization['incumbent']!=[False,False,True] or authorization['signals']!=authorization['incumbent']:
            stages[-1]['status']='FAIL';stages[-1]['failures'].append('AUTHORIZATION_DIFFERENCE')
          if profile=='full':
            # Failed backend request and recovery must not change the DOM
            # or produce divergent receipts; the same failure is injected.
            for page in pages.values():
              page.evaluate('window.__fmTestFailNext="/read"')
              page.get_by_role('button',name='Preview Entry').click()
              page.wait_for_function("()=>window.__fmTestFailNext===null && window.__mcelWorkbench.app.receipts().at(-1)?.disposition!=='completed'")
            record('transport-failure')
            for page in pages.values():
              page.get_by_role('button',name='Preview Entry').click()
              page.wait_for_function("()=>window.__mcelWorkbench.app.receipts().at(-1)?.disposition==='completed'")
            record('transport-recovery')
            # Immediate rollback must reconcile the complete app without
            # a remount, preserve state, and prevent further signal skips.
            for page in pages.values():
              if page==pages['signals']:
                r=page.evaluate('window.__mcelWorkbench.app.setSignalSchedulingMode("incumbent")')
                if r['status']!='ROLLED_BACK':raise RuntimeError('ROLLBACK_REFUSED:'+str(r))
            generation+=1;skipped_before=0
            record('live-rollback-to-incumbent')
            before=pages['signals'].evaluate('window.__mcelWorkbench.app.signalCandidateReport()')
            for page in pages.values():
              _click(page,'[data-record-key="workspace"] button')
              page.locator('[data-record-key="README.md"]').wait_for()
            record('incumbent-execution-after-rollback')
            after=pages['signals'].evaluate('window.__mcelWorkbench.app.signalCandidateReport()')
            if before['integrationEnabled'] or after['integrationEnabled'] or after.get('statistics',{}).get('skips',0):
              stages[-1]['status']='FAIL';stages[-1]['failures'].append('ROLLBACK_DID_NOT_DISABLE_SIGNALS')
            # Re-enabling starts a new signal graph with a full bootstrap.
            r=pages['signals'].evaluate('window.__mcelWorkbench.app.setSignalSchedulingMode("certified")')
            if r['status']!='ENABLED':raise RuntimeError('REENABLE_REFUSED:'+str(r))
            generation+=1;skipped_before=0
            record('reenable-full-reconciliation')
            for page in pages.values():
              _click(page,'[data-record-key="docs"] button[data-select-key="docs"]')
              page.locator('[data-record-key="docs/guide.txt"]').wait_for()
            record('navigation-after-reenable')
            # Destroy the previous subscription graph and replay startup.
            for role,page in pages.items():
              counts=page.evaluate('''()=>{const a=window.__mcelWorkbench.app;
                const before=a.signalCandidateReport();a.unmount();
                const after=a.signalCandidateReport();
                return {before:before.consumerCount||0,after:after.consumerCount||0};}''')
              if role=='signals' and counts['after']!=0:raise RuntimeError('CONSUMER_DISPOSAL_LEAK')
              page.close()
            generation+=1;skipped_before=0
            pages={role:mount(role) for role in ('incumbent','signals')}
            record('unmount-remount-bootstrap')
            for page in pages.values():
              page.get_by_role('button',name='Browse',exact=True).click()
              page.locator('[data-record-key="README.md"]').wait_for()
            record('remount-followup-browse')
            # An older /list completes AFTER the root has changed. Matching
            # incumbent/signal outputs alone is insufficient: independently
            # require current-root data in both pages (never stale docs).
            for page in pages.values():
              page.evaluate('window.__fmTestHoldNext="/list"')
              _click(page,'[data-record-key="docs"] button[data-select-key="docs"]')
              page.wait_for_function('()=>window.__fmTestHolds.length===1')
              _click(page,'[data-record-key="scratch"] button')
              page.locator('[data-record-key="scratch.txt"]').wait_for()
              page.evaluate('window.__fmTestRelease()')
              page.wait_for_timeout(120)
            record('delayed-old-root-list-after-new-root')
            for role,page in pages.items():
              observed=page.evaluate('''()=>({
                root:window.__mcelWorkbench.app.readState('state:selected-root'),
                rows:[...document.querySelectorAll('[data-record-key][data-mcel-tree-depth]')]
                  .map(x=>x.dataset.recordKey),
                entries:window.__mcelWorkbench.app.readState('state:entries')
              })''')
              keys=[x.get('id') for x in (observed['entries'] or [])]
              if (observed['root']!='scratch' or
                  any(x in ('docs/guide.txt','README.md') for x in keys) or
                  any(x in ('docs/guide.txt','README.md') for x in observed['rows'])):
                stages[-1]['status']='FAIL'
                stages[-1]['failures'].append('STALE_ROOT_RESULT_'+role.upper())
          candidate=pages['signals'].evaluate('window.__mcelWorkbench.app.signalCandidateReport()')
          incumbent=pages['incumbent'].evaluate('window.__mcelWorkbench.app.signalCandidateReport()')
          registered={kind:sum(1 for c in candidate.get('consumers',[])
                       if c.get('id','').startswith(kind+'/')) for kind in KINDS}
          fall={'candidate':candidate.get('fallbacks',0),
                'lastFallback':candidate.get('lastFallback'),
                'incumbentMode':incumbent.get('mode'),
                'candidateMode':candidate.get('mode'),
                'candidateEnabled':candidate.get('integrationEnabled'),
                'skipsByClass':dict(sorted(by_class.items()))}
          failures=[s['stage']+':'+','.join(s['failures']) for s in stages if s['status']!='PASS']
          if (modes['incumbent']['mode']!='disabled' or modes['incumbent']['integrationEnabled'] or
              modes['incumbent']['enableAttempt']!='REFUSED'):
            failures.append('REFERENCE_NOT_INCUMBENT_OR_PRODUCTION_OPT_IN_LEAK')
          if modes['signals']['mode']!='integrated-opt-in' or not modes['signals']['integrationEnabled']:
            failures.append('SIGNAL_INTEGRATION_NOT_ENABLED')
          if candidate.get('mode')!='integrated-opt-in' or not candidate.get('integrationEnabled'):
            failures.append('SIGNAL_NOT_ACTIVE_AT_FINISH')
          if fall['candidate']!=0 or candidate.get('statistics',{}).get('unsafeFallbacks',0):
            failures.append('SIGNAL_UNSAFE_FALLBACK')
          if skips<1:failures.append('NO_ACTUAL_SKIPS')
          authorized=set(certified_kinds if certified_kinds is not None else KINDS)
          if any(kind not in authorized for kind in by_class):failures.append('UNAUTHORIZED_CONSUMER_CLASS_SKIPPED')
          if sum(by_class.values())!=skips:failures.append('SIGNAL_SKIP_ACCOUNTING_INCOMPLETE')
          if errors['incumbent'] or errors['signals']:failures.append('BROWSER_PAGEERROR')
          return {'status':'PASS_DIFFERENTIAL' if not failures else 'FAIL_DIFFERENTIAL',
                  'failures':failures,'stages':stages,'browser':browser_source,
                  'mountModes':modes,'fallback':fall,'actualSkips':skips,
                  'certifiedKinds':list(certified_kinds if certified_kinds is not None else KINDS),
                  'registeredConsumersByClass':registered,
                  'errors':errors,'permissions':authorization,
                  'limitations':['Mock HTTP against temporary real filesystem',
                     'Not a Windows-host or cross-app file consumption acceptance',
                     'File Manager is NOT authorized to start signals in its canonical host']}
        finally:
          for page in pages.values():
            try:page.close()
            except Exception:pass
          browser.close()


def accept(repo:Path=ROOT,*,profile:str='full',include_legacy:bool=True)->dict:
    if profile not in ('smoke','full'):raise ValueError('INVALID_PROFILE')
    legacy_result=legacy.assess(repo,browser=True) if include_legacy else None
    failures=[]
    if not include_legacy:failures.append('LEGACY_PARITY_NOT_RUN')
    elif legacy_result['status']!='PASS_ISOLATED':failures.append('LEGACY_PARITY_NOT_PASS')
    try:
        differential=signal_differential(repo,profile=profile)
    except Exception as exc:
        differential={'status':'FAIL_DIFFERENTIAL','failures':[type(exc).__name__+': '+str(exc)[:1200]],
                      'stages':[],'actualSkips':0}
    if differential['status']!='PASS_DIFFERENTIAL':failures.append('SIGNAL_DIFFERENTIAL_NOT_PASS')
    classes=[]
    if profile=='full' and differential['status']=='PASS_DIFFERENTIAL':
        registered=differential.get('registeredConsumersByClass',{})
        if not registered or not any(registered.values()):failures.append('NO_CERTIFIED_CONSUMER_CLASSES_FOUND')
        for kind in KINDS:
            if not registered.get(kind,0):
                classes.append({'class':kind,'status':'NOT_APPLICABLE',
                    'reason':'No File Manager consumer of this class in the mounted source'})
                continue
            try:
                witness=signal_differential(repo,profile='smoke',certified_kinds=(kind,))
                good=(witness['status']=='PASS_DIFFERENTIAL' and witness['actualSkips']>0 and
                      set(witness['fallback']['skipsByClass'])=={kind})
                classes.append({'class':kind,'status':'PASS_CERTIFIED_CLASS' if good else 'BLOCKED',
                    'actualSkips':witness.get('actualSkips',0),
                    'failures':witness.get('failures',[]),
                    'observedSkipsByClass':witness.get('fallback',{}).get('skipsByClass',{}),
                    'stageCount':len(witness.get('stages',[]))})
            except Exception as exc:
                classes.append({'class':kind,'status':'BLOCKED',
                    'failures':[type(exc).__name__+': '+str(exc)[:1000]]})
        if any(c['status']=='BLOCKED' for c in classes):failures.append('CLASS_CERTIFICATION_NOT_PASS')
    source=repo/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js'
    adapter=repo/'tools/fixtures/mcel_semantic_other_apps/file-manager/application.js'
    return {'schema':'mcel.file-manager-full-functional.v1',
        'generatedAt':datetime.now(timezone.utc).isoformat(),'profile':profile,
        'status':'PASS_ISOLATED_FUNCTIONAL_WITH_SIGNALS' if not failures else 'BLOCKED',
        'failures':failures,'legacyParity':legacy_result,'signalDifferential':differential,
        'consumerClasses':classes,
        'fingerprints':{'runtimeSha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                        'applicationSha256':hashlib.sha256(adapter.read_bytes()).hexdigest()},
        'boundaries':{'canonicalRoute':'/applications/file-explorer',
            'productionSignalDefault':'DISABLED',
            'hostMutations':'NEVER_DISPATCHED','crossAppDocumentHandoff':'UNVERIFIED',
            'realWindowsHost':'NOT_TESTED', 'referenceScheduler':'INCUMBENT',
            'backendTransport':'IN_MEMORY_READ_ONLY_TEMP_FILESYSTEM',
            'productionPromotionAuthorized':False}}


def main(argv=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=ROOT)
    parser.add_argument('--profile',choices=('smoke','full'),default='full')
    parser.add_argument('--mode',choices=('isolated',),default='isolated')
    parser.add_argument('--signals',choices=('integrated',),default='integrated')
    parser.add_argument('--report',type=Path,default=DEFAULT)
    parser.add_argument('--skip-legacy',action='store_true',help='diagnostic only; cannot certify legacy parity')
    args=parser.parse_args(argv)
    report=accept(args.repo.resolve(),profile=args.profile,include_legacy=not args.skip_legacy)
    dest=args.report if args.report.is_absolute() else args.repo.resolve()/args.report
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(report,indent=2,sort_keys=True,ensure_ascii=False)+'\n',encoding='utf8')
    diff=report['signalDifferential']
    for stage in diff.get('stages',[]):
        print('FILE_MANAGER_SIGNAL',stage['stage'],stage['status'],
              'differences='+str(stage['differences']),
              'skips='+str(stage['actualSkipTotal']),flush=True)
    print('FILE_MANAGER_SIGNAL_OVERALL',diff['status'],'skips='+str(diff.get('actualSkips',0)),flush=True)
    for cls in report['consumerClasses']:
        print('FILE_MANAGER_SIGNAL_CLASS',cls['class'],cls['status'],
            'skips='+str(cls.get('actualSkips',0)),flush=True)
    print('FILE_MANAGER_FUNCTIONAL_OVERALL',report['status'],'report:',dest,flush=True)
    if report['failures']:print('BLOCKERS',report['failures'],diff.get('failures',[]),flush=True)
    return 0 if report['status']=='PASS_ISOLATED_FUNCTIONAL_WITH_SIGNALS' else 1

if __name__=='__main__':raise SystemExit(main())
