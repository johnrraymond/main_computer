"""Offline contracts for the disposable real-editor callback's signed fields.

No Docker, MC process, authentication material, or production document is used.
Run: python -m tools.test_mcel_onlyoffice_optional_userdata
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

FIXTURE = Path(__file__).with_name('mcel_onlyoffice_real_editor_fixture.py')
LAB = Path(__file__).with_name('mcel_onlyoffice_real_editor_lab.py')


def load_verifier():
    tree = ast.parse(FIXTURE.read_text(encoding='utf-8'))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
              and node.name == 'callback_signed_fields_bound')
    scope: dict = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
                 str(FIXTURE), 'exec'), scope)
    handler = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == 'Handler')
    post = next(node for node in handler.body if isinstance(node, ast.FunctionDef)
                and node.name == 'do_POST')
    if not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == 'callback_signed_fields_bound'
               for node in ast.walk(post)):
        raise AssertionError('production-shaped disposable callback handler does not invoke verifier')
    return scope['callback_signed_fields_bound']


class CallbackOptionalUserdata(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verify = staticmethod(load_verifier())

    def fields(self, *, status=6, userdata=None):
        body = {'status': status, 'key': 'registered-test-key',
                'url': 'http://onlyoffice/cache/files/test-output.xlsx'}
        if userdata is not None:
            body['userdata'] = userdata
        return body

    def test_status6_without_command_userdata_is_allowed_when_signed(self):
        body = self.fields()
        self.assertTrue(self.verify(body, dict(body), 'callback'))

    def test_status6_with_signed_userdata_is_allowed(self):
        body = self.fields(userdata='issued-authenticated-command')
        self.assertTrue(self.verify(body, dict(body), 'callback'))

    def test_status2_final_close_without_userdata_is_allowed(self):
        body = self.fields(status=2)
        self.assertTrue(self.verify(body, dict(body), None))

    def test_missing_signed_userdata_is_denied_when_body_has_it(self):
        body = self.fields(userdata='untrusted-command')
        jwt = dict(body)
        jwt.pop('userdata')
        self.assertFalse(self.verify(body, jwt, 'callback'))

    def test_modified_userdata_is_denied(self):
        body = self.fields(userdata='issued-command')
        jwt = dict(body, userdata='different-command')
        self.assertFalse(self.verify(body, jwt, 'callback'))

    def test_signed_userdata_not_in_body_is_denied(self):
        body = self.fields()
        jwt = dict(body, userdata='unsigned-body-omission')
        self.assertFalse(self.verify(body, jwt, 'callback'))

    def test_modified_callback_url_is_denied(self):
        body = self.fields()
        jwt = dict(body, url='http://other-server/cache/files/other.xlsx')
        self.assertFalse(self.verify(body, jwt, 'callback'))

    def test_missing_signed_callback_url_is_denied(self):
        body = self.fields()
        jwt = dict(body)
        jwt.pop('url')
        self.assertFalse(self.verify(body, jwt, 'callback'))

    def test_missing_body_callback_url_is_denied(self):
        body = self.fields()
        body.pop('url')
        self.assertFalse(self.verify(body, dict(body), 'callback'))

    def test_modified_status_is_denied(self):
        body = self.fields()
        self.assertFalse(self.verify(body, dict(body, status=2), 'callback'))

    def test_modified_document_key_is_denied(self):
        body = self.fields()
        self.assertFalse(self.verify(body, dict(body, key='different-key'), 'callback'))

    def test_unrelated_jwt_operation_is_denied(self):
        body = self.fields()
        self.assertFalse(self.verify(body, dict(body), 'command'))

    def test_non_dict_signed_or_body_is_denied(self):
        body = self.fields()
        self.assertFalse(self.verify(body, None, 'callback'))
        self.assertFalse(self.verify(None, body, 'callback'))

    def test_optional_url_must_be_bound_if_sent_even_outside_save_status(self):
        body = self.fields(status=1)
        jwt = dict(body)
        jwt.pop('url')
        self.assertFalse(self.verify(body, jwt, 'callback'))

    def test_callback_without_url_may_exist_in_nonsaving_status(self):
        body = {'status': 1, 'key': 'registered-test-key'}
        self.assertTrue(self.verify(body, dict(body), 'callback'))

    def test_diagnostic_only_emits_aggregate_counters(self):
        source = LAB.read_text(encoding='utf-8')
        self.assertIn('SAVE_CALLBACK_DIAGNOSTIC', source)
        self.assertIn("'bad_callbacks':metrics.get('bad_callbacks')", source)
        self.assertNotIn("'userdata':metrics", source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
