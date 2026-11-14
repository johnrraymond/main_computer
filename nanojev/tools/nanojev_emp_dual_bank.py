#!/usr/bin/env python3
"""Four fully independent AdditionNet banks: two (left/right) per source EMP.

Each EMP starts from its own source readout and the source shared backbone.
After cloning, no parameters, optimizers, or gradients are shared.  Supervision
uses only each bank's decoded arithmetic answer and the gold TRAIN answer;
the right bank's answer can select which cards the left bank emphasizes, but
its logits, hidden state, and gradient never teach the left bank.

Research-only: no promotion, routing, or sealed TEST evaluation.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import random
import shutil
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from tools.nanojev_addition_curriculum import target_digits
from tools.nanojev_zero_line_ladder import atomic_checkpoint, atomic_json, prediction_values
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256
from tools.nanojev_zero_line_zero_only import BANDS, input_tensor
from tools.nanojev_zero_line_distribution_monitor import summary_for
from tools.nanojev_emp_population import (
    SCHEMA as PARENT_SCHEMA, example_emphases, ratios_histogram, HIST_BOUNDS,
    Population,
)
from tools import nanojev_zero_line_outward_return as ladder
from tools import nanojev_zero_line_sampled_radius_ladder as old

SCHEMA = 'nanojev.emp-dual-bank-research.v1'
EMPS = ('left', 'right')
SIDES = ('left', 'right')
BANKS = tuple(f'{emp}.{side}' for emp in EMPS for side in SIDES)


def default_controls():
    return {'revision':0, 'banks':{
        'left.left': {'backbone_lr':1e-5,'readout_lr':1e-4,'train_every':1,
                      'frontier_boost':8.,'zero_boost':1.,'repeat_boost':1.,'right_answer_boost':4.},
        'left.right':{'backbone_lr':1e-5,'readout_lr':1e-4,'train_every':1,
                      'frontier_boost':2.,'zero_boost':1.,'repeat_boost':1.,'right_answer_boost':1.},
        'right.left':{'backbone_lr':1e-5,'readout_lr':1e-4,'train_every':1,
                      'frontier_boost':1.,'zero_boost':1.,'repeat_boost':1.,'right_answer_boost':3.},
        'right.right':{'backbone_lr':1e-5,'readout_lr':1e-4,'train_every':1,
                      'frontier_boost':1.,'zero_boost':1.,'repeat_boost':1.,'right_answer_boost':1.},
    }}


def checked_controls(obj):
    if not isinstance(obj,dict) or set(obj)!={'revision','banks'} or type(obj['revision']) is not int or obj['revision']<0:
        raise ValueError('EMP_DUAL_INVALID_CONTROLS')
    if not isinstance(obj['banks'],dict) or set(obj['banks'])!=set(BANKS):
        raise ValueError('EMP_DUAL_MISSING_BANK')
    expected={'backbone_lr','readout_lr','train_every','frontier_boost','zero_boost','repeat_boost','right_answer_boost'}
    for bank,values in obj['banks'].items():
        if not isinstance(values,dict) or set(values)!=expected:
            raise ValueError('EMP_DUAL_INVALID_BANK_CONTROL_'+bank)
        for field,value in values.items():
            if field=='train_every':
                if type(value) is not int or not 1<=value<=1000:raise ValueError('EMP_DUAL_INVALID_TRAIN_EVERY')
            elif type(value) not in (int,float) or not math.isfinite(value) or value<=0 or value>(.01 if field.endswith('_lr') else 100.):
                raise ValueError('EMP_DUAL_INVALID_CONTROL_'+bank+'_'+field)
        if bank.endswith('.right') and values['right_answer_boost']!=1.:
            raise ValueError('EMP_DUAL_ONLY_LEFT_CAN_USE_RIGHT_ANSWER')
    return obj


def loss_per_card(out,target):
    return F.cross_entropy(out['sign'],target.sign()+1,reduction='none')+F.cross_entropy(
        out['digits'].transpose(1,2),target_digits(target),reduction='none').mean(-1)


def right_answer_emphasis(cards,left_values,right_values,boost,device):
    """Answer-only right-to-left feedback. No right-side differentiable tensors.

    Right is a *selector*, never a label/teacher.  If its decoded answer has
    smaller absolute arithmetic error than the left bank's answer, increase
    the left bank's supervised CE against the TRUE sum on that TRAIN card.
    """
    if not(len(cards)==len(left_values)==len(right_values)):
        raise ValueError('EMP_DUAL_ANSWER_LENGTH_MISMATCH')
    return torch.tensor([
        float(boost) if abs(int(r)-int(c[2]))<abs(int(l)-int(c[2])) else 1.
        for c,l,r in zip(cards,left_values,right_values)
    ],device=device,dtype=torch.float32)


def make_bank_optimizers(banks,controls):
    result={}
    for key,model in banks.items():
        params=controls['banks'][key]
        readout=set(id(p) for layer in (model.sign_head,model.digit_head) for p in layer.parameters())
        core=[p for p in model.parameters() if id(p) not in readout]
        top=[p for p in model.parameters() if id(p) in readout]
        if not core or not top:raise RuntimeError('EMP_DUAL_BANK_PARAMETERS_MISSING')
        result[key]=torch.optim.AdamW([
            {'params':core,'lr':params['backbone_lr'],'section':'backbone'},
            {'params':top,'lr':params['readout_lr'],'section':'readout'}],weight_decay=0.)
    return result


def update_rates(optimizers,controls):
    for name,opt in optimizers.items():
        params=controls['banks'][name]
        for group in opt.param_groups:
            group['lr']=params['backbone_lr'] if group['section']=='backbone' else params['readout_lr']


class BankPopulation(nn.Module):
    """Names exposed as 'emp.side'; Torch's ModuleDict uses safe internal names."""
    def __init__(self):
        super().__init__()
        self.units=nn.ModuleDict()

    def __setitem__(self,name,model):
        if name not in BANKS:raise KeyError('EMP_DUAL_UNKNOWN_BANK')
        self.units[name.replace('.', '__')]=model

    def __getitem__(self,name):
        return self.units[name.replace('.', '__')]

    def __iter__(self):
        return iter(BANKS)

    def __len__(self):
        return len(BANKS)

    def items(self):
        return ((name,self[name]) for name in BANKS)

    def values(self):
        return (self[name] for name in BANKS)


