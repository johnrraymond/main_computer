"""Read-only forensic analyzer of saved Hawk–Dove per-mask dev diagnoses.

Shows Y multiplicative compression, numerical-range failure concentrations,
mask-level transfer and matched-case changes. Does not run Torch or train.
Never uses the test split for tuning.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import statistics

from tools import nanojev_hawk_dove_million as hd


def analyze(report:dict,db:Path)->dict:
    if report.get('split')!='dev' or report.get('tier') not in hd.TIERS:
        raise ValueError('ONLY_HELDOUT_DEV_MASK_DIAGNOSES_SUPPORTED')
    cases=report.get('cases')
    if not isinstance(cases,list) or not cases:
        raise ValueError('DIAGNOSIS_CASES_REQUIRED_NOT_CONSOLE_SUMMARY')
    with closing(sqlite3.connect(f'file:{db.resolve().as_posix()}?mode=ro',uri=True)) as con:
        instance_map={int(i):(int(a),int(b)) for i,a,b in con.execute('SELECT instance_id,a,b FROM instances')}
    buckets={}
    def add(k,c):buckets.setdefault(k,[]).append(c)
    for case in cases:
        if 'pred_y' not in case:continue
        index=case['instance']
        if index not in instance_map:raise ValueError('DIAGNOSIS_INSTANCE_NOT_IN_TEACHER')
        a,b=instance_map[index]
        case={**case,'a':a,'b':b,'signed_y_error':case['pred_y']-case['y']}
        add('all',case)
        add(f'mask:{case["mask"]}',case)
        add('raw-only' if case['mask']==0 else 'annotated',case)
        add('b:0-249999' if b<250_000 else
            'b:250000-499999' if b<500_000 else
            'b:500000-749999' if b<750_000 else 'b:750000-1000000',case)
        if case['y']!=0:
            add('y:positive' if case['y']>0 else 'y:negative',case)
    result={}
    for bucket,rows in sorted(buckets.items()):
        n=len(rows)
        errors=[r['pred_y']-r['y'] for r in rows]
        rel=[r['relative_error_y'] for r in rows]
        xs=[r['y'] for r in rows];ys=[r['pred_y'] for r in rows]
        meanx=statistics.mean(xs);meany=statistics.mean(ys)
        den=sum((x-meanx)**2 for x in xs)
        slope=(sum((x-meanx)*(y-meany) for x,y in zip(xs,ys))/den if den else None)
        result[bucket]={'n':n,'y_5pct':sum(bool(r['y_pass']) for r in rows)/n,
            'joint_5pct':sum(bool(r['joint_pass']) for r in rows)/n,
            'mean_signed_y_error':statistics.mean(errors),
            'median_signed_y_error':statistics.median(errors),
            'mean_abs_relative_y_error':statistics.mean(rel),
            'predicted_y_vs_actual_slope':slope,
            'intercept':meany-slope*meanx if slope is not None else None,
            'predicted_y_min':min(ys),'predicted_y_max':max(ys),
            'actual_y_min':min(xs),'actual_y_max':max(xs)}
    return {'tier':report['tier'],'step':report.get('step'),'checkpoint':report.get('checkpoint'),
        'split':'dev','cases_analyzed':len(buckets.get('all',[])),
        'by_group':result}


def compare(before:dict,after:dict)->dict:
    if before.get('tier')!=after.get('tier') or before.get('split')!='dev' or after.get('split')!='dev':
        raise ValueError('COMPARISON_REQUIRES_SAME_TIER_DEV_REPORTS')
    a={c['id']:c for c in before['cases'] if 'pred_y' in c}
    b={c['id']:c for c in after['cases'] if 'pred_y' in c}
    if set(a)!=set(b):raise ValueError('DIAGNOSIS_CASE_SETS_DIFFER')
    changes=[]
    for ident in sorted(a):
        x=a[ident];y=b[ident]
        if (x['x'],x['y'])!=(y['x'],y['y']):raise ValueError('TARGET_DRIFT:'+ident)
        changes.append({'id':ident,'mask':x['mask'],
                        'before_y_error':x['relative_error_y'],
                        'after_y_error':y['relative_error_y'],
                        'improvement':x['relative_error_y']-y['relative_error_y'],
                        'before_joint_pass':x['joint_pass'],
                        'after_joint_pass':y['joint_pass']})
    groups={}
    for row in changes:
        groups.setdefault(str(row['mask']),[]).append(row)
    return {'cases_matched':len(changes),
            'by_mask':{mask:{'n':len(rows),
                'mean_y_relative_error_improvement':statistics.mean(r['improvement'] for r in rows),
                'new_joint_passes':sum(not r['before_joint_pass'] and r['after_joint_pass'] for r in rows),
                'lost_joint_passes':sum(r['before_joint_pass'] and not r['after_joint_pass'] for r in rows)}
                for mask,rows in sorted(groups.items(),key=lambda item:int(item[0]))},
            'biggest_y_improvements':sorted(changes,key=lambda row:-row['improvement'])[:10],
            'biggest_y_regressions':sorted(changes,key=lambda row:row['improvement'])[:10]}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',required=True,type=Path)
    p.add_argument('--teacher',type=Path,default=Path('runtime/hawk-dove-million/teacher-scientific-v2.sqlite3'))
    p.add_argument('--compare-to',type=Path,help='Older full diagnostic JSON of same dev tier')
    p.add_argument('--out',type=Path,help='Optional output JSON (never overwrites)')
    a=p.parse_args(argv)
    current=json.loads(a.report.read_text(encoding='utf8'))
    output=analyze(current,a.teacher)
    if a.compare_to:
        older=json.loads(a.compare_to.read_text(encoding='utf8'))
        output['comparison']=compare(older,current)
    if a.out:
        if a.out.exists():raise FileExistsError('PROBE_OUTPUT_EXISTS')
        a.out.parent.mkdir(parents=True,exist_ok=True)
        a.out.write_text(json.dumps(output,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf8')
    print(json.dumps(output,indent=2,sort_keys=True,allow_nan=False))

if __name__=='__main__':main()
