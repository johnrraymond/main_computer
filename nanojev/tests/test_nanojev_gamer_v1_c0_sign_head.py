"""Frozen sign-head: split isolation, real triples, class balance, lineage, no promotion."""
from __future__ import annotations

import json
import sys
import types
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_c0_sign_head as sign
from tools import nanojev_gamer_v1_sign_information_probe as oracle


def deck():
    return [c.public() for c in oracle.make_deck(24,12,12,include_ood=True)]


def synthetic(rows,*,width=16):
    labels=sign._labels(rows)
    x=np.zeros((len(rows),width),dtype=np.float32)
    x[:,0]=(labels-1)*15.
    x[:,1]=(labels==1)*15.
    x[:,2]=np.arange(len(rows))%2*.01
    return x


def test_zero_is_a_separate_class_and_perfect_sign_only_can_solve_all_triples():
    rows=deck();split=sign._partitions(rows)
    pred=sign._labels(rows)[split['test']]
    probabilities=np.eye(3)[pred]
    report=sign._metrics_by_family(rows,split['test'],pred,probabilities)
    assert report['complete_triples']==36
    assert report['triples']==36
    assert report['sign_reversal_both_correct']==36
    assert report['recall']=={'-1':1.,'0':1.,'1':1.}
    assert report['by_family']['hawk_indifference']['complete_triples']==12
    assert report['passes_sign_zero_and_triples_gate']
    assert report['confusion_rows_true_cols_pred_negative_zero_positive']==[[36,0,0],[0,36,0],[0,0,36]]


def test_dominant_sign_with_lost_zero_line_cannot_pass():
    rows=deck();split=sign._partitions(rows)
    actual=sign._labels(rows)[split['test']]
    pred=actual.copy()
    pred[pred==1]=2
    res=sign._metrics_by_family(rows,split['test'],pred,np.eye(3)[pred])
    assert res['recall']['-1']==1 and res['recall']['1']==1
    assert res['recall']['0']==0
    assert res['complete_triples']==0
    assert not res['passes_sign_zero_and_triples_gate']


def test_confusion_matrix_preserves_negative_zero_positive_class_order():
    rows=deck();split=sign._partitions(rows)
    pred=np.zeros(len(split['test']),dtype=int)
    r=sign._metrics(rows,split['test'],pred)
    assert r['recall']['-1']==1
    assert r['recall']['0']==0 and r['recall']['1']==0
    assert r['balanced_sign_accuracy']==pytest.approx(1/3)
    with pytest.raises(ValueError,match='INVALID_CLASS_OUTPUT'):
        sign._metrics(rows,split['test'],np.full(len(pred),4))


def test_partial_triple_is_not_accepted():
    rows=deck();split=sign._partitions(rows)
    indices=split['test'][:-1]
    with pytest.raises(ValueError,match='NONTRIPLE_EVALUATION_GROUP'):
        sign._metrics(rows,indices,sign._labels(rows)[indices])


def test_train_only_scaler_and_kernel_are_unchanged_when_holdout_mutates():
    rows=deck();split=sign._partitions(rows)
    x=synthetic(rows)
    maps,_=sign._train_only_maps(x,split)
    mutated=x.copy()
    mutated[split['test']]=np.random.default_rng(7).normal(0,1000,size=mutated[split['test']].shape)
    new_maps,_=sign._train_only_maps(mutated,split)
    for name in sign.KINDS:
        assert np.array_equal(maps[name][split['train']],new_maps[name][split['train']]),name
        assert np.array_equal(maps[name][split['dev']],new_maps[name][split['dev']]),name


def test_sign_head_is_train_only_and_generalizes_zero_and_negative():
    rows=deck(); x=synthetic(rows)
    fit=sign._select_head(rows,x,epochs=80,nulls=2)
    assert fit['test']['balanced_sign_accuracy']>.9
    assert fit['test']['min_class_recall']>.85
    assert fit['test']['complete_triples']>=24
    assert fit['test']['by_family']['hawk_indifference']['balanced_sign_accuracy']>.9
    assert fit['beats_all_shuffled_train_label_controls']
    assert fit['train_cases']==144 and fit['dev_cases']==72 and fit['test_cases']==108
    assert fit['model_selection']=='TRAIN_FIT_DEV_ONLY_TEST_UNTOUCHED'
    assert not fit['any_magnitude_fit'] and fit['production_head_unchanged']
    assert len(fit['shuffled_train_label_test_sign_accuracy_nulls'])==2


def test_fitted_sign_head_artifact_roundtrip_preserves_exact_zero_decisions(tmp_path):
    rows=deck();x=synthetic(rows)
    save=tmp_path/'standalone-sign-head.npz'
    fit=sign._select_head(rows,x,epochs=60,nulls=1,export_path=save)
    assert save.exists()
    assert fit['diagnostic_sign_head_artifact']['sha256']==sign._sha(save)
    assert fit['diagnostic_sign_head_artifact']['type']=='DIAGNOSTIC_SIGN_HEAD_ONLY_NO_MAGNITUDE'
    ix=sign._partitions(rows)['test']
    classes,probs=sign._replay_head(save,x[ix])
    exact=sign._metrics(rows,ix,classes,probabilities=probs)
    assert exact['balanced_sign_accuracy']==fit['test']['balanced_sign_accuracy']
    assert exact['complete_triples']==fit['test']['complete_triples']
    assert 1 in classes  # zero is a genuine third class, not a signed cutoff
    with pytest.raises(ValueError,match='INPUT_WIDTH_MISMATCH'):
        sign._replay_head(save,x[ix,:4])


