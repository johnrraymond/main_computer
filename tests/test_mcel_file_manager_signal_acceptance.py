"""Fail-closed tests for File Explorer integrated-signal acceptance gating."""
from pathlib import Path
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools import mcel_file_manager_signal_acceptance as acceptance

ROOT=Path(__file__).resolve().parents[1]


def test_file_explorer_host_stays_incumbent_without_test_opt_in():
    html=render_semantic_page('file-manager',ROOT)
    assert 'signalSchedulerIntegration:false' in html
    assert 'signalSchedulerStartEnabled:false' in html
    assert 'signalSchedulerIntegration:true' in acceptance._mount_html(ROOT,True)
    assert 'signalSchedulerStartEnabled:true' in acceptance._mount_html(ROOT,True)
    assert 'signalSchedulerIntegration:false' in acceptance._mount_html(ROOT,False)


def test_integrated_signal_scheduler_matches_real_browser_file_manager():
    result=acceptance.signal_differential(ROOT,profile='full')
    assert result['status']=='PASS_DIFFERENTIAL',result['failures']
    assert result['actualSkips']>0
    assert not result['errors']['incumbent'] and not result['errors']['signals']
    assert result['fallback']['candidate']==0
    assert result['fallback']['incumbentMode']=='disabled'
    assert result['fallback']['candidateEnabled'] is True
    assert len(result['stages'])>=19
    assert all(x['status']=='PASS' for x in result['stages'])
    assert set(result['fallback']['skipsByClass']) == set(acceptance.APPLICABLE)


def test_skipping_legacy_never_produces_certification(monkeypatch):
    monkeypatch.setattr(acceptance,'signal_differential',lambda *_a,**_k:{
        'status':'PASS_DIFFERENTIAL','actualSkips':40,'stages':[]})
    report=acceptance.accept(ROOT,profile='smoke',include_legacy=False)
    assert report['status']=='BLOCKED'
    # Skipping the independent reference must block certification in any caller.
    assert report['legacyParity'] is None
    assert report['boundaries']['productionSignalDefault']=='DISABLED'
