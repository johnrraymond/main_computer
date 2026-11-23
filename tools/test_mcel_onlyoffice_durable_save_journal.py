"""Source-backed disposable sequence journal acceptance; no MC production writes.

Runs on Windows and Linux; the separate-process race uses a durable file journal.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree import ElementTree as ET
from tools.mcel_onlyoffice_durable_save_journal import (DurableSaveJournal,
    JournalConflict, RevisionInvalid)

SECRET=b'candidate-test-secret-is-never-production-state-32bytes!'


# Portable, real XLSX fixture: no third-party package required by the host test.
_MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
_PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
_CT = 'http://schemas.openxmlformats.org/package/2006/content-types'


def workbook_bytes(value):
    if not isinstance(value, str):
        raise TypeError('fixture A1 must be a text value')
    ET.register_namespace('r', _REL)
    workbook = ET.Element(f'{{{_MAIN}}}workbook')
    sheets = ET.SubElement(workbook, f'{{{_MAIN}}}sheets')
    ET.SubElement(sheets, f'{{{_MAIN}}}sheet',
                  {'name': 'Sheet1', 'sheetId': '1', f'{{{_REL}}}id': 'rId1'})
    worksheet = ET.Element(f'{{{_MAIN}}}worksheet')
    sheetdata = ET.SubElement(worksheet, f'{{{_MAIN}}}sheetData')
    row = ET.SubElement(sheetdata, f'{{{_MAIN}}}row', {'r': '1'})
    a1 = ET.SubElement(row, f'{{{_MAIN}}}c', {'r': 'A1', 't': 'inlineStr'})
    inline = ET.SubElement(a1, f'{{{_MAIN}}}is')
    ET.SubElement(inline, f'{{{_MAIN}}}t').text = value
    types = ET.Element(f'{{{_CT}}}Types')
    ET.SubElement(types, f'{{{_CT}}}Default',
                  {'Extension': 'rels', 'ContentType': 'application/vnd.openxmlformats-package.relationships+xml'})
    ET.SubElement(types, f'{{{_CT}}}Default',
                  {'Extension': 'xml', 'ContentType': 'application/xml'})
    for part, media in (
        ('/xl/workbook.xml', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'),
        ('/xl/worksheets/sheet1.xml', 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'),
    ):
        ET.SubElement(types, f'{{{_CT}}}Override', {'PartName': part, 'ContentType': media})
    package_rels = ET.Element(f'{{{_PKG}}}Relationships')
    ET.SubElement(package_rels, f'{{{_PKG}}}Relationship',
                  {'Id': 'rId1', 'Type': _REL + '/officeDocument', 'Target': 'xl/workbook.xml'})
    workbook_rels = ET.Element(f'{{{_PKG}}}Relationships')
    ET.SubElement(workbook_rels, f'{{{_PKG}}}Relationship',
                  {'Id': 'rId1', 'Type': _REL + '/worksheet', 'Target': 'worksheets/sheet1.xml'})
    out = BytesIO()
    with ZipFile(out, 'w', compression=ZIP_DEFLATED) as archive:
        for name, xml in (
            ('[Content_Types].xml', types), ('_rels/.rels', package_rels),
            ('xl/workbook.xml', workbook),
            ('xl/_rels/workbook.xml.rels', workbook_rels),
            ('xl/worksheets/sheet1.xml', worksheet),
        ):
            archive.writestr(name, ET.tostring(xml, encoding='utf-8', xml_declaration=True))
    return out.getvalue()


def cell(path):
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
    a1 = root.find(f".//{{{_MAIN}}}c[@r='A1']/{{{_MAIN}}}is/{{{_MAIN}}}t")
    if a1 is None:
        raise ValueError('fixture workbook lacks cell A1')
    return a1.text


def child_main(path, action, userdata=None, value=None):
    p=Path(path); j=DurableSaveJournal(p.parent, SECRET)
    if action=='issue':
        print(j.issue(p,mode='dev',key='stable-editor-key'),flush=True)
    elif action=='commit':
        try:
            j.commit(p,mode='dev',key='stable-editor-key',userdata=userdata,
                     data=workbook_bytes(value))
            print('COMMITTED',flush=True)
        except JournalConflict:
            print('CONFLICT',flush=True)
    elif action=='inspect':
        print(json.dumps(j.inspect(p,mode='dev',key='stable-editor-key')),flush=True)
    else:raise ValueError(action)


class DurableJournalTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mc-oo-durable-candidate-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.path=self.root/'candidate.xlsx';self.path.write_bytes(workbook_bytes('INITIAL'))
        self.j=DurableSaveJournal(self.root,SECRET)
        self.mode='dev';self.key='stable-editor-key'
        self.j.register(self.path,mode=self.mode,key=self.key)

    def issue(self,journal=None):
        return (journal or self.j).issue(self.path,mode=self.mode,key=self.key)

    def commit(self, token, value, journal=None, **kw):
        return (journal or self.j).commit(self.path,mode=self.mode,key=self.key,
                                        userdata=token,data=workbook_bytes(value),**kw)

    def run_child(self,action,*more):
        proc=subprocess.run([sys.executable,'-m','tools.test_mcel_onlyoffice_durable_save_journal',
            'child',str(self.path),action,*more],capture_output=True,text=True,timeout=40,
            cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(proc.returncode,0,proc.stderr)
        return proc.stdout.strip()

    def test_unregistered_document_cannot_issue(self):
        other=self.root/'unregistered.xlsx'
        other.write_bytes(workbook_bytes('NEW'))
        with self.assertRaises(JournalConflict):
            self.j.issue(other,mode=self.mode,key=self.key)
        self.j.register(other,mode=self.mode,key=self.key)
        self.assertIsInstance(self.j.issue(other,mode=self.mode,key=self.key),str)

    def test_two_ordered_saves_one_key(self):
        first=self.issue();self.commit(first,'FIRST')
        second=self.issue();self.commit(second,'SECOND')
        self.assertEqual(cell(self.path),'SECOND')
        self.assertEqual(self.j.inspect(self.path,mode=self.mode,key=self.key)['accepted'],2)

    def test_replay_rejected(self):
        token=self.issue();self.commit(token,'FIRST')
        with self.assertRaises(JournalConflict):self.commit(token,'REPLAY')
        self.assertEqual(cell(self.path),'FIRST')

    def test_out_of_order_second_wins(self):
        old=self.issue();new=self.issue();self.commit(new,'SECOND')
        with self.assertRaises(JournalConflict):self.commit(old,'OLD')
        self.assertEqual(cell(self.path),'SECOND')

    def test_wrong_mode_or_key_cannot_open(self):
        token=self.issue()
        with self.assertRaises(JournalConflict):
            self.j.commit(self.path,mode='debug',key=self.key,userdata=token,data=workbook_bytes('EVIL'))
        with self.assertRaises(JournalConflict):
            self.j.commit(self.path,mode=self.mode,key='other',userdata=token,data=workbook_bytes('EVIL'))

    def test_tampered_token_rejected(self):
        token=self.issue()
        with self.assertRaises(JournalConflict):self.commit(token[:-1]+('0' if token[-1]!='0' else '1'),'EVIL')

    def test_expired_callback_rejected(self):
        base=1000
        j=DurableSaveJournal(self.root,SECRET,clock=lambda:base)
        token=j.issue(self.path,mode=self.mode,key=self.key,lifetime=1)
        expired=DurableSaveJournal(self.root,SECRET,clock=lambda:base+2)
        with self.assertRaises(JournalConflict):self.commit(token,'LATE',journal=expired)
        self.assertEqual(cell(self.path),'INITIAL')

    def test_restart_retains_save_order(self):
        first=self.issue();self.commit(first,'FIRST')
        second=self.run_child('issue')
        self.assertEqual(self.run_child('commit',second,'SECOND'),'COMMITTED')
        self.assertEqual(cell(self.path),'SECOND')
        with self.assertRaises(JournalConflict):self.commit(first,'OLD')
        self.assertEqual(self.j.inspect(self.path,mode=self.mode,key=self.key)['accepted'],2)

    def test_independent_processes_one_commit_wins(self):
        first=self.issue()
        processes=[subprocess.Popen([sys.executable,'-m','tools.test_mcel_onlyoffice_durable_save_journal',
            'child',str(self.path),'commit',first,v],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,cwd=Path(__file__).resolve().parents[1]) for v in ('ALPHA','BETA')]
        results=[]
        for proc in processes:
            try:stdout,stderr=proc.communicate(timeout=40)
            except subprocess.TimeoutExpired:
                proc.kill();proc.communicate();self.fail('child timed out')
            self.assertEqual(proc.returncode,0,stderr)
            results.append(stdout.strip())
        self.assertEqual(sorted(results),['COMMITTED','CONFLICT'])
        self.assertIn(cell(self.path),('ALPHA','BETA'))
        self.assertEqual(self.j.inspect(self.path,mode=self.mode,key=self.key)['accepted'],1)

    def test_crash_between_file_and_journal_fails_closed(self):
        token=self.issue()
        def simulate_crash():raise RuntimeError('SIMULATED_PROCESS_CRASH')
        with self.assertRaisesRegex(RuntimeError,'SIMULATED_PROCESS_CRASH'):
            self.commit(token,'CHANGED',after_replace=simulate_crash)
        self.assertEqual(cell(self.path),'CHANGED')
        recovered=DurableSaveJournal(self.root,SECRET)
        with self.assertRaisesRegex(JournalConflict,'journal and workbook disagree'):
            recovered.issue(self.path,mode=self.mode,key=self.key)
        with self.assertRaises(JournalConflict):
            recovered.commit(self.path,mode=self.mode,key=self.key,userdata=token,data=workbook_bytes('OLD'))

    def test_atomic_rename_failure_preserves_bytes_and_cleans_temp(self):
        import unittest.mock
        token=self.issue()
        before=self.path.read_bytes()
        with unittest.mock.patch('tools.mcel_onlyoffice_durable_save_journal.os.replace',side_effect=OSError('RENAME_FAIL')):
            with self.assertRaisesRegex(OSError,'RENAME_FAIL'):self.commit(token,'NEW')
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(list(self.root.glob('.*.tmp')),[])
        self.commit(token,'RETRY_OK')
        self.assertEqual(cell(self.path),'RETRY_OK')

    def test_non_workbook_rejected(self):
        token=self.issue()
        with self.assertRaises(RevisionInvalid):
            self.j.commit(self.path,mode=self.mode,key=self.key,userdata=token,data=b'REPLACEMENT')

    def test_tampered_journal_fails_closed(self):
        token=self.issue()
        relative=self.j.fence._relative(self.path)
        file=self.j._state_path(relative)
        file.write_text('{','utf8')
        with self.assertRaises(JournalConflict):self.issue()
        with self.assertRaises(JournalConflict):self.commit(token,'EVIL')

    def test_deleted_journal_fails_closed_on_callback(self):
        token=self.issue()
        relative=self.j.fence._relative(self.path)
        self.j._state_path(relative).unlink()
        with self.assertRaises(JournalConflict):self.commit(token,'EVIL')
        with self.assertRaises(JournalConflict):self.issue()

    def test_marker_deleted_fails_closed(self):
        self.issue()
        relative=self.j.fence._relative(self.path)
        self.j._initialization_marker(relative).unlink()
        with self.assertRaises(JournalConflict):self.issue()

    def test_wrong_document_audience(self):
        token=self.issue()
        other=self.root/'other.xlsx';other.write_bytes(workbook_bytes('OTHER'))
        with self.assertRaises(JournalConflict):
            self.j.commit(other,mode=self.mode,key=self.key,userdata=token,data=workbook_bytes('EVIL'))

    def test_unchanged_save_does_not_consume_token(self):
        token=self.issue()
        with self.assertRaises(JournalConflict):
            self.j.commit(self.path,mode=self.mode,key=self.key,userdata=token,data=self.path.read_bytes())
        self.commit(token,'CHANGE')
        self.assertEqual(cell(self.path),'CHANGE')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',nargs='?',default='run',choices=['run','child'])
    parser.add_argument('rest',nargs='*');args=parser.parse_args()
    if args.action=='child':
        child_main(*args.rest);return 0
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(DurableJournalTest)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'tests':result.testsRun,'failures':len(result.failures),
            'errors':len(result.errors),'status':'PASS' if result.wasSuccessful() else 'FAIL',
            'production_modified':False,'scope':'isolated-file-journal-only'}
    print('MC_OO_DURABLE_JOURNAL SUMMARY '+json.dumps(report,sort_keys=True),flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
