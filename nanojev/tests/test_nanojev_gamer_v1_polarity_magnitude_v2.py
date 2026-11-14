"""Frozen V2 sign/magnitude diagnostics, split guards and replay contracts."""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
import json
import types
import numpy as np
import pytest

from tools import nanojev_gamer_v1_polarity_magnitude_v2 as lab
from tools import nanojev_gamer_v1_polarity_magnitude_repair as v1
from tools import nanojev_gamer_v1_joint_sign_magnitude_lab as joint
from tools import nanojev_gamer_v1_dual_sign_head as dual
from tools import nanojev_gamer_v1_c0_sign_head as three
from tools import nanojev_gamer_v1_sign_information_probe as oracle


def deck():
    base=oracle.make_deck(96,12,12,True)
    base,_=joint._auxiliary_deck(base)
    base,_=v1._hero_deck(base)
    all_cases,role,profiles=lab._extension_deck(base)
    return all_cases,[c.public() for c in all_cases],role,profiles


def test_new_payoff_profiles_are_exactly_certified_disjoint_and_not_hawk():
    base=oracle.make_deck(96,12,12,True)
    base,_=joint._auxiliary_deck(base)
    base,_=v1._hero_deck(base)
    decked,rows,roles,profiles=deck()
    assert len(base)==972 and len(rows)==1212
    assert rows[:972]==[c.public() for c in base]
    assert {k:len(v) for k,v in roles.items()}=={
        'transfer_train':144,'transfer_dev':48,'fresh_profile':48}
    assert len(profiles)==40
    assert len({tuple(x['payoff']) for x in profiles.values()})==40
    assert all(c.family!='hawk_indifference' for cases in roles.values() for c in cases)
    assert all(c.split=='train' for c in roles['transfer_train'])
    assert all(c.split=='dev' for c in roles['transfer_dev'])
    assert all(c.split=='test' for c in roles['fresh_profile'])
    assert all((Fraction(v.target)>0)-(Fraction(v.target)<0)==s
        for cases in roles.values() for v in cases
        for s in [1 if '.positive.' in v.id else -1 if '.negative.' in v.id else 0])
    assert oracle.deck_hash(decked)==oracle.deck_hash(lab._extension_deck(base)[0])


def test_new_payoff_sign_reversals_are_symmetric_and_ordinally_distinct():
    new,_,_,profiles=deck()
    assert {x['role'] for x in profiles.values()}=={'transfer_train','transfer_dev','fresh_profile'}
    for i in range(40):
        stage,q0=lab._profile(i)
        assert stage.margin(q0)==0
        assert stage.gaps()[0]*stage.gaps()[1]<0
        assert stage.R!=stage.T and stage.S!=stage.P
    grouped={}
    for v in new[972:]:
        key=(v.group,v.surface)
        grouped.setdefault(key,[]).append(v)
    assert len(grouped)==80
    for items in grouped.values():
        targets=sorted([Fraction(c.target) for c in items])
        assert targets[0]<0==targets[1]<targets[2]
        assert targets[2]==-targets[0]


def test_positive_decoder_never_outputs_exact_zero_and_counts_saturation():
    z=np.array([-1.e10,-100,-19.,-18.,-4.,0.,3.,5.,6.,1e10])
    out=lab._decode_log_magnitude(z)
    assert np.all(out>0) and np.isfinite(out).all()
    assert out[0]==out[1]==out[2]==out[3]
    assert out[-1]==out[-2]==out[-3]
    sat=lab._logit_saturation(z)
    assert sat=={'low':4,'high':3,'cases':10}
    with pytest.raises(ValueError,match='NONFINITE'):
        lab._decode_log_magnitude(np.array([np.inf]))


