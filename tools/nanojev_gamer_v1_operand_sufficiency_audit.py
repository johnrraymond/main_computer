#!/usr/bin/env python3
"""Frozen GAMER V1 operand-sufficiency and arithmetic-composition audit.

Replays the attested 440-case token-localization deck against the SAME frozen
LoRA snapshot, now capturing token-aligned q, R, S, T, P and context features
from embeddings, intermediate layers, and final layer. Fits only small
TRAIN/DEV-selected diagnostic readouts for operands, differences, weighted
terms, signed margin, zero and polarity. Tests held-out payoff profiles and
real-prompt q/T edits with exact-rational oracle deltas. Does not train LoRA,
C0, or any production numerical head. Observed lexical signal is explicitly
not evidence of semantic arithmetic, and no finite probe failure proves
absence of the information.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from fractions import Fraction
import gc
import json
from pathlib import Path

import numpy as np
from tools import nanojev_gamer_v1_token_localization_lab as token
from tools import nanojev_gamer_v1_representation_transfer_audit as audit
from tools import nanojev_gamer_v1_c0_sign_head as c0sign

SCHEMA='nanojev.gamer.v1.frozen-operand-sufficiency.v1'
STEP=3000
CONTINUOUS=('q','R','S','T','P','T_minus_R','P_minus_S',
            'q_times_T_minus_R','one_minus_q_times_P_minus_S','signed_margin','abs_margin')
BINARY=('zero','positive_on_nonzero')
TASKS=CONTINUOUS+BINARY
VIEWS=('belief','R','S','T','P','payoff_joint','operand_joint',
       'context','raster_mean100','raster_last','lexical_numeric')
KINDS=('linear','rbf')
REGS=(.1,1.)
THRESHOLDS=(.25,.4,.5,.6,.75)


def _source(token_dir):
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    root=Path(token_dir).resolve();report_path=root/'final_report.json'
    report=json.loads(report_path.read_text(encoding='utf-8'))
    if (report.get('schema')!=token.SCHEMA or
        report.get('status')!='COMPLETE_FROZEN_TOKEN_LOCALIZATION_NO_PROMOTION' or
        report.get('lora_step')!=STEP or
        report.get('no_lora_training') is not True or
        report.get('no_c0_training') is not True or
        report.get('promotion')!='BLOCKED_DIAGNOSTIC_ONLY'):
        raise ValueError('GAMER_OPERAND_REQUIRES_COMPLETE_FROZEN_TOKEN_AUDIT')
    source=token._load_source(Path(report['prior_audit_path']).parent)
    if (token._sha(source['report_path'])!=report['prior_audit_sha256'] or
        oracle.deck_hash(source['deck'])!=report['deck_hash'] or
        report['frozen_lora_sha256']!=source['origin']['snapshots'][STEP][1] or
        report['frozen_zero_head_sha256']!=token._sha(source['source']['original']['prior']['zero_head'])):
        raise ValueError('GAMER_OPERAND_LINEAGE_OR_ZERO_HEAD_CHANGED')
    pool=Path(report['feature_archive']['path'])
    if not pool.is_file() or token._sha(pool)!=report['feature_archive']['sha256']:
        raise ValueError('GAMER_OPERAND_TOKEN_POOL_ARCHIVE_CHANGED')
    return {'token_report':report,'token_report_path':report_path,
            'source':source,'pool_path':pool}


def _targets(deck):
    """Read the actual printed operands and certify all algebraic intermediates."""
    vals=defaultdict(list)
    for case in deck:
        prompt=case.prompt;spans=token._spans(prompt)
        q=Fraction(prompt[slice(*spans['belief'][0])])
        operands=[Fraction(prompt[slice(*s)]) for s in spans['payoffs']]
        if not 0<=q<=1:raise ValueError('GAMER_OPERAND_BELIEF_OUT_OF_RANGE')
        R,S,T,P=operands
        left=T-R;right=P-S
        weighted_left=q*left;weighted_right=(1-q)*right
        margin=weighted_left+weighted_right
        if margin!=case.target:raise ValueError('GAMER_OPERAND_ORACLE_MISMATCH')
        entries={'q':q,'R':R,'S':S,'T':T,'P':P,
                 'T_minus_R':left,'P_minus_S':right,
                 'q_times_T_minus_R':weighted_left,
                 'one_minus_q_times_P_minus_S':weighted_right,
                 'signed_margin':margin,'abs_margin':abs(margin),
                 'zero':int(margin==0),'positive_on_nonzero':int(margin>0)}
        for k,v in entries.items():vals[k].append(float(v))
    out={k:np.asarray(v,dtype=np.float64) for k,v in vals.items()}
    if set(out)!=set(TASKS) or any(not np.isfinite(a).all() for a in out.values()):
        raise ValueError('GAMER_OPERAND_BAD_ORACLE_LABELS')
    return out


def _aligned_token_spans(tok,deck):
    """Exact tokenizer offsets; use ALL operand tokens, not 100-raster subset."""
    if getattr(tok,'padding_side',None)!='right':
        raise ValueError('GAMER_OPERAND_REQUIRES_RIGHT_PADDING')
    result=[]
    for case in deck:
        spans=token._spans(case.prompt)
        try:
            raw=tok(case.prompt,add_special_tokens=True,truncation=False,
                    return_offsets_mapping=True)
            ids=np.asarray(raw['input_ids'],dtype=np.int64)
            offsets=np.asarray(raw['offset_mapping'],dtype=np.int64)
        except (TypeError,ValueError,KeyError,AttributeError) as exc:
            raise ValueError('GAMER_OPERAND_FAST_TOKENIZER_REQUIRED') from exc
        if offsets.shape!=(len(ids),2) or not len(ids):
            raise ValueError('GAMER_OPERAND_INVALID_OFFSETS')
        fields={}
        for name,part in zip(('belief','R','S','T','P'),
                             spans['belief']+spans['payoffs']):
            start,end=part
            mask=(offsets[:,1]>offsets[:,0]) & (offsets[:,0]<end) & (offsets[:,1]>start)
            if not mask.any():raise ValueError('GAMER_OPERAND_MISSING_TOKEN_SPAN: '+name)
            fields[name]=mask
        occupied=np.zeros(len(ids),dtype=bool)
        for key in fields:
            if np.any(occupied & fields[key]):
                raise ValueError('GAMER_OPERAND_OVERLAPPING_TOKEN_SPANS')
            occupied|=fields[key]
        if not (~occupied).any():raise ValueError('GAMER_OPERAND_NO_CONTEXT_TOKENS')
        fields['context']=~occupied
        fields['ids']=ids
        result.append(fields)
    return result


def _feature_from_hidden(hidden,span,positions):
    """Project one forward-pass hidden state to invariant pooled readout views."""
    h=np.asarray(hidden,dtype=np.float32)
    if h.ndim!=2 or h.shape[1]!=1024 or len(h)!=len(span['ids']):
        raise ValueError('GAMER_OPERAND_BAD_HIDDEN_RASTER')
    views={key:h[span[key]].mean(axis=0) for key in ('belief','R','S','T','P','context')}
    views['payoff_joint']=np.concatenate([views[k] for k in ('R','S','T','P')])
    views['operand_joint']=np.concatenate([views[k] for k in ('belief','R','S','T','P')])
    views['raster_mean100']=h[positions].mean(axis=0)
    views['raster_last']=h[positions[-1]].copy()
    return views


def _lexical(span):
    """Input token-identity control, no hidden states or numeric target labels."""
    v=np.zeros(512,dtype=np.float32)
    for j,k in enumerate(('belief','R','S','T','P','context')):
        for tid in span['ids'][span[k]]:
            col=(int(tid)*2654435761+(j+1)*1009)%512
            v[col]+=1. if k!='context' else .1
    norm=np.linalg.norm(v)
    if norm:v/=norm
    return v


def _capture(info,device,batch_size):
    import torch
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools.nanojev_gamer_v1_expanded_head import positions_for_length
    src=info['source'];origin=src['origin'];meta=origin['meta']
    snapshot,sha=origin['snapshots'][STEP]
    if stress._verify_snapshot(snapshot,STEP)!=sha:
        raise ValueError('GAMER_OPERAND_FROZEN_LORA_FINGERPRINT_MISMATCH')
    _,tok,model=gamer.model_components(meta['lineage']['model_id'],device,adapter_dir=snapshot)
    cfg=meta['lineage']['configuration'];deck=src['deck']
    arrays={};checks=[]
    try:
        spans=_aligned_token_spans(tok,deck)
        core=oracle._backbone_parts(model)
        indices=sorted(set((0,len(core.layers)//3,(2*len(core.layers))//3,len(core.layers)-1)))
        modules={'embedding':core.embed_tokens,
                 **{f'layer_{i:02d}':core.layers[i] for i in indices},
                 'final':core.norm}
        for layer in modules:
            arrays[layer]={v:[] for v in VIEWS}
        model.eval()
        for start in range(0,len(deck),batch_size):
            if start==0 or (start//batch_size)%20==0:
                print(f'OPERAND_CAPTURE {start}/{len(deck)}',flush=True)
            cases=deck[start:start+batch_size]
            if any(len(tok.encode(c.prompt,add_special_tokens=True))>cfg['max_length'] for c in cases):
                raise ValueError('GAMER_OPERAND_PROMPT_TOO_LONG')
            encoded=tok([c.prompt for c in cases],padding=True,truncation=True,
                        max_length=cfg['max_length'],return_tensors='pt')
            encoded={k:v.to(device) for k,v in encoded.items()}
            lengths=[int(v) for v in encoded['attention_mask'].sum(-1).tolist()]
            seen=defaultdict(list);handles=[]
            for name,module in modules.items():
                def hook(_module,_args,value,n=name):
                    h=value[0] if isinstance(value,(tuple,list)) else value
                    if not isinstance(h,torch.Tensor) or h.ndim!=3:
                        raise ValueError('GAMER_OPERAND_BAD_LAYER_HOOK: '+n)
                    seen[n].append(h.detach())
                handles.append(module.register_forward_hook(hook))
            try:
                with torch.inference_mode():
                    model(**encoded,output_hidden_states=False,return_dict=True,
                          logits_to_keep=1)
            finally:
                for handle in handles:handle.remove()
            if set(seen)!=set(modules) or any(len(v)!=1 for v in seen.values()):
                raise ValueError('GAMER_OPERAND_INCOMPLETE_LAYER_CAPTURE')
            for j,length in enumerate(lengths):
                span=spans[start+j]
                if length!=len(span['ids']):
                    raise ValueError('GAMER_OPERAND_TOKENIZATION_PARITY_FAILURE')
                pos=np.asarray(positions_for_length(length,100,cfg['curvature']),dtype=int)
                for layer in modules:
                    h=seen[layer][0]
                    if h.shape[0]!=len(cases) or h.shape[1]<length or h.shape[2]!=1024:
                        raise ValueError('GAMER_OPERAND_WRONG_LAYER_WIDTH')
                    frame=h[j,:length].float().cpu().numpy()
                    if not np.isfinite(frame).all():
                        raise ValueError('GAMER_OPERAND_NONFINITE_HIDDEN')
                    values=_feature_from_hidden(frame,span,pos)
                    values['lexical_numeric']=_lexical(span)
                    for view,a in values.items():arrays[layer][view].append(a)
            del seen
        for stage in arrays:
            for view in VIEWS:
                arrays[stage][view]=np.stack(arrays[stage][view]).astype(np.float32)
        with np.load(info['pool_path'],allow_pickle=False) as pool:
            expected=pool['raster_mean100']
        actual=arrays['final']['raster_mean100']
        delta=float(np.max(np.abs(expected-actual)))
        if delta>1e-3:raise ValueError(f'GAMER_OPERAND_FINAL_TOKEN_CAPTURE_PARITY_FAILURE: {delta}')
        return arrays,{'layers':list(modules),'layer_indices':indices,'cases':len(deck),
                       'final_mean100_parity_max_abs_delta':delta,
                       'offsets':'complete_numeric_token_spans_not_raster_only',
                       'parity_replayed_from':str(info['pool_path'])}
    finally:
        del model,tok;gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()


def _encode_train_map(x,train,kind):
    x=np.asarray(x,dtype=np.float32)
    if x.ndim!=2 or not np.isfinite(x).all():
        raise ValueError('GAMER_OPERAND_NONFINITE_FEATURES')
    train=np.asarray(train,dtype=int)
    center=np.mean(x[train].astype(np.float64),axis=0)
    scale=max(float(np.sqrt(np.mean((x[train].astype(np.float64)-center)**2))),1e-5)
    z=np.asarray((x.astype(np.float64)-center)/(scale*np.sqrt(x.shape[1])),dtype=np.float32)
    if kind=='linear':return z@z[train].T, z[train]@z[train].T,{'width':1.,'scale':scale}
    if kind!='rbf':raise ValueError('GAMER_OPERAND_UNKNOWN_KERNEL')
    a=z[train];gram=a@a.T
    d=np.maximum(np.diag(gram)[:,None]+np.diag(gram)[None,:]-2*gram,0)
    upper=d[np.triu_indices(len(train),1)];upper=upper[upper>1e-8]
    width=float(np.median(upper)) if len(upper) else 1.
    d2=np.maximum(np.sum(z*z,axis=1)[:,None]+np.diag(gram)[None,:]-2*z@a.T,0)
    return np.exp(-d2/width),np.exp(-d/width),{'width':width,'scale':scale}


def _predict_tasks(x,targets,train,dev,kind,reg):
    """Multi-target train-only kernel ridge; polarity uses NONZERO training only."""
    train=np.asarray(train,dtype=int)
    score={};meta={}
    for subset in ('all','nonzero'):
        chosen=train if subset=='all' else train[targets['zero'][train]==0]
        keys=[k for k in TASKS if (k!='positive_on_nonzero')==(subset=='all')]
        cross,gram,params=_encode_train_map(x,chosen,kind)
        y=np.stack([targets[k][chosen] for k in keys],axis=1)
        bias=y.mean(axis=0);scale=np.maximum(y.std(axis=0),1e-6)
        norm=(y-bias)/scale
        alpha=np.linalg.solve(gram.astype(np.float64)+reg*np.eye(len(chosen)),norm)
        predicted=cross@alpha
        for j,k in enumerate(keys):score[k]=predicted[:,j]*scale[j]+bias[j]
        meta[subset]={'training_cases':len(chosen),**params}
    return score,meta


def _score(y,p,task,idx,threshold=0.):
    ix=np.asarray(idx,dtype=int)
    a=np.asarray(y,dtype=np.float64)[ix]
    b=np.asarray(p,dtype=np.float64)[ix]
    if task in BINARY:
        yy=a>=.5;pred=b>=threshold
        if not yy.any() or not (~yy).any():
            raise ValueError('GAMER_OPERAND_BINARY_METRIC_MISSING_CLASS')
        r0=float(np.mean(~pred[~yy]));r1=float(np.mean(pred[yy]))
        return {'cases':len(ix),'balanced_accuracy':(r0+r1)/2,
                'negative_class_recall':r0,'positive_class_recall':r1,
                'min_recall':min(r0,r1),'threshold':threshold}
    err=np.abs(b-a)
    return {'cases':len(ix),'mae':float(err.mean()),'p90_abs_error':float(np.quantile(err,.9)),
            'train_constant_mae':None,
            'pearson':float(np.corrcoef(a,b)[0,1]) if len(a)>2 and np.std(a)>1e-12 and np.std(b)>1e-12 else None,
            'within_0_05':float(np.mean(err<=.05)),
            'mean_target':float(np.mean(a)), 'mean_prediction':float(np.mean(b))}


def _evaluate(targets,pred,idx,train,thresholds=None):
    result={}
    thresholds=thresholds or {}
    for k in TASKS:
        ids=np.asarray(idx,dtype=int)
        if k=='positive_on_nonzero':ids=ids[targets['zero'][ids]==0]
        if not len(ids):raise ValueError('GAMER_OPERAND_EMPTY_EVAL')
        t=_score(targets[k],pred[k],k,ids,thresholds.get(k,0.))
        if k in CONTINUOUS:
            baseline=np.median(targets[k][np.asarray(train,dtype=int)])
            t['train_median_constant_mae']=float(np.mean(np.abs(targets[k][ids]-baseline)))
            t['improves_train_constant']=bool(t['mae']<t['train_median_constant_mae'])
            if k in ('signed_margin','abs_margin'):
                truth=targets[k][ids];err=np.abs(pred[k][ids]-truth)
                tol=np.minimum(.05,np.maximum(.001,.1*np.abs(truth)))
                t['within_tight_tolerance']=float(np.mean(err<=tol))
        result[k]=t
    return result


def _fit_select(features,targets,indices):
    train,dev,hold=(np.asarray(indices[k],dtype=int) for k in ('train','dev','holdout'))
    nonzero_dev=dev[targets['zero'][dev]==0]
    fit={};candidates=[];winner={}
    for kind in KINDS:
        for reg in REGS:
            prediction,meta=_predict_tasks(features,targets,train,dev,kind,reg)
            candidate_id=f'{kind}-reg-{reg}'
            fit[candidate_id]=(prediction,meta)
            candidates.append({'kind':kind,'reg':reg})
    for task in TASKS:
        best=None
        for spec in candidates:
            key=f"{spec['kind']}-reg-{spec['reg']}"
            p,meta=fit[key]
            subset=nonzero_dev if task=='positive_on_nonzero' else dev
            for threshold in (THRESHOLDS if task in BINARY else (0.,)):
                m=_score(targets[task],p[task],task,subset,threshold)
                rank=(m['min_recall'],m['balanced_accuracy']) if task in BINARY else (-m['mae'],-m['p90_abs_error'])
                if best is None or rank>best[0]:
                    best=(rank,key,threshold,m)
        _,key,threshold,m=best
        winner[task]={'model_key':key,'threshold':threshold,
                      'development':m}
    predicted={k:fit[winner[k]['model_key']][0][k] for k in TASKS}
    thresholds={k:v['threshold'] for k,v in winner.items()}
    result={'selected':winner,
            'heldout':_evaluate(targets,predicted,hold,train,thresholds),
            'development':_evaluate(targets,predicted,dev,train,thresholds)}
    # Recompose WITHOUT training another head; a good operand readout should
    # also give the right result when its recovered operands are combined.
    recompositions={
        'from_four_payoffs_and_q':predicted['q']*(predicted['T']-predicted['R']) +
            (1-predicted['q'])*(predicted['P']-predicted['S']),
        'from_two_gaps_and_q':predicted['q']*predicted['T_minus_R'] +
            (1-predicted['q'])*predicted['P_minus_S'],
        'from_weighted_terms':predicted['q_times_T_minus_R'] +
            predicted['one_minus_q_times_P_minus_S'],
        'direct_margin_readout':predicted['signed_margin'],
    }
    result['recomposition']={part:{name:_score(targets['signed_margin'],value,
                                  'signed_margin',ids)
                                   for name,value in recompositions.items()}
                             for part,ids in (('development',dev),('heldout',hold))}
    profile_ids=sorted(indices.get('holdout_profiles',[]))
    if profile_ids:
        result['by_heldout_profile']={}
        for name,subset in profile_ids:
            subset=np.asarray(subset,dtype=int)
            # Only 18 examples per profile; do not claim fine precision from small groups.
            result['by_heldout_profile'][name]=_evaluate(targets,predicted,subset,train,thresholds)
    return result,predicted


def _counterfactual_deltas(source,targets,pred):
    """Actual-input q and T edits; per-label delta from identical prompt surface."""
    rows=source['rows'];by_id={r['id']:i for i,r in enumerate(rows)}
    pairs=[]
    for i in source['indices']['holdout']:
        caseid=rows[int(i)]['id']
        if '.negative.' in caseid:
            partner=caseid.replace('.negative.','.zero.')
            if partner not in by_id:raise ValueError('GAMER_OPERAND_MISSING_Q_CONTROL')
            pairs.append((int(i),by_id[partner],'belief_q'))
        elif '.zero.' in caseid:
            partner=caseid.replace('.zero.','.positive.')
            if partner not in by_id:raise ValueError('GAMER_OPERAND_MISSING_Q_CONTROL')
            pairs.append((int(i),by_id[partner],'belief_q'))
    for id_,details in source['variants'].items():
        baseline=by_id[details['base_id']]
        if baseline in set(map(int,source['indices']['holdout'])):
            pairs.append((baseline,by_id[id_],'payoff_T'))
    per_type=defaultdict(list)
    for i,j,kind in pairs:
        tasks=('q','q_times_T_minus_R','one_minus_q_times_P_minus_S','signed_margin') if kind=='belief_q' else ('T','T_minus_R','q_times_T_minus_R','signed_margin')
        record={'recipient':rows[i]['id'],'donor':rows[j]['id'],
                'oracle_delta':{k:float(targets[k][j]-targets[k][i]) for k in tasks},
                'predicted_delta':{k:float(pred[k][j]-pred[k][i]) for k in tasks}}
        for k in tasks:
            d=record['oracle_delta'][k];estimated=record['predicted_delta'][k]
            record.setdefault('error',{})[k]=abs(d-estimated)
        per_type[kind].append(record)
    output={}
    for kind,data in per_type.items():
        tasks=list(data[0]['oracle_delta'])
        output[kind]={'cases':len(data),
          'tasks':{k:{'delta_mae':float(np.mean([d['error'][k] for d in data])),
                      'direction_correct_fraction':float(np.mean([np.sign(d['oracle_delta'][k])==np.sign(d['predicted_delta'][k]) for d in data])) if any(d['oracle_delta'][k] for d in data) else None,
                      'oracle_mean_abs_delta':float(np.mean([abs(d['oracle_delta'][k]) for d in data]))}
                   for k in tasks},
          'examples':data[:6]}
    return output


def run(args):
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_handoff as gamer
    if args.capture_batch<1:raise ValueError('GAMER_OPERAND_INVALID_CAPTURE_BATCH')
    info=_source(args.token)
    src=info['source'];origin=src['origin'];original=src['source']['original']
    protect=[Path(args.token).resolve(),info['token_report_path'].parent,
             src['report_path'].parent,src['source']['v2_report_path'].parent,
             original['report_path'].parent,original['prior']['joint_dir'],
             original['prior']['lineage']['report_path'].parent,
             original['prior']['lineage']['origin']['root'],origin['training'],
             origin['source'],origin['capture'],origin['ckpt'].parent,
             origin['adapter'],origin['audit_path'].parent,origin['causal_path'].parent]
    out=c0sign._output(args.output,protect)
    if out.exists():raise FileExistsError('GAMER_OPERAND_OUTPUT_ALREADY_EXISTS')
    step_snapshot,step_sha=origin['snapshots'][STEP]
    if stress._verify_snapshot(step_snapshot,STEP)!=step_sha:
        raise ValueError('GAMER_OPERAND_FROZEN_LORA_CHANGED_BEFORE_CAPTURE')
    before=token._sha(info['token_report_path'])
    targets=_targets(src['deck'])
    # Strong payoffs-disjoint contract before any CUDA work.
    indices=src['indices'];train=indices['train'];dev=indices['dev'];hold=indices['holdout']
    roles=[{src['rows'][int(i)]['group'].split('.radius.')[0] for i in ids} for ids in (train,dev,hold)]
    if any(roles[i]&roles[j] for i,j in ((0,1),(0,2),(1,2))):
        raise ValueError('GAMER_OPERAND_PAYOFF_ROLE_LEAK')
    by_profile=defaultdict(list)
    for i in hold:
        key=src['rows'][int(i)]['group'].split('.radius.')[0]
        by_profile[key].append(int(i))
    print(f'GAMER OPERAND SUFFICIENCY: frozen LoRA {STEP}, {len(src["deck"])} cases, {len(roles[2])} held-out profiles',flush=True)
    arrays,verification=_capture(info,args.device,args.capture_batch)
    if (token._sha(info['token_report_path'])!=before or
        stress._verify_snapshot(step_snapshot,STEP)!=step_sha):
        raise ValueError('GAMER_OPERAND_SOURCE_CHANGED_DURING_CAPTURE')
    report={'schema':SCHEMA,'status':'COMPLETE_FROZEN_OPERAND_SUFFICIENCY_NO_PROMOTION',
            'promotion':'BLOCKED_DIAGNOSTIC_ONLY','lora_step':STEP,
            'frozen_lora_sha256':step_sha,'token_report_path':str(info['token_report_path']),
            'token_report_sha256':before,'zero_head_sha256':info['token_report']['frozen_zero_head_sha256'],
            'deck_sha256':oracle.deck_hash(src['deck']),'capture':verification,
            'case_counts':{'train':len(train),'dev':len(dev),'heldout':len(hold),
                           'payoff_variants':len(indices['payoff_variants'])},
            'stages':{},'no_lora_training':True,'no_c0_training':True,
            'no_production_training':True,'heldout_not_used_for_selection':True,
            'interpretation':'Token-offset operand probes compare available input tokens and hidden states. Exact q, payoffs, differences and weighted terms are certified from prompt text. Lexical identity and constant baselines are controls, not semantic arithmetic. Finite probe failure cannot prove upstream absence. Previous heldout profiles have already been inspected; this run is diagnostic, not blind generalization certification.'}
    idx={'train':train,'dev':dev,'holdout':hold,
         'holdout_profiles':list(sorted(by_profile.items()))}
    for layer,views in arrays.items():
        print('  layer:',layer,flush=True)
        layer_report={}
        for view in VIEWS:
            result,pred=_fit_select(views[view],targets,idx)
            # On-manifold input counterfactuals: only for the joint operand readout.
            if view in ('operand_joint','lexical_numeric'):
                result['actual_input_counterfactuals']=_counterfactual_deltas(src,targets,pred)
            layer_report[view]=result
        report['stages'][layer]=layer_report
    # Cross-view summaries select ONE view per task by DEVELOPMENT metrics only.
    # Heldout and lexical-control statistics are attached afterward, never ranked.
    summary={}
    for stage,views in report['stages'].items():
        summary[stage]={}
        for task in TASKS:
            def development_rank(view):
                metric=views[view]['development'][task]
                return (metric['min_recall'],metric['balanced_accuracy']) if task in BINARY else (-metric['mae'],-metric['p90_abs_error'])
            chosen=max(VIEWS,key=development_rank)
            summary[stage][task]={'view_selected_on_dev':chosen,
                'development':views[chosen]['development'][task],
                'heldout':views[chosen]['heldout'][task],
                'lexical_heldout_control':views['lexical_numeric']['heldout'][task]}
    report['stage_sufficiency_summary']=summary
    report['arithmetic_chain']=list(CONTINUOUS)
    report['training_roles']={'selection':'TRAIN_FIT_DEV_ONLY',
        'counterfactual_variants':'POST_SELECTION_EVALUATION_ONLY',
        'heldout':'PREVIOUSLY_INSPECTED_DIAGNOSTIC_NOT_BLIND'}
    if (token._sha(info['token_report_path'])!=before or
        stress._verify_snapshot(step_snapshot,STEP)!=step_sha):
        raise ValueError('GAMER_OPERAND_SOURCE_CHANGED_BEFORE_REPORT')
    out.mkdir(parents=True,exist_ok=False)
    # Cache strictly frozen stage features for reproducible follow-up analysis.
    archive={f'{stage}__{view}':values.astype(np.float16)
             for stage,views in arrays.items() for view,values in views.items()}
    if any(not np.isfinite(a).all() for a in archive.values()):
        raise ValueError('GAMER_OPERAND_FEATURE_ARCHIVE_NONFINITE')
    np.savez_compressed(out/'operand_stage_views.npz',**archive)
    report['feature_archive']={'path':str(out/'operand_stage_views.npz'),
        'sha256':token._sha(out/'operand_stage_views.npz'),
        'arrays':{name:list(a.shape) for name,a in archive.items()},
        'storage_dtype':'float16','inference_features_dtype':'float32'}
    gamer.atomic_json(out/'final_report.json',report)
    print('GAMER OPERAND SUFFICIENCY COMPLETE:',out/'final_report.json',flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--token',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',choices=('cpu','cuda','directml'),default='cuda')
    p.add_argument('--capture-batch',type=int,default=2)
    return run(p.parse_args(argv))


if __name__=='__main__':main()
