"""Isolated, automated exploit -> candidate correction -> authorized-regression lab.

Nothing in this module edits production source or changes running MC services.
The default mode creates DISPOSABLE Docker containers with no host ports and
an internal-only network namespace; a second attacker container shares the
fixture's loopback namespace to model Bob on Alice's machine.

    python -m tools.mcel_security_correction_lab run
    python -m tools.mcel_security_correction_lab run --mode local  # developer test only

Copies the five vulnerable modules into a private temporary workspace, applies
strictly anchored trial security changes, tests real HTTP handlers extracted
from baseline/candidate source, and retains a candidate ZIP only upon PASS.
Candidate code is NOT production integrated; gateway/consumer integration and
real Windows-user separation remain explicitly unverified.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODULES = (
    'viewport_server.py',
    'heartbeat.py',
    'main_log_service.py',
    'openclaw_bridge.py',
    'mcel_backend_browser_route.py',
)


def anchored(source: str, before: str, after: str, *, name: str) -> str:
    occur = source.count(before)
    if occur != 1:
        raise RuntimeError(f"CANDIDATE_SOURCE_DRIFT {name}: anchor occurrences={occur}, expected=1")
    return source.replace(before, after, 1)


def make_candidate(filename: str, text: str) -> str:
    if filename == 'viewport_server.py':
        text = anchored(text, 'from __future__ import annotations\n',
                        'from __future__ import annotations\nimport hmac\n', name='viewport-hmac-import')
        text = anchored(text,
            '    def do_GET(self) -> None:\n        dispatch_get(self)\n\n    def do_POST(self) -> None:\n        dispatch_post(self)\n',
            '''    def _security_gate(self) -> bool:
        expected = getattr(self.server, "ingress_capability", None)
        supplied = self.headers.get("X-MC-Gateway-Capability", "")
        if not (isinstance(expected, str) and len(expected) >= 32 and
                isinstance(supplied, str) and hmac.compare_digest(expected, supplied)):
            self.send_response(HTTPStatus.FORBIDDEN)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return False
        return True

    def do_GET(self) -> None:
        if not self._security_gate():
            return
        dispatch_get(self)

    def do_POST(self) -> None:
        if not self._security_gate():
            return
        dispatch_post(self)
''', name='viewport-entry')
    elif filename == 'heartbeat.py':
        text = anchored(text,
            'class HeartbeatHandler(BaseHTTPRequestHandler):\n',
            '''def heartbeat_service_authorized(handler) -> bool:
    expected = getattr(handler.server, "ingress_capability", None)
    supplied = handler.headers.get("X-MC-Internal-Authority", "")
    return bool(isinstance(expected, str) and len(expected) >= 32 and
                isinstance(supplied, str) and hmac.compare_digest(expected, supplied))


class HeartbeatHandler(BaseHTTPRequestHandler):
''', name='heartbeat-service-auth')
        text = anchored(text,
            '''        if self.path == "/api/heartbeat/authority/session":
            if not heartbeat_local_caller(self):''',
            '''        if self.path == "/api/heartbeat/authority/session":
            if not heartbeat_service_authorized(self):
                self._send_json({"ok": False, "error": "heartbeat-service-authority-required"},
                                status=HTTPStatus.FORBIDDEN)
                return
            if not heartbeat_local_caller(self):''', name='heartbeat-session-issuance')
        text = anchored(text,
            '''        if self.path in {"/api/heartbeat/control", "/api/heartbeat/status", "/healthz"}:
            self._send_json(status_payload(self.server.config))''',
            '''        if self.path in {"/api/heartbeat/control", "/api/heartbeat/status", "/healthz"}:
            if not heartbeat_service_authorized(self):
                self._send_json({"ok": False, "error": "heartbeat-service-authority-required"},
                                status=HTTPStatus.FORBIDDEN)
                return
            self._send_json(status_payload(self.server.config))''', name='heartbeat-read-status-auth')
    elif filename == 'main_log_service.py':
        text = anchored(text, 'from __future__ import annotations\n',
                        'from __future__ import annotations\nimport hmac\n', name='mainlog-hmac-import')
        text = anchored(text,
            'class MainLogRequestHandler(BaseHTTPRequestHandler):\n',
            '''def main_log_authorized(handler) -> bool:
    expected = getattr(handler.server, "ingress_capability", None)
    supplied = handler.headers.get("X-MC-Internal-Authority", "")
    return bool(isinstance(expected, str) and len(expected) >= 32 and
                isinstance(supplied, str) and hmac.compare_digest(expected, supplied))


class MainLogRequestHandler(BaseHTTPRequestHandler):
''', name='mainlog-service-auth')
        text = anchored(text,
            '''    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        parsed = urlparse(self.path)
''',
            '''    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        if not main_log_authorized(self):
            _json_response(self, 403, {"ok": False, "state": "authority-required"})
            return
        parsed = urlparse(self.path)
''', name='mainlog-get-auth')
        text = anchored(text,
            '''    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        parsed = urlparse(self.path)
''',
            '''    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        if not main_log_authorized(self):
            _json_response(self, 403, {"ok": False, "state": "authority-required"})
            return
        parsed = urlparse(self.path)
''', name='mainlog-post-auth')
    elif filename == 'openclaw_bridge.py':
        text = anchored(text,
            '''        if token is None:
            return True
        header = self.headers.get("Authorization", "").strip()
        if header == f"Bearer {token}":
''',
            '''        if not token:
            self._send_json({"error": "Bridge token required."}, status=HTTPStatus.UNAUTHORIZED)
            return False
        header = self.headers.get("Authorization", "").strip()
        if hmac.compare_digest(header.encode("utf-8"), f"Bearer {token}".encode("utf-8")):
''', name='openclaw-fail-closed')
        text = anchored(text,'from __future__ import annotations\n',
                        'from __future__ import annotations\nimport hmac\n',name='openclaw-hmac-import')
    elif filename == 'mcel_backend_browser_route.py':
        text = anchored(text, 'from __future__ import annotations\n',
                        'from __future__ import annotations\nimport hmac\n', name='browser-hmac-import')
        text = anchored(text,
            '''def browser_request_authorized(handler, *, write: bool) -> bool:
    """Deny remote clients, hostile Host, cross-origin scripts and forms."""
''',
            '''def browser_request_authorized(handler, *, write: bool) -> bool:
    """Require gateway authority as well as the existing network/origin checks."""
    expected = getattr(handler.server, "ingress_capability", None)
    supplied = handler.headers.get("X-MC-Gateway-Capability", "")
    if not (isinstance(expected, str) and len(expected) >= 32 and
            isinstance(supplied, str) and hmac.compare_digest(expected, supplied)):
        return False
''',name='browser-control-session-gate')
    else:
        raise ValueError(filename)
    ast.parse(text, filename=filename)
    return text


def generate(base: Path, work: Path) -> tuple[Path, Path, dict[str, dict]]:
    baseline, candidate = work / 'baseline', work / 'candidate'
    baseline.mkdir();candidate.mkdir()
    hashes = {}
    for filename in MODULES:
        source = base / 'main_computer' / filename
        raw = source.read_bytes()
        text = raw.decode('utf-8')
        revised = make_candidate(filename, text)
        assert revised != text
        (baseline / filename).write_bytes(raw)
        (candidate / filename).write_text(revised,encoding='utf-8')
        hashes[filename] = {'baseline':hashlib.sha256(raw).hexdigest(),
                            'candidate':hashlib.sha256(revised.encode()).hexdigest()}
    return baseline, candidate, hashes


def candidate_zip(base: Path, candidate: Path, hashes: dict, path: Path) -> None:
    """ZIP is a candidate only, never promoted or applied automatically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for filename in MODULES:
            source = base / 'main_computer' / filename
            original = source.read_bytes()
            if hashlib.sha256(original).hexdigest()!=hashes[filename]['baseline']:
                raise RuntimeError('SOURCE_CHANGED_SINCE_CANDIDATE_GENERATION:'+filename)
            z.write(candidate/filename, 'main_computer/'+filename)
    with zipfile.ZipFile(path) as z:
        assert z.namelist()==['main_computer/'+name for name in MODULES]


