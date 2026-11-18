"""G2-0068: frozen-checkpoint slot supervision and set-loss causal bake-off.

Starts four equal-budget optimizer continuations from the SAME hash-verified
G2-0062 step-4900 weights. The control uses the exact existing G2-0057
D_combined paired objective. Three ablations alter only (a) the unmatched-slot
null coefficient, (b) a candidate-complete-edge hard-negative ranking term,
or (c) both. Features/labels come only from existing, reachable TRAIN pairs.

Policy margins are tuned on a pair-disjoint CAL subset and are locked before
EVAL examples are fetched. EVAL remains historically used validation, NOT an
independent certification set. Inference never accesses gold. No Qwen, no
re-capture, no test/challenge, no production promotion, no checkpoint writes.

Run from main_computer/nanojev after G2-0067 is installed:
  & $py -u -m tools.nanojev_g2_set_loss_bakeoff_0068 --checkpoint-step 4900
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import hashlib
import json
import math
from pathlib import Path
import random

from tools import nanojev_g2_joint_graph_bakeoff_0067 as p67
from tools import nanojev_g2_learning_probe as receipts
from tools import nanojev_gamer_v1_g2_counterfactual_bakeoff_0057 as p57
from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55

SCHEMA = 'nanojev.gamer.graph.g2.set-loss-causal-bakeoff-0068.v1'
DEFAULT_TRAINING = p67.DEFAULT_TRAINING
DEFAULT_PLAN = p67.DEFAULT_PLAN
DEFAULT_CAPTURE = p67.DEFAULT_CAPTURE
SEED = 680068
MARGINS = (0., 0.5, 1., 1.5, 2., 2.5, 3.)
ARMS = ('control', 'null_supervision', 'hard_edge_separation', 'combined_loss')
NULL_CONTROL = .2
NULL_STRONG = .55
HARD_EDGE_COEFF = .30
HARD_EDGE_MARGIN = .35


def _features(sample):
    """Explicitly exclude labels, author IDs and truth cardinality in forward."""
    from tools import nanojev_gamer_v1_g2_scaling_0059 as g
    return {name: sample[name] for name in g.FEATURE_FIELDS}


def _matched_loss_from_forward(logits, sample, *, null_weight=NULL_CONTROL,
                               hard_weight=0.):
    """Exactly preserve G2-0057 matched loss, add isolated training-only terms.

    The existing objective is obtained by null_weight=.2/hard_weight=0.
    Hungarian matching is unchanged, detached, and gold-aware for TRAIN only.
    No different matching algorithm, pairing term, architecture, or optimizer.
    """
    import torch
    import torch.nn.functional as F
    if not 0 <= null_weight <= 3 or not 0 <= hard_weight <= 3:
        raise ValueError('INVALID_LOSS_WEIGHTS')
    base, matched = p57._matched_set_loss(logits, sample)
    loss = base
    if null_weight != NULL_CONTROL:
        unmatched = torch.ones(p55.SLOTS, dtype=torch.bool, device=logits[0].device)
        unmatched[list(matched.values())] = False
        if bool(unmatched.any()):
            no_edge = torch.zeros(int(unmatched.sum()), dtype=torch.long,
                                  device=logits[0].device)
            null_loss = F.cross_entropy(logits[0][unmatched], no_edge)
            loss = loss + (null_weight - NULL_CONTROL) * null_loss
    if hard_weight:
        loss = loss + hard_weight * _hard_edge_objective(logits, sample)
    return loss, matched


def _hard_edge_objective(logits, sample, *, margin=HARD_EDGE_MARGIN):
    """Differentiable separation of FULL admissible gold triples from negatives.

    Per-edge score = strongest model slot's summed typed-relation/source/
    destination log-probability. Penalize the strongest negative proposals
    against the weakest gold triples. No graph gold count used at inference.
    TRAIN-only gold is read here. Score aggregation doesn't assume one-to-one
    relations or use any invented structural constraints.
    """
    import torch
    import torch.nn.functional as F
    rel, src, dst = logits
    ids = sample['src'].long().to(rel.device)
    targets = sample['dst'].long().to(rel.device)
    kinds = sample['relation'].long().to(rel.device) + 1
    if not len(ids):
        raise ValueError('NO_CANDIDATE_EDGES')
    gold = sample['positive'].long().to(rel.device)
    if not len(gold) or len(gold) > p55.SLOTS or len(gold) != sample['truth_count']:
        raise ValueError('CENSORED_OR_INVALID_HARD_EDGE_GOLD')
    positive = torch.zeros(len(ids),dtype=torch.bool,device=rel.device)
    positive[gold] = True
    if not bool((~positive).any()):
        raise ValueError('NO_HARD_NEGATIVE_CANDIDATES')
    score = (F.log_softmax(rel,-1)[:,kinds]
             +F.log_softmax(src,-1)[:,ids]
             +F.log_softmax(dst,-1)[:,targets]).amax(dim=0)
    high_false = torch.topk(score[~positive], k=min(12, int((~positive).sum()))).values
    low_true = -torch.topk(-score[positive], k=min(12,len(gold))).values
    return F.softplus(high_false[:,None] - low_true[None,:] + margin).mean()


def _paired_objective(model, chosen_pairs, sample_map, arm):
    """One forward per graph. Pair consistency is IDENTICAL in all four arms."""
    import torch
    if arm not in ARMS:
        raise ValueError('UNKNOWN_LOSS_ARM')
    if arm == 'control':
        return p57.batch_objective(model,chosen_pairs,sample_map,'D_combined')
    null_weight = NULL_STRONG if arm in ('null_supervision','combined_loss') else NULL_CONTROL
    hard_weight = HARD_EDGE_COEFF if arm in ('hard_edge_separation','combined_loss') else 0.
    losses=[];consistency=[]
    for pair in chosen_pairs:
        a,b=(sample_map[sha] for sha in pair['sha256'])
        fa=model(_features(a));fb=model(_features(b))
        la,ma=_matched_loss_from_forward(fa,a,null_weight=null_weight,hard_weight=hard_weight)
        lb,mb=_matched_loss_from_forward(fb,b,null_weight=null_weight,hard_weight=hard_weight)
        losses.extend((la,lb))
        consistency.append(p57.paired_consistency(a,b,fa,fb,ma,mb))
    if not losses:
        raise ValueError('EMPTY_TRAIN_BATCH')
    return torch.stack(losses).mean()+p57.PAIR_WEIGHT*torch.stack(consistency).mean()


def _get_training_pair(ds,index):
    samples,receipt=ds.pair(index,with_labels=True)
    if any(len(s['positive'])!=s['truth_count'] or
           s.get('training_node_ids') is None or
           len(s['positive']) > p55.SLOTS for s in samples):
        raise ValueError('UNREACHABLE_OR_UNALIGNED_TRAIN_PAIR')
    # Requires paired canonical semantics, as in the exact original objective.
    if set(p57.canonical_edges(samples[0]))!=set(p57.canonical_edges(samples[1])):
        raise ValueError('PAIRED_CANONICAL_GOLD_DIFFERENCE')
    return samples,receipt


def _batch_schedule(ids,epochs,batch_pairs):
    """Precomputed, identical pair-complete optimizer schedule for all arms."""
    if epochs<1 or not 1<=batch_pairs<=8 or len(ids)<batch_pairs or len(ids)%batch_pairs:
        raise ValueError('BAD_MATCHED_BUDGET')
    rng=random.Random(SEED)
    result=[]
    for _ in range(epochs):
        order=list(ids);rng.shuffle(order)
        result.extend(tuple(order[i:i+batch_pairs]) for i in range(0,len(order),batch_pairs))
    return result


def _slot_diagnostics(model,train_ds,indices):
    """TRAIN-only matching/decoder diagnostic; slot IDs are not semantic IDs."""
    import torch
    import torch.nn.functional as F
    totals=Counter()
    model.eval()
    with torch.inference_mode():
        for idx in indices:
            (a,b),_=_get_training_pair(train_ds,idx)
            assignments=[]
            for sample in (a,b):
                r,s,d=model(_features(sample))
                gold=sample['positive'].long().to('cpu')
                sg=sample['src'][gold].to(r.device)
                dg=sample['dst'][gold].to(r.device)
                rg=sample['relation'][gold].to(r.device)+1
                cost=-(F.log_softmax(r,-1)[:,rg]
                      +F.log_softmax(s,-1)[:,sg]
                      +F.log_softmax(d,-1)[:,dg]).detach().cpu().numpy()
                q,gi=p55.rectangular_assignment(cost)
                mapping={int(gold[g]):int(slot) for slot,g in zip(q,gi)}
                local_best=list(cost.argmin(axis=0))
                totals['gold_edges']+=len(gold)
                totals['independent_match_collisions']+=len(local_best)-len(set(local_best))
                totals['hungarian_reassigned_edges']+=sum(int(qi!=int(local_best[gj])) for qi,gj in zip(q,gi))
                probs_s=F.log_softmax(s,-1)
                probs_d=F.log_softmax(d,-1)
                probs_r=F.log_softmax(r,-1)
                ids=sample['src'].to(r.device);targets=sample['dst'].to(r.device)
                rels=sample['relation'].to(r.device)+1
                assigned_good_slots=set(q)
                totals['slots_null_supervised']+=p55.SLOTS-len(assigned_good_slots)
                totals['unmatched_nonnull_argmax']+=sum(int(r[i].argmax()!=0)
                      for i in range(p55.SLOTS) if i not in assigned_good_slots)
                for edge,slot in mapping.items():
                    if int(r[slot].argmax())==0:totals['matched_gold_slot_predicted_null']+=1
                    if int(r[slot].argmax())==int(rels[edge]):totals['matched_relation_top1']+=1
                    if int(s[slot].argmax())==int(ids[edge]):totals['matched_source_top1']+=1
                    if int(d[slot].argmax())==int(targets[edge]):totals['matched_destination_top1']+=1
                    scores=probs_s[slot,ids]+probs_d[slot,targets]+probs_r[slot,rels]
                    best=int(scores.argmax())
                    if best==edge and int(r[slot].argmax())!=0 and float(r[slot,rels[edge]])>float(r[slot,0]):
                        totals['matched_slot_decodes_assigned_gold']+=1
                assignments.append(mapping)
                totals['graphs']+=1
            left=p57.canonical_edges(a);right=p57.canonical_edges(b)
            totals['pair_canonical_gold']+=len(left)
            totals['same_numeric_slot_across_pair']+=sum(
                int(assignments[0][left[k]]==assignments[1][right[k]]) for k in left)
            totals['pairs']+=1
    edges=totals['gold_edges']
    return {**dict(totals),
        'numeric_slot_agreement_fraction':totals['same_numeric_slot_across_pair']/totals['pair_canonical_gold'] if totals['pair_canonical_gold'] else 0.,
        'unmatched_nonnull_fraction':totals['unmatched_nonnull_argmax']/totals['slots_null_supervised'] if totals['slots_null_supervised'] else 0.,
        'hungarian_reassigned_fraction':totals['hungarian_reassigned_edges']/edges if edges else 0.,
        'assigned_gold_edge_decoded_fraction':totals['matched_slot_decodes_assigned_gold']/edges if edges else 0.,
        'warning':'Permutation-invariant slots have no required fixed identities; numeric agreement is descriptive, not a failure gate.'}


def _decode(logits,features,margin):
    """The existing typed edge decoder plus a calibration-selected null margin."""
    import torch
    import torch.nn.functional as F
    r,s,d=logits
    ids=features['src'].to(r.device)
    targets=features['dst'].to(r.device)
    labels=features['relation'].to(r.device)+1
    lr=F.log_softmax(r,-1);ls=F.log_softmax(s,-1);ld=F.log_softmax(d,-1)
    scored={}
    for q in range(p55.SLOTS):
        if int(r[q].argmax())==0:continue
        scores=ls[q,ids]+ld[q,targets]+lr[q,labels]
        edge=int(scores.argmax())
        if float(r[q,labels[edge]]-r[q,0])<=margin:continue
        conf=float(scores[edge])
        if edge not in scored or conf>scored[edge]:scored[edge]=conf
    return set(sorted(scored,key=lambda i:-scored[i])[:p55.MAX_EDGE])


def _case_predictions(model, ds, indices, margin=None):
    """Never passes gold to model/decoder. Read gold AFTER forward and decode."""
    import torch
    result=[]
    model.eval()
    with torch.inference_mode():
        for index in indices:
            samples,_=ds.pair(index,with_labels=True)
            for sample in samples:
                features=_features(sample)
                logits=model(features)
                if margin is None:
                    # Small bounded cache of chosen CAL logits: no new model
                    # forwards during margin selection, no evaluation loaded.
                    values={str(m):_decode(logits,features,m) for m in MARGINS}
                    pred=None
                else:
                    values=None
                    pred=_decode(logits,features,margin)
                gold=frozenset(int(i) for i in sample['positive'].tolist())
                result.append({'pair_index':index,'family':sample['family'],'mode':sample['mode'],
                               'id':sample['prompt_sha256'],'gold':gold,
                               'truth_count':sample['truth_count'],
                               'predicted_by_margin':values,'prediction':pred})
    return result


def _record(count,pred,gold,truth):
    tp=len(pred&gold)
    count.update({'tp':tp,'pred':len(pred),'gold':truth,'missing':truth-tp,
                  'extra':len(pred-gold),'graphs':1,
                  'exact':int(len(gold)==truth and pred==gold),
                  'candidate_reachable':int(len(gold)==truth)})


def _metrics(c):
    tp=c['tp'];g=c['gold'];p=c['pred'];n=c['graphs']
    return {'graphs':n,'precision':tp/p if p else 0.,'recall':tp/g if g else 0.,
            'f1':2*tp/(p+g) if p+g else 0.,'missing':c['missing'],
            'extra':c['extra'],'edge_errors':c['missing']+c['extra'],
            'exact':c['exact'],'candidate_reachable':c['candidate_reachable'],
            'gold_edges':g,'predicted_edges':p,'true_positive':tp}


def _summarize(cases,margin=None):
    total=Counter();families=defaultdict(Counter);modes=defaultdict(Counter)
    by_pair=defaultdict(Counter)
    for row in cases:
        pred=row['prediction'] if margin is None else row['predicted_by_margin'][str(margin)]
        _record(total,pred,row['gold'],row['truth_count'])
        _record(families[row['family']],pred,row['gold'],row['truth_count'])
        _record(modes[row['mode']],pred,row['gold'],row['truth_count'])
        _record(by_pair[row['pair_index']],pred,row['gold'],row['truth_count'])
    return {'overall':_metrics(total),
            'by_family':{k:_metrics(x) for k,x in sorted(families.items())},
            'by_mode':{k:_metrics(x) for k,x in sorted(modes.items())},
            'by_pair':{k:_metrics(x) for k,x in by_pair.items()}}


def _calibrate(cases):
    scored=[(m,_summarize(cases,m)['overall']) for m in MARGINS]
    # Highest CAL F1; on ties use fewer errors then standard 1.5 margin.
    return max(scored,key=lambda x:(x[1]['f1'],-x[1]['edge_errors'],-abs(x[0]-1.5),-x[0]))


def _paired_ci(control,changed,reps=400):
    """Graph-pair-block bootstrap of pooled F1 difference, exploratory only."""
    a=control['by_pair'];b=changed['by_pair'];ids=sorted(a)
    if set(ids)!=set(b):raise ValueError('EVALUATION_PAIR_MISMATCH')
    rng=random.Random(SEED)
    deltas=[]
    for _ in range(reps):
        drawn=[ids[rng.randrange(len(ids))] for _ in ids]
        def f1(rows):
            t=sum(r['true_positive'] for r in rows)
            d=sum(r['gold_edges']+r['predicted_edges'] for r in rows)
            return 2*t/d if d else 0.
        delta=f1([b[i] for i in drawn])-f1([a[i] for i in drawn])
        deltas.append(100*delta)
    deltas.sort()
    return [round(deltas[int(.025*reps)],3),round(deltas[int(.975*reps)-1],3)]


def _new_model(weights,device):
    model=p55.model_for('set_prediction')
    model.load_state_dict(copy.deepcopy(weights),strict=True)
    return model.to(device).requires_grad_(True)


def _training_arm(model,ds,schedule,arm,*,lr,device):
    import torch
    if arm not in ARMS:raise ValueError('INVALID_TRAINING_ARM')
    opt=torch.optim.AdamW(model.parameters(),lr=lr)
    losses=[]
    for step,batch in enumerate(schedule,1):
        pairs=[];samples={}
        for idx in batch:
            (a,b),receipt=_get_training_pair(ds,idx)
            pairs.append(receipt)
            samples[a['prompt_sha256']]=a
            samples[b['prompt_sha256']]=b
        model.train();opt.zero_grad(set_to_none=True)
        loss=_paired_objective(model,pairs,samples,arm)
        if not bool(torch.isfinite(loss)):
            raise ValueError('NONFINITE_TRAINING_LOSS')
        loss.backward()
        grad=torch.nn.utils.clip_grad_norm_(model.parameters(),5.)
        if not bool(torch.isfinite(grad)):
            raise ValueError('NONFINITE_TRAINING_GRADIENT')
        opt.step()
        if step==1 or step==len(schedule) or step%max(1,len(schedule)//4)==0:
            value=float(loss.detach().cpu())
            losses.append({'step':step,'loss':round(value,6)})
            print('G2_0068_TRAIN '+json.dumps({'arm':arm,'step':step,
                        'total_steps':len(schedule),'loss':round(value,6)},sort_keys=True),flush=True)
    return {'arm':arm,'optimizer_steps':len(schedule),'learning_rate':lr,
            'training_loss_samples':losses}


def _load_baseline(training_dir,plan_dir,capture_dir,step,cpu_threads):
    """Exact hash-pinned step-4900 lineage and sealed-lane guard of G2-0067."""
    from tools import nanojev_gamer_v1_g2_scaling_0059 as g
    model,train,val,_,cfg,receipt=p67._load_model_and_data(
        training_dir,plan_dir,capture_dir,step,cpu_threads)
    if cfg.get('budget',{}).get('requested_training_graphs') is None:
        raise ValueError('ORIGINAL_TRAIN_BUDGET_MISSING')
    return model,train,val,cfg,receipt


def run(*,training_dir,plan_dir,capture_dir,checkpoint_step=4900,
        training_pairs=96,calibration_pairs=24,evaluation_pairs=48,
        epochs=2,batch_pairs=2,lr=p55.LR,device='cpu',cpu_threads=2,
        audit_pairs=8):
    import torch
    if min(training_pairs,calibration_pairs,evaluation_pairs,epochs,batch_pairs,cpu_threads,audit_pairs)<1:
        raise ValueError('COUNTS_MUST_BE_POSITIVE')
    if not (0<lr<=.005) or device not in ('cpu','cuda') or (
       device=='cuda' and not torch.cuda.is_available()):
        raise ValueError('TRAINING_DEVICE_OR_RATE_INVALID')
    if device=='cpu':torch.set_num_threads(cpu_threads)
    torch.manual_seed(SEED)
    if device=='cuda':torch.cuda.manual_seed_all(SEED)
    initial,train_ds,val_ds,cfg,receipt=_load_baseline(
        training_dir,plan_dir,capture_dir,checkpoint_step,cpu_threads)
    original_budget=int(cfg['budget']['requested_training_graphs'])//2
    train_ids=p67._train_indices(train_ds,training_pairs,original_budget)
    cal_ids,eval_ids=p67._partition(val_ds,calibration_pairs,evaluation_pairs)
    if set(cal_ids)&set(eval_ids):raise ValueError('CAL_EVAL_NOT_DISJOINT')
    schedule=_batch_schedule(train_ids,epochs,batch_pairs)
    frozen_weights={k:v.detach().cpu().clone() for k,v in initial.state_dict().items()}
    audit_ids=train_ids[:min(len(train_ids),audit_pairs)]
    before=_slot_diagnostics(initial,train_ds,audit_ids)
    lineage={'checkpoint_step':checkpoint_step,'checkpoint_sha256':receipt['weights_sha256'],
             'capture_config_sha256':cfg['capture_config_sha256'],
             'plan_sha256':cfg['plan_sha256'],
             'train_pair_digest':hashlib.sha256(str(train_ids).encode()).hexdigest(),
             'cal_pair_digest':hashlib.sha256(str(cal_ids).encode()).hexdigest(),
             'eval_pair_digest':hashlib.sha256(str(eval_ids).encode()).hexdigest(),
             'training_schedule_digest':hashlib.sha256(str(schedule).encode()).hexdigest()}
    models={'frozen_reference':initial}
    training={}
    # Train all arms fully, from IDENTICAL weights & identical ordered batches.
    for arm in ARMS:
        torch.manual_seed(SEED)
        if device=='cuda':torch.cuda.manual_seed_all(SEED)
        model=_new_model(frozen_weights,device)
        info=_training_arm(model,train_ds,schedule,arm,lr=lr,device=device)
        model=model.cpu().eval().requires_grad_(False)
        models[arm]=model
        info['train_slot_diagnostics']=_slot_diagnostics(model,train_ds,audit_ids)
        training[arm]=info
    # Select decoder margins on CAL only; no evaluation gold or features read.
    calibration={};selection={}
    for name,model in models.items():
        cases=_case_predictions(model,val_ds,cal_ids,margin=None)
        margin,m=_calibrate(cases)
        selection[name]=margin
        calibration[name]={'margin':margin,'f1':m['f1'],
                           'edge_errors':m['edge_errors'],'exact':m['exact']}
    # Only now consume EVAL. Every model uses its own previously locked margin.
    evaluation_raw={}
    for name,model in models.items():
        rows=_case_predictions(model,val_ds,eval_ids,margin=selection[name])
        evaluation_raw[name]=_summarize(rows)
    baseline=evaluation_raw['control']
    intervals={name:_paired_ci(baseline,result) for name,result in evaluation_raw.items()
               if name!='control'}
    verdict={name:{'delta_f1_pp':round(100*(m['overall']['f1']-
                        baseline['overall']['f1']),4),'paired_ci_95_pp':intervals[name],
                   'supported_on_historical_cohort':m['overall']['f1']>baseline['overall']['f1']
                   and intervals[name][0]>0}
             for name,m in evaluation_raw.items() if name!='control'}
    evaluation={name:{k:v for k,v in result.items() if k!='by_pair'}
                for name,result in evaluation_raw.items()}
    return {'schema':SCHEMA,'status':'EXPERIMENTAL_EQUAL_BUDGET_SET_LOSS_BAKEOFF_NOT_CERTIFIED',
            'lineage':lineage,
            'split':{'train_pairs':len(train_ids),'cal_pairs':len(cal_ids),
                    'eval_pairs':len(eval_ids),'epochs':epochs,'batch_pairs':batch_pairs,
                    'optimizer_steps_per_arm':len(schedule),
                    'optimizer':'AdamW','learning_rate':lr,'device':device},
            'loss_arms':{'control':{'null_weight':.2,'hard_edge_weight':0.,'pair_weight':p57.PAIR_WEIGHT},
                         'null_supervision':{'null_weight':NULL_STRONG,'hard_edge_weight':0.,'pair_weight':p57.PAIR_WEIGHT},
                         'hard_edge_separation':{'null_weight':.2,'hard_edge_weight':HARD_EDGE_COEFF,'pair_weight':p57.PAIR_WEIGHT},
                         'combined_loss':{'null_weight':NULL_STRONG,'hard_edge_weight':HARD_EDGE_COEFF,'pair_weight':p57.PAIR_WEIGHT}},
            'initial_training_slot_diagnostics':before,
            'training':training,'calibration':calibration,'evaluation':evaluation,
            'hypothesis_results':verdict,
            'target_95_edge_f1_reached':any(m['overall']['f1']>=.95 for m in evaluation.values()),
            'sealed_test_used':False,'production_promotion':False,'gold_at_inference':False,
            'warnings':[
                'Existing step-4900 checkpoint was selected using this historical validation cohort: NOT independent certification.',
                'The original D_combined paired set loss is retained exactly for control; no optimizer continuation state is restored for any arm.',
                'All arms restart AdamW with identical state from frozen weights, so comparison is of matched-batch continuation from same checkpoint.',
                'Numeric slot IDs are permutation-variant; slot agreement across transformations is diagnostic, not a correctness requirement.',
                'No gold or author IDs are passed to inference; CAL chooses margins, and EVAL is read after calibration.',
                'No test/challenge, no upstream Qwen capture, no model checkpoints persisted or production promotion.']}


def format_report(report):
    x=report['split']
    lines=[f'G2-0068 SLOT SUPERVISION / SET-LOSS CAUSAL BAKE-OFF — checkpoint {report["lineage"]["checkpoint_step"]}',
           'Frozen source SHA-256 '+report['lineage']['checkpoint_sha256'],
           f'TRAIN {x["train_pairs"]} pairs | CAL {x["cal_pairs"]} pairs | '
           f'EVAL {x["eval_pairs"]} pairs | {x["optimizer_steps_per_arm"]} updates/arm',
           'Controlled arms: identical training order, initial weights, AdamW, learning rate, and paired consistency.',
           'TRAIN-ONLY MATCHING DIAGNOSTIC (before continuation):']
    d=report['initial_training_slot_diagnostics']
    lines.append(f'  Hungarian reassigned {d["hungarian_reassigned_fraction"]*100:.2f}% of matched edges '
                 f'(independent collisions {d["independent_match_collisions"]}); '
                 f'numeric slot pair agreement {d["numeric_slot_agreement_fraction"]*100:.2f}%')
    lines.append(f'  Gold slot decodes assigned edge {d["assigned_gold_edge_decoded_fraction"]*100:.2f}%; '
                 f'unmatched non-null {d["unmatched_nonnull_fraction"]*100:.2f}%')
    lines.append('NON-ORACLE EVAL (margin selected independently on CAL):')
    lines.append('Arm                       Precision  Recall    F1       dF1 vs control   Miss Extra Exact')
    control=report['evaluation']['control']['overall']['f1']
    for name,r in report['evaluation'].items():
        m=r['overall']
        lines.append(f'{name:<26}{m["precision"]*100:7.2f}% {m["recall"]*100:7.2f}% '
                     f'{m["f1"]*100:7.2f}% {(m["f1"]-control)*100:+8.2f}pp '
                     f'{m["missing"]:6} {m["extra"]:5} {m["exact"]:3}/{m["graphs"]}')
    for name,m in report['hypothesis_results'].items():
        lines.append(f'  {name}: paired 95% ΔF1 {m["paired_ci_95_pp"]} pp; '
                     f'supported={m["supported_on_historical_cohort"]}')
    lines += [f'CAL-selected margins: { {name:d["margin"] for name,d in report["calibration"].items()} }',
              'Research-only historical cohort. Baseline continuation is the correct comparator, not the unchanged checkpoint.',
              'No sealed data, test/challenge, checkpoint mutations or production promotion.']
    return '\n'.join(lines)


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--training-dir',type=Path,default=DEFAULT_TRAINING)
    ap.add_argument('--plan',type=Path,default=DEFAULT_PLAN)
    ap.add_argument('--capture',type=Path,default=DEFAULT_CAPTURE)
    ap.add_argument('--checkpoint-step',type=int,default=4900)
    ap.add_argument('--training-pairs',type=int,default=96)
    ap.add_argument('--calibration-pairs',type=int,default=24)
    ap.add_argument('--evaluation-pairs',type=int,default=48)
    ap.add_argument('--epochs',type=int,default=2)
    ap.add_argument('--batch-pairs',type=int,default=2)
    ap.add_argument('--learning-rate',type=float,default=p55.LR)
    ap.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    ap.add_argument('--cpu-threads',type=int,default=2)
    ap.add_argument('--audit-pairs',type=int,default=8)
    ap.add_argument('--output',type=Path)
    a=ap.parse_args(argv)
    if a.output and a.output.exists():
        ap.error('OUTPUT_ALREADY_EXISTS: refusing to overwrite')
    try:
        result=run(training_dir=a.training_dir.resolve(),plan_dir=a.plan.resolve(),
                   capture_dir=a.capture.resolve(),checkpoint_step=a.checkpoint_step,
                   training_pairs=a.training_pairs,calibration_pairs=a.calibration_pairs,
                   evaluation_pairs=a.evaluation_pairs,epochs=a.epochs,
                   batch_pairs=a.batch_pairs,lr=a.learning_rate,device=a.device,
                   cpu_threads=a.cpu_threads,audit_pairs=a.audit_pairs)
        print(format_report(result),flush=True)
        if a.output:
            if a.output.exists():raise ValueError('OUTPUT_ALREADY_EXISTS')
            a.output.parent.mkdir(parents=True,exist_ok=True)
            with a.output.open('x',encoding='utf-8') as f:
                json.dump(result,f,indent=2,sort_keys=True);f.write('\n')
            print('New research report: '+str(a.output),flush=True)
    except (ValueError,RuntimeError,OSError,AssertionError) as exc:
        ap.exit(2,f'G2_0068_BAKEOFF_ERROR: {exc}; existing checkpoint and sources untouched.\n')


if __name__ == '__main__':
    main()
