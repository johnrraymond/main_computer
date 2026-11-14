"""Train-only dual binary regression; zero, polarity, leakage, snapshot replay."""
from __future__ import annotations
import json
from pathlib import Path
from fractions import Fraction
import numpy as np
import pytest

from tools import nanojev_gamer_v1_dual_sign_head as dual
from tools import nanojev_gamer_v1_sign_information_probe as oracle
from tools import nanojev_gamer_v1_c0_sign_head as three


def rows(train_groups=24):
    return [v.public() for v in oracle.make_deck(train_groups,12,12,include_ood=True)]


def features(rs,width=24):
    # Both zero and polarity must be learned; family independent synthetic case.
    labels=dual._labels(rs)
    a=np.zeros((len(rs),width),dtype=np.float32)
    a[:,0]=(labels==0)*10
    a[:,1]=labels*10
    a[:,2]=(labels==-1)*10
    a[:,3]=np.arange(len(rs))%2*.001
    return a


def test_train_deck_has_full_balanced_triplets_and_original_holdouts_are_stable():
    old=rows(24);new=rows(96)
    old_map={r['id']:r for r in old};new_map={r['id']:r for r in new}
    assert all(new_map.get(k)==v for k,v in old_map.items())
    assert sum(r['split']=='train' for r in new)==576
    assert sum(r['split']=='dev' for r in new)==72
    assert sum(r['split']=='test' for r in new)==108
    assert sum(r['family']=='hawk_indifference' for r in new if r['split']=='train')==0
    train=three._partitions(new)['train']
    tri=dual._triples(new,train)
    assert tri.shape==(192,3)
    assert np.array_equal(dual._labels(new)[train][tri],np.tile([-1,0,1],(192,1)))


def test_training_triplets_cannot_lose_zero_or_duplicate_sign():
    r=rows();t=three._partitions(r)['train']
    assert dual._triples(r,t).shape==(48,3)
    edited=[dict(v) for v in r]
    edited[int(t[1])]['target']='7'
    with pytest.raises(ValueError,match='DUPLICATE_TRIPLE_CLASS|INCOMPLETE'):
        dual._triples(edited,t)


def test_zero_takes_precedence_and_polarity_never_outputs_zero():
    cls,probs=dual._combine([.99,.1,.1,.85],[.9,.1,.91,.2],.6)
    assert cls.tolist()==[1,0,2,1]
    assert np.allclose(probs.sum(axis=1),1)
    assert np.all(probs>=0)
    with pytest.raises(ValueError,match='BAD_BINARY_OUTPUT'):
        dual._combine([.1,np.nan],[.2,.2],.5)


def test_independent_binary_head_training_yields_zero_and_polarity():
    r=rows(24);split=three._partitions(r);x=features(r)
    train,dev=split['train'],split['dev'];y=dual._labels(r)
    tri=dual._triples(r,train)
    for task in ('zero','polarity'):
        model=dual._binary(x[train],y[train],x[dev],y[dev],tri,task=task,
                           regularization=.001,epochs=60)
        prob=dual._score(model,x[dev])
        result=dual._binary_diag(y[dev],prob,task=task)
        assert result['balanced_accuracy']>.95
        assert model['development']['dev_min_binary_recall']>.9


def test_polarity_loss_excludes_zeros_but_training_requires_zero_triples():
    r=rows();split=three._partitions(r);t,d=split['train'],split['dev'];x=features(r)
    y=dual._labels(r);tri=dual._triples(r,t)
    with pytest.raises(ValueError,match='BINARY_TRAIN_CONTRACT'):
        dual._binary(x[t],np.ones(len(t),dtype=np.int8),x[d],y[d],tri,
                     task='polarity',regularization=0,epochs=10)
    with pytest.raises(ValueError,match='BINARY_TRAIN_CONTRACT'):
        dual._binary(x[t],y[t],x[d],y[d],tri[:,:2],task='zero',regularization=0,epochs=10)


