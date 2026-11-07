"""Synthetic oracle, frozen-C0 and protocol tests. No Qwen/GPU required."""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
import json

import numpy as np
import pytest
import torch
from tools import nanojev_gamer_v1_c0_sign_causality as diag
from tools import nanojev_gamer_v1_sign_information_probe as oracle
from tools.nanojev_qwen_c0_compressive_train import make_network


def deck():
    return [x.public() for x in oracle.make_deck(24,12,12)]


def model():
    torch.manual_seed(101)
    net=make_network(torch,12,hidden=(8,6,4,3)).float().eval()
    for p in net.parameters():p.requires_grad_(False)
    return net


def features(rows):
    x=np.zeros((len(rows),12),dtype=np.float32)
    for i,r in enumerate(rows):
        sign=int(np.sign(float(Fraction(r['target']))))
        x[i,0]=sign*7
        x[i,1]=hash(r['group'])%100/100
        x[i,2]=0
    return x


def test_oracle_pairs_are_matched_and_complete():
    rows=deck()
    groups=diag._groups(rows,'test')
    assert len(groups)==36
    assert sum(t['family']=='hawk_indifference' for t in groups)==12
    assert sum(t['family']=='signed_offset' for t in groups)==24
    for g in groups:
        assert {int(np.sign(float(Fraction(rows[g[x]]['target'])))) for x in ('negative','zero','positive')}=={-1,0,1}
        assert len({rows[g[x]]['group'] for x in ('negative','zero','positive')})==1
        assert len({rows[g[x]]['surface'] for x in ('negative','zero','positive')})==1


def test_oracle_duplicate_or_partial_triple_refused():
    rows=deck()
    rows[0]['target']=rows[2]['target']
    with pytest.raises(ValueError,match='DUPLICATE_SIGN'):
        diag._groups(rows,'train')
    rows=deck();rows=[r for r in rows if r['id']!=rows[0]['id']]
    with pytest.raises(ValueError,match='INCOMPLETE_ORACLE_TRIPLE'):
        diag._groups(rows,'train')


def test_real_sign_axis_fitted_to_train_only_and_preserved_in_test():
    rows=deck();x=features(rows)
    r=diag._geometry(rows,x)
    assert r['train_axis_fit']=='TRAIN_TRIPLES_ONLY'
    for name in ('signed_offset','hawk_indifference'):
        f=r['test_by_family'][name]
        assert f['sign_ordering_along_frozen_train_axis']==1.0
        assert f['zero_distance_from_signed_midpoint_over_span']['median']<0.001
    # With the train rows untouched, a test-only contrast reversal cannot
    # silently reorient the frozen train-axis.
    y=x.copy()
    for t in diag._groups(rows,'test'):
        y[t['negative'],0],y[t['positive'],0]=y[t['positive'],0],y[t['negative'],0]
    z=diag._geometry(rows,y)
    assert z['test_by_family']['signed_offset']['sign_ordering_along_frozen_train_axis']==0.0


def test_full_frozen_suffix_parity_at_every_stage():
    net=model()
    rows=deck()
    x=features(rows)[:7]
    stages,corrected,aux,pred=diag._read_stages(net,x,device='cpu',batch=3)
    assert len(corrected)==len(aux)==len(pred)==7
    assert tuple(stages)==diag.STAGES
    for stage,vec in stages.items():
        assert len(vec)==7
        got=diag._suffix_corrected(net,stage,vec,device='cpu',batch=2)
        assert np.allclose(got,corrected,atol=1e-6),stage
    with pytest.raises(ValueError,match='UNKNOWN_STAGE'):
        diag._suffix_corrected(net,'bad_stage',x,device='cpu')


def test_counterfactual_real_and_equal_l2_permuted_controls_by_family():
    net=model();rows=deck();x=features(rows)
    stage,logits,aux,pred=diag._read_stages(net,x,device='cpu',batch=32)
    report=diag._counterfactual(net,rows,stage,logits,'c0_norm',device='cpu',batch=32)
    assert report['intervention_is_synthetic_not_mathematical_counterfactual']
    for family in ('signed_offset','hawk_indifference'):
        assert report['arms']['real_sign_contrast'][family]['triples'] in (24,12)
        assert report['arms']['coordinate_permuted_equal_l2_null'][family]['triples'] in (24,12)
        assert np.isfinite(report['real_versus_equal_l2_null'][family]['real_over_null_abs_output_response'])


def test_counterfactual_with_frozen_constant_final_weight_cannot_cross_zero():
    net=model();rows=deck();x=features(rows)
    with torch.no_grad():
        net.corrected.weight.zero_()
        net.corrected.bias.fill_(1.0)
    stage,logits,aux,pred=diag._read_stages(net,x,device='cpu',batch=32)
    report=diag._counterfactual(net,rows,stage,logits,'mid_gelu',device='cpu',batch=16)
    for family in ('signed_offset','hawk_indifference'):
        v=report['arms']['real_sign_contrast'][family]
        assert v['fraction_both_directions_expected']==0
        assert v['fraction_minus_crosses_zero']==0
        assert v['mean_abs_intervention_span']==0


