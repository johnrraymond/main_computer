"""Offline, CUDA-free safety and causal-protocol tests for isolated LoRA stress."""
from __future__ import annotations
import json
from fractions import Fraction
import numpy as np
import pytest
from tools import nanojev_gamer_v1_lora_sign_stress as stress
from tools import nanojev_gamer_v1_sign_information_probe as oracle


def _deck():
    return oracle.make_deck(24,12,12,include_ood=True)


def test_expanded_train_only_deck_preserves_every_prior_oracle_and_all_holdout_cases():
    base=_deck()
    new=stress._dataset({'dataset':[x.public() for x in base]},96)
    assert len([x for x in new if x.split=='train'])==576
    assert len([x for x in new if x.split=='dev'])==72
    assert len([x for x in new if x.split=='test'])==108
    assert all(x.family=='signed_offset' for x in new if x.split=='train')
    assert len([x for x in new if x.family=='hawk_indifference'])==36
    assert {x.id:x.public() for x in new if x.split!='train'} == {
        x.id:x.public() for x in base if x.split!='train'}


def test_prior_train_identity_must_be_preserved():
    old=[x.public() for x in _deck()]
    old[0]['target']='999'
    with pytest.raises(ValueError,match='ORIGINAL_DECK_CHANGED'):
        stress._dataset({'dataset':old},96)


def test_refuses_train_dataset_crossing_dev_range():
    with pytest.raises(ValueError,match='RANGE'):
        stress._dataset({'dataset':[x.public() for x in _deck()]},140)


def test_sampler_balances_sign_and_near_zero_without_hawk_or_test_labels():
    import random
    train=stress._examples(stress._dataset({'dataset':[x.public() for x in _deck()]},96))
    groups=stress._group_examples(train)
    seen=set()
    rng=random.Random(19)
    for i in range(600):
        x=stress._draw(rng,groups,0.75)
        assert x.split=='train' and x.family=='signed_offset'
        seen.add(int((x.target>0)-(x.target<0)))
    assert seen=={-1,0,1}
    assert all(k in groups for k in ((-1,True),(-1,False),(0,True),(0,False),(1,True),(1,False)))


def test_output_must_not_be_inside_original_or_source(tmp_path):
    old=tmp_path/'base';old.mkdir()
    with pytest.raises(ValueError,match='OVERLAPS'):
        stress._destination(old/'subdir',[old])
    with pytest.raises(ValueError,match='OVERLAPS'):
        stress._destination(tmp_path,[old])
    with pytest.raises(FileExistsError):
        stress._destination(old)


def test_snapshot_fingerprint_rejects_mutation(tmp_path):
    folder=tmp_path/'step-0000000';folder.mkdir()
    (folder/'adapter_config.json').write_text('config')
    (folder/'adapter_model.safetensors').write_bytes(b'WEIGHTS')
    digest=stress._weights_hash(folder)
    (folder/'snapshot.json').write_text(json.dumps({'step':0,'adapter_payload_sha256':digest}))
    assert stress._verify_snapshot(folder,0)==digest
    (folder/'adapter_model.safetensors').write_bytes(b'REPLACED')
    with pytest.raises(ValueError,match='SNAPSHOT_PAYLOAD_CHANGED'):
        stress._verify_snapshot(folder,0)


def test_snapshot_wrong_step_rejected(tmp_path):
    folder=tmp_path/'snapshot';folder.mkdir()
    (folder/'snapshot.json').write_text(json.dumps({'step':100,'adapter_payload_sha256':'X'}))
    with pytest.raises(ValueError,match='WRONG_SNAPSHOT_STEP'):
        stress._verify_snapshot(folder,500)


def test_audit_snapshots_must_include_immutable_baseline(tmp_path):
    root=tmp_path/'run';(root/'snapshots').mkdir(parents=True)
    for i in (0,500,1000):
        (root/'snapshots'/f'step-{i:07d}').mkdir()
    assert stress._steps('0,1000',root)==[0,1000]
    assert stress._steps('all',root)==[0,500,1000]
    with pytest.raises(ValueError,match='MUST_INCLUDE_ZERO'):
        stress._steps('500,1000',root)
    with pytest.raises(ValueError,match='MUST_INCLUDE_ZERO'):
        stress._steps('0,750',root)


def test_output_metric_uses_same_zero_tolerance_as_production():
    deck=_deck(); rows=[x.public() for x in deck]
    preds=np.asarray([(-0.06 if x.target<0 else 0.02 if x.target==0 else 0.06) for x in deck])
    measured=stress._head_metrics(rows,preds)
    assert measured['test']['balanced_sign_accuracy']==1
    assert measured['test']['triple_accuracy']==1
    assert measured['test_by_family']['hawk_indifference']['triple_accuracy']==1


def test_compares_delta_for_each_stage_and_zero_negative_independently():
    template={'test':{'balanced_sign_accuracy':0.5,'triple_accuracy':.1,'recall':{'-1':.2,'0':.3}},
              'test_by_family':{'hawk_indifference':{'balanced_sign_accuracy':.3}}}
    good={'test':{'balanced_sign_accuracy':.6,'triple_accuracy':.2,'recall':{'-1':.5,'0':.4}},
          'test_by_family':{'hawk_indifference':{'balanced_sign_accuracy':.4}}}
    stages=('lora_layer09_mean100','lora_final_mean100','c0_raw_mean100',
            'c0_raw_flat','c0_norm','enc_linear_1','enc_gelu_3','mid_gelu')
    a={'production_c0':template,'matched_readouts':dict.fromkeys(stages,template)}
    b={'production_c0':good,'matched_readouts':dict.fromkeys(stages,good)}
    comp=stress._compare(a,b)
    assert np.isclose(comp['stages']['enc_gelu_3']['sign_delta'],.1)
    assert comp['stages']['enc_gelu_3']['negative_recall_after']==.5
    assert comp['stages']['enc_gelu_3']['zero_recall_after']==.4
    assert np.isclose(comp['c0_output']['triple_delta'],.1)
    assert comp['no_model_promotion']


def test_plan_has_no_training_and_validates_flags(monkeypatch,capsys,tmp_path):
    base=_deck()
    source=tmp_path/'preservation';source.mkdir()
    (source/'final_report.json').write_text('ORIGINAL')
    meta={'dataset':[x.public() for x in base], 'dataset_sha256':'ORACLE',
          'lineage':{'checkpoint_sha256':'C0','adapter_sha256':'LORA'}}
    monkeypatch.setattr(stress,'_source',lambda path:(source,{},meta,{},tmp_path))
    stress.main(['plan','--source',str(source)])
    out=json.loads(capsys.readouterr().out)
    assert out['status']=='PLAN_ONLY'
    assert out['oracle_train_cases']==576
    assert out['unseen_hawk_train_count']==0
    with pytest.raises(ValueError,match='INVALID_FLAGS'):
        stress.main(['plan','--source',str(source),'--near-zero-fraction','1.5'])


def test_audit_requires_separate_output():
    with pytest.raises(ValueError,match='REQUIRES_OUTPUT_AND_AUDIT_OUTPUT'):
        stress.main(['audit','--source','source','--output','output'])
