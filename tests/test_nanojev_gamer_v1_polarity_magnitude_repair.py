"""Frozen dual-path specialists: zero preservation, hawk transfer, magnitude gates."""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
import json
import types
import numpy as np
import pytest

from tools import nanojev_gamer_v1_polarity_magnitude_repair as lab
from tools import nanojev_gamer_v1_joint_sign_magnitude_lab as joint
from tools import nanojev_gamer_v1_dual_sign_head as dual
from tools import nanojev_gamer_v1_c0_sign_head as three
from tools import nanojev_gamer_v1_sign_information_probe as oracle


def deck():
    old=oracle.make_deck(96,12,12,True)
    both,_=joint._auxiliary_deck(old)
    extended,extra=lab._hero_deck(both)
    return extended,[v.public() for v in extended],extra


def synthetics(rows):
    y=dual._labels(rows)
    x=np.zeros((len(rows),12),dtype=np.float32)
    x[:,0]=y*4
    x[:,1]=(y==0)*5
    x[:,2]=np.asarray([float(abs(Fraction(r['target']))) for r in rows])
    x[:,3]=(np.arange(len(rows))%2)*.005
    return x


def test_hero_expansion_is_mathematically_certified_and_hawk_untouched():
    extended,rows,extra=deck()
    old,_=joint._auxiliary_deck(oracle.make_deck(96,12,12,True))
    assert len(old)==900 and len(extended)==972 and len(extra)==72
    assert [v.public() for v in old]==rows[:900]
    assert {v.family for v in extra}=={'aux_hero'}
    assert {v.split for v in extra}=={'train'}
    assert all('hawk' not in v.id for v in extra)
    split=three._partitions(rows)
    assert (len(split['train']),len(split['dev']),len(split['test']))==(792,72,108)
    assert dual._triples(rows,split['train']).shape==(264,3)
    assert sum(rows[int(i)]['family']=='hawk_indifference' for i in split['test'])==36
    assert all(rows[int(i)]['family']!='hawk_indifference' for i in split['train'])
    assert all(rows[int(i)]['family']!='hawk_indifference' for i in split['dev'])
    assert oracle.deck_hash(extra)==oracle.deck_hash(lab._hero_deck(old)[1])


def test_saved_magnitude_and_polarity_heads_replay_linear_and_rbf(tmp_path):
    x=np.random.default_rng(2).normal(size=(30,8)).astype(np.float32)
    train=np.arange(18)
    for kind in ('linear','rbf_1.0','rbf_4.0'):
        mapped=three._train_only_maps(x,{'train':train,'dev':np.arange(18,24),'test':np.arange(24,30)})[0][kind]
        weight=np.linspace(-.2,.3,mapped.shape[1]).astype(np.float32)
        for head in ('polarity','magnitude'):
            path=tmp_path/f'{kind}-{head}.npz'
            saved=lab._store_head(path,x,train,kind,weight,.15,30,head=head)
            value=lab._head_replay(path,x,head)
            expect=mapped.astype(np.float64)@weight.astype(np.float64)+.15
            target=dual._sigmoid(expect) if head=='polarity' else lab._decode_magnitude(expect)
            assert np.allclose(value,target,atol=1e-5)
            assert saved['sha256']==three._sha(path)
            with pytest.raises(FileExistsError,match='CHECKPOINT_ALREADY_EXISTS'):
                lab._store_head(path,x,train,kind,weight,.15,30,head=head)
            with pytest.raises(ValueError,match='SCHEMA_MISMATCH'):
                lab._head_replay(path,x,'magnitude' if head=='polarity' else 'polarity')


