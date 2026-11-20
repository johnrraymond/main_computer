"""Behavioral SLH certification gaps: real semantic UI submissions and state reconciliation.

Browser HTTP is explicitly mocked here.  UI evidence never substitutes for the
separate real, disposable ORG-SLH backend or live deployment evidence.
"""
from __future__ import annotations

import shutil
from contextlib import contextmanager

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import render_slh_page
from tests.test_mcel_seh_management_oracle_v1 import _browser_mock


@contextmanager
def _page(target):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None, args=['--no-sandbox'])
        try:
            page = browser.new_page(viewport={'width': 1240, 'height': 900})
            page.set_default_timeout(6500)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('dialog', lambda d: d.accept())
            page.evaluate(_browser_mock())
            yield page, errors
            assert not errors, errors
        finally:
            browser.close()


def test_remote_seh_disconnect_ui_revokes_ticket_and_protected_projection():
    with _page('remote-seh') as (page, errors):
        page.set_content(render_slh_page('remote-seh'))
        page.wait_for_function('() => !!window.__mcelSlh?.app')
        form = page.locator('form[data-intent="intent:connect-remote-seh"]')
        form.locator('[name="sehUrl"]').fill('wss://relay.example')
        form.locator('[type="submit"]').click()
        page.wait_for_function("() => window.__mcelSlh.app.readState('state:remote-seh-session')?.authenticationState==='AUTHENTICATED'")
        before = page.evaluate("window.__mcelSlh.app.readState('state:remote-seh-session')")
        assert before['authenticationState'] == 'AUTHENTICATED'
        assert 'sessionTicket' not in before  # Opaque ticket must never be projected
        page.locator('button[data-intent="intent:disconnect-remote-seh"]:visible').click()
        page.wait_for_function("() => window.__mcelSlh.app.readState('state:remote-seh-session')?.authenticationState==='DISCONNECTED'")
        disconnects = page.evaluate("window.calls.filter(c=>c.url.endsWith('/remote-seh/connect') && c.body.action==='disconnect')")
        assert len(disconnects) == 1
        assert disconnects[0]['body'] == {'action':'disconnect','arguments':{'sessionTicket':'n'*40}}
        after = page.evaluate("window.__mcelSlh.app.readState('state:remote-seh-session')")
        assert not after.get('sessionTicket')
        assert 'DISCONNECTED' in page.locator('[data-mcel-node="surface-node:slh.remote-seh-connection-details"]').inner_text()
        assert 'Select an identity and establish the target connection' in page.locator('body').inner_text()


@pytest.mark.parametrize('target', ['overview', 'network'])
def test_seh_connection_start_stop_restart_ui_reconciles_authoritative_state(target):
    with _page(target) as (page, errors):
        page.evaluate('''() => {
          window.connectionActions=[];
          const baseFetch=window.fetch;
          window.fetch=async(url,opts={})=>{
            if(String(url)==='/api/applications/slh/seh/lifecycle'){
              const body=JSON.parse(opts.body||'{}');
              window.connectionActions.push(body);
              if(body.operation==='stop'){
                Object.assign(window.sehStatus,{phase:'STOPPED',connected:false,connectionEnabled:false});
              } else if(body.operation==='start'){
                Object.assign(window.sehStatus,{phase:'CONNECTED',connected:true,connectionEnabled:true});
              } else if(body.operation==='restart'){
                Object.assign(window.sehStatus,{phase:'RECONNECTED',connected:true,connectionEnabled:true});
              }
              return {ok:true,json:async()=>({ok:true,result:{...window.sehStatus}})};
            }
            return baseFetch(url,opts);
          };
        }''')
        page.set_content(render_slh_page(target))
        page.wait_for_function('() => !!window.__mcelSlh?.app')
        if target == 'network':
            form = page.locator('form[data-intent="intent:connect-network-slh"]')
            form.locator('[name="address"]').fill('192.168.1.8')
            form.locator('[name="port"]').fill('8899')
            form.locator('[type="submit"]').click()
            page.wait_for_function("() => window.calls.some(c=>c.url.endsWith('/network/connect'))")
        notebook = page.locator('[data-mcel-operation-notebook]:visible')
        notebook.locator('[data-mcel-operation-tab="connection"]').click()
        for action, expected_phase in [('Stop','STOPPED'),('Start','CONNECTED'),('Restart','RECONNECTED')]:
            notebook.locator(f'[data-mcel-operation-action="{action}"]').click()
            page.wait_for_function('(v) => window.__mcelSlh.app.readState("state:seh-connection-status")?.phase===v', arg=expected_phase)
            state = page.evaluate('window.__mcelSlh.app.readState("state:seh-connection-status")')
            assert state['connected'] is (action != 'Stop')
        actions = page.evaluate('window.connectionActions')
        assert [(x['target'],x['operation']) for x in actions if x['operation'] != 'status'] == [
            ('network' if target == 'network' else 'local', 'stop'),
            ('network' if target == 'network' else 'local', 'start'),
            ('network' if target == 'network' else 'local', 'restart')]
        assert all(x['target'] != 'remote-seh' for x in actions)


