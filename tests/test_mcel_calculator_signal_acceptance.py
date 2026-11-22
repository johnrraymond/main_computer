"""Calculator signals are verified without promotion or weakening parity."""
from pathlib import Path
from playwright.sync_api import sync_playwright
import pytest
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools import mcel_calculator_signal_acceptance as a
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT=Path(__file__).resolve().parents[1]


def test_normal_calculator_mount_remains_incumbent():
    html=render_expanded_page(ROOT,promoted=True)
    assert 'signalSchedulerIntegration:' not in html
    assert 'signalSchedulerStartEnabled:' not in html
    assert 'window.__MCEL_CALCULATOR_SIGNAL_ACCEPTANCE__=true' not in html
    experimental=a._mount_html(ROOT,True)
    assert experimental.count('signalSchedulerIntegration:true')==1
    assert experimental.count('signalSchedulerStartEnabled:true')==1
    assert experimental.count('window.__MCEL_CALCULATOR_SIGNAL_ACCEPTANCE__=true')==1
    disabled=a._mount_html(ROOT,False)
    assert 'signalSchedulerIntegration:false' in disabled
    assert 'signalSchedulerStartEnabled:false' in disabled


def test_test_marker_alone_does_not_implicitly_authorize_production():
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page()
            page.evaluate(a.TRANSPORT)
            page.set_content(a._mount_html(ROOT,True,marker=False))
            page.wait_for_function('()=>!!window.__mcelExpanded?.app')
            report=page.evaluate('window.__mcelExpanded.app.signalCandidateReport()')
            assert report['mode']=='disabled'
            assert not report['integratedSupported']
            assert not report['integrationEnabled']
            assert page.evaluate('window.__mcelExpanded.app.setSignalSchedulingMode("certified")')['status']=='REFUSED'
        finally:
            browser.close()


def test_calculator_signal_smoke_compares_real_renderers_and_skips():
    result=a.signal_differential(ROOT,profile='smoke')
    assert result['status']=='PASS_DIFFERENTIAL',result['failures']
    assert len(result['stages'])>=9
    assert all(x['status']=='PASS' for x in result['stages'])
    assert result['actualSkips']>0
    assert result['skipKinds']=={'display':result['actualSkips']}
    assert result['registeredConsumersByClass']['display']>0
    assert not result['errors']['incumbent'] and not result['errors']['signals']
    assert not result['endModes']['signals']['fallbacks']


def test_invalid_consumer_class_fails_closed():
    with pytest.raises(ValueError,match='UNAUTHORIZED_CALCULATOR_CONSUMER_KIND'):
        a._mount_html(ROOT,True,kinds=('collection',))


def test_legacy_parity_failure_blocks_full_certification(monkeypatch):
    monkeypatch.setattr(a.legacy,'assess',lambda *_x,**_k:{'status':'INCOMPLETE'})
    monkeypatch.setattr(a,'signal_differential',lambda *_x,**_k:{
        'status':'PASS_DIFFERENTIAL','actualSkips':10,'registeredConsumersByClass':{'display':1},
        'stages':[],'skipKinds':{'display':10},'failures':[]})
    result=a.accept(ROOT,profile='smoke')
    assert result['status']=='BLOCKED'
    assert result['failures']==['LEGACY_CALCULATOR_PARITY_INCOMPLETE']
    assert result['boundaries']['productionPromotionAuthorized'] is False
    assert result['boundaries']['productionSignalDefault']=='DISABLED'
    assert result['signalDifferential']['status']=='PASS_DIFFERENTIAL'


def test_skipping_independent_legacy_parity_cannot_certify(monkeypatch):
    monkeypatch.setattr(a,'signal_differential',lambda *_x,**_k:{
        'status':'PASS_DIFFERENTIAL','actualSkips':10,'stages':[],
        'registeredConsumersByClass':{'display':1},'failures':[]})
    result=a.accept(ROOT,profile='smoke',legacy_baseline=False)
    assert result['status']=='BLOCKED'
    assert result['legacyParity'] is None
    assert 'INDEPENDENT_FUNCTIONAL_PARITY_NOT_RUN' in result['failures']
