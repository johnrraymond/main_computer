"""Opt-in intrinsic semantic working unit: geometry and isolation oracles.

The identity of the app is irrelevant to composition: the paired semantic
regions declare widthGroup/sizing/align; other regions remain unchanged.
"""
from __future__ import annotations

from copy import deepcopy

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import compile_expanded, render_expanded_page
from main_computer.mcel_knowledge.interface_intent import html_interface_intent_errors
from main_computer.mcel_semantic_html import SemanticHtmlError, parse_semantic_html
from tools.mcel_phase2b_browser import launch_semantic_browser


def test_compact_working_unit_is_authored_and_has_a_valid_pair():
    surface = compile_expanded()['surfaces'][0]
    regions = {r['id'].rsplit('.', 1)[-1]: r for r in surface['interfaceIntent']['regions']}
    assert regions['answer']['preferences'] == {
        'density': 'compact', 'widthGroup': 'calculation', 'sizing': 'intrinsic', 'align': 'end'}
    assert regions['arithmetic']['preferences'] == {
        'density': 'compact', 'widthGroup': 'calculation', 'sizing': 'intrinsic'}
    assert not html_interface_intent_errors(surface)
    # Symbolic has its own automatic-width group: it must not inherit the
    # intrinsic keypad bounds of the Basic calculation unit.
    assert regions['mathics-output']['preferences'] == {
        'density': 'compact', 'widthGroup': 'symbolic-calculation', 'align': 'end'}
    assert regions['mathics-input']['preferences'] == {'widthGroup': 'symbolic-calculation'}
    assert all('widthGroup' not in r.get('preferences', {})
               for name, r in regions.items()
               if name not in {'answer', 'arithmetic', 'mathics-output', 'mathics-input'})


def test_width_group_contract_rejects_unpaired_or_inconsistent_regions():
    surface = compile_expanded()['surfaces'][0]
    by_name = {r['id'].rsplit('.', 1)[-1]: r for r in surface['interfaceIntent']['regions']}
    unpaired = deepcopy(surface)
    for region in unpaired['interfaceIntent']['regions']:
        if region['id'] == by_name['arithmetic']['id']:
            del region['preferences']['widthGroup']
            del region['preferences']['sizing']
    assert any('width group' in error for error in html_interface_intent_errors(unpaired))

    mismatched = deepcopy(surface)
    for region in mismatched['interfaceIntent']['regions']:
        if region['id'] == by_name['answer']['id']:
            region['preferences']['sizing'] = 'automatic'
    assert any('width group' in error for error in html_interface_intent_errors(mismatched))

    invalid = deepcopy(surface)
    for region in invalid['interfaceIntent']['regions']:
        if region['id'] == by_name['answer']['id']:
            region['preferences']['widthGroup'] = ['untrusted']
    assert html_interface_intent_errors(invalid)


def test_html_parser_rejects_intrinsic_sizing_without_a_group():
    with pytest.raises(SemanticHtmlError, match='Intrinsic sizing requires a width group'):
        parse_semantic_html(
            '<main data-mcel-id="surface" data-mcel-purpose="workspace">'
            '<section data-mcel-id="value" data-mcel-prefer-sizing="intrinsic"></section>'
            '</main>', app_id='fixture', source='fixture.html')



@pytest.mark.parametrize('name', ['file-manager','task-manager','code-editor'])
def test_unrelated_paired_applications_do_not_opt_in(name):
    from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
    surface = compile_semantic_app(name)['surfaces'][0]
    assert all('widthGroup' not in r.get('preferences', {})
               for r in surface['interfaceIntent']['regions'])

@pytest.mark.parametrize('width,height', [(1260, 400), (1100, 400), (390, 720)])
def test_calc_result_expression_evaluate_and_keypad_share_intrinsic_bounds(width, height):
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        page = browser.new_page(viewport={'width': width, 'height': height}, device_scale_factor=1)
        try:
            page.evaluate('''() => {
              const values=new Map();const memory={getItem:k=>values.get(String(k))??null,
                setItem:(k,v)=>values.set(String(k),String(v)),
                removeItem:k=>values.delete(String(k)),clear:()=>values.clear(),
                key:i=>[...values.keys()][i]??null,get length(){return values.size;}};
              Object.defineProperty(window,'localStorage',{configurable:true,value:memory});
            }''')
            page.set_content(render_expanded_page())
            assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
            field = page.locator('form[data-intent="intent:evaluate"] input[name="expression"]')
            field.fill('8*3')
            page.locator('form[data-intent="intent:evaluate"] > button[type="submit"]').click()
            page.locator('[data-state="state:arithmetic-result"]').get_by_text('24').wait_for()
            bounds = page.evaluate('''() => {
              const find=s=>document.querySelector(s);
              const rect=s=>{const b=find(s).getBoundingClientRect();return {
                left:b.left,top:b.top,right:b.right,bottom:b.bottom,width:b.width};};
              const selectors={
                unit:'[data-mcel-working-unit="calculation"]',
                result:'[data-mcel-purpose="results"][data-mcel-align="end"]',
                answer:'[data-state="state:arithmetic-result"]',
                compute:'[data-mcel-purpose="input"][data-mcel-width-group="calculation"]',
                field:'form[data-mcel-composition] input[name="expression"]',
                evaluate:'form[data-mcel-composition] > button[type="submit"]',
                pad:'[data-mcel-expression-pad]',
                workspace:'[data-mcel-natural-scroller="workspace"]'};
              const out=Object.fromEntries(Object.entries(selectors).map(([k,v])=>[k,rect(v)]));
              out.unitCount=document.querySelectorAll('[data-mcel-working-unit]').length;
              out.pageOverflow=document.documentElement.scrollWidth-innerWidth;
              out.workspaceOverflow=find('[data-mcel-natural-scroller="workspace"]').scrollWidth-
                find('[data-mcel-natural-scroller="workspace"]').clientWidth;
              return out;
            }''')
            assert bounds['unitCount'] == 2  # Basic intrinsic + Symbolic automatic, mode-isolated
            assert bounds['result']['top'] < bounds['field']['top']
            assert abs(bounds['compute']['width'] - bounds['result']['width']) < 1
            assert abs(bounds['answer']['right'] - bounds['pad']['right']) < 2
            assert abs(bounds['field']['left'] - bounds['pad']['left']) < 2
            assert abs(bounds['evaluate']['right'] - bounds['pad']['right']) < 2
            assert bounds['unit']['right'] <= bounds['workspace']['right'] + 1
            assert bounds['pageOverflow'] <= 1
            assert bounds['workspaceOverflow'] <= 1
            if width >= 1100:
                assert bounds['unit']['width'] < bounds['workspace']['width'] * .8, bounds
            # Switching to another mode must remove the entire hidden working
            # unit from layout without moving its original live controls.
            page.locator('[data-mcel-mode-tabs] button').get_by_text('Graphing').click()
            assert page.locator('[data-mcel-working-unit="calculation"]').evaluate(
                'e=>getComputedStyle(e).display') == 'none'
            assert page.locator('form[data-intent="intent:draw-graph"]').is_visible()
            page.locator('[data-mcel-mode-tabs] button').get_by_text('Basic').click()
            assert page.locator('[data-mcel-working-unit="calculation"]').is_visible()
            assert field.input_value() == '8*3'
        finally:
            page.close()
            browser.close()
