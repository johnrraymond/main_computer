"""Read-only G2 learning probe over committed checkpoint validation receipts.

Run from main_computer/nanojev. Works alongside a running capture or training process:
no imports of torch, no CUDA, no model loads, and no filesystem mutations.
Only train/validation statistics already committed by the experiment are read.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime
from pathlib import Path

DEFAULT_TRAINING_DIR = Path('..') / 'runtime' / 'reports' / 'gamer-v1' / 'graph-g2-0061-training'


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'Cannot read committed receipt {path}: {exc}') from exc


def _checkpoint_history(directory: Path):
    """Checkpoints are visible only after the trainer atomically commits a JSON receipt."""
    result = []
    for path in directory.glob('checkpoint-*.json'):
        # Ignore malformed filenames, not-yet-committed temporary files and other output.
        if len(path.stem) != len('checkpoint-000000000') or not path.stem.startswith('checkpoint-'):
            continue
        suffix = path.stem[len('checkpoint-'):]
        if not suffix.isascii() or not suffix.isdecimal():
            continue
        entry = _load_json(path)
        if entry.get('step') != int(suffix) or not isinstance(entry.get('validation'), dict):
            raise ValueError(f'Invalid committed checkpoint receipt: {path}')
        _metrics(entry['validation'])
        result.append(entry)
    result.sort(key=lambda v: v['step'])
    return result


def _metrics(m):
    required = ('graphs', 'exact', 'true_positive', 'gold_edges', 'predicted_edges',
                'missing', 'extra')
    if any(type(m.get(k)) is not int or m[k] < 0 for k in required):
        raise ValueError('Checkpoint validation metrics missing or invalid')
    tp = m['true_positive']; gold = m['gold_edges']; predicted = m['predicted_edges']
    if tp > gold or tp > predicted or m['exact'] > m['graphs']:
        raise ValueError('Checkpoint validation counters are inconsistent')
    precision = tp / predicted if predicted else 0.0
    recall = tp / gold if gold else 0.0
    f1 = 2 * tp / (gold + predicted) if (gold + predicted) else 0.0
    return {'precision': precision, 'recall': recall, 'f1': f1,
            'errors': m['missing'] + m['extra'], 'exact': m['exact'],
            'graphs': m['graphs'], 'missing': m['missing'], 'extra': m['extra']}


def _quality(rec):
    # Same ordering as existing G2-0059/G2-0061 checkpoint selection.
    m = rec['validation']
    return (m['exact'], -m['missing'] - m['extra'], m.get('exact_pairs', 0),
            -m['missing'], -rec['step'])


def _fmt_rate(v):
    return f'{100*v:6.2f}%'


def _metric_changes(base, now):
    return {'delta_precision_pp': (now['precision'] - base['precision']) * 100,
            'delta_recall_pp': (now['recall'] - base['recall']) * 100,
            'delta_f1_pp': (now['f1'] - base['f1']) * 100,
            'delta_errors': now['errors'] - base['errors'],
            'delta_exact': now['exact'] - base['exact']}


def _signal(base, current):
    d = _metric_changes(base, current)
    # A change must improve both F1 and total error burden to demonstrate
    # better prediction on the held-out *validation* examples.
    if d['delta_f1_pp'] > 0 and d['delta_errors'] < 0:
        return 'VALIDATION_LEARNING_SIGNAL'
    if d['delta_f1_pp'] < 0 and d['delta_errors'] > 0:
        return 'VALIDATION_REGRESSION'
    if d['delta_f1_pp'] == 0 and d['delta_errors'] == 0:
        return 'NO_VALIDATION_CHANGE'
    return 'MIXED_VALIDATION_CHANGE'


def snapshot(directory: Path):
    if not directory.is_dir():
        return {'status': 'NO_TRAINING_DIRECTORY', 'directory': str(directory)}
    history = _checkpoint_history(directory)
    if not history:
        return {'status': 'NO_COMMITTED_CHECKPOINTS', 'directory': str(directory),
                'training_config_present': (directory / 'training.json').is_file()}
    base = next((r for r in history if r['step'] == 0), None)
    if base is None:
        return {'status': 'NO_STEP_ZERO_BASELINE', 'directory': str(directory),
                'checkpoints': len(history)}
    latest = history[-1]
    best = max(history, key=_quality)
    result = {'status': 'STEP_ZERO_ONLY' if latest['step'] == 0 else 'HAS_OPTIMIZER_CHECKPOINTS',
              'directory': str(directory), 'baseline': base, 'latest': latest,
              'best': best, 'history': history,
              'selection': None}
    selection_file = directory / 'selection.json'
    if selection_file.is_file():
        sel = _load_json(selection_file)
        result['selection'] = {'selected_step': sel.get('selected_step'),
                               'status': sel.get('status'),
                               'training': sel.get('selected_checkpoint_reachable_training')}
    return result


def format_snapshot(s, *, history_limit=8):
    stamp = datetime.now().strftime('%H:%M:%S')
    lines = [f'[{stamp}] G2 LEARNING PROBE — {s["directory"]}']
    status = s['status']
    if status == 'NO_TRAINING_DIRECTORY':
        return '\n'.join(lines + ['NO TRAINING RUN FOUND. The running learn-capture command only creates data; it does not train weights.',
                                   'Pass --training-dir to inspect a different completed or active learning run.'])
    if status == 'NO_COMMITTED_CHECKPOINTS':
        return '\n'.join(lines + [
            ('Training configuration exists but no optimizer checkpoint is committed yet.'
             if s['training_config_present'] else
             'No training receipts found. learn-capture does not update weights.'),
            'Cannot measure learning until checkpoint-000000000.json and a later checkpoint exist.'])
    if status == 'NO_STEP_ZERO_BASELINE':
        return '\n'.join(lines + ['No step-zero baseline receipt; refusing to call this learning.'])
    base = _metrics(s['baseline']['validation'])
    latest = _metrics(s['latest']['validation'])
    best = _metrics(s['best']['validation'])
    lines.extend([
        'Validation is the same frozen cohort across these checkpoints; sealed test/challenge not accessed.',
        'Step       Precision   Recall     F1       Missing  Extra   Exact',
    ])
    history = s['history']
    display = history if len(history) <= history_limit else [history[0], *history[-(history_limit - 1):]]
    for rec in display:
        if len(history) > history_limit and rec is display[1]:
            lines.append(f'  ... ({len(history) - len(display)} intervening checkpoint(s) omitted)')
        m = _metrics(rec['validation'])
        lines.append(f'{rec["step"]:>8}   {_fmt_rate(m["precision"])}  {_fmt_rate(m["recall"])}  '
                     f'{_fmt_rate(m["f1"])}  {m["missing"]:>7}  {m["extra"]:>5}  '
                     f'{m["exact"]}/{m["graphs"]}')
    if status == 'STEP_ZERO_ONLY':
        lines.append('STATUS: STEP_ZERO_ONLY — baseline established, but no optimizer progress is yet measurable.')
        return '\n'.join(lines)
    d = _metric_changes(base, latest)
    label = _signal(base, latest)
    lines.extend([
        f'STATUS: {label} | latest step {s["latest"]["step"]} vs step 0',
        f'Change: precision {d["delta_precision_pp"]:+.2f} pp, recall {d["delta_recall_pp"]:+.2f} pp, '
        f'F1 {d["delta_f1_pp"]:+.2f} pp, total edge errors {d["delta_errors"]:+,}, '
        f'exact graphs {d["delta_exact"]:+d}.',
        f'Best committed checkpoint by experiment selection rule: step {s["best"]["step"]} '
        f'(F1 {_fmt_rate(best["f1"])}, exact {best["exact"]}/{best["graphs"]}).',
    ])
    for kind in ('families', 'modes'):
        a = s['baseline']['validation'].get(kind, {})
        b = s['latest']['validation'].get(kind, {})
        if isinstance(a, dict) and isinstance(b, dict):
            for name in sorted(a.keys() & b.keys()):
                am, bm = _metrics(a[name]), _metrics(b[name])
                lines.append(f'  {"family" if kind == "families" else "mode"} {name}: F1 {_fmt_rate(am["f1"])} -> '
                             f'{_fmt_rate(bm["f1"])}; exact {bm["exact"]}/{bm["graphs"]}')
    # The experiment selects on full validation; reachable-only has its own denominator.
    reach = s['latest']['validation'].get('reachable_only')
    if isinstance(reach, dict):
        rm = _metrics(reach)
        lines.append(f'  Latest reachable-only: F1 {_fmt_rate(rm["f1"])}, '
                     f'exact {rm["exact"]}/{rm["graphs"]}.')
    selection = s['selection']
    if selection and isinstance(selection.get('training'), dict):
        tr = _metrics(selection['training'])
        lines.append(f'  Selected checkpoint reachable TRAIN: F1 {_fmt_rate(tr["f1"])}, '
                     f'exact {tr["exact"]}/{tr["graphs"]}. Compare with validation for memorization risk.')
    if latest['exact'] == 0:
        lines.append('Interpretation: individual edge prediction may improve; complete graph reconstruction remains unsolved.')
    lines.append('No score can be inferred between committed checkpoints; capture itself does not train.')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--training-dir', type=Path, default=DEFAULT_TRAINING_DIR,
                        help='Directory containing checkpoint-*.json receipts')
    parser.add_argument('--watch', action='store_true', help='Refresh while separate training runs')
    parser.add_argument('--interval', type=float, default=30.0, help='Watch refresh in seconds')
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error('--interval must be a positive finite number')
    directory = args.training_dir.resolve()
    print('Read-only receipt analysis. No torch, CUDA, weight loads, mutations, or test labels.', flush=True)
    last_signature = None
    try:
        while True:
            try:
                s = snapshot(directory)
                signature = (s['status'], s.get('latest', {}).get('step'),
                             s.get('selection', {}).get('selected_step') if s.get('selection') else None)
                if signature != last_signature or not args.watch:
                    print(format_snapshot(s), flush=True)
                    last_signature = signature
                else:
                    print(f'[{datetime.now().strftime("%H:%M:%S")}] No new committed optimizer checkpoint; '
                          'this does not establish that training has stopped.', flush=True)
            except (ValueError, OSError) as exc:
                print(f'PROBE_READ_ERROR: {exc}. Existing experiment untouched.', flush=True)
            if not args.watch:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\nLearning probe stopped; experiment unaffected.', flush=True)


if __name__ == '__main__':
    main()
