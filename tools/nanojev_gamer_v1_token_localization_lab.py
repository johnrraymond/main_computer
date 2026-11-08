#!/usr/bin/env python3
"""GAMER V1: frozen LoRA token-localization and matched activation-intervention lab.

Reads a COMPLETE frozen representation-transfer audit (its lineage and source
fingerprints are revalidated), recaptures ONLY its 360 exact counterfactual
cases plus separately identified payoff counterfactuals, and probes whether
zero, polarity and magnitude are recoverable in the flattened 100-token LoRA
raster or in tokenizer-aligned belief/payoff/context token pools.

Belief and payoff interventions are performed on the 100x1024 frozen LoRA
raster fed to frozen production C0, never on model parameters. Token-swap
eligibility requires identical token count, matching raster span slots, and
unchanged tokens outside the edited numeric span. Mismatches are REFUSED.
Masking and hybrid swaps are off-manifold causal interventions ON C0's frozen
input, not proof of semantic computation by the language model.

No TEST/heldout fitting, model selection, threshold calibration or promotion.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import gc
import hashlib
import json
import os
from pathlib import Path
import re

import numpy as np
from tools import nanojev_gamer_v1_representation_transfer_audit as prior
from tools import nanojev_gamer_v1_c0_sign_head as three

SCHEMA='nanojev.gamer.v1.frozen-token-localization.v1'
STEP=3000
NUMERIC=r'[+-]?(?:\d+(?:/\d+)?|\d+\.\d+)'
PAYOFF_RE=re.compile(r'(?<![A-Za-z])(?:CC|CD|DC|DD|R|S|T|P)=('+NUMERIC+r')')
BELIEF_RE=re.compile(r'\bprobability\s+('+NUMERIC+r')\b')
KINDS=('linear','rbf')
REGS=(.05,.5,5.)
THRESHOLDS=(-.15,0.,.15)
FEATURE_NAMES=('raster_flat_100','raster_mean100','raster_last','belief_tokens_mean',
               'payoff_tokens_mean','other_tokens_mean','belief_payoff_joint','lexical_tokens')
TASKS=('zero','polarity','magnitude')


def _sha(path): return prior._sha(path)


def _spans(prompt):
    """Ground numerical token spans in actual prompt characters, never guessed token ids."""
    q=list(BELIEF_RE.finditer(prompt));p=list(PAYOFF_RE.finditer(prompt))
    if len(q)!=1 or len(p)!=4:
        raise ValueError('GAMER_TOKEN_NUMERIC_SPANS_MISSING_OR_AMBIGUOUS')
    q_span=(q[0].start(1),q[0].end(1))
    p_spans=[(m.start(1),m.end(1)) for m in p]
    if any(max(a,q_span[0])<min(b,q_span[1]) for a,b in p_spans):
        raise ValueError('GAMER_TOKEN_SPANS_OVERLAP')
    # Both supported surfaces enumerate R,S,T,P (or CC,CD,DC,DD).
    # Only the third entry (T/DC) changes in payoff intervention pairs.
    return {'belief':[q_span],'payoffs':p_spans,'payoff_t':[p_spans[2]]}


def _token_masks(tok,deck,*,max_length,curvature):
    """Offsets -> token IDs -> same 100-position sampling as C0's full vector."""
    from tools.nanojev_gamer_v1_expanded_head import positions_for_length
    if getattr(tok,'padding_side',None)!='right':
        raise ValueError('GAMER_TOKEN_REQUIRES_RIGHT_PADDING')
    records=[]
    for item in deck:
        prompt=item.prompt;spans=_spans(prompt)
        try:
            encoding=tok(prompt,add_special_tokens=True,truncation=False,
                         return_offsets_mapping=True)
            tokens=list(encoding['input_ids'])
            offsets=[tuple(int(v) for v in span) for span in encoding['offset_mapping']]
        except (TypeError,ValueError,KeyError,AttributeError) as exc:
            raise ValueError('GAMER_TOKEN_FAST_TOKENIZER_OFFSETS_REQUIRED') from exc
        if len(tokens)!=len(offsets) or not 0<len(tokens)<=max_length:
            raise ValueError('GAMER_TOKEN_LENGTH_OR_OFFSETS_INVALID')
        if any(a>b or a<0 or b>len(prompt) for a,b in offsets):
            raise ValueError('GAMER_TOKEN_OFFSET_OUTSIDE_PROMPT')
        positions=np.asarray(positions_for_length(len(tokens),100,curvature),dtype=np.int32)
        def overlaps(span):
            a,b=span
            return np.asarray([hi>lo and max(lo,a)<min(hi,b) for lo,hi in offsets],dtype=bool)
        bmask=overlaps(spans['belief'][0])[positions]
        pmask=np.logical_or.reduce([overlaps(s)[positions] for s in spans['payoffs']])
        tmask=overlaps(spans['payoff_t'][0])[positions]
        if not np.all(~tmask | pmask) or not tmask.any():
            raise ValueError('GAMER_TOKEN_T_SPAN_NOT_IN_PAYOFF_MASK')
        # Special tokens, other numbers and prompt body are background.
        other=~(bmask|pmask)
        if not bmask.any() or not pmask.any() or not other.any() or np.any(bmask&pmask):
            raise ValueError('GAMER_TOKEN_RASTER_OMITS_NUMERIC_SPAN')
        records.append({'ids':np.asarray(tokens,dtype=np.int64), 'positions':positions,
                        'raster_ids':np.asarray(tokens,dtype=np.int64)[positions],
                        'belief':bmask,'payoffs':pmask,'payoff_t':tmask,'other':other,
                        'token_count':len(tokens),
                        'belief_chars':spans['belief'], 'payoff_chars':spans['payoffs']})
    return records


