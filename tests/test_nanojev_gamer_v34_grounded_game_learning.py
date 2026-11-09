"""V34 TRAIN/DEV numerical grounding, sealed audit, Captain shadow tests."""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_v33_game_learning as v33
from tools import nanojev_gamer_v1_v34_grounded_game_learning as v34
from main_computer import gamer_tactical_shadow as shadow
from main_computer.tactical_ai_service import TacticalAIService


def test_grammar_grounded_from_input_text_only_train_variants():
    # This test uses ONLY the train-family surfaces, not the sealed V34 test deck.
    train_rows, dev_rows = v33.fit_partition(v33.make_deck('fit', 33))
    x = v34.features([r['prompt'] for r in train_rows + dev_rows])
    assert x.shape == (360, 5)
    assert np.isfinite(x).all()
    assert set(x[:, -1]) == {-0.5, 0.5}
    for i in range(0, len(x), 2):
        np.testing.assert_allclose(x[i, :4], x[i+1, :4])
        assert x[i,-1] == -x[i+1,-1]
    assert not any('label' in str(part) for part in v34.ground.__code__.co_names)


@pytest.mark.parametrize('prompt,correct', [
    ('Outcomes for us: CC=8; CD=1; DC=14; DD=-5. C probability 3/4.',
     [.4,.05,.7,-.25,.5]),
    ('Payoff ledger, our score first: DD=-5, DC=14, CD=1, CC=8. C probability 1/4.',
     [.4,.05,.7,-.25,-.5]),
    ('When we Yield and they Yield: 8; when we Yield and they Contest: 1; '
     'when we Contest and they Yield: 14; when both Contest: -5. Chance 1/4.',
     [.4,.05,.7,-.25,-.5]),
    ('Scoring for our captain: (our D, other C)=14; (our C, other D)=1; '
     '(our D, other D)=-5; (our C, other C)=8. Chance 3/4.',
     [.4,.05,.7,-.25,.5]),
])
def test_grounding_all_four_table_formats(prompt, correct):
    np.testing.assert_allclose(v34.ground(prompt), correct, atol=1.e-7)


@pytest.mark.parametrize('bad', [
    '', 'not a payoff game',
    'CC=1, CD=2, DC=3, DD=4. No belief.',
    'CC=1, CD=2, DC=3, DD=4. C probability 1/4 and 3/4.',
    'CC=1, CD=2, DC=3, DD=4. C probability 9/4.',
    'CC=1, CD=2, DC=3, DD=4. C probability 0/0.',
    'CC=1001, CD=2, DC=3, DD=4. C probability 1/4.',
    'CC=1, CD=2, DC=3, DD=4. Another: CC=5, CD=6, DC=7, DD=8. Chance 1/4.',
    'CC=1, CD=2, DC=3. C probability 1/4.',
    'CC=1, CD=2, DC=3, DD=4. C probability 1/4. Unknown payoff DD=5, DC=4, CD=3, CC=2.',
])
def test_grounding_fails_closed(bad):
    with pytest.raises((ValueError, ZeroDivisionError)):
        v34.ground(bad)



def test_asymmetric_deck_includes_reversals_nonreversals_and_independent_contrasts():
    fit, dev = v34.asymmetric_partition(v34.asymmetric_deck('fit',33))
    assert len(fit) == 900 and len(dev) == 300
    assert {r['pair'] for r in fit}.isdisjoint(r['pair'] for r in dev)
    for rows in (fit, dev):
        assert len({r['prompt'] for r in rows}) == len(rows)
        families = {r['family'] for r in rows}
        assert families == {'reversal', 'non_reversal', 'independent'}
        for i in range(0,len(rows),2):
            a,b=rows[i:i+2]
            assert a['pair']==b['pair']
            if a['family']=='reversal':
                assert a['label']!=b['label']
            if a['family']=='non_reversal':
                assert a['label']==b['label']



def test_asymmetric_dev_rejects_original_q_sign_shortcut():
    _, dev = v34.asymmetric_partition(v34.asymmetric_deck('fit',33))
    numbers = v34.features([r['prompt'] for r in dev])
    # Shortcut that succeeds on every symmetric V33 reversal:
    # choose by T-R and whether q is greater than 1/2.
    shortcut = [int((row[2] - row[0] > 0) == (row[4] > 0)) for row in numbers]
    score = sum(r['label'] == p for r,p in zip(dev,shortcut))
    assert score < v34.ASYM_DEV_MIN


