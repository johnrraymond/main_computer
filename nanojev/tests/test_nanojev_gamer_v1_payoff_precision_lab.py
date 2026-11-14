"""Payoff integer classifier, digitwise decoder, zero-line and lineage regressions."""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tools import nanojev_gamer_v1_payoff_precision_lab as lab
from tools import nanojev_gamer_v1_deconfounded_operand_audit as deconf
from tools import nanojev_gamer_v1_operand_sufficiency_audit as operand


@pytest.fixture(scope='module')
def deck():
    cases, tags, ix, profiles, _ = deconf._make_deck()
    labels=operand._targets(cases)
    return cases, tags, ix, labels


def test_exact_integer_targets_and_negative_rejected(deck):
    _,_,ix,y=deck
    ints=lab._integer_targets(y)
    assert all(np.all(vals == np.rint(vals)) for vals in ints.values())
    assert len(ints['R'])==600
    bad=dict(y);bad['T']=y['T'].copy();bad['T'][0]=125.5
    with pytest.raises(ValueError,match='EXACT_NONNEGATIVE_INTEGERS'):
        lab._integer_targets(bad)


def test_class_and_digit_columns_respect_train_support():
    targets={k:np.array([107,112,126,134,109,141,99,143])
             for k in lab.PAYOFFS}
    train=np.array([0,1,2,3])
    matrix,spec=lab._columns(targets,train)
    assert len(matrix)==4 and matrix.ndim==2
    for k in lab.PAYOFFS:
        a,b,classes=spec[(k,'integer_class')]
        assert np.array_equal(classes,np.array([107,112,126,134]))
        assert np.all(matrix[:,a:b].sum(axis=1)==1)
        for pos in range(3):
            a,b,classes=spec[(k,f'digit{pos}')]
            assert (b-a)==10
            assert np.all(matrix[:,a:b].sum(axis=1)==1)


def test_integer_decoder_never_invents_unseen_classes():
    targets={k:np.array([112,128,135,143]) for k in lab.PAYOFFS}
    mat,spec=lab._columns(targets,np.array([0,1,2]))
    scores=np.zeros((4,mat.shape[1]))
    direct=lab._decode(scores,spec,'integer_class')
    assert all(set(v).issubset({112,128,135}) for v in direct.values())
    # Digit-wise recomposition is not limited to previously seen whole integers.
    for k in lab.PAYOFFS:
        for pos,digit in enumerate((1,4,3)):
            a,b,_=spec[(k,f'digit{pos}')]
            scores[:,a+digit]=2
    digits=lab._decode(scores,spec,'digitwise')
    assert all(np.all(v==143) for v in digits.values())
    assert np.all(direct['R']!=143)


def test_kernel_only_scales_from_training_and_rejects_nonfinite():
    features=np.array([[0.,1.],[1,0],[1,1],[2,2],[1000.,-1000.]])
    train=[0,1,2]
    kinds=list(lab._kernel_candidates(features,train,regs=(.1,),kernels=('linear','rbf')))
    assert {k[0] for k in kinds}=={'linear','rbf'}
    changed=features.copy();changed[-1]*=100
    a=list(lab._kernel_candidates(changed,train,regs=(.1,),kernels=('linear','rbf')))
    for item,updated in zip(kinds,a):
        assert np.allclose(item[2],updated[2])
        assert np.allclose(item[3],updated[3])
        assert np.allclose(item[1][:3],updated[1][:3])
    features[0,0]=np.nan
    with pytest.raises(ValueError,match='NONFINITE_FEATURES'):
        list(lab._kernel_candidates(features,train))


def _synthetic(n=140):
    rng=np.random.default_rng(22)
    vals=rng.integers(110,130,size=(n,4))
    targets={k:vals[:,j] for j,k in enumerate(lab.PAYOFFS)}
    # A numerical magnitude signal plus strong representations for each digit.
    frame=np.concatenate((vals.astype(float)/15,
             np.eye(10)[vals[:,0]%10],np.eye(10)[vals[:,1]%10],
             np.eye(10)[vals[:,2]%10],np.eye(10)[vals[:,3]%10]),axis=1)
    return frame,targets


