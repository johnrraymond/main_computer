#!/usr/bin/env python3
"""Permanent alternating Gamer LoRA / C0 Wide trainer.

One source run keeps its original LoRA optimizer and dataset. Each head cycle
pins the exact latest LoRA tensors at a chosen optimizer step, re-extracts
original-*08-prompt features, and continues numerical-head weights from the
previous cycle with a fresh optimizer on the new representation.

 python -m tools.nanojev_gamer_v1_staircase status --run RUN
 python -m tools.nanojev_gamer_v1_staircase head --run RUN --steps 300 --device cuda
 python -m tools.nanojev_gamer_v1_staircase lora --run RUN --steps 1600 --device cuda
 python -m tools.nanojev_gamer_v1_staircase head --run RUN --steps 600 --device cuda

Stop the other trainer first. No anonymous random-game examples are created.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile

from tools import nanojev_gamer_v1_handoff as gamer


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _load_lora(run):
    import torch
    latest=run/'lora'/'latest.pt'
    if not latest.is_file():
        raise FileNotFoundError('GAMER_STAIRCASE_LORA_CHECKPOINT_MISSING')
    with gamer._open_live_checkpoint_shared(latest) as stream:
        raw=stream.read()
    state=torch.load(io.BytesIO(raw),map_location='cpu',weights_only=True)
    manifest=_json(run/'handoff_manifest.json')
    if (state.get('schema')!=gamer.SCHEMA or state.get('phase')!='lora'
        or state.get('dataset_sha256')!=manifest.get('dataset_sha256')
        or state.get('original_prompt_corpus_sha256')!=manifest.get('original_prompt_corpus_sha256')
        or state.get('hyperparameters',{}).get('model')!=manifest.get('model')
        or not isinstance(state.get('step'),int) or state['step']<1
        or not isinstance(state.get('adapter'),dict) or not state['adapter']):
        raise ValueError('GAMER_STAIRCASE_LORA_STATE_INVALID')
    return state,manifest,gamer.sha_bytes(raw)


def _adapter_tensor_fingerprint(state):
    """Identity of trained adapter tensors, independent of evaluation bookkeeping.

    An evaluation finishing changes latest.pt (pending flag, history, RNG) but
    not the LoRA weights. Reuse that head cycle rather than recapturing identical
    Qwen features or resetting the head optimizer.
    """
    import torch
    weights=state.get('adapter')
    if not isinstance(weights,dict) or not weights:
        raise ValueError('GAMER_STAIRCASE_ADAPTER_TENSORS_MISSING')
    digest=hashlib.sha256()
    for name, tensor in sorted(weights.items()):
        if not isinstance(name,str) or not isinstance(tensor,torch.Tensor):
            raise ValueError('GAMER_STAIRCASE_ADAPTER_TENSORS_INVALID')
        if not torch.isfinite(tensor).all().item():
            raise ValueError('GAMER_STAIRCASE_NONFINITE_ADAPTER')
        data=tensor.detach().to('cpu').contiguous()
        digest.update(name.encode('utf-8')+b'\0')
        digest.update(str(data.dtype).encode('ascii')+b'\0')
        digest.update(str(tuple(data.shape)).encode('ascii')+b'\0')
        digest.update(data.view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()


def _flags_for_manifest(manifest):
    flags=[]
    if manifest.get('bounded_transition_games'):
        flags.append('--include-transition-games')
    elif manifest.get('bounded_catalog_extensions'):
        flags.append('--include-catalog-extensions')
    if manifest.get('allow_partial_smoke'):
        flags.append('--allow-partial-smoke')
    return flags


def _validate_corpus(run,manifest):
    dataset=run/'certified_pairs.jsonl'
    if not dataset.is_file() or gamer.sha_bytes(dataset.read_bytes())!=manifest.get('dataset_sha256'):
        raise ValueError('GAMER_STAIRCASE_DATASET_CHANGED_OR_MISSING')


def _cycles(run):
    root=run/'staircase'
    result=[]
    for directory in sorted(root.glob('head_cycle_*')):
        if directory.is_dir():
            status_path=directory/'staircase_cycle.json'
            if not status_path.is_file():
                raise ValueError('GAMER_STAIRCASE_INCOMPLETE_CYCLE: '+str(directory))
            result.append((directory,_json(status_path)))
    return result


def snapshot_latest(run, dest, *, expect_step=None):
    """Clone exact durable LoRA tensors, not best_adapter's possibly older weights."""
    import torch
    from safetensors.torch import load_file, save_file
    run=Path(run).resolve(); dest=Path(dest).resolve()
    if run==dest:
        raise ValueError('GAMER_STAIRCASE_SNAPSHOT_MUST_BE_SEPARATE')
    if dest.exists():
        raise FileExistsError('GAMER_STAIRCASE_SNAPSHOT_TARGET_EXISTS')
    state,manifest,source_sha=_load_lora(run)
    # A pending evaluation does not mean the optimizer checkpoint is incomplete.
    # It contains fully committed adapter weights, optimizer and RNG at `step`.
    # The source evaluation progress remains untouched and resumes on the next
    # LoRA phase; only the frozen weights are consumed by this head cycle.
    step=state['step']
    if expect_step is not None and step!=expect_step:
        raise ValueError('GAMER_STAIRCASE_LORA_STEP_CHANGED')
    _validate_corpus(run,manifest)
    source_best=run/'lora'/'best_adapter'
    config=source_best/'adapter_config.json'
    if not config.is_file():
        raise FileNotFoundError('GAMER_STAIRCASE_NEEDS_PEFT_ADAPTER_TEMPLATE')
    cfg=_json(config)
    if (cfg.get('r')!=state.get('hyperparameters',{}).get('lora_rank') or
        cfg.get('task_type')!='CAUSAL_LM' or
        cfg.get('base_model_name_or_path') not in (None,manifest['model'])):
        raise ValueError('GAMER_STAIRCASE_PEFT_CONFIG_INCOMPATIBLE')
    original_file=source_best/'adapter_model.safetensors'
    old_bin=source_best/'adapter_model.bin'
    if original_file.is_file():
        original_weights=load_file(str(original_file),device='cpu')
    elif old_bin.is_file():
        original_weights=torch.load(old_bin,map_location='cpu',weights_only=True)
    else:
        raise FileNotFoundError('GAMER_STAIRCASE_PEFT_WEIGHT_TEMPLATE_MISSING')
    weights=state['adapter']
    if set(weights)!=set(original_weights) or not all(isinstance(v,torch.Tensor) for v in weights.values()):
        raise ValueError('GAMER_STAIRCASE_PEFT_TENSOR_KEYS_DO_NOT_MATCH_TEMPLATE')
    if any(weights[k].shape != original_weights[k].shape for k in weights):
        raise ValueError('GAMER_STAIRCASE_PEFT_TENSOR_SHAPES_DO_NOT_MATCH_TEMPLATE')
    source_tree_sha=gamer.file_tree_sha256(source_best)
    if not all(torch.isfinite(v).all().item() for v in weights.values()):
        raise ValueError('GAMER_STAIRCASE_NONFINITE_ADAPTER')
    dest.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix=dest.name+'.preparing-',dir=dest.parent))
    try:
        shutil.copyfile(run/'certified_pairs.jsonl',stage/'certified_pairs.jsonl')
        shutil.copyfile(run/'handoff_manifest.json',stage/'handoff_manifest.json')
        target_adapter=stage/'lora'/'best_adapter'
        shutil.copytree(source_best,target_adapter)
        if (target_adapter/'adapter_model.bin').exists():
            (target_adapter/'adapter_model.bin').unlink()
        # PEFT state keys are the same format used by save_pretrained. Refuse
        # divergent names above rather than emitting an un-loadable checkpoint.
        save_file({k:v.detach().to('cpu').contiguous() for k,v in weights.items()},
                  str(target_adapter/'adapter_model.safetensors'),metadata={'format':'pt'})
        adapter_sha=gamer.file_tree_sha256(target_adapter)
        # Fail closed if another LoRA write raced this snapshot. A newer step
        # must be selected explicitly in a new cycle.
        _,new_manifest,new_source_sha=_load_lora(run)
        if (new_source_sha!=source_sha or new_manifest!=manifest or
            gamer.file_tree_sha256(source_best)!=source_tree_sha):
            raise ValueError('GAMER_STAIRCASE_SOURCE_CHANGED_DURING_SNAPSHOT')
        if gamer.sha_bytes((stage/'certified_pairs.jsonl').read_bytes())!=manifest['dataset_sha256']:
            raise ValueError('GAMER_STAIRCASE_DATASET_CHANGED_DURING_COPY')
        provenance={'run':str(run),'checkpoint_kind':'latest','checkpoint_step':step,
                    'checkpoint_sha256':source_sha,'adapter_sha256':adapter_sha,
                    'source_pending_eval':bool(state.get('pending_eval')),
                    'source_adapter_tensor_sha256':_adapter_tensor_fingerprint(state)}
        frozen={'schema':gamer.SCHEMA,'phase':'lora','model':manifest['model'],
                'dataset_sha256':manifest['dataset_sha256'],
                'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
                'best_step':step,'best_adapter_sha256':adapter_sha,
                'handoff_eligible':False,'frozen_snapshot':True,
                'snapshot_source':provenance,'retention_certification':'NOT_AUDITED',
                'evaluation_scope':('EXACT_LATEST_PENDING_EVALUATION'
                                    if state.get('pending_eval')
                                    else 'EXACT_LATEST_NOT_EVALUATED'),
                'lora_release_status':'EXPERIMENTAL_NOT_PROMOTED'}
        gamer.atomic_json(stage/'lora'/'report.json',frozen)
        if dest.exists():
            raise FileExistsError('GAMER_STAIRCASE_SNAPSHOT_TARGET_EXISTS')
        os.replace(stage,dest)
    finally:
        if stage.exists():shutil.rmtree(stage)
    return frozen


