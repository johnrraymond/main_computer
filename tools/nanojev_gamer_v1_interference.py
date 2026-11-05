#!/usr/bin/env python3
"""Opt-in, CPU-only causal GAMER C0 Wide interference experiments.

Full-matrix experiments restore the saved head weights and AdamW state.
Optimizer-isolation experiments compare saved AdamW, fresh AdamW and plain
SGD from identical weights, batches and gradients with matched update norms.
Qwen/LoRA are never loaded or updated; cached prompt activations are verified.
These are local causal perturbations, NOT identification of historical batches.
"""
from __future__ import annotations

from collections import defaultdict
import csv
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np

SCHEMA = 'nanojev.gamer.v1.interference-diagnostic.v1'
FAMILIES = ('calculation', 'probabilistic_outcomes', 'strategic_payoff', 'state_transition')


def target_zone(answer, radius):
    y = float(answer)
    if not math.isfinite(y):
        raise ValueError('GAMER_INTERFERENCE_NONFINITE_TARGET')
    if y == 0: return 'zero'
    if -radius <= y < 0: return 'small_negative'
    if 0 < y <= radius: return 'small_positive'
    return 'outer_negative' if y < 0 else 'outer_positive'


def table_signature(row):
    families = row.get('table_families') or ()
    if isinstance(families, str):
        raise ValueError('GAMER_INTERFERENCE_INVALID_TABLE_FAMILIES')
    unknown = set(families) - set(FAMILIES)
    if unknown:
        raise ValueError('GAMER_INTERFERENCE_UNKNOWN_TABLE_FAMILY: '+','.join(sorted(unknown)))
    return '+'.join(sorted(set(families))) if families else 'unclassified'


def source_groups(rows, radius):
    """Nonexclusive family groups, exact overlapping signatures, and games.

    The family×numeric-band cohorts offer magnitude-matched comparisons;
    a ubiquitous calculation tag is never misreported as an exclusive family.
    """
    groups = defaultdict(list)
    for i, row in enumerate(rows):
        game = str(row['game_id'])
        sig = table_signature(row)
        zone = target_zone(row['answer'], radius)
        groups['mixed:all'].append(i)
        groups['game:'+game].append(i)
        groups['signature:'+sig].append(i)
        groups['zone:'+zone].append(i)
        for family in set(row.get('table_families') or ()):
            groups['family:'+family].append(i)
            groups['family_zone:'+family+'|'+zone].append(i)
    return {name: tuple(values) for name, values in sorted(groups.items())}


def draw_game_balanced(rows, indices, rng, batch_size):
    """Same batch size across experiments; game-balanced with replacement."""
    games = defaultdict(list)
    for index in indices:
        games[str(rows[index]['game_id'])].append(int(index))
    names = sorted(games)
    return _draw_balanced(groups=games,names=names,rng=rng,n=batch_size)


def _draw_balanced(*, groups, names, rng, n):
    if not names:
        raise ValueError('GAMER_INTERFERENCE_EMPTY_SOURCE')
    picked = []
    for _ in range(n):
        game = names[int(rng.integers(len(names)))]
        examples = groups[game]
        picked.append(examples[int(rng.integers(len(examples)))])
    return np.asarray(picked, dtype=np.int64)


def target_masks(rows, radius):
    masks = defaultdict(list)
    for i, row in enumerate(rows):
        masks['all'].append(i)
        masks['game:'+str(row['game_id'])].append(i)
        masks['signature:'+table_signature(row)].append(i)
        masks['zone:'+target_zone(row['answer'],radius)].append(i)
        for family in set(row.get('table_families') or ()):
            masks['family:'+family].append(i)
    return {key: np.asarray(values, dtype=np.int64) for key, values in sorted(masks.items())}


def _one_result(baseline, after, truth, indexes):
    before = np.abs(baseline[indexes]-truth[indexes])
    now = np.abs(after[indexes]-truth[indexes])
    # For each target cohort, this is an ACTUAL change in held-out prediction.
    nonzero=truth[indexes]!=0.0
    sign_before=np.sign(baseline[indexes][nonzero])==np.sign(truth[indexes][nonzero])
    sign_after=np.sign(after[indexes][nonzero])==np.sign(truth[indexes][nonzero])
    return {'cases': int(len(indexes)),
            'delta_mae': float(np.mean(now-before)),
            'newly_damaged': int(np.sum((before <= 0.1) & (now > 0.1))),
            'newly_repaired': int(np.sum((before > 0.1) & (now <= 0.1))),
            'signs_lost':int(np.sum(sign_before & ~sign_after)),
            'signs_recovered':int(np.sum(~sign_before & sign_after)),
            'delta_within_0_1': float(np.mean(now <= 0.1)-np.mean(before <= 0.1))}


