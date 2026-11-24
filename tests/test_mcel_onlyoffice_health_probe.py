"""Source-backed transport regression: browser DNS must never gate backend health."""
import ast
import http.client
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
API = '/web-apps/apps/api/documents/api.js'
PUBLIC = 'https://onlyoffice.main-computer.localhost:18086'


def handler_source():
    source = ROOT/'main_computer/viewport_routes_onlyoffice.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ViewportOnlyOfficeRoutesMixin')
    names = {'_handle_onlyoffice_status', '_onlyoffice_probe_url', '_onlyoffice_status_payload'}
    methods = {n.name:n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names}
    assert set(methods) == names
    module = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='SourceHandler', bases=[], keywords=[], body=list(methods.values()), decorator_list=[])], type_ignores=[]))
    scope = dict(http=http, HTTPStatus=HTTPStatus, time=time, Request=Request, urlopen=urlopen, urlsplit=urlsplit, Any=object)
    exec(compile(module, str(source), 'exec'), scope)
    return scope['SourceHandler']()


class ResponseHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.paths.append(self.path)
        self.send_response(self.server.response_code)
        if self.server.response_code == 302:
            self.send_header('Location', 'http://127.0.0.1:1/never')
        self.end_headers()
        self.wfile.write(b'/* API script */')
    def log_message(self, *_): pass


class HealthProbe(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),ResponseHandler)
        self.http.paths=[];self.http.response_code=200
        thread=threading.Thread(target=self.http.serve_forever,daemon=True);thread.start()
        self.addCleanup(self.http.server_close); self.addCleanup(self.http.shutdown)
        self.internal=f'http://127.0.0.1:{self.http.server_port}'
        self.handler=handler_source(); h=self.handler
        h.server=SimpleNamespace(config=SimpleNamespace(onlyoffice_enabled=True,onlyoffice_jwt_secret='test'),signal=lambda *a,**kw:None)
        h._onlyoffice_root=lambda:Path(self.tmp.name)
        h._onlyoffice_public_url=lambda:PUBLIC
        h._onlyoffice_browser_public_url_override=lambda:PUBLIC
        h._onlyoffice_browser_document_server_url=lambda:PUBLIC
        h._onlyoffice_internal_url=lambda:self.internal
        h._onlyoffice_callback_base_url=lambda:'http://host.docker.internal:8765'
        h._onlyoffice_browser_public_url_candidates=lambda:[PUBLIC]
        h._onlyoffice_jwt_enabled=lambda:True
        h._onlyoffice_effective_mode=lambda:'docker'
        h._coerce_bool=lambda x,default=False:bool(x)
        self.body={'probe':True,'timeout_s':2}
        h._read_json=lambda:self.body
        self.sent=[];h._send_json=lambda value,status=200:self.sent.append((int(status),value))
        env=patch.dict(os.environ,{'MAIN_COMPUTER_HARDENED_CANDIDATE':'1'});env.start();self.addCleanup(env.stop)
    def status(self):
        self.handler._handle_onlyoffice_status()
        status,value=self.sent[-1];self.assertEqual(status,200,value);return value
    def test_real_loopback_and_distinct_browser_url(self):
        data=self.status();self.assertTrue(data['api_reachable']);self.assertEqual(data['server_probe']['url'],self.internal+API)
        self.assertEqual(data['api_url'],PUBLIC+API);self.assertEqual(data['public_url'],PUBLIC)
        self.assertEqual(data['browser_probe'],{'checked':False,'ok':None,'url':PUBLIC+API})
        self.assertEqual(self.http.paths,[API])
    def test_redirect_not_followed(self):
        self.http.response_code=302; data=self.status();self.assertFalse(data['api_reachable']);self.assertEqual(data['server_probe']['status'],302);self.assertEqual(self.http.paths,[API])
    def test_unavailable_server_reports_failure(self):
        self.http.response_code=503;data=self.status();self.assertFalse(data['api_reachable']);self.assertEqual(data['api_error'],'HTTP 503')
    def test_no_probe_no_network(self):
        self.body={'probe':False};data=self.status();self.assertIsNone(data['api_reachable']);self.assertFalse(data['server_probe']['checked']);self.assertEqual(self.http.paths,[])
    def test_nonloopback_rejected(self):
        self.handler._onlyoffice_internal_url=lambda:PUBLIC
        with patch('http.client.HTTPConnection') as connection:
            self.assertFalse(self.status()['api_reachable']);connection.assert_not_called()
    def test_other_status_payload_same_transport(self):
        data=self.handler._onlyoffice_status_payload();self.assertTrue(data['server_probe']['ok']);self.assertEqual(data['server_probe']['url'],self.internal+API);self.assertEqual(self.http.paths,[API])

if __name__=='__main__':unittest.main()
