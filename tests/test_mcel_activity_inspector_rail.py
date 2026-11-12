"""Activity placement and SLH evidence must follow semantic inspectors, not app footers."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

from main_computer.mcel_knowledge.workspace_plan import plan_workspace
from main_computer.mcel_slh_application import compile_slh_application, render_slh_page
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from tools import mcel_paired_app_oracles as paired_oracle


def test_paired_status_follows_inspector_and_never_defaults_to_footer():
    ir = compile_semantic_app('file-manager')
    plan = plan_workspace(ir)
    zones = {zone['id']:zone for zone in plan['zones']}
    assert zones['tools']['kind'] == 'inspector'
    assert zones['status']['members']
    assert all(value == 'inspector-after-tools' for value in zones['status']['ladder'].values())
    assert 'bottom' not in zones['status']['ladder'].values()
    # Negative control: the old planner's activity footer is not acceptable.
    broken = json.loads(json.dumps(plan))
    broken['zones'][next(i for i,z in enumerate(broken['zones']) if z['id']=='status')]['ladder']['wide']='bottom'
    assert broken['zones'] != plan['zones']


def test_paired_file_manager_activity_is_in_right_inspector_below_details():
    with sync_playwright() as pw:
      browser=_browser(pw)
      try:
        for width in (390, 1280):
          page=browser.new_page(viewport={'width':width,'height':800})
          page.set_content(render_semantic_page('file-manager'))
          page.wait_for_function('()=>!!window.__mcelWorkbench')
          geometry=page.evaluate('''() => {
            const details=document.querySelector('[data-mcel-zone="tools"] [data-mcel-purpose="inspection"]');
            const activity=document.querySelector('[data-mcel-zone="status"] [data-mcel-purpose="status"]');
            const tools=details?.closest('[data-mcel-zone="tools"]');
            const workspace=document.querySelector('[data-mcel-zone="workspace"]');
            if(!details || !activity || !tools || !workspace)return null;
            const d=details.getBoundingClientRect(),a=activity.getBoundingClientRect(),
              t=tools.getBoundingClientRect(),w=workspace.getBoundingClientRect();
            return {
              sameRail:tools.contains(activity), belowDetails:a.top>=d.bottom-2,
              rightAtDesktop:document.documentElement.clientWidth>=1000 ? t.left>=w.right-2 : true,
              noFullWidthFooter: document.documentElement.clientWidth<1000 || a.width < 0.5*document.documentElement.clientWidth,
              activityInsideTools:activity.closest('[data-mcel-zone="tools"]')===tools
            };
          }''')
          assert geometry is not None
          assert all(geometry.values()),(width,geometry)
          page.close()
      finally:browser.close()


def test_negative_control_rejects_file_manager_activity_as_detached_footer():
    with sync_playwright() as pw:
      browser=_browser(pw)
      try:
        page=browser.new_page(viewport={'width':1280,'height':800})
        page.set_content(render_semantic_page('file-manager'))
        page.wait_for_function('()=>!!window.__mcelWorkbench')
        assert 'activity-not-in-inspector-rail' not in paired_oracle.check_layout(
            'file-manager',page.evaluate(paired_oracle.DOM_OBSERVATION),1280)['failures']
        page.evaluate('''() => {
          const activity=document.querySelector('[data-mcel-zone="status"]');
          const layout=document.querySelector('[data-mcel-workspace-layout]');
          layout.append(activity);
        }''')
        outcome=paired_oracle.check_layout('file-manager',page.evaluate(paired_oracle.DOM_OBSERVATION),1280)
        assert outcome['status']=='FAIL'
        assert 'activity-not-in-inspector-rail' in outcome['failures']
      finally:browser.close()


def test_generated_inspector_is_derived_observation_and_activity_is_receipt():
    ir, composition = compile_slh_application()
    regions = {r['recipe']:r for r in composition['regions']}
    assert 'inspection' in regions and 'evidence' in regions
    assert regions['inspection']['purpose'] == 'inspection'
    assert regions['evidence']['purpose'] == 'status'
    nodes = {n['id']:n for n in ir['surfaces'][0]['nodes']}
    assert {nodes[x['ref']]['nodeKind'] for x in regions['inspection']['members']} == {'state-value'}
    assert {nodes[x['ref']]['nodeKind'] for x in regions['evidence']['members']} == {'operation-evidence'}
    ids = [n['id'] for n in nodes.values() if n.get('nodeKind')=='control' and n.get('intent')]
    assert ids == ['surface-node:slh.disable-org-slh']


def _browser(pw):
    return pw.chromium.launch(executable_path=shutil.which('chromium') or None,headless=True,args=['--no-sandbox'])


def _mock_api(page):
    page.evaluate('''() => {
      const data={};
      Object.defineProperty(window,'sessionStorage',{configurable:true,value:{
        getItem:k=>data[k]??null,setItem:(k,v)=>data[k]=String(v),removeItem:k=>delete data[k]
      }});
      window.confirm=()=>true;
      window.mcelFailRequest=true;
      window.mcelCalls=[];
      window.fetch=async(url,opts={})=>{
        window.mcelCalls.push({url,method:opts.method||'GET'});
        if(url==='/api/applications/slh/observe?view=overview')return {ok:true,json:async()=>({
          ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',
          overview:{organizationState:'running',organizationEndpoint:'10.0.0.15:8899'}})};
        if(url==='/api/applications/slh/identities')return {ok:true,json:async()=>({
          schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'test-device',
          identities:[{deviceId:'test-device',label:'Test identity',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'}],
          explanation:'Available'})};
        if(url==='/api/applications/slh/setup/action')return window.mcelFailRequest
          ? {ok:false,status:409,json:async()=>({ok:false,error:'BIND_HOST_NOT_ASSIGNED'})}
          : {ok:true,status:200,json:async()=>({ok:true,outcome:'RUNNING',message:'Service verified running',deviceId:'test-device'})};
        throw Error('UNEXPECTED_FETCH:'+url);
      };
    }''')


def test_slh_inspector_is_right_of_workspace_and_reports_backend_failure():
    with sync_playwright() as pw:
      browser=_browser(pw)
      try:
        page=browser.new_page(viewport={'width':1320,'height':830})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        _mock_api(page)
        page.set_content(render_slh_page())
        page.wait_for_function('''() => window.__mcelSlh?.app.readState('state:organization-lifecycle')?.state==='running' ''')
        inspector=page.locator('[data-mcel-natural-scroller="inspection"]')
        assert inspector.count()==1
        assert inspector.locator('[data-mcel-purpose="inspection"]').count()==1
        assert inspector.locator('[data-mcel-purpose="status"]').count()==1
        assert page.evaluate('''() => {
          const inspector=document.querySelector('[data-mcel-natural-scroller="inspection"]');
          const workspace=document.querySelector('[data-mcel-natural-scroller="workspace"]');
          const details=inspector.querySelector('[data-mcel-purpose="inspection"]');
          const activity=inspector.querySelector('[data-mcel-purpose="status"]');
          const a=inspector.getBoundingClientRect(),w=workspace.getBoundingClientRect();
          return a.left>=w.right-2 && activity.getBoundingClientRect().top>=details.getBoundingClientRect().bottom-2;
        }''')
        assert '10.0.0.15' in inspector.inner_text()
        page.get_by_role('button',name='Configure ORG-SLH').click()
        page.wait_for_function('''()=>window.__mcelSlh.app.receipts().some(r =>
           r.intentId==='intent:configure-and-enable-organization' && r.disposition!=='completed')''')
        assert inspector.get_by_text('BIND_HOST_NOT_ASSIGNED',exact=False).count()>=1
        assert not any(c['method']=='POST' for c in page.evaluate('window.mcelCalls')[:-1])
        assert errors == [],errors
      finally:browser.close()


def test_slh_latest_success_then_failure_stays_in_inspector():
    with sync_playwright() as pw:
      browser=_browser(pw)
      try:
        page=browser.new_page(viewport={'width':1320,'height':830})
        _mock_api(page)
        page.set_content(render_slh_page())
        page.wait_for_function('''() => !!window.__mcelSlh && window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='running' ''')
        inspector=page.locator('[data-mcel-natural-scroller="inspection"]')
        page.evaluate('window.mcelFailRequest=false')
        page.get_by_role('button',name='Configure ORG-SLH').click()
        page.wait_for_function('''()=>window.__mcelSlh.app.receipts().some(r=>r.intentId==='intent:configure-and-enable-organization' && r.disposition==='completed')''')
        assert 'completed' in inspector.locator('[data-mcel-request-result]').inner_text()
        page.evaluate('window.mcelFailRequest=true')
        page.get_by_role('button',name='Configure ORG-SLH').click()
        page.wait_for_function('''()=>window.__mcelSlh.app.receipts().filter(r=>r.intentId==='intent:configure-and-enable-organization').length>=2''')
        assert 'BIND_HOST_NOT_ASSIGNED' in inspector.locator('[data-mcel-request-result]').inner_text()
        assert inspector.get_by_text('BIND_HOST_NOT_ASSIGNED',exact=False).count()>=1
      finally:browser.close()


def test_slh_inspector_stays_grouped_at_narrow_width():
    with sync_playwright() as pw:
      browser=_browser(pw)
      try:
        page=browser.new_page(viewport={'width':390,'height':780})
        _mock_api(page);page.set_content(render_slh_page())
        page.wait_for_function('''() => !!window.__mcelSlh''')
        assert page.evaluate('''()=>{
          const rail=document.querySelector('[data-mcel-natural-scroller="inspection"]');
          const task=document.querySelector('[data-mcel-natural-scroller="workspace"]');
          return rail.getBoundingClientRect().top>=task.getBoundingClientRect().top &&
            rail.querySelector('[data-mcel-purpose="status"]')?.parentElement===rail;
        }''')
      finally:browser.close()
