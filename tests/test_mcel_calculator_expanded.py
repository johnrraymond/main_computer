"""Opt-in expanded Calculator: real deterministic core and optional governed APIs."""
from __future__ import annotations
import copy
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from main_computer.mcel_calculator_expanded_host import (compile_expanded, render_expanded_page,
                                                           CandidateHostError, ROUTE)
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b,_=launch_semantic_browser(p)
        try:yield b
        finally:b.close()

def opened(browser,width=1100):
    page=browser.new_page(viewport={"width":width,"height":900})
    page.on('pageerror',lambda e: print('Page error:',e))
    # The native embedded Chat Console persists its notebook; opaque-origin
    # Chromium fixtures require a disposable in-memory store instead.
    page.evaluate("""() => {const data=new Map();Object.defineProperty(window,'localStorage',{
      configurable:true,value:{getItem:k=>data.get(String(k))??null,
      setItem:(k,v)=>data.set(String(k),String(v)),removeItem:k=>data.delete(String(k)),
      clear:()=>data.clear(),key:i=>[...data.keys()][i]??null,
      get length(){return data.size;}}});}""")
    page.set_content(render_expanded_page())
    assert page.locator('#mcel-expanded-diagnostic').inner_text()=='Semantic Calculator ready'
    return page

def select(page,mode):
    tabs = page.locator(f'[data-mcel-mode-tabs] button[data-value="{mode}"]')
    if tabs.is_visible():
        tabs.click()
    else:
        page.locator('select[data-intent="intent:switch-mode"]').select_option(mode)
    page.wait_for_function('(m)=>window.__mcelExpanded.app.readState("state:mode")===m',arg=mode)

def test_expanded_compiles_from_real_semantic_html_and_js():
    ir=compile_expanded()
    assert not ir.get('layouts')
    assert len(ir['intents'])==7
    assert len(ir['surfaces'][0]['nodes'])==14
    assert len(ir['surfaces'][0]['interfaceIntent']['regions'])==13
    assert {a['risk'] for a in ir['effects']}=={'external-read'}
    assert any(n['nodeKind']=='plot' for n in ir['surfaces'][0]['nodes'])
    assert len(ir['provenance']['frontend']['sourceFiles'])==2

def test_semantic_hierarchy_and_purpose_headings_visible(browser):
    p=opened(browser)
    try:
        assert p.get_by_text('Arithmetic and Units').count()==0
        assert p.locator('[data-mcel-purpose="input"][data-mcel-density="compact"]').count()==1
        assert p.locator('[data-mcel-region]').count()==13
        assert p.locator('form[data-intent="intent:draw-graph"]').is_hidden()
        select(p,'graphing')
        assert p.locator('h2').get_by_text('Graph a Function').is_visible()
        assert p.locator('form[data-intent="intent:evaluate"]').is_hidden()
        select(p,'symbolic')
        assert p.locator('form[data-intent="intent:evaluate-symbolic"]').is_visible()
        select(p,'help')
        assert p.locator('[data-mcel-embedded-chat]').is_visible()
    finally:p.close()

def test_help_oracle_has_exactly_one_ai_terminus_and_owned_context():
    ir = compile_expanded()
    regions = {r["id"].split(".")[-1]: r for r in ir["surfaces"][0]["interfaceIntent"]["regions"]}
    help_region = regions["help-workspace"]
    assert help_region["purpose"] == "assistance"
    assert help_region["tasks"] == ["review"]
    context = regions["calculator-context"]
    chat = regions["helper-chat"]
    assert context["parentId"] == chat["parentId"] == help_region["id"]
    assert context["purpose"] == "inspection"
    assert [m["ref"].split(".")[-1] for m in context["members"]] == [
        "basic-expression-context", "basic-result-context", "graph-expression-context",
        "symbolic-expression-context", "symbolic-result-context"]
    assert [m["ref"].split(".")[-1] for m in chat["members"]] == ["calculator-chat"]
    assert chat["nativeTag"] == "section"
    ai_surfaces = [n for n in ir["surfaces"][0]["nodes"] if n["nodeKind"] == "embedded-chat"]
    assert len(ai_surfaces) == 1
    assert ai_surfaces[0]["intent"] == {"ref": "intent:calculator-chat"}
    assert {i["id"] for i in ir["intents"]} == {
        "intent:evaluate", "intent:draw-graph", "intent:evaluate-symbolic",
        "intent:edit-graph-expression", "intent:clear-symbolic", "intent:calculator-chat",
        "intent:switch-mode"}
    assert {effect["risk"] for effect in ir["effects"]} == {"external-read"}
    for legacy in ("ask-arithmetic-model", "ask-graph-model", "ask-symbolic-model", "ask-result-question"):
        assert not any(i["id"] == "intent:" + legacy for i in ir["intents"])


