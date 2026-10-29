"""Patch 14.6 acceptance: the claimed advantages must not be fabricated.

Uses actual fixed Task Manager fixture, actual catalog, universal composer,
real Chromium DOM and canonical record provenance; never a mock renderer.
"""
from __future__ import annotations

import json

from main_computer.mcel_knowledge.universal_layout import PROFILE
from tools.mcel_patch14_6_promise_verification_frob import (
    CONTROL_SEED, FIXTURE, OBS, STATES, TASKS, WIDTHS,
    adaptation_checks, blind_control, scenario, verify,
)


def test_equal_budget_universal_arms_are_different_only_in_order():
    for state in STATES:
        semantic = scenario(state)['plan']
        blind = blind_control(semantic)
        assert semantic['profile'] == blind['profile'] == PROFILE
        assert semantic['readOnly'] is blind['readOnly'] is True
        assert semantic['productionPromotion'] is blind['productionPromotion'] is False
        assert semantic['collection']['rows'] == blind['collection']['rows']
        assert semantic['tree'] == blind['tree']
        assert semantic['regions'] == blind['regions']
        assert semantic['summaryFacts'] == blind['summaryFacts']
        assert sorted((f['key'], f['kind'], f['tier'], f.get('exact')) for f in semantic['collection']['spec']['fields']) == \
               sorted((f['key'], f['kind'], f['tier'], f.get('exact')) for f in blind['collection']['spec']['fields'])
        assert semantic['decision']['fields'] != blind['decision']['fields']
        assert blind_control(semantic) == blind  # deterministic; no per-case search
        assert CONTROL_SEED


def test_semantic_intervention_is_causal_and_content_is_not_structure():
    source=json.loads(FIXTURE.read_text(encoding='utf-8'))
    result=adaptation_checks(source)
    assert result['contentChangesWithoutDecisionChange']
    assert result['contentChangeActuallyObserved']
    assert result['purposeChangedSignature']
    assert result['purposeChangesRegionSet']
    assert result['retainedProfile']
    semantic=result['semanticRetyping']
    assert semantic['sourceByteEquivalent']
    assert semantic['semanticSignatureChanged']
    assert semantic['fieldOrderChanged']
    assert semantic['stateCountRemoved']
    assert semantic['declaredIntervention']=={'property':'state','from':'mc.process.state','to':'mc.text'}


def test_rendered_task_manager_full_browser_verification(tmp_path):
    report=verify(tmp_path/'proof',screenshots=False)
    assert report['engineeringStatus']=='PASS',report['errors']
    assert report['promiseVerdict'] in ('DIRECTIONAL_PROXY_ADVANTAGE','NO_MEASURED_PROXY_ADVANTAGE')
    assert report['operatorActionsExecuted'] is False
    assert report['productionPromotion'] is False
    assert report['profile']==PROFILE
    assert len(report['cases'])==len(STATES)*len(WIDTHS)
    assert len(report['adaptationBrowser'])==len(WIDTHS)
    assert report['retrievalSummary']['comparisons']==len(STATES)*len(WIDTHS)*len(TASKS)
    assert sum(report['retrievalSummary'][x] for x in ('semantic-win','blind-win','tie')) == report['retrievalSummary']['comparisons']
    for case in report['cases']:
        assert case['status']=='PASS' and case['equivalentControls']
        assert not case['networkRequests'] and not case['browserErrors']
        for arm in ('blind-order','semantic'):
            data=case['arms'][arm]
            assert data['exactEvidencePassed']
            assert len(data['acquisitions'])==len(TASKS)
            assert all(v['displayAccurate'] for v in data['acquisitions'])
            assert data['proof']['collapsed'] and data['proof']['rawHiddenVisually']
            assert data['proof']['disclosureOpens'] and data['proof']['provenanceOpens']
            assert data['proof']['sourceIdentityMatches']
            assert data['proof']['provenanceConsistent']
            assert data['proof']['keyboardFocusPreserved']
            assert data['proof']['structuralRefused']
            assert data['proof']['rejectsUnauthorizedOperations']
            assert data['proof']['rejectsProductionPromotion']
            assert data['proof']['rawKeys']  # exact source still recoverable
            assert not data['proof']['documentOverflow']
    for item in report['adaptationBrowser']:
        assert item['contentAccepted'] and item['sourceChangedInDOM']
        assert item['structuralRefused'] and item['purposeRefused']
        assert item['remountDifferent'] and item['stateSummaryRemoved']
        assert not item['overflow']
    written=json.loads((tmp_path/'proof/report.json').read_text(encoding='utf-8'))
    assert written['engineeringStatus']=='PASS'
    assert 'participant time-to-answer' in ' '.join(written['limitations'])
