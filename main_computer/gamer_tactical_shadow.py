"""Read-only GAMER V33/V34 -> Space Captain bridge. No action publishing.

The supplied game payoffs are EXTERNAL scenario assumptions, not derivable
from range/velocity sensors. A learned T08 choice is a shadow proposal, not a
Space Captain tactical-v6 decision, and cannot initiate boarding or helm orders.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

REQUEST_SCHEMA = 'game.bridgeGamerShadowRequest.v33'
RESPONSE_SCHEMA = 'game.bridgeGamerShadowResponse.v33'


def need(condition, reason):
    if not condition:
        raise ValueError('GAMER_V33_CAPTAIN_' + reason)


def real_number(observation, name, low, high):
    val = observation.get(name)
    need(not isinstance(val, bool) and isinstance(val, (int, float)) and
         math.isfinite(float(val)) and low <= float(val) <= high,
         'BAD_' + name)
    return float(val)


def validate_observation(observation):
    need(isinstance(observation, dict) and
         observation.get('schema') == 'game.bridgeCaptainObservation.v1',
         'BAD_OBSERVATION_SCHEMA')
    need(observation.get('captainId') == 'captain.beta' and
         observation.get('shipId') == 'ship.beta', 'WRONG_CAPTAIN')
    need(observation.get('mission') == 'pursue-main-ship-and-seek-boarding-range',
         'UNSUPPORTED_MISSION')
    sim = real_number(observation, 'simulationSeconds', 0, 1e9)
    distance = real_number(observation, 'rangeM', 0, 1e12)
    real_number(observation, 'radialVelocityMps', -1e7, 1e7)
    real_number(observation, 'targetHullPercent', 0, 100)
    real_number(observation, 'transporterMaxRangeM', 200, 1e9)
    for name in ('relativePositionM', 'relativeVelocityMps'):
        values = observation.get(name)
        need(isinstance(values, list) and len(values) == 2, 'BAD_' + name)
        for value in values:
            need(not isinstance(value, bool) and isinstance(value, (int, float))
                 and math.isfinite(float(value)) and abs(float(value)) <= 1e12,
                 'BAD_' + name)
    x, y = observation['relativePositionM']
    need(abs(math.hypot(x, y) - distance) <= max(0.01, distance * 1e-6),
         'RELATIVE_RANGE_MISMATCH')
    boarding = observation.get('boarding', {'phase': 'idle', 'boarders': 'aboard'})
    need(isinstance(boarding, dict) and boarding.get('phase') in
         ('idle', 'deploying', 'deployed', 'recalling', 'recovered',
          'abandoning', 'abandoned'), 'BAD_BOARDING_PHASE')
    # The learned T08 contest/yield binary does not encode recalling or
    # abandoning crews; only idle/recovered/abandoned allows both actions.
    need(boarding['phase'] in ('idle', 'recovered', 'abandoned') and
         boarding.get('boarders', 'aboard') == 'aboard',
         'BOARDERS_COMMITTED_ABSTAIN')
    return sim, distance


def evaluate(root, request: dict[str, Any], *, device='cuda'):
    need(isinstance(request, dict) and request.get('schema') in
         (REQUEST_SCHEMA, 'game.bridgeGamerShadowRequest.v34'),
         'BAD_REQUEST_SCHEMA')
    need(set(request) == {'schema', 'observation', 't08Prompt', 'run'},
         'UNRECOGNIZED_REQUEST_FIELDS')
    sim, distance = validate_observation(request['observation'])
    prompt = request['t08Prompt']
    need(isinstance(prompt, str) and prompt.strip(), 'PROMPT_REQUIRED')
    run = Path(request['run']).resolve()
    reports_root = (Path(root).resolve() / 'runtime' / 'reports' / 'gamer-v1').resolve()
    need(run != reports_root and reports_root in run.parents,
         'RUN_OUTSIDE_GAMER_REPORTS')
    if request['schema'] == 'game.bridgeGamerShadowRequest.v34':
        from tools import nanojev_gamer_v1_v34_grounded_game_learning as engine
    else:
        from tools import nanojev_gamer_v1_v33_game_learning as engine
    prediction = engine.infer(run, prompt, project=root, device=device)
    need(prediction.get('learned') is True and
         prediction['choice'] in ('Contest', 'Yield') and
         prediction.get('scoring_oracle_used') is False and
         prediction.get('production_authority') is False,
         'MODEL_PREDICTION_INVALID')
    choice = prediction['choice']
    suggestion = {'actionType': 'helm',
                  'maneuver': 'approach' if choice == 'Contest' else 'withdraw',
                  'rangeM': None}
    return {'ok': True, 'status': 'PASS_SHADOW_PROPOSAL_NO_ORDER',
            'schema': ('game.bridgeGamerShadowResponse.v34' if
                       request['schema'] == 'game.bridgeGamerShadowRequest.v34'
                       else RESPONSE_SCHEMA), 'captainId': 'captain.beta',
            'shipId': 'ship.beta', 'observationSeconds': sim,
            'observedRangeM': distance, 't08Decision': choice,
            'suggestedLegalIntent': suggestion, 'captainOrder': None,
            'actionPublished': False, 'tacticalV6AuthorityChanged': False,
            'modelReceipt': {'checkpointSha256': prediction['checkpoint_sha256'],
                             'learnedDecision': True, 'oracleUsedForPrediction': False},
            'limits': 'T08 narrative payoffs are supplied externally. No game-sensor-to-payoff model or tactical utility certification.'}
