"""Patch 4: normal application path is opt-in, reversible, and fail-closed."""
from __future__ import annotations

from pathlib import Path

import pytest
from main_computer.mcel_slh_application import render_slh_page
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools import mcel_signal_integrated_acceptance as integrated
from tools import mcel_signal_differential_acceptance as diff


def test_app_hosts_do_not_enable_signals_without_explicit_per_app_opt_in(monkeypatch):
    monkeypatch.delenv('MCEL_SIGNAL_SCHEDULER_SLH',raising=False)
    monkeypatch.delenv('MCEL_SIGNAL_SCHEDULER_TASK_MANAGER',raising=False)
    for app,html in (
        ('slh',render_slh_page()),
        ('task-manager',render_semantic_page('task-manager',promoted=True)),
    ):
        assert html.count('signalSchedulerIntegration:true')==1,app
        assert html.count('signalSchedulerStartEnabled:false')==1,app
        assert 'signalSchedulerStartEnabled:true' not in html
    assert 'signalSchedulerIntegration:false' in render_semantic_page('file-manager',promoted=True)
    monkeypatch.setenv('MCEL_SIGNAL_SCHEDULER_SLH','certified')
    assert render_slh_page().count('signalSchedulerStartEnabled:true')==1
    assert render_semantic_page('task-manager',promoted=True).count('signalSchedulerStartEnabled:false')==1
    monkeypatch.setenv('MCEL_SIGNAL_SCHEDULER_TASK_MANAGER','certified')
    assert render_semantic_page('task-manager',promoted=True).count('signalSchedulerStartEnabled:true')==1
    monkeypatch.setenv('MCEL_SIGNAL_SCHEDULER_SLH','CERTIFIED')
    assert render_slh_page().count('signalSchedulerStartEnabled:false')==1
    monkeypatch.setenv('MCEL_SIGNAL_SCHEDULER_TASK_MANAGER','anything')
    assert render_semantic_page('task-manager',promoted=True).count('signalSchedulerStartEnabled:false')==1


def test_only_normal_app_mount_uses_integrated_gate_not_isolated_gate():
    for app in ('slh','task-manager'):
        reference=diff._html(app,False,candidate_mode='integrated')
        candidate=diff._html(app,True,candidate_mode='integrated')
        assert 'signalSchedulerStartEnabled:true' not in reference
        assert candidate.count('signalSchedulerStartEnabled:true')==1
        assert 'signalCandidateIsolatedTest:true' not in reference+candidate
        assert diff.GATE not in reference+candidate
    with pytest.raises(ValueError,match='INVALID_CANDIDATE_MODE'):
        diff._html('slh',True,candidate_mode='unsupported')


def test_integrated_acceptance_rejects_missing_skips_or_wrong_gate(monkeypatch):
    base={'status':'PASS_DIFFERENTIAL','failures':[],
        'candidate':{'mode':'integrated-opt-in','actualSkips':2},
        'boundaries':{'mainPathOptInExercised':True},
        'stages':[{'differenceCount':0}]}
    import copy
    def check(edit):
        value=copy.deepcopy(base);edit(value)
        monkeypatch.setattr(integrated,'run_differential',lambda *a,**kw:value)
        return integrated.accept(apps=('slh',),profile='smoke')
    assert check(lambda value:None)['status']=='PASS_MAIN_PATH_INTEGRATION'
    for edit in (
        lambda r:r['candidate'].update(actualSkips=0),
        lambda r:r['candidate'].update(mode='isolated-differential'),
        lambda r:r['boundaries'].update(mainPathOptInExercised=False),
        lambda r:r.update(status='FAIL_DIFFERENTIAL',failures=['MISMATCH']),
    ):
        assert check(edit)['status']=='BLOCKED'


