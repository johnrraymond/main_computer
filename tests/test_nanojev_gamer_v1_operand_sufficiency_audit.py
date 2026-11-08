"""Frozen GAMER operand-sufficiency audit: exact arithmetic and lineage regressions."""
from __future__ import annotations
import argparse
from collections import defaultdict
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pytest

from tools import nanojev_gamer_v1_operand_sufficiency_audit as lab
from tools import nanojev_gamer_v1_token_localization_lab as token
from tools import nanojev_gamer_v1_representation_transfer_audit as prior
from tools import nanojev_gamer_v1_sign_information_probe as oracle


class FakeTokenizer:
    padding_side='right'
    def __call__(self,prompt,**kwargs):
        words=list(re.finditer(r'\S+',prompt))
        return {'input_ids':[int.from_bytes(hashlib.sha256(m.group().encode()).digest()[:4],'big') for m in words],
                'offset_mapping':[(m.start(),m.end()) for m in words]}


@pytest.fixture(scope='module')
def deck():
    items=[]
    for i in range(20):
        stage,q0=prior._profile(i)
        from tools import nanojev_gamer_v1_zero_sign_frob as prompts
        for sign in (-1,0,1):
            q=q0+sign*prior.RADII[0]*(1 if stage.gaps()[0]>stage.gaps()[1] else -1)
            exact=stage.margin(q)
            prompt=prompts._spine_belief_prompt(stage,q)
            items.append(oracle.Case(f'operand.{i}.{sign}','train',f'profile.{i}',
                                     'spine_belief',prompt,exact,'counterfactual_test',Fraction(sign)))
    return items


def test_oracle_labels_verify_exact_order_and_decomposition(deck):
    labels=lab._targets(deck)
    assert set(labels)==set(lab.TASKS)
    for i,c in enumerate(deck):
        q=Fraction(str(labels['q'][i]))
        # Use rational prompt extraction for EXACT oracle identity.
        spans=token._spans(c.prompt)
        q=Fraction(c.prompt[slice(*spans['belief'][0])])
        R,S,T,P=[Fraction(c.prompt[slice(*x)]) for x in spans['payoffs']]
        assert c.target==q*(T-R)+(1-q)*(P-S)
        assert np.isclose(labels['T_minus_R'][i],float(T-R))
        assert np.isclose(labels['P_minus_S'][i],float(P-S))
        assert np.isclose(labels['q_times_T_minus_R'][i],float(q*(T-R)))
        assert np.isclose(labels['one_minus_q_times_P_minus_S'][i],float((1-q)*(P-S)))
        assert np.isclose(labels['signed_margin'][i],float(c.target))
        assert labels['abs_margin'][i]>=0


def test_oracle_rejects_false_target(deck):
    fake=list(deck)
    c=fake[0]
    fake[0]=oracle.Case(c.id,c.split,c.group,c.surface,c.prompt,c.target+1,c.family,c.offset)
    with pytest.raises(ValueError,match='ORACLE_MISMATCH'):
        lab._targets(fake)


def test_full_token_operand_offsets_on_real_prompt_surfaces(deck):
    masks=lab._aligned_token_spans(FakeTokenizer(),deck[:6])
    assert len(masks)==6
    for m in masks:
        for key in ('belief','R','S','T','P','context'):
            assert m[key].dtype==bool and m[key].any()
        assert not np.any(m['R'] & m['T'])
        assert len(m['ids'])>5
    bad=FakeTokenizer();bad.padding_side='left'
    with pytest.raises(ValueError,match='RIGHT_PADDING'):
        lab._aligned_token_spans(bad,deck[:1])


def test_span_projections_are_distinct_and_keep_order(deck):
    mask=lab._aligned_token_spans(FakeTokenizer(),deck[:1])[0]
    x=np.arange(len(mask['ids']),dtype=np.float32)[:,None]+np.zeros((len(mask['ids']),1024),dtype=np.float32)
    out=lab._feature_from_hidden(x,mask,np.arange(min(10,len(x))))
    assert len(out)==10
    assert out['operand_joint'].shape==(5120,)
    assert out['payoff_joint'].shape==(4096,)
    for key in ('belief','R','S','T','P'):
        assert np.allclose(out[key],np.arange(len(x))[mask[key]].mean())
    assert len(np.unique([out[k][0] for k in ('R','S','T','P')]))>=3


