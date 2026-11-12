"""Calculator compact-density acceptance at 100% zoom and a short app viewport.

The authored compute/result regions opt into density, so this test rejects
solutions that only hide the obsolete heading or shrink unrelated MCEL forms.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import compile_expanded, render_expanded_page
from tools.mcel_phase2b_browser import launch_semantic_browser


def test_compact_density_is_authored_semantics_not_hidden_heading():
    regions = {r['id'].rsplit('.', 1)[-1]: r for r in
               compile_expanded()['surfaces'][0]['interfaceIntent']['regions']}
    assert regions['arithmetic']['preferences']['density'] == 'compact'
    assert regions['answer']['preferences']['density'] == 'compact'
    assert not regions['arithmetic'].get('staticContent')
    assert regions['answer']['staticContent'][0]['text'] == 'Calculated Result'


@pytest.mark.parametrize('width,height', [(1260, 400), (1100, 400), (390, 720)])
def test_calculator_working_surface_is_compact_and_usable(width, height):
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        page = browser.new_page(viewport={'width': width, 'height': height}, device_scale_factor=1)
        try:
            page.evaluate('''() => {
              const data=new Map();Object.defineProperty(window,'localStorage',
                {configurable:true,value:{getItem:k=>data.get(String(k))??null,
                setItem:(k,v)=>data.set(String(k),String(v)),
                removeItem:k=>data.delete(String(k)),clear:()=>data.clear(),
                key:i=>[...data.keys()][i]??null,get length(){return data.size;}}});
            }''')
            page.set_content(render_expanded_page())
            assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
            assert page.get_by_text('Arithmetic and Units').count() == 0
            pad = page.locator('[data-mcel-expression-pad]')
            assert pad.locator('button').count() == 21
            assert page.locator('[data-mcel-natural-scroller="navigation"] [data-mcel-mode-tabs] button').count() == 4
            assert page.locator('[data-mcel-natural-scroller="inspection"] [data-mcel-activity]').count() == 1
            layout = page.evaluate('''() => {
              const find=q=>document.querySelector(q),rect=e=>e.getBoundingClientRect();
              const workspace=find('[data-mcel-natural-scroller="workspace"]');
              const result=find('[data-mcel-purpose="results"][data-mcel-density="compact"]');
              const compute=find('[data-mcel-purpose="input"][data-mcel-density="compact"]');
              const expression=find('form[data-mcel-composition] input[name="expression"]');
              const evaluate=find('form[data-mcel-composition] > button[type="submit"]');
              const keys=[...document.querySelectorAll('[data-mcel-expression-pad] button')];
              return {resultTop:rect(result).top, resultHeight:rect(result).height,
                inputTop:rect(expression).top, buttonTop:rect(evaluate).top,
                keyHeight:Math.max(...keys.map(k=>rect(k).height)),
                bottom:rect(keys[keys.length-1]).bottom,
                workspaceBottom:rect(workspace).bottom,
                scrollExcess:workspace.scrollHeight-workspace.clientHeight,
                horizontalOverflow:document.documentElement.scrollWidth-innerWidth,
                formHeight:rect(compute).height};
            }''')
            assert layout['resultTop'] < layout['inputTop']
            assert layout['resultHeight'] <= 46
            assert layout['keyHeight'] <= 38
            assert layout['formHeight'] < 320
            assert abs(layout['buttonTop'] - layout['inputTop']) < 2
            assert layout['horizontalOverflow'] <= 0
            if width >= 1100:
                assert layout['scrollExcess'] <= 1
                assert layout['bottom'] <= layout['workspaceBottom']
            # Both submission paths retain the same deterministic Calculator core.
            field = page.locator('form[data-intent="intent:evaluate"] input[name="expression"]')
            field.fill('7+8')
            page.locator('form[data-intent="intent:evaluate"] > button[type="submit"]').click()
            page.locator('[data-state="state:arithmetic-result"]').get_by_text('15').wait_for()
            pad.locator('button[data-pad-action="clear"]').click()
            for action in ('2', '+', '3', 'submit'):
                pad.locator(f'button[data-pad-action="{action}"]').click()
            page.locator('[data-state="state:arithmetic-result"]').get_by_text('5').wait_for()
        finally:
            page.close()
            browser.close()