def _payoff_variants(source):
    """A T-only edit at EXACT SAME q on each profile's first zero triple."""
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_zero_sign_frob as prompts
    from tools import nanojev_two_game_spine_smoke as spine
    variants=[];pmap={}
    for i in range(prior.NEW_TRAIN_PROFILES+prior.NEW_DEV_PROFILES+prior.NEW_HOLDOUT_PROFILES):
        stage,q0=prior._profile(i)
        role=('counterfactual_train' if i<prior.NEW_TRAIN_PROFILES else
              'counterfactual_dev' if i<prior.NEW_TRAIN_PROFILES+prior.NEW_DEV_PROFILES else
              'counterfactual_holdout')
        key=f'{role}.payoff.{i:04d}'
        for surface,render in (('spine_belief',prompts._spine_belief_prompt),
                               ('game_prose_belief',prompts._game_prose_belief_prompt)):
            baseid=f'{key}.radius.00.zero.{surface}'
            for delta in (-1,1):
                altered=spine.Stage(stage.R,stage.S,stage.T+delta,stage.P)
                target=altered.margin(q0)
                if target!=Fraction(delta)*q0 or target==0:
                    raise ValueError('GAMER_TOKEN_PAYOFF_COUNTERFACTUAL_CERTIFICATION_FAILED')
                label='minus' if delta<0 else 'plus'
                case=oracle.Case(f'{key}.payoff_T_{label}.{surface}',
                         'train' if role=='counterfactual_train' else
                         'dev' if role=='counterfactual_dev' else 'test',
                         f'{key}.payoff_T_{label}',surface,render(altered,q0),target,
                         'payoff_counterfactual_'+role,Fraction(delta))
                variants.append(case)
                pmap[case.id]={'base_id':baseid,'target_sign':delta,'payoff':'T','profile':key}
    return variants,pmap


