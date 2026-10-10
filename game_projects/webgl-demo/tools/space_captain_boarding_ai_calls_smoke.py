#!/usr/bin/env python3
"""Replay real first-encounter observations through NanoJev's working 9765 batch API.

The observations come from the deterministic production authority, not authored
stand-ins. Tests real, checkpoint-backed model choices for the same moments:
boarding eligibility, crew commitment, recovery versus abandonment, and retreat.
This does NOT claim model orders were issued in a running Chromium game.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIVE_SCRIPT = HERE / 'space_captain_bridge_nanojev_live_endpoint_smoke.py'
DETERMINISTIC_SCRIPT = HERE / 'space_captain_boarding_commitment_smoke.py'
spec = importlib.util.spec_from_file_location('bridge_nanojev_endpoint', LIVE_SCRIPT)
assert spec and spec.loader
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)




def validate_stage_observation(stage: dict) -> None:
    """Fail *before* warming NanoJev if the replay violates its input contract."""
    observation = stage.get('observation')
    label = f"{stage.get('strategy', '?')}/{stage.get('stage', '?')}"
    if not isinstance(observation, dict):
        raise RuntimeError(f"BOARDING_AI_REPLAY_OBSERVATION_MISSING: {label}")
    if observation.get('schema') != 'game.bridgeCaptainObservation.v1' or (
            observation.get('captainId'), observation.get('shipId')) != ('captain.beta', 'ship.beta'):
        raise RuntimeError(f"BOARDING_AI_REPLAY_AUTHORITY_INVALID: {label}")
    for key in ('simulationSeconds', 'rangeM', 'radialVelocityMps',
                'relativeSpeedMps', 'targetHullPercent', 'transporterMaxRangeM'):
        value = observation.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise RuntimeError(f"BOARDING_AI_REPLAY_FIELD_REQUIRED: {label}: {key}")
    if observation['transporterMaxRangeM'] <= 0:
        raise RuntimeError(f"BOARDING_AI_REPLAY_TRANSPORTER_RANGE_INVALID: {label}")
    if observation['rangeM'] < 0 or observation['relativeSpeedMps'] < 0:
        raise RuntimeError(f"BOARDING_AI_REPLAY_PHYSICS_INVALID: {label}")
    for key in ('relativePositionM', 'relativeVelocityMps'):
        values = observation.get(key)
        if not isinstance(values, list) or len(values) != 2 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in values
        ):
            raise RuntimeError(f"BOARDING_AI_REPLAY_VECTOR_INVALID: {label}: {key}")
    if abs(math.hypot(*observation['relativePositionM']) - observation['rangeM']) > max(0.01, observation['rangeM'] * 1e-6):
        raise RuntimeError(f"BOARDING_AI_REPLAY_RANGE_MISMATCH: {label}")
    boarding = observation.get('boarding')
    if not isinstance(boarding, dict) or boarding.get('phase') not in (
            'idle', 'deploying', 'deployed', 'recalling', 'recovered', 'abandoning', 'abandoned'):
        raise RuntimeError(f"BOARDING_AI_REPLAY_BOARDING_STATE_INVALID: {label}")

def stage_observations() -> list[dict]:
    task = subprocess.run(['python', str(DETERMINISTIC_SCRIPT)], capture_output=True,
                          text=True, timeout=40, check=True)
    summary = json.loads(task.stdout)
    if summary.get('ok') is not True:
        raise RuntimeError('Deterministic scenario not green: ' + str(summary.get('failedChecks')))
    stages = []
    for strategy in ('recall','abandon'):
        for stage in summary['metrics'][strategy]['decisionObservations']:
            stages.append({'strategy': strategy, **stage})
    for stage in stages:
        validate_stage_observation(stage)
    return stages


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8765')
    parser.add_argument('--manager-url',default=None)
    parser.add_argument('--no-prepare',action='store_true')
    parser.add_argument('--timeout-seconds',type=float,default=40)
    args=parser.parse_args()
    summary={'schema':'game.spaceCaptainBoardingNanoJevCalls.v1','ok':False,
             'gameplayOrderIssued':False,'gameIntegrationVerified':False,'stages':[]}
    try:
        stages=stage_observations()
        # Existing smoke manages preparation and checkpoint verification.
        prep_args=argparse.Namespace(base_url=args.base_url,manager_url=args.manager_url,
            no_prepare=args.no_prepare,probe_route_only=False,game_route=False,
            time_step_seconds=5,consecutive_passes=3,warmup_timeout_seconds=120,
            startup_timeout_seconds=180,request_timeout_seconds=300,
            ready_timeout_seconds=420,decision_timeout_seconds=args.timeout_seconds,
            http_timeout_seconds=10,poll_seconds=2)
        prepared=bridge.run(prep_args)
        if not prepared['ok']:
            raise RuntimeError('NanoJev preparation/direct inference failed: '+str(prepared.get('error')))
        manager_url=prepared['managerUrl']
        expected=prepared['gameStatus']['adapter']
        matches=0
        trusted_count=0
        for stage in stages:
            observation=stage['observation']
            model=bridge.direct_nanojev_captain_decision(
                manager_url, observation, expected, args.timeout_seconds, counterbalance=True)
            trusted = model['selectionTrusted']
            selected=(model['boardingAction'] if model.get('actionType')=='boarding' else model['maneuver']) if trusted else None
            equal=(selected == stage['expectedAction']) if trusted else None
            matches+=int(equal is True)
            trusted_count+=int(trusted)
            summary['stages'].append({
                'strategy':stage['strategy'],'stage':stage['stage'],
                'simulationSeconds':observation['simulationSeconds'],
                'boardingState':observation['boarding']['phase'],
                'rangeM':observation['rangeM'], 'expectedDeterministicAction':stage['expectedAction'],
                'nanojevSelectedAction':selected, 'matchesDeterministic':equal,
                'selectionTrusted':trusted,
                'modelReceipt':model['modelReceipt'],
                'candidateScores':model['candidateScores'],
                'decisionDiagnostics':model['diagnostics'],
            })
        summary.update({'ok':trusted_count == len(stages),'directModelVerified':True,
            'modelCalls':len(stages),'modelDecisions':trusted_count,
            'inconclusiveDecisions':len(stages)-trusted_count,
            'matchesDeterministic':matches,
            'diagnostic':('POSITION_ORDER_BIAS_OR_INCONCLUSIVE_NANOJEV_SELECTION' if trusted_count < len(stages) else None),
            'checkpointId':expected['checkpointId'],
            'checkpointSha256':expected['checkpointSha256']})
    except Exception as exc:
        summary['error']=f'{type(exc).__name__}: {exc}'
    print(json.dumps(summary,indent=2))
    return 0 if summary['ok'] else 1

if __name__=='__main__':
    raise SystemExit(main())
