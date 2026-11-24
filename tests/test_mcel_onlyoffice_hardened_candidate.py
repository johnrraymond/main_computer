"""Local, source-backed candidate security contracts; never launches production."""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from main_computer.auth_hostnames import HOSTS, canonical_host, managed_browser_resolver_rules, validate_origin
from main_computer.hardened_ingress import AssertionVerifier, issue_assertion, admit
from main_computer.onlyoffice_secure_runtime import (
    OnlyOfficeDenied, _b64, active_key, check_grant, fetch_edited_document, grant,
    installation_mode, journal_for, shared_secret, verified_key, verify_signed_callback)
from main_computer.onlyoffice_revision_fence import RevisionConflict
from main_computer.onlyoffice_durable_journal import JournalConflict

SECRET = b'j'*32


def workbook(label):
    """Stable synthetic XLSX: ZIP timestamps must not change expected bytes."""
    buffer = io.BytesIO()
    entries = (
        ('[Content_Types].xml', '<Types/>'),
        ('xl/workbook.xml', '<workbook/>'),
        ('xl/worksheets/sheet1.xml', f'<sheet>{label}</sheet>'),
    )
    with zipfile.ZipFile(buffer, 'w') as f:
        for name, body in entries:
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            f.writestr(info, body)
    return buffer.getvalue()


def jwt(body, secret=SECRET):
    h = _b64(b'{"alg":"HS256","typ":"JWT"}')
    p = _b64(json.dumps({'payload': body}, separators=(',', ':')).encode())
    sig = _b64(hmac.new(secret, (h+'.'+p).encode(), hashlib.sha256).digest())
    return h+'.'+p+'.'+sig


class WorkbookFixtureTests(unittest.TestCase):
    def test_stable_zip_entry_timestamps_and_bytes(self):
        # The old writer silently changed bytes at each two-second ZIP time tick.
        early = time.struct_time((2026, 11, 23, 23, 43, 32, 0, 0, -1))
        late = time.struct_time((2026, 11, 23, 23, 43, 34, 0, 0, -1))
        with patch('zipfile.time.localtime', return_value=early):
            before = workbook('original')
        with patch('zipfile.time.localtime', return_value=late):
            after = workbook('original')
        self.assertEqual(before, after)
        with zipfile.ZipFile(io.BytesIO(before)) as fixture:
            self.assertEqual(
                [entry.date_time for entry in fixture.infolist()],
                [(2020, 1, 1, 0, 0, 0)] * 3,
            )
            self.assertIn(b'original', fixture.read('xl/worksheets/sheet1.xml'))
        self.assertNotEqual(before, workbook('changed'))


class Session:
    def __init__(self): self.valid_tokens = {'session-session-session-session-session'}
    def valid(self, token): return token in self.valid_tokens


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.sessions = Session()
        self.verifier = AssertionVerifier(SimpleNamespace(key=SECRET, sessions=self.sessions))
        self.session = next(iter(self.sessions.valid_tokens))

    def ticket(self, body=b'{}'):
        return issue_assertion(SECRET, session=self.session, method='POST',
            path='/api/applications/browser/control', body=body, host='127.0.0.1:8765')

    def test_exact_method_path_body_host(self):
        token = self.ticket()
        self.assertTrue(self.verifier.verify(token, method='POST', path='/api/applications/browser/control',
            body=b'{}', host='127.0.0.1:8765'))
        self.assertFalse(self.verifier.verify(token, method='POST', path='/api/applications/browser/control',
            body=b'{}', host='127.0.0.1:8765'))

    def test_body_swap_rejected(self):
        self.assertFalse(self.verifier.verify(self.ticket(), method='POST',
            path='/api/applications/browser/control', body=b'{"action":"stop"}',host='127.0.0.1:8765'))

    def test_revoked_session_rejected(self):
        token = self.ticket()
        self.sessions.valid_tokens.clear()
        self.assertFalse(self.verifier.verify(token, method='POST', path='/api/applications/browser/control',
            body=b'{}', host='127.0.0.1:8765'))

    def test_expired_ticket_rejected(self):
        token = issue_assertion(SECRET, session=self.session, method='POST',
            path='/api/applications/browser/control', body=b'{}', host='127.0.0.1:8765', now=time.time()-30)
        self.assertFalse(self.verifier.verify(token, method='POST', path='/api/applications/browser/control',
            body=b'{}', host='127.0.0.1:8765'))

    def test_ingress_denies_anonymous_direct_and_reads_body_once(self):
        request = SimpleNamespace(path='/api/applications/browser/control',
            rfile=io.BytesIO(b'{}'), headers={'Content-Length':'2','Host':'127.0.0.1:8765'},
            server=SimpleNamespace(mc_gateway_verifier=self.verifier))
        request.headers = FakeHeaders(request.headers)
        statuses=[]
        request._send_json=lambda payload,status:statuses.append(int(status))
        with patch.dict(os.environ, {'MAIN_COMPUTER_HARDENED_CANDIDATE':'1'}):
            self.assertFalse(admit(request,'POST'))
        self.assertEqual(statuses, [401])
        request.rfile=io.BytesIO(b'{}')
        request.headers['X-MC-Gateway-Assertion']=self.ticket()
        with patch.dict(os.environ, {'MAIN_COMPUTER_HARDENED_CANDIDATE':'1'}):
            self.assertTrue(admit(request,'POST'))
        self.assertEqual(request.rfile.read(), b'{}')