def test_corrected_head_report_distinguishes_bias_and_weighted_activation():
    net=model();rows=deck();x=features(rows)
    stage,logits,aux,pred=diag._read_stages(net,x,device='cpu',batch=32)
    h=diag._head_analysis(rows,logits,aux,stage['mid_gelu'],net)
    assert h['corrected_final_linear_bias_signed_log']==pytest.approx(net.corrected.bias.item())
    assert h['test_by_family_and_true_sign']['signed_offset']['0']['corrected_signed_log']['count']==24
    assert h['test_by_family_and_true_sign']['hawk_indifference']['-1']['corrected_value']['count']==12
    assert h['auxiliary_uncorrected_head_is_frozen_diagnostic_only']


def test_requires_audited_baseline_and_fingerprinted_snapshots():
    r={'steps':{'0':{},'500':{},'1500':{},'3000':{}}}
    assert diag._select_steps(r,'0,500,3000')==[0,500,3000]
    assert diag._select_steps(r,'all')==[0,500,1500,3000]
    with pytest.raises(ValueError,match='REQUIRE_ZERO'):
        diag._select_steps(r,'500,1500')
    with pytest.raises(ValueError,match='REQUIRE_ZERO'):
        diag._select_steps(r,'0,250')
    with pytest.raises(ValueError,match='BAD_STEP_LIST'):
        diag._select_steps(r,'0,oops')


def test_output_directory_guard(tmp_path):
    base=tmp_path/'protected';base.mkdir()
    with pytest.raises(ValueError,match='OVERLAPS'):
        diag._safe_output(base/'nested',(base,))
    with pytest.raises(ValueError,match='OVERLAPS'):
        diag._safe_output(tmp_path,(base,))
    with pytest.raises(FileExistsError):
        diag._safe_output(base,())
    assert diag._safe_output(tmp_path/'new',(base,))==tmp_path/'new'


def test_cli_refuses_bad_batch_before_loading_cuda():
    with pytest.raises(ValueError,match='INVALID_BATCH'):
        diag.main(['--audit','audit','--output','out','--head-batch','0'])
    assert diag.SCHEMA.startswith('nanojev.gamer.v1.')