def test_magnitude_train_fits_absolute_values_independent_of_sign():
    import torch
    # All samples have exact magnitude tied to a positive feature; zero anchors
    # and negative/positive matched pairs share equal |target|.
    pairs=np.array([[3*i,3*i+1,3*i+2] for i in range(20)])
    y=np.tile([.5,0.,.5],20)
    x=np.column_stack((y,np.ones(len(y)))).astype(np.float32)
    model=lab._fit_magnitude_candidate(x,y,x,y,pairs,reg=.001,epochs=120,paired_weight=.25)
    pred=lab._decode_magnitude(x@model['weights']+model['bias'])
    assert np.mean(np.abs(pred-y))<.1
    assert model['dev_mae']<.1
    assert np.isfinite(pred).all() and np.all(pred>=0)


def test_magnitude_metrics_and_acceptance_block_if_zero_or_hawk_fails():
    _,rows,_=deck();test=three._partitions(rows)['test']
    target=np.asarray([abs(float(Fraction(r['target']))) for r in rows])
    good=lab._magnitude_metrics(rows,test,target,.05)
    assert good['all']['mae']==0
    assert good['all']['within_tolerance']==1
    baseline=lab._magnitude_metrics(rows,test,target+1.,.05)
    metrics=three._metrics_by_family(rows,test,dual._labels(rows)[test]+1,
                 np.eye(3)[dual._labels(rows)[test]+1])
    yes=lab._acceptance(metrics,good,baseline,tolerance=.05)
    assert yes['passed'] and not yes['reasons']
    bad_zero=dual._labels(rows)[test]+1
    bad_zero=np.where(bad_zero==1,2,bad_zero)
    fail=three._metrics_by_family(rows,test,bad_zero,np.eye(3)[bad_zero])
    denied=lab._acceptance(fail,good,baseline,tolerance=.05)
    assert not denied['passed']
    assert any('sign_recall_0' in reason for reason in denied['reasons'])
    denied_mag=lab._acceptance(metrics,baseline,baseline,tolerance=.05)
    assert not denied_mag['passed']
    assert any('magnitude_mae_not_improved' in s for s in denied_mag['reasons'])


def test_hawk_polarity_geometry_traces_all_twelve_true_pairs():
    _,rows,_=deck();t=three._partitions(rows)['test']
    y=dual._labels(rows)
    p=np.where(y==1,.95,np.where(y==-1,.05,.5)).astype(float)
    geom=lab._hawk_polarity_geometry(rows,t,p)
    assert geom['pairs']==12
    assert geom['positive_greater_than_negative_count']==12
    assert geom['mean_polarity_probability_delta']>.8
    assert geom['diagnostic_only_not_used_for_selection']


def test_no_test_features_participate_in_head_model_selection(monkeypatch,tmp_path):
    monkeypatch.setattr(lab,'KINDS',('linear',))
    monkeypatch.setattr(lab,'REGS',(.001,))
    _,rows,_=deck();x=synthetics(rows);split=three._partitions(rows)
    train=np.asarray(split['train'])[:144]
    # Use full complete groups for the short synthetic experiment.
    train=np.asarray([i for i in split['train'] if rows[int(i)]['family']=='signed_offset'])[:144]
    p1,info1=lab._fit_polarity(rows,x,train,split['dev'],triple_weight=.5,
                              epochs=20,output=tmp_path/'first.npz')
    changed=x.copy();changed[split['test']]+=500
    p2,info2=lab._fit_polarity(rows,changed,train,split['dev'],triple_weight=.5,
                              epochs=20,output=tmp_path/'second.npz')
    assert info1['selected']==info2['selected']
    assert np.allclose(p1[split['dev']],p2[split['dev']])
    assert info1['selection']=='TRAIN_FIT_DEV_SELECTED_TEST_NOT_SELECTED'
    assert info1['pairs']==48
    m1,mm1=lab._fit_magnitude(rows,x,train,split['dev'],epochs=20,
        paired_weight=.2,tolerance=.05,output=tmp_path/'mag1.npz')
    m2,mm2=lab._fit_magnitude(rows,changed,train,split['dev'],epochs=20,
        paired_weight=.2,tolerance=.05,output=tmp_path/'mag2.npz')
    assert mm1['selected']==mm2['selected']
    assert np.allclose(m1[split['dev']],m2[split['dev']])
    assert mm1['selection']=='TRAIN_FIT_DEV_SELECTED_TEST_NOT_SELECTED'