def test_lexical_control_does_not_receive_hidden_states(deck):
    rec=lab._aligned_token_spans(FakeTokenizer(),deck[:1])[0]
    a=lab._lexical(rec)
    assert a.shape==(512,) and np.isclose(np.linalg.norm(a),1.)
    assert np.all(a>=0)


def test_kernel_ridge_train_only_scaling_and_holdout_invariance():
    rng=np.random.default_rng(41)
    x=rng.normal(size=(36,13)).astype(np.float32)
    train=np.arange(0,24)
    for kind in lab.KINDS:
        one,g1,p1=lab._encode_train_map(x,train,kind)
        mutated=x.copy();mutated[24:]+=1000
        two,g2,p2=lab._encode_train_map(mutated,train,kind)
        assert np.allclose(g1,g2,rtol=1e-4,atol=1e-4)
        assert np.allclose(one[:24],two[:24],rtol=1e-4,atol=1e-4)
        assert p1==p2


def test_probe_discovers_simple_known_operand_and_sign_signals():
    rng=np.random.default_rng(8)
    sign=np.tile(np.array([-1,0,1]),45)
    q=np.linspace(.1,.9,len(sign))
    x=np.column_stack((q,sign,(sign==0).astype(float),rng.normal(size=len(sign))*.0001)).astype(np.float32)
    y={k:np.zeros(len(sign),dtype=float) for k in lab.TASKS}
    y['q']=q
    y['signed_margin']=sign*.5
    y['abs_margin']=np.abs(sign)*.5
    y['zero']=(sign==0).astype(float)
    y['positive_on_nonzero']=(sign>0).astype(float)
    tr=np.arange(0,90);dev=np.arange(90,114);held=np.arange(114,135)
    prediction,_=lab._predict_tasks(x,y,tr,dev,'linear',.1)
    assert lab._score(y['q'],prediction['q'],'q',held)['mae']<.05
    assert lab._score(y['zero'],prediction['zero'],'zero',held,.5)['min_recall']>.9
    nonzero=held[y['zero'][held]==0]
    assert lab._score(y['positive_on_nonzero'],prediction['positive_on_nonzero'],
                      'positive_on_nonzero',nonzero,.5)['min_recall']>.9


def test_metric_baseline_is_train_median_not_holdout_median():
    n=30
    y={k:np.zeros(n) for k in lab.TASKS}
    y['q'][0:15]=.2;y['q'][15:]=.9
    y['zero']=np.tile([0,0,1],10)
    y['positive_on_nonzero']=np.tile([0,1,0],10)
    p={k:np.ones(n)*.2 for k in lab.TASKS}
    results=lab._evaluate(y,p,np.arange(15,30),np.arange(0,15))
    assert abs(results['q']['train_median_constant_mae']-.7)<1e-8
    assert results['q']['mae']>.6
    assert results['q']['improves_train_constant'] is False


def test_polarity_does_not_count_true_zero_as_negative():
    targets={k:np.zeros(12) for k in lab.TASKS}
    targets['zero']=np.tile([0.,1.,0.],4)
    targets['positive_on_nonzero']=np.tile([0.,0.,1.],4)
    predicted={k:np.zeros(12) for k in lab.TASKS}
    predicted['positive_on_nonzero']=np.tile([-.8,.99,.8],4)
    res=lab._evaluate(targets,predicted,np.arange(12),np.arange(12))
    assert res['positive_on_nonzero']['cases']==8
    assert res['positive_on_nonzero']['balanced_accuracy']==1.


def test_source_rejects_incomplete_report_before_capture(tmp_path):
    p=tmp_path/'source';p.mkdir()
    (p/'final_report.json').write_text(json.dumps({'schema':token.SCHEMA,'status':'BROKEN'}))
    with pytest.raises(ValueError,match='REQUIRES_COMPLETE_FROZEN_TOKEN_AUDIT'):
        lab._source(p)