def test_shuffled_labels_do_not_inherit_the_correct_training_targets():
    rows=deck(); x=synthetic(rows)
    fit=sign._select_head(rows,x,epochs=40,nulls=2)
    assert fit['test']['balanced_sign_accuracy']>.80
    assert max(fit['shuffled_train_label_test_sign_accuracy_nulls']) < fit['test']['balanced_sign_accuracy']


def test_sign_stage_with_degenerate_activation_cannot_fake_sufficiency():
    rows=deck()
    x=np.zeros((len(rows),32),dtype=np.float32)
    fit=sign._select_head(rows,x,epochs=20,nulls=1)
    assert fit['test']['balanced_sign_accuracy']==pytest.approx(1/3)
    assert fit['test']['complete_triples']==0
    assert fit['test']['recall']['0'] in (0.,1.)
    assert not fit['test']['passes_sign_zero_and_triples_gate']
    assert not fit['beats_all_shuffled_train_label_controls']


def test_experimental_training_changes_no_original_adapter_or_c0():
    # This is a readout contract assertion, not a write to either checkpoint.
    rows=deck();x=synthetic(rows)
    out=sign._select_head(rows,x,epochs=20,nulls=1)
    assert out['production_head_unchanged']
    assert not out['any_magnitude_fit']
    assert out['selected']['kind'] in sign.KINDS


def test_snapshot_trajectory_keeps_distinct_sign_stage_reports():
    perf={'test':{'balanced_sign_accuracy':0.5,'complete_triples':0,
                  'recall':{'-1':.2,'0':.4,'1':.9},
                  'by_family':{'hawk_indifference':{'balanced_sign_accuracy':1/3}},
                  'passes_sign_zero_and_triples_gate':False}}
    result={'steps':{'0':{'stages':dict.fromkeys(sign.STAGES,perf)},
                     '3000':{'stages':dict.fromkeys(sign.STAGES,perf)}}}
    out=sign._comparison(result)
    assert out['last_step']==3000
    assert out['stages']['mid_gelu']['last_negative_recall']==.2
    assert not out['stages']['enc_gelu_3']['last_passed_all_family_gates']


def test_output_directory_guards_long_paths_and_source_overlap(tmp_path):
    protected=tmp_path/'checkpoint';protected.mkdir()
    with pytest.raises(ValueError,match='OVERLAPS_SOURCE'):
        sign._output(protected/'nested',[protected])
    with pytest.raises(ValueError,match='OVERLAPS_SOURCE'):
        sign._output(tmp_path,[protected])
    with pytest.raises(FileExistsError):
        sign._output(protected,[])
    with pytest.raises(ValueError,match='PATH_TOO_LONG'):
        sign._output(tmp_path/('x'*111),[])
    assert sign._output(tmp_path/'sign-head-0001',[protected])==tmp_path/'sign-head-0001'


def test_invalid_causal_report_fails_before_loading_models(tmp_path):
    source=tmp_path/'causal';source.mkdir()
    (source/'final_report.json').write_text(json.dumps({'schema':'forged','status':'COMPLETE'}))
    with pytest.raises(ValueError,match='REQUIRES_COMPLETE_FROZEN_CAUSAL_AUDIT'):
        sign._source(source,'0,3000')


def test_cli_refuses_invalid_epochs_and_null_counts_without_cuda():
    with pytest.raises(ValueError,match='INVALID_DIAGNOSTIC_FLAGS'):
        sign.main(['--causal','causal','--output','out','--epochs','0'])
    with pytest.raises(ValueError,match='INVALID_DIAGNOSTIC_FLAGS'):
        sign.main(['--causal','causal','--output','out','--nulls','0'])
    with pytest.raises(ValueError,match='INVALID_DIAGNOSTIC_FLAGS'):
        sign.main(['--causal','causal','--output','out','--capture-batch','0'])


