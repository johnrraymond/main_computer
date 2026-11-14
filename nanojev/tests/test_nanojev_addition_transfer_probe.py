"""Controlled transfer tests: source checkpoint, held-out pairs, deterministic CPU."""
import json

import pytest
import torch

from tools import nanojev_addition_curriculum as base
from tools import nanojev_addition_transfer_probe as lab


@pytest.fixture(autouse=True)
def single_thread():
    torch.set_num_threads(1)


def make_checkpoint(tmp_path):
    source = tmp_path / 'signed_passed.pt'
    model = base.AdditionNet('digit_tokens', 16, 4, 1, 1, seed=19)
    ident = {'schema': base.SCHEMA, 'source_sha256': base.sha256(base.__file__),
             'options': {'seed': 19, 'width': 16, 'heads': 4, 'layers': 1,
                         'rounds': 1, 'dev_per_group': 8}}
    base.write_torch(source, {'schema': base.SCHEMA + '.passed', 'arm': 'digit_tokens',
                              'stage': 'signed', 'identity': ident,
                              'model': model.state_dict(),
                              'result': {'gate': {'pass': True}}})
    return source


def args(source, out, *extra):
    return lab.parser().parse_args([
        '--signed-checkpoint', str(source), '--output', str(out),
        '--device', 'cpu', '--steps', '2', '--probe-every', '2',
        '--branches', 'positive_3digit', '--batch-size', '8',
        '--train-per-group', '8', '--dev-per-group', '8',
        '--replay-per-stage', '8', '--eval-batch-size', '64',
        '--cpu-threads', '1', *extra,
    ])


@pytest.mark.parametrize('branch', lab.BRANCHES)
def test_data_properties_and_all_splits(branch):
    datasets = {s: lab.make_dataset(branch, s, 20261024, 12)
                for s in ('train', 'dev', 'test')}
    keys = {}
    for split, data in datasets.items():
        assert set(data['groups']) == set(lab.GROUPS[branch])
        assert torch.equal(data['y'], data['x'].sum(-1))
        keys[split] = {lab.magnitude_pair(*pair) for pair in data['x'].tolist()}
        assert len(keys[split]) == len(data['y'])
        for (a, b), y, category in zip(data['x'].tolist(), data['y'].tolist(), data['groups']):
            assert base.pair_fold(*lab.magnitude_pair(a, b), 20261024) == split
            if branch.startswith('positive'):
                lo, hi = (100, 999) if '3digit' in branch else (1000, 9999)
                assert lo <= a <= hi and lo <= b <= hi
                assert lab.has_carry(a, b) == (category == 'carry')
            elif branch.startswith('opposite'):
                lo, hi = (100, 999) if '3digit' in branch else (1000, 9999)
                assert a * b < 0
                assert lo <= abs(a) <= hi and lo <= abs(b) <= hi
                assert abs(y) >= .25 * max(abs(a), abs(b))
                assert (y > 0) == (category == 'positive_result')
            elif branch == 'near_4digit':
                assert a * b < 0
                assert 1000 <= abs(a) <= 9999 and 1000 <= abs(b) <= 9999
                assert 0 < abs(y) <= max(abs(a), abs(b)) / 100
                assert (y > 0) == (category == 'positive_residual')
            else:
                assert y == 0
                assert a == -b
                assert (a > 0) == (category == 'positive_first')
    assert keys['train'].isdisjoint(keys['dev'])
    assert keys['train'].isdisjoint(keys['test'])
    assert keys['dev'].isdisjoint(keys['test'])


def test_all_branches_load_identical_signed_weights(tmp_path):
    source = make_checkpoint(tmp_path)
    ck, digest = lab.read_signed_checkpoint(source)
    assert digest == base.sha256(source)
    ident = ck['identity']['options']
    for branch in lab.BRANCHES:
        net = base.AdditionNet('digit_tokens', ident['width'], ident['heads'],
                               ident['layers'], ident['rounds'], ident['seed'])
        net.load_state_dict(ck['model'], strict=True)
        assert all(torch.equal(v, ck['model'][k]) for k, v in net.state_dict().items())


def test_source_refuses_unpassed_checkpoint_and_source_drift(tmp_path):
    source = make_checkpoint(tmp_path)
    ck = torch.load(source, weights_only=True)
    ck['result']['gate']['pass'] = False
    torch.save(ck, source)
    with pytest.raises(RuntimeError, match='SIGNED_PASSED'):
        lab.read_signed_checkpoint(source)
    ck['result']['gate']['pass'] = True
    ck['identity']['source_sha256'] = 'bad'
    torch.save(ck, source)
    with pytest.raises(RuntimeError, match='SOURCE_DRIFT'):
        lab.read_signed_checkpoint(source)


