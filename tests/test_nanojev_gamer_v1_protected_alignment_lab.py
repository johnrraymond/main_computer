"""Protected v6 numerical head: fail-closed routing, preservation and ablations."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_protected_alignment_lab as aligned
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
    assert out['candidate_configs']==4
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
    assert np.all(spec['train_source'][-1]==-1)
    assert np.all(spec['dev_source'][-1]==-1)
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
    assert report['candidate_count']==4
    assert report['matched_alignment_ablation']['test_not_used_for_selection_or_promotion']
    assert report['protection_ablation']['training_steps_matched']
    assert report['protection']['general_production_router']=='NOT_CERTIFIED'
    assert report['protection']['original_retention_interpretation'].startswith('GUARANTEED_BY_HARD_BYPASS')
    assert report['matched_alignment_ablation']['identical_architecture_optimizer_data_seed_steps']
    assert [x['id'] for x in report['candidates']]==list(aligned.ARMS)
    assert report['lora_frozen'] and report['production_head_frozen']
    assert report['original_dev_used_for_fitting'] is False
    assert report['routing']['unseen_arbitrary_prompt_router']=='NOT_CERTIFIED_FOR_PRODUCTION'
    assert not report['production_promotion']
    import zipfile
    for item in report['candidates']:
        assert not item['promotion_authorized']
        assert item['original_dev_retention']['status']=='PASS' or item['architecture']=='dual_unprotected_aligned'
        assert len(item['history'])==2
    for name in ('report.json','architecture_comparison.csv',
                 'checkpoint_history.csv','ablation_comparisons.csv',
                 'protection_ablation.csv','disposable_candidate.pt'):
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


def test_capture_bound_admission_does_not_trust_template_or_dataset_label():
    deck=probe.make_deck(4,3,3,False)
    allowed={probe.sha_text(c.prompt) for c in deck}
    math=next(c for c in deck if c.surface=='source_native')
    game=next(c for c in deck if c.surface=='spine_difference')
    assert aligned._captured_prompt_route(math.prompt,allowed)==0
    assert aligned._captured_prompt_route(game.prompt,allowed)==1
    # A near-identical prompt still matches the broad text router, but its
    # exact fingerprint is not certified. The correction must fail closed.
    unknown=math.prompt+' '
    assert aligned._prompt_surface(unknown)=='source_native'
    assert aligned._captured_prompt_route(unknown,allowed)==-1
    assert aligned._captured_prompt_route(game.prompt,set())==-1
    assert aligned._captured_prompt_route('Arbitrary original-game prompt',allowed)==-1
    with pytest.raises(ValueError,match='PROMPT_TEXT'):
        aligned._captured_prompt_route(None,allowed)


def test_original_and_unsupported_predictions_are_bit_exact_even_under_large_correction():
    net=aligned._build_aligned_model(torch,protected=True)
    with torch.no_grad():
        net.decoder[-1].bias[:]=torch.tensor([-100.,100.,-100.,-100.])
    features=np.zeros((8,1024),dtype=np.float32)
    # Original and unknown, including tiny nonzero baselines, are never
    # reconstructed using the signed-log decoder.
    old=np.asarray([.00001,-.00001,0.,-5.25,12.,.1,.1,.1],dtype=np.float64)
    routes=np.asarray([-1,-1,-1,-1,-1,0,1,-1],dtype=np.float32)
    cls,val,delta=aligned._predict(torch,net,features,old,routes,
        np.zeros(1024,dtype=np.float32),np.ones(1024,dtype=np.float32),
        'cpu',return_delta=True)
    assert np.array_equal(val[:5],old[:5])
    assert val[7]==old[7]
    assert np.array_equal(cls[:5],aligned.lab._classify_continuous(old[:5]))
    assert np.all(delta[np.asarray([0,1,2,3,4,7])]==0)
    assert np.array_equal(cls[5:7],np.asarray([-1,-1]))
    assert np.all(delta[5:7,0]>=-aligned.MAX_SIGNED_LOG_CORRECTION)
    assert np.all(delta[5:7,0]<=aligned.MAX_SIGNED_LOG_CORRECTION)
    assert np.abs(delta[5:7,1:]).max()<=aligned.MAX_CLASS_LOGIT_CORRECTION


def test_low_margin_sign_change_abstains_without_target_labels():
    net=aligned._build_aligned_model(torch,protected=True)
    # With positive base=+0.1, baseline positive logit=1.5.
    # Produce negative logit 1.9 and positive logit 1.8 (margin=0.1).
    import math
    def inv_tanh(value,cap):
        return cap*math.atanh(value/cap)
    with torch.no_grad():
        net.decoder[-1].bias[:]=torch.tensor([0.,
            inv_tanh(1.9,2.0),inv_tanh(-1.9,2.0),inv_tanh(.3,2.0)])
    x=np.zeros((2,1024),dtype=np.float32)
    old=np.asarray([.1,.1],dtype=np.float64)
    cls,val,delta=aligned._predict(torch,net,x,old,[0,-1],
        np.zeros(1024,dtype=np.float32),np.ones(1024,dtype=np.float32),'cpu',return_delta=True)
    assert np.array_equal(cls,[1,1])
    assert np.array_equal(val,old)
    assert np.array_equal(delta,np.zeros_like(delta))


def test_denied_route_cannot_backpropagate_a_source_correction():
    net=aligned._build_aligned_model(torch,protected=True)
    with torch.no_grad():
        net.decoder[-1].weight.normal_(0,.05)
    x=torch.randn(6,1026)
    x[:,-2]=-1
    delta=net(x)
    assert torch.equal(delta,torch.zeros_like(delta))
    delta.sum().backward()
    assert net.decoder[-1].weight.grad.abs().sum()==0
    assert net.decoder[-1].bias.grad.abs().sum()==0


def test_original_development_fingerprint_collision_fails_closed():
    data,prepared=_verified_small()
    original_development=data['retention_info']['dataset'][0]
    original_development['prompt_sha256']=data['probe_info']['dataset'][0]['prompt_sha256']
    with pytest.raises(ValueError,match='ORIGINAL_DEV_PROMPT_OVERLAP'):
        aligned._routed_profiles(data,prepared)


def test_protected_training_learns_without_losing_original_zero_or_sign(tmp_path):
    data,prepared=_verified_small()
    spec,_=aligned._routed_profiles(data,prepared)
    net,mu,sd,best,hist,counts=aligned._fit_aligned(torch,spec,
        device='cpu',steps=120,eval_every=30,seed=49,lr=.003,
        source_weight=1,anchor_weight=3,zero_margin_weight=2,
        alignment_weight=2,protected=True)
    assert counts['protected_frozen_head_passthrough'] is True
    assert counts['original_train_role']=='NORMALIZATION_AND_PASSTHROUGH_AUDIT'
    assert best['step']>0
    assert best['dev']['all']['balanced_sign_accuracy']>1/3
    assert all(h['source_retention']=='PASS' for h in hist)
    assert all(h['source_retention_by_class']['zero']['lost']==0 for h in hist)
    sx,old,_,routes=spec['train_source']
    assert np.all(routes==-1)
    cls,pred=aligned._predict(torch,net,sx,old,routes,mu,sd,'cpu')
    assert np.array_equal(pred,np.asarray(old,dtype=np.float64))
    assert np.array_equal(cls,aligned.lab._classify_continuous(old))


def test_protected_and_unprotected_models_have_independent_inference_policy():
    net=aligned._build_aligned_model(torch,protected=True)
    raw=aligned._build_aligned_model(torch,protected=False)
    raw.load_state_dict(net.state_dict())
    with torch.no_grad():
        net.decoder[-1].bias[:]=torch.tensor([-100.,100.,-100.,-100.])
        raw.decoder[-1].bias.copy_(net.decoder[-1].bias)
    x=torch.zeros((3,1026))
    x[:,-2]=torch.tensor([-1.,0.,1.])
    assert torch.equal(net(x)[0],torch.zeros(4))
    assert raw(x)[0].abs().sum()>0
    assert not torch.equal(net(x)[1],torch.zeros(4))
    assert not torch.equal(net(x)[2],torch.zeros(4))


def test_public_certified_inference_ignores_caller_surfaces_and_fails_closed():
    deck=probe.make_deck(4,3,3,False)
    allowed={probe.sha_text(c.prompt) for c in deck}
    source=next(c for c in deck if c.surface=='source_native')
    game=next(c for c in deck if c.surface=='spine_difference')
    prompt=[source.prompt,game.prompt,source.prompt+' ',
            'A wholly unknown game question']
    net=aligned._build_aligned_model(torch,protected=True)
    with torch.no_grad():
        net.decoder[-1].bias[:]=torch.tensor([-100.,100.,-100.,-100.])
    old=np.asarray([.1,.1,0.00001,-.00002])
    cls,val=aligned.predict_certified_prompts(torch,net,
        np.zeros((4,1024),dtype=np.float32),old,prompt,allowed,
        np.zeros(1024,dtype=np.float32),np.ones(1024,dtype=np.float32),'cpu')
    assert np.array_equal(val[2:],old[2:])
    assert np.array_equal(cls[:2],[-1,-1])
    assert not np.array_equal(val[:2],old[:2])
    raw=aligned._build_aligned_model(torch,protected=False)
    with pytest.raises(ValueError,match='REQUIRES_PROTECTED_MODEL'):
        aligned.predict_certified_prompts(torch,raw,
            np.zeros((4,1024),dtype=np.float32),old,prompt,allowed,
            np.zeros(1024,dtype=np.float32),np.ones(1024,dtype=np.float32),'cpu')
    with pytest.raises(ValueError,match='INPUT_LENGTH_MISMATCH'):
        aligned.predict_certified_prompts(torch,net,
            np.zeros((4,1024),dtype=np.float32),old,prompt[:2],allowed,
            np.zeros(1024,dtype=np.float32),np.ones(1024,dtype=np.float32),'cpu')
