#!/usr/bin/env python3
"""Extend the learned signed zero line through EVERY integer -32..32 at +/-2M.

This is a standalone backbone finetune, not a bank, oracle, or calculator.
The prior radius16 sample holdout's single nonzero miss is admitted only as
an explicitly documented experimental source, NOT promoted retrospectively.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

import torch
from torch import nn

from tools.nanojev_zero_line_ladder import atomic_checkpoint, atomic_json, deck, sample_deck, evaluate as legacy_evaluate
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256
from tools.nanojev_zero_line_signed_radius16 import (
    source_model as verified_zero_source, assess as prior_assess,
    build_matrix as prior_matrix, magnitude_fold as prior_fold,
    exact_supervision,
)
from tools.nanojev_zero_line_zero_only import (
    BANDS, MAX_ABS, build_probe as previous_probe,
    classify as zero_classify, legacy_loss, input_tensor,
)
from tools import nanojev_addition_curriculum, nanojev_zero_line_ladder, nanojev_zero_line_zero_only, nanojev_zero_line_signed_radius16

SCHEMA = 'nanojev.zero-line-signed-radius32.v1'
RESIDUALS = tuple(range(-32, 33))
RADIUS = 32
OLD_RADIUS = 16
DEFAULT_SEED = 20261232
PRIOR_SEED = 20261116


def fold(m: int, seed: int = DEFAULT_SEED) -> str:
    """New magnitude-family split; previous radius16 TEST is quarantined.

    MAX_ABS is a named extreme-boundary stress sentinel, never used in train,
    DEV selection or the fresh TEST accuracy numerator.
    """
    if not 0 <= m <= MAX_ABS:
        raise ValueError('ZERO_R32_BAD_MAGNITUDE')
    if m == MAX_ABS:
        return 'endpoint'
    if prior_fold(m, PRIOR_SEED) == 'test':
        return 'quarantine'
    q = hashlib.sha256(f'r32-family:{seed}:{m}'.encode()).digest()[0] % 10
    return 'train' if q < 4 else 'dev' if q < 7 else 'test'


def pair_for_max(m: int, delta: int, swap=False, zero_direction=1):
    if abs(delta) > RADIUS or not 0 <= m <= MAX_ABS or m < abs(delta):
        return None
    s = (1 if delta > 0 else -1) if delta else (1 if zero_direction > 0 else -1)
    a,b = s*m, delta-s*m
    if max(abs(a),abs(b)) != m or max(abs(a),abs(b)) > MAX_ABS:
        raise AssertionError('ZERO_R32_PAIR_INVARIANT')
    return (b,a) if swap else (a,b)


def anchor_magnitudes(band, split, seed, count, view=0, used=None):
    lo,hi = BANDS[band]
    lo=max(lo,RADIUS)
    rng=random.Random(f'r32-anchor:{seed}:{band}:{split}:{view}')
    if count<=0 or split not in ('train','dev','test'):
        raise ValueError('ZERO_R32_BAD_ANCHOR_REQUEST')
    used=set(used or ())
    proposed=[lo,lo+1,hi-1,hi]
    for p in (100,1000,10000,100000,1000000,MAX_ABS):
        proposed.extend((p-1,p,p+1))
    proposed += [lo+(hi-lo)*i//max(count*4-1,1) for i in range(count*4)]
    out=[]
    def add(m):
        if lo<=m<=hi and m not in used and fold(m,seed)==split:
            used.add(m);out.append(m)
    for m in proposed:
        if len(out)>=count:break
        add(m)
    for _ in range(count*1200):
        if len(out)>=count:break
        add(rng.randint(lo,hi))
    if len(out)!=count:
        raise RuntimeError(f'ZERO_R32_INSUFFICIENT_INDEPENDENT_MAGNITUDES band={band} split={split} view={view} {len(out)}/{count}')
    return sorted(out)


def rows_for_m(m, band):
    rows=[]
    for delta in RESIDUALS:
        if delta:
            pairs=[pair_for_max(m,delta,False),pair_for_max(m,delta,True)]
        else:
            pairs=[pair_for_max(m,0,swap,direction) for direction in (-1,1) for swap in (False,True)]
        for pair in pairs:
            if pair is not None:
                a,b=pair
                rows.append((a,b,delta,band,m))
    return rows


def build_matrix(split,seed,anchors_per_band=6,views=1):
    """Each view has its own disjoint anchor families within the split."""
    if views<1:
        raise ValueError('ZERO_R32_INVALID_VIEWS')
    grids=[]
    previously={k:set() for k in range(len(BANDS))}
    for v in range(views):
        rows=[]
        for band in range(len(BANDS)):
            anchors=anchor_magnitudes(band,split,seed,anchors_per_band,v,previously[band])
            previously[band].update(anchors)
            for m in anchors:
                rows.extend(rows_for_m(m,band))
        rows=list({(a,b):(a,b,d,band,m) for a,b,d,band,m in rows}.values())
        if len(set((b,d) for _,_,d,b,_ in rows))!=len(BANDS)*len(RESIDUALS):
            raise RuntimeError('ZERO_R32_EMPTY_RESIDUAL_CELL')
        grids.append(rows)
    return grids


def train_card(rng,band,delta,seed):
    lo,hi=BANDS[band];lo=max(lo,RADIUS)
    for _ in range(6000):
        m=rng.randint(lo,hi)
        if fold(m,seed)=='train':
            pair=pair_for_max(m,delta,bool(rng.getrandbits(1)), -1 if rng.getrandbits(1) else 1)
            if pair is not None:
                return (*pair,delta,band,m)
    raise RuntimeError(f'ZERO_R32_NO_TRAIN_FAMILY band={band} delta={delta}')


def train_cards(rng,batch_size,seed,weights,cursor):
    """Balanced 65 residuals; uniform lattice plus train-only failure replay."""
    n=len(RESIDUALS)
    if batch_size<n:
        raise ValueError('ZERO_R32_BATCH_MUST_COVER_ALL_65_RESIDUALS')
    mined_count=n if batch_size>=2*n else 0
    uniform=batch_size-mined_count
    result=[]
    for i in range(batch_size):
        delta=RESIDUALS[(cursor+i)%n]
        band=(((cursor+i)//n)%len(BANDS)) if i<uniform else rng.choices(range(len(BANDS)),weights=weights,k=1)[0]
        result.append(train_card(rng,band,delta,seed))
    rng.shuffle(result)
    return result,cursor+batch_size


@torch.inference_mode()
def assess(model,rows,device,batch_size=256,max_errors_saved=48):
    model.eval()
    counts={(k,d):[0,0] for k in range(len(BANDS)) for d in RESIDUALS}
    examples=[]
    for offset in range(0,len(rows),batch_size):
        batch=rows[offset:offset+batch_size]
        out=model(input_tensor(batch,device))
        signs=out['sign'].argmax(-1)-1
        digits=out['digits'].argmax(-1)
        from tools.nanojev_addition_curriculum import target_digits
        target=torch.tensor([r[2] for r in batch],device=device,dtype=torch.long)
        correct=(signs==target.sign()) & (digits==target_digits(target)).all(-1)
        predicted=(digits*torch.tensor([10**i for i in range(6,-1,-1)],device=device)).sum(-1)*signs
        for (a,b,d,band,m),ok,p in zip(batch,correct.tolist(),predicted.tolist()):
            counts[(band,d)][0]+=int(ok);counts[(band,d)][1]+=1
            if not ok and len(examples)<max_errors_saved:
                examples.append({'a':a,'b':b,'delta':d,'prediction':int(p),'magnitude':m,'band':list(BANDS[band])})
    matrix={}; errors=0; nonzero_errors=0; nonzero_cards=0;zero_errors=0; zero_cards=0
    largest_cell_error=0; min_cell=1.; residual_errors={str(d):0 for d in RESIDUALS}; band_errors={}
    populated=True
    for band,(lo,hi) in enumerate(BANDS):
        cells={};be=0
        for d in RESIDUALS:
            good,total=counts[(band,d)];bad=total-good
            populated &= total>0
            if total:min_cell=min(min_cell,good/total)
            cells[str(d)]={'correct':good,'total':total,'errors':bad,'accuracy':good/total if total else None}
            errors+=bad;be+=bad;residual_errors[str(d)]+=bad
            largest_cell_error=max(largest_cell_error,bad)
            if d==0:zero_errors+=bad;zero_cards+=total
            else:nonzero_errors+=bad;nonzero_cards+=total
        key=f'{lo}-{hi}';matrix[key]=cells;band_errors[key]=be
    return {'cards':len(rows),'errors':errors,'zero_errors':zero_errors,'zero_cards':zero_cards,
            'nonzero_errors':nonzero_errors,'nonzero_cards':nonzero_cards,
            'nonzero_error_rate':nonzero_errors/nonzero_cards if nonzero_cards else None,
            'min_cell_accuracy':min_cell if populated else None,
            'max_cell_errors':largest_cell_error,'every_cell_populated':bool(populated),
            'residual_errors':residual_errors,'band_errors':band_errors,'matrix':matrix,
            'counterexamples':examples}


def passes(metrics,args):
    return bool(metrics['every_cell_populated'] and metrics['zero_errors']==0
        and metrics['max_cell_errors']<=args.max_cell_errors
        and metrics['nonzero_error_rate'] is not None
        and metrics['nonzero_error_rate']<=args.max_nonzero_error_rate)


def research_parent(ladder_output,zero_only_output,r16_output,device):
    """Load verified stable DEV checkpoint; never relabel r16 as promoted."""
    zero,zero_origin=verified_zero_source(ladder_output,zero_only_output,device)
    root=Path(r16_output).resolve()
    config_file=root/'config.json'
    result_file=root/'result.json'
    last_file=root/'latest.pt'
    seal=root/'test_opened.json'
    if not all(f.is_file() for f in (config_file,result_file,last_file,seal)):
        raise FileNotFoundError('ZERO_R32_R16_SOURCE_FILES_MISSING')
    cfg=json.loads(config_file.read_text('utf-8'))
    report=json.loads(result_file.read_text('utf-8'))
    state=torch.load(last_file,map_location='cpu',weights_only=True)
    from tools.nanojev_zero_line_signed_radius16 import SCHEMA as R16_SCHEMA
    if (cfg.get('schema')!=R16_SCHEMA
        or cfg.get('code_sha256')!=sha256(nanojev_zero_line_signed_radius16.__file__)
        or cfg.get('source',{}).get('zero_promoted_sha256')!=zero_origin['zero_promoted_sha256']
        or state.get('schema')!=R16_SCHEMA+'.checkpoint'
        or report.get('status')!='heldout_refused'
        or state.get('step')!=report.get('last_step')
        or state.get('streak',0)<3
        or report.get('dev',{}).get('passes') is not True
        or report.get('historical_full',{}).get('groups',{}).get('all',{}).get('passed')!=65025
        or any(v.get('passes') is not True for v in report.get('previous_zero_retention',{}).values())
        or len(report.get('previous_zero_retention',{}))!=2
        or report.get('holdout_once',{}).get('cards',0)<=0):
        raise RuntimeError('ZERO_R32_UNVERIFIED_R16_RESEARCH_CHECKPOINT')
    holdout=report['holdout_once']
    errors=[]
    for band,cells in holdout['matrix'].items():
        for delta,cell in cells.items():
            failures=cell['total']-cell['correct']
            if failures:errors.append((band,int(delta),failures))
    if errors!=[('1572864-2000000',-16,1)]:
        raise RuntimeError(f'ZERO_R32_PARENT_HOLDOUT_NOT_EXPECTED_ONE_ERROR {errors}')
    zero.load_state_dict(state['model'],strict=True)
    zero.eval().requires_grad_(False)
    return zero,{'status':'experimental_6863_of_6864_not_formally_certified',
                 'r16_latest_sha256':sha256(last_file),
                 'r16_config_sha256':sha256(config_file),
                 'r16_result_sha256':sha256(result_file),
                 'r16_test_seal_sha256':sha256(seal),
                 'r16_step':state['step'], 'r16_holdout_cards':holdout['cards'],
                 'r16_holdout_errors':1,'zero_origin':zero_origin,
                 'previous_test_magnitudes':'quarantined_from_new_train_dev_and_fresh_test'}


def run_config(args,origin):
    fields=('seed','batch_size','legacy_batch_size','legacy_weight','zero_weight',
      'inner_weight','lr','probe_every','eval_batch_size','legacy_probe_cards','dev_anchors',
      'dev_views','test_anchors','train_probe_anchors','parent_anchor_count','stability_probes',
      'max_cell_errors','max_nonzero_error_rate','cpu_threads')
    return {'schema':SCHEMA,'source':origin,'settings':{k:getattr(args,k) for k in fields},
        'code_sha256':sha256(__file__),
        'backbone_code_sha256':sha256(nanojev_addition_curriculum.__file__),
        'ladder_code_sha256':sha256(nanojev_zero_line_ladder.__file__),
        'zero_only_code_sha256':sha256(nanojev_zero_line_zero_only.__file__),
        'r16_code_sha256':sha256(nanojev_zero_line_signed_radius16.__file__),
        'holdout_policy':'new independent magnitude fold; prior test quarantined; seal before opening',
        'pass_policy':'zero exact; nonzero <= bounded rate and per-cell errors; historical exact'}


def snapshot(model,optimizer,rng,step,streak,weights,cursor,best):
    return {'schema':SCHEMA+'.checkpoint','model':model.state_dict(),'optimizer':optimizer.state_dict(),
      'rng':rng.getstate(),'torch_rng':torch.get_rng_state(),
      'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
      'step':step,'streak':streak,'weights':weights,'cursor':cursor,'best':best}


def restore(state,model,optimizer,rng):
    if state.get('schema')!=SCHEMA+'.checkpoint':
        raise RuntimeError('ZERO_R32_BAD_RESUME_CHECKPOINT')
    model.load_state_dict(state['model'],strict=True)
    optimizer.load_state_dict(state['optimizer'])
    rng.setstate(state['rng'])
    torch.set_rng_state(state['torch_rng'])
    if torch.cuda.is_available() and state.get('cuda_rng') is not None:
        torch.cuda.set_rng_state_all(state['cuda_rng'])
    return state['step'],state['streak'],state['weights'],state['cursor'],state['best']


def check_args(a):
    if (a.max_steps<1 or a.probe_every<1 or a.batch_size<len(RESIDUALS)
        or a.legacy_batch_size<1 or a.eval_batch_size<1 or a.dev_views<2
        or a.dev_anchors<1 or a.test_anchors<1 or a.train_probe_anchors<1
        or a.parent_anchor_count<1 or a.legacy_probe_cards<1 or a.stability_probes<2
        or a.cpu_threads<1 or a.max_cell_errors<0 or not 0<=a.max_nonzero_error_rate<.1
        or not 0<a.lr<1 or not 0<a.zero_weight<100 or not 0<a.inner_weight<100
        or not 0<a.legacy_weight<100):
        raise ValueError('ZERO_R32_BAD_ARGUMENTS')
    if a.phase=='train' and a.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('ZERO_R32_CUDA_UNAVAILABLE')


def run(args):
    check_args(args)
    if args.phase=='plan':
        plan={'schema':SCHEMA,'radius':RADIUS,'residuals':list(RESIDUALS),
             'residual_count':len(RESIDUALS),'magnitude_bands':[list(x) for x in BANDS],
             'total_cells':len(RESIDUALS)*len(BANDS),
             'historical_arithmetic':65025,'prior_r16_source':'unpromoted one-error research milestone',
             'zero_exact':True,'nonzero_error_rate_max':args.max_nonzero_error_rate,
             'max_errors_in_one_cell':args.max_cell_errors}
        print('ZERO_R32_PLAN='+json.dumps(plan,sort_keys=True),flush=True)
        return plan
    torch.set_num_threads(args.cpu_threads)
    root=Path(args.output).resolve()
    parent,origin=research_parent(args.ladder_output,args.zero_only_output,args.r16_output,args.device)
    cfg=run_config(args,origin)
    config_file=root/'config.json'
    if args.resume:
        if not config_file.exists() or json.loads(config_file.read_text('utf-8'))!=cfg:
            raise RuntimeError('ZERO_R32_CONFIG_OR_SOURCE_DRIFT')
        if (root/'test_opened.json').exists() or (root/'promoted.pt').exists():
            raise RuntimeError('ZERO_R32_SEALED_TEST_ALREADY_OPENED')
    else:
        if root.exists() and any(root.iterdir()):
            raise FileExistsError('ZERO_R32_OUTPUT_OVERWRITE_REFUSED')
        root.mkdir(parents=True,exist_ok=True)
        atomic_json(config_file,cfg)
    model,_=load_certified_model(args.ladder_output,args.device)
    model.load_state_dict(parent.state_dict(),strict=True)
    original,_=load_certified_model(args.ladder_output,args.device)
    original.eval().requires_grad_(False)
    for name,p in model.named_parameters():
        p.requires_grad_(name.startswith('backbone.') or name.startswith('norm.') or name in ('query','pos'))
    optimizer=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=args.lr,weight_decay=0.)
    rng=random.Random(args.seed+367)
    torch.manual_seed(args.seed+181)
    step,streak,weights,cursor,best=0,0,[1.]*len(BANDS),0,1e9
    if args.resume:
        if not (root/'latest.pt').is_file():
            raise FileNotFoundError('ZERO_R32_MISSING_LATEST')
        step,streak,weights,cursor,best=restore(torch.load(root/'latest.pt',map_location='cpu',weights_only=True),model,optimizer,rng)
    train_probe=build_matrix('train',args.seed,args.train_probe_anchors)[0]
    dev_views=build_matrix('dev',args.seed,args.dev_anchors,args.dev_views)
    # TEST constructed deterministically but not evaluated, inspected, or used
    # until test_opened seal is written. No TEST score ever informs training.
    test=build_matrix('test',args.seed,args.test_anchors)[0]
    endpoint=rows_for_m(MAX_ABS,len(BANDS)-1)
    old_dev=prior_matrix('dev',PRIOR_SEED,8)
    historical_legacy=deck(127)
    legacy_probe=sample_deck(historical_legacy,args.legacy_probe_cards,args.seed+34)
    zero_cfg=json.loads((Path(args.zero_only_output)/'config.json').read_text('utf-8'))
    zero_seed=int(zero_cfg['settings']['seed'])
    parent_zero={s:previous_probe(s,zero_seed,args.parent_anchor_count) for s in ('dev','test')}
    if not args.resume:
        baselines=[assess(parent,v,args.device,args.eval_batch_size) for v in dev_views]
        prior=prior_assess(parent,old_dev,args.device,args.eval_batch_size)
        atomic_json(root/'baseline.json',{'dev':baselines,'r16_historical_dev':prior,
                     'source_status':origin['status']})
        print('ZERO_R32_BASELINE='+json.dumps({'dev_errors':[r['errors'] for r in baselines],
                'dev_zero_errors':[r['zero_errors'] for r in baselines],
                'r16_historical_worst_cell':prior['worst_cell_exact']},sort_keys=True),flush=True)
    elif not (root/'baseline.json').is_file():
        raise RuntimeError('ZERO_R32_MISSING_BASELINE')
    report={'status':'training','source':origin,'last_step':step}
    for step in range(step+1,args.max_steps+1):
        cards,cursor=train_cards(rng,args.batch_size,args.seed,weights,cursor)
        model.train()
        x=input_tensor(cards,args.device)
        from tools.nanojev_addition_curriculum import target_digits
        from torch.nn import functional as F
        output=model(x)
        targets=torch.tensor([r[2] for r in cards],dtype=torch.long,device=args.device)
        sign_loss=F.cross_entropy(output['sign'],targets.sign()+1,reduction='none')
        digit_loss=F.cross_entropy(output['digits'].transpose(1,2),target_digits(targets),reduction='none').mean(-1)
        w=torch.ones_like(targets,dtype=torch.float)
        w=torch.where(targets.abs()<=OLD_RADIUS,args.inner_weight,w)
        w=torch.where(targets==0,args.zero_weight,w)
        signed=((sign_loss+digit_loss)*w).sum()/w.sum()
        history_x=torch.randint(-127,128,(args.legacy_batch_size,2),device=args.device)
        hist=legacy_loss(model,original,history_x)
        loss=signed+args.legacy_weight*hist
        if not bool(torch.isfinite(loss)):
            raise FloatingPointError('ZERO_R32_NONFINITE_LOSS')
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad),1.,error_if_nonfinite=True)
        optimizer.step()
        if step%args.probe_every and step!=args.max_steps:
            continue
        t=assess(model,train_probe,args.device,args.eval_batch_size)
        weights=[1+4*sum(t['matrix'][f'{lo}-{hi}'][str(d)]['errors'] for d in RESIDUALS)/
                          max(1,sum(t['matrix'][f'{lo}-{hi}'][str(d)]['total'] for d in RESIDUALS))
                 for lo,hi in BANDS]
        dev=[assess(model,v,args.device,args.eval_batch_size) for v in dev_views]
        legacy_small=legacy_evaluate(model,legacy_probe,args.device,args.eval_batch_size)
        zero_history={s:zero_classify(model,rows,args.device,args.eval_batch_size) for s,rows in parent_zero.items()}
        old_signed=prior_assess(model,old_dev,args.device,args.eval_batch_size)
        legacy_ok=legacy_small['groups']['all']['passed']==len(legacy_probe['y'])
        zero_history_ok=all(v['passes'] for v in zero_history.values())
        old_signed_ok=old_signed['passes']
        ready=all(passes(v,args) for v in dev) and legacy_ok and zero_history_ok and old_signed_ok
        streak=streak+1 if ready else 0
        overall_dev_errors=sum(v['errors'] for v in dev)
        if ready and overall_dev_errors<best:
            best=overall_dev_errors
            atomic_checkpoint(root/'best_dev.pt',{'schema':SCHEMA+'.best','model':model.state_dict(),
                              'step':step,'source_sha256':origin['r16_latest_sha256'],
                              'dev_errors':overall_dev_errors})
        report.update({'status':'training','last_step':step,'loss':float(loss.detach()),
          'signed_loss':float(signed.detach()),'legacy_loss':float(hist.detach()),
          'train_probe':t,'dev_views':dev,'r16_historical_dev':old_signed,
          'previous_zero_retention':zero_history,'historical_probe':legacy_small,
          'streak':streak,'stability_target':args.stability_probes,
          'min_dev_error_count':best})
        atomic_checkpoint(root/'latest.pt',snapshot(model,optimizer,rng,step,streak,weights,cursor,best))
        atomic_json(root/'result.json',report)
        print('ZERO_R32_PROBE='+json.dumps({'step':step,'loss':round(float(loss.detach()),6),
          'dev_errors':[v['errors'] for v in dev],
          'dev_zero_errors':[v['zero_errors'] for v in dev],
          'dev_worst_cell_accuracy':min((v['min_cell_accuracy'] for v in dev if v['min_cell_accuracy'] is not None),default=None),
          'r16_historical_dev_exact':old_signed['passes'],
          'legacy_probe_accuracy':legacy_small['groups']['all']['accuracy'],
          'zero_history_retained':zero_history_ok,
          'worst_train_band':max(range(len(weights)),key=lambda k:weights[k]),
          'streak':streak,'target':args.stability_probes},sort_keys=True),flush=True)
        if streak<args.stability_probes:
            continue
        full=legacy_evaluate(model,historical_legacy,args.device,args.eval_batch_size)
        report['historical_full']=full
        if full['groups']['all']['passed']!=65025 or full['false_zero']!=0:
            streak=0
            report['status']='retention_refused'
            report['reason']='historical_65025_regression'
            atomic_checkpoint(root/'latest.pt',snapshot(model,optimizer,rng,step,streak,weights,cursor,best))
            atomic_json(root/'result.json',report)
            print('ZERO_R32_RETENTION_REFUSED='+json.dumps({'step':step,
                'historical_passed':full['groups']['all']['passed']},sort_keys=True),flush=True)
            continue
        atomic_json(root/'test_opened.json',{'step':step,
            'source_sha256':origin['r16_latest_sha256'],
            'status':'sealed_holdout_open_once_resume_prohibited'})
        holdout=assess(model,test,args.device,args.eval_batch_size)
        # Endpoint is a previously examined magnitude in r16; report only as
        # a separate boundary sentinel, not part of fresh held-out accuracy.
        boundary=assess(model,endpoint,args.device,args.eval_batch_size)
        report['holdout_once']=holdout
        report['endpoint_diagnostic']=boundary
        if passes(holdout,args) and boundary['zero_errors']==0:
            report['status']='promoted'
            atomic_checkpoint(root/'promoted.pt',{'schema':SCHEMA+'.promoted',
                'model':model.state_dict(),'step':step,
                'source_r16_research_sha256':origin['r16_latest_sha256'],
                'config_sha256':sha256(config_file),'historical_exact':65025,
                'dev_views':dev,'sealed_test':holdout,
                'r16_historical_dev':old_signed,'parent_zero_retention':zero_history,
                'endpoint_diagnostic':boundary,
                'certification_scope':'sampled experimental radius32; not exhaustive to 2M'})
        else:
            report['status']='heldout_refused'
            report['reason']='new_sealed_test_or_endpoint_zero_failed'
        atomic_json(root/'result.json',report)
        print('ZERO_R32_CERTIFICATION='+json.dumps({'status':report['status'],
                'step':step,'holdout_cards':holdout['cards'],'holdout_errors':holdout['errors'],
                'holdout_zero_errors':holdout['zero_errors'],
                'holdout_max_cell_errors':holdout['max_cell_errors'],
                'endpoint_zero_errors':boundary['zero_errors'],
                'historical_exact':65025},sort_keys=True),flush=True)
        return report
    report['status']='budget_exhausted_no_promotion'
    atomic_json(root/'result.json',report)
    print('ZERO_R32_SUMMARY='+json.dumps({'status':report['status'],'step':args.max_steps,
         'promoted':False},sort_keys=True),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=('plan','train'),default='train')
    p.add_argument('--ladder-output',default='')
    p.add_argument('--zero-only-output',default='')
    p.add_argument('--r16-output',default='')
    p.add_argument('--output',default='')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--max-steps',type=int,default=30000)
    p.add_argument('--probe-every',type=int,default=250)
    p.add_argument('--batch-size',type=int,default=130)
    p.add_argument('--legacy-batch-size',type=int,default=128)
    p.add_argument('--legacy-weight',type=float,default=2.)
    p.add_argument('--zero-weight',type=float,default=3.)
    p.add_argument('--inner-weight',type=float,default=1.5)
    p.add_argument('--lr',type=float,default=.000025)
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--legacy-probe-cards',type=int,default=2048)
    p.add_argument('--dev-anchors',type=int,default=6)
    p.add_argument('--dev-views',type=int,default=2)
    p.add_argument('--test-anchors',type=int,default=12)
    p.add_argument('--train-probe-anchors',type=int,default=3)
    p.add_argument('--parent-anchor-count',type=int,default=20)
    p.add_argument('--stability-probes',type=int,default=3)
    p.add_argument('--max-cell-errors',type=int,default=1)
    p.add_argument('--max-nonzero-error-rate',type=float,default=.001)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--seed',type=int,default=DEFAULT_SEED)
    p.add_argument('--resume',action='store_true')
    return p


def main(argv=None):
    return run(parser().parse_args(argv))

if __name__=='__main__':
    main()