def test_zero_detector_takes_precedence_and_other_sign_boundary_is_adjustable():
    z=np.array([.95,.01,.01,.99])
    p=np.array([.99,.60,.39,.01])
    classes,_=lab._sign_decision(z,p,.5,.5)
    assert classes.tolist()==[1,2,0,1]
    stricter,_=lab._sign_decision(z,p,.5,.7)
    assert stricter.tolist()==[1,0,0,1]
    with pytest.raises(ValueError,match='BAD_SIGN_PROBABILITIES'):
        lab._sign_decision(z,p[:2],.5,.5)


def test_saved_heads_replay_all_kinds_and_reject_mutable_overwrite(tmp_path):
    raw=np.random.default_rng(4).normal(size=(30,6)).astype(np.float32)
    train=np.arange(18)
    for kind in ('linear','rbf_1.0'):
        spec=dual._raw_map_spec(raw,{'train':train})
        mapped=dual._map_from_spec(raw,kind,spec)
        weights=np.linspace(-.15,.15,mapped.shape[1]).astype(np.float32)
        for head in ('polarity','magnitude'):
            path=tmp_path/f'{kind}-{head}.npz'
            result=lab._saved_head(path,raw,train,kind,weights,.2,name=head,epoch=25,threshold=.65)
            expected=mapped.astype(float)@weights.astype(float)+.2
            predicted=lab._replay(path,raw,head)
            expected=dual._sigmoid(expected) if head=='polarity' else lab._decode_log_magnitude(expected)
            assert np.allclose(predicted,expected,atol=1e-5)
            assert result['sha256']==three._sha(path)
            with pytest.raises(FileExistsError,match='HEAD_ALREADY_EXISTS'):
                lab._saved_head(path,raw,train,kind,weights,.2,name=head,epoch=25)
            with pytest.raises(ValueError,match='SAVED_HEAD_MISMATCH'):
                lab._replay(path,raw,'magnitude' if head=='polarity' else 'polarity')


def test_nonzero_magnitude_fit_ignores_zero_targets_and_learns_small_levels():
    # Three exact nonzero magnitudes, paired signs and an intervening zero.
    mag=np.tile([.016,.08,.32],12)
    y=np.ravel(np.column_stack((mag,np.zeros(len(mag)),mag)))
    x=np.column_stack((np.log(y+.003),np.ones(len(y)))).astype(np.float32)
    triples=np.asarray([[3*i,3*i+1,3*i+2] for i in range(len(mag))])
    fit=lab._fit_magnitude_candidate(x,y,x,y,triples,regularization=.001,
                                     epochs=120,pair_weight=.25)
    pred=lab._decode_log_magnitude(x@fit['weights']+fit['bias'])
    assert np.all(pred>0)
    assert np.mean(np.abs(pred[y>0]-y[y>0]))<.10
    assert fit['dev_nonzero_mae']<.10
    with pytest.raises(ValueError,match='MAGNITUDE_TRAIN_CONTRACT'):
        lab._fit_magnitude_candidate(x,np.full(len(y),0.),x,y,triples,
            regularization=.001,epochs=10,pair_weight=.25)


def test_frozen_zero_and_trivial_magnitude_baselines_can_block_promotion():
    cases,rows,roles,_=deck()
    test=np.asarray([i for i,row in enumerate(rows) if row['family']=='hawk_indifference'])
    y=np.asarray([abs(float(Fraction(r['target']))) for r in rows])
    zero=lab._magnitude_stats(rows,test,np.zeros(len(rows)))
    perfect=lab._magnitude_stats(rows,test,y)
    classes=dual._labels(rows)[test]+1
    sign=three._metrics_by_family(rows,test,classes,np.eye(3)[classes])
    first=lab._accept(sign,zero,perfect,zero)
    assert not first['passed']
    assert any('not_better_than_c0_and_zero' in r for r in first['reasons'])
    baseline=lab._magnitude_stats(rows,test,y+1)
    actual=lab._accept(sign,perfect,baseline,zero)
    assert actual['passed'] and actual['promotion']=='BLOCKED_DIAGNOSTIC_ONLY'
    bad=classes.copy();bad[bad==1]=2
    failed=three._metrics_by_family(rows,test,bad,np.eye(3)[bad])
    assert any('sign_recall_0' in r for r in lab._accept(failed,perfect,baseline,zero)['reasons'])


