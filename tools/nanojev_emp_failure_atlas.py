#!/usr/bin/env python3
"""Adaptive, read-only NanoJev arithmetic failure atlas.

Tier 1: deterministic, varied, checkpoint-fixed census (streaming gzip shards).
Tier 2: dense neighborhoods, activation differences, and measured ablations.
Cross-event: discovered signatures plus held-out magnitude-band validation.

A historical event is *only* reproduced with its own probe_snapshot.pt.
With --checkpoint-source latest, every result describes the FINAL model.
No campaign model, optimizer, detector, or training artifacts are modified.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import random
import tempfile
from types import SimpleNamespace

import torch
import torch.nn.functional as F

from tools import nanojev_emp_first_failure as ff
from tools import nanojev_emp_failure_neighborhood as nb
from tools import nanojev_emp_hard_lasso as lasso
from tools.nanojev_zero_line_zero_only import MAX_ABS
from tools.nanojev_zero_line_ladder import atomic_json, atomic_checkpoint
from tools.nanojev_zero_line_radius255_probe import sha256

SCHEMA = 'nanojev.emp-adaptive-failure-atlas.v1'


def magnitude_band(m):
    return max(0, int(m).bit_length() - 1)


def independent_split(m, seed):
    """Hold out WHOLE log2 magnitude bands, not neighbors of a training case."""
    band = magnitude_band(m)
    h = hashlib.sha256(f'atlas-band:{seed}:{band}'.encode()).digest()[0] % 5
    return 'validation' if h == 0 else 'discovery'


def make_variant(m, delta, sign, swap):
    a = sign * m
    b = -a + delta
    if swap:
        a, b = b, a
    if max(abs(a), abs(b)) > MAX_ABS:
        return None
    return {'card':list(ff.card(a,b)), 'm':m, 'delta':delta,
            'sign':sign, 'swap':bool(swap), 'band':magnitude_band(m),
            'branch': 'same_sign' if a * b > 0 else 'opposite_or_zero'}


def varied_cases(seed, count, min_m, max_m, delta_max):
    """Deterministic failure-independent census, including the hard zero-line."""
    rng = random.Random(f'atlas-census-v1:{seed}:{count}:{min_m}:{max_m}:{delta_max}')
    seen = set()
    found = 0
    attempts = 0
    loglo, loghi = math.log2(min_m), math.log2(max_m)
    delta_landmarks = (0,1,-1,2,-2,3,-3,4,-4,8,-8,16,-16,32,-32,64,-64)
    while found < count:
        attempts += 1
        if attempts > count * 100:
            raise RuntimeError(f'ATLAS_CENSUS_EXHAUSTED:{found}:{count}')
        style = attempts % 6
        if style == 0:
            m = rng.randint(min_m,max_m)
        elif style == 1:
            m = min(max_m,max(min_m,round(2**rng.uniform(loglo,loghi))))
        elif style == 2:
            p = rng.randint(math.floor(loglo), math.ceil(loghi))
            m = min(max_m,max(min_m,2**p+rng.choice((-2,-1,0,1,2))))
        elif style == 3:
            m = rng.randint(min_m, min(max_m, max(min_m,64)))
        elif style == 4:
            m = rng.randint(min_m, min(max_m,max(min_m,2048)))
        else:
            m = min(max_m,max(min_m,int(round(10**rng.uniform(math.log10(min_m),math.log10(max_m))))))
        if attempts % 5 == 0:
            # Same-sign addition is included as a distinct arithmetic regime.
            d = rng.choice((-1,1))*rng.randint(m+1, max(m+1, m+delta_max))
        elif attempts % 3 == 0:
            d = rng.choice(delta_landmarks)
        else:
            d = rng.randint(-delta_max,delta_max)
        if abs(d) > delta_max and attempts % 5 != 0:
            continue
        sign = -1 if rng.randrange(2) else 1
        swap = bool(rng.randrange(2))
        row = make_variant(m,d,sign,swap)
        if row is None:
            continue
        key=tuple(row['card'][:2])
        if key in seen:
            continue
        seen.add(key)
        row['split'] = independent_split(m,seed)
        row['census_id'] = found
        found += 1
        yield row


def score_chunk(model, rows, device, batch_size):
    # Only inputs and numeric predictions are collected in the large census.
    params = SimpleNamespace(device=device, eval_batch=batch_size, failure_mode='exact')
    return ff.score_cards(model, rows, params)


def atomic_gzip(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    try:
        with gzip.open(tmp,'wt',encoding='utf-8') as handle:
            for r in rows:
                handle.write(json.dumps({**r,'card':list(r['card'])},sort_keys=True)+'\n')
        os.replace(tmp,path)
    finally:
        if tmp.exists():
            tmp.unlink()


def stream_census(folder):
    for shard in sorted(folder.glob('chunk_*.jsonl.gz')):
        meta=shard.with_suffix('').with_suffix('.meta.json')
        if not meta.exists():
            raise RuntimeError(f'ATLAS_INCOMPLETE_CENSUS_SHARD:{shard.name}')
        details=json.loads(meta.read_text(encoding='utf-8'))
        if details['sha256'] != sha256(shard):
            raise RuntimeError(f'ATLAS_CENSUS_SHA_MISMATCH:{shard.name}')
        with gzip.open(shard,'rt',encoding='utf-8') as handle:
            for line in handle:
                yield json.loads(line)


def collect_census(model, output, args):
    output.mkdir(parents=True,exist_ok=True)
    completed=[]
    totals=Counter()
    stats=defaultdict(lambda:Counter())
    items=varied_cases(args.seed,args.census_cases,args.min_magnitude,args.max_magnitude,args.max_delta)
    for ix,start in enumerate(range(0,args.census_cases,args.chunk_size)):
        chunk=[next(items) for _ in range(min(args.chunk_size,args.census_cases-start))]
        target=output/f'chunk_{ix:05d}.jsonl.gz'
        meta=output/f'chunk_{ix:05d}.meta.json'
        if target.exists() and meta.exists():
            info=json.loads(meta.read_text(encoding='utf-8'))
            if info['start'] != start or info['count'] != len(chunk) or info['sha256'] != sha256(target):
                raise RuntimeError(f'ATLAS_CENSUS_RESUME_MISMATCH:{ix}')
            # The committed archive is authoritative when resuming.
            completed.append(ix)
            continue
        if target.exists() or meta.exists():
            # No committed metadata: an interrupted write is never trusted.
            target.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
        scored=score_chunk(model,chunk,args.device,args.batch_size)
        atomic_gzip(target,scored)
        info={'schema':SCHEMA+'.census-shard','start':start,'count':len(scored),
              'sha256':sha256(target)}
        atomic_json(meta,info)
        completed.append(ix)
        print('EMP_FAILURE_ATLAS_CENSUS='+json.dumps({'chunk':ix,'scanned':start+len(chunk),
            'errors':sum(not s['exact'] for s in scored), 'archive':str(target)},sort_keys=True),flush=True)
    for row in stream_census(output):
        totals['cases']+=1
        totals['errors']+=int(not row['exact'])
        totals[f"{row['split']}_cases"]+=1
        totals[f"{row['split']}_errors"]+=int(not row['exact'])
        key=(row['band'],row['split'],row['branch'])
        stats[key]['cases']+=1
        stats[key]['errors']+=int(not row['exact'])
    if totals['cases']!=args.census_cases:
        raise RuntimeError('ATLAS_INCOMPLETE_CENSUS')
    rows=[{'band':b,'split':s,'branch':br,**v} for (b,s,br),v in sorted(stats.items())]
    atomic_json(output/'census_summary.json',{'schema':SCHEMA+'.census','totals':dict(totals),'strata':rows})
    return dict(totals), rows


def select_centers(folder, args):
    """Choose diverse failures without using their activation signatures."""
    pools={'discovery':defaultdict(list),'validation':defaultdict(list)}
    for row in stream_census(folder):
        if row['exact']:
            continue
        # Avoid preferring any hypothesized error cause; balance across bands,
        # residual bins, sign branches and error direction for independent events.
        key=(row['band'], row['branch'],
             (0 if row['delta']==0 else (1 if row['delta']>0 else -1)),
             int(row['prediction']>row['card'][2]))
        pools[row['split']][key].append(row)
    chosen={}
    for split,n in (('discovery',args.discovery_centers),('validation',args.validation_centers)):
        groups=pools[split]
        rng=random.Random(f'atlas-center:{args.seed}:{split}')
        for values in groups.values():
            rng.shuffle(values)
        keys=sorted(groups)
        rng.shuffle(keys)
        picked=[]
        while len(picked)<n and any(groups.values()):
            for key in keys:
                if groups[key]:
                    picked.append(groups[key].pop())
                    if len(picked)>=n:
                        break
        chosen[split]=picked
    return chosen


def local_grid(center, args):
    rows=nb.neighborhood(center,args.m_window,args.delta_window, extra_scale=())
    for r in rows:
        r['split']=independent_split(r['m'],args.seed)
        r['band']=magnitude_band(r['m'])
    # A split is assigned by the shell's log2 magnitude family.
    # Near a band edge, exclude a neighboring band from attribution controls.
    return [r for r in rows if r['split']==center['split'] and r['band']==center['band']]


def inspect_center(model, center, dest, args, idx):
    entries=local_grid(center,args)
    if not entries or not any(r['original_center'] for r in entries):
        raise RuntimeError('ATLAS_CENTER_LOST_IN_HELDOUT_FOLD')
    scored, acts=nb.score_with_activations(model,entries,args.device)
    ci=next(i for i,r in enumerate(scored) if r['original_center'])
    if scored[ci]['prediction'] != center['prediction']:
        raise RuntimeError('ATLAS_FIXED_CHECKPOINT_PREDICTION_CHANGED')
    if scored[ci]['exact']:
        raise RuntimeError('ATLAS_SELECTED_FAILURE_BECAME_CORRECT')
    controls=nb.select_controls(center,scored,args.controls)
    indices=[i for i,r in enumerate(scored) if r in controls]
    ranks=ff.rank_channels(acts[ci],[acts[i] for i in indices])
    out=dest/f'{center["split"]}_{idx:04d}'
    out.mkdir(parents=True,exist_ok=True)
    nb.svg_grid(out/'arithmetic_heatmap.svg',scored,center,'correct','Actual arithmetic success/failure')
    heat=[]
    if indices and ranks:
        coords=[(r['layer'],r['channel']) for r in ranks[:args.activation_top]]
        baseline={}
        for layer,ch in coords:
            v=torch.tensor([float(acts[i][layer][ch]) for i in indices])
            baseline[(layer,ch)]=(float(v.mean()),max(.05,float(v.std(unbiased=False))))
        for case,activation in zip(scored,acts):
            z=[abs(float(activation[layer][ch])-mu)/sd
               for (layer,ch),(mu,sd) in baseline.items()]
            heat.append({**case,'anomaly':sum(z)/len(z)})
    else:
        heat=[{**case,'anomaly':0.0} for case in scored]
    nb.svg_grid(out/'activation_heatmap.svg',heat,center,'anomaly',
                'Activation difference versus nearest correct controls')
    atomic_gzip(out/'local_predictions.jsonl.gz',heat)
    ablate=[]
    if controls and args.ablate_top:
        ablate=ff.interventions(model,scored[ci],controls,ranks,
                 SimpleNamespace(device=args.device,failure_mode='exact',ablate_top=args.ablate_top))
    # Only the selected failure, selected controls, and nearest other failures
    # need stored full activations, not every voxel in the dense local grid.
    other=[i for i,r in enumerate(scored) if not r['exact'] and i!=ci]
    selected=[ci,*indices,*other[:args.other_failures]]
    atomic_checkpoint(out/'selected_activations.pt',{'schema':SCHEMA+'.selected-activations',
        'selected':[{'role':'failure' if i==ci else 'easy' if i in indices else 'other_failure',
                     'card':list(scored[i]['card']),
                     'activations':acts[i]} for i in selected]})
    report={'schema':SCHEMA+'.event','split':center['split'],'event_index':idx,
        'center':{**center,'card':list(center['card'])},
        'neighborhood_cases':len(scored),'neighborhood_wrong':sum(not r['exact'] for r in scored),
        'correct_controls':len(indices),'attribution_valid':bool(indices),
        'top_channels':ranks[:args.activation_top],
        'interventions':ablate, 'other_failures':len(other),
        'neighborhood':str(out),
        'causal_scope':'zero-ablation of a selected channel; local repair is not a universal causal mechanism'}
    # Report last: incomplete analysis never masquerades as a completed event.
    atomic_json(out/'event.json',report)
    return report


def feature_key(r):
    return (r['layer'],int(r['channel']))


def patterns(events, min_recurring=2):
    discovered=[r for r in events if r['split']=='discovery' and r['attribution_valid']]
    validated=[r for r in events if r['split']=='validation' and r['attribution_valid']]
    count=Counter(feature_key(f) for r in discovered for f in r['top_channels'][:8])
    recurrent=[k for k,n in count.most_common() if n>=min_recurring]
    verification=[]
    for layer,ch in recurrent[:32]:
        validations=sum((layer,ch) in {feature_key(f) for f in r['top_channels'][:8]}
                        for r in validated)
        corrective=sum(any(x['layer']==layer and x['channel']==ch and x['rescued']
                for x in r['interventions']) for r in discovered)
        corroborated=sum(any(x['layer']==layer and x['channel']==ch and x['rescued']
                for x in r['interventions']) for r in validated)
        verification.append({'layer':layer,'channel':ch,'discovery_top8':count[(layer,ch)],
          'validation_top8':validations,'discovery_repairs':corrective,
          'validation_repairs':corroborated})
    # Recurring errors are determined after census rather than preselected.
    errors=Counter((r['center']['prediction']-r['center']['card'][2]) for r in discovered)
    return {'discovery_events_with_controls':len(discovered),
        'validation_events_with_controls':len(validated),
        'recurrence_min':min_recurring,'recurring_features':verification,
        'discovered_output_errors':[{'error':k,'events':v} for k,v in errors.most_common()],
        'validation_is_independent_magnitude_band':True,
        'pattern_emerged':any(r['discovery_top8']>=max(3,min_recurring) and
              r['validation_top8']>=2 and r['validation_repairs']>=1 for r in verification),
        'caveat':'Top-channel recurrence is association; selected zero ablations are local interventions.'}


def predictive_check(events, n_features=16, epochs=200):
    """Small, explicitly exploratory cross-band linear activation classifier.

    Channel selection, standardization and fitting use DISCOVERY events ONLY.
    All VALIDATION event activations are excluded until final scoring. The test
    uses selected failure neighborhoods, not a natural prevalence estimate.
    """
    primary=[e for e in events if e['split']=='discovery' and e['attribution_valid']]
    heldout=[e for e in events if e['split']=='validation' and e['attribution_valid']]
    if len(primary)<2 or not heldout:
        return {'status':'insufficient_events','discovery':len(primary),'validation':len(heldout)}
    freq=Counter(feature_key(t) for e in primary for t in e['top_channels'][:8])
    keys=[k for k,_ in freq.most_common(n_features)]
    if not keys:
        return {'status':'insufficient_channels'}
    def rows(source):
        X=[];Y=[]; group=[]
        for event in source:
            path=Path(event['neighborhood'])/'selected_activations.pt'
            data=torch.load(path,map_location='cpu',weights_only=True)
            for item in data['selected']:
                if item['role']=='other_failure':
                    continue
                acts=item['activations']
                if any(layer not in acts or ch>=acts[layer].numel() for layer,ch in keys):
                    continue
                X.append([float(acts[layer][ch]) for layer,ch in keys])
                Y.append(1 if item['role']=='failure' else 0)
                group.append(event['event_index'])
        return X,Y,group
    train,y,gr=rows(primary)
    dev,z,gdev=rows(heldout)
    if min(Counter(y).values(),default=0)<2 or min(Counter(z).values(),default=0)<1:
        return {'status':'insufficient_labels','discovery_samples':len(y),'validation_samples':len(z)}
    x=torch.tensor(train,dtype=torch.float32); labels=torch.tensor(y,dtype=torch.float32)
    v=torch.tensor(dev,dtype=torch.float32); truth=torch.tensor(z,dtype=torch.float32)
    mu=x.mean(0); std=x.std(0,unbiased=False).clamp_min(.05)
    x=((x-mu)/std).clamp(-15,15);v=((v-mu)/std).clamp(-15,15)
    # Fixed balanced logistic objective. Do not tune threshold using validation.
    w=torch.zeros(x.shape[1],requires_grad=True)
    b=torch.zeros((),requires_grad=True)
    opt=torch.optim.Adam([w,b],lr=.04)
    pos=labels.sum().clamp_min(1)
    weight=torch.where(labels>0,len(labels)/(2*pos),len(labels)/(2*(len(labels)-pos).clamp_min(1)))
    for _ in range(epochs):
        opt.zero_grad()
        loss=(F.binary_cross_entropy_with_logits(x@w+b,labels,reduction='none')*weight).mean()+.01*w.square().sum()
        loss.backward();opt.step()
    with torch.no_grad():
        pred=(torch.sigmoid(v@w+b)>=.5).long()
        yy=truth.long()
    tp=int(((pred==1)&(yy==1)).sum());fp=int(((pred==1)&(yy==0)).sum())
    fn=int(((pred==0)&(yy==1)).sum());tn=int(((pred==0)&(yy==0)).sum())
    return {'status':'scored','selected_channels':[{'layer':l,'channel':c} for l,c in keys],
        'train_cases':len(train),'validation_cases':len(dev),'train_failure_cases':sum(y),
        'validation_failure_cases':sum(z), 'tp':tp,'fp':fp,'fn':fn,'tn':tn,
        'precision':tp/(tp+fp) if tp+fp else None,
        'recall':tp/(tp+fn) if tp+fn else None,
        'balanced_accuracy':.5*(tp/(tp+fn)+tn/(tn+fp)) if tp+fn and tn+fp else None,
        'warning':'Adaptive balanced neighborhood sample, not an unbiased deployment estimate; independent log2 magnitude bands.'}


def atlas_summary_svg(path,strata):
    bands=sorted({x['band'] for x in strata})
    names=['opposite_or_zero','same_sign']
    w=max(480,145+len(bands)*30);h=180
    rows=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">',
      '<rect width="100%" height="100%" fill="#fafafa"/>',
      '<text x="12" y="18" font-family="sans-serif" font-size="13">Atlas arithmetic census: failure rate by magnitude and operand sign</text>']
    folded=defaultdict(lambda:Counter())
    for item in strata:
        folded[(item['band'],item['branch'])].update({'cases':item['cases'],'errors':item['errors']})
    for j,name in enumerate(names):
        rows.append(f'<text x="105" y="{70+j*40}" text-anchor="end" font-family="sans-serif" font-size="10">{name}</text>')
        for i,band in enumerate(bands):
            cnt=folded[(band,name)]['cases'];errs=folded[(band,name)]['errors']
            value=errs/cnt if cnt else 0
            r=round(216+30*value);g=round(239-178*value);b=round(230-160*value)
            rows.append(f'<rect x="{110+i*30}" y="{52+j*40}" width="27" height="26" fill="rgb({r},{g},{b})"/>')
            rows.append(f'<text x="{123+i*30}" y="{93+j*40}" text-anchor="middle" font-size="8">{band}</text>')
    rows.append('</svg>')
    path.write_text('\n'.join(rows),encoding='utf-8')



def activation_recurrence_svg(path, events, result):
    """Observed top-8 channel recurrence; selected repairs marked with a star."""
    candidates=result['recurring_features'][:24]
    if not candidates:
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="550" height="55">'
                        '<rect width="100%" height="100%" fill="#ffffff"/>'
                        '<text x="10" y="30" font-family="sans-serif">'
                        'No recurring channels above the discovery support threshold'
                        '</text></svg>',encoding='utf-8')
        return
    width=245+len(events)*20
    height=85+len(candidates)*20
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
         '<rect width="100%" height="100%" fill="#ffffff"/>',
         '<text x="12" y="17" font-size="12" font-family="sans-serif">Anomalous channels by independent failure neighborhood</text>']
    for j,ev in enumerate(events):
        split_mark='V' if ev['split']=='validation' else 'D'
        svg.append(f'<text x="{249+j*20}" y="42" text-anchor="middle" font-size="9">{split_mark}{ev["event_index"]}</text>')
    for i,f in enumerate(candidates):
        y=53+i*20
        name=f'{f["layer"]} / {f["channel"]}'
        from xml.sax.saxutils import escape as xml_escape
        svg.append(f'<text x="235" y="{y+13}" text-anchor="end" font-size="9" font-family="monospace">{xml_escape(name)}</text>')
        for j,ev in enumerate(events):
            present=any(feature_key(t)==(f['layer'],f['channel']) for t in ev['top_channels'][:8])
            fixed=any(k['layer']==f['layer'] and k['channel']==f['channel'] and k['rescued']
                      for k in ev['interventions'])
            color=('#d38372' if fixed else '#496aa4' if present else '#e6e9ed')
            x=240+j*20
            svg.append(f'<rect x="{x}" y="{y}" width="17" height="17" fill="{color}"/>')
            if fixed:
                svg.append(f'<text x="{x+8.5}" y="{y+12.5}" text-anchor="middle" font-size="11" fill="#ffffff">*</text>')
    svg.append('</svg>')
    path.write_text('\n'.join(svg),encoding='utf-8')


def load_checkpoint_and_model(args):
    run=Path(args.run).expanduser().resolve()
    config=json.loads((run/'config.json').read_text(encoding='utf-8'))
    if config['schema']!=ff.SCHEMA:
        raise RuntimeError('ATLAS_UNSUPPORTED_FIRST_FAILURE_RUN')
    if args.checkpoint_source=='event':
        if args.ring is None:
            raise RuntimeError('ATLAS_EVENT_REQUIRES_RING')
        path=run/f'ring_{args.ring:02d}'/'probe_snapshot.pt'
        if not path.is_file():
            raise RuntimeError('ATLAS_EVENT_SNAPSHOT_MISSING; old runs require --checkpoint-source latest')
        mode='event_snapshot'
    else:
        path=run/'latest.pt'
        mode='final_checkpoint'
    state=torch.load(path,map_location='cpu',weights_only=True)
    schema=ff.SCHEMA+('.probe-snapshot.v1' if mode=='event_snapshot' else '.checkpoint')
    if state['schema']!=schema or state['config']!=config or (mode=='event_snapshot' and state['ring']!=args.ring):
        raise RuntimeError('ATLAS_MODEL_CONFIG_DRIFT')
    campaign=Path(args.campaign).expanduser().resolve()
    sidecar=campaign/args.hard_state_dir/'latest.pt'
    model,_,_,a,b=lasso.initialize(campaign,args.ladder_output,sidecar,args.device)
    if a!=config['source_hard_sha256'] or b!=config['source_reverse_sha256']:
        raise RuntimeError('ATLAS_SOURCE_SNAPSHOT_DRIFT')
    model.load_state_dict(state['model']);model.eval().requires_grad_(False)
    return model, path, mode


def check_args(args):
    if (args.min_magnitude < 1 or args.max_magnitude < args.min_magnitude or
        args.max_magnitude > MAX_ABS-args.max_delta-2 or args.max_delta < 1 or
        args.census_cases < 1 or args.chunk_size<1 or args.batch_size<1 or
        args.discovery_centers<0 or args.validation_centers<0 or
        args.discovery_centers+args.validation_centers<1 or args.m_window<1 or
        args.delta_window<1 or args.controls<1 or args.ablate_top<0 or
        args.activation_top<1 or args.min_recurring<1):
        raise ValueError('ATLAS_INVALID_ARGUMENTS')


def run(args):
    check_args(args)
    model,checkpoint,mode=load_checkpoint_and_model(args)
    root=Path(args.output).expanduser().resolve()
    config={'schema':SCHEMA,'checkpoint':str(checkpoint), 'checkpoint_sha256':sha256(checkpoint),
        'checkpoint_mode':mode,'source_run':str(Path(args.run).resolve()),
        'parameters':{k:getattr(args,k) for k in ('seed','min_magnitude','max_magnitude','max_delta',
          'census_cases','chunk_size','batch_size','discovery_centers','validation_centers','m_window',
          'delta_window','controls','ablate_top','activation_top','min_recurring','other_failures')}}
    root.mkdir(parents=True,exist_ok=True)
    if (root/'config.json').exists():
        if json.loads((root/'config.json').read_text(encoding='utf-8'))!=config:
            raise RuntimeError('ATLAS_CONFIG_OR_CHECKPOINT_DRIFT')
    else:
        atomic_json(root/'config.json',config)
    counts,strata=collect_census(model,root/'census',args)
    atlas_summary_svg(root/'census_heatmap.svg',strata)
    centers=select_centers(root/'census',args)
    events=[]
    for split in ('discovery','validation'):
        for idx,center in enumerate(centers[split]):
            path=root/'events'/f'{split}_{idx:04d}'/'event.json'
            if path.exists():
                data=json.loads(path.read_text(encoding='utf-8'))
                if data['center']['card']!=center['card'] or data['split']!=split:
                    raise RuntimeError(f'ATLAS_EVENT_RESUME_MISMATCH:{path}')
            else:
                data=inspect_center(model,center,root/'events',args,idx)
                print('EMP_FAILURE_ATLAS_EVENT='+json.dumps({'split':split,'index':idx,
                    'm':center['m'],'delta':center['delta'],'controls':data['correct_controls'],
                    'repairs':sum(x['rescued'] for x in data['interventions']),
                    'output':str(path)},sort_keys=True),flush=True)
            events.append(data)
    comparison=patterns(events,args.min_recurring)
    activation_recurrence_svg(root/'cross_event_activation_heatmap.svg',events,comparison)
    prediction=predictive_check(events)
    result={'schema':SCHEMA,'status':'complete','checkpoint_mode':mode,
        'checkpoint_sha256':config['checkpoint_sha256'],'census':counts,
        'selected_events':len(events),'discovery_events':len(centers['discovery']),
        'validation_events':len(centers['validation']),
        'patterns':comparison,'prediction':prediction,
        'limitations':['Fixed-checkpoint observational atlas; no training during mapping',
         'Adaptive case families are not a natural deployment distribution',
         'Single-neuron ablation repairs are local, not unique-cause proof',
         'Only full event-time snapshots permit historical replay']}
    atomic_json(root/'result.json',result)
    print('EMP_FAILURE_ATLAS_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',required=True,help='Original first-failure experiment directory')
    p.add_argument('--campaign',required=True)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--hard-state-dir',default='hard_emp_frontier_v2')
    p.add_argument('--output',required=True)
    p.add_argument('--checkpoint-source',choices=('latest','event'),default='latest')
    p.add_argument('--ring',type=int)
    p.add_argument('--device',default='cuda' if torch.cuda.is_available() else 'cpu')
    p.add_argument('--seed',type=int,default=20261026)
    p.add_argument('--min-magnitude',type=int,default=1)
    p.add_argument('--max-magnitude',type=int,default=1000000)
    p.add_argument('--max-delta',type=int,default=64)
    p.add_argument('--census-cases',type=int,default=100000)
    p.add_argument('--chunk-size',type=int,default=4096)
    p.add_argument('--batch-size',type=int,default=256)
    p.add_argument('--discovery-centers',type=int,default=24)
    p.add_argument('--validation-centers',type=int,default=12)
    p.add_argument('--m-window',type=int,default=8)
    p.add_argument('--delta-window',type=int,default=8)
    p.add_argument('--controls',type=int,default=8)
    p.add_argument('--ablate-top',type=int,default=8)
    p.add_argument('--activation-top',type=int,default=32)
    p.add_argument('--min-recurring',type=int,default=2)
    p.add_argument('--other-failures',type=int,default=4)
    return p.parse_args()


if __name__=='__main__':
    run(parser())
