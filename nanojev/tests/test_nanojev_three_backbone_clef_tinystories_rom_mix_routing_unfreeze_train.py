from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools' / 'nanojev_three_backbone_clef_tinystories_rom_mix_routing_unfreeze_train.py'
CHAMP = ROOT / 'tools' / 'nanojev_three_backbone_clef_tinystories_structured_supervision_train.py'


def load_target():
    name = 'test_nanojev_clef_rom_mix_targeted_unfreeze'
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    target = importlib.util.module_from_spec(spec)
    sys.modules[name] = target
    spec.loader.exec_module(target)
    return target


def load_champ(target):
    return target._load_module('test_champ_for_targeted_unfreeze', CHAMP)


def test_only_option_question_is_unfrozen():
    torch = pytest.importorskip('torch')
    target = load_target()
    champ = load_champ(target)
    head, _ = champ.build_layer_tap_head(
        torch=torch, hidden_sizes={'qwen': 1024, 'pythia': 512, 'tinystories': 768}
    )
    report = target.configure_targeted_routing(head, champ.configure_routing_freeze)
    assert report['targeted_unfrozen_parameters'] == 1_179_648
    assert report['schema'] == champ.ROUTING_FREEZE_SCHEMA
    named = dict(head.named_parameters())
    assert named[target.TARGET_PARAMETER].requires_grad
    assert all(not parameter.requires_grad for name, parameter in named.items()
               if champ.is_routing_parameter_name(name) and name != target.TARGET_PARAMETER)
    # All originally trainable parameters remain trainable.
    assert all(named[name].requires_grad for name in report['trainable_names'])


def test_routing_adapter_separate_optimizer_group():
    torch = pytest.importorskip('torch')
    target = load_target()
    champ = load_champ(target)
    head, _ = champ.build_layer_tap_head(
        torch=torch, hidden_sizes={'qwen': 1024, 'pythia': 512, 'tinystories': 768}
    )
    target.configure_targeted_routing(head, champ.configure_routing_freeze)
    frozen_backbone = torch.nn.Linear(4, 4)
    for param in frozen_backbone.parameters():
        param.requires_grad_(False)
    optimizer = target.build_targeted_optimizer(
        torch=torch, head=head, tinystories_lm=frozen_backbone,
        args=SimpleNamespace(clef_head_lr=1e-6, head_lr=1e-4, weight_decay=0.01),
    )
    assert [(g['group_name'],g['lr']) for g in optimizer.param_groups] == [
        ('clef_mature_head', 1e-6), ('tinystories_residual_taps', 1e-4),
        ('rom_routing_option_question', 1e-5),
    ]
    assert optimizer.param_groups[2]['params'] == [dict(head.named_parameters())[target.TARGET_PARAMETER]]
    grouped_ids = [id(p) for group in optimizer.param_groups for p in group['params']]
    assert len(grouped_ids) == len(set(grouped_ids))
    assert set(grouped_ids) == {id(p) for p in head.parameters() if p.requires_grad}


