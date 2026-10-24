"""Multi-tap diagnostics: source truth, frozen backbone, paired controls, resume."""
from __future__ import annotations
from argparse import Namespace
import copy
import json

import pytest
import torch
from tools import nanojev_hawk_dove_hybrid_multitap_lab as mt
from tools import nanojev_hawk_dove_hybrid_teacher_probe as probe
from tests.test_nanojev_hawk_dove_hybrid_teacher_probe import seed_case


def args(source,run,output,**kwargs):
    config=dict(reference_checkpoint=source,run_dir=run,output=output,
        checkpoint='teacher_best.pt',device='cpu',arms='early,early+late,all,all_depth1,all_depth2,all_nofeedback',
        steps=2,probe_every=1,batch_size=8,eval_batch_size=128,lr=.001,
        ridge=.01,seed=712,heads=4,layers=1,tolerance=.05,min_gain=.02,
        cpu_threads=1,resume=False,stop_after=None,final_test=False)
    config.update(kwargs)
    return Namespace(**config)


def test_real_internal_taps_are_oracle_blind(tmp_path):
    source,run,_,_=seed_case(tmp_path)
    model,splits,domain,config,_=probe.load_experiment(source,run)
    reader=mt.ReferenceTaps(model.reference)
    x=splits['dev']['features'][:10]
    a=reader.early(x)
    tap=reader.taps(a,reader.late(a))
    assert set(tap)==set(mt.TAPS)
    assert tap['early'].shape==(10,model.reference.hidden)
    assert tap['late'].shape==(10,model.reference.hidden)
    assert tap['prehead'].shape==(10,2)
    assert tap['bank'].shape==(10,32)
    assert torch.allclose(tap['bank'].reshape(-1,2,16).sum(-1),torch.ones(10,2),atol=1e-5)
    assert torch.allclose(tap['prehead'],model.reference.encoder[4](tap['late']))
    assert not any(p.requires_grad for p in reader.reference.parameters())
    probe_res=mt.linear_probes(reader,splits,max_rows=50)
    assert set(probe_res)==set(mt.SPEC)
    assert set(probe_res['early'])=={'operands','products','partial_sums'}


def test_backbone_reentry_matches_no_feedback_at_first_step(tmp_path):
    source,run,_,_=seed_case(tmp_path)
    model,splits,*_=probe.load_experiment(source,run)
    recurrent=mt.MultitapSidecar(model.reference,('early','late'),4,1,False)
    control=mt.MultitapSidecar(model.reference,('early','late'),4,1,True)
    control.load_state_dict(recurrent.state_dict())
    with torch.no_grad():
        recurrent.inject.weight.copy_(torch.eye(model.reference.hidden)*.015)
        control.inject.weight.copy_(recurrent.inject.weight)
    x=splits['dev']['features'][:8]
    assert torch.equal(recurrent(x,1),control(x,1))
    assert torch.equal(recurrent(x,0),control(x,0))
    assert not torch.allclose(recurrent(x,4),control(x,4))
    assert not any(p.requires_grad for p in recurrent.source.reference.parameters())
    loss=recurrent(x,4).square().mean();loss.backward()
    assert recurrent.inject.weight.grad is not None
    assert recurrent.link.net[1].weight.grad is not None


def test_all_sidecar_arms_share_trainable_initialization(tmp_path):
    source,run,_,_=seed_case(tmp_path)
    model,*_=probe.load_experiment(source,run)
    torch.manual_seed(700);a=mt.MultitapSidecar(model.reference,('early',),4,1)
    torch.manual_seed(700);b=mt.MultitapSidecar(model.reference,mt.TAPS,4,1,True)
    for k,val in a.state_dict().items():
        assert torch.equal(val,b.state_dict()[k]),k


def test_e2e_read_only_split_selection_and_intervention(tmp_path):
    source,run,_,_=seed_case(tmp_path)
    originals=probe.sha256(source),probe.sha256(run/'teacher_best.pt')
    result=mt.run(args(source,run,tmp_path/'output'))
    assert set(result['arms'])=={'early','early+late','all','all_depth1','all_depth2','all_nofeedback'}
    assert result['causal_feedback_gain']['status'] in ('PASS','NOT_ESTABLISHED')
    assert result['arms']['all']['TEST'] is None
    assert result['arms']['all']['DEV_interventions']['shuffle_distinct_instance']
    assert set(result['arms']['all']['DEV_interventions']['interventions'])=={
           'zero_early','zero_late','zero_prehead','zero_bank',
           'shuffle_early','shuffle_late','shuffle_prehead','shuffle_bank'}
    assert originals==(probe.sha256(source),probe.sha256(run/'teacher_best.pt'))
    with pytest.raises(FileExistsError,match='OVERWRITE'):
        mt.run(args(source,run,tmp_path/'output'))


def test_resume_exact_and_identity_refusal(tmp_path):
    source,run,_,_=seed_case(tmp_path)
    settings={'arms':'early,all,all_depth1,all_depth2,all_nofeedback'}
    whole=mt.run(args(source,run,tmp_path/'whole',**settings))
    half=mt.run(args(source,run,tmp_path/'half',stop_after=1,**settings))
    assert half['status']=='PAUSED'
    res=mt.run(args(source,run,tmp_path/'half',resume=True,**settings))
    assert whole['arms']==res['arms']
    for name in ('early','all','all_depth1','all_depth2','all_nofeedback'):
        a=torch.load(tmp_path/'whole'/name/'best.pt',weights_only=True)
        b=torch.load(tmp_path/'half'/name/'best.pt',weights_only=True)
        assert all(torch.equal(a['model'][k],b['model'][k]) for k in a['model'])
    with pytest.raises(RuntimeError,match='DRIFT'):
        mt.run(args(source,run,tmp_path/'half',resume=True,lr=.002,**settings))


def test_no_oracle_in_prediction_and_paired_grouping(tmp_path):
    source,run,_,_=seed_case(tmp_path)
    model,splits,domain,*_=probe.load_experiment(source,run)
    net=mt.MultitapSidecar(model.reference,mt.TAPS)
    x=splits['dev']['features'][:8]
    p=mt.direct_decode(net(x,4),domain)
    assert p.shape==(8,2)
    _,trace=net(x,4,return_trace=True)
    assert len(trace)==4
    assert all(t.shape==(8,model.reference.hidden) for t in trace)
    met=mt.evaluation(net,splits['dev'],domain,128,.05)
    assert met['independent_instances']>=1 and met['presentations']>=met['independent_instances']
    assert met['exact_zero_coordinates']>=0
    assert mt.paired_gain(splits['dev']['numeric'].cpu(),splits['dev']['numeric'].cpu(),splits['dev'],.05)['net']==0


def test_parser_rejects_unknown_or_unsafe_arguments(tmp_path):
    source,run,_,_=seed_case(tmp_path)
    with pytest.raises(ValueError,match='UNKNOWN'):
        mt.run(args(source,run,tmp_path/'out',arms='madeup'))
