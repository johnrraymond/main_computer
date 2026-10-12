#!/usr/bin/env python3
"""Paired, frozen-backbone arithmetic probe using log-length unary tallies.

The original, sealed 1,000-question C0 bank is unchanged. Every positive
integer n is rendered as n.bit_length() separated tally bars: this is exactly
1+floor(log2(n)). Original labels are preserved *deliberately*, despite the
fact that tally-length addition is generally not original-number addition.
No CLEF, optimizer, residual adapter, or training is used.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import inspect
import json
import os
from pathlib import Path

import nanojev_frozen_arithmetic_direct_logp_probe as direct
from nanojev_arithmetic_task_bank import render_prompt

SCHEMA = 'nanojev.frozen-arithmetic-direct-logp-logunary.v1'
DEFAULT_OUTPUT = Path(r'C:\Users\subsi\NanoJev\runs\frozen_arithmetic_direct_logp_logunary_v1')
UNIT = '|'


def emit(event, **data):
    direct.emit(event, **data)


def length(n: int) -> int:
    """1 + floor(log2(n)) without floating-point rounding; n must be positive."""
    if type(n) is not int or n < 1:
        raise ValueError('FROZEN_LOGUNARY_REQUIRES_POSITIVE_INTEGER: ' + str(n))
    return n.bit_length()


def tally(n: int) -> str:
    return ' '.join([UNIT] * length(n))


def render_logunary(case: dict) -> str:
    return render_prompt(*(tally(case[key]) for key in ('a', 'b', 'threshold')))


def literal_displayed_gold(case: dict) -> str:
    a, b, t = (length(case[k]) for k in ('a', 'b', 'threshold'))
    return 'LESS' if a+b<t else ('MORE' if a+b>t else 'EQUAL')


def has_encoding_collision(case: dict) -> bool:
    values = tuple(case[k] for k in ('a', 'b', 'threshold'))
    return any(values[i] != values[j] and length(values[i]) == length(values[j])
               for i in range(3) for j in range(i+1, 3))


def digest(s: str) -> str:
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def renderer_fingerprint() -> str:
    body = (SCHEMA, UNIT, inspect.getsource(length), inspect.getsource(tally),
            inspect.getsource(render_logunary), inspect.getsource(literal_displayed_gold),
            inspect.getsource(has_encoding_collision), inspect.getsource(render_prompt))
    return digest(json.dumps(body, separators=(',', ':')))


def parse(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline-root', type=Path, default=direct.DEFAULT_OUTPUT,
                   help='Existing sealed original-number C0 probe and 1000-question bank')
    p.add_argument('--output-root', type=Path, default=DEFAULT_OUTPUT)
    p.add_argument('--legacy-bank-dir', type=Path, default=direct.DEFAULT_BANK)
    p.add_argument('--v2-bank-dir', type=Path, default=direct.V2_BANK)
    p.add_argument('--count', type=int, default=1000)
    p.add_argument('--seed', type=int, default=direct.DEFAULT_SEED)
    p.add_argument('--models', nargs='+', choices=tuple(direct.SOURCES), default=list(direct.SOURCES))
    p.add_argument('--device', choices=('auto', 'cpu', 'cuda'), default='auto')
    p.add_argument('--dtype', choices=('auto', 'float32', 'float16', 'bfloat16'), default='auto')
    p.add_argument('--allow-model-download', action='store_true')
    p.add_argument('--report-every', type=int, default=50)
    p.add_argument('--prepare-only', action='store_true')
    p.add_argument('--resume', action='store_true')
    a = p.parse_args(argv)
    if a.count < 3 or a.report_every < 1 or len(set(a.models)) != len(a.models):
        p.error('invalid count, report interval, or duplicate models')
    return a


def paths(root: Path, model_name: str):
    return (root/'results'/f'{model_name}.C0.logunary.jsonl',
            root/'results'/f'{model_name}.C0.logunary.meta.json')


def expected_meta(model_name, bank_sha, model_info):
    return {'schema': SCHEMA, 'model': model_name, 'source': direct.SOURCES[model_name],
            'level': 'C0', 'numeric_transform': 'repeat_tally_bit_length',
            'encoding': 'n>0 -> n.bit_length() separated ASCII | symbols; no digit literals',
            'renderer_sha256': renderer_fingerprint(),
            'original_bank_sha256': bank_sha, 'original_gold_rule': 'a+b COMPARE threshold',
            'displayed_gold_rule': 'INTENTIONALLY IGNORED; preserve original integers gold',
            'score_contract': 'raw frozen LM prefix+candidate conditional logP, sum and per-token mean',
            'loaded_revision': model_info}


def make_record(case, score):
    result = direct.record(case, score, 'C0')
    result['prompt_sha256'] = digest(render_logunary(case))
    result['numeric_transform'] = 'logunary'
    result['tally_lengths'] = {k: length(case[k]) for k in ('a', 'b', 'threshold')}
    result['literal_displayed_gold'] = literal_displayed_gold(case)
    result['deliberate_label_mismatch'] = result['literal_displayed_gold'] != case['gold']
    result['encoding_collision'] = has_encoding_collision(case)
    return result


def check_records(result_path, meta_path, expected, cases, *, complete=False):
    if not result_path.exists() or not meta_path.exists():
        raise RuntimeError('FROZEN_LOGUNARY_INCOMPLETE_RESULTS_PAIR')
    if json.loads(meta_path.read_text(encoding='utf-8')) != expected:
        raise RuntimeError('FROZEN_LOGUNARY_IDENTITY_DRIFT')
    rows = []
    for idx, line in enumerate(result_path.read_text(encoding='utf-8').splitlines()):
        if idx >= len(cases):
            raise RuntimeError('FROZEN_LOGUNARY_EXTRA_SCORED_CASES')
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError('FROZEN_LOGUNARY_TRUNCATED_RECORD') from exc
        source = cases[idx]
        if (item.get('id') != source['id'] or item.get('gold') != source['gold']
            or any(item.get(k) != source[k] for k in ('a', 'b', 'threshold'))
            or item.get('prompt_sha256') != digest(render_logunary(source))
            or item.get('numeric_transform') != 'logunary'
            or item.get('tally_lengths') != {k:length(source[k]) for k in ('a','b','threshold')}
            or item.get('literal_displayed_gold') != literal_displayed_gold(source)
            or item.get('deliberate_label_mismatch') != (literal_displayed_gold(source) != source['gold'])
            or item.get('encoding_collision') != has_encoding_collision(source)
            or item.get('sum_pred') != direct.choose(item['sum_probs'])
            or item.get('mean_pred') != direct.choose(item['mean_probs'])):
            raise RuntimeError('FROZEN_LOGUNARY_SAVED_RECORD_DRIFT: ' + str(idx))
        rows.append(item)
    if complete and len(rows) != len(cases):
        raise RuntimeError('FROZEN_LOGUNARY_INCOMPLETE_PROBE')
    return rows


def paired_report(cases, original, new):
    if len(cases) != len(original) or len(cases) != len(new):
        raise RuntimeError('FROZEN_LOGUNARY_PAIR_LENGTH_MISMATCH')
    report = {'original_c0': direct.summary(original), 'logunary_c0': direct.summary(new),
              'paired': {}, 'encoding_collision_cases': sum(r['encoding_collision'] for r in new),
              'deliberate_label_mismatch_cases': sum(r['deliberate_label_mismatch'] for r in new),
              'literal_displayed_label_counts': dict(Counter(r['literal_displayed_gold'] for r in new)),
              'distinct_tally_lengths': sorted(set(n for r in new for n in r['tally_lengths'].values()))}
    for mode in ('sum', 'mean'):
        win = lose = flips = 0
        for c, before, after in zip(cases, original, new):
            if c['id'] != before['id'] or c['id'] != after['id'] or before['gold'] != after['gold']:
                raise RuntimeError('FROZEN_LOGUNARY_PAIR_IDENTITY_DRIFT')
            was = before[mode+'_pred'] == c['gold']
            is_now = after[mode+'_pred'] == c['gold']
            win += int(is_now and not was)
            lose += int(was and not is_now)
            flips += int(before[mode+'_pred'] != after[mode+'_pred'])
        report['paired'][mode] = {'improved_cases':win, 'regressed_cases':lose,
                                  'prediction_flips':flips, 'net_correct_gain':win-lose,
                                  'accuracy_gain':(win-lose)/len(cases)}
    return report


def atomic_json(path, value):
    tmp = path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(value, sort_keys=True, indent=2)+'\n', encoding='utf-8')
    os.replace(tmp, path)


def run(args):
    baseline_root = args.baseline_root.expanduser().resolve(strict=True)
    output_root = args.output_root.expanduser().resolve()
    if output_root == baseline_root:
        raise RuntimeError('FROZEN_LOGUNARY_OUTPUT_MUST_DIFFER_FROM_ORIGINAL')
    cases, manifest = direct.load_bank(baseline_root, args.legacy_bank_dir,
                                       args.v2_bank_dir, args.count, args.seed)
    bank_sha = manifest['questions_sha256']
    if any(any(type(case[k]) is not int or case[k] < 1 for k in ('a','b','threshold')) for case in cases):
        raise RuntimeError('FROZEN_LOGUNARY_REQUIRES_POSITIVE_BANK_NUMBERS')
    collision_count = sum(has_encoding_collision(c) for c in cases)
    mismatch_count = sum(literal_displayed_gold(c) != c['gold'] for c in cases)
    visible_labels = dict(Counter(literal_displayed_gold(c) for c in cases))
    emit('frozen_logunary_bank_verified',bank_sha256=bank_sha,count=len(cases),
         encoding_collision_cases=collision_count,
         deliberate_label_mismatch_cases=mismatch_count,
         displayed_label_counts=visible_labels,training=False)
    if args.prepare_only:
        emit('frozen_logunary_example',source={k:cases[0][k] for k in ('a','b','threshold','gold')},
             tally_lengths={k:length(cases[0][k]) for k in ('a','b','threshold')},
             prompt=render_logunary(cases[0]),model_loaded=False)
        return
    (output_root/'results').mkdir(parents=True, exist_ok=True)
    summary_rows = []
    for name in args.models:
        original_path, original_meta, original_expected = direct.checkpoint(
            baseline_root,name,'C0',bank_sha,cases,None)
        original = direct.inspect_results(original_path,original_meta,
                                          original_expected,cases,require_completed=True)
        torch, model, tokenizer, device, info = direct.load_source(name,args)
        path, meta_path = paths(output_root,name)
        expected = expected_meta(name,bank_sha,info)
        if path.exists() or meta_path.exists():
            if not args.resume:
                raise RuntimeError('FROZEN_LOGUNARY_OUTPUT_EXISTS_USE_RESUME: '+str(path))
            collected = check_records(path,meta_path,expected,cases)
        else:
            collected = []
            atomic_json(meta_path,expected)
        emit('frozen_logunary_scoring_start',model=name,completed=len(collected),total=len(cases))
        with path.open('a',encoding='utf-8',newline='\n') as stream:
            for idx in range(len(collected),len(cases)):
                case = cases[idx]
                prediction = direct.score_one(torch=torch,model=model,tokenizer=tokenizer,
                                              prompt=render_logunary(case),device=device)
                item = make_record(case,prediction)
                stream.write(json.dumps(item,sort_keys=True,separators=(',',':'))+'\n')
                stream.flush()
                if (idx+1)%args.report_every == 0:
                    emit('frozen_logunary_progress',model=name,done=idx+1,total=len(cases))
        scored = check_records(path,meta_path,expected,cases,complete=True)
        report = paired_report(cases,original,scored)
        atomic_json(output_root/'results'/f'{name}.C0.logunary.summary.json',
                    {'schema':SCHEMA,'model':name,'bank_sha256':bank_sha,'results':report})
        for mode in ('sum','mean'):
            before = report['original_c0']['modes'][mode]
            after = report['logunary_c0']['modes'][mode]
            comp = report['paired'][mode]
            result = {'model':name,'level':'C0','transformation':'log2_length_tally_original_gold',
                      'scoring':mode,'questions':len(cases),
                      'raw_accuracy':before['accuracy'],'tally_accuracy':after['accuracy'],
                      'accuracy_gain':comp['accuracy_gain'],
                      'raw_ce':before['cross_entropy'],'tally_ce':after['cross_entropy'],
                      'prediction_flips':comp['prediction_flips'],
                      'improved':comp['improved_cases'],'regressed':comp['regressed_cases'],
                      'encoding_collision_cases':report['encoding_collision_cases'],
                      'deliberate_label_mismatch_cases':report['deliberate_label_mismatch_cases'],
                      'displayed_label_counts':json.dumps(report['literal_displayed_label_counts'],sort_keys=True),
                      'tally_predicted_counts':json.dumps(after['predicted_counts'],sort_keys=True)}
            summary_rows.append(result)
            emit('frozen_logunary_model_result',**result)
        del model
        if device=='cuda':
            torch.cuda.empty_cache()
    atomic_json(output_root/'summary.json',
                {'schema':SCHEMA,'bank_sha256':bank_sha,'renderer_sha256':renderer_fingerprint(),
                 'rows':summary_rows})
    target = output_root/'summary.csv'
    tmp = target.with_name(target.name+'.tmp')
    with tmp.open('w',encoding='utf-8',newline='') as f:
        writer = csv.DictWriter(f,fieldnames=list(summary_rows[0]))
        writer.writeheader();writer.writerows(summary_rows)
    os.replace(tmp,target)
    emit('frozen_logunary_probe_complete',questions=len(cases),summary=str(target))


if __name__ == '__main__':
    try:
        run(parse())
    except Exception as exc:
        emit('frozen_logunary_probe_error',error=str(exc))
        raise SystemExit(1)