def construct_banks(parent,left_source,right_source):
    """Independent full model copies prefilled from corresponding EMP heads."""
    outputs=BankPopulation()
    for emp,checkpoint in (('left',left_source),('right',right_source)):
        src=copy.deepcopy(parent)
        payload=Population(src)
        payload.load_state_dict(checkpoint['model'],strict=True)
        candidate=copy.deepcopy(payload.parent)
        candidate.sign_head.load_state_dict(payload.heads[emp].sign.state_dict())
        candidate.digit_head.load_state_dict(payload.heads[emp].digits.state_dict())
        candidate.requires_grad_(True)
        for side in SIDES:
            outputs[f'{emp}.{side}']=copy.deepcopy(candidate)
    ids=[set(map(id,m.parameters())) for m in outputs.values()]
    if any(ids[i]&ids[j] for i in range(4) for j in range(i+1,4)):
        raise RuntimeError('EMP_DUAL_BACKBONE_NOT_INDEPENDENT')
    return outputs


def decoded(out):
    val,_,_=prediction_values(out)
    return [int(x) for x in val.detach().cpu().tolist()]


def paired_outcomes(rows,left,right):
    counts={'both_correct':0,'left_only':0,'right_only':0,'both_wrong':0,'left_tip_only':0,'right_tip_only':0,'both_tip':0}
    for card,l,r in zip(rows,left,right):
        d=int(card[2])
        if d==0:
            lgood=l==0;rgood=r==0
        else:
            lgood=abs(l/d-1)<=.05+1e-12;rgood=abs(r/d-1)<=.05+1e-12
            ltip=l/d<=0;rtip=r/d<=0
            if ltip and rtip:counts['both_tip']+=1
            elif ltip:counts['left_tip_only']+=1
            elif rtip:counts['right_tip_only']+=1
        counts[('both_correct' if lgood else 'right_only') if rgood else ('left_only' if lgood else 'both_wrong')]+=1
    return {'total':len(rows),**counts}