def test_nonzero_zero_prediction_fails_and_retention_is_strict():
    data = lab.make_dataset('near_4digit', 'dev', 20261024, 8)
    assert all(y != 0 for y in data['y'].tolist())
    metric = base.score(torch.zeros(len(data['y']), dtype=torch.long), data['y'], data['groups'])
    dev = {'branch': 'near_4digit', 'digit': metric}
    retention = {s: {'digit': {'all': {'rate': 1.}, 'cat': {'rate': 1.}}}
                 for s in lab.ORIGINAL_STAGES}
    assert not lab.gate(dev, retention, .8)['pass']
    dev['digit'] = base.score(data['y'], data['y'], data['groups'])
    assert lab.gate(dev, retention, .8)['pass']
    retention['signed']['digit']['cat']['rate'] = .7422
    assert not lab.gate(dev, retention, .8)['pass']
    retention['signed']['digit']['cat']['rate'] = 1.
    retention['atomic']['digit']['cat']['rate'] = .99
    assert not lab.gate(dev, retention, .8)['pass']


def test_cpu_run_preserves_checkpoint_and_does_not_open_test(tmp_path, monkeypatch):
    source = make_checkpoint(tmp_path)
    before = base.sha256(source)
    called = []
    real_make = lab.make_dataset
    def observe(branch, split, *rest):
        called.append(split)
        assert split != 'test'
        return real_make(branch, split, *rest)
    monkeypatch.setattr(lab, 'make_dataset', observe)
    out = tmp_path / 'run'
    result = lab.run(args(source, out, '--branches', 'positive_3digit,near_4digit'))
    assert set(result) == {'positive_3digit', 'near_4digit'}
    assert base.sha256(source) == before
    assert set(called) == {'train', 'dev'}
    assert json.loads((out / 'summary.json').read_text())['TEST'] is None
    for branch in result:
        root = out / 'branches' / branch
        assert (root / 'baseline.json').exists()
        assert (root / 'best.pt').exists()
        assert (root / 'latest.pt').exists()
        report = json.loads((root / 'best.json').read_text())
        assert all(stage in report['retention'] for stage in lab.ORIGINAL_STAGES)
        assert report['dev']['kind'] == 'held_out_magnitude_pairs'
    with pytest.raises(FileExistsError, match='OVERWRITE_REFUSED'):
        lab.run(args(source, out, '--branches', 'positive_3digit,near_4digit'))


def test_deterministic_resume_and_config_drift(tmp_path):
    source = make_checkpoint(tmp_path)
    full = tmp_path / 'full'
    part = tmp_path / 'part'
    lab.run(args(source, full, '--steps', '4'))
    lab.run(args(source, part, '--steps', '2'))
    lab.run(args(source, part, '--steps', '4', '--resume'))
    a = torch.load(full / 'branches' / 'positive_3digit' / 'latest.pt', weights_only=True)
    b = torch.load(part / 'branches' / 'positive_3digit' / 'latest.pt', weights_only=True)
    assert a['step'] == b['step'] == 4
    assert all(torch.equal(a['model'][k], b['model'][k]) for k in a['model'])
    with pytest.raises(RuntimeError, match='CONFIG_DRIFT'):
        lab.run(args(source, part, '--steps', '6', '--lr', '.002', '--resume'))


def test_result_is_predicted_not_external_addition():
    # Only predicted sign and predicted digits are read by the decoder.
    p = {'sign': torch.full((1, 3), -100.), 'digits': torch.full((1, 7, 10), -100.)}
    p['sign'][0, 2] = 100.
    for i, digit in enumerate((3, 2, 1, 0, 0)):
        p['digits'][0, i, digit] = 100.
    assert base.decode(p, 5).item() == 123
    assert lab.OUTPUT_DIGITS == 5


@pytest.mark.skipif(not torch.cuda.is_available(), reason='CUDA unavailable')
def test_new_decoder_cuda_matches_cpu():
    torch.manual_seed(12)
    p = {'sign': torch.randn(8, 3), 'digits': torch.randn(8, 7, 10)}
    assert torch.equal(base.decode(p, 5), base.decode({k: v.cuda() for k, v in p.items()}, 5).cpu())
