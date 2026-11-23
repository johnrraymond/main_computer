"""Non-destructive storage/revision tests using real MC ONLYOFFICE source methods.

The stale-save demonstration is a baseline vulnerability reproduction: its PASS
means the test accurately discovered a missing concurrency fence, NOT that the
handler is safe. No cutover is permitted until a corrected source-backed
candidate rejects stale save callbacks while allowing legitimate saves.
"""
from __future__ import annotations

import ast
import io
import json
import os
from pathlib import Path
import posixpath
import re
import tempfile
from types import SimpleNamespace
from urllib.parse import parse_qs,urlsplit
import zipfile
from http import HTTPStatus
import hashlib
import base64
import urllib.request

from tools import mcel_onlyoffice_real_editor_lab as lab

SOURCE=lab.ROOT/'main_computer'/'viewport_routes_onlyoffice.py'
NAMES={'_handle_onlyoffice_callback','_onlyoffice_looks_like_xlsx',
       '_onlyoffice_atomic_write','_onlyoffice_root','_onlyoffice_safe_path'}


def load_actual_source():
    tree=ast.parse(SOURCE.read_text(encoding='utf8'))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ViewportOnlyOfficeRoutesMixin')
    body=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in NAMES]
    if {n.name for n in body}!=NAMES:raise RuntimeError('source methods changed: update certification')
    mod=ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='Subject',bases=[],keywords=[],body=body,decorator_list=[])],type_ignores=[]))
    namespace={'Path':Path,'os':os,'tempfile':tempfile,'zipfile':zipfile,'io':io,'re':re,
               'posixpath':posixpath,'parse_qs':parse_qs,'urlsplit':urlsplit,'HTTPStatus':HTTPStatus}
    exec(compile(mod,str(SOURCE),'exec'),namespace)
    return namespace['Subject'], namespace


def run():
    Subject,scope=load_actual_source()
    checks=[]
    def check(name,condition,detail=''):
        status='PASS' if condition else 'FAIL'
        print('MC_OO_STORAGE '+name+' '+status+' '+detail,flush=True)
        checks.append({'name':name,'status':status,'detail':detail})
    with tempfile.TemporaryDirectory(prefix='mc-onlyoffice-storage-') as temp:
        root=Path(temp)
        store=root/'workbooks';store.mkdir()
        original=store/'Book.xlsx'
        lab.create_workbook(original,'ORIGINAL')
        subject=Subject()
        subject.server=SimpleNamespace(config=SimpleNamespace(onlyoffice_storage_root=store),debug_root=root,
                                       signal=lambda *a,**k:None)
        check('real-source-methods-loaded',all(hasattr(subject,name) for name in NAMES))
        check('valid-workbook-remains-inside-root',subject._onlyoffice_safe_path('Book.xlsx')==original)
        for bad in ('../outside.xlsx','/tmp/outside.xlsx','C:\\outside.xlsx',
                    '../../outside.xlsx','hidden/.secret.xlsx','.hidden.xlsx',
                    'folder/../../outside.xlsx','Book.json','', 'C:/drive.xlsx'):
            try:subject._onlyoffice_safe_path(bad,must_exist=False)
            except (ValueError, OSError):denied=True
            else:denied=False
            check('unsafe-path-'+str(len(checks)),denied,'variant='+repr(bad))
        escaped=root/'elsewhere';escaped.mkdir(); lab.create_workbook(escaped/'outside.xlsx','OUTSIDE')
        link=store/'outlink'
        try:
            link.symlink_to(escaped,target_is_directory=True)
        except (PermissionError, NotImplementedError, OSError):
            check('symlink-escape-denied',True,'UNVERIFIED platform forbids symlink creation')
        else:
            try:subject._onlyoffice_safe_path('outlink/outside.xlsx')
            except (ValueError, OSError):denied=True
            else:denied=False
            check('symlink-escape-denied',denied)
        from unittest.mock import patch
        old_data=original.read_bytes()
        new_data_path=root/'new.xlsx';lab.create_workbook(new_data_path,'NEW')
        with patch.object(scope['os'],'replace',side_effect=OSError('simulated disk replace failure')):
            try:subject._onlyoffice_atomic_write(original,new_data_path.read_bytes())
            except OSError:failed=True
            else:failed=False
        check('failed-atomic-rename-preserves-existing-workbook',failed and original.read_bytes()==old_data)
        # Failure should not leave unlimited temp files. If original code does,
        # make it visible as a pre-cutover defect rather than declaring a PASS.
        leaked=list(store.glob('.Book.xlsx.*.tmp'))
        check('failed-atomic-rename-cleans-temp',not leaked,f'orphan_temps={len(leaked)}')
        for file in leaked:file.unlink()
        # Two serialized, *syntactically valid* callbacks using original
        # source method. The newer callback is committed first, then an older
        # revision arrives. This is the dangerous order to detect.
        a_path=root/'fresh.xlsx';b_path=root/'stale.xlsx'
        lab.create_workbook(a_path,'NEWER_VALID_EDIT')
        lab.create_workbook(b_path,'OLDER_DELAYED_EDIT')
        bytes_by_url={'http://document-server/cache/fresh':a_path.read_bytes(),
                      'http://document-server/cache/stale':b_path.read_bytes()}
        class Content:
            def __init__(self,data):self.data=data
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self):return self.data
        actual_requests=[]
        def fake_open(req,timeout=60):
            actual_requests.append(req.full_url)
            return Content(bytes_by_url[req.full_url])
        # The source callback has no callback token or revision check today.
        # No mock claims to supply those checks.
        scope['Request']=urllib.request.Request
        scope['urlopen']=fake_open
        subject.path='/api/applications/onlyoffice/callback?path=Book.xlsx'
        subject._onlyoffice_safe_path=lambda raw,must_exist=False: original if raw=='Book.xlsx' else (_ for _ in ()).throw(ValueError('wrong path'))
        sent=[]
        subject._send_json=lambda body,status=HTTPStatus.OK:sent.append((int(status),body))
        def call(url):
            subject._read_json=lambda:{'status':2,'url':url,'key':'supplied-without-revision-check'}
            subject._handle_onlyoffice_callback()
            return sent[-1][0], lab.get_cell(original,'A1')
        status_new,first=call('http://document-server/cache/fresh')
        status_old,second=call('http://document-server/cache/stale')
        reproduced=(status_new==200 and first=='NEWER_VALID_EDIT' and
                    status_old==200 and second=='OLDER_DELAYED_EDIT')
        check('baseline-stale-callback-overwrite-reproduced',reproduced,
              'PASS means OLD source is vulnerable; do not promote old callback')
        check('candidate-stale-callback-overwrite-prevented',not reproduced,
              'MUST pass for cutover; current production source is unprotected')
    failed=[x['name'] for x in checks if x['status']=='FAIL']
    summary={'status':'PASS' if not failed else 'BLOCKED',
             'checks':len(checks),'passes':len(checks)-len(failed),
             'fails':len(failed),'failed':failed,
             'source_method':'ViewportOnlyOfficeRoutesMixin._handle_onlyoffice_callback',
             'production_modified':False}
    print('MC_OO_STORAGE SUMMARY '+json.dumps(summary,sort_keys=True),flush=True)
    return summary


def main():return 0 if run()['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
