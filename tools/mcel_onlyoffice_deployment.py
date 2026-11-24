"""Deploy the source-matched ONLYOFFICE candidate through the normal Windows launcher.

Promotion and rollback require all application lanes stopped and editors closed.
Neither transition deletes workbook data, journal state, credentials or Docker volumes.
"""
from __future__ import annotations

import argparse
import http.client
import json
import ntpath
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
from types import SimpleNamespace
from contextlib import contextmanager

from main_computer import onlyoffice_deployment as d
from main_computer.auth_hostnames import APP_PORTS, HOSTS
from tools import mcel_onlyoffice_cutover as certified

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_FILES = ('main_computer/onlyoffice_deployment.py', 'tools/mcel_onlyoffice_deployment.py',
                'scripts/main-computer-start-stop.ps1', 'scripts/open-main-computer-browser.ps1',
                'main_computer/app_control.py', 'main_computer/service_supervisor.py',
                'main_computer/auth_bootstrap_service_deployment.py', 'main_computer/web/graphical.html',
                'docker-compose.onlyoffice.yml', 'tools/onlyoffice/onlyoffice-control.ps1',
                'main_computer/config.py', 'main_computer/main_log_client.py',
                'main_computer/main_log_service.py', 'main_computer/heartbeat.py',
                'main_computer/openclaw_bridge.py', 'start_v2.bat', 'stop_v2.bat')
ALL_FILES = tuple(dict.fromkeys((*certified.FILES, *DEPLOY_FILES)))
PORTS = (8765,8766,8767,28865,28866,28867,38865,38866,38867,8875)


def say(name, value):
    print(f'MC_OO_DEPLOY {name} {value}', flush=True)


def require(value, name):
    if not value:
        raise RuntimeError(name)


def source_evidence(root, *, actions=('gateway','live')):
    expected = d.file_hashes(root, certified.FILES)
    for action in actions:
        rows = []
        for p in sorted((root/'runtime/reports/mcel-onlyoffice-cutover').glob('*/summary.json'), reverse=True):
            try: row = json.loads(p.read_text(encoding='utf-8'))
            except (OSError, ValueError): continue
            if row.get('action') == action:
                rows.append(row); break
        require(rows and rows[0].get('verdict') == 'PASS_ISOLATED_'+action.upper()
                and rows[0].get('fingerprint') == expected, 'SOURCE_MATCHED_'+action.upper()+'_EVIDENCE_REQUIRED')


def listening(port):
    with socket.socket() as sock:
        sock.settimeout(.4)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def quiescent():
    busy = sorted(set(process_listeners()) | {p for p in PORTS if listening(p)})
    require(not busy, 'STOP_ALL_MC_LANES_FIRST_PORTS_'+','.join(map(str,busy)))


def process_listeners():
    # Verify actual Windows listener ownership, not merely a stale PID file.
    code = ('$ErrorActionPreference="Stop"; '
            '@(Get-NetTCPConnection -State Listen | Where-Object {$_.LocalPort -in '+','.join(map(str,PORTS))+'} '
            '| Select-Object LocalPort,OwningProcess) | ConvertTo-Json -Compress')
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',code],capture_output=True,text=True,timeout=45)
    require(result.returncode==0, 'WINDOWS_LISTENER_INVENTORY_FAILED')
    data=json.loads(result.stdout or '[]'); data=[data] if isinstance(data,dict) else data
    owners={}
    for row in data:
        port,pid=int(row['LocalPort']),int(row['OwningProcess'])
        require(port not in owners or owners[port]==pid,'AMBIGUOUS_LISTENER_OWNERSHIP_'+str(port))
        owners[port]=pid
    return owners


def windows_argv(command):
    """Parse Windows quoting without treating the command as executable code."""
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    parse = shell.CommandLineToArgvW
    parse.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    free = kernel.LocalFree
    free.argtypes = (ctypes.c_void_p,)
    free.restype = ctypes.c_void_p
    count = ctypes.c_int()
    ptr = parse(command, ctypes.byref(count))
    if not ptr:
        raise OSError('WINDOWS_COMMAND_LINE_PARSE_FAILED')
    try:
        return [ptr[i] for i in range(count.value)]
    finally:
        free(ctypes.cast(ptr, ctypes.c_void_p))


def process_inventory():
    code = ('$ErrorActionPreference="Stop"; @(Get-CimInstance Win32_Process | '
            'Where-Object {$_.Name -match "^python(w)?\\.exe$"} | '
            'Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine,'
            '@{Name="Created";Expression={$_.CreationDate.ToUniversalTime().Ticks}}) | ConvertTo-Json -Compress')
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',code],
                            capture_output=True,text=True,timeout=45)
    require(result.returncode == 0, 'WINDOWS_PROCESS_INVENTORY_FAILED')
    rows = json.loads(result.stdout or '[]'); rows = [rows] if isinstance(rows,dict) else rows
    return {int(row['ProcessId']): {**row, 'argv': windows_argv(row['CommandLine'])
            if row.get('CommandLine') else []} for row in rows}


