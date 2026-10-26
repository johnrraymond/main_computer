#!/usr/bin/env python3
"""Read-only, per-card numerical error diagnosis for the 192:223 ablation.

Reloads the *DEV-selected frozen* representation-ablation arms; rebuilds the
identical deterministic DEV / magnitude-held-out OOD / adjacent populations;
compares all arms on identical cards; and checks input transformations.

Targets, carry/borrow classifications, and magnitudes are evaluation-only.
Inference uses frozen operand-derived features and never routes by a label.
No weights are trained, modified, or selected using these evaluations.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import torch

from tools.nanojev_zero_line_absolute_magnitude_banks import AbsoluteBank, atomic_json, exact_mask
from tools.nanojev_zero_line_bank_autopilot import sample_band
from tools.nanojev_zero_line_learned_router import cache_with_tokens
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256
from tools import nanojev_zero_line_representation_ablation as ab

SCHEMA = 'nanojev.zero-line-192-223-failure-diagnosis.v1'
SPLITS = ('dev', 'ood', 'adjacent')
GROUPS = ('residual_class', 'residual_sign', 'operation', 'carry_borrow',
          'carry_count', 'magnitude_bin', 'magnitude_boundary',
          'operand_order', 'residual_x_operation', 'residual_x_carry_borrow')


def signed_value(logits):
    """Independent decoder for the frozen model's 3 sign + 7*10 digit logits."""
    sign = logits[:, :3].argmax(-1).long() - 1
    digits = logits[:, 3:].reshape(-1, 7, 10).argmax(-1).long()
    weights = digits.new_tensor([1, 10, 100, 1000, 10000, 100000, 1000000])
    return sign * (digits * weights).sum(-1)


def decimal_events(a, b):
    """Count actual base-10 magnitude addition carries or subtraction borrows.

    Same-sign operands add magnitudes; opposite-sign operands subtract the
    smaller absolute magnitude from the larger. Zero inputs are neutral.
    """
    if a == 0 or b == 0:
        return 'none', 0
    x, y = abs(a), abs(b)
    if (a > 0) == (b > 0):
        carry = events = 0
        while x or y or carry:
            carry = int(x % 10 + y % 10 + carry >= 10)
            events += carry
            x //= 10; y //= 10
        return 'carry', events
    x, y = max(x, y), min(x, y)
    borrow = events = 0
    while x or y:
        borrow = int(x % 10 - borrow < y % 10)
        events += borrow
        x //= 10; y //= 10
    return 'borrow', events


def residual_class(delta):
    n = abs(delta)
    if n == 0: return 'zero'
    if n <= 2: return 'near_1_2'
    if n <= 15: return 'small_3_15'
    if n <= 32: return 'frontier_16_32'
    if n <= 64: return 'mid_33_64'
    return 'ordinary_65_plus'


def classify(a, b, band):
    m = max(abs(a), abs(b))
    target = a + b
    op = ('zero_operand' if a == 0 or b == 0 else
          'same_sign_addition' if (a > 0) == (b > 0) else
          'opposite_sign_cancellation')
    event, count = decimal_events(a, b)
    lo, hi = band
    if m <= lo + 2: boundary = 'first_three_magnitudes'
    elif m >= hi - 2: boundary = 'last_three_magnitudes'
    elif m in (199, 200, 201, 209, 210, 211, 219, 220, 221):
        boundary = 'decimal_decade_transition'
    else: boundary = 'interior'
    return {
        'magnitude': m, 'residual': target, 'residual_class': residual_class(target),
        'residual_sign': 'zero' if target == 0 else 'positive' if target > 0 else 'negative',
        'operation': op, 'carry_borrow': event if count else 'none',
        'carry_count': 'none' if not count else 'single' if count == 1 else 'multiple',
        'carry_events': count,
        'magnitude_bin': f'{lo+((m-lo)//8)*8}-{min(hi,lo+((m-lo)//8)*8+7)}',
        'magnitude_boundary': boundary,
        'operand_order': 'equal_absolute' if abs(a)==abs(b) else
                         'larger_left' if abs(a)>abs(b) else 'larger_right',
        'residual_x_operation': f'{residual_class(target)} | {op}',
        'residual_x_carry_borrow': f'{residual_class(target)} | {event if count else "none"}',
    }