def test_full_stage_report_uses_real_576_train_and_preserves_hawk_gate(monkeypatch,tmp_path):
    monkeypatch.setattr(dual,'KINDS',('linear',))
    monkeypatch.setattr(dual,'REGS',(.001,))
    r=rows(96);x=features(r)
    artifact=tmp_path/'dual-sign.npz'
    report=dual._fit_stage(r,x,epochs=30,nulls=1,output=artifact)
    assert report['training_cases']==576
    assert report['train_groups']==192
    assert report['dev_cases']==72 and report['test_cases']==108
    assert report['train_only_paired_ordering_weight']==.25
    assert report['model_selection']=='TRAIN_FIT_DEV_ONLY_TEST_NOT_SELECTED'
    assert report['test']['balanced_sign_accuracy']>.95
    assert report['test']['recall']=={'-1':1.0,'0':1.0,'1':1.0}
    assert report['test']['complete_triples']==36
    assert report['test']['by_family']['hawk_indifference']['complete_triples']==12
    assert report['test']['passes_sign_zero_and_triples_gate']
    assert report['beats_all_shuffled_train_nulls']
    assert artifact.exists()
    assert three._sha(artifact)==report['diagnostic_binary_head_artifact']['sha256']
    indices=three._partitions(r)['test']
    cls,probs=dual._reload(artifact,x[indices])
    metrics=three._metrics(r,indices,cls,probabilities=probs)
    assert metrics['balanced_sign_accuracy']==1.0
    with pytest.raises(ValueError,match='WIDTH_MISMATCH'):
        dual._reload(artifact,x[indices,:2])


def test_input_holdout_changes_do_not_change_train_only_scaler_or_map():
    r=rows(96);split=three._partitions(r);x=features(r)
    base=dual._raw_map_spec(x,split)
    changed=x.copy()
    changed[split['test']]=np.random.default_rng(19).normal(size=changed[split['test']].shape)*100
    next_spec=dual._raw_map_spec(changed,split)
    for a,b in zip(base,next_spec):assert np.array_equal(a,b)
    for kind in ('linear','rbf_1.0','rbf_4.0'):
        m1=dual._map_from_spec(x,kind,base)
        m2=dual._map_from_spec(changed,kind,next_spec)
        assert np.array_equal(m1[split['train']],m2[split['train']])
        assert np.array_equal(m1[split['dev']],m2[split['dev']])


def test_degenerate_features_do_not_pass_class_or_zero_gates(monkeypatch):
    monkeypatch.setattr(dual,'KINDS',('linear',))
    monkeypatch.setattr(dual,'REGS',(.001,))
    r=rows(24);x=np.zeros((len(r),8),dtype=np.float32)
    report=dual._fit_stage(r,x,epochs=20,nulls=1)
    assert report['test']['balanced_sign_accuracy']<=1/3+1e-6
    assert report['test']['complete_triples']==0
    assert not report['test']['passes_sign_zero_and_triples_gate']
    assert not report['beats_all_shuffled_train_nulls']


def test_dual_no_test_data_ever_used_for_training_or_dev_threshold(monkeypatch):
    monkeypatch.setattr(dual,'KINDS',('linear',))
    monkeypatch.setattr(dual,'REGS',(.001,))
    r=rows(24);x=features(r)
    a=dual._fit_stage(r,x,epochs=20,nulls=1)
    x2=x.copy();x2[three._partitions(r)['test']]+=200
    b=dual._fit_stage(r,x2,epochs=20,nulls=1)
    assert a['heads']==b['heads']
    assert a['zero_threshold_selected_on_dev']==b['zero_threshold_selected_on_dev']
    assert a['dev']==b['dev']


def test_source_invalid_report_refused_before_gpu(tmp_path):
    root=tmp_path/'three';root.mkdir()
    (root/'final_report.json').write_text(json.dumps({'schema':'not-three','status':'COMPLETE'}))
    with pytest.raises(ValueError,match='REQUIRES_COMPLETED_THREE_CLASS_AUDIT'):
        dual._source(root,'0,3000')


def test_cli_rejects_bad_config_without_loading_models():
    with pytest.raises(ValueError,match='INVALID_FLAGS'):
        dual.main(['--sign-head','any','--output','out','--epochs','0'])
    with pytest.raises(ValueError,match='INVALID_FLAGS'):
        dual.main(['--sign-head','any','--output','out','--triplet-weight','-1'])


def test_report_policy_does_not_promote_or_claim_absence():
    fake={'steps':{'0':{'stages':{}},'3000':{'stages':{}}}}
    for step in fake['steps'].values():
        step['stages']={name:{'test':{'balanced_sign_accuracy':.4,'recall':{'-1':.5,'0':0.,'1':.7},
                         'complete_triples':0,'passes_sign_zero_and_triples_gate':False,
                         'by_family':{'hawk_indifference':{'balanced_sign_accuracy':1/3}}}} for name in dual.STAGES}
        step['control_144']={}
        step['previous_three_class_144']={name:{'balanced_sign_accuracy':1/3,'complete_triples':0}
                                           for name in dual.STAGES}
    s=dual._summary(fake)
    assert not s['stages']['mid_gelu']['dual_576_all_gates']
    assert 'not an information-theoretic proof' in s['interpretation']