def _gradient_flat(torch, head):
    return torch.cat([p.grad.detach().reshape(-1).cpu().clone() if p.grad is not None
                      else torch.zeros(p.numel(), dtype=torch.float32)
                      for p in head.parameters()])


def causal_probe(torch, rows_train, rows_dev, x_train, x_dev, *, weights,
                 optimizer_state, hyperparameters, radius=1.0, repeats=2,
                 hidden=None, seed=1729, progress=None):
    """All operations occur on disposable CPU model/optimizer instances.

    Caller supplies exact cached features and a verified optimizer checkpoint.
    repeat=2 is a screening replicate, not statistical certainty. Results
    report directions and agreement rather than declaring a cause from noise.
    """
    from tools.nanojev_qwen_c0_compressive_train import (
        VARIANTS, make_network, signed_log, eval_heads, neural_loss)
    if repeats < 2:
        raise ValueError('GAMER_INTERFERENCE_REQUIRES_TWO_REPEATS')
    hp=hyperparameters
    batch = int(hp.get('head_batch') or 32)
    if batch < 1: raise ValueError('GAMER_INTERFERENCE_INVALID_BATCH_SIZE')
    x_train=np.asarray(x_train,dtype=np.float32)
    x_dev=np.asarray(x_dev,dtype=np.float32)
    y_train=np.asarray([float(r['answer']) for r in rows_train],dtype=np.float64)
    y_dev=np.asarray([float(r['answer']) for r in rows_dev],dtype=np.float64)
    if (not len(x_train) or not len(x_dev) or x_train.ndim!=2 or x_dev.ndim!=2 or
        x_train.shape[0]!=len(rows_train) or x_dev.shape[0]!=len(rows_dev) or
        x_train.shape[1]!=x_dev.shape[1] or
        not np.isfinite(x_train).all() or not np.isfinite(x_dev).all()):
        raise ValueError('GAMER_INTERFERENCE_INVALID_FEATURES')
    hidden = VARIANTS['wide'] if hidden is None else hidden
    head=make_network(torch,x_train.shape[1],hidden)
    opt=torch.optim.AdamW(head.parameters(),lr=float(hp['head_lr']),
                          weight_decay=float(hp['weight_decay']))
    # A single model is restored before EVERY update, including mixed controls.
    head.load_state_dict(weights, strict=True)
    opt.load_state_dict(optimizer_state)
    baseline,_=eval_heads(torch,head,x_dev,y_dev,0.,1.,'cpu',int(hp.get('eval_batch') or 32))
    groups=source_groups(rows_train,radius)
    cohorts=target_masks(rows_dev,radius)
    traces=defaultdict(list)
    update_batches=defaultdict(list)
    gradient_vectors={}
    for source, indices in groups.items():
        for rep in range(repeats):
            head.load_state_dict(weights,strict=True)
            opt.load_state_dict(optimizer_state)
            rng=np.random.default_rng(seed + 1000003*rep + 13*int.from_bytes(
                hashlib.sha256(source.encode()).digest()[:4],'big'))
            selection=draw_game_balanced(rows_train,indices,rng,batch)
            x=torch.as_tensor(x_train[selection].copy(),dtype=torch.float32)
            y=torch.as_tensor(signed_log(y_train[selection]).astype('float32'))
            head.train()
            a,b=head(x)
            loss=neural_loss(torch,a,b,y,beta=float(hp['huber_beta']),
                             aux_weight=float(hp['aux_weight']))
            opt.zero_grad(set_to_none=True)
            loss.backward()
            if rep == 0 and (source.startswith('family:') or source.startswith('zone:')):
                gradient_vectors[source]=_gradient_flat(torch,head)
            gradient_norm=float(torch.nn.utils.clip_grad_norm_(
                head.parameters(),float(hp['grad_clip'])))
            opt.step()
            update_batches[source].append({'repeat':rep,
                'train_indices':[int(i) for i in selection],
                'pre_update_loss':float(loss.detach()),
                'unclipped_gradient_norm':gradient_norm})
            preds,_=eval_heads(torch,head,x_dev,y_dev,0.,1.,'cpu',int(hp.get('eval_batch') or 32))
            traces[source].append({name:_one_result(baseline,preds,y_dev,idx)
                                   for name,idx in cohorts.items()})
        if progress:
            progress(f'GAMER INTERFERENCE: evaluated {len(traces)}/{len(groups)} '
                     f'sources ({source}); repeats={repeats}')
    effects=[]
    for source, trials in traces.items():
        for target in cohorts:
            measurements=[r[target] for r in trials]
            delta=np.asarray([m['delta_mae'] for m in measurements])
            effects.append({'source':source,'target':target,
                            'cases':measurements[0]['cases'],
                            'delta_mae':float(delta.mean()),
                            'delta_mae_by_repeat':[float(x) for x in delta],
                            'damaging_repeats':int(np.sum(delta>1e-6)),
                            'improving_repeats':int(np.sum(delta< -1e-6)),
                            'mean_newly_damaged':float(np.mean([m['newly_damaged'] for m in measurements])),
                            'mean_newly_repaired':float(np.mean([m['newly_repaired'] for m in measurements])),
                            'mean_signs_lost':float(np.mean([m['signs_lost'] for m in measurements])),
                            'mean_signs_recovered':float(np.mean([m['signs_recovered'] for m in measurements])),
                            'delta_within_0_1':float(np.mean([m['delta_within_0_1'] for m in measurements]))})
    gradients=[]
    for i, left in enumerate(sorted(gradient_vectors)):
        a=gradient_vectors[left]
        for right in sorted(gradient_vectors)[i+1:]:
            b=gradient_vectors[right]
            denominator=float(torch.linalg.vector_norm(a)*torch.linalg.vector_norm(b))
            cos=float(torch.dot(a,b))/denominator if denominator>0 else None
            gradients.append({'source_a':left,'source_b':right,'cosine':cos,
                              'opposing':cos is not None and cos<0})
    effects.sort(key=lambda e:(e['source'],e['target']))
    return {'baseline_dev_mae':float(np.mean(np.abs(baseline-y_dev))),
            'baseline_dev_within_0_1':float(np.mean(np.abs(baseline-y_dev)<=.1)),
            'source_count':len(groups),'source_counts':{s:len(ix) for s,ix in groups.items()},
            'target_counts':{t:len(ix) for t,ix in cohorts.items()},
            'batch_size':batch,'repeats':repeats,'updates_per_trial':1,
            'effects':effects,'gradient_cosines':gradients,
            'source_batches':dict(sorted(update_batches.items())),
            'source_sampling_seed':seed}


