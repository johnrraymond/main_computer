"""Disposable real Docker failure/recovery, not production deployment.

Simulates Document Server and MC test-backend loss and recovery, with session
persistence and document byte integrity checks. It does NOT claim to prove
interruption *during* a real callback; stale-revision integrity is separately
falsified by the actual handler test and must be fixed before cutover.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import tempfile
import time

from tools import mcel_onlyoffice_real_editor_lab as lab


def https_response(port,cert,host,method,path,body=None,cookie=None):
    ctx=ssl.create_default_context(cafile=str(cert))
    conn=socket.create_connection(('127.0.0.1',port),timeout=12)
    tls=ctx.wrap_socket(conn,server_hostname=host)
    try:
        payload=json.dumps(body).encode() if body is not None else b''
        request=(f'{method} {path} HTTP/1.1\r\nHost: {host}:{port}\r\n'
                 f'Connection: close\r\nContent-Type: application/json\r\n'
                 f'Content-Length: {len(payload)}\r\n')
        if method=='POST' and host==lab.HOST_DEV:request+=f'Origin: https://{host}:{port}\r\n'
        if cookie:request+=f'Cookie: {cookie}\r\n'
        tls.sendall(request.encode()+b'\r\n'+payload)
        response=http.client.HTTPResponse(tls)
        response.begin()
        data=response.read(4096)
        return response.status,dict(response.getheaders()),data
    finally:tls.close()


def experiment(tag='latest'):
    if not shutil.which('docker'):
        print('MC_OO_DOCKER_FAULTS SUMMARY '+json.dumps({'status':'UNVERIFIED','reason':'docker-unavailable'}),flush=True)
        return 2
    if not re.fullmatch(r'[A-Za-z0-9][\w.\-]{0,95}',tag):raise ValueError('unsafe image tag')
    outcome=2
    with tempfile.TemporaryDirectory(prefix='mc-oo-fault-lab-') as tmp:
        root=Path(tmp); (root/'documents').mkdir()
        workbook=root/'documents'/'real-edit.xlsx'
        lab.create_workbook(workbook,'ORIGINAL_STABLE')
        original_sha=hashlib.sha256(workbook.read_bytes()).hexdigest()
        cert,key=lab.certificate(root)
        (root/'nginx.conf').write_text(lab.nginx_config(),encoding='utf8')
        secret=secrets.token_bytes(32)
        b64=base64.urlsafe_b64encode(secret).decode().rstrip('=')
        port=lab.available_port()
        project='mc-oo-fault-'+secrets.token_hex(4)
        (root/'docker-compose.json').write_text(json.dumps(lab.manifest(root,port,b64,secrets.token_urlsafe(40),tag),indent=2))
        compose=['docker','compose','-p',project,'-f',str(root/'docker-compose.json')]
        checks=[]
        def check(name,ok):
            checks.append((name,bool(ok)))
            print('MC_OO_DOCKER_FAULTS '+name+' '+('PASS' if ok else 'FAIL'),flush=True)
            if not ok:raise AssertionError(name)
        try:
            lab.run_command(['docker','version','--format','{{.Server.Version}}'],timeout=15)
            lab.run_command([*compose,'config','--quiet'],timeout=30)
            lab.run_command([*compose,'up','-d','fixture','onlyoffice','proxy'],timeout=600)
            lab.audit_containers(compose,port)
            lab.wait_for_https(port,cert)
            # Authenticate through the actual private BootstrapAuthority.
            status,_,data=https_response(port,cert,lab.HOST_DEV,'GET','/auth/challenge')
            check('real-challenge-available',status==200)
            import hmac
            c=json.loads(data)
            msg=b'MC-AUTH-BOOTSTRAP-V1\n'+c['challenge_id'].encode()+b'\n'+c['nonce'].encode()+b'\n'
            sig=base64.urlsafe_b64encode(hmac.new(secret,msg,hashlib.sha256).digest()).decode().rstrip('=')
            status,headers,_=https_response(port,cert,lab.HOST_DEV,'POST','/auth/verify',
                                              {'challenge_id':c['challenge_id'],'signature':sig})
            check('real-session-issued',status==200)
            cookie=headers.get('Set-Cookie','').split(';',1)[0]
            status,_,_=https_response(port,cert,lab.HOST_DEV,'GET','/api/onlyoffice/config',cookie=cookie)
            check('authorized-config-before-fault',status==200)
            # Actual document server outage: proxy may return 502/503, but
            # MC's authentication and workbook bytes must not be affected.
            lab.run_command([*compose,'stop','onlyoffice'],timeout=120)
            # Verify the stopped service via Docker as well as the real HTTPS
            # proxy. A client read timeout alone is NOT evidence of a 502/503.
            inspect=lab.run_command([*compose,'ps','--status','running','--services'],timeout=20,capture=True)
            live_services=set(inspect.stdout.splitlines())
            check('documentserver-is-really-stopped', 'onlyoffice' not in live_services)
            started=time.monotonic()
            print('MC_OO_DOCKER_FAULTS outage-healthcheck START timeout_budget=10s',flush=True)
            try:
                st=lab.https_request(port,lab.HOST_DOCS,'/healthcheck',cert)
            except (OSError,TimeoutError,ssl.SSLError) as exc:
                raise RuntimeError('OUTAGE_HTTPS_RESPONSE_UNVERIFIED '+type(exc).__name__+
                    '; proxy must produce a real 502/503/504 within the bounded readiness timeout') from exc
            elapsed=time.monotonic()-started
            print(f'MC_OO_DOCKER_FAULTS outage-healthcheck response={st} elapsed={elapsed:.2f}s',flush=True)
            check('document-server-outage-fails-closed',st in (502,503,504) and elapsed<10)
            st=lab.https_request(port,lab.HOST_DEV,'/health',cert)
            check('mc-auth-backend-survives-document-server-outage',st==200)
            check('document-bytes-intact-during-outage',hashlib.sha256(workbook.read_bytes()).hexdigest()==original_sha)
            lab.run_command([*compose,'start','onlyoffice'],timeout=120)
            lab.wait_for_https(port,cert)
            lab.audit_containers(compose,port)
            check('document-bytes-intact-after-document-server-restart',
                  hashlib.sha256(workbook.read_bytes()).hexdigest()==original_sha)
            # Actual MC backend restart: auth state is on the disposable
            # container's writable layer and survives process restart.
            lab.run_command([*compose,'restart','fixture'],timeout=120)
            until=time.monotonic()+40
            status=0
            while time.monotonic()<until:
                try:status,_,_=https_response(port,cert,lab.HOST_DEV,'GET','/health')
                except (OSError,ssl.SSLError,TimeoutError,http.client.HTTPException):status=0
                if status==200:break
                time.sleep(1)
            check('mc-backend-returns-after-process-restart',status==200)
            status,_,_=https_response(port,cert,lab.HOST_DEV,'GET','/api/onlyoffice/config',cookie=cookie)
            check('same-session-survives-fixture-process-restart',status==200)
            status,_,_=https_response(port,cert,lab.HOST_DEV,'GET','/api/onlyoffice/config')
            check('anonymous-still-denied-after-process-restart',status==401)
            status,_,_=https_response(port,cert,lab.HOST_DEV,'GET','/api/onlyoffice/file?path=real-edit.xlsx')
            check('anonymous-download-still-denied-after-restart',status==403)
            check('document-bytes-unchanged-after-both-service-restarts',
                  hashlib.sha256(workbook.read_bytes()).hexdigest()==original_sha)
            print('MC_OO_DOCKER_FAULTS SUMMARY '+json.dumps({
                'status':'PASS','checks':len(checks),'passed':len(checks),
                'production_modified':False,'faults':['documentserver-stop-start','fixture-process-restart'],
                'live_editor_mid_save_fault':False},sort_keys=True),flush=True)
            outcome=0
        except KeyboardInterrupt:
            print('MC_OO_DOCKER_FAULTS SUMMARY '+json.dumps({'status':'INTERRUPTED','production_modified':False}),flush=True)
            outcome=130
        except (AssertionError,Exception) as exc:
            print('MC_OO_DOCKER_FAULTS SUMMARY '+json.dumps({
                'status':'FAIL','reason':type(exc).__name__,
                'detail':str(exc)[:180], 'checks':checks,'production_modified':False},sort_keys=True),flush=True)
            outcome=1
        finally:
            try:
                lab.run_command([*compose,'down','-v','--remove-orphans'],timeout=180)
                print('MC_OO_DOCKER_FAULTS docker-disposable-cleanup PASS',flush=True)
            except Exception as exc:
                print('MC_OO_DOCKER_FAULTS docker-disposable-cleanup FAIL '+repr(exc)[:180],flush=True)
                outcome=2
    return outcome


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',nargs='?',default='run',choices=['run'])
    p.add_argument('--onlyoffice-tag',default=os.environ.get('MAIN_COMPUTER_ONLYOFFICE_IMAGE_TAG','latest'))
    args=p.parse_args(argv)
    return experiment(args.onlyoffice_tag)

if __name__=='__main__':raise SystemExit(main())