def supervised_listener(child, supervisor_pid, owner_pid, processes, module, root):
    """Accept the recorded process or its verified Windows venv redirector child.

    Do not accept arbitrary descendants, commandless records, PID reuse across
    generations, or an unrelated listener on the expected port.
    """
    try:
        if child['state'] != 'running' or not owner_pid:
            return False
        pid = int(child['pid']); supervisor_pid = int(supervisor_pid)
        command = child['command']; launcher = processes[pid]; owner = processes[owner_pid]
        supervisor = processes[supervisor_pid]
        if (not isinstance(command,list) or command[1:3] != ['-m', module]
                or command[3] != '--root' or not d.same_path(command[4], root)):
            return False
        def matches(row):
            return (ntpath.basename(row['ExecutablePath']).lower() in {'python.exe','pythonw.exe'}
                    and row['argv'][1:] == command[1:] and len(row['argv']) > 1)
        if (int(launcher['ParentProcessId']) != supervisor_pid
                or int(launcher['Created']) < int(supervisor['Created'])
                or not matches(launcher) or not matches(owner)):
            return False
        if owner_pid == pid:
            return True
        # CPython's Windows virtual-environment redirector owns the child
        # process, whose command is the same except for the executable.
        return (ntpath.basename(ntpath.dirname(launcher['ExecutablePath'])).lower() == 'scripts'
                and int(owner['ParentProcessId']) == pid
                and int(owner['Created']) >= int(launcher['Created'])
                and ntpath.normcase(owner['ExecutablePath']) != ntpath.normcase(launcher['ExecutablePath']))
    except (KeyError, TypeError, ValueError, IndexError, OSError):
        return False


def no_supervisors(roots):
    # A stopped listener is not enough when its supervisor can restart it.
    code = ('$ErrorActionPreference="Stop"; @(Get-CimInstance Win32_Process | '
            'Where-Object {$_.Name -match "^python(w)?\\.exe$"} | '
            'Select-Object ProcessId,CommandLine) | ConvertTo-Json -Compress')
    r=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',code],capture_output=True,text=True,timeout=45)
    require(r.returncode==0, 'WINDOWS_PROCESS_INVENTORY_FAILED')
    rows=json.loads(r.stdout or '[]'); rows=[rows] if isinstance(rows,dict) else rows
    for row in rows:
        cmd=(row.get('CommandLine') or '').lower()
        if ('main_computer.app_control' in cmd and 'bootstrap' in cmd) or ('main_computer.service_supervisor' in cmd and 'supervise' in cmd):
            raise RuntimeError('STOP_ALL_MC_SUPERVISORS_FIRST')
    quiescent()


def private_write(state, name, value):
    from main_computer.auth_bootstrap import _private_folder, _replace_private
    _private_folder(state)
    _replace_private(state/name, (json.dumps(value, indent=2)+'\n').encode())


@contextmanager
def transition_lock(state):
    from main_computer.auth_bootstrap import _private_folder
    _private_folder(state)
    path=state/'onlyoffice-deployment.lock'
    with path.open('a+b') as lock:
        lock.seek(0,2)
        if lock.tell()==0: lock.write(b'0'); lock.flush()
        lock.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            try: yield
            finally: lock.seek(0); msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            try: yield
            finally: fcntl.flock(lock,fcntl.LOCK_UN)


def prepare_secrets(state):
    if not (state/d.SECRET_NAME).exists():
        private_write(state,d.SECRET_NAME,{k:secrets.token_hex(32) for k in ('MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET','MAIN_COMPUTER_MAIN_LOG_TOKEN')})
    return d.secret_environment(state)


def docker_env(state, policy):
    env={**os.environ,**d.secret_environment(state)}
    env.update({'MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME':'main-computer-onlyoffice-documentserver',
                'MAIN_COMPUTER_ONLYOFFICE_PORT':'18085','MAIN_COMPUTER_ONLYOFFICE_HTTPS_PORT':'18086',
                'MAIN_COMPUTER_ONLYOFFICE_TLS_CERT_PATH':str(state/'tls-cert-localhost.pem'),
                'MAIN_COMPUTER_ONLYOFFICE_TLS_KEY_PATH':str(state/'tls-key-localhost.pem'),
                'MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED':'true','MAIN_COMPUTER_ONLYOFFICE_ALLOW_PRIVATE_IP_ADDRESS':'true',
                'MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS':'false',
                'MAIN_COMPUTER_ONLYOFFICE_IMAGE_TAG':policy.get('image_tag','latest')})
    return env


def compose(root, state, policy, args, *, hardened=True, capture=False):
    env=docker_env(state,policy)
    cmd=['docker','compose','--project-name',d.PROJECT,'-f',str(root/'docker-compose.onlyoffice.yml')]
    if hardened: cmd+=['-f',str(root/'docker-compose.onlyoffice.hardened.yml')]
    else: env.update(MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED='false',MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET='',MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS='true')
    # Never print compose config (contains JWT). Capture errors privately and
    # report only exit status, not Docker output that might include secrets.
    result=subprocess.run([*cmd,*args],cwd=root,env=env,capture_output=True,text=True,timeout=600)
    require(result.returncode==0,'DOCKER_COMPOSE_FAILED_'+str(result.returncode))
    return result.stdout if capture else None


def docker_inspect(container):
    r=subprocess.run(['docker','inspect',container],capture_output=True,text=True,timeout=30)
    require(r.returncode==0,'DOCUMENTSERVER_INSPECT_FAILED')
    return json.loads(r.stdout)[0]


def image_id(tag):
    r=subprocess.run(['docker','image','inspect','onlyoffice/documentserver:'+tag,'--format','{{.Id}}'],capture_output=True,text=True,timeout=30)
    require(r.returncode==0 and r.stdout.strip().startswith('sha256:'),'LOCAL_DOCUMENTSERVER_IMAGE_REQUIRED')
    return r.stdout.strip()


