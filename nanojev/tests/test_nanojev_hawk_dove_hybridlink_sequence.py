"""Causal backbone recurrence, hybrid stages, source immutable, deterministic resume."""
from argparse import Namespace
import copy
import json

import pytest
import torch
from tools import nanojev_hawk_dove_hybridlink_sequence as hy
from tools import nanojev_hawk_dove_hybrid_teacher_probe as probe
from tests.test_nanojev_hawk_dove_hybrid_teacher_probe import seed_case


def options(source,run,out,**overrides):
    fields=dict(reference_checkpoint=source,output=out,device='cpu',
                format_steps=2,align_steps=2,joint_steps=2,probe_every=1,
                batch_size=4,eval_batch_size=256,lr=.001,heads=4,layers=1,
                seed=718,cpu_threads=1,tolerance=.05,autonomy_weight=.1,
                min_gain=.02,arms='recurrent,static',stop_after=None,resume=False,final_test=False)
    fields.update(overrides)
    return Namespace(**fields)


def setup(tmp):
    source,run,_,_=seed_case(tmp)
    reference,splits,domain,config=hy.load_source(source,"cpu")
    net=hy.HybridSequence(reference)
    return source,run,net,splits


def test_teacher_only_used_during_training_and_generated_context_grows(tmp_path,monkeypatch):
    _,_,model,splits=setup(tmp_path)
    x=splits['dev']['features'][:3]
    seen=[]
    raw_forward=model.backbone.forward
    def capture(context):
        seen.append(context.shape[1]);return raw_forward(context)
    monkeypatch.setattr(model.backbone,'forward',capture)
    answer,thoughts=model.rollout(x,4,'recurrent',trace=True)
    assert answer.shape==(3,2)
    assert [t.shape for t in thoughts]==[(3,model.width)]*4
    assert seen==[2,3,4,5,6]
    assert not any(p.requires_grad for p in model.reference.parameters())
    # No oracle partial products are used during free-running prediction.
    monkeypatch.setattr(model,'teacher_tokens',lambda _: (_ for _ in ()).throw(AssertionError('ORACLE_IN_INFERENCE')))
    assert model.rollout(x,4,'recurrent').shape==(3,2)
    assert model.rollout(x,4,'static').shape==(3,2)


def test_teacher_forced_alignment_positions_are_causal(tmp_path):
    _,_,model,splits=setup(tmp_path)
    x=splits['dev']['features'][:4]
    teacher=model.teacher_tokens(x)
    assert teacher.shape==(4,4,model.width)
    expected=model.link(model.backbone(torch.cat((model.seed(x),teacher),dim=1))[:,1:5])
    assert torch.equal(model.teacher_first_pass(x,'recurrent'),expected)
    assert model.format_pass(x)[1].shape==(4,4)
    with pytest.raises(ValueError,match='BAD_DEPTH'):
        model.rollout(x,3)
    with pytest.raises(ValueError,match='CONTEXT_SHAPE_OR_OVERFLOW'):
        model.backbone(torch.randn(2,7,model.width))


def test_no_feedback_control_is_not_recursive(tmp_path,monkeypatch):
    _,_,model,splits=setup(tmp_path)
    x=splits['dev']['features'][:4]
    captures=[];fn=model.backbone.forward
    def instrument(tokens):captures.append(tokens.shape[1]);return fn(tokens)
    monkeypatch.setattr(model.backbone,'forward',instrument)
    model.rollout(x,4,mode='static')
    assert captures==[2,6]
    with torch.no_grad():
        _,recursive=model.rollout(x,4,'recurrent',trace=True)
        _,static=model.rollout(x,4,'static',trace=True)
    assert len(recursive)==len(static)==4
    assert not torch.allclose(recursive[1],static[1])


def test_stages_jointly_update_real_backbone_and_link(tmp_path):
    _,_,net,splits=setup(tmp_path)
    x=splits['train']['features'][:4]
    y=splits['train']['numeric'][:4]
    for stage in hy.PHASES:
        params=hy.trainable(net,stage)
        net.zero_grad(set_to_none=True)
        loss,_=hy.stage_loss(net,x,y,stage,'recurrent',2e6,.1)
        loss.backward()
        assert torch.isfinite(loss)
        if stage=='format':
            assert net.backbone.layers.layers[0].linear1.weight.grad is not None
            assert net.link.net[1].weight.grad is None
        if stage=='alignment':
            assert net.backbone.layers.layers[0].linear1.weight.grad is None
            assert net.link.net[1].weight.grad is not None
        if stage=='joint':
            assert net.backbone.layers.layers[0].linear1.weight.grad is not None
            assert net.link.net[1].weight.grad is not None
            assert net.seed_numeric[0].weight.grad is not None
        assert not any(p.requires_grad for p in net.reference.parameters())


