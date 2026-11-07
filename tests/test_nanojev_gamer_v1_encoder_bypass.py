"""Encoder bypass: train/dev/test isolation, exact C0 stages, immutable evidence."""
from __future__ import annotations
import json
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_encoder_bypass as bypass
from tools import nanojev_gamer_v1_sign_information_probe as oracle
from tools import nanojev_gamer_v1_staircase as staircase


def _deck_features():
    rows=[x.public() for x in oracle.make_deck(4,3,3,include_ood=True)]
    X=np.zeros((len(rows),6),dtype=np.float32)
    for i,row in enumerate(rows):
        sign=int(np.sign(float(Fraction(row['target']))))
        X[i,sign+1]=3.
        X[i,3]=.1*len(row['surface'])
        X[i,4]=.001*int(row['group'].split('.')[-1])
        X[i,5]=.05*(row['surface'].startswith('spine'))
    return rows,X


def test_raw_features_sufficient_but_pure_encoder_collapse_in_mock_data():
    rows,X=_deck_features()
    good=bypass._reconstruct(rows,X)
    assert good['test']['balanced_sign_accuracy']>.9
    assert good['test']['triple_accuracy']>.9
    assert good['test_by_family']['hawk_indifference']['balanced_sign_accuracy']>.9
    collapsed=bypass._reconstruct(rows,np.ones((len(rows),4),dtype=np.float32))
    assert collapsed['test']['balanced_sign_accuracy']<=1/3+1e-8
    assert collapsed['test']['triple_accuracy']==0
    readouts={k:collapsed for k in ('c0_norm','enc_gelu_3','mid_gelu','lora_layer09_mean100')}
    readouts['c0_raw_flat']=good
    decision=bypass._decision(readouts,{
        'balanced_sign_accuracy':.333333,'recall':{'-1':0},'triple_accuracy':0})
    assert decision['bounded_answer']=='RECOVERABLE_AT_RAW_C0_INPUT_NOT_AFTER_ENCODER'
    assert not decision['causal_certification']


def test_raw_feature_failures_do_not_assert_information_absence():
    rows,X=_deck_features()
    failed=bypass._reconstruct(rows,np.ones((len(rows),4),dtype=np.float32))
    readouts={k:failed for k in ('c0_raw_flat','c0_norm','enc_gelu_3','mid_gelu','lora_layer09_mean100')}
    decision=bypass._decision(readouts,{
        'balanced_sign_accuracy':.333333,'recall':{'-1':0},'triple_accuracy':0})
    assert decision['bounded_answer']=='RAW_INPUT_AND_ENCODER_FAIL_SUFFICIENCY_GATE'
    assert 'not guaranteed' in decision['interpretation']


def test_all_kernel_splits_use_training_only_and_preserve_heldout():
    rows,X=_deck_features()
    original=bypass._reconstruct(rows,X)
    # Changing the entire held-out split must not affect development choice
    # or train-derived kernel bandwidth; unlike all-case feature scaling.
    changed=X.copy()
    idx=[i for i,row in enumerate(rows) if row['split']=='test']
    changed[idx]=np.random.default_rng(55).normal(0,1000,size=(len(idx),X.shape[1]))
    after=bypass._reconstruct(rows,changed)
    assert after['decoder']==original['decoder']
    assert after['development_selection']==original['development_selection']
    assert original['train_cases']==24
    assert original['dev_cases']==18
    assert original['test_cases']==54
    assert original['model_selection']=='DEV_ONLY'


