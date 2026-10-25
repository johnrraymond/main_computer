"""Standalone addition experiment: no oracle inference, split safeguards, exact resume."""
from pathlib import Path
import json
import pytest
import torch

from tools import nanojev_addition_lab as lab


@pytest.fixture(autouse=True)
def one_thread():
    torch.set_num_threads(1)


def args(tmp_path, *extra):
    return lab.parser().parse_args([
        '--output', str(tmp_path), '--device', 'cpu', '--steps', '2',
        '--probe-every', '2', '--train-size', '60', '--dev-size', '12',
        '--test-size', '12', '--batch-size', '8', '--eval-batch-size', '12',
        '--width', '16', '--layers', '1', '--heads', '4', '--rounds', '1',
        '--cpu-threads', '1', *extra,
    ])


def test_split_cancellation_and_blind_operand_partition():
    splits = {k: lab.make_split(k, 120 if k == 'train' else 60, 20261024)
              for k in ('train', 'dev', 'test')}
    magnitudes = {}
    for name, data in splits.items():
        magnitudes[name] = set(data['operands'].abs().flatten().tolist())
        assert all(lab.bucket(int(m), 20261024) == name for m in magnitudes[name])
        for (a, b), total, category in zip(data['operands'].tolist(), data['targets'].tolist(), data['strata']):
            assert total == a + b
            assert a != 0 and b != 0
            if category == 'zero':
                assert total == 0
            elif category == 'near':
                assert total != 0 and abs(total) <= .01 * max(abs(a), abs(b))
            else:
                assert abs(total) > .01 * max(abs(a), abs(b))
    assert all(not magnitudes[a] & magnitudes[b]
               for a, b in (('train', 'dev'), ('train', 'test'), ('dev', 'test')))
    assert set(splits['dev']['bands']) == {'id', 'ood'}
    assert set(splits['train']['bands']) == {'id'}


def test_same_core_weight_initialization_despite_different_encoders():
    a = lab.AdditionModel('continuous', 16, 4, 1, 1, init_seed=14)
    b = lab.AdditionModel('positional', 16, 4, 1, 1, init_seed=14)
    for component in ('backbone', 'sign_head', 'digit_head', 'regression_head'):
        pa = dict(getattr(a, component).named_parameters())
        pb = dict(getattr(b, component).named_parameters())
        assert pa.keys() == pb.keys()
        assert all(torch.equal(pa[k], pb[k]) for k in pa)


@pytest.mark.parametrize('arm', lab.ARMS)
def test_model_does_not_receive_oracle_answer_and_backpropagates(arm):
    model = lab.AdditionModel(arm, 16, 4, 1, 2)
    x = torch.tensor([[12, -10], [-12, 12], [232, 423]], dtype=torch.long)
    y = torch.tensor([2, 0, 655], dtype=torch.long)
    pred = model(x)
    assert pred['sign'].shape == (3, 3)
    assert pred['digits'].shape == (3, 7, 10)
    assert pred['continuous'].shape == (3,)
    loss = lab.objective(pred, y)
    loss.backward()
    assert torch.isfinite(loss)
    assert model.backbone.layers.layers[0].linear1.weight.grad is not None
    assert any(p.grad is not None for p in model.input.parameters())
    assert model.digit_head.weight.grad is not None
    assert model.sign_head.weight.grad is not None
    assert model.regression_head.weight.grad is not None
    assert not torch.equal(model(x)['continuous'], model(torch.tensor([[12, 10], [-12, 12], [232, 423]]))['continuous'])


