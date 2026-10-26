#!/usr/bin/env python3
"""Research-only outward radius ladder for four independent dual-bank EMP models.

Carries the full four-AdditionNet state from an existing dual-bank research run.
No merge, hidden-feature transfer, label oracle at inference, or TEST access.
A stage only advances after left.left reaches 80% and right.right reaches 50%
(on both outer and frontier DEV decks in both views) for consecutive probes.
Weights/optimizer histories are isolated from the original experiments.
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import shutil
from pathlib import Path

import torch
from torch import nn

from tools.nanojev_zero_line_ladder import atomic_checkpoint, atomic_json
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256
from tools.nanojev_zero_line_zero_only import BANDS, input_tensor
from tools import nanojev_zero_line_outward_return as outward
from tools import nanojev_emp_dual_bank as dual
from tools import nanojev_zero_line_sampled_radius_ladder as old
from tools.nanojev_emp_population import example_emphases

SCHEMA = 'nanojev.emp-dual-bank-outward-ladder.v1'


def gate(scores, left=.80, right=.50):
    """Different actual trainable banks govern the two EMPs' gate."""
    return all(row.get('nonzero', 0)>0 and row['within_5pct']>=threshold
               for name, threshold in (('left.left',left),('right.right',right))
               for kind in ('frontier','outer') for row in scores[name][kind])


def from_source(parent, left_source, right_source, model_state, device):
    banks=dual.construct_banks(parent,left_source,right_source)
    banks.load_state_dict(model_state,strict=True)
    return banks.to(device)


def source_config(args):
    src=Path(args.source_run).expanduser().resolve()
    root=Path(args.output).expanduser().resolve()
    if src==root:raise ValueError('EMP_LADDER_SOURCE_EQUALS_OUTPUT')
    cp=src/'latest.pt';sc=src/'config.json'
    if args.resume:
        if not (root/'config.json').is_file():
            raise FileNotFoundError('EMP_LADDER_CAMPAIGN_NOT_FOUND')
        cfg=json.loads((root/'config.json').read_text('utf-8'))
        if cfg.get('source_run')!=str(src):raise RuntimeError('EMP_LADDER_SOURCE_CHANGED')
        for key,file in (('source_checkpoint_sha256','source_dual.pt'),
                         ('source_config_sha256','source_config.json'),
                         ('left_source_sha256','source_left.pt'),
                         ('right_source_sha256','source_right.pt')):
            if sha256(root/file)!=cfg.get(key):raise RuntimeError('EMP_LADDER_SNAPSHOT_MUTATED')
        source_cfg=json.loads((root/'source_config.json').read_text('utf-8'))
        source=torch.load(root/'source_dual.pt',map_location='cpu',weights_only=True)
    else:
        if not cp.is_file() or not sc.is_file():raise FileNotFoundError('EMP_LADDER_SOURCE_MISSING')
        source=torch.load(cp,map_location='cpu',weights_only=True)
        source_cfg=json.loads(sc.read_text('utf-8'))
    if source.get('schema')!=dual.SCHEMA+'.checkpoint' or source_cfg.get('schema')!=dual.SCHEMA:
        raise RuntimeError('EMP_LADDER_SOURCE_SCHEMA_MISMATCH')
    if 'banks' not in source or 'controls' not in source or source_cfg.get('radius',0)<64:
        raise RuntimeError('EMP_LADDER_SOURCE_INCOMPLETE')
    controls=dual.checked_controls(source['controls'])
    if args.target<source_cfg['radius'] or args.target>outward.OUTER_TARGET:
        raise ValueError('EMP_LADDER_TARGET_NOT_FORWARD')
    if not args.resume:
        if root.exists() and any(root.iterdir()):raise FileExistsError('EMP_LADDER_OUTPUT_EXISTS')
        root.mkdir(parents=True,exist_ok=True)
        for inp,out in ((cp,'source_dual.pt'),(sc,'source_config.json'),
                        (src/'source_left.pt','source_left.pt'),(src/'source_right.pt','source_right.pt')):
            if not inp.is_file():raise FileNotFoundError(f'EMP_LADDER_SOURCE_MISSING:{inp}')
            shutil.copyfile(inp,root/out)
        cfg={'schema':SCHEMA,'source_run':str(src), 'start_radius':source_cfg['radius'],
             'source_checkpoint_sha256':sha256(root/'source_dual.pt'),
             'source_config_sha256':sha256(root/'source_config.json'),
             'left_source_sha256':sha256(root/'source_left.pt'),
             'right_source_sha256':sha256(root/'source_right.pt'),
             'seed':args.seed,'batch_size':args.batch_size,'probe_every':args.probe_every,
             'dev_cards':args.dev_cards,'breadth_anchors':args.breadth_anchors,
             'breadth_outer_per_anchor':args.breadth_outer_per_anchor,
             'target':args.target,'left_gate':args.left_gate,'right_gate':args.right_gate,
             'gate_streak':args.gate_streak,
             'source_step':source['step'], 'code_sha256':sha256(__file__),
             'dual_sha256':sha256(dual.__file__),'sampler_sha256':sha256(outward.__file__),
             'note':'Four independent whole backbones, research DEV gate only; not TEST certification.'}
        atomic_json(root/'config.json',cfg)
        atomic_json(root/'controls.json',controls)
    expected={'schema':SCHEMA,'source_run':str(src),'start_radius':source_cfg['radius'],
              'seed':args.seed,'batch_size':args.batch_size,'probe_every':args.probe_every,
              'dev_cards':args.dev_cards,'breadth_anchors':args.breadth_anchors,
              'breadth_outer_per_anchor':args.breadth_outer_per_anchor,
              'target':args.target,'left_gate':args.left_gate,'right_gate':args.right_gate,
              'gate_streak':args.gate_streak,
              'code_sha256':sha256(__file__),'dual_sha256':sha256(dual.__file__),
              'sampler_sha256':sha256(outward.__file__)}
    if any(cfg.get(key)!=value for key,value in expected.items()):raise RuntimeError('EMP_LADDER_CONFIG_DRIFT')
    # The snapshot, rather than the mutable original source run, is authoritative.
    source=torch.load(root/'source_dual.pt',map_location='cpu',weights_only=True)
    return root,cfg,source,dual.checked_source(root/'source_left.pt'),dual.checked_source(root/'source_right.pt')