def docs_ready(state, policy):
    row=docker_inspect('main-computer-onlyoffice-documentserver')
    cfg=dict(v.split('=',1) for v in row['Config']['Env'] if '=' in v)
    labels=row['Config'].get('Labels') or {}
    require(labels.get('com.docker.compose.project')==d.PROJECT and labels.get('com.docker.compose.service')=='onlyoffice','DOCUMENTSERVER_COMPOSE_OWNERSHIP_MISMATCH')
    require(row['State']['Running'] and row['Image']==policy['image_id'] and cfg.get('JWT_ENABLED')=='true'
            and secrets.compare_digest(cfg.get('JWT_SECRET',''),d.secret_environment(state)['MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET'])
            and cfg.get('ALLOW_META_IP_ADDRESS')=='false','DOCUMENTSERVER_RUNTIME_POLICY_MISMATCH')
    ports=row['NetworkSettings']['Ports'].get('80/tcp') or []
    require(ports==[{'HostIp':'127.0.0.1','HostPort':'18085'}],'DOCUMENTSERVER_BINDING_MISMATCH')
    proxy=subprocess.run(['docker','ps','--filter','label=com.docker.compose.project='+d.PROJECT,
                          '--filter','label=com.docker.compose.service=docs-https','--format','{{.ID}}'],
                          capture_output=True,text=True,timeout=30)
    ids=proxy.stdout.splitlines()
    require(proxy.returncode==0 and len(ids)==1,'EXACTLY_ONE_DOCS_HTTPS_OWNER_REQUIRED')
    proxy_row=docker_inspect(ids[0])
    require(proxy_row['State']['Running'] and proxy_row['NetworkSettings']['Ports'].get('443/tcp')==[{'HostIp':'127.0.0.1','HostPort':'18086'}], 'DOCS_HTTPS_BINDING_MISMATCH')
    code,_,body=certified.request(HOSTS['onlyoffice'],d.DOCS_PORT,state/'tls-cert-localhost.pem','/healthcheck')
    require(code==200 and body.strip()==b'true','DOCUMENTSERVER_HTTPS_NOT_READY')


def configure_docker(state, root, action):
    policy=d.read_policy(state)
    require(policy and policy['mode']=='hardened','HARDENED_DEPLOYMENT_NOT_SELECTED')
    lane=d.lane_for(policy,root); d.verify_source(policy,root)
    if action=='start':
        lane_ports=[APP_PORTS[lane]+i for i in range(3)]
        if lane==policy['primary']: lane_ports.append(8875)
        require(not (set(lane_ports)&set(process_listeners())), 'LANE_LISTENERS_NOT_RELEASED_'+lane)
    if action=='start' and lane==policy['primary']:
        require(image_id(policy['image_tag'])==policy['image_id'],'DOCUMENTSERVER_IMAGE_CHANGED_RECERTIFY')
        # Certificate preflight ran before the supervisor was stopped. Docker
        # mounts the same private cert files; existing named volumes survive.
        compose(root,state,policy,['up','-d','--pull','never'])
        deadline=time.monotonic()+300
        while True:
            try: docs_ready(state,policy); break
            except (OSError,RuntimeError,http.client.HTTPException):
                if time.monotonic()>=deadline: raise
                time.sleep(3)
    elif action in {'start','status'}:
        docs_ready(state,policy)
    elif action=='stop':
        raise RuntimeError('SHARED_DOCUMENTSERVER_STOP_REQUIRES_EXPLICIT_ROLLBACK')
    say('shared-documentserver','PASS')


def promote(state, root, opts):
    require(opts.authorize_cutover,'EXPLICIT_CUTOVER_AUTHORIZATION_REQUIRED')
    existing=d.read_policy(state)
    require(not existing or existing['mode']=='legacy','ALREADY_HARDENED_ROLLBACK_OR_VERIFY')
    roots={opts.primary:str(root)}
    for lane in ('debug','safe'):
        path=getattr(opts,lane+'_root')
        if path: roots[lane]=str(path.resolve())
    no_supervisors(roots)
    source_evidence(root)
    fp=d.file_hashes(root,ALL_FILES)
    for location in roots.values():
        require(d.file_hashes(Path(location),ALL_FILES)==fp,'REGISTERED_LANE_SOURCE_MISMATCH')
    require(len({os.path.normcase(p) for p in roots.values()})==len(roots),'DUPLICATE_INSTALLATION_ROOT')
    locations=[Path(p).resolve() for p in roots.values()]
    require(not any(a in b.parents for a in locations for b in locations if a!=b),'NESTED_DEPLOYMENT_ROOTS_NOT_ALLOWED')
    # Preflight in a fresh process: auth_bootstrap chooses its hostname on import.
    env={**os.environ,'MAIN_COMPUTER_HARDENED_CANDIDATE':'1','MAIN_COMPUTER_PRIMARY_OWNER':opts.primary,
         'MAIN_COMPUTER_INSTALLATION_MODE':opts.primary,'MAIN_COMPUTER_AUTH_BOOTSTRAP_PORT':'8875','MAIN_COMPUTER_CONTROL_PORT':'8765'}
    r=subprocess.run([sys.executable,'-m','main_computer.auth_bootstrap_service','--root',str(root),'preflight'],cwd=root,env=env,timeout=300)
    require(r.returncode==0,'HARDENED_CERTIFICATE_PREFLIGHT_FAILED')
    prepare_secrets(state)
    selected={'schema':1,'mode':'hardened','primary':opts.primary,'roots':roots,'fingerprint':fp,
              'image_tag':opts.tag,'image_id':image_id(opts.tag)}
    # Commit only after all preconditions. Actual startup is the ordinary launcher.
    no_supervisors(roots)
    private_write(state,d.POLICY_NAME,selected)
    say('promotion','HARDENED_SELECTED_START_REQUIRED')