def test_zero_detector_replayed_without_training_or_alteration(tmp_path):
    x=np.random.default_rng(7).normal(size=(12,8)).astype(np.float32)
    spec=dual._raw_map_spec(x,{'train':np.arange(6)})
    mu,std,ref,median=spec
    p=tmp_path/'dual.npz'
    np.savez_compressed(p,schema=np.asarray(dual.SCHEMA),mean=mu,std=std,
        reference=ref,median_distance=np.asarray(median),zero_kind=np.asarray('linear'),
        zero_weight=np.ones(8,dtype=np.float32),zero_bias=np.asarray(.1),
        zero_threshold=np.asarray(.55))
    before=three._sha(p)
    actual,threshold=lab._zero_proba(p,x)
    mapped=dual._map_from_spec(x,'linear',spec)
    assert np.allclose(actual,dual._sigmoid(mapped.sum(axis=1)+.1))
    assert threshold==.55 and before==three._sha(p)


def test_source_rejects_incomplete_or_tampered_reports(monkeypatch,tmp_path):
    root=tmp_path/'joint';root.mkdir()
    path=root/'final_report.json'
    path.write_text(json.dumps({'status':'INCOMPLETE'}))
    with pytest.raises(ValueError,match='REQUIRES_COMPLETED_FROZEN_JOINT_AUDIT'):
        lab._source(root)
    prior={'schema':joint.SCHEMA,
           'status':'COMPLETE_FROZEN_DUAL_PATH_AND_UNSEEN_PROFILE_TRANSFER',
           'promotion':'BLOCKED_DIAGNOSTIC_ONLY',
           'no_production_lora_or_c0_training':True,
           'original_72_dev_and_108_test_unchanged':True,
           'source_dual_report':str(tmp_path/'dual'/'final_report.json'),
           'steps':{'3000':{'snapshot_sha256':'SHA',
                            'cross_game_training':{'diagnostic_binary_head_artifact':{
                                'path':'MISSING','sha256':'WRONG',
                                'type':'DIAGNOSTIC_TWO_BINARY_SIGN_HEADS_NO_MAGNITUDE'}}}},
           'fingerprints':{},'combined_deck_sha256':'B','additional_auxiliary_train_deck_sha256':'A',
           'hawk_test_profile_trained':False}
    path.write_text(json.dumps(prior))
    (tmp_path/'dual').mkdir()
    (tmp_path/'dual'/'final_report.json').write_text('UNCHANGED')
    monkeypatch.setattr(joint,'_source',lambda *_: {
        'report_path':tmp_path/'dual'/'final_report.json',
        'origin':{'source':{'snapshots':{3000:(tmp_path/'adapter','SHA')}}},
        'combined_deck_sha256':'B','additional_deck_sha256':'A'})
    monkeypatch.setattr(dual,'_fingerprints',lambda *_:{})
    with pytest.raises(ValueError,match='PRIOR_LINEAGE_OR_HOLDOUT_CHANGED'):
        lab._source(root)


