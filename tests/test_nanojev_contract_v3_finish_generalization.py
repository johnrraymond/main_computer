"""Non-model tests of the resumable, fail-closed original-shard continuation."""
import importlib.util
import json
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / 'tools/nanojev_contract_v3_finish_generalization.py'
spec = importlib.util.spec_from_file_location('finish_gen', MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def report(index=0, correct=3, n=5):
    return {'schema': mod.REPORT_SCHEMA, 'mode': 'live', 'model': mod.MODEL,
            'corpus_sha256': mod.CORPUS_SHA256,
            'no_assisted_retries': True, 'native_nanojev_model_used': False,
            'independent_expert_gold': False, 'forge_requested': False,
            'training_or_improvement_evidence': False,
            'shard': {'index': index, 'count': 8, 'selected': n},
            'score': {'correct': correct, 'evaluated': n, 'selected': n},
            'cases': [{'id': f'case{i}', 'correct': i < correct, 'model_invoked': True}
                      for i in range(n)]}


def test_valid_existing_baseline(tmp_path):
    p = tmp_path / 'shard-0.json'
    p.write_text(json.dumps(report()))
    assert mod.inspect_report(p, 0, original=True)['score']['correct'] == 3


@pytest.mark.parametrize('change', [
    lambda x: x['score'].update(evaluated=4),
    lambda x: x.update(mode='replay'),
    lambda x: x.update(corpus_sha256='tampered'),
    lambda x: x.update(no_assisted_retries=False),
    lambda x: x['cases'][0].update(model_invoked=False),
    lambda x: x['score'].update(correct=4),
])
def test_bad_existing_shard_is_rejected(tmp_path, change):
    p = tmp_path / 'shard-0.json'
    obj = report()
    change(obj)
    p.write_text(json.dumps(obj))
    with pytest.raises(mod.ContinuationError):
        mod.inspect_report(p, 0, original=True)


def test_other_complete_shard_can_be_reused(tmp_path):
    p = tmp_path / 'shard-3.json'
    p.write_text(json.dumps(report(index=3, correct=0)))
    assert mod.inspect_report(p, 3)['score']['correct'] == 0


def test_missing_shard_is_not_fabricated(tmp_path):
    with pytest.raises(mod.ContinuationError):
        mod.inspect_report(tmp_path / 'shard-1.json', 1)


def test_seven_live_shards_then_merge_without_rerunning_baseline(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    base = tmp_path / 'reports'
    base.mkdir()
    corpus = tmp_path / 'manifest.json'
    corpus.write_bytes(b'fixed corpus bytes')
    monkeypatch.setattr(mod, 'DEFAULT_CORPUS', corpus)
    monkeypatch.setattr(mod, 'CORPUS_SHA256', hashlib.sha256(corpus.read_bytes()).hexdigest())
    (base / 'shard-0.json').write_text(json.dumps(report(0, 3, 5)))
    runs = []

    def fake_run(command):
        runs.append(command)
        if command[2] == 'tools.nanojev_contract_v3_generalization_eval':
            idx = int(command[command.index('--shard-index') + 1])
            path = Path(command[command.index('--output') + 1])
            path.write_text(json.dumps(report(idx, 2, 5)))
        else:
            assert command[2] == 'tools.nanojev_contract_v3_generalization_merge'
            path = Path(command[command.index('--output') + 1])
            path.write_text(json.dumps({
                'status': 'MEASURED_LIVE_FULL_CORPUS_NOT_CERTIFIED',
                'corpus_sha256': mod.CORPUS_SHA256, 'mode': 'live',
                'score': {'selected': 40, 'evaluated': 40, 'correct': 17,
                          'accuracy': 17 / 40, 'correct_positive': 15,
                          'correct_refusals': 2, 'incorrect_contract_ir': 8,
                          'wrong_reason_refusals': 4}}))
        return SimpleNamespace(returncode=0)

    assert mod.main(['--report-dir', str(base)], run=fake_run) == 0
    assert len(runs) == 8
    assert sum(cmd[2] == 'tools.nanojev_contract_v3_generalization_eval' for cmd in runs) == 7
    assert (base / 'shard-0.json').exists()
    assert (base / 'full-40.json').exists()
    assert '3' in (base / 'shard-0.json').read_text()


def test_existing_workspace_stops_before_any_inference(tmp_path, monkeypatch):
    import hashlib
    base = tmp_path / 'reports'
    base.mkdir()
    (base / 'shard-0.json').write_text(json.dumps(report(0, 3, 5)))
    (base / 'shard-2.cases').mkdir()
    corpus = tmp_path / 'manifest.json'
    corpus.write_bytes(b'fixed corpus bytes')
    monkeypatch.setattr(mod, 'DEFAULT_CORPUS', corpus)
    monkeypatch.setattr(mod, 'CORPUS_SHA256', hashlib.sha256(corpus.read_bytes()).hexdigest())
    (base / 'shard-0.json').write_text(json.dumps(report(0, 3, 5)))
    called = []

    def fake_run(command):
        called.append(command)
        raise AssertionError('Must not start model')

    with pytest.raises(mod.ContinuationError, match='workspace exists'):
        mod.main(['--report-dir', str(base)], run=fake_run)
    assert not called
