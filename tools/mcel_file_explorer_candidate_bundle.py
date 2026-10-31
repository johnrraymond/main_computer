#!/usr/bin/env python3
"""Compile the independent File Explorer semantic candidate from author-owned DSL.

This is opt-in browser delivery. The existing read-only File Explorer is left intact.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_semantic_application_shell import validate_semantic_application_shell

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'mcel_experiments/file-explorer-candidate/application.js'
TARGET=ROOT/'main_computer/web/applications/scripts/mcel-file-explorer-candidate-ir.js'

def build():
    report=compile_dsl_application(SOURCE,write_candidate=False)
    if not report.valid or not report.normalized_ir:
        raise ValueError('MCEL_FILE_EXPLORER_SOURCE_INVALID: '+repr(report.diagnostics))
    ir=dict(report.normalized_ir)
    validate_semantic_application_shell(ir)
    if ir['application']['appId']!='file-explorer-candidate':
        raise ValueError('MCEL_FILE_EXPLORER_WRONG_APP_AUTHORITY')
    source_sha=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    meta={'schema':'mcel.file-explorer-source-bound-candidate.v1',
          'source':'mcel_experiments/file-explorer-candidate/application.js','sourceSha256':source_sha,
          'semanticFingerprint':report.semantic_fingerprint,'status':'opt-in-read-only-candidate'}
    js='/* Generated from author-owned DSL; do not hand-edit. */\n'+(
       'window.McelFileExplorerCandidateSource = Object.freeze({metadata:'+json.dumps(meta,sort_keys=True,separators=(',',':'))+',\n'+
       'ir:'+json.dumps(ir,sort_keys=True,separators=(',',':')).replace('<','\\u003c')+'});\n')
    return js,meta

def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True);mode.add_argument('--write',action='store_true');mode.add_argument('--check',action='store_true')
    args=p.parse_args()
    try:
        js,meta=build()
        if args.write:TARGET.write_text(js,encoding='utf-8')
        if args.check and (not TARGET.is_file() or TARGET.read_text(encoding='utf-8')!=js):
            print('MCEL_FILE_EXPLORER_CANDIDATE_STALE');return 1
        print('MCEL_FILE_EXPLORER_SOURCE_PASS',meta['sourceSha256']);return 0
    except (ValueError,OSError) as e:
        print('MCEL_FILE_EXPLORER_SOURCE_FAIL',e);return 2
if __name__=='__main__':raise SystemExit(main())
