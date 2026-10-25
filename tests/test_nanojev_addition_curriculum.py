"""Progressive addition: exposed atomic mastery vs genuinely novel compositions."""
import json
from collections import Counter

import pytest
import torch
from tools import nanojev_addition_curriculum as lab


@pytest.fixture(autouse=True)
def single_thread():
    torch.set_num_threads(1)


def options(root, *extra):
    return lab.parser().parse_args([
        '--output', str(root), '--device', 'cpu', '--width', '16',
        '--heads', '4', '--layers', '1', '--rounds', '1',
        '--steps-per-stage', '4', '--probe-every', '2', '--batch-size', '8',
        '--eval-batch-size', '32', '--train-per-group', '12',
        '--dev-per-group', '8', '--test-per-group', '8',
        '--stop-after-stage', '1', '--cpu-threads', '1', *extra,
    ])


def canon(stage, x):
    a, b = x
    if stage in ('signed', 'cancellation', 'generalization'):
        return tuple(sorted((abs(a), abs(b))))
    return tuple(sorted((a, b)))


def test_atomic_is_complete_and_explicitly_not_holdout():
    train = lab.make_stage('atomic', 'train', 20261024, 1)
    exposed = lab.make_stage('atomic', 'dev', 20261024, 1000)
    all_pairs = {(a, b) for a in range(10) for b in range(10)}
    assert set(map(tuple, train['x'].tolist())) == all_pairs
    assert set(map(tuple, exposed['x'].tolist())) == all_pairs
    assert Counter(train['groups']) == {'no_carry': 55, 'carry': 45}
    assert train['evaluation_kind'] == exposed['evaluation_kind'] == 'exposed_atomic_mastery'
    assert max(train['y'].tolist()) == 18
    with pytest.raises(ValueError, match='ATOMIC_NO_UNSEEN_TEST_DOMAIN'):
        lab.make_stage('atomic', 'test', 20261024)


def test_composition_train_dev_test_pair_disjoint_and_correct():
    for stage in lab.STAGES[1:]:
        splits = {sp: lab.make_stage(stage, sp, 20261024, 32)
                  for sp in ('train', 'dev', 'test')}
        for sp, ds in splits.items():
            assert ds['evaluation_kind'] == 'pair_held_out'
            assert torch.equal(ds['y'], ds['x'].sum(-1))
            assert len(ds['groups']) == len(ds['y'])
            assert len(set(map(tuple, ds['x'].tolist()))) == len(ds['y'])
            for pair in ds['x'].tolist():
                key = canon(stage, pair)
                assert lab.pair_fold(*key, 20261024) == sp
        for i, left in enumerate(splits):
            for right in list(splits)[i + 1:]:
                assert {canon(stage, row) for row in splits[left]['x'].tolist()}.isdisjoint(
                    {canon(stage, row) for row in splits[right]['x'].tolist()})


def test_stages_categories_carries_signs_cancellation_and_range():
    for stage in lab.STAGES[1:]:
        dev = lab.make_stage(stage, 'dev', 20261024, 24)
        assert set(dev['groups']) == set(lab.CATEGORIES[stage])
        for (a, b), result, group in zip(dev['x'].tolist(), dev['y'].tolist(), dev['groups']):
            assert result == a + b
            if stage == 'no_carry':
                assert a % 10 + b % 10 < 10
                assert a // 10 + b // 10 < 10
            if stage == 'carry':
                units = a % 10 + b % 10 >= 10
                hundreds = a // 10 + b // 10 + int(units) >= 10
                assert group == ('both' if units and hundreds else 'units_only' if units else 'hundreds_only')
            if stage == 'signed':
                assert a < 0 or b < 0
                assert group == lab._stage_group('signed', a, b)
            if group.endswith('zero') or group == 'zero':
                assert result == 0
            if group.endswith('near') or group == 'near':
                assert 0 < abs(result) <= max(abs(a), abs(b)) / 100
            if group.startswith('ood_'):
                assert min(abs(a), abs(b)) >= 100000
            if stage == 'generalization' and group.startswith('id_'):
                assert max(abs(a), abs(b)) < 100000


def test_finite_groups_do_not_duplicate_examples_to_hit_quota():
    for stage in ('no_carry', 'carry', 'signed'):
        dev = lab.make_stage(stage, 'dev', 20261024, 3000)
        assert len(dev['y']) == len({tuple(row) for row in dev['x'].tolist()})
        assert all(dev['groups'].count(g) <= 3000 for g in lab.CATEGORIES[stage])
        assert any(dev['groups'].count(g) < 3000 for g in lab.CATEGORIES[stage])


def test_identical_initial_shared_weights_but_different_token_lengths():
    a = lab.AdditionNet('compact', 16, 4, 1, 1, 29)
    b = lab.AdditionNet('digit_tokens', 16, 4, 1, 1, 29)
    for component in ('backbone', 'sign_head', 'digit_head', 'regression_head'):
        aa, bb = getattr(a, component).state_dict(), getattr(b, component).state_dict()
        assert all(torch.equal(aa[k], bb[k]) for k in aa)
    x = torch.tensor([[71, -22], [-97, 89]])
    assert a.input(x).shape == (2, 2, 16)
    assert b.input(x).shape == (2, 14, 16)


