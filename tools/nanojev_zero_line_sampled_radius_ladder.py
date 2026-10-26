#!/usr/bin/env python3
"""Train ONE NanoJev backbone along sampled signed residual radii to +/-4,000,000.

No MLP bank, residual/magnitude router, arithmetic inference fallback, or answer
features. Labels exist exclusively in the Python training/evaluation harness.

The parent ±32 checkpoint is a research milestone, not formally promoted.
Magnitude holdouts are fixed across stages; stages are experimental milestones,
not independent mathematical or statistically pristine final certification.
"""
from __future__ import annotations

import argparse
import hashlib
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
from tools.nanojev_zero_line_signed_radius32 import fold as r32_fold, SCHEMA as PARENT_SCHEMA
from tools import nanojev_addition_curriculum, nanojev_zero_line_ladder, nanojev_zero_line_zero_only, nanojev_zero_line_signed_radius16, nanojev_zero_line_signed_radius32

SCHEMA = 'nanojev.zero-line-sampled-residual-radius-ladder.v1'
START_RADIUS = 32
FULL_SUM_RADIUS = MAX_ABS * 2
INNER_RESIDUALS = tuple(range(-START_RADIUS, START_RADIUS + 1))
DEFAULT_SEED = 20261025


def planned_radii(target=FULL_SUM_RADIUS):
    if not 64 <= target <= FULL_SUM_RADIUS:
        raise ValueError('SAMPLED_LADDER_BAD_TARGET')
    out=[];r=64
    while r < target:
        out.append(r);r*=2
    out.append(target)
    return out


def stable_fold(m, seed=DEFAULT_SEED):
    """Keep previous R16/R32 TEST families permanently outside training/DEV.

    One new magnitude-family split persists through all new stages. Thus stage
    TEST is not a fresh, independent family after the first stage; no repeat
    after a stage holdout failure is allowed.
    """
    if m < 0 or m > MAX_ABS:
        raise ValueError('SAMPLED_LADDER_BAD_MAGNITUDE')
    old = r32_fold(m)
    if old in ('test','quarantine','endpoint'):
        return 'quarantine'
    h=hashlib.sha256(f'continuous-scale-family:{seed}:{m}'.encode()).digest()[0] % 10
    return 'train' if h < 6 else 'dev' if h < 8 else 'test'


def pair_for(m, delta, swap=False, orientation=1):
    """Given max operand magnitude m, use two bounded integer operands.

    For positive/negative delta, anchor the corresponding signed operand at m.
    Both signs and swaps enter through targets/swap, not model inputs.
    """
    if not 0 <= m <= MAX_ABS or abs(delta) > 2*m or abs(delta)>FULL_SUM_RADIUS:
        return None
    direction=(1 if delta>0 else -1) if delta else (1 if orientation>=0 else -1)
    a=direction*m;b=delta-a
    if abs(b)>m or abs(b)>MAX_ABS or max(abs(a),abs(b))!=m:
        raise AssertionError('SAMPLED_LADDER_BOUNDED_PAIR_FAILURE')
    return (b,a) if swap else (a,b)


def bins_for(max_residual):
    result=[];low=33
    while low<=max_residual:
        high=min(max_residual,2**((low-1).bit_length()))
        if high<low:high=low
        result.append((low,high))
        low=high+1
    return result


def sample_outer(rng, radius, m, cursor=0):
    possible=min(radius,2*m)
    bins=bins_for(possible)
    if not bins:return None
    lo,hi=bins[cursor%len(bins)]
    delta=rng.randint(lo,hi)
    return delta if rng.getrandbits(1) else -delta


def anchors(band, split, seed, count, stage, view=0, used=None):
    lo,hi=BANDS[band];lo=max(lo,32)
    if split not in ('train','dev','test') or count<1:raise ValueError('SAMPLED_LADDER_BAD_ANCHOR_ARGS')
    rng=random.Random(f'sampled-ladder-anchors:{seed}:{band}:{split}:{stage}:{view}')
    seen=set(used or ());out=[]
    def add(m):
        if lo<=m<=hi and m not in seen and stable_fold(m,seed)==split:
            out.append(m);seen.add(m)
    candidates=[lo,lo+1,lo+2,hi-2,hi-1,hi]
    for x in (100,1000,10000,100000,1000000,MAX_ABS):candidates.extend((x-1,x,x+1))
    for m in candidates:add(m)
    # No huge list of magnitudes; sample with a fixed local RNG.
    for _ in range(count*1300):
        if len(out)>=count:break
        add(rng.randint(lo,hi))
    if not out:raise RuntimeError(f'SAMPLED_LADDER_EMPTY_ANCHORS {band} {split}')
    return sorted(out[:count])