def test_delta_audit_exact_target_prediction_is_perfect():
    rows=[];targets={k:[] for k in lab.TASKS}
    # One q crossing and a T edit at constant q; code uses replayed row identifiers.
    names=['counterfactual_holdout.payoff.0016.radius.00.negative.spine_belief',
           'counterfactual_holdout.payoff.0016.radius.00.zero.spine_belief',
           'counterfactual_holdout.payoff.0016.radius.00.positive.spine_belief',
           'counterfactual_holdout.payoff.0016.payoff_T_plus.spine_belief']
    for name in names:
        rows.append({'id':name,'target':'0','group':'counterfactual_holdout.payoff.0016.radius.00'})
    for k in targets:targets[k]=np.zeros(4,dtype=float)
    for k in ('signed_margin','q_times_T_minus_R'):
        targets[k]=np.array([-.5,0,.5,.3],dtype=float)
    targets['q']=np.array([.4,.5,.6,.5]);targets['T']=np.array([3,3,3,4])
    targets['T_minus_R']=np.array([1,1,1,2])
    targets['one_minus_q_times_P_minus_S']=np.array([-.9,-.5,-.1,-.5])
    src={'rows':rows,'indices':{'holdout':np.arange(0,3)},
         'variants':{names[3]:{'base_id':names[1]}}}
    result=lab._counterfactual_deltas(src,targets,targets)
    assert result['belief_q']['cases']==2
    assert result['payoff_T']['cases']==1
    assert all(m['delta_mae']==0 for branch in result.values() for m in branch['tasks'].values())


def test_contract_fixed_snapshot_no_production_promotion():
    assert lab.STEP==3000
    assert 'q_times_T_minus_R' in lab.CONTINUOUS
    assert 'one_minus_q_times_P_minus_S' in lab.CONTINUOUS
    assert lab.VIEWS[0]=='belief'
    assert lab.SCHEMA.startswith('nanojev.gamer.v1.')


def test_mocked_run_enforces_fingerprints_and_writes_complete_report(monkeypatch,tmp_path):
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_sign_information_probe as oracle_mod
    from tools import nanojev_gamer_v1_c0_sign_head as c0sign
    prior_path=tmp_path/'prior.json';prior_path.write_text('verified')
    sha=lab.token._sha(prior_path)
    original={'report_path':prior_path,
              'prior':{'joint_dir':tmp_path/'joint','lineage':{'report_path':prior_path,
                       'origin':{'root':tmp_path/'origin'}}}}
    origin={'snapshots':{3000:(tmp_path/'adapter','digest')},
            'training':tmp_path/'training','source':tmp_path/'source',
            'capture':tmp_path/'capture','ckpt':tmp_path/'ckpt'/'file',
            'adapter':tmp_path/'adapter','audit_path':prior_path,
            'causal_path':prior_path}
    rows=[{'id':'dummy','group':'counterfactual_train.payoff.0000.radius.00',
           'target':'0'}]
    src={'origin':origin,'source':{'original':original,'v2_report_path':prior_path},
         'report_path':prior_path,'rows':rows,
         'deck':[object()],
         'indices':{'train':np.array([0]),'dev':np.array([1]),
                    'holdout':np.array([2]),'payoff_variants':np.array([],dtype=int)},
         'variants':{}}
    info={'source':src,'token_report_path':prior_path,
          'token_report':{'frozen_zero_head_sha256':'zero'},'pool_path':prior_path}
    src['rows']=[{'id':'train','group':'counterfactual_train.payoff.0000.radius.00'},
                 {'id':'dev','group':'counterfactual_dev.payoff.0012.radius.00'},
                 {'id':'hold','group':'counterfactual_holdout.payoff.0016.radius.00'}]
    monkeypatch.setattr(lab,'_source',lambda _:info)
    monkeypatch.setattr(stress,'_verify_snapshot',lambda *a:'digest')
    monkeypatch.setattr(lab,'_targets',lambda _: {k:np.zeros(3) for k in lab.TASKS})
    fake_arrays={'final':{key:np.ones((3,1024),dtype=np.float32) for key in lab.VIEWS}}
    monkeypatch.setattr(lab,'_capture',lambda *args:(fake_arrays,{'parity':True}))
    fake_metrics={k:({'min_recall':.5,'balanced_accuracy':.5} if k in lab.BINARY
                     else {'mae':1.,'p90_abs_error':2.}) for k in lab.TASKS}
    monkeypatch.setattr(lab,'_fit_select',lambda *args:({'selected':{},
                      'heldout':fake_metrics,'development':fake_metrics},
                      {k:np.zeros(3) for k in lab.TASKS}))
    monkeypatch.setattr(lab,'_counterfactual_deltas',lambda *args:{'pass':True})
    monkeypatch.setattr(oracle_mod,'deck_hash',lambda *args:'deck')
    monkeypatch.setattr(gamer,'atomic_json',lambda path,result:Path(path).write_text(json.dumps(result)))
    out=tmp_path.parent/(tmp_path.name+'_operand_report')
    report=lab.run(argparse.Namespace(token=tmp_path/'token',output=out,device='cpu',capture_batch=2))
    assert report['promotion']=='BLOCKED_DIAGNOSTIC_ONLY'
    assert report['no_lora_training'] and report['no_c0_training']
    assert report['stages']['final']['belief']['selected']=={}
    assert (out/'final_report.json').is_file()
    with pytest.raises(FileExistsError):lab.run(argparse.Namespace(token=tmp_path/'token',output=out,device='cpu',capture_batch=2))


