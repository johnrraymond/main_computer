#!/usr/bin/env python3
"""Gamer v1 LoRA -> existing C0 Wide/to_numerical handoff.

Both learned tasks consume the SAME original *08 prompt. The human-readable
mathematical table is ONLY LoRA completion supervision; it is never encoded by
or passed to the C0 Wide head. The data contract requires upstream oracle and
factor-table certification. No production model activation is performed here.

Usage:
 python -m tools.nanojev_gamer_v1_handoff prepare --output RUN
 python -m tools.nanojev_gamer_v1_handoff validate --output RUN
 python -m tools.nanojev_gamer_v1_handoff lora --dataset CASES.jsonl --output RUN --device cuda
 python -m tools.nanojev_gamer_v1_handoff switch --dataset CASES.jsonl --output RUN
 python -m tools.nanojev_gamer_v1_handoff to-numerical --dataset CASES.jsonl --output RUN --device cuda
 python -m tools.nanojev_gamer_v1_handoff probe --dataset CASES.jsonl --output RUN --device cuda
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from collections import Counter, defaultdict
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import tempfile
import time

SCHEMA = 'nanojev.gamer.v1.original-prompt-shared-handoff.v1'
PAIR_SCHEMA = 'nanojev.gamer.v1.oracle-table-pair.v1'
SPLITS = ('train', 'dev', 'test')
FIELDS = ('game_id', 'prompt_08', 'math_prompt', 'answer', 'split', 'oracle_verified', 'table_verified')


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as file:
        file.write(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n')
        temp = Path(file.name)
    os.replace(temp, path)


def registry_ids(*, include_catalog_extensions=False, include_transition_games=False):
    # The base 33 remain the default. Only the 58 explicit, restricted,
    # certified catalog objectives can be opted into; the rest remain blocked.
    from tools.nanojev_game_transformation_library import MATRICES, SCALAR_MODELS
    games = set(MATRICES) | set(SCALAR_MODELS)
    if len(games) != 33:
        raise ValueError('GAMER_REGISTRY_NOT_33')
    if include_catalog_extensions or include_transition_games:
        from tools.nanojev_gamer_v1_catalog_extensions import EXTRA_IDS
        if len(EXTRA_IDS) != 58 or games & EXTRA_IDS:
            raise ValueError('GAMER_BOUNDED_EXTENSION_REGISTRY_INVALID')
        games.update(EXTRA_IDS)
    if include_transition_games:
        from tools.nanojev_gamer_v1_transition_games import TRANSITION_IDS
        if len(TRANSITION_IDS)!=55 or games & TRANSITION_IDS:
            raise ValueError('GAMER_TRANSITION_REGISTRY_INVALID')
        games.update(TRANSITION_IDS)
    return games


def original_prompt(row):
    """The ONLY model-side input for both phases. No math/table fallback."""
    return row['prompt_08']


def original_prompt_fingerprint(row):
    return sha_bytes(original_prompt(row).encode('utf-8'))


def load_cases(path, *, allow_partial=False, include_catalog_extensions=False, include_transition_games=False):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'GAMER_DATASET_MISSING: {path}\nRun: python -m tools.nanojev_gamer_v1_handoff prepare --output {path.parent}')
    raw = path.read_bytes()
    cases = []
    seen = set()
    games = registry_ids(include_catalog_extensions=include_catalog_extensions,
                         include_transition_games=include_transition_games)
    from tools.nanojev_gamer_v1_pairs import certify, SCHEMA as GENERATED_SCHEMA, EXTENSION_SCHEMA, TRANSITION_SCHEMA
    provenance_classes=set()
    for index, text in enumerate(raw.decode('utf-8').splitlines(), 1):
        if not text.strip():
            continue
        try:
            row = json.loads(text)
        except ValueError as e:
            raise ValueError(f'GAMER_INVALID_JSONL_LINE_{index}') from e
        if not isinstance(row, dict) or row.get('schema') != PAIR_SCHEMA:
            raise ValueError(f'GAMER_BAD_PAIR_SCHEMA_LINE_{index}')
        if any(k not in row for k in FIELDS):
            raise ValueError(f'GAMER_MISSING_FIELDS_LINE_{index}')
        if row.get('blind_matrix') is True or row.get('blind_matrix_regime') is not None:
            raise ValueError('GAMER_BLIND_MATRIX_RETIRED: generate a fresh named-game dataset')
        if row['game_id'] not in games:
            raise ValueError(f'GAMER_UNVERIFIED_GAME_LINE_{index}: {row["game_id"]}')
        if row['split'] not in SPLITS:
            raise ValueError(f'GAMER_BAD_SPLIT_LINE_{index}')
        if not isinstance(row['prompt_08'], str) or not row['prompt_08'].strip():
            raise ValueError(f'GAMER_EMPTY_ORIGINAL_PROMPT_LINE_{index}')
        if not isinstance(row['math_prompt'], str) or not row['math_prompt'].strip():
            raise ValueError(f'GAMER_EMPTY_TABLE_TARGET_LINE_{index}')
        if row['oracle_verified'] is not True or row['table_verified'] is not True:
            raise ValueError(f'GAMER_UNCERTIFIED_PAIR_LINE_{index}')
        if row.get('generator_schema') is not None:
            if row['generator_schema'] not in (GENERATED_SCHEMA, EXTENSION_SCHEMA, TRANSITION_SCHEMA):
                raise ValueError(f'GAMER_UNKNOWN_GENERATOR_SCHEMA_LINE_{index}')
            if row['generator_schema']==EXTENSION_SCHEMA and not (include_catalog_extensions or include_transition_games):
                raise ValueError('GAMER_CATALOG_EXTENSIONS_REQUIRE_OPT_IN')
            if row['generator_schema']==TRANSITION_SCHEMA and not include_transition_games:
                raise ValueError('GAMER_TRANSITION_GAMES_REQUIRE_OPT_IN')
            certify(row)
            provenance_classes.add('source_recomputed')
        else:
            provenance_classes.add('self_attested')
        if (not isinstance(row.get('oracle_fingerprint'), str) or len(row['oracle_fingerprint']) != 64
                or any(c not in '0123456789abcdef' for c in row['oracle_fingerprint'])):
            raise ValueError(f'GAMER_MISSING_ORACLE_FINGERPRINT_LINE_{index}')
        if (not isinstance(row.get('table_fingerprint'), str) or len(row['table_fingerprint']) != 64
                or any(c not in '0123456789abcdef' for c in row['table_fingerprint'])):
            raise ValueError(f'GAMER_MISSING_TABLE_FINGERPRINT_LINE_{index}')
        try:
            answer = float(row['answer'])
        except (ValueError, TypeError, OverflowError) as e:
            raise ValueError(f'GAMER_BAD_NUMERICAL_TARGET_LINE_{index}') from e
        if not math.isfinite(answer):
            raise ValueError(f'GAMER_NONFINITE_TARGET_LINE_{index}')
        if 'zero_line_residual' in row:
            try:
                residual=float(row['zero_line_residual'])
            except (TypeError, ValueError, OverflowError) as e:
                raise ValueError(f'GAMER_BAD_ZERO_LINE_RESIDUAL_LINE_{index}') from e
            if not math.isfinite(residual):
                raise ValueError(f'GAMER_BAD_ZERO_LINE_RESIDUAL_LINE_{index}')
        key = original_prompt_fingerprint(row)
        if key in seen:
            raise ValueError(f'GAMER_ORIGINAL_PROMPT_COLLISION_OR_LEAKAGE_LINE_{index}')
        seen.add(key)
        cases.append(row)
    if not cases:
        raise ValueError('GAMER_EMPTY_DATASET')
    if len(provenance_classes)>1:
        raise ValueError('GAMER_MIXED_PROVENANCE_REFUSED')
    by_split = Counter(row['split'] for row in cases)
    if not by_split['train'] or not by_split['dev'] or not by_split['test']:
        raise ValueError('GAMER_REQUIRES_TRAIN_DEV_TEST')
    coverage = sorted({r['game_id'] for r in cases})
    if not allow_partial and len(coverage) != len(games):
        raise ValueError(f'GAMER_COVERAGE_{len(coverage)}_OF_{len(games)}; use --allow-partial-smoke only for local smoke')
    return cases, {
        **({'bounded_catalog_extensions':True,
            'bounded_catalog_extension_count':len({r['game_id'] for r in cases if r.get('bounded_catalog_objective')}),
            'oracle_scope':('EXPLICIT_BOUNDED_OBJECTIVES_NOT_FULL_GAMES' if include_transition_games else 'EXPLICIT_ONE_STEP_OBJECTIVES_NOT_FULL_GAME')}
           if include_catalog_extensions or include_transition_games else {}),
        **({'bounded_transition_games':True,
            'bounded_transition_game_count':len({r['game_id'] for r in cases if r.get('bounded_transition_objective')}),
            'fourth_table_family':'state_transition'} if include_transition_games else {}),
        'schema': SCHEMA, 'dataset_sha256': sha_bytes(raw),
        'original_prompt_corpus_sha256': sha_bytes(canonical(sorted(seen)).encode('utf-8')),
        'number_of_cases': len(cases), 'number_of_verified_games': len(coverage),
        'game_ids': coverage, 'by_split': dict(by_split),
        'oracle_verified': True, 'table_verified': True,
        'provenance':next(iter(provenance_classes)),
    }


def ensure_manifest(output, dataset, model, allow_partial, *, include_catalog_extensions=False, include_transition_games=False):
    output = Path(output)
    cases, observed = load_cases(dataset, allow_partial=allow_partial,
                                 include_catalog_extensions=include_catalog_extensions,
                                 include_transition_games=include_transition_games)
    observed.update({'model': model, 'allow_partial_smoke': bool(allow_partial)})
    path = output / 'handoff_manifest.json'
    if path.exists():
        expected = json.loads(path.read_text(encoding='utf-8'))
        if expected != observed:
            raise ValueError('GAMER_HANDOFF_LINEAGE_CHANGED: use a new --output; never repoint trained state')
    else:
        atomic_json(path, observed)
    return cases, observed


def by_split(cases, split):
    return [row for row in cases if row['split'] == split]


def tokenize_completion(tok, prompt, completion, max_length):
    """Mask original prompt tokens; teach only math-table completion tokens."""
    prefix = tok.encode(prompt, add_special_tokens=True)
    suffix = tok.encode('\n' + completion, add_special_tokens=False)
    if tok.eos_token_id is not None:
        suffix.append(tok.eos_token_id)
    if not suffix or len(prefix) + len(suffix) > max_length:
        raise ValueError('GAMER_LORA_PAIR_EXCEEDS_MAX_LENGTH; never truncate math target')
    return {'input_ids': prefix + suffix, 'labels': [-100]*len(prefix) + suffix}


def collate(torch, tok, items, device):
    longest = max(len(it['input_ids']) for it in items)
    pad = tok.pad_token_id
    if pad is None:
        raise ValueError('GAMER_TOKENIZER_HAS_NO_PAD')
    return {
        'input_ids': torch.tensor([it['input_ids']+[pad]*(longest-len(it['input_ids'])) for it in items], dtype=torch.long, device=device),
        'labels': torch.tensor([it['labels']+[-100]*(longest-len(it['labels'])) for it in items], dtype=torch.long, device=device),
        'attention_mask': torch.tensor([[1]*len(it['input_ids'])+[0]*(longest-len(it['input_ids'])) for it in items], dtype=torch.long, device=device),
    }


def model_components(model_id, device, adapter_dir=None):
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    if str(device).startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('GAMER_CUDA_UNAVAILABLE')
    tok = AutoTokenizer.from_pretrained(model_id, trust_remote_code=False)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = 'right'
    kw = {'trust_remote_code': False, 'dtype': torch.float16 if str(device).startswith('cuda') else torch.float32}
    model = AutoModelForCausalLM.from_pretrained(model_id, **kw).to(device)
    if adapter_dir is not None:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, str(adapter_dir), is_trainable=False)
        for p in model.parameters():
            p.requires_grad_(False)
        model.eval()
    return torch, tok, model


def evaluate_lora(torch, adapter, tok, dev, *, device, max_length, max_new_tokens,
                  progress_path=None, progress_key=None, target_bounded=False):
    """Greedy evaluation with durable per-prompt progress when a path is supplied.

    Each saved match belongs to an exact ordered dev deck, model phase, and
    checkpoint fingerprint. A restart never reuses results for other weights.
    """
    adapter.eval()
    total = len(dev)
    deck_sha = sha_bytes(canonical([
        (r['game_id'], original_prompt(r), r['math_prompt']) for r in dev
    ]).encode('utf-8'))
    identity = {'schema': 'nanojev.gamer.lora.eval-progress.v1',
                'key': progress_key, 'deck_sha256': deck_sha,
                'max_length': max_length, 'max_new_tokens': max_new_tokens}
    if target_bounded:
        identity['target_bounded'] = True
    matches = []
    if progress_path is not None:
        if progress_key is None:
            raise ValueError('GAMER_LORA_EVAL_PROGRESS_REQUIRES_IDENTITY')
        progress_path = Path(progress_path)
        if progress_path.exists():
            stored = json.loads(progress_path.read_text(encoding='utf-8'))
            if {k: stored.get(k) for k in identity} != identity:
                raise ValueError('GAMER_LORA_EVAL_PROGRESS_LINEAGE_CHANGED')
            matches = stored.get('matches')
            if (not isinstance(matches, list) or len(matches) > total
                    or any(type(x) is not bool for x in matches)):
                raise ValueError('GAMER_LORA_EVAL_PROGRESS_INVALID')
            print(f'GAMER LoRA eval: RESUME {len(matches)}/{total} '
                  'verified development prompts; saved results preserved.', flush=True)
        else:
            # Create the resume marker BEFORE prompt 1. An interruption even
            # during that first long generation is restartable with --resume.
            atomic_json(progress_path, {**identity, 'matches': []})
    strict = sum(matches)
    by_game = defaultdict(lambda: [0, 0])
    by_regime = defaultdict(lambda: [0, 0])
    for row, matched in zip(dev, matches):
        by_game[row['game_id']][0] += int(matched)
        by_game[row['game_id']][1] += 1
        if row.get('blind_matrix'):
            by_regime[row['blind_matrix_regime']][0] += int(matched)
            by_regime[row['blind_matrix_regime']][1] += 1
    started = time.monotonic()
    print(f'GAMER LoRA evaluation: beginning {total} development prompts; '
          f'up to {max_new_tokens} generated tokens per prompt; '
          'completed prompts are checkpointed individually.', flush=True)
    with torch.inference_mode():
        for index in range(len(matches), total):
            row = dev[index]
            ordinal = index + 1
            print(f'GAMER LoRA eval: START {ordinal}/{total} '
                  f'game={row["game_id"]} elapsed={time.monotonic()-started:.1f}s',flush=True)
            enc = tok(original_prompt(row), truncation=False, return_tensors='pt')
            if enc['input_ids'].shape[1] > max_length:
                raise ValueError('GAMER_DEV_PROMPT_TOO_LONG')
            inputs = {k:v.to(device) for k,v in enc.items()}
            generation_limit = max_new_tokens
            if target_bounded:
                required = len(tok.encode('\n' + row['math_prompt'], add_special_tokens=False)) + 8
                if required > max_new_tokens:
                    raise ValueError('GAMER_PROBE_TARGET_EXCEEDS_MAX_NEW_TOKENS: increase --max-new-tokens')
                generation_limit = required
            generated = adapter.generate(**inputs, max_new_tokens=generation_limit, do_sample=False,
                                         pad_token_id=tok.pad_token_id, eos_token_id=tok.eos_token_id)
            text = tok.decode(generated[0, inputs['input_ids'].shape[-1]:], skip_special_tokens=True).strip()
            matched = text == row['math_prompt'].strip()
            strict += int(matched)
            by_game[row['game_id']][0] += int(matched)
            by_game[row['game_id']][1] += 1
            if row.get('blind_matrix'):
                by_regime[row['blind_matrix_regime']][0] += int(matched)
                by_regime[row['blind_matrix_regime']][1] += 1
            matches.append(bool(matched))
            if progress_path is not None:
                atomic_json(progress_path, {**identity, 'matches': matches})
            print(f'GAMER LoRA eval: DONE {ordinal}/{total} '
                  f'exact={strict}/{ordinal} elapsed={time.monotonic()-started:.1f}s',flush=True)
    print(f'GAMER LoRA evaluation complete: exact={strict}/{total} '
          f'elapsed={time.monotonic()-started:.1f}s',flush=True)
    return {'strict_exact': strict/total, 'exact': strict, 'total': total,
            'by_game': {g:{'exact':v[0], 'total':v[1]} for g,v in sorted(by_game.items())},
            'by_blind_regime': {g:{'exact':v[0], 'total':v[1]} for g,v in sorted(by_regime.items())},
            'matches': matches}



def fast_probe_deck(dev, *, budget, evaluation_index):
    """Fixed anchor for comparable promotion + rotating, non-leaking coverage.

    Every chosen case comes from the certified development split.  The anchor
    is identical at every evaluation; the rotation covers other named games
    and anonymous matrix regimes across successive evaluations.  A short
    probe is never described as full-game retention certification.
    """
    if budget < 4:
        raise ValueError('GAMER_FAST_PROBE_REQUIRES_AT_LEAST_FOUR_CASES')
    named = {}
    blind = {}
    for row in dev:
        group = blind if row.get('blind_matrix') else named
        key = row['blind_matrix_regime'] if row.get('blind_matrix') else row['game_id']
        group.setdefault(key, row)
    named_rows = [named[k] for k in sorted(named)]
    blind_rows = [blind[k] for k in sorted(blind)]
    if not named_rows:
        raise ValueError('GAMER_FAST_PROBE_REQUIRES_NAMED_DEV_CASES')
    if any(row.get('bounded_transition_objective') for row in dev):
        # Fourth-table curriculum: fixed apples-to-apples anchors from each
        # provenance class; rotations revisit all three, never just the first
        # alphabetic game names. No held-out case is trained on.
        groups={'native':[], 'catalog':[], 'transition':[]}
        for row in named_rows:
            group=('transition' if row.get('bounded_transition_objective') else
                   ('catalog' if row.get('bounded_catalog_objective') else 'native'))
            groups[group].append(row)
        if any(not rows for rows in groups.values()):
            raise ValueError('GAMER_TRANSITION_PROBE_MISSING_PROVENANCE_GROUP')
        if budget < 6:raise ValueError('GAMER_TRANSITION_PROBE_REQUIRES_SIX_CASES')
        anchor_each=max(1,min(2,budget//6))
        anchors=[];rotations=[]
        for name in ('native','catalog','transition'):
            anchors.extend(groups[name][:anchor_each])
        available=budget-len(anchors)
        # Distribute available cases approximately equally, with a round-robin
        # accounting of the remainder to avoid systematic under-probing.
        counts={name:available//3 for name in groups}
        for name in ('transition','native','catalog')[:available%3]:counts[name]+=1
        for name in ('native','catalog','transition'):
            remaining=groups[name][anchor_each:]
            wanted=min(counts[name],len(remaining))
            start=(evaluation_index*wanted)%len(remaining) if remaining else 0
            rotations.extend(remaining[(start+i)%len(remaining)] for i in range(wanted))
        return anchors+rotations,len(anchors)
    if not blind_rows:
        # New v11 curriculum: fixed named anchors plus rotating named cases.
        # No unknown-game or anonymous-matrix generation.
        anchor_count=min(len(named_rows),max(1,budget//3))
        fixed=named_rows[:anchor_count]
        remaining=named_rows[anchor_count:]
        rotate_count=min(budget-len(fixed),len(remaining))
        begin=(evaluation_index*rotate_count)%len(remaining) if remaining else 0
        rotated=[remaining[(begin+i)%len(remaining)] for i in range(rotate_count)]
        return fixed+rotated,len(fixed)
    anchor_named = min(len(named_rows), max(1, budget // 4))
    anchor_blind = min(len(blind_rows), max(1, budget // 8))
    if anchor_named + anchor_blind >= budget:
        raise ValueError('GAMER_FAST_PROBE_HAS_NO_ROTATION_BUDGET')
    def spread(rows, count):
        indices = [i * len(rows) // count for i in range(count)]
        picked = [rows[i] for i in indices]
        remainder = [row for i,row in enumerate(rows) if i not in indices]
        return picked, remainder
    fixed_named, remaining_named = spread(named_rows, anchor_named)
    fixed_blind, remaining_blind = spread(blind_rows, anchor_blind)
    remaining_budget = budget - anchor_named - anchor_blind
    rotate_named_count = min(len(remaining_named),
                             max(1, round(remaining_budget * 0.73)))
    rotate_blind_count = min(len(remaining_blind), remaining_budget - rotate_named_count)
    if rotate_named_count + rotate_blind_count < remaining_budget:
        rotate_named_count = min(len(remaining_named), remaining_budget - rotate_blind_count)
    def rotation(rows, count):
        if not rows or not count:return []
        start = (evaluation_index * count) % len(rows)
        return [rows[(start+i)%len(rows)] for i in range(count)]
    anchors = fixed_named + fixed_blind
    rotating = rotation(remaining_named, rotate_named_count) + rotation(remaining_blind, rotate_blind_count)
    return anchors + rotating, len(anchors)


def parent_lora_identity(args, torch):
    """Verify the source adapter; optionally import its exact latest LoRA tensors.

    Never import optimizer state across a different certified dataset.
    """
    source = Path(args.init_lora_from).resolve()
    destination = Path(args.output).resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('GAMER_INIT_ADAPTER_MUST_BE_SEPARATE_RUN')
    manifest_path = source/'handoff_manifest.json'
    selection=getattr(args,'init_lora_checkpoint','best')
    if selection not in ('best','latest'):
        raise ValueError('GAMER_INIT_CHECKPOINT_MUST_BE_BEST_OR_LATEST')
    folder = source/'lora'/'best_adapter'
    if not manifest_path.is_file() or not folder.is_dir():
        raise FileNotFoundError('GAMER_INIT_REQUIRES_SOURCE_MANIFEST_AND_BEST_ADAPTER')
    old_manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if (old_manifest.get('schema') != SCHEMA or old_manifest.get('model') != args.model
            or old_manifest.get('provenance') != 'source_recomputed'):
        raise ValueError('GAMER_INIT_SOURCE_MODEL_OR_MANIFEST_MISMATCH')
    fingerprint = file_tree_sha256(folder)
    adapter_config_path = folder/'adapter_config.json'
    if not adapter_config_path.is_file():
        raise FileNotFoundError('GAMER_INIT_PEFT_ADAPTER_CONFIG_MISSING')
    cfg = json.loads(adapter_config_path.read_text(encoding='utf-8'))
    if cfg.get('r') != args.lora_rank or cfg.get('task_type') != 'CAUSAL_LM':
        raise ValueError('GAMER_INIT_ADAPTER_RANK_OR_TASK_MISMATCH')
    base_id = cfg.get('base_model_name_or_path')
    if base_id and base_id != args.model:
        raise ValueError('GAMER_INIT_ADAPTER_BASE_MODEL_MISMATCH')
    old_report_path = source/'lora'/'report.json'
    if old_report_path.is_file():
        report = json.loads(old_report_path.read_text(encoding='utf-8'))
        if (report.get('model')!=args.model or
            report.get('dataset_sha256')!=old_manifest.get('dataset_sha256') or
            report.get('best_adapter_sha256')!=fingerprint):
            raise ValueError('GAMER_INIT_SOURCE_REPORT_OR_ADAPTER_CHANGED')
        step = report.get('best_step')
        if not isinstance(step,int) or step < 1:
            raise ValueError('GAMER_INIT_SOURCE_FINAL_REPORT_HAS_NO_BEST_STEP')
        verified = 'final_report'
    else:
        latest_path=source/'lora'/'latest.pt'
        if not latest_path.is_file():
            raise FileNotFoundError('GAMER_INIT_UNFINALIZED_SOURCE_NEEDS_LATEST_CHECKPOINT')
        state=torch.load(latest_path,map_location='cpu',weights_only=True)
        if (state.get('schema') != SCHEMA or state.get('phase') != 'lora' or
            state.get('dataset_sha256') != old_manifest.get('dataset_sha256') or
            not isinstance(state.get('best_step'),int) or state['best_step'] < 1 or
            state.get('best_eval') is None):
            raise ValueError('GAMER_INIT_UNFINALIZED_SOURCE_NOT_VERIFIABLE')
        step = state['best_step']
        verified = 'durable_best_checkpoint_not_finalized'
    identity={'source_run':str(source), 'source_dataset_sha256':old_manifest['dataset_sha256'],
              'adapter_sha256':fingerprint, 'source_best_step':step,
              'source_evidence':verified, 'checkpoint_kind':selection}
    if selection == 'latest':
        latest_path=source/'lora'/'latest.pt'
        if not latest_path.is_file():
            raise FileNotFoundError('GAMER_INIT_LATEST_CHECKPOINT_MISSING')
        state=torch.load(latest_path,map_location='cpu',weights_only=True)
        if (state.get('schema')!=SCHEMA or state.get('phase')!='lora' or
            state.get('dataset_sha256')!=old_manifest['dataset_sha256'] or
            state.get('original_prompt_corpus_sha256')!=old_manifest.get('original_prompt_corpus_sha256') or
            state.get('hyperparameters',{}).get('model')!=args.model or
            state.get('hyperparameters',{}).get('lora_rank')!=args.lora_rank or
            not isinstance(state.get('step'),int) or state['step']<1 or
            not isinstance(state.get('adapter'),dict) or not state['adapter'] or
            not all(isinstance(v,torch.Tensor) for v in state['adapter'].values())):
            raise ValueError('GAMER_INIT_LATEST_CHECKPOINT_LINEAGE_INVALID')
        if state.get('pending_eval'):
            raise ValueError('GAMER_INIT_LATEST_EVALUATION_PENDING: finish or resume source first')
        identity.update(source_checkpoint_step=state['step'],
                        checkpoint_sha256=sha_bytes(latest_path.read_bytes()),
                        source_evidence='exact_latest_checkpoint_weights')
    return identity


def lora_eval_key(args, manifest, *, step, checkpoint_sha256=None):
    """Keep evaluation progress bound to an exact training state."""
    return sha_bytes(canonical({
        'dataset_sha256': manifest['dataset_sha256'],
        'original_prompt_corpus_sha256': manifest['original_prompt_corpus_sha256'],
        'hyperparameters': frozen_hyperparameters(args, 'lora'),
        'step': step, 'checkpoint_sha256': checkpoint_sha256,
    }).encode('utf-8'))


def file_tree_sha256(folder):
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError('GAMER_ADAPTER_NOT_FOUND: ' + str(folder))
    paths = sorted(p for p in folder.rglob('*') if p.is_file())
    if not paths:
        raise ValueError('GAMER_EMPTY_ADAPTER_DIRECTORY')
    digest = hashlib.sha256()
    for file in paths:
        rel = file.relative_to(folder).as_posix()
        digest.update(rel.encode('utf-8') + b'\0')
        with file.open('rb') as f:
            for block in iter(lambda: f.read(1024*1024), b''):
                digest.update(block)
    return digest.hexdigest()


def set_seeds(seed):
    import torch
    import numpy as np
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def frozen_hyperparameters(args, phase):
    if phase=='lora':
        keys=('model','seed','max_length','max_new_tokens','lora_rank','lora_batch',
              'lora_accum','lora_lr','lora_eval_every')
    elif phase=='to_numerical':
        keys=('model','seed','max_length','capture_batch','eval_batch','head_batch',
              'head_eval_every','head_lr','weight_decay','aux_weight',
              'huber_beta','grad_clip')
    else:
        raise ValueError('GAMER_UNKNOWN_PHASE')
    result = {key:getattr(args,key,None) for key in keys}
    if phase=='lora' and getattr(args,'init_lora_from',None):
        result['init_lora_from']=str(Path(args.init_lora_from).resolve())
        result['lora_probe_cases']=getattr(args,'lora_probe_cases',17)
        if getattr(args,'init_lora_checkpoint','best')!='best':
            result['init_lora_checkpoint']=args.init_lora_checkpoint
    if phase=='to_numerical' and getattr(args,'init_head_from',None):
        result['init_head_from']=str(Path(args.init_head_from).resolve())
    return result


def save_training_state(torch,path,data):
    # Tensor and plain-scalar objects only; compatible with weights_only=True.
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as file:
        temp=Path(file.name)
    try:
        torch.save(data,temp)
        os.replace(temp,path)
    finally:
        if temp.exists():temp.unlink()


def training_rng(torch, python_rng=None, numpy_rng=None):
    info={'torch_cpu':torch.get_rng_state()}
    if torch.cuda.is_available():
        info['torch_cuda']=torch.cuda.get_rng_state_all()
    if python_rng is not None:
        info['python']=python_rng.getstate()
    if numpy_rng is not None:
        info['numpy']=numpy_rng.bit_generator.state
    return info


def restore_rng(torch,info,python_rng=None,numpy_rng=None):
    torch.set_rng_state(info['torch_cpu'])
    if 'torch_cuda' in info:
        if not torch.cuda.is_available():
            raise ValueError('GAMER_CUDA_RESUME_DEVICE_UNAVAILABLE')
        torch.cuda.set_rng_state_all(info['torch_cuda'])
    if python_rng is not None:
        python_rng.setstate(info['python'])
    if numpy_rng is not None:
        numpy_rng.bit_generator.state=info['numpy']


def load_training_state(torch,path,*,phase,manifest,args):
    state=torch.load(path,map_location='cpu',weights_only=True)
    if (state.get('schema')!=SCHEMA or state.get('phase')!=phase
        or state.get('dataset_sha256')!=manifest['dataset_sha256']
        or state.get('original_prompt_corpus_sha256')!=manifest['original_prompt_corpus_sha256']
        or state.get('hyperparameters')!=frozen_hyperparameters(args,phase)):
        raise ValueError('GAMER_RESUME_LINEAGE_OR_HYPERPARAMETERS_CHANGED')
    return state


def train_lora(args, cases, manifest):
    if manifest.get('provenance') != 'source_recomputed' and not args.allow_partial_smoke:
        raise ValueError('GAMER_LORA_REQUIRES_SOURCE_RECOMPUTED_ORACLE_TABLE_PAIRS')
    import torch
    from peft import LoraConfig, get_peft_model
    from peft.utils.save_and_load import get_peft_model_state_dict, set_peft_model_state_dict
    if any(row.get('blind_matrix') for row in cases):
        raise ValueError('GAMER_BLIND_MATRIX_RETIRED: prepare a new verified-games-only dataset')
    out = Path(args.output)/'lora'
    latest = out/'latest.pt'
    completed_report=out/'report.json'
    if completed_report.exists():
        if (Path(args.output)/'handoff.json').exists() or (Path(args.output)/'to_numerical'/'report.json').exists():
            raise ValueError('GAMER_EXTENSION_FROZEN_HANDOFF_EXISTS: use a new run to preserve the numerical-head contract')
        if not args.resume or not latest.is_file():
            raise FileExistsError('GAMER_LORA_ALREADY_FINALIZED: use --resume --lora-steps N where N exceeds the completed step')
        # Do not delete any prior work: old final report is archived before
        # extending an already completed run in the SAME dataset/optimizer.
        previous=load_training_state(__import__('torch'),latest,phase='lora',manifest=manifest,args=args)
        if args.lora_steps <= previous['step']:
            raise ValueError(f'GAMER_EXTENSION_REQUIRES_LARGER_LORA_STEPS: completed={previous["step"]}, requested={args.lora_steps}')
        previous_report=json.loads(completed_report.read_text(encoding='utf-8'))
        if (previous_report.get('dataset_sha256')!=manifest['dataset_sha256'] or
            previous_report.get('model')!=args.model):
            raise ValueError('GAMER_EXTENSION_REPORT_LINEAGE_MISMATCH')
        archive=out/'completed_reports'/f'report_step_{previous["step"]:07d}.json'
        if archive.exists():
            if json.loads(archive.read_text(encoding='utf-8'))!=previous_report:
                raise ValueError('GAMER_EXTENSION_ARCHIVED_REPORT_CONFLICT')
        else:
            atomic_json(archive,previous_report)
        completed_report.unlink()
        print(f'GAMER LoRA EXTEND: archived completed report at {archive}; continuing optimizer from step {previous["step"]} to {args.lora_steps}.',flush=True)
    baseline_progress = out/'eval_baseline.json'
    if (latest.exists() or baseline_progress.exists()) and not args.resume:
        raise FileExistsError('GAMER_LORA_INTERRUPTED: explicitly provide --resume')
    if args.resume and not (latest.exists() or baseline_progress.exists()):
        raise FileNotFoundError('GAMER_LORA_NO_RESUME_STATE')
    set_seeds(args.seed)
    # A source is imported only on first initialization. --resume restores
    # this run's own adapter, optimizer and RNG, never the source optimizer.
    bootstrap = (parent_lora_identity(args,torch) if getattr(args,'init_lora_from',None)
                 and not (args.resume and latest.exists()) else None)
    torch, tok, base = model_components(args.model, args.device)
    train, dev = by_split(cases,'train'), by_split(cases,'dev')
    print(f'GAMER LoRA: model loaded; tokenizing {len(train)} training pairs '
          f'and preparing {len(dev)} development cases.',flush=True)
    items = [tokenize_completion(tok, original_prompt(row), row['math_prompt'], args.max_length) for row in train]
    print(f'GAMER LoRA: tokenization complete; {len(items)} pairs.',flush=True)
    config = LoraConfig(r=args.lora_rank, lora_alpha=2*args.lora_rank,
                        lora_dropout=.05, bias='none', task_type='CAUSAL_LM',
                        target_modules=['q_proj','k_proj','v_proj','o_proj'])
    if bootstrap is not None:
        from peft import PeftModel
        adapter = PeftModel.from_pretrained(base,
            str(Path(bootstrap['source_run'])/'lora'/'best_adapter'), is_trainable=True)
        if bootstrap.get('checkpoint_kind')=='latest':
            parent_latest=Path(bootstrap['source_run'])/'lora'/'latest.pt'
            if sha_bytes(parent_latest.read_bytes())!=bootstrap['checkpoint_sha256']:
                raise ValueError('GAMER_INIT_LATEST_CHANGED_DURING_IMPORT')
            parent_state=torch.load(parent_latest,map_location='cpu',weights_only=True)
            set_peft_model_state_dict(adapter,parent_state['adapter'])
            restored=get_peft_model_state_dict(adapter)
            if (restored.keys()!=parent_state['adapter'].keys() or
                any(not torch.equal(restored[k].detach().cpu(),v)
                    for k,v in parent_state['adapter'].items())):
                raise ValueError('GAMER_INIT_LATEST_WEIGHT_TRANSFER_MISMATCH')
            bootstrap_step=bootstrap['source_checkpoint_step']
            if args.lora_steps <= bootstrap_step:
                raise ValueError(f'GAMER_CONTINUATION_STEPS_MUST_EXCEED_PARENT: parent={bootstrap_step}, requested={args.lora_steps}')
            print(f'GAMER LoRA CONTINUATION: exact source optimizer step {bootstrap_step} weights imported; steps {bootstrap_step+1} through {args.lora_steps} use a FRESH optimizer because the dataset changed.',flush=True)
        if not any(p.requires_grad for p in adapter.parameters()):
            raise ValueError('GAMER_INIT_ADAPTER_NOT_TRAINABLE')
        print('GAMER LoRA TRANSFER: verified source run '
              f'{bootstrap["source_run"]}, checkpoint={bootstrap["checkpoint_kind"]} '
              f'sha256={bootstrap.get("checkpoint_sha256",bootstrap["adapter_sha256"])}; fresh optimizer for new dataset.',flush=True)
    else:
        adapter = get_peft_model(base, config)
    adapter.config.use_cache = False
    optimizer = torch.optim.AdamW((p for p in adapter.parameters() if p.requires_grad), lr=args.lora_lr,weight_decay=.01)
    rng = random.Random(args.seed + 883)
    baseline = None
    history = []
    best_score = -1.0
    best_step = 0
    best_eval = None
    start_step = (bootstrap.get('source_checkpoint_step',0) if bootstrap is not None else 0)
    pending_eval = False
    last_step_loss = None
    sampling_focus = {}
    sampling_counts = {'named_replay':0}
    fast_start = bool(getattr(args,'init_lora_from',None))
    probe_size = getattr(args,'lora_probe_cases',17)
    if fast_start:
        # Fail before optimizer step 1 if a math-table target would be
        # impossible to generate within the configured token ceiling.
        evaluations=(args.lora_steps+args.lora_eval_every-1)//args.lora_eval_every
        for i in range(evaluations):
            deck,_=fast_probe_deck(dev,budget=probe_size,evaluation_index=i)
            for row in deck:
                needed=len(tok.encode('\n'+row['math_prompt'],add_special_tokens=False))+8
                if needed>args.max_new_tokens:
                    raise ValueError('GAMER_PROBE_TARGET_EXCEEDS_MAX_NEW_TOKENS: '
                                     'increase --max-new-tokens before training')

    def save_checkpoint(step, *, pending=False):
        save_training_state(torch, latest, {
            'schema': SCHEMA, 'phase':'lora', 'step':step,
            'pending_eval': bool(pending),
            'dataset_sha256':manifest['dataset_sha256'],
            'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
            'hyperparameters':frozen_hyperparameters(args,'lora'),
            'adapter':get_peft_model_state_dict(adapter),
            'optimizer':optimizer.state_dict(),
            'rng':training_rng(torch,python_rng=rng),
            'baseline':baseline,'history':history,
            'last_step_loss':last_step_loss,
            'best_score':best_score,'best_step':best_step,'best_eval':best_eval,
            'bootstrap':bootstrap,
            'sampling_focus':dict(sampling_focus),
            'sampling_counts':dict(sampling_counts),
        })
        print(f'GAMER LoRA: SAVED resumable optimizer checkpoint at step {step} '
              f'(pending_eval={pending}) -> {latest}',flush=True)

    def complete_evaluation(step):
        nonlocal best_score, best_step, best_eval
        # A step checkpoint has already been committed before evaluation.
        # This SHA binds every cached generation to the exact trained adapter.
        checkpoint_sha = sha_bytes(latest.read_bytes())
        path = out/f'eval_step_{step:07d}.json'
        print(f'GAMER LoRA: dev evaluation at step {step}/{args.lora_steps}.',flush=True)
        if fast_start:
            deck, anchors = fast_probe_deck(dev,budget=probe_size,
                           evaluation_index=(step-1)//args.lora_eval_every)
            print(f'GAMER LoRA: bounded probe {len(deck)}/{len(dev)} dev prompts; '
                  f'{anchors} fixed anchors, {len(deck)-anchors} rotating; '
                  'this is NOT full retention certification.',flush=True)
        else:
            deck, anchors = dev, None
        score = evaluate_lora(torch,adapter,tok,deck,device=args.device,
                              max_length=args.max_length,max_new_tokens=args.max_new_tokens,
                              progress_path=path,
                              progress_key=lora_eval_key(args,manifest,step=step,
                                                         checkpoint_sha256=checkpoint_sha),
                              target_bounded=fast_start)
        if fast_start:
            score['anchor_exact']=sum(score['matches'][:anchors])
            score['anchor_total']=anchors
            score['coverage']='bounded_probe_not_full_retention'
        if fast_start:
            # Error-directed focus on *training* cases from the same game or
            # blind sampling regime. Never train on development prompts.
            for row, matched in zip(deck,score['matches']):
                key=('blind:'+row['blind_matrix_regime'] if row.get('blind_matrix')
                     else 'named:'+row['game_id'])
                sampling_focus[key] = 1.0 if matched else 3.0
        history.append({'step':step, 'evaluation':score,
                        'loss_last_microbatch': last_step_loss})
        # Only compare the same fixed anchor across checkpoints, never a
        # different rotating deck against the prior score.
        candidate_score=(score['anchor_exact']/score['anchor_total']
                         if fast_start else score['strict_exact'])
        if candidate_score > best_score:
            best_score = candidate_score
            best_step = step
            best_eval = score
            adapter.save_pretrained(out/'best_adapter')
        # Commit evaluation results. A crash before this write leaves the
        # pending checkpoint and complete prompt progress for cheap recovery.
        save_checkpoint(step, pending=False)

    if args.resume and latest.exists():
        state=load_training_state(torch,latest,phase='lora',manifest=manifest,args=args)
        set_peft_model_state_dict(adapter,state['adapter'])
        optimizer.load_state_dict(state['optimizer'])
        for value in optimizer.state.values():
            for key,v in value.items():
                if isinstance(v,torch.Tensor): value[key]=v.to(args.device)
        restore_rng(torch,state['rng'],python_rng=rng)
        baseline=state['baseline']
        bootstrap=state.get('bootstrap')
        if fast_start and not bootstrap:
            raise ValueError('GAMER_INIT_ADAPTER_PROVENANCE_MISSING_FROM_RESUME')
        if not fast_start and bootstrap:
            raise ValueError('GAMER_RESUME_BOOTSTRAP_FLAG_MISSING')
        history=state['history']
        best_score=state['best_score']
        best_step=state['best_step']
        best_eval=state['best_eval']
        start_step=state['step']
        pending_eval=bool(state.get('pending_eval',False))
        last_step_loss=state.get('last_step_loss')
        sampling_focus=dict(state.get('sampling_focus',{}))
        sampling_counts=dict(state.get('sampling_counts',sampling_counts))
        if best_step and not (out/'best_adapter').is_dir() and not pending_eval:
            raise FileNotFoundError('GAMER_LORA_RESUME_BEST_ADAPTER_MISSING')
        print(f'GAMER LoRA: RESUMED step {start_step}/{args.lora_steps} '
              f'(pending_eval={pending_eval}); baseline will not repeat.',flush=True)
    else:
        if fast_start:
            baseline={'status':'SKIPPED_PRETRAINED_TRANSFER', 'strict_exact':None,
                      'exact':None, 'total':0, 'by_game':{}, 'by_blind_regime':{}}
            print('GAMER LoRA FAST START: no serial generative baseline; '
                  'saving imported adapter at step 0 and beginning optimizer.',flush=True)
            save_checkpoint(start_step)
        else:
            print('GAMER LoRA: baseline evaluation before optimizer step 1. '
                  'Each completed prompt will be saved for --resume.',flush=True)
            baseline = evaluate_lora(torch,adapter,tok,dev,device=args.device,
                                     max_length=args.max_length,max_new_tokens=args.max_new_tokens,
                                     progress_path=baseline_progress,
                                     progress_key=lora_eval_key(args,manifest,step=0))
            print(f'GAMER LoRA: baseline complete, exact={baseline["exact"]}/{baseline["total"]}; '
                  'saving optimizer step 0.',flush=True)
            save_checkpoint(0)

    if pending_eval:
        if start_step % args.lora_eval_every != 0 and start_step != args.lora_steps:
            raise ValueError('GAMER_LORA_PENDING_EVAL_AT_INVALID_STEP')
        print(f'GAMER LoRA: recovering unfinished evaluation at step {start_step}.',flush=True)
        complete_evaluation(start_step)

    named_indices=list(range(len(train)))
    if fast_start and not named_indices:
        raise ValueError('GAMER_TRANSFER_REQUIRES_VERIFIED_GAMES')

    def draw_item():
        if not fast_start:
            return items[rng.randrange(len(items))]
        # Train only verified games; prioritize sampled failures by game ID.
        indices=named_indices
        weights=[sampling_focus.get('named:'+train[idx]['game_id'],1.0) for idx in indices]
        chosen=rng.choices(indices,weights=weights,k=1)[0]
        sampling_counts['named_replay']+=1
        return items[chosen]

    for step in range(start_step+1,args.lora_steps+1):
        print(f'GAMER LoRA optimizer: START step {step}/{args.lora_steps} '
              f'({args.lora_accum} accumulation microbatches)',flush=True)
        adapter.train()
        optimizer.zero_grad(set_to_none=True)
        for microbatch in range(1, args.lora_accum+1):
            batch = collate(torch,tok,[draw_item() for _ in range(args.lora_batch)],args.device)
            loss = adapter(**batch).loss / args.lora_accum
            loss.backward()
            if microbatch == 1 or microbatch == args.lora_accum or microbatch % 4 == 0:
                print(f'GAMER LoRA optimizer: step {step}/{args.lora_steps} '
                      f'microbatch {microbatch}/{args.lora_accum} '
                      f'loss={float(loss.detach().cpu())*args.lora_accum:.6g}',flush=True)
        torch.nn.utils.clip_grad_norm_(adapter.parameters(),1.0)
        optimizer.step()
        last_step_loss=float(loss.detach().cpu())*args.lora_accum
        print(f'GAMER LoRA optimizer: DONE step {step}/{args.lora_steps}',flush=True)
        due_eval = (step % args.lora_eval_every == 0 or step == args.lora_steps)
        if due_eval or step % getattr(args,'lora_save_every',1) == 0:
            save_checkpoint(step, pending=due_eval)
        if due_eval:
            complete_evaluation(step)

    if best_step == 0:
        raise AssertionError('GAMER_LORA_NO_CHECKPOINT')
    fingerprint = file_tree_sha256(out/'best_adapter')
    covered_dev = {r['game_id'] for r in dev}
    if fast_start:
        # No false retention claim: the imported adapter has not undergone
        # a full, paired evaluation on the NEW dataset. Explicit audit later.
        no_regressions = False
        blind_regression_free = False
        handoff_eligible = False
    else:
        no_regressions = all(
            best_eval['by_game'][gid]['exact'] >= baseline['by_game'][gid]['exact']
            for gid in best_eval['by_game']
        )
        blind_regression_free = all(
            best_eval.get('by_blind_regime',{}).get(regime,{}).get('exact',-1)
            >= measured['exact']
            for regime,measured in baseline.get('by_blind_regime',{}).items()
        )
        no_regressions = no_regressions and blind_regression_free
        handoff_eligible = (best_score > baseline['strict_exact'] and no_regressions
                            and len(covered_dev)==manifest['number_of_verified_games'] and not args.allow_partial_smoke)
    report = {'schema':SCHEMA, 'phase':'lora', 'model':args.model,
              'dataset_sha256':manifest['dataset_sha256'],
              'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
              'train_count':len(train), 'dev_count':len(dev),
              'best_step':best_step, 'best':best_score, 'baseline':baseline,
              'best_adapter_sha256':fingerprint,
              'handoff_eligible':handoff_eligible,
              'lora_release_status':'EXPERIMENTAL_NOT_PROMOTED',
              'dev_game_coverage':len(covered_dev),
              'dev_no_regressions':no_regressions,
              'blind_matrix_dev_no_regressions':blind_regression_free,
              'best_evaluation':best_eval,
              'learning_objective':'prompt_08 -> table_text (not numeric answer)',
              'resumed':bool(args.resume),
              'history':history, 'bootstrap':bootstrap,
              'sampling_counts':sampling_counts, 'sampling_focus':sampling_focus,
              'evaluation_scope':('BOUNDED_PROBE_NOT_FULL_AUDIT' if fast_start else 'FULL_DEV'),
              'retention_certification':('NOT_AUDITED' if fast_start else
                                        ('PASS' if no_regressions else 'FAIL'))}
    atomic_json(out/'report.json',report)
    return report



@contextmanager
def _open_live_checkpoint_shared(path):
    """Read a running checkpoint without preventing atomic replacement on Windows."""
    path=Path(path)
    if os.name!='nt':
        with path.open('rb') as stream:
            yield stream
        return
    import ctypes
    import msvcrt
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW
    create.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,
                     ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
    create.restype=ctypes.c_void_p
    handle=create(str(path.resolve()),0x80000000,0x1|0x2|0x4,None,3,0x80,None)
    if handle in (None,ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    except Exception:
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        kernel.CloseHandle(handle)
        raise
    with os.fdopen(fd,'rb') as stream:
        yield stream

def snapshot_active_lora(args):
    """Freeze an evaluated best adapter from a still-training run into a NEW run.

    No source files are written. The destination gets its own certified corpus,
    immutable adapter copy, explicit experimental report, and evaluation evidence.
    The active run can continue improving or changing its best adapter afterward.
    """
    if not getattr(args, 'allow_experimental', False):
        raise ValueError('GAMER_ACTIVE_SNAPSHOT_REQUIRES_ALLOW_EXPERIMENTAL')
    source = Path(args.snapshot_lora_from).resolve()
    target = Path(args.output).resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('GAMER_ACTIVE_SNAPSHOT_REQUIRES_SEPARATE_RUN')
    if target.exists():
        raise FileExistsError('GAMER_ACTIVE_SNAPSHOT_TARGET_EXISTS: choose a fresh --output')
    step = args.snapshot_step
    if not isinstance(step, int) or step < 1:
        raise ValueError('GAMER_ACTIVE_SNAPSHOT_REQUIRES_SNAPSHOT_STEP')
    source_manifest_path = source/'handoff_manifest.json'
    source_dataset = source/'certified_pairs.jsonl'
    source_best = source/'lora'/'best_adapter'
    source_latest = source/'lora'/'latest.pt'
    source_eval = source/'lora'/f'eval_step_{step:07d}.json'
    for required in (source_manifest_path, source_dataset, source_latest, source_eval):
        if not required.is_file():
            raise FileNotFoundError('GAMER_ACTIVE_SNAPSHOT_SOURCE_EVIDENCE_MISSING: ' + str(required))
    if not source_best.is_dir():
        raise FileNotFoundError('GAMER_ACTIVE_SNAPSHOT_BEST_ADAPTER_MISSING')
    if not (source_best/'adapter_config.json').is_file() or not any(
        (source_best/name).is_file() for name in ('adapter_model.safetensors','adapter_model.bin')
    ):
        raise FileNotFoundError('GAMER_ACTIVE_SNAPSHOT_PEFT_ADAPTER_INCOMPLETE')

    source_manifest = json.loads(source_manifest_path.read_text(encoding='utf-8'))
    cases, observed = load_cases(source_dataset,
        allow_partial=args.allow_partial_smoke,
        include_catalog_extensions=args.include_catalog_extensions,
        include_transition_games=args.include_transition_games)
    observed.update({'model':args.model, 'allow_partial_smoke':bool(args.allow_partial_smoke)})
    if source_manifest != observed or observed['provenance'] != 'source_recomputed':
        raise ValueError('GAMER_ACTIVE_SNAPSHOT_SOURCE_DATASET_OR_MANIFEST_MISMATCH')
    cfg=json.loads((source_best/'adapter_config.json').read_text(encoding='utf-8'))
    if cfg.get('task_type')!='CAUSAL_LM' or cfg.get('r')!=args.lora_rank:
        raise ValueError('GAMER_ACTIVE_SNAPSHOT_ADAPTER_CONFIG_MISMATCH')
    if cfg.get('base_model_name_or_path') not in (None, args.model):
        raise ValueError('GAMER_ACTIVE_SNAPSHOT_BASE_MODEL_MISMATCH')

    # Loading the atomic latest checkpoint is CPU-only; no GPU contention.
    import torch
    def checked_source_state():
        with _open_live_checkpoint_shared(source_latest) as stream:
            state=torch.load(stream,map_location='cpu',weights_only=True)
        hp=state.get('hyperparameters',{})
        if (state.get('schema')!=SCHEMA or state.get('phase')!='lora'
            or state.get('dataset_sha256')!=observed['dataset_sha256']
            or state.get('original_prompt_corpus_sha256')!=observed['original_prompt_corpus_sha256']
            or hp.get('model')!=args.model or hp.get('lora_rank')!=args.lora_rank
            or not isinstance(state.get('step'),int) or state['step']<step
            or state.get('best_step')!=step):
            raise ValueError('GAMER_ACTIVE_SNAPSHOT_STEP_OR_LINEAGE_MISMATCH')
        best=state.get('best_eval')
        if (not isinstance(best,dict) or not isinstance(best.get('matches'),list)
            or any(type(m) is not bool for m in best['matches'])
            or best.get('total')!=len(best['matches'])
            or best.get('exact')!=sum(best['matches'])
            or best.get('coverage')!='bounded_probe_not_full_retention'
            or best.get('anchor_total',0)<1):
            raise ValueError('GAMER_ACTIVE_SNAPSHOT_BEST_EVALUATION_UNVERIFIED')
        return state
    state=checked_source_state()
    score=state['best_eval']
    progress=json.loads(source_eval.read_text(encoding='utf-8'))
    if (progress.get('schema')!='nanojev.gamer.lora.eval-progress.v1'
        or progress.get('target_bounded') is not True
        or progress.get('matches')!=score['matches']):
        raise ValueError('GAMER_ACTIVE_SNAPSHOT_EVALUATION_RECORD_MISMATCH')
    # Validate the exact named-game deck rather than trusting a summary count.
    deck,_=fast_probe_deck(by_split(cases,'dev'), budget=score['total'],
                           evaluation_index=(step-1)//state['hyperparameters']['lora_eval_every'])
    deck_sha=sha_bytes(canonical([(r['game_id'],original_prompt(r),r['math_prompt'])
                                  for r in deck]).encode('utf-8'))
    if progress.get('deck_sha256')!=deck_sha:
        raise ValueError('GAMER_ACTIVE_SNAPSHOT_EVALUATION_DECK_CHANGED')

    source_sha=file_tree_sha256(source_best)
    target.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix=target.name+'.snapshot-',dir=target.parent))
    try:
        shutil.copyfile(source_dataset,staging/'certified_pairs.jsonl')
        shutil.copytree(source_best,staging/'lora'/'best_adapter')
        shutil.copyfile(source_eval,staging/'lora'/'source_evaluation.json')
        if (file_tree_sha256(source_best)!=source_sha or
            file_tree_sha256(staging/'lora'/'best_adapter')!=source_sha):
            raise ValueError('GAMER_ACTIVE_SNAPSHOT_ADAPTER_CHANGED_DURING_COPY')
        second=checked_source_state()
        if second['best_eval']!=score:
            raise ValueError('GAMER_ACTIVE_SNAPSHOT_BEST_CHANGED_DURING_COPY')
        if sha_bytes(source_dataset.read_bytes())!=observed['dataset_sha256']:
            raise ValueError('GAMER_ACTIVE_SNAPSHOT_DATASET_CHANGED_DURING_COPY')
        if (sha_bytes((staging/'certified_pairs.jsonl').read_bytes())!=observed['dataset_sha256']
            or sha_bytes((staging/'lora'/'source_evaluation.json').read_bytes())!=
               sha_bytes(source_eval.read_bytes())):
            raise ValueError('GAMER_ACTIVE_SNAPSHOT_EVIDENCE_COPY_MISMATCH')
        atomic_json(staging/'handoff_manifest.json',observed)
        frozen={'schema':SCHEMA,'phase':'lora', 'model':args.model,
                'dataset_sha256':observed['dataset_sha256'],
                'original_prompt_corpus_sha256':observed['original_prompt_corpus_sha256'],
                'best_step':step,'best_adapter_sha256':source_sha,
                'best_evaluation':score, 'handoff_eligible':False,
                'lora_release_status':'EXPERIMENTAL_NOT_PROMOTED',
                'retention_certification':'NOT_AUDITED',
                'evaluation_scope':'BOUNDED_PROBE_NOT_FULL_AUDIT',
                'frozen_snapshot':True,
                'snapshot_source':{'run':str(source),'checkpoint_step_observed':state['step'],
                                   'evaluated_best_step':step,'adapter_sha256':source_sha,
                                   'source_eval_sha256':sha_bytes(source_eval.read_bytes())}}
        atomic_json(staging/'lora'/'report.json',frozen)
        # Publication is a single directory rename, never an in-place copy into
        # the live training run. Refuse an occupied destination even on retry.
        if target.exists():
            raise FileExistsError('GAMER_ACTIVE_SNAPSHOT_TARGET_EXISTS')
        os.replace(staging,target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return frozen


def load_frozen_adapter(args, manifest):
    out = Path(args.output)
    path = out/'lora'/'report.json'
    if not path.exists():
        raise FileNotFoundError('GAMER_LORA_REPORT_MISSING; run --stage lora first')
    report = json.loads(path.read_text(encoding='utf-8'))
    if (report.get('model') != args.model or report.get('dataset_sha256') != manifest['dataset_sha256']
            or (report.get('frozen_snapshot') and
                (report.get('schema')!=SCHEMA or report.get('phase')!='lora'
                 or report.get('retention_certification')!='NOT_AUDITED'
                 or report.get('handoff_eligible') is not False))):
        raise ValueError('GAMER_LORA_DATA_OR_MODEL_MISMATCH')
    folder = out/'lora'/'best_adapter'
    if report.get('best_adapter_sha256') != file_tree_sha256(folder):
        raise ValueError('GAMER_LORA_ADAPTER_FINGERPRINT_MISMATCH')
    if not report.get('handoff_eligible') and not args.allow_experimental:
        raise ValueError('GAMER_LORA_NOT_PROMOTED; use --allow-experimental for explicitly isolated smoke')
    return folder, report


def switch_to_numerical(args, manifest):
    """Atomically authorize the pinned LoRA -> frozen C0 Wide boundary."""
    folder, report = load_frozen_adapter(args, manifest)
    data = {
        'schema':SCHEMA, 'phase':'switch', 'status':'READY_TO_TRAIN_TO_NUMERICAL',
        'model':args.model, 'dataset_sha256':manifest['dataset_sha256'],
        'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
        'adapter_path':'lora/best_adapter',
        'adapter_sha256':file_tree_sha256(folder),
        'numerical_input':'original_prompt_08_hidden_activations_only',
        'numerical_target':'oracle_numeric_answer',
        'head_variant':'existing_C0_wide',
        'lora_handoff_eligible':bool(report['handoff_eligible']),
        'experimental_override_used':bool(not report['handoff_eligible']),
        'forbidden_numerical_inputs':['math_prompt','oracle_table','generated_table'],
    }
    if report.get('frozen_snapshot'):
        data['frozen_snapshot_source']=report['snapshot_source']
    path=Path(args.output)/'handoff.json'
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8'))!=data:
            raise ValueError('GAMER_SWITCH_LINEAGE_MISMATCH')
    else:
        atomic_json(path,data)
    return data


def assert_switch(args, manifest, adapter_report):
    path=Path(args.output)/'handoff.json'
    if not path.exists():
        raise FileNotFoundError('GAMER_SWITCH_NOT_EXECUTED: explicitly run stage switch')
    data=json.loads(path.read_text(encoding='utf-8'))
    if (data.get('schema')!=SCHEMA or data.get('dataset_sha256')!=manifest['dataset_sha256']
        or data.get('adapter_sha256')!=adapter_report['best_adapter_sha256']
        or data.get('original_prompt_corpus_sha256')!=manifest['original_prompt_corpus_sha256']
        or data.get('model')!=args.model
        or data.get('numerical_input')!='original_prompt_08_hidden_activations_only'
        or data.get('head_variant')!='existing_C0_wide'
        or (adapter_report.get('frozen_snapshot') and
            data.get('frozen_snapshot_source')!=adapter_report.get('snapshot_source'))):
        raise ValueError('GAMER_SWITCH_CONTRACT_MISMATCH')
    if data.get('experimental_override_used') and not args.allow_experimental:
        raise ValueError('GAMER_EXPERIMENTAL_SWITCH_REQUIRES_EXPLICIT_OPT_IN')
    return data


def capture_original_features(torch, model, tok, cases, device, *, batch_size, max_length):
    """The Wide head never sees math_prompt, table contents, or LoRA completions."""
    import numpy as np
    from tools.nanojev_qwen_c0_compressive_train import capture_features
    # The existing C0 function reads ONLY row['prompt']; construct these rows
    # exclusively from the original 08 input, never the certified math target.
    opaque = [{'prompt':original_prompt(row)} for row in cases]
    # C0's existing capture function uses truncation=True internally; refuse any
    # original prompt that would be truncated rather than silently change the math.
    for row in opaque:
        if len(tok.encode(row['prompt'], add_special_tokens=True)) > max_length:
            raise ValueError('GAMER_ORIGINAL_PROMPT_WOULD_BE_TRUNCATED')
    hargs = argparse.Namespace(device=device, capture_batch=batch_size, max_length=max_length)
    result = capture_features(torch, model, tok, opaque, hargs)
    if result.shape[0] != len(cases) or not np.isfinite(result).all():
        raise RuntimeError('GAMER_INVALID_ORIGINAL_PROMPT_ACTIVATION_CAPTURE')
    return result


def numeric_metrics(cases, predictions):
    import numpy as np
    truth = np.asarray([float(r['answer']) for r in cases], dtype=np.float64)
    prediction = np.asarray(predictions, dtype=np.float64)
    if not len(truth) or truth.shape != prediction.shape or not np.isfinite(prediction).all():
        raise ValueError('GAMER_INVALID_NUMERIC_PREDICTIONS')
    exact = np.isclose(truth, prediction, rtol=0.0, atol=1e-6)
    integer = np.isclose(truth, np.rint(truth), rtol=0.0, atol=1e-9)
    rounded = np.rint(prediction) == np.rint(truth)
    signed = np.sign(prediction) == np.sign(truth)
    per_game = {}
    for gid in sorted({r['game_id'] for r in cases}):
        idx = np.asarray([i for i,r in enumerate(cases) if r['game_id']==gid])
        per_game[gid] = {'cases':int(len(idx)), 'mae':float(np.abs(truth[idx]-prediction[idx]).mean()),
                         'sign_accuracy':float(signed[idx].mean()), 'exact_with_1e-6':float(exact[idx].mean())}
    residual_present = ['zero_line_residual' in r for r in cases]
    zero_line = {'status':'NO_RESIDUAL_METADATA', 'excluded_cases':len(cases)}
    if any(residual_present):
        classes = {}
        for i,row in enumerate(cases):
            if 'zero_line_residual' not in row:continue
            r=float(row['zero_line_residual'])
            classes[i]=('zero' if r==0 else 'near_negative' if -1 <= r < 0 else
                        'near_positive' if 0 < r <= 1 else 'outer_negative' if r < 0 else 'outer_positive')
        zero_line={'status':'MEASURED' if all(residual_present) else 'MEASURED_PARTIAL',
                   'included_cases':len(classes),'excluded_cases':len(cases)-len(classes),
                   'by_residual_class':{}}
        for cls in sorted(set(classes.values())):
            idx=np.asarray([i for i,name in classes.items() if name==cls])
            zero_line['by_residual_class'][cls]={
                'cases':len(idx),'mae':float(np.abs(truth[idx]-prediction[idx]).mean()),
                'sign_accuracy':float(signed[idx].mean()),
                'exact_with_1e-6':float(exact[idx].mean()),
            }
    blind_regimes={}
    for regime in sorted({r['blind_matrix_regime'] for r in cases if r.get('blind_matrix')}):
        idx=np.asarray([i for i,r in enumerate(cases) if r.get('blind_matrix_regime')==regime])
        blind_regimes[regime]={
            'cases':int(len(idx)),
            'mae':float(np.abs(truth[idx]-prediction[idx]).mean()),
            'exact_with_1e-6':float(exact[idx].mean()),
            'integer_rounded_exact':float(rounded[idx].mean()),
        }
    return {'cases':len(cases), 'mae':float(np.abs(truth-prediction).mean()),
            'by_blind_regime':blind_regimes,
            'zero_line':zero_line,
            'sign_accuracy':float(signed.mean()), 'exact_with_1e-6':float(exact.mean()),
            'integer_rounded_exact':float(rounded[integer].mean()) if integer.any() else None,
            'per_game':per_game}


def head_transfer_identity(args, manifest, torch):
    """Verify a previous numeric cycle; import weights, not stale feature optimizer moments."""
    source=Path(args.init_head_from).resolve()
    output=Path(args.output).resolve()
    if source==output or source in output.parents or output in source.parents:
        raise ValueError('GAMER_HEAD_TRANSFER_REQUIRES_DISTINCT_CYCLE')
    source_state=source/'to_numerical'/'latest.pt'
    source_manifest=source/'handoff_manifest.json'
    if not source_state.is_file() or not source_manifest.is_file():
        raise FileNotFoundError('GAMER_HEAD_TRANSFER_SOURCE_CHECKPOINT_OR_MANIFEST_MISSING')
    previous=json.loads(source_manifest.read_text(encoding='utf-8'))
    if (previous.get('dataset_sha256')!=manifest['dataset_sha256']
        or previous.get('original_prompt_corpus_sha256')!=manifest['original_prompt_corpus_sha256']
        or previous.get('model')!=args.model):
        raise ValueError('GAMER_HEAD_TRANSFER_DATASET_OR_BACKBONE_CHANGED')
    # Read the source through the same Windows-safe shared handle as the live diagnostic.
    with _open_live_checkpoint_shared(source_state) as stream:
        raw=stream.read()
    import io
    state=torch.load(io.BytesIO(raw),map_location='cpu',weights_only=True)
    if (state.get('phase')!='to_numerical' or state.get('schema')!=SCHEMA
        or state.get('dataset_sha256')!=manifest['dataset_sha256']
        or state.get('original_prompt_corpus_sha256')!=manifest['original_prompt_corpus_sha256']
        or not isinstance(state.get('step'),int) or state['step']<1
        or not isinstance(state.get('model'),dict) or not state['model']
        or state.get('hyperparameters',{}).get('model')!=args.model):
        raise ValueError('GAMER_HEAD_TRANSFER_SOURCE_STATE_INVALID')
    if args.head_steps <= state['step']:
        raise ValueError(f'GAMER_HEAD_TARGET_MUST_EXCEED_SOURCE: source={state["step"]}, target={args.head_steps}')
    return state,{'source_run':str(source),'source_head_step':state['step'],
                  'source_head_sha256':sha_bytes(raw),
                  'source_adapter_sha256':state.get('adapter_sha256'),
                  'optimizer_policy':'FRESH_ON_CHANGED_FEATURE_DISTRIBUTION'}


def train_to_numerical(args, cases, manifest):
    """Train C0 Wide; preserve head learning across LoRA changes using explicit cycles."""
    import numpy as np
    import torch
    from tools.nanojev_qwen_c0_compressive_train import (
        VARIANTS, make_network, signed_log, eval_heads, neural_loss,
    )
    from tools.nanojev_gamer_v1_error_atlas import build_error_atlas, write_atlas, export_csv
    from tools.nanojev_gamer_v1_zero_line import (
        ZeroLineSampler, resolve_policy, target_zero_line_metrics, FOCUS_CLASSES,
    )
    out=Path(args.output)/'to_numerical'
    latest=out/'latest.pt'
    completed_report=out/'report.json'
    # Resolve sampling before touching the completed report. Unlike the model's
    # immutable hyperparameters, this training-corpus selection may be changed
    # explicitly at a *committed* optimizer-step boundary without resetting
    # network weights, optimizer moments, or the RNG.
    prior_state = (load_training_state(torch,latest,phase='to_numerical',manifest=manifest,args=args)
                   if args.resume and latest.is_file() else None)
    prior_policy = prior_state.get('sampling_policy') if prior_state is not None else None
    sampling_policy = resolve_policy(
        focus=getattr(args,'zero_line_focus',None),
        radius=getattr(args,'zero_line_radius',None),previous=prior_policy)
    train,dev,test=[by_split(cases,s) for s in SPLITS]
    sampler=ZeroLineSampler(train,**sampling_policy)
    if completed_report.is_file():
        if not args.resume or not latest.is_file():
            raise FileExistsError('GAMER_WIDE_ALREADY_FINALIZED: --resume with larger --head-steps to extend')
        previous=prior_state
        if args.head_steps<=previous['step']:
            raise ValueError('GAMER_HEAD_EXTENSION_REQUIRES_LARGER_STEP_TARGET')
        prior=json.loads(completed_report.read_text(encoding='utf-8'))
        if prior.get('dataset_sha256')!=manifest['dataset_sha256']:
            raise ValueError('GAMER_HEAD_EXTENSION_REPORT_DATASET_MISMATCH')
        archive=out/'completed_reports'/f'report_step_{previous["step"]:07d}.json'
        if archive.exists() and json.loads(archive.read_text(encoding='utf-8'))!=prior:
            raise ValueError('GAMER_HEAD_EXTENSION_REPORT_ARCHIVE_CONFLICT')
        if not archive.exists():atomic_json(archive,prior)
        completed_report.unlink()
        print(f'GAMER Wide EXTEND: previous report archived; resuming at step {previous["step"]}.',flush=True)
    if latest.exists() and not args.resume:
        raise FileExistsError('GAMER_WIDE_INTERRUPTED: explicitly provide --resume')
    if args.resume and not latest.exists():
        raise FileNotFoundError('GAMER_WIDE_NO_RESUME_STATE')
    adapter_dir,lora_report=load_frozen_adapter(args,manifest)
    assert_switch(args,manifest,lora_report)
    set_seeds(args.seed)
    # Each cycle uses its OWN frozen LoRA. Reuse captures only within the
    # exact same adapter+prompt lineage; a newer LoRA always gets fresh inputs.
    cache=out/'feature_cache'
    captured_model=None
    def features(split,rows):
        nonlocal captured_model
        path=cache/f'{split}.npz'
        identity={'dataset_sha256':manifest['dataset_sha256'],
                  'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
                  'adapter_sha256':lora_report['best_adapter_sha256'],
                  'model':args.model,'max_length':args.max_length,
                  'prompts_sha256':sha_bytes(canonical([original_prompt(r) for r in rows]).encode('utf-8'))}
        if path.is_file():
            with np.load(path,allow_pickle=False) as saved:
                if saved['identity'].item()!=canonical(identity):
                    raise ValueError('GAMER_HEAD_FEATURE_CACHE_LINEAGE_CHANGED')
                x=saved['features'].copy()
            if x.ndim!=2 or x.shape[0]!=len(rows) or not np.isfinite(x).all():
                raise ValueError('GAMER_HEAD_FEATURE_CACHE_INVALID')
            print(f'GAMER Wide features: reused {split} exact frozen-LoRA capture ({len(rows)} prompts).',flush=True)
            return x
        if captured_model is None:
            _,tok,model=model_components(args.model,args.device,adapter_dir=adapter_dir)
            model.eval()
            captured_model=(tok,model)
        tok,model=captured_model
        x=capture_original_features(torch,model,tok,rows,args.device,
                                    batch_size=args.capture_batch,max_length=args.max_length)
        if file_tree_sha256(adapter_dir)!=lora_report['best_adapter_sha256']:
            raise ValueError('GAMER_HEAD_FROZEN_ADAPTER_CHANGED_DURING_CAPTURE')
        cache.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix='.npz',dir=cache,delete=False) as f:
            temporary=Path(f.name)
        try:
            with temporary.open('wb') as stream:
                np.savez_compressed(stream,identity=canonical(identity),features=x)
            os.replace(temporary,path)
        finally:
            if temporary.exists():temporary.unlink()
        print(f'GAMER Wide features: captured and checkpointed {split} ({len(rows)} prompts).',flush=True)
        return x
    x_train=features('train',train)
    x_dev=features('dev',dev)
    x_test=features('test',test)
    y_train=np.asarray([float(r['answer']) for r in train],dtype=np.float64)
    y_dev=np.asarray([float(r['answer']) for r in dev],dtype=np.float64)
    y_test=np.asarray([float(r['answer']) for r in test],dtype=np.float64)
    # Free GPU memory occupied by frozen Qwen before training the numeric head.
    if captured_model is not None:
        del captured_model
        if str(args.device).startswith('cuda'):
            torch.cuda.empty_cache()
    head=make_network(torch,x_train.shape[1],VARIANTS['wide']).to(args.device)
    optimizer=torch.optim.AdamW(head.parameters(),lr=args.head_lr,weight_decay=args.weight_decay)
    rng=np.random.default_rng(args.seed+9981)
    best=float('inf');best_weights=None;best_optimizer=None;best_step=0;checks=[]
    start_step=0
    bootstrap_head=None
    last_loss=None
    sampling_counts = {key: 0 for key in ('uniform',) + FOCUS_CLASSES}
    sampled_game_counts = {}
    sampling_history = []
    if args.resume:
        state=prior_state
        if state.get('adapter_sha256') != lora_report['best_adapter_sha256']:
            raise ValueError('GAMER_WIDE_RESUME_ADAPTER_DRIFT')
        head.load_state_dict(state['model'])
        optimizer.load_state_dict(state['optimizer'])
        for value in optimizer.state.values():
            for k,v in value.items():
                if isinstance(v,torch.Tensor):value[k]=v.to(args.device)
        restore_rng(torch,state['rng'],numpy_rng=rng)
        best=state['best'];best_weights=state['best_weights']
        best_optimizer=state['best_optimizer'];best_step=state['best_step']
        checks=state['checks'];start_step=state['step']
        bootstrap_head=state.get('bootstrap_head')
        last_loss=state.get('last_loss')
        sampling_counts=dict(state.get('sampling_counts',sampling_counts))
        sampled_game_counts=dict(state.get('sampled_game_counts',{}))
        sampling_history=list(state.get('sampling_history',[]))
        if bool(bootstrap_head)!=bool(getattr(args,'init_head_from',None)):
            raise ValueError('GAMER_HEAD_TRANSFER_RESUME_LINEAGE_MISMATCH')
        print(f'GAMER Wide RESUMED: step={start_step}, frozen LoRA SHA={lora_report["best_adapter_sha256"]}.',flush=True)
    elif getattr(args,'init_head_from',None):
        old,bootstrap_head=head_transfer_identity(args,manifest,torch)
        head.load_state_dict(old['model'],strict=True)
        start_step=old['step']
        # Unlike a same-LoRA resume, changed Qwen activations invalidate the
        # previous optimizer moments and best evaluation score; preserve weights.
        print(f'GAMER Wide STAIR STEP: transferred exact head weights from '
              f'step {start_step}; new LoRA activations, fresh optimizer and evaluation baseline.',flush=True)
    # Store each policy transition with the FIRST optimizer step using it.
    # Older checkpoints without policy metadata were uniformly sampled.
    if prior_policy != sampling_policy:
        sampling_history.append({'start_step': start_step + 1, **sampling_policy})
    if sampling_policy['focus']:
        stats=sampler.coverage
        print(f'GAMER Wide ZERO-LINE: focus={sampling_policy["focus"]:.3f} '
              f'radius={sampling_policy["radius"]:g}; eligible games '
              f'{stats["eligible_games_any"]}/{stats["train_games"]}; '
              f'eligible cases {stats["eligible_cases_by_class"]}; '
              f'games with no eligible cases={len(stats["games_without_eligible_examples"])}.',flush=True)
    def save_head_checkpoint(step):
        save_training_state(torch,latest,{
            'schema':SCHEMA,'phase':'to_numerical','step':step,
            'dataset_sha256':manifest['dataset_sha256'],
            'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
            'adapter_sha256':lora_report['best_adapter_sha256'],
            'hyperparameters':frozen_hyperparameters(args,'to_numerical'),
            'model':head.state_dict(),'optimizer':optimizer.state_dict(),
            'rng':training_rng(torch,numpy_rng=rng),
            'best':best,'best_weights':best_weights,
            'best_optimizer':best_optimizer,'best_step':best_step,'checks':checks,
            'last_loss':last_loss,'bootstrap_head':bootstrap_head,
            'sampling_policy':dict(sampling_policy),
            'sampling_history':list(sampling_history),
            'sampling_counts':dict(sampling_counts),
            'sampled_game_counts':dict(sampled_game_counts),
        })
    if not args.resume:
        save_head_checkpoint(start_step)
        print(f'GAMER Wide: initial resumable checkpoint at step {start_step}.',flush=True)
    elif prior_policy != sampling_policy:
        # Persist the policy switch with the same completed optimizer step;
        # an interruption before step+1 deterministically recovers it.
        save_head_checkpoint(start_step)
        print(f'GAMER Wide SAMPLING CHANGE COMMITTED: effective from step '
              f'{start_step+1}.',flush=True)
    if start_step>=args.head_steps:
        raise ValueError('GAMER_HEAD_TARGET_NOT_GREATER_THAN_CURRENT')
    for step in range(start_step+1,args.head_steps+1):
        indices,strata=sampler.draw(rng,args.head_batch)
        for index,kind in zip(indices,strata):
            sampling_counts[kind] += 1
            game_id=train[int(index)]['game_id']
            sampled_game_counts[game_id] = sampled_game_counts.get(game_id,0) + 1
        x=torch.as_tensor(x_train[indices].copy(),device=args.device,dtype=torch.float32)
        y=torch.as_tensor(signed_log(y_train[indices]).astype('float32'),device=args.device)
        head.train()
        corrected,raw=head(x)
        loss=neural_loss(torch,corrected,raw,y,beta=args.huber_beta,aux_weight=args.aux_weight)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(head.parameters(),args.grad_clip)
        optimizer.step()
        last_loss=float(loss.detach().cpu())
        due_eval=(step % args.head_eval_every==0 or step==args.head_steps)
        if due_eval:
            prediction,_=eval_heads(torch,head,x_dev,y_dev,0.,1.,args.device,args.eval_batch)
            score=float(np.abs(prediction-y_dev).mean())
            # Preserve the error geometry for every held-out development case.
            # This is separate from exact classification and remains comparable
            # across identical dev decks even when the frozen LoRA changes.
            atlas=build_error_atlas(dev,prediction,split='dev',step=step,
                adapter_sha256=lora_report['best_adapter_sha256'],
                dataset_sha256=manifest['dataset_sha256'],checkpoint_kind='evaluated_latest')
            atlas_path=out/'error_atlas'/f'dev_step_{step:07d}.json'
            write_atlas(atlas,atlas_path)
            export_csv(atlas,atlas_path.with_suffix('.csv'))
            checks.append({'step':step,'dev_mae':score,'train_loss':last_loss,
                           'sampling_policy':dict(sampling_policy),
                           'sampling_counts':dict(sampling_counts),
                           'target_zero_line':target_zero_line_metrics(
                               dev,prediction,radius=sampling_policy['radius']),
                           'adapter_sha256':lora_report['best_adapter_sha256'],
                           'error_atlas':str(atlas_path.relative_to(out)),
                           'p90_abs_error':atlas['summary']['p90_abs_error'],
                           'within_one':atlas['summary']['within_1'],
                           'correct_sign_rate':atlas['summary']['correct_sign_rate']})
            if score<best:
                best,best_weights,best_optimizer,best_step=(score,copy.deepcopy(head.state_dict()),
                                                             copy.deepcopy(optimizer.state_dict()),step)
            print(f'GAMER Wide: step {step}/{args.head_steps} dev_mae={score:.6g} '
                  f'best={best:.6g} best_step={best_step}.',flush=True)
        if due_eval or step % getattr(args,'head_save_every',1)==0:
            save_head_checkpoint(step)
        if not due_eval and (step==start_step+1 or step%25==0):
            print(f'GAMER Wide optimizer: DONE step {step}/{args.head_steps} loss={last_loss:.6g}',flush=True)
    if best_weights is None:
        raise AssertionError('GAMER_WIDE_NO_CHECKPOINT')
    head.load_state_dict(best_weights)
    final_dev,_=eval_heads(torch,head,x_dev,y_dev,0.,1.,args.device,args.eval_batch)
    final_test,_=eval_heads(torch,head,x_test,y_test,0.,1.,args.device,args.eval_batch)
    out.mkdir(parents=True,exist_ok=True)
    # Keep the final selected checkpoint's complete per-case error map for both
    # held-out splits; the final weights need not equal the latest optimizer step.
    final_atlases={}
    for split,rows,predictions in (('dev',dev,final_dev),('test',test,final_test)):
        final_atlas=build_error_atlas(rows,predictions,split=split,step=best_step,
            adapter_sha256=lora_report['best_adapter_sha256'],
            dataset_sha256=manifest['dataset_sha256'],checkpoint_kind='selected_best')
        atlas_path=out/'error_atlas'/f'{split}_best_step_{best_step:07d}.json'
        write_atlas(final_atlas,atlas_path)
        export_csv(final_atlas,atlas_path.with_suffix('.csv'))
        final_atlases[split]={'json':str(atlas_path.relative_to(out)),
                              'csv':str(atlas_path.with_suffix('.csv').relative_to(out)),
                              'summary':final_atlas['summary']}
    # Archive best of THIS LoRA feature distribution. The exact latest learned
    # head weights remain in latest.pt for transfer into the next stair-step.
    save_training_state(torch,out/'wide.pt',{
        'model':best_weights,'optimizer':best_optimizer,
        'input_width':int(x_train.shape[1]),'hidden':list(VARIANTS['wide']),
        'variant':'wide','normalization':'signed_log1p_exact_target',
        'backbone_model':args.model,
        'frozen_adapter_sha256':lora_report['best_adapter_sha256'],
        'dataset_sha256':manifest['dataset_sha256'],
        'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
        'best_step':best_step})
    report={'schema':SCHEMA,'phase':'to_numerical',
            'architecture':'existing_C0_wide',
            'input':'frozen_Qwen_LoRA_hidden_states_of_original_prompt_08_only',
            'forbidden_inputs':['math_prompt','table_text','oracle_mathematical_prompt','generated_math_prompt'],
            'target':'oracle_numeric_answer',
            'dataset_sha256':manifest['dataset_sha256'],
            'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
            'frozen_adapter_sha256':lora_report['best_adapter_sha256'],
            'train_count':len(train),'dev_count':len(dev),'test_count':len(test),
            'best_step':best_step,'latest_step':args.head_steps,'resumed':bool(args.resume),
            'head_transfer':bootstrap_head,
            'sampling_policy':dict(sampling_policy),
            'sampling_history':list(sampling_history),
            'sampling_counts':dict(sampling_counts),
            'sampled_game_counts':dict(sampled_game_counts),
            'sampling_coverage':sampler.coverage,
            'target_zero_line':{
                'dev':target_zero_line_metrics(dev,final_dev,radius=sampling_policy['radius']),
                'test':target_zero_line_metrics(test,final_test,radius=sampling_policy['radius']),
            },
            'dev':numeric_metrics(dev,final_dev),
            'test':numeric_metrics(test,final_test),'checks':checks,
            'error_atlas':final_atlases,
            'release_status':'EXPERIMENTAL_NOT_PROMOTED'}
    atomic_json(completed_report,report)
    return report


def probe(args, cases, manifest):
    import torch
    from tools.nanojev_qwen_c0_compressive_train import VARIANTS, make_network, eval_heads
    from tools.nanojev_gamer_v1_error_atlas import build_error_atlas, write_atlas, export_csv
    out = Path(args.output)/'to_numerical'
    saved = out/'wide.pt'
    if not saved.exists():
        raise FileNotFoundError('GAMER_WIDE_CHECKPOINT_NOT_FOUND')
    adapter_dir, lora_report = load_frozen_adapter(args,manifest)
    assert_switch(args,manifest,lora_report)
    checkpoint = torch.load(saved,map_location='cpu',weights_only=True)
    if checkpoint.get('dataset_sha256') != manifest['dataset_sha256'] or checkpoint.get('frozen_adapter_sha256') != lora_report['best_adapter_sha256']:
        raise ValueError('GAMER_WIDE_BACKBONE_OR_DATA_CHANGED')
    if checkpoint.get('hidden') != list(VARIANTS['wide']):
        raise ValueError('GAMER_WIDE_ARCHITECTURE_CHANGED')
    torch, tok, frozen = model_components(args.model,args.device,adapter_dir=adapter_dir)
    head = make_network(torch,checkpoint['input_width'],VARIANTS['wide']).to(args.device)
    head.load_state_dict(checkpoint['model'])
    result = {}
    for split in ('dev','test'):
        rows=by_split(cases,split)
        x=capture_original_features(torch,frozen,tok,rows,args.device,batch_size=args.capture_batch,max_length=args.max_length)
        pred,_=eval_heads(torch,head,x,None,0.,1.,args.device,args.eval_batch)
        result[split]=numeric_metrics(rows,pred)
        atlas=build_error_atlas(rows,pred,split=split,step=checkpoint['best_step'],
            adapter_sha256=lora_report['best_adapter_sha256'],
            dataset_sha256=manifest['dataset_sha256'],checkpoint_kind='probe_frozen_best')
        atlas_path=out/'error_atlas'/f'{split}_probe_best_{checkpoint["best_step"]:07d}.json'
        write_atlas(atlas,atlas_path)
        export_csv(atlas,atlas_path.with_suffix('.csv'))
        result[split]['error_atlas']={'json':str(atlas_path.relative_to(out)),
                                      'summary':atlas['summary']}
    result.update({'schema':SCHEMA,'phase':'probe', 'dataset_sha256':manifest['dataset_sha256'],
                   'frozen_adapter_sha256':lora_report['best_adapter_sha256']})
    atomic_json(out/'probe.json',result)
    return result


def probe_blind_lora(args,cases,manifest):
    """Explicit held-out anonymous-matrix probe, no training or Wide inputs."""
    test=[row for row in cases if row.get('blind_matrix') and row['split']=='test']
    if not test:
        raise ValueError('GAMER_NO_BLIND_MATRIX_TEST_CASES: prepare a blind-matrix dataset first')
    folder,report=load_frozen_adapter(args,manifest)
    torch,tok,adapter=model_components(args.model,args.device,adapter_dir=folder)
    out=Path(args.output)/'lora'
    score=evaluate_lora(torch,adapter,tok,test,device=args.device,
                        max_length=args.max_length,max_new_tokens=args.max_new_tokens,
                        progress_path=out/'blind_probe_progress.json',
                        progress_key=sha_bytes(canonical({
                            'dataset_sha256':manifest['dataset_sha256'],
                            'adapter_sha256':report['best_adapter_sha256'],
                            'probe':'held_out_blind_matrix_test'
                        }).encode('utf-8')))
    result={'schema':SCHEMA,'phase':'blind_lora_probe',
            'dataset_sha256':manifest['dataset_sha256'],
            'adapter_sha256':report['best_adapter_sha256'],
            'blind_scale_schedule':'training 1/10/100/1000; test 10000/100000/1000000',
            'assessment':'2x2 best-response table translation only; not arbitrary game theory',
            'score':score}
    atomic_json(out/'blind_probe.json',result)
    return result


def parser():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stage',choices=('prepare','validate','lora','switch','to-numerical','probe','blind-probe','experiment','shadow','route-accept','route-activate','route-rollback','route'))
    ap.add_argument('--dataset',help='Optional explicit path; by default OUTPUT/certified_pairs.jsonl')
    ap.add_argument('--output',required=True,help='GAMER run directory; read-only for shadow/route stages')
    ap.add_argument('--shadow-closure',help='shadow only: completed frozen S-closure report directory')
    ap.add_argument('--shadow-evidence',help='route: completed 160-case production shadow directory')
    ap.add_argument('--route-report',help='route: isolated serving-route acceptance report directory')
    ap.add_argument('--authority-file',help='route: reversible authority state (outside frozen production run)')
    ap.add_argument('--route-demo',action='store_true',help='route: serve representative canonical GAMER requests')
    ap.add_argument('--route-request-file',help='route: JSONL file of {id,prompt} requests')
    ap.add_argument('--route-response-file',help='route: isolated JSONL output file')
    ap.add_argument('--shadow-wide',help='shadow only: explicit Wide checkpoint, otherwise latest completed staircase head cycle')
    ap.add_argument('--shadow-separate-adapters',action='store_true',help='shadow only: compare original Wide and candidate with separately attested frozen adapters')
    ap.add_argument('--shadow-report',help='shadow only: fresh, separate output directory (never within production run)')
    ap.add_argument('--shadow-plan-only',action='store_true',help='shadow only: validate lineage and report planned actions; no GPU')
    ap.add_argument('--model',default='Qwen/Qwen3-0.6B')
    ap.add_argument('--device',default='cuda')
    ap.add_argument('--seed',type=int,default=47)
    ap.add_argument('--include-catalog-extensions',action='store_true',
                    help='Opt in to 58 explicitly bounded unused-catalog mathematical objectives; does not add anonymous random games')
    ap.add_argument('--include-transition-games',action='store_true',
                    help='Add the 55 named, finite one-step transition games AND the 58 existing bounded catalog entries (146 total); includes fourth table')
    ap.add_argument('--cases-per-game',type=int,default=18,
                    help='prepare: game instances per verified model; multiple of 6, default 18')
    ap.add_argument('--blind-matrix-per-game',type=int,default=0,
                    help='RETIRED: must be 0; no anonymous randomized payoff games')
    ap.add_argument('--allow-partial-smoke',action='store_true',help='Non-production small dataset; skips complete selected-registry coverage requirement')
    ap.add_argument('--snapshot-lora-from',help='switch only: copy a verified active-run best adapter and certified dataset into a NEW frozen numerical experiment')
    ap.add_argument('--snapshot-step',type=int,help='Required exact best-evaluation step when using --snapshot-lora-from; refuses a changed selection')
    ap.add_argument('--init-lora-from',help='Prior Gamer run to import weights from; uses a fresh optimizer for the new dataset')
    ap.add_argument('--init-lora-checkpoint',choices=('best','latest'),default='best',help='Choose best adapter or exact latest optimizer-step weights (latest enables cumulative steps beyond 200)')
    ap.add_argument('--lora-probe-cases',type=int,default=17,help='Transferred LoRA: bounded, fixed-anchor+rotating dev probes (default 17)')
    ap.add_argument('--resume',action='store_true',help='Resume interrupted baseline evaluation per prompt, or optimizer and RNG from last completed LoRA step')
    ap.add_argument('--allow-experimental',action='store_true',help='Explicitly accept unpromoted LoRA for isolated Wide smoke')
    ap.add_argument('--max-length',type=int,default=1024)
    ap.add_argument('--capture-batch',type=int,default=8)
    ap.add_argument('--eval-batch',type=int,default=64)
    ap.add_argument('--lora-steps',type=int,default=200,help='TOTAL cumulative optimizer step target; can increase with --resume, including beyond 200')
    ap.add_argument('--lora-rank',type=int,default=16)
    ap.add_argument('--lora-batch',type=int,default=1)
    ap.add_argument('--lora-accum',type=int,default=8)
    ap.add_argument('--lora-lr',type=float,default=1e-4)
    ap.add_argument('--lora-eval-every',type=int,default=50)
    ap.add_argument('--lora-save-every',type=int,default=1,help='Save the exact LoRA optimizer/RNG state every N completed steps; default 1')
    ap.add_argument('--max-new-tokens',type=int,default=512)
    ap.add_argument('--init-head-from',help='Prior numerical cycle to import its exact latest head weights; NEW optimizer on changed LoRA activations')
    ap.add_argument('--head-save-every',type=int,default=1,help='Commit head optimizer and RNG every N steps; default 1 for interrupt-safe alternating training')
    ap.add_argument('--head-steps',type=int,default=1200)
    ap.add_argument('--zero-line-focus',type=float,default=None,
                    help='to-numerical: proportion of sampling drawn from balanced '
                         'exact-zero/small-negative/small-positive certified training answers; '
                         '0 is uniform, 0.75 means 75%% focused. Omitted on resume '
                         'inherits the saved sampling policy.')
    ap.add_argument('--zero-line-radius',type=float,default=None,
                    help='to-numerical: maximum absolute certified training answer '
                         'in the focused nonzero bands (default 1.0); does not '
                         'change the dataset or held-out evaluations.')
    ap.add_argument('--head-batch',type=int,default=32)
    ap.add_argument('--head-eval-every',type=int,default=100)
    ap.add_argument('--head-lr',type=float,default=2e-4)
    ap.add_argument('--weight-decay',type=float,default=1e-4)
    ap.add_argument('--aux-weight',type=float,default=.25)
    ap.add_argument('--huber-beta',type=float,default=.1)
    ap.add_argument('--grad-clip',type=float,default=1.)
    return ap


def validate_flags(args):
    from tools.nanojev_gamer_v1_zero_line import resolve_policy
    focus=getattr(args,'zero_line_focus',None)
    radius=getattr(args,'zero_line_radius',None)
    resolve_policy(focus=focus,radius=radius)
    if getattr(args,'stage',None) in ('route','route-accept','route-activate','route-rollback'):
        if any((getattr(args,'shadow_wide',None),getattr(args,'shadow_report',None),
                getattr(args,'shadow_plan_only',False),getattr(args,'shadow_separate_adapters',False))):
            raise ValueError('GAMER_ROUTE_CANNOT_USE_SHADOW_MUTATION_FLAGS')
        return
    if any(getattr(args,f,None) for f in ('shadow_evidence','route_report','authority_file',
                                         'route_demo','route_request_file','route_response_file')):
        raise ValueError('GAMER_ROUTE_FLAGS_ONLY_ROUTE_STAGE')
    if getattr(args,'stage',None) != 'shadow' and any((getattr(args,'shadow_closure',None),getattr(args,'shadow_wide',None),getattr(args,'shadow_report',None),getattr(args,'shadow_plan_only',False),getattr(args,'shadow_separate_adapters',False))):
        raise ValueError('GAMER_SHADOW_FLAGS_ONLY_SHADOW_STAGE')
    if getattr(args,'stage',None)=='shadow' and not (getattr(args,'shadow_closure',None) and getattr(args,'shadow_report',None)):
        raise ValueError('GAMER_SHADOW_REQUIRES_CLOSURE_AND_SEPARATE_REPORT')
    if getattr(args,'stage',None) not in (None,'to-numerical') and (focus is not None or radius is not None):
        raise ValueError('GAMER_ZERO_LINE_FOCUS_ONLY_TO_NUMERICAL')
    if bool(getattr(args,'snapshot_lora_from',None)) != (getattr(args,'snapshot_step',None) is not None):
        raise ValueError('GAMER_ACTIVE_SNAPSHOT_REQUIRES_BOTH_SOURCE_AND_STEP')
    if getattr(args,'snapshot_lora_from',None):
        if (args.stage!='switch' or getattr(args,'dataset',None) is not None or
            getattr(args,'resume',False) or not getattr(args,'allow_experimental',False)):
            raise ValueError('GAMER_ACTIVE_SNAPSHOT_SWITCH_ONLY_NEW_RUN_ALLOW_EXPERIMENTAL')
    for field in ('lora_steps','lora_rank','lora_batch','lora_accum','lora_eval_every','max_new_tokens',
                  'head_steps','head_batch','head_eval_every','capture_batch','eval_batch','max_length'):
        if getattr(args,field)<1:
            raise ValueError(f'GAMER_INVALID_POSITIVE_FLAG: {field}')
    if getattr(args,'blind_matrix_per_game',0):
        raise ValueError('GAMER_BLIND_MATRIX_RETIRED: prepare only verified games')
    if getattr(args,'init_lora_checkpoint','best')!='best' and not getattr(args,'init_lora_from',None):
        raise ValueError('GAMER_INIT_LATEST_REQUIRES_INIT_LORA_FROM')
    if getattr(args,'lora_save_every',1)<1:
        raise ValueError('GAMER_INVALID_LORA_SAVE_EVERY')
    if getattr(args,'head_save_every',1)<1:
        raise ValueError('GAMER_INVALID_HEAD_SAVE_EVERY')
    if getattr(args,'init_head_from',None) and args.stage!='to-numerical':
        raise ValueError('GAMER_HEAD_TRANSFER_ONLY_TO_NUMERICAL')
    if getattr(args,'init_head_from',None) and getattr(args,'resume',False) and not (Path(args.output)/'to_numerical'/'latest.pt').is_file():
        raise ValueError('GAMER_HEAD_TRANSFER_CANNOT_RESUME_BEFORE_INITIALIZATION')
    if getattr(args,'lora_probe_cases',17)<4:
        raise ValueError('GAMER_LORA_PROBE_CASES_MUST_BE_AT_LEAST_FOUR')
    if args.lora_rank%2 or args.lora_rank>256:
        raise ValueError('GAMER_LORA_RANK_INVALID')
    if not (0 < args.lora_lr < .1 and 0 < args.head_lr < .1):
        raise ValueError('GAMER_INVALID_LEARNING_RATE')


def main(argv=None):
    args = parser().parse_args(argv)
    validate_flags(args)
    if args.stage in ('route','route-accept','route-activate','route-rollback'):
        from tools import nanojev_gamer_v1_to_numerical_serve as route
        return route.main([args.stage, '--output', str(args.output)] +
            sum(([flag,str(getattr(args,name))] for name,flag in (
                ('shadow_closure','--shadow-closure'),('shadow_evidence','--shadow-evidence'),
                ('route_report','--route-report'),('authority_file','--authority-file'),
                ('route_request_file','--route-request-file'),
                ('route_response_file','--route-response-file')) if getattr(args,name,None)),[]) +
            (['--route-demo'] if args.route_demo else []) +
            ['--capture-batch',str(args.capture_batch),'--eval-batch',str(args.eval_batch),
             '--device',str(args.device)])
    if args.stage == 'shadow':
        # Integrate the shadow with the real to_numerical CLI; do not create a
        # manifest, train, or overwrite existing handoff/production artifacts.
        from tools import nanojev_gamer_v1_to_numerical_shadow as shadow
        return shadow.run(args)
    if getattr(args,'snapshot_lora_from',None):
        snapshot_active_lora(args)
    if args.dataset is None:
        args.dataset=str(Path(args.output)/'certified_pairs.jsonl')
    if args.stage == 'experiment' and not Path(args.dataset).exists():
        from tools.nanojev_gamer_v1_pairs import write_dataset
        if (Path(args.output)/'handoff_manifest.json').exists():
            raise ValueError('GAMER_EXISTING_MANIFEST_WITHOUT_DATASET: do not recreate lineage')
        prepared=write_dataset(args.dataset,cases_per_game=args.cases_per_game,seed=args.seed,
                               blind_cases_per_matrix=args.blind_matrix_per_game,
                               include_catalog_extensions=args.include_catalog_extensions,
                               include_transition_games=args.include_transition_games)
        print(f'GAMER DATA READY: {prepared["games"]} games, {prepared["cases"]} verified pairs',flush=True)
    if args.stage == 'prepare':
        from tools.nanojev_gamer_v1_pairs import write_dataset
        stats=write_dataset(args.dataset,cases_per_game=args.cases_per_game,seed=args.seed,
                               blind_cases_per_matrix=args.blind_matrix_per_game,
                               include_catalog_extensions=args.include_catalog_extensions,
                               include_transition_games=args.include_transition_games)
        _, manifest=ensure_manifest(args.output,args.dataset,args.model,False,
                                    include_catalog_extensions=args.include_catalog_extensions,
                               include_transition_games=args.include_transition_games)
        if manifest['provenance']!='source_recomputed':
            raise ValueError('GAMER_GENERATOR_VERIFICATION_MISSING')
        result={'status':'PASS_PREPARE_AND_CERTIFY','generation':stats,'manifest':manifest}
        print(json.dumps(result,sort_keys=True,ensure_ascii=False,allow_nan=False))
        return result
    cases, manifest = ensure_manifest(args.output,args.dataset,args.model,args.allow_partial_smoke,
                                      include_catalog_extensions=args.include_catalog_extensions,
                               include_transition_games=args.include_transition_games)
    if args.stage == 'validate':
        result={'status':'PASS_SOURCE_RECOMPUTED_CERTIFICATION' if manifest['provenance']=='source_recomputed' else 'PASS_SELF_ATTESTED_CONTRACT_ONLY', 'manifest':manifest}
    elif args.stage == 'experiment':
        if manifest['provenance'] != 'source_recomputed':
            raise ValueError('GAMER_EXPERIMENT_REQUIRES_ORACLE_RECOMPUTED_DATASET')
        root=Path(args.output)
        # Resume is an explicit choice, so a partial experiment cannot quietly
        # discard optimizer state or overwrite a finalized adapter.
        if (root/'lora'/'report.json').is_file():
            lora=json.loads((root/'lora'/'report.json').read_text(encoding='utf-8'))
        else:
            print('GAMER STAGE 1: Qwen LoRA learning original *08 prompts to mathematical tables',flush=True)
            lora=train_lora(args,cases,manifest)
        print('GAMER SWITCH: freezing and fingerprinting LoRA adapter',flush=True)
        switched=switch_to_numerical(args,manifest)
        if (root/'to_numerical'/'report.json').is_file():
            head=json.loads((root/'to_numerical'/'report.json').read_text(encoding='utf-8'))
        else:
            print('GAMER STAGE 2: training existing C0 Wide from original-prompt activations',flush=True)
            head=train_to_numerical(args,cases,manifest)
        print('GAMER PROBE: evaluating numerical predictions',flush=True)
        result={'phase':'experiment','lora':{'best':lora['best'],'handoff_eligible':lora['handoff_eligible']},
                'switch':switched['status'],'to_numerical':head['test'],'probe':probe(args,cases,manifest)['test']}
    elif args.stage == 'lora':
        result = train_lora(args,cases,manifest)
    elif args.stage == 'blind-probe':
        raise ValueError('GAMER_BLIND_MATRIX_RETIRED: use the named-game development probes')
    elif args.stage == 'switch':
        result = switch_to_numerical(args,manifest)
    elif args.stage == 'to-numerical':
        result = train_to_numerical(args,cases,manifest)
    else:
        result = probe(args,cases,manifest)
    print(json.dumps(result,sort_keys=True,ensure_ascii=False,allow_nan=False))
    return result


if __name__ == '__main__':
    main()
