#!/usr/bin/env python3
"""Guarded continuous continuation of the existing frozen-Qwen precision-v2 head.

Does not change the mathematical trainer or fork a new optimizer lineage. Resumes
its exact latest.pt, keeps historical best checkpoints, and journals fixed-size
segments outside the trainer's results directory. Requires exclusive use of the
selected results root (do not concurrently run the standalone trainer).
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone

DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'independent_altreg_math_qwen_continuous_v1'
DEFAULT_SOURCE = Path.home() / 'NanoJev' / 'runs' / 'independent_altreg_math_qwen_weight0_control'
TRAINER_NAME = 'nanojev_altreg_independent_math_qwen_precision_v2.py'
CAMPAIGN_SCHEMA = 'nanojev.qwen-continuous.exact-resume.v1'
EXPECTED_CHECKPOINT_SCHEMA = 'nanojev.independent-three-backbone-math.precision.v2'


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json_atomic(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + '.partial')
    partial.write_text(json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(partial, path)


def read_checkpoint(path: Path):
    # weights_only avoids arbitrary pickle execution from untrusted checkpoints.
    import torch
    if not path.is_file():
        raise FileNotFoundError('CONTINUOUS_CHECKPOINT_MISSING:' + str(path))
    return torch.load(path, map_location='cpu', weights_only=True)


def inspect_checkpoint(obj):
    if obj.get('schema') != EXPECTED_CHECKPOINT_SCHEMA or obj.get('backbone') != 'qwen':
        raise RuntimeError('CONTINUOUS_WRONG_CHECKPOINT_SCHEMA_OR_BACKBONE')
    cfg = obj.get('config')
    if not isinstance(cfg, dict) or cfg.get('backbone') != 'qwen':
        raise RuntimeError('CONTINUOUS_MISSING_QWEN_CONFIG')
    if float(cfg.get('precision_weight', -1)) != 0.0:
        raise RuntimeError('CONTINUOUS_EXPECTS_WEIGHT_ZERO_LINEAGE')
    parent = cfg.get('parent_v1')
    if not isinstance(parent, dict) or not parent.get('sha256'):
        raise RuntimeError('CONTINUOUS_PARENT_LINEAGE_MISSING')
    if not isinstance(obj.get('stream'), dict) or 'optimizer' not in obj or 'torch_rng' not in obj:
        raise RuntimeError('CONTINUOUS_NOT_EXACT_RESUME_CHECKPOINT')
    return cfg, int(obj['step'])


def dtype_from_config(cfg):
    info = cfg.get('model_info') or {}
    raw = str(info.get('dtype', 'torch.float16')).removeprefix('torch.')
    if raw not in ('float16', 'bfloat16', 'float32'):
        raise RuntimeError('CONTINUOUS_UNKNOWN_MODEL_DTYPE:' + raw)
    return raw


def device_from_config(cfg):
    info = cfg.get('model_info') or {}
    device = str(info.get('device', 'cuda')).split(':')[0]
    if device not in ('cuda', 'cpu'):
        raise RuntimeError('CONTINUOUS_UNKNOWN_MODEL_DEVICE:' + device)
    return device


def build_command(python, trainer, root, cfg, additional_steps, allow_model_download,
                  eval_every=100, report_every=50):
    if additional_steps < 1:
        raise ValueError('CONTINUOUS_NONPOSITIVE_STEPS')
    cmd = [str(python), str(trainer), '--resume', '--results-root', str(root),
           '--additional-steps', str(additional_steps),
           '--device', device_from_config(cfg), '--dtype', dtype_from_config(cfg),
           '--precision-weight', str(cfg['precision_weight']),
           '--precision-relative-tolerance', str(cfg['precision_relative_tolerance']),
           '--precision-huber-beta', str(cfg['precision_huber_beta']),
           '--seed', str(cfg['seed']), '--width', str(cfg['width']),
           '--batch-size', str(cfg['batch_size']), '--lr', str(cfg['lr']),
           '--val-examples', str(cfg['val_examples']),
           '--test-examples', str(cfg['test_examples']),
           '--max-prompt-tokens', str(cfg['max_prompt_tokens']),
           '--eval-every', str(eval_every), '--report-every', str(report_every)]
    if allow_model_download:
        cmd.append('--allow-model-download')
    return cmd



def bootstrap_from_verified_best(root: Path, source: Path):
    """New isolated continuation; no modification of source control artifacts.

    The resumed optimizer, stream, torch RNG and best metric bookkeeping are
    inherited from the exact best-precision checkpoint. All source artifacts
    stay byte-identical. Renaming the staged directory provides atomic publish.
    """
    if root.exists():
        raise RuntimeError('CONTINUOUS_BOOTSTRAP_DESTINATION_ALREADY_EXISTS:' + str(root))
    best = source / 'best-precision.pt'
    cache = source / 'validation-cache.pt'
    if not best.is_file() or not cache.is_file():
        raise FileNotFoundError('CONTINUOUS_SOURCE_BEST_OR_CACHE_MISSING:' + str(source))
    best_checkpoint = read_checkpoint(best)
    cfg, best_step = inspect_checkpoint(best_checkpoint)
    cache_obj = read_checkpoint(cache)
    if cache_obj.get('config') != cfg:
        raise RuntimeError('CONTINUOUS_SOURCE_VALIDATION_CACHE_DRIFT')
    root.parent.mkdir(parents=True, exist_ok=True)
    stage = root.with_name(root.name + '.bootstrap-' + uuid.uuid4().hex)
    stage.mkdir()
    try:
        for filename in ('latest.pt', 'best-precision.pt', 'best-asinh.pt'):
            shutil.copy2(best, stage / filename)
        shutil.copy2(cache, stage / 'validation-cache.pt')
        for optional in ('fork.pt', 'fork.json', 'baseline.json'):
            src = source / optional
            if src.is_file():
                shutil.copy2(src, stage / optional)
        write_json_atomic(stage / 'continuous-origin.json',
                          {'schema': CAMPAIGN_SCHEMA, 'source_root': str(source),
                           'source_checkpoint': 'best-precision.pt',
                           'source_checkpoint_sha256': sha256_file(best),
                           'source_best_step': best_step, 'parent_v1': cfg['parent_v1'],
                           'created_utc': utc_now()})
        os.rename(stage, root)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return best_step


def checkpoint_best(obj):
    best = obj.get('best') or {}
    p = best.get('precision') or {}
    a = best.get('asinh') or {}
    rank = p.get('rank')
    if not isinstance(rank, list) or len(rank) != 4:
        raise RuntimeError('CONTINUOUS_BAD_PRECISION_RANK')
    if 'score' not in a:
        raise RuntimeError('CONTINUOUS_BAD_ASINH_SCORE')
    return {'precision_rank': [float(x) for x in rank],
            'precision_step': int(p['step']),
            'asinh_score': float(a['score']),
            'asinh_step': int(a['step'])}


def same_experiment(a, b):
    # The complete checkpoint config already binds parent, model revision,
    # data plan, dimensions, optimizer hyperparameters and evaluation split.
    return a == b


@contextmanager
def exclusive_lock(path):
    """Kernel-backed lock, released automatically on process exit/crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as f:
        if os.name == 'nt':
            import msvcrt
            f.seek(0, 2)
            if f.tell() == 0:
                f.write(b'0')
                f.flush()
            f.seek(0)
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as e:
                raise RuntimeError('CONTINUOUS_ALREADY_RUNNING') from e
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as e:
                raise RuntimeError('CONTINUOUS_ALREADY_RUNNING') from e
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def load_or_initialize(journal, cfg, trainer_digest, checkpoint, root, meta):
    if journal.is_file():
        state = read_json(journal)
        if (state.get('schema') != CAMPAIGN_SCHEMA
                or state.get('results_root') != str(root)
                or not same_experiment(state.get('checkpoint_config'), cfg)):
            raise RuntimeError('CONTINUOUS_EXPERIMENT_IDENTITY_DRIFT')
        if state.get('trainer_sha256') != trainer_digest:
            raise RuntimeError('CONTINUOUS_TRAINER_CHANGED_REQUIRES_NEW_CAMPAIGN')
        return state
    best = checkpoint_best(checkpoint)
    state = {'schema': CAMPAIGN_SCHEMA, 'created_utc': utc_now(),
             'results_root': str(root), 'checkpoint_config': cfg,
             'trainer_sha256': trainer_digest, 'initial_step': int(checkpoint['step']),
             'cycles': [], 'active_cycle': None,
             'best_precision_rank': best['precision_rank'],
             'best_precision_step': best['precision_step'],
             'best_asinh_score': best['asinh_score'],
             'stale_cycles': 0}
    # Preserve an immutable baseline before authorizing any continuation.
    initial_dir = meta / 'baseline'
    initial_dir.mkdir(parents=True, exist_ok=True)
    for name in ('latest.pt', 'best-precision.pt', 'best-asinh.pt'):
        src = root / name
        if src.is_file():
            dest = initial_dir / name
            if not dest.exists():
                shutil.copy2(src, dest)
    write_json_atomic(initial_dir / 'identity.json',
                      {'at': utc_now(), 'step': int(checkpoint['step']),
                       'best': best, 'parent': cfg['parent_v1'],
                       'latest_sha256': sha256_file(root / 'latest.pt')})
    write_json_atomic(journal, state)
    return state


