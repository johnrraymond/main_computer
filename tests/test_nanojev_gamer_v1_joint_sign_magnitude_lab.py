"""Joint sign transfer and magnitude separation: heldout-profile and no-mutation gates."""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
import types
import sys
import numpy as np
import pytest

from tools import nanojev_gamer_v1_joint_sign_magnitude_lab as joint
from tools import nanojev_gamer_v1_dual_sign_head as dual
from tools import nanojev_gamer_v1_c0_sign_head as three
from tools import nanojev_gamer_v1_sign_information_probe as oracle


def old_deck():return oracle.make_deck(96,12,12,True)


def inputs():
    deck,aux=joint._auxiliary_deck(old_deck())
    return deck,[x.public() for x in deck],aux


def test_auxiliary_cross_game_has_certified_zero_and_no_hawk_exposure():
    original=old_deck();deck,aux=joint._auxiliary_deck(original)
    assert len(deck)==900 and len(aux)==144
    assert [r.public() for r in deck[:756]]==[r.public() for r in original]
    oldtest={x.id:x.public() for x in original if x.split=='test'}
    newtest={x.id:x.public() for x in deck if x.split=='test'}
    assert oldtest==newtest
    assert {x.family for x in aux}=={f'aux_{name}' for name in joint.AUX_GAMES}
    assert all(x.split=='train' for x in aux)
    assert len({x.prompt for x in aux})==144
    for r in aux:
        assert r.target==joint.Fraction(r.target)
    split=three._partitions([x.public() for x in deck])
    assert tuple(len(split[x]) for x in ('train','dev','test'))==(720,72,108)
    triples=dual._triples([x.public() for x in deck],split['train'])
    assert triples.shape==(240,3)
    assert sum(x.family=='hawk_indifference' for x in aux)==0


def test_cross_game_belief_prompts_are_not_original_hawk_or_test_prompts():
    deck,aux=joint._auxiliary_deck(old_deck())
    test_prompts={x.prompt for x in deck if x.split=='test'}
    assert not any(x.prompt in test_prompts for x in aux)
    assert any('spine_belief'==x.surface for x in aux)
    assert any('game_prose_belief'==x.surface for x in aux)
    assert oracle.deck_hash(aux)==oracle.deck_hash(joint._auxiliary_deck(old_deck())[1])


def test_signed_reconstruction_is_zero_authoritative_and_separates_ceiling():
    r=[x.public() for x in oracle.make_deck(24,12,12,True)]
    ix=three._partitions(r)['test']
    signed=dual._labels(r)
    classes=signed+1
    mag=np.ones(len(r),dtype=np.float64)*3
    c0=np.ones(len(r),dtype=np.float64)*3
    scores=joint._reconstruction(r,ix,classes,mag,c0)
    assert scores['all']['exact_zero_output_count']==36
    assert scores['all']['true_zero_cases']==36
    assert scores['all']['false_exact_zero_on_nonzero_count']==0
    assert scores['all']['reconstructed_sign_correct_count']==108
    # An incorrect zero detector forces exact zero but can give false zeros.
    broken=classes.copy();nonzero=np.flatnonzero(signed!=0)
    broken[nonzero]=1
    wrong=joint._reconstruction(r,ix,broken,mag,c0)
    assert wrong['all']['false_exact_zero_on_nonzero_count']==72
    assert wrong['all']['exact_zero_output_count']==36
    with pytest.raises(ValueError,match='BAD_SIGN_OR_MAGNITUDE_ARRAY'):
        joint._reconstruction(r,ix,classes,-mag,c0)


def test_frozen_c0_magnitude_is_scored_independently_of_polarity():
    deck,rows,_=inputs();split=three._partitions(rows)
    truth=np.asarray([abs(float(Fraction(r['target']))) for r in rows],dtype=float)
    metric=joint._magnitude_errors(rows,split['test'],truth)
    assert metric['all']['magnitude_mae']==0
    assert metric['by_true_sign']['0']['magnitude_mae']==0
    assert all(v['magnitude_mae']==0 for v in metric['by_family'].values())
    # Arbitrarily flip the output signs: magnitude evaluation is invariant.
    fit1=joint._magnitude_fit(rows,split['train'],truth)
    fit2=joint._magnitude_fit(rows,split['train'],np.abs(-truth))
    assert fit1==fit2


def test_magnitude_calibrator_never_uses_test_for_fitting_or_selection():
    _,rows,_=inputs();split=three._partitions(rows)
    y=np.asarray([abs(float(Fraction(r['target']))) for r in rows],dtype=float)
    x=y*.4+.1
    base=joint._select_magnitude(rows,split,x,split['train'])
    altered=x.copy();altered[split['test']]=1e6
    assert base==joint._select_magnitude(rows,split,altered,split['train'])
    # Changed dev data MAY change the selected model, but never fitted coefficients.
    altered_dev=x.copy();altered_dev[split['dev']]=0.
    assert joint._magnitude_fit(rows,split['train'],altered_dev)==joint._magnitude_fit(rows,split['train'],x)
    assert base['selection']=='DEV_ONLY_NO_TEST_SELECTION'