@pytest.mark.parametrize("width", [390, 1272])
def test_one_help_composer_and_notebook_toggle(browser, width):
    p = opened(browser, width)
    try:
        select(p, "help")
        assert p.locator('[data-mcel-embedded-chat]').count() == 1
        assert p.locator('form[data-intent^="intent:ask-"]').count() == 0
        assert p.locator('[data-mcel-region$=".calculator-context"] [data-mcel-value-label]').count() == 5
        assert p.locator('[data-chat-conversation-role="composer"] textarea').count() == 1
        assert p.locator('[data-chat-conversation-role="composer"] .chat-cell-controls button').first.is_visible()
        assert p.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert p.locator('[data-chat-console-embed]').get_attribute('data-mcel-chat-layout-status') == 'pass'
        dimensions = p.evaluate('''() => {
          const rect = selector => document.querySelector(selector).getBoundingClientRect();
          return {
            scroller:rect('[data-mcel-natural-scroller="workspace"]').height,
            help:rect('[data-mcel-region$=".help-workspace"]').height,
            chat:rect('[data-mcel-embedded-chat]').width,
            editor:rect('[data-chat-conversation-role="composer"] textarea').width
          };
        }''')
        assert dimensions['help'] >= dimensions['scroller'] * 0.9
        assert dimensions['editor'] >= dimensions['chat'] * 0.65
        toggle = p.locator('[data-chat-conversation-switch]')
        assert toggle.inner_text() == "Notebook tools"
        toggle.click()
        assert toggle.inner_text() == "Conversation view"
        assert p.locator('[data-chat-console-embedded-notebook] .chat-cell-tabs').first.is_visible()
        toggle.click()
        assert p.locator('[data-chat-conversation-role="composer"] textarea').count() == 1
    finally:
        p.close()


def test_decimal_and_unit_arithmetic_real_core(browser):
    p=opened(browser)
    try:
        f=p.locator('form[data-intent="intent:evaluate"]')
        f.locator('input[name="expression"]').fill('2.5 + 3.25');f.locator('button[type="submit"]').click()
        p.locator('[data-mcel-region$=".answer"] [data-state="state:arithmetic-result"]').get_by_text('5.75').wait_for()
        f.locator('input[name="expression"]').fill('3 m + 40 cm');f.locator('button[type="submit"]').click()
        p.locator('[data-mcel-region$=".answer"] [data-state="state:arithmetic-result"]').get_by_text('3.4 m').wait_for()
        f.locator('input[name="expression"]').fill('3 m + 2 s');f.locator('button[type="submit"]').click()
        p.locator('[data-disposition="failed"]').wait_for()
        assert p.evaluate('window.__mcelExpanded.app.readState("state:arithmetic-result")')=='3.4 m'
    finally:p.close()

def test_plot_is_generated_numeric_series_not_html_and_is_responsive(browser):
    for width in (390,1272):
        p=opened(browser,width)
        try:
            select(p,'graphing')
            f=p.locator('form[data-intent="intent:draw-graph"]')
            for k,v in [('expression','sin(x)'),('xMin','-6.28'),('xMax','6.28'),('yMin','-2'),('yMax','2')]:
                f.locator(f'input[name="{k}"]').fill(v)
            f.locator('button[type="submit"]').click()
            p.locator('[data-mcel-plot] svg path').wait_for()
            samples=p.evaluate('window.__mcelExpanded.app.readState("state:graph-points")')
            assert len(samples)>100 and all(set(x)=={'id','x','y'} for x in samples)
            assert p.evaluate('document.documentElement.scrollWidth <= innerWidth')
        finally:p.close()

