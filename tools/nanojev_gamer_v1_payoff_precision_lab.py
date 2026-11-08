#!/usr/bin/env python3
"""Frozen-GAMER exact-payoff extraction lab, independent of production C0.

Train only on the deconfounded 600-case deck. Compare three decoders on
identical layer-9 LoRA features: continuous ridge rounded to integers,
whole-integer multiclass ridge, and per-digit multiclass ridge. Retain the
anchored-zero panel separately; select all candidate hyperparameters/views
using development profiles, never heldout outcomes. Recompose margins via
exact Fraction arithmetic from either decoded or parsed operands.

Nothing here trains LoRA, changes C0 or alters the existing zero detector.
Numerical-token classification is an extraction diagnostic, not proof that
Qwen intrinsically performed exact integer arithmetic.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
from fractions import Fraction
import json
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_deconfounded_operand_audit as parent
from tools import nanojev_gamer_v1_operand_sufficiency_audit as operand
from tools import nanojev_gamer_v1_token_localization_lab as token
from tools import nanojev_gamer_v1_c0_sign_head as c0sign

SCHEMA = 'nanojev.gamer.v1.frozen-integer-payoff-precision.v1'
STEP = 3000
PAYOFFS = ('R', 'S', 'T', 'P')
VIEWS = ('R', 'S', 'T', 'P', 'payoff_joint', 'operand_joint', 'raster_mean100')
DECODERS = ('integer_class', 'digitwise')
KERNELS = ('linear', 'rbf')
REGS = (0.1, 1.0, 5.0)
PANELS = ('independent', 'zero_anchor')


def _source(report_dir: Path):
    """Validate every upstream lineage before a potentially expensive capture."""
    root = Path(report_dir).resolve()
    path = root / 'final_report.json'
    report = json.loads(path.read_text(encoding='utf-8'))
    if (report.get('schema') != parent.SCHEMA or
        report.get('status') != 'COMPLETE_DECONFOUNDED_OPERAND_DIAGNOSTIC_NO_PROMOTION' or
        report.get('promotion') != 'BLOCKED_DIAGNOSTIC_ONLY' or
        report.get('lora_step') != STEP or
        report.get('no_lora_training') is not True or
        report.get('no_c0_training') is not True or
        report.get('no_zero_detector_mutation') is not True or
        report.get('selection') != 'TRAIN_FIT_DEV_ONLY'):
        raise ValueError('GAMER_PAYOFF_PRECISION_REQUIRES_COMPLETE_DECONFOUNDED_REPORT')
    # The source report contains an attested absolute path to the preceding
    # operand audit. It is a file, not a directory.
    preceding = Path(report['old_operand_report_path']).resolve()
    if (preceding.name != 'final_report.json' or not preceding.is_file() or
        token._sha(preceding) != report['old_operand_report_sha256']):
        raise ValueError('GAMER_PAYOFF_PRECISION_OPERAND_LINEAGE_CHANGED')
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    source = parent._source(preceding.parent)
    if (source['snapshot_sha'] != report['frozen_lora_sha256'] or
        stress._verify_snapshot(source['snapshot'], STEP) != source['snapshot_sha'] or
        token._sha(source['zero_head']) != report['frozen_zero_head_sha256']):
        raise ValueError('GAMER_PAYOFF_PRECISION_PROTECTED_CHECKPOINT_CHANGED')
    archive = Path(report['feature_archive']['path']).resolve()
    if (not archive.is_file() or
        token._sha(archive) != report['feature_archive']['sha256']):
        raise ValueError('GAMER_PAYOFF_PRECISION_SOURCE_FEATURE_ARCHIVE_CHANGED')
    return {'root': root, 'report_path': path, 'report': report,
            'source': source, 'archive': archive, 'old_operand_path': preceding}


def _integer_targets(labels):
    result = {}
    for key in PAYOFFS:
        values = np.asarray(labels[key], dtype=np.float64)
        ints = np.rint(values).astype(np.int64)
        if (not np.isfinite(values).all() or
            not np.array_equal(values, ints.astype(np.float64)) or
            np.any((ints < 0) | (ints > 999))):
            raise ValueError('GAMER_PAYOFF_PRECISION_REQUIRES_EXACT_NONNEGATIVE_INTEGERS')
        result[key] = ints
    return result


def _columns(targets, train):
    """Each digit class has an independent logit; never decode using labels."""
    matrix, specs = [], {}
    train = np.asarray(train, dtype=int)
    for name in PAYOFFS:
        vals = targets[name][train]
        classes = np.unique(vals).astype(int)
        if not len(classes):
            raise ValueError('GAMER_PAYOFF_PRECISION_EMPTY_CLASSES')
        specs[(name, 'integer_class')] = (len(matrix), len(matrix) + len(classes), classes)
        for cls in classes:
            matrix.append((vals == cls).astype(np.float64))
        for index, power in enumerate((100, 10, 1)):
            digits = (vals // power) % 10
            specs[(name, f'digit{index}')] = (len(matrix), len(matrix) + 10,
                                             np.arange(10, dtype=int))
            for d in range(10):
                matrix.append((digits == d).astype(np.float64))
    return np.stack(matrix, axis=1), specs


def _decode(scores, specs, decoder):
    result = {}
    for name in PAYOFFS:
        if decoder == 'integer_class':
            start, stop, labels = specs[(name, decoder)]
            result[name] = labels[np.argmax(scores[:, start:stop], axis=1)]
        elif decoder == 'digitwise':
            parts = []
            for position in range(3):
                a, b, digits = specs[(name, f'digit{position}')]
                parts.append(digits[np.argmax(scores[:, a:b], axis=1)])
            result[name] = 100 * parts[0] + 10 * parts[1] + parts[2]
        else:
            raise ValueError('GAMER_PAYOFF_PRECISION_UNKNOWN_DECODER')
    return result


def _kernel_candidates(frame, train, regs=REGS, kernels=KERNELS):
    """Train-only centering/kernel width; shared eigenbasis for all ridge regs."""
    train = np.asarray(train, dtype=int)
    for kind in kernels:
        cross, gram, meta = operand._encode_train_map(frame, train, kind)
        sym = (gram.astype(np.float64) + gram.T.astype(np.float64)) * .5
        eigenvalues, eigenvectors = np.linalg.eigh(sym)
        eigenvalues = np.maximum(eigenvalues, 0.)
        yield kind, cross.astype(np.float64), eigenvalues, eigenvectors, meta


def _model_outputs(frame, targets, train, *, regs=REGS, kernels=KERNELS):
    """Diagnostic candidate predictions with saved replay parameters."""
    y, specs = _columns(targets, train)
    bias = np.mean(y, axis=0)
    residual = y - bias
    for kind, cross, eigval, eigvec, meta in _kernel_candidates(frame, train,
                                                                  regs, kernels):
        projection = eigvec.T @ residual
        for reg in regs:
            coeff = eigvec @ (projection / (eigval[:, None] + reg))
            scores = cross @ coeff + bias
            if not np.isfinite(scores).all():
                raise ValueError('GAMER_PAYOFF_PRECISION_NONFINITE_CLASS_SCORES')
            for decoder in DECODERS:
                yield {'kind': kind, 'reg': float(reg), 'decoder': decoder,
                       'predictions': _decode(scores, specs, decoder),
                       'coeff': coeff, 'bias': bias, 'specs': specs,
                       'train': np.asarray(train, dtype=int), 'meta': meta,
                       'feature_sha256': hashlib.sha256(np.ascontiguousarray(
                           frame[np.asarray(train, dtype=int)], dtype=np.float32).tobytes()).hexdigest()}


def _operand_metric(target, predicted, indices, train_values):
    ix = np.asarray(indices, dtype=int)
    truth = target[ix]
    got = predicted[ix]
    error = np.abs(truth - got)
    seen = np.isin(truth, train_values)
    return {'cases': int(len(ix)), 'exact': float(np.mean(error == 0)),
            'exact_cases': int(np.sum(error == 0)),
            'mae': float(np.mean(error)),
            'p90_abs_error': float(np.quantile(error, .9)),
            'train_supported_fraction': float(np.mean(seen)),
            'seen_exact': float(np.mean((error == 0)[seen])) if seen.any() else None,
            'unseen_exact': float(np.mean((error == 0)[~seen])) if (~seen).any() else None,
            'unseen_cases': int(np.sum(~seen)),
            'mean_signed_bias': float(np.mean(got - truth)),
            'predicted_range': [int(got.min()), int(got.max())]}


def _rank(core, zero):
    """Never trade zero-anchor competence away for aggregate core accuracy."""
    return (min(core['exact'], zero['exact']),
            .8*core['exact'] + .2*zero['exact'],
            core['exact'], zero['exact'],
            -.8*core['mae'] - .2*zero['mae'])


def _fit_select(views, labels, ix, *, views_to_try=VIEWS, regs=REGS,
                kernels=KERNELS, panels=('train_independent', 'train')):
    """All 4 selected heads chosen exclusively from disjoint DEV profiles."""
    targets = _integer_targets(labels)
    best = {k: None for k in PAYOFFS}
    lexical = {k: None for k in PAYOFFS}
    candidate_count = 0
    for view in tuple(views_to_try) + ('lexical_numeric',):
        if view not in views:
            raise ValueError('GAMER_PAYOFF_PRECISION_MISSING_FEATURE_VIEW')
        frame = views[view]
        for train_panel in panels:
            tr = ix[train_panel]
            for candidate in _model_outputs(frame, targets, tr, regs=regs,
                                             kernels=kernels):
                candidate_count += 1
                for name in PAYOFFS:
                    result = candidate['predictions'][name]
                    support = targets[name][tr]
                    core = _operand_metric(targets[name], result,
                                           ix['dev_independent'], support)
                    zero = _operand_metric(targets[name], result,
                                           ix['dev_zero_anchor'], support)
                    rank = _rank(core, zero)
                    current = lexical if view == 'lexical_numeric' else best
                    if current[name] is None or rank > current[name]['rank']:
                        current[name] = {'rank': rank, 'view': view,
                                'train_panel': train_panel,
                                'kind': candidate['kind'],
                                'reg': candidate['reg'],
                                'decoder': candidate['decoder'],
                                'dev_independent': core, 'dev_zero_anchor': zero,
                                'predicted': result.copy(),
                                'model': {key: candidate[key] for key in
                                          ('coeff','bias','specs','train','meta','feature_sha256')}}
    if any(value is None for value in best.values()):
        raise ValueError('GAMER_PAYOFF_PRECISION_INCOMPLETE_MODEL_SELECTION')
    detail = {}; predictions = {}
    for name in PAYOFFS:
        selected, ctrl = best[name], lexical[name]
        pred = selected['predicted']
        core, zero = ( _operand_metric(targets[name], pred, ix['heldout_' + panel],
                      targets[name][ix[selected['train_panel']]]) for panel in PANELS )
        detail[name] = {
            'selection': {k:selected[k] for k in ('view','train_panel','kind','reg','decoder')},
            'development': {panel:selected['dev_'+panel] for panel in PANELS},
            'heldout': dict(zip(PANELS, (core, zero))),
            'lexical_control': {panel:_operand_metric(targets[name], ctrl['predicted'],
               ix['heldout_' + panel], targets[name][ix[ctrl['train_panel']]])
                                for panel in PANELS},
            'lexical_selection': {k:ctrl[k] for k in ('train_panel','kind','reg','decoder')},
            'train_supported_unique_integer_values': int(len(np.unique(
                   targets[name][ix[selected['train_panel']]]))) }
        predictions[name] = pred.copy()
    return detail, predictions, best, candidate_count


def _continuous_control(views, labels, ix, prior):
    """Replay previous layer-9 continuous readouts on same freshly captured FP32."""
    output, details = {}, {}
    for name in PAYOFFS:
        item = prior['stages']['layer_09']['tasks'][name]
        view = item['dev_selected_view']
        selection = item['selected_readout']
        result = parent._ridge_tasks(views[view], labels, ix['train_independent'],
                      (name,), selection['kernel'],selection['regularization'])[name]
        output[name] = np.rint(result).astype(np.int64)
        targets = _integer_targets(labels)[name]
        details[name] = {
          'view': view,
          'original_continuous_heldout_mae': item['heldout']['mae'],
          'replayed_continuous_heldout_mae': float(np.mean(np.abs(
                     result[ix['heldout_independent']] - targets[ix['heldout_independent']]))),
          'rounded_integer': {panel:_operand_metric(targets, output[name],
                    ix['heldout_' + panel],targets[ix['train_independent']])
                    for panel in PANELS}}
    return output, details


def _q_readout(views, labels, ix, old, source):
    """Retain frozen layer-9 q baseline; q is not given as a hidden oracle."""
    item = old['stages']['layer_09']['tasks']['q']
    frame = views[item['dev_selected_view']]
    cfg = item['selected_readout']
    return parent._ridge_tasks(frame, labels, ix['train_independent'],
                 ('q',), cfg['kernel'], cfg['regularization'])['q']


def _composition(labels, payoff_estimates, qhat, ix):
    """Float and exact Fraction recomposition; exact-q baseline isolates payoffs."""
    q = labels['q']
    t, r, p, s = (np.asarray(payoff_estimates[key], dtype=np.float64)
                 for key in ('T', 'R', 'P', 'S'))
    for arr in (t,r,p,s):
        if not np.isfinite(arr).all():
            raise ValueError('GAMER_PAYOFF_PRECISION_NONFINITE_RECOMPOSITION')
    signed = lambda belief: belief * (t-r) + (1-belief) * (p-s)
    original = np.asarray(labels['signed_margin'], dtype=np.float64)
    outputs = {'exact_q_decoded_integer_payoffs': signed(q),
               'decoded_q_decoded_integer_payoffs': signed(qhat),
               'always_zero': np.zeros_like(q)}
    exact = q*(labels['T']-labels['R'])+(1-q)*(labels['P']-labels['S'])
    if not np.allclose(exact, original, atol=1e-11, rtol=0):
        raise ValueError('GAMER_PAYOFF_PRECISION_ORACLE_DISAGREEMENT')
    outputs['exact_parsed_operands_exact_formula'] = exact
    result = {}
    for panel in PANELS:
        ids = ix['heldout_'+panel]
        result[panel] = {name:parent._error(original, pred, ids)
                         for name,pred in outputs.items()}
        if result[panel]['exact_parsed_operands_exact_formula']['mae'] > 1e-10:
            raise ValueError('GAMER_PAYOFF_PRECISION_EXACT_FORMULA_FAILED')
    return result


def _triples(tags, indices, labels, estimates, qhat):
    """Strict exact integer operands required for zero-line certification."""
    cases = defaultdict(list)
    for i in indices:
        i = int(i); t = tags[i]
        if t['panel'] != 'zero_anchor':
            raise ValueError('GAMER_PAYOFF_PRECISION_NOT_AN_ANCHOR')
        cases[(t['profile'],t['surface'])].append(i)
    if len(cases)!=8 or any(len(ids)!=3 for ids in cases.values()):
        raise ValueError('GAMER_PAYOFF_PRECISION_INCOMPLETE_ANCHOR_TRIPLES')
    all_exact = np.ones(len(labels['q']),dtype=bool)
    for key in PAYOFFS:
        all_exact &= (np.asarray(estimates[key]) == labels[key])
    complete = 0; exact_zero = 0; exact_zero_from_model_q = 0
    for ids in cases.values():
        if all(all_exact[i] for i in ids):
            complete += 1
        zeros = [i for i in ids if labels['zero'][i] >.5]
        if len(zeros)!=1:
            raise ValueError('GAMER_PAYOFF_PRECISION_BAD_ZERO_TRIPLE')
        i = zeros[0]
        m = (labels['q'][i]*(estimates['T'][i]-estimates['R'][i])+
            (1-labels['q'][i])*(estimates['P'][i]-estimates['S'][i]))
        q_m = (qhat[i]*(estimates['T'][i]-estimates['R'][i])+
               (1-qhat[i])*(estimates['P'][i]-estimates['S'][i]))
        exact_zero += int(abs(m)<1e-10)
        exact_zero_from_model_q += int(abs(q_m)<1e-10)
    return {'triples':len(cases),'all_four_payoffs_exact_all_three_cases':complete,
            'strict_exact_zero_with_parsed_q':exact_zero,
            'strict_exact_zero_with_decoded_q':exact_zero_from_model_q,
            'strict_full_triple_rate': complete/len(cases),
            'zero_line_accepted': complete==len(cases) and exact_zero==len(cases)}


def _counterfactuals(deck,tags,ix,labels,estimates):
    """One-operand edit response, including spurious drift in the other three."""
    by_id = {c.id:i for i,c in enumerate(deck)}
    output = defaultdict(list)
    for i in ix['heldout_independent']:
        i=int(i); tag=tags[i]
        if tag['edit']=='base' or tag['changed'] not in PAYOFFS: continue
        j=by_id[tag['base_id']]
        operand_name=tag['changed']
        if any(labels[k][i]!=labels[k][j] for k in ('q',)+PAYOFFS if k!=operand_name):
            raise ValueError('GAMER_PAYOFF_PRECISION_NOT_ONE_OPERAND_CHANGE')
        oracle = labels[operand_name][i]-labels[operand_name][j]
        predicted = estimates[operand_name][i]-estimates[operand_name][j]
        output[operand_name].append((oracle, predicted,
               [estimates[k][i]-estimates[k][j] for k in PAYOFFS if k!=operand_name]))
    results={}
    for name in PAYOFFS:
        cases=output[name]
        if len(cases) != 2*parent.CORE_GROUPS['heldout']:
            raise ValueError('GAMER_PAYOFF_PRECISION_MISSING_COUNTERFACTUALS')
        results[name] = {
          'cases':len(cases),
          'exact_operand_delta_fraction':float(np.mean([a==b for a,b,_ in cases])),
          'correct_operand_direction_fraction':float(np.mean([
                                  np.sign(a)==np.sign(b) for a,b,_ in cases])),
          'operand_delta_mae':float(np.mean([abs(a-b) for a,b,_ in cases])),
          'other_payoffs_unchanged_all_fraction':float(np.mean([
                                  all(v==0 for v in ds) for _,__,ds in cases])),
          'spurious_other_payoff_abs_change':float(np.mean([
                                   abs(v) for _,__,ds in cases for v in ds]))}
    return results


def _replay_checkpoint(path, frame, *, expected_source_sha=None):
    """Deterministically replay saved train-only kernel weights; never refit."""
    with np.load(path, allow_pickle=False) as ckpt:
        meta=json.loads(str(ckpt['metadata'].item()))
        ref=np.asarray(ckpt['training_feature'], dtype=np.float32)
        indices=np.asarray(ckpt['train_indices'], dtype=int)
        alpha=np.asarray(ckpt['alpha'], dtype=np.float64)
        bias=np.asarray(ckpt['bias'], dtype=np.float64)
    if (meta.get('schema')!=SCHEMA or
        (expected_source_sha is not None and
         meta.get('source_report_sha256')!=expected_source_sha) or
        not np.array_equal(ref,np.asarray(frame[indices],dtype=np.float32)) or
        hashlib.sha256(ref.tobytes()).hexdigest()!=meta.get('train_feature_sha256')):
        raise ValueError('GAMER_PAYOFF_PRECISION_CHECKPOINT_REPLAY_LINEAGE_CHANGED')
    cross,_,_=operand._encode_train_map(frame,indices,meta['kernel'])
    scores=cross.astype(np.float64)@alpha+bias
    specs={}
    for key,item in meta['specs'].items():
        name,task=key.split('::')
        specs[(name,task)]=(int(item['start']),int(item['stop']),
                             np.asarray(item['classes'],dtype=int))
    result=_decode(scores,specs,meta['decoder'])
    if any(len(result[name])!=len(frame) for name in PAYOFFS):
        raise ValueError('GAMER_PAYOFF_PRECISION_CHECKPOINT_BAD_SHAPE')
    return result


def _save_checkpoint(path, chosen, frame, name, source_sha):
    """Save replayable weights with a complete train-only feature basis."""
    model=chosen['model']; specs=model['specs']; meta={
      'schema':SCHEMA,'payoff':name,'source_report_sha256':source_sha,
      'view':chosen['view'],'kernel':chosen['kind'],
      'regularization':chosen['reg'],'decoder':chosen['decoder'],
      'train_panel':chosen['train_panel'],
      'specs': {f'{key[0]}::{key[1]}':{'start':int(a),'stop':int(b),
                    'classes':xs.astype(int).tolist()}
             for key,(a,b,xs) in specs.items()},
      'train_feature_sha256':model['feature_sha256']}
    reference=np.ascontiguousarray(frame[model['train']], dtype=np.float32)
    if hashlib.sha256(reference.tobytes()).hexdigest()!=model['feature_sha256']:
        raise ValueError('GAMER_PAYOFF_PRECISION_CHECKPOINT_FEATURE_CHANGED')
    np.savez_compressed(path, training_feature=reference,
       train_indices=model['train'],alpha=model['coeff'],bias=model['bias'],
       metadata=np.asarray(json.dumps(meta,sort_keys=True)))
    # Ensure serialization, metadata, dimensions and full-dataset replay agree.
    with np.load(path, allow_pickle=False) as ckpt:
        saved=json.loads(str(ckpt['metadata'].item()))
        if saved['payoff']!=name or ckpt['alpha'].shape[1]!=len(ckpt['bias']):
            raise ValueError('GAMER_PAYOFF_PRECISION_BAD_CHECKPOINT_REPLAY')
        if not np.array_equal(ckpt['training_feature'],reference):
            raise ValueError('GAMER_PAYOFF_PRECISION_CHECKPOINT_FEATURE_CHANGED')
    replay=_replay_checkpoint(path,frame,expected_source_sha=source_sha)
    if not np.array_equal(replay[name],chosen['predicted']):
        raise ValueError('GAMER_PAYOFF_PRECISION_CHECKPOINT_PREDICTION_MISMATCH')
    return {'path':str(path),'sha256':token._sha(path),
            'replay_matches_selected_predictions':True,
            'type':'DIAGNOSTIC_INTEGER_READOUT_NO_PRODUCTION_UPDATE'}


def run(args):
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_handoff as gamer
    if args.capture_batch<1:
        raise ValueError('GAMER_PAYOFF_PRECISION_INVALID_CAPTURE_BATCH')
    source = _source(args.deconfounded)
    previous = source['source']; origin=previous['origin']
    protected = [source['root'], previous['path'].parent,
       previous['parent']['pool_path'].parent,
       previous['parent']['source']['report_path'].parent,
       previous['parent']['source']['source']['v2_report_path'].parent,
       origin['adapter'], origin['ckpt'].parent,origin['training'],
       origin['source'],origin['capture'],origin['audit_path'].parent]
    out=c0sign._output(args.output,protected)
    if out.exists():
        raise FileExistsError('GAMER_PAYOFF_PRECISION_OUTPUT_EXISTS')
    deck,tags,ix,profiles,_ = parent._make_deck()
    if oracle.deck_hash(deck)!=source['report']['deck_sha256']:
        raise ValueError('GAMER_PAYOFF_PRECISION_DECK_CHANGED')
    labels=operand._targets(deck)
    _integer_targets(labels)
    sha_before=token._sha(source['report_path'])
    print(f'GAMER PAYOFF PRECISION: frozen LoRA {STEP}; {len(deck)} cases; '
          'fresh float32 layer-09 capture; integer vs digit vs rounded regression',flush=True)
    frames=parent._captured_features(previous,deck,device=args.device,
                                      batch_size=args.capture_batch)
    views=frames['layer_09']
    # Comparing the saved archival float16 raster validates identical model
    # inputs but deliberately does not use the quantized features for fitting.
    with np.load(source['archive'],allow_pickle=False) as old:
        cached=old['layer_09__belief'].astype(np.float32)
        difference=float(np.max(np.abs(cached-views['belief'])))
        if difference>.1:
            raise ValueError('GAMER_PAYOFF_PRECISION_CAPTURE_PARITY_FAILURE')
    if (token._sha(source['report_path'])!=sha_before or
        stress._verify_snapshot(previous['snapshot'], STEP)!=previous['snapshot_sha'] or
        token._sha(previous['zero_head'])!=source['report']['frozen_zero_head_sha256']):
        raise ValueError('GAMER_PAYOFF_PRECISION_SOURCE_MUTATED_DURING_CAPTURE')
    print('  fitting train-only categorical and digit-wise payoff readouts',flush=True)
    details,pred,selected,candidates=_fit_select(views,labels,ix)
    continuous,continuous_detail=_continuous_control(views,labels,ix,source['report'])
    qhat=_q_readout(views,labels,ix,source['report'],previous)
    report = {
      'schema':SCHEMA,'status':'COMPLETE_FROZEN_PAYOFF_PRECISION_DIAGNOSTIC',
      'promotion':'BLOCKED_DIAGNOSTIC_ONLY','no_lora_training':True,
      'no_c0_training':True,'no_zero_detector_mutation':True,
      'frozen_lora_step':STEP,'frozen_lora_sha256':previous['snapshot_sha'],
      'frozen_zero_head_sha256':source['report']['frozen_zero_head_sha256'],
      'deconfounded_report_sha256':sha_before,
      'deconfounded_report_path':str(source['report_path']),
      'deck_sha256':source['report']['deck_sha256'],
      'case_counts':{k:len(ix[k]) for k in ('train','dev','heldout')},
      'feature_policy':{'stage':'layer_09','capture_dtype':'float32',
               'previous_archive_dtype':'float16',
               'max_float16_archive_parity_delta':difference,
               'select_only_dev':True,'independent_vs_zero_panels_separate':True},
      'selection_candidates_evaluated':candidates,
      'payoffs':details,'continuous_rounded_control':continuous_detail,
      'recomposition':{
         'integer_extraction':_composition(labels,pred,qhat,ix),
         'rounded_continuous':_composition(labels,continuous,qhat,ix)},
      'zero_anchor_triples':_triples(tags,ix['heldout_zero_anchor'],labels,pred,qhat),
      'rounded_continuous_zero_anchor_triples':_triples(
                 tags,ix['heldout_zero_anchor'],labels,continuous,qhat),
      'single_payoff_counterfactuals':_counterfactuals(deck,tags,ix,labels,pred),
      'rounded_continuous_counterfactuals':_counterfactuals(
                 deck,tags,ix,labels,continuous),
      'exact_oracle_control':'PROMPT_PARSED_FRACTIONS_PLUS_DETERMINISTIC_ARITHMETIC',
      'interpretation': ('Exact-decoding accuracy is the acceptance target. '
         'Payoff readouts are frozen-LoRA probes, not proof that Qwen performs '
         'exact arithmetic. Independent and anchored-zero profiles remain separate; '
         'heldout cases do not influence training or model selection. Float32 '
         'recapture avoids precision loss from the previous float16 archive.')}
    # Acceptance cannot be earned by a low MAE or a good independent-panel result.
    accepted = (all(details[k]['heldout'][p]['exact']>=.95 for k in PAYOFFS
                      for p in PANELS) and
                report['zero_anchor_triples']['zero_line_accepted'] and
                all(report['recomposition']['integer_extraction'][p]
                   ['exact_q_decoded_integer_payoffs']['within_0_05']>=.95
                   for p in PANELS) and
                all(report['single_payoff_counterfactuals'][k]
                         ['exact_operand_delta_fraction']>=.95 for k in PAYOFFS))
    report['component_gate']={'passed':bool(accepted),
          'threshold':.95,'promotion':'BLOCKED_DIAGNOSTIC_ONLY'}
    # Never promote even when diagnostic components pass.
    if (token._sha(source['report_path'])!=sha_before or
        stress._verify_snapshot(previous['snapshot'], STEP)!=previous['snapshot_sha'] or
        token._sha(previous['zero_head'])!=source['report']['frozen_zero_head_sha256']):
        raise ValueError('GAMER_PAYOFF_PRECISION_SOURCE_CHANGED_BEFORE_REPORT')
    out.mkdir(parents=True,exist_ok=False)
    checkpoints={}
    for name in PAYOFFS:
        item=selected[name]
        ckpt=out/f'{name.lower()}-integer-diagnostic.npz'
        checkpoints[name]=_save_checkpoint(ckpt,item,views[item['view']],name,sha_before)
    report['diagnostic_checkpoints']=checkpoints
    # Save small, unquantized stage views to support independent replay.
    archive={f'layer_09__{k}':np.asarray(v,dtype=np.float32)
             for k,v in views.items()}
    np.savez_compressed(out/'frozen_layer09_float32.npz',**archive)
    report['fresh_feature_archive']={'path':str(out/'frozen_layer09_float32.npz'),
                         'sha256':token._sha(out/'frozen_layer09_float32.npz'),
                         'dtype':'float32',
                         'arrays':{key:list(value.shape) for key,value in archive.items()}}
    gamer.atomic_json(out/'final_report.json',report)
    print('GAMER PAYOFF PRECISION COMPLETE:',out/'final_report.json',flush=True)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deconfounded',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--device',default='cuda',choices=('cpu','cuda','directml'))
    parser.add_argument('--capture-batch',type=int,default=2)
    return run(parser.parse_args(argv))


if __name__=='__main__':
    main()
