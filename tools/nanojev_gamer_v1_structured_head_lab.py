#!/usr/bin/env python3
"""Frozen 100-vector Gamer head-input architecture ablation; never promotion.

Compare the original step-500 head against three disposable, *identically
trained* additive residual decoders using the SAME original 100x1024 raster:
  final_last          -> final 1024-d token vector
  final_mean100       -> per-question mean of all 100 vectors
  structured_last_mean100 -> their explicit 2048-d concatenation

Every residual is trained on certified original TRAIN and matched six-case
synthetic TRAIN groups. Original DEV is used only for retention and checkpoint
selection. Synthetic TEST is previously seen and is not virgin acceptance.

This test isolates the use of two deterministic feature projections. The
structured variant uses a narrower hidden layer to keep total decoder
parameters within 0.3% of each single-view candidate. Different hidden widths
remain an architectural confound; this is not proof of upstream deficiency. Never trains Qwen, the LoRA, or the original head.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe

SCHEMA = 'nanojev.gamer.v1.structured-head-ablation.v1'
VARIANTS = ('final_last', 'final_mean100', 'structured_last_mean100')


def _views_from_flat(flat):
    """Recover exactly the two registered profiles from 100x1024 flat input.

    Critical: use each case's own 100x1024 mean, like probe capture. A single
    batched reduction takes a different accumulation path and can differ from
    the captured float32 features by >1e-2. No new Qwen captures are used.
    """
    import numpy as np
    arr = np.asarray(flat)
    if arr.ndim != 2 or arr.shape[1] != 102400 or arr.dtype not in (np.float16,np.float32):
        raise ValueError('STRUCTURED_NOT_CERTIFIED_100_VECTOR_FLOAT32_INPUT')
    if not np.isfinite(arr).all():
        raise ValueError('STRUCTURED_NONFINITE_HEAD_INPUT')
    x = arr.reshape(len(arr), 100, 1024)
    last = x[:, -1, :].astype(np.float32).copy()
    mean = np.stack([row.astype(np.float32).mean(axis=0) for row in x], axis=0).astype(np.float32)
    structured = np.concatenate((last, mean), axis=1)
    if last.shape != (len(arr), 1024) or mean.shape != last.shape or \
            structured.shape != (len(arr), 2048):
        raise ValueError('STRUCTURED_VIEW_SHAPE_MISMATCH')
    return {'final_last': last, 'final_mean100': mean,
            'structured_last_mean100': structured}


def _strict_capture_parity(derived, expected, kind):
    """Reject mismatched feature definitions, including tiny silent drift."""
    import numpy as np
    for feature in ('final_last', 'final_mean100'):
        name = 'lora__' + feature
        if name not in expected:
            raise ValueError('STRUCTURED_ARCHIVE_PROFILE_MISSING: ' + kind + '/' + name)
        captured = np.asarray(expected[name])
        if captured.shape != derived[feature].shape or captured.dtype not in (np.float16,np.float32) or \
                not np.array_equal(derived[feature].astype(captured.dtype), captured):
            difference = None
            if captured.shape == derived[feature].shape:
                difference = float(np.max(np.abs(derived[feature] - captured)))
            raise ValueError('STRUCTURED_100_VECTOR_CAPTURE_PARITY_FAILED: ' +
                             kind + '/' + feature + '/' + str(difference))


def _assemble(data, prepared):
    """Produce matched train/dev/test tuples for all three architectures.

    Only original TRAIN and synthetic TRAIN features enter fitting and
    normalization. The per-case raster is derived from the frozen production
    head's original 100x1024 interface, including synthetic and original DEV.
    """
    import numpy as np
    info = data['probe_info']
    retention = data['retention_arrays']
    arrays = data['probe_arrays']
    p = data['partitions']
    if set(p) != {'train', 'dev', 'test'}:
        raise ValueError('STRUCTURED_MISSING_SPLIT')
    source = _views_from_flat(data['original_train_features'])
    original_dev = _views_from_flat(retention['lora__final_flat'])
    synthetic = _views_from_flat(arrays['lora__final_flat'])
    _strict_capture_parity(original_dev, retention, 'original_dev')
    _strict_capture_parity(synthetic, arrays, 'synthetic')
    # Match the archived capture dtype (often float16) AFTER verifying that
    # the values were produced from exactly the same original flat vectors.
    # Otherwise sub-ULP rounding differences would alter the experiment.
    for view,archive in ((original_dev,retention),(synthetic,arrays)):
        for name in ('final_last','final_mean100'):
            captured=archive['lora__'+name]
            view[name]=view[name].astype(captured.dtype).astype(np.float32)
        view['structured_last_mean100']=np.concatenate(
            (view['final_last'],view['final_mean100']),axis=1)
    if len(synthetic['final_last']) != len(info['dataset']) or \
            len(original_dev['final_last']) != len(data['retention_info']['dataset']):
        raise ValueError('STRUCTURED_FEATURE_DATASET_LENGTH_MISMATCH')
    for variant in ('final_last', 'final_mean100'):
        spec = prepared[variant]
        for split, actual in (
            ('dev_source', original_dev[variant]),
            ('train_synthetic', synthetic[variant][p['train']]),
            ('dev_synthetic', synthetic[variant][p['dev']]),
            ('test_synthetic', synthetic[variant][p['test']]),
        ):
            ix = {'train_source':0, 'dev_source':3,
                  'train_synthetic':0, 'dev_synthetic':3, 'test_synthetic':3}[split]
            if not np.array_equal(actual, spec[split][ix]):
                raise ValueError('STRUCTURED_V3_DATA_BINDING_PARITY_FAILED: '+variant+'/'+split)
    a=prepared['final_last']
    result={}
    for name in VARIANTS:
        result[name]={
            'train_source': (source[name], *a['train_source'][1:]),
            'train_synthetic': (synthetic[name][p['train']], *a['train_synthetic'][1:]),
            'dev_source': (*a['dev_source'][:3], original_dev[name]),
            'dev_synthetic': (*a['dev_synthetic'][:3], synthetic[name][p['dev']], p['dev']),
            'test_synthetic': (*a['test_synthetic'][:3], synthetic[name][p['test']], p['test']),
        }
    if any(set(p[k]) & set(p[l]) for k,l in
           (('train','dev'),('train','test'),('dev','test'))):
        raise ValueError('STRUCTURED_SYNTHETIC_GROUP_SPLIT_LEAK')
    return result


def _baseline_metrics(data, variants):
    import numpy as np
    spec=variants['final_last']
    source_rows,source_y,source_old,_=spec['dev_source']
    source_class=lab._classify_continuous(source_old)
    retention=lab._retention(source_y,source_old,source_class,source_old,source_rows)
    if retention['status']!='PASS':
        raise ValueError('STRUCTURED_FROZEN_HEAD_FAILED_SELF_RETENTION')
    zero_res=np.zeros((len(source_old),4),dtype=np.float32)
    retention['correct_zero_margin_monitor']=joint._correct_original_zero_margins(
        source_old,source_y,zero_res)
    summaries={}
    for part in ('dev_synthetic','test_synthetic'):
        rows,y,old,_,indexes=spec[part]
        cls=lab._classify_continuous(old)
        score=lab._group_scores(rows,y,old,cls,indexes)
        score['cross_format']=joint._paired_agreement(rows,y,old,cls,indexes)
        summaries['dev' if part=='dev_synthetic' else 'test']=score
    return retention,summaries


def _score_candidate(torch, *, variant, specs, args):
    import numpy as np
    # Match the number of trainable decoder parameters within 0.3%.
    # Last/mean 1025->128 vs concat 2049->64.
    hidden=64 if variant=='structured_last_mean100' else 128
    model,mu,sigma,best,history,counts=joint._fit_one(
        torch,feature=variant,source=specs['train_source'],
        synthetic=specs['train_synthetic'],original_dev=specs['dev_source'],
        synthetic_dev=specs['dev_synthetic'],device=args.device,
        steps=args.steps,lr=args.lr,source_weight=args.source_weight,
        anchor_weight=args.anchor_weight,seed=args.seed,eval_every=args.eval_every,
        consistency_weight=args.cross_format_weight,
        zero_margin_weight=args.zero_margin_weight,decoder_hidden=hidden)
    meta,y,old,profile,part=specs['test_synthetic']
    classes,pred=joint._predict(model,profile,old[part],mu,sigma,args.device)
    all_values=np.asarray(old,dtype=np.float64).copy()
    all_classes=lab._classify_continuous(all_values)
    all_values[part]=pred
    all_classes[part]=classes
    test=lab._group_scores(meta,y,all_values,all_classes,part)
    test['cross_format']=joint._paired_agreement(meta,y,all_values,all_classes,part)
    original_test=lab._group_scores(meta,y,old,lab._classify_continuous(old),part)
    gate=lab._gates(test,best['retention'],original_test['all'])
    gate['correct_cross_format_invariance']=joint._cross_format_gate(test['cross_format'])
    gate['all_bounded_gates_pass']=bool(gate['all_bounded_gates_pass'] and
                                         gate['correct_cross_format_invariance'])
    entry={'id':variant,'architecture':variant,
           'frozen_head_used_as_baseline':True,
           'input_width':int(specs['train_source'][0].shape[1]),
           'decoder_parameters':sum(v.numel() for v in model.parameters()),
           'decoder_hidden':hidden,
           'training_steps':args.steps,'selected_step':best['step'],
           'original_dev_retention':best['retention'],
           'dev':best['dev'],'test':test,'gates':gate,
           'history':history,'train_counts':counts,
           'candidate_never_promoted':True}
    return entry,{'weights':{k:v.detach().cpu().clone() for k,v in model.state_dict().items()},
                  'mu':mu,'sigma':sigma}


def _write_outputs(out, data, report, winner):
    import torch
    out.mkdir(parents=True,exist_ok=False)
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
                                           allow_nan=False)+'\n',encoding='utf-8')
    fields=['architecture','input_width','decoder_parameters','selected_step',
            'retention','correct_kept','retained_negative','retained_zero',
            'retained_positive','original_mae','dev_sign','dev_triples',
            'dev_both_correct','dev_six_groups','test_sign','test_zero',
            'test_triples','test_both_correct','test_six_groups',
            'all_bounded_gates_pass']
    with (out/'architecture_comparison.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        for item in report['candidates']:
            ret=item['original_dev_retention']
            writer.writerow({'architecture':item['architecture'],
                'input_width':item['input_width'],
                'decoder_parameters':item['decoder_parameters'],
                'selected_step':item['selected_step'],
                'retention':ret['status'],
                'correct_kept':ret['original_correct_kept_fraction'],
                **{'retained_'+c:ret['by_class'][c]['preserved_fraction']
                   for c in ('negative','zero','positive')},
                'original_mae':ret['candidate']['signed_mae'],
                'dev_sign':item['dev']['all']['balanced_sign_accuracy'],
                'dev_triples':item['dev']['all']['triple_accuracy'],
                'dev_both_correct':item['dev']['cross_format']['both_correct_fraction'],
                'dev_six_groups':item['dev']['cross_format']['complete_six_case_fraction'],
                'test_sign':item['test']['all']['balanced_sign_accuracy'],
                'test_zero':item['test']['all']['zero_recall'],
                'test_triples':item['test']['all']['triple_accuracy'],
                'test_both_correct':item['test']['cross_format']['both_correct_fraction'],
                'test_six_groups':item['test']['cross_format']['complete_six_case_fraction'],
                'all_bounded_gates_pass':item['gates']['all_bounded_gates_pass']})
    with (out/'checkpoint_history.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['architecture','step','retention','failed_gates','retained_negative',
                'retained_zero','retained_positive','lost_negative','lost_zero',
                'lost_positive','overall_retention','original_mae',
                'dev_sign','dev_triples','dev_both_correct','dev_six_groups',
                'correct_zeros_below_margin_floor']
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        for item in report['candidates']:
            for h in item['history']:
                r=h['source_retention_by_class']
                writer.writerow({'architecture':item['architecture'],
                    'step':h['step'],'retention':h['source_retention'],
                    'failed_gates':';'.join(h['source_retention_failed_gates']),
                    **{'retained_'+c:r[c]['preserved_fraction']
                       for c in ('negative','zero','positive')},
                    **{'lost_'+c:r[c]['lost'] for c in ('negative','zero','positive')},
                    'overall_retention':h['source_correct_kept'],
                    'original_mae':h['source_mae'],
                    'dev_sign':h['synthetic_dev_balanced_sign'],
                    'dev_triples':h['synthetic_dev_triples'],
                    'dev_both_correct':(h['synthetic_dev_cross_format'] or {}).get('both_correct_fraction'),
                    'dev_six_groups':(h['synthetic_dev_cross_format'] or {}).get('complete_six_case_fraction'),
                    'correct_zeros_below_margin_floor':(h['source_correct_zero_margin'] or {}).get('below_floor_count')})
    torch.save({'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
                'lineage':data['probe_info']['lineage'],
                'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
                'original_train_cache_sha256':data['train_cache']['sha256'],
                'feature':winner['item']['architecture'],
                'weights':winner['weight']['weights'],
                'mean':torch.from_numpy(winner['weight']['mu']),
                'std':torch.from_numpy(winner['weight']['sigma']),
                'selected_step':winner['item']['selected_step'],
                'promotion_authorized':False},out/'disposable_candidate.pt')


def train(args):
    import torch
    if (args.steps<1 or args.steps>20000 or args.eval_every<1 or
            args.lr<=0 or args.source_weight<=0 or args.anchor_weight<1 or
            args.zero_margin_weight<0 or args.cross_format_weight<0 or
            not all(math.isfinite(v) for v in (args.lr,args.source_weight,
                         args.anchor_weight,args.zero_margin_weight,
                         args.cross_format_weight))):
        raise ValueError('STRUCTURED_INVALID_OPTIMIZER_CONFIGURATION')
    if args.device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('STRUCTURED_CUDA_UNAVAILABLE')
    roots=[Path(x) for x in (args.run,args.expanded_run,args.input,args.source_features)
           if x is not None]
    out=lab._output(args.output,roots)
    # Reuse v3's original corpus, step-500 weights, adapter and cache checks.
    data,existing=joint._prepare(args)
    variants=_assemble(data,existing)
    frozen_retention,frozen=_baseline_metrics(data,variants)
    print(f'STRUCTURED_HEAD source_train={len(data["original_train"])} '
          f'source_dev={len(data["retention_info"]["dataset"])} '
          f'synthetic={len(data["probe_info"]["dataset"])}; '
          f'original 100x1024 raster; frozen Qwen/LoRA/step-500 head',flush=True)
    results=[];winner=None
    for variant in VARIANTS:
        item,weight=_score_candidate(torch,variant=variant,
                                     specs=variants[variant],args=args)
        results.append(item)
        rank=joint._rank(item['original_dev_retention'],item['dev'])
        if winner is None or rank>winner['rank']:
            winner={'rank':rank,'item':item,'weight':weight}
        print(probe.canonical({'architecture':variant,
            'input_width':item['input_width'],
            'retention':item['original_dev_retention']['status'],
            'kept':item['original_dev_retention']['original_correct_kept_fraction'],
            'dev_sign':item['dev']['all']['balanced_sign_accuracy'],
            'test_sign':item['test']['all']['balanced_sign_accuracy'],
            'test_triples':item['test']['all']['triple_accuracy'],
            'test_six_case_groups':item['test']['cross_format']['complete_six_case_groups'],
            'gates':item['gates']['all_bounded_gates_pass']}),flush=True)
    joint._verify_after(data)
    report={'schema':SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
        'architecture_comparison':True,
        'feature_source':'EXACT_ORIGINAL_100x1024_FROZEN_HEAD_INPUT',
        'synthetic_profiles_capture_parity':'EXACT',
        'retention_profiles_capture_parity':'EXACT',
        'model_lineage':data['probe_info']['lineage'],
        'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
        'original_dev_sha256':data['retention_info']['dataset_sha256'],
        'original_train_cache':data['train_cache'],
        'frozen_head':{'original_dev_retention':frozen_retention,**frozen},
        'candidates':results,'candidate_count':len(results),
        'selected_candidate':winner['item']['architecture'],
        'selected_bounded_gates_pass':winner['item']['gates']['all_bounded_gates_pass'],
        'train_configuration':{'steps':args.steps,'eval_every':args.eval_every,
            'seed':args.seed,'lr':args.lr,'source_weight':args.source_weight,
            'anchor_weight':args.anchor_weight,
            'cross_format_weight':args.cross_format_weight,
            'zero_margin_weight':args.zero_margin_weight},
        'matched_training_budget':True,
        'decoder_capacity_control':'Single-view hidden=128, concatenated-view hidden=64; each decoder has approximately 131k trainable parameters (within 0.3%). Hidden width differs and remains an architectural confound.',
        'original_train_used_for_fitting':True,
        'original_dev_used_for_fitting':False,
        'synthetic_dev_used_for_selection_only':True,
        'synthetic_test_policy':'PREVIOUSLY_EXAMINED_NOT_A_VIRGIN_TEST',
        'hawk_belief_training':'UNAVAILABLE_TEST_ONLY',
        'lora_frozen':True,'production_head_frozen':True,
        'production_promotion':False,'independent_acceptance_required':True}
    _write_outputs(out,data,report,winner)
    print(probe.canonical({'status':report['status'],
        'selected':report['selected_candidate'],
        'bounded_gates_pass':report['selected_bounded_gates_pass'],
        'production_weights_modified':False,
        'original_dev_used_for_fitting':False,
        'report':str(out/'report.json'),
        'candidate_checkpoint':str(out/'disposable_candidate.pt')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('oracle','train'),default='oracle')
    p.add_argument('--run',type=Path)
    p.add_argument('--expanded-run',type=Path)
    p.add_argument('--input',type=Path)
    p.add_argument('--source-features',type=Path)
    p.add_argument('--output',type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--steps',type=int,default=300)
    p.add_argument('--eval-every',type=int,default=30)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--source-weight',type=float,default=1.)
    p.add_argument('--anchor-weight',type=float,default=3.)
    p.add_argument('--seed',type=int,default=49)
    p.add_argument('--cross-format-weight',type=float,default=0.,
                   help='Hold fixed across variants; v3 control used 0')
    p.add_argument('--zero-margin-weight',type=float,default=2.)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.mode=='oracle':
        deck=probe.make_deck()
        print(probe.canonical({'status':'ORACLE_ONLY',
            'synthetic':probe.validate_deck(deck),
            'features':list(VARIANTS),'candidate_configs':len(VARIANTS),
            'original_100_vector_head_interface':True,
            'same_optimizer_seed_steps':True,'original_dev_fit':False,
            'no_model_training':True}))
    else:train(args)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