@pytest.mark.parametrize('app',('slh','task-manager'))
def test_normal_runtime_switches_to_signals_and_back_without_changing_operations(app):
    pytest.importorskip('playwright')
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from tests.test_mcel_slh_lifecycle_disable_enablement_oracle import MOCK as SLH_MOCK
    from tools.mcel_task_manager_functional_parity_frob import FIXTURE_JS as TASK_MOCK
    key='__mcelSlh' if app=='slh' else '__mcelWorkbench'
    html=diff._html(app,False,candidate_mode='integrated')
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page(viewport={'width':1240,'height':900})
            page.evaluate(SLH_MOCK if app=='slh' else TASK_MOCK)
            page.set_content(html)
            page.wait_for_function(f'()=>!!window.{key}?.app')
            before=page.evaluate(f'()=>window.{key}.app.signalCandidateReport()')
            assert before['mode']=='disabled' and before['integrationEnabled'] is False
            bad=page.evaluate(f'()=>window.{key}.app.setSignalSchedulingMode("nonexistent")')
            assert bad['status']=='REFUSED'
            enabled=page.evaluate(f'()=>window.{key}.app.setSignalSchedulingMode("certified")')
            assert enabled['status']=='ENABLED'
            assert enabled['report']['mode']=='integrated-opt-in'
            if app=='slh':
                page.evaluate("()=>window.__mcelSlh.app.execute('intent:refresh-lifecycle')")
            else:
                page.wait_for_function("()=>window.__mcelWorkbench.app.receipts().some(r=>r.intentId==='intent:refresh')")
                page.evaluate("()=>window.__mcelWorkbench.app.execute('intent:list-schedules')")
            changed=page.evaluate(f'()=>window.{key}.app.signalCandidateReport()')
            assert changed['statistics']['skips']>0
            rolled=page.evaluate(f'()=>window.{key}.app.setSignalSchedulingMode("incumbent")')
            assert rolled['status']=='ROLLED_BACK'
            assert rolled['report']['mode']=='disabled'
            assert rolled['report']['integrationEnabled'] is False
            resumed=page.evaluate(f'()=>window.{key}.app.setSignalSchedulingMode("certified")')
            assert resumed['status']=='ENABLED'
            assert resumed['report']['statistics']['skips']==0
            page.evaluate(f'()=>window.{key}.app.unmount()')
            no=page.evaluate(f'()=>window.{key}.app.setSignalSchedulingMode("certified")')
            assert no['status']=='REFUSED' and no['reason']=='UNMOUNTED'
        finally:browser.close()


@pytest.mark.parametrize('app',('slh','task-manager'))
def test_actual_main_path_differential_has_real_skips(app):
    pytest.importorskip('playwright')
    result=diff.run_differential(app,candidate_mode='integrated')
    assert result['status']=='PASS_DIFFERENTIAL',result.get('failures')
    assert result['candidate']['mode']=='integrated-opt-in'
    assert result['candidate']['actualSkips']>0
    assert all(stage['differenceCount']==0 for stage in result['stages'])
    assert result['boundaries']['mainPathOptInExercised'] is True
    assert result['boundaries']['productionSchedulerChanged'] is False


def test_integrated_engine_fails_closed_without_replaying_renderer_exception():
    import json
    import shutil
    import subprocess
    import os
    node=shutil.which('node')
    if not node: pytest.skip('Node unavailable')
    runtime=Path(__file__).resolve().parents[1]/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js'
    script=r'''
const fs=require('fs'),vm=require('vm');const ctx={};
vm.runInNewContext(fs.readFileSync(process.env.MCEL_RUNTIME,'utf8'),ctx);
const create=ctx.McelSemanticOperationalRuntime.createSignalCandidate;
let renders=0,faults=[];
const candidate=create({integrated:true,selectiveInNotify:true,selectiveKinds:['display'],
  onSchedulerFault:reason=>faults.push(reason)});
candidate.beginNotify(new Map([['state:x',1]]),1,0);
candidate.observe('',()=>{renders++;}); // invalid scheduler key, BEFORE renderer
candidate.endNotify();
const handledFault=renders===1&&faults.length===1&&faults[0].includes('INVALID_CONSUMER');
const followup=create({integrated:true,selectiveInNotify:true,selectiveKinds:['display']});
followup.beginNotify(new Map([['state:x',1]]),1,0);
let emitted='';
try{followup.observe('display/item',()=>{renders++;throw Error('RENDER_FAILURE')})}
catch(e){emitted=e.message;}
followup.endNotify();
console.log(JSON.stringify({handledFault,renders,emitted,executionSkips:followup.report().statistics.skips}));
'''
    response=subprocess.run([node,'-e',script],capture_output=True,text=True,check=True,
        timeout=8,env={**os.environ,'MCEL_RUNTIME':str(runtime)})
    result=json.loads(response.stdout.strip())
    assert result=={'handledFault':True,'renders':2,
                    'emitted':'RENDER_FAILURE','executionSkips':0}
