"""V33: 15/5 variant split, honest learned readout and Captain shadow boundary."""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_v33_game_learning as v33
from main_computer import gamer_tactical_shadow as shadow
from main_computer.tactical_ai_service import TacticalAIService


def _observation():
    return {'schema': 'game.bridgeCaptainObservation.v1',
            'captainId': 'captain.beta', 'shipId': 'ship.beta',
            'mission': 'pursue-main-ship-and-seek-boarding-range',
            'simulationSeconds': 12., 'rangeM': 3000.,
            'radialVelocityMps': -2., 'targetHullPercent': 100.,
            'transporterMaxRangeM': 1500.,
            'relativePositionM': [3000., 0.],
            'relativeVelocityMps': [-2., 0.],
            'boarding': {'phase': 'idle', 'boarders': 'aboard'}}


def test_15_5_whole_surface_holdout_with_no_prompt_overlap():
    a = v33.make_deck('fit', 33)
    b = v33.make_deck('holdout', 33)
    assert len(v33.TRAIN_VARIANTS) == 15
    assert len(v33.TEST_VARIANTS) == 5
    assert len(a) == 360 and len(b) == 120
    assert set(v33.TRAIN_VARIANTS).isdisjoint(v33.TEST_VARIANTS)
    assert {row['variant'] for row in a} == set(v33.TRAIN_VARIANTS)
    assert {row['variant'] for row in b} == set(v33.TEST_VARIANTS)
    assert {row['prompt'] for row in a}.isdisjoint(r['prompt'] for r in b)
    assert a == v33.make_deck('fit', 33)
    assert b == v33.make_deck('holdout', 33)
    assert Counter(r['label'] for r in a) == {0:180, 1:180}
    assert Counter(r['label'] for r in b) == {0:60, 1:60}
    assert all('signed_margin' not in r['prompt'] for r in a + b)


def test_train_dev_does_not_share_the_same_payoff_pair():
    train, dev = v33.fit_partition(v33.make_deck('fit', 42))
    assert len(train) == 270 and len(dev) == 90
    assert {r['pair'] for r in train}.isdisjoint(r['pair'] for r in dev)
    assert Counter(r['label'] for r in train) == {0:135,1:135}


@pytest.mark.parametrize('seed', [33, 117, 2701])
def test_each_pair_reverses_action_when_only_belief_changes(seed):
    for group in ('fit', 'holdout'):
        deck = v33.make_deck(group, seed)
        for i in range(0, len(deck), 2):
            x, y = deck[i:i+2]
            assert x['pair'] == y['pair']
            assert x['variant'] == y['variant']
            assert {x['label'],y['label']} == {0,1}
            assert Fraction(x['signed_margin']) == -Fraction(y['signed_margin'])
            assert x['prompt'] != y['prompt']
            assert x['prompt'].replace('1/4','Q').replace('3/4','Q') == \
                   y['prompt'].replace('1/4','Q').replace('3/4','Q')


def test_oracle_only_scores_and_refuses_tie():
    assert v33.exact_teacher(1, 0, 9, -8, Fraction(3,4)) == 1
    assert v33.exact_teacher(1, 0, 9, -8, Fraction(1,4)) == 0
    with pytest.raises(ValueError, match='AMBIGUOUS_TIE'):
        v33.exact_teacher(1, 0, 1, 0, Fraction(1,2))


def test_model_head_trains_on_features_without_payoff_oracle():
    net = v33.head(torch, 12)
    assert net(torch.zeros(4,12)).shape == (4,2)
    with torch.inference_mode():
        assert len(v33.predict_features(torch,net,np.ones((3,12),dtype=np.float32))) == 3
    with pytest.raises(ValueError, match='INVALID_FIT_SHAPES'):
        v33.train_head(torch,np.ones((2,12),dtype=np.float32),[0,1],
                       np.ones((2,12),dtype=np.float32),[0,1], epochs=1)


def test_paired_acceptance_requires_both_opposite_decisions():
    rows = v33.make_deck('holdout',33)
    assert v33.paired_accuracy(rows,[r['label'] for r in rows]) == (60,60)
    assert v33.paired_accuracy(rows,[0]*len(rows)) == (0,60)
    with pytest.raises(ValueError, match='INCOMPLETE_TEST_PAIRS'):
        v33.paired_accuracy(rows[:-1],[r['label'] for r in rows[:-1]])


