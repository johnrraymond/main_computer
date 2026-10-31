#!/usr/bin/env python3
"""Build/check the browser-delivered IR from the *new* Task Manager notebook DSL candidate.

No changes to the live Task Manager or application-package admission.  This is a
source-identity guard for a static opt-in browser artifact, NOT proof of the
missing historical Phase 3 source or of runtime behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_notebook_projection import _validate_projection_scope
from main_computer.mcel_semantic_application_shell import validate_semantic_application_shell

SOURCE = ROOT / 'mcel_experiments/task-manager-notebook/application.js'
TARGET = ROOT / 'main_computer/web/applications/scripts/mcel-task-manager-notebook-candidate-ir.js'

def build() -> tuple[str, dict]:
    report = compile_dsl_application(SOURCE, write_candidate=False)
    if not report.valid or not report.normalized_ir:
        raise ValueError('MCEL_TASK_NOTEBOOK_CANDIDATE_COMPILATION_REFUSED: '+repr(report.diagnostics))
    ir = dict(report.normalized_ir)
    _validate_projection_scope(ir)
    validate_semantic_application_shell(ir)
    app = ir.get('application', {})
    if app.get('appId') != 'task-manager-notebook':
        raise ValueError('MCEL_TASK_NOTEBOOK_CANDIDATE_AUTHORITY_REFUSED')
    raw = SOURCE.read_bytes()
    metadata = {'schema':'mcel.task-notebook-source-bound-candidate.v1',
                'source':'mcel_experiments/task-manager-notebook/application.js',
                'sourceSha256':hashlib.sha256(raw).hexdigest(),
                'semanticFingerprint':report.semantic_fingerprint,
                'sourceBindingFingerprint':report.source_binding_fingerprint,
                'status':'new-candidate-not-historical-recovery'}
    js = ('/* GENERATED from new candidate DSL; do not hand edit. */\n'
          'window.McelTaskManagerNotebookCandidate = Object.freeze({\n'
          '  metadata: Object.freeze('+json.dumps(metadata,sort_keys=True,separators=(',',':'))+'),\n'
          '  ir: '+json.dumps(ir,sort_keys=True,separators=(',',':')).replace('<','\\u003c')+'\n'
          '});\n')
    return js, metadata

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true',help='Regenerate static browser delivery from authored DSL')
    parser.add_argument('--check',action='store_true',help='Require existing delivery to match current source exactly')
    args=parser.parse_args(argv)
    if not (args.write or args.check): parser.error('Select --write or --check')
    try:
        js, metadata=build()
        if args.write:
            TARGET.write_text(js,encoding='utf-8')
        if args.check and (not TARGET.is_file() or TARGET.read_text(encoding='utf-8')!=js):
            print('MCEL_TASK_NOTEBOOK_CANDIDATE_STALE_OR_MISSING',file=sys.stderr)
            return 1
        print('MCEL_TASK_NOTEBOOK_CANDIDATE_PASS',metadata['sourceSha256'], 'bytes',len(js.encode()))
        return 0
    except (ValueError,OSError) as error:
        print(str(error),file=sys.stderr)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
