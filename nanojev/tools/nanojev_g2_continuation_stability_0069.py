"""G2-0069: controlled optimizer-continuation and replay-diversity bake-off.

Six equally budgeted arms (3 learning rates x {96,384} UNIQUE training
pairs), all starting at the exact frozen G2-0062 step-4900 weights, with
the ORIGINAL G2-0057 D_combined paired-set objective and AdamW unchanged.

Optional seventh arm loads the original SHA-verified step-4900 AdamW state
and shares the 384-pair/5e-4 training schedule with a fresh-optimizer arm.
Checkpoint optimizer files are read-only. No source checkpoint is rewritten.

Train/calibration/evaluation remain split by corpus. Intermediate steps are
scored on CAL only. Two checkpoint rules are predeclared: fixed last step and
best nonzero step selected on CAL. EVAL is accessed only after all model,
margin and checkpoint decisions have been frozen. EVAL is historical G2-0062
validation, NOT independent certification. No test/challenge, Qwen, capture,
production promotion, or output overwrites.

Run from main_computer/nanojev after applying this patch.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random

from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
from tools import nanojev_gamer_v1_g2_counterfactual_bakeoff_0057 as p57
from tools import nanojev_gamer_v1_g2_scaling_0059 as scale

SCHEMA = 'nanojev.gamer.graph.g2.continuation-stability-0069.v1'
DEFAULT_TRAINING = Path('..')/'runtime'/'reports'/'gamer-v1'/'graph-g2-0062-5000-4'
DEFAULT_PLAN = Path('..')/'runtime'/'reports'/'gamer-v1'/'graph-g2-0059-plan'
DEFAULT_CAPTURE = Path('..')/'runtime'/'reports'/'gamer-v1'/'graph-g2-0061-context-capture'
SEED = 690069
MARGINS = (0., 0.5, 1., 1.5, 2., 2.5, 3.)
RATES = (5e-4, 1e-4, 3e-5)


def _fingerprint(values):
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _budget_graphs(training_config):
    plan=training_config.get('training_budget') or training_config.get('budget') or {}
    count=plan.get('requested_training_graphs')
    if not isinstance(count,int) or count<768 or count%2:
        raise ValueError('ORIGINAL_TRAINING_GRAPH_BUDGET_UNVERIFIABLE')
    return count


def _load_parent(training_dir,plan_dir,capture_dir,step,device,with_optimizer):
    """Validate receipt/weight/optimizer byte integrity BEFORE any training."""
    import torch
    from safetensors.torch import load_file
    cfg=json.loads((training_dir/'training.json').read_text(encoding='utf8'))
    if cfg.get('sealed_test_used') is not False or cfg.get('production_promotion') is not False:
        raise ValueError('NOT_RESEARCH_ONLY_TRAINING_SOURCE')
    if cfg.get('plan_sha256')!=scale.file_sha(plan_dir/'plan.json'):
        raise ValueError('PLAN_LINEAGE_HASH_MISMATCH')
    if cfg.get('capture_config_sha256')!=scale.file_sha(capture_dir/'capture.json'):
        raise ValueError('CAPTURE_LINEAGE_HASH_MISMATCH')
    budget=_budget_graphs(cfg)
    meta=training_dir/f'checkpoint-{step:09d}.json'
    receipt=json.loads(meta.read_text(encoding='utf8'))
    if receipt.get('step')!=step or not isinstance(receipt.get('weights_sha256'),str):
        raise ValueError('CHECKPOINT_RECEIPT_INVALID')
    weights=training_dir/f'checkpoint-{step:09d}.safetensors'
    if scale.file_sha(weights)!=receipt['weights_sha256']:
        raise ValueError('CHECKPOINT_WEIGHTS_HASH_MISMATCH')
    optim_path=training_dir/f'checkpoint-{step:09d}.optimizer.pt'
    if with_optimizer:
        if not optim_path.is_file() or not receipt.get('optimizer_sha256'):
            raise ValueError('ORIGINAL_OPTIMIZER_STATE_UNAVAILABLE')
        if scale.file_sha(optim_path)!=receipt['optimizer_sha256']:
            raise ValueError('ORIGINAL_OPTIMIZER_HASH_MISMATCH')
    model=p55.model_for('set_prediction').cpu()
    model.load_state_dict(load_file(str(weights),device='cpu'),strict=True)
    model.eval().requires_grad_(False)
    state={k:v.cpu().detach().clone() for k,v in model.state_dict().items()}
    if any(not bool(torch.isfinite(v).all()) for v in state.values()):
        raise ValueError('NONFINITE_SOURCE_WEIGHTS')
    train=scale.FeatureCorpus(plan_dir,capture_dir,'train',require_complete=False)
    validation=scale.FeatureCorpus(plan_dir,capture_dir,'validation',require_complete=False)
    if (train.config.get('candidate_policy')!='actor_cues' or
        validation.config.get('candidate_policy')!='actor_cues'):
        raise ValueError('FROZEN_CANDIDATE_POLICY_MISMATCH')
    return state,train,validation,cfg,receipt,optim_path,budget


def _split_validation(ds,cal_pairs,eval_pairs):
    """Use G2-0064..0068 seeded stratification for directly comparable EVAL."""
    if min(cal_pairs,eval_pairs)<1:raise ValueError('CAL_EVAL_COUNTS_POSITIVE')
    rng=random.Random(640064)
    cells=defaultdict(list)
    for i,(_,_,a,b) in enumerate(ds.pairs):
        if a['family']!=b['family'] or a['mode']!=b['mode']:
            raise ValueError('PAIR_METADATA_MISMATCH')
        cells[(a['family'],a['mode'])].append(i)
    for group in cells.values():rng.shuffle(group)
    ids=[]
    while len(ids)<cal_pairs+eval_pairs and any(cells.values()):
        for cell in sorted(cells):
            if cells[cell] and len(ids)<cal_pairs+eval_pairs:
                ids.append(cells[cell].pop())
    if len(ids)!=cal_pairs+eval_pairs:
        raise ValueError('INSUFFICIENT_HELDOUT_PAIRS')
    return ids[:cal_pairs],ids[cal_pairs:]


def _nested_train_pools(ds,low,high,limit):
    """Balanced, NESTED, reachable training pools within original G2 budget."""
    if min(low,high)<1 or low>=high or high%4 or low%4:
        raise ValueError('TRAIN_DIVERSITY_COUNTS_REQUIRE_FOUR_CELLS_AND_LOW_LT_HIGH')
    cells=defaultdict(list)
    for i in ds.indices:
        if i>=limit//2:continue
        a=ds.pairs[i][2]
        cells[(a['family'],a['mode'])].append(i)
    if len(cells)!=4:raise ValueError('TRAIN_MISSING_FAMILY_MODE_CELL')
    rng=random.Random(SEED)
    for group in cells.values():rng.shuffle(group)
    order=[]
    while len(order)<high and any(cells.values()):
        for key in sorted(cells):
            if cells[key] and len(order)<high:order.append(cells[key].pop())
    if len(order)!=high:raise ValueError('NOT_ENOUGH_REACHABLE_TRAIN_PAIRS')
    return order[:low],order


def _schedule(ids,steps,batch_pairs):
    """Equal optimizer steps and pair presentations, cycling low-pool replay."""
    if (steps<1 or not 1<=batch_pairs<=8 or
        len(ids)<batch_pairs or len(ids)%batch_pairs or steps*batch_pairs%len(ids)):
        raise ValueError('PAIR_POOL_CANNOT_MAKE_EXACT_EQUAL_STEP_BUDGET')
    rng=random.Random(SEED+7)
    result=[]
    while len(result)<steps:
        perm=list(ids);rng.shuffle(perm)
        result.extend(tuple(perm[i:i+batch_pairs]) for i in range(0,len(ids),batch_pairs))
    if len(result)!=steps:raise ValueError('PARTIAL_EPOCH_IN_SCHEDULE')
    return tuple(result)


def _new_model(weights,device):
    model=p55.model_for('set_prediction')
    model.load_state_dict(weights,strict=True)
    return model.to(device).requires_grad_(True)


def _train_step(model,optimizer,ds,indices,device):
    """Exact original G2 D_combined batch objective and gradient clipping."""
    import torch
    pairs=[];rows={}
    for i in indices:
        (a,b),receipt=ds.pair(i,with_labels=True)
        if (len(a['positive'])!=a['truth_count'] or
            len(b['positive'])!=b['truth_count'] or
            'training_node_ids' not in a or 'training_node_ids' not in b):
            raise ValueError('UNREACHABLE_OR_UNALIGNED_TRAIN_PAIR')
        pairs.append(receipt)
        rows[a['prompt_sha256']]=a;rows[b['prompt_sha256']]=b
    model.train();optimizer.zero_grad(set_to_none=True)
    loss=p57.batch_objective(model,pairs,rows,'D_combined')
    if not bool(torch.isfinite(loss)):raise ValueError('NONFINITE_CONTINUATION_LOSS')
    loss.backward()
    norm=torch.nn.utils.clip_grad_norm_(model.parameters(),5.)
    if not bool(torch.isfinite(norm)):raise ValueError('NONFINITE_CONTINUATION_GRADIENT')
    optimizer.step()
    return float(loss.detach().cpu())


def _snap(model):
    """Temporary in-memory copy only; no new checkpoint files on disk."""
    return {k:v.detach().cpu().clone() for k,v in model.state_dict().items()}


def _run_arm(state,ds,schedule,*,lr,device,milestones,optimizer_path=None):
    import torch
    model=_new_model(state,device)
    opt=torch.optim.AdamW(model.parameters(),lr=lr)
    status='fresh'
    if optimizer_path is not None:
        payload=torch.load(optimizer_path,map_location=device,weights_only=True)
        opt.load_state_dict(payload)
        for group in opt.param_groups:group['lr']=lr
        status='restored'
    states={0:_snap(model)}
    losses=[]
    for step,ids in enumerate(schedule,1):
        current=_train_step(model,opt,ds,ids,device)
        if step==1 or step in milestones:
            losses.append({'step':step,'loss':round(current,6)})
            print('G2_0069_TRAIN '+json.dumps({'optimizer':status,'step':step,
                    'total_steps':len(schedule),'rate':lr,'loss':round(current,6)},
                    sort_keys=True),flush=True)
        if step in milestones:states[step]=_snap(model)
    del model,opt
    return states,losses


def _infer_from_logits(logits,features,margin):
    """Original G2 slot choice and edge cap, plus predeclared margin."""
    import torch
    import torch.nn.functional as F
    r,s,d=logits
    src=features['src'].to(r.device);dst=features['dst'].to(r.device)
    rel=features['relation'].to(r.device)+1
    lr=F.log_softmax(r,-1);ls=F.log_softmax(s,-1);ld=F.log_softmax(d,-1)
    selected={}
    for slot in range(p55.SLOTS):
        if int(r[slot].argmax())==0:continue
        scores=ls[slot,src]+ld[slot,dst]+lr[slot,rel]
        edge=int(scores.argmax())
        if float(r[slot,rel[edge]]-r[slot,0])<=margin:continue
        confidence=float(scores[edge])
        if edge not in selected or confidence>selected[edge]:
            selected[edge]=confidence
    return set(sorted(selected,key=lambda e:-selected[e])[:p55.MAX_EDGE])


def _infer(model,sample,margin):
    """Public no-label model decoder, identical at margin zero to G2-0055."""
    features={name:sample[name] for name in scale.FEATURE_FIELDS}
    return _infer_from_logits(model(features),features,margin)


def _collect(model,ds,indices,margin=None):
    """No gold or canonical metadata forwarded to model; labels used for scoring."""
    import torch
    model.eval()
    cases=[]
    with torch.inference_mode():
        for idx in indices:
            samples,receipt=ds.pair(idx,with_labels=True)
            for sample in samples:
                features={name:sample[name] for name in scale.FEATURE_FIELDS}
                logits=model(features)  # Exactly one forward per graph at each snapshot.
                if margin is None:
                    pred={m:_infer_from_logits(logits,features,m) for m in MARGINS}
                else:
                    pred={margin:_infer_from_logits(logits,features,margin)}
                gold=frozenset(int(x) for x in sample['positive'].tolist())
                cases.append({'pair':idx,'id':sample['prompt_sha256'],
                              'family':sample['family'],'mode':sample['mode'],
                              'gold':gold,'truth_count':int(sample['truth_count']),
                              'pred':pred})
    return cases


def _update(count,pred,gold,truth):
    tp=len(pred&gold)
    count.update({'tp':tp,'pred':len(pred),'gold':truth,
                  'missing':truth-tp,'extra':len(pred-gold),
                  'graphs':1,'exact':int(pred==gold and truth==len(gold)),
                  'candidate_reachable':int(truth==len(gold))})


def _metrics(c):
    tp=c['tp'];pred=c['pred'];gold=c['gold']
    return {'graphs':c['graphs'],'f1':2*tp/(pred+gold) if pred+gold else 0.,
            'precision':tp/pred if pred else 0.,'recall':tp/gold if gold else 0.,
            'exact':c['exact'],'missing':c['missing'],'extra':c['extra'],
            'edge_errors':c['missing']+c['extra'],'tp':tp,
            'gold_edges':gold,'predicted_edges':pred,
            'candidate_reachable':c['candidate_reachable']}


def _scores(cases,margin,details=False):
    aggregate=Counter();per_pair=defaultdict(Counter);family=defaultdict(Counter)
    for case in cases:
        prediction=case['pred'][margin]
        _update(aggregate,prediction,case['gold'],case['truth_count'])
        _update(per_pair[case['pair']],prediction,case['gold'],case['truth_count'])
        _update(family[case['family']],prediction,case['gold'],case['truth_count'])
    result={'overall':_metrics(aggregate)}
    if details:
        result['by_pair']={str(key):_metrics(row) for key,row in per_pair.items()}
        result['by_family']={key:_metrics(row) for key,row in family.items()}
    return result


def _select_margin(cases):
    values=[(m,_scores(cases,m)['overall']) for m in MARGINS]
    return max(values,key=lambda x:(x[1]['f1'],-x[1]['edge_errors'],-abs(x[0]-1.5),-x[0]))


def _calibrate_states(states,ds,indices,*,device='cpu'):
    """CAL only; independently selects margin at each predefined milestone."""
    rows=[]
    for step,weights in sorted(states.items()):
        model=_new_model(weights,device).eval().requires_grad_(False)
        cases=_collect(model,ds,indices)
        margin,score=_select_margin(cases)
        rows.append({'step':step,'margin':margin,'cal_f1':score['f1'],
                     'cal_exact':score['exact'],'cal_edge_errors':score['edge_errors']})
        del model
    return rows


def _select_checkpoint(curve,steps):
    """Only CAL evidence selects the alternate checkpoint; excludes step zero."""
    rows=[r for r in curve if r['step']>0]
    if not rows:raise ValueError('NO_CONTINUATION_SNAPSHOTS')
    # predeclared: CAL F1 > fewer errors > later step as deterministic tiebreak
    selected=max(rows,key=lambda r:(r['cal_f1'],-r['cal_edge_errors'],r['step']))
    return selected['step']


def _bootstrap(control,comparison,reps=400):
    a=control['by_pair'];b=comparison['by_pair']
    keys=sorted(a)
    if set(keys)!=set(b):raise ValueError('EVAL_PAIR_MISMATCH')
    rng=random.Random(SEED)
    def f1(values):
        total_tp=sum(v['tp'] for v in values)
        denom=sum(v['gold_edges']+v['predicted_edges'] for v in values)
        return 2*total_tp/denom if denom else 0.
    diff=[]
    for _ in range(reps):
        drawn=[keys[rng.randrange(len(keys))] for _ in keys]
        diff.append(100*(f1([b[i] for i in drawn])-f1([a[i] for i in drawn])))
    diff.sort()
    return [round(diff[int(.025*reps)],4),round(diff[int(.975*reps)-1],4)]


def _validate_selection(arms,calibration,steps):
    decisions={}
    for name,curve in calibration.items():
        final=next(r for r in curve if r['step']==steps)
        best=next(r for r in curve if r['step']==_select_checkpoint(curve,steps))
        decisions[name]={'fixed_final':{'step':steps,'margin':final['margin']},
                         'cal_selected':{'step':best['step'],'margin':best['margin']}}
    return decisions


def _evaluate_choices(states,decisions,baseline_state,ds,indices,*,device='cpu'):
    """EVAL accessed only AFTER all arms' training and CAL decisions are final."""
    baseline_model=_new_model(baseline_state,device).eval().requires_grad_(False)
    # Frozen reference uses CAL-selected margin (from a separately calibrated
    # step-0 snapshot), same for all arms and both evaluation policies.
    ref_margin=decisions[next(iter(decisions))]['frozen_margin']
    reference=_scores(_collect(baseline_model,ds,indices,ref_margin),ref_margin,details=True)
    out={}
    for name,by_step in states.items():
        out[name]={}
        for rule in ('fixed_final','cal_selected'):
            choice=decisions[name][rule]
            model=_new_model(by_step[choice['step']],device).eval().requires_grad_(False)
            rows=_collect(model,ds,indices,choice['margin'])
            summary=_scores(rows,choice['margin'],details=True)
            ci=_bootstrap(reference,summary)
            out[name][rule]={'step':choice['step'],'margin':choice['margin'],
                             'summary':{key:value for key,value in summary.items() if key!='by_pair'},
                             'delta_f1_pp':round(100*(summary['overall']['f1']-
                                                     reference['overall']['f1']),4),
                             'paired_ci_95_pp_vs_frozen':ci,
                             'beats_frozen_on_historical_cohort':(
                                summary['overall']['f1']>reference['overall']['f1'] and ci[0]>0)}
            del model
    return ({k:v for k,v in reference.items() if k!='by_pair'},out)


