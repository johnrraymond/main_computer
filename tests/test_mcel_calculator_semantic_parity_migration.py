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
    # Shared Chat Console persistence requires an origin-safe store in this
    # opaque browser fixture; production uses the normal browser store.
    page.evaluate("""() => {const data=new Map();Object.defineProperty(window,'localStorage',{
      configurable:true,value:{getItem:k=>data.get(String(k))??null,
      setItem:(k,v)=>data.set(String(k),String(v)),removeItem:k=>data.delete(String(k)),
      clear:()=>data.clear(),key:i=>[...data.keys()][i]??null,
      get length(){return data.size;}}});}""")
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
          const port={authorize:async()=>true,invoke:()=>new Promise(resolve=>{finish=resolve}),
            mountEmbeddedChat:()=>({dispose(){}})};
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


def test_single_help_conversation_receives_verified_calculator_evidence(browser):
    page = opened(browser)
    try:
        page.evaluate('''() => {
          window.__helpRequests=[];
          window.fetch=async(path,opts)=>{
            const payload=opts?.body?JSON.parse(opts.body):{};
            window.__helpRequests.push({path:String(path),payload});
            if(String(path).startsWith('/api/applications/chat-console/ai/capacity?'))
              return {ok:true,status:200,json:async()=>({ok:true,busy:false,available_now:true,active_run_count:0,max_local_concurrency:1})};
            if(path==='/api/activity/snapshot' || path==='/api/activity/ollama-ps')
              return {ok:true,status:200,json:async()=>({ok:true,events:[],models:[]})};
            if(path==='/api/applications/chat-console/cell/evaluate')
              return {ok:true,status:200,json:async()=>({ok:true,status:'completed',
                output_cell:{id:'out-mock',type:'output',source_cell_id:payload.cell.id,status:'ok',
                  parts:[{id:'part-mock',kind:'text',title:'Explanation',content:'Based on computed evidence',snippets:[]}]}})};
            if(path==='/api/applications/calculator/mathics/prepare')
              return {ok:true,status:200,json:async()=>({ok:true,status:'ready'})};
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
        assert page.locator('[data-mcel-embedded-chat]').count() == 1
        assert page.locator('form[data-intent="intent:ask-result-question"]').count() == 0
        composer = page.locator('[data-chat-conversation-role="composer"]')
        composer.locator('textarea').fill('Compare the results')
        composer.locator('.chat-cell-controls button').first.click()
        page.locator('[data-chat-conversation-role="assistant"]').get_by_text('Based on computed evidence').wait_for()
        calls = page.evaluate('window.__helpRequests')
        sent = [x['payload'] for x in calls if x['path']=='/api/applications/chat-console/cell/evaluate']
        assert len(sent) == 1
        assert sent[0]['cell']['source'] == 'Compare the results'
        context = sent[0]['embedded_context']
        assert context['arithmetic']['result'] == '3.4 m'
        assert context['graph']['expression'] == 'x^2'
        assert context['graph']['status'] == 'graph sampled'
        assert float(context['graph']['x_min']) < 0 < float(context['graph']['x_max'])
        assert context['mathics']['expression'] == 'Integrate[x,x]'
        assert context['mathics']['result'] == 'x^2/2'
        assert context['active_mode'] == 'symbolic'
        # AI explanations do not alter deterministic computed evidence.
        assert page.evaluate('window.__mcelExpanded.app.readState("state:arithmetic-result")') == '3.4 m'
        assert page.evaluate('window.__mcelExpanded.app.readState("state:symbolic-result")') == 'x^2/2'
    finally:
        page.close()


def test_retired_result_question_port_refuses_legacy_and_forged_requests(browser):
    page = opened(browser)
    try:
        result = page.evaluate('''async () => {
          const port=McelCalculatorExpandedPort.create();
          const base={appId:'semantic-calculator-expanded',intentId:'intent:calculator-chat',
            effectId:'effect:calculator-chat.request',capabilityId:'capability:calculator-service',
            operation:'chat',runtimeMethod:'calculatorEmbeddedChat',risk:'external-read',
            arguments:{message:'Explain'}};
          return [await port.authorize(base),
            await port.authorize({...base,intentId:'intent:ask-result-question',
              effectId:'effect:ask-result-question.request',operation:'question',
              runtimeMethod:'askResultQuestion'}),
            await port.authorize({...base,arguments:{...base.arguments,arithmeticResult:'forged'}}),
            await port.authorize({...base,risk:'source-write'}),
            await port.authorize({...base,arguments:{...base.arguments,graphPoints:[{id:'1',x:0,y:1}]}})];
        }''')
        assert result == [True, False, False, False, False]
    finally:
        page.close()