def test_magnitude_stats_distinguish_zero_collapse_from_nonzero_small_precision():
    cases,rows,roles,_=deck()
    idx=np.asarray([i for i,r in enumerate(rows) if r['family']=='fresh_profile'])
    near_zero=np.zeros(len(rows));actual=np.asarray([abs(float(Fraction(r['target']))) for r in rows])
    stats=lab._magnitude_stats(rows,idx,near_zero)
    assert stats['overall']['nonzero_at_least_one_tenth_true_magnitude']==0
    assert stats['overall']['nonzero_within_tight_tolerance']<1
    assert stats['overall']['nonzero_mae']>0
    exact=lab._magnitude_stats(rows,idx,actual)
    assert exact['overall']['nonzero_within_tight_tolerance']==1
    assert exact['overall']['nonzero_at_least_one_tenth_true_magnitude']==1


def test_pair_ordering_reports_ranking_without_claiming_sign_correct():
    _,rows,roles,_=deck();idx=np.asarray([i for i,r in enumerate(rows) if r['family']=='fresh_profile'])
    y=dual._labels(rows)
    pp=np.where(y==1,.9,np.where(y==-1,.8,.5))
    ordering=lab._pair_order(rows,idx,pp)
    assert ordering['pairs']==16 and ordering['positive_order_fraction']==1
    stat=lab._binary_stats(y[idx],pp[idx],.5)
    assert stat['negative_recall']==0  # ordered, but all classified positive


def test_reconstruction_reported_when_components_fail_but_is_not_promoted():
    _,rows,roles,_=deck();test=np.asarray([i for i,r in enumerate(rows) if r['family']=='fresh_profile'])
    y=dual._labels(rows);mag=np.ones(len(rows))*.3
    combined=lab._diagnostic_recombine(rows,test,y+1,mag,np.zeros(len(rows)))
    assert combined['diagnostic_only']
    assert combined['production_reconstruction_forbidden']
    assert combined['overall']['complete_exact_zero']==16
    assert combined['overall']['false_exact_zero_on_nonzero']==0


def test_v2_rejects_incomplete_prior_repair_report(tmp_path):
    path=tmp_path/'repair';path.mkdir()
    (path/'final_report.json').write_text(json.dumps({'schema':v1.SCHEMA,'status':'INCOMPLETE'}))
    with pytest.raises(ValueError,match='REQUIRES_COMPLETED_FROZEN_REPAIR'):
        lab._source(path)


def test_split_reconstruction_does_not_read_heldout_for_training(monkeypatch,tmp_path):
    _,rows,roles,_=deck(); n=len(rows)
    raw=np.random.default_rng(7).normal(size=(n,6)).astype(np.float32)
    y=dual._labels(rows)
    raw[:,0]=y*2.5
    pzero=np.where(y==0,.99,.01)
    train=np.asarray([i for i,r in enumerate(rows) if r['split']=='train' and r['family']=='signed_offset'])[:144]
    dev=np.asarray([i for i,r in enumerate(rows) if r['split']=='dev' and r['family']=='signed_offset'])
    monkeypatch.setattr(lab,'KINDS',('linear',))
    monkeypatch.setattr(lab,'REGS',(.001,))
    monkeypatch.setattr(lab,'PAIR_WEIGHTS',(.25,))
    monkeypatch.setattr(lab,'POL_THRESHOLDS',(.5,))
    p1,t1,m1=lab._fit_polarity(rows,raw,train,dev,pzero,.5,epochs=20,out=tmp_path/'a.npz')
    altered=raw.copy()
    test=np.asarray([i for i,r in enumerate(rows) if r['split']=='test'])
    altered[test]+=1000
    p2,t2,m2=lab._fit_polarity(rows,altered,train,dev,pzero,.5,epochs=20,out=tmp_path/'b.npz')
    assert m1['selected']==m2['selected'] and t1==t2
    assert np.allclose(p1[dev],p2[dev])
    mag1,info1=lab._fit_magnitude(rows,raw,train,dev,epochs=20,out=tmp_path/'c.npz')
    mag2,info2=lab._fit_magnitude(rows,altered,train,dev,epochs=20,out=tmp_path/'d.npz')
    assert info1['selected']['kind']==info2['selected']['kind']
    assert info1['selected']['epoch']==info2['selected']['epoch']
    assert np.allclose(mag1[dev],mag2[dev])


