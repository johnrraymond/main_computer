"""Symbolic mode uses result-first, compact, right-aligned semantics.

Unlike Basic, symbolic expressions are not constrained to the keypad's intrinsic
width: the entire available workspace is useful for lengthy Mathics results.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import compile_expanded, render_expanded_page
from tools.mcel_phase2b_browser import launch_semantic_browser


def test_symbolic_result_declares_layout_semantics_with_full_width_symbolic_group():
    regions = {
        region['id'].rsplit('.', 1)[-1]: region
        for region in compile_expanded()['surfaces'][0]['interfaceIntent']['regions']
    }
    result = regions['mathics-output']
    assert result['purpose'] == 'results'
    assert result['priority'] == 'primary'
    assert result['preferences'] == {'density': 'compact', 'widthGroup': 'symbolic-calculation', 'align': 'end'}
    assert result['visibleWhen']['equals'] == 'symbolic'
    assert regions['mathics-input']['preferences'] == {'widthGroup': 'symbolic-calculation'}
    assert 'sizing' not in result['preferences']
    assert regions['answer']['preferences']['widthGroup'] == 'calculation'
    assert regions['graph-output']['parentId'] == regions['status']['parentId']


@pytest.mark.parametrize('width,height', [(390, 720), (800, 720), (1260, 720)])
def test_symbolic_result_precedes_input_and_right_aligns_long_output(width, height):
    with sync_playwright() as playwright:
        browser, _ = launch_semantic_browser(playwright)
        page = browser.new_page(viewport={'width': width, 'height': height}, device_scale_factor=1)
        try:
            page.evaluate('''() => {
              const store=new Map();
              Object.defineProperty(window,'localStorage',{
                configurable:true,value:{getItem:k=>store.get(String(k))??null,
                setItem:(k,v)=>store.set(String(k),String(v)),
                removeItem:k=>store.delete(String(k)),clear:()=>store.clear(),
                key:i=>[...store.keys()][i]??null,get length(){return store.size;}}
              });
            }''')
            page.set_content(render_expanded_page())
            assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
            page.locator('[data-mcel-mode-tabs] button[data-value="symbolic"]').click()
            result = page.locator('[data-mcel-region$=".mathics-output"]')
            form_region = page.locator('[data-mcel-region$=".mathics-input"]')
            assert result.is_visible() and form_region.is_visible()
            assert page.locator('[data-mcel-working-unit="calculation"]').is_hidden()
            before = page.evaluate('''() => {
              const result=document.querySelector('[data-mcel-region$=".mathics-output"]');
              const input=document.querySelector('[data-mcel-region$=".mathics-input"]');
              const workspace=document.querySelector('[data-mcel-natural-scroller="workspace"]');
              const value=result.querySelector('[data-node-kind="state-value"]');
              const output=value.querySelector('output');
              const rr=result.getBoundingClientRect();
              const vr=value.getBoundingClientRect();
              const wr=workspace.getBoundingClientRect();
              return {above:rr.bottom<=input.getBoundingClientRect().top,
                height:rr.height, width:rr.width, workspaceWidth:wr.width,
                valueRight:vr.right, resultRight:rr.right,
                textAlign:getComputedStyle(output).textAlign,
                overflow:document.documentElement.scrollWidth-innerWidth};
            }''')
            assert before['above'], before
            assert before['height'] <= 52, before
            assert before['overflow'] <= 1, before
            assert abs(before['valueRight']-before['resultRight']) <= 12, before
            assert before['textAlign'] in ('end', 'right'), before
            if width >= 800:
                assert before['width'] > 0.85 * before['workspaceWidth'], before

            # Verify the live Mathics response uses the full, right-aligned result
            # region without losing data or introducing horizontal overflow.
            long_result='(x^3 + 7*x^2 + 11*x + 13)/(x^2 + 5*x + 6) + Integrate[Sin[x]^2, {x, 0, Pi}]'
            page.evaluate('''(reply) => {
              window.fetch=async () => ({ok:true,status:200,json:async()=>({ok:true,result_text:reply})});
            }''', long_result)
            form=page.locator('form[data-intent="intent:evaluate-symbolic"]')
            form.locator('input[name="expression"]').fill('Simplify[(x^3 + 1)/(x + 1)]')
            form.locator('button[type="submit"]').click()
            page.locator('[data-state="state:symbolic-result"]').get_by_text(long_result, exact=True).wait_for()
            assert page.evaluate('window.__mcelExpanded.app.readState("state:symbolic-result")') == long_result
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert result.is_visible()
            page.locator('[data-mcel-mode-tabs] button[data-value="basic"]').click()
            assert result.is_hidden()
            assert page.locator('[data-mcel-working-unit="calculation"]').is_visible()
            page.locator('[data-mcel-mode-tabs] button[data-value="symbolic"]').click()
            assert result.is_visible()
            assert page.locator('[data-state="state:symbolic-result"]').get_by_text(long_result, exact=True).is_visible()
        finally:
            page.close()
            browser.close()
