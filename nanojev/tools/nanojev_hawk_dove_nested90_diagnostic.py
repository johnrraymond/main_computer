#!/usr/bin/env python3
"""Read-only preflight and probe monitor for 21-bank fractional expected-count nested90.

Never loads Qwen, changes a checkpoint, reads the held-out TEST split, or mutates
any training state. Check the generated inputs *and* the actual serialized probe
reports; these are different evidence classes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import struct

SCHEMA = 'nanojev.hawk-dove.nested90-expected.diagnostic.v1'
TIERS = tuple(f'T{i:02}' for i in range(9))


def fp32(value):
    """Match torch.float32 rounding (including tie handling) without CUDA."""
    return struct.unpack('!f', struct.pack('!f', float(value)))[0]


def check(report, name, ok, *, severity='fail', detail=None):
    status='pass' if ok else severity
    result={'name':name,'status':status}
    if detail is not None:result['detail']=detail
    report['checks'].append(result)
    return result


def _fractional_encoding(data, tier):
    from tools import nanojev_hawk_dove_nested90_expected as mixture
    from tools import nanojev_hawk_dove_xyop_mask_train as xyop
    row={'tier':tier,'expected_a':str(data['a']),'expected_b':str(data['b'])}
    role=tuple(fp32(x) for x in mixture.encode_expected(row))
    # Pattern 011 exposes Y, hides X, and exposes OP=MAX. Do NOT pack X as visible.
    hint=tuple(fp32(x) for x in xyop.pack(str(data['x']),str(data['y']),'011'))
    if hint[0]!=0 or hint[1]!=0:
        raise ValueError('DIAGNOSTIC_HIDDEN_X_LEAK')
    return role,role+hint


def numeric_encoding_audit(train,dev,payoffs):
    from tools import nanojev_hawk_dove_nested90_expected as mix
    out={}
    # Capture both collision rate and the span of *different answers* that share
    # one float32 numerical input. This is NOT a full frozen-Qwen embedding audit.
    for label,counts in [('train',train),('new_dev',dev)]:
        groups_by_tier={}
        oracle=[{'a':a,'b':mix.TOTAL-a,'x':mix.expected_oracle(a,payoffs)[0],
                 'y':mix.expected_oracle(a,payoffs)[1]} for a in counts]
        tiny=[r for r in oracle if abs(r['x'])<1]
        for tier in TIERS:
            role_groups=defaultdict(list);joined_groups=defaultdict(list)
            for r in tiny:
                role,full=_fractional_encoding(r,tier)
                role_groups[role].append(r['x']);joined_groups[full].append(r['x'])
            def stats(groups):
                spans=[max(v)-min(v) for v in groups.values() if len(v)>1]
                return {'unique_input_vectors':len(groups),
                        'target_cases':len(tiny),
                        'colliding_cases':sum(len(v) for v in groups.values() if len(v)>1),
                        'distinct_answer_collisions':sum(len(set(v)) for v in groups.values() if len(set(v))>1),
                        'max_target_span_sharing_vector':str(max(spans,default=Decimal(0)))}
            groups_by_tier[tier]={'operand_roles_float32':stats(role_groups),
                                  'operand_plus_visible_y_float32':stats(joined_groups)}
        out[label]={'below_one_count':len(tiny),'per_tier':groups_by_tier}
    return out


def preflight(args):
    from tools import nanojev_hawk_dove_nested90_expected as mix
    from tools import nanojev_hawk_dove_nested90_mixture as old
    from tools import nanojev_hawk_dove_million as hd
    report={'schema':SCHEMA+'.preflight','checks':[],'scope':'new fractional expected-count curriculum only',
            'test_split_read':False}
    payoffs,fixture=old.certified_payoffs(args.fixture,args.teacher)
    excluded=old.original_counts(args.teacher,splits=('dev',))
    train_old=old.original_counts(args.teacher,splits=('train',))
    feasible,train,dev=mix.plan(payoffs,excluded,args.train_per_tier,args.dev_per_tier,args.seed,
                strict_unique=True,reallocate_empty=args.reallocate_empty_bands)
    report['mixture']={k:v for k,v in feasible.items() if k!='available_x_intervals_by_exclusive_band'}
    report['fixture_sha256']=fixture['sha256']
    if train is None or dev is None:
        check(report,'oracle_support',False,detail=feasible['unreachable_bands'])
        return _finalize(report)
    check(report,'oracle_support',bool(feasible['feasible']) and bool(feasible['unique_quotas_feasible']))
    check(report,'exact_requested_mixture',bool(feasible['distribution_exact']),detail=feasible['empty_band_transfers'])
    check(report,'new_dev_enabled',len(dev)>0,severity='fail',detail={'new_dev_per_tier':len(dev)})
    check(report,'unique_numerical_inputs',len(set(train))==len(train) and len(set(dev))==len(dev),
          detail={'train':len(train),'new_dev':len(dev)})
    check(report,'train_new_dev_disjoint',not (set(train)&set(dev)))
    check(report,'train_original_integer_teacher_disjoint',not(set(train)&{Decimal(a) for a in train_old|excluded}),
          detail={'original_integer_collisions':len(set(train)&{Decimal(a) for a in train_old|excluded})})
    bins=Counter(old.bin_for(mix.expected_oracle(a,payoffs)[0]) for a in train)
    quotas=old.quotas(len(train))
    check(report,'exclusive_band_quotas',all(bins.get(i,0)==q for i,q in enumerate(quotas)),
          detail={'expected':list(quotas),'actual':[bins.get(i,0) for i in range(8)]})
    tiny=[mix.expected_oracle(a,payoffs)[0] for a in train if abs(mix.expected_oracle(a,payoffs)[0])<1]
    check(report,'nonzero_subunit_targets',len(tiny)>=1 and all(x!=0 for x in tiny) and len(set(tiny))==len(tiny),
          detail={'distinct_nonzero_below_one':len(set(tiny))})
    # Every T00-T08 presentation, not a hand-picked smoke sample.
    prompts_checked=0
    prompt_digests=set()
    for tier in TIERS:
        for split,values in [('train',train),('new_dev',dev)]:
            for a in values:
                prompt,verified=mix.render_one(tier,a,payoffs)
                x,y,z=mix.expected_oracle(a,payoffs)
                if verified!={'x':hd.canonical_num(x),'y':hd.canonical_num(y),'z':z}:
                    raise ValueError('DIAGNOSTIC_ORACLE_LABEL_MISMATCH')
                key=(tier,prompt)
                if key in prompt_digests:raise ValueError('DIAGNOSTIC_DUPLICATE_PROMPT')
                prompt_digests.add(key);prompts_checked+=1
    check(report,'all_tier_prompt_oracle_certification',prompts_checked==(len(train)+len(dev))*9,
          detail={'certified':prompts_checked,'tiers':list(TIERS)})
    # Distinguish the adapter's float32 loss of resolution from full Qwen text features.
    encoding=numeric_encoding_audit(train,dev,payoffs)
    report['numeric_encoding_precision']=encoding
    smallest=encoding['train']['per_tier']['T07']['operand_plus_visible_y_float32']
    check(report,'float32_role_plus_visible_y_subunit_uniqueness',smallest['unique_input_vectors']==len(tiny),
          severity='warn',detail={'tier':'T07','unique_vectors':smallest['unique_input_vectors'],
                                  'distinct_targets':len(tiny),'interpretation':'upstream tokenizer could still distinguish raw decimal text'})
    report['precision_notes']={
        'evaluation_threshold':'5% metric actually uses |prediction-X|/max(1,|X|) <= 0.05; for |X|<1 this is 0.05 absolute error',
        'input_float32':'Reported collisions apply only to role and XYOP adapter vectors, not Qwen tokenized prompt features',
        'dataset_semantics':'Expected fractional encounter counts differ from original integer-count teacher observations',
        'bank_resolution':'Narrowest radius at relative_max=2,000,000 is 1.9073486328125; 129 interior centers imply spacing 0.0298023223876953125'}
    report['summary']={'train_cases_per_tier':len(train),'new_dev_cases_per_tier':len(dev),
                       'train_presentations':len(train)*9,'new_dev_presentations':len(dev)*9,
                       'distinct_below_one_targets':len(tiny),'old_integer_teacher_collisions':len(set(train)&{Decimal(a) for a in train_old|excluded})}
    return _finalize(report)


def _number(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)


def _metric(block,key):
    val=block.get(key) if isinstance(block,dict) else None
    return float(val) if _number(val) else None


def _finalize(result):
    counts=Counter(row['status'] for row in result['checks'])
    result['status']='fail' if counts['fail'] else 'warn' if counts['warn'] else 'pass'
    result['check_counts']=dict(counts)
    return result


def monitor(args):
    report={'schema':SCHEMA+'.monitor','checks':[],'test_split_read':False,'run_dir':str(args.run_dir)}
    run=args.run_dir.expanduser()
    files=sorted(run.glob('probe-step-*.json')) if run.is_dir() else []
    if not files:
        check(report,'probe_files_available',False,severity='warn',detail='No completed probe yet. This does not prove a crash.')
        return _finalize(report)
    series=[]
    for path in files:
        try:
            p=json.loads(path.read_text(encoding='utf-8'))
            step=int(p['step'])
            if p.get('schema') not in (None,'nanojev.hawk-dove.gaussian-joint-decoder-21.nested90-expected.v1.probe.v1',
                                     'nanojev.hawk-dove.gaussian-joint-decoder-21.nested90-expected.v2-retention.probe.v1'):
                raise ValueError('NOT_EXPECTED_COUNT_PROBE')
            if step<0 or path.stem!=f'probe-step-{step:07d}':raise ValueError('STEP_NAME_MISMATCH')
            original=p['joint'];base=p['frozen_stage2']
            new=p.get('nested90_unseen_dev',{}).get('joint')
            tr=p.get('train')
            row={'step':step,'probe_schema':p.get('schema'),
                 'original_dev':_metric(original,'accuracy_5pct'),
                 'original_small':_metric(original,'small_accuracy_5pct'),
                 'base_original':_metric(base,'accuracy_5pct'),
                 'base_small':_metric(base,'small_accuracy_5pct'),
                 'new_dev':_metric(new,'accuracy_5pct'),'new_small':_metric(new,'small_accuracy_5pct'),
                 'train':_metric(tr,'accuracy_5pct'),'train_small':_metric(tr,'small_accuracy_5pct'),
                 'new_dev_base':_metric(p.get('nested90_unseen_dev',{}).get('frozen_stage2'),'accuracy_5pct'),
                 'new_dev_base_small':_metric(p.get('nested90_unseen_dev',{}).get('frozen_stage2'),'small_accuracy_5pct'),
                 'new_subunit':p.get('nested90_unseen_dev',{}).get('magnitude_bands',{}).get('7'),
                 'new_subunit_by_tier':p.get('nested90_unseen_dev',{}).get('below_one_by_tier'),
                 'train_subunit':p.get('magnitude_bands_train',{}).get('7'),
                 'frozen_feature_resolution':p.get('mixture_feasibility',{}).get('frozen_feature_resolution'),
                 'gradients':p.get('chunk_mean_train_losses_and_gradients'),
                 'banks':p.get('bank_dev'),
                 'new_banks':p.get('nested90_unseen_dev',{}).get('per_bank'),
                 'promotion_gate':p.get('promotion_gate'),
                 'retention_train':p.get('integer_replay_train_accuracy'),
                 'streams':p.get('mixture_feasibility',{}).get('optimizer_streams'),
                 'improved':p.get('improved_vs_base_cases'),'regressed':p.get('regressed_vs_base_cases')}
            if row['original_dev'] is None or row['base_original'] is None:raise ValueError('MISSING_REQUIRED_ACCURACY')
            series.append(row)
        except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError) as exc:
            check(report,'probe_read_integrity',False,detail={'file':str(path),'error':str(exc)})
            return _finalize(report)
    series.sort(key=lambda x:x['step'])
    check(report,'probe_read_integrity',True,detail={'count':len(series)})
    check(report,'step_monotonic_and_unique',len({x['step'] for x in series})==len(series))
    latest=series[-1]
    subunit=latest.get('new_subunit')
    if isinstance(subunit,dict):
        report['new_dev_below_one']=subunit
        report['new_dev_below_one_by_tier']=latest.get('new_subunit_by_tier')
        check(report,'below_one_new_dev_coverage',subunit.get('n',0)>0)
        if subunit.get('accuracy_5pct_floor') is not None and subunit.get('frozen_accuracy_5pct_floor') is not None:
            check(report,'below_one_new_dev_beats_frozen',subunit['accuracy_5pct_floor']>=subunit['frozen_accuracy_5pct_floor'],
                  severity='warn',detail={'decoder':subunit['accuracy_5pct_floor'],'frozen':subunit['frozen_accuracy_5pct_floor']})
    else:check(report,'below_one_new_dev_metrics_present',False,
               severity='warn' if 'v1.probe' in str(latest.get('probe_schema')) else 'fail',
               detail='Historical v1 probes lack subunit metrics; rerun v2-retention for new measurements')
    resolution=latest.get('frozen_feature_resolution')
    if isinstance(resolution,dict):
        report['frozen_feature_resolution']=resolution
        disjoint=resolution.get('new_dev') or {}
        aliased={tier:row.get('aliased_downstream_distinct_target_cases') for tier,row in disjoint.items() if row.get('aliased_downstream_distinct_target_cases',0)>0}
        check(report,'complete_downstream_inputs_distinguish_subunit_targets',not aliased,severity='warn',detail={'aliased_tiers':aliased})
    else:check(report,'full_frozen_feature_resolution_present',False,severity='warn',detail='Available on probes produced with this diagnostic patch')
    report['latest']={k:v for k,v in latest.items() if k not in ('gradients','banks','frozen_feature_resolution','new_subunit_by_tier')}
    report['history']=[{k:v for k,v in row.items() if k in ('step','original_dev','original_small','new_dev','new_small','train','train_small')} for row in series[-args.last_n:]]
    has_new=all(x['new_dev'] is not None for x in series)
    check(report,'independent_expected_count_new_dev',has_new,detail='Provide --mixture-new-dev-per-tier 128 to training; old original dev alone cannot test the new curriculum')
    if not has_new:return _finalize(report)
    baseline_stable=all(abs(row['base_original']-series[0]['base_original'])<1e-7 for row in series)
    check(report,'unchanged_frozen_baseline',baseline_stable)
    if latest['train'] is None or latest['train_small'] is None:
        check(report,'train_dev_comparison_available',False)
    else:
        check(report,'train_dev_comparison_available',True)
        overall_gap=latest['train']-latest['new_dev']
        tiny_gap=latest['train_small']-latest['new_small'] if latest['new_small'] is not None else None
        check(report,'expected_count_generalization_gap',overall_gap<=args.max_generalization_gap,
              severity='warn',detail={'train_minus_new_dev':round(overall_gap,5),'max':args.max_generalization_gap})
        if tiny_gap is not None:
            check(report,'subunit_generalization_gap',tiny_gap<=args.max_small_gap,
                  severity='warn',detail={'train_small_minus_new_dev_small':round(tiny_gap,5),'max':args.max_small_gap,
                                          'note':'small metric is threshold from training args, not necessarily |X|<1'})
    if latest['new_dev_base'] is not None:
        check(report,'new_dev_beats_frozen_stage2',latest['new_dev']>=latest['new_dev_base'],
              severity='warn',detail={'head':latest['new_dev'],'frozen':latest['new_dev_base']})
    check(report,'original_integer_dev_retention',latest['original_dev']+args.allowed_regression>=latest['base_original'],
          severity='fail',detail={'head':latest['original_dev'],'frozen':latest['base_original'],
                                  'allowed_loss':args.allowed_regression})
    check(report,'catastrophic_original_integer_forgetting',
          latest['original_dev']+args.collapse_limit>=latest['base_original'],
          severity='fail',detail={'original':latest['original_dev'],'baseline':latest['base_original'],
                                  'collapse_limit':args.collapse_limit})
    gate=latest.get('promotion_gate')
    if isinstance(gate,dict):
        report['promotion_gate']=gate
        check(report,'dual_domain_best_promotion',gate.get('promotable') is True,
              severity='warn',detail=gate)
        streams=latest.get('streams')
        check(report,'integer_train_replay_stream_present',isinstance(streams,dict) and
              streams.get('original_integer_replay_per_batch',0)>0,detail=streams)
        check(report,'integer_replay_train_accuracy_present',_number(latest.get('retention_train')))
    else:
        check(report,'dual_domain_best_promotion',False,severity='warn',
              detail='Old probe format: no promotion decision; do not infer a promotion')
    if latest['new_small'] is not None and latest['new_dev_base_small'] is not None:
        check(report,'new_dev_small_beats_frozen',latest['new_small']>=latest['new_dev_base_small'],
              severity='warn',detail={'head':latest['new_small'],'frozen':latest['new_dev_base_small']})
    if latest['original_small'] is not None and latest['base_small'] is not None:
        check(report,'original_dev_small_retention',latest['original_small']+args.allowed_regression>=latest['base_small'],
              severity='warn',detail={'head':latest['original_small'],'frozen':latest['base_small']})
    if latest['step']>0:
        grads=latest['gradients']
        check(report,'gradient_diagnostics_present',isinstance(grads,dict))
        if isinstance(grads,dict):
            ratio=_metric(grads,'gaussian_to_numeric_bank_gradient_ratio')
            head=_metric(grads,'numeric_gradient_head_norm')
            check(report,'numerical_head_gradient_alive',head is not None and head>1e-12,severity='warn',detail={'head_gradient_norm':head})
            check(report,'gaussian_bank_gradient_bounded',ratio is not None and ratio<=args.gradient_ratio_limit+1e-3,
                  detail={'observed':ratio,'allowed':args.gradient_ratio_limit})
    if isinstance(latest['banks'],list):
        check(report,'21_bank_diagnostics_present',len(latest['banks'])==21)
        if len(latest['banks'])==21:
            empty=[b.get('bank') for b in latest['banks'] if b.get('interior_n',0)==0]
            report['bank_coverage']={'banks_with_no_interior_dev':empty,
                'finest_five':[{'bank':b.get('bank'),'interior_n':b.get('interior_n'),
                                'interior_accuracy_5pct':b.get('interior_numeric_accuracy_5pct'),
                                'overflow_accuracy':b.get('overflow_accuracy')} for b in latest['banks'][-5:]]}
            fine=latest.get('new_banks')
            if isinstance(fine,list) and len(fine)==21:
                empty_new=[b.get('bank') for b in fine if b.get('interior_n',0)==0]
                report['bank_coverage']['banks_with_no_interior_new_dev']=empty_new
                check(report,'every_bank_has_interior_new_dev',not empty_new,severity='warn',
                      detail={'no_interior_banks':empty_new})
            else:
                check(report,'every_bank_has_interior_new_dev',False,severity='warn',
                      detail='Older probe has only original-integer dev bank coverage')
    else:check(report,'21_bank_diagnostics_present',False)
    if len(series)>=args.plateau_probes+1:
        previous=series[-(args.plateau_probes+1):-1]
        current=latest['new_dev']
        improvement=current-max(p['new_dev'] for p in previous)
        check(report,'new_dev_improvement_recent',improvement>args.min_improvement,
              severity='warn',detail={'change_vs_recent_best':round(improvement,6),
                                      'probes':args.plateau_probes})
    if _number(latest['improved']) and _number(latest['regressed']):
        report['case_flips_vs_stage2']={'fixed':latest['improved'],'broken':latest['regressed']}
    return _finalize(report)


def parser():
    from tools import nanojev_hawk_dove_gaussian_joint_decoder_21_nested90_expected as runner
    upstream=runner.parser().parse_args([])
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('preflight','monitor','both'),default='both')
    p.add_argument('--mixture-fixture',dest='fixture',type=Path,default=upstream.mixture_fixture)
    p.add_argument('--teacher',type=Path,default=upstream.teacher)
    p.add_argument('--mixture-train-per-tier',dest='train_per_tier',type=int,default=512)
    p.add_argument('--mixture-new-dev-per-tier',dest='dev_per_tier',type=int,default=128)
    p.add_argument('--seed',type=int,default=upstream.seed)
    p.add_argument('--mixture-reallocate-empty-bands',dest='reallocate_empty_bands',action='store_true')
    p.add_argument('--run-dir',type=Path,default=runner.DEFAULT_OUTPUT)
    p.add_argument('--last-n',type=int,default=12)
    p.add_argument('--max-generalization-gap',type=float,default=.15)
    p.add_argument('--max-small-gap',type=float,default=.25)
    p.add_argument('--allowed-regression',type=float,default=.02)
    p.add_argument('--collapse-limit',type=float,default=.20)
    p.add_argument('--gradient-ratio-limit',type=float,default=.25)
    p.add_argument('--plateau-probes',type=int,default=3)
    p.add_argument('--min-improvement',type=float,default=.001)
    p.add_argument('--fail-on-alert',action='store_true',help='Exit nonzero for warnings too; never writes to the training run')
    return p


def main(argv=None):
    p=parser();a=p.parse_args(argv)
    if a.train_per_tier<1 or a.dev_per_tier<0 or a.last_n<1 or a.plateau_probes<1:
        p.error('INVALID_DIAGNOSTIC_PARAMETERS')
    out={'schema':SCHEMA,'mode':a.mode,'read_only':True,'test_split_read':False}
    if a.mode in ('preflight','both'):out['preflight']=preflight(a)
    if a.mode in ('monitor','both'):out['monitor']=monitor(a)
    statuses=[x['status'] for x in (out.get('preflight'),out.get('monitor')) if x]
    out['status']='fail' if 'fail' in statuses else 'warn' if 'warn' in statuses else 'pass'
    print(json.dumps(out,sort_keys=True,indent=2,allow_nan=False))
    return 2 if (out['status']=='fail' or (a.fail_on_alert and out['status']!='pass')) else 0


if __name__=='__main__':
    raise SystemExit(main())