def compose_manifest(work: Path, shared_secret: str, login_key: str | None = None) -> dict:
    login_key = login_key or secrets.token_urlsafe(40)
    if login_key == shared_secret: raise ValueError("login and internal secrets must differ")
    fixture = ROOT / 'tools' / 'mcel_security_correction_fixture.py'
    volumes = [
        {'type':'bind','source':str(work/'baseline'),'target':'/lab/baseline','read_only':True},
        {'type':'bind','source':str(work/'candidate'),'target':'/lab/candidate','read_only':True},
        {'type':'bind','source':str(fixture.resolve()),'target':'/lab/fixture.py','read_only':True},
        {'type':'bind','source':str((ROOT/'tools'/'mcel_security_session_gateway.py').resolve()),'target':'/lab/mcel_security_session_gateway.py','read_only':True},
        {'type':'bind','source':str((ROOT/'tools'/'mcel_localhost_cookie_gateway_smoke.py').resolve()),'target':'/lab/mcel_localhost_cookie_gateway_smoke.py','read_only':True},
        {'type':'bind','source':str((ROOT/'main_computer'/'viewport_routes_onlyoffice.py').resolve()),'target':'/lab/viewport_routes_onlyoffice.py','read_only':True},
    ]
    base = {'image':'python:3.12-slim','restart':'no','environment':{'MC_CORRECTION_CAPABILITY':shared_secret,'MC_CORRECTION_LOGIN_KEY':login_key},
            'volumes':volumes,'security_opt':['no-new-privileges:true'],
            'cap_drop':['ALL'],'read_only':True,'tmpfs':['/tmp:rw,noexec,nosuid,size=32m']}
    targets = {**base,'command':['python','-u','/lab/fixture.py','serve'],'networks':['sandbox']}
    probe = {**base,'command':['python','-u','/lab/fixture.py','probe'], 'network_mode':'service:targets'}
    # Bob gets the targets' *network namespace* but nothing else: different
    # filesystem, PID namespace, UID, environment, and no Docker socket.
    bob = {
        'image':'python:3.12-slim', 'restart':'no',
        'network_mode':'service:targets', 'user':'65534:65534',
        'environment':{'MC_BOB_PORT_BASE':'32410'},
        'volumes':[{'type':'bind','source':str((ROOT/'tools'/'mcel_security_bob_probe.py').resolve()),
                    'target':'/lab/bob.py','read_only':True}],
        'command':['python','-u','/lab/bob.py','run'],
        'cap_drop':['ALL'], 'security_opt':['no-new-privileges:true'],
        'read_only':True, 'tmpfs':['/tmp:rw,noexec,nosuid,size=16m'],
    }
    return {'services':{'targets':targets,'probe':probe,'bob':bob},'networks':{'sandbox':{'internal':True}}}


