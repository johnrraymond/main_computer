"""Inspect actual running MCEL Browser Docker sidecar: first-party vs iframe SW.

PowerShell (Main Computer checkout root):
    python -m tools.mcel_browser_service_worker_differential_0008

Read-only with respect to Main Computer and Docker. Creates temporary Edge
contexts, uses *only* the already existing /mcel-sw-probe.js in the sidecar,
and writes a local diagnostic report under runtime/reports/.
"""
from __future__ import annotations

import argparse
from contextlib import suppress
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler

WORKER_PATH = '/mcel-sw-probe.js'
SCRIPT_PATH = '/mcel-surface.js'
SURFACE_PATH = '/mcel-surface.html'
_TIMEOUT_MS = 9500


def sidecar_from_state(root: Path) -> str:
    state_path = root / 'runtime' / 'reports' / 'mcel-browser-scramjet-app' / 'container-state.json'
    if not state_path.is_file():
        raise RuntimeError('Production Scramjet container state not found; open MCEL Browser first. Expected: ' + str(state_path))
    data = json.loads(state_path.read_text(encoding='utf-8'))
    url = str(data.get('url', '')).rstrip('/')
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.3' or not parsed.port
            or parsed.path not in ('', '/') or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise RuntimeError('Refusing to probe unexpected Docker origin: ' + url[:120])
    return url


def resources(sidecar: str) -> dict:
    opener = build_opener(ProxyHandler({}))
    results = {}
    for path in (SURFACE_PATH, SCRIPT_PATH, WORKER_PATH, '/sw.js'):
        try:
            with opener.open(sidecar + path, timeout=7) as response:
                data = response.read(8192)
                results[path] = {'http':response.status, 'type':response.headers.get('Content-Type',''),
                                'bytes_sampled':len(data),
                                'headers':{k:response.headers.get(k,'') for k in (
                                    'Cross-Origin-Opener-Policy','Cross-Origin-Embedder-Policy',
                                    'Cross-Origin-Resource-Policy','Content-Security-Policy',
                                    'Service-Worker-Allowed','X-Content-Type-Options')},
                                'has_worker_code': b'addEventListener' in data if path == WORKER_PATH else None}
        except Exception as exc:
            results[path] = {'error':f'{type(exc).__name__}: {exc}'}
    return results


class HostPage(BaseHTTPRequestHandler):
    target = ''
    level = 1

    def log_message(self, *_):
        pass

    def do_GET(self):
        target = escape(self.target + SURFACE_PATH, quote=True)
        if self.path == '/shell':
            body = f'<html><body><iframe id="child" sandbox="allow-scripts allow-same-origin allow-forms" src="{target}"></iframe></body></html>'
        elif self.path == '/':
            nested = 'http://127.0.0.1:' + str(self.server.server_port) + '/shell'
            target_url = nested if self.level == 2 else target
            body = f'<html><body><iframe id="child" sandbox="allow-scripts allow-same-origin allow-forms" src="{escape(target_url, quote=True)}"></iframe></body></html>'
        else:
            self.send_error(404)
            return
        payload = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        self.wfile.write(payload)


EVALUATE_REGISTRATION = r'''async ({script,timeoutMs}) => {
  const result = {origin: location.origin, page: location.href, embedded: window.top !== window,
    secure: isSecureContext, hasApi: !!navigator.serviceWorker,
    time: new Date().toISOString(), registration: 'not-started'};
  if (!navigator.serviceWorker) return {...result, registration:'missing-api'};
  const deadline = (promise, label) => Promise.race([promise, new Promise((_,reject) =>
    setTimeout(()=>reject(new Error(label + ' TIMEOUT')),timeoutMs))]);
  try {result.storage = typeof document.hasStorageAccess === 'function' ?
       await deadline(document.hasStorageAccess(), 'hasStorageAccess') : 'unsupported'}
  catch (e) {result.storage = String(e)}
  try {
    const response = await deadline(fetch(script, {cache:'no-store',credentials:'same-origin'}), 'fetch-worker');
    result.worker = {status:response.status, type:response.headers.get('content-type')};
    if (!response.ok) return {...result, registration:'worker-http-failure'};
  } catch(e) {result.worker={error:String(e)};return {...result, registration:'worker-fetch-failure'}}
  try {
    // Crucial: the tested frame runs under its actual third-party/top-level
    // storage key, with the runtime controller script disabled. No frame
    // navigations, origin-bridging, or production state changes are made.
    const registration = await deadline(navigator.serviceWorker.register(script,{
      scope:'/mcel-worker-probe/',updateViaCache:'none'}), 'register-worker');
    result.registration='registered';
    result.scope=registration.scope;
    result.active=registration.active?.scriptURL || null;
    try {result.unregister=await deadline(registration.unregister(), 'unregister-worker')}
    catch(e) {result.unregisterError=String(e)}
  } catch(e) {result.registration='failed';result.registerError=String(e)}
  return result;
}'''


