"""Tests for read-only, failure-mechanism diagnosis. No CUDA required."""
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_zero_line_failure_diagnosis as diag
from tools import nanojev_zero_line_representation_ablation as ab
from tools.nanojev_addition_curriculum import AdditionNet
from tools.nanojev_zero_line_learned_router import cache_with_tokens
from tools.nanojev_zero_line_radius255_probe import sha256


def test_carries_and_borrows_are_real_decimal_events():
    assert diag.decimal_events(105, 5) == ('carry', 1)   # 5+5 carry into tens
    assert diag.decimal_events(-95, -8) == ('carry', 2)
    assert diag.decimal_events(203, -9) == ('borrow', 2)
    assert diag.decimal_events(200, -1) == ('borrow', 2)
    assert diag.decimal_events(203, -201) == ('borrow', 0)
    assert diag.decimal_events(-202, 2) == ('borrow', 0)
    assert diag.decimal_events(0, -202) == ('none', 0)


def test_labels_cover_zero_cancellation_sign_and_boundaries():
    z = diag.classify(200, -200, (192,223))
    assert z['residual_class'] == 'zero'
    assert z['operation'] == 'opposite_sign_cancellation'
    assert z['magnitude_boundary'] == 'decimal_decade_transition'
    p = diag.classify(199,-198,(192,223))
    assert p['residual_sign']=='positive' and p['residual_class']=='near_1_2'
    n = diag.classify(-200,168,(192,223))
    assert n['residual_sign']=='negative' and n['residual_class']=='frontier_16_32'
    far = diag.classify(-215,-155,(192,223))
    assert far['operation']=='same_sign_addition' and far['carry_borrow']=='carry'
    edge = diag.classify(224,-200,(224,255))
    assert edge['magnitude_boundary']=='first_three_magnitudes'


def test_decode_and_group_statistics_ignore_logits_probabilities():
    logits=torch.zeros((4,73))
    targets=[0,2,-16,222]
    for i,truth in enumerate(targets):
        sign = (truth>0) - (truth<0)
        logits[i,sign+1]=3
        n=abs(truth)
        for pos in range(7):
            logits[i,3+pos*10+(n%10)] = 8
            n//=10
    decoded=diag.signed_value(logits)
    assert decoded.tolist()==targets
    cards=[(1,-1,0,0,1),(193,-191,2,0,1),(-200,184,-16,0,1),(200,22,222,0,1)]
    rows=diag.describe_predictions(cards,{'backbone':logits})
    assert all(r['backbone_correct'] for r in rows)
    assert diag.summarize_rows(rows,['backbone'])['residual_class']['zero']['cards']==1
    rows[0]['backbone_correct']=False
    pair=diag.complementarity(rows,'backbone','backbone')
    assert pair['both_wrong']==1 and pair['left_only_correct']==0


def test_model_inference_is_independent_of_evaluation_target():
    torch.set_num_threads(2)
    net=AdditionNet('digit_tokens',width=16,heads=2,layers=1,rounds=1,seed=8).eval()
    cards=[(192,-192,0,0,1),(210,-188,22,0,1),(-200,-30,-230,0,-1)]
    cache=cache_with_tokens(net,cards,'cpu',4,True)
    models,_=ab.make_models(16,224,128)
    assert diag.output_independent_of_truth(models,cache,4)
    pred=diag.predict_all(models,cache,4)
    stats=diag.transforms(net,cards,'cpu',4,models,None,pred)
    assert set(stats['backbone'])=={'operand_swap','both_signs_reversed'}
    assert stats['backbone']['operand_swap']['cards']==3


def test_reject_swapped_or_unmatched_checkpoint(tmp_path):
    root=tmp_path/'r';root.mkdir()
    cfg={'budgets':{'backbone':{'hidden':24}},'seed':12,'sample_per_cell':4,'steps':5}
    torch.save({'meta':{'arm':'augmented','source_sha256':'known'},'step':1,'model':{}},root/'best.pt')
    with pytest.raises(RuntimeError,match='MISSING_ARM'):
        diag.load_arm_models(tmp_path,cfg,16,224,'known','cpu')
    (tmp_path/'backbone').mkdir()
    (tmp_path/'backbone'/'best.pt').write_bytes((root/'best.pt').read_bytes())
    with pytest.raises(RuntimeError,match='CHECKPOINT_PROVENANCE_MISMATCH'):
        diag.load_arm_models(tmp_path,cfg,16,224,'known','cpu')


def test_dataset_config_rejects_mutated_counts():
    samples=ab.sample_band(192,223,123,2)
    adjacent=ab.sample_band(224,255,130,2,splits=('ood',))['ood']
    cfg={'seed':123,'split_counts':{k:len(v) for k,v in samples.items()},'adjacent_cards':len(adjacent)}
    assert diag.validate_population(samples,adjacent,cfg)
    cfg['adjacent_cards']+=1
    with pytest.raises(RuntimeError,match='ADJACENT_CONFIG_MISMATCH'):
        diag.validate_population(samples,adjacent,cfg)


def test_full_readonly_synthetic_end_to_end(monkeypatch,tmp_path):
    torch.set_num_threads(2)
    model=AdditionNet('digit_tokens',width=16,heads=2,layers=1,rounds=1,seed=11).eval()
    fixture=tmp_path/'frozen.pt'
    fixture.write_bytes(b'pretrained-source-hash-synthetic')
    source={'checkpoint':str(fixture),'checkpoint_sha256':sha256(fixture),'config_sha256':'fixture',
            'step':1,'source_radius':127,'source_streak':3}
    monkeypatch.setattr(ab,'load_certified_model',lambda path,device:(model.to(device),source))
    monkeypatch.setattr(diag,'load_certified_model',lambda path,device:(model.to(device),source))
    apath=tmp_path/'ablation'
    params=ab.parser().parse_args(['--ladder-output',str(tmp_path/'ladder'),'--output',str(apath),
        '--device','cpu','--steps','1','--probe-every','1','--batch-size','8',
        '--sample-per-cell','2','--eval-batch-size','64','--reference-hidden','128','--cpu-threads','2'])
    ab.run(params)
    orig_config=(apath/'config.json').read_bytes()
    orig_weights={a:sha256(apath/a/'best.pt') for a in ab.ARMS}
    args=diag.parser().parse_args(['--ladder-output',str(tmp_path/'ladder'),
        '--ablation-output',str(apath),'--output',str(tmp_path/'diagnostics'),
        '--device','cpu','--eval-batch-size','64','--cpu-threads','2'])
    result=diag.run(args)
    assert result['labels_used_for_inference'] is False
    assert result['label_independence_checked'] and result['frozen_models']
    assert result['splits']['ood']['cards']>0
    assert result['splits']['adjacent']['metamorphic']['backbone']['operand_swap']['cards']>0
    assert (tmp_path/'diagnostics'/'cards_ood.csv').is_file()
    assert (tmp_path/'diagnostics'/'result.json').is_file()
    assert fixture.read_bytes()==b'pretrained-source-hash-synthetic'
    assert (apath/'config.json').read_bytes()==orig_config
    assert {a:sha256(apath/a/'best.pt') for a in ab.ARMS} == orig_weights
    again=diag.run(args)
    assert again['splits']['ood']['totals']==result['splits']['ood']['totals']
