"""Exact rational GAMER q readout, frozen payoff replay and zero-line regressions."""
from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tools import nanojev_gamer_v1_exact_belief_lab as lab
from tools import nanojev_gamer_v1_deconfounded_operand_audit as parent
from tools import nanojev_gamer_v1_payoff_precision_lab as payoff


@pytest.fixture(scope='module')
def data():
    deck,tags,ix,_,_=parent._make_deck()
    challenge,ct=lab._challenge()
    return deck,tags,ix,challenge,ct,lab._exact_q(deck+challenge)


def test_fresh_challenge_exact_96_and_independent_new_denominators(data):
    old,tags,ix,challenge,ct,qs=data
    assert len(challenge)==96
    assert len({c.id for c in challenge})==96
    assert len([t for t in ct if t['panel']=='independent'])==72
    assert len([t for t in ct if t['panel']=='zero_anchor'])==24
    train_den={qs[int(i)].denominator for i in ix['train']}
    heldout_den={qs[600+i].denominator for i,t in enumerate(ct)
                  if t['panel']=='independent'}
    assert heldout_den=={19,23,29,31,37,41}
    assert heldout_den.isdisjoint(train_den)
    assert all(x.split=='test' for x in challenge)
    assert all(0<q<1 for q in qs)


def test_challenge_zero_is_exact_and_signs_reverse(data):
    _,_,_,challenge,tags,qs=data
    signs=defaultdict(set)
    for i,(case,t) in enumerate(zip(challenge,tags)):
        if t['panel']!='zero_anchor':continue
        sign=(case.target>0)-(case.target<0)
        target={'negative':-1,'zero':0,'positive':1}[t['edit']]
        assert sign==target
        signs[(t['group'],t['surface'])].add(sign)
    assert len(signs)==8
    assert all(s=={-1,0,1} for s in signs.values())


def test_exact_labels_from_prompt_reject_wrong_target(data):
    deck,tags,ix,challenge,ct,qs=data
    for c,q in zip(deck[:30]+challenge[:30],qs[:30]+qs[600:630]):
        from tools.nanojev_gamer_v1_token_localization_lab import _spans
        span=_spans(c.prompt)['belief'][0]
        assert Fraction(c.prompt[slice(*span)])==q
    from tools.nanojev_gamer_v1_sign_information_probe import Case
    case=deck[0]
    wrong=Case(case.id,case.split,case.group,case.surface,case.prompt,
               case.target+1,case.family,case.offset)
    with pytest.raises(ValueError,match='PROMPT_ARITHMETIC_MISMATCH'):
        lab._exact_q([wrong])


def test_onehot_class_targets_use_training_only():
    fs=[Fraction(3,17),Fraction(4,17),Fraction(9,17),Fraction(5,9),Fraction(8,19)]
    y,sp=lab._output_columns(fs,[0,1,2,3])
    assert y.shape[0]==4
    assert Fraction(8,19) not in {Fraction(x) for x in sp['fraction']['classes']}
    assert np.all(y[:,:sp['fraction']['stop']].sum(axis=1)==1)
    assert sp['numerator_digit_5']['classes']==[str(k) for k in range(10)]
    assert sp['denominator_digit_0']['stop']-sp['denominator_digit_0']['start']==10


def test_exact_class_decoder_cannot_invent_fraction():
    q=[Fraction(3,17),Fraction(4,17),Fraction(5,17)]
    matrix,spec=lab._output_columns(q,[0,1,2])
    prediction=lab._decoded(np.zeros((3,matrix.shape[1])),spec,'fraction_class')
    assert set(prediction).issubset(set(q))
    assert Fraction(4,19) not in prediction


def test_numerator_denominator_class_validity_refusal():
    q=[Fraction(1,5),Fraction(4,7),Fraction(1,3)]
    matrix,sp=lab._output_columns(q,[0,1,2])
    scores=np.zeros((2,matrix.shape[1]))
    # force numerator 4, denominator 3 -> invalid q; no repaired oracle.
    for name,val in (('numerator',4),('denominator',3)):
        cfg=sp[name];scores[:,cfg['start']+cfg['classes'].index(str(val))]=4
    assert lab._decoded(scores,sp,'numerator_denominator_class')==[None,None]