def test_end_to_end_mocked_reports_all_arms_and_preserves_sources(monkeypatch,tmp_path):
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_handoff as gamer
    new,rows,roles,profiles=deck()
    original=rows[:972]
    idx={r['id']:i for i,r in enumerate(rows)}
    indices={
      'train720':np.asarray([i for i,r in enumerate(original) if r['split']=='train' and r['family']!='aux_hero']),
      'train792':np.asarray([i for i,r in enumerate(original) if r['split']=='train']),
      'old_dev':np.asarray([i for i,r in enumerate(original) if r['split']=='dev']),
      'old_test':np.asarray([i for i,r in enumerate(original) if r['split']=='test']),
      'transfer_train':np.asarray([idx[v.id] for v in roles['transfer_train']]),
      'transfer_dev':np.asarray([idx[v.id] for v in roles['transfer_dev']]),
      'fresh':np.asarray([idx[v.id] for v in roles['fresh_profile']]),
    }
    indices['train936']=np.concatenate([indices['train792'],indices['transfer_train']])
    indices['dev120']=np.concatenate([indices['old_dev'],indices['transfer_dev']])
    protect={k:tmp_path/k for k in ('repair','joint','dual','three','training','source','capture','ckptdir','adapter','audit','causal','snapshot')}
    for directory in protect.values():directory.mkdir()
    report_path=protect['repair']/'final_report.json';report_path.write_text('FROZEN_REPAIR_REPORT')
    frozen_zero=tmp_path/'frozen_zero.npz';frozen_zero.write_bytes(b'ORIGINAL_ZERO')
    checkpoint=protect['ckptdir']/'best.pt';checkpoint.write_bytes(b'PRODUCTION_C0')
    sha=lab._sha(checkpoint)
    origin={'snapshots':{3000:(protect['snapshot'],'SNAP_SHA')},
        'training':protect['training'],'source':protect['source'],
        'capture':protect['capture'],'ckpt':checkpoint,
        'adapter':protect['adapter'],'audit_path':protect['audit']/'final_report.json',
        'causal_path':protect['causal']/'final_report.json'}
    old={'prior':{'steps':{'3000':{'cross_game_training':{'test':None}}},
             'fingerprints':{'production_c0':sha}},
        'lineage':{'origin':{'source':origin,'root':protect['three']},
                   'report_path':protect['dual']/'final_report.json'},
        'report_path':protect['joint']/'final_report.json',
        'joint_dir':protect['joint'],'zero_head':frozen_zero}
    labels=dual._labels(rows)
    prior_classes=labels[indices['old_test']]+1
    prior_prob=np.eye(3)[prior_classes].astype(float)
    prior_prob[:,1]=np.where(prior_classes==1,.99,.01)
    prior_sign=three._metrics_by_family(rows,indices['old_test'],prior_classes,prior_prob)
    old['prior']['steps']['3000']['cross_game_training']['test']=prior_sign
    src={'prior':old,'report_path':report_path,'repair':{},'rows':rows,'deck':new,
         'profiles':profiles,'indices':indices,'role_cases':roles}
    monkeypatch.setattr(lab,'_source',lambda _:src)
    monkeypatch.setattr(stress,'_verify_snapshot',lambda *_:'SNAP_SHA')
    yabs=np.asarray([abs(float(Fraction(r['target']))) for r in rows])
    x=np.column_stack((labels,np.array(labels==0,dtype=int),yabs)).astype(np.float32)
    monkeypatch.setattr(lab,'_capture',lambda *a:(x.copy(),np.zeros(len(rows))))
    monkeypatch.setattr(v1,'_zero_proba',lambda *_:(np.where(labels==0,.99,.01),.5))
    monkeypatch.setattr(dual,'_reload',lambda *_:(prior_classes,prior_prob))
    monkeypatch.setattr(lab,'_fit_polarity',lambda *a,**kw:(np.where(labels>0,.95,.05),.5,{'mocked':True}))
    monkeypatch.setattr(lab,'_fit_magnitude',lambda *a,**kw:(yabs.copy(),{'mocked':True}))
    monkeypatch.setattr(gamer,'atomic_json',lambda p,obj:Path(p).write_text(json.dumps(obj)))
    args=types.SimpleNamespace(repair=protect['repair'],output=tmp_path/'v2output',
        device='cpu',capture_batch=2,head_batch=8,epochs=20)
    result=lab.run(args)
    assert result['status']=='COMPLETE_FROZEN_DIAGNOSTICS_NO_PROMOTION'
    assert result['all_arms_promotion_blocked']
    assert len(result['arms'])==3
    assert all('fresh' in a['scores'] and 'original_test' in a['scores']
               for a in result['arms'].values())
    assert report_path.read_text()=='FROZEN_REPAIR_REPORT'
    assert frozen_zero.read_bytes()==b'ORIGINAL_ZERO'
    assert checkpoint.read_bytes()==b'PRODUCTION_C0'
    assert (args.output/'final_report.json').exists()
    assert all(a['scores']['fresh']['diagnostic_reconstruction']['diagnostic_only']
               for a in result['arms'].values())


