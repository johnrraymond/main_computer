"""Regression tests for offline, read-only two-way frozen-feature transfer."""
from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_frozen_transfer_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as base


def _rows(ntrain=8,ndev=4,ntest=4,include_ood=False):
    return [x.public() for x in base.make_deck(ntrain,ndev,ntest,include_ood)]


def _truth_features(rows,anti_align=False):
    values=[]
    for r in rows:
        y=Fraction(r['target']);sign=-1 if y<0 else 1 if y>0 else 0
        if anti_align and r['surface']=='spine_difference':sign=-sign
        values.append([sign, sign==0, sign==-1, sign==1,1.])
    return np.asarray(values,dtype=np.float32)


def test_oracle_612_cases_and_independent_paritions():
    rows=_rows(48,24,24,True)
    a=lab.audit(rows)
    assert a['group_count']==102
    assert a['signed_offset_groups']==96
    assert a['capture_coverage']['hawk_indifference_within_family']=='UNAVAILABLE_TEST_ONLY'
    assert a['paired_triples']=='PASS'
    assert len(lab.partition(rows,'train',surface='source_native'))==144
    assert len(lab.partition(rows,'train',surface='spine_difference'))==144
    assert len(lab.partition(rows,'dev',surface='source_native'))==72
    assert len(lab.partition(rows,'test',surface='source_native'))==72
    assert len(lab.partition(rows,'test',family='hawk_indifference'))==36
    assert set(rows[i]['group'] for i in lab.partition(rows,'train')).isdisjoint(
        set(rows[i]['group'] for i in lab.partition(rows,'test')))


def test_oracle_rejects_corrupt_math_labels_duplicate_group_and_cross_split():
    rows=_rows()
    wrong=[dict(x) for x in rows]
    victim=next(i for i,r in enumerate(rows) if r['surface']=='spine_difference' and 'positive' in r['id'])
    wrong[victim]['target']='99'
    with pytest.raises(ValueError,match='MATHEMATICALLY_UNEQUAL'):
        lab.audit(wrong)
    with pytest.raises(ValueError,match='LEAKED_OR_INCOMPLETE_GROUP'):
        lab.audit(rows[:-1])
    wrong=[dict(x) for x in rows];wrong[0]['split']='test'
    with pytest.raises(ValueError,match='LEAKED_OR_INCOMPLETE_GROUP'):
        lab.audit(wrong)
    wrong=[dict(x) for x in rows];wrong[0]['id']=wrong[1]['id']
    with pytest.raises(ValueError,match='DUPLICATE_IDS'):
        lab.audit(wrong)


def test_true_information_decodes_within_and_transfers_both_directions():
    rows=_rows()
    y=np.asarray([float(Fraction(r['target'])) for r in rows])
    x=_truth_features(rows)
    for origin in (*lab.SURFACES,'both'):
        out=lab.fit_direction(rows,x,y,origin,null_shuffles=2)
        for surface in lab.SURFACES:
            assert out['test_by_surface'][surface]['balanced_sign_accuracy']==1
            assert out['test_by_surface'][surface]['triple_accuracy']==1
        assert out['test_matched_pairs']['class_agreement']==1
        assert out['test_matched_pairs']['both_correct_fraction']==1
        assert out['test_matched_pairs']['complete_six_case_fraction']==1
        assert max(out['shuffled_label_null_test_balanced_accuracy'])<1
    assert lab.audit(rows)['group_count']==16


def test_misaligned_surfaces_individually_decode_but_cross_transfer_fails():
    rows=_rows()
    y=np.asarray([float(Fraction(r['target'])) for r in rows])
    x=_truth_features(rows,anti_align=True)
    source=lab.fit_direction(rows,x,y,'source_native')
    game=lab.fit_direction(rows,x,y,'spine_difference')
    assert source['test_by_surface']['source_native']['balanced_sign_accuracy']==1
    assert game['test_by_surface']['spine_difference']['balanced_sign_accuracy']==1
    assert source['test_by_surface']['spine_difference']['balanced_sign_accuracy']<=1/3+1e-9
    assert game['test_by_surface']['source_native']['balanced_sign_accuracy']<=1/3+1e-9
    selected={'lora':{'source_native':{'feature':'mock','result':source},
                      'spine_difference':{'feature':'mock','result':game}}}
    summary=lab._summary_for(selected)['lora']
    assert summary['evidence']=='BOTH_FORMATS_ACCESSIBLE_TRANSFER_DEFICIT'
    assert summary['native_to_spine_gap']>=2/3-1e-9


def test_constant_features_cannot_prove_transfer_or_independent_accessibility():
    rows=_rows();y=np.asarray([float(Fraction(r['target'])) for r in rows])
    X=np.ones((len(rows),4),dtype=np.float32)
    for origin in lab.SURFACES:
        result=lab.fit_direction(rows,X,y,origin)
        assert result['test_by_surface'][origin]['balanced_sign_accuracy']<=1/3+1e-10
        assert result['test_matched_pairs']['both_correct_fraction']<=1/3+1e-10
        assert result['test_matched_pairs']['complete_six_case_fraction']==0


