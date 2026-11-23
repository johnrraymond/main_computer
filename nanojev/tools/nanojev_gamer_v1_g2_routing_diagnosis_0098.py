"""G2-0098: read-only causal routing diagnosis, without retraining or promotion.

  python -u -m tools.nanojev_gamer_v1_g2_routing_diagnosis_0098 audit
  python -u -m tools.nanojev_gamer_v1_g2_routing_diagnosis_0098 probe --device cuda
  python -u -m tools.nanojev_gamer_v1_g2_routing_diagnosis_0098 probe --device cuda --split fit

The audit uses the prior run's persisted metrics and does not need CUDA.
The probe loads the already sealed 0098 competence checkpoint and replays only
0098's disjoint discovery/development cases. It NEVER opens confirmation answers.
No learned weights, prior artifacts, selection, or production paths are changed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

import torch
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_competence_routing_0098 as m
from tools import nanojev_gamer_v1_g2_hierarchical_moe_0097 as prev
from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old
from tools import nanojev_gamer_v1_g2_hard_negative_learning_0087 as hard
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g1_native_composite_0082 as g1native
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

TAG = 'G2_0098_DIAG'


def say(kind, value):
    print(f'{TAG}_{kind} '+json.dumps(value,sort_keys=True,allow_nan=False),flush=True)


def check(value, why):
    if not value:
        raise ValueError(TAG+'_'+why)


def _locate(root, output=None, require_complete=True):
    root, atlas_dir, commit, parent_dir, h95, h96, p97, previous = m._sources(root)
    dest = Path(output).resolve() if output else root/m.NAME
    check(dest.is_dir(), '0098_RUN_DIRECTORY_MISSING')
    provenance_path = dest/'provenance.json'
    check(provenance_path.is_file(), '0098_PROVENANCE_MISSING')
    provenance=old.read(provenance_path)
    check(provenance['schema']==m.SCHEMA,'0098_SCHEMA_MISMATCH')
    if not require_complete:
        return root,atlas_dir,commit,parent_dir,h95,h96,p97,previous,dest,provenance,None
    report_path=dest/'report.json'
    lock_path=dest/'lock.json'
    check(report_path.is_file() and lock_path.is_file(),
          '0098_NOT_FINISHED__COMPLETE_RUN_BEFORE_CHECKPOINT_PROBE')
    report,lock=old.read(report_path),old.read(lock_path)
    check(report['status']=='G2_0098_COMPETENCE_ROUTING_RESEARCH_ONLY' and
          report['g3_unlocked'] is False and not report['production_promotion'],
          '0098_REPORT_NOT_FROZEN')
    check(old.digest(report_path)==lock['report_sha256'] and
          old.digest(provenance_path)==lock['provenance_sha256'],
          '0098_REPORT_DRIFT')
    router_path=dest/'competence-router.safetensors'
    check(router_path.is_file() and
          old.digest(router_path)==report['training']['router_sha256']==lock['router_sha256'],
          '0098_ROUTER_DRIFT')
    return root,atlas_dir,commit,parent_dir,h95,h96,p97,previous,dest,provenance,report


def audit(*,root=old.ROOT_DEFAULT,output=None):
    source=_locate(root,output)
    *_,dest,provenance,report=source
    selection=provenance['selection']
    dev_oracle=report['dev_meta']['oracle']
    comp=report['dev_meta']['complementarity']
    stage=report['development']
    metric={key:record['overall'] for key,record in stage['models'].items()}
    target=report['training']['selected_policy'][0]
    saved=stage['corrections'].get(target,{})
    exact=metric[target]['exact']
    say('AUDIT',{
        'router_selected_step':report['training']['selected_step'],
        'router_policy':target,
        'router_dev_exact':exact,
        'anchor_dev_exact':metric[m.ANCHOR]['exact'],
        'router_dev_f1':metric[target]['edge_f1'],
        'anchor_dev_f1':metric[m.ANCHOR]['edge_f1'],
        'pairwise_oracle_exact':dev_oracle['pair_select_exact'],
        'some_expert_exact':dev_oracle['some_expert_exact'],
        'all_experts_wrong_relevant':comp['all_experts_wrong_positions'],
        'all_experts_wrong_fraction':comp['all_experts_wrong_positions']/max(1,comp['relevant_positions']),
        'router_change_counts':saved,
        'recovered_graphs_no_expert_exact':stage['newly_exact_beyond_all_experts'].get(target,0),
        'selected_case_counts':{'fit':len(selection['fit_case_ids']),
                                'dev':len(selection['dev_case_ids'])},
        'independent_base_counts':{'fit':len(selection['fit_bases']),
                                   'dev':len(selection['dev_bases'])},
        'model_selection_reuses_dev':True,
        'inference_has_G1_semantic_hidden_states':False,
        'soft_router_blends_margins_not_decoded_expert_labels':True,
        'probe_required_for_causal_attribution':True,
        'confirmation_used':False})
    history=dest/'training-history.jsonl'
    if history.is_file():
        rows=list(old.lines(history))
        say('LEARNING_HISTORY',{'evaluations':len(rows),
            'best_step':report['training']['selected_step'],
            'steps':[{'step':r['step'],'dev_exact':r['dev_exact'],
                    'f1':r['dev_edge_f1'],'policy':r['policy']}
                    for r in rows]})
    return source


def _onehot_scores(labels, *, high=12.):
    """Gold-aware only for a sealed diagnostics oracle; never inference/training."""
    n=labels.shape[0]
    scores=torch.full((n,n,m.R+1),-high,dtype=torch.float32)
    scores[...,0]=0
    scores.scatter_(-1,labels.long()[...,None],high)
    return scores


def _hard_logits(rec,model,experts,device,*,threshold,gap):
    margins,pred=m._predict_inputs(rec,experts,device)
    _,competence,meta=model(rec['item']['objects'],margins,pred)
    weight=competence.softmax(0)
    winner=weight.argmax(0)
    best=weight.max(0).values
    swap=(meta['disagreement']&(winner!=0)&
          (best>=threshold)&(best-weight[0]>=gap))
    n=winner.shape[0]
    loc=torch.arange(n,device=device)
    i,j=torch.meshgrid(loc,loc,indexing='ij')
    selected=margins[winner,i,j]
    scores=torch.where(swap[...,None],selected,margins[0])
    return torch.cat((scores.new_zeros((n,n,1)),scores),-1),swap


def _correctness_metrics(pred,gold,valid):
    return int(((pred==gold)&valid).sum()),int(((pred!=gold)&valid).sum())


def _calculate(records,experts,spec,device,model,selected_policy,split):
    total=Counter()
    families=defaultdict(Counter)
    modes=defaultdict(Counter)
    classes=defaultdict(Counter)
    expert_wins=defaultdict(Counter)
    bases=defaultdict(Counter)
    gold_decoder_failures=[]
    # Do not persist per-example raw prompt, identities, gold, or hidden states.
    profile={'selected_policy':selected_policy[0], 'split':split,
             'checkpoint_only':True,'confirmation_used':False,
             'counts':None,'by_family':None,'by_mode':None,
             'by_gold_relation':None,'expert_choices':None,
             'model_weights':None,'examples_of_decoder_limitation':None}
    with torch.no_grad():
        for rec in records:
            item=rec['item']
            family=item['row']['family']
            mode=item['row']['mode']
            target=item['targets'].long().cpu()
            n=target.shape[0]
            valid=~torch.eye(n,dtype=torch.bool)
            if not n: continue
            margins,head_preds=m._predict_inputs(rec,experts,device)
            scores,competence,meta=model(item['objects'],margins,head_preds)
            preds=head_preds.detach().cpu()
            winner=competence.argmax(0).cpu()
            weights=competence.softmax(0).detach().cpu()
            i=torch.arange(n)[:,None].expand(n,n)
            j=torch.arange(n)[None,:].expand(n,n)
            selected_expert_label=preds[winner,i,j]
            anchor=preds[0]
            all_correct=((preds==target[None,:,:])&valid[None,:,:]).any(0)
            disagreements=meta['disagreement'].detach().cpu()&valid
            relevant=valid & ((target>0)|(preds>0).any(0))
            correctable=(anchor!=target)&all_correct&relevant
            protected=(anchor==target)&relevant
            all_wrong=(~all_correct)&relevant
            winner_correct=(selected_expert_label==target)&valid
            soft_raw=scores.argmax(-1).cpu()
            soft_dec,_=old._decode_matrix(item,scores,spec)
            policy_kind=selected_policy[1]
            if policy_kind=='hard':
                hard_scores,swaps=_hard_logits(rec,model,experts,device,
                    threshold=float(selected_policy[2]),gap=float(selected_policy[3]))
                policy_raw=hard_scores.argmax(-1).cpu()
                policy_dec,_=old._decode_matrix(item,hard_scores,spec)
                swapped=swaps.cpu()&valid
            else:
                policy_raw,policy_dec=soft_raw,soft_dec
                swapped=(winner!=0)&disagreements
            # Gold-aware counterfactuals are diagnostics only, not deployable.
            # An oracle chooses a correct expert's decoded label wherever possible.
            oracle_labels=torch.where(all_correct,target,anchor)
            oracle_dec,_=old._decode_matrix(item,_onehot_scores(oracle_labels),spec)
            full_gold_dec,_=old._decode_matrix(item,_onehot_scores(target),spec)
            oracle_graph=bool((oracle_labels[valid]==target[valid]).all())
            # Distinguish pair correctness from full-graph correctness.
            def exact(matrix):
                return bool(m._count(rec,matrix)['exact'])
            counts=Counter(
                cases=1,positions=int(valid.sum()),relevant=int(relevant.sum()),
                disagreement=int(disagreements.sum()),
                anchor_wrong_recoverable=int(correctable.sum()),
                anchor_correct_at_risk=int(protected.sum()),
                all_experts_wrong=int(all_wrong.sum()),
                winner_correct_on_recoverable=int((correctable&winner_correct).sum()),
                winner_wrong_on_protected=int((protected&~winner_correct).sum()),
                selected_expert_not_anchor=int(((winner!=0)&disagreements).sum()),
                policy_swapped_positions=int(swapped.sum()),
                corrected_at_raw_soft=int((correctable&(soft_raw==target)).sum()),
                corrected_after_soft_decode=int((correctable&(soft_dec==target)).sum()),
                correct_winner_but_soft_wrong=int((correctable&winner_correct&(soft_dec!=target)).sum()),
                corrected_at_policy_predecode=int((correctable&(policy_raw==target)).sum()),
                corrected_at_policy_postdecode=int((correctable&(policy_dec==target)).sum()),
                corrected_winner_but_policy_wrong=int((correctable&winner_correct&(policy_dec!=target)).sum()),
                policy_damaged_anchor_right=int((protected&(policy_dec!=target)).sum()),
                policy_repaired_anchor_wrong=int((correctable&(policy_dec==target)).sum()),
                decoder_destroyed_raw_correct=int((relevant&(policy_raw==target)&(policy_dec!=target)).sum()),
                decoder_repaired_raw_wrong=int((relevant&(policy_raw!=target)&(policy_dec==target)).sum()),
                anchor_exact=int(exact(anchor)),
                policy_exact=int(exact(policy_dec)),
                raw_selected_labels_exact=int(bool((selected_expert_label[valid]==target[valid]).all())),
                oracle_pair_exact=int(item['g1_exact'] and oracle_graph),
                oracle_decoded_exact=int(exact(oracle_dec)),
                full_gold_decoded_exact=int(exact(full_gold_dec)),
                oracle_gold_known_unreconstructable=int(item['g1_exact'] and oracle_graph and not exact(oracle_dec)))
            for sink in (total,families[family],modes[mode]):sink.update(counts)
            base= item['row']['base_id']
            bases[base].update({k:counts[k] for k in ('cases','anchor_exact',
                'policy_exact','oracle_pair_exact','full_gold_decoded_exact')})
            for label in range(1,len(BIND_CLASSES)):
                mask=valid&(target==label)
                if not bool(mask.any()):continue
                cls=classes[BIND_CLASSES[label]]
                cls.update(gold_edges=int(mask.sum()),
                      anchor_missing_or_wrong=int((mask&(anchor!=target)).sum()),
                      correctable=int((mask&correctable).sum()),
                      winner_correct=int((mask&correctable&winner_correct).sum()),
                      repaired=int((mask&correctable&(policy_dec==target)).sum()),
                      damaged=int((mask&(anchor==target)&(policy_dec!=target)).sum()))
            for k,name in enumerate(experts):
                mask=valid&(winner==k)&disagreements
                expert_wins[name].update(selected=int(mask.sum()),
                       selected_correct=int((mask&(preds[k]==target)).sum()),
                       selected_rescue=int((mask&correctable&(preds[k]==target)).sum()),
                       rescue_opportunities=int((correctable&(preds[k]==target)).sum()))
            total.update(top_weight_sum_scaled=int(weights.max(0).values[relevant].sum().item()*1000),
                         top_weight_observations=int(relevant.sum()))
            if exact(full_gold_dec) is False and item['g1_exact']:
                if len(gold_decoder_failures)<8:
                    gold_decoder_failures.append({'family':family,'mode':mode,
                        'objects':n,'gold_edges':int((target>0).sum()),
                        'decoded_edges':int((full_gold_dec>0).sum())})
    profile['counts']=dict(total)
    profile['by_family']={f:dict(v) for f,v in families.items()}
    profile['by_mode']={f:dict(v) for f,v in modes.items()}
    profile['by_gold_relation']={f:dict(v) for f,v in classes.items()}
    profile['expert_choices']={f:dict(v) for f,v in expert_wins.items()}
    profile['base_cluster']={
        'independent_bases':len(bases),
        'all_variants_exact_baseline':sum(
            b['cases']==b['anchor_exact'] for b in bases.values()),
        'all_variants_exact_router':sum(
            b['cases']==b['policy_exact'] for b in bases.values()),
        'bases_with_any_router_regression_in_exact_count':sum(
            b['policy_exact']<b['anchor_exact'] for b in bases.values()),
        'bases_with_any_router_gain_in_exact_count':sum(
            b['policy_exact']>b['anchor_exact'] for b in bases.values()),
        'variant_counts_histogram':dict(Counter(
            b['cases'] for b in bases.values()))}
    profile['mean_top_expert_weight_on_relevant']=(
        total['top_weight_sum_scaled']/1000/max(1,total['top_weight_observations']))
    profile['examples_of_decoder_limitation']=gold_decoder_failures
    return profile



def _fuzz_contradictions(records,experts):
    """Check if 0098's fuzz regularizer aligns mutually incompatible competence.

    FIT ONLY: stable authored IDs and gold are used for this audit. Neither is
    exposed to the competence model or any confirmation inference.
    """
    groups=defaultdict(dict)
    for rec in records:
        row=rec['item']['row']
        groups[row['base_id']][row['mode']]=rec
    result=Counter()
    by_mode=defaultdict(Counter)
    for family in groups.values():
        variants=sorted(family.items())
        for first_index,(mode_a,original) in enumerate(variants):
            for mode,altered in variants[first_index+1:]:
                if mode=='base':continue
                ids_a=original.get('identity',{})
                ids_b=altered.get('identity',{})
                shared=sorted(set(ids_a)&set(ids_b))
                # Replicate 0098._fuzz_loss sampling: all positive aligned pairs,
                # followed by the first 12 aligned negative pairs.
                positive,negative=[],[]
                for x in shared:
                    for y in shared:
                        if x==y:continue
                        ia,ja=ids_a[x],ids_a[y]
                        ib,jb=ids_b[x],ids_b[y]
                        ga=int(original['item']['targets'][ia,ja])
                        gb=int(altered['item']['targets'][ib,jb])
                        if ga!=gb:continue
                        entry=(ia,ja,ib,jb,ga)
                        (positive if ga else negative).append(entry)
                chosen=positive+negative[:12]
                for ia,ja,ib,jb,label in chosen:
                    left=tuple(int(original['predictions'][k][ia,ja])==label for k in experts)
                    right=tuple(int(altered['predictions'][k][ib,jb])==label for k in experts)
                    stats=Counter(
                        aligned=1,
                        positive=int(label>0),
                        negative=int(label==0),
                        competence_target_changed=int(left!=right),
                        individual_expert_flip=sum(a!=b for a,b in zip(left,right)),
                        incompatible_correct_sets=int(any(left) and any(right)
                            and not any(a and b for a,b in zip(left,right))),
                        one_variant_unanswerable=int(any(left)!=any(right)))
                    result.update(stats)
                    by_mode[mode].update(stats)
    return {'overall':dict(result), 'by_mode':{k:dict(v) for k,v in by_mode.items()},
            'interpretation':'The original fuzz penalty aligns competence '
              'distributions even when an expert changes from correct to '
              'incorrect on equivalent semantic targets. Inversions measure '
              'how often that constraint conflicts with the competence labels.'}


def _interpret(profile):
    c=profile['counts']
    opportunities=c['anchor_wrong_recoverable']
    right=c['winner_correct_on_recoverable']
    output=c['policy_repaired_anchor_wrong']
    gold_decode=c['full_gold_decoded_exact']
    return {
        'expert_selector_recovers_fraction_of_available_anchor_errors':right/max(1,opportunities),
        'end_to_end_recovers_fraction_of_available_anchor_errors':output/max(1,opportunities),
        'correct_winner_lost_by_policy_positions':c['corrected_winner_but_policy_wrong'],
        'decoder_destroys_correct_raw_position_count':c['decoder_destroyed_raw_correct'],
        'decoder_repairs_incorrect_raw_position_count':c['decoder_repaired_raw_wrong'],
        'decoder_can_reconstruct_gold_exact_rate':gold_decode/max(1,c['cases']),
        'oracle_pairs_exact_but_decoder_fails':c['oracle_gold_known_unreconstructable'],
        'likely_router_selection_bottleneck':right*2 < opportunities,
        'likely_score_or_assembly_bottleneck':c['corrected_winner_but_policy_wrong']>0,
        'do_not_infer_causality_from_dev_checkpoint_selection':True}


def probe(*,root=old.ROOT_DEFAULT,output=None,device='cuda',split='dev',
          write_report=True):
    check(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),
          'DEVICE_NOT_AVAILABLE')
    root,atlas_dir,commit,parent_dir,h95,h96,p97,previous,dest,provenance,report= _locate(root,output)
    cfg=provenance['settings']
    experts=m._choose_experts(previous,cfg['expert_set'],cfg['expert_limit'])
    check(list(experts)==list(m._choose_experts(previous,cfg['expert_set'],cfg['expert_limit'])),
          'NONDETERMINISTIC_EXPERT_ORDER')
    check(set(experts)==set(provenance['experts']), 'EXPERT_SET_DRIFT')
    check(all(experts[k]['sha256']==provenance['experts'][k]['sha256'] for k in experts),
          'EXPERT_HASH_DRIFT')
    router=m.CompetenceRouter(len(experts)).to(device)
    state=load_file(str(dest/'competence-router.safetensors'),device='cpu')
    router.load_state_dict(state,strict=True)
    router.eval()
    say('PROBE_START',{'split':split,'cases':len(provenance['selection'][split+'_case_ids']),
        'experts':list(experts),'selected_policy':report['training']['selected_policy'],
        'confirmation_used':False,'weights_retrained':False})
    paths=m.atlas._roots(root)
    parent_rec,_=hard._verify_trained(paths,paths['parent_0087'])
    network,tok=backbone.model_components(parent_rec['model_id'],paths['adapter'],device)
    composite=g1native.load_composite(paths['composite'],device=device)
    head,_=cur._load_checkpoint(parent_dir,device=device)
    old._mutable(head)
    spec=commit['lineage']['decoder']
    rows=old._get_rows(atlas_dir,set(provenance['selection'][split+'_case_ids']),split='discovery')
    items=old._capture(network,tok,composite,rows,device,must_exact=True)
    records=m._capture_experts(head,items,device,spec,experts,'0098-diagnosis-'+split,training=(split=='fit'))
    del items
    profile=_calculate(records,experts,spec,device,router,
                       report['training']['selected_policy'],split)
    interpretation=_interpret(profile)
    profile['interpretation']=interpretation
    if split=='fit':
        profile['fuzz_competence_target_contradictions']=_fuzz_contradictions(records,experts)
    # Emit diagnostics before writing a sidecar: if a filesystem failure occurs,
    # the expensive inference results remain visible in the console.
    say('COUNTS',profile['counts'])
    say('BY_FAMILY',profile['by_family'])
    say('BY_BASE_CLUSTER',profile['base_cluster'])
    say('BY_RELATION',profile['by_gold_relation'])
    say('EXPERT_CHOICES',profile['expert_choices'])
    if split=='fit':
        say('FUZZ_CONTRADICTIONS',profile['fuzz_competence_target_contradictions'])
    say('INTERPRETATION',interpretation)
    if write_report:
        # Write only a new diagnostic sidecar, never checkpoint or seal files.
        target = dest / ('routing-diagnosis-' + split + '.json')
        old.write(target, profile)
        say('SAVED', {'path': str(target), 'sha256': old.digest(target)})
    return profile


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',nargs='?',choices=('audit','probe'),default='audit')
    p.add_argument('--root',default=old.ROOT_DEFAULT)
    p.add_argument('--output')
    p.add_argument('--device',default='cuda')
    p.add_argument('--split',choices=('dev','fit'),default='dev')
    p.add_argument('--no-save',action='store_true')
    a=p.parse_args(argv)
    if a.command=='audit':return audit(root=a.root,output=a.output)
    return probe(root=a.root,output=a.output,device=a.device,split=a.split,
                 write_report=not a.no_save)

if __name__=='__main__':
    main()