def _load_features(run,cycle,manifest,state,rows,split):
    # Use the same identity format that the numerical trainer writes; do not
    # infer arrays from names or evaluate activations from another LoRA cycle.
    from tools.nanojev_gamer_v1_handoff import canonical,original_prompt,sha_bytes
    import numpy as np
    from tools.nanojev_gamer_v1_diagnostic import _open_checkpoint_shared
    path=cycle/'to_numerical'/'feature_cache'/f'{split}.npz'
    with _open_checkpoint_shared(path) as stream:
        with np.load(stream,allow_pickle=False) as data:
            identity=json.loads(str(data['identity'].item()))
            x=data['features'].copy()
    expected={'dataset_sha256':manifest['dataset_sha256'],
              'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
              'adapter_sha256':state['adapter_sha256'],
              'model':state['hyperparameters']['model'],
              'max_length':state['hyperparameters']['max_length'],
              'prompts_sha256':sha_bytes(canonical([original_prompt(r) for r in rows]).encode('utf-8'))}
    if identity!=expected or x.ndim!=2 or len(x)!=len(rows) or not np.isfinite(x).all():
        raise ValueError('GAMER_INTERFERENCE_FEATURE_LINEAGE_MISMATCH: '+split)
    return x


# A separate, bounded diagnostic: unlike the full 179-source matrix, this
# explicitly compares optimizer mechanisms on IDENTICAL batches/gradients.
OPTIMIZER_COMPARISON_SOURCES = (
    'zone:zero', 'zone:small_negative', 'zone:small_positive',
    'zone:outer_negative', 'zone:outer_positive', 'mixed:all',
)
OPTIMIZER_COMPARISON_MODES = (
    'saved_adamw', 'fresh_adamw', 'plain_gradient_descent',
)
OPTIMIZER_COMPARISON_SCHEMA = 'nanojev.gamer.v1.optimizer-interference-comparison.v1'


