#!/usr/bin/env python3
"""Map discrete arithmetic-error and activation-smoothness radii around Atlas failures.

Read-only second pass over an existing Adaptive Failure Atlas. The original
100,000-case census is never regenerated. Probe only a fixed, SHA-verified
checkpoint; expand in (magnitude, signed residual), refine sampled transitions,
and measure observed/certified radii without assuming a continuous error surface.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import gzip
import json
import math
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape

import torch

from tools import nanojev_emp_failure_atlas as atlas
from tools import nanojev_emp_failure_neighborhood as nb
from tools import nanojev_emp_first_failure as ff
from tools.nanojev_zero_line_ladder import atomic_checkpoint, atomic_json
from tools.nanojev_zero_line_radius255_probe import sha256

SCHEMA = 'nanojev.emp-atlas-local-smooth-radius.v1'


def validate_options(args):
    if (args.max_m_radius < 1 or args.max_delta_radius < 1 or args.inner_radius < 1 or
        args.inner_radius > min(args.max_m_radius, args.max_delta_radius) or
        args.budget < (2 * args.inner_radius + 1)**2 or args.refine_rounds < 0 or
        args.activation_budget < 1 or args.controls < 1 or args.ablate_top < 0 or
        args.activation_top < 1 or args.threshold <= 0 or args.batch_size < 1 or
        args.extra_discovery < 0 or args.extra_validation < 0 or args.max_centers < 1):
        raise ValueError('ATLAS_RADIUS_INVALID_ARGUMENTS')


def source_centers(root, params, args):
    """Existing atlas events first; optional new diverse cases from SAME census."""
    seen = set()
    centers = []
    for path in sorted((root/'events').glob('*/event.json')):
        event = json.loads(path.read_text(encoding='utf-8'))
        center = event['center']
        key = tuple(center['card'][:2])
        if key in seen:
            continue
        seen.add(key)
        centers.append({'id':path.parent.name,'center':center,'original_atlas_event':True})
    if args.extra_discovery or args.extra_validation:
        candidates = atlas.select_centers(root/'census',SimpleNamespace(
            seed=params['seed'],discovery_centers=params['discovery_centers']+args.extra_discovery,
            validation_centers=params['validation_centers']+args.extra_validation))
        for split in ('discovery','validation'):
            extra = 0
            for row in candidates[split]:
                key = tuple(row['card'][:2])
                if key in seen:
                    continue
                if extra >= getattr(args,'extra_'+split):
                    break
                seen.add(key)
                centers.append({'id':f'{split}_extra_{extra:04d}',
                                'center':row,'original_atlas_event':False})
                extra += 1
    if not centers:
        raise RuntimeError('ATLAS_RADIUS_NO_CENTERS')
    return centers[:args.max_centers]


def model_for_atlas(root, atlas_config, args):
    expected = Path(atlas_config['checkpoint']).resolve()
    if not expected.is_file() or sha256(expected)!=atlas_config['checkpoint_sha256']:
        raise RuntimeError('ATLAS_RADIUS_CHECKPOINT_SHA_MISMATCH')
    source = Path(atlas_config['source_run']).resolve()
    mode = atlas_config['checkpoint_mode']
    if mode not in ('final_checkpoint','event_snapshot'):
        raise RuntimeError('ATLAS_RADIUS_UNKNOWN_CHECKPOINT_MODE')
    if mode == 'event_snapshot':
        try:
            ring = int(expected.parent.name.split('_')[-1])
        except ValueError as exc:
            raise RuntimeError('ATLAS_RADIUS_UNREADABLE_SNAPSHOT_RING') from exc
    else:
        ring = None
    model, checkpoint, loaded_mode = atlas.load_checkpoint_and_model(SimpleNamespace(
        run=str(source),campaign=args.campaign,ladder_output=args.ladder_output,
        hard_state_dir=args.hard_state_dir,device=args.device,
        checkpoint_source='event' if mode=='event_snapshot' else 'latest',ring=ring))
    if Path(checkpoint).resolve()!=expected or loaded_mode!=mode:
        raise RuntimeError('ATLAS_RADIUS_CHECKPOINT_MODE_DRIFT')
    return model


def powers_up_to(limit):
    values=[1]
    while values[-1] < limit:
        values.append(min(values[-1]*2,limit))
    return values


def initial_offsets(args):
    """Dense certified core plus sparse, scale-expanding Cartesian shells."""
    offsets={(dm,dd) for dm in range(-args.inner_radius,args.inner_radius+1)
             for dd in range(-args.inner_radius,args.inner_radius+1)}
    ms=powers_up_to(args.max_m_radius)
    ds=powers_up_to(args.max_delta_radius)
    for idx in range(max(len(ms),len(ds))):
        rm=ms[min(idx,len(ms)-1)]
        rd=ds[min(idx,len(ds)-1)]
        mm={0,rm,-rm,rm//2,-rm//2}
        dd={0,rd,-rd,rd//2,-rd//2}
        offsets.update((m,d) for m in mm for d in dd)
        # Independent axis exploration: big magnitude shifts at small residuals
        # and vice versa, not only a diagonal scale progression.
        offsets.update((m,d) for m in (rm,-rm) for d in (-1,0,1))
        offsets.update((m,d) for m in (-1,0,1) for d in (rd,-rd))
    return sorted(offsets,key=lambda q:(max(abs(q[0]),abs(q[1])),
                                         abs(q[0])+abs(q[1]),q[0],q[1]))


def branch_entries(center, offsets, args, seed):
    sign,swap=nb.branch_for(center)
    m0,d0=int(center['m']),int(center['delta'])
    entries=[]
    for dm,dd in offsets:
        if abs(dm)>args.max_m_radius or abs(dd)>args.max_delta_radius:
            continue
        m,d=m0+dm,d0+dd
        if m<1:
            continue
        row=atlas.make_variant(m,d,sign,swap)
        if row is None:
            continue
        row.update({'m_offset':dm,'delta_offset':dd,
                    'original_center':dm==0 and dd==0,
                    'split':atlas.independent_split(m,seed),
                    'cross_band':atlas.magnitude_band(m)!=center['band']})
        entries.append(row)
    return entries


def probe(model, center, offsets, cache, args, seed):
    new=[r for r in branch_entries(center,offsets,args,seed)
         if (r['m_offset'],r['delta_offset']) not in cache]
    remaining=max(0,args.budget-len(cache))
    new=new[:remaining]
    if not new:
        return 0
    scored=ff.score_cards(model,new,SimpleNamespace(device=args.device,
                           eval_batch=args.batch_size,failure_mode='exact'))
    for row in scored:
        row['error']=int(row['prediction'])-int(row['card'][2])
        row['same_error']=row['error']==int(center['prediction'])-int(center['card'][2])
        cache[(row['m_offset'],row['delta_offset'])]=row
    return len(scored)


def adjacent_pairs(rows):
    """Enumerate *sampled* consecutive positions along each coordinate line."""
    by_delta=defaultdict(list);by_m=defaultdict(list)
    for r in rows:
        by_delta[r['delta_offset']].append(r)
        by_m[r['m_offset']].append(r)
    for family,coord in ((by_delta,'m_offset'),(by_m,'delta_offset')):
        for group in family.values():
            line=sorted(group,key=lambda r:r[coord])
            for left,right in zip(line,line[1:]):
                yield left,right,coord,right[coord]-left[coord]


def refine_offsets(cache,args):
    """Target observed label/error transitions; no monotonicity assumption."""
    candidates={}
    for a,b,coord,gap in adjacent_pairs(cache.values()):
        if gap <= 1 or (a['exact']==b['exact'] and a['error']==b['error']):
            continue
        point=(a['m_offset'],a['delta_offset'])
        midpoint=(a[coord]+b[coord])//2
        pos=(midpoint,point[1]) if coord=='m_offset' else (point[0],midpoint)
        if pos in cache:
            continue
        # Prioritize close transitions, then longer unresolved intervals.
        priority=(min(abs(a['m_offset'])+abs(a['delta_offset']),
                      abs(b['m_offset'])+abs(b['delta_offset'])),gap,pos)
        candidates[pos]=priority
        # Also resolve the immediate neighbors of both known endpoints.
        for end in (a,b):
            n=end[coord]+(1 if end is a else -1)
            p=(n,end['delta_offset']) if coord=='m_offset' else (end['m_offset'],n)
            if p not in cache:
                candidates[p]=(priority[0],1,p)
    return [pos for pos,_ in sorted(candidates.items(),key=lambda item:item[1])]


def explore(model, center, args, seed):
    cache={}
    probe(model,center,initial_offsets(args),cache,args,seed)
    if (0,0) not in cache:
        raise RuntimeError('ATLAS_RADIUS_CENTER_NOT_SAMPLED')
    observed=cache[(0,0)]
    if observed['exact'] or observed['prediction'] != center['prediction']:
        raise RuntimeError('ATLAS_RADIUS_FIXED_MODEL_DRIFT')
    refinement=[]
    for round_idx in range(args.refine_rounds):
        if len(cache)>=args.budget:
            break
        offsets=refine_offsets(cache,args)
        added=probe(model,center,offsets,cache,args,seed)
        refinement.append({'round':round_idx+1,'added':added})
        if added==0:
            break
    return cache,refinement


def certified_radii(rows,center,args):
    """Fully observed Chebyshev balls on an integer lattice, NOT interpolation."""
    max_radius=args.inner_radius
    results={}
    base_error=int(center['prediction'])-int(center['card'][2])
    for label,predicate in (
        ('failure',lambda r:not r['exact']),
        ('same_error',lambda r:r['error']==base_error)):
        valid=0
        coverage=0
        first_break=None
        for radius in range(max_radius+1):
            shell=[(dm,dd) for dm in range(-radius,radius+1)
                   for dd in range(-radius,radius+1) if max(abs(dm),abs(dd))==radius
                   and int(center['m'])+dm>=1]
            if not shell or not all(p in rows for p in shell):
                break
            coverage=radius
            if not all(predicate(rows[p]) for p in shell):
                first_break=radius
                break
            valid=radius
        results[label]={'certified_radius':valid,
                        'first_fully_sampled_break_radius':first_break,
                        'fully_sampled_through':coverage,
                        'radius_capped_by_dense_core':first_break is None,
                        'metric':'integer Chebyshev / (m-offset, residual-offset)',
                        'domain':'valid m>=1; other samples not interpolated'}
    return results


def directional_radii(rows,center):
    base=int(center['prediction'])-int(center['card'][2])
    result={}
    for name,axis,sgn in (('m+','m_offset',1),('m-','m_offset',-1),
                          ('delta+','delta_offset',1),('delta-','delta_offset',-1)):
        other='delta_offset' if axis=='m_offset' else 'm_offset'
        path=sorted((r for r in rows.values() if r[other]==0 and r[axis]*sgn>0),
                    key=lambda r:abs(r[axis]))
        def first(predicate):
            found=next((r for r in path if predicate(r)),None)
            return abs(found[axis]) if found else None
        result[name]={'samples':len(path),
                      'nearest_sampled_success':first(lambda r:r['exact']),
                      'nearest_sampled_error_change':first(lambda r:r['error']!=base),
                      'sampled_max_offset':max((abs(r[axis]) for r in path),default=0),
                      'right_censored_success':not any(r['exact'] for r in path),
                      'right_censored_error_change':not any(r['error']!=base for r in path),
                      'scope':'observed sampled axis points; no assumed monotonicity'}
    return result


def sampled_component(rows):
    """Connected wrong component using ONLY unit-adjacent evaluated lattice cells."""
    if (0,0) not in rows or rows[(0,0)]['exact']:
        return {'size':0,'touches_survey_edge':False}
    todo=deque([(0,0)]);seen={(0,0)}
    while todo:
        m,d=todo.popleft()
        for k in ((m+1,d),(m-1,d),(m,d+1),(m,d-1)):
            if k not in seen and k in rows and not rows[k]['exact']:
                seen.add(k);todo.append(k)
    has_unknown_edge=any((m+dm,d+dd) not in rows for m,d in seen
                         for dm,dd in ((1,0),(-1,0),(0,1),(0,-1)))
    return {'sampled_connected_wrong_cells':len(seen),
            'touches_unsampled_edge':has_unknown_edge,
            'certified_complete':not has_unknown_edge,
            'adjacency':'unit Manhattan steps among evaluated coordinates'}


def activation_selection(cache,center,args):
    group=list(cache.values())
    same=[r for r in group if not r['cross_band'] and r['split']==center['split']]
    controls=nb.select_controls(center,same,args.controls)
    selected={(0,0)}
    selected.update((r['m_offset'],r['delta_offset']) for r in controls)
    # First cover the immediate local lattice (local gradients), then sample
    # BOTH sides of unit-spaced correctness transitions near and far away.
    near=sorted(group,key=lambda r:(max(abs(r['m_offset']),abs(r['delta_offset'])),
                                   abs(r['m_offset'])+abs(r['delta_offset'])))
    for r in near:
        if len(selected)>=args.activation_budget:
            break
        if max(abs(r['m_offset']),abs(r['delta_offset']))<=min(2,args.inner_radius):
            selected.add((r['m_offset'],r['delta_offset']))
    candidates=[]
    for a,b,_,gap in adjacent_pairs(group):
        if gap==1 and a['exact']!=b['exact']:
            priority=min(abs(a['m_offset'])+abs(a['delta_offset']),
                         abs(b['m_offset'])+abs(b['delta_offset']))
            candidates.append((priority,a,b))
    for _,a,b in sorted(candidates,key=lambda item:item[0]):
        if len(selected)>=args.activation_budget:
            break
        for r in (a,b):
            if len(selected)<args.activation_budget:
                selected.add((r['m_offset'],r['delta_offset']))
    for r in near:
        if len(selected)>=args.activation_budget:
            break
        selected.add((r['m_offset'],r['delta_offset']))
    return [cache[k] for k in sorted(selected)],controls


def capture_norm_without_encoder_hooks(model,card,device):
    """Read recurrent final-norm states without altering encoder fastpath choice.

    Hooking TransformerEncoderLayer disables a PyTorch fused encoder path on
    some torch versions. A hook on the *outer* norm does not hook those layers;
    it yields norm@0 / norm@1 observations from the original encoder route.
    This fallback never claims to observe intermediate backbone layer outputs.
    """
    if not hasattr(model,'norm'):
        raise RuntimeError('ATLAS_RADIUS_NO_NORM_FOR_PARITY_CAPTURE')
    recordings={}
    calls=[0]
    def hook(_module,_inputs,output):
        index=calls[0]
        calls[0]+=1
        recordings[f'norm@{index}']=ff.select_latent(output).detach().cpu().flatten().clone()
    handle=model.norm.register_forward_hook(hook)
    try:
        model.eval()
        with torch.inference_mode():
            pred=ff.dual.decoded(model(ff.input_tensor([card],device)))[0]
    finally:
        handle.remove()
    if not recordings:
        raise RuntimeError('ATLAS_RADIUS_NO_NORM_CAPTURED')
    return pred,recordings


def capture_selected(model,rows,device):
    """Require output parity before using any activation as error evidence.

    The scan uses batched unhooked inference. Layer hooks in ff.capture may
    disable fused TransformerEncoderLayer execution. If its singleton result
    differs, try a norm-only hook that preserves the encoder's fastpath. The
    full-hook activation is archived but never mixed into an incompatible
    neural comparison. If neither mode agrees, exclude the case's activations.
    """
    reliable={}
    raw={}
    comparisons=[]
    for row in rows:
        pos=(row['m_offset'],row['delta_offset'])
        captured_prediction,full_acts=ff.capture(model,row['card'],device)
        census_prediction=int(row['prediction'])
        full_consistent=int(captured_prediction)==census_prediction
        entry={'m_offset':pos[0],'delta_offset':pos[1],
               'card':list(row['card']),
               'scanned_prediction':census_prediction,
               'full_hook_prediction':int(captured_prediction),
               'full_hook_agrees_with_scan':full_consistent,
               'capture_mode':'full_hook' if full_consistent else 'uncertified'}
        raw[pos]=full_acts
        if full_consistent:
            reliable[pos]=full_acts
        else:
            # The single-case unhooked comparison diagnoses batch dependence.
            # Neither alternative prediction replaces the archived scan label.
            singleton=ff.score_cards(model,[row],SimpleNamespace(
                device=device,eval_batch=1,failure_mode='exact'))[0]
            entry['unhooked_single_prediction']=int(singleton['prediction'])
            entry['batch_sensitive']=int(singleton['prediction'])!=census_prediction
            entry['full_hook_sensitive']=int(singleton['prediction'])!=int(captured_prediction)
            norm_pred,norm_acts=capture_norm_without_encoder_hooks(model,row['card'],device)
            entry['norm_only_prediction']=int(norm_pred)
            if int(norm_pred)==census_prediction:
                entry['capture_mode']='norm_only'
                reliable[pos]=norm_acts
        comparisons.append(entry)
    disagreements=[x for x in comparisons if not x['full_hook_agrees_with_scan']]
    provenance={'scanned_and_captured':len(comparisons),
                'full_hook_matches':sum(x['full_hook_agrees_with_scan'] for x in comparisons),
                'full_hook_disagreements':len(disagreements),
                'norm_only_recoveries':sum(x['capture_mode']=='norm_only' for x in comparisons),
                'usable_activation_cases':len(reliable),
                'uncertified_cases':sum(x['capture_mode']=='uncertified' for x in comparisons),
                'batch_sensitive_disagreements':sum(x['batch_sensitive'] for x in disagreements),
                'hook_sensitive_disagreements':sum(x['full_hook_sensitive'] for x in disagreements),
                'center_capture_matches':(0,0) in reliable,
                'center_capture_mode':next((x['capture_mode'] for x in comparisons
                                            if (x['m_offset'],x['delta_offset'])==(0,0)),None),
                'disagreements':disagreements,
                'semantics':'Unhooked batched scan determines arithmetic labels. '
                    'Full-hook or norm-only activations are compared only when '
                    'that capture reproduces the scan prediction. '
                    'Norm-only recovery does not measure intermediate backbone layers.'}
    return reliable,raw,provenance,comparisons


def layer_distances(cache,acts,controls,center,threshold,inner_radius):
    origin=acts.get((0,0))
    if not origin or not controls:
        return {'available':False,'reason':'missing_center_or_same_band_correct_controls'}, {}, {}
    control_acts=[acts.get((r['m_offset'],r['delta_offset'])) for r in controls]
    control_acts=[a for a in control_acts if a is not None]
    if not control_acts:
        return {'available':False,'reason':'correct_controls_not_captured'}, {}, {}
    layers=sorted(set(origin).intersection(*(set(a) for a in control_acts)))
    if not layers:
        return {'available':False,'reason':'no_shared_verified_activation_layers'}, {}, {}
    baseline={}
    for layer in layers:
        stack=torch.stack([a[layer].float() for a in control_acts])
        baseline[layer]=stack.std(dim=0,unbiased=False).clamp_min(.05)
    distances={}
    for pos,vec in acts.items():
        distances[pos]={layer:float(torch.sqrt(torch.mean(
                 ((vec[layer].float()-origin[layer].float())/baseline[layer])**2)))
                 for layer in layers if layer in vec}
    pairs=list(adjacent_pairs([cache[pos] for pos in acts]))
    layer_stats={}
    for layer in layers:
        crossing=[];stable=[]
        for left,right,_,gap in pairs:
            if gap!=1:
                continue
            a=(left['m_offset'],left['delta_offset']);b=(right['m_offset'],right['delta_offset'])
            if layer not in acts[a] or layer not in acts[b]:
                # A norm-only fallback cannot certify a backbone-layer edge.
                continue
            gradient=float(torch.sqrt(torch.mean(((acts[a][layer].float()-acts[b][layer].float())/
                                                 baseline[layer])**2)))
            if left['exact']!=right['exact']:
                crossing.append(gradient)
            else:
                stable.append(gradient)
        smooth=0
        fully_sampled=-1
        breaking=None
        for radius in range(inner_radius+1):
            shell=[(dm,dd) for dm in range(-radius,radius+1)
                   for dd in range(-radius,radius+1) if max(abs(dm),abs(dd))==radius
                   and center['m']+dm>=1]
            if not shell or not all(p in distances and layer in distances[p] for p in shell):
                break
            fully_sampled=radius
            if not all(distances[p][layer]<threshold for p in shell):
                breaking=radius
                break
            smooth=radius
        def mean(x):return sum(x)/len(x) if x else None
        layer_stats[layer]={
            'activation_certified_radius':smooth,
            'activation_first_fully_sampled_break':breaking,
            'activation_fully_sampled_through':fully_sampled,
            'cross_boundary_unit_edges':len(crossing),
            'same_label_unit_edges':len(stable),
            'crossing_gradient_mean':mean(crossing),
            'stable_gradient_mean':mean(stable),
            'crossing_vs_stable_ratio':mean(crossing)/mean(stable) if crossing and stable and mean(stable)>0 else None,
            'distance_metric':'RMS standardized activation displacement from center',
            'threshold':threshold}
    return {'available':True,'layers':len(layers),'captured_points':len(acts)},distances,layer_stats


def svg_scatter(path,cache,center,values,title,kind):
    """Signed-log plotted offsets: visualization only, not a metric radius."""
    def trans(v):
        return math.copysign(math.log2(1+abs(v)),v)
    points=list(cache.keys())
    xs=[trans(p[0]) for p in points];ys=[trans(p[1]) for p in points]
    xmin,xmax=min(xs),max(xs);ymin,ymax=min(ys),max(ys)
    width,height=810,540
    def xy(k):
        x,y=trans(k[0]),trans(k[1])
        return (75+690*(x-xmin)/max(1e-10,xmax-xmin),
                470-420*(y-ymin)/max(1e-10,ymax-ymin))
    lines=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
           '<rect width="100%" height="100%" fill="#ffffff"/>',
           f'<text x="20" y="22" font-size="16" font-family="sans-serif">{escape(title)}</text>',
           '<text x="400" y="530" font-size="12" text-anchor="middle">Signed-log magnitude offset (m - m0)</text>',
           '<text x="18" y="280" font-size="12" transform="rotate(-90 18 280)" text-anchor="middle">Signed-log residual offset (delta - delta0)</text>']
    for k in points:
        row=cache[k];x,y=xy(k)
        if kind=='correctness':
            color='#c85252' if not row['exact'] else '#35947e'
        elif kind=='error':
            error=row['error']
            color=('#c85252' if error==center['prediction']-center['card'][2]
                   else '#d1a24a' if error else '#35947e')
        else:
            val=values.get(k)
            if val is None:
                color='#e5e7eb'
            else:
                t=min(1,max(0,val/5))
                color=f'rgb({int(220-60*t)},{int(233-184*t)},{int(241-188*t)})'
        lines.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.3" fill="{color}"/>')
    if (0,0) in cache:
        x,y=xy((0,0));lines.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="7.5" fill="none" stroke="#111827" stroke-width="2"/>')
    lines.append('<text x="78" y="498" font-size="10">Display coordinates use signed-log scaling; numerical radii are computed in untransformed integer coordinates.</text>')
    lines.append('</svg>')
    path.write_text('\n'.join(lines),encoding='utf-8')


def inspect(model,center,root,args,seed):
    cache,refinements=explore(model,center,args,seed)
    rows,controls=activation_selection(cache,center,args)
    acts,raw_acts,capture_provenance,comparisons=capture_selected(model,rows,args.device)
    # Do not use a scan-correct control that the instrumented path gets wrong.
    reliable_controls=[r for r in controls
        if (r['m_offset'],r['delta_offset']) in acts]
    evidence,distances,layers=layer_distances(
        cache,acts,reliable_controls,center,args.threshold,args.inner_radius)
    base=acts.get((0,0))
    control_vecs=[acts[(r['m_offset'],r['delta_offset'])] for r in reliable_controls]
    ranked=ff.rank_channels(base,control_vecs) if base is not None and control_vecs else []
    tested=[]
    if ranked and args.ablate_top:
        tested=ff.interventions(model,cache[(0,0)],reliable_controls,ranked,SimpleNamespace(
            device=args.device,failure_mode='exact',ablate_top=args.ablate_top))
    root.mkdir(parents=True,exist_ok=True)
    counted=sorted(cache.values(),key=lambda r:(r['m_offset'],r['delta_offset']))
    atlas.atomic_gzip(root/'samples.jsonl.gz',counted)
    atomic_json(root/'samples.meta.json',{'sha256':sha256(root/'samples.jsonl.gz'),'count':len(counted)})
    comparisons_by_pos={(r['m_offset'],r['delta_offset']):r for r in comparisons}
    atomic_checkpoint(root/'selected_activations.pt',{'schema':SCHEMA+'.activations',
        'selected':[{'m_offset':p[0],'delta_offset':p[1],'card':cache[p]['card'],
                     'full_hook_activation':vec,
                     'usable_activation':acts.get(p),
                     'capture':comparisons_by_pos[p]}
                    for p,vec in sorted(raw_acts.items())]})
    svg_scatter(root/'arithmetic_map.svg',cache,center,{},'Observed arithmetic correctness','correctness')
    svg_scatter(root/'error_signature_map.svg',cache,center,{},'Same signed prediction error as selected failure','error')
    activation_mean={p:sum(v.values())/len(v) for p,v in distances.items() if v}
    svg_scatter(root/'activation_distance_map.svg',cache,center,activation_mean,
                'Captured activation distance from failure (uncaptured = grey)','activation')
    report={'schema':SCHEMA+'.center','center':center,
        'sampled_points':len(cache),'sample_budget':args.budget,
        'wrong':sum(not r['exact'] for r in cache.values()),
        'correct':sum(r['exact'] for r in cache.values()),
        'same_error_count':sum(r['same_error'] for r in cache.values()),
        'cross_band_samples':sum(r['cross_band'] for r in cache.values()),
        'refinements':refinements,
        'certified':certified_radii(cache,center,args),
        'directional_observed':directional_radii(cache,center),
        'wrong_component':sampled_component(cache),
        'activation':evidence,'activation_layers':layers,
        'capture_consistency':capture_provenance,
        'selected_correct_controls':len(controls),
        'correct_controls':len(reliable_controls),
        'attribution_valid':evidence['available'],
        'top_channels':ranked[:args.activation_top],
        'interventions':tested,
        'sampling_warning':'Unprobed integer coordinates have unknown labels; signed-log plots are display only. '
          'Arithmetic labels use unhooked batched inference; only activation captures with the same prediction '
          'participate in neural statistics. Activation distances are not proof of causation.'}
    atomic_json(root/'report.json',report)  # Commit marker, last.
    return report


def summarize(centers):
    valid=[x for x in centers if x['attribution_valid']]
    patterns=atlas.patterns(centers,2)
    groups={}
    for split in ('discovery','validation'):
        subs=[x for x in centers if x['split']==split]
        good=[x for x in subs if x['attribution_valid']]
        ratios=defaultdict(list)
        for ev in good:
            for layer,entry in ev['activation_layers'].items():
                ratio=entry['crossing_vs_stable_ratio']
                if ratio is not None and entry['cross_boundary_unit_edges']>=2 and entry['same_label_unit_edges']>=2:
                    ratios[layer].append(ratio)
        groups[split]={'events':len(subs),'with_controls':len(good),
            'mean_crossing_gradient_ratio_by_layer':{k:sum(v)/len(v) for k,v in ratios.items()},
            'independent_event_support_by_layer':{k:len(v) for k,v in ratios.items()}}
    return {'schema':SCHEMA+'.summary','events':len(centers),
            'events_with_controls':len(valid),'groups':groups,
            'recurring_channel_candidates':patterns,
            'interpretation':'Discovery/validation magnitude bands kept separate; gradient-ratio statistics are descriptive. '
             'No boundary smoothness or causal transfer inferred from untested locations.'}


def run(args):
    validate_options(args)
    source=Path(args.atlas).expanduser().resolve()
    config=json.loads((source/'config.json').read_text(encoding='utf-8'))
    if config['schema']!=atlas.SCHEMA or not (source/'result.json').is_file():
        raise RuntimeError('ATLAS_RADIUS_REQUIRES_COMPLETED_ATLAS')
    params=config['parameters']
    selected=source_centers(source,params,args)
    out=Path(args.output).expanduser().resolve()
    immutable={'schema':SCHEMA,'atlas':str(source),'atlas_config_sha256':sha256(source/'config.json'),
       'checkpoint_sha256':config['checkpoint_sha256'],
       'parameters':{name:getattr(args,name) for name in ('max_m_radius','max_delta_radius',
          'inner_radius','budget','refine_rounds','activation_budget','controls','ablate_top',
          'activation_top','threshold','batch_size','extra_discovery','extra_validation','max_centers')},
       'center_cards':[[x['id'],list(x['center']['card'][:2])] for x in selected]}
    out.mkdir(parents=True,exist_ok=True)
    manifest=out/'config.json'
    if manifest.exists() and json.loads(manifest.read_text(encoding='utf-8'))!=immutable:
        raise RuntimeError('ATLAS_RADIUS_RESUME_CONFIG_DRIFT')
    if not manifest.exists():
        atomic_json(manifest,immutable)
    model=model_for_atlas(source,config,args)
    results=[]
    for item in selected:
        dest=out/'centers'/item['id'];reportpath=dest/'report.json'
        if reportpath.exists():
            report=json.loads(reportpath.read_text(encoding='utf-8'))
            metapath=dest/'samples.meta.json'
            metadata=json.loads(metapath.read_text(encoding='utf-8'))
            if (report['schema']!=SCHEMA+'.center' or report['center']['card']!=item['center']['card']
                or metadata['sha256']!=sha256(dest/'samples.jsonl.gz')
                or metadata['count']!=report['sampled_points']):
                raise RuntimeError(f'ATLAS_RADIUS_RESUME_EVIDENCE_DRIFT:{item["id"]}')
        else:
            report=inspect(model,item['center'],dest,args,params['seed'])
            print('EMP_ATLAS_RADIUS_CENTER='+json.dumps({'id':item['id'],
                  'samples':report['sampled_points'],'wrong':report['wrong'],
                  'controls':report['correct_controls'],
                  'capture_disagreements':report['capture_consistency']['full_hook_disagreements'],
                  'center_capture_matches':report['capture_consistency']['center_capture_matches'],
                  'output':str(reportpath)},sort_keys=True),flush=True)
        results.append({**report,'split':item['center']['split'],'event_index':item['id'],
                        'neighborhood':str(dest)})
    summary=summarize(results)
    atomic_json(out/'result.json',summary)
    print('EMP_ATLAS_RADIUS_RESULT='+json.dumps({'status':'complete','centers':summary['events'],
        'with_controls':summary['events_with_controls'],
        'discovery':summary['groups']['discovery'],
        'validation':summary['groups']['validation'],'output':str(out/'result.json')},sort_keys=True),flush=True)
    return summary


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--atlas',required=True,help='Completed Adaptive Failure Atlas output folder')
    p.add_argument('--campaign',required=True)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--hard-state-dir',default='hard_emp_frontier_v2')
    p.add_argument('--output',required=True)
    p.add_argument('--device',default='cuda' if torch.cuda.is_available() else 'cpu')
    p.add_argument('--max-m-radius',type=int,default=512)
    p.add_argument('--max-delta-radius',type=int,default=32)
    p.add_argument('--inner-radius',type=int,default=4)
    p.add_argument('--budget',type=int,default=1200)
    p.add_argument('--refine-rounds',type=int,default=4)
    p.add_argument('--activation-budget',type=int,default=96)
    p.add_argument('--controls',type=int,default=8)
    p.add_argument('--ablate-top',type=int,default=0)
    p.add_argument('--activation-top',type=int,default=24)
    p.add_argument('--threshold',type=float,default=2.0)
    p.add_argument('--batch-size',type=int,default=256)
    p.add_argument('--extra-discovery',type=int,default=0)
    p.add_argument('--extra-validation',type=int,default=0)
    p.add_argument('--max-centers',type=int,default=200)
    return p.parse_args()


if __name__=='__main__':
    run(parser())
