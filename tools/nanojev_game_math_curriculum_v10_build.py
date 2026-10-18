#!/usr/bin/env python3
"""Build and verify a V10 immutable SQLite teacher with per-tier XY lattices.

Only deterministic brain and database; NO trainer, probe, GPU or model import.

From the repository root:
  python -m tools.nanojev_game_math_curriculum_v10_build build \
      --native-db runtime/game-math-source-smoke-v8/game_transformations.sqlite3 \
      --output runtime/game-math-curriculum-v10/teacher.sqlite3
  python -m tools.nanojev_game_math_curriculum_v10_build verify \
      --teacher-db runtime/game-math-curriculum-v10/teacher.sqlite3

A source DB needs native_v6_cases + native_v6_tiers created by V6/V8.
The legacy transformation-experiment DB is NOT the native source DB.
No generator artifacts are shipped with the source patch.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from . import nanojev_deterministic_brain_v10 as brain
from . import nanojev_deterministic_brain_v8 as v8
from . import nanojev_game_native_objectives_v6 as native

SCHEMA = 'nanojev.game-math.cumulative-xy-derivation-audited.teacher.v10.2'
# Keep old case identities and mutation seeds so the selected 48 cases and
# frozen numerical targets are IDENTICAL to the existing V10 baseline.
CASE_NAMESPACE = 'nanojev.game-math.cumulative-xy.teacher.v10'
SPLITS = ('train', 'dev', 'test', 'game_transfer')
NATIVE_SOURCE_EXAMPLE = Path('runtime/game-math-source-smoke-v8/game_transformations.sqlite3')
NATIVE_REQUIRED_COLUMNS = {
    'native_v6_cases': frozenset({'case_id','instance_id','split','game_id',
                                  'objective','t08_sha256','status'}),
    'native_v6_tiers': frozenset({'case_id','tier','prompt'}),
}


def validate_native_source(db: sqlite3.Connection, path: Path) -> dict:
    # Reject the legacy transform DB before running SQL against native tables.
    names = {name for (name,) in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = sorted(set(NATIVE_REQUIRED_COLUMNS) - names)
    if missing:
        raise ValueError(
            f'V10_NATIVE_SOURCE_SCHEMA_MISSING: {path}; missing tables: '
            f'{", ".join(missing)}. Use the verified V8 native source, for example '
            f'--native-db {NATIVE_SOURCE_EXAMPLE}. The legacy '
            'runtime/game-transformation-experiment/game_transformations.sqlite3 '
            'is not a native_v6 source.')
    for name, required in NATIVE_REQUIRED_COLUMNS.items():
        columns = {row[1] for row in db.execute(f'PRAGMA table_info({name})')}
        absent = sorted(required - columns)
        if absent:
            raise ValueError('V10_NATIVE_SOURCE_COLUMNS_MISSING: '
                             + name + ': ' + ', '.join(absent))
    eligible = db.execute('''SELECT COUNT(*) FROM native_v6_cases c
        JOIN native_v6_tiers t ON c.case_id=t.case_id
        WHERE c.status='PASS' AND t.tier=8''').fetchone()[0]
    if eligible == 0:
        raise ValueError('V10_NATIVE_SOURCE_NO_PASSING_T08_CASES: ' + str(path))
    return {'status':'PASS','native_db':str(path),
            'eligible_native_t08_cases':eligible}


def source_info(native_db: Path) -> dict:
    # Lightweight read-only preflight, separate from teacher construction.
    source=Path(native_db).resolve()
    if not source.is_file():
        raise FileNotFoundError('V10_NATIVE_SOURCE_NOT_FOUND: ' + str(source))
    with closing(sqlite3.connect(f'file:{source.as_posix()}?mode=ro',uri=True)) as db:
        result=validate_native_source(db,source)
        result['splits']={split:count for split,count in db.execute('''
            SELECT c.split,COUNT(*) FROM native_v6_cases c
            JOIN native_v6_tiers t ON c.case_id=t.case_id
            WHERE c.status='PASS' AND t.tier=8 GROUP BY c.split''')}
        return result


def seed_for(*parts: Any) -> int:
    return int(brain.digest([CASE_NAMESPACE, *parts])[:15], 16)


def filehash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


DDL = '''
PRAGMA foreign_keys=ON;
CREATE TABLE manifest (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE mutation_classes (
 class_id TEXT PRIMARY KEY, dimension TEXT NOT NULL,
 side TEXT NOT NULL, invariant TEXT NOT NULL);
CREATE TABLE stages (
 stage_id TEXT PRIMARY KEY, math_tier TEXT NOT NULL,
 assistance_node TEXT NOT NULL, mutation_set_json TEXT NOT NULL,
 UNIQUE(math_tier, assistance_node));
CREATE TABLE stage_edges (
 parent_id TEXT NOT NULL REFERENCES stages(stage_id),
 child_id TEXT NOT NULL REFERENCES stages(stage_id),
 mutation TEXT NOT NULL REFERENCES mutation_classes(class_id),
 edge_kind TEXT NOT NULL,
 PRIMARY KEY(parent_id,child_id));
CREATE TABLE cases (
 case_id TEXT PRIMARY KEY, source_case_id TEXT NOT NULL,
 source_instance_id TEXT NOT NULL, split TEXT NOT NULL,
 game_id TEXT NOT NULL, objective TEXT NOT NULL,
 variant INTEGER NOT NULL, input_sha256 TEXT NOT NULL,
 mutation_seed INTEGER NOT NULL, order_seed INTEGER NOT NULL,
 snapshot_json TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL,
 answer_json TEXT NOT NULL, answer_sha256 TEXT NOT NULL,
 native_prompts_json TEXT NOT NULL, source_proof_json TEXT NOT NULL,
 source_proof_sha256 TEXT NOT NULL,
 descent_proofs_json TEXT NOT NULL,
 oracle_verified INTEGER NOT NULL CHECK(oracle_verified=1),
 UNIQUE(source_case_id,variant));
CREATE TABLE presentations (
 case_id TEXT NOT NULL REFERENCES cases(case_id),
 stage_id TEXT NOT NULL REFERENCES stages(stage_id),
 prompt TEXT NOT NULL, prompt_sha256 TEXT NOT NULL,
 snapshot_sha256 TEXT NOT NULL, answer_sha256 TEXT NOT NULL,
 PRIMARY KEY(case_id,stage_id));
CREATE TABLE bridge_proofs (
 case_id TEXT NOT NULL REFERENCES cases(case_id),
 math_tier TEXT NOT NULL, family TEXT NOT NULL,
 base_stage_id TEXT NOT NULL REFERENCES stages(stage_id),
 x_stage_id TEXT NOT NULL REFERENCES stages(stage_id),
 y_stage_id TEXT NOT NULL REFERENCES stages(stage_id),
 merge_stage_id TEXT NOT NULL REFERENCES stages(stage_id),
 merged_prompt_sha256 TEXT NOT NULL, target_sha256 TEXT NOT NULL,
 verified INTEGER NOT NULL CHECK(verified=1),
 PRIMARY KEY(case_id,math_tier,family));
CREATE TABLE equivalence_proofs (
 case_id TEXT NOT NULL REFERENCES cases(case_id),
 math_tier TEXT NOT NULL, family TEXT NOT NULL,
 base_stage_id TEXT NOT NULL REFERENCES stages(stage_id),
 x_stage_id TEXT NOT NULL REFERENCES stages(stage_id),
 y_stage_id TEXT NOT NULL REFERENCES stages(stage_id),
 merge_stage_id TEXT NOT NULL REFERENCES stages(stage_id),
 merged_prompt_sha256 TEXT NOT NULL,
 target_sha256 TEXT NOT NULL,
 x_path_json TEXT NOT NULL, y_path_json TEXT NOT NULL,
 verified INTEGER NOT NULL CHECK(verified=1),
 PRIMARY KEY(case_id,math_tier,family));
CREATE TABLE operand_derivation_proofs (
 case_id TEXT NOT NULL REFERENCES cases(case_id),
 math_tier TEXT NOT NULL, side TEXT NOT NULL CHECK(side IN ('X','Y')),
 expression TEXT NOT NULL, expression_sha256 TEXT NOT NULL,
 evaluation_sha256 TEXT NOT NULL,
 verified INTEGER NOT NULL CHECK(verified=1),
 PRIMARY KEY(case_id,math_tier,side));
CREATE INDEX cases_split_game ON cases(split,game_id,objective);
CREATE INDEX presentations_stage ON presentations(stage_id,case_id);
'''


def stage_id(tier: str, node: str) -> str:
    return tier + '/' + node


def graph(conn: sqlite3.Connection) -> None:
    brain.mutation_sets_valid()
    conn.executemany('INSERT INTO mutation_classes VALUES (?,?,?,?)',
                     ((key,*values) for key,values in sorted({**brain.MUTATION_CLASSES, **brain.BRIDGE_CLASSES}.items())))
    for tier in brain.TIERS:
        conn.executemany('INSERT INTO stages VALUES (?,?,?,?)', (
            (stage_id(tier,node),tier,node,brain.canon([]))
            for node in brain.BRIDGE_NODES[tier]))
        conn.executemany('INSERT INTO stages VALUES (?,?,?,?)', (
            (stage_id(tier, node), tier, node, brain.canon(mutations))
            for node, mutations in brain.NODES.items()))
        conn.executemany('INSERT INTO stage_edges VALUES (?,?,?,?)', (
            (stage_id(tier, parent), stage_id(tier, child), mutation, 'grammar_bridge')
            for parent,child,mutation in brain.bridge_edges(tier)))
        conn.executemany('INSERT INTO stage_edges VALUES (?,?,?,?)', (
            (stage_id(tier, parent), stage_id(tier, child), mutation, 'presentation')
            for parent, child, mutation in brain.EDGES))
    # Intentional scaffolding reset at the next math tier, recorded separately.
    # These are NOT asserted to be one-operand mutations.
    for lo, hi in zip(brain.TIERS, brain.TIERS[1:]):
        conn.execute('INSERT INTO stage_edges VALUES (?,?,?,?)', (
            stage_id(lo, 'raw'), stage_id(hi, 'full'),
            'advance_math_tier_and_restore_assistance', 'math_tier'))


def _source_rows(native_db: Path, limit_per_split: int | None) -> list[tuple]:
    counts = Counter()
    rows = []
    with closing(sqlite3.connect(f'file:{native_db.resolve().as_posix()}?mode=ro', uri=True)) as db:
        validate_native_source(db, native_db)
        # Do not let different objectives or mutations of the same source
        # instance straddle splits.
        leaked = db.execute('''SELECT instance_id FROM native_v6_cases
            WHERE split IN ('train','dev','test','game_transfer')
            GROUP BY instance_id HAVING COUNT(DISTINCT split)>1 LIMIT 1''').fetchone()
        if leaked:
            raise ValueError('SOURCE_INSTANCE_SPLIT_LEAKAGE:' + leaked[0])
        sql = '''SELECT c.case_id,c.instance_id,c.split,c.game_id,c.objective,
            t.prompt,c.t08_sha256
            FROM native_v6_cases c JOIN native_v6_tiers t
            ON c.case_id=t.case_id AND t.tier=8 AND c.status='PASS'
            ORDER BY c.split,c.game_id,c.case_id'''
        for cid, instance, split, game, objective, prompt, prompt_sha in db.execute(sql):
            if native.sha(prompt) != prompt_sha:
                raise ValueError('SOURCE_T08_SHA256_MISMATCH:' + cid)
            if split not in SPLITS:
                continue
            if limit_per_split is not None and counts[split] >= limit_per_split:
                continue
            rows.append((cid, instance, split, game, objective, prompt))
            counts[split] += 1
    if not rows:
        raise ValueError('NO_VERIFIED_NATIVE_SOURCE_CASES')
    return rows


def _persist_case(db: sqlite3.Connection, record: dict, source: tuple, variant: int) -> tuple[int,int]:
    cid, instance, split, game, objective, _ = source
    frozen = record['frozen']
    run_id = record['case_id']
    db.execute('INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
        run_id, cid, instance, split, game, objective, variant,
        frozen['input_sha256'], frozen['mutation_seed'], frozen['order_seed'],
        brain.canon(frozen['snapshot']), frozen['snapshot_sha256'],
        brain.canon(frozen['answer']), frozen['answer_sha256'],
        brain.canon(frozen['native_prompts']),
        brain.canon(frozen['source_proof']), frozen['source_proof_sha256'],
        brain.canon(frozen['descent_proofs']), 1))
    n_prompts = n_proofs = 0
    for tier, family in record['presentations']['tiers'].items():
        db.executemany('INSERT INTO presentations VALUES (?,?,?,?,?,?)', (
            (run_id, stage_id(tier, node), text, family['prompt_hashes'][node],
             frozen['snapshot_sha256'], frozen['answer_sha256'])
            for node, text in family['prompts'].items()))
        n_prompts += len(family['prompts'])
        if tier in brain.TIERS[3:]:
            formula_proofs = family['operand_derivation_proof']
            db.executemany('INSERT INTO operand_derivation_proofs VALUES (?,?,?,?,?,?,?)', (
                (run_id,tier,side,proof['expression'],proof['expression_sha256'],
                 proof['evaluation_sha256'],1) for side,proof in formula_proofs.items()))
        bridge = record['presentations']['bridges'][tier]
        db.executemany('INSERT INTO presentations VALUES (?,?,?,?,?,?)', (
            (run_id, stage_id(tier, node), text, bridge['prompt_hashes'][node],
             frozen['snapshot_sha256'], frozen['answer_sha256'])
            for node, text in bridge['prompts'].items()))
        n_prompts += len(bridge['prompts'])
        logbase = 'grammar_answer' if tier in ('T00','T01') else 'bridge_registered'
        db.execute('INSERT INTO bridge_proofs VALUES (?,?,?,?,?,?,?,?,?,?)', (
            run_id, tier, 'bridge_logbar_xy', stage_id(tier,logbase),
            stage_id(tier,'bridge_logbar_x'),stage_id(tier,'bridge_logbar_y'),
            stage_id(tier,'full'),bridge['merge_prompt_sha256'],
            frozen['answer_sha256'],1))
        if tier in ('T00','T01'):
            db.execute('INSERT INTO bridge_proofs VALUES (?,?,?,?,?,?,?,?,?,?)', (
                run_id,tier,'bridge_punctuation_xy',stage_id(tier,'v9_anchor'),
                stage_id(tier,'grammar_equals_x'),stage_id(tier,'grammar_equals_y'),
                stage_id(tier,'grammar_equals'),brain.digest(bridge['prompts']['grammar_equals']),
                frozen['answer_sha256'],1))
        if tier not in ('T00','T01'):
            regbase = 'grammar_answer' if tier=='T02' else 'v9_anchor'
            db.execute('INSERT INTO bridge_proofs VALUES (?,?,?,?,?,?,?,?,?,?)', (
                run_id,tier,'bridge_register_xy',stage_id(tier,regbase),
                stage_id(tier,'bridge_register_x'),stage_id(tier,'bridge_register_y'),
                stage_id(tier,'bridge_registered'),brain.digest(bridge['prompts']['bridge_registered']),
                frozen['answer_sha256'],1))
        diamond_families = ('logbar_xy','x_register_y_logbar',
                            'x_logbar_y_register','register_xy')
        for i, diamond in enumerate(family['diamonds']):
            db.execute('INSERT INTO equivalence_proofs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (
                run_id, tier, diamond_families[i],
                stage_id(tier, diamond['parent']),
                stage_id(tier, diamond['x_first']),
                stage_id(tier, diamond['y_first']),
                stage_id(tier, diamond['merge']),
                diamond['merged_prompt_sha256'], frozen['answer_sha256'],
                brain.canon([diamond['x_mutation'],diamond['y_mutation']]),
                brain.canon([diamond['y_mutation'],diamond['x_mutation']]), 1))
            n_proofs += 1
        paths = family['whole_side_paths']
        db.execute('INSERT INTO equivalence_proofs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (
            run_id, tier, 'whole_side_xy', stage_id(tier,'full'),
            stage_id(tier,'x_raw_y_full'),stage_id(tier,'y_raw_x_full'),
            stage_id(tier,'raw'),
            paths[0]['merged_prompt_sha256'], frozen['answer_sha256'],
            brain.canon(paths[0]['operations']),
            brain.canon(paths[1]['operations']), 1))
        n_proofs += 1
    return n_prompts, n_proofs


def make_teacher(native_db: Path, output: Path, *, seed: int = 20261017,
                 variants: int = 1, limit_per_split: int | None = None) -> dict:
    if variants < 1 or limit_per_split is not None and limit_per_split < 1:
        raise ValueError('INVALID_GENERATION_SIZE')
    source, dest = Path(native_db).resolve(), Path(output).resolve()
    if not source.is_file() or source == dest:
        raise ValueError('SOURCE_DB_MISSING_OR_OVERWRITE')
    if dest.exists():
        raise FileExistsError('V10_TEACHER_IS_IMMUTABLE')
    rows = _source_rows(source, limit_per_split)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Local staging file; all handles close before atomic create-only promotion.
    staging = dest.with_name(dest.name + f'.staging-{os.getpid()}')
    if staging.exists():
        raise FileExistsError('V10_STAGING_PATH_ALREADY_EXISTS')
    counts = Counter()
    try:
        with closing(sqlite3.connect(staging)) as db:
            db.executescript(DDL)
            with db:
                graph(db)
                db.executemany('INSERT INTO manifest VALUES (?,?)', sorted({
                    'schema': SCHEMA, 'brain_version': brain.VERSION,
                    'case_namespace': CASE_NAMESPACE,
                    'curriculum_bridge': 'native_v9_anchor_to_full_scaffold',
                    'source_sha256': filehash(source), 'seed': str(seed),
                    'variants': str(variants),
                    'limit_per_split': str(limit_per_split or 'all'),
                    'mathematical_snapshot_policy': 'single_v8_checkpoint_then_frozen',
                    'presentation_mutations': brain.canon(brain.MUTATIONS),
                    'native_teacher': 'v8_oracle_verified',
                    'operand_derivation_audit': 'all_T03_to_T08_sides_and_nodes',
                }.items()))
                for row in rows:
                    cid, _, split, _, _, t08 = row
                    for variant in range(variants):
                        mutation_seed = seed_for(seed, split, cid, variant, 'source_mutation')
                        order_seed = seed_for(seed, split, cid, variant, 'source_order')
                        frozen = brain.freeze(t08, mutation_seed=mutation_seed,
                                              order_seed=order_seed)
                        proven = brain.prove_all(frozen)
                        run_id = brain.digest([CASE_NAMESPACE, cid, variant,
                                               frozen['snapshot_sha256']])
                        np, nd = _persist_case(db, {
                            'case_id': run_id, 'frozen': frozen, 'presentations': proven,
                        }, row, variant)
                        counts['cases'] += 1
                        counts['prompts'] += np
                        counts['equivalence_proofs'] += nd
                        counts['split_' + split] += 1
            if db.execute('PRAGMA foreign_key_check').fetchall():
                raise AssertionError('INVALID_V10_DATABASE_FOREIGN_KEYS')
            result = _verify_conn(db, oracle_recheck=True)
            if result['cases'] != counts['cases'] or result['presentations'] != counts['prompts']:
                raise AssertionError('V10_BUILD_COUNTS_DRIFT')
        # os.link is create-only. Unlike os.replace it never overwrites a
        # simultaneously created destination, including on Windows/NTFS.
        os.link(staging, dest)
        staging.unlink()
    finally:
        if staging.exists():
            staging.unlink()
    return {'status': 'PASS', **result,
            'splits': {split: counts['split_' + split] for split in SPLITS},
            'source_sha256': filehash(source), 'teacher_sha256': filehash(dest)}


def _verify_conn(db: sqlite3.Connection, *, oracle_recheck: bool) -> dict:
    if db.execute('PRAGMA foreign_key_check').fetchall():
        raise AssertionError('BROKEN_FOREIGN_KEYS')
    manifest = dict(db.execute('SELECT key,value FROM manifest'))
    if (manifest.get('schema') != SCHEMA or manifest.get('brain_version') != brain.VERSION or
            manifest.get('operand_derivation_audit') != 'all_T03_to_T08_sides_and_nodes'):
        raise ValueError('WRONG_TEACHER_SCHEMA')
    actual_classes = set(db.execute('SELECT class_id,dimension,side,invariant FROM mutation_classes'))
    expected_classes = {(key,*values) for key,values in {**brain.MUTATION_CLASSES, **brain.BRIDGE_CLASSES}.items()}
    if actual_classes != expected_classes:
        raise AssertionError('MUTATION_CLASS_METADATA_DRIFT')
    expected = len(brain.TIERS) * len(brain.NODES) + sum(len(n) for n in brain.BRIDGE_NODES.values())
    if db.execute('SELECT COUNT(*) FROM stages').fetchone()[0] != expected:
        raise AssertionError('INCOMPLETE_STAGE_GRAPH')
    actual_stages = set(db.execute(
        'SELECT stage_id,math_tier,assistance_node,mutation_set_json FROM stages'))
    expected_stages = {
        (stage_id(tier,node),tier,node,brain.canon(muts))
        for tier in brain.TIERS for node,muts in brain.NODES.items()}
    expected_stages.update((stage_id(tier,node),tier,node,brain.canon([]))
        for tier in brain.TIERS for node in brain.BRIDGE_NODES[tier])
    if actual_stages != expected_stages:
        raise AssertionError('STAGE_GRAPH_METADATA_DRIFT')
    actual_edges = set(db.execute(
        'SELECT parent_id,child_id,mutation,edge_kind FROM stage_edges'))
    expected_edges = {
        (stage_id(tier,p),stage_id(tier,c),op,'presentation')
        for tier in brain.TIERS for p,c,op in brain.EDGES}
    expected_edges.update((stage_id(tier,p),stage_id(tier,c),op,'grammar_bridge')
        for tier in brain.TIERS for p,c,op in brain.bridge_edges(tier))
    expected_edges.update((stage_id(lo,'raw'),stage_id(hi,'full'),
                          'advance_math_tier_and_restore_assistance','math_tier')
                          for lo,hi in zip(brain.TIERS,brain.TIERS[1:]))
    if actual_edges != expected_edges:
        raise AssertionError('STAGE_GRAPH_EDGE_DRIFT')
    n_cases = n_prompts = n_diamonds = 0
    splits_by_instance = {}
    for values in db.execute('''SELECT case_id,source_case_id,source_instance_id,split,
        snapshot_json,snapshot_sha256,answer_json,answer_sha256,native_prompts_json,
        source_proof_json,source_proof_sha256,oracle_verified FROM cases ORDER BY case_id'''):
        (run_id, source_id, instance, split, snap_text, snap_hash, answer_text,
         answer_hash, native_text, proof_text, proof_hash, verified) = values
        if split not in SPLITS or verified != 1:
            raise AssertionError('UNVERIFIED_OR_BAD_SPLIT')
        if instance in splits_by_instance and splits_by_instance[instance] != split:
            raise AssertionError('SPLIT_ANCESTRY_LEAK')
        splits_by_instance[instance] = split
        snapshot, answer = json.loads(snap_text), json.loads(answer_text)
        native_prompts = json.loads(native_text)
        if (brain.digest(snapshot) != snap_hash or brain.digest(answer) != answer_hash or
                brain.digest(json.loads(proof_text)) != proof_hash):
            raise AssertionError('CASE_PROVENANCE_HASH_MISMATCH')
        if oracle_recheck:
            st = v8.begin(native.render08(snapshot))
            replay_native={'T08':st['prompt']}
            for _ in range(8):
                st, _ = v8.descend(st)
                replay_native[f'T{st["tier"]:02}']=st['prompt']
            if replay_native != native_prompts:
                raise AssertionError('NATIVE_V9_GRAMMAR_ANCHOR_REPLAY_FAILED')
            if v8.coupled_t00(st, verify=True) != answer:
                raise AssertionError('INDEPENDENT_ORACLE_RECHECK_FAILED')
        frozen = {'snapshot': snapshot, 'snapshot_sha256': snap_hash,
                  'answer': answer, 'answer_sha256': answer_hash,
                  'native_prompts': native_prompts, 'oracle_verified': True}
        expected_proofs = brain.prove_all(frozen)
        expected_family = expected_proofs['tiers']
        actual = list(db.execute('''SELECT s.math_tier,s.assistance_node,p.prompt,p.prompt_sha256,
            p.snapshot_sha256,p.answer_sha256
            FROM presentations p JOIN stages s USING(stage_id)
            WHERE p.case_id=? ORDER BY s.math_tier,s.assistance_node''', (run_id,)))
        if len(actual) != expected:
            raise AssertionError('CASE_STAGE_COVERAGE_MISSING')
        for tier, node, prompt, sha, snap_sha, ans_sha in actual:
            expected_stage = (expected_family[tier] if node in brain.NODES
                              else expected_proofs['bridges'][tier])
            if (prompt != expected_stage['prompts'][node] or
                sha != expected_stage['prompt_hashes'][node] or
                snap_sha != snap_hash or ans_sha != answer_hash):
                raise AssertionError('STAGE_PROMPT_OR_TARGET_DRIFT')
        proof_rows = list(db.execute('''SELECT math_tier,family,base_stage_id,x_stage_id,
            y_stage_id,merge_stage_id,merged_prompt_sha256,target_sha256,
            x_path_json,y_path_json,verified
            FROM equivalence_proofs WHERE case_id=? ORDER BY math_tier,family''', (run_id,)))
        if len(proof_rows) != len(brain.TIERS) * (len(brain.DIAMONDS)+1):
            raise AssertionError('MISSING_XY_EQUIVALENCE_PROOFS')
        pair_names = ('logbar_xy','x_register_y_logbar','x_logbar_y_register',
                      'register_xy')
        for tier, family, base, x, y, merge, merged_hash, target_sha, x_path, y_path, passed in proof_rows:
            expected_tier = expected_family[tier]
            if family in pair_names:
                d = expected_tier['diamonds'][pair_names.index(family)]
                check = (base == stage_id(tier,d['parent']) and
                         x == stage_id(tier,d['x_first']) and
                         y == stage_id(tier,d['y_first']) and
                         merge == stage_id(tier,d['merge']) and
                         merged_hash == d['merged_prompt_sha256'] and
                         x_path == brain.canon([d['x_mutation'],d['y_mutation']]) and
                         y_path == brain.canon([d['y_mutation'],d['x_mutation']]))
            elif family == 'whole_side_xy':
                paths = expected_tier['whole_side_paths']
                check = (base == stage_id(tier,'full') and
                         x == stage_id(tier,'x_raw_y_full') and
                         y == stage_id(tier,'y_raw_x_full') and
                         merge == stage_id(tier,'raw') and
                         merged_hash == paths[0]['merged_prompt_sha256'] and
                         x_path == brain.canon(paths[0]['operations']) and
                         y_path == brain.canon(paths[1]['operations']))
            else:
                check = False
            if not check or target_sha != answer_hash or passed != 1:
                raise AssertionError('INVALID_COMMUTING_DIAMOND')
        actual_bridge_proofs = list(db.execute('''SELECT math_tier,family,base_stage_id,
            x_stage_id,y_stage_id,merge_stage_id,merged_prompt_sha256,target_sha256,verified
            FROM bridge_proofs WHERE case_id=? ORDER BY math_tier,family''', (run_id,)))
        expected_bridge_n = len(brain.TIERS)*2
        if len(actual_bridge_proofs) != expected_bridge_n:
            raise AssertionError('MISSING_GRAMMAR_BRIDGE_PROOFS')
        for tier,family,base,x,y,merge,merged_sha,target_sha,passed in actual_bridge_proofs:
            b = expected_proofs['bridges'][tier]
            if family == 'bridge_logbar_xy':
                expected_tuple = (
                    stage_id(tier,'grammar_answer' if tier in ('T00','T01') else 'bridge_registered'),
                    stage_id(tier,'bridge_logbar_x'),stage_id(tier,'bridge_logbar_y'),
                    stage_id(tier,'full'),b['merge_prompt_sha256'])
            elif family == 'bridge_punctuation_xy' and tier in ('T00','T01'):
                expected_tuple = (stage_id(tier,'v9_anchor'),
                    stage_id(tier,'grammar_equals_x'),stage_id(tier,'grammar_equals_y'),
                    stage_id(tier,'grammar_equals'),brain.digest(b['prompts']['grammar_equals']))
            elif family == 'bridge_register_xy' and tier not in ('T00','T01'):
                expected_tuple = (
                    stage_id(tier,'grammar_answer' if tier=='T02' else 'v9_anchor'),
                    stage_id(tier,'bridge_register_x'),stage_id(tier,'bridge_register_y'),
                    stage_id(tier,'bridge_registered'),brain.digest(b['prompts']['bridge_registered']))
            else:
                raise AssertionError('UNKNOWN_GRAMMAR_BRIDGE_PROOF')
            if (base,x,y,merge,merged_sha)!=expected_tuple or target_sha!=answer_hash or passed!=1:
                raise AssertionError('GRAMMAR_BRIDGE_PROOF_DRIFT')
        actual_derivations = list(db.execute('''SELECT math_tier,side,expression,
            expression_sha256,evaluation_sha256,verified
            FROM operand_derivation_proofs WHERE case_id=? ORDER BY math_tier,side''', (run_id,)))
        expected_derivations = [
            (tier,side,details['expression'],details['expression_sha256'],
             details['evaluation_sha256'],1)
            for tier in brain.TIERS[3:]
            for side,details in sorted(expected_family[tier]['operand_derivation_proof'].items())]
        if actual_derivations != expected_derivations:
            raise AssertionError('OPERAND_DERIVATION_PROOF_DRIFT')
        n_cases += 1
        n_prompts += len(actual)
        n_diamonds += len(proof_rows)
    if not n_cases:
        raise AssertionError('EMPTY_V10_TEACHER')
    return {'cases': n_cases, 'mathematical_tiers': len(brain.TIERS),
            'sublevels_per_tier': len(brain.NODES),
            'presentations': n_prompts, 'equivalence_proofs': n_diamonds,
            'split_ancestries': len(splits_by_instance),
            'grammar_bridge_sublevels': sum(len(n) for n in brain.BRIDGE_NODES.values()),
            'grammar_bridge_proofs': db.execute('SELECT COUNT(*) FROM bridge_proofs').fetchone()[0],
            'operand_derivation_proofs': db.execute('SELECT COUNT(*) FROM operand_derivation_proofs').fetchone()[0]}


def verify_teacher(path: Path, *, oracle_recheck: bool = True) -> dict:
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with closing(sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)) as db:
        result = _verify_conn(db, oracle_recheck=oracle_recheck)
    return {'status': 'PASS', **result, 'teacher_sha256': filehash(path)}


def inspect_teacher(path: Path, *, tier: str = 'T02', case_id: str | None = None) -> dict:
    """Read-only inspection of an actual stored tier's XY mutation paths."""
    if tier not in brain.TIERS:
        raise ValueError('UNKNOWN_MATH_TIER')
    path = Path(path).resolve()
    with closing(sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True)) as db:
        if case_id is None:
            chosen = db.execute('SELECT case_id FROM cases ORDER BY case_id LIMIT 1').fetchone()
            if chosen is None:
                raise ValueError('EMPTY_TEACHER')
            case_id = chosen[0]
        case = db.execute('SELECT source_case_id,game_id,objective,split,answer_json,snapshot_sha256,answer_sha256 FROM cases WHERE case_id=?', (case_id,)).fetchone()
        if case is None:
            raise ValueError('CASE_NOT_IN_TEACHER')
        source_id, game, objective, split, answer, snapshot_sha, target_sha = case
        prompts = dict(db.execute('SELECT s.assistance_node,p.prompt FROM presentations p JOIN stages s ON s.stage_id=p.stage_id WHERE p.case_id=? AND s.math_tier=? ORDER BY s.assistance_node', (case_id,tier)))
        formulas = {side:expression for side,expression in db.execute(
            'SELECT side,expression FROM operand_derivation_proofs WHERE case_id=? AND math_tier=?',
            (case_id,tier))}
        paths = [dict(family=family,x_first=json.loads(x),y_first=json.loads(y),
                      merged_prompt_sha256=hashtext)
                 for family,x,y,hashtext in db.execute('SELECT family,x_path_json,y_path_json,merged_prompt_sha256 FROM equivalence_proofs WHERE case_id=? AND math_tier=? ORDER BY family', (case_id,tier))]
        return {'case_id':case_id,'source_case_id':source_id,'game_id':game,
                'objective':objective,'split':split,'tier':tier,
                'answer':json.loads(answer),'snapshot_sha256':snapshot_sha,
                'answer_sha256':target_sha,'prompts':prompts,
                'equivalent_transformation_paths':paths,
                'grammar_bridge_edges':brain.bridge_edges(tier),
                'grammar_bridge_nodes':list(brain.BRIDGE_NODES[tier]),
                'operand_formulas':formulas}