def frozen_cards(args):
    # Exactly the same DEV decks used for the original population.
    return {'frontier':[ladder.frontier_cards(args.radius,'dev',args.seed,i,args.dev_cards) for i in range(2)],
            'breadth':old.evaluation_cards(args.radius,'dev',args.seed,args.radius,
                      args.breadth_anchors,2,args.breadth_outer_per_anchor)}


def evaluate(banks,fixtures,device,batch):
    for m in banks.values():m.eval()
    result={name:{'frontier':[],'outer':[],'inner':[],'zero':[],'repeated_digits':[]} for name in BANKS}
    paired={emp:{'frontier':[],'outer':[]} for emp in EMPS}
    with torch.inference_mode():
        for kind,views in fixtures.items():
            for rows in views:
                preds={n:[] for n in BANKS}
                for start in range(0,len(rows),batch):
                    x=input_tensor(rows[start:start+batch],device)
                    for name,model in banks.items():preds[name].extend(decoded(model(x)))
                for name in BANKS:
                    from tools.nanojev_emp_population import is_numeric_repeat
                    filters=({'frontier':lambda c:c[2]!=0} if kind=='frontier' else {
                        'outer':lambda c:abs(c[2])>32,
                        'inner':lambda c:0<abs(c[2])<=32,
                        'zero':lambda c:c[2]==0,
                        'repeated_digits':lambda c:is_numeric_repeat(c[0],c[1])})
                    for subset,check in filters.items():
                        selected=[(c,p) for c,p in zip(rows,preds[name]) if check(c)]
                        report=summary_for([c for c,_ in selected],[p for _,p in selected])
                        if subset=='frontier':
                            report['histogram']={'bounds':list(HIST_BOUNDS),'counts':ratios_histogram(rows,preds[name])}
                        result[name][subset].append(report)
                for emp in EMPS:
                    if kind=='frontier':
                        paired[emp]['frontier'].append(paired_outcomes(rows,preds[f'{emp}.left'],preds[f'{emp}.right']))
                    else:
                        sub=[i for i,c in enumerate(rows) if abs(c[2])>32]
                        paired[emp]['outer'].append(paired_outcomes([rows[i] for i in sub],
                            [preds[f'{emp}.left'][i] for i in sub],[preds[f'{emp}.right'][i] for i in sub]))
    return result,paired


def research_gate(scores,left_min=.80,right_min=.50):
    # This is an empirical diagnostic, not a certification or stop requirement.
    return all(row.get('nonzero',0)>0 and row['within_5pct']>=threshold
        for emp,threshold in (('left',left_min),('right',right_min))
        for subset in ('frontier','outer')
        for row in scores[f'{emp}.left'][subset])


