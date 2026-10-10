"""Bridge AI's legal action vocabulary follows the abstract commitment lifecycle."""
import json
from pathlib import Path

import pytest

from main_computer.tactical_ai_service import TacticalAIService, TacticalAIError


class Response:
    def __init__(self, payload):self.raw=json.dumps(payload).encode()
    def __enter__(self):return self
    def __exit__(self,*args):return None
    def read(self):return self.raw


def scenario(phase='idle',range_m=2500,speed=2,hull=100):
    return {'schema':'game.bridgeCaptainObservation.v1','captainId':'captain.beta',
     'shipId':'ship.beta','mission':'pursue-main-ship-and-seek-boarding-range',
     'simulationSeconds':61,'rangeM':range_m,'radialVelocityMps':-speed,
     'relativePositionM':[range_m,0],'relativeVelocityMps':[-speed,0],
     'targetHullPercent':hull,'transporterMaxRangeM':2500,
     'boarding':{'phase':phase,'boarders':'deployed' if phase=='deployed' else 'aboard',
                 'completeAtSeconds':None}}


def ready(tmp_path):
    svc=TacticalAIService(tmp_path)
    svc._performance_ready=True;svc._model_loaded=True
    svc._adapter_evaluate_url='http://adapter.test/captain/evaluate'
    svc._adapter_health={'checkpointId':'real-checkpoint','checkpointSha256':'trusted-sha'}
    svc._adapter_alive=lambda:True
    svc._manager_status=lambda:{'ok':True,'running':True,'backend_ready':True,'model_loaded':True,'checkpoint_validated':True}
    return svc


def model_choice(monkeypatch,svc,observation,desired):
    recorded=[]
    def perform(request,timeout):
        body=json.loads(request.data.decode());recorded.append(body)
        controls=body['tacticalControls']
        ids=[o['id'] for o in controls if o.get('boardingAction')==desired]
        chosen=ids[0] if ids else 'beta-c0'
        results=[{'choice': chosen if chosen in [q['optionA'],q['optionB']] else q['optionA'],
                  'margin':0} for q in body['questions']]
        return Response({'schema':'game.captainDecisionResponse.v6','answers':results})
    monkeypatch.setattr('main_computer.tactical_ai_service.urllib.request.urlopen',perform)
    decision=svc.decide_bridge_captain(observation)
    return decision,recorded[0]['tacticalControls'],recorded[0]


def test_in_range_stabilized_model_can_initiate(monkeypatch,tmp_path):
    decision,choices,request=model_choice(monkeypatch,ready(tmp_path),scenario(), 'initiate')
    assert decision['actionType']=='boarding' and decision['boardingAction']=='initiate'
    assert decision['modelReceipt']['checkpointSha256']=='trusted-sha'
    assert len(choices)==6 and len(request['questions'])==15
    assert 'Deployment takes 30s' in request['semanticContext']['text']


def test_deployed_captain_must_choose_recall_or_abandon_before_withdraw(monkeypatch,tmp_path):
    for selected in ('recall','abandon'):
        decision,choices,request=model_choice(monkeypatch,ready(tmp_path),scenario('deployed',hull=50),selected)
        assert decision['boardingAction']==selected
        assert len(choices)==6 and len(request['questions'])==15
        assert {'recall','abandon'} == {x.get('boardingAction') for x in choices if x.get('actionType')=='boarding'}
        assert 'withdraw' not in {x.get('maneuver') for x in choices}


def test_no_teleport_from_outside_range_or_high_relative_speed(monkeypatch,tmp_path):
    for obs in (scenario(range_m=2501), scenario(speed=6), scenario(phase='deploying')):
        _,choices,_=model_choice(monkeypatch,ready(tmp_path),obs,'initiate')
        assert not any(x.get('actionType')=='boarding' for x in choices)


def test_complete_evacuations_allow_retreat(monkeypatch,tmp_path):
    for phase in ('recovered','abandoned'):
        _,choices,_=model_choice(monkeypatch,ready(tmp_path),scenario(phase), 'missing')
        assert any(x.get('maneuver')=='withdraw' for x in choices)
        assert not any(x.get('boardingAction') for x in choices)


def test_invalid_boarding_state_rejected_before_adapter(monkeypatch,tmp_path):
    monkeypatch.setattr('main_computer.tactical_ai_service.urllib.request.urlopen',
                        lambda *_args,**_kwargs:pytest.fail('invalid state reached model'))
    with pytest.raises(TacticalAIError,match='BOARDING_STATE_INVALID'):
        ready(tmp_path).decide_bridge_captain(scenario(phase='on-mars'))

@pytest.mark.parametrize('phase,range_m,speed,target_choice,expected_pairs', [
 ('idle',2500,2,'initiate',15),
 ('deployed',2050,1,'recall',15),
 ('deployed',2050,1,'abandon',15),
 ('idle',2700,2,'approach',10),
 ('recovered',2050,1,'withdraw',10),
])
def test_native_9765_protocol_replicates_game_candidate_catalog(
        monkeypatch, phase, range_m, speed, target_choice, expected_pairs):
    import importlib.util
    path=Path(__file__).resolve().parents[1] / 'game_projects/webgl-demo/tools/space_captain_bridge_nanojev_live_endpoint_smoke.py'
    spec=importlib.util.spec_from_file_location('direct_manager_boarding_catalog',path)
    direct=importlib.util.module_from_spec(spec);spec.loader.exec_module(direct)
    obs=scenario(phase=phase,range_m=range_m,speed=speed)
    calls=[]
    def respond(url,data=None,timeout=1):
        if url.endswith('/api/health'):
            return {'ready':True,'model_family':'nanojev-clef','release_name':'real-checkpoint',
                    'release_manifest_sha256':'trusted-sha'}
        assert url.endswith('/api/evaluate-batch')
        assert data['schema']=='nanojev.pairwise-batch.v1'
        calls.append(data)
        ids=set()
        for q in data['questions']:
            for c in q['candidates']:
                if target_choice in c['text'].lower() or (target_choice=='approach' and 'pursue' in c['text'].lower()):
                    ids.add(c['id'])
        assert len(ids)==1,(target_choice,ids)
        preferred=next(iter(ids))
        return {'schema':'nanojev.pairwise-batch-result.v1',
                'model':{'release_name':'real-checkpoint','release_manifest_sha256':'trusted-sha'},
                'results':[{'id':q['id'],
                            'choice':preferred if preferred in [c['id'] for c in q['candidates']]
                              else q['candidates'][0]['id'], 'margin':0}
                           for q in data['questions']]}
    monkeypatch.setattr(direct,'read_json',respond)
    result=direct.direct_nanojev_captain_decision('http://127.0.0.1:9765',obs,
        {'checkpointId':'real-checkpoint','checkpointSha256':'trusted-sha'},10)
    assert result['modelReceipt']['questionCount']==expected_pairs
    assert result['modelReceipt']['checkpointSha256']=='trusted-sha'
    assert result['boardingAction'] if target_choice in ('initiate','recall','abandon') else result['maneuver']
    assert (result['boardingAction'] if result['actionType']=='boarding' else result['maneuver'])==target_choice
    assert len(calls)==1