def _load_source(audit_dir):
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    root=Path(audit_dir).resolve();file=root/'final_report.json'
    report=json.loads(file.read_text(encoding='utf-8'))
    if (report.get('schema')!=prior.SCHEMA or
        report.get('status')!='COMPLETE_FROZEN_REPRESENTATION_AUDIT_NO_PROMOTION' or
        report.get('no_production_training_or_promotion') is not True or
        report.get('frozen_lora_step')!=STEP or
        report.get('promotion')!='BLOCKED_DIAGNOSTIC_ONLY'):
        raise ValueError('GAMER_TOKEN_REQUIRES_COMPLETED_FROZEN_TRANSFER_AUDIT')
    source=prior._source(Path(report['v2_report_path']).parent)
    if (_sha(source['v2_report_path'])!=report['v2_report_sha256'] or
        oracle.deck_hash(source['deck'])!=report['counterfactual_deck_sha256'] or
        any(oracle.deck_hash(source['role_cases'][k])!=v for k,v in report['role_hashes'].items())):
        raise ValueError('GAMER_TOKEN_PRIOR_AUDIT_LINEAGE_CHANGED')
    original=source['original'];prior._joint_sign_baseline(original)
    origin=original['prior']['lineage']['origin']['source']
    if origin['snapshots'][STEP][1]!=report['frozen_lora_sha256']:
        raise ValueError('GAMER_TOKEN_LORA_FINGERPRINT_CHANGED')
    stagefile=Path(report['feature_archive']['path'])
    if not stagefile.is_file() or _sha(stagefile)!=report['feature_archive']['sha256']:
        raise ValueError('GAMER_TOKEN_FROZEN_STAGE_ARCHIVE_CHANGED')
    indices=source['indices']
    selected=np.concatenate([indices[k] for k in ('counterfactual_train','counterfactual_dev','counterfactual_holdout')])
    if len(selected)!=360 or len(set(map(int,selected)))!=360:
        raise ValueError('GAMER_TOKEN_COUNTERFACTUAL_SPLIT_CHANGED')
    base=[source['deck'][int(i)] for i in selected]
    variants,pmap=_payoff_variants(source)
    deck=base+variants
    if len(deck)!=440 or len({c.id for c in deck})!=440:
        raise ValueError('GAMER_TOKEN_CASE_COUNT_CHANGED')
    ix={'train':np.arange(0,216,dtype=int),
        'dev':np.arange(216,288,dtype=int),
        'holdout':np.arange(288,360,dtype=int)}
    # New payoff variants are causal inputs, not probe fitting / selection data.
    ix['payoff_variants']=np.arange(360,440,dtype=int)
    rows=[c.public() for c in deck]
    return {'report_path':file,'report':report,'source':source,'deck':deck,
            'rows':rows,'indices':ix,'original_indices':selected,
            'variants':pmap,'stage_archive':stagefile,'origin':origin}


def _capture(info,device,batch):
    import torch
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    origin=info['origin'];meta=origin['meta'];snapshot,sha=origin['snapshots'][STEP]
    if stress._verify_snapshot(snapshot,STEP)!=sha:
        raise ValueError('GAMER_TOKEN_FROZEN_LORA_CHECK_FAILED')
    _,tok,model=gamer.model_components(meta['lineage']['model_id'],device,adapter_dir=snapshot)
    cfg=meta['lineage']['configuration']
    try:
        masks=_token_masks(tok,info['deck'],max_length=cfg['max_length'],
                           curvature=cfg['curvature'])
        features,detail=oracle._capture_profiles(torch,model,tok,info['deck'],device=device,
                        max_length=cfg['max_length'],batch_size=batch,
                        curvature=cfg['curvature'],verify_exact_final=True)
    finally:
        del model,tok;gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()
    flat=np.asarray(features['final_flat'],dtype=np.float32)
    pool=np.asarray(features['final_mean100'],dtype=np.float32)
    del features;gc.collect()
    if flat.shape!=(len(info['deck']),102400) or pool.shape!=(len(info['deck']),1024):
        raise ValueError('GAMER_TOKEN_CAPTURE_SHAPE_CHANGED')
    with np.load(info['stage_archive'],allow_pickle=False) as archived:
        expected=archived['lora_final_mean100'][info['original_indices']]
    delta=float(np.max(np.abs(expected-pool[:360])))
    if delta>1e-3:
        raise ValueError(f'GAMER_TOKEN_FROZEN_CAPTURE_PARITY_FAILED: {delta}')
    return flat,masks,{'model_capture':detail,'old_mean100_parity_max_abs_delta':delta}


