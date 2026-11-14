#!/usr/bin/env python3
"""Frozen-Qwen glass refinement and error-mode audit, without modifying v1 files.

Consumes baseline.pt and glass.pt made by nanojev_qwen_lm_deformation_smoke.
Cache the frozen Qwen's already-computed hidden states ONCE (only matched
activation neighborhoods); train correction vectors for additional epochs.
Audit exact teacher-forced transitions and actual greedy answers separately.
All outputs live in output/glass_refinement; the original artifacts are readonly.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import hashlib
import json
import random
import time
from pathlib import Path

from tools import nanojev_qwen_lm_deformation_legacy as base

SCHEMA = 'nanojev.qwen.relative-deformation-refinement.v1'


def _json(path, payload):
    base.ladder._atomic_json(path, payload)


def _store(path, payload):
    base.ladder._atomic_torch(path, payload)


def _lineage(args, rows, old_meta, baseline_path, glass_path):
    return {'schema': SCHEMA, 'corpus_hash': base._cases_hash(rows),
            'original_meta': old_meta,
            'baseline_sha256': base._file_hash(baseline_path),
            'glass_sha256': base._file_hash(glass_path)}


def _unpack(args):
    import torch
    old_dir = Path(args.output)
    old_report_file = old_dir / 'report.json'
    baseline_path = old_dir / 'baseline.pt'
    glass_path = old_dir / 'glass.pt'
    snapshot_path = old_dir / 'source_snapshot.pt'
    for path in (old_report_file, baseline_path, glass_path, snapshot_path):
        if not path.is_file():
            raise FileNotFoundError('GLASS_REFINEMENT_REQUIRES_COMPLETED_TWO_PASS_RUN:' + str(path))
    old_report = json.loads(old_report_file.read_text(encoding='utf8'))
    baseline = torch.load(baseline_path, map_location='cpu', weights_only=True)
    saved_glass = torch.load(glass_path, map_location='cpu', weights_only=True)
    if old_report['meta'] != baseline['meta'] or baseline['meta'] != saved_glass['meta']:
        raise RuntimeError('GLASS_REFINEMENT_CHECKPOINT_META_MISMATCH')
    # Source snapshot stays immutable, independently of the concurrently running ladder.
    rows, _ = base.corpus_for_schedule(args.radii,args.max_rung,args.operand_limit,
                                       args.per_cell,args.seed,args.t00_mode)
    if args.case_cap:
        grouped = base._groups(rows)
        rows = [row for k in sorted(grouped) for row in grouped[k][:args.case_cap]]
    meta = base._glossary_for_checkpoint(args, rows, baseline['meta']['scope'],
                                         args.ladder_working, snapshot_path)
    if meta != baseline['meta'] or old_report['corpus_cases'] != len(rows):
        raise RuntimeError('GLASS_REFINEMENT_CORPUS_DRIFT')
    bb,tok,sources,frozen,dirs,zero=base.ladder._fresh_parent(args)
    base.ladder._outputs_safe(old_dir, [Path(args.ladder_working).parent, *dirs])
    parent = base._load_source(bb,zero,snapshot_path,sources,frozen)
    if baseline['source_sha256'] != sources or baseline['frozen_sha256'] != frozen:
        raise RuntimeError('GLASS_REFINEMENT_QWEN_LINEAGE_DRIFT')
    zero.restore_lora(bb,baseline['lora'])
    bb.eval();bb.requires_grad_(False)
    qwen_sha=base._lora_digest(bb,zero)
    if saved_glass['backbone_lora_sha256'] != qwen_sha:
        raise RuntimeError('GLASS_REFINEMENT_FROZEN_QWEN_DRIFT')
    head=bb.get_output_embeddings()
    if head is None or getattr(head,'weight',None) is None:
        raise RuntimeError('GLASS_REFINEMENT_HEAD_UNAVAILABLE')
    hidden=int(head.weight.shape[1]);keys=saved_glass['keys']
    old_glass=base.RelativeDeformationGlass(None, hidden=hidden,keys=keys,
                 projection_dim=saved_glass['projection_dim'],
                 threshold=saved_glass['threshold'],seed=args.seed)
    old_glass.net.to(args.device).load_state_dict(saved_glass['glass'])
    old_glass.net.eval()
    net=old_glass.net
    lineage=_lineage(args,rows,baseline['meta'],baseline_path,glass_path)
    work=old_dir/'glass_refinement'
    work.mkdir(parents=True,exist_ok=True)
    return bb,tok,head,zero,rows,old_glass,lineage,work,qwen_sha,old_report


def _match(net,hidden):
    """The very same nearest-mode gate as the model's inference path."""
    import torch
    import torch.nn.functional as F
    flat=hidden.detach().float().reshape(-1,hidden.shape[-1])
    if not len(net.keys):
        return torch.full((len(flat),),-1,dtype=torch.long,device=flat.device)
    with torch.no_grad():
        projected=F.normalize(flat @ net.projection,dim=-1)
        strength,index=(projected @ net.keys.T).max(-1)
        return torch.where(strength >= net.threshold-1e-6,index,torch.full_like(index,-1))


