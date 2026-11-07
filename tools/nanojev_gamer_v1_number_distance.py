#!/usr/bin/env python3
"""Read-only GAMER numeric-distance microscope.

Tests whether a +1/+2/+3 numerical *displacement*, after subtracting the
same prompt's activation at a different number, transfers across unseen games,
numerical regions, signs and numeral spellings.  A shared slope is estimated
from training contexts only; test contexts never fit calibration/normalization.
No model or optimizer is trained.  Text edits are synthetic, not certified
correct-answer cases.  Probe performance is not proof of arithmetic ability.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_radius_activations as radius
from tools import nanojev_gamer_v1_handoff as gamer

SCHEMA='nanojev.gamer.v1.number-distance.v1'
FORMS=('digits','signed','words')
STAGES=('embedding_last','embedding_mean100',
        'qwen_layer_07_last','qwen_layer_07_mean100',
        'qwen_layer_14_mean100','qwen_layer_21_mean100',
        'qwen_final_last','qwen_final_mean100',
        'qwen_final_100','c0_input_norm','c0_hidden_1','c0_hidden_2',
        'c0_hidden_3','c0_bottleneck','c0_answer')
_WORDS=('zero','one','two','three','four','five','six','seven','eight','nine',
        'ten','eleven','twelve','thirteen','fourteen','fifteen','sixteen',
        'seventeen','eighteen','nineteen','twenty')


def numeral(value,form):
    if type(value) is not int or abs(value)>20:raise ValueError('GAMER_DISTANCE_NUMBER_OUT_OF_RANGE')
    if form=='digits':return str(value)
    if form=='signed':return f'{value:+d}'
    if form=='words':return ('minus ' if value<0 else '')+_WORDS[abs(value)]
    raise ValueError('GAMER_DISTANCE_UNKNOWN_NUMERAL_FORM')


def substitute(prompt,span,value,form):
    a,b=span
    return prompt[:a]+numeral(value,form)+prompt[b:]


def _group_folds(contexts,folds=4,seed=47):
    if len(contexts)<folds*2:raise ValueError('GAMER_DISTANCE_TOO_FEW_CONTEXTS')
    if len(set(contexts))!=len(contexts):raise ValueError('GAMER_DISTANCE_DUPLICATE_GAME')
    buckets=np.array_split(np.random.default_rng(seed).permutation(len(contexts)),folds)
    all_ix=np.arange(len(contexts))
    return [(all_ix[~np.isin(all_ix,test)],np.asarray(test,dtype=int)) for test in buckets]


def _same_sign(a,b,sign):
    return (a>=1 and b>=1) if sign=='positive' else (a<=-1 and b<=-1)


def _train_direction(a,counts,values,train_ix,forms,signs,*,parity=0):
    """Equal weight per TRAIN GAME. All training slopes are numeric +1 steps."""
    locate={int(v):i for i,v in enumerate(values)}
    by_game=[];total_steps=0
    for game in train_ix:
        steps=[]
        for form in forms:
            fi=FORMS.index(form)
            for sign in signs:
                for base in range(int(values[0]),int(values[-1])):
                    if base%2!=parity or not _same_sign(base,base+1,sign):continue
                    ia,ib=locate[base],locate[base+1]
                    if counts[game,fi,ia]!=counts[game,fi,ib]:continue
                    steps.append(np.asarray(a[game,fi,ib]-a[game,fi,ia],dtype=np.float64))
        if steps:
            by_game.append(np.mean(steps,axis=0))
            total_steps+=len(steps)
    if len(by_game)<3:return None,None,0
    return np.mean(by_game,axis=0),np.stack(by_game),total_steps


def _test_pairs(a,counts,values,test_ix,forms,signs,*,parity=1,offsets=(1,2,3),zero=False):
    locate={int(v):i for i,v in enumerate(values)}
    probes=[];truth=[];metadata=[];excluded=0
    for game in test_ix:
        for form in forms:
            fi=FORMS.index(form)
            for sign in signs:
                if zero and sign!='positive':continue
                for base in range(int(values[0]),int(values[-1])+1):
                    if base%2!=parity:continue
                    for offset in offsets:
                        end=base+offset
                        if end not in locate:continue
                        if zero:
                            if not (base<=0<=end and base<end):continue
                        elif not _same_sign(base,end,sign):continue
                        ia,ib=locate[base],locate[end]
                        if counts[game,fi,ia]!=counts[game,fi,ib]:
                            excluded+=1;continue
                        probes.append(a[game,fi,ib]-a[game,fi,ia]);truth.append(offset)
                        metadata.append((int(game),form,sign,base,end))
    if not probes:return np.empty((0,a.shape[-1]),dtype=np.float32),np.array([],dtype=float),[],excluded
    return np.asarray(probes,dtype=np.float32),np.asarray(truth,dtype=np.float64),metadata,excluded


def _metrics(y,p):
    if not len(y):return {'cases':0,'r2_vs_zero':None,'mae':None,'slope_ratio':None,'sign_accuracy':None}
    baseline=float(np.sum(y*y));err=float(np.sum((y-p)**2))
    return {'cases':len(y),'r2_vs_zero':float(1-err/baseline) if baseline else None,
            'mae':float(np.mean(np.abs(y-p))),
            'slope_ratio':float(np.dot(y,p)/baseline) if baseline else None,
            'sign_accuracy':float(np.mean(np.sign(y)==np.sign(p))),
            'by_delta':{str(int(d)):{'cases':int(np.sum(y==d)),
                 'mean_predicted_delta':float(np.mean(p[y==d])),
                 'mae':float(np.mean(np.abs(d-p[y==d])))}
                 for d in sorted(set(y))}}


def _predict_probes(probes,direction):
    mag=float(direction@direction)
    if mag<1e-20:return np.zeros(len(probes),dtype=np.float64)
    return np.asarray(probes,dtype=np.float64)@direction/mag


def analyze_displacements(arr,token_counts,values,games,*,folds=4,controls=16,seed=47):
    """Four-fold, held-out-game transfer of a single +1 activation direction.

    Training uses even-start +1 steps. Testing uses odd-start changes of +1,
    +2, +3; the +3 length is not fit, and word spellings are entirely held out
    in cross-form/sign arms. No test-game activation enters fitting, including
    unsupervised normalization. Zero-crossing probes excluded from slope fitting.
    """
    a=np.asarray(arr,dtype=np.float32);counts=np.asarray(token_counts)
    v=np.asarray(values,dtype=np.int32)
    if a.ndim!=4 or a.shape[:3]!=(len(games),len(FORMS),len(v)) or counts.shape!=a.shape[:3]:
        raise ValueError('GAMER_DISTANCE_BAD_CAPTURE_SHAPE')
    if not np.isfinite(a).all() or not np.array_equal(v,np.arange(-int(v[-1]),int(v[-1])+1)) or int(v[-1])<6:
        raise ValueError('GAMER_DISTANCE_INVALID_RADIUS_DATA')
    if not 0<=controls<=64:raise ValueError('GAMER_DISTANCE_BAD_CONTROL_BUDGET')
    experiments={
      'heldout_games':(('digits','signed'),('positive','negative'),('digits','signed'),('positive','negative'),False),
      'heldout_words':(('digits',),('positive','negative'),('words',),('positive','negative'),False),
      'positive_digits_to_negative_words':(('digits',),('positive',),('words',),('negative',),False),
      'negative_digits_to_positive_words':(('digits',),('negative',),('words',),('positive',),False),
      'unseen_zero_crossing_words':(('digits',),('positive','negative'),('words',),('positive','negative'),True),
    }
    out={}
    for experiment,(train_forms,train_signs,test_forms,test_signs,zero) in experiments.items():
        yy=[];pp=[];nulls=[[] for _ in range(controls)];games_used=[];dropped=0;steps=0
        for fold_id,(tr,te) in enumerate(_group_folds(games,folds,seed)):
            direction,per_game,nsteps=_train_direction(a,counts,v,tr,train_forms,train_signs)
            if direction is None:continue
            steps+=nsteps
            probes,target,_,excluded=_test_pairs(a,counts,v,te,test_forms,test_signs,zero=zero)
            dropped+=excluded
            if not len(target):continue
            prediction=_predict_probes(probes,direction)
            yy.extend(target);pp.extend(prediction);games_used.extend(te.tolist())
            for j in range(controls):
                rng=np.random.default_rng(seed+fold_id*70001+j*1009)
                # Per-game sign randomization destroys consistent numerical
                # orientation while preserving all feature distributions.
                signs=rng.choice((-1.,1.),size=len(per_game))
                null_direction=np.mean(per_game*signs[:,None],axis=0)
                nulls[j].extend(_predict_probes(probes,null_direction))
        if not yy:
            out[experiment]={'status':'INSUFFICIENT_MATCHED_PAIRS','excluded_token_mismatch':dropped};continue
        truth=np.asarray(yy,dtype=np.float64);pred=np.asarray(pp,dtype=np.float64)
        m=_metrics(truth,pred)
        null_scores=[_metrics(truth,np.asarray(p,dtype=np.float64))['r2_vs_zero'] for p in nulls] if controls else []
        finite_null=[x for x in null_scores if x is not None]
        m.update({'status':'COMPLETE','heldout_game_count':len(set(games_used)),
                  'training_unit_steps_across_folds':steps,
                  'excluded_token_mismatch':dropped,
                  'null_mean_r2':float(np.mean(finite_null)) if finite_null else None,
                  'null_max_r2':float(np.max(finite_null)) if finite_null else None,
                  'null_p_one_sided':float((1+sum(x>=m['r2_vs_zero'] for x in finite_null))/(1+len(finite_null))) if finite_null else None,
                  'training_forms':train_forms,'test_forms':test_forms,
                  'training_signs':train_signs,'test_signs':test_signs,
                  'training_base_parity':'even','testing_base_parity':'odd',
                  'note':'Test displacements use TWO test-game activations; this is not single-prompt numerical decoding.'})
        out[experiment]=m
    return out


def geometry_checks(arr,counts,values,zero_minus=None):
    """Within-game geometry and cross-form zero checks, no fitted decoder."""
    x=np.asarray(arr,dtype=np.float32);n=np.asarray(counts);v=np.asarray(values)
    loc={int(num):i for i,num in enumerate(v)};r=int(v[-1]);i0=loc[0]
    align=[];ratios=[];zero_mid=[];syntax_zero=[];zero_token_lengths=[];steps=0
    for game in range(x.shape[0]):
        for fi,form in enumerate(FORMS):
            for sign in (-1,1):
                bases=range(1,r-1) if sign==1 else range(-r,-2)
                for b in bases:
                    a,b1,b2=loc[b],loc[b+1],loc[b+2]
                    if len({int(n[game,fi,j]) for j in (a,b1,b2)})!=1:continue
                    u=np.asarray(x[game,fi,b1]-x[game,fi,a],dtype=np.float64)
                    w=np.asarray(x[game,fi,b2]-x[game,fi,b1],dtype=np.float64)
                    un=np.linalg.norm(u);wn=np.linalg.norm(w)
                    if un>1e-10 and wn>1e-10:
                        align.append(float(u@w/un/wn));steps+=1
                        ratios.append(float(np.linalg.norm(u+w)/(un+wn)))
            if n[game,fi,loc[-1]]==n[game,fi,i0]==n[game,fi,loc[1]]:
                left=np.asarray(x[game,fi,i0]-x[game,fi,loc[-1]],dtype=np.float64)
                right=np.asarray(x[game,fi,loc[1]]-x[game,fi,i0],dtype=np.float64)
                denom=np.linalg.norm(left)+np.linalg.norm(right)
                if denom>1e-10:zero_mid.append(float(np.linalg.norm(right-left)/denom))
        for f in (1,2):
            dx=np.linalg.norm(np.asarray(x[game,f,i0]-x[game,0,i0],dtype=np.float64))
            adjacent=[]
            for base in (1,2,-3,-2):
                if n[game,0,loc[base]]==n[game,0,loc[base+1]]:
                    adjacent.append(float(np.linalg.norm(np.asarray(x[game,0,loc[base+1]]-x[game,0,loc[base]],dtype=np.float64))))
            if adjacent and np.median(adjacent)>1e-10:
                syntax_zero.append({'from':'digits','to':FORMS[f],
                                    'distance_over_numeric_step':float(dx/np.median(adjacent))})
        if zero_minus is not None:
            dx=np.linalg.norm(np.asarray(zero_minus[game]-x[game,0,i0],dtype=np.float64))
            adjacent=np.linalg.norm(np.asarray(x[game,0,loc[2]]-x[game,0,loc[1]],dtype=np.float64))
            if adjacent>1e-10:
                syntax_zero.append({'from':'digits','to':'minus_zero',
                                    'distance_over_numeric_step':float(dx/adjacent)})
    def mean(xs):return float(np.mean(xs)) if xs else None
    syntax={form:{'cases':len(rows),'median_distance_over_numeric_step':float(np.median(rows)) if rows else None}
       for form,rows in ((form,[r['distance_over_numeric_step'] for r in syntax_zero if r['to']==form])
                         for form in ('signed','words','minus_zero'))}
    return {'adjacent_step_cosine':mean(align),'adjacent_step_pairs':steps,
            'two_step_path_straightness':mean(ratios),
            'zero_midpoint_curvature':mean(zero_mid), 'zero_midpoint_contexts':len(zero_mid),
            'zero_spelling_distance_vs_one_numeric_step':syntax,
            'note':'Numerical-step geometry is descriptive; equal literal spellings and zero spellings can have different token counts.'}


def _destination(root,explicit,suffix):
    if explicit:
        dest=Path(explicit).resolve()
        if dest.exists():raise FileExistsError(dest)
    else:
        base=root.with_name(root.name+suffix)
        dest=None
        for i in range(1,10000):
            proposed=base.with_name(base.name+f'-{i:04d}')
            if not proposed.exists():dest=proposed;break
        if dest is None:raise RuntimeError('GAMER_DISTANCE_NO_OUTPUT_SLOTS')
    return dest


def _analyze_arrays(npz,games,*,folds,controls):
    v=npz['values'];counts=npz['token_counts']
    stages={};summary={}
    for stage in STAGES:
        if stage not in npz.files:raise ValueError('GAMER_DISTANCE_STAGE_MISSING: '+stage)
        data=npz[stage]
        result=analyze_displacements(data,counts,v,games,folds=folds,controls=controls)
        geom=geometry_checks(data,counts,v,npz['minus_zero__'+stage] if 'minus_zero__'+stage in npz.files else None)
        stages[stage]={'transfer':result,'geometry':geom}
        tr=result['heldout_words'];cross=result['positive_digits_to_negative_words']
        summary[stage]={'heldout_words_r2':tr.get('r2_vs_zero'),
                        'positive_digits_to_negative_words_r2':cross.get('r2_vs_zero'),
                        'adjacent_step_cosine':geom['adjacent_step_cosine']}
        def fmt(x):return 'N/A' if x is None else f'{x:.4f}'
        print(f'  {stage}: word_transfer_R2={fmt(tr.get("r2_vs_zero"))}'
              f' cross_sign_R2={fmt(cross.get("r2_vs_zero"))}'
              f' step_cos={fmt(geom["adjacent_step_cosine"])}',flush=True)
    return stages,summary


def analyze_capture(source,*,output=None,folds=4,controls=16):
    """Offline rescore of existing capture, never accesses CUDA/model weights."""
    src=Path(source).resolve()
    files=[src/'final_report.json',src/'activations.npz',src/'interventions.jsonl']
    for f in files:
        if not f.is_file():raise FileNotFoundError(f)
    original=json.loads(files[0].read_text(encoding='utf-8'))
    if original.get('schema')!=SCHEMA or original.get('status')!='COMPLETE_READ_ONLY':
        raise ValueError('GAMER_DISTANCE_BAD_CAPTURE_REPORT')
    digests={f.name:radius._canonical_hash(f) for f in files}
    games=[c['game_id'] for c in original['contexts']]
    dest=_destination(src,output,'-analysis')
    for protected in (src,original['expanded_run'],original['source_run']):
        root=Path(protected).resolve()
        if dest==root or root in dest.parents or dest in root.parents:
            raise ValueError('GAMER_DISTANCE_OUTPUT_OVERLAPS_SOURCE')
    with np.load(files[1],allow_pickle=False) as npz:
        stages,summary=_analyze_arrays(npz,games,folds=folds,controls=controls)
    if any(radius._canonical_hash(f)!=digests[f.name] for f in files):
        raise ValueError('GAMER_DISTANCE_CAPTURE_CHANGED_DURING_ANALYSIS')
    dest.mkdir(parents=True,exist_ok=False)
    report={'schema':SCHEMA+'.analysis','status':'COMPLETE_READ_ONLY',
            'capture':str(src),'source_sha256':digests,'folds':folds,'controls':controls,
            'by_stage':stages,'layer_comparison':summary,
            'limitations':['Two-activation displacement probes are not deployable one-prompt numerical decoders',
             'A shared direction may encode token transitions rather than mathematical distance',
             'Synthetic changed-register prompts are not certified answer cases',
             'No numerical structure or information destruction is proven from an individual failed decoder']}
    gamer.atomic_json(dest/'final_report.json',report)
    print('GAMER NUMBER DISTANCE ANALYSIS COMPLETE: '+str(dest/'final_report.json'),flush=True)
    return report


def run_distance(expanded_run,*,source_run=None,output=None,device='cuda',contexts=24,
                 width=8,checkpoint='best',max_length=None,curvature=None,folds=4,controls=16):
    import torch
    from tools.nanojev_gamer_v1_expanded_diagnostic_train import _source_run_from_expanded,_load_checkpoint
    from tools.nanojev_gamer_v1_diagnostic_train import _source_inputs,_output_path
    from tools import nanojev_gamer_v1_expanded_head as exp
    if contexts<folds*2 or width<6 or width>20 or folds<2 or folds>12 or controls<0 or controls>64:
        raise ValueError('GAMER_DISTANCE_INVALID_EXPERIMENT_BUDGET')
    expanded=Path(expanded_run).resolve()
    source=Path(source_run).resolve() if source_run else _source_run_from_expanded(expanded)
    src=_source_inputs(source)
    identity=exp._json(expanded/'provenance.json');cfg=identity.get('configuration',{})
    if exp._identity(src,cfg)!=identity or cfg.get('samples')!=100:
        raise ValueError('GAMER_DISTANCE_EXPANDED_SOURCE_IDENTITY_INVALID')
    if checkpoint not in ('best','latest'):raise ValueError('GAMER_DISTANCE_BAD_CHECKPOINT')
    ckpt=expanded/('best_model.pt' if checkpoint=='best' else 'latest.pt')
    if not ckpt.is_file():raise FileNotFoundError(ckpt)
    protected=[ckpt,expanded/'provenance.json',source/'certified_pairs.jsonl']
    hashes={str(p):radius._canonical_hash(p) for p in protected}
    adapter_hash=gamer.file_tree_sha256(src['source_adapter'])
    if adapter_hash!=identity['adapter_sha256']:raise ValueError('GAMER_DISTANCE_ADAPTER_DRIFT')
    selected=radius.contexts_from_rows(src['full_split_rows']['dev']+src['full_split_rows']['train'],count=contexts)
    values=np.arange(-width,width+1,dtype=np.int32)
    out=_destination(expanded,output,'-number-distance')
    for root in (source,expanded):
        if out==root or root in out.parents or out in root.parents:
            raise ValueError('GAMER_DISTANCE_OUTPUT_OVERLAPS_SOURCE')
    radius_curvature=float(cfg['curvature'] if curvature is None else curvature)
    max_length=int(cfg['max_length'] if max_length is None else max_length)
    if not np.isfinite(radius_curvature) or not 0<=radius_curvature<=20 or max_length>int(cfg['max_length']):
        raise ValueError('GAMER_DISTANCE_BAD_CAPTURE_CONFIGURATION')
    state=_load_checkpoint(torch,ckpt)
    if state.get('identity')!=identity or state.get('schema')!=exp.SCHEMA:
        raise ValueError('GAMER_DISTANCE_CHECKPOINT_LINEAGE_INVALID')
    weights=state['model'] if checkpoint=='best' else state['trainer']['model']
    print(f'GAMER NUMBER DISTANCE: {out}',flush=True)
    print(f'  frozen LoRA; C0 {checkpoint} step={state["step"]}; games={len(selected)};'
          f' range=[-{width},{width}]; numeral forms={FORMS}; NO TRAINING',flush=True)
    _,tok,qwen=gamer.model_components(identity['model'],device,adapter_dir=src['source_adapter'])
    qwen.eval()
    core=(qwen.get_base_model() if hasattr(qwen,'get_base_model') else qwen).model
    layer_count=len(core.layers)
    layers=tuple(sorted(set(i for i in (layer_count//4-1,layer_count//2-1,3*layer_count//4-1) if i>=0)))
    from tools.nanojev_gamer_v1_expanded_head import make_expanded_head
    head=make_expanded_head(torch,cfg['input_width'],tuple(cfg['hidden']))
    head.load_state_dict(weights)
    head=head.to(device=device,dtype=torch.float16 if device.startswith('cuda') else torch.float32)
    head.eval()
    shape=(len(selected),len(FORMS),len(values))
    counts=np.zeros(shape,dtype=np.int32)
    captures={};minus_zero={};records=[]
    # Create output directory before GPU work, so partial runs can be
    # recognized as incomplete rather than masquerading as valid captures.
    out.mkdir(parents=True,exist_ok=False)
    for gi,ctx in enumerate(selected):
        suffix_ids=[]
        for fi,form in enumerate(FORMS):
            suffix=[]
            for vi,value in enumerate(values):
                prompt=substitute(ctx['prompt'],ctx['span'],int(value),form)
                q,meta=radius.capture_qwen(torch,qwen,tok,prompt,device=device,
                    max_length=max_length,curvature=radius_curvature,layer_indices=layers)
                h=radius.capture_c0(torch,head,q['qwen_final_100'],device=device)
                stages={**q,**h}
                if not captures:
                    captures={s:np.empty(shape+(len(stages[s]),),dtype=np.float32) for s in STAGES}
                for s in STAGES:captures[s][gi,fi,vi]=stages[s]
                counts[gi,fi,vi]=meta['token_count']
                suffix.append(meta['last_token_id'])
                records.append({'game':ctx['game_id'],'context':gi,'form':form,
                  'value':int(value),'register':ctx['register_name'],
                  'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),**meta})
            if len(set(suffix))!=1:raise ValueError('GAMER_DISTANCE_SUFFIX_CHANGED_WITHIN_NUMERAL_FORM')
            suffix_ids.extend(suffix)
        if len(set(suffix_ids))!=1:raise ValueError('GAMER_DISTANCE_SUFFIX_CHANGED_ACROSS_FORMS')
        # Distinguish literal -0 from 0, +0 and the word zero; it is a
        # numeral-spelling control, not a separate numerical value.
        neg_zero=ctx['prompt'][:ctx['span'][0]]+'-0'+ctx['prompt'][ctx['span'][1]:]
        q,meta=radius.capture_qwen(torch,qwen,tok,neg_zero,device=device,
            max_length=max_length,curvature=radius_curvature,layer_indices=layers)
        h=radius.capture_c0(torch,head,q['qwen_final_100'],device=device)
        zero_trace={**q,**h}
        for s in STAGES:
            if s not in minus_zero:minus_zero[s]=np.empty((len(selected),len(zero_trace[s])),dtype=np.float32)
            minus_zero[s][gi]=zero_trace[s]
        records.append({'game':ctx['game_id'],'context':gi,'form':'minus_zero',
           'value':0,'register':ctx['register_name'],
           'prompt_sha256':hashlib.sha256(neg_zero.encode()).hexdigest(),**meta})
        print(f'  game {gi+1}/{len(selected)}: {ctx["game_id"]} ({ctx["register_name"]})',flush=True)
    checkpoint_step=int(state['step'])
    del state,head,qwen
    np.savez_compressed(out/'activations.npz',values=values,token_counts=counts,
        **captures,**{'minus_zero__'+s:a for s,a in minus_zero.items()})
    with (out/'interventions.jsonl').open('w',encoding='utf-8') as f:
        for r in records:f.write(gamer.canonical(r)+'\n')
    for p,sha in hashes.items():
        if radius._canonical_hash(p)!=sha:raise ValueError('GAMER_DISTANCE_SOURCE_DRIFT: '+p)
    if gamer.file_tree_sha256(src['source_adapter'])!=adapter_hash:
        raise ValueError('GAMER_DISTANCE_ADAPTER_CHANGED_DURING_RUN')
    report={'schema':SCHEMA,'status':'COMPLETE_READ_ONLY','source_run':str(source),
       'expanded_run':str(expanded),'checkpoint':checkpoint,'checkpoint_step':checkpoint_step,
       'contexts':[{k:v for k,v in ctx.items() if k!='prompt'} for ctx in selected],
       'range':[-width,width],'formats':FORMS,'source_sha256':hashes,'adapter_sha256':adapter_hash,
       'token_count_all_values_equal_by_game_form':int(np.sum(np.all(counts==counts[:,:,:1],axis=2))),
       'artifacts':['activations.npz','interventions.jsonl','final_report.json'],
       'limitations':['Text substitutions are not certified game answer cases',
          'Per-game activation differences require both input variants; not one-shot decoding',
          'Same-signed integer transitions may reflect token identity, not quantity',
          'Token-count mismatches are excluded from transfer decoding']}
    gamer.atomic_json(out/'final_report.json',report)
    print('GAMER NUMBER DISTANCE CAPTURE COMPLETE: '+str(out/'final_report.json'),flush=True)
    return analyze_capture(out,folds=folds,controls=controls)


def main(argv=None):
    import argparse
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run',type=Path,required=True)
    ap.add_argument('--analyze',action='store_true')
    ap.add_argument('--output',type=Path,default=None)
    ap.add_argument('--device',default='cuda')
    ap.add_argument('--contexts',type=int,default=24)
    ap.add_argument('--width',type=int,default=8)
    ap.add_argument('--checkpoint',choices=('best','latest'),default='best')
    ap.add_argument('--folds',type=int,default=4)
    ap.add_argument('--controls',type=int,default=16)
    a=ap.parse_args(argv)
    if a.analyze:return analyze_capture(a.run,output=a.output,folds=a.folds,controls=a.controls)
    return run_distance(a.run,output=a.output,device=a.device,contexts=a.contexts,width=a.width,
                        checkpoint=a.checkpoint,folds=a.folds,controls=a.controls)

if __name__=='__main__':main()
