"""Source-to-renderer acceptance for semantic left navigation.

The browser must place the *authored* navigation destinations in the rail.
Geometry and the mere presence of a scroller are insufficient.
"""
from __future__ import annotations

from copy import deepcopy

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import compile_expanded, render_expanded_page
from main_computer.mcel_slh_application import compile_slh_application, render_slh_page
from main_computer.mcel_knowledge.semantic_composition import synthesize_composition, verify_composition
from tools.mcel_phase2b_browser import launch_semantic_browser
from tools.mcel_viewport_composition_oracle import DOM_OBSERVATION, assess


def test_slh_navigation_membership_is_authored_and_survives_composition():
    ir, composition = compile_slh_application()
    surface, = ir['surfaces']
    regions = surface['interfaceIntent']['regions']
    navs = {r['id']:r for r in regions if r['role'] == 'navigation'}
    assert set(navs) == {'interface-region:slh.identities',
                         'interface-region:slh.observation-query'}
    identities = navs['interface-region:slh.identities']
    observation = navs['interface-region:slh.observation-query']
    assert all(r['prefer'] == {'side':'start'} for r in navs.values())
    assert {x['ref'] for x in identities['members']} == {
        'surface-node:slh.available-local-identities',
    }
    assert {x['ref'] for x in observation['members']} == {'surface-node:slh.network-slh-target'}
    generated = [r for r in composition['regions'] if r['recipe'] == 'navigation']
    assert [r['id'] for r in generated] == [
        'interface-region:generated-navigation-observation-query',
        'interface-region:generated-navigation-identities',
    ]
    assert all(r['nativeTag'] == 'nav' and r['purpose'] == 'navigation' for r in generated)
    assert {x['ref'] for r in generated for x in r['members']} == {
        x['ref'] for r in navs.values() for x in r['members']}
    assert not any('refresh' in x['ref'] or 'worker-bank' in x['ref']
                   for r in generated for x in r['members'])
    verify_composition(ir, composition)

    # A tampered generated nav cannot take possession of administrative actions.
    tampered = deepcopy(composition)
    next(r for r in tampered['regions'] if r['id'] == 'interface-region:generated-navigation-identities')['members'] = [
        {'ref': 'surface-node:slh.refresh-health'}]
    with pytest.raises(ValueError):
        verify_composition(ir, tampered)


def test_calculator_authored_mode_control_remains_semantic():
    ir = compile_expanded()
    surface, = ir['surfaces']
    mode, = (r for r in surface['interfaceIntent']['regions']
             if r.get('purpose') == 'mode-control')
    assert mode['id'].endswith('.mode')
    assert any(x['ref'].endswith('.choose-mode') for x in mode['members'])
    assert any(x['id'] == 'intent:switch-mode' for x in ir['intents'])


def test_composition_oracle_refuses_false_navigation_ownership():
    # A perfect outer geometry cannot redeem incorrect semantic membership.
    observation = {
        'app': {'x': 0, 'y': 0, 'w': 1040, 'h': 768},
        'primary': {'id': 'workspace', 'purpose': 'workspace', 'scrollOwner': '',
                    'rect': {'x': 250, 'y': 0, 'w': 790, 'h': 768,
                             'scrollHeight': 900, 'clientHeight': 768, 'overflowY': 'auto'}},
        'navigation': {'id': 'navigator', 'purpose': 'navigation', 'scrollOwner': '',
                       'rect': {'x': 0, 'y': 0, 'w': 240, 'h': 768,
                                'scrollHeight': 800, 'clientHeight': 768, 'overflowY': 'auto'}},
        'panels': [], 'scrollRegions': [], 'viewport': {'width': 1040, 'height': 768},
        'navigationSemantics': {
            'authoredCount': 1, 'owned': False, 'noGenericQueryControls': False,
            'modeCount': 1, 'modeButtons': 4, 'verticalModes': False},
    }
    result = assess(observation)
    assert result['status'] == 'FAIL'
    assert {'authored-navigation-owned', 'navigation-excludes-generic-controls',
            'mode-selector-vertical'} <= set(result['failures'])


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as pw:
        b, _ = launch_semantic_browser(pw)
        try:
            yield b
        finally:
            b.close()


IDENTITY_FETCH = r'''() => {
 window.fetch=async url=>{
  if(String(url).includes('/identities'))return {ok:true,status:200,json:async()=>({
   schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'first-id',
   explanation:'Local identity',identities:[
    {deviceId:'first-id',label:'Default identity',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'},
    {deviceId:'second-id',label:'Second identity',fingerprint:'b'.repeat(64),defaultStatus:'ADDITIONAL'}]})};
  return {ok:false,status:503,json:async()=>({})};
 };
}'''


