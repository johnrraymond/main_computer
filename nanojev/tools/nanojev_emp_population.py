#!/usr/bin/env python3
"""Experimental simultaneous EMP readouts over ONE NanoJev arithmetic backbone.

Each head sees the same backbone hidden state and target but uses an independent
readout and a configurable TRAIN-only per-card loss emphasis.  Editable controls
are loaded at probe boundaries.  This experiment does NOT alter an existing
outward ladder or certify arithmetic; it does not perform inference routing.

'lexical_repeats' is deliberately unsupported: AdditionNet takes two integers,
not token sequences.  'repeated_digits' is the separately named numeric proxy.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from tools.nanojev_addition_curriculum import target_digits
from tools.nanojev_zero_line_ladder import atomic_checkpoint, atomic_json, prediction_values
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256
from tools.nanojev_zero_line_zero_only import BANDS, input_tensor, legacy_loss
from tools import nanojev_zero_line_outward_return as ladder
from tools import nanojev_zero_line_sampled_radius_ladder as old
from tools.nanojev_zero_line_distribution_monitor import summary_for

SCHEMA = 'nanojev.emp-population-research.v1'
HEADS = ('left', 'right', 'zero', 'repeated_digits')
HIST_BOUNDS = (-3., -1., -.25, 0., .25, .5, .75, .9, .95, 1.05, 1.1, 1.25, 1.5, 2., 3.)


def default_controls():
    return {'revision': 0, 'backbone_lr': 0.00001, 'heads': {
        'left': {'lr': 0.0001, 'loss_weight': 3., 'frontier_boost': 4., 'zero_boost': 1., 'repeat_boost': 1.},
        'right': {'lr': 0.0001, 'loss_weight': 1., 'frontier_boost': 1., 'zero_boost': 1., 'repeat_boost': 1.},
        'zero': {'lr': 0.0001, 'loss_weight': 1., 'frontier_boost': 1., 'zero_boost': 6., 'repeat_boost': 1.},
        'repeated_digits': {'lr': 0.0001, 'loss_weight': 1., 'frontier_boost': 1., 'zero_boost': 1., 'repeat_boost': 6.}
    }}


def checked_controls(obj):
    if not isinstance(obj, dict) or set(obj)!={'revision','backbone_lr','heads'}:
        raise ValueError('EMP_CONTROLS_BAD_KEYS')
    if type(obj['revision']) is not int or obj['revision']<0 or not isinstance(obj['heads'],dict) or set(obj['heads'])!=set(HEADS):
        raise ValueError('EMP_CONTROLS_BAD_HEADS')
    for name,v in [('backbone_lr',obj['backbone_lr'])]:
        if type(v) not in (float,int) or not math.isfinite(v) or not 0<v<=.01:raise ValueError('EMP_CONTROLS_BAD_'+name.upper())
    expected={'lr','loss_weight','frontier_boost','zero_boost','repeat_boost'}
    for name,params in obj['heads'].items():
        if not isinstance(params,dict) or set(params)!=expected:raise ValueError('EMP_CONTROLS_HEAD_SCHEMA_'+name)
        for key,value in params.items():
            if type(value) not in (float,int) or not math.isfinite(value) or not 0<value<=100:
                raise ValueError('EMP_CONTROLS_BAD_VALUE_'+name+'_'+key)
            if key=='lr' and value>.01:raise ValueError('EMP_CONTROLS_LR_TOO_HIGH')
    return obj


def is_numeric_repeat(a,b):
    return any(s[i]==s[i+1] for x in (a,b) for s in [str(abs(int(x)))] for i in range(len(s)-1))


def example_emphases(cards, radius, control, device):
    result=[]
    for a,b,d,*_ in cards:
        factor=1.
        if abs(d)>max(32,radius//2): factor*=control['frontier_boost']
        if abs(d)<=32:factor*=control['zero_boost']
        if is_numeric_repeat(a,b):factor*=control['repeat_boost']
        result.append(factor)
    return torch.tensor(result,dtype=torch.float32,device=device)


class Head(nn.Module):
    def __init__(self, src):
        super().__init__()
        self.sign=copy.deepcopy(src.sign_head)
        self.digits=copy.deepcopy(src.digit_head)

    def forward(self,state):
        return {'sign':self.sign(state),'digits':self.digits(state).reshape(-1,7,10)}


class Population(nn.Module):
    def __init__(self,parent):
        super().__init__()
        self.parent=parent
        self.heads=nn.ModuleDict({name:Head(parent) for name in HEADS})
        # The original sign/digit/regression readouts are still used by legacy
        # rehearsal, not by any of the new specialization heads.
        for n,p in self.parent.named_parameters():
            p.requires_grad_(n.startswith('backbone.') or n.startswith('norm.') or n in ('query','pos'))

    def shared_state(self,x):
        # Identical state computation to AdditionNet.forward; only the readouts
        # are new. Inputs contain operands only (no label-based inference).
        model=self.parent
        tokens=model.input(x)
        state=model.query.expand(len(x),-1)
        length=tokens.shape[1]+1
        mask=torch.ones(length,length,device=x.device,dtype=torch.bool).triu(1)
        for _ in range(model.rounds):
            ctx=torch.cat((tokens,state[:,None]),dim=1)+model.pos[:length]
            state=model.norm(model.backbone(ctx,mask=mask)[:,-1])
        return state

    def forward(self,x):
        hidden=self.shared_state(x)
        return {name:head(hidden) for name,head in self.heads.items()}


def make_optimizer(model, controls):
    groups=[{'params':[p for p in model.parent.parameters() if p.requires_grad],
             'lr':controls['backbone_lr'],'head_name':'backbone'}]
    for name in HEADS:
        groups.append({'params':list(model.heads[name].parameters()),
                       'lr':controls['heads'][name]['lr'],'head_name':name})
    return torch.optim.AdamW(groups,weight_decay=0.)


def apply_learning_rates(opt, controls):
    for g in opt.param_groups:
        name=g['head_name'];g['lr']=controls['backbone_lr'] if name=='backbone' else controls['heads'][name]['lr']


def training_loss(pred,y,weights):
    losses=F.cross_entropy(pred['sign'],y.sign()+1,reduction='none')+F.cross_entropy(
        pred['digits'].transpose(1,2),target_digits(y),reduction='none').mean(-1)
    return (losses*weights).sum()/weights.sum()


def ratios_histogram(cards, predictions):
    count=[0]*(len(HIST_BOUNDS)+1)
    for c,p in zip(cards,predictions):
        d=int(c[2]);pred=int(p)
        if d==0:continue
        q=pred/d
        # Labels mark intervals (-inf,b0), [b0,b1), ... [blast,inf).
        idx=next((i for i,cut in enumerate(HIST_BOUNDS) if q<cut),len(HIST_BOUNDS))
        count[idx]+=1
    return count


def evaluate_head(model,head_name,fixtures,device,batch_size):
    model.eval()
    result=[]
    with torch.inference_mode():
        for rows in fixtures:
            preds=[]
            for start in range(0,len(rows),batch_size):
                x=input_tensor(rows[start:start+batch_size],device)
                out=model(x)[head_name]
                p,_,_=prediction_values(out)
                preds.extend(int(y) for y in p.detach().cpu().tolist())
            s=summary_for(rows,preds)
            s['histogram']={'bounds':list(HIST_BOUNDS),'counts':ratios_histogram(rows,preds)}
            result.append(s)
    return result



def population_evaluation(model, fixtures, device, batch_size):
    """One backbone evaluation per fixture batch, not per-head re-encoding.

    Every EMP uses identical cards.  Breadth reports include separate numerical
    tasks; the 80/50 gate evaluates *both* true outer and true frontier cases.
    """
    result={name:{'frontier':[], 'outer':[], 'inner':[], 'zero':[], 'repeated_digits':[]}
            for name in HEADS}
    model.eval()
    for category,views in fixtures.items():
        for rows in views:
            pred={name:[] for name in HEADS}
            with torch.inference_mode():
                for start in range(0,len(rows),batch_size):
                    x=input_tensor(rows[start:start+batch_size],device)
                    outputs=model(x)
                    for name,out in outputs.items():
                        decoded,_,_=prediction_values(out)
                        pred[name].extend(int(v) for v in decoded.detach().cpu().tolist())
            for name in HEADS:
                if category=='frontier':
                    ss=summary_for(rows,pred[name])
                    ss['histogram']={'bounds':list(HIST_BOUNDS),'counts':ratios_histogram(rows,pred[name])}
                    result[name]['frontier'].append(ss)
                else:
                    for key,condition in {
                        'outer':lambda c:abs(c[2])>32,
                        'inner':lambda c:0<abs(c[2])<=32,
                        'zero':lambda c:c[2]==0,
                        'repeated_digits':lambda c:is_numeric_repeat(c[0],c[1])
                    }.items():
                        sub=[(c,p) for c,p in zip(rows,pred[name]) if condition(c)]
                        subrows=[v[0] for v in sub];subpred=[v[1] for v in sub]
                        ss=summary_for(subrows,subpred)
                        if key=='zero':ss['zero_exact']=1-ss['zero_errors']/max(1,ss['zero_cards'])
                        result[name][key].append(ss)
    return result

def meets_gate(report,minimum):
    return all(row.get('nonzero',0)>0 and row['within_5pct']>=minimum for row in report)


def population_gate(reports,left_gate=.80,right_gate=.50):
    return all(meets_gate(reports[name][category],floor)
               for name,floor in (('left',left_gate),('right',right_gate))
               for category in ('frontier','outer'))


def write_svg(path,record):
    # Snapshot of normalized prediction-ratio histogram. Every head uses the
    # identical x bins and held-out cards, so the bars are comparable.
    palette=['#2563eb','#d97706','#0d9488','#7c3aed']
    parts=['<svg xmlns="http://www.w3.org/2000/svg" width="1040" height="610" viewBox="0 0 1040 610">',
           '<rect width="1040" height="610" fill="#fff"/>',
           '<text x="28" y="32" font-size="19" font-family="sans-serif">EMP prediction/target histogram — DEV view 0</text>',
           '<text x="28" y="52" font-size="12" font-family="sans-serif">Horizontal axis: normalized prediction ratio q (correct target = 1; tipping point = 0)</text>']
    k=len(HIST_BOUNDS)+1
    for n,name in enumerate(HEADS):
        counts=record['heads'][name]['frontier'][0]['histogram']['counts']
        left=42;top=84+128*n
        max_count=max(1,max(counts));w=57
        parts.append(f'<text x="{left}" y="{top}" font-family="sans-serif" font-size="15">{name}</text>')
        parts.append(f'<line x1="{left+4*w}" y1="{top+8}" x2="{left+4*w}" y2="{top+90}" stroke="#dc2626" stroke-dasharray="3 3" stroke-width="1.5"/>')
        parts.append(f'<line x1="{left+9.5*w}" y1="{top+8}" x2="{left+9.5*w}" y2="{top+90}" stroke="#0d9488" stroke-dasharray="3 3" stroke-width="1.5"/>')
        parts.append(f'<text x="{left+4*w+4}" y="{top+14}" font-size="11" fill="#dc2626">q=0</text>')
        parts.append(f'<text x="{left+9.5*w+4}" y="{top+14}" font-size="11" fill="#0d9488">q=1</text>')
        for i,v in enumerate(counts):
            height=80*v/max_count
            x=left+i*w
            parts.append(f'<rect x="{x}" y="{top+88-height:.1f}" width="{w-3}" height="{height:.1f}" fill="{palette[n]}" fill-opacity="0.78"/>')
        part=record['heads'][name]['frontier'][0]
        parts.append(f'<text x="{left}" y="{top+107}" font-family="sans-serif" font-size="11">n={part["nonzero"]} · within 5%={part["within_5pct"]:.1%} · tipping failures={part["tipping_violation_rate"]:.1%}</text>')
    parts.append('</svg>')
    path.write_text('\n'.join(parts),encoding='utf-8')


def stage_source(args):
    path=Path(args.source_stage).resolve()
    checkpoint=path/'latest.pt'
    stcfg=path/'config.json'
    rootcfg=path.parent.parent/'config.json'
    if not all(p.is_file() for p in (checkpoint,stcfg,rootcfg)):
        raise FileNotFoundError('EMP_SOURCE_FILES_MISSING')
    sc=json.loads(stcfg.read_text('utf-8'))
    if sc.get('schema')!=ladder.SCHEMA+'.stage' or sc.get('phase')!='outward' or sc.get('stage')!=args.radius:
        raise RuntimeError('EMP_SOURCE_WRONG_STAGE')
    if sc.get('root_sha256')!=sha256(rootcfg):raise RuntimeError('EMP_SOURCE_CONFIG_DRIFT')
    before=sha256(checkpoint)
    state=torch.load(checkpoint,map_location='cpu',weights_only=True)
    if before!=sha256(checkpoint):raise RuntimeError('EMP_SOURCE_CHANGED')
    if state.get('schema')!=ladder.SCHEMA+'.checkpoint' or state.get('stage')!=args.radius or state.get('phase')!='outward':
        raise RuntimeError('EMP_SOURCE_WRONG_CHECKPOINT')
    return state,{'checkpoint':str(checkpoint),'sha256':before,'stage_config_sha256':sha256(stcfg),
                  'root_config_sha256':sha256(rootcfg),'step':state['step']}


def load_controls(path,old_state=None):
    if not path.is_file():raise RuntimeError('EMP_CONTROLS_MISSING')
    current=checked_controls(json.loads(path.read_text('utf-8')))
    if old_state is not None:
        if current['revision']<old_state['revision']:
            raise RuntimeError('EMP_CONTROLS_REVISION_REGRESSION')
        if current['revision']==old_state['revision'] and current!=old_state:
            raise RuntimeError('EMP_CONTROLS_EDIT_REQUIRES_REVISION_INCREMENT')
    return current


def run(args):
    if (args.steps<1 or args.probe_every<1 or args.batch_size<16 or args.radius<64
        or args.radius>4000000 or args.seed<0 or args.dev_cards<1
        or args.breadth_anchors<1 or args.breadth_outer_per_anchor<1):
        raise ValueError('EMP_INVALID_ARGS')
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('EMP_CUDA_UNAVAILABLE')
    torch.set_num_threads(args.cpu_threads)
    root=Path(args.output).resolve();config_path=root/'config.json'; controls_path=root/'controls.json'
    # Source is immutable to prevent silent comparison against moving controls.
    source, provenance=stage_source(args)
    cfg={'schema':SCHEMA,'source':provenance,'radius':args.radius,'seed':args.seed,
         'batch_size':args.batch_size,'probe_every':args.probe_every,
         'dev_cards':args.dev_cards,'breadth_anchors':args.breadth_anchors,
         'breadth_outer_per_anchor':args.breadth_outer_per_anchor,
         'left_gate':args.left_gate,'right_gate':args.right_gate,
         'code_sha256':sha256(__file__),
         'disclaimer':'experimental heads and tuning DEV; never sealed certification or production inference'}
    if args.resume:
        if not config_path.is_file() or json.loads(config_path.read_text('utf-8'))!=cfg:
            raise RuntimeError('EMP_CONFIG_DRIFT')
    else:
        if root.exists() and any(root.iterdir()):raise FileExistsError('EMP_OUTPUT_EXISTS')
        root.mkdir(parents=True,exist_ok=True)
        atomic_json(config_path,cfg)
        atomic_json(controls_path,default_controls())
    parent,_=load_certified_model(args.ladder_output,args.device)
    parent.load_state_dict(source['model'],strict=True)
    model=Population(parent).to(args.device)
    controls=load_controls(controls_path)
    opt=make_optimizer(model,controls)
    rng=random.Random(args.seed+args.radius*13)
    torch.manual_seed(args.seed+args.radius*13+59)
    start=0;cursor=0;weights=[1.]*len(BANDS)
    control_log=[];history=[]
    if args.resume:
        ckpt=torch.load(root/'latest.pt',map_location='cpu',weights_only=True)
        if ckpt.get('schema')!=SCHEMA+'.checkpoint':raise RuntimeError('EMP_BAD_CHECKPOINT')
        model.load_state_dict(ckpt['model'],strict=True);opt.load_state_dict(ckpt['optimizer'])
        rng.setstate(ckpt['rng']);torch.set_rng_state(ckpt['torch_rng'])
        if torch.cuda.is_available() and ckpt.get('cuda_rng') is not None:
            torch.cuda.set_rng_state_all(ckpt['cuda_rng'])
        start=ckpt['step'];cursor=ckpt['cursor'];weights=ckpt['weights']
        prev=ckpt['controls']
        if controls['revision']==prev['revision'] and controls!=prev:
            raise RuntimeError('EMP_CONTROLS_EDIT_REQUIRES_REVISION_INCREMENT')
        if controls['revision']<prev['revision']:
            raise RuntimeError('EMP_CONTROLS_REVISION_REGRESSION')
        control_log=ckpt['control_log']
        # Resume optimizer at the exact checkpoint controls, then apply any
        # operator revision at the first new step with an auditable event.
        controls=copy.deepcopy(prev)
        if (root/'history.json').is_file():history=json.loads((root/'history.json').read_text('utf-8'))
    origin,_=load_certified_model(args.ladder_output,args.device)
    origin.eval().requires_grad_(False)
    fixtures={
      'frontier':[ladder.frontier_cards(args.radius,'dev',args.seed,i,args.dev_cards) for i in range(2)],
      'breadth':old.evaluation_cards(args.radius,'dev',args.seed,args.radius,
                                    args.breadth_anchors,2,args.breadth_outer_per_anchor)
    }
    # All heads are evaluated on BOTH populations and BOTH task groups.
    for step in range(start+1,args.steps+1):
        if step==start+1 or (step-1)%args.probe_every==0:
            new=load_controls(controls_path, controls if step!=start+1 else None)
            if new!=controls:
                control_log.append({'effective_step':step,'controls':copy.deepcopy(new)})
            controls=new;apply_learning_rates(opt,controls)
        rows,cursor=ladder.generate_batch(rng,args.radius,args.seed,args.batch_size,weights,cursor,
                                           'outward',args.radius,4000000)
        x=input_tensor(rows,args.device)
        y=torch.tensor([c[2] for c in rows],dtype=torch.long,device=args.device)
        model.train();pred=model(x)
        per_head={}
        for name in HEADS:
            w=example_emphases(rows,args.radius,controls['heads'][name],args.device)
            per_head[name]=training_loss(pred[name],y,w)
        total=sum(controls['heads'][name]['loss_weight']*per_head[name] for name in HEADS)/sum(
            controls['heads'][name]['loss_weight'] for name in HEADS)
        # Historic rehearsal gradient passes through the SAME shared body.
        xx=torch.randint(-127,128,(64,2),device=args.device)
        loss=total + .25*legacy_loss(model.parent,origin,xx)
        if not torch.isfinite(loss):raise FloatingPointError('EMP_NONFINITE_LOSS')
        opt.zero_grad(set_to_none=True);loss.backward()
        nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad),1.,error_if_nonfinite=True)
        opt.step()
        if step%args.probe_every and step<args.steps:continue
        heads=population_evaluation(model,fixtures,args.device,args.eval_batch_size)
        passed=population_gate(heads,args.left_gate,args.right_gate)
        record={'step':step,'loss':float(loss.detach()),'revision':controls['revision'],
                'heads':heads,'qualified_80_50':passed,'train_head_losses':{n:float(v.detach()) for n,v in per_head.items()}}
        history.append(record)
        atomic_json(root/'history.json',history)
        write_svg(root/'histogram.svg',record)
        state={'schema':SCHEMA+'.checkpoint','model':model.state_dict(),'optimizer':opt.state_dict(),
               'step':step,'rng':rng.getstate(),'torch_rng':torch.get_rng_state(),
               'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
               'weights':weights,'cursor':cursor,'controls':copy.deepcopy(controls),
               'control_log':control_log}
        atomic_checkpoint(root/'latest.pt',state)
        print('EMP_POPULATION_PROBE='+json.dumps({'step':step,'loss':round(record['loss'],6),
            'revision':controls['revision'],'qualified_80_50':passed,
            'heads':{n:{'frontier_5pct':[round(v['within_5pct'],4) for v in views['frontier']],
                         'outer_5pct':[round(v['within_5pct'],4) for v in views['outer']],
                         'tipping':[round(v['tipping_violation_rate'],4) for v in views['frontier']],
                         'q05':[round(v['q05'],4) for v in views['frontier']]} for n,views in heads.items()}},sort_keys=True),flush=True)
        if passed and args.stop_on_gate:break
    return history[-1] if history else {'step':start,'status':'nothing_to_do'}


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-stage',required=True)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--radius',type=int,default=512)
    p.add_argument('--steps',type=int,default=3000)
    p.add_argument('--probe-every',type=int,default=250)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--dev-cards',type=int,default=96)
    p.add_argument('--breadth-anchors',type=int,default=1)
    p.add_argument('--breadth-outer-per-anchor',type=int,default=4)
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--seed',type=int,default=old.DEFAULT_SEED)
    p.add_argument('--left-gate',type=float,default=.80)
    p.add_argument('--right-gate',type=float,default=.50)
    p.add_argument('--cpu-threads',type=int,default=2)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--stop-on-gate',action='store_true')
    return p


if __name__=='__main__':
    args=parser().parse_args()
    run(args)
