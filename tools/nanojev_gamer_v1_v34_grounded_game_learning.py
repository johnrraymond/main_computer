#!/usr/bin/env python3
"""V34: learned Hawk/Dove choice from audited, raw-numeric prompt grounding.

V33's frozen-Qwen pooled-vector classifier failed DEV (47/90). V34 does NOT
claim a successful free-form Qwen game learner: it provides an explicit grammar
for the four existing payoff displays, reads raw R/S/T/P/q, and trains a NEW
neural choice head from TRAIN labels. No payoff difference, expected reward,
optimal action, or oracle-derived quantity enters inference. The five untouched
V33 holdout surface IDs remain sealed until the distinct V34 DEV gate passes.
No production gameplay or tactical-v6 path is modified.
"""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import random
import tempfile

import numpy as np

from tools import nanojev_gamer_v1_v33_game_learning as v33

SCHEMA = 'nanojev.gamer.v1.hawk-dove-grounded-v34'
HEAD_SCHEMA = 'nanojev.gamer.v1.grounded-action-head.v34'
REPORT_SCHEMA = 'nanojev.gamer.v1.grounded-action-audit.v34'
CHOICES = ('Yield', 'Contest')
DEV_MIN = 72
DEV_PAIR_MIN = 36
ASYM_DEV_MIN = 270   # of 300 independently varied payoff outcomes
ASYM_DEV_PAIR_MIN = 135  # of 150 paired probabilities
TEST_MIN = 96
TEST_PAIR_MIN = 48
ASYM_TEST_MIN = 180  # of 200 independent holdout outcomes
ASYM_TEST_PAIR_MIN = 90  # of 100 independent heldout payoff pairs


def require(condition, code):
    if not condition:
        raise ValueError('GAMER_V34_' + code)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def hashbytes(value):
    return hashlib.sha256(value).hexdigest()


