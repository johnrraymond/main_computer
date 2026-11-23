"""G2-0098 router forensics: frozen experts, frozen router, controlled ablations.

Run from nanojev directory:
  python -u -m tools.nanojev_gamer_v1_g2_router_forensics_0098 audit
  python -u -m tools.nanojev_gamer_v1_g2_router_forensics_0098 probe --device cuda --split dev
  python -u -m tools.nanojev_gamer_v1_g2_router_forensics_0098 probe --device cuda --split fit

No training, checkpoint changes, confirmation reads, production promotion, or G3 unlock.
Gold is used only for measuring diagnostics and explicitly marked upper bounds.
All deployable counterfactuals select experts without using targets.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import math

import torch
from safetensors.torch import load_file
from tools import nanojev_gamer_v1_g2_competence_routing_0098 as m
from tools import nanojev_gamer_v1_g2_routing_diagnosis_0098 as base
from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old
from tools import nanojev_gamer_v1_g2_hard_negative_learning_0087 as hard
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g1_native_composite_0082 as g1native
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

TAG='G2_0098_FORENSICS'
R=len(BIND_CLASSES)-1
FEATURE_ABLATIONS={
    "no_graph_context":(0,32),
    "no_pair_context":(32,64),
    "no_expert_identity":(64,76),
    "no_predicted_relation_embedding":(76,88),
    "no_expert_confidence_features":(88,98),
}
POLICIES=(('anchor',None,None,None),
          ('hard_current','margin',.2,.05),
          ('hard_ungated','margin',0.,0.),
          ('hard_p015_gap0','margin',.15,0.),
          ('hard_p020_gap0','margin',.2,0.),
          ('hard_p025_gap010','margin',.25,.10),
          ('hard_p035_gap015','margin',.35,.15),
          ('label_pinned_current','pin',.2,.05),
          ('label_pinned_ungated','pin',0.,0.),
          ('label_pinned_p025','pin',.25,.10),
          ('soft','soft',None,None))


def emit(kind, value):
    print(f'{TAG}_{kind} '+json.dumps(value,sort_keys=True,allow_nan=False),flush=True)


def ensure(condition,message):
    if not condition:raise ValueError(TAG+'_'+message)


def _score_from_margins(margins):
    return torch.cat((margins.new_zeros((*margins.shape[:-1],1)),margins),-1)


def _route_scores(margins,predictions,competence,disagreement,kind,threshold,gap,*,strength=12.):
    """No gold, identities, or authored labels enter policy counterfactuals."""
    weights=competence.softmax(0)
    winner=weights.argmax(0)
    winprob=weights.max(0).values
    swaps=disagreement & (winner!=0) & (winprob>=threshold) & (
        winprob-weights[0]>=gap)
    n=winner.shape[0]
    ix=torch.arange(n,device=winner.device)
    i,j=torch.meshgrid(ix,ix,indexing='ij')
    if kind=='margin':
        replacement=margins[winner,i,j]
        logits=_score_from_margins(torch.where(swaps[...,None],replacement,margins[0]))
    elif kind=='pin':
        # Force the *decoded label* that made the winning expert attractive.
        # Scores on unchanged pairs are exactly the supervised-head margins.
        labels=predictions[winner,i,j]
        logits=_score_from_margins(margins[0]).clone()
        force=torch.full_like(logits,-strength)
        force.scatter_(-1,labels.long().unsqueeze(-1),strength)
        logits=torch.where(swaps.unsqueeze(-1),force,logits)
    else:raise ValueError('UNKNOWN_SCORE_POLICY')
    return logits,swaps,winner


def _metric_matrix(pred,gold,valid):
    return int(((pred==gold)&valid).sum()),int(((pred!=gold)&valid).sum())


def _family_exact(item,pred):
    return int(m._count({'item':item},pred)['exact'])


def _bucket(confidence):
    x=float(confidence)
    for boundary in (.15,.20,.25,.30,.40,.50,.65,.80):
        if x<boundary:return '<'+str(boundary)
    return '>=.8'


def _cumulative(counter):
    out=dict(counter)
    for name in ('rescues','harms','changed','cases','exact','correct_relevant','wrong_relevant'):
        out.setdefault(name,0)
    return out


def _evaluate_case(rec,experts,model,spec,device,collector,*,max_pin_strength=12.):
    item=rec['item']
    gold=item['targets'].long().cpu()
    n=len(gold)
    if n<2:return
    valid=~torch.eye(n,dtype=torch.bool)
    margins,preds_gpu=m._predict_inputs(rec,experts,device)
    with torch.no_grad():
        soft,competence,meta=model(item['objects'],margins,preds_gpu)
        weights=competence.softmax(0)
        winner=weights.argmax(0)
        pred=preds_gpu.detach().cpu()
        winner_cpu=winner.cpu()
        weight_cpu=weights.detach().cpu()
        disagreement=(meta['disagreement'].cpu()&valid)
        i=torch.arange(n)[:,None].expand(n,n)
        j=torch.arange(n)[None,:].expand(n,n)
        winning=pred[winner_cpu,i,j]
        baseline=pred[0]
        all_correct=(pred==gold[None]).any(0)
        relevant=valid&((gold>0)|(pred>0).any(0))
        rescue=(baseline!=gold)&all_correct&relevant
        protect=(baseline==gold)&relevant
        hard_raw={}
        for label,kind,th,gap in POLICIES:
            if kind is None:
                decoded=baseline
                raw=baseline
                swapped=torch.zeros_like(valid)
            elif kind=='soft':
                raw=soft.argmax(-1).cpu()
                decoded,_=old._decode_matrix(item,soft,spec)
                swapped=((winner_cpu!=0)&disagreement)
            else:
                scores,switch,_=_route_scores(margins,preds_gpu,competence,
                    meta['disagreement'],kind,th,gap,strength=max_pin_strength)
                raw=scores.argmax(-1).cpu()
                decoded,_=old._decode_matrix(item,scores,spec)
                swapped=switch.cpu()&valid
            hard_raw[label]=(raw,decoded,swapped)
            c=collector['policy'][label]
            c.update(cases=1,exact=_family_exact(item,decoded),
                raw_exact=int(bool(((raw==gold)|~valid).all() and item['g1_exact'])),
                repairs=int((rescue&(decoded==gold)).sum()),
                harms=int((protect&(decoded!=gold)).sum()),
                swaps=int(swapped.sum()),
                missed_correct_winner=int((rescue&(winning==gold)&(decoded!=gold)).sum()),
                raw_correct_lost=int((relevant&(raw==gold)&(decoded!=gold)).sum()),
                raw_wrong_repaired=int((relevant&(raw!=gold)&(decoded==gold)).sum()))
            by=collector['policy_family'][label][item['row']['family']]
            by.update(cases=1,exact=_family_exact(item,decoded),
                repairs=int((rescue&(decoded==gold)).sum()),
                harms=int((protect&(decoded!=gold)).sum()))
        collector['total'].update(cases=1,relevant=int(relevant.sum()),
            disagreement=int(disagreement.sum()),rescue_available=int(rescue.sum()),
            anchor_protected=int(protect.sum()),all_experts_wrong=int((relevant&~all_correct).sum()),
            winner_right_on_rescue=int((rescue&(winning==gold)).sum()),
            winner_wrong_on_protect=int((protect&(winning!=gold)).sum()))
        # Controlled feature-group ablations. A forward-prehook masks exactly
        # one of the competence model's concatenated feature branches, keeping
        # all expert heads, output policy, and decoder fixed. Out-of-distribution
        # ablation is mechanistic evidence, NOT a retrained model bakeoff.
        for ablation,(first,last) in FEATURE_ABLATIONS.items():
            def mask_hook(_module,inputs,lo=first,hi=last):
                x=inputs[0].clone()
                x[...,lo:hi]=0
                return (x,)
            handle=model.competence.register_forward_pre_hook(mask_hook)
            try:
                _,ablated_comp,ablated_meta=model(item['objects'],margins,preds_gpu)
            finally:
                handle.remove()
            ablated_scores,_,ablated_winner=_route_scores(margins,preds_gpu,
                ablated_comp,ablated_meta['disagreement'],'margin',.2,.05)
            ablated_dec,_=old._decode_matrix(item,ablated_scores,spec)
            aw=ablated_winner.cpu()
            ablated_selected=pred[aw,i,j]
            count=collector['feature_ablations'][ablation]
            count.update(cases=1,exact=_family_exact(item,ablated_dec),
                rescued=int((rescue&(ablated_dec==gold)).sum()),
                harmed=int((protect&(ablated_dec!=gold)).sum()),
                correct_winner_on_rescue=int((rescue&(ablated_selected==gold)).sum()),
                refers_to_winner=int((rescue&(gold==BIND_CLASSES.index('refers_to'))&
                                      (ablated_selected==gold)).sum()))
        # Audit the source-label contract. The correctness of the selected decoded
        # label and its raw logit argmax are NOT interchangeable.
        raw_logits=_score_from_margins(margins)
        raw_exp=raw_logits.argmax(-1).cpu()
        raw_winner=raw_exp[winner_cpu,i,j]
        choice_correct=(winning==gold)
        collector['representation'].update(
            expert_decoded_differs_from_raw=int((pred!=raw_exp).logical_and(valid[None]).sum()),
            expert_decoded_total=int(valid.sum())*len(experts),
            selected_decoded_differs_from_raw=int(((winning!=raw_winner)&relevant).sum()),
            winner_correct_but_raw_wrong=int((rescue&(winning==gold)&(raw_winner!=gold)).sum()),
            winner_wrong_but_raw_correct=int((rescue&(winning!=gold)&(raw_winner==gold)).sum()))
        # For a decoded expert's correct prediction, how strong was its unmodified
        # raw margin for that label? This captures raw/structured disagreements.
        for k,name in enumerate(experts):
            e=collector['by_expert'][name]
            winning_mask=(winner_cpu==k)&disagreement
            e.update(selected=int(winning_mask.sum()),
                selected_correct=int((winning_mask&(pred[k]==gold)).sum()),
                selected_rescue=int((winning_mask&rescue&(pred[k]==gold)).sum()),
                rescue_available=int((rescue&(pred[k]==gold)).sum()))
        # Score and class/feature forensics for gold 'refers_to' and all classes.
        for label_id,name in enumerate(BIND_CLASSES):
            focus=(gold==label_id)&relevant
            if not bool(focus.any()):continue
            cls=collector['by_gold_relation'][name]
            relrescue=focus&rescue
            out=hard_raw['hard_current'][1]
            cls.update(positions=int(focus.sum()),rescue_available=int(relrescue.sum()),
                winner_correct=int((relrescue&(winning==gold)).sum()),
                policy_fixed=int((relrescue&(out==gold)).sum()),
                protected=int((focus&protect).sum()),
                damaged=int((focus&protect&(out!=gold)).sum()))
            if name=='refers_to':
                ensure(BIND_CLASSES[label_id]=='refers_to','RELATION_INDEX_BAD')
                idx=relrescue.nonzero(as_tuple=False)
                for a,b in idx.tolist():
                    votes=pred[:,a,b]
                    wins=int(winner_cpu[a,b])
                    correct_heads=(votes==gold[a,b])
                    ranking=weight_cpu[:,a,b].argsort(descending=True)
                    rank=next(q+1 for q,k in enumerate(ranking.tolist()) if correct_heads[k])
                    endpoint=(str(item['objects'][a][2]),str(item['objects'][b][2]))
                    rr=collector['refers_to']
                    rr['ranking'][str(rank)]+=1
                    rr['winner_label'][BIND_CLASSES[int(winning[a,b])]]+=1
                    rr['endpoint_kind'][str(endpoint)]+=1
                    rr['winner_expert'][list(experts)[wins]]+=1
                    rr['correct_expert_count'][str(int(correct_heads.sum()))]+=1
                    rr['correct_expert'][list(experts)[int(correct_heads.nonzero()[0,0])]]+=1
                    rr['top_weight_bin'][_bucket(weight_cpu[:,a,b].max())]+=1
                    rr['baseline_raw_label'][BIND_CLASSES[int(raw_exp[0,a,b])]]+=1
                    rr['winner_raw_label'][BIND_CLASSES[int(raw_winner[a,b])]]+=1
                    rr['correct_head_raw_label'][BIND_CLASSES[int(raw_exp[int(correct_heads.nonzero()[0,0]),a,b])]]+=1
                    rr['hard_gate_swap'][str(bool(hard_raw['hard_current'][2][a,b]))]+=1
        # When the router selects an expert, how often is the selection right
        # conditional on evidence and score strength? This is descriptive only.
        maxprob=weight_cpu.max(0).values
        focus=(disagreement&relevant).nonzero(as_tuple=False)
        for a,b in focus.tolist():
            bucket=_bucket(maxprob[a,b])
            cc=collector['confidence'][bucket]
            cc.update(cases=1,
                winner_correct=int(choice_correct[a,b]),
                winner_anchor=int(winner_cpu[a,b]==0),
                anchor_correct=int(baseline[a,b]==gold[a,b]),
                available_rescue=int(rescue[a,b]),
                won_rescue=int(rescue[a,b] and choice_correct[a,b]))
        # Mode and independence diagnostics, never save raw IDs or prompts.
        baseline_exact=_family_exact(item,baseline)
        selected_exact=_family_exact(item,hard_raw['hard_current'][1])
        base_stats=collector['base'][str(item['row']['base_id'])]
        base_stats.update(cases=1,anchor_exact=baseline_exact,
                          current_exact=selected_exact,
                          potential_exact=int(bool((all_correct[valid]).all()) and item['g1_exact']))
        mode_stats=collector['mode'][item['row']['mode']]
        mode_stats.update(cases=1,anchor_exact=baseline_exact,
                          current_exact=selected_exact,
                          rescue_available=int(rescue.sum()))


def _gradient_conflict(records,experts,model,spec,device,max_pairs):
    """FIT ONLY: local gradient directions of G2-0098 base objective vs fuzz.

    This is a controlled observational gradient test; it does not update the
    model. Explicitly distinguish negative cosine from causal ablation proof.
    """
    if max_pairs<=0:return {'enabled':False}
    grouped=defaultdict(dict)
    for rec in records:grouped[rec['item']['row']['base_id']][rec['item']['row']['mode']]=rec
    pairs=[]
    for b in sorted(grouped):
        g=grouped[b]
        for mode in ('renaming','role_swap','added_distractor'):
            if 'base' in g and mode in g:
                pairs.append((g['base'],g[mode],mode))
    pairs=pairs[:max_pairs]
    stats=Counter()
    modes=defaultdict(Counter)
    trainables=[p for p in model.parameters() if p.requires_grad]
    for original,variant,mode in pairs:
        with torch.enable_grad():
            loss_a,ca,_=m._loss_for_case(model,original,experts,spec,device,
                                        positive_cap=12.,hinge_weight=0.,with_hinge=False)
            loss_b,cb,_=m._loss_for_case(model,variant,experts,spec,device,
                                        positive_cap=12.,hinge_weight=0.,with_hinge=False)
            fuzz,n=m._fuzz_loss(original,ca,variant,cb,device)
            if not n:continue
            supervised=(loss_a+loss_b)/2
            a=torch.autograd.grad(supervised,trainables,retain_graph=True,
                                  allow_unused=True)
            b=torch.autograd.grad(fuzz,trainables,allow_unused=True)
            dot=sum(float((x*y).sum().detach()) for x,y in zip(a,b) if x is not None and y is not None)
            na=sum(float(x.square().sum().detach()) for x in a if x is not None)
            nb=sum(float(x.square().sum().detach()) for x in b if x is not None)
            norm=math.sqrt(max(na*nb,0.))
            cos=dot/norm if norm else 0.
            for target in (stats,modes[mode]):
                target.update(pairs=1,aligned=int(n),opposed=int(dot<0),
                              cos_sum_1000=int(cos*1000),
                              near_orthogonal=int(abs(cos)<.1))
    return {'overall':dict(stats),'by_mode':{k:dict(v) for k,v in modes.items()},
            'interpretation':'Opposed gradient means the frozen-checkpoint fuzz penalty locally conflicts with the supervised objective; it does not establish ablation outcome.'}


def _summarize(collector,split,experts,selection,score,report,gradients):
    total=dict(collector['total'])
    base_summary={
        'independent_bases':len(collector['base']),
        'any_exact_gain':sum(s['current_exact']>s['anchor_exact'] for s in collector['base'].values()),
        'any_exact_regression':sum(s['current_exact']<s['anchor_exact'] for s in collector['base'].values()),
        'oracle_possible_bases':sum(s['potential_exact']>0 for s in collector['base'].values())}
    p={name:dict(data) for name,data in collector['policy'].items()}
    fam={name:{f:dict(v) for f,v in groups.items()}
         for name,groups in collector['policy_family'].items()}
    return {'schema':'nanojev.g2.router-forensics-0098.v1',
        'split':split,'status':'DIAGNOSTIC_ONLY_NO_TRAINING',
        'confirmation_used':False,'g3_unlocked':False,
        'expert_names':list(experts),
        'selected_step':report['training']['selected_step'],
        'historical_policy':report['training']['selected_policy'],
        'checkpoint_sha256':score,
        'selection_counts':{'fit':len(selection['fit_case_ids']),'dev':len(selection['dev_case_ids'])},
        'totals':total,'policies':p,'policies_by_family':fam,
        'representation_mismatch':dict(collector['representation']),
        'feature_ablations':{k:dict(v) for k,v in collector['feature_ablations'].items()},
        'by_expert':{k:dict(v) for k,v in collector['by_expert'].items()},
        'by_gold_relation':{k:dict(v) for k,v in collector['by_gold_relation'].items()},
        'refers_to':{k:dict(v) for k,v in collector['refers_to'].items()},
        'confidence_bins':{k:dict(v) for k,v in collector['confidence'].items()},
        'fuzz_mode':{k:dict(v) for k,v in collector['mode'].items()},
        'base_summary':base_summary,
        'gradient_conflicts':gradients,
        'caveats':['Oracle upper bounds and post-hoc gold scoring are not deployable inference.',
                   'No retraining: these measurements localize but do not prove training-time causation.',
                   'Fit cases and repeated dev are NOT independent certification.',
                   'Score pinning overrides individual labels only at inference-time expert selections.']}


def _frozen_experts_in_training_order(previous, provenance, p97):
    """Rebuild the *trained* expert axis; JSON object keys are sorted on disk.

    The competence router's learned expert embeddings and biases address heads
    by index. provenance['experts'] is a name-keyed immutable inventory, not an
    order declaration: the JSON writer serializes dictionary keys in sorted
    order, while training's _choose_experts puts the anchor at index zero.
    """
    experts=m._choose_experts(previous,
        provenance['settings']['expert_set'],
        provenance['settings']['expert_limit'])
    recorded=provenance['experts']
    ensure(set(experts)==set(recorded),'EXPERT_SET_DRIFT')
    ensure(all(experts[k]['sha256']==recorded[k]['sha256'] and
               Path(experts[k]['path']).resolve()==Path(recorded[k]['path']).resolve()
               for k in experts),'EXPERT_CHECKPOINT_DRIFT')
    ensure(next(iter(experts))==m.ANCHOR,'ANCHOR_ORDER_DRIFT')
    ensure(old.digest(p97/'report.json')==provenance['previous_0097_report_sha256'],
           'EXPERT_SELECTION_SOURCE_DRIFT')
    return experts


def audit(root=old.ROOT_DEFAULT,output=None):
    source=base._locate(root,output)
    root,atlas_dir,commit,parent_dir,h95,h96,p97,previous,dest,provenance,report=source
    experts=_frozen_experts_in_training_order(previous,provenance,p97)
    emit('AUDIT',{'ready':True,'checkpoint_sha256':report['training']['router_sha256'],
        'chosen_experts':list(experts),
        'expert_order_matches_training_selection':True,
        'serialized_inventory_order_is_not_training_order':list(experts)!=list(provenance['experts']),
        'split_sizes':{s:len(provenance['selection'][s+'_case_ids']) for s in ('fit','dev')},
        'possible_causes':['expert decoded labels vs raw margins','hard gating',
                           'score-fusion reversal','relation-specific selector',
                           'fuzz-gradient conflict','structured decoder'],
        'confirmation_used':False,'new_training':False,'g3_unlocked':False})
    return source


def probe(*,root=old.ROOT_DEFAULT,output=None,split='dev',device='cuda',
          grad_pairs=16,pin_strength=12.,max_cases=0):
    ensure(split in ('fit','dev'),'SPLIT_ONLY_FIT_OR_DEV')
    ensure(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),
           'DEVICE_UNAVAILABLE')
    source=base._locate(root,output)
    root,atlas_dir,commit,parent_dir,h95,h96,p97,previous,dest,provenance,report=source
    # Trust the original deterministic *selection* order, not the alphabetical
    # key order of the serialized expert inventory. Preserve axis zero = anchor.
    experts=_frozen_experts_in_training_order(previous,provenance,p97)
    emit('ORDER_VERIFIED',{'training_selection_order':list(experts),
         'serialized_inventory_order':list(provenance['experts']),
         'anchor_index':0,'names_and_checkpoints_match':True})
    path=dest/'competence-router.safetensors'
    ensure(old.digest(path)==report['training']['router_sha256'], 'ROUTER_HASH_DRIFT')
    model=m.CompetenceRouter(len(experts)).to(device)
    model.load_state_dict(load_file(str(path),device='cpu'),strict=True)
    model.eval()
    selected=set(provenance['selection'][split+'_case_ids'])
    emit('START',{'split':split,'cases':len(selected),'device':device,
                  'gradient_pairs':grad_pairs if split=='fit' else 0,
                  'label_pinning_strength':pin_strength,'expert_count':len(experts),
                  'frozen_checkpoint':True,'confirmation_used':False})
    paths=m.atlas._roots(root)
    parent_record,_=hard._verify_trained(paths,paths['parent_0087'])
    network,tok=backbone.model_components(parent_record['model_id'],paths['adapter'],device)
    composite=g1native.load_composite(paths['composite'],device=device)
    head,_=cur._load_checkpoint(parent_dir,device=device)
    old._mutable(head)
    spec=commit['lineage']['decoder']
    rows=old._get_rows(atlas_dir,selected,split='discovery')
    if max_cases:
        ensure(max_cases>0,'INVALID_CASE_CAP')
        rows=rows[:max_cases]
    items=old._capture(network,tok,composite,rows,device,must_exact=True)
    records=m._capture_experts(head,items,device,spec,experts,
                          '0098-forensics-'+split,training=(split=='fit'))
    del items
    collector={'total':Counter(),'representation':Counter(),
               'policy':defaultdict(Counter),
               'feature_ablations':defaultdict(Counter),
               'policy_family':defaultdict(lambda:defaultdict(Counter)),
               'by_expert':defaultdict(Counter),
               'by_gold_relation':defaultdict(Counter),
               'refers_to':defaultdict(Counter),
               'confidence':defaultdict(Counter),
               'base':defaultdict(Counter),'mode':defaultdict(Counter)}
    for index,rec in enumerate(records,1):
        _evaluate_case(rec,experts,model,spec,device,collector,
                       max_pin_strength=pin_strength)
        if index%50==0 or index==len(records):
            emit('EVALUATED',{'split':split,'done':index,'of':len(records)})
    gradients=_gradient_conflict(records,experts,model,spec,device,
                                 grad_pairs if split=='fit' else 0)
    result=_summarize(collector,split,experts,provenance['selection'],
                      old.digest(path),report,gradients)
    emit('TOTAL',result['totals'])
    emit('REPRESENTATION',result['representation_mismatch'])
    emit('FEATURE_ABLATIONS',result['feature_ablations'])
    emit('POLICY_BAKEOFF',result['policies'])
    emit('BY_FAMILY',result['policies_by_family'])
    emit('BY_GOLD_RELATION',result['by_gold_relation'])
    emit('REFERS_TO',result['refers_to'])
    emit('EXPERTS',result['by_expert'])
    emit('CONFIDENCE',result['confidence_bins'])
    emit('FUZZ_GRADIENTS',gradients)
    emit('INDEPENDENT_BASES',result['base_summary'])
    output_dir=dest/'router-forensics-0098'
    output_dir.mkdir(exist_ok=True)
    suffix='-limited'+str(max_cases) if max_cases else ''
    out=output_dir/('forensics-'+split+suffix+'.json')
    # Overwrite only dedicated diagnostic sidecars, never experiment checkpoints.
    payload=json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+'\n'
    out.write_text(payload,encoding='utf-8')
    emit('SAVED',{'path':str(out),'sha256':hashlib.sha256(payload.encode()).hexdigest(),
                  'research_only':True})
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('audit','probe'),nargs='?',default='audit')
    p.add_argument('--root',default=old.ROOT_DEFAULT)
    p.add_argument('--output')
    p.add_argument('--split',choices=('fit','dev'),default='dev')
    p.add_argument('--device',default='cuda')
    p.add_argument('--grad-pairs',type=int,default=16)
    p.add_argument('--pin-strength',type=float,default=12.)
    p.add_argument('--max-cases',type=int,default=0)
    a=p.parse_args(argv)
    if a.action=='audit':return audit(a.root,a.output)
    return probe(root=a.root,output=a.output,split=a.split,device=a.device,
                 grad_pairs=a.grad_pairs,pin_strength=a.pin_strength,max_cases=a.max_cases)


if __name__=='__main__':main()
