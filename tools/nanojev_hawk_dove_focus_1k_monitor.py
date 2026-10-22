"""Read-only focused 1..1000 train/held-out learning-curve monitor.

Does not select checkpoints, mutate weights, inspect TEST, or infer learning
from overall signed-asinh error. Reports true 5% numerical accuracy and MAE
for 1..10, 10..100, 100..1000 separately, with real samples per band.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

BANDS = ('1-10','10-100','100-1000')


def review(root):
    root = Path(root)
    files = sorted(root.glob('probe-step-*.json'))
    if not files:
        return {'run_dir':str(root),'status':'NO_PROBES','test_split_read':False}
    records = []
    steps = set()
    for path in files:
        doc=json.loads(path.read_text(encoding='utf8'))
        step=doc.get('step')
        if not isinstance(step,int) or step in steps:
            raise ValueError('FOCUS_1K_MONITOR_DUPLICATE_OR_INVALID_STEP')
        steps.add(step)
        focused=doc.get('focus_1k')
        if not isinstance(focused,dict):
            raise ValueError('FOCUS_1K_MONITOR_WRONG_RUN_OR_OLD_PROBE:'+str(path))
        item={'step':step,'bands':{}, 'gradient_clip_fraction':None,
              'mean_gradient_norm':None,'numeric_loss':None,'decoder_loss':None}
        for name in BANDS:
            train=focused.get('train_by_band',{}).get(name)
            dev=focused.get('new_dev_by_band',{}).get(name)
            if not isinstance(train,dict) or not isinstance(dev,dict) or \
                not all(isinstance(x,dict) and x.get('n',0)>0 for x in (train,dev)):
                raise ValueError('FOCUS_1K_MONITOR_MISSING_BAND_EVIDENCE:'+name)
            item['bands'][name]={
                'train_n':train['n'],'dev_n':dev['n'],
                'train_accuracy_5pct':train['accuracy_5pct_floor'],
                'dev_accuracy_5pct':dev['accuracy_5pct_floor'],
                'train_mae':train['mae'],'dev_mae':dev['mae'],
                'dev_frozen_accuracy_5pct':dev.get('frozen_accuracy_5pct_floor'),
                'dev_zero_accuracy_5pct':dev.get('zero_predictor_accuracy_5pct_floor')}
        g=doc.get('chunk_mean_train_losses_and_gradients') or {}
        item.update(gradient_clip_fraction=g.get('gradient_clip_fraction'),
                    mean_gradient_norm=g.get('gradient_global_norm_before_clip'),
                    numeric_loss=g.get('numeric_loss'),decoder_loss=g.get('decoder_loss'))
        records.append(item)
    records.sort(key=lambda row:row['step'])
    first,last=records[0],records[-1]
    results={}
    for name in BANDS:
        start=first['bands'][name];end=last['bands'][name]
        all_mae=[(r['step'],r['bands'][name]['dev_mae']) for r in records]
        best_step,best_mae=min(all_mae,key=lambda p:p[1])
        train_gain=start['train_mae']-end['train_mae']
        dev_gain=start['dev_mae']-end['dev_mae']
        acc_gain=end['dev_accuracy_5pct']-start['dev_accuracy_5pct']
        if end['train_mae'] is None or end['dev_mae'] is None or not all(math.isfinite(x) for x in (train_gain,dev_gain,acc_gain)):
            label='INVALID_METRICS'
        elif train_gain>0 and dev_gain>0 and acc_gain>0:
            label='PRECISION_AND_GENERALIZATION_IMPROVING'
        elif train_gain>0 and dev_gain>0:
            label='ERROR_REDUCING_PRECISION_NOT_YET_IMPROVING'
        elif train_gain>0 and dev_gain<=0:
            label='TRAIN_ONLY_FIT_NO_HELD_OUT_ERROR_GAIN'
        else:
            label='NO_CLEAR_TRAINING_PROGRESS'
        results[name]={**end,'train_mae_reduction':train_gain,
                       'held_out_mae_reduction':dev_gain,
                       'held_out_accuracy_gain_points':100*acc_gain,
                       'best_held_out_mae':best_mae,'best_held_out_step':best_step,
                       'assessment':label}
    clip=last.get('gradient_clip_fraction')
    return {'schema':'nanojev.hawk-dove.focus-1k-monitor.v1',
            'run_dir':str(root),'status':'OK',
            'step':last['step'],'first_step':first['step'],
            'probe_count':len(records),'bands':results,
            'persistent_clipping_warning':clip is not None and clip>.95,
            'latest_gradient_clip_fraction':clip,
            'latest_numeric_loss':last.get('numeric_loss'),
            'latest_decoder_loss':last.get('decoder_loss'),
            'history':records,'read_only':True,'test_split_read':False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',required=True,type=Path)
    a=p.parse_args(argv)
    print(json.dumps(review(a.run_dir),indent=2,sort_keys=True,allow_nan=False))

if __name__=='__main__':
    main()
