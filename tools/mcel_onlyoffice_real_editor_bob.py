"""Credentialless adversarial client for the disposable real-editor ONLYOFFICE lab."""
from __future__ import annotations
import http.client
import json
import os


def request(method, path, body=None, headers=None):
    con = http.client.HTTPConnection('fixture', 8000, timeout=8)
    try:
        payload=json.dumps(body).encode() if body is not None else None
        con.request(method,path,body=payload,headers={'Host':'fixture:'+os.environ.get('LAB_PORT','8000'),'Content-Type':'application/json',**(headers or {})})
        response=con.getresponse()
        return response.status,response.read(8192)
    finally:con.close()


def main():
    outcomes=[]
    primary_host='dev.main-computer.local:'+os.environ['LAB_BROWSER_PORT']
    cases=[
      # Redirect is only served from MC's canonical host. The internal
      # fixture hostname is intentionally denied, even without a session.
      ('anonymous-graphical-redirect','GET','/graphical',None,{'Host':primary_host},(303,)),
      ('internal-fixture-host-cannot-render-graphical','GET','/graphical',None,{},(403,)),
      ('anonymous-editor-config-denied','GET','/api/onlyoffice/config',None,{},(401,403)),
      ('anonymous-file-download-denied','GET','/api/onlyoffice/file?mode=dev&doc=real-edit.xlsx',None,{},(401,403)),
      ('forged-file-download-denied','GET','/api/onlyoffice/file?mode=dev&doc=real-edit.xlsx&cap=forged',None,{},(401,403)),
      ('other-document-denied','GET','/api/onlyoffice/file?mode=dev&doc=other.xlsx&cap=forged',None,{},(401,403,404)),
      ('anonymous-save-callback-denied','POST','/api/onlyoffice/callback?mode=dev&doc=real-edit.xlsx',{'status':2,'url':'http://evil.example/'},{},(401,403)),
      ('forged-callback-capability-denied','POST','/api/onlyoffice/callback?mode=dev&doc=real-edit.xlsx&path=real-edit.xlsx&cap=forged',{'status':2,'url':'http://evil.example/'},{},(401,403)),
      ('forged-origin-does-not-authorize-config','GET','/api/onlyoffice/config',None,{'Origin':'https://dev.main-computer.local:8765','X-Forwarded-User':'owner'},(401,403)),
      ('foreign-host-denied','GET','/api/onlyoffice/config',None,{'Host':'unknown.main-computer.local'},(401,403)),
      ('no-editor-launch-ticket','GET','/mc-editor',None,{'Host':'onlyoffice.main-computer.local:'+os.environ.get('LAB_BROWSER_PORT','0')},(401,403)),
      ('no-editor-config-ticket','GET','/mc-config',None,{'Host':'onlyoffice.main-computer.local:'+os.environ.get('LAB_BROWSER_PORT','0')},(401,403)),
    ]
    for label,method,path,body,headers,expected in cases:
        try:
            status,_=request(method,path,body,headers)
            ok=status in expected
        except Exception as exc:
            status=type(exc).__name__;ok=False
        print(f'MC_ONLYOFFICE_REAL_BOB {label} {"PASS" if ok else "FAIL"} observed={status}',flush=True)
        outcomes.append(ok)
    print('MC_ONLYOFFICE_REAL_BOB SUMMARY '+json.dumps({'checks':len(outcomes),'passed':sum(outcomes),'failed':len(outcomes)-sum(outcomes),'production_modified':False}),flush=True)
    return 0 if all(outcomes) else 1

if __name__=='__main__':raise SystemExit(main())
