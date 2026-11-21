"""Docker-on-demand Scramjet functional + MCEL primary-surface acceptance smoke.

From the Main Computer repository root:
    python -m tools.mcel_browser_scramjet_smoke                # build/start/check/stop
    python -m tools.mcel_browser_scramjet_smoke start          # keep sidecar running
    python -m tools.mcel_browser_scramjet_smoke status
    python -m tools.mcel_browser_scramjet_smoke stop
    python -m tools.mcel_browser_scramjet_smoke run --keep     # leave sidecar for inspection
    python -m tools.mcel_browser_scramjet_smoke --legacy-local

The image is built from a pinned upstream Git commit and MCEL's local Dockerfile.
No Node/pnpm installation is required on Windows. The smoke sidecar is published only at
127.0.0.2 (distinct from production 127.0.0.3) and the fixture is available only inside its container at 127.0.0.9.
The fixture Wisp allowlist is intentionally SMOKE ONLY. A pass is not a security audit.
"""
from __future__ import annotations

import argparse
from contextlib import suppress
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener

COMMIT = 'f6f83cbc93091e47b9c357eb00ea289828aedf94'
REPO_URL = 'https://github.com/MercuryWorkshop/Scramjet-App.git'
IMAGE = 'mcel-browser-scramjet-smoke:' + COMMIT[:12] + '-v4'
ISOLATED_IP = '127.0.0.2'
TRUSTED_IP = '127.0.0.1'
FIXTURE_URL = 'http://127.0.0.9:48081/'  # INSIDE the container
TRAP_URL = 'http://127.0.0.10:48082/mcel-secret'  # denied by Wisp allowlist
LABEL = 'mcel.browser.scramjet.smoke'
CONTAINER = 'mcel-browser-scramjet-smoke'


def report_dir(repo: Path) -> Path:
    return repo / 'runtime' / 'reports' / 'mcel-browser-scramjet-docker-0001'


def check(report: dict, name: str, passed: bool, detail: object = '') -> None:
    report['checks'][name] = {'status': 'PASS' if passed else 'FAIL', 'detail': str(detail)[:700]}
    print('SCRAMJET_DOCKER ' + name + ' ' + ('PASS' if passed else 'FAIL') +
          (f' {str(detail)[:200]}' if detail else ''), flush=True)


def run_command(cmd: list[str], *, cwd: Path | None = None, timeout: int = 60, check_ok: bool = True) -> subprocess.CompletedProcess:
    try:
        p = subprocess.run(cmd, cwd=cwd, encoding='utf-8', errors='replace',
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f'Cannot execute {cmd[0]}: {type(e).__name__}: {e}') from e
    if check_ok and p.returncode != 0:
        raise RuntimeError(f'Command failed ({p.returncode}): {cmd[:5]!r}\n{p.stdout[-1700:]}')
    return p