def _interference_inputs(run, *, dataset=None, output=None, radius=None):
    """Canonical source of checkpoint, corpus, and feature-lineage validation."""
    from tools.nanojev_gamer_v1_diagnostic import _read_json, _sha, _open_checkpoint_shared
    import torch
    root=Path(run).resolve()
    manifest=_read_json(root/'handoff_manifest.json')
    dataset_path=Path(dataset) if dataset else root/'certified_pairs.jsonl'
    if _sha(dataset_path)!=manifest['dataset_sha256']:
        raise ValueError('GAMER_INTERFERENCE_DATASET_SHA_MISMATCH')
    rows=[json.loads(x) for x in dataset_path.read_text(encoding='utf-8').splitlines() if x.strip()]
    cycles=[p for p in sorted((root/'staircase').glob('head_cycle_*'))
            if p.is_dir() and (p/'staircase_cycle.json').is_file()]
    if not cycles: raise FileNotFoundError('GAMER_INTERFERENCE_NO_STAIRCASE_CYCLES')
    cycle=cycles[-1]
    with _open_checkpoint_shared(cycle/'to_numerical'/'latest.pt') as stream:
        state=torch.load(stream,map_location='cpu',weights_only=True)
    frozen=_read_json(cycle/'lora'/'report.json')
    if (state.get('phase')!='to_numerical' or
        state.get('schema')!='nanojev.gamer.v1.original-prompt-shared-handoff.v1' or
        state.get('dataset_sha256')!=manifest['dataset_sha256'] or
        state.get('original_prompt_corpus_sha256')!=manifest['original_prompt_corpus_sha256'] or
        state.get('adapter_sha256')!=frozen.get('best_adapter_sha256')):
        raise ValueError('GAMER_INTERFERENCE_CHECKPOINT_LINEAGE_MISMATCH')
    train=[row for row in rows if row['split']=='train']
    dev=[row for row in rows if row['split']=='dev']
    if not train or not dev: raise ValueError('GAMER_INTERFERENCE_MISSING_TRAIN_OR_DEV')
    xtrain=_load_features(root,cycle,manifest,state,train,'train')
    xdev=_load_features(root,cycle,manifest,state,dev,'dev')
    if radius is None:
        radius=float((state.get('sampling_policy') or {}).get('radius',1.0))
    if not math.isfinite(radius) or radius<=0:
        raise ValueError('GAMER_INTERFERENCE_INVALID_RADIUS')
    outdir=Path(output).resolve() if output else root.parent/(root.name+'-interference')
    if outdir==root or root in outdir.parents:
        raise ValueError('GAMER_INTERFERENCE_OUTPUT_MUST_BE_OUTSIDE_ACTIVE_RUN')
    return root,cycle,manifest,state,train,dev,xtrain,xdev,float(radius),outdir


def _atomic_json(path, payload):
    path=Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.interference-',
                                     suffix='.tmp',delete=False,mode='w',encoding='utf-8') as f:
        tmp=Path(f.name)
        json.dump(payload,f,indent=2,sort_keys=True,allow_nan=False)
    try: os.replace(tmp,path)
    finally:
        if tmp.exists(): tmp.unlink()


def _atomic_csv(path, fields, records):
    path=Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.interference-',
                                     suffix='.tmp',delete=False,mode='w',
                                     encoding='utf-8',newline='') as f:
        tmp=Path(f.name)
        writer=csv.DictWriter(f,fieldnames=fields,lineterminator='\n')
        writer.writeheader()
        for record in records:
            writer.writerow({name:record[name] for name in fields})
    try: os.replace(tmp,path)
    finally:
        if tmp.exists(): tmp.unlink()


