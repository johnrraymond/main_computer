"""Package a public MCEL declarative keyed collection as a standalone browser app.

The application-specific artifact is validated IR. The shared implementation is
copied unchanged; neither domain-specific JavaScript nor application CSS is generated.
Production promotion and canonical durability are outside this opt-in scope.
"""
from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application

REPO = Path(__file__).resolve().parents[1]
ASSETS = REPO / 'main_computer/web/applications/scripts'
RUNTIME = 'mcel-declarative-collection-runtime.js'
STYLE = 'mcel-declarative-collection-runtime.css'


def build(source: Path, output: Path) -> dict:
    compiled = compile_dsl_application(Path(source), write_candidate=False)
    if not compiled.valid or not compiled.normalized_ir:
        return {'status': 'REFUSED', 'diagnostics': [dict(x) for x in compiled.diagnostics]}
    ir = compiled.normalized_ir
    nodes = [x for s in ir['surfaces'] for x in s.get('nodes', [])]
    if not any(x.get('nodeKind') == 'collection' for x in nodes):
        return {'status': 'REFUSED', 'reason': 'no-public-collection-surface'}
    if not any(x.get('nodeKind') == 'form' for x in nodes):
        return {'status': 'REFUSED', 'reason': 'no-public-form-surface'}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'app.ir.json').write_text(json.dumps(ir, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    shutil.copyfile(ASSETS / RUNTIME, output / RUNTIME)
    shutil.copyfile(ASSETS / STYLE, output / STYLE)
    title = html.escape(ir['application']['title'])
    markup = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
              f'<meta name="viewport" content="width=device-width,initial-scale=1">'
              f'<title>{title}</title><link rel="stylesheet" href="{STYLE}"></head>'
              f'<body><main id="mcel-root"></main><script src="{RUNTIME}"></script>'
              '<script>fetch("app.ir.json",{cache:"no-store"}).then(r=>{if(!r.ok)throw Error("IR load refused");return r.json()})'
              '.then(ir=>{window.mcelMounted=McelCollectionApplication.mount(document.getElementById("mcel-root"),ir)})'
              '.catch(e=>{document.getElementById("mcel-root").textContent="Application refused: "+e.message})'
              '</script></body></html>')
    (output / 'index.html').write_text(markup, encoding='utf-8')
    return {'status': 'BUILT_UNVERIFIED', 'appId': ir['application']['appId'],
            'source': str(source), 'output': str(output), 'files': ['index.html','app.ir.json',RUNTIME,STYLE]}
