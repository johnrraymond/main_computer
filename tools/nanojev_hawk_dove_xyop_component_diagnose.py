#!/usr/bin/env python3
"""Read-only V3 sign/exponent/mantissa diagnosis for the XYOP Hawk-Dove run.

Evaluates the same balanced *development* rows and weighted visible hints used by
XYOP advancement, including tiers excluded from the training curriculum. No
optimizer steps, checkpoint writes, deterministic predictors, or test-split reads.

Example:
  python -m tools.nanojev_hawk_dove_xyop_component_diagnose --xyop-mix "*:*=011:1" --device cuda
  python -m tools.nanojev_hawk_dove_xyop_component_diagnose --xyop-mix "*:*=011:1" --tiers T02,T04,T05,T06 --device cuda
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import median

from tools import nanojev_hawk_dove_xyop_mask_train as xyop
from tools import nanojev_hawk_dove_scientific_train as science
from tools import nanojev_hawk_dove_cached_train as cached
from tools import nanojev_hawk_dove_numeric_adapter_train as adapter
from tools import nanojev_hawk_dove_numeric_sidecar_train as sidecar
from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_game_transform_experiment as ex
from tools import nanojev_game_math_curriculum_v10_runtime as rt

SCHEMA = 'nanojev.hawk-dove.xyop-v3-component-diagnosis.v1'
SIGNS = ('negative', 'zero', 'positive')


def tiers_arg(value):
    names = value.split(',')
    if not names or len(set(names)) != len(names) or any(t not in hd.TIERS for t in names):
        raise ValueError('INVALID_COMPONENT_DIAGNOSIS_TIERS')
    return tuple(t for t in hd.TIERS if t in names)


def _digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _oracle_value(sign_class, exponent_class, fraction, max_value):
    sign = (-1., 0., 1.)[int(sign_class)]
    if sign == 0.:
        return 0.
    return sign * 10. ** min(math.log10(float(max_value)),
                             max(0., float(exponent_class) + min(1., max(0., float(fraction)))))


def decode_component_rows(output, predictions, truths, patterns, weights, case_ids, sublevels,
                          max_value):
    """Make audited per-hidden-slot records from actual V3 output, not a sidecar.

    `patterns` and `weights` correspond one-to-one with the exact mixture-matched
    advancement tasks; revealed slots never enter hidden-component denominators.
    """
    import torch
    from tools import nanojev_altreg_math_v3_decoder_experiment as v3
    if set(output) < {'sign', 'exponent', 'fraction'}:
        raise ValueError('V3_COMPONENT_OUTPUT_MISSING')
    n = len(patterns)
    if len(truths) != n or len(weights) != n or len(predictions) != n or len(case_ids) != n or len(sublevels) != n:
        raise ValueError('V3_COMPONENT_ROW_COUNT_MISMATCH')
    sign_logits = output['sign'].detach().cpu()
    exponent_logits = output['exponent'].detach().cpu()
    fractions = output['fraction'].detach().cpu()
    if sign_logits.shape != (n,2,3) or exponent_logits.ndim != 3 or exponent_logits.shape[:2] != (n,2) or fractions.shape != (n,2):
        raise ValueError('V3_COMPONENT_LOGIT_SHAPE_DRIFT')
    # SQLite teacher rows expose x/y as decimal strings. The native development
    # scorer converts them with float() before numerical comparison; do the same
    # here, before tensor construction, to preserve exact scorer input semantics.
    numeric_truths = []
    for pair in truths:
        if isinstance(pair, (str, bytes)) or not hasattr(pair, '__len__') or len(pair) != 2:
            raise ValueError('V3_COMPONENT_BAD_NUMERIC_TARGET')
        try:
            values = (float(pair[0]), float(pair[1]))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError('V3_COMPONENT_BAD_NUMERIC_TARGET') from exc
        if not all(math.isfinite(value) for value in values):
            raise ValueError('V3_COMPONENT_BAD_NUMERIC_TARGET')
        numeric_truths.append(values)
    s_truth, e_truth, m_truth, nonzero = v3.target_parts(
        torch.tensor(numeric_truths, dtype=torch.float32),
        exponent_logits.shape[-1]-1, 'cpu')
    ps = sign_logits.argmax(-1)
    pe = exponent_logits.argmax(-1)
    results = []
    for i in range(n):
        if len(patterns[i]) != 3 or patterns[i][:2] == '11' or not math.isfinite(float(weights[i])) or weights[i] <= 0:
            raise ValueError('V3_COMPONENT_BAD_VISIBILITY_OR_WEIGHT')
        for slot,label in enumerate(('x','y')):
            if patterns[i][slot] == '1':
                continue  # The revealed value is never an evaluated answer.
            true = numeric_truths[i][slot]; pred = float(predictions[i][slot]);
            if not math.isfinite(pred):
                raise ValueError('V3_COMPONENT_NONFINITE_DECODE')
            s = int(s_truth[i,slot]); e = int(e_truth[i,slot]); m = float(m_truth[i,slot])
            p_s = int(ps[i,slot]); p_e = int(pe[i,slot]); p_m = float(fractions[i,slot])
            valid_exp = bool(nonzero[i,slot]);
            err = abs(pred - true)/max(1.,abs(true))
            result = {'case_id':case_ids[i], 'sublevel':int(sublevels[i]),
                 'visibility':patterns[i], 'slot':label, 'weight':float(weights[i]),
                 'true':true,'predicted':pred,'relative_error':err,
                 'within_1pct':err<=.01,'within_5pct':err<=.05,'within_10pct':err<=.10,
                 'true_sign':SIGNS[s],'predicted_sign':SIGNS[p_s], 'sign_correct':p_s==s,
                 'exponent_applicable':valid_exp,
                 'true_exponent':e if valid_exp else None,
                 'predicted_exponent':p_e if valid_exp else None,
                 'exponent_correct':(p_e==e) if valid_exp else None,
                 'true_mantissa':m if valid_exp else None,
                 'predicted_mantissa':p_m if valid_exp else None,
                 'mantissa_abs_error':abs(p_m-m) if valid_exp else None,
                 'oracle_sign_within_5pct':None,
                 'oracle_exponent_within_5pct':None,
                 'oracle_mantissa_within_5pct':None}
            # Interventions alter ONE component of the decoded V3 value, not
            # the input hint or the model weights. Diagnostic only.
            interventions = {
                'oracle_sign_within_5pct':_oracle_value(s,p_e,p_m,max_value),
                'oracle_exponent_within_5pct':_oracle_value(p_s,e,p_m,max_value),
                'oracle_mantissa_within_5pct':_oracle_value(p_s,p_e,m,max_value),
            }
            for k,v in interventions.items():
                if k == 'oracle_exponent_within_5pct' and not valid_exp:
                    continue
                if k == 'oracle_mantissa_within_5pct' and not valid_exp:
                    continue
                result[k] = abs(v-true)/max(1.,abs(true)) <= .05
            results.append(result)
    return results


def summarize(cases):
    """Weighted metrics, honoring XYOP's per-presentation mixture shares."""
    if not cases:
        return {'n':0,'weighted_n':0.,'note':'No hidden targets available'}
    w = sum(x['weight'] for x in cases)
    if w <= 0:raise ValueError('COMPONENT_ZERO_WEIGHT')
    def average(field):
        selected=[x for x in cases if x[field] is not None]
        total=sum(x['weight'] for x in selected)
        return sum(x['weight']*float(x[field]) for x in selected)/total if total else None
    errors=[x['relative_error'] for x in cases]
    return {'n':len(cases),'weighted_n':w,
            'within_1pct':average('within_1pct'),'within_5pct':average('within_5pct'),
            'within_10pct':average('within_10pct'),
            'mean_relative_error':average('relative_error'),
            'median_relative_error':median(errors),
            'sign_accuracy':average('sign_correct'),
            'exponent_accuracy_nonzero':average('exponent_correct'),
            'mantissa_mae_log10_nonzero':average('mantissa_abs_error'),
            'nonzero_n':sum(x['exponent_applicable'] for x in cases),
            'oracle_sign_5pct':average('oracle_sign_within_5pct'),
            'oracle_exponent_5pct_nonzero':average('oracle_exponent_within_5pct'),
            'oracle_mantissa_5pct_nonzero':average('oracle_mantissa_within_5pct'),
            'sign_confusion':{actual:{pred:sum(x['weight'] for x in cases
                         if x['true_sign']==actual and x['predicted_sign']==pred)
                         for pred in SIGNS} for actual in SIGNS}}