def _dummy_lineage():
    return {'model_id':'mock-frozen-qwen', 'v32_state_sha256':'aaa',
            'source_files':{}, 'base_activation':'frozen_base_qwen_layer18_no_lora_adapter',
            'v31_acceptance_sha256':'bbb'}


def _stub_training(monkeypatch, output, *, seed=33):
    seen = []
    make = v33.make_deck
    def traced(split, value):
        seen.append(split)
        return make(split,value)
    monkeypatch.setattr(v33,'make_deck',traced)
    monkeypatch.setattr(v33,'activation',lambda root: _dummy_lineage())
    monkeypatch.setattr(v33,'verify_activation',lambda root,lineage: lineage)
    monkeypatch.setattr(v33,'model_context',lambda model_id,device: (
        torch, object(), SimpleNamespace(eval=lambda: None)))
    monkeypatch.setattr(v33,'capture',lambda model,tok,t,prompts,**kwargs:
        np.ones((len(prompts),12), dtype=np.float32))
    monkeypatch.setattr(v33,'train_head',lambda *args, **kwargs:
        (v33.head(torch,12).state_dict(),1,[{'epoch':1,'dev_exact':90,'dev_total':90}]))
    result = v33.training(output, project=output.parent, device='cpu',seed=seed,epochs=1)
    return result, seen


def test_training_never_opens_test_and_writes_only_shadow_head(tmp_path, monkeypatch):
    out = tmp_path/'runtime/reports/gamer-v1/v33-train'
    result,seen = _stub_training(monkeypatch,out)
    assert seen == ['fit']
    assert result['holdout_variants_unopened'] == 5
    assert not (out/'holdout_consumed.json').exists()
    assert not (out/'holdout_acceptance.json').exists()
    assert v33.read(out/'train_manifest.json')['head_authority']=='experimental_shadow_only'
    _,net=v33.load_head(out,tmp_path)
    assert net[1].weight.shape[1]==12
    with pytest.raises(ValueError,match='OUTPUT_ALREADY_EXISTS'):
        v33.training(out,project=tmp_path,device='cpu')


def test_sealed_audit_one_shot_and_fails_if_policy_is_constant(tmp_path,monkeypatch):
    out=tmp_path/'runtime/reports/gamer-v1/v33-train'
    _stub_training(monkeypatch,out)
    monkeypatch.setattr(v33,'model_context',lambda model_id,device: (
        torch,object(),SimpleNamespace(eval=lambda:None)))
    monkeypatch.setattr(v33,'capture',lambda model,tok,t,prompts,**kwargs:
        np.zeros((len(prompts),12),dtype=np.float32))
    monkeypatch.setattr(v33,'predict_features',lambda t,net,x:[0]*len(x))
    result=v33.audit(out,project=tmp_path,device='cpu')
    assert result['status']=='FAIL_SEALED_HOLDOUT_NO_PROMOTION'
    assert result['exact']==60 and result['paired_reversals_exact']==0
    assert (out/'holdout_consumed.json').exists()
    with pytest.raises(ValueError,match='HOLDOUT_ALREADY_CONSUMED'):
        v33.audit(out,project=tmp_path,device='cpu')
    with pytest.raises(ValueError,match='HOLDOUT_NOT_CERTIFIED'):
        v33.load_head(out,tmp_path,require_audit=True)


def test_sealed_audit_can_pass_only_complete_correct_predictions(tmp_path,monkeypatch):
    out=tmp_path/'runtime/reports/gamer-v1/v33-train'
    _stub_training(monkeypatch,out)
    monkeypatch.setattr(v33,'model_context',lambda model_id,device: (
        torch,object(),SimpleNamespace(eval=lambda:None)))
    monkeypatch.setattr(v33,'capture',lambda model,tok,t,prompts,**kwargs:
        np.zeros((len(prompts),12),dtype=np.float32))
    labels = [r['label'] for r in v33.make_deck('holdout',33)]
    monkeypatch.setattr(v33,'predict_features',lambda t,net,x: labels)
    result=v33.audit(out,project=tmp_path,device='cpu')
    assert result['heldout_pass'] and result['exact']==120
    assert result['paired_reversals_exact']==60
    manifest,head=v33.load_head(out,tmp_path,require_audit=True)
    assert manifest['seed']==33
    (out/'learned_head.pt').write_bytes(b'modified')
    with pytest.raises(ValueError,match='HEAD_CHECKPOINT_CHANGED'):
        v33.load_head(out,tmp_path,require_audit=True)


