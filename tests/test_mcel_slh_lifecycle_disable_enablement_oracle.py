"""Configuration task lifecycle oracle: no Service Lifecycle panel or form reset.

Stop is a temporary pause of the running child, whereas Disable permanently
turns off the ORG-SLH auto-start policy. These are local signed actions only.
"""
from __future__ import annotations

import shutil
import pytest
from playwright.sync_api import sync_playwright
from main_computer.mcel_slh_application import compile_slh_application, render_slh_page


MOCK = r'''() => {
  window.lifecycle='not_configured';window.slhCalls=[];
  window.fetch=async(url,options={})=>{
    const method=options.method||'GET',body=options.body?JSON.parse(options.body):null;
    window.slhCalls.push({url,method,body});
    if(url==='/api/applications/slh/identities')return {ok:true,json:async()=>({
      schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'device-a',
      identities:[{deviceId:'device-a',label:'Default identity',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'}],
      explanation:'available'})};
    if(url==='/api/applications/slh/observe?view=overview')return {ok:true,json:async()=>({
      ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',
      overview:{organizationState:window.lifecycle,organizationEndpoint:'127.0.0.1:8899'}})};
    if(url==='/api/applications/slh/seh/deployment'){
      if(body?.operation==='operation-state')return {ok:true,json:async()=>({ok:true,result:{
        schema:'main-computer.seh-management-operation-snapshot.v1',state:'IDLE',busy:false,
        operation:null,last_operation:null}})};
      if(body?.operation==='inspect')return {ok:true,json:async()=>({ok:true,result:{
        generation:33,selectedController:'coolify-a',applicationUuid:'',
        deploymentStatus:'UNDEPLOYED',controllers:[]}})};
      throw Error('UNEXPECTED_SEH_MUTATION:'+body?.operation);
    }
    if(url==='/api/applications/slh/setup/action'){
      if(body.action==='toggle-enablement')window.lifecycle=window.lifecycle==='disabled'?'running':'disabled';
      else if(body.action==='toggle')window.lifecycle=window.lifecycle==='running'?'stopped':'running';
      return {ok:true,json:async()=>({outcome:body.action==='toggle-enablement'?(window.lifecycle==='disabled'?'DISABLED':'ENABLED'):window.lifecycle==='stopped'?'STOPPED':'RUNNING',
        message:'Operation confirmed',deviceId:body.arguments.adminDeviceId})};
    }
    throw Error('UNEXPECTED_API:'+url);
  };
}'''


def _setup_action_calls(page):
    """Separate local signer authentication from consequential lifecycle writes.

    The Organization Identities notebook may authenticate when it enters the
    viewport while ORG-SLH is running. That is not a lifecycle mutation.
    """
    calls = page.evaluate(
        """() => window.slhCalls.filter(c => c.url === '/api/applications/slh/setup/action')"""
    )
    for call in calls:
        assert call['method'] == 'POST', call
        assert isinstance(call['body'], dict), call
        action = call['body'].get('action')
        assert action in {'authenticate', 'toggle', 'toggle-enablement'}, call
        if action == 'authenticate':
            assert call['body'].get('arguments') == {'adminDeviceId': 'device-a'}, call
    return [call['body']['action'] for call in calls]


def test_lifecycle_controls_are_part_of_configuration_task_and_semantically_gated():
    ir, composition=compile_slh_application()
    nodes={n['id']:n for n in ir['surfaces'][0]['nodes']}
    form=nodes['surface-node:slh.configure-org-slh']
    assert form['resetControl'] is False
    disable=nodes['surface-node:slh.organization-enablement']
    toggle=nodes['surface-node:slh.start-stop-org-slh']
    assert disable['labelByState']['labels']=={'running':'Disable','stopped':'Disable','disabled':'Enable'}
    assert toggle['labelByState']['labels']=={'running':'Stop','stopped':'Start'}
    assert {m['ref'] for r in composition['regions'] if r['recipe']=='task' for m in r['members']} >= {
        form['id'],disable['id'],toggle['id']}
    assert not any(r['id'].endswith('service-lifecycle') for r in composition['regions'])