def test_full_run_and_strict_source_immutability(tmp_path):
    source,run,_,_=setup(tmp_path)
    before=(probe.sha256(source),probe.sha256(run/'teacher_best.pt'))
    report=hy.run(options(source,run,tmp_path/'run1'))
    assert report['feedback_certification']['status'] in ('PASS','NOT_ESTABLISHED')
    assert set(report['arms'])=={'recurrent','static'}
    assert 'reference_gaussian' in report['feedback_certification']['tested_controls']
    assert report['reference_gaussian_DEV']['independent_instances']>0
    assert all(report['arms'][k]['TEST'] is None for k in ('recurrent','static'))
    assert report['arms']['recurrent']['DEV_interventions']['accuracy']['normal']['independent_instances']>0
    assert before==(probe.sha256(source),probe.sha256(run/'teacher_best.pt'))
    with pytest.raises(FileExistsError,match='OUTPUT_EXISTS'):
        hy.run(options(source,run,tmp_path/'run1'))


def test_exact_resumption_across_training_stage_boundary(tmp_path):
    source,run,_,_=setup(tmp_path)
    complete=hy.run(options(source,run,tmp_path/'full'))
    paused=hy.run(options(source,run,tmp_path/'partial',stop_after=2))
    assert paused['status']=='PAUSED' and paused['index']==2
    resumed=hy.run(options(source,run,tmp_path/'partial',resume=True))
    assert complete['arms']==resumed['arms']
    for arm in ('recurrent','static'):
        a=torch.load(tmp_path/'full'/arm/'best.pt',weights_only=True)
        b=torch.load(tmp_path/'partial'/arm/'best.pt',weights_only=True)
        assert all(torch.equal(a['model'][key],b['model'][key]) for key in a['model'])
    with pytest.raises(RuntimeError,match='DRIFT'):
        hy.run(options(source,run,tmp_path/'partial',resume=True,lr=.002))


def test_frozen_source_and_input_only_no_teacher_at_eval(tmp_path):
    source,run,model,splits=setup(tmp_path)
    a=model.rollout(splits['dev']['features'][:5],4,trace=True)[0]
    b=model.rollout(splits['dev']['features'][:5],4,intervention='zero')
    assert a.shape==b.shape==(5,2)
    assert not torch.allclose(a,b)
    metrics=hy.score(model,splits['dev'],2e6,'recurrent',4,'cpu',256,.05)
    assert set(metrics['per_tier'])==set(hy.blind.TIERS)
    assert all(0<=v<=1 for v in metrics['per_tier'].values())


def test_bad_options_fail_closed(tmp_path):
    source,run,_,_=setup(tmp_path)
    with pytest.raises(ValueError,match='BAD_ARMS'):
        hy.run(options(source,run,tmp_path/'out',arms='fantasy'))
    with pytest.raises(ValueError,match='INVALID_OPTIONS'):
        hy.run(options(source,run,tmp_path/'out',joint_steps=0))


def test_resume_mid_alignment_optimizer_equivalence(tmp_path):
    source,run,_,_=setup(tmp_path)
    complete=hy.run(options(source,run,tmp_path/'full_mid',arms='recurrent'))
    pause=hy.run(options(source,run,tmp_path/'mid',arms='recurrent',stop_after=3))
    assert pause['index']==3
    continued=hy.run(options(source,run,tmp_path/'mid',arms='recurrent',resume=True))
    assert complete['arms']==continued['arms']


def test_identical_starting_parameters_across_arms_and_thought_perturbation(tmp_path):
    _,_,model,splits=setup(tmp_path)
    import copy
    torch.manual_seed(123);a=hy.HybridSequence(copy.deepcopy(model.reference))
    torch.manual_seed(123);b=hy.HybridSequence(copy.deepcopy(model.reference))
    assert all(torch.equal(a.state_dict()[k],b.state_dict()[k]) for k in a.state_dict())
    x=splits['dev']['features'][:3]
    assert not torch.equal(a.rollout(x,4,'recurrent'),a.rollout(x,4,'recurrent','repeat_first'))
