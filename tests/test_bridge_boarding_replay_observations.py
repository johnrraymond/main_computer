"""Contract tests for the production first-encounter replay sent to NanoJev.

The HTTP responses in this file are mocked; successful tests do not mean the
real model has run, only that all six real replay observations can reach the
native pairwise inference protocol without missing required fields.
"""
import copy
import importlib.util
import json
import math
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'game_projects/webgl-demo/tools/space_captain_boarding_ai_calls_smoke.py'
spec = importlib.util.spec_from_file_location('boarding_ai_calls_replay_contract', SCRIPT)
assert spec and spec.loader
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


def test_six_deterministic_stage_observations_share_production_capability():
    stages = replay.stage_observations()
    assert len(stages) == 6
    assert [(s['strategy'], s['stage'], s['expectedAction']) for s in stages] == [
        ('recall', 'boarding-eligible', 'initiate'),
        ('recall', 'boarders-committed', 'recall'),
        ('recall', 'ready-to-withdraw', 'withdraw'),
        ('abandon', 'boarding-eligible', 'initiate'),
        ('abandon', 'boarders-committed', 'abandon'),
        ('abandon', 'ready-to-withdraw', 'withdraw'),
    ]
    assert all(s['observation']['transporterMaxRangeM'] == 2500 for s in stages)
    assert all(s['observation']['captainId'] == 'captain.beta' for s in stages)
    assert all(s['observation']['shipId'] == 'ship.beta' for s in stages)
    assert all(s['observation']['rangeM'] <= 2500 for s in stages)
    assert all(s['observation']['relativeSpeedMps'] <= 5 for s in stages)
    assert all(s['observation']['boarding']['phase'] in ('idle','deployed','recovered','abandoned') for s in stages)


@pytest.mark.parametrize('name,updated', [
    ('transporterMaxRangeM', None),
    ('transporterMaxRangeM', float('nan')),
    ('rangeM', float('inf')),
    ('relativePositionM', [0, 0]),
    ('boarding', None),
])
def test_missing_or_invalid_observation_is_rejected_before_model(name, updated):
    stage = copy.deepcopy(replay.stage_observations()[0])
    if updated is None:
        stage['observation'].pop(name)
    else:
        stage['observation'][name] = updated
    with pytest.raises(RuntimeError, match='BOARDING_AI_REPLAY_'):
        replay.validate_stage_observation(stage)


def test_all_six_observations_construct_native_batch_requests(monkeypatch):
    # This is a mocked NanoJev response exercising the *real* native request
    # builder. It does not assert the actual model would choose the same action.
    stages = replay.stage_observations()
    requests = []
    desired = iter(s['expectedAction'] for s in stages)
    def respond(url, data=None, timeout=40):
        if url.endswith('/api/health'):
            return {'ready': True, 'model_family': 'nanojev-clef',
                    'release_name': 'fixture-checkpoint',
                    'release_manifest_sha256': 'fixture-sha'}
        assert url.endswith('/api/evaluate-batch')
        assert data['schema'] == 'nanojev.pairwise-batch.v1'
        assert 'transporter max range=2500.0m' in data['shared_context']['text']
        assert 'boarding phase=' in data['shared_context']['text']
        choices = {c['id']: c['text'] for q in data['questions'] for c in q['candidates']}
        expected = next(desired)
        options = [name for name, description in choices.items() if
                   expected in description.lower() or
                   (expected == 'initiate' and 'initiate boarding' in description.lower())]
        # 'recall', 'abandon', and 'withdraw' must be explicitly offered by the
        # corresponding lifecycle state, not inserted by the test.
        assert len(options) == 1, (expected, choices)
        wanted = options[0]
        requests.append((expected, data))
        return {'schema':'nanojev.pairwise-batch-result.v1',
                'model':{'release_name':'fixture-checkpoint','release_manifest_sha256':'fixture-sha'},
                'results':[{'id': q['id'], 'choice': wanted if wanted in [
                    c['id'] for c in q['candidates']] else q['candidates'][0]['id'], 'margin': 0.1}
                    for q in data['questions']]}
    monkeypatch.setattr(replay.bridge, 'read_json', respond)
    for stage in stages:
        decision = replay.bridge.direct_nanojev_captain_decision(
            'http://127.0.0.1:9765',stage['observation'],
            {'checkpointId':'fixture-checkpoint','checkpointSha256':'fixture-sha'},40)
        chosen = decision['boardingAction'] if decision['actionType'] == 'boarding' else decision['maneuver']
        assert chosen == stage['expectedAction']
        assert decision['modelReceipt']['checkpointSha256'] == 'fixture-sha'
    assert len(requests) == 6
    assert [len(data['questions']) for _, data in requests] == [15,15,10,15,15,10]