def port_for(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def fetch_status(url: str, timeout: float = 2) -> tuple[int, int]:
    # Never send 127/8 through system HTTP proxies.
    with build_opener(ProxyHandler({})).open(url, timeout=timeout) as response:
        return response.status, len(response.read(1024 * 1024))


def docker_cmd(*args: str, timeout: int = 60, check_ok: bool = True) -> subprocess.CompletedProcess:
    return run_command(['docker', *args], timeout=timeout, check_ok=check_ok)


def require_docker() -> None:
    if not shutil.which('docker'):
        raise RuntimeError('Docker CLI not found. Docker Desktop with a running Linux engine is required.')
    p = docker_cmd('info', '--format', '{{.OSType}}', timeout=15, check_ok=False)
    if p.returncode or p.stdout.strip() != 'linux':
        raise RuntimeError('Docker Linux engine unavailable. Start Docker Desktop (Linux containers). ' + p.stdout[-500:])


def get_vendor(repo: Path, no_bootstrap: bool) -> Path:
    vendor = repo / 'runtime' / 'vendor' / 'scramjet-app-smoke'
    if not (vendor / '.git').exists():
        if no_bootstrap:
            raise RuntimeError('Scramjet Git checkout absent and --no-bootstrap requested.')
        if not shutil.which('git'):
            raise RuntimeError('Git CLI is required to retrieve the pinned Scramjet source.')
        vendor.parent.mkdir(parents=True, exist_ok=True)
        print('SCRAMJET_DOCKER fetching pinned upstream source', flush=True)
        run_command(['git', 'clone', '--no-checkout', '--filter=blob:none', REPO_URL, str(vendor)], timeout=180)
    if not shutil.which('git'):
        raise RuntimeError('Git CLI needed to verify the upstream commit')
    probe = run_command(['git', '-C', str(vendor), 'cat-file', '-t', COMMIT], check_ok=False)
    if probe.returncode or probe.stdout.strip() != 'commit':
        if no_bootstrap:
            raise RuntimeError('Pinned Scramjet commit not present locally; remove --no-bootstrap.')
        run_command(['git', '-C', str(vendor), 'fetch', '--depth=1', 'origin', COMMIT], timeout=180)
    assert run_command(['git', '-C', str(vendor), 'rev-parse', COMMIT]).stdout.strip() == COMMIT
    return vendor


def prepare_build_context(repo: Path, *, no_bootstrap: bool) -> Path:
    vendor = get_vendor(repo, no_bootstrap)
    context = repo / 'runtime' / 'vendor' / 'scramjet-docker-smoke-build'
    context.mkdir(parents=True, exist_ok=True)
    # Git archive uses the pinned COMMIT, never the mutable local smoke-patched index.js.
    archive = subprocess.run(['git', '-C', str(vendor), 'archive', '--format=tar', COMMIT,
                              'src', 'public', 'package.json', 'pnpm-lock.yaml', 'LICENSE'],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40, check=False)
    if archive.returncode:
        raise RuntimeError('Pinned Git archive failed: ' + archive.stderr.decode(errors='replace')[:500])
    with tarfile.open(fileobj=BytesIO(archive.stdout), mode='r:') as tar:
        for member in tar:
            p = PurePosixPath(member.name)
            if p.is_absolute() or '..' in p.parts or not member.isfile():
                continue
            dest = context.joinpath(*p.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source:
                dest.write_bytes(source.read())
    assets = repo / 'docker' / 'mcel-browser-scramjet'
    for name in ('Dockerfile','patch-server.mjs','fixture.mjs','entrypoint.mjs','mcel-surface.html','mcel-surface.js','mcel-sw-probe.js',
                 'mcel-scramjet-db.js','patch-stock-init.mjs'):
        if not (assets / name).is_file():
            raise RuntimeError('Docker smoke assets missing: ' + str(assets / name))
        dest = context / ('Dockerfile' if name == 'Dockerfile' else 'mcel-docker/' + name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(assets / name, dest)
    return context


def build_image(repo: Path, *, rebuild: bool, no_bootstrap: bool) -> None:
    if not rebuild and docker_cmd('image', 'inspect', IMAGE, check_ok=False).returncode == 0:
        print('SCRAMJET_DOCKER image cached ' + IMAGE, flush=True)
        return
    context = prepare_build_context(repo, no_bootstrap=no_bootstrap)
    log_path = report_dir(repo) / 'build.log'
    print('SCRAMJET_DOCKER building pinned image ' + IMAGE, flush=True)
    cmd = ['docker', 'build', '--progress=plain', '--tag', IMAGE, '--file', str(context / 'Dockerfile'), str(context)]
    with log_path.open('w', encoding='utf-8') as stream:
        stream.write(' '.join(cmd) + '\n')
        stream.flush()
        try:
            p = subprocess.run(cmd, stdout=stream, stderr=subprocess.STDOUT, timeout=900, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f'Docker image build failed: {type(e).__name__}: {e}; log={log_path}') from e
    if p.returncode:
        tail = '\n'.join(log_path.read_text(encoding='utf-8', errors='replace').splitlines()[-25:])
        raise RuntimeError(f'Docker build failed ({p.returncode}); log={log_path}\n{tail}')
    print('SCRAMJET_DOCKER image built; log=' + str(log_path), flush=True)


def state_path(repo: Path) -> Path:
    return report_dir(repo) / 'container-state.json'


def load_state(repo: Path) -> dict | None:
    try:
        value = json.loads(state_path(repo).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def inspect_owned(container: str) -> bool:
    if not re.fullmatch(r'mcel-browser-scramjet-smoke(?:-[a-z0-9]+)?', container):
        return False
    p = docker_cmd('inspect', '--format', '{{index .Config.Labels "' + LABEL + '"}}', container, check_ok=False)
    return p.returncode == 0 and p.stdout.strip() == 'true'


def status(repo: Path) -> dict | None:
    state = load_state(repo)
    if not state or not inspect_owned(state.get('container', '')) or state.get('ip') != ISOLATED_IP:
        return None
    p = docker_cmd('inspect', '--format', '{{.State.Running}}', state['container'], check_ok=False)
    if p.returncode or p.stdout.strip() != 'true':
        return None
    return state


def stop(repo: Path) -> None:
    state = load_state(repo)
    if state and inspect_owned(state.get('container', '')):
        print('SCRAMJET_DOCKER stopping ' + state['container'], flush=True)
        docker_cmd('rm', '-f', state['container'], timeout=30)
    with suppress(OSError):
        state_path(repo).unlink()


def start(repo: Path, *, rebuild: bool = False, no_bootstrap: bool = False) -> dict:
    live = status(repo)
    if live and rebuild:
        stop(repo)
        live = None
    if live:
        try:
            http_code, _ = fetch_status(live['url'], timeout=2)
            if http_code == 200:
                print('SCRAMJET_DOCKER reusing on-demand sidecar ' + live['url'], flush=True)
                return live
        except Exception:
            pass
        stop(repo)
    old = load_state(repo)
    if old and inspect_owned(old.get('container','')):
        stop(repo)
    build_image(repo, rebuild=rebuild, no_bootstrap=no_bootstrap)
    port = port_for(ISOLATED_IP)
    name = CONTAINER + '-' + secrets.token_hex(4)
    cmd = ['run', '--detach', '--name', name, '--label', LABEL + '=true',
           '--publish', f'{ISOLATED_IP}:{port}:8080', '--network', 'bridge',
           '--cap-drop=ALL', '--security-opt=no-new-privileges',
           '--pids-limit=256', '--memory=768m', '--cpus=2', '--read-only',
           '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m', '--user', 'node',
           '--env', 'MCEL_SCRAMJET_SMOKE=1', IMAGE]
    p = docker_cmd(*cmd, timeout=60)
    state = {'container': name, 'container_id': p.stdout.strip(),
             'ip': ISOLATED_IP, 'port': port, 'url': f'http://{ISOLATED_IP}:{port}/',
             'image': IMAGE, 'git_commit': COMMIT, 'fixture_url': FIXTURE_URL,
             'created_utc': datetime.now(timezone.utc).isoformat()}
    state_path(repo).write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            if fetch_status(state['url'], 0.8)[0] == 200:
                print('SCRAMJET_DOCKER READY ' + state['url'], flush=True)
                return state
        except (OSError, URLError, TimeoutError):
            pass
        if not status(repo):
            break
        time.sleep(0.4)
    tail = docker_cmd('logs', '--tail', '40', name, check_ok=False).stdout
    stop(repo)
    raise RuntimeError('Docker Scramjet sidecar failed health check: ' + tail[-1200:])


class MockTrustedMCEL(BaseHTTPRequestHandler):
    """Same-origin backend stub, serving the REAL Browser HTML/CSS/JS.

    Only Docker ownership is stubbed, because the smoke already owns the
    isolated container. The frontend under test is the production controller.
    """
    sidecar_url = ''
    secret = ''
    remote_url = FIXTURE_URL
    requested_secret = 0
    source_html = ''
    source_css = ''
    source_js = ''
    commands: list[str] = []
    _token = 'a' * 32

    def log_message(self, *_):
        pass

    def do_POST(self):
        if self.path != '/api/applications/browser/control':
            self.send_error(404)
            return
        try:
            raw = self.rfile.read(min(int(self.headers.get('Content-Length','0')),4096))
            payload = json.loads(raw)
            action = payload['operation']
            if action not in ('surface-start','surface-touch','surface-close'):
                self.send_error(400)
                return
            type(self).commands.append(action)
            value = ({'ok':True,'session':self._token,'sidecar_url':self.sidecar_url,
                      'engine':'scramjet-docker'} if action == 'surface-start' else {'ok':True})
        except (ValueError, KeyError, TypeError):
            self.send_error(400)
            return
        data = json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith('/mcel-secret'):
            type_ = 'text/plain'
            data = self.secret.encode()
            type(self).requested_secret += 1
        elif self.path == '/' or self.path.startswith('/applications/browser/'):
            type_ = 'text/html'
            assert '<iframe id="browser-frame"' in self.source_html, 'Production Browser must own its Scramjet iframe'
            html = self.source_html.replace('/* BROWSER_STYLES */',self.source_css)
            html = html.replace('/* BROWSER_SCRIPT */',self.source_js)
            data = html.encode()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', type_)
        self.send_header('Cache-Control', 'no-store')
        if self.path == '/' or self.path.startswith('/applications/browser/'):
            self.send_header('Set-Cookie', 'MOCK_MCEL_CANARY=' + self.secret + '; Path=/; SameSite=Lax')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def acceptance(repo: Path, sidecar: dict, *, headful: bool) -> int:
    from playwright.sync_api import sync_playwright, expect
    report = {'timestamp_utc':datetime.now(timezone.utc).isoformat(), 'status':'ERROR',
              'image': IMAGE, 'source_commit': COMMIT, 'container': sidecar['container'],
              'sidecar_url': sidecar['url'], 'checks':{}, 'browser_trace':[],
              'scope':'Actual production MCEL Browser frontend with Docker session backend stub; Docker lifecycle covered independently'}
    report_file = report_dir(repo) / 'report.json'
    MockTrustedMCEL.secret = secrets.token_hex(20)
    MockTrustedMCEL.sidecar_url = sidecar['url']
    MockTrustedMCEL.requested_secret = 0
    MockTrustedMCEL.commands = []
    assets = repo / 'main_computer' / 'web' / 'applications'
    MockTrustedMCEL.source_html = (assets / 'apps' / 'browser-page.html').read_text(encoding='utf-8')
    MockTrustedMCEL.source_css = (assets / 'styles' / 'browser-page.css').read_text(encoding='utf-8')
    MockTrustedMCEL.source_js = (assets / 'scripts' / 'browser-page.js').read_text(encoding='utf-8')
    # Smoke and production MUST use distinct cookie hosts. In this controlled
    # mock only, substitute the allowed sidecar IP; the production file on disk
    # remains unchanged and still permits exclusively 127.0.0.3.
    guard = "base.hostname !== '127.0.0.3'"
    if MockTrustedMCEL.source_js.count(guard) != 1:
        raise RuntimeError('Browser isolated-origin guard changed; update Docker smoke intentionally')
    MockTrustedMCEL.source_js = MockTrustedMCEL.source_js.replace(guard, "base.hostname !== '127.0.0.2'")
    host = ThreadingHTTPServer((TRUSTED_IP, 0), MockTrustedMCEL)
    thread = threading.Thread(target=host.serve_forever, daemon=True)
    thread.start()
    try:
        baseline = (repo / 'main_computer' / 'mcel_backend_browser.py').read_text(encoding='utf-8')
        check(report, 'legacy-screenshot-bridge-recognized', 'screenshot' in baseline.lower())
        check(report, 'docker-running-on-demand', status(repo) is not None)
        check(report, 'isolated-host-ip', fetch_status(sidecar['url'])[0] == 200, sidecar['url'])
        try:
            fetch_status(f'http://{TRUSTED_IP}:{sidecar["port"]}/', timeout=0.8)
            check(report, 'not-bound-on-trusted-ip', False, 'The container is reachable on trusted IP')
        except (URLError, OSError, TimeoutError):
            check(report, 'not-bound-on-trusted-ip', True)
        for path, label in [('scram/scramjet.all.js','scramjet-client-bundle'),
                             ('scram/scramjet.wasm.wasm','scramjet-ast-wasm'),
                             ('baremux/index.js','bare-mux'), ('libcurl/index.mjs','network-transport'),
                             ('sw.js','service-worker-script'),
                             ('mcel-surface.html','mcel-main-surface-resource'),
                             ('mcel-surface.js','mcel-main-surface-controller'),
                             ('mcel-sw-probe.js','mcel-service-worker-diagnostic-resource'),
                             ('mcel-scramjet-db.js','mcel-scramjet-idb-initializer')]:
            try:
                code, length = fetch_status(sidecar['url'] + path, timeout=6)
                check(report, label, code == 200 and length > 10, f'HTTP {code}, {length} bytes')
            except Exception as e:
                check(report, label, False, f'{type(e).__name__}: {e}')
        with sync_playwright() as pw:
            browser = None
            errors = []
            for channel in ('msedge', None):
                try:
                    browser = pw.chromium.launch(headless=not headful, **({'channel':channel} if channel else {}))
                    break
                except Exception as exc:
                    errors.append(str(exc).splitlines()[0])
            if browser is None:
                raise RuntimeError('Cannot launch Edge or Chromium: ' + '; '.join(errors))
            try:
                context = browser.new_context()
                page = context.new_page()
                def trace(message: str) -> None:
                    report['browser_trace'].append(message[:300])
                    report['browser_trace'][:] = report['browser_trace'][-120:]
                page.on('pageerror', lambda e: trace('pageerror: ' + str(e)))
                page.on('console', lambda e: trace('console '+ e.type + ': ' + e.text))
                page.on('requestfailed',lambda e: trace('requestfailed: ' + e.url + ' / ' + str(e.failure)))
                page.on('response',lambda e: trace('HTTP '+ str(e.status) + ' ' + e.url) if e.status >= 400 else None)
                page.goto(f'http://{TRUSTED_IP}:{host.server_port}/applications/browser/{FIXTURE_URL}', wait_until='domcontentloaded')
                check(report, 'mock-trusted-cookie-present', MockTrustedMCEL.secret in page.evaluate('document.cookie'))
                check(report, 'actual-mcel-browser-layout-mounted',
                      page.locator('#browser-app').get_attribute('data-mcel-app') == 'browser'
                      and page.locator('#browser-toolbar').count() == 1
                      and page.locator('#browser-frame').count() == 1)
                frame = page.frame_locator('#browser-frame')
                try:
                    expect(frame.locator('#mcel-remote-frame').content_frame.locator('#fixture-ready')).to_have_text('ready', timeout=50000)
                    remote = frame.frame_locator('#mcel-remote-frame')
                    check(report, 'mcel-main-surface-rewritten-dom', remote.locator('#fixture-ready').inner_text() == 'ready')
                    check(report, 'mcel-controller-ready-handshake', page.locator('#browser-status').get_attribute('data-error') != 'true'
                          and 'initialize' not in (page.locator('#browser-status').inner_text().lower()))
                    check(report, 'production-browser-session-api-used', MockTrustedMCEL.commands.count('surface-start') == 1, MockTrustedMCEL.commands)
                    check(report, 'mcel-main-surface-ast-fetch', remote.locator('#data').inner_text() == 'rewritten fetch succeeded')
                    check(report, 'mcel-main-surface-css', remote.locator('#color').evaluate('(el)=>getComputedStyle(el).color') == 'rgb(17, 101, 207)')
                    remote.locator('#click').click()
                    check(report, 'mcel-main-surface-pointer', remote.locator('#clicked').inner_text() == 'clicked natively')
                    remote.locator('#typing').fill('typed in Docker-scramjet MCEL surface')
                    check(report, 'mcel-main-surface-keyboard', remote.locator('#typing').input_value() == 'typed in Docker-scramjet MCEL surface')
                    remote.locator('#select').select_text()
                    check(report, 'mcel-main-surface-selection', 'selectable fixture text' in remote.locator('#select').evaluate('el=>getSelection().toString()'))
                    # An accessible test trap exists on the container loopback and MUST be denied by Wisp's fixture-only allowlist.
                    trap = remote.locator('#fixture-root').evaluate('''async (_,u)=>{try{const r=await Promise.race([fetch(u),new Promise((_,reject)=>setTimeout(()=>reject(Error('timeout')),6500))]);return {status:r.status,body:(await r.text()).slice(0,100)}}catch(e){return {error:String(e)}}}''', TRAP_URL)
                    check(report, 'smoke-wisp-denies-nonfixture-private-endpoint', 'MCEL_TRAP_SECRET' not in trap.get('body',''), trap)
                    # Do not regard a 500 as proof of an explicit security policy.
                    check(report, 'sidecar-origin-does-not-own-trusted-cookie', MockTrustedMCEL.secret not in frame.locator('body').evaluate('() => document.cookie'))
                    barrier = page.evaluate('''()=>{const f=document.getElementById('browser-frame');try{return {blocked:false, read:f.contentWindow.document.title}}catch(e){return {blocked:e.name==='SecurityError',name:e.name}}}''')
                    check(report, 'cross-origin-mcel-frame-barrier', barrier['blocked'] is True, barrier)
                    page.locator('#browser-address').fill('http://127.0.0.9:48081/next')
                    page.locator('#browser-go').click()
                    expect(remote.locator('#next-title')).to_have_text('Native navigation succeeded', timeout=20000)
                    check(report, 'mcel-toolbar-controls-primary-surface', True)
                    check(report, 'mcel-surface-is-not-screenshot-bridge', page.locator('#browser-surface img').count() == 0)
                    check(report, 'main-surface-fills-region', page.locator('#browser-frame').evaluate('e=>Math.abs(e.getBoundingClientRect().width-e.parentElement.getBoundingClientRect().width)<2 && Math.abs(e.getBoundingClientRect().height-e.parentElement.getBoundingClientRect().height)<2'))
                except Exception as exc:
                    check(report,'mcel-main-surface-pipeline',False,f'{type(exc).__name__}: {str(exc)[:210]}')
                    with suppress(Exception):
                        page.screenshot(path=str(report_dir(repo) / 'mcel-surface-failure.png'),full_page=True)
                    with suppress(Exception):
                        report['mcel_browser_status'] = page.locator('#browser-status').inner_text()
                        report['mcel_browser_frame_url'] = page.locator('#browser-frame').get_attribute('src')
                    with suppress(Exception):
                        report['mcel_messages'] = page.evaluate('window.mcelMessages || []')[-20:]
                    with suppress(Exception):
                        report['browser_frames_at_failure'] = [x.url for x in page.frames]
                    # Distinguish an iframe-specific Chromium/storage restriction
                    # from a sidecar-wide failure to register even a minimal SW.
                    try:
                        independent = context.new_page()
                        independent.goto(sidecar['url'] + 'mcel-surface.html', wait_until='domcontentloaded', timeout=12000)
                        report['first_party_sw_probe'] = independent.evaluate('''async () => {
                          const result = {origin:location.origin,secure:isSecureContext,framed:top!==self};
                          try {
                            const p = navigator.serviceWorker.register('/mcel-sw-probe.js',
                              {scope:'/mcel-worker-probe/',updateViaCache:'none'});
                            const r = await Promise.race([p,new Promise((_,reject) =>
                              setTimeout(()=>reject(Error('minimal SW registration timed out')),7000))]);
                            result.status = 'registered';
                            await r.unregister();
                          } catch(e) {result.status='failed';result.error=String(e)}
                          return result;
                        }''')
                        independent.close()
                        print('SCRAMJET_DIAG first_party_sw_probe=' + json.dumps(report['first_party_sw_probe']),flush=True)
                    except Exception as diagnostic_error:
                        report['first_party_sw_probe'] = {'error':str(diagnostic_error)[:400]}
                    print('SCRAMJET_DIAG browser_status=' + str(report.get('mcel_browser_status','')),flush=True)
                    print('SCRAMJET_DIAG browser_iframe=' + str(report.get('mcel_browser_frame_url','')),flush=True)
                    print('SCRAMJET_DIAG messages=' + json.dumps(report.get('mcel_messages',[]))[:1100],flush=True)
                    print('SCRAMJET_DIAG frames=' + json.dumps(report.get('browser_frames_at_failure',[]))[:1100],flush=True)
                    for line in report['browser_trace'][-15:]:print('SCRAMJET_DIAG ' + line,flush=True)
                context.close()
            finally:
                browser.close()
        required = ('mock-trusted-cookie-present','actual-mcel-browser-layout-mounted','docker-running-on-demand','isolated-host-ip','not-bound-on-trusted-ip',
                    'scramjet-client-bundle','scramjet-ast-wasm','bare-mux','network-transport',
                    'service-worker-script','mcel-main-surface-resource', 'mcel-main-surface-controller',
                    'mcel-service-worker-diagnostic-resource', 'mcel-scramjet-idb-initializer',
                    'mcel-main-surface-rewritten-dom', 'mcel-controller-ready-handshake',
                    'mcel-main-surface-ast-fetch','production-browser-session-api-used','mcel-main-surface-css','mcel-main-surface-pointer',
                    'mcel-main-surface-keyboard','mcel-main-surface-selection',
                    'smoke-wisp-denies-nonfixture-private-endpoint', 'sidecar-origin-does-not-own-trusted-cookie',
                    'cross-origin-mcel-frame-barrier','mcel-toolbar-controls-primary-surface',
                    'mcel-surface-is-not-screenshot-bridge','main-surface-fills-region')
        missing = [n for n in required if report['checks'].get(n,{}).get('status') != 'PASS']
        report['status'] = 'PASS_DOCKER_MCEL_SURFACE_SMOKE_NOT_SECURITY_CERTIFIED' if not missing else 'BLOCKED'
        print('SCRAMJET_DOCKER RESULT '+report['status']+(' missing='+','.join(missing) if missing else ''),flush=True)
        return 0 if not missing else 1
    except Exception as exc:
        report['status'] = 'ERROR'
        report['error'] = f'{type(exc).__name__}: {exc}'
        print('SCRAMJET_DOCKER ERROR ' + report['error'], file=sys.stderr,flush=True)
        return 2
    finally:
        report_file.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print('SCRAMJET_DOCKER REPORT '+str(report_file),flush=True)
        host.shutdown()
        host.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='run', choices=('run','start','stop','status','build'))
    parser.add_argument('--rebuild', action='store_true', help='Rebuild the pinned Docker image')
    parser.add_argument('--no-bootstrap', action='store_true', help='Do not clone/fetch pinned upstream source')
    parser.add_argument('--keep', action='store_true', help='Keep the on-demand container running after the smoke')
    parser.add_argument('--headful', action='store_true', help='Show Edge/Chromium for interactive debugging')
    parser.add_argument('--legacy-local', action='store_true', help='Execute the previous local Node smoke instead of Docker')
    args = parser.parse_args()
    if args.legacy_local:
        from tools import mcel_browser_scramjet_smoke_local
        sys.argv = [sys.argv[0]] + (['--no-bootstrap'] if args.no_bootstrap else []) + (['--headful'] if args.headful else [])
        return mcel_browser_scramjet_smoke_local.main()
    repo = Path.cwd().resolve()
    if not (repo/'main_computer'/'mcel_backend_browser.py').is_file():
        print('SCRAMJET_DOCKER ERROR Run from Main Computer root',file=sys.stderr)
        return 2
    report_dir(repo).mkdir(parents=True,exist_ok=True)
    try:
        require_docker()
        if args.action == 'stop':
            stop(repo)
            print('SCRAMJET_DOCKER STOPPED',flush=True)
            return 0
        if args.action == 'status':
            running = status(repo)
            print('SCRAMJET_DOCKER STATUS ' + json.dumps(running or {'running':False}),flush=True)
            return 0 if running else 1
        if args.action == 'build':
            build_image(repo,rebuild=args.rebuild,no_bootstrap=args.no_bootstrap)
            return 0
        before = status(repo)
        sidecar = start(repo,rebuild=args.rebuild,no_bootstrap=args.no_bootstrap)
        if args.action == 'start':
            print('SCRAMJET_DOCKER ON_DEMAND ' + json.dumps(sidecar,sort_keys=True),flush=True)
            return 0
        try:
            result = acceptance(repo, sidecar, headful=args.headful)
            return result
        finally:
            if args.keep or before:
                print('SCRAMJET_DOCKER PRESERVED ' + sidecar['url'], flush=True)
            else:
                tail = docker_cmd('logs','--tail','70',sidecar['container'],check_ok=False).stdout
                (report_dir(repo)/'sidecar.log').write_text(tail,encoding='utf-8')
                stop(repo)
    except Exception as exc:
        print('SCRAMJET_DOCKER ERROR '+f'{type(exc).__name__}: {exc}',file=sys.stderr,flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
