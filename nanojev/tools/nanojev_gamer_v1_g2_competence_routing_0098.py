"""G2-0098: recover complementary G2 expert knowledge via learned competence.

The G2-0097 hierarchy demonstrated actual expert complementarity but only 69
exact confirmation graphs versus a 177-graph, gold-aware per-pair oracle.
This experiment trains on previously UNUSED G2-0093 discovery bases, holds all
prior G1/G2/expert checkpoints frozen, and learns to predict which experts are
correct *at disagreements*. If historical experiments have consumed part of
that discovery pool, balanced fit/dev quotas shrink without reusing cases. It compares conservative conditional substitution
against competence-weighted fusion and the strongest unchanged expert.

Correct answers / stable authored IDs are used ONLY for training losses and
scoring. Inference sees G1 objects, frozen scores, frozen structured predictions,
and learned competence. Global output always uses the existing joint decoder.
Reused confirmation is for regression only, NEVER independent certification.

  python -u -m tools.nanojev_gamer_v1_g2_competence_routing_0098 audit
  python -u -m tools.nanojev_gamer_v1_g2_competence_routing_0098 run --device cuda
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import json
import math
import random

import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_hierarchical_moe_0097 as prev
from tools import nanojev_gamer_v1_g2_extended_learning_0096 as exp
from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old
from tools import nanojev_gamer_v1_g2_error_atlas_0093 as atlas
from tools import nanojev_gamer_v1_g2_hard_negative_learning_0087 as hard
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g1_native_composite_0082 as g1native
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural
from tools.nanojev_gamer_v1_graph_head import SPAN_KINDS, BIND_CLASSES

SCHEMA = 'nanojev.gamer.graph.g2.competence-aware-correction-0098.v1'
NAME = 'oracle-fabric-g2-competence-aware-correction-0098'
SEED = 980098
S = len(SPAN_KINDS)
R = len(BIND_CLASSES) - 1
MODES = prev.MODES
ANCHOR = '0096__warm_supervised'
MANDATORY = (ANCHOR, '0096__warm_consistency',
             '0096__inherited_consistency', '0096__champion_consistency')


def require(ok, reason):
    if not ok:
        raise ValueError('G2_0098_' + reason)


def log(label, value):
    print(label + ' ' + json.dumps(value, sort_keys=True, allow_nan=False), flush=True)


def _sources(root):
    root, atlas_dir, commit, parent_dir, h95, h96 = prev._verify(root)
    p97 = root / prev.NAME
    require((p97/'report.json').is_file() and (p97/'lock.json').is_file() and
            (p97/'provenance.json').is_file(), '0097_RESULT_REQUIRED')
    lock = old.read(p97/'lock.json')
    require(old.digest(p97/'report.json') == lock['report_sha256'] and
            old.digest(p97/'provenance.json') == lock['provenance_sha256'],
            '0097_PROVENANCE_DRIFT')
    report = old.read(p97/'report.json')
    require(report['status'] == 'G2_0097_HIERARCHICAL_MOE_RESEARCH_ONLY' and
            report['g3_unlocked'] is False and report['parent_unchanged'],
            '0097_NOT_FROZEN_RESEARCH')
    for name,entry in report['experts'].items():
        require(old.digest(Path(entry['path'])) == entry['sha256'],
                'EXPERT_DRIFT_'+name)
    return root, atlas_dir, commit, parent_dir, h95, h96, p97, report


def _choose_experts(report, policy, limit):
    registry = report['experts']
    require(ANCHOR in registry and all(k in registry for k in MANDATORY),
            'MANDATORY_EXPERT_MISSING')
    if policy == 'all':
        ordered = [ANCHOR] + [k for k in registry if k != ANCHOR]
    else:
        unique = report['dev']['complementarity']['exclusive_right_on_relevant_positions']
        # The old development set helps identify candidates; the NEW split is
        # disjoint, so no answer from the new dev or confirmation is used here.
        ordered = list(MANDATORY)
        ranked = sorted(registry, key=lambda k:(-unique.get(k,0), k))
        ordered += [k for k in ranked if k not in ordered]
        ordered = ordered[:limit]
    require(len(ordered) >= 4 and len(set(ordered)) == len(ordered),
            'EXPERT_SELECTION_INVALID')
    return {k:registry[k] for k in ordered}


def _new_selection(atlas_dir, h95, h96, previous, fit_bases, dev_bases, seed):
    excluded_ids, excluded_bases = prev._prior_exclusions(h95[2],h96[2])
    psel = previous['selection']
    excluded_ids |= set(psel['fit_case_ids']) | set(psel['dev_case_ids'])
    excluded_bases |= set(psel['fit_bases']) | set(psel['dev_bases'])
    grouped = defaultdict(dict)
    for case in old.gzlines(atlas_dir/'discovery'/'cases.jsonl.gz'):
        grouped[case['base_id']][case['mode']] = case
    pools, cases = defaultdict(list), {}
    for base, modes in grouped.items():
        if base in excluded_bases or any(m['case_id'] in excluded_ids
                                           for m in modes.values()):
            continue
        if not all(k in modes and modes[k]['g1_exact'] for k in old.NEEDED_MODES):
            continue
        family = modes['base']['family']
        pools[family].append(base)
        cases[base] = [modes[k]['case_id'] for k in MODES
                       if k in modes and modes[k]['g1_exact']]
    # Fit/development quotas are upper bounds, not a reason to recycle bases
    # consumed by earlier experts or the preceding 0097 router. Keep the same
    # family-balanced split, shrinking BOTH families before any random draw.
    requested_total = fit_bases + dev_bases
    eligible = {family:len(pools[family]) for family in atlas.FAMILIES}
    available = min(eligible.values())
    usable = min(requested_total, available)
    # An automatic reduction must not silently turn the scientific experiment
    # into a handful of examples. Explicit small quotas remain possible.
    minimum_usable = min(requested_total, 30)
    require(usable >= minimum_usable,
            'INSUFFICIENT_UNUSED_DISCOVERY_MINIMUM_'+str(minimum_usable)
            +'_AVAILABLE_'+str(eligible))
    actual_fit = (usable * fit_bases) // requested_total
    actual_dev = usable - actual_fit
    require(actual_fit > 0 and actual_dev > 0,
            'UNUSABLE_FIT_DEV_RATIO_'+str((actual_fit,actual_dev)))
    selection = {'fit_bases':[], 'dev_bases':[], 'fit_case_ids':[],
                  'dev_case_ids':[], 'unused_from_0097':True,
                  'excluded_previous_bases':len(excluded_bases),
                  'excluded_previous_case_ids':len(excluded_ids),
                  'confirmation_gold_used':False,
                  'selection_policy':'balanced_eligible_discovery_only',
                  'eligible_unused_by_family':eligible,
                  'requested_bases_per_family':{'fit':fit_bases,'dev':dev_bases},
                  'selected_bases_per_family':{'fit':actual_fit,'dev':actual_dev},
                  'automatically_reduced':usable < requested_total}
    log('G2_0098_DISCOVERY_SELECTION', {
        'eligible_unused_by_family':eligible,
        'requested_fit_per_family':fit_bases,
        'requested_dev_per_family':dev_bases,
        'selected_fit_per_family':actual_fit,
        'selected_dev_per_family':actual_dev,
        'reused_previous_bases':False,
        'reduced':usable < requested_total})
    for i,family in enumerate(atlas.FAMILIES):
        candidates = sorted(pools[family])
        random.Random(seed+i).shuffle(candidates)
        for key,base_list in (('fit',candidates[:actual_fit]),
                               ('dev',candidates[actual_fit:actual_fit+actual_dev])):
            selection[key+'_bases'] += base_list
            for base in base_list:
                selection[key+'_case_ids'] += cases[base]
    require(not set(selection['fit_bases']) & set(selection['dev_bases']) and
            not set(selection['fit_case_ids']) & set(selection['dev_case_ids']),
            'FIT_DEV_OVERLAP')
    return selection


def _identity_map(item):
    # Only used when constructing training-time fuzz alignment; do not pass
    # stable author node IDs to the competence router.
    return exp._identity(item)


def _capture_experts(head, items, device, spec, registry, label, *, training=False):
    # Gold-backed identity metadata is permitted on FIT discovery cases only.
    # Dev / confirmation inference never constructs that metadata.
    identity = ({item['row']['case_id']:_identity_map(item) for item in items}
                if training else {})
    records = prev._cached_features(head,items,device,spec,registry,trace=label)
    for r in records:
        r['identity'] = identity.get(r['item']['row']['case_id'],{})
    return records


class CompetenceRouter(nn.Module):
    """Contextual per-expert correctness estimator; independent of gold labels.

    Graph and pair trunks form the hierarchy; expert and predicted-relation
    embeddings resolve disagreements. Each expert gets a calibrated competence
    logit; no gold labels or authored node identity ever enter forward().
    """
    def __init__(self, experts):
        super().__init__()
        self.experts = experts
        self.graph = nn.Sequential(nn.Linear(S+4,32),nn.Tanh(),nn.Linear(32,32),nn.Tanh())
        self.pair = nn.Sequential(nn.Linear(2*S+8,32),nn.Tanh(),nn.Linear(32,32),nn.Tanh())
        self.expert_embed = nn.Embedding(experts,12)
        self.relation_embed = nn.Embedding(R+1,12)
        self.competence = nn.Sequential(nn.Linear(32+32+12+12+10,64),nn.Tanh(),
                                         nn.Linear(64,32),nn.Tanh(),nn.Linear(32,1))
        self.expert_bias = nn.Parameter(torch.zeros(experts))
        self.vote_scale = nn.Parameter(torch.tensor(0.5))
        self.relation_bias = nn.Parameter(torch.zeros(R))
        self.blend = nn.Parameter(torch.tensor(-0.5))

    def forward(self, objects, margins, predictions):
        # margins [E,N,N,R], predictions from original JOINT decoder [E,N,N].
        e,n,m,r = margins.shape
        require(e == self.experts and n == m and r == R and
                predictions.shape == (e,n,n), 'COMPETENCE_INPUT_SHAPE')
        device = margins.device
        if not n:
            return (margins.new_zeros((0,0,R+1)),
                    margins.new_zeros((e,0,0)),
                    {'weights':margins.new_zeros((e,0,0)),
                     'disagreement':torch.zeros((0,0),dtype=torch.bool,device=device),
                     'vote':margins.new_zeros((0,0,R+1))})
        ids = torch.tensor([SPAN_KINDS.index(o[2]) for o in objects],device=device)
        kinds = F.one_hot(ids,S).to(margins.dtype)
        start = torch.tensor([o[0] for o in objects],device=device,dtype=margins.dtype)
        end = torch.tensor([o[1] for o in objects],device=device,dtype=margins.dtype)
        span = (end-start).clamp_min(0)
        length = (end.max()-start.min()).clamp_min(1)
        top = margins.amax(-1).tanh()                # [E,N,N]
        expert_agreement = (predictions==predictions[:1]).float().mean(0)
        density = (predictions>0).float().mean()
        disagreement = (predictions!=predictions[:1]).any(0)
        graph_features = torch.cat((kinds.mean(0),
             torch.log1p(length).view(1)/8,
             torch.log1p(torch.tensor(float(n),device=device)).view(1)/5,
             density.view(1),
             top.std(0,unbiased=False).mean().view(1)))
        g = self.graph(graph_features)
        left = kinds[:,None,:].expand(n,n,S)
        right = kinds[None,:,:].expand(n,n,S)
        gap = (start[:,None]-start[None,:])/length
        width = (span[:,None]-span[None,:])/length
        rate = (predictions>0).float().mean(0)
        diversity = predictions.float().std(0,unbiased=False)/max(1,R)
        pair_features = torch.cat((left,right,
            gap[...,None],gap.abs()[...,None],width[...,None],
            (span[:,None]/length).expand(n,n)[...,None],
            (span[None,:]/length).expand(n,n)[...,None],
            rate[...,None],diversity[...,None],
            expert_agreement[...,None]),dim=-1)
        p = self.pair(pair_features)
        logits = torch.cat((margins.new_zeros((e,n,n,1)),margins),-1)
        probs = logits.softmax(-1)
        predicted_prob = probs.gather(-1,predictions[...,None]).squeeze(-1)
        top_two = logits.topk(min(2,R+1),dim=-1).values
        lead = top_two[...,0]-top_two[...,1]
        predicted_margin = logits.gather(-1,predictions[...,None]).squeeze(-1)
        expert_features = torch.stack((
            predicted_prob,
            lead.tanh(),
            predicted_margin.tanh(),
            top,
            (top-top.mean(0,keepdim=True)).tanh(),
            (predictions>0).float(),
            (predictions==predictions[:1]).float(),
            (predictions==predictions.mode(0).values[None]).float(),
            probs[...,0],
            margins.std(-1,unbiased=False).tanh()),dim=-1)
        emb = self.expert_embed.weight[:,None,None,:].expand(e,n,n,12)
        rel = self.relation_embed(predictions)
        graph = g[None,None,None,:].expand(e,n,n,32)
        pair = p[None,:,:,:].expand(e,n,n,32)
        concat = torch.cat((graph,pair,emb,rel,expert_features),dim=-1)
        competence = self.competence(concat).squeeze(-1) + self.expert_bias[:,None,None]
        weights = competence.softmax(0)
        # Route frozen *margins*, not raw logits with incompatible null scales.
        routed = (weights[...,None]*margins).sum(0)
        vote = (weights[...,None]*F.one_hot(predictions,R+1)).sum(0)
        vote_shift = vote[...,1:]-vote[...,0:1]
        anchor = margins[0]
        blend = self.blend.sigmoid()
        correction = blend*(routed-anchor) + self.vote_scale.clamp(-2,2)*vote_shift
        correction = correction + self.relation_bias.clamp(-2,2)
        scores = anchor + torch.where(disagreement[...,None],correction,
                                       torch.zeros_like(correction))
        scores = torch.cat((scores.new_zeros((n,n,1)),scores),-1)
        return scores, competence, {'weights':weights,'disagreement':disagreement,
                                     'vote':vote}


def _predict_inputs(rec, experts, device):
    margins = rec['expert_margins'].to(device)
    pred = torch.stack([rec['predictions'][k] for k in experts]).to(device)
    return margins,pred


def _loss_for_case(model,rec,experts,spec,device,positive_cap,hinge_weight,with_hinge):
    item = rec['item']
    margins, pred = _predict_inputs(rec,experts,device)
    scores, competence, meta = model(item['objects'],margins,pred)
    gold = item['targets'].to(device)
    n = len(gold)
    valid = ~torch.eye(n,dtype=torch.bool,device=device)
    relevant = valid & ((gold>0)|(pred>0).any(0))
    disagreement = meta['disagreement'] & relevant
    correct = (pred==gold[None]).float()
    # A wrong-high-margin head cannot dominate merely because of its margin.
    # Train competence at all hard/relevant positions and directly imitate
    # any existing correct expert when heads disagree.
    weight = (1.+3.*(gold>0).float())*relevant.float()
    denom = weight.sum().clamp_min(1.)
    calibration = (F.binary_cross_entropy_with_logits(
         competence,correct,reduction='none')*weight[None]).sum() / (
             denom*len(experts))
    teacher = correct*disagreement[None]
    available = teacher.sum(0)>0
    teacher = teacher/teacher.sum(0,keepdim=True).clamp_min(1.)
    teacher_loss = -(teacher*competence.log_softmax(0)).sum(0)
    focus = (1.+3.*(gold>0).float())*available.float()
    selection_loss = (teacher_loss*focus).sum()/focus.sum().clamp_min(1.)
    output_loss = exp._example_loss(scores,item,positive_cap)
    objective = .4*calibration+.7*selection_loss+output_loss
    if with_hinge and hinge_weight:
        hinge,_ = structural.graph_set_hinge(scores,gold,item['objects'],spec)
        objective = objective + hinge_weight*hinge
    return objective,competence,{
        'calibration':float(calibration.detach()),
        'selection':float(selection_loss.detach()),
        'output':float(output_loss.detach()),
        'disagreement_positions':int(disagreement.sum()),
        'correctable_positions':int(available.sum())}


def _fuzz_loss(rec_a,ca,rec_b,cb,device,max_negs=12):
    """Same author-ID pair, same label across variants: agree about competence.

    Stable authored IDs and gold equality only construct the *loss*. They are
    never provided to forward(), routing, or confirmation inference.
    """
    a,b = rec_a['identity'],rec_b['identity']
    shared = sorted(set(a)&set(b))
    chosen = []
    negatives=[]
    for x in shared:
        for y in shared:
            if x==y: continue
            ia,ja = a[x],a[y]
            ib,jb = b[x],b[y]
            ga = int(rec_a['item']['targets'][ia,ja])
            gb = int(rec_b['item']['targets'][ib,jb])
            if ga != gb: continue
            entry = ((ia,ja),(ib,jb))
            if ga:
                chosen.append(entry)
            else:
                negatives.append(entry)
    # Stable ordering: deterministic, bounded backgrounds, no gold input.
    chosen += negatives[:max_negs]
    if not chosen:
        return ca.sum()*0,0
    ai,aj = zip(*(p for p,_ in chosen))
    bi,bj = zip(*(p for _,p in chosen))
    # Penalize inconsistent expertise *rankings*, not frozen expert scores.
    pa = ca[:,ai,aj].softmax(0)
    pb = cb[:,bi,bj].softmax(0)
    return F.smooth_l1_loss(pa,pb),len(chosen)


def _count(rec,pred):
    item=rec['item']
    truth=set(item['full_gold'])
    edges={structural.previous.format_edge(item['objects'],i,j,int(pred[i,j]))
           for i,j in (pred>0).nonzero(as_tuple=False).tolist()}
    return Counter(tp=len(edges&truth),fp=len(edges-truth),fn=len(truth-edges),
                   cases=1,exact=int(edges==truth),g1_exact=int(item['g1_exact']))


def _metric(c):
    tp,fp,fn=c['tp'],c['fp'],c['fn']
    return dict(cases=c['cases'],exact=c['exact'],tp=tp,fp=fp,fn=fn,
                g1_exact=c['g1_exact'],edge_f1=2*tp/max(1,2*tp+fp+fn),
                precision=tp/max(1,tp+fp),recall=tp/max(1,tp+fn))


def _decision(model,rec,experts,spec,device,policy,threshold=.25,gap=.1, *, cached=None):
    item=rec['item']
    with torch.no_grad():
        if cached is None:
            margins,pred=_predict_inputs(rec,experts,device)
            logits,comp,meta=model(item['objects'],margins,pred)
        else:
            margins,logits,comp,meta=cached
        if policy == 'soft':
            result,_=old._decode_matrix(item,logits,spec)
            return result,meta
        elif policy == 'hard':
            # Conservative substitution: preserve the original expert except
            # on actual disagreements where another head has a learned lead.
            probs=comp.softmax(0)
            winner=probs.argmax(0)
            winner_p=probs.max(0).values
            baseline_p=probs[0]
            selected=meta['disagreement'] & (winner != 0) & (winner_p>=threshold) & (
                      winner_p-baseline_p>=gap)
            n=len(item['objects'])
            a=torch.arange(n,device=device)
            i,j=torch.meshgrid(a,a,indexing='ij')
            replacement=margins[winner,i,j]
            changed=torch.where(selected[...,None],replacement,margins[0])
            scores=torch.cat((changed.new_zeros((n,n,1)),changed),-1)
            result,_=old._decode_matrix(item,scores,spec)
            return result,{'changed_positions':int(selected.sum()),
                           'disagreement_positions':int(meta['disagreement'].sum())}
        else:
            raise ValueError('G2_0098_UNKNOWN_POLICY')


def _evaluate(records,experts,spec,device,model=None,policies=()):
    counters=defaultdict(lambda:defaultdict(Counter))
    correction=defaultdict(Counter)
    rescued=Counter()
    for rec in records:
        item=rec['item']
        fam=item['row']['family']
        mode=item['row']['mode']
        gold=item['targets']
        baseline=rec['predictions'][ANCHOR]
        available_exact=any(_count(rec,p)['exact'] for p in rec['predictions'].values())
        predictions={k:rec['predictions'][k] for k in experts}
        average=rec['expert_margins'].mean(0)
        uniform=torch.cat((torch.zeros_like(average[...,:1]),average),dim=-1)
        predictions['uniform'],_=old._decode_matrix(item,uniform,spec)
        cached=None
        if policies:
            require(model is not None,'ROUTER_REQUIRED')
            with torch.no_grad():
                margins,pred=_predict_inputs(rec,experts,device)
                logits,comp,meta=model(item['objects'],margins,pred)
                cached=(margins,logits,comp,meta)
        for label,policy,t,g in policies:
            predictions[label],_=_decision(model,rec,experts,spec,device,policy,t,g,
                                             cached=cached)
        for label,pred in predictions.items():
            c=_count(rec,pred)
            for key in ('overall','family:'+fam,'mode:'+mode):
                counters[label][key].update(c)
            if label not in experts and label!='uniform':
                valid=~torch.eye(len(gold),dtype=torch.bool)
                changed=valid & (pred!=baseline)
                fixed=changed & (pred==gold) & (baseline!=gold)
                harmed=changed & (pred!=gold) & (baseline==gold)
                correction[label].update(changed=int(changed.sum()),
                                         fixed=int(fixed.sum()),harmed=int(harmed.sum()))
                if c['exact'] and not available_exact:
                    rescued[label]+=1
    models={k:{'overall':_metric(v['overall']),
               'by_family':{f:_metric(v['family:'+f]) for f in atlas.FAMILIES},
               'by_fuzz_mode':{m:_metric(v['mode:'+m]) for m in MODES}}
            for k,v in counters.items()}
    return {'models':models,'corrections':{k:dict(v) for k,v in correction.items()},
            'newly_exact_beyond_all_experts':dict(rescued)}


def _quality(metric):
    return (metric['exact'],metric['edge_f1'])


def _search_policies(records,experts,spec,device,model):
    # Dev-only policy search, never confirmation. Comparison is against the
    # unchanged anchor; finding a strong dev checkpoint is not certification.
    possibilities=[('soft','soft',0.,0.)]
    for threshold in (.20,.30,.40,.55,.70):
        for gap in (.05,.15,.30):
            possibilities.append((f'hard_p{threshold:.2f}_gap{gap:.2f}',
                                  'hard',threshold,gap))
    results=_evaluate(records,experts,spec,device,model,possibilities)
    ranked=sorted(possibilities,key=lambda x:(
        _quality(results['models'][x[0]]['overall']),x[0]),reverse=True)
    return results,ranked[0],ranked


def _train(model,fit,dev,experts,spec,cfg,device,dest):
    rng=random.Random(cfg.seed)
    groups=defaultdict(list)
    for rec in fit:
        groups[rec['item']['row']['base_id']].append(rec)
    bases=sorted(groups)
    optimizer=torch.optim.AdamW(model.parameters(),lr=cfg.lr,weight_decay=.0001)
    best_key=None
    best_state=None
    best_policy=None
    history=[]
    for step in range(1,cfg.steps+1):
        model.train()
        optimizer.zero_grad(set_to_none=True)
        chosen=rng.sample(bases,min(cfg.batch_bases,len(bases)))
        losses=[];aligned=0
        for base in chosen:
            rows=rng.sample(groups[base],min(2,len(groups[base])))
            scored=[]
            for rec in rows:
                cost,competence,_=_loss_for_case(model,rec,experts,spec,device,
                       cfg.positive_cap,cfg.hinge_weight,
                       with_hinge=(step%cfg.hinge_every==0))
                losses.append(cost)
                scored.append((rec,competence))
            if len(scored)==2 and cfg.fuzz_weight:
                fuzz,pairs=_fuzz_loss(scored[0][0],scored[0][1],
                                      scored[1][0],scored[1][1],device)
                if pairs:
                    losses.append(cfg.fuzz_weight*fuzz)
                    aligned+=pairs
        require(losses,'EMPTY_TRAINING_BATCH')
        loss=torch.stack(losses).mean()
        require(bool(torch.isfinite(loss)),'NONFINITE_TRAINING_LOSS')
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(),1.)
        optimizer.step()
        if step==1 or step%cfg.eval_every==0 or step==cfg.steps:
            model.eval()
            result,policy,ranked=_search_policies(dev,experts,spec,device,model)
            quality=_quality(result['models'][policy[0]]['overall'])
            if best_key is None or quality>best_key:
                best_key=quality
                best_state={k:v.detach().cpu().clone()
                            for k,v in model.state_dict().items()}
                best_policy=policy
                improved=True
            else:
                improved=False
            row={'step':step,'loss':float(loss.detach()),'aligned_fuzz_pairs':aligned,
                 'dev_exact':quality[0],'dev_edge_f1':quality[1],
                 'policy':policy[0],'selected':improved}
            history.append(row)
            log('G2_0098_LEARN',row)
    require(best_state is not None,'NO_COMPETENCE_CHECKPOINT')
    model.load_state_dict(best_state,strict=True)
    model.eval()
    save_file({k:v.contiguous() for k,v in best_state.items()},
              str(dest/'competence-router.safetensors'))
    with (dest/'training-history.jsonl').open('x',encoding='utf8') as h:
        for row in history:
            h.write(json.dumps(row,sort_keys=True)+'\n')
    return {'selected_policy':best_policy,'best_dev_quality':best_key,
            'selected_step':next(x['step'] for x in reversed(history) if x['selected']),
            'router_sha256':old.digest(dest/'competence-router.safetensors'),
            'history_sha256':old.digest(dest/'training-history.jsonl')}


def audit(*,root=old.ROOT_DEFAULT):
    *_,p97,report=_sources(root)
    comp=report['dev']['complementarity']
    ranked=sorted(comp['exclusive_right_on_relevant_positions'].items(),
                  key=lambda x:(-x[1],x[0]))
    for name,count in ranked:
        if count:
            log('G2_0098_HISTORICAL_EXPERT',{'name':name,'exclusive_correct':count})
    log('G2_0098_AUDIT',{'previous_exact':report['confirmation']['models'][
          'hierarchical_moe']['overall']['exact'],
          'previous_oracle_exact':report['confirmation']['oracle']['pair_select_exact'],
          'prior_expert_count':len(report['experts']),
          'candidate_experts':list(_choose_experts(report,'selected',7)),
          'new_discovery_required':True,'g3_unlocked':False})


def run(*,root=old.ROOT_DEFAULT,output=None,device='cuda',seed=SEED,
        expert_set='selected',expert_limit=7,fit_bases=36,dev_bases=18,
        steps=800,eval_every=80,batch_bases=2,lr=.001,positive_cap=12.,
        hinge_weight=.025,hinge_every=5,fuzz_weight=.12,
        confirmation_bases=300):
    require(expert_set in ('all','selected') and 4<=expert_limit<=18 and
            fit_bases>0 and dev_bases>0 and 1<=steps<=10000 and
            1<=eval_every<=steps and 1<=batch_bases<=16 and 0<lr<.1 and
            1<=positive_cap<=100 and 0<=hinge_weight<=10 and
            hinge_every>=1 and 0<=fuzz_weight<=10 and
            0<=confirmation_bases<=300,'INVALID_CONFIGURATION')
    require(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),
            'DEVICE_NOT_AVAILABLE')
    root,atlas_dir,commit,parent_dir,h95,h96,p97,previous=_sources(root)
    experts=_choose_experts(previous,expert_set,expert_limit)
    selection=_new_selection(atlas_dir,h95,h96,previous,fit_bases,dev_bases,seed)
    dest=(Path(output) if output else root/NAME).resolve()
    require(not dest.exists() and dest.parent.is_dir(), 'OUTPUT_EXISTS_OR_PARENT_MISSING')
    cfg=argparse.Namespace(root=str(root),output=str(dest),device=device,seed=seed,
         expert_set=expert_set,expert_limit=expert_limit,fit_bases=fit_bases,
         dev_bases=dev_bases,steps=steps,eval_every=eval_every,
         batch_bases=batch_bases,lr=lr,positive_cap=positive_cap,
         hinge_weight=hinge_weight,hinge_every=hinge_every,
         fuzz_weight=fuzz_weight,confirmation_bases=confirmation_bases)
    dest.mkdir(exist_ok=False)
    old.write(dest/'provenance.json',{'schema':SCHEMA,
        'runner_sha256':old.digest(__file__),
        'previous_0097_report_sha256':old.digest(p97/'report.json'),
        'previous_0097_router_sha256':previous['router']['router_sha256'],
        'experts':experts,'selection':selection,'settings':vars(cfg),
        'prior_confirmation_never_training':True,
        'competence_training_gold_only':True,
        'inference_uses_gold_or_author_ids':False,
        'historical_and_new_expert_weights_frozen':True,
        'confirmation_not_independent':True,'g3_unlocked':False})
    log('G2_0098_PREPARED',{'experts':len(experts),
        'fit_cases':len(selection['fit_case_ids']),
        'dev_cases':len(selection['dev_case_ids']),
        'excludes_previous_0095_0096_0097_bases':True})
    torch.manual_seed(seed)
    paths=atlas._roots(root)
    parent_rec,_=hard._verify_trained(paths,paths['parent_0087'])
    network,tok=backbone.model_components(parent_rec['model_id'],paths['adapter'],device)
    require(all(not p.requires_grad for p in network.parameters()), 'BACKBONE_NOT_FROZEN')
    composite=g1native.load_composite(paths['composite'],device=device)
    head,_=cur._load_checkpoint(parent_dir,device=device)
    old._mutable(head)
    spec=commit['lineage']['decoder']
    fit_rows=old._get_rows(atlas_dir,set(selection['fit_case_ids']),split='discovery')
    dev_rows=old._get_rows(atlas_dir,set(selection['dev_case_ids']),split='discovery')
    fit_items=old._capture(network,tok,composite,fit_rows,device,must_exact=True)
    dev_items=old._capture(network,tok,composite,dev_rows,device,must_exact=True)
    fit=_capture_experts(head,fit_items,device,spec,experts,'0098-fit',training=True)
    dev=_capture_experts(head,dev_items,device,spec,experts,'0098-dev')
    fit_prior=prev._evaluate(fit,experts,spec)
    dev_prior=prev._evaluate(dev,experts,spec)
    old.write(dest/'fit-expert-meta-analysis.json',fit_prior)
    old.write(dest/'dev-expert-meta-analysis.json',dev_prior)
    log('G2_0098_EXPERT_ORACLE',{
        'fit':fit_prior['oracle'], 'dev':dev_prior['oracle'],
        'fit_complementarity':{k:v for k,v in fit_prior['complementarity'].items()
                               if k!='pairwise'},
        'dev_complementarity':{k:v for k,v in dev_prior['complementarity'].items()
                               if k!='pairwise'}})
    router=CompetenceRouter(len(experts)).to(device)
    training=_train(router,fit,dev,experts,spec,cfg,device,dest)
    policy=training['selected_policy']
    dev_result=_evaluate(dev,experts,spec,device,router,[policy])
    old.write(dest/'dev-routing-analysis.json',dev_result)
    old.write(dest/'sealed-before-confirmation.json',{
        'schema':SCHEMA,'router_sha256':training['router_sha256'],
        'selected_policy':policy,
        'expert_sha256':{k:v['sha256'] for k,v in experts.items()},
        'selection':selection,'confirmation_answers_seen':False,
        'g3_unlocked':False})
    confirmation=None
    if confirmation_bases:
        rows=prev._read_confirmation(atlas_dir,confirmation_bases,seed=prev.SEED)
        chunk_results=[]
        for offset in range(0,len(rows),50):
            chunk=rows[offset:offset+50]
            items=[old._capture_quiet(network,tok,composite,row,device) for row in chunk]
            records=_capture_experts(head,items,device,spec,experts,
                         '0098-confirmation-'+str(offset//50+1))
            chunk_results.append(_evaluate(records,experts,spec,device,router,[policy]))
            log('G2_0098_CONFIRM',{'done':min(offset+50,len(rows)),
                                    'of':len(rows)})
            del items,records
        # Aggregate integer counts, never average chunk F1.
        combined=defaultdict(lambda:defaultdict(Counter))
        correction=defaultdict(Counter)
        rescued=Counter()
        for block in chunk_results:
            for key,entry in block['models'].items():
                for subname,record in [('overall',entry['overall'])]:
                    combined[key][subname].update({k:record[k] for k in
                        ('tp','fp','fn','cases','exact','g1_exact')})
                for family,record in entry['by_family'].items():
                    combined[key]['family:'+family].update({k:record[k] for k in
                        ('tp','fp','fn','cases','exact','g1_exact')})
                for mode,record in entry['by_fuzz_mode'].items():
                    combined[key]['mode:'+mode].update({k:record[k] for k in
                        ('tp','fp','fn','cases','exact','g1_exact')})
            for key,change in block['corrections'].items():
                correction[key].update(change)
            rescued.update(block['newly_exact_beyond_all_experts'])
        confirmation={'models':{
            name:{'overall':_metric(v['overall']),
                  'by_family':{f:_metric(v['family:'+f]) for f in atlas.FAMILIES},
                  'by_fuzz_mode':{m:_metric(v['mode:'+m]) for m in MODES}}
            for name,v in combined.items()},
            'corrections':{k:dict(v) for k,v in correction.items()},
            'newly_exact_beyond_all_experts':dict(rescued)}
        old.write(dest/'confirmation-regression.json',confirmation)
    result={'schema':SCHEMA,'status':'G2_0098_COMPETENCE_ROUTING_RESEARCH_ONLY',
            'provenance_sha256':old.digest(dest/'provenance.json'),
            'prior_0097_report_sha256':old.digest(p97/'report.json'),
            'expert_count':len(experts),'experts':experts,
            'fit_meta':fit_prior,'dev_meta':dev_prior,
            'training':training,'development':dev_result,
            'confirmation':confirmation,
            'historical_0097_same_confirmation':(previous['confirmation']['models']
                ['hierarchical_moe']['overall'] if confirmation_bases==300 else None),
            'parent_unchanged':old.digest(parent_dir/cur.HEAD_FILE)==
                 h96[1]['parent_checkpoint_sha256'],
            'production_promotion':False,'g3_unlocked':False,
            'independent_certification':False}
    require(result['parent_unchanged'],'PARENT_CHECKPOINT_CHANGED')
    old.write(dest/'report.json',result)
    old.write(dest/'lock.json',{'schema':SCHEMA,
        'report_sha256':old.digest(dest/'report.json'),
        'router_sha256':training['router_sha256'],
        'provenance_sha256':old.digest(dest/'provenance.json'),
        'parent_checkpoint_sha256':h96[1]['parent_checkpoint_sha256'],
        'g3_unlocked':False})
    main_metric=(confirmation or dev_result)['models'][policy[0]]['overall']
    anchor_metric=(confirmation or dev_result)['models'][ANCHOR]['overall']
    log('G2_0098_RESULT',{'status':result['status'],
        'selected_policy':policy[0],'comparison_cases':main_metric['cases'],
        'competence_router':main_metric,'frozen_supervised':anchor_metric,
        'historical_0097':result['historical_0097_same_confirmation'],
        'new_exact_beyond_existing_experts':(confirmation or dev_result)[
          'newly_exact_beyond_all_experts'].get(policy[0],0),
        'g3_unlocked':False})
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',nargs='?',default='run',choices=('audit','run'))
    parser.add_argument('--root',default=old.ROOT_DEFAULT)
    parser.add_argument('--output')
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--expert-set',choices=('all','selected'),default='selected')
    parser.add_argument('--expert-limit',type=int,default=7)
    parser.add_argument('--fit-bases',type=int,default=36)
    parser.add_argument('--dev-bases',type=int,default=18)
    parser.add_argument('--steps',type=int,default=800)
    parser.add_argument('--eval-every',type=int,default=80)
    parser.add_argument('--batch-bases',type=int,default=2)
    parser.add_argument('--lr',type=float,default=.001)
    parser.add_argument('--positive-cap',type=float,default=12.)
    parser.add_argument('--hinge-weight',type=float,default=.025)
    parser.add_argument('--hinge-every',type=int,default=5)
    parser.add_argument('--fuzz-weight',type=float,default=.12)
    parser.add_argument('--confirmation-bases',type=int,default=300)
    args=vars(parser.parse_args(argv))
    cmd=args.pop('command')
    if cmd=='audit':
        return audit(root=args['root'])
    return run(**args)


if __name__=='__main__':
    main()
