#!/usr/bin/env python3
"""Persistent, split-reserved, representation-aware offline NanoJev task bank v1.

All gold targets belong to a canonical problem group, never a rendering.
A group and all its orientations/representations cannot straddle splits.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

SCHEMA = 'nanojev.training-task-bank.v1'
SPLITS = ('train', 'dev', 'predev', 'test')


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


class TrainingTaskStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.execute('PRAGMA busy_timeout=30000')
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cohorts(
                cohort_id TEXT PRIMARY KEY, family TEXT NOT NULL, split TEXT NOT NULL
                CHECK(split IN ('train','dev','predev','test')), cycle INTEGER,
                source_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS task_groups(
                group_id TEXT PRIMARY KEY, cohort_id TEXT NOT NULL REFERENCES cohorts(cohort_id),
                family TEXT NOT NULL, task TEXT NOT NULL,
                split TEXT NOT NULL CHECK(split IN ('train','dev','predev','test')),
                source_fingerprint TEXT NOT NULL UNIQUE,
                oracle TEXT NOT NULL, label_status TEXT NOT NULL,
                target_json TEXT NOT NULL, provenance_json TEXT NOT NULL,
                train_count INTEGER NOT NULL DEFAULT 0 CHECK(train_count>=0),
                eval_count INTEGER NOT NULL DEFAULT 0 CHECK(eval_count>=0),
                CHECK ((split='train' AND eval_count=0) OR
                       (split<>'train' AND train_count=0)));
            CREATE TABLE IF NOT EXISTS presentations(
                presentation_id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL REFERENCES task_groups(group_id),
                variant TEXT NOT NULL, sample_json TEXT NOT NULL,
                sample_sha256 TEXT NOT NULL,
                UNIQUE(group_id,variant));
            CREATE INDEX IF NOT EXISTS idx_task_groups_selection
                ON task_groups(family,split,task,train_count,eval_count);
            CREATE INDEX IF NOT EXISTS idx_presentations_group
                ON presentations(group_id,variant);
        ''')
        existing = self.conn.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()
        if existing is None:
            self.conn.execute('INSERT INTO metadata VALUES (?,?)', ('schema',SCHEMA))
            self.conn.commit()
        elif existing[0] != SCHEMA:
            raise RuntimeError('TASK_BANK_SCHEMA_MISMATCH')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.conn.close()

    def add_cohort(self, cohort_id: str, *, family: str, split: str, cycle: int | None = None,
                   source: dict | None = None):
        if split not in SPLITS:
            raise ValueError('invalid split')
        self.conn.execute('INSERT INTO cohorts VALUES (?,?,?,?,?)',
                          (cohort_id,family,split,cycle,canonical(source or {})))

    def put(self, *, cohort_id: str, family: str, task: str,
            split: str, fingerprint: str, oracle: str, label_status: str,
            target: Any, provenance: dict, variants: dict[str, Any]) -> str:
        if split not in SPLITS or not variants or not fingerprint or not task:
            raise ValueError('TASK_BANK_INVALID_GROUP')
        if label_status not in ('verified','provisional','unlabeled'):
            raise ValueError('TASK_BANK_LABEL_STATUS_INVALID')
        if label_status == 'unlabeled' and target is not None:
            raise ValueError('TASK_BANK_UNLABELED_HAS_TARGET')
        cohort = self.conn.execute('SELECT split,family FROM cohorts WHERE cohort_id=?',
                                   (cohort_id,)).fetchone()
        if cohort is None or cohort['split'] != split or cohort['family'] != family:
            raise ValueError('TASK_BANK_COHORT_MISMATCH')
        group_id = sha(['group',family,fingerprint])
        self.conn.execute('''INSERT INTO task_groups
            (group_id,cohort_id,family,task,split,source_fingerprint,oracle,label_status,
             target_json,provenance_json) VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (group_id,cohort_id,family,task,split,fingerprint,oracle,label_status,
             canonical(target),canonical(provenance)))
        for variant, sample in sorted(variants.items()):
            if not variant:
                raise ValueError('empty variant')
            sample_text = canonical(sample)
            self.conn.execute('''INSERT INTO presentations VALUES (?,?,?,?,?)''',
                (sha(['presentation',group_id,variant]),group_id,variant,
                 sample_text,hashlib.sha256(sample_text.encode('utf-8')).hexdigest()))
        return group_id

    def claim(self, group_id: str, purpose: str) -> None:
        if purpose not in ('train','eval'):
            raise ValueError('TASK_BANK_INVALID_CLAIM')
        row = self.conn.execute('SELECT split,label_status FROM task_groups WHERE group_id=?',
                                (group_id,)).fetchone()
        if row is None:
            raise KeyError('TASK_BANK_GROUP_NOT_FOUND')
        if (purpose == 'train') != (row['split'] == 'train'):
            raise ValueError('TASK_BANK_SPLIT_RESERVE_VIOLATION')
        if purpose == 'train' and row['label_status'] == 'unlabeled':
            raise ValueError('TASK_BANK_UNLABELED_TRAIN_VIOLATION')
        col = 'train_count' if purpose == 'train' else 'eval_count'
        with self.conn:
            self.conn.execute(f'UPDATE task_groups SET {col}={col}+1 WHERE group_id=?', (group_id,))

    def iter_examples(self, split: str, variant: str, *, family: str | None = None):
        if split not in SPLITS:
            raise ValueError('invalid split')
        sql = '''SELECT g.group_id,g.family,g.task,g.split,g.label_status,g.oracle,
                        g.target_json,g.provenance_json,p.sample_json
                 FROM presentations p JOIN task_groups g USING(group_id)
                 WHERE g.split=? AND p.variant=?'''
        params: list[Any] = [split,variant]
        if family is not None:
            sql += ' AND g.family=?'
            params.append(family)
        sql += ' ORDER BY g.group_id'
        for row in self.conn.execute(sql,params):
            yield {'groupId':row['group_id'],'family':row['family'],
                   'task':row['task'],'split':row['split'],
                   'labelStatus':row['label_status'],'oracle':row['oracle'],
                   'target':json.loads(row['target_json']),
                   'provenance':json.loads(row['provenance_json']),
                   'example':json.loads(row['sample_json'])}

    def stats(self) -> dict:
        return {
            'groups':int(self.conn.execute('SELECT COUNT(*) FROM task_groups').fetchone()[0]),
            'presentations':int(self.conn.execute('SELECT COUNT(*) FROM presentations').fetchone()[0]),
            'by_family_split':[
                dict(row) for row in self.conn.execute('''SELECT family,split,COUNT(*) AS groups
                    FROM task_groups GROUP BY family,split ORDER BY family,split''')],
            'by_task':[dict(r) for r in self.conn.execute('''SELECT task,COUNT(*) AS groups
                         FROM task_groups GROUP BY task ORDER BY task''')],
            'by_variant':[dict(r) for r in self.conn.execute('''SELECT variant,COUNT(*) AS samples
                         FROM presentations GROUP BY variant ORDER BY variant''')],
        }

    def verify(self) -> dict:
        integrity = self.conn.execute('PRAGMA integrity_check').fetchone()[0]
        fk = self.conn.execute('PRAGMA foreign_key_check').fetchone()
        count_bad = self.conn.execute('''SELECT COUNT(*) FROM task_groups g
            JOIN cohorts c ON c.cohort_id=g.cohort_id
            WHERE c.split<>g.split OR c.family<>g.family''').fetchone()[0]
        leak = self.conn.execute('''SELECT COUNT(*) FROM task_groups WHERE
            (split='train' AND eval_count<>0) OR (split<>'train' AND train_count<>0)''').fetchone()[0]
        zero = self.conn.execute('''SELECT COUNT(*) FROM task_groups g WHERE NOT EXISTS
            (SELECT 1 FROM presentations p WHERE p.group_id=g.group_id)''').fetchone()[0]
        if integrity != 'ok' or fk or count_bad or leak or zero:
            raise RuntimeError(f'TASK_BANK_INTEGRITY_FAILURE: {integrity}, {fk}, {count_bad}, {leak}, {zero}')
        return {'db_integrity':True,'foreign_keys':True,'cohort_boundaries':True,
                'claim_boundaries':True,'presentations_present':True}
