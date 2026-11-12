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
    assert len(ir['intents'])==14
    assert len(ir['surfaces'][0]['nodes'])>=16
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
        assert p.locator('form[data-intent="intent:ask-arithmetic-model"]').is_visible()
    finally:p.close()

def test_decimal_and_unit_arithmetic_real_core(browser):
    p=opened(browser)
    try:
        f=p.locator('form[data-intent="intent:evaluate"]')
        f.locator('input[name="expression"]').fill('2.5 + 3.25');f.locator('button[type="submit"]').click()
        p.locator('[data-state="state:arithmetic-result"]').get_by_text('5.75').wait_for()
        f.locator('input[name="expression"]').fill('3 m + 40 cm');f.locator('button[type="submit"]').click()
        p.locator('[data-state="state:arithmetic-result"]').get_by_text('3.4 m').wait_for()
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

def test_optional_services_use_real_endpoints_and_result_question_context(browser):
    p=opened(browser)
    try:
        # Stub only the HTTP boundary; Calculator capability wrapper itself is real.
        p.evaluate('''() => {
          window.__requests=[];
          window.fetch=async (path,opts)=>{
            window.__requests.push({path,payload:JSON.parse(opts.body)});
            const data=path==='/api/applications/calculator/mathics/evaluate'?
              {ok:true,result_text:'4'} : path==='/api/applications/calculator/qa'?
              {ok:true,answer:'The result follows the arithmetic expression.'}:
              path==='/api/chat'?{ok:true,content:'2 + 2'}:
              {ok:true,expression:'Integrate[x^2,x]'};
            return {ok:true,status:200,json:async()=>data};
          };
        }''')
        select(p,'symbolic')
        f=p.locator('form[data-intent="intent:evaluate-symbolic"]')
        f.locator('input[name="expression"]').fill('2+2');f.locator('button[type="submit"]').click()
        p.locator('[data-state="state:symbolic-result"]').get_by_text('4').wait_for()
        select(p,'help')
        f=p.locator('form[data-intent="intent:ask-arithmetic-model"]')
        f.locator('input[name="prompt"]').fill('What is 2+2?');f.locator('button[type="submit"]').click()
        p.locator('[data-state="state:arithmetic-suggestion"]').get_by_text('2 + 2').wait_for()
        p.wait_for_function("() => window.__mcelExpanded.app.readState('state:arithmetic-result') === '4'",timeout=5000)
        f=p.locator('form[data-intent="intent:ask-result-question"]')
        f.locator('input[name="question"]').fill('Explain the answer');f.locator('button[type="submit"]').click()
        p.locator('[data-state="state:qa-result"]').get_by_text('The result follows the arithmetic expression.').wait_for()
        calls=p.evaluate('window.__requests')
        assert [x['path'] for x in calls]==['/api/applications/calculator/mathics/evaluate','/api/chat','/api/applications/calculator/qa']
        assert json.loads(calls[-1]['payload']['context'])['mathics_output'] == '4'
        assert {'basic_expression','basic_result','graph_expression','graph_range',
                'graph_status','mathics_expression','mathics_output'} <= set(json.loads(calls[-1]['payload']['context']))
    finally:p.close()

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


def test_competing_helper_results_do_not_overwrite_newer_one(browser):
    p=opened(browser)
    try:
        result=p.evaluate('''async()=>{
          const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
          const root=document.createElement('div');document.body.append(root);
          const resolvers=[];
          const port={authorize:async()=>true,invoke:req=>new Promise(resolve=>resolvers.push({id:req.intentId,resolve})),mountEmbeddedChat:()=>({dispose(){}})};
          const app=McelSemanticOperationalRuntime.mount(root,ir,{capabilities:port});
          const first=app.execute('intent:ask-arithmetic-model',{prompt:'first'});
          const second=app.execute('intent:ask-graph-model',{prompt:'second'});
          await new Promise(resolve=>setTimeout(resolve,0));
          resolvers.find(x=>x.id==='intent:ask-graph-model').resolve('second-result');
          await second;
          resolvers.find(x=>x.id==='intent:ask-arithmetic-model').resolve('first-result');
          await first;
          const final=[app.readState('state:arithmetic-suggestion'),
                       app.readState('state:graph-suggestion')];app.unmount();root.remove();return final;
        }''')
        assert result==['first-result','second-result']
    finally:p.close()