def test_contrastive_development_learning_from_raw_numbers_without_oracle(monkeypatch):
    original_train,original_dev = v33.fit_partition(v33.make_deck('fit',33))
    general_train,general_dev = v34.asymmetric_partition(v34.asymmetric_deck('fit',33))
    train_rows=original_train+general_train
    dev_rows=original_dev+general_dev
    def no_oracle(*args,**kwargs):
        raise AssertionError('oracle must not enter feature extraction or optimization')
    monkeypatch.setattr(v33,'exact_teacher',no_oracle)
    train=v34.features([r['prompt'] for r in train_rows]); dev=v34.features([r['prompt'] for r in dev_rows])
    state,epoch,hist,pred=v34.train_head(train,[r['label'] for r in train_rows],
               dev,[r['label'] for r in dev_rows],seed=33,epochs=40)
    assert 0 < epoch <= 40
    assert hist[epoch-1]['original_dev_exact'] >= 72
    assert hist[epoch-1]['asymmetric_dev_exact'] >= 270
    assert v34.test_pairs(original_dev,pred[:90])[0] >= 36
    assert v34.test_pairs(general_dev,pred[90:])[0] >= 135
    assert set(pred)=={0,1}
    model=v34.head(torch);model.load_state_dict(state)
    assert v34.predict(model,dev)==pred
    assert not any('holdout' in row['id'] for row in train_rows+dev_rows)


def _lineage():
    return {'model_id':'base-qwen','v32_state_sha256':'a', 'source_files':{},
            'base_activation':'frozen_base_qwen_layer18_no_lora_adapter',
            'v31_acceptance_sha256':'b'}


def make_trained(tmp_path, monkeypatch, name='v34', epochs=30):
    monkeypatch.setattr(v33, 'activation', lambda _: _lineage())
    monkeypatch.setattr(v33, 'verify_activation', lambda *_: _lineage())
    out = tmp_path / 'runtime' / 'reports' / 'gamer-v1' / name
    res=v34.train(out,project=tmp_path,seed=33,epochs=epochs)
    return out,res


def test_full_train_preserves_v32_and_does_not_open_test(tmp_path, monkeypatch):
    out, res = make_trained(tmp_path, monkeypatch)
    assert res['original_dev_exact'] >= 72
    assert res['asymmetric_dev_exact'] >= 270
    assert res['original_dev_pairs_exact'] >= 36
    assert res['asymmetric_dev_pairs_exact'] >= 135
    assert res['holdout_evaluated'] is False
    assert not (out / 'holdout_consumed.json').exists()
    assert not (out / 'holdout_acceptance.json').exists()
    assert v34.read(out/'train_manifest.json')['oracle_inference'] is False
    assert v34.read(out/'train_manifest.json')['frozen_qwen_feature_used'] is False
    _,head=v34.load_head(out,tmp_path)
    assert head[0].weight.shape[1] == 5
    with pytest.raises(ValueError, match='OUTPUT_ALREADY_EXISTS'):
        v34.train(out,project=tmp_path,epochs=1)
    (out/'grounded_head.pt').write_bytes(b'bad')
    with pytest.raises(ValueError, match='HEAD_CHECKPOINT_CHANGED'):
        v34.load_head(out,tmp_path)


def test_train_failure_never_opens_test(tmp_path, monkeypatch):
    monkeypatch.setattr(v34,'train_head',lambda *args,**kwargs: (
        v34.head(torch).state_dict(),1,[{'epoch':1,'dev_exact':45,'dev_total':390,
        'original_dev_exact':45,'asymmetric_dev_exact':0}],
        [0]*390))
    out,res=make_trained(tmp_path,monkeypatch,'lowdev',epochs=1)
    assert not res['dev_pass']
    with pytest.raises(ValueError, match='DEV_GATE_NOT_MET_TEST_REMAINS_SEALED'):
        v34.audit(out,project=tmp_path)
    assert not (out/'holdout_consumed.json').exists()