def test_full_probe_selection_and_recomposition_on_exact_certified_deck(deck):
    target=lab._targets(deck)
    idx={'train':np.arange(0,36),'dev':np.arange(36,48),
         'holdout':np.arange(48,60),
         'holdout_profiles':[(f'profile.{i}',np.arange(3*i,3*i+3)) for i in range(16,20)]}
    rng=np.random.default_rng(42)
    # Synthetic representation: exact q, gaps, and signed margin are readable.
    x=np.column_stack((target['q'],target['T_minus_R'],
                       target['P_minus_S'],target['signed_margin'],
                       target['abs_margin'],target['zero'],
                       target['positive_on_nonzero'],
                       rng.normal(scale=.001,size=len(deck)))).astype(np.float32)
    summary,prediction=lab._fit_select(x,target,idx)
    assert set(summary['selected'])==set(lab.TASKS)
    assert set(summary['recomposition']['heldout'])=={
        'from_four_payoffs_and_q','from_two_gaps_and_q',
        'from_weighted_terms','direct_margin_readout'}
    assert set(summary['by_heldout_profile'])=={f'profile.{i}' for i in range(16,20)}
    assert all(np.isfinite(p).all() for p in prediction.values())
    for k in lab.BINARY:
        assert summary['selected'][k]['threshold'] in lab.THRESHOLDS
        assert summary['heldout'][k]['threshold']==summary['selected'][k]['threshold']


def test_development_model_selection_ignores_heldout_labels():
    # Mutating labels outside TRAIN and DEV must not change selected models.
    rng=np.random.default_rng(82)
    n=48;train=np.arange(30);dev=np.arange(30,39);test=np.arange(39,48)
    sign=np.tile([-1,0,1],16)
    x=np.column_stack((sign.astype(float),np.abs(sign),rng.normal(scale=.001,size=n))).astype(np.float32)
    y={k:np.zeros(n,dtype=float) for k in lab.TASKS}
    y['zero']=(sign==0).astype(float)
    y['positive_on_nonzero']=(sign>0).astype(float)
    y['signed_margin']=sign.astype(float)
    a,_=lab._fit_select(x,y,{'train':train,'dev':dev,'holdout':test})
    modified={k:v.copy() for k,v in y.items()}
    modified['positive_on_nonzero'][test]=1-modified['positive_on_nonzero'][test]
    modified['signed_margin'][test]+=100
    b,_=lab._fit_select(x,modified,{'train':train,'dev':dev,'holdout':test})
    assert a['selected']==b['selected']
