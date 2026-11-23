"""Offline negative controls for disposable real-editor/durable restart integration.

A passing result does NOT substitute for the real Docker Document Server test.
No production applications, state, or credentials are read or changed.
"""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest
from tools import mcel_onlyoffice_real_editor_lab as lab

ROOT=Path(__file__).resolve().parents[1]


class DurableRealEditorRestartContract(unittest.TestCase):
    def test_durable_mode_requires_continuous_saves(self):
        with self.assertRaisesRegex(ValueError,'requires --continuous-saves'):
            lab.experiment(durable_restart=True)

    def test_durable_container_mounts_only_source_and_disposable_documents(self):
        with tempfile.TemporaryDirectory() as root:
            work=Path(root); (work/'documents').mkdir()
            spec=lab.manifest(work,56601,'a'*43,'s'*43,'latest',
                shadow_revision=True,continuous_saves=True,durable_restart=True)
            self.assertEqual(set(spec['services']),{'fixture','proxy','onlyoffice','bob'})
            self.assertTrue(spec['networks']['sandbox']['internal'])
            fixture=spec['services']['fixture']
            self.assertNotIn('ports',fixture)
            self.assertEqual(fixture['environment']['LAB_DURABLE_SAVE_JOURNAL'],'1')
            self.assertEqual(fixture['environment']['LAB_SHADOW_CONTINUOUS_SAVE'],'1')
            mounts=fixture['volumes']
            self.assertEqual(len(mounts),6)
            self.assertEqual(sum(x['target']=='/lab/documents' for x in mounts),1)
            self.assertEqual([x['read_only'] for x in mounts].count(False),1)
            expected={str((ROOT/'tools'/'mcel_onlyoffice_durable_save_journal.py').resolve()),
                      str((ROOT/'tools'/'mcel_onlyoffice_shadow_revision.py').resolve())}
            self.assertEqual({x['source'] for x in mounts if x['target'] in
                ('/lab/mcel_onlyoffice_durable_save_journal.py',
                 '/lab/mcel_onlyoffice_shadow_revision.py')},expected)
            self.assertFalse(any('/var/run/docker.sock' in x['target'] for x in mounts))

    def test_real_editor_script_requires_new_process_and_genuine_second_save(self):
        source=(ROOT/'tools'/'mcel_onlyoffice_real_editor_lab.py').read_text(encoding='utf8')
        for needle in ("'restart','fixture'",'durable-backend-restarted-with-different-process',
                       'durable-first-save-state-recovered-across-restart',
                       'durable-real-issued-first-command-replay-denied-after-restart',
                       'continuous-second-genuine-editor-save-persisted',
                       'durable-second-save-journal-persisted-after-restart',
                       'reopened-workbook-cell-persisted'):
            self.assertIn(needle,source)
        fixture=(ROOT/'tools'/'mcel_onlyoffice_real_editor_fixture.py').read_text(encoding='utf8')
        for needle in ('JOURNAL.issue(', 'JOURNAL.commit(', 'JOURNAL.rotate_key(',
                       'SERVER_BOOT_ID', 'active_document_key('):
            self.assertIn(needle,fixture)

    def test_docker_like_top_level_imports_without_tools_package(self):
        # The disposable container mounts standalone modules, not /lab/tools.
        # Check that its import layout actually works before using Docker.
        with tempfile.TemporaryDirectory(prefix='mc-oo-import-layout-') as td:
            work=Path(td)
            for module in ('mcel_onlyoffice_real_editor_fixture.py',
                           'mcel_onlyoffice_shadow_revision.py',
                           'mcel_onlyoffice_durable_save_journal.py'):
                shutil.copy(ROOT/'tools'/module,work/module)
            (work/'main_computer').mkdir()
            shutil.copy(ROOT/'main_computer'/'auth_bootstrap.py',
                        work/'main_computer'/'auth_bootstrap.py')
            shutil.copy(ROOT/'main_computer'/'viewport_routes_onlyoffice.py',
                        work/'viewport_routes_onlyoffice.py')
            (work/'documents').mkdir()
            secret=secrets.token_bytes(32)
            env={**os.environ,'PYTHONPATH':str(work),
                 'LAB_DOCUMENT_DIR':str(work/'documents'),
                 'LAB_AUTH_ROOT':str(work/'auth'),
                 'LAB_AUTH_KEY_B64':base64.urlsafe_b64encode(secret).decode().rstrip('='),
                 'LAB_JWT_SECRET':'s'*48,'LAB_BROWSER_PORT':'55220',
                 'LAB_SHADOW_REVISION_FENCE':'1','LAB_SHADOW_CONTINUOUS_SAVE':'1',
                 'LAB_DURABLE_SAVE_JOURNAL':'1',
                 'LAB_SOURCE_PATH':str(work/'viewport_routes_onlyoffice.py')}
            result=subprocess.run([sys.executable,'-c',
                'import mcel_onlyoffice_real_editor_fixture as f; '
                'assert f.DURABLE_SAVE and f.JOURNAL is not None'],
                cwd=work,env=env,capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,msg=result.stderr[-2200:])

    def test_cross_process_fixture_restarts_preserve_real_command_order(self):
        with tempfile.TemporaryDirectory(prefix='mc-oo-durable-fixture-') as td:
            root=Path(td)
            docs=root/'documents';docs.mkdir()
            workbook=docs/'real-edit.xlsx'
            lab.create_workbook(workbook,'ORIGINAL')
            key_bytes=secrets.token_bytes(32)
            env={**os.environ,
                 'LAB_DOCUMENT_DIR':str(docs),
                 'LAB_AUTH_ROOT':str(root/'auth'),
                 'LAB_AUTH_KEY_B64':base64.urlsafe_b64encode(key_bytes).decode().rstrip('='),
                 'LAB_JWT_SECRET':secrets.token_urlsafe(40),
                 'LAB_BROWSER_PORT':'56123',
                 'LAB_SHADOW_REVISION_FENCE':'1',
                 'LAB_SHADOW_CONTINUOUS_SAVE':'1',
                 'LAB_DURABLE_SAVE_JOURNAL':'1',
                 'LAB_SOURCE_PATH':str(ROOT/'main_computer'/'viewport_routes_onlyoffice.py'),
                 'PYTHONPATH':str(ROOT/'tools')+os.pathsep+str(ROOT)+os.pathsep+os.environ.get('PYTHONPATH','')}
            phase1=r'''
import json
from tools import mcel_onlyoffice_real_editor_fixture as f
from tools import mcel_onlyoffice_real_editor_lab as l
key=f.make_config()['document']['key']
token=f.issue_save_userdata(key)
assert f.JOURNAL.inspect(f.TARGET,mode=f.MODE,key=key)['accepted']==0
l.create_workbook(f.TARGET.parent/'stage.xlsx','FIRST')
f.JOURNAL.commit(f.TARGET,mode=f.MODE,key=key,userdata=token,
                 data=(f.TARGET.parent/'stage.xlsx').read_bytes())
print(json.dumps({'key':key,'token':token,'boot':f.SERVER_BOOT_ID}))
'''
            phase2=r'''
import json,sys
from tools import mcel_onlyoffice_real_editor_fixture as f
from tools import mcel_onlyoffice_real_editor_lab as l
state=json.loads(sys.argv[1]);key=state['key']
assert f.SERVER_BOOT_ID!=state['boot']
assert f.JOURNAL.inspect(f.TARGET,mode=f.MODE,key=key)['accepted']==1
token=f.issue_save_userdata(key)
l.create_workbook(f.TARGET.parent/'stage.xlsx','SECOND')
f.JOURNAL.commit(f.TARGET,mode=f.MODE,key=key,userdata=token,
                 data=(f.TARGET.parent/'stage.xlsx').read_bytes())
assert f.JOURNAL.inspect(f.TARGET,mode=f.MODE,key=key)['accepted']==2
try:
    f.JOURNAL.commit(f.TARGET,mode=f.MODE,key=key,userdata=state['token'],
                     data=(f.TARGET.parent/'stage.xlsx').read_bytes())
except f.JournalConflict:
    pass
else:
    raise AssertionError('first command replay accepted after process restart')
fresh_key=f.make_config()['document']['key']
assert fresh_key!=key
assert f.JOURNAL.current(f.TARGET,mode=f.MODE)['key']==fresh_key
assert f.JOURNAL.inspect(f.TARGET,mode=f.MODE,key=fresh_key)['accepted']==2
try:
    f.JOURNAL.issue(f.TARGET,mode=f.MODE,key=key)
except f.JournalConflict:
    pass
else:
    raise AssertionError('prior editing key remained valid after reopening')
assert f.JOURNAL.inspect(f.TARGET,mode=f.MODE,key=fresh_key)['next']==2
print(json.dumps({'key':key,'boot':f.SERVER_BOOT_ID,'accepted':2,'reopened_key':fresh_key}))
'''
            results=[]
            for source,arg in ((phase1,None),(phase2,None)):
                args=[sys.executable,'-c',source]
                if arg is None and results: args.append(json.dumps(results[-1]))
                proc=subprocess.run(args,cwd=ROOT,env=env,stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,text=True,timeout=130)
                self.assertEqual(proc.returncode,0,msg=proc.stdout[-1000:]+'\n'+proc.stderr[-2200:])
                results.append(json.loads(proc.stdout.strip().splitlines()[-1]))
            self.assertNotEqual(results[0]['boot'],results[1]['boot'])
            self.assertEqual(results[1]['accepted'],2)
            self.assertEqual(lab.get_cell(workbook,'A1'),'SECOND')


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(DurableRealEditorRestartContract))
    print('MC_OO_DURABLE_EDITOR_RESTART_CONTRACT SUMMARY '+json.dumps({
        'tests':result.testsRun,'errors':len(result.errors),'failures':len(result.failures),
        'status':'PASS' if result.wasSuccessful() else 'FAIL',
        'production_modified':False},sort_keys=True),flush=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)
