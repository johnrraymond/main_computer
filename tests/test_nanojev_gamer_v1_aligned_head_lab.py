"""Disposable aligned numerical head: verified routing and causal ablation tests."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_aligned_head_lab as aligned
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_sign_information_probe as probe
from tests.test_nanojev_gamer_v1_structured_head_lab import _prepared_mock


@pytest.fixture(autouse=True)
def _bound_cpu_threads():
    old=torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(old)


def _verified_small():
    data,prepared=_prepared_mock()
    deck=probe.make_deck(4,3,3,False)
    data['probe_info']['dataset_sha256']=probe.deck_hash(deck)
    return data,prepared


def test_oracle_regenerates_all_six_case_groups_without_training(capsys):
    aligned.main([])
    out=json.loads(capsys.readouterr().out)
    assert out['status']=='ORACLE_ONLY'
    assert out['candidate_configs']==3
    assert out['route_cases']==612
    assert out['text_router']=={'source_native':288,'spine_difference':288,'unsupported':36}
    assert not out['promotion_authorized']


def test_prompt_router_reads_text_not_metadata_or_target():
    deck=probe.make_deck(4,3,3,False)
    expected={'source_native':0,'spine_difference':0}
    for row in deck:
        assert aligned._prompt_surface(row.prompt)==row.surface
        expected[row.surface]+=1
    assert expected=={'source_native':30,'spine_difference':30}
    assert aligned._prompt_surface('What is the expected payoff difference?')=='unsupported'
    assert aligned._prompt_surface('')=='unsupported'
    with pytest.raises(ValueError,match='PROMPT_TEXT'):
        aligned._prompt_surface(None)


def test_router_refuses_forged_capture_sha_labels_and_unverifiable_deck():
    info={'dataset':[x.public() for x in probe.make_deck(4,3,3,False)],
          'dataset_sha256':probe.deck_hash(probe.make_deck(4,3,3,False))}
    routes,summary=aligned._verified_prompt_routes(info)
    assert len(routes)==60 and summary['source_native']==30 and summary['spine_difference']==30
    bad=json.loads(json.dumps(info));bad['dataset'][0]['surface']='spine_difference'
    with pytest.raises(ValueError,match='HASH_MISMATCH|FINGERPRINT|ROUTER'):
        aligned._verified_prompt_routes(bad)
    bad=json.loads(json.dumps(info));bad['dataset'][0]['prompt_sha256']='deadbeef'
    with pytest.raises(ValueError,match='HASH_MISMATCH|FINGERPRINT'):
        aligned._verified_prompt_routes(bad)
    bad=json.loads(json.dumps(info));bad['dataset_sha256']='wrong'
    with pytest.raises(ValueError,match='HASH_MISMATCH'):
        aligned._verified_prompt_routes(bad)


def test_routed_profiles_match_existing_v4_views_and_keep_dev_test_disjoint():
    data,prepared=_verified_small()
    spec,routes=aligned._routed_profiles(data,prepared)
    assert len(spec['train_source'][0])==60
    assert spec['train_source'][0].shape[1]==1024
    assert np.all(spec['train_source'][-1]==0)
    assert np.all(spec['dev_source'][-1]==0)
    assert routes['source_native']==30 and routes['spine_difference']==30
    assert len(spec['train_synthetic'][0])==24
    assert len(spec['dev_synthetic'][3])==18
    assert len(spec['test_synthetic'][3])==18
    for name in ('train_synthetic','dev_synthetic','test_synthetic'):
        assert np.any(spec[name][-1]==0) and np.any(spec[name][-1]==1)
        assert len(spec[name][-1])==len(spec[name][0] if name=='train_synthetic' else spec[name][3])
    assert np.array_equal(spec['train_synthetic'][0],prepared['final_last']['train_synthetic'][0])


def test_encoded_input_has_a_true_binary_route_not_normalized_leakage():
    x=np.ones((3,1024),np.float32)
    mu=np.zeros(1024,np.float32);sd=np.ones(1024,np.float32)
    encoded=aligned._encode(x,np.asarray([-2.,0.,2.]),np.asarray([0,1,0]),mu,sd)
    assert encoded.shape==(3,1026)
    assert np.array_equal(encoded[:,-2],[0,1,0])
    assert encoded[0,-1]<0 and encoded[2,-1]>0
    with pytest.raises(ValueError,match='INVALID_ROUTED'):
        aligned._encode(x,np.zeros(3),[0,2,0],mu,sd)
    with pytest.raises(ValueError,match='INVALID_ROUTED'):
        aligned._encode(x[:,:1000],np.zeros(3),[0,1,0],mu,sd)


def test_two_distinct_projectors_one_shared_decoder_zero_residual_initially():
    net=aligned._build_aligned_model(torch)
    x=torch.randn(12,1026)
    x[:6,-2]=0;x[6:,-2]=1
    with torch.no_grad():
        residual=net(x)
    assert torch.equal(residual,torch.zeros_like(residual))
    assert net.native[0].weight.data_ptr()!=net.game[0].weight.data_ptr()
    assert list(net.decoder.modules())[-1].out_features==4
    z=net.latent(x)
    assert z.shape==(12,aligned.PROJECT_DIM)
    with pytest.raises(ValueError,match='EXPECTS'):
        net(torch.randn(2,1025))


def _perfect_sixcase():
    """One paired mathematical group with a clear aligned solution."""
    net=aligned._build_aligned_model(torch)
    # Directly inject a latent function for differentiable loss tests.
    x=torch.zeros((6,1026))
    x[:3,-2]=0;x[3:,-2]=1
    x[:,0]=torch.tensor([-2,0,2,-2,0,2],dtype=torch.float32)
    return net,x


def test_alignment_is_supervised_contrastive_and_rejects_all_zero_collapse():
    net,x=_perfect_sixcase()
    loss,components=aligned._alignment_loss(torch,net,x,1)
    assert torch.isfinite(loss) and loss.item()>0
    assert components['supervised_bidirectional_infonce']>0
    loss.backward()
    assert net.native[0].weight.grad is not None
    assert net.game[0].weight.grad is not None
    assert net.native[0].weight.grad.abs().sum()>0
    assert net.game[0].weight.grad.abs().sum()>0
    with pytest.raises(ValueError,match='GROUP'):
        aligned._alignment_loss(torch,net,x,2)
    broken=x.clone();broken[5,-2]=0
    with pytest.raises(ValueError,match='ROUTING'):
        aligned._alignment_loss(torch,net,broken,1)


def test_gradient_reaches_shared_decoder_from_signed_supervision():
    net,x=_perfect_sixcase()
    values=torch.tensor([-.25,0,.25,-.25,0,.25])
    labels=torch.zeros(6,dtype=torch.bool)
    loss,stats=joint._training_loss(torch,net,x,torch.full((6,),.1),values,
                                    labels,weight=1.,source=False)
    loss.backward()
    assert torch.isfinite(loss)
    assert net.decoder[-1].weight.grad.abs().sum()>0
    assert net.native[0].weight.grad.abs().sum()==0  # exact zero residual initialization
    assert net.game[0].weight.grad.abs().sum()==0


def test_unaligned_and_aligned_zero_step_have_identical_predictions():
    torch.manual_seed(49)
    a=aligned._build_aligned_model(torch)
    torch.manual_seed(49)
    b=aligned._build_aligned_model(torch)
    x=torch.randn(6,1026)
    x[:3,-2]=0;x[3:,-2]=1
    assert all(torch.equal(a.state_dict()[k],b.state_dict()[k]) for k in a.state_dict())
    assert torch.equal(a(x),b(x))


def test_training_arms_differ_only_by_alignment_weight_on_fixed_seed_and_data():
    data,prepared=_verified_small()
    spec,_=aligned._routed_profiles(data,prepared)
    cfg=dict(device='cpu',steps=2,eval_every=2,seed=49,lr=.002,
             source_weight=1.,anchor_weight=3.,zero_margin_weight=2.)
    no=aligned._fit_aligned(torch,spec,alignment_weight=0.,**cfg)
    yes=aligned._fit_aligned(torch,spec,alignment_weight=2.,**cfg)
    assert no[4][0]==yes[4][0]
    assert no[5]['alignment_weight']==0.
    assert yes[5]['alignment_weight']==2.
    assert no[3]['step'] in (0,2) and yes[3]['step'] in (0,2)
    assert no[5]['source_dev_used_for_fitting'] is False
    assert no[5]['synthetic_test_used_for_fitting'] is False
    assert no[4][1]['loss_parts']['alignment'] is not None
    assert np.array_equal(no[1],yes[1])
    assert np.array_equal(no[2],yes[2])


@pytest.mark.parametrize('bad',[{'steps':0},{'anchor_weight':0.3},
                                   {'alignment_weight':0},{'zero_margin_weight':-1},
                                   {'lr':float('inf')}])
def test_invalid_optimizer_rejected_before_touching_sources(monkeypatch,tmp_path,bad):
    monkeypatch.setattr(joint,'_prepare',lambda args:pytest.fail('loaded source'))
    a=dict(device='cpu',run=tmp_path/'r',expanded_run=tmp_path/'e',
           input=tmp_path/'i',source_features=tmp_path/'s',
           output=tmp_path/'out',steps=2,eval_every=2,lr=.003,
           seed=49,source_weight=1.,anchor_weight=3.,
           zero_margin_weight=2.,alignment_weight=2.)
    a.update(bad)
    with pytest.raises(ValueError,match='INVALID_OPTIMIZER'):
        aligned.train(SimpleNamespace(**a))


def test_output_no_promotion_three_arms_and_dev_only_selection(monkeypatch,tmp_path):
    data,prepared=_verified_small()
    data['probe_info']['lineage']={'checkpoint_sha256':'frozen',
        'adapter_sha256':'frozen-lora','source_run':'source',
        'expanded_run':'expanded'}
    monkeypatch.setattr(joint,'_prepare',lambda args:(data,prepared))
    monkeypatch.setattr(joint,'_verify_after',lambda d:None)
    args=SimpleNamespace(mode='train',device='cpu',run=tmp_path/'run',
        expanded_run=tmp_path/'expanded',input=tmp_path/'probe',
        source_features=tmp_path/'retention',output=tmp_path/'output',
        steps=2,eval_every=2,lr=.003,seed=49,source_weight=1.,
        anchor_weight=3.,zero_margin_weight=2.,alignment_weight=2.)
    report=aligned.train(args)
    assert report['status']=='EXPERIMENT_COMPLETE_NO_PROMOTION'
    assert report['candidate_count']==3
    assert report['matched_alignment_ablation']['test_not_used_for_selection_or_promotion']
    assert report['matched_alignment_ablation']['identical_architecture_optimizer_data_seed_steps']
    assert [x['id'] for x in report['candidates']]==list(aligned.ARMS)
    assert report['lora_frozen'] and report['production_head_frozen']
    assert report['original_dev_used_for_fitting'] is False
    assert report['routing']['unseen_arbitrary_prompt_router']=='NOT_CERTIFIED_FOR_PRODUCTION'
    assert not report['production_promotion']
    import zipfile
    for item in report['candidates']:
        assert not item['promotion_authorized']
        assert item['original_dev_retention']['status']=='PASS'
        assert len(item['history'])==2
    for name in ('report.json','architecture_comparison.csv',
                 'checkpoint_history.csv','ablation_comparisons.csv','disposable_candidate.pt'):
        assert (args.output/name).is_file()
    saved=torch.load(args.output/'disposable_candidate.pt',map_location='cpu',weights_only=True)
    assert saved['promotion_authorized'] is False
    assert saved['status']=='DISPOSABLE_NON_PRODUCTION'
    with pytest.raises(FileExistsError):
        aligned.train(args)


def test_collapsed_latent_vectors_cannot_satisfy_alignment_objective():
    class Collapse(torch.nn.Module):
        def latent(self,x):
            return torch.zeros((len(x),aligned.PROJECT_DIM),dtype=x.dtype,device=x.device)
    _,x=_perfect_sixcase()
    loss,parts=aligned._alignment_loss(torch,Collapse(),x,1)
    # A constant vector yields uniform 1/3 contrastive probabilities.
    assert parts['supervised_bidirectional_infonce']==pytest.approx(np.log(3),rel=1e-6)
    assert loss.item()>=np.log(3)
    assert parts['signed_triple_separation']>0


def test_alignment_loss_can_learn_matched_signs_in_controlled_six_case_toy():
    net,x=_perfect_sixcase()
    torch.manual_seed(5)
    opt=torch.optim.AdamW(list(net.native.parameters())+
                          list(net.game.parameters()),lr=.01,weight_decay=0)
    start=float(aligned._alignment_loss(torch,net,x,1)[0].detach())
    for _ in range(70):
        opt.zero_grad()
        loss,_=aligned._alignment_loss(torch,net,x,1)
        loss.backward()
        opt.step()
    stop=float(aligned._alignment_loss(torch,net,x,1)[0].detach())
    assert stop<start*0.6
    z=net.latent(x).detach()
    similarities=torch.nn.functional.normalize(z[:3],dim=-1) @ \
                 torch.nn.functional.normalize(z[3:],dim=-1).T
    assert torch.equal(similarities.argmax(dim=1),torch.arange(3))