def summarize_rows(rows, model_names):
    """Each grouping is explicitly conditional; results are not weighted globally."""
    out = {}
    for group in GROUPS:
        groups = defaultdict(list)
        for item in rows:
            groups[item[group]].append(item)
        out[group] = {}
        for group_name, items in sorted(groups.items()):
            by_model = {}
            for name in model_names:
                correct = sum(bool(x[name+'_correct']) for x in items)
                bad = [x for x in items if not x[name+'_correct']]
                by_model[name] = {
                    'correct': correct,
                    'accuracy': correct / len(items),
                    'errors': len(bad),
                    'mean_abs_error_on_errors': (sum(abs(x[name+'_prediction'] - x['target'])
                                                     for x in bad) / len(bad)) if bad else 0.,
                    'sign_accuracy': sum((x[name+'_prediction']>0)-(x[name+'_prediction']<0)
                                         == (x['target']>0)-(x['target']<0) for x in items) / len(items),
                }
            out[group][str(group_name)] = {'cards': len(items), 'models': by_model}
    return out


def complementarity(rows, left, right):
    a = [bool(row[left+'_correct']) for row in rows]
    b = [bool(row[right+'_correct']) for row in rows]
    return {
        'both_correct': sum(x and y for x,y in zip(a,b)),
        'both_wrong': sum(not x and not y for x,y in zip(a,b)),
        'left_only_correct': sum(x and not y for x,y in zip(a,b)),
        'right_only_correct': sum(y and not x for x,y in zip(a,b)),
        'offline_answer_aware_ceiling_not_inference': sum(x or y for x,y in zip(a,b))/len(rows),
    }


def _tensor_cache(base, cards, device, eval_batch):
    return cache_with_tokens(base, cards, device, eval_batch, True)


def load_arm_models(ablation, config, fw, tw, source_hash, device):
    models = {}
    for arm in ab.ARMS:
        checkpoint = ablation/arm/'best.pt'
        if not checkpoint.is_file():
            raise RuntimeError(f'ZERO_DIAG_MISSING_ARM {arm}')
        payload = torch.load(checkpoint, map_location='cpu', weights_only=True)
        meta = payload.get('meta', {})
        budget = config['budgets'][arm]
        if (meta.get('source_sha256')!=source_hash or meta.get('arm')!=arm or
            meta.get('band')!=list(ab.BAND) or meta.get('seed')!=config['seed'] or
            meta.get('per_cell')!=config['sample_per_cell'] or
            meta.get('feature_width')!=fw or meta.get('token_width')!=tw or
            meta.get('budget')!=budget or
            not (0<int(payload.get('step',-1))<=int(config['steps']))):
            raise RuntimeError(f'ZERO_DIAG_CHECKPOINT_PROVENANCE_MISMATCH {arm}')
        model = ab.RepresentationBank(arm,fw,tw,int(budget['hidden'])).to(device)
        model.load_state_dict(payload['model'],strict=True)
        model.eval().requires_grad_(False)
        models[arm]=model
    return models


def load_original_specialist(ablation_config,stage,source_hash,feature_width,device):
    if not stage:return None
    stage=Path(stage).expanduser().resolve()
    cfg=json.loads((stage/'config.json').read_text(encoding='utf-8'))
    if cfg.get('band') != list(ab.BAND) or cfg.get('source_sha256')!=source_hash:
        raise RuntimeError('ZERO_DIAG_REFERENCE_PROVENANCE_MISMATCH')
    ckpt=stage/'bank_best.pt'
    payload=torch.load(ckpt,map_location='cpu',weights_only=True)
    bank=AbsoluteBank(feature_width,int(cfg['training_meta']['bank_hidden'])).to(device)
    bank.load_state_dict(payload['bank'],strict=True)
    return bank.eval().requires_grad_(False), sha256(ckpt)