@pytest.mark.parametrize('target', ['overview', 'network'])
def test_seh_controller_register_select_ui_persists_in_authoritative_inspection(target):
    with _page(target) as (page, errors):
        page.evaluate('''() => {
          window.registry={generation:33,names:['coolify-a'],selected:'coolify-a',writes:[]};
          const baseFetch=window.fetch;
          window.fetch=async(url,opts={})=>{
            if(String(url)==='/api/applications/slh/seh/deployment'){
              const body=JSON.parse(opts.body||'{}');
              if(body.operation==='operation-state')return {ok:true,json:async()=>({ok:true,result:{
                schema:'main-computer.seh-management-operation-snapshot.v1',state:'IDLE',busy:false,operation:null}})};
              if(body.operation==='inspect')return {ok:true,json:async()=>({ok:true,result:{
                generation:window.registry.generation,controllers:window.registry.names.map(identity=>({identity})),
                selectedController:window.registry.selected,applicationUuid:'',deploymentStatus:'TARGET_SELECTED'}})};
              if(body.operation==='register'){
                window.registry.writes.push(body);window.registry.names.push(body.arguments.name);
                window.registry.generation++;
              } else if(body.operation==='select'){
                window.registry.writes.push(body);window.registry.selected=body.arguments.name;
                window.registry.generation++;
              } else throw Error('UNEXPECTED_REGISTRY_OPERATION:'+body.operation);
              return {ok:true,json:async()=>({ok:true,result:{outcome:'CONTROLLER_SAVED',message:'Saved',operationId:'',applicationUuid:''}})};
            }
            return baseFetch(url,opts);
          };
        }''')
        page.set_content(render_slh_page(target))
        page.wait_for_function('() => !!window.__mcelSlh?.app')
        if target == 'network':
            form = page.locator('form[data-intent="intent:connect-network-slh"]')
            form.locator('[name="address"]').fill('192.168.1.8')
            form.locator('[name="port"]').fill('8899')
            form.locator('[type="submit"]').click()
            page.wait_for_function("() => window.calls.some(c=>c.url.endsWith('/network/connect'))")
        notebook = page.locator('[data-mcel-operation-notebook]:visible')
        page.wait_for_function("() => window.__mcelSlh.app.readState('state:seh-deployment-status')?.selectedController==='coolify-a'")
        register = notebook.locator('[data-mcel-operation-form="intent:seh-deployment-register"]')
        register.locator('[name="name"]').fill('coolify-c')
        register.locator('[name="url"]').fill('https://coolify-c.example.org')
        register.locator('[name="apiToken"]').fill('temporary-secret-not-in-state')
        register.locator('[type="submit"]').click()
        page.wait_for_function("() => window.registry.writes.some(x=>x.operation==='register')")
        page.wait_for_function("() => window.__mcelSlh.app.readState('state:seh-deployment-status')?.controllers?.some(x=>x.identity==='coolify-c')")
        select = notebook.locator('[data-mcel-operation-form="intent:seh-deployment-select"]')
        select.locator('select[name="name"]').select_option('coolify-c')
        select.locator('[type="submit"]').click()
        page.wait_for_function("() => window.registry.writes.some(x=>x.operation==='select')")
        page.wait_for_function("() => window.__mcelSlh.app.readState('state:seh-deployment-status')?.selectedController==='coolify-c'")
        writes = page.evaluate('window.registry.writes')
        assert [(x['target'],x['operation']) for x in writes] == [
            ('local' if target == 'overview' else 'network','register'),
            ('local' if target == 'overview' else 'network','select')]
        assert writes[0]['arguments'] == {'name':'coolify-c', 'url':'https://coolify-c.example.org',
                                           'apiToken':'temporary-secret-not-in-state'}
        assert writes[1]['arguments'] == {'name':'coolify-c'}
        assert 'temporary-secret-not-in-state' not in str(page.evaluate("window.__mcelSlh.app.readState('state:seh-deployment-status')"))