def test_two_to_three_group_migration_preserves_optimizer_moments():
    torch = pytest.importorskip('torch')
    target = load_target()
    champ = load_champ(target)
    mature = torch.nn.Parameter(torch.tensor([1.0, 2.0]))
    residual = torch.nn.Parameter(torch.tensor([2.0, 3.0]))
    adapter = torch.nn.Parameter(torch.tensor([4.0, 5.0]))
    old = torch.optim.AdamW([
        {'params': [mature], 'lr':1e-6, 'group_name':'clef_mature_head'},
        {'params': [residual], 'lr':1e-4, 'group_name':'tinystories_residual_taps'},
    ], foreach=False)
    (mature.sum() + 2 * residual.sum()).backward()
    old.step()
    source = old.state_dict()
    new = torch.optim.AdamW([
        {'params': [mature], 'lr':1e-6, 'group_name':'clef_mature_head'},
        {'params': [residual], 'lr':1e-4, 'group_name':'tinystories_residual_taps'},
        {'params': [adapter], 'lr':1e-5, 'group_name':'rom_routing_option_question'},
    ], foreach=False)
    info = target.migrate_optimizer(
        torch=torch, optimizer=new, source_state=source,
        original_loader=champ._load_optimizer_state_with_unfrozen_head_compat,
        champion=champ,
    )
    assert info['optimizer_reset'] is False
    assert info['migrated_state_entries'] == 2
    assert adapter not in new.state
    for p in (mature, residual):
        assert torch.equal(old.state[p]['exp_avg'], new.state[p]['exp_avg'])
        assert torch.equal(old.state[p]['exp_avg_sq'], new.state[p]['exp_avg_sq'])
    assert [g['lr'] for g in new.param_groups] == [1e-6, 1e-4, 1e-5]
    # Also exercise checkpoint resume from a post-migration 3-group state.
    again = torch.optim.AdamW([
        {'params': [mature], 'lr':1e-6, 'group_name':'clef_mature_head'},
        {'params': [residual], 'lr':1e-4, 'group_name':'tinystories_residual_taps'},
        {'params': [adapter], 'lr':1e-5, 'group_name':'rom_routing_option_question'},
    ], foreach=False)
    result = target.migrate_optimizer(
        torch=torch, optimizer=again, source_state=new.state_dict(),
        original_loader=champ._load_optimizer_state_with_unfrozen_head_compat,
        champion=champ,
    )
    assert result['mode'] == 'exact-full-head'
    assert len(again.state) == 2


def test_rom_weight_scaled_without_weakening_natural():
    torch = pytest.importorskip('torch')
    target = load_target()
    routing = torch.tensor(18.0, requires_grad=True)
    field = torch.tensor(0.8, requires_grad=True)
    parts = {'loss':.25*routing+.25*field, 'routing_loss': routing,
             'field_loss': field, 'routing_weight': .25}
    rom = target.adjust_supervision_for_representation(parts, 'rom')
    natural = target.adjust_supervision_for_representation(parts, 'natural')
    assert float(rom['loss'].detach()) == pytest.approx(.02 * 18 + .25 * .8)
    assert float(natural['loss'].detach()) == pytest.approx(.25 * 18 + .25 * .8)
    rom['loss'].backward()
    assert float(routing.grad) == pytest.approx(.02)
    assert float(field.grad) == pytest.approx(.25)
    assert target._is_rom_source_question(SimpleNamespace(question_id='abc::rom::pair-ab'),
        SimpleNamespace(is_rom_question=lambda q: q.question_id.endswith('::rom')))


def test_fails_closed_before_cycle_complete(tmp_path):
    target = load_target()
    out = tmp_path / 'run'
    out.mkdir()
    (out/'experiment.json').write_text(json.dumps({
        'trainer_variant':'three-backbone-clef-tinystories-additive-rom-mix-v2',
        'contract': {},
    }), encoding='utf-8')
    (out/'training_state.json').write_text(json.dumps({'cycle':136}), encoding='utf-8')
    with pytest.raises(RuntimeError, match='cycle 137 must finish'):
        target._state_guard(out)


def test_real_clef_gradients_reach_exact_adapter():
    torch = pytest.importorskip('torch')
    target = load_target()
    champ = load_champ(target)
    original = champ.configure_routing_freeze
    champ.configure_routing_freeze = lambda head: target.configure_targeted_routing(head, original)
    result = target.gradient_self_test(SimpleNamespace(champion=champ))
    assert result['success']
    assert result['gradient_norm'] > 0


def test_natural_retention_blocks_hidden_regressions():
    target = load_target()
    baseline = {'overall': {'accuracy':0.91, 'mean_loss':0.32}}
    assert target.natural_retention_accepts(
        baseline, {'overall': {'accuracy':0.90, 'mean_loss':0.33}}
    )
    assert not target.natural_retention_accepts(
        baseline, {'overall': {'accuracy':0.90, 'mean_loss':0.36}}
    )
    assert not target.natural_retention_accepts(
        baseline, {'overall': {'accuracy':0.87, 'mean_loss':0.31}}
    )
    with pytest.raises(RuntimeError, match='natural retention metrics missing'):
        target.natural_retention_accepts(baseline, None)
