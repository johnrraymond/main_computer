#!/usr/bin/env python3
"""Frozen GAMER causal-path microscope: Qwen feature swaps -> C0 outputs.

Uses a previously captured Qwen-final-100 activation, a train-only localization
selection and the exact expanded C0 checkpoint. Intervenes only in disposable
copies of activations; the source files, LoRA, optimizer and C0 are never trained.

There are two distinct tasks:
  (1) synthetic register pairs: counterfactual *output influence*, NOT correctness;
  (2) cached certified-dev prompts: *real oracle accuracy* and matched feature
      transplant controls, with the original certified label kept unchanged.

This is causal with respect to the frozen C0's dependence on the intervened
activation coordinates. It does not establish that Qwen learned abstract numbers
or that these coordinates are the unique neuronal locus of numerical meaning.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_information_localization as loc
from tools import nanojev_gamer_v1_number_distance as dist
from tools import nanojev_gamer_v1_radius_activations as radius
from tools import nanojev_gamer_v1_handoff as gamer

SCHEMA = 'nanojev.gamer.v1.causal-path.v1'
PAIR_VALUES = ((-3,-2),(-2,-1),(1,2),(2,3),(-1,0),(0,-1),(0,1),(1,0),
               (-1,1),(1,-1),(-2,0),(0,2),(2,0),(0,-2),(-2,2),(2,-2))


def _hash(path):
    return radius._canonical_hash(Path(path))


def _signed_log(value):
    x=np.asarray(value,dtype=np.float64)
    return np.sign(x)*np.log1p(np.abs(x))


def _inverse_signed_log(value):
    x=np.asarray(value,dtype=np.float64)
    return np.sign(x)*np.expm1(np.clip(np.abs(x),0,35))


def _case_kind(a,b):
    if a==0 or b==0:return 'touch_zero'
    return 'cross_sign' if a*b<0 else 'same_sign'


def _read_source(report_dir):
    report_dir=Path(report_dir).resolve()
    loc_file=report_dir/'final_report.json'
    if not loc_file.is_file():raise FileNotFoundError(loc_file)
    record=json.loads(loc_file.read_text(encoding='utf-8'))
    if record.get('schema')!=loc.SCHEMA or record.get('status')!='COMPLETE_READ_ONLY':
        raise ValueError('GAMER_CAUSAL_REQUIRES_LOCALIZATION_REPORT')
    stage=record['by_stage'].get('qwen_final_100')
    if not stage or not stage.get('primary_candidate'):
        raise ValueError('GAMER_CAUSAL_NO_APPROVED_QWEN_CANDIDATE')
    capture=Path(record['source_capture']).resolve()
    cap_files=[capture/name for name in ('final_report.json','activations.npz','interventions.jsonl')]
    for file in cap_files:
        if not file.is_file():raise FileNotFoundError(file)
        if _hash(file)!=record['source_sha256'][file.name]:
            raise ValueError('GAMER_CAUSAL_CAPTURE_FINGERPRINT_MISMATCH:'+file.name)
    cr=json.loads(cap_files[0].read_text(encoding='utf-8'))
    if cr.get('schema')!=dist.SCHEMA or cr.get('status')!='COMPLETE_READ_ONLY':
        raise ValueError('GAMER_CAUSAL_BAD_DISTANCE_CAPTURE')
    if cr.get('checkpoint')!='best' or cr.get('checkpoint_step') is None:
        raise ValueError('GAMER_CAUSAL_NEEDS_BEST_CHECKPOINT_CAPTURE')
    return record,cr,capture,cap_files


def _fold_positions(record,contexts,top=4):
    """Use location choices made on *other* games; no test-game re-selection."""
    n=record['folds'];seed=record['seed']
    groups=dist._group_folds(contexts,folds=n,seed=seed)
    folds=record['by_stage']['qwen_final_100']['arms']['unseen_games_values_words']['fold_details']
    if len(folds)!=len(groups):raise ValueError('GAMER_CAUSAL_LOCALIZATION_FOLD_COUNT_CHANGED')
    hidden_folds=record['by_stage']['c0_hidden_1']['arms']['unseen_games_values_words']['fold_details']
    if len(hidden_folds)!=len(groups):raise ValueError('GAMER_CAUSAL_HIDDEN_FOLD_COUNT_CHANGED')
    per_game={}
    for fold,(tr,te) in enumerate(groups):
        row=folds[fold]
        if int(row['test_games'])!=len(te) or int(row['train_games'])!=len(tr):
            raise ValueError('GAMER_CAUSAL_LOCALIZATION_FOLDS_CHANGED')
        candidates=[int(i) for i in row['top_blocks'][:top]]
        if len(candidates)!=top or len(set(candidates))!=len(candidates) or any(not 0<=v<100 for v in candidates):
            raise ValueError('GAMER_CAUSAL_INVALID_SELECTED_POSITIONS')
        tile=int(row['nested_top1_tile'])
        if not 0<=tile<16:raise ValueError('GAMER_CAUSAL_INVALID_SELECTED_TILE')
        hidden=hidden_folds[fold]
        hidden_block=int(hidden['top_blocks'][0]);hidden_tile=int(hidden['nested_top1_tile'])
        if not 0<=hidden_block<4 or not 0<=hidden_tile<16:
            raise ValueError('GAMER_CAUSAL_HIDDEN_SELECTION_OUT_OF_RANGE')
        for i in te:per_game[int(i)]={'fold':fold,'blocks':candidates,'tile':tile,
                            'hidden_block':hidden_block,'hidden_tile':hidden_tile}
    if len(per_game)!=len(contexts):raise ValueError('GAMER_CAUSAL_MISSING_GAME_SELECTION')
    # A fixed, reproducible candidate for certified games outside the probe deck;
    # no certified labels contribute to ranking.
    counts=record['by_stage']['qwen_final_100']['arms']['unseen_games_values_words']['top_block_selection_frequency']
    consensus=sorted(((int(k),int(v)) for k,v in counts.items()),key=lambda z:(-z[1],z[0]))
    if len(consensus)<top:raise ValueError('GAMER_CAUSAL_NOT_ENOUGH_CONSENSUS_POSITIONS')
    return per_game,[k for k,_ in consensus[:top]]


def _controls(selected,*,count,seed,nblocks=100):
    # Nearby slots are more credible controls for positional weighting than
    # arbitrary distant slots (which may be mostly unchanged suffixes).
    local=[i for i in range(nblocks) if i not in set(selected)
           and any(abs(i-j)<=8 for j in selected)]
    candidates=np.asarray(local if len(local)>=len(selected) else
          [i for i in range(nblocks) if i not in set(selected)],dtype=int)
    if len(candidates)<len(selected):raise ValueError('GAMER_CAUSAL_NO_MATCHED_CONTROL_BLOCKS')
    rng=np.random.default_rng(seed)
    return [rng.choice(candidates,size=len(selected),replace=False).tolist() for _ in range(count)]


def _swap_blocks(receiver,donor,blocks,block_width=1024):
    x=np.asarray(receiver,dtype=np.float32).copy()
    donor=np.asarray(donor,dtype=np.float32)
    if x.shape!=donor.shape or x.ndim!=1 or len(x)%block_width:
        raise ValueError('GAMER_CAUSAL_INPUT_SHAPE_MISMATCH')
    for block in blocks:
        if not 0<=block<len(x)//block_width:raise ValueError('GAMER_CAUSAL_BAD_BLOCK_INDEX')
        lo=block*block_width;x[lo:lo+block_width]=donor[lo:lo+block_width]
    return x


def _swap_tile(receiver,donor,block,tile,tile_width=64,block_width=1024):
    x=np.asarray(receiver,dtype=np.float32).copy()
    donor=np.asarray(donor,dtype=np.float32)
    if x.ndim!=1 or x.shape!=donor.shape or len(x)%block_width:
        raise ValueError('GAMER_CAUSAL_TILE_INPUT_SHAPE_MISMATCH')
    lo=block*block_width+tile*tile_width
    if not 0<=lo<len(x) or lo+tile_width>min(len(x),(block+1)*block_width):
        raise ValueError('GAMER_CAUSAL_TILE_OUT_OF_RANGE')
    x[lo:lo+tile_width]=donor[lo:lo+tile_width]
    return x


def _chosen_pairs(values,counts,*,games,limit,seed=47):
    """Same game, same numeral form, same token count, balanced around zero."""
    index={int(v):i for i,v in enumerate(values)}
    out=[];dropped=0
    for game in range(games):
        for fi,form in enumerate(dist.FORMS):
            for a,b in PAIR_VALUES:
                if a not in index or b not in index:continue
                ia,ib=index[a],index[b]
                if counts[game,fi,ia]!=counts[game,fi,ib]:
                    dropped+=1;continue
                out.append({'game':game,'form':fi,'from':ia,'to':ib,
                            'a':a,'b':b,'kind':_case_kind(a,b)})
    # Deterministic stratified sample; balance zero, sign reversal and ordinary
    # changes so high-frequency within-sign cases cannot hide zero behavior.
    rng=np.random.default_rng(seed)
    buckets={k:[] for k in ('touch_zero','cross_sign','same_sign')}
    for case in out:buckets[case['kind']].append(case)
    for rows in buckets.values():rng.shuffle(rows)
    chosen=[]
    while len(chosen)<limit and any(buckets.values()):
        for kind in ('touch_zero','cross_sign','same_sign'):
            if len(chosen)>=limit:break
            if buckets[kind]:chosen.append(buckets[kind].pop())
    chosen.sort(key=lambda r:(r['game'],r['form'],r['a'],r['b']))
    return chosen,{'eligible':len(out),'selected':len(chosen),
                   'token_count_mismatch_excluded':dropped,
                   'by_kind':{k:sum(c['kind']==k for c in chosen) for k in buckets}}


def _infer(torch,head,x,*,device,trace=False):
    """Exact expanded-head forward, including LayerNorm and signed-log scalar."""
    dtype=next(head.parameters()).dtype
    t=torch.as_tensor(np.asarray(x,dtype=np.float32).copy(),device=device,dtype=dtype).reshape(1,-1)
    with torch.inference_mode():
        z=head.norm(t)
        h1=head.enc[1](head.enc[0](z))
        intermediate=head.enc[2:](h1)
        hidden=head.mid(intermediate)
        logit=float(head.corrected(hidden).float().flatten()[0].item())
    if not np.isfinite(logit):raise ValueError('GAMER_CAUSAL_NONFINITE_LOGIT')
    return (logit,h1.detach().float().cpu().numpy()[0]) if trace else logit


def _infer_h1(torch,head,h1,*,device):
    dtype=next(head.parameters()).dtype
    t=torch.as_tensor(np.asarray(h1,dtype=np.float32).copy(),device=device,dtype=dtype).reshape(1,-1)
    with torch.inference_mode():
        zz=head.enc[2:](t)
        logit=float(head.corrected(head.mid(zz)).float().flatten()[0].item())
    if not np.isfinite(logit):raise ValueError('GAMER_CAUSAL_HIDDEN_NONFINITE')
    return logit


def _parity(expected_answer,actual_logit,*,tol=0.025):
    # Compare in signed-log coordinates; raw inverse-log outputs can be huge.
    delta=abs(float(_signed_log(expected_answer))-actual_logit)
    if delta>tol:raise ValueError('GAMER_CAUSAL_REPLAY_PARITY_FAILED: '+str(delta))
    return delta


def _measure(rows,*,permutations=99,seed=47):
    """Paired effect and placebo comparison; no artificial sign accuracy claim."""
    if not rows:return {'cases':0,'status':'NO_PAIRS'}
    r=np.asarray([v['receiver_logit'] for v in rows])
    d=np.asarray([v['donor_logit'] for v in rows]);cand=np.asarray([v['candidate_logit'] for v in rows])
    controls=np.asarray([v['control_logits'] for v in rows],dtype=np.float64)
    gap=d-r;delta=cand-r;control=controls-r[:,None]
    mask=np.abs(gap)>0.01
    def gain(p):
        target=gap[:,None] if np.asarray(p).ndim==2 else gap
        return np.abs(target)-np.abs(target-p)
    cg=gain(delta);bg=np.mean(gain(control),axis=1)
    diff=cg-bg
    rng=np.random.default_rng(seed)
    # Each game, not each highly dependent within-game register pair, is the
    # permutation unit. Avoid inflated confidence from repeated game prompts.
    if all('game' in item for item in rows):
        groups=sorted({item['game'] for item in rows})
        grouped=np.asarray([np.mean(diff[[i for i,item in enumerate(rows)
                   if item['game']==game]]) for game in groups],dtype=float)
    else:grouped=diff
    if permutations:
        null=[float(np.mean(grouped*rng.choice((-1,1),size=len(grouped)))) for _ in range(permutations)]
        p=(1+sum(x>=float(np.mean(grouped))-1e-12 for x in null))/(1+permutations)
    else:p=None
    return {'status':'COMPLETE','cases':len(rows),'permutation_independent_groups':len(grouped),
            'donor_gap_median_abs_logit':float(np.median(np.abs(gap))),
            'candidate_median_abs_change':float(np.median(np.abs(delta))),
            'control_median_abs_change':float(np.median(np.abs(control))),
            'candidate_donor_gain_mean':float(np.mean(cg)),
            'control_donor_gain_mean':float(np.mean(bg)),
            'candidate_minus_control_gain':float(np.mean(diff)),
            'paired_signflip_p':p,
            'fraction_output_changed_above_0_01':float(np.mean(np.abs(delta)>.01)),
            'donor_aligned_fraction':float(np.mean(np.sign(delta[mask])==np.sign(gap[mask]))) if mask.any() else None,
            'donor_effect_slope':float(np.dot(delta,gap)/max(np.dot(gap,gap),1e-12)),
            'control_effect_slope':float(np.sum(np.mean(control,axis=1)*gap)/max(np.dot(gap,gap),1e-12)),
            'nearly_identical_donor_excluded_from_alignment':int((~mask).sum())}


def evaluate_transplants(torch,head,x,h1,answer,counts,values,games,choices,*,device,
                         max_pairs=120,controls=3,permutations=99,seed=47):
    """Intervene at both Qwen 100-vector input and first C0 hidden output."""
    x=np.asarray(x,dtype=np.float32);h1=np.asarray(h1,dtype=np.float32)
    if x.ndim!=4 or x.shape[:3]!=counts.shape or x.shape[-1]!=102400 or h1.shape[:3]!=x.shape[:3]:
        raise ValueError('GAMER_CAUSAL_BAD_CAPTURE_SHAPE')
    pairs,selection=_chosen_pairs(values,counts,games=len(games),limit=max_pairs,seed=seed)
    findings=[];hidden_findings=[];parity_max=0.;hidden_parity_max=0.;identity_max=0.
    for ni,pair in enumerate(pairs):
        g,f,a,b=(pair[k] for k in ('game','form','from','to'))
        rec=x[g,f,a];don=x[g,f,b];hrec=h1[g,f,a];hdon=h1[g,f,b]
        chosen=choices[g]
        positions=chosen['blocks']
        ctrl=_controls(positions,count=controls,seed=seed+ni*9283)
        r,hr=_infer(torch,head,rec,device=device,trace=True)
        d=_infer(torch,head,don,device=device)
        parity_max=max(parity_max,_parity(answer[g,f,a,0],r),_parity(answer[g,f,b,0],d))
        hidden_parity_max=max(hidden_parity_max,float(np.max(np.abs(hr-hrec))))
        if hidden_parity_max>0.15:raise ValueError('GAMER_CAUSAL_HIDDEN_REPLAY_PARITY_FAILED')
        if ni<4:identity_max=max(identity_max,abs(r-_infer(torch,head,_swap_blocks(rec,don,[],1024),device=device)))
        patched,causal_h1=_infer(torch,head,_swap_blocks(rec,don,positions),device=device,trace=True)
        controls_logits=[_infer(torch,head,_swap_blocks(rec,don,s),device=device) for s in ctrl]
        direct_tile=_swap_tile(rec,don,positions[0],chosen['tile'])
        tile_logit=_infer(torch,head,direct_tile,device=device)
        change_h1=float(np.linalg.norm((causal_h1-hr).astype(np.float64)))
        full_h1_gap=float(np.linalg.norm((hdon-hrec).astype(np.float64)))
        row={**pair,'fold':chosen['fold'],'selected_positions':positions,'selected_tile':chosen['tile'],
             'receiver_logit':r,'donor_logit':d,'candidate_logit':patched,
             'tile_logit':tile_logit,'control_logits':controls_logits,
             'hidden1_change_ratio_to_full_donor':change_h1/max(full_h1_gap,1e-10)}
        findings.append(row)
        # Nested first hidden layer is downstream of the input. Patching its
        # 64D training-selected tile directly distinguishes transport from
        # downstream sensitivity; compare an equal-size tile in same block.
        hidden_width=len(hrec);hblocks=hidden_width//1024
        if hblocks and hidden_width%1024==0:
            # C0 hidden block is an activation block, not a Qwen token.
            block=chosen['hidden_block']
            tiles=1024//64
            ht=chosen['hidden_tile']
            ht_control=(ht+7)%tiles
            hpatch=_swap_tile(hrec,hdon,block,ht)
            hcontrol=_swap_tile(hrec,hdon,block,ht_control)
            hidden_findings.append({**pair,'receiver_logit':r,'donor_logit':d,
                'candidate_logit':_infer_h1(torch,head,hpatch,device=device),
                'control_logits':[_infer_h1(torch,head,hcontrol,device=device)],
                'block':block,'tile':ht,'control_tile':ht_control})
    def split(rows):
        return {'all':_measure(rows,permutations=permutations,seed=seed),
                **{k:_measure([p for p in rows if p['kind']==k],permutations=permutations,seed=seed+i*7001)
                   for i,k in enumerate(('touch_zero','cross_sign','same_sign'))}}
    return {'status':'COMPLETE_READ_ONLY','selection':selection,'input_position_swap':split(findings),
            'hidden_1_tile_swap':split(hidden_findings),
            'baseline_replay_max_abs_signed_log_error':parity_max,
            'baseline_hidden_1_max_abs_error':hidden_parity_max,
            'identity_swap_max_abs_signed_log_error':identity_max,
            'preview':[{'game':games[r['game']],'form':dist.FORMS[r['form']],
                       'from':r['a'],'to':r['b'],'kind':r['kind'],
                       'receiver_logit':r['receiver_logit'],'donor_logit':r['donor_logit'],
                       'candidate_logit':r['candidate_logit'],
                       'control_mean_logit':float(np.mean(r['control_logits'])),
                       'selected_positions':r['selected_positions']}
                       for r in findings[:min(len(findings),16)]]}


def zero_spelling_invariance(answers,values,counts,minus_zero=None,minus_zero_counts=None):
    """Same numeric zero; distinct literal spellings are *lexical* controls.

    These comparisons test output invariance to alternate spellings, not
    arithmetic correctness. Count mismatches remain visible and stratified.
    """
    ans=np.asarray(answers,dtype=np.float64)
    v=np.asarray(values,dtype=int)
    ix=np.flatnonzero(v==0)
    if ans.ndim!=4 or ans.shape[1]!=3 or ans.shape[-1]!=1 or len(ix)!=1:
        raise ValueError('GAMER_CAUSAL_MISSING_ZERO_SPELLINGS')
    z=int(ix[0]);base=_signed_log(ans[:,0,z,0])
    out={}
    for fi,name in ((1,'plus_zero'),(2,'word_zero'),(None,'minus_zero')):
        if fi is None:
            if minus_zero is None:continue
            alt=_signed_log(np.asarray(minus_zero,dtype=np.float64).reshape(-1))
            mismatches=(np.asarray(minus_zero_counts,dtype=int)!=counts[:,0,z]
                        if minus_zero_counts is not None else None)
        else:
            alt=_signed_log(ans[:,fi,z,0]);mismatches=(counts[:,fi,z]!=counts[:,0,z])
        if len(base)!=len(alt):raise ValueError('GAMER_CAUSAL_ZERO_SPELLING_GAMES_MISMATCH')
        gap=np.abs(alt-base)
        out[name]={'cases':len(base),'median_abs_signed_logit_gap':float(np.median(gap)),
                   'mean_abs_signed_logit_gap':float(np.mean(gap)),
                   'within_0_05_logit':float(np.mean(gap<=.05)),
                   'changed_token_count':int(np.sum(mismatches)) if mismatches is not None else None,
                   'matched_count_median_gap':float(np.median(gap[~mismatches]))
                        if mismatches is not None and np.any(~mismatches) else None,
                   'mismatched_count_median_gap':float(np.median(gap[mismatches]))
                        if mismatches is not None and np.any(mismatches) else None}
    return {'status':'COMPLETE','basis':'synthetic game-matched forms of integer zero',
            'forms':out,'note':'Spelling agreement is a necessary invariant but not certified numerical accuracy.'}


def _minus_zero_token_counts(path,games):
    output=np.full(games,-1,dtype=int)
    with Path(path).open(encoding='utf-8') as f:
        for line in f:
            row=json.loads(line)
            if row.get('form')=='minus_zero':
                ix=int(row['context'])
                if not 0<=ix<games or output[ix]>=0:raise ValueError('GAMER_CAUSAL_BAD_MINUS_ZERO_ROW')
                output[ix]=int(row['token_count'])
    if np.any(output<0):raise ValueError('GAMER_CAUSAL_MISSING_MINUS_ZERO_TOKEN_COUNTS')
    return output


def _certified_metrics(answers,pred):
    target=np.asarray(answers,dtype=np.float64);pred=np.asarray(pred,dtype=np.float64)
    if target.shape!=pred.shape or not len(target):raise ValueError('GAMER_CAUSAL_BAD_CERTIFIED_METRICS')
    zero=(target==0);nonzero=~zero
    def cohort(sel):
        if not np.any(sel):return {'cases':0}
        return {'cases':int(sel.sum()),'mae':float(np.mean(np.abs(pred[sel]-target[sel]))),
                'within_0_1':float(np.mean(np.abs(pred[sel]-target[sel])<=0.1)),
                'within_1e_6':float(np.mean(np.abs(pred[sel]-target[sel])<=1e-6))}
    near_neg=(target<0)&(target>=-1);near_pos=(target>0)&(target<=1)
    result={'all':cohort(np.ones(len(target),dtype=bool)),'zero':cohort(zero),
            'negative':cohort(target<0),'positive':cohort(target>0),
            'small_negative':cohort(near_neg),'small_positive':cohort(near_pos),
            'nonzero_sign_accuracy':float(np.mean(np.sign(pred[nonzero])==np.sign(target[nonzero])))
                if nonzero.any() else None}
    for name,mask in (('small_negative',near_neg),('small_positive',near_pos)):
        result[name]['sign_accuracy']=(float(np.mean(np.sign(pred[mask])==np.sign(target[mask])))
                     if mask.any() else None)
    return result


def _certified_source(expanded,source,identity,*,limit=96):
    """Exact source-order cache; original certified answers, never synthetic."""
    import hashlib
    from tools import nanojev_gamer_v1_expanded_head as exp
    manifest=json.loads((source/'handoff_manifest.json').read_text(encoding='utf-8'))
    rows,_=gamer.load_cases(source/'certified_pairs.jsonl',
          allow_partial=bool(manifest.get('allow_partial_smoke')),
          include_catalog_extensions=bool(manifest.get('bounded_catalog_extensions')),
          include_transition_games=bool(manifest.get('bounded_transition_games')))
    dev=gamer.by_split(rows,'dev')
    cfg=identity['configuration']
    cache_id={**identity,'split':'dev','prompt_sha256':hashlib.sha256(gamer.canonical(
           [gamer.original_prompt(r) for r in dev]).encode('utf-8')).hexdigest()}
    cache=expanded/'feature_cache'/'dev.npz'
    if not cache.is_file():raise FileNotFoundError('GAMER_CAUSAL_CERTIFIED_DEV_CACHE_MISSING:'+str(cache))
    x,_=exp._read_cache(cache,cache_id,len(dev),cfg['input_width'])
    if len(dev)!=len(x):raise ValueError('GAMER_CAUSAL_CERTIFIED_DEV_ALIGNMENT_MISMATCH')
    # Balanced certified selection, with explicit same-game donors, so real
    # labels can be audited under both untouched and matched feature ablations.
    ids=[];chosen=set();rng=np.random.default_rng(47)
    games={}
    for i,row in enumerate(dev):games.setdefault(row['game_id'],[]).append(i)
    strata=([i for i,r in enumerate(dev) if float(r['answer'])==0],
            [i for i,r in enumerate(dev) if -1<=float(r['answer'])<0],
            [i for i,r in enumerate(dev) if 0<float(r['answer'])<=1],
            [i for i,r in enumerate(dev) if float(r['answer'])< -1],
            [i for i,r in enumerate(dev) if float(r['answer'])>1])
    for bucket in strata:
        bucket=np.asarray(bucket,dtype=int);rng.shuffle(bucket)
        quota=max(2,limit//5)
        taken=0
        for i in bucket:
            if taken>=quota or len(ids)>=limit:break
            if int(i) in chosen:continue
            game=dev[int(i)]['game_id']
            donors=[j for j in games[game] if j!=int(i) and j not in chosen]
            if not donors or len(ids)+2>limit:continue
            j=next((j for j in donors if float(dev[j]['answer'])!=float(dev[int(i)]['answer'])),donors[0])
            ids.extend((int(i),int(j)));chosen.update((int(i),int(j)));taken+=2
    for i in range(len(dev)):
        if len(ids)>=limit:break
        if i not in chosen:ids.append(i);chosen.add(i)
    return [dev[i] for i in ids],x[ids],cache,{'full_dev_cases':len(dev),'selected':len(ids),
              'zero_cases':sum(float(dev[i]['answer'])==0 for i in ids)}



def evaluate_certified(torch,head,rows,x,positions,*,device,controls=3,seed=47):
    """Certified accuracy is only on real original prompts, not mutations."""
    if len(rows)!=len(x):raise ValueError('GAMER_CAUSAL_CERTIFIED_ROWS_MISMATCH')
    truth=np.asarray([float(r['answer']) for r in rows],dtype=np.float64)
    baseline=np.asarray([_inverse_signed_log(_infer(torch,head,s,device=device)) for s in x])
    baseline_metrics=_certified_metrics(truth,baseline)
    # Separate labeled-input *ablations*: replace selected coordinates with
    # features from another naturally certified case in same named game. The
    # modified vectors are NOT themselves certified prompts.
    groups={}
    for i,r in enumerate(rows):groups.setdefault(r['game_id'],[]).append(i)
    pairs=[]
    for game,indices in groups.items():
        if len(indices)<2:continue
        for i in indices:
            j=next((j for j in indices if j!=i),None)
            if j is not None:pairs.append((i,j))
    if not pairs:return {'status':'COMPLETE','source_kind':'REAL_CERTIFIED_DEV',
       'baseline':baseline_metrics,'matched_ablation':{'status':'INSUFFICIENT_SAME_GAME_DONORS',
                                                        'pairs':0}}
    cand=[];ctrls=[];target=[];original=[]
    for k,(i,j) in enumerate(pairs):
        target.append(truth[i]);original.append(baseline[i])
        cand.append(float(_inverse_signed_log(_infer(torch,head,
              _swap_blocks(x[i],x[j],positions),device=device))))
        randoms=_controls(positions,count=controls,seed=seed+k*883)
        ctrls.append([float(_inverse_signed_log(_infer(torch,head,
              _swap_blocks(x[i],x[j],b),device=device))) for b in randoms])
    target=np.asarray(target);original=np.asarray(original)
    candidate=_certified_metrics(target,np.asarray(cand));controls_metrics=[
         _certified_metrics(target,np.asarray([row[k] for row in ctrls])) for k in range(controls)]
    return {'status':'COMPLETE','source_kind':'REAL_CERTIFIED_DEV',
            'baseline':baseline_metrics,'matched_ablation':{
              'status':'COMPLETE','pairs':len(pairs),
              'original_on_same_cases':_certified_metrics(target,original),
              'candidate':candidate,'controls':controls_metrics,
              'interpretation':'The transplanted vectors are out-of-distribution internal states; '
                  'accuracy loss is causal sensitivity, not proof of semantic feature importance.'}}


def _load_head(expanded,cr,*,device):
    import torch
    from tools.nanojev_gamer_v1_expanded_diagnostic_train import _load_checkpoint
    from tools.nanojev_gamer_v1_expanded_head import make_expanded_head,SCHEMA as HEAD_SCHEMA
    identity=json.loads((expanded/'provenance.json').read_text(encoding='utf-8'))
    cfg=identity['configuration'];checkpoint=expanded/'best_model.pt'
    state=_load_checkpoint(torch,checkpoint)
    if state.get('identity')!=identity or state.get('schema')!=HEAD_SCHEMA:
        raise ValueError('GAMER_CAUSAL_HEAD_IDENTITY_MISMATCH')
    if int(state['step'])!=int(cr['checkpoint_step']) or cfg['input_width']!=102400:
        raise ValueError('GAMER_CAUSAL_CHECKPOINT_STEP_OR_WIDTH_MISMATCH')
    head=make_expanded_head(torch,cfg['input_width'],tuple(cfg['hidden']))
    head.load_state_dict(state['model'],strict=True)
    del state
    head=head.to(device=device,dtype=torch.float16 if device.startswith('cuda') else torch.float32).eval()
    for p in head.parameters():p.requires_grad_(False)
    return torch,head,identity,checkpoint


def _zero_gate(certified,synthetic,spelling=None):
    """Fail closed. Causal influence is NOT a substitute for learned zero competence."""
    if certified is None:return {'status':'BLOCKED_NO_CERTIFIED_DEV','passed':False}
    m=certified['baseline'];z=m['zero'];sign=m['nonzero_sign_accuracy']
    neg=m.get('small_negative',{'cases':0});pos=m.get('small_positive',{'cases':0})
    if (z['cases']<8 or sign is None or neg['cases']<8 or pos['cases']<8
        or neg.get('sign_accuracy') is None or pos.get('sign_accuracy') is None):
        return {'status':'BLOCKED_INCOMPLETE_ZERO_OR_SIGN_EVIDENCE','passed':False,
                'zero_cases':z['cases'],'small_negative_cases':neg['cases'],
                'small_positive_cases':pos['cases']}
    effect=synthetic['input_position_swap']['touch_zero']
    influence=effect.get('candidate_minus_control_gain')
    measured=effect.get('paired_signflip_p')
    gaps=([r['median_abs_signed_logit_gap'] for r in spelling['forms'].values()]
          if spelling is not None else [])
    spelling_ok=(len(gaps)==3 and max(gaps)<=.05)
    condition=(z['within_1e_6']>=.95 and z['within_0_1']>=.95 and sign>=.95
               and neg['sign_accuracy']>=.95 and pos['sign_accuracy']>=.95
               and effect.get('cases',0)>=8 and influence is not None and influence>0
               and measured is not None and measured<=.01 and spelling_ok)
    return {'status':'PASS' if condition else 'BLOCKED_ZERO_LINE_NOT_CERTIFIED',
            'passed':bool(condition),'requires':{'zero_exact_rate':.95,
            'zero_within_0_1_rate':.95,'nonzero_sign_rate':.95,
            'zero_causal_pairs':8,'causal_p_max':.01,'candidate_effect_better_than_controls':True,
            'small_negative_cases_min':8,'small_positive_cases_min':8,
            'small_negative_sign_rate_min':.95,'small_positive_sign_rate_min':.95,
            'zero_spelling_median_gap_max':.05,'zero_spelling_forms':3},
            'observed':{'zero_cases':z['cases'],'zero_exact_rate':z['within_1e_6'],
            'zero_within_0_1_rate':z['within_0_1'],'nonzero_sign_rate':sign,
            'zero_causal_pairs':effect.get('cases'),'causal_p':measured,
            'small_negative_cases':neg['cases'],'small_positive_cases':pos['cases'],
            'small_negative_sign_rate':neg['sign_accuracy'],
            'small_positive_sign_rate':pos['sign_accuracy'],
            'candidate_minus_control_donor_gain':influence,
            'max_zero_spelling_median_gap':max(gaps) if gaps else None}}


def run_causal(localization_run,*,output=None,device='cuda',max_pairs=120,
               top=4,controls=3,permutations=99,certified_cases=96,
               skip_certified=False,seed=47):
    if not 12<=max_pairs<=2000 or not 1<=top<=8 or not 1<=controls<=12 or not 0<=permutations<=999:
        raise ValueError('GAMER_CAUSAL_INVALID_EXPERIMENT_BUDGET')
    if not 12<=certified_cases<=438:raise ValueError('GAMER_CAUSAL_INVALID_CERTIFIED_BUDGET')
    record,cr,capture,cap_files=_read_source(localization_run)
    expanded=Path(cr['expanded_run']).resolve();source=Path(cr['source_run']).resolve()
    ckpt=expanded/'best_model.pt';provenance=expanded/'provenance.json'
    for f in (ckpt,provenance):
        if not f.is_file():raise FileNotFoundError(f)
    protected={str(f):_hash(f) for f in [ckpt,provenance,Path(localization_run)/'final_report.json']}
    out=dist._destination(Path(localization_run).resolve(),output,'-causal-path')
    for root in (Path(localization_run).resolve(),capture,expanded,source):
        if out==root or out in root.parents or root in out.parents:
            raise ValueError('GAMER_CAUSAL_OUTPUT_OVERLAPS_SOURCE')
    games=[c['game_id'] for c in cr['contexts']]
    choices,consensus=_fold_positions(record,games,top=top)
    if device.startswith('cuda'):
        import torch
        if not torch.cuda.is_available():raise RuntimeError('GAMER_CAUSAL_CUDA_NOT_AVAILABLE')
    torch,head,identity,checkpoint=_load_head(expanded,cr,device=device)
    print('GAMER CAUSAL PATH: '+str(out),flush=True)
    print(f'  checkpoint step={cr["checkpoint_step"]}; frozen LoRA/C0; '
          f'train-selected positions, {max_pairs} synthetic counterfactuals; NO TRAINING',flush=True)
    minus_counts=_minus_zero_token_counts(cap_files[2],len(games))
    with np.load(cap_files[1],allow_pickle=False) as npz:
        for k in ('qwen_final_100','c0_hidden_1','c0_answer','token_counts','values'):
            if k not in npz:raise ValueError('GAMER_CAUSAL_MISSING_CAPTURE_STAGE:'+k)
        spelling=zero_spelling_invariance(npz['c0_answer'],npz['values'],npz['token_counts'],
                 minus_zero=npz['minus_zero__c0_answer'] if 'minus_zero__c0_answer' in npz else None,
                 minus_zero_counts=minus_counts)
        synthetic=evaluate_transplants(torch,head,npz['qwen_final_100'],npz['c0_hidden_1'],
                    npz['c0_answer'],npz['token_counts'],npz['values'],games,choices,
                    device=device,max_pairs=max_pairs,controls=controls,
                    permutations=permutations,seed=seed)
    certified=None;certified_origin=None
    if not skip_certified:
        rows,features,cache,certified_origin=_certified_source(expanded,source,identity,limit=certified_cases)
        protected[str(cache)]=_hash(cache)
        print(f'  certified dev: {len(rows)} original prompts, '
              f'{certified_origin["zero_cases"]} exact-zero targets',flush=True)
        certified=evaluate_certified(torch,head,rows,features,consensus,
                                  device=device,controls=controls,seed=seed)
    gate=_zero_gate(certified,synthetic,spelling)
    print('  causal zero paired difference: '
       +str(synthetic['input_position_swap']['touch_zero'].get('candidate_minus_control_gain')),
       flush=True)
    print(f'  zero acceptance: {gate["status"]}',flush=True)
    for file,sha in protected.items():
        if _hash(file)!=sha:raise ValueError('GAMER_CAUSAL_PROTECTED_SOURCE_CHANGED:'+file)
    # Verify data was never mutated, including the large frozen activation NPZ.
    for p in cap_files:
        if _hash(p)!=record['source_sha256'][p.name]:
            raise ValueError('GAMER_CAUSAL_CAPTURE_CHANGED:'+p.name)
    result={'schema':SCHEMA,'status':'COMPLETE_READ_ONLY',
       'source_localization':str(Path(localization_run).resolve()),
       'source_capture':str(capture),'expanded_run':str(expanded),
       'checkpoint_step':cr['checkpoint_step'],'checkpoint_sha256':protected[str(ckpt)],
       'source_sha256':record['source_sha256'],'candidate_positions_global':consensus,
       'settings':{'device':device,'max_pairs':max_pairs,'top_positions':top,
                   'matched_controls':controls,'paired_permutations':permutations,
                   'certified_cases_limit':certified_cases,'skip_certified':skip_certified,'seed':seed},
       'synthetic_counterfactual_influence':synthetic,
       'zero_spelling_invariance':spelling,
       'certified_dev':certified,'certified_origin':certified_origin,
       'zero_line_gate':gate,'promotion':'BLOCKED_NO_TRAINING',
       'interpretation_constraints':[
         'Causal intervention is on frozen C0 input and hidden activations, not on Qwen weights.',
         'Selected Qwen blocks were chosen in other games by localization training folds; no heldout-game re-selection occurs.',
         'Synthetic register substitutions have no certified correct answers; donor effects are measured as logit changes only.',
         'Certified-dev accuracy uses original labels and pre-existing Qwen feature cache; transplanted internal states are artificial.',
         'Nearby random block controls match the number of intervened 1024D positions; their semantic roles may still differ.',
         'Paired sign-flip inference clusters by game to avoid treating repeated within-game pairs as independent.',
         'Exploratory candidate selection and multiple intervention cohorts are not adjusted for familywise multiple comparisons; confirmation needs new games.',
         'The first-hidden-layer 64D intervention is exploratory, not a validated information-location test.',
         'A nonzero logit effect establishes dependence but not use of an abstract numerical magnitude code.',
         'The zero-line gate uses exact-zero certified labels AND three literal-zero spelling controls; predictive change is never counted as correct-zero.',
         'No LoRA, C0 weights, gradients, optimizer, Qwen features, cached dev inputs, or prior reports were modified.',
       ]}
    out.mkdir(parents=True,exist_ok=False)
    gamer.atomic_json(out/'final_report.json',result)
    print('GAMER CAUSAL PATH COMPLETE: '+str(out/'final_report.json'),flush=True)
    return result


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run',type=Path,required=True)
    ap.add_argument('--output',type=Path)
    ap.add_argument('--device',default='cuda')
    ap.add_argument('--max-pairs',type=int,default=120)
    ap.add_argument('--top',type=int,default=4)
    ap.add_argument('--controls',type=int,default=3)
    ap.add_argument('--permutations',type=int,default=99)
    ap.add_argument('--certified-cases',type=int,default=96)
    ap.add_argument('--skip-certified',action='store_true')
    ap.add_argument('--seed',type=int,default=47)
    a=ap.parse_args()
    run_causal(a.run,output=a.output,device=a.device,max_pairs=a.max_pairs,
        top=a.top,controls=a.controls,permutations=a.permutations,
        certified_cases=a.certified_cases,skip_certified=a.skip_certified,seed=a.seed)
