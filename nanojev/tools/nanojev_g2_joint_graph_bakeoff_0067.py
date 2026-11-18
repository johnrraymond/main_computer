"""G2-0067: frozen checkpoint joint assignment and graph assembly bake-off.

Read-only experiment: no weight updates, no Qwen/CUDA, no sealed tests, no
promotion. Fits *only* conservative graph-degree priors on TRAIN gold. CAL
chooses discrete policies; EVAL is loaded after selections have been frozen.

Interventions (and their ablations):
  assignment: global maximum-weight slot-to-distinct-edge matching with a
              dummy/null option for each slot, vs independent best-edge votes.
  joint:      candidate-source/destination balanced joint score, vs the
              original additive joint candidate triple score.
  admission:  endpoint-aware relation-vs-null margin, vs a static margin.
  consistency: train-only empirical per-relation/node-kind degree caps,
              NOT asserted semantic one-to-one constraints.
Each is tested alone and in combination. No oracle information at inference.
These are diagnostic interventions on a historically used validation cohort,
NOT an independent certification or a guarantee of reaching 95% edge F1.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random

from tools import nanojev_g2_learning_probe as receipts

SCHEMA = 'nanojev.gamer.graph.g2.joint-graph-assembly-bakeoff-0067.v1'
ROOT = Path('..') / 'runtime' / 'reports' / 'gamer-v1'
DEFAULT_TRAINING = ROOT / 'graph-g2-0062-5000-4'
DEFAULT_CAPTURE = ROOT / 'graph-g2-0061-context-capture'
DEFAULT_PLAN = ROOT / 'graph-g2-0059-plan'
SEED = 640064  # intentionally matches G2-0064 / 0065 / 0066 CAL/EVAL pairs
REL_MARGIN = (1.0, 1.5, 2.0, 2.5)
ASSIGNMENT_BONUS = (0.5, 1.0, 2.0)
JOINT_BALANCE = (0.25, 0.5, 1.0)
ADMISSION_PENALTY = (0.25, 0.5, 1.0)
CONSISTENCY_QUANTILE = (0.90, 0.98)
TOP_K = 4


def _partition(ds, cal_pairs, eval_pairs):
    """Metadata-balanced and deterministic; pair disjoint without gold filtering."""
    if min(cal_pairs, eval_pairs) < 1:
        raise ValueError('Cal/eval pair counts must be positive')
    cells = defaultdict(list)
    for idx, (_shard, _pair, a, b) in enumerate(ds.pairs):
        if a['family'] != b['family'] or a['mode'] != b['mode']:
            raise ValueError('PAIR_METADATA_MISMATCH')
        cells[(a['family'], a['mode'])].append(idx)
    rng = random.Random(SEED)
    for idx in cells.values():
        rng.shuffle(idx)
    chosen = []
    while len(chosen) < cal_pairs + eval_pairs and any(cells.values()):
        for cell in sorted(cells):
            if cells[cell] and len(chosen) < cal_pairs + eval_pairs:
                chosen.append(cells[cell].pop())
    if len(chosen) != cal_pairs + eval_pairs:
        raise ValueError(f'Only {len(chosen)} validation pairs; requested {cal_pairs+eval_pairs}')
    return chosen[:cal_pairs], chosen[cal_pairs:]


def _train_indices(ds, n, budget):
    """Only reachable original-training-budget pairs, not over-captured shards."""
    if n < 1:
        raise ValueError('Train pair count must be positive')
    cells = defaultdict(list)
    for i in ds.indices:
        if i >= budget:
            continue
        meta = ds.pairs[i][2]
        cells[(meta['family'], meta['mode'])].append(i)
    rng = random.Random(670067)
    for idx in cells.values():
        rng.shuffle(idx)
    chosen = []
    while len(chosen) < n and any(cells.values()):
        for key in sorted(cells):
            if cells[key] and len(chosen) < n:
                chosen.append(cells[key].pop())
    if len(chosen) != n:
        raise ValueError(f'Only {len(chosen)} reachable train pairs')
    return chosen


def _sha_ids(cases):
    return hashlib.sha256('\n'.join(row['id'] for row in cases).encode()).hexdigest()


def _capture(model, ds, indices, p55, *, parity=True):
    """Inference accepts allowlisted features ONLY; gold read afterward."""
    import torch
    results = []
    names = ('node', 'src', 'dst', 'relation', 'kinds', 'margin', 'geometry')
    model.eval()
    with torch.inference_mode():
        for pair_index in indices:
            samples, _ = ds.pair(pair_index, with_labels=True)
            for sample in samples:
                features = {name: sample[name] for name in names}
                logits = tuple(x.detach().cpu().clone() for x in model(features))
                if parity and len(results) < 2:
                    inferred = p55.infer_edges(model, features, 'set_prediction')
                    if _decode(logits, features, {'margin': 0},
                               max_edge=p55.MAX_EDGE, use_consistency=False) != inferred:
                        raise ValueError('BASELINE_DECODER_PARITY_FAILED')
                # Gold boundary: no ground-truth access before independent forward.
                gold = frozenset(int(i) for i in sample['positive'].tolist())
                results.append({'logits': logits, 'features': features,
                                'gold': gold, 'truth_count': int(sample['truth_count']),
                                'family': sample['family'], 'mode': sample['mode'],
                                'pair_index': pair_index, 'id': sample['prompt_sha256']})
    return results


def _prepared(logits, features):
    """Frozen per-slot candidate scores and typed/null margins (no labels)."""
    import numpy as np
    import torch
    r, s, d = logits
    src_ids = features['src'].long()
    dst_ids = features['dst'].long()
    relation_ids = features['relation'].long() + 1
    if not len(src_ids):
        raise ValueError('CANDIDATE_GRAPH_EMPTY')
    with torch.no_grad():
        lr = torch.log_softmax(r, dim=-1)
        ls = torch.log_softmax(s, dim=-1)
        ld = torch.log_softmax(d, dim=-1)
        a = ls[:, src_ids]
        b = ld[:, dst_ids]
        c = lr[:, relation_ids]
        scores = (a+b+c).numpy()
        imbalance = (a - ls.max(dim=1).values[:, None] -
                     b + ld.max(dim=1).values[:, None]).abs().numpy()
        endpoint_cost = ((ls.max(dim=1).values[:, None]-a) +
                         (ld.max(dim=1).values[:, None]-b)).numpy()
        typed = (r[:, relation_ids]-r[:, 0][:, None]).numpy()
        active = (r.argmax(dim=1) != 0).numpy()
    return {'score':scores, 'imbalance':imbalance, 'cost':endpoint_cost,
            'typed':typed, 'active':active}


def _assignment(proposals, bonus):
    """Global unique-edge maximum-weight matching, with null dummy columns.

    Each active slot nominates up to TOP_K valid edges. The reward for its
    best candidate is `bonus`; alternatives pay the relative score deficit.
    A null/dummy has zero reward and cannot be emitted as a graph edge.
    """
    import numpy as np
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    if not proposals:
        return []
    q_ids = sorted(proposals)
    all_edges = sorted({i for p in proposals.values() for i, _score in p})
    if not all_edges:
        return []
    col = {edge:i for i,edge in enumerate(all_edges)}
    size = (len(q_ids), len(all_edges)+len(q_ids))
    utilities = np.full(size, -1e8, dtype=np.float64)
    utilities[:, len(all_edges):] = 0.0  # every slot may choose null
    for row,slot in enumerate(q_ids):
        highest = max(x[1] for x in proposals[slot])
        for edge,score in proposals[slot]:
            utilities[row,col[edge]] = bonus + score-highest
    try:
        from scipy.optimize import linear_sum_assignment
    except ImportError:
        rows, cols = p55.rectangular_assignment(-utilities)
    else:
        rows, cols = linear_sum_assignment(-utilities)
    out = []
    for row,column in zip(rows,cols):
        if column >= len(all_edges) or utilities[row,column] <= 0:
            continue
        slot=q_ids[row];edge=all_edges[column]
        selected_score = next(value for i,value in proposals[slot] if i==edge)
        out.append((edge,slot,selected_score))
    return sorted(out,key=lambda x:(-x[2],x[0],x[1]))


def _degree_profile(cases):
    """TRAIN labels only. Empirical degree prior, never a semantic invariant."""
    counters = defaultdict(list)
    for case in cases:
        features = case['features']
        if len(case['gold']) != case['truth_count']:
            raise ValueError('UNREACHABLE_TRAIN_GRAPH_FOR_DEGREE_PROFILE')
        src=features['src'].tolist();dst=features['dst'].tolist()
        rel=features['relation'].tolist();kind=features['kinds'].tolist()
        degree=Counter()
        for edge in case['gold']:
            degree[('out',int(rel[edge]),int(kind[src[edge]]),int(src[edge]))]+=1
            degree[('in',int(rel[edge]),int(kind[dst[edge]]),int(dst[edge]))]+=1
        for (direction,relation,kind_id,_node),num in degree.items():
            counters[(direction,relation,kind_id)].append(num)
    return counters


def _caps(profile,quantile,min_support=12):
    """Conservative quantile of *observed positive* train degrees only."""
    import math
    if quantile is None:return {}
    caps={}
    for key,counts in profile.items():
        if len(counts)<min_support:continue
        ordered=sorted(counts)
        pos=min(len(ordered)-1,max(0,math.ceil(quantile*len(ordered))-1))
        caps[key]=max(2,int(ordered[pos]))
    return caps


def _graph_consistency(proposals, features, caps):
    """Soft empirical degree gate on predicted graph; never invent type rules."""
    if not caps:return proposals
    src=features['src'].tolist();dst=features['dst'].tolist()
    rel=features['relation'].tolist();kind=features['kinds'].tolist()
    degree=Counter();filtered=[]
    for e,q,score in proposals:
        a=('out',int(rel[e]),int(kind[src[e]]));b=('in',int(rel[e]),int(kind[dst[e]]))
        left=(a,int(src[e]));right=(b,int(dst[e]))
        if degree[left] >= caps.get(a, 10**9) or degree[right] >= caps.get(b, 10**9):
            continue
        degree[left]+=1;degree[right]+=1
        filtered.append((e,q,score))
    return filtered


def _decode(logits, features, policy, *, max_edge=96, use_consistency=True,
            prepared=None, caps=None):
    """Entirely label-blind decoder. Public input is only logits/features/policy."""
    import numpy as np
    cache=prepared if prepared is not None else _prepared(logits,features)
    margin=float(policy.get('margin',0))
    balance=float(policy.get('joint',0))
    penalty=float(policy.get('admission',0))
    assign=float(policy.get('assignment',0))
    if min(margin,balance,penalty,assign)<0 or not all(
        math.isfinite(x) for x in (margin,balance,penalty,assign)):
        raise ValueError('INVALID_DECODER_POLICY')
    scores=cache['score']-balance*cache['imbalance']
    # Endpoint-aware null margin; component-dependent validity constraint.
    valid=(cache['typed']>margin+penalty*cache['cost'])
    valid &= cache['active'][:, None]
    max_edge=int(max_edge)
    if max_edge<1:raise ValueError('MAX_EDGE_INVALID')
    if assign:
        candidates={}
        for q in np.flatnonzero(cache['active']):
            eligible=np.flatnonzero(valid[q])
            if not eligible.size:continue
            order=np.argsort(-scores[q,eligible],kind='stable')[:TOP_K]
            candidates[int(q)]=[(int(eligible[j]),float(scores[q,eligible[j]])) for j in order]
        proposed=_assignment(candidates,assign)
    else:
        # Exactly the original independently chosen, deduplicated slot policy
        # when joint=admission=margin=0. No gold cardinality is consulted.
        unique={}
        for q in np.flatnonzero(cache['active']):
            # A slot first chooses its best admissible typed edge irrespective
            # of the additional threshold, as in the original G2 decoder.
            # For admission>0, allow its next-best eligible edge as a new arm.
            if penalty == 0:
                edge=int(scores[q].argmax())
                if not valid[q,edge]:continue
            else:
                eligible=np.flatnonzero(valid[q])
                if not eligible.size:continue
                edge=int(eligible[scores[q,eligible].argmax()])
            score=float(scores[q,edge])
            if edge not in unique or score>unique[edge][1]:
                unique[edge]=(int(q),score)
        proposed=[(edge,q,score) for edge,(q,score) in unique.items()]
        proposed.sort(key=lambda x:(-x[2],x[0],x[1]))
    if use_consistency and policy.get('consistency') is not None:
        proposed=_graph_consistency(proposed,features,caps or {})
    return set(edge for edge,_slot,_score in proposed[:max_edge])


def _new_stats():
    return Counter(graphs=0,exact=0,gold=0,pred=0,tp=0,missing=0,extra=0,
                   candidate_reachable=0)


def _add(s,pred,gold,truth):
    tp=len(pred&gold)
    s.update({'graphs':1,'gold':truth,'pred':len(pred),'tp':tp,
              'missing':truth-tp,'extra':len(pred-gold),
              'exact':int(pred==gold and len(gold)==truth),
              'candidate_reachable':int(len(gold)==truth)})


def _summary(s):
    return {'graphs':s['graphs'],'f1':2*s['tp']/(s['gold']+s['pred']) if s['gold']+s['pred'] else 0.,
            'precision':s['tp']/s['pred'] if s['pred'] else 0.,
            'recall':s['tp']/s['gold'] if s['gold'] else 0.,
            'missing':s['missing'],'extra':s['extra'],'exact':s['exact'],
            'edge_errors':s['missing']+s['extra'],
            'gold_edges':s['gold'],'predicted_edges':s['pred'],
            'candidate_reachable':s['candidate_reachable']}


def _eval(cases, policy, profile, *, with_details=False):
    total=_new_stats();family=defaultdict(_new_stats);mode=defaultdict(_new_stats)
    pair=defaultdict(_new_stats)
    capset=_caps(profile,policy.get('consistency')) if policy.get('consistency') is not None else {}
    for case in cases:
        pred=_decode(case['logits'],case['features'],policy,
                     caps=capset,prepared=case.get('prepared'))
        for s in (total,family[case['family']],mode[case['mode']],pair[case['pair_index']]):
            _add(s,pred,case['gold'],case['truth_count'])
    result={'overall':_summary(total)}
    if with_details:
        result.update({'by_family':{k:_summary(v) for k,v in sorted(family.items())},
                       'by_mode':{k:_summary(v) for k,v in sorted(mode.items())},
                       'by_pair':{str(k):_summary(v) for k,v in sorted(pair.items())}})
    return result


def _choose(cases, options, profile):
    """Calibration-only selection, deterministic tie-breaking toward less change."""
    if not options:raise ValueError('NO_CANDIDATE_POLICIES')
    best=None
    for order,policy in enumerate(options):
        score=_eval(cases,policy,profile)['overall']
        key=(score['f1'],-score['edge_errors'],-order)
        if best is None or key>best[0]:best=(key,dict(policy),score)
    return best[1],best[2]


def _calibrate(cases, profile):
    """Small predeclared policy search on CAL only; EVAL loaded afterward."""
    margins=[{'margin':m} for m in REL_MARGIN]
    base,score=_choose(cases,margins,profile)
    margin=base['margin']
    choices={
        'calibrated_baseline':(base,score),
        'global_assignment':_choose(cases,[base,*[{**base,'assignment':x} for x in ASSIGNMENT_BONUS]],profile),
        'joint_pair_score':_choose(cases,[base,*[{**base,'joint':x} for x in JOINT_BALANCE]],profile),
        'confidence_admission':_choose(cases,[base,*[{**base,'admission':x} for x in ADMISSION_PENALTY]],profile),
        'degree_consistency':_choose(cases,[base,*[{**base,'consistency':q} for q in CONSISTENCY_QUANTILE]],profile),
    }
    # Combined policies compare against all single arms, preserving a fallback
    # to the CAL winner rather than forcing an improvement by construction.
    components={name:dict(row[0]) for name,row in choices.items() if name!='calibrated_baseline'}
    paired=[]
    for x in ('global_assignment','joint_pair_score','confidence_admission','degree_consistency'):
        for y in ('global_assignment','joint_pair_score','confidence_admission','degree_consistency'):
            if x>=y:continue
            merged={**base,**components[x],**components[y]}
            paired.append(merged)
    choice_pair=_choose(cases,[base,*paired],profile)
    combined={**base}
    for policy in components.values():combined.update(policy)
    full=_choose(cases,[base,*[row[0] for row in choices.values()],choice_pair[0],combined],profile)
    choices['paired_assembly']=choice_pair
    choices['combined_assembly']=full
    return choices


def _bootstrap(ref,changed,reps=400):
    """Pair-block bootstrap for exploratory intervals; no test access."""
    a=ref['by_pair'];b=changed['by_pair'];keys=sorted(a)
    if set(keys)!=set(b):raise ValueError('BOOTSTRAP_PAIR_ALIGNMENT')
    rng=random.Random(670067)
    def pooled(samples,source):
        tp=sum(source[key]['gold_edges']-source[key]['missing'] for key in samples)
        gold=sum(source[key]['gold_edges'] for key in samples)
        pred=sum(source[key]['predicted_edges'] for key in samples)
        return 2*tp/(gold+pred) if gold+pred else 0
    vals=[]
    for _ in range(reps):
        sampled=[keys[rng.randrange(len(keys))] for i in keys]
        vals.append(100*(pooled(sampled,b)-pooled(sampled,a)))
    vals.sort()
    return [round(vals[int(reps*.025)],3),round(vals[int(reps*.975)-1],3)]


def _load_model_and_data(train_dir,plan_dir,capture_dir,checkpoint_step,cpu_threads):
    import torch
    from safetensors.torch import load_file
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    from tools import nanojev_gamer_v1_g2_scaling_0059 as scale
    torch.set_num_threads(cpu_threads)
    state=receipts.snapshot(train_dir)
    if state.get('status')!='HAS_OPTIMIZER_CHECKPOINTS':
        raise ValueError('NO_COMMITTED_TRAINING_CHECKPOINT')
    checkpoint=next((x for x in state['history'] if x['step']==checkpoint_step),None)
    if checkpoint is None:raise ValueError('CHECKPOINT_NOT_COMMITTED')
    config=receipts._load_json(train_dir/'training.json')
    if config.get('sealed_test_used') is not False or config.get('production_promotion') is not False:
        raise ValueError('EXPERIMENTAL_ONLY_GATE')
    if config.get('plan_sha256')!=scale.file_sha(plan_dir/'plan.json'):
        raise ValueError('PLAN_HASH_MISMATCH')
    if config.get('capture_config_sha256')!=scale.file_sha(capture_dir/'capture.json'):
        raise ValueError('CAPTURE_HASH_MISMATCH')
    weights=train_dir/f'checkpoint-{checkpoint_step:09d}.safetensors'
    if not weights.is_file() or scale.file_sha(weights)!=checkpoint.get('weights_sha256'):
        raise ValueError('CHECKPOINT_WEIGHT_HASH_MISMATCH')
    model=p55.model_for('set_prediction').cpu()
    model.load_state_dict(load_file(str(weights),device='cpu'),strict=True)
    model.eval().requires_grad_(False)
    train=scale.FeatureCorpus(plan_dir,capture_dir,'train',require_complete=False)
    validation=scale.FeatureCorpus(plan_dir,capture_dir,'validation',require_complete=False)
    if train.config.get('candidate_policy')!='actor_cues' or validation.config.get('candidate_policy')!='actor_cues':
        raise ValueError('CANDIDATE_POLICY_DRIFT')
    return model,train,validation,p55,config,checkpoint


def run(*,training_dir,plan_dir,capture_dir,checkpoint_step=4900,
        training_pairs=96,calibration_pairs=24,evaluation_pairs=48,cpu_threads=2):
    if min(training_pairs,calibration_pairs,evaluation_pairs,cpu_threads)<1:
        raise ValueError('POSITIVE_COUNTS_REQUIRED')
    model,train,validation,p55,config,ckpt=_load_model_and_data(
        training_dir,plan_dir,capture_dir,checkpoint_step,cpu_threads)
    original_pairs=int(config.get('budget',{}).get('requested_training_graphs',5000))//2
    train_idx=_train_indices(train,training_pairs,original_pairs)
    cal_idx,eval_idx=_partition(validation,calibration_pairs,evaluation_pairs)
    if set(cal_idx)&set(eval_idx):raise ValueError('CAL_EVAL_OVERLAP')
    training_cases=_capture(model,train,train_idx,p55)
    profile=_degree_profile(training_cases)  # TRAIN labels only
    calibration=_capture(model,validation,cal_idx,p55)
    for row in calibration:row['prepared']=_prepared(row['logits'],row['features'])
    selections=_calibrate(calibration,profile)
    # Evaluation examples and labels accessed only after calibration completed.
    evaluation=_capture(model,validation,eval_idx,p55)
    for row in evaluation:row['prepared']=_prepared(row['logits'],row['features'])
    result={name:_eval(evaluation,policy,profile,with_details=True)
            for name,(policy,_cal_score) in selections.items()}
    baseline=result['calibrated_baseline']
    ci={name:_bootstrap(baseline,metrics) for name,metrics in result.items()
        if name!='calibrated_baseline'}
    verdicts={name:{'delta_f1_pp':round(100*(metrics['overall']['f1']-
                    baseline['overall']['f1']),4),'paired_ci_95_pp':ci[name],
                    'improvement_supported_on_historical_cohort':(
                     metrics['overall']['f1']>baseline['overall']['f1'] and ci[name][0]>0)}
              for name,metrics in result.items() if name!='calibrated_baseline'}
    return {'schema':SCHEMA,'status':'EXPERIMENTAL_GRAPH_ASSEMBLY_BAKEOFF_NOT_CERTIFIED',
            'checkpoint_step':checkpoint_step,'checkpoint_sha256':ckpt['weights_sha256'],
            'plan_sha256':config['plan_sha256'],'capture_sha256':config['capture_config_sha256'],
            'split':{'train_pairs':len(train_idx),'cal_pairs':len(cal_idx),
                     'eval_pairs':len(eval_idx),'cal_prompt_digest':_sha_ids(calibration),
                     'eval_prompt_digest':_sha_ids(evaluation)},
            'learned_degree_profile':{'groups':len(profile),
                'eligible_groups_at_90pct':len(_caps(profile,0.9))},
            'calibration':{name:{'policy':policy,'cal_f1':cal['f1']}
                           for name,(policy,cal) in selections.items()},
            'evaluation':{name:{key:value for key,value in metrics.items() if key!='by_pair'}
                          for name,metrics in result.items()},
            'hypothesis_results':verdicts,
            'target_95_edge_f1_reached':any(x['overall']['f1']>=0.95 for x in result.values()),
            'sealed_test_used':False,
            'production_promotion':False,'gold_at_inference':False,
            'warnings':[
                'TRAIN gold is used only to fit degree prior, never as a graph inference input.',
                'Empirical degree caps are hypotheses, not asserted semantic invariants.',
                'Checkpoint was previously selected using this validation cohort; results are research-only.',
                'CAL/EVAL are pair-disjoint; policies are calibrated before EVAL access.',
                '95% edge F1 and 95% exact-graph accuracy are separate goals.',
                'No sealed test/challenge, CUDA training, optimizer updates, or production promotion.']}


def format_report(r):
    lines=[f'G2-0067 JOINT EDGE ASSIGNMENT / GRAPH ASSEMBLY BAKE-OFF — checkpoint {r["checkpoint_step"]}',
           'CPU only, frozen SHA-256 '+r['checkpoint_sha256'],
           f'TRAIN {r["split"]["train_pairs"]} pairs | CAL {r["split"]["cal_pairs"]} pairs | '
           f'EVAL {r["split"]["eval_pairs"]} distinct pairs',
           'Non-oracle EVAL. Historical validation, NOT independent certification.',
           'Arm                        Precision  Recall     F1       dF1    Miss  Extra  Exact']
    ref=r['evaluation']['calibrated_baseline']['overall']['f1']
    for name,report in r['evaluation'].items():
        x=report['overall']
        lines.append(f'{name:<27}{100*x["precision"]:7.2f}% '
                     f'{100*x["recall"]:7.2f}% {100*x["f1"]:7.2f}% '
                     f'{100*(x["f1"]-ref):+7.2f}pp {x["missing"]:6} '
                     f'{x["extra"]:6} {x["exact"]:3}/{x["graphs"]}')
    for name,h in r['hypothesis_results'].items():
        lines.append(f'  {name}: paired 95% ΔF1 {h["paired_ci_95_pp"]} pp; '
                     f'supported={h["improvement_supported_on_historical_cohort"]}')
    for name in ('calibrated_baseline','combined_assembly'):
        family=r['evaluation'].get(name,{}).get('by_family',{})
        if not family:continue
        lines.append(name+' family F1: '+', '.join(
            f'{key}={100*value["f1"]:.2f}%' for key,value in sorted(family.items())))
    lines.append('CAL-selected policies: '+repr({k:v['policy'] for k,v in r['calibration'].items()}))
    lines.append('CONSISTENCY: empirical TRAIN-only degree caps, not an assumed one-to-one relation.')
    lines.append('No gold at inference, sealed test, weight mutation, or production promotion.')
    return '\n'.join(lines)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--training-dir',type=Path,default=DEFAULT_TRAINING)
    p.add_argument('--plan',type=Path,default=DEFAULT_PLAN)
    p.add_argument('--capture',type=Path,default=DEFAULT_CAPTURE)
    p.add_argument('--checkpoint-step',type=int,default=4900)
    p.add_argument('--training-pairs',type=int,default=96)
    p.add_argument('--calibration-pairs',type=int,default=24)
    p.add_argument('--evaluation-pairs',type=int,default=48)
    p.add_argument('--cpu-threads',type=int,default=2)
    p.add_argument('--output',type=Path)
    args=p.parse_args(argv)
    try:
        report=run(training_dir=args.training_dir.resolve(),plan_dir=args.plan.resolve(),
                   capture_dir=args.capture.resolve(),checkpoint_step=args.checkpoint_step,
                   training_pairs=args.training_pairs,calibration_pairs=args.calibration_pairs,
                   evaluation_pairs=args.evaluation_pairs,cpu_threads=args.cpu_threads)
        print(format_report(report),flush=True)
        if args.output:
            if args.output.exists():raise ValueError('Refusing to overwrite existing report')
            args.output.parent.mkdir(exist_ok=True,parents=True)
            with args.output.open('x',encoding='utf8') as file:
                json.dump(report,file,sort_keys=True,indent=2)
                file.write('\n')
            print('New research report: '+str(args.output),flush=True)
    except (ValueError,RuntimeError,OSError,AssertionError) as exc:
        p.exit(2,f'G2_0067_BAKEOFF_ERROR: {exc}; existing artifacts unchanged.\n')


if __name__=='__main__':
    main()