def diagnose_tier(model, rows, mixes, seed, max_value):
    """Keep the native masked development scorer and capture its true V3 logits."""
    original_predict=science.predict
    raw=[]
    def traced_predict(active_model,prompts):
        import torch
        with torch.inference_mode():
            out,_=ex.forward(active_model,prompts)
            guessed=active_model['v3'].compose_logits(out['sign'],out['exponent'],out['fraction'],max_value)
        values=[tuple(float(v) for v in row) for row in guessed.detach().cpu().tolist()]
        raw.append((prompts[:], {k:v.detach().cpu().clone() for k,v in out.items()
                                if k in ('sign','exponent','fraction')},values))
        return values
    science.predict=traced_predict
    try:
        # This is xyop.live_v3_only's conditional evaluator: same masking,
        # same frozen Qwen and V3, same 5%-threshold development scoring.
        advancement=science.eval_tier(model,rows)
    finally:
        science.predict=original_predict
    tasks=[]
    for row in rows:
        mix=xyop.resolved_mix(mixes,row['tier'],row['mask'])
        total=sum(w for _,w in mix)
        if not rt.token_eligible(model,row['prompt']):continue
        for pattern,weight in mix:
            if pattern[:2]!='11':
                tasks.append((row,pattern,weight/total))
    outputs=[]
    actual=[]
    for prompts,out,preds in raw:
        n=len(prompts)
        batch=tasks[len(actual):len(actual)+n]
        if n!=len(batch) or prompts!=[row['prompt'] for row,_,_ in batch]:
            raise RuntimeError('COMPONENT_ADVANCEMENT_INPUT_ORDER_DRIFT')
        actual.extend(batch)
        outputs.append((out,preds))
    if len(actual)!=len(tasks):
        raise RuntimeError('COMPONENT_ADVANCEMENT_OUTPUT_COUNT_DRIFT')
    import torch
    combined={name:torch.cat([out[name] for out,_ in outputs],0) for name in ('sign','exponent','fraction')} if outputs else None
    if combined is None: raise ValueError('COMPONENT_TIER_HAS_NO_ELIGIBLE_ROWS')
    results=decode_component_rows(combined,[v for _,p in outputs for v in p],
            [(row['x'],row['y']) for row,_,_ in tasks],
            [p for _,p,_ in tasks], [w for _,_,w in tasks],
            [row['case_id'] for row,_,_ in tasks],
            [row['mask'] for row,_,_ in tasks],max_value)
    grouped={slot:summarize([c for c in results if c['slot']==slot]) for slot in ('x','y')}
    groups={key:summarize([c for c in results if c['visibility']==key])
            for key in sorted({x['visibility'] for x in results})}
    sublevels={str(key):{slot:summarize([c for c in results if c['sublevel']==key and c['slot']==slot])
                  for slot in ('x','y')} for key in sorted({x['sublevel'] for x in results})}
    # Reconstructed V3 accuracy must equal the live gate's hidden X/Y scores.
    for slot,key in (('x','x_5pct'),('y','y_5pct')):
        got=grouped[slot]['within_5pct'] if grouped[slot]['n'] else None
        target=advancement[key]
        if target is None and got is None:continue
        if got is None or target is None or abs(got-target)>1e-6:
            raise RuntimeError('COMPONENT_V3_SCORING_PARITY_DRIFT:'+slot)
    return {'advancement':advancement, 'hidden':grouped,'by_visibility':groups,
            'by_scientific_sublevel':sublevels,'cases':results}


