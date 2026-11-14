"""Protected v6 continuation: case failure atlas and train-only zero/reversal repair."""
from __future__ import annotations

import csv
import json
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tests.test_nanojev_gamer_v1_protected_alignment_lab import _verified_small
from tools import nanojev_gamer_v1_protected_math_repair_v7 as v7
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint


@pytest.fixture(autouse=True)
def cpu_threads():
    n=torch.get_num_threads();torch.set_num_threads(2)
    yield
    torch.set_num_threads(n)


def _setup():
    data,pre=_verified_small()
    spec,routing=v6._routed_profiles(data,pre)
    sx,_,_,_=spec['train_source']
    tx=spec['train_synthetic'][0]
    mu,sd=joint._normalization(sx,tx)
    model=v6._build_aligned_model(torch,protected=True)
    return data,pre,spec,model,mu,sd


def _checkpoint(p, data, mu, sd, *, arm=v7.REQUIRED_PRIOR):
    net=v6._build_aligned_model(torch,protected=True)
    torch.save({'schema':v6.SCHEMA, 'arm':arm,
        'status':'DISPOSABLE_NON_PRODUCTION','promotion_authorized':False,
        'protected_original_passthrough':True,
        'correction_policy':'CAPTURE_BOUND_FAIL_CLOSED_NO_PRODUCTION_ROUTER',
        'max_signed_log_correction':v6.MAX_SIGNED_LOG_CORRECTION,
        'max_class_logit_correction':v6.MAX_CLASS_LOGIT_CORRECTION,
        'min_class_change_margin':v6.MIN_CLASS_CHANGE_MARGIN,
        'frozen_lineage':data['probe_info']['lineage'],
        'original_train_cache_sha256':data['train_cache']['sha256'],
        'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
        'weights':net.state_dict(), 'mean':torch.as_tensor(mu),
        'std':torch.as_tensor(sd),'selected_step':180},p)
    (p.parent/'report.json').write_text(json.dumps({
        'schema':v6.SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
        'selected_candidate':v7.REQUIRED_PRIOR,
        'candidates':[{'id':v7.REQUIRED_PRIOR,'selected_step':180}],
        'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
        'lineage':data['probe_info']['lineage']}),encoding='utf-8')


def test_case_atlas_contains_exact_signs_and_never_changes_original_unsupported():
    _,_,spec,model,mu,sd=_setup()
    for split,count,groups in [('train',24,4),('dev',18,3),('test',18,3)]:
        cases=v7._case_atlas(torch,model,spec,mu,sd,'cpu',split)
        aggregate=v7._group_atlas(cases)
        summary=v7._atlas_summary(cases,aggregate)
        assert len(cases)==count
        assert len(aggregate)==groups
        assert summary['groups']==groups
        assert all(c['target_fraction'] in ('-1/100','0','1/100','-1/20','1/20','-1/4','1/4','-1','1')
                   for c in cases)
        assert all(g['complete_six']==0 for g in aggregate)
        assert all(g['zero_misses']==2 for g in aggregate)
        assert summary['zero_misses']==groups*2
        assert set(summary['by_surface_and_sign']) == {
            f'{s}/{sign}' for s in ('source_native','spine_difference')
            for sign in ('negative','zero','positive')}
    assert v7._magnitude_band('0')=='exact_zero'
    assert v7._magnitude_band('1/1000')=='below_0.05'
    assert v7._magnitude_band('1/20')=='0.05_to_1'
    assert v7._magnitude_band('2')=='1_to_2'
    assert v7._operand_band('151')=='100_to_1000'
    assert v7._operand_band('1001')=='1000_or_more'