def audit_teacher(path: Path, *, tier: str='T03', split: str='dev', examples: int=5) -> dict:
    """Read-only, all-case operand proof check and true prompt-pair examples.

    Builds no database, loads no model, performs no mutations. The verify
    phase reconstructs all stored cases and operand expressions first.
    """
    if tier not in brain.TIERS or split not in SPLITS or not 1 <= examples <= 20:
        raise ValueError('INVALID_AUDIT_OPTIONS')
    verified = verify_teacher(path, oracle_recheck=True)
    with closing(sqlite3.connect(f'file:{Path(path).resolve().as_posix()}?mode=ro', uri=True)) as db:
        counts = {f'{kind}/{mode}':amount for kind,mode,amount in db.execute('''
            SELECT json_extract(snapshot_json,'$.kind'),objective,COUNT(*)
            FROM cases GROUP BY 1,2 ORDER BY 1,2''')}
        sample_ids = [row[0] for row in db.execute('''
            SELECT case_id FROM cases WHERE split=? ORDER BY game_id,case_id LIMIT ?''',
            (split,examples))]
    samples = []
    for cid in sample_ids:
        item=inspect_teacher(path,tier=tier,case_id=cid)
        samples.append({key:item[key] for key in
            ('case_id','game_id','objective','answer','operand_formulas')})
        samples[-1]['prompts']={key:item['prompts'][key] for key in
            ('x_raw_y_full','y_raw_x_full','raw')}
    return {'status':'PASS','verified_teacher':verified,'objective_coverage':counts,
            'sample_split':split,'sample_tier':tier,'examples':samples}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build', help='Build a new verified immutable V10 teacher')
    build.add_argument('--native-db', type=Path, required=True)
    build.add_argument('--output', type=Path, required=True)
    build.add_argument('--seed', type=int, default=20261017)
    build.add_argument('--variants', type=int, default=1)
    build.add_argument('--limit-per-split', type=int)
    source = sub.add_parser('source-info', help='Check native source DB read-only before building')
    source.add_argument('--native-db', type=Path, required=True)
    verify = sub.add_parser('verify', help='Independently verify all stored prompts and proofs')
    verify.add_argument('--teacher-db', type=Path, required=True)
    verify.add_argument('--no-oracle-recheck', action='store_true')
    audit = sub.add_parser('audit', help='Recheck every mathematical expression and show paired examples')
    audit.add_argument('--teacher-db', type=Path, required=True)
    audit.add_argument('--tier', choices=brain.TIERS, default='T03')
    audit.add_argument('--split', choices=SPLITS, default='dev')
    audit.add_argument('--examples', type=int, default=5)
    inspect = sub.add_parser('inspect', help='Show stored X-first/Y-first prompts')
    inspect.add_argument('--teacher-db', type=Path, required=True)
    inspect.add_argument('--tier', choices=brain.TIERS, default='T02')
    inspect.add_argument('--case-id')
    a = p.parse_args(argv)
    if a.command == 'build':
        result = make_teacher(a.native_db,a.output,seed=a.seed,
                              variants=a.variants,limit_per_split=a.limit_per_split)
    elif a.command == 'source-info':
        result = source_info(a.native_db)
    elif a.command == 'verify':
        result = verify_teacher(a.teacher_db,oracle_recheck=not a.no_oracle_recheck)
    elif a.command == 'audit':
        result = audit_teacher(a.teacher_db,tier=a.tier,split=a.split,examples=a.examples)
    else:
        result = inspect_teacher(a.teacher_db,tier=a.tier,case_id=a.case_id)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