def _pooled_features(flat,masks):
    x=np.asarray(flat,dtype=np.float32)
    if x.ndim!=2 or x.shape[1]!=102400 or len(masks)!=len(x):
        raise ValueError('GAMER_TOKEN_BAD_FULL_RASTER')
    tokens=x.reshape(-1,100,1024)
    def mean(k):
        mask=np.stack([m[k] for m in masks]).astype(np.float32)
        if np.min(np.sum(mask,axis=1))<1:raise ValueError('GAMER_TOKEN_EMPTY_POOL')
        return np.einsum('nt,ntd->nd',mask,tokens,optimize=True)/np.sum(mask,axis=1)[:,None]
    b=mean('belief');p=mean('payoffs');other=mean('other')
    # Token ids are input-only; lexical baseline cannot access hidden states.
    lexical=np.zeros((len(x),512),dtype=np.float32)
    for i,m in enumerate(masks):
        for category,weight in (('belief',1.),('payoffs',1.),('other',.25)):
            ids=m['raster_ids'][m[category]]
            for token_id in ids:
                col=(int(token_id)*2654435761+int(weight*1000))%512
                lexical[i,col]+=weight
        lexical[i]/=max(1.,np.linalg.norm(lexical[i]))
    return {'raster_flat_100':x,'raster_mean100':tokens.mean(1),
            'raster_last':tokens[:,-1,:].copy(),
            'belief_tokens_mean':b,'payoff_tokens_mean':p,
            'other_tokens_mean':other,
            'belief_payoff_joint':np.concatenate([b,p,other],axis=1),
            'lexical_tokens':lexical}


def _kernel_fit(features,sign,mag,train,dev,task,kind,reg,cache=None):
    """Dual ridge. Kernels/scales and coefficients depend on TRAIN only."""
    x=np.asarray(features,dtype=np.float32)
    train=np.asarray(train,dtype=int)
    chosen=train if task=='zero' else train[np.asarray(sign)[train]!=0]
    if len(chosen)<6:raise ValueError('GAMER_TOKEN_NOT_ENOUGH_CLASS_DATA')
    key=(id(x),task=='zero',kind)
    if cache is not None and key in cache:
        gram,cross,scale,width=cache[key]
    else:
        center=x[chosen].astype(np.float64).mean(axis=0)
        # One scalar scale; fit only on TRAIN, not on any heldout examples.
        scale=max(float(np.sqrt(np.mean((x[chosen].astype(np.float64)-center)**2))),1e-5)
        z=np.asarray((x.astype(np.float64)-center)/(scale*np.sqrt(x.shape[1])),dtype=np.float32)
        tr=z[chosen];k=tr@tr.T
        cross=z@tr.T
        if kind=='rbf':
            d=np.maximum(np.diag(k)[:,None]+np.diag(k)[None,:]-2*k,0)
            positive=d[np.triu_indices(len(chosen),1)];positive=positive[positive>1e-8]
            width=float(np.median(positive)) if len(positive) else 1.
            gram=np.exp(-d/width)
            d=np.maximum(np.sum(z*z,axis=1)[:,None]+np.diag(k)[None,:]-2*cross,0)
            cross=np.exp(-d/width)
        elif kind=='linear':width=1.;gram=k
        else:raise ValueError('GAMER_TOKEN_UNKNOWN_KERNEL')
        if cache is not None:cache[key]=(gram,cross,scale,width)
    y=np.asarray(sign,dtype=np.float64)[chosen] if task!='magnitude' else np.log1p(mag[chosen])
    if task=='zero':y=(y==0).astype(np.float64)*2-1
    # Explicit TRAIN-only intercept is essential for nonzero magnitude.
    # Centering x alone makes a kernel model predict zero for any constant y.
    bias=float(np.mean(y))
    alpha=np.linalg.solve(gram.astype(np.float64)+float(reg)*np.eye(len(chosen)),y-bias)
    # Evaluate once for all examples; score task thresholds using DEV only.
    score=np.asarray(cross@alpha+bias,dtype=np.float64)
    if task=='magnitude':score=np.maximum(np.expm1(np.clip(score,-30.,10.)),0.)
    return score,{'kind':kind,'regularization':float(reg),'train_only_scale':scale,
                  'train_only_width':width,'training_examples':len(chosen)}