def execute_docker(work: Path, secret: str, login_key: str | None = None) -> int:
    if not shutil.which('docker'):
        print('MC_CORRECTION_LAB UNVERIFIED docker unavailable; production unmodified',flush=True)
        return 2
    project='mc-sec-correct-'+secrets.token_hex(4)
    file=work/'docker-compose.json'
    file.write_text(json.dumps(compose_manifest(work,secret,login_key),indent=2),encoding='utf-8')
    base=['docker','compose','-p',project,'-f',str(file)]
    outcome=2
    def command(args,timeout):
        cp=subprocess.run(args,timeout=timeout,check=False)
        if cp.returncode:
            raise RuntimeError('docker exit='+str(cp.returncode)+' action='+str(args[-2:]))
        return cp
    try:
        command(['docker','version','--format','{{.Server.Version}}'],20)
        command(base+['config','--quiet'],30)
        print('MC_CORRECTION_LAB project='+project+' docker-network=internal published-ports=0',flush=True)
        command(base+['up','-d','targets'],90)
        cid=subprocess.run(base+['ps','-q','targets'],capture_output=True,text=True,timeout=20,check=True).stdout.strip()
        if not cid:
            raise RuntimeError('target container missing')
        ports=subprocess.run(['docker','inspect','--format','{{json .HostConfig.PortBindings}}',cid],
                            capture_output=True,text=True,timeout=20,check=True)
        if json.loads(ports.stdout):
            raise RuntimeError('correction target unexpectedly publishes host ports')
        print('MC_CORRECTION_LAB no-published-host-ports PASS',flush=True)
        result=subprocess.run(base+['run','--rm','--no-deps','probe'],timeout=180,check=False)
        outcome=result.returncode
        if outcome:
            print('MC_CORRECTION_LAB candidate-probe FAIL exit='+str(outcome),flush=True)
        else:
            # Probe deliberately runs WITHOUT the shared service capability.
            bob=subprocess.run(base+['run','--rm','--no-deps','bob'],timeout=180,check=False)
            outcome=bob.returncode
            if outcome:
                print('MC_CORRECTION_LAB bob-adversarial-probe FAIL exit='+str(outcome),flush=True)
            else:
                print('MC_CORRECTION_LAB bob-adversarial-probe PASS',flush=True)
    except Exception as exc:
        print('MC_CORRECTION_LAB UNVERIFIED '+type(exc).__name__+': '+str(exc)[:300],flush=True)
        outcome=2
    finally:
        try:
            command(base+['down','-v','--remove-orphans'],120)
            print('MC_CORRECTION_LAB docker-cleanup PASS',flush=True)
        except Exception as exc:
            print('MC_CORRECTION_LAB docker-cleanup FAIL '+str(exc)[:180],flush=True)
            print('MC_CORRECTION_LAB RECOVERY_PROJECT '+project,flush=True)
            outcome=2
    return outcome