def test_digitwise_can_express_unseen_denominator():
    q=[Fraction(3,17),Fraction(4,19),Fraction(7,23)]
    m,sp=lab._output_columns(q,[0,1,2])
    scores=np.zeros((3,m.shape[1]))
    for field,val in (('numerator',37),('denominator',101)):
        for pos,dig in enumerate(f'{val:06d}'):
            cfg=sp[f'{field}_digit_{pos}']
            scores[:,cfg['start']+int(dig)]=9
    assert lab._decoded(scores,sp,'digitwise_fraction')==[Fraction(37,101)]*3


def test_rationalized_regression_has_fixed_bound_and_refuses_invalid():
    q=[Fraction(3,17),Fraction(4,19)]
    m,sp=lab._output_columns(q,[0,1])
    scores=np.zeros((4,m.shape[1])); idx=sp['q_regression']['start']
    scores[:,idx]=[.25,0.,1.1,np.nan]
    assert lab._decoded(scores,sp,'rationalized_regression')==[Fraction(1,4),None,None,None]


def test_exact_metric_records_numerator_denominator_and_unseen():
    true=[Fraction(1,7),Fraction(2,11),Fraction(3,17),Fraction(5,19)]
    pred=[Fraction(1,7),Fraction(3,11),None,Fraction(5,17)]
    m=lab._metric(true,pred,[0,1,2,3],true[:2])
    assert m['exact_cases']==1
    assert m['unseen_cases']==2
    assert m['unseen_exact']==0
    assert m['numerator_exact']==.5
    assert m['denominator_exact']==.5
    assert m['valid_fraction_fraction']==.75


def _synthetic(n=72):
    qs=[Fraction(i%7+3,17) for i in range(n)]
    labels=np.stack([np.array([float(q) for q in qs]),
                     np.eye(7)[np.arange(n)%7].T[0]],axis=1)
    return qs,labels


def test_candidate_predictions_independent_of_dev_and_heldout_q_labels():
    qs,x=_synthetic(63)
    tr=np.arange(0,42)
    a=list(lab._candidates(x,qs,tr,regs=(.1,),kernels=('linear',)))
    perturbed=qs.copy()
    for i in range(42,63):perturbed[i]=Fraction(100+i,301)
    b=list(lab._candidates(x,perturbed,tr,regs=(.1,),kernels=('linear',)))
    for aa,bb in zip(a,b):
        assert aa['mode']==bb['mode']
        assert aa['prediction']==bb['prediction']


def test_rank_prefers_zero_anchor_over_core_only():
    very_high={'exact':1.,'belief_mae_with_invalid_penalty':0.}
    low={'exact':0.,'belief_mae_with_invalid_penalty':1.}
    okay={'exact':.8,'belief_mae_with_invalid_penalty':.1}
    assert lab._rank(okay,okay)>lab._rank(very_high,low)


def test_select_never_uses_heldout_truth_for_model_selection():
    q,x=_synthetic(72)
    ix={'train':np.arange(24),'train_independent':np.arange(24),
        'dev_independent':np.arange(24,40),'dev_zero_anchor':np.arange(40,48),
        'heldout_independent':np.arange(48,64),'heldout_zero_anchor':np.arange(64,72)}
    views={'belief':x,'lexical_numeric':x*.25}
    a,ctrl,n=lab._select(views,q,ix,views_to_try=('belief','lexical_numeric'),
                        regs=(.1,),kernels=('linear',),train_panels=('train',))
    changed=q.copy()
    for i in range(48,72):changed[i]=Fraction(10+i,307)
    b,_,_=lab._select(views,changed,ix,views_to_try=('belief','lexical_numeric'),
                      regs=(.1,),kernels=('linear',),train_panels=('train',))
    assert n==8
    assert (a['view'],a['mode'],a['rank'])==(b['view'],b['mode'],b['rank'])
    assert a['prediction']==b['prediction']


def test_checkpoint_replays_and_refuses_tampering(tmp_path):
    qs,x=_synthetic(64)
    item=next(iter(lab._candidates(x,qs,np.arange(40),regs=(.1,),kernels=('linear',))))
    chosen={**item,'view':'belief','panel':'train'}
    checkpoint=tmp_path/'q.npz'
    reference=lab._save(checkpoint,chosen,x,'a'*64,'b'*64)
    assert reference['replay_matches_selected_predictions']
    decoded,meta=lab._replay(checkpoint,x,'a'*64,'b'*64)
    assert decoded==chosen['prediction']
    with pytest.raises(ValueError,match='LINEAGE_OR_FEATURE_CHANGED'):
        lab._replay(checkpoint,x,'c'*64,'b'*64)
    changed=x.copy();changed[0,0]+=1
    with pytest.raises(ValueError,match='LINEAGE_OR_FEATURE_CHANGED'):
        lab._replay(checkpoint,changed,'a'*64,'b'*64)