def test_mocked_two_snapshot_end_to_end_report_and_protected_hashes(tmp_path,monkeypatch):
    import torch
    from tools import nanojev_gamer_v1_c0_sign_causality as causal
    from tools import nanojev_gamer_v1_handoff as gamer
    rows=deck();data=synthetic(rows,width=16)
    causal_dir=tmp_path/'causal';causal_dir.mkdir()
    audit_dir=tmp_path/'audit';audit_dir.mkdir()
    training=tmp_path/'training';training.mkdir()
    preservation=tmp_path/'preservation';preservation.mkdir()
    capture=preservation/'capture';capture.mkdir()
    head=tmp_path/'frozen-head'/'frozen-c0.pt';head.parent.mkdir();head.write_bytes(b'FROZEN_C0')
    original=tmp_path/'frozen-adapter';original.mkdir();(original/'weights').write_bytes(b'FROZEN_LORA')
    for p in (causal_dir/'final_report.json',audit_dir/'final_report.json',preservation/'final_report.json',training/'experiment.json'):
        p.write_text('IMMUTABLE_'+p.name+str(p.parent))
    for step in (0,500):
        (training/'snapshots'/f'step-{step:07d}').mkdir(parents=True)
    pred=data[:,0]*0+0.5
    def production(_rows,values):
        y=np.asarray(values)
        cls=np.where(abs(y)<=.05,0,np.sign(y)).astype(int)+1
        idx=sign._partitions(_rows)['test']
        # The actual numeric head wrapper is only used to validate replay.
        return {'test':{'balanced_sign_accuracy':sign._metrics(_rows,idx,cls[idx])['balanced_sign_accuracy'],
                        'triple_all_correct':0,'signed_mae':float(np.mean(abs(y[idx])))}}
    step_results={str(k):{'metrics':{'production_c0':{'test':production(rows,pred)['test']}}} for k in (0,500)}
    source={'causal_path':causal_dir/'final_report.json','audit_path':audit_dir/'final_report.json',
            'training':training,'source':preservation,'ckpt':head,'adapter':original,
            'capture':capture,'snapshots':{k:(training/'snapshots'/f'step-{k:07d}','SHA'+str(k)) for k in (0,500)},
            'archive':{'lora__final_flat':data,'production_head_predictions':pred},
            'meta':{'dataset':rows,'dataset_sha256':'ORACLE',
                    'lineage':{'model_id':'FAKE','checkpoint_sha256':'FIXED',
                               'adapter_sha256':'FIXED',
                               'configuration':{'max_length':256,'curvature':1.0}}},
            'rows':rows,'deck':object(),
            'audit':{'steps':step_results}}
    monkeypatch.setattr(sign,'_source',lambda *a:source)
    monkeypatch.setattr(sign,'_protected_hashes',lambda *a:{'production_c0':'FIXED','original_lora':'FIXED'})
    fake_c0=types.ModuleType('tools.nanojev_gamer_v1_c0_boundary')
    fake_c0._load_head=lambda *a:types.SimpleNamespace(eval=lambda :None)
    fake_c0._verify_baseline=lambda x,y: {'max_abs_delta':0.} if np.array_equal(x,y) else (_ for _ in ()).throw(ValueError('NOT_PARITY'))
    monkeypatch.setitem(sys.modules,'tools.nanojev_gamer_v1_c0_boundary',fake_c0)
    fake_stress=types.ModuleType('tools.nanojev_gamer_v1_lora_sign_stress')
    fake_stress._verify_snapshot=lambda path,step:'SHA'+str(step)
    fake_stress._head_metrics=production
    monkeypatch.setitem(sys.modules,'tools.nanojev_gamer_v1_lora_sign_stress',fake_stress)
    monkeypatch.setattr(gamer,'model_components',lambda *a,**kw:(torch,None,{'step':int(str(kw['adapter_dir']).split('-')[-1])}))
    monkeypatch.setattr(oracle,'_capture_profiles',lambda t,model,tok,deck,**kw:({
        'final_flat':data+(0 if model['step']==0 else .01),
        'final_mean100':data+(0 if model['step']==0 else .01)},{}))
    monkeypatch.setattr(causal,'_read_stages',lambda net,x,**kw:({'enc_gelu_3':x[:,:12], 'mid_gelu':x[:,:8]},
         np.zeros(len(x)),np.zeros(len(x)),pred.copy()))
    monkeypatch.setattr(gamer,'atomic_json',lambda path,value:Path(path).write_text(json.dumps(value)))
    # Avoid a slow model search here: fully exercise the report composition
    # with the real sign-only training in independent unit tests above.
    result_report={'selected':{'kind':'linear'},'test':{'balanced_sign_accuracy':.4,'recall':{'-1':.2,'0':.5,'1':.5},
        'complete_triples':0,'triples':36,'by_family':{'hawk_indifference':{'balanced_sign_accuracy':1/3}},
        'passes_sign_zero_and_triples_gate':False}}
    monkeypatch.setattr(sign,'_select_head',lambda *a,**kw:result_report)
    protected=[causal_dir/'final_report.json',audit_dir/'final_report.json',preservation/'final_report.json',
               training/'experiment.json',head,original/'weights']
    before={str(p):p.read_bytes() for p in protected}
    args=types.SimpleNamespace(causal=causal_dir,output=tmp_path/'sign-head-out',steps='0,500',device='cpu',
                               head_batch=16,capture_batch=2,epochs=20,nulls=1,seed=47)
    report=sign.run(args)
    assert report['status']=='COMPLETE_SIGN_ONLY_DIAGNOSTIC_NO_PRODUCTION_UPDATES'
    assert set(report['steps'])=={'0','500'}
    assert report['no_lora_or_c0_training'] and report['magnitude_regression_not_tested']
    assert (tmp_path/'sign-head-out'/'final_report.json').exists()
    assert {str(p):p.read_bytes() for p in protected}==before