def rollback(state, root, opts):
    require(opts.authorize_rollback,'EXPLICIT_ROLLBACK_AUTHORIZATION_REQUIRED')
    policy=d.read_policy(state)
    require(policy and policy['mode']=='hardened','NO_HARDENED_SELECTION_TO_ROLL_BACK')
    require(d.lane_for(policy,root)==policy['primary'],'ROLLBACK_FROM_PRIMARY_ROOT_REQUIRED')
    no_supervisors(policy['roots'])
    # Retain all data and named volumes. Do not use down -v. If Docker fails,
    # retain hardened policy rather than silently enable the incumbent.
    require(image_id(policy['image_tag'])==policy['image_id'],'DOCUMENTSERVER_IMAGE_CHANGED_REFUSE_ROLLBACK')
    compose(root,state,policy,['stop','docs-https'])
    compose(root,state,policy,['up','-d','--pull','never','onlyoffice'],hardened=False)
    private_write(state,d.POLICY_NAME,{'schema':1,'mode':'legacy'})
    say('rollback','LEGACY_SELECTED_START_REQUIRED')


def runtime_source_fingerprints(policy):
    """Capture current source per lane, not the historical promotion receipt."""
    result = {}
    for lane, location in policy['roots'].items():
        path = Path(location)
        d.verify_source(policy, path)
        result[lane] = d.file_hashes(path, ALL_FILES)
    return result


def runtime_receipt_matches(report, policy, fingerprints, listeners):
    return (report.get('source_fingerprints') == fingerprints
            and report.get('fingerprint') == fingerprints[policy['primary']]
            and report.get('roots') == policy['roots']
            and report.get('verdict') in {'PASS_NORMAL_STARTUP_PRIMARY_ONLY', 'PASS_NORMAL_STARTUP_REGISTERED_LANES'}
            and set(report.get('listener_pids', {})) == {str(p) for p in ({8875} | {APP_PORTS[lane] for lane in policy['roots']})}
            and all(listeners.get(int(k)) == v for k, v in report['listener_pids'].items()))