def test_source_enforces_extended_576_train_with_old_72_dev_108_test(monkeypatch,tmp_path):
    import sys,types
    original=rows(24);expanded=oracle.make_deck(96,12,12,include_ood=True)
    fake_stress=types.ModuleType('tools.nanojev_gamer_v1_lora_sign_stress')
    fake_stress._dataset=lambda _meta,groups: oracle.make_deck(groups,12,12,include_ood=True)
    monkeypatch.setitem(sys.modules,'tools.nanojev_gamer_v1_lora_sign_stress',fake_stress)
    causal=tmp_path/'causal';causal.mkdir();(causal/'final_report.json').write_text('CAUSAL_IMMUTABLE')
    training=tmp_path/'training';training.mkdir()
    (training/'experiment.json').write_text(json.dumps({
        'train_groups':96,'training_deck_sha256':oracle.deck_hash(expanded)}))
    root=tmp_path/'three';root.mkdir()
    audit=tmp_path/'audit';audit.mkdir()
    prior={'schema':three.SCHEMA,'status':'COMPLETE_SIGN_ONLY_DIAGNOSTIC_NO_PRODUCTION_UPDATES',
           'no_lora_or_c0_training':True,'promotion':'BLOCKED_DIAGNOSTIC_ONLY',
           'source_causal_report':str(causal/'final_report.json'),
           'source_audit_report':str(audit/'final_report.json'),
           'oracle_dataset_sha256':'ORACLE_ORIGINAL',
           'fingerprints':{'causal_report':three._sha(causal/'final_report.json'),'protected':'FINGERPRINT'},
           'snapshots':{'0':'SHA0'}}
    (root/'final_report.json').write_text(json.dumps(prior))
    upstream={'training':training,'audit_path':audit/'final_report.json',
              'meta':{'dataset':[x.public() for x in oracle.make_deck(24,12,12,True)],
                      'dataset_sha256':'ORACLE_ORIGINAL'},
              'rows':original,'snapshots':{0:(tmp_path/'snap0','SHA0')}}
    monkeypatch.setattr(three,'_source',lambda causal_path,steps:upstream)
    monkeypatch.setattr(three,'_protected_hashes',lambda source:{'causal_report':three._sha(causal/'final_report.json'),'protected':'FINGERPRINT'})
    src=dual._source(root,'0')
    assert len(src['rows'])==756
    assert len(src['split']['train'])==576
    assert len(src['split']['dev'])==72
    assert len(src['split']['test'])==108
    assert len(src['old_indices'])==len(original)
    assert [src['rows'][int(i)] for i in src['old_indices']]==original
    # A changed expanded training deck is refused before any GPU model load.
    (training/'experiment.json').write_text(json.dumps({
        'train_groups':96,'training_deck_sha256':'TAMPERED'}))
    with pytest.raises(ValueError,match='EXPANDED_TRAIN_DECK_CHANGED'):
        dual._source(root,'0')


def test_independent_heldout_metrics_separate_zero_from_conditional_polarity():
    r=rows();index=three._partitions(r)['test'];y=dual._labels(r)[index]
    pz=np.where(y==0,.95,.05); pp=np.where(y==1,.9,.1)
    scores=dual._conditional_test_metrics(r,index,pz,pp,.6)
    assert scores['overall']['zero_detection']['balanced_accuracy']==1
    assert scores['overall']['nonzero_polarity']['balanced_accuracy']==1
    assert scores['by_family']['hawk_indifference']['zero_detection']['balanced_accuracy']==1
    # Polarity score does not count zero predictions and so can be good while
    # zero detector fails; both gates must be reported independently.
    broken=dual._conditional_test_metrics(r,index,np.zeros(len(y)),pp,.5)
    assert broken['overall']['zero_detection']['recall_positive_class']==0
    assert broken['overall']['nonzero_polarity']['balanced_accuracy']==1


