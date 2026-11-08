"""V23 must remain read-only, surface-matched, and TRAIN-only for fitted readouts."""
import argparse
from fractions import Fraction
import json
from pathlib import Path

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_operand_causality_v23 as v23
from tools import nanojev_gamer_v1_sign_information_probe as probe


def _deck():
    deck=probe.make_deck(4,2,2,False)
    return {'dataset':[x.public() for x in deck],
            'dataset_sha256':probe.deck_hash(deck),
            'deck':probe.validate_deck(deck)}


def test_exact_operand_parsing_both_surfaces_and_paired_oracle():
    info=_deck()
    train=v23._operand_rows(info,'train')
    dev=v23._operand_rows(info,'dev')
    assert len(train)==24 and len(dev)==12
    for row in train+dev:
        assert (Fraction(row['first_operand'])-Fraction(row['second_operand'])==
                Fraction(row['target_fraction']))
    assert [r['target_sign'] for r in dev]==[-1,-1,0,0,1,1]*2
    assert all(r['control_operands'] for r in dev if r['surface']=='spine_difference')
    assert all(not r['control_operands'] for r in dev if r['surface']=='source_native')


def test_tampered_prompt_fingerprint_or_label_cannot_enter_operand_audit():
    info=_deck()
    info['dataset'][0]=dict(info['dataset'][0],prompt_sha256='f'*64)
    with pytest.raises(ValueError,match='CAPTURE|ALIGN|ORACLE|HASH'):
        v23._operand_rows(info,'train')
    info=_deck()
    info['dataset'][1]=dict(info['dataset'][1],target='8')
    with pytest.raises(ValueError,match='ALIGN|ORACLE|HASH'):
        v23._operand_rows(info,'train')


def test_readout_uses_train_only_and_has_random_label_null():
    info=_deck()
    train=v23._operand_rows(info,'train')
    dev=v23._operand_rows(info,'dev')
    def vector(rows):
        return np.asarray([[float(r['target_sign']),
           float(r['target_sign']**2),
           int(r['surface']=='spine_difference'),
           float(Fraction(r['first_operand'])/100.)] for r in rows],dtype=np.float32)
    train_x=vector(train);dev_x=vector(dev)
    metrics,by,pred=v23._readout_fit(train_x,dev_x,train,dev,
                                    alpha=.01,surface='both')
    assert metrics['correct']>=8
    assert metrics['groups']==2
    assert len(metrics['randomized_label_null_balanced_sign_accuracy'])==3
    assert len(by)==2 and len(pred)==12
    assert all(x['eval_surface'] in ('source_native','spine_difference') for x in by)
    # A readout trained on only one surface cannot silently use the other.
    native,_,_=v23._readout_fit(train_x,dev_x,train,dev,surface='source_native')
    assert native['train_cases']==len(train)//2
    # TEST is not an argument and is not used.


def test_matched_group_geometry_and_contrast_sign():
    info=_deck()
    train=v23._operand_rows(info,'train');dev=v23._operand_rows(info,'dev')
    def vector(rows):
        return np.asarray([[r['target_sign']*5.,
          r['target_sign']*3.,float(int(r['surface']=='spine_difference'))]
          for r in rows],dtype=np.float32)
    g=v23._contrast_geometry(vector(train),vector(dev),train,dev)
    assert g['group_anchored_contrast_cases']==4
    assert g['group_anchored_sign_contrast_positive_fraction']==1.
    assert g['cross_surface_groups']==2
    assert g['same_answer_cross_surface_nearest_neighbor_fraction']==1.
    assert g['cross_surface_matched_distance_advantage']>0