def test_end_to_end_mocked_blocks_reconstruction_without_component_pass(monkeypatch,tmp_path):
    import torch
    from tools import nanojev_gamer_v1_sign_information_probe as oracle_mod
    from tools import nanojev_gamer_v1_c0_sign_causality as causal
    from tools import nanojev_gamer_v1_c0_boundary as c0
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_handoff as gamer

    expanded,rows,extra=deck();split=three._partitions(rows)
    original_rows=[v.public() for v in oracle.make_deck(24,12,12,True)]
    index={r['id']:i for i,r in enumerate(rows)}
    orig_ix=np.asarray([index[r['id']] for r in original_rows],dtype=int)
    dirs={k:tmp_path/k for k in ('joint','dual','three','training','source','capture','checkpoint','adapter','audit','causal','snapshot')}
    for d in dirs.values():d.mkdir()
    for k in ('joint','dual'):(dirs[k]/'final_report.json').write_text('UNMODIFIED')
    ckpt=dirs['checkpoint']/'best.pt';ckpt.write_text('FROZEN')
    zero_artifact=tmp_path/'prior_zero.npz';zero_artifact.write_bytes(b'FROZEN')
    meta={'lineage':{'model_id':'MOCK','configuration':{'max_length':128,'curvature':0}}}
    base={'meta':meta,'rows':original_rows,
        'archive':{'lora__final_flat':np.zeros((len(original_rows),8),np.float32),
                   'production_head_predictions':np.zeros(len(original_rows))},
        'audit':{'steps':{'3000':{'metrics':{'production_c0':{'test':{'triple_all_correct':0,
             'signed_mae':.1}}}}}},
        'snapshots':{3000:(dirs['snapshot'],'SHA')},'training':dirs['training'],
        'source':dirs['source'],'capture':dirs['capture'],'ckpt':ckpt,
        'adapter':dirs['adapter'],'audit_path':dirs['audit']/'final_report.json',
        'causal_path':dirs['causal']/'final_report.json'}
    lineage={'report_path':dirs['dual']/'final_report.json',
        'origin':{'source':base,'old_indices':orig_ix,'root':dirs['three']}}
    prior={'steps':{'3000':{'cross_game_training':{'test':{
        'balanced_sign_accuracy':1.,'complete_triples':36}}}}}
    src={'joint_dir':dirs['joint'],'prior_path':dirs['joint']/'final_report.json',
         'prior':prior,'lineage':lineage,'deck':expanded,'extra':extra,
         'rows':rows,'split':split,'zero_head':zero_artifact,
         'fingerprints':{'dual_report_sha256':three._sha(dirs['dual']/'final_report.json'),
                         'other':'HASH'}}
    monkeypatch.setattr(lab,'_source',lambda *_:src)
    monkeypatch.setattr(dual,'_fingerprints',lambda *_:{'other':'HASH'})
    monkeypatch.setattr(c0,'_load_head',lambda *_:object())
    monkeypatch.setattr(c0,'_verify_baseline',lambda *_:None)
    monkeypatch.setattr(stress,'_verify_snapshot',lambda *_:'SHA')
    monkeypatch.setattr(stress,'_head_metrics',lambda *_:{'test':{'triple_all_correct':0,
        'signed_mae':.1,'balanced_sign_accuracy':1/3}})
    monkeypatch.setattr(causal,'_read_stages',lambda _,features,**kwargs:(
        {},None,None,np.zeros(len(features),dtype=np.float64)))
    monkeypatch.setattr(gamer,'model_components',lambda *_ ,**kw:(torch,object(),object()))
    monkeypatch.setattr(gamer,'atomic_json',lambda path,value:Path(path).write_text(json.dumps(value)))
    x=synthetics(rows)
    monkeypatch.setattr(oracle_mod,'_capture_profiles',lambda *a,**kw:(
        {'final_mean100':x.copy(),'final_flat':np.zeros((len(rows),8),dtype=np.float32)},None))
    label=dual._labels(rows)
    monkeypatch.setattr(lab,'_zero_proba',lambda *_:(np.where(label==0,.99,.01),.5))
    cls=label[split['test']]+1
    prior_probs=np.eye(3)[cls].astype(float)
    prior_probs[:,1]=np.where(label[split['test']]==0,.99,.01)
    monkeypatch.setattr(dual,'_reload',lambda *_:(cls,prior_probs))
    monkeypatch.setattr(lab,'_fit_polarity',lambda *a,**kw:(np.where(label>0,.99,.01),
        {'selection':'TRAIN_FIT_DEV_SELECTED_TEST_NOT_SELECTED'}))
    monkeypatch.setattr(lab,'_fit_magnitude',lambda *a,**kw:(np.zeros(len(rows)),
        {'selection':'TRAIN_FIT_DEV_SELECTED_TEST_NOT_SELECTED'}))
    args=types.SimpleNamespace(joint=dirs['joint'],output=tmp_path/'result',device='cpu',
        epochs=20,polarity_triplet_weight=.5,magnitude_pair_weight=.25,
        magnitude_tolerance=.05,capture_batch=2,head_batch=8)
    r=lab.run(args)
    assert r['status']=='COMPLETE_INDEPENDENT_DIAGNOSTICS_NO_PRODUCTION_PROMOTION'
    assert r['zero_detector_unchanged']
    assert all(a['reconstruction']['status']=='BLOCKED_COMPONENT_ACCEPTANCE_FAILED'
               for a in r['arms'].values())
    assert (args.output/'final_report.json').exists()
    assert ckpt.read_text()=='FROZEN'
    assert (dirs['joint']/'final_report.json').read_text()=='UNMODIFIED'
    assert zero_artifact.read_bytes()==b'FROZEN'


