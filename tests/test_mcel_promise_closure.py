"""Patch 06: truthful cross-wave acceptance + generic identity reconciliation."""
from __future__ import annotations
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from tools.mcel_promise_closure_frob import (
    SCHEMA, WAVES, NEW_HOST_CHECKS, consolidate, evidence, read_report,
)

ROOT=Path(__file__).resolve().parents[1]


def test_missing_reports_and_sources_never_become_success(tmp_path):
    result=consolidate(root=tmp_path)
    assert result['schema']==SCHEMA
    assert result['verdict']=='INCOMPLETE'
    assert result['gates']['twoRealHostedApplications']=='INCOMPLETE'
    assert result['gates']['comparativeAuthoringEconomy']=='UNVERIFIED'
    assert result['gates']['independentVisualReview']=='NOT_ACCEPTED'
    assert result['gates']['consequentialEffects']=='NOT_ADMITTED'


def test_invalid_schema_never_becomes_evidence(tmp_path):
    p=tmp_path/'report.json';p.write_text(json.dumps({'schema':'other','offline':'PASS','hosted':'PASS'}))
    assert read_report(p,WAVES['file-explorer'][1])['status']=='FAIL'


def test_old_wave5_hosted_pass_lacks_real_receipt_evidence():
    checks={'http200':True,'real-entries-parity':True,'only-read-receipts':True}
    data={'schema':WAVES['file-explorer'][1],'offline':'PASS','hosted':'PASS',
          'source':{'status':'PASS'},'browserCases':[
          {'profile':p,'width':w,'status':'PASS','checks':{'ok':True}}
          for p in ('operational','analytical') for w in (320,390,560,1100,1272)],
          'hostCases':[{'profile':p,'status':'PASS','checks':checks} for p in ('operational','analytical')]}
    result=evidence('file-explorer',{'status':'AVAILABLE','path':'fixture','sha256':'test','data':data})
    assert result['status']=='INCOMPLETE'
    assert result['hosted']=='UNVERIFIED_OR_FAIL'
    assert 'rerun Wave 5' in result['reason']
    for h in data['hostCases']:
        h['checks'].update({k:True for k in NEW_HOST_CHECKS})
    assert evidence('file-explorer',{'status':'AVAILABLE','path':'fixture','sha256':'test','data':data})['status']=='PASS'


def test_identity_change_with_same_display_value_updates_selection_and_row_identity():
    js=(ROOT/'main_computer/web/applications/scripts/mcel-semantic-application-shell.js').read_text()
    with sync_playwright() as p:
        import shutil
        executable=shutil.which('chromium') or shutil.which('chromium-browser')
        browser=p.chromium.launch(headless=True,**({'executable_path':executable,'args':['--no-sandbox','--disable-dev-shm-usage']} if executable else {}))
        try:
            page=browser.new_page()
            page.set_content('<html><body><main id="host"></main></body></html>')
            page.add_script_tag(content=js)
            result=page.evaluate('''async () => {
              const ir={application:{title:'Generic Collection'},intents:[],surfaces:[
                {semanticSurface:{controls:[{id:'open',role:'capability-action'}],applicationShell:{
                  schema:'mcel.semantic-application-shell.v1',layout:'sidebar-workspace',id:'generic',
                  regions:[{id:'region:summary',zone:'sidebar',kind:'summary',title:'Overview',items:[{id:'count',label:'Count',path:'count'}]},
                    {id:'region:items',zone:'main',kind:'collection',title:'Items',path:'records',columns:['label'],rowKey:'id',selectControl:'open'}]
                }}}]};
              const selected=[];const effectRuntime={available:()=>true,dispatch:async()=>({ok:true}),
                subscribe:cb=>{cb({phase:'idle',pending:false,receipt:null});return ()=>{};}};
              const host=document.getElementById('host');
              const app=McelSemanticApplicationShell.mount(host,ir,{effectRuntime,
                onCollectionSelect:(_,item)=>{selected.push(item.id);return true;}});
              app.update({count:1,records:[{id:'old',label:'same'}]});
              const q=()=>host.querySelector('[data-mcel-region="region:items"] tbody tr');
              const old=q();old.querySelector('button').click();
              app.update({count:1,records:[{id:'new',label:'same'}]});
              const newRow=q();newRow.querySelector('button').click();
              app.update({count:1,records:[{id:'new',label:'changed'}]});
              const retained=q()===newRow;
              newRow.querySelector('button').focus();
              app.update({count:1,records:[{id:'new',label:'twice'}]});
              return {selected,oldKey:old.dataset.mcelRowKey,newKey:newRow.dataset.mcelRowKey,
                differentNode:old!==newRow,retained,focusRetained:document.activeElement===newRow.querySelector('button')};
            }''')
            assert result=={'selected':['old','new'],'oldKey':'old','newKey':'new',
                            'differentNode':True,'retained':True,'focusRetained':True}
        finally:browser.close()