def histogram_svg(path,scores):
    colors={'left.left':'#2563eb','left.right':'#93c5fd','right.left':'#c2410c','right.right':'#fdba74'}
    svg=['<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="650" viewBox="0 0 1024 650">',
         '<rect width="1024" height="650" fill="white"/>',
         '<text x="24" y="33" font-family="sans-serif" font-size="20">Independent EMP backbone prediction histograms</text>',
         '<text x="24" y="54" font-family="sans-serif" font-size="12">DEV A frontier · prediction / true residual · q=0 tipping point · q=1 target</text>']
    for index,name in enumerate(BANKS):
        counts=scores[name]['frontier'][0]['histogram']['counts']; top=88+135*index
        max_n=max([1,*counts]);w=54
        svg.append(f'<text x="25" y="{top}" font-size="15" font-family="sans-serif">{name}</text>')
        for i,n in enumerate(counts):
            h=75*n/max_n
            svg.append(f'<rect x="{28+i*w}" y="{top+82-h:.1f}" width="{w-3}" height="{h:.1f}" fill="{colors[name]}"/>')
        svg.append(f'<line x1="{28+4*w}" y1="{top+8}" x2="{28+4*w}" y2="{top+83}" stroke="#dc2626" stroke-dasharray="3 3"/>')
        svg.append(f'<line x1="{28+9.5*w}" y1="{top+8}" x2="{28+9.5*w}" y2="{top+83}" stroke="#059669" stroke-dasharray="3 3"/>')
        row=scores[name]['frontier'][0]
        svg.append(f'<text x="28" y="{top+105}" font-family="sans-serif" font-size="11">within5={row["within_5pct"]:.1%} · tipping={row["tipping_violation_rate"]:.1%}</text>')
    svg.append('</svg>');path.write_text('\n'.join(svg),encoding='utf-8')


def source_path_for(root,explicit):
    p=Path(explicit).expanduser().resolve() if explicit else root/'latest.pt'
    if not p.is_file():raise FileNotFoundError(f'EMP_DUAL_SOURCE_MISSING: {p}')
    return p


def checked_source(path):
    before=sha256(path)
    obj=torch.load(path,map_location='cpu',weights_only=True)
    if before!=sha256(path) or obj.get('schema')!=PARENT_SCHEMA+'.checkpoint' or 'model' not in obj:
        raise RuntimeError('EMP_DUAL_INVALID_SOURCE')
    return obj


def save_run_state(root,banks,opt,step,rng,cursor,weights,controls,events):
    atomic_checkpoint(root/'latest.pt',{
        'schema':SCHEMA+'.checkpoint','step':step,'banks':banks.state_dict(),
        'optimizers':{n:o.state_dict() for n,o in opt.items()},
        'rng':rng.getstate(),'torch_rng':torch.get_rng_state(),
        'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
        'cursor':cursor,'weights':weights,'controls':copy.deepcopy(controls),'control_log':events})


