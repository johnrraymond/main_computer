"""Read-only radius-255 transfer probe and its evaluation contract."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_zero_line_radius255_probe as p
from tools.nanojev_addition_curriculum import AdditionNet, POWERS
from tools.nanojev_zero_line_ladder import SCHEMA as LADDER_SCHEMA


@pytest.fixture(autouse=True)
def single_thread():
    torch.set_num_threads(1)


class ExactInTestsOnly(torch.nn.Module):
    """Oracle exists exclusively in tests, never in the production runner."""
    def forward(self, x):
        s = x.sum(-1)
        sign = torch.full((len(x), 3), -50., device=x.device)
        sign.scatter_(1, (s.sign() + 1).long()[:, None], 50.)
        digs = torch.full((len(x), len(POWERS), 10), -50., device=x.device)
        powers = torch.tensor(POWERS, device=x.device)
        d = (s.abs().unsqueeze(-1) // powers) % 10
        digs.scatter_(2, d.unsqueeze(-1), 50.)
        return {'sign': sign, 'digits': digs}


class ZeroInTestsOnly(torch.nn.Module):
    def forward(self, x):
        sign = torch.full((len(x), 3), -50., device=x.device)
        sign[:, 1] = 50.
        digs = torch.full((len(x), len(POWERS), 10), -50., device=x.device)
        digs[:, :, 0] = 50.
        return {'sign': sign, 'digits': digs}


def fake_certified_root(tmp_path, passed=True):
    base = tmp_path / 'ladder'
    (base / 'rungs').mkdir(parents=True)
    settings = {'width': 16, 'heads': 4, 'layers': 1, 'rounds': 1, 'seed': 33}
    (base / 'config.json').write_text(json.dumps({'schema': LADDER_SCHEMA, 'settings': settings}))
    model = AdditionNet('digit_tokens', 16, 4, 1, 1, 33)
    torch.save({'schema': LADDER_SCHEMA + '.passed', 'model': model.state_dict(), 'radius': 127,
                'step': 4800, 'report': {'passed': passed, 'streak': 3, 'stability_target': 3,
                 'mastery': {'groups': {'all': {'accuracy': 1.}}}}},
               base / 'rungs' / 'radius_127_passed.pt')
    return base


def test_shell_is_unseen_and_all_five_residuals_present():
    shell = p.generate_cards('shell', p.TARGET_RESIDUALS)
    known = p.generate_cards('known', p.TARGET_RESIDUALS)
    assert {r['delta'] for r in shell} == {-2, -1, 0, 1, 2}
    assert {(r['a'], r['b']) for r in known}.isdisjoint({(r['a'], r['b']) for r in shell})
    assert all(r['a'] + r['b'] == r['delta'] for r in shell + known)
    assert all(max(abs(r['a']), abs(r['b'])) > 127 for r in shell)
    assert all(max(abs(r['a']), abs(r['b'])) <= 127 for r in known)
    assert len(shell) == len(set((r['a'], r['b']) for r in shell))
    assert all(128 <= r['magnitude'] <= 255 for r in shell)
    assert all(sum(r['delta'] == d for r in shell) > 200 for d in p.TARGET_RESIDUALS)


def test_full_2d_symmetry_and_four_scale_bands():
    shell = p.generate_cards('shell', p.TARGET_RESIDUALS)
    lookup = {(r['a'], r['b']) for r in shell}
    assert all((b, a) in lookup for a, b in lookup)
    assert {r['band'] for r in shell} == {'128-159', '160-191', '192-223', '224-255'}
    assert {r['a_sign'] for r in shell} >= {'negative', 'positive'}


def test_oracle_in_tests_only_wins_every_card():
    cards = p.predict_cards(ExactInTestsOnly(), p.generate_cards('shell', p.TARGET_RESIDUALS), 'cpu', 128)
    assert p.metrics(cards)['exact_rate'] == 1.
    assert p.invariant_audits(cards)['commutativity_both_exact']['rate'] == 1.
    assert p.invariant_audits(cards)['cross_band_same_delta']['rate'] == 1.
    assert p.breakdown(cards)['residual_x_band']['-2']['224-255']['exact_rate'] == 1.


def test_constant_zero_is_detected():
    cards = p.predict_cards(ZeroInTestsOnly(), p.generate_cards('shell', p.TARGET_RESIDUALS), 'cpu', 128)
    audit = p.breakdown(cards)
    assert audit['by_residual']['0']['exact_rate'] == 1.
    assert all(audit['by_residual'][str(x)]['exact_rate'] == 0. for x in (-2, -1, 1, 2))
    assert audit['overall']['false_zero'] > 0
    assert audit['overall']['exact_rate'] < 0.3
    assert p.invariant_audits(cards)['cross_band_same_delta']['rate'] == pytest.approx(0.2)


def test_passed_source_required(tmp_path):
    root = fake_certified_root(tmp_path, passed=False)
    with pytest.raises(RuntimeError, match='SOURCE_NOT_CERTIFIED'):
        p.load_certified_model(root, 'cpu')


def test_source_checkpoint_loaded_strictly_and_unchanged(tmp_path):
    root = fake_certified_root(tmp_path)
    path = root / 'rungs' / 'radius_127_passed.pt'
    original = p.sha256(path)
    model, info = p.load_certified_model(root, 'cpu')
    assert info['source_radius'] == 127 and info['step'] == 4800
    assert p.sha256(path) == original
    assert not model.training
    assert all(p.requires_grad for p in model.parameters())  # inference mode, not freezing mutation


def test_end_to_end_read_only_with_synthetic_untrained_checkpoint(tmp_path):
    root = fake_certified_root(tmp_path)
    ckpt = root / 'rungs' / 'radius_127_passed.pt'
    before = p.sha256(ckpt)
    dest = tmp_path / 'probe'
    args = p.parser().parse_args(['--ladder-output', str(root), '--output', str(dest),
                                  '--device', 'cpu', '--eval-batch-size', '256',
                                  '--cpu-threads', '1'])
    report = p.run(args)
    assert report['schema'] == p.SCHEMA
    assert report['read_only'] and not report['trained'] and report['test_untouched']
    assert report['unseen_near']['overall']['cards'] > 1000
    assert report['unseen_controls']['overall']['cards'] > 1000
    assert report['source']['checkpoint_sha256'] == p.sha256(ckpt) == before
    assert (dest / 'cards.csv').is_file() and (dest / 'result.json').is_file()
    assert json.loads((dest / 'result.json').read_text())['schema'] == p.SCHEMA
    with pytest.raises(FileExistsError, match='OVERWRITE_REFUSED'):
        p.run(args)


def test_never_write_to_source_or_parent(tmp_path):
    root = fake_certified_root(tmp_path)
    args = p.parser().parse_args(['--ladder-output', str(root),
                                  '--output', str(root / 'probes' / 'danger'), '--device', 'cpu'])
    with pytest.raises(ValueError, match='OUTPUT_MUST_BE_SEPARATE'):
        p.run(args)
    args.output = tmp_path  # parent directory of original source
    with pytest.raises(ValueError, match='OUTPUT_MUST_BE_SEPARATE'):
        p.run(args)


def test_cpu_cuda_semantics_when_available():
    if not torch.cuda.is_available():
        pytest.skip('CUDA unavailable')
    vals = p.generate_cards('shell', p.TARGET_RESIDUALS)[:128]
    cpu = p.predict_cards(ExactInTestsOnly(), [dict(r) for r in vals], 'cpu', 64)
    gpu = p.predict_cards(ExactInTestsOnly().cuda(), [dict(r) for r in vals], 'cuda', 64)
    assert [r['exact'] for r in cpu] == [r['exact'] for r in gpu]
