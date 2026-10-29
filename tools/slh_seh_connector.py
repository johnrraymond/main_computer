"""SLH-owned outbound Secure External Hub connector, no additional server UI.

  python -m tools.slh_seh_connector identity --state-dir PATH
  python -m tools.slh_seh_connector run --seh-url wss://hub.secure.company.com --org-id ORG --slh-id SLH

The SLH private service key never leaves its own state directory. `identity`
returns ONLY the public key to place in the SEH Coolify target JSON.
"""
from __future__ import annotations

import argparse
import base64
from pathlib import Path
import json
import os
import sys
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from main_computer.slh.seh_transport import SLHSEHConnector
from main_computer.slh.ports import DEFAULT_SLH_MANAGED_PORT


def default_state():
    return Path(os.getenv('LOCALAPPDATA') or Path.home()/'.local'/'share')/'MainComputer'/'slh'


def service_identity(state_dir:Path, *, create=False):
    root=Path(state_dir)
    file=root/'seh-service-signing-key.pem'
    if not file.exists():
        if not create:
            raise FileNotFoundError('SEH identity not initialized; run identity first')
        root.mkdir(parents=True,exist_ok=True)
        private=ec.generate_private_key(ec.SECP256R1())
        bits=private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption())
        fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as out:out.write(bits)
    private=serialization.load_pem_private_key(file.read_bytes(),password=None)
    if not isinstance(private, ec.EllipticCurvePrivateKey) or not isinstance(private.curve, ec.SECP256R1):
        raise ValueError('SEH identity must be P-256')
    public=private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    return private,base64.b64encode(public).decode('ascii')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['identity','run'])
    p.add_argument('--state-dir',type=Path,default=default_state())
    p.add_argument('--seh-url',help='Public WSS SEH origin')
    p.add_argument('--org-id')
    p.add_argument('--slh-id')
    p.add_argument('--gateway-port',type=int,default=DEFAULT_SLH_MANAGED_PORT)
    p.add_argument('--retry-seconds',type=float,default=3)
    p.add_argument('--allow-loopback-lab',action='store_true',
                   help='Allow plaintext WS only to localhost/127.0.0.1 for local Coolify lab')
    args=p.parse_args(argv)
    if args.command=='identity':
        _,public=service_identity(args.state_dir,create=True)
        print(json.dumps({'schema':'main-computer.seh-slh-identity.v1',
                          'public_key_b64':public,'state_dir':str(args.state_dir),
                          'private_key_exposed':False},indent=2))
        return 0
    if not args.seh_url or not args.org_id or not args.slh_id:
        p.error('run requires --seh-url, --org-id and --slh-id')
    if args.retry_seconds < 1 or args.retry_seconds > 60:
        p.error('--retry-seconds must be between 1 and 60')
    from main_computer.slh.seh_transport import _endpoint
    _endpoint(args.seh_url, allow_lab=args.allow_loopback_lab)
    private,_=service_identity(args.state_dir)
    print(json.dumps({'event':'seh_connector_starting', 'org_id':args.org_id,
                      'slh_id':args.slh_id,'gateway_port':args.gateway_port}),flush=True)
    try:
        while True:
            conn=None
            try:
                conn=SLHSEHConnector(seh_url=args.seh_url,organization_id=args.org_id,
                                     slh_id=args.slh_id,identity_private_key=private,
                                     gateway_port=args.gateway_port,
                                     allow_lab=args.allow_loopback_lab).connect()
                print(json.dumps({'event':'seh_outbound_authenticated'}),flush=True)
                while conn._running.is_set():
                    time.sleep(.3)
            except (OSError,ValueError,RuntimeError,ConnectionError,TimeoutError) as exc:
                print(json.dumps({'event':'seh_connection_failed','error_type':type(exc).__name__}),file=sys.stderr,flush=True)
            finally:
                if conn:conn.stop()
            time.sleep(args.retry_seconds)
    except KeyboardInterrupt:
        return 0


if __name__=='__main__':raise SystemExit(main())
