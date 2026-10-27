#!/usr/bin/env python3
"""Replay and map the local arithmetic neighborhood of archived NanoJev failures.

Requires a *probe-time* snapshot for exact historical replay. Legacy runs can
be examined at the final checkpoint using --checkpoint-source latest, but their
original predictions are only compared, never reported as reproduced.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
from pathlib import Path
from xml.sax.saxutils import escape

import torch

from tools import nanojev_emp_first_failure as ff
from tools import nanojev_emp_hard_lasso as lasso
from tools.nanojev_zero_line_zero_only import MAX_ABS
from tools.nanojev_zero_line_sampled_radius_ladder import stable_fold
from tools.nanojev_zero_line_ladder import atomic_checkpoint, atomic_json

SCHEMA = 'nanojev.emp-failure-neighborhood.v1'


def branch_for(original):
    """Recover the exact ordered, signed operand branch of the archived card."""
    m, d = int(original['m']), int(original['delta'])
    want = tuple(original['card'][:2])
    for sign in (1, -1):
        for swap in (False, True):
            pair = (sign*m, -sign*m+d)
            if swap:
                pair = pair[::-1]
            if pair == want:
                return sign, swap
    raise ValueError('NEIGHBORHOOD_CANNOT_RECOVER_OPERAND_BRANCH')


def neighborhood(original, m_window, d_window, extra_scale=(0.98, 1.02)):
    m0, d0 = int(original['m']), int(original['delta'])
    sign, swap = branch_for(original)
    magnitudes = {m for m in range(max(1, m0-m_window), m0+m_window+1)}
    magnitudes.update(max(1, round(m0*factor)) for factor in extra_scale)
    deltas = range(d0-d_window, d0+d_window+1)
    entries=[]
    for m in sorted(magnitudes):
        for d in deltas:
            a,b=sign*m, -sign*m+d
            if swap:
                a,b=b,a
            if max(abs(a),abs(b))>MAX_ABS:
                continue
            c=ff.card(a,b)
            entries.append({'card':c,'m':m,'delta':d,
                'original_center': m==m0 and d==d0,
                'm_offset':m-m0,'delta_offset':d-d0})
    if sum(x['original_center'] for x in entries)!=1:
        raise RuntimeError('NEIGHBORHOOD_MISSING_CENTER')
    return entries


def select_controls(center, scores, k):
    clean=[s for s in scores if s['exact'] and not s['original_center']]
    def distance(r):
        return abs(r['m']-center['m'])/max(1,center['m']*.02) + abs(r['delta']-center['delta'])
    return sorted(clean,key=lambda s:(distance(s),abs(s['delta_offset']),abs(s['m_offset']),s['card'][:2]))[:k]


def score_with_activations(model, entries, device):
    result=[]
    acts=[]
    for e in entries:
        guess,recordings=ff.capture(model,e['card'],device)
        gold=e['card'][2]
        row={**e,'prediction':int(guess),'exact':bool(guess==gold),
             'within_5pct':bool(ff.hard.is_correct(gold,guess))}
        result.append(row)
        acts.append(recordings)
    return result,acts


def compare_archive(model, archived, device):
    # No archived prediction can be silently replaced with a new prediction.
    rows=[archived['first_failure'],*archived.get('neighboring_successes',[]),
          *archived.get('neighboring_failures',[])]
    checks=[]
    for row in rows:
        actual,_=ff.capture(model,row['card'],device)
        checks.append({'card':row['card'],'archive_prediction':row['prediction'],
                       'replay_prediction':actual,'match':actual==row['prediction'],
                       'archive_correct':bool(row['exact']),
                       'replay_correct':actual==row['card'][2]})
    return checks


def svg_grid(path,rows,center,key,title):
    xs=sorted({r['m'] for r in rows})
    ys=sorted({r['delta'] for r in rows},reverse=True)
    xidx={v:i for i,v in enumerate(xs)}
    yidx={v:i for i,v in enumerate(ys)}
    cw,ch=18,18
    w=max(450,170+len(xs)*cw)
    h=max(120,100+len(ys)*ch)
    items=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">',
           '<rect width="100%" height="100%" fill="#f8fafc"/>',
           f'<text x="12" y="20" font-size="13" font-family="sans-serif">{escape(title)}</text>']
    for r in rows:
        x=95+cw*xidx[r['m']]
        y=33+ch*yidx[r['delta']]
        if key=='correct':
            color='#69af9e' if r['exact'] else '#d66b6b'
        else:
            value=min(1.,max(0.,math.log1p(max(0.,float(r.get(key,0.))))/math.log1p(10.)))
            color=f'rgb({int(237-35*value)},{int(240-194*value)},{int(244-190*value)})'
        items.append(f'<rect x="{x}" y="{y}" width="17" height="17" fill="{color}"/>')
        if r['original_center']:
            items.append(f'<rect x="{x}" y="{y}" width="17" height="17" fill="none" stroke="#111827" stroke-width="3"/>')
    for j,d in enumerate(ys):
        items.append(f'<text x="88" y="{46+j*ch}" text-anchor="end" font-size="9" font-family="monospace">{d}</text>')
    for i,m in enumerate(xs):
        if i%max(1,len(xs)//12)==0:
            items.append(f'<text x="{95+i*cw}" y="{52+len(ys)*ch}" font-size="9" font-family="monospace">{m}</text>')
    items.append('</svg>')
    path.write_text('\n'.join(items),encoding='utf-8')


def pack_raw(path,rows,acts):
    atomic_checkpoint(path,{'schema':SCHEMA+'.activation-grid',
        'cards':[r['card'] for r in rows],
        'coordinates':[(r['m'],r['delta']) for r in rows],
        'activations':acts})


def replay_one(run,folder,ring,args,model,state_kind):
    src=run/f'ring_{ring:02d}'/'event.json'
    if not src.is_file():
        raise FileNotFoundError(f'NEIGHBORHOOD_EVENT_MISSING:{src}')
    event=json.loads(src.read_text(encoding='utf-8'))
    center=event['first_failure']
    checks=compare_archive(model,event,args.device)
    same=all(x['match'] for x in checks)
    strict=state_kind=='event_snapshot'
    if strict and not same:
        # Fail closed: an event-time snapshot must reproduce archived predictions.
        raise RuntimeError(f'NEIGHBORHOOD_EVENT_SNAPSHOT_MISMATCH:ring={ring}')
    cases=neighborhood(center,args.m_window,args.delta_window)
    scored,acts=score_with_activations(model,cases,args.device)
    seed = int(json.loads((run/'config.json').read_text(encoding='utf-8'))['parameters']['seed'])
    for row in scored:
        row['magnitude_fold'] = stable_fold(int(row['card'][4]),seed)
    center_index=next(i for i,r in enumerate(scored) if r['original_center'])
    observed_center=scored[center_index]
    controls=select_controls(center,scored,args.controls)
    control_index=[i for i,r in enumerate(scored) if r in controls]
    differences=ff.rank_channels(acts[center_index],[acts[i] for i in control_index])
    case_diffs=[]
    if control_index:
        ranked=[(r['layer'],r['channel']) for r in differences[:args.activation_top]]
        # Each cell is the mean standardized deviation of its chosen channels
        # from the same local correct controls. The center uses the same scale.
        baseline={}
        for layer,channel in ranked:
            values=torch.tensor([float(acts[i][layer][channel]) for i in control_index])
            baseline[layer,channel]=(float(values.mean()),float(values.std(unbiased=False).clamp_min(.05)))
        for r,vec in zip(scored,acts):
            z=[abs(float(vec[layer][channel])-baseline[layer,channel][0])/baseline[layer,channel][1]
               for layer,channel in ranked]
            case_diffs.append({**r,'activation_difference':sum(z)/len(z) if z else 0.})
    else:
        case_diffs=[{**r,'activation_difference':0.} for r in scored]
    output=folder/f'ring_{ring:02d}'
    output.mkdir(parents=True,exist_ok=True)
    svg_grid(output/'arithmetic_neighborhood.svg',scored,center,'correct',f'Ring {ring} arithmetic outcomes (green correct, red wrong)')
    svg_grid(output/'activation_neighborhood.svg',case_diffs,center,'activation_difference',f'Ring {ring} activation deviations (normalized, capped)')
    channel_maps=[]
    if control_index:
        for ranked in differences[:min(8,args.activation_top)]:
            layer,channel=ranked['layer'],ranked['channel']
            values=torch.tensor([float(acts[i][layer][channel]) for i in control_index])
            mu=float(values.mean())
            sd=float(values.std(unbiased=False).clamp_min(.05))
            heat=[{**r, 'individual_channel_z': abs(float(v[layer][channel])-mu)/sd}
                  for r,v in zip(scored,acts)]
            safe_layer=layer.replace('.','_').replace('@','_')
            filename=f'channel_{safe_layer}_{channel:03d}.svg'
            svg_grid(output/filename,heat,center,'individual_channel_z',
                     f'Ring {ring} {layer} channel {channel}: deviation from nearby correct')
            channel_maps.append({'layer':layer,'channel':channel,'filename':filename,
                                 'failure_anomaly':ranked['anomaly']})
    with gzip.open(output/'all_local_predictions.jsonl.gz','wt',encoding='utf-8') as f:
        for r in case_diffs:
            f.write(json.dumps({**r,'card':list(r['card'])},sort_keys=True)+'\n')
    pack_raw(output/'local_activations.pt',scored,acts)
    # Re-measure interventions on the actual replay state, not on original logs.
    ff_args=argparse.Namespace(device=args.device,failure_mode='exact',ablate_top=args.ablate_top)
    ablations=[]
    if controls and args.ablate_top>0 and not observed_center['exact']:
        ablations=ff.interventions(model,observed_center,controls,differences,ff_args)
    report={'schema':SCHEMA,'ring':ring,'checkpoint_kind':state_kind,
            'historical_reproduction':bool(strict and same),
            'archived_prediction_matches':sum(x['match'] for x in checks),
            'archived_prediction_count':len(checks),
            'archive_comparison':checks,
            'center_original':center,'center_current':{**observed_center,'card':list(observed_center['card'])},
            'neighborhood_cases':len(scored),'neighborhood_correct':sum(s['exact'] for s in scored),
            'nearby_correct_controls':len(controls),
            'top_neuron_differences':differences[:args.activation_top],
            'per_channel_heatmaps':channel_maps,
            'interventions':ablations,
            'historical_warning':None if strict else 'Final checkpoint: an old failure may have changed; not an event-time reproduction'}
    atomic_json(output/'report.json',report)
    return report


def run(args):
    run=Path(args.run).expanduser().resolve()
    out=Path(args.output).expanduser().resolve()
    config=json.loads((run/'config.json').read_text(encoding='utf-8'))
    if config.get('schema')!=ff.SCHEMA:
        raise RuntimeError('NEIGHBORHOOD_UNRECOGNIZED_EXPERIMENT')
    rings=([int(args.ring)] if args.ring!='all' else sorted(int(p.parent.name.split('_')[-1])
           for p in run.glob('ring_*/event.json')))
    if not rings:
        raise RuntimeError('NEIGHBORHOOD_NO_FAILURE_EVENTS')
    out.mkdir(parents=True,exist_ok=True)
    manifest={'schema':SCHEMA,'run':str(run),'ring':rings,
        'parameters':{'m_window':args.m_window,'delta_window':args.delta_window,'controls':args.controls,
                      'activation_top':args.activation_top,'ablate_top':args.ablate_top,
                      'checkpoint_source':args.checkpoint_source}}
    p=out/'config.json'
    if p.exists() and json.loads(p.read_text(encoding='utf-8'))!=manifest:
        raise RuntimeError('NEIGHBORHOOD_CONFIG_DRIFT')
    atomic_json(p,manifest)
    results=[]
    for ring in rings:
        snap=run/f'ring_{ring:02d}'/'probe_snapshot.pt'
        source_kind='event_snapshot'
        if snap.is_file():
            state=torch.load(snap,map_location='cpu',weights_only=True)
            if state['schema']!=ff.SCHEMA+'.probe-snapshot.v1' or state['ring']!=ring or state['config']!=config:
                raise RuntimeError(f'NEIGHBORHOOD_SNAPSHOT_DRIFT:ring={ring}')
        else:
            if args.checkpoint_source!='latest':
                raise RuntimeError(f'NEIGHBORHOOD_EVENT_SNAPSHOT_MISSING:ring={ring}; '+
                    'rerun original experiment with the snapshot patch or explicitly use --checkpoint-source latest')
            source_kind='final_checkpoint'
            state=torch.load(run/'latest.pt',map_location='cpu',weights_only=True)
            if state['schema']!=ff.SCHEMA+'.checkpoint' or state['config']!=config:
                raise RuntimeError('NEIGHBORHOOD_LATEST_DRIFT')
        # Initialize model architecture by the original verified campaign loader,
        # then substitute the exact probe-time (or explicitly final) state.
        campaign=Path(args.campaign).expanduser().resolve()
        sidecar=campaign/args.hard_state_dir/'latest.pt'
        model,_,_,digest,reverse_digest=lasso.initialize(campaign,args.ladder_output,sidecar,args.device)
        if (digest!=config['source_hard_sha256'] or reverse_digest!=config['source_reverse_sha256']):
            raise RuntimeError('NEIGHBORHOOD_SOURCE_SNAPSHOT_CHANGED')
        model.load_state_dict(state['model'])
        model.eval()
        result=replay_one(run,out,ring,args,model,source_kind)
        results.append({'ring':ring,'historical_reproduction':result['historical_reproduction'],
             'neighborhood_cases':result['neighborhood_cases'],
             'reproduction_matches':result['archived_prediction_matches'],
             'reproduction_checks':result['archived_prediction_count'],
             'output':str(out/f'ring_{ring:02d}'/'report.json')})
        print('EMP_FAILURE_NEIGHBORHOOD='+json.dumps(results[-1],sort_keys=True),flush=True)
    atomic_json(out/'summary.json',{'schema':SCHEMA,'results':results})
    return results


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',required=True,help='Original first-failure output folder')
    p.add_argument('--campaign',required=True)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--hard-state-dir',default='hard_emp_frontier_v2')
    p.add_argument('--output',required=True)
    p.add_argument('--device',default='cuda' if torch.cuda.is_available() else 'cpu')
    p.add_argument('--ring',default='all')
    p.add_argument('--checkpoint-source',choices=('event','latest'),default='event')
    p.add_argument('--m-window',type=int,default=8)
    p.add_argument('--delta-window',type=int,default=8)
    p.add_argument('--controls',type=int,default=8)
    p.add_argument('--activation-top',type=int,default=16)
    p.add_argument('--ablate-top',type=int,default=8)
    args=p.parse_args()
    if any(getattr(args,x)<0 for x in ('m_window','delta_window','ablate_top')) or \
            args.controls<1 or args.activation_top<1 or (args.m_window==0 and args.delta_window==0):
        p.error('Invalid neighborhood dimensions')
    return args


if __name__=='__main__':
    run(parser())
