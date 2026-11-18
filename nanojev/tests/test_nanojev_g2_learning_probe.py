"""CPU-only regression tests for the read-only validation learning probe."""
import importlib.util
import json
from pathlib import Path

import pytest


PROBE = Path(__file__).resolve().parents[1] / 'tools' / 'nanojev_g2_learning_probe.py'
spec = importlib.util.spec_from_file_location('nanojev_g2_learning_probe', PROBE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _metrics(tp, gold, pred, exact=0, graphs=10):
    return {'true_positive': tp, 'gold_edges': gold, 'predicted_edges': pred,
            'missing': gold - tp, 'extra': pred - tp, 'exact': exact,
            'exact_pairs': exact // 2, 'graphs': graphs}


def _write(d, step, m):
    (d / f'checkpoint-{step:09d}.json').write_text(
        json.dumps({'step': step, 'validation': m}), encoding='utf-8')


def test_untrained_capture_is_not_reported_as_learning(tmp_path):
    assert module.snapshot(tmp_path)['status'] == 'NO_COMMITTED_CHECKPOINTS'
    assert 'does not update weights' in module.format_snapshot(module.snapshot(tmp_path))


def test_step_zero_is_not_learning(tmp_path):
    _write(tmp_path, 0, _metrics(40, 100, 110))
    s = module.snapshot(tmp_path)
    assert s['status'] == 'STEP_ZERO_ONLY'
    assert 'no optimizer progress' in module.format_snapshot(s)


def test_real_validation_gain_and_best_selection(tmp_path):
    _write(tmp_path, 0, _metrics(40, 100, 110))
    _write(tmp_path, 5, _metrics(55, 100, 100))
    _write(tmp_path, 10, _metrics(60, 100, 105))
    s = module.snapshot(tmp_path)
    assert s['latest']['step'] == 10
    assert s['best']['step'] == 10
    assert module._signal(module._metrics(s['baseline']['validation']),
                          module._metrics(s['latest']['validation'])) == 'VALIDATION_LEARNING_SIGNAL'
    text = module.format_snapshot(s)
    assert 'VALIDATION_LEARNING_SIGNAL' in text
    assert 'complete graph reconstruction remains unsolved' in text


def test_false_positive_tradeoff_does_not_claim_learning(tmp_path):
    _write(tmp_path, 0, _metrics(40, 100, 80))
    _write(tmp_path, 3, _metrics(55, 100, 150))
    s = module.snapshot(tmp_path)
    assert module._signal(module._metrics(s['baseline']['validation']),
                          module._metrics(s['latest']['validation'])) != 'VALIDATION_LEARNING_SIGNAL'


def test_malformed_checkpoint_refused_without_write(tmp_path):
    p = tmp_path / 'checkpoint-000000000.json'
    p.write_text('{"step":0,"validation": {"graphs":9}}', encoding='utf-8')
    before = p.read_bytes()
    with pytest.raises(ValueError, match='missing or invalid'):
        module.snapshot(tmp_path)
    assert p.read_bytes() == before


def test_complete_selected_training_and_validation(tmp_path):
    m = _metrics(40, 100, 110)
    m['families'] = {'allocation': _metrics(20, 50, 60), 'encounter': _metrics(20, 50, 50)}
    m['modes'] = {'renaming': _metrics(20, 50, 60), 'role_swap': _metrics(20, 50, 50)}
    m['reachable_only'] = _metrics(35, 80, 85, graphs=8)
    _write(tmp_path, 0, m)
    _write(tmp_path, 2, {**m, **_metrics(60, 100, 105)})
    (tmp_path/'selection.json').write_text(json.dumps({
        'selected_step': 2, 'selected_checkpoint_reachable_training': _metrics(80, 100, 90)}),
        encoding='utf-8')
    s = module.snapshot(tmp_path)
    txt = module.format_snapshot(s)
    assert 'reachable TRAIN' in txt and 'reachable-only' in txt
    assert 'family allocation' in txt and 'mode renaming' in txt
