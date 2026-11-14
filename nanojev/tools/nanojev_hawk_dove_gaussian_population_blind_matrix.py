#!/usr/bin/env python3
"""Oracle-blind, matrix-held-out 2,000-bank Hawk–Dove certification experiment.

Research control, NOT a claim to raw-text operator understanding. T01/T02 are
explicitly blinded variants; T00 cannot be evaluated after removing its only
numerical information. Every numeric inference feature is recovered from the
prompt itself, never from oracle labels or database instance metadata.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import re
import time

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_gaussian_population_conditional as base

SCHEMA = 'nanojev.hawk-dove.gaussian-2000.oracle-blind-matrix-holdout.v1'
TIERS = tuple(t for t in hd.TIERS if t != 'T00')
TOTAL = 1_000_000
INPUT_DIM = 2 + 4 + len(TIERS)
DEFAULT_OUTPUT = Path.home() / 'NanoJev/runs/hawk_dove_gaussian_2000_blind_matrix_v1'
PAYOFF_LINE = re.compile(r'^BIND ([A-Z_]+) = (-?\d+)$')
MUL = re.compile(r'\(MUL\s+(?:(\d+)\s+([A-Z_]+)|([A-Z_]+)\s+(\d+))\)')
HAWK = re.compile(r'plays Hawk\s+(\d+)\s+out of a million times')
DOVE = re.compile(r'plays Dove\s+(\d+)\s+out of the same million times')


def _matrix_dict(matrix):
    return {tag: Decimal(int(v)) for tag, v in zip(hd.TAGS, matrix)}


def _header(matrix):
    return '\n'.join(['PAYOFF_BINDINGS'] +
        [f'BIND {tag} = {v}' for tag,v in zip(hd.TAGS,matrix)] +
        ['END_PAYOFF_BINDINGS', 'Compute X and Y from the visible counts and these four payoffs.'])


def _blinded_body(tier, a, b, matrix, mask):
    if tier not in TIERS: raise ValueError('BLIND_UNSUPPORTED_TIER')
    if tier in ('T01', 'T02'):
        # Preserve the partial-expansion form, but replace its oracle branch with
        # a nonnumeric instruction. This is T01B/T02B, not canonical T01/T02.
        full = tuple(s for s in hd.SLOTS[tier] if s[0] in 'ab') if mask else ()
        vals={'a':Decimal(a),'b':Decimal(b)}
        def subst(m):
            slot=m.group(1)
            if slot[0] in 'xy':return 'COMPUTE_X' if slot[0]=='x' else 'COMPUTE_Y'
            v=vals[slot[0]]
            return hd.canonical_num(v)+(hd.annotation(v) if slot in full else '')
        return hd.PLACEHOLDER.sub(subst,hd.TEMPLATES[tier])
    full=tuple(s for s in hd.SLOTS[tier] if s[0] in 'ab') if mask else ()
    return hd.render(tier,a,b,_matrix_dict(matrix),full,hd.REP_LEGACY)


def render_blind(tier, a, matrix, mask=0):
    if tier not in TIERS or mask not in (0,1):raise ValueError('BLIND_BAD_TIER_OR_MASK')
    if not isinstance(a,int) or not 0 <= a <= TOTAL: raise ValueError('BLIND_INVALID_COUNT')
    if len(matrix)!=4 or any(not isinstance(v,int) for v in matrix):
        raise ValueError('BLIND_INVALID_MATRIX')
    return _header(matrix)+'\n\n'+_blinded_body(tier,a,TOTAL-a,matrix,mask)


def parse_blind(prompt, tier, mask=0):
    """Fail closed unless the visible prompt is exactly a valid blinded rendering."""
    if tier not in TIERS or mask not in (0,1): raise ValueError('BLIND_BAD_TIER_OR_MASK')
    lines=prompt.split('\n\n',1)
    if len(lines)!=2: raise ValueError('BLIND_MISSING_BINDINGS')
    header,body=lines
    fields=header.splitlines()
    if len(fields)!=7 or fields[0]!='PAYOFF_BINDINGS' or fields[5]!='END_PAYOFF_BINDINGS':
        raise ValueError('BLIND_MALFORMED_BINDINGS')
    bindings=[]
    for expect,line in zip(hd.TAGS,fields[1:5]):
        match=PAYOFF_LINE.fullmatch(line)
        if not match or match.group(1)!=expect:raise ValueError('BLIND_BINDING_MISMATCH')
        bindings.append(int(match.group(2)))
    matrix=tuple(bindings)
    if header!=_header(matrix):raise ValueError('BLIND_UNEXPECTED_HEADER')
    clean=hd.strip_and_validate_bars(body,hd.REP_LEGACY)
    mapped={}
    for m in MUL.finditer(clean):
        count=int(m.group(1) if m.group(1) is not None else m.group(4))
        tag=m.group(2) if m.group(2) is not None else m.group(3)
        if tag not in hd.TAGS:raise ValueError('BLIND_UNKNOWN_PAYOFF_TAG')
        if tag in mapped and mapped[tag]!=count:raise ValueError('BLIND_INCONSISTENT_COUNTS')
        mapped[tag]=count
    hawk={mapped[tag] for tag in (hd.TAGS[0],hd.TAGS[2]) if tag in mapped}
    dove={mapped[tag] for tag in (hd.TAGS[1],hd.TAGS[3]) if tag in mapped}
    prose_a=HAWK.search(clean);prose_b=DOVE.search(clean)
    if tier=='T08':
        if mapped or not prose_a or not prose_b:raise ValueError('BLIND_T08_COUNTS_MISSING')
        a,b=int(prose_a.group(1)),int(prose_b.group(1))
    else:
        if len(hawk)!=1 or len(dove)!=1:raise ValueError('BLIND_LISP_COUNTS_MISSING')
        a,b=next(iter(hawk)),next(iter(dove))
        if tier=='T07' and (not prose_a or not prose_b or (int(prose_a.group(1)),int(prose_b.group(1)))!=(a,b)):
            raise ValueError('BLIND_PROSE_COUNT_MISMATCH')
    if a<0 or b<0 or a+b!=TOTAL:raise ValueError('BLIND_COUNTS_SUM_INVALID')
    if render_blind(tier,a,matrix,mask)!=prompt:
        raise ValueError('BLIND_NONCANONICAL_OR_ORACLE_EXPOSURE')
    return {'a':a,'b':b,'payoffs':matrix,'tier':tier,'mask':mask}


def features_from_prompt(prompt,tier,mask,payoff_bound):
    visible=parse_blind(prompt,tier,mask)
    return [visible['a']/TOTAL,visible['b']/TOTAL] + [v/payoff_bound for v in visible['payoffs']] + [float(tier==t) for t in TIERS]


def _distinct_matrices(seed,train_count,dev_count,test_count,payoff_bound):
    needed=train_count+dev_count+test_count
    if (2*payoff_bound+1)**4 < needed:raise ValueError('BLIND_NOT_ENOUGH_UNIQUE_MATRICES')
    # Uniform without replacement; matrix identity, not a count instance, owns splits.
    options=list(itertools.product(range(-payoff_bound,payoff_bound+1),repeat=4))
    random.Random(seed).shuffle(options)
    m=options[:needed]
    return {'train':m[:train_count],'dev':m[train_count:train_count+dev_count],
            'test':m[train_count+dev_count:]}


def _count_cases(matrix,seed,n):
    rng=random.Random(int.from_bytes(hashlib.sha256(f'{seed}/{matrix}'.encode()).digest()[:8],'big'))
    hh,hd,dh,dd=matrix
    roots=[]
    for numerator,denominator in [((dd-hd)*TOTAL,hh-hd-dh+dd),
                                  (-hd*TOTAL,hh-hd),(-dd*TOTAL,dh-dd)]:
        if denominator:
            root=round(numerator/denominator)
            roots.append(root)
    # Prioritize the target boundaries, then the nearest exact/near-zero and
    # X=Y cases; these must not be randomly discarded when the list is trimmed.
    ordered=[0,500000,TOTAL]
    for delta in (0,-1,1,-2,2):
        ordered.extend(root+delta for root in roots)
    ordered.extend((1,999999,1000,999000,50000,950000,250000,750000))
    seen=set(); counts=[]
    for a in ordered:
        if 0<=a<=TOTAL and a not in seen:
            seen.add(a);counts.append(a)
            if len(counts)==n:break
    while len(counts)<n:
        a=rng.randrange(TOTAL+1)
        if a not in seen:
            seen.add(a);counts.append(a)
    return sorted(counts)


def _dataset(args,device):
    matrices=_distinct_matrices(args.seed,args.train_matrices,args.dev_matrices,
                               args.test_matrices,args.payoff_bound)
    checks=[{tuple(m) for m in matrices[s]} for s in ('train','dev','test')]
    if any(checks[i]&checks[j] for i,j in ((0,1),(0,2),(1,2))):
        raise AssertionError('BLIND_MATRIX_SPLIT_LEAK')
    domain=((-1.25*TOTAL*args.payoff_bound,1.25*TOTAL*args.payoff_bound),)*2
    result={}
    for split,matrices_for_split in matrices.items():
        rows=[];inputs=[];ys=[]
        for index,matrix in enumerate(matrices_for_split):
            for a in _count_cases(matrix,args.seed,args.counts_per_matrix):
                b=TOTAL-a
                x,y,_=hd.oracle(a,b,_matrix_dict(matrix))
                instance=f'{split}/matrix-{index}/a-{a}'
                for tier in TIERS:
                    for mask in range(args.masks):
                        prompt=render_blind(tier,a,matrix,mask)
                        inputs.append(features_from_prompt(prompt,tier,mask,args.payoff_bound))
                        ys.append([float(x),float(y)])
                        rows.append({'instance':instance,'tier':tier,'mask':mask,
                                     'prompt':prompt,'x':float(x),'y':float(y),
                                     'matrix_index':index})
        result[split]={
            'features':torch.tensor(inputs,dtype=torch.float32,device=device),
            'numeric':torch.tensor(ys,dtype=torch.float32,device=device),
            'rows':rows,
            'tier_indices':{t:torch.tensor([i for i,r in enumerate(rows) if r['tier']==t],
                          dtype=torch.long,device=device) for t in TIERS}}
    matrix_hashes={split:[hashlib.sha256(json.dumps(m).encode()).hexdigest() for m in listed]
                   for split,listed in matrices.items()}
    manifest={'matrix_hashes':matrix_hashes,
              'counts_per_matrix':args.counts_per_matrix,
              'masks':args.masks,'payoff_bound':args.payoff_bound,
              'seed':args.seed,'matrix_split_sizes':{k:len(v) for k,v in matrices.items()},
              'presentation_split_sizes':{k:len(v['rows']) for k,v in result.items()},
              'zero_targets':{k:{'X':int((v['numeric'][:,0]==0).sum()),
                                 'Y':int((v['numeric'][:,1]==0).sum())} for k,v in result.items()}}
    return result,domain,manifest


class BlindDensityNetwork(base.DensityNetwork):
    def __init__(self,banks,bins,seed,hidden=128):
        super().__init__(banks,bins,seed,hidden=hidden)
        self.encoder[0]=nn.Linear(INPUT_DIM,hidden)


def _gate(result,min_rate):
    tiers={}
    for tier in TIERS:
        s=result['by_tier'][tier]
        # One instance counts as successful only if ALL rendered masks pass.
        value=s['instances_all_masks_xy_hit_rate']
        tiers[tier]={'presentations':s['n'],'independent_matrix_count_cases':s['independent_instances'],
                     'X_rate':s['x_within_tolerance'],'Y_rate':s['y_within_tolerance'],
                     'joint_presentation_rate':s['xy_within_tolerance'],
                     'joint_all_masks_instance_rate':value,'pass':value+1e-12>=min_rate,
                     'zero_X':s['zero_x_count'],'zero_Y':s['zero_y_count']}
    return {'status':'PASS' if all(v['pass'] for v in tiers.values()) else 'FAIL',
            'min_tier_joint_instance_rate':min(v['joint_all_masks_instance_rate'] for v in tiers.values()),
            'tiers':tiers}


def _rank(scores):
    vals=[scores['by_tier'][t]['instances_all_masks_xy_hit_rate'] for t in TIERS]
    return (min(vals),sum(vals)/len(vals),-scores['overall']['mean_tv'])


def _identity(args,manifest,domain):
    return {'schema':SCHEMA,'seed':args.seed,'banks':args.banks,'grid_bins':args.grid_bins,
            'hidden':args.hidden,'target_sigma_bins':args.target_sigma_bins,
            'train_matrices':args.train_matrices,'dev_matrices':args.dev_matrices,
            'test_matrices':args.test_matrices,'counts_per_matrix':args.counts_per_matrix,
            'masks':args.masks,'payoff_bound':args.payoff_bound,'steps':args.steps,
            'probe_every':args.probe_every,'batch_size':args.batch_size,
            'lr':args.lr,'min_lr':args.min_lr,'tolerance':args.tolerance,
            'min_rate':args.min_rate,'domain':[list(d) for d in domain],
            'matrix_hashes':manifest['matrix_hashes'],
            'inputs':'canonical prompt parsed counts and four numeric payoff bindings ONLY',
            'splitting':'unique full payoff matrices, disjoint train/dev/test',
            'tiers':'T01B,T02B,T03,T04,T05,T06,T07,T08',
            'data_policy':'TRAIN gradient; DEV promotion; TEST once after freeze'}


def _validate(args):
    if (args.banks<2 or args.grid_bins<32 or args.hidden<4 or args.steps<1
        or args.probe_every<1 or args.batch_size<8 or args.eval_batch_size<1
        or args.cpu_threads<1 or args.payoff_bound<1 or args.counts_per_matrix<3
        or args.masks not in (1,2) or min(args.train_matrices,args.dev_matrices,args.test_matrices)<1):
        raise ValueError('BLIND_INVALID_DIMENSIONS')
    if not (0<args.min_lr<=args.lr<=1 and 0<args.tolerance<1 and 0<args.min_rate<=1
            and args.target_sigma_bins>0):raise ValueError('BLIND_INVALID_OPTIMIZATION')
    if args.device=='cuda' and not torch.cuda.is_available():raise ValueError('BLIND_CUDA_UNAVAILABLE')
    # Oracle numeric annotations cannot encode more than 9,999,999 in hd library.
    if args.payoff_bound>9:raise ValueError('BLIND_PAYOFF_BOUND_EXCEEDS_ORACLE_FIXTURE')


def _evaluate(model,data,domain,args):
    return base.evaluate(model,data,domain,args.target_sigma_bins,
                         batch_size=args.eval_batch_size,relative_tolerance=args.tolerance)


def train(args):
    _validate(args)
    if args.device=='cpu':torch.set_num_threads(args.cpu_threads)
    torch.manual_seed(args.seed)
    device=torch.device(args.device)
    dataset,domain,manifest=_dataset(args,device)
    ident=_identity(args,manifest,domain)
    output=args.output
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('BLIND_OUTPUT_EXISTS_NO_OVERWRITE:'+str(output))
    output.mkdir(parents=True,exist_ok=True)
    base._atomic_json(output/'config.json',ident)
    base._atomic_json(output/'matrix_split_manifest.json',manifest)
    model=BlindDensityNetwork(args.banks,args.grid_bins,args.seed,args.hidden).to(device)
    opt=torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=0.)
    scheduler=torch.optim.lr_scheduler.CosineAnnealingLR(opt,T_max=args.steps,eta_min=args.min_lr)
    generator=torch.Generator(device='cpu').manual_seed(args.seed+91)
    pools=dataset['train']['tier_indices']
    history=[];best_rank=None;best_step=None
    started=time.monotonic()
    def probe(step,loss=None):
        nonlocal best_rank,best_step
        dev=_evaluate(model,dataset['dev'],domain,args)
        rank=_rank(dev)
        gate=_gate(dev,args.min_rate)
        promote=(best_rank is None or rank>best_rank)
        if promote:
            best_rank=rank;best_step=step
            base._atomic_save(output/'best.pt',{'schema':SCHEMA,'identity':ident,
                'step':step,'model':model.state_dict()})
        row={'step':step,'loss':loss,'dev':dev['overall'],'dev_gate':gate,
             'promoted':promote,'lr':opt.param_groups[0]['lr']}
        history.append(row)
        base._atomic_json(output/'history.json',{'schema':SCHEMA,'records':history})
        print(f'blind-matrix banks={args.banks} step={step}/{args.steps} '
              f'dev_min_joint5={rank[0]:.4f} dev_tv={dev["overall"]["mean_tv"]:.6f} '
              f'gate={gate["status"]}',flush=True)
    probe(0)
    for step in range(1,args.steps+1):
        model.train();selected=[]
        for j,tier in enumerate(TIERS):
            count=args.batch_size//len(TIERS)+int(j<args.batch_size%len(TIERS))
            pool=pools[tier]
            draws=torch.randint(len(pool),(count,),generator=generator)
            selected.append(pool[draws.to(device)])
        ids=torch.cat(selected)
        inp=dataset['train']['features'][ids]
        actual=dataset['train']['numeric'][ids]
        target=base.normalized_target(actual,domain,args.grid_bins,args.target_sigma_bins)
        opt.zero_grad(set_to_none=True)
        density=model(inp)
        kl=(target*(target.clamp_min(1e-12).log()-density.clamp_min(1e-12).log())).sum(-1).mean()
        mse=args.grid_bins*(density-target).square().mean()
        locations=torch.stack([(actual[:,j]-domain[j][0])/(domain[j][1]-domain[j][0])
                               for j in range(2)],dim=1)
        loss=kl+mse+10.*(model.locations(inp)-locations).square().mean()
        if not bool(torch.isfinite(loss)):raise FloatingPointError('BLIND_NONFINITE_LOSS')
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
        opt.step();scheduler.step()
        if step%args.probe_every==0 or step==args.steps:probe(step,float(loss.detach()))
    state=torch.load(output/'best.pt',map_location=device,weights_only=True)
    model.load_state_dict(state['model'])
    dev=_evaluate(model,dataset['dev'],domain,args)
    test=_evaluate(model,dataset['test'],domain,args)  # first and only TEST evaluation
    report={'schema':SCHEMA,'status':'PASS' if _gate(dev,args.min_rate)['status']=='PASS'
                                          and _gate(test,args.min_rate)['status']=='PASS' else 'FAIL',
            'best_step':best_step,'identity':ident,'manifest':manifest,
            'dev':{'gate':_gate(dev,args.min_rate),'overall':dev['overall'],'by_tier':dev['by_tier']},
            'test':{'gate':_gate(test,args.min_rate),'overall':test['overall'],'by_tier':test['by_tier']},
            'uses_test_for_training_or_promotion':False,
            'raw_token_comprehension_certified':False,
            'T01_T02_are_blinded_variants':True,
            'oracle_XY_in_input':False,
            'elapsed_seconds':round(time.monotonic()-started,3)}
    base._atomic_json(output/'summary.json',report)
    print(json.dumps({'status':report['status'],'best_step':best_step,
        'dev_gate':report['dev']['gate'],'test_gate':report['test']['gate'],
        'summary':str(output/'summary.json')},indent=2),flush=True)
    return report


def audit(args):
    if args.device=='cuda' and not torch.cuda.is_available():raise ValueError('BLIND_CUDA_UNAVAILABLE')
    if args.device=='cpu':torch.set_num_threads(args.cpu_threads)
    state=torch.load(args.checkpoint,map_location='cpu',weights_only=True)
    if state.get('schema')!=SCHEMA:raise ValueError('BLIND_CHECKPOINT_SCHEMA_MISMATCH')
    ident=state['identity']
    opts=argparse.Namespace(**{**ident,'device':args.device,'output':args.output,
             'eval_batch_size':args.eval_batch_size,'cpu_threads':args.cpu_threads})
    _validate(opts)
    dataset,domain,manifest=_dataset(opts,torch.device(args.device))
    if _identity(opts,manifest,domain)!=ident:
        raise ValueError('BLIND_AUDIT_DATASET_IDENTITY_MISMATCH')
    model=BlindDensityNetwork(ident['banks'],ident['grid_bins'],ident['seed'],ident['hidden']).to(args.device)
    model.load_state_dict(state['model']);model.eval()
    selected={}
    for split in args.splits.split(','):
        if split not in ('dev','test') or split in selected:raise ValueError('BLIND_AUDIT_SPLITS_INVALID')
        result=_evaluate(model,dataset[split],domain,opts)
        selected[split]={'gate':_gate(result,opts.min_rate),'overall':result['overall'],
                         'by_tier':result['by_tier']}
    if not selected:raise ValueError('BLIND_AUDIT_SPLITS_EMPTY')
    status='PASS' if all(v['gate']['status']=='PASS' for v in selected.values()) else 'FAIL'
    report={'schema':SCHEMA+'.audit','status':status,'checkpoint':str(args.checkpoint),
            'step':state['step'],'splits':selected,'matrix_hashes':manifest['matrix_hashes'],
            'oracle_XY_in_input':False,'certified_tiers':list(TIERS)}
    if args.output:base._atomic_json(args.output,report)
    for split,entry in selected.items():
        print(f'\n{split.upper()} matrix-held-out joint X/Y within '
              f'{100*opts.tolerance:g}% (minimum {100*opts.min_rate:g}% per tier):')
        for tier,score in entry['gate']['tiers'].items():
            print(f'  {tier}: {score["joint_all_masks_instance_rate"]:.1%} '
                  f'({score["independent_matrix_count_cases"]} distinct matrix/count cases) '
                  f'{"PASS" if score["pass"] else "FAIL"}')
    print('BLIND_MATRIX_GATE_STATUS='+status,flush=True)
    return report



@torch.no_grad()
def predict(args):
    """Standalone inference: no teacher database, oracle labels, or target objects."""
    if args.device=='cuda' and not torch.cuda.is_available():
        raise ValueError('BLIND_CUDA_UNAVAILABLE')
    state=torch.load(args.checkpoint,map_location='cpu',weights_only=True)
    if state.get('schema')!=SCHEMA:
        raise ValueError('BLIND_CHECKPOINT_SCHEMA_MISMATCH')
    ident=state['identity']
    matrix=args.prompt_file.read_text(encoding='utf8')
    visible=parse_blind(matrix,args.tier,args.mask)
    feats=torch.tensor([features_from_prompt(matrix,args.tier,args.mask,ident['payoff_bound'])],
                       dtype=torch.float32,device=args.device)
    model=BlindDensityNetwork(ident['banks'],ident['grid_bins'],ident['seed'],ident['hidden']).to(args.device)
    model.load_state_dict(state['model']);model.eval()
    pred=base.numeric_decode(model(feats),tuple(tuple(x) for x in ident['domain']))[0].cpu()
    x,y=(float(v) for v in pred)
    result={'schema':SCHEMA+'.prediction','step':state['step'],'tier':args.tier,
            'predicted_X':x,'predicted_Y':y,'predicted_Z':'HAWK' if x>y else 'DOVE' if x<y else 'TIE',
            'visible_count_a':visible['a'],'visible_count_b':visible['b'],
            'visible_payoff_bindings':dict(zip(hd.TAGS,visible['payoffs'])),
            'oracle_XY_in_input':False,'oracle_labels_read':False,
            'raw_text_understanding_certified':False}
    print(json.dumps(result,indent=2),flush=True)
    return result

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    subs=p.add_subparsers(dest='command',required=True)
    t=subs.add_parser('train',help='Generate matrix-held-out prompts; train; certify TEST once')
    t.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    t.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    t.add_argument('--banks',type=int,default=2000)
    t.add_argument('--grid-bins',type=int,default=256)
    t.add_argument('--target-sigma-bins',type=float,default=2.5)
    t.add_argument('--hidden',type=int,default=128)
    t.add_argument('--steps',type=int,default=15000)
    t.add_argument('--probe-every',type=int,default=500)
    t.add_argument('--batch-size',type=int,default=64)
    t.add_argument('--eval-batch-size',type=int,default=256)
    t.add_argument('--lr',type=float,default=.003)
    t.add_argument('--min-lr',type=float,default=.00003)
    t.add_argument('--seed',type=int,default=20261022)
    t.add_argument('--train-matrices',type=int,default=96)
    t.add_argument('--dev-matrices',type=int,default=24)
    t.add_argument('--test-matrices',type=int,default=24)
    t.add_argument('--counts-per-matrix',type=int,default=16)
    t.add_argument('--masks',type=int,choices=(1,2),default=2)
    t.add_argument('--payoff-bound',type=int,default=4)
    t.add_argument('--tolerance',type=float,default=.05)
    t.add_argument('--min-rate',type=float,default=.8)
    t.add_argument('--cpu-threads',type=int,default=4)
    a=subs.add_parser('audit',help='Re-evaluate frozen checkpoint on held-out matrix splits')
    a.add_argument('--checkpoint',type=Path,required=True)
    a.add_argument('--output',type=Path)
    a.add_argument('--splits',default='dev,test')
    a.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    a.add_argument('--eval-batch-size',type=int,default=256)
    a.add_argument('--cpu-threads',type=int,default=4)
    q=subs.add_parser('predict',help='Standalone inference from one oracle-blind prompt')
    q.add_argument('--checkpoint',type=Path,required=True)
    q.add_argument('--prompt-file',type=Path,required=True)
    q.add_argument('--tier',choices=TIERS,required=True)
    q.add_argument('--mask',type=int,choices=(0,1),default=0)
    q.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    return train(args) if args.command=='train' else audit(args) if args.command=='audit' else predict(args)

if __name__=='__main__':main()