def _head_run_args(cycle, *, target, device, manifest, source_hyperparameters=None, previous=None, resume=False,
                   head_eval_every=100, head_save_every=1, capture_batch=2,
                   zero_line_focus=None, zero_line_radius=None):
    hp={}
    if resume:
        import torch
        with gamer._open_live_checkpoint_shared(cycle/'to_numerical'/'latest.pt') as stream:
            state=torch.load(stream,map_location='cpu',weights_only=True)
        hp=state['hyperparameters']
        previous=hp.get('init_head_from')
    source_hp=source_hyperparameters or {}
    command=['to-numerical','--output',str(cycle),'--device',str(device),
             '--model',str(manifest['model']),
             '--seed',str(hp.get('seed',source_hp.get('seed',47))),
             '--max-length',str(hp.get('max_length',source_hp.get('max_length',1024))),
             '--head-steps',str(target),'--allow-experimental',
             '--head-save-every',str(head_save_every),
             '--head-eval-every',str(hp.get('head_eval_every',head_eval_every)),
             '--capture-batch',str(hp.get('capture_batch',capture_batch))]
    for key in ('eval_batch','head_batch','head_lr',
                'weight_decay','aux_weight','huber_beta','grad_clip'):
        if hp.get(key) is not None:
            command+=['--'+key.replace('_','-'),str(hp[key])]
    if zero_line_focus is not None:
        command+=['--zero-line-focus',str(zero_line_focus)]
    if zero_line_radius is not None:
        command+=['--zero-line-radius',str(zero_line_radius)]
    if previous:
        command+=['--init-head-from',str(previous)]
    if resume:
        command.append('--resume')
    command+=_flags_for_manifest(manifest)
    return command