def test_source_real_contract_reconstructs_lineage_and_checks_zero_checksum(monkeypatch,tmp_path):
    orig=oracle.make_deck(96,12,12,True)
    existing,aux=joint._auxiliary_deck(orig)
    current_root=tmp_path/'joint';current_root.mkdir()
    dual_root=tmp_path/'dual';dual_root.mkdir()
    (dual_root/'final_report.json').write_text('FROZEN_DUAL')
    zero_head=tmp_path/'zero.npz';zero_head.write_bytes(b'IMMUTABLE_ZERO_HEAD')
    lineage={'report_path':dual_root/'final_report.json',
             'origin':{'source':{'snapshots':{3000:(tmp_path/'snap3000','SHA_3000')}}},
             'combined_deck_sha256':oracle.deck_hash(existing),
             'additional_deck_sha256':oracle.deck_hash(aux),
             'deck':existing,'rows':[x.public() for x in existing]}
    monkeypatch.setattr(joint,'_source',lambda path,steps: (
        lineage if int(steps[0])==3000 else (_ for _ in ()).throw(AssertionError())))
    monkeypatch.setattr(dual,'_fingerprints',lambda *_:{'original':'FIXED'})
    prior={'schema':joint.SCHEMA,'status':'COMPLETE_FROZEN_DUAL_PATH_AND_UNSEEN_PROFILE_TRANSFER',
           'promotion':'BLOCKED_DIAGNOSTIC_ONLY',
           'no_production_lora_or_c0_training':True,
           'original_72_dev_and_108_test_unchanged':True,
           'hawk_test_profile_trained':False,
           'source_dual_report':str(dual_root/'final_report.json'),
           'fingerprints':{'dual_report_sha256':three._sha(dual_root/'final_report.json'),
                            'original':'FIXED'},
           'combined_deck_sha256':oracle.deck_hash(existing),
           'additional_auxiliary_train_deck_sha256':oracle.deck_hash(aux),
           'steps':{'3000':{'snapshot_sha256':'SHA_3000','cross_game_training':{
               'diagnostic_binary_head_artifact':{
                   'type':'DIAGNOSTIC_TWO_BINARY_SIGN_HEADS_NO_MAGNITUDE',
                   'path':str(zero_head),'sha256':three._sha(zero_head)}}}}}
    (current_root/'final_report.json').write_text(json.dumps(prior))
    good=lab._source(current_root)
    assert len(good['deck'])==972
    assert len(good['split']['train'])==792
    assert good['zero_head']==zero_head
    assert good['rows'][:900]==[v.public() for v in existing]
    zero_head.write_bytes(b'FORBIDDEN_MUTATION')
    with pytest.raises(ValueError,match='FROZEN_ZERO_ARTIFACT_CHANGED'):
        lab._source(current_root)