def hashfile(path):
    return hashbytes(Path(path).read_bytes())


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_new(path, obj):
    # Atomic no-overwrite, including when a prior run crashed mid-audit.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    require(not path.exists(), 'REFUSE_OVERWRITE_' + path.name)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8',
            prefix='.v34-', dir=path.parent, delete=False) as handle:
        json.dump(obj, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write('\n')
        temp = Path(handle.name)
    try:
        os.link(temp, path)
    finally:
        temp.unlink(missing_ok=True)


# These patterns read the four V33 payoff renderings from the PROMPT only.
# Neither the labelled deck row nor exact_teacher is consulted for features.
_VAL = r'(-?\d{1,5})'
_TABLE_PATTERNS = (
    (re.compile(r'\bCC\s*=\s*'+_VAL+r'\s*[,;]\s*CD\s*=\s*'+_VAL+
                r'\s*[,;]\s*DC\s*=\s*'+_VAL+r'\s*[,;]\s*DD\s*=\s*'+_VAL, re.I),
     ('R', 'S', 'T', 'P')),
    (re.compile(r'\bDD\s*=\s*'+_VAL+r'\s*[,;]\s*DC\s*=\s*'+_VAL+
                r'\s*[,;]\s*CD\s*=\s*'+_VAL+r'\s*[,;]\s*CC\s*=\s*'+_VAL, re.I),
     ('P', 'T', 'S', 'R')),
    (re.compile(r'When we Yield and they Yield:\s*'+_VAL+
                r'\s*;\s*when we Yield and they Contest:\s*'+_VAL+
                r'\s*;\s*when we Contest and they Yield:\s*'+_VAL+
                r'\s*;\s*when both Contest:\s*'+_VAL, re.I),
     ('R', 'S', 'T', 'P')),
    (re.compile(r'\(our D, other C\)\s*=\s*'+_VAL+
                r'\s*;\s*\(our C, other D\)\s*=\s*'+_VAL+
                r'\s*;\s*\(our D, other D\)\s*=\s*'+_VAL+
                r'\s*;\s*\(our C, other C\)\s*=\s*'+_VAL, re.I),
     ('T', 'S', 'P', 'R')),
)
# The smoke scenario uses another common, fully named payoff format.
_Q = re.compile(r'(?<![\d/])([0-9]{1,4}/[0-9]{1,4})(?![\d/])')


def ground(prompt):
    """Read only observed payoff amounts and belief, never a decision label.

    Unknown layouts, ambiguous fractions, duplicate tables or missing fields
    fail closed. This is an intentionally BOUNDED parser, not free-form NLU.
    """
    require(isinstance(prompt, str) and 0 < len(prompt) <= 4000,
            'INVALID_PROMPT')
    recognized = []
    for pattern, order in _TABLE_PATTERNS:
        matches = list(pattern.finditer(prompt))
        for match in matches:
            values = dict(zip(order, map(int, match.groups())))
            recognized.append(values)
    require(len(recognized) == 1, 'PAYOFF_FORMAT_UNSUPPORTED_OR_AMBIGUOUS')
    values = recognized[0]
    require(all(-1000 <= v <= 1000 for v in values.values()),
            'PAYOFF_OUT_OF_RANGE')
    qmatch = _Q.findall(prompt)
    require(len(qmatch) == 1, 'PROBABILITY_MISSING_OR_AMBIGUOUS')
    try:
        q = Fraction(qmatch[0])
    except (ValueError, ZeroDivisionError):
        require(False, 'INVALID_PROBABILITY')
    require(0 <= q <= 1, 'INVALID_PROBABILITY')
    # No expected payoff, action-value difference, sign, or target label here.
    raw = [values[key] for key in ('R', 'S', 'T', 'P')]
    return np.asarray([raw[0] / 20., raw[1] / 20., raw[2] / 20.,
                       raw[3] / 20., float((q - Fraction(1, 2)) * 2)],
                      dtype=np.float32)


def features(rows):
    # Deliberately accept prompt strings, not rows with ground-truth metadata.
    require(bool(rows), 'EMPTY_FEATURE_INPUT')
    return np.stack([ground(prompt) for prompt in rows], axis=0)



def asymmetric_deck(split, seed):
    """Independent payoff advantages: reversals AND non-reversals.

    Merely recognizing the sign of (T-R) and whether q > 1/2 is not enough:
    at least one third of these games deliberately keep the best action
    unchanged when q changes. Their independently varying magnitudes matter.
    Holdout is generated only inside the one-shot sealed audit.
    """
    require(split in ('fit', 'holdout'), 'INVALID_ASYMMETRIC_SPLIT')
    variants = v33.TRAIN_VARIANTS if split == 'fit' else v33.TEST_VARIANTS
    rows = []
    n_pairs = 40 if split == 'fit' else 20
    for variant in variants:
        rng = random.Random(f'v34-general-{seed}-{variant}-{split}')
        for pair in range(n_pairs):
            R, S = rng.randrange(-12, 12), rng.randrange(-12, 12)
            a, b = rng.randrange(4, 11), rng.randrange(4, 11)
            direction = 1 if rng.randrange(2) else -1
            if pair % 3 == 0:
                # Probabilities 1/4 vs 3/4 REVERSE the best action.
                T, P = R + direction*a, S - direction*b
                family = 'reversal'
            elif pair % 3 == 1:
                # Both advantages have the same sign. Changing q DOES NOT
                # reverse the better action.
                T, P = R + direction*a, S + direction*b
                family = 'non_reversal'
            else:
                # Unconstrained payoffs, and independent contrasts.
                while True:
                    R, S, T, P = [rng.randint(-12, 12) for _ in range(4)]
                    if all(q * (T - R) + (1-q) * (P - S) != 0
                           for q in (Fraction(1, 4), Fraction(3, 4))):
                        break
                family = 'independent'
            pair_id = f'general.v{variant:02}.pair{pair:02}'
            for q in (Fraction(1, 4), Fraction(3, 4)):
                prompt = ' '.join((v33.CONTEXTS[variant // 4],
                    v33.TABLES[variant % 4].format(R=R, S=S, T=T, P=P),
                    v33.QUESTIONS[variant // 4].format(q=str(q))))
                rows.append({'id': f'{split}.{pair_id}.q{q.numerator}',
                             'pair': pair_id, 'variant': variant,
                             'prompt': prompt,
                             'label': v33.exact_teacher(R, S, T, P, q),
                             'family': family})
    require(len(rows) == len(variants)*n_pairs*2 and
            len({r['id'] for r in rows}) == len(rows) and
            len({r['prompt'] for r in rows}) == len(rows),
            'ASYMMETRIC_DATASET_INTEGRITY')
    for i in range(0, len(rows), 2):
        a,b = rows[i:i+2]
        require(a['pair'] == b['pair'], 'ASYMMETRIC_PAIR_INCOMPLETE')
        if a['family'] == 'reversal':
            require(a['label'] != b['label'], 'ASYMMETRIC_REVERSAL_NOT_REVERSED')
        if a['family'] == 'non_reversal':
            require(a['label'] == b['label'], 'ASYMMETRIC_NONREVERSAL_CHANGED')
    return rows


def asymmetric_partition(rows):
    fit = [r for r in rows if int(r['pair'].split('pair')[1]) < 30]
    dev = [r for r in rows if int(r['pair'].split('pair')[1]) >= 30]
    require(len(fit) == 900 and len(dev) == 300 and
            not ({r['pair'] for r in fit} & {r['pair'] for r in dev}),
            'ASYMMETRIC_TRAIN_DEV_LEAK')
    return fit, dev


def asy_counts(rows, pred):
    return sum(r['label'] == p for r,p in zip(rows, pred)), len(rows)


def head(torch):
    return torch.nn.Sequential(
        torch.nn.Linear(5, 32), torch.nn.GELU(),
        torch.nn.Linear(32, 32), torch.nn.GELU(),
        torch.nn.Linear(32, 2))


def train_head(train_x, train_y, dev_x, dev_y, *, seed=33, epochs=160):
    import torch
    require(train_x.shape == (1170, 5) and dev_x.shape == (390, 5)
            and len(train_y) == 1170 and len(dev_y) == 390 and epochs > 0,
            'INVALID_FIT_CONFIG')
    require(np.isfinite(train_x).all() and np.isfinite(dev_x).all(),
            'NONFINITE_FEATURES')
    # Tiny learned head: CPU makes the training deterministic across CUDA hosts.
    torch.manual_seed(seed)
    net = head(torch)
    opt = torch.optim.AdamW(net.parameters(), lr=.003, weight_decay=.01)
    x = torch.from_numpy(train_x.copy()).float()
    y = torch.tensor(train_y, dtype=torch.long)
    d = torch.from_numpy(dev_x.copy()).float()
    dy = torch.tensor(dev_y, dtype=torch.long)
    best, best_epoch, best_state, best_dev_pred = -1, 0, None, None
    history = []
    for epoch in range(1, epochs + 1):
        net.train()
        generator = torch.Generator().manual_seed(seed * 10000 + epoch)
        for indices in torch.randperm(len(y), generator=generator).split(32):
            opt.zero_grad(set_to_none=True)
            loss = torch.nn.functional.cross_entropy(net(x[indices]), y[indices])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), 2.0)
            opt.step()
        net.eval()
        with torch.inference_mode():
            pred = net(d).argmax(-1).tolist()
            n = sum(a == b for a, b in zip(pred, dev_y))
        history.append({'epoch': epoch, 'dev_exact': n, 'dev_total': 390,
                        'original_dev_exact': sum(a == b for a,b in zip(pred[:90],dev_y[:90])),
                        'asymmetric_dev_exact': sum(a == b for a,b in zip(pred[90:],dev_y[90:]))})
        if epoch % 20 == 0 or epoch == epochs:
            print(f'GAMER_V34_TRAIN_EPOCH {epoch}/{epochs} dev_exact={n}/390' , flush=True)
        if n > best:
            best, best_epoch = n, epoch
            best_state = {k: tensor.detach().cpu().clone()
                          for k, tensor in net.state_dict().items()}
            best_dev_pred = pred
    return best_state, best_epoch, history, best_dev_pred


def predict(net, x):
    import torch
    with torch.inference_mode():
        result = net(torch.from_numpy(x.copy()).float()).argmax(-1).tolist()
    return [int(n) for n in result]


def test_pairs(rows, predictions):
    return v33.paired_accuracy(rows, predictions)

def reversal_pair_stats(rows, predictions):
    by_pair = {}
    for row, pred in zip(rows, predictions):
        by_pair.setdefault(row['pair'], []).append((row['label'], pred))
    require(all(len(pair) == 2 for pair in by_pair.values()),
            'INCOMPLETE_PAIR_STATS')
    reversal = [p for p in by_pair.values() if p[0][0] != p[1][0]]
    exact = sum(all(truth == guess for truth,guess in pair) for pair in reversal)
    return int(exact), len(reversal)



def checkpoint(root):
    return Path(root) / 'grounded_head.pt'


def train(root, *, project='.', seed=33, epochs=160):
    import torch
    root = Path(root)
    require(not root.exists(), 'OUTPUT_ALREADY_EXISTS')
    require(seed >= 0 and epochs > 0, 'INVALID_TRAIN_ARGUMENTS')
    lineage = v33.activation(project)
    original_train, original_dev = v33.fit_partition(v33.make_deck('fit', seed))
    general_train, general_dev = asymmetric_partition(asymmetric_deck('fit', seed))
    train_rows = original_train + general_train
    dev_rows = original_dev + general_dev
    # Entire held-out family is not generated or scored in train.
    x = features([r['prompt'] for r in train_rows])
    d = features([r['prompt'] for r in dev_rows])
    root.mkdir(parents=True, exist_ok=False)
    state, epoch, history, pred = train_head(
        x, [r['label'] for r in train_rows],
        d, [r['label'] for r in dev_rows], seed=seed, epochs=epochs)
    dev_exact = history[epoch - 1]['dev_exact']
    orig_dev_exact = history[epoch - 1]['original_dev_exact']
    asym_dev_exact = history[epoch - 1]['asymmetric_dev_exact']
    orig_dev_pairs, orig_dev_pairs_total = test_pairs(original_dev, pred[:90])
    asym_dev_pairs, asym_dev_pairs_total = test_pairs(general_dev, pred[90:])
    dev_pass = (orig_dev_exact >= DEV_MIN and orig_dev_pairs >= DEV_PAIR_MIN
                and asym_dev_exact >= ASYM_DEV_MIN
                and asym_dev_pairs >= ASYM_DEV_PAIR_MIN)
    v33.verify_activation(project, lineage)
    manifest = {
        'schema': SCHEMA, 'status': 'TRAIN_FITTED_NO_TEST_NO_PROMOTION',
        'head_schema': HEAD_SCHEMA, 'seed': seed, 'epochs': epochs,
        'best_epoch': epoch, 'dev_exact': dev_exact,
        'original_dev_exact': orig_dev_exact,
        'asymmetric_dev_exact': asym_dev_exact,
        'original_dev_pairs_exact': orig_dev_pairs,
        'asymmetric_dev_pairs_exact': asym_dev_pairs,
        'dev_pass': dev_pass, 'train_count': len(train_rows),
        'dev_count': len(dev_rows),
        'train_variants': list(v33.TRAIN_VARIANTS),
        'sealed_holdout_variants': list(v33.TEST_VARIANTS),
        'train_deck_sha256': hashbytes(canonical(train_rows).encode()),
        'dev_deck_sha256': hashbytes(canonical(dev_rows).encode()),
        'asymmetric_training_added': True,
        'grounding': 'prompt-only-raw-R-S-T-P-q',
        'head_training_device': 'cpu', 'head_only': True, 'oracle_inference': False,
        'frozen_qwen_feature_used': False,
        'lineage': lineage, 'production_modified': False,
        'tactical_v6_authority_changed': False,
    }
    torch.save({'schema': HEAD_SCHEMA, 'manifest_sha256':
                hashbytes(canonical(manifest).encode()),
                'state': state}, checkpoint(root))
    write_new(root / 'train_manifest.json', manifest)
    write_new(root / 'train_history.json', {'history': history})
    result = {'status': 'TRAIN_FITTED_NO_TEST_NO_PROMOTION',
              'best_dev_exact': dev_exact, 'dev_total': len(dev_rows),
              'original_dev_exact': orig_dev_exact, 'original_dev_total': 90,
              'asymmetric_dev_exact': asym_dev_exact, 'asymmetric_dev_total': 300,
              'original_dev_pairs_exact': orig_dev_pairs, 'original_dev_pairs_total': orig_dev_pairs_total,
              'asymmetric_dev_pairs_exact': asym_dev_pairs, 'asymmetric_dev_pairs_total': asym_dev_pairs_total,
              'dev_pass': dev_pass, 'holdout_evaluated': False,
              'holdout_variants_unopened': len(v33.TEST_VARIANTS),
              'checkpoint_sha256': hashfile(checkpoint(root)),
              'output': str(root), 'training_device': 'cpu'}
    write_new(root / 'train_result.json', result)
    return result


def load_head(root, project, *, require_audit=False):
    import torch
    root = Path(root)
    manifest = read(root / 'train_manifest.json')
    require(manifest.get('schema') == SCHEMA and
            manifest.get('head_schema') == HEAD_SCHEMA and
            manifest.get('status') == 'TRAIN_FITTED_NO_TEST_NO_PROMOTION' and
            manifest.get('grounding') == 'prompt-only-raw-R-S-T-P-q' and
            manifest.get('head_only') is True and
            manifest.get('oracle_inference') is False and
            manifest.get('frozen_qwen_feature_used') is False and
            manifest.get('asymmetric_training_added') is True and
            manifest.get('train_variants') == list(v33.TRAIN_VARIANTS) and
            manifest.get('sealed_holdout_variants') == list(v33.TEST_VARIANTS),
            'MANIFEST_INCOMPATIBLE')
    v33.verify_activation(project, manifest['lineage'])
    original_train, original_dev = v33.fit_partition(v33.make_deck('fit', manifest['seed']))
    general_train, general_dev = asymmetric_partition(asymmetric_deck('fit', manifest['seed']))
    train_rows = original_train + general_train
    dev_rows = original_dev + general_dev
    require(manifest['train_deck_sha256'] == hashbytes(canonical(train_rows).encode())
            and manifest['dev_deck_sha256'] == hashbytes(canonical(dev_rows).encode())
            and manifest['train_count'] == 1170 and manifest['dev_count'] == 390,
            'TRAIN_PROVENANCE_CHANGED')
    receipt = read(root / 'train_result.json')
    require(hashfile(checkpoint(root)) == receipt.get('checkpoint_sha256'),
            'HEAD_CHECKPOINT_CHANGED')
    saved = torch.load(checkpoint(root), weights_only=True, map_location='cpu')
    require(saved['schema'] == HEAD_SCHEMA and
            saved['manifest_sha256'] == hashbytes(canonical(manifest).encode()),
            'HEAD_MANIFEST_MISMATCH')
    model = head(torch)
    model.load_state_dict(saved['state'], strict=True)
    model.eval()
    if require_audit:
        report = read(root / 'holdout_acceptance.json')
        require(report.get('status') == 'PASS_SEALED_HOLDOUT_SHADOW_ONLY' and
                report.get('heldout_pass') is True, 'HOLDOUT_NOT_CERTIFIED')
        ledger_path = root / 'holdout_predictions.json'
        require(ledger_path.exists() and
                hashfile(ledger_path) == report.get('predictions_sha256'),
                'HOLDOUT_LEDGER_CHANGED')
        expected_orig = v33.make_deck('holdout', manifest['seed'])
        expected_general = asymmetric_deck('holdout', manifest['seed'])
        expected = expected_orig + expected_general
        ledger = read(ledger_path)
        require(isinstance(ledger, list) and len(ledger) == len(expected),
                'HOLDOUT_LEDGER_INCOMPLETE')
        pred = []
        for rec, row in zip(ledger, expected):
            require(isinstance(rec, dict) and rec.get('id') == row['id'] and
                    rec.get('prompt_sha256') == hashbytes(row['prompt'].encode())
                    and rec.get('truth') == row['label'] and
                    type(rec.get('prediction')) is int and
                    rec['prediction'] in (0, 1), 'HOLDOUT_LEDGER_INVALID')
            pred.append(rec['prediction'])
        exact = sum(a == b['label'] for a, b in zip(pred, expected))
        orig_exact = sum(a == b['label'] for a,b in zip(pred[:120], expected_orig))
        general_exact = sum(a == b['label'] for a,b in zip(pred[120:], expected_general))
        pair_exact, pair_total = test_pairs(expected, pred)
        reversal_exact, reversal_total = reversal_pair_stats(expected, pred)
        orig_pairs, _ = test_pairs(expected_orig, pred[:120])
        general_pairs, _ = test_pairs(expected_general, pred[120:])
        require(report.get('exact') == exact and report.get('total') == len(expected)
                and report.get('paired_cases_exact') == pair_exact and
                report.get('paired_cases_total') == pair_total and
                report.get('paired_reversals_exact') == reversal_exact and
                report.get('paired_reversals_total') == reversal_total and
                report.get('head_checkpoint_sha256') == hashfile(checkpoint(root))
                and report.get('manifest_sha256') == hashfile(root / 'train_manifest.json')
                and orig_exact >= TEST_MIN and orig_pairs >= TEST_PAIR_MIN
                and general_exact >= ASYM_TEST_MIN and general_pairs >= ASYM_TEST_PAIR_MIN
                and report.get('original_test_exact') == orig_exact
                and report.get('asymmetric_test_exact') == general_exact
                and report.get('asymmetric_test_pairs_exact') == general_pairs
                and all(sum(r['label'] == p for r,p in zip(expected,pred)
                            if r['variant'] == variant) >= 51
                        for variant in v33.TEST_VARIANTS),
                'HOLDOUT_ACCEPTANCE_REPLAY_FAILED')
    return manifest, model


def audit(root, *, project='.'):
    root = Path(root)
    require(not (root / 'holdout_consumed.json').exists() and
            not (root / 'holdout_acceptance.json').exists(),
            'HOLDOUT_ALREADY_CONSUMED')
    manifest, model = load_head(root, project)
    require(manifest['dev_pass'] is True and
            manifest['original_dev_exact'] >= DEV_MIN and
            manifest['original_dev_pairs_exact'] >= DEV_PAIR_MIN and
            manifest['asymmetric_dev_exact'] >= ASYM_DEV_MIN and
            manifest['asymmetric_dev_pairs_exact'] >= ASYM_DEV_PAIR_MIN,
            'DEV_GATE_NOT_MET_TEST_REMAINS_SEALED')
    # Irrevocable sentinel before constructing or scoring TEST.
    write_new(root / 'holdout_consumed.json', {
        'schema': REPORT_SCHEMA, 'status': 'CONSUMED_NO_RETRY',
        'manifest_sha256': hashfile(root / 'train_manifest.json'),
        'checkpoint_sha256': hashfile(checkpoint(root)),
        'holdout_variants': list(v33.TEST_VARIANTS)})
    original_rows = v33.make_deck('holdout', manifest['seed'])
    general_rows = asymmetric_deck('holdout', manifest['seed'])
    rows = original_rows + general_rows
    pred = predict(model, features([r['prompt'] for r in rows]))
    exact = sum(row['label'] == p for row,p in zip(rows,pred))
    original_exact = sum(row['label'] == p for row,p in zip(original_rows,pred[:120]))
    general_exact = sum(row['label'] == p for row,p in zip(general_rows,pred[120:]))
    original_pair_exact, _ = test_pairs(original_rows,pred[:120])
    general_pair_exact, _ = test_pairs(general_rows,pred[120:])
    pair_exact, pair_total = test_pairs(rows,pred)
    reversal_exact, reversal_total = reversal_pair_stats(rows,pred)
    by_variant = {str(variant): {'correct': sum(row['label'] == p
                   for row,p in zip(rows,pred) if row['variant'] == variant),
                   'total': 64} for variant in v33.TEST_VARIANTS}
    majority = max(Counter(row['label'] for row in rows).values())
    passed = (original_exact >= TEST_MIN and original_pair_exact >= TEST_PAIR_MIN
              and general_exact >= ASYM_TEST_MIN
              and general_pair_exact >= ASYM_TEST_PAIR_MIN
              and exact > majority
              and all(v['correct'] >= 51 for v in by_variant.values()))
    v33.verify_activation(project, manifest['lineage'])
    ledger = [{'id': row['id'], 'prompt_sha256': hashbytes(row['prompt'].encode()),
               'truth': row['label'], 'prediction': p} for row,p in zip(rows,pred)]
    write_new(root / 'holdout_predictions.json', ledger)
    report = {'schema': REPORT_SCHEMA, 'status': 'PASS_SEALED_HOLDOUT_SHADOW_ONLY'
              if passed else 'FAIL_SEALED_HOLDOUT_NO_PROMOTION',
              'heldout_pass': passed, 'exact': exact, 'total': len(rows),
              'paired_cases_exact': pair_exact, 'paired_cases_total': pair_total,
              'paired_reversals_exact': reversal_exact,
              'paired_reversals_total': reversal_total,
              'original_test_exact': original_exact,
              'original_test_pairs_exact': original_pair_exact,
              'asymmetric_test_exact': general_exact,
              'asymmetric_test_pairs_exact': general_pair_exact,
              'per_variant': by_variant,
              'majority_baseline_exact': majority,
              'head_checkpoint_sha256': hashfile(checkpoint(root)),
              'manifest_sha256': hashfile(root / 'train_manifest.json'),
              'predictions_sha256': hashfile(root / 'holdout_predictions.json'),
              'holdout_deck_sha256': hashbytes(canonical(rows).encode()),
              'test_variants': list(v33.TEST_VARIANTS),
              'production_authority_changed': False,
              'tactical_v6_replaced': False,
              'limitations': 'Bounded prompt grammar and supplied payoff inputs; no arbitrary narrative parsing, live telemetry payoff learning, or tactical battle certification.'}
    write_new(root / 'holdout_acceptance.json', report)
    return report


def infer(root, prompt, *, project='.', device='cpu'):
    # Ground *new* prompt text before inference, never from labels or an oracle.
    x = ground(prompt)[None, :]
    manifest, model = load_head(root, project, require_audit=True)
    choice = CHOICES[predict(model, x)[0]]
    v33.verify_activation(project, manifest['lineage'])
    return {'choice': choice, 'learned': True,
            'model': HEAD_SCHEMA, 'checkpoint_sha256': hashfile(checkpoint(root)),
            'scoring_oracle_used': False, 'production_authority': False,
            'grounding': 'raw-prompt-payoff-and-belief-only'}


def shadow_smoke(root, *, project='.', device='cpu'):
    from main_computer.tactical_ai_service import TacticalAIService
    # New independently scored problem, external narrative payoffs, no sensors
    # used to infer the table or belief.
    prompt = ('A patrol must choose Contest (D) or Yield (C). Our payoffs: '
              'CC=8; CD=1; DC=14; DD=-5. The rival chooses C with '
              'probability 3/4, otherwise D. Which maximizes our expected '
              'score? Answer only Contest or Yield.')
    observation = {'schema': 'game.bridgeCaptainObservation.v1',
                   'captainId': 'captain.beta', 'shipId': 'ship.beta',
                   'mission': 'pursue-main-ship-and-seek-boarding-range',
                   'simulationSeconds': 10.0, 'rangeM': 3000.0,
                   'radialVelocityMps': -2.0, 'targetHullPercent': 100.0,
                   'transporterMaxRangeM': 1500.0,
                   'relativePositionM': [3000.0, 0.0],
                   'relativeVelocityMps': [-2.0, 0.0],
                   'boarding': {'phase': 'idle', 'boarders': 'aboard'}}
    response = TacticalAIService(Path(project)).shadow_gamer_hawk_dove(
        {'schema': 'game.bridgeGamerShadowRequest.v34', 'observation': observation,
         't08Prompt': prompt, 'run': str(Path(root).resolve())}, device=device)
    # Scoring only, after the model and Captain bridge have produced an answer.
    expected = v33.exact_teacher(8, 1, 14, -5, Fraction(3,4))
    passed = (response['t08Decision'] == CHOICES[expected] and
              response['actionPublished'] is False and
              response['captainOrder'] is None and
              response['suggestedLegalIntent']['maneuver'] == 'approach')
    result = {'status': 'PASS_V34_CAPTAIN_SHADOW_NO_ORDER' if passed
              else 'FAIL_V34_CAPTAIN_SHADOW_NO_ORDER',
              'expected_action': CHOICES[expected],
              'observed_action': response['t08Decision'],
              'captain_response': response, 'production_modified': False}
    write_new(Path(root) / 'captain_shadow_smoke.json', result)
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=('run','train','audit','status','shadow-smoke'))
    p.add_argument('--root', default='.')
    p.add_argument('--run', required=True)
    p.add_argument('--device', default='cuda')
    p.add_argument('--seed', type=int, default=33)
    p.add_argument('--epochs', type=int, default=160)
    return p


def main(argv=None):
    a = parser().parse_args(argv)
    if a.stage == 'run':
        fitted = train(a.run, project=a.root, seed=a.seed, epochs=a.epochs)
        print('GAMER_V34_TRAIN ' + canonical(fitted), flush=True)
        if not fitted['dev_pass']:
            result = {'status': 'FAIL_DEV_GATE_HOLDOUT_STILL_SEALED',
                      'dev_correct': fitted['best_dev_exact'],
                      'dev_total': fitted['dev_total'],
                      'original_dev_exact': fitted['original_dev_exact'],
                      'asymmetric_dev_exact': fitted['asymmetric_dev_exact'],
                      'test_evaluated': False, 'production_modified': False}
        else:
            audited = audit(a.run, project=a.root)
            print('GAMER_V34_HOLDOUT ' + canonical(audited), flush=True)
            if not audited['heldout_pass']:
                result = audited
            else:
                smoke = shadow_smoke(a.run, project=a.root, device=a.device)
                print('GAMER_V34_CAPTAIN ' + canonical(smoke), flush=True)
                result = {'status': 'PASS_V34_GROUNDED_GAME_LEARNING_SHADOW_ACCEPTANCE'
                          if smoke['status'] == 'PASS_V34_CAPTAIN_SHADOW_NO_ORDER'
                          else 'FAIL_V34_CAPTAIN_SHADOW_ACCEPTANCE',
                          'heldout_exact': audited['exact'],
                          'heldout_total': audited['total'],
                          'heldout_pairs': audited['paired_reversals_exact'],
                          'captain_shadow_pass': smoke['status'] == 'PASS_V34_CAPTAIN_SHADOW_NO_ORDER',
                          'v32_authority_changed': False,
                          'tactical_v6_authority_changed': False}
                write_new(Path(a.run) / 'operational_acceptance.json', result)
    elif a.stage == 'train':
        result = train(a.run, project=a.root, seed=a.seed, epochs=a.epochs)
    elif a.stage == 'audit':
        result = audit(a.run, project=a.root)
    elif a.stage == 'status':
        result = {'status': 'V34_GROUNDED_SHADOW_STATUS',
                  'train': (read(Path(a.run) / 'train_result.json')
                            if (Path(a.run) / 'train_result.json').exists() else None),
                  'holdout': (read(Path(a.run) / 'holdout_acceptance.json')
                              if (Path(a.run) / 'holdout_acceptance.json').exists() else None),
                  'captain': (read(Path(a.run) / 'captain_shadow_smoke.json')
                              if (Path(a.run) / 'captain_shadow_smoke.json').exists() else None)}
    else:
        result = shadow_smoke(a.run, project=a.root, device=a.device)
    print('GAMER_V34_' + result['status'] + ' ' + canonical(result), flush=True)
    return 1 if str(result['status']).startswith('FAIL_') else 0


if __name__ == '__main__':
    raise SystemExit(main())