def run(*,training_dir,plan_dir,capture_dir,checkpoint_step=4900,
        low_pairs=96,high_pairs=384,calibration_pairs=24,evaluation_pairs=48,
        steps=192,batch_pairs=2,interval=48,device='cuda',cpu_threads=2,
        with_restored_optimizer=False):
    import torch
    if min(low_pairs,high_pairs,calibration_pairs,evaluation_pairs,steps,batch_pairs,interval,cpu_threads)<1:
        raise ValueError('COUNTS_MUST_BE_POSITIVE')
    if not 1<=batch_pairs<=8 or interval>steps or steps%interval:
        raise ValueError('MILESTONE_OR_BATCH_INVALID')
    if device not in ('cpu','cuda') or device=='cuda' and not torch.cuda.is_available():
        raise ValueError('TRAINING_DEVICE_UNAVAILABLE')
    torch.set_num_threads(cpu_threads)
    torch.manual_seed(SEED)
    if device=='cuda':torch.cuda.manual_seed_all(SEED)
    source,train,validation,cfg,receipt,opt_path,original_budget=_load_parent(
        training_dir,plan_dir,capture_dir,checkpoint_step,device,with_restored_optimizer)
    low_pool,high_pool=_nested_train_pools(train,low_pairs,high_pairs,original_budget)
    low_schedule=_schedule(low_pool,steps,batch_pairs)
    high_schedule=_schedule(high_pool,steps,batch_pairs)
    cal_ids,eval_ids=_split_validation(validation,calibration_pairs,evaluation_pairs)
    if set(cal_ids)&set(eval_ids):raise ValueError('CAL_EVAL_OVERLAP')
    milestones=tuple(range(interval,steps+1,interval))
    states={};training={};schedules={}
    for pool,schedule in ((low_pairs,low_schedule),(high_pairs,high_schedule)):
        for lr in RATES:
            name=f'fresh_{pool}_lr{lr:g}'
            torch.manual_seed(SEED)
            if device=='cuda':torch.cuda.manual_seed_all(SEED)
            result,losses=_run_arm(source,train,schedule,lr=lr,device=device,
                                   milestones=milestones)
            states[name]=result
            schedules[name]=_fingerprint(schedule)
            training[name]={'learning_rate':lr,'unique_pairs':pool,'optimizer':'fresh',
                            'steps':steps,'training_loss_samples':losses}
    if with_restored_optimizer:
        name=f'restored_{high_pairs}_lr{RATES[0]:g}'
        result,losses=_run_arm(source,train,high_schedule,lr=RATES[0],device=device,
                              milestones=milestones,optimizer_path=opt_path)
        states[name]=result;schedules[name]=_fingerprint(high_schedule)
        training[name]={'learning_rate':RATES[0],'unique_pairs':high_pairs,
                        'optimizer':'restored_from_step_4900','steps':steps,
                        'training_loss_samples':losses}
    # CAL selection and frozen margin use only CAL examples, not EVAL.
    frozen_cal=_calibrate_states({0:source},validation,cal_ids)
    reference_margin=frozen_cal[0]['margin']
    calibration={name:_calibrate_states(weights,validation,cal_ids)
                 for name,weights in states.items()}
    decisions=_validate_selection(states,calibration,steps)
    for name in decisions:decisions[name]['frozen_margin']=reference_margin
    # The only EVAL reads happen after ALL CAL and checkpoint choices.
    frozen,evaluation=_evaluate_choices(states,decisions,source,validation,eval_ids)
    lineage={'checkpoint_step':checkpoint_step,
             'checkpoint_sha256':receipt['weights_sha256'],
             'optimizer_sha256':receipt.get('optimizer_sha256') if with_restored_optimizer else None,
             'plan_sha256':cfg['plan_sha256'],
             'capture_config_sha256':cfg['capture_config_sha256'],
             'training_pool_low_digest':_fingerprint(low_pool),
             'training_pool_high_digest':_fingerprint(high_pool),
             'calibration_pairs_digest':_fingerprint(cal_ids),
             'evaluation_pairs_digest':_fingerprint(eval_ids),
             'schedule_digests':schedules}
    return {'schema':SCHEMA,'status':'EXPERIMENTAL_CONTINUATION_STABILITY_NOT_CERTIFIED',
            'lineage':lineage,
            'design':{'steps_per_arm':steps,'batch_pairs':batch_pairs,
                      'pair_presentations_per_arm':steps*batch_pairs,
                      'low_pool_unique_pairs':low_pairs,'high_pool_unique_pairs':high_pairs,
                      'low_pool_replays':steps*batch_pairs//low_pairs,
                      'high_pool_replays':steps*batch_pairs//high_pairs,
                      'learning_rates':list(RATES),'milestones':[0,*milestones],
                      'training_optimizer':'AdamW','training_objective':'0057_D_combined_original',
                      'device':device,'calibration_pairs':calibration_pairs,
                      'evaluation_pairs':evaluation_pairs,
                      'optimizer_crossover_included':with_restored_optimizer},
            'frozen_calibration':frozen_cal,
            'calibration_trajectories':calibration,
            'checkpoint_decisions':{name:{key:v for key,v in decision.items()
                                         if key!='frozen_margin'}
                                    for name,decision in decisions.items()},
            'training':training,'frozen_reference':frozen,'evaluation':evaluation,
            'sealed_test_used':False,'production_promotion':False,
            'gold_at_inference':False,
            'warnings':[
                'Historical G2-0062 validation was previously used to select checkpoint; not independent certification.',
                'Initial checkpoint and original paired loss are unchanged; each fresh arm restarts AdamW.',
                'Low/high diversity differ in unique pairs and replay frequency at identical step budgets.',
                'Optional restored-optimizer arm is valid only if recorded checkpoint optimizer hash verifies.',
                'Intermediate snapshot trajectories are CAL ONLY; EVAL sees predeclared final and CAL-selected snapshots.',
                'No model or optimizer checkpoint saved, no sealed tests/challenge, no Qwen recapture or production mutation.']}


