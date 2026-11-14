"""The radius ladder must not confuse flash-card memorization with transfer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_zero_line_ladder as z


@pytest.fixture(autouse=True)
def one_thread():
    torch.set_num_threads(1)


def params(output, *extras):
    return z.parser().parse_args([
        '--output', str(output), '--device', 'cpu', '--max-radius', '1',
        '--steps-per-rung', '4', '--probe-every', '2', '--stability-probes', '2',
        '--batch-size', '8', '--eval-batch-size', '32', '--width', '16',
        '--heads', '4', '--layers', '1', '--rounds', '1', '--cpu-threads', '1',
        '--invariance-cards', '9', *extras])


def test_complete_first_deck_and_signed_residuals():
    d = z.deck(1)
    assert len(d['x']) == 9
    assert set(map(tuple, d['x'].tolist())) == {(a,b) for a in (-1,0,1) for b in (-1,0,1)}
    assert d['y'].tolist() == [-2,-1,0,-1,0,1,0,1,2]
    assert d['groups'].count('zero') == 3
    assert torch.equal(d['y'], d['x'].sum(-1))


def test_bit_ladder_deck_cardinality_shells_and_order():
    previous = 0
    for r in z.RADII:
        full = z.deck(r)
        shell = z.deck(r, previous, True)
        assert len(full['y']) == (2*r+1)**2
        assert len(shell['y']) == ((2*r+1)**2 - (2*previous+1)**2 if previous else 9)
        assert all(max(map(abs, p)) > previous for p in shell['x'].tolist()) if previous else len(shell['y']) == 9
        assert torch.equal(shell['x'].sum(-1), shell['y'])
        previous = r


def test_shift_and_swap_preserve_sum_without_leaving_radius():
    gen = torch.Generator().manual_seed(9)
    for r in z.RADII[:5]:
        x = z.sample_deck(z.deck(r), 100, 9)['x']
        y = z.shifted(x, r, gen)
        assert torch.equal(x.sum(-1), y.sum(-1))
        assert int(y.abs().max()) <= r
        assert torch.equal(x.sum(-1), x.flip(-1).sum(-1))


def test_augmented_labels_identity_and_residual():
    x = z.deck(1)['x']
    augmented, y, n = z.make_augmentations(x, 1, torch.Generator().manual_seed(10))
    assert torch.equal(augmented.sum(-1), y)
    assert torch.equal(y[:n], y[n:2*n])
    assert len(y) > 2*n


def test_sample_deck_keeps_strata_for_preprobe():
    d = z.sample_deck(z.deck(31, 15, True), 105, 1)
    assert len(d['y']) == 105
    assert set(z.GROUPS) == set(d['groups'])
    assert torch.equal(d['x'].sum(-1), d['y'])



def test_balanced_sampler_covers_entire_deck_and_signed_zero_cases():
    d = z.deck(3)
    gen = torch.Generator().manual_seed(33)
    sampler = z.CompleteDeckSampler(len(d['y']), gen)
    groups_seen = set()
    for _ in range(10):
        ids = z.sample_balanced_flashcards(d, sampler, 32, gen)
        assert len(ids) == 32
        groups_seen.update(d['groups'][i] for i in ids.tolist())
    assert sampler.cards_seen >= len(d['y'])
    assert set(z.GROUPS) == groups_seen

def test_zero_only_model_cannot_pass_nine_card_gate():
    class Zero(torch.nn.Module):
        def forward(self, x):
            n = len(x)
            sign = torch.full((n,3), -10., device=x.device)
            sign[:,1] = 10.
            digits = torch.full((n,7,10), -10., device=x.device)
            digits[:,:,0] = 10.
            return {'sign': sign, 'digits': digits}
    result = z.evaluate(Zero(), z.deck(1), 'cpu')
    assert result['groups']['all']['passed'] == 3
    assert result['false_zero'] == 6
    assert result['groups']['zero']['accuracy'] == 1.
    assert result['groups']['positive_near']['accuracy'] == 0.
    inv = z.invariance_audit(Zero(), z.deck(1), 1, 'cpu', 32, 1)
    assert inv['views']['identity_A']['accuracy'] < 1.


def test_model_has_nonoracle_inference_and_differentiable_loss():
    from tools.nanojev_addition_curriculum import AdditionNet
    m = AdditionNet('digit_tokens', 16, 4, 1, 1, 7)
    x = z.deck(1)['x']
    val = z.train_loss(m, x, 1, torch.Generator().manual_seed(5), 'cpu')
    val.backward()
    assert torch.isfinite(val)
    assert any(p.grad is not None and float(p.grad.norm()) > 0 for p in m.backbone.parameters())
    direct = z.evaluate(m, z.deck(1), 'cpu')
    assert direct['evaluated'] == 9



def test_two_dimensional_error_map_has_one_row_per_card(tmp_path):
    import csv
    from tools.nanojev_addition_curriculum import AdditionNet
    model = AdditionNet('digit_tokens', 16, 4, 1, 1, 7)
    target = tmp_path / 'map.csv'
    z.write_error_map(model, z.deck(1), 'cpu', 16, target)
    with target.open(newline='') as fp:
        content = list(csv.DictReader(fp))
    assert len(content) == 9
    assert len({(int(r['A']), int(r['B'])) for r in content}) == 9
    assert all(int(r['A']) + int(r['B']) == int(r['target_delta']) for r in content)
    assert {r['exact'] for r in content} <= {'0', '1'}

def test_first_training_run_isolated_and_no_test_or_source_changes(tmp_path):
    opts = params(tmp_path/'first')
    # A smoke test is not expected to learn all nine; only exercise save and gate.
    z.run(opts)
    assert (opts.output/'latest.pt').exists()
    assert (opts.output/'rungs'/'radius_1.json').exists()
    assert not (opts.output/'final_test.json').exists()
    s = json.loads((opts.output/'summary.json').read_text())
    assert s['TEST'] is None
    with pytest.raises(FileExistsError, match='OVERWRITE_REFUSED'):
        z.run(opts)


def test_deterministic_resume_and_config_guard(tmp_path):
    a = params(tmp_path/'resume')
    z.run(a)
    args2 = params(a.output, '--resume', '--steps-per-rung', '6')
    z.run(args2)
    state = torch.load(a.output/'latest.pt', weights_only=True)
    # If rung unexpectedly passed in tiny smoke, stage index increases;
    # otherwise resumed step must be 6.
    assert state['idx'] == 1 or state['step'] == 6
    with pytest.raises(RuntimeError, match='CONFIG_DRIFT'):
        z.run(params(a.output, '--resume', '--seed', '99'))



def test_resume_replays_bit_identical_weights(tmp_path):
    direct = params(tmp_path / 'direct', '--steps-per-rung', '6')
    z.run(direct)
    broken = params(tmp_path / 'broken')
    z.run(broken)
    z.run(params(broken.output, '--resume', '--steps-per-rung', '6'))
    a = torch.load(direct.output / 'latest.pt', map_location='cpu', weights_only=True)
    b = torch.load(broken.output / 'latest.pt', map_location='cpu', weights_only=True)
    assert a['idx'] == b['idx'] and a['step'] == b['step']
    assert all(torch.equal(a['model'][key], b['model'][key]) for key in a['model'])
    assert torch.equal(a['generator'], b['generator'])

def test_saved_checkpoint_unmodified_under_new_runner(tmp_path):
    from tools.nanojev_addition_curriculum import AdditionNet
    import hashlib
    model = AdditionNet('digit_tokens', 16, 4, 1, 1, 6)
    ckpt = tmp_path/'existing.pt'
    torch.save({'arm':'digit_tokens','stage':'signed','model':model.state_dict()}, ckpt)
    old = hashlib.sha256(ckpt.read_bytes()).hexdigest()
    args = params(tmp_path/'new', '--init-checkpoint', str(ckpt), '--seed','6')
    z.run(args)
    assert hashlib.sha256(ckpt.read_bytes()).hexdigest() == old


def test_pretraining_next_shell_stays_disjoint_from_mastered_deck():
    first = {tuple(a) for a in z.deck(1)['x'].tolist()}
    shell = {tuple(a) for a in z.deck(3,1,True)['x'].tolist()}
    assert len(first) == 9 and len(shell) == 40
    assert not first.intersection(shell)


def test_cuda_path_if_available():
    if not torch.cuda.is_available():
        pytest.skip('CUDA hardware unavailable')
    from tools.nanojev_addition_curriculum import AdditionNet
    model = AdditionNet('digit_tokens',16,4,1,1,7).cuda()
    d = z.deck(1)
    assert z.evaluate(model,d,'cuda')['evaluated'] == 9
