import json
from email.message import Message
from urllib.error import HTTPError

import pytest
from tools.worker_browser_acceptance.rpc_origin_preflight import BROWSER_STYLE_UA, request_check, run


class _Response:
    status = 200
    def __init__(self, headers, contents=b''):
        self.headers = Message()
        for k, v in headers.items():
            self.headers[k] = v
        self.contents = contents
    def read(self, limit):
        return self.contents[:limit]
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class _Opener:
    def __init__(self):
        self.requests = []
    def open(self, req, timeout):
        assert timeout == 12
        assert req.full_url == 'https://testnet-rpc.greatlibrary.io/'
        assert req.get_method() in ('OPTIONS', 'POST')
        origin = req.get_header('Origin')
        self.requests.append((origin, req.get_method(), req.get_header('User-agent')))
        if origin and origin.startswith('chrome-extension:'):
            # Reproduce a CORS-denied origin without inventing a 403 diagnosis.
            raise HTTPError(req.full_url, 403, 'denied', Message(), None)
        return _Response({'Access-Control-Allow-Origin': origin,
                          'Access-Control-Allow-Methods':'POST, OPTIONS',
                          'Access-Control-Allow-Headers': 'content-type'},
                         json.dumps({'result':'0x28757b1'}).encode())


def test_compares_specific_origins_with_pinned_methods():
    opener = _Opener()
    output = run('a'*32, opener)
    assert output['origins']['workerPage']['preflight']['status'] == 200
    assert output['origins']['workerPage']['chainPost']['chainProof'] == 'PASS'
    assert output['origins']['extension']['preflight']['status'] == 403
    assert output['origins']['extension']['preflight']['allowOrigin'] == 'missing'
    assert len(opener.requests) == 7
    assert output['clientHeaderControls']['noOriginPost']['chainProof'] == 'PASS'
    assert output['clientHeaderControls']['workerOriginBrowserStylePost']['status'] == 200
    assert output['clientHeaderControls']['extensionOriginBrowserStylePost']['status'] == 403
    assert opener.requests[-2][2] == BROWSER_STYLE_UA


def test_no_untrusted_origin_or_mutating_method():
    with pytest.raises(ValueError):
        run('evil')
    with pytest.raises(ValueError):
        request_check('https://evil.example', 'OPTIONS')
    with pytest.raises(ValueError):
        request_check('http://127.0.0.1:8765', 'PUT')


def test_does_not_follow_redirects_or_record_response_body():
    response = _Response({'Access-Control-Allow-Origin': '*'}, b'SECRET_DATA')
    class Fake:
        def open(self, request, timeout):
            return response
    result = request_check('http://127.0.0.1:8765', 'POST', Fake())
    assert 'SECRET_DATA' not in str(result)
    assert result['allowOrigin'] == 'wildcard'
    assert result['chainProof'] == 'MALFORMED'


def test_records_only_safe_edge_markers_and_does_not_log_denied_body():
    class Denied:
        def open(self, req, timeout):
            headers = Message()
            headers['Server'] = 'cloudflare'
            headers['CF-Ray'] = '1234abcd9988ef00-PDX'
            headers['CF-Mitigated'] = 'challenge'
            import io
            raise HTTPError(req.full_url, 403, 'denied', headers,
                            io.BytesIO(b'<html>Error 1010: SECRET-RECOVERY-PHRASE</html>'))
    result = request_check('http://127.0.0.1:8765', 'POST', Denied())
    assert result['server'] == 'cloudflare'
    assert result['cfRay'] == '1234abcd9988ef00-PDX'
    assert result['edgeChallenge'] is True
    assert result['edgeErrorCode'] == '1010'
    assert 'SECRET' not in str(result)


def test_explicit_header_control_keeps_origin_and_body_identical():
    class Tracking:
        requests = []
        def open(self, req, timeout):
            self.requests.append((req.get_header('Origin'),
                                  req.get_header('User-agent'), req.data))
            return _Response({'Server': 'something'}, b'{"jsonrpc":"2.0","result":"0x28757b1"}')
    transport = Tracking()
    origin = 'chrome-extension://' + 'd' * 32
    request_check(origin, 'POST', transport)
    request_check(origin, 'POST', transport, user_agent=BROWSER_STYLE_UA)
    assert transport.requests[0][0] == transport.requests[1][0]
    assert transport.requests[0][2] == transport.requests[1][2]
    assert transport.requests[1][1] == BROWSER_STYLE_UA
    with pytest.raises(ValueError):
        request_check(origin, 'POST', transport, user_agent='UntrustedHeader')
