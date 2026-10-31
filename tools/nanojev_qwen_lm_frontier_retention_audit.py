#!/usr/bin/env python3
"""Read-only, paired cross-task audit of a running Qwen mathematical frontier.

Copies a *committed* checkpoint and its immutable seed into a separate report
folder. Evaluates exactly the same generated packets against frozen seed and
latest LoRA weights, classifying acquisition/retention/regressions per task.
No optimizer, checkpoint modifications, glass, or training loop.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
from types import SimpleNamespace

from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_frontier_tasks as tasks
from tools import nanojev_qwen_lm_radius_ladder_frontier as frontier

SCHEMA='nanojev.qwen.frontier.retention-audit.v1'


def _sha(path):
    digest=hashlib.sha256()
    with open(path,'rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def _emit(label, body):
    print(label+'='+json.dumps(body,sort_keys=True),flush=True)


def _json_atomic(path, data):
    path=Path(path)
    tmp=path.with_name(path.name+'.tmp')
    try:
        with open(tmp,'w',encoding='utf8') as file:
            json.dump(data,file,sort_keys=True,indent=2,ensure_ascii=False)
            file.write('\n');file.flush();os.fsync(file.fileno())
        os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)


def _append(path, item):
    with open(path,'a',encoding='utf8') as file:
        file.write(json.dumps(item,sort_keys=True,ensure_ascii=False)+'\n')


def _is_within(path, parent):
    path=Path(path).resolve(); parent=Path(parent).resolve()
    return path==parent or parent in path.parents


def _validate_paths(args):
    source=Path(args.frontier_output).resolve()
    if not source.is_dir():raise FileNotFoundError(source)
    if not args.stage1_checkpoint or not args.text_checkpoint:
        raise ValueError('RETENTION_PARENT_CHECKPOINTS_REQUIRED')
    for parent in (args.stage1_checkpoint,args.text_checkpoint):
        if not Path(parent).is_file():raise FileNotFoundError(parent)
    root=Path(args.report_root).resolve() if args.report_root else source.parent/(source.name+'_retention_audits')
    # Do not write *inside* any training/parent directory; do not write to
    # its ancestors either (which can overwrite through path mistakes).
    for unsafe in (source,Path(args.stage1_checkpoint).resolve().parent,
                   Path(args.text_checkpoint).resolve().parent):
        if _is_within(root,unsafe) or _is_within(unsafe,root):
            raise ValueError('RETENTION_REPORT_OVERLAPS_TRAINING_INPUT')
    if getattr(args,'cpu_threads',2)<1 or args.progress_every<1:
        raise ValueError('RETENTION_INVALID_EXECUTION_LIMITS')
    if args.fixed_cases<0 or args.novel_cases<0 or (args.fixed_cases+args.novel_cases)==0:
        raise ValueError('RETENTION_NEEDS_CASES')
    if args.max_new_tokens<8 or args.max_tokens<args.max_new_tokens or args.max_seq<32:
        raise ValueError('RETENTION_INVALID_TOKEN_LIMITS')
    return source,root


def _read_snapshot(path):
    import torch
    return torch.load(path,map_location='cpu',weights_only=True)


def _checkpoint_set(source,report_dir,*,compare_previous=False):
    """Only reads training files; all writes go to the independent report."""
    copied={}
    paths={'seed':source/'seed_snapshot.pt','latest':source/'working.pt'}
    if compare_previous:paths['previous']=source/'previous.pt'
    for role,path in paths.items():
        if not path.is_file():raise FileNotFoundError(path)
        dst=report_dir/'snapshots'/(role+'.pt')
        frontier._atomic_copy(path,dst)
        copied[role]={'path':dst,'sha256':_sha(dst),'checkpoint':_read_snapshot(dst)}
    seed=copied['seed']['checkpoint']
    seed_state=frontier._seed_meta(seed)
    latest=copied['latest']['checkpoint']
    if latest.get('schema')!=frontier.SCHEMA or not isinstance(latest.get('state'),dict):
        raise RuntimeError('RETENTION_LATEST_NOT_FRONTIER_CHECKPOINT')
    state=latest['state']
    if state.get('seed_sha256')!=copied['seed']['sha256']:
        raise RuntimeError('RETENTION_SEED_MISMATCH')
    if state.get('seed_state',{}).get('config_hash')!=seed_state['config_hash']:
        raise RuntimeError('RETENTION_SEED_CONFIG_MISMATCH')
    if state.get('status') not in {'TRAINING','COMPLETE','PAUSED','BUDGET_LIMIT','TASK_BUDGET_LIMIT'}:
        raise RuntimeError('RETENTION_BAD_CHECKPOINT_STATUS')
    if not isinstance(latest.get('lora'),dict) or not latest['lora']:
        raise RuntimeError('RETENTION_LATEST_MISSING_LORA')
    if 'previous' in copied:
        prev=copied['previous']['checkpoint']
        if prev.get('schema')!=frontier.SCHEMA or prev.get('state',{}).get('seed_sha256')!=copied['seed']['sha256']:
            raise RuntimeError('RETENTION_PREVIOUS_LINEAGE_MISMATCH')
        if prev['state']['step']>state['step']:
            raise RuntimeError('RETENTION_PREVIOUS_NEWER_THAN_LATEST')
    return copied, state, seed_state


def _cells(state, scope):
    policy=state['policy']
    order=tasks.task_order(policy['levels'])
    if scope=='all':return order
    last=int(state['task_index'])
    if state.get('status')=='COMPLETE':last=len(order)-1
    if last<0 or last>=len(order):
        raise RuntimeError('RETENTION_INVALID_TASK_CURSOR')
    return order[:last+1]


def _test_decks(state, scope, fixed_cases, novel_cases, novel_seed):
    policy=state['policy']
    rung=(int(policy['start_rung_override']) if policy['start_rung_override']>=0
          else max(0,int(state['seed_state']['rung'])))
    cells=_cells(state,scope)
    all_rows=[]
    for task,level in cells:
        # Fixed monitoring deck across audit runs; NEVER drawn from the
        # trainer's 200000+ training or 900000+ reference index ranges.
        for kind,n,seed,index_base in (
            ('fixed',fixed_cases,policy['seed'],3_000_000),
            ('novel',novel_cases,novel_seed,4_000_000),
        ):
            for i in range(n):
                row=tasks.make_case(task,level,index_base+i,seed=seed,
                    operand_boundary=policy['operand_boundary'],start_rung=rung)
                all_rows.append({'kind':kind,'case':row})
    return all_rows,cells


def _classify(before,after):
    if before and after:return 'retained'
    if before and not after:return 'regressed'
    if not before and after:return 'acquired'
    return 'still_wrong'


def _metric(rows):
    cnt=Counter(row['transition'] for row in rows)
    count=len(rows)
    return {'tested':count,'seed_correct':cnt['retained']+cnt['regressed'],
            'latest_correct':cnt['retained']+cnt['acquired'],
            'seed_accuracy':(cnt['retained']+cnt['regressed'])/count if count else None,
            'latest_accuracy':(cnt['retained']+cnt['acquired'])/count if count else None,
            'net_gained':cnt['acquired']-cnt['regressed'],
            'acquired':cnt['acquired'],'regressed':cnt['regressed'],
            'retained':cnt['retained'],'still_wrong':cnt['still_wrong']}


def _summaries(results):
    groups={}
    for row in results:
        for key in ('all',row['kind'],row['task'],f"{row['task']}:L{row['level']}",
                    f"{row['kind']}:{row['task']}:L{row['level']}"):
            groups.setdefault(key,[]).append(row)
    return {key:_metric(value) for key,value in sorted(groups.items())}



def _task_best_snapshots(source,report_dir,cells,latest_step):
    """Capture the strongest reference-loss checkpoint for each visited task.

    Those files contain LoRA weights only (not a full model). They are specific
    historical references for retention, NOT certificates of task mastery.
    """
    best={};missing=[]
    for task,level in cells:
        key=f'{task}_L{level}'
        parent=source/'task_checkpoints'/(key+'_best.pt')
        if not parent.is_file():
            missing.append(key);continue
        dst=report_dir/'snapshots'/('task_best_'+key+'.pt')
        frontier._atomic_copy(parent,dst)
        ck=_read_snapshot(dst)
        if (ck.get('schema')!=frontier.SCHEMA or ck.get('task')!=task or
            ck.get('level')!=level or not isinstance(ck.get('lora'),dict) or
            ck.get('step',-1)<0 or ck.get('step')>latest_step):
            raise RuntimeError('RETENTION_TASK_BEST_INCOMPATIBLE:'+key)
        best[(task,level)]={'path':dst,'checkpoint':ck,'sha256':_sha(dst),
                            'reference_loss':ck.get('reference_loss'),
                            'step':ck['step']}
    return best,missing


def audit(args):
    source,root=_validate_paths(args)
    # Creating the directory is the ONLY filesystem side effect outside
    # model inference; refuse existing report output rather than overwrite.
    root.mkdir(parents=True,exist_ok=True)
    name=datetime.now(timezone.utc).strftime('audit_%Y%m%dT%H%M%S_%fZ_')+secrets.token_hex(3)
    report_dir=root/name
    report_dir.mkdir(exist_ok=False)
    (report_dir/'snapshots').mkdir()
    copied,state,seed_state=_checkpoint_set(source,report_dir,compare_previous=args.compare_previous)
    novel_seed=args.novel_seed if args.novel_seed is not None else secrets.randbits(63)
    rows,cells=_test_decks(state,args.scope,args.fixed_cases,args.novel_cases,novel_seed)
    task_best,missing_best=(_task_best_snapshots(source,report_dir,cells,state['step'])
                             if args.compare_task_best else ({},[]))
    for data in rows:
        _append(report_dir/'cases.jsonl',data)
    labels=list(copied)
    metadata={'schema':SCHEMA,'status':'RUNNING','frontier_checkpoint_step':state['step'],
              'frontier_task_index':state['task_index'],'frontier_status':state['status'],
              'frontier_seed_sha256':state['seed_sha256'],
              'source_parent_hashes':state['seed_state'],
              'device':args.device,'scope':args.scope,
              'numeric_terminal_preflight':tasks.terminal_preflight(),
              'pending_check_at_snapshot':bool(state.get('pending_check')), 
              'cells':[{'task':t,'level':l} for t,l in cells],
              'fixed_cases_per_cell':args.fixed_cases,'novel_cases_per_cell':args.novel_cases,
              'novel_seed':novel_seed,'total_unique_case_ids':len(rows),
              'snapshots':{k:{'sha256':v['sha256'],
                             'step':v['checkpoint'].get('state',{}).get('step')}
                           for k,v in copied.items()},
              'report_path':str(report_dir),
              'task_best_checkpoints':{f'{k[0]}:L{k[1]}':{'step':v['step'],
                  'sha256':v['sha256'],'reference_loss':v['reference_loss']}
                  for k,v in task_best.items()},'missing_task_best':missing_best}
    _json_atomic(report_dir/'report.json',metadata)
    _emit('QWEN_FRONTIER_AUDIT_START',{'report':str(report_dir),'step':state['step'],
                                      'cases':len(rows),'cells':len(cells),'device':args.device})
    if args.device=='cpu':
        import torch
        torch.set_num_threads(max(1,int(getattr(args,'cpu_threads',2))))
    # Parent-load call uses the checkpoint's frozen model source SHA and normal
    # Qwen config, but no optimizer and no training. CPU by default so that
    # the concurrent CUDA training loop retains its GPU and CUDA memory.
    parent_args=SimpleNamespace(stage1_checkpoint=args.stage1_checkpoint,
        text_checkpoint=args.text_checkpoint,device=args.device,
        baseline=None,backbone=None,activation=None,format=None,
        allow_model_download=args.allow_model_download)
    bb,tok,sources,frozen,dirs,zero=ladder._fresh_parent(parent_args)
    if sources!=seed_state['source_sha256'] or frozen!=seed_state['frozen_sha256']:
        raise RuntimeError('RETENTION_PARENT_HASH_MISMATCH')
    bb.eval();bb.requires_grad_(False)
    results=[]
    def one_case(bb,tok,case):
        gargs=SimpleNamespace(device=args.device,max_tokens=args.max_tokens,
                              max_new_tokens=args.max_new_tokens)
        return frontier._greedy_one(bb,tok,case,gargs)
    # Evaluate *all* examples with each snapshot. No model or snapshot writes.
    predictions={}
    for label in labels:
        zero.restore_lora(bb,copied[label]['checkpoint']['lora'])
        bb.eval();bb.requires_grad_(False)
        raw=[]
        for i,data in enumerate(rows):
            prediction=one_case(bb,tok,data['case'])
            correct,parsed=ladder.correctness(data['case']['source'],data['case']['target'],prediction)
            raw.append({'correct':bool(correct),'prediction':prediction,'parsed':parsed})
            if (i+1)%max(1,args.progress_every)==0 or i+1==len(rows):
                _emit('QWEN_FRONTIER_AUDIT_PROGRESS',{'snapshot':label,'tested':i+1,
                    'total':len(rows),'correct_so_far':sum(p['correct'] for p in raw)})
        predictions[label]=raw
    # Historical task-specific snapshots make it possible to measure whether
    # subsequently trained families have damaged a previously acquired skill.
    task_best_results={}
    for (task,level),best in task_best.items():
        zero.restore_lora(bb,best['checkpoint']['lora'])
        bb.eval();bb.requires_grad_(False)
        sampled=[(i,data) for i,data in enumerate(rows)
                 if data['case']['task']==task and data['case']['level']==level]
        for i,data in sampled:
            got=one_case(bb,tok,data['case'])
            correct,parsed=ladder.correctness(data['case']['source'],data['case']['target'],got)
            task_best_results[i]={'correct':bool(correct),'prediction':got,'parsed':parsed}
        _emit('QWEN_FRONTIER_AUDIT_TASK_BEST',{'task':task,'level':level,
            'tested':len(sampled),'checkpoint_step':best['step']})
    for i,data in enumerate(rows):
        case=data['case'];comparison={k:predictions[k][i] for k in labels}
        item={'kind':data['kind'],'task':case['task'],'level':case['level'],
              'case_id':case['id'],'source':case['source'],'target':case['target'],
              'terminal_kind':case['terminal_kind'],'case_metadata':case['metadata'],
              'outputs':comparison,'transition':_classify(comparison['seed']['correct'],
                                                          comparison['latest']['correct'])}
        if 'previous' in comparison:
            item['previous_to_latest']=_classify(comparison['previous']['correct'],
                                                comparison['latest']['correct'])
        if i in task_best_results:
            item['task_best_reference']=task_best_results[i]
            item['task_best_to_latest']=_classify(task_best_results[i]['correct'],
                                                 comparison['latest']['correct'])
        _append(report_dir/'results.jsonl',item)
        results.append(item)
    summary=_summaries(results)
    metadata.update({'status':'COMPLETE','summary':summary,'snapshot_accuracy':{
        label:sum(item['outputs'][label]['correct'] for item in results)/len(results)
        for label in labels},
        'grading':'exact greedy next-PROBLEM-packet string; not semantic equivalence or end-to-end HybridLink Z',
        'certification':'DIAGNOSTIC_ONLY_NON_EXHAUSTIVE'})
    if 'previous' in predictions:
        metadata['previous_to_latest']=_metric([{'transition':r['previous_to_latest']} for r in results])
    historical=[r for r in results if 'task_best_to_latest' in r]
    metadata['task_best_to_latest']={
        'overall':_metric([{'transition':r['task_best_to_latest']} for r in historical]),
        'by_task_level':{f'{task}:L{level}':_metric([
          {'transition':r['task_best_to_latest']} for r in historical
          if r['task']==task and r['level']==level]) for task,level in task_best}}
    _json_atomic(report_dir/'report.json',metadata)
    _emit('QWEN_FRONTIER_AUDIT_DONE',{'report':str(report_dir),'overall':summary['all'],
                                     'novel':summary.get('novel'),
                                     'fixed':summary.get('fixed')})
    return metadata


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--frontier-output',required=True,help='Active frontier run (never written)')
    p.add_argument('--stage1-checkpoint',required=True)
    p.add_argument('--text-checkpoint',required=True)
    p.add_argument('--report-root',help='Separate report directory; default sibling of frontier output')
    p.add_argument('--device',choices=('cpu','cuda'),default='cpu',
                   help='CPU by default to avoid competing with ongoing CUDA training')
    p.add_argument('--scope',choices=('visited','all'),default='visited')
    p.add_argument('--cpu-threads',type=int,default=2,help='Limit CPU inference thread contention (default 2)')
    p.add_argument('--fixed-cases',type=int,default=8,help='Stable cross-run comparison cases per task-level')
    p.add_argument('--novel-cases',type=int,default=8,help='Fresh seeded problems per task-level')
    p.add_argument('--novel-seed',type=int,help='Omit to generate fresh novel problems each audit')
    p.add_argument('--max-tokens',type=int,default=256)
    p.add_argument('--max-seq',type=int,default=192)
    p.add_argument('--max-new-tokens',type=int,default=64)
    p.add_argument('--progress-every',type=int,default=8)
    p.add_argument('--compare-previous',action='store_true',help='Also compare prior committed working snapshot')
    p.add_argument('--no-task-best',dest='compare_task_best',action='store_false',
                   help='Skip each task historical best-loss snapshot (less inference)')
    p.set_defaults(compare_task_best=True)
    p.add_argument('--allow-model-download',action='store_true')
    return audit(p.parse_args(argv))

if __name__=='__main__':main()