def head(run, steps, device, *, head_eval_every=100, capture_batch=2, head_save_every=1,
         zero_line_focus=None, zero_line_radius=None):
    from tools.nanojev_gamer_v1_zero_line import resolve_policy
    resolve_policy(focus=zero_line_focus,radius=zero_line_radius)
    run=Path(run).resolve()
    state,manifest,source_sha=_load_lora(run)
    _validate_corpus(run,manifest)
    # Even while a dev evaluation is pending, the adapter/optimizer step is
    # durable. The next LoRA resume will complete that exact evaluation.
    cycles=_cycles(run)
    source_step=state['step']
    old_dir,old_record=cycles[-1] if cycles else (None,None)
    source_weights_sha=_adapter_tensor_fingerprint(state)
    if (old_record and old_record['source_step']==source_step and
        (old_record.get('source_weights_sha256')==source_weights_sha
         if old_record.get('source_weights_sha256')
         else old_record['source_sha256']==source_sha)):
        cycle=old_dir
        resume=(cycle/'to_numerical'/'latest.pt').is_file()
        previous=old_record.get('head_from')
        # Ctrl+C during initial feature capture leaves a valid frozen adapter
        # without a head checkpoint. Retry the capture without overwriting it.
        if not (cycle/'handoff.json').is_file():
            gamer.main(['switch','--output',str(cycle),'--model',str(manifest['model']),
                        '--allow-experimental']+
                       _flags_for_manifest(manifest))
        print(f'GAMER STAIRCASE HEAD: same LoRA step {source_step}; '
              f'{"resuming" if resume else "retrying initial capture for"} cycle {cycle.name}.',flush=True)
    else:
        resume=False
        cycle=run/'staircase'/f'head_cycle_{len(cycles)+1:04d}'
        previous=old_dir if old_dir and (old_dir/'to_numerical'/'latest.pt').is_file() else None
        if old_dir and previous is None:
            raise ValueError('GAMER_STAIRCASE_PREVIOUS_HEAD_NEVER_CHECKPOINTED')
        # If the latest LoRA is different, the new cycle receives a new Qwen
        # feature distribution. The prior head tensors are retained as input.
        snapshot_latest(run,cycle,expect_step=source_step)
        record={'schema':'nanojev.gamer.staircase.cycle.v1',
                'source_step':source_step,'source_sha256':source_sha,
                'source_weights_sha256':source_weights_sha,
                'source_pending_eval':bool(state.get('pending_eval')),
                'head_from':str(previous) if previous else None,
                'feature_policy':'RECAPTURE_ORIGINAL_08_AFTER_EACH_LORA_CHANGE',
                'head_policy':'IMPORT_LATEST_WEIGHTS_FRESH_OPTIMIZER_ON_CHANGED_LORA'}
        gamer.atomic_json(cycle/'staircase_cycle.json',record)
        print(f'GAMER STAIRCASE HEAD: LoRA step {source_step} pinned to {cycle.name}; '
              f'previous head={previous or "none"}.',flush=True)
        gamer.main(['switch','--output',str(cycle),'--model',str(manifest['model']),
                    '--allow-experimental']+
                   _flags_for_manifest(manifest))
    return gamer.main(_head_run_args(cycle,target=steps,device=device,manifest=manifest,
                         source_hyperparameters=state['hyperparameters'],
                         previous=previous,resume=resume,head_eval_every=head_eval_every,
                         capture_batch=capture_batch,head_save_every=head_save_every,
                         zero_line_focus=zero_line_focus,zero_line_radius=zero_line_radius))