def test_source_refuses_noncompleted_or_modified_dual_audit(tmp_path):
    path=tmp_path/'dual';path.mkdir()
    (path/'final_report.json').write_text(json.dumps({'status':'INCOMPLETE'}))
    with pytest.raises(ValueError,match='REQUIRES_COMPLETED_DUAL_AUDIT'):
        joint._source(path,[3000])


def test_source_verifies_strict_sign_artifact_fingerprints(monkeypatch,tmp_path):
    _,rows,aux=inputs()
    report=tmp_path/'dual';report.mkdir()
    src3=tmp_path/'three';src3.mkdir()
    strict=tmp_path/'frozen.npz';strict.write_bytes(b'IMMUTABLE_BINARY_SIGN')
    prior={'schema':dual.SCHEMA,'status':'COMPLETE_TWO_BINARY_HEADS_NO_PRODUCTION_UPDATES',
           'promotion':'BLOCKED_DIAGNOSTIC_ONLY','no_original_lora_or_production_c0_training':True,
           'dev_and_test_unchanged':True,'source_three_class_report':str(src3/'final_report.json'),
           'fingerprints':{'all_protected':'FROZEN'},
           'original_oracle_dataset_sha256':'HASH','expanded_training_deck_sha256':'TRAIN_HASH',
           'snapshots':{'0':'BASE_SHA','3000':'SHA'},
           'steps':{'3000':{'stages':{'lora_final_mean100':{
               'diagnostic_binary_head_artifact':{'type':'DIAGNOSTIC_TWO_BINARY_SIGN_HEADS_NO_MAGNITUDE',
                                                   'path':str(strict),'sha256':three._sha(strict)}}}}}}
    (report/'final_report.json').write_text(json.dumps(prior))
    origin={'source':{'meta':{'dataset_sha256':'HASH'},'snapshots':{
                0:(tmp_path/'baseline','BASE_SHA'),3000:(tmp_path/'snap','SHA')}},
            'deck':old_deck(),'rows':[x.public() for x in old_deck()],
            'expanded_deck_sha256':'TRAIN_HASH'}
    def checked_legacy_source(path, steps):
        from tools import nanojev_gamer_v1_c0_sign_causality as causal
        # Use the REAL upstream selector: it rejects the prior buggy '3000'
        # (or a Python list) because baseline checkpoint 0 must be included.
        assert causal._select_steps({'steps':{'0':{},'3000':{}}},steps)==[0,3000]
        return origin
    monkeypatch.setattr(dual,'_source',checked_legacy_source)
    monkeypatch.setattr(dual,'_fingerprints',lambda *a:{'all_protected':'FROZEN'})
    check=joint._source(report,[3000])
    assert len(check['rows'])==900
    assert len(check['split']['train'])==720
    assert check['requested']==[3000]  # baseline verified, not re-evaluated
    strict.write_bytes(b'TAMPER')
    with pytest.raises(ValueError,match='STRICT_SIGN_ARTIFACT_CHANGED'):
        joint._source(report,[3000])


