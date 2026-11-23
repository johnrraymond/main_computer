"""Source-backed shadow ONLYOFFICE revision/fault certification; no cutover.

The original MC callback method is executed against a disposable workbook. The
only replaced boundary is its final write, which is delegated to the candidate
cross-process revision fence. Production source is opened read-only.
"""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import urllib.request
import zipfile

from tools.mcel_onlyoffice_shadow_revision import (
    ShadowRevisionFence, RevisionConflict, RevisionInvalid, sha256_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'main_computer' / 'viewport_routes_onlyoffice.py'


def create_xlsx(value: str) -> bytes:
    import xml.sax.saxutils
    cell = xml.sax.saxutils.escape(value)
    parts = {
        '[Content_Types].xml': ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>'),
        '_rels/.rels': ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>'),
        'xl/workbook.xml': ('<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'),
        'xl/_rels/workbook.xml.rels': ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>'),
        'xl/worksheets/sheet1.xml': ('<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>{cell}</t></is></c></row></sheetData></worksheet>'),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, payload in parts.items():
            z.writestr(name, payload)
    return output.getvalue()


def read_a1(data: bytes) -> str:
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
    node = sheet.find('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t')
    assert node is not None
    return node.text or ''


def extract_real_callback():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == 'ViewportOnlyOfficeRoutesMixin')
    keep = {'_handle_onlyoffice_callback', '_onlyoffice_looks_like_xlsx'}
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in keep]
    if {n.name for n in methods} != keep:
        raise RuntimeError('real production callback source changed; update test')
    code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='RealSource',
        bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[]))
    scope = {'parse_qs':__import__('urllib.parse',fromlist=['parse_qs']).parse_qs,
             'urlsplit':__import__('urllib.parse',fromlist=['urlsplit']).urlsplit,
             'Request':urllib.request.Request,'urlopen':urllib.request.urlopen,
             'HTTPStatus':HTTPStatus, 'zipfile':zipfile, 'io':io}
    exec(compile(code,str(SOURCE),'exec'),scope)
    return scope['RealSource'],scope