def test_real_fitted_magnitude_report_serializes_without_weight_arrays(monkeypatch,tmp_path):
    _,rows,_,_=deck();n=len(rows)
    y=np.asarray([abs(float(Fraction(r['target']))) for r in rows])
    raw=np.zeros((n,5),dtype=np.float32)
    raw[:,0]=np.log1p(y)
    raw[:,1]=np.arange(n)%3
    train=np.asarray([i for i,r in enumerate(rows) if r['split']=='train' and r['family']=='signed_offset'])[:144]
    dev=np.asarray([i for i,r in enumerate(rows) if r['split']=='dev' and r['family']=='signed_offset'])
    monkeypatch.setattr(lab,'KINDS',('linear',))
    monkeypatch.setattr(lab,'REGS',(.001,))
    output,info=lab._fit_magnitude(rows,raw,train,dev,epochs=20,out=tmp_path/'mag.npz')
    assert info['training_nonzero_cases']==96
    assert len(output)==n and (output>0).all()
    assert np.allclose(output,lab._replay(tmp_path/'mag.npz',raw,'magnitude'))
    json.dumps(info,allow_nan=False)


def test_individual_fresh_profile_gate_cannot_be_hidden_by_aggregate_accuracy():
    _,rows,_,_=deck()
    idx=np.asarray([i for i,r in enumerate(rows) if r['family']=='fresh_profile'])
    classes=dual._labels(rows)+1
    targets=np.asarray([abs(float(Fraction(r['target']))) for r in rows])
    perfect=lab._payoff_profile_gates(rows,idx,classes,targets)
    assert perfect['profiles']==8 and perfect['passed_profiles']==8
    bad=classes.copy()
    one=rows[int(idx[0])]['group']
    bad[np.asarray([i for i in idx if rows[int(i)]['group']==one])]=2
    failed=lab._payoff_profile_gates(rows,idx,bad,targets)
    assert failed['passed_profiles']==7 and not failed['passed_all']


