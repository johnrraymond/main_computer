"""Independently register Scramjet's *real* /sw.js in a fresh Edge context.

From Main Computer checkout root, with its production browser Docker running:
    python -m tools.mcel_browser_actual_worker_0009

The normal mcel-surface.js initializer is intentionally blocked ONLY in this
isolated Playwright context, so it cannot race the explicit registration.
Docker, main application files, and the user's Edge profile are not modified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

REGISTER_SCRIPT = r'''async () => {
  const state = {secure: isSecureContext, origin:location.origin,
                 hasAPI: Boolean(navigator.serviceWorker)};
  const timeout = (promise, label, limit = 18000) => Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(Error(label + ' timeout')), limit)),
  ]);
  try {
    const results = {};
    for (const path of ['/sw.js', '/scram/scramjet.all.js']) {
      const response = await timeout(fetch(path, {cache:'no-store'}), 'Fetch '+path, 10000);
      results[path] = {http:response.status,
                       type:response.headers.get('content-type'),
                       bytes:(await response.arrayBuffer()).byteLength};
    }
    state.resources = results;
  } catch(e) {state.fetchError = String(e)}
  try {
    const registration = await timeout(navigator.serviceWorker.register('/sw.js',
      {scope:'/', updateViaCache:'none'}), 'Real Scramjet SW registration');
    state.registration = 'registered';
    state.scope = registration.scope;
    state.installing = registration.installing?.state || null;
    state.waiting = registration.waiting?.state || null;
    state.active = registration.active?.state || null;
    try {state.unregister = await timeout(registration.unregister(), 'unregister', 4000)}
    catch(e) {state.unregisterError = String(e)}
  } catch(e) {
    state.registration = 'failed';
    state.error = String(e);
    state.name = e?.name || null;
  }
  return state;
}'''


def docker_origin(root: Path) -> str:
    state_file = root/'runtime/reports/mcel-browser-scramjet-app/container-state.json'
    if not state_file.is_file():
        raise RuntimeError('Open the MCEL Browser to start its production Docker container first.')
    state = json.loads(state_file.read_text(encoding='utf-8'))
    url = str(state.get('url', '')).rstrip('/')
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.3' or not parsed.port
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment
            or parsed.username or parsed.password):
        raise RuntimeError('Unexpected Docker origin; refusing probe: '+url[:120])
    return url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--headful', action='store_true')
    args = parser.parse_args()
    root = Path.cwd().resolve()
    if not (root/'main_computer/viewport_server.py').is_file():
        parser.error('Run from the Main Computer repository root.')
    try:
        from playwright.sync_api import sync_playwright
        origin = docker_origin(root)
        report = {'time_utc':datetime.now(timezone.utc).isoformat(),
                  'origin':origin, 'tests':{}, 'events':[]}
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge',headless=not args.headful)
            report['browser_version'] = browser.version
            try:
                ctx = browser.new_context()
                # Intentionally disable only the frontend initializer. This is
                # a dedicated worker test, never a normal browsing session.
                ctx.route('**/mcel-surface.js', lambda route:route.abort())
                page = ctx.new_page()
                def log(kind, detail):
                    report['events'].append((kind+' '+str(detail))[:500])
                    report['events'] = report['events'][-100:]
                page.on('console', lambda m:log('console:'+m.type,m.text))
                page.on('pageerror', lambda e:log('pageerror',e))
                page.on('requestfailed', lambda r: log('failed',r.url+' '+str(r.failure))
                        if '/mcel-surface.js' not in r.url else None)
                page.on('response', lambda r:log('HTTP '+str(r.status),r.url)
                        if r.status>=400 else None)
                try:
                    cdp = ctx.new_cdp_session(page)
                    cdp.send('ServiceWorker.enable')
                    cdp.on('ServiceWorker.workerErrorReported', lambda e:log('worker-error',json.dumps(e)))
                    cdp.on('ServiceWorker.workerVersionUpdated', lambda e:log('worker-version',json.dumps(e)[:450]))
                except Exception as e:
                    log('cdp-unavailable',e)
                page.goto(origin+'/mcel-surface.html',wait_until='domcontentloaded',timeout=15000)
                report['tests']['real_scramjet_worker'] = page.evaluate(REGISTER_SCRIPT)
                print('SW_REAL '+json.dumps(report['tests']['real_scramjet_worker']),flush=True)
                for line in report['events'][-20:]: print('SW_REAL_EVENT '+line,flush=True)
                ctx.close()
            finally:
                browser.close()
        out = root/'runtime/reports/mcel-browser-actual-worker-0009/report.json'
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print('SW_REAL_REPORT '+str(out),flush=True)
        return 0 if report['tests']['real_scramjet_worker'].get('registration')=='registered' else 1
    except Exception as e:
        print(f'SW_REAL_ERROR {type(e).__name__}: {e}',file=sys.stderr,flush=True)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