@torch.inference_mode()
def predict_all(models, cache, batch_size, specialist=None):
    results = {'original_base':cache['original']}
    for name,model in models.items():
        results[name]=ab.predict(model,cache,batch_size)
    if specialist is not None:
        outputs = [specialist(cache['features'][i:i+batch_size])
                   for i in range(0,len(cache['target']),batch_size)]
        results['existing_specialist']=torch.cat(outputs)
    return results


def output_independent_of_truth(models,cache,batch_size,specialist=None):
    check={k:v[:min(16,len(cache['target']))] if isinstance(v,torch.Tensor) else v
           for k,v in cache.items()}
    if len(check['target'])==0:raise RuntimeError('ZERO_DIAG_EMPTY_CACHE')
    before=predict_all(models,check,batch_size,specialist)
    mutated={**check,'target':check['target']+113}
    after=predict_all(models,mutated,batch_size,specialist)
    if any(not torch.equal(before[k],after[k]) for k in before):
        raise RuntimeError('ZERO_DIAG_INFERENCE_TARGET_DEPENDENCE')
    return True


def describe_predictions(cards,outputs):
    ys=[int(r[2]) for r in cards]
    model_names=list(outputs)
    prediction={name:signed_value(logits).detach().cpu().tolist() for name,logits in outputs.items()}
    correct={name:exact_mask(logits,torch.tensor(ys,device=logits.device)).detach().cpu().tolist()
             for name,logits in outputs.items()}
    rows=[]
    for i,(a,b,*_) in enumerate(cards):
        group=classify(int(a),int(b),(192,223) if max(abs(a),abs(b))<=223 else (224,255))
        row={'a':int(a),'b':int(b),'target':int(a+b),**group}
        for name in model_names:
            row[name+'_prediction']=prediction[name][i]
            row[name+'_correct']=bool(correct[name][i])
        rows.append(row)
    return rows


def transforms(base,cards,device,eval_batch,models,specialist,first_outputs):
    """Check transformed predictions, not merely answer invariants.

    For swaps, f(a,b) should equal f(b,a). For sign flips,
    f(-a,-b) should equal -f(a,b), including exact-zero handling.
    """
    model_names=list(first_outputs)
    ret={name:{} for name in model_names}
    variants={
        'operand_swap':[(b,a,a+b,0,1) for a,b,*_ in cards],
        'both_signs_reversed':[(-a,-b,-(a+b),0,1) for a,b,*_ in cards],
    }
    baseline={name:signed_value(logits).detach().cpu() for name,logits in first_outputs.items()}
    for transform_name,rows in variants.items():
        inp=_tensor_cache(base,rows,device,eval_batch)
        out=predict_all(models,inp,eval_batch,specialist)
        for name in model_names:
            p=signed_value(out[name]).detach().cpu()
            expected=baseline[name] if transform_name=='operand_swap' else -baseline[name]
            truth=inp['target']
            exact=exact_mask(out[name],truth)
            start_exact=exact_mask(first_outputs[name],torch.tensor([r[2] for r in cards],device=out[name].device))
            consistency=(p==expected)
            ret[name][transform_name]={
                'cards':len(rows),
                'prediction_consistency':float(consistency.float().mean()),
                'transformed_exact_accuracy':float(exact.float().mean()),
                'both_correct':int((start_exact&exact).sum()),
                'original_correct_transformed_wrong':int((start_exact&~exact).sum()),
            }
    return ret


def write_csv(path, rows):
    if not rows:raise RuntimeError('ZERO_DIAG_NO_ROWS')
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]),extrasaction='ignore')
        writer.writeheader();writer.writerows(rows)


def validate_population(samples,adjacent,config):
    ab.validate_split(samples,config['seed'])
    expected=config.get('split_counts',{})
    if expected and any(len(samples[k])!=expected[k] for k in expected):
        raise RuntimeError('ZERO_DIAG_DATASET_CONFIG_MISMATCH')
    if config.get('adjacent_cards')!=len(adjacent):
        raise RuntimeError('ZERO_DIAG_ADJACENT_CONFIG_MISMATCH')
    if any(not 224<=max(abs(a),abs(b))<=255 for a,b,*_ in adjacent):
        raise RuntimeError('ZERO_DIAG_BAD_ADJACENT_RANGE')
    return True