def test_help_chat_uses_single_shared_endpoint_with_live_symbolic_context(browser):
    p=opened(browser)
    try:
        # Stub the HTTP edge. The notebook and MCEL capability ports are real.
        p.evaluate('''() => {
          window.__requests=[];
          window.fetch=async (path,opts)=>{
            const payload=opts?.body?JSON.parse(opts.body):{};
            window.__requests.push({path:String(path),payload});
            if(String(path).startsWith('/api/applications/chat-console/ai/capacity?'))
              return {ok:true,status:200,json:async()=>({ok:true,busy:false,available_now:true,active_run_count:0,max_local_concurrency:1})};
            if(path==='/api/activity/snapshot' || path==='/api/activity/ollama-ps')
              return {ok:true,status:200,json:async()=>({ok:true,events:[],models:[]})};
            if(path==='/api/applications/calculator/mathics/prepare')
              return {ok:true,status:200,json:async()=>({ok:true,status:'ready'})};
            if(path==='/api/applications/calculator/mathics/evaluate')
              return {ok:true,status:200,json:async()=>({ok:true,result_text:'4'})};
            if(path==='/api/applications/chat-console/cell/evaluate')
              return {ok:true,status:200,json:async()=>({ok:true,status:'completed',
                output_cell:{id:'out-mock',type:'output',source_cell_id:payload.cell.id,status:'ok',
                  parts:[{id:'part-mock',kind:'text',title:'Explanation',content:'Because two plus two equals four.',snippets:[]}]}})};
            throw Error('unexpected endpoint:'+path);
          };
        }''')
        select(p,'symbolic')
        f=p.locator('form[data-intent="intent:evaluate-symbolic"]')
        f.locator('input[name="expression"]').fill('2+2');f.locator('button[type="submit"]').click()
        p.locator('[data-mcel-region$=".mathics-output"] [data-state="state:symbolic-result"]').get_by_text('4').wait_for()
        select(p,'help')
        context=p.evaluate('window.MainComputerChatConsole.getActiveEmbeddedContext()')
        assert context['active_mode']=='symbolic'
        assert context['mathics']['expression']=='2+2'
        assert context['mathics']['result']=='4'
        assert p.locator('[data-mcel-region$=".calculator-context"] output').all_inner_texts().count('4') >= 1
        composer=p.locator('[data-chat-conversation-role="composer"]')
        composer.locator('textarea').fill('Why is the answer 4?')
        composer.locator('.chat-cell-controls button').first.click()
        p.locator('[data-chat-conversation-role="assistant"]').get_by_text('Because two plus two equals four.').wait_for()
        assert p.locator('[data-chat-conversation-role="composer"] textarea').count()==1
        calls=p.evaluate('window.__requests')
        assert [x['path'] for x in calls if '/api/applications/calculator/mathics/' in x['path'] or '/cell/evaluate' in x['path']] == [
            '/api/applications/calculator/mathics/prepare',
            '/api/applications/calculator/mathics/evaluate',
            '/api/applications/chat-console/cell/evaluate']
        assert next(x for x in calls if x['path'].endswith('/cell/evaluate'))['payload']['embedded_context']['mathics']['result']=='4'
        assert next(x for x in calls if x['path'].endswith('/cell/evaluate'))['payload']['embedded_context']['active_mode']=='symbolic'
    finally:p.close()


def test_entering_symbolic_prepares_once_and_evaluation_waits_for_ready(browser):
    p=opened(browser)
    try:
        p.evaluate('''() => {
          window.__mathicsCalls=[];
          window.__finishMathicsPreparation=null;
          window.fetch=(path,opts)=>{
            window.__mathicsCalls.push(String(path));
            if(path==="/api/applications/calculator/mathics/prepare")
              return new Promise(resolve=>{
                window.__finishMathicsPreparation=()=>resolve({ok:true,status:200,
                  json:async()=>({ok:true,status:"ready"})});
              });
            if(path==="/api/applications/calculator/mathics/evaluate")
              return Promise.resolve({ok:true,status:200,json:async()=>({ok:true,result_text:"4"})});
            throw Error("unexpected request: "+path);
          };
        }''')
        select(p, 'graphing')
        assert p.evaluate('window.__mathicsCalls') == []
        select(p, 'symbolic')
        p.wait_for_function('() => !!window.__finishMathicsPreparation')
        assert p.evaluate('window.__mathicsCalls') == ['/api/applications/calculator/mathics/prepare']
        assert p.locator('#mcel-expanded-diagnostic').inner_text() == 'Loading Mathics…'
        f=p.locator('form[data-intent="intent:evaluate-symbolic"]')
        f.locator('input[name="expression"]').fill('2+2')
        f.locator('button[type="submit"]').click()
        assert p.evaluate('window.__mathicsCalls') == ['/api/applications/calculator/mathics/prepare']
        p.evaluate('window.__finishMathicsPreparation()')
        p.locator('[data-mcel-region$=".mathics-output"] [data-state="state:symbolic-result"]').get_by_text('4').wait_for()
        assert p.evaluate('window.__mathicsCalls') == [
            '/api/applications/calculator/mathics/prepare',
            '/api/applications/calculator/mathics/evaluate',
        ]
        select(p, 'basic')
        select(p, 'symbolic')
        assert p.evaluate('window.__mathicsCalls').count('/api/applications/calculator/mathics/prepare') == 1
    finally:
        p.close()