def run():
    checks=[]
    def record(name,good,detail=''):
        state='PASS' if good else 'FAIL'
        print(f'MC_OO_SHADOW {name} {state} {detail}',flush=True)
        checks.append((name,good))
    RealSource,scope=extract_real_callback()
    with tempfile.TemporaryDirectory(prefix='mc-oo-shadow-revision-') as temp:
        root=Path(temp)
        target=root/'Book.xlsx'
        original=create_xlsx('ORIGINAL'); target.write_bytes(original)
        newer=create_xlsx('NEWER'); older=create_xlsx('OLDER')
        fence=ShadowRevisionFence(root)
        baseline=fence.snapshot(target)
        record('source-backed-original-callback-loaded',hasattr(RealSource,'_handle_onlyoffice_callback'))
        record('original-revision-snapshot-matches-bytes',baseline==sha256_bytes(original))

        committed=fence.commit(target,baseline,newer)
        record('authorized-newer-revision-committed',committed==sha256_bytes(newer) and read_a1(target.read_bytes())=='NEWER')
        try:fence.commit(target,baseline,older)
        except RevisionConflict:rejected=True
        else:rejected=False
        record('stale-callback-cannot-overwrite-newer-workbook',rejected and read_a1(target.read_bytes())=='NEWER')
        try:fence.commit(target,baseline,newer)
        except RevisionConflict:rejected=True
        else:rejected=False
        record('replayed-editor-grant-cannot-overwrite',rejected)
        try:fence.commit(target,committed,newer)
        except RevisionConflict:rejected=True
        else:rejected=False
        record('unchanged-callback-is-not-reusable',rejected)
        newest=create_xlsx('LATEST')
        new_hash=fence.commit(target,committed,newest)
        record('reopened-editor-fresh-revision-commits',new_hash==sha256_bytes(newest) and read_a1(target.read_bytes())=='LATEST')

        outside=root.parent / ('not-our-workbook-' + root.name + '.xlsx')
        outside.write_bytes(original)
        try:
            try:fence.commit(outside,baseline,newer)
            except RevisionInvalid:denied=True
            else:denied=False
        finally:outside.unlink(missing_ok=True)
        record('outside-document-rejected',denied)
        try:fence.commit(target, 'invalid-client-hash', newer)
        except RevisionInvalid:denied=True
        else:denied=False
        record('invalid-base-revision-rejected',denied)
        try:fence.commit(target,new_hash,b'NOT_AN_XLSX')
        except RevisionInvalid:denied=True
        else:denied=False
        record('invalid-workbook-bytes-rejected',denied and target.read_bytes()==newest)
        current=new_hash
        with patch('tools.mcel_onlyoffice_shadow_revision.os.replace',side_effect=OSError('injected failed replace')):
            try:fence.commit(target,current,create_xlsx('WRITE_SHOULD_FAIL'))
            except OSError:rejected=True
            else:rejected=False
        leftovers=list(root.glob('.Book.xlsx.*.tmp'))
        record('atomic-rename-failure-preserves-bytes',rejected and target.read_bytes()==newest)
        record('atomic-rename-failure-cleans-temp',not leftovers,f'orphan_temps={len(leftovers)}')

        # Actual source callback, with ONLY the final atomic-write operation
        # replaced by the candidate revision guard. The real fetch is supplied
        # with inert bytes, never a network request.
        class Response:
            def __init__(self,data):self.data=data
            def __enter__(self):return self
            def __exit__(self,*exc):return False
            def read(self):return self.data
        downloads=[]
        def fetch(request,timeout):
            downloads.append(request.full_url)
            return Response({'http://isolated-docs/newer':create_xlsx('CALLBACK_FRESH'),
                             'http://isolated-docs/stale':create_xlsx('CALLBACK_STALE')}[request.full_url])
        scope['urlopen']=fetch
        source=RealSource()
        source.server=SimpleNamespace(signal=lambda *args,**kw:None)
        source.path='/api/applications/onlyoffice/callback?path=Book.xlsx'
        source._onlyoffice_safe_path=lambda name,must_exist=False: target if name=='Book.xlsx' else None
        responses=[]
        source._send_json=lambda body,status=HTTPStatus.OK:responses.append((int(status),body))
        issued_base=fence.snapshot(target)
        source._onlyoffice_atomic_write=lambda path,data:fence.commit(path,issued_base,data)
        source._read_json=lambda:{'status':2,'url':'http://isolated-docs/newer','key':'issued-by-server'}
        source._handle_onlyoffice_callback()
        record('real-source-callback-with-shadow-commit-works',responses[-1][0]==200 and read_a1(target.read_bytes())=='CALLBACK_FRESH')
        source._read_json=lambda:{'status':2,'url':'http://isolated-docs/stale','key':'issued-by-server'}
        source._handle_onlyoffice_callback()
        record('real-source-delayed-callback-rejected',responses[-1][0]==400 and read_a1(target.read_bytes())=='CALLBACK_FRESH')
        record('source-callback-fetched-fixture-only',downloads==['http://isolated-docs/newer','http://isolated-docs/stale'])

        # Two independent file descriptors contend for the same OS lock. Both
        # start with the same issued revision; exactly one must win.
        race_base=fence.snapshot(target)
        items=[create_xlsx('RACE_A'),create_xlsx('RACE_B')]
        def racer(data):
            other=ShadowRevisionFence(root)
            try:return ('committed',other.commit(target,race_base,data))
            except RevisionConflict:return ('conflict',None)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(racer,items))
        winners=[x for x,_ in results if x=='committed']
        rejects=[x for x,_ in results if x=='conflict']
        record('concurrent-writers-exactly-one-wins',len(winners)==1 and len(rejects)==1)
        record('concurrent-writes-leave-intact-workbook',read_a1(target.read_bytes()) in {'RACE_A','RACE_B'})
        # Real independent OS processes, not only threads in one interpreter.
        base_for_processes=fence.snapshot(target)
        processes=[]
        try:
            for marker in ('PROC_A','PROC_B'):
                child=subprocess.Popen([sys.executable,'-u','-m',
                    'tools.mcel_onlyoffice_shadow_revision_smoke','--worker',
                    str(root),str(target),base_for_processes,marker],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    text=True,cwd=ROOT)
                processes.append(child)
            # Workers block on stdin until both have been spawned. Each child
            # uses an independent file descriptor and an independent OS lock.
            def release(child):
                return child.communicate('GO\n',timeout=30)
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes=list(pool.map(release,processes))
            signals=[out.splitlines()[-1] if out.splitlines() else '' for out,err in outcomes]
            one_winner=(signals.count('WORKER_COMMITTED')==1 and
                        signals.count('WORKER_CONFLICT')==1 and
                        all(child.returncode==0 for child in processes))
            record('cross-process-lock-exactly-one-commit',one_winner,
                   'results='+repr(signals))
            record('cross-process-commits-leave-valid-workbook',
                   read_a1(target.read_bytes()) in ('PROC_A','PROC_B'))
        finally:
            for child in processes:
                if child.poll() is None:child.kill()
                try:child.wait(timeout=3)
                except subprocess.TimeoutExpired:pass
            for child in processes:
                for stream in (child.stdin,child.stdout,child.stderr):
                    if stream:stream.close()

    failed=[name for name,good in checks if not good]
    summary={'status':'PASS' if not failed else 'FAIL','checks':len(checks),
             'passed':len(checks)-len(failed),'failed':failed,
             'real_source_callback_executed':True,'production_modified':False,
             'requires_new_editor_grant_after_commit':True}
    print('MC_OO_SHADOW SUMMARY '+json.dumps(summary,sort_keys=True),flush=True)
    return summary

def worker_main(args):
    if len(args)!=4:return 2
    root,path,expected,marker=args
    input()  # parent releases both workers after spawning
    fence=ShadowRevisionFence(Path(root))
    try:fence.commit(Path(path),expected,create_xlsx(marker))
    except RevisionConflict:print('WORKER_CONFLICT',flush=True)
    else:print('WORKER_COMMITTED',flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(worker_main(sys.argv[2:]) if len(sys.argv)>1 and
                     sys.argv[1]=='--worker' else
                     0 if run()['status']=='PASS' else 1)