def optimizer_comparison_probe(torch, rows_train, rows_dev, x_train, x_dev, *,
                               weights, optimizer_state, hyperparameters,
                               radius=1.0, repeats=2, hidden=None, seed=1729,
                               progress=None):
    """Isolate AdamW history vs update direction with matched parameter-step norm.

    The exact SAME supervised gradient and minibatch are used in all three
    modes per trial. The actual saved AdamW parameter displacement defines the
    norm budget; fresh AdamW and gradient descent are scaled to that budget.
    All models and optimizer states are disposable. No checkpoint is written.
    """
    from tools.nanojev_qwen_c0_compressive_train import (
        VARIANTS, make_network, signed_log, eval_heads, neural_loss)
    if repeats < 2:
        raise ValueError('GAMER_INTERFERENCE_REQUIRES_TWO_REPEATS')
    if not optimizer_state.get('state'):
        raise ValueError('GAMER_INTERFERENCE_OPTIMIZER_HISTORY_UNAVAILABLE')
    hp=hyperparameters
    batch=int(hp.get('head_batch') or 32)
    if batch<1: raise ValueError('GAMER_INTERFERENCE_INVALID_BATCH_SIZE')
    x_train=np.asarray(x_train,dtype=np.float32)
    x_dev=np.asarray(x_dev,dtype=np.float32)
    y_train=np.asarray([float(r['answer']) for r in rows_train],dtype=np.float64)
    y_dev=np.asarray([float(r['answer']) for r in rows_dev],dtype=np.float64)
    if (x_train.ndim!=2 or x_dev.ndim!=2 or not len(x_train) or not len(x_dev) or
        x_train.shape[0]!=len(rows_train) or x_dev.shape[0]!=len(rows_dev) or
        x_train.shape[1]!=x_dev.shape[1] or
        not np.isfinite(x_train).all() or not np.isfinite(x_dev).all() or
        not np.isfinite(y_train).all() or not np.isfinite(y_dev).all()):
        raise ValueError('GAMER_INTERFERENCE_INVALID_FEATURES')
    hidden=VARIANTS['wide'] if hidden is None else hidden
    reference=make_network(torch,x_train.shape[1],hidden)
    reference.load_state_dict(weights,strict=True)
    baseline,_=eval_heads(torch,reference,x_dev,y_dev,0.,1.,'cpu',int(hp.get('eval_batch') or 32))
    groups=source_groups(rows_train,radius)
    cohorts=target_masks(rows_dev,radius)
    comparisons=[]
    records=[]
    skipped=[]
    optimizer_steps=[]
    for entry in optimizer_state['state'].values():
        if 'step' in entry:
            v=entry['step']
            optimizer_steps.append(int(v.item() if hasattr(v,'item') else v))
    for source in OPTIMIZER_COMPARISON_SOURCES:
        indices=groups.get(source)
        if not indices:
            skipped.append(source)
            continue
        for rep in range(repeats):
            rng=np.random.default_rng(seed + 1000003*rep + 13*int.from_bytes(
                hashlib.sha256(source.encode()).digest()[:4],'big'))
            selection=draw_game_balanced(rows_train,indices,rng,batch)
            x=torch.as_tensor(x_train[selection].copy(),dtype=torch.float32)
            y=torch.as_tensor(signed_log(y_train[selection]).astype('float32'))
            # One backward pass, not three approximations of the same gradient.
            reference.load_state_dict(weights,strict=True)
            reference.train()
            corrected,uncorrected=reference(x)
            loss=neural_loss(torch,corrected,uncorrected,y,
                             beta=float(hp['huber_beta']),aux_weight=float(hp['aux_weight']))
            reference.zero_grad(set_to_none=True)
            loss.backward()
            unclip=float(torch.nn.utils.clip_grad_norm_(
                reference.parameters(),float(hp['grad_clip'])))
            gradients=[p.grad.detach().clone() if p.grad is not None
                       else torch.zeros_like(p) for p in reference.parameters()]
            if not all(torch.isfinite(g).all().item() for g in gradients):
                raise ValueError('GAMER_INTERFERENCE_NONFINITE_GRADIENT')
            negative_gradient=torch.cat([(-g).reshape(-1) for g in gradients])
            neg_grad_norm=float(torch.linalg.vector_norm(negative_gradient))
            trial={}
            # Identical starting parameters and gradients in all modes.
            for mode in OPTIMIZER_COMPARISON_MODES:
                head=make_network(torch,x_train.shape[1],hidden)
                head.load_state_dict(weights,strict=True)
                params=list(head.parameters())
                start=[p.detach().clone() for p in params]
                if mode=='plain_gradient_descent':
                    opt=torch.optim.SGD(params,lr=1.0,momentum=0.,weight_decay=0.)
                else:
                    opt=torch.optim.AdamW(params,lr=float(hp['head_lr']),
                                          weight_decay=float(hp['weight_decay']))
                    opt.load_state_dict(copy.deepcopy(optimizer_state))
                    if mode=='fresh_adamw':
                        opt.state.clear()  # Same AdamW settings, NO historical moments.
                for p,g in zip(params,gradients):
                    p.grad=g.clone()
                opt.step()
                delta=[p.detach()-initial for p,initial in zip(params,start)]
                flat=torch.cat([d.reshape(-1) for d in delta])
                raw_norm=float(torch.linalg.vector_norm(flat))
                if not math.isfinite(raw_norm):
                    raise ValueError('GAMER_INTERFERENCE_NONFINITE_PARAMETER_UPDATE')
                trial[mode]={'head':head,'start':start,'delta':delta,
                             'raw_norm':raw_norm,'flat':flat}
            reference_norm=trial['saved_adamw']['raw_norm']
            if reference_norm<=0:
                raise ValueError('GAMER_INTERFERENCE_ZERO_SAVED_ADAMW_UPDATE')
            saved_flat=trial['saved_adamw']['flat']
            for mode in OPTIMIZER_COMPARISON_MODES:
                info=trial[mode]
                raw_norm=info['raw_norm']
                scale=reference_norm/raw_norm if raw_norm>0 else 0.
                with torch.no_grad():
                    for p,initial,delta in zip(info['head'].parameters(),
                                               info['start'],info['delta']):
                        p.copy_(initial+delta*scale)
                effective_norm=float(torch.linalg.vector_norm(info['flat']*scale))
                # If plain gradient was exactly zero, a history-only AdamW
                # update cannot be norm matched. This is explicit evidence.
                match_possible=raw_norm>0
                cos=(float(torch.dot(info['flat'],saved_flat)) /
                     (raw_norm*reference_norm)) if raw_norm>0 else None
                grad_cos=(float(torch.dot(info['flat'],negative_gradient)) /
                          (raw_norm*neg_grad_norm)) if raw_norm>0 and neg_grad_norm>0 else None
                pred,_=eval_heads(torch,info['head'],x_dev,y_dev,0.,1.,'cpu',
                                  int(hp.get('eval_batch') or 32))
                measures={name:_one_result(baseline,pred,y_dev,ix)
                          for name,ix in cohorts.items()}
                comparisons.append({'source':source,'repeat':rep,'mode':mode,
                    'train_indices':[int(i) for i in selection],
                    'training_loss_before_update':float(loss.detach()),
                    'unclipped_gradient_norm':unclip,
                    'negative_gradient_norm_after_clip':neg_grad_norm,
                    'raw_update_norm':raw_norm,'matched_update_norm':effective_norm,
                    'step_scale':float(scale),'norm_matched':match_possible,
                    'update_cosine_to_saved_adamw':cos,
                    'update_cosine_to_negative_gradient':grad_cos,
                    'outcomes':measures})
                for index,row in enumerate(rows_dev):
                    records.append({'source':source,'repeat':rep,'mode':mode,
                        'case_index':index,'game_id':str(row['game_id']),
                        'target_zone':target_zone(row['answer'],radius),
                        'answer':float(y_dev[index]),
                        'before':float(baseline[index]),
                        'after':float(pred[index]),
                        'prediction_delta':float(pred[index]-baseline[index]),
                        'absolute_error_delta':float(abs(pred[index]-y_dev[index])-
                                                     abs(baseline[index]-y_dev[index]))})
        if progress:
            progress(f'GAMER INTERFERENCE OPTIMIZER: compared '
                     f'{source} ({repeats} identical-batch trials, 3 update methods)')
    # Aggregate only paired source+target comparisons; do not claim the
    # six source cohorts are independent (they overlap by construction).
    summary=[]
    for source in OPTIMIZER_COMPARISON_SOURCES:
        if source in skipped: continue
        for mode in OPTIMIZER_COMPARISON_MODES:
            sub=[item for item in comparisons if item['source']==source and item['mode']==mode]
            for target in ('all','zone:zero','zone:small_negative','zone:small_positive',
                           'zone:outer_negative','zone:outer_positive'):
                if target not in cohorts: continue
                values=[item['outcomes'][target]['delta_mae'] for item in sub]
                summary.append({'source':source,'mode':mode,'target':target,
                                'cases':len(cohorts[target]),
                                'mean_delta_mae':float(np.mean(values)),
                                'delta_mae_by_repeat':[float(v) for v in values],
                                'damaging_trials':sum(v>0.01 for v in values),
                                'improving_trials':sum(v< -0.01 for v in values)})
    return {'baseline_dev_mae':float(np.mean(np.abs(baseline-y_dev))),
            'baseline_dev_within_0_1':float(np.mean(np.abs(baseline-y_dev)<=.1)),
            'repeats':repeats,'batch_size':batch,'updates_per_trial':1,
            'optimizer_history_parameter_count':len(optimizer_state['state']),
            'optimizer_history_steps_min':min(optimizer_steps) if optimizer_steps else None,
            'optimizer_history_steps_max':max(optimizer_steps) if optimizer_steps else None,
            'modes':list(OPTIMIZER_COMPARISON_MODES),
            'source_groups':[s for s in OPTIMIZER_COMPARISON_SOURCES if s not in skipped],
            'skipped_source_groups':skipped,'trials':comparisons,'summary':summary,
            'predictions':records,'source_sampling_seed':seed}


