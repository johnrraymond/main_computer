"""G2-0086: frozen within-type destination discrimination and causal tracing.

Reads the *existing* G2-0085 evidence and replays the same deterministic prompts
through the frozen G2-0083 head. Contrasts real and false destination candidates
for a fixed source, destination kind and binding type. Intervenes at encoder,
coupler, and separate pair-input channels, with reciprocal and sham controls.

No training, model selection, checkpoint mutation, threshold fitting, promotion,
or certification. Gold is used only for post-inference comparison/target selection.

  python -m tools.nanojev_gamer_v1_g2_within_type_causal_discrimination_0086 --device cuda
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

import torch
import torch.nn.functional as F

from tools import nanojev_gamer_v1_g2_causal_activation_fuzz_0085 as fuzz
from tools import nanojev_gamer_v1_g2_native_training_0083 as prior
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_g1_native_composite_0082 as composite_module
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

SCHEMA='nanojev.gamer.graph.g2.within-type-causal-discrimination-0086.v1'
FOCUS=(('quantity','outcome'),('quantity','action'))
PAIR_CHANNELS={
    'destination_only':(1,),
    'interaction_only':(2,),
    'difference_only':(3,),
    'destination_and_interaction':(1,2),
    'destination_and_difference':(1,3),
    'all_destination_channels':(1,2,3),
}


def require(condition,code):
    if not condition:
        raise ValueError('G2_0086_'+code)


def _typed_margin(r,label):
    return float(r['raw_logits'][label]-r['raw_logits'][0])


def matching_groups(rows):
    """One discriminative comparison per (source, destination kind, binding type).

    Negatives must have the SAME predicted source and destination *kind* as
    positives. Every group reports best and worst positive ranks before any
    small diagnostic intervention subset is chosen. This avoids comparing an
    actor->action positive with an irrelevant quantity->quantity negative.
    """
    coords=set();ids=set();grouped=defaultdict(list)
    for r in rows:
        require(type(r['i']) is int and type(r['j']) is int and r['i']!=r['j'],
                'INVALID_PAIR_INDICES')
        require(len(r['raw_logits'])==len(BIND_CLASSES) and all(math.isfinite(v) for v in r['raw_logits']),
                'INVALID_PAIR_LOGITS')
        require(type(r['gold_label']) is int and 0<=r['gold_label']<len(BIND_CLASSES),
                'INVALID_GOLD_PAIR_CLASS')
        key=(r['i'],r['j'])
        require(key not in coords,'DUPLICATE_PAIR_INDICES')
        coords.add(key)
        obj=(r['i'],tuple(r['src']))
        require(len(r['src'])==3 and len(r['dst'])==3,'INVALID_PAIR_SPAN')
        ids.add(obj)
        grouped[(r['i'],r['dst'][2])].append(r)
    results=[]
    for (source,kind),items in sorted(grouped.items()):
        gold_by_kind=defaultdict(list)
        negatives=[]
        for r in items:
            (gold_by_kind[r['gold_label']] if r['gold_label'] else negatives).append(r)
        for label,positives in sorted(gold_by_kind.items()):
            if not negatives:continue
            best=max(positives,key=lambda r:(_typed_margin(r,label),-r['j']))
            weakest=min(positives,key=lambda r:(_typed_margin(r,label),r['j']))
            hard=max(negatives,key=lambda r:(_typed_margin(r,label),-r['j']))
            delta=_typed_margin(weakest,label)-_typed_margin(hard,label)
            group={
                'src_index':source,'src_kind':best['src'][2], 'dst_kind':kind,
                'binding_label':label,'binding_type':BIND_CLASSES[label],
                'positives':len(positives),'negatives':len(negatives),
                'best_true_index':best['j'],'weakest_true_index':weakest['j'],
                'hardest_false_index':hard['j'],
                'best_true_typed_margin':_typed_margin(best,label),
                'weakest_true_typed_margin':_typed_margin(weakest,label),
                'hardest_false_typed_margin':_typed_margin(hard,label),
                'worst_case_rank_margin':delta,
                'all_true_above_false':delta>0,
                'at_least_one_true_above_false':_typed_margin(best,label)>_typed_margin(hard,label),
                'ambiguous_exact_tie':delta==0,
                'source_anchor':best['src'],
                'true_destination_anchor':weakest['dst'],
                'false_destination_anchor':hard['dst'],
                '_true':weakest,'_false':hard,
                '_other_false':next((r for r in sorted(negatives,key=lambda r:
                         (-_typed_margin(r,label),r['j'])) if r['j']!=hard['j']),None),
            }
            results.append(group)
    return results


def public_group(g):
    return {k:v for k,v in g.items() if not k.startswith('_')}


def annotate_author_ids(group,graph):
    anchors={(*node['span'],node['kind']):node['id']
             for node in graph['nodes'] if 'span' in node}
    for label,key in (('source_author_id','source_anchor'),
                      ('true_destination_author_id','true_destination_anchor'),
                      ('false_destination_author_id','false_destination_anchor')):
        group[label]=anchors.get(tuple(group[key]))
    return group


def counterfactual_rank_stability(groups,case_rows):
    """Compare diagnostic groups on both sides of gold-invariant fuzz pairs.

    Count only exact-G1 comparisons; G1 error would confound G2 attribution.
    """
    cases={(r['pair_id'],r['side']):r for r in case_rows}
    paired=defaultdict(dict)
    for g in groups:
        key=(g['pair_id'],g['source_author_id'],g['dst_kind'],g['binding_type'])
        if g['source_author_id'] is None:continue
        require(g['side'] not in paired[key],'DUPLICATE_COUNTERFACTUAL_GROUP')
        paired[key][g['side']]=g
    aggregates=defaultdict(list)
    for (pid,_sid,_kind,_binding),sides in paired.items():
        if not ('base' in sides and 'fuzz' in sides):continue
        a,b=sides['base'],sides['fuzz']
        first,second=cases[(pid,'base')],cases[(pid,'fuzz')]
        if first['mode']=='action_removal' or not(first['g1_exact'] and second['g1_exact']):
            continue
        if (a['true_destination_author_id'] is None or
            a['true_destination_author_id']!=b['true_destination_author_id'] or
            a['positives']!=b['positives']):continue
        aggregates[first['mode']].append((a,b))
    def result(items):
        return {'compared':len(items),
                'rank_status_flipped':sum(a['all_true_above_false']!=b['all_true_above_false']
                    for a,b in items),
                'hardest_false_identity_changed':sum(a['false_destination_author_id']!=
                    b['false_destination_author_id'] for a,b in items),
                'mean_abs_margin_change':(sum(abs(a['worst_case_rank_margin']-
                    b['worst_case_rank_margin']) for a,b in items)/len(items)
                    if items else None)}
    all_pairs=[v for items in aggregates.values() for v in items]
    return {'all':result(all_pairs),'by_mode':{
                name:result(items) for name,items in sorted(aggregates.items())}}


def choose_targets(groups,limit):
    """Predeclared diagnostic priority, NOT a calibrator / a deployment policy."""
    require(type(limit) is int and 0<=limit<=64,'INVALID_INTERVENTION_LIMIT')
    # First, inverted quantity->outcome / quantity->action ranks, then other
    # failures. Include a correctly ranked group as an explicit positive control
    # where space allows. Gold labels are diagnostic only.
    def order(g):
        category=(g['src_kind'],g['dst_kind'])
        focus=(FOCUS.index(category) if category in FOCUS else len(FOCUS))
        return (g['all_true_above_false'],focus,g['worst_case_rank_margin'],
                g['src_index'],g['binding_label'],g['weakest_true_index'])
    ordered=sorted(groups,key=order)
    if limit==0:return []
    selected=ordered[:limit]
    if len(groups)>limit and all(not g['all_true_above_false'] for g in selected):
        ranked=[g for g in groups if g['all_true_above_false']]
        if ranked:
            selected[-1]=sorted(ranked,key=lambda g:
                (0 if (g['src_kind'],g['dst_kind']) in FOCUS else 1,
                 g['worst_case_rank_margin'],g['src_index']))[0]
    return selected


def summarize_group_ranks(groups):
    by=defaultdict(list)
    for g in groups:by[(g['src_kind'],g['dst_kind'])].append(g)
    return {'groups':len(groups),'all_true_above_false':sum(g['all_true_above_false'] for g in groups),
        'by_source_destination_kind':{
          f'{a}->{b}':{'groups':len(items),
              'all_true_above_false':sum(g['all_true_above_false'] for g in items),
              'some_true_above_false':sum(g['at_least_one_true_above_false'] for g in items),
              'single_true':sum(g['positives']==1 for g in items),
              'mean_worst_case_rank_margin':sum(g['worst_case_rank_margin'] for g in items)/len(items)}
          for (a,b),items in sorted(by.items())}}


def traced_pair_input(head,hidden,eligible,spans):
    """Preserve the *actual* pre-GELU 4-channel pair features, not a surrogate."""
    seen=[]
    def capture(_module,inputs):
        require(len(inputs)==1 and inputs[0].ndim==3 and
                inputs[0].shape[-1]==4*head.width,'PAIR_FEATURE_SHAPE')
        require(not seen,'DUPLICATE_PAIR_PROJECTION')
        seen.append(inputs[0].detach().clone())
    handle=head.pair_projection.register_forward_pre_hook(capture)
    try:
        output,caps=fuzz.trace_head(head,hidden,eligible,spans)
    finally:
        handle.remove()
    require(len(seen)==1,'PAIR_INPUT_NOT_CAPTURED')
    require(seen[0].shape[:2]==caps['pair_projection'].shape[:2],
            'PAIR_INPUT_NODE_SHAPE')
    caps['pair_input']=seen[0]
    return output,caps


def _pooled(v,spans,index,stage):
    t=v.squeeze(0) if stage=='encoder' else v
    s=spans[index]
    return t[s['token_first']:s['token_last']+1].float().mean(0)


def _relative_geometry(a,b):
    x=a.flatten().float();y=b.flatten().float()
    require(x.shape==y.shape and x.numel()>0,'GEOMETRY_DIMENSION')
    return {'distance':float(torch.linalg.vector_norm(x-y)),
            'cosine':float(F.cosine_similarity(x[None],y[None])[0]),
            'norm_true':float(torch.linalg.vector_norm(x)),
            'norm_false':float(torch.linalg.vector_norm(y)),
            'relative_separation':float(torch.linalg.vector_norm(x-y) /
                  ((torch.linalg.vector_norm(x)+torch.linalg.vector_norm(y))/2+1e-8))}


def trace_contrast(caps,spans,group):
    """Contrast exactly the same source with competing destinations at stages."""
    i=group['src_index'];a=group['weakest_true_index'];b=group['hardest_false_index']
    trace={}
    for stage in ('project','encoder','g1_g2_coupler'):
        if stage not in caps:continue
        trace[stage]={
            'destination':_relative_geometry(_pooled(caps[stage],spans,a,stage),
                                             _pooled(caps[stage],spans,b,stage)),
            'source_same_for_both_pairs':True}
    for stage in ('pair_input','pair_projection','binding_input'):
        v=caps[stage]
        trace[stage]={'whole_pair':_relative_geometry(v[i,a],v[i,b])}
        if stage=='pair_input':
            w=v.shape[-1]//4
            require(w*4==v.shape[-1],'PAIR_INPUT_WIDTH')
            trace[stage]['channels']={name:_relative_geometry(v[i,a,k*w:(k+1)*w],
                                        v[i,b,k*w:(k+1)*w])
                for k,name in enumerate(('source','destination','interaction','difference'))}
    p=caps['binding'].detach().float()
    label=group['binding_label']
    trace['readout']={
        'typed_true_margin':float(p[i,a,label]-p[i,a,0]),
        'typed_false_margin':float(p[i,b,label]-p[i,b,0]),
        'logit_delta_true_minus_false':float((p[i,a,label]-p[i,a,0])-
                                             (p[i,b,label]-p[i,b,0])),
        'true_argmax':int(p[i,a].argmax()),
        'false_argmax':int(p[i,b].argmax())}
    return trace


def _run_intervention(head,hidden,eligible,spans,*,stage,from_index,to_index,
                      pair_source,channels=None):
    """Hook a single downstream activation tensor and rerun frozen native G2.

    Encoder/coupler transplant: replace destination tokens with donor *mean*
    vector in same prompt. Length-agnostic but not a positional token swap.

    Pair-input transplant: modify only the requested channels of one pair.
    Channels represent [source, destination, source*destination, source-destination].
    """
    n=len(spans)
    require(type(from_index) is int and type(to_index) is int and
            type(pair_source) is int and all(0<=k<n for k in
            (from_index,to_index,pair_source)),'BAD_INTERVENTION_INDEX')
    require(stage in ('encoder','g1_g2_coupler','pair_input'),'BAD_INTERVENTION_STAGE')
    modules=fuzz.stage_modules(head)
    if stage=='g1_g2_coupler':require(stage in modules,'MISSING_COUPLER')
    if stage=='pair_input':
        require(isinstance(channels,tuple) and channels and
                all(type(c) is int and c in (0,1,2,3) for c in channels) and
                len(channels)==len(set(channels)),'BAD_CHANNEL_MASK')
    else:require(channels is None,'CHANNELS_ONLY_PAIR_INPUT')
    fired=[]
    def forward_swap(_mod,_ins,output):
        if stage=='g1_g2_coupler' and output.ndim!=2:return output
        require(not fired,'INTERVENTION_FIRED_TWICE')
        fired.append(1)
        out=output.clone()
        tok=out.squeeze(0) if stage=='encoder' else out
        donor=spans[from_index];target=spans[to_index]
        donor_mean=tok[donor['token_first']:donor['token_last']+1].mean(0).clone()
        if stage=='encoder':
            out[:,target['token_first']:target['token_last']+1,:]=donor_mean
        else:
            out[target['token_first']:target['token_last']+1,:]=donor_mean
        return out
    def pair_swap(_mod,inputs):
        require(not fired and len(inputs)==1 and inputs[0].ndim==3 and
                inputs[0].shape[-1]==4*head.width,'PAIR_PREHOOK_SHAPE')
        fired.append(1)
        x=inputs[0];out=x.clone();w=head.width
        for ch in channels:
            out[pair_source,to_index,ch*w:(ch+1)*w]=x[pair_source,from_index,ch*w:(ch+1)*w]
        return (out,)
    module=head.pair_projection if stage=='pair_input' else modules[stage]
    hook=(module.register_forward_pre_hook(pair_swap) if stage=='pair_input' else
          module.register_forward_hook(forward_swap))
    try:
        with torch.inference_mode():
            out=head(hidden,eligible=eligible,inference_spans=spans,active_stage='G2')
    finally:
        hook.remove()
    require(len(fired)==1 and out['surface_count']==n,'INTERVENTION_NOT_EXECUTED')
    return out['binding_logits'].detach().float()


def _local_pair_input_swap(head,caps,*,from_index,to_index,pair_source,channels):
    """Exact native G2 readout from cached pair features (no full-head replay).

    After pair_projection's input, G2 binding is strictly elementwise:
    binding(GELU(pair_projection(pair_features))). A one-pair intervention
    cannot affect any other pair. Validate this equivalence in regression tests
    against the full real forward hook before using the fast diagnostic path.
    """
    x=caps['pair_input']
    require(x.ndim==3 and x.shape[-1]==4*head.width,'CACHED_PAIR_FEATURE_SHAPE')
    v=x[pair_source,to_index].clone();donor=x[pair_source,from_index]
    for channel in channels:
        a=channel*head.width;b=a+head.width
        v[a:b]=donor[a:b]
    with torch.inference_mode():
        logit=head.binding(F.gelu(head.pair_projection(v)))
    result=caps['binding'].detach().float().clone()
    result[pair_source,to_index]=logit.detach().float()
    return result


def interventions(head,hidden,eligible,spans,caps,group,*,include_pair_controls=True):
    """Reciprocal target/donor swaps plus sham and wrong-donor controls.

    No gold labels enter network computation: they only select the diagnostic
    correct/wrong donor objects. Outcomes are evidence, never a learned policy.
    """
    i=group['src_index'];p=group['weakest_true_index'];n=group['hardest_false_index']
    label=group['binding_label'];base=caps['binding'].detach().float()
    plans=[]
    for direction,donor,target in (('true_to_false',p,n),('false_to_true',n,p)):
        for stage in ('encoder','g1_g2_coupler'):
            if stage in caps:
                plans.append((direction,stage,stage,donor,target,None))
        for name,channels in PAIR_CHANNELS.items():
            plans.append((direction,name,'pair_input',donor,target,channels))
        # Never deploy a correctness label-conditioned transplant.
    if include_pair_controls:
        plans.append(('sham_false','self_pair_input_control','pair_input',n,n,(1,2,3)))
        other=group.get('_other_false')
        if other is not None:
            plans.append(('negative_control','wrong_to_wrong_pair_input','pair_input',
                          other['j'],n,(1,2,3)))
    records=[]
    for direction,name,stage,donor,target,channels in plans:
        if stage=='pair_input':
            changed=_local_pair_input_swap(head,caps,from_index=donor,to_index=target,
                    pair_source=i,channels=channels)
        else:
            changed=_run_intervention(head,hidden,eligible,spans,stage=stage,
                 from_index=donor,to_index=target,pair_source=i,channels=channels)
        before=float(base[i,target,label]-base[i,target,0])
        after=float(changed[i,target,label]-changed[i,target,0])
        before_other=float(base[i,donor,label]-base[i,donor,0])
        after_other=float(changed[i,donor,label]-changed[i,donor,0])
        count=int((fuzz._decision_matrix(changed[:len(spans),:len(spans)])!=
             fuzz._decision_matrix(base[:len(spans),:len(spans)])).sum().item())
        denom=before_other-before
        transfer_fraction=((after-before)/denom if abs(denom)>1e-7 else None)
        output={
            'direction':direction,'operation':name,'stage':stage,
            'pair_source_index':i,'donor_index':donor,'target_index':target,
            'binding_label':label,'binding_type':BIND_CLASSES[label],
            'baseline_typed_margin':before,'intervened_typed_margin':after,
            'delta_typed_margin':after-before,
            'donor_minus_target_margin':denom,
            'donor_margin_transfer_fraction':transfer_fraction,
            'baseline_decision':fuzz._decision(base[i,target]),
            'intervened_decision':fuzz._decision(changed[i,target]),
            'other_pair_margin_delta':after_other-before_other,
            'total_graph_decision_changes':count,
            'no_checkpoint_mutation':True,
        }
        if stage=='pair_input':
            # The pair prehook must not change ANY other pair readout.
            mask=torch.ones(base.shape[:2],dtype=torch.bool,device=base.device)
            mask[i,target]=False
            require(torch.allclose(changed[mask],base[mask],atol=1e-5,rtol=1e-5),
                    'PAIR_INTERVENTION_LEAKAGE')
            output['other_pair_outputs_unchanged']=True
        records.append(output)
    return records


def verify_evidence(source,head_sha,source_code_sha):
    """Ensure frozen diagnostic context is exactly the saved G2-0085 run."""
    require(source.is_dir(),'G2_0085_NOT_FOUND')
    paths=[source/'report.json',source/'cases.jsonl',source/'pair-scores.jsonl']
    require(all(p.is_file() for p in paths),'G2_0085_INCOMPLETE_EVIDENCE')
    report=prior.js(paths[0])
    require(report.get('schema')==fuzz.SCHEMA and
            report.get('status')=='FROZEN_G2_ACTIVATION_FUZZ_DIAGNOSIS_ONLY' and
            report.get('checkpoint_sha256')==head_sha and
            report.get('source_code_sha256')==source_code_sha and
            report.get('no_train') is True and report.get('no_checkpoint_mutation') is True,
            'G2_0085_EVIDENCE_PROVENANCE')
    cases=prior.load_jsonl(paths[1]);score_records=prior.load_jsonl(paths[2])
    require(len(cases)==len(score_records)==2*report['pairs'],
            'G2_0085_EVIDENCE_CASE_COUNT')
    by_case={};by_score={}
    for case,score in zip(cases,score_records):
        key=(case['pair_id'],case['side'])
        require(key not in by_case and case['prompt_sha256']==score['prompt_sha256'] and
                key==(score['pair_id'],score['side']) and
                hashlib.sha256(case['prompt'].encode()).hexdigest()==case['prompt_sha256'],
                'G2_0085_CASE_JOIN')
        require(len(score['scores'])==case['pair_metrics']['candidate_pairs'],
                'G2_0085_SCORE_COUNT')
        require(len({(r['i'],r['j']) for r in score['scores']})==len(score['scores']),
                'G2_0085_DUPLICATE_SCORE_PAIR')
        summary=fuzz.summarize_pairs(score['scores'])
        require(summary['outcomes']==case['pair_metrics']['outcomes'] and
                summary['predicted_edges']==case['pair_metrics']['predicted_edges'],
                'G2_0085_SCORE_SUMMARY_DRIFT')
        by_case[key]=case;by_score[key]=score
    return report,by_case,by_score,{p.name:prior.sha(p) for p in paths}


def _assert_replay(rows,old,case):
    require(len(rows)==len(old['scores'])==case['pair_metrics']['candidate_pairs'],
            'REPLAY_CASE_COUNT')
    for current,expected in zip(rows,old['scores']):
        require((current['i'],current['j'],current['src'],current['dst'],
                 current['gold_label'],current['pred_label'])==
                (expected['i'],expected['j'],expected['src'],expected['dst'],
                 expected['gold_label'],expected['pred_label']),'REPLAY_CASE_DRIFT')
        require(all(abs(a-b)<1e-3 for a,b in zip(current['raw_logits'],expected['raw_logits'])),
                'REPLAY_LOGIT_DRIFT')


def _aggregate(rows):
    mode=Counter();effect=defaultdict(lambda:defaultdict(list))
    geometry=defaultdict(lambda:defaultdict(list))
    for r in rows:
        # run() appends one selected comparison per match_record, not a
        # per-case collection.  The JSONL output's public schema is `group`
        # (singular).  Aggregate that same schema, rather than the outdated
        # `groups` list used by an early unit-test fixture.
        require('group' in r and isinstance(r['group'],dict) and
                type(r['group'].get('all_true_above_false')) is bool and
                isinstance(r.get('stage_contrast'),dict) and
                isinstance(r.get('interventions'),list),
                'INVALID_MATCH_RECORD')
        mode['groups']+=1
        mode['reversed_rank']+=not r['group']['all_true_above_false']
        for stage,entry in r['stage_contrast'].items():
            if stage=='readout':continue
            vector=(entry['destination'] if stage in ('project','encoder','g1_g2_coupler')
                else entry['whole_pair'])
            geometry[stage]['distance'].append(vector['distance'])
            geometry[stage]['cosine'].append(vector['cosine'])
            geometry[stage]['relative_separation'].append(vector['relative_separation'])
        for intervention in r['interventions']:
            k=(intervention['direction'],intervention['operation'])
            effect[k]['delta'].append(intervention['delta_typed_margin'])
            effect[k]['changes'].append(intervention['total_graph_decision_changes'])
            effect[k]['other'].append(intervention['other_pair_margin_delta'])
            if intervention['donor_margin_transfer_fraction'] is not None:
                effect[k]['transfer'].append(intervention['donor_margin_transfer_fraction'])
    return {'intervened_groups':mode['groups'],'inverted_ranks':mode['reversed_rank'],
        'stage_destination_or_pair_geometry':{
            stage:{'n':len(d['distance']),
                **{'mean_'+name:sum(values)/len(values) for name,values in d.items()}}
            for stage,d in sorted(geometry.items())},
        'by_intervention':{
          direction+':'+op:{'n':len(v['delta']),
            'mean_delta_typed_margin':sum(v['delta'])/len(v['delta']),
            'mean_graph_decision_changes':sum(v['changes'])/len(v['changes']),
            'mean_donor_pair_margin_shift':sum(v['other'])/len(v['other']),
            'mean_margin_transfer_fraction':(sum(v['transfer'])/len(v['transfer'])
                 if v['transfer'] else None)}
          for (direction,op),v in sorted(effect.items())}}


def run(*,root='../runtime/reports/gamer-v1/oracle-fabric-g2-native-training-0083',
        source='../runtime/reports/gamer-v1/oracle-fabric-g2-causal-activation-fuzz-0085',
        output='../runtime/reports/gamer-v1/oracle-fabric-g2-within-type-causal-discrimination-0086',
        device='cuda',max_length=2048,max_pairs_per_case=3):
    root=Path(root).resolve();source=Path(source).resolve();output=Path(output).resolve()
    require(not output.exists(),'OUTPUT_IMMUTABLE')
    require(type(max_pairs_per_case) is int and 0<=max_pairs_per_case<=16,
            'INVALID_MAX_PAIRS_PER_CASE')
    require(type(max_length) is int and 128<=max_length<=8192,
            'INVALID_MAX_LENGTH')
    require(root.is_dir(),'G2_0083_NOT_FOUND')
    paths=prior.defaults(root.parent)
    parent,proof=prior.preflight(paths)
    seal=prior.check_seal(paths,root,expected_head=parent['head_sha256'])
    trained=root/'training'
    head,manifest=cur._load_checkpoint(trained,device=device)
    require(manifest['stage']=='G2' and manifest['head_sha256']==prior.sha(trained/cur.HEAD_FILE)
            and manifest['native_composite_g1']['mode']=='predicted_G1_spans_native_G2_binding',
            'WRONG_G2_CHECKPOINT')
    prior.compare_frozen_stage(paths['g1'],trained)
    require(manifest['native_composite_g1']['certificate_sha256']==prior.sha(paths['certificate'])
            and seal['native_certificate_sha256']==prior.sha(paths['certificate']),
            'G1_CERTIFICATE_DRIFT')
    failure_report=prior.js(root/'report.json')
    require(failure_report.get('schema')==prior.SCHEMA and
            failure_report.get('status')=='G2_PREDICTED_SPAN_TRAINED_BUT_GATE_BLOCKED' and
            failure_report.get('g2_checkpoint_sha256')==manifest['head_sha256'] and
            failure_report.get('g3_training_unlocked') is False,'G2_0083_FAILURE_REQUIRED')
    evidence,by_case,by_score,hashes=verify_evidence(source,manifest['head_sha256'],
                  prior.sha(Path(fuzz.__file__)))
    require(evidence['source_g2_failure_report_sha256']==prior.sha(root/'report.json') and
            evidence['g1_native_certificate_sha256']==prior.sha(paths['certificate']),
            'G2_0085_ROOT_PROVENANCE')
    composite=composite_module.load_composite(paths['composite'],device=device)
    require(composite.manifest['head_sha256']==parent['head_sha256'],
            'COMPOSITE_G1_PARENT_DRIFT')
    head.eval().requires_grad_(False)
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    require(not any(p.requires_grad for p in model.parameters()),'BACKBONE_NOT_FROZEN')
    experiments=fuzz.make_pairs(seed=evidence['seed'],pairs=evidence['pairs'])
    case_records=[];match_records=[];all_groups=[];all_interventions=[]
    for experiment in experiments:
        for side in ('base','fuzz'):
            goldrow=experiment[side]
            key=(experiment['id'],side)
            case=by_case[key];old=by_score[key]
            require(case['prompt']==goldrow['prompt'] and
                    case['family']==experiment['family'] and
                    case['mode']==experiment['mode'],'ORIGINAL_FUZZ_PROMPT_DRIFT')
            prepared,hidden=prior._capture_inference(model,tok,goldrow['prompt'],device,max_length)
            with torch.inference_mode():
                selection=composite.predict(hidden,prepared,family=experiment['family'],
                              native_stage='G1')
            spans=selection['token_spans'];elig=prepared['eligible'].to(device)
            _,caps=traced_pair_input(head,hidden,elig,spans)
            rows=fuzz._pair_stats(caps['binding'],spans,prepared,goldrow['graph'])
            _assert_replay(rows,old,case)
            groups=[annotate_author_ids(g,goldrow['graph']) for g in matching_groups(rows)]
            chosen=choose_targets(groups,max_pairs_per_case)
            case_entry={'pair_id':experiment['id'],'side':side,
                'mode':experiment['mode'],'family':experiment['family'],
                'g1_exact':case['g1_exact'],'prompt_sha256':case['prompt_sha256'],
                'candidate_pairs':len(rows),'matching_groups':len(groups),
                'inverted_rank_groups':sum(not g['all_true_above_false'] for g in groups),
                'intervened_groups':len(chosen)}
            case_records.append(case_entry)
            for g in groups:
                item=public_group(g)
                all_groups.append({'pair_id':experiment['id'],'side':side,**item})
            for g in chosen:
                trace=trace_contrast(caps,spans,g)
                swaps=interventions(head,hidden,elig,spans,caps,g)
                match_records.append({'pair_id':experiment['id'],'side':side,
                    'prompt_sha256':case['prompt_sha256'],
                    'g1_exact':case['g1_exact'],
                    'group':public_group(g),'stage_contrast':trace,
                    'interventions':swaps})
                all_interventions.extend(swaps)
            del hidden,caps
            print('G2_0086_TRACE '+json.dumps({'pair_id':experiment['id'],'side':side,
                  'groups':len(groups),'inverted':case_entry['inverted_rank_groups'],
                  'interventions':len(chosen)}),flush=True)
    require(prior.sha(trained/cur.HEAD_FILE)==manifest['head_sha256'] and
            prior.sha(paths['certificate'])==evidence['g1_native_certificate_sha256'] and
            all(prior.sha(source/name)==digest for name,digest in hashes.items()),
            'FROZEN_EVIDENCE_MUTATED')
    summary={'rankings':summarize_group_ranks(all_groups),
             'interventions':_aggregate(match_records),
             'cases':len(case_records),'matched_comparisons':len(all_groups),
             'intervened_comparisons':len(match_records),
             'source_g1_exact_cases':sum(c['g1_exact'] for c in case_records),
             'counterfactual_rank_stability':counterfactual_rank_stability(all_groups,case_records)}
    report={'schema':SCHEMA,'status':'FROZEN_G2_WITHIN_TYPE_CAUSAL_DIAGNOSIS_ONLY',
        'checkpoint_sha256':manifest['head_sha256'],
        'source_0085_evidence_sha256':hashes,
        'source_0085_code_sha256':prior.sha(Path(fuzz.__file__)),
        'source_0083_failure_report_sha256':prior.sha(root/'report.json'),
        'native_g1_certificate_sha256':prior.sha(paths['certificate']),
        'source_code_sha256':prior.sha(Path(__file__)),
        'g1_policy':'native_predicted_G1; held_fixed_per_intervention',
        'gold_use':'after_inference_for_same_source_type_matching_and_donor_choice_only',
        'train':False,'checkpoint_mutation':False,'threshold_fit':False,
        'independent_certification':False,'g2_certified':False,'g3_unlocked':False,
        'intervention_policy':'diagnostic_oracle_selected_transplants_NOT_DEPLOYABLE',
        'controls':'reciprocal_dest_transplants; same_pair_sham; wrong_to_wrong_if_available',
        'max_pairs_per_case':max_pairs_per_case,'summary':summary}
    output.mkdir(parents=True,exist_ok=False)
    prior.write_json(output/'report.json',report)
    prior.write_jsonl(output/'groups.jsonl',all_groups)
    prior.write_jsonl(output/'matches.jsonl',match_records)
    prior.write_jsonl(output/'cases.jsonl',case_records)
    print('G2_0086_DIAGNOSIS '+json.dumps({'status':report['status'],
        'checkpoint_sha256':manifest['head_sha256'],
        'cases':summary['cases'],'matched_comparisons':summary['matched_comparisons'],
        'intervened_comparisons':summary['intervened_comparisons'],
        'g3_unlocked':False},sort_keys=True),flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',default='../runtime/reports/gamer-v1/oracle-fabric-g2-native-training-0083')
    p.add_argument('--source',default='../runtime/reports/gamer-v1/oracle-fabric-g2-causal-activation-fuzz-0085')
    p.add_argument('--output',default='../runtime/reports/gamer-v1/oracle-fabric-g2-within-type-causal-discrimination-0086')
    p.add_argument('--device',default='cuda')
    p.add_argument('--max-length',type=int,default=2048)
    p.add_argument('--max-pairs-per-case',type=int,default=3)
    return run(**vars(p.parse_args(argv)))


if __name__=='__main__':main()
