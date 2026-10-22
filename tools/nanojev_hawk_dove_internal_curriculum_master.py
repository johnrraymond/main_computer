#!/usr/bin/env python3
"""Isolated NanoJev internal-bridge curriculum A/B with retained best checkpoints.

Starts from a frozen, hash-checked checkpoint of the prior internal A/B run.
For each architecture (bank/continuous), clones the *same* starting state into
control and curriculum arms. All four arms use the same frozen Qwen/donor,
training population, optimizer and numerical objective, except for the
explicitly selected curriculum interventions. No runtime Hawk-Dove oracle.

Training never opens the test split. --test-only evaluates a previously saved
best checkpoint on held-out test examples and does not train or select a model.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import random
from pathlib import Path

import torch
from torch.nn import functional as F

from tools import nanojev_hawk_dove_gaussian_internal_bank_ab as base
from tools import nanojev_hawk_dove_gaussian_register_fork as fork
from tools import nanojev_hawk_dove_gaussian_correction_ab as iohelp

SCHEMA='nanojev.hawk-dove.internal-curriculum-master.v1'
DEFAULT_PARENT=base.DEFAULT_OUTPUT/'best_checkpoints'/'best_all_nine_continuous.pt'
DEFAULT_OUTPUT=Path.home()/'NanoJev/runs/hawk_dove_internal_curriculum_master_v1'
KINDS=('bank','continuous')
ARMS=('bank_control','bank_curriculum','continuous_control','continuous_curriculum')
TIERS=base.TIERS


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def checked_file(path:Path):
    before=path.stat()
    raw=path.read_bytes()
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise ValueError('MASTER_INPUT_CHANGED_DURING_READ:'+str(path))
    return raw


def load_state(raw:bytes):
    return torch.load(io.BytesIO(raw),map_location='cpu',weights_only=True)


def validate_options(args):
    if (args.additional_steps<0 or args.chunk_steps<1 or args.views<9 or
        args.train_per_tier<8 or args.dev_per_tier<8 or args.test_per_tier<8 or
        args.batch_size<1 or not math.isfinite(args.lr) or args.lr<=0 or
        not 0<args.gate_percent<=100 or
        not 0<args.small_threshold<fork.MAX_ABS or
        not math.isfinite(args.small_x_boost) or args.small_x_boost<1 or
        not math.isfinite(args.consistency_weight) or args.consistency_weight<0 or
        not math.isfinite(args.gaussian_weight) or args.gaussian_weight<0 or
        not math.isfinite(args.magnitude_weight) or args.magnitude_weight<=0 or
        not math.isfinite(args.sign_weight) or args.sign_weight<0 or
        not math.isfinite(args.y_stability_weight) or args.y_stability_weight<0 or
        not math.isfinite(args.feedback_weight) or args.feedback_weight<0):
        raise ValueError('MASTER_INVALID_OPTIONS')
    out=args.output.expanduser().resolve()
    protected=(args.parent.expanduser().resolve().parent,
               args.source.expanduser().resolve().parent,
               args.teacher.expanduser().resolve().parent,
               args.parent.expanduser().resolve().parent.parent)
    if not all(iohelp.disjoint(out,p) for p in protected):
        raise ValueError('MASTER_OUTPUT_OVERLAPS_INPUT')
    return out


def parent_input(args,out):
    """First call validates live best checkpoint; resumes use frozen local copy."""
    parent_copy=out/'parent_snapshot.pt'
    donor_copy=out/'donor_snapshot.pt'
    cfg_path=out/'config.json'
    latest=out/'latest.pt'
    found=[p.exists() for p in (parent_copy,donor_copy,cfg_path,latest)]
    if any(found) and not all(found):
        raise ValueError('MASTER_PARTIAL_RUN_STATE')
    if all(found):
        return checked_file(parent_copy),checked_file(donor_copy),True
    parent_raw=checked_file(args.parent.expanduser().resolve())
    parent=load_state(parent_raw)
    meta_path=args.parent.with_suffix('.json')
    if meta_path.exists():
        meta=json.loads(meta_path.read_text(encoding='utf-8'))
        if (meta['checkpoint_sha256']!=sha(parent_raw) or
            int(meta['step'])!=int(parent['step']) or
            meta['arm'] not in KINDS):
            raise ValueError('MASTER_PARENT_BEST_HASH_OR_STEP_MISMATCH')
    if parent.get('schema')!=base.SCHEMA:
        raise ValueError('MASTER_BAD_PARENT_SCHEMA')
    origin=args.parent.expanduser().resolve().parent.parent/'donor_snapshot.pt'
    donor_raw=checked_file(origin)
    if sha(donor_raw)!=parent['identity']['donor_snapshot_sha256']:
        raise ValueError('MASTER_PARENT_DONOR_HASH_MISMATCH')
    return parent_raw,donor_raw,False


def configuration(args,parent_raw,donor_raw):
    parent=load_state(parent_raw)
    donor=load_state(donor_raw)
    if (parent.get('schema')!=base.SCHEMA or
        donor.get('schema')!=fork.SCHEMA or
        parent['identity']['donor_snapshot_sha256']!=sha(donor_raw) or
        parent['identity']['origin_fork_step']!=int(donor['fork_step']) or
        parent['identity']['source_sha256']!=fork.fingerprint(args.source) or
        parent['identity']['teacher_sha256']!=fork.fingerprint(args.teacher)):
        raise ValueError('MASTER_SOURCE_PARENT_PROVENANCE_DRIFT')
    for kind in KINDS:
        if kind not in parent['heads'] or kind not in parent['optimizers']:
            raise ValueError('MASTER_PARENT_ARM_MISSING:'+kind)
    ident={'schema':SCHEMA,'parent_path':str(args.parent.expanduser().resolve()),
           'parent_sha256':sha(parent_raw),'parent_step':int(parent['step']),
           'donor_sha256':sha(donor_raw),'donor_identity':parent['identity'],
           'source':str(args.source.expanduser().resolve()),
           'teacher':str(args.teacher.expanduser().resolve()),
           'seed':args.seed,'train_per_tier':args.train_per_tier,
           'dev_per_tier':args.dev_per_tier,'test_per_tier':args.test_per_tier,
           'batch_size':args.batch_size,'views':args.views,
           'gate_percent':args.gate_percent,'small_threshold':args.small_threshold,
           'small_x_boost':args.small_x_boost,
           'consistency_weight':args.consistency_weight,
           'gaussian_weight':args.gaussian_weight,
           'magnitude_weight':args.magnitude_weight,
           'sign_weight':args.sign_weight,
           'y_stability_weight':args.y_stability_weight,
           'feedback_weight':args.feedback_weight,'lr':args.lr,
           'sampler':'balanced_tier_uniform_control_vs_small_x_weighted_curriculum',
           'consistency':'paired_train_same_instance_cross_tier_preferred',
           'test_selection':'test_split_is_not_read_during_train'}
    return ident


def rows_metadata(teacher,train_n,seed):
    """Never mix splits; pairs require same true X,Y and distinct presentations."""
    return {t:fork.science.rows_for_probe(teacher,'train',t,train_n,seed) for t in TIERS}


def build_pair_index(rows):
    by_instance={}
    for i,r in enumerate(rows):
        key=(str(r['instance']),float(r['x']),float(r['y']))
        by_instance.setdefault(key,[]).append(i)
    result={}
    for indexes in by_instance.values():
        for i in indexes:
            result[i]=[j for j in indexes if j!=i and rows[j]['prompt']!=rows[i]['prompt']]
    return result


def build_cross_tier_pairs(metadata):
    """Match equal training instances across masks AND different tier grammars.

    Matching requires exact underlying numeric targets, not just an instance ID.
    A pair is never drawn from dev or test and must change the actual prompt.
    """
    groups={}
    for tier,rows in metadata.items():
        for idx,row in enumerate(rows):
            key=(str(row['instance']),float(row['x']),float(row['y']))
            groups.setdefault(key,[]).append((tier,idx,row['prompt']))
    result={tier:{} for tier in TIERS}
    for group in groups.values():
        for tier,idx,prompt in group:
            alternatives=[(t,i) for t,i,p in group if p!=prompt]
            # Prioritize a different grammar; same-tier LogBar variants serve
            # as fallback when the selected instance is absent on other tiers.
            cross=[x for x in alternatives if x[0]!=tier]
            result[tier][idx]=cross if cross else alternatives
    return result


def choices_for_tier(data,threshold):
    x=data['target_x'].detach().cpu()
    return tuple(i for i,v in enumerate(x.tolist()) if abs(v)<threshold)


def pick_rows(train,meta,pairs,step,views,seed,boost,threshold):
    """Two deterministic schedules: uniform and weighted within identical tiers.

    Every consecutive 9 views contains each tier exactly once. Each pair uses
    matching ground-truth X/Y, and cannot cross a train/dev/test split.
    """
    result={}
    for mode in ('control','curriculum'):
        selected=[];partners=[]
        for j in range(views):
            tier=TIERS[(step*views+j)%len(TIERS)]
            records=train[tier]
            count=len(records['target_x'])
            rng=random.Random(f'master/{seed}/{step}/{j}/{tier}')
            if mode=='curriculum' and boost>1:
                small=choices_for_tier(records,threshold)
                large=tuple(i for i in range(count) if i not in set(small))
                # Sampling probabilities exactly proportional to boost for small
                # rows, and 1 for the rest, with no rejection-loop biases.
                cut=boost*len(small)
                if small and rng.random()*(cut+len(large))<cut:
                    idx=small[rng.randrange(len(small))]
                elif large:
                    idx=large[rng.randrange(len(large))]
                else:
                    idx=small[rng.randrange(len(small))]
            else:
                idx=rng.randrange(count)
            p=pairs[tier].get(idx,())
            partner=p[rng.randrange(len(p))] if p else (tier,idx)
            selected.append((tier,idx));partners.append(partner)
        result[mode]=(selected,partners)
    return result


def make_sample(train,indexes):
    return {key:torch.stack([train[t][key][idx] for t,idx in indexes],dim=0)
            for key in train[TIERS[0]]}


def concat_samples(a,b):
    return {key:torch.cat((a[key],b[key]),dim=0) for key in a}


def target_gaussian_loss(decoder,out,truth):
    """Optional auxiliary Gaussian pressure on frozen *output* magnitude bank.

    The internal register bank has no known numeric intermediate target. We
    never invent one, and this pressure applies equally to both architectures.
    """
    g=torch.log10(1+truth.abs())
    centers=decoder.centers.float().view(1,-1)
    sigma=float(decoder.sigma)
    q=F.softmax(-.5*((centers-g[:,None])/sigma).square(),dim=-1)
    return F.kl_div(F.log_softmax(out['bank_logits'][:,0,:],dim=-1),q,reduction='batchmean')


def train_loss(head,decoder,data,paired,*, consistency_weight,
               gaussian_weight,magnitude_weight,sign_weight,
               y_stability_weight,feedback_weight):
    all_data=concat_samples(data,paired)
    _x,_y,out,latent=base.predict(head,decoder,all_data)
    target=all_data['target_x']
    primary=F.smooth_l1_loss(out['log_magnitude'][:,0],torch.log10(1+target.abs()),beta=.05)
    labels=torch.where(target<0,0,torch.where(target>0,2,1)).long()
    sign=F.cross_entropy(out['sign'][:,0],labels)
    y_stability=F.smooth_l1_loss(out['log_magnitude'][:,1],all_data['original_g'][:,1],beta=.05)
    feedback=latent['feedback'].square().mean()
    n=data['target_x'].numel()
    consistent=F.smooth_l1_loss(out['log_magnitude'][:n,0],out['log_magnitude'][n:,0],beta=.05)
    gauss=target_gaussian_loss(decoder,out,target) if gaussian_weight else primary.new_zeros(())
    return (magnitude_weight*primary+sign_weight*sign+y_stability_weight*y_stability+
            feedback_weight*feedback+consistency_weight*consistent+gaussian_weight*gauss)


def arm_report(arms,decoder,dev,gate,step):
    info={'step':step,'gate_percent':gate,'arms':{}}
    for name,head in arms.items():
        head.eval()
        scores={t:base.assess(head,decoder,dev[t],gate) for t in TIERS}
        fails=[t for t in TIERS if not scores[t]['passes_gate']]
        info['arms'][name]={'scores':scores,'failing_tiers':fails,
                            'all_nine_pass':not fails,'first_failing':fails[0] if fails else None,
                            'macro_accuracy_5pct':sum(v['accuracy_5pct'] for v in scores.values())/len(TIERS)}
    return info


def score_for_best(arm,gate):
    scores=arm['scores']
    if set(scores)!=set(TIERS):raise ValueError('MASTER_BAD_SCORE_TIERS')
    ratios=[];total=0
    for tier in TIERS:
        s=scores[tier]
        n=int(s['cases']);k=int(s['correct'])
        if n<1 or not 0<=k<=n or abs(float(s['accuracy_5pct'])-k/n)>1e-6:
            raise ValueError('MASTER_INVALID_TIER_SCORE:'+tier)
        ratios.append(k/n);total+=k
    macro=sum(ratios)/len(TIERS)
    if abs(macro-arm['macro_accuracy_5pct'])>1e-6 or bool(arm['all_nine_pass'])!=all(x>=gate/100 for x in ratios):
        raise ValueError('MASTER_INVALID_OVERALL_SCORE')
    return {'macro':macro,'minimum':min(ratios),'all_nine':all(x>=gate/100 for x in ratios),
            'total_correct':total,'per_tier_correct':{t:int(scores[t]['correct']) for t in TIERS}}


def save_best(output,checkpoint,report,raw=None):
    """Save selected full optimizer checkpoint synchronously after each probe."""
    scores={name:score_for_best(arm,report['gate_percent']) for name,arm in report['arms'].items()}
    if raw is None:
        stream=io.BytesIO();torch.save(checkpoint,stream);raw=stream.getvalue()
    root=output/'best_checkpoints';updates=[]
    for name in ARMS:
        for metric in ('macro','all_nine'):
            value=scores[name]
            if metric=='all_nine' and not value['all_nine']:continue
            pt=root/f'best_{metric}_{name}.pt'
            md=pt.with_suffix('.json')
            old=None
            if pt.exists()!=md.exists():raise ValueError('MASTER_INCOMPLETE_BEST:'+name)
            if md.exists():
                old=json.loads(md.read_text(encoding='utf-8'))
                if old['source_parent_sha256']!=checkpoint['identity']['parent_sha256'] or sha(checked_file(pt))!=old['checkpoint_sha256']:
                    raise ValueError('MASTER_PREVIOUS_BEST_PROVENANCE_DRIFT:'+name)
                old_metric=old['selection']
                first=(value['macro'],value['minimum']) if metric=='macro' else (value['minimum'],value['macro'])
                second=(old_metric['macro'],old_metric['minimum']) if metric=='macro' else (old_metric['minimum'],old_metric['macro'])
                if first<=second:continue
            meta={'schema':SCHEMA+'.best.v1','arm':name,'metric':metric,
                  'step':checkpoint['step'],'selection':value,
                  'source_parent_sha256':checkpoint['identity']['parent_sha256'],
                  'checkpoint_sha256':sha(raw),'contains_all_arms_and_optimizers':True}
            iohelp.atomic_bytes(pt,raw)
            iohelp.atom_json(md,meta)
            updates.append({'arm':name,'metric':metric,'step':checkpoint['step'],
                            'macro_accuracy_5pct':value['macro'],'minimum_accuracy_5pct':value['minimum']})
    return updates


def init_heads(parent,width,device):
    prev=parent['identity']
    arms={}
    for kind in KINDS:
        for mode in ('control','curriculum'):
            key=f'{kind}_{mode}'
            head=base.IntermediateBridge(width,int(prev['registers']),kind=kind,
                         limit=float(prev['limit']),sigma_bins=float(prev['sigma_bins']),
                         feedback_scale=float(prev['feedback_scale'])).to(device)
            head.load_state_dict(parent['heads'][kind],strict=True)
            arms[key]=head
    if len({sum(p.numel() for p in h.parameters()) for h in arms.values()})!=1:
        raise ValueError('MASTER_NOT_PARAMETER_MATCHED')
    return arms


def all_optimizers(arms,parent,lr):
    opts={}
    for name,head in arms.items():
        kind=name.split('_',1)[0]
        opt=torch.optim.AdamW(head.parameters(),lr=lr)
        # Start each pair from the corresponding exact saved optimizer state.
        opt.load_state_dict(parent['optimizers'][kind])
        for g in opt.param_groups:g['lr']=lr
        opts[name]=opt
    return opts


def data_for(model,decoder,cache,args,split,collector):
    if split=='train':raise ValueError('MASTER_USE_PRECOMPUTE_FOR_TRAIN')
    result={}
    n=args.dev_per_tier if split=='dev' else args.test_per_tier
    for tier in TIERS:
        rows=fork.science.rows_for_probe(args.teacher,split,tier,n,args.seed)
        result[tier]=base.capture_rows(model,collector,cache,rows,base.DEV_BATCH,model['device'])
        fork.emit('master_features_ready',split=split,tier=tier,cases=len(rows))
    return result


def run(args):
    output=validate_options(args)
    parent_raw,donor_raw,resume=parent_input(args,output)
    ident=configuration(args,parent_raw,donor_raw)
    parent=load_state(parent_raw)
    donor=load_state(donor_raw)
    if resume:
        if json.loads((output/'config.json').read_text(encoding='utf-8'))!=ident:
            raise ValueError('MASTER_RESUME_CONFIG_DRIFT')
        saved=torch.load(output/'latest.pt',map_location='cpu',weights_only=True)
        if saved.get('schema')!=SCHEMA or saved.get('identity')!=ident:
            raise ValueError('MASTER_RESUME_CHECKPOINT_DRIFT')
    else:
        saved=None
    fork.emit('master_prepare',parent_step=ident['parent_step'],
              experiment_step=int(saved['step']) if saved else 0,
              parent_sha256=ident['parent_sha256'],parent_read_only=True,
              live_training_untouched=True,test_data_used_for_training=False)
    torch.manual_seed(args.seed)
    model=fork.ex.load_current(args.source,args.device,args.dtype,args.allow_model_download,args.seed)
    model['decoder']=fork.build_for_resume(model,donor,
                    parent['identity']['parent_registers'],parent['identity']['parent_sigma_steps'])
    decoder=model['decoder']
    decoder.eval().requires_grad_(False)
    cache=fork.cached.FrozenCaptureCache(output/'frozen-capture-cache-v1',{
           'schema':SCHEMA+'.capture-cache','source_sha256':ident['donor_identity']['source_sha256'],
           'teacher_sha256':ident['donor_identity']['teacher_sha256'],
           'capture_max_tokens':model['config']['max_prompt_tokens']},args.cache_memory_mb)
    collector=base.FrozenPreBody(decoder)
    try:
        if args.test_only:
            if not resume:raise ValueError('MASTER_TEST_NEEDS_EXISTING_EXPERIMENT')
            best_path=(output/'best_checkpoints'/f'best_{args.best_metric}_{args.best_arm}.pt')
            meta=json.loads(best_path.with_suffix('.json').read_text(encoding='utf-8'))
            raw=checked_file(best_path)
            if sha(raw)!=meta['checkpoint_sha256'] or meta['arm']!=args.best_arm:
                raise ValueError('MASTER_TEST_BEST_CHECKPOINT_MISMATCH')
            chosen=load_state(raw)
            if chosen['identity']!=ident or chosen['schema']!=SCHEMA:
                raise ValueError('MASTER_TEST_BEST_IDENTITY_DRIFT')
            test=data_for(model,decoder,cache,args,'test',collector)
            width=test[TIERS[0]]['features'].shape[-1]
            arms=init_heads(parent,width,model['device'])
            for name,head in arms.items():head.load_state_dict(chosen['heads'][name],strict=True)
            result=arm_report(arms,decoder,test,args.gate_percent,chosen['step'])
            result['schema']=SCHEMA+'.untouched-test.v1'
            result['checkpoint_sha256']=sha(raw)
            result['best_arm']=args.best_arm
            result['best_metric']=args.best_metric
            iohelp.atom_json(output/f'test-{args.best_metric}-{args.best_arm}-step-{chosen["step"]:07d}.json',result)
            fork.emit('master_test_finished',checkpoint_step=chosen['step'],
                      tested_arm=args.best_arm,accuracy=result['arms'][args.best_arm]['macro_accuracy_5pct'])
            return
        train,dev=base.precompute(model,collector,cache,args.teacher,args.train_per_tier,
                                  args.dev_per_tier,args.seed,args.batch_size,model['device'])
        base.validate_baseline(model,cache,dev,args.teacher,args.gate_percent,args.seed,args.dev_per_tier)
    finally:
        collector.close()
    width=train[TIERS[0]]['features'].shape[-1]
    arms=init_heads(parent,width,model['device'])
    opts=all_optimizers(arms,parent,args.lr)
    if saved is not None:
        for name,head in arms.items():
            head.load_state_dict(saved['heads'][name],strict=True)
            opts[name].load_state_dict(saved['optimizers'][name])
    else:
        # Same checkpoint for control and curriculum within each architecture.
        for kind in KINDS:
            control=arms[kind+'_control'];curriculum=arms[kind+'_curriculum']
            for x,y in zip(control.parameters(),curriculum.parameters()):
                if not torch.equal(x,y):raise ValueError('MASTER_ARM_NOT_IDENTICAL_AT_START')
        output.mkdir(parents=True,exist_ok=True)
        iohelp.atomic_bytes(output/'parent_snapshot.pt',parent_raw)
        iohelp.atomic_bytes(output/'donor_snapshot.pt',donor_raw)
        iohelp.atom_json(output/'config.json',ident)
    meta=rows_metadata(args.teacher,args.train_per_tier,args.seed)
    if any(len(meta[t])!=len(train[t]['target_x']) for t in TIERS):
        raise ValueError('MASTER_TRAIN_ROWS_LENGTH_DRIFT')
    pairs=build_cross_tier_pairs(meta)
    coverage={t:sum(bool(p) for p in pairs[t].values()) for t in TIERS}
    fork.emit('master_equivalent_presentation_coverage',**coverage)
    if args.consistency_weight and any(v==0 for v in coverage.values()):
        raise ValueError('MASTER_NO_CONSISTENCY_PAIRS_IN_ONE_OR_MORE_TIERS')
    start=int(saved['step']) if saved else 0
    if saved:
        torch.set_rng_state(saved['rng_cpu'])
        if args.device=='cuda' and saved.get('rng_cuda') is not None:
            torch.cuda.set_rng_state_all(saved['rng_cuda'])
    def checkpoint(step):
        return {'schema':SCHEMA,'identity':ident,'step':step,
                'heads':{k:h.state_dict() for k,h in arms.items()},
                'optimizers':{k:o.state_dict() for k,o in opts.items()},
                'rng_cpu':torch.get_rng_state(),
                'rng_cuda':torch.cuda.get_rng_state_all() if args.device=='cuda' else None}
    if saved is None:
        ck=checkpoint(0)
        iohelp.atom_torch(output/'latest.pt',ck)
        initial=arm_report(arms,decoder,dev,args.gate_percent,0)
        iohelp.atom_json(output/'probe-step-0000000.json',initial)
        for event in save_best(output,ck,initial):fork.emit('master_best_saved',**event)
    for step in range(start+1,start+args.additional_steps+1):
        selection=pick_rows(train,meta,pairs,step,args.views,args.seed,args.small_x_boost,args.small_threshold)
        for name,head in arms.items():
            mode=name.split('_',1)[1]
            indexes,other=selection[mode]
            samples=make_sample(train,indexes)
            paired=make_sample(train,other)
            optimizer=opts[name]
            head.train();optimizer.zero_grad(set_to_none=True)
            loss=train_loss(head,decoder,samples,paired,
                consistency_weight=args.consistency_weight if mode=='curriculum' else 0.,
                gaussian_weight=args.gaussian_weight if mode=='curriculum' else 0.,
                magnitude_weight=args.magnitude_weight,sign_weight=args.sign_weight,
                y_stability_weight=args.y_stability_weight,
                feedback_weight=args.feedback_weight)
            if not bool(torch.isfinite(loss)):
                raise FloatingPointError('MASTER_NONFINITE_TRAINING_LOSS:'+name)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(head.parameters(),1.)
            optimizer.step()
        if step%args.chunk_steps==0 or step==start+args.additional_steps:
            info=arm_report(arms,decoder,dev,args.gate_percent,step)
            ck=checkpoint(step)
            iohelp.atom_torch(output/'latest.pt',ck)
            iohelp.atom_json(output/f'probe-step-{step:07d}.json',info)
            for event in save_best(output,ck,info):fork.emit('master_best_saved',**event)
            fork.emit('master_probe',step=step,
                bank_control=info['arms']['bank_control']['macro_accuracy_5pct'],
                bank_curriculum=info['arms']['bank_curriculum']['macro_accuracy_5pct'],
                continuous_control=info['arms']['continuous_control']['macro_accuracy_5pct'],
                continuous_curriculum=info['arms']['continuous_curriculum']['macro_accuracy_5pct'],
                continuous_curriculum_failing=info['arms']['continuous_curriculum']['failing_tiers'])
    if sha(checked_file(output/'parent_snapshot.pt'))!=ident['parent_sha256'] or sha(checked_file(output/'donor_snapshot.pt'))!=ident['donor_sha256']:
        raise ValueError('MASTER_FROZEN_SNAPSHOTS_CHANGED')
    fork.emit('master_finished',step=start+args.additional_steps,
              parent_checkpoint_immutable=True,best_checkpoints=str(output/'best_checkpoints'))


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent',type=Path,default=DEFAULT_PARENT)
    p.add_argument('--source',type=Path,default=fork.science.DEFAULT_SOURCE)
    p.add_argument('--teacher',type=Path,default=fork.science.DEFAULT_TEACHER)
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--dtype',choices=('float32','float16','bfloat16'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--additional-steps',type=int,default=3000)
    p.add_argument('--chunk-steps',type=int,default=300)
    p.add_argument('--views',type=int,default=9,help='At least one example of each tier per update')
    p.add_argument('--train-per-tier',type=int,default=256)
    p.add_argument('--dev-per-tier',type=int,default=48)
    p.add_argument('--test-per-tier',type=int,default=48)
    p.add_argument('--batch-size',type=int,default=8)
    p.add_argument('--gate-percent',type=float,default=40.)
    p.add_argument('--small-threshold',type=float,default=250000.)
    p.add_argument('--small-x-boost',type=float,default=4.)
    p.add_argument('--consistency-weight',type=float,default=.1)
    p.add_argument('--gaussian-weight',type=float,default=0.,help='Output Gaussian pressure; optional ablation')
    p.add_argument('--magnitude-weight',type=float,default=1.)
    p.add_argument('--sign-weight',type=float,default=.05)
    p.add_argument('--y-stability-weight',type=float,default=.1)
    p.add_argument('--feedback-weight',type=float,default=.0001)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--seed',type=int,default=20261018)
    p.add_argument('--cache-memory-mb',type=int,default=1024)
    p.add_argument('--test-only',action='store_true')
    p.add_argument('--best-metric',choices=('macro','all_nine'),default='all_nine')
    p.add_argument('--best-arm',choices=ARMS,default='continuous_curriculum')
    return p


def main(argv=None):
    run(parser().parse_args(argv))


if __name__=='__main__':main()
