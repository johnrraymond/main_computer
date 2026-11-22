"""Credentialless adversary in the ONLYOFFICE disposable Docker network.

Does not carry LAB_SECRET, source mounts or Docker daemon access. Exercises
callback authentication and detects whether the simulated document download
source is directly readable by an uncredentialed network client. Detection of
such a read is a lab-level finding, not a claim about production Docker ports.
"""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def call(url: str, data: dict | None=None, headers: dict | None=None):
    payload=json.dumps(data).encode() if data is not None else None
    request=Request(url,data=payload,headers={'Content-Type':'application/json',**(headers or {})})
    try:
        with urlopen(request,timeout=5) as response:
            return response.status,response.read(4096)
    except HTTPError as exc:
        return exc.code,exc.read(4096)


def main() -> int:
    import os
    if os.environ.get('LAB_SECRET'):
        print('ONLYOFFICE_BOB FAIL unexpected-access-to-secret',flush=True)
        return 2
    checks=[]
    base='http://secured:8000/api/applications/onlyoffice/callback?path=fixture.xlsx'
    variants=[
        ('anonymous-write',{'status':2,'url':'http://rogue:8000/download/fixture.xlsx'},{}),
        ('anonymous-status',{'status':1},{}),
        ('forged-write',{'status':2,'url':'http://document:8000/download/fixture.xlsx'}, {'X-MC-Document-Capability':'forged'}),
        ('cross-document',{'status':2,'url':'http://document:8000/download/fixture.xlsx'}, {'X-MC-Document-Capability':'9999999999.'+'0'*64}),
    ]
    for name,body,headers in variants:
        try:status,_=call(base,body,headers)
        except Exception as exc:status=-1
        ok=status in {401,403}
        checks.append((name,ok))
        print(f'ONLYOFFICE_BOB {name} {"PASS" if ok else "FAIL"} HTTP={status}',flush=True)
    # A separate direct-document GET is an intentionally adversarial probe:
    # it cannot be ruled out merely by signing MC callback requests.
    try:status,payload=call('http://document:8000/download/fixture.xlsx')
    except Exception:status,payload=-1,b''
    content_is_xlsx = payload.startswith(b'PK')
    ok=status in (401,403)
    checks.append(('uncredentialed-document-download-denied',ok))
    print(f'ONLYOFFICE_BOB uncredentialed-document-download-denied {"PASS" if ok else "FAIL"} HTTP={status} xlsx={content_is_xlsx}',flush=True)
    # Bob cannot forge a download query token, borrow a callback token, or
    # exploit URL-parser ambiguity. These requests carry no real credential.
    additional = [
        ('forged-document-download-denied', 'http://document:8000/download/fixture.xlsx?cap=forged'),
        ('duplicate-document-capability-denied', 'http://document:8000/download/fixture.xlsx?cap=forged&cap=other'),
        ('other-document-path-denied', 'http://document:8000/download/other.xlsx?cap=forged'),
        ('unsigned-document-redirect-denied', 'http://document:8000/redirect'),
    ]
    for name,url in additional:
        try:code,body=call(url)
        except Exception:code,body=-1,b''
        ok=code in (401,403,404) and not body.startswith(b'PK')
        checks.append((name,ok))
        print(f'ONLYOFFICE_BOB {name} {"PASS" if ok else "FAIL"} HTTP={code}',flush=True)
    failed=[name for name,ok in checks if not ok]
    print('ONLYOFFICE_BOB SUMMARY '+json.dumps({'status':'PASS' if not failed else 'FAIL',
        'failed':failed,'checks':len(checks),'production_modified':False,
        'scope':'isolated fixture network; document source represents simulated exported file, not production ONLYOFFICE'}),flush=True)
    return 1 if failed else 0


if __name__=='__main__':
    raise SystemExit(main())
