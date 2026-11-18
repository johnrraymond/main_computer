"""G2-0065 frozen-logit *learned* endpoint vs admission bake-off.

Uses existing G2-0062 checkpoint and previously captured TRAIN and VALIDATION
features. Fits only small, CPU-only post-head research calibrators on TRAIN.
Validation pairs are split into disjoint policy-calibration and final evaluation.
No Qwen, CUDA, checkpoint/production mutations, sealed test or challenge.

Results from the historically selected validation cohort are exploratory and
NOT proof of out-of-sample certification or a 95% accuracy claim.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import random

from tools import nanojev_g2_endpoint_bakeoff_0064 as previous
from tools import nanojev_g2_learning_probe as receipts

SCHEMA = 'nanojev.gamer.graph.g2.learned-endpoint-admission-bakeoff-0065.v1'
DEFAULT_TRAINING = previous.DEFAULT_TRAINING
DEFAULT_PLAN = previous.DEFAULT_PLAN
DEFAULT_CAPTURE = previous.DEFAULT_CAPTURE
SEED = 650065
RANK_EPOCHS = 18
ADMISSION_EPOCHS = 120
NEGATIVES_PER_SLOT = 6
ALPHAS = (0.0, 0.25, 0.5, 1.0, 1.5, 2.0)
THRESHOLDS = (0.25, 0.35, 0.45, 0.5, 0.55, 0.65, 0.75, 0.85)
MODES = ('source', 'destination', 'joint')


def _features_for_edges(sample, mode):
    """Label-blind, bounded frozen contextual features for per-slot candidate ranking.

    Source and destination arms use *only their respective* node evidence.
    Joint additionally sees pair interaction and geometry; the relation logits
    and the relation/null classifier remain frozen throughout every ranker arm.
    """
    import torch
    a = sample['src'].long()
    b = sample['dst'].long()
    node = sample['node'].float()
    node = torch.tanh(node)  # Bound extreme captured features without using labels.
    margin = sample['margin'].float().reshape(-1)
    s = node[a]
    d = node[b]
    ms = torch.tanh(margin[a, None] / 4)
    md = torch.tanh(margin[b, None] / 4)
    if mode == 'source':
        return torch.cat((s, ms), dim=1)
    if mode == 'destination':
        return torch.cat((d, md), dim=1)
    if mode == 'joint':
        geo = torch.tanh(sample['geometry'].float())
        return torch.cat((s, d, s*d, ms, md, geo), dim=1)
    raise ValueError('Unknown endpoint ranker mode')


def _slot_scores(logits, sample):
    import torch.nn.functional as F
    r, s, d = logits
    ids = sample['src'].long()
    targets = sample['dst'].long()
    labels = sample['relation'].long()+1
    lr = F.log_softmax(r, dim=-1)
    ls = F.log_softmax(s, dim=-1)
    ld = F.log_softmax(d, dim=-1)
    return ls[:, ids] + ld[:, targets] + lr[:, labels]


def _rank_inputs(logits, sample, mode):
    """Scores and residual feature tensor for source/destination/joint readouts."""
    import torch
    _, s, d = logits
    ids = sample['src'].long()
    targets = sample['dst'].long()
    fp = _features_for_edges(sample, mode)
    ls = torch.log_softmax(s, dim=-1)[:, ids]
    ld = torch.log_softmax(d, dim=-1)[:, targets]
    # Only tune endpoint contributions, not relation/null classification.
    if mode == 'source':
        sp = ls.unsqueeze(-1)
    elif mode == 'destination':
        sp = ld.unsqueeze(-1)
    else:
        sp = torch.stack((ls, ld), dim=-1)
    return fp, sp


def _rank_residual(logits, sample, mode, weights):
    import torch
    base = _slot_scores(logits, sample)
    if weights is None:
        return base
    fixed, slot = _rank_inputs(logits, sample, mode)
    n = slot.shape[-1]
    if len(weights) != fixed.shape[-1] + n:
        raise ValueError('Endpoint-head shape drift')
    correction = (fixed @ weights[n:])[None, :] + (slot @ weights[:n])
    return base + correction


def _admission_vector(logits, sample, slot, edge, score):
    """Frozen prediction confidence only. No gold count or target features."""
    import torch
    r,s,d = logits
    src = int(sample['src'][edge]); dst = int(sample['dst'][edge]); label = int(sample['relation'][edge])+1
    sr = float(torch.log_softmax(r[slot],-1)[label]); ss = float(torch.log_softmax(s[slot],-1)[src]); sd = float(torch.log_softmax(d[slot],-1)[dst])
    margin = float(r[slot,label]-r[slot,0])
    # bounded, interpretable and stable between train/cal/evaluation
    return torch.tensor([1.0, math.tanh(margin/3), math.tanh(score/9),
                         math.tanh(ss/6), math.tanh(sd/6), math.tanh(sr/5),
                         math.tanh(float(len(sample['src']))/2000)],dtype=torch.float32)


def _propose(logits, sample, *, mode=None, weights=None, alpha=1.0, margin=0.0):
    """One top-ranked admissible edge per non-null slot, with original dedup.

    Uses label-free feature fields only. At alpha=0, margin=0 reproduces the
    frozen G2-0055 decoder, including its MAX_EDGE cap and tie behavior.
    """
    import torch
    base = _slot_scores(logits, sample)
    scores = base if mode is None or weights is None or alpha == 0 else (
        base + alpha * (_rank_residual(logits, sample, mode, weights)-base))
    r = logits[0]
    selected={}
    for q in range(r.shape[0]):
        if int(r[q].argmax()) == 0:
            continue
        i = int(scores[q].argmax())
        label = int(sample['relation'][i])+1
        if float(r[q,label] - r[q,0]) <= margin:
            continue
        score = float(scores[q,i])
        if i not in selected or score>selected[i][1]:
            selected[i] = (q,score)
    max_edge = int(sample.get('_max_edge',96))
    ordered=sorted(selected, key=lambda i: -selected[i][1])[:max_edge]
    return [(i,selected[i][0],selected[i][1]) for i in ordered]


def _decode(case, *, mode=None, weights=None, alpha=1.0, margin=0., admission=None, threshold=None):
    proposals=_propose(case['logits'],case['features'],mode=mode,weights=weights,
                       alpha=alpha,margin=margin)
    if admission is None:
        return {idx for idx,_,_ in proposals}
    import torch
    chosen=set()
    for idx,slot,score in proposals:
        x=_admission_vector(case['logits'],case['features'],slot,idx,score)
        if float(torch.sigmoid(x @ admission))>=threshold:
            chosen.add(idx)
    return chosen


def _rank_training_examples(cases,mode):
    """Gold-aware training matching is confined to TRAIN, never inference.

    Includes hard competitors that share the correct source or destination
    plus high-ranking frozen-model negatives, so labels supervise *which*
    endpoint wins, rather than just how many edges to admit.
    """
    import torch
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    features=[];deltas=[]
    for case in cases:
        c=case['features']; gold=case['gold']; logits=case['logits']
        if len(gold)!=case['truth_count']:
            raise ValueError('Unreachable TRAIN labels forbidden for endpoint loss')
        matches=previous._oracle_matching(logits,gold,c,p55.rectangular_assignment)
        base = _slot_scores(logits,c)
        fixed,slotpart=_rank_inputs(logits,c,mode)
        for q,g in matches:
            # Bounded top-K plus the strongest same-endpoint competitors.
            # Never perform a full Python sort over every candidate edge.
            row=base[q]
            strongest=torch.topk(row,k=min(len(row),NEGATIVES_PER_SLOT+8)).indices.tolist()
            def near(mask):
                values=row.masked_fill(~mask,float('-inf'))
                indices=torch.topk(values,k=min(3,len(row))).indices.tolist()
                return [int(i) for i in indices if bool(mask[i]) and i!=g][:2]
            neg=[]
            for i in (near(c['src']==c['src'][g]) +
                      near(c['dst']==c['dst'][g]) + strongest):
                if i!=g and i not in neg:neg.append(int(i))
                if len(neg)>=NEGATIVES_PER_SLOT:break
            for i in neg[:NEGATIVES_PER_SLOT]:
                diff0 = (base[q,g]-base[q,i]).detach()
                diff = torch.cat((slotpart[q,g]-slotpart[q,i],fixed[g]-fixed[i])).detach()
                features.append(diff);deltas.append(diff0)
    if not features:raise ValueError('No endpoint ranking training comparisons')
    return torch.stack(features), torch.stack(deltas)


def _fit_ranker(cases,mode,epochs=RANK_EPOCHS):
    import torch
    import torch.nn.functional as F
    x,offset = _rank_training_examples(cases,mode)
    w=torch.zeros(x.shape[-1],requires_grad=True)
    optimizer=torch.optim.AdamW([w],lr=0.035,weight_decay=0)
    for _ in range(epochs):
        optimizer.zero_grad(set_to_none=True)
        loss=F.softplus(-(offset+x@w)).mean() + 0.005*(w*w).sum()
        loss.backward();torch.nn.utils.clip_grad_norm_([w],1.0);optimizer.step()
        if not bool(torch.isfinite(loss)):raise ValueError('NONFINITE_ENDPOINT_TRAINING')
    return w.detach(),{'comparisons':len(offset),'epochs':epochs,'final_loss':float(loss.detach())}


def _admission_examples(cases, *, mode=None, weights=None, alpha=1.0):
    import torch
    xx=[];yy=[]
    for c in cases:
        for edge,q,score in _propose(c['logits'],c['features'],mode=mode,
                                      weights=weights,alpha=alpha,margin=-10**9):
            xx.append(_admission_vector(c['logits'],c['features'],q,edge,score))
            yy.append(float(edge in c['gold']))
    if not xx or len(set(yy))!=2:raise ValueError('Need positive and negative TRAIN edge admissions')
    return torch.stack(xx),torch.tensor(yy,dtype=torch.float32)


def _fit_admission(cases, *, mode=None, weights=None, alpha=1.0):
    import torch
    import torch.nn.functional as F
    x,y=_admission_examples(cases,mode=mode,weights=weights,alpha=alpha)
    w=torch.zeros(x.shape[-1],requires_grad=True)
    opt=torch.optim.AdamW([w],lr=0.06,weight_decay=0)
    for _ in range(ADMISSION_EPOCHS):
        opt.zero_grad(set_to_none=True)
        loss=F.binary_cross_entropy_with_logits(x@w,y) + .003*(w[1:]**2).sum()
        loss.backward();opt.step()
        if not bool(torch.isfinite(loss)):raise ValueError('NONFINITE_ADMISSION_TRAINING')
    return w.detach(),{'examples':len(y),'positive_fraction':float(y.mean()),
                       'epochs':ADMISSION_EPOCHS,'final_loss':float(loss.detach())}


def _score(cases, policy):
    scores=previous._stats();families=defaultdict(previous._stats)
    modes=defaultdict(previous._stats)
    per_pair=defaultdict(lambda:previous._stats())
    for c in cases:
        pred=_decode(c,**policy)
        previous._observe(scores,pred,c['gold'],c['truth_count'])
        previous._observe(families[c['family']],pred,c['gold'],c['truth_count'])
        previous._observe(modes[c['mode']],pred,c['gold'],c['truth_count'])
        previous._observe(per_pair[c['pair_index']],pred,c['gold'],c['truth_count'])
    return {'overall':previous._summarize(scores),
            'by_family':{k:previous._summarize(v) for k,v in sorted(families.items())},
            'by_mode':{k:previous._summarize(v) for k,v in sorted(modes.items())},
            'per_pair':{k:previous._summarize(v) for k,v in per_pair.items()}}


def _select_cal(cases, policies):
    """Calibrate only on disjoint CAL labels; no evaluation access."""
    rated=[(i,name,p, _score(cases,p)['overall']) for i,(name,p) in enumerate(policies)]
    if not rated:raise ValueError('Missing calibration candidates')
    _,name,policy,score=max(rated,key=lambda item:(item[3]['f1'],
                                                   -item[3]['edge_errors'],-item[0]))
    return name,policy,score


def _paired_bootstrap(cases, base_policy, test_policy, repeats=400, seed=650065):
    """Pair-block bootstrap of held-out F1 difference, descriptive not certification."""
    from collections import Counter
    group={}
    for c in cases:
        pair=group.setdefault(c['pair_index'],[[],[]])
        for index, policy in enumerate((base_policy,test_policy)):
            pred=_decode(c,**policy)
            pair[index].append((len(pred&c['gold']),len(pred),c['truth_count']))
    items=list(group.values()); rng=random.Random(seed)
    def f1(rows):
        a=sum(t for t,p,g in rows); b=sum(p for t,p,g in rows);g=sum(g for t,p,g in rows)
        return 2*a/(b+g) if b+g else 0.
    diffs=[]
    for _ in range(repeats):
        bag=[items[rng.randrange(len(items))] for _ in items]
        diffs.append(100*(f1([r for pair in bag for r in pair[1]])-
                          f1([r for pair in bag for r in pair[0]])))
    diffs.sort()
    return {'repeats':repeats,'delta_f1_pp_ci_95':
            [round(diffs[int(.025*repeats)],4),round(diffs[int(.975*repeats)-1],4)],
            'method':'paired bootstrap resampling counterfactual pairs; exploratory'}


def _balanced_train(ds,n,*,max_index=None):
    rng=random.Random(SEED)
    cells=defaultdict(list)
    for i in ds.indices:
        if max_index is not None and i>=max_index:continue
        a=ds.pairs[i][2];cells[(a['family'],a['mode'])].append(i)
    for x in cells.values():rng.shuffle(x)
    order=[]
    while len(order)<n and any(cells.values()):
        for key in sorted(cells):
            if cells[key] and len(order)<n:order.append(cells[key].pop())
    if len(order)!=n:raise ValueError('Insufficient reachable train pairs')
    return order


def _load_corpus_and_model(training_dir,plan_dir,capture_dir,step,cpu_threads):
    import torch
    from safetensors.torch import load_file
    from tools import nanojev_gamer_v1_g2_scaling_0059 as g
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    torch.set_num_threads(cpu_threads)
    history=receipts.snapshot(training_dir)
    choice=next((c for c in history.get('history',[]) if c['step']==step),None)
    if not choice:raise ValueError('Not a committed checkpoint')
    cfg=receipts._load_json(training_dir/'training.json')
    if cfg.get('production_promotion') is not False or cfg.get('sealed_test_used') is not False:
        raise ValueError('Not a research-only checkpoint')
    if cfg.get('capture_config_sha256')!=g.file_sha(capture_dir/'capture.json'):
        raise ValueError('Capture lineage mismatch')
    if cfg.get('plan_sha256')!=g.file_sha(plan_dir/'plan.json'):
        raise ValueError('Plan lineage mismatch')
    weights=training_dir/f'checkpoint-{step:09d}.safetensors'
    if not weights.exists() or g.file_sha(weights)!=choice.get('weights_sha256'):
        raise ValueError('Checkpoint hash mismatch')
    model=p55.model_for('set_prediction').to('cpu')
    model.load_state_dict(load_file(str(weights),device='cpu'),strict=True)
    model.eval().requires_grad_(False)
    td=g.FeatureCorpus(plan_dir,capture_dir,'train',require_complete=False)
    vd=g.FeatureCorpus(plan_dir,capture_dir,'validation',require_complete=False)
    if td.config.get('candidate_policy')!='actor_cues' or vd.config.get('candidate_policy')!='actor_cues':
        raise ValueError('Candidate policy mismatch')
    # Do not open or enumerate sealed feature labels, ever.
    return model,p55,td,vd,choice,cfg


def run(*,training_dir,plan_dir,capture_dir,checkpoint_step=4900,
        train_pairs=96,calibration_pairs=24,evaluation_pairs=48,cpu_threads=2):
    import torch
    if min(train_pairs,calibration_pairs,evaluation_pairs,cpu_threads)<1:
        raise ValueError('Positive cohort and CPU thread sizes required')
    torch.manual_seed(SEED)
    model,p55,td,vd,choice,cfg=_load_corpus_and_model(
        training_dir,plan_dir,capture_dir,checkpoint_step,cpu_threads)
    # Use only pairs admitted to G2-0062's original training budget, not
    # incidental over-capture at its final batch boundary.
    original_budget=cfg.get('budget',{})
    budget_max=int(original_budget.get('requested_training_graphs',5000))//2
    ti=_balanced_train(td,train_pairs,max_index=budget_max)
    ci,ei=previous._balanced_pair_partition(vd,calibration_pairs,evaluation_pairs,seed=640064)
    assert not set(ci)&set(ei)
    train=previous._capture_predictions(model,td,ti,p55)
    calibration=previous._capture_predictions(model,vd,ci,p55)
    # No evaluation examples loaded before calibrator training and policy choice.
    endpoint={};endpoint_training={}
    for mode in MODES:
        endpoint[mode],endpoint_training[mode]=_fit_ranker(train,mode)
    ep_policies=[]
    for mode in MODES:
        for alpha in ALPHAS:
            ep_policies.append((f'{mode}:{alpha}',{'mode':mode,'weights':endpoint[mode],'alpha':alpha}))
    ep_choice=_select_cal(calibration,ep_policies)
    ep_policy=ep_choice[1]
    admission0,adm0_receipt=_fit_admission(train)
    admission1,adm1_receipt=_fit_admission(train,**ep_policy)
    adm0=_select_cal(calibration,[(str(t),{'admission':admission0,'threshold':t}) for t in THRESHOLDS])
    adm1=_select_cal(calibration,[(str(t),{**ep_policy,'admission':admission1,'threshold':t}) for t in THRESHOLDS])
    margins=_select_cal(calibration,[(str(t),{'margin':t}) for t in previous.MARGINS])
    chosen={'baseline':{},
            'calibrated_margin':margins[1],
            'endpoint_source':_select_cal(calibration,[(str(a),{'mode':'source',
                'weights':endpoint['source'],'alpha':a}) for a in ALPHAS])[1],
            'endpoint_destination':_select_cal(calibration,[(str(a),{'mode':'destination',
                'weights':endpoint['destination'],'alpha':a}) for a in ALPHAS])[1],
            'endpoint_joint':ep_policy,
            'admission_only':adm0[1],
            'endpoint_plus_admission':adm1[1]}
    # Evaluation labels first accessed after all model fitting and policy selection.
    evaluation=previous._capture_predictions(model,vd,ei,p55)
    results={name:_score(evaluation,policy) for name,policy in chosen.items()}
    bootstrap={name:_paired_bootstrap(evaluation,chosen['calibrated_margin'],chosen[name])
               for name in ('endpoint_source','endpoint_destination','endpoint_joint',
                            'admission_only','endpoint_plus_admission')}
    summary={name:{k:v for k,v in m.items() if k!='per_pair'} for name,m in results.items()}
    def verdict(delta,ci):
        if delta<=0:return 'NOT_SUPPORTED_ON_EVALUATION'
        if ci['delta_f1_pp_ci_95'][0]<=0:return 'POSITIVE_BUT_UNCERTAIN'
        return 'SUPPORTED_ON_THIS_HISTORICAL_COHORT_NOT_CERTIFIED'
    def comparison(challenger,baseline,ci):
        change=100*(summary[challenger]['overall']['f1']-
                    summary[baseline]['overall']['f1'])
        return {'delta_f1_pp':round(change,4),
                'paired_interval_f1_pp':ci['delta_f1_pp_ci_95'],
                'result':verdict(change,ci)}
    endpoint_result=comparison('endpoint_joint','calibrated_margin',bootstrap['endpoint_joint'])
    admission_result=comparison('admission_only','calibrated_margin',bootstrap['admission_only'])
    best_single=max(('endpoint_joint','admission_only'),
                    key=lambda n:summary[n]['overall']['f1'])
    combined_ci=_paired_bootstrap(evaluation,chosen[best_single],chosen['endpoint_plus_admission'])
    combined_result=comparison('endpoint_plus_admission',best_single,combined_ci)
    hypotheses={'endpoint_learning':endpoint_result,'admission_learning':admission_result,
                'combination_adds_value':{**combined_result,'comparator':best_single},
                'target_95_edge_f1_reached':summary['endpoint_plus_admission']['overall']['f1']>=.95}
    return {'schema':SCHEMA,'status':'RESEARCH_BAKEOFF_NOT_CERTIFIED',
        'checkpoint_step':checkpoint_step,'checkpoint_sha256':choice['weights_sha256'],
        'capture_config_sha256':cfg['capture_config_sha256'],'plan_sha256':cfg['plan_sha256'],
        'splits':{'training_pairs':len(ti),'calibration_pairs':len(ci),
                  'evaluation_pairs':len(ei),
                  'training_ids_sha256':hashlib.sha256(str(ti).encode().strip()).hexdigest(),
                  'calibration_sha256':previous._sha_ids(calibration),
                  'evaluation_sha256':previous._sha_ids(evaluation)},
        'endpoint_training':endpoint_training,'admission_training':{
            'baseline':adm0_receipt,'endpoint':adm1_receipt},
        'policy_selection':{'endpoint_joint':ep_choice[0],
            'admission_only_threshold':adm0[0],
            'joint_admission_threshold':adm1[0],
            'baseline_margin':margins[0],
            'endpoint_source_alpha':chosen['endpoint_source']['alpha'],
            'endpoint_destination_alpha':chosen['endpoint_destination']['alpha']},
        'evaluation':summary,'paired_bootstrap_vs_calibrated_margin':bootstrap,
        'hypotheses':hypotheses,
        'gold_at_inference':False,'head_training_split':'train_only',
        'sealed_test_used':False,'production_promotion':False,
        'warnings':['This uses a historically selected validation cohort and cannot certify generalization.',
                    'No evaluation labels used for fitting or tuning.',
                    'Endpoint ranker is a bounded frozen-feature residual, not a retrained G2 backbone.',
                    'A learned-arm failure falsifies this *specific* intervention, not all endpoint models.',
                    '95% target is edge F1; exact graphs reported independently.']}


def format_report(r):
    lines=[f'G2-0065 LEARNED ENDPOINT / ADMISSION BAKE-OFF — checkpoint {r["checkpoint_step"]}',
           'CPU-only; frozen checkpoint '+r['checkpoint_sha256'],
           f'TRAIN {r["splits"]["training_pairs"]} pairs | CAL {r["splits"]["calibration_pairs"]} pairs | '
           f'EVAL {r["splits"]["evaluation_pairs"]} distinct pairs',
           'Non-oracle evaluation; no sealed test. Historical validation cohort is NOT certification.',
           'Arm                         Precision  Recall     F1      dF1 vs cal-margin   Missing Extra Exact']
    baseline=r['evaluation']['calibrated_margin']['overall']['f1']
    for key,m in r['evaluation'].items():
        x=m['overall']
        lines.append(f'{key:<28}{x["precision"]*100:>7.2f}% {x["recall"]*100:>7.2f}% '
                     f'{x["f1"]*100:>7.2f}% {(x["f1"]-baseline)*100:>+9.2f}pp '
                     f'{x["missing"]:>7} {x["extra"]:>5} {x["exact"]:>3}/{x["graphs"]}')
    for key,ci in r['paired_bootstrap_vs_calibrated_margin'].items():
        lines.append(f'  {key}: paired 95% interval for ΔF1 (pp) = {ci["delta_f1_pp_ci_95"]}')
    lines += [f'Hypothesis results: {r["hypotheses"]}',
              f'Selected calibration policies: {r["policy_selection"]}',
              'Interpretation: endpoint claim supported only if learned endpoint beats calibrated margin;',
              'admission claim supported only if learned admission beats calibrated margin;',
              'joint benefit supported only if combined beats both alone on untouched evaluation;',
              'no training model changed, no oracle at inference, no production promotion.']
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
    a=p.parse_args(argv)
    try:
        report=run(training_dir=a.training_dir.resolve(),plan_dir=a.plan.resolve(),
                   capture_dir=a.capture.resolve(),checkpoint_step=a.checkpoint_step,
                   train_pairs=a.training_pairs,calibration_pairs=a.calibration_pairs,
                   evaluation_pairs=a.evaluation_pairs,cpu_threads=a.cpu_threads)
        print(format_report(report),flush=True)
        if a.output:
            if a.output.exists():raise ValueError('Refusing to overwrite a previous report')
            a.output.parent.mkdir(parents=True,exist_ok=True)
            with a.output.open('x',encoding='utf-8') as f:
                json.dump(report,f,indent=2,sort_keys=True);f.write('\n')
            print('New research report: '+str(a.output),flush=True)
    except (ValueError,RuntimeError,OSError,AssertionError) as exc:
        p.exit(2,f'G2_0065_BAKEOFF_ERROR: {exc}; no existing artifacts modified.\n')

if __name__=='__main__':
    main()
