#!/usr/bin/env python3
"""NanoJev v3.8: deterministic alternating X/Y register-prompt order.

Only freshly generated TRAINING prompt registration lines are reordered.
The associated a/b values, X/Y supervision, operation instruction, losses,
model topology, 16:3:1 schedule, checkpoint selection, and cached fixed-order
benchmark validation are unchanged from NanoJev v3.7.

This additive launcher forks an existing v3.7 checkpoint into a new root,
retaining its trainable weights, optimizer moments, RNG/stream and replay.
The original v3.7 code and artifacts remain unmodified. All subsequent
resume/evaluation invocations must use this launcher, not the base v3.7 one.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

import torch

import nanojev_parallel_result_asinh_squared_v3_7 as v37
from nanojev_register_stability_v3_generate import FIRST, SECOND, number_repr

CUTOVER_SCHEMA = 'nanojev.register-prompt-order-alternation.v3.8'
ORDER_VALUE = 'original-first-on-even-fresh-record;reversed-on-odd-fresh-record'
MARKER = 'register-order-cutover.json'


def reorder_registration(row, reverse):
    """Change only the first two lines, never X=A, Y=B or the operation.

    A malformed/unknown registration format must fail closed rather than
    accidentally perturb the operation or reverse noncommutative operands.
    """
    original = row['prompt']
    first = f'{FIRST} = {number_repr(int(row["a"]))}'
    second = f'{SECOND} = {number_repr(int(row["b"]))}'
    prefix = first + '\n' + second + '\n'
    if not isinstance(original, str) or not original.startswith(prefix):
        raise RuntimeError('ALTREG_UNEXPECTED_ORIGINAL_PROMPT_FORMAT')
    # Frozen Qwen suffix capture depends on the operation/Answer tail, which
    # must remain byte-for-byte identical in both variants.
    if not original[len(prefix):].startswith('operation: '):
        raise RuntimeError('ALTREG_UNEXPECTED_OPERATION_TAIL')
    if not reverse:
        return row
    swapped = dict(row)
    swapped['prompt'] = second + '\n' + first + '\n' + original[len(prefix):]
    return swapped


def alternate_batch(original_batch):
    """One original ordering and one reversed ordering per TWO fresh rows.

    Use the persisted FreshPairs.drawn counter, not the current step number,
    so parity is stable across resumed runs and even-sized batches.
    """
    def generate(stream, size):
        rows = original_batch(stream, size)
        first_idx = stream.drawn - len(rows)
        if first_idx < 0:
            raise RuntimeError('ALTREG_INVALID_DRAW_COUNTER')
        return [reorder_registration(row, (first_idx+i) % 2 == 1)
                for i, row in enumerate(rows)]
    return generate


def with_order_config(original_config):
    def generate(*args, **kwargs):
        cfg = original_config(*args, **kwargs)
        if 'register_prompt_order' in cfg:
            raise RuntimeError('ALTREG_CONFIG_KEY_COLLISION')
        return {**cfg, 'register_prompt_order': ORDER_VALUE}
    return generate


def _sha256(path):
    h = hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024 * 1024),b''):
            h.update(chunk)
    return h.hexdigest()


def _load_with_mode(path):
    state = torch.load(path, map_location='cpu', weights_only=True)
    if state.get('schema') != v37.SCHEMA:
        raise RuntimeError('ALTREG_REQUIRES_V3_7_CHECKPOINT')
    if not isinstance(state.get('config'), dict):
        raise RuntimeError('ALTREG_INVALID_PARENT_CONFIG')
    if 'register_prompt_order' in state['config']:
        raise RuntimeError('ALTREG_PARENT_ALREADY_ALTERNATING')
    state['config'] = {**state['config'], 'register_prompt_order': ORDER_VALUE}
    return state


def _write_checkpoint(path, state):
    path.parent.mkdir(parents=True,exist_ok=True)
    torch.save(state,path)


def create_fork(parent_root, result_root, checkpoint='latest.pt'):
    parent = Path(parent_root).expanduser().resolve()
    root = Path(result_root).expanduser().resolve()
    if (root == parent or parent in root.parents or root in parent.parents):
        raise RuntimeError('ALTREG_UNSAFE_FORK_ROOT')
    if checkpoint not in ('latest.pt','asinh_intermediary/best.pt',
                          'asinh_result/best.pt','asinh_final/best.pt'):
        raise ValueError('ALTREG_INVALID_FORK_CHECKPOINT')
    source_file = parent / checkpoint
    if not source_file.is_file():
        raise FileNotFoundError(f'ALTREG_SOURCE_CHECKPOINT_MISSING:{source_file}')
    if root.exists():
        raise RuntimeError('ALTREG_TARGET_ROOT_NOT_EMPTY')
    source_hash = _sha256(source_file)
    state = _load_with_mode(source_file)
    marker = {
        'schema': CUTOVER_SCHEMA,
        'order': ORDER_VALUE,
        'source_checkpoint': str(source_file),
        'source_sha256': source_hash,
        'fork_step': int(state['step']),
        'preserves': ['weights','optimizer_moments','stream_rng','stream_drawn',
                      'replay','phase_tick','phase_counts','regression_targets',
                      '16:3:1_phase_schedule','original_order_benchmark_validation'],
        'validation_prompt_order': 'original_from_unchanged_v3_capture',
    }
    # Build the new root off to one side; publish it only after all required
    # snapshots and the manifest have been durably written.
    stage = root.with_name(root.name + '.altreg-stage-' + uuid.uuid4().hex)
    stage.mkdir(parents=True,exist_ok=False)
    try:
        _write_checkpoint(stage/'latest.pt', state)
        # Seed all existing best-checkpoint tracks so eval-best is usable even
        # before the fork trains a new champion. Avoid read/write to parent.
        for name in ('asinh_result','asinh_final','asinh_intermediary'):
            previous = parent/name/'best.pt'
            if previous.is_file():
                _write_checkpoint(stage/name/'best.pt',_load_with_mode(previous))
        (stage/MARKER).write_text(json.dumps(marker,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        stage.replace(root)
    except Exception:
        shutil.rmtree(stage,ignore_errors=True)
        raise
    return marker


def parser():
    p = v37.parser()
    p.description = __doc__
    p.set_defaults(results_root=Path.home()/'NanoJev'/'runs'/'parallel_register_order_v3_8')
    p.add_argument('--fork-v3-7-root',type=Path,default=None,
                   help='Unmodified v3.7 results root to fork once; omitted for --resume or --eval-only.')
    p.add_argument('--fork-v3-7-checkpoint',default='latest.pt',
                   choices=('latest.pt','asinh_intermediary/best.pt',
                            'asinh_result/best.pt','asinh_final/best.pt'))
    return p


def self_test():
    from nanojev_register_stability_v3_generate import OPERATIONS, render
    for op in (OPERATIONS[0], OPERATIONS[1], OPERATIONS[2]):
        row = {'a':17,'b':-3,'operation':op[0], 'prompt':render(17,-3,op)}
        orig = reorder_registration(row,False)
        swap = reorder_registration(row,True)
        assert orig is row and swap['a']==17 and swap['b']==-3
        assert swap['operation']==op[0]
        assert swap['prompt'].splitlines()[:2] == [
            f'{SECOND} = {number_repr(-3)}',f'{FIRST} = {number_repr(17)}']
        assert swap['prompt'].splitlines()[2:] == row['prompt'].splitlines()[2:]
        assert reorder_registration(row,True)==swap
    class FakeStream:
        drawn=20
    def original(stream,size):
        rows=[]
        for _ in range(size):
            stream.drawn+=1
            rows.append({'a':17,'b':-3,'prompt':render(17,-3,OPERATIONS[1])})
        return rows
    s=FakeStream()
    first=alternate_batch(original)(s,4)
    second=alternate_batch(original)(s,3)
    assert [r['prompt'].startswith(f'{FIRST} =') for r in first+second] == [True,False,True,False,True,False,True]
    assert len(v37.PHASE_NAMES)==20 and v37.PHASE_NAMES.count('intermediate')==16
    assert v37.PHASE_NAMES.count('result')==3 and v37.PHASE_NAMES.count('final')==1
    v37.base.emit('altreg_self_test_pass',schedule='16:3:1', prompt_only=True,
                  alternating_by='fresh_record_index')
    return 0


def execute(args):
    if args.self_test:
        return self_test()
    if args.fork_v3_7_root is not None and (args.resume or args.eval_only):
        raise ValueError('ALTREG_FORK_AND_RESUME_EXCLUSIVE')
    if args.fork_v3_7_root is None and not (args.resume or args.eval_only):
        raise ValueError('ALTREG_REQUIRES_FORK_OR_RESUME')
    if args.fork_v3_5_root is not None or args.fork_v3_4_root is not None:
        raise ValueError('ALTREG_FORK_ONLY_FROM_V3_7')
    if args.fork_parent_best:
        raise ValueError('ALTREG_USE_--fork-v3-7-checkpoint')
    root=Path(args.results_root).expanduser().resolve()
    if args.fork_v3_7_root is not None:
        marker=create_fork(args.fork_v3_7_root,root,args.fork_v3_7_checkpoint)
        args.resume=True
        v37.base.emit('altreg_forked',**marker,results_root=str(root))
    else:
        path=root/MARKER
        if not path.is_file():
            raise RuntimeError('ALTREG_RESUME_REQUIRES_CUTOVER_MARKER')
        marker=json.loads(path.read_text(encoding='utf-8'))
        if marker.get('schema')!=CUTOVER_SCHEMA or marker.get('order')!=ORDER_VALUE:
            raise RuntimeError('ALTREG_CUTOVER_MARKER_DRIFT')
    # Only intervention in the existing v3.7 training loop: FreshPairs.batch
    # returns alternating presentations. The original capture, optimizer,
    # objective, ground-truth mapping, schedule and validation are untouched.
    original_batch=v37.base.previous.FreshPairs.batch
    original_config_for=v37.config_for
    v37.base.previous.FreshPairs.batch=alternate_batch(original_batch)
    v37.config_for=with_order_config(original_config_for)
    try:
        return v37.execute(args)
    finally:
        v37.base.previous.FreshPairs.batch=original_batch
        v37.config_for=original_config_for


if __name__=='__main__':
    raise SystemExit(execute(parser().parse_args()))