def lora(run, steps, device):
    run=Path(run).resolve()
    state,manifest,_=_load_lora(run)
    _validate_corpus(run,manifest)
    hp=state['hyperparameters']
    if steps<state['step'] or (steps==state['step'] and not state.get('pending_eval')):
        raise ValueError(f'GAMER_STAIRCASE_LORA_TARGET_MUST_EXCEED_{state["step"]}')
    if steps==state['step']:
        print(f'GAMER STAIRCASE LORA: recovering pending evaluation at step {steps}; '
              'optimizer weights and next-step numbering remain unchanged.',flush=True)
    cmd=['lora','--output',str(run),'--device',str(device),
         '--lora-steps',str(steps),'--resume']+_flags_for_manifest(manifest)
    for key in ('model','seed','max_length','max_new_tokens','lora_rank','lora_batch',
                'lora_accum','lora_lr','lora_eval_every','lora_probe_cases'):
        if key in hp and hp[key] is not None:
            cmd+=['--'+key.replace('_','-'),str(hp[key])]
    if hp.get('init_lora_from'):
        cmd+=['--init-lora-from',hp['init_lora_from'],
              '--init-lora-checkpoint',hp.get('init_lora_checkpoint','best')]
    print(f'GAMER STAIRCASE LORA: continuing exact run optimizer from step '
          f'{state["step"]} through {steps}; saved numerical-head cycles untouched.',flush=True)
    return gamer.main(cmd)


def status(run):
    run=Path(run).resolve()
    state,manifest,_=_load_lora(run)
    cycles=_cycles(run)
    print(f'GAMER STAIRCASE — {run}\n'
          f'LoRA: step={state["step"]}, pending_eval={bool(state.get("pending_eval"))}, '
          f'best_step={state.get("best_step")}, games={manifest.get("number_of_verified_games")}')
    if not cycles:
        print('Numerical head: not yet trained in this staircase.')
    for folder,record in cycles:
        path=folder/'to_numerical'/'latest.pt'
        if path.is_file():
            import torch
            with gamer._open_live_checkpoint_shared(path) as stream:
                head_state=torch.load(stream,map_location='cpu',weights_only=True)
            summary=f'step={head_state.get("step")} best_dev_mae={head_state.get("best")} '
        else:
            summary='no head checkpoint yet '
        if path.is_file():
            policy=head_state.get('sampling_policy') or {'focus':0.0,'radius':1.0}
            summary+=f'zero_line_focus={policy["focus"]:g} zero_line_radius={policy["radius"]:g} '
        print(f'  {folder.name}: LoRA step {record["source_step"]}, {summary}'
              f'final_report={(folder/"to_numerical"/"report.json").is_file()}')
    print('Use head --steps N to continue numerical learning; use lora --steps N to continue LoRA learning.')


