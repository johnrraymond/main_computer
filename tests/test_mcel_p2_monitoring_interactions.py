"""Generated Task Manager four-view read-only behavior; authoritative P1 test remains separate."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json

import pytest
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_knowledge.semantic_composition import synthesize_composition, verify_composition
from main_computer.mcel_semantic_generated_host import compile_generated_candidate, render_generated_page
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'tools/fixtures/mcel_p1_task_monitor/application.js'


def test_four_views_compile_without_authored_layout_or_authority():
    ir, plan = compile_generated_candidate()
    verify_composition(ir, plan)
    nodes = ir['surfaces'][0]['nodes']
    assert not ir['layouts'] and not ir['surfaces'][0].get('interfaceIntent')
    assert {i['id'] for i in ir['intents']} == {'intent:refresh','intent:refresh-all',
        'intent:refresh-connections','intent:refresh-hardware','intent:choose-process','intent:switch-view'}
    assert {c['risk'] for c in ir['capabilities']} == {'external-read'}
    assert {e['risk'] for e in ir['effects']} == {'external-read'}
    assert len([n for n in nodes if n['nodeKind']=='collection']) == 3
    assert len([n for n in nodes if n['nodeKind']=='form']) == 4
    assert len([n for n in nodes if n.get('visibleWhen')]) == 9
    assert len([n for n in nodes if n['nodeKind']=='control']) == 1
    for n in nodes:
        if n.get('visibleWhen'):
            assert n['visibleWhen']['state'] == {'ref':'state:view'}
    assert not any(word in SOURCE.read_text() for word in ('bindHtml(', 'layout.define(', 'layoutGrammar', 'parentId', 'selector:'))


def test_mode_binding_mutation_fails_closed(tmp_path):
    ir,_ = compile_generated_candidate()
    invalid = deepcopy(ir)
    next(n for n in invalid['surfaces'][0]['nodes'] if n.get('visibleWhen'))['visibleWhen']['equals']='not-a-view'
    with pytest.raises(ValueError,match='view transition'):
        synthesize_composition(invalid)
    invalid=deepcopy(ir)
    invalid['intents']=[i for i in invalid['intents'] if i['id']!='intent:switch-view']
    with pytest.raises(ValueError,match='view transition'):
        synthesize_composition(invalid)
    changed = SOURCE.read_text().replace('when:inView("server-processes")', 'when:{state:processes,equals:"server-processes"}',1)
    p=tmp_path/'application.js';p.write_text(changed)
    compiled=compile_dsl_application(p)
    assert not compiled.valid
    assert 'MCEL_DSL_VIEW_CONDITION_INVALID' in str(compiled.diagnostics)


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b,_=launch_semantic_browser(pw)
        try:yield b
        finally:b.close()


MOCK_JS='''() => {
  window.__requests=[];window.__round=0;window.__reject=false;
  window.fetch=async (url,opts)=>{
    const body=JSON.parse(opts.body);window.__requests.push({url,body});
    if(window.__reject)return {ok:true,status:200,json:async()=>({processes:[{pid:'bad',name:'forged'}]})};
    if(body.include_connections){
      return {ok:true,status:200,json:async()=>({connections:[
        {pid:901,process_name:'Network agent',status:'ESTABLISHED',local:'127.0.0.1:1234',remote:'127.0.0.1:8765'}],processes:[]})};
    }
    if(body.include_all){
      return {ok:true,status:200,json:async()=>({processes:[
        {pid:1000,name:'Browser',created_at:'2026-11-01T01:20:00Z',status:'running',memory_human:'1 MB'},
        {pid:1001,name:'Shell',created_at:'2026-11-01T01:21:00Z',status:'idle',memory_human:'2 MB'}]})};
    }
    if(window.__mcelP1?.app?.readState('state:view')==='cpu-gpu'){
      return {ok:true,status:200,json:async()=>({hardware:{summary:'CPU and GPU telemetry',updated_at:'2026-11-01T01:30:00Z',
        cpu:{available:true,overall_percent:21,per_core:[{index:0,label:'Core 0',percent:12}]},
        gpu:{available:true,overall_percent:45,devices:[{index:0,name:'GPU 0',utilization_percent:45,memory_used_mb:100,
          memory_total_mb:500,temperature_c:51}]}}})};
    }
    window.__round++;
    const numbers=Array.from({length:50},(_,i)=>i+100);
    if(window.__round%2===0)numbers.unshift(numbers.pop());
    return {ok:true,status:200,json:async()=>({processes:numbers.map(pid=>({
      pid,name:'Worker '+pid,status:'running',memory_human:'5 MB',memory_rss:5242880,
      command_preview:'python --worker='+pid,created_at:'2026-11-01T01:00:00Z'}))})};
  };
}'''


def prepare(page):
    page.evaluate(MOCK_JS)
    page.set_content(render_generated_page())
    assert page.locator('#mcel-workbench-status').inner_text()=='P2 generated monitor ready'


@pytest.mark.parametrize('width',[320,390,560,1100,1272])
def test_four_monitoring_views_in_real_chromium(browser,width):
    page=browser.new_page(viewport={'width':width,'height':820})
    try:
        prepare(page)
        assert page.locator('[data-mcel-mode-tabs] button').count()==4
        assert page.locator('[data-mcel-node="surface-node:semantic-task-monitor-generated.monitor-query"]').is_visible()
        assert not page.locator('[data-mcel-node="surface-node:semantic-task-monitor-generated.all-query"]').is_visible()
        assert page.evaluate('window.__requests.length')==0
        page.locator('form[data-intent="intent:refresh"] button[type=submit]').click()
        assert page.locator('[data-record-key="140:2026-11-01T01:00:00Z"]').is_visible()
        assert page.locator('form[data-intent="intent:refresh"] [name="query"]').is_visible()
        assert page.evaluate('window.__requests[0].body.include_all') is False
        row=page.locator('[data-record-key="140:2026-11-01T01:00:00Z"]')
        row.locator('summary').click()
        assert row.locator('[data-mcel-inline-details]').get_attribute('open') is not None
        assert row.get_by_text('Created At').count() == 1
        page.evaluate('window.__keptRow = document.querySelector(`[data-record-key="140:2026-11-01T01:00:00Z"]`)')
        page.locator('form[data-intent="intent:refresh"] button[type=submit]').click()
        page.wait_for_function('window.__round===2')
        assert page.evaluate('window.__keptRow===document.querySelector(`[data-record-key="140:2026-11-01T01:00:00Z"]`)')
        assert row.locator('[data-mcel-inline-details]').get_attribute('open') is not None
        row.locator('[data-select-key]').click()
        assert page.locator('[data-mcel-selected-details="140:2026-11-01T01:00:00Z"]').count()==1
        requests=page.evaluate('window.__requests.length')
        page.locator('[data-mcel-mode-tabs] [data-value="all-processes"]').click()
        assert page.evaluate('window.__mcelP1.app.readState("state:view")')=='all-processes'
        assert page.evaluate('window.__requests.length')==requests
        assert not page.locator('form[data-intent="intent:refresh"]').is_visible()
        page.locator('form[data-intent="intent:refresh-all"] button[type=submit]').click()
        assert page.locator('[data-record-key="1000:2026-11-01T01:20:00Z"]').is_visible()
        assert page.evaluate('window.__requests.at(-1).body.include_all') is True
        page.locator('[data-mcel-mode-tabs] [data-value="connections"]').click()
        page.locator('form[data-intent="intent:refresh-connections"] button[type=submit]').click()
        assert page.locator('[data-record-field="remote"]').filter(has_text='8765').is_visible()
        assert page.evaluate('window.__requests.at(-1).body.include_connections') is True
        page.locator('[data-mcel-mode-tabs] [data-value="cpu-gpu"]').click()
        assert not page.locator('[data-mcel-purpose="workspace"]').is_visible()
        assert page.locator('[data-mcel-purpose="results"]').is_visible()
        page.locator('form[data-intent="intent:refresh-hardware"] button[type=submit]').click()
        assert page.locator('[data-mcel-record-summary]').inner_text()=='CPU and GPU telemetry'
        # A typed hardware record must be visibly readable: not only correct
        # in renderer state or present as an opaque JSON object.
        assert page.locator('[data-mcel-metric="cpuPercent"] [data-mcel-metric-value]').inner_text()=='21%'
        assert page.locator('[data-mcel-metric="gpuPercent"] [data-mcel-metric-value]').inner_text()=='45%'
        assert page.locator('[data-mcel-measurement-detail="gpuDevices"]').count()==1
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
        assert page.locator('[data-mcel-auto-refresh]').count()==0
        assert page.locator('#mcel-workbench[data-mcel-layout-authority="inferred"]').count()==1
        if width == 1100:
            before=page.evaluate('window.__mcelP1.composition.fingerprint')
            page.set_viewport_size({'width':390,'height':820})
            page.wait_for_function('document.documentElement.scrollWidth <= innerWidth+2')
            assert page.evaluate('window.__mcelP1.composition.fingerprint')==before
    finally:page.close()


def test_authentic_source_refusal_and_runtime_identity_not_pid_only(browser):
    page=browser.new_page()
    try:
        prepare(page)
        page.locator('form[data-intent="intent:refresh"] button[type=submit]').click()
        page.locator('[data-record-key]').first.wait_for()
        pid=page.evaluate('window.__mcelP1.app.readState("state:processes")[0].processKey')
        assert pid.endswith(':2026-11-01T01:00:00Z')
        assert page.evaluate('''async () => await McelSemanticReadPorts.create({appId:'semantic-task-monitor-generated'}).authorize({
          appId:'semantic-task-monitor-generated', intentId:'intent:kill-process',
          effectId:'effect:kill-process.request', capabilityId:'capability:task-control',
          operation:'kill',runtimeMethod:'killObservedProcess',risk:'consequential',arguments:{selected:'140'}})''') is False
        assert page.evaluate('window.__requests.every(r=>r.url==="/api/applications/task/overview")')
        # Invalid backend data must yield failure receipt and not replace a real state.
        page.evaluate('window.__reject=true')
        before=page.evaluate('window.__mcelP1.app.readState("state:processes").length')
        page.locator('form[data-intent="intent:refresh"] button[type=submit]').click()
        page.locator('li[data-disposition="failed"]').first.wait_for()
        assert page.evaluate('window.__mcelP1.app.readState("state:processes").length')==before
    finally:page.close()


def test_genuine_task_service_snapshot_through_controlled_browser_port(browser):
    """Actual TaskManagerService and OS PID/generation; browser transport mocked.

    This does NOT certify a Windows-host or production HTTP endpoint.
    """
    import os
    from main_computer.task_manager import TaskManagerService, psutil
    if psutil is None:
        pytest.skip('real process producer unavailable')
    # The process endpoint intentionally caps responses at 100 rows. On Windows,
    # Win32_Process records are sorted before that cap, so the pytest worker's
    # PID need not be present in an otherwise valid, authentic full snapshot.
    # Preserve the unfiltered source check and use the real PID query when the
    # current process falls outside that bounded window.
    authentic=TaskManagerService(ROOT).snapshot(include_all=True,include_connections=True,limit=100)
    assert authentic['processes'], 'Task Manager returned no operating-system processes'
    query=''
    if not any(row['pid']==os.getpid() for row in authentic['processes']):
        query=str(os.getpid())
        authentic=TaskManagerService(ROOT).snapshot(query=query,include_all=True,include_connections=True,limit=100)
    assert os.getpid() in [r['pid'] for r in authentic['processes']], \
        'OS process producer omitted the current PID even with an explicit PID query'
    page=browser.new_page(viewport={'width':1100,'height':800})
    try:
        page.evaluate('''(snapshot) => {
          window.__requests=[];
          window.fetch=async (url,opts)=>{
            const body=JSON.parse(opts.body);window.__requests.push({url,body});
            return {ok:true,status:200,json:async()=>snapshot};
          };
        }''',authentic)
        page.set_content(render_generated_page())
        assert page.locator('#mcel-workbench-status').inner_text()=='P2 generated monitor ready'
        page.locator('[data-mcel-mode-tabs] [data-value="all-processes"]').click()
        page.locator('form[data-intent="intent:refresh-all"] [name="query"]').fill(query)
        page.locator('form[data-intent="intent:refresh-all"] button[type=submit]').click()
        page.wait_for_function('Array.isArray(window.__mcelP1?.app.readState("state:all-processes"))')
        assert page.evaluate('window.__requests[0].body.query')==query
        rows=page.evaluate('window.__mcelP1.app.readState("state:all-processes")')
        assert rows and os.getpid() in [r['pid'] for r in rows]
        current=next(r for r in rows if r['pid']==os.getpid())
        assert current['createdAt'] and current['processKey']==str(os.getpid())+':'+current['createdAt']
        assert current['isCurrentProcess'] is True
        assert page.evaluate('window.__requests[0].body.include_all') is True
        page.locator('[data-mcel-mode-tabs] [data-value="cpu-gpu"]').click()
        page.locator('form[data-intent="intent:refresh-hardware"] button[type=submit]').click()
        page.wait_for_function('!!window.__mcelP1.app.readState("state:hardware")')
        hardware=page.evaluate('window.__mcelP1.app.readState("state:hardware")')
        assert isinstance(hardware['summary'],str) and isinstance(hardware['cpuCores'],list)
        assert page.locator('[data-mcel-record-summary]').is_visible()
    finally:page.close()


def test_keyed_refresh_preserves_scroll_anchor_expansion_and_focus(browser):
    page=browser.new_page(viewport={'width':1100,'height':730})
    try:
        prepare(page)
        page.locator('form[data-intent="intent:refresh"] button[type=submit]').click()
        page.locator('[data-record-key="110:2026-11-01T01:00:00Z"]').wait_for()
        before=page.evaluate('''() => {
          const holder=document.querySelector('[data-collection="surface-node:semantic-task-monitor-generated.process-list"]');
          holder.scrollTop=450;
          const box=holder.getBoundingClientRect();
          const row=[...holder.querySelectorAll('[data-record-key]')].find(r=>r.getBoundingClientRect().bottom>box.top);
          return {scroll:holder.scrollTop,key:row.dataset.recordKey,top:row.getBoundingClientRect().top};
        }''')
        assert before['scroll']>0
        # The view is updated through the declared capability, not by DOM test mutation.
        page.evaluate('''async() => await window.__mcelP1.app.execute('intent:refresh',{query:''})''')
        after=page.evaluate('''(key) => {
          const holder=document.querySelector('[data-collection="surface-node:semantic-task-monitor-generated.process-list"]');
          return {scroll:holder.scrollTop,top:[...holder.querySelectorAll('[data-record-key]')]
             .find(r=>r.dataset.recordKey===key).getBoundingClientRect().top};
        }''',before['key'])
        assert after['scroll']>0
        assert abs(after['top']-before['top'])<3
        row=page.locator(f'[data-record-key="{before["key"]}"]')
        row.locator('summary').click()
        row.locator('[data-select-key]').click()
        page.evaluate('''async() => await window.__mcelP1.app.execute('intent:refresh',{query:''})''')
        assert row.locator('[data-mcel-inline-details]').get_attribute('open') is not None
        assert page.evaluate('''(key)=>document.activeElement?.getAttribute('data-select-key')===key''',before['key'])
        assert row.get_attribute('aria-selected')=='true'
    finally:page.close()
