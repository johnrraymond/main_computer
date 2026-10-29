"""Patch 14.5: read-only, human-first *single universal* profile acceptance.

These assertions do not certify subjective superiority or production promotion.
They ensure visual disclosure does not damage source integrity or live continuity.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from playwright.sync_api import sync_playwright
import pytest

from main_computer.mcel_knowledge.universal_layout import PROFILE, compose
from main_computer.mcel_knowledge.ir_bridge import compile_knowledge_ir, resolve_knowledge
from tools.mcel_patch13_live_adoption_frob import _chrome
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture
from tools.mcel_patch14_visual_quality_frob import CASES, build_case

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'main_computer/web/applications/scripts'


def test_human_first_is_one_profile_without_application_specific_code():
    src=(SCRIPTS/'mcel-semantic-collection-workspace.js').read_text()
    lab=(SCRIPTS/'mcel-universal-semantic-layout-lab.js').read_text()
    assert 'humanFirst=opts.humanFirst===true' in src
    assert 'humanFirst:true' in lab
    assert 'productionPromotion!==false' in lab
    assert "const PROFILE='mcel.layout.universal.v1'" in lab
    assert 'task-manager.processes' not in src and 'file-explorer.entries' not in src
    assert 'worker.market-workers' not in src
    for ob,fixture,purpose in CASES:
        plan=build_case(ob,fixture,purpose,'normal')['plan']
        assert plan['profile']==PROFILE and plan['productionPromotion'] is False
        assert plan['collection']['spec']['fields'][-1]['key']=='mcelIdentity'
        assert plan['collection']['spec']['fields'][-1]['tier']=='primary'
        assert plan['collection']['spec']['identity']=='mcelIdentity'


@pytest.mark.parametrize('case',CASES,ids=[c[0] for c in CASES])
@pytest.mark.parametrize('stressed',('normal','stressed'))
@pytest.mark.parametrize('width',(390,1440))
def test_human_first_chromium_selection_exact_recovery_and_keyboard(case,stressed,width):
    ob,fixture,purpose=case
    plan=build_case(ob,fixture,purpose,stressed)['plan']
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            page=browser.new_page(viewport={'width':width,'height':900},reduced_motion='reduce')
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.set_content(html_fixture(),wait_until='load')
            page.add_script_tag(path=str(SCRIPTS/'mcel-layout-continuity.js'))
            page.add_script_tag(path=str(SCRIPTS/'mcel-universal-semantic-layout-lab.js'))
            report=page.evaluate('''plan=>{
              const root=document.createElement('section');root.id='patch145-check';
              root.style.cssText='max-width:100%;min-width:0;width:100%';
              document.querySelector('#task-manager-app').append(root);
              window.__p145=McelUniversalSemanticLayoutLab.mount(root,plan,{lab:true});
              const rows=[...root.querySelectorAll('mcel-row')];
              rows[0].focus();rows[0].dispatchEvent(new KeyboardEvent('keydown',
                {key:'Enter',bubbles:true,cancelable:true}));
              const detail=root.querySelector('details.mcel-semantic-collection__exact-evidence');
              const exact=detail.querySelector('.mcel-semantic-collection__raw-values');
              const fields=Object.fromEntries([...exact.querySelectorAll('dt')].map((dt,i)=>
                [dt.textContent,exact.querySelectorAll('dd')[i].textContent]));
              const before={
                profile:window.__p145.profile,rows:rows.length,
                selected:window.__p145.collection.inspectedId,
                firstField:rows[0].firstElementChild?.dataset.semanticField,
                detailCollapsed:!detail.open,
                machineIdentity:fields.mcelIdentity,
                canonicalRecord:fields.mcelCanonicalRecord,
                hasHumanFacts:root.querySelectorAll('.mcel-semantic-collection__readable-facts dt').length>0,
                hasTitle:root.querySelector('.mcel-semantic-collection__inspector h3')?.textContent.startsWith('Selected:'),
                focusPreserved:document.activeElement===rows[0],
                rowSelected:rows[0].getAttribute('aria-pressed')==='true',
                overflow:document.documentElement.scrollWidth>innerWidth+2,
                regions:root.querySelectorAll('.mcel-universal-composition__region').length
              };
              detail.querySelector('summary').click();
              before.exactOpened=detail.open;
              before.canonicalAfterOpen=exact.querySelectorAll('dd')[1]?.textContent||'';
              return before;
            }''',plan)
            assert not errors,errors
            assert report['profile']==PROFILE and report['regions']==len(plan['regions'])
            assert report['rows']==len(plan['collection']['rows']) and not report['overflow']
            assert report['selected']==plan['collection']['rows'][0]['mcelIdentity']
            assert report['machineIdentity']==plan['collection']['rows'][0]['mcelIdentity']
            assert json.loads(report['canonicalRecord'])==plan['collection']['rows'][0]['mcelCanonicalRecord']
            assert report['detailCollapsed'] and report['exactOpened']
            assert report['hasTitle'] and report['hasHumanFacts']
            assert report['focusPreserved'] and report['rowSelected']
            assert report['firstField']!='mcelIdentity'
            page.close()
        finally:browser.close()


def test_selection_is_not_reassigned_after_unverified_source_generation_change():
    ob,fixture,purpose=CASES[0]
    base=json.loads((ROOT/'tools/fixtures/mcel_computer_knowledge'/fixture).read_text())
    bridge=compile_knowledge_ir('human-first-test',observation_ids=[ob])
    current=compose(resolve_knowledge(base,bridge),ob,purpose=purpose)
    changed=deepcopy(base);changed['processes'][0]['name']='renamed-but-same-lifetime'
    next_plan=compose(resolve_knowledge(changed,bridge),ob,purpose=purpose,prior=current['commit'])
    assert next_plan['decision']==current['decision']
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            page=browser.new_page(viewport={'width':390,'height':900})
            page.set_content(html_fixture(),wait_until='load')
            page.add_script_tag(path=str(SCRIPTS/'mcel-layout-continuity.js'))
            page.add_script_tag(path=str(SCRIPTS/'mcel-universal-semantic-layout-lab.js'))
            result=page.evaluate('''({before,after})=>{
              const root=document.createElement('section');root.style.cssText='width:100%;min-width:0';
              document.querySelector('#task-manager-app').append(root);
              const handle=McelUniversalSemanticLayoutLab.mount(root,before,{lab:true});
              root.querySelector('mcel-row').click();
              const change=handle.update(after);
              return {ok:change.ok,selected:handle.collection.inspectedId,
                noEffects:change.effects.length===0,
                noOverflow:document.documentElement.scrollWidth<=innerWidth+2};
            }''',{'before':current,'after':next_plan})
            assert result=={'ok':True,'selected':None,'noEffects':True,'noOverflow':True}
        finally:browser.close()
