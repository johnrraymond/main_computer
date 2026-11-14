#!/usr/bin/env python3
"""First sequential glass layer: easy -> mixed -> conditional hard.

Both A (ungated) and B (soft gated) are independent first-layer candidates.
Only the heads train; a *copied*, frozen mixed-frontier backbone is reused.
An immutable baseline classification deck is shared across schemes. Train,
validation and fresh-audit indices occupy nonoverlapping deterministic ranges.
Round checkpoints are recoverable after interruption, including each A/B optimizer.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import random
from types import SimpleNamespace

from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_radius_ladder_frontier as fr
from tools import nanojev_qwen_lm_frontier_mixed_pass as samples
from tools import nanojev_qwen_lm_frontier_tasks as tasks
from tools import nanojev_qwen_lm_deformation_smoke as ab

SCHEMA='nanojev.qwen.frontier.rank1-glasses.v1'
SCHEMES=('easy','mixed','hard')


def _paths(args):
    parent=Path(args.samples_output).resolve()
    root=Path(args.glasses_output).resolve()
    if parent==root or root in parent.parents or parent in root.parents:
        raise ValueError('GLASSES_OUTPUT_MUST_BE_SEPARATE')
    return parent,root


def _policy(args,source_sha):
    return {'schema':SCHEMA,'backbone_sha256':source_sha,
            'seed':args.glass_seed,'train_per_cell':args.glass_train_per_cell,
            'validation_per_cell':args.glass_validation_per_cell,
            'easy_threshold':args.glass_easy_threshold,
            'width':args.glass_width,'feature_layers':args.glass_feature_layers,
            'lr':args.glass_lr,'preserve_weight':args.glass_preserve_weight,
            'delta_l2':args.glass_delta_l2,'gate_loss':args.glass_gate_loss,
            'steps_per_round':args.glass_steps_per_round,
            'rounds':args.glass_rounds,'plateau_patience':args.glass_plateau_patience,
            'retention_floor':args.glass_retention_floor,
            'max_tokens':args.max_tokens,'max_new_tokens':args.max_new_tokens}


def _validate(args):
    if (args.glass_train_per_cell<2 or args.glass_validation_per_cell<2 or
        not 0<args.glass_easy_threshold<=1 or not 0<args.glass_retention_floor<=1 or
        args.glass_feature_layers<1 or args.glass_width<1 or args.glass_lr<=0 or
        args.glass_steps_per_round<1 or args.glass_rounds<1 or
        args.glass_plateau_patience<1 or args.max_new_tokens<1 or args.max_tokens<32 or
        args.glass_preserve_weight<0 or args.glass_delta_l2<0 or args.glass_gate_loss<0):
        raise ValueError('GLASSES_INVALID_POLICY')
    parent,root=_paths(args)
    if args.glass_scheme not in SCHEMES:raise ValueError('GLASSES_UNKNOWN_SCHEME')
    return parent,root


def _readiness(parent):
    path=parent/'glass_readiness.json';ck=parent/'glass_ready.pt'
    if not path.is_file() or not ck.is_file():
        raise RuntimeError('GLASSES_REQUIRE_COMPLETED_MIXED_FRONTIER')
    info=json.loads(path.read_text(encoding='utf8'))
    if info.get('status') not in ('RAW_ACCURACY_BALANCED','MIXED_LOSS_PLATEAU') or info.get('glass_ready_checkpoint')!='glass_ready.pt':
        raise RuntimeError('GLASSES_MIXED_READINESS_INVALID')
    return ck,info


def _verify_backbone(snapshot):
    import torch
    obj=torch.load(snapshot,map_location='cpu',weights_only=True)
    state=obj.get('state',{})
    from tools import nanojev_qwen_lm_frontier_accuracy_balance as balance
    if (obj.get('schema') not in (balance.SCHEMA,samples.SCHEMA) or not isinstance(state,dict) or
        not isinstance(state.get('best_step'),int) or
        state.get('best_step')!=state.get('step') or
        not isinstance(state.get('policy'),dict) or
        not isinstance(obj.get('lora'),dict) or not obj['lora']):
        raise RuntimeError('GLASSES_INCOMPATIBLE_BEST_MIXED_CHECKPOINT')
    return obj


def _snapshot_backbone(parent,root):
    source,readiness=_readiness(parent)
    root.mkdir(parents=True,exist_ok=True)
    frozen=root/'backbone_snapshot.pt'
    if not frozen.is_file():
        _verify_backbone(source)
        fr._atomic_copy(source,frozen)
    _verify_backbone(frozen)
    # The already-copied snapshot is the frozen identity for all schemes.
    if fr._sha256(source)!=fr._sha256(frozen):
        raise RuntimeError('GLASSES_SOURCE_BACKBONE_CHANGED_AFTER_SNAPSHOT')
    return frozen,readiness


def _case_deck(state,policy,split):
    # No overlap between training selection and validation. The "fresh" deck
    # is only used for *final* diagnosis, never head selection/training.
    prefix={'train':100_000_000,'validation':200_000_000,'fresh':300_000_000}[split]
    count={'train':policy['train_per_cell'],
           'validation':policy['validation_per_cell'],
           'fresh':policy['validation_per_cell']}[split]
    p=state['policy'];rung=(p['start_rung_override'] if p['start_rung_override']>=0
                             else max(0,int(state['seed_state']['rung'])))
    out=[]
    for task,level in tasks.task_order(p['source_levels']):
        for i in range(count):
            # Coprime stride walks all generator submodes (3,4,5) rather than
            # sampling only one residue; distinct per-split seed prevents reuse.
            idx=prefix+97*i+37*level
            out.append(tasks.make_case(task,level,idx,
                seed=policy['seed']+{'train':0,'validation':1,'fresh':2}[split],
                operand_boundary=p['operand_boundary'],start_rung=rung))
    if len({r['id'] for r in out})!=len(out):
        raise RuntimeError('GLASSES_DUPLICATE_CASE_IDS')
    return out


def _classify(rows,baseline,threshold):
    stats={}
    for r in rows:
        key=f"{r['task']}:L{r['level']}"
        entry=stats.setdefault(key,{'correct':0,'total':0})
        entry['correct']+=int(baseline[r['id']]['correct']);entry['total']+=1
    easy={k for k,v in stats.items() if v['correct']/v['total']>=threshold}
    hard=set(stats)-easy
    return easy,hard,{k:{**v,'accuracy':v['correct']/v['total'],
                          'class':'easy' if k in easy else 'hard'} for k,v in stats.items()}


def _group(row,easy):
    return 'easy' if f"{row['task']}:L{row['level']}" in easy else 'hard'


def _metric(rows,baseline,outputs,easy):
    a={r['id']:v for r,v in zip(rows,outputs)}
    b={r['id']:v for r,v in zip(rows,baseline)} if isinstance(baseline,list) else baseline
    result={}
    for tag in ('easy','hard','all'):
        subset=[r for r in rows if tag=='all' or _group(r,easy)==tag]
        fixed=sum(not b[r['id']]['correct'] and a[r['id']]['correct'] for r in subset)
        regressed=sum(b[r['id']]['correct'] and not a[r['id']]['correct'] for r in subset)
        result[tag]={'tested':len(subset),'correct':sum(a[r['id']]['correct'] for r in subset),
                     'baseline_correct':sum(b[r['id']]['correct'] for r in subset),
                     'fixed':fixed,'regressed':regressed}
    return result


def _score(m,scheme):
    # Training validation is fixed, paired, and counted on *net correctness*.
    # Easy specialization never pretends hard-domain performance has improved.
    return (m['easy']['correct'] if scheme=='easy' else
            m['hard']['correct'] if scheme=='hard' else m['all']['correct'],
            -m['all']['regressed'])


def _stage_score(metric,scheme,root,name,policy):
    if scheme!='mixed':return list(_score(metric,scheme))
    parent=_read_state(_stage(root,'easy')/'result.json')
    if parent is None:raise RuntimeError('GLASSES_MIXED_NEEDS_EASY_METRICS')
    success=_mixed_success(parent['validation'][name],metric,
                           retention_floor=policy['retention_floor'])
    # Never replace a successfully generalizing checkpoint with a higher
    # aggregate score that destroys the easy corrections.
    return [int(success['success']),metric['all']['correct'],
            -metric['all']['regressed']]


def _mixed_success(before,after,*,retention_floor):
    easy_before=before['easy']['correct'];easy_after=after['easy']['correct']
    hard_gain=after['hard']['correct']-before['hard']['correct']
    return {'success':bool(after['hard']['tested'] and hard_gain>0 and
                           easy_after>=retention_floor*easy_before),
            'hard_correct_gain':hard_gain,'easy_correct_before':easy_before,
            'easy_correct_after':easy_after,'retention_floor':retention_floor}


def _stage(root,scheme):return root/scheme


def _read_state(path):
    return json.loads(path.read_text(encoding='utf8')) if path.is_file() else None


def _predecessor(root,scheme):
    return None if scheme=='easy' else 'easy'


def _hard_needed(root,force=False):
    # A complete baseline census can legitimately find zero hard cells.
    # A forced fallback still cannot train on nonexistent hard examples.
    classification=_read_state(root/'classification.json')
    if classification is not None and classification.get('hard') == []:
        return False
    if force:return True  # Keep the preexisting force behavior when hard cells exist.
    info=_read_state(_stage(root,'mixed')/'result.json')
    if not info or info.get('status')!='COMPLETE':
        raise RuntimeError('GLASSES_HARD_REQUIRES_COMPLETED_MIXED')
    return not any(v['success'] for v in info['mixed_success'].values())


def _check_stage_order(root,scheme,force=False):
    if scheme=='easy':return True
    previous=_read_state(_stage(root,'easy')/'result.json')
    if not previous or previous.get('status')!='COMPLETE':
        raise RuntimeError('GLASSES_REQUIRE_COMPLETED_EASY')
    if scheme=='hard':return _hard_needed(root,force)
    return True


def _load_networks(args,hidden,root,scheme,policy,device):
    import torch
    glasses={};opts={}
    predecessor=_predecessor(root,scheme)
    parent_checksums={}
    for name,gated in [('A',False),('B',True)]:
        glass=ab.ContinuousGlass(hidden,args.glass_feature_layers,args.glass_width,gated)
        glass.net.to(device)
        opt=torch.optim.AdamW(glass.net.parameters(),lr=args.glass_lr,weight_decay=0.)
        if predecessor:
            path=_stage(root,predecessor)/f'best_{name}.pt'
            if not path.exists():raise RuntimeError('GLASSES_MISSING_EASY_BEST_'+name)
            obj=torch.load(path,map_location='cpu',weights_only=True)
            if obj.get('policy')!=policy or obj.get('name')!=name:
                raise RuntimeError('GLASSES_EASY_PARENT_POLICY_DRIFT')
            glass.net.load_state_dict(obj['weights'])
            opt.load_state_dict(obj['optimizer'])
            for s in opt.state.values():
                for k,v in s.items():
                    if torch.is_tensor(v):s[k]=v.to(device)
            parent_checksums[name]=fr._sha256(path)
        glasses[name]=glass;opts[name]=opt
    return glasses,opts,parent_checksums


def _weights(glass):
    return {k:v.detach().cpu().clone() for k,v in glass.net.state_dict().items()}


def _save_best(stage,name,glass,opt,policy,metric,round_no):
    obj={'schema':SCHEMA,'name':name,'policy':policy,'weights':_weights(glass),
         'optimizer':opt.state_dict(),'validation_metric':metric,'round':round_no}
    fr._durable_torch(stage/f'best_{name}.pt',obj)


def _working_state(stage,glasses,opts,state):
    obj={'schema':SCHEMA,'state':state,
         'branches':{name:{'weights':_weights(glasses[name]),
                           'optimizer':opts[name].state_dict()} for name in ('A','B')}}
    if (stage/'working.pt').is_file():fr._atomic_copy(stage/'working.pt',stage/'previous.pt')
    fr._durable_torch(stage/'working.pt',obj)
    ladder._atomic_json(stage/'progress.json',state)


def _resume(stage,glasses,opts,policy,device):
    import torch
    file=stage/'working.pt'
    try:obj=torch.load(file,map_location='cpu',weights_only=True)
    except Exception:
        file=stage/'previous.pt'
        obj=torch.load(file,map_location='cpu',weights_only=True)
        fr._event('GLASSES_RECOVERED',{'from':str(file)})
    state=obj['state']
    if obj.get('schema')!=SCHEMA or state.get('policy')!=policy:
        raise RuntimeError('GLASSES_RESUME_POLICY_DRIFT')
    for name in ('A','B'):
        glasses[name].net.load_state_dict(obj['branches'][name]['weights'])
        opts[name].load_state_dict(obj['branches'][name]['optimizer'])
        for s in opts[name].state.values():
            for k,v in s.items():
                if torch.is_tensor(v):s[k]=v.to(device)
    return state


def _setup_deck(args,root,bb,tok,snapshot,policy,source_state):
    deck=root/'classification.json'
    train=_case_deck(source_state,policy,'train')
    validation=_case_deck(source_state,policy,'validation')
    # Baseline progress is append-only: an interrupted census resumes at the
    # next unanswered problem rather than losing hours of greedy evaluation.
    baseline_path=root/'baseline.jsonl'
    known={}
    if baseline_path.exists():
        for line in baseline_path.read_text(encoding='utf8').splitlines():
            entry=json.loads(line)
            if entry['backbone_sha256']!=policy['backbone_sha256']:
                raise RuntimeError('GLASSES_BASELINE_CHECKPOINT_DRIFT')
            known[entry['id']]=entry['result']
    all_rows=train+validation
    cached_baseline_records=sum(r['id'] in known for r in all_rows)
    for index,row in enumerate(all_rows,1):
        if row['id'] not in known:
            result=ab._rollout(bb,tok,row,args)
            known[row['id']]={'id':row['id'],'correct':result['correct'],'output':result['output']}
            fr._append(baseline_path,{'id':row['id'],'backbone_sha256':policy['backbone_sha256'],
                                      'result':known[row['id']]})
        if index%8==0 or index==len(all_rows):
            fr._event('GLASSES_BASELINE_PROGRESS',{'tested':index,'total':len(all_rows)})
    easy,hard,rank=_classify(train,known,policy['easy_threshold'])
    document={'schema':SCHEMA,'policy':policy,'train_cases':len(train),
              'validation_cases':len(validation), 'cells':rank,
              'easy':sorted(easy),'hard':sorted(hard),
              'baseline_correct':sum(v['correct'] for v in known.values())}
    if deck.exists() and _read_state(deck)!=document:
        raise RuntimeError('GLASSES_CLASSIFICATION_DRIFT')
    if not deck.exists():ladder._atomic_json(deck,document)
    # All-easy and all-hard are valid observations, not reasons to fabricate
    # counterexamples. Preserve this immutable classification on restart.
    train_correct=sum(bool(known[r['id']]['correct']) for r in train)
    validation_correct=sum(bool(known[r['id']]['correct']) for r in validation)
    fr._event('GLASSES_BASELINE_CLASSIFICATION',{
        'classification':'split' if easy and hard else ('all_easy' if easy else 'all_hard'),
        'threshold':policy['easy_threshold'],'easy_cells':len(easy),
        'hard_cells':len(hard),'train_correct':train_correct,
        'train_errors':len(train)-train_correct,'train_total':len(train),
        'validation_correct':validation_correct,
        'validation_errors':len(validation)-validation_correct,
        'validation_total':len(validation),
        'no_invented_hard_cases':True,
        'reused_baseline_records':cached_baseline_records,
        'new_baseline_records':len(all_rows)-cached_baseline_records})
    return train,validation,known,easy,hard


def _evaluations(bb,tok,rows,base,easy,glasses,args):
    summary={}
    for name in ('A','B'):
        outputs=[]
        for i,row in enumerate(rows,1):
            outputs.append(ab._rollout(bb,tok,row,args,glass=glasses[name]))
            if i%8==0 or i==len(rows):
                fr._event('GLASSES_EVAL_PROGRESS',{'branch':name,'tested':i,'total':len(rows)})
        summary[name]=_metric(rows,base,outputs,easy)
    return summary


def _baseline_metrics(rows,base,easy):
    return _metric(rows,base,[base[r['id']] for r in rows],easy)


def _train_args(args):
    # The proven glass trainer uses these names; adapt without altering it.
    return SimpleNamespace(device=args.device,max_tokens=args.max_tokens,
        max_new_tokens=args.max_new_tokens,feature_layers=args.glass_feature_layers,
        preserve_weight=args.glass_preserve_weight,delta_l2=args.glass_delta_l2,
        gate_loss=args.glass_gate_loss,decode_cache=True,decode_progress_tokens=16)


def _train_rank1_step(glass,opt,bb,tok,row,replay,baseline,args,rng):
    """Learn actual failures while preserving previously acquired fixes.

    Crucial distinction from the old edge-smoke trainer: an example originally
    wrong for frozen Qwen but *now corrected by the glass* gets a positive
    teacher-forced token loss, never a zero-delta preservation penalty. A
    baseline-correct success can favor low intervention; acquired corrections
    must not be erased to satisfy that unrelated preservation objective.
    """
    import torch
    import torch.nn.functional as F
    opt.zero_grad(set_to_none=True)
    terms=[]
    for sample,is_replay in ((row,False),(replay,True)):
        if sample is None:continue
        actual=ab._rollout(bb,tok,sample,args,glass=glass,capture_first_wrong=True)
        was_baseline_correct=baseline[sample['id']]['correct']
        if not actual['correct'] and actual['wrong_state'] is not None and actual['wrong_state']['gold'] is not None:
            h,feats=ab._capture_gold_position(bb,tok,actual['wrong_state'],args)
            gold=int(actual['wrong_state']['gold'])
            needs_target=True
        else:
            src=ab._token_ids(tok,sample['source']+'\n')
            expected=ab._token_ids(tok,sample['target']+'\n')
            position=rng.randrange(len(expected))
            h,feats=ab._capture_gold_position(bb,tok,{'prefix':src+expected[:position]},args)
            gold=int(expected[position])
            # The crucial preservation policy: previously learned corrections
            # stay supervised even when the output is already correct.
            needs_target=not was_baseline_correct or not actual['correct']
        adjusted,delta,prob=glass.apply(h,feats)
        if needs_target:
            head=bb.get_output_embeddings()
            weight=getattr(head,'weight',None)
            logits=head(adjusted.to(dtype=weight.dtype if weight is not None else adjusted.dtype)).float()
            terms.append(F.cross_entropy(logits,torch.tensor([gold],device=args.device)))
            terms.append(args.delta_l2*delta.square().mean())
            if glass.gated:
                terms.append(args.gate_loss*F.binary_cross_entropy(prob,torch.ones_like(prob)))
        else:
            terms.append(args.preserve_weight*delta.square().mean())
            if glass.gated:
                terms.append(args.gate_loss*F.binary_cross_entropy(prob,torch.zeros_like(prob)))
    loss=sum(terms)
    if not torch.isfinite(loss):raise RuntimeError('GLASSES_NONFINITE_TRAIN_LOSS')
    loss.backward()
    torch.nn.utils.clip_grad_norm_(glass.net.parameters(),1.)
    opt.step()
    return {'loss':round(float(loss.detach()),7)}


def _load_backbone(args,snapshot,source_state):
    from tools import nanojev_qwen_zero_line_backbone as zero
    import torch
    bb,tok,sources,frozen,dirs,_=ladder._fresh_parent(args)
    if (sources!=source_state['seed_state']['source_sha256'] or
        frozen!=source_state['seed_state']['frozen_sha256']):
        raise RuntimeError('GLASSES_FROZEN_BACKBONE_LINEAGE_MISMATCH')
    ck=torch.load(snapshot,map_location='cpu',weights_only=True)
    zero.restore_lora(bb,ck['lora'])
    bb.eval();bb.requires_grad_(False)
    return bb,tok,zero


def _stage_result(stage,scheme,state,metrics,root,policy):
    result={'schema':SCHEMA,'status':'COMPLETE','scheme':scheme,
            'rounds':state['round'],'reason':state['finish_reason'],
            'backbone_sha256':policy['backbone_sha256'],'policy':policy,
            'validation':metrics,'best_rounds':state['best_round']}
    if scheme=='mixed':
        easy_result=_read_state(_stage(root,'easy')/'result.json')
        result['mixed_success']={name:_mixed_success(easy_result['validation'][name],metrics[name],
                    retention_floor=policy['retention_floor']) for name in ('A','B')}
        classification=_read_state(root/'classification.json')
        no_hard_cells=classification is not None and classification.get('hard')==[]
        result['skip_hard']=no_hard_cells or any(v['success'] for v in result['mixed_success'].values())
        if no_hard_cells:result['skip_hard_reason']='NO_HARD_CELLS_IN_BASELINE'
    ladder._atomic_json(stage/'result.json',result)
    return result


def run(args):
    import torch
    parent,root=_validate(args)
    if not _check_stage_order(root,args.glass_scheme,args.glass_force_hard):
        classification=_read_state(root/'classification.json')
        reason=('NO_HARD_CELLS_IN_BASELINE' if classification is not None and
                classification.get('hard')==[] else 'MIXED_ALREADY_SUCCEEDED')
        result={'status':'SKIPPED','scheme':'hard','reason':reason}
        fr._event('QWEN_GLASS_RANK1',result);return result
    if not args.stage1_checkpoint or not args.text_checkpoint:
        raise ValueError('GLASSES_PARENT_CHECKPOINTS_REQUIRED')
    source,ready=_snapshot_backbone(parent,root)
    source_ck=_verify_backbone(source);source_state=source_ck['state']
    policy=_policy(args,fr._sha256(source))
    stage=_stage(root,args.glass_scheme)
    if (stage/'result.json').is_file():
        result=_read_state(stage/'result.json')
        if result['backbone_sha256']!=policy['backbone_sha256']:
            raise RuntimeError('GLASSES_COMPLETED_STAGE_BACKBONE_DRIFT')
        if result.get('policy')!=policy:
            raise RuntimeError('GLASSES_COMPLETED_STAGE_POLICY_DRIFT')
        fr._event('QWEN_GLASS_RANK1_COMPLETE',result);return result
    stage.mkdir(parents=True,exist_ok=True)
    # No glass may ever train backbone parameters or write into the mixed run.
    bb,tok,zero=_load_backbone(args,source,source_state)
    args_train=_train_args(args)
    train,validation,baseline,easy,hard=_setup_deck(args,root,bb,tok,source,policy,source_state)
    hidden=int(bb.get_output_embeddings().weight.shape[1])
    glasses,opts,parent_hashes=_load_networks(args,hidden,root,args.glass_scheme,policy,args.device)
    if (stage/'working.pt').is_file():
        state=_resume(stage,glasses,opts,policy,args.device)
        if state['parents']!=parent_hashes:raise RuntimeError('GLASSES_STAGE_PARENT_DRIFT')
    else:
        if args.glass_scheme=='easy':
            validation_metrics={'A':_baseline_metrics(validation,baseline,easy),
                                'B':_baseline_metrics(validation,baseline,easy)}
        else:
            validation_metrics=_evaluations(bb,tok,validation,baseline,easy,glasses,args_train)
        state={'policy':policy,'scheme':args.glass_scheme,'round':0,'step_in_round':0,
               'best_scores':{name:_stage_score(validation_metrics[name],args.glass_scheme,root,name,policy)
                              for name in ('A','B')},
               'best_metrics':validation_metrics,'best_round':{'A':0,'B':0},
               'no_improve':0,'parents':parent_hashes,'finish_reason':None}
        for name in ('A','B'):
            _save_best(stage,name,glasses[name],opts[name],policy,validation_metrics[name],0)
        _working_state(stage,glasses,opts,state)
    frozen_hash=fr._sha256(source)
    train_subset=[r for r in train if args.glass_scheme=='mixed' or
                  _group(r,easy)==args.glass_scheme]
    failing=[r for r in train_subset if not baseline[r['id']]['correct']]
    easy_fail=[r for r in failing if _group(r,easy)=='easy']
    hard_fail=[r for r in failing if _group(r,easy)=='hard']
    good=[r for r in train_subset if baseline[r['id']]['correct']]
    # In Mixed and fallback Hard, explicitly replay learned Easy corrections,
    # not just cases that frozen Qwen could already answer.
    easy_fixes=[r for r in train if _group(r,easy)=='easy' and not baseline[r['id']]['correct']]
    # Easy may have no initial mistakes after Phase 2: that's an empirical
    # outcome, not permission to fabricate a corrective-training example.
    if not failing:
        fr._event('GLASSES_NO_RESIDUAL_FAILURES',{'scheme':args.glass_scheme})
        state['finish_reason']='no_residual_training_errors'
    fr._event('GLASSES_STAGE_START',{'scheme':args.glass_scheme,'round':state['round'],
        'rounds':policy['rounds'],'train_cases':len(train_subset),
        'failed_baseline':len(failing),'correct_replay':len(good),
        'easy_cells':len(easy),'hard_cells':len(hard),'frozen_checkpoint':str(source)})
    while state['round']<policy['rounds'] and state['finish_reason'] is None:
        for step in range(state['step_in_round'],policy['steps_per_round']):
            n=state['round']*policy['steps_per_round']+step
            rng=random.Random(policy['seed']+n*487+17)
            # Balanced Easy/Hard failure sampling, rather than allowing the
            # numerous hard errors to overwhelm acquisition of Easy fixes.
            choice=(easy_fail if n%2==0 else hard_fail) if args.glass_scheme=='mixed' else failing
            if not choice:choice=failing
            row=choice[rng.randrange(len(choice))]
            replay_pool=(easy_fixes if args.glass_scheme!='easy' and n%2==0 and easy_fixes else good)
            replay=replay_pool[rng.randrange(len(replay_pool))] if replay_pool else None
            losses={}
            for name in ('A','B'):
                stat=_train_rank1_step(glasses[name],opts[name],bb,tok,row,replay,baseline,
                                       args_train,random.Random(policy['seed']+n*487+29))
                losses[name]=stat['loss']
            state['step_in_round']=step+1
            _working_state(stage,glasses,opts,state)
            fr._event('GLASSES_TRAIN',{'scheme':args.glass_scheme,'round':state['round']+1,
                'step':step+1,'steps_per_round':policy['steps_per_round'],
                'case':row['id'],'A_loss':losses['A'],'B_loss':losses['B']})
        metrics=_evaluations(bb,tok,validation,baseline,easy,glasses,args_train)
        improved=False
        for name in ('A','B'):
            score=_stage_score(metrics[name],args.glass_scheme,root,name,policy)
            if score>state['best_scores'][name]:
                state['best_scores'][name]=score
                state['best_metrics'][name]=metrics[name]
                state['best_round'][name]=state['round']+1
                _save_best(stage,name,glasses[name],opts[name],policy,metrics[name],state['round']+1)
                improved=True
        state['no_improve']=0 if improved else state['no_improve']+1
        state['round']+=1;state['step_in_round']=0
        fr._event('GLASSES_ROUND',{'scheme':args.glass_scheme,'round':state['round'],
            'validation':metrics,'best_round':state['best_round'],
            'no_improve':state['no_improve']})
        if state['round']>=2 and state['no_improve']>=policy['plateau_patience']:
            state['finish_reason']='validation_plateau'
        elif state['round']>=policy['rounds']:
            state['finish_reason']='round_budget'
        _working_state(stage,glasses,opts,state)
    # Success is judged on individually selected best head checkpoints, not on
    # potentially regressed final-round weights. No claim of unseen accuracy.
    if fr._sha256(source)!=frozen_hash:raise RuntimeError('GLASSES_BACKBONE_CHANGED')
    result=_stage_result(stage,args.glass_scheme,state,state['best_metrics'],root,policy)
    fr._event('QWEN_GLASS_RANK1_COMPLETE',result)
    return result


def audit(args):
    """Fresh greedy audit after a stage: no training, no held-out selection."""
    import torch
    parent,root=_validate(args)
    stage=_stage(root,args.glass_scheme)
    record=_read_state(stage/'result.json')
    if not record or record.get('status')!='COMPLETE':
        raise RuntimeError('GLASSES_AUDIT_REQUIRES_COMPLETED_STAGE')
    source=root/'backbone_snapshot.pt'
    if not source.is_file():raise FileNotFoundError(source)
    source_ck=_verify_backbone(source)
    policy=_policy(args,fr._sha256(source))
    if record['backbone_sha256']!=policy['backbone_sha256']:
        raise RuntimeError('GLASSES_AUDIT_LINEAGE_DRIFT')
    if not args.stage1_checkpoint or not args.text_checkpoint:
        raise ValueError('GLASSES_AUDIT_PARENT_CHECKPOINTS_REQUIRED')
    bb,tok,_=_load_backbone(args,source,source_ck['state'])
    candidates=_case_deck(source_ck['state'],policy,'fresh')
    seen={r['source'] for split in ('train','validation')
          for r in _case_deck(source_ck['state'],policy,split)}
    # Finite Boolean/graph templates can repeat an identical source even with
    # disjoint generator indices. Such repeats cannot certify generalization.
    rows=[];duplicates=0
    for row in candidates:
        if row['source'] in seen:
            duplicates+=1;continue
        seen.add(row['source']);rows.append(row)
    classification=_read_state(root/'classification.json')
    if classification is None or classification['policy']!=policy:
        raise RuntimeError('GLASSES_AUDIT_CLASSIFICATION_REQUIRED')
    easy=set(classification['easy'])
    frozen=[]
    for idx,row in enumerate(rows,1):
        frozen.append(ab._rollout(bb,tok,row,_train_args(args)))
        if idx%8==0 or idx==len(rows):
            fr._event('GLASSES_FRESH_BASELINE_PROGRESS',{'tested':idx,'total':len(rows)})
    baseline={r['id']:v for r,v in zip(rows,frozen)}
    metric={'baseline':_baseline_metrics(rows,baseline,easy)}
    hidden=int(bb.get_output_embeddings().weight.shape[1])
    for name,gated in [('A',False),('B',True)]:
        file=stage/f'best_{name}.pt'
        ck=torch.load(file,map_location='cpu',weights_only=True)
        if ck.get('policy')!=policy:raise RuntimeError('GLASSES_AUDIT_BEST_POLICY_DRIFT')
        glass=ab.ContinuousGlass(hidden,policy['feature_layers'],policy['width'],gated)
        glass.net.to(args.device);glass.net.load_state_dict(ck['weights'])
        output=[]
        for idx,row in enumerate(rows,1):
            output.append(ab._rollout(bb,tok,row,_train_args(args),glass=glass))
            if idx%8==0 or idx==len(rows):
                fr._event('GLASSES_FRESH_EVAL_PROGRESS',{'branch':name,'tested':idx,'total':len(rows)})
        metric[name]=_metric(rows,baseline,output,easy)
    report={'schema':SCHEMA,'scheme':args.glass_scheme,'fresh_cases':len(rows),
            'backbone_sha256':policy['backbone_sha256'],
            'no_training':True,'not_used_for_checkpoint_selection':True,
            'candidate_cases':len(candidates),'duplicate_sources_excluded':duplicates,
            'unseen_relative_to_glass_decks':True,
            'metrics':metric}
    ladder._atomic_json(stage/'fresh_audit.json',report)
    fr._event('GLASSES_FRESH_AUDIT',report)
    return report


def main(args):
    parent,root=_validate(args)
    stage=_stage(root,args.glass_scheme)
    if args.stage=='status':
        result={'schema':SCHEMA,'phase':'glasses','scheme':args.glass_scheme,
                'samples_ready':(parent/'glass_readiness.json').is_file() and
                                 (parent/'glass_ready.pt').is_file(),
                'easy':_read_state(_stage(root,'easy')/'result.json'),
                'mixed':_read_state(_stage(root,'mixed')/'result.json'),
                'hard':_read_state(_stage(root,'hard')/'result.json'),
                'current':_read_state(stage/'progress.json')}
        fr._event('QWEN_FRONTIER_GLASSES_STATUS',result);return result
    if args.stage=='preview':
        ready=(parent/'glass_readiness.json').is_file() and (parent/'glass_ready.pt').is_file()
        result={'schema':SCHEMA,'phase':'glasses','scheme':args.glass_scheme,
                'ready':ready,'source':str(parent/'glass_ready.pt'),
                'output':str(stage),'training_requires_ready':True,
                'stage_order':list(SCHEMES),'hard_is_conditional':True}
        fr._event('QWEN_FRONTIER_GLASSES_PREVIEW',result);return result
    if args.stage=='audit':return audit(args)
    if args.stage!='run':raise ValueError('GLASSES_STAGE_RUN_STATUS_PREVIEW_AUDIT_ONLY')
    return run(args)