def test_c0_encoder_stage_capture_parity_with_real_torch_modules():
    import torch
    from torch import nn
    class Tiny(nn.Module):
        def __init__(self):
            super().__init__()
            self.norm=nn.LayerNorm(102400)
            width=102400
            layers=[]
            for n in (10,8,5):
                layers.extend((nn.Linear(width,n),nn.GELU()))
                width=n
            self.enc=nn.Sequential(*layers)
            self.mid=nn.Sequential(nn.Linear(width,3),nn.GELU())
            self.corrected=nn.Linear(3,1)
    torch.manual_seed(4)
    head=Tiny().eval()
    for p in head.parameters():p.requires_grad_(False)
    x=np.random.default_rng(2).normal(size=(3,102400)).astype(np.float32)
    p,features=bypass._features_at_every_stage(head,x,device='cpu',batch_size=2)
    from tools.nanojev_gamer_v1_c0_boundary import _forward
    expected,_=_forward(head,x,device='cpu',batch_size=2)
    assert np.allclose(p,expected,atol=1e-6)
    assert features['c0_norm'].shape==(3,102400)
    assert [features[f'enc_gelu_{i}'].shape[1] for i in (1,2,3)]==[10,8,5]
    assert features['mid_gelu'].shape==(3,3)
    with pytest.raises(ValueError,match='INVALID_C0_RAW_INPUT'):
        bypass._features_at_every_stage(head,x[:,:4],device='cpu')


def test_staircase_phase_is_read_only_and_accepts_flags():
    options=staircase.parser().parse_args(['head-100-encoder-bypass','--run','boundary','--device','cpu'])
    assert options.bypass_batch==8
    assert options.bypass_seed==47
    with pytest.raises(ValueError,match='READ_ONLY_NO_STEPS'):
        staircase.main(['head-100-encoder-bypass','--run','boundary','--steps','1'])
    with pytest.raises(ValueError,match='FLAGS_ONLY_ENCODER_BYPASS'):
        staircase.main(['head-100-c0-boundary','--run','boundary','--bypass-batch','4'])


def test_forged_prior_report_fails_closed(tmp_path):
    path=tmp_path/'boundary';path.mkdir()
    (path/'final_report.json').write_text(json.dumps({'schema':'unrelated','status':'COMPLETE'}))
    with pytest.raises(ValueError,match='COMPLETED_BOUNDARY'):
        bypass._source(path)
    with pytest.raises(ValueError,match='OUTPUT_OVERLAPS'):
        bypass._destination(path,path/'subfolder')


def test_auto_output_shortens_long_windows_style_lineage(tmp_path):
    # This parent component is legal (under 255), but appending the old
    # '-encoder-bypass-0001' suffix would make a component too long.
    boundary=tmp_path/('long-gamer-lineage-'+'x'*221)
    boundary.mkdir()
    (boundary/'final_report.json').write_text('IMMUTABLE_ORACLE_BOUNDARY')
    from tools import nanojev_gamer_v1_sufficiency as suff
    fingerprint=suff._digest(boundary/'final_report.json')[:12]
    dest=bypass._destination(boundary,None)
    assert dest==tmp_path/('c0-encoder-bypass-'+fingerprint+'-0001')
    assert len(dest.name)<240
    assert boundary!=dest
    dest.mkdir()
    assert bypass._destination(boundary,None).name.endswith('-0002')
    # Explicit short --bypass-output stays supported and cannot overwrite.
    manual=tmp_path/'my-bypass-output'
    assert bypass._destination(boundary,manual)==manual
    manual.mkdir()
    with pytest.raises(FileExistsError):
        bypass._destination(boundary,manual)


def test_rbf_constant_input_is_safe_and_no_false_100pct_accuracy():
    rows,_=_deck_features()
    x=np.zeros((len(rows),9),dtype=np.float32)
    r=bypass._reconstruct(rows,x)
    assert r['decoder']=='linear'
    assert r['test']['balanced_sign_accuracy']<=.34
    assert not r['passes_95pct_all_classes_and_triples']
    assert r['test_by_family']['hawk_indifference']['triple_accuracy']==0