def test_train_only_scores_are_invariant_to_heldout_labels():
    frame,y=_synthetic(100)
    tr=np.arange(0,70)
    cand=list(lab._model_outputs(frame,y,tr,regs=(.1,),kernels=('linear',)))
    altered={k:v.copy() for k,v in y.items()}
    for key in altered:altered[key][70:]+=100
    again=list(lab._model_outputs(frame,altered,tr,regs=(.1,),kernels=('linear',)))
    for a,b in zip(cand,again):
        assert a['decoder']==b['decoder']
        for key in lab.PAYOFFS:
            assert np.array_equal(a['predictions'][key],b['predictions'][key])


def test_digits_and_integer_classification_recover_synthetic_local_signal():
    vals=np.tile(np.arange(112,120),16)
    targets={k:np.roll(vals,i*3) for i,k in enumerate(lab.PAYOFFS)}
    features=np.concatenate([np.eye(10)[targets[k]%10] for k in lab.PAYOFFS],axis=1)
    chosen=list(lab._model_outputs(features,targets,np.arange(96),
                 regs=(.05,),kernels=('linear',)))
    for cand in chosen:
        for name in lab.PAYOFFS:
            assert np.mean(cand['predictions'][name][96:]==targets[name][96:])>=.99


def test_candidate_development_rank_protects_worst_panel():
    core={'exact':.99,'mae':.1};zero={'exact':0.,'mae':3.}
    core2={'exact':.85,'mae':.4};zero2={'exact':.8,'mae':.3}
    assert lab._rank(core2,zero2)>lab._rank(core,zero)


def test_recomposition_uses_true_math_even_when_payoffs_are_rounded(deck):
    _,_,ix,y=deck
    decoded={k:lab._integer_targets(y)[k].copy() for k in lab.PAYOFFS}
    qhat=y['q'].copy()
    result=lab._composition(y,decoded,qhat,ix)
    for panel in lab.PANELS:
        assert result[panel]['exact_q_decoded_integer_payoffs']['mae']<1e-10
        assert result[panel]['decoded_q_decoded_integer_payoffs']['mae']<1e-10
        assert result[panel]['exact_parsed_operands_exact_formula']['mae']<1e-10
    decoded['P']+=1
    altered=lab._composition(y,decoded,qhat,ix)
    assert altered['independent']['exact_q_decoded_integer_payoffs']['mae']>.1


def test_zero_anchor_triples_strict_when_exact_and_blocked_on_error(deck):
    _,tags,ix,y=deck
    values=lab._integer_targets(y)
    result=lab._triples(tags,ix['heldout_zero_anchor'],y,values,y['q'])
    assert result['triples']==8
    assert result['all_four_payoffs_exact_all_three_cases']==8
    assert result['strict_exact_zero_with_parsed_q']==8
    assert result['zero_line_accepted']
    wrong={k:v.copy() for k,v in values.items()}
    zero_i=int(ix['heldout_zero_anchor'][0]);wrong['R'][zero_i]+=1
    rejected=lab._triples(tags,ix['heldout_zero_anchor'],y,wrong,y['q'])
    assert not rejected['zero_line_accepted']
    assert rejected['strict_full_triple_rate']<1


def test_counterfactual_detects_exactness_and_unchanged_drift(deck):
    cases,tags,ix,y=deck
    p=lab._integer_targets(y)
    good=lab._counterfactuals(cases,tags,ix,y,p)
    for key in lab.PAYOFFS:
        assert good[key]['exact_operand_delta_fraction']==1
        assert good[key]['other_payoffs_unchanged_all_fraction']==1
    changed={k:v.copy() for k,v in p.items()}
    ix0=int(next(i for i in ix['heldout_independent']
              if tags[int(i)]['changed']=='T'))
    changed['R'][ix0]+=5
    bad=lab._counterfactuals(cases,tags,ix,y,changed)
    assert bad['T']['other_payoffs_unchanged_all_fraction']<1


def test_integer_metric_reports_unseen_class_separately():
    actual=np.array([100,110,120,130,140])
    guessed=np.array([100,111,120,135,129])
    m=lab._operand_metric(actual,guessed,np.arange(5),np.array([100,110,130]))
    assert m['exact_cases']==2
    assert m['unseen_cases']==2
    assert m['seen_exact']==pytest.approx(1/3)
    assert m['unseen_exact']==pytest.approx(.5)