def verify(state, root):
    policy=d.read_policy(state); require(policy and policy['mode']=='hardened','HARDENED_DEPLOYMENT_NOT_SELECTED')
    require(d.lane_for(policy,root)==policy['primary'],'VERIFY_FROM_PRIMARY_ROOT_REQUIRED')
    fingerprints=runtime_source_fingerprints(policy)
    docs_ready(state,policy); say('shared-documentserver','PASS')
    expected_ports={8875}|{APP_PORTS[lane] for lane in policy['roots']}
    deadline=time.monotonic()+180
    while True:
        owners=process_listeners()
        if expected_ports<=set(owners) or time.monotonic()>=deadline: break
        time.sleep(3)
    require(expected_ports<=set(owners),'NORMAL_STARTUP_LISTENERS_NOT_READY')
    for lane in {'debug','safe'}-set(policy['roots']):
        require(APP_PORTS[lane] not in owners,'UNREGISTERED_LANE_LISTENING_'+lane)
        require(certified.request(HOSTS[lane],8875,state/'tls-cert-localhost.pem','/api/auth/bootstrap/session')[0]==403,'UNREGISTERED_HOST_NOT_DENIED_'+lane)
    # Match running normal supervisor state and actual listener PID, not an
    # isolated backend spawned by a smoke test.
    processes=process_inventory()
    for lane, location in policy['roots'].items():
        p=APP_PORTS[lane]; r=Path(location)
        info=json.loads((r/'runtime/service_supervisor/state.json').read_text(encoding='utf-8-sig'))
        children=info['children']; app=children['app']; auth=children.get('auth')
        require(supervised_listener(app,info['service']['pid'],owners.get(p),processes,
                    'main_computer.app_control',r),'NORMAL_SUPERVISOR_APP_NOT_LISTENING_'+lane)
        require(bool(info['auth_supervised'])==(lane==policy['primary']),'SHARED_AUTH_OWNERSHIP_MISMATCH_'+lane)
        if lane==policy['primary']:
            require(auth and supervised_listener(auth,info['service']['pid'],owners.get(8875),processes,
                        'main_computer.auth_bootstrap_service_deployment',r),'NORMAL_AUTH_SUPERVISOR_NOT_LISTENING')
        else: require(not auth or auth['state']!='running','SECOND_AUTH_OWNER_'+lane)
        conn=http.client.HTTPConnection('127.0.0.1',p,timeout=30)
        try:
            conn.request('POST','/api/applications/onlyoffice/status',b'{}',{'Content-Type':'application/json'})
            require(conn.getresponse().status==401,'DIRECT_BACKEND_NOT_DENIED_'+lane)
        finally: conn.close()
        say('normal-supervisor-'+lane,'PASS')
    # Use the real permanent proof for a disposable session; do not log the
    # cookie, revoke only the test session in finally. Real Edge uses its
    # normal CurrentUser trust, not an SPKI pin or ignore-certificate-errors.
    from main_computer.auth_bootstrap import BootstrapAuthority, proof_file_path
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    cert=state/'tls-cert-localhost.pem'; primary=HOSTS[policy['primary']]
    cookie=certified.auth_cookie(SimpleNamespace(server_port=8875),proof_file_path(state),cert,host=primary)
    name, token=cookie.split('=',1)
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context(ignore_https_errors=False)
                for lane in policy['roots']:
                    host=HOSTS[lane]; origin=f'https://{host}:8875'
                    if host != primary:
                        status, headers,_=certified.request(primary,8875,cert,'/auth/handoff/start?target='+host,cookie=cookie)
                        require(status==303,'LIVE_HANDOFF_ISSUE_FAILED_'+lane)
                        url=urlsplit(headers['Location'])
                        status,headers,_=certified.request(host,8875,cert,url.path+'?'+url.query)
                        require(status==303 and headers.get('Set-Cookie','').split(';')[0]==cookie,'LIVE_HANDOFF_REDEEM_FAILED_'+lane)
                    context.add_cookies([{'name':name,'value':token,'url':origin,'secure':True,'httpOnly':True,'sameSite':'Strict'}])
                    page=context.new_page()
                    response=page.goto(origin+'/api/auth/bootstrap/session',wait_until='domcontentloaded',timeout=90000)
                    require(response and response.status==200 and response.json()['authenticated'],'EDGE_CURRENTUSER_AUTH_FAILED_'+lane)
                    data=page.evaluate("""async () => {const r=await fetch('/api/applications/onlyoffice/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({probe:true,timeout_s:10})});return {status:r.status,body:await r.json()};}""")
                    require(data['status']==200 and data['body'].get('jwt_enabled') and data['body'].get('jwt_configured') and data['body'].get('public_url')==d.DOCS_ORIGIN and data['body'].get('callback_base_url')==f'http://host.docker.internal:{APP_PORTS[lane]}' and data['body'].get('api_reachable') is True and data['body'].get('server_probe',{}).get('url')=='http://127.0.0.1:18085/web-apps/apps/api/documents/api.js','LIVE_GATEWAY_LANE_STATUS_MISMATCH_'+lane)
                    response=page.goto(origin+'/graphical',wait_until='domcontentloaded',timeout=90000)
                    require(response and response.status==200 and urlsplit(page.url).path=='/graphical','NORMAL_GRAPHICAL_ROUTE_FAILED_'+lane)
                    # The canonical shell must actually mount its native child.
                    # A top-level HTTP 200 alone missed the framing regression.
                    response=page.goto(origin+'/applications/code-editor',wait_until='domcontentloaded',timeout=90000)
                    require(response and response.status==200,'NORMAL_CODE_EDITOR_SHELL_FAILED_'+lane)
                    try:
                        page.frame_locator('#mcel-shell-frame').locator('#mcel-workbench').wait_for(state='visible',timeout=120000)
                    except BrowserTimeout:
                        raise RuntimeError('NORMAL_CODE_EDITOR_CHILD_NOT_VISIBLE_'+lane) from None
                    say('edge-native-code-editor-frame-'+lane,'PASS')
                    response=page.goto(origin+'/applications/onlyoffice',wait_until='domcontentloaded',timeout=90000)
                    require(response and response.status==200,'NORMAL_ONLYOFFICE_SHELL_FAILED_'+lane)
                    try:
                        page.frame_locator('#mcel-shell-frame').locator('#onlyoffice-app').wait_for(state='visible',timeout=120000)
                    except BrowserTimeout:
                        raise RuntimeError('NORMAL_ONLYOFFICE_CHILD_NOT_VISIBLE_'+lane) from None
                    say('edge-native-onlyoffice-frame-'+lane,'PASS')
                    page.close(); say('edge-trusted-route-'+lane,'PASS')
                page=context.new_page(); response=page.goto(d.DOCS_ORIGIN+'/healthcheck',timeout=90000)
                require(response and response.status==200,'EDGE_DOCUMENTSERVER_TRUST_FAILED')
                require(not any(c['name']==name for c in context.cookies([d.DOCS_ORIGIN])),'MC_COOKIE_LEAK_TO_DOCS_ORIGIN')
                say('edge-currentuser-docs-trust','PASS')
            finally: browser.close()
    finally: BootstrapAuthority(state).sessions.revoke(token)
    final_owners=process_listeners()
    require(all(final_owners.get(p)==owners[p] for p in expected_ports),
            'LISTENER_OWNERSHIP_CHANGED_DURING_VERIFY')
    verdict='PASS_NORMAL_STARTUP_REGISTERED_LANES' if len(policy['roots'])>1 else 'PASS_NORMAL_STARTUP_PRIMARY_ONLY'
    require(runtime_source_fingerprints(policy)==fingerprints,'SOURCE_CHANGED_DURING_RUNTIME_VERIFY')
    report={'verdict':verdict,'fingerprint':fingerprints[policy['primary']],
            'source_fingerprints':fingerprints,'roots':policy['roots'],
            'listener_pids':{str(port):owners[port] for port in sorted(expected_ports)},
            'windows_currentuser_trust_tested':True,'real_windows_multi_lane_tested':len(policy['roots'])>1,
            'timestamp':time.time()}
    private_write(state,'onlyoffice-deployment-verification.json',report)
    say('verdict',verdict)


