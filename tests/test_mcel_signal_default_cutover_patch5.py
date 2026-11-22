"""Patch 5 defaults: scope, emergency rollback, actual browser mounts and isolation."""
from __future__ import annotations

import pytest

from main_computer.mcel_signal_scheduler_rollout import start_certified
from main_computer.mcel_slh_application import render_slh_page
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools.mcel_signal_differential_acceptance import _html


def test_policy_default_and_emergency_kill_are_fail_closed():
    assert start_certified('slh', {})
    assert start_certified('semantic-task-manager', {})
    for app in ('file-manager','semantic-file-manager','code-editor','mcel-lab','unknown'):
        assert start_certified(app, {}) is False
    assert start_certified('slh', {'MCEL_SIGNAL_SCHEDULER_SLH':'incumbent'}) is False
    assert start_certified('semantic-task-manager', {'MCEL_SIGNAL_SCHEDULER_TASK_MANAGER':'incumbent'}) is False
    assert start_certified('slh', {'MCEL_SIGNAL_SCHEDULER_SLH':'CERTIFIED'}) is False
    assert start_certified('slh', {'MCEL_SIGNAL_SCHEDULER_SLH':'nope'}) is False
    assert start_certified('slh', {'MCEL_SIGNAL_SCHEDULER_SLH':'certified'})
    assert not start_certified('slh', {'MCEL_SIGNAL_SCHEDULER_SLH':'certified',
                                      'MCEL_SIGNAL_SCHEDULER_DISABLE':'1'})
    assert not start_certified('semantic-task-manager', {'MCEL_SIGNAL_SCHEDULER_DISABLE':'INVALID'})
    assert start_certified('slh', {'MCEL_SIGNAL_SCHEDULER_DISABLE':'0'})


def test_production_html_has_default_certified_and_explicit_kill_switch(monkeypatch):
    for key in ('MCEL_SIGNAL_SCHEDULER_SLH','MCEL_SIGNAL_SCHEDULER_TASK_MANAGER',
                'MCEL_SIGNAL_SCHEDULER_DISABLE'):
        monkeypatch.delenv(key,raising=False)
    assert 'signalSchedulerStartEnabled:true' in render_slh_page()
    assert 'signalSchedulerStartEnabled:true' in render_semantic_page('task-manager',promoted=True)
    assert 'signalSchedulerStartEnabled:false' in render_semantic_page('task-manager',promoted=False)
    assert 'signalSchedulerIntegration:false' in render_semantic_page('file-manager',promoted=True)
    assert 'signalSchedulerStartEnabled:false' in render_semantic_page('file-manager',promoted=True)
    monkeypatch.setenv('MCEL_SIGNAL_SCHEDULER_DISABLE','1')
    assert 'signalSchedulerStartEnabled:false' in render_slh_page()
    assert 'signalSchedulerStartEnabled:false' in render_semantic_page('task-manager',promoted=True)


def test_pre_cutover_isolated_differential_reference_remains_incumbent(monkeypatch):
    for key in ('MCEL_SIGNAL_SCHEDULER_SLH','MCEL_SIGNAL_SCHEDULER_TASK_MANAGER',
                'MCEL_SIGNAL_SCHEDULER_DISABLE'):
        monkeypatch.delenv(key,raising=False)
    for app in ('slh','task-manager'):
        html=_html(app,False,candidate_mode='isolated')
        assert 'signalSchedulerStartEnabled:false' in html
        assert 'signalCandidateIsolatedTest:true' not in html
        candidate=_html(app,True,candidate_mode='isolated')
        assert 'signalSchedulerStartEnabled:false' in candidate
        assert 'signalCandidateIsolatedTest:true' in candidate


@pytest.mark.parametrize('app',('slh','task-manager'))
def test_unmodified_mounted_host_starts_certified_and_manual_rollback(app,monkeypatch):
    pw=pytest.importorskip('playwright.sync_api')
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from tests.test_mcel_slh_lifecycle_disable_enablement_oracle import MOCK as SLH_MOCK
    from tools.mcel_task_manager_functional_parity_frob import FIXTURE_JS as TASK_MOCK
    for key in ('MCEL_SIGNAL_SCHEDULER_SLH','MCEL_SIGNAL_SCHEDULER_TASK_MANAGER',
                'MCEL_SIGNAL_SCHEDULER_DISABLE'):
        monkeypatch.delenv(key,raising=False)
    html=render_slh_page() if app=='slh' else render_semantic_page('task-manager',promoted=True)
    obj='__mcelSlh' if app=='slh' else '__mcelWorkbench'
    with pw.sync_playwright() as playwright:
        browser,_=launch_semantic_browser(playwright)
        try:
            page=browser.new_page(viewport={'width':1180,'height':860})
            page.evaluate(SLH_MOCK if app=='slh' else TASK_MOCK)
            page.set_content(html)
            page.wait_for_function(f'()=>!!window.{obj}?.app')
            state=page.evaluate(f'()=>window.{obj}.app.signalCandidateReport()')
            assert state['integrationEnabled'] and state['mode']=='integrated-opt-in',state
            if app=='slh':
                page.evaluate('()=>window.__mcelSlh.app.execute("intent:refresh-lifecycle")')
            else:
                page.wait_for_function('()=>window.__mcelWorkbench.app.receipts().some(r=>r.intentId==="intent:refresh")')
                page.evaluate('()=>window.__mcelWorkbench.app.execute("intent:list-schedules")')
            assert page.evaluate(f'()=>window.{obj}.app.signalCandidateReport().statistics.skips')>0
            rolled=page.evaluate(f'()=>window.{obj}.app.setSignalSchedulingMode("incumbent")')
            assert rolled['status']=='ROLLED_BACK' and rolled['report']['mode']=='disabled'
            restored=page.evaluate(f'()=>window.{obj}.app.setSignalSchedulingMode("certified")')
            assert restored['status']=='ENABLED'
            assert restored['report']['statistics']['skips']==0
        finally:
            browser.close()
