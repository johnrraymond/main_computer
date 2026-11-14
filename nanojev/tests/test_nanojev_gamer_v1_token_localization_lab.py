"""Frozen token localization: structural and mocked integration regression tests."""
from __future__ import annotations
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pytest

from tools import nanojev_gamer_v1_token_localization_lab as lab
from tools import nanojev_gamer_v1_representation_transfer_audit as prior
from tools import nanojev_gamer_v1_polarity_magnitude_v2 as v2
from tools import nanojev_gamer_v1_polarity_magnitude_repair as repair
from tools import nanojev_gamer_v1_joint_sign_magnitude_lab as joint
from tools import nanojev_gamer_v1_sign_information_probe as oracle


class FakeTokenizer:
    padding_side='right'
    def __call__(self,prompt,**kwargs):
        matches=list(re.finditer(r'\S+',prompt))
        return {'input_ids':[int.from_bytes(hashlib.sha256(m.group().encode()).digest()[:4],'big') for m in matches],
                'offset_mapping':[(m.start(),m.end()) for m in matches]}


@pytest.fixture(scope='module')
def counterfactual():
    base=oracle.make_deck(96,12,12,True)
    base,_=joint._auxiliary_deck(base)
    base,_=repair._hero_deck(base)
    base,_,_=v2._extension_deck(base)
    deck,roles,profiles=prior._new_deck(base)
    variants,mapping=lab._payoff_variants(None)
    selected=sum((roles[r] for r in ('counterfactual_train','counterfactual_dev','counterfactual_holdout')),[])
    return selected,variants,mapping,profiles


def test_exact_numerical_spans_on_both_real_prompt_surfaces(counterfactual):
    cases,variants,_,_=counterfactual
    for case in cases[:18]+variants[:8]:
        spans=lab._spans(case.prompt)
        assert len(spans['belief'])==1 and len(spans['payoffs'])==4
        for a,b in spans['belief']+spans['payoffs']:
            assert Fraction(case.prompt[a:b]) or case.prompt[a:b]=='0'
    for text in ('q=1/2 R=1 S=2 T=3 P=4',
                 'probability 1/2 R=1 S=2 T=3',
                 'probability 1/2 R=1 S=2 T=3 P=4 probability 3/4'):
        with pytest.raises(ValueError,match='SPANS_MISSING_OR_AMBIGUOUS'):
            lab._spans(text)


def test_role_separation_and_exact_payoff_edit_at_fixed_belief(counterfactual):
    cases,variants,mapping,profiles=counterfactual
    assert len(cases)==360 and len(variants)==80 and len(mapping)==80
    by_id={c.id:c for c in cases}
    for item in variants:
        match=mapping[item.id]
        baseline=by_id[match['base_id']]
        assert baseline.target==0 and item.target !=0
        assert (item.target>0)-(item.target<0)==match['target_sign']
        assert item.surface==baseline.surface
        assert lab._spans(item.prompt)['belief']!=[]
        assert item.prompt[item.prompt.find('probability'):].split(' ')[1] == baseline.prompt[baseline.prompt.find('probability'):].split(' ')[1]


def test_exact_tokenizer_offsets_and_raster_masks(counterfactual):
    cases,*_=counterfactual
    tok=FakeTokenizer();records=lab._token_masks(tok,cases[:12],max_length=512,curvature=4.)
    assert len(records)==12
    for rec in records:
        assert rec['belief'].shape==(100,) and rec['payoffs'].shape==(100,)
        assert rec['belief'].sum()>0 and rec['payoffs'].sum()>0
        assert np.all(~(rec['belief']&rec['payoffs']))
        assert (rec['belief']|rec['payoffs']|rec['other']).all()
        assert np.all(rec['positions'][:-1]<=rec['positions'][1:])
    tok.padding_side='left'
    with pytest.raises(ValueError,match='RIGHT_PADDING'):
        lab._token_masks(tok,cases[:1],max_length=512,curvature=4.)


def test_numeric_slots_are_grounded_and_other_token_pool_cannot_be_empty(counterfactual):
    cases,*_=counterfactual
    r=lab._token_masks(FakeTokenizer(),cases[:1],max_length=512,curvature=4.)[0]
    assert len(r['ids'])==r['token_count']
    assert r['raster_ids'].shape==(100,)
    x=np.arange(100,dtype=np.float32)[None,:,None]+np.zeros((1,100,1024),np.float32)
    features=lab._pooled_features(x.reshape(1,-1),[r])
    assert features['raster_flat_100'].shape==(1,102400)
    assert features['belief_payoff_joint'].shape==(1,3072)
    for typ,key in [('belief','belief_tokens_mean'),('payoffs','payoff_tokens_mean'),('other','other_tokens_mean')]:
        expected=float(np.arange(100)[r[typ]].mean())
        assert np.allclose(features[key][0],expected)


