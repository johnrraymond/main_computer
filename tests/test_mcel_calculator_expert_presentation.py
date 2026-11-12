"""Semantic computed-workbench presentation: real Calculator baseline, generic rules."""
from __future__ import annotations
import copy
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright
from main_computer.mcel_calculator_expanded_host import compile_expanded,render_expanded_page
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as p:
        b,_=launch_semantic_browser(p)
        try:yield b
        finally:b.close()

def open_app(browser,width=1272):
    page=browser.new_page(viewport={'width':width,'height':850})
    page.evaluate("""() => {const data=new Map();Object.defineProperty(window,'localStorage',{
      configurable:true,value:{getItem:k=>data.get(String(k))??null,
      setItem:(k,v)=>data.set(String(k),String(v)),removeItem:k=>data.delete(String(k)),
      clear:()=>data.clear(),key:i=>[...data.keys()][i]??null,
      get length(){return data.size;}}});}""")
    page.set_content(render_expanded_page())
    assert page.locator('#mcel-expanded-diagnostic').inner_text()=='Semantic Calculator ready'
    return page

def test_expert_is_driven_by_computation_semantics_and_not_app_id(browser):
    p=open_app(browser)
    try:
        assert p.locator('[data-mcel-expression-pad] button').count()==21
        assert p.locator('[data-mcel-mode-tabs] button').count()==4
        # Remove the compute task only; the optional shared expert must not invent a keypad.
        count=p.evaluate('''() => {
          const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
          ir.application.appId='unrelated-domain';
          const target=ir.surfaces[0].interfaceIntent.regions.find(r=>r.id.endsWith('.arithmetic'));
          target.tasks=['inspect'];
          const node=document.createElement('div');document.body.append(node);
          const app=McelSemanticOperationalRuntime.mount(node,ir,{presentation:'expert',capabilities:{mountEmbeddedChat:()=>({dispose(){}})}});
          const count=node.querySelectorAll('[data-mcel-expression-pad]').length;
          app.unmount();node.remove();return count;
        }''')
        assert count==0
    finally:p.close()

def test_expression_pad_operates_actual_calculator_core_and_can_edit(browser):
    p=open_app(browser)
    try:
        pad=p.locator('[data-mcel-expression-pad]')
        for v in ('7','+','8','submit'):
            pad.locator(f'button[data-pad-action="{v}"]').click()
        p.locator('[data-state="state:arithmetic-result"]').get_by_text('15').wait_for()
        assert p.locator('form[data-intent="intent:evaluate"] input[name="expression"]').input_value()=='7+8'
        pad.locator('button[data-pad-action="backspace"]').click()
        assert p.locator('form[data-intent="intent:evaluate"] input[name="expression"]').input_value()=='7+'
        pad.locator('button[data-pad-action="clear"]').click()
        assert p.locator('form[data-intent="intent:evaluate"] input[name="expression"]').input_value()==''
        p.locator('form[data-intent="intent:evaluate"] input[name="expression"]').fill('3 m + 40 cm')
        p.locator('form[data-intent="intent:evaluate"] input[name="expression"]').press('Enter')
        p.locator('[data-state="state:arithmetic-result"]').get_by_text('3.4 m').wait_for()
    finally:p.close()

def test_mode_navigation_and_no_debug_receipt_flood(browser):
    p=open_app(browser)
    try:
        for mode in ('graphing','symbolic','help','basic','graphing','basic'):
            p.locator(f'[data-mcel-mode-tabs] button[data-value="{mode}"]').click()
            assert p.evaluate('window.__mcelExpanded.app.readState("state:mode")')==mode
        assert p.locator('[data-mcel-activity] li').count()==0
        assert p.locator('[data-mcel-activity] summary').inner_text()=='Activity · No operations yet'
        p.locator('form[data-intent="intent:evaluate"] input[name="expression"]').fill('2+3')
        p.locator('form[data-intent="intent:evaluate"] button[type="submit"]').click()
        p.locator('[data-state="state:arithmetic-result"]').get_by_text('5').wait_for()
        assert p.locator('[data-mcel-activity] summary').inner_text()=='Activity · 1 recent operation'
        assert p.locator('[data-mcel-activity] li').count()==1
        # Receipts remain accessible to application code; visual history is intentionally concise.
        assert len(p.evaluate('window.__mcelExpanded.app.receipts()'))==7
    finally:p.close()

@pytest.mark.parametrize('width',[320,390,560,1100,1272])
def test_responsive_semantic_workbench_no_horizontal_overflow(browser,width):
    p=open_app(browser,width)
    try:
        assert p.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert p.locator('form[data-mcel-composition="expression-workbench"]').is_visible()
        assert p.locator('[data-mcel-purpose="results"]:visible').count()==1
        # Mode choices are actual left-navigation destinations at every width,
        # not a wrapping toolbar or an unrelated dropdown above the workspace.
        nav=p.locator('[data-mcel-natural-scroller="navigation"]')
        choices=nav.locator('[data-mcel-mode-tabs] button')
        assert choices.all_inner_texts()==['Basic','Graphing','Symbolic','Help']
        assert choices.first.is_visible()
        bounds=choices.evaluate_all('''els=>els.map(e=>{const r=e.getBoundingClientRect();
            return [r.x,r.top,r.bottom]})''')
        assert all(bounds[i][1]>=bounds[i-1][2]-1 and abs(bounds[i][0]-bounds[0][0])<2
                   for i in range(1,len(bounds)))
        nav.locator('[data-mcel-mode-tabs] button[data-value="graphing"]').click()
        f=p.locator('form[data-intent="intent:draw-graph"]')
        f.locator('input[name="expression"]').fill('sin(x)')
        f.locator('button[type="submit"]').click()
        p.locator('[data-mcel-plot] svg path').wait_for()
        assert p.evaluate('document.documentElement.scrollWidth <= innerWidth')
    finally:p.close()

def test_generic_expert_layout_policy_has_no_app_specific_branches():
    css=(ROOT/'main_computer/web/applications/styles/mcel-semantic-expert.css').read_text()
    js=(ROOT/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js').read_text()
    # Source comments may describe a named fixture; selectors must remain
    # generic and must never branch on a particular application identity.
    import re
    executable_css=re.sub(r'/\*.*?\*/', '', css, flags=re.S).lower()
    for text in ('calculator','file-manager','task-manager','code-editor','semantic-calculator-expanded'):
        assert text not in executable_css
    assert 'semantic-calculator-expanded' not in js
    assert 'region.tasks?.includes("compute")' in js
    assert 'region.priority === "primary"' in js

def test_compiled_semantics_and_authority_are_unchanged():
    ir=compile_expanded()
    assert not ir['layouts']
    assert len(ir['intents'])==14
    assert {e['risk'] for e in ir['effects']}=={'external-read'}
    assert all('htmlSource' in r for r in ir['surfaces'][0]['interfaceIntent']['regions'])
