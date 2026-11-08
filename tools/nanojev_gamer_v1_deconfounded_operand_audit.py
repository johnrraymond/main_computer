#!/usr/bin/env python3
"""Frozen GAMER V1 deconfounded operand extraction/composition diagnostic.

Creates a new payoff panel in which q, R, S, T and P are independently
randomized and one operand at a time is changed, plus a separately labelled
exact-zero triple panel. TRAIN and DEV alone select readouts; a new heldout
panel is evaluated only after selection. Compare frozen LoRA token readouts
against an exact-Fraction parsed-operand + deterministic arithmetic oracle.

Everything is diagnostic. LoRA step 3000, production C0 and the established
zero detector remain immutable. Probe accuracy and hidden-state correlations
are not proof of intrinsic mathematical computation.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from fractions import Fraction
import gc
import json
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_operand_sufficiency_audit as operand
from tools import nanojev_gamer_v1_token_localization_lab as token
from tools import nanojev_gamer_v1_c0_sign_head as c0sign

SCHEMA = 'nanojev.gamer.v1.frozen-deconfounded-operands.v1'
STEP = 3000
SEED = 314159
CORE_GROUPS = {'train': 24, 'dev': 8, 'heldout': 8}
ZERO_GROUPS = {'train': 12, 'dev': 4, 'heldout': 4}
STAGES = ('embedding', 'layer_09', 'final')
VIEWS = ('belief', 'R', 'S', 'T', 'P', 'payoff_joint', 'operand_joint',
         'raster_mean100', 'lexical_numeric')
SELECTABLE = tuple(v for v in VIEWS if v != 'lexical_numeric')
SINGLE_OPERANDS = ('q', 'R', 'S', 'T', 'P')
DIAGNOSTIC_TASKS = ('q', 'R', 'S', 'T', 'P', 'T_minus_R', 'P_minus_S',
                    'q_times_T_minus_R', 'one_minus_q_times_P_minus_S',
                    'signed_margin', 'abs_margin', 'zero', 'positive_on_nonzero')


def _source(report_dir: Path) -> dict:
    """Fail before CUDA if any required upstream artifact changed."""
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    root = Path(report_dir).resolve()
    path = root / 'final_report.json'
    report = json.loads(path.read_text(encoding='utf-8'))
    if (report.get('schema') != operand.SCHEMA or
            report.get('status') != 'COMPLETE_FROZEN_OPERAND_SUFFICIENCY_NO_PROMOTION' or
            report.get('lora_step') != STEP or
            report.get('promotion') != 'BLOCKED_DIAGNOSTIC_ONLY' or
            report.get('no_lora_training') is not True or
            report.get('no_c0_training') is not True):
        raise ValueError('GAMER_DECONFOUND_REQUIRES_COMPLETE_FROZEN_OPERAND_AUDIT')
    token_report_path = Path(report['token_report_path']).resolve()
    if (token_report_path.name != 'final_report.json' or
            not token_report_path.is_file() or
            token._sha(token_report_path) != report['token_report_sha256']):
        raise ValueError('GAMER_DECONFOUND_TOKEN_LINEAGE_CHANGED')
    archive = Path(report['feature_archive']['path'])
    if (not archive.is_file() or
            token._sha(archive) != report['feature_archive']['sha256']):
        raise ValueError('GAMER_DECONFOUND_OPERAND_FEATURE_ARCHIVE_CHANGED')
    parent = operand._source(token_report_path.parent)
    src = parent['source']
    origin = src['origin']
    snapshot, sha = origin['snapshots'][STEP]
    if (sha != report['frozen_lora_sha256'] or
            not snapshot.exists() or stress._verify_snapshot(snapshot, STEP) != sha or
            report['zero_head_sha256'] != parent['token_report']['frozen_zero_head_sha256']):
        raise ValueError('GAMER_DECONFOUND_PROTECTED_CHECKPOINT_CHANGED')
    return {'report': report, 'path': path, 'archive': archive, 'parent': parent,
            'origin': origin, 'snapshot': snapshot, 'snapshot_sha': sha,
            'zero_head': src['source']['original']['prior']['zero_head']}


def _profile_design(n: int, seed: int):
    """Numerical range-matched Latin-hypercube columns, not profile trends."""
    if n < 8:
        raise ValueError('GAMER_DECONFOUND_TOO_FEW_INDEPENDENT_GROUPS')
    rng = np.random.default_rng(seed)
    # Optimize ONLY input-feature independence, never oracle label/accuracy.
    best = None
    for _ in range(1024):
        order = np.stack([rng.permutation(n) for _ in range(5)], axis=1)
        corr = np.corrcoef(order.astype(float), rowvar=False)
        max_abs = float(np.max(np.abs(corr[np.triu_indices(5, 1)])))
        if best is None or max_abs < best[0]:
            best = (max_abs, order.copy())
    return best[1], best[0]


def _make_deck():
    """Keep independently randomized core and algebraically anchored zero separate.

    Every single-variable edit changes exactly one of q/R/S/T/P, and the same
    original tuple never crosses a train/dev/heldout boundary. The zero panel
    is *not* used as evidence that the independently varied operands are
    statistically independent.
    """
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_zero_sign_frob as prompts
    from tools import nanojev_two_game_spine_smoke as spine
    renderers = (('spine_belief', prompts._spine_belief_prompt),
                 ('game_prose_belief', prompts._game_prose_belief_prompt))
    deck = []
    tags = []
    profiles = []
    base_values_by_role = {}
    for role_i, role in enumerate(('train', 'dev', 'heldout')):
        n = CORE_GROUPS[role]
        design, max_corr = _profile_design(n, SEED + role_i * 19)
        base_values = []
        for i, (q_i, r_i, s_i, t_i, p_i) in enumerate(design):
            # Each operand has the *same* support in each split (not the
            # disjoint upward-trending ranges of the previous audit).
            def level(x): return 112 + int(round(23 * int(x) / (n - 1)))
            q = Fraction(3 + int(round(10 * int(q_i) / (n - 1))), 17)
            r, s, t, p = [Fraction(level(z)) for z in (r_i, s_i, t_i, p_i)]
            stage = spine.Stage(r, s, t, p)
            base_values.append([float(q), float(r), float(s), float(t), float(p)])
            group = f'orthogonal.{role}.{i:04d}'
            edits = [('base', q, stage, None),
                     ('q_edit', q + Fraction(1, 17), stage, 'q')]
            for j, name in enumerate(('R', 'S', 'T', 'P')):
                values = [r, s, t, p]
                shift = Fraction(3 if (i + j) % 2 == 0 else -3)
                values[j] += shift
                edits.append((name + '_edit', q, spine.Stage(*values), name))
            for edit, q_edit, stage_edit, changed in edits:
                for surface, render in renderers:
                    ident = f'{group}.{edit}.{surface}'
                    value = stage_edit.margin(q_edit)
                    case = oracle.Case(ident, 'test' if role == 'heldout' else role,
                           group, surface, render(stage_edit, q_edit), value,
                           'independent_operands', value)
                    deck.append(case)
                    tags.append({'role': role, 'panel': 'independent', 'profile': group,
                                 'surface': surface, 'edit': edit, 'changed': changed,
                                 'base_id': f'{group}.base.{surface}',
                                 'oracle': spine.fstr(value)})
            profiles.append({'id': group, 'role': role, 'panel': 'independent',
                             'base': [spine.fstr(z) for z in (q, r, s, t, p)],
                             'design_max_input_column_correlation': max_corr})
        base_values_by_role[role] = base_values
        # Separate anchored triples, not part of independent-variable results.
        for i in range(ZERO_GROUPS[role]):
            rng = np.random.default_rng(SEED + 300 + role_i * 101 + i)
            r = Fraction(int(rng.integers(113, 129)))
            s = Fraction(int(rng.integers(113, 129)))
            left_mag = Fraction(int(rng.integers(8, 17)))
            right_mag = Fraction(int(rng.integers(8, 17)))
            orient = 1 if (i + role_i) % 2 == 0 else -1
            stage = spine.Stage(r, s, r + orient * left_mag,
                                s - orient * right_mag)
            q0 = spine.critical_q(stage)
            if q0 is None or stage.margin(q0) != 0:
                raise ValueError('GAMER_DECONFOUND_INVALID_ZERO_ORACLE')
            radius = Fraction(1, 100 + 20 * (i % 3))
            group = f'zero_anchor.{role}.{i:04d}'
            for label, shift in (('negative', -1), ('zero', 0), ('positive', 1)):
                # Label based on actual sign; ±belief direction reverses
                # when left and right payoff gaps reverse.
                q = q0 + shift * radius * orient
                if not Fraction(0) < q < Fraction(1):
                    raise ValueError('GAMER_DECONFOUND_ZERO_BELIEF_OUTSIDE_UNIT')
                target = stage.margin(q)
                true_label = ('negative' if target < 0 else
                              'positive' if target > 0 else 'zero')
                if true_label != label:
                    # Because dmargin/dq = orient * (left_mag+right_mag).
                    raise ValueError('GAMER_DECONFOUND_ZERO_TRIPLE_SIGN_FAILURE')
                for surface, render in renderers:
                    ident = f'{group}.{label}.{surface}'
                    deck.append(oracle.Case(ident, 'test' if role == 'heldout' else role,
                      group, surface, render(stage, q), target, 'zero_anchor', target))
                    tags.append({'role': role, 'panel': 'zero_anchor',
                         'profile': group, 'surface': surface, 'edit': label,
                         'changed': 'q', 'base_id': f'{group}.zero.{surface}',
                         'oracle': spine.fstr(target)})
            profiles.append({'id': group, 'role': role, 'panel': 'zero_anchor',
                             'center_q': spine.fstr(q0),
                             'payoffs': [spine.fstr(z) for z in (stage.R, stage.S, stage.T, stage.P)]})
    if len(deck) != 2 * (sum(CORE_GROUPS.values()) * 6 + sum(ZERO_GROUPS.values()) * 3):
        raise ValueError('GAMER_DECONFOUND_CASE_COUNT_MISMATCH')
    if len({x.id for x in deck}) != len(deck):
        raise ValueError('GAMER_DECONFOUND_DUPLICATE_CASE_ID')
    if any(tags[i]['oracle'] != spine.fstr(c.target) for i, c in enumerate(deck)):
        raise ValueError('GAMER_DECONFOUND_BAD_ORACLE')
    # Role IDs and complete underlying (q,R,S,T,P) tuples are disjoint.
    spans_by_role = {r: set() for r in CORE_GROUPS}
    for case, tag in zip(deck, tags):
        spans = token._spans(case.prompt)
        exact = (case.prompt[slice(*spans['belief'][0])],) + tuple(
            case.prompt[slice(*s)] for s in spans['payoffs'])
        # The second prompt rendering for the same case is intentional.
        spans_by_role[tag['role']].add(tuple(map(str, map(Fraction, exact))))
    names = tuple(CORE_GROUPS)
    if any(spans_by_role[names[i]] & spans_by_role[names[j]]
           for i, j in ((0, 1), (0, 2), (1, 2))):
        raise ValueError('GAMER_DECONFOUND_SPLIT_OPERAND_TUPLE_LEAK')
    indices = {role: np.asarray([i for i, tag in enumerate(tags)
                 if tag['role'] == role], dtype=int) for role in CORE_GROUPS}
    for role in CORE_GROUPS:
        for panel in ('independent', 'zero_anchor'):
            indices[f'{role}_{panel}'] = np.asarray(
               [i for i, t in enumerate(tags) if t['role'] == role and t['panel'] == panel],
               dtype=int)
    return deck, tags, indices, profiles, base_values_by_role


def _captured_features(source: dict, deck: list, *, device: str, batch_size: int):
    """One frozen forward pass per batch; no gradients/weight changes."""
    import torch
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools.nanojev_gamer_v1_expanded_head import positions_for_length
    origin = source['origin']
    _, tok, model = gamer.model_components(
        origin['meta']['lineage']['model_id'], device,
        adapter_dir=source['snapshot'])
    cfg = origin['meta']['lineage']['configuration']
    features = {stage: {view: [] for view in VIEWS} for stage in STAGES}
    try:
        spans = operand._aligned_token_spans(tok, deck)
        core = oracle._backbone_parts(model)
        if len(core.layers) <= 9:
            raise ValueError('GAMER_DECONFOUND_LAYER_09_ABSENT')
        modules = {'embedding': core.embed_tokens, 'layer_09': core.layers[9],
                   'final': core.norm}
        model.eval()
        for start in range(0, len(deck), batch_size):
            if start == 0 or start % 40 < batch_size:
                print(f'DECONFOUND_CAPTURE {start}/{len(deck)}', flush=True)
            chunk = deck[start:start+batch_size]
            if any(len(tok.encode(c.prompt, add_special_tokens=True)) > cfg['max_length']
                   for c in chunk):
                raise ValueError('GAMER_DECONFOUND_PROMPT_TOO_LONG')
            batch = tok([c.prompt for c in chunk], padding=True, truncation=True,
                        max_length=cfg['max_length'], return_tensors='pt')
            batch = {k: v.to(device) for k, v in batch.items()}
            lengths = [int(v) for v in batch['attention_mask'].sum(-1).tolist()]
            observed = defaultdict(list)
            handles = []
            for name, mod in modules.items():
                def collect(_module, _arguments, output, stage=name):
                    value = output[0] if isinstance(output, (tuple, list)) else output
                    if not isinstance(value, torch.Tensor) or value.ndim != 3:
                        raise ValueError('GAMER_DECONFOUND_WRONG_CAPTURE_SHAPE')
                    observed[stage].append(value.detach())
                handles.append(mod.register_forward_hook(collect))
            try:
                with torch.inference_mode():
                    model(**batch, output_hidden_states=False,
                          return_dict=True, logits_to_keep=1)
            finally:
                for hook in handles:
                    hook.remove()
            if any(len(observed[stage]) != 1 for stage in STAGES):
                raise ValueError('GAMER_DECONFOUND_INCOMPLETE_HIDDEN_CAPTURE')
            for j, size in enumerate(lengths):
                spec = spans[start+j]
                if size != len(spec['ids']):
                    raise ValueError('GAMER_DECONFOUND_CAPTURE_TOKENIZATION_MISMATCH')
                positions = np.asarray(positions_for_length(
                    size, 100, cfg['curvature']), dtype=int)
                for stage in STAGES:
                    frame = observed[stage][0][j, :size].float().cpu().numpy()
                    views = operand._feature_from_hidden(frame, spec, positions)
                    views['lexical_numeric'] = operand._lexical(spec)
                    for view in VIEWS:
                        features[stage][view].append(views[view])
            del observed
        arrays = {stage: {view: np.stack(values).astype(np.float32)
                  for view, values in views.items()}
                  for stage, views in features.items()}
        if any(len(a) != len(deck) or not np.isfinite(a).all()
               for views in arrays.values() for a in views.values()):
            raise ValueError('GAMER_DECONFOUND_NONFINITE_OR_INCOMPLETE_FEATURES')
        return arrays
    finally:
        del model, tok
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _rank(task, metric):
    if task in operand.BINARY:
        return metric['min_recall'], metric['balanced_accuracy']
    return -metric['mae'], -metric['p90_abs_error']


def _ridge_tasks(features, targets, ids, tasks, kind, reg):
    """Train-only, regularized multi-target readout; no DEV/heldout fitting."""
    ids = np.asarray(ids, dtype=int)
    cross, gram, _meta = operand._encode_train_map(features, ids, kind)
    y = np.stack([targets[k][ids] for k in tasks], axis=1)
    bias = y.mean(axis=0)
    scale = np.maximum(y.std(axis=0), 1e-6)
    alpha = np.linalg.solve(gram.astype(np.float64) + reg*np.eye(len(ids)),
                            (y-bias)/scale)
    output = np.asarray(cross @ alpha * scale + bias, dtype=np.float64)
    if not np.isfinite(output).all():
        raise ValueError('GAMER_DECONFOUND_NONFINITE_READOUT')
    return dict(zip(tasks, output.T))


def _fit_candidate(frame, labels, ix, kind, reg):
    """Strict core-only numerical/polarity fit; zero uses the separate panel."""
    core = ix['train_independent']
    if np.any(labels['zero'][core] != 0):
        raise ValueError('GAMER_DECONFOUND_INDEPENDENT_TRAIN_ZERO_INTRUSION')
    core_tasks = tuple(k for k in DIAGNOSTIC_TASKS if k != 'zero')
    numeric = _ridge_tasks(frame, labels, core, core_tasks, kind, reg)
    zero = _ridge_tasks(frame, labels, ix['train'], ('zero',), kind, reg)
    return {**numeric, **zero}


def _metric(task, labels, prediction, ids, threshold, train):
    ids = np.asarray(ids, dtype=int)
    m = operand._score(labels[task], prediction, task, ids, threshold)
    if task in operand.CONTINUOUS:
        baseline = np.median(labels[task][train])
        m['train_median_constant_mae'] = float(np.mean(np.abs(labels[task][ids] - baseline)))
        m['improves_train_median'] = m['mae'] < m['train_median_constant_mae']
        if task in ('signed_margin', 'abs_margin'):
            truth = labels[task][ids]
            err = np.abs(prediction[ids] - truth)
            tol = np.minimum(.05, np.maximum(.001, .1*np.abs(truth)))
            m['within_tight_tolerance'] = float(np.mean(err <= tol))
    return m


def _development_subset(task, labels, ix):
    if task == 'zero':
        return ix['dev']  # Includes the deliberately separate zero-anchor panel.
    if task == 'positive_on_nonzero':
        ids = ix['dev_independent']
        return ids[labels['zero'][ids] == 0]
    return ix['dev_independent']


def _evaluation_subset(task, labels, ix):
    if task == 'zero':
        return ix['heldout']
    if task == 'positive_on_nonzero':
        ids = ix['heldout_independent']
        return ids[labels['zero'][ids] == 0]
    return ix['heldout_independent']


def _fit_stages(arrays: dict, labels: dict, ix: dict):
    """TRAIN fit; independent-only DEV model selection for numerical tasks.

    No numbers from heldout influence fitting, thresholding, or view selection.
    Zero-only conditioning never changes the numerical-operand training set.
    """
    details = {}
    predictions = {}
    train = ix['train_independent']
    for stage, views in arrays.items():
        print('  readouts:', stage, flush=True)
        by_view = {}
        for name, frame in views.items():
            fitted = {}
            for kind in operand.KINDS:
                for reg in operand.REGS:
                    candidate = (kind, reg)
                    fitted[candidate] = _fit_candidate(frame, labels, ix, kind, reg)
            chosen = {}
            for task in DIAGNOSTIC_TASKS:
                dev_ids = _development_subset(task, labels, ix)
                best = None
                for (kind, reg), pred in fitted.items():
                    thresholds = operand.THRESHOLDS if task in operand.BINARY else (0.,)
                    for threshold in thresholds:
                        m = _metric(task, labels, pred[task], dev_ids,
                                    threshold, train)
                        rank = _rank(task, m)
                        if best is None or rank > best[0]:
                            best = rank, kind, reg, threshold, m, pred[task]
                _, kind, reg, threshold, dev_score, values = best
                held_score = _metric(task, labels, values,
                    _evaluation_subset(task, labels, ix), threshold, train)
                chosen[task] = {'selection': {'kernel': kind, 'regularization': reg,
                                 'threshold': threshold},
                                'development': dev_score, 'heldout': held_score,
                                'predicted': values}
            by_view[name] = chosen
        task_details = {}
        final_predictions = {}
        for task in DIAGNOSTIC_TASKS:
            selected = max(SELECTABLE,
               key=lambda view: _rank(task, by_view[view][task]['development']))
            best = by_view[selected][task]
            lexical = by_view['lexical_numeric'][task]
            task_details[task] = {
                'dev_selected_view': selected,
                'selected_readout': best['selection'],
                'development': best['development'],
                'heldout': best['heldout'],
                'lexical_holdout': lexical['heldout'],
                'train_median_baseline': best['heldout'].get(
                        'train_median_constant_mae')}
            final_predictions[task] = best['predicted']
        details[stage] = {'tasks': task_details,
               'numerical_fit': 'INDEPENDENT_TRAIN_ONLY',
               'numerical_selection': 'INDEPENDENT_DEV_ONLY',
               'zero_fit': 'TRAIN_INCLUDING_SEPARATE_ZERO_ANCHORS',
               'lexical_control_is_not_an_oracle': True}
        predictions[stage] = final_predictions
    return details, predictions


def _error(y, pred, ids):
    ids = np.asarray(ids, dtype=int)
    truth = np.asarray(y)[ids]
    got = np.asarray(pred)[ids]
    if not len(ids) or not np.isfinite(got).all():
        raise ValueError('GAMER_DECONFOUND_INVALID_EVALUATION')
    e = np.abs(truth - got)
    tight = np.minimum(.05, np.maximum(.001, .1 * np.abs(truth)))
    return {'cases': len(ids), 'mae': float(np.mean(e)),
            'p90_abs_error': float(np.quantile(e, .9)),
            'within_0_05': float(np.mean(e <= .05)),
            'within_tight_tolerance': float(np.mean(e <= tight)),
            'exact_zero_predictions': int(np.sum((np.abs(got) <= 1e-9) & (np.abs(truth) <= 1e-9))),
            'zero_target_cases': int(np.sum(np.abs(truth) <= 1e-9))}


def _composition(labels, estimates, ix):
    """Separate operand extraction from exact arithmetic, using oracle swaps."""
    q = labels['q']; r = labels['R']; s = labels['S']; t = labels['T']; p = labels['P']
    a = estimates['q']; br = estimates['R']; bs = estimates['S']
    bt = estimates['T']; bp = estimates['P']
    gap_a = estimates['T_minus_R']; gap_b = estimates['P_minus_S']
    recon = {
        'always_zero': np.zeros_like(q),
        'direct_margin_readout': estimates['signed_margin'],
        'decoded_all_five_plus_exact_formula': a * (bt - br) + (1-a) * (bp - bs),
        'exact_q_decoded_payoffs': q * (bt - br) + (1-q) * (bp - bs),
        'decoded_q_exact_payoffs': a * (t - r) + (1-a) * (p - s),
        'decoded_q_decoded_gaps': a * gap_a + (1-a) * gap_b,
        'exact_q_decoded_gaps': q * gap_a + (1-q) * gap_b,
        'decoded_q_exact_gaps': a * (t-r) + (1-a) * (p-s),
        'exact_parsed_operands_exact_formula': q * (t-r) + (1-q) * (p-s),
    }
    result = {}
    for panel in ('independent', 'zero_anchor'):
        ids = ix[f'heldout_{panel}']
        result[panel] = {name: _error(labels['signed_margin'], got, ids)
                         for name, got in recon.items()}
        if result[panel]['exact_parsed_operands_exact_formula']['mae'] > 1e-10:
            raise ValueError('GAMER_DECONFOUND_EXACT_PARSER_CONTROL_FAILED')
    return result


def _counterfactuals(deck, tags, ix, labels, estimates):
    """Evaluate on-manifold one-operand edits and unchanged-operand leakage."""
    by_id = {case.id: i for i, case in enumerate(deck)}
    entries = defaultdict(list)
    for i in ix['heldout_independent']:
        i = int(i)
        t = tags[i]
        if t['edit'] == 'base':
            continue
        base = by_id[t['base_id']]
        changed = t['changed']
        if changed not in SINGLE_OPERANDS or base == i:
            raise ValueError('GAMER_DECONFOUND_INVALID_EDIT_PAIR')
        unchanged = [k for k in SINGLE_OPERANDS if k != changed]
        if any(labels[k][i] != labels[k][base] for k in unchanged):
            raise ValueError('GAMER_DECONFOUND_NOT_ONE_OPERAND_EDIT')
        if labels[changed][i] == labels[changed][base]:
            raise ValueError('GAMER_DECONFOUND_EDIT_DOES_NOT_CHANGE_OPERAND')
        oracle_delta = labels['signed_margin'][i] - labels['signed_margin'][base]
        predicted_delta = estimates['signed_margin'][i] - estimates['signed_margin'][base]
        operand_oracle = labels[changed][i] - labels[changed][base]
        operand_pred = estimates[changed][i] - estimates[changed][base]
        entries[changed].append({
          'id': t['profile'], 'surface': t['surface'],
          'oracle_operand_delta': float(operand_oracle),
          'decoded_operand_delta': float(operand_pred),
          'oracle_margin_delta': float(oracle_delta),
          'decoded_margin_delta': float(predicted_delta),
          'wrong_operand_changes': [float(estimates[k][i] - estimates[k][base])
                                    for k in unchanged],
          'true_unchanged_operands': unchanged})
    output = {}
    for changed, cases in entries.items():
        oracle = np.array([c['oracle_margin_delta'] for c in cases])
        got = np.array([c['decoded_margin_delta'] for c in cases])
        op = np.array([c['oracle_operand_delta'] for c in cases])
        op_got = np.array([c['decoded_operand_delta'] for c in cases])
        moving = oracle != 0
        output[changed] = {
            'cases': len(cases),
            'oracle_zero_delta_cases': int(np.sum(~moving)),
            'operand_delta_mae': float(np.mean(np.abs(op - op_got))),
            'operand_direction_accuracy': float(np.mean(np.sign(op) == np.sign(op_got))),
            'margin_delta_mae': float(np.mean(np.abs(oracle - got))),
            'margin_direction_accuracy': float(np.mean(
                    np.sign(oracle[moving]) == np.sign(got[moving])))
                 if moving.any() else None,
            'null_delta_false_response_fraction': float(np.mean(np.abs(got[~moving]) > 1e-8))
                 if (~moving).any() else None,
            'no_change_margin_baseline_mae': float(np.mean(np.abs(oracle))),
            'mean_spurious_unchanged_operand_delta': float(np.mean(
                   [abs(d) for c in cases for d in c['wrong_operand_changes']])),
            'illustrative_cases': cases[:2]}
    if set(output) != set(SINGLE_OPERANDS) or any(x['cases'] != 2 * CORE_GROUPS['heldout']
                        for x in output.values()):
        raise ValueError('GAMER_DECONFOUND_INCOMPLETE_ONE_OPERAND_TESTS')
    return output


def _zero_panel(labels, estimates, ix, threshold=.5):
    ids = ix['heldout_zero_anchor']
    yy = labels['zero'][ids] >= .5
    pp = estimates['zero'][ids] >= threshold
    if yy.sum() != 2 * ZERO_GROUPS['heldout'] or len(ids) != 6 * ZERO_GROUPS['heldout']:
        raise ValueError('GAMER_DECONFOUND_INCOMPLETE_EXACT_ZERO_PANEL')
    recall_zero = float(np.mean(pp[yy]))
    recall_nonzero = float(np.mean(~pp[~yy]))
    pos_ids = ids[~yy]
    true_sign = labels['signed_margin'][pos_ids] > 0
    pred_sign = estimates['signed_margin'][pos_ids] > 0
    return {'zero_recall': recall_zero, 'nonzero_recall': recall_nonzero,
            'zero_cases': int(yy.sum()), 'nonzero_cases': int((~yy).sum()),
            'polarity_accuracy_from_signed_margin': float(np.mean(true_sign == pred_sign)),
            'sign_correct_case_count': int(np.sum(true_sign == pred_sign)),
            'zero_line_promotion': 'BLOCKED_DIAGNOSTIC_ONLY'}


def run(args):
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    if args.capture_batch < 1:
        raise ValueError('GAMER_DECONFOUND_INVALID_CAPTURE_BATCH')
    source = _source(args.operand)
    old = source['parent']['source']; original = old['source']['original']
    origin = source['origin']
    protect = [Path(args.operand).resolve(), source['path'].parent,
        source['parent']['pool_path'].parent, old['report_path'].parent,
        old['source']['v2_report_path'].parent, original['report_path'].parent,
        original['prior']['joint_dir'], original['prior']['lineage']['report_path'].parent,
        origin['training'], origin['source'], origin['capture'], origin['ckpt'].parent,
        origin['adapter'], origin['audit_path'].parent]
    out = c0sign._output(args.output, protect)
    if out.exists():
        raise FileExistsError('GAMER_DECONFOUND_OUTPUT_ALREADY_EXISTS')
    deck, tags, ix, profiles, base_inputs = _make_deck()
    label = operand._targets(deck)  # Exact target parsed independently of ID or model.
    sha_before = token._sha(source['path'])
    print(f'GAMER DECONFOUNDED OPERANDS: frozen LoRA {STEP}; {len(deck)} cases '
          f'({len(ix["train"])} TRAIN, {len(ix["dev"])} DEV, '
          f'{len(ix["heldout"])} heldout); five independent operand edits',flush=True)
    arrays = _captured_features(source, deck, device=args.device,
                                 batch_size=args.capture_batch)
    if (token._sha(source['path']) != sha_before or
            stress._verify_snapshot(source['snapshot'], STEP) != source['snapshot_sha'] or
            token._sha(source['archive']) != source['report']['feature_archive']['sha256']):
        raise ValueError('GAMER_DECONFOUND_SOURCE_CHANGED_DURING_CAPTURE')
    stages, estimates = _fit_stages(arrays, label, ix)
    for stage in STAGES:
        stages[stage]['composition'] = _composition(label, estimates[stage], ix)
        stages[stage]['single_operand_counterfactuals'] = _counterfactuals(
            deck, tags, ix, label, estimates[stage])
        # The zero classifier readout is diagnostic, selected only on DEV.
        stages[stage]['zero_panel'] = _zero_panel(label, estimates[stage], ix,
          threshold=float(stages[stage]['tasks']['zero']['development']['threshold']))
    # Compute exact-Fraction parse -> formula on all inputs, not just float64.
    for i, c in enumerate(deck):
        spans = token._spans(c.prompt)
        q = Fraction(c.prompt[slice(*spans['belief'][0])])
        r, s, t, p = (Fraction(c.prompt[slice(*span)]) for span in spans['payoffs'])
        if c.target != q * (t-r) + (1-q) * (p-s):
            raise ValueError('GAMER_DECONFOUND_EXACT_OPERAND_PARSER_FAILURE')
    if (token._sha(source['path']) != sha_before or
            token._sha(source['zero_head']) != source['report']['zero_head_sha256'] or
            stress._verify_snapshot(source['snapshot'], STEP) != source['snapshot_sha']):
        raise ValueError('GAMER_DECONFOUND_PROTECTED_SOURCE_CHANGED_BEFORE_REPORT')
    report = {
      'schema': SCHEMA, 'status': 'COMPLETE_DECONFOUNDED_OPERAND_DIAGNOSTIC_NO_PROMOTION',
      'promotion': 'BLOCKED_DIAGNOSTIC_ONLY', 'lora_step': STEP,
      'frozen_lora_sha256': source['snapshot_sha'],
      'old_operand_report_sha256': sha_before,
      'old_operand_report_path': str(source['path']),
      'frozen_zero_head_sha256': source['report']['zero_head_sha256'],
      'cases': {'total': len(deck), 'by_role': {k: len(ix[k]) for k in CORE_GROUPS},
                'independent_core': {k: len(ix[f'{k}_independent']) for k in CORE_GROUPS},
                'exact_zero_panel': {k: len(ix[f'{k}_zero_anchor']) for k in CORE_GROUPS}},
      'panel_design': {'seed': SEED, 'single_operand_changes': list(SINGLE_OPERANDS),
          'ranges_matched_between_splits': True,
          'independent_core_vs_anchored_zero_separated': True,
          'base_input_column_correlation_max_by_role': {
              role: float(np.max(np.abs(np.corrcoef(np.asarray(rows).T)[
                        np.triu_indices(5, 1)]))) for role, rows in base_inputs.items()},
          'profiles': profiles},
      'deck_sha256': oracle.deck_hash(deck),
      'stages': stages, 'no_lora_training': True, 'no_c0_training': True,
      'no_zero_detector_mutation': True, 'selection': 'TRAIN_FIT_DEV_ONLY',
      'heldout_inspected_only_after_selection': True,
      'arithmetic_control': 'EXACT_FRACTION_PARSED_OPERANDS_AND_DETERMINISTIC_FORMULA',
      'interpretation': (
          'The independently varied core diagnoses extraction and signed arithmetic. '
          'Anchored exact-zero triples are separate because zero formation constrains operands. '
          'Exact parsing plus deterministic arithmetic is an oracle control, not evidence '
          'that frozen LoRA represents exact operands. Probes can fail without proving '
          'information absence; hidden-state correlations may reflect lexical patterns.'),
    }
    out.mkdir(parents=True, exist_ok=False)
    # Cache small selected frozen readout views as independently auditable features.
    archive = {f'{stage}__{view}': values.astype(np.float16)
        for stage, frames in arrays.items() for view, values in frames.items()}
    if any(not np.isfinite(value).all() for value in archive.values()):
        raise ValueError('GAMER_DECONFOUND_NONFINITE_ARCHIVE')
    np.savez_compressed(out/'frozen_deconfounded_views.npz', **archive)
    report['feature_archive'] = {
      'path': str(out/'frozen_deconfounded_views.npz'),
      'sha256': token._sha(out/'frozen_deconfounded_views.npz'),
      'arrays': {k: list(v.shape) for k, v in archive.items()},
      'stored_dtype': 'float16', 'probe_dtype': 'float32'}
    gamer.atomic_json(out/'final_report.json', report)
    print('GAMER DECONFOUNDED OPERANDS COMPLETE:', out/'final_report.json',flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operand', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--device', choices=('cpu','cuda','directml'), default='cuda')
    parser.add_argument('--capture-batch', type=int, default=2)
    return run(parser.parse_args(argv))


if __name__ == '__main__':
    main()