def _metrics(sign,mag,score,idx,task,threshold=0.):
    indices=np.asarray(idx,dtype=int);truth=np.asarray(sign)[indices]
    pred=np.asarray(score)[indices]
    if task=='magnitude':
        active=truth!=0
        target=np.asarray(mag)[indices][active];observed=pred[active]
        if not np.isfinite(observed).all() or np.any(observed<0) or not len(target):
            raise ValueError('GAMER_TOKEN_NONFINITE_MAGNITUDE')
        err=np.abs(observed-target)
        tight=np.minimum(.05,np.maximum(.001,.1*target))
        return {'n':len(target),'mae':float(err.mean()),
                'p90':float(np.quantile(err,.9)),
                'within_0_05':float(np.mean(err<=.05)),
                'within_tight':float(np.mean(err<=tight)),
                'near_zero_collapse':int(np.sum(observed<.1*target)),
                'always_zero_mae':float(np.mean(target))}
    active=np.ones(len(indices),dtype=bool) if task=='zero' else truth!=0
    y=(truth[active]==0) if task=='zero' else (truth[active]>0)
    p=pred[active]>=threshold
    if not np.any(y) or not np.any(~y):
        raise ValueError('GAMER_TOKEN_BINARY_CLASS_MISSING')
    r0=float(np.mean(~p[~y]));r1=float(np.mean(p[y]))
    return {'n':int(active.sum()),'balanced_accuracy':(r0+r1)/2,
            'negative_or_nonzero_recall':r0,'positive_or_zero_recall':r1,
            'min_recall':min(r0,r1),'threshold':float(threshold)}


def _fit_features(features,rows,ix):
    sign,mag=prior._targets(rows)
    reports={};held=ix['holdout'];dev=ix['dev'];train=ix['train']
    cache={}
    profile_ids=sorted({rows[int(i)]['group'].split('.radius.')[0] for i in held})
    for name,x in features.items():
        print('  token feature:',name,flush=True)
        tasks={}
        for task in TASKS:
            best=None;trials=[]
            for kind in KINDS:
                for reg in REGS:
                    p,info=_kernel_fit(x,sign,mag,train,dev,task,kind,reg,cache=cache)
                    for threshold in ((0.,) if task=='magnitude' else THRESHOLDS):
                        m=_metrics(sign,mag,p,dev,task,threshold)
                        if task=='magnitude':rank=(-m['mae'],-m['p90'])
                        else:rank=(m['min_recall'],m['balanced_accuracy'])
                        trials.append({'candidate':info,'threshold':threshold,'dev':m})
                        # Ties are stable: model kind and REG order are deterministic.
                        if best is None or rank>best[0]:
                            best=(rank,p,info,threshold,m)
            _,score,chosen,threshold,dev_metrics=best
            hold_metrics=_metrics(sign,mag,score,held,task,threshold)
            profiles={}
            for profile in profile_ids:
                subset=np.asarray([i for i in held if rows[int(i)]['group'].startswith(profile+'.radius.')],dtype=int)
                profiles[profile]=_metrics(sign,mag,score,subset,task,threshold)
            tasks[task]={'selected':chosen,'threshold':threshold,'development':dev_metrics,
                         'holdout':hold_metrics,'holdout_by_profile':profiles,
                         'dev_candidates':trials}
        reports[name]={'tasks':tasks,'input_dim':int(x.shape[1])}
    return reports


