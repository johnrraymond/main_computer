"""Phase gates, causal next-product targets, checkpoint integrity and resume."""
from argparse import Namespace
import json

import pytest
import torch

from tools import nanojev_hawk_dove_hybridlink_curriculum as c
from tools import nanojev_hawk_dove_hybridlink_sequence as base
from tests.test_nanojev_hawk_dove_hybridlink_sequence import setup


def args(source, output, phase='format', **kw):
    opts = dict(reference_checkpoint=source, output=output, phase=phase,
                steps=2, resume=False, arms='recurrent', device='cpu',
                probe_every=1, batch_size=4, eval_batch_size=128,
                lr=.001, heads=4, layers=1, seed=718, cpu_threads=1,
                tolerance=.05, autonomy_weight=.1, min_teacher=.8,
                max_next_mae=.08, min_next_r2=.8, max_consumed_mae=.08,
                max_generated_mae=.10, max_shuffle_ratio=.5)
    opts.update(kw)
    return Namespace(**opts)


def check_success(tmp_path, phase='format', **kw):
    source, _, _, splits = setup(tmp_path)
    dest = tmp_path / 'curriculum'
    return source, dest, splits, args(source, dest, phase, **kw)


def _fake_accepting_format(model, split, scale, size, tolerance):
    # Explicit mock for checkpoint lifecycle tests. Real gates have separate tests.
    return {'teacher_xy': {'min_tier_joint5': 1.0},
            'next_product': {'normalized_mae':0.,'r2':1.},
            'consumed_product': {'normalized_mae':0.}}


def _fake_accepting_alignment(model, split, arm, scale, size, tolerance):
    return {'generated_product':{'normalized_mae':0.},
            'link_over_shuffle_mse_ratio':0., 'generated_xy':{'min_tier_joint5':0.}}


def test_next_product_uses_preceding_hidden_positions(tmp_path):
    source, _, _, splits = setup(tmp_path)
    torch.manual_seed(10)
    ref, _, _, _ = base.load_source(source, 'cpu')
    model = c.CurriculumModel(ref)
    x = splits['dev']['features'][:4]
    teacher, states = model.teacher_state(x)
    assert teacher.shape == (4, 4, model.width)
    _, nxt, consumed = model.teacher_predictions(x)
    assert torch.equal(nxt, model.next_product(states[:,1:5]).squeeze(-1))
    assert torch.equal(consumed, model.value_probe(states[:,2:]).squeeze(-1))
    assert len(nxt[0])==4
    # Causal next-thought outputs cannot see corresponding teacher input.
    changed = teacher.clone()
    changed[:,1] += 10
    s2=model.backbone(torch.cat((model.seed(x), changed), dim=1))
    assert torch.allclose(states[:,1:3],s2[:,1:3],atol=1e-5)
    assert not torch.allclose(states[:,3:],s2[:,3:])


def test_phase_gradient_ownership(tmp_path):
    source, _, _, splits = setup(tmp_path)
    ref, _, _, _ = base.load_source(source, 'cpu')
    model = c.CurriculumModel(ref)
    x = splits['train']['features'][:4]; y=splits['train']['numeric'][:4]
    for phase in c.TRAIN_PHASES:
        params=c.configure_trainable(model,phase)
        model.zero_grad(set_to_none=True)
        loss=c.curriculum_loss(model,x,y,phase,'recurrent',2e6,.1)
        loss.backward()
        assert torch.isfinite(loss)
        if phase=='format':
            assert model.next_product.weight.grad is not None
            assert model.backbone.layers.layers[0].linear1.weight.grad is not None
            assert model.link.net[1].weight.grad is None
        elif phase=='alignment':
            assert model.next_product.weight.grad is None
            assert model.backbone.layers.layers[0].linear1.weight.grad is None
            assert model.link.net[1].weight.grad is not None
        else:
            assert model.backbone.layers.layers[0].linear1.weight.grad is not None
            assert model.link.net[1].weight.grad is not None
        assert all(not p.requires_grad for p in model.reference.parameters())


def test_format_and_verify_gate_refuses_weak_learning(tmp_path):
    source, output, splits, cfg = check_success(tmp_path)
    sha=base.file_sha256(source)
    result = c.run(cfg)
    assert 'next_product' in result['recurrent']
    assert 'consumed_product' in result['recurrent']
    with pytest.raises(RuntimeError, match='VERIFY_GATE_FAILED'):
        c.run(args(source, output, 'verify'))
    gate=json.loads((output/'arms/recurrent/format/gate.json').read_text())
    assert gate['status']=='FAIL'
    assert (output/'arms/recurrent/format/gate.json').exists()
    assert base.file_sha256(source)==sha
    with pytest.raises(RuntimeError, match='PREVIOUS_GATE_NOT_ACCEPTED'):
        c.run(args(source,output,'alignment'))
    with pytest.raises(FileExistsError, match='PHASE_OVERWRITE'):
        c.run(cfg)
    assert not (output/'arms/recurrent/alignment/latest.pt').exists()


