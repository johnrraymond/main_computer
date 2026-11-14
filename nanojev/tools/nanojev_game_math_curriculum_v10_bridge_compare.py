#!/usr/bin/env python3
"""Compare exact original V10 held-out cases against the V10.1 grammar bridge.

By default verifies original case IDs, targets, and 81 lattice prompts. With a
checkpoint, runs one same-48 neural baseline on new anchors, all intermediate
bridge stages, and original lattice nodes of T00 and T02. It NEVER trains.

The old V10 teacher is read-only; its schema is checked independently because
V10.1's new runtime intentionally does not accept old teachers for training.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from tools import nanojev_game_math_curriculum_v10_runtime as rt
from tools import nanojev_game_math_curriculum_v10_probe as probe
from tools import nanojev_game_math_curriculum_v10_build as builder
from tools import nanojev_deterministic_brain_v10 as brain

OLD_SCHEMA = 'nanojev.game-math.cumulative-xy.teacher.v10'
DEFAULT_OLD = Path('runtime/game-math-curriculum-v10/teacher.sqlite3')


def old_selected(path: Path, split: str, limit: int) -> list[tuple]:
    """Reproduce V10's balance-by-(OP,game,objective) selection byte-for-byte."""
    with closing(sqlite3.connect(f'file:{path.resolve().as_posix()}?mode=ro',uri=True)) as db:
        manifest=dict(db.execute('SELECT key,value FROM manifest'))
        if manifest.get('schema') != OLD_SCHEMA:
            raise ValueError('WRONG_ORIGINAL_V10_TEACHER')
        rows=db.execute('''SELECT case_id,game_id,objective,answer_json,
                 snapshot_sha256,answer_sha256
                 FROM cases WHERE split=? ORDER BY case_id''',(split,)).fetchall()
        buckets=defaultdict(list)
        for cid,game,objective,answer,ssha,asha in rows:
            buckets[(json.loads(answer)['OP'],game,objective)].append((cid,ssha,asha))
        result=[]
        while len(result)<limit and buckets:
            for key in sorted(tuple(buckets)):
                if len(result)>=limit:break
                result.append(buckets[key].pop(0))
                if not buckets[key]:del buckets[key]
        return result


def verify_same_cases(old:Path,new:Path,split:str,limit:int)->dict:
    rows=rt.read_cases(new,split,limit=limit)
    oldrows=old_selected(old,split,limit)
    if [r['id'] for r in rows] != [cid for cid,*_ in oldrows]:
        raise AssertionError('BRIDGE_CHANGED_ORIGINAL_HELDOUT_48_CASE_SELECTION')
    with closing(sqlite3.connect(f'file:{old.resolve().as_posix()}?mode=ro',uri=True)) as old_db, \
         closing(sqlite3.connect(f'file:{new.resolve().as_posix()}?mode=ro',uri=True)) as new_db:
        old_manifest=dict(old_db.execute('SELECT key,value FROM manifest'))
        new_manifest=dict(new_db.execute('SELECT key,value FROM manifest'))
        for key in ('source_sha256','seed','variants','limit_per_split'):
            if old_manifest.get(key)!=new_manifest.get(key):
                raise AssertionError('BRIDGE_SOURCE_OR_BUILD_PROVENANCE_CHANGED:'+key)
        count=0
        for (cid,ssha,asha),row in zip(oldrows,rows):
            newcase=new_db.execute('SELECT snapshot_sha256,answer_sha256,native_prompts_json FROM cases WHERE case_id=?',(cid,)).fetchone()
            if newcase is None or (ssha,asha) != newcase[:2]:
                raise AssertionError('BRIDGE_CHANGED_ORIGINAL_NUMERICAL_TRUTH:'+cid)
            native=json.loads(newcase[2])
            for tier in brain.TIERS:
                if row['prompts'][f'{tier}/v9_anchor'] != native[tier]:
                    raise AssertionError('BRIDGE_ANCHOR_NOT_EXACT_LEGACY:'+tier)
                for node in brain.NODES:
                    stage=f'{tier}/{node}'
                    old_prompt=old_db.execute('SELECT prompt FROM presentations WHERE case_id=? AND stage_id=?',(cid,stage)).fetchone()
                    if old_prompt is None or row['prompts'][stage] != old_prompt[0]:
                        raise AssertionError('BRIDGE_ALTERED_ORIGINAL_PRESENTATION:'+stage)
            count+=1
    return {'status':'PASS','same_case_ids':True,'same_numerical_targets':True,
            'old_lattice_prompts_byte_identical':True,'legacy_anchors_exact':True,
            'cases':count,'split':split,'source_build_provenance_identical':True}


def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--old-teacher',type=Path,default=DEFAULT_OLD)
    p.add_argument('--teacher-db',type=Path,default=rt.DEFAULT_TEACHER)
    p.add_argument('--source-checkpoint',type=Path,default=rt.DEFAULT_SOURCE)
    p.add_argument('--checkpoint',type=Path,help='Optional V9 checkpoint; omit for no-GPU dataset check')
    p.add_argument('--old-report',type=Path,help='Optional original V10 baseline-probe.json')
    p.add_argument('--output',type=Path,default=rt.DEFAULT_OUTPUT/'baseline-bridge.json')
    p.add_argument('--limit',type=int,default=48)
    p.add_argument('--split',choices=('dev','test'),default='dev')
    p.add_argument('--tiers',choices=brain.TIERS,nargs='+',default=['T00','T02'])
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--batch-size',type=int,default=4)
    a=p.parse_args(argv)
    if a.limit<1:raise ValueError('INVALID_CASE_LIMIT')
    rt.teacher_preflight(a.teacher_db)
    check=verify_same_cases(a.old_teacher,a.teacher_db,a.split,a.limit)
    print('Same-case provenance:',json.dumps(check,sort_keys=True),flush=True)
    if a.checkpoint is None:
        print('Neural baseline NOT RUN (no --checkpoint specified)',flush=True)
        return 0
    args=probe.make_parser().parse_args([
        '--teacher-db',str(a.teacher_db),'--source-checkpoint',str(a.source_checkpoint),
        '--checkpoint',str(a.checkpoint),'--split',a.split,'--limit',str(a.limit),
        '--output',str(a.output),'--device',a.device,'--dtype',a.dtype,
        '--batch-size',str(a.batch_size),'--tiers',*a.tiers])
    result=probe.run(args)
    print(probe.format_console(result),flush=True)
    if a.old_report:
        report=json.loads(a.old_report.read_text(encoding='utf-8'))
        if report.get('teacher_sha256')!=builder.filehash(a.old_teacher):
            raise AssertionError('ORIGINAL_BASELINE_REPORT_TEACHER_HASH_MISMATCH')
        scores=report.get('scores',{})
        for stage in ('T00/full','T00/no_logbars','T02/raw'):
            if stage not in scores or stage not in result['scores']:continue
            before=scores[stage]['joint_xy']
            after=result['scores'][stage]['joint']
            print(f'CONTROL {stage}: original={before:.1%} rebuilt={after:.1%} '
                  f'delta_pp={(after-before)*100:+.1f}',flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
