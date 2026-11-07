"""Checks matched readout, numerical inversion and non-mutating staircase routing."""
from __future__ import annotations

import json
from fractions import Fraction

import numpy as np
import pytest

from tools import nanojev_gamer_v1_c0_information_preservation as preserve
from tools import nanojev_gamer_v1_sign_information_probe as oracle
from tools import nanojev_gamer_v1_staircase as staircase


def _synthetic():
    # Enough training examples for full 64-dimensional train-only PCA.
    rows=[r.public() for r in oracle.make_deck(12,6,6,include_ood=True)]
    x=np.zeros((len(rows),80),dtype=np.float32)
    for i,row in enumerate(rows):
        sign=np.sign(float(Fraction(row['target'])))
        x[i,0]=12*sign
        x[i,1]=12*(sign==0)
        x[i,2]=12*(sign==1)
        x[i,3]=.01*(i%6)
    return rows,x


def test_layernorm_reconstruction_with_moments_faithful_in_float32_and_not_without_moments():
    import torch
    torch.manual_seed(1)
    class Net(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.norm=torch.nn.LayerNorm(102400)
    net=Net().eval()
    x=np.random.default_rng(2).normal(loc=2.5,scale=3.2,size=(4,102400)).astype(np.float32)
    with torch.no_grad():
        n=net.norm(torch.from_numpy(x)).numpy()
    recovered,metrics=preserve._norm_inverse(net,x,n,dtype=torch.float32)
    assert metrics['affine_zero_or_effectively_zero_gamma_coordinates']==0
    assert metrics['relative_rmse_vs_forward_quantized_input']<2e-5
    assert np.max(np.abs(x-recovered))<5e-4
    assert not metrics['c0_itself_receives_these_statistics']
    with torch.no_grad():
        net.norm.weight[10]=0
        n2=net.norm(torch.from_numpy(x)).numpy()
    recovered2,metrics2=preserve._norm_inverse(net,x,n2,dtype=torch.float32)
    assert metrics2['affine_zero_or_effectively_zero_gamma_coordinates']==1
    assert not np.allclose(recovered2[:,10],x[:,10])


def test_layernorm_fp16_quantization_is_recorded_without_false_exactness():
    import torch
    class Net(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.norm=torch.nn.LayerNorm(102400)
    net=Net().half().eval()
    x=np.random.default_rng(13).normal(size=(3,102400)).astype(np.float32)
    with torch.no_grad():
        n=net.norm(torch.from_numpy(x).half()).float().numpy()
    _,report=preserve._norm_inverse(net,x,n,dtype=torch.float16)
    assert report['max_abs_input_cast_error']>0
    assert report['relative_rmse_vs_forward_quantized_input']>0
    assert report['per_example_statistics_injected']


def test_train_only_pca_and_locked_matched_decoder_can_detect_stage_failure():
    rows,x=_synthetic()
    train=oracle._test_partitions(rows)['train']
    good,info=preserve._train_pca(x,train,max_rank=64)
    bad,bad_info=preserve._train_pca(np.zeros_like(x),train,max_rank=64)
    assert info['effective_rank']>=1
    assert bad_info['effective_rank']==0
    selected=preserve._select_matched(rows,{'c0_raw_flat':good})
    assert selected['selected_on']=='RAW_C0_TRAIN_AND_DEV_ONLY'
    assert selected['feature_count'] in preserve.RANKS
    matched=preserve._matched(rows,{'c0_raw_flat':(good,info),
                                   'c0_norm':(good,info),
                                   'enc_gelu_3':(bad,bad_info)},selected)
    assert matched['c0_raw_flat']['test']['balanced_sign_accuracy']>.95
    assert matched['c0_raw_flat']['test_by_family']['hawk_indifference']['triple_accuracy']>.95
    assert matched['enc_gelu_3']['test']['balanced_sign_accuracy']<=1/3+1e-6
    assert matched['enc_gelu_3']['test']['triple_accuracy']==0
    assert matched['c0_raw_flat']['beats_all_shuffled_nulls']


def test_train_only_transform_and_hyperparameters_ignore_changed_test_rows():
    rows,x=_synthetic()
    train=oracle._test_partitions(rows)['train']
    original,info=preserve._train_pca(x,train,max_rank=64)
    selected=preserve._select_matched(rows,{'c0_raw_flat':original})
    changed=x.copy()
    test=oracle._test_partitions(rows)['test']
    changed[test]=np.random.default_rng(7).normal(size=(len(test),x.shape[1]))*1000
    after,info2=preserve._train_pca(changed,train,max_rank=64)
    assert info==info2
    assert np.allclose(original[train],after[train],atol=1e-6)
    assert preserve._select_matched(rows,{'c0_raw_flat':after})==selected


def test_inverse_readout_keeps_model_fitted_to_raw_training_and_scores_real_triples():
    rows,_=_synthetic()
    x=np.zeros((len(rows),102400),dtype=np.float32)
    for i,row in enumerate(rows):
        x[i,0]=float(np.sign(float(Fraction(row['target']))))
        x[i,1]=float(Fraction(row['target']))
    # A literal copy of raw input must reproduce the diagnostic output.
    source={'decoder':'linear','development_selection':{'alpha':0.1}}
    report=preserve._inverse_readout(rows,x,x.copy(),source)
    assert report['preserves_all_test_sign_predictions']
    assert report['heldout_max_abs_prediction_change']==0
    assert report['raw']==report['inverse_with_input_moments']
    assert 'hawk_indifference' in report['test_by_family']


def test_command_rejects_training_and_cross_phase_flags():
    args=staircase.parser().parse_args(['head-100-information-preservation','--run','foo'])
    assert args.preservation_batch==8
    assert args.preservation_seed==47
    assert args.preservation_output is None
    with pytest.raises(ValueError,match='PRESERVATION_READ_ONLY_NO_STEPS'):
        staircase.main(['head-100-information-preservation','--run','foo','--steps','5'])
    with pytest.raises(ValueError,match='PRESERVATION_FLAGS_ONLY'):
        staircase.main(['head-100-encoder-bypass','--run','foo','--preservation-batch','4'])
    with pytest.raises(ValueError,match='BYPASS_FLAGS_ONLY'):
        staircase.main(['head-100-information-preservation','--run','foo','--bypass-batch','4'])


def test_output_paths_are_short_and_cannot_overlap(tmp_path):
    from tools import nanojev_gamer_v1_sufficiency as suff
    source=tmp_path/('deep-long-branch-'+'q'*220)
    source.mkdir()
    (source/'final_report.json').write_text('FROZEN')
    dest=preserve._destination(source,None)
    assert dest.name.startswith('c0-preservation-'+suff._digest(source/'final_report.json')[:12])
    assert len(dest.name)<100
    with pytest.raises(ValueError,match='OUTPUT_OVERLAPS'):
        preserve._destination(source,source/'nested')
    with pytest.raises(ValueError,match='OUTPUT_OVERLAPS'):
        preserve._destination(source,source.parent)
    dest.mkdir()
    with pytest.raises(FileExistsError):
        preserve._destination(source,dest)


def test_fake_bypass_report_refused_before_model_loading(tmp_path):
    path=tmp_path/'bypass';path.mkdir()
    (path/'final_report.json').write_text(json.dumps({'schema':'forged'}))
    with pytest.raises(ValueError,match='REQUIRES_COMPLETED_BYPASS'):
        preserve._source(path)


def test_unmatched_probes_do_not_certify_information_destruction():
    case={'test':{'balanced_sign_accuracy':.5,'triple_accuracy':0},
          'test_by_family':{'hawk_indifference':{'balanced_sign_accuracy':1/3}},
          'beats_all_shuffled_nulls':False}
    matched={'c0_raw_flat':case,'c0_norm':case,'enc_gelu_3':case}
    inv={'heldout_prediction_sign_agreement':1.0}
    norm={'affine_zero_or_effectively_zero_gamma_coordinates':0,
          'relative_rmse_vs_forward_quantized_input':0.0001}
    d=preserve._decision(matched,inv,{'recall':{'-1':0},'triple_accuracy':0},norm)
    assert d['bounded_answer']=='INSUFFICIENT_CAUSAL_SEPARATION'
    assert d['inverse_normalization_parity_demonstrated']
    assert not d['causal_information_destruction_certified']


def test_complete_mocked_run_writes_isolated_report_and_keeps_sources(tmp_path,monkeypatch):
    import torch
    from tools import nanojev_gamer_v1_c0_boundary as c0
    from tools import nanojev_gamer_v1_encoder_bypass as bypass
    from tools import nanojev_gamer_v1_sufficiency as suff
    root=tmp_path/'bypass';root.mkdir()
    boundary=tmp_path/'boundary';boundary.mkdir()
    checkpoint=tmp_path/'frozen_head.pt';checkpoint.write_bytes(b'IMMUTABLE_C0')
    (root/'final_report.json').write_text('BYPASS')
    (boundary/'final_report.json').write_text(json.dumps({
        'prior_frozen_production_c0_test':{'recall':{'-1':0},'triple_accuracy':0}}))
    rows,small=_synthetic()
    x=np.zeros((len(rows),102400),dtype=np.float32)
    x[:,:80]=small
    arrays={'lora__final_flat':x,
            'lora__final_mean100':x.reshape(len(x),100,1024).mean(axis=1),
            'lora__layer_09_mean100':x.reshape(len(x),100,1024).mean(axis=1),
            'production_head_predictions':np.zeros(len(x),dtype=np.float32)}
    prior={'source_boundary_report':str(boundary/'final_report.json'),
           'readouts':{'c0_raw_mean100':{'decoder':'linear',
              'development_selection':{'alpha':0.1}}}}
    meta={'dataset':rows,'dataset_sha256':'SYNTHETIC_DECK'}
    known={'c0_checkpoint_sha256':suff._digest(checkpoint)}
    monkeypatch.setattr(preserve,'_source',lambda _: (root,prior,meta,arrays,tmp_path,known))
    class Head(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.norm=torch.nn.LayerNorm(102400)
    head=Head().eval()
    monkeypatch.setattr(c0,'_load_head',lambda *a,**k:head)
    def fake_features(_head,raw,*,device,batch_size):
        with torch.no_grad():
            norm=_head.norm(torch.from_numpy(raw)).numpy()
        return np.zeros(len(raw),dtype=np.float32),{
            'c0_norm':norm,'enc_linear_1':raw[:,:256].copy(),
            'enc_gelu_1':raw[:,:256].copy(),'enc_linear_2':raw[:,:128].copy(),
            'enc_gelu_2':raw[:,:128].copy(),'enc_linear_3':raw[:,:64].copy(),
            'enc_gelu_3':raw[:,:64].copy(),'mid_gelu':raw[:,:64].copy()}
    monkeypatch.setattr(bypass,'_features_at_every_stage',fake_features)
    originals={str(f):suff._digest(f) for f in (checkpoint,root/'final_report.json',
                                               boundary/'final_report.json')}
    result=preserve.run_information_preservation(root,device='cpu')
    assert result['status']=='COMPLETE_READ_ONLY_BOUNDED_INFERENCE'
    assert result['no_production_training_or_weight_update']
    assert result['baseline_replay_parity']['max_abs_delta']==0
    assert 'hawk_indifference' in result['matched_stage_readouts']['c0_raw_flat']['test_by_family']
    assert {p:suff._digest(p) for p in originals}==originals
    dest=next(tmp_path.glob('c0-preservation-*/final_report.json'))
    assert json.loads(dest.read_text())['schema']==preserve.SCHEMA
