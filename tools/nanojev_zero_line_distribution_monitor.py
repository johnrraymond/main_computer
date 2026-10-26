#!/usr/bin/env python3
"""Non-invasive, checkpoint-safe distribution monitor for the NanoJev outward/return ladder.

Run this module *instead of* tools.nanojev_zero_line_outward_return with identical
trainer arguments, plus optional --monitor-* arguments.  It monkey-patches only
an in-process checkpoint snapshot hook; the trainer source, configuration hash,
training weights, optimizer, sampler and acceptance gates remain untouched.

Every recorded probe uses deterministic, frozen DEV operand pairs.  Gold arithmetic
is used ONLY to evaluate predicted outputs.  Test magnitudes are never queried.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import torch

from tools import nanojev_zero_line_outward_return as trainer
from tools import nanojev_zero_line_sampled_radius_ladder as old
from tools.nanojev_zero_line_ladder import atomic_json, prediction_values
from tools.nanojev_zero_line_zero_only import BANDS, input_tensor

SCHEMA = 'nanojev.zero-line-distribution-monitor.v1'
HISTORY_FILE = 'distribution_monitor_v1.json'
MONITOR_CONFIG = 'distribution_monitor_v1_config.json'


def percentile(values, fraction):
    """Linear interpolated percentile; deterministic and dependency-free."""
    if not values:
        return None
    xs = sorted(float(v) for v in values)
    h = (len(xs) - 1) * fraction
    i = int(h)
    return xs[i] * (1 - (h - i)) + xs[min(i + 1, len(xs) - 1)] * (h - i)


def summary_for(rows, predictions):
    """Distribution over *problems*, not probabilistic uncertainty per answer.

    q = predicted / true for nonzero residuals.  q<=0 crosses/touches zero.
    Numbers are decoded from the network; labels are never network inputs.
    """
    if len(rows) != len(predictions):
        raise ValueError('ZERO_DIST_CARD_PREDICTION_MISMATCH')
    q=[]; errors=[]; sign_wrong=0; zero_count=zero_errors=0
    for card, prediction in zip(rows, predictions):
        delta=int(card[2]); value=int(prediction)
        if delta==0:
            zero_count+=1; zero_errors+=int(value!=0)
            continue
        ratio=value/delta
        q.append(ratio)
        errors.append(abs(ratio-1))
        sign_wrong+=int(ratio<=0)
    if not q:
        return {'cards':len(rows),'nonzero':0,'zero_cards':zero_count,'zero_errors':zero_errors}
    lo=percentile(q,.05);hi=percentile(q,.95)
    return {'cards':len(rows),'nonzero':len(q),'zero_cards':zero_count,'zero_errors':zero_errors,
        'q05':lo,'q25':percentile(q,.25),'median_q':percentile(q,.5),
        'q75':percentile(q,.75),'q95':hi,'q05_q95_width':hi-lo,
        'median_abs_relative_error':percentile(errors,.5),
        'p90_abs_relative_error':percentile(errors,.9),
        'p95_abs_relative_error':percentile(errors,.95),
        'mean_abs_relative_error':sum(errors)/len(errors),
        'tipping_violations':sign_wrong,'tipping_violation_rate':sign_wrong/len(q),
        'within_1pct':sum(x<=.01+1e-12 for x in errors)/len(q),
        'within_5pct':sum(x<=.05+1e-12 for x in errors)/len(q),
        'within_10pct':sum(x<=.10+1e-12 for x in errors)/len(q),
        'within_20pct':sum(x<=.20+1e-12 for x in errors)/len(q)}


def cancellation_bucket(card):
    a,b,d,*_=card
    ratio=abs(int(d))/max(1,abs(int(a))+abs(int(b)))
    if ratio<=.001: return 'extreme_le_0.1pct'
    if ratio<=.01: return 'deep_le_1pct'
    if ratio<=.1: return 'partial_le_10pct'
    return 'low_cancellation_gt_10pct'


def grouped(rows,predictions):
    bins={'positive':{},'negative':{},'magnitude_band':{},'cancellation_depth':{},'residual_shell':{}}
    for card,pred in zip(rows,predictions):
        d=int(card[2]);band=str(int(card[3]));m=abs(d)
        if d==0:continue
        keys={'positive':'positive' if d>0 else 'negative',
              'magnitude_band':band,
              'cancellation_depth':cancellation_bucket(card),
              'residual_shell':str(2**max(0,(m-1).bit_length()))}
        for k,value in keys.items():
            bins[k].setdefault(value,([],[]))
            bins[k][value][0].append(card);bins[k][value][1].append(pred)
    # Each group contains denominators and support counts to avoid tiny-group claims.
    return {k:{label:summary_for(cards,preds) for label,(cards,preds) in sorted(groups.items())}
            for k,groups in bins.items()}


def hash_cards(cards):
    h=hashlib.sha256()
    for a,b,d,band,m in cards:
        h.update(f'{a},{b},{d},{band},{m}\n'.encode('ascii'))
    return h.hexdigest()


def fixed_cards(radius, phase, stage, seed, view, frontier_count, breadth_outer):
    """Independent local-RNG samples.  DEV magnitude folds only; no test leakage."""
    radius=max(64,int(radius))
    high=trainer.frontier_cards(radius,'dev',seed,view,frontier_count)
    broader=old.evaluation_cards(radius,'dev',seed,stage,1,view+1,breadth_outer)[view]
    # Sort and de-duplicate for a fixed fixture; keep original labels for audit.
    breadth=sorted({(r[0],r[1]):r for r in broader}.values())
    for card in [*high,*breadth]:
        a,b,d,band,m=card
        if not (abs(a)<=old.MAX_ABS and abs(b)<=old.MAX_ABS and a+b==d and
                old.stable_fold(m,seed)=='dev'):
            raise RuntimeError('ZERO_DIST_NON_DEV_OR_INVALID_CARD')
    return {'frontier':high, 'breadth':breadth}


@torch.inference_mode()
def infer(model, cards, device, batch_size):
    result=[]
    was_training=model.training
    try:
        model.eval()
        for start in range(0,len(cards),batch_size):
            # Input function drops every field except operands a,b.
            output=model(input_tensor(cards[start:start+batch_size],device))
            values,_,_=prediction_values(output)
            result.extend(int(v) for v in values.detach().cpu().tolist())
    finally:
        model.train(was_training)
    return result


def evaluate(model, fixtures, device, batch):
    out={}
    for view,types in fixtures.items():
        out[view]={}
        for name,rows in types.items():
            pred=infer(model,rows,device,batch)
            out[view][name]={'summary':summary_for(rows,pred),'by_group':grouped(rows,pred)}
    return out


def convergence(current, baseline, recent):
    """Descriptive only, never a training/selection gate.

    Guard against false 'convergence' when distribution becomes narrow but biased.
    Interdecile width may increase even as tail error shrinks; report both.
    """
    width=current['q05_q95_width'];bw=baseline['q05_q95_width']
    tail=current['p90_abs_relative_error'];bt=baseline['p90_abs_relative_error']
    center=abs(current['median_q']-1)
    if len(recent)<3:
        status='insufficient_probes'
    else:
        widths=[x['q05_q95_width'] for x in recent[-3:]]
        centers=[x['median_q'] for x in recent[-3:]]
        steady=max(widths)-min(widths)<=.025 and max(centers)-min(centers)<=.025
        if steady and center<=.10 and current['tipping_violation_rate']<=.01:
            status='stabilizing_near_target'
        elif steady:
            status='plateau_or_biased'
        elif width<bw and tail<bt:
            status='contracting'
        else:status='mixed_or_expanding'
    return {'status':status,'baseline_width':bw,'current_width':width,
        'width_change':width-bw,'baseline_p90_error':bt,'current_p90_error':tail,
        'p90_error_change':tail-bt,'center_distance':center,
        'tip_violation_change':current['tipping_violation_rate']-baseline['tipping_violation_rate']}


def export_stage_visuals(root, history):
    """Dependency-free, sidecar-only SVG + CSV showing fixed-card contraction.

    Shows q05/q95 bands, median, the target q=1 and tipping line q=0.
    This is a population of model predictions, NOT predicted per-card uncertainty.
    """
    views=sorted(history['baseline'])
    probes=history['probes']
    def summary(view, point):
        return point['results'][view]['frontier']['summary']
    columns=['step','view','q05','median_q','q95','q05_q95_width',
        'p90_abs_relative_error','tipping_violation_rate','within_1pct',
        'within_5pct','within_10pct','within_20pct']
    lines=[','.join(columns)]
    for i,entry in enumerate([{'step':0,'results':history['baseline']},*probes]):
        for v in views:
            data=summary(v,entry)
            lines.append(','.join([str(entry['step']),v]+[str(data[k]) for k in columns[2:]]))
    _atomic_text(root/'distribution_frontier_v1.csv','\n'.join(lines)+'\n')
    if not probes:
        return
    full=[{'step':0,'results':history['baseline']},*probes]
    qlist=[summary(v,x)[k] for v in views for x in full for k in ('q05','q95')]
    # In the initial regime the q-tail can be arbitrarily large. Clip the
    # DISPLAY range, not the saved raw numbers, and explicitly label clipping.
    ymin=max(-5.,min(-.12,min(qlist)-.08))
    ymax=min(5.,max(1.15,max(qlist)+.08))
    if ymax<=ymin:ymax=ymin+1
    left,right,top,bottom=72.,910.,40.,320.
    maxstep=max(1,probes[-1]['step'])
    def x_coord(step):return left+(right-left)*step/maxstep
    def y_coord(q):return top+(bottom-top)*(ymax-max(ymin,min(ymax,q)))/(ymax-ymin)
    def points(view,key):return ' '.join(f'{x_coord(row["step"]):.2f},{y_coord(summary(view,row)[key]):.2f}' for row in full)
    colors=['#2563eb','#d97706','#059669','#7c3aed']
    parts=['<svg xmlns="http://www.w3.org/2000/svg" width="980" height="390" viewBox="0 0 980 390">',
        '<rect width="980" height="390" fill="white"/>',
        '<text x="72" y="22" font-family="Arial,sans-serif" font-size="17" fill="#111827">NanoJev fixed-frontier prediction ratio: 5th–95th percentile</text>',
        '<text x="72" y="38" font-family="Arial,sans-serif" font-size="10" fill="#4b5563">q = predicted / true residual; bands reflect errors across fixed DEV problems</text>']
    for target,label in [(0,'tipping point q=0'),(1,'target q=1')]:
        if ymin<=target<=ymax:
            yy=y_coord(target)
            parts.append(f'<line x1="{left}" y1="{yy:.2f}" x2="{right}" y2="{yy:.2f}" stroke="{("#dc2626" if target==0 else "#16a34a")}" stroke-dasharray="5 5" stroke-width="1.5"/>')
            parts.append(f'<text x="{right+4}" y="{yy+3:.2f}" font-family="Arial,sans-serif" font-size="10" fill="#334155">{label}</text>')
    parts.append(f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="#334155"/>')
    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="#334155"/>')
    for v,color in zip(views,colors):
        upper=points(v,'q95').split(' ')
        lower=points(v,'q05').split(' ')
        polygon=' '.join(upper+list(reversed(lower)))
        parts.append(f'<polygon points="{polygon}" fill="{color}" fill-opacity=".12" stroke="none"/>')
        for key in ('q05','q95'):
            parts.append(f'<polyline points="{points(v,key)}" fill="none" stroke="{color}" stroke-opacity=".32" stroke-width="1"/>')
        parts.append(f'<polyline points="{points(v,"median_q")}" fill="none" stroke="{color}" stroke-width="2"/>')
    for k in range(6):
        val=ymin+(ymax-ymin)*k/5;yy=y_coord(val)
        parts.append(f'<text x="65" y="{yy+3:.2f}" text-anchor="end" font-family="Arial,sans-serif" font-size="10" fill="#334155">{val:.2f}</text>')
    for k in range(6):
        step=round(maxstep*k/5);xx=x_coord(step)
        parts.append(f'<text x="{xx:.1f}" y="{bottom+17:.1f}" text-anchor="middle" font-family="Arial,sans-serif" font-size="10" fill="#334155">{step}</text>')
    parts.append('<text x="458" y="365" font-family="Arial,sans-serif" font-size="12" text-anchor="middle" fill="#334155">Training steps (0 = stage-parent baseline)</text>')
    parts.append(f'<text x="72" y="383" font-family="Arial,sans-serif" font-size="10" fill="#64748b">Plot clipped to [{ymin:.2f}, {ymax:.2f}]; raw quantiles preserved in CSV/JSON. No oracle/routing.</text>')
    for i,(v,color) in enumerate(zip(views,colors)):
        parts.append(f'<text x="{right-70-i*95}" y="27" font-family="Arial,sans-serif" font-size="12" fill="{color}">DEV {v}</text>')
    parts.append('</svg>')
    _atomic_text(root/'distribution_frontier_v1.svg','\n'.join(parts)+'\n')


def _atomic_text(path, value):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(value,encoding='utf-8')
    os.replace(temp,path)


class Observer:
    def __init__(self,args,*,frontier_count=96,breadth_outer=12,eval_batch=256):
        self.args=args;self.frontier_count=frontier_count
        self.breadth_outer=breadth_outer;self.eval_batch=eval_batch
        self.parents={};self.fixtures={}
        self.root=Path(args.output).resolve()
        self.config={'schema':SCHEMA,'seed':args.seed,'frontier_count':frontier_count,
            'breadth_outer':breadth_outer,'eval_batch':eval_batch,
            'algorithm':'prediction/true; DEV fixed by seed, stage, and view; no TEST',
            'trainer_code_hash_preserved':True}

    def setup(self):
        path=self.root/MONITOR_CONFIG
        if path.is_file():
            if json.loads(path.read_text('utf-8')) !=self.config:
                raise RuntimeError('ZERO_DIST_MONITOR_CONFIG_DRIFT')
        else:
            if not (self.root/'config.json').is_file():
                # For newly created campaigns wait for trainer to create root.
                return
            atomic_json(path,self.config)

    def on_model(self,args,state):
        model=self.old_model(args,state)
        # Keep a CPU copy only for the next stage baseline, never alter source.
        self.parents['pending']={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
        return model

    def _fixtures(self,phase,stage):
        key=(phase,stage)
        if key not in self.fixtures:
            radius=stage if phase=='outward' else max(64,stage)
            self.fixtures[key]={str(v):fixed_cards(radius,phase,stage,self.args.seed,v,
                self.frontier_count,self.breadth_outer) for v in range(self.args.dev_views)}
        return self.fixtures[key]

    def on_snapshot(self,model,opt,rng,step,weights,cursor,qualifying,phase,stage):
        # Snapshot is called once after each original trainer probe, BEFORE the
        # original atomic checkpoint. Never consume model optimizer/random state.
        self.setup()
        root=self.root/'stages'/f'{phase}_{stage}'
        path=root/HISTORY_FILE
        fixture=self._fixtures(phase,stage)
        card_hashes={view:{name:hash_cards(cards) for name,cards in groups.items()}
            for view,groups in fixture.items()}
        if path.is_file():
            history=json.loads(path.read_text('utf-8'))
            if history.get('card_hashes')!=card_hashes or history.get('schema')!=SCHEMA:
                raise RuntimeError('ZERO_DIST_FIXTURE_DRIFT')
        else:
            parent=self.parents.get('pending')
            if parent is None:raise RuntimeError('ZERO_DIST_MISSING_PARENT_BASELINE')
            start=copy.deepcopy(model)
            start.load_state_dict(parent,strict=True)
            baseline=evaluate(start,fixture,self.args.device,self.eval_batch)
            del start
            history={'schema':SCHEMA,'phase':phase,'radius':stage,'card_hashes':card_hashes,
                'population':'Fixed DEV examples; previously investigated magnitude folds; not independent certification',
                'baseline':baseline,'probes':[]}
        probes=history['probes']
        if probes and step<probes[-1]['step']:
            # Trainer can resume from a checkpoint older than a written monitor
            # update if interruption happened between sidecar and checkpoint.
            probes[:]=[v for v in probes if v['step']<step]
        if probes and step==probes[-1]['step']:
            # Recover from interruption after writing monitor but before original
            # latest.pt; checkpoint is authoritative for actual training replay.
            probes.pop()
        if probes and step<=probes[-1]['step']:
            raise RuntimeError('ZERO_DIST_NON_MONOTONIC_PROBES')
        current=evaluate(model,fixture,self.args.device,self.eval_batch)
        compact={}
        for view in current:
            curr=current[view]['frontier']['summary']
            base=history['baseline'][view]['frontier']['summary']
            latest=[p['results'][view]['frontier']['summary'] for p in probes[-2:]]+[curr]
            compact[view]={'q05':round(curr['q05'],4),'median_q':round(curr['median_q'],4),
                'q95':round(curr['q95'],4),'p90_abs_error':round(curr['p90_abs_relative_error'],4),
                'tip_cross_rate':round(curr['tipping_violation_rate'],4),
                'within_5pct':round(curr['within_5pct'],4),
                'trend':convergence(curr,base,latest)['status']}
        probes.append({'step':step,'results':current,'summary':compact})
        atomic_json(path,history)
        export_stage_visuals(root,history)
        print('ZERO_OR_DISTRIBUTION='+json.dumps({'phase':phase,'radius':stage,
            'step':step,'views':compact,'history':str(path)},sort_keys=True),flush=True)
        return self.old_snapshot(model,opt,rng,step,weights,cursor,qualifying,phase,stage)


def monitor_main(argv=None):
    p=argparse.ArgumentParser(add_help=False)
    p.add_argument('--monitor-frontier-count',type=int,default=96)
    p.add_argument('--monitor-breadth-outer',type=int,default=12)
    p.add_argument('--monitor-eval-batch',type=int,default=256)
    monitor_args,remaining=p.parse_known_args(argv)
    if (monitor_args.monitor_frontier_count<8 or monitor_args.monitor_breadth_outer<1
        or monitor_args.monitor_eval_batch<1):
        raise ValueError('ZERO_DIST_BAD_MONITOR_ARGS')
    args=trainer.parser().parse_args(remaining)
    if args.phase=='plan':return trainer.run(args)
    obs=Observer(args,frontier_count=monitor_args.monitor_frontier_count,
        breadth_outer=monitor_args.monitor_breadth_outer,eval_batch=monitor_args.monitor_eval_batch)
    obs.setup()
    orig_snapshot=trainer.snapshot;orig_model=trainer.frozen_model
    obs.old_snapshot=orig_snapshot;obs.old_model=orig_model
    trainer.snapshot=obs.on_snapshot;trainer.frozen_model=obs.on_model
    try:return trainer.run(args)
    finally:
        trainer.snapshot=orig_snapshot;trainer.frozen_model=orig_model


def main(argv=None):return monitor_main(argv)


if __name__=='__main__':main()
