"""In-network client for the disposable ONLYOFFICE Docker attack lab."""
from __future__ import annotations

import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def req(url, data=None, headers=None):
    payload = json.dumps(data).encode() if data is not None else None
    request = Request(url, data=payload, headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urlopen(request, timeout=8) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw.startswith(b'{') else raw.decode(errors='replace')
    except HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw.startswith(b'{') else raw.decode(errors='replace')


def wait_for_documentserver_health(attempts=75, delay_seconds=2, *, fetch=None, sleep=None):
    """Wait through transient 502/503 startup responses, never certify on error.

    The final failure includes the last actual HTTP/network observation.  This
    helper is injectable for fast, deterministic tests without Docker.
    """
    if fetch is None:
        fetch = urlopen
    if sleep is None:
        sleep = time.sleep
    if attempts < 1:
        raise ValueError('at least one healthcheck attempt is required')
    last_detail = 'no successful healthcheck'
    for attempt in range(1, attempts + 1):
        try:
            with fetch('http://onlyoffice/healthcheck', timeout=4) as response:
                body = response.read(128).strip().lower()
                if response.status == 200 and body == b'true':
                    return True, attempt, 'ready'
                last_detail = f'HTTP {response.status}, body={body[:48]!r}'
        except HTTPError as exc:
            last_detail = f'HTTP {exc.code}'
            exc.close()
        except (URLError, TimeoutError, OSError) as exc:
            last_detail = f'{type(exc).__name__}: {str(exc)[:100]}'
        if attempt in (1, 10, 30, 60) and attempt < attempts:
            print(f'ONLYOFFICE_DOCKER_LAB documentserver-starting RETRY '
                  f'attempt={attempt}/{attempts} last={last_detail}', flush=True)
        if attempt < attempts:
            sleep(delay_seconds)
    return False, attempts, last_detail


def main(capability):
    failure = []
    checks = []

    def check(name, ok, detail=''):
        checks.append((name, bool(ok)))
        print(f'ONLYOFFICE_DOCKER_LAB {name} {"PASS" if ok else "FAIL"}' + (f' detail={detail}' if detail else ''), flush=True)
        if not ok: failure.append(name)

    def metrics(name):
        code, obj = req(f'http://{name}:8000/metrics')
        assert code == 200, (name, code)
        return obj

    # Document Server commonly returns HTTP 502 until its internal components
    # finish initializing. This is a *retryable readiness state*, not a Python
    # exception or proof of an insecure/security-correct server.
    available, checks_used, last_detail = wait_for_documentserver_health()
    check('actual-onlyoffice-documentserver-health', available,
          f'attempts={checks_used} last={last_detail}')
    if not available:
        print('ONLYOFFICE_DOCKER_LAB READINESS_BLOCKED '
              'real Document Server never returned HTTP 200 true', flush=True)
        return 1

    for name in ('baseline','secured','document','rogue'):
        ok = False
        for _ in range(15):
            try:
                code, _ = req(f'http://{name}:8000/health')
                ok = code == 200
                if ok:break
            except (URLError,TimeoutError):
                pass
            time.sleep(1)
        check('container-' + name + '-ready', ok)
        if not ok:return 1

    baseline_url = 'http://baseline:8000/api/applications/onlyoffice/callback?path=fixture.xlsx'
    secured_url = 'http://secured:8000/api/applications/onlyoffice/callback?path=fixture.xlsx'
    bad = {'status':2,'url':'http://rogue:8000/download/fixture.xlsx'}
    from tools.mcel_onlyoffice_docker_fixture import signed_download_url, download_capability
    # ONLYOFFICE can fetch a signed file URL without sharing MC's callback key.
    signed_url = signed_download_url('/download/fixture.xlsx', int(time.time()) + 100)
    good = {'status':2,'url':signed_url}
    before_rogue = metrics('rogue')['fetches']
    status, _ = req(baseline_url, bad)
    check('unpatched-handler-anonymous-ssrf-reproduced', status == 200 and metrics('rogue')['fetches'] > before_rogue and metrics('baseline')['writes'] == 1)
    before_rogue = metrics('rogue')['fetches']
    status, _ = req(secured_url, bad)
    check('candidate-anonymous-ssrf-denied', status in (401,403) and metrics('rogue')['fetches'] == before_rogue and metrics('secured')['writes'] == 0)
    status,_=req(secured_url, {'status':1})
    check('candidate-anonymous-status-only-denied', status in (401,403))
    status,_=req(secured_url, good, {'X-MC-Document-Capability':'wrong'})
    check('candidate-forged-capability-denied', status in (401,403))
    token = capability('fixture.xlsx',int(time.time())+100)
    doc_count_before = metrics('document')['fetches']
    # Real HTTP checks of the independently protected document-source route.
    # HTTPError is imported at module scope; an import inside main() would
    # shadow it for the entire function and crash the earlier health probe.
    def direct(url):
        try:
            with urlopen(url, timeout=5) as response:
                return response.status, response.read()
        except HTTPError as exc:
            return exc.code, exc.read()
    status,_ = direct('http://document:8000/download/fixture.xlsx')
    check('candidate-document-source-anonymous-denied', status in (401,403)
          and metrics('document')['fetches'] == doc_count_before)
    status,_ = direct('http://document:8000/download/fixture.xlsx?cap=forged')
    check('candidate-document-source-forged-denied', status in (401,403))
    status,_ = direct('http://document:8000/download/fixture.xlsx?cap=' + capability('fixture.xlsx',int(time.time())+100))
    check('candidate-callback-token-cannot-download', status in (401,403))
    status,_ = direct('http://document:8000/download/fixture.xlsx?cap=' + download_capability('/download/fixture.xlsx',int(time.time())-3))
    check('candidate-expired-download-denied', status in (401,403))
    status,_ = direct('http://document:8000/download/fixture.xlsx?cap=' + download_capability('/download/other.xlsx',int(time.time())+100))
    check('candidate-cross-document-download-denied', status in (401,403))
    status,_ = direct(signed_url + '&cap=forged')
    check('candidate-duplicate-download-token-denied', status in (401,403))
    status,data = direct(signed_url)
    check('candidate-authorized-download-works', status == 200 and data.startswith(b'PK')
          and metrics('document')['fetches'] == doc_count_before + 1)
    status,_=req(secured_url, bad, {'X-MC-Document-Capability':token})
    check('candidate-valid-capability-bad-download-rejected', status != 200 and metrics('rogue')['fetches'] == before_rogue)
    status,_=req(secured_url,good, {'X-MC-Document-Capability':token})
    check('candidate-authenticated-save-works', status == 200 and metrics('secured')['writes'] == 1 and metrics('secured')['written']
          and metrics('document')['fetches'] == doc_count_before + 2)
    status,_=req(secured_url, {'status':2,'url':signed_download_url('/redirect',int(time.time())+100)}, {'X-MC-Document-Capability':token})
    check('candidate-redirect-ssrf-rejected', status != 200 and metrics('rogue')['fetches'] == before_rogue)
    expired=capability('fixture.xlsx',int(time.time())-1)
    status,_=req(secured_url,good, {'X-MC-Document-Capability':expired})
    check('candidate-expired-capability-denied', status in (401,403))
    wrong_scope=capability('different.xlsx',int(time.time())+100)
    status,_=req(secured_url,good, {'X-MC-Document-Capability':wrong_scope})
    check('candidate-other-document-capability-denied', status in (401,403))
    status,_=req(secured_url, {'status':1}, {'X-MC-Document-Capability':token})
    check('candidate-authenticated-status-callback-works', status == 200)

    summary={'status':'PASS' if not failure else 'BLOCKED','tests':len(checks),'failed':failure,
             'production_modified':False,'documentserver_real_callback_tested':False}
    print('ONLYOFFICE_DOCKER_LAB SUMMARY '+json.dumps(summary,sort_keys=True),flush=True)
    return 0 if not failure else 1


if __name__ == '__main__':
    from tools.mcel_onlyoffice_docker_fixture import capability
    raise SystemExit(main(capability))