def stage_probe(banks,args,radius):
    fixtures={'frontier':[outward.frontier_cards(radius,'dev',args.seed,v,args.dev_cards) for v in range(2)],
             'breadth':old.evaluation_cards(radius,'dev',args.seed,radius,
                         args.breadth_anchors,2,args.breadth_outer_per_anchor)}
    return dual.evaluate(banks,fixtures,args.device,args.eval_batch_size)


def save_stage(path,banks,opt,controls,events,rng,step,cursor,weights,streak,radius):
    atomic_checkpoint(path/'latest.pt',{
        'schema':SCHEMA+'.checkpoint','radius':radius,'step':step,
        'banks':banks.state_dict(),'optimizers':{name:o.state_dict() for name,o in opt.items()},
        'controls':copy.deepcopy(controls),'control_log':events,
        'rng':rng.getstate(),'torch_rng':torch.get_rng_state(),
        'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
        'cursor':cursor,'weights':weights,'streak':streak})


def advance_stage(args,root,parent,parent_sha,radius,parent_src_left,parent_src_right):
    path=root/'stages'/f'outward_{radius}'
    stage_config={'schema':SCHEMA+'.stage','radius':radius,'parent_sha256':parent_sha,
                  'campaign_config_sha256':sha256(root/'config.json')}
    if (path/'config.json').is_file():
        if json.loads((path/'config.json').read_text('utf-8'))!=stage_config:
            raise RuntimeError('EMP_LADDER_STAGE_CONFIG_DRIFT')
    else:
        if path.exists() and any(path.iterdir()):raise RuntimeError('EMP_LADDER_UNTRACKED_STAGE')
        path.mkdir(parents=True,exist_ok=True);atomic_json(path/'config.json',stage_config)
    base,_=load_certified_model(args.ladder_output,'cpu')
    banks=from_source(base,parent_src_left,parent_src_right,parent['banks'],args.device)
    controls=dual.checked_controls(json.loads((root/'controls.json').read_text('utf-8')))
    opt=dual.make_bank_optimizers(banks,controls)
    seed=args.seed+radius*13+16001
    torch.manual_seed(seed+109)
    if torch.cuda.is_available():torch.cuda.manual_seed_all(seed+109)
    rng=random.Random(seed)
    step=streak=cursor=0;weights=[1.]*len(BANDS);events=[]
    log=[]
    if (path/'latest.pt').is_file():
        if not args.resume:raise RuntimeError('EMP_LADDER_RESUME_REQUIRED')
        ckpt=torch.load(path/'latest.pt',map_location='cpu',weights_only=True)
        if ckpt['schema']!=SCHEMA+'.checkpoint' or ckpt['radius']!=radius:
            raise RuntimeError('EMP_LADDER_CHECKPOINT_DRIFT')
        banks.load_state_dict(ckpt['banks'],strict=True)
        for name,o in opt.items():o.load_state_dict(ckpt['optimizers'][name])
        step=ckpt['step'];streak=ckpt['streak'];cursor=ckpt['cursor'];weights=ckpt['weights']
        rng.setstate(ckpt['rng']);torch.set_rng_state(ckpt['torch_rng'])
        if torch.cuda.is_available() and ckpt['cuda_rng'] is not None:
            torch.cuda.set_rng_state_all(ckpt['cuda_rng'])
        controls_saved=dual.checked_controls(ckpt['controls'])
        if controls['revision']<controls_saved['revision'] or (controls['revision']==controls_saved['revision'] and controls!=controls_saved):
            raise RuntimeError('EMP_LADDER_CONTROLS_DRIFT')
        controls=controls_saved;events=ckpt['control_log']
        if (path/'history.json').is_file():log=json.loads((path/'history.json').read_text('utf-8'))
    # Gate check on stage entry, including the source's ±512 checkpoint.
    # This does not retroactively certify the source; we re-evaluate on this stage's frozen DEV cards.
    if step==0:
        scores,paired=stage_probe(banks,args,radius)
        good=gate(scores,args.left_gate,args.right_gate)
        rec={'step':0,'radius':radius,'qualified':good,'streak':0,'source_baseline':True,
             'banks':scores,'paired':paired}
        atomic_json(path/'baseline.json',rec)
        if good and args.gate_streak==1:
            save_stage(path,banks,opt,controls,events,rng,0,cursor,weights,1,radius)
            return accept_stage(path,rec)
    for i in range(step+1,args.stage_steps+1):
        if i==step+1 or (i-1)%args.probe_every==0:
            new=dual.checked_controls(json.loads((root/'controls.json').read_text('utf-8')))
            if new['revision']<controls['revision'] or (new['revision']==controls['revision'] and new!=controls):
                raise RuntimeError('EMP_LADDER_CONTROLS_DRIFT')
            if new!=controls:events.append({'effective_stage':radius,'effective_step':i,'controls':copy.deepcopy(new)})
            controls=new;dual.update_rates(opt,controls)
        rows,cursor=outward.generate_batch(rng,radius,args.seed,args.batch_size,weights,cursor,
                                           'outward',radius,args.target)
        x=input_tensor(rows,args.device)
        y=torch.tensor([r[2] for r in rows],device=args.device,dtype=torch.long)
        # All bank predictions computed independently on the same TRAIN cards.
        outputs={name:bank(x) for name,bank in banks.items()}
        paired_emphasis={emp:dual.right_answer_emphasis(rows,
            dual.decoded(outputs[f'{emp}.left']),dual.decoded(outputs[f'{emp}.right']),
            controls['banks'][f'{emp}.left']['right_answer_boost'],args.device)
            for emp in dual.EMPS}
        losses={}
        for name in dual.BANKS:
            settings=controls['banks'][name]
            if i%settings['train_every']:continue
            weight=example_emphases(rows,radius,settings,args.device)
            if name.endswith('.left'):
                weight=weight*paired_emphasis[name.split('.')[0]]
            loss=(dual.loss_per_card(outputs[name],y)*weight).sum()/weight.sum()
            if not torch.isfinite(loss):raise FloatingPointError('EMP_LADDER_NONFINITE_LOSS')
            optimizer=opt[name];optimizer.zero_grad(set_to_none=True)
            loss.backward();nn.utils.clip_grad_norm_(banks[name].parameters(),1.,error_if_nonfinite=True)
            optimizer.step();losses[name]=float(loss.detach())
        if i%args.probe_every and i!=args.stage_steps:continue
        scores,paired=stage_probe(banks,args,radius)
        good=gate(scores,args.left_gate,args.right_gate)
        streak=streak+1 if good else 0
        rec={'step':i,'radius':radius,'qualified':good,'streak':streak,
             'revision':controls['revision'],'losses':losses,'banks':scores,'paired':paired}
        log.append(rec)
        atomic_json(path/'history.json',log)
        dual.histogram_svg(path/'histogram.svg',scores)
        save_stage(path,banks,opt,controls,events,rng,i,cursor,weights,streak,radius)
        print('EMP_LADDER_PROBE='+json.dumps({'radius':radius,'step':i,'revision':controls['revision'],
            'qualified':good,'streak':streak,
            'left_frontier':[round(row['within_5pct'],4) for row in scores['left.left']['frontier']],
            'left_outer':[round(row['within_5pct'],4) for row in scores['left.left']['outer']],
            'right_frontier':[round(row['within_5pct'],4) for row in scores['right.right']['frontier']],
            'right_outer':[round(row['within_5pct'],4) for row in scores['right.right']['outer']],
            'tipping':{key:[round(row['tipping_violation_rate'],4) for row in scores[key]['frontier']] for key in dual.BANKS}},sort_keys=True),flush=True)
        if streak>=args.gate_streak:return accept_stage(path,rec)
    atomic_json(path/'result.json',{'status':'halted_budget_exhausted','radius':radius,
         'step':args.stage_steps,'gate_streak':streak,'resume_available':True})
    return None


