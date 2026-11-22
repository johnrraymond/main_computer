"""Gamer MIT 0098: train-only endpoint direction and sealed head comparison."""
from __future__ import annotations

import json
from pathlib import Path
import torch
import pytest
from safetensors.torch import save_file,load_file

from tools import nanojev_gamer_v1_g2_mit_directional_0098 as m
from tools import nanojev_gamer_v1_graph_curriculum as cur


def _parent(root):
    path = root/'oracle-fabric-g2-extended-learning-0096'/'warm_supervised'/'milestone-01600'
    path.mkdir(parents=True)
    state={'binding.weight':torch.randn(5,192,generator=torch.Generator().manual_seed(77)),
           'binding.bias':torch.zeros(5),
           'project.weight':torch.ones(2,2)}
    save_file(state,str(path/cur.HEAD_FILE))
    m.write(path/cur.MANIFEST,{'schema':'nanojev.gamer.graph.g2.extended-fuzz-learning-0096.v1',
        'head_sha256':m.sha(path/cur.HEAD_FILE),'g3_unlocked':False,
        'production_promotion':False})
    return path,state


def _train_rows():
    rows=[]
    for i in range(100):
        for fam in ('allocation','encounter'):
            rows.append({'prompt':f'Train-only {fam} {i}',
                         'family':fam,'split':'train','graph':{'nodes':[],'edges':[]}})
    return rows


def _direction_input():
    # Gold-versus-wrong endpoint paired features, not accuracy gold from blind data.
    means=torch.zeros(192,dtype=torch.float64)
    means[:16]=torch.tensor([1.5,-1.2,1.1,-.9,.8,-.7,.6,-.5,
                             .45,-.42,.39,-.37,.34,-.31,.28,-.26])
    generator=torch.Generator().manual_seed(918)
    x=[(means+torch.randn(192,generator=generator)*.025).tolist()
       for _ in range(24)]
    y=[(means+torch.randn(192,generator=generator)*.025).tolist()
       for _ in range(24)]
    return {'measures':{0:x,1:y}}


def test_competing_endpoints_are_same_class_and_share_an_anchor():
    objects=[[0,5,'action'],[6,11,'outcome'],[12,17,'outcome'],[18,20,'quantity']]
    selected={('action',(0,5),'outcome',(12,17),'measures')}
    gold={('action',(0,5),'outcome',(6,11),'measures')}
    pairs=m.competing_pairs(selected,gold,objects)
    assert pairs==[('measures',(0,1),(0,2),'target')]
    assert m.competing_pairs(gold,gold,objects)==[]
    assert m.competing_pairs(selected,{('action',(0,5),'outcome',(6,11),'owns')},objects)==[]
    assert m.competing_pairs(selected,gold,objects[:-1])==pairs


def test_direction_is_sparse_and_validated_on_independent_train_half():
    plan,diagnostic=m.direction_from_pairs(_direction_input())
    assert plan['label']=='measures'
    assert len(plan['columns'])==len(set(plan['columns']))==m.TOP_FEATURES
    assert plan['estimated_discovery_margin_gain']>0
    assert plan['estimated_validation_margin_gain']>=m.MIN_VALIDATED_GAIN
    assert plan['delta_max_abs']<=m.MAX_ABS_CHANGE+1e-9
    assert plan['delta_l2']<=m.MAX_L2_CHANGE+1e-9
    assert plan['gradient_steps']==0
    assert diagnostic['measures']['validation_competitions']==24
    bad=_direction_input()
    bad['measures'][1]=[[-q for q in sample] for sample in bad['measures'][1]]
    with pytest.raises(ValueError,match='NO_STABLE_TRAIN_ONLY_ENDPOINT_DIRECTION'):
        m.direction_from_pairs(bad)


def test_direct_edit_changes_only_one_weight_output_row():
    plan,_=m.direction_from_pairs(_direction_input())
    state={'binding.weight':torch.randn(5,192),'binding.bias':torch.randn(5),
           'encoder.weight':torch.randn(5,5)}
    edited,receipt=m.edit_state(state,plan)
    assert receipt['modified_output_rows']==1
    assert receipt['changed_coefficients']==len(plan['columns'])
    assert receipt['gradient_steps']==0
    assert all(torch.equal(state[k],edited[k]) for k in state if k!='binding.weight')
    for index in range(5):
        if index!=plan['index']:
            assert torch.equal(state['binding.weight'][index],edited['binding.weight'][index])
    drift=edited['binding.weight'][plan['index']]-state['binding.weight'][plan['index']]
    assert set(drift.nonzero().flatten().tolist())==set(plan['columns'])
    invalid=dict(plan);invalid['columns']=[0]*len(plan['columns'])
    with pytest.raises(ValueError,match='DIRECTION_COORDINATE_INVALID'):
        m.edit_state(state,invalid)
    oversized=dict(plan);oversized['deltas']=[3.]*len(plan['columns'])
    with pytest.raises(ValueError,match='DIRECTION_COEFFICIENT_LIMIT'):
        m.edit_state(state,oversized)