@pytest.mark.parametrize('width',[390,1040,1440])
def test_configure_footer_actions_follow_authoritative_lifecycle(width):
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':width,'height':800})
            page.on('dialog',lambda dialog:dialog.accept())
            page.evaluate(MOCK)
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:organization-lifecycle')?.state==='not_configured'")
            form=page.locator('form[data-intent="intent:configure-and-enable-organization"]')
            assert form.count()==1
            assert form.locator('button[type="reset"]').count()==0
            disable=form.locator('button[data-intent="intent:toggle-organization-enablement"]')
            toggle=form.locator('button[data-intent="intent:toggle-organization"]')
            assert disable.count()==toggle.count()==1
            assert disable.is_disabled() and toggle.is_disabled()
            assert all(
                c['method']=='GET' or (
                    c['method']=='POST' and
                    c['url']=='/api/applications/slh/seh/deployment' and
                    c['body']['operation'] in ('inspect','operation-state'))
                for c in page.evaluate('window.slhCalls')
            ), 'Startup issued a mutation, not a read-only SEH inspection'
            for state, can_disable, can_toggle, label, enable_label in [
                ('running',True,True,'Stop','Disable'),('stopped',True,True,'Start','Disable'),
                ('disabled',True,False,'Start','Enable'),('starting',False,False,'Start','Disable'),
                ('failed',False,False,'Start','Disable'),('not_configured',False,False,'Start','Disable')]:
                page.evaluate('(s)=>window.lifecycle=s',state)
                page.evaluate("() => window.__mcelSlh.app.execute('intent:refresh-lifecycle')")
                page.wait_for_function('(s)=>window.__mcelSlh.app.readState("state:organization-lifecycle")?.state===s',arg=state)
                assert disable.is_enabled() is can_disable,(state,'disable')
                assert toggle.is_enabled() is can_toggle,(state,'toggle')
                assert toggle.inner_text()==label
                assert disable.inner_text()==enable_label
                if not can_disable:
                    before=page.evaluate("() => window.slhCalls.filter(c=>c.url==='/api/applications/slh/setup/action').length")
                    refused=page.evaluate("async()=>window.__mcelSlh.app.execute('intent:toggle-organization-enablement')")
                    assert refused['disposition']=='refused-before-attempt',refused
                    assert page.evaluate("() => window.slhCalls.filter(c=>c.url==='/api/applications/slh/setup/action').length")==before
            page.evaluate("() => {window.lifecycle='running'}")
            page.evaluate("() => window.__mcelSlh.app.execute('intent:refresh-lifecycle')")
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='running'")
            toggle.click()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='stopped'")
            assert toggle.inner_text()=='Start'
            assert disable.is_enabled() and disable.inner_text()=='Disable'
            toggle.click()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='running'")
            disable.click()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='disabled'")
            assert disable.inner_text()=='Enable' and toggle.is_disabled()
            disable.click()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='running'")
            assert disable.inner_text()=='Disable' and toggle.inner_text()=='Stop'
            # Identity-notebook authentication is an allowed protected read
            # prerequisite, not an extra SLH lifecycle mutation.
            lifecycle_actions = [a for a in _setup_action_calls(page) if a != 'authenticate']
            assert lifecycle_actions == ['toggle','toggle','toggle-enablement','toggle-enablement']
        finally:
            browser.close()


