#!/usr/bin/env python3
"""Source-only, full-family V10 smoke. No model, CUDA, or shipped database.

Creates a TEMPORARY verified native-V6 fixture drawn from the executable
source game catalog, builds V10 SQLite twice, checks identical bytes, reopens
read-only, and independently verifies every stored stage and XY path.

Run: python -m tools.nanojev_game_math_curriculum_v10_smoke
The resulting teacher is intentionally discarded; use v10_build against the
real native database to produce the production supervision data.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from . import nanojev_game_native_objectives_v6 as native
from . import nanojev_game_transformation_library as games
from . import nanojev_game_math_curriculum_v10_build as build


def emit_fixture(path: Path) -> dict:
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''CREATE TABLE native_v6_cases(
          case_id TEXT PRIMARY KEY, instance_id TEXT NOT NULL,
          split TEXT NOT NULL, game_id TEXT NOT NULL, objective TEXT NOT NULL,
          t08_sha256 TEXT NOT NULL, status TEXT NOT NULL);
          CREATE TABLE native_v6_tiers (
          case_id TEXT NOT NULL,tier INTEGER NOT NULL,prompt TEXT NOT NULL,
          PRIMARY KEY(case_id,tier));''')
        count = 0
        families = Counter()
        all_games = sorted(set(games.SCALAR_MODELS) | set(games.MATRICES))
        for index, gid in enumerate(all_games):
            split = ('train','dev','test')[index % 3]
            if gid in games.SCALAR_MODELS:
                title, _, _, params, *_ = games.SCALAR_MODELS[gid]
                facts = [native._facts('scalar',gid,title,
                        {'parameters':dict(params)},'scalar_value')]
            else:
                title, _, rows, cols, U = games.MATRICES[gid]
                src = {'row_actions':list(rows),'column_actions':list(cols),'U':U}
                facts = [native._facts('matrix',gid,title,src,'best_response',col)
                         for col in (0,1)]
                if native._mixed(U) is not None:
                    facts += [native._facts('matrix',gid,title,src,mode)
                              for mode in ('mixed_row','mixed_col')]
            for i, fact in enumerate(facts):
                prompt = native.render08(fact)
                # Full nine-tier native execution with independent oracle.
                native.walk(prompt)
                case_id = native.sha([gid, fact['mode'],fact.get('selector'),i])
                db.execute('INSERT INTO native_v6_cases VALUES (?,?,?,?,?,?,?)', (
                    case_id,gid,split,gid,fact['mode'],native.sha(prompt),'PASS'))
                db.execute('INSERT INTO native_v6_tiers VALUES (?,?,?)',(
                    case_id,8,prompt))
                count += 1
                families[gid] += 1
        if len(families) != 33:
            raise AssertionError('EXPECTED_33_EXECUTABLE_GAME_FAMILIES')
    return {'source_cases':count,'game_families':len(families)}


def main() -> int:
    with TemporaryDirectory(prefix='nanojev-v10-xy-smoke-') as tmp:
        root = Path(tmp)
        source, a, b = (root / name for name in
                        ('native.sqlite3','teacher-a.sqlite3','teacher-b.sqlite3'))
        report = emit_fixture(source)
        first = build.make_teacher(source,a,seed=20261017)
        second = build.make_teacher(source,b,seed=20261017)
        if first['teacher_sha256'] != second['teacher_sha256'] or a.read_bytes() != b.read_bytes():
            raise AssertionError('CLEAN_REBUILD_NOT_BYTE_IDENTICAL')
        verify = build.verify_teacher(a,oracle_recheck=True)
        if (verify['cases'] != report['source_cases'] or
            verify['presentations'] != verify['cases']*(9*9 + sum(len(n) for n in build.brain.BRIDGE_NODES.values())) or
            verify['equivalence_proofs'] != verify['cases']*9*5):
            raise AssertionError('INCOMPLETE_V10_TEACHER')
        print(json.dumps({'status':'PASS',**report,**verify,
                          'rebuild_byte_identical':True,
                          'scope':'synthetic_native_fixture_not_user_training_db'},
                         sort_keys=True),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