def next_cycle(state, current_step, cycle_steps):
    active = state.get('active_cycle')
    if active:
        if current_step < active['start_step'] or current_step > active['target_step']:
            raise RuntimeError('CONTINUOUS_ACTIVE_CYCLE_STEP_DRIFT')
        return active, active['target_step'] - current_step
    number = len(state['cycles']) + 1
    active = {'number': number, 'start_step': current_step,
              'target_step': current_step + cycle_steps,
              'started_utc': utc_now()}
    return active, cycle_steps


def save_best_archive(meta, root, number, name):
    src = root / name
    if not src.is_file():
        raise RuntimeError('CONTINUOUS_BEST_CHECKPOINT_MISSING:' + str(src))
    dest = meta / 'champions' / f'cycle-{number:06d}-{name}'
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise RuntimeError('CONTINUOUS_CHAMPION_ARCHIVE_EXISTS:' + str(dest))
    partial = dest.with_name(dest.name + '.partial')
    shutil.copy2(src, partial)
    os.replace(partial, dest)
    return {'path': str(dest), 'sha256': sha256_file(dest)}


def finalize_cycle(state, active, last, root, meta, plateau_cycles):
    step = int(last['step'])
    if step != active['target_step']:
        raise RuntimeError('CONTINUOUS_TARGET_STEP_MISSING')
    test_path = root / 'test-last.json'
    if not test_path.is_file() or int(read_json(test_path).get('step', -1)) != step:
        raise RuntimeError('CONTINUOUS_TEST_COMPLETION_MISSING')
    best = checkpoint_best(last)
    previous = tuple(state['best_precision_rank'])
    numerical_improved = tuple(best['precision_rank']) > previous
    asinh_improved = best['asinh_score'] < state['best_asinh_score']
    archived = {}
    if numerical_improved:
        archived['numeric'] = save_best_archive(meta, root, active['number'], 'best-precision.pt')
        state['best_precision_rank'] = best['precision_rank']
        state['best_precision_step'] = best['precision_step']
        state['stale_cycles'] = 0
    else:
        state['stale_cycles'] += 1
    if asinh_improved:
        archived['asinh'] = save_best_archive(meta, root, active['number'], 'best-asinh.pt')
        state['best_asinh_score'] = best['asinh_score']
    record = {**active, 'completed_utc': utc_now(), 'completed_step': step,
              'numeric_improved': numerical_improved,
              'asinh_improved': asinh_improved, 'best': best,
              'archived': archived, 'latest_sha256': sha256_file(root / 'latest.pt'),
              'stale_cycles': state['stale_cycles'],
              'validation_cases_per_orientation': last['config']['val_examples'],
              'promotion_status': 'candidate_only_needs_larger_independent_audit'}
    state['cycles'].append(record)
    state['active_cycle'] = None
    return record


