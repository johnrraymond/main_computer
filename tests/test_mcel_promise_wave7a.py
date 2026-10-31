"""Patch 07A contract: task-aware planning is opt-in, semantic, stable, and honest."""
from pathlib import Path
import json
import shutil
import pytest
from playwright.sync_api import sync_playwright
from tools.mcel_promise_wave7a_frob import run

ROOT=Path(__file__).resolve().parents[1]
SHELL=ROOT/'main_computer/web/applications/scripts/mcel-semantic-application-shell.js'
CSS=ROOT/'main_computer/web/applications/styles/mcel-semantic-application-shell.css'


def test_wave7a_browser_two_applications(tmp_path):
    assert run(tmp_path)==0
    report=json.loads((tmp_path/'report.json').read_text())
    assert len(report['cases'])==20
    assert all(c['status']=='PASS' for c in report['cases'])
    assert report['hosted']=='NOT_REQUESTED'
    assert report['sourceChanges'] is False and report['authorityChanges'] is False


def test_candidate_is_explicit_and_app_neutral():
    js=SHELL.read_text()
    assert 'options.planner || "legacy"' in js
    assert 'taskAwarePlan(spec' in js
    assert 'new ResizeObserver' in js
    assert 'data-mcel-planner="task-aware"' in CSS.read_text()
    assert 'file-explorer-candidate' not in js and 'task-manager-notebook' not in js
    task=(ROOT/'main_computer/web/applications/scripts/mcel-task-manager-notebook-bridge.js').read_text()
    file=(ROOT/'main_computer/web/applications/scripts/mcel-file-explorer-candidate-bridge.js').read_text()
    assert 'mcel_planner' in task and 'planner=\"legacy\"' in file


def test_candidate_latches_and_state_matches_exact_rendered_plan():
    js=SHELL.read_text()
    with sync_playwright() as p:
        exe=shutil.which('chromium') or shutil.which('chromium-browser')
        kw={'executable_path':exe,'args':['--no-sandbox','--disable-dev-shm-usage']} if exe else {}
        b=p.chromium.launch(headless=True,**kw)
        try:
            page=b.new_page(viewport={'width':900,'height':800})
            page.set_content('<div id="host" style="width:900px;height:700px"></div>')
            page.add_style_tag(content=CSS.read_text())
            page.add_script_tag(content=js)
            result=page.evaluate('''() => {
              const ir={application:{title:'Unrelated Field Log'},intents:[],surfaces:[
                {semanticSurface:{controls:[],applicationShell:{schema:'mcel.semantic-application-shell.v1',
                  id:'field-log',layout:'sidebar-workspace',regions:[
                  {id:'region:records',kind:'collection',zone:'main',title:'Records',path:'rows',
                   columns:['label','value'],rowKey:'id'},
                  {id:'region:nav',kind:'collection',zone:'sidebar',title:'Browse',path:'nav',
                   columns:['name'],rowKey:'id'}]}}}]};
              const host=document.getElementById('host');
              const app=McelSemanticApplicationShell.mount(host,ir,{planner:'task-aware'});
              const card=host.querySelector('[data-mcel-region="region:records"]');
              const baseline=app.getState().design;
              const pristine=(baseline.contentLatched.length===0 && card.dataset.mcelRole==='workspace'
                && card.dataset.mcelProminence==='primary');
              app.update({rows:[],nav:[]});
              const notYetLatched=app.getState().design.contentLatched.length===0;
              const rows=Array.from({length:181},(_,i)=>({id:'key-'+i,label:'Label '+i,value:'short'}));
              app.update({rows,nav:[{id:'one',name:'A'}]});
              const selected=card.querySelector('tbody tr:nth-child(4)');
              selected.tabIndex=0;selected.focus();
              const first=app.getState().design;
              const firstRegion=first.regions.find(x=>x.id==='region:records');
              const contentCorrect=firstRegion.extent==='expanded' && firstRegion.density==='compact';
              const nameStable=firstRegion.role==='workspace' && firstRegion.prominence==='primary';
              const attrsCorrect=card.dataset.mcelPlanRole===firstRegion.planRole &&
                card.dataset.mcelPlanDensity===firstRegion.density;
              const before=firstRegion;
              app.update({rows:[{id:'new-1',label:'Much longer '+('X'.repeat(130)),value:'changed'}],nav:[]});
              const latched=app.getState().design.regions.find(x=>x.id==='region:records')===before;
              const nav=host.querySelector('[data-mcel-region="region:nav"]');
              const originalNode=nav;
              const switched=app.setPresentation('analytical');
              const live=app.getState().design;
              const after=live.regions.find(x=>x.id==='region:records');
              const livePlan=live.presentation==='analytical' &&
                after.planRole===card.dataset.mcelPlanRole && after.density===card.dataset.mcelPlanDensity;
              const preservedNodes=card===host.querySelector('[data-mcel-region="region:records"]') &&
                originalNode===host.querySelector('[data-mcel-region="region:nav"]');
              const semanticsUnchanged=card.dataset.mcelRole==='workspace' &&
                card.dataset.mcelProminence==='primary';
              const invalid=app.setPresentation('unknown');
              const invalidPreserved=invalid.ok===false && app.getState().design===live;
              app.destroy();
              return {pristine,notYetLatched,contentCorrect,nameStable,attrsCorrect,
                latched,switched:switched.ok,livePlan,preservedNodes,semanticsUnchanged,
                invalidPreserved,unmounted:host.childElementCount===0};
            }''')
            assert all(result.values()),result
        finally:b.close()


def test_unknown_planner_refuses_without_changing_ir():
    with sync_playwright() as p:
        exe=shutil.which('chromium') or shutil.which('chromium-browser')
        kw={'executable_path':exe,'args':['--no-sandbox','--disable-dev-shm-usage']} if exe else {}
        b=p.chromium.launch(headless=True,**kw)
        try:
            page=b.new_page();page.set_content('<main id="host"></main>')
            page.add_script_tag(content=SHELL.read_text())
            result=page.evaluate('''() => {
              const ir={application:{title:'Test'},intents:[],surfaces:[{semanticSurface:{
                applicationShell:{schema:'mcel.semantic-application-shell.v1',id:'audit',
                  layout:'sidebar-workspace',regions:[
                    {id:'region:nav',kind:'collection',zone:'sidebar',title:'Nav',path:'nav',columns:['id']},
                    {id:'region:main',kind:'collection',zone:'main',title:'Main',path:'rows',columns:['id']}
                  ]}}}]};
              const before=JSON.stringify(ir);
              try{McelSemanticApplicationShell.mount(document.getElementById('host'),ir,{planner:'invented'});
                return false;
              }catch(error){return /UNKNOWN_PLANNER/.test(error.message) && JSON.stringify(ir)===before;}
            }''')
            assert result is True
        finally:b.close()