def test_exact_integer_decoder_and_strict_zero():
    n = len(lab.POWERS)
    pred = {'sign': torch.full((3, 3), -10.), 'digits': torch.full((3, n, 10), -10.)}
    intended = [145, 0, -278]
    for i, value in enumerate(intended):
        pred['sign'][i, {-1: 0, 0: 1, 1: 2}[(value > 0) - (value < 0)]] = 10.
        for pos in range(n):
            pred['digits'][i, pos, abs(value) // (10 ** pos) % 10] = 10.
    assert lab.decode(pred).tolist() == intended
    stats = lab._one_score(torch.tensor([1., 0., 2.]), torch.tensor([0, 0, 1]),
                           ['zero', 'zero', 'near'], ['id'] * 3, .05)
    assert stats['zero']['passed'] == 1
    assert stats['near']['passed'] == 0


def test_decoder_avoids_integer_matmul(monkeypatch):
    # The previous decoder used int64 @, which works on CPU but raises
    # NotImplementedError: addmv_impl_cuda on CUDA at the first DEV probe.
    original_matmul = torch.Tensor.__matmul__

    def forbid_integer_matmul(lhs, rhs):
        if lhs.dtype == torch.long:
            raise AssertionError('INTEGER_MATRIX_MULTIPLICATION_FORBIDDEN')
        return original_matmul(lhs, rhs)

    monkeypatch.setattr(torch.Tensor, '__matmul__', forbid_integer_matmul)
    signs = torch.tensor([2, 1, 0], dtype=torch.long)
    values = [1_234_567, 0, -3_999_999]
    pred = {'sign': torch.full((3, 3), -10.),
            'digits': torch.full((3, len(lab.POWERS), 10), -10.)}
    for i, value in enumerate(values):
        pred['sign'][i, int(signs[i])] = 10.
        for pos in range(len(lab.POWERS)):
            pred['digits'][i, pos, abs(value) // 10 ** pos % 10] = 10.
    assert lab.decode(pred).tolist() == values


@pytest.mark.skipif(not torch.cuda.is_available(), reason='CUDA unavailable')
def test_decoder_cuda_matches_cpu_exactly():
    # Specifically exercise the original failure path on GPU when present.
    n = len(lab.POWERS)
    pred = {'sign': torch.full((4, 3), -10.),
            'digits': torch.full((4, n, 10), -10.)}
    values = [0, 1, -1_234_567, 3_999_999]
    for i, value in enumerate(values):
        pred['sign'][i, (value > 0) - (value < 0) + 1] = 10.
        for pos in range(n):
            pred['digits'][i, pos, abs(value) // 10 ** pos % 10] = 10.
    cpu = lab.decode(pred)
    cuda = lab.decode({key: value.cuda() for key, value in pred.items()})
    assert cuda.device.type == 'cuda'
    assert torch.equal(cpu, cuda.cpu())
    assert cpu.tolist() == values


def test_training_does_not_generate_test_and_rejects_test_without_authorization(tmp_path, monkeypatch):
    original = lab.make_split
    invoked = []

    def guarded(split, *a, **kw):
        invoked.append(split)
        if split == 'test':
            raise AssertionError('TEST LEAK')
        return original(split, *a, **kw)

    monkeypatch.setattr(lab, 'make_split', guarded)
    result = lab.run(args(tmp_path))
    assert len(result) == 2
    assert 'arithmetic_identity_probes' in result['continuous']['DEV']
    assert invoked == ['train', 'dev']
    assert not (tmp_path / 'final_test.json').exists()
    lab.run(args(tmp_path, '--phase', 'dev'))
    assert invoked[-1] == 'dev'
    with pytest.raises(RuntimeError, match='EXPLICIT_AUTHORIZATION'):
        lab.run(args(tmp_path, '--phase', 'test'))
    with pytest.raises(RuntimeError, match='BLOCKED_BY_DEV_GATE'):
        lab.run(args(tmp_path, '--phase', 'test', '--authorize-test'))
    assert 'test' not in invoked


def test_checkpoint_resume_matches_uninterrupted_training(tmp_path):
    continuous = tmp_path / 'full'
    resumed = tmp_path / 'partial'
    lab.run(args(continuous, '--steps', '4'))
    lab.run(args(resumed, '--steps', '2'))
    lab.run(args(resumed, '--steps', '4', '--resume'))
    for arm in lab.ARMS:
        one = torch.load(continuous / 'arms' / arm / 'latest.pt', weights_only=True)
        two = torch.load(resumed / 'arms' / arm / 'latest.pt', weights_only=True)
        assert one['step'] == two['step'] == 4
        assert all(torch.equal(one['model'][k], two['model'][k]) for k in one['model'])
        assert one['best_step'] == two['best_step']
    assert json.loads((resumed / 'comparison.json').read_text())['TEST'] is None


def test_config_identity_prevents_quiet_domain_or_source_drift(tmp_path):
    lab.run(args(tmp_path))
    with pytest.raises(RuntimeError, match='CONFIG_DRIFT'):
        lab.run(args(tmp_path, '--resume', '--lr', '.002'))
    with pytest.raises(FileExistsError, match='OVERWRITE_REFUSED'):
        lab.run(args(tmp_path))


def test_dev_metric_requires_close_answers_in_all_six_groups():
    dev = lab.make_split('dev', 60, 20261024)
    truth = dev['targets']
    all_correct = lab._one_score(truth, truth, dev['strata'], dev['bands'], .05)
    assert all_correct['all']['rate'] == 1.0
    assert len([x for x in all_correct if x in {f'{s}_{b}' for s in lab.STRATA for b in lab.BANDS}]) == 6
    wrong_near = truth.clone()
    for i, category in enumerate(dev['strata']):
        if category == 'near':
            wrong_near[i] += 20000
    metric = lab._one_score(wrong_near, truth, dev['strata'], dev['bands'], .05)
    assert metric['near_id']['rate'] == 0.
    assert metric['near_ood']['rate'] == 0.
    assert metric['zero_ood']['rate'] == 1.
