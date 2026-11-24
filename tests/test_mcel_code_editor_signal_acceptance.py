"""Fail-closed Code Editor integrated-signal acceptance and nonpromotion policy."""
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools import mcel_code_editor_signal_acceptance as accept
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT=Path(__file__).resolve().parents[1]


def test_code_editor_production_defaults_signals_and_isolated_fixture_preserves_incumbent(monkeypatch):
    monkeypatch.delenv('MCEL_SIGNAL_SCHEDULER_CODE_EDITOR',raising=False)
    monkeypatch.delenv('MCEL_SIGNAL_SCHEDULER_DISABLE',raising=False)
    production=render_semantic_page('code-editor',ROOT,promoted=True)
    assert 'signalSchedulerIntegration:true' in production
    assert 'signalSchedulerStartEnabled:true' in production
    assert 'window.__MCEL_CODE_EDITOR_SIGNAL_ACCEPTANCE__=true' not in production
    # Legacy isolated/differential harness still tests a genuine incumbent.
    assert 'signalSchedulerIntegration:false' in accept._html(ROOT,False)
    assert 'signalSchedulerStartEnabled:false' in accept._html(ROOT,False)
    candidate=accept._html(ROOT,True)
    assert candidate.count('signalSchedulerIntegration:true')==1
    assert candidate.count('signalSchedulerStartEnabled:true')==1
    assert '__MCEL_CODE_EDITOR_SIGNAL_ACCEPTANCE__' not in (ROOT/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js').read_text('utf8')


def test_code_editor_isolated_reference_remains_incumbent():
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page()
            page.set_content(accept._html(ROOT,False))
            page.wait_for_function('()=>!!window.__mcelWorkbench?.app')
            report=page.evaluate('window.__mcelWorkbench.app.signalCandidateReport()')
            assert report['mode']=='disabled'
            assert report['integrationEnabled'] is False
            assert report['integratedSupported'] is False
            assert page.evaluate('window.__mcelWorkbench.app.setSignalSchedulingMode("certified")')['status']=='REFUSED'
        finally:browser.close()


def test_code_editor_signal_smoke_is_differential_and_skips():
    result=accept.differential(ROOT,profile='smoke')
    assert result['status']=='PASS_DIFFERENTIAL',result['failures']
    assert len(result['stages'])>=14
    assert result['actualSkips']>0
    assert not any(result['pageErrors'].values())
    assert set(result['skipKinds'])=={'display','typed-record'}
    assert all(stage['status']=='PASS' and stage['differences']==0 for stage in result['stages'])
    assert result['mountModes']['incumbent']['mode']=='disabled'
    assert result['mountModes']['signals']['mode']=='integrated-opt-in'


def test_code_editor_invalid_consumer_class_fails_closed():
    with pytest.raises(ValueError,match='UNAUTHORIZED_CONSUMER_CLASS'):
        accept._html(ROOT,True,kinds=('unverified-collection',))


def test_code_editor_missing_parity_blocks_overall_certification(monkeypatch):
    monkeypatch.setattr(accept.parity,'run',lambda *_:{'status':'INCOMPLETE'})
    monkeypatch.setattr(accept,'differential',lambda *a,**k:{
        'status':'PASS_DIFFERENTIAL','stages':[],'actualSkips':11,'skipKinds':{'display':11},
        'registeredConsumersByClass':{'display':1},'failures':[]})
    result=accept.accept(ROOT,profile='smoke')
    assert result['status']=='BLOCKED'
    assert result['failures']==['CODE_EDITOR_FUNCTIONAL_PARITY_INCOMPLETE']
    assert not result['boundaries']['productionPromotionAuthorized']
    assert result['boundaries']['productionSignalDefault']=='CERTIFIED_WHEN_PROMOTED'


def test_code_editor_skipped_parity_blocks_overall_certification(monkeypatch):
    monkeypatch.setattr(accept,'differential',lambda *a,**k:{
        'status':'PASS_DIFFERENTIAL','stages':[],'actualSkips':11,
        'registeredConsumersByClass':{'display':1},'failures':[]})
    result=accept.accept(ROOT,profile='smoke',skip_parity=True)
    assert result['status']=='BLOCKED'
    assert 'INDEPENDENT_PARITY_NOT_RUN' in result['failures']
    assert result['functionalParity'] is None
