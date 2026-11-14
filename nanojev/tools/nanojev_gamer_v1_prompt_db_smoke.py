#!/usr/bin/env python3
"""Build a complete GAMER game-prompt SQLite smoke corpus from registered sources.

All catalog and executable games receive prompts. Only source-regenerated,
mathematically certified objectives are marked eligible for numerical training.
Catalog-only games receive clearly marked *unlabelled* modeling/transfer prompts;
no invented payoff or equilibrium answers are introduced.

Usage:
  python -m tools.nanojev_gamer_v1_prompt_db_smoke build --output runtime/reports/gamer-v1/game-prompts-smoke-0001/prompts.sqlite3
  python -m tools.nanojev_gamer_v1_prompt_db_smoke verify --output runtime/reports/gamer-v1/game-prompts-smoke-0001/prompts.sqlite3
  python -m tools.nanojev_gamer_v1_prompt_db_smoke export --output runtime/reports/gamer-v1/game-prompts-smoke-0001/prompts.sqlite3 --jsonl runtime/reports/gamer-v1/game-prompts-smoke-0001/certified_pairs.jsonl
  python -m tools.nanojev_gamer_v1_prompt_db_smoke train --output runtime/reports/gamer-v1/game-prompts-smoke-0001/prompts.sqlite3 --train-output runtime/reports/gamer-v1/game-planning-smoke-0001
  python -m tools.nanojev_gamer_v1_prompt_db_smoke query --output runtime/reports/gamer-v1/game-prompts-smoke-0001/prompts.sqlite3 --train-output runtime/reports/gamer-v1/game-planning-smoke-0001 --prompt "Two players choose whether to cooperate."
  python -m tools.nanojev_gamer_v1_prompt_db_smoke audit --output runtime/reports/gamer-v1/game-prompts-smoke-0001/prompts.sqlite3 --train-output runtime/reports/gamer-v1/game-planning-smoke-0001 --audit-report runtime/reports/gamer-v1/game-planning-audit-0001.json
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
import tempfile

from tools import nanojev_game_transformation_library as catalog
from tools import nanojev_gamer_v1_catalog_extensions as ext
from tools import nanojev_gamer_v1_transition_games as transition
from tools import nanojev_gamer_v1_pairs as pairs

SCHEMA = 'nanojev.gamer.v1.complete-prompt-smoke.v1'
SCOPES = ('native_oracle', 'bounded_catalog_oracle', 'bounded_transition_oracle', 'catalog_only')
FAMILY_MODELS = {family: (name, equation) for family, name, equation, _ in catalog.FAMILIES}

DDL = '''
PRAGMA foreign_keys=ON;
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE games (
 game_id TEXT PRIMARY KEY, title TEXT NOT NULL, family TEXT NOT NULL,
 scope TEXT NOT NULL CHECK(scope IN ('native_oracle','bounded_catalog_oracle','bounded_transition_oracle','catalog_only')),
 numerical_oracle_available INTEGER NOT NULL CHECK(numerical_oracle_available IN (0,1)),
 bounded_objective_only INTEGER NOT NULL CHECK(bounded_objective_only IN (0,1)),
 family_math_reference TEXT NOT NULL
);
CREATE TABLE prompts (
 prompt_id TEXT PRIMARY KEY,
 game_id TEXT NOT NULL REFERENCES games(game_id),
 split TEXT NOT NULL CHECK(split IN ('train','dev','test','catalog')),
 task TEXT NOT NULL,
 prompt TEXT NOT NULL,
 prompt_sha256 TEXT NOT NULL UNIQUE,
 numerical_training_eligible INTEGER NOT NULL CHECK(numerical_training_eligible IN (0,1)),
 oracle_verified INTEGER NOT NULL CHECK(oracle_verified IN (0,1)),
 table_verified INTEGER NOT NULL CHECK(table_verified IN (0,1)),
 numeric_answer TEXT,
 math_target TEXT,
 table_families_json TEXT,
 source_case_json TEXT,
 CHECK (
   (numerical_training_eligible=1 AND oracle_verified=1 AND table_verified=1
     AND split IN ('train','dev','test') AND numeric_answer IS NOT NULL
     AND math_target IS NOT NULL AND source_case_json IS NOT NULL)
   OR
   (numerical_training_eligible=0 AND oracle_verified=0 AND table_verified=0
     AND split='catalog' AND numeric_answer IS NULL
     AND math_target IS NULL AND source_case_json IS NULL)
 )
);
CREATE INDEX prompts_game_split ON prompts(game_id,split);
CREATE INDEX prompts_split_task ON prompts(split,task);
CREATE VIEW certified_training_prompts AS
 SELECT prompt_id,game_id,split,task,prompt,numeric_answer,math_target,
        table_families_json,source_case_json
 FROM prompts WHERE numerical_training_eligible=1;
CREATE VIEW catalog_research_prompts AS
 SELECT prompt_id,game_id,task,prompt FROM prompts
 WHERE numerical_training_eligible=0;
CREATE VIEW game_coverage AS
 SELECT g.game_id,g.title,g.family,g.scope,
        count(p.prompt_id) AS prompt_count,
        sum(CASE WHEN p.numerical_training_eligible=1 THEN 1 ELSE 0 END) AS certified_count,
        sum(CASE WHEN p.numerical_training_eligible=0 THEN 1 ELSE 0 END) AS unlabelled_count
 FROM games g LEFT JOIN prompts p ON g.game_id=p.game_id GROUP BY g.game_id;
'''


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def source_registry():
    """Union of the *real* source registries; never silently invent new games."""
    items = {}
    for family, titles in catalog.CATALOG.items():
        for title in titles.split('|'):
            key = catalog.family_game_id(title)
            if key in items and items[key][:2] != (title, family):
                raise ValueError(f'GAMER_CATALOG_ID_COLLISION:{key}')
            items[key] = (title, family, 'catalog_only')
    native = set(catalog.MATRICES) | set(catalog.SCALAR_MODELS)
    extra = set(ext.EXTRA_IDS)
    trans = set(transition.TRANSITION_IDS)
    if native & extra or native & trans or extra & trans:
        raise ValueError('GAMER_PROMPT_REGISTRY_OVERLAP')
    for game in native:
        entry = catalog.MATRICES.get(game) or catalog.SCALAR_MODELS.get(game)
        items[game] = (entry[0], entry[1], 'native_oracle')
    for game in extra:
        data = ext.EXTRA_MATRICES.get(game) or ext.EXTRA_SCALARS.get(game)
        if game not in items:
            raise ValueError(f'GAMER_CATALOG_EXTENSION_NOT_IN_CATALOG:{game}')
        if data['title'] != items[game][0] or data['family'] != items[game][1]:
            raise ValueError(f'GAMER_CATALOG_EXTENSION_METADATA_DRIFT:{game}')
        items[game] = (items[game][0], items[game][1], 'bounded_catalog_oracle')
    for game in trans:
        data = transition.SPECS[game]
        if game not in items or data['title'] != items[game][0] or data['family'] != items[game][1]:
            raise ValueError(f'GAMER_TRANSITION_METADATA_DRIFT:{game}')
        items[game] = (items[game][0], items[game][1], 'bounded_transition_oracle')
    return dict(sorted(items.items()))


def catalog_prompt(title: str, family: str, kind: int) -> str:
    name, equation = FAMILY_MODELS[family]
    if kind == 0:
        return (
            f'Game: {title}. Family: {name}. The family-level mathematical reference is {equation}. '
            'This entry is a catalog reference, not an executable specification of this named game. '
            'Identify the players, legal actions, strategic state, observations or hidden information, '
            'horizon, transition rules, and payoff definition that would have to be supplied '
            'before a certified numerical game objective or equilibrium could be calculated. '
            'Do not invent missing game rules or numerical answers.'
        )
    return (
        f'Consider a proposed strategic transformation of {title} in the {name} family. '
        f'The family-level mathematical reference is {equation}. '
        'Describe the original and transformed game specifications and verification obligations '
        'needed to determine whether changing a player action, available information, or payoff '
        'would preserve strategic structure or alter incentives. '
        'No executable instance or numerical oracle is supplied. Do not fabricate an equilibrium or payoff.'
    )


def _verify_counts(registry, *, strict=True):
    counts = Counter(entry[2] for entry in registry.values())
    if strict and (counts != Counter(native_oracle=33, bounded_catalog_oracle=58,
                                    bounded_transition_oracle=55, catalog_only=83)
                   or len(registry) != 229):
        raise ValueError(f'GAMER_REGISTRY_DRIFT:{dict(counts)}; total={len(registry)}')
    return counts


def _insert_certified(conn, registry, cases_per_game: int, seed: int):
    ids = [game for game, info in registry.items() if info[2] != 'catalog_only']
    seen = set()
    splits = Counter()
    for game in ids:
        for index in range(cases_per_game):
            for attempt in range(300):
                row = pairs.generate_case(game, index, seed, attempt)
                if row['prompt_08'] not in seen:
                    break
            else:
                raise ValueError(f'GAMER_DUPLICATE_PROMPT_NO_UNIQUE_CASE:{game}:{index}')
            pairs.certify(row)
            if row['oracle_verified'] is not True or row['table_verified'] is not True:
                raise ValueError(f'GAMER_UNCERTIFIED_GENERATED_CASE:{game}:{index}')
            if row['split'] not in ('train','dev','test'):
                raise ValueError(f'GAMER_UNEXPECTED_SOURCE_SPLIT:{game}:{index}')
            seen.add(row['prompt_08'])
            splits[row['split']] += 1
            prompt_sha = sha_text(row['prompt_08'])
            conn.execute('''INSERT INTO prompts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (row['case_sha256'], game, row['split'], 'certified_numerical_objective',
                 row['prompt_08'], prompt_sha, 1, 1, 1,
                 str(row['answer']), row['math_prompt'], canonical(row['table_families']), canonical(row)))
    if not all(splits.get(split) for split in ('train','dev','test')):
        raise ValueError('GAMER_PROMPTS_MISSING_GLOBAL_SPLIT')
    return splits


def build(output: Path, *, cases_per_game=18, catalog_prompts_per_game=2, seed=47, replace=False):
    if cases_per_game < 6 or cases_per_game % 6:
        raise ValueError('GAMER_CASES_PER_GAME_MUST_BE_MULTIPLE_OF_SIX_AT_LEAST_SIX')
    if catalog_prompts_per_game not in (1,2):
        raise ValueError('GAMER_CATALOG_PROMPTS_PER_GAME_MUST_BE_1_OR_2')
    output = Path(output)
    if output.exists() and not replace:
        raise FileExistsError(f'GAMER_PROMPT_DB_EXISTS:{output}')
    registry = source_registry()
    scopes = _verify_counts(registry)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Build a new DB in the same filesystem and replace only after verification.
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.sqlite3', delete=False) as tmp:
        temporary = Path(tmp.name)
    try:
        # sqlite3.Connection.__exit__ commits but does NOT close the handle.
        # Explicit closing is required before os.replace on Windows (WinError 32).
        with closing(sqlite3.connect(temporary)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.executescript(DDL)
            conn.executemany('INSERT INTO games VALUES (?,?,?,?,?,?,?)',
                [(game, title, family, scope, int(scope != 'catalog_only'),
                  int(scope in ('bounded_catalog_oracle','bounded_transition_oracle')),
                  FAMILY_MODELS[family][1])
                 for game,(title,family,scope) in registry.items()])
            splits = _insert_certified(conn, registry, cases_per_game, seed)
            for game,(title,family,scope) in registry.items():
                if scope != 'catalog_only':
                    continue
                for kind in range(catalog_prompts_per_game):
                    prompt = catalog_prompt(title, family, kind)
                    pid = sha_text(canonical(['catalog',game,kind,prompt]))
                    conn.execute('INSERT INTO prompts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                 (pid,game,'catalog',('structural_inventory' if kind==0 else 'transformation_specification'),
                                  prompt,sha_text(prompt),0,0,0,None,None,None,None))
            meta = dict(schema=SCHEMA, seed=str(seed), cases_per_verified_game=str(cases_per_game),
                        prompts_per_catalog_only_game=str(catalog_prompts_per_game),
                        registry_sha256=sha_text(canonical(registry)),
                        native_source_sha256=pairs.source_signature(),
                        bounded_catalog_source_sha256=pairs.extension_signature(),
                        bounded_transition_source_sha256=pairs.transition_signature(),
                        source_game_count=str(len(registry)),
                        certified_game_count=str(len(registry)-scopes['catalog_only']),
                        catalog_only_game_count=str(scopes['catalog_only']),
                        training_contract='SOURCE_CERTIFIED_ROWS_ONLY; CATALOG_ROWS_HAVE_NO_NUMERICAL_TARGET',
                        split_counts=canonical(splits))
            conn.executemany('INSERT INTO metadata VALUES (?,?)', sorted(meta.items()))
            conn.commit()
            result = verify_connection(conn, cases_per_game=cases_per_game,
                                       catalog_prompts_per_game=catalog_prompts_per_game)
        if output.exists() and not replace:
            raise FileExistsError(f'GAMER_PROMPT_DB_EXISTS:{output}')
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return dict(result, output=str(output), sqlite_sha256=hashlib.sha256(output.read_bytes()).hexdigest())


def verify_connection(conn, *, cases_per_game=None, catalog_prompts_per_game=None, verify_each_oracle=False):
    conn.execute('PRAGMA foreign_keys=ON')
    bad = conn.execute('PRAGMA integrity_check').fetchone()[0]
    if bad != 'ok':
        raise ValueError(f'GAMER_PROMPT_SQLITE_CORRUPT:{bad}')
    if conn.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('GAMER_PROMPT_FOREIGN_KEYS_BROKEN')
    meta = dict(conn.execute('SELECT key,value FROM metadata'))
    if meta.get('schema') != SCHEMA:
        raise ValueError('GAMER_PROMPT_WRONG_SCHEMA')
    registry = source_registry()
    expected_counts = _verify_counts(registry)
    if meta['registry_sha256'] != sha_text(canonical(registry)) or any(
        meta.get(key) != sig for key,sig in (
            ('native_source_sha256',pairs.source_signature()),
            ('bounded_catalog_source_sha256',pairs.extension_signature()),
            ('bounded_transition_source_sha256',pairs.transition_signature()))):
        raise ValueError('GAMER_PROMPT_SOURCE_REGISTRY_DRIFT')
    expected_cert = int(meta['cases_per_verified_game'])
    expected_catalog = int(meta['prompts_per_catalog_only_game'])
    if cases_per_game is not None and expected_cert != cases_per_game:
        raise ValueError('GAMER_PROMPT_EXPECTED_CERTIFIED_COUNT_MISMATCH')
    if catalog_prompts_per_game is not None and expected_catalog != catalog_prompts_per_game:
        raise ValueError('GAMER_PROMPT_EXPECTED_CATALOG_COUNT_MISMATCH')
    games = conn.execute('SELECT game_id,title,family,scope FROM games ORDER BY game_id').fetchall()
    if games != [(game,*data) for game,data in registry.items()]:
        raise ValueError('GAMER_PROMPT_GAME_COVERAGE_OR_SCOPE_CHANGED')
    verified=0
    catalog_rows=0
    splits=Counter()
    fingerprints=set()
    for pid,game,split,task,prompt,hash_,eligible,oracle,table,num,math,source in conn.execute(
            '''SELECT prompt_id,game_id,split,task,prompt,prompt_sha256,
                      numerical_training_eligible,oracle_verified,table_verified,
                      numeric_answer,math_target,source_case_json FROM prompts'''):
        if not prompt.strip() or sha_text(prompt) != hash_ or hash_ in fingerprints:
            raise ValueError('GAMER_PROMPT_BAD_OR_DUPLICATE_PROMPT_FINGERPRINT')
        fingerprints.add(hash_)
        scope=registry[game][2]
        if eligible:
            if scope=='catalog_only' or not (oracle == table == 1) or split not in ('train','dev','test'):
                raise ValueError('GAMER_PROMPT_CERTIFICATION_LEAKAGE')
            record=json.loads(source)
            if (record['case_sha256'] != pid or record['prompt_08'] != prompt
                    or record['game_id'] != game or record['split'] != split
                    or str(record['answer']) != num or record['math_prompt'] != math):
                raise ValueError('GAMER_PROMPT_CERTIFIED_SOURCE_MISMATCH')
            if verify_each_oracle:
                pairs.certify(record)
            verified+=1
            splits[split]+=1
        else:
            if scope != 'catalog_only' or split != 'catalog' or oracle or table or source is not None or num is not None or math is not None:
                raise ValueError('GAMER_PROMPT_UNVERIFIED_CATALOG_TARGET_LEAKAGE')
            if pid != sha_text(canonical(['catalog',game,0 if task=='structural_inventory' else 1,prompt])):
                raise ValueError('GAMER_PROMPT_CATALOG_LINEAGE_CHANGED')
            if task not in ('structural_inventory','transformation_specification'):
                raise ValueError('GAMER_PROMPT_UNEXPECTED_CATALOG_TASK')
            if prompt != catalog_prompt(registry[game][0], registry[game][1],
                                        0 if task=='structural_inventory' else 1):
                raise ValueError('GAMER_PROMPT_CATALOG_SOURCE_PROMPT_CHANGED')
            catalog_rows+=1
            splits['catalog']+=1
    by_game=dict(conn.execute('SELECT game_id, COUNT(*) FROM prompts GROUP BY game_id'))
    if len(by_game) != len(registry) or any(by_game.get(game) !=
                (expected_catalog if data[2]=='catalog_only' else expected_cert)
                for game,data in registry.items()):
        raise ValueError('GAMER_PROMPT_GAME_COVERAGE_INCOMPLETE')
    if verified != (len(registry)-expected_counts['catalog_only'])*expected_cert or catalog_rows != expected_counts['catalog_only']*expected_catalog:
        raise ValueError('GAMER_PROMPT_COUNT_DRIFT')
    if not all(splits.get(k,0) for k in ('train','dev','test','catalog')):
        raise ValueError('GAMER_PROMPT_MISSING_SPLIT')
    if meta['split_counts'] != canonical({k:v for k,v in splits.items() if k!='catalog'}):
        raise ValueError('GAMER_PROMPT_SPLIT_LINEAGE_CHANGED')
    return {
        'status':'PASS_COMPLETE_GAME_PROMPT_SQLITE_SMOKE',
        'schema': SCHEMA,
        'games':len(registry),
        'families':len({data[1] for data in registry.values()}),
        'game_scopes':dict(expected_counts),
        'prompts':len(fingerprints),
        'certified_numerical_prompts':verified,
        'unlabelled_catalog_prompts':catalog_rows,
        'by_split':dict(sorted(splits.items())),
        'all_game_ids_covered':True,
        'catalog_only_excluded_from_training':True,
        'source_signatures_verified':True,
        'recomputed_each_numerical_oracle':bool(verify_each_oracle),
    }


def verify(path: Path, *, verify_each_oracle=False):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'GAMER_PROMPT_DB_MISSING:{path}')
    with closing(sqlite3.connect(f'file:{path.resolve().as_posix()}?mode=ro', uri=True)) as conn:
        return verify_connection(conn, verify_each_oracle=verify_each_oracle)


def export_verified(path: Path, jsonl: Path, *, replace=False):
    report=verify(path, verify_each_oracle=True)
    jsonl=Path(jsonl)
    if jsonl.exists() and not replace:
        raise FileExistsError(f'GAMER_VERIFIED_EXPORT_EXISTS:{jsonl}')
    jsonl.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',dir=jsonl.parent,encoding='utf-8',delete=False) as fp:
        temporary=Path(fp.name)
    try:
        with closing(sqlite3.connect(f'file:{Path(path).resolve().as_posix()}?mode=ro',uri=True)) as conn, temporary.open('w',encoding='utf-8') as fp:
            for (serialized,) in conn.execute('''SELECT source_case_json FROM prompts
                  WHERE numerical_training_eligible=1 ORDER BY game_id, json_extract(source_case_json,'$.case_index')'''):
                fp.write(serialized+'\n')
        if jsonl.exists() and not replace:
            raise FileExistsError(f'GAMER_VERIFIED_EXPORT_EXISTS:{jsonl}')
        os.replace(temporary,jsonl)
    finally:
        if temporary.exists():temporary.unlink()
    return dict(report,exported_jsonl=str(jsonl),export_sha256=hashlib.sha256(jsonl.read_bytes()).hexdigest())


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('build','verify','export','train','score','query','audit','construct-train','construct-query','construct-census','construct-audit','hawk-dove-train','hawk-dove-query','hawk-dove-language-train','hawk-dove-language-query','hawk-dove-language-census'))
    parser.add_argument('--output',type=Path,help='SQLite prompt database (required for original DB stages)')
    parser.add_argument('--cases-per-game',type=int,default=18,help='Certified examples per source-executable game (multiple of six)')
    parser.add_argument('--catalog-prompts-per-game',type=int,default=2,help='Unlabelled research prompts per catalog-only game (1 or 2)')
    parser.add_argument('--seed',type=int,default=47)
    parser.add_argument('--jsonl',type=Path,help='For export: source-verified GAMER pairs JSONL')
    parser.add_argument('--train-output',type=Path,help='Isolated auxiliary strategy-router training output directory')
    parser.add_argument('--prompt',help='For score/query: raw unlabelled prompt; bounded 2x2 games can also be supplied as strict JSON (no oracle labels)')
    parser.add_argument('--construction-output',type=Path,help='Isolated frozen-Qwen game-construction checkpoint directory')
    parser.add_argument('--hawk-dove-output',type=Path,help='Isolated T08->T00 fixed-game acquired payoff head')
    parser.add_argument('--hawk-dove-language-output',type=Path,help='Isolated T08 15-train/5-holdout language head')
    parser.add_argument('--hawk-dove-language-encoder',choices=('frozen-qwen','text'),default='frozen-qwen',help='Frozen Qwen/LoRA plus lexical context (or text-only diagnostic)')
    parser.add_argument('--hawk-dove-step',type=int,default=1000,help='Full acquired count-grid stride (must divide 1,000,000)')
    parser.add_argument('--hawk-dove-fixture',type=Path,default=Path('examples/hawk_dove_million_DEMO_ONLY.json'),help='Original fixed teacher fixture, if available')
    parser.add_argument('--run',type=Path,default=Path('runtime/reports/gamer-v1/transition-005'),help='Source frozen GAMER V1 run containing lora/best_adapter')
    parser.add_argument('--model',default='Qwen/Qwen3-0.6B',help='Frozen original GAMER Qwen model identifier')
    parser.add_argument('--device',default='cuda',help='Qwen frozen feature capture device')
    parser.add_argument('--layer',type=int,default=9,help='Frozen Qwen hidden-state layer')
    parser.add_argument('--samples-per-template',type=int,default=48,help='Synthetic construction cases per distinct language template')
    parser.add_argument('--max-length',type=int,default=256,help='Maximum nontruncated construction prompt tokens')
    parser.add_argument('--min-confidence',type=float,default=0.80,help='Query abstention threshold per predicted payoff slot')
    parser.add_argument('--audit-report',type=Path,help='For audit: immutable report destination outside training output')
    parser.add_argument('--epochs',type=int,default=8,help='Auxiliary router/constructor head epochs; no LoRA training')
    parser.add_argument('--alpha',type=float,default=1e-5,help='Strategy-router SGD regularization')
    parser.add_argument('--replace',action='store_true',help='Explicitly replace the output (never trained model state)')
    parser.add_argument('--full-oracle-verify',action='store_true',help='Regenerate every numerical label during verification')
    args=parser.parse_args(argv)
    if args.stage not in ('construct-train','construct-query','construct-census','construct-audit','hawk-dove-train','hawk-dove-query','hawk-dove-language-train','hawk-dove-language-query','hawk-dove-language-census') and args.output is None:
        parser.error(args.stage + ' requires --output')
    if args.stage=='build':
        report=build(args.output,cases_per_game=args.cases_per_game,
                     catalog_prompts_per_game=args.catalog_prompts_per_game,
                     seed=args.seed,replace=args.replace)
    elif args.stage=='verify':
        report=verify(args.output,verify_each_oracle=args.full_oracle_verify)
    elif args.stage=='export':
        if args.jsonl is None:
            parser.error('export requires --jsonl')
        report=export_verified(args.output,args.jsonl,replace=args.replace)
    elif args.stage=='train':
        if args.train_output is None:
            parser.error('train requires --train-output')
        if args.replace:
            parser.error('train never replaces an existing run')
        from tools.nanojev_gamer_v1_prompt_db_train import train, validate_train_request
        # Fail before creating a new database if training could not proceed.
        validate_train_request(args.output, args.train_output, epochs=args.epochs, alpha=args.alpha)
        if not args.output.exists():
            print('GAMER TRAIN AUTO-BUILD: missing SQLite corpus; generating all 229 games '
                  f'(cases/game={args.cases_per_game}, catalog/game={args.catalog_prompts_per_game}, '
                  f'seed={args.seed})', flush=True)
            built=build(args.output,cases_per_game=args.cases_per_game,
                        catalog_prompts_per_game=args.catalog_prompts_per_game,seed=args.seed)
            print('GAMER TRAIN AUTO-BUILD VERIFIED: '
                  f"{built['games']} games, {built['prompts']} prompts, "
                  f"{built['certified_numerical_prompts']} certified numerical cases, "
                  f"{built['unlabelled_catalog_prompts']} catalog prompts", flush=True)
        else:
            # Never silently overwrite or repair an existing corpus. `train` will
            # verify registry, all splits, every source, and each numerical oracle.
            print(f'GAMER TRAIN: reusing existing SQLite corpus; verifying {args.output}',
                  flush=True)
        report=train(args.output,args.train_output,epochs=args.epochs,alpha=args.alpha,seed=args.seed)
    elif args.stage=='hawk-dove-train':
        if args.hawk_dove_output is None:
            parser.error('hawk-dove-train requires --hawk-dove-output')
        from tools.nanojev_gamer_v1_hawk_dove_acquisition import train as hawk_dove_train
        report=hawk_dove_train(args.hawk_dove_output,epochs=args.epochs,seed=args.seed,
                               fixture=args.hawk_dove_fixture,step=args.hawk_dove_step)
    elif args.stage=='hawk-dove-query':
        if args.hawk_dove_output is None or args.prompt is None:
            parser.error('hawk-dove-query requires --hawk-dove-output and --prompt')
        from tools.nanojev_gamer_v1_hawk_dove_acquisition import query as hawk_dove_query
        report=hawk_dove_query(args.hawk_dove_output,args.prompt)
    elif args.stage=='hawk-dove-language-census':
        from tools.nanojev_gamer_v1_hawk_dove_t08_corpus import generate,TRAIN_TEMPLATES,HOLDOUT_TEMPLATES
        rows=generate(seed=args.seed,samples_per_template=args.samples_per_template)
        report={'status':'T08_LANGUAGE_15_TRAIN_5_HOLDOUT_CORPUS_PLAN',
                'training_templates':len(TRAIN_TEMPLATES),
                'heldout_templates':len(HOLDOUT_TEMPLATES),
                'training_cases':sum(r['split']=='train' for r in rows),
                'heldout_cases':sum(r['split']=='holdout' for r in rows),
                'checkpoint_or_source_modified':False}
    elif args.stage=='hawk-dove-language-train':
        if args.hawk_dove_language_output is None or args.hawk_dove_output is None:
            parser.error('hawk-dove-language-train requires --hawk-dove-output and --hawk-dove-language-output')
        from tools.nanojev_gamer_v1_hawk_dove_t08_language import train as language_train
        report=language_train(language_output=args.hawk_dove_language_output,
                              hawk_dove_output=args.hawk_dove_output,
                              run=args.run,model_id=args.model,device=args.device,layer=args.layer,
                              encoder=args.hawk_dove_language_encoder,
                              epochs=args.epochs,seed=args.seed,
                              samples_per_template=args.samples_per_template,
                              max_length=args.max_length)
    elif args.stage=='hawk-dove-language-query':
        if args.hawk_dove_language_output is None or args.hawk_dove_output is None or args.prompt is None:
            parser.error('hawk-dove-language-query requires --hawk-dove-output, --hawk-dove-language-output and --prompt')
        from tools.nanojev_gamer_v1_hawk_dove_t08_language import query as language_query
        report=language_query(language_output=args.hawk_dove_language_output,
                              hawk_dove_output=args.hawk_dove_output,
                              prompt=args.prompt,device=args.device)
    elif args.stage=='construct-train':
        if args.construction_output is None:
            parser.error('construct-train requires --construction-output')
        from tools.nanojev_gamer_v1_construction_head import train as construction_train
        report=construction_train(run=args.run,output=args.construction_output,
                                  model_id=args.model,device=args.device,layer=args.layer,
                                  seed=args.seed,samples_per_template=args.samples_per_template,
                                  epochs=args.epochs,max_length=args.max_length)
    elif args.stage=='construct-query':
        if args.construction_output is None or args.prompt is None:
            parser.error('construct-query requires --construction-output and --prompt')
        from tools.nanojev_gamer_v1_construction_head import query as construction_query
        report=construction_query(run=args.run,output=args.construction_output,
                                  prompt=args.prompt,device=args.device,
                                  min_confidence=args.min_confidence)
    elif args.stage=='construct-audit':
        if args.construction_output is None:
            parser.error('construct-audit requires --construction-output')
        from tools.nanojev_gamer_v1_construction_head import audit_existing_checkpoint
        report=audit_existing_checkpoint(run=args.run,output=args.construction_output,
                                         device=args.device)
    elif args.stage=='construct-census':
        from tools.nanojev_gamer_v1_construction_head import generate,TRAIN_TEMPLATES,TEST_TEMPLATES
        rows=generate(seed=args.seed,samples_per_template=args.samples_per_template)
        report={'status':'CONSTRUCTION_CORPUS_PLAN_ONLY_NO_TRAINING',
                'training_cases':sum(r['split']=='train' for r in rows),
                'challenge_cases':sum(r['split']=='challenge' for r in rows),
                'training_language_templates':len(TRAIN_TEMPLATES),
                'challenge_disjoint_language_templates':len(TEST_TEMPLATES),
                'bounded_2x2_domain_only':True,
                'no_source_corpus_or_checkpoint_modified':True}
    elif args.stage=='audit':
        if args.train_output is None:
            parser.error('audit requires --train-output')
        from tools.nanojev_gamer_v1_prompt_db_query import audit
        report=audit(args.output,args.train_output,seed=args.seed,epochs=args.epochs,
                     report_path=args.audit_report)
    elif args.stage=='query':
        if args.train_output is None or args.prompt is None:
            parser.error('query requires --train-output and --prompt')
        from tools.nanojev_gamer_v1_prompt_db_query import query
        report=query(args.output,args.train_output,args.prompt)
    else:
        if args.train_output is None or args.prompt is None:
            parser.error('score requires --train-output and --prompt')
        from tools.nanojev_gamer_v1_prompt_db_train import score_checkpoint
        report=score_checkpoint(args.train_output/'strategy_router.npz',args.prompt,
                                report=args.train_output/'final_report.json')
    print('GAMER PROMPT DB SMOKE '+report['status'])
    print(json.dumps(report,indent=2,ensure_ascii=False,sort_keys=True))
    return report

if __name__=='__main__':
    main()
