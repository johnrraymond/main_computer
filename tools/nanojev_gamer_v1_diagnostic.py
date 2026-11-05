#!/usr/bin/env python3
"""Permanent, read-only Gamer v1 run diagnostic.

    python -m tools.nanojev_gamer_v1_diagnostic --run runtime/reports/gamer-v1/transition-005
    python -m tools.nanojev_gamer_v1_diagnostic --run runtime/reports/gamer-v1/transition-005 --watch 60
    python -m tools.nanojev_gamer_v1_diagnostic --run runtime/reports/gamer-v1/transition-005 --json
    python -m tools.nanojev_gamer_v1_diagnostic --run runtime/reports/gamer-v1/transition-005 --interference
    python -m tools.nanojev_gamer_v1_diagnostic --run runtime/reports/gamer-v1/transition-005 --interference-optimizer

Inspects on-disk checkpoints and individual resumable evaluation records. Does not
load Qwen, change the dataset, evaluate new examples, or write into the run.
The optional --interference and --interference-optimizer switches perform
CPU-only controlled updates on disposable head copies and write evidence
OUTSIDE the active run. The optimizer comparison does not run the full matrix
unless --interference is also explicitly selected.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
import pickle
from pathlib import Path
import sys
import time
from contextlib import contextmanager

SCHEMA = 'nanojev.gamer.v1.run-diagnostic.v1'


def _read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def _sha(path: Path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(part)
    return digest.hexdigest()


def _age(path: Path):
    return round(max(0, time.time() - path.stat().st_mtime), 1)


def _fmt_age(seconds):
    if seconds is None:
        return 'unknown'
    if seconds < 120:
        return f'{seconds:.0f}s ago'
    return f'{seconds / 60:.1f}min ago'


def _deck_sha(deck):
    from tools.nanojev_gamer_v1_handoff import canonical, original_prompt, sha_bytes
    return sha_bytes(canonical([
        (r['game_id'], original_prompt(r), r['math_prompt']) for r in deck
    ]).encode('utf-8'))


def _groups(row):
    if row.get('bounded_transition_objective'):
        return 'transition'
    if row.get('bounded_catalog_objective'):
        return 'catalog'
    return 'native'


def _stats():
    return {'correct': 0, 'evaluated': 0}


def _grade_by(rows, matches, key_fn):
    groups = defaultdict(_stats)
    for row, matched in zip(rows, matches):
        keys = key_fn(row)
        if isinstance(keys, str):
            keys = [keys]
        for key in keys:
            item = groups[key]
            item['evaluated'] += 1
            item['correct'] += int(matched)
    return dict(sorted(groups.items()))


@contextmanager
def _open_checkpoint_shared(path):
    """On Windows, share DELETE so training's os.replace is not blocked.

    The trainer writes complete temp files and atomically replaces latest.pt.
    Normal Windows Python opens can prevent a simultaneous replacement; a
    FILE_SHARE_DELETE handle avoids that interference.
    """
    if os.name != 'nt':
        with path.open('rb') as stream:
            yield stream
        return
    import ctypes
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                       ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                       ctypes.c_void_p]
    create.restype = ctypes.c_void_p
    # GENERIC_READ, FILE_SHARE_READ|WRITE|DELETE, OPEN_EXISTING
    handle = create(str(path.resolve()), 0x80000000, 0x1 | 0x2 | 0x4,
                    None, 3, 0x80, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(handle)
        raise
    with os.fdopen(fd, 'rb') as stream:
        yield stream


def _safe_load_checkpoint(path, errors):
    """Load CPU-only state. The trainer publishes checkpoints via atomic replace.

    No model initialization or device allocation. An inaccessible/mid-swap
    checkpoint produces a diagnostic warning, not a failure of the trainer.
    """
    try:
        import torch
    except ImportError:
        errors.append('Torch unavailable: checkpoint internals cannot be read; evaluation files remain accessible.')
        return None
    try:
        # Never touch CUDA or the running optimizer. An atomic replaced file is
        # either a previous complete snapshot or a later complete snapshot.
        with _open_checkpoint_shared(path) as stream:
            state = torch.load(stream, map_location='cpu', weights_only=True)
        if not isinstance(state, dict) or state.get('phase') != 'lora':
            errors.append('Checkpoint is not a recognized Gamer LoRA state.')
            return None
        return state
    except (OSError, RuntimeError, ValueError, EOFError, KeyError, pickle.UnpicklingError, TypeError) as exc:
        errors.append('Unable to read live checkpoint (' + type(exc).__name__ + '); retry when atomic replacement completes.')
        return None


def _extract_checkpoint(state):
    if state is None:
        return None
    h = state.get('hyperparameters') or {}
    bootstrap = state.get('bootstrap') or {}
    score = state.get('best_score')
    return {
        'step': state.get('step'),
        'pending_evaluation': bool(state.get('pending_eval')),
        'last_microbatch_loss': state.get('last_step_loss'),
        'best_step': state.get('best_step'),
        'best_anchor_score': score,
        'history_count': len(state.get('history') or []),
        'evaluation_loss_snapshots': [
            {'step': h.get('step'), 'last_microbatch_loss': h.get('loss_last_microbatch')}
            for h in (state.get('history') or []) if isinstance(h, dict)
        ],
        'sampling_counts': state.get('sampling_counts') or {},
        'sampling_focus': state.get('sampling_focus') or {},
        'source_run': bootstrap.get('source_run'),
        'source_step': bootstrap.get('source_checkpoint_step'),
        'source_checkpoint_kind': bootstrap.get('checkpoint_kind'),
        'source_sha256': bootstrap.get('checkpoint_sha256', bootstrap.get('adapter_sha256')),
        'max_new_tokens': h.get('max_new_tokens'),
        'evaluation_interval': h.get('lora_eval_every'),
        'probe_cases': h.get('lora_probe_cases'),
        'transferred_adapter': bool(bootstrap),
        'dataset_sha256': state.get('dataset_sha256'),
        'hyperparameters': h,
    }


def _resolve_deck(progress, step, dev, checkpoint):
    """Accept per-game attribution ONLY when exact stored deck hash matches."""
    if not dev or not isinstance(progress.get('deck_sha256'), str):
        return None, None
    deck_hash = progress['deck_sha256']
    if _deck_sha(dev) == deck_hash:
        return dev, 'full-dev'
    if not checkpoint:
        return None, None
    interval = checkpoint.get('evaluation_interval')
    budget = checkpoint.get('probe_cases')
    if not isinstance(interval, int) or interval < 1:
        return None, None
    if not isinstance(budget, int):
        # Older transferred checkpoints recorded no explicit budget.
        budget = 17
    try:
        from tools.nanojev_gamer_v1_handoff import fast_probe_deck
        deck, anchors = fast_probe_deck(
            dev, budget=budget, evaluation_index=max(0, (step - 1) // interval))
    except (ValueError, IndexError, KeyError):
        return None, None
    if deck_hash == _deck_sha(deck):
        return deck, anchors
    return None, None


def _evaluation(path, dev, checkpoint, warnings):
    try:
        p = _read_json(path)
    except (OSError, ValueError, UnicodeError):
        warnings.append(f'Invalid or inaccessible evaluation: {path.name}')
        return None
    try:
        step = int(path.stem.split('_')[-1])
    except (ValueError, IndexError):
        warnings.append(f'Unexpected evaluation filename: {path.name}')
        return None
    matches = p.get('matches')
    if not isinstance(matches, list) or not all(type(x) is bool for x in matches):
        warnings.append(f'Invalid evaluation matches: {path.name}')
        return None
    deck, anchors = _resolve_deck(p, step, dev, checkpoint)
    if deck is not None and len(matches) > len(deck):
        warnings.append(f'Invalid evaluation length: {path.name}')
        return None
    result = {
        'step': step,
        'correct': sum(matches),
        'evaluated': len(matches),
        'expected': len(deck) if deck is not None else None,
        'complete': bool(deck is not None and len(deck) == len(matches)),
        'deck_verified': deck is not None,
        'deck_type': 'full-dev' if anchors == 'full-dev' else 'bounded-probe' if deck is not None else 'unknown',
        'checkpoint_binding_recorded': isinstance(p.get('key'), str) and len(p.get('key')) == 64,
        'file': str(path),
        'updated_seconds_ago': _age(path),
        'by_group': {}, 'by_table': {}, 'by_game': {},
        'anchors': None,
    }
    if deck is not None:
        rows = deck[:len(matches)]
        result['by_group'] = _grade_by(rows, matches, _groups)
        result['by_table'] = _grade_by(rows, matches, lambda r: r.get('table_families') or ['unknown'])
        result['by_game'] = _grade_by(rows, matches, lambda r: r['game_id'])
        if isinstance(anchors, int):
            used = min(anchors, len(matches))
            result['anchors'] = {
                'correct': sum(matches[:used]),
                'evaluated': used,
                'expected': anchors,
                'complete': used == anchors,
                'deck_sha256': _deck_sha(deck[:anchors]),
            }
    else:
        warnings.append(f'Step {step}: evaluation deck hash cannot be matched; game/table attribution withheld.')
    return result


def _head_error_atlases(run, manifest, all_rows, cycles, warnings, *,
                        atlas_cycles='latest', atlas_weights='both',
                        atlas_split='both', atlas_csv=None):
    """Re-evaluate saved head tensors on cached original-prompt features (CPU only).

    Works for PRE-v18 training cycles: existing latest.pt and feature_cache are
    sufficient; no Qwen loading, no GPU, no change to the active run.
    """
    result=[]
    if not all_rows or not manifest or not cycles:
        return result
    if not (run/'handoff_manifest.json').is_file():
        return result
    if atlas_cycles=='latest':
        cycles=[cycles[-1]]
    try:
        import numpy as np
        import torch
        from tools.nanojev_gamer_v1_error_atlas import build_error_atlas, export_csv
        from tools.nanojev_qwen_c0_compressive_train import VARIANTS,make_network,eval_heads
    except ImportError as exc:
        warnings.append(f'NUMERICAL_ATLAS_MODULE_UNAVAILABLE: {type(exc).__name__} {exc}')
        return result
    dataset_sha=manifest.get('dataset_sha256')
    requested_splits=['dev','test'] if atlas_split=='both' else [atlas_split]
    requested_weights=['latest','best'] if atlas_weights=='both' else [atlas_weights]
    for folder,record in cycles:
        ckpt=folder/'to_numerical'/'latest.pt'
        if not ckpt.is_file():
            warnings.append(f'NUMERICAL_ATLAS_NO_HEAD_CHECKPOINT: {folder.name}')
            continue
        try:
            with _open_checkpoint_shared(ckpt) as stream:
                state=torch.load(stream,map_location='cpu',weights_only=True)
            if state.get('dataset_sha256')!=dataset_sha or state.get('phase')!='to_numerical':
                raise ValueError('HEAD_CHECKPOINT_LINEAGE_MISMATCH')
            frozen=_read_json(folder/'lora'/'report.json')
            adapter_sha=frozen['best_adapter_sha256']
            if adapter_sha != state['adapter_sha256'] or record.get('source_step')!=frozen['best_step']:
                raise ValueError('HEAD_CHECKPOINT_ADAPTER_MISMATCH')
            model_states={'latest':state['model'], 'best':state.get('best_weights')}
            for split in requested_splits:
                cases=[row for row in all_rows if row['split']==split]
                cache=folder/'to_numerical'/'feature_cache'/f'{split}.npz'
                if not cache.is_file():
                    warnings.append(f'NUMERICAL_ATLAS_FEATURE_CACHE_MISSING: {folder.name}/{split}')
                    continue
                # Share DELETE on Windows; training saves its cache atomically.
                with _open_checkpoint_shared(cache) as stream:
                    with np.load(stream,allow_pickle=False) as saved:
                        identity=json.loads(str(saved['identity'].item()))
                        features=saved['features'].copy()
                if (identity.get('dataset_sha256')!=dataset_sha or
                    identity.get('adapter_sha256')!=adapter_sha or
                    identity.get('original_prompt_corpus_sha256')!=manifest.get('original_prompt_corpus_sha256') or
                    features.ndim!=2 or len(features)!=len(cases) or
                    not np.isfinite(features).all()):
                    raise ValueError('FEATURE_CAPTURE_LINEAGE_MISMATCH')
                hp=state.get('hyperparameters') or {}
                for kind in requested_weights:
                    tensors=model_states.get(kind)
                    if tensors is None:continue
                    head=make_network(torch,features.shape[1],VARIANTS['wide'])
                    head.load_state_dict(tensors,strict=True)
                    head.eval()
                    preds,_=eval_heads(torch,head,features,None,0.,1.,'cpu',
                                       int(hp.get('eval_batch') or 32))
                    step=state['step'] if kind=='latest' else state.get('best_step',0)
                    atlas=build_error_atlas(cases,preds,split=split,step=step,
                        adapter_sha256=adapter_sha,dataset_sha256=dataset_sha,
                        checkpoint_kind=kind)
                    if atlas_csv:
                        target=Path(atlas_csv).resolve()
                        fullrun=run.resolve()
                        if target==fullrun or fullrun in target.parents:
                            raise ValueError('GAMER_DIAGNOSTIC_CSV_MUST_BE_OUTSIDE_ACTIVE_RUN')
                        export_csv(atlas,target/f'{folder.name}_{kind}_{split}.csv')
                    # JSON carries the full per-zone/per-game map; CSV is opt-in.
                    result.append({'cycle':folder.name,'source_lora_step':record['source_step'],
                                   'kind':kind,'split':split,'head_step':step,
                                   'atlas':{k:v for k,v in atlas.items() if k!='records'}})
        except (OSError,KeyError,ValueError,TypeError,RuntimeError,EOFError,pickle.UnpicklingError) as exc:
            warnings.append(f'NUMERICAL_ATLAS_UNAVAILABLE: {folder.name} ({type(exc).__name__}: {exc})')
    return result


def _atlas_trends(entries):
    """Compare identical held-out decks across evaluated weights and LoRA cycles."""
    changes=[]
    def diff(a,b,axis):
        left=a['atlas'];right=b['atlas']
        if (left['dataset_sha256']!=right['dataset_sha256'] or
            left['split']!=right['split']):
            return None
        before=left[axis];after=right[axis]
        names=sorted(set(before)|set(after))
        cells=[]
        for name in names:
            x=before.get(name);y=after.get(name)
            if x and y and x['cases']==y['cases']:
                cells.append({'band':name,'cases':x['cases'],
                              'mae_before':x['mae'],'mae_after':y['mae'],
                              'delta_mae':y['mae']-x['mae'],
                              'p90_before':x['p90_abs_error'],'p90_after':y['p90_abs_error'],
                              'delta_p90':y['p90_abs_error']-x['p90_abs_error'],
                              'delta_within_1':y['within_1']-x['within_1']})
        return cells
    for split in ('dev','test'):
        candidates=[x for x in entries if x['split']==split]
        # Compare best and latest inside the same head cycle, if both exist.
        for cycle in sorted({x['cycle'] for x in candidates}):
            best=next((x for x in candidates if x['cycle']==cycle and x['kind']=='best'),None)
            latest=next((x for x in candidates if x['cycle']==cycle and x['kind']=='latest'),None)
            if best and latest:
                changes.append({'scope':'best_vs_latest', 'split':split,
                                'from':f'{cycle}:best','to':f'{cycle}:latest',
                                'by_target_zone':diff(best,latest,'by_target_zone'),
                                'by_residual_zone':_atlas_residual_delta(best,latest),
                                'mae_delta':latest['atlas']['summary']['mae']-best['atlas']['summary']['mae']})
        # Across head cycles on the SAME certified dataset. Compare latest
        # weights from each cycle; the input LoRA is intentionally different.
        latest=[x for x in candidates if x['kind']=='latest']
        for before,after in zip(latest,latest[1:]):
            changes.append({'scope':'staircase_cycle_to_cycle','split':split,
                            'from':f'{before["cycle"]}:latest','to':f'{after["cycle"]}:latest',
                            'by_target_zone':diff(before,after,'by_target_zone'),
                            'by_residual_zone':_atlas_residual_delta(before,after),
                            'mae_delta':after['atlas']['summary']['mae']-before['atlas']['summary']['mae']})
    return changes


def _atlas_residual_delta(before,after):
    a=before['atlas']['zero_line_residual']['by_zone']
    b=after['atlas']['zero_line_residual']['by_zone']
    cells=[]
    for name in sorted(set(a)|set(b)):
        x=a.get(name); y=b.get(name)
        if x and y and x['cases']==y['cases']:
            cells.append({'band':name,'cases':x['cases'],
                          'mae_before':x['mae'],'mae_after':y['mae'],
                          'delta_mae':y['mae']-x['mae'],
                          'delta_p90':y['p90_abs_error']-x['p90_abs_error']})
    return cells


def diagnose(run, *, dataset=None, atlas_cycles='latest', atlas_weights='both',
             atlas_split='both', atlas_csv=None, no_error_atlas=False):
    run = Path(run)
    warnings = []
    result = {
        'schema': SCHEMA,
        'run': str(run),
        'status': 'UNKNOWN',
        'read_only': True,
        'manifest': None,
        'dataset': None,
        'checkpoint': None,
        'checkpoint_file': None,
        'best_adapter': None,
        'evaluations': [],
        'latest_evaluation': None,
        'comparable_anchor_history': [],
        'full_retention_certified': False,
        'staircase_cycles': [],
        'numerical_error_atlases': [],
        'numerical_error_trends': [],
        'warnings': warnings,
    }
    manifest_path = run/'handoff_manifest.json'
    manifest = None
    if manifest_path.is_file():
        try:
            manifest = _read_json(manifest_path)
            result['manifest'] = {
                'games': manifest.get('number_of_verified_games'),
                'cases': manifest.get('number_of_cases'),
                'by_split': manifest.get('by_split'),
                'dataset_sha256': manifest.get('dataset_sha256'),
                'provenance': manifest.get('provenance'),
                'fourth_table': manifest.get('fourth_table_family'),
            }
        except (OSError, ValueError):
            warnings.append('Invalid handoff_manifest.json; cannot establish dataset lineage.')
    else:
        warnings.append('No handoff_manifest.json found.')
    cases_path = Path(dataset) if dataset is not None else run/'certified_pairs.jsonl'
    dev = []
    all_rows = []
    if cases_path.is_file():
        try:
            hash_value = _sha(cases_path)
            rows = [json.loads(line) for line in cases_path.read_text(encoding='utf-8').splitlines() if line.strip()]
            all_rows = rows
            dev = [row for row in rows if row.get('split') == 'dev']
            counts = Counter(row.get('split') for row in rows)
            ids = set(row.get('game_id') for row in rows)
            groups = Counter(_groups(row) for row in rows)
            table_counts = Counter(k for row in rows for k in row.get('table_families', []))
            consistent = bool(manifest and hash_value == manifest.get('dataset_sha256'))
            result['dataset'] = {
                'path': str(cases_path),
                'sha256': hash_value,
                'manifest_hash_matches': consistent,
                'cases': len(rows),
                'games': len(ids),
                'by_split': dict(counts),
                'by_group': dict(groups),
                'by_table': dict(table_counts),
                'dev_count': len(dev),
            }
            if manifest and not consistent:
                warnings.append('DATASET HASH MISMATCH: run lineage is not verified.')
        except (OSError, ValueError, KeyError, UnicodeError) as exc:
            warnings.append('Unable to parse certified dataset (' + type(exc).__name__ + ').')
    else:
        warnings.append('No certified_pairs.jsonl found; evaluation attribution unavailable.')
    lora_dir = run/'lora'
    checkpoint_path = lora_dir/'latest.pt'
    checkpoint = None
    if checkpoint_path.is_file():
        result['checkpoint_file'] = {
            'path': str(checkpoint_path),
            'bytes': checkpoint_path.stat().st_size,
            'updated_seconds_ago': _age(checkpoint_path),
        }
        state = _safe_load_checkpoint(checkpoint_path, warnings)
        checkpoint = _extract_checkpoint(state)
        result['checkpoint'] = checkpoint
        if checkpoint and manifest and checkpoint['dataset_sha256'] != manifest.get('dataset_sha256'):
            warnings.append('CHECKPOINT DATASET HASH DOES NOT MATCH MANIFEST.')
    else:
        warnings.append('No resumable LoRA latest.pt checkpoint found.')
    adapter_dir = lora_dir/'best_adapter'
    if adapter_dir.is_dir():
        adapter_files = sorted(p for p in adapter_dir.rglob('*') if p.is_file())
        result['best_adapter'] = {
            'exists': bool(adapter_files),
            'files': len(adapter_files),
            'selected_step': checkpoint.get('best_step') if checkpoint else None,
        }
    elif checkpoint and checkpoint.get('best_step', 0) > 0 and not checkpoint.get('pending_evaluation'):
        warnings.append('BEST_ADAPTER_MISSING: checkpoint records a best step but adapter directory is absent.')
    result['numerical_handoff_present'] = (run/'handoff.json').is_file()
    result['numerical_head_report_present'] = (run/'to_numerical'/'report.json').is_file()
    final_report = lora_dir/'report.json'
    if final_report.is_file():
        try:
            report = _read_json(final_report)
            result['final_report'] = {
                'best_step': report.get('best_step'),
                'best_anchor_score': report.get('best'),
                'evaluation_scope': report.get('evaluation_scope'),
                'retention_certification': report.get('retention_certification'),
                'handoff_eligible': report.get('handoff_eligible'),
            }
            result['status'] = 'COMPLETED'
            if result['best_adapter'] and report.get('best_adapter_sha256'):
                try:
                    from tools.nanojev_gamer_v1_handoff import file_tree_sha256
                    actual = file_tree_sha256(adapter_dir)
                    result['best_adapter']['reported_sha_matches'] = actual == report['best_adapter_sha256']
                    if actual != report['best_adapter_sha256']:
                        warnings.append('BEST_ADAPTER_SHA_MISMATCH: saved adapter differs from completed report.')
                except (OSError, ValueError) as exc:
                    warnings.append('Unable to verify best adapter SHA (' + type(exc).__name__ + ').')
            result['full_retention_certified'] = report.get('retention_certification') == 'PASS'
        except (OSError, ValueError):
            warnings.append('Final LoRA report is unreadable.')
    elif checkpoint is not None:
        result['status'] = 'CHECKPOINTED_ACTIVE_OR_INTERRUPTED'
    elif checkpoint_path.is_file():
        result['status'] = 'CHECKPOINT_EXISTS_UNREADABLE'
    else:
        result['status'] = 'NO_TRAINING_CHECKPOINT'
    for file in sorted(lora_dir.glob('eval_step_*.json')):
        entry = _evaluation(file, dev, checkpoint, warnings)
        if entry is not None:
            result['evaluations'].append(entry)
    if result['evaluations']:
        result['latest_evaluation'] = result['evaluations'][-1]
        anchors = {}
        for item in result['evaluations']:
            a = item.get('anchors')
            if a and a['complete']:
                anchors.setdefault(a['deck_sha256'], []).append({
                    'step': item['step'], 'correct': a['correct'], 'total': a['expected']})
        result['comparable_anchor_history'] = max(anchors.values(), key=len) if anchors else []
        last = result['latest_evaluation']
        if last['complete'] and last['evaluated'] >= 5 and last['correct'] / last['evaluated'] < 0.5:
            warnings.append('LOW_BOUNDED_PROBE_ACCURACY: under 50% exact reconstruction; not evidence of successful retention.')
        if last['deck_type'] == 'bounded-probe':
            warnings.append(f'BOUNDED_COVERAGE: latest probe checked {last["evaluated"]}/{len(dev)} dev cases; not full retention certification.')
    if checkpoint and checkpoint['pending_evaluation']:
        warnings.append('Evaluation pending/in progress at checkpoint step; resumable per-prompt results may still be accumulating.')
    if checkpoint and checkpoint.get('best_step') and checkpoint.get('best_step') != checkpoint.get('step'):
        warnings.append('BEST_ADAPTER_DIFFERS_FROM_LATEST: best selection uses the fixed anchor score, not the full rotating probe.')
    if len(result['comparable_anchor_history']) >= 2:
        a, b = result['comparable_anchor_history'][-2:]
        if b['correct'] < a['correct'] and b['total'] == a['total']:
            warnings.append(f'FIXED_ANCHOR_REGRESSION: {a["step"]}={a["correct"]}/{a["total"]} to {b["step"]}={b["correct"]}/{b["total"]}.')
    for cycle in sorted((run/'staircase').glob('head_cycle_*')):
        if not cycle.is_dir():
            continue
        metadata=cycle/'staircase_cycle.json'
        if not metadata.is_file():
            warnings.append(f'STAIRCASE_CYCLE_UNVERIFIED: {cycle.name} lacks lineage metadata.')
            continue
        try:
            record=_read_json(metadata)
            entry={'cycle':cycle.name,'source_lora_step':record.get('source_step'),
                   'head_source':record.get('head_from'),'head_step':None,
                   'dev_mae':None,'best_step':None,'finished':False,
                   'source_adapter_sha256':None}
            ckpt=cycle/'to_numerical'/'latest.pt'
            if ckpt.is_file():
                import torch
                with _open_checkpoint_shared(ckpt) as stream:
                    head=torch.load(stream,map_location='cpu',weights_only=True)
                if (head.get('schema')!='nanojev.gamer.v1.original-prompt-shared-handoff.v1'
                    or head.get('phase')!='to_numerical'):
                    warnings.append(f'STAIRCASE_HEAD_CHECKPOINT_INVALID: {cycle.name}.')
                else:
                    entry.update(head_step=head.get('step'),dev_mae=head.get('best'),
                                 best_step=head.get('best_step'),
                                 source_adapter_sha256=head.get('adapter_sha256'))
            entry['finished']=(cycle/'to_numerical'/'report.json').is_file()
            result['staircase_cycles'].append(entry)
        except (OSError,RuntimeError,ValueError,EOFError,KeyError,pickle.UnpicklingError,TypeError) as exc:
            warnings.append(f'STAIRCASE_CYCLE_UNREADABLE: {cycle.name} ({type(exc).__name__}).')
    if result['staircase_cycles'] and not no_error_atlas:
        usable=[]
        for cycle in sorted((run/'staircase').glob('head_cycle_*')):
            metadata=cycle/'staircase_cycle.json'
            if cycle.is_dir() and metadata.is_file():
                try:usable.append((cycle,_read_json(metadata)))
                except (OSError,ValueError):pass
        if result['dataset'] and result['dataset']['manifest_hash_matches']:
            result['numerical_error_atlases']=_head_error_atlases(
                run,manifest,all_rows,usable,warnings,
                atlas_cycles=atlas_cycles,atlas_weights=atlas_weights,
                atlas_split=atlas_split,atlas_csv=atlas_csv)
            result['numerical_error_trends']=_atlas_trends(result['numerical_error_atlases'])
        else:
            warnings.append('NUMERICAL_ATLAS_REQUIRES_MATCHING_CERTIFIED_DATASET')
    if result['status'] == 'COMPLETED' and not result['full_retention_certified']:
        warnings.append('Completed training does not imply full-game retention certification.')
    return result


def _rate(stats):
    if not stats or not stats['evaluated']:
        return 'not measured'
    return f'{stats["correct"]}/{stats["evaluated"]} ({100 * stats["correct"] / stats["evaluated"]:.1f}%)'


def format_report(report):
    lines = [f'GAMER V1 RUN DIAGNOSTIC — {report["run"]}',
             f'Status: {report["status"]} (not a process-liveness check)']
    dataset = report['dataset'] or {}
    if dataset:
        lines.append(f'Dataset: {dataset["games"]} named entries, {dataset["cases"]} cases; '
                     f'{dataset["by_split"].get("train",0)} train / '
                     f'{dataset["by_split"].get("dev",0)} dev / '
                     f'{dataset["by_split"].get("test",0)} test; '
                     f'manifest SHA {"MATCH" if dataset["manifest_hash_matches"] else "UNVERIFIED"}')
        lines.append('Curriculum: ' + ', '.join(f'{k}={v}' for k,v in sorted(dataset['by_group'].items())))
    checkpoint = report['checkpoint']
    cf = report['checkpoint_file']
    if checkpoint:
        lines.append('Checkpoint: step {step}; pending_eval={pending_evaluation}; saved {age}'.format(
            **checkpoint, age=_fmt_age(cf['updated_seconds_ago']) if cf else 'unknown'))
        lines.append(f'Last microbatch loss: {checkpoint["last_microbatch_loss"]} '
                     '(NOT a run-wide average or validation accuracy)')
        lines.append(f'Best anchor checkpoint: step {checkpoint["best_step"]} '
                     f'(anchor score {checkpoint["best_anchor_score"]})')
        if checkpoint['source_run']:
            lines.append(f'Transferred from: {checkpoint["source_run"]} '
                         f'step={checkpoint["source_step"]} kind={checkpoint["source_checkpoint_kind"]}')
        if checkpoint['evaluation_loss_snapshots']:
            lines.append('Last-microbatch loss at completed evaluations: ' + ', '.join(
                f'{e["step"]}={e["last_microbatch_loss"]}' for e in checkpoint['evaluation_loss_snapshots'][-8:]))
        lines.append('Training selection counts: ' + ', '.join(
            f'{k}={v}' for k,v in sorted(checkpoint['sampling_counts'].items())))
    elif cf:
        lines.append('Checkpoint: exists but internals unavailable; saved ' + _fmt_age(cf['updated_seconds_ago']))
    else:
        lines.append('Checkpoint: absent')
    adapter = report.get('best_adapter')
    if adapter:
        lines.append('Best adapter: ' + ('present' if adapter['exists'] else 'EMPTY') +
                     (f', files={adapter["files"]}' if adapter['exists'] else '') +
                     (', report SHA ' + ('MATCH' if adapter['reported_sha_matches'] else 'MISMATCH')
                      if 'reported_sha_matches' in adapter else ''))
    if report['numerical_handoff_present'] or report['numerical_head_report_present']:
        lines.append('Handoff: present; numerical head report: ' +
                     ('present' if report['numerical_head_report_present'] else 'absent'))
    if report['evaluations']:
        lines.append('Development evaluations:')
        for e in report['evaluations']:
            status = 'complete' if e['complete'] else 'partial/unmapped'
            expected = e['expected'] if e['expected'] is not None else '?'
            lines.append(f'  step {e["step"]}: {e["correct"]}/{e["evaluated"]}/{expected} '
                         f'(correct/completed/expected), {status}, {e["deck_type"]}')
        latest = report['latest_evaluation']
        if latest['deck_verified']:
            lines.append(f'Latest step {latest["step"]} by provenance:')
            for k,v in latest['by_group'].items():
                lines.append(f'  {k}: {_rate(v)}')
            lines.append('Latest by table family (a case can count under multiple tables):')
            for k,v in latest['by_table'].items():
                lines.append(f'  {k}: {_rate(v)}')
            weak = sorted(latest['by_game'].items(), key=lambda kv: (kv[1]['correct'] / kv[1]['evaluated'],kv[0]))
            lines.append('Games missed in latest probe: ' + (
                ', '.join(k for k,v in weak if v['correct'] < v['evaluated']) or 'none in sampled deck'))
        if report['comparable_anchor_history']:
            lines.append('Comparable fixed-anchor exact (rotating cases excluded): ' + ', '.join(
                f'{v["step"]}={v["correct"]}/{v["total"]}'
                for v in report['comparable_anchor_history']))
    else:
        lines.append('Development evaluations: none available yet')
    if report.get('staircase_cycles'):
        lines.append('Numerical-head staircase (read-only):')
        for cycle in report['staircase_cycles']:
            lines.append(f'  {cycle["cycle"]}: LoRA={cycle["source_lora_step"]}, '
                         f'head={cycle["head_step"]}, best_dev_mae={cycle["dev_mae"]}, '
                         f'completed={cycle["finished"]}')
    if report.get('numerical_error_atlases'):
        from tools.nanojev_gamer_v1_error_atlas import describe
        lines.append('NUMERICAL HEAD ERROR GEOMETRY (CPU-only, read-only, complete held-out splits):')
        for entry in report['numerical_error_atlases']:
            lines.append(f'  {entry["cycle"]}: source LoRA={entry["source_lora_step"]}')
            lines.append(describe(entry['atlas']))
    for entry in report.get('numerical_error_trends',[]):
        lines.append(f'ERROR TREND ({entry["scope"]}, {entry["split"]}): '
                     f'{entry["from"]} -> {entry["to"]}; '
                     f'delta MAE={entry["mae_delta"]:+.5g} (negative means improvement)')
        for zone in entry['by_target_zone'] or []:
            lines.append(f'  target {zone["band"]}: n={zone["cases"]} '
                         f'delta MAE={zone["delta_mae"]:+.5g}, '
                         f'delta p90={zone["delta_p90"]:+.5g}, '
                         f'delta within1={zone["delta_within_1"]:+.1%}')
        for zone in entry['by_residual_zone'] or []:
            lines.append(f'  residual {zone["band"]}: n={zone["cases"]} '
                         f'delta MAE={zone["delta_mae"]:+.5g}')
    if report.get('interference_optimizer'):
        x=report['interference_optimizer']
        lines.append(f'OPTIMIZER INTERFERENCE COMPARISON (opt-in): cycle={x["cycle"]} '
                     f'head_step={x["head_step"]}, repeats={x["repeats"]}, '
                     f'sources={len(x["sources"])}')
        lines.append(f'  Starting optimizer history: steps '
                     f'{x["optimizer_history_steps_min"]}–{x["optimizer_history_steps_max"]}; '
                     'matched parameter-update norms across methods')
        for source in ('zone:small_positive','zone:small_negative','zone:zero'):
            comparisons=[e for e in x['summary']
                         if e['source']==source and e['target']==source]
            if comparisons:
                desc=', '.join(f'{v["mode"]}={v["mean_delta_mae"]:+.5g}'
                               for v in comparisons)
                lines.append(f'  {source} -> same zone (delta MAE): {desc}')
        lines.append(f'  Full paired trial report: {x["report"]}')
        lines.append(f'  Before/after predictions: {x["predictions_csv"]}')
        lines.append('  One-step comparison isolates optimizer effects, not long-run forgetting.')
    if report.get('interference'):
        x = report['interference']
        lines.append(f'INTERFERENCE DIAGNOSTIC (opt-in): cycle={x["cycle"]} '
                     f'head_step={x["head_step"]}, sources={x["source_groups"]}, '
                     f'repeats={x["repeats"]}')
        lines.append(f'  Baseline development MAE={x["baseline_dev_mae"]:.6g}; '
                     f'reproducibly damaged game-to-game pairs={x["reproduced_game_damage_pairs"]}')
        for pair in x['top_game_damage'][:8]:
            lines.append(f'  {pair["source"]} -> {pair["target"]}: '
                         f'delta MAE={pair["delta_mae"]:+.5g} '
                         f'({pair["cases"]} dev cases)')
        lines.append(f'  Complete matrix and gradients: {x["report"]}')
        lines.append(f'  Flat CSV matrix: {x["matrix_csv"]}')
        lines.append('  Controlled one-update effects diagnose local vulnerability; '
                     'they cannot identify past harmful minibatches.')
    lines.append('Full-game retention certification: ' +
                 ('PASS' if report['full_retention_certified'] else 'NOT ESTABLISHED'))
    if report['warnings']:
        lines.append('Findings:')
        lines.extend('  - ' + w for w in report['warnings'])
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', '--output', required=True, dest='run', help='Existing Gamer run directory')
    parser.add_argument('--json', action='store_true', help='Emit one JSON report instead of readable text')
    parser.add_argument('--dataset', help='Explicit certified JSONL path when not stored under the run directory')
    parser.add_argument('--watch', type=float, metavar='SECONDS', help='Repeat read-only diagnostics every N seconds; Ctrl+C to stop')
    parser.add_argument('--no-error-atlas', action='store_true', help='Skip CPU numerical-head predictions for faster LoRA-only inspection')
    parser.add_argument('--atlas-cycles', choices=('latest','all'),default='latest',help='Which staircase cycles to grade (default latest)')
    parser.add_argument('--atlas-weights', choices=('best','latest','both'),default='both',help='Grade selected best head, latest head, or both')
    parser.add_argument('--atlas-split', choices=('dev','test','both'),default='both')
    parser.add_argument('--atlas-csv',help='Explicit output DIRECTORY outside active run; writes one per-case CSV per chosen cycle/checkpoint/split')
    parser.add_argument('--interference', action='store_true',
                        help='Opt-in slow causal source-game/table/numeric-regime interference diagnostic (CPU; active run read-only)')
    parser.add_argument('--interference-optimizer', action='store_true',
                        help='Opt-in 3-way saved AdamW vs fresh AdamW vs plain gradient descent comparison; independent of full 179-source matrix')
    parser.add_argument('--interference-repeats',type=int,default=2,
                        help='Independent source minibatches per isolated update (default 2; >=2)')
    parser.add_argument('--interference-output',
                        help='Directory OUTSIDE active run for interference and/or optimizer reports (default sibling run-name-interference)')
    args = parser.parse_args(argv)
    if args.watch is not None and args.watch < 1:
        parser.error('--watch must be at least one second')
    if (args.interference or args.interference_optimizer) and args.watch is not None:
        parser.error('interference experiments cannot be combined with --watch')
    if args.interference_repeats < 2:
        parser.error('--interference-repeats must be at least 2')
    if not (args.interference or args.interference_optimizer) and args.interference_output:
        parser.error('--interference-output requires --interference or --interference-optimizer')
    try:
        while True:
            report = diagnose(args.run, dataset=args.dataset,
                atlas_cycles=args.atlas_cycles,atlas_weights=args.atlas_weights,
                atlas_split=args.atlas_split,atlas_csv=args.atlas_csv,
                no_error_atlas=args.no_error_atlas)
            if args.interference_optimizer:
                from tools.nanojev_gamer_v1_interference import run_optimizer_comparison
                report['interference_optimizer'] = run_optimizer_comparison(
                    args.run, dataset=args.dataset, output=args.interference_output,
                    repeats=args.interference_repeats,
                    progress=None if args.json else lambda line: print(line,flush=True))
            if args.interference:
                from tools.nanojev_gamer_v1_interference import run_interference
                report['interference'] = run_interference(
                    args.run, dataset=args.dataset, output=args.interference_output,
                    repeats=args.interference_repeats,
                    progress=None if args.json else lambda line: print(line,flush=True))
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False), flush=True)
            else:
                print(format_report(report), flush=True)
            if args.watch is None:
                return 0
            print('\n--- next diagnostic ---\n', flush=True)
            time.sleep(args.watch)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