def run_optimizer_comparison(run, *, dataset=None, output=None, repeats=2,
                             radius=None, progress=None):
    """Opt-in, bounded three-way optimizer experiment; active run untouched."""
    import torch
    (root,cycle,manifest,state,train,dev,xtrain,xdev,radius,outdir)=_interference_inputs(
        run,dataset=dataset,output=output,radius=radius)
    torch.set_num_threads(min(torch.get_num_threads(),4))
    evidence=optimizer_comparison_probe(
        torch,train,dev,xtrain,xdev,weights=state['model'],
        optimizer_state=state['optimizer'],hyperparameters=state['hyperparameters'],
        radius=radius,repeats=repeats,progress=progress)
    predictions=evidence.pop('predictions')
    payload={'schema':OPTIMIZER_COMPARISON_SCHEMA,
             'purpose':'OPT_IN_MATCHED_NORM_OPTIMIZER_HISTORY_CAUSAL_COMPARISON',
             'read_only_active_run':True,'device':'cpu','run':str(root),
             'cycle':cycle.name,'head_step':int(state['step']),
             'checkpoint_kind':'latest','dataset_sha256':manifest['dataset_sha256'],
             'adapter_sha256':state['adapter_sha256'],'radius':radius,
             'training_examples':len(train),'development_examples':len(dev),
             'interpretation_limitations':[
                 'Only one controlled optimizer update per trial is tested; this does not establish long-run training convergence.',
                 'The same exact gradient and starting weights are used in all modes; source cohorts overlap.',
                 'The saved AdamW displacement sets the norm for every method; this isolates update direction, not raw update size.',
                 'If the current source gradient is zero, a history-only AdamW update cannot be norm matched and is explicitly reported.',
                 'These results isolate optimizer effects at this checkpoint, not which historical batches caused forgetting.',
             ],
             'experiment':evidence}
    outdir.mkdir(parents=True,exist_ok=True)
    stem=f'{cycle.name}_step_{int(state["step"]):07d}_optimizer_comparison'
    json_path=outdir/(stem+'.json')
    csv_path=outdir/(stem+'_predictions.csv')
    _atomic_json(json_path,payload)
    fields=('source','repeat','mode','case_index','game_id','target_zone','answer',
            'before','after','prediction_delta','absolute_error_delta')
    _atomic_csv(csv_path,fields,predictions)
    return {'schema':OPTIMIZER_COMPARISON_SCHEMA,'report':str(json_path),
            'predictions_csv':str(csv_path),'cycle':cycle.name,
            'head_step':int(state['step']),'repeats':repeats,
            'sources':evidence['source_groups'],
            'skipped_sources':evidence['skipped_source_groups'],
            'baseline_dev_mae':evidence['baseline_dev_mae'],
            'optimizer_history_steps_min':evidence['optimizer_history_steps_min'],
            'optimizer_history_steps_max':evidence['optimizer_history_steps_max'],
            'summary':evidence['summary'],
            'limitations':payload['interpretation_limitations']}


