"""G2-0085: frozen G2 causal activation fuzzing (diagnostic, never promotion).

Generate new author-annotated counterfactuals, infer G1 without gold, and trace
native G2 representations and binding logits. Interventions hold predicted G1
spans fixed. Gold labels enter ONLY post-inference attribution / target choice.

  python -m tools.nanojev_gamer_v1_g2_causal_activation_fuzz_0085 --device cuda

No optimizer, gradient step, checkpoint write, decoder-selection or stage gate.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import random

import torch
import torch.nn.functional as F

from tools import nanojev_gamer_v1_g2_native_training_0083 as prior
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_g1_native_composite_0082 as composite_module
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_contract import BINDINGS
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds, BIND_CLASSES

SCHEMA='nanojev.gamer.graph.g2.causal-activation-fuzz-0085.v1'
SEED=850085
MODES=('renaming','role_swap','added_distractor','action_removal')
STAGES=('project','encoder','g1_g2_coupler','pair_projection','binding')


def require(value,code):
    if not value:raise ValueError('G2_0085_'+code)


def make_pairs(*, seed=SEED, pairs=8):
    """Author gold is for post-hoc measurement, never network inference."""
    require(type(seed) is int and type(pairs) is int and pairs>=4 and pairs%4==0,
            'DESIGN_PAIRS_MULTIPLE_OF_FOUR')
    rng=random.Random(seed); result=[];seen=set()
    for i in range(pairs):
        mode=MODES[i%4]
        family=('encounter','allocation')[(i//4+i)%2]
        if mode=='action_removal':family='allocation'
        if mode in ('renaming','role_swap'):
            left,right=corpus._counterfactual_pair(rng,family,mode,
                 850000+i,name_pool=corpus.NAME_POOLS['test'])
        elif mode=='added_distractor':
            factory=corpus.encounter if family=='encounter' else corpus.allocation
            left=factory(random.Random(rng.randrange(2**31)),'test',850000+i)
            right=deepcopy(left)
            decoy=rng.choice(corpus.NAME_POOLS['test'])
            right['prompt']+=' Independent note: '+decoy+' is unrelated to the listed decision.'
        else:
            names=rng.sample(corpus.NAME_POOLS['test'],3)
            actor=rng.choice(corpus.ACTORS_TEST)
            s=rng.randrange(2**31)
            left=corpus.allocation(random.Random(s),'test',850000+i,
                                   actor_override=actor,names_override=names)
            right=corpus.allocation(random.Random(s),'test',850000+i,
                                    actor_override=actor,names_override=names[:2])
        require(left['prompt']!=right['prompt'] and
                left['annotation_provenance']==right['annotation_provenance']==
                'AUTHOR_MARKED_IN_SOURCE_GENERATOR','INVALID_FUZZ_PAIR')
        for r in (left,right):
            digest=hashlib.sha256(r['prompt'].encode()).hexdigest()
            require(digest not in seen,'DUPLICATE_FUZZ_PROMPT')
            seen.add(digest)
            # Gold endpoint objects must have unique author-coordinate anchors.
            spans=[(x['start'],x['end'],x['kind']) for x in r['object_spans']]
            require(len(spans)==len(set(spans)),'DUPLICATE_GOLD_OBJECT')
        if mode!='action_removal':
            require([(e['src'],e['dst'],e['type']) for e in left['graph']['edges']
                     if e['type'] in BINDINGS]==
                    [(e['src'],e['dst'],e['type']) for e in right['graph']['edges']
                     if e['type'] in BINDINGS], 'INVARIANT_RELATIONSHIP_DRIFT')
        else:
            require(len(prior._gold_edges(right['graph']))<
                    len(prior._gold_edges(left['graph'])),'ACTION_REMOVAL_NO_EDGE_CHANGE')
        result.append({'id':f'g2-0085-{i+1:04}', 'mode':mode,'family':family,
                       'base':left,'fuzz':right})
    return result


def stage_modules(head):
    result={'project':head.project,'encoder':head.encoder,
            'pair_projection':head.pair_projection,'binding':head.binding}
    if head.interface_couplers is not None:
        result['g1_g2_coupler']=head.interface_couplers['G1_G2']
    return result


def _freeze_captured(t):
    require(isinstance(t,torch.Tensor),'HOOK_OUTPUT_NOT_TENSOR')
    return t.detach().clone()


def trace_head(head,hidden,eligible,spans):
    """Observe actual forward activations, with no replacement or gradients."""
    captures={};handles=[]
    def hook(name):
        def observe(_module,inputs,output):
            # Coupler is invoked once for tokens and once for global state;
            # capture only the token-branch activation.
            if name=='g1_g2_coupler' and output.ndim!=2:return
            require(name not in captures,'DUPLICATE_STAGE_'+name)
            captures[name]=_freeze_captured(output)
            if name=='binding':captures['binding_input']=_freeze_captured(inputs[0])
        return observe
    try:
        for name,module in stage_modules(head).items():
            handles.append(module.register_forward_hook(hook(name)))
        with torch.inference_mode():
            output=head(hidden,eligible=eligible,inference_spans=spans,active_stage='G2')
    finally:
        for h in handles:h.remove()
    require(set(stage_modules(head)) <= set(captures),'INCOMPLETE_ACTIVATION_TRACE')
    require(torch.equal(captures['binding'],output['binding_logits']),
            'CAPTURED_LOGITS_DIFFER')
    require(output['surface_count']==len(spans),'G1_SPANS_NOT_CONSUMED')
    return output,captures


def _pairs_by_gold(graph):
    nodes={n['id']:n for n in graph['nodes'] if 'span' in n}
    edges={}
    for item in graph['edges']:
        if item['type'] not in BINDINGS or item['src'] not in nodes or item['dst'] not in nodes:
            continue
        src=nodes[item['src']];dst=nodes[item['dst']]
        a=(src['span'][0],src['span'][1],src['kind'])
        b=(dst['span'][0],dst['span'][1],dst['kind'])
        key=(a,b)
        require(key not in edges or edges[key]==item['type'],'MULTILABEL_G2_PAIR')
        edges[key]=item['type']
    return edges


def _decision(logits):
    probabilities=logits.softmax(-1)
    label=int(logits.argmax(-1))
    return label if label and float(probabilities[label])>=0.5 else 0


def _pair_stats(logits,spans,prepared,gold_graph):
    """All directed G1-predicted surface pairs; gold only used afterwards."""
    keys=[]
    for s in spans:
        bounds=token_span_char_bounds(s,prepared)
        require(bounds is not None,'UNMAPPABLE_PREDICTED_OBJECT')
        keys.append((*bounds,s['kind']))
    require(len(keys)==len(set(keys)),'DUPLICATE_PREDICTED_OBJECT')
    gold_edges=_pairs_by_gold(gold_graph)
    rows=[];n=len(keys)
    require(logits.ndim==3 and logits.shape[0]==logits.shape[1] and
            logits.shape[0]>=n and logits.shape[2]==len(BIND_CLASSES),
            'BIND_LOGIT_DIMENSION')
    cpu_logits=logits[:n,:n].detach().float().cpu()
    for i in range(n):
        for j in range(n):
            if i==j:continue
            logit=cpu_logits[i,j]
            prob=logit.softmax(-1)
            edge_label=int(torch.argmax(logit[1:]))+1
            decision=_decision(logit)
            truth_name=gold_edges.get((keys[i],keys[j]))
            gold_label=BIND_CLASSES.index(truth_name) if truth_name is not None else 0
            margin=float(logit[edge_label]-logit[0])
            rows.append({'i':i,'j':j,'src':list(keys[i]),'dst':list(keys[j]),
                'gold_label':gold_label,'pred_label':decision,
                'raw_logits':[float(x) for x in logit],
                'edge_margin':margin,'edge_probability':float(prob[edge_label]),
                'null_probability':float(prob[0]),
                'outcome':('tp' if gold_label and decision==gold_label else
                           'wrong_type' if gold_label and decision else
                           'fn' if gold_label else 'fp' if decision else 'tn')})
    return rows


def rank_auc(positives,negatives):
    """Mann–Whitney ROC AUC, proper half-credit for ties; null if undefined."""
    if not positives or not negatives:return None
    series=sorted([(float(s),1) for s in positives]+[(float(s),0) for s in negatives])
    num_neg=0;wins=0.;i=0
    while i<len(series):
        j=i+1
        while j<len(series) and series[j][0]==series[i][0]:j+=1
        pos=sum(label for _,label in series[i:j]);neg=(j-i)-pos
        wins+=pos*(num_neg+neg/2)
        num_neg+=neg;i=j
    return wins/(len(positives)*len(negatives))


def summarize_pairs(rows):
    count=Counter(r['outcome'] for r in rows)
    pos=[r['edge_margin'] for r in rows if r['gold_label']]
    neg=[r['edge_margin'] for r in rows if not r['gold_label']]
    return {'candidate_pairs':len(rows),'outcomes':dict(count),
            'gold_positive':len(pos),'gold_negative':len(neg),
            'margin_auc':rank_auc(pos,neg),
            'mean_positive_margin':sum(pos)/len(pos) if pos else None,
            'mean_negative_margin':sum(neg)/len(neg) if neg else None,
            'predicted_edges':sum(r['pred_label']!=0 for r in rows)}


def _trace_features(captures,spans,rows,limit=16):
    """Compact, stage-local geometry; never serialize hidden activation arrays."""
    result=[]
    chosen=(sorted((r for r in rows if r['outcome']=='fp'),key=lambda r:-r['edge_margin'])[:limit//2]
           +sorted((r for r in rows if r['outcome']=='tp'),key=lambda r:r['edge_margin'])[:limit//2])
    for r in chosen:
        item={'src_index':r['i'],'dst_index':r['j'],'outcome':r['outcome'],
              'binding_margin':r['edge_margin'],'stages':{}}
        for name,v in captures.items():
            if name in ('project','g1_g2_coupler','encoder'):
                t=v.squeeze(0) if name=='encoder' else v
                a,b=spans[r['i']],spans[r['j']]
                src=t[a['token_first']:a['token_last']+1].mean(0)
                dst=t[b['token_first']:b['token_last']+1].mean(0)
                item['stages'][name]={'source_target_distance':float(torch.linalg.vector_norm(src-dst)),
                     'cosine':float(F.cosine_similarity(src[None],dst[None])[0])}
            elif name in ('pair_projection','binding_input'):
                item['stages'][name]={'pair_norm':float(torch.linalg.vector_norm(v[r['i'],r['j']]))}
            elif name=='binding':
                item['stages'][name]={'null_logit':float(v[r['i'],r['j'],0]),
                        'best_edge_logit':float(v[r['i'],r['j'],1:].max())}
        result.append(item)
    return result



def _representation_probe(captures,spans,rows):
    """Post-hoc class geometry; centroid statistics use evaluation labels.

    These are diagnostics, NOT proof a trainable decoder could generalize.
    Equal stage values cannot distinguish gold edge from null; a nonzero
    centroid contrast only shows information might be present.
    """
    if not rows:return {}
    output={}
    for stage in ('project','encoder','g1_g2_coupler','pair_projection','binding_input'):
        if stage not in captures:continue
        data=captures[stage]
        if stage=='encoder':data=data.squeeze(0)
        positives=[];negatives=[]
        for r in rows:
            i,j=r['i'],r['j']
            if stage in ('pair_projection','binding_input'):
                vector=data[i,j].float()
            else:
                a,b=spans[i],spans[j]
                left=data[a['token_first']:a['token_last']+1].mean(0).float()
                right=data[b['token_first']:b['token_last']+1].mean(0).float()
                vector=torch.cat((left,right,left*right,left-right))
            (positives if r['gold_label'] else negatives).append(vector)
        if not positives or not negatives:
            output[stage]={'positive':len(positives),'negative':len(negatives),
                           'centroid_separation':None}
            continue
        pos=torch.stack(positives);neg=torch.stack(negatives)
        pc=pos.mean(0);nc=neg.mean(0)
        within=(torch.linalg.vector_norm(pos-pc,dim=-1).mean()+
                torch.linalg.vector_norm(neg-nc,dim=-1).mean())/2
        distance=torch.linalg.vector_norm(pc-nc)
        output[stage]={'positive':len(positives),'negative':len(negatives),
            'class_centroid_distance':float(distance),
            'within_class_radius':float(within),
            'centroid_separation':float(distance/(within+1e-8)),
            'in_sample_gold_conditioned':True}
    return output


def _author_vectors(graph,prepared,spans,captures):
    """Temporary author-ID alignment only for controlled fuzz comparison."""
    matched={}
    by_key={(*token_span_char_bounds(s,prepared),s['kind']):i for i,s in enumerate(spans)}
    for node in graph['nodes']:
        if 'span' not in node:continue
        key=(node['span'][0],node['span'][1],node['kind'])
        if key in by_key:matched[node['id']]=by_key[key]
    vectors={};pairs={}
    for name in ('project','encoder','g1_g2_coupler'):
        if name not in captures:continue
        data=captures[name].squeeze(0) if name=='encoder' else captures[name]
        vectors[name]={id:data[spans[i]['token_first']:spans[i]['token_last']+1].mean(0)
                       for id,i in matched.items()}
    for name in ('pair_projection','binding_input'):
        if name not in captures:continue
        pairs[name]={(id,other):captures[name][i,j]
                     for id,i in matched.items() for other,j in matched.items() if id!=other}
    return {'objects':vectors,'pairs':pairs}


def _paired_activation_shift(a,b):
    """No learned alignment: only author-ID-matched objects/pairs qualify."""
    result={}
    for kind in ('objects','pairs'):
        for stage in sorted(set(a[kind])&set(b[kind])):
            left=a[kind][stage];right=b[kind][stage]
            shared=sorted(set(left)&set(right))
            if not shared:continue
            cos=[];distance=[]
            for key in shared:
                x=left[key].float();y=right[key].float()
                require(x.shape==y.shape,'FUZZ_STAGE_SHAPE_MISMATCH')
                cos.append(float(F.cosine_similarity(x.unsqueeze(0),y.unsqueeze(0),dim=-1)[0]))
                distance.append(float(torch.linalg.vector_norm(x-y)))
            result[stage]={'matched_'+kind:len(shared),
                           'mean_cosine':sum(cos)/len(cos),
                           'mean_l2_shift':sum(distance)/len(distance)}
    return result


def _decision_matrix(logits):
    label=logits.argmax(-1)
    selected=logits.softmax(-1).gather(-1,label.unsqueeze(-1)).squeeze(-1)
    return torch.where((label>0)&(selected>=0.5),label,0)

def _patch_head(head,hidden,eligible,spans,*,stage,src_index=None,pair=None,
                replacement=None):
    """Zero object-token evidence or transplant exactly one pair representation."""
    require(stage in ('encoder','g1_g2_coupler','pair_projection'),'BAD_PATCH_STAGE')
    modules=stage_modules(head)
    require(stage in modules,'MISSING_PATCH_STAGE')
    if stage!='pair_projection':
        require(type(src_index) is int and 0<=src_index<len(spans),'BAD_TOKEN_TARGET')
        span=spans[src_index]
    else:
        require(isinstance(pair,tuple) and len(pair)==2 and
                all(type(k) is int and 0<=k<len(spans) for k in pair) and pair[0]!=pair[1],
                'BAD_PAIR_TARGET')
        if replacement is not None:
            require(isinstance(replacement,torch.Tensor) and replacement.ndim==1 and
                    replacement.numel()==head.width,'BAD_REPLACEMENT_SHAPE')
    fired=[0]
    def apply(_module,_inputs,output):
        if stage=='g1_g2_coupler' and output.ndim!=2:return output
        require(fired[0]==0,'PATCH_STAGE_DUPLICATE')
        fired[0]+=1
        new=output.clone()
        if stage=='encoder':
            new[:,span['token_first']:span['token_last']+1,:]=0
        elif stage=='g1_g2_coupler':
            new[span['token_first']:span['token_last']+1,:]=0
        elif replacement is None:
            new[pair[0],pair[1],:]=0
        else:
            new[pair[0],pair[1],:]=replacement.to(device=new.device,dtype=new.dtype)
        return new
    handle=modules[stage].register_forward_hook(apply)
    try:
        with torch.inference_mode():
            out=head(hidden,eligible=eligible,active_stage='G2',inference_spans=spans)
    finally:
        handle.remove()
    require(fired[0]==1 and out['surface_count']==len(spans),'PATCH_NOT_EXECUTED')
    return out['binding_logits']


def interventions(head,hidden,eligible,spans,rows,captures,*,max_targets=3):
    """Gold-selected *diagnostic* targets; never a deployable edge selector."""
    require(type(max_targets) is int and max_targets>=1,'INVALID_TARGET_LIMIT')
    if not rows:return []
    fp=sorted((r for r in rows if r['outcome']=='fp'),key=lambda r:-r['edge_margin'])
    tp=sorted((r for r in rows if r['outcome']=='tp'),key=lambda r:r['edge_margin'])
    fn=sorted((r for r in rows if r['outcome']=='fn'),key=lambda r:-r['edge_margin'])
    tn=sorted((r for r in rows if r['outcome']=='tn'),key=lambda r:r['edge_margin'])
    selected=(fp[:max_targets]+tp[:max(1,max_targets//2)]+fn[:max(1,max_targets//2)])
    recorded=[];prior_logits=captures['binding']
    base_matrix=_decision_matrix(prior_logits[:len(spans),:len(spans)])
    mask=~torch.eye(len(spans),device=base_matrix.device,dtype=torch.bool)
    for target in selected:
        i,j=target['i'],target['j']
        base_margin=target['edge_margin'];base_pred=target['pred_label']
        ops=[('pair_projection_zero','pair_projection',None,None),
             ('encoder_src_zero','encoder',i,None)]
        if 'g1_g2_coupler' in captures:
            ops.append(('coupler_src_zero','g1_g2_coupler',i,None))
        # Pair-specific negative and positive donors are causal probes,
        # selected using oracle labels POST-INFERENCE (non-deployable).
        if tn:
            donor=tn[0]
            ops.append(('true_negative_pair_transplant','pair_projection',None,
                captures['pair_projection'][donor['i'],donor['j']].clone()))
        if tp:
            donor=tp[0]
            ops.append(('true_positive_pair_transplant','pair_projection',None,
                captures['pair_projection'][donor['i'],donor['j']].clone()))
        # Matched unrelated object zeroing: intervention control, not a claim
        # that this object is independent (the model may couple all nodes).
        others=[r['i'] for r in rows if r['i'] not in (i,j)]
        if others:ops.append(('unrelated_object_zero_control','encoder',others[0],None))
        for op,stage,source,repl in ops:
            altered=_patch_head(head,hidden,eligible,spans,stage=stage,
                src_index=source,pair=(i,j) if stage=='pair_projection' else None,
                replacement=repl)
            actual=altered[i,j].detach().float()
            label=int(torch.argmax(actual[1:]))+1
            margin=float(actual[label]-actual[0]);decision=_decision(actual)
            changed=int(((_decision_matrix(altered[:len(spans),:len(spans)])!=base_matrix)
                         & mask).sum().item())
            recorded.append({'src_index':i,'dst_index':j,'target_outcome':target['outcome'],
                'intervention':op,'baseline_predicted_label':base_pred,
                'intervened_predicted_label':decision,'baseline_margin':base_margin,
                'intervened_margin':margin,'delta_margin':margin-base_margin,
                'edge_decision_flipped':decision!=base_pred,'total_pair_decisions_flipped':changed,
                'gold_label':target['gold_label'],
                'correct_after':decision==target['gold_label']})
    return recorded


def _id_edges(graph, predicted_objects, binding_rows):
    by_anchor={(n['span'][0],n['span'][1],n['kind']):n['id']
                for n in graph['nodes'] if 'span' in n}
    result=set()
    for r in binding_rows:
        if not r['pred_label']:continue
        src=tuple(r['src']);dst=tuple(r['dst'])
        if src in by_anchor and dst in by_anchor:
            result.add((by_anchor[src],by_anchor[dst],BIND_CLASSES[r['pred_label']]))
    return result


def analyze_pair(pair, model, tok, head, composite, *,device,max_length,max_targets,
                 include_pair_scores=False):
    records=[];comparative=[];pair_scores=[]
    for side in ('base','fuzz'):
        row=pair[side]
        prepared,hidden=prior._capture_inference(model,tok,row['prompt'],device,max_length)
        with torch.inference_mode():
            selection=composite.predict(hidden,prepared,family=pair['family'],native_stage='G1')
        spans=selection['token_spans']
        head_out,captures=trace_head(head,hidden,prepared['eligible'].to(device),spans)
        logits=head_out['binding_logits']
        rows=_pair_stats(logits,spans,prepared,row['graph'])
        if include_pair_scores:
            pair_scores.append({'pair_id':pair['id'],'side':side,'mode':pair['mode'],
                                'prompt_sha256':hashlib.sha256(row['prompt'].encode()).hexdigest(),
                                'scores':rows})
        if len(spans)>1:
            causal=interventions(head,hidden,prepared['eligible'].to(device),spans,rows,captures,
                                max_targets=max_targets)
        else:causal=[]
        gold={(n['span'][0],n['span'][1],n['kind'])
              for n in row['graph']['nodes'] if 'span' in n}
        pred={(*token_span_char_bounds(s,prepared),s['kind']) for s in spans}
        required=_pairs_by_gold(row['graph'])
        obs_edges=_id_edges(row['graph'],pred,rows)
        comparative.append(_author_vectors(row['graph'],prepared,spans,captures))
        records.append({'pair_id':pair['id'],'mode':pair['mode'],'family':pair['family'],
          'side':side,'prompt':row['prompt'],'prompt_sha256':hashlib.sha256(row['prompt'].encode()).hexdigest(),
          'g1_objects':len(pred),'g1_exact':pred==gold,
          'g1_gold_objects':len(gold),'g1_missing':len(gold-pred),'g1_extra':len(pred-gold),
          'gold_edges':len(required),'reachable_gold_edges':sum(a in pred and b in pred for a,b in required),
          'pair_metrics':summarize_pairs(rows),
          'activation_geometry':_trace_features(captures,spans,rows),
          'stage_gold_edge_separability':_representation_probe(captures,spans,rows),
          'interventions':causal,'predicted_author_id_edges':[list(e) for e in sorted(obs_edges)]})
        del hidden,head_out,captures,logits
    b,f=records
    bb={tuple(e) for e in b['predicted_author_id_edges']}
    ff={tuple(e) for e in f['predicted_author_id_edges']}
    expected_invariant=pair['mode']!='action_removal'
    comparison={'pair_id':pair['id'],'mode':pair['mode'],'family':pair['family'],
        'expected_gold_invariant':expected_invariant,
        'predicted_same_author_id_edge_set':bb==ff,
        'predicted_edge_set_symmetric_difference':len(bb^ff),
        'baseline_edges':len(bb),'fuzzed_edges':len(ff),
        'base_g1_exact':b['g1_exact'],'fuzz_g1_exact':f['g1_exact'],
        'matched_stage_shift':_paired_activation_shift(*comparative)
          if expected_invariant else {'not_comparable':'ACTION_REMOVAL_CHANGES_AUTHOR_ID_ALIGNMENT'}}
    return (records,comparison,pair_scores) if include_pair_scores else (records,comparison)


def summarize(records,paired):
    totals=Counter();positive=[];negative=[];by_mode=defaultdict(Counter);stages=defaultdict(Counter)
    for row in records:
        m=row['pair_metrics'];totals['cases']+=1
        totals['predicted_edges']+=m['predicted_edges'];totals['candidate_pairs']+=m['candidate_pairs']
        totals['g1_exact']+=row['g1_exact'];totals['gold_edges']+=row['gold_edges']
        totals['reachable_gold_edges']+=row['reachable_gold_edges']
        for k,v in m['outcomes'].items():totals['outcome_'+k]+=v
        by_mode[row['mode']]['cases']+=1
        by_mode[row['mode']]['false_positive_edges']+=m['outcomes'].get('fp',0)
        by_mode[row['mode']]['true_positive_edges']+=m['outcomes'].get('tp',0)
        for i in row['interventions']:
            s=stages[i['intervention']];s['n']+=1
            s['correct_after']+=int(i['correct_after'])
            s['decision_flipped']+=int(i['edge_decision_flipped'])
            s['net_delta_margin_milli']+=round(i['delta_margin']*1000)
    return {'totals':dict(totals),'by_fuzz_mode':{k:dict(v) for k,v in by_mode.items()},
        'by_intervention':{k:{'n':v['n'],'correct_after':v['correct_after'],
         'decision_flipped':v['decision_flipped'],
         'mean_delta_margin':v['net_delta_margin_milli']/v['n']/1000}
          for k,v in stages.items()},
        'counterfactual_pairs':len(paired),
        'predicted_edge_set_invariant_on_gold_invariant_pairs':sum(
            r['predicted_same_author_id_edge_set'] for r in paired if r['expected_gold_invariant']),
        'gold_invariant_pairs':sum(r['expected_gold_invariant'] for r in paired)}


def run(*,root='../runtime/reports/gamer-v1/oracle-fabric-g2-native-training-0083',
        output='../runtime/reports/gamer-v1/oracle-fabric-g2-causal-activation-fuzz-0085',
        device='cuda',pairs=8,seed=SEED,max_length=2048,max_targets=3):
    root=Path(root).resolve();output=Path(output).resolve()
    require(not output.exists(),'OUTPUT_IMMUTABLE')
    require(root.is_dir(),'TRAINED_RUN_NOT_FOUND')
    require(type(max_length) is int and max_length>=128 and max_length<=8192,
            'INVALID_MAX_LENGTH')
    paths=prior.defaults(root.parent)
    parent,proof=prior.preflight(paths)
    seal=prior.check_seal(paths,root,expected_head=parent['head_sha256'])
    trained=root/'training'
    head,manifest=cur._load_checkpoint(trained,device=device)
    require(manifest['stage']=='G2' and manifest['head_sha256']==prior.sha(trained/cur.HEAD_FILE)
            and manifest['native_composite_g1']['mode']=='predicted_G1_spans_native_G2_binding',
            'WRONG_TRAINED_HEAD')
    prior.compare_frozen_stage(paths['g1'],trained)
    require(manifest['native_composite_g1']['certificate_sha256']==prior.sha(paths['certificate'])
            and seal['native_certificate_sha256']==prior.sha(paths['certificate']),
            'CERTIFICATE_DRIFT')
    report=prior.js(root/'report.json')
    require(report.get('schema')==prior.SCHEMA and
            report.get('status')=='G2_PREDICTED_SPAN_TRAINED_BUT_GATE_BLOCKED' and
            report.get('g2_checkpoint_sha256')==manifest['head_sha256'] and
            report.get('g3_training_unlocked') is False,'G2_0083_FAILURE_PROVENANCE_REQUIRED')
    comp=composite_module.load_composite(paths['composite'],device=device)
    require(comp.manifest['head_sha256']==parent['head_sha256'],'COMPOSITE_PARENT_DRIFT')
    head.eval().requires_grad_(False)
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    require(not any(p.requires_grad for p in model.parameters()),'BACKBONE_NOT_FROZEN')
    experiments=make_pairs(seed=seed,pairs=pairs)
    # Neither earlier research nor the scored G2-0083 seal may be repurposed as
    # unseen evidence. New exploration avoids their exact prompts.
    excluded=prior.previously_seen(paths)
    excluded.update(r['prompt_sha256'] for r in prior.load_jsonl(root/'seal'/'prompts.jsonl'))
    require(all(hashlib.sha256(row['prompt'].encode()).hexdigest() not in excluded
                for experiment in experiments for row in (experiment['base'],experiment['fuzz'])),
            'PREVIOUSLY_EXPOSED_PROMPT')
    result=[];comparisons=[];score_records=[]
    for index,item in enumerate(experiments,1):
        observations,comparison,scores=analyze_pair(item,model,tok,head,comp,device=device,
                         max_length=max_length,max_targets=max_targets,include_pair_scores=True)
        result.extend(observations);comparisons.append(comparison);score_records.extend(scores)
        print('G2_0085_FUZZ '+json.dumps({'pair':index,'of':pairs,
          'mode':item['mode'],'family':item['family'],
          'fp_base':observations[0]['pair_metrics']['outcomes'].get('fp',0),
          'fp_fuzz':observations[1]['pair_metrics']['outcomes'].get('fp',0),
          'g1_exact':[r['g1_exact'] for r in observations]}),flush=True)
    summary=summarize(result,comparisons)
    require(prior.sha(trained/cur.HEAD_FILE)==manifest['head_sha256'] and
            prior.sha(paths['certificate'])==seal['native_certificate_sha256'],
            'CHECKPOINT_OR_CERTIFICATE_CHANGED_DURING_DIAGNOSIS')
    audit={'schema':SCHEMA,'status':'FROZEN_G2_ACTIVATION_FUZZ_DIAGNOSIS_ONLY',
       'checkpoint_sha256':manifest['head_sha256'],
       'composite_manifest_sha256':prior.sha(paths['composite']/'composite-interface.json'),
       'g1_native_certificate_sha256':prior.sha(paths['certificate']),
       'source_g2_failure_report_sha256':prior.sha(root/'report.json'),
       'seed':seed,'pairs':pairs,'device':device,'max_length':max_length,
       'gold_use':'post_inference_diagnostic_pair_labels_and_intervention_target_choice_only',
       'g1_policy':'predicted_g1_per_fuzz; fixed_within_all_g2_causal_interventions',
       'patches':'encoder_source_token_zero; g1_g2_coupler_source_token_zero; pair_projection_zero_or_donor_transplant',
       'controls':'unrelated_object_token_zero; true_negative_vs_true_positive_pair_donors',
       'no_train':True,'no_checkpoint_mutation':True,'no_threshold_selection':True,
       'not_independent_certification':True,'g2_certified':False,'g3_unlocked':False,
       'source_code_sha256':prior.sha(Path(__file__)),
       'raw_pair_logits_recorded':True,
       'summary':summary,'counterfactual_comparisons':comparisons}
    # Only new artifacts; all existing G1/G2 proofs remain read-only.
    output.mkdir(parents=True,exist_ok=False)
    prior.write_json(output/'report.json',audit)
    prior.write_jsonl(output/'cases.jsonl',result)
    prior.write_jsonl(output/'pair-scores.jsonl',score_records)
    print('G2_0085_DIAGNOSIS '+json.dumps({'status':audit['status'],
        'head_sha256':manifest['head_sha256'],**summary['totals'],
        'gold_invariant_pairs':summary['gold_invariant_pairs'],
        'predicted_invariant_pairs':summary['predicted_edge_set_invariant_on_gold_invariant_pairs'],
        'g3_unlocked':False},sort_keys=True),flush=True)
    return audit


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='../runtime/reports/gamer-v1/oracle-fabric-g2-native-training-0083')
    parser.add_argument('--output',default='../runtime/reports/gamer-v1/oracle-fabric-g2-causal-activation-fuzz-0085')
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--pairs',type=int,default=8)
    parser.add_argument('--seed',type=int,default=SEED)
    parser.add_argument('--max-length',type=int,default=2048)
    parser.add_argument('--max-targets',type=int,default=3)
    return run(**vars(parser.parse_args(argv)))

if __name__=='__main__':main()
