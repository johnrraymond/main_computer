"""Network-target SLH application: distinct MCEL semantic app and read-only port."""
from __future__ import annotations

import json
from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_knowledge.semantic_composition import synthesize_composition, verify_composition
from main_computer.mcel_slh_target_navigation import TAB_STYLE, target_tabs

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path('mcel_sources/slh_network/application.js')
STYLE=Path('main_computer/web/applications/styles/mcel-semantic-workbench.css')
ASSETS=(
    'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js',
    'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js',
    'main_computer/web/applications/scripts/mcel-semantic-slh-network-ports.js',
)


def compile_network_slh_application(repo:Path=ROOT):
    if (repo/SOURCE).with_name('application.html').exists():
        raise ValueError('Network SLH is semantic-only; authored layout prohibited')
    compiled=compile_dsl_application(repo/SOURCE)
    if not compiled.valid or compiled.normalized_ir is None:
        raise ValueError('Network SLH semantic source invalid: '+str(compiled.diagnostics))
    ir=compiled.normalized_ir
    if (ir['application']['appId']!='slh-network' or ir.get('layouts') or
        {c['id'] for c in ir['capabilities']}!={'capability:network-slh-identity'} or
        {i['id'] for i in ir['intents']}!={'intent:inspect-network-slh'} or
        any(x['risk']!='external-read' for x in ir['effects'])):
        raise ValueError('Network SLH source exceeds read-only identity authority')
    composition=synthesize_composition(ir)
    verify_composition(ir,composition)
    return ir,composition


def render_network_slh_page(repo:Path=ROOT)->str:
    ir,composition=compile_network_slh_application(repo)
    data=json.dumps({'ir':ir,'composition':composition},separators=(',',':'),ensure_ascii=False).replace('<','\\u003c')
    css=(repo/STYLE).read_text(encoding='utf-8')
    if '</style' in css.lower():
        raise ValueError('Unsafe stylesheet')
    scripts=[]
    for name in ASSETS:
        content=(repo/name).read_text(encoding='utf-8')
        if '</script' in content.lower():
            raise ValueError('Unsafe application script')
        scripts.append('<script>\n'+content+'\n</script>')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Network Secure Local Hub · MCEL</title><style>{css}\n{TAB_STYLE}</style></head>
<body class="mcel-workbench-page" data-mcel-slh-target="network">
<header><h1>Secure Local Hub</h1>{target_tabs('network')}
<p>Connect to an existing Organization Service on another private-network computer.</p>
<p>First verify its TLS certificate fingerprint independently. Verification never logs you in or changes the remote hub.</p></header>
<div id="mcel-workbench" aria-label="MCEL generated network SLH workspace"></div>
<p id="mcel-workbench-status" role="status">Preparing network target verification</p>
<script type="application/json" id="mcel-generated-data">{data}</script>
{''.join(scripts)}
<script>(function(){{"use strict";
const message=document.getElementById("mcel-workbench-status");
try{{const data=JSON.parse(document.getElementById("mcel-generated-data").textContent);
const port=McelSemanticSlhNetworkPorts.create({{appId:data.ir.application.appId}});
const app=McelSemanticOperationalRuntime.mount(document.getElementById("mcel-workbench"),data.ir,
  {{capabilities:port,composition:data.composition,presentation:"expert"}});
window.__mcelSlhNetwork=Object.freeze({{app,composition:data.composition}});
message.textContent="Network target verification available · not authenticated";
}}catch(error){{message.dataset.error="true";message.textContent="Network SLH mount refused: "+String(error?.message||error);}}
}})();</script></body></html>'''
