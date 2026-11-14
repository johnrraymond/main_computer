#!/usr/bin/env python3
"""Six SEQUENTIAL learned Gaussian refiners following the existing two-bank head.

Stage 1 is the original frozen Gaussian-register readout. Stage 2 is the
previously trained local Gaussian refiner, frozen and verified from its best
checkpoint. Stages 3..8 form an ordered cascade: each predicts a *local signed
residual* around the last stage's own prediction using the original frozen
hidden features, the preceding stage's Gaussian posterior and predicted state.

Stages train progressively, one at a time, from stage 3 to stage 8. Later
stages start at exact identity, and each trained stage is frozen before the next
stage starts. No target X or Y is supplied to any forward path. All six newly
learned banks remain accessible as intermediate predictions in the report.

Important experimental limitation: the frozen original Hawk-Dove test fixture
still exposes Y upstream. This head does not itself consume Y as an answer.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from pathlib import Path

import torch
from torch import nn

from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine
from tools import nanojev_hawk_dove_gaussian_register_fork as fork
from tools import nanojev_hawk_dove_gaussian_correction_ab as store
from tools import nanojev_hawk_dove_internal_curriculum_master as master

SCHEMA='nanojev.hawk-dove.gaussian-refinement-stack.v1'
STAGES=tuple(range(3,9))
BASE_STAGE=2
DEFAULT_PARENT=fine.DEFAULT_OUTPUT/'best_checkpoints'/'best_small_gaussian.pt'
DEFAULT_OUTPUT=Path.home()/'NanoJev/runs/hawk_dove_gaussian_eight_stage_v1'
TIERS=fine.TIERS


def digest(data:bytes)->str:
    return hashlib.sha256(data).hexdigest()


def safe_bytes(path):
    return master.checked_file(path)


def check_parent(args):
    """Load the second-bank Gaussian without treating its external path as mutable authority."""
    root=args.refinement_checkpoint.parent.parent if args.refinement_checkpoint.parent.name=='best_checkpoints' else args.refinement_checkpoint.parent
    read=safe_bytes(args.refinement_checkpoint)
    meta=args.refinement_checkpoint.with_suffix('.json')
    if not meta.is_file():raise ValueError('STACK_MISSING_REFINEMENT_METADATA')
    md=json.loads(meta.read_text(encoding='utf8'))
    parent=master.load_state(read)
    if (parent.get('schema')!=fine.SCHEMA or md.get('checkpoint_sha256')!=digest(read) or
        md.get('arm')!='gaussian' or int(md.get('step',-1))!=int(parent.get('step',-2))):
        raise ValueError('STACK_REFINEMENT_PROVENANCE_MISMATCH')
    ident=parent['identity']
    frozen_parent=safe_bytes(root/'parent_snapshot.pt')
    donor=safe_bytes(root/'donor_snapshot.pt')
    if (digest(frozen_parent)!=ident['parent_sha256'] or digest(donor)!=ident['donor_sha256'] or
        fork.fingerprint(args.source)!=ident['source_sha256'] or
        fork.fingerprint(args.teacher)!=ident['teacher_sha256']):
        raise ValueError('STACK_FROZEN_SOURCE_MISMATCH')
    if args.seed!=int(ident['seed']) or args.dev_per_tier!=int(ident['dev_per_tier']):
        raise ValueError('STACK_PARENT_SPLIT_MISMATCH')
    if int(ident['train_per_tier'])!=args.train_per_tier:
        raise ValueError('STACK_PARENT_TRAIN_SPLIT_MISMATCH')
    return read,frozen_parent,donor,md


def check_args(args):
    if (args.additional_steps<0 or args.steps_per_bank<1 or args.chunk_steps<1 or
        args.views<9 or args.train_per_tier<8 or args.dev_per_tier<8 or args.test_per_tier<8 or
        args.batch_size<1 or args.zoom<=0 or args.zoom>=1 or
        args.relative_weight<0 or args.small_boost<=0 or args.lr<=0 or
        args.small_threshold<=0 or not 0<args.gate_percent<=100 or
        not all(map(math.isfinite,(args.zoom,args.lr,args.relative_weight,args.small_boost,
                                    args.small_threshold,args.gate_percent)))):
        raise ValueError('STACK_INVALID_ARGUMENTS')
    if args.additional_steps>len(STAGES)*args.steps_per_bank:
        raise ValueError('STACK_TOO_MANY_ADDITIONAL_STEPS')
    fine.fault.verify_no_overlap(args.output,[args.refinement_checkpoint.parent,
             args.refinement_checkpoint.parent.parent,args.source,args.teacher])
    return args.output.expanduser().resolve()


def identity(args,first_sha,original_sha,donor_sha,first_id):
    return {'schema':SCHEMA,'source_refinement_path':str(args.refinement_checkpoint.expanduser().resolve()),
            'stage2_sha256':first_sha,'original_parent_sha256':original_sha,
            'donor_sha256':donor_sha,'source_sha256':first_id['source_sha256'],
            'teacher_sha256':first_id['teacher_sha256'],
            'stage2_step':int(first_id.get('step',0)),
            'stage2_head_arm':'gaussian','original_bridge_arm':first_id['parent_arm'],
            'zoom':args.zoom,'steps_per_bank':args.steps_per_bank,'views':args.views,
            'train_per_tier':args.train_per_tier,'dev_per_tier':args.dev_per_tier,
            'test_per_tier':args.test_per_tier,'batch_size':args.batch_size,
            'gate_percent':args.gate_percent,'small_threshold':args.small_threshold,
            'small_boost':args.small_boost,'relative_weight':args.relative_weight,
            'lr':args.lr,'seed':args.seed,
            'training':'one additional Gaussian bank at a time, freeze preceding banks',
            'benchmark_visibility':'upstream 011 exposes Y; no result injected into the new heads'}


def load_or_prepare(args):
    out=check_args(args)
    names=('stage2_snapshot.pt','parent_snapshot.pt','donor_snapshot.pt','config.json','latest.pt')
    exist=[(out/n).exists() for n in names]
    if any(exist) and not all(exist):raise ValueError('STACK_PARTIAL_RUN_STATE')
    resume=all(exist)
    if resume:
        first=safe_bytes(out/'stage2_snapshot.pt')
        original=safe_bytes(out/'parent_snapshot.pt')
        donor=safe_bytes(out/'donor_snapshot.pt')
        previous=json.loads((out/'config.json').read_text(encoding='utf8'))
        step=int(master.load_state(first)['step'])
        first_id={**master.load_state(first)['identity'],'step':step}
        ident=identity(args,digest(first),digest(original),digest(donor),first_id)
        if ident!=previous or (digest(first)!=previous['stage2_sha256'] or
           digest(original)!=previous['original_parent_sha256'] or
           digest(donor)!=previous['donor_sha256'] or
           fork.fingerprint(args.source)!=ident['source_sha256'] or
           fork.fingerprint(args.teacher)!=ident['teacher_sha256']):
            raise ValueError('STACK_RESUME_DRIFT')
        saved=master.load_state(safe_bytes(out/'latest.pt'))
        if saved.get('schema')!=SCHEMA or saved.get('identity')!=ident:
            raise ValueError('STACK_BAD_RESUME_STATE')
        md=None
    else:
        first,original,donor,md=check_parent(args)
        step=int(master.load_state(first)['step'])
        first_id={**master.load_state(first)['identity'],'step':step}
        ident=identity(args,digest(first),digest(original),digest(donor),first_id)
        saved=None
    return out,ident,saved,(first,original,donor),md


def scalar_features(features,origin,current,previous,info):
    """Use only predictions and predicted Gaussian occupancy, never target values."""
    prob=info['probabilities']
    if prob is None:raise ValueError('STACK_NON_GAUSSIAN_PREDECESSOR')
    radius=info['radius'].detach().clamp_min(1.)
    offsets=torch.stack((
        (current-origin)/(origin.abs()+125000.),
        (current-previous)/radius,
        torch.log10(1.+current.abs())/math.log10(1.+fine.fork.MAX_ABS),
        torch.sign(current),
        -((prob*prob.clamp_min(1e-10).log()).sum(-1))/math.log(prob.shape[-1]),
        (prob*torch.linspace(-1.,1.,prob.shape[-1],device=prob.device)[None,:]).sum(-1)),dim=-1)
    return torch.cat((features.float(),prob.float()*prob.shape[-1],offsets.float()),dim=-1)


class Cascade(nn.Module):
    def __init__(self,base_width,stage2,*,zoom=.72):
        super().__init__()
        if not 0<zoom<1:raise ValueError('STACK_INVALID_ZOOM')
        self.stage2=stage2
        self.stage2.eval().requires_grad_(False)
        self.zoom=float(zoom)
        prior_width=base_width+stage2.registers+6
        self.banks=nn.ModuleDict({str(k):fine.LocalRefiner(
            prior_width,kind='gaussian',registers=stage2.registers,
            sigma_bins=2.,floor=stage2.floor*(zoom**(k-2)),
            fraction=stage2.fraction*(zoom**(k-2)),temperature=stage2.temperature)
            for k in STAGES})

    def upto(self,data,last=8,return_input=False,with_details=False):
        """Predict every stage from the coarse estimate and frozen stage-2 head."""
        if last<BASE_STAGE or last>STAGES[-1]:raise ValueError('STACK_INVALID_DEPTH')
        base=data['coarse'];feat=data['features']
        current,info=self.stage2(feat,base)
        predecessor=base
        predictions={2:current}
        details={2:info}
        for k in STAGES:
            if k>last:break
            inp=scalar_features(feat,base,current,predecessor,info)
            if return_input and k==last:
                return {'features':inp,'coarse':current,'target':data['target']}
            next_value,next_info=self.banks[str(k)](inp,current)
            predecessor,current,info=current,next_value,next_info
            predictions[k]=current
            details[k]=info
        return (predictions,details) if with_details else predictions

    def active_inputs(self,data,k):
        return self.upto(data,k,True)


def score_predictions(truth,pred,coarse,threshold,previous=None,radius=None):
    small=truth.abs()<threshold
    large=truth.abs()>=1000000.
    correct=(pred-truth).abs()/truth.abs().clamp_min(1.)<=.05
    return {'cases':int(truth.numel()),'correct':int(correct.sum()),
            'accuracy_5pct':float(correct.float().mean()),
            'small_n':int(small.sum()),'small_correct':int(correct[small].sum()),
            'small_mae':float((pred[small]-truth[small]).abs().mean()) if small.any() else None,
            'mean_absolute_error':float((pred-truth).abs().mean()),
            'large_n':int(large.sum()),'large_correct':int(correct[large].sum()),
            'mean_abs_change_from_stage2':float((pred-coarse).abs().mean()),
            'mean_abs_change_from_previous':float((pred-(previous if previous is not None else coarse)).abs().mean()),
            'local_coverage':float(((truth-(previous if previous is not None else coarse)).abs()<=radius).float().mean()) if radius is not None else None,
            'small_local_coverage':float(((truth[small]-(previous if previous is not None else coarse)[small]).abs()<=radius[small]).float().mean()) if radius is not None and small.any() else None}


@torch.no_grad()
def probe(cascade,dev,gate,threshold,step):
    cascade.eval()
    tiers_by_stage={k:{} for k in (2,*STAGES)}
    for tier in TIERS:
        data=dev[tier]
        predictions,details=cascade.upto(data,with_details=True)
        for k,pred in predictions.items():
            prev=predictions[k-1] if k>2 else data['coarse']
            tiers_by_stage[k][tier]=score_predictions(data['target'],pred,predictions[2],threshold,
                                                        prev,details[k]['radius'])
    scores={}
    for stage,tiers in tiers_by_stage.items():
        correct=sum(tiers[t]['correct'] for t in TIERS)
        total=sum(tiers[t]['cases'] for t in TIERS)
        nsmall=sum(tiers[t]['small_n'] for t in TIERS)
        small=sum(tiers[t]['small_correct'] for t in TIERS)
        minimum=min(tiers[t]['accuracy_5pct'] for t in TIERS)
        failing=[t for t in TIERS if tiers[t]['accuracy_5pct']+1e-10<gate/100.]
        scores[str(stage)]={'scores':tiers,'macro_accuracy_5pct':correct/total,
          'small_x_correct':small,'small_x_n':nsmall,'small_x_accuracy_5pct':small/nsmall,
          'small_x_mae':sum(tiers[t]['small_mae']*tiers[t]['small_n'] for t in TIERS)/nsmall,
          'minimum_accuracy_5pct':minimum,'failing_tiers':failing,'all_nine_pass':not failing,
          'correct':correct,'cases':total,
          'small_local_coverage':sum(tiers[t]['small_local_coverage']*tiers[t]['small_n'] for t in TIERS)/nsmall}
    return {'schema':SCHEMA+'.probe.v1','step':step,
      'stage_being_trained':None,
      'stages':scores,'small_threshold':threshold,'gate_percent':gate}


def active_stage(step,steps_per_bank):
    if step<1 or step>steps_per_bank*len(STAGES):
        raise ValueError('STACK_STEP_OUTSIDE_BUDGET')
    return STAGES[(step-1)//steps_per_bank]


def snapshot(cascade,optimizers,ident,step,args):
    return {'schema':SCHEMA,'identity':ident,'step':step,
      'bank_states':{str(k):cascade.banks[str(k)].state_dict() for k in STAGES},
      'optimizers':{str(k):optimizers[str(k)].state_dict() for k in STAGES},
      'rng_cpu':torch.get_rng_state(),
      'rng_cuda':torch.cuda.get_rng_state_all() if args.device=='cuda' else None}


def rank(score,metric):
    if metric=='small':return (score['small_x_accuracy_5pct'],score['macro_accuracy_5pct'],score['minimum_accuracy_5pct'])
    if metric=='macro':return (score['macro_accuracy_5pct'],score['small_x_accuracy_5pct'],score['minimum_accuracy_5pct'])
    if metric=='all_nine':return (score['minimum_accuracy_5pct'],score['macro_accuracy_5pct'],score['small_x_accuracy_5pct'])
    raise ValueError('STACK_UNKNOWN_BEST_METRIC')


def retain_best(out,ck,report):
    raw=io.BytesIO();torch.save(ck,raw);contents=raw.getvalue()
    emitted=[]
    for metric in ('small','macro','all_nine'):
        for depth,score in report['stages'].items():
            if metric=='all_nine' and not score['all_nine_pass']:continue
            path=out/'best_checkpoints'/f'best_{metric}_stage{depth}.pt'
            metadata=path.with_suffix('.json')
            value=rank(score,metric)
            if path.exists()!=metadata.exists():raise ValueError('STACK_PARTIAL_BEST')
            if metadata.exists():
                old=json.loads(metadata.read_text(encoding='utf8'))
                if (old['stage2_sha256']!=ck['identity']['stage2_sha256'] or
                    digest(safe_bytes(path))!=old['checkpoint_sha256']):
                    raise ValueError('STACK_BEST_INTEGRITY_FAILED')
                if value<=tuple(old['rank']):continue
            store.atomic_bytes(path,contents)
            store.atom_json(metadata,{'schema':SCHEMA+'.best.v1','metric':metric,'stage':int(depth),
                'step':ck['step'],'rank':value,'stage2_sha256':ck['identity']['stage2_sha256'],
                'checkpoint_sha256':digest(contents)})
            emitted.append({'metric':metric,'stage':int(depth),'step':ck['step'],
                            'macro':score['macro_accuracy_5pct'],
                            'small':score['small_x_accuracy_5pct']})
    return emitted


def verify_stage2(info,md):
    if md is None:return
    metric=md['metric'];expected=tuple(float(z) for z in md['rank'])
    found=rank(info['stages']['2'],metric)
    if any(abs(a-b)>1e-6 for a,b in zip(found,expected)):
        raise ValueError(f'STACK_STAGE2_SCORE_MISMATCH:{found}:{expected}')


def load_features(args,model,decoder,bridge,cache,split):
    return fine.rows_for(model,decoder,bridge,cache,args,split)


def run(args):
    out,ident,saved,copies,md=load_or_prepare(args)
    first,original,donor=copies
    budget=args.steps_per_bank*len(STAGES)
    start=int(saved['step']) if saved else 0
    if not args.test_only and start+args.additional_steps>budget:raise ValueError('STACK_BUDGET_EXCEEDED')
    fork.emit('stack_prepare',step=start,stage_count=8,new_banks=6,
              frozen_stage2_step=ident['stage2_step'],stage2_sha256=ident['stage2_sha256'],
              original_immutable=True,test_split_accessed=args.test_only,
              upstream_y_visible=True)
    model=fork.ex.load_current(args.source,args.device,args.dtype,args.allow_model_download,args.seed)
    original_ck=master.load_state(original);donor_ck=master.load_state(donor)
    orig_args=argparse.Namespace(arm=ident['original_bridge_arm'])
    decoder,bridge=fine.init_frozen(orig_args,original_ck,donor_ck,model)
    cache=fork.cached.FrozenCaptureCache(out/'frozen-capture-cache-v1',{
        'schema':SCHEMA+'.capture','source_sha256':ident['source_sha256'],
        'teacher_sha256':ident['teacher_sha256'],
        'max_prompt_tokens':model['config']['max_prompt_tokens']},args.cache_memory_mb)
    if args.test_only:
        if saved is None:raise ValueError('STACK_TEST_REQUIRES_SAVED_RUN')
        best=out/'best_checkpoints'/f'best_{args.best_metric}_stage{args.best_stage}.pt'
        if not best.exists():raise ValueError('STACK_BEST_NOT_AVAILABLE')
        meta=json.loads(best.with_suffix('.json').read_text(encoding='utf8'))
        contents=safe_bytes(best)
        if (meta['checkpoint_sha256']!=digest(contents) or meta['stage']!=args.best_stage or
            meta['metric']!=args.best_metric):raise ValueError('STACK_TEST_INTEGRITY_FAILED')
        chosen=master.load_state(contents)
        if chosen['identity']!=ident:raise ValueError('STACK_TEST_IDENTITY_DRIFT')
        test,_=load_features(args,model,decoder,bridge,cache,'test')
        base_width=int(test[TIERS[0]]['features'].shape[-1])
        s2_id=master.load_state(first)['identity']
        stage2=fine.LocalRefiner(base_width,kind='gaussian',registers=int(s2_id['registers']),
          sigma_bins=float(s2_id['sigma_bins']),floor=float(s2_id['floor']),
          fraction=float(s2_id['fraction']),temperature=float(s2_id['temperature'])).to(model['device'])
        stage2.load_state_dict(master.load_state(first)['heads']['gaussian'],strict=True)
        cascade=Cascade(base_width,stage2,zoom=args.zoom).to(model['device'])
        for k in STAGES:cascade.banks[str(k)].load_state_dict(chosen['bank_states'][str(k)])
        result=probe(cascade,test,args.gate_percent,args.small_threshold,chosen['step'])
        result['schema']=SCHEMA+'.untouched-test.v1';result['best_stage']=args.best_stage
        result['best_metric']=args.best_metric;result['checkpoint_sha256']=digest(contents)
        store.atom_json(out/f'test-{args.best_metric}-stage{args.best_stage}-step-{chosen["step"]:07d}.json',result)
        fork.emit('stack_test_finished',stage=args.best_stage,step=chosen['step'],
                  macro=result['stages'][str(args.best_stage)]['macro_accuracy_5pct'],
                  small=result['stages'][str(args.best_stage)]['small_x_accuracy_5pct'])
        return result
    train,tr_meta=load_features(args,model,decoder,bridge,cache,'train')
    dev,dv_meta=load_features(args,model,decoder,bridge,cache,'dev')
    if {str(r['instance']) for t in TIERS for r in tr_meta[t]} & \
       {str(r['instance']) for t in TIERS for r in dv_meta[t]}:
        raise ValueError('STACK_TRAIN_DEV_INSTANCE_LEAKAGE')
    width=int(train[TIERS[0]]['features'].shape[-1])
    s2_id=master.load_state(first)['identity']
    stage2=fine.LocalRefiner(width,kind='gaussian',registers=int(s2_id['registers']),
          sigma_bins=float(s2_id['sigma_bins']),floor=float(s2_id['floor']),
          fraction=float(s2_id['fraction']),temperature=float(s2_id['temperature'])).to(model['device'])
    stage2.load_state_dict(master.load_state(first)['heads']['gaussian'],strict=True)
    cascade=Cascade(width,stage2,zoom=args.zoom).to(model['device'])
    optimizers={str(k):torch.optim.AdamW(cascade.banks[str(k)].parameters(),lr=args.lr) for k in STAGES}
    if saved:
        for k in STAGES:
            cascade.banks[str(k)].load_state_dict(saved['bank_states'][str(k)],strict=True)
            optimizers[str(k)].load_state_dict(saved['optimizers'][str(k)])
        torch.set_rng_state(saved['rng_cpu'])
        if args.device=='cuda' and saved.get('rng_cuda') is not None:torch.cuda.set_rng_state_all(saved['rng_cuda'])
    else:
        initial=probe(cascade,dev,args.gate_percent,args.small_threshold,0)
        verify_stage2(initial,md)
        s2=initial['stages']['2']
        for k in STAGES:
            score=initial['stages'][str(k)]
            if score['correct']!=s2['correct'] or score['small_x_correct']!=s2['small_x_correct']:
                raise ValueError('STACK_NOT_INITIAL_IDENTITY')
        out.mkdir(parents=True,exist_ok=True)
        store.atomic_bytes(out/'stage2_snapshot.pt',first)
        store.atomic_bytes(out/'parent_snapshot.pt',original)
        store.atomic_bytes(out/'donor_snapshot.pt',donor)
        store.atom_json(out/'config.json',ident)
    def save(step):
        ck=snapshot(cascade,optimizers,ident,step,args)
        store.atom_torch(out/'latest.pt',ck)
        results=probe(cascade,dev,args.gate_percent,args.small_threshold,step)
        results['stage_being_trained']=active_stage(step,args.steps_per_bank) if step else None
        store.atom_json(out/f'probe-step-{step:07d}.json',results)
        for event in retain_best(out,ck,results):fork.emit('stack_best_saved',**event)
        summary={str(k):{'macro':results['stages'][str(k)]['macro_accuracy_5pct'],
                         'small':results['stages'][str(k)]['small_x_accuracy_5pct'],
                         'small_mae':results['stages'][str(k)]['small_x_mae'],
                         'all_nine':results['stages'][str(k)]['all_nine_pass']} for k in (2,*STAGES)}
        fork.emit('stack_probe',step=step,active_stage=results['stage_being_trained'],stages=summary)
        return results
    if saved is None:save(0)
    for step in range(start+1,start+args.additional_steps+1):
        stage=active_stage(step,args.steps_per_bank)
        batch=fine.take_sample(train,args.seed,step,args.views)
        cascade.train()
        cascade.stage2.eval()
        for k in STAGES:
            cascade.banks[str(k)].eval().requires_grad_(k==stage)
        with torch.no_grad():
            inp=cascade.active_inputs(batch,stage)
        target_head=cascade.banks[str(stage)]
        opt=optimizers[str(stage)];opt.zero_grad(set_to_none=True)
        loss=fine.objective(target_head,inp,args.small_boost,args.small_threshold,args.relative_weight)
        if not bool(torch.isfinite(loss)):raise FloatingPointError('STACK_NONFINITE_LOSS:'+str(stage))
        loss.backward()
        torch.nn.utils.clip_grad_norm_(target_head.parameters(),1.)
        opt.step()
        if step%args.chunk_steps==0 or step==start+args.additional_steps or step%args.steps_per_bank==0:
            save(step)
    for p,sha in [('stage2_snapshot.pt','stage2_sha256'),('parent_snapshot.pt','original_parent_sha256'),('donor_snapshot.pt','donor_sha256')]:
        if digest(safe_bytes(out/p))!=ident[sha]:raise ValueError('STACK_IMMUTABLE_SNAPSHOT_DRIFT')
    fork.emit('stack_finished',step=start+args.additional_steps,latest=str(out/'latest.pt'),
              test_split_read=False)


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--refinement-checkpoint',type=Path,default=DEFAULT_PARENT)
    p.add_argument('--source',type=Path,default=fork.science.DEFAULT_SOURCE)
    p.add_argument('--teacher',type=Path,default=fork.science.DEFAULT_TEACHER)
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--additional-steps',type=int,default=18000)
    p.add_argument('--steps-per-bank',type=int,default=3000)
    p.add_argument('--chunk-steps',type=int,default=300)
    p.add_argument('--zoom',type=float,default=.72)
    p.add_argument('--views',type=int,default=9)
    p.add_argument('--train-per-tier',type=int,default=256)
    p.add_argument('--dev-per-tier',type=int,default=48)
    p.add_argument('--test-per-tier',type=int,default=48)
    p.add_argument('--batch-size',type=int,default=8)
    p.add_argument('--gate-percent',type=float,default=40.)
    p.add_argument('--small-threshold',type=float,default=250000.)
    p.add_argument('--small-boost',type=float,default=2.)
    p.add_argument('--relative-weight',type=float,default=.2)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--seed',type=int,default=20261018)
    p.add_argument('--cache-memory-mb',type=int,default=1024)
    p.add_argument('--test-only',action='store_true')
    p.add_argument('--best-stage',type=int,choices=(2,*STAGES),default=8)
    p.add_argument('--best-metric',choices=('small','macro','all_nine'),default='small')
    return p


def main(argv=None):run(parser().parse_args(argv))

if __name__=='__main__':main()