def execute_local(work: Path, secret: str, login_key: str | None = None) -> int:
    login_key = login_key or secrets.token_urlsafe(40)
    """Developer-only real socket verification without Docker; NOT Docker proof."""
    env=os.environ.copy();env.update({'MC_CORRECTION_CAPABILITY':secret,'MC_CORRECTION_LOGIN_KEY':login_key,'MC_CORRECTION_ROOT':str(work),
                                     'MC_CORRECTION_PORT_BASE':str(secrets.randbelow(15000)+30000),
                                     'MC_CORRECTION_SESSION_FILE':str(work/'sessions.json')})
    fixture=ROOT/'tools'/'mcel_security_correction_fixture.py'
    # Source-backed ONLYOFFICE status callback for the same-session lab.
    (work/'viewport_routes_onlyoffice.py').write_bytes((ROOT/'main_computer'/'viewport_routes_onlyoffice.py').read_bytes())
    proc=subprocess.Popen([sys.executable,'-u',str(fixture),'serve'],env=env,
                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    try:
        time.sleep(0.8)
        if proc.poll() is not None:
            print('MC_CORRECTION_LAB local-target-exited '+(proc.stdout.read() if proc.stdout else '')[:300])
            return 2
        result=subprocess.run([sys.executable,'-u',str(fixture),'probe'],env=env,timeout=60,check=False)
        if result.returncode:
            return result.returncode
        # Local test has its own fresh *credentialless* process, though not a
        # distinct OS user. Docker has a separate PID/filesystem namespace.
        bob_env={k:v for k,v in env.items() if k not in {'MC_CORRECTION_CAPABILITY','MC_CORRECTION_LOGIN_KEY','MC_CORRECTION_ROOT'}}
        bob_env['MC_BOB_PORT_BASE']=env['MC_CORRECTION_PORT_BASE']
        attack=ROOT/'tools'/'mcel_security_bob_probe.py'
        return subprocess.run([sys.executable,'-u',str(attack),'run'],env=bob_env,timeout=120,check=False).returncode
    finally:
        proc.terminate()
        try:proc.wait(timeout=4)
        except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
        if proc.stdout:proc.stdout.close()


def experiment(*, mode: str='docker', root: Path=ROOT) -> int:
    secret=secrets.token_urlsafe(40)
    login_key=secrets.token_urlsafe(40)
    with tempfile.TemporaryDirectory(prefix='mc-correction-dryrun-') as temp:
        work=Path(temp)
        try:
            baseline,candidate,hashes=generate(root,work)
            print('MC_CORRECTION_LAB five-source-candidates-prepared PASS',flush=True)
        except Exception as exc:
            print('MC_CORRECTION_LAB candidate-preparation FAIL '+type(exc).__name__+': '+str(exc)[:240],flush=True)
            return 1
        outcome=execute_docker(work,secret,login_key) if mode=='docker' else execute_local(work,secret,login_key)
        if outcome==0:
            # Retain a candidate for review, not production promotion.
            # Reports and JSON are generated automatically, not supplied as
            # standalone downloaded JSON files.
            runtime = root / 'runtime'
            # In installed MC runtime is a directory. Some source-only ZIPs
            # include an empty file placeholder named runtime; don't edit it.
            base_reports = runtime / 'reports' if not runtime.is_file() else Path(tempfile.gettempdir()) / 'mc-security-correction-reports'
            reports=base_reports/'mcel-security-correction-lab'
            reports.mkdir(parents=True,exist_ok=True)
            out=reports/'candidate-five-ingress-modules.zip'
            candidate_zip(root,candidate,hashes,out)
            (reports/'last-result.json').write_text(json.dumps({'status':'ISOLATED_CANDIDATE_VERIFIED_IN_'+mode.upper(),
                'production_modified':False,'promotion_eligible':False,'limitations':['selected handler methods, not full application runtime','no live MC cutover','no separate Windows user',
                'no real WS/SSE','credentialless Docker attacker is not a separate Windows SID','HTTPS gateway is simulated with HTTP in Docker; Windows TLS acceptance remains separate','ONLYOFFICE status callback tested; real native Document Server callback and save are separate'], 'source_sha256':hashes},indent=2),encoding='utf-8')
            print('MC_CORRECTION_LAB isolated-candidate-verified-in-'+mode+' PASS production-unchanged; not-promotable; candidate='+str(out),flush=True)
        else:
            print('MC_CORRECTION_LAB blocked-no-promotion outcome='+str(outcome),flush=True)
        return outcome


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['run'],nargs='?',default='run')
    parser.add_argument('--mode',choices=['docker','local'],default='docker')
    args=parser.parse_args(argv)
    return experiment(mode=args.mode)


if __name__=='__main__':
    raise SystemExit(main())