def test_mocked_full_two_snapshot_audit_is_fingerprint_bound_and_nonmutating(tmp_path,monkeypatch):
    import sys,types
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_sufficiency as suff

    audit=tmp_path/'audit';audit.mkdir()
    source=tmp_path/'preservation';source.mkdir()
    train=tmp_path/'lora-training';train.mkdir()
    ckpt=tmp_path/'head'/'best.pt';ckpt.parent.mkdir();ckpt.write_bytes(b'IMMUTABLE_HEAD')
    orig=tmp_path/'original-adapter';orig.mkdir();(orig/'weights').write_bytes(b'IMMUTABLE_LORA')
    (source/'final_report.json').write_text('FROZEN_PRESERVATION')
    (train/'experiment.json').write_text(json.dumps({
        'source_preservation':str(source),
        'source_preservation_sha256':suff._digest(source/'final_report.json'),
        'training_deck_sha256':'STRESS_TRAIN_DECK'}))
    rows=deck();inputs=features(rows)
    pretrained=model()
    stage,logits,aux,pred=diag._read_stages(pretrained,inputs,device='cpu',batch=32)
    labels=np.array([float(Fraction(r['target'])) for r in rows])
    def head_metrics(data,estimate):
        from tools import nanojev_gamer_v1_sign_information_probe as sign
        arr=np.asarray(estimate)
        cls=np.where(np.abs(arr)<=.05,0,np.sign(arr)).astype(np.int8)
        idx=sign._test_partitions(data)['test']
        families={}
        for family in ('signed_offset','hawk_indifference'):
            ids=np.asarray([i for i in idx if data[int(i)]['family']==family],dtype=int)
            families[family]=sign._metrics(labels[ids],arr[ids],cls[ids],data,ids)
        return {'test':sign._metrics(labels[idx],arr[idx],cls[idx],data,idx),
                'test_by_family':families}
    baseline=head_metrics(rows,pred)['test']
    lineage={'checkpoint_path':str(ckpt),'checkpoint_sha256':suff._digest(ckpt),
             'adapter_dir':str(orig),'adapter_sha256':'ORIGINAL_HASH',
             'model_id':'QWEN_LOCAL','configuration':{'max_length':256,'curvature':1.0}}
    meta={'lineage':lineage,'dataset':rows,'dataset_sha256':'IMMUTABLE_ORACLE'}
    audit_contents={'schema':'nanojev.gamer.v1.isolated-lora-sign-stress-audit.v1',
         'status':'AUDIT_COMPLETE_NO_PRODUCTION_UPDATES',
         'promotion':'BLOCKED_DIAGNOSTIC_ONLY','no_c0_weight_update':True,
         'source':str(source),'training_run':str(train),'oracle_dataset_sha256':meta['dataset_sha256'],
         'original_adapter_sha256':'ORIGINAL_HASH','frozen_c0_sha256':lineage['checkpoint_sha256'],
         'training_deck_sha256':'STRESS_TRAIN_DECK',
         'steps':{str(s):{'snapshot_sha256':'SNAP'+str(s),
                    'metrics':{'production_c0':{'test':baseline}}} for s in (0,500)}}
    (audit/'final_report.json').write_text(json.dumps(audit_contents))
    for s in (0,500):
        (train/'snapshots'/f'step-{s:07d}').mkdir(parents=True)
    fake_stress=types.ModuleType('tools.nanojev_gamer_v1_lora_sign_stress')
    fake_stress.AUDIT_SCHEMA=audit_contents['schema']
    fake_stress._source=lambda path:(source,{},meta,{'lora__final_flat':inputs,
                                                     'production_head_predictions':pred},source)
    fake_stress._weights_hash=lambda path:'ORIGINAL_HASH'
    fake_stress._verify_snapshot=lambda path,step:'SNAP'+str(step)
    fake_stress._head_metrics=head_metrics
    monkeypatch.setitem(sys.modules,'tools.nanojev_gamer_v1_lora_sign_stress',fake_stress)
    fake_c0=types.ModuleType('tools.nanojev_gamer_v1_c0_boundary')
    fake_c0._load_head=lambda meta,device:pretrained
    fake_c0._verify_baseline=lambda result,source: (
        {'max_abs_delta':0.0} if np.allclose(result,source,atol=1e-6)
        else (_ for _ in ()).throw(ValueError('BASELINE_MISMATCH')))
    monkeypatch.setitem(sys.modules,'tools.nanojev_gamer_v1_c0_boundary',fake_c0)
    monkeypatch.setattr(gamer,'model_components',lambda mod,device,adapter_dir:
        (torch,object(),{'data':inputs + (0.0 if 'step-0000000' in str(adapter_dir) else .04)}))
    monkeypatch.setattr(oracle,'_capture_profiles',lambda t,m,tok,deck,**kw:
        ({'final_flat':m['data'].astype(np.float16)},{}))
    monkeypatch.setattr(gamer,'atomic_json',lambda path,data:
        Path(path).write_text(json.dumps(data)))
    before={str(p):suff._digest(p) for p in (ckpt,orig/'weights',source/'final_report.json',
                                            audit/'final_report.json',train/'experiment.json')}
    args=type('Options',(),dict(audit=audit,output=tmp_path/'new-output',steps='0,500',
        device='cpu',capture_batch=8,head_batch=32,seed=47))()
    result=diag.run(args)
    assert result['status']=='COMPLETE_READ_ONLY_NO_CAUSAL_DESTRUCTION_CERTIFICATION'
    assert result['no_training_or_model_mutation']
    assert set(result['steps'])=={'0','500'}
    assert result['steps']['0']['production_c0']['test']['triple_all_correct']==baseline['triple_all_correct']
    assert 'real_over_null_abs_output_response' in result['steps']['500']['signed_axis_interventions']['enc_gelu_3']['real_versus_equal_l2_null']['signed_offset']
    assert result['steps']['500']['frozen_suffix_replay_max_abs_signed_log_delta']['enc_gelu_3'] < 1e-6
    assert result['steps']['0']['corrected_vs_aux_and_bias_ablations']['alternatives']['deployed_corrected_head']['test']['triples']==36
    assert {p:suff._digest(p) for p in before}==before
    assert json.loads((tmp_path/'new-output'/'final_report.json').read_text())['status']==result['status']


def test_diagnostic_bias_ablation_scores_negative_zero_and_triples_unconditionally():
    net=model();rows=deck();x=features(rows)
    stages,c,u,pred=diag._read_stages(net,x,device='cpu',batch=32)
    report=diag._head_analytical_ablation(rows,c,u,net)
    keys=tuple(report['alternatives'])
    assert keys==('deployed_corrected_head','corrected_minus_frozen_bias',
                  'frozen_uncorrected_auxiliary_head','uncorrected_minus_frozen_bias')
    assert report['deployed_head_unchanged']
    assert report['bias_ablation_is_diagnostic_not_a_numerical_repair']
    for item in report['alternatives'].values():
        assert item['test']['cases']==108
        assert item['test']['triples']==36
        assert item['test_by_family']['hawk_indifference']['triples']==12
        assert item['test_by_family']['signed_offset']['triples']==24
        assert set(item['test']['recall'])=={'-1','0','1'}
