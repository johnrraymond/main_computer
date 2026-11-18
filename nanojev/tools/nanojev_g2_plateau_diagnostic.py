"""G2-0063: read-only plateau diagnosis of G2-0061/0062 checkpoint receipts.

Default mode: stdlib-only, reads committed validation receipts and can watch CUDA
training without loading tensors or modifying any artifact.

--deep: optional, bounded CPU-only replay of a committed checkpoint over already
captured TRAIN and VALIDATION features. Never reads TEST/CHALLENGE, never trains,
never saves weights, and never changes the running training configuration.
Gold labels are examined only AFTER independent inference to explain mistakes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import random
import statistics
import time

from tools import nanojev_g2_learning_probe as receipt_probe

ROOT = Path('..') / 'runtime' / 'reports' / 'gamer-v1'
DEFAULT_TRAINING = ROOT / 'graph-g2-0062-5000-4'
DEFAULT_CAPTURE = ROOT / 'graph-g2-0061-context-capture'
DEFAULT_PLAN = ROOT / 'graph-g2-0059-plan'
SCHEMA = 'nanojev.gamer.graph.g2.plateau-diagnostic-0063.v1'
ADMISSION_MARGINS = (-1.0, -0.5, 0.0, 0.5, 1.0, 2.0)  # diagnostic only


def _pct(value):
    return f'{value * 100:.2f}%'


def f1(metrics):
    return receipt_probe._metrics(metrics)['f1']


def gap_to_f1_target(metrics, target):
    """Necessary edge-error budget; NOT a sufficient criterion for target F1."""
    m = receipt_probe._metrics(metrics)
    gold = metrics['gold_edges']
    if not 0 < target < 1:
        raise ValueError('target F1 must be between zero and one')
    # f1 = 2*TP/(2*TP + FN + FP), and TP <= gold.
    max_errors = math.floor(2 * gold * (1 - target) / target + 1e-9)
    return {
        'target_f1': target,
        'current_f1': m['f1'],
        'current_edge_errors': m['errors'],
        'necessary_max_edge_errors': max_errors,
        'necessary_error_reduction': max(0, m['errors'] - max_errors),
        'note': 'Necessary only: maximum assumes TP equals all gold edges; not a forecast.'
    }


def candidate_exact_ceiling(metrics, target_exact=0.95):
    """Upper bound from per-graph candidate reachability, never model accuracy."""
    graphs = metrics['graphs']
    reachable = metrics.get('candidate_reachable')
    if type(reachable) is not int or reachable > graphs or reachable < 0 or graphs < 1:
        return None
    needed = math.ceil(graphs * target_exact - 1e-10)
    return {
        'graphs': graphs,
        'candidate_reachable_graphs': reachable,
        'max_exact_graph_rate': reachable / graphs,
        'target_exact_rate': target_exact,
        'target_exact_graphs': needed,
        'necessary_reachable_exact_rate': needed / reachable if reachable else math.inf,
        'feasible_by_candidate_count': needed <= reachable,
    }


def classify_receipts(snapshot, target_f1=0.95):
    history = snapshot.get('history', [])
    if not history:
        return {'status': snapshot['status'], 'observations': [], 'limits': []}
    last = history[-1]
    current = last['validation']
    changes = []
    for before, after in zip(history, history[1:]):
        old, new = f1(before['validation']), f1(after['validation'])
        changes.append({'from_step': before['step'], 'to_step': after['step'],
                        'gain_f1_pp': (new - old) * 100,
                        'gain_pp_per_1000_steps': (new - old) * 100000 /
                              (after['step'] - before['step']),
                        'missing_delta': after['validation']['missing'] - before['validation']['missing'],
                        'extra_delta': after['validation']['extra'] - before['validation']['extra']})
    prior = history[-2]['validation'] if len(history)>1 else None
    status = ('STEP_ZERO_ONLY' if len(history) == 1 else 'LEARNING_WITH_LATEST_GAIN'
              if changes[-1]['gain_f1_pp'] > 0 else 'NO_LATEST_F1_GAIN')
    return {
        'status': status,
        'current_step': last['step'],
        'current_f1': f1(current),
        'current_precision': receipt_probe._metrics(current)['precision'],
        'current_recall': receipt_probe._metrics(current)['recall'],
        'changes': changes,
        'gap': gap_to_f1_target(current, target_f1),
        'exact_ceiling': candidate_exact_ceiling(current),
        'error_shares': {
            'missing': current['missing'], 'extra': current['extra'],
            'extra_share': (current['extra'] / (current['extra'] + current['missing'])
                            if current['extra']+current['missing'] else 0),
        },
        'families': {
            k:{'f1':f1(v),'missing':v['missing'],'extra':v['extra'],
               'f1_delta_pp': (f1(v)-f1(prior['families'][k]))*100
                       if prior and k in prior.get('families',{}) else None}
            for k,v in current.get('families',{}).items()},
        'modes': {
            k:{'f1':f1(v),'missing':v['missing'],'extra':v['extra'],
               'f1_delta_pp': (f1(v)-f1(prior['modes'][k]))*100
                       if prior and k in prior.get('modes',{}) else None}
            for k,v in current.get('modes',{}).items()},
        'limits': [
            'Receipts establish validation trajectory, not optimization cause.',
            'Low incremental F1 is not proof of a permanent architecture ceiling.',
            '95% edge F1 and 95% exact graphs are distinct objectives.',
            'Same validation author/grammar; not independent grammar generalization.',
        ],
    }


def format_receipt_diagnosis(result):
    lines = ['G2-0063 LEARNING PLATEAU DIAGNOSIS — COMMITTED RECEIPTS (read-only)']
    if result['status'] in ('NO_TRAINING_DIRECTORY','NO_COMMITTED_CHECKPOINTS','NO_STEP_ZERO_BASELINE'):
        return '\n'.join(lines+[result['status'], 'No optimizer evidence available.'])
    lines.append(f"Step {result['current_step']}: validation F1 {_pct(result['current_f1'])} "
                 f"(precision {_pct(result['current_precision'])}, "
                 f"recall {_pct(result['current_recall'])}).")
    lines.append('Consecutive checkpoint gains (F1 percentage points):')
    for row in result['changes']:
        lines.append(f"  {row['from_step']:>5} -> {row['to_step']:<5}: "
                     f"{row['gain_f1_pp']:+.2f} pp; missing {row['missing_delta']:+,}, "
                     f"extra {row['extra_delta']:+,}")
    if len(result['changes'])>1:
        a,b=result['changes'][-2:]
        lines.append('Recent marginal gain is '
                     + ('smaller' if b['gain_pp_per_1000_steps']<a['gain_pp_per_1000_steps']
                        else 'not smaller')
                     + ' than the previous interval; cause is not identifiable from receipts.')
    gap = result['gap']
    lines.append(f"To reach {_pct(gap['target_f1'])} F1 on this same cohort, "
                 f"total edge errors must fall from {gap['current_edge_errors']:,} "
                 f"to at most {gap['necessary_max_edge_errors']:,} "
                 f"(reduce by at least {gap['necessary_error_reduction']:,}).")
    lines.append('That error budget is NECESSARY, not sufficient, and is not a projection.')
    err = result['error_shares']
    lines.append(f"Residuals: {err['missing']:,} missed gold edges, "
                 f"{err['extra']:,} extra edges ({_pct(err['extra_share'])} of errors are extra).")
    ceiling=result['exact_ceiling']
    if ceiling:
        lines.append(f"Candidate reachability permits at most {ceiling['candidate_reachable_graphs']}/"
                     f"{ceiling['graphs']} exact graphs ({_pct(ceiling['max_exact_graph_rate'])}), "
                     'before learned errors.')
        if ceiling['feasible_by_candidate_count']:
            lines.append(f"For {_pct(ceiling['target_exact_rate'])} exact graphs, "
                         f"at least {_pct(ceiling['necessary_reachable_exact_rate'])} "
                         'of candidate-reachable graphs must be exact.')
        else:
            lines.append('95% exact graphs is impossible under current candidate reachability.')
    for name,entries in [('Family',result['families']),('Mode',result['modes'])]:
        for key,row in sorted(entries.items(),key=lambda x:x[1]['f1']):
            delta=(f"; last interval {row['f1_delta_pp']:+.2f} pp"
                   if row['f1_delta_pp'] is not None else '')
            lines.append(f"{name} {key}: F1 {_pct(row['f1'])}{delta}, "
                         f"missing {row['missing']:,}, extra {row['extra']:,}")
    lines.append('CAUSE NOT YET ESTABLISHED: use --deep to inspect slot, relation, '
                 'node endpoint, deduplication and oracle-cardinality ranking errors.')
    return '\n'.join(lines)


def _balanced_indices(ds, count, *, eligible_only=False):
    """Deterministic, family/mode-balanced sample; labels do not select examples."""
    cells=defaultdict(list)
    for i, (_,_,a,b) in enumerate(ds.pairs):
        if eligible_only and not (a['candidate_reachable'] and b['candidate_reachable']):
            continue
        cells[(a['family'],a['mode'])].append(i)
    rng=random.Random(630063)
    for key in cells:
        rng.shuffle(cells[key])
    chosen=[]
    while len(chosen)<count and any(cells.values()):
        for key in sorted(cells):
            if len(chosen)<count and cells[key]:
                chosen.append(cells[key].pop())
    return chosen


def _sample_case(model, sample, p55, *, deep_slot=True):
    """Predict without label keys; only then use gold for diagnostic comparison."""
    import torch
    import torch.nn.functional as F

    features={k:v for k,v in sample.items() if k not in
              ('positive','truth_count','labels','edges','training_node_ids')}
    with torch.inference_mode():
        pred=p55.infer_edges(model,features,'set_prediction')
        rel,src,dst=model(features)
    # GOLD BOUNDARY: everything above this line is label-blind inference.
    gold=set(sample['positive'].tolist())
    tp=len(pred & gold)
    all_errors=sample['truth_count']-tp+len(pred-gold)
    missed=gold-pred
    extra=pred-gold
    relation=sample['relation']
    missed_by=Counter(int(relation[i]) for i in missed)
    extra_by=Counter(int(relation[i]) for i in extra)
    out={'family':sample['family'],'mode':sample['mode'],
         'graph_id':sample['prompt_sha256'][:12],
         'gold':sample['truth_count'],'reachable_gold':len(gold),
         'predicted':len(pred),'tp':tp,'missing':sample['truth_count']-tp,
         'extra':len(extra),'errors':all_errors,
         'candidate_reachable':len(gold)==sample['truth_count'],
         'missed_by_relation':dict(missed_by),'extra_by_relation':dict(extra_by),
         'exact':int(not all_errors)}
    if not deep_slot:
        return out

    # Compare the actual slot-decoder outputs with a label-informed cardinality
    # counterfactual. Never use its results for model selection or inference.
    with torch.inference_mode():
        lr=F.log_softmax(rel,dim=-1)
        ls=F.log_softmax(src,dim=-1)
        ld=F.log_softmax(dst,dim=-1)
        device=rel.device
        sources=sample['src'].to(device)
        targets=sample['dst'].to(device)
        labels=relation.to(device)+1
        scores=(ls[:,sources]+ld[:,targets]+lr[:,labels]).max(dim=0).values
        k=min(int(sample['truth_count']),int(sample['edge_count']))
        topk=set(int(x) for x in torch.topk(scores,k=k).indices.tolist()) if k else set()
        # A second post-inference decoder audit, with precisely the existing
        # null/typed-edge admission criteria, records where slots disappear.
        admitted=[];non_null=0;rejected=0;slot_best=[]
        for q in range(p55.SLOTS):
            is_non_null=int(rel[q].argmax())!=0
            non_null+=int(is_non_null)
            confidence=ls[q,sources]+ld[q,targets]+lr[q,labels]
            best=int(confidence.argmax())
            margin=float(rel[q,labels[best]]-rel[q,0])
            score=float(confidence[best])
            slot_best.append((best,score,margin))
            if not is_non_null:continue
            if margin<=0:
                rejected+=1
                continue
            admitted.append(best)
        unique=len(set(admitted))
        assert len(pred)==min(unique,p55.MAX_EDGE), 'Decoder audit drift'
        out['decoder']={
            'non_null_slots':non_null,
            'typed_edge_rejected_slots':rejected,
            'duplicate_edge_slots':len(admitted)-unique,
            'truncated_unique_edges':max(0,unique-p55.MAX_EDGE),
            'admitted_unique_edges':unique,
        }
        # Decoder counterfactuals are not trained and do not inspect gold
        # to *produce* their predictions. Validation scores of these variants
        # are descriptive, never sufficient to select an inference policy.
        variants={}
        for threshold in ADMISSION_MARGINS:
            scored={}
            for edge,score,margin in slot_best:
                if margin<=threshold:continue
                if edge not in scored or score>scored[edge]:scored[edge]=score
            chosen=set(sorted(scored,key=lambda x:-scored[x])[:p55.MAX_EDGE])
            hits=len(chosen&gold)
            variants[str(threshold)]={'tp':hits,'predicted':len(chosen),
                'exact':int(chosen==gold and len(gold)==sample['truth_count'])}
        out['admission_margin_sweep']=variants
        out['oracle_count_rank']={
            'gold_count_used':int(sample['truth_count']),
            'tp':len(topk & gold),'missing':sample['truth_count']-len(topk & gold),
            'extra':len(topk-gold),'exact':int(topk==gold and len(gold)==sample['truth_count']),
        }
        # Hungarian assignment is used ONLY for post-inference blame analysis.
        # It is a gold-aligned diagnostic, not a deployable prediction method.
        available=sorted(gold)
        slot_counts=Counter()
        if available:
            gs=sources[available];gd=targets[available];gr=labels[available]
            cost=-(lr[:,gr]+ls[:,gs]+ld[:,gd]).detach().cpu().numpy()
            slots,positions=p55.rectangular_assignment(cost)
            mapping={available[int(position)]:int(slot) for slot,position in zip(slots,positions)}
            for index in missed:
                slot=mapping[int(index)]
                correct_rel=int(rel[slot].argmax())==int(labels[index])
                correct_src=int(src[slot].argmax())==int(sources[index])
                correct_dst=int(dst[slot].argmax())==int(targets[index])
                if int(rel[slot].argmax())==0:slot_counts['gold_matched_slot_null']+=1
                if not correct_rel:slot_counts['gold_matched_wrong_relation']+=1
                if not correct_src:slot_counts['gold_matched_wrong_source']+=1
                if not correct_dst:slot_counts['gold_matched_wrong_destination']+=1
                if correct_rel and correct_src and correct_dst:
                    slot_counts['gold_matched_triple_top1_correct_but_unselected']+=1
        out['gold_matched_slot_indicators']=dict(slot_counts)
    return out


def _aggregate_cases(cases):
    total=Counter()
    family=defaultdict(Counter)
    mode=defaultdict(Counter)
    missing_rel=Counter();extra_rel=Counter();slots=Counter();indicators=Counter()
    errors=[];sweep=defaultdict(Counter)
    for row in cases:
        counters={k:row[k] for k in ('gold','predicted','tp','missing','extra','errors','exact','candidate_reachable')}
        total.update(counters);family[row['family']].update(counters)
        mode[row['mode']].update(counters)
        errors.append(row['errors'])
        missing_rel.update(row['missed_by_relation'])
        extra_rel.update(row['extra_by_relation'])
        slots.update(row['decoder'])
        indicators.update(row['gold_matched_slot_indicators'])
        for name,value in row['admission_margin_sweep'].items():
            sweep[name].update(value)
        oracle=row['oracle_count_rank'];total.update({'oracle_tp':oracle['tp'],
             'oracle_missing':oracle['missing'],'oracle_extra':oracle['extra'],
             'oracle_exact':oracle['exact']})
    total['graphs']=len(cases)
    exact=total['exact'];n=len(cases)
    return {'graphs':n,'f1':2*total['tp']/(total['gold']+total['predicted'])
            if total['gold']+total['predicted'] else 0,
            'precision':total['tp']/total['predicted'] if total['predicted'] else 0,
            'recall':total['tp']/total['gold'] if total['gold'] else 0,
            'exact':exact,'mean_errors_per_graph':statistics.mean(errors) if errors else 0,
            'median_errors_per_graph':statistics.median(errors) if errors else 0,
            'graphs_with_at_most_3_errors':sum(x<=3 for x in errors),
            'oracle_count_rank_f1':2*total['oracle_tp']/(total['gold']+total['gold'])
            if total['gold'] else 0,
            'oracle_count_rank_exact':total['oracle_exact'],
            'counts':dict(total),'by_family':{k:dict(v) for k,v in family.items()},
            'by_mode':{k:dict(v) for k,v in mode.items()},
            'missed_by_relation':dict(missing_rel),
            'extra_by_relation':dict(extra_rel),
            'decoder':dict(slots),
            'matched_slot_indicators':dict(indicators),
            'admission_margin_sweep':{k:{'f1':2*v['tp']/(total['gold']+v['predicted'])
                    if total['gold']+v['predicted'] else 0,
                    'precision':v['tp']/v['predicted'] if v['predicted'] else 0,
                    'recall':v['tp']/total['gold'] if total['gold'] else 0,
                    'exact':v['exact'],'predicted':v['predicted']}
                for k,v in sorted(sweep.items(),key=lambda kv:float(kv[0]))}}


def deep_diagnosis(training_dir, plan_dir, capture_dir, *, step=None,
                   validation_pairs=16, training_pairs=16, cpu_threads=2):
    """Small, reproducible CPU-only audit of a *committed* checkpoint.

    Does not import the frozen Qwen architecture or instantiate upstream models;
    read-only validation and train features are used as already captured.
    """
    import torch
    from safetensors.torch import load_file
    from tools import nanojev_gamer_v1_g2_scaling_0059 as g
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    from tools.nanojev_gamer_v1_g2_repaired_graph_learning import RELATIONS

    torch.set_num_threads(cpu_threads)
    run=receipt_probe.snapshot(training_dir)
    if not run.get('history'):
        raise ValueError('No complete checkpoint receipts; nothing to inspect')
    choice=run['latest'] if step is None else next(
        (r for r in run['history'] if r['step']==step),None)
    if choice is None:raise ValueError(f'Checkpoint step {step} not committed')
    cfg=receipt_probe._load_json(training_dir/'training.json')
    if cfg.get('sealed_test_used') is not False or cfg.get('production_promotion') is not False:
        raise ValueError('Not an experimental train-only run')
    if cfg.get('capture_config_sha256')!=g.file_sha(capture_dir/'capture.json'):
        raise ValueError('Capture checksum disagrees with training configuration')
    if cfg.get('plan_sha256')!=g.file_sha(plan_dir/'plan.json'):
        raise ValueError('Plan checksum disagrees with training configuration')
    weights=training_dir/f'checkpoint-{choice["step"]:09d}.safetensors'
    if not weights.is_file() or g.file_sha(weights)!=choice.get('weights_sha256'):
        raise ValueError('Selected committed checkpoint weights missing or corrupted')
    model=p55.model_for('set_prediction').to('cpu')
    model.load_state_dict(load_file(str(weights),device='cpu'),strict=True)
    model.eval().requires_grad_(False)
    dataset={}
    for split,num,eligible in [('validation',validation_pairs,False),
                              ('train',training_pairs,True)]:
        if num<=0:continue
        ds=g.FeatureCorpus(plan_dir,capture_dir,split,require_complete=False)
        indices=_balanced_indices(ds,num,eligible_only=eligible)
        if not indices:raise ValueError(f'No eligible {split} pairs')
        case=[]
        for i in indices:
            samples,_=ds.pair(i)
            for sample in samples:
                case.append(_sample_case(model,sample,p55))
        dataset[split]=_aggregate_cases(case)
    return {'schema':SCHEMA,'status':'READ_ONLY_CPU_AUDIT',
            'checkpoint_step':choice['step'],'checkpoint_sha256':choice['weights_sha256'],
            'split_audits':dataset,'relation_names':list(RELATIONS),
            'selection_only_on_validation_receipts':True,
            'oracle_count_rank_uses_gold_count_for_diagnosis_only':True,
            'sampled_cohorts_are_not_full_validation_scores':True,
            'sealed_test_used':False,'production_promotion':False}


def format_deep_diagnosis(data):
    names=data['relation_names']; lines=[
        f"G2-0063 CPU POST-INFERENCE AUDIT — checkpoint {data['checkpoint_step']}",
        'Gold was read AFTER inference; oracle-count ranking is diagnostic only.',
        'These sampled split scores are NOT full-validation scores.']
    for split,stats in data['split_audits'].items():
        lines.append(f"{split.upper()}: {stats['graphs']} graphs, F1 {_pct(stats['f1'])}, "
                     f"exact {stats['exact']}/{stats['graphs']}, "
                     f"median {stats['median_errors_per_graph']:.1f} edge errors/graph, "
                     f"<=3 errors {stats['graphs_with_at_most_3_errors']}/{stats['graphs']}")
        lines.append(f"  Posthoc oracle-GOLD-count candidate ranking F1 "
                     f"{_pct(stats['oracle_count_rank_f1'])}, "
                     f"exact {stats['oracle_count_rank_exact']}/{stats['graphs']} "
                     '(NOT deployable).')
        dec=stats['decoder'];n=stats['graphs']
        lines.append(f"  Decoder per-graph means: non-null slots "
                     f"{dec.get('non_null_slots',0)/n:.1f}, "
                     f"typed-edge rejected {dec.get('typed_edge_rejected_slots',0)/n:.1f}, "
                     f"duplicates {dec.get('duplicate_edge_slots',0)/n:.1f}, "
                     f"truncated {dec.get('truncated_unique_edges',0)/n:.1f}.")
        indicators=stats['matched_slot_indicators'];den=max(1,stats['counts']['missing'])
        lines.append('  Among missed reachable gold edges, gold-matched slot indicators '
                     '(not exclusive causes):')
        for k,v in sorted(indicators.items(),key=lambda kv:-kv[1]):
            lines.append(f'    {k}: {v} ({_pct(v/den)} of all missed gold, including unreachable)')
        miss=stats['missed_by_relation'];extra=stats['extra_by_relation']
        def readable(counter):
            return ', '.join(f'{names[int(k)] if int(k)<len(names) else k}={v}'
                             for k,v in sorted(counter.items(),key=lambda kv:-kv[1])[:6]) or 'none'
        lines.append(f'  Most missed reachable gold relation types: {readable(miss)}')
        lines.append(f'  Most extra predicted relation types: {readable(extra)}')
        variants=stats.get('admission_margin_sweep',{})
        if variants:
            label,improved=max(variants.items(),key=lambda kv:kv[1]['f1'])
            lines.append(f'  Diagnostic admission-margin sweep (same weights):')
            lines.append(f'    Best in-sample margin {label}, F1 {_pct(improved["f1"])} '
                         f'vs actual decoder {_pct(stats["f1"])}; '
                         'NOT a selected inference policy.')
            for k,v in variants.items():
                lines.append(f'    margin {k}: P {_pct(v["precision"])}, R {_pct(v["recall"])}, '
                             f'F1 {_pct(v["f1"])}, exact {v["exact"]}/{stats["graphs"]}')
    if 'train' in data['split_audits'] and 'validation' in data['split_audits']:
        a,b=data['split_audits']['train'],data['split_audits']['validation']
        lines.append(f"Sampled reachable TRAIN vs all VALIDATION F1 gap "
                     f"{100*(a['f1']-b['f1']):+.2f} pp; NOT a controlled gap because "
                     'train excludes unreachable examples and sample sizes are small.')
    lines.append('Interpretation: compare the real decoder with oracle-count rank, '
                 'check slot/null/endpoint indicators, then design a controlled intervention.')
    return '\n'.join(lines)


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--training-dir',type=Path,default=DEFAULT_TRAINING)
    ap.add_argument('--target-f1',type=float,default=.95)
    ap.add_argument('--watch',action='store_true',help='Read checkpoint receipts repeatedly')
    ap.add_argument('--interval',type=float,default=30)
    ap.add_argument('--deep',action='store_true',help='CPU-only frozen-checkpoint feature audit')
    ap.add_argument('--checkpoint-step',type=int,help='Committed step, default latest')
    ap.add_argument('--plan',type=Path,default=DEFAULT_PLAN)
    ap.add_argument('--capture',type=Path,default=DEFAULT_CAPTURE)
    ap.add_argument('--validation-pairs',type=int,default=16)
    ap.add_argument('--training-pairs',type=int,default=16)
    ap.add_argument('--cpu-threads',type=int,default=2)
    args=ap.parse_args(argv)
    if (not math.isfinite(args.target_f1) or not 0<args.target_f1<1 or
        not math.isfinite(args.interval) or args.interval<=0):
        ap.error('target-f1 must be (0,1); interval must be finite and positive')
    if args.cpu_threads<1 or args.validation_pairs<0 or args.training_pairs<0:
        ap.error('cpu-threads must be positive; sampled pair counts nonnegative')
    if args.deep and args.watch:
        ap.error('--deep and --watch are mutually exclusive; deep replay is a separate bounded audit')
    directory=args.training_dir.resolve()
    print('READ-ONLY. Default mode: receipts only, no CUDA or model loads.',flush=True)
    last=None
    try:
        while True:
            s=receipt_probe.snapshot(directory)
            key=(s['status'],s.get('latest',{}).get('step'))
            if key!=last or not args.watch:
                report=classify_receipts(s,args.target_f1)
                print(format_receipt_diagnosis(report),flush=True)
                if args.deep:
                    print('Deep mode: CPU-only, hash-verifies chosen weights and reads captured TRAIN/VALIDATION shards.',flush=True)
                    detail=deep_diagnosis(directory,args.plan.resolve(),args.capture.resolve(),
                       step=args.checkpoint_step,validation_pairs=args.validation_pairs,
                       training_pairs=args.training_pairs,cpu_threads=args.cpu_threads)
                    print(format_deep_diagnosis(detail),flush=True)
                last=key
            if not args.watch:break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('Probe stopped. Training unaffected.',flush=True)
    except (OSError,ValueError,RuntimeError,AssertionError) as exc:
        ap.exit(2,f'G2_0063_DIAGNOSTIC_ERROR: {exc}; no experiment artifacts modified.\n')


if __name__=='__main__':
    main()
