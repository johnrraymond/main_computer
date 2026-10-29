"""Patch 14.3: real canonical semantics into one universal composition policy.

Independent domains are never named in the composer; fixture producers are real
contract shapes, not invented example applications.  No production promotion.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

import pytest

from main_computer.mcel_knowledge import SemanticCatalogError
from main_computer.mcel_knowledge.ir_bridge import compile_knowledge_ir, resolve_knowledge
from main_computer.mcel_knowledge.universal_layout import compose, extract_capabilities, PROFILE

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=ROOT/'tools/fixtures/mcel_computer_knowledge'
LAB=ROOT/'main_computer/web/applications/scripts/mcel-universal-semantic-layout-lab.js'
COMPOSER=ROOT/'main_computer/mcel_knowledge/universal_layout.py'


def resolve(observation, fixture, *, bindings=None):
    snapshot=json.loads((FIXTURES/fixture).read_text('utf-8'))
    bridge=compile_knowledge_ir('universal-profile-frob',observation_ids=[observation],bindings=bindings or {})
    return snapshot, bridge, resolve_knowledge(snapshot,bridge)


def task():
    return resolve('task-manager.processes','task_manager_snapshot.v1.json')


def file():
    return resolve('file-explorer.entries','expanded_computer_snapshot.v1.json')


def worker():
    return resolve('worker.market-workers','expanded_computer_snapshot.v1.json')


def test_single_profile_and_semantic_composition_without_hand_placed_regions():
    cases=[(task(),'task-manager.processes','monitor'),
           (file(),'file-explorer.entries','navigate'),
           (worker(),'worker.market-workers','monitor')]
    for (_,_,view),ob,purpose in cases:
        plan=compose(view,ob,purpose=purpose)
        assert plan['profile']==PROFILE and plan['readOnly'] and not plan['productionPromotion']
        assert plan['tree']['schema']=='mcel.layout-tree.v1'
        assert plan['collection']['spec']['schema']=='mcel.semantic-collection-spec.v2'
        assert plan['collection']['rows'] and plan['collection']['noOperations']
        assert plan['regions'] and set(r['recipe'] for r in plan['regions']).issubset(plan['recipesUsed'])
        assert [x['id'] for x in plan['regions']]==[x['id'] for x in plan['tree']['root']['children']]
        assert 'semantic-comparable-entities' in plan['decision']['regions']
        assert 'semantic-selected-details' in plan['decision']['regions']
        # Exact canonical records survive presentation decisions.
        assert all('mcelCanonicalRecord' in x for x in plan['collection']['rows'])
    # Same universal policy; purpose + semantic type, not producer name, changes composition.
    assert compose(task()[2],'task-manager.processes',purpose='monitor')['decision']['profile']== \
           compose(file()[2],'file-explorer.entries',purpose='navigate')['decision']['profile']


def test_identities_units_availability_provenance_and_uncertain_relationships_survive():
    _,_,v=task();p=compose(v,'task-manager.processes',purpose='monitor')
    assert p['collection']['rows'][0]['resident_memoryAvailability']=='unavailable'
    assert 'resident_memory' not in p['collection']['rows'][0]
    assert p['collection']['rows'][1]['resident_memory']==0
    assert p['collection']['rows'][1]['resident_memoryAvailability']=='present'
    assert p['collection']['spec']['identity']=='mcelIdentity'
    assert not p['collection']['spec'].get('reconciliation')  # no universal continuity claims
    assert p['capabilities']['observationCompleteness'] in ('unknown','bounded','complete')
    _,_,v=worker();p=compose(v,'worker.market-workers',purpose='monitor')
    assert p['capabilities']['relationshipCertainty']==['candidate-only']
    assert not p['capabilities']['capabilities']['authorizedActions']
    assert p['decision']['profile']==PROFILE


def test_same_semantics_across_content_churn_and_producer_field_rename():
    snap, bridge, view=task()
    orig=compose(view,'task-manager.processes',purpose='monitor')
    changed=deepcopy(snap)
    changed['processes'][0]['cmdline']='python ' + 'massive-argument '*80
    changed['processes'][1]['memory_rss']=2**30
    view2=resolve_knowledge(changed,bridge)
    updated=compose(view2,'task-manager.processes',purpose='monitor',prior=orig['commit'])
    assert updated['commit']['status']=='retained'
    assert orig['decision']==updated['decision']
    assert orig['collection']['rows']!=updated['collection']['rows']
    renamed=deepcopy(snap)
    for row in renamed['processes']:
        row['process_number']=row.pop('pid')
        row['started']=row.pop('created_at')
    overlay={'task-manager.processes':{'properties':{'pid':'process_number','created_at':'started'}}}
    bridge2=compile_knowledge_ir('universal-profile-frob',observation_ids=['task-manager.processes'],bindings=overlay)
    assert bridge2['semanticFingerprint']==bridge['semanticFingerprint']
    assert bridge2['sourceBindingFingerprint']!=bridge['sourceBindingFingerprint']
    remapped=compose(resolve_knowledge(renamed,bridge2),'task-manager.processes',purpose='monitor')
    assert orig['decision']==remapped['decision']
    assert [r['mcelIdentity'] for r in orig['collection']['rows']]==[r['mcelIdentity'] for r in remapped['collection']['rows']]


def test_purpose_is_input_not_new_profile_or_per_record_search():
    _,_,v=task()
    monitor=compose(v,'task-manager.processes',purpose='monitor')
    compare=compose(v,'task-manager.processes',purpose='compare')
    assert monitor['decision']['name']!=compare['decision']['name']
    assert monitor['profile']==compare['profile']==PROFILE
    assert 'semantic-observed-status' in monitor['decision']['regions']
    assert 'semantic-observed-status' not in compare['decision']['regions']
    with pytest.raises(SemanticCatalogError):compose(v,'task-manager.processes',purpose='task-manager')


def test_no_application_branching_no_fabricated_hierarchy_or_authority():
    src=COMPOSER.read_text('utf-8')
    assert not re.search(r'(?i)if\s+(?:app|observation|source)\s*==\s*[\'\"](?:task|file|worker)',src)
    assert 'task-manager.processes' not in src and 'file-explorer.entries' not in src
    _,_,fv=file();p=compose(fv,'file-explorer.entries',purpose='navigate')
    # A textual path alone is NOT a verified parent-child relationship.
    assert p['capabilities']['capabilities']['hierarchy'] is False
    assert 'available-intents' not in p['decision']['regions']


def test_profile_is_inert_until_explicit_lab_activation():
    src=LAB.read_text('utf-8')
    assert 'EXPLICIT_OPT_IN_REQUIRED' in src
    assert 'McelSemanticCollectionWorkspace.mount' in src
    assert 'mountCanonicalLayoutTree' in src
    assert 'fetch(' not in src and 'XMLHttpRequest' not in src
    assert 'productionPromotion' in src


def test_chromium_real_existing_collection_and_tree_composition(tmp_path):
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch13_live_adoption_frob import _chrome
    from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture
    _,_,view=task()
    original=compose(view,'task-manager.processes',purpose='monitor')
    mutated=json.loads((FIXTURES/'task_manager_snapshot.v1.json').read_text())
    mutated['processes'][0]['cmdline']='python '+'long-argument '*30
    changed=compose(resolve_knowledge(mutated,compile_knowledge_ir('universal-profile-frob',observation_ids=['task-manager.processes'])),
                    'task-manager.processes',purpose='monitor',prior=original['commit'])
    evidence=[]
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            for width in (320,390,560,1100):
                page=browser.new_page(viewport={'width':width,'height':840},reduced_motion='reduce')
                errors=[];page.on('pageerror',lambda err:errors.append(str(err)))
                try:
                    page.set_content(html_fixture(),wait_until='load')
                    page.add_script_tag(path=str(ROOT/'main_computer/web/applications/scripts/mcel-layout-continuity.js'))
                    page.add_script_tag(path=str(LAB))
                    initial=page.evaluate('''plan=>{
                      const host=document.createElement('section');host.id='universal-frob-lab';
                      host.style.cssText='width:100%;max-width:100%;min-width:0';
                      document.querySelector('#task-manager-app').append(host);
                      window.__universal=McelUniversalSemanticLayoutLab.mount(host,plan,{lab:true});
                      const row=document.querySelector('#universal-frob-lab mcel-row');
                      if(row){row.click();row.focus();}
                      window.__beforeRow=row;
                      const work=document.querySelector('#mcel-universal-workspace');
                      return {rows:host.querySelectorAll('mcel-row').length,
                        regions:[...work.children].map(e=>e.id),
                        inspector:host.querySelector('#semantic-selected-details')?.textContent||'',
                        focused:document.activeElement===row,
                        state:window.__universal.state, profile:window.__universal.profile,
                        width:work.getBoundingClientRect().width,
                        overflow:document.documentElement.scrollWidth>innerWidth+2};
                    }''',original)
                    assert initial['rows']==2 and initial['profile']==PROFILE,initial
                    assert len(initial['regions'])==3, initial
                    assert 'mcelCanonicalRecord' in initial['inspector'],initial
                    assert not initial['overflow'],initial
                    update=page.evaluate('''plan=>{
                      const result=window.__universal.update(plan);
                      const host=document.querySelector('#universal-frob-lab');
                      return {result, sameNode:window.__beforeRow===host.querySelector('mcel-row'),
                        focused:document.activeElement===window.__beforeRow,
                        rows:host.querySelectorAll('mcel-row').length,
                        overflow:document.documentElement.scrollWidth>innerWidth+2};
                    }''',changed)
                    assert update['result']['ok'],update
                    # No verified-generation evidence was supplied by this fixture.
                    # The generic consumer correctly refuses to claim DOM retention.
                    assert update['rows']==2 and not update['overflow'],update
                    assert not errors,errors
                    evidence.append({'width':width,'rows':initial['rows'],'regions':initial['regions'],
                       'browserErrors':errors,'overflow':initial['overflow'],'updateOk':update['result']['ok'],
                       'continuityClaimed':update['result'].get('collection',{}).get('retained',0)>0,
                       'exactInspector':True})
                finally:page.close()
        finally:browser.close()
    path=tmp_path/'browser.json';path.write_text(json.dumps(evidence,indent=2))
    assert len(evidence)==4 and all(e['updateOk'] for e in evidence)

@pytest.mark.parametrize('observation,fixture,purpose', [
    ('file-explorer.entries','expanded_computer_snapshot.v1.json','navigate'),
    ('worker.market-workers','expanded_computer_snapshot.v1.json','monitor'),
])
def test_chromium_independent_domain_without_composer_changes(observation, fixture, purpose):
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch13_live_adoption_frob import _chrome
    from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture
    _,_,view=resolve(observation,fixture)
    plan=compose(view,observation,purpose=purpose)
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            page=browser.new_page(viewport={'width':560,'height':840},reduced_motion='reduce')
            errors=[];page.on('pageerror',lambda err:errors.append(str(err)))
            page.set_content(html_fixture(),wait_until='load')
            page.add_script_tag(path=str(ROOT/'main_computer/web/applications/scripts/mcel-layout-continuity.js'))
            page.add_script_tag(path=str(LAB))
            rendered=page.evaluate('''plan=>{
              const host=document.createElement('section');host.id='universal-frob-lab';
              host.style.cssText='width:100%;max-width:100%;min-width:0';
              document.querySelector('#task-manager-app').append(host);
              const live=McelUniversalSemanticLayoutLab.mount(host,plan,{lab:true});
              const row=host.querySelector('mcel-row');if(row)row.click();
              return {profile:live.profile,rows:host.querySelectorAll('mcel-row').length,
                regions:host.querySelectorAll('.mcel-universal-composition__region').length,
                inspector:host.querySelector('.mcel-semantic-collection__inspector')?.textContent||'',
                noOverflow:document.documentElement.scrollWidth<=innerWidth+2};
            }''',plan)
            assert rendered['profile']==PROFILE and rendered['rows']==len(plan['collection']['rows']),rendered
            assert rendered['regions']==len(plan['regions']) and rendered['noOverflow'],rendered
            assert 'mcelCanonicalRecord' in rendered['inspector'] and not errors,rendered
        finally:browser.close()


def test_semantic_facts_are_bounded_and_not_population_claims():
    _,_,v=task();p=compose(v,'task-manager.processes',purpose='monitor')
    facts=p['summaryFacts']
    assert facts and all(f['denominator']==2 and f['scope']=='observed-subset-only' for f in facts)
    assert any(f['kind']=='measurement-availability' and f['states'].get('unavailable')==1 for f in facts)
    assert not p['capabilities']['capabilities']['authorizedActions']