@pytest.mark.parametrize('width', [390, 1040, 1440])
def test_slh_identity_navigation_is_live_and_separate_from_operations(browser, width):
    page = browser.new_page(viewport={'width': width, 'height': 900})
    try:
        page.evaluate(IDENTITY_FETCH)
        page.set_content(render_slh_page(), wait_until='domcontentloaded')
        nav = page.locator('[data-mcel-natural-scroller="navigation"]')
        workspace = page.locator('[data-mcel-natural-scroller="workspace"]')
        choice = nav.locator('[data-select-key="second-id"]')
        choice.wait_for(timeout=6000)
        assert nav.locator('[data-mcel-purpose="navigation"] [data-collection]').count() == 1
        assert nav.get_by_text('Refresh Health').count() == 0
        assert workspace.get_by_text('Refresh Health').count() == 0
        assert workspace.get_by_role('heading',name='Configuration').count() == 1
        assert workspace.locator('form[data-intent="intent:configure-and-enable-organization"]').count() == 1
        assert nav.get_by_text('Default identity').count() >= 1
        assert page.evaluate('window.__mcelSlh.app.readState("state:selected-local-identity")') == 'first-id'
        choice.click()
        assert page.evaluate('window.__mcelSlh.app.readState("state:selected-local-identity")') == 'second-id'
        assert workspace.locator('[data-mcel-reference="selected"]').count() == 1
        assert workspace.locator('[data-mcel-reference-label]').inner_text() == 'Second identity'
        assert workspace.locator('[data-mcel-selected-details]').count() == 0
        assert page.locator('#mcel-slh-auth-status').inner_text() == 'Organization session not established'
        if width >= 680:
            placement = page.evaluate('''() => {const n=document.querySelector('[data-mcel-natural-scroller="navigation"]').getBoundingClientRect();
                const w=document.querySelector('[data-mcel-natural-scroller="workspace"]').getBoundingClientRect();
                return [n.x+n.width,w.x,n.height,w.height];}''')
            assert placement[0] < placement[1]
            # A semantic inspector may reflow below the workspace at medium
            # widths. The navigation then spans both rows instead of matching
            # only the height of the primary workspace row.
            if page.locator('[data-mcel-natural-scroller="inspection"]').count():
                assert placement[2] + 2 >= placement[3]
            else:
                assert abs(placement[2] - placement[3]) < 2
        result = assess(page.evaluate(DOM_OBSERVATION))
        assert result['status'] == 'PASS', result
        assert result['checks']['authored-navigation-owned']
        assert result['checks']['navigation-excludes-generic-controls']
    finally:
        page.close()


def _memory_only_storage(page):
    page.evaluate('''() => {const data=new Map();Object.defineProperty(window,'localStorage',{
      configurable:true,value:{getItem:k=>data.get(String(k))??null,
      setItem:(k,v)=>data.set(String(k),String(v)),removeItem:k=>data.delete(String(k)),
      clear:()=>data.clear(),key:i=>[...data.keys()][i]??null,
      get length(){return data.size;}}});}''')


@pytest.mark.parametrize('width', [390, 1040, 1440])
def test_calculator_mode_navigation_is_vertical_and_switches_real_workspaces(browser, width):
    page = browser.new_page(viewport={'width': width, 'height': 900})
    try:
        _memory_only_storage(page)
        page.set_content(render_expanded_page(), wait_until='domcontentloaded')
        assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
        nav = page.locator('[data-mcel-natural-scroller="navigation"]')
        mode = nav.locator('[data-mcel-purpose="mode-control"]')
        buttons = mode.locator('[data-mcel-mode-tabs] button')
        assert buttons.all_inner_texts() == ['Basic', 'Graphing', 'Symbolic', 'Help']
        coords = buttons.evaluate_all('''els => els.map(e=>{const r=e.getBoundingClientRect();
            return {x:r.x,top:r.top,bottom:r.bottom,width:r.width};})''')
        assert all(coords[i]['top'] >= coords[i-1]['bottom']-1 and
                   abs(coords[i]['x']-coords[0]['x']) < 2
                   for i in range(1,len(coords)))
        nav.locator('[data-value="graphing"]').click()
        assert page.evaluate('window.__mcelExpanded.app.readState("state:mode")') == 'graphing'
        assert page.locator('form[data-intent="intent:draw-graph"]').is_visible()
        result = assess(page.evaluate(DOM_OBSERVATION))
        assert result['status'] == 'PASS', result
        assert result['checks']['mode-selector-vertical']
    finally:
        page.close()
