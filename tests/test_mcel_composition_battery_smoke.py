"""Cross-application blast-radius smoke gate; fails closed on untested app types."""
from __future__ import annotations

from copy import deepcopy

import pytest

from tools import mcel_composition_battery_smoke as battery
from tools.mcel_composition_negotiation_smoke import SmokeRefusal, negotiate
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app


def test_complete_registry_and_separate_slh_are_accounted_for():
    result=battery.run_battery(browser=False)
    assert result['coverage']['inventory']==21
    assert result['coverage']['semanticApps']==5
    assert result['coverage']['legacyApps']==15
    assert result['coverage']['additionalMCELTargets']==2
    assert len(result['apps'])==21
    assert {a['app'] for a in result['auxiliary']}=={'slh-local','slh-network'}
    assert not result['productionPromotion']
    assert result['productionGate']=='BLOCKED'


def test_all_seven_semantic_targets_receive_general_candidate_sets():
    result=battery.run_battery(browser=False)
    assert {a['app'] for a in result['apps']+result['auxiliary'] if
            a['negotiation']=='ELIGIBLE_TRIAL'}=={
        'calculator','file-explorer','task-manager','code-editor',
        'chat-console','slh-local','slh-network'}
    assert result['coverage']['negotiationEligible']==7
    assert result['coverage']['negotiationUnsupported']==0
    assert result['productionGate']=='BLOCKED'  # Browser evidence absent.


def test_known_missing_assets_do_not_become_fake_success():
    report=battery.run_battery(browser=False)
    for app in ('webgl','onlyoffice'):
        target=next(x for x in report['apps'] if x['app']==app)
        assert target['promotionBlockers']
        assert 'legacyFragmentSHA256' not in target['evidence']


def test_battery_refuses_new_unregistered_semantic_application(monkeypatch):
    changed=deepcopy(battery.SEMANTIC_LOADERS)
    changed.pop('code-editor')
    monkeypatch.setattr(battery,'SEMANTIC_LOADERS',changed)
    with pytest.raises(AssertionError,match='inventory has changed'):
        battery.run_battery(browser=False)


def test_general_solver_accepts_multi_region_file_manager():
    from tools.mcel_general_negotiation_smoke import negotiate_application
    ir=compile_semantic_app('file-manager')
    result=negotiate_application(ir,1260)
    assert result['winner']['arrangement']=='three-region'
    assert len(result['regions'])>=3
    assert result['candidateCount']>1
    assert not result['productionPromotion']


@pytest.fixture(scope='module')
def observed_battery():
    pytest.importorskip('playwright')
    return battery.run_battery(browser=True)


def test_full_battery_browser_guards_all_canonical_routes(observed_battery):
    result=observed_battery
    assert result['coverage']['shellRoutesChecked']==63
    assert len(result['shell'])==3
    assert all(s['status']=='GUARD_PASS' for s in result['shell'])
    assert {v for s in result['shell'] for v in (x['app'] for x in s['routes'])}=={
        x['app'] for x in result['apps']}


def test_all_semantic_hosts_mount_and_preserve_geometry(observed_battery):
    result=observed_battery
    assert result['coverage']['browserObservations']==21  # 5 shell + 2 SLH, 3 widths
    assert all(x['ready'] and x['geometryGuardAccepted'] for x in result['semanticBrowser'])
    assert all(x['status']=='GUARD_PASS' for x in result['semanticBrowser'])
    assert result['coverage']['guardFailures']==0
    assert result['coverage']['negotiationEligible']==7
    assert result['coverage']['liveTrials']==14
    assert result['coverage']['fullyTrialedTargets']==7
    assert result['coverage']['partiallyTrialedTargets']==0
    assert result['coverage']['unsupportedTopologies']==0
    assert result['coverage']['rejectedTrials']==0
    assert result['experimentalGate']=='PASS'
    assert all(x['graphUpdatedAfterReflow'] for x in result['semanticBrowser'])
    assert any(x['negotiationTrial']['childComposition']['moved'] for x in result['semanticBrowser'])
    assert result['productionGate']=='BLOCKED'


def test_legacy_surfaces_guarded_without_misclaiming_execution(observed_battery):
    result=observed_battery
    assert result['coverage']['legacyMarkupObservations']==39  # 13/15 available, 3 widths
    assert all(x['status']=='GUARD_PASS' for x in result['legacyBrowser'])
    assert all('execution/geometry not certified' in x['scope'] for x in result['legacyBrowser'])
    assert all(x['runtime']=='NOT_VERIFIED' for x in result['apps'] if x['app'] in {'webgl','onlyoffice'})
    assert result['productionGate']=='BLOCKED'


def test_corrupt_legacy_dom_fails_guard():
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            result=battery._legacy_markup_browser(browser,'synthetic-duplicate',
                '<section id="collision"><p id="collision">bad</p></section>',390)
            assert result['status']=='FAIL'
            assert result['uniqueIds'] is False
        finally:
            browser.close()