def run(args):
    if args.radius<64 or args.radius>4000000 or args.steps<1 or args.probe_every<1 or args.batch_size<4 or args.dev_cards<1 or args.cpu_threads<1:
        raise ValueError('EMP_DUAL_INVALID_ARGS')
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('EMP_DUAL_CUDA_UNAVAILABLE')
    torch.set_num_threads(args.cpu_threads)
    root=Path(args.output).expanduser().resolve()
    population_root=Path(args.source_population).expanduser().resolve()
    config_path=root/'config.json';controls_path=root/'controls.json'
    if args.resume:
        cfg=json.loads(config_path.read_text('utf-8'))
        expected={'schema':SCHEMA,'source_population':str(population_root),'radius':args.radius,
            'seed':args.seed,'batch_size':args.batch_size,'probe_every':args.probe_every,
            'dev_cards':args.dev_cards,'breadth_anchors':args.breadth_anchors,
            'breadth_outer_per_anchor':args.breadth_outer_per_anchor,
            'code_sha256':sha256(__file__),'left_gate':args.left_gate,'right_gate':args.right_gate}
        if {k:cfg.get(k) for k in expected}!=expected:raise RuntimeError('EMP_DUAL_CONFIG_DRIFT')
        if sha256(root/'source_population_config.json')!=cfg.get('source_population_config_sha256'):
            raise RuntimeError('EMP_DUAL_PARENT_CONFIG_SNAPSHOT_CHANGED')
        sources={emp:checked_source(root/f'source_{emp}.pt') for emp in EMPS}
        for emp in EMPS:
            if sha256(root/f'source_{emp}.pt')!=cfg['sources'][emp]['sha256']:
                raise RuntimeError('EMP_DUAL_SNAPSHOT_CHANGED')
    else:
        inputs={'left':source_path_for(population_root,args.left_checkpoint),
                'right':source_path_for(population_root,args.right_checkpoint)}
        if root.exists() and any(root.iterdir()):raise FileExistsError('EMP_DUAL_OUTPUT_EXISTS')
        root.mkdir(parents=True,exist_ok=True)
        sources={}
        for emp in EMPS:
            sources[emp]=checked_source(inputs[emp])
            shutil.copyfile(inputs[emp],root/f'source_{emp}.pt')
            if sha256(root/f'source_{emp}.pt')!=sha256(inputs[emp]):raise RuntimeError('EMP_DUAL_SOURCE_COPY_CHANGED')
        # Source experiment identity is pinned at initialization. Its original
        # source checkpoints may subsequently change; the copied bytes remain
        # the only initialization authority for this separate experiment.
        pop_cfg_file=population_root/'config.json'
        if not pop_cfg_file.is_file():raise FileNotFoundError('EMP_DUAL_PARENT_CONFIG_MISSING')
        pop_cfg=json.loads(pop_cfg_file.read_text('utf-8'))
        if pop_cfg.get('schema')!=PARENT_SCHEMA or pop_cfg.get('radius')!=args.radius:
            raise RuntimeError('EMP_DUAL_PARENT_CONFIG_MISMATCH')
        shutil.copyfile(pop_cfg_file,root/'source_population_config.json')
        cfg={'schema':SCHEMA,'source_population':str(population_root),'radius':args.radius,
            'seed':args.seed,'batch_size':args.batch_size,'probe_every':args.probe_every,
            'dev_cards':args.dev_cards,'breadth_anchors':args.breadth_anchors,
            'breadth_outer_per_anchor':args.breadth_outer_per_anchor,
            'code_sha256':sha256(__file__),'left_gate':args.left_gate,'right_gate':args.right_gate,
            'source_population_config_sha256':sha256(root/'source_population_config.json'),
            'sources':{emp:{'original':str(inputs[emp]),'sha256':sha256(inputs[emp]),
                      'source_step':sources[emp]['step']} for emp in EMPS},
            'warning':'research-only; uses DEV, no sealed TEST or production promotion; original population stores latest, not historical-best head snapshots'}
        atomic_json(config_path,cfg)
        atomic_json(controls_path,default_controls())
    parent,_=load_certified_model(args.ladder_output,'cpu')
    banks=construct_banks(parent,sources['left'],sources['right']).to(args.device)
    controls=checked_controls(json.loads(controls_path.read_text('utf-8')))
    optimizers=make_bank_optimizers(banks,controls)
    rng=random.Random(args.seed+args.radius*13)
    torch.manual_seed(args.seed+args.radius*13+109)
    if torch.cuda.is_available():torch.cuda.manual_seed_all(args.seed+args.radius*13+109)
    cursor=0;weights=[1.]*len(BANDS);start=0;events=[];history=[]
    if args.resume:
        prior=torch.load(root/'latest.pt',map_location='cpu',weights_only=True)
        if prior.get('schema')!=SCHEMA+'.checkpoint':raise RuntimeError('EMP_DUAL_BAD_CHECKPOINT')
        banks.load_state_dict(prior['banks'],strict=True)
        for n,o in optimizers.items():o.load_state_dict(prior['optimizers'][n])
        rng.setstate(prior['rng']);torch.set_rng_state(prior['torch_rng'])
        if torch.cuda.is_available() and prior['cuda_rng'] is not None:torch.cuda.set_rng_state_all(prior['cuda_rng'])
        start=prior['step'];cursor=prior['cursor'];weights=prior['weights'];events=prior['control_log']
        prev=prior['controls']
        if controls['revision']<prev['revision'] or (controls['revision']==prev['revision'] and controls!=prev):
            raise RuntimeError('EMP_DUAL_CONTROL_DRIFT')
        controls=copy.deepcopy(prev)
        if (root/'history.json').is_file():history=json.loads((root/'history.json').read_text('utf-8'))
    fixtures=frozen_cards(args)
    for step in range(start+1,args.steps+1):
        if step==start+1 or (step-1)%args.probe_every==0:
            new=checked_controls(json.loads(controls_path.read_text('utf-8')))
            if new['revision']<controls['revision'] or (new['revision']==controls['revision'] and new!=controls):
                raise RuntimeError('EMP_DUAL_CONTROL_DRIFT')
            if new!=controls:events.append({'effective_step':step,'controls':copy.deepcopy(new)})
            controls=new;update_rates(optimizers,controls)
        rows,cursor=ladder.generate_batch(rng,args.radius,args.seed,args.batch_size,weights,cursor,
                                           'outward',args.radius,4000000)
        x=input_tensor(rows,args.device)
        y=torch.tensor([c[2] for c in rows],device=args.device,dtype=torch.long)
        # All four output heads infer independently; right answers only determine
        # the left bank's per-card *weight*. True y supplies every loss target.
        outputs={name:bank(x) for name,bank in banks.items()}
        boost_by_emp={}
        for emp in EMPS:
            boost_by_emp[emp]=right_answer_emphasis(rows,
                decoded(outputs[f'{emp}.left']),decoded(outputs[f'{emp}.right']),
                controls['banks'][f'{emp}.left']['right_answer_boost'],args.device)
        losses={}
        for name in BANKS:
            params=controls['banks'][name]
            if step%params['train_every']!=0:continue
            bank=banks[name];opt=optimizers[name]
            cards_weights=example_emphases(rows,args.radius,params,args.device)
            if name.endswith('.left'):
                cards_weights=cards_weights*boost_by_emp[name.split('.')[0]]
            loss=(loss_per_card(outputs[name],y)*cards_weights).sum()/cards_weights.sum()
            if not torch.isfinite(loss):raise FloatingPointError('EMP_DUAL_NONFINITE_LOSS')
            opt.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(bank.parameters(),1.,error_if_nonfinite=True)
            opt.step()
            losses[name]=float(loss.detach())
        if step%args.probe_every!=0 and step!=args.steps:continue
        scores,paired=evaluate(banks,fixtures,args.device,args.eval_batch_size)
        record={'step':step,'revision':controls['revision'],'bank_losses':losses,
                'banks':scores,'paired':paired,
                'qualified_80_50':research_gate(scores,args.left_gate,args.right_gate)}
        history.append(record)
        atomic_json(root/'history.json',history)
        histogram_svg(root/'histogram.svg',scores)
        save_run_state(root,banks,optimizers,step,rng,cursor,weights,controls,events)
        print('EMP_DUAL_BANK_PROBE='+json.dumps({'step':step,'revision':controls['revision'],
            'qualified_80_50':record['qualified_80_50'],
            'banks':{n:{'frontier_5pct':[round(r['within_5pct'],4) for r in scores[n]['frontier']],
                         'outer_5pct':[round(r['within_5pct'],4) for r in scores[n]['outer']],
                         'tipping':[round(r['tipping_violation_rate'],4) for r in scores[n]['frontier']]}
                     for n in BANKS},'paired':paired},sort_keys=True),flush=True)
        if record['qualified_80_50'] and args.stop_on_gate:break
    return history[-1] if history else {'status':'no_work','step':start}


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-population',required=True)
    p.add_argument('--left-checkpoint',help='Optional preserved snapshot of left EMP; defaults to source population latest.pt')
    p.add_argument('--right-checkpoint',help='Optional preserved snapshot of right EMP; defaults to source population latest.pt')
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--radius',type=int,default=512)
    p.add_argument('--steps',type=int,default=2000)
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
    run(parser().parse_args())