def test_group_sampling_uses_all_train_groups_and_upweights_zero_reversal_failures():
    g=[{'group':f'g{i}','zero_misses':z,'nonzero_misses':n,
        'reversal_miss':int(n>0),'surface_disagreement_pairs':0}
       for i,(z,n) in enumerate([(0,0),(1,2),(2,4),(0,2)])]
    groups=[((6*i,6*i+1,6*i+2,6*i+3,6*i+4,6*i+5),None)
            for i in range(len(g))]
    p=v7._training_group_weights(g,groups)
    assert p.shape==(4,)
    assert np.isclose(p.sum(),1)
    assert min(p)>0 and p[2]>p[1]>p[0]
    with pytest.raises(ValueError,match='GROUP_COUNT'):
        v7._training_group_weights(g[:-1],groups)


def test_targeted_loss_has_zero_reversal_and_nonzero_gradients():
    torch.manual_seed(49)
    net=v6._build_aligned_model(torch,protected=True)
    x=torch.randn(6,1026)
    x[:, -2]=torch.tensor([0.,0.,0.,1.,1.,1.])
    base=torch.full((6,),.1)
    target=torch.tensor([-.25,0.,.25,-.25,0.,.25])
    value,parts=v7._targeted_mathematical_loss(torch,net,x,base,target)
    assert torch.isfinite(value) and value.item()>0
    assert set(parts)=={'zero_class','zero_signed_log','nonzero_class',
                        'sign_reversal','matched_numeric'}
    value.backward()
    assert net.decoder[-1].weight.grad.abs().sum()>0
    bad=target.clone();bad[0]=.25
    with pytest.raises(ValueError,match='SIGN_GROUP_ORDER'):
        v7._targeted_mathematical_loss(torch,net,x,base,bad)
    with pytest.raises(ValueError,match='SIX_CASE'):
        v7._targeted_mathematical_loss(torch,net,x[:5],base[:5],target[:5])


def test_checkpoint_fingerprint_lineage_arm_and_normalization_bound(tmp_path):
    data,_,spec,_,mu,sd=_setup()
    cp=tmp_path/'v6.pt';_checkpoint(cp,data,mu,sd)
    model,actual_mu,actual_sd,raw=v7._read_v6_checkpoint(torch,cp,data,spec)
    assert model.protected
    assert np.array_equal(mu,actual_mu) and np.array_equal(sd,actual_sd)
    assert raw['selected_step']==180
    _checkpoint(cp,data,mu,sd,arm='dual_protected_aligned')
    with pytest.raises(ValueError,match='SELECTED_PROTECTED_UNALIGNED'):
        v7._read_v6_checkpoint(torch,cp,data,spec)
    _checkpoint(cp,data,mu+1,sd)
    with pytest.raises(ValueError,match='NORMALIZATION_MISMATCH'):
        v7._read_v6_checkpoint(torch,cp,data,spec)
    _checkpoint(cp,data,mu,sd)
    changed={**data,'train_cache':{**data['train_cache'],'sha256':'changed'}}
    with pytest.raises(ValueError,match='FINGERPRINT_CHANGED'):
        v7._read_v6_checkpoint(torch,cp,changed,spec)


def test_focused_and_uniform_continuations_same_initial_head_and_hard_bypass():
    _,_,spec,model,mu,sd=_setup()
    initial={k:v.detach().clone() for k,v in model.state_dict().items()}
    args=dict(device='cpu',steps=4,eval_every=2,seed=49,lr=.002,
              zero_weight=2.,reversal_weight=1.)
    a,ba,ha,ca=v7._fit_continuation(torch,spec,initial,mu,sd,focused=False,**args)
    b,bb,hb,cb=v7._fit_continuation(torch,spec,initial,mu,sd,focused=True,
                                     targeted_loss=True,**args)
    assert ha[0]==hb[0]
    assert len(ha)==len(hb)==3
    assert ca['source_train_exact_passthrough'] and cb['source_train_exact_passthrough']
    assert cb['focus_updated_from']=='TRAIN_ONLY'
    assert ca['focus_updated_from']=='NONE'
    assert all(h['source_retention']=='PASS' for h in ha+hb)
    assert ba['step'] in (0,2,4) and bb['step'] in (0,2,4)
    for net in (a,b):
        x,old,_,routes=spec['train_source']
        _,value=v6._predict(torch,net,x,old,routes,mu,sd,'cpu')
        assert np.array_equal(value,old)