def parser():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('phase',choices=('status','head','lora','diagnostic-train','diagnostic-localize','register-oracle','flat-registers','flat-attribution','flat-prompt','representation-diagnostic','head-100','head-100-diagnostic-train','head-100-radius','head-100-radius-recovery'))
    ap.add_argument('--run',type=Path,required=True)
    ap.add_argument('--steps',type=int,help='CUMULATIVE target step for the selected trainer')
    ap.add_argument('--device',default='cuda')
    ap.add_argument('--head-eval-every',type=int,default=100)
    ap.add_argument('--head-save-every',type=int,default=1)
    ap.add_argument('--capture-batch',type=int,default=2)
    ap.add_argument('--zero-line-focus',type=float,default=None,
                    help='head only: fraction of training draws devoted to '
                         'balanced exact-zero, small-negative and small-positive '
                         'targets; 0 disables focus, omitted inherits on resume')
    ap.add_argument('--zero-line-radius',type=float,default=None,
                    help='head only: maximum magnitude of focused nonzero '
                         'targets (default 1.0, omitted inherits on resume)')
    # The diagnostic intentionally does not use --steps: each controlled
    # experiment has its own fixed, matched budget and an isolated output tree.
    ap.add_argument('--diag-output',type=Path,default=None,
                    help='Diagnostic-only NEW directory outside --run (default unique sibling)')
    ap.add_argument('--diag-head-steps',type=int,default=120,
                    help='Diagnostic-only optimizer updates per matched head trial')
    ap.add_argument('--diag-sequence-steps',type=int,default=90,
                    help='Diagnostic-only updates for blocked/alternating schedules')
    ap.add_argument('--diag-eval-every',type=int,default=10,
                    help='Diagnostic-only cadence for complete per-case evaluations')
    ap.add_argument('--diag-lora-steps',type=int,default=12,
                    help='Extra LoRA steps on PRIVATE copied run; 0 skips and marks inconclusive')
    ap.add_argument('--diag-seeds',type=int,default=2,
                    help='Matched initialization and sampling seeds (default 2)')
    ap.add_argument('--diag-max-games',type=int,default=0,
                    help='Maximum distinct games to capture (0 means ALL games)')
    ap.add_argument('--diag-train-per-game',type=int,default=0,
                    help='Training cases per game (0=all certified training cases)')
    ap.add_argument('--diag-dev-per-game',type=int,default=0,
                    help='Development cases per game (0=all held-out development cases)')
    ap.add_argument('--diag-game-limit',type=int,default=12,
                    help='Number of individual high-error games to isolate (0 means all)')
    ap.add_argument('--diag-radius',type=float,default=1.0)
    ap.add_argument('--diag-capture-batch',type=int,default=2)
    ap.add_argument('--diag-checkpoint-every',type=int,default=0,
                    help='Save extra experiment-local checkpoints at this many updates; 0 start/end only')
    # Per-step causality experiment. Separate from the broader diagnostic-train
    # so it never retrains LoRA or repeats the expensive factorial experiment.
    ap.add_argument('--localize-games',default='captain_crew_risk,bertrand_oligopoly',
                    help='Comma-separated isolated game names; mixed is an optional control')
    ap.add_argument('--localize-start',choices=('fresh','checkpoint','both'),default='both',
                    help='Fresh diagnostic head, exact saved head+optimizer, or both')
    ap.add_argument('--localize-steps',type=int,default=20,
                    help='Trace every optimizer update, including full dev predictions')
    ap.add_argument('--localize-seeds',type=int,default=2)
    ap.add_argument('--localize-radius',type=float,default=1.)
    ap.add_argument('--localize-output',type=Path,default=None,
                    help='New isolated report directory outside --run')
    ap.add_argument('--register-output',type=Path,default=None,
                    help='Register-oracle-only NEW report directory outside the source run')
    ap.add_argument('--register-max-games',type=int,default=0,
                    help='Register-oracle-only max games; 0 means all certified games')
    ap.add_argument('--register-train-per-game',type=int,default=0,
                    help='Register-oracle-only train examples per game; 0 means all')
    ap.add_argument('--register-dev-per-game',type=int,default=0,
                    help='Register-oracle-only dev examples per game; 0 means all')
    ap.add_argument('--register-probe-limit',type=int,default=128,
                    help='Max frozen-feature training examples per role group')
    ap.add_argument('--flat-output',type=Path,default=None,
                    help='Flat-registers-only NEW experimental report directory')
    ap.add_argument('--flat-epochs',type=int,default=160,
                    help='Flat-registers-only complete training passes')
    ap.add_argument('--flat-batch-size',type=int,default=256)
    ap.add_argument('--flat-lr',type=float,default=.004)
    ap.add_argument('--flat-seed',type=int,default=1729)
    ap.add_argument('--flat-max-games',type=int,default=0,
                    help='Flat-registers-only games limit (0=all)')
    ap.add_argument('--flat-train-per-game',type=int,default=0)
    ap.add_argument('--flat-dev-per-game',type=int,default=0)
    ap.add_argument('--attribution-output',type=Path,default=None,
                    help='Flat-attribution-only NEW report directory outside completed flat run')
    ap.add_argument('--prompt-output',type=Path,default=None,
                    help='Flat-prompt-only NEW report directory outside completed flat run')
    ap.add_argument('--expanded-output',type=Path,default=None,
                    help='head-100 only: new sibling output dir, or existing output with --expanded-resume')
    ap.add_argument('--expanded-resume',action='store_true')
    ap.add_argument('--expanded-samples',type=int,default=100)
    ap.add_argument('--expanded-curvature',type=float,default=4.0)
    ap.add_argument('--expanded-hidden',default='4096,2048,512,128')
    ap.add_argument('--expanded-backend',choices=('offload-cuda','cpu','full-cuda'),default='offload-cuda',
                    help='head-100 only: offload-cuda keeps full-precision AdamW on CPU')
    ap.add_argument('--expanded-precision',choices=('float16','bfloat16'),default='float16')
    ap.add_argument('--expanded-batch',type=int,default=4)
    ap.add_argument('--expanded-capture-batch',type=int,default=2)
    ap.add_argument('--expanded-max-length',type=int,default=None)
    ap.add_argument('--expanded-seed',type=int,default=47)
    ap.add_argument('--expanded-lr',type=float,default=.0002)
    ap.add_argument('--expanded-weight-decay',type=float,default=.0001)
    ap.add_argument('--expanded-grad-clip',type=float,default=1.0)
    ap.add_argument('--expanded-eval-every',type=int,default=100)
    ap.add_argument('--expanded-save-every',type=int,default=100)
    ap.add_argument('--expanded-zero-focus',type=float,default=.5)
    ap.add_argument('--expanded-zero-radius',type=float,default=1.0)
    ap.add_argument('--expanded-diag-output',type=Path,default=None,
                    help='head-100-diagnostic-train: NEW read-only report directory')
    ap.add_argument('--expanded-diag-source-run',type=Path,default=None,
                    help='Source certified run when expanded run has a custom name')
    ap.add_argument('--expanded-diag-steps',type=int,default=12,
                    help='Number of isolated optimizer updates for each trial')
    ap.add_argument('--expanded-diag-eval-every',type=int,default=4)
    ap.add_argument('--expanded-diag-trials',default='mixed,lr-quarter,zero-focus,game:captain_crew_risk',
                    help='Comma-separated matched continuations; mixed,lr-quarter,zero-focus,game:NAME')
    ap.add_argument('--expanded-diag-train-eval',type=int,default=0,
                    help='Training examples evaluated at checkpoints (0=all)')
    ap.add_argument('--expanded-diag-ablation-cases',type=int,default=128,
                    help='Development examples in fixed-weight input ablations (0=skip)')
    ap.add_argument('--expanded-diag-precision-cases',type=int,default=4,
                    help='Cases in CPU FP32 / GPU FP16 logit comparison (0=skip)')
    ap.add_argument('--expanded-diag-matched-steps',type=int,default=0,
                    help='Optional same-capacity scratch comparison: full100 vs last-token-repeated100; 0 skips')
    ap.add_argument('--radius-output',type=Path,default=None,
                    help='head-100-radius: NEW sibling report directory')
    ap.add_argument('--radius-source-run',type=Path,default=None,
                    help='Original certified run, if expanded run name is custom')
    ap.add_argument('--radius-contexts',type=int,default=12,
                    help='Different certified games for numerical radius probing')
    ap.add_argument('--radius-width',type=int,default=2,
                    help='Probe all integers within [-R,R], including zero')
    ap.add_argument('--radius-permutations',type=int,default=64,
                    help='Null-label control resamples per activation stage')
    ap.add_argument('--radius-checkpoint',choices=('best','latest'),default='best',
                    help='Frozen expanded C0 checkpoint to trace')
    ap.add_argument('--radius-recovery',action='store_true',
                    help='After read-only radius capture, run within/heldout nonlinear recovery probes')
    ap.add_argument('--radius-recovery-output',type=Path,default=None,
                    help='Optional NEW analysis report directory; source captures remain read-only')
    ap.add_argument('--radius-recovery-folds',type=int,default=4,
                    help='Held-out whole-game folds for read-only recovery probes')
    ap.add_argument('--radius-recovery-rank',type=int,default=12,
                    help='Rank of TRAIN-game numerical slope/curvature projection')
    ap.add_argument('--radius-recovery-controls',type=int,default=1,
                    help='Number of shuffled train-label controls for nonlinear decoder')
    ap.add_argument('--repr-output',type=Path,default=None,
                    help='Representation-diagnostic-only NEW report folder')
    ap.add_argument('--repr-max-games',type=int,default=0,
                    help='Deterministic selected game sample; 0=all 146 (full diagnostic)')
    ap.add_argument('--repr-train-per-game',type=int,default=0,
                    help='Matched train rows per game; 0=all')
    ap.add_argument('--repr-dev-per-game',type=int,default=0,
                    help='Matched untouched dev rows per game; 0=all')
    ap.add_argument('--repr-perturbations',type=int,default=16,
                    help='Dev prompt one-register mutation controls per model; 0 disables')
    ap.add_argument('--repr-skip-base',action='store_true',
                    help='Omit matched base-Qwen capture if GPU budget is constrained')
    return ap