def _eligible_swap(a,b,which):
    """Do not invent aligned token indices when numerical tokenization differs."""
    # For T-only changes, swap T/DC positions, never the other unchanged payoffs.
    component='payoff_t' if which=='payoffs' else which
    if component not in ('belief','payoff_t'):raise ValueError('GAMER_TOKEN_UNKNOWN_SWAP_SPAN')
    ma=a[component];mb=b[component]
    if a['token_count']!=b['token_count']:
        return False,'token_count_changed',None
    if not np.array_equal(a['positions'],b['positions']):
        return False,'raster_changed',None
    if not np.array_equal(ma,mb):
        return False,'raster_numeric_slots_changed',None
    ia=a['ids'];ib=b['ids']
    # Outside the intended number, token identifiers MUST be identical.
    token_mask=np.zeros(len(ia),dtype=bool)
    token_mask[np.unique(a['positions'][ma])]=True
    if not np.array_equal(ia[~token_mask],ib[~token_mask]):
        return False,'outside_span_token_ids_changed',None
    if not np.any(ia[token_mask]!=ib[token_mask]):
        return False,'edited_span_tokens_identical',None
    return True,'eligible',ma


def _placebo_mask(recipient,donor,n):
    same=(recipient['raster_ids']==donor['raster_ids'])&recipient['other']&donor['other']
    candidates=np.flatnonzero(same)
    if len(candidates)<n:return None
    # Deterministic nearest-to-belief-or-payoff context controls, no donor labels.
    return candidates[:n]


def _intervention_inputs(flat,records,pairs):
    x=np.asarray(flat,dtype=np.float32).reshape(-1,100,1024)
    interventions=[];metadata=[];skipped=Counter()
    for recipient,donor,kind in pairs:
        a=records[recipient];b=records[donor]
        ok,why,mask=_eligible_swap(a,b,kind)
        if not ok:skipped[f'{kind}:{why}']+=1;continue
        n=int(mask.sum());placebo=_placebo_mask(a,b,n)
        if placebo is None:skipped[f'{kind}:insufficient_matched_placebo']+=1;continue
        changed=x[recipient].copy();changed[mask]=x[donor][mask]
        control=x[recipient].copy();control[placebo]=x[donor][placebo]
        # Mask input region with own background-token mean; intentionally off-manifold.
        masked=x[recipient].copy();masked[mask]=x[recipient][a['other']].mean(axis=0)
        interventions.extend([changed.reshape(-1),control.reshape(-1),masked.reshape(-1)])
        metadata.append({'recipient':recipient,'donor':donor,'kind':kind,
                         'token_slots':n,'indices':tuple(int(v) for v in np.flatnonzero(mask))})
    mat=np.asarray(interventions,dtype=np.float32).reshape(-1,102400)
    return mat,metadata,dict(skipped)


def _make_swaps(info):
    rows=info['rows'];index={r['id']:i for i,r in enumerate(rows)}
    hold=set(map(int,info['indices']['holdout']))
    swaps=[]
    # Within exact same payoff/surface/radius, change belief q only.
    for i in sorted(hold):
        r=rows[i]
        if '.negative.' not in r['id'] and '.zero.' not in r['id']:
            continue
        donor_id=(r['id'].replace('.negative.','.zero.') if '.negative.' in r['id']
                  else r['id'].replace('.zero.','.positive.'))
        if donor_id not in index or index[donor_id] not in hold:
            raise ValueError('GAMER_TOKEN_INCOMPLETE_BELIEF_COUNTERFACTUAL')
        swaps.append((i,index[donor_id],'belief'))
    # Exact same q and otherwise identical surface, edit payoff T only.
    for caseid,reference in info['variants'].items():
        i=index[caseid];baseline=index[reference['base_id']]
        if baseline in hold:swaps.append((baseline,i,'payoffs'))
    return swaps


