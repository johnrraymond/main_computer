"""Read-only production Scramjet site-compatibility diagnostic.

Run from the Main Computer repository root while the MCEL Browser is open:

    python -m tools.mcel_browser_site_diagnostic_0013

Does NOT rebuild Docker, change network policy, modify browser profiles, or
register a new Docker session. Uses a fresh temporary Edge context and the
existing isolated production sidecar. Results are written under runtime/reports.
"""
from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlencode, urlsplit

REPORT_FOLDER = 'mcel-browser-site-diagnostic-0013'
TARGETS = ('https://example.org/', 'https://youtube.com/', 'https://www.youtube.com/')


def load_sidecar(repo: Path) -> dict:
    path = repo / 'runtime' / 'reports' / 'mcel-browser-scramjet-app' / 'container-state.json'
    state = json.loads(path.read_text(encoding='utf-8'))
    name = state.get('container')
    image = state.get('image')
    base = state.get('url')
    if not isinstance(name, str) or not re.fullmatch(r'mcel-browser-scramjet-app-[a-f0-9]{8}', name):
        raise RuntimeError('Refusing to inspect an unverified production container name')
    if not isinstance(image, str) or not image.startswith('mcel-browser-scramjet-app:'):
        raise RuntimeError('Production image identity invalid')
    if not isinstance(base, str):
        raise RuntimeError('Production URL missing')
    parts = urlsplit(base)
    if parts.scheme != 'http' or parts.hostname != '127.0.0.3' or not parts.port or parts.path not in ('', '/'):
        raise RuntimeError('Production URL is not an isolated 127.0.0.3 origin')
    proc = subprocess.run(['docker', 'inspect', '--format',
                           '{{index .Config.Labels "mcel.browser.scramjet.app"}}|{{.Config.Image}}|{{.State.Running}}',
                           name], text=True, capture_output=True, timeout=15, check=False)
    if proc.returncode or proc.stdout.strip() != f'true|{image}|true':
        raise RuntimeError('The named Docker container is not a running authenticated production sidecar: ' + proc.stderr[:200])
    return {'container': name, 'image': image, 'url': parts.scheme + '://' + parts.netloc}


def docker_connection_probe(container: str) -> dict:
    # Node is present in the pinned Scramjet Docker image. Fixed destinations;
    # never pass user supplied scripts, domains, or proxy credentials.
    javascript = '''const dns=require('node:dns').promises;
const https=require('node:https');
const names=['example.org','youtube.com','www.youtube.com'];
const makeResolver=(ip)=>{const x=new dns.Resolver({timeout:3000,tries:1});x.setServers([ip]);return x};
const resolvers={'cloudflare-family':makeResolver('1.1.1.3'),'cloudflare-standard':makeResolver('1.1.1.1')};
(async()=>{for(const name of names){let item={host:name};
try{item.dns=(await dns.lookup(name,{all:true})).map(x=>x.address).slice(0,4)}catch(e){item.dnsError=e.code||e.message}
item.resolvers={};for(const [key,r] of Object.entries(resolvers)){try{item.resolvers[key]=(await r.resolve4(name)).slice(0,4)}catch(e){item.resolvers[key]={error:e.code||e.message}}}
try{item.https=await new Promise(resolve=>{const req=https.request('https://'+name+'/',{method:'HEAD',timeout:6500,headers:{'User-Agent':'MCEL-Browser-Connectivity-Diagnostic/1.0'}},res=>{res.resume();resolve({status:res.statusCode,location:res.headers.location||null})});req.on('error',e=>resolve({error:e.code||e.message}));req.on('timeout',()=>req.destroy(Error('connect timeout')));req.end()})}catch(e){item.https={error:e.code||e.message}}
process.stdout.write(JSON.stringify(item)+'\\n')}
})().catch(e=>{process.stderr.write(String(e));process.exitCode=1})'''
    try:
        proc = subprocess.run(['docker', 'exec', container, 'node', '-e', javascript],
                              text=True, capture_output=True, timeout=40, check=False)
        entries = []
        for line in proc.stdout.splitlines():
            try:
                entries.append(json.loads(line))
            except ValueError:
                pass
        return {'exit_code': proc.returncode, 'results': entries, 'error': proc.stderr[:450]}
    except Exception as exc:
        return {'error': str(exc)[:450]}