def test_hook_observes_post_gelu_pair_features_without_training():
    class Mini(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.pair_projection=torch.nn.Linear(2,2,bias=False)
            with torch.no_grad():self.pair_projection.weight.copy_(torch.eye(2))
    head=Mini()
    x=torch.tensor([[[-.2,.4],[.3,.9]],[[-.1,.1],[0.,-2.]]])
    result,features=m._hook_features(head,lambda:head.pair_projection(x))
    assert torch.allclose(features,torch.nn.functional.gelu(x))
    assert result.shape==(2,2,2)
    assert not features.requires_grad
    assert len(head.pair_projection._forward_hooks)==0


def test_train_selection_is_balanced_in_both_diagnostic_halves(monkeypatch):
    monkeypatch.setattr(m.cur,'load_rows',lambda path:_train_rows())
    selected=m._train_selection({'dataset':'unused'},80)
    assert len(selected)==80
    assert sum(v['family']=='allocation' for v in selected)==40
    assert sum(v['family']=='encounter' for v in selected)==40
    for half in (0,1):
        members=[r for i,r in enumerate(selected) if (i//2)%2==half]
        assert sum(r['family']=='allocation' for r in members)==20
        assert sum(r['family']=='encounter' for r in members)==20


def test_sealed_lifecycle_and_prediction_has_no_challenge_answers(tmp_path,monkeypatch):
    root=tmp_path/'reports';root.mkdir()
    src,initial=_parent(root)
    dataset=root/'training.jsonl';dataset.write_text('immutable test train cohort\n')
    training=_train_rows()
    monkeypatch.setattr(m.cur,'load_rows',lambda path:training)
    monkeypatch.setattr(m.structural,'paths_for',lambda root:{'dataset':dataset})
    monkeypatch.setattr(m.structural,'preflight',lambda paths:({},
             {'method':'joint','threshold':0.,'out_caps':{},'in_caps':{}}))
    monkeypatch.setattr(m.earlier,'known_prompts',lambda root,dataset:set())
    m.prepare(root=root,cases=40,train_cases=80)
    dest=m.location(root,None)
    commit=m.check(dest,gold=True)
    assert commit['source']['head_sha256']==m.sha(src/cur.HEAD_FILE)
    assert (dest/'seal'/'answers.SECRET.jsonl').is_file()
    # All train-only diagnosis and the edit must succeed without challenge gold.
    secret_file=dest/'seal'/'answers.SECRET.jsonl'
    gold=secret_file.read_bytes();secret_file.unlink()

    class Composite:
        def predict(self,*args,**kwargs):return {'token_spans':[]}
    monkeypatch.setattr(m.structural,'_load_runtime',lambda *args:
                        (None,None,Composite(),None,None))
    monkeypatch.setattr(m.earlier,'_load_scientific_head',lambda *args,**kwargs:None)
    calls=[]
    def synthetic_train(row,*args):
        calls.append(row['prompt'])
        difference=_direction_input()['measures'][0][0]
        return {'family':row['family'],'prompt_sha256':m.digest(row['prompt']),
               'selected':5,'gold':5,'wrong_endpoint_competitions':1,
               'pair_differences':{'measures':[difference]}}
    monkeypatch.setattr(m,'_row_training_pairs',synthetic_train)
    diagnosis=m.diagnose(root=root,device='cpu')
    assert len(calls)==80
    assert diagnosis['edit']['label']=='measures'
    assert diagnosis['challenge_used_for_selection'] is False
    m.edit(root=root)
    receipt=m.verify_edit(dest)
    assert m.sha(src/cur.HEAD_FILE)==commit['source']['head_sha256']
    edited=load_file(str(dest/'edit'/'checkpoint'/cur.HEAD_FILE))
    assert torch.equal(initial['binding.bias'],edited['binding.bias'])
    assert torch.equal(initial['project.weight'],edited['project.weight'])
    assert receipt['changed_coefficients']<=12
    # The answer file is deliberately unavailable for the entire blind phase.
    monkeypatch.setattr(m.bakeoff.previous,'_capture_inference',lambda *args:
                        ({'eligible':torch.ones(2)},torch.ones(2)))
    inferred=[]
    def fake_infer(*args):
        inferred.append(1)
        return {'g1_objects':[],'pair_margins':[],'binding_edges':[]}
    monkeypatch.setattr(m.frozen,'_infer',fake_infer)
    m.predict(root=root,device='cpu')
    assert len(inferred)==80
    assert m.load(dest/'blind'/'lock.json')['gold_accessed'] is False
    assert len(list(m.rows(dest/'blind'/'predictions.jsonl')))==40
    (dest/'blind'/'lock.json').unlink()
    monkeypatch.setattr(m.structural,'_load_runtime',lambda *args:
                        (_ for _ in ()).throw(AssertionError('GPU SHOULD NOT RELOAD')))
    m.predict(root=root,device='cpu')
    assert len(inferred)==80  # Resume from 40 shards without re-inference.
    secret_file.write_bytes(gold)
    report=m.score(root=root)
    assert report['status']=='MIT_DIRECTION_NO_SAFE_GAIN_ON_FRESH_CHALLENGE'
    assert report['precommitted_gate_passed'] is False
    assert report['parent_unchanged'] is True
    assert report['g3_unlocked'] is False


def test_unknown_checkpoint_or_training_drift_blocks_edit(tmp_path,monkeypatch):
    root=tmp_path/'reports';root.mkdir()
    src,_=_parent(root)
    dataset=root/'train.jsonl';dataset.write_text('test')
    monkeypatch.setattr(m.cur,'load_rows',lambda path:_train_rows())
    monkeypatch.setattr(m.structural,'paths_for',lambda root:{'dataset':dataset})
    monkeypatch.setattr(m.structural,'preflight',lambda paths:({},
                         {'method':'joint','threshold':0.,'out_caps':{},'in_caps':{}}))
    monkeypatch.setattr(m.earlier,'known_prompts',lambda *args:set())
    m.prepare(root=root,cases=40,train_cases=80)
    dataset.write_text('tampered')
    with pytest.raises(ValueError,match='TRAIN_DATA_CHANGED'):
        m.diagnose(root=root,device='cpu')
    dataset.write_text('test')
    manifest=src/cur.MANIFEST
    item=m.load(manifest)
    item['head_sha256']='0'*64
    m.write(manifest,item)
    with pytest.raises(ValueError,match='SOURCE_CHECKPOINT_HASH_DRIFT'):
        m.check(m.location(root,None))
