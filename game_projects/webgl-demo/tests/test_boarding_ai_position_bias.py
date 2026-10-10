"""Position counterbalancing must not confuse 'first candidate' with intelligence."""
from __future__ import annotations

import importlib.util
import pathlib
import pytest

SOURCE = pathlib.Path(__file__).resolve().parents[1] / 'tools/space_captain_bridge_nanojev_live_endpoint_smoke.py'
spec = importlib.util.spec_from_file_location('bridge_bias_test', SOURCE)
bridge = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(bridge)


def observation():
    return {
        'schema': 'game.bridgeCaptainObservation.v1',
        'captainId': 'captain.beta', 'shipId': 'ship.beta',
        'simulationSeconds': 21, 'rangeM': 2082.48,
        'radialVelocityMps': 0.0, 'relativeVelocityMps': [0.0, 0.0],
        'relativePositionM': [2082.48, 0.0], 'relativeSpeedMps': 0.0,
        'transporterMaxRangeM': 2500, 'targetHullPercent': 100,
        'boarding': {'phase': 'idle', 'boarders': 'aboard'},
        'mission': 'pursue-main-ship-and-seek-boarding-range',
    }


def model_reply(monkeypatch, vote):
    requests=[]
    def get(url, request=None, timeout=30):
        if url.endswith('/api/health'):
            return {'ready': True, 'model_family':'nanojev-clef',
                'release_name':'test-checkpoint','release_manifest_sha256':'abcsha'}
        assert url.endswith('/api/evaluate-batch')
        requests.append(request)
        rows = []
        for q in request['questions']:
            candidates=q['candidates']
            win=vote(candidates)
            rows.append({'id':q['id'],'choice':win, 'margin': 0.2})
        return {'schema':'nanojev.pairwise-batch-result.v1',
                'model':{'release_name':'test-checkpoint','release_manifest_sha256':'abcsha'},
                'results':rows,'metrics':{'model_latency_ms':99}}
    monkeypatch.setattr(bridge,'read_json',get)
    return requests


def decide(counterbalance):
    return bridge.direct_nanojev_captain_decision('http://fake',observation(),
              {'checkpointId':'test-checkpoint','checkpointSha256':'abcsha'},
              timeout=10,counterbalance=counterbalance)


def test_initial_version_would_mistake_first_option_bias_for_approach(monkeypatch):
    requests=model_reply(monkeypatch,lambda options: options[0]['id'])
    result=decide(counterbalance=False)
    assert result['selectionTrusted'] is True  # demonstrates original weakness
    assert result['maneuver']=='approach'
    assert len(requests[0]['questions']) == 15
    assert result['diagnostics']['firstCandidateWinRate']==1


def test_reversal_detects_always_choose_first_and_withholds_a_maneuver(monkeypatch):
    requests=model_reply(monkeypatch,lambda options: options[0]['id'])
    result=decide(counterbalance=True)
    assert result['ok'] is True   # transport/verified model call was real
    assert result['selectionTrusted'] is False
    assert result['maneuver'] is None and result['boardingAction'] is None
    assert result['modelReceipt']['chosenCandidateId'] is None
    assert result['diagnostics']['positionDisagreements']==15
    assert result['diagnostics']['consistentPairs']==0
    assert result['diagnostics']['firstCandidateWinRate']==1
    assert len(requests[0]['questions'])==30
    assert result['modelReceipt']['questionCount']==30


def test_reverse_order_preserves_real_action_preference(monkeypatch):
    requests=model_reply(monkeypatch,lambda opts: (
        max(v['id'] for v in opts)))
    result=decide(counterbalance=True)
    assert result['selectionTrusted'] is True
    assert result['actionType']=='boarding'
    assert result['boardingAction']=='initiate'
    assert result['diagnostics']['consistentPairs']==15
    assert result['diagnostics']['positionDisagreements']==0
    assert result['modelReceipt']['chosenCandidateId']=='beta-c5'
    assert len(requests)==1


def test_corrupt_choice_fails_before_selection(monkeypatch):
    model_reply(monkeypatch,lambda opts:'unauthorized-candidate')
    with pytest.raises(bridge.EndpointError, match='INVALID_CHOICE'):
        decide(counterbalance=True)


def test_bad_question_id_fails_before_selection(monkeypatch):
    def mock(url,request=None,timeout=30):
        if url.endswith('/api/health'):
            return {'ready':True,'model_family':'nanojev-clef','release_name':'test-checkpoint','release_manifest_sha256':'abcsha'}
        return {'schema':'nanojev.pairwise-batch-result.v1','model':{'release_name':'test-checkpoint','release_manifest_sha256':'abcsha'},
                'results':[{'id':'invalid','choice':q['candidates'][0]['id'],'margin':0.1} for q in request['questions']]}
    monkeypatch.setattr(bridge,'read_json',mock)
    with pytest.raises(bridge.EndpointError,match='QUESTION_ID_MISMATCH'):
        decide(counterbalance=True)
