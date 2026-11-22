"""Disposable Docker-only ONLYOFFICE ingress security laboratory fixture.

Not a production HTTP service or authentication implementation. This deliberately
executes the unchanged repository callback handler inside an isolated container,
with a candidate gate added only to the secured fixture. No MC user files are
mounted, and the Document Server and probe containers publish no host ports.
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import io
import json
import os
import secrets
import sys
import time
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, urlopen

PORT = 8000
SECRET = os.environ.get('LAB_SECRET', '')
DOCUMENT_KEY = 'fixture-document-v1'
WORKBOOK = Path('/tmp/fixture.xlsx')
MAX_DOWNLOAD = 2 * 1024 * 1024


def workbook_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr('xl/workbook.xml', '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>')
    return buf.getvalue()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BoundedResponse:
    def __init__(self, response):
        self.response = response
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return self.response.__exit__(*args)
    def read(self):
        data = self.response.read(MAX_DOWNLOAD + 1)
        if len(data) > MAX_DOWNLOAD:
            raise ValueError('XLSX download exceeds allowed size')
        return data


def restricted_urlopen(req, *, timeout=5):
    target = urlsplit(req.full_url)
    # The simulator's 'document' service represents an approved ONLYOFFICE
    # document-download destination. The actual production allowlist must
    # resolve real Document Server addresses, redirects, and IP ranges safely.
    if (target.scheme, target.hostname, target.port) != ('http', 'document', PORT):
        raise ValueError('unapproved download destination')
    if target.username or target.password or target.fragment or target.path not in (
            '/download/fixture.xlsx', '/redirect'):
        raise ValueError('unapproved document path')
    if not download_query_allowed(target):
        raise ValueError('missing or invalid document download capability')
    opener = build_opener(NoRedirect())
    return BoundedResponse(opener.open(req, timeout=min(timeout, 5)))


def capability(path: str, exp: int, document_key: str = DOCUMENT_KEY) -> str:
    payload = f'v1|{path}|{document_key}|{exp}'
    sig = hmac.new(SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    return f'{exp}.{sig}'


def valid_capability(token: str, path: str) -> bool:
    try:
        exp_str, signature = token.split('.', 1)
        if len(exp_str) > 12 or not exp_str.isdecimal() or len(signature) != 64:
            return False
        exp = int(exp_str)
        if exp < int(time.time()) or exp > int(time.time()) + 180:
            return False
        return secrets.compare_digest(capability(path, exp), token)
    except (ValueError, TypeError):
        return False



def download_capability(path: str, exp: int) -> str:
    """Purpose- and document-scoped bearer URL for the disposable source.

    Real ONLYOFFICE document retrieval happens through a configured URL, not
    necessarily an application-defined request header. This fixture therefore
    uses a bounded query capability that never appears in metrics or logs.
    Callback capabilities cannot authorize downloads (or vice versa).
    """
    message = f'onlyoffice-fixture-download-v1|{DOCUMENT_KEY}|{path}|{exp}'
    digest = hmac.new(SECRET.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
    return f'{exp}.{digest}'


def valid_download_capability(token: str, path: str) -> bool:
    if not isinstance(token, str) or not isinstance(path, str):
        return False
    try:
        exp_str, sig = token.split('.', 1)
        if (not exp_str.isdecimal() or len(exp_str) > 12 or
                len(sig) != 64 or any(c not in '0123456789abcdef' for c in sig)):
            return False
        exp = int(exp_str)
        now = int(time.time())
        if exp < now or exp > now + 180:
            return False
        return secrets.compare_digest(download_capability(path, exp), token)
    except (ValueError, TypeError):
        return False


def signed_download_url(path: str, exp: int, *, target: str = 'document') -> str:
    # This utility is restricted to fixed fixture paths; never reflect a
    # caller-supplied hostname or arbitrary internal URL into a signed token.
    if path not in ('/download/fixture.xlsx', '/redirect') or target != 'document':
        raise ValueError('unapproved signed download target')
    return f'http://{target}:{PORT}{path}?cap={download_capability(path, exp)}'


def download_query_allowed(target) -> bool:
    # urllib.parse.parse_qs normally collapses duplicate parameter names.
    # Require exactly one parameter of exactly one value to prevent confusion.
    try:
        pairs = parse_qs(target.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    if set(pairs) != {'cap'} or len(pairs['cap']) != 1:
        return False
    return valid_download_capability(pairs['cap'][0], target.path)

def load_actual_handler():
    source = Path(os.environ.get('LAB_SOURCE_PATH', '/work/viewport_routes_onlyoffice.py'))
    tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ViewportOnlyOfficeRoutesMixin')
    names = {'_handle_onlyoffice_callback', '_onlyoffice_looks_like_xlsx'}
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(methods) == 2, 'Original ONLYOFFICE handler/helper not found'
    module = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='ActualHandler', bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[]))
    scope = {'parse_qs': parse_qs, 'urlsplit': urlsplit, 'Request': Request, 'urlopen': urlopen,
             'zipfile': zipfile, 'io': io, 'HTTPStatus': HTTPStatus}
    exec(compile(module, str(source), 'exec'), scope)
    return scope


COUNTS = {'callbacks': 0, 'fetches': 0, 'writes': 0, 'http_hits': 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status: int, obj: dict | bytes):
        if isinstance(obj, dict):
            data = json.dumps(obj, sort_keys=True).encode()
            content_type = 'application/json'
        else:
            data = obj
            content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        role = os.environ.get('LAB_ROLE')
        COUNTS['http_hits'] += 1
        if self.path == '/health':
            return self.reply(200, {'role': role})
        if self.path == '/metrics':
            return self.reply(200, dict(COUNTS, written=WORKBOOK.exists(), sha256=hashlib.sha256(WORKBOOK.read_bytes()).hexdigest() if WORKBOOK.exists() else None))
        target = urlsplit(self.path)
        if role in {'document', 'rogue'} and target.path == '/download/fixture.xlsx':
            # The rogue source stays intentionally vulnerable for the baseline
            # SSRF reproduction. The approved document source does not.
            if role == 'document' and not download_query_allowed(target):
                return self.reply(403, {'error': 'invalid document download capability'})
            COUNTS['fetches'] += 1
            return self.reply(200, workbook_bytes())
        if role == 'document' and target.path == '/redirect':
            if not download_query_allowed(target):
                return self.reply(403, {'error': 'invalid document download capability'})
            # A credentialed redirect must still be rejected by secured MC.
            self.send_response(302)
            self.send_header('Location', 'http://rogue:8000/download/fixture.xlsx')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        return self.reply(404, {'error': 'not found'})

    def do_POST(self):
        role = os.environ.get('LAB_ROLE')
        if role not in {'baseline', 'secured'}:
            return self.reply(404, {'error': 'not found'})
        COUNTS['callbacks'] += 1
        path = urlsplit(self.path)
        if path.path != '/api/applications/onlyoffice/callback':
            return self.reply(404, {'error': 'not found'})
        parsed = parse_qs(path.query)
        doc = parsed.get('path', [''])[0]
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 100_000:
                return self.reply(413, {'error': 'size'})
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                return self.reply(400, {'error': 'body'})
        except (ValueError, json.JSONDecodeError):
            return self.reply(400, {'error': 'invalid JSON'})
        if role == 'secured':
            if not valid_capability(self.headers.get('X-MC-Document-Capability', ''), doc):
                return self.reply(403, {'error': 'invalid document capability'})
            if doc != 'fixture.xlsx':
                return self.reply(403, {'error': 'document not permitted'})
            if body.get('status') in (2, 6) and not isinstance(body.get('url'), str):
                return self.reply(400, {'error': 'missing download URL'})
        scope = load_actual_handler()
        if role == 'secured':
            scope['urlopen'] = restricted_urlopen
        else:
            scope['urlopen'] = urlopen
        obj = scope['ActualHandler']()
        obj.path = self.path
        obj.server = SimpleNamespace(signal=lambda *a, **kw: None)
        obj._onlyoffice_safe_path = lambda name, **kw: WORKBOOK if name == 'fixture.xlsx' else (_ for _ in ()).throw(ValueError('invalid path'))
        obj._read_json = lambda: body
        def save(where, data):
            COUNTS['writes'] += 1
            where.write_bytes(data)
        obj._onlyoffice_atomic_write = save
        outcome = {}
        obj._send_json = lambda payload, status=HTTPStatus.OK: outcome.update(status=int(status), payload=payload)
        obj._handle_onlyoffice_callback()
        return self.reply(outcome.get('status', 500), outcome.get('payload', {'error': 'missing reply'}))


def serve(role: str):
    os.environ['LAB_ROLE'] = role
    if not SECRET or len(SECRET) < 32:
        raise RuntimeError('LAB_SECRET is missing or insecure')
    server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    server.serve_forever()


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {'baseline', 'secured', 'document', 'rogue', 'probe'}:
        raise SystemExit('role required: baseline|secured|document|rogue|probe')
    role = sys.argv[1]
    if role == 'probe':
        from tools import mcel_onlyoffice_docker_probe
        return mcel_onlyoffice_docker_probe.main(capability)
    return serve(role)


if __name__ == '__main__':
    raise SystemExit(main())
