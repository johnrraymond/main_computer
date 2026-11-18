"""G2-0064: frozen-checkpoint, CPU-only endpoint and decoder causal bake-off.

Post-inference oracle edits are LABEL-INFORMED DIAGNOSTICS, never inference
policies or grounds for certification. Genuine (label-free) decoder choices are
selected only on a separate, pair-disjoint calibration subset. No train, test,
challenge, CUDA, Qwen, checkpoint writes, or production promotion.

Run from main_computer/nanojev, after applying the G2-0063 diagnostics:
  & $py -u -m tools.nanojev_g2_endpoint_bakeoff_0064 --checkpoint-step 4900
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random

from tools import nanojev_g2_learning_probe as receipts
from tools import nanojev_g2_plateau_diagnostic as plateau

SCHEMA = 'nanojev.gamer.graph.g2.endpoint-causal-bakeoff-0064.v1'
MARGINS = (-1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0)
ALT_K = (1, 2, 4)
ORACLES = {
    'oracle_source': ('source',),
    'oracle_destination': ('destination',),
    'oracle_both_endpoints': ('source','destination'),
    'oracle_relation_and_null': ('relation',),
    'oracle_all_components': ('source','destination','relation'),
}
DEFAULT_TRAINING = Path('..') / 'runtime' / 'reports' / 'gamer-v1' / 'graph-g2-0062-5000-4'
DEFAULT_CAPTURE = plateau.DEFAULT_CAPTURE
DEFAULT_PLAN = plateau.DEFAULT_PLAN


def _safe_features(sample):
    """Allowlisted inference fields; never pass labels, canonical IDs or gold counts."""
    return {name: sample[name] for name in ('node','src','dst','relation','kinds','margin','geometry')}


def _decode_logits(logits, sample, margin, alternative_k=1, *, max_edge=96):
    """Ground-truth-free slot -> admissible edge decoder.

    alternative_k=1 reproduces the original G2-0055 decoder exactly. Larger
    values permit alternate typed edges for slots which would otherwise
    duplicate an already selected edge; this is a *new* diagnostic policy.
    """
    import torch
    import torch.nn.functional as F

    rel,src,dst=logits
    ids=sample['src'].to(rel.device);targets=sample['dst'].to(rel.device)
    labels=sample['relation'].to(rel.device)+1
    if not len(ids):return set()
    if alternative_k < 1:raise ValueError('alternative_k must be positive')
    lr=F.log_softmax(rel,dim=-1)
    ls=F.log_softmax(src,dim=-1)
    ld=F.log_softmax(dst,dim=-1)
    proposals=[]
    for q in range(rel.shape[0]):
        if int(rel[q].argmax())==0:continue
        confidence=ls[q,ids]+ld[q,targets]+lr[q,labels]
        choices=torch.topk(confidence,k=min(alternative_k,len(ids)),sorted=True)
        for score,idx in zip(choices.values.tolist(),choices.indices.tolist()):
            # Existing typed-vs-null admission contract, with optional margin.
            if float(rel[q,labels[idx]]-rel[q,0])>margin:
                proposals.append((float(score),q,int(idx)))
    # For top-1 this is equivalent to per-edge score-max dedup and MAX_EDGE cap.
    # For top-K it implements a deterministic max-confidence greedy matching
    # (distinct edge and slot), NOT oracle matching.
    used_slots=set();used_edges=set()
    for score,q,idx in sorted(proposals,key=lambda x:(-x[0],x[1],x[2])):
        if q in used_slots or idx in used_edges:continue
        used_slots.add(q);used_edges.add(idx)
        if len(used_edges)>=max_edge:break
    return used_edges


def _oracle_matching(logits, gold, sample, assignment):
    """Hungarian GOLD matching, used only AFTER baseline inference."""
    import torch
    import torch.nn.functional as F
    rel,src,dst=logits
    if not gold:return []
    chosen=sorted(gold)
    ids=sample['src'][chosen].to(rel.device)
    targets=sample['dst'][chosen].to(rel.device)
    labels=sample['relation'][chosen].to(rel.device)+1
    with torch.inference_mode():
        cost=-(F.log_softmax(rel,-1)[:,labels]
               +F.log_softmax(src,-1)[:,ids]
               +F.log_softmax(dst,-1)[:,targets]).detach().cpu().numpy()
    slots,columns=assignment(cost)
    return [(int(slot),chosen[int(col)]) for slot,col in zip(slots,columns)]


def _swap_top_to_gold(vector, gold_index):
    """Correct only the top class identity; preserve sorted logit magnitudes."""
    top=int(vector.argmax())
    if top==gold_index:return
    old=vector[top].clone()
    vector[top]=vector[gold_index]
    vector[gold_index]=old


def _intervene(logits, sample, mapping, parts):
    """Label-guided counterfactual; no logits used to train or select policy."""
    r,s,d=(v.clone() for v in logits)
    for slot,edge in mapping:
        if 'source' in parts:
            _swap_top_to_gold(s[slot],int(sample['src'][edge]))
        if 'destination' in parts:
            _swap_top_to_gold(d[slot],int(sample['dst'][edge]))
        if 'relation' in parts:
            # Includes recovery from null: report this as RELATION + NULL,
            # never as pure relation-type classification accuracy.
            _swap_top_to_gold(r[slot],int(sample['relation'][edge])+1)
    return r,s,d


def _stats():
    return Counter(gold=0,predicted=0,tp=0,missing=0,extra=0,exact=0,graphs=0,
                   candidate_reachable=0)


def _observe(stats, pred, gold, truth_count):
    tp=len(pred & gold)
    stats.update({'gold':int(truth_count),'predicted':len(pred),'tp':tp,
                  'missing':int(truth_count)-tp,'extra':len(pred-gold),
                  'exact':int(pred==gold and len(gold)==truth_count),
                  'graphs':1,'candidate_reachable':int(len(gold)==truth_count)})


def _summarize(c):
    g=c['gold'];p=c['predicted'];tp=c['tp'];n=c['graphs']
    return {'graphs':n,'gold_edges':g,'predicted_edges':p,'true_positive':tp,
            'missing':c['missing'],'extra':c['extra'],'exact':c['exact'],
            'candidate_reachable':c['candidate_reachable'],
            'precision':tp/p if p else 0.0,'recall':tp/g if g else 0.0,
            'f1':2*tp/(g+p) if g+p else 0.0,
            'edge_errors':c['missing']+c['extra']}


def _select_margin_and_k(calibration):
    """Select ONLY on calibration. Prefer simple decoder on exact F1 ties."""
    if not calibration:raise ValueError('Empty calibration cohort')
    outcomes={}
    for margin in MARGINS:
        for alt in ALT_K:
            count=_stats()
            for case in calibration:
                pred=_decode_logits(case['logits'],case['features'],margin,alt,
                                    max_edge=case['max_edge'])
                _observe(count,pred,case['gold'],case['truth_count'])
            outcomes[(margin,alt)]=_summarize(count)
    def objective(item):
        (margin,alt),v=item
        # F1, edge errors, then less intervention and smaller admission margin.
        return (v['f1'],-v['edge_errors'],-(alt>1),-alt,-abs(margin),-margin)
    # Select margin-only and joint margin+unique policy separately.
    margin_only=max(((k,v) for k,v in outcomes.items() if k[1]==1),key=objective)[0]
    joint=max(outcomes.items(),key=objective)[0]
    return {'margin_only':margin_only,'joint':joint,
            'calibration_scores':{f'margin={m},k={k}':v for (m,k),v in outcomes.items()}}


def _balanced_pair_partition(ds,calibration_pairs,evaluation_pairs,seed=640064):
    """Pair-disjoint family/mode balanced sampling WITHOUT label selection."""
    if calibration_pairs<1 or evaluation_pairs<1:
        raise ValueError('Both calibration and evaluation must contain pairs')
    cells=defaultdict(list)
    for idx,(_,_,a,b) in enumerate(ds.pairs):
        if a['family']!=b['family'] or a['mode']!=b['mode']:
            raise ValueError('Paired metadata mismatch')
        cells[(a['family'],a['mode'])].append(idx)
    rng=random.Random(seed)
    for v in cells.values():rng.shuffle(v)
    chosen=[]
    while len(chosen)<calibration_pairs+evaluation_pairs and any(cells.values()):
        for cell in sorted(cells):
            if cells[cell] and len(chosen)<calibration_pairs+evaluation_pairs:
                chosen.append(cells[cell].pop())
    if len(chosen)<calibration_pairs+evaluation_pairs:
        raise ValueError(f'Only {len(chosen)} validation pairs available; requested '
                         f'{calibration_pairs+evaluation_pairs}')
    cal=chosen[:calibration_pairs];ev=chosen[calibration_pairs:]
    assert not set(cal)&set(ev)
    return cal,ev


def _capture_predictions(model,ds,indices,p55,*,baseline_parity=True):
    """Run inference once; detach CPU logits; label access begins thereafter."""
    import torch
    cases=[]
    model.eval()
    with torch.inference_mode():
        for pair_idx in indices:
            samples,_=ds.pair(pair_idx,with_labels=True)
            for sample in samples:
                features=_safe_features(sample)
                logits=tuple(v.detach().cpu() for v in model(features))
                baseline=_decode_logits(logits,features,0.0,1,max_edge=p55.MAX_EDGE)
                if baseline_parity and len(cases)<2:
                    actual=p55.infer_edges(model,features,'set_prediction')
                    if baseline!=actual:
                        raise ValueError('BASIS_DECODER_PARITY_FAILED; aborting audit')
                # Strict GOLD BOUNDARY: after forward and baseline decoding.
                gold=set(sample['positive'].tolist())
                cases.append({'logits':logits,'features':features,
                              'gold':gold,'truth_count':int(sample['truth_count']),
                              'family':sample['family'],'mode':sample['mode'],
                              'pair_index':pair_idx,'id':sample['prompt_sha256'],
                              'baseline':baseline,'max_edge':p55.MAX_EDGE})
    return cases


def _score_interventions(cases,selected,assignment):
    from collections import defaultdict
    totals=defaultdict(_stats)
    by_family=defaultdict(lambda:defaultdict(_stats))
    by_mode=defaultdict(lambda:defaultdict(_stats))
    reachable=defaultdict(_stats)
    all_keys=['baseline','calibrated_margin','calibrated_unique_assignment',*ORACLES]
    for c in cases:
        preds={'baseline':c['baseline']}
        m,_=selected['margin_only']
        preds['calibrated_margin']=_decode_logits(c['logits'],c['features'],m,1,
                                                  max_edge=c['max_edge'])
        m,k=selected['joint']
        preds['calibrated_unique_assignment']=_decode_logits(c['logits'],c['features'],m,k,
                                                               max_edge=c['max_edge'])
        # Oracle labels are used here only for the counterfactual arms.
        matches=_oracle_matching(c['logits'],c['gold'],c['features'],assignment)
        for name,parts in ORACLES.items():
            intervention=_intervene(c['logits'],c['features'],matches,parts)
            preds[name]=_decode_logits(intervention,c['features'],0.0,1,
                                       max_edge=c['max_edge'])
        for name in all_keys:
            pred=preds[name]
            _observe(totals[name],pred,c['gold'],c['truth_count'])
            _observe(by_family[name][c['family']],pred,c['gold'],c['truth_count'])
            _observe(by_mode[name][c['mode']],pred,c['gold'],c['truth_count'])
            if len(c['gold'])==c['truth_count']:
                _observe(reachable[name],pred,c['gold'],c['truth_count'])
    return {'overall':{key:_summarize(totals[key]) for key in all_keys},
            'reachable_only':{key:_summarize(reachable[key]) for key in all_keys},
            'by_family':{key:{f:_summarize(row) for f,row in group.items()}
                         for key,group in by_family.items()},
            'by_mode':{key:{m:_summarize(row) for m,row in group.items()}
                       for key,group in by_mode.items()}}


def _sha_ids(cases):
    # Public integrity digest, never serializes gold or logits.
    ids='\n'.join(c['id'] for c in cases)
    return hashlib.sha256(ids.encode('ascii')).hexdigest()


def run(*,training_dir,plan_dir,capture_dir,checkpoint_step=4900,
        calibration_pairs=24,evaluation_pairs=48,cpu_threads=2):
    """Read-only CPU experiment: record and hash-verify immutable training lineage."""
    import torch
    from safetensors.torch import load_file
    from tools import nanojev_gamer_v1_g2_scaling_0059 as g
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55

    if cpu_threads<1:raise ValueError('cpu_threads must be positive')
    torch.set_num_threads(cpu_threads)
    snap=receipts.snapshot(training_dir)
    if snap['status'] not in ('HAS_OPTIMIZER_CHECKPOINTS','STEP_ZERO_ONLY'):
        raise ValueError(f'No committed checkpoint: {snap["status"]}')
    choice=next((r for r in snap['history'] if r['step']==checkpoint_step),None)
    if choice is None:raise ValueError(f'Checkpoint {checkpoint_step} not committed')
    cfg=receipts._load_json(training_dir/'training.json')
    if cfg.get('production_promotion') is not False or cfg.get('sealed_test_used') is not False:
        raise ValueError('Refusing non-research checkpoint')
    if cfg.get('capture_config_sha256')!=g.file_sha(capture_dir/'capture.json'):
        raise ValueError('Capture lineage checksum mismatch')
    if cfg.get('plan_sha256')!=g.file_sha(plan_dir/'plan.json'):
        raise ValueError('Plan lineage checksum mismatch')
    weights=training_dir/f'checkpoint-{choice["step"]:09d}.safetensors'
    if not weights.is_file() or g.file_sha(weights)!=choice.get('weights_sha256'):
        raise ValueError('Checkpoint weights missing or sha256 mismatch')
    model=p55.model_for('set_prediction').to('cpu')
    model.load_state_dict(load_file(str(weights),device='cpu'),strict=True)
    model.eval().requires_grad_(False)
    corpus=g.FeatureCorpus(plan_dir,capture_dir,'validation',require_complete=False)
    cal_ix,eval_ix=_balanced_pair_partition(corpus,calibration_pairs,evaluation_pairs)
    calibration=_capture_predictions(model,corpus,cal_ix,p55)
    chosen=_select_margin_and_k(calibration)
    # All policy selection completed before evaluation predictions and labels
    # are read. Pair-disjoint evaluation is used exactly once.
    evaluation=_capture_predictions(model,corpus,eval_ix,p55)
    scores=_score_interventions(evaluation,chosen,p55.rectangular_assignment)
    cal_summary={
        'graphs':len(calibration),'pair_count':len(cal_ix),'sha256_of_prompt_ids':_sha_ids(calibration),
        'selected_margin_only':chosen['margin_only'][0],
        'selected_joint_margin':chosen['joint'][0],
        'selected_joint_top_k':chosen['joint'][1],
        'selected_calibration_scores':{
            'margin_only':chosen['calibration_scores'][f'margin={chosen["margin_only"][0]},k=1'],
            'joint':chosen['calibration_scores'][f'margin={chosen["joint"][0]},k={chosen["joint"][1]}']}}
    return {'schema':SCHEMA,'status':'EXPERIMENTAL_FROZEN_CAUSAL_DIAGNOSTIC_NOT_CERTIFIED',
            'checkpoint_step':choice['step'],'checkpoint_sha256':choice['weights_sha256'],
            'capture_config_sha256':cfg['capture_config_sha256'],'plan_sha256':cfg['plan_sha256'],
            'calibration':cal_summary,
            'evaluation':{'graphs':len(evaluation),'pair_count':len(eval_ix),
                          'sha256_of_prompt_ids':_sha_ids(evaluation),
                          'results':scores},
            'oracle_matching':'gold-aware Hungarian assignment on reachable gold edges; top-class logit swaps preserve each slot-logit multiset but alter candidate scores',
            'relation_oracle_includes_null_recovery':True,
            'output_policy':'read-only checkpoint; label-guided counterfactuals only',
            'sealed_test_used':False,'production_promotion':False,
            'warnings':[
                'Oracle arms explicitly see evaluation gold and are not deployable scores or guaranteed accuracy ceilings.',
                'Oracle edits can interact; deltas are not exclusive causal attributions.',
                'Calibrated policies are selected only on disjoint calibration pairs.',
                'Checkpoint was already selected using this historical validation split; '
                'this is a diagnostic, not an independent certification cohort.',
                'The 95% target means edge F1; exact graph accuracy is reported independently.',
            ]}


def format_report(report):
    ev=report['evaluation']['results'];overall=ev['overall'];base=overall['baseline']
    lines=[f'G2-0064 FROZEN CAUSAL BAKE-OFF — checkpoint {report["checkpoint_step"]}',
           'CPU-only; checkpoint SHA-256: '+report['checkpoint_sha256'],
           f'Calibration: {report["calibration"]["pair_count"]} pairs; evaluation: '
           f'{report["evaluation"]["pair_count"]} distinct pairs. No sealed test.',
           'Calibrated margin: '+str(report['calibration']['selected_margin_only']),
           'Calibrated unique-assignment: margin '+str(report['calibration']['selected_joint_margin'])+
           ', top-k '+str(report['calibration']['selected_joint_top_k']),
           'EVALUATION: SAMPLE ONLY; all arms use the SAME frozen logits and examples.',
           'Intervention                  Precision  Recall     F1        dF1    Miss  Extra  Exact']
    for name,metrics in overall.items():
        lines.append(f'{name:<30}{metrics["precision"]*100:>7.2f}% '
                     f'{metrics["recall"]*100:>7.2f}% '
                     f'{metrics["f1"]*100:>7.2f}% '
                     f'{(metrics["f1"]-base["f1"])*100:>+7.2f}pp '
                     f'{metrics["missing"]:>6} {metrics["extra"]:>6}  '
                     f'{metrics["exact"]}/{metrics["graphs"]}')
    singles=['oracle_source','oracle_destination','oracle_relation_and_null']
    top=max(singles,key=lambda n:overall[n]['f1']-base['f1'])
    lines.append(f'Largest single-component counterfactual F1 difference: {top} '
                 f'{(overall[top]["f1"]-base["f1"])*100:+.2f} pp (not an exclusive causal effect).')
    lines.append('FAMILY BREAKDOWN (F1):')
    for name in overall:
        family=ev['by_family'][name]
        lines.append('  '+name+': '+', '.join(f'{key}={value["f1"]*100:.2f}%'
                  for key,value in sorted(family.items())))
    lines.extend(['Reachable-only metrics are in JSON output when requested.',
                  'Oracle arms read GOLD after inference; they are interventions, NOT guaranteed upper bounds or deployable.',
                  'Calibration and evaluation pairs are disjoint; previously used validation data means this is NOT certification.',
                  'No training, mutation, test/challenge use, or production promotion.'])
    return '\n'.join(lines)


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--training-dir',type=Path,default=DEFAULT_TRAINING)
    ap.add_argument('--plan',type=Path,default=DEFAULT_PLAN)
    ap.add_argument('--capture',type=Path,default=DEFAULT_CAPTURE)
    ap.add_argument('--checkpoint-step',type=int,default=4900)
    ap.add_argument('--calibration-pairs',type=int,default=24)
    ap.add_argument('--evaluation-pairs',type=int,default=48)
    ap.add_argument('--cpu-threads',type=int,default=2)
    ap.add_argument('--output',type=Path,help='Optional new JSON diagnostic report (never overwrites)')
    args=ap.parse_args(argv)
    try:
        result=run(training_dir=args.training_dir.resolve(),plan_dir=args.plan.resolve(),
                   capture_dir=args.capture.resolve(),checkpoint_step=args.checkpoint_step,
                   calibration_pairs=args.calibration_pairs,
                   evaluation_pairs=args.evaluation_pairs,cpu_threads=args.cpu_threads)
        print(format_report(result),flush=True)
        if args.output:
            if args.output.exists():raise ValueError('Refusing to overwrite existing report')
            args.output.parent.mkdir(parents=True,exist_ok=True)
            with args.output.open('x',encoding='utf8') as f:
                json.dump(result,f,sort_keys=True,indent=2)
                f.write('\n')
            print('New diagnostic report: '+str(args.output),flush=True)
    except (OSError,ValueError,RuntimeError,AssertionError) as exc:
        ap.exit(2,f'G2_0064_DIAGNOSTIC_ERROR: {exc}; no source or checkpoint files altered.\n')


if __name__=='__main__':
    main()