def accept_stage(path,record):
    shutil.copyfile(path/'latest.pt',path/'accepted.pt')
    acceptance={'schema':SCHEMA+'.acceptance','radius':record['radius'],
       'step':record['step'],'status':'acquisition_milestone','qualified':True,
       'checkpoint_sha256':sha256(path/'accepted.pt'),'stage_config_sha256':sha256(path/'config.json')}
    atomic_json(path/'acceptance.json',acceptance)
    atomic_json(path/'result.json',acceptance)
    print('EMP_LADDER_STAGE_ACCEPTED='+json.dumps({'radius':record['radius'],'step':record['step'],
        'status':'research_acquisition_milestone'}),flush=True)
    return acceptance


def run(args):
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('EMP_LADDER_CUDA_UNAVAILABLE')
    if args.stage_steps<1 or args.max_stages<1 or args.probe_every<1 or args.cpu_threads<1 or args.batch_size<16 or args.dev_cards<1:
        raise ValueError('EMP_LADDER_INVALID_ARGS')
    torch.set_num_threads(args.cpu_threads)
    root,cfg,source,left,right=source_config(args)
    controls=dual.checked_controls(json.loads((root/'controls.json').read_text('utf-8')))
    del controls
    plan=[r for r in outward.outward_plan(args.target) if r>=cfg['start_radius']]
    parent=source;parent_sha=sha256(root/'source_dual.pt')
    completed=[];ran=0
    for radius in plan:
        path=root/'stages'/f'outward_{radius}'
        cp=path/'accepted.pt';accept_file=path/'acceptance.json'
        if cp.is_file() or accept_file.is_file():
            if not cp.is_file() or not accept_file.is_file():raise RuntimeError('EMP_LADDER_PARTIAL_ACCEPTANCE')
            record=json.loads(accept_file.read_text('utf-8'))
            cfg_stage=json.loads((path/'config.json').read_text('utf-8'))
            if (record.get('status')!='acquisition_milestone' or record['radius']!=radius
                or record.get('checkpoint_sha256')!=sha256(cp)
                or record.get('stage_config_sha256')!=sha256(path/'config.json')
                or cfg_stage['parent_sha256']!=parent_sha):raise RuntimeError('EMP_LADDER_ACCEPTANCE_DRIFT')
            parent=torch.load(cp,map_location='cpu',weights_only=True)
            if parent.get('schema')!=SCHEMA+'.checkpoint' or parent.get('radius')!=radius:
                raise RuntimeError('EMP_LADDER_ACCEPTED_STATE_DRIFT')
            parent_sha=sha256(cp)
            completed.append(radius)
            continue
        if ran>=args.max_stages:break
        accepted=advance_stage(args,root,parent,parent_sha,radius,left,right)
        if accepted is None:
            result={'status':'halted_budget_exhausted','pending_radius':radius,'accepted_radii':completed}
            print('EMP_LADDER_SUMMARY='+json.dumps(result,sort_keys=True),flush=True)
            return result
        parent=torch.load(cp,map_location='cpu',weights_only=True)
        parent_sha=sha256(cp)
        completed.append(radius);ran+=1
    status='outward_demonstrated_to_target' if completed and completed[-1]==plan[-1] else 'stage_limit_reached'
    result={'status':status,'accepted_radii':completed,'next_radius':next((v for v in plan if v not in completed),None),
            'warning':'DEV acquisition milestones only; no independent TEST conformity certification'}
    atomic_json(root/'manifest.json',result)
    print('EMP_LADDER_SUMMARY='+json.dumps(result,sort_keys=True),flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-run',required=True,help='Existing four-backbone experiment (source latest.pt, readout snapshots, config.json)')
    p.add_argument('--ladder-output',required=True,help='Original certified backbone for model architecture construction')
    p.add_argument('--output',required=True,help='New campaign root; never point at original dual run')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--target',type=int,default=4000000)
    p.add_argument('--stage-steps',type=int,default=10000)
    p.add_argument('--max-stages',type=int,default=16)
    p.add_argument('--probe-every',type=int,default=250)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--dev-cards',type=int,default=96)
    p.add_argument('--breadth-anchors',type=int,default=1)
    p.add_argument('--breadth-outer-per-anchor',type=int,default=4)
    p.add_argument('--seed',type=int,default=old.DEFAULT_SEED)
    p.add_argument('--left-gate',type=float,default=.80)
    p.add_argument('--right-gate',type=float,default=.50)
    p.add_argument('--gate-streak',type=int,default=2)
    p.add_argument('--cpu-threads',type=int,default=2)
    p.add_argument('--resume',action='store_true')
    return p


if __name__=='__main__':
    run(parser().parse_args())
