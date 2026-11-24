"""Normal Windows deployment adapter for the certified HTTPS gateway.

The primary supervisor alone owns the listener. It reuses the tested gateway
and proof monitor, with a route map limited to registered installation roots.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

from main_computer import onlyoffice_deployment as deployment
from main_computer.auth_hostnames import APP_PORTS, HOSTS
from main_computer import auth_bootstrap_service as bootstrap
from main_computer.auth_bootstrap import BootstrapAuthority, BootstrapHandler, BootstrapServer


class RegisteredHandler(BootstrapHandler):
    def _proxy(self, method):
        # Only successful HTML returned by the authenticated application
        # proxy may be framed. Login, errors, APIs, and docs remain denied.
        from urllib.parse import urlsplit
        path = urlsplit(self.path).path
        self._application_proxy_get = (method == 'GET' and self._host_ok()
            and (path == '/applications' or path.startswith('/applications/')))
        try:
            return super()._proxy(method)
        finally:
            self._application_proxy_get = False

    def _send(self, code, payload=b'', *, kind='application/json; charset=utf-8',
              cookie=None, csp=None):
        self._same_origin_frame = bool(getattr(self, '_application_proxy_get', False)
            and code == 200 and kind.split(';', 1)[0].strip().lower() == 'text/html'
            and cookie is None and csp is None)
        try:
            return super()._send(code, payload, kind=kind, cookie=cookie,
                csp="frame-ancestors 'self'" if self._same_origin_frame else csp)
        finally:
            self._same_origin_frame = False

    def send_header(self, keyword, value):
        if (getattr(self, '_same_origin_frame', False)
                and keyword.lower() == 'x-frame-options' and value == 'DENY'):
            value = 'SAMEORIGIN'
        return super().send_header(keyword, value)

    def _host_ok(self):
        return (super()._host_ok() and self.headers.get('Host','') in
                {f'{HOSTS[lane]}:{self.server.server_port}' for lane in self.server.isolated_backend_ports})

    def do_GET(self):
        # Handoff capabilities must never target a nonregistered lane.
        from urllib.parse import urlsplit, parse_qs
        url=urlsplit(self.path)
        if url.path.startswith('/auth/deployment/handoff/'):
            lane=url.path.rsplit('/',1)[-1]
            if (not self._host_ok() or lane not in self.server.isolated_backend_ports
                    or self.headers.get('Host','') != f'{HOSTS[os.environ["MAIN_COMPUTER_PRIMARY_OWNER"]]}:{self.server.server_port}'):
                self._json(403, {'ok':False,'error':'HANDOFF_UNREGISTERED_LANE'})
                return
            if not self._authorized():
                self._redirect_to_login(url.path)
                return
            self._redirect('/auth/handoff/start?target='+HOSTS[lane])
            return
        if url.path=='/auth/handoff/start':
            target=parse_qs(url.query).get('target',[''])[0]
            if target not in {HOSTS[lane] for lane in self.server.isolated_backend_ports}:
                self._json(403, {'ok':False,'error':'HANDOFF_UNREGISTERED_LANE'})
                return
        return super().do_GET()


def serve(root, state, policy):
    if deployment.lane_for(policy,root)!=policy['primary']:
        raise RuntimeError('SHARED_AUTH_PRIMARY_ONLY')
    deployment.verify_source(policy,root)
    _, cert, key=bootstrap.preflight(state=state)
    authority=BootstrapAuthority(state)
    routes={lane:APP_PORTS[lane] for lane in policy['roots']}
    with BootstrapServer(('127.0.0.1',8875),authority,cert=cert,key=key,
                         isolated_backend_ports=routes) as server:
        server.RequestHandlerClass=RegisteredHandler
        pid_file=root/bootstrap.PID_NAME
        pid_file.write_text(json.dumps({'pid':os.getpid(),'root':str(root),'service':'main_computer.auth_bootstrap_service'})+'\n',encoding='utf-8')
        stop=threading.Event()
        monitor=threading.Thread(target=bootstrap.monitor_proof_file,args=(authority,stop),daemon=True)
        monitor.start()
        def terminate(*_): threading.Thread(target=server.shutdown,daemon=True).start()
        previous={}
        for sig in (signal.SIGINT,signal.SIGTERM):
            try: previous[sig]=signal.signal(sig,terminate)
            except (OSError,ValueError): pass
        try:
            print('MC_AUTH_BOOTSTRAP READY registered-lanes='+','.join(routes),flush=True)
            server.serve_forever(poll_interval=.2)
        finally:
            stop.set();monitor.join(timeout=15)
            for sig,prior in previous.items():signal.signal(sig,prior)
            try:
                if json.loads(pid_file.read_text(encoding='utf-8')).get('pid')==os.getpid():pid_file.unlink()
            except (OSError,ValueError):pass


def open_browser(root,state,policy):
    lane=deployment.lane_for(policy,root)
    deployment.verify_source(policy,root)
    bootstrap.recover_deleted_proof_locally(state)
    ready,detail=bootstrap.wait_for_probe(state=state,port=8875,wait_seconds=45)
    if not ready: raise RuntimeError('SHARED_HTTPS_NOT_READY_'+detail)
    # Use the Windows default browser and its ordinary persistent profile.
    # .localhost is natively resolved by modern browsers.
    primary=HOSTS[policy['primary']]; host=HOSTS[lane]
    # The allowlisted handoff path redirects to primary login when no session
    # exists, then resumes the one-use handoff after authentication.
    url=(f'https://{primary}:8875/auth/deployment/handoff/{lane}' if lane!=policy['primary']
         else f'https://{host}:8875/auth/login?next=%2Fgraphical')
    os.startfile(url)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('command',choices=('run','open'))
    opts=parser.parse_args(argv)
    try:
        state=deployment.state_path();root=opts.root.resolve();policy=deployment.read_policy(state)
        if not policy or policy['mode']!='hardened' or os.getenv('MAIN_COMPUTER_HARDENED_CANDIDATE')!='1':
            raise RuntimeError('HARDENED_DEPLOYMENT_REQUIRED')
        if opts.command=='run':serve(root,state,policy)
        else:open_browser(root,state,policy)
        return 0
    except Exception as exc:
        print('MC_AUTH_DEPLOYMENT BLOCKED '+type(exc).__name__,file=sys.stderr,flush=True)
        return 1

if __name__=='__main__':raise SystemExit(main())