def test_visible_identity_notebook_authentication_does_not_count_as_lifecycle_mutation():
    """Reproduce the wide-viewport signer handshake that exposed the false failure."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=shutil.which('chromium') or None
        )
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 800})
            page.on('dialog', lambda dialog: dialog.accept())
            page.evaluate(MOCK)
            page.set_content(render_slh_page())
            page.wait_for_function(
                "() => window.__mcelSlh?.app.readState('state:selected-local-identity') === 'device-a'"
            )
            page.evaluate("() => {window.lifecycle = 'running'}")
            page.evaluate("() => window.__mcelSlh.app.execute('intent:refresh-lifecycle')")
            page.wait_for_function(
                "() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state === 'running'"
            )
            page.locator(
                '[data-mcel-notebook="surface-node:slh.organization-identity-notebook"]'
            ).scroll_into_view_if_needed()
            page.wait_for_function(
                """() => window.slhCalls.some(c =>
                  c.url === '/api/applications/slh/setup/action' &&
                  c.body?.action === 'authenticate')"""
            )
            assert _setup_action_calls(page) == ['authenticate']
        finally:
            browser.close()


@pytest.mark.parametrize('view',['network','remote-seh'])
def test_no_local_management_controls_on_remote_targets(view):
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':1040,'height':800})
            page.evaluate(MOCK)
            page.set_content(render_slh_page(view))
            page.wait_for_function('() => !!window.__mcelSlh?.app')
            for intent in ('toggle-organization-enablement','toggle-organization'):
                button=page.locator(f'button[data-intent="intent:{intent}"]')
                assert button.count()==1 and not button.is_visible()
                refused=page.evaluate('(id)=>window.__mcelSlh.app.execute("intent:"+id)',intent)
                assert refused['disposition']=='refused-before-attempt'
            assert not any(c['url']=='/api/applications/slh/setup/action' for c in page.evaluate('window.slhCalls'))
        finally:
            browser.close()


def test_real_slh_icon_oracle_tracks_authoritative_lifecycle_without_dom_reconstruction():
    """Independent expected action identities, checked on the actual SLH page.

    All state changes arrive through the real read-only lifecycle capability.
    The production icon resolver never imports the test-only oracle.
    """
    import json
    from pathlib import Path

    source = Path(__file__).parent / 'fixtures/mcel-semantic-icon-oracle.v1.json'
    spec = json.loads(source.read_text(encoding='utf-8'))['applications']['slh']
    states = spec['stateful']
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = browser.new_page(viewport={'width': 1040, 'height': 800})
            errors = []
            page.on('pageerror', lambda err: errors.append(str(err)))
            page.evaluate(MOCK)
            page.set_content(render_slh_page(), wait_until='domcontentloaded')
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:organization-lifecycle')?.state==='not_configured'")
            ids = {
                'configure-org-slh': 'intent:configure-and-enable-organization',
                'organization-enablement': 'intent:toggle-organization-enablement',
                'start-stop-org-slh': 'intent:toggle-organization',
            }

            def inspect():
                return page.evaluate('''ids => Object.fromEntries(Object.entries(ids).map(([name,intent])=>{
                  const form=document.querySelector('form[data-intent="'+intent+'"]');
                  const button=form ? form.querySelector('button[type="submit"]') :
                    document.querySelector('button[data-intent="'+intent+'"]');
                  const slots=[...button.querySelectorAll('[data-mcel-icon-core="slot"]')];
                  return [name,{label:button.textContent,icon:slots[0]?.getAttribute('data-mcel-icon-identity')||null,
                    count:slots.length,svg:slots[0]?.querySelector('svg')?.getAttribute('data-mcel-icon-id')||null,
                    decorative:slots[0]?.getAttribute('aria-hidden')||null}];
                }))''', ids)

            initial = inspect()
            assert initial['configure-org-slh'] == {'label':'Apply','icon':'action.apply','count':1,
                'svg':'action.apply','decorative':'true'}, initial
            assert initial['organization-enablement']['icon'] is None
            assert initial['start-stop-org-slh']['icon'] is None

            for state, expected in states.items():
                page.evaluate('(state)=>{window.lifecycle=state}', state)
                page.evaluate("() => window.__mcelSlh.app.execute('intent:refresh-lifecycle')")
                page.wait_for_function('(state)=>window.__mcelSlh.app.readState("state:organization-lifecycle")?.state===state', arg=state)
                actual = inspect()
                assert actual['configure-org-slh']['icon']=='action.apply', (state,actual)
                assert actual['configure-org-slh']['label']=='Apply', (state,actual)
                for name, wanted in expected.items():
                    assert actual[name]['icon']==wanted, (state,name,wanted,actual[name])
                    assert actual[name]['count']==(1 if wanted else 0), (state,name,actual[name])
                    assert actual[name]['svg']==wanted, (state,name,actual[name])
                    if wanted: assert actual[name]['decorative']=='true'

            # A same-label transition must preserve the existing MCEL icon slot.
            for state in ('running','stopped'):
                page.evaluate('(state)=>{window.lifecycle=state}',state)
                page.evaluate("() => window.__mcelSlh.app.execute('intent:refresh-lifecycle')")
                page.wait_for_function('(state)=>window.__mcelSlh.app.readState("state:organization-lifecycle")?.state===state',arg=state)
                if state=='running':
                    page.evaluate('''() => {window.__disableSlot=document.querySelector(
                      'button[data-intent="intent:toggle-organization-enablement"] [data-mcel-icon-core="slot"]')}''')
                else:
                    assert page.evaluate('''() => window.__disableSlot===document.querySelector(
                      'button[data-intent="intent:toggle-organization-enablement"] [data-mcel-icon-core="slot"]')''')
            assert errors == [], errors
            # Rendering may expose the protected notebook and initiate its
            # signer handshake; it must never trigger a lifecycle mutation.
            assert all(a == 'authenticate' for a in _setup_action_calls(page))
        finally:
            browser.close()
