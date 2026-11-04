"""Calculator parity contracts for the shared, semantically generated UI.

These are browser checks of the real deterministic core and the generic MCEL
runtime, not assertions about the old host-bound Calculator DOM.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance, _ = launch_semantic_browser(playwright)
        try:
            yield instance
        finally:
            instance.close()


def opened(browser, width=1100):
    page = browser.new_page(viewport={"width": width, "height": 850})
    page.set_content(render_expanded_page())
    assert page.locator("#mcel-expanded-diagnostic").inner_text() == "Semantic Calculator ready"
    return page


def test_edit_keeps_last_valid_result_but_explicit_clear_revokes_it(browser):
    page = opened(browser)
    try:
        field = page.locator('form[data-intent="intent:evaluate"] input[name="expression"]')
        field.fill('4+5')
        field.press('Enter')
        result = page.locator('[data-state="state:arithmetic-result"]')
        result.get_by_text('9').wait_for()
        field.fill('6+7')
        # Old successful evidence is retained until new computation succeeds;
        # an invalid replacement must not destroy the last valid result.
        assert page.evaluate('window.__mcelExpanded.app.readState("state:arithmetic-result")') == '9'
        field.press('Enter')
        result.get_by_text('13').wait_for()
        page.locator('[data-mcel-expression-pad] button[data-pad-action="clear"]').click()
        assert field.input_value() == ''
        assert result.inner_text() == ''
    finally:
        page.close()


def test_graph_changes_and_reset_do_not_leave_stale_plot(browser):
    page = opened(browser)
    try:
        page.locator('[data-mcel-mode-tabs] button[data-value="graphing"]').click()
        form = page.locator('form[data-intent="intent:draw-graph"]')
        expression = form.locator('input[name="expression"]')
        expression.fill('sin(x)')
        form.locator('button[type="submit"]').click()
        page.locator('[data-mcel-plot] svg path').wait_for()
        assert len(page.evaluate('window.__mcelExpanded.app.readState("state:graph-points")')) > 100
        expression.fill('cos(x)')
        # A prior successful graph remains visible while editing; reset clears.
        assert page.locator('[data-mcel-plot] svg').count() == 1
        form.locator('button[type="submit"]').click()
        page.locator('[data-mcel-plot] svg path').wait_for()
        form.locator('button[type="reset"]').click()
        assert expression.input_value() == ''
        assert page.locator('[data-mcel-plot] svg').count() == 0
        assert page.evaluate('window.__mcelExpanded.app.readState("state:graph-points")') == []
    finally:
        page.close()


def test_discontinuous_function_plot_does_not_connect_across_pole(browser):
    page = opened(browser)
    try:
        page.locator('[data-mcel-mode-tabs] button[data-value="graphing"]').click()
        form = page.locator('form[data-intent="intent:draw-graph"]')
        form.locator('input[name="expression"]').fill('1/x')
        form.locator('button[type="submit"]').click()
        plot = page.locator('[data-mcel-plot] svg path')
        plot.wait_for()
        path = plot.get_attribute('d')
        assert path and path.count('M') >= 2, 'plot bridged a removed out-of-range/undefined interval'
        assert 'Infinity' not in path and 'NaN' not in path
        points = page.evaluate('window.__mcelExpanded.app.readState("state:graph-points")')
        assert len(points) >= 2
        assert all(set(p) == {'id', 'x', 'y'} for p in points)
    finally:
        page.close()


def test_edit_during_pending_compute_blocks_late_result(browser):
    page = opened(browser)
    try:
        result = page.evaluate('''async () => {
          const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
          const root=document.createElement('div');document.body.append(root);
          let finish;
          const port={authorize:async()=>true,invoke:()=>new Promise(resolve=>{finish=resolve})};
          const app=McelSemanticOperationalRuntime.mount(root,ir,{capabilities:port,presentation:'expert'});
          const form=root.querySelector('form[data-intent="intent:evaluate"]');
          const input=form.querySelector('input[name="expression"]');
          input.value='first';form.requestSubmit();
          for(let i=0;i<20 && !finish;i++)await new Promise(resolve=>setTimeout(resolve,0));
          if(!finish)throw Error('request was not dispatched');
          input.value='second';input.dispatchEvent(new Event('input',{bubbles:true}));
          finish('stale-value');
          await new Promise(resolve=>setTimeout(resolve,10));
          const outcome={value:app.readState('state:arithmetic-result'),receipts:app.receipts()};
          app.unmount();root.remove();return outcome;
        }''')
        assert result['value'] == ''
        assert all(r['disposition'] != 'completed' for r in result['receipts'])
    finally:
        page.close()


def test_result_question_includes_verified_calculator_evidence(browser):
    page = opened(browser)
    try:
        page.evaluate('''() => {
          window.__qaCalls=[];
          window.fetch=async(path,opts)=>{
            const payload=JSON.parse(opts.body);
            if(path==='/api/applications/calculator/qa') {
              window.__qaCalls.push(payload);
              return {ok:true,status:200,json:async()=>({ok:true,answer:'Based on computed evidence'})};
            }
            if(path==='/api/applications/calculator/mathics/evaluate')
              return {ok:true,status:200,json:async()=>({ok:true,result_text:'x^2/2'})};
            throw Error('unexpected request: '+path);
          };
        }''')
        arithmetic = page.locator('form[data-intent="intent:evaluate"] input[name="expression"]')
        arithmetic.fill('3 m + 40 cm')
        arithmetic.press('Enter')
        page.locator('[data-state="state:arithmetic-result"]').get_by_text('3.4 m').wait_for()
        page.locator('[data-mcel-mode-tabs] button[data-value="graphing"]').click()
        graph = page.locator('form[data-intent="intent:draw-graph"]')
        graph.locator('input[name="expression"]').fill('x^2')
        graph.locator('button[type="submit"]').click()
        page.locator('[data-mcel-plot] svg path').wait_for()
        page.locator('[data-mcel-mode-tabs] button[data-value="symbolic"]').click()
        symbolic = page.locator('form[data-intent="intent:evaluate-symbolic"]')
        symbolic.locator('input[name="expression"]').fill('Integrate[x,x]')
        symbolic.locator('button[type="submit"]').click()
        page.locator('[data-state="state:symbolic-result"]').get_by_text('x^2/2').wait_for()
        page.locator('[data-mcel-mode-tabs] button[data-value="help"]').click()
        form = page.locator('form[data-intent="intent:ask-result-question"]')
        form.locator('input[name="question"]').fill('Compare the results')
        form.locator('button[type="submit"]').click()
        page.locator('[data-state="state:qa-result"]').get_by_text('Based on computed evidence').wait_for()
        sent = page.evaluate('window.__qaCalls')
        assert len(sent) == 1
        assert sent[0]['question'] == 'Compare the results'
        import json
        context = json.loads(sent[0]['context'])
        assert context['arithmetic_result'] == '3.4 m'
        assert context['symbolic_result'] == 'x^2/2'
        assert 2 <= context['graph']['sample_count'] <= 161
        assert context['graph']['x_min'] < 0 < context['graph']['x_max']
        assert context['graph']['visible_y_min'] >= -10
        assert context['graph']['visible_y_max'] <= 10
        # A model explanation never modifies the already computed result states.
        assert page.evaluate('window.__mcelExpanded.app.readState("state:arithmetic-result")') == '3.4 m'
        assert page.evaluate('window.__mcelExpanded.app.readState("state:symbolic-result")') == 'x^2/2'
    finally:
        page.close()


def test_result_question_port_rejects_forged_computational_context(browser):
    page = opened(browser)
    try:
        result = page.evaluate('''async () => {
          const port=McelCalculatorExpandedPort.create();
          const base={appId:'semantic-calculator-expanded',intentId:'intent:ask-result-question',
            effectId:'effect:ask-result-question.request',capabilityId:'capability:calculator-service',
            operation:'question',runtimeMethod:'askResultQuestion',risk:'external-read',
            arguments:{question:'Explain',arithmeticResult:'3',symbolicResult:'',graphPoints:[]}};
          return [await port.authorize(base),
            await port.authorize({...base,arguments:{...base.arguments,graphPoints:[{id:'1',x:0,y:Infinity}]}}),
            await port.authorize({...base,arguments:{...base.arguments,shell:'run'}}),
            await port.authorize({...base,risk:'source-write'}),
            await port.authorize({...base,arguments:{...base.arguments,graphPoints:[{id:'1',x:0,y:1,html:'<script>'}]}})];
        }''')
        assert result == [True, False, False, False, False]
    finally:
        page.close()
