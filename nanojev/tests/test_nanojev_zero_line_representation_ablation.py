"""Deterministic, offline NanoJev 192:223 representation-ablation checks."""
from pathlib import Path
import random

import pytest
import torch
from torch import nn

from tools import nanojev_zero_line_representation_ablation as ab
from tools.nanojev_addition_curriculum import AdditionNet
from tools.nanojev_zero_line_learned_router import cache_with_tokens


def _mini():
    torch.set_num_threads(2)
    base=AdditionNet('digit_tokens',width=16,heads=2,layers=1,rounds=1,seed=9).eval().requires_grad_(False)
    rows=[(a,b,a+b,0,1) for a,b in [(192,-192),(193,-191),(-199,201),(220,-189),(210,20),(-202,-20),(215,-214),(205,205)]]
    return cache_with_tokens(base,rows,'cpu',4,True)


def test_real_additionnet_feature_dimensions():
    c=_mini()
    assert c['features'].shape==(8,16)
    assert c['tokens'].shape==(8,224)


def test_three_arms_input_independence():
    c=_mini();models,_=ab.make_models(16,224,128)
    original={k:ab.predict(v,c).clone() for k,v in models.items()}
    c['target']=torch.full_like(c['target'],123456)
    for k,v in models.items():assert torch.equal(original[k],ab.predict(v,c))


def test_ablate_features_without_oracle():
    c=_mini();models,_=ab.make_models(16,224,128)
    b=models['backbone'];t=models['token_only'];a=models['augmented']
    changed={**c,'features':c['features']+torch.randn_like(c['features'])*10}
    assert torch.equal(ab.predict(t,c),ab.predict(t,changed))
    changed_tokens={**c,'tokens':c['tokens']+torch.randn_like(c['tokens'])*10}
    assert torch.equal(ab.predict(b,c),ab.predict(b,changed_tokens))
    assert not torch.equal(ab.predict(a,c),ab.predict(a,changed_tokens))


def test_comparable_parameter_budgets():
    models,budgets=ab.make_models(64,896,256)
    for name in ab.ARMS:
        assert budgets[name]['relative_parameter_gap']<=.02
        assert ab.parameter_count(models[name])==budgets[name]['parameters']


def test_split_leak_and_extrapolation():
    samples=ab.sample_band(192,223,31,4)
    ab.validate_split(samples,31)
    s=ab.sample_band(224,255,31,4,splits=('ood',))['ood']
    assert all(224<=max(abs(a),abs(b))<=255 for a,b,*_ in s)
    samples['dev'].append(samples['train'][0])
    with pytest.raises(RuntimeError,match='LEAKED_PAIR'):ab.validate_split(samples,31)


def test_resume_stable_and_no_ood_checkpoint_selector(tmp_path):
    c=_mini();models,budgets=ab.make_models(16,224,128)
    ys=torch.tensor([0,1,4,16,40,91,0,-1,-4,-16,-40,-91],dtype=torch.long)
    data={'features':torch.randn(len(ys),16),'tokens':torch.randn(len(ys),224),'target':ys}
    meta={'arm':'backbone'}
    p=tmp_path/'stage'
    ret=ab.train_arm(models['backbone'],data,data,p,meta,steps=2,probe_every=1,batch_size=8,lr=.0003,seed=15)
    state=torch.load(p/'training.pt',weights_only=True)
    old={k:v.clone() for k,v in models['backbone'].state_dict().items()}
    newer=ab.RepresentationBank('backbone',16,224,budgets['backbone']['hidden'])
    r2=ab.train_arm(newer,data,data,p,meta,steps=2,probe_every=1,batch_size=8,lr=.0003,seed=15)
    assert ret['selected_step']==r2['selected_step']
    assert all(torch.equal(newer.state_dict()[k],v) for k,v in old.items())
    assert state['step']==2
    with pytest.raises(RuntimeError,match='RESUME_CONFIG_MISMATCH'):
        ab.train_arm(newer,data,data,p,{'arm':'token_only'},steps=3,probe_every=1,batch_size=8,lr=.0003,seed=15)


def test_baseline_rejects_other_checkpoint(tmp_path):
    path=tmp_path/'stage';path.mkdir();(path/'config.json').write_text('{"band":[192,223],"source_sha256":"bad"}')
    c=_mini()
    with pytest.raises(RuntimeError,match='PROVENANCE_MISMATCH'):
        ab.baseline_score(path,{'checkpoint_sha256':'good'},c,c,c,'cpu',16)


def test_output_never_overwrites_source(tmp_path):
    root=tmp_path/'source';root.mkdir()
    with pytest.raises(RuntimeError,match='OVERLAP'):ab.safe_output(root/'nested',[root])


def test_train_dev_sampling_consistent():
    a=ab.sample_band(192,223,41,8)
    b=ab.sample_band(192,223,41,8)
    assert a==b


def test_full_synthetic_cpu_run(monkeypatch,tmp_path):
    torch.set_num_threads(2)
    base=AdditionNet('digit_tokens',width=16,heads=2,layers=1,rounds=1,seed=8)
    sentinel=tmp_path/'source.pt'
    sentinel.write_bytes(b'unchanged-certified-source-fixture')
    from tools.nanojev_zero_line_radius255_probe import sha256
    source={'checkpoint':str(sentinel),'checkpoint_sha256':sha256(sentinel),'config_sha256':'fixture',
            'step':1,'source_radius':127,'source_streak':3}
    monkeypatch.setattr(ab,'load_certified_model',lambda path,device:(base.to(device),source))
    output=tmp_path/'output'
    args=ab.parser().parse_args(['--ladder-output',str(tmp_path/'ladder'),
        '--output',str(output),'--device','cpu','--steps','2','--probe-every','1',
        '--batch-size','8','--sample-per-cell','2','--eval-batch-size','64',
        '--reference-hidden','128','--cpu-threads','2'])
    result=ab.run(args)
    assert result['source_unchanged'] and result['ood_not_used_for_training_or_selection']
    assert set(result['arms'])==set(ab.ARMS)
    assert (output/'result.json').is_file()
    for arm in ab.ARMS:
        assert result['arms'][arm]['selected_step']<=2
        assert result['arms'][arm]['adjacent_stress']['cards']>0
        assert (output/arm/'best.pt').is_file()
    resumed=ab.run(args)
    assert resumed['arms']['backbone']['selected_step']==result['arms']['backbone']['selected_step']
    assert sha256(sentinel)==source['checkpoint_sha256']
