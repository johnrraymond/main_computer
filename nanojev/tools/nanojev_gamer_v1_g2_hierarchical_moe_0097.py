"""G2-0097: prior learned heads become measured, routed hierarchical experts.

No expert retraining or promotion. The expert checkpoints from 0095/0096 are
immutable. Fit ONLY a small graph -> pair -> relation gate on NEW 0093 discovery
bases excluded from ALL prior 0095 and 0096 training/development selections.

First measure individual errors and complementary predictions. Compare router to
best fixed head, uniform margins and oracle expert selection. Freeze router
before reading confirmation gold. Previously reused confirmation is a regression
benchmark, never independent certification.

From main_computer/nanojev:
  python -u -m tools.nanojev_gamer_v1_g2_hierarchical_moe_0097 audit
  python -u -m tools.nanojev_gamer_v1_g2_hierarchical_moe_0097 run --device cuda
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import random

import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old
from tools import nanojev_gamer_v1_g2_extended_learning_0096 as exp
from tools import nanojev_gamer_v1_g2_error_atlas_0093 as atlas
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_g2_hard_negative_learning_0087 as hard
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g1_native_composite_0082 as g1native
from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural
from tools.nanojev_gamer_v1_graph_head import SPAN_KINDS, BIND_CLASSES

SCHEMA = 'nanojev.gamer.graph.g2.hierarchical-expert-meta-analysis-0097.v1'
NAME = 'oracle-fabric-g2-hierarchical-expert-meta-analysis-0097'
SEED = 970097
R = len(BIND_CLASSES) - 1
S = len(SPAN_KINDS)
MODES = ('base', 'renaming', 'role_swap', 'added_distractor', 'action_removal')


def require(value, reason):
    if not value:
        raise ValueError('G2_0097_' + reason)


def log(tag, value):
    print(tag + ' ' + json.dumps(value, sort_keys=True, allow_nan=False), flush=True)


def _verify(root):
    root, atlas_dir, commit, parent_dir = old._source(root)
    p95 = root / old.NAME
    p96 = root / exp.NAME
    require((p95 / 'report.json').is_file() and (p95 / 'lock.json').is_file(), '0095_RUN_MISSING')
    require((p96 / 'report.json').is_file() and (p96 / 'lock.json').is_file(), '0096_RUN_MISSING')
    rep95, lock95 = old.read(p95/'report.json'), old.read(p95/'lock.json')
    rep96, lock96 = old.read(p96/'report.json'), old.read(p96/'lock.json')
    require(old.digest(p95/'report.json') == lock95['report_sha256'] and
            old.digest(p96/'report.json') == lock96['report_sha256'], 'PAST_REPORT_DRIFT')
    require(rep95['g3_unlocked'] is False and rep96['g3_unlocked'] is False and
            lock95['g3_unlocked'] is False and lock96['g3_unlocked'] is False,
            'UNEXPECTED_G3_UNLOCK')
    parent_hash = old.digest(parent_dir/cur.HEAD_FILE)
    require(parent_hash == lock95['parent_checkpoint_sha256'] ==
            lock96['parent_checkpoint_sha256'], 'PARENT_DRIFT')
    return root, atlas_dir, commit, parent_dir, (rep95, lock95, p95), (rep96, lock96, p96)


def _expert_registry(hist95, hist96, parent_dir, *, expert_set):
    rep95, lock95, p95 = hist95
    rep96, lock96, p96 = hist96
    names = {}
    # Former controls and prior experimental arms are now genuine candidates.
    for name, record in rep96['models'].items():
        if name.startswith('edited_warm_consistency__'):
            path = p96/name/'checkpoint'/cur.HEAD_FILE
            expected = record['checkpoint_sha256']
        else:
            path = Path(record['best_checkpoint']) / cur.HEAD_FILE
            if not path.is_absolute():
                # The original runner stored its chosen path as given at runtime.
                path = (Path.cwd()/path).resolve()
            expected = record['selected_checkpoint_sha256']
        names['0096__'+name] = (path,expected)
    names['0095__fresh_bootstrap'] = (
        p95/'fresh-bootstrap-checkpoint'/cur.HEAD_FILE,
        lock95['shared_bootstrap_checkpoint_sha256'])
    names['0092__inherited_control'] = (
        parent_dir/cur.HEAD_FILE,lock96['parent_checkpoint_sha256'])
    for name, expected in lock95['checkpoints'].items():
        if expert_set == 'core' and not name.startswith(old.ARM_FRESH+'__'):
            continue
        names['0095__'+name] = (p95/name/'checkpoint'/cur.HEAD_FILE,expected)
    if expert_set == 'core':
        keep = {k for k in names if k.startswith('0096__') or
                k in ('0095__fresh_bootstrap','0092__inherited_control') or
                ('0095__'+old.ARM_FRESH+'__') in k}
        names = {k:v for k,v in names.items() if k in keep}
    output = {}
    for name,(path,expected) in sorted(names.items()):
        require(path.is_file(), 'EXPERT_CHECKPOINT_MISSING_'+name)
        require(old.digest(path) == expected, 'EXPERT_CHECKPOINT_DRIFT_'+name)
        output[name] = {'path':str(path.resolve()),'sha256':expected}
    require(len(output) >= 4, 'TOO_FEW_EXPERTS')
    return output


def _prior_exclusions(p95, p96):
    prov95 = old.read(p95/'provenance.json')
    prov96 = old.read(p96/'provenance.json')
    exclude = set()
    for k in ('train_case_ids','dev_case_ids'):
        # These are case IDs, not base IDs; protect both via case IDs below.
        exclude.update(prov95.get(k,()))
    selection = prov96['selection']
    excluded_bases = set()
    for key in ('train_bases','dev_bases','excluded_0095_dev_bases',
                'retained_0095_train_bases'):
        excluded_bases.update(selection.get(key,()))
    return exclude, excluded_bases


def _select(atlas_dir, p95, p96, train_bases, dev_bases, seed):
    excluded_ids, excluded_bases = _prior_exclusions(p95,p96)
    grouped = defaultdict(dict)
    for case in old.gzlines(atlas_dir/'discovery'/'cases.jsonl.gz'):
        grouped[case['base_id']][case['mode']] = case
    pools = defaultdict(list)
    rows = {}
    for bid, modes in grouped.items():
        if bid in excluded_bases or any(x['case_id'] in excluded_ids for x in modes.values()):
            continue
        if not all(k in modes and modes[k]['g1_exact'] for k in old.NEEDED_MODES):
            continue
        family = modes['base']['family']
        ids = [modes[k]['case_id'] for k in MODES
               if k in modes and modes[k]['g1_exact']]
        pools[family].append(bid)
        rows[bid] = ids
    selection = {'fit_bases':[],'dev_bases':[],'fit_case_ids':[],
                 'dev_case_ids':[],'excluded_previous_base_ids':sorted(excluded_bases),
                 'excluded_previous_case_ids_count':len(excluded_ids),
                 'confirmation_gold_used':False}
    for index, family in enumerate(atlas.FAMILIES):
        candidates = sorted(pools[family])
        random.Random(seed+index).shuffle(candidates)
        require(len(candidates) >= train_bases+dev_bases,
                'INSUFFICIENT_UNUSED_DISCOVERY_'+family+'_HAVE_'+str(len(candidates)))
        fitting = candidates[:train_bases]
        development = candidates[train_bases:train_bases+dev_bases]
        selection['fit_bases'] += fitting
        selection['dev_bases'] += development
        for bid in fitting:
            selection['fit_case_ids'].extend(rows[bid])
        for bid in development:
            selection['dev_case_ids'].extend(rows[bid])
    require(not (set(selection['fit_case_ids']) & set(selection['dev_case_ids'])),
            'ROUTER_FIT_DEV_COLLISION')
    return selection


def _history_audit(hist95, hist96):
    records = {}
    for source,hist in (('0095',hist95),('0096',hist96)):
        confirmation = hist[0]['confirmation']['results']
        for name,v in confirmation.items():
            overall = v['overall']
            by = v['by_family']
            records[source+'__'+name] = {
                'edge_f1':overall['edge_f1'],'exact':overall['exact'],
                'cases':overall['cases'], 'tp':overall['tp'],'fp':overall['fp'],
                'fn':overall['fn'],
                'precision':overall['tp']/max(1,overall['tp']+overall['fp']),
                'recall':overall['tp']/max(1,overall['tp']+overall['fn']),
                'by_family':{f:{'f1':d['edge_f1'],'exact':d['exact'],
                                'tp':d['tp'],'fp':d['fp'],'fn':d['fn']}
                             for f,d in by.items()}}
    return records


class HierarchicalRouter(nn.Module):
    """Gold-blind graph gate -> pair gate -> relation-specific expert gate.

    Family labels and gold targets are never features. Training can supervise
    gates with gold; inference receives only G1 spans and frozen expert scores.
    """
    def __init__(self, expert_count):
        super().__init__()
        self.expert_count = expert_count
        self.graph_gate = nn.Sequential(nn.Linear(1+S+2*R+2,48), nn.Tanh(),
                                        nn.Linear(48,expert_count))
        self.pair_gate = nn.Sequential(nn.Linear(2*S+7,48),nn.Tanh(),
                                       nn.Linear(48,expert_count))
        self.relation_gate = nn.Parameter(torch.zeros(R,expert_count))
        self.log_scales = nn.Parameter(torch.zeros(expert_count))

    def forward(self, objects, expert_margins):
        # expert_margins: [experts, nodes, nodes, non-null classes]
        e,n,m,r = expert_margins.shape
        require(e == self.expert_count and n == m and r == R, 'ROUTER_INPUT_SHAPE')
        device = expert_margins.device
        if n == 0:
            return torch.zeros((0,0,R+1),device=device), None
        type_ids = [SPAN_KINDS.index(obj[2]) for obj in objects]
        kinds = F.one_hot(torch.tensor(type_ids,device=device),S).float()
        histogram = kinds.mean(0)
        starts = torch.tensor([obj[0] for obj in objects],device=device,dtype=torch.float32)
        ends = torch.tensor([obj[1] for obj in objects],device=device,dtype=torch.float32)
        denom = (ends.max()-starts.min()).clamp(min=1.)
        scaled = expert_margins * self.log_scales.clamp(-1.5,1.5).exp()[:,None,None,None]
        rel_means = scaled.mean((0,1,2))
        rel_std = scaled.std((0,1,2),unbiased=False)
        expert_best = scaled.amax(-1)
        diversity = expert_best.std(0,unbiased=False).mean()
        density = (expert_best > 0).float().mean()
        graph_features = torch.cat((torch.log1p(torch.tensor([n],device=device,dtype=torch.float32))/5,
              histogram,rel_means.tanh(),rel_std.tanh(),diversity.view(1).tanh(),
              density.view(1)))
        graph_logits = self.graph_gate(graph_features)
        src = kinds[:,None,:].expand(n,n,S)
        dst = kinds[None,:,:].expand(n,n,S)
        gap = (starts[:,None]-starts[None,:])/denom
        spread = ((ends-starts)[:,None]-(ends-starts)[None,:])/denom
        pair_features = torch.cat((src,dst,gap.unsqueeze(-1),gap.abs().unsqueeze(-1),
            spread.unsqueeze(-1),expert_best.mean(0).unsqueeze(-1).tanh(),
            expert_best.std(0,unbiased=False).unsqueeze(-1).tanh(),
            (expert_best>0).float().mean(0).unsqueeze(-1),
            (ends-starts)[:,None,None].expand(n,n,1)/denom),dim=-1)
        pair_logits = self.pair_gate(pair_features)
        weights = torch.softmax(graph_logits[None,None,None,:] +
             pair_logits[:,:,None,:] + self.relation_gate[None,None,:,:],dim=-1)
        # [n,n,R,E] times [n,n,R,E]. Model outputs margins against null.
        margins = (weights * scaled.permute(1,2,3,0)).sum(-1)
        logits = torch.cat((torch.zeros((n,n,1),device=device),margins),dim=-1)
        return logits, weights



class MarginCalibrator(nn.Module):
    """Equal-data non-MoE control: per-relation scale and threshold shift."""
    def __init__(self):
        super().__init__()
        self.log_scale = nn.Parameter(torch.zeros(R))
        self.bias = nn.Parameter(torch.zeros(R))

    def forward(self, margins):
        n = margins.shape[0]
        score = margins * self.log_scale.clamp(-2,2).exp() + self.bias
        return torch.cat((score.new_zeros((n,n,1)),score),dim=-1)


def _calibrated_logits(rec, calibrator, *, source, experts, device):
    margins=rec['expert_margins']
    if source != 'mean':
        margins=margins[list(experts).index(source)]
    else:
        margins=margins.mean(0)
    return calibrator(margins.to(device))

def _cached_features(head, items, device, spec, experts, *, trace):
    """One checkpoint loaded at a time; one G1 capture reused by all heads."""
    output = {item['row']['case_id']:{'item':item,'expert_margins':[],
              'predictions':{},'source':item['row']['family']} for item in items}
    frozen = old._frozen_hash(head)
    for index,(name,entry) in enumerate(experts.items(),1):
        exp._load_state_to_head(head,entry['path'],frozen_hash=frozen,
                                expected_hash=entry['sha256'])
        head.eval()
        with torch.no_grad():
            for item in items:
                key = item['row']['case_id']
                scores = structural._forward(head,item,device)
                output[key]['expert_margins'].append((scores[...,1:]-scores[...,0:1])
                                                     .float().detach().cpu())
                pred,_ = old._decode_matrix(item,scores,spec)
                output[key]['predictions'][name] = pred
        if index==1 or index==len(experts) or index%max(1,len(experts)//4)==0:
            log('G2_0097_EXPERT_CAPTURE',{'split':trace,'expert':name,'done':index,
                                            'total':len(experts),'cases':len(items)})
    for record in output.values():
        record['expert_margins'] = torch.stack(record['expert_margins'])
        # All expert logits are cached; release large frozen G1 hidden states.
        item = record['item']
        record['item'] = {k:item[k] for k in ('row','objects','targets','full_gold','g1_exact')}
        record['item']['row'] = {k:item['row'][k] for k in ('case_id','base_id','mode','family')}
    return list(output.values())


def _evaluate(records, experts, spec, *, router=None, calibrated=None, device='cpu'):
    controls=list(calibrated) if calibrated else []
    counters = {name:defaultdict(Counter) for name in
                list(experts)+['uniform','hierarchical_moe']+controls}
    pair = Counter()
    exclusive = Counter()
    pairwise = defaultdict(Counter)
    class_stats = defaultdict(Counter)
    oracle_graph = Counter()
    for rec in records:
        item = rec['item']
        family = item['row']['family']
        mode = item['row']['mode']
        gold = item['targets'].cpu()
        n = len(gold)
        valid = ~torch.eye(n,dtype=torch.bool)
        preds = dict(rec['predictions'])
        avg = rec['expert_margins'].mean(0)
        uniform = torch.cat((torch.zeros_like(avg[...,:1]),avg),dim=-1)
        preds['uniform'],_ = old._decode_matrix(item,uniform,spec)
        if router is not None:
            with torch.no_grad():
                scores,_ = router(item['objects'],rec['expert_margins'].to(device))
            preds['hierarchical_moe'],_ = old._decode_matrix(item,scores,spec)
        if calibrated:
            for label,(calibrator,source) in calibrated.items():
                with torch.no_grad():
                    scores=_calibrated_logits(rec,calibrator,source=source,
                                              experts=experts,device=device)
                preds[label],_ = old._decode_matrix(item,scores,spec)
        truth = set(item['full_gold'])
        for name, matrix in preds.items():
            selected = {old.structural.previous.format_edge(item['objects'],i,j,
                        int(matrix[i,j])) for i,j in (matrix>0).nonzero(as_tuple=False).tolist()}
            tp = len(selected & truth)
            fp = len(selected - truth)
            fn = len(truth - selected)
            for bucket in (counters[name]['overall'],counters[name][family],
                           counters[name]['mode:'+mode]):
                bucket.update({'tp':tp,'fp':fp,'fn':fn,'cases':1,
                               'exact':int(selected==truth),
                               'g1_exact':int(item['g1_exact'])})
        matrices = torch.stack([rec['predictions'][k] for k in experts])
        correct = (matrices==gold[None,:,:]) & valid[None,:,:]
        all_correct = correct.any(0)
        oracle_graph['cases']+=1
        oracle_graph['g1_exact_cases']+=int(item['g1_exact'])
        # Pairwise label oracle only upper-bounds FULL graph reconstruction when
        # the frozen G1 object set is complete. Never misstate reachable-only
        # target matching as full-graph success for G1-inexact inputs.
        oracle_graph['pair_select_exact']+=int(item['g1_exact'] and
                                                  bool(all_correct[valid].all()))
        oracle_graph['some_expert_exact']+=int(item['g1_exact'] and any(
            bool(((matrices[i]==gold)|~valid).all()) for i in range(len(experts))))
        relevant = valid & ((gold>0)|((matrices>0).any(0)))
        pair['relevant_positions']+=int(relevant.sum())
        pair['oracle_solved_positions']+=int((all_correct&relevant).sum())
        pair['all_experts_wrong_positions']+=int(((~all_correct)&relevant).sum())
        pair['disagreed_positions']+=int(((matrices!=matrices[:1]).any(0)&relevant).sum())
        for k, name in enumerate(experts):
            unique = correct[k] & (correct.sum(0)==1) & relevant
            exclusive[name]+=int(unique.sum())
            for label in range(1,len(BIND_CLASSES)):
                relevant_gold = valid & (gold==label)
                predicts = valid & (matrices[k]==label)
                cls = class_stats[(name,family,mode,BIND_CLASSES[label])]
                cls['tp']+=int((predicts&relevant_gold).sum())
                cls['fn']+=int(((~predicts)&relevant_gold).sum())
                cls['fp']+=int((predicts&(~relevant_gold)).sum())
            for j,other in enumerate(experts):
                if j == k:
                    continue
                comp = pairwise[(name,other)]
                comp['a_right_b_wrong']+=int((correct[k]&(~correct[j])&relevant).sum())
                comp['b_right_a_wrong']+=int((correct[j]&(~correct[k])&relevant).sum())
                comp['both_right']+=int((correct[j]&correct[k]&relevant).sum())
    def report(count):
        tp,fp,fn = (count[k] for k in ('tp','fp','fn'))
        return {'cases':count['cases'],'edge_f1':2*tp/max(1,2*tp+fp+fn),
                'precision':tp/max(1,tp+fp),'recall':tp/max(1,tp+fn),
                'exact':count['exact'],'tp':tp,'fp':fp,'fn':fn,
                'g1_exact':count['g1_exact']}
    scores = {}
    for name, groups in counters.items():
        if groups['overall']['cases']:
            scores[name] = {'overall':report(groups['overall']),
                            'by_family':{f:report(groups[f]) for f in atlas.FAMILIES},
                            'by_fuzz_mode':{mode:report(groups['mode:'+mode])
                                            for mode in MODES}}
    return {'models':scores,'oracle':dict(oracle_graph),
        'complementarity':{**dict(pair),'exclusive_right_on_relevant_positions':dict(exclusive),
            'pairwise':{'|'.join(k):dict(v) for k,v in pairwise.items()}},
        'relation_errors_by_family_and_fuzz':{'|'.join(k):dict(v) for k,v in class_stats.items()}}



def _merge_evaluations(parts):
    """Sum exact integer confusion counts; never average subgroup F1 values."""
    if len(parts)==1:
        return parts[0]
    pooled = defaultdict(lambda: defaultdict(Counter))
    oracle = Counter()
    complements = Counter()
    exclusive = Counter()
    pairwise = defaultdict(Counter)
    relations = defaultdict(Counter)
    for part in parts:
        for name, groups in part['models'].items():
            for group in ('overall','by_family','by_fuzz_mode'):
                if group=='overall':
                    records={'overall':groups['overall']}
                else:
                    records=groups[group]
                for key,row in records.items():
                    pooled[name][group+'|'+key].update({k:row[k] for k in
                        ('cases','exact','tp','fp','fn','g1_exact')})
        oracle.update(part['oracle'])
        complements.update({k:part['complementarity'][k] for k in (
            'relevant_positions','oracle_solved_positions',
            'all_experts_wrong_positions','disagreed_positions')})
        exclusive.update(part['complementarity']['exclusive_right_on_relevant_positions'])
        for key,rec in part['complementarity']['pairwise'].items():
            pairwise[key].update(rec)
        for key,rec in part['relation_errors_by_family_and_fuzz'].items():
            relations[key].update(rec)
    def metric(c):
        tp,fp,fn=c['tp'],c['fp'],c['fn']
        return {'cases':c['cases'],'exact':c['exact'],'tp':tp,'fp':fp,'fn':fn,
            'g1_exact':c['g1_exact'],
            'edge_f1':2*tp/max(1,2*tp+fp+fn),
            'precision':tp/max(1,tp+fp),'recall':tp/max(1,tp+fn)}
    models={}
    for name, groups in pooled.items():
        models[name]={'overall':metric(groups['overall|overall']),
          'by_family':{f:metric(groups['by_family|'+f]) for f in atlas.FAMILIES},
          'by_fuzz_mode':{m:metric(groups['by_fuzz_mode|'+m]) for m in MODES}}
    return {'models':models,'oracle':dict(oracle),
        'complementarity':{**dict(complements),
            'exclusive_right_on_relevant_positions':dict(exclusive),
            'pairwise':{k:dict(v) for k,v in pairwise.items()}},
        'relation_errors_by_family_and_fuzz':{k:dict(v) for k,v in relations.items()}}

def _router_train(router, fit, dev, experts, spec, cfg, dest, device):
    rng = random.Random(cfg.seed)
    optimizer = torch.optim.AdamW(router.parameters(),lr=cfg.lr,weight_decay=.0001)
    best, best_state, history = None,None,[]
    router.train()
    for step in range(1,cfg.steps+1):
        optimizer.zero_grad(set_to_none=True)
        cases = rng.sample(fit,k=min(cfg.batch,len(fit)))
        losses = []
        for rec in cases:
            item = rec['item']
            scores,weights = router(item['objects'],rec['expert_margins'].to(device))
            if not len(item['objects']):
                continue
            ce = exp._example_loss(scores,item,cfg.positive_cap)
            if cfg.hinge_weight:
                hinge,_ = structural.graph_set_hinge(scores,item['targets'].to(device),
                                                       item['objects'],spec)
                ce = ce + cfg.hinge_weight*hinge
            # Discourage total expert collapse without forcing uniform per-point routing.
            mean_gate = weights.mean((0,1,2))
            balance = (mean_gate * (mean_gate.clamp_min(1e-9).log()+
                                    math.log(len(experts)))).sum()
            losses.append(ce + cfg.balance_weight*balance)
        require(losses,'NO_ROUTER_TRAINING_CASES')
        objective = torch.stack(losses).mean()
        require(bool(torch.isfinite(objective).item()),'NONFINITE_ROUTER_LOSS')
        objective.backward()
        nn.utils.clip_grad_norm_(router.parameters(),1.)
        optimizer.step()
        if step == 1 or step%cfg.eval_every == 0 or step==cfg.steps:
            router.eval()
            result = _evaluate(dev,experts,spec,router=router,device=device)
            metric = result['models']['hierarchical_moe']['overall']
            quality = (metric['exact'],metric['edge_f1']) if cfg.select_exact_first else (
                       metric['edge_f1'],metric['exact'])
            improved = best is None or quality>best
            if improved:
                best = quality
                best_state = {k:v.detach().cpu().clone() for k,v in router.state_dict().items()}
            history.append({'step':step,'loss':float(objective.detach()),
                            'dev_edge_f1':metric['edge_f1'],'dev_exact':metric['exact'],
                            'selected':improved})
            log('G2_0097_ROUTER',history[-1])
            router.train()
    require(best_state is not None,'NO_SELECTED_ROUTER')
    router.load_state_dict(best_state,strict=True)
    router.eval()
    save_file({k:v.cpu().contiguous() for k,v in best_state.items()},
              str(dest/'router.safetensors'))
    with (dest/'router-history.jsonl').open('x',encoding='utf-8') as fp:
        for row in history:
            fp.write(json.dumps(row,sort_keys=True)+'\n')
    return {'selection_metric':'exact_then_f1' if cfg.select_exact_first else 'f1_then_exact',
            'best':best,'selected_step':next(r['step'] for r in reversed(history) if r['selected']),
            'router_sha256':old.digest(dest/'router.safetensors'),
            'router_history_sha256':old.digest(dest/'router-history.jsonl')}



def _train_calibrator(calibrator, fit, dev, experts, spec, cfg, dest, device, *, source, name):
    """Control for mere scale/threshold differences among existing heads."""
    calibrator.train()
    optimizer = torch.optim.AdamW(calibrator.parameters(),lr=.01)
    rng = random.Random(cfg.seed+11)
    best = None
    best_state = None
    for step in range(1,cfg.calibration_steps+1):
        optimizer.zero_grad(set_to_none=True)
        selected=rng.sample(fit,k=min(cfg.batch,len(fit)))
        losses=[]
        for rec in selected:
            scores=_calibrated_logits(rec,calibrator,source=source,experts=experts,device=device)
            if scores.shape[0]:
                losses.append(exp._example_loss(scores,rec['item'],cfg.positive_cap))
        require(losses,'CALIBRATION_NO_CASES')
        loss=torch.stack(losses).mean()
        loss.backward()
        optimizer.step()
        if step==1 or step%cfg.eval_every==0 or step==cfg.calibration_steps:
            metrics=_evaluate(dev,experts,spec,calibrated={name:(calibrator,source)},
                              device=device)['models'][name]['overall']
            quality=(metrics['exact'],metrics['edge_f1']) if cfg.select_exact_first else (
                     metrics['edge_f1'],metrics['exact'])
            if best is None or quality>best:
                best=quality
                best_state={k:v.detach().cpu().clone() for k,v in calibrator.state_dict().items()}
    require(best_state is not None,'CALIBRATION_NO_SELECTION')
    calibrator.load_state_dict(best_state,strict=True)
    calibrator.eval()
    checkpoint=dest/(name+'.safetensors')
    save_file({k:v.contiguous() for k,v in best_state.items()},str(checkpoint))
    result={'control':name,'source':source,'dev_quality':best,
            'checkpoint_sha256':old.digest(checkpoint),'steps':cfg.calibration_steps}
    log('G2_0097_CALIBRATION',result)
    return result

def _read_confirmation(atlas_dir, count, *, seed):
    if not count:
        return []
    ids = defaultdict(list)
    for row in old.lines(atlas_dir/'seal'/'prompts.jsonl'):
        if row['split']=='confirmation':
            ids[row['base_id']].append(row['case_id'])
    chosen = sorted(ids)
    require(count<=len(chosen),'EXCESS_CONFIRMATION_BASES')
    if count < len(chosen):
        random.Random(seed+990).shuffle(chosen)
        chosen = chosen[:count]
    return old._get_rows(atlas_dir,{cid for bid in chosen for cid in ids[bid]},split='confirmation')


def run(*,root=old.ROOT_DEFAULT,output=None,device='cuda',expert_set='all',
        fit_bases=40,dev_bases=20,steps=600,eval_every=50,batch=4,
        lr=.002,positive_cap=12.,hinge_weight=.05,balance_weight=.005,
        calibration_steps=300,confirmation_bases=300,seed=SEED,select_exact_first=True):
    require(expert_set in ('all','core') and fit_bases>0 and dev_bases>0 and
            1<=steps<=10000 and 1<=eval_every<=steps and 1<=batch<=32 and
            0<lr<.1 and 0<=hinge_weight<=10 and 0<=balance_weight<=10 and
            0<=confirmation_bases<=300 and 1<=positive_cap<=100 and
            1<=calibration_steps<=10000,
            'INVALID_CONFIGURATION')
    require(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),
            'REQUESTED_DEVICE_NOT_AVAILABLE')
    cfg=argparse.Namespace(root=root,output=output,device=device,expert_set=expert_set,
            fit_bases=fit_bases,dev_bases=dev_bases,steps=steps,eval_every=eval_every,
            batch=batch,lr=lr,positive_cap=positive_cap,hinge_weight=hinge_weight,
            balance_weight=balance_weight,calibration_steps=calibration_steps,
            confirmation_bases=confirmation_bases,
            seed=seed,select_exact_first=select_exact_first)
    root, atlas_dir, commit, parent_dir, hist95, hist96 = _verify(root)
    registry = _expert_registry(hist95,hist96,parent_dir,expert_set=expert_set)
    selection = _select(atlas_dir,hist95[2],hist96[2],fit_bases,dev_bases,seed)
    dest = (Path(output) if output else root/NAME).resolve()
    require(not dest.exists() and dest.parent.is_dir(),'OUTPUT_EXISTS_OR_PARENT_MISSING')
    dest.mkdir(exist_ok=False)
    prior = _history_audit(hist95,hist96)
    old.write(dest/'provenance.json',{'schema':SCHEMA,'runner_sha256':old.digest(__file__),
        'prior_0095_report_sha256':old.digest(hist95[2]/'report.json'),
        'prior_0096_report_sha256':old.digest(hist96[2]/'report.json'),
        'prior_0093_commit_sha256':old.digest(atlas_dir/'seal'/'commit.json'),
        'experts':registry,'selection':selection,'settings':vars(cfg),
        'route_input_uses_gold_family':False,'expert_weights_frozen':True,
        'confirmation_not_independent':True,'g3_unlocked':False})
    old.write(dest/'prior-meta-analysis.json',prior)
    log('G2_0097_PREPARED',{'experts':len(registry),
        'fit_cases':len(selection['fit_case_ids']),
        'dev_cases':len(selection['dev_case_ids']),
        'independent_router_discovery':True})
    torch.manual_seed(seed)
    paths = atlas._roots(root)
    parent_rec,_ = hard._verify_trained(paths,paths['parent_0087'])
    model,tok = backbone.model_components(parent_rec['model_id'],paths['adapter'],device)
    require(all(not p.requires_grad for p in model.parameters()), 'G1_NOT_FROZEN')
    composite = g1native.load_composite(paths['composite'],device=device)
    reference,_ = cur._load_checkpoint(parent_dir,device=device)
    old._mutable(reference)
    frozen = old._frozen_hash(reference)
    spec=commit['lineage']['decoder']
    fit_rows=old._get_rows(atlas_dir,set(selection['fit_case_ids']),split='discovery')
    dev_rows=old._get_rows(atlas_dir,set(selection['dev_case_ids']),split='discovery')
    fit_items=old._capture(model,tok,composite,fit_rows,device,must_exact=True)
    dev_items=old._capture(model,tok,composite,dev_rows,device,must_exact=True)
    fit=_cached_features(reference,fit_items,device,spec,registry,trace='fit')
    dev=_cached_features(reference,dev_items,device,spec,registry,trace='dev')
    fit_meta=_evaluate(fit,registry,spec)
    dev_meta=_evaluate(dev,registry,spec)
    old.write(dest/'fit-expert-analysis.json',fit_meta)
    old.write(dest/'dev-expert-analysis.json',dev_meta)
    log('G2_0097_COMPLEMENTARITY',{'fit':{k:v for k,v in
        fit_meta['complementarity'].items() if k not in ('pairwise',)},
        'dev':{k:v for k,v in dev_meta['complementarity'].items()
               if k not in ('pairwise',)},
        'fit_oracle':fit_meta['oracle'],'dev_oracle':dev_meta['oracle'],
        'pairwise_details_saved':True})
    # Never select a reference expert using the previously reused confirmation.
    # Historical outcomes are descriptive; discovery-only fit scores choose controls.
    best_historical = max(registry,key=lambda n:
                          fit_meta['models'][n]['overall']['edge_f1'])
    calibrators = {
        'calibrated_uniform':(MarginCalibrator().to(device),'mean'),
        'calibrated_best_single':(MarginCalibrator().to(device),best_historical),
    }
    controls = {name:_train_calibrator(model,fit,dev,registry,spec,cfg,dest,device,
                    source=source,name=name) for name,(model,source) in calibrators.items()}
    router=HierarchicalRouter(len(registry)).to(device)
    training=_router_train(router,fit,dev,registry,spec,cfg,dest,device)
    dev_result=_evaluate(dev,registry,spec,router=router,
                         calibrated=calibrators,device=device)
    old.write(dest/'dev-routing-analysis.json',dev_result)
    # Lock the learned router BEFORE any confirmation answers are accessed.
    old.write(dest/'frozen-before-confirmation.json',{'schema':SCHEMA,
        'router_sha256':training['router_sha256'],
        'expert_sha256':{k:v['sha256'] for k,v in registry.items()},
        'calibrator_sha256':{name:r['checkpoint_sha256'] for name,r in controls.items()},
        'dev_score':dev_result['models']['hierarchical_moe']['overall'],
        'g3_unlocked':False})
    confirm=None
    if confirmation_bases:
        rows=_read_confirmation(atlas_dir,confirmation_bases,seed=seed)
        # Bounded confirmation processing. Preserve only integer aggregate
        # confusion counts after each independent chunk, never G1 hidden states.
        evaluations=[]
        for offset in range(0,len(rows),50):
            chunk=rows[offset:offset+50]
            items=[old._capture_quiet(model,tok,composite,row,device) for row in chunk]
            chunk_records=_cached_features(reference,items,device,spec,registry,
                                           trace='confirmation-'+str(offset//50+1))
            evaluations.append(_evaluate(chunk_records,registry,spec,
                    router=router,calibrated=calibrators,device=device))
            log('G2_0097_CONFIRM',{'done':min(offset+50,len(rows)),'of':len(rows)})
            del chunk_records,items
        confirm=_merge_evaluations(evaluations)
        old.write(dest/'confirmation-regression.json',confirm)
    report={'schema':SCHEMA,'status':'G2_0097_HIERARCHICAL_MOE_RESEARCH_ONLY',
            'historical':prior,'experts':registry,'selection':selection,
            'fit':fit_meta,'dev':dev_result,'router':training,
            'calibration_controls':controls,'confirmation':confirm,
            'parent_unchanged':old.digest(parent_dir/cur.HEAD_FILE)==
                     hist96[1]['parent_checkpoint_sha256'],
            'g3_unlocked':False,'production_promotion':False,
            'independent_certification':False}
    require(report['parent_unchanged'],'PARENT_MUTATED')
    old.write(dest/'report.json',report)
    old.write(dest/'lock.json',{'schema':SCHEMA,'report_sha256':old.digest(dest/'report.json'),
        'router_sha256':training['router_sha256'],
        'provenance_sha256':old.digest(dest/'provenance.json'),
        'g3_unlocked':False})
    log('G2_0097_RESULT',{'status':report['status'],
        'development':dev_result['models']['hierarchical_moe']['overall'],
        'confirmation':confirm['models']['hierarchical_moe']['overall'] if confirm else None,
        'oracle':confirm['oracle'] if confirm else dev_result['oracle'],
        'expert_count':len(registry),'g3_unlocked':False})
    return report


def audit(*,root=old.ROOT_DEFAULT):
    root, atlas_dir, commit, parent_dir, h95,h96=_verify(root)
    reports=_history_audit(h95,h96)
    for name,row in sorted(reports.items(),key=lambda kv:-kv[1]['edge_f1']):
        log('G2_0097_PRIOR_EXPERT',{'name':name,'edge_f1':round(row['edge_f1'],5),
             'precision':round(row['precision'],5),'recall':round(row['recall'],5),
             'exact':row['exact'],
             'allocation_f1':round(row['by_family']['allocation']['f1'],5),
             'encounter_f1':round(row['by_family']['encounter']['f1'],5)})
    log('G2_0097_AUDIT',{'heads':len(reports),
        'pairwise_oracle_not_in_aggregate_reports':True,
        'next_step':'run for per-case complementarity and learned routing',
        'certification':False})
    return reports


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',nargs='?',default='run',choices=('run','audit'))
    parser.add_argument('--root',default=old.ROOT_DEFAULT)
    parser.add_argument('--output')
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--expert-set',choices=('core','all'),default='all')
    parser.add_argument('--fit-bases',type=int,default=40)
    parser.add_argument('--dev-bases',type=int,default=20)
    parser.add_argument('--steps',type=int,default=600)
    parser.add_argument('--eval-every',type=int,default=50)
    parser.add_argument('--batch',type=int,default=4)
    parser.add_argument('--lr',type=float,default=.002)
    parser.add_argument('--positive-cap',type=float,default=12.)
    parser.add_argument('--hinge-weight',type=float,default=.05)
    parser.add_argument('--balance-weight',type=float,default=.005)
    parser.add_argument('--confirmation-bases',type=int,default=300)
    parser.add_argument('--calibration-steps',type=int,default=300)
    parser.add_argument('--seed',type=int,default=SEED)
    parser.add_argument('--select-f1-first',action='store_true')
    args=vars(parser.parse_args(argv))
    command=args.pop('command')
    if command=='audit':
        return audit(root=args['root'])
    args['select_exact_first']=not args.pop('select_f1_first')
    return run(**args)


if __name__=='__main__':
    main()
