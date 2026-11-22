"""Offline tests of the Docker lab harness; no Docker or production ports needed."""
import io
import os
import time
import tempfile
import zipfile
from pathlib import Path
from unittest import mock
from urllib.request import Request
from urllib.error import HTTPError
from tools import mcel_onlyoffice_docker_fixture as fx
from tools import mcel_onlyoffice_docker_lab as lab


def run():
    assert 'onlyoffice' in lab.compose_spec('latest','A'*40,Path(__file__).resolve(),Path(__file__).resolve(),Path(__file__).resolve())['services']
    obj=lab.compose_spec('latest','A'*40,Path(__file__).resolve(),Path(__file__).resolve(),Path(__file__).resolve())
    assert obj['networks']['sandbox']['internal']
    assert all('ports' not in svc for svc in obj['services'].values())
    bob=obj['services']['bob']
    assert bob['environment'] == {} and bob['user'] == '65534:65534'
    assert bob['read_only'] is True and bob['cap_drop'] == ['ALL']
    assert not any('LAB_SECRET' in str(v) or '/work' in str(v) for v in bob['volumes'])
    print('OFFLINE_DOCKER_LAB no-exposed-ports-and-isolated-network PASS')
    print('OFFLINE_DOCKER_LAB credentialless-bob-container-contract PASS')
    os.environ['LAB_SECRET']='test-secret-only-for-offline-unit-test-123456789'
    fx.SECRET=os.environ['LAB_SECRET']
    token=fx.capability('fixture.xlsx',int(time.time())+120)
    assert fx.valid_capability(token,'fixture.xlsx')
    assert not fx.valid_capability(token,'another.xlsx')
    assert not fx.valid_capability(fx.capability('fixture.xlsx',int(time.time())-5),'fixture.xlsx')
    assert not fx.valid_capability('invalid','fixture.xlsx')
    print('OFFLINE_DOCKER_LAB scoped-capability PASS')
    try:
        fx.restricted_urlopen(Request('http://rogue:8000/download/fixture.xlsx'),timeout=1)
        raise AssertionError('rogue URL accepted')
    except ValueError:
        pass
    print('OFFLINE_DOCKER_LAB rogue-URL-refusal PASS')
    with tempfile.TemporaryDirectory() as d:
        os.environ['LAB_SOURCE_PATH']=str(Path(__file__).resolve().parents[1]/'main_computer'/'viewport_routes_onlyoffice.py')
        scope=fx.load_actual_handler()
        handler=scope['ActualHandler']()
        handler.path='/api/applications/onlyoffice/callback?path=fixture.xlsx'
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return fx.workbook_bytes()
        with mock.patch.dict(scope,{'urlopen':lambda *a,**kw:Response()}):
            handler._read_json=lambda:{'status':2,'url':'http://rogue:8000/download/fixture.xlsx'}
            handler._onlyoffice_safe_path=lambda *args,**kw:Path(d)/'file.xlsx'
            handler.server=type('FakeServer',(),{'signal':lambda *args,**kw:None})()
            records=[]
            handler._onlyoffice_atomic_write=lambda path,data:records.append((path,data))
            handler._send_json=lambda payload,status=200: records.append(('status',int(status)))
            handler._handle_onlyoffice_callback()
            assert len(records)==2 and records[0][1]==fx.workbook_bytes()
            assert records[1]==('status',200)
        print('OFFLINE_DOCKER_LAB actual-original-handler-reproduces-unsafe-fetch PASS')



def test_document_download_security() -> None:
    """Real local HTTP source: deny anonymous/forged; allow scoped token.

    This tests the same fixture handler that will run in Docker without needing
    Docker or granting any access to the user's MC data.
    """
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.parse import urlsplit

    fx.SECRET = 'test-secret-only-for-offline-unit-test-123456789'
    now = int(time.time())
    signed = fx.download_capability('/download/fixture.xlsx', now + 100)
    assert fx.valid_download_capability(signed, '/download/fixture.xlsx')
    assert not fx.valid_download_capability(signed, '/download/other.xlsx')
    assert not fx.valid_download_capability(fx.capability('fixture.xlsx', now + 100), '/download/fixture.xlsx')
    assert not fx.valid_capability(signed, 'fixture.xlsx')
    assert not fx.valid_download_capability(fx.download_capability('/download/fixture.xlsx', now - 1), '/download/fixture.xlsx')
    assert not fx.download_query_allowed(urlsplit('/download/fixture.xlsx?cap=forged&cap=' + signed))
    assert not fx.download_query_allowed(urlsplit('/download/fixture.xlsx?cap=' + signed + '&other=1'))
    assert not fx.download_query_allowed(urlsplit('/download/fixture.xlsx?garbage'))
    with mock.patch.object(fx, 'build_opener') as opener:
        fx.restricted_urlopen(Request(fx.signed_download_url('/download/fixture.xlsx', now + 100)))
        assert opener.return_value.open.call_count == 1
        for bad in ('http://document:8000/download/fixture.xlsx',
                    'http://document:8000/download/fixture.xlsx?cap=forged',
                    fx.signed_download_url('/download/fixture.xlsx', now + 100).replace('document:8000','rogue:8000')):
            try:
                fx.restricted_urlopen(Request(bad))
            except ValueError:
                pass
            else:
                raise AssertionError('unsafe candidate download accepted')
        assert opener.return_value.open.call_count == 1
    print('OFFLINE_DOCKER_LAB purpose-separated-document-capabilities PASS')
    print('OFFLINE_DOCKER_LAB strict-duplicate-and-ambiguous-token-rejection PASS')

    with mock.patch.dict(os.environ, {'LAB_ROLE': 'document'}):
        server = ThreadingHTTPServer(('127.0.0.1', 0), fx.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        endpoint = f'http://127.0.0.1:{server.server_port}/download/fixture.xlsx'
        def get(path):
            try:
                with __import__('urllib.request', fromlist=['urlopen']).urlopen(path, timeout=3) as r:
                    return r.status, r.read()
            except HTTPError as exc:
                return exc.code, exc.read()
        try:
            initial = fx.COUNTS['fetches']
            for suffix in ('', '?cap=forged', '?cap=' + fx.capability('fixture.xlsx',now+100),
                           '?cap=' + fx.download_capability('/download/fixture.xlsx',now-1),
                           '?cap=' + fx.download_capability('/download/other.xlsx',now+100),
                           '?cap=' + signed + '&cap=forged', '?cap=' + signed + '&other=1', '?garbage'):
                status, raw = get(endpoint + suffix)
                assert status == 403, (suffix[:10], status)
                assert not raw.startswith(b'PK')
            assert fx.COUNTS['fetches'] == initial
            print('OFFLINE_DOCKER_LAB credentialless-and-forged-document-HTTP-denial PASS')
            status, raw = get(endpoint + '?cap=' + signed)
            assert status == 200 and raw == fx.workbook_bytes()
            assert fx.COUNTS['fetches'] == initial + 1
            print('OFFLINE_DOCKER_LAB authorized-document-HTTP-retrieval PASS')
            # Deliberate regression: insecure source serves the workbook, and
            # the same acceptance check must recognize the bypass.
            with mock.patch.object(fx, 'download_query_allowed', lambda parsed: True):
                status, raw = get(endpoint)
                assert status == 200 and raw.startswith(b'PK')
                print('OFFLINE_DOCKER_LAB deliberately-broken-download-guard-detected PASS')
            status, _ = get(endpoint)
            assert status == 403
            print('OFFLINE_DOCKER_LAB reverted-guard-still-denies PASS')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    run()
    test_document_download_security()