def probe_websites(base: str, timeout_seconds: int = 14) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError('Playwright is required; it is already used by the existing Scramjet smoke') from exc
    output = {}
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
        except Exception:
            browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=False, service_workers='allow')
        for target in TARGETS:
            page = context.new_page()
            observations = deque(maxlen=100)
            def note(kind, item):
                observations.append({'kind':kind, **item})
            page.on('console', lambda m: note('console', {'level':m.type,'message':m.text[:400]}) if m.type in ('error','warning') else None)
            page.on('pageerror', lambda error: note('pageerror', {'message':str(error)[:500]}))
            page.on('requestfailed', lambda request: note('requestfailed', {'url': request.url[:500], 'failure': request.failure}))
            page.on('response', lambda response: note('response', {'url':response.url[:500], 'status':response.status}) if response.status >= 400 else None)
            script = '/mcel-surface.html?' + urlencode({'address':target})
            data = {'site':target,'entrypoint':base+script,'frames':[], 'events':[], 'outcome':'unknown'}
            try:
                page.goto(base+script, wait_until='domcontentloaded', timeout=20000)
                deadline=time.monotonic()+timeout_seconds
                while time.monotonic()<deadline:
                    urls=[frame.url for frame in page.frames]
                    if any('/scramjet/' in u for u in urls):
                        break
                    page.wait_for_timeout(400)
                # Give the remote page time to complete initial document loading.
                page.wait_for_timeout(4500)
                for frame in page.frames:
                    item = {'url':frame.url[:800]}
                    try:
                        item['title'] = frame.title(timeout=1200)[:200]
                    except Exception:
                        pass
                    try:
                        item['body'] = frame.locator('body').inner_text(timeout=1400)[:850]
                    except Exception:
                        pass
                    data['frames'].append(item)
                bodies = [f.get('body','') for f in data['frames']]
                if any('The server could not route your request' in x for x in bodies):
                    data['outcome'] = 'scramjet-route-error'
                elif any('/scramjet/' in f['url'] for f in data['frames']):
                    data['outcome'] = 'proxied-frame-created'
                else:
                    data['outcome'] = 'no-proxied-frame'
                data['events'] = list(observations)
            except Exception as exc:
                data['error'] = str(exc)[:750]
                data['events'] = list(observations)
            finally:
                page.close()
            output[target] = data
            print('SCRAMJET_SITE', json.dumps({'site':target,'outcome':data['outcome'],
                  'frames':[{'url': f['url'],'title':f.get('title'),'body':f.get('body','')[:120]} for f in data['frames']],
                  'failures':[x for x in data['events'] if x['kind'] in ('requestfailed','response')][-8:]},ensure_ascii=False),flush=True)
        context.close()
        browser.close()
    return output


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, default=14, help='Seconds to observe each site before classification (5..45)')
    args=parser.parse_args()
    if not 5 <= args.timeout <= 45:
        parser.error('--timeout must be between 5 and 45')
    repo=Path.cwd()
    if not (repo/'main_computer'/'viewport_server.py').is_file():
        parser.error('Run from the Main Computer repository root')
    sidecar=load_sidecar(repo)
    print('SCRAMJET_SITE_CONTAINER',json.dumps(sidecar),flush=True)
    connection=docker_connection_probe(sidecar['container'])
    print('SCRAMJET_SITE_CONTAINER_NETWORK',json.dumps(connection),flush=True)
    websites=probe_websites(sidecar['url'],args.timeout)
    try:
        logs = subprocess.run(['docker','logs','--tail','90',sidecar['container']],
                              text=True,capture_output=True,timeout=12,check=False)
        docker_logs=(logs.stdout+'\n'+logs.stderr)[-9000:]
    except Exception as exc:
        docker_logs='Docker log retrieval unavailable: '+str(exc)
    report={'sidecar':sidecar,'container_network':connection,'websites':websites,
            'docker_logs':docker_logs}
    folder=repo/'runtime'/'reports'/REPORT_FOLDER
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/'report.json'
    target.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('SCRAMJET_SITE_REPORT',target,flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