def top_failures(rows, arm='backbone', limit=25):
    failed=[r for r in rows if not r[arm+'_correct']]
    # Deterministic, includes biggest absolute numerical errors first.
    failed.sort(key=lambda r:(-abs(r[arm+'_prediction']-r['target']),r['a'],r['b']))
    return [{k:row[k] for k in ('a','b','target','magnitude','residual_class',
                                'carry_borrow','carry_events','magnitude_boundary')}
            |{name+'_prediction':row[name+'_prediction'] for name in ('backbone','augmented','token_only')}
            for row in failed[:limit]]


def run(args):
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('ZERO_DIAG_CUDA_UNAVAILABLE')
    if args.eval_batch_size<1 or args.cpu_threads<1:raise ValueError('ZERO_DIAG_BAD_OPTIONS')
    source_dir=Path(args.ladder_output).expanduser().resolve()
    ablation_dir=Path(args.ablation_output).expanduser().resolve()
    config_file=ablation_dir/'config.json'
    if not config_file.is_file():raise RuntimeError('ZERO_DIAG_MISSING_ABLATION_CONFIG')
    config=json.loads(config_file.read_text(encoding='utf-8'))
    if (config.get('schema')!=ab.SCHEMA or config.get('band')!=list(ab.BAND) or
        not set(ab.ARMS)==set(config.get('budgets',{}))):
        raise RuntimeError('ZERO_DIAG_WRONG_ABLATION_CONFIG')
    stage=args.baseline_stage or config.get('baseline_stage') or ''
    input_roots=[source_dir,ablation_dir]+([stage] if stage else [])
    out=ab.safe_output(args.output,input_roots)
    torch.set_num_threads(args.cpu_threads)
    base,source=load_certified_model(str(source_dir),args.device)
    base.eval().requires_grad_(False)
    if source['checkpoint_sha256'] != config['source']['checkpoint_sha256']:
        raise RuntimeError('ZERO_DIAG_SOURCE_PROVENANCE_MISMATCH')
    seed=int(config['seed']);cell=int(config['sample_per_cell'])
    samples=sample_band(*ab.BAND,seed,cell)
    adjacent=sample_band(224,255,seed+7,cell,splits=('ood',))['ood']
    validate_population(samples,adjacent,config)
    cards_by_split={'dev':samples['dev'],'ood':samples['ood'],'adjacent':adjacent}
    cache={k:_tensor_cache(base,v,args.device,args.eval_batch_size) for k,v in cards_by_split.items()}
    fw=int(cache['dev']['features'].shape[-1]);tw=int(cache['dev']['tokens'].shape[-1])
    arm_hashes_before={arm:sha256(ablation_dir/arm/'best.pt') for arm in ab.ARMS}
    models=load_arm_models(ablation_dir,config,fw,tw,source['checkpoint_sha256'],args.device)
    extra=load_original_specialist(config,stage,source['checkpoint_sha256'],fw,args.device)
    specialist,baseline_hash=(extra if extra else (None,None))
    truth_independent=output_independent_of_truth(models,cache['dev'],args.eval_batch_size,specialist)
    outputs={};reports={}
    out.mkdir(parents=True,exist_ok=True)
    for split in SPLITS:
        c=cache[split];cards=cards_by_split[split]
        pred=predict_all(models,c,args.eval_batch_size,specialist)
        outputs[split]=pred
        rows=describe_predictions(cards,pred)
        write_csv(out/f'cards_{split}.csv',rows)
        names=list(pred)
        summaries=summarize_rows(rows,names)
        totals={name:{'cards':len(rows),'correct':sum(r[name+'_correct'] for r in rows),
                      'accuracy':sum(r[name+'_correct'] for r in rows)/len(rows)} for name in names}
        comparisons={f'{x}_vs_{y}':complementarity(rows,x,y)
                     for x,y in (('backbone','augmented'),('backbone','token_only'),
                                 ('original_base','backbone'))}
        if specialist:comparisons['existing_specialist_vs_backbone']=complementarity(rows,'existing_specialist','backbone')
        morph=transforms(base,cards,args.device,args.eval_batch_size,models,specialist,pred)
        # Report frequent error populations, not just dramatic individual errors.
        worst=[]
        for group_name in ('residual_class','operation','carry_borrow','magnitude_boundary',
                           'residual_x_carry_borrow'):
            for bucket,val in summaries[group_name].items():
                if val['cards']>=20:
                    worst.append({'feature':group_name,'bucket':bucket,'cards':val['cards'],
                                  'backbone_accuracy':val['models']['backbone']['accuracy'],
                                  'augmented_accuracy':val['models']['augmented']['accuracy']})
        worst.sort(key=lambda d:(d['backbone_accuracy'], -d['cards']))
        reports[split]={
            'cards':len(rows),'totals':totals,'by_feature':summaries,
            'model_complementarity':comparisons,'metamorphic':morph,
            'worst_backbone_buckets':worst[:15],
            'largest_backbone_errors':top_failures(rows),
            'card_csv':f'cards_{split}.csv',
        }
        brief={'split':split,'cards':len(rows),
               'exact':{k:round(v['accuracy'],5) for k,v in totals.items()},
               'zero':{k:round(v['models']['backbone']['accuracy'],5) for k,v in summaries['residual_class'].items() if k in ('zero','near_1_2')},
               'carry_vs_borrow':{k:round(v['models']['backbone']['accuracy'],5) for k,v in summaries['carry_borrow'].items()}}
        print('ZERO_FAILURE_SPLIT='+json.dumps(brief,sort_keys=True),flush=True)
    if sha256(source['checkpoint'])!=source['checkpoint_sha256']:
        raise RuntimeError('ZERO_DIAG_SOURCE_MUTATED')
    if any(sha256(ablation_dir/arm/'best.pt') != arm_hashes_before[arm] for arm in ab.ARMS):
        raise RuntimeError('ZERO_DIAG_ABLATION_CHECKPOINT_MUTATED')
    if stage and sha256(Path(stage).expanduser().resolve()/'bank_best.pt')!=baseline_hash:
        raise RuntimeError('ZERO_DIAG_REFERENCE_CHECKPOINT_MUTATED')
    sources={
        'certified_backbone_sha256':source['checkpoint_sha256'],
        'ablation_config_sha256':sha256(config_file),
        'arms':arm_hashes_before,
        'reference_specialist_sha256':baseline_hash,
    }
    report={'schema':SCHEMA,'source':sources,'band':list(ab.BAND),
            'ablation_output':str(ablation_dir),'sample_seed':seed,
            'sample_per_cell':cell,'labels_used_for_inference':False,
            'frozen_models':True,'selected_checkpoints_unchanged':True,
            'label_independence_checked':truth_independent,
            'checkpoint_selection':'original ablation DEV-selected best.pt; diagnostic never selects a checkpoint',
            'population_warning':'Existing OOD/adjacent folds have informed research decisions; these are diagnostics, not untouched final certification.',
            'classification_note':'carry/borrow events computed from decimal magnitudes for diagnostic labels only',
            'splits':reports}
    atomic_json(out/'result.json',report)
    print('ZERO_FAILURE_SUMMARY='+json.dumps({
        'report':str(out/'result.json'),
        'ood':{k:round(v['accuracy'],5) for k,v in reports['ood']['totals'].items()},
        'ood_worst_buckets':reports['ood']['worst_backbone_buckets'][:5],
        'frozen':True,'target_independent':True},sort_keys=True),flush=True)
    print('ZERO_FAILURE_READ_ONLY=TRUE ZERO_FAILURE_NO_TRAINING=TRUE ZERO_FAILURE_NO_ROUTING=TRUE',flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--ablation-output',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--baseline-stage',default='')
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--eval-batch-size',type=int,default=512)
    p.add_argument('--cpu-threads',type=int,default=4)
    return p


def main(argv=None):return run(parser().parse_args(argv))

if __name__=='__main__':main()