def evaluation_cards(radius, split, seed, stage, per_band=2, views=1, outer_per_anchor=24):
    if views<1 or per_band<1 or outer_per_anchor<1:raise ValueError('SAMPLED_LADDER_BAD_EVAL_ARGS')
    groups=[];held={i:set() for i in range(len(BANDS))}
    for v in range(views):
        rows=[]
        for band in range(len(BANDS)):
            magnitudes=anchors(band,split,seed,per_band,stage,v,held[band])
            held[band].update(magnitudes)
            for m in magnitudes:
                for delta in INNER_RESIDUALS:
                    for swap in (False,True):
                        pair=pair_for(m,delta,swap)
                        if pair:rows.append((*pair,delta,band,m))
                for orientation in (-1,1):
                    pair=pair_for(m,0,False,orientation)
                    if pair:rows.append((*pair,0,band,m))
                rng=random.Random(f'sampled-ladder-outer:{seed}:{stage}:{v}:{m}:{radius}:{split}')
                bin_count=len(bins_for(min(radius,2*m)))
                for i in range(outer_per_anchor):
                    d=sample_outer(rng,radius,m,i%max(1,bin_count))
                    if d is None:continue
                    # Balance +/- and swapped operands in each evaluation.
                    d=abs(d)*(1 if i%2 else -1)
                    pair=pair_for(m,d,bool(i%4>=2))
                    if pair:rows.append((*pair,d,band,m))
        # Deduplicate exact cards; swaps of zero collapse to identical inputs.
        rows=list({(a,b):(a,b,d,band,m) for a,b,d,band,m in rows}.values())
        if not rows:raise RuntimeError('SAMPLED_LADDER_EMPTY_EVALUATION')
        groups.append(rows)
    return groups


