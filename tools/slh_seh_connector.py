"""Legacy manual outbound SEH connector and isolated identity diagnostic.

Retained for historical lab tests and backwards compatibility. The normal
SLH lifecycle must prepare identity during SLH setup and use the resident
managed-gateway connection supervisor. This CLI is not the deploy workflow.

  python -m tools.slh_seh_connector fresh-smoke
  python -m tools.slh_seh_connector identity --state-dir PATH
  python -m tools.slh_seh_connector run --seh-url wss://hub.secure.company.com

The SLH private service key never leaves its own state directory. `identity`
returns ONLY the public key. On a fresh, empty state root it initializes that
key securely and refuses to mix it with an existing SLH authority. `fresh-smoke`
exercises creation/read-back in a disposable directory without SQL or Coolify.
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
from main_computer.slh.storage import secure_machine_state_dir, secure_private_state_file


def default_state():
    from main_computer.slh.storage import slh_state_dir
    return slh_state_dir()


def service_identity(state_dir:Path, *, create=False, directory_protector=None):
    root=Path(state_dir)
    file=root/'seh-service-signing-key.pem'
    if not file.exists():
        if not create:
            raise FileNotFoundError('SEH identity not initialized; run identity first')
        # Empty-state bootstrap must be secure before writing a single key.
        # Do not generate a new relay identity into an established SLH state.
        if root.exists() and any(root.iterdir()):
            raise RuntimeError('SLH_STATE_NOT_FRESH: refusing to generate a missing signing key alongside existing state')
        # The resident SLH supplies its selected machine-state protector here.
        # Direct CLI use retains the production protector.
        (directory_protector or secure_machine_state_dir)(root)
        private=ec.generate_private_key(ec.SECP256R1())
        bits=private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption())
        fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as out:
            out.write(bits)
            out.flush()
            os.fsync(out.fileno())
        # The bootstrap is not accepted until this file has its OWN ACL and can
        # be read again by the same account. No SQL/database is involved.
        try:
            secure_private_state_file(file)
        except BaseException:
            # This path did not exist on entry (O_EXCL); never leave a new,
            # unverified private key as an apparently initialized identity.
            file.unlink(missing_ok=True)
            raise
    private=serialization.load_pem_private_key(file.read_bytes(),password=None)
    if not isinstance(private, ec.EllipticCurvePrivateKey) or not isinstance(private.curve, ec.SECP256R1):
        raise ValueError('SEH identity must be P-256')
    public=private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    return private,base64.b64encode(public).decode('ascii')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['identity','run','fresh-smoke'])
    p.add_argument('--state-dir',type=Path,default=default_state())
    p.add_argument('--seh-url',help='Public WSS SEH origin')
    p.add_argument('--gateway-port',type=int,default=DEFAULT_SLH_MANAGED_PORT)
    p.add_argument('--retry-seconds',type=float,default=3)
    p.add_argument('--allow-loopback-lab',action='store_true',
                   help='Allow plaintext WS only to localhost/127.0.0.1 for local Coolify lab')
    args=p.parse_args(argv)
    if args.command=='fresh-smoke':
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(prefix='mc-slh-fresh-smoke-') as temporary:
            state = Path(temporary) / 'slh'
            _, first = service_identity(state, create=True)
            _, second = service_identity(state, create=False)
            if first != second or (state / 'organization.sqlite3').exists():
                raise RuntimeError('Fresh SLH identity smoke failed')
        print(json.dumps({'status':'PASS', 'fresh_private_file_acl':'VERIFIED_READABLE',
                          'identity_stable_across_restart':True,
                          'sqlite_used':False, 'persistent_state_modified':False}, indent=2))
        return 0
    if args.command=='identity':
        _,public=service_identity(args.state_dir,create=True)
        print(json.dumps({'schema':'main-computer.seh-slh-identity.v1',
                          'public_key_b64':public,'state_dir':str(args.state_dir),
                          'private_key_exposed':False},indent=2))
        return 0
    if not args.seh_url:
        p.error('run requires --seh-url')
    if args.retry_seconds < 1 or args.retry_seconds > 60:
        p.error('--retry-seconds must be between 1 and 60')
    from main_computer.slh.seh_transport import _endpoint
    _endpoint(args.seh_url, allow_lab=args.allow_loopback_lab)
    private,_=service_identity(args.state_dir)
    print(json.dumps({'event':'seh_connector_starting',
                      'gateway_port':args.gateway_port}),flush=True)
    try:
        while True:
            conn=None
            try:
                conn=SLHSEHConnector(seh_url=args.seh_url,
                                     identity_private_key=private,
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
