"""Opt-in upgraded semantic Calculator route, preserving the earlier candidate.

All controls, data and authority originate in the paired authored DSL/HTML.
The host only chooses the already-existing Calculator engines and provides
shared purpose-based styling (no authored fixed layout tree).
"""
from __future__ import annotations
import json
from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_knowledge.workspace_plan import plan_workspace, verify_workspace_plan
from main_computer.mcel_phase2c_candidate_host import ROOT, CandidateHostError

SOURCE = Path('tools/fixtures/mcel_calculator_expanded/application.js')
ROUTE = '/applications/mcel-semantic-calculator-expanded'
ASSETS = [
    'main_computer/web/applications/scripts/calculator-core.js',
    'main_computer/web/applications/scripts/calculator-capabilities.js',
    'main_computer/web/applications/scripts/calculator-view-model.js',
    'main_computer/web/applications/scripts/dom-bindings/chat-console.js',
    'main_computer/web/applications/scripts/chat-thread-store.js',
    'main_computer/web/applications/scripts/chat-thread-controller.js',
    'main_computer/web/applications/scripts/chat-console-state.js',
    'main_computer/web/applications/scripts/chat-console.js',
    'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js',
    'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js',
    'main_computer/web/applications/scripts/mcel-calculator-expanded-port.js',
]
INTENT_OPS = {
    'evaluate':('calculator-core','evaluate','evaluateCalculatorExpression'),
    'draw-graph':('calculator-core','graph','sampleGraphExpression'),
    'evaluate-symbolic':('calculator-service','mathics','evaluateMathics'),
    'ask-symbolic-model':('calculator-service','mathics-ask','askMathicsModel'),
    'ask-arithmetic-model':('calculator-service','arithmetic-ask','askArithmeticModel'),
    'ask-graph-model':('calculator-service','graph-ask','askGraphModel'),
    'ask-result-question':('calculator-service','question','askResultQuestion'),
    'apply-arithmetic-suggestion':('calculator-core','evaluate','evaluateCalculatorExpression'),
    'apply-graph-suggestion':('calculator-core','graph','sampleGraphExpression'),
    'apply-symbolic-suggestion':('calculator-core','normalize-symbolic','normalizeSymbolicSuggestion'),
    'edit-graph-expression':('calculator-core','edit-graph','editGraphExpression'),
    'clear-symbolic':('calculator-core','clear-symbolic','clearSymbolic'),
    'calculator-chat':('calculator-service','chat','calculatorEmbeddedChat'),
}

def compile_expanded(repo: Path = ROOT) -> dict:
    report=compile_dsl_application(repo/SOURCE)
    if not report.valid or not report.normalized_ir:
        raise CandidateHostError(f'Expanded paired source invalid: {report.diagnostics}')
    ir=report.normalized_ir
    if ir.get('application',{}).get('appId') != 'semantic-calculator-expanded' or ir.get('layouts'):
        raise CandidateHostError('expanded candidate identity/layout invalid')
    sf=ir.get('surfaces',[])
    if len(sf)!=1 or not sf[0].get('interfaceIntent',{}).get('hierarchy'):
        raise CandidateHostError('expanded candidate missing authored semantic hierarchy')
    intents={i['id']:i for i in ir.get('intents',[])}
    if set(intents)!={*(f'intent:{k}' for k in INTENT_OPS), 'intent:switch-mode'}:
        raise CandidateHostError('expanded candidate intents changed')
    caps={c['id']:c for c in ir.get('capabilities',[])}
    effects={e['id']:e for e in ir.get('effects',[])}
    if set(caps) != {'capability:calculator-core','capability:calculator-service'} or len(effects)!=len(INTENT_OPS):
        raise CandidateHostError('expanded candidate capability set changed')
    for cap in caps.values():
        if cap.get('risk')!='external-read':raise CandidateHostError('expanded capability authority changed')
    for name,(cap_name,op_name,method) in INTENT_OPS.items():
        intent=intents[f'intent:{name}']
        effect=effects.get(f'effect:{name}.request')
        cap=caps[f'capability:{cap_name}']
        operation=next((o for o in cap['operations'] if o['name']==op_name),None)
        if not effect or not operation or (intent.get('operationKind'), intent.get('operation'), intent.get('runtimeMethod'), intent.get('risk')) != ('capability',op_name,method,'external-read'):
            raise CandidateHostError(f'expanded operation changed: {name}')
        if effect.get('owner')!={'ref':intent['id']} or effect.get('authority')!={'ref':cap['id']} or effect.get('target')!={'ref':cap['id']} or effect.get('risk')!='external-read' or operation.get('risk')!='external-read' or operation.get('runtimeMethod')!=method:
            raise CandidateHostError(f'expanded operation authority changed: {name}')
    return ir

def render_expanded_page(repo: Path = ROOT) -> str:
    ir=compile_expanded(repo)
    plan=plan_workspace(ir)
    verify_workspace_plan(ir,plan)
    plan_payload=json.dumps(plan,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
    payload=json.dumps(ir,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
    def asset(path):
        script=(repo/path).read_text(encoding='utf8')
        if '</script' in script.lower():raise CandidateHostError('asset script termination')
        return '<script>\n'+script+'\n</script>'
    scripts='\n'.join(asset(Path(x)) for x in ASSETS)
    expert_css=(repo/'main_computer/web/applications/styles/mcel-semantic-expert.css').read_text(encoding='utf8')
    expert_css+='\n'+(repo/'main_computer/web/applications/styles/chat-console.css').read_text(encoding='utf8')
    if '</style' in expert_css.lower():
        raise CandidateHostError('style termination')
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MCEL Semantic Calculator · Expanded</title>
<style>''' + expert_css + '''</style></head><body class="mcel-expert-page"><header><h1>Semantic Calculator</h1><p>Arithmetic and units · Function graphs · Symbolic mathematics · Optional AI assistance</p></header>
<div id="mcel-expanded-root" aria-label="Generated semantic Calculator"></div><div id="mcel-expanded-diagnostic" role="status" aria-live="polite"></div>
<script id="mcel-expanded-ir" type="application/json">''' + payload + '</script>\n<script id="mcel-expanded-plan" type="application/json">' + plan_payload + '</script>\n' + scripts + '''<script>
(function(){"use strict";
 const status=document.getElementById('mcel-expanded-diagnostic');
 try {
   const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
   const port=McelCalculatorExpandedPort.create({core:MainComputerCalculatorCore,services:MainComputerCalculatorCapabilities});
   const workspacePlan=JSON.parse(document.getElementById('mcel-expanded-plan').textContent);
   const app=McelSemanticOperationalRuntime.mount(document.getElementById('mcel-expanded-root'),ir,{capabilities:port,presentation:'expert',workspacePlan});
   window.__mcelExpanded=Object.freeze({app,appId:ir.application.appId});
   status.textContent='Semantic Calculator ready';
 } catch(e){status.dataset.error='true';status.textContent='Calculator refused: '+String(e?.message||e);}
})();
</script></body></html>'''