@pytest.mark.parametrize('bad',[{'steps':0},{'zero_weight':-1},
                                    {'lr':float('inf')},{'reversal_weight':-1}])
def test_invalid_arguments_rejected_without_loading_source(monkeypatch,tmp_path,bad):
    monkeypatch.setattr(joint,'_prepare',lambda args:pytest.fail('loaded files'))
    cfg=dict(mode='repair',device='cpu',run=tmp_path/'run',
        expanded_run=tmp_path/'expanded',input=tmp_path/'capture',
        source_features=tmp_path/'source',prior=tmp_path/'prior.pt',
        output=tmp_path/'result',steps=4,eval_every=2,lr=.002,seed=49,
        zero_weight=2.,reversal_weight=1.)
    cfg.update(bad)
    with pytest.raises(ValueError,match='INVALID_CONFIGURATION'):
        v7.run(SimpleNamespace(**cfg))


def test_atlas_only_and_repair_report_v6_lineage_selection_and_no_promotion(monkeypatch,tmp_path):
    data,pre,spec,model,mu,sd=_setup()
    cp=tmp_path/'prior.pt';_checkpoint(cp,data,mu,sd)
    monkeypatch.setattr(joint,'_prepare',lambda args:(data,pre))
    monkeypatch.setattr(joint,'_verify_after',lambda data:None)
    cfg=dict(device='cpu',run=tmp_path/'run',expanded_run=tmp_path/'expanded',
        input=tmp_path/'capture',source_features=tmp_path/'source',prior=cp,
        steps=4,eval_every=2,lr=.002,seed=49,zero_weight=2.,reversal_weight=1.)
    atlas_dir=tmp_path/'atlas'
    report=v7.run(SimpleNamespace(mode='atlas',output=atlas_dir,**cfg))
    assert report['status']=='EXPERIMENT_COMPLETE_NO_PROMOTION'
    assert len(report['candidates'])==1
    assert report['selected_candidate']=='v6_reference'
    assert not (atlas_dir/'disposable_candidate.pt').exists()
    assert (atlas_dir/'v6_reference_cases_dev.csv').exists()
    assert (atlas_dir/'v6_reference_groups_previously_seen_test.csv').exists()
    with pytest.raises(FileExistsError):
        v7.run(SimpleNamespace(mode='atlas',output=atlas_dir,**cfg))
    out=tmp_path/'repair'
    rep=v7.run(SimpleNamespace(mode='repair',output=out,**cfg))
    assert [o['id'] for o in rep['candidates']]==['v6_reference',*v7.ARMS]
    assert rep['training_ablation']['same_initial_v6_weights']
    assert rep['training_ablation']['training_atlas_from']=='TRAIN_ONLY'
    assert rep['original_dev_used_for_fitting'] is False
    assert rep['synthetic_test_used_for_fitting'] is False
    assert not rep['production_weights_modified'] and not rep['production_promotion']
    assert rep['general_production_router']=='NOT_CERTIFIED'
    assert rep['original_task_protection']=='EXACT_V6_HARD_BYPASS_UNCHANGED'
    assert len(rep['checkpoint_history']['zero_reversal_focused'])==3
    assert (out/'zero_reversal_focused_cases_dev.csv').exists()
    assert (out/'candidate_comparison.csv').exists()
    saved=torch.load(out/'disposable_candidate.pt',weights_only=True)
    assert saved['status']=='DISPOSABLE_NON_PRODUCTION'
    assert saved['promotion_authorized'] is False
    assert saved['protected_original_passthrough']
    with (out/'candidate_comparison.csv').open() as f:
        rows=list(csv.DictReader(f))
    assert len(rows)==4
