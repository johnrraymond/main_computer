"""Disposable protocol attacks against the actual MC ONLYOFFICE callback method.

No Docker or production state required. Starts the real-editor fixture with a
private proof, workbook, and loopback listener, then uses a *separate client*
for both valid and hostile operations. A known valid callback is required as a
positive control: all denials are meaningless if the service is simply down.

This is a candidate-layer test, not certification of the unchanged production
HTTP dispatch or a second real Windows security principal.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

from tools import mcel_onlyoffice_real_editor_lab as lab

ROOT = lab.ROOT
DOC = 'real-edit.xlsx'


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def jwt(secret: str, body: dict) -> str:
    header = b64(json.dumps({'typ':'JWT','alg':'HS256'},separators=(',',':')).encode())
    payload = b64(json.dumps(body,separators=(',',':')).encode())
    signing = f'{header}.{payload}'
    return signing+'.'+b64(hmac.new(secret.encode(),signing.encode(),hashlib.sha256).digest())


def cap(key: bytes, kind: str, mode: str, document: str, expiry: int) -> str:
    body=f'mc-real-onlyoffice-v1|{kind}|{mode}|{document}|{expiry}'
    return f'{expiry}.'+hmac.new(key,body.encode(),hashlib.sha256).hexdigest()


def exercise(count: int = 200) -> dict:
    if not 1 <= count <= 10000:raise ValueError('count out of bounds')
    results=[]
    with tempfile.TemporaryDirectory(prefix='mc-onlyoffice-adversarial-') as tmp:
        root=Path(tmp)
        documents=root/'documents';documents.mkdir()
        workbook=documents/DOC
        lab.create_workbook(workbook,'ORIGINAL_UNMODIFIED')
        key=secrets.token_bytes(32)
        secret=secrets.token_urlsafe(48)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={**os.environ,
             'LAB_AUTH_KEY_B64':b64(key), 'LAB_JWT_SECRET':secret,
             'LAB_BROWSER_PORT':str(port), 'LAB_PORT':str(port),
             'LAB_BIND':'127.0.0.1','LAB_DOCUMENT_DIR':str(documents),
             'LAB_AUTH_ROOT':str(root/'private'),
             'LAB_SOURCE_PATH':str(ROOT/'main_computer'/'viewport_routes_onlyoffice.py'),
             'PYTHONPATH':str(ROOT)}
        proc=subprocess.Popen([sys.executable,'-u',str(ROOT/'tools'/'mcel_onlyoffice_real_editor_fixture.py')],
            env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
        host=f'dev.main-computer.local:{port}'
        def req(method,path,body=None,headers=None,request_host=None,*,recovery_probe=False):
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=100 if recovery_probe else 8)
            payload=json.dumps(body).encode() if body is not None else None
            all_headers={'Host': request_host or host,
                         'Content-Type':'application/json',**(headers or {})}
            try:
                conn.request(method,path,body=payload,headers=all_headers)
                r=conn.getresponse()
                content=r.read(2048)
                return r.status, dict(r.getheaders()), content
            finally:conn.close()
        def check(name,expected,actual):
            ok=(actual in expected)
            print(f'MC_OO_PRECUTOVER_ATTACK {name} {"PASS" if ok else "FAIL"} '
                  f'observed={actual} expected={sorted(expected)}',flush=True)
            results.append((name,ok,actual))
        try:
            for _ in range(200):
                if proc.poll() is not None:
                    raise RuntimeError(f'fixture terminated early, returncode={proc.returncode}')
                try:
                    if req('GET','/health')[0]==200:break
                except (OSError, TimeoutError):time.sleep(.03)
            else:raise RuntimeError('fixture readiness timeout')
            # Alice authenticates with a real disposable proof HMAC.
            st,_,raw=req('GET','/auth/challenge')
            check('real-disposable-challenge',{200},st)
            c=json.loads(raw)
            msg=b'MC-AUTH-BOOTSTRAP-V1\n'+c['challenge_id'].encode()+b'\n'+c['nonce'].encode()+b'\n'
            signed=b64(hmac.new(key,msg,hashlib.sha256).digest())
            st,heads,_=req('POST','/auth/verify',{'challenge_id':c['challenge_id'],'signature':signed},
                           {'Origin':f'https://{host}'})
            check('real-disposable-session-issued',{200},st)
            cookie=heads.get('Set-Cookie','').split(';',1)[0]
            if '__Host-MC-Session=' not in cookie:raise AssertionError('missing actual cookie')
            st,_,raw=req('GET','/api/onlyoffice/config',headers={'Cookie':cookie})
            check('authorized-config-positive-control',{200},st)
            config=json.loads(raw)
            doc_key=config['document']['key']
            now=int(time.time())
            download_url='/api/onlyoffice/file?mode=dev&doc=real-edit.xlsx&cap='
            callback_url='/api/onlyoffice/callback?mode=dev&doc=real-edit.xlsx&path=real-edit.xlsx&cap='
            dl=cap(key,'download','dev',DOC,now+90)
            cb=cap(key,'callback','dev',DOC,now+90)
            st,_,raw=req('GET',download_url+dl)
            check('valid-document-download-positive-control',{200},st)
            if b'PK'!=raw[:2]:raise AssertionError('valid workbook bytes absent')
            status={'status':1,'key':doc_key}
            st,_,_=req('POST',callback_url+cb,status,{'Authorization':'Bearer '+jwt(secret,status)})
            check('valid-document-status-positive-control',{200},st)
            # A valid user cookie is insufficient to authorize Doc Server callbacks.
            attacks=[
                ('anonymous-editor-config','GET','/api/onlyoffice/config',None,{}, {401,403}),
                ('forged-user-cookie','GET','/api/onlyoffice/config',None,
                    {'Cookie':'__Host-MC-Session=forged'}, {401,403}),
                ('duplicate-user-cookie','GET','/api/onlyoffice/config',None,
                    {'Cookie':cookie+'; '+cookie}, {401,403}),
                ('malicious-origin-even-with-valid-session','POST','/api/onlyoffice/forcesave',
                    {'action':'forcesave'},{'Cookie':cookie,'Origin':'https://onlyoffice.main-computer.local'}, {401,403}),
                ('callback-cookie-not-capability','POST',callback_url+'bogus',status,
                    {'Cookie':cookie,'Authorization':'Bearer '+jwt(secret,status)}, {401,403}),
                ('wrong-document-token','GET',download_url+cap(key,'download','dev','other.xlsx',now+90),None,{}, {401,403,404}),
                ('cross-mode-token','GET',download_url+cap(key,'download','debug',DOC,now+90),None,{}, {401,403}),
                ('callback-token-not-download','GET',download_url+cb,None,{}, {401,403}),
                ('expired-download-token','GET',download_url+cap(key,'download','dev',DOC,now-1),None,{}, {401,403}),
                ('future-expiry-too-long','GET',download_url+cap(key,'download','dev',DOC,now+7200),None,{}, {401,403}),
                ('duplicate-doc-query','GET',download_url+dl+'&doc=real-edit.xlsx',None,{}, {401,403}),
                ('wrong-download-mode','GET',download_url.replace('mode=dev','mode=safe')+dl,None,{}, {401,403}),
                ('other-document-url','GET',download_url.replace('real-edit.xlsx','other.xlsx')+dl,None,{}, {401,403,404}),
                ('download-not-callback','POST',callback_url+dl,status,
                    {'Authorization':'Bearer '+jwt(secret,status)}, {401,403}),
                ('cross-lane-callback','POST',callback_url.replace('mode=dev','mode=debug')+cb,status,
                    {'Authorization':'Bearer '+jwt(secret,status)}, {401,403}),
                ('callback-missing-jwt','POST',callback_url+cb,status,{}, {401,403}),
                ('callback-forged-jwt','POST',callback_url+cb,status,{'Authorization':'Bearer '+jwt('wrong',status)}, {401,403}),
                ('callback-wrong-document-key','POST',callback_url+cb,{'status':1,'key':'other-revision'},
                    {'Authorization':'Bearer '+jwt(secret,{'status':1,'key':'other-revision'})}, {401,403}),
                ('callback-mismatched-signed-status','POST',callback_url+cb,
                    {'status':2,'key':doc_key,'url':'http://attacker.invalid/exfil'},
                    {'Authorization':'Bearer '+jwt(secret,status)}, {401,403}),
                ('callback-signed-evil-url','POST',callback_url+cb,
                    {'status':2,'key':doc_key,'url':'http://127.0.0.1:1/private'},
                    {'Authorization':'Bearer '+jwt(secret,{'status':2,'key':doc_key,'url':'http://127.0.0.1:1/private'})}, {400,401,403}),
                ('callback-signed-metadata-ip','POST',callback_url+cb,
                    {'status':2,'key':doc_key,'url':'http://169.254.169.254/latest/meta-data'},
                    {'Authorization':'Bearer '+jwt(secret,{'status':2,'key':doc_key,'url':'http://169.254.169.254/latest/meta-data'})}, {400,401,403}),
                ('duplicate-callback-query','POST',callback_url+cb+'&cap='+cb,status,
                    {'Authorization':'Bearer '+jwt(secret,status)}, {401,403}),
                ('internal-host-cannot-grant-user-access','GET','/graphical',None,{}, {403}),
                ('unknown-host-cannot-grant-user-access','GET','/graphical',None,{}, {403}),
                ('no-editor-launch-without-ticket','GET','/mc-editor',None,{}, {401,403}),
            ]
            for name,method,url,body,headers,expected in attacks:
                actual_host=('fixture:'+str(port) if name.startswith('internal-host') else
                             'other.main-computer.local' if name.startswith('unknown-host') else None)
                check(name,expected,req(method,url,body,headers,actual_host)[0])
            # Fuzz malformed capabilities with varying fields. A forged bearer,
            # origin or browser cookie must never convert a denial into success.
            for i in range(count):
                random_token=secrets.token_urlsafe(30)
                attack_kind=i%6
                if attack_kind==0:
                    url=download_url+quote(random_token,safe=''); method='GET'; body=None;headers={}
                elif attack_kind==1:
                    url=download_url+quote(random_token,safe=''); method='GET'; body=None;headers={'Cookie':cookie}
                elif attack_kind==2:
                    url=download_url+dl+'&cap='+quote(random_token,safe='');method='GET';body=None;headers={}
                elif attack_kind==3:
                    url=callback_url+quote(random_token,safe='');method='POST';body=status;headers={'Authorization':'Bearer '+jwt(secret,status)}
                elif attack_kind==4:
                    url=callback_url+cb;method='POST';body=status;headers={'Authorization':'Bearer '+random_token}
                else:
                    url=callback_url+cb;method='POST';body={'status':2,'key':doc_key,'url':'http://attacker.invalid/'+random_token};headers={'Authorization':'Bearer '+jwt('wrong',status)}
                result=req(method,url,body,headers)[0]
                if result not in {400,401,403,404}:
                    check('fuzz-forged-creds-'+str(i),{400,401,403,404},result)
                    break
            else:
                check('fuzz-forged-capability-and-jwt-denial',{count},count)
            check('workbook-bytes-unchanged-after-attacks',{'ORIGINAL_UNMODIFIED'},lab.get_cell(workbook,'A1'))
            # Reissuing the proof invalidates the valid session without a fixture restart.
            (root/'private'/'AAA_AUTH_MAIN_COMPUTER.json').unlink()
            from tools.mcel_onlyoffice_pre_cutover_recovery import await_reissue_http
            recovered_status,_,_=await_reissue_http(
                lambda: req('GET','/api/onlyoffice/config',
                            headers={'Cookie':cookie},recovery_probe=True),
                process=proc,label='MC_OO_PRECUTOVER_ATTACK deleted-proof-session-revocation',
                deadline_seconds=90)
            check('proof-deletion-revokes-authorized-session',{401,403},recovered_status)
            check('reissued-proof-exists',{True},(root/'private'/'AAA_AUTH_MAIN_COMPUTER.json').is_file())
        finally:
            from tools.mcel_onlyoffice_pre_cutover_recovery import stop_disposable_fixture
            stop_disposable_fixture(proc)
            if proc.stderr:proc.stderr.close()
    passed=sum(int(ok) for _,ok,_ in results)
    summary={'status':'PASS' if passed==len(results) else 'FAIL',
             'checks':len(results),'passed':passed,'failed':len(results)-passed,
             'fuzz_iterations':count,'production_modified':False,
             'scope':'disposable original callback with lab authorization wrapper'}
    print('MC_OO_PRECUTOVER_ATTACK SUMMARY '+json.dumps(summary,sort_keys=True),flush=True)
    return summary


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',nargs='?',default='run',choices=['run'])
    p.add_argument('--fuzz-count',type=int,default=300)
    args=p.parse_args(argv)
    return 0 if exercise(args.fuzz_count)['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
