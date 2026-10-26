#!/usr/bin/env python3
"""Adjust experimental EMP loss weights/LRs at the next probe boundary.

Runs from a second terminal; atomic control edits and monotone revision numbers
preserve an auditable timeline in the training checkpoint.
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
from tools.nanojev_zero_line_ladder import atomic_json
from tools.nanojev_emp_population import HEADS, checked_controls


def update(root,head=None,changes=None,backbone_lr=None):
    root=Path(root)
    path=root/'controls.json'
    before=checked_controls(json.loads(path.read_text('utf-8')))
    changed=copy.deepcopy(before)
    if head is not None:
        if head not in HEADS:raise ValueError('EMP_UNKNOWN_HEAD')
        for key,value in (changes or {}).items():
            if key not in changed['heads'][head]:raise ValueError('EMP_UNKNOWN_CONTROL')
            changed['heads'][head][key]=float(value)
    elif changes:
        raise ValueError('EMP_HEAD_REQUIRED_FOR_CHANGE')
    if backbone_lr is not None:changed['backbone_lr']=float(backbone_lr)
    if changed==before:raise ValueError('EMP_NO_CHANGE')
    changed['revision']+=1
    checked_controls(changed)
    # This is a manual multi-process interface, not concurrent shared editing.
    # Fail if an operator changed the file since it was read.
    if json.loads(path.read_text('utf-8'))!=before:raise RuntimeError('EMP_CONTROLS_CHANGED_CONCURRENTLY')
    atomic_json(path,changed)
    return changed


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True)
    p.add_argument('--head',choices=HEADS)
    for field in ('lr','loss_weight','frontier_boost','zero_boost','repeat_boost'):
        p.add_argument('--'+field.replace('_','-'),type=float)
    p.add_argument('--backbone-lr',type=float)
    return p


if __name__=='__main__':
    a=parser().parse_args()
    changes={k:getattr(a,k) for k in ('lr','loss_weight','frontier_boost','zero_boost','repeat_boost')
             if getattr(a,k) is not None}
    cfg=update(a.output,a.head,changes,a.backbone_lr)
    print('EMP_TUNED='+json.dumps({'revision':cfg['revision'],'head':a.head,
                                  'changes':changes,'backbone_lr':a.backbone_lr},sort_keys=True))
