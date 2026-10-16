#!/usr/bin/env python3
"""Evaluate SQL game-transform predictions; supports an oracle positive/negative smoke.

Real model predictions: one JSONL row per case and orientation with keys:
  case_id, orientation, operator, x, y,
  and for matrix: U_prime, pure_nash, equilibrium_preserved
  or scalar: value_after.

The oracle smoke explicitly synthesizes gold predictions and corrupts one row;
this is an evaluator test, NOT evidence that a model learned the tasks.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sqlite3
import tempfile


def predictions_from_db(db: Path, split: str) -> list[dict]:
    with sqlite3.connect(db) as cn:
        rows=cn.execute('''SELECT p.case_id,p.orientation,p.target_json
            FROM presentations p JOIN transformation_cases c USING(case_id)
            WHERE c.split=? ORDER BY p.case_id,p.orientation''',(split,)).fetchall()
    result=[]
    for case,orientation,raw in rows:
        target=json.loads(raw)
        row={'case_id':case,'orientation':orientation,'operator':target['operator'],
             'x':target['numeric_bridge']['x'],'y':target['numeric_bridge']['y']}
        if target['kind']=='matrix':
            row.update({k:target[k] for k in ('U_prime','pure_nash','equilibrium_preserved')})
        else:row['value_after']=target['value_after']
        result.append(row)
    return result


def _ok(actual,truth,tol):
    try:
        pred=float(actual)
        return math.isfinite(pred) and abs(pred-float(truth))<=tol*max(1.,abs(float(truth)))
    except (TypeError,ValueError,OverflowError):return False


def evaluate(db:Path,predictions:list[dict],split:str='dev') -> dict:
    if split not in ('train','dev','test'):raise ValueError('invalid split')
    seen={}
    for row in predictions:
        key=(row.get('case_id'),row.get('orientation'))
        if key in seen:raise ValueError('duplicate prediction: '+str(key))
        seen[key]=row
    per_case={}
    counts={'total':0,'provided':0,'operator_correct':0,'transformation_exact':0,
            'numeric_5pct':0,'numeric_10pct':0,'paired_numeric_5pct':0,
            'paired_numeric_10pct':0,'paired_transformation_exact':0}
    by_operator={};by_game={}
    with sqlite3.connect(db) as cn:
        rows=cn.execute('''SELECT p.case_id,p.orientation,p.target_json,c.op_id,i.game_id
            FROM presentations p JOIN transformation_cases c USING(case_id)
            JOIN instances i USING(instance_id) WHERE c.split=?
            ORDER BY p.case_id,p.orientation''',(split,)).fetchall()
    if not rows:raise ValueError('split empty')
    for case,orientation,text,op,game in rows:
        target=json.loads(text);actual=seen.get((case,orientation),{})
        counts['total']+=1
        counts['provided']+=bool(actual)
        correct_op=actual.get('operator')==op
        counts['operator_correct']+=correct_op
        if target['kind']=='matrix':
            structural=actual.get('U_prime')==target['U_prime'] and actual.get('pure_nash')==target['pure_nash'] \
                       and actual.get('equilibrium_preserved')==target['equilibrium_preserved']
        else:structural=_ok(actual.get('value_after'),target['value_after'],1e-7)
        exact=bool(structural and correct_op)
        counts['transformation_exact']+=exact
        x=target['numeric_bridge']['x'];y=target['numeric_bridge']['y']
        numeric5=_ok(actual.get('x'),x,.05) and _ok(actual.get('y'),y,.05)
        numeric10=_ok(actual.get('x'),x,.10) and _ok(actual.get('y'),y,.10)
        counts['numeric_5pct']+=numeric5
        counts['numeric_10pct']+=numeric10
        per_case.setdefault(case,{})[orientation]=(numeric5,numeric10,exact)
        for dest,key in ((by_operator,op),(by_game,game)):
            bucket=dest.setdefault(key,{'total':0,'exact':0,'numeric_5pct':0})
            bucket['total']+=1;bucket['exact']+=exact;bucket['numeric_5pct']+=numeric5
    for case,orientations in per_case.items():
        # Both natural-language orientations and the symbolic equivalent must
        # independently work; no averaging decoded numbers.
        if set(orientations)!= {'forward','reordered','symbolic'}:
            raise AssertionError('missing gold presentation for '+case)
        counts['paired_numeric_5pct']+=all(z[0] for z in orientations.values())
        counts['paired_numeric_10pct']+=all(z[1] for z in orientations.values())
        counts['paired_transformation_exact']+=all(z[2] for z in orientations.values())
    total=counts['total'];unique=len(per_case)
    return {'split':split,'presentations':total,'cases':unique,'provided':counts['provided'],
       'operator_accuracy':counts['operator_correct']/total,
       'transformation_exact':counts['transformation_exact']/total,
       'numeric_joint_5pct':counts['numeric_5pct']/total,
       'numeric_joint_10pct':counts['numeric_10pct']/total,
       'strict_paired_5pct':counts['paired_numeric_5pct']/unique,
       'strict_paired_10pct':counts['paired_numeric_10pct']/unique,
       'strict_paired_transformation_exact':counts['paired_transformation_exact']/unique,
       'by_operator':{name:{'n':d['total'],'exact':d['exact']/d['total'],'numeric_5pct':d['numeric_5pct']/d['total']}
                      for name,d in sorted(by_operator.items())},
       'by_game':{name:{'n':d['total'],'exact':d['exact']/d['total'],'numeric_5pct':d['numeric_5pct']/d['total']}
                      for name,d in sorted(by_game.items())}}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,default=Path('runtime/game-transformation-library/game_transformations.sqlite3'))
    p.add_argument('--split',choices=('train','dev','test'),default='dev')
    p.add_argument('--predictions',type=Path)
    p.add_argument('--smoke',action='store_true')
    args=p.parse_args(argv)
    if args.smoke:
        gold=predictions_from_db(args.database,args.split)
        good=evaluate(args.database,gold,args.split)
        if any(good[k]!=1. for k in ('operator_accuracy','transformation_exact','numeric_joint_5pct',
                                  'numeric_joint_10pct','strict_paired_5pct','strict_paired_10pct',
                                  'strict_paired_transformation_exact')):
            raise AssertionError('GOOD_GOLD_FAILED')
        bad=[dict(x) for x in gold]
        bad[0]['y']=float(bad[0]['y'])+100000.0
        bad[0]['operator']='wrong_operator'
        damaged=evaluate(args.database,bad,args.split)
        if not (damaged['numeric_joint_5pct']<1 and damaged['operator_accuracy']<1 and
                damaged['strict_paired_5pct']<1):
            raise AssertionError('NEGATIVE_CONTROL_FAILED')
        print(json.dumps({'event':'game_transformation_evaluator_smoke','status':'PASS',
                          'gold_strict_5pct':good['strict_paired_5pct'],
                          'corrupt_strict_5pct':damaged['strict_paired_5pct'],
                          'gold_transform_exact':good['transformation_exact'],
                          'corrupt_operator_accuracy':damaged['operator_accuracy'],
                          'split':args.split,'case_count':good['cases']},indent=2))
        return 0
    if not args.predictions:p.error('provide --predictions or --smoke')
    preds=[json.loads(line) for line in args.predictions.read_text(encoding='utf8').splitlines() if line.strip()]
    print(json.dumps(evaluate(args.database,preds,args.split),indent=2,sort_keys=True))
    return 0

if __name__=='__main__':raise SystemExit(main())
