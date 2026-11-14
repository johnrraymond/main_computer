#!/usr/bin/env python3
"""V10 read-only teacher, paired diagnostics, and checkpoint imports.

No GPU imports at module import time. The teacher is never modified.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Callable

from tools import nanojev_deterministic_brain_v10 as brain
from tools import nanojev_game_math_curriculum_v10_build as builder
from tools import nanojev_game_math_curriculum_v9 as v9
from tools import nanojev_game_transform_experiment as ex

SCHEMA = 'nanojev.game-math.xy-training.grammar-bridge.v10.1'
ORDER_NODES = ('full', 'logbar_x', 'logbar_y', 'no_logbars',
               'x_raw_y_full', 'y_raw_x_full', 'register_x', 'register_y', 'raw')
# T00/T01 start with the known V9 grammar before being bridged to full.
# Higher mathematical tiers retain the established full→raw difficulty order.
# Their reverse-direction grammar bridges remain measured after the lattice;
# the source-operation edge direction is not an assumption about difficulty.
STAGE_ORDER = tuple(f'{tier}/{node}' for tier in brain.TIERS
    for node in ((*brain.BRIDGE_NODES[tier], *ORDER_NODES)
                 if tier in ('T00','T01') else
                 (*ORDER_NODES, *brain.BRIDGE_NODES[tier])))
ALL_NODES = tuple(dict.fromkeys(node for tier in brain.TIERS
    for node in (*brain.BRIDGE_NODES[tier], *ORDER_NODES)))
DEFAULT_TEACHER = Path('runtime/game-math-curriculum-v10-bridge/teacher.sqlite3')
DEFAULT_SOURCE = Path('runtime/game-transform-experiment/source-v4.pt')
DEFAULT_PARENT = Path.home()/'NanoJev/runs/game_math_xy_v9/latest.pt'
DEFAULT_OUTPUT = Path.home()/'NanoJev/runs/game_math_xy_v10_bridge'


def source_hash(path: Path) -> str:
    return builder.filehash(Path(path).resolve())


def stable_seed(*parts: object) -> int:
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(',', ':'), default=str).encode()).digest()
    return int.from_bytes(digest[:8], 'big')


def teacher_preflight(path: Path) -> dict:
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError('V10_TEACHER_NOT_FOUND: '+str(path))
    with closing(sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)) as db:
        manifest = dict(db.execute('SELECT key,value FROM manifest'))
        if manifest.get('schema') != builder.SCHEMA or manifest.get('brain_version') != brain.VERSION:
            raise ValueError('V10_TEACHER_SCHEMA_MISMATCH')
        actual = {row[0] for row in db.execute('SELECT stage_id FROM stages')}
        if actual != set(STAGE_ORDER):
            raise ValueError('V10_TEACHER_STAGE_GRAPH_MISMATCH')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('V10_TEACHER_FOREIGN_KEY_FAILURE')
        splits = dict(db.execute('SELECT split,COUNT(*) FROM cases GROUP BY split'))
    return {'teacher_sha256': source_hash(path), 'splits': splits, 'stages': len(STAGE_ORDER)}


def read_cases(path: Path, split: str, *, limit: int | None = None) -> list[dict]:
    if split not in builder.SPLITS:
        raise ValueError('INVALID_V10_SPLIT')
    if limit is not None and limit < 1:
        raise ValueError('INVALID_CASE_LIMIT')
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with closing(sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)) as db:
        manifest = dict(db.execute('SELECT key,value FROM manifest'))
        if manifest.get('schema') != builder.SCHEMA:
            raise ValueError('V10_TEACHER_SCHEMA_MISMATCH')
        rows = db.execute('''SELECT c.case_id,c.source_case_id,c.source_instance_id,
            c.game_id,c.objective,c.answer_json,s.stage_id,p.prompt
            FROM cases c JOIN presentations p ON p.case_id=c.case_id
            JOIN stages s ON s.stage_id=p.stage_id
            WHERE c.split=? ORDER BY c.case_id,s.stage_id''', (split,))
        collected = {}
        for cid, source, instance, game, objective, answer, stage, prompt in rows:
            if cid not in collected:
                a = json.loads(answer)
                collected[cid] = {'id': cid, 'source_id': source, 'instance': instance,
                                  'game': game, 'objective': objective, 'answer': a,
                                  'x': v9.number(a['X']), 'y': v9.number(a['Y']),
                                  'op': a['OP'], 'prompts': {}}
            collected[cid]['prompts'][stage] = prompt
    records = list(collected.values())
    for row in records:
        if set(row['prompts']) != set(STAGE_ORDER):
            raise ValueError('V10_CASE_INCOMPLETE_STAGE_SET:'+row['id'])
    if limit is None:
        return records
    # Exact same case selection for every representation; balance operators first.
    buckets: dict[tuple, list] = defaultdict(list)
    for row in records:
        buckets[(row['op'], row['game'], row['objective'])].append(row)
    selected = []
    while len(selected) < limit and buckets:
        for key in sorted(tuple(buckets)):
            if len(selected) >= limit:
                break
            selected.append(buckets[key].pop(0))
            if not buckets[key]:
                del buckets[key]
    return selected


def stage_list(tiers: tuple[str, ...] | list[str] | None = None,
               nodes: tuple[str, ...] | list[str] | None = None) -> list[str]:
    if tiers is not None and any(t not in brain.TIERS for t in tiers):
        raise ValueError('UNKNOWN_V10_TIER')
    if nodes is not None and any(n not in ALL_NODES for n in nodes):
        raise ValueError('UNKNOWN_V10_ASSISTANCE_NODE')
    return [s for s in STAGE_ORDER if (tiers is None or s.split('/')[0] in tiers)
            and (nodes is None or s.split('/')[1] in nodes)]


def token_eligible(model: dict, prompt: str) -> bool:
    ids = model['tokenizer'](prompt, add_special_tokens=False)['input_ids']
    return 4 <= len(ids) <= model['config']['max_prompt_tokens']


def batch_predict(model: dict, prompts: list[str]) -> list[tuple[float, float]]:
    """Batched inference using the existing frozen-backbone V9 math head."""
    import torch
    with torch.inference_mode():
        outputs, _ = ex.forward(model, prompts)
        values = model['v3'].predict(outputs, model['base'], 'attention').detach().cpu().tolist()
    return [(float(v[0]), float(v[1])) for v in values]


def _record(row: dict, stage: str, result: tuple[float,float] | None, status: str,
            error: str | None = None) -> dict:
    out = {'case_id': row['id'], 'stage': stage, 'game': row['game'],
           'objective': row['objective'], 'OP': row['op'], 'status': status,
           'true_x': row['x'], 'true_y': row['y']}
    if error:
        out['error'] = error
    if status == 'ok' and result is not None:
        x, y = result
        if math.isfinite(x) and math.isfinite(y):
            out.update(pred_x=x, pred_y=y, x_ok=v9.within(x,row['x']),
                       y_ok=v9.within(y,row['y']))
            out['joint_ok'] = out['x_ok'] and out['y_ok']
            out['coupled_Z'] = v9.coupled_z(x,y,row['op'],true_z=row['answer']['Z'])
        else:
            out['status'] = 'nonfinite_prediction'
    return out


def metrics(records: list[dict]) -> dict:
    n = len(records)
    scorable = [r for r in records if r['status']=='ok' and 'joint_ok' in r]
    return {'n':n, 'scorable':len(scorable),
            'x':sum(bool(r['x_ok']) for r in scorable)/n if n else 0.0,
            'y':sum(bool(r['y_ok']) for r in scorable)/n if n else 0.0,
            'joint_xy':sum(bool(r['joint_ok']) for r in scorable)/n if n else 0.0,
            'coupled_Z':sum(bool(r['coupled_Z'].get('within_5pct')) for r in scorable)/n if n else 0.0,
            'invalid':dict(sorted(Counter(r['status'] for r in records if r['status']!='ok').items()))}


def passed(measure: dict, minimum: float=.80, min_count: int=16) -> bool:
    return bool(measure['n']>=min_count and measure['scorable']==measure['n'] and
                measure['joint_xy']>=minimum-1e-12 and
                measure['x']>=minimum-1e-12 and measure['y']>=minimum-1e-12)


def first_failing(scores: dict, *, minimum: float=.80, min_count: int=16) -> str | None:
    for stage in STAGE_ORDER:
        if stage not in scores or not passed(scores[stage],minimum,min_count):
            return stage
    return None


def paired_edges(results: dict[str,list[dict]], stages: list[str]) -> dict:
    """Compare same held-out case across each atomic presentation mutation."""
    chosen = set(stages)
    indexed = {stage:{r['case_id']:r for r in results[stage]} for stage in stages}
    output = {}
    for tier in brain.TIERS:
        for parent, child, mutation in (*brain.bridge_edges(tier), *brain.EDGES):
            a, b = f'{tier}/{parent}', f'{tier}/{child}'
            if a not in chosen or b not in chosen:
                continue
            transitions = Counter()
            for cid, before in indexed[a].items():
                after = indexed[b][cid]
                if before['status']!='ok' or after['status']!='ok':
                    transitions['unscorable'] += 1
                    continue
                for field in ('x_ok','y_ok','joint_ok'):
                    initial, final = before[field], after[field]
                    if initial and not final:
                        transitions[field+'_regressed'] += 1
                    if not initial and final:
                        transitions[field+'_improved'] += 1
                    if initial and final:
                        transitions[field+'_retained'] += 1
            output[a+' -> '+b] = {'mutation':mutation, 'cases':len(indexed[a]),
                                     **dict(sorted(transitions.items()))}
    return output


def evaluate(rows: list[dict], stages: list[str], predict: Callable[[list[str]],list[tuple[float,float]]],
             token_ok: Callable[[str],bool], *, batch_size: int=4,
             gate_fraction: float=.80, min_count: int=16, report_cases: int=5) -> dict:
    if batch_size < 1 or not 0 < gate_fraction <= 1:
        raise ValueError('INVALID_V10_PROBE_OPTIONS')
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError('V10_DUPLICATE_PROBE_CASE')
    records = {stage: [] for stage in stages}
    for stage in stages:
        pending = []
        for row in rows:
            prompt = row['prompts'][stage]
            if not token_ok(prompt):
                records[stage].append(_record(row,stage,None,'unscorable_token_limit'))
            else:
                pending.append(row)
        for i in range(0,len(pending),batch_size):
            batch = pending[i:i+batch_size]
            try:
                answers = predict([r['prompts'][stage] for r in batch])
                if len(answers) != len(batch):
                    raise RuntimeError('V10_PREDICTION_BATCH_LENGTH_MISMATCH')
                for row, xy in zip(batch,answers):
                    records[stage].append(_record(row,stage,xy,'ok'))
            except (RuntimeError,ValueError,TypeError,ArithmeticError,OverflowError) as error:
                # Explicit inference failure, never silently exclude a failed case.
                for row in batch:
                    records[stage].append(_record(row,stage,None,'inference_error',type(error).__name__))
        records[stage].sort(key=lambda x:x['case_id'])
    scores={stage:metrics(records[stage]) for stage in stages}
    perop={stage:{op:metrics([r for r in records[stage] if r['OP']==op])
                  for op in sorted({r['OP'] for r in records[stage]})}
           for stage in stages}
    summary={
        'schema':SCHEMA, 'cases':len(rows), 'stages_evaluated':len(stages),
        'tolerance':.05, 'gate_fraction':gate_fraction, 'min_count':min_count,
        'scores':scores,'by_operation':perop,
        'first_failing':(first_failing(scores,minimum=gate_fraction,min_count=min_count)
                         if set(stages)==set(STAGE_ORDER) else None),
        'first_failing_in_selection':next((s for s in stages
                 if not passed(scores[s],gate_fraction,min_count)),None),
        'all_stages_probed':set(stages)==set(STAGE_ORDER),
        'paired_edges':paired_edges(records,stages),
        'examples':{stage:[r for r in records[stage] if not r.get('joint_ok',False)][:report_cases]
                    for stage in stages},
    }
    return summary


def load_model(source_checkpoint: Path, weights_checkpoint: Path, *, device: str='cuda',
               dtype: str='float16', seed: int=20261017, allow_model_download: bool=False,
               expected_teacher_sha: str | None=None) -> tuple[dict,dict,str]:
    """Import V9 or V2.1 checkpoint, or a saved V10 checkpoint."""
    import torch
    source = Path(source_checkpoint).resolve()
    cp_path = Path(weights_checkpoint).resolve()
    if not cp_path.is_file():
        raise FileNotFoundError('V10_PARENT_CHECKPOINT_MISSING: '+str(cp_path))
    if not source.is_file():
        raise FileNotFoundError('V10_SOURCE_CHECKPOINT_MISSING: '+str(source))
    cp=torch.load(cp_path,map_location='cpu',weights_only=True)
    kind=cp.get('schema','')
    if kind == v9.SCHEMA:
        if cp.get('config',{}).get('source_sha256')!=source_hash(source):
            raise ValueError('V10_V9_SOURCE_PROVENANCE_MISMATCH')
    elif kind == SCHEMA:
        config=cp.get('config',{})
        if config.get('source_sha256')!=source_hash(source):
            raise ValueError('V10_SOURCE_PROVENANCE_MISMATCH')
        if expected_teacher_sha and config.get('teacher_sha256')!=expected_teacher_sha:
            raise ValueError('V10_TEACHER_PROVENANCE_MISMATCH')
    elif cp.get('source_sha256')!=source_hash(source):
        raise ValueError('V10_UNRECOGNIZED_PARENT_CHECKPOINT_OR_SOURCE_MISMATCH')
    model=ex.load_current(source,device,dtype,allow_model_download,seed)
    model['decoder'].load_state_dict(cp['decoder'],strict=True)
    model['gamehead'].load_state_dict(cp['game_operator_head'],strict=True)
    model['decoder'].eval();model['gamehead'].eval()
    return model,cp,source_hash(cp_path)


def save_json(path: Path, obj: dict) -> None:
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(obj,indent=2,sort_keys=True,allow_nan=False),encoding='utf-8')
    temp.replace(path)