def test_independent_digit_tokens_and_trainable_backprop():
    encoded = lab.SeparateDigitEncoding(16)(torch.tensor([[19, 29], [29, 19]]))
    assert encoded.shape == (2, 14, 16)
    assert not torch.equal(encoded[0, 0], encoded[0, 1])
    assert not torch.equal(encoded[0, 2], encoded[1, 2])
    for arm in lab.ARMS:
        model = lab.AdditionNet(arm, 16, 4, 1, 1)
        xx = torch.tensor([[3, 4], [9, 9], [-12, 12], [112, -111]])
        yy = xx.sum(-1)  # only the supervised training target
        p = model(xx)
        loss = lab.objective(p, xx, yy, 3)
        loss.backward()
        assert torch.isfinite(loss)
        assert model.backbone.layers[0].linear1.weight.grad is not None
        assert model.regression_head.weight.grad is not None
        assert model.digit_head.weight.grad is not None


def test_digit_decode_on_cpu_and_exact_zero():
    pred = {'sign': torch.full((3, 3), -20.), 'digits': torch.full((3, 7, 10), -20.)}
    for i, v in enumerate((0, -102, 98)):
        pred['sign'][i, (v > 0) - (v < 0) + 1] = 20.
        for pos in range(7):
            pred['digits'][i, pos, abs(v) // (10 ** pos) % 10] = 20.
    assert lab.decode(pred, 3).tolist() == [0, -102, 98]
    s = lab.score(torch.tensor([0., 1., 100.]), torch.tensor([0, 0, 100]),
                  ['zero', 'zero', 'away'])
    assert s['zero']['passed'] == 1
    assert s['away']['rate'] == 1.


def test_atomic_gate_is_full_mastery_regardless_of_later_threshold():
    data = {'digit': {'all': {'rate': .99}, 'no_carry': {'rate': 1.0}, 'carry': {'rate': .99}},
            'evaluation_kind': 'exposed_atomic_mastery'}
    g = lab.gate_metrics('atomic', data, {}, .001)
    assert not g['pass'] and g['threshold'] == 1.0
    data['digit']['carry']['rate'] = 1.
    assert lab.gate_metrics('atomic', data, {}, .8)['pass']
    next_stage = {'digit': {'all': {'rate': 1.0}, 'no_carry': {'rate': 1.0}}}
    data['digit']['carry']['rate'] = .99
    assert not lab.gate_metrics('no_carry', next_stage, {'atomic': data}, .8)['pass']


def test_zero_shortcut_cannot_pass_nonzero_groups():
    group = ['zero'] * 10 + ['near'] * 10 + ['away'] * 10
    y = torch.tensor([0] * 10 + [1] * 10 + [100] * 10)
    d = lab.score(torch.zeros(len(group)), y, group)
    assert d['zero']['rate'] == 1.
    assert d['near']['rate'] == d['away']['rate'] == 0.
    assert not lab.gate_metrics('cancellation', {'digit': d}, {}, .8)['pass']


def test_train_never_materializes_test_and_test_requires_gate(tmp_path, monkeypatch):
    calls = []
    original = lab.make_stage
    def observe(stage, split, *args):
        calls.append(split)
        assert split != 'test', 'TEST_DATA_LEAK'
        return original(stage, split, *args)
    monkeypatch.setattr(lab, 'make_stage', observe)
    result = lab.run(options(tmp_path))
    assert set(result) == set(lab.ARMS)
    assert 'test' not in calls
    assert not (tmp_path / 'final_test.json').exists()
    with pytest.raises(RuntimeError, match='REQUIRES_AUTHORIZATION'):
        lab.run(options(tmp_path, '--phase', 'test'))
    with pytest.raises(RuntimeError, match='BLOCKED_BY_DEV'):
        lab.run(options(tmp_path, '--phase', 'test', '--authorize-test'))
    assert 'test' not in calls


def test_resume_matches_uninterrupted_and_rejects_drift(tmp_path):
    full, part = tmp_path / 'full', tmp_path / 'part'
    lab.run(options(full, '--steps-per-stage', '6'))
    lab.run(options(part, '--steps-per-stage', '4'))
    lab.run(options(part, '--steps-per-stage', '6', '--resume'))
    for arm in lab.ARMS:
        a = torch.load(full / 'arms' / arm / 'latest.pt', weights_only=True)
        b = torch.load(part / 'arms' / arm / 'latest.pt', weights_only=True)
        assert a['stage_idx'] == b['stage_idx'] == 0
        assert a['stage_step'] == b['stage_step'] == 6
        assert all(torch.equal(a['model'][k], b['model'][k]) for k in a['model'])
    with pytest.raises(RuntimeError, match='CONFIG_DRIFT'):
        lab.run(options(part, '--resume', '--lr', '.002'))
    with pytest.raises(FileExistsError, match='OVERWRITE_REFUSED'):
        lab.run(options(part))


def test_stage_transitions_replay_and_retention(tmp_path, monkeypatch):
    # Exercise checkpoint and stage state via simulated pass in tests ONLY.
    # This deliberately does not constitute a certified trained model.
    original_gate = lab.gate_metrics
    def simulated(stage, dev, retained, threshold):
        g = original_gate(stage, dev, retained, threshold)
        g['pass'] = True
        return g
    monkeypatch.setattr(lab, 'gate_metrics', simulated)
    result = lab.run(options(tmp_path, '--steps-per-stage', '4',
                             '--stop-after-stage', '2', '--probe-every', '2'))
    for arm, info in result.items():
        assert 'atomic' in info['records']
        assert 'no_carry' in info['records']
        assert 'atomic' in info['records']['no_carry']['retention']
    assert json.loads((tmp_path / 'summary.json').read_text())['TEST'] is None


@pytest.mark.skipif(not torch.cuda.is_available(), reason='CUDA unavailable on test host')
def test_cuda_decode_matches_cpu():
    torch.manual_seed(5)
    x = {'sign': torch.randn(32, 3), 'digits': torch.randn(32, 7, 10)}
    assert torch.equal(lab.decode(x, 7), lab.decode({k: v.cuda() for k, v in x.items()}, 7).cpu())