def test_aligned_belief_swap_refuses_length_or_unrelated_token_changes(counterfactual):
    cases,variants,*_=counterfactual
    a=[c for c in cases if '.negative.' in c.id and c.surface=='spine_belief'][0]
    b=next(c for c in cases if c.id==a.id.replace('.negative.','.zero.'))
    rec=lab._token_masks(FakeTokenizer(),[a,b],max_length=512,curvature=4.)
    eligible,reason,mask=lab._eligible_swap(rec[0],rec[1],'belief')
    assert eligible and reason=='eligible' and mask.sum()>0
    corrupt=dict(rec[1]);corrupt['token_count']+=1
    assert lab._eligible_swap(rec[0],corrupt,'belief')[1]=='token_count_changed'
    corrupt=dict(rec[1]);corrupt['ids']=rec[1]['ids'].copy()
    other_position=next(int(i) for i in range(len(corrupt['ids'])) if i not in set(rec[0]['positions'][rec[0]['belief']]))
    corrupt['ids'][other_position]+=1
    assert lab._eligible_swap(rec[0],corrupt,'belief')[1]=='outside_span_token_ids_changed'


def test_payoff_counterfactual_swap_uses_identical_belief_and_matches_only_t_tokens(counterfactual):
    cases,variants,mapping,_=counterfactual
    baseline=next(c for c in cases if c.id==mapping[variants[0].id]['base_id'])
    pair=lab._token_masks(FakeTokenizer(),[baseline,variants[0]],max_length=512,curvature=4.)
    eligible,reason,mask=lab._eligible_swap(pair[0],pair[1],'payoffs')
    assert eligible,(reason,baseline.prompt,variants[0].prompt)
    assert 0<int(mask.sum())<int(pair[0]['payoffs'].sum())
    assert np.array_equal(pair[0]['raster_ids'][pair[0]['belief']],pair[1]['raster_ids'][pair[1]['belief']])


def test_input_swap_mask_and_placebo_are_distinct(counterfactual):
    cases,*_=counterfactual
    a=next(c for c in cases if '.negative.' in c.id and c.surface=='spine_belief')
    b=next(c for c in cases if c.id==a.id.replace('.negative.','.zero.'))
    mask=lab._token_masks(FakeTokenizer(),[a,b],max_length=512,curvature=4.)
    x=np.ones((2,100,1024),np.float32)
    x[1]*=3
    interventions,meta,skipped=lab._intervention_inputs(x.reshape(2,-1),mask,[(0,1,'belief')])
    assert len(meta)==1 and not skipped and interventions.shape==(3,102400)
    target=interventions.reshape(3,100,1024)
    assert np.all(target[0][mask[0]['belief']]==3)
    assert np.all(target[0][~mask[0]['belief']]==1)
    assert np.all(target[1][mask[0]['belief']]==1)
    assert np.all(target[2][mask[0]['belief']]==1) # target background mean


def test_dual_ridge_probe_finds_simple_signal_on_unseen_examples():
    rng=np.random.default_rng(17)
    sign=np.tile(np.array([-1,0,1],dtype=np.int8),40)
    mag=np.where(sign==0,0,.2)
    x=np.column_stack(((sign==0).astype(float),sign.astype(float),mag,
                       rng.normal(scale=.001,size=len(sign)))).astype(np.float32)
    train=np.arange(0,90);dev=np.arange(90,105);test=np.arange(105,120)
    for kind in lab.KINDS:
        for task in lab.TASKS:
            predicted,info=lab._kernel_fit(x,sign,mag,train,dev,task,kind,.05)
            result=lab._metrics(sign,mag,predicted,test,task)
            if task=='magnitude':assert result['mae']<.05
            else:assert result['min_recall']>.9,(kind,task,result)


def test_probe_scaler_does_not_fit_heldout():
    rng=np.random.default_rng(72)
    x=rng.normal(size=(42,11)).astype(np.float32)
    sign=np.tile(np.array([-1,0,1],dtype=np.int8),14)
    mag=np.where(sign==0,0,.25)
    train=np.arange(0,24);dev=np.arange(24,33)
    a,info=lab._kernel_fit(x,sign,mag,train,dev,'zero','rbf',.5)
    mutated=x.copy();mutated[33:]+=1000
    b,info2=lab._kernel_fit(mutated,sign,mag,train,dev,'zero','rbf',.5)
    assert np.allclose(a[:33],b[:33],atol=1e-6)
    assert info['train_only_scale']==info2['train_only_scale']


def test_source_refuses_incomplete_audit_before_any_cuda(tmp_path):
    d=tmp_path/'audit';d.mkdir()
    (d/'final_report.json').write_text(json.dumps({'schema':prior.SCHEMA,'status':'INCOMPLETE'}))
    with pytest.raises(ValueError,match='REQUIRES_COMPLETED_FROZEN_TRANSFER_AUDIT'):
        lab._load_source(d)


def test_separate_heldout_gate_and_nonpromotion_contract():
    assert lab.STEP==3000 and lab.SCHEMA.startswith('nanojev.gamer.v1.')
    assert lab.FEATURE_NAMES[0]=='raster_flat_100'
    assert set(lab.TASKS)=={'zero','polarity','magnitude'}
    assert lab._metrics(np.array([-1,0,1]),np.array([.3,0,.3]),
                        np.array([-.8,.8,-.8]),np.array([0,1,2]),'zero')['min_recall']==1