class FakeHeaders(dict):
    def get_all(self, name, default=None):
        return [self[name]] if name in self else (default if default is not None else [])


class DocumentSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='mc-oo-candidate-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.path=self.root/'Book.xlsx'
        self.path.write_bytes(workbook('start'))
        self.config=SimpleNamespace(onlyoffice_jwt_enabled=True,
                                   onlyoffice_jwt_secret='j'*32)
        self.handler=SimpleNamespace(server=SimpleNamespace(config=self.config),
            _onlyoffice_root=lambda:self.root,
            _onlyoffice_relative_path=lambda p:p.relative_to(self.root).as_posix(),
            _onlyoffice_internal_url=lambda:'http://127.0.0.1:18085')

    def test_canonical_registry_and_browser_resolver(self):
        self.assertEqual(len(set(HOSTS.values())), 5)
        self.assertEqual(canonical_host('onlyoffice'), 'onlyoffice.main-computer.local')
        self.assertIn('MAP *.main-computer.local ^NOTFOUND', managed_browser_resolver_rules())
        self.assertTrue(validate_origin('https://dev.main-computer.local:8875'))
        self.assertFalse(validate_origin('http://dev.main-computer.local:8875'))

    def test_jwt_refuses_missing_secret(self):
        self.config.onlyoffice_jwt_secret = ''
        with self.assertRaises(OnlyOfficeDenied): shared_secret(self.config)

    def test_grant_bound_to_purpose_mode_document_key(self):
        token=grant(SECRET, path='Book.xlsx', mode='dev', key='livekey', purpose='callback')
        self.assertTrue(
                        check_grant(SECRET, token, path='Book.xlsx', mode='dev', purpose='callback', current_key='livekey'))
        for changed in [dict(mode='debug', purpose='callback', current_key='livekey'),
                        dict(mode='dev',purpose='download',current_key='livekey'),
                        dict(mode='dev',purpose='callback',current_key='newkey')]:
            self.assertFalse(check_grant(SECRET, token, path='Book.xlsx', **changed))
        expired=grant(SECRET, path='Book.xlsx',mode='dev',key='livekey',purpose='callback', expires=int(time.time())-1)
        self.assertFalse(check_grant(SECRET,expired,path='Book.xlsx',mode='dev',purpose='callback',current_key='livekey'))

    def test_callback_requires_valid_signed_payload(self):
        body={'key':'livekey','status':6,'url':'http://127.0.0.1:18085/cache/files/1'}
        headers={'Authorization':'Bearer '+jwt(body)}
        self.assertEqual(verify_signed_callback(headers,body,SECRET,expected_key='livekey'),body)
        with self.assertRaises(OnlyOfficeDenied):
            verify_signed_callback(headers,{**body,'url':'http://169.254.169.254/latest'},SECRET,expected_key='livekey')
        with self.assertRaises(OnlyOfficeDenied):
            verify_signed_callback(headers,body,SECRET,expected_key='wrongkey')
        with self.assertRaises(OnlyOfficeDenied):
            verify_signed_callback({'Authorization': 'Bearer '+jwt(body,b'e'*32)},body,SECRET,expected_key='livekey')
        with self.assertRaises(OnlyOfficeDenied):
            verify_signed_callback({},body,SECRET,expected_key='livekey')

    def test_ssrf_blocked_before_network(self):
        for url in ['http://169.254.169.254/cache/files/x',
                    'http://localhost:1/cache/files/x',
                    'http://127.0.0.1:18085/redirect',
                    'http://user@127.0.0.1:18085/cache/files/a',
                    'https://127.0.0.1:18085/cache/files/a']:
            with self.subTest(url=url):
                with self.assertRaises(OnlyOfficeDenied):
                    fetch_edited_document(self.handler,url)

    def test_durable_journal_preserves_live_key_and_rejects_replay(self):
        with patch.dict(os.environ, {'MAIN_COMPUTER_INSTALLATION_MODE':'dev'}):
            key=active_key(self.handler,self.path)
            self.assertEqual(active_key(self.handler,self.path),key)
            j=journal_for(self.handler)
            userdata=j.issue(self.path,mode='dev',key=key)
            j.commit(self.path,mode='dev',key=key,userdata=userdata,data=workbook('edited'))
            self.assertEqual(active_key(self.handler,self.path),key)
            with self.assertRaises(JournalConflict):
                j.commit(self.path,mode='dev',key=key,userdata=userdata,data=workbook('stale'))
            j.commit_final_close(self.path,mode='dev',key=key,data=workbook('final'))
            with self.assertRaises(JournalConflict):
                j.commit_final_close(self.path,mode='dev',key=key,data=workbook('final'))
            new=active_key(self.handler,self.path)
            self.assertNotEqual(new,key)


if __name__=='__main__':unittest.main()
