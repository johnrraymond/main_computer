"""Exercise actual candidate handler source with disposable document state.

No server is launched, and no production documents or ports are modified.
"""
from __future__ import annotations

import ast
import io
import mimetypes
import json
import os
from pathlib import Path
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import unittest
import zipfile

from main_computer.onlyoffice_secure_runtime import (
    active_key, grant, journal_for, shared_secret)

from test_mcel_onlyoffice_hardened_candidate import FakeHeaders, SECRET, jwt, workbook

SOURCE = Path(__file__).resolve().parents[1] / 'main_computer' / 'viewport_routes_onlyoffice.py'


def source_handlers():
    tree = ast.parse(SOURCE.read_text('utf-8'))
    cls = next(c for c in tree.body if isinstance(c, ast.ClassDef) and c.name == 'ViewportOnlyOfficeRoutesMixin')
    names = {'_handle_onlyoffice_file_get', '_handle_onlyoffice_callback', '_onlyoffice_looks_like_xlsx'}
    methods = [m for m in cls.body if isinstance(m, ast.FunctionDef) and m.name in names]
    assert len(methods) == 3
    module = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='SourceHandlers',
        bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[]))
    scope = {'parse_qs': parse_qs, 'urlsplit': urlsplit, 'HTTPStatus': HTTPStatus,
             'zipfile': zipfile, 'io': io, 'mimetypes': mimetypes}
    exec(compile(module, str(SOURCE), 'exec'), scope)
    return scope['SourceHandlers']


class ActualRouteCandidateTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory(prefix='mcel-oo-handler-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.book = self.root / 'Book.xlsx'
        self.book.write_bytes(workbook('original'))
        self.handler = source_handlers()()
        self.handler.server = SimpleNamespace(
            config=SimpleNamespace(onlyoffice_jwt_enabled=True, onlyoffice_jwt_secret='j'*32),
            signal=lambda *args, **kwargs: None)
        self.handler._onlyoffice_root = lambda: self.root
        self.handler._onlyoffice_relative_path = lambda path: path.relative_to(self.root).as_posix()
        self.handler._onlyoffice_internal_url = lambda: 'http://127.0.0.1:18085'
        self.handler._onlyoffice_safe_path = lambda raw_path, must_exist=True: (
            self.book if raw_path == 'Book.xlsx' else (_ for _ in ()).throw(ValueError('unsafe path')))
        self.handler._onlyoffice_atomic_write = lambda path, data: path.write_bytes(data)
        self.sent = []
        self.handler._send_json = lambda data, status=HTTPStatus.OK: self.sent.append((int(status), data))
        self.handler.path = '/api/applications/onlyoffice/callback?path=Book.xlsx'
        self.handler.headers = FakeHeaders({'Content-Length': '200'})
        self.env = patch.dict(os.environ, {'MAIN_COMPUTER_HARDENED_CANDIDATE':'1',
                                           'MAIN_COMPUTER_INSTALLATION_MODE':'dev'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.key = active_key(self.handler, self.book)
        self.callback_grant = grant(SECRET, path='Book.xlsx', mode='dev',key=self.key,purpose='callback')
        self.download_grant = grant(SECRET, path='Book.xlsx', mode='dev',key=self.key,purpose='download')

    def callback(self, body, *, cap=None, signed=True):
        self.handler.path = '/api/applications/onlyoffice/callback?path=Book.xlsx&cap=' + (
            self.callback_grant if cap is None else cap)
        raw = json.dumps(body).encode()
        self.handler.headers=FakeHeaders({'Content-Length':str(len(raw)),
                                         'Authorization':'Bearer '+jwt(body) if signed else ''})
        self.handler._read_json=lambda:body
        self.handler._handle_onlyoffice_callback()
        return self.sent[-1]

    def test_anonymous_callback_denied_before_read(self):
        self.handler._read_json = lambda: self.fail('body must not be read')
        self.handler._handle_onlyoffice_callback()
        self.assertEqual(self.sent[-1][0], 403)
        self.assertEqual(self.book.read_bytes(), workbook('original'))

    def test_real_signed_force_save_then_replay_denied(self):
        issued = journal_for(self.handler).issue(self.book, mode='dev', key=self.key)
        body = {'status':6, 'key':self.key,'userdata':issued,
                'url':'http://127.0.0.1:18085/cache/files/valid'}
        target=workbook('new')
        with patch('main_computer.onlyoffice_secure_runtime.fetch_edited_document',return_value=target) as download:
            self.assertEqual(self.callback(body)[0], 200)
            self.assertEqual(self.book.read_bytes(),target)
            self.assertEqual(self.callback(body)[0],400)
            self.assertEqual(download.call_count,2)
        self.assertEqual(self.book.read_bytes(),target)

    def test_forged_signed_body_denied_before_network(self):
        body={'status':2,'key':self.key,'url':'http://127.0.0.1:18085/cache/files/ok'}
        with patch('main_computer.onlyoffice_secure_runtime.fetch_edited_document') as download:
            self.assertEqual(self.callback(body,signed=False)[0],403)
            self.assertEqual(self.callback(body,cap='bad')[0],403)
            download.assert_not_called()
        self.assertEqual(self.book.read_bytes(),workbook('original'))

    def test_final_close_rotates_key_and_late_callback_denied(self):
        body={'status':2,'key':self.key,'url':'http://127.0.0.1:18085/cache/files/end'}
        with patch('main_computer.onlyoffice_secure_runtime.fetch_edited_document',return_value=workbook('final')):
            self.assertEqual(self.callback(body)[0],200)
            self.assertEqual(self.callback(body)[0],403)
            self.assertNotEqual(active_key(self.handler,self.book),self.key)
            self.assertEqual(self.book.read_bytes(),workbook('final'))

    def test_file_download_requires_purpose_bound_grant(self):
        raw = io.BytesIO()
        self.handler.wfile = raw
        self.handler.send_response=lambda status: self.sent.append(('http',int(status)))
        self.handler.send_header=lambda *a: None
        self.handler.end_headers=lambda: None
        self.handler.path='/api/applications/onlyoffice/file?path=Book.xlsx'
        self.handler._handle_onlyoffice_file_get()
        self.assertEqual(self.sent[-1][0],403)
        self.assertEqual(raw.getvalue(),b'')
        self.handler.path += '&cap='+self.download_grant
        self.handler._handle_onlyoffice_file_get()
        self.assertEqual(raw.getvalue(),workbook('original'))
        self.assertIn(('http',200),self.sent)


if __name__=='__main__': unittest.main()