def test_composition_requires_only_decoded_q_and_payoffs(data):
    deck,tags,ix,challenge,ct,qs=data
    all_deck=deck+challenge
    all_tags=tags+ct
    indexes={**ix,'challenge_independent':np.arange(600,672),
                   'challenge_zero_anchor':np.arange(672,696)}
    actual={k:np.asarray([int(lab._parse_payoffs(c.prompt)[i]) for c in all_deck])
            for i,k in enumerate(payoff.PAYOFFS)}
    report,margin,exact=lab._recompose(all_deck,all_tags,indexes,qs,qs,actual)
    for key in ('heldout_independent','heldout_zero_anchor',
                'challenge_independent','challenge_zero_anchor'):
        assert report[key]['exact_fraction_margin']==1
        assert report[key]['within_0_05']==1
    wrong=qs.copy();wrong[0]=Fraction(1,3)
    pos=int(ix['heldout_zero_anchor'][0]);wrong[pos]=Fraction(1,3)
    altered,_,_=lab._recompose(all_deck,all_tags,indexes,qs,wrong,actual)
    assert altered['heldout_zero_anchor']['q_exact']<1


def test_complete_zero_triples_require_exact_q_not_approximation(data):
    deck,tags,ix,challenge,ct,qs=data
    alltags=tags+ct
    correct={k:np.array([int(lab._parse_payoffs(c.prompt)[j]) for c in deck+challenge])
             for j,k in enumerate(payoff.PAYOFFS)}
    ixall={**ix,'challenge_zero_anchor':np.arange(672,696)}
    _,margin,exact=lab._recompose(deck+challenge,alltags,ixall,qs,qs,correct)
    assert lab._triples(alltags,ix['heldout_zero_anchor'],qs,qs,margin,exact,expected_groups=8)['accepted']
    assert lab._triples(alltags,ixall['challenge_zero_anchor'],qs,qs,margin,exact,expected_groups=8)['accepted']
    i=int(ixall['challenge_zero_anchor'][1]);bad=qs.copy();bad[i]=qs[i]+Fraction(1,100000)
    _,margin,exact=lab._recompose(deck+challenge,alltags,ixall,qs,bad,correct)
    report=lab._triples(alltags,ixall['challenge_zero_anchor'],qs,bad,margin,exact,expected_groups=8)
    assert not report['accepted'] and report['strict_exact_full_triples']<8


def test_q_only_counterfactual_checks_fraction_exactness(data):
    deck,tags,ix,challenge,ct,qs=data
    t=tags+ct
    h=lab._q_counterfactuals(deck+challenge,t,qs,qs,ix['heldout_independent'])
    c=lab._q_counterfactuals(deck+challenge,t,qs,qs,np.arange(600,672))
    assert h['pairs']==16 and h['exact_fraction_delta']==1
    assert c['pairs']==36 and c['exact_fraction_delta']==1
    bad=qs.copy();bad[600]=None
    changed=lab._q_counterfactuals(deck+challenge,t,qs,bad,np.arange(600,672))
    assert changed['exact_fraction_delta']<1


def test_source_rejects_incomplete_previous_report(tmp_path):
    folder=tmp_path/'prior';folder.mkdir()
    (folder/'final_report.json').write_text(json.dumps({'status':'wrong'}))
    with pytest.raises(ValueError,match='REQUIRES_COMPLETE_PAYOFF_PRECISION_REPORT'):
        lab._source(folder)