def format_report(report):
    meta=report['design'];baseline=report['frozen_reference']['overall']
    lines=[f'G2-0069 OPTIMIZATION STABILITY / CONTINUED LEARNING — checkpoint {report["lineage"]["checkpoint_step"]}',
           'Source SHA-256: '+report['lineage']['checkpoint_sha256'],
           f'Original D_combined objective; {meta["steps_per_arm"]} identical optimizer steps per arm, '
           f'{meta["pair_presentations_per_arm"]} pair presentations',
           f'TRAIN unique pairs {meta["low_pool_unique_pairs"]} ({meta["low_pool_replays"]} replays) vs '
           f'{meta["high_pool_unique_pairs"]} ({meta["high_pool_replays"]} replay); '
           f'CAL {meta["calibration_pairs"]} | EVAL {meta["evaluation_pairs"]} pairs',
           'Frozen reference F1 '+f'{100*baseline["f1"]:.2f}%'+
           f'; exact {baseline["exact"]}/{baseline["graphs"]}',
           'CAL-only F1 trajectory (no EVAL read during training or checkpoint choice):',
           'Arm                              '+''.join(f' {k:>6}' for k in meta['milestones'])]
    for name,curve in report['calibration_trajectories'].items():
        lines.append(f'{name:<33}'+''.join(f' {100*x["cal_f1"]:6.2f}' for x in curve))
    lines.extend(['EVAL: fixed-final and CAL-selected checkpoint (paired 95% interval vs FROZEN)',
                  'Arm / decision                       Precision  Recall     F1     dF1    Miss Extra Exact   CI'])
    for name,picks in report['evaluation'].items():
        for rule,row in picks.items():
            v=row['summary']['overall']
            lines.append(f'{name+"/"+rule+" @"+str(row["step"]):<41}'
                 f'{100*v["precision"]:7.2f}% {100*v["recall"]:7.2f}% '
                 f'{100*v["f1"]:7.2f}% {row["delta_f1_pp"]:+7.2f}pp '
                 f'{v["missing"]:5} {v["extra"]:5} {v["exact"]}/{v["graphs"]} '
                 f'{row["paired_ci_95_pp_vs_frozen"]}')
    lines += ['EVAL was accessed after all CAL checkpoint/margin decisions; historical validation is NOT certification.',
              'No production promotion, sealed tests or original checkpoint mutation.']
    return '\n'.join(lines)


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--training-dir',type=Path,default=DEFAULT_TRAINING)
    ap.add_argument('--plan',type=Path,default=DEFAULT_PLAN)
    ap.add_argument('--capture',type=Path,default=DEFAULT_CAPTURE)
    ap.add_argument('--checkpoint-step',type=int,default=4900)
    ap.add_argument('--low-pairs',type=int,default=96)
    ap.add_argument('--high-pairs',type=int,default=384)
    ap.add_argument('--calibration-pairs',type=int,default=24)
    ap.add_argument('--evaluation-pairs',type=int,default=48)
    ap.add_argument('--steps',type=int,default=192)
    ap.add_argument('--batch-pairs',type=int,default=2)
    ap.add_argument('--interval',type=int,default=48)
    ap.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    ap.add_argument('--cpu-threads',type=int,default=2)
    ap.add_argument('--with-restored-optimizer',action='store_true')
    ap.add_argument('--output',type=Path)
    a=ap.parse_args(argv)
    if a.output and a.output.exists():ap.error('REFUSING_TO_OVERWRITE_OUTPUT')
    try:
        report=run(training_dir=a.training_dir.resolve(),plan_dir=a.plan.resolve(),
                   capture_dir=a.capture.resolve(),checkpoint_step=a.checkpoint_step,
                   low_pairs=a.low_pairs,high_pairs=a.high_pairs,
                   calibration_pairs=a.calibration_pairs,evaluation_pairs=a.evaluation_pairs,
                   steps=a.steps,batch_pairs=a.batch_pairs,interval=a.interval,
                   device=a.device,cpu_threads=a.cpu_threads,
                   with_restored_optimizer=a.with_restored_optimizer)
        print(format_report(report),flush=True)
        if a.output:
            if a.output.exists():raise ValueError('REFUSING_TO_OVERWRITE_OUTPUT')
            a.output.parent.mkdir(parents=True,exist_ok=True)
            with a.output.open('x',encoding='utf8') as f:
                json.dump(report,f,sort_keys=True,indent=2);f.write('\n')
            print('New research report: '+str(a.output),flush=True)
    except (ValueError,RuntimeError,OSError,AssertionError) as exc:
        ap.exit(2,f'G2_0069_BAKEOFF_ERROR: {exc}; original training artifacts untouched.\n')


if __name__=='__main__':main()
