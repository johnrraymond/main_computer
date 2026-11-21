"""Semantic argument proof and real browser geometry/scroll/Chrome confinement."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_application_ir import semantic_fingerprint
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from main_computer.mcel_knowledge.code_argument_resolver import resolve_code_arguments
from main_computer.mcel_knowledge.workspace_plan import plan_workspace
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT = Path(__file__).resolve().parents[1]


def sources():
    ir = compile_semantic_app('code-editor', ROOT)
    return ir, plan_workspace(ir), None


def rebound(ir):
    original = copy.deepcopy(ir)
    for node in original['surfaces'][0]['nodes']:
        node.pop('externalJob', None)
    ir['fingerprints']['semantic'] = semantic_fingerprint(original)
    return plan_workspace(ir)


def test_resolver_refuses_stale_source_plan():
    ir, plan, policy = sources()
    edited = copy.deepcopy(ir)
    edited['intents'][0]['description'] = 'edited semantic declaration'
    rebound(edited)
    with pytest.raises(ValueError, match='fingerprint|disagrees'):
        resolve_code_arguments(edited, plan)


@pytest.mark.parametrize('mutation', [
    'effect-risk', 'effect-owner', 'effect-target',
    'capability-handler', 'capability-risk', 'effect-identity',
])
def test_argument_binding_refuses_broken_effect_graph(mutation):
    ir, _, policy = sources()
    edited = copy.deepcopy(ir)
    intent = next(i for i in edited['intents'] if i['id'] == 'intent:run-aider')
    effect = next(e for e in edited['effects'] if e['id'] == 'effect:run-aider.request')
    cap = next(c for c in edited['capabilities'] if c['id'] == 'capability:aider-control')
    if mutation == 'effect-risk':
        effect['risk'] = 'external-read'
    elif mutation == 'effect-owner':
        effect['owner'] = {'ref': 'intent:aider-jobs'}
    elif mutation == 'effect-target':
        effect['target'] = {'ref': 'capability:source-reader'}
    elif mutation == 'capability-handler':
        next(op for op in cap['operations'] if op['name']=='run')['runtimeMethod'] = 'wrongHandler'
    elif mutation == 'capability-risk':
        cap['risk'] = 'external-read'
    elif mutation == 'effect-identity':
        intent['effectRefs'] = [{'ref': 'effect:aider-jobs.request'}]
    plan = rebound(edited)
    with pytest.raises(ValueError, match='authority|handler|effect|operation'):
        resolve_code_arguments(edited, plan)


def test_graph_matcher_is_independent_of_application_policy():
    ir,plan,_=sources()
    assert resolve_code_arguments(ir,plan)['groups']==resolve_code_arguments(ir,plan)['groups']
    with pytest.raises(ValueError,match='Legacy override'):
        resolve_code_arguments(ir,plan,{'profile':'code'})


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as playwright:
        instance,_=launch_semantic_browser(playwright)
        yield instance
        instance.close()


def live_page(browser,app,width):
    page=browser.new_page(viewport={'width':width,'height':820})
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.set_content(render_semantic_page(app,ROOT,promoted=True))
    assert page.locator('#mcel-workbench-status').inner_text()=='Semantic application ready',errors
    return page,errors


@pytest.mark.parametrize('width',[320,390,760,1024,1100,1366])
def test_editor_docks_have_reachable_bounded_geometry_and_one_scroll_owner(browser,width):
    page,errors=live_page(browser,'code-editor',width)
    try:
        page.wait_for_function("() => document.querySelector('[data-mcel-document-workbench]')?.dataset.mcelSurfaceContract==='pass'")
        if width<780:
            assert page.locator('[data-mcel-document-workbench]').get_attribute('data-mcel-explorer-open')=='true'
            assert page.locator('[data-mcel-document-workbench]').get_attribute('data-mcel-companion-open')=='false'
            # A real first-row explorer action must not be intercepted by editor tabs.
            target = page.locator('[data-mcel-document-explorer] button').first
            target.scroll_into_view_if_needed()
            assert target.evaluate('''el => {const r=el.getBoundingClientRect();
              const h=el.ownerDocument.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
              return el===h||el.contains(h);}''')
            page.locator('[data-mcel-document-activity="companion"]').first.click()
            assert page.locator('[data-mcel-document-workbench]').get_attribute('data-mcel-explorer-open')=='false'
            assert page.locator('[data-mcel-document-workbench]').get_attribute('data-mcel-companion-open')=='true'
        info = page.evaluate('''() => {
          const s=document.querySelector('[data-mcel-document-workbench]');
          const c=s.querySelector('[data-mcel-document-companion]');
          const r=c.getBoundingClientRect(),outer=s.getBoundingClientRect();
          return {width:r.width,height:r.height,top:r.top,bottom:r.bottom,
            boundTop:outer.top,boundBottom:outer.bottom,
            overflow:document.documentElement.scrollWidth>innerWidth,
            state:s.dataset.mcelSurfaceContract,issues:s.dataset.mcelSurfaceViolations};
        }''')
        assert info['width']>=24 and info['height']>=24,info
        assert info['top']>=info['boundTop']-1 and info['bottom']<=info['boundBottom']+1,info
        assert not info['overflow'],info
        assert info['state']=='pass',info
        # Force enough height to require scrolling in the companion's intended
        # single tab-panel owner; body remains stationary and navigable.
        if page.locator('[data-mcel-document-workbench]').get_attribute('data-mcel-companion-open')!='true':
            page.locator('[data-mcel-document-activity="companion"]').first.click()
        scroll=page.evaluate('''() => {
          const s=document.querySelector('[data-mcel-document-workbench]');
          const p=s.querySelector('[data-mcel-document-tabpanel="companion"]:not([hidden])');
          const tall=document.createElement('div');tall.style.height='1900px';tall.dataset.mcelScrollProbe='';
          p.appendChild(tall);p.scrollTop=0;const before=document.documentElement.scrollTop;
          p.scrollTop=450;
          return {top:p.scrollTop,owner:getComputedStyle(p).overflowY,
            bodyBefore:before,bodyAfter:document.documentElement.scrollTop,
            height:p.clientHeight,content:p.scrollHeight};
        }''')
        assert scroll['top']>=200 and scroll['owner'] in ('auto','scroll'),scroll
        assert scroll['bodyBefore']==scroll['bodyAfter'] and scroll['content']>scroll['height'],scroll
        assert not errors,errors
    finally:
        page.close()


@pytest.mark.parametrize('app',['file-manager','task-manager'])
def test_other_semantic_apps_do_not_gain_document_workbench_or_overlay(browser,app):
    page,errors=live_page(browser,app,760)
    try:
        assert page.locator('[data-mcel-document-workbench]').count()==0
        assert page.locator('[data-mcel-region]').count()>0
        assert not errors,errors
    finally:
        page.close()


def test_code_grouping_is_in_initial_render_and_preserves_chrome(browser):
    ir,plan,_=sources()
    args=resolve_code_arguments(ir,plan)
    page,errors=live_page(browser,'code-editor',1366)
    try:
        assert page.locator('[data-mcel-semantic-template-group]').count()==3
        region=page.locator('[data-mcel-region$=".aider"]')
        assert region.locator('[data-mcel-node]').count()==11
        assert set(region.locator('[data-mcel-semantic-template-group]').evaluate_all(
            'els=>els.map(el=>el.dataset.mcelSemanticTemplateGroup)'))==set(args['groups'])
        for name,ids in args['groups'].items():
            actual=page.locator(f'[data-mcel-semantic-template-group="{name}"] [data-mcel-node]').evaluate_all(
                'els=>els.map(el=>el.dataset.mcelNode)')
            assert sorted(actual)==sorted(ids),(name,actual,ids)
        buttons=region.locator('button').evaluate_all(
            'els=>els.map(el=>el.getAttribute("data-mcel-chrome-material"))')
        assert buttons and all(x in ('raised-control','emissive-control') for x in buttons)
        assert not errors,errors
    finally:
        page.close()


def test_live_surface_tripwire_detects_and_recovers_from_collapsed_child(browser):
    page,errors=live_page(browser,'code-editor',1100)
    try:
        root=page.locator('[data-mcel-document-workbench]')
        page.wait_for_function("() => document.querySelector('[data-mcel-document-workbench]')?.dataset.mcelSurfaceContract==='pass'")
        page.locator('[data-mcel-document-companion]').evaluate(
            "el => el.style.setProperty('width','0px','important')")
        page.wait_for_function("() => document.querySelector('[data-mcel-document-workbench]')?.dataset.mcelSurfaceContract==='failed'",
                               timeout=5000)
        assert 'companion:zero-or-unusable-geometry' in (root.get_attribute('data-mcel-surface-violations') or '')
        page.locator('[data-mcel-document-companion]').evaluate(
            "el => el.style.removeProperty('width')")
        page.wait_for_function("() => document.querySelector('[data-mcel-document-workbench]')?.dataset.mcelSurfaceContract==='pass'",
                               timeout=5000)
        assert not errors,errors
    finally:
        page.close()
