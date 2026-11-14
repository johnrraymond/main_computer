"""SQLite ledger of exact arithmetic trouble cases for NanoJev frontier repair.

Stores pairs, not external answer lookups or a substitute arithmetic engine.
All targets are verified as a+b. Scores are scheduling heuristics, not truth.
"""
from __future__ import annotations

import csv
import json
import random
import sqlite3
from pathlib import Path

SCHEMA = 'nanojev.emp-hard-case-bank.v1'


def card(a, b):
    a,b=int(a),int(b)
    return {'a':a,'b':b,'target':a+b}


def valid(row, radius, delta):
    a,b=int(row['a']),int(row['b'])
    return (a+b==int(row['target']) and a*b<=0 and
            max(abs(a),abs(b))<=radius and abs(a+b)<=delta)


class HardCaseBank:
    def __init__(self, path, *, source_sha256, max_delta):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.conn=sqlite3.connect(self.path)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA busy_timeout=10000')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS metadata
                             (key TEXT PRIMARY KEY, value TEXT NOT NULL)''')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS cases (
            a INTEGER NOT NULL, b INTEGER NOT NULL, target INTEGER NOT NULL,
            magnitude INTEGER NOT NULL, residual INTEGER NOT NULL,
            first_radius INTEGER NOT NULL, last_radius INTEGER NOT NULL,
            wrong_count INTEGER NOT NULL DEFAULT 0,
            regression_count INTEGER NOT NULL DEFAULT 0,
            neighbor_count INTEGER NOT NULL DEFAULT 0,
            correct_count INTEGER NOT NULL DEFAULT 0,
            last_prediction INTEGER, last_status TEXT NOT NULL,
            last_wave TEXT NOT NULL,
            PRIMARY KEY (a,b))''')
        self.conn.execute('CREATE INDEX IF NOT EXISTS cases_ranking ON cases(wrong_count,regression_count,neighbor_count)')
        identity={'schema':SCHEMA,'source_sha256':str(source_sha256),'max_delta':int(max_delta),
                  'domain':'signed_opposite_sign_zero_cancellation'}
        existing=dict(self.conn.execute('SELECT key,value FROM metadata'))
        if existing:
            if existing!= {k:json.dumps(v,sort_keys=True) for k,v in identity.items()}:
                self.conn.close()
                raise RuntimeError('HARD_BANK_SOURCE_OR_DOMAIN_MISMATCH')
        else:
            self.conn.executemany('INSERT INTO metadata VALUES(?,?)',
                                  [(k,json.dumps(v,sort_keys=True)) for k,v in identity.items()])
        self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self,*_):
        self.close()

    def known_keys(self, radius, delta):
        return {(a,b) for a,b in self.conn.execute('''SELECT a,b FROM cases
                   WHERE magnitude<=? AND abs(residual)<=?''',(int(radius),int(delta)))}

    def observe(self, rows, predictions, *, radius, delta, wave, reason='audit'):
        """Record errors and refresh previously tracked entries; skip ordinary correct cases."""
        if len(rows)!=len(predictions):
            raise ValueError('HARD_BANK_PREDICTION_LENGTH_DRIFT')
        known=self.known_keys(radius,delta)
        changes=[];bad=[]
        for r,p in zip(rows,predictions):
            if not valid(r,radius,delta):
                raise ValueError('HARD_BANK_INVALID_ARITHMETIC_CARD')
            a,b=int(r['a']),int(r['b']);target=a+b;p=int(p)
            wrong=p!=target
            if not wrong and (a,b) not in known:
                continue
            if wrong:bad.append(r)
            changes.append((a,b,target,max(abs(a),abs(b)),target,radius,radius,
                            int(wrong),int(not wrong),p,'wrong' if wrong else 'correct',str(wave)))
        with self.conn:
            self.conn.executemany('''INSERT INTO cases (
                a,b,target,magnitude,residual,first_radius,last_radius,
                wrong_count,correct_count,last_prediction,last_status,last_wave)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(a,b) DO UPDATE SET
                    last_radius=excluded.last_radius,
                    wrong_count=cases.wrong_count+excluded.wrong_count,
                    correct_count=cases.correct_count+excluded.correct_count,
                    last_prediction=excluded.last_prediction,
                    last_status=excluded.last_status,last_wave=excluded.last_wave''',changes)
        return {'observed':len(rows),'tracked_updated':len(changes),'wrong':len(bad),'new_or_repeated_failures':bad}

    def regressions(self, rows, *, radius, delta, wave):
        """Only callers comparing old/new correctness may classify regressions."""
        changes=[]
        for r in rows:
            if not valid(r,radius,delta):
                raise ValueError('HARD_BANK_INVALID_REGRESSION_CARD')
            a,b=int(r['a']),int(r['b']);target=a+b
            changes.append((a,b,target,max(abs(a),abs(b)),target,radius,radius,
                            1,'threatened',str(wave)))
        with self.conn:
            self.conn.executemany('''INSERT INTO cases (
                 a,b,target,magnitude,residual,first_radius,last_radius,
                 regression_count,last_status,last_wave)
                 VALUES(?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(a,b) DO UPDATE SET
                   last_radius=excluded.last_radius,
                   regression_count=cases.regression_count+1,
                   last_status=excluded.last_status,last_wave=excluded.last_wave''',changes)
        return len(changes)

    def neighbors(self, failures, *, radius, delta, wave, offsets=(-2,-1,1,2)):
        """Adjacent examples are candidates for rehearsal, not asserted errors."""
        changes={}
        failed={(int(r['a']),int(r['b'])) for r in failures}
        for r in failures:
            a,b=int(r['a']),int(r['b'])
            for v in offsets:
                for x,y in ((a+v,b),(a,b+v),(a+v,b-v),(b+v,a-v)):
                    c=card(x,y)
                    if (x,y) not in failed and valid(c,radius,delta):
                        changes[(x,y)]=(x,y,x+y,max(abs(x),abs(y)),x+y,
                                        radius,radius,1,'neighbor',str(wave))
        with self.conn:
            self.conn.executemany('''INSERT INTO cases (
                a,b,target,magnitude,residual,first_radius,last_radius,
                neighbor_count,last_status,last_wave)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(a,b) DO UPDATE SET
                  neighbor_count=cases.neighbor_count+1,
                  last_radius=excluded.last_radius,
                  last_wave=excluded.last_wave''',changes.values())
        return len(changes)

    def prioritized(self, radius, delta, limit, *, seed, wave):
        """Reproducible selection: mostly high-risk, partly seeded diversity."""
        if limit<=0:return []
        data=list(self.conn.execute('''SELECT a,b,wrong_count,regression_count,
               neighbor_count,correct_count,magnitude,residual FROM cases
               WHERE magnitude<=? AND abs(residual)<=?''',(radius,delta)))
        if not data:return []
        def score(r):
            return 120*r[3]+100*r[2]+8*r[4]+min(30,r[5])
        data.sort(key=lambda r:(-score(r),-r[6],r[7],r[0],r[1]))
        top_count=min(len(data), max(1,round(limit*.75)))
        selected=data[:top_count]
        selected_keys={(r[0],r[1]) for r in selected}
        rest=[r for r in data[top_count:] if (r[0],r[1]) not in selected_keys]
        rng=random.Random(f'hard-bank:{seed}:{wave}:{radius}:{delta}')
        if rest:
            selected+=rng.sample(rest,min(limit-len(selected),len(rest)))
        return [card(r[0],r[1]) for r in selected[:limit]]

    def export(self, output_dir):
        """Readable snapshots; SQLite remains the authoritative durable store."""
        root=Path(output_dir)
        root.mkdir(parents=True,exist_ok=True)
        fields=['a','b','target','magnitude','residual','first_radius','last_radius',
                'wrong_count','regression_count','neighbor_count','correct_count',
                'last_prediction','last_status','last_wave']
        rows=self.conn.execute('SELECT '+','.join(fields)+
            ' FROM cases ORDER BY (120*regression_count+100*wrong_count+8*neighbor_count) DESC, magnitude DESC,a,b')
        with (root/'hard_pairs.csv').open('w',newline='',encoding='utf-8') as fd:
            writer=csv.writer(fd);writer.writerow(fields);writer.writerows(rows)
        query=('SELECT operand,count(*) AS hard_pairs, '
               'sum(wrong_count),sum(regression_count),sum(neighbor_count) '
               'FROM (SELECT a AS operand,wrong_count,regression_count,neighbor_count FROM cases '
               'UNION ALL SELECT b AS operand,wrong_count,regression_count,neighbor_count FROM cases) '
               'GROUP BY operand ORDER BY (120*sum(regression_count)+ '
               '100*sum(wrong_count)+8*sum(neighbor_count)) DESC,hard_pairs DESC,operand')
        with (root/'hard_numbers.csv').open('w',newline='',encoding='utf-8') as fd:
            writer=csv.writer(fd)
            writer.writerow(['operand','hard_pair_occurrences','wrong_events',
                             'threatened_events','neighbor_events'])
            writer.writerows(self.conn.execute(query))
        return {'pairs_csv':str(root/'hard_pairs.csv'),
                'numbers_csv':str(root/'hard_numbers.csv')}

    def summary(self):
        n,w,r,c,near=self.conn.execute('''SELECT count(*),
           coalesce(sum(CASE WHEN wrong_count>0 THEN 1 ELSE 0 END),0),
           coalesce(sum(CASE WHEN regression_count>0 THEN 1 ELSE 0 END),0),
           coalesce(sum(CASE WHEN correct_count>0 THEN 1 ELSE 0 END),0),
           coalesce(sum(CASE WHEN neighbor_count>0 THEN 1 ELSE 0 END),0)
           FROM cases''').fetchone()
        return {'entries':n,'ever_wrong':w,'ever_threatened':r,
                'ever_verified_correct':c,'neighbor_candidates':near}