def migrate_frame_fix(state, root, opts):
    """Explicit, bounded update of a selected deployment, never arbitrary re-pin."""
    require(opts.authorize_cutover,'EXPLICIT_CUTOVER_AUTHORIZATION_REQUIRED')
    policy=d.read_policy(state)
    require(policy and policy['mode']=='hardened','HARDENED_DEPLOYMENT_NOT_SELECTED')
    require(d.lane_for(policy,root)==policy['primary'],'MIGRATE_FROM_PRIMARY_ROOT_REQUIRED')
    no_supervisors(policy['roots'])
    manifest=json.loads((root/'tools/onlyoffice-deployment-frame-fix.json').read_text(encoding='utf-8'))
    allowed={'main_computer/auth_bootstrap_service_deployment.py',
             'scripts/main-computer-start-stop.ps1','tools/mcel_onlyoffice_deployment.py'}
    require(set(manifest)=={'previous','corrected'} and set(manifest['previous'])==allowed
            and set(manifest['corrected'])==allowed,'INVALID_FRAME_FIX_MIGRATION_MANIFEST')
    old=policy['fingerprint']
    require(set(old)==set(ALL_FILES),'DEPLOYMENT_FINGERPRINT_INVENTORY_MISMATCH')
    previous=manifest['previous']; corrected=manifest['corrected']
    require(all(old.get(k)==v for k,v in previous.items()) or
            all(old.get(k)==v for k,v in corrected.items()),'UNRECOGNIZED_FRAME_FIX_BASELINE')
    expected={**old,**corrected}
    for location in policy['roots'].values():
        require(d.file_hashes(Path(location),ALL_FILES)==expected,'FRAME_FIX_SOURCE_MISMATCH')
    source_evidence(root) # core gateway/editor bytes remain unchanged
    no_supervisors(policy['roots'])
    private_write(state,d.POLICY_NAME,{**policy,'fingerprint':expected})
    say('migration','HARDENED_FRAME_FIX_SELECTED_START_REQUIRED')


def migrate_localhost(state, root, opts):
    """Move the selected deployment to normal-browser localhost origins only."""
    require(opts.authorize_cutover,'EXPLICIT_CUTOVER_AUTHORIZATION_REQUIRED')
    policy=d.read_policy(state)
    require(policy and policy['mode']=='hardened','HARDENED_DEPLOYMENT_NOT_SELECTED')
    require(d.lane_for(policy,root)==policy['primary'],'MIGRATE_FROM_PRIMARY_ROOT_REQUIRED')
    no_supervisors(policy['roots'])
    manifest=json.loads((root/'tools/onlyoffice-deployment-localhost.json').read_text(encoding='utf-8'))
    require(set(manifest)=={'previous','intermediate','corrected'},'INVALID_LOCALHOST_MIGRATION_MANIFEST')
    old=policy['fingerprint']; previous=manifest['previous']; corrected=manifest['corrected']; intermediate=manifest['intermediate']
    require(set(old)==set(ALL_FILES) and set(previous)==set(corrected)==set(intermediate) and bool(previous)
            and set(previous)<=set(ALL_FILES),'LOCALHOST_FINGERPRINT_INVENTORY_MISMATCH')
    require(any(all(old.get(k)==v for k,v in baseline.items())
                for baseline in (previous,intermediate,corrected)),'UNRECOGNIZED_LOCALHOST_BASELINE')
    expected={**old,**corrected}
    for location in policy['roots'].values():
        require(d.file_hashes(Path(location),ALL_FILES)==expected,'LOCALHOST_SOURCE_MISMATCH')
    # Trust is a prerequisite, not a final step. Run in a fresh process so
    # import-time hostname selection sees the candidate environment. No gateway
    # or Docker certification is dispatched until CurrentUser trust succeeds.
    env={**os.environ,'MAIN_COMPUTER_HARDENED_CANDIDATE':'1','MAIN_COMPUTER_PRIMARY_OWNER':policy['primary'],
         'MAIN_COMPUTER_INSTALLATION_MODE':policy['primary'],'MAIN_COMPUTER_AUTH_BOOTSTRAP_PORT':'8875',
         'MAIN_COMPUTER_CONTROL_PORT':'8765'}
    say('localhost-preflight','CERTIFICATE_TRUST_REQUIRED (approve Windows certificate prompt if shown)')
    r=subprocess.run([sys.executable,'-m','main_computer.auth_bootstrap_service','--root',str(root),'preflight'],cwd=root,env=env,timeout=300)
    require(r.returncode==0,'LOCALHOST_CERTIFICATE_PREFLIGHT_FAILED')
    say('localhost-preflight','CERTIFICATE_TRUST_PASS')
    require(image_id(policy['image_tag'])==policy['image_id'],'DOCUMENTSERVER_IMAGE_CHANGED_RECERTIFY')
    # Hostname, certificate and JS origin policy changed. Never reuse .local evidence.
    # Reuse current source-matched successes, including a previous attempt that
    # completed certification but was blocked by certificate trust.
    for action in ('gateway','live'):
        try:
            source_evidence(root, actions=(action,))
        except RuntimeError:
            say('localhost-certification',action)
            r=subprocess.run([sys.executable,'-m','tools.mcel_onlyoffice_cutover',action,'--tag',policy['image_tag']],cwd=root)
            require(r.returncode==0,'LOCALHOST_'+action.upper()+'_CERTIFICATION_FAILED')
            source_evidence(root, actions=(action,))
        else:
            say('localhost-certification',action+' PASS_SOURCE_MATCHED_REUSED')
    source_evidence(root)
    require(image_id(policy['image_tag'])==policy['image_id'],'DOCUMENTSERVER_IMAGE_CHANGED_RECERTIFY')
    # New certificate filenames preserve the previous certificate and force the
    # shared Docker HTTPS proxy to adopt the new bind mounts on normal startup.
    no_supervisors(policy['roots'])
    for location in policy['roots'].values():
        require(d.file_hashes(Path(location),ALL_FILES)==expected,'LOCALHOST_SOURCE_CHANGED_DURING_CERTIFICATION')
    private_write(state,d.POLICY_NAME,{**policy,'fingerprint':expected})
    say('migration','HARDENED_LOCALHOST_SELECTED_START_REQUIRED')


