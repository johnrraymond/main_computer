"""SLH Phase 2A CLI: local-only authentication service and isolated auth harness.

  python -m tools.slh_auth_harness init --state-dir ./runtime/slh
  python -m tools.slh_auth_harness serve --state-dir ./runtime/slh
  python -m tools.slh_auth_harness contracts --state-dir ./runtime/slh
  python -m tools.slh_auth_harness verify-local --state-dir ./runtime/slh-test

No LAN/SEH exposure. A loopback origin and protected local bootstrap secret are mandatory.
"""
import argparse
import json
import os
import socket
from pathlib import Path
from wsgiref.simple_server import WSGIServer, make_server
from main_computer.slh.authority import SLHAuthority
from main_computer.slh.api import SLHAPI
from main_computer.slh.ports import DEFAULT_SLH_API_HOST, DEFAULT_SLH_API_PORT


class ExclusiveSLHServer(WSGIServer):
    """Refuse a shared/reused listener, especially important on Windows."""
    allow_reuse_address = False
    allow_reuse_port = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('init','serve','contracts','verify-local'))
    default_root=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local'/'share'))) / 'MainComputer' / 'slh'
    p.add_argument('--state-dir',type=Path,default=default_root)
    p.add_argument('--host',default=DEFAULT_SLH_API_HOST)
    p.add_argument('--port',type=int,default=DEFAULT_SLH_API_PORT)
    args=p.parse_args(argv)
    if args.host!=DEFAULT_SLH_API_HOST:
        p.error('SLH Phase 2A HTTP is loopback-only; encrypted remote transport is NOT implemented')
    if args.command=='verify-local':
        from tests.test_slh_phase2a_auth_api import run_isolated_harness
        result=run_isolated_harness()
    else:
        auth=SLHAuthority(args.state_dir)
        api=SLHAPI(auth)
        if args.command=='init':
            result={'schema':'main-computer.slh-bootstrap.v1','state_dir':str(args.state_dir.resolve()),
                    'bootstrap_secret_file':str((args.state_dir/'bootstrap.secret').resolve()),
                    'status':auth.status(),'instruction':'Read bootstrap.secret locally; never copy it to an SEH or public website'}
        elif args.command=='contracts':result=api.openapi()
        else:
            try:
                # The listener must bind successfully before we claim readiness.
                with make_server(DEFAULT_SLH_API_HOST,args.port,api.wsgi_app,server_class=ExclusiveSLHServer) as server:
                    print(f'SLH Phase 2A loopback-only API listening at http://{DEFAULT_SLH_API_HOST}:{server.server_port}',flush=True)
                    server.serve_forever()
            except OSError as exc:
                print(json.dumps({'schema':'main-computer.slh-listener.v1',
                                  'status':'SLH_LISTENER_UNAVAILABLE',
                                  'host':DEFAULT_SLH_API_HOST, 'port':args.port,
                                  'detail':str(exc)},sort_keys=True),flush=True)
                return 2
            return 0
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0 if result.get('status')!='FAIL' else 1


if __name__=='__main__':raise SystemExit(main())
