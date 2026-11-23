"""Run from a DIFFERENT, non-admin Windows account while Alice's disposable
real-editor lab is running with --external-bob-window 300.

Bob receives NO AAA key, session cookie, editor ticket, or Docker credentials.
A missing Alice proof path is UNVERIFIED, not a PASS. The script checks the
current Windows SID and non-elevated token, then attacks the loopback HTTPS
proxy. No production data is read, printed or modified.

Example (execute in Bob's own Windows login, with an accessible checkout):
    python -m tools.mcel_onlyoffice_pre_cutover_windows_bob run \
        --port <ephemeral-port> --alice-sid <alice-SID> \
        --alice-proof-file <Alice-proof-path>

The explicit --alice-proof-file argument is optional, but omission leaves its
ACL boundary UNVERIFIED. A separate-account *authenticated* cross-user
session attack requires a later two-account production-equivalent integration.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import http.client
from datetime import datetime, timezone


def current_sid():
    if os.name!='nt':return None
    r=subprocess.run(['whoami.exe','/user','/fo','csv','/nh'],capture_output=True,text=True,timeout=12)
    if r.returncode:return None
    rows=list(csv.reader(io.StringIO(r.stdout)))
    return rows[0][1].strip() if len(rows)==1 and len(rows[0])>=2 else None


def elevated():
    if os.name!='nt':return None
    cmd=("$p=[Security.Principal.WindowsPrincipal]"+
         "[Security.Principal.WindowsIdentity]::GetCurrent();"+
         "$p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)")
    r=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',cmd],
                     capture_output=True,text=True,timeout=15)
    if r.returncode:return None
    return r.stdout.strip().lower()=='true'


def request(port,host,method,path,body=None,headers=None):
    # Intentionally ignore TLS trust for *Bob's unauthenticated attack* only;
    # Alice's separate SNI/SAN test establishes the cert trust property.
    # The attacker must not need to install a cert or edit Windows DNS.
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname=False
    context.verify_mode=ssl.CERT_NONE
    con=http.client.HTTPSConnection('127.0.0.1',port,context=context,timeout=10)
    try:
        data=json.dumps(body).encode() if body is not None else None
        head={'Host':host,'Content-Type':'application/json',**(headers or {})}
        con.request(method,path,body=data,headers=head)
        r=con.getresponse()
        r.read(8192)
        return r.status
    finally:con.close()


def run(port,alice_sid,alice_proof_file=None,report_dir=None):
    rows=[]
    def check(name,status,reason):
        print('MC_OO_WINDOWS_BOB '+name+' '+status+' '+reason,flush=True)
        rows.append({'name':name,'status':status,'detail':reason})
    if os.name!='nt':
        check('actual-windows-token','UNVERIFIED','requires a real Windows login')
    else:
        sid=current_sid();elev=elevated()
        check('bob-real-different-windows-sid',
              'PASS' if sid and alice_sid and sid.lower()!=alice_sid.lower() else 'FAIL',
              'SID-different='+str(bool(sid and alice_sid and sid.lower()!=alice_sid.lower())))
        check('bob-nonadministrator-token',
              'PASS' if elev is False else 'UNVERIFIED' if elev is None else 'FAIL',
              'elevated='+str(elev))
        if alice_proof_file is not None:
            try:
                with Path(alice_proof_file).open('rb'):
                    accessible=True
            except PermissionError:
                check('alice-proof-windows-acl-denies-bob','PASS','open denied by filesystem')
            except FileNotFoundError:
                check('alice-proof-windows-acl-denies-bob','UNVERIFIED','specified file not present')
            except OSError as exc:
                check('alice-proof-windows-acl-denies-bob','UNVERIFIED',type(exc).__name__)
            else:
                check('alice-proof-windows-acl-denies-bob','FAIL','Bob can open Alice proof file')
        else:
            check('alice-proof-windows-acl-denies-bob','UNVERIFIED','requires --alice-proof-file')
        dev=f'dev.main-computer.local:{port}'
        docs=f'onlyoffice.main-computer.local:{port}'
        cases=[
          ('unauthenticated-graphical-redirect','GET','/graphical',None,dev,{303}),
          ('anonymous-editor-config-denied','GET','/api/onlyoffice/config',None,dev,{401,403}),
          ('forged-session-cookie-denied','GET','/api/onlyoffice/config',None,dev,{401,403}),
          ('anonymous-download-denied','GET','/api/onlyoffice/file?mode=dev&doc=real-edit.xlsx',None,dev,{403}),
          ('forged-document-download-denied','GET','/api/onlyoffice/file?mode=dev&doc=real-edit.xlsx&cap=garbage',None,dev,{403}),
          ('anonymous-callback-denied','POST','/api/onlyoffice/callback?mode=dev&doc=real-edit.xlsx',
            {'status':2,'url':'http://127.0.0.1:1/attack'},dev,{403}),
          ('forged-callback-denied','POST','/api/onlyoffice/callback?mode=dev&doc=real-edit.xlsx&path=real-edit.xlsx&cap=fake',
            {'status':2,'url':'http://127.0.0.1:1/attack'},dev,{403}),
          ('docs-editor-launch-no-ticket','GET','/mc-editor',None,docs,{403}),
          ('docs-config-no-ticket','GET','/mc-config',None,docs,{403}),
          ('unapproved-local-host-rejected','GET','/graphical',None,f'unknown.main-computer.local:{port}',{421}),
        ]
        for name,method,path,body,host,expected in cases:
            headers={'Cookie':'__Host-MC-Session=forged-credential-should-not-work'} if name=='forged-session-cookie-denied' else {}
            try:actual=request(port,host,method,path,body,headers)
            except Exception as exc:
                check(name,'UNVERIFIED',type(exc).__name__+': cannot reach disposable proxy')
            else:check(name,'PASS' if actual in expected else 'FAIL',f'HTTP={actual}')
    counts={s:sum(r['status']==s for r in rows) for s in ('PASS','FAIL','UNVERIFIED')}
    status='PASS' if counts['FAIL']==0 and counts['UNVERIFIED']==0 else 'BLOCKED'
    result={'status':status,'counts':counts,'checks':rows,'port':port,
            'original_alice_sid_supplied':bool(alice_sid),'current_sid':current_sid(),
            'production_modified':False,
            'separate_windows_principal':'operator-run-not-independent-os-telemetry',
            'report_created_utc':datetime.now(timezone.utc).isoformat()}
    if report_dir:
        out=Path(report_dir);out.mkdir(parents=True,exist_ok=True)
        (out/'windows-bob.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print('MC_OO_WINDOWS_BOB SUMMARY '+json.dumps({
        'status':status,'counts':counts,'production_modified':False,
        'second_sid_observed':bool(os.name=='nt' and current_sid() and alice_sid and current_sid()!=alice_sid)},sort_keys=True),flush=True)
    return 0 if status=='PASS' else 1


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',nargs='?',default='run',choices=['run'])
    p.add_argument('--port',type=int,required=True)
    p.add_argument('--alice-sid',required=True)
    p.add_argument('--alice-proof-file',type=Path)
    p.add_argument('--report-dir',type=Path)
    args=p.parse_args(argv)
    if not 1<=args.port<=65535:p.error('invalid port')
    if not re.fullmatch(r'S-1-\d+(?:-\d+)+',args.alice_sid):p.error('invalid Alice SID')
    return run(args.port,args.alice_sid,args.alice_proof_file,args.report_dir)

if __name__=='__main__':raise SystemExit(main())