def migrate_large_html(state, root, opts):
    """Rebind only the exact localhost response-budget correction."""
    require(opts.authorize_cutover,'EXPLICIT_CUTOVER_AUTHORIZATION_REQUIRED')
    policy=d.read_policy(state)
    require(policy and policy['mode']=='hardened','HARDENED_DEPLOYMENT_NOT_SELECTED')
    require(d.lane_for(policy,root)==policy['primary'],'MIGRATE_FROM_PRIMARY_ROOT_REQUIRED')
    no_supervisors(policy['roots'])
    manifest=json.loads((root/'tools/onlyoffice-deployment-large-html.json').read_text(encoding='utf-8'))
    allowed={'main_computer/auth_bootstrap.py','tools/mcel_onlyoffice_deployment.py'}
    require(set(manifest)=={'previous','corrected'} and set(manifest['previous'])==set(manifest['corrected'])==allowed,'INVALID_LARGE_HTML_MIGRATION_MANIFEST')
    old=policy['fingerprint']; previous=manifest['previous']; corrected=manifest['corrected']
    require(set(old)==set(ALL_FILES),'DEPLOYMENT_FINGERPRINT_INVENTORY_MISMATCH')
    require(any(all(old.get(k)==v for k,v in baseline.items()) for baseline in (previous,corrected)), 'UNRECOGNIZED_LARGE_HTML_BASELINE')
    expected={**old,**corrected}
    for location in policy['roots'].values():
        require(d.file_hashes(Path(location),ALL_FILES)==expected,'LARGE_HTML_SOURCE_MISMATCH')
    env={**os.environ,'MAIN_COMPUTER_HARDENED_CANDIDATE':'1','MAIN_COMPUTER_PRIMARY_OWNER':policy['primary'],
         'MAIN_COMPUTER_INSTALLATION_MODE':policy['primary'],'MAIN_COMPUTER_AUTH_BOOTSTRAP_PORT':'8875',
         'MAIN_COMPUTER_CONTROL_PORT':'8765'}
    say('large-html-preflight','CERTIFICATE_TRUST_REQUIRED (approve Windows certificate prompt if shown)')
    r=subprocess.run([sys.executable,'-m','main_computer.auth_bootstrap_service','--root',str(root),'preflight'],cwd=root,env=env,timeout=300)
    require(r.returncode==0,'LARGE_HTML_CERTIFICATE_PREFLIGHT_FAILED')
    say('large-html-preflight','CERTIFICATE_TRUST_PASS')
    require(image_id(policy['image_tag'])==policy['image_id'],'DOCUMENTSERVER_IMAGE_CHANGED_RECERTIFY')
    r=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p','test_mcel_onlyoffice_gateway_large_html.py','-v'],cwd=root,timeout=1000)
    require(r.returncode==0,'LARGE_HTML_GATEWAY_REGRESSION_FAILED')
    for action in ('gateway','live'):
        try:
            source_evidence(root, actions=(action,))
        except RuntimeError:
            say('large-html-certification',action)
            r=subprocess.run([sys.executable,'-m','tools.mcel_onlyoffice_cutover',action,'--tag',policy['image_tag']],cwd=root)
            require(r.returncode==0,'LARGE_HTML_'+action.upper()+'_CERTIFICATION_FAILED')
            source_evidence(root, actions=(action,))
        else:
            say('large-html-certification',action+' PASS_SOURCE_MATCHED_REUSED')
    source_evidence(root)
    require(image_id(policy['image_tag'])==policy['image_id'],'DOCUMENTSERVER_IMAGE_CHANGED_RECERTIFY')
    no_supervisors(policy['roots'])
    for location in policy['roots'].values():
        require(d.file_hashes(Path(location),ALL_FILES)==expected,'LARGE_HTML_SOURCE_CHANGED_DURING_CERTIFICATION')
    private_write(state,d.POLICY_NAME,{**policy,'fingerprint':expected})
    say('migration','HARDENED_LARGE_HTML_SELECTED_START_REQUIRED')