def test_mocked_end_to_end_frozen_dual_sign_run_writes_report(monkeypatch,tmp_path):
    import sys,types,torch
    from tools import nanojev_gamer_v1_c0_sign_causality as causal
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_sign_information_probe as oracle_module
    monkeypatch.setattr(dual,'KINDS',('linear',))
    monkeypatch.setattr(dual,'REGS',(.001,))
    old=rows(24);expanded=rows(96);values=features(expanded)
    mapping={r['id']:i for i,r in enumerate(expanded)}
    old_indices=np.asarray([mapping[r['id']] for r in old],dtype=int)
    training=tmp_path/'training';training.mkdir()
    preservation=tmp_path/'preservation';preservation.mkdir()
    three_dir=tmp_path/'three';three_dir.mkdir()
    audit=tmp_path/'audit';audit.mkdir()
    causal_dir=tmp_path/'causal';causal_dir.mkdir()
    capture=tmp_path/'capture';capture.mkdir()
    weights=tmp_path/'weights';weights.mkdir()
    adapter=tmp_path/'adapter';adapter.mkdir()
    snapshot=training/'snap0';snapshot.mkdir()
    ckpt=weights/'frozen_c0.pt';ckpt.write_text('FROZEN')
    origin={'meta':{'dataset':old,'dataset_sha256':'OLD_HASH',
                    'lineage':{'model_id':'FAKE','configuration':{'max_length':128,'curvature':0}}},
            'rows':old,'archive':{'lora__final_flat':values[old_indices],
                                  'production_head_predictions':np.zeros(len(old))},
            'audit':{'steps':{'0':{'metrics':{'production_c0':{'test':{
                'balanced_sign_accuracy':1/3,'triple_all_correct':0,'signed_mae':.1}}}}}},
            'snapshots':{0:(snapshot,'SNAP_HASH')},
            'source':preservation,'training':training,'capture':capture,
            'ckpt':ckpt,'adapter':adapter,'audit_path':audit/'final_report.json',
            'causal_path':causal_dir/'final_report.json'}
    prior={'steps':{'0':{'stages':{name:{'test':{'balanced_sign_accuracy':1/3,
                       'complete_triples':0}} for name in dual.STAGES}}}}
    source={'root':three_dir,'prior_path':three_dir/'final_report.json',
            'prior':prior,'source':origin,'deck':oracle_module.make_deck(96,12,12,include_ood=True),
            'rows':expanded,'split':three._partitions(expanded),
            'old_indices':old_indices,'expanded_deck_sha256':'TRAIN_HASH'}
    monkeypatch.setattr(dual,'_source',lambda *a:source)
    monkeypatch.setattr(dual,'_fingerprints',lambda src:{'all_protected':'FROZEN_HASH'})
    fake_c0=types.ModuleType('tools.nanojev_gamer_v1_c0_boundary')
    fake_c0._load_head=lambda meta,device:object()
    fake_c0._verify_baseline=lambda *a: {'max_abs_delta':0.0}
    monkeypatch.setitem(sys.modules,'tools.nanojev_gamer_v1_c0_boundary',fake_c0)
    fake_stress=types.ModuleType('tools.nanojev_gamer_v1_lora_sign_stress')
    fake_stress._verify_snapshot=lambda *a:'SNAP_HASH'
    fake_stress._head_metrics=lambda rs,p:{'test':{'balanced_sign_accuracy':1/3,
                                   'triple_all_correct':0,'signed_mae':.1}}
    monkeypatch.setitem(sys.modules,'tools.nanojev_gamer_v1_lora_sign_stress',fake_stress)
    monkeypatch.setattr(causal,'_read_stages',lambda net,x,**kwargs: (
        {'enc_gelu_3':np.asarray(x), 'mid_gelu':np.asarray(x)},None,None,np.zeros(len(x))))
    monkeypatch.setattr(gamer,'model_components',lambda *a,**k:(torch,object(),object()))
    monkeypatch.setattr(gamer,'atomic_json',lambda path,data: Path(path).write_text(json.dumps(data)))
    monkeypatch.setattr(oracle_module,'_capture_profiles',lambda *a,**k: (
        {'final_flat':values.copy(),'final_mean100':values.copy()},None))
    args=types.SimpleNamespace(sign_head=three_dir,output=tmp_path/'diagnostic',
        steps='0',device='cpu',epochs=20,nulls=1,seed=47,triplet_weight=.25,
        control_144=True,capture_batch=2,head_batch=8)
    result=dual.run(args)
    assert result['status']=='COMPLETE_TWO_BINARY_HEADS_NO_PRODUCTION_UPDATES'
    assert result['training_restricted_to_expanded_576_train_cases']
    assert result['no_original_lora_or_production_c0_training']
    assert result['steps']['0']['stages']['lora_final_mean100']['training_cases']==576
    assert result['steps']['0']['stages']['mid_gelu']['test']['complete_triples']==36
    assert result['steps']['0']['control_144']['mid_gelu']['training_cases']==144
    assert result['steps']['0']['previous_three_class_144']['mid_gelu']['complete_triples']==0
    for stage in dual.STAGES:
        assert (args.output/f'step-0000000-{stage}-dual-sign.npz').is_file()
    assert (args.output/'final_report.json').is_file()
    assert ckpt.read_text()=='FROZEN'