def test_checkpoints_are_replayable_and_reject_changed_training_features(tmp_path):
    frame,y=_synthetic(60)
    candidates=list(lab._model_outputs(frame,y,np.arange(0,40),
                     regs=(.1,),kernels=('linear',)))
    c=candidates[0]
    chosen={'view':'R','train_panel':'train','kind':c['kind'],
         'decoder':c['decoder'],'reg':c['reg'],
         'predicted':c['predictions']['R'],
         'model':{key:c[key] for key in ('coeff','bias','specs','train','meta','feature_sha256')}}
    ckpt=tmp_path/'model.npz'
    ref=lab._save_checkpoint(ckpt,chosen,frame,'R','a'*64)
    assert ref['type']=='DIAGNOSTIC_INTEGER_READOUT_NO_PRODUCTION_UPDATE'
    assert ckpt.is_file()
    replay=lab._replay_checkpoint(ckpt,frame,expected_source_sha='a'*64)
    assert np.array_equal(replay['R'],c['predictions']['R'])
    with pytest.raises(ValueError,match='REPLAY_LINEAGE_CHANGED'):
        lab._replay_checkpoint(ckpt,frame,expected_source_sha='b'*64)
    with np.load(ckpt,allow_pickle=False) as z:
        assert z['alpha'].shape[0]==40
        assert z['training_feature'].shape[0]==40
    bad=frame.copy();bad[0,0]+=1
    with pytest.raises(ValueError,match='CHECKPOINT_FEATURE_CHANGED'):
        lab._save_checkpoint(tmp_path/'bad.npz',chosen,bad,'R','a'*64)


def test_mock_source_detects_missing_required_contract(tmp_path,monkeypatch):
    reportdir=tmp_path/'prior';reportdir.mkdir()
    (reportdir/'final_report.json').write_text(json.dumps({'status':'wrong'}))
    with pytest.raises(ValueError,match='REQUIRES_COMPLETE_DECONFOUNDED_REPORT'):
        lab._source(reportdir)


def test_source_requires_attested_report_and_canonical_file_path(tmp_path):
    root=tmp_path/'prior';root.mkdir()
    report={'schema':deconf.SCHEMA,'status':'COMPLETE_DECONFOUNDED_OPERAND_DIAGNOSTIC_NO_PROMOTION',
     'promotion':'BLOCKED_DIAGNOSTIC_ONLY','lora_step':3000,
     'no_lora_training':True,'no_c0_training':True,'no_zero_detector_mutation':True,
     'selection':'TRAIN_FIT_DEV_ONLY','old_operand_report_path':str(tmp_path/'missing.json'),
     'old_operand_report_sha256':'deadbeef'}
    (root/'final_report.json').write_text(json.dumps(report))
    with pytest.raises(ValueError,match='OPERAND_LINEAGE_CHANGED'):
        lab._source(root)


def test_model_selection_ignores_all_heldout_targets(deck):
    # Tiny deterministic feature subspace and a single candidate per view.
    _,_,ix,labels=deck
    x=np.stack([labels[k] for k in lab.PAYOFFS],axis=1)
    rng=np.random.default_rng(9)
    view={'R':x + rng.normal(0,.03,x.shape),
          'lexical_numeric':np.zeros_like(x)}
    before,_,_,_=lab._fit_select(view,labels,ix,views_to_try=('R',),
                regs=(.1,),kernels=('linear',),panels=('train_independent',))
    altered={k:v.copy() for k,v in labels.items()}
    for k in lab.PAYOFFS:
        altered[k][ix['heldout']]+=55
    after,_,_,_=lab._fit_select(view,altered,ix,views_to_try=('R',),
                regs=(.1,),kernels=('linear',),panels=('train_independent',))
    for k in lab.PAYOFFS:
        assert before[k]['selection']==after[k]['selection']
        assert before[k]['development']==after[k]['development']


def test_fresh_float32_recapture_is_required_by_contract():
    assert lab.SCHEMA.endswith('integer-payoff-precision.v1')
    assert 'lexical_numeric' not in lab.VIEWS
    assert lab.DECODERS==('integer_class','digitwise')
    assert lab.STEP==3000