def migrate_health_probe(state, root, opts):
    """Rebind only the exact internal health-probe and workbook-name correction."""
    require(opts.authorize_cutover,'EXPLICIT_CUTOVER_AUTHORIZATION_REQUIRED')
    policy=d.read_policy(state)
    require(policy and policy['mode']=='hardened','HARDENED_DEPLOYMENT_NOT_SELECTED')
    require(d.lane_for(policy,root)==policy['primary'],'MIGRATE_FROM_PRIMARY_ROOT_REQUIRED')
    no_supervisors(policy['roots'])
    manifest=json.loads((root/'tools/onlyoffice-deployment-health-probe.json').read_text(encoding='utf-8'))
    allowed={'main_computer/web/applications/scripts/onlyoffice-app.js', 'tools/mcel_onlyoffice_deployment.py', 'main_computer/viewport_routes_onlyoffice.py', 'tools/mcel_onlyoffice_cutover.py'}
    require(set(manifest)=={'previous','corrected'} and set(manifest['previous'])==set(manifest['corrected'])==allowed,'INVALID_HEALTH_PROBE_MIGRATION_MANIFEST')
    old=policy['fingerprint']; previous=manifest['previous']; corrected=manifest['corrected']
    require(set(old)==set(ALL_FILES),'DEPLOYMENT_FINGERPRINT_INVENTORY_MISMATCH')
    require(any(all(old.get(k)==v for k,v in baseline.items()) for baseline in (previous,corrected)), 'UNRECOGNIZED_HEALTH_PROBE_BASELINE')
    expected={**old,**corrected}
    for location in policy['roots'].values():
        require(d.file_hashes(Path(location),ALL_FILES)==expected,'HEALTH_PROBE_SOURCE_MISMATCH')
    env={**os.environ,'MAIN_COMPUTER_HARDENED_CANDIDATE':'1','MAIN_COMPUTER_PRIMARY_OWNER':policy['primary'],
         'MAIN_COMPUTER_INSTALLATION_MODE':policy['primary'],'MAIN_COMPUTER_AUTH_BOOTSTRAP_PORT':'8875',
         'MAIN_COMPUTER_CONTROL_PORT':'8765'}
    say('health-probe-preflight','CERTIFICATE_TRUST_REQUIRED (approve Windows certificate prompt if shown)')
    r=subprocess.run([sys.executable,'-m','main_computer.auth_bootstrap_service','--root',str(root),'preflight'],cwd=root,env=env,timeout=300)
    require(r.returncode==0,'HEALTH_PROBE_CERTIFICATE_PREFLIGHT_FAILED')
    say('health-probe-preflight','CERTIFICATE_TRUST_PASS')
    require(image_id(policy['image_tag'])==policy['image_id'],'DOCUMENTSERVER_IMAGE_CHANGED_RECERTIFY')
    r=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p','test_mcel_onlyoffice_health_probe.py','-v'],cwd=root,timeout=1000)
    require(r.returncode==0,'HEALTH_PROBE_REGRESSION_FAILED')
    for action in ('gateway','live'):
        try:
            source_evidence(root, actions=(action,))
        except RuntimeError:
            say('health-probe-certification',action)
            r=subprocess.run([sys.executable,'-m','tools.mcel_onlyoffice_cutover',action,'--tag',policy['image_tag']],cwd=root)
            require(r.returncode==0,'HEALTH_PROBE_'+action.upper()+'_CERTIFICATION_FAILED')
            source_evidence(root, actions=(action,))
        else:
            say('health-probe-certification',action+' PASS_SOURCE_MATCHED_REUSED')
    source_evidence(root)
    require(image_id(policy['image_tag'])==policy['image_id'],'DOCUMENTSERVER_IMAGE_CHANGED_RECERTIFY')
    no_supervisors(policy['roots'])
    for location in policy['roots'].values():
        require(d.file_hashes(Path(location),ALL_FILES)==expected,'HEALTH_PROBE_SOURCE_CHANGED_DURING_CERTIFICATION')
    private_write(state,d.POLICY_NAME,{**policy,'fingerprint':expected})
    say('migration','HARDENED_HEALTH_PROBE_SELECTED_START_REQUIRED')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('promote','rollback','verify','status','environment','docker','migrate-frame-fix','migrate-localhost','migrate-large-html','migrate-health-probe'))
    p.add_argument('--root',type=Path,default=ROOT); p.add_argument('--primary',choices=('dev','unleashed'),default='dev')
    p.add_argument('--debug-root',type=Path); p.add_argument('--safe-root',type=Path)
    p.add_argument('--tag',default='latest'); p.add_argument('--authorize-cutover',action='store_true'); p.add_argument('--authorize-rollback',action='store_true')
    p.add_argument('--for-stop',action='store_true')
    p.add_argument('--docker-action',choices=('start','status','stop'),default='status')
    opts=p.parse_args(argv); root=opts.root.resolve()
    try:
        state=d.state_path()
        if opts.action=='environment': print(json.dumps(d.public_environment(state,root,check_source=not opts.for_stop))); return 0
        if opts.action=='status':
            policy=d.read_policy(state); say('selection',policy['mode'] if policy else 'legacy');
            if policy and policy['mode']=='hardened':
                say('registered-lanes',','.join(policy['roots']))
                try:
                    fingerprints=runtime_source_fingerprints(policy)
                    report=json.loads((state/'onlyoffice-deployment-verification.json').read_text())
                    current=process_listeners()
                    matched=runtime_receipt_matches(report,policy,fingerprints,current)
                    say('runtime-verification',report['verdict'] if matched else 'RUN_VERIFY')
                except (OSError,ValueError,KeyError,RuntimeError): say('runtime-verification','RUN_VERIFY')
        elif opts.action=='promote':
            require(opts.authorize_cutover,'EXPLICIT_CUTOVER_AUTHORIZATION_REQUIRED')
            with transition_lock(state): promote(state,root,opts)
        elif opts.action=='migrate-health-probe':
            with transition_lock(state): migrate_health_probe(state,root,opts)
        elif opts.action=='migrate-large-html':
            with transition_lock(state): migrate_large_html(state,root,opts)
        elif opts.action=='migrate-localhost':
            with transition_lock(state): migrate_localhost(state,root,opts)
        elif opts.action=='migrate-frame-fix':
            with transition_lock(state): migrate_frame_fix(state,root,opts)
        elif opts.action=='rollback':
            require(opts.authorize_rollback,'EXPLICIT_ROLLBACK_AUTHORIZATION_REQUIRED')
            with transition_lock(state): rollback(state,root,opts)
        elif opts.action=='docker': configure_docker(state,root,opts.docker_action)
        elif opts.action=='verify': verify(state,root)
        return 0
    except Exception as exc:
        msg=str(exc)
        # Error payloads may contain signed URLs or Docker secrets; whitelist
        # our named errors, otherwise disclose only the exception class.
        import re
        safe=msg if isinstance(exc,RuntimeError) and re.fullmatch(r'[A-Z0-9_,\-]+(?:_(?:dev|unleashed|debug|safe))?',msg) else type(exc).__name__
        print('MC_OO_DEPLOY BLOCKED '+safe,file=sys.stderr,flush=True); return 1

if __name__=='__main__': raise SystemExit(main())
