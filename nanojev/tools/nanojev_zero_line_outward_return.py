#!/usr/bin/env python3
"""Fast outward acquisition to 4M, then reverse zero-line consolidation.

A *single* recurrent AdditionNet is fine-tuned. Each outward step is a measured
research milestone, NOT a claim of full retention or certified arithmetic.
The only sealed TEST is opened after final reverse-consolidation DEV/legacy gates.
All labels and residual routing exist in training/evaluation, never inference.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import random
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from tools.nanojev_addition_curriculum import target_digits
from tools.nanojev_zero_line_ladder import atomic_checkpoint, atomic_json, deck, sample_deck, evaluate as legacy_evaluate
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256
from tools.nanojev_zero_line_zero_only import BANDS, MAX_ABS, input_tensor, legacy_loss, classify as zero_classify, build_probe as zero_probe
from tools import nanojev_zero_line_sampled_radius_ladder as old

SCHEMA = 'nanojev.zero-line-outward-return.v1'
ORIGIN_RADIUS = 32
OUTER_TARGET = MAX_ABS * 2


def outward_plan(target):
    return old.planned_radii(target)


def return_plan(target):
    """Reverse through useful logarithmic radii then refine the exact core."""
    return list(reversed(outward_plan(target))) + [32, 16, 0]


def metrics(model, rows, device, batch):
    return old.assess(model, rows, device, batch)


def frontier_cards(radius, split, seed, view, count=48):
    """A separately balanced high-residual probe; no answer-aware selection.

    At million-scale, a logarithmic general DEV sampler can contain too few
    frontier examples to tell whether we really learned the new scale.
    """
    lower=max(33,(radius+1)//2)
    eligible=[i for i,(lo,hi) in enumerate(BANDS) if 2*hi>=lower]
    rng=random.Random(f'zero-or-frontier:{seed}:{radius}:{split}:{view}')
    out=[];used=set()
    for k in range(count):
        band=eligible[k%len(eligible)]
        lo,hi=BANDS[band]
        lo=max(lo,32,(lower+1)//2)
        m=None
        for _ in range(5000):
            x=rng.randint(lo,hi)
            if old.stable_fold(x,seed)==split and x not in used:
                m=x;break
        if m is None:
            # Allow repeated max magnitudes when a tiny band has too few
            # distinct values in the designated split.
            for _ in range(5000):
                x=rng.randint(lo,hi)
                if old.stable_fold(x,seed)==split:m=x;break
        if m is None:raise RuntimeError('ZERO_OR_FRONTIER_EMPTY_SPLIT')
        used.add(m)
        hi_d=min(2*m,radius)
        if hi_d < lower:raise AssertionError('ZERO_OR_FRONTIER_MAGNITUDE_INVALID')
        d=rng.randint(lower,hi_d)
        if k%2:d=-d
        pair=old.pair_for(m,d,swap=(k%4>=2))
        if pair is None:raise AssertionError('ZERO_OR_FRONTIER_BAD_PAIR')
        out.append((*pair,d,band,m))
    return out


def frontier_metrics(model, rows, device, batch):
    score=metrics(model,rows,device,batch)['classes']['outer']
    return {'cards':score['cards'],'accuracy':score['accuracy']}


def generate_batch(rng, radius, seed, size, weights, cursor, phase, focus, target):
    """Train on legal operand pairs, with hard frontier exposure in outward mode.

    During return, favor the requested smaller shell, always mixing broad-scale
    replay and the exact signed core. Both phases use TRAIN magnitudes only.
    """
    if phase not in ('outward','return'):raise ValueError('ZERO_OR_INVALID_PHASE')
    if size<16:raise ValueError('ZERO_OR_BATCH_TOO_SMALL')
    rows,cursor=old.train_cards(rng,radius if phase=='outward' else target,seed,size,weights,cursor)
    for i in range(len(rows)):
        # Preserve core practice in BOTH phases; later consolidation raises its rate.
        u=i%16
        if phase=='outward':
            if u>=8:continue
            # Half of the batch is deliberately exposed to the *current frontier*.
            lower=max(33,(radius+1)//2)
            upper=radius
        else:
            if u<5:
                # 5/16 focus on return shell (or zero-core at end).
                lower=max(1,(focus+1)//2)
                upper=max(1,focus)
                if focus==0:lower=upper=0
            elif u<9:
                # 4/16 old exact-zero / signed-inner rehearsal.
                lower=upper=0 if u==5 else 1 + ((cursor+i) % 32)
            elif u<13:
                # 4/16 full-range replay, not limited to small numbers.
                lower=max(33,(target+1)//4)
                upper=target
            else:continue
        valid=[b for b,(_,hi) in enumerate(BANDS) if hi*2 >= lower]
        if not valid:continue
        band=rng.choice(valid)
        lo,hi=BANDS[band];lo=max(lo,32)
        m=None
        for _ in range(3000):
            candidate=rng.randint(max(lo,min(hi,(lower+1)//2)),hi)
            if old.stable_fold(candidate,seed)=='train':m=candidate;break
        if m is None:raise RuntimeError(f'ZERO_OR_NO_TRAIN_ANCHOR band={band}')
        possible=min(upper,2*m)
        if lower>possible:continue
        # Logarithmic shell sampling favors diversity within enormous intervals.
        if lower==0:d=0
        elif lower==possible:d=lower
        else:
            lo_log=math.log(max(1,lower));hi_log=math.log(possible)
            d=min(possible,max(lower,int(round(math.exp(rng.uniform(lo_log,hi_log))))))
        if d and rng.getrandbits(1):d=-d
        pair=old.pair_for(m,d,swap=bool(rng.getrandbits(1)),orientation=(1 if rng.getrandbits(1) else -1))
        if pair is None:raise AssertionError('ZERO_OR_BAD_PAIR')
        rows[i]=(*pair,d,band,m)
    rng.shuffle(rows)
    return rows,cursor


def stage_progress(dev, frontier, baseline, args):
    """No zero/inner/legacy gates in outward acquisition; document them instead.

    Require nontrivial outer arithmetic and actual competence at frontier. Avoid
    zero/constant-output collapse falsely passing a radius milestone.
    """
    rates=[r['classes']['outer']['accuracy'] for r in dev]
    fronts=[r['accuracy'] for r in frontier if r['cards']>=args.min_frontier_cards]
    base_rates=[r['classes']['outer']['accuracy'] for r in baseline]
    if len(fronts)!=len(dev):return False
    if min(rates)<args.outward_min_outer or min(fronts)<args.outward_min_frontier:return False
    # Must improve on at least the weakest baseline view, unless already strong.
    return min(rates)>=min(base_rates)+args.outward_min_gain or min(rates)>=args.outward_strong_outer


def snapshot(model,opt,rng,step,weights,cursor,qualifying,phase,stage):
    return {'schema':SCHEMA+'.checkpoint','model':model.state_dict(),
        'optimizer':opt.state_dict(),'rng':rng.getstate(),'torch_rng':torch.get_rng_state(),
        'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
        'step':step,'weights':weights,'cursor':cursor,'qualifying':qualifying,
        'phase':phase,'stage':stage}


def restore(state,model,opt,rng,phase,stage):
    if state.get('schema')!=SCHEMA+'.checkpoint' or state.get('phase')!=phase or state.get('stage')!=stage:
        raise RuntimeError('ZERO_OR_BAD_RESUME_CHECKPOINT')
    model.load_state_dict(state['model'],strict=True)
    opt.load_state_dict(state['optimizer']);rng.setstate(state['rng']);torch.set_rng_state(state['torch_rng'])
    if torch.cuda.is_available() and state.get('cuda_rng') is not None:torch.cuda.set_rng_state_all(state['cuda_rng'])
    return state['step'],state['weights'],state['cursor'],state['qualifying']


def initial_parent(args):
    parent,src=old.verified_research_parent(args)
    if args.warmstart_stage:
        stage=Path(args.warmstart_stage).resolve()
        if stage.name!='radius_64':raise RuntimeError('ZERO_OR_WARMSTART_MUST_BE_RADIUS64')
        previous_config=stage/'config.json'
        root_config=stage.parent.parent/'config.json'
        ckpt=stage/'latest.pt'
        if not all(p.is_file() for p in (previous_config,root_config,ckpt)):
            raise RuntimeError('ZERO_OR_WARMSTART_FILES_MISSING')
        prev=json.loads(previous_config.read_text('utf-8'))
        root_cfg=json.loads(root_config.read_text('utf-8'))
        if (prev.get('schema')!=old.SCHEMA+'.stage' or prev.get('radius')!=64
            or prev.get('parent_sha256')!=src['parent_sha256']
            or prev.get('settings_sha256')!=sha256(root_config)
            or root_cfg.get('schema')!=old.SCHEMA
            or root_cfg.get('source',{}).get('parent_sha256')!=src['parent_sha256']):
            raise RuntimeError('ZERO_OR_WARMSTART_PARENT_MISMATCH')
        if (stage/'test_opened.json').exists():
            raise RuntimeError('ZERO_OR_WARMSTART_SEALED_STAGE')
        before=sha256(ckpt)
        state=torch.load(ckpt,map_location='cpu',weights_only=True)
        if before!=sha256(ckpt):raise RuntimeError('ZERO_OR_WARMSTART_CHANGED_DURING_READ')
        if state.get('schema')!=old.SCHEMA+'.checkpoint' or state.get('step',0)<1:
            raise RuntimeError('ZERO_OR_WARMSTART_INCOMPATIBLE')
        parent.load_state_dict(state['model'],strict=True)
        src['warmstart']={'checkpoint':str(ckpt),'checkpoint_sha256':before,
                          'step':state['step'],'stage_config_sha256':sha256(previous_config),
                          'root_config_sha256':sha256(root_config)}
    return parent,src


def frozen_model(args,state):
    model,_=load_certified_model(args.ladder_output,args.device)
    model.load_state_dict(state,strict=True)
    for n,p in model.named_parameters():
        p.requires_grad_(n.startswith('backbone.') or n.startswith('norm.') or n in ('query','pos'))
    return model


def config_for(args,source):
    keys=('target','seed','batch_size','legacy_batch_size','legacy_weight','inner_weight','zero_weight',
      'lr','return_lr','probe_every','eval_batch_size','dev_anchors','dev_views',
      'outer_per_anchor','historical_probe_cards','zero_probe_anchors','outward_min_outer',
      'outward_min_frontier','outward_min_gain','outward_strong_outer','min_frontier_cards',
      'return_min_outer','return_min_inner','return_final_min_outer','return_final_min_inner',
      'return_final_min_sign','test_anchors','cpu_threads','outward_policy')
    return {'schema':SCHEMA,'source':source,'parameters':{k:getattr(args,k) for k in keys},
      'code_sha256':sha256(__file__), 'old_sampler_sha256':sha256(old.__file__),
      'scope':'research, not independent final certification; embargo inherited from earlier investigations',
      'inference':'one AdditionNet, no Python oracle/router, no specialist bank'}


def validation(args):
    if (not 64<=args.target<=OUTER_TARGET or args.max_stages<1 or args.stage_steps<1
      or args.return_steps<1 or args.probe_every<1 or args.batch_size<16 or args.legacy_batch_size<1
      or args.dev_views<2 or args.dev_anchors<1 or args.test_anchors<1 or args.eval_batch_size<1
      or args.outer_per_anchor<1 or args.historical_probe_cards<1 or args.zero_probe_anchors<1
      or args.cpu_threads<1 or not 0<args.lr<1 or not 0<args.return_lr<1
      or not 0<args.outward_min_outer<=1 or not 0<args.outward_min_frontier<=1
      or not 0<=args.outward_min_gain<1 or not 0<args.outward_strong_outer<=1
      or not 0<args.return_min_outer<=1 or not 0<args.return_min_inner<=1
      or not 0<args.return_final_min_outer<=1 or not 0<args.return_final_min_inner<=1
      or not 0<args.return_final_min_sign<=1 or args.min_frontier_cards<1):
        raise ValueError('ZERO_OR_INVALID_ARGUMENTS')
    if args.phase in ('outward','return') and args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('ZERO_OR_CUDA_UNAVAILABLE')


def trained_stage(args,root,phase,stage,parent,parent_sha,origin_model,history,legacy,final=False):
    label=f'{phase}_{stage}'
    path=root/'stages'/label
    cfg={'schema':SCHEMA+'.stage','phase':phase,'stage':stage,'parent_sha256':parent_sha,
         'root_sha256':sha256(root/'config.json')}
    if (path/'config.json').is_file():
        if json.loads((path/'config.json').read_text())!=cfg:raise RuntimeError('ZERO_OR_STAGE_CONFIG_DRIFT')
    else:
        if path.exists() and any(path.iterdir()):raise RuntimeError('ZERO_OR_UNTRACKED_STAGE_FILES')
        path.mkdir(parents=True,exist_ok=True);old.atomic_json(path/'config.json',cfg)
    if (path/'test_opened.json').exists():raise RuntimeError('ZERO_OR_TEST_ALREADY_OPENED')
    model=frozen_model(args,parent.state_dict())
    optimizer=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],
        lr=args.lr if phase=='outward' else args.return_lr,weight_decay=0.)
    seed=args.seed + stage*17 + (0 if phase=='outward' else 12_800_000)
    torch.manual_seed(seed+107);rng=random.Random(seed)
    weights=[1.]*len(BANDS);cursor=step=qualifying=0
    if (path/'latest.pt').is_file():
        if not args.resume:raise RuntimeError('ZERO_OR_EXISTING_STAGE_USE_RESUME')
        state=torch.load(path/'latest.pt',map_location='cpu',weights_only=True)
        step,weights,cursor,qualifying=restore(state,model,optimizer,rng,phase,stage)
    train_probe=old.evaluation_cards(args.target if phase=='return' else stage,'train',args.seed,stage,1,1,args.outer_per_anchor)[0]
    dev=old.evaluation_cards(args.target if phase=='return' else stage,'dev',args.seed,stage,args.dev_anchors,args.dev_views,args.outer_per_anchor)
    baseline_file=path/'baseline.json'
    if baseline_file.is_file():
        baseline_record=json.loads(baseline_file.read_text('utf-8'))
        if baseline_record.get('parent_sha256')!=parent_sha:
            raise RuntimeError('ZERO_OR_BASELINE_PARENT_DRIFT')
        baselines=baseline_record['dev']
    else:
        if step:raise RuntimeError('ZERO_OR_MISSING_RESUME_BASELINE')
        baselines=[metrics(model,v,args.device,args.eval_batch_size) for v in dev]
        atomic_json(baseline_file,{'parent_sha256':parent_sha,'dev':baselines})
    outer_frontier=[frontier_cards(stage,'dev',args.seed,v) for v in range(args.dev_views)] if phase=='outward' else []
    historic=sample_deck(legacy,args.historical_probe_cards,seed)
    max_steps=args.stage_steps if phase=='outward' else args.return_steps
    for i in range(step+1,max_steps+1):
        rows,cursor=generate_batch(rng,stage if phase=='outward' else args.target,
            args.seed,args.batch_size,weights,cursor,phase,stage,args.target)
        model.train()
        y=torch.tensor([c[2] for c in rows],device=args.device,dtype=torch.long)
        output=model(input_tensor(rows,args.device))
        losses=F.cross_entropy(output['sign'],y.sign()+1,reduction='none')+F.cross_entropy(
            output['digits'].transpose(1,2),target_digits(y),reduction='none').mean(-1)
        if phase=='outward':
            w=torch.where(y==0,args.zero_weight,torch.where(y.abs()<=32,args.inner_weight,1.))
            leg_w=args.legacy_weight*.25
        else:
            # As consolidation approaches zero, emphasize precision and legacy.
            w=torch.where(y==0,args.zero_weight*2,torch.where(y.abs()<=32,args.inner_weight*3,1.))
            leg_w=args.legacy_weight
        weighted=(losses*w).sum()/w.sum()
        old_x=torch.randint(-127,128,(args.legacy_batch_size,2),device=args.device)
        loss=weighted+leg_w*legacy_loss(model,origin_model,old_x)
        if not torch.isfinite(loss):raise FloatingPointError('ZERO_OR_NONFINITE_LOSS')
        optimizer.zero_grad(set_to_none=True);loss.backward()
        nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad),1.,error_if_nonfinite=True)
        optimizer.step()
        if i%args.probe_every!=0 and i<max_steps:continue
        dev_reports=[metrics(model,v,args.device,args.eval_batch_size) for v in dev]
        train_report=metrics(model,train_probe,args.device,args.eval_batch_size)
        frontier=[frontier_metrics(model,v,args.device,args.eval_batch_size) for v in outer_frontier] if phase=='outward' else []
        weights=[1.+4*(1-train_report['outer_by_band'].get(str(b),{'accuracy':1.})['accuracy']) for b in range(len(BANDS))]
        if phase=='outward':
            good=stage_progress(dev_reports,frontier,baselines,args)
        else:
            outer_ok=all(x['classes']['outer']['accuracy']>=args.return_min_outer for x in dev_reports)
            inner_ok=all(x['classes']['inner']['accuracy']>=args.return_min_inner for x in dev_reports)
            good=outer_ok and inner_ok
        qualifying=qualifying+1 if good else 0
        historical=legacy_evaluate(model,historic,args.device,args.eval_batch_size)
        record={'schema':SCHEMA+'.stage-result','status':'training','phase':phase,'stage':stage,
            'step':i,'loss':float(loss.detach()),'qualified':good,'streak':qualifying,
            'dev':dev_reports,'baseline':baselines,'frontier':frontier,
            'historical_probe':{'passed':historical['groups']['all']['passed'],
                                'total':len(historic['y']),'false_zero':historical['false_zero']}}
        atomic_checkpoint(path/'latest.pt',snapshot(model,optimizer,rng,i,weights,cursor,qualifying,phase,stage))
        atomic_json(path/'result.json',record)
        print('ZERO_OR_PROBE='+json.dumps({'phase':phase,'radius':stage,'step':i,
            'outer_5pct':[round(x['classes']['outer']['accuracy'],5) for x in dev_reports],
            'inner_exact':[round(x['classes']['inner']['accuracy'],5) for x in dev_reports],
            'zero_errors':[x['classes']['zero']['errors'] for x in dev_reports],
            'frontier_5pct':[None if x['accuracy'] is None else round(x['accuracy'],5) for x in frontier],
            'legacy_probe':record['historical_probe'],'streak':qualifying,'qualified':good},sort_keys=True),flush=True)
        # Outward can advance when new large-residual competence exists.
        # Return stages advance on modest progress, but only final return is strict.
        record['outward_policy']=args.outward_policy
        if phase=='outward' and args.outward_policy=='demonstrated' and good:
            return publish(path,record,model,parent_sha,phase,stage)
        if phase=='return' and final and qualifying>=2:
            if final_conformity(model,dev_reports,history,origin_model,legacy,args,path,record):
                return publish(path,record,model,parent_sha,phase,stage,final_cert=True)
            # If the sealed test was opened, the attempt is over permanently.
            if (path/'test_opened.json').exists():return record
    if (phase=='outward' and args.outward_policy=='exposure') or (phase=='return' and not final):
        # Completion of scheduled exposure/consolidation, NOT competence certification.
        record['outward_policy']=args.outward_policy
        record['milestone_rule']='bounded training exposure; performance is diagnostic, not a pass gate'
        return publish(path,record,model,parent_sha,phase,stage)
    record={'schema':SCHEMA+'.stage-result','phase':phase,'stage':stage,
            'status':'budget_exhausted','step':max_steps,'resume_available':True}
    atomic_json(path/'result.json',record)
    return record


def publish(path,report,model,parent_sha,phase,stage,final_cert=False):
    report['status']=('final_conformity_passed' if final_cert else
        ('exposure_milestone' if phase=='outward' and report.get('outward_policy')=='exposure'
         else 'acquisition_milestone' if phase=='outward' else 'reverse_milestone'))
    atomic_checkpoint(path/'accepted.pt',{'schema':SCHEMA+'.accepted','model':model.state_dict(),
        'phase':phase,'stage':stage,'parent_sha256':parent_sha,'status':report['status'],
        'scope':'research milestone; full certification only after final return'})
    atomic_json(path/'acceptance.json',{'schema':SCHEMA+'.acceptance','phase':phase,'stage':stage,
        'parent_sha256':parent_sha,'accepted_sha256':sha256(path/'accepted.pt'),
        'config_sha256':sha256(path/'config.json')})
    atomic_json(path/'result.json',report)
    print('ZERO_OR_STAGE_ACCEPTED='+json.dumps({'phase':phase,'radius':stage,'status':report['status'],
        'step':report['step']},sort_keys=True),flush=True)
    return report


def final_conformity(model,dev,zero_history,origin_model,legacy,args,path,report):
    if not all(x['classes']['zero']['errors']==0 and
               x['classes']['inner']['accuracy']>=args.return_final_min_inner and
               x['classes']['outer']['accuracy']>=args.return_final_min_outer and
               x['classes']['sign']['accuracy']>=args.return_final_min_sign
               for x in dev):return False
    full=legacy_evaluate(model,legacy,args.device,args.eval_batch_size)
    historical_ok=full['groups']['all']['passed']==65025 and full['false_zero']==0
    z={label:zero_classify(model,rows,args.device,args.eval_batch_size) for label,rows in zero_history.items()}
    report['historical_full']={'passed':full['groups']['all']['passed'],'false_zero':full['false_zero']}
    report['zero_history']={k:bool(v['passes']) for k,v in z.items()}
    if not historical_ok or not all(x['passes'] for x in z.values()):
        report['status']='final_dev_retention_refused';atomic_json(path/'result.json',report)
        return False
    # One-time sealed final test. No training/selection from test failures.
    atomic_json(path/'test_opened.json',{'step':report['step'],'policy':'single use; NEVER train or tune from this result'})
    test=old.evaluation_cards(args.target,'test',args.seed,999_999,args.test_anchors,1,args.outer_per_anchor)[0]
    tested=metrics(model,test,args.device,args.eval_batch_size)
    report['final_test']=tested
    passes=(tested['classes']['zero']['errors']==0 and
        tested['classes']['inner']['accuracy']>=args.return_final_min_inner and
        tested['classes']['outer']['accuracy']>=args.return_final_min_outer and
        tested['classes']['sign']['accuracy']>=args.return_final_min_sign)
    report['status']='final_conformity_passed' if passes else 'final_test_refused'
    atomic_json(path/'result.json',report)
    print('ZERO_OR_FINAL_TEST='+json.dumps({'status':report['status'],
      'cards':tested['cards'],'zero_errors':tested['classes']['zero']['errors'],
      'inner_exact':tested['classes']['inner']['accuracy'],
      'outer_5pct':tested['classes']['outer']['accuracy'],
      'historical_exact':full['groups']['all']['passed']},sort_keys=True),flush=True)
    return passes


def load_chain(args,root,source_model,phase):
    parent=source_model
    src=json.loads((root/'config.json').read_text())['source']
    previous_sha=src.get('warmstart',{}).get('checkpoint_sha256',src['parent_sha256'])
    completed=[]
    for p,s in ([('outward',x) for x in outward_plan(args.target)] +
        ([('return',x) for x in return_plan(args.target)] if phase=='return' else [])):
        path=root/'stages'/f'{p}_{s}'
        cp=path/'accepted.pt'
        if not cp.exists():
            if (path/'test_opened.json').exists():raise RuntimeError('ZERO_OR_SEALED_TEST_PREVIOUSLY_FAILED')
            return parent,previous_sha,completed,(p,s)
        accept=json.loads((path/'acceptance.json').read_text())
        state=torch.load(cp,map_location='cpu',weights_only=True)
        if (state.get('schema')!=SCHEMA+'.accepted' or state['phase']!=p or state['stage']!=s
            or state['parent_sha256']!=previous_sha or accept.get('accepted_sha256')!=sha256(cp)
            or accept.get('parent_sha256')!=previous_sha or accept.get('config_sha256')!=sha256(path/'config.json')):
            raise RuntimeError('ZERO_OR_CHAIN_TAMPERED')
        parent.load_state_dict(state['model'],strict=True)
        previous_sha=sha256(cp);completed.append((p,s))
    return parent,previous_sha,completed,None


def run(args):
    validation(args)
    if args.phase=='plan':
        r={'outward':outward_plan(args.target),'return':return_plan(args.target),
           'outward_gates':'exposure mode completes fixed budget regardless of accuracy; demonstrated mode requires outer+frontier progress; retention recorded, not blocking',
           'return_gates':'historical+zero+exact inner+outer are binding only at final conformity'}
        print('ZERO_OR_PLAN='+json.dumps(r,sort_keys=True));return r
    torch.set_num_threads(args.cpu_threads)
    root=Path(args.output).resolve();cfg_file=root/'config.json'
    source_model,source=initial_parent(args)
    config=config_for(args,source)
    if args.resume:
        if not cfg_file.is_file() or json.loads(cfg_file.read_text('utf-8'))!=config:
            raise RuntimeError('ZERO_OR_CONFIG_DRIFT')
    else:
        if root.exists() and any(root.iterdir()):raise FileExistsError('ZERO_OR_OUTPUT_EXISTS')
        root.mkdir(parents=True,exist_ok=True);atomic_json(cfg_file,config)
    parent,parent_sha,completed,nxt=load_chain(args,root,source_model,args.phase)
    if args.phase=='return' and len([x for x in completed if x[0]=='outward'])!=len(outward_plan(args.target)):
        raise RuntimeError('ZERO_OR_OUTWARD_TARGET_NOT_REACHED')
    original,_=load_certified_model(args.ladder_output,args.device)
    original.eval().requires_grad_(False)
    legacy=deck(127)
    zs=int(json.loads((Path(args.zero_only_output)/'config.json').read_text())['settings']['seed'])
    zero_history={k:zero_probe(k,zs,args.zero_probe_anchors) for k in ('dev','test')}
    stages=[('outward',r) for r in outward_plan(args.target)]
    if args.phase=='return':stages += [('return',r) for r in return_plan(args.target)]
    done=0
    for p,s in stages[len(completed):]:
        if p!=args.phase:raise RuntimeError('ZERO_OR_INCOMPLETE_OUTWARD')
        if done>=args.max_stages:break
        final=p=='return' and s==0
        report=trained_stage(args,root,p,s,parent,parent_sha,original,zero_history,legacy,final)
        if report['status'] not in ('exposure_milestone','acquisition_milestone','reverse_milestone','final_conformity_passed'):
            result={'status':'halted_'+report['status'],'phase':p,'pending_radius':s,
              'accepted_stages':completed,'checkpoint':str(root/'stages'/f'{p}_{s}'/'latest.pt')}
            atomic_json(root/'manifest.json',result)
            print('ZERO_OR_SUMMARY='+json.dumps(result,sort_keys=True));return result
        checkpoint=root/'stages'/f'{p}_{s}'/'accepted.pt'
        model=torch.load(checkpoint,map_location='cpu',weights_only=True)
        parent.load_state_dict(model['model'],strict=True)
        parent_sha=sha256(checkpoint);completed.append((p,s));done+=1
        atomic_json(root/'manifest.json',{'status':'in_progress','accepted_stages':completed,'parent_sha256':parent_sha})
    ending=completed[-1] if completed else ('seed',ORIGIN_RADIUS)
    status=('final_conformity_passed' if ending==('return',0) else
        ('outward_exposure_completed' if args.outward_policy=='exposure' else 'outward_demonstrated_to_target')
        if ending==('outward',args.target) else 'stage_limit_reached')
    result={'status':status,'phase':args.phase,'accepted_stages':completed,
      'last_accepted':ending,'research_not_full_proof':True}
    atomic_json(root/'manifest.json',result)
    print('ZERO_OR_SUMMARY='+json.dumps(result,sort_keys=True));return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=('plan','outward','return'),default='outward')
    for name in ('ladder_output','zero_only_output','r16_output','r32_output','output'):
        p.add_argument('--'+name.replace('_','-'),default='')
    p.add_argument('--warmstart-stage',default='',help='Optional unfinished legacy ±64 stage; stop old trainer first')
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--target',type=int,default=OUTER_TARGET)
    p.add_argument('--max-stages',type=int,default=17)
    p.add_argument('--stage-steps',type=int,default=750,help='Outward steps per radius; default 750 to reach million-scale promptly')
    p.add_argument('--outward-policy',choices=('exposure','demonstrated'),default='exposure',
                   help='exposure: move outward after bounded steps; demonstrated: require accuracy/frontier gate')
    p.add_argument('--return-steps',type=int,default=1500)
    p.add_argument('--probe-every',type=int,default=250)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--legacy-batch-size',type=int,default=64)
    p.add_argument('--legacy-weight',type=float,default=1.0)
    p.add_argument('--inner-weight',type=float,default=1.5)
    p.add_argument('--zero-weight',type=float,default=3.0)
    p.add_argument('--lr',type=float,default=.00004)
    p.add_argument('--return-lr',type=float,default=.000015)
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--dev-anchors',type=int,default=2)
    p.add_argument('--dev-views',type=int,default=2)
    p.add_argument('--test-anchors',type=int,default=10)
    p.add_argument('--outer-per-anchor',type=int,default=24)
    p.add_argument('--historical-probe-cards',type=int,default=1024)
    p.add_argument('--zero-probe-anchors',type=int,default=20)
    p.add_argument('--outward-min-outer',type=float,default=.50)
    p.add_argument('--outward-min-frontier',type=float,default=.30)
    p.add_argument('--outward-min-gain',type=float,default=.03)
    p.add_argument('--outward-strong-outer',type=float,default=.80)
    p.add_argument('--min-frontier-cards',type=int,default=8)
    p.add_argument('--return-min-outer',type=float,default=.30)
    p.add_argument('--return-min-inner',type=float,default=.80)
    p.add_argument('--return-final-min-outer',type=float,default=.90)
    p.add_argument('--return-final-min-inner',type=float,default=.999)
    p.add_argument('--return-final-min-sign',type=float,default=.99)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--seed',type=int,default=20261026)
    p.add_argument('--resume',action='store_true')
    return p


def main(argv=None):return run(parser().parse_args(argv))


if __name__=='__main__':main()