def test_matched_c0_interventions_report_effect_without_claiming_lora_computation(monkeypatch,counterfactual):
    from tools import nanojev_gamer_v1_c0_sign_causality as causal
    cases,*_=counterfactual
    a=next(c for c in cases if '.negative.' in c.id and c.surface=='spine_belief')
    b=next(c for c in cases if c.id==a.id.replace('.negative.','.zero.'))
    records=lab._token_masks(FakeTokenizer(),[a,b],max_length=512,curvature=4.)
    data=np.stack([np.zeros((100,1024),np.float32),np.ones((100,1024),np.float32)]).reshape(2,-1)
    def predict(net,features,*,device,batch):
        arr=np.asarray(features,dtype=np.float32)
        return {},None,None,np.mean(arr,axis=1)
    monkeypatch.setattr(causal,'_read_stages',predict)
    report=lab._counterfactual_effects(object(),data,records,[a.public(),b.public()],[(0,1,'belief')],'cpu',4)
    assert report['eligible']==1
    assert report['by_span']['belief']['mean_swap_abs_change']>0
    assert report['by_span']['belief']['mean_placebo_abs_change']>0
    assert 'off-manifold' in report['limitation']


def test_mocked_run_checks_c0_parity_and_writes_report_without_training(monkeypatch,tmp_path):
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_c0_boundary as c0
    from tools import nanojev_gamer_v1_c0_sign_causality as causal
    sourcefile=tmp_path/'prior.json';sourcefile.write_text('verified')
    zero=tmp_path/'zero.npz';zero.write_bytes(b'unchanged')
    archive=tmp_path/'features.npz'
    np.savez_compressed(archive,enc_gelu_3=np.zeros((4,512),dtype=np.float32))
    fake={'report_path':sourcefile,'source':{'v2_report_path':sourcefile,'original':{
          'report_path':sourcefile,'prior':{'zero_head':zero,'joint_dir':tmp_path/'joint',
          'lineage':{'report_path':sourcefile,'origin':{'root':tmp_path/'origin'}}}}},
          'origin':{'training':tmp_path/'training','source':tmp_path/'source',
            'capture':tmp_path/'capture','ckpt':tmp_path/'ckpt'/'file',
            'adapter':tmp_path/'adapter','audit_path':sourcefile,
            'causal_path':sourcefile,'snapshots':{3000:(tmp_path/'lora','digest')},
            'meta':{}},
          'indices':{'train':np.arange(0,2),'dev':np.arange(2,3),
                     'holdout':np.arange(3,4),'payoff_variants':np.arange(4,5)},
          'deck':[object()], 'rows':[{'id':'one','target':'0','group':'x','surface':'s'}]*5,
          'variants':{},'stage_archive':archive,'original_indices':np.arange(4), 'report':{}}
    monkeypatch.setattr(lab,'_load_source',lambda _:fake)
    monkeypatch.setattr(stress,'_verify_snapshot',lambda *args:'digest')
    data=np.zeros((5,102400),dtype=np.float32)
    monkeypatch.setattr(lab,'_capture',lambda *args:(data,[{}]*5,{'parity':True}))
    monkeypatch.setattr(lab,'_pooled_features',lambda *args:{'raster_flat_100':data,
                'raster_mean100':np.zeros((5,1024),np.float32)})
    monkeypatch.setattr(lab,'_fit_features',lambda *args:{'raster_flat_100':{'tasks':{}}})
    monkeypatch.setattr(c0,'_load_head',lambda *args:object())
    monkeypatch.setattr(causal,'_read_stages',lambda *args,**kwargs:
                        ({'enc_gelu_3':np.zeros((4,512),dtype=np.float32)},None,None,np.zeros(4)))
    monkeypatch.setattr(lab,'_make_swaps',lambda *args:[])
    monkeypatch.setattr(lab,'_counterfactual_effects',lambda *args,**kwargs:{'eligible':0})
    monkeypatch.setattr(oracle,'deck_hash',lambda *args:'mock_deck')
    target=tmp_path.parent/(tmp_path.name+'_report')
    result=lab.run(argparse.Namespace(audit=tmp_path/'audit',output=target,
                                      device='cpu',capture_batch=2,head_batch=4))
    assert result['no_production_training'] is True
    assert result['promotion']=='BLOCKED_DIAGNOSTIC_ONLY'
    assert result['encoder_stage_parity_max_abs_delta']==0
    assert (target/'final_report.json').is_file()
    assert result['feature_archive']['sha256']==lab._sha(target/'token_span_pools.npz')
    assert zero.read_bytes()==b'unchanged'
    with pytest.raises(FileExistsError):
        lab.run(argparse.Namespace(audit=tmp_path/'audit',output=target,
                                   device='cpu',capture_batch=2,head_batch=4))
