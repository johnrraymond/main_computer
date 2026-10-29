"""Configure the existing local Worker runtime to connect to an enrolled SLH.

Defaults to preview. --apply saves only managed-SLH fields to the local viewport.
This does not enroll a device, create a marketplace wallet, or enable a public Hub.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request,urlopen

from main_computer.slh.ports import DEFAULT_SLH_API_HOST


def plan(*, device_id:str, slh_url:str, tls_fingerprint:str, identity_dir:Path):
    url=urlsplit(slh_url)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
        raise ValueError('SLH managed endpoint must be an HTTPS origin')
    if len(tls_fingerprint)!=64 or not all(c in '0123456789abcdef' for c in tls_fingerprint.lower()):
        raise ValueError('SLH SHA-256 transport fingerprint required')
    from tools.slh_authenticator_cli import load_identity
    load_identity(identity_dir,device_id)  # fail closed if device key is missing
    config={'selectedNetwork':'slh','workerAutoConnectNetwork':'slh',
            'managedSLHUrl':slh_url.rstrip('/'),'managedSLHDeviceId':device_id,
            'managedSLHFingerprint':tls_fingerprint.lower(),
            'managedSLHIdentityDir':str(Path(identity_dir).resolve()),
            'sellerEnabled':True}
    return {'schema':'main-computer.slh-managed-worker-settings.v1',
            'settings':config,'changed_fields':list(config)}


def apply_local_viewport(preview, origin):
    parsed=urlsplit(origin)
    if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost','::1'} or parsed.path not in ('','/'):
        raise ValueError('Worker configuration is local-loopback-only')
    endpoint=origin.rstrip('/')+'/api/applications/worker/settings'
    payload=json.dumps(preview).encode('utf-8')
    with urlopen(Request(endpoint,data=payload,method='POST',headers={'Content-Type':'application/json'}),timeout=8) as res:
        body=json.loads(res.read())
    if body.get('ok') is not True:raise RuntimeError('Worker settings update refused')
    return {'status':'CONFIGURED','mode':'slh','device_id':preview['settings']['managedSLHDeviceId']}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device-id',required=True)
    p.add_argument('--slh-url',required=True)
    p.add_argument('--fingerprint',required=True)
    p.add_argument('--identity-dir',type=Path,required=True)
    p.add_argument('--viewport-url',default='http://127.0.0.1:8765')
    p.add_argument('--apply',action='store_true')
    args=p.parse_args(argv)
    result=plan(device_id=args.device_id,slh_url=args.slh_url,
                tls_fingerprint=args.fingerprint,identity_dir=args.identity_dir)
    if args.apply:result=apply_local_viewport(result,args.viewport_url)
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0

if __name__=='__main__':raise SystemExit(main())
