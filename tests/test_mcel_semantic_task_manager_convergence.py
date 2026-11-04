"""V1 paired Task Manager convergence with P3's bounded operational contract.

Only a throwaway TaskManagerService, synthetic process, controller and provider
are allowed. No production host endpoints or process mutations are used.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from main_computer.mcel_semantic_generated_host import compile_p3_generated_candidate
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
# Reuse the proven test-owned real HTTP/route/service fixture without creating
# another listener, synthetic controller or separate acceptance framework.
from test_mcel_p3_operational import (
    browser, isolated_task_http, _task_http_transport_bridge, _contract_receipt,
    _INTEGRATION_PID, _INTEGRATION_BIRTH,
)


def _without_source(value):
    if isinstance(value, dict):
        return {k: _without_source(v) for k, v in value.items() if k != 'source'}
    if isinstance(value, list):
        return [_without_source(v) for v in value]
    return value


def test_paired_semantic_contract_matches_p3_except_source_provenance():
    paired = compile_semantic_app('task-manager')
    p3, _ = compile_p3_generated_candidate()
    assert len(paired['intents']) == len(p3['intents']) == 19
    assert len(paired['provenance']['frontend']['sourceFiles']) == 2
    assert not paired['layouts']
    for key in ('states', 'capabilities', 'intents', 'effects'):
        left = {v['id']: _without_source(v) for v in paired[key]}
        right = {v['id']: _without_source(v) for v in p3[key]}
        assert left == right, f'Operational IR discrepancy in {key}'
    hierarchy = paired['surfaces'][0]['interfaceIntent']['hierarchy']
    assert hierarchy
    html = render_semantic_page('task-manager')
    source = (Path(__file__).resolve().parents[1] / 'tools/fixtures/mcel_semantic_other_apps/task-manager/application.html').read_text()
    assert 'data-mcel-presentation="expert"' not in source
    assert 'semantic-task-manager' in html


def _page(browser, lab, width=1100):
    page = browser.new_page(viewport={'width': width, 'height': 850})
    page.set_default_timeout(6500)
    page.on('dialog', lambda dialog: dialog.accept() if lab.accept_dialog else dialog.dismiss())
    page.expose_binding('__taskContractHttp',
                        lambda _source, uri, opts: _task_http_transport_bridge(lab, uri, opts))
    page.evaluate("""() => { window.fetch=async(uri,opts={})=>{
      const r=await window.__taskContractHttp(String(uri),opts);
      return {ok:r.ok,status:r.status,json:async()=>r.payload};
    }; }""")
    page.set_content(render_semantic_page('task-manager'))
    assert page.locator('#mcel-workbench-status').inner_text() == 'Semantic candidate ready'
    return page


def _observe_and_select(page, intent='refresh'):
    page.locator(f'form[data-intent="intent:{intent}"] button[type=submit]').click()
    key = f'{_INTEGRATION_PID}:{_INTEGRATION_BIRTH}'
    row = page.locator(f'[data-record-key="{key}"]:visible').first
    row.locator('button[data-select-key]').click()
    assert page.evaluate('window.__mcelWorkbench.app.readState("state:selected")') == key


def test_paired_real_http_process_effects_and_all_process_selection(browser, isolated_task_http):
    lab = isolated_task_http
    page = _page(browser, lab)
    try:
        _observe_and_select(page)
        page.locator('button[data-intent="intent:terminate-process"]').click()
        _contract_receipt(page, 'terminate-process', 'command-returned')
        assert ('process', 'terminate', _INTEGRATION_PID) in lab.effects
        page.locator('[data-mcel-mode-tabs] button[data-value="all-processes"]').click()
        _observe_and_select(page, 'refresh-all')
        page.locator('button[data-intent="intent:kill-process"]').click()
        _contract_receipt(page, 'kill-process', 'command-returned')
        assert ('process', 'kill', _INTEGRATION_PID) in lab.effects
        effects = [body for path, body in lab.requests if path.endswith('/task/action')]
        assert [v['action'] for v in effects] == ['terminate_pid', 'kill_pid']
        assert all(v['expected_created_at'] == _INTEGRATION_BIRTH and v['confirm'] is True
                   and len(v['request_id']) >= 12 for v in effects)
    finally:
        page.close()


def test_paired_real_http_server_status_and_lifecycle(browser, isolated_task_http):
    lab = isolated_task_http
    page = _page(browser, lab)
    try:
        page.locator('button[data-intent="intent:server-status"]').click()
        _contract_receipt(page, 'server-status', 'completed')
        assert 'running' in page.locator('body').inner_text()
        assert not lab.effects
        lab.accept_dialog = False
        page.locator('button[data-intent="intent:shutdown-server"]').click()
        page.wait_for_timeout(80)
        assert not lab.effects
        lab.accept_dialog = True
        for intent, verb, outcome in (
            ('start-server', 'start', 'command-returned'),
            ('restart-server', 'restart', 'accepted-deferred'),
            ('shutdown-server', 'shutdown', 'accepted-deferred'),
        ):
            page.locator(f'button[data-intent="intent:{intent}"]').click()
            _contract_receipt(page, intent, outcome)
            assert ('controller', verb) in lab.effects
        assert [body['action'] for path, body in lab.requests if path.endswith('/task/action')] == [
            'server_start', 'server_restart', 'server_shutdown']
    finally:
        page.close()


def test_paired_real_http_schedule_selected_revision(browser, isolated_task_http):
    lab = isolated_task_http
    page = _page(browser, lab)
    try:
        # Direct unobserved schedule invocation is not authorized by the port.
        before = page.evaluate('''async()=>{
          const p=McelSemanticReadPorts.create({appId:'semantic-task-manager'});
          return p.authorize({appId:'semantic-task-manager',intentId:'intent:run-schedule',
            effectId:'effect:run-schedule.request',capabilityId:'capability:task-scheduling',
            operation:'run',runtimeMethod:'runScheduledAction',risk:'consequential',
            arguments:{scheduleId:'forged'}});
        }''')
        assert before is False
        form = page.locator('form[data-intent="intent:create-schedule"]')
        form.locator('[name="action"]').fill('server_restart')
        form.locator('[name="runAt"]').fill('2026-11-04T12:00:00+00:00')
        form.locator('[name="note"]').fill('paired bounded test')
        form.locator('button[type=submit]').click()
        _contract_receipt(page, 'create-schedule', 'recorded')
        entries = lab.service.list_schedules()['schedules']
        assert len(entries) == 1
        page.locator('button[data-intent="intent:list-schedules"]').click()
        row = page.locator(f'[data-record-key="{entries[0]["id"]}"]')
        row.locator('button[data-select-key]').click()
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:selected-schedule")') == entries[0]['id']
        page.locator('button[data-intent="intent:run-schedule"]').click()
        _contract_receipt(page, 'run-schedule', 'accepted-deferred')
        run = next(body for path, body in lab.requests if path.endswith('/schedule/run'))
        assert run['expected_revision'] == entries[0]['revision']
        assert run['expected_action'] == entries[0]['action']
        assert ('controller', 'restart') in lab.effects
        page.locator('button[data-intent="intent:delete-schedule"]').click()
        _contract_receipt(page, 'delete-schedule', 'recorded')
        assert lab.service.list_schedules()['schedules'] == []
    finally:
        page.close()



def test_paired_stale_schedule_revision_refuses_run_and_delete(browser, isolated_task_http):
    lab = isolated_task_http
    page = _page(browser, lab)
    try:
        form = page.locator('form[data-intent="intent:create-schedule"]')
        form.locator('[name="action"]').fill('server_restart')
        form.locator('[name="runAt"]').fill('2026-11-04T12:00:00+00:00')
        form.locator('[name="note"]').fill('original revision')
        form.locator('button[type=submit]').click()
        _contract_receipt(page, 'create-schedule', 'recorded')
        record = lab.service.list_schedules()['schedules'][0]
        page.locator('button[data-intent="intent:list-schedules"]').click()
        page.locator(f'[data-record-key="{record["id"]}"] button[data-select-key]').click()
        # Change the *actual test-owned backend* after the browser has observed
        # the original revision. No production schedule or service is touched.
        data = lab.service._read_schedules()
        data['schedules'][0]['note'] = 'server-side changed after selection'
        lab.service._write_schedules(data)
        assert lab.service.list_schedules()['schedules'][0]['revision'] != record['revision']
        for intent in ('run-schedule', 'delete-schedule'):
            page.locator(f'button[data-intent="intent:{intent}"]').click()
            _contract_receipt(page, intent, 'failed')
        assert lab.service.list_schedules()['schedules'][0]['id'] == record['id']
        assert not any(e[0] == 'controller' for e in lab.effects)
    finally:
        page.close()

def test_paired_real_http_monitor_options_and_ai(browser, isolated_task_http):
    lab = isolated_task_http
    page = _page(browser, lab)
    try:
        monitor = page.locator('form[data-intent="intent:refresh"]')
        monitor.locator('[name="query"]').fill('disposable')
        monitor.locator('[name="limit"]').fill('17')
        monitor.locator('[name="includeConnections"]').fill('false')
        monitor.locator('button[type=submit]').click()
        _contract_receipt(page, 'refresh', 'completed')
        observed = next(body for path, body in lab.requests if path.endswith('/task/overview'))
        assert observed['limit'] == 17 and observed['include_connections'] is False
        ai = page.locator('form[data-intent="intent:task-ai"]')
        ai.locator('[name="instruction"]').fill('Summarize the test-owned process')
        ai.locator('[name="query"]').fill('disposable')
        ai.locator('button[type=submit]').click()
        _contract_receipt(page, 'task-ai', 'completed')
        assert 'Bounded AI contract result' in page.locator('body').inner_text()
        ask = next(body for path, body in lab.requests if path.endswith('/task/ai'))
        assert ask['limit'] == 17 and ask['include_connections'] is False
        assert any(e[0] == 'ai-provider-contract' for e in lab.effects)
    finally:
        page.close()


def test_paired_real_http_failures_are_not_success(browser, isolated_task_http):
    lab = isolated_task_http
    page = _page(browser, lab)
    try:
        _observe_and_select(page)
        lab.generation['epoch'] += 3600
        page.locator('button[data-intent="intent:terminate-process"]').click()
        _contract_receipt(page, 'terminate-process', 'failed')
        assert not lab.effects
        monitor = page.locator('form[data-intent="intent:refresh"]')
        monitor.locator('[name="limit"]').fill('99999')
        monitor.locator('button[type=submit]').click()
        _contract_receipt(page, 'refresh', 'failed')
        # Invalid monitor options must be refused before backend dispatch.
        assert not any(body.get('limit') == 99999 for path, body in lab.requests if path.endswith('/task/overview'))
        lab.ai_provider.fail = True
        ai = page.locator('form[data-intent="intent:task-ai"]')
        ai.locator('[name="instruction"]').fill('Fail on purpose')
        ai.locator('[name="query"]').fill('disposable')
        ai.locator('button[type=submit]').click()
        _contract_receipt(page, 'task-ai', 'failed')
        assert 'Bounded AI contract result' not in page.locator('body').inner_text()
    finally:
        page.close()