def test_mock_full_run_writes_checkpoint_and_no_promotion(deck,tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    cases,tags,ix,labels=deck
    prior=tmp_path/'prior';prior.mkdir()
    baseline=prior/'final_report.json';baseline.write_text('immutable source')
    old_operand=tmp_path/'operand';old_operand.mkdir()
    old_report=old_operand/'final_report.json';old_report.write_text('immutable operand')
    zero=tmp_path/'original_zero.npz';zero.write_bytes(b'protected original zero detector')
    snapshot=tmp_path/'frozen_step3000.pt';snapshot.write_bytes(b'frozen lora')
    old_arch=prior/'frozen_deconfounded_views.npz'
    rng=np.random.default_rng(4)
    arr=np.stack([labels[k] for k in ('q','R','S','T','P')],axis=1)
    views={name:arr.astype(np.float32).copy() for name in lab.VIEWS}
    views['belief']=arr.copy().astype(np.float32)
    views['lexical_numeric']=np.zeros_like(arr,dtype=np.float32)
    np.savez_compressed(old_arch,layer_09__belief=views['belief'].astype(np.float16))
    sha=lambda path:lab.token._sha(path)
    upstream=SimpleNamespace()
    preceding={'path':old_report,'parent':{'pool_path':tmp_path/'token'/'token_span_pools.npz',
        'source':{'report_path':tmp_path/'transfer'/'final_report.json',
                 'source':{'v2_report_path':tmp_path/'v2'/'final_report.json'}}},
        'origin':{'adapter':tmp_path/'adapter','ckpt':tmp_path/'c0'/'checkpoint.pt',
                  'training':tmp_path/'training','source':tmp_path/'source',
                  'capture':tmp_path/'capture','audit_path':tmp_path/'audit'/'report.json'},
        'snapshot':snapshot,'snapshot_sha':'frozen-proof','zero_head':zero}
    prior_report={'deck_sha256':oracle.deck_hash(cases),
        'frozen_zero_head_sha256':sha(zero),
        'feature_archive':{'sha256':sha(old_arch)},
        'stages':{'layer_09':{'tasks':{name:{
            'dev_selected_view':name if name in lab.PAYOFFS else 'belief',
            'selected_readout':{'kernel':'linear','regularization':.1},
            'heldout':{'mae':0.}
            } for name in ('q',)+lab.PAYOFFS}}}}
    source={'root':prior,'report_path':baseline,'report':prior_report,
        'source':preceding,'archive':old_arch,'old_operand_path':old_report}
    monkeypatch.setattr(lab,'_source',lambda path:source)
    monkeypatch.setattr(stress,'_verify_snapshot',lambda path,step:'frozen-proof')
    monkeypatch.setattr(lab.parent,'_captured_features',lambda *_args,**_kwargs:{
       'layer_09':views})
    real_fit=lab._fit_select
    monkeypatch.setattr(lab,'_fit_select',lambda vv,yy,ii:real_fit(vv,yy,ii,
         views_to_try=('R',),regs=(.1,),kernels=('linear',),
         panels=('train_independent',)))
    # Valid full report uses the same oracle controls but avoids full-grid training.
    monkeypatch.setattr(lab,'_q_readout',lambda vv,yy,ii,old,src:yy['q'].copy())
    report=lab.run(SimpleNamespace(deconfounded=prior,output=tmp_path/'precision',
                         device='cpu',capture_batch=3))
    assert report['status']=='COMPLETE_FROZEN_PAYOFF_PRECISION_DIAGNOSTIC'
    assert report['no_lora_training'] and report['no_c0_training']
    assert report['promotion']=='BLOCKED_DIAGNOSTIC_ONLY'
    assert report['recomposition']['integer_extraction']['independent'][
        'exact_parsed_operands_exact_formula']['mae']<1e-10
    assert len(report['diagnostic_checkpoints'])==4
    assert all(item['replay_matches_selected_predictions']
              for item in report['diagnostic_checkpoints'].values())
    assert (tmp_path/'precision'/'final_report.json').is_file()
    assert (tmp_path/'precision'/'frozen_layer09_float32.npz').is_file()
    assert baseline.read_text()=='immutable source'
    assert old_report.read_text()=='immutable operand'
    assert zero.read_bytes()==b'protected original zero detector'
    with pytest.raises(FileExistsError):
        lab.run(SimpleNamespace(deconfounded=prior,output=tmp_path/'precision',
                                 device='cpu',capture_batch=3))