def _request(root):
    return {'schema':shadow.REQUEST_SCHEMA,'observation':_observation(),
            't08Prompt':'T08 external payoff assumption; choose between Contest and Yield.',
            'run':str(root/'runtime/reports/gamer-v1/v33-train')}


@pytest.mark.parametrize('choice,maneuver', [('Contest','approach'),('Yield','withdraw')])
def test_space_captain_shadow_mapping_never_publishes_order(tmp_path,monkeypatch,choice,maneuver):
    from tools import nanojev_gamer_v1_v33_game_learning as engine
    monkeypatch.setattr(engine,'infer',lambda *a,**k: {
        'choice':choice,'learned':True,'checkpoint_sha256':'00'*32,
        'scoring_oracle_used':False,'production_authority':False})
    service=TacticalAIService(tmp_path)
    result=service.shadow_gamer_hawk_dove(_request(tmp_path),device='cpu')
    assert result['status']=='PASS_SHADOW_PROPOSAL_NO_ORDER'
    assert result['suggestedLegalIntent']['maneuver']==maneuver
    assert result['captainOrder'] is None
    assert result['actionPublished'] is False
    assert result['tacticalV6AuthorityChanged'] is False


def test_shadow_rejects_committed_boarders_before_model_inference(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_v33_game_learning as engine
    monkeypatch.setattr(engine,'infer',lambda *a,**k: pytest.fail('no model inference allowed'))
    req=_request(tmp_path)
    req['observation']['boarding']={'phase':'deployed','boarders':'deployed'}
    with pytest.raises(ValueError,match='BOARDERS_COMMITTED_ABSTAIN'):
        shadow.evaluate(tmp_path,req,device='cpu')


@pytest.mark.parametrize('mutation', [
    lambda x:x.update(captainId='captain.alpha'),
    lambda x:x.update(rangeM=2400),
    lambda x:x.update(relativeVelocityMps=[float('nan'),0]),
    lambda x:x.update(mission='not-the-approved-mission'),
    lambda x:x.update(boarding={'phase':'recalling'}),
])
def test_shadow_rejects_invalid_authority_or_geometry(tmp_path,mutation):
    req=_request(tmp_path)
    mutation(req['observation'])
    with pytest.raises(ValueError,match='GAMER_V33_CAPTAIN_'):
        shadow.evaluate(tmp_path,req,device='cpu')


def test_shadow_rejects_report_path_escape_and_unknown_fields(tmp_path):
    req=_request(tmp_path)
    req['run']=str(tmp_path/'separate-dir')
    with pytest.raises(ValueError,match='RUN_OUTSIDE_GAMER_REPORTS'):
        shadow.evaluate(tmp_path,req,device='cpu')
    req=_request(tmp_path)
    req['forceOrder']=True
    with pytest.raises(ValueError,match='UNRECOGNIZED_REQUEST_FIELDS'):
        shadow.evaluate(tmp_path,req,device='cpu')


def test_shadow_rejects_oracle_claim_and_authority_escalation(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_v33_game_learning as engine
    monkeypatch.setattr(engine,'infer',lambda *a,**k: {
        'choice':'Contest','learned':True,'checkpoint_sha256':'00'*32,
        'scoring_oracle_used':True,'production_authority':False})
    with pytest.raises(ValueError,match='MODEL_PREDICTION_INVALID'):
        shadow.evaluate(tmp_path,_request(tmp_path),device='cpu')


def test_dev_gate_fails_closed_without_consuming_test(tmp_path,monkeypatch):
    out=tmp_path/'runtime/reports/gamer-v1/v33-train'
    _stub_training(monkeypatch,out)
    manifest=v33.read(out/'train_manifest.json')
    assert manifest['dev_pass'] is True
    # Test production code's DEV gate using a second independently fitted head.
    monkeypatch.setattr(v33,'train_head',lambda *args, **kwargs:
        (v33.head(torch,12).state_dict(),1,[{'epoch':1,'dev_exact':45,'dev_total':90}]))
    out2=tmp_path/'runtime/reports/gamer-v1/v33-lowdev'
    v33.training(out2,project=tmp_path,device='cpu',epochs=1)
    assert v33.read(out2/'train_manifest.json')['dev_pass'] is False
    with pytest.raises(ValueError,match='DEV_GATE_NOT_MET_TEST_REMAINS_SEALED'):
        v33.audit(out2,project=tmp_path,device='cpu')
    assert not (out2/'holdout_consumed.json').exists()


def test_saved_holdout_prediction_tampering_is_detected(tmp_path,monkeypatch):
    out=tmp_path/'runtime/reports/gamer-v1/v33-train'
    _stub_training(monkeypatch,out)
    monkeypatch.setattr(v33,'model_context',lambda model_id,device: (
        torch,object(),SimpleNamespace(eval=lambda:None)))
    monkeypatch.setattr(v33,'capture',lambda model,tok,t,prompts,**kwargs:
        np.zeros((len(prompts),12),dtype=np.float32))
    labels=[r['label'] for r in v33.make_deck('holdout',33)]
    monkeypatch.setattr(v33,'predict_features',lambda t,net,x: labels)
    assert v33.audit(out,project=tmp_path,device='cpu')['heldout_pass']
    ledger=out/'holdout_predictions.json'
    doc=v33.read(ledger)
    doc[0]['prediction']=1-doc[0]['prediction']
    ledger.write_text(json.dumps(doc),encoding='utf-8')
    with pytest.raises(ValueError,match='HOLDOUT_EVIDENCE_CHANGED'):
        v33.load_head(out,tmp_path,require_audit=True)


def test_shadow_requires_explicit_binary_policy_model_not_synthetic_order(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_v33_game_learning as engine
    monkeypatch.setattr(engine,'infer',lambda *a,**k: {
        'choice':'Hack','learned':True,'checkpoint_sha256':'00'*32,
        'scoring_oracle_used':False,'production_authority':False})
    with pytest.raises(ValueError,match='MODEL_PREDICTION_INVALID'):
        shadow.evaluate(tmp_path,_request(tmp_path),device='cpu')


def test_live_prediction_does_not_call_oracle_or_parse_reward_values(tmp_path, monkeypatch):
    out=tmp_path/'v33'
    out.mkdir()
    (out/'learned_head.pt').write_bytes(b'fake-fingerprint-only')
    monkeypatch.setattr(v33,'load_head',lambda *a,**k: (
        {'lineage':_dummy_lineage(),'model_id':'mock-frozen-qwen'},
        v33.head(torch,12)))
    monkeypatch.setattr(v33,'model_context',lambda *a,**k: (
        torch,object(),SimpleNamespace(eval=lambda:None)))
    monkeypatch.setattr(v33,'capture',lambda *a,**k: np.zeros((1,12),dtype=np.float32))
    monkeypatch.setattr(v33,'predict_features',lambda *a,**k: [1])
    monkeypatch.setattr(v33,'verify_activation',lambda *a,**k: None)
    monkeypatch.setattr(v33,'exact_teacher',lambda *a,**k:
        pytest.fail('oracle cannot be called for model decision'))
    result=v33.infer(out,'new unseen Hawk Dove story',project=tmp_path,device='cpu')
    assert result['choice']=='Contest'
    assert result['learned'] is True
    assert result['scoring_oracle_used'] is False
    assert result['production_authority'] is False


def test_real_gradient_training_acquires_synthetic_nonconstant_policy():
    # Tests that train_head really updates weights rather than returning
    # deterministic expected-payoff arithmetic or a canned label.
    rng = np.random.default_rng(390)
    X = rng.standard_normal((360,12)).astype(np.float32)
    labels = (X[:,0] + X[:,1] > 0).astype(np.int64)
    # Synthetic labels are only a *unit test* of the optimizer, not a claim
    # about actual base-Qwen or heldout T08 results.
    weights,epoch,history = v33.train_head(torch,X[:270],labels[:270].tolist(),
                     X[270:],labels[270:].tolist(),seed=3,epochs=25)
    assert 1 <= epoch <= 25
    assert max(row['dev_exact'] for row in history) >= 70
    model = v33.head(torch,12)
    model.load_state_dict(weights)
    result=v33.predict_features(torch,model,X[270:])
    assert sum(a==b for a,b in zip(result,labels[270:])) >= 70
