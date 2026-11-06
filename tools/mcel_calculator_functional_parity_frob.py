"""Read-only incumbent ↔ semantic Calculator functional-parity diagnostic.

The incumbent is rendered from its *actual canonical compiled host markup* and
runs its *actual* Calculator JavaScript in explicit standalone mode. The semantic
candidate runs the real paired DSL/compiler/operational runtime. Both use the
same checked-in deterministic core. Optional HTTP services are replaced only
inside the isolated browser by a recorded, deterministic mock.

This is a gap-finding Frob, not a promotion gate: a simulated HTTP result does
not certify Mathics, model providers, hosted Windows routing, or human design.
No production file, route, application state, or real remote endpoint is changed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from main_computer.mcel_calculator_expanded_host import compile_expanded, render_expanded_page
from main_computer.mcel_calculator_host_projection import project as project_incumbent
from tools.mcel_calculator_production_baseline_diagnostic import inspect_baseline
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT = Path(__file__).resolve().parents[1]
REPORT = Path('runtime/reports/mcel-calculator-functional-parity/report.json')
WEB_SCRIPTS = Path('main_computer/web/applications/scripts')
CANDIDATE_SOURCE = Path('tools/fixtures/mcel_calculator_expanded/application.js')
CANDIDATE_RUNTIME = WEB_SCRIPTS / 'mcel-semantic-operational-runtime.js'
INCUMBENT_SOURCE = WEB_SCRIPTS / 'calculator.js'


@dataclass(frozen=True)
class Feature:
    id: str
    label: str
    incumbent_dom: tuple[str, ...]
    incumbent_code: tuple[str, ...]
    candidate_intents: tuple[str, ...]
    candidate_dom: tuple[str, ...] = ()
    candidate_runtime: tuple[str, ...] = ()
    difference: str = ''
    needs_live: bool = False


# These independent expectations describe real incumbent affordances. The
# authoring DSL is NEVER used as the oracle for which incumbent features exist.
FEATURES = (
    Feature('arithmetic', 'Arithmetic, units, and invalid expressions',
            ('calculator-display', 'calculator-result'), ('calculateExpression',), ('intent:evaluate',)),
    Feature('expression-keypad', 'Numeric keypad, clear, backspace, equals',
            ('calculator-display',), ('applyCalculatorToken', 'backspaceCalculatorExpression'),
            ('intent:evaluate',), candidate_runtime=('data-mcel-expression-pad',)),
    Feature('graph', 'Function graph with numeric ranges',
            ('calculator-graph-canvas', 'calculator-graph-expression'), ('drawCalculatorGraph',),
            ('intent:draw-graph',)),
    Feature('symbolic-evaluate', 'Mathics symbolic evaluation',
            ('calculator-mathics-expression', 'calculator-mathics-output'), ('evaluateCalculatorMathics',),
            ('intent:evaluate-symbolic',), needs_live=True),
    Feature('arithmetic-model-handoff', 'Model expression applied to arithmetic and evaluated',
            ('calculator-ask-model',), ('askCalculatorModel', 'calculateExpression();'),
            ('intent:ask-arithmetic-model', 'intent:apply-arithmetic-suggestion'),
            difference='A displayed model suggestion is not an applied and evaluated expression.', needs_live=True),
    Feature('graph-model-handoff', 'Model graph expression applied and drawn',
            ('calculator-scientific-ask-model',), ('askScientificCalculatorModel', 'const graph = drawCalculatorGraph();'),
            ('intent:ask-graph-model', 'intent:apply-graph-suggestion'),
            difference='A displayed suggestion is not an updated and drawn graph.', needs_live=True),
    Feature('symbolic-model-handoff', 'Model Mathics expression applied to symbolic input',
            ('calculator-mathics-ask-model',), ('askCalculatorMathicsModel', 'applyCalculatorMathicsViewModel'),
            ('intent:ask-symbolic-model', 'intent:apply-symbolic-suggestion'),
            difference='A displayed suggestion is not a populated symbolic expression.', needs_live=True),
    Feature('symbolic-clear', 'Clear symbolic expression and output',
            ('calculator-mathics-clear',), ('clearCalculatorMathics',),
            ('intent:evaluate-symbolic', 'intent:clear-symbolic'),
            difference='Resetting a form does not prove the previously computed output was cleared.'),
    Feature('graph-editor', 'Graph token/template editing and graph-specific clear/backspace',
            ('calculator-graph-expression',), ('insertCalculatorGraphText', 'data-calc-graph-template'),
            ('intent:draw-graph', 'intent:edit-graph-expression'),
            difference='Free-text graph input alone does not preserve graph editing controls.'),
    Feature('graph-reset', 'Graph range reset semantics',
            ('calculator-graph-reset',), ('resetCalculatorGraphView',),
            ('intent:draw-graph',),
            difference='The incumbent resets Y to [-5,5], while the candidate currently defaults to [-10,10].'),
    Feature('result-question', 'Result Q&A with full current computation context',
            ('calculator-qa-prompt', 'calculator-qa-answer'), ('calculatorQaContext',),
            ('intent:ask-result-question',),
            difference='Reduced result summaries do not prove full expression/range context parity.', needs_live=True),
    Feature('contextual-chat', 'Embedded multi-turn Calculator Chat with session context',
            ('calculator-chat-panel', 'calculator-chat-notebook'),
            ('mountCalculatorEmbeddedChat', 'calculatorEmbeddedChatContextSnapshot'),
            ('intent:calculator-chat',),
            difference='One-shot result Q&A is not an embedded multi-turn Calculator Chat.', needs_live=True),
    Feature('mode-state', 'Mode switching, focus, and working state preservation',
            ('calculator-mode-basic', 'calculator-mode-graphing'), ('setCalculatorMode',),
            ('intent:switch-mode',), needs_live=False),
)


class BrowserProbeError(RuntimeError):
    def __init__(self, message: str, cases: list[dict], page_errors: dict):
        super().__init__(message)
        self.cases = cases
        self.page_errors = page_errors


# Repair instructions are independent of candidate layout and name the actual
# runtime/authoring surfaces to inspect when a witness fails.
REPAIR_GUIDANCE = {
    'arithmetic': 'Verify retained last-good answer and failed receipt for malformed expressions against the incumbent error presentation.',
    'expression-keypad': 'Keep the generic expression-workbench keypad, and verify clear/backspace/equals in the actual canonical host.',
    'graph': 'Preserve sampling and discontinuity handling while rendering the graph from the same calculator-core.js authority.',
    'symbolic-evaluate': 'Exercise /api/applications/calculator/mathics/evaluate against an authorized live Mathics instance.',
    'arithmetic-model-handoff': 'Connect the returned arithmetic suggestion to editable expression state and evaluate through the existing deterministic core; test the full click-to-result sequence.',
    'graph-model-handoff': 'Connect the returned graph suggestion to graph input and execute drawing with the current range; test the full click-to-graph sequence.',
    'symbolic-model-handoff': 'Populate the Mathics input from the returned model expression, preserving review before evaluation.',
    'symbolic-clear': 'Provide a semantic clear operation that clears both the Mathics expression and prior symbolic result, not only the input field.',
    'graph-editor': 'Preserve graph insertion templates, token editing, clear, and backspace through semantic controls or a generic expression-editor capability.',
    'graph-reset': 'Restore the incumbent -10/10 X and -5/5 Y defaults, and redraw the graph when reset is invoked.',
    'result-question': 'Send the incumbent expression, result, graph expression/status/range, and Mathics expression/output to the existing Q&A service; do not confuse matching endpoint names with context parity.',
    'contextual-chat': 'Reuse the existing embedded multi-turn chat service and Calculator session context (thread metadata and linked target) through the semantic interface.',
    'mode-state': 'Verify mode transitions, keyboard focus, and working inputs across all operational panels in the actual host.',
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _source(repo: Path, path: Path) -> str:
    return (repo/path).read_text(encoding='utf-8')


def source_inventory(repo: Path, *, ir: dict | None = None, markup: str | None = None,
                     candidate_js: str | None = None, incumbent_js: str | None = None,
                     candidate_runtime: str | None = None) -> dict:
    """Independent source comparison. Injectable parameters support mutation tests."""
    ir = ir if ir is not None else compile_expanded(repo)
    markup = markup if markup is not None else project_incumbent(repo)
    candidate_js = candidate_js if candidate_js is not None else _source(repo, CANDIDATE_SOURCE)
    incumbent_js = incumbent_js if incumbent_js is not None else _source(repo, INCUMBENT_SOURCE)
    candidate_runtime = candidate_runtime if candidate_runtime is not None else _source(repo, CANDIDATE_RUNTIME)
    intent_ids = {i['id'] for i in ir.get('intents', [])}
    region_ids = {r['id'].split('.')[-1] for s in ir.get('surfaces', [])
                  for r in s.get('interfaceIntent', {}).get('regions', [])}
    rows: dict[str, dict] = {}
    for feature in FEATURES:
        absent_dom = [s for s in feature.incumbent_dom if f'id="{s}"' not in markup]
        absent_code = [s for s in feature.incumbent_code if s not in incumbent_js]
        absent_intents = [s for s in feature.candidate_intents if s not in intent_ids]
        absent_candidate_dom = [s for s in feature.candidate_dom if s not in candidate_js]
        absent_runtime = [s for s in feature.candidate_runtime if s not in candidate_runtime]
        baseline_ok = not (absent_dom or absent_code)
        candidate_ok = not (absent_intents or absent_candidate_dom or absent_runtime)
        status = ('UNVERIFIED' if not baseline_ok else
                  'MISSING' if not candidate_ok else 'SOURCE_COVERED')
        rows[feature.id] = {
            'label': feature.label,
            'status': status,
            'incumbent': {'markupIdsPresent': sorted(set(feature.incumbent_dom)-set(absent_dom)),
                          'missingMarkupIds': absent_dom,
                          'codeSignaturesPresent': sorted(set(feature.incumbent_code)-set(absent_code)),
                          'missingCodeSignatures': absent_code},
            'candidate': {'intentIdsPresent': sorted(set(feature.candidate_intents)-set(absent_intents)),
                          'missingIntentIds': absent_intents,
                          'missingDomSignatures': absent_candidate_dom,
                          'missingRuntimeSignatures': absent_runtime},
            'difference': feature.difference or None,
            'repairGuidance': REPAIR_GUIDANCE[feature.id],
            'implementationFiles': [str(CANDIDATE_SOURCE),
                str(WEB_SCRIPTS / 'mcel-calculator-expanded-port.js'),
                str(CANDIDATE_RUNTIME)],
            'requiredNextEvidence': ('Isolated browser regression, then live authorized provider/Windows-host parity'
                                     if feature.needs_live else 'Isolated browser regression, then real canonical-host parity'),
            'requiresLiveEvidence': feature.needs_live,
            'browserScenario': None,
            'nextAction': ('Repair incumbent baseline evidence before comparing' if not baseline_ok
                           else ('Implement and test the missing semantic capability without adding layout' if not candidate_ok
                                 else 'Exercise the shared semantic capability in the browser')),
        }
    return {'features': rows, 'declaredIntentIds': sorted(intent_ids),
            'regionIds': sorted(region_ids), 'sourceEvidence': {
                'incumbentProjectionSha256': _sha(markup),
                'incumbentJavascriptSha256': _sha(incumbent_js),
                'candidateJavascriptSha256': _sha(candidate_js),
                'candidateRuntimeSha256': _sha(candidate_runtime),
            }}


def incumbent_probe_html(repo: Path) -> str:
    """Actual projected incumbent DOM and actual incumbent JS; explicit no-host mode."""
    paths = ('calculator-core.js', 'calculator-view-model.js',
             'calculator-capabilities.js', 'dom-bindings/calculator.js', 'calculator.js')
    scripts = ['<script>window.MainComputerCalculatorStandaloneMode=true;</script>']
    for asset in paths:
        content = _source(repo, WEB_SCRIPTS / asset)
        if '</script' in content.lower():
            raise ValueError(f'Unsafe inline script terminator in {asset}')
        scripts.append('<script>\n'+content+'\n</script>')
    return ('<!doctype html><html><head><meta charset="utf-8"></head><body>'
            + project_incumbent(repo) + '\n' + '\n'.join(scripts) + '</body></html>')


def _select_mode(page, mode: str) -> None:
    selector = page.locator(f'[data-mcel-mode-tabs] button[data-value="{mode}"]')
    if selector.count() and selector.is_visible():
        selector.click()
    else:
        page.locator('select[data-intent="intent:switch-mode"]').select_option(mode)
    page.wait_for_function('m => window.__mcelExpanded.app.readState("state:mode") === m',arg=mode)


def _candidate_eval(page, expression: str) -> dict:
    form = page.locator('form[data-intent="intent:evaluate"]')
    previous=page.locator('li[data-intent="intent:evaluate"]').count()
    form.locator('input[name="expression"]').fill(expression)
    form.locator('button[type="submit"]').click()
    page.wait_for_function('''count =>
      document.querySelectorAll('li[data-intent="intent:evaluate"]').length > count''',
      arg=previous,timeout=5000)
    return {'result':page.evaluate('window.__mcelExpanded.app.readState("state:arithmetic-result")'),
            'failed':bool(page.locator('[data-intent="intent:evaluate"][data-disposition="failed"]').count())}


def _mock_http(page) -> None:
    page.evaluate('''() => {
      window.__calcParityRequests=[];
      window.fetch=async (url, options={}) => {
        const path=String(url); const payload=JSON.parse(options.body || '{}');
        window.__calcParityRequests.push({path,payload});
        const data=path==='/api/applications/calculator/mathics/evaluate' ?
          {ok:true,result_text:'4'}:
          path==='/api/applications/calculator/mathics/ask' ?
          {ok:true,expression:'Integrate[x^2,x]'}:
          path==='/api/applications/calculator/qa' ?
          {ok:true,answer:'The result is four.'}:
          path==='/api/chat' ?
          {ok:true,content:'2+2'} : {ok:false,error:'unexpected-path'};
        return {ok:data.ok,status:data.ok?200:404,json:async()=>data};
      };
    }''')


def _comparison(case: str, feature: str, incumbent: Any, candidate: Any,
                same: bool, *, mocked: bool = False, note: str = '') -> dict:
    return {'id':case,'feature':feature,'status':'PASS' if same else 'MISMATCH',
            'incumbent':incumbent,'candidate':candidate,'mockedHttp':mocked,
            'note':note or None}


def browser_comparison(repo: Path) -> dict:
    """Differential browser witnesses with both *real* UIs, never live effects."""
    from playwright.sync_api import sync_playwright
    evidence: list[dict] = []
    page_errors: dict[str, list[str]] = {'incumbent': [], 'candidate': []}
    with sync_playwright() as p:
        browser, backend = launch_semantic_browser(p)
        try:
            left = browser.new_page(viewport={'width':1100,'height':900})
            right = browser.new_page(viewport={'width':1100,'height':900})
            left.on('pageerror', lambda exc: page_errors['incumbent'].append(str(exc)[:400]))
            right.on('pageerror', lambda exc: page_errors['candidate'].append(str(exc)[:400]))
            # Never load remotely fetched resources. Both are assembled locally.
            left.route('**/*', lambda route: route.abort())
            right.route('**/*', lambda route: route.abort())
            # The two offline documents use opaque origins. Provide isolated
            # in-memory storage exclusively for Chat Console's thread store;
            # this never accesses the user's canonical browser storage.
            storage_stub="""() => {const data=new Map();
              Object.defineProperty(window,'localStorage',{configurable:true,value:{
                getItem:k=>data.get(String(k))??null,
                setItem:(k,v)=>data.set(String(k),String(v)),
                removeItem:k=>data.delete(String(k)),
                clear:()=>data.clear(),key:i=>[...data.keys()][i]??null,
                get length(){return data.size;}}});}"""
            left.evaluate(storage_stub)
            right.evaluate(storage_stub)
            left.set_content(incumbent_probe_html(repo),wait_until='load')
            right.set_content(render_expanded_page(repo),wait_until='load')
            if not left.evaluate('typeof window.MainComputerCalculatorRuntime === "object"'):
                raise RuntimeError('Incumbent runtime did not mount')
            if right.locator('#mcel-expanded-diagnostic').inner_text() != 'Semantic Calculator ready':
                raise RuntimeError('Semantic candidate did not mount')
            # The deterministic core is shared, but this tests that both real
            # application event paths actually produce matching visible outputs.
            for expression in ('2.5 + 3.25', '3 m + 40 cm', '7 * (3 + 2)'):
                legacy = left.evaluate('e => window.MainComputerCalculatorRuntime.evaluateExpression({expression:e})',expression)
                current = _candidate_eval(right,expression)
                evidence.append(_comparison('arithmetic:'+expression,'arithmetic',
                    legacy.get('result'),current['result'],
                    legacy.get('ok') is True and legacy.get('result')==current['result']))
            # Bad units must fail rather than invent a number. Error text and
            # retention policies are separately reported, not assumed equal.
            prior=right.evaluate('window.__mcelExpanded.app.readState("state:arithmetic-result")')
            old_bad=left.evaluate('e => window.MainComputerCalculatorRuntime.evaluateExpression({expression:e})','3 m + 2 s')
            _candidate_eval(right,'3 m + 2 s')
            right.locator('li[data-intent="intent:evaluate"][data-disposition="failed"]').wait_for(timeout=5000)
            candidate_after_error=right.evaluate('window.__mcelExpanded.app.readState("state:arithmetic-result")')
            evidence.append(_comparison('arithmetic:incompatible-units','arithmetic',
                {'failed':old_bad.get('ok') is False,'message':left.locator('#calculator-result').inner_text()},
                {'failed':True,'lastGoodResultRetained':candidate_after_error==prior},
                old_bad.get('ok') is False and candidate_after_error==prior,
                note='Both refuse incompatible units; legacy displays error, candidate records failed receipt and retains last result.'))
            # Genuine keypad path, including clear and submit; not merely core API.
            left.locator('[data-calc-action="clear"]').first.click()
            pad = right.locator('[data-mcel-expression-pad]')
            pad.locator('button[data-pad-action="clear"]').click()
            for key in ('2','+','3'):
                left.locator('[data-calc-key="'+key+'"]').first.click()
                pad.locator('button[data-pad-action="'+key+'"]').click()
            incumbent_input = left.locator('#calculator-display').input_value()
            candidate_input = right.locator('form[data-intent="intent:evaluate"] input[name="expression"]').input_value()
            evidence.append(_comparison('keypad-entry','expression-keypad',incumbent_input,
                                        candidate_input,incumbent_input==candidate_input))
            left.locator('[data-calc-action="equals"]').first.click()
            pad.locator('button[data-pad-action="submit"]').click()
            right.locator('[data-state="state:arithmetic-result"]').get_by_text('5').wait_for(timeout=5000)
            legacy_value = left.locator('#calculator-result').inner_text()
            new_value = right.evaluate('window.__mcelExpanded.app.readState("state:arithmetic-result")')
            evidence.append(_comparison('keypad-equals','expression-keypad',legacy_value,new_value,
                                        legacy_value==new_value))
            # Mode transitions must retain the user's expression, even though
            # the incumbent and candidate expose different mode topologies.
            legacy_before=left.locator('#calculator-display').input_value()
            candidate_before=right.locator('form[data-intent="intent:evaluate"] input[name="expression"]').input_value()
            left.evaluate('window.MainComputerCalculatorRuntime.switchMode({mode:"graphing"})')
            _select_mode(right,'graphing')
            left.evaluate('window.MainComputerCalculatorRuntime.switchMode({mode:"basic"})')
            _select_mode(right,'basic')
            legacy_after=left.locator('#calculator-display').input_value()
            candidate_after=right.locator('form[data-intent="intent:evaluate"] input[name="expression"]').input_value()
            evidence.append(_comparison('mode-preserves-arithmetic-input','mode-state',
                {'before':legacy_before,'after':legacy_after},
                {'before':candidate_before,'after':candidate_after},
                legacy_before==legacy_after and candidate_before==candidate_after))
            # Graph uses the shared core; compare the real legacy draw and
            # actual semantic runtime plot's sampled near-origin values.
            legacy_graph = left.evaluate('''() => window.MainComputerCalculatorRuntime.drawGraph({
              expression:'x^2',range:{xMin:-2,xMax:2,yMin:-2,yMax:5}})''')
            _select_mode(right,'graphing')
            graph_form = right.locator('form[data-intent="intent:draw-graph"]')
            for k,v in [('expression','x^2'),('xMin','-2'),('xMax','2'),('yMin','-2'),('yMax','5')]:
                graph_form.locator('input[name="'+k+'"]').fill(v)
            graph_form.locator('button[type="submit"]').click()
            right.locator('[data-mcel-plot] svg path').wait_for(timeout=5000)
            samples = right.evaluate('window.__mcelExpanded.app.readState("state:graph-points")')
            graph_valid = bool(legacy_graph and legacy_graph.get('ok') and len(samples)>100
                               and any(abs(x['x']) < .02 and abs(x['y']) < .01 for x in samples))
            evidence.append(_comparison('graph:x^2','graph',
                {'ok':bool(legacy_graph and legacy_graph.get('ok')),'legacyCanvas':left.locator('#calculator-graph-canvas').count()},
                {'samples':len(samples),'hasSvgPath':right.locator('[data-mcel-plot] svg path').count()>0},graph_valid,
                note='Different visual representations; verifies core-derived graph, not pixel equivalence.'))
            # A constant graph retains the complete X domain when reset; a
            # clipped parabola cannot witness that full-domain redraw.
            graph_form.locator('input[name="expression"]').fill('2+2')
            graph_form.locator('button[type="submit"]').click()
            right.wait_for_function('() => window.__mcelExpanded.app.readState("state:graph-points")?.length > 150')
            samples=right.evaluate('window.__mcelExpanded.app.readState("state:graph-points")')
            # Reset defaults expose a real functional mismatch if they differ.
            left.evaluate('window.MainComputerCalculatorRuntime.resetGraph()')
            left_defaults = {k:left.locator('#calculator-graph-'+k).input_value()
                             for k in ('x-min','x-max','y-min','y-max')}
            graph_form.locator('button[type="reset"]').click()
            candidate_defaults = {k:graph_form.locator('input[name="'+v+'"]').input_value()
                                  for k,v in (('x-min','xMin'),('x-max','xMax'),('y-min','yMin'),('y-max','yMax'))}
            evidence.append(_comparison('graph-reset-defaults','graph-reset',left_defaults,candidate_defaults,
                                        left_defaults==candidate_defaults))
            # The incumbent reset also re-renders, while resetting input
            # fields alone leaves the previous plotted sample coordinates.
            old_reset_graph=left.evaluate('window.MainComputerCalculatorRuntime.resetGraph()')
            displayed_sample_limits={'minX':round(min(x['x'] for x in samples),5),
                                     'maxX':round(max(x['x'] for x in samples),5)}
            right.wait_for_function('() => {const rows=window.__mcelExpanded.app.readState("state:graph-points");return rows?.length>2 && rows[0].x < -9.9;}',timeout=5000)
            after_reset=right.evaluate('window.__mcelExpanded.app.readState("state:graph-points")')
            candidate_sample_limits=({'minX':round(min(x['x'] for x in after_reset),5),
                                      'maxX':round(max(x['x'] for x in after_reset),5)}
                                     if after_reset else None)
            reset_redrew=bool(old_reset_graph and old_reset_graph.get('ok') and
                              candidate_sample_limits is not None and
                              candidate_sample_limits['minX'] <= -9.9 and
                              candidate_sample_limits['maxX'] >= 9.9)
            evidence.append(_comparison('graph-reset-redraw','graph-reset',
                {'redrew':bool(old_reset_graph and old_reset_graph.get('ok')),'rangeX':[-10,10]},
                {'sampleRangeX':candidate_sample_limits,'previousRangeX':displayed_sample_limits},
                reset_redrew,note='A form reset is not a redraw of the previous graph.'))
            _mock_http(left); _mock_http(right)
            legacy_sym = left.evaluate('''async () => await window.MainComputerCalculatorRuntime.evaluateMathics({expression:'2+2'})''')
            _select_mode(right,'symbolic')
            mf=right.locator('form[data-intent="intent:evaluate-symbolic"]')
            mf.locator('input[name="expression"]').fill('2+2');mf.locator('button[type="submit"]').click()
            right.locator('[data-state="state:symbolic-result"]').get_by_text('4').wait_for(timeout=5000)
            lr=left.locator('#calculator-mathics-output').inner_text()
            cr=right.evaluate('window.__mcelExpanded.app.readState("state:symbolic-result")')
            evidence.append(_comparison('symbolic:evaluate','symbolic-evaluate',lr,cr,
                                        legacy_sym.get('ok') is True and lr==cr,mocked=True))
            # The incumbent helper *applies* an expression and computes it;
            # semantic candidate currently shows a suggestion only.
            legacy_help = left.evaluate('''async () => await window.MainComputerCalculatorRuntime.askModelForExpression({prompt:'two and two'})''')
            _select_mode(right,'help')
            hf=right.locator('form[data-intent="intent:ask-arithmetic-model"]')
            hf.locator('input[name="prompt"]').fill('two and two');hf.locator('button[type="submit"]').click()
            right.wait_for_function('() => window.__mcelExpanded.app.readState("state:arithmetic-result") === "4"',timeout=5000)
            inc_applied=left.locator('#calculator-display').input_value()
            cand_applied=right.locator('form[data-intent="intent:evaluate"] input[name="expression"]').input_value()
            evidence.append(_comparison('arithmetic-helper-applies-result','arithmetic-model-handoff',
                {'expression':inc_applied,'computed':left.locator('#calculator-result').inner_text()},
                {'expression':cand_applied,'suggestion':right.evaluate('window.__mcelExpanded.app.readState("state:arithmetic-suggestion")')},
                inc_applied==cand_applied and bool(legacy_help and legacy_help.get('ok')),mocked=True))
            # Model graph/symbolic helpers are distinct from merely displaying
            # a suggestion. Their HTTP responses are controlled and identical.
            left.evaluate('window.MainComputerCalculatorRuntime.switchMode({mode:"graphing"})')
            _select_mode(right,'graphing')
            _select_mode(right,'help')
            legacy_graph_help=left.evaluate('''async () => await window.MainComputerCalculatorRuntime.askModelForGraphExpression({prompt:'flat line'})''')
            graph_help=right.locator('form[data-intent="intent:ask-graph-model"]')
            graph_help.locator('input[name="prompt"]').fill('flat line')
            graph_help.locator('button[type="submit"]').click()
            right.wait_for_function("""() => window.__mcelExpanded.app.readState('state:graph-points')?.length > 10 && document.querySelector('form[data-intent="intent:draw-graph"] input[name="expression"]')?.value === '2+2'""",timeout=5000)
            ig=left.locator('#calculator-graph-expression').input_value()
            cg=right.locator('form[data-intent="intent:draw-graph"] input[name="expression"]').input_value()
            evidence.append(_comparison('graph-helper-applies-graph','graph-model-handoff',
                {'graphExpression':ig,'drawSucceeded':bool(legacy_graph_help and legacy_graph_help.get('ok'))},
                {'graphExpression':cg,'suggestion':right.evaluate('window.__mcelExpanded.app.readState("state:graph-suggestion")')},
                ig==cg and bool(legacy_graph_help and legacy_graph_help.get('ok')),mocked=True))
            legacy_symbolic_help=left.evaluate('''async () => await window.MainComputerCalculatorRuntime.askModelForMathicsExpression({prompt:'integrate x squared'})''')
            symbolic_help=right.locator('form[data-intent="intent:ask-symbolic-model"]')
            symbolic_help.locator('input[name="prompt"]').fill('integrate x squared')
            symbolic_help.locator('button[type="submit"]').click()
            right.wait_for_function("""() => document.querySelector('form[data-intent="intent:evaluate-symbolic"] input[name="expression"]')?.value === 'Integrate[x^2,x]'""",timeout=5000)
            im=left.locator('#calculator-mathics-expression').input_value()
            cm=right.locator('form[data-intent="intent:evaluate-symbolic"] input[name="expression"]').input_value()
            evidence.append(_comparison('symbolic-helper-populates-input','symbolic-model-handoff',
                {'expression':im,'ok':bool(legacy_symbolic_help and legacy_symbolic_help.get('ok'))},
                {'expression':cm,'suggestion':right.evaluate('window.__mcelExpanded.app.readState("state:symbolic-suggestion")')},
                im==cm and bool(legacy_symbolic_help and legacy_symbolic_help.get('ok')),mocked=True))
            # Q&A: both invoke the same endpoint but are not assumed to send
            # equivalent full current context. Compare recorded payload keys.
            left.evaluate('''async () => await window.MainComputerCalculatorRuntime.askResultQuestion({question:'why?'})''')
            qf=right.locator('form[data-intent="intent:ask-result-question"]')
            qf.locator('input[name="question"]').fill('why?');qf.locator('button[type="submit"]').click()
            right.locator('[data-state="state:qa-result"]').get_by_text('The result is four.').wait_for(timeout=5000)
            inc_requests=left.evaluate('window.__calcParityRequests')
            cand_requests=right.evaluate('window.__calcParityRequests')
            inc_qa=next((req['payload'] for req in reversed(inc_requests) if req['path'].endswith('/qa')),None)
            cand_qa=next((req['payload'] for req in reversed(cand_requests) if req['path'].endswith('/qa')),None)
            def context(payload):
                if not payload: return {}
                raw=payload.get('context') or ''
                if isinstance(raw,dict): return raw
                try: return json.loads(raw)
                except (ValueError,TypeError): return {'unparsed':str(raw)[:200]}
            ic=context(inc_qa);cc=context(cand_qa)
            evidence.append(_comparison('qa-context-keys','result-question',
                ic,cc,bool(ic) and ic==cc,mocked=True,
                note='Endpoint and answer alone do not prove context parity.'))
            # Incumbent's clear removes both expression and previous output;
            # candidate generic reset may only clear the form field.
            left.locator('#calculator-mathics-clear').click()
            _select_mode(right,'symbolic')
            if mf.locator('[data-mcel-clear-intent]').count():
                mf.locator('[data-mcel-clear-intent]').click()
                right.wait_for_function('() => window.__mcelExpanded.app.readState("state:symbolic-result") === "Mathics ready."',timeout=5000)
            incumbent_clear={'expression':left.locator('#calculator-mathics-expression').input_value(),
                             'output':left.locator('#calculator-mathics-output').inner_text()}
            candidate_clear={'expression':mf.locator('input[name="expression"]').input_value(),
                             'output':right.evaluate('window.__mcelExpanded.app.readState("state:symbolic-result")'),
                             'clearControlPresent':mf.locator('[data-mcel-clear-intent]').count()>0}
            evidence.append(_comparison('symbolic-clear-output','symbolic-clear',
                incumbent_clear,candidate_clear,all(candidate_clear.get(k)==v for k,v in incumbent_clear.items())
                and candidate_clear['clearControlPresent']))
            # The incumbent's graph template and token editing are genuine
            # interactive controls. Compare source-core results and caret
            # positions through both browser UIs, then exercise semantic
            # backspace/clear against normal keyboard editing.
            left.evaluate('window.MainComputerCalculatorRuntime.switchMode({mode:"graphing"})')
            _select_mode(right,'graphing')
            l_expr=left.locator('#calculator-graph-expression')
            r_expr=right.locator('form[data-intent="intent:draw-graph"] input[name="expression"]')
            l_expr.fill('');r_expr.fill('')
            editor=right.locator('[data-mcel-expression-editor]')
            def editor_snapshot(page, selector):
                return page.evaluate('''selector => {const el=document.querySelector(selector);
                  return {expression:el.value,selectionStart:el.selectionStart,selectionEnd:el.selectionEnd};}''',selector)
            for token,legacy_selector in (('sin()','[data-calc-graph-template="sin()"]'),
                                          ('x','[data-calc-graph-token="x"]')):
                left.locator(legacy_selector).click()
                editor.locator('button[data-editor-token="'+token+'"]').click()
                expected=l_expr.input_value()
                right.wait_for_function("value => document.querySelector('form[data-intent=\"intent:draw-graph\"] input[name=\"expression\"]')?.value === value",
                                        arg=expected,timeout=5000)
                legacy_state=editor_snapshot(left,'#calculator-graph-expression')
                semantic_state=editor_snapshot(right,'form[data-intent="intent:draw-graph"] input[name="expression"]')
                evidence.append(_comparison('graph-editor:'+token,'graph-editor',legacy_state,semantic_state,
                                            legacy_state==semantic_state))
            l_expr.press('Backspace')
            editor.locator('button[data-editor-action="backspace"]').click()
            right.wait_for_function("() => document.querySelector('form[data-intent=\"intent:draw-graph\"] input[name=\"expression\"]')?.value === 'sin()'",timeout=5000)
            legacy_back=editor_snapshot(left,'#calculator-graph-expression')
            semantic_back=editor_snapshot(right,'form[data-intent="intent:draw-graph"] input[name="expression"]')
            evidence.append(_comparison('graph-editor:backspace','graph-editor',legacy_back,semantic_back,
                                        legacy_back==semantic_back))
            l_expr.fill('')
            editor.locator('button[data-editor-action="clear"]').click()
            right.wait_for_function("() => document.querySelector('form[data-intent=\"intent:draw-graph\"] input[name=\"expression\"]')?.value === ''",timeout=5000)
            legacy_clear=editor_snapshot(left,'#calculator-graph-expression')
            semantic_clear=editor_snapshot(right,'form[data-intent="intent:draw-graph"] input[name="expression"]')
            evidence.append(_comparison('graph-editor:clear','graph-editor',legacy_clear,semantic_clear,
                                        legacy_clear==semantic_clear))
            # The semantic candidate must mount the incumbent's real Chat
            # Console, not substitute a single Q&A response. Thread metadata,
            # live calculator context and multiple notebook cells are checked
            # here; live chat service behavior still requires hosted evidence.
            r_expr.fill('x+1')
            _select_mode(right,'help')
            chat=right.evaluate('''() => {
              const api=window.MainComputerChatConsole;
              const store=window.MainComputerChatThreads;
              const before=store?.getActive?.();
              const context=api?.getActiveEmbeddedContext?.();
              const cellBefore=document.querySelectorAll('[data-chat-console-embedded-notebook] textarea').length;
              api?.addCell?.('ai');
              const after=store?.getActive?.();
              return {
                nativeController:typeof api?.mountEmbedded==='function',
                threadId:before?.id||null,
                sameThread:!!before?.id && before.id===after?.id,
                origin:after?.metadata?.origin_app||'',
                linkedTarget:after?.metadata?.linked_targets?.[0]||null,
                context:context||null,
                cellsBefore:cellBefore,
                cellsAfter:document.querySelectorAll('[data-chat-console-embedded-notebook] textarea').length,
                storedCells:after?.cells?.length||0,
                runControl:!!document.querySelector('[data-chat-console-embedded-notebook] button') &&
                  [...document.querySelectorAll('[data-chat-console-embedded-notebook] button')].some(b=>b.textContent?.trim()==='Run')
              };
            }''')
            chat_ok=(chat['nativeController'] and chat['sameThread'] and
                     chat['origin']=='calculator' and chat['linkedTarget']=={
                       'app':'calculator','kind':'calculator-session','id':'calculator',
                       'path':'applications/calculator'} and
                     chat['context'] is not None and chat['context'].get('graph',{}).get('expression')=='x+1' and
                     chat['context'].get('target_kind')=='calculator-session' and
                     'arithmetic' in chat['context'].get('allowed_tools',[]) and
                     chat['cellsBefore']>=1 and chat['cellsAfter']==chat['cellsBefore']+1 and
                     chat['storedCells']==chat['cellsAfter'] and chat['runControl'])
            evidence.append(_comparison('contextual-chat:controller-thread-context','contextual-chat',
                {'nativeController':'required','threadMetadata':'calculator-session',
                 'liveContext':True,'multipleCells':True},chat,chat_ok,
                note='Tests incumbent Chat Console controller reuse and session metadata; not live chat-provider parity.'))
        except Exception as exc:
            raise BrowserProbeError(f'{type(exc).__name__}: {exc}', evidence, page_errors) from exc
        finally:
            browser.close()
    return {'engine':'chromium', 'backend':backend, 'cases':evidence, 'pageErrors':page_errors,
            'status': 'PASS' if evidence and all(x['status']=='PASS' for x in evidence)
                      and not any(page_errors.values()) else 'INCOMPLETE'}


def assess(repo: Path = ROOT, *, browser: bool = True) -> dict:
    report: dict[str, Any] = {
        'schema':'mcel.calculator-functional-parity-frob.v1',
        'scope':'isolated-read-only-no-production-promotion',
        'canonicalRoute':'/applications/calculator',
        'candidateRoute':'/applications/mcel-semantic-calculator-expanded',
        'promotionAuthorized':False,
        'evidenceLimits':['Browser HTTP calls are deterministic mocks, not live services',
                          'Incumbent browser uses canonical projected markup and standalone handlers, not a hosted Windows mount',
                          'Candidate browser uses expanded semantic runtime, not canonical route',
                          'Live provider parity and human visual approval remain unverified'],
    }
    try:
        baseline=inspect_baseline(repo)
        report['baseline']={k:baseline[k] for k in
                ('status','baseline_provenance','missing_binding_ids','duplicate_dom_ids',
                 'html_sha256','canonical_projection_sha256','projection_error')}
        if baseline['status'] not in ('PROJECTED_SOURCE_BASELINE_READY_FOR_PARITY_REVIEW',
                                      'SOURCE_BASELINE_READY_FOR_PARITY_REVIEW'):
            raise RuntimeError('Canonical incumbent baseline is incomplete')
        inventory=source_inventory(repo)
        report.update(inventory)
        report['browser']={'status':'UNVERIFIED','reason':'Browser disabled by --no-browser','cases':[]}
        if browser:
            try:
                report['browser']=browser_comparison(repo)
            except BrowserProbeError as exc:
                report['browser']={'status':'INCOMPLETE','reason':str(exc),
                                   'cases':exc.cases,'pageErrors':exc.page_errors}
            except Exception as exc:
                report['browser']={'status':'UNVERIFIED','reason':f'{type(exc).__name__}: {exc}',
                                   'cases':[]}
        scenario_by_feature: dict[str,list[dict]]={}
        for case in report['browser']['cases']:
            scenario_by_feature.setdefault(case['feature'],[]).append(case)
        for fid,row in inventory['features'].items():
            cases=scenario_by_feature.get(fid,[])
            row['browserScenario']=[c['id'] for c in cases] or None
            if row['status']=='SOURCE_COVERED':
                if any(c['status']=='MISMATCH' for c in cases):
                    row['status']='BROKEN'
                    row['nextAction']='Repair observed incumbent/candidate behavior mismatch; preserve existing layout oracle'
                elif cases and all(c['status']=='PASS' for c in cases):
                    row['status']='PASS_ISOLATED'
                    row['nextAction']='Verify this capability against real authorized services and canonical host'
                else:
                    row['status']='UNVERIFIED'
                    row['nextAction']='Add an isolated browser witness and required live-host parity evidence'
            if row['status']=='MISSING' and any(c['status']=='MISMATCH' for c in cases):
                row['browserMismatchObserved']=True
        statuses=[v['status'] for v in inventory['features'].values()]
        counts={s:statuses.count(s) for s in sorted(set(statuses))}
        report['summary']={'featureCount':len(statuses),'byStatus':counts,
                           'browserCases':len(report['browser']['cases']),
                           'browserMismatches':sum(c['status']=='MISMATCH' for c in report['browser']['cases']),
                           'missingFeatures':[k for k,v in inventory['features'].items() if v['status']=='MISSING'],
                           'brokenFeatures':[k for k,v in inventory['features'].items() if v['status']=='BROKEN'],
                           'unverifiedFeatures':[k for k,v in inventory['features'].items() if v['status']=='UNVERIFIED']}
        # The isolated gate can become green after real repairs; promotion
        # remains a separate authorization and hosted operational gate.
        report['status']=('PASS_ISOLATED' if statuses and
                          all(x=='PASS_ISOLATED' for x in statuses) and
                          report['browser']['status']=='PASS' else 'INCOMPLETE')
        report['liveProviderParity']='UNVERIFIED'
        report['humanDesignApproval']='UNVERIFIED'
    except Exception as exc:
        report['status']='INCOMPLETE'
        report['error']=f'{type(exc).__name__}: {exc}'
    return report


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=ROOT)
    parser.add_argument('--report',type=Path,default=REPORT)
    parser.add_argument('--no-browser',action='store_true',help='Source-only inspection (browser evidence UNVERIFIED)')
    args=parser.parse_args(argv)
    repo=args.repo.resolve()
    result=assess(repo,browser=not args.no_browser)
    dest=args.report if args.report.is_absolute() else repo/args.report
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(result,indent=2,sort_keys=True,ensure_ascii=False)+'\n',encoding='utf-8')
    print('MCEL_CALCULATOR_FUNCTIONAL_PARITY '+result['status'])
    for name,row in result.get('features',{}).items():
        print(f'  {name}: {row["status"]}')
    summary=result.get('summary',{})
    print(f'  browser: {summary.get("browserCases",0)} cases; mismatches: {summary.get("browserMismatches",0)}')
    if result.get('error'): print('  error: '+result['error'])
    if result.get('browser',{}).get('reason'): print('  browser: '+result['browser']['reason'])
    print('  report: '+str(dest))
    return 0 if result['status']=='PASS_ISOLATED' else 1


if __name__ == '__main__':
    sys.exit(main())
