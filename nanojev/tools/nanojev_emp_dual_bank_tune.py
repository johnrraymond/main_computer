#!/usr/bin/env python3
"""Atomic control-revision edits for the four fully independent EMP banks."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from tools.nanojev_zero_line_ladder import atomic_json
from tools.nanojev_emp_dual_bank import BANKS,checked_controls


FIELDS=('backbone_lr','readout_lr','train_every','frontier_boost','zero_boost','repeat_boost','right_answer_boost')


def update(root,bank,changes):
    path=Path(root)/'controls.json'
    before=checked_controls(json.loads(path.read_text('utf-8')))
    if bank not in BANKS:raise ValueError('EMP_DUAL_UNKNOWN_BANK')
    if not changes or any(name not in FIELDS for name in changes):raise ValueError('EMP_DUAL_UNKNOWN_FIELD_OR_NO_CHANGE')
    after=copy.deepcopy(before)
    for field,value in changes.items():
        if field=='train_every':
            if type(value) is not int:
                raise ValueError('EMP_DUAL_TRAIN_EVERY_MUST_BE_INT')
            after['banks'][bank][field]=value
        else:
            after['banks'][bank][field]=float(value)
    if after==before:raise ValueError('EMP_DUAL_NO_CHANGE')
    after['revision']+=1;checked_controls(after)
    if checked_controls(json.loads(path.read_text('utf-8')))!=before:
        raise RuntimeError('EMP_DUAL_CONTROLS_CONCURRENT_EDIT')
    atomic_json(path,after)
    return after


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True)
    p.add_argument('--bank',choices=BANKS,required=True)
    for name in FIELDS:
        p.add_argument('--'+name.replace('_','-'),type=int if name=='train_every' else float)
    return p


if __name__=='__main__':
    a=parser().parse_args()
    changes={name:getattr(a,name) for name in FIELDS if getattr(a,name) is not None}
    new=update(a.output,a.bank,changes)
    print('EMP_DUAL_TUNED='+json.dumps({'revision':new['revision'],'bank':a.bank,'changes':changes},sort_keys=True))