def cache_activations(bb,tok,head,rows,glass,args,work,lineage):
    """Single full-corpus frozen forward pass; store all matched errors and
    reservoir-sampled matched correct next-tokens for protection training.
    No original problem strings or oracle output are stored as mode keys.
    """
    import torch
    path=work/'activation_cache.pt'
    policy={'correct_per_mode':args.protect_per_mode,'protect_ratio':args.protect_ratio,'seed':args.seed,
            'batch_size':args.batch_size,'max_seq':args.max_seq}
    if path.exists():
        cache=torch.load(path,map_location='cpu',weights_only=True)
        if cache['lineage']!=lineage or cache['policy']!=policy:
            raise RuntimeError('GLASS_REFINEMENT_CACHE_POLICY_DRIFT')
        return cache
    net=glass.net
    correct=collections.defaultdict(list)
    wrong=[];summary=collections.Counter();seen_correct=collections.Counter()
    rng=random.Random(args.seed+299)
    captured=[]
    def capture(_module,inputs):captured.append(inputs[0].detach())
    with head.register_forward_pre_hook(capture):
        for start in range(0,len(rows),args.eval_batch):
            batch=rows[start:start+args.eval_batch]
            x,y,mask=base._token_batch(tok,batch,args.device,args.max_seq)
            with torch.no_grad():
                logits=bb(input_ids=x,attention_mask=mask,return_dict=True,use_cache=False).logits[:,:-1]
                gold=y[:,1:];valid=gold!=-100
                actual=logits.argmax(-1)
                hidden=captured[-1][:,:-1,:]
                index=_match(net,hidden).reshape(gold.shape)
                for i,row in enumerate(batch):
                    for pos in torch.nonzero(valid[i],as_tuple=False).flatten().tolist():
                        is_wrong=bool(actual[i,pos]!=gold[i,pos])
                        mode=int(index[i,pos])
                        summary['total_valid_tokens']+=1
                        if is_wrong:summary['baseline_wrong_tokens']+=1
                        if mode<0:
                            if is_wrong:summary['unmatched_wrong_tokens']+=1
                            continue
                        summary['matched_tokens']+=1
                        if is_wrong:summary['matched_wrong_tokens']+=1
                        mode_key=f'mode_{mode:03d}'
                        entry={'h':hidden[i,pos].detach().cpu().float(),
                               'gold':int(gold[i,pos]),'mode':mode,
                               'rung':int(row['rung']),'delta':int(row['residual']),
                               'id':row['id'],'position':int(pos)}
                        if is_wrong:
                            wrong.append(entry)
                        else:
                            summary['matched_correct_tokens']+=1
                            seen_correct[mode]+=1
                            slot=len(correct[mode])
                            if slot>=args.protect_per_mode:
                                slot=rng.randrange(seen_correct[mode])
                            if slot<args.protect_per_mode:
                                if slot==len(correct[mode]):correct[mode].append(entry)
                                else:correct[mode][slot]=entry
            captured.clear()
            if args.progress_every and (start//args.eval_batch+1)%args.progress_every==0:
                print('GLASS_REFINE_CACHE_PROGRESS='+json.dumps({'cases':start+len(batch),
                       'total':len(rows),'matched_errors':len(wrong)},sort_keys=True),flush=True)
    # Balance zero-preservation rehearsals against the number of actual
    # errors in each mode: 32 correct / 1 error would otherwise suppress
    # the very correction the experiment is trying to measure.
    wrong_by_mode=collections.Counter(item['mode'] for item in wrong)
    protection=[item for k in sorted(correct) if wrong_by_mode[k] > 0
                for item in correct[k][:min(len(correct[k]),
                   args.protect_per_mode, max(1,args.protect_ratio*wrong_by_mode[k]))]]
    entries=wrong+protection
    if entries:
        h=torch.stack([r['h'] for r in entries])
        labels=torch.tensor([r['gold'] for r in entries],dtype=torch.long)
        modes=torch.tensor([r['mode'] for r in entries],dtype=torch.long)
        erroneous=torch.tensor([i<len(wrong) for i in range(len(entries))],dtype=torch.bool)
    else:
        h=torch.empty(0,net.corrections.shape[1]);labels=torch.empty(0,dtype=torch.long)
        modes=torch.empty(0,dtype=torch.long);erroneous=torch.empty(0,dtype=torch.bool)
    cache={'schema':SCHEMA,'lineage':lineage,'policy':policy,'h':h,'labels':labels,
           'modes':modes,'erroneous':erroneous,
           'entry_meta':[{k:v for k,v in row.items() if k!='h' and k!='gold'} for row in entries],
           'stats':dict(summary),'by_mode_matched_correct':dict(seen_correct),
           'matched_wrong_entries':len(wrong),'protected_correct_entries':len(protection),
           'full_corpus':not bool(args.case_cap)}
    _store(path,cache)
    return cache


def train_refinement(head,glass,cache,args,work,lineage):
    """Optimize only a small existing glass correction bank, offline against
    frozen Qwen hidden features. Checkpoint optimizer and exact epoch/batch offset.
    """
    import torch
    import torch.nn.functional as F
    net=glass.net
    net.train()
    head.eval();head.requires_grad_(False)
    old_corrections=net.corrections.detach().clone()
    policy={'epochs':args.refine_epochs,'lr':args.refine_lr,'batch_size':args.refine_batch_size,
            'preserve_weight':args.preserve_weight,'l2':args.refine_l2,'seed':args.seed}
    done=work/'refined_glass.pt';partial=work/'refinement_partial.pt'
    if done.exists():
        finished=torch.load(done,map_location='cpu',weights_only=True)
        if finished['lineage']!=lineage or finished['policy']!=policy:
            raise RuntimeError('GLASS_REFINEMENT_POLICY_DRIFT_USE_NEW_OUTPUT')
        net.load_state_dict(finished['glass'])
        return finished['training']
    n=len(cache['h'])
    opt=torch.optim.AdamW([net.corrections],lr=args.refine_lr,weight_decay=0)
    start_epoch=start_offset=steps=0
    if partial.exists():
        previous=torch.load(partial,map_location='cpu',weights_only=True)
        if previous['lineage']!=lineage or previous['policy']!=policy:
            raise RuntimeError('GLASS_REFINEMENT_PARTIAL_POLICY_DRIFT')
        net.load_state_dict(previous['glass']);opt.load_state_dict(previous['optimizer'])
        start_epoch=previous['epoch'];start_offset=previous['offset'];steps=previous['steps']
    train_start=time.perf_counter()
    # Both incorrect and correct activations are part of the frozen-mode corpus.
    for epoch in range(start_epoch,args.refine_epochs):
        order=list(range(n));random.Random(args.seed+3941+epoch).shuffle(order)
        for offset in range(start_offset if epoch==start_epoch else 0,n,args.refine_batch_size):
            indices=order[offset:offset+args.refine_batch_size]
            h=cache['h'][indices].to(device=args.device,dtype=head.weight.dtype);target=cache['labels'][indices].to(args.device)
            wrong=cache['erroneous'][indices].to(args.device)
            opt.zero_grad(set_to_none=True)
            corrected=net(h)
            delta=corrected.float()-h.float()
            loss=0.0
            if bool(wrong.any()):
                logits=head(corrected[wrong]).float()
                loss=F.cross_entropy(logits,target[wrong])
            if bool((~wrong).any()):
                loss=loss+args.preserve_weight*delta[~wrong].square().sum(-1).mean()
            if args.refine_l2:
                loss=loss+args.refine_l2*delta.square().sum(-1).mean()
            if not isinstance(loss,torch.Tensor):continue
            if not bool(torch.isfinite(loss)):
                raise RuntimeError('GLASS_REFINEMENT_NONFINITE_LOSS')
            loss.backward()
            if net.corrections.grad is None:
                raise RuntimeError('GLASS_REFINEMENT_NO_GRADIENT')
            torch.nn.utils.clip_grad_norm_([net.corrections],1.)
            opt.step();steps+=1
            if args.progress_every and steps%args.progress_every==0:
                print('GLASS_REFINE_TRAIN_PROGRESS='+json.dumps({'epoch':epoch+1,
                    'steps':steps,'loss':round(float(loss.detach()),8)},sort_keys=True),flush=True)
            if steps%args.checkpoint_every==0:
                _store(partial,{'schema':SCHEMA,'lineage':lineage,'policy':policy,
                        'glass':net.state_dict(),'optimizer':opt.state_dict(),
                        'epoch':epoch,'offset':offset+args.refine_batch_size,'steps':steps})
        start_offset=0
        _store(partial,{'schema':SCHEMA,'lineage':lineage,'policy':policy,
                'glass':net.state_dict(),'optimizer':opt.state_dict(),
                'epoch':epoch+1,'offset':0,'steps':steps})
    stats={'steps':steps,'epochs':args.refine_epochs,'cache_entries':n,
           'training_seconds':round(time.perf_counter()-train_start,3),
           'original_glass_parameters':net.corrections.numel(),
           'additional_trainable_parameters':0,
           'change_l2':round(float((net.corrections.detach()-old_corrections).float().norm()),6)}
    _store(done,{'schema':SCHEMA,'lineage':lineage,'policy':policy,
                 'glass':net.state_dict(),'training':stats})
    partial.unlink(missing_ok=True)
    return stats


def teacher_forced_audit(bb,tok,head,rows,original,refined,args,work,lineage):
    """Paired token audit with ONE frozen Qwen pass per batch.
    Assign credit for fixes/regressions only to modes actually activated.
    """
    import torch
    import torch.nn.functional as F
    path=work/'teacher_forced_audit.json'
    if path.exists():
        old=json.loads(path.read_text(encoding='utf8'))
        if old['lineage']==lineage and old['refined_sha256']==base._file_hash(work/'refined_glass.pt'):
            return old
    grouped=collections.defaultdict(lambda:{k:{'nll':0.,'tokens':0,'wrong_tokens':0} for k in ('baseline','original_glass','refined_glass')})
    mode_stats=collections.defaultdict(lambda:collections.Counter())
    mode_failure_cases=collections.defaultdict(set)
    mode_fixed_cases=collections.defaultdict(set)
    cases=collections.defaultdict(lambda:collections.Counter())
    token_counts={k:collections.Counter() for k in ('baseline','original_glass','refined_glass')}
    captured=[]
    def hook(_mod,inputs):captured.append(inputs[0].detach())
    with head.register_forward_pre_hook(hook):
        for start in range(0,len(rows),args.eval_batch):
            batch=rows[start:start+args.eval_batch]
            x,y,mask=base._token_batch(tok,batch,args.device,args.max_seq)
            with torch.no_grad():
                base_logits=bb(input_ids=x,attention_mask=mask,return_dict=True,use_cache=False).logits[:,:-1].float()
                hidden=captured[-1][:,:-1,:]
                gold=y[:,1:];valid=gold!=-100
                # Compute the two glass-head projections on supervised token
                # positions only. This avoids materializing TWO additional
                # [batch, sequence, full-vocabulary] GPU logit tensors.
                supervised=hidden[valid]
                original_logits=head(original.net(supervised)).float()
                refined_logits=head(refined.net(supervised)).float()
                baseline_supervised=base_logits[valid]
                target_supervised=gold[valid]
                modes=torch.full_like(gold,-1)
                modes[valid]=_match(refined.net,supervised)
                logits={'baseline':baseline_supervised,'original_glass':original_logits,
                        'refined_glass':refined_logits}
                all_predictions={};per_loss={}
                for k,v in logits.items():
                    predicted=torch.full_like(gold,-1)
                    predicted[valid]=v.argmax(-1)
                    all_predictions[k]=predicted
                    token_losses=torch.zeros_like(gold,dtype=torch.float32)
                    token_losses[valid]=F.cross_entropy(v,target_supervised,reduction='none')
                    per_loss[k]=token_losses
                for i,row in enumerate(batch):
                    cell=f'T{row["rung"]:02d}/delta{row["residual"]:+d}'
                    for label in logits:
                        t=grouped[cell][label]
                        t['nll']+=float(per_loss[label][i][valid[i]].sum())
                        t['tokens']+=int(valid[i].sum())
                        wrong=(all_predictions[label][i]!=gold[i])&valid[i]
                        t['wrong_tokens']+=int(wrong.sum())
                        if bool(wrong.any()):cases[row['id']][label]+=int(wrong.sum())
                    for pos in torch.nonzero(valid[i],as_tuple=False).flatten().tolist():
                        mode=int(modes[i,pos]);key='unmatched' if mode<0 else f'mode_{mode:03d}'
                        stats=mode_stats[key];stats['tokens']+=1
                        bad_b=bool(all_predictions['baseline'][i,pos]!=gold[i,pos])
                        bad_o=bool(all_predictions['original_glass'][i,pos]!=gold[i,pos])
                        bad_r=bool(all_predictions['refined_glass'][i,pos]!=gold[i,pos])
                        if bad_b:
                            stats['baseline_errors']+=1
                            if mode>=0:mode_failure_cases[key].add(row['id'])
                        if bad_o:stats['original_errors']+=1
                        if bad_r:stats['refined_errors']+=1
                        if bad_b and not bad_r:
                            stats['baseline_fixed']+=1
                            if mode>=0:mode_fixed_cases[key].add(row['id'])
                        if not bad_b and bad_r:stats['baseline_regressions']+=1
                        if bad_o and not bad_r:stats['original_fixed']+=1
                        if not bad_o and bad_r:stats['original_regressions']+=1
            captured.clear()
            if args.progress_every and (start//args.eval_batch+1)%args.progress_every==0:
                print('GLASS_REFINE_AUDIT_PROGRESS='+json.dumps({'cases':start+len(batch),
                              'total':len(rows)},sort_keys=True),flush=True)
    totals={k:{'tokens':sum(c[k]['tokens'] for c in grouped.values()),
               'wrong_tokens':sum(c[k]['wrong_tokens'] for c in grouped.values())}
            for k in ('baseline','original_glass','refined_glass')}
    for k,v in totals.items():
        v['mean_loss']=round(sum(c[k]['nll'] for c in grouped.values())/max(1,v['tokens']),8)
    by_cell={key:{model:{'tokens':v['tokens'],'wrong_tokens':v['wrong_tokens'],
                    'mean_loss':round(v['nll']/max(1,v['tokens']),8)} for model,v in item.items()}
             for key,item in sorted(grouped.items())}
    by_mode={k:{**dict(v),
                'distinct_baseline_failure_cases':len(mode_failure_cases.get(k,())),
                'distinct_fixed_cases':len(mode_fixed_cases.get(k,()))}
             for k,v in sorted(mode_stats.items())}
    mode_reuse={'modes_with_baseline_errors':sum(v.get('baseline_errors',0)>0 for k,v in by_mode.items() if k!='unmatched'),
                'modes_correcting_multiple_tokens':sum(v.get('baseline_fixed',0)>1 for k,v in by_mode.items() if k!='unmatched'),
                'modes_correcting_multiple_distinct_cases':sum(v.get('distinct_fixed_cases',0)>1 for k,v in by_mode.items() if k!='unmatched'),
                'total_matched_baseline_error_tokens':sum(v.get('baseline_errors',0) for k,v in by_mode.items() if k!='unmatched'),
                'total_matched_fixes':sum(v.get('baseline_fixed',0) for k,v in by_mode.items() if k!='unmatched')}
    result={'schema':SCHEMA,'lineage':lineage,'refined_sha256':base._file_hash(work/'refined_glass.pt'),
            'cases':len(rows),'full_corpus':not bool(args.case_cap),'totals':totals,
            'by_cell':by_cell,'by_mode':by_mode,'mode_reuse':mode_reuse,
            'error_case_ids':sorted(cases),
            'note':'Teacher-forced next-token predictions, NOT greedy final-answer correctness.'}
    _json(path,result)
    return result


def greedy_audit(bb,tok,head,rows,glass,args,work,lineage,error_ids):
    """Either exhaustive greedy corpus preservation test or targeted original
    failure + stratified control test. Restartable at completed batch boundaries.
    """
    import torch
    pool={row['id']:row for row in rows}
    if args.greedy_audit=='full':
        selected=rows
    else:
        selected=[row for row in rows if row['id'] in error_ids]
        selected_ids={row['id'] for row in selected}
        for group in base._groups(rows).values():
            n=min(args.controls_per_cell,len(group))
            for j in range(n):
                item=group[min(len(group)-1,int((j+0.5)*len(group)/n))]
                if item['id'] not in selected_ids:
                    selected.append(item);selected_ids.add(item['id'])
    path=work/f'greedy_{args.greedy_audit}.json'
    partial=work/f'greedy_{args.greedy_audit}_partial.json'
    identity={'lineage':lineage,'refined_sha256':base._file_hash(work/'refined_glass.pt'),
              'mode':args.greedy_audit,'case_ids_sha256':hashlib.sha256(
                 '\n'.join(row['id'] for row in selected).encode()).hexdigest(),
              'max_tokens':args.max_tokens,'max_new_tokens':args.max_new_tokens,
              'eval_batch':args.eval_batch,'controls_per_cell':args.controls_per_cell}
    if path.exists():
        previous=json.loads(path.read_text(encoding='utf8'))
        if previous['identity']!=identity:raise RuntimeError('GLASS_GREEDY_AUDIT_DRIFT')
        return previous
    completed={}
    if partial.exists():
        saved=json.loads(partial.read_text(encoding='utf8'))
        if saved['identity']!=identity:raise RuntimeError('GLASS_GREEDY_AUDIT_PARTIAL_DRIFT')
        completed=saved['completed']
    for start in range(0,len(selected),args.eval_batch):
        batch=selected[start:start+args.eval_batch]
        if all(row['id'] in completed for row in batch):continue
        with torch.no_grad():
            baseline_outputs=base.ladder._greedy(bb,tok,[row['source'] for row in batch],
                                    args.device,args.max_tokens,args.max_new_tokens)
            with glass.hook(head):
                refined_outputs=base.ladder._greedy(bb,tok,[row['source'] for row in batch],
                                    args.device,args.max_tokens,args.max_new_tokens)
        if len(baseline_outputs)!=len(batch) or len(refined_outputs)!=len(batch):
            raise RuntimeError('GLASS_GREEDY_BATCH_LENGTH_MISMATCH')
        for row,b,n in zip(batch,baseline_outputs,refined_outputs):
            bok,_=base.ladder.correctness(row['source'],row['target'],b)
            nok,_=base.ladder.correctness(row['source'],row['target'],n)
            completed[row['id']]={'rung':row['rung'],'delta':row['residual'],
                   'baseline_correct':bool(bok),'refined_correct':bool(nok),
                   'changed':b!=n,'baseline_output':b if not bok or bok!=nok else None,
                   'refined_output':n if not nok or bok!=nok else None}
        if args.progress_every and ((start//args.eval_batch+1)%args.progress_every==0 or
                                     start+len(batch)==len(selected)):
            print('GLASS_GREEDY_AUDIT_PROGRESS='+json.dumps({'tested':len(completed),
                     'total':len(selected),'mode':args.greedy_audit},sort_keys=True),flush=True)
        if ((start//args.eval_batch+1)%args.greedy_checkpoint_every==0 or
                start+len(batch)==len(selected)):
            _json(partial,{'identity':identity,'completed':completed})
    counts=collections.Counter();by_cell=collections.defaultdict(collections.Counter)
    failures=[]
    for id,entry in completed.items():
        b=entry['baseline_correct'];r=entry['refined_correct']
        outcome='both_correct' if b and r else 'fixed' if r else 'regressed' if b else 'both_wrong'
        counts[outcome]+=1
        by_cell[f'T{entry["rung"]:02d}/delta{entry["delta"]:+d}'][outcome]+=1
        if outcome in ('fixed','regressed'):
            failures.append({'id':id,'outcome':outcome,
                             'baseline_output':entry['baseline_output'],
                             'refined_output':entry['refined_output']})
    result={'schema':SCHEMA,'identity':identity,'tested':len(completed),
            'corpus_cases':len(rows),'full_corpus_tested':args.greedy_audit=='full' and len(completed)==len(rows),
            'outcomes':dict(counts),'net_fixes':counts['fixed']-counts['regressed'],
            'by_cell':{k:dict(v) for k,v in sorted(by_cell.items())},
            'changed_answers':failures,
            'note':'Greedy answers are independently generated with glass off and on.'}
    _json(path,result)
    partial.unlink(missing_ok=True)
    return result


def run(args):
    if args.stage=='status':
        work=Path(args.output)/'glass_refinement'
        paths=[work/'refine_training.json',work/'teacher_forced_audit.json',
               work/'greedy_targeted.json',work/'greedy_full.json']
        found={p.name:json.loads(p.read_text(encoding='utf8')) for p in paths if p.is_file()}
        print('GLASS_REFINE_STATUS='+json.dumps(found,sort_keys=True),flush=True)
        return found
    if (args.refine_epochs<1 or args.refine_batch_size<1 or args.eval_batch<1 or
        args.protect_per_mode<0 or args.protect_ratio<0 or args.refine_lr<=0 or
        args.preserve_weight<0 or args.refine_l2<0 or args.greedy_checkpoint_every<1):
        raise ValueError('GLASS_REFINEMENT_INVALID_BUDGET')
    bb,tok,head,zero,rows,old_glass,lineage,work,qwen_sha,old_report=_unpack(args)
    refined=base.RelativeDeformationGlass(None,hidden=int(head.weight.shape[1]),
                  keys=__import__('torch').load(Path(args.output)/'glass.pt',map_location='cpu',weights_only=True)['keys'],
                  projection_dim=old_glass.net.projection.shape[1],
                  threshold=old_glass.net.threshold,seed=args.seed)
    refined.net.to(args.device).load_state_dict(old_glass.net.state_dict())
    if args.stage in ('refine','both'):
        cache=cache_activations(bb,tok,head,rows,old_glass,args,work,lineage)
        stats=train_refinement(head,refined,cache,args,work,lineage)
        info={'lineage':lineage,'training':stats,'cache_stats':cache['stats'],
              'matched_wrong_entries':cache['matched_wrong_entries'],
              'protected_correct_entries':cache['protected_correct_entries'],
              'baseline_original_report':old_report.get('glass')}
        _json(work/'refine_training.json',info)
        print('GLASS_REFINEMENT_TRAINED='+json.dumps({k:v for k,v in info.items()
          if k not in ('lineage','baseline_original_report')},sort_keys=True),flush=True)
    if args.stage=='refine':
        return info
    import torch
    final=work/'refined_glass.pt'
    if not final.is_file():raise RuntimeError('GLASS_REFINEMENT_MUST_TRAIN_FIRST')
    finished=torch.load(final,map_location='cpu',weights_only=True)
    if finished['lineage']!=lineage:raise RuntimeError('GLASS_REFINEMENT_STALE_MODEL')
    refined.net.load_state_dict(finished['glass']);refined.net.eval()
    old_glass.net.eval()
    audit=teacher_forced_audit(bb,tok,head,rows,old_glass,refined,args,work,lineage)
    error_ids=set(audit['error_case_ids'])
    for key in ('backbone_greedy','glass_greedy'):
        for summary in old_report.get(key,{}).values():
            error_ids.update(summary.get('failed_ids',[]))
    greedy=greedy_audit(bb,tok,head,rows,refined,args,work,lineage,error_ids)
    if base._lora_digest(bb,zero)!=qwen_sha:
        raise RuntimeError('GLASS_REFINEMENT_MUTATED_FROZEN_QWEN')
    result={'original_glass':audit['totals']['original_glass'],
            'refined_glass':audit['totals']['refined_glass'],
            'baseline':audit['totals']['baseline'],
            'mode_reuse':audit['mode_reuse'],
            'greedy':{'tested':greedy['tested'],'outcomes':greedy['outcomes'],
                      'net_fixes':greedy['net_fixes'],'full_corpus_tested':greedy['full_corpus_tested']},
            'report_directory':str(work)}
    print('GLASS_REFINEMENT_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('refine','audit','both','status'),default='refine')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--stage1-checkpoint');p.add_argument('--text-checkpoint')
    p.add_argument('--ladder-working');p.add_argument('--output',required=True)
    p.add_argument('--radii',default='0,1,2,3,4');p.add_argument('--max-rung',type=int,default=8)
    p.add_argument('--operand-limit',type=int,default=1024)
    p.add_argument('--per-cell',type=int,default=8)
    p.add_argument('--t00-mode',choices=('exhaustive','sampled'),default='exhaustive')
    p.add_argument('--seed',type=int,default=42257);p.add_argument('--case-cap',type=int,default=0)
    p.add_argument('--batch-size',type=int,default=4)
    p.add_argument('--eval-batch',type=int,default=8)
    p.add_argument('--max-seq',type=int,default=192)
    p.add_argument('--max-tokens',type=int,default=256)
    p.add_argument('--max-new-tokens',type=int,default=64)
    p.add_argument('--refine-epochs',type=int,default=8)
    p.add_argument('--refine-batch-size',type=int,default=32)
    p.add_argument('--refine-lr',type=float,default=.001)
    p.add_argument('--refine-l2',type=float,default=.0001)
    p.add_argument('--preserve-weight',type=float,default=.1)
    p.add_argument('--protect-per-mode',type=int,default=8)
    p.add_argument('--protect-ratio',type=int,default=2,help='Maximum protected correct tokens per matched baseline error in the same mode')
    p.add_argument('--greedy-audit',choices=('targeted','full'),default='targeted')
    p.add_argument('--controls-per-cell',type=int,default=2)
    p.add_argument('--greedy-checkpoint-every',type=int,default=20)
    p.add_argument('--checkpoint-every',type=int,default=20)
    p.add_argument('--progress-every',type=int,default=100)
    p.add_argument('--baseline');p.add_argument('--backbone');p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if args.stage!='status' and (not args.stage1_checkpoint or not args.text_checkpoint or not args.ladder_working):
        p.error('same three source checkpoint arguments as the original two-pass smoke required')
    return run(args)

if __name__=='__main__': main()