def test_new_lab_runs_from_minimal_archive_without_training_old_heads(tmp_path,monkeypatch,data):
    # The no-model integration contract: archive and pre-existing heads are
    # immutable, while a newly selected q head can be checkpointed.
    deck,tags,ix,challenge,ct,qs=data
    import tools.nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_handoff as gamer
    out=tmp_path/'belief'
    root=tmp_path/'precision';root.mkdir()
    prior=root/'final_report.json';prior.write_text('{}')
    archive=root/'frozen_layer09_float32.npz'
    source_dir=tmp_path/'origin';source_dir.mkdir()
    (source_dir/'snap').write_bytes(b'xx');(source_dir/'zero').write_bytes(b'zz')
    views={k:np.stack([np.asarray([float(q),float(q*q)]) for q in qs])[:600]
           for k in parent.VIEWS}
    np.savez_compressed(archive,**{'layer_09__'+k:v.astype('float32') for k,v in views.items()})
    expected={k:np.asarray([int(lab._parse_payoffs(c.prompt)[i]) for c in deck+challenge])
              for i,k in enumerate(payoff.PAYOFFS)}
    # Live heads are not refit; fake replay supplies their known exact outputs.
    for k in payoff.PAYOFFS:(root/f'{k}.npz').write_bytes(k.encode())
    sha=lab.token._sha
    report={'deck_sha256':'deck','frozen_lora_sha256':'sha',
      'frozen_zero_head_sha256':sha(source_dir/'zero'),
      'fresh_feature_archive':{'path':str(archive),'sha256':sha(archive),'dtype':'float32'},
      'diagnostic_checkpoints':{k:{'path':str(root/f'{k}.npz'),
             'sha256':sha(root/f'{k}.npz'),'replay_matches_selected_predictions':True}
                     for k in payoff.PAYOFFS},
      'payoffs':{k:{'selection':{'view':'belief','train_panel':'train_independent'},
             'heldout':{p:{'exact':1.0} for p in payoff.PANELS}} for k in payoff.PAYOFFS},
      'deconfounded_report_sha256':'deconf'}
    origin={'adapter':source_dir/'snap','ckpt':source_dir/'snap',
      'training':source_dir,'source':source_dir,'capture':source_dir,
      'audit_path':source_dir/'audit'}
    child={'origin':origin,'snapshot':source_dir/'snap','zero_head':source_dir/'zero',
         'path':source_dir/'previous','parent':{'pool_path':source_dir/'prior'}}
    previous={'root':source_dir,'source':child}
    src={'root':root,'report':report,'report_path':prior,
         'attested_report_sha256':sha(prior),'previous':previous,'archive':archive}
    monkeypatch.setattr(lab,'_source',lambda _:src)
    monkeypatch.setattr(lab,'_verify_frozen',lambda _:None)
    monkeypatch.setattr(lab.payoff,'_replay_checkpoint',lambda _p,_x,**kw:
        {k:expected[k] for k in payoff.PAYOFFS})
    monkeypatch.setattr(lab.deconf,'_captured_features',lambda _src,_deck,**kw:
        {'layer_09':{k:np.stack([np.asarray([float(q),float(q*q)]) for q in qs[600:]]) for k in views}})
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    monkeypatch.setattr(oracle,'deck_hash',lambda a:'deck' if len(a)==600 else 'fresh')
    # Full q exact synthetic: selected q estimator is an artificial oracle,
    # explicitly not evidence of real CUDA accuracy.
    candidate={ 'view':'belief','panel':'train','kind':'linear','reg':.1,
       'mode':'fraction_class','prediction':qs,
       'train':np.arange(360),'alpha':np.zeros((360,1)),
       'bias':np.zeros(1),'specs':{'fraction':{'start':0,'stop':1,'classes':['1/2']}}}
    monkeypatch.setattr(lab,'_select',lambda *a,**kw:(candidate,candidate,1))
    monkeypatch.setattr(lab,'_save',lambda path,*a:(path.write_bytes(b'test') and
         {'path':str(path),'sha256':sha(path),'replay_matches_selected_predictions':True}))
    result=lab.run(SimpleNamespace(precision=root,output=out,device='cpu',capture_batch=2))
    assert result['promotion']=='BLOCKED_DIAGNOSTIC_ONLY'
    assert result['no_payoff_head_training']
    assert result['component_gate']['passed']
    assert result['zero_anchor_triples']['heldout']['strict_exact_full_triples']==8
    assert result['zero_anchor_triples']['challenge']['strict_exact_full_triples']==8
    assert (out/'final_report.json').exists()
    assert prior.read_text()=='{}'
    assert all((root/f'{k}.npz').read_bytes()==k.encode() for k in payoff.PAYOFFS)