def run(args):
    import torch
    from tools import nanojev_altreg_math_v3_decoder_experiment as v3
    run_root=Path(args.output).expanduser().resolve()
    ck,ck_path=adapter.load_run_state(run_root)
    sidecar.verify_sidecar(ck);adapter.verify_saved(ck);xyop.verify(ck)
    # Verify the exact lineage against the immutable scientific configuration.
    saved=json.loads((run_root/'config.json').read_text(encoding='utf8'))
    cfg=argparse.Namespace(output=run_root,source=Path(args.source),teacher=Path(args.teacher),
        seed=args.seed,device=args.device,dtype=args.dtype,lr=1e-5,views=4,
        probe_per_tier=args.probe_per_tier,gate_percent=None)
    info=science.teacher_info(cfg.teacher)
    ident,differences=science.resolve_run_identity(saved,science.config(cfg,info),'diagnose')
    if ck.get('schema')!=science.CHECKPOINT_SCHEMA or ck['config']!=ident:
        raise ValueError('COMPONENT_CHECKPOINT_LINEAGE_DRIFT')
    original_sha=_digest(ck_path)
    cache_args=argparse.Namespace(output=run_root,source=Path(args.source),teacher=Path(args.teacher))
    cache=cached.prepare_cache(cache_args,args.cache_memory_mb)
    excluded,mask_source=xyop.resolve_level_mask(ck,None)
    mixes=xyop.parse_mixes(args.xyop_mix)
    selected=tiers_arg(args.tiers)
    # The hooks load only the actual frozen parent, Qwen and V3 + input adapters.
    # They never invoke sidecar prediction or numerical sidecar loss.
    with xyop.live_v3_only(ck,run_root,mixes,args.seed,masked_levels=excluded):
        # Hook ex.forward exactly as cached.main does, retaining the hint route.
        old_forward=ex.forward
        ex.forward=lambda model,prompts: cached.cached_forward(model,prompts,cache)
        try:
            model=ex.load_current(Path(args.source).resolve(),args.device,args.dtype,
                                  args.allow_model_download,ident['seed'])
            model['decoder'].load_state_dict(ck['decoder'],strict=True)
            model['gamehead'].load_state_dict(ck['game_operator_head'],strict=True)
            model['decoder'].eval(); model['gamehead'].eval()
            max_value=10_000_000
            scores={}
            for tier in selected:
                rows=science.rows_for_probe(args.teacher,'dev',tier,args.probe_per_tier,args.seed)
                scores[tier]=diagnose_tier(model,rows,mixes,args.seed,max_value)
                print(json.dumps({'event':'xyop_component_tier','tier':tier,
                     'hidden':scores[tier]['hidden'],
                     'gate_5pct':scores[tier]['advancement']['joint_5pct']},sort_keys=True),flush=True)
        finally:
            ex.forward=old_forward
    if _digest(ck_path)!=original_sha:
        raise RuntimeError('COMPONENT_CHECKPOINT_MUTATED')
    report={'schema':SCHEMA,'step':int(ck['step']),'checkpoint':str(ck_path),
            'checkpoint_sha256':original_sha,'split':'dev','probe_per_tier':args.probe_per_tier,
            'seed':args.seed,'xyop_mix':list(args.xyop_mix),'hidden_targets_only':True,
            'predictor':'V3_ONLY','test_split_read':False,'optimizer_steps':0,
            'checkpoint_unchanged':True,'trainer_config_differences':differences,
            'masked_training_levels':list(excluded),'masked_training_levels_source':mask_source,
            'diagnosed_levels':list(selected),
            'diagnoses_include_masked_training_levels':True,'scores':scores}
    path=(Path(args.report).expanduser().resolve() if args.report else
          run_root/f'xyop-components-dev-step-{ck["step"]:07d}.json')
    if path.resolve()==Path(ck_path).resolve() or path.resolve()==(run_root/'config.json').resolve():
        raise ValueError('COMPONENT_UNSAFE_REPORT_DESTINATION')
    science.jwrite(path,report)
    print(json.dumps({'event':'xyop_component_report','report_path':str(path),
                      'step':int(ck['step']),'checkpoint_unchanged':True,
                      'tiers':list(selected)},sort_keys=True),flush=True)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xyop-mix',action='append',default=[],metavar='TIER:MASK=XYZ:WEIGHT,...')
    parser.add_argument('--tiers',default=','.join(hd.TIERS),help='Comma-separated tiers to diagnose, including masked')
    parser.add_argument('--output',type=Path,default=science.DEFAULT_OUT)
    parser.add_argument('--teacher',type=Path,default=science.DEFAULT_TEACHER)
    parser.add_argument('--source',type=Path,default=science.DEFAULT_SOURCE)
    parser.add_argument('--seed',type=int,default=20261018)
    parser.add_argument('--probe-per-tier',type=int,default=48)
    parser.add_argument('--cache-memory-mb',type=int,default=1024)
    parser.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    parser.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    parser.add_argument('--allow-model-download',action='store_true')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args(argv)
    if args.probe_per_tier<8 or args.cache_memory_mb<0:
        raise ValueError('INVALID_COMPONENT_DIAGNOSIS_CONFIGURATION')
    tiers_arg(args.tiers); xyop.parse_mixes(args.xyop_mix)
    return run(args)

if __name__=='__main__':main()