def test_end_to_end_mocked_preserves_sources_and_writes_separate_answer(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_sufficiency as suff
    from tools import nanojev_gamer_v1_c0_boundary as c0
    from tools import nanojev_two_game_spine_smoke as spine
    boundary=tmp_path/'boundary';boundary.mkdir()
    upstream=tmp_path/'upstream';upstream.mkdir()
    capture=upstream/'oracle-capture';capture.mkdir()
    signs=tmp_path/'sign';signs.mkdir()
    expanded=tmp_path/'expanded';expanded.mkdir()
    checkpoint=tmp_path/'best_model.pt';checkpoint.write_bytes(b'FROZEN')
    (upstream/'final_report.json').write_text('UPSTREAM')
    (boundary/'final_report.json').write_text('BOUNDARY')
    (capture/'frozen_features.npz').write_bytes(b'ORACLE_CAPTURE')
    (capture/'analysis.json').write_text('ORACLE_ANALYSIS')
    (signs/'final_report.json').write_text('SIGN_SOURCE')
    rows,_=_deck_features();length=len(rows)
    x=np.zeros((length,102400),dtype=np.float32)
    y=np.zeros(length,dtype=np.float32)
    data={'lora__final_flat':x,'lora__layer_09_mean100':np.zeros((length,1024),dtype=np.float32),
          'lora__final_mean100':np.zeros((length,1024),dtype=np.float32),
          'lora__final_last':np.zeros((length,1024),dtype=np.float32),
          'production_head_predictions':y}
    lineage={'checkpoint_path':str(checkpoint),'checkpoint_sha256':suff._digest(checkpoint),
             'adapter_dir':str(tmp_path/'adapter'),'adapter_sha256':'FROZEN_ADAPTER'}
    meta={'lineage':lineage,'dataset':rows,'dataset_sha256':'BOUND_DECK'}
    src={'source_expanded':str(expanded),'sign_only_source':str(signs),
         'oracle_analysis_sha256':suff._digest(capture/'analysis.json')}
    prior={'source_oracle_capture_sha256':suff._digest(capture/'frozen_features.npz'),
           'source_upstream_sha256':suff._digest(upstream/'final_report.json'),
           'prior_frozen_production_c0_test':{'balanced_sign_accuracy':.3,'triple_accuracy':0,
              'recall':{'-1':0}}}
    monkeypatch.setattr(bypass,'_source',lambda *a:(boundary,prior,upstream,src,meta,data,capture))
    monkeypatch.setattr(c0,'_load_head',lambda *a,**k:object())
    monkeypatch.setattr(spine,'adapter_identity',lambda *a:'FROZEN_ADAPTER')
    def mocked_features(_net,features,**kw):
        return np.zeros(len(features),dtype=np.float32),{
            name:np.zeros((len(features),4),dtype=np.float32) for name in (
              'c0_norm','enc_linear_1','enc_gelu_1','enc_linear_2','enc_gelu_2',
              'enc_linear_3','enc_gelu_3','mid_linear','mid_gelu')}
    monkeypatch.setattr(bypass,'_features_at_every_stage',mocked_features)
    # The readout fitting itself is tested in the unmocked unit tests above.
    mock_metric={'balanced_sign_accuracy':.3333333,'triple_accuracy':0,
                 'small_nonzero_correct_sign':0}
    def mocked_reconstruct(*a,**kw):
        return {'test':mock_metric,'test_by_family':{
          'hawk_indifference':mock_metric},'feature_width':4,
          'passes_95pct_all_classes_and_triples':False,'beats_all_shuffled_nulls':False}
    monkeypatch.setattr(bypass,'_reconstruct',mocked_reconstruct)
    files=[checkpoint,upstream/'final_report.json',boundary/'final_report.json',
         capture/'frozen_features.npz',capture/'analysis.json',signs/'final_report.json']
    before={str(p):suff._digest(p) for p in files}
    result=bypass.run_encoder_bypass(boundary,device='cpu')
    assert result['decision']['bounded_answer']=='RAW_INPUT_AND_ENCODER_FAIL_SUFFICIENCY_GATE'
    assert result['baseline_parity']['max_abs_delta']==0
    assert result['no_production_training_or_weight_update']
    assert result['readouts']['c0_raw_flat']
    assert {str(p):suff._digest(p) for p in files}==before
    out=boundary.parent/('c0-encoder-bypass-'+suff._digest(boundary/'final_report.json')[:12]+'-0001')/'final_report.json'
    assert out.exists()
    assert json.loads(out.read_text())['status']=='COMPLETE_READ_ONLY_BOUNDED_INFERENCE'