def stream_command(cmd, logfile):
    logfile.parent.mkdir(parents=True, exist_ok=True)
    with logfile.open('a', encoding='utf-8') as log:
        log.write('COMMAND ' + json.dumps(cmd) + '\n')
        log.flush()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding='utf-8', errors='replace', bufsize=1)
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                print(line, end='', flush=True)
                log.write(line)
                log.flush()
            rc = proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait()
            raise
        finally:
            if proc.stdout is not None:
                proc.stdout.close()
    return rc


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--results-root', type=Path, default=DEFAULT_ROOT)
    p.add_argument('--bootstrap-source', type=Path, default=DEFAULT_SOURCE)
    p.add_argument('--trainer', type=Path, default=Path(__file__).with_name(TRAINER_NAME))
    p.add_argument('--python', type=Path, default=Path(sys.executable))
    p.add_argument('--meta-root', type=Path, default=None)
    p.add_argument('--cycle-steps', type=int, default=2000)
    p.add_argument('--cycles', type=int, default=0,
                   help='Number of cycles this invocation (0 = continue until plateau or interrupted)')
    p.add_argument('--plateau-cycles', type=int, default=8,
                   help='Stop after N cycles without new best numeric rank; 0 disables')
    p.add_argument('--eval-every', type=int, default=100)
    p.add_argument('--report-every', type=int, default=50)
    p.add_argument('--allow-model-download', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--status', action='store_true')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.cycle_steps < 1 or args.cycles < 0 or args.plateau_cycles < 0:
        raise ValueError('CONTINUOUS_INVALID_CYCLE_ARGUMENT')
    if args.eval_every < 1 or args.report_every < 1:
        raise ValueError('CONTINUOUS_INVALID_REPORT_ARGUMENT')
    root = args.results_root.expanduser().resolve()
    trainer = args.trainer.expanduser().resolve()
    meta = (args.meta_root or root.parent / (root.name + '_continuous_v1')).expanduser().resolve()
    if not trainer.is_file():
        raise FileNotFoundError('CONTINUOUS_TRAINER_MISSING:' + str(trainer))
    source = args.bootstrap_source.expanduser().resolve()
    digest = sha256_file(trainer)
    journal = meta / 'campaign-state.json'
    needs_bootstrap = not (root / 'latest.pt').is_file()
    if needs_bootstrap:
        if root.exists():
            raise RuntimeError('CONTINUOUS_RESULTS_ROOT_NONEMPTY_OR_INCOMPLETE:' + str(root))
        if not (source / 'best-precision.pt').is_file():
            raise FileNotFoundError('CONTINUOUS_BOOTSTRAP_SOURCE_MISSING:' + str(source))
        checkpoint = read_checkpoint(source / 'best-precision.pt')
    else:
        checkpoint = read_checkpoint(root / 'latest.pt')
    cfg, step = inspect_checkpoint(checkpoint)
    print(json.dumps({'event': 'qwen_continuous_preflight',
                      'root': str(root), 'step': step, 'parent': cfg['parent_v1'],
                      'precision_weight': cfg['precision_weight'],
                      'bootstrap_from': str(source) if needs_bootstrap else None,
                      'best': checkpoint_best(checkpoint), 'meta': str(meta)},
                     sort_keys=True), flush=True)
    if args.status:
        if journal.is_file():
            state = read_json(journal)
            print(json.dumps({'event': 'qwen_continuous_status',
                              'cycles': len(state['cycles']),
                              'active_cycle': state['active_cycle'],
                              'best_precision_rank': state['best_precision_rank'],
                              'stale_cycles': state['stale_cycles']}, sort_keys=True))
        return 0
    initial_cmd = build_command(args.python, trainer, root, cfg,
                                args.cycle_steps, args.allow_model_download,
                                args.eval_every, args.report_every)
    if args.dry_run:
        print(json.dumps({'event': 'qwen_continuous_dry_run',
                          'command': initial_cmd, 'cycles': args.cycles,
                          'plateau_cycles': args.plateau_cycles,
                          'journal_exists': journal.exists(),
                          'needs_bootstrap': needs_bootstrap}, sort_keys=True))
        return 0
    # The external standalone trainer does NOT acquire this lock. Do not run
    # it simultaneously against this results root.
    with exclusive_lock(meta / '.supervisor.lock'):
        if needs_bootstrap:
            boot_step = bootstrap_from_verified_best(root, source)
            print(json.dumps({'event': 'qwen_continuous_bootstrapped',
                              'source': str(source), 'destination': str(root),
                              'step': boot_step}, sort_keys=True), flush=True)
            checkpoint = read_checkpoint(root / 'latest.pt')
        state = load_or_initialize(journal, cfg, digest, checkpoint, root, meta)
        done = 0
        while args.cycles == 0 or done < args.cycles:
            checkpoint = read_checkpoint(root / 'latest.pt')
            latest_cfg, step = inspect_checkpoint(checkpoint)
            if not same_experiment(cfg, latest_cfg):
                raise RuntimeError('CONTINUOUS_CONFIG_CHANGED_DURING_CAMPAIGN')
            if not state.get('active_cycle') and args.plateau_cycles and state['stale_cycles'] >= args.plateau_cycles:
                print(json.dumps({'event': 'qwen_continuous_plateau_stop',
                                  'stale_cycles': state['stale_cycles'],
                                  'step': step}, sort_keys=True))
                break
            active, remaining = next_cycle(state, step, args.cycle_steps)
            if not state['active_cycle']:
                state['active_cycle'] = active
                write_json_atomic(journal, state)  # journal target BEFORE trainer runs
            if remaining:
                cmd = build_command(args.python, trainer, root, cfg, remaining,
                                    args.allow_model_download,
                                    args.eval_every, args.report_every)
                log = meta / 'logs' / f"cycle-{active['number']:06d}.log"
                print(json.dumps({'event': 'qwen_continuous_cycle_start',
                                  'cycle': active['number'], 'from_step': step,
                                  'target_step': active['target_step'],
                                  'remaining': remaining}, sort_keys=True), flush=True)
                rc = stream_command(cmd, log)
                if rc:
                    raise RuntimeError('CONTINUOUS_TRAINER_FAILED_EXIT:' + str(rc))
            last = read_checkpoint(root / 'latest.pt')
            newest_cfg, newest_step = inspect_checkpoint(last)
            if not same_experiment(cfg, newest_cfg):
                raise RuntimeError('CONTINUOUS_TRAINER_CONFIG_DRIFT')
            record = finalize_cycle(state, active, last, root, meta, args.plateau_cycles)
            write_json_atomic(journal, state)
            print(json.dumps({'event': 'qwen_continuous_cycle_complete',
                              'cycle': active['number'], 'step': newest_step,
                              'best': record['best'],
                              'numeric_improved': record['numeric_improved'],
                              'asinh_improved': record['asinh_improved'],
                              'stale_cycles': record['stale_cycles'],
                              'promotion_status': record['promotion_status']},
                             sort_keys=True), flush=True)
            done += 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