def train_cards(rng, radius, seed, batch_size, weights, cursor):
    if batch_size<16:raise ValueError('SAMPLED_LADDER_BATCH_TOO_SMALL')
    cards=[]
    for i in range(batch_size):
        j=cursor+i
        category=j%8
        # Outer cases focus on scale-capable bands; all bands still receive
        # exact zero / inner rehearsal. Sampling weights come ONLY from TRAIN.
        if category>=4 and radius>512 and rng.random()<.65:
            eligible=[b for b,(lo,hi) in enumerate(BANDS) if 2*hi>=radius//2]
            band=rng.choice(eligible)
        elif rng.random()<.22:
            band=rng.choices(range(len(BANDS)),weights=weights,k=1)[0]
        else:
            band=(j//8)%len(BANDS)
        lo,hi=BANDS[band];lo=max(32,lo)
        for _ in range(8000):
            m=rng.randint(lo,hi)
            if stable_fold(m,seed)=='train':break
        else:raise RuntimeError(f'SAMPLED_LADDER_TRAIN_MAGNITUDE_EXHAUSTED band={band}')
        if category==0:delta=0
        elif category<4:
            d=1+((j//8+j//128)%START_RADIUS)
            delta=d if rng.getrandbits(1) else -d
        else:
            delta=sample_outer(rng,radius,m,j//8+i)
            if delta is None:
                d=1+rng.randrange(min(START_RADIUS,2*m))
                delta=d if rng.getrandbits(1) else -d
        pair=pair_for(m,delta,bool(rng.getrandbits(1)), -1 if rng.getrandbits(1) else 1)
        if not pair:raise AssertionError('SAMPLED_LADDER_BAD_TRAIN_PAIR')
        cards.append((*pair,delta,band,m))
    rng.shuffle(cards)
    return cards,cursor+batch_size


def accuracy_report_from_predictions(rows, predictions, canonical=None, max_examples=24):
    """Evaluation uses labels, but labels/metadata NEVER enter the model."""
    if len(rows)!=len(predictions):raise ValueError('SAMPLED_LADDER_PREDICTION_COUNT_MISMATCH')
    if canonical is None:canonical=[p==d for (*_,d,__,___),p in zip(rows,predictions)]
    counts={'zero': [0,0], 'inner': [0,0], 'outer':[0,0], 'sign':[0,0]}
    bucket={};by_band={};errors=[]
    for row,p,canon in zip(rows,predictions,canonical):
        a,b,d,band,m=row
        kind='zero' if d==0 else 'inner' if abs(d)<=START_RADIUS else 'outer'
        if kind=='outer':good=(p*d>0 and abs(p-d)<=.05*abs(d)+1e-9)
        else:good=(p==d and bool(canon))
        counts[kind][1]+=1;counts[kind][0]+=int(good)
        if d!=0:
            counts['sign'][1]+=1;counts['sign'][0]+=int((int(p>0)-int(p<0))==(int(d>0)-int(d<0)))
        if kind=='outer':
            low=2**(max(5,abs(d).bit_length()-1))+1
            if abs(d)==2**(abs(d).bit_length()-1):low=max(33,low//2+1)
            label=f'{low}-{min(2**((low-1).bit_length()),FULL_SUM_RADIUS)}'
            for key in (f'band:{BANDS[band][0]}-{BANDS[band][1]}',f'residual:{label}'):
                v=bucket.setdefault(key,[0,0]);v[0]+=int(good);v[1]+=1
            z=by_band.setdefault(str(band),[0,0]);z[0]+=int(good);z[1]+=1
        if not good and len(errors)<max_examples:
            errors.append({'a':a,'b':b,'truth':d,'predicted':int(p),'band':band,'magnitude':m,'class':kind})
    def wrap(x):return {'correct':x[0],'cards':x[1],'accuracy':x[0]/x[1] if x[1] else None,'errors':x[1]-x[0]}
    return {'cards':len(rows), 'classes':{k:wrap(v) for k,v in counts.items()},
            'outer_buckets':{k:wrap(v) for k,v in bucket.items()},
            'outer_by_band':{k:wrap(v) for k,v in by_band.items()},
            'counterexamples':errors}


@torch.inference_mode()
def assess(model, rows, device, batch_size=256):
    model.eval(); preds=[];canon=[]
    from tools.nanojev_zero_line_ladder import prediction_values
    for idx in range(0,len(rows),batch_size):
        batch=rows[idx:idx+batch_size]
        output=model(input_tensor(batch,device))
        values,signs,digits=prediction_values(output)
        actual=torch.tensor([r[2] for r in batch],device=device,dtype=torch.long)
        good=(signs==actual.sign()) & (digits==target_digits(actual)).all(-1)
        preds.extend(int(v) for v in values.cpu().tolist())
        canon.extend(good.cpu().tolist())
    return accuracy_report_from_predictions(rows,preds,canon)


def gates(report,args):
    c=report['classes']
    if c['zero']['cards']==0 or c['zero']['errors']!=0:return False
    if c['inner']['cards']==0 or c['inner']['accuracy']<1-args.max_inner_error_rate:return False
    if c['outer']['cards']==0 or c['outer']['accuracy']<args.min_outer_5pct:return False
    if c['sign']['accuracy'] < args.min_sign_accuracy:return False
    for bucket,stat in report['outer_buckets'].items():
        if stat['cards']>=args.min_bucket_cards and stat['accuracy']<args.min_bucket_5pct:
            return False
    return True


def verified_research_parent(args):
    """Accept only the recorded ±32 near-perfect research checkpoint.

    No retroactive promotion of its sealed holdout. The old files are never
    mutated by this harness. Any provenance discrepancy refuses to run.
    """
    root=Path(args.r32_output).resolve()
    required=('config.json','result.json','latest.pt','test_opened.json')
    if not all((root/p).is_file() for p in required):
        raise FileNotFoundError('SAMPLED_LADDER_PARENT_FILES_MISSING')
    cfg=json.loads((root/'config.json').read_text('utf-8'))
    report=json.loads((root/'result.json').read_text('utf-8'))
    state=torch.load(root/'latest.pt',map_location='cpu',weights_only=True)
    holdout=report.get('holdout_once',{})
    if (cfg.get('schema')!=PARENT_SCHEMA or cfg.get('code_sha256')!=sha256(nanojev_zero_line_signed_radius32.__file__)
        or state.get('schema')!=PARENT_SCHEMA+'.checkpoint'
        or state.get('step')!=report.get('last_step') or state.get('streak',0)<3
        or report.get('status')!='heldout_refused'
        or holdout.get('cards')!=20280 or holdout.get('errors')!=10
        or holdout.get('zero_errors')!=0 or holdout.get('max_cell_errors')!=2
        or report.get('historical_full',{}).get('groups',{}).get('all',{}).get('passed')!=65025
        or report.get('r16_historical_dev',{}).get('passes') is not True
        or any(r.get('passes') is not True for r in report.get('previous_zero_retention',{}).values())
        or len(report.get('previous_zero_retention',{}))!=2):
        raise RuntimeError('SAMPLED_LADDER_R32_RESEARCH_PROVENANCE_REFUSED')
    cfgsource=cfg.get('source',{})
    if sha256(Path(args.r16_output)/'latest.pt')!=cfgsource.get('r16_latest_sha256'):
        raise RuntimeError('SAMPLED_LADDER_R16_SOURCE_CHANGED')
    if sha256(Path(args.zero_only_output)/'promoted.pt')!=cfgsource.get('zero_origin',{}).get('zero_promoted_sha256'):
        raise RuntimeError('SAMPLED_LADDER_ZERO_SOURCE_CHANGED')
    model,_=load_certified_model(args.ladder_output,args.device)
    model.load_state_dict(state['model'],strict=True)
    model.eval().requires_grad_(False)
    src={name:sha256(root/name) for name in required}
    return model,{'parent_status':'experimental_r32_20270_of_20280_not_formally_certified',
                  'parent_step':state['step'],'parent_sha256':src['latest.pt'],'parent_files':src,
                  'parent_seed':cfg['settings']['seed']}


def params_config(args,origin):
    keys=('seed','batch_size','legacy_batch_size','legacy_weight','inner_weight','zero_weight',
          'lr','probe_every','eval_batch_size','dev_anchors','dev_views','test_anchors',
          'outer_per_anchor','legacy_probe_cards','zero_probe_anchors','stability_probes',
          'max_inner_error_rate','min_outer_5pct','min_bucket_5pct','min_bucket_cards',
          'min_sign_accuracy','cpu_threads','target')
    source_files=(nanojev_addition_curriculum,nanojev_zero_line_ladder,nanojev_zero_line_zero_only,
                  nanojev_zero_line_signed_radius16,nanojev_zero_line_signed_radius32)
    return {'schema':SCHEMA,'source':origin,'params':{k:getattr(args,k) for k in keys},
            'code_sha256':sha256(__file__),
            'dependency_sha256':{x.__name__:sha256(x.__file__) for x in source_files},
            'testing':'fixed heldout magnitude families reused across ladder stages; research evidence, not pristine final certificate',
            'inference':'only recurrent backbone and original output heads; no oracle/banks/fallback'}


def snapshot(model,opt,rng,step,streak,weights,cursor,best):
    return {'schema':SCHEMA+'.checkpoint','model':model.state_dict(),'optimizer':opt.state_dict(),
            'rng':rng.getstate(),'torch_rng':torch.get_rng_state(),
            'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
            'step':step,'streak':streak,'weights':weights,'cursor':cursor,'best':best}


def restore(state,model,opt,rng):
    if state.get('schema')!=SCHEMA+'.checkpoint':raise RuntimeError('SAMPLED_LADDER_BAD_SNAPSHOT')
    model.load_state_dict(state['model'],strict=True)
    opt.load_state_dict(state['optimizer']);rng.setstate(state['rng']);torch.set_rng_state(state['torch_rng'])
    if torch.cuda.is_available() and state.get('cuda_rng') is not None:torch.cuda.set_rng_state_all(state['cuda_rng'])
    return state['step'],state['streak'],state['weights'],state['cursor'],state['best']


def check_args(a):
    if (a.target<64 or a.target>FULL_SUM_RADIUS or a.max_new_stages<1 or a.steps_per_stage<1
        or a.batch_size<16 or a.legacy_batch_size<1 or a.probe_every<1
        or a.eval_batch_size<1 or a.dev_views<2 or a.dev_anchors<1 or a.test_anchors<1
        or a.outer_per_anchor<1 or a.legacy_probe_cards<1 or a.zero_probe_anchors<1
        or a.stability_probes<2 or a.cpu_threads<1 or not 0<a.lr<1
        or not 0<=a.max_inner_error_rate<.1 or not 0<a.min_outer_5pct<=1
        or not 0<a.min_bucket_5pct<=1 or a.min_bucket_cards<1 or not 0<a.min_sign_accuracy<=1
        or min(a.legacy_weight,a.inner_weight,a.zero_weight)<=0):
        raise ValueError('SAMPLED_LADDER_INVALID_ARGUMENTS')
    if a.phase=='run' and a.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('SAMPLED_LADDER_CUDA_UNAVAILABLE')


def new_model_from(parent,args):
    model,_=load_certified_model(args.ladder_output,args.device)
    model.load_state_dict(parent.state_dict(),strict=True)
    for name,p in model.named_parameters():
        p.requires_grad_(name.startswith('backbone.') or name.startswith('norm.') or name in ('query','pos'))
    opt=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=args.lr,weight_decay=0.)
    return model,opt


def run_stage(args,root,radius,parent,parent_hash,original,zero_history,legacy_cards):
    path=root/'stages'/f'radius_{radius}'
    cfg={'schema':SCHEMA+'.stage','radius':radius,'parent_sha256':parent_hash,
         'settings_sha256':sha256(root/'config.json')}
    cfg_path=path/'config.json'; seal=path/'test_opened.json'
    if cfg_path.is_file():
        if json.loads(cfg_path.read_text('utf-8'))!=cfg:raise RuntimeError('SAMPLED_LADDER_STAGE_CONFIG_DRIFT')
    else:
        if path.exists() and any(path.iterdir()):raise RuntimeError('SAMPLED_LADDER_UNTRACKED_STAGE_FILES')
        path.mkdir(parents=True,exist_ok=True);atomic_json(cfg_path,cfg)
    if seal.exists():raise RuntimeError('SAMPLED_LADDER_TEST_ALREADY_OPENED')
    model,opt=new_model_from(parent,args)
    rng=random.Random(args.seed+radius)
    torch.manual_seed(args.seed+radius+107)
    weights=[1.]*len(BANDS);cursor=0;step=0;streak=0;best=1e10
    if (path/'latest.pt').exists():
        if not args.resume:raise RuntimeError('SAMPLED_LADDER_EXISTING_STAGE_REQUIRES_RESUME')
        step,streak,weights,cursor,best=restore(torch.load(path/'latest.pt',map_location='cpu',weights_only=True),model,opt,rng)
    train_probe=evaluation_cards(radius,'train',args.seed,radius,1,1,args.outer_per_anchor)[0]
    dev_views=evaluation_cards(radius,'dev',args.seed,radius,args.dev_anchors,args.dev_views,args.outer_per_anchor)
    zero_probe_rows=zero_history
    historical_probe=sample_deck(legacy_cards,args.legacy_probe_cards,args.seed+radius)
    for i in range(step+1,args.steps_per_stage+1):
        rows,cursor=train_cards(rng,radius,args.seed,args.batch_size,weights,cursor)
        model.train()
        target=torch.tensor([r[2] for r in rows],device=args.device,dtype=torch.long)
        output=model(input_tensor(rows,args.device))
        signloss=F.cross_entropy(output['sign'],target.sign()+1,reduction='none')
        digitloss=F.cross_entropy(output['digits'].transpose(1,2),target_digits(target),reduction='none').mean(-1)
        wt=torch.ones_like(target,dtype=torch.float)
        wt=torch.where(target.abs()<=32,args.inner_weight,wt)
        wt=torch.where(target==0,args.zero_weight,wt)
        signed=((signloss+digitloss)*wt).sum()/wt.sum()
        hx=torch.randint(-127,128,(args.legacy_batch_size,2),device=args.device)
        historic=legacy_loss(model,original,hx)
        loss=signed+args.legacy_weight*historic
        if not bool(torch.isfinite(loss)):
            raise FloatingPointError('SAMPLED_LADDER_NONFINITE_TRAIN_LOSS')
        opt.zero_grad(set_to_none=True);loss.backward()
        nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad),1.,error_if_nonfinite=True)
        opt.step()
        if i%args.probe_every and i!=args.steps_per_stage:continue
        training=assess(model,train_probe,args.device,args.eval_batch_size)
        weights=[1+4*(1-training['outer_by_band'].get(str(b),{'accuracy':1.})['accuracy']) for b in range(len(BANDS))]
        dev=[assess(model,view,args.device,args.eval_batch_size) for view in dev_views]
        historical=legacy_evaluate(model,historical_probe,args.device,args.eval_batch_size)
        zero_retained={key:zero_classify(model,zz,args.device,args.eval_batch_size) for key,zz in zero_probe_rows.items()}
        historical_ok=historical['groups']['all']['passed']==len(historical_probe['y']) and historical['false_zero']==0
        zero_ok=all(v['passes'] for v in zero_retained.values())
        qualifies=all(gates(x,args) for x in dev) and historical_ok and zero_ok
        streak=streak+1 if qualifies else 0
        score=sum(v['classes']['inner']['errors'] + v['classes']['outer']['errors'] + 100*v['classes']['zero']['errors'] for v in dev)
        if qualifies and score<best:
            best=score
            atomic_checkpoint(path/'best_dev.pt',{'schema':SCHEMA+'.best','model':model.state_dict(),
                'radius':radius,'step':i,'score':score,'parent_sha256':parent_hash})
        report={'schema':SCHEMA+'.stage-report','radius':radius,'status':'training',
          'step':i,'streak':streak,'loss':float(loss.detach()),'dev':dev,
          'historical_probe':historical,'zero_retention':zero_retained,'train_probe':training}
        atomic_checkpoint(path/'latest.pt',snapshot(model,opt,rng,i,streak,weights,cursor,best))
        atomic_json(path/'result.json',report)
        print('SAMPLED_LADDER_PROBE='+json.dumps({'radius':radius,'step':i,
          'loss':round(float(loss.detach()),6),
          'inner_exact':[round(x['classes']['inner']['accuracy'],6) for x in dev],
          'outer_5pct':[round(x['classes']['outer']['accuracy'],6) for x in dev],
          'zero_errors':[x['classes']['zero']['errors'] for x in dev],
          'legacy_probe_exact':historical_ok,'zero_retained':zero_ok,'streak':streak},sort_keys=True),flush=True)
        if streak<args.stability_probes:continue
        exhaustive=legacy_evaluate(model,legacy_cards,args.device,args.eval_batch_size)
        report['historical_full']=exhaustive
        if exhaustive['groups']['all']['passed']!=65025 or exhaustive['false_zero']!=0:
            streak=0;report['status']='retention_refused'
            atomic_checkpoint(path/'latest.pt',snapshot(model,opt,rng,i,streak,weights,cursor,best))
            atomic_json(path/'result.json',report)
            print('SAMPLED_LADDER_RETENTION_REFUSED='+json.dumps({'radius':radius,'step':i,
              'historical':exhaustive['groups']['all']['passed']},sort_keys=True),flush=True)
            continue
        # This is one-way. Once the fixed magnitude TEST pool has been opened
        # for this stage, its verdict must never select the checkpoint.
        atomic_json(seal,{'radius':radius,'step':i,'parent_sha256':parent_hash,
                'warning':'fixed magnitude TEST families reused for future stage research; never used for TRAIN/DEV'})
        test=evaluation_cards(radius,'test',args.seed,radius,args.test_anchors,1,args.outer_per_anchor)[0]
        holdout=assess(model,test,args.device,args.eval_batch_size)
        endpoint=[]
        for d in INNER_RESIDUALS:
            for swap in (False,True):
                p=pair_for(MAX_ABS,d,swap)
                if p:endpoint.append((*p,d,len(BANDS)-1,MAX_ABS))
        for d in (33, -33, radius, -radius, (radius+1)//2, -(radius+1)//2, FULL_SUM_RADIUS, -FULL_SUM_RADIUS):
            if 32<abs(d)<=radius:
                for swap in (False,True):
                    p=pair_for(MAX_ABS,d,swap)
                    if p:endpoint.append((*p,d,len(BANDS)-1,MAX_ABS))
        endpoint=assess(model,endpoint,args.device,args.eval_batch_size)
        report['sealed_test']=holdout;report['endpoint_diagnostic']=endpoint
        if (gates(holdout,args) and endpoint['classes']['zero']['errors']==0
            and (radius<FULL_SUM_RADIUS or endpoint['classes']['outer']['accuracy']>=args.min_outer_5pct)):
            report['status']='promoted'
            atomic_checkpoint(path/'promoted.pt',{'schema':SCHEMA+'.promoted',
                'model':model.state_dict(),'radius':radius,'step':i,
                'parent_sha256':parent_hash,'config_sha256':sha256(cfg_path),
                'historical_exact':65025,'dev_views':dev,'sealed_test':holdout,
                'zero_retention':zero_retained,'endpoint_diagnostic':endpoint,
                'certificate_scope':'sampled research-stage evaluation; fixed TEST magnitudes reused across stages'})
            atomic_json(path/'acceptance.json',{'schema':SCHEMA+'.accepted-stage',
                'radius':radius,'promoted_sha256':sha256(path/'promoted.pt'),
                'parent_sha256':parent_hash,'stage_config_sha256':sha256(cfg_path)})
        else:report['status']='heldout_refused'
        atomic_json(path/'result.json',report)
        print('SAMPLED_LADDER_CERTIFICATION='+json.dumps({'radius':radius,
              'status':report['status'],'step':i,'test_zero_errors':holdout['classes']['zero']['errors'],
              'test_inner_exact':holdout['classes']['inner']['accuracy'],
              'test_outer_5pct':holdout['classes']['outer']['accuracy'],
              'history_exact':65025},sort_keys=True),flush=True)
        return report
    report={'schema':SCHEMA+'.stage-report','status':'budget_exhausted',
        'radius':radius,'step':args.steps_per_stage,'resume_available':True}
    atomic_json(path/'result.json',report)
    return report


def run(args):
    check_args(args)
    plan=planned_radii(args.target)
    if args.phase=='plan':
        result={'radii':plan,'stages':len(plan),'first_radius':plan[0],
           'final_radius':plan[-1],'inner_exact_interval':[-32,32],
           'operand_range':[-MAX_ABS,MAX_ABS],'sum_range':[-FULL_SUM_RADIUS,FULL_SUM_RADIUS],
           'holdout_policy':'fixed embargoed magnitude test pool, reused across stage reports'}
        print('SAMPLED_LADDER_PLAN='+json.dumps(result,sort_keys=True),flush=True)
        return result
    torch.set_num_threads(args.cpu_threads)
    root=Path(args.output).resolve();config_path=root/'config.json'
    parent,origin=verified_research_parent(args)
    config=params_config(args,origin)
    if args.resume:
        if not config_path.exists() or json.loads(config_path.read_text('utf-8'))!=config:
            raise RuntimeError('SAMPLED_LADDER_CONFIG_OR_SOURCE_DRIFT')
    else:
        if root.exists() and any(root.iterdir()):raise FileExistsError('SAMPLED_LADDER_OUTPUT_EXISTS')
        root.mkdir(parents=True,exist_ok=True);atomic_json(config_path,config)
    original,_=load_certified_model(args.ladder_output,args.device)
    original.eval().requires_grad_(False)
    legacy_cards=deck(127)
    zs=int(json.loads((Path(args.zero_only_output)/'config.json').read_text('utf-8'))['settings']['seed'])
    zero_history={split:zero_probe(split,zs,args.zero_probe_anchors) for split in ('dev','test')}
    completed=[]
    for radius in plan:
        path=root/'stages'/f'radius_{radius}'
        published=path/'promoted.pt'
        if published.is_file():
            accepted=path/'acceptance.json'
            if not accepted.is_file():raise RuntimeError('SAMPLED_LADDER_MISSING_STAGE_ACCEPTANCE')
            record=json.loads(accepted.read_text('utf-8'))
            if (record.get('schema')!=SCHEMA+'.accepted-stage'
                or record.get('radius')!=radius or record.get('promoted_sha256')!=sha256(published)
                or record.get('parent_sha256')!=origin['parent_sha256']
                or record.get('stage_config_sha256')!=sha256(path/'config.json')):
                raise RuntimeError('SAMPLED_LADDER_ACCEPTED_STAGE_SHA_DRIFT')
            state=torch.load(published,map_location='cpu',weights_only=True)
            if state.get('schema')!=SCHEMA+'.promoted' or state.get('radius')!=radius:
                raise RuntimeError('SAMPLED_LADDER_INVALID_PRIOR_STAGE')
            if state.get('parent_sha256')!=origin['parent_sha256']:
                raise RuntimeError('SAMPLED_LADDER_PRIOR_CHAIN_PROVENANCE_DRIFT')
            parent.load_state_dict(state['model'],strict=True)
            origin['parent_sha256']=sha256(published)
            completed.append(radius)
            continue
        if (path/'test_opened.json').is_file():
            raise RuntimeError(f'SAMPLED_LADDER_REFUSED_TEST_ALREADY_OPENED radius={radius}')
        if len(completed)>=args.max_new_stages:break
        report=run_stage(args,root,radius,parent,origin['parent_sha256'],original,zero_history,legacy_cards)
        if report['status']!='promoted':
            final={'status':'halted_'+report['status'],'covered_radius':completed[-1] if completed else START_RADIUS,
                'successful_radii':completed,'pending_radius':radius,'stage_report':str(path/'result.json')}
            atomic_json(root/'manifest.json',final)
            print('SAMPLED_LADDER_SUMMARY='+json.dumps(final,sort_keys=True),flush=True)
            return final
        published=path/'promoted.pt'
        state=torch.load(published,map_location='cpu',weights_only=True)
        parent.load_state_dict(state['model'],strict=True)
        origin['parent_sha256']=sha256(published)
        completed.append(radius)
        atomic_json(root/'manifest.json',{'status':'in_progress','covered_radius':radius,
            'successful_radii':completed,'promoted_checkpoint':str(published)})
    final={'status':'target_reached_sample_evaluated' if completed and completed[-1]==plan[-1] else 'stage_limit_reached',
           'covered_radius':completed[-1] if completed else START_RADIUS,
           'successful_radii':completed,'remaining_radii':plan[len(completed):],
           'research_not_exhaustive':True}
    atomic_json(root/'manifest.json',final)
    print('SAMPLED_LADDER_SUMMARY='+json.dumps(final,sort_keys=True),flush=True)
    return final


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=('plan','run'),default='run')
    p.add_argument('--ladder-output',default='')
    p.add_argument('--zero-only-output',default='')
    p.add_argument('--r16-output',default='')
    p.add_argument('--r32-output',default='')
    p.add_argument('--output',default='')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--target',type=int,default=FULL_SUM_RADIUS)
    p.add_argument('--max-new-stages',type=int,default=1)
    p.add_argument('--steps-per-stage',type=int,default=15000)
    p.add_argument('--probe-every',type=int,default=250)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--legacy-batch-size',type=int,default=128)
    p.add_argument('--legacy-weight',type=float,default=2.)
    p.add_argument('--inner-weight',type=float,default=1.5)
    p.add_argument('--zero-weight',type=float,default=3.)
    p.add_argument('--lr',type=float,default=.000025)
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--dev-anchors',type=int,default=2)
    p.add_argument('--dev-views',type=int,default=2)
    p.add_argument('--test-anchors',type=int,default=8)
    p.add_argument('--outer-per-anchor',type=int,default=24)
    p.add_argument('--legacy-probe-cards',type=int,default=2048)
    p.add_argument('--zero-probe-anchors',type=int,default=20)
    p.add_argument('--stability-probes',type=int,default=3)
    p.add_argument('--max-inner-error-rate',type=float,default=.001)
    p.add_argument('--min-outer-5pct',type=float,default=.90)
    p.add_argument('--min-bucket-5pct',type=float,default=.80)
    p.add_argument('--min-bucket-cards',type=int,default=16)
    p.add_argument('--min-sign-accuracy',type=float,default=.99)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--seed',type=int,default=DEFAULT_SEED)
    p.add_argument('--resume',action='store_true')
    return p


def main(argv=None):
    return run(parser().parse_args(argv))


if __name__=='__main__':main()