def test_audit_is_one_shot_and_no_promotion_if_predictions_constant(tmp_path,monkeypatch):
    out,res=make_trained(tmp_path,monkeypatch,'one-shot')
    assert res['dev_pass']
    monkeypatch.setattr(v34,'predict',lambda model, features: [0]*len(features))
    result=v34.audit(out,project=tmp_path)
    assert not result['heldout_pass']
    assert result['total']==320
    assert result['original_test_exact']==60
    assert result['original_test_pairs_exact']==0
    assert not result['heldout_pass']
    with pytest.raises(ValueError, match='HOLDOUT_ALREADY_CONSUMED'):
        v34.audit(out,project=tmp_path)
    with pytest.raises(ValueError, match='HOLDOUT_NOT_CERTIFIED'):
        v34.load_head(out,tmp_path,require_audit=True)


def test_sealed_pass_integrity_replay_does_not_trust_json_status(tmp_path,monkeypatch):
    out,res=make_trained(tmp_path,monkeypatch,'integrity')
    assert res['dev_pass']
    original_make_deck=v33.make_deck
    expected_rows=(original_make_deck('holdout',33) +
                   v34.asymmetric_deck('holdout',33))
    # The unit test supplies fake perfect model predictions to test gates;
    # this is NOT evidence of actual V34 test generalization.
    monkeypatch.setattr(v34,'predict',lambda model,x: [r['label'] for r in expected_rows])
    report=v34.audit(out,project=tmp_path)
    assert report['heldout_pass']
    v34.load_head(out,tmp_path,require_audit=True)
    ledger=out/'holdout_predictions.json'
    contents=v34.read(ledger)
    contents[0]['prediction']=1-contents[0]['prediction']
    ledger.write_text(json.dumps(contents),encoding='utf-8')
    with pytest.raises(ValueError, match='HOLDOUT_LEDGER_CHANGED'):
        v34.load_head(out,tmp_path,require_audit=True)


def observation():
    return {'schema':'game.bridgeCaptainObservation.v1',
            'captainId':'captain.beta','shipId':'ship.beta',
            'mission':'pursue-main-ship-and-seek-boarding-range',
            'simulationSeconds':10., 'rangeM':3000.,
            'radialVelocityMps':-2.,'targetHullPercent':100.,
            'transporterMaxRangeM':1500.,'relativePositionM':[3000.,0.],
            'relativeVelocityMps':[-2.,0.],
            'boarding':{'phase':'idle','boarders':'aboard'}}


def request(tmp_path, schema='game.bridgeGamerShadowRequest.v34'):
    return {'schema':schema,'observation':observation(),
            't08Prompt':'CC=8; CD=1; DC=14; DD=-5. C probability 3/4.',
            'run':str(tmp_path/'runtime/reports/gamer-v1/v34')}


@pytest.mark.parametrize('choice,maneuver', [('Contest','approach'),('Yield','withdraw')])
def test_v34_captain_shadow_legal_and_no_order(tmp_path,monkeypatch,choice,maneuver):
    monkeypatch.setattr(v34,'infer',lambda *a,**k:{
        'choice':choice,'learned':True,'scoring_oracle_used':False,
        'production_authority':False,'checkpoint_sha256':'a'*64})
    response=TacticalAIService(tmp_path).shadow_gamer_hawk_dove(request(tmp_path),device='cpu')
    assert response['status']=='PASS_SHADOW_PROPOSAL_NO_ORDER'
    assert response['schema']=='game.bridgeGamerShadowResponse.v34'
    assert response['suggestedLegalIntent']['maneuver']==maneuver
    assert response['captainOrder'] is None and response['actionPublished'] is False
    assert response['tacticalV6AuthorityChanged'] is False


def test_v34_captain_refuses_committed_boarding(tmp_path,monkeypatch):
    monkeypatch.setattr(v34,'infer',lambda *a,**k:pytest.fail('should never infer'))
    req=request(tmp_path)
    req['observation']['boarding']={'phase':'deployed','boarders':'deployed'}
    with pytest.raises(ValueError,match='BOARDERS_COMMITTED_ABSTAIN'):
        shadow.evaluate(tmp_path,req,device='cpu')


def test_v34_captain_avoids_oracle_and_cannot_switch_authority(tmp_path,monkeypatch):
    monkeypatch.setattr(v34,'infer',lambda *a,**k:{
        'choice':'Contest','learned':True,'scoring_oracle_used':True,
        'production_authority':False,'checkpoint_sha256':'a'*64})
    with pytest.raises(ValueError,match='MODEL_PREDICTION_INVALID'):
        shadow.evaluate(tmp_path,request(tmp_path),device='cpu')