def diagnose(root: Path, sidecar: str, channel: str, show_browser: bool) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError('Python Playwright is required (`python -m pip install playwright`)') from exc
    result = {'sidecar':sidecar,'timestamp_utc':datetime.now(timezone.utc).isoformat(),
              'resource_preflight':resources(sidecar),'tests':{}}
    print('SW_DIFF resource_preflight ' + json.dumps(result['resource_preflight'])[:1800], flush=True)
    with sync_playwright() as pw:
        browser=None
        launch_errors=[]
        options=['msedge',None] if channel == 'auto' else [channel if channel != 'chromium' else None]
        for candidate in options:
            try:
                browser = pw.chromium.launch(headless=not show_browser,**({'channel':candidate} if candidate else {}))
                result['browser_channel'] = candidate or 'playwright-chromium'
                result['browser_version'] = browser.version
                break
            except Exception as exc:
                launch_errors.append(f'{candidate}: {str(exc).splitlines()[0]}')
        if browser is None:
            raise RuntimeError('No Edge/Chromium executable: ' + '; '.join(launch_errors))
        try:
            for mode in ('top_level','one_frame','two_frames'):
                ctx = browser.new_context()
                # Prevent ONLY the MCEL Scramjet initializer from running so a
                # competing SW registration cannot interfere with this controlled test.
                ctx.route('**/mcel-surface.js', lambda route:route.abort())
                events=[]
                page=ctx.new_page()
                page.on('console', lambda msg: events.append('console '+msg.type+': '+msg.text[:200]))
                page.on('pageerror', lambda err: events.append('pageerror '+str(err)[:200]))
                page.on('requestfailed', lambda req: events.append('failed '+req.url[:140]+' '+str(req.failure)[:80]))
                page.on('response', lambda res: events.append('HTTP '+str(res.status)+' '+res.url[:140]) if res.status >= 400 else None)
                server=None
                try:
                    if mode == 'top_level':
                        url=sidecar+SURFACE_PATH
                    else:
                        host_cls=type('EphemeralHost', (HostPage,), {'target':sidecar,'level':1 if mode=='one_frame' else 2})
                        server=ThreadingHTTPServer(('127.0.0.1',0),host_cls)
                        threading.Thread(target=server.serve_forever,daemon=True).start()
                        url=f'http://127.0.0.1:{server.server_port}/'
                    page.goto(url,wait_until='domcontentloaded',timeout=12000)
                    # Wait for the isolated page to be fully visible in either frame stack.
                    target=None
                    for _ in range(25):
                        target=next((f for f in page.frames if f.url.startswith(sidecar+SURFACE_PATH)), None)
                        if target: break
                        page.wait_for_timeout(200)
                    if target is None:
                        raise RuntimeError('Isolated target iframe never loaded; frames='+str([f.url for f in page.frames]))
                    probe = target.evaluate(EVALUATE_REGISTRATION,{'script':WORKER_PATH,'timeoutMs':_TIMEOUT_MS})
                    result['tests'][mode]=probe
                    print('SW_DIFF '+mode+' '+json.dumps(probe)[:1200],flush=True)
                except Exception as exc:
                    result['tests'][mode]={'error':f'{type(exc).__name__}: {str(exc)[:800]}'}
                    print('SW_DIFF '+mode+' ERROR '+str(exc)[:400], flush=True)
                finally:
                    if events: result['tests'][mode]['browser_events'] = events[-20:]
                    if server:
                        server.shutdown()
                        server.server_close()
                    ctx.close()
        finally:
            browser.close()
    a = result['tests']
    first=a.get('top_level',{}).get('registration')
    second=a.get('one_frame',{}).get('registration')
    third=a.get('two_frames',{}).get('registration')
    if first == 'registered' and (second != 'registered' or third != 'registered'):
        result['interpretation']='ISOLATION_CONTEXT_SENSITIVE: standalone registers, at least one iframe context does not'
    elif first != 'registered' and second != 'registered' and third != 'registered':
        result['interpretation']='BROWSER_OR_WORKER_WIDE: no tested context registered (inspect status, worker response, and profile)'
    elif first == second == third == 'registered':
        result['interpretation']='FRESH_EDGE_CONTEXT_OK: all controlled contexts register; compare with the normal Edge profile or Scramjet worker'
    else:
        result['interpretation']='MIXED_RESULT: inspect per-context results'
    print('SW_DIFF INTERPRETATION '+result['interpretation'],flush=True)
    return result


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel', choices=['auto','msedge','chromium'],default='auto')
    parser.add_argument('--headful',action='store_true')
    args=parser.parse_args()
    root=Path.cwd().resolve()
    try:
        sidecar=sidecar_from_state(root)
        out_dir=root/'runtime'/'reports'/'mcel-browser-service-worker-differential-0008'
        out_dir.mkdir(parents=True,exist_ok=True)
        report=diagnose(root,sidecar,args.channel,args.headful)
        file=out_dir/'report.json'
        file.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print('SW_DIFF REPORT '+str(file),flush=True)
        return 0 if report['interpretation'] else 1
    except Exception as exc:
        print('SW_DIFF ERROR '+f'{type(exc).__name__}: {exc}',flush=True)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
