"""G2-0066: frozen-representation endpoint information and ranking separability.

Research-only CPU bake-off. Fits only small independent endpoint readouts on
reachable TRAIN pairs. Selects policy on pair-disjoint CAL; evaluates on EVAL.
Tests both conditional endpoint ranking (GOLD slot alignment; diagnostic only)
and non-oracle full-graph inference with the real decoder. No Qwen, CUDA,
backbone updates, test/challenge data, or promotion. Historical validation is
not an independent certification set.

Run from main_computer/nanojev after installing G2-0064 and G2-0065:
  & $py -u -m tools.nanojev_g2_endpoint_separability_0066 --checkpoint-step 4900
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random

from tools import nanojev_g2_endpoint_bakeoff_0064 as prior
from tools import nanojev_g2_learning_bakeoff_0065 as b65

SCHEMA = 'nanojev.gamer.graph.g2.endpoint-separability-bakeoff-0066.v1'
SEED = 660066
ARCHS = ('bilinear', 'nonlinear')
PARTS = ('source', 'destination')
ALPHAS = (0.0, 0.5, 1.0, 2.0)
MARGINS = (0.0, 0.5, 1.0, 1.5, 2.0, 3.0)
DEFAULT_TRAINING = b65.DEFAULT_TRAINING
DEFAULT_PLAN = b65.DEFAULT_PLAN
DEFAULT_CAPTURE = b65.DEFAULT_CAPTURE


def _capture_activations(model, ds, indices, p55):
    """Capture Q/context BEFORE reading any labels, with no model mutation.

    q is the final normalized slot representation; context is the frozen
    projected node representation consumed by the original endpoint heads.
    """
    import torch
    cases = []
    for pair_idx in indices:
        samples, _ = ds.pair(pair_idx, with_labels=True)
        for sample in samples:
            features = prior._safe_features(sample)
            states = []
            handle = model.norm.register_forward_hook(
                lambda _layer, _inputs, out: states.append(out.detach().cpu().clone()))
            try:
                with torch.inference_mode():
                    logits = tuple(x.detach().cpu() for x in model(features))
                    node = features['node'].float()
                    kinds = features['kinds'].long()
                    context = (model.nodes(node) + model.kind(kinds)).detach().cpu()
            finally:
                handle.remove()
            if len(states) != 2 or states[-1].ndim != 3 or states[-1].shape[0] != 1:
                raise ValueError('FROZEN_SLOT_ACTIVATION_CONTRACT_CHANGED')
            # Convert inference tensors to ordinary detached CPU tensors for
            # subsequent readout backprop; backbone graph remains discarded.
            q = states[-1].squeeze(0).clone()
            context = context.clone()
            logits = tuple(x.clone() for x in logits)
            if q.shape[0] != logits[0].shape[0] or q.shape[1] != context.shape[1]:
                raise ValueError('SLOT_CONTEXT_SHAPE_DRIFT')
            if not cases:
                expected = p55.infer_edges(model, features, 'set_prediction')
                if prior._decode_logits(logits, features, 0., 1, max_edge=p55.MAX_EDGE) != expected:
                    raise ValueError('BASELINE_DECODER_PARITY_FAILED')
            # Labels may be read only after all preceding model operations.
            cases.append({'logits': logits, 'features': features,
                          'q': q, 'context': context,
                          'gold': set(sample['positive'].tolist()),
                          'truth_count': int(sample['truth_count']),
                          'family': sample['family'], 'mode': sample['mode'],
                          'pair_index': pair_idx, 'id': sample['prompt_sha256'],
                          'max_edge': p55.MAX_EDGE})
    return cases


def _match_train(cases, p55):
    """GOLD slot alignment exclusively for TRAIN supervision."""
    matched = []
    for case in cases:
        if len(case['gold']) != case['truth_count']:
            raise ValueError('UNREACHABLE_TRAIN_GOLD_FOR_ENDPOINT_LOSS')
        assigned = prior._oracle_matching(
            case['logits'], case['gold'], case['features'], p55.rectangular_assignment)
        matched.append((case, assigned))
    if not any(pairs for _case, pairs in matched):
        raise ValueError('NO_TRAIN_ENDPOINT_POSITIVES')
    return matched


class EndpointReadout:
    """Factory wrapper to keep trainable modules out of import-time torch use."""
    @staticmethod
    def create(arch):
        import torch
        from torch import nn
        if arch == 'bilinear':
            class Bilinear(nn.Module):
                def __init__(self):
                    super().__init__()
                    self.weight = nn.Parameter(torch.zeros(64, 64))
                def forward(self, q, node):
                    return (q @ self.weight @ node.T) / math.sqrt(q.shape[1])
            return Bilinear()
        if arch == 'nonlinear':
            class Nonlinear(nn.Module):
                def __init__(self):
                    super().__init__()
                    self.layers = nn.Sequential(nn.LayerNorm(256), nn.Linear(256, 64),
                                                nn.GELU(), nn.Linear(64, 1))
                    nn.init.zeros_(self.layers[-1].weight)
                    nn.init.zeros_(self.layers[-1].bias)
                def forward(self, q, node):
                    slots = q[:, None, :].expand(-1, len(node), -1)
                    nodes = node[None, :, :].expand(len(q), -1, -1)
                    inp = torch.cat((slots, nodes, slots*nodes, slots-nodes), dim=-1)
                    return self.layers(inp).squeeze(-1)
            return Nonlinear()
        raise ValueError('Unknown endpoint architecture')


def _pool(case, part):
    import torch
    field = 'src' if part == 'source' else 'dst'
    return torch.unique(case['features'][field].long(), sorted=True)


def _model_score(case, part, readout=None, alpha=1., ablate=False):
    """Label-blind score for ALL slots and ALL nodes; original head preserved."""
    import torch
    base = case['logits'][1 if part == 'source' else 2]
    if readout is None or alpha == 0:
        return base
    nodes = case['context']
    if ablate:
        # Fixed deterministic permutation; never uses endpoint labels.
        seed = int(hashlib.sha256(case['id'].encode()).hexdigest()[:12], 16)
        order = torch.randperm(len(nodes), generator=torch.Generator().manual_seed(seed))
        nodes = nodes[order]
    return base + alpha*readout(case['q'], nodes)


def _fit(part, arch, matched, epochs=6):
    import torch
    import torch.nn.functional as F
    if epochs < 1:
        raise ValueError('Positive readout epochs required')
    torch.manual_seed(SEED + (0 if part=='source' else 100) +
                      (0 if arch=='bilinear' else 200))
    model = EndpointReadout.create(arch)
    opt = torch.optim.AdamW(model.parameters(), lr=0.008 if arch=='bilinear' else 0.003,
                            weight_decay=0.003)
    rng = random.Random(SEED)
    rows = list(matched)
    total_matched = sum(len(matches) for _case,matches in rows)
    for _ in range(epochs):
        rng.shuffle(rows)
        losses=[]
        for case, assigned in rows:
            if not assigned:
                continue
            q_slots = torch.tensor([q for q,_ in assigned], dtype=torch.long)
            candidates = _pool(case, part)
            field = 'src' if part == 'source' else 'dst'
            targets = torch.tensor([int(case['features'][field][edge]) for _,edge in assigned])
            where = torch.searchsorted(candidates,targets)
            if not torch.equal(candidates[where],targets):
                raise ValueError('TRAIN_GOLD_ENDPOINT_NOT_IN_CANDIDATES')
            logits = case['logits'][1 if part == 'source' else 2]
            correction = model(case['q'][q_slots], case['context'][candidates])
            corrected = logits[q_slots][:, candidates] + correction
            loss = F.cross_entropy(corrected, where) + 1e-5*(correction**2).mean()
            if not bool(torch.isfinite(loss)):
                raise ValueError('NONFINITE_ENDPOINT_TRAINING')
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 2.)
            opt.step()
            losses.append(float(loss.detach()))
        if not losses:
            raise ValueError('EMPTY_ENDPOINT_TRAINING')
    model.eval().requires_grad_(False)
    return model, {'gold_aligned_slot_examples': total_matched,
                   'epochs': epochs, 'final_mean_loss':sum(losses)/len(losses),
                   'architecture':arch, 'part':part}


def _adjusted_logits(case, src=None, dst=None, alpha=1., ablate=False):
    """No gold, oracle slot matching, or target cardinality used in inference."""
    return (case['logits'][0],
            _model_score(case,'source',src,alpha,ablate),
            _model_score(case,'destination',dst,alpha,ablate))


def _pred(case, *, src=None,dst=None,alpha=1.,margin=0.,ablate=False):
    with __import__('torch').inference_mode():
        return prior._decode_logits(_adjusted_logits(case,src,dst,alpha,ablate),
                                    case['features'], margin, 1,
                                    max_edge=case['max_edge'])


def _graph_scores(cases,policy):
    count=prior._stats()
    by_family=defaultdict(prior._stats)
    for c in cases:
        pred=_pred(c,**policy)
        prior._observe(count,pred,c['gold'],c['truth_count'])
        prior._observe(by_family[c['family']],pred,c['gold'],c['truth_count'])
    return {'overall':prior._summarize(count),
            'by_family':{x:prior._summarize(y) for x,y in sorted(by_family.items())}}


def _select(cases, src=None,dst=None):
    """Calibration-only policy selection, prefer no intervention on exact ties."""
    choices=[]
    for alpha in ALPHAS if (src is not None or dst is not None) else (0.,):
        for margin in MARGINS:
            policy={'src':src,'dst':dst,'alpha':alpha,'margin':margin}
            result=_graph_scores(cases,policy)['overall']
            choices.append((result['f1'],-result['edge_errors'],-alpha,
                            -abs(margin),policy,result))
    _f,_e,_a,_m,policy,score=max(choices,key=lambda p:p[:4])
    return policy,score


def _endpoint_ranks(cases,p55,part,readouts):
    """Conditional ranking: diagnostic GOLD slot alignment AFTER inference.

    This is NOT a label-blind graph accuracy metric. Identical frozen matching
    across all arms, but matching itself uses gold, and is disclosed as such.
    """
    sums={k:Counter(n=0, top1=0,top3=0,top5=0,rank_total=0,reciprocal=0.,
                    positive_margin=0,margin_sum=0.) for k in readouts}
    skipped=0
    with __import__('torch').inference_mode():
        for case in cases:
            if len(case['gold'])!=case['truth_count']:
                skipped+=1
                continue
            matches=prior._oracle_matching(case['logits'],case['gold'],
                                           case['features'],p55.rectangular_assignment)
            pool=_pool(case,part)
            expected=case['features']['src' if part=='source' else 'dst']
            scores={name:_model_score(case,part,reader,1.0)
                    for name,reader in readouts.items()}
            for slot,edge in matches:
                gold_node=int(expected[edge]);target_index=int((pool==gold_node).nonzero()[0,0])
                for name,matrix in scores.items():
                    row=matrix[slot,pool]
                    gold_score=float(row[target_index]);sorted_scores=row.sort(descending=True).values
                    rank=1+int((row>row[target_index]).sum())
                    rival=float(row[pool!=gold_node].max()) if len(pool)>1 else gold_score
                    stat=sums[name]
                    stat['n']+=1;stat['top1']+=int(rank==1)
                    stat['top3']+=int(rank<=3);stat['top5']+=int(rank<=5)
                    stat['rank_total']+=rank;stat['reciprocal']+=1/rank
                    stat['margin_sum']+=gold_score-rival
                    stat['positive_margin']+=int(gold_score>rival)
    return {'skipped_unreachable_graphs':skipped,'oracle_slot_alignment':True,
            'conditional_endpoint_ranking':{name:{
                'gold_aligned_slots':x['n'],
                'top1':x['top1']/x['n'] if x['n'] else 0.,
                'top3':x['top3']/x['n'] if x['n'] else 0.,
                'top5':x['top5']/x['n'] if x['n'] else 0.,
                'mean_rank':x['rank_total']/x['n'] if x['n'] else 0.,
                'mean_reciprocal_rank':x['reciprocal']/x['n'] if x['n'] else 0.,
                'mean_margin_to_strongest_competitor':x['margin_sum']/x['n'] if x['n'] else 0.
            } for name,x in sums.items()}}


def _paired_ci(cases,base,challenger,reps=300):
    """Paired pair-block bootstrap, no claim of independent certification."""
    blocks=defaultdict(lambda:[[],[]])
    for c in cases:
        for i,p in enumerate((base,challenger)):
            pred=_pred(c,**p)
            blocks[c['pair_index']][i].append((len(pred&c['gold']),len(pred),c['truth_count']))
    grouped=list(blocks.values());rng=random.Random(SEED)
    def f1(rows):
        tp=sum(x for x,_,_ in rows);pred=sum(y for _,y,_ in rows)
        gold=sum(z for _,_,z in rows)
        return 2*tp/(gold+pred) if gold+pred else 0.
    diffs=[]
    for _ in range(reps):
        bag=[grouped[rng.randrange(len(grouped))] for _ in grouped]
        diffs.append(100*(f1([r for b in bag for r in b[1]])-
                          f1([r for b in bag for r in b[0]])))
    diffs.sort()
    return [round(diffs[int(reps*.025)],3),round(diffs[int(reps*.975)-1],3)]


def run(*,training_dir,plan_dir,capture_dir,checkpoint_step=4900,
        train_pairs=96,calibration_pairs=24,evaluation_pairs=48,
        epochs=6,cpu_threads=2):
    import torch
    if min(train_pairs,calibration_pairs,evaluation_pairs,epochs,cpu_threads)<1:
        raise ValueError('All counts must be positive')
    torch.manual_seed(SEED)
    model,p55,td,vd,choice,cfg=b65._load_corpus_and_model(
        training_dir,plan_dir,capture_dir,checkpoint_step,cpu_threads)
    budget=int(cfg.get('budget',{}).get('requested_training_graphs',5000))//2
    ti=b65._balanced_train(td,train_pairs,max_index=budget)
    ci,ei=prior._balanced_pair_partition(vd,calibration_pairs,evaluation_pairs,seed=640064)
    if set(ci)&set(ei):
        raise ValueError('DISJOINT_PARTITION_FAILURE')
    train=_capture_activations(model,td,ti,p55)
    aligned=_match_train(train,p55)
    learned={};fit={}
    for arch in ARCHS:
        learned[arch]={};fit[arch]={}
        for part in PARTS:
            learned[arch][part],fit[arch][part]=_fit(part,arch,aligned,epochs)
    calibration=_capture_activations(model,vd,ci,p55)
    policies={};cal_scores={}
    for name,src,dst in [('calibrated_baseline',None,None),
                         ('bilinear_source',learned['bilinear']['source'],None),
                         ('bilinear_destination',None,learned['bilinear']['destination']),
                         ('bilinear_joint',learned['bilinear']['source'],learned['bilinear']['destination']),
                         ('nonlinear_source',learned['nonlinear']['source'],None),
                         ('nonlinear_destination',None,learned['nonlinear']['destination']),
                         ('nonlinear_joint',learned['nonlinear']['source'],learned['nonlinear']['destination'])]:
        policies[name],cal_scores[name]=_select(calibration,src,dst)
    # All training and policy selection completed before loading evaluation.
    evaluation=_capture_activations(model,vd,ei,p55)
    scores={name:_graph_scores(evaluation,policy) for name,policy in policies.items()}
    baseline=policies['calibrated_baseline']
    intervals={name:_paired_ci(evaluation,baseline,policy)
               for name,policy in policies.items() if name!='calibrated_baseline'}
    # Specific, falsifiable feature-dependence control on the same frozen model.
    feature_ablation={}
    for arch in ARCHS:
        policy=policies[arch+'_joint']
        feature_ablation[arch]=_graph_scores(evaluation,{**policy,'ablate':True})
    rank={part:_endpoint_ranks(evaluation,p55,part,{
        'frozen':None,
        'bilinear':learned['bilinear'][part],
        'nonlinear':learned['nonlinear'][part]}) for part in PARTS}
    baseline_f1=scores['calibrated_baseline']['overall']['f1']
    signals={}
    for arch in ARCHS:
        name=arch+'_joint';f=scores[name]['overall']['f1']
        intervals_ci=intervals[name]
        control=feature_ablation[arch]['overall']['f1']
        signals[arch]={
            'nonoracle_delta_f1_pp':round(100*(f-baseline_f1),4),
            'paired_ci_95_pp':intervals_ci,
            'improves_nonoracle_graph_f1':f>baseline_f1 and intervals_ci[0]>0,
            'feature_ablation_f1':control,
            'ablation_delta_pp':round(100*(f-control),4),
            'conditional_source_top1_gain_pp':round(100*(rank['source']['conditional_endpoint_ranking'][arch]['top1']-
                                          rank['source']['conditional_endpoint_ranking']['frozen']['top1']),4),
            'conditional_destination_top1_gain_pp':round(100*(rank['destination']['conditional_endpoint_ranking'][arch]['top1']-
                                          rank['destination']['conditional_endpoint_ranking']['frozen']['top1']),4)}
    return {'schema':SCHEMA,'status':'EXPERIMENTAL_SEPARABILITY_DIAGNOSTIC_NOT_CERTIFIED',
            'checkpoint_step':checkpoint_step,'checkpoint_sha256':choice['weights_sha256'],
            'capture_config_sha256':cfg['capture_config_sha256'],'plan_sha256':cfg['plan_sha256'],
            'training':fit,'split':{
                'train_pairs':len(ti),'calibration_pairs':len(ci),'evaluation_pairs':len(ei),
                'calibration_prompt_sha256':prior._sha_ids(calibration),
                'evaluation_prompt_sha256':prior._sha_ids(evaluation)},
            'calibration':{name:{'alpha':p['alpha'],'margin':p['margin'],
                                  'f1':cal_scores[name]['f1']}
                           for name,p in policies.items()},
            'evaluation_graph':scores,'conditional_endpoint':rank,
            'ablation':feature_ablation,'paired_ci_95_pp_vs_calibrated_baseline':intervals,
            'hypothesis_signals':signals,'sealed_test_used':False,
            'production_promotion':False,'gold_used_at_inference':False,
            'warnings':[
                'Conditional endpoint metrics use GOLD-aware Hungarian slot alignment; diagnostic only, NOT deployable accuracy.',
                'Frozen-context feature ablation permutes node features only, and is not a causal proof of representation sufficiency.',
                'Evaluation is distinct from train and calibration, but checkpoint was previously selected on this historical validation cohort.',
                'Only the two bounded readout architectures are tested; failure cannot establish missing upstream information.',
                '95% means edge F1; exact graph accuracy is a separate target.']}


def format_report(r):
    lines=[f'G2-0066 ENDPOINT INFORMATION / RANKING SEPARABILITY — checkpoint {r["checkpoint_step"]}',
           'CPU-only; frozen checkpoint '+r['checkpoint_sha256'],
           f'TRAIN {r["split"]["train_pairs"]} pairs | CAL {r["split"]["calibration_pairs"]} pairs | '
           f'EVAL {r["split"]["evaluation_pairs"]} distinct pairs',
           'CONDITIONAL endpoint ranking uses GOLD-aware slot alignment: diagnostic ONLY.',
           'Ranking model               Source top1/top3/top5        Destination top1/top3/top5']
    for name in ('frozen','bilinear','nonlinear'):
        def fmt(part):
            x=r['conditional_endpoint'][part]['conditional_endpoint_ranking'][name]
            return f'{x["top1"]*100:5.1f}/{x["top3"]*100:5.1f}/{x["top5"]*100:5.1f}%'
        lines.append(f'{name:<26}{fmt("source"):<29}{fmt("destination")}')
    lines += ['NON-ORACLE end-to-end graph evaluation (calibration policies frozen before EVAL):',
              'Arm                        Precision   Recall      F1      Delta vs baseline   Exact']
    ref=r['evaluation_graph']['calibrated_baseline']['overall']['f1']
    for key,v in r['evaluation_graph'].items():
        x=v['overall']
        lines.append(f'{key:<27}{100*x["precision"]:7.2f}%   {100*x["recall"]:7.2f}%  '
                     f'{100*x["f1"]:7.2f}%   {100*(x["f1"]-ref):+8.2f}pp'
                     f'      {x["exact"]}/{x["graphs"]}')
    for arch,h in r['hypothesis_signals'].items():
        lines.append(f'{arch}: paired 95% ΔF1 {h["paired_ci_95_pp"]} pp; '
                     f'ablation F1 {100*h["feature_ablation_f1"]:.2f}%; '
                     f'conditional source top1 Δ {h["conditional_source_top1_gain_pp"]:+.2f} pp, '
                     f'destination Δ {h["conditional_destination_top1_gain_pp"]:+.2f} pp')
    lines += ['INTERPRETATION: If conditional ranking improves but non-oracle graphs do not,',
              'slot alignment / decoder integration remains limiting. If both fail, the tested',
              'readouts do not recover endpoint information; absence of information is NOT proven.',
              'Historical validation, no sealed test, no production promotion.']
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
    p.add_argument('--epochs',type=int,default=6)
    p.add_argument('--cpu-threads',type=int,default=2)
    p.add_argument('--output',type=Path)
    a=p.parse_args(argv)
    try:
        report=run(training_dir=a.training_dir.resolve(),plan_dir=a.plan.resolve(),
                   capture_dir=a.capture.resolve(),checkpoint_step=a.checkpoint_step,
                   train_pairs=a.training_pairs,calibration_pairs=a.calibration_pairs,
                   evaluation_pairs=a.evaluation_pairs,epochs=a.epochs,
                   cpu_threads=a.cpu_threads)
        print(format_report(report),flush=True)
        if a.output:
            if a.output.exists():raise ValueError('Refusing to overwrite previous report')
            a.output.parent.mkdir(parents=True,exist_ok=True)
            with a.output.open('x',encoding='utf-8') as f:
                json.dump(report,f,indent=2,sort_keys=True);f.write('\n')
            print('New research report: '+str(a.output),flush=True)
    except (ValueError,RuntimeError,OSError,AssertionError) as exc:
        p.exit(2,f'G2_0066_BAKEOFF_ERROR: {exc}; no existing artifacts modified.\n')

if __name__=='__main__':
    main()
