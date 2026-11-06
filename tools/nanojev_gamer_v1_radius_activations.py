#!/usr/bin/env python3
"""Read-only numerical radius microscope for frozen GAMER Qwen + expanded C0.

A visible numeric literal is replaced with the same signed integer values in
multiple independent certified game prompts. This is a controlled textual
intervention, NOT a certified new game or correct-answer calculation. Record
Qwen embedding/intermediate/final activations and C0 internal activations, then
measure cross-context shared numerical directions on token-count-matched pairs.
No model weights, optimizer states, or source files are modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np

from tools import nanojev_gamer_v1_handoff as gamer
from tools import nanojev_gamer_v1_pairs as pairs
from tools.nanojev_gamer_v1_expanded_head import positions_for_length

SCHEMA = 'nanojev.gamer.v1.radius-activations.v1'


def _literal_occurrences(text, literal):
    """Find a complete numeric literal, never a substring of another number."""
    pattern=r'(?<![\w.])'+re.escape(literal)+r'(?![\w.]|\d)'
    return list(re.finditer(pattern,text))


def select_public_register(row):
    """Select exactly one certified, exposed numeric literal, deterministically."""
    source=row.get('source',{})
    prompt=row.get('prompt_08','')
    if source.get('kind') not in ('scalar','bounded_scalar','bounded_state_transition'):
        return None
    candidates=[]
    for name,value in sorted(source.get('parameters',{}).items()):
        # Probabilities/fractions do not admit a signed integer radius without
        # leaving the model's advertised domain.
        if any(term in name.lower() for term in ('probab','fraction','rate','ratio')):
            continue
        if type(value) not in (float,int):
            continue
        if not math.isfinite(float(value)) or 0 <= float(value) <= 1:
            # Do not rewrite a likely probability into an impossible signed value.
            continue
        literal=pairs.number(value)
        spans=_literal_occurrences(prompt,literal)
        if len(spans)==1:
            m=spans[0]
            # Prefer a non-terminal register so the embedding_last control is
            # the same unedited suffix token across radius values.
            if len(prompt)-m.end()<16:
                continue
            candidates.append((name,literal,m.start(),m.end()))
    return candidates[0] if candidates else None


def intervene(prompt,selection,number):
    name,literal,start,end=selection
    if prompt[start:end]!=literal:
        raise ValueError('GAMER_RADIUS_REGISTER_SPAN_DRIFT')
    if type(number) is not int:
        raise ValueError('GAMER_RADIUS_INTEGER_VALUES_ONLY')
    # Always explicit + or -, including +0; equal rendering policy across
    # positive/negative/zero. Token counts are checked after tokenization.
    return prompt[:start]+f'{number:+d}'+prompt[end:]


def contexts_from_rows(rows,*,count):
    if count<2:raise ValueError('GAMER_RADIUS_MIN_TWO_CONTEXTS')
    selected=[];seen=set()
    for row in rows:
        if row['game_id'] in seen:continue
        candidate=select_public_register(row)
        if candidate is None:continue
        selected.append({'game_id':row['game_id'],'case_sha256':row['case_sha256'],
                         'split':row['split'],'prompt':row['prompt_08'],
                         'register_name':candidate[0],'literal':candidate[1],
                         'span':[candidate[2],candidate[3]]})
        seen.add(row['game_id'])
        if len(selected)>=count:break
    if len(selected)<2:raise ValueError('GAMER_RADIUS_NOT_ENOUGH_CERTIFIED_VISIBLE_CONTEXTS')
    return selected


def radius_values(radius):
    radius=int(radius)
    if radius<1 or radius>32:
        raise ValueError('GAMER_RADIUS_INVALID_INTEGER_RADIUS')
    return tuple(range(-radius,radius+1))


def _sample_last(hidden,length):
    return hidden[0,length-1].detach().float().cpu().numpy()


def capture_qwen(torch,model,tok,prompt,*,device,max_length,curvature,layer_indices):
    """Only requested layers are copied from GPU to CPU, one prompt at a time."""
    model.eval()
    backbone=model.get_base_model() if hasattr(model,'get_base_model') else model
    core=getattr(backbone,'model',None)
    if core is None or not hasattr(core,'layers') or not hasattr(core,'embed_tokens') or not hasattr(core,'norm'):
        raise ValueError('GAMER_RADIUS_QWEN_BACKBONE_HOOKS_UNAVAILABLE')
    encoded=tok(prompt,return_tensors='pt',add_special_tokens=True)
    ids=encoded['input_ids'][0].tolist()
    if not ids or len(ids)>max_length:
        raise ValueError('GAMER_RADIUS_PROMPT_EXCEEDS_UNTRUNCATED_LENGTH')
    inputs={key:value.to(device) for key,value in encoded.items()}
    captured={};handles=[]
    def install(module,name):
        def hook(_module,_args,value):
            x=value[0] if isinstance(value,(tuple,list)) else value
            if not isinstance(x,torch.Tensor) or x.ndim!=3:
                raise ValueError('GAMER_RADIUS_ACTIVATION_SHAPE_UNEXPECTED: '+name)
            length=int(inputs['input_ids'].shape[1])
            ix=positions_for_length(length,100,curvature)
            sampled=x[0,list(ix)].detach().float().cpu().numpy()
            if name=='qwen_final_100':
                captured[name]=sampled.reshape(-1)
                captured['qwen_final_last']=_sample_last(x,length)
                captured['qwen_final_mean100']=sampled.mean(axis=0)
            else:
                captured[name]=_sample_last(x,length)
                captured[name.replace('_last','_mean100')]=sampled.mean(axis=0)
        handles.append(module.register_forward_hook(hook))
    install(core.embed_tokens,'embedding_last')
    for i in layer_indices:
        if i<0 or i>=len(core.layers):
            raise ValueError('GAMER_RADIUS_BAD_QWEN_LAYER_INDEX')
        install(core.layers[i],f'qwen_layer_{i+1:02d}_last')
    install(core.norm,'qwen_final_100')
    try:
        with torch.inference_mode():
            model(**inputs,output_hidden_states=False,return_dict=True,logits_to_keep=1)
    finally:
        for handle in handles:handle.remove()
    base_names={'embedding_last'}|{f'qwen_layer_{i+1:02d}_last' for i in layer_indices}
    expected={'qwen_final_last','qwen_final_100','qwen_final_mean100'}|base_names|{name.replace('_last','_mean100') for name in base_names}
    if set(captured)!=expected or any(not np.isfinite(a).all() for a in captured.values()):
        raise ValueError('GAMER_RADIUS_QWEN_CAPTURE_INCOMPLETE_OR_NONFINITE')
    return captured,{'token_count':len(ids),'last_token_id':int(ids[-1]),
                     'token_ids_sha256':hashlib.sha256(np.asarray(ids,dtype='<i8').tobytes()).hexdigest()}


def capture_c0(torch,net,x,*,device):
    """Trace actual expanded C0 graph (both signed-log readouts)."""
    from tools.nanojev_qwen_c0_compressive_train import inverse_signed_log
    net.eval()
    dtype=next(net.parameters()).dtype
    z=torch.as_tensor(np.asarray(x,dtype=np.float32).copy(),device=device,dtype=dtype).reshape(1,-1)
    result={}
    with torch.inference_mode():
        z=net.norm(z)
        result['c0_input_norm']=z.flatten().float().cpu().numpy()
        for idx,module in enumerate(net.enc):
            z=module(z)
            if idx%2:
                result[f'c0_hidden_{(idx+1)//2}']=z.flatten().float().cpu().numpy()
        aux=net.uncorrected(z).flatten().float().cpu().numpy()
        z=net.mid(z)
        result['c0_bottleneck']=z.flatten().float().cpu().numpy()
        head=net.corrected(z).flatten().float().cpu().numpy()
    result['c0_logit']=head
    result['c0_answer']=inverse_signed_log(head).astype(np.float32)
    result['c0_aux_logit']=aux
    if any(not np.isfinite(a).all() for a in result.values()):
        raise ValueError('GAMER_RADIUS_C0_NONFINITE')
    return result


def _mean_pairwise_cosines(vectors):
    vec=np.asarray(vectors,dtype=np.float64)
    norms=np.linalg.norm(vec,axis=1)
    use=norms>1e-12
    if use.sum()<2:return None
    unit=vec[use]/norms[use,None]
    cos=unit@unit.T
    return float((cos.sum()-len(unit))/(len(unit)*(len(unit)-1)))


def shared_radius_analysis(array,values,token_counts,*,permutations=64,seed=47):
    """Probe shared *changes*, not absolute activation magnitudes.

    Each context contributes a least-squares local slope from comparisons with
    its own zero intervention. Cases with changed token counts are excluded
    from the primary causal analysis (but retained in saved raw captures).
    A held-out context is projected onto the mean direction from other
    contexts. Its baseline (zero intervention) is known to the diagnostic;
    this is NOT a standalone deployable numerical decoder.
    """
    arr=np.asarray(array,dtype=np.float64)
    values=np.asarray(values,dtype=np.float64)
    counts=np.asarray(token_counts,dtype=np.int64)
    if arr.ndim!=3 or arr.shape[:2]!=(len(counts),len(values)) or counts.shape!=arr.shape[:2]:
        raise ValueError('GAMER_RADIUS_ANALYSIS_INPUT_SHAPE')
    centers=np.where(values==0)[0]
    if len(centers)!=1:raise ValueError('GAMER_RADIUS_ZERO_CENTER_REQUIRED')
    center=int(centers[0]);c,n,w=arr.shape
    slopes=[];retained=[];residuals=[];labels=[];nonzero_pairs=0
    for k in range(c):
        valid=(counts[k]==counts[k,center])&(values!=0)
        y=values[valid]
        if len(y)<2 or len(np.unique(y))<2:continue
        delta=arr[k,valid]-arr[k,center]
        slopes.append((y@delta)/(y@y))
        retained.append(k)
        residuals.append(delta);labels.append(y)
        nonzero_pairs+=len(y)
    if len(slopes)<3:
        return {'status':'INSUFFICIENT_SAME_TOKEN_COUNT_CONTEXTS',
                'contexts_total':c,'contexts_valid':len(slopes),
                'matched_nonzero_pairs':nonzero_pairs,'width':w}
    slopes=np.stack(slopes)
    # A constant control (for example, the unchanged suffix embedding)
    # legitimately has zero activation slopes. A cosine does not exist for
    # zero-length vectors; retain null rather than misreporting 0.0.
    nonzero_slope_contexts=int(np.count_nonzero(np.linalg.norm(slopes,axis=1)>1e-12))
    coherence=_mean_pairwise_cosines(slopes)
    # Orthogonal projection of heldout centered activations on a shared slope.
    # All fitting, including scale, excludes the heldout context.
    ys=[];preds=[]
    for i in range(len(slopes)):
        shared=np.mean(np.delete(slopes,i,axis=0),axis=0)
        scale=float(shared@shared)
        if scale<1e-20:
            prediction=np.zeros_like(labels[i])
        else:
            prediction=(residuals[i]@shared)/scale
        ys.extend(labels[i]);preds.extend(prediction)
    yy=np.asarray(ys);pp=np.asarray(preds)
    # Zero-line curvature compares symmetric neighboring radii, conditional
    # on identical token counts; not a measure of correct game answers.
    curvature_ratios=[]
    i_neg=np.where(values==-1)[0];i_pos=np.where(values==1)[0]
    if len(i_neg)==1 and len(i_pos)==1:
        lo=int(i_neg[0]);hi=int(i_pos[0])
        for k in range(c):
            if counts[k,lo]==counts[k,center]==counts[k,hi]:
                left=arr[k,lo]-arr[k,center];right=arr[k,hi]-arr[k,center]
                denom=np.linalg.norm(left)+np.linalg.norm(right)
                if denom>1e-12:
                    curvature_ratios.append(float(np.linalg.norm(left+right)/denom))
    rmse=float(np.sqrt(np.mean((yy-pp)**2)))
    baseline_rmse=float(np.sqrt(np.mean(yy**2)))
    null=[]
    if permutations:
        rng=np.random.default_rng(seed)
        # Shuffle the numerical ordering of radius interventions separately
        # within each context while keeping actual activation changes fixed.
        # The null tests shared *ordered* numerical responses, not token identity.
        for _ in range(permutations):
            random_slopes=[]
            for y,delta in zip(labels,residuals):
                randomized=rng.permutation(y)
                random_slopes.append((randomized@delta)/(randomized@randomized))
            null.append(_mean_pairwise_cosines(random_slopes))
    finite_null=[v for v in null if v is not None]
    p=(1+sum(v>=coherence for v in finite_null))/(1+len(finite_null)) if coherence is not None else None
    return {'status':'COMPLETE','width':w,'contexts_total':c,
            'contexts_valid':len(slopes),'nonzero_slope_contexts':nonzero_slope_contexts,
            'matched_nonzero_pairs':nonzero_pairs,
            'mean_pairwise_slope_cosine':coherence,
            'permutation_null_mean_cosine':float(np.mean(finite_null)) if finite_null else None,
            'permutation_p_one_sided':p,
            'heldout_context_delta_rmse':rmse,'zero_predictor_delta_rmse':baseline_rmse,
            'heldout_context_r2_vs_zero':float(1-(rmse/baseline_rmse)**2) if baseline_rmse>0 else None,
            'zero_crossing_matched_contexts':len(curvature_ratios),
            'zero_crossing_mean_curvature_ratio':float(np.mean(curvature_ratios)) if curvature_ratios else None,
            'caveat':'Centered diagnostic uses the same prompt at zero; not a standalone value decoder. Explicitly signed tokens can still encode lexical identity rather than arithmetic.'}


def _format_radius_stage_summary(stage,stat):
    """Render diagnostic metrics without inventing numeric values for nulls."""
    if stat['status']!='COMPLETE':
        return f'  {stage}: {stat["status"]} matched={stat.get("matched_nonzero_pairs", 0)}'

    def display(value):
        if value is None or not math.isfinite(float(value)):
            return 'N/A'
        return f'{value:.4f}'

    nonzero=stat.get('nonzero_slope_contexts')
    if nonzero is None:
        activity=''
    else:
        activity=f' nonzero_slopes={nonzero}/{stat["contexts_valid"]}'
    return (f'  {stage}: cosine={display(stat["mean_pairwise_slope_cosine"])}'
            f' heldout_r2={display(stat["heldout_context_r2_vs_zero"])}'
            f' matched={stat["matched_nonzero_pairs"]}{activity}')


def _canonical_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_radius(expanded_run,*,source_run=None,output=None,device='cuda',contexts=12,
               radius=2,curvature=None,permutations=64,max_length=None,checkpoint='best',
               recovery=False,recovery_output=None,recovery_folds=4,recovery_rank=12,
               recovery_controls=1):
    import torch
    from tools.nanojev_gamer_v1_expanded_diagnostic_train import _source_run_from_expanded,_load_checkpoint
    from tools.nanojev_gamer_v1_diagnostic_train import _source_inputs,_output_path
    from tools import nanojev_gamer_v1_expanded_head as exp
    expanded=Path(expanded_run).resolve()
    source=Path(source_run).resolve() if source_run else _source_run_from_expanded(expanded)
    src=_source_inputs(source)
    identity=exp._json(expanded/'provenance.json')
    cfg=identity.get('configuration',{})
    if exp._identity(src,cfg)!=identity or cfg.get('samples')!=100:
        raise ValueError('GAMER_RADIUS_EXPANDED_SOURCE_IDENTITY_INVALID')
    if checkpoint not in ('best','latest'):
        raise ValueError('GAMER_RADIUS_UNKNOWN_CHECKPOINT')
    ckpt=expanded/('best_model.pt' if checkpoint=='best' else 'latest.pt')
    if not ckpt.is_file():raise FileNotFoundError(ckpt)
    provenance_files=[ckpt,expanded/'provenance.json',source/'certified_pairs.jsonl']
    fingerprints={str(p):_canonical_hash(p) for p in provenance_files}
    adapter_fingerprint=gamer.file_tree_sha256(src['source_adapter'])
    if adapter_fingerprint!=identity['adapter_sha256']:
        raise ValueError('GAMER_RADIUS_ADAPTER_DRIFT')
    if contexts<3:raise ValueError('GAMER_RADIUS_REQUIRES_3_CONTEXTS')
    if recovery and (contexts<8 or recovery_folds<2 or recovery_folds>16 or
                     recovery_rank<1 or recovery_rank>128 or
                     recovery_controls<0 or recovery_controls>16):
        raise ValueError('GAMER_RADIUS_RECOVERY_INVALID_REQUEST')
    if permutations<0 or permutations>4096:
        raise ValueError('GAMER_RADIUS_PERMUTATION_BUDGET_INVALID')
    values=radius_values(radius)
    deck=src['full_split_rows']['dev']+src['full_split_rows']['train']
    selected=contexts_from_rows(deck,count=contexts)
    out=_output_path(expanded,output,suffix='radius-activations')
    if any(out==p or p in out.parents or out in p.parents for p in (source,expanded)):
        raise ValueError('GAMER_RADIUS_OUTPUT_OVERLAPS_SOURCE')
    out.mkdir(parents=True,exist_ok=False)
    radius_curvature=float(cfg['curvature'] if curvature is None else curvature)
    max_length=int(cfg['max_length'] if max_length is None else max_length)
    if not math.isfinite(radius_curvature) or not 0<=radius_curvature<=20:
        raise ValueError('GAMER_RADIUS_CURVATURE_INVALID')
    if max_length>cfg['max_length']:
        raise ValueError('GAMER_RADIUS_LENGTH_EXCEEDS_TRAINING_CONTRACT')
    state=_load_checkpoint(torch,ckpt)
    if state.get('identity')!=identity or state.get('schema')!=exp.SCHEMA:
        raise ValueError('GAMER_RADIUS_CHECKPOINT_LINEAGE_INVALID')
    source_step=int(state['step'])
    if checkpoint=='best':weights=state['model']
    else:weights=state['trainer']['model']
    print(f'GAMER NUMERICAL ACTIVATION RADIUS: {out}',flush=True)
    print(f'  frozen LoRA; C0 {checkpoint} step={state["step"]}; contexts={len(selected)}; values={values}; no training',flush=True)
    _,tok,qwen=gamer.model_components(identity['model'],device,adapter_dir=src['source_adapter'])
    qwen.eval()
    layer_count=len((qwen.get_base_model() if hasattr(qwen,'get_base_model') else qwen).model.layers)
    layer_indices=tuple(sorted(set(i for i in (layer_count//4-1,layer_count//2-1,(3*layer_count)//4-1) if i>=0)))
    from tools.nanojev_gamer_v1_expanded_head import make_expanded_head
    head=make_expanded_head(torch,cfg['input_width'],tuple(cfg['hidden']))
    head.load_state_dict(weights)
    head=head.to(device=device,dtype=(torch.float16 if device.startswith('cuda') else torch.float32))
    head.eval()
    captures={};counts=np.zeros((len(selected),len(values)),dtype=np.int32)
    token_ids=[];example_rows=[]
    for c,ctx in enumerate(selected):
        current=[]
        for v,value in enumerate(values):
            intervention=intervene(ctx['prompt'],(ctx['register_name'],ctx['literal'],*ctx['span']),value)
            q,meta=capture_qwen(torch,qwen,tok,intervention,device=device,
                 max_length=max_length,curvature=radius_curvature,layer_indices=layer_indices)
            h=capture_c0(torch,head,q['qwen_final_100'],device=device)
            record={**q,**h}
            if not captures:
                captures={key:np.zeros((len(selected),len(values),len(data)),dtype=np.float32)
                          for key,data in record.items()}
            if set(record)!=set(captures):raise ValueError('GAMER_RADIUS_STAGE_CHANGED')
            for key,data in record.items():captures[key][c,v]=data
            counts[c,v]=meta['token_count']
            current.append(meta)
            example_rows.append({'context':c,'value':value,'game_id':ctx['game_id'],
                 'register':ctx['register_name'],'prompt_sha256':hashlib.sha256(intervention.encode()).hexdigest(),
                 **meta})
        token_ids.append(current)
        if len({m['last_token_id'] for m in current})!=1:
            raise ValueError('GAMER_RADIUS_SUFFIX_TOKEN_CHANGED_ACROSS_VALUES')
        print(f'  context {c+1}/{len(selected)}: {ctx["game_id"]} / {ctx["register_name"]} / token lengths {counts[c].tolist()}',flush=True)
    del qwen,head,state
    analysis={k:shared_radius_analysis(val,values,counts,permutations=permutations)
              for k,val in captures.items()}
    np.savez_compressed(out/'activations.npz',values=np.asarray(values,dtype=np.int32),
                        token_counts=counts,**captures)
    with (out/'interventions.jsonl').open('w',encoding='utf-8') as f:
        for item in example_rows:f.write(gamer.canonical(item)+'\n')
    for p,sha in fingerprints.items():
        if _canonical_hash(p)!=sha:raise ValueError('GAMER_RADIUS_SOURCE_DRIFT: '+p)
    if gamer.file_tree_sha256(src['source_adapter'])!=adapter_fingerprint:
        raise ValueError('GAMER_RADIUS_ADAPTER_CHANGED_DURING_RUN')
    report={'schema':SCHEMA,'status':'COMPLETE_READ_ONLY','source_run':str(source),
            'expanded_run':str(expanded),'checkpoint':checkpoint,'checkpoint_step':source_step,'files_sha256':fingerprints,
            'adapter_sha256':adapter_fingerprint,'radius_values':list(values),'contexts':[
              {k:v for k,v in row.items() if k!='prompt'} for row in selected],
            'qwen_layer_indices_zero_based':list(layer_indices),
            'token_count_all_values_equal_contexts':int(np.sum(np.all(counts==counts[:,0:1],axis=1))),
            'suffix_token_identity_preserved':True,
            'per_stage':analysis,
            'limitations':['Synthetic one-register textual edits are NOT certified gameplay inputs or evaluated target answers',
                           'Token-count mismatched interventions are excluded from primary slope analysis',
                           'A common signed-digit token pattern may yield lexical rather than arithmetic alignment',
                           'C0 activations inherit frozen LoRA and C0 weights; no weights are trained',
                           'A strong direction is a candidate shared numerical signal, not proof of causal arithmetic'],
            'artifacts':['activations.npz','interventions.jsonl','final_report.json']}
    gamer.atomic_json(out/'final_report.json',report)
    print('GAMER NUMERICAL ACTIVATION RADIUS COMPLETE: '+str(out/'final_report.json'),flush=True)
    for stage,stat in analysis.items():
        print(_format_radius_stage_summary(stage,stat),flush=True)
    if recovery:
        from tools.nanojev_gamer_v1_radius_recovery import run_recovery
        run_recovery(out,output=recovery_output,folds=recovery_folds,
                     rank=recovery_rank,controls=recovery_controls)
    return report


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run',type=Path,required=True,help='Expanded head-100 run directory')
    ap.add_argument('--source-run',type=Path,default=None)
    ap.add_argument('--output',type=Path,default=None)
    ap.add_argument('--device',default='cuda')
    ap.add_argument('--contexts',type=int,default=12)
    ap.add_argument('--radius',type=int,default=2)
    ap.add_argument('--permutations',type=int,default=64)
    ap.add_argument('--checkpoint',choices=('best','latest'),default='best')
    ap.add_argument('--recovery',action='store_true',help='Follow capture with read-only numerical recovery analysis')
    ap.add_argument('--recovery-output',type=Path,default=None)
    ap.add_argument('--recovery-folds',type=int,default=4)
    ap.add_argument('--recovery-rank',type=int,default=12)
    ap.add_argument('--recovery-controls',type=int,default=1)
    ap.add_argument('--max-length',type=int,default=None)
    ap.add_argument('--curvature',type=float,default=None)
    a=ap.parse_args(argv)
    return run_radius(a.run,source_run=a.source_run,output=a.output,device=a.device,
        contexts=a.contexts,radius=a.radius,permutations=a.permutations,
        checkpoint=a.checkpoint,max_length=a.max_length,curvature=a.curvature,
        recovery=a.recovery,recovery_output=a.recovery_output,
        recovery_folds=a.recovery_folds,recovery_rank=a.recovery_rank,
        recovery_controls=a.recovery_controls)

if __name__=='__main__':main()