def main(argv=None):
    args=parser().parse_args(argv)
    if args.phase!='head' and (args.zero_line_focus is not None or args.zero_line_radius is not None):
        raise ValueError('GAMER_ZERO_LINE_FOCUS_ONLY_HEAD')
    diag_options=('diag_output','diag_head_steps','diag_sequence_steps','diag_eval_every',
                  'diag_lora_steps','diag_seeds','diag_max_games','diag_train_per_game',
                  'diag_dev_per_game','diag_game_limit','diag_radius','diag_capture_batch',
                  'diag_checkpoint_every')
    diag_defaults={'diag_output':None,'diag_head_steps':120,'diag_sequence_steps':90,
                   'diag_eval_every':10,'diag_lora_steps':12,'diag_seeds':2,'diag_max_games':0,
                   'diag_train_per_game':0,'diag_dev_per_game':0,'diag_game_limit':12,
                   'diag_radius':1.0,'diag_capture_batch':2,'diag_checkpoint_every':0}
    if args.phase!='diagnostic-train' and any(getattr(args,k)!=diag_defaults[k] for k in diag_options):
        raise ValueError('GAMER_DIAGNOSTIC_FLAGS_ONLY_DIAGNOSTIC_TRAIN')
    localize_options=('localize_games','localize_start','localize_steps',
                      'localize_seeds','localize_radius','localize_output')
    localize_defaults={'localize_games':'captain_crew_risk,bertrand_oligopoly',
                       'localize_start':'both','localize_steps':20,
                       'localize_seeds':2,'localize_radius':1.,'localize_output':None}
    if args.phase!='diagnostic-localize' and any(
            getattr(args,k)!=v for k,v in localize_defaults.items()):
        raise ValueError('GAMER_LOCALIZE_FLAGS_ONLY_DIAGNOSTIC_LOCALIZE')
    register_defaults={'register_output':None,'register_max_games':0,
                       'register_train_per_game':0,'register_dev_per_game':0,
                       'register_probe_limit':128}
    if args.phase!='register-oracle' and any(
            getattr(args,k)!=v for k,v in register_defaults.items()):
        raise ValueError('GAMER_REGISTER_FLAGS_ONLY_REGISTER_ORACLE')
    flat_defaults={'flat_output':None,'flat_epochs':160,'flat_batch_size':256,
                   'flat_lr':.004,'flat_seed':1729,'flat_max_games':0,
                   'flat_train_per_game':0,'flat_dev_per_game':0}
    if args.phase!='flat-registers' and any(
            getattr(args,k)!=v for k,v in flat_defaults.items()):
        raise ValueError('GAMER_FLAT_FLAGS_ONLY_FLAT_REGISTERS')
    if args.phase!='flat-attribution' and args.attribution_output is not None:
        raise ValueError('GAMER_ATTRIBUTION_FLAGS_ONLY_FLAT_ATTRIBUTION')
    if args.phase!='flat-prompt' and args.prompt_output is not None:
        raise ValueError('GAMER_PROMPT_FLAGS_ONLY_FLAT_PROMPT')
    repr_defaults={'repr_output':None,'repr_max_games':0,'repr_train_per_game':0,
                   'repr_dev_per_game':0,'repr_perturbations':16,'repr_skip_base':False}
    if args.phase!='representation-diagnostic' and any(
            getattr(args,k)!=v for k,v in repr_defaults.items()):
        raise ValueError('GAMER_REPR_FLAGS_ONLY_REPRESENTATION_DIAGNOSTIC')
    expanded_defaults={'expanded_output':None,'expanded_resume':False,'expanded_samples':100,
        'expanded_curvature':4.0,'expanded_hidden':'4096,2048,512,128',
        'expanded_backend':'offload-cuda','expanded_precision':'float16',
        'expanded_batch':4,'expanded_capture_batch':2,'expanded_max_length':None,
        'expanded_seed':47,'expanded_lr':.0002,'expanded_weight_decay':.0001,
        'expanded_grad_clip':1.0,'expanded_eval_every':100,'expanded_save_every':100,
        'expanded_zero_focus':.5,'expanded_zero_radius':1.0}
    if args.phase!='head-100' and any(
            getattr(args,k)!=v for k,v in expanded_defaults.items()):
        raise ValueError('GAMER_EXPANDED_FLAGS_ONLY_HEAD_100')
    exp_diag_defaults={'expanded_diag_output':None,'expanded_diag_source_run':None,
       'expanded_diag_steps':12,'expanded_diag_eval_every':4,
       'expanded_diag_trials':'mixed,lr-quarter,zero-focus,game:captain_crew_risk',
       'expanded_diag_train_eval':0,'expanded_diag_ablation_cases':128,
       'expanded_diag_precision_cases':4,'expanded_diag_matched_steps':0}
    if args.phase!='head-100-diagnostic-train' and any(
            getattr(args,k)!=v for k,v in exp_diag_defaults.items()):
        raise ValueError('GAMER_EXP_DIAG_FLAGS_ONLY_EXPANDED_DIAGNOSTIC_TRAIN')
    radius_defaults={'radius_output':None,'radius_source_run':None,
        'radius_contexts':12,'radius_width':2,'radius_permutations':64,
        'radius_checkpoint':'best'}
    if args.phase!='head-100-radius' and any(
            getattr(args,k)!=v for k,v in radius_defaults.items()):
        raise ValueError('GAMER_RADIUS_FLAGS_ONLY_HEAD_100_RADIUS')
    recovery_defaults={'radius_recovery':False,'radius_recovery_output':None,
        'radius_recovery_folds':4,'radius_recovery_rank':12,'radius_recovery_controls':1}
    if args.phase not in ('head-100-radius','head-100-radius-recovery') and any(
            getattr(args,k)!=v for k,v in recovery_defaults.items()):
        raise ValueError('GAMER_RADIUS_RECOVERY_FLAGS_ONLY_RADIUS')
    if args.phase=='head-100-radius' and not args.radius_recovery and any(
            getattr(args,k)!=v for k,v in recovery_defaults.items() if k!='radius_recovery'):
        raise ValueError('GAMER_RADIUS_RECOVERY_FLAGS_REQUIRE_RADIUS_RECOVERY')
    if args.phase=='head-100-radius-recovery':
        if args.steps is not None:
            raise ValueError('GAMER_RADIUS_RECOVERY_READ_ONLY_NO_STEPS')
        if args.radius_recovery:
            raise ValueError('GAMER_RADIUS_RECOVERY_ALREADY_SELECTED_AS_PHASE')
        from tools.nanojev_gamer_v1_radius_recovery import run_recovery
        return run_recovery(args.run,output=args.radius_recovery_output,
            folds=args.radius_recovery_folds,rank=args.radius_recovery_rank,
            controls=args.radius_recovery_controls)
    if args.phase=='head-100-radius':
        if args.steps is not None:
            raise ValueError('GAMER_RADIUS_READ_ONLY_NO_TRAINING_STEPS')
        from tools.nanojev_gamer_v1_radius_activations import run_radius
        return run_radius(args.run,device=args.device,output=args.radius_output,
            source_run=args.radius_source_run,contexts=args.radius_contexts,
            radius=args.radius_width,permutations=args.radius_permutations,
            checkpoint=args.radius_checkpoint,
            recovery=args.radius_recovery,
            recovery_output=args.radius_recovery_output,
            recovery_folds=args.radius_recovery_folds,
            recovery_rank=args.radius_recovery_rank,
            recovery_controls=args.radius_recovery_controls)
    if args.phase=='head-100-diagnostic-train':
        if args.steps is not None:
            raise ValueError('GAMER_EXP_DIAG_USES_EXPANDED_DIAG_STEPS_NOT_STEPS')
        from tools.nanojev_gamer_v1_expanded_diagnostic_train import run_expanded_diagnostic
        return run_expanded_diagnostic(args.run,device=args.device,
            source_run=args.expanded_diag_source_run,output=args.expanded_diag_output,
            steps=args.expanded_diag_steps,eval_every=args.expanded_diag_eval_every,
            trials=args.expanded_diag_trials,train_eval=args.expanded_diag_train_eval,
            ablation_cases=args.expanded_diag_ablation_cases,
            precision_cases=args.expanded_diag_precision_cases,
            matched_steps=args.expanded_diag_matched_steps)
    if args.phase=='head-100':
        if args.steps is None or args.steps<1:
            raise ValueError('GAMER_EXPANDED_POSITIVE_STEPS_REQUIRED')
        from tools.nanojev_gamer_v1_expanded_head import run_expanded
        hidden=tuple(int(x.strip()) for x in args.expanded_hidden.split(','))
        return run_expanded(args.run,device=args.device,output=args.expanded_output,
            resume=args.expanded_resume,steps=args.steps,
            samples=args.expanded_samples,curvature=args.expanded_curvature,
            hidden=hidden,max_length=args.expanded_max_length,
            batch_size=args.expanded_batch,capture_batch=args.expanded_capture_batch,
            backend=args.expanded_backend,precision=args.expanded_precision,
            seed=args.expanded_seed,lr=args.expanded_lr,
            weight_decay=args.expanded_weight_decay,grad_clip=args.expanded_grad_clip,
            zero_focus=args.expanded_zero_focus,zero_radius=args.expanded_zero_radius,
            eval_every=args.expanded_eval_every,save_every=args.expanded_save_every)
    if args.phase=='representation-diagnostic':
        if args.steps is not None:
            raise ValueError('GAMER_REPR_DOES_NOT_TRAIN_WITH_STEPS')
        from tools.nanojev_gamer_v1_representation_diagnostic import run_representation_diagnostic
        return run_representation_diagnostic(args.run,device=args.device,output=args.repr_output,
            max_games=args.repr_max_games,train_per_game=args.repr_train_per_game,
            dev_per_game=args.repr_dev_per_game,perturbations=args.repr_perturbations,
            include_base=not args.repr_skip_base)
    if args.phase=='flat-prompt':
        if args.steps is not None:
            raise ValueError('GAMER_PROMPT_DOES_NOT_TRAIN_WITH_STEPS')
        from tools.nanojev_gamer_v1_prompt_fidelity import run_prompt_fidelity
        return run_prompt_fidelity(args.run,output=args.prompt_output)
    if args.phase=='flat-attribution':
        if args.steps is not None:
            raise ValueError('GAMER_ATTRIBUTION_DOES_NOT_TRAIN_WITH_STEPS')
        from tools.nanojev_gamer_v1_register_attribution import run_attribution
        return run_attribution(args.run,output=args.attribution_output)
    if args.phase=='flat-registers':
        if args.steps is not None:
            raise ValueError('GAMER_FLAT_USES_FLAT_EPOCHS_NOT_STEPS')
        from tools.nanojev_gamer_v1_flat_registers import run_flat_registers
        return run_flat_registers(args.run,output=args.flat_output,device=args.device,
            epochs=args.flat_epochs,batch_size=args.flat_batch_size,
            lr=args.flat_lr,seed=args.flat_seed,
            max_games=args.flat_max_games,
            train_per_game=args.flat_train_per_game,
            dev_per_game=args.flat_dev_per_game)
    if args.phase=='register-oracle':
        if args.steps is not None:
            raise ValueError('GAMER_REGISTER_DOES_NOT_TRAIN_WITH_STEPS')
        from tools.nanojev_gamer_v1_register_oracle import run_register_oracle
        return run_register_oracle(args.run,output=args.register_output,
            max_games=args.register_max_games,
            train_per_game=args.register_train_per_game,
            dev_per_game=args.register_dev_per_game,
            probe_limit=args.register_probe_limit)
    if args.phase=='diagnostic-localize':
        if args.steps is not None:
            raise ValueError('GAMER_LOCALIZE_USES_LOCALIZE_STEPS_NOT_STEPS')
        from tools.nanojev_gamer_v1_failure_localization import run_localization
        games=tuple(g.strip() for g in args.localize_games.split(','))
        starts=('fresh','checkpoint') if args.localize_start=='both' else (args.localize_start,)
        return run_localization(args.run,device=args.device,output=args.localize_output,
            games=games,starts=starts,steps=args.localize_steps,
            seeds=args.localize_seeds,radius=args.localize_radius)
    if args.phase=='diagnostic-train':
        if args.steps is not None:
            raise ValueError('GAMER_DIAGNOSTIC_TRAIN_USES_DIAG_HEAD_STEPS_NOT_STEPS')
        from tools.nanojev_gamer_v1_diagnostic_train import run_diagnostic_train
        return run_diagnostic_train(args.run,device=args.device,output=args.diag_output,
            head_steps=args.diag_head_steps,sequence_steps=args.diag_sequence_steps,
            eval_every=args.diag_eval_every,lora_steps=args.diag_lora_steps,
            seeds=args.diag_seeds,max_games=args.diag_max_games,
            train_per_game=args.diag_train_per_game,dev_per_game=args.diag_dev_per_game,
            game_limit=args.diag_game_limit,radius=args.diag_radius,
            capture_batch=args.diag_capture_batch,
            checkpoint_every=args.diag_checkpoint_every)
    if args.phase=='status':
        return status(args.run)
    if args.steps is None or args.steps<1:
        raise ValueError('GAMER_STAIRCASE_REQUIRES_POSITIVE_CUMULATIVE_STEPS')
    if args.phase=='head':
        return head(args.run,args.steps,args.device,
                    head_eval_every=args.head_eval_every,
                    capture_batch=args.capture_batch,head_save_every=args.head_save_every,
                    zero_line_focus=args.zero_line_focus,zero_line_radius=args.zero_line_radius)
    return lora(args.run,args.steps,args.device)


if __name__=='__main__':
    main()