def test_paired_accuracy_distinguishes_wrong_agreement():
    rows=_rows();test=lab.partition(rows,'test')
    y=np.asarray([float(Fraction(rows[i]['target'])) for i in test])
    cls=np.ones(len(test),dtype=np.int8)
    score=lab.paired_stats(rows,test,cls,np.ones(len(test)))
    assert score['class_agreement']==1
    assert score['both_correct_fraction']==1/3
    assert score['both_wrong_same_fraction']==2/3
    assert score['complete_six_case_fraction']==0
    assert score['pairs']==12 and score['groups']==4


def test_origin_development_alphas_do_not_look_at_target_labels(monkeypatch):
    rows=_rows();y=np.asarray([float(Fraction(r['target'])) for r in rows]);X=_truth_features(rows)
    observed=[]
    original=base._solve_probe
    def checked(K,Kdev,Ktest,ytrain,ydev,**kwargs):
        observed.append((len(ytrain),len(ydev)))
        return original(K,Kdev,Ktest,ytrain,ydev,**kwargs)
    monkeypatch.setattr(base,'_solve_probe',checked)
    lab.fit_direction(rows,X,y,'source_native')
    lab.fit_direction(rows,X,y,'spine_difference')
    lab.fit_direction(rows,X,y,'both')
    assert observed==[(24,12),(24,12),(48,24)]


def test_output_refuses_nested_or_existing_paths(tmp_path):
    src=tmp_path/'capture';src.mkdir();(src/base.CAPTURE_NAME).write_bytes(b'x')
    out=tmp_path/'readout'
    assert lab.verify_output(src,out)==(src/base.CAPTURE_NAME,out)
    for path in (src,src/'report',tmp_path):
        with pytest.raises(ValueError,match='OUTPUT_OVERLAPS_INPUT'):
            lab.verify_output(src,path)
    out.mkdir()
    with pytest.raises(FileExistsError,match='OUTPUT_EXISTS'):
        lab.verify_output(src,out)


def _mock_archive(tmp_path):
    rows=_rows(3,2,2,True)
    features=_truth_features(rows).astype(np.float16)
    empty=np.zeros((len(rows),102400),dtype=np.float16)
    head=np.zeros(len(rows),dtype=np.float32)
    arrays={'lora__final_flat':empty,'lora__final_last':features,
            'production_head_predictions':head}
    meta={'schema':base.SCHEMA,'dataset':rows,'dataset_sha256':base.sha_text(base.canonical(rows)),
          'ablation':['lora'], 'lineage':{'model_id':'mock'},
          'captures':{'lora':{'available_profiles':['final_flat','final_last']}},
          'array_sha256':{k:hashlib.sha256(np.ascontiguousarray(v).tobytes()).hexdigest()
                          for k,v in arrays.items()}}
    d=tmp_path/'input';d.mkdir()
    with (d/base.CAPTURE_NAME).open('wb') as f:np.savez_compressed(f,metadata=base.canonical(meta),**arrays)
    return d


def test_complete_analyze_writes_no_checkpoint_and_is_rerun_safe(tmp_path):
    d=_mock_archive(tmp_path)
    out=tmp_path/'report'
    args=lab.parser().parse_args(['--mode','analyze','--input',str(d),'--output',str(out),
                                  '--features','final_last','--offline','--null-shuffles','1'])
    original=(d/base.CAPTURE_NAME).read_bytes()
    report=lab.analyze(args)
    assert report['policy_count']==3
    assert report['source_fingerprint_verified'] is False
    assert report['no_promotion_authorized'] and not report['head_weights_modified']
    assert report['dev_selected_transfer_summary']['lora']['native_within']==1
    assert report['dev_selected_transfer_summary']['lora']['spine_within']==1
    assert (out/'report.json').exists()
    assert len((out/'transfer_scores.csv').read_text().strip().splitlines())==4
    assert len((out/'selected_transfer.csv').read_text().strip().splitlines())==4
    assert not list(out.rglob('*.pt'))
    assert original==(d/base.CAPTURE_NAME).read_bytes()
    with pytest.raises(FileExistsError,match='OUTPUT_EXISTS'):
        lab.analyze(args)


def test_bad_features_and_unverified_source_fail_before_output(tmp_path):
    d=_mock_archive(tmp_path)
    args=lab.parser().parse_args(['--mode','analyze','--input',str(d),
             '--output',str(tmp_path/'out'),'--features','not-a-capture','--offline'])
    with pytest.raises(ValueError,match='MISSING_CAPTURED_FEATURE'):
        lab.analyze(args)
    assert not (tmp_path/'out').exists()
    args.features='final_last';args.offline=False
    with pytest.raises((FileNotFoundError,KeyError)):
        lab.analyze(args)
    assert not (tmp_path/'out').exists()


def test_oracle_cli_and_bad_mode(capsys):
    assert lab.main(['--mode','oracle'])==0
    info=json.loads(capsys.readouterr().out)
    assert info['status']=='ORACLE_ONLY_NO_MODEL_FITTED'
    assert info['dataset_sha256']=='0bcf5ff1f092df985dd3ccd28110302d1ff3d54643f63ba6b21c428d4281ec61'
    with pytest.raises(ValueError,match='REQUIRES_INPUT_AND_OUTPUT'):
        lab.main(['--mode','analyze'])
    with pytest.raises(ValueError,match='INVALID_NULL_SHUFFLE_COUNT'):
        lab.main(['--null-shuffles','-1'])