def test_swaps_keep_oracle_out_of_forward_and_cover_controls(monkeypatch):
    info=_deck();rows=v23._operand_rows(info,'dev')
    x=np.asarray([[float(r['target_sign']),0.] for r in rows],dtype=np.float32)
    old=np.asarray([0. for _ in rows],dtype=np.float64)
    routes=np.asarray([0 if r['surface']=='source_native' else 1 for r in rows],dtype=np.float32)
    spec={'dev_synthetic':(None,None,None,None,None,None)}
    monkeypatch.setattr(v23.v7,'_split_arrays',lambda *args:
                        (rows,x,old,None,routes))
    def dummy(_torch,_model,feature,base,route,mu,sd,device):
        return np.sign(feature[:,0]).astype(np.int8),np.asarray(feature[:,0])
    monkeypatch.setattr(v23.v6,'_predict',dummy)
    trials,summary=v23._interventions(torch,None,spec,None,None,'cpu',rows)
    kinds={x['kind'] for x in summary}
    assert len(kinds)==4
    total={x['kind']:x['sign_changes'] for x in summary}
    assert total['same_answer_cross_surface_feature_only']==0
    assert total['opposite_sign_same_surface_feature_only']==8
    assert total['opposite_sign_same_surface_baseline_only']==0
    assert total['opposite_sign_same_surface_full_input_control']==8
    assert len(trials)==len(rows)+3*(len(rows)//3*2)


def test_output_overlap_and_no_inplace_reuse(tmp_path):
    source=tmp_path/'history';source.mkdir()
    with pytest.raises(ValueError,match='OVERLAPS_SOURCE'):
        v23.lab._output(source/'out',[source])
    out=tmp_path/'candidate';out.mkdir()
    with pytest.raises(FileExistsError):v23.lab._output(out,[source])


def test_parser_requires_source_verification_and_never_has_train_mode():
    p=v23.parser()
    with pytest.raises(SystemExit):p.parse_args(['--mode','train'])
    for name in ('--v22-run','--source-features','--v19-run'):
        assert any(name in a.option_strings for a in p._actions)


def test_metric_bounded_and_complete_six():
    rows=v23._operand_rows(_deck(),'dev')
    pred=np.asarray([r['target_sign'] for r in rows],dtype=np.int8)
    correct=v23._scores(rows,pred)
    assert correct['correct']==12 and correct['complete_six']==2
    pred[0]=0
    bad=v23._scores(rows,pred)
    assert bad['correct']==11 and bad['complete_six']==1
    with pytest.raises(ValueError,match='BAD_PREDICTION'):
        v23._scores(rows,pred[:3])


def test_v23_read_only_full_chain_smoke_with_actual_terminal_checkpoints(tmp_path,monkeypatch):
    """Build authentic small V9–V22 evidence; audit without creating weights."""
    from types import SimpleNamespace
    from tests.test_nanojev_gamer_v1_conflict_projection_v10 import _setup
    from tests.test_nanojev_gamer_v1_protected_math_repair_v7 import _checkpoint
    from tests.test_nanojev_gamer_v1_mathematical_acquisition_v22 import _make_history
    from tools import nanojev_gamer_v1_margin_consolidation_v13 as v13
    from tools import nanojev_gamer_v1_corrected_margin_v14 as v14
    from tools import nanojev_gamer_v1_capacity_retention_v11 as v11
    from tools import nanojev_gamer_v1_conflict_projection_v10 as v10
    from tools import nanojev_gamer_v1_math_retention_v9 as v9
    from tools import nanojev_gamer_v1_boundary_isolation_v12 as v12
    from tools import nanojev_gamer_v1_joint_head_repair as joint
    from tools import nanojev_gamer_v1_mathematical_acquisition_v22 as v22
    from tools import nanojev_gamer_v1_precision_continuation_v19 as v19

    before=torch.get_num_threads();torch.set_num_threads(2)
    try:
        data,prepared,spec,mu,sd,_=_setup()
        # Supplement mock capture with realistic keys. The actual source archive
        # is verified separately against its real SHA in production.
        capture=data['probe_arrays'];last=capture['lora__final_last']
        mean=capture['lora__final_mean100']
        for variant in ('lora','base'):
            for key in v23.DEFAULT_FEATURES:
                capture[f'{variant}__{key}']=(mean if key.endswith('mean100') else last).copy()
        prior=tmp_path/'v6'/'disposable_candidate.pt';prior.parent.mkdir()
        _checkpoint(prior,data,mu,sd)
        monkeypatch.setattr(joint,'_prepare',lambda _:(data,prepared))
        monkeypatch.setattr(joint,'_verify_after',lambda _:None)
        shared=dict(device='cpu',run=tmp_path/'r',expanded_run=tmp_path/'e',
          input=tmp_path/'i',source_features=tmp_path/'s',prior=prior,
          steps=1,eval_every=1,lr=.001,seed=49,replay_weight=2.,
          max_halvings=1,projection_passes=1,max_constraints=4)
        v9.run(SimpleNamespace(**{k:v for k,v in shared.items() if k not in
                   ('projection_passes','max_constraints')},output=tmp_path/'v9'))
        v10.run(SimpleNamespace(**shared,v9_run=tmp_path/'v9',output=tmp_path/'v10'))
        v11.run(SimpleNamespace(**shared,v10_run=tmp_path/'v10',
                                 output=tmp_path/'v11',capacity_width=16))
        groups,capture_sha=v9._groups_and_fingerprints(spec)
        proof=v12._verify_v11(torch,tmp_path/'v11',data,capture_sha,
                               v19._sha(prior),spec,'cpu')[3]
        focus=spec['train_synthetic'][3][0]['group']
        v12root=tmp_path/'v12';v12root.mkdir()
        (v12root/'report.json').write_text(json.dumps(dict(
           schema=v12.SCHEMA,status='EXPERIMENT_COMPLETE_NO_PROMOTION',mode='repair',
           promotion_authorized=False,source_dev_used_for_fitting=False,
           synthetic_dev_used_for_fitting=False,
           previously_seen_test_used_for_fitting_selection_or_metric=False,
           training_capture_sha256=capture_sha,v6_sha256=v19._sha(prior),
           v11_verified_checkpoints=proof,focus_group=focus,focus_member_count=6,
           v11_historical_conflict_events=1,
           v11_report_sha256=v19._sha(tmp_path/'v11'/'report.json'))))
        verified12=v13._check_v12(v12root,proof,capture_sha,v19._sha(prior),focus,
                    v19._sha(tmp_path/'v11'/'report.json'))
        v13root=tmp_path/'v13';v13root.mkdir()
        (v13root/'report.json').write_text(json.dumps(dict(
          schema=v13.SCHEMA,status='EXPERIMENT_COMPLETE_NO_PROMOTION',
          training_capture_sha256=capture_sha,v6_prior_sha256=v19._sha(prior),
          v11_verified=proof,v12_verified=verified12,focus_group=focus,
          source_dev_used_for_training=False,synthetic_dev_used_for_training=False,
          previously_seen_test_used_for_training_or_selection=False,
          production_weights_modified=False,promotion_authorized=False,
          comparison=[{}, {}, {}, {}],consolidation={'accepted':0},
          selected='no_consolidation_control')))
        v14.run(SimpleNamespace(**shared,v11_run=tmp_path/'v11',
           v12_run=v12root,v13_run=v13root,output=tmp_path/'v14',
           focus_group=focus,consolidation_steps=1,consolidation_lr=.001,
           reserve=.08,margin_width=16))
        p19,p20,p21=_make_history(tmp_path,prior,mu,sd,data,spec,capture_sha,
                                   proof,tmp_path/'v14')
        args=SimpleNamespace(**{k:v for k,v in shared.items() if k!='seed'},
           seeds=[49],v11_run=tmp_path/'v11',v14_run=tmp_path/'v14',
           v19_run=p19,v20_run=p20,v21_run=p21,output=tmp_path/'v22',
           focus_group=focus)
        v22.run(args)
        v23args=SimpleNamespace(**{k:v for k,v in vars(args).items() if k!='output'},
            v22_run=tmp_path/'v22',output=tmp_path/'v23',ridge_alpha=4.)
        report=v23._run(v23args)
        assert report['status']=='READ_ONLY_VERIFIED_NO_PROMOTION'
        assert report['checkpoint_saved'] is False
        assert report['production_weights_modified'] is False
        assert len(report['decoder_scores'])==6  # three starts + three terminals
        assert len(report['intervention_summary'])==24
        assert (tmp_path/'v23'/'readout_case_predictions.csv').is_file()
        assert (tmp_path/'v23'/'case_localization.csv').is_file()
        assert not list((tmp_path/'v23').glob('*.pt'))
        with pytest.raises(FileExistsError):v23._run(v23args)
        # History mismatch must fail before any new diagnostic can run.
        historical=json.loads((tmp_path/'v22'/'report.json').read_text())
        historical['v21_checkpoint_sha256']='forged'
        (tmp_path/'v22'/'report.json').write_text(json.dumps(historical))
        v23args.output=tmp_path/'v23-invalid'
        with pytest.raises(ValueError,match='V22_REPORT_PROVENANCE_MISMATCH'):
            v23._run(v23args)
    finally:
        torch.set_num_threads(before)