def _counterfactual_effects(net,flat,records,rows,swaps,device,head_batch):
    from tools import nanojev_gamer_v1_c0_sign_causality as causal
    from tools import nanojev_gamer_v1_c0_sign_head as three
    if not swaps:return {'status':'NO_ELIGIBLE_PAIRS','attempted':0}
    changed,meta,skipped=_intervention_inputs(flat,records,swaps)
    if not len(meta):return {'status':'UNVERIFIED_NO_TOKEN_ALIGNED_SWAPS',
                             'attempted':len(swaps),'skipped':skipped}
    _,_,_,base=causal._read_stages(net,flat,device=device,batch=head_batch)
    _,_,_,altered=causal._read_stages(net,changed,device=device,batch=head_batch)
    outcomes=defaultdict(list)
    for i,m in enumerate(meta):
        r=m['recipient'];d=m['donor']
        a,b,c=map(float,altered[3*i:3*i+3]);original=float(base[r])
        donor_label=float(Fraction(rows[d]['target']));recipient_label=float(Fraction(rows[r]['target']))
        results={'recipient':rows[r]['id'],'donor':rows[d]['id'],
            'recipient_oracle':recipient_label,'donor_oracle':donor_label,
            'baseline_prediction':original,'donor_prediction':float(base[d]),
            'swap_prediction':a,'placebo_prediction':b,'masked_prediction':c,
            'swap_abs_change':abs(a-original),'placebo_abs_change':abs(b-original),
            'mask_abs_change':abs(c-original),
            'toward_donor_target':int(abs(a-donor_label)<abs(original-donor_label)),
            'donor_oracle_delta':donor_label-recipient_label,
            'slot_count':m['token_slots']}
        outcomes[m['kind']].append(results)
    report={}
    for key,values in outcomes.items():
        report[key]={'eligible':len(values),
          'mean_swap_abs_change':float(np.mean([v['swap_abs_change'] for v in values])),
          'mean_placebo_abs_change':float(np.mean([v['placebo_abs_change'] for v in values])),
          'mean_mask_abs_change':float(np.mean([v['mask_abs_change'] for v in values])),
          'fraction_swap_toward_donor_target':float(np.mean([v['toward_donor_target'] for v in values])),
          'examples':values}
    return {'status':'COMPLETE_FROZEN_C0_INPUT_INTERVENTIONS','attempted':len(swaps),
            'eligible':len(meta),'skipped':skipped,'by_span':report,
            'limitation':'Swapping or masking frozen LoRA token vectors at C0 input is off-manifold. Effects establish frozen C0 sensitivity, not that Qwen causally computed the game margin. Token alignment and lexical controls reduce, not eliminate, confounding.'}


