"""Isolated, blind-TEST teacher precision experiment safeguards."""
import json
from argparse import Namespace

import pytest
import torch

from tools import nanojev_hawk_dove_hybridlink_curriculum as curriculum
from tools import nanojev_hawk_dove_hybridlink_precision as precision
from tools import nanojev_hawk_dove_hybridlink_sequence as base
from tests.test_nanojev_hawk_dove_hybridlink_curriculum import args as curriculum_args
from tests.test_nanojev_hawk_dove_hybridlink_sequence import setup


def opts(source, old, output, **overrides):
    obj = dict(reference_checkpoint=source, curriculum_output=old, output=output,
               device='cpu', arm='recurrent', scope='head', steps=2,
               resume=False, probe_every=1, batch_size=6,
               eval_batch_size=64, seed=38, lr=.0005,
               near_fraction=.01, zero_threshold=.5, zero_weight=2.,
               tolerance=.05, min_teacher=.8, cpu_threads=1)
    obj.update(overrides)
    return Namespace(**obj)


def test_adapter_initially_preserves_original_raw_predictions(tmp_path):
    source, _, _, split = setup(tmp_path)
    ref, _, _, _ = base.load_source(source,'cpu')
    model = precision.PrecisionAdapter(curriculum.CurriculumModel(ref))
    features = split['dev']['features'][:4]
    original, _, _ = model.base.teacher_predictions(features)
    initial, logits = model.raw_predictions(features)
    assert torch.equal(original,initial)
    assert not bool((logits.sigmoid() >= .5).any())
    assert torch.equal(model(features),original)


def test_zero_head_has_exact_output_without_true_target_inputs(tmp_path):
    source, _, _, split = setup(tmp_path)
    ref, _, _, _ = base.load_source(source,'cpu')
    model = precision.PrecisionAdapter(curriculum.CurriculumModel(ref))
    model.zero_classifier[-1].bias.data.fill_(5.)
    output = model(split['dev']['features'][:4])
    assert torch.equal(output,torch.zeros_like(output))


def test_stratified_sampling_and_relative_loss():
    train = {'numeric': torch.tensor([[0.,5.],[.1,1.],[20.,30.],[2.,3.]])}
    pools = precision.population_indices(train,100.,.01)
    assert [v.tolist() for v in pools] == [[2,3],[1],[0]]
    sampled = precision.balanced_sample(pools,9,torch.Generator().manual_seed(1),'cpu')
    assert len(sampled)==9
    assert set(sampled.tolist()) == {0,1,2,3} or len(set(sampled.tolist())) >= 3
    pred = torch.tensor([[.01, .03], [.02, .03]], requires_grad=True)
    truth = torch.tensor([[0., 3.],[2.,3.]])
    logits = torch.zeros_like(pred, requires_grad=True)
    loss,_ = precision.precision_loss(pred,logits,truth,100.)
    loss.backward()
    assert torch.isfinite(loss) and torch.isfinite(pred.grad).all()
    assert torch.isfinite(logits.grad).all()


def test_head_scope_does_not_mutate_backbone(tmp_path):
    source, _, _, split = setup(tmp_path)
    ref, _, _, _ = base.load_source(source,'cpu')
    adapter = precision.PrecisionAdapter(curriculum.CurriculumModel(ref))
    params = precision.configure_trainable(adapter,'head')
    assert all(not p.requires_grad for p in adapter.base.parameters())
    assert all(p.requires_grad for p in params)
    precision.configure_trainable(adapter,'backbone')
    assert adapter.base.backbone.positions.requires_grad
    assert not any(p.requires_grad for p in adapter.base.reference.parameters())


def test_run_isolated_best_resume_and_identity(tmp_path):
    source, _, _, splits = setup(tmp_path)
    old = tmp_path/'original_curriculum'
    curriculum.run(curriculum_args(source,old,steps=2))
    old_ck = old/'arms/recurrent/format/latest.pt'
    old_sha, source_sha = base.file_sha256(old_ck),base.file_sha256(source)
    target=tmp_path/'new_precision'
    first=precision.run(opts(source,old,target))
    assert first['status'] in ('PASS','FAIL')
    assert (target/'latest.pt').is_file() and (target/'best.pt').is_file()
    assert first['evaluation']['zero']['true_coordinate_count'] >= 0
    assert base.file_sha256(old_ck)==old_sha
    assert base.file_sha256(source)==source_sha
    assert not (old/'arms/recurrent/format/gate.json').exists()
    assert json.loads((target/'result.json').read_text())['promotion_policy'].startswith('EXPERIMENT_ONLY')
    with pytest.raises(FileExistsError, match='OVERWRITE_REFUSED'):
        precision.run(opts(source,old,target))
    continued=precision.run(opts(source,old,target,steps=3,resume=True))
    assert continued['latest_step']==3
    with pytest.raises(RuntimeError,match='CONFIG_DRIFT'):
        precision.run(opts(source,old,target,steps=4,resume=True,lr=.002))


def test_backbone_scope_retains_product_losses_and_preserves_inputs(tmp_path):
    source, _, _, _ = setup(tmp_path)
    old = tmp_path/'original'
    curriculum.run(curriculum_args(source,old,steps=2))
    ck = old/'arms/recurrent/format/latest.pt'
    before = base.file_sha256(ck)
    result = precision.run(opts(source,old,tmp_path/'backbone_branch',scope='backbone',steps=1))
    assert result['latest_step'] == 1
    assert base.file_sha256(ck) == before
    assert result['original_baseline']['strict']['min_tier_joint5'] >= 0


def test_resume_matches_uninterrupted_training(tmp_path):
    source, _, _, _ = setup(tmp_path)
    old = tmp_path/'original'
    curriculum.run(curriculum_args(source,old,steps=2))
    full_dir = tmp_path/'full'
    resumed_dir = tmp_path/'resume'
    precision.run(opts(source,old,full_dir,steps=4,probe_every=2))
    precision.run(opts(source,old,resumed_dir,steps=2,probe_every=2))
    precision.run(opts(source,old,resumed_dir,steps=4,probe_every=2,resume=True))
    full = torch.load(full_dir/'latest.pt',weights_only=True)
    resumed = torch.load(resumed_dir/'latest.pt',weights_only=True)
    assert full['step'] == resumed['step'] == 4
    assert all(torch.equal(full['model'][k],resumed['model'][k]) for k in full['model'])
    assert torch.equal(full['sampling_rng'],resumed['sampling_rng'])
