"""Pre-cutover final-close journal negative controls (disposable XLSX only).

This is a journal unit/integration test, NOT proof of the real Document Server
`status=2` lifecycle. The latter is separately tested by `--final-close-probe`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import base64
import secrets
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.mcel_onlyoffice_durable_save_journal import DurableSaveJournal, JournalConflict
from tools.test_mcel_onlyoffice_durable_save_journal import SECRET, workbook_bytes, cell


class FinalCloseJournalTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mc-oo-final-close-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.path=self.root/'book.xlsx'
        self.path.write_bytes(workbook_bytes('ORIGINAL'))
        self.mode='dev'; self.key='real-editor-key'
        self.j=DurableSaveJournal(self.root, SECRET)
        self.j.register(self.path,mode=self.mode,key=self.key)

    def issue(self):
        return self.j.issue(self.path,mode=self.mode,key=self.key)

    def commit(self,token,value):
        return self.j.commit(self.path,mode=self.mode,key=self.key,
                             userdata=token,data=workbook_bytes(value))

    def close(self,value,*,journal=None,**kwargs):
        return (journal or self.j).commit_final_close(self.path,mode=self.mode,
                            key=self.key,data=workbook_bytes(value),**kwargs)

    def test_realistic_two_force_saves_then_final_close(self):
        self.commit(self.issue(),'FIRST')
        self.commit(self.issue(),'SECOND')
        self.close('FINAL_UNFORCED')
        self.assertEqual(cell(self.path),'FINAL_UNFORCED')
        self.assertEqual(self.j.inspect(self.path,mode=self.mode,key=self.key)['accepted'],2)
        self.assertTrue(self.j.current(self.path,mode=self.mode)['final_closed'])

    def test_close_without_userdata_is_single_use(self):
        self.close('LAST')
        with self.assertRaises(JournalConflict):self.close('REPLAY')
        self.assertEqual(cell(self.path),'LAST')

    def test_closed_editor_cannot_issue_another_force_save(self):
        self.close('LAST')
        with self.assertRaises(JournalConflict):self.issue()

    def test_signed_force_save_issued_before_close_cannot_commit_late(self):
        first=self.issue();self.commit(first,'FIRST')
        second=self.issue();self.commit(second,'SECOND')
        self.close('FINAL')
        with self.assertRaises(JournalConflict):self.commit(second,'STALE')
        self.assertEqual(cell(self.path),'FINAL')

    def test_pending_command_blocks_ambiguous_final_close(self):
        pending=self.issue()
        with self.assertRaisesRegex(JournalConflict,'outstanding force-save'):
            self.close('MIGHT_BE_OLDER')
        self.assertEqual(cell(self.path),'ORIGINAL')
        self.commit(pending,'FIRST')
        self.close('FINAL')
        self.assertEqual(cell(self.path),'FINAL')

    def test_unchanged_close_consumes_one_use_grant(self):
        self.close('ORIGINAL')
        with self.assertRaises(JournalConflict):self.close('LATE')
        self.assertEqual(cell(self.path),'ORIGINAL')

    def test_wrong_mode_and_key_rejected_before_write(self):
        with self.assertRaises(JournalConflict):
            self.j.commit_final_close(self.path,mode='debug',key=self.key,
                                      data=workbook_bytes('EVIL'))
        with self.assertRaises(JournalConflict):
            self.j.commit_final_close(self.path,mode=self.mode,key='other-editor',
                                      data=workbook_bytes('EVIL'))
        self.assertEqual(cell(self.path),'ORIGINAL')

    def test_unregistered_document_rejected(self):
        other=self.root/'another.xlsx';other.write_bytes(workbook_bytes('OTHER'))
        with self.assertRaises(JournalConflict):
            self.j.commit_final_close(other,mode=self.mode,key=self.key,
                                      data=workbook_bytes('EVIL'))

    def test_expired_close_grant_rejected(self):
        reference=int(self.j.clock())
        later=DurableSaveJournal(self.root,SECRET,clock=lambda:reference+3602)
        with self.assertRaisesRegex(JournalConflict,'expired'):
            self.close('TOO_LATE',journal=later)
        self.assertEqual(cell(self.path),'ORIGINAL')

    def test_atomic_replace_fails_without_tmp_or_data_loss(self):
        original=self.path.read_bytes()
        with patch('tools.mcel_onlyoffice_durable_save_journal.os.replace',
                   side_effect=OSError('forced rename failure')):
            with self.assertRaises(OSError):self.close('CANNOT_COMMIT')
        self.assertEqual(self.path.read_bytes(),original)
        self.assertEqual(list(self.root.glob('*.tmp')),[])
        self.assertFalse(self.j.inspect(self.path,mode=self.mode,key=self.key)['final_closed'])
        self.close('RECOVERED')
        self.assertEqual(cell(self.path),'RECOVERED')

    def test_crash_after_workbook_replacement_fails_closed(self):
        def crash():raise RuntimeError('injected crash before journal commit')
        with self.assertRaises(RuntimeError):
            self.close('CHANGED_ON_DISK',after_replace=crash)
        self.assertEqual(cell(self.path),'CHANGED_ON_DISK')
        fresh=DurableSaveJournal(self.root,SECRET)
        with self.assertRaises(JournalConflict):
            self.close('MUST_NOT_OVERWRITE',journal=fresh)
        with self.assertRaises(JournalConflict):
            fresh.issue(self.path,mode=self.mode,key=self.key)

    def test_missing_journal_fails_closed(self):
        relative=self.j.fence._relative(self.path)
        self.j._state_path(relative).unlink()
        with self.assertRaises(JournalConflict):self.close('EVIL')

    def test_reopen_rotates_key_and_rejects_previous_final_close(self):
        self.close('FIRST_CLOSED')
        new_key='second-editor-key'
        self.j.rotate_key(self.path,mode=self.mode,old_key=self.key,new_key=new_key)
        with self.assertRaises(JournalConflict):self.close('OLD_REPLAY')
        self.j.commit_final_close(self.path,mode=self.mode,key=new_key,
                                  data=workbook_bytes('SECOND_CLOSED'))
        self.assertEqual(cell(self.path),'SECOND_CLOSED')

    def test_cross_process_replay_exactly_one_close_wins(self):
        program='''
from pathlib import Path
import sys
from tools.mcel_onlyoffice_durable_save_journal import DurableSaveJournal,JournalConflict
from tools.test_mcel_onlyoffice_durable_save_journal import SECRET,workbook_bytes
p=Path(sys.argv[1]);j=DurableSaveJournal(p.parent,SECRET)
try:
 j.commit_final_close(p,mode="dev",key="real-editor-key",data=workbook_bytes(sys.argv[2]))
 print("COMMITTED")
except JournalConflict:
 print("CONFLICT")
'''
        processes=[subprocess.Popen([sys.executable,'-c',program,str(self.path),v],
                       stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                       cwd=Path(__file__).resolve().parents[1]) for v in ('ALICE','REPLAY')]
        outputs=[]
        try:
            for proc in processes:
                stdout,stderr=proc.communicate(timeout=40)
                self.assertEqual(proc.returncode,0,stderr)
                outputs.append(stdout.strip())
        finally:
            for proc in processes:
                if proc.poll() is None:proc.kill();proc.communicate()
        self.assertEqual(sorted(outputs),['COMMITTED','CONFLICT'])
        self.assertIn(cell(self.path),('ALICE','REPLAY'))

    def test_source_backed_http_status2_admission_and_replay(self):
        """Synthetic JWT positive control through the REAL MC callback method.

        Not a substitute for the separately required Document Server callback;
        here the bytes are provided by an in-process pinned-source stand-in.
        """
        with tempfile.TemporaryDirectory(prefix='mc-oo-source-final-') as td:
            root=Path(td);docs=root/'docs';docs.mkdir()
            (docs/'real-edit.xlsx').write_bytes(workbook_bytes('ORIGINAL'))
            key=secrets.token_bytes(32)
            env={**os.environ, 'LAB_DOCUMENT_DIR':str(docs),
                 'LAB_AUTH_ROOT':str(root/'auth'),
                 'LAB_AUTH_KEY_B64':base64.urlsafe_b64encode(key).decode().rstrip('='),
                 'LAB_JWT_SECRET':secrets.token_urlsafe(42),
                 'LAB_BROWSER_PORT':'61245', 'LAB_BIND':'127.0.0.1',
                 'LAB_SHADOW_REVISION_FENCE':'1','LAB_SHADOW_CONTINUOUS_SAVE':'1',
                 'LAB_DURABLE_SAVE_JOURNAL':'1','LAB_FINAL_CLOSE_PROBE':'1',
                 'LAB_SOURCE_PATH':str(Path(__file__).resolve().parents[1]/'main_computer'/'viewport_routes_onlyoffice.py'),
                 'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'tools')+os.pathsep+str(Path(__file__).resolve().parents[1])+os.pathsep+os.environ.get('PYTHONPATH','')}
            source=r'''
import http.client, json, threading, time
from http.server import ThreadingHTTPServer
from urllib.request import Request
from tools import mcel_onlyoffice_real_editor_fixture as f
from tools.test_mcel_onlyoffice_durable_save_journal import workbook_bytes,cell
f.make_config()
key=f.JOURNAL.current(f.TARGET,mode=f.MODE)['key']
for marker in ('FIRST','SECOND'):
 token=f.JOURNAL.issue(f.TARGET,mode=f.MODE,key=key)
 f.JOURNAL.commit(f.TARGET,mode=f.MODE,key=key,userdata=token,data=workbook_bytes(marker))
assert cell(f.TARGET)=='SECOND'
class PinnedBytes:
 def __enter__(self):return self
 def __exit__(self,*args):return None
 def read(self):return workbook_bytes('FINAL_CLOSE')
# Only replace the private Document Server's cached XLSX fetch in this
# offline positive control; the production-sourced callback body and JWT
# admission handler are actually executed over HTTP.
def isolated_urlopen(request,timeout=60):
 assert isinstance(request,Request)
 assert request.full_url=='http://onlyoffice/cache/files/final-close.xlsx'
 return PinnedBytes()
f.ActualHandler._handle_onlyoffice_callback.__globals__['urlopen']=isolated_urlopen
server=ThreadingHTTPServer(('127.0.0.1',0),f.Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
port=server.server_port
cap=f.scoped_token('callback',f.MODE,f.DOC,int(time.time())+180)
url=f'/api/onlyoffice/callback?mode=dev&doc=real-edit.xlsx&path=real-edit.xlsx&cap={cap}'
body={'status':2,'key':key,'url':'http://onlyoffice/cache/files/final-close.xlsx'}
def request(headers,send_body=None):
 c=http.client.HTTPConnection('127.0.0.1',port,timeout=25)
 payload=json.dumps(body if send_body is None else send_body).encode()
 c.request('POST',url,payload,headers={'Host':'fixture:8000','Content-Type':'application/json',**headers})
 r=c.getresponse();status=r.status;r.read();c.close();return status
try:
 assert request({})==403
 assert request({'Authorization':'Bearer fake.jwt.signature'})==403
 assert request({'Authorization':'Bearer '+f.jwt_sign({'unrelated':True})})==403
 assert request({'Authorization':'Bearer '+f.jwt_sign({'payload':body,'operation':'download'})})==403
 invalid={**body,'status':'2'}
 assert request({'Authorization':'Bearer '+f.jwt_sign(invalid)},invalid)==403
 assert cell(f.TARGET)=='SECOND'
 signed=f.jwt_sign(body)
 assert request({'Authorization':'Bearer '+signed})==200
 assert cell(f.TARGET)=='FINAL_CLOSE'
 assert request({'Authorization':'Bearer '+signed})==400
 assert cell(f.TARGET)=='FINAL_CLOSE'
 assert f.REPORT['final_close_signed_status2']==2
 assert f.REPORT['final_close_committed']==1
 assert f.REPORT['final_close_rejected']==1
 print('SOURCE_BACKED_SIGNED_STATUS2_FINAL_CLOSE_PASS',flush=True)
finally:
 server.shutdown();server.server_close();thread.join(timeout=10)
'''
            proc=subprocess.run([sys.executable,'-c',source],cwd=Path(__file__).resolve().parents[1],
                                env=env,capture_output=True,text=True,timeout=120)
            self.assertEqual(proc.returncode,0,proc.stderr[-2600:])
            self.assertIn('SOURCE_BACKED_SIGNED_STATUS2_FINAL_CLOSE_PASS',proc.stdout)

    def test_invalid_xlsx_does_not_close(self):
        with self.assertRaises(Exception):
            self.j.commit_final_close(self.path,mode=self.mode,key=self.key,
                                      data=b'garbage')
        self.assertFalse(self.j.inspect(self.path,mode=self.mode,key=self.key)['final_closed'])


def main():
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(FinalCloseJournalTest)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    print('MC_OO_FINAL_CLOSE_JOURNAL SUMMARY '+json.dumps({
        'tests':result.testsRun,'errors':len(result.errors),'failures':len(result.failures),
        'status':'PASS' if result.wasSuccessful() else 'FAIL',
        'production_modified':False,'scope':'isolated-durable-journal-only'},sort_keys=True),flush=True)
    return 0 if result.wasSuccessful() else 1

if __name__=='__main__':raise SystemExit(main())