def run(args):
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_c0_boundary as c0
    from tools import nanojev_gamer_v1_c0_sign_causality as causal
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    if args.capture_batch<1 or args.head_batch<1:
        raise ValueError('GAMER_TOKEN_INVALID_BATCH_ARGUMENT')
    info=_load_source(args.audit)
    origin=info['origin'];original=info['source']['original']
    protect=[Path(args.audit).resolve(),info['report_path'].parent,
             info['source']['v2_report_path'].parent,original['report_path'].parent,
             original['prior']['joint_dir'],original['prior']['lineage']['report_path'].parent,
             original['prior']['lineage']['origin']['root'],origin['training'],origin['source'],
             origin['capture'],origin['ckpt'].parent,origin['adapter'],
             origin['audit_path'].parent,origin['causal_path'].parent]
    out=three._output(args.output,protect)
    if out.exists():raise FileExistsError('GAMER_TOKEN_OUTPUT_ALREADY_EXISTS')
    sourcehash=_sha(info['report_path']);zerohash=_sha(original['prior']['zero_head'])
    snapshot,check=origin['snapshots'][STEP]
    if stress._verify_snapshot(snapshot,STEP)!=check:raise ValueError('GAMER_TOKEN_LORA_CHANGED')
    print(f'GAMER TOKEN LOCALIZATION: LoRA {STEP} frozen; 360 source counterfactuals + 80 payoff edits',flush=True)
    flat,masks,verification=_capture(info,args.device,args.capture_batch)
    if (_sha(info['report_path'])!=sourcehash or _sha(original['prior']['zero_head'])!=zerohash or
        stress._verify_snapshot(snapshot,STEP)!=check):
        raise ValueError('GAMER_TOKEN_SOURCE_CHANGED_DURING_CAPTURE')
    source_features=_pooled_features(flat,masks)
    print('  selecting train-only readouts on four development payoff profiles',flush=True)
    probes=_fit_features(source_features,info['rows'],info['indices'])
    meta=origin['meta'];net=c0._load_head(meta,args.device)
    # Frozen C0 output on exactly original counterfactuals, with same flattened raster.
    # Prior audit fed float16-quantized flat rasters into C0. Replay exactly.
    c0_flat=flat.astype(np.float16).astype(np.float32)
    stage,_,_,baseline=causal._read_stages(net,c0_flat[:360],device=args.device,batch=args.head_batch)
    with np.load(info['stage_archive'],allow_pickle=False) as prior_features:
        expected=prior_features['enc_gelu_3'][info['original_indices']]
    encoder_delta=float(np.max(np.abs(expected-stage['enc_gelu_3'])))
    if encoder_delta>1e-3:raise ValueError('GAMER_TOKEN_C0_STAGE_PARITY_FAILURE')
    swaps=_make_swaps(info)
    print('  matched activation swaps:',len(swaps),'attempted (unaligned pairs refused)',flush=True)
    interventions=_counterfactual_effects(net,c0_flat,masks,info['rows'],swaps,args.device,args.head_batch)
    del net;gc.collect()
    report={'schema':SCHEMA,'status':'COMPLETE_FROZEN_TOKEN_LOCALIZATION_NO_PROMOTION',
            'promotion':'BLOCKED_DIAGNOSTIC_ONLY','lora_step':STEP,
            'frozen_lora_sha256':check,'prior_audit_path':str(info['report_path']),
            'prior_audit_sha256':sourcehash,'frozen_zero_head_sha256':zerohash,
            'deck_hash':oracle.deck_hash(info['deck']),
            'capture':verification,'encoder_stage_parity_max_abs_delta':encoder_delta,
            'case_counts':{'train':216,'dev':72,'heldout':72,'payoff_interventions':80},
            'component_probes':probes,'activation_interventions':interventions,
            'no_production_training':True,'no_lora_training':True,'no_c0_training':True,
            'heldout_not_used_for_model_selection':True,
            'interpretation':'Pooled, flattened, and token-span features are probes of a frozen representation, not proof of semantic numerical understanding. Activation interventions modify a frozen C0 input off-manifold. Failure of probes does not prove information absence; any success must beat lexical controls and survive disjoint payoff profiles.'}
    if (_sha(info['report_path'])!=sourcehash or _sha(original['prior']['zero_head'])!=zerohash or
        stress._verify_snapshot(snapshot,STEP)!=check):
        raise ValueError('GAMER_TOKEN_SOURCE_CHANGED_BEFORE_REPORT')
    out.mkdir(parents=True,exist_ok=False)
    # Pool-only archive; no huge full-flat source dump by default.
    compact={k:v for k,v in source_features.items() if k!='raster_flat_100'}
    np.savez_compressed(out/'token_span_pools.npz',**compact)
    report['feature_archive']={'path':str(out/'token_span_pools.npz'),
                               'sha256':_sha(out/'token_span_pools.npz')}
    gamer.atomic_json(out/'final_report.json',report)
    print('GAMER TOKEN LOCALIZATION COMPLETE:',out/'final_report.json',flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',choices=('cpu','cuda','directml'),default='cuda')
    p.add_argument('--capture-batch',type=int,default=2)
    p.add_argument('--head-batch',type=int,default=12)
    return run(p.parse_args(argv))

if __name__=='__main__':main()
