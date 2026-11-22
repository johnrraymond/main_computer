"""Scramjet v2 Docker smoke, including the REAL MCEL Browser frontend.

    python -m tools.mcel_browser_scramjet_smoke run --rebuild
    python -m tools.mcel_browser_scramjet_smoke run --rebuild --public --promote
    python -m tools.mcel_browser_scramjet_v2_smoke rollback

Smoke alone does not change the production browser. Only a passing run with
--promote enables v2, with an immediate reversible rollback path.
"""
from __future__ import annotations
import argparse
from contextlib import suppress
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.error import URLError
from tools import mcel_browser_scramjet_v2 as v2
from tools import mcel_browser_scramjet_smoke as legacy

STATE = Path('runtime/reports/mcel-browser-scramjet-v2/container-state.json')
REPORT = Path('runtime/reports/mcel-browser-scramjet-v2/report.json')
URL = 'http://127.0.0.9:48081/'

def _state(repo: Path):
    try:return json.loads((repo/STATE).read_text(encoding='utf8'))
    except (OSError,ValueError):return None

def _owned(state):
    import re
    if not isinstance(state,dict) or not re.fullmatch(r'mcel-browser-scramjet-v2-smoke-[a-f0-9]{8}',state.get('container','')):
        return False
    p=legacy.docker_cmd('inspect','--format','{{index .Config.Labels "'+v2.SMOKE_LABEL+'"}}|{{.Config.Image}}|{{.State.Running}}',state['container'],check_ok=False)
    return p.returncode==0 and p.stdout.strip()==f'true|{state.get("image")}|true'

def stop(repo: Path):
    state=_state(repo)
    if _owned(state):
        print('SCRAMJET_V2 stopping '+state['container'],flush=True)
        legacy.docker_cmd('rm','-f',state['container'],check_ok=False)
    (repo/STATE).unlink(missing_ok=True)

def status(repo:Path):
    state=_state(repo)
    if not _owned(state) or state.get('image')!=v2.image(repo,'smoke'):
        return None
    ok,_=v2.preflight(state['url'])
    return state if ok else None

def start(repo:Path,rebuild=False):
    old=_state(repo)
    current=status(repo)
    if current and not rebuild:return current
    if old:stop(repo)
    tag=v2.build(repo,'smoke',rebuild=rebuild)
    state=v2.run_container(repo,'smoke',tag,ip='127.0.0.2',smoke=True)
    path=repo/STATE;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(state,indent=2)+'\n',encoding='utf8')
    print('SCRAMJET_V2 READY '+state['url']+' image='+tag,flush=True)
    return state

