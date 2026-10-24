"""Read-only, pinned RPC CORS preflight comparison. No credentials or mutations."""
from __future__ import annotations

import argparse
import json
import re
from urllib import error, request

RPC_URL = 'https://testnet-rpc.greatlibrary.io/'
EXPECTED_CHAIN = '0x28757b1'
EXTENSION_ID = re.compile(r'^[a-p]{32}$')
# Diagnostic *header* control only, not a browser or a Chrome TLS fingerprint.
# The same urllib transport and identical request body are retained.
BROWSER_STYLE_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36'
)
RAY_ID = re.compile(r'^[0-9a-fA-F]{8,32}-[A-Z]{3}$')
ERROR_CODE = re.compile(r'(?<!\d)(?:Error\s*|code\s*[:#]?\s*)(10(?:10|20))(?!\d)', re.I)



class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _access(result_headers, origin):
    allow_origin = result_headers.get('Access-Control-Allow-Origin', '')
    methods = result_headers.get('Access-Control-Allow-Methods', '')
    allowed_headers = result_headers.get('Access-Control-Allow-Headers', '')
    return {
        'allowOrigin': ('exact' if allow_origin == origin else
                        'wildcard' if allow_origin == '*' else
                        'missing' if not allow_origin else 'different'),
        'allowsPost': 'POST' in {s.strip().upper() for s in methods.split(',')},
        'allowsContentType': 'content-type' in {s.strip().lower() for s in allowed_headers.split(',')},
    }


def request_check(origin: str | None, method: str, opener=None, *, user_agent: str | None = None):
    if not (origin is None or origin == 'http://127.0.0.1:8765' or
            (origin.startswith('chrome-extension://') and
             EXTENSION_ID.fullmatch(origin[len('chrome-extension://'):]))):
        raise ValueError('Unsupported origin')
    if method not in ('OPTIONS', 'POST'):
        raise ValueError('Only read-only OPTIONS/JSON-RPC POST are permitted')
    headers = {'Origin': origin} if origin else {}
    if user_agent is not None:
        if user_agent != BROWSER_STYLE_UA:
            raise ValueError('Only the pinned diagnostic User-Agent is permitted')
        headers['User-Agent'] = user_agent
    body = None
    if method == 'OPTIONS':
        headers.update({'Access-Control-Request-Method': 'POST',
                        'Access-Control-Request-Headers': 'content-type'})
    else:
        headers['Content-Type'] = 'application/json'
        body = json.dumps({'jsonrpc': '2.0', 'id': 1,
                           'method': 'eth_chainId', 'params': []}).encode('ascii')
    req = request.Request(RPC_URL, data=body, headers=headers, method=method)
    opener = opener or request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=12) as response:
            status = response.status
            result_headers = response.headers
            content = response.read(8192) if status != 200 or method == 'POST' else b''
    except error.HTTPError as exc:
        status = exc.code
        result_headers = exc.headers
        try:
            content = exc.read(8192)  # inspect only a bounded error-page prefix
        except (OSError, ValueError):
            content = b''
        finally:
            exc.close()
    except (error.URLError, TimeoutError, OSError) as exc:
        # Do not serialize raw errors: proxies may include private environment data.
        return {'kind': 'transport-error', 'errorClass': type(exc).__name__}
    cors = _access(result_headers, origin)
    result = {'kind': 'http-response', 'status': status, **cors}
    # Identifiers/markers only. Never serialize the error page or arbitrary
    # response headers, which can contain private data.
    server = result_headers.get('Server', '').lower()
    result['server'] = 'cloudflare' if server == 'cloudflare' else 'other-or-absent'
    cf_ray = result_headers.get('CF-Ray', '')
    if RAY_ID.fullmatch(cf_ray):
        result['cfRay'] = cf_ray
    mitigation = result_headers.get('CF-Mitigated', '').lower()
    if mitigation == 'challenge':
        result['edgeChallenge'] = True
    if status >= 400 and content:
        evidence = ERROR_CODE.search(content.decode('utf-8', errors='replace')[:8192])
        if evidence:
            result['edgeErrorCode'] = evidence.group(1)
    if method == 'POST' and status == 200:
        try:
            payload = json.loads(content)
            result['chainProof'] = ('PASS' if payload.get('result', '').lower() == EXPECTED_CHAIN
                                    else 'WRONG_OR_INVALID')
        except (ValueError, TypeError, AttributeError):
            result['chainProof'] = 'MALFORMED'
    return result


def run(extension_id: str, opener=None):
    if not EXTENSION_ID.fullmatch(extension_id):
        raise ValueError('Expected one unpacked Chromium extension ID (32 letters a-p)')
    output = {'schema': 'main-computer.rpc-origin-preflight.v1',
              'endpoint': 'testnet-rpc.greatlibrary.io', 'extensionId': extension_id,
              'origins': {}}
    for name, origin in [('workerPage', 'http://127.0.0.1:8765'),
                         ('extension', f'chrome-extension://{extension_id}')]:
        output['origins'][name] = {'preflight': request_check(origin, 'OPTIONS', opener),
                                   'chainPost': request_check(origin, 'POST', opener)}
    # Identical urllib transport, request and Origin; change only the
    # User-Agent. A positive difference would establish sensitivity to this
    # header, not the real cause of MetaMask's Chrome-extension fetch failure.
    output['clientHeaderControls'] = {
        'description': 'Python urllib transport with a browser-style User-Agent header (not Chromium)',
        'noOriginPost': request_check(None, 'POST', opener),
        'workerOriginBrowserStylePost': request_check(
            'http://127.0.0.1:8765', 'POST', opener, user_agent=BROWSER_STYLE_UA),
        'extensionOriginBrowserStylePost': request_check(
            f'chrome-extension://{extension_id}', 'POST', opener, user_agent=BROWSER_STYLE_UA),
    }
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extension-id', required=True,
                        help='ID printed by python tools/worker_browser_acceptance.py rpc-probe')
    args = parser.parse_args()
    try:
        result = run(args.extension_id)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