def test_real_multigame_development_fit_and_report_are_json_serializable(monkeypatch,tmp_path):
    _,rows,roles,_=deck()
    labels=dual._labels(rows)
    target=np.asarray([abs(float(Fraction(r['target']))) for r in rows])
    raw=np.zeros((len(rows),6),dtype=np.float32)
    raw[:,0]=labels*3
    raw[:,1]=(labels==0)*3
    raw[:,2]=np.log1p(target)*3
    raw[:,3]=np.arange(len(rows))%2
    train=np.asarray([i for i,r in enumerate(rows) if r['split']=='train'])
    dev=np.asarray([i for i,r in enumerate(rows) if r['split']=='dev'])
    assert len(train)==936 and len(dev)==120
    pzero=np.where(labels==0,.99,.01)
    monkeypatch.setattr(lab,'KINDS',('linear',))
    monkeypatch.setattr(lab,'REGS',(.001,))
    monkeypatch.setattr(lab,'PAIR_WEIGHTS',(.25,))
    monkeypatch.setattr(lab,'POL_THRESHOLDS',(.5,))
    ppos,threshold,pol=lab._fit_polarity(rows,raw,train,dev,pzero,.5,
        epochs=20,out=tmp_path/'pol.npz')
    mag,info=lab._fit_magnitude(rows,raw,train,dev,epochs=20,out=tmp_path/'mag.npz')
    assert len(ppos)==len(mag)==len(rows)
    assert info['training_nonzero_cases']==624
    assert info['training_pairs']==312
    assert pol['training_pairs']==312
    assert pol['dev']['by_family']['transfer_dev']['cases']==48
    assert all(mag>0)
    json.dumps({'polarity':pol,'magnitude':info},allow_nan=False)


def test_constant_magnitude_baseline_is_an_independent_gate():
    _,rows,_,_=deck()
    idx=np.asarray([i for i,r in enumerate(rows) if r['family']=='hawk_indifference'])
    targets=np.asarray([abs(float(Fraction(r['target']))) for r in rows])
    classes=dual._labels(rows)[idx]+1
    sign=three._metrics_by_family(rows,idx,classes,np.eye(3)[classes])
    perfect=lab._magnitude_stats(rows,idx,targets)
    weak=lab._magnitude_stats(rows,idx,targets+1)
    zero=lab._magnitude_stats(rows,idx,np.zeros(len(rows)))
    assert lab._accept(sign,perfect,weak,zero,constant_metrics=weak)['passed']
    blocker=lab._accept(sign,perfect,weak,zero,constant_metrics=perfect)
    assert not blocker['passed']
    assert any('not_better_than_constant' in reason for reason in blocker['reasons'])


def test_rbf_nonzero_magnitude_path_serializes_and_replays(monkeypatch,tmp_path):
    _,rows,_,_=deck()
    truth=np.asarray([abs(float(Fraction(r['target']))) for r in rows])
    features=np.column_stack((np.log1p(truth),dual._labels(rows),
                              np.arange(len(rows))%3)).astype(np.float32)
    train=np.asarray([i for i,r in enumerate(rows) if r['split']=='train' and r['family']=='signed_offset'])[:144]
    dev=np.asarray([i for i,r in enumerate(rows) if r['split']=='dev' and r['family']=='signed_offset'])
    monkeypatch.setattr(lab,'KINDS',('rbf_1.0',))
    monkeypatch.setattr(lab,'REGS',(.001,))
    mag,details=lab._fit_magnitude(rows,features,train,dev,epochs=20,out=tmp_path/'rbf.npz')
    assert details['selected']['kind']=='rbf_1.0'
    assert np.allclose(mag,lab._replay(tmp_path/'rbf.npz',features,'magnitude'))
    json.dumps(details,allow_nan=False)
