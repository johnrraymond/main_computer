"""Offline positive/negative controls for pre-cutover continuous-save experiment.

Only tests disposable Python state and source wiring. A true editor/save proof
is the separate --continuous-saves Docker run on the Windows host.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest

from tools import mcel_onlyoffice_real_editor_lab as lab

ROOT=Path(__file__).resolve().parents[1]


class ContinuousSaveContract(unittest.TestCase):
    def test_lab_requires_shadow_revision_guard(self):
        with self.assertRaisesRegex(ValueError, 'requires --shadow-revision'):
            # Invalid combinations fail without attempting Docker startup.
            lab.experiment(shadow_revision=False,continuous_saves=True)

    def test_manifest_saves_are_disposable_and_signed(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);(base/'documents').mkdir()
            original=lab.manifest(base,55666,'a'*43,'s'*44,'latest',shadow_revision=True)
            continuous=lab.manifest(base,55666,'a'*43,'s'*44,'latest',shadow_revision=True,
                                    continuous_saves=True)
            self.assertEqual(original['services']['fixture']['environment']['LAB_SHADOW_CONTINUOUS_SAVE'],'0')
            self.assertEqual(continuous['services']['fixture']['environment']['LAB_SHADOW_CONTINUOUS_SAVE'],'1')
            self.assertEqual(continuous['services']['fixture']['environment']['LAB_SHADOW_REVISION_FENCE'],'1')
            self.assertEqual(original['services']['proxy']['ports'],continuous['services']['proxy']['ports'])
            self.assertEqual(len(continuous['services']['onlyoffice'].get('ports',[])),0)
            self.assertEqual(len(continuous['services']['fixture'].get('ports',[])),0)
            self.assertEqual(continuous['services']['fixture']['volumes'],original['services']['fixture']['volumes'])

    def test_main_cutover_gate_is_never_satisfied_by_one_save(self):
        from tools import mcel_onlyoffice_pre_cutover_certification as cert
        self.assertIn('continuous-editor-ordered-forcesaves',cert.REQUIRED_PROOFS)
        src=Path(cert.__file__).read_text(encoding='utf8')
        for marker in ('docker-continuous-editor-saves','--continuous-saves',
                       'continuous-second-genuine-editor-save-persisted PASS',
                       'continuous-document-key-remains-stable-after-second-save PASS'):
            self.assertIn(marker,src)

    def test_signed_sequences_are_distinct_and_scoped_negative_controls(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            docs=root/'documents';docs.mkdir()
            lab.create_workbook(docs/'real-edit.xlsx','MC_INITIAL')
            key=secrets.token_bytes(32)
            env={**os.environ,
                 'LAB_DOCUMENT_DIR':str(docs),
                 'LAB_AUTH_ROOT':str(root/'authority'),
                 'LAB_AUTH_KEY_B64':base64.urlsafe_b64encode(key).decode().rstrip('='),
                 'LAB_JWT_SECRET':secrets.token_urlsafe(40),
                 'LAB_BROWSER_PORT':'56123',
                 'LAB_SHADOW_REVISION_FENCE':'1',
                 'LAB_SHADOW_CONTINUOUS_SAVE':'1',
                 'LAB_SOURCE_PATH':str(ROOT/'main_computer'/'viewport_routes_onlyoffice.py'),
                 'PYTHONPATH':str(ROOT/'tools')+os.pathsep+str(ROOT)+os.pathsep+os.environ.get('PYTHONPATH','')}
            code=r'''
import base64,hashlib,hmac,json
from tools import mcel_onlyoffice_real_editor_fixture as f
cfg=f.make_config();key=cfg['document']['key']
a=f.issue_save_userdata(key);b=f.issue_save_userdata(key)
assert a != b
ma=f.parse_save_userdata(a,key);mb=f.parse_save_userdata(b,key)
assert ma['seq']==1 and mb['seq']==2
assert f.save_sequence_fresh(key,1) and f.save_sequence_fresh(key,2)
f.CONTINUOUS_STATE[key]['accepted']=2
assert not f.save_sequence_fresh(key,1)
assert not f.save_sequence_fresh(key,2)
assert f.save_sequence_fresh(key,3)
assert f.parse_save_userdata(a[:-1]+('0' if a[-1]!='0' else '1'),key) is None
assert f.parse_save_userdata(a,'other-key') is None
assert f.parse_save_userdata(None,key) is None
payload,mac=a.split('.')
meta=json.loads(base64.urlsafe_b64decode(payload+'==='))
def sign(data):
  raw=f.b64json(data)
  return raw+'.'+hmac.new(f.KEY_BYTES,b'mc-oo-forcesave|'+raw.encode(),hashlib.sha256).hexdigest()
for changed in ({**meta,'mode':'debug'}, {**meta,'doc':'other.xlsx'},
                {**meta,'exp':0}, {**meta,'seq':10}):
  assert f.parse_save_userdata(sign(changed),key) is None
print(json.dumps({'status':'PASS','verified_sequences':[ma['seq'],mb['seq']],
                  'forged_and_wrong_scope_rejected':True}))
'''
            result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                  text=True,timeout=60)
            self.assertEqual(result.returncode,0,msg=result.stderr[-2500:])
            self.assertIn('"status": "PASS"',result.stdout)


if __name__=='__main__':unittest.main(verbosity=2)