def run_interference(run, *, dataset=None, output=None, repeats=2, radius=None,
                     progress=None):
    import torch
    (root,cycle,manifest,state,train,dev,xtrain,xdev,radius,outdir)=_interference_inputs(
        run,dataset=dataset,output=output,radius=radius)
    # CPU-only; no mutations to the checkpoint, cached features or live GPU.
    torch.set_num_threads(min(torch.get_num_threads(),4))
    evidence=causal_probe(torch,train,dev,xtrain,xdev,weights=state['model'],
                          optimizer_state=state['optimizer'],
                          hyperparameters=state['hyperparameters'],
                          radius=radius,repeats=repeats,progress=progress)
    effects=evidence['effects']
    # Ranked game-to-game damage requires ALL independent replicate updates
    # to increase error by >0.01 target units on the same held-out game.
    # This is a diagnostic screening threshold, NOT statistical significance.
    reproduced=sorted((v for v in effects if v['source'].startswith('game:') and
                      v['target'].startswith('game:') and all(x>0.01 for x in v['delta_mae_by_repeat'])),
                      key=lambda v:-v['delta_mae'])
    table_matrix=[v for v in effects if v['source'].startswith('family:') and
                  v['target'].startswith('family:')]
    zone_matrix=[v for v in effects if v['source'].startswith('zone:') and
                 v['target'].startswith('zone:')]
    payload={'schema':SCHEMA,'purpose':'OPT_IN_LOCAL_CAUSAL_INTERFERENCE_SCREEN',
             'read_only_active_run':True,'device':'cpu','run':str(root),
             'cycle':cycle.name,'head_step':int(state['step']),
             'checkpoint_kind':'latest','dataset_sha256':manifest['dataset_sha256'],
             'adapter_sha256':state['adapter_sha256'],
             'radius':radius,'training_examples':len(train),'development_examples':len(dev),
             'tables_overlap':True,
             'interpretation_limitations':[
                 'A controlled update proves a local parameter/prediction effect, not which historical minibatch caused forgetting.',
                 'Overlapping table memberships are not independent categories; family comparisons are descriptive unless matched by numerical regime.',
                 'Reproduced damage means each replicate increased the target game MAE by more than 0.01; this threshold is a screen, not statistical certainty.',
                 'Development cohorts can be small; compare cases and raw deltas before attributing a cause.',
             ],
             'reproduced_game_damage_top_30':reproduced[:30],
             'table_family_matrix':table_matrix,'numerical_zone_matrix':zone_matrix,
             'experiment':evidence}
    outdir.mkdir(parents=True,exist_ok=True)
    path=outdir/f'{cycle.name}_step_{int(state["step"]):07d}_interference.json'
    with tempfile.NamedTemporaryFile(dir=outdir,prefix='.interference-',suffix='.tmp',delete=False,mode='w',encoding='utf-8') as f:
        tmp=Path(f.name)
        json.dump(payload,f,indent=2,sort_keys=True,allow_nan=False)
    try:os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()
    # Flat matrix is directly inspectable without parsing a nested JSON file.
    csv_path=outdir/f'{cycle.name}_step_{int(state["step"]):07d}_interference_matrix.csv'
    columns=('source','target','cases','delta_mae','damaging_repeats',
             'improving_repeats','mean_newly_damaged','mean_newly_repaired',
             'mean_signs_lost','mean_signs_recovered','delta_within_0_1',
             'delta_mae_by_repeat')
    with tempfile.NamedTemporaryFile(dir=outdir,prefix='.interference-',suffix='.tmp',
                                     delete=False,mode='w',encoding='utf-8',newline='') as f:
        tmp_csv=Path(f.name)
        writer=csv.DictWriter(f,fieldnames=columns,lineterminator='\n')
        writer.writeheader()
        for entry in effects:
            writer.writerow({key:(json.dumps(entry[key]) if key=='delta_mae_by_repeat'
                                  else entry[key]) for key in columns})
    try:os.replace(tmp_csv,csv_path)
    finally:
        if tmp_csv.exists():tmp_csv.unlink()
    return {'schema':SCHEMA,'report':str(path),'matrix_csv':str(csv_path),'cycle':cycle.name,
            'head_step':int(state['step']),'source_groups':evidence['source_count'],
            'repeats':repeats,'baseline_dev_mae':evidence['baseline_dev_mae'],
            'reproduced_game_damage_pairs':len(reproduced),
            'top_game_damage':reproduced[:10],
            'family_matrix':table_matrix,'zone_matrix':zone_matrix,
            'limitations':payload['interpretation_limitations']}