def test_unavailable_optional_endpoint_fails_not_fake_result(browser):
    p=opened(browser)
    try:
        p.evaluate('''() => window.fetch=async()=>({ok:false,status:503,json:async()=>({ok:false,error:'symbolic-unavailable'})})''')
        select(p,'symbolic')
        f=p.locator('form[data-intent="intent:evaluate-symbolic"]')
        f.locator('input[name="expression"]').fill('Integrate[x,x]');f.locator('button[type="submit"]').click()
        p.locator('li[data-intent="intent:evaluate-symbolic"][data-disposition="failed"]').wait_for()
        assert p.evaluate('window.__mcelExpanded.app.readState("state:symbolic-result")')==''
    finally:p.close()

def test_forged_capabilities_and_html_metadata_denied(browser):
    p=opened(browser)
    try:
        results=p.evaluate('''async () => {
          const port=McelCalculatorExpandedPort.create({core:MainComputerCalculatorCore,services:MainComputerCalculatorCapabilities});
          const base={appId:'semantic-calculator-expanded',intentId:'intent:evaluate',effectId:'effect:evaluate.request',
            capabilityId:'capability:calculator-core',operation:'evaluate',runtimeMethod:'evaluateCalculatorExpression',
            risk:'external-read',arguments:{expression:'2+2'}};
          return [await port.authorize(base),await port.authorize({...base,risk:'source-write'}),
            await port.authorize({...base,capabilityId:'capability:filesystem'}),
            await port.authorize({...base,arguments:{expression:'2+2',shell:'rm -rf /'}})];
        }''')
        assert results==[True,False,False,False]
    finally:p.close()

def test_legacy_candidate_unmodified():
    from main_computer.mcel_phase2c_candidate_host import compile_candidate
    old=compile_candidate()
    assert {i['id'] for i in old['intents']}=={'intent:compute','intent:switch-mode'}

def test_host_rejects_source_drift(monkeypatch):
    import main_computer.mcel_calculator_expanded_host as host
    original=host.compile_dsl_application
    def modified(path):
        obj=original(path)
        graph=copy.deepcopy(obj.normalized_ir)
        graph['effects'][0]['risk']='source-write'
        class Result: valid=True; normalized_ir=graph
        return Result()
    monkeypatch.setattr(host,'compile_dsl_application',modified)
    with pytest.raises(CandidateHostError):host.render_expanded_page()

def test_route_is_opt_in_local_only():
    code=(ROOT/'main_computer/viewport_route_dispatch.py').read_text()
    i=code.index('route_path == "/applications/mcel-semantic-calculator-expanded"')
    assert '_hard_halt_client_is_local(self)' in code[i:i+450]
    assert 'render_expanded_page' in code[i:i+450]

def test_graph_defaults_and_generic_reset(browser):
    p=opened(browser)
    try:
        select(p,'graphing')
        form=p.locator('form[data-intent="intent:draw-graph"]')
        assert form.locator('input[name="xMin"]').input_value()=='-10'
        assert form.locator('input[name="xMax"]').input_value()=='10'
        form.locator('input[name="xMin"]').fill('-2')
        form.locator('button[type="reset"]').click()
        assert form.locator('input[name="xMin"]').input_value()=='-10'
        form.locator('input[name="expression"]').fill('x^2')
        form.locator('button[type="submit"]').click()
        p.locator('[data-mcel-plot] svg path').wait_for()
    finally:p.close()


def test_html_visibility_contract_rejects_unowned_state():
    from main_computer.mcel_application_ir import validate_application_ir
    ir=copy.deepcopy(compile_expanded())
    ir['surfaces'][0]['interfaceIntent']['regions'][1]['visibleWhen']={'state':{'ref':'state:invented'},'equals':'graphing'}
    validated=validate_application_ir(ir)
    assert not validated.valid


def test_old_model_specific_intents_are_denied(browser):
    p=opened(browser)
    try:
        denied=p.evaluate('''async () => {
          const port=McelCalculatorExpandedPort.create({core:MainComputerCalculatorCore,
            services:MainComputerCalculatorCapabilities});
          const obsolete=['ask-arithmetic-model','ask-graph-model','ask-symbolic-model','ask-result-question'];
          return Promise.all(obsolete.map(name=>port.authorize({
            appId:'semantic-calculator-expanded',intentId:'intent:'+name,
            effectId:'effect:'+name+'.request',capabilityId:'capability:calculator-service',
            operation:'arithmetic-ask',runtimeMethod:'askArithmeticModel',risk:'external-read',
            arguments:{prompt:'test'}})));
        }''')
        assert denied==[False]*4
    finally:p.close()
