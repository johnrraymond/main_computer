"""Canonical paired-source native Document Editor host."""
from pathlib import Path
import json
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_applications_registry import attach_inert_presentation

ROOT = Path(__file__).resolve().parents[1]
WEB = Path('main_computer/web/applications')
SCRIPTS = ('mcel-document-engine.js','mcel-element-registry.js','mcel-elements-core.js',
           'mcel-document-surface-element.js','mcel-semantic-operational-runtime.js','mcel-document-candidate-port.js','mcel-document-workspace.js')
STYLES = ('document.css','mcel-semantic-workbench.css','mcel-document-surface-element.css','mcel-document-workspace.css')


def compile_document_candidate(repo=ROOT):
    report = compile_dsl_application(repo/'mcel_apps/document/application.js')
    if not report.valid:
        raise ValueError(f'Document candidate compilation failed: {report.diagnostics}')
    ir = report.normalized_ir
    surfaces = ir['surfaces']
    nodes = [n for s in surfaces for n in s['nodes'] if n['nodeKind']=='document-surface']
    if ir['application']['appId']!='document' or len(nodes)!=1 or nodes[0]['id']!='surface-node:document.draft' or nodes[0]['elementId']!='element.authoring.document-surface':
        raise ValueError('Unexpected document candidate contract')
    return ir


def render_document_candidate(repo=ROOT):
    ir = compile_document_candidate(repo)
    payload = json.dumps(ir,ensure_ascii=True).replace('<','\\u003c')
    def asset(kind,name):
        tag = 'script' if kind=='scripts' else 'style'
        content = (repo/WEB/kind/name).read_text(encoding='utf-8')
        if f'</{tag}' in content.lower(): raise ValueError('Unsafe inline asset '+name)
        return f'<{tag}>\n{content}\n</{tag}>'
    page = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Document Editor</title>''' + '\n'.join(asset('styles',f) for f in STYLES) + '''</head><body class="mcel-workbench-page mcel-rich-document-workspace">
<div id="mcel-workbench" aria-label="Native MCEL Document Editor"></div><div id="mcel-workbench-status" role="status"></div>
<script type="application/json" id="mcel-workbench-ir">''' + payload + '</script>\n' + '\n'.join(asset('scripts',f) for f in SCRIPTS) + '''
<script>(function(){"use strict";
const root=document.getElementById('mcel-workbench'),status=document.getElementById('mcel-workbench-status');
let app;try{const ir=JSON.parse(document.getElementById('mcel-workbench-ir').textContent);
const port=McelDocumentCandidatePort.create(()=>app);
app=McelSemanticOperationalRuntime.mount(root,ir,{capabilities:port,presentation:'expert'});
window.__mcelWorkbench=Object.freeze({app,appId:'document',getDocumentMetadata:port.getMetadata});
window.addEventListener('beforeunload',event=>{if(port.isDirty()){event.preventDefault();event.returnValue='';}});
McelDocumentWorkspace.mount(root,app,port);
status.textContent='Document Editor ready';
}catch(e){status.dataset.error='true';status.textContent='Document Editor unavailable: '+String(e.message||e);throw e;}
})();</script></body></html>'''
    return attach_inert_presentation(page,repo,'document','mcel-workbench',promoted=True)