def acceptance(repo:Path,sidecar:dict,headful=False,public=False,transport='libcurl'):
    from playwright.sync_api import sync_playwright,expect
    report={'timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'engine':'scramjet-v2','version':v2.V2_CORE,'controller':v2.V2_CONTROLLER,
            'image':sidecar['image'],'url':sidecar['url'],'transport':transport,
            'checks':{},'events':[],'result':'BLOCKED'}
    def check(name,passed,detail=''):
        report['checks'][name]={'passed':bool(passed),'detail':str(detail)[:350]}
        print('SCRAMJET_V2 '+name+' '+('PASS' if passed else 'FAIL')+((' '+str(detail)[:150]) if detail else ''),flush=True)
    host=None
    try:
        valid,reason=v2.preflight(sidecar['url'])
        check('runtime-bundles-v2-only',valid,reason)
        check('source-matched-image',sidecar['image']==v2.image(repo,'smoke'))
        # The earlier v2 server permitted ONLY the loopback fixture through
        # Wisp; that made --public deterministically fail with TLS EOF even
        # though both TLS implementations and Docker itself were healthy.
        # Verify the policy of the RUNNING image, not merely local source.
        if public:
            from urllib.request import urlopen
            with urlopen(sidecar['url'].rstrip('/')+'/healthz',timeout=5) as response:
                advertised=json.load(response)
            allowed=(advertised.get('smokeHostPolicy')=='fixture-plus-public-acceptance'
                     and set(advertised.get('smokePublicTargets',[]))=={'example.org','www.youtube.com'})
            check('v2-public-wisp-host-policy',allowed,advertised.get('smokeHostPolicy','missing'))
            if not allowed:
                raise RuntimeError('Public acceptance requested but Docker Wisp is fixture-only; rebuild image')
        # The isolated port must not accidentally bind to the privileged IP.
        try:
            legacy.fetch_status(f'http://127.0.0.1:{sidecar["port"]}/',timeout=.6)
            check('isolated-ip-only',False,'Accessible on trusted 127.0.0.1')
        except (URLError,OSError,TimeoutError):check('isolated-ip-only',True)
        mock=legacy.MockTrustedMCEL
        mock.sidecar_url=sidecar['url'];mock.secret=secrets.token_hex(24)
        mock.commands=[];mock.requested_secret=0
        assets=repo/'main_computer/web/applications'
        mock.source_html=(assets/'apps/browser-page.html').read_text(encoding='utf8')
        mock.source_css=(assets/'styles/browser-page.css').read_text(encoding='utf8')
        js=(assets/'scripts/browser-page.js').read_text(encoding='utf8')
        guard="base.hostname !== '127.0.0.3'"
        if js.count(guard)!=1:raise RuntimeError('MCEL Browser isolated-origin guard changed')
        mock.source_js=js.replace(guard,"base.hostname !== '127.0.0.2'")
        # Test-only Epoxy comparison. Production runs on proven libcurl;
        # do not modify the installed MCEL Browser page just to compare engines.
        if transport == 'epoxy':
            marker = "frameUrl.searchParams.set('parentOrigin', location.origin);"
            if mock.source_js.count(marker) < 1:
                raise RuntimeError('Browser frame URL setup changed; update transport comparison explicitly')
            mock.source_js = mock.source_js.replace(
                marker, marker + "frameUrl.searchParams.set('transport', 'epoxy');")
        host=ThreadingHTTPServer(('127.0.0.1',0),mock)
        threading.Thread(target=host.serve_forever,daemon=True).start()
        with sync_playwright() as playwright:
            # The existing MCEL smoke prefers real Edge and falls back to
            # Playwright Chromium if Edge is unavailable on the test host.
            try:
                browser=playwright.chromium.launch(channel='msedge',headless=not headful)
            except Exception:
                browser=playwright.chromium.launch(headless=not headful)
            try:
                context=browser.new_context(ignore_https_errors=False)
                page=context.new_page()
                page.on('console',lambda m:report['events'].append('console '+m.type+': '+m.text[:300]))
                page.on('pageerror',lambda e:report['events'].append('pageerror '+str(e)[:300]))
                page.on('requestfailed',lambda r:report['events'].append('requestfailed '+r.url[:230]+': '+str(r.failure)))
                # Capture the network-service rejection reason for OOPIFs as
                # well as the top-level MCEL shell. Playwright's generic
                # net::ERR_BLOCKED_BY_RESPONSE omits blockedReason.
                trace = report['network_policy_trace'] = []
                cdp_sessions=[]
                def attach_cdp(target, label):
                    try:
                        session=context.new_cdp_session(target)
                        sessions_requests={}
                        def request_seen(event):
                            info=event.get('request',{})
                            sessions_requests[event['requestId']]=info.get('url','')
                        def relevant(event):
                            url=event.get('response',{}).get('url') or sessions_requests.get(event.get('requestId',''),'')
                            return '/~/sj/' in url or '/mcel-surface.' in url or '/sw.js' in url
                        def response_seen(event):
                            if relevant(event):
                                response=event.get('response',{})
                                trace.append({'target':label,'phase':'response','url':response.get('url','')[:500],
                                    'status':response.get('status'),'mimeType':response.get('mimeType'),
                                    'fromServiceWorker':response.get('fromServiceWorker'),
                                    'headers':{k:v for k,v in response.get('headers',{}).items()
                                               if k.lower() in ('content-type','cross-origin-embedder-policy',
                                                  'cross-origin-resource-policy','cross-origin-opener-policy',
                                                  'x-frame-options','content-security-policy')},
                                    'requestId':event.get('requestId','')})
                        def failure_seen(event):
                            if relevant(event):
                                trace.append({'target':label,'phase':'failure',
                                    'url':sessions_requests.get(event.get('requestId',''),'')[:500],
                                    'reason':event.get('blockedReason'),
                                    'cors':event.get('corsErrorStatus'),
                                    'error':event.get('errorText'),
                                    'requestId':event.get('requestId','')})
                        def audit_seen(event):
                            issue=event.get('issue',{})
                            if issue.get('code') in ('CorpNotSameOrigin','CorpNotSameSite',
                                'CoepFrameResourceNeedsCoepHeader','HeavyAdIssue','GenericIssue'):
                                trace.append({'target':label,'phase':'audit',
                                              'issue':str(issue)[:1300]})
                        session.on('Network.requestWillBeSent',request_seen)
                        session.on('Network.responseReceived',response_seen)
                        session.on('Network.loadingFailed',failure_seen)
                        session.on('Audits.issueAdded',audit_seen)
                        session.send('Network.enable')
                        session.send('Audits.enable')
                        cdp_sessions.append(session)
                        return True
                    except Exception as e:
                        trace.append({'target':label,'phase':'attach-error','error':str(e)[:350]})
                        return False
                attach_cdp(page,'mcel-top')
                page.goto(f'http://127.0.0.1:{host.server_port}/applications/browser/{URL}',wait_until='domcontentloaded')
                surface=page.frame_locator('#browser-frame')
                remote=surface.frame_locator('#mcel-remote-frame')
                # The untrusted surface may be an out-of-process iframe. Attach
                # directly to its CDP target before the proxied frame loads.
                for _ in range(35):
                    targets=[f for f in page.frames if f.url.startswith(sidecar['url'].rstrip('/')+'/mcel-surface.html')]
                    if targets:
                        attach_cdp(targets[0],'scramjet-isolated-frame')
                        break
                    page.wait_for_timeout(100)
                try:
                    expect(remote.locator('#fixture-ready')).to_have_text('ready',timeout=65000)
                except Exception as e:
                    details={}
                    with suppress(Exception):
                        details['mcel_status']=page.locator('#browser-status').inner_text(timeout=2500)
                    with suppress(Exception):
                        details['surface_error']=surface.locator('#mcel-surface-error').inner_text(timeout=2500)
                    with suppress(Exception):
                        details['worker']=surface.locator('body').evaluate('''async () => {
                            const r=await navigator.serviceWorker.getRegistration('/');
                            const brief=w=>w?{state:w.state,scriptURL:w.scriptURL}:null;
                            return {registrationScope:r?.scope||null,active:brief(r?.active),
                                installing:brief(r?.installing),waiting:brief(r?.waiting),
                                controller:brief(navigator.serviceWorker.controller)};
                        }''')
                    # Force a second navigation while CDP is attached, so
                    # the precise network policy rejection is observable even
                    # if the initial frame request raced the CDP attachment.
                    with suppress(Exception):
                        page.locator('#browser-address').fill(URL,timeout=3000)
                        page.locator('#browser-go').click(timeout=3000)
                        page.wait_for_timeout(2500)
                    details['blocking_events']=[e for e in trace if e.get('phase') in ('failure','audit')][-12:]
                    details['proxied_responses']=[e for e in trace if e.get('phase') == 'response' and '/~/sj/' in e.get('url','')][-8:]
                    report['worker_failure_diagnostic']=details
                    print('SCRAMJET_V2 WORKER_DIAG '+json.dumps(details,sort_keys=True),flush=True)
                    print('SCRAMJET_V2 NETWORK_POLICY '+json.dumps(details['blocking_events'],sort_keys=True),flush=True)
                    print('SCRAMJET_V2 PROXIED_RESPONSES '+json.dumps(details['proxied_responses'],sort_keys=True),flush=True)
                    raise RuntimeError('v2 remote frame did not load; '+json.dumps(details,sort_keys=True)[:1100]) from e
                chosen=surface.locator('html').get_attribute('data-mcel-transport')
                check('v2-transport-selection', chosen==transport, f'expected={transport} actual={chosen}')
                check('mcel-v2-end-to-end-ast-fetch',True)
                worker_state=surface.locator('body').evaluate('''async () => {
                    const r=await navigator.serviceWorker.getRegistration('/');
                    return {scope:r?.scope||null,state:r?.active?.state||null,
                      scriptURL:r?.active?.scriptURL||null};
                }''')
                check('v2-service-worker-activated',worker_state.get('state')=='activated'
                      and worker_state.get('scriptURL')==sidecar['url'].rstrip('/')+'/sw.js',worker_state)
                check('v2-session-api',mock.commands.count('surface-start')==1,mock.commands)
                check('v2-handshake',page.locator('#browser-status').get_attribute('data-error')!='true')
                expect(remote.locator('#data')).to_have_text('rewritten fetch succeeded')
                check('v2-remote-js-fetch',True)
                color=remote.locator('#color').evaluate('e=>getComputedStyle(e).color')
                check('v2-css',color=='rgb(17, 101, 207)',color)
                remote.locator('#click').click()
                expect(remote.locator('#clicked')).to_have_text('clicked natively')
                check('v2-pointer',True)
                remote.locator('#typing').fill('v2 native keyboard')
                check('v2-keyboard',remote.locator('#typing').input_value()=='v2 native keyboard')
                check('v2-native-selection',remote.locator('#select').evaluate('e=>{let r=document.createRange();r.selectNodeContents(e);let s=getSelection();s.removeAllRanges();s.addRange(r);return s.toString().includes("native selectable")}'))
                # Redirects cross a real HTTP 301 from port 48081 to 48083.
                page.locator('#browser-address').fill('http://127.0.0.9:48081/redirect-301')
                page.locator('#browser-go').click()
                expect(remote.locator('#redirected-title')).to_have_text('HTTP 301 target loaded',timeout=25000)
                check('v2-301-redirect',True)
                final='http://127.0.0.9:48083/redirected?source=301'
                expect(page.locator('#browser-address')).to_have_value(final,timeout=15000)
                check('v2-final-url-authority',True)
                page.locator('#browser-address').fill('http://127.0.0.9:48081/redirect-relative')
                page.locator('#browser-go').click()
                expect(remote.locator('#next-title')).to_have_text('Native navigation succeeded',timeout=25000)
                expect(page.locator('#browser-address')).to_have_value('http://127.0.0.9:48081/next',timeout=15000)
                check('v2-302-relative-redirect',True)
                # Ensure proxy pages are unable to read MCEL's trusted cookie.
                check('v2-trusted-cookie-isolation',all(cookie['value'] != mock.secret for cookie in context.cookies(sidecar['url'])),
                    'proxy origin did not receive the trusted cookie')
                cross=page.locator('#browser-frame').evaluate('e=>{try{return !!e.contentWindow.document.body}catch(x){return x.name}}')
                check('v2-cross-origin-barrier',cross=='SecurityError',cross)
                check('v2-native-iframe-no-jpeg',page.locator('#browser-image').count()==0)
                # Run real HTTPS BEFORE the deliberate private-network trap, so
                # a rejected Wisp stream cannot contaminate TLS observations.
                if public:
                    page.locator('#browser-address').fill('https://example.org/')
                    page.locator('#browser-go').click()
                    expect(remote.locator('body')).to_contain_text('documentation',timeout=50000)
                    check('public-example.org',True)
                    page.locator('#browser-address').fill('https://www.youtube.com/')
                    page.locator('#browser-go').click()
                    expect(remote.locator('body')).to_contain_text('YouTube',timeout=60000)
                    check('public-youtube',True)
                    # Restore the controlled fixture before checking private
                    # network enforcement. A public site's own CSP must never
                    # be mistaken for proof that Wisp denies private targets.
                    page.locator('#browser-address').fill(URL)
                    page.locator('#browser-go').click()
                    expect(remote.locator('#fixture-ready')).to_have_text('ready',timeout=35000)
                    check('v2-fixture-after-public',True)
                denied=remote.locator('body').evaluate('''async () => {
                  try {
                    const r=await fetch('http://127.0.0.10:48082/mcel-secret');
                    return {status:r.status,body:(await r.text()).slice(0,300)};
                  } catch(e) {return {error:String(e).slice(0,150)}}
                }''')
                check('v2-private-trap-not-exposed','MCEL_TRAP_SECRET_DENY' not in str(denied),denied)
                context.close()
            finally:browser.close()
        required=('runtime-bundles-v2-only','source-matched-image','isolated-ip-only',
                  'v2-transport-selection',
                  'mcel-v2-end-to-end-ast-fetch','v2-service-worker-activated',
                  'v2-session-api','v2-handshake','v2-remote-js-fetch',
                  'v2-css','v2-pointer','v2-keyboard','v2-native-selection','v2-301-redirect',
                  'v2-final-url-authority','v2-302-relative-redirect','v2-trusted-cookie-isolation',
                  'v2-cross-origin-barrier','v2-native-iframe-no-jpeg','v2-private-trap-not-exposed')
        if public:required+=('v2-public-wisp-host-policy','public-example.org','public-youtube','v2-fixture-after-public')
        missing=[x for x in required if not report['checks'].get(x,{}).get('passed')]
        report['result']='PASS_V2_MCEL_BROWSER_SMOKE_NOT_SECURITY_CERTIFIED' if not missing else 'BLOCKED'
        print('SCRAMJET_V2 RESULT '+report['result']+(' missing='+','.join(missing) if missing else ''),flush=True)
    except Exception as e:
        report['result']='BLOCKED'
        report['error']=f'{type(e).__name__}: {str(e)[:800]}'
        print('SCRAMJET_V2 ERROR '+report['error'],flush=True)
        for msg in report['events'][-12:]:print('SCRAMJET_V2 DIAG '+msg,flush=True)
    finally:
        report_path=repo/REPORT;report_path.parent.mkdir(parents=True,exist_ok=True)
        report_path.write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
        print('SCRAMJET_V2 REPORT '+str(report_path),flush=True)
        if host:host.shutdown();host.server_close()
    return report['result'].startswith('PASS_')

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',nargs='?',choices=['run','build','start','stop','status','promote','rollback'],default='run')
    parser.add_argument('--rebuild',action='store_true')
    parser.add_argument('--no-bootstrap',action='store_true',help='Compatibility flag (v2 fetches npm packages during Docker build)')
    parser.add_argument('--keep',action='store_true')
    parser.add_argument('--headful',action='store_true')
    parser.add_argument('--public',action='store_true',help='Also check example.org and YouTube')
    parser.add_argument('--transport',choices=('libcurl','epoxy'),default='libcurl',
        help='libcurl v2 is the production default; Epoxy v3 is a diagnostic comparison only')
    parser.add_argument('--promote',dest='promote_after_pass',action='store_true',help='Enable v2 ONLY after all v2 smoke and public checks pass')
    args=parser.parse_args(argv)
    repo=Path.cwd().resolve()
    if not (repo/'main_computer/mcel_backend_browser.py').is_file():parser.error('Run from Main Computer repository root')
    if args.action=='rollback':
        v2.rollback(repo);print('SCRAMJET_V2 ROLLBACK prepared; restart MCEL to use v1');return 0
    if args.promote_after_pass and args.transport != 'libcurl':
        parser.error('Promotion requires the production libcurl transport, not Epoxy diagnostic mode')
    if args.action=='promote':
        parser.error('Use run --public --promote; promotion requires a fresh acceptance PASS')
    try:
        legacy.require_docker()
        if args.action=='stop':stop(repo);return 0
        if args.action=='status':
            print('SCRAMJET_V2 STATUS '+json.dumps(status(repo) or {'running':False}));return 0
        if args.action=='build':v2.build(repo,'smoke',rebuild=args.rebuild);return 0
        before=status(repo)
        sidecar=start(repo,rebuild=args.rebuild)
        if args.action=='start':return 0
        try:
            passed=acceptance(repo,sidecar,headful=args.headful,
                public=args.public or args.promote_after_pass,transport=args.transport)
            if passed and args.promote_after_pass:
                v2.promote(repo)
                print('SCRAMJET_V2 PROMOTED; restart Main Computer to activate v2',flush=True)
            elif args.promote_after_pass:
                print('SCRAMJET_V2 NOT PROMOTED; v1 remains the production engine',flush=True)
            return 0 if passed else 1
        finally:
            if not args.keep and before is None:stop(repo)
    except Exception as e:
        print('SCRAMJET_V2 ERROR '+type(e).__name__+': '+str(e),file=sys.stderr,flush=True)
        return 2

if __name__=='__main__':raise SystemExit(main())