def test_end_to_end_mocked_joint_run_is_read_only(monkeypatch,tmp_path):
    import torch
    from tools import nanojev_gamer_v1_c0_sign_causality as causal
    from tools import nanojev_gamer_v1_c0_boundary as c0
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_sign_information_probe as oracle_module

    deck,rows,_=inputs()
    old=[x.public() for x in oracle.make_deck(24,12,12,True)]
    oldmap={r['id']:i for i,r in enumerate(rows)}
    oldix=np.asarray([oldmap[r['id']] for r in old],dtype=int)
    training=tmp_path/'training';training.mkdir()
    source=tmp_path/'source';source.mkdir()
    capture=tmp_path/'capture';capture.mkdir()
    weight=tmp_path/'weights';weight.mkdir()
    adapter=tmp_path/'adapter';adapter.mkdir()
    snap=tmp_path/'snap';snap.mkdir()
    c0path=weight/'original.pt';c0path.write_text('ORIGINAL')
    audit=tmp_path/'audit';audit.mkdir()
    causal_dir=tmp_path/'causal';causal_dir.mkdir()
    three_root=tmp_path/'three';three_root.mkdir()
    dual_root=tmp_path/'dual';dual_root.mkdir()
    (dual_root/'final_report.json').write_text('UNMODIFIED')
    meta={'dataset_sha256':'ORIGINAL','lineage':{'model_id':'FAKE','configuration':{'max_length':128,'curvature':0}}}
    base={'meta':meta,'rows':old,
          'archive':{'lora__final_flat':np.zeros((len(old),8),dtype=np.float32),
                     'production_head_predictions':np.zeros(len(old))},
          'audit':{'steps':{'3000':{'metrics':{'production_c0':{'test':{
              'balanced_sign_accuracy':1/3,'triple_all_correct':0,'signed_mae':.1}}}}}},
          'snapshots':{3000:(snap,'SHA')},'source':source,'training':training,
          'capture':capture,'ckpt':c0path,'adapter':adapter,
          'audit_path':audit/'final_report.json','causal_path':causal_dir/'final_report.json'}
    original={'source':base,'root':three_root,'old_indices':oldix,
              'expanded_deck_sha256':'EXPAND_HASH'}
    strict_path=tmp_path/'strict.npz';strict_path.write_text('STRICT')
    strict={'diagnostic_binary_head_artifact':{'path':str(strict_path),'sha256':three._sha(strict_path)},
            'test':{'complete_triples':0,'balanced_sign_accuracy':1/3}}
    prior={'steps':{'3000':{'stages':{joint.STAGE:strict}}}}
    source_object={'origin':original,'prior':prior,'report_path':dual_root/'final_report.json',
                   'deck':deck,'rows':rows,'split':three._partitions(rows),
                   'additional_deck_sha256':'ADDED','combined_deck_sha256':'ALL',
                   'requested':[3000]}
    monkeypatch.setattr(joint,'_source',lambda *a:source_object)
    monkeypatch.setattr(dual,'_fingerprints',lambda *a:{'protected':'UNCHANGED'})
    monkeypatch.setattr(c0,'_load_head',lambda *a:object())
    monkeypatch.setattr(c0,'_verify_baseline',lambda *a:{'status':'PASS'})
    monkeypatch.setattr(stress,'_verify_snapshot',lambda *a:'SHA')
    monkeypatch.setattr(stress,'_head_metrics',lambda *a:{'test':{'balanced_sign_accuracy':1/3,
                                                           'triple_all_correct':0,'signed_mae':.1}})
    y=dual._labels(rows)
    x=np.zeros((len(rows),8),dtype=np.float32)
    x[:,0]=(y==0)*10;x[:,1]=y*10;x[:,2]=(y==-1)*10
    monkeypatch.setattr(causal,'_read_stages',lambda _,features,**kwargs: (
        {},None,None,np.zeros(len(features),dtype=float)))
    monkeypatch.setattr(gamer,'model_components',lambda *a,**k:(torch,object(),object()))
    monkeypatch.setattr(gamer,'atomic_json',lambda path,val:Path(path).write_text(json.dumps(val)))
    monkeypatch.setattr(oracle_module,'_capture_profiles',lambda *a,**k: (
        {'final_mean100':x.copy(),'final_flat':x.copy()},None))
    saved_reload=dual._reload
    def reload_maybe_strict(path, values):
        if str(path)==str(strict_path):
            target=dual._labels(rows)[three._partitions(rows)['test']]+1
            return target,np.eye(3)[target]
        return saved_reload(path,values)
    monkeypatch.setattr(dual,'_reload',reload_maybe_strict)
    monkeypatch.setattr(dual,'KINDS',('linear',))
    monkeypatch.setattr(dual,'REGS',(.001,))
    # An oracle-perfect frozen classifier is the strict reference for the mock;
    # it is never refitted by this joint lab.
    prior['steps']['3000']['stages'][joint.STAGE]['test']={'complete_triples':36,'balanced_sign_accuracy':1.}
    args=types.SimpleNamespace(dual=dual_root,output=tmp_path/'output',steps=[3000],
                               device='cpu',capture_batch=2,head_batch=8,epochs=20,
                               nulls=1,seed=47,triplet_weight=.25)
    result=joint.run(args)
    assert result['status']=='COMPLETE_FROZEN_DUAL_PATH_AND_UNSEEN_PROFILE_TRANSFER'
    assert result['train_cases']==720
    assert result['hawk_test_profile_trained'] is False
    assert result['steps']['3000']['magnitude']['uncalibrated']['all']['cases']==108
    assert result['steps']['3000']['reconstructions']['strict_uncalibrated']['all']['true_zero_cases']==36
    assert c0path.read_text()=='ORIGINAL'
    assert (dual_root/'final_report.json').read_text()=='UNMODIFIED'
    assert (args.output/'final_report.json').exists()


def test_cli_rejects_invalid_flags_before_touching_source():
    with pytest.raises(ValueError,match='GAMER_JOINT_BAD_FLAGS'):
        joint.main(['--dual','missing','--output','out','--epochs','1'])
    with pytest.raises(ValueError,match='GAMER_JOINT_BAD_STEPS'):
        joint.main(['--dual','missing','--output','out','--steps','uh-oh'])