def test_gate_functions_required_metrics():
    metrics={'teacher_xy':{'min_tier_joint5':.9},
             'next_product':{'normalized_mae':.05,'r2':.91},
             'consumed_product':{'normalized_mae':.03}}
    opt=Namespace(min_teacher=.8,max_next_mae=.08,min_next_r2=.8,max_consumed_mae=.08,
                  max_generated_mae=.10,max_shuffle_ratio=.5)
    assert c.format_gate(metrics,opt)['status']=='PASS'
    metrics['next_product']['r2'] = -.2
    assert c.format_gate(metrics,opt)['checks']['next_product_r2'] is False
    assert c.alignment_gate({'generated_product':{'normalized_mae':.09},
                             'link_over_shuffle_mse_ratio':.3},opt)['status']=='PASS'
    assert c.alignment_gate({'generated_product':{'normalized_mae':.12},
                             'link_over_shuffle_mse_ratio':.3},opt)['status']=='FAIL'


def test_format_resume_equivalent_to_full(tmp_path):
    source, output, splits, cfg=check_success(tmp_path)
    direct = args(source, tmp_path/'uninterrupted', steps=4)
    c.run(direct)
    c.run(cfg)
    c.run(args(source,output,steps=4,resume=True))
    a=torch.load(tmp_path/'uninterrupted/arms/recurrent/format/latest.pt',weights_only=True)
    b=torch.load(output/'arms/recurrent/format/latest.pt',weights_only=True)
    assert a['step']==b['step']==4
    assert all(torch.equal(a['model'][k],b['model'][k]) for k in a['model'])
    assert all(torch.equal(a['optimizer']['state'][i]['exp_avg'], b['optimizer']['state'][i]['exp_avg'])
               for i in a['optimizer']['state'])
    with pytest.raises(RuntimeError, match='CONFIG_DRIFT'):
        c.run(args(source,output,steps=5,resume=True,lr=.002))


def test_separate_phases_and_checkpoint_gate_lineage(tmp_path,monkeypatch):
    source, output, _, cfg=check_success(tmp_path)
    c.run(cfg)
    # Simulate passing format in the fixture to exercise the acceptance
    # *mechanism*. This is not counted as a scientific successful gate.
    monkeypatch.setattr(c,'teacher_evaluation',_fake_accepting_format)
    verified=c.run(args(source,output,'verify'))
    assert verified['recurrent']['status']=='PASS'
    with pytest.raises(ValueError,match='STEPS_BEHIND'):
        c.run(args(source,output,steps=1,resume=True))
    monkeypatch.setattr(c,'alignment_evaluation',_fake_accepting_alignment)
    c.run(args(source,output,'alignment',steps=2))
    gate=json.loads((output/'arms/recurrent/alignment/gate.json').read_text())
    assert gate['status']=='PASS'
    with pytest.raises(RuntimeError,match='FORMAT_LOCKED'):
        c.run(args(source,output,'format',steps=3,resume=True))
    joint=c.run(args(source,output,'joint',steps=2))
    assert 'autonomous' in joint['recurrent']
    assert (output/'arms/recurrent/joint/result.json').exists()
    with pytest.raises(RuntimeError,match='ALIGNMENT_LOCKED'):
        c.run(args(source,output,'alignment',steps=3,resume=True))
    assert not (output/'arms/recurrent/joint/gate.json').exists()
    assert not any('TEST' in str(k) for k in joint['recurrent'])


def test_stale_gate_refuses_alignment(tmp_path,monkeypatch):
    source, output, _, cfg=check_success(tmp_path)
    c.run(cfg)
    monkeypatch.setattr(c,'teacher_evaluation',_fake_accepting_format)
    c.run(args(source,output,'verify'))
    accepted=output/'arms/recurrent/format/gate.json'
    obj=json.loads(accepted.read_text());obj['checkpoint_sha256']='0'*64
    accepted.write_text(json.dumps(obj))
    with pytest.raises(RuntimeError,match='NOT_ACCEPTED_OR_STALE'):
        c.run(args(source,output,'alignment'))


def test_autonomous_has_no_oracle_thoughts(tmp_path,monkeypatch):
    source, _, _, splits=setup(tmp_path)
    ref, _, _, _ = base.load_source(source,'cpu')
    model=c.CurriculumModel(ref)
    expected=model.rollout(splits['dev']['features'][:3],4,'recurrent')
    monkeypatch.setattr(model,'teacher_tokens',lambda *a: (_ for _ in ()).throw(AssertionError('TEACHER_LEAK')))
    assert torch.equal(model.rollout(splits['dev']['features'][:3],4,'recurrent'),expected)


def test_distinct_product_shuffled_alignment_stats(tmp_path):
    source, _, _, splits=setup(tmp_path)
    ref, _, _, _ = base.load_source(source,'cpu')
    model=c.CurriculumModel(ref)
    metric=c.alignment_evaluation(model,splits['dev'],'recurrent',2e6,32,.05)
    assert metric['link_over_shuffle_mse_ratio']>=0
    assert len(metric['generated_product']['per_step_mae'])==4
    assert 0<=metric['generated_xy']['min_tier_joint5']<=1


def test_alignment_failure_returns_nonzero_and_blocks_joint(tmp_path,monkeypatch):
    source, output, _, cfg=check_success(tmp_path)
    c.run(cfg)
    monkeypatch.setattr(c,'teacher_evaluation',_fake_accepting_format)
    c.run(args(source,output,'verify'))
    with pytest.raises(RuntimeError,match='ALIGNMENT_GATE_FAILED'):
        c.run(args(source,output,'alignment',steps=1))
    gate=json.loads((output/'arms/recurrent/alignment/gate.json').read_text())
    assert gate['status']=='FAIL'
    with pytest.raises(RuntimeError,match='NOT_ACCEPTED_OR_STALE'):
        c.run(args(source,output,'joint',steps=1))
