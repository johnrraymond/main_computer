#!/usr/bin/env python3
"""Qwen attention exact-count X/Y curriculum with optional Pass-One story bank.

Original v4 checkpoint identity, optimizer and stream stay unchanged. A separately
pinned Captain overlay remains available for historical runs. An optional fixed
per-batch training breakdown overrides all milestone/phase transitions.
RESULT/FINAL remain diagnostic only.
"""
from __future__ import annotations

import argparse
from collections import Counter
import os
import hashlib
import json
import math
from pathlib import Path
import random
import sys
from types import SimpleNamespace

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

import nanojev_altreg_independent_math_qwen_precision_v2 as base
import nanojev_altreg_math_v3_decoder_experiment as v3

SCHEMA = 'nanojev.altreg.attention.curriculum.v4.exact-mix'
VERSION = 'attention-curriculum-v4-exact-mix-v1+captain-fixed-breakdown-v3'
STAGES = ('registered_logbar', 'one_raw_operand', 'one_logbar_removed',
          'one_raw_no_logbar', 'plain_expression')
DEFAULT_PHASE_COUNTS = (
    (16, 0, 0, 0, 0),
    (12, 4, 0, 0, 0),
    (8, 4, 4, 0, 0),
    (6, 3, 3, 4, 0),
    (4, 3, 3, 3, 3),
)


def parse_counts(spec, batch_size):
    """Parse five exact counts, validating that one batch is filled exactly."""
    try:
        fields = [int(item.strip()) for item in spec.split(',')]
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError('CURRICULUM_COUNTS_MUST_BE_INTEGERS') from exc
    if len(fields) != len(STAGES) or any(n < 0 for n in fields):
        raise ValueError('CURRICULUM_COUNTS_NEED_FIVE_NONNEGATIVE_INTEGERS')
    if sum(fields) != batch_size:
        raise ValueError(f'CURRICULUM_COUNTS_SUM_MUST_EQUAL_BATCH_SIZE:{sum(fields)}!={batch_size}')
    return tuple(fields)


def resolve_phases(args):
    """Each phase is a 5-vector of exact examples per minibatch."""
    if args.mix_counts and args.phase_counts:
        raise ValueError('CURRICULUM_SELECT_MIX_COUNTS_OR_PHASE_COUNTS')
    if args.mix_counts:
        return (parse_counts(args.mix_counts, args.batch_size),)
    if args.phase_counts:
        return tuple(parse_counts(spec, args.batch_size) for spec in args.phase_counts)
    if args.batch_size != 16:
        raise ValueError('CURRICULUM_EXPLICIT_COUNTS_REQUIRED_FOR_NONDEFAULT_BATCH_SIZE')
    return DEFAULT_PHASE_COUNTS


def phase_counts(step, phase_steps, phases):
    index = min(len(phases)-1, max(0, (step-1)//phase_steps))
    return index, phases[index]


def exact_batch_stages(counts, rng):
    """Shuffle an exact quota of each selected class, without replacement."""
    stages = [stage for stage, n in zip(STAGES, counts) for _ in range(n)]
    rng.shuffle(stages)
    return stages

# The operation target is semantic, not the original 24 surface-template IDs.
# Multiple surface templates share semantic operations. Predict semantic IDs
# from prompt features rather than memorizing a template-key shortcut.
OP_CLASSES = (
    'add','subtract','reverse_subtract','multiply','absolute_difference',
    'maximum','greater_than','average','minimum','positive_select',
    'absolute_greater_than','double_subtract','double_sum','square_sum',
)
OP_GROUPS = {
    'sym_add':'add','written_add':'add','algebra_difference':'add',
    'sym_sub':'subtract','written_sub':'subtract','algebra_left':'subtract',
    'algebra_balance':'subtract',
    'sym_reverse_sub':'reverse_subtract','written_difference':'reverse_subtract',
    'algebra_right':'reverse_subtract',
    'sym_multiply':'multiply','written_product':'multiply',
    'sym_absdiff':'absolute_difference','sym_max':'maximum',
    'written_greater':'greater_than','conditional_gt':'greater_than',
    'written_average':'average','conditional_min':'minimum',
    'conditional_positive':'positive_select','conditional_abs':'absolute_greater_than',
    'multistep_double':'double_subtract','multistep_difference':'double_subtract',
    'multistep_combine':'double_sum','multistep_square':'square_sum',
}
if set(OP_GROUPS)!=set(base.OPERATIONS):
    raise RuntimeError('CURRICULUM_OPERATION_CATALOG_DRIFT')
OP_INDEX = {name:i for i,name in enumerate(OP_CLASSES)}

# The original five-class checkpoint config and its exact-count schedule remain
# byte-for-byte compatible. This additional, independently pinned schedule
# replaces selected *samples* in future minibatches, not their model/optimizer.
CAPTAIN_PLAN_SCHEMA = 'nanojev.altreg.attention.captain-overlay.v1'
CAPTAIN_PLAN_FILE = 'captain-numeracy-schedule.json'
PAIRED_PLAN_SCHEMA = 'nanojev.altreg.attention.captain-semantic-binding.v2'
PAIRED_PLAN_FILE = 'captain-paired-supervision.json'
FIXED_BREAKDOWN_SCHEMA = 'nanojev.altreg.attention.fixed-breakdown.v3'
FIXED_BREAKDOWN_FILE = 'captain-training-breakdown.json'
PAIRED_TARGET_WEIGHT = 1.0
# Additional paired, *distinct* semantic examples, without changing the original
# 16-place minibatch, v4 config, original stream, milestones, or optimizer.
SEMANTIC_FOCUS_PAIR_COUNT = 2
SEMANTIC_FOCUS_WEIGHT = 2.0
SEMANTIC_REPLAY_PAIR_COUNT = 1
SEMANTIC_REPLAY_WEIGHT = 1.0
SEMANTIC_FOCUS_VARIANTS = frozenset((
    'story_numeric_x_semantic_y','story_semantic_x_numeric_y',
    'story_semantic_with_op','story_semantic_no_op',
    'story_one_semantic_x','story_one_semantic_y',
    'story_op_only','story_raw',
))


def captain_coprime_stride(n, batch_size):
    """Smallest step stride at least batch_size+1, coprime with bank size."""
    if n < 1: raise ValueError('CURRICULUM_CAPTAIN_EMPTY_BANK')
    stride = batch_size+1
    while math.gcd(stride,n) != 1: stride += 1
    return stride


def focused_semantic_variant(counts):
    """Prefer the current curriculum's newest active semantic challenge."""
    return next((v for v in reversed(list(counts)) if v in SEMANTIC_FOCUS_VARIANTS), None)


def previous_semantic_variant(milestones, active_start, current):
    """Choose only the most recently graduated hard stage for light rehearsal."""
    for milestone in reversed(milestones):
        if milestone['start_step'] >= active_start: continue
        previous=focused_semantic_variant(milestone['counts'])
        if previous and previous != current: return previous
    return None

CAPTAIN_VARIANTS = (
    'registered_logbar', 'plain_expression', 'story_full_logbar',
    'story_one_logbar_x', 'story_one_logbar_y', 'story_numeric_registers',
    'story_numeric_no_op', 'story_numeric_x_semantic_y',
    'story_semantic_x_numeric_y', 'story_semantic_with_op',
    'story_semantic_no_op', 'story_one_semantic_x', 'story_one_semantic_y',
    'story_op_only', 'story_raw',
)
CAPTAIN_NEUTRAL_OP = frozenset((
    'plain_expression', 'story_numeric_no_op', 'story_semantic_no_op',
    'story_one_semantic_x', 'story_one_semantic_y', 'story_raw',
))
# We preferentially replace the hardest part of the old minibatch. Other
# registered classes remain represented for replay until the requested captain
# overlay grows beyond the old plain-expression count.
OVERLAY_REPLACE_ORDER = (4, 3, 2, 1, 0)


def parse_training_breakdown(spec, batch_size):
    """A permanent exact allocation of the WHOLE minibatch, no step milestones.

    Legacy stage names are unprefixed; Captain-bank stages require `captain:`.
    Zero quotas can be omitted. This is an exact count, not a percentage.
    """
    allowed = set(STAGES) | {'captain:' + v for v in CAPTAIN_VARIANTS}
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError('CURRICULUM_BREAKDOWN_EMPTY')
    counts = {}
    try:
        for part in spec.split(','):
            name, value = part.strip().rsplit('=', 1)
            name = name.strip()
            if name not in allowed or name in counts:
                raise ValueError('CURRICULUM_BREAKDOWN_UNKNOWN_OR_DUPLICATE:'+name)
            number = int(value.strip())
            if number < 0:
                raise ValueError('CURRICULUM_BREAKDOWN_NEGATIVE:'+name)
            if number:
                counts[name] = number
    except (AttributeError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith('CURRICULUM_BREAKDOWN_'):
            raise
        raise ValueError('CURRICULUM_BREAKDOWN_FORMAT:name=count,...') from exc
    if sum(counts.values()) != batch_size:
        raise ValueError(f'CURRICULUM_BREAKDOWN_SUM:{sum(counts.values())}!={batch_size}')
    if not any(name.startswith('captain:') for name in counts):
        raise ValueError('CURRICULUM_BREAKDOWN_CAPTAIN_REQUIRED')
    # A stable canonical representation: order-independent CLI semantics.
    return dict(sorted(counts.items()))


def fixed_breakdown_plan(args, output, saved_step, captain_cfg, bank, paired_plan):
    """Pin the fixed distribution independently of historical step milestones.

    An explicit change on a subsequent resume replaces the previous distribution
    at a saved checkpoint, preserving all earlier revisions in the sidecar.
    An omitted --training-mix reuses the last pinned distribution unchanged.
    """
    path = output / FIXED_BREAKDOWN_FILE
    requested = getattr(args, '_requested_training_mix', None)
    if not path.exists() and requested is None:
        return None
    if not args.resume or saved_step < 1 or not captain_cfg or not bank or not paired_plan:
        raise RuntimeError('CURRICULUM_BREAKDOWN_REQUIRES_PAIRED_CAPTAIN_RESUME')
    existing = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    if existing and (existing.get('schema') != FIXED_BREAKDOWN_SCHEMA or
            existing.get('captain_bank_sha256') != bank.sha256 or
            existing.get('batch_size') != args.batch_size or
            saved_step < existing.get('installed_after_step',-1)):
        raise RuntimeError('CURRICULUM_BREAKDOWN_SIDECAR_DRIFT')
    if existing and requested is None:
        return existing
    if existing and requested == existing['counts']:
        return existing
    if requested is None:
        raise RuntimeError('CURRICULUM_BREAKDOWN_MISSING')
    import shutil
    if not existing:
        for original, destination in ((output/'latest.pt', output/'latest-pre-breakdown.pt'),
                                      (output/'best-curriculum.pt', output/'best-pre-breakdown.pt')):
            if original.is_file() and not destination.exists():
                temp = destination.with_suffix('.tmp')
                shutil.copy2(original,temp)
                os.replace(temp,destination)
    # On changes, do not silently discard the existing champion.
    history = list(existing.get('history',[])) if existing else []
    if existing:
        # Explicit composition changes are experiments in the same lineage;
        # keep a restartable pre-change checkpoint and preceding champion.
        marker='%08d-%s'%(saved_step,existing['mix_sha256'][:12])
        for source, prefix in ((output/'latest.pt','latest-pre-mix-change-'),
                               (output/'best-curriculum.pt','best-pre-mix-change-')):
            backup=output/(prefix+marker+'.pt')
            if source.is_file() and not backup.exists():
                temporary=backup.with_suffix('.tmp')
                shutil.copy2(source,temporary)
                os.replace(temporary,backup)
        history.append({'installed_after_step': existing['installed_after_step'],
                        'counts':existing['counts'], 'mix_sha256':existing['mix_sha256']})
    canonical=json.dumps(requested,sort_keys=True,separators=(',',':')).encode('utf-8')
    plan = {'schema':FIXED_BREAKDOWN_SCHEMA,'installed_after_step':saved_step,
            'batch_size':args.batch_size, 'captain_bank_sha256':bank.sha256,
            'counts':requested,'mix_sha256':hashlib.sha256(canonical).hexdigest(),
            'history':history,'paired_orientation':True,
            'additional_focus_examples':0,'additional_replay_examples':0,
            'note':'Fixed exact core batch; Captain positions have the opposite orientation supervised with same XY.'}
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(plan,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    os.replace(tmp,path)
    return plan


def fixed_breakdown_batch(examples, breakdown, bank, step, batch_size, rng):
    """One exact per-batch allocation; paired mates carry same numerical label."""
    stages=[stage for stage, count in breakdown.items() for _ in range(count)]
    if len(stages) != batch_size:
        raise ValueError('CURRICULUM_BREAKDOWN_BATCH_SIZE_MISMATCH')
    rng.shuffle(stages)
    rows=list(examples)
    prompts=[None]*batch_size
    partners=[]
    for slot, stage in enumerate(stages):
        if stage.startswith('captain:'):
            variant=stage.split(':',1)[1]
            row,mate=bank.draw_with_partner('train',variant,step,slot,batch_size)
            if (row['a'],row['b'],row['operation']) != (mate['a'],mate['b'],mate['operation']):
                raise RuntimeError('CURRICULUM_CAPTAIN_PAIRED_TARGET_DRIFT')
            rows[slot]={'a':row['a'],'b':row['b'],'operation':row['operation']}
            prompts[slot]=row['prompt']
            partners.append((slot,mate['prompt']))
    return stages,rows,prompts,partners


def parse_captain_milestones(specs, batch_size):
    """Repeated START:variant=count,...; each milestone replaces the last."""
    milestones = []
    for spec in specs:
        try:
            start_text, assignments = spec.split(':', 1)
            start = int(start_text)
            pairs = [piece.strip() for piece in assignments.split(',')]
            counts = {}
            for piece in pairs:
                variant, number = piece.split('=', 1)
                variant = variant.strip()
                if variant not in CAPTAIN_VARIANTS or variant in counts:
                    raise ValueError('CURRICULUM_CAPTAIN_UNKNOWN_OR_DUPLICATE_VARIANT:'+variant)
                count = int(number)
                if count < 1:
                    raise ValueError('CURRICULUM_CAPTAIN_COUNTS_MUST_BE_POSITIVE')
                counts[variant] = count
        except (TypeError, AttributeError, ValueError) as exc:
            raise ValueError('CURRICULUM_CAPTAIN_MILESTONE_FORMAT:START:variant=count,...') from exc
        if start < 1 or not counts or sum(counts.values()) > batch_size:
            raise ValueError('CURRICULUM_CAPTAIN_MILESTONE_RANGE')
        if milestones and start <= milestones[-1]['start_step']:
            raise ValueError('CURRICULUM_CAPTAIN_MILESTONES_MUST_BE_INCREASING')
        milestones.append({'start_step': start, 'counts': counts})
    return milestones


def active_captain_counts(step, milestones):
    current = {}
    identity = 0
    for milestone in milestones:
        if step < milestone['start_step']:
            break
        current = milestone['counts']
        identity = milestone['start_step']
    return identity, dict(current)


def overlay_batch(base_stages, base_examples, milestone_counts, bank, step, batch_size, *, pair_prompts=False):
    """Stable exact-count overlay, optionally returning each selected prompt's mate.

    The legacy stream's draw count, selected Captain group, selected orientation
    and total training minibatch remain unchanged. Partners have the same X/Y
    labels but are NOT additional random samples from the bank.
    """
    if not milestone_counts:
        original = (list(base_stages), list(base_examples), [None]*len(base_stages))
        return (*original, []) if pair_prompts else original
    stages = list(base_stages)
    examples = list(base_examples)
    prompts = [None]*len(stages)
    partners = []
    replace = sorted(range(len(stages)), key=lambda idx: (OVERLAY_REPLACE_ORDER.index(STAGES.index(stages[idx])), idx))
    entries = [variant for variant, count in milestone_counts.items() for _ in range(count)]
    # The shuffled legacy minibatch determines the replacement positions. The
    # bank index is a pure function of step/slot: no additional RNG checkpoint.
    for rank, (slot, variant) in enumerate(zip(replace, entries)):
        if pair_prompts:
            row, mate = bank.draw_with_partner('train', variant, step, rank, batch_size)
            if (row['a'], row['b'], row['operation']) != (mate['a'], mate['b'], mate['operation']):
                raise RuntimeError('CURRICULUM_CAPTAIN_PAIRED_TARGET_DRIFT')
            partners.append((slot, mate['prompt']))
        else:
            row = bank.draw('train', variant, step, rank, batch_size)
        stages[slot] = 'captain:'+variant
        examples[slot] = {'a': row['a'], 'b':row['b'], 'operation':row['operation']}
        prompts[slot] = row['prompt']
    result = (stages, examples, prompts)
    return (*result, partners) if pair_prompts else result


def captain_operation_id(example, stage):
    if stage.startswith('captain:'):
        variant = stage.split(':', 1)[1]
        return 0 if variant in CAPTAIN_NEUTRAL_OP else base.OP_IDS[example['operation']]
    return 0 if stage == 'plain_expression' else base.OP_IDS[example['operation']]


class CaptainBank:
    """Read-only, sealed Pass-One SQLite training and independent dev prompts."""
    def __init__(self, root, *, expected_sha=None, excluded_pairs=()):
        from nanojev_training_task_store import TrainingTaskStore
        self.root = Path(root).expanduser().resolve(strict=True)
        manifest_path = self.root/'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('schema') != 'nanojev.captain-numeracy-pass1.generator.v1':
            raise RuntimeError('CURRICULUM_CAPTAIN_MANIFEST_SCHEMA_MISMATCH')
        self.path = self.root/manifest['databaseFile']
        file_sha = v3.sha256(self.path)
        if file_sha != manifest['databaseSha256'] or (expected_sha and file_sha != expected_sha):
            raise RuntimeError('CURRICULUM_CAPTAIN_BANK_SHA_MISMATCH')
        self.sha256 = file_sha
        self.manifest = manifest
        self.excluded_pairs = set(excluded_pairs)
        self.rows = {}
        with TrainingTaskStore(self.path) as store:
            store.verify()
            if store.stats() != manifest['stats']:
                raise RuntimeError('CURRICULUM_CAPTAIN_BANK_COUNTS_DRIFT')
            for split in ('train', 'dev'):
                for variant in CAPTAIN_VARIANTS:
                    forward = list(store.iter_examples(split, variant+'__forward', family='captain_numeracy_pass1'))
                    reverse = list(store.iter_examples(split, variant+'__reordered', family='captain_numeracy_pass1'))
                    if len(forward) != len(reverse) or [r['groupId'] for r in forward] != [r['groupId'] for r in reverse]:
                        raise RuntimeError('CURRICULUM_CAPTAIN_ORIENTATION_DRIFT')
                    pairs = []
                    for f, r in zip(forward, reverse):
                        target = f['target']
                        a,b,operation = int(target['x']), int(target['y']), target['operation']
                        if operation not in OP_GROUPS or (split == 'train' and operation in self.manifest['heldOutOperations']):
                            raise RuntimeError('CURRICULUM_CAPTAIN_OPERATION_NOT_SUPPORTED')
                        if split == 'train' and (min(a,b),max(a,b)) in self.excluded_pairs:
                            continue
                        if target != r['target']:
                            raise RuntimeError('CURRICULUM_CAPTAIN_TARGET_DRIFT')
                        pairs.append(({'a':a,'b':b,'operation':operation,'prompt':f['example']['prompt']},
                                      {'a':a,'b':b,'operation':operation,'prompt':r['example']['prompt']}))
                    if not pairs:
                        raise RuntimeError('CURRICULUM_CAPTAIN_EMPTY_SPLIT_VARIANT:'+split+':'+variant)
                    self.rows[(split,variant)] = pairs

    def draw_with_partner(self, split, variant, step, rank, batch_size):
        pairs = self.rows[(split,variant)]
        # Correct the former stride=batch_size aliasing. With a 4,096-row bank
        # and batch=16, the old sampler exposed only 256 rows per fixed slot.
        # This new schedule is activated ONLY under the pinned v2 sidecar.
        stride = captain_coprime_stride(len(pairs),batch_size)
        index = ((step-1)*stride + rank) % len(pairs)
        pair = pairs[index]
        orientation = (step+rank)%2
        return pair[orientation], pair[1-orientation]

    def draw(self, split, variant, step, rank, batch_size):
        # Historical sampling path: unpatched training remains identical.
        pairs=self.rows[(split,variant)]
        index=((step-1)*batch_size + rank) % len(pairs)
        return pairs[index][(step+rank)%2]

    def validation_pairs(self, variant, limit):
        pairs = self.rows[('dev',variant)]
        if len(pairs) < limit:
            raise RuntimeError('CURRICULUM_CAPTAIN_INSUFFICIENT_DEV_EXAMPLES')
        return pairs[:limit]


def captain_plan(args, output, *, saved_step, excluded_pairs):
    """Install once after a checkpoint; subsequent resumes reload pinned plan."""
    location = output/CAPTAIN_PLAN_FILE
    if location.exists():
        plan = json.loads(location.read_text(encoding='utf-8'))
        if plan.get('schema') != CAPTAIN_PLAN_SCHEMA:
            raise RuntimeError('CURRICULUM_CAPTAIN_PLAN_SCHEMA_MISMATCH')
        proposed = parse_captain_milestones(args.captain_milestone,args.batch_size)
        if proposed and proposed != plan['milestones']:
            raise RuntimeError('CURRICULUM_CAPTAIN_PLAN_ALREADY_PINNED')
        if args.captain_bank and args.captain_bank.expanduser().resolve() != Path(plan['bank_root']):
            raise RuntimeError('CURRICULUM_CAPTAIN_BANK_ROOT_CHANGED')
        root = Path(plan['bank_root'])
    else:
        if not args.captain_milestone:
            if args.captain_bank:
                raise ValueError('CURRICULUM_CAPTAIN_MILESTONES_REQUIRED')
            return None, None
        if not args.captain_bank:
            raise ValueError('CURRICULUM_CAPTAIN_BANK_REQUIRED')
        proposed = parse_captain_milestones(args.captain_milestone,args.batch_size)
        if proposed[0]['start_step'] <= saved_step:
            raise RuntimeError('CURRICULUM_CAPTAIN_MILESTONE_MUST_START_AFTER_SAVED_CHECKPOINT')
        root = args.captain_bank.expanduser().resolve(strict=True)
        plan = {'schema':CAPTAIN_PLAN_SCHEMA,'bank_root':str(root),'milestones':proposed,
                'installed_after_step':saved_step,'eval_examples':args.captain_eval_examples}
    if args.captain_eval_examples != plan['eval_examples'] and location.exists() and args.captain_eval_examples != 32:
        raise RuntimeError('CURRICULUM_CAPTAIN_EVAL_EXAMPLES_CHANGED')
    bank = CaptainBank(root, expected_sha=plan.get('database_sha256'), excluded_pairs=excluded_pairs)
    if not location.exists():
        if len(bank.rows[('dev', 'story_raw')]) < plan['eval_examples']:
            raise RuntimeError('CURRICULUM_CAPTAIN_INSUFFICIENT_DEV_EXAMPLES')
        # Preserve the old active champion before any captain score can promote
        # a new one. No optimizer, running checkpoint or source is changed.
        previous_best = output/'best-curriculum.pt'
        pre_captain_best = output/'best-pre-captain.pt'
        if previous_best.is_file() and not pre_captain_best.exists():
            import shutil
            shutil.copy2(previous_best,pre_captain_best)
        plan['database_sha256'] = bank.sha256
        temporary = location.with_suffix('.tmp')
        temporary.write_text(json.dumps(plan,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        os.replace(temporary,location)
    return plan,bank



def captain_paired_plan(args, output, saved_step, captain_cfg, bank):
    """Opt-in, permanently pinned resume-compatible correction.

    Keep the existing v4 config/checkpoint schema, optimizer and numeric stream
    unchanged. This sidecar is written at a checkpoint boundary and is reloaded
    automatically on all subsequent resumes. It cannot be silently uninstalled.
    """
    location = output/PAIRED_PLAN_FILE
    if location.exists():
        plan = json.loads(location.read_text(encoding='utf-8'))
        if (plan.get('schema') != PAIRED_PLAN_SCHEMA or
            plan.get('target_weight') != PAIRED_TARGET_WEIGHT or
            plan.get('focus_pair_count') != SEMANTIC_FOCUS_PAIR_COUNT or
            plan.get('focus_weight') != SEMANTIC_FOCUS_WEIGHT or
            plan.get('replay_pair_count') != SEMANTIC_REPLAY_PAIR_COUNT or
            plan.get('replay_weight') != SEMANTIC_REPLAY_WEIGHT or
            plan.get('sampler') != 'coprime-full-bank-v2' or
            not captain_cfg or plan.get('captain_bank_sha256') != bank.sha256 or
            saved_step < plan.get('installed_after_step',-1)):
            raise RuntimeError('CURRICULUM_CAPTAIN_PAIRED_PLAN_DRIFT')
        return plan
    if not args.captain_paired_supervision:
        return None
    if not args.resume or saved_step < 1 or not captain_cfg:
        raise RuntimeError('CURRICULUM_CAPTAIN_PAIRED_REQUIRES_CAPTAIN_CHECKPOINT_RESUME')
    # Preserve the running checkpoint AND the old averaged-score champion.
    # The first paired-score evaluation can legitimately promote a different
    # checkpoint, so never overwrite the last pre-fix best without a backup.
    import shutil
    for source, backup in ((output/'latest.pt',output/'latest-pre-paired.pt'),
                           (output/'best-curriculum.pt',output/'best-pre-paired.pt')):
        if source.is_file() and not backup.exists():
            temporary_backup = backup.with_suffix('.tmp')
            shutil.copy2(source,temporary_backup)
            os.replace(temporary_backup,backup)
    plan = {'schema': PAIRED_PLAN_SCHEMA, 'installed_after_step': saved_step,
            'captain_bank_sha256': bank.sha256,
            'target_weight': PAIRED_TARGET_WEIGHT,
            'focus_pair_count': SEMANTIC_FOCUS_PAIR_COUNT,
            'focus_weight': SEMANTIC_FOCUS_WEIGHT,
            'replay_pair_count': SEMANTIC_REPLAY_PAIR_COUNT,
            'replay_weight': SEMANTIC_REPLAY_WEIGHT,
            'sampler': 'coprime-full-bank-v2',
            'selection_metric': 'captain-semantic-paired-focus-v2'}
    temporary = location.with_suffix('.tmp')
    temporary.write_text(json.dumps(plan,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    os.replace(temporary,location)
    return plan


def paired_captain_target_loss(decoder,parent,backbone,tokenizer,device,
                               partners,examples,stages,max_tokens):
    """Supervise the other orientation of exactly the same selected examples.

    Uses ground-truth X and Y for both presentations. No decoder post-hoc
    averaging and no additional optimizer/stream RNG draws occur.
    """
    if not partners:
        raise ValueError('CURRICULUM_CAPTAIN_PAIRED_EMPTY')
    slots, prompts = zip(*partners)
    if len(slots) != len(set(slots)) or any(not stages[slot].startswith('captain:') for slot in slots):
        raise RuntimeError('CURRICULUM_CAPTAIN_PAIRED_INVALID_SLOTS')
    companion_examples = [examples[slot] for slot in slots]
    companion_stages = [stages[slot] for slot in slots]
    return supervised_captain_prompts(decoder,parent,backbone,tokenizer,device,
                                     list(prompts),companion_examples,companion_stages,max_tokens)


def supervised_captain_prompts(decoder,parent,backbone,tokenizer,device,
                               prompts,examples,stages,max_tokens):
    if not prompts or len(prompts)!=len(examples) or len(prompts)!=len(stages):
        raise ValueError('CURRICULUM_CAPTAIN_FOCUS_PROMPT_TARGET_MISMATCH')
    flat,seq,mask = v3.full_capture(backbone,tokenizer,device,list(prompts),max_tokens)
    with torch.no_grad():
        h = parent.extract(flat.to(device))
        previous_xy = parent.xy_coord(h)
    ops = operation_ids(examples,stages,device)
    gold = torch.tensor([[row['a'],row['b']] for row in examples],
                        device=device,dtype=torch.float32)
    prediction = decoder(h,previous_xy,ops,seq.to(device),mask.to(device))
    supervised_loss,_ = v3.objective(prediction,gold,base,'attention')
    return supervised_loss


def focus_semantic_prompts(bank,variant,step,active_captain_count,batch_size,*,n_pairs=SEMANTIC_FOCUS_PAIR_COUNT):
    """Retrieve distinct canonical bank groups and their two orientations."""
    if variant not in SEMANTIC_FOCUS_VARIANTS:
        raise ValueError('CURRICULUM_CAPTAIN_FOCUS_VARIANT_INVALID')
    prompts=[]; examples=[]; stages=[]
    for extra_rank in range(n_pairs):
        first,other=bank.draw_with_partner('train',variant,step,active_captain_count+extra_rank,batch_size)
        if (first['a'],first['b'],first['operation']) != (other['a'],other['b'],other['operation']):
            raise RuntimeError('CURRICULUM_CAPTAIN_FOCUS_TARGET_DRIFT')
        for row in (first,other):
            prompts.append(row['prompt'])
            examples.append({'a':row['a'],'b':row['b'],'operation':row['operation']})
            stages.append('captain:'+variant)
    return prompts,examples,stages


def captain_paired_accuracy(predicted,classified,targets,operation_gold):
    """Success means BOTH orientations decode the same target correctly.

    Unlike averaging decoded numbers, an extreme exponent in one presentation
    cannot be masked by, or contaminate, an otherwise correct prediction.
    """
    forward = np.abs(predicted['original']-targets)/np.maximum(1.,np.abs(targets))
    backward = np.abs(predicted['reversed']-targets)/np.maximum(1.,np.abs(targets))
    both = (forward<=.05)&(backward<=.05)
    operations = ((classified['original'].argmax(axis=1)==operation_gold)&
                  (classified['reversed'].argmax(axis=1)==operation_gold))
    worst = np.maximum(forward,backward)
    return {'joint_5pct':float(both.all(axis=1).mean()),
            'x_5pct':float(both[:,0].mean()),
            'y_5pct':float(both[:,1].mean()),
            'joint_5pct_and_operation':float((both.all(axis=1)&operations).mean()),
            'operand_5pct':float(both.mean()),
            'operand_1pct':float(((forward<=.01)&(backward<=.01)).mean()),
            'operand_0p1pct':float(((forward<=.001)&(backward<=.001)).mean()),
            'operation_accuracy':float(operations.mean()),
            'mean_abs_relative_error':float(worst.mean())}

def evaluate_captain_variant(decoder,op_head,parent,backbone,tokenizer,device,bank,variant,
                             max_tokens,batch_size,limit):
    decoder.eval();op_head.eval()
    pairs = bank.validation_pairs(variant,limit)
    predicted = {}; classified = {}
    targets = np.asarray([[a['a'],a['b']] for a,_ in pairs],dtype=np.float64)
    samples = [pair[0] for pair in pairs]
    operation_gold = operation_targets(samples,device).cpu().numpy()
    with torch.no_grad():
        for label, index in (('original',0),('reversed',1)):
            values = []; logits = []
            for start in range(0,len(pairs),batch_size):
                selected = [pair[index] for pair in pairs[start:start+batch_size]]
                flat,seq,mask=v3.full_capture(backbone,tokenizer,device,
                                              [e['prompt'] for e in selected],max_tokens)
                h=parent.extract(flat.to(device));p_xy=parent.xy_coord(h)
                ops=operation_ids(selected,['captain:'+variant]*len(selected),device)
                prediction=decoder(h,p_xy,ops,seq.to(device),mask.to(device))
                values.append(v3.predict(prediction,base,'attention').cpu().numpy())
                logits.append(op_head(seq.to(device),mask.to(device)).cpu().numpy())
            predicted[label]=np.concatenate(values).astype(np.float64)
            classified[label]=np.concatenate(logits).astype(np.float64)
    predicted['averaged']=(predicted['original']+predicted['reversed'])/2
    classified['averaged']=(classified['original']+classified['reversed'])/2
    result={}
    for label, guess in predicted.items():
        rel=np.abs(guess-targets)/np.maximum(1.,np.abs(targets))
        hit5=rel<=.05
        op_hit=classified[label].argmax(axis=1)==operation_gold
        result[label]={'operation_accuracy':float(op_hit.mean()),
                       'x_5pct':float(hit5[:,0].mean()),
                       'y_5pct':float(hit5[:,1].mean()),
                       'x_extreme_over_100pct':float((rel[:,0]>1.).mean()),
                       'y_extreme_over_100pct':float((rel[:,1]>1.).mean()),
                       'joint_5pct_and_operation':float((hit5.all(axis=1)&op_hit).mean()),
                       'joint_5pct':float(hit5.all(axis=1).mean()),
                       'operand_5pct':float(hit5.mean()),
                       'operand_1pct':float((rel<=.01).mean()),
                       'operand_0p1pct':float((rel<=.001).mean()),
                       'mean_abs_relative_error':float(rel.mean())}
    result['paired'] = captain_paired_accuracy(predicted,classified,targets,operation_gold)
    decoder.train();op_head.train()
    return result


class OperationHead(nn.Module):
    """Identify the semantic operation from contextual Qwen prompt tokens.

    Never sees the training label or the decoder's out-of-band operation ID.
    Frozen token features are sufficient inputs; the head itself trains.
    """
    def __init__(self, token_dim, hidden):
        super().__init__()
        self.classifier=nn.Sequential(
            nn.LayerNorm(token_dim), nn.Linear(token_dim,hidden), nn.GELU(),
            nn.Linear(hidden,len(OP_CLASSES)))

    def forward(self, tokens, mask):
        weights=mask.to(dtype=tokens.dtype).unsqueeze(-1)
        pooled=(tokens*weights).sum(dim=1)/weights.sum(dim=1).clamp(min=1)
        return self.classifier(pooled)


def operation_targets(examples,device):
    return torch.tensor([OP_INDEX[OP_GROUPS[e['operation']]] for e in examples],
                        dtype=torch.long,device=device)


def emit(name, **fields):
    print(json.dumps({'event':name, **fields},sort_keys=True,allow_nan=False),flush=True)


def instruction(a, b, operation):
    if operation == 'conditional_abs':
        return f'Is abs({a}) > abs({b})? Answer true or false.'
    return base.OPERATIONS[operation][2].replace(base.registers.FIRST,str(a)).replace(base.registers.SECOND,str(b))


def curriculum_prompt(a,b,operation,stage,reverse=False):
    """Every prompt has exact numeric ground truth (a,b) and no answer leakage."""
    if stage not in STAGES:raise ValueError('CURRICULUM_UNKNOWN_STAGE:'+str(stage))
    a=int(a);b=int(b)
    if stage == 'registered_logbar':
        return base.prompt(a,b,operation,reverse)
    if stage == 'plain_expression':
        prefix='Please solve: ' if reverse else 'operation: '
        return prefix+instruction(a,b,operation)+'\nAnswer:'
    first,second=base.registers.FIRST,base.registers.SECOND
    if stage == 'one_logbar_removed':
        # Both registers present; drop the LogBar for alternating operand side.
        x = f'{a}, {base.registers.logbar(a)}' if reverse else str(a)
        y = str(b) if reverse else f'{b}, {base.registers.logbar(b)}'
        lines=[f'{first} = {x}',f'{second} = {y}']
        if reverse:lines.reverse()
        return '\n'.join(lines)+'\noperation: '+base.OPERATIONS[operation][2]+'\nAnswer:'
    # Keep one operand registered; use the other literal directly in the task.
    # In one_raw_operand, the literal keeps its LogBar aid; in the next level
    # it does not. Alternate sides to prevent asymmetric shortcut learning.
    lose_first=bool(reverse)
    if lose_first:
        raw_a=str(a)
        if stage == 'one_raw_operand':raw_a+=f' [LogBar {base.registers.logbar(a)}]'
        line=f'{second} = {b}, {base.registers.logbar(b)}'
        question=instruction(raw_a,second,operation)
    else:
        raw_b=str(b)
        if stage == 'one_raw_operand':raw_b+=f' [LogBar {base.registers.logbar(b)}]'
        line=f'{first} = {a}, {base.registers.logbar(a)}'
        question=instruction(first,raw_b,operation)
    return line+'\noperation: '+question+'\nAnswer:'


def operation_ids(examples,stages,device):
    # Preserve historical conditioning on the original scaffolded stages.
    # Captain prompts without an explicit OP use the same masked ID as plain.
    # The operation classifier itself NEVER receives oracle operation IDs.
    if len(examples)!=len(stages):
        raise ValueError('CURRICULUM_BATCH_MISMATCH')
    return torch.tensor([captain_operation_id(e,stage) for e,stage in zip(examples,stages)],
                        dtype=torch.long,device=device)


def evaluate_stage(decoder,op_head,parent,backbone,tokenizer,device,examples,stage,max_tokens,batch_size):
    decoder.eval();op_head.eval()
    truth=np.asarray([[e['a'],e['b']] for e in examples],dtype=np.float64)
    preds={};op_preds={}
    targets=operation_targets(examples,device).cpu().numpy()
    with torch.no_grad():
        for label,rev in (('original',False),('reversed',True)):
            batches=[];op_batches=[]
            for offset in range(0,len(examples),batch_size):
                sample=examples[offset:offset+batch_size]
                prompts=[curriculum_prompt(e['a'],e['b'],e['operation'],stage,rev) for e in sample]
                flat,seq,mask=v3.full_capture(backbone,tokenizer,device,prompts,max_tokens)
                h=parent.extract(flat.to(device));parent_xy=parent.xy_coord(h)
                op=operation_ids(sample,[stage]*len(sample),device)
                prediction=decoder(h,parent_xy,op,seq.to(device),mask.to(device))
                batches.append(v3.predict(prediction,base,'attention').cpu().numpy())
                op_batches.append(op_head(seq.to(device),mask.to(device)).cpu().numpy())
            preds[label]=np.concatenate(batches).astype(np.float64)
            op_preds[label]=np.concatenate(op_batches).astype(np.float64)
    preds['averaged']=(preds['original']+preds['reversed'])/2
    op_preds['averaged']=(op_preds['original']+op_preds['reversed'])/2
    result={}
    for key,guess in preds.items():
        rel=np.abs(guess-truth)/np.maximum(1.,abs(truth))
        hit5=rel<=.05
        op_hit=op_preds[key].argmax(axis=1)==targets
        result[key]={'operation_accuracy':float(op_hit.mean()),
                     'joint_5pct_and_operation':float((hit5.all(axis=1)&op_hit).mean()),
                     'joint_5pct':float(hit5.all(axis=1).mean()),
                     'operand_5pct':float(hit5.mean()),
                     'operand_1pct':float((rel<=.01).mean()),
                     'operand_0p1pct':float((rel<=.001).mean()),
                     'mean_abs_relative_error':float(rel.mean())}
    decoder.train();op_head.train()
    return result


def make_config(args,source_sha,parent_sha,source_cfg):
    return {'schema':SCHEMA,'backbone':'qwen','source_sha256':source_sha,
        'parent_sha256':parent_sha,'source_best_step':int(args._source_step),
        'model_id':base.MODEL_ID,'source_root':str(args.source_root.resolve()),
        'parent_root':str(Path(source_cfg['parent_root']).resolve()),
        'parent_checkpoint':source_cfg['parent_checkpoint'],
        'seed':args.seed,'lr':args.lr,'batch_size':args.batch_size,
        'phase_steps':args.phase_steps,'hidden':source_cfg['hidden'],
        'max_prompt_tokens':source_cfg['max_prompt_tokens'],
        'validation_examples':args.validation_examples,'eval_every':args.eval_every,
        'report_every':args.report_every,'operation_loss_weight':args.operation_loss_weight,
        'operation_classes':list(OP_CLASSES),'operation_mapping':sorted(OP_GROUPS.items()),
        'phases':[list(x) for x in args._phases],
        'stages':list(STAGES),'side_channel':'oracle-op-id-scaffolded-masked-on-plain-expression',
        'parent_feature_semantics':'frozen-precision-v2'}


def snapshot(decoder,op_head,optimizer,stream,rng,step,cfg,best):
    result={'schema':SCHEMA,'config':cfg,'step':step,'head':decoder.state_dict(),
            'operation_head':op_head.state_dict(),
            'optimizer':optimizer.state_dict(),'stream':stream.state_dict(),
            'mix_rng_state':rng.getstate(),'best':best,
            'rng_cpu':torch.get_rng_state()}
    if torch.cuda.is_available():result['rng_cuda']=torch.cuda.get_rng_state_all()
    return result


def restore(checkpoint,decoder,op_head,optimizer,stream,rng,cfg):
    saved=torch.load(checkpoint,map_location='cpu',weights_only=True)
    if saved.get('schema')!=SCHEMA or saved.get('config')!=cfg:
        raise RuntimeError('CURRICULUM_RESUME_CONFIG_MISMATCH')
    decoder.load_state_dict(saved['head'],strict=True)
    op_head.load_state_dict(saved['operation_head'],strict=True)
    optimizer.load_state_dict(saved['optimizer'])
    stream.load_state_dict(saved['stream']);rng.setstate(saved['mix_rng_state'])
    torch.set_rng_state(saved['rng_cpu'])
    if torch.cuda.is_available() and saved.get('rng_cuda') is not None:
        torch.cuda.set_rng_state_all(saved['rng_cuda'])
    return saved['step'],saved['best']


def run(args):
    args._phases = resolve_phases(args)
    args._captain_milestones = parse_captain_milestones(args.captain_milestone,args.batch_size)
    if args.training_mix and args.captain_milestone:
        raise ValueError('CURRICULUM_BREAKDOWN_NOT_WITH_EXPLICIT_MILESTONES')
    args._requested_training_mix = (parse_training_breakdown(args.training_mix,args.batch_size)
                                     if args.training_mix else None)
    if args.captain_eval_examples < 1:
        raise ValueError('CURRICULUM_CAPTAIN_EVAL_EXAMPLES_INVALID')
    if args.steps < 0 or args.batch_size < 1 or args.phase_steps < 1 or args.eval_every < 1 or args.report_every < 1 or args.validation_examples < 1 or args.operation_loss_weight < 0:
        raise ValueError('CURRICULUM_INVALID_ARGUMENT')
    if args.preview:
        if args._requested_training_mix:
            emit('curriculum_fixed_breakdown',counts=args._requested_training_mix,
                 batch_size=args.batch_size,transitions='none',
                 paired_orientation=True,extra_semantic_examples=0)
        else:
            for i, counts in enumerate(args._phases):
                emit('curriculum_mix_phase', phase=i, start_step=i*args.phase_steps+1,
                     counts=dict(zip(STAGES, counts)), batch_size=args.batch_size)
            for milestone in args._captain_milestones:
                emit('curriculum_captain_milestone',**milestone)
        for stage in STAGES:
            for reverse in (False,True):
                emit('curriculum_prompt_preview',stage=stage,reverse=reverse,
                     text=curriculum_prompt(17,-3,'written_sub',stage,reverse))
        return 0
    if args.device=='auto':args.device='cuda' if torch.cuda.is_available() else 'cpu'
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('CURRICULUM_CUDA_UNAVAILABLE')
    output=args.output_root.expanduser().resolve()
    args.source_root=args.source_root.expanduser().resolve()
    if output==args.source_root or output in args.source_root.parents or args.source_root in output.parents:
        raise RuntimeError('CURRICULUM_SOURCE_OUTPUT_OVERLAP')
    if args.resume:
        source_path=output/'source-v3-attention.pt'
        if not source_path.is_file() or not (output/'latest.pt').is_file():
            raise RuntimeError('CURRICULUM_RESUME_FILES_MISSING')
    else:
        if output.exists() and any(output.iterdir()):
            raise RuntimeError('CURRICULUM_OUTPUT_NOT_EMPTY_USE_RESUME')
        source_path=args.source_root/(args.source_checkpoint+'.pt')
        if not source_path.is_file():raise FileNotFoundError('CURRICULUM_SOURCE_NOT_FOUND:'+str(source_path))
    source=torch.load(source_path,map_location='cpu',weights_only=True)
    source_cfg=source.get('config',{})
    if source.get('schema')!=v3.SCHEMA or source_cfg.get('arm')!='attention' or source_cfg.get('backbone')!='qwen':
        raise RuntimeError('CURRICULUM_SOURCE_IDENTITY_MISMATCH')
    args._source_step=int(source['step'])
    p_root=Path(source_cfg['parent_root']);p_name=source_cfg['parent_checkpoint']
    p_args=SimpleNamespace(parent_root=p_root,parent_checkpoint=p_name,backbone='qwen',device=args.device)
    parent_cp,cache,parent,parent_sha=v3.get_parent(base,p_args)
    if parent_sha!=source_cfg['parent_sha256']:
        raise RuntimeError('CURRICULUM_PARENT_CHANGED')
    source_sha=v3.sha256(source_path)
    cfg=make_config(args,source_sha,parent_sha,source_cfg)
    token_dim=(parent_cp['config']['input_dim']//8)*2
    torch.manual_seed(args.seed)
    if args.device=='cuda':torch.cuda.manual_seed_all(args.seed)
    decoder=v3.V3Decoder(parent_cp['config']['width'],len(base.OP_IDS),'attention',
                         math.floor(math.log10(base.registers.MAX_VALUE)),source_cfg['hidden'],token_dim).to(args.device)
    decoder.load_state_dict(source['head'],strict=True)
    op_head=OperationHead(token_dim,source_cfg['hidden']).to(args.device)
    optimizer=torch.optim.AdamW(list(decoder.parameters())+list(op_head.parameters()),lr=args.lr)
    reserved_tasks=base.registers.build_plan(parent_cp['config']['seed'])[1]
    reserved={(min(int(t['a']),int(t['b'])),max(int(t['a']),int(t['b']))) for t in reserved_tasks if t['split']!='train'}
    stream=base.Stream(args.seed+303,reserved)
    mix_rng=random.Random(args.seed+919)
    val_examples=cache['validation']['examples'][:args.validation_examples]
    if len(val_examples)!=args.validation_examples:raise RuntimeError('CURRICULUM_INSUFFICIENT_VALIDATION')
    initial=0;best={'step':0,'joint_5pct':-1.}
    if not args.resume:
        output.mkdir(parents=True,exist_ok=True)
        # Pin the source champion once; subsequent v3 training can update best.pt
        # without changing this curriculum's origin.
        base.atomic_save(output/'source-v3-attention.pt',source)
        source_sha=v3.sha256(output/'source-v3-attention.pt')
        cfg=make_config(args,source_sha,parent_sha,source_cfg)
        base.write_json(output/'fork.json',{'config':cfg,'source_step':args._source_step,
            'notice':'Frozen Qwen and parent; forked attention head only. RESULT/FINAL diagnostic-only.'})
    else:
        # The forked copy, not the live source best.pt, defines resume identity.
        expected_cfg=torch.load(output/'latest.pt',map_location='cpu',weights_only=True).get('config',{})
        if expected_cfg!=cfg:raise RuntimeError('CURRICULUM_RESUME_CONFIG_MISMATCH')
    saved_step = (int(torch.load(output/'latest.pt',map_location='cpu',weights_only=True)['step'])
                  if args.resume else 0)
    # The sealed plan is deliberately outside 'config'. Old checkpoints retain
    # their exact historical config and optimizer/stream states on resume.
    captain_cfg, captain_bank = captain_plan(args,output,saved_step=saved_step,excluded_pairs=reserved)
    if captain_cfg:
        emit('curriculum_captain_plan',bank_sha256=captain_cfg['database_sha256'],
             installed_after_step=captain_cfg['installed_after_step'],
             milestones=captain_cfg['milestones'],saved_step=saved_step)
    paired_plan = captain_paired_plan(args,output,saved_step,captain_cfg,captain_bank)
    if paired_plan:
        emit('curriculum_captain_paired_supervision',**paired_plan)
    fixed_plan = fixed_breakdown_plan(args,output,saved_step,captain_cfg,captain_bank,paired_plan)
    if fixed_plan:
        emit('curriculum_fixed_breakdown',counts=fixed_plan['counts'],
             batch_size=args.batch_size,transitions='none',
             installed_after_step=fixed_plan['installed_after_step'],
             mix_sha256=fixed_plan['mix_sha256'],saved_step=saved_step,
             paired_orientation=True,extra_semantic_examples=0,
             old_step_milestones_ignored=True)
    c=SimpleNamespace(device=args.device,dtype=args.dtype,allow_model_download=args.allow_model_download)
    _,backbone,tokenizer,model_device,_=base.cassette.load_source('qwen',c)
    if str(model_device)!=args.device or any(p.requires_grad for p in backbone.parameters()):
        raise RuntimeError('CURRICULUM_FROZEN_BACKBONE_INVALID')
    if args.resume:
        initial,best=restore(output/'latest.pt',decoder,op_head,optimizer,stream,mix_rng,cfg)
    else:
        base.atomic_save(output/'latest.pt',snapshot(decoder,op_head,optimizer,stream,mix_rng,0,cfg,best))
        emit('curriculum_fork_ready',source_step=args._source_step,output=str(output),version=VERSION)
    if initial>args.steps:raise RuntimeError('CURRICULUM_TARGET_BELOW_SAVED_STEP')
    for step in range(initial+1,args.steps+1):
        legacy_phase,legacy_counts=phase_counts(step,args.phase_steps,args._phases)
        phase=0 if fixed_plan else legacy_phase
        counts=(tuple(fixed_plan['counts'].get(stage,0) for stage in STAGES)
                if fixed_plan else legacy_counts)
        examples=[stream.get() for _ in range(args.batch_size)]
        if fixed_plan:
            stages,examples,bank_prompts,partners=fixed_breakdown_batch(
                examples,fixed_plan['counts'],captain_bank,step,args.batch_size,mix_rng)
            captain_identity=0
            captain_counts={stage.split(':',1)[1]: n for stage,n in fixed_plan['counts'].items()
                            if stage.startswith('captain:')}
        else:
            stages=exact_batch_stages(counts,mix_rng)
            captain_identity,captain_counts = active_captain_counts(step,captain_cfg['milestones']) if captain_cfg else (0,{})
            if captain_counts:
                if paired_plan:
                    stages,examples,bank_prompts,partners=overlay_batch(
                        stages,examples,captain_counts,captain_bank,step,args.batch_size,pair_prompts=True)
                else:
                    stages,examples,bank_prompts=overlay_batch(
                        stages,examples,captain_counts,captain_bank,step,args.batch_size)
                    partners=[]
            else:
                bank_prompts=[None]*len(stages)
                partners=[]
        prompts=[bank_prompts[j] if bank_prompts[j] is not None else
                 curriculum_prompt(e['a'],e['b'],e['operation'],stage,(step*args.batch_size+j)%2==1)
                 for j,(e,stage) in enumerate(zip(examples,stages))]
        flat,seq,mask=v3.full_capture(backbone,tokenizer,args.device,prompts,cfg['max_prompt_tokens'])
        ops=operation_ids(examples,stages,args.device)
        gold=torch.tensor([[e['a'],e['b']] for e in examples],device=args.device,dtype=torch.float32)
        with torch.no_grad():
            h=parent.extract(flat.to(args.device));old_xy=parent.xy_coord(h)
        out=decoder(h,old_xy,ops,seq.to(args.device),mask.to(args.device))
        xy_loss,parts=v3.objective(out,gold,base,'attention')
        operation_logits=op_head(seq.to(args.device),mask.to(args.device))
        operation_gold=operation_targets(examples,args.device)
        operation_loss=F.cross_entropy(operation_logits,operation_gold)
        loss=xy_loss+args.operation_loss_weight*operation_loss
        paired_loss = None
        semantic_focus_loss = None
        semantic_replay_loss = None
        # In fixed-breakdown mode *all* represented training tasks come from
        # the declared quotas. V2's hidden extra semantic/replay groups are
        # intentionally disabled: the user's breakdown is authoritative.
        semantic_focus_variant = (focused_semantic_variant(captain_counts)
                                  if paired_plan and not fixed_plan else None)
        semantic_replay_variant = (previous_semantic_variant(captain_cfg['milestones'],
            captain_identity,semantic_focus_variant) if semantic_focus_variant else None)
        if paired_plan and partners:
            paired_loss = paired_captain_target_loss(
                decoder,parent,backbone,tokenizer,args.device,
                partners,examples,stages,cfg['max_prompt_tokens'])
            # Objective uses a mean over the companion samples. Restore their
            # per-minibatch contribution: one supervised mate per Captain slot.
            loss = loss + paired_plan['target_weight']*(len(partners)/args.batch_size)*paired_loss
        if semantic_focus_variant:
            f_prompts,f_examples,f_stages=focus_semantic_prompts(
                captain_bank,semantic_focus_variant,step,sum(captain_counts.values()),
                args.batch_size,n_pairs=paired_plan['focus_pair_count'])
            semantic_focus_loss=supervised_captain_prompts(
                decoder,parent,backbone,tokenizer,args.device,
                f_prompts,f_examples,f_stages,cfg['max_prompt_tokens'])
            loss = loss + paired_plan['focus_weight']*(len(f_prompts)/args.batch_size)*semantic_focus_loss
        if semantic_replay_variant:
            r_prompts,r_examples,r_stages=focus_semantic_prompts(
                captain_bank,semantic_replay_variant,step,sum(captain_counts.values())+10,
                args.batch_size,n_pairs=paired_plan['replay_pair_count'])
            semantic_replay_loss=supervised_captain_prompts(
                decoder,parent,backbone,tokenizer,args.device,
                r_prompts,r_examples,r_stages,cfg['max_prompt_tokens'])
            loss = loss + paired_plan['replay_weight']*(len(r_prompts)/args.batch_size)*semantic_replay_loss
        if not torch.isfinite(loss):raise RuntimeError('CURRICULUM_NONFINITE_LOSS')
        optimizer.zero_grad(set_to_none=True);loss.backward()
        if any(p.grad is not None and not bool(torch.isfinite(p.grad).all()) for p in list(decoder.parameters())+list(op_head.parameters())):
            raise RuntimeError('CURRICULUM_NONFINITE_GRADIENT')
        nn.utils.clip_grad_norm_(list(decoder.parameters())+list(op_head.parameters()),1.)
        optimizer.step()
        if step%args.report_every==0:
            emit('curriculum_progress',step=step,target_step=args.steps,phase=phase,
                 mix_counts=dict(Counter(stages)),captain_milestone_step=captain_identity,
                 loss=float(loss.detach()),loss_components=parts,
                 operation_loss=float(operation_loss.detach()),
                 operation_accuracy=float((operation_logits.argmax(-1)==operation_gold).float().mean().detach()),
                 captain_paired_target_loss=(float(paired_loss.detach()) if paired_loss is not None else None),
                 captain_paired_slots=len(partners),
                 captain_semantic_focus_variant=semantic_focus_variant,
                 captain_semantic_focus_pairs=(paired_plan['focus_pair_count'] if semantic_focus_variant else 0),
                 captain_semantic_focus_loss=(float(semantic_focus_loss.detach()) if semantic_focus_loss is not None else None),
                 captain_semantic_replay_variant=semantic_replay_variant,
                 captain_semantic_replay_loss=(float(semantic_replay_loss.detach()) if semantic_replay_loss is not None else None),
                 examples_seen=stream.draws)
        if step%args.eval_every==0 or step==args.steps:
            # Validate all currently active prompt classes on the SAME reserved
            # numeric examples. Do not sample the untouched test split.
            effective_counts=Counter(stages)
            active = [stage for stage in STAGES if effective_counts.get(stage,0) > 0]
            scores = {
                name: evaluate_stage(decoder, op_head, parent, backbone, tokenizer,
                    args.device, val_examples, name, cfg['max_prompt_tokens'], args.batch_size)
                for name in active
            }
            if captain_counts:
                scores.update({
                    'captain:'+variant: evaluate_captain_variant(
                        decoder,op_head,parent,backbone,tokenizer,args.device,captain_bank,variant,
                        cfg['max_prompt_tokens'],args.batch_size,captain_cfg['eval_examples'])
                    for variant in captain_counts
                })
            # A graduated semantic rung still receives light training replay.
            # Keep its held-out score visible even after its original milestone.
            replay_name = 'captain:'+semantic_replay_variant if semantic_replay_variant else None
            if replay_name and replay_name not in scores:
                scores[replay_name] = evaluate_captain_variant(
                    decoder,op_head,parent,backbone,tokenizer,args.device,captain_bank,
                    semantic_replay_variant,cfg['max_prompt_tokens'],args.batch_size,
                    captain_cfg['eval_examples'])
            replay_joint = scores[replay_name]['paired']['joint_5pct'] if replay_name else None
            weighted_joint = sum(n * scores[name]['averaged']['joint_5pct']
                                 for name,n in effective_counts.items()) / args.batch_size
            # Only the NEW Captain score uses paired correctness; retain old
            # averaged metrics in JSON so historical plots remain comparable.
            robust_joint = sum(n * scores[name][
                                'paired' if paired_plan and name.startswith('captain:') else 'averaged'
                                ]['joint_5pct'] for name,n in effective_counts.items()) / args.batch_size
            objective = ('fixed-paired-joint-5pct-v3' if fixed_plan else
                         'captain-semantic-paired-focus-v2' if paired_plan else 'averaged-joint-5pct')
            selection_score = robust_joint if paired_plan else weighted_joint
            focus_name = ('captain:'+focused_semantic_variant(captain_counts)
                          if paired_plan and not fixed_plan and focused_semantic_variant(captain_counts) else None)
            focus_joint = scores[focus_name]['paired']['joint_5pct'] if focus_name else None
            # Mode change explicitly resets champion comparison; never compare
            # a paired score against the historical averaged-score champion.
            old_objective = best.get('selection_metric','averaged-joint-5pct')
            improved = (best.get('phase') != phase or
                        best.get('captain_milestone_step',0) != captain_identity or
                        (fixed_plan is not None and best.get('mix_sha256') != fixed_plan['mix_sha256']) or
                        old_objective != objective or
                        (focus_joint is not None and best.get('focus_joint_5pct') is not None and
                         (focus_joint,selection_score) > (best['focus_joint_5pct'],best['joint_5pct'])) or
                        (focus_joint is None and selection_score > best['joint_5pct']))
            if improved:
                best = {'step': step, 'phase': phase,
                        'captain_milestone_step': captain_identity,'joint_5pct': selection_score,
                        'selection_metric': objective,
                        'mix_sha256':fixed_plan['mix_sha256'] if fixed_plan else None,
                        'focus_joint_5pct': focus_joint,
                        'focus_variant': focus_name}
            base.write_json(output/('validation-step-%08d.json'%step),
                            {'step':step,'phase':phase,'mix_counts':dict(zip(STAGES,counts)),
                             'captain_milestone_step':captain_identity,
                             'effective_mix_counts':dict(effective_counts),
                             'fixed_breakdown_sha256':fixed_plan['mix_sha256'] if fixed_plan else None,
                             'captain_bank_sha256':captain_bank.sha256 if captain_bank else None,
                             'validation':scores,'weighted_joint_5pct':weighted_joint,
                             'weighted_robust_joint_5pct':robust_joint,
                             'focus_variant':focus_name,'focus_paired_joint_5pct':focus_joint,
                             'replay_variant':replay_name,'replay_paired_joint_5pct':replay_joint,
                             'selection_metric':objective,'best':best})
            base.atomic_save(output/'latest.pt',snapshot(decoder,op_head,optimizer,stream,mix_rng,step,cfg,best))
            if improved:
                winning_snapshot=snapshot(decoder,op_head,optimizer,stream,mix_rng,step,cfg,best)
                # Keep the last pre-captain phase champion untouched. Once a
                # new milestone changes selection weights it gets its own best.
                milestone_suffix=('-captain-%08d'%captain_identity) if captain_identity else ''
                if fixed_plan:
                    base.atomic_save(output/('best-fixed-mix-%s.pt'%fixed_plan['mix_sha256'][:12]),winning_snapshot)
                else:
                    base.atomic_save(output/('best-phase-%d%s.pt'%(phase,milestone_suffix)),winning_snapshot)
                base.atomic_save(output/'best-curriculum.pt',winning_snapshot)
            emit('curriculum_validation',step=step,phase=phase,best=best,
                 weighted_joint_5pct=weighted_joint,
                 weighted_robust_joint_5pct=robust_joint,selection_metric=objective,
                 focus_variant=focus_name,focus_paired_joint_5pct=focus_joint,
                 replay_variant=replay_name,replay_paired_joint_5pct=replay_joint,
                 mix_counts=dict(effective_counts),captain_milestone_step=captain_identity,
                 metrics={name:(v['paired'] if paired_plan and name.startswith('captain:')
                                else v['averaged']) for name,v in scores.items()})
    emit('curriculum_complete',step=args.steps,source_step=args._source_step,output=str(output),best=best)
    return 0


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version',action='version',version=VERSION)
    p.add_argument('--preview',action='store_true')
    p.add_argument('--source-root',type=Path,default=Path.home()/'NanoJev'/'runs'/'independent_altreg_math_qwen_v3_attention')
    p.add_argument('--source-checkpoint',choices=('best','latest'),default='best')
    p.add_argument('--output-root',type=Path,default=Path.home()/'NanoJev'/'runs'/'independent_altreg_math_qwen_curriculum_v4_exact_mix')
    p.add_argument('--steps',type=int,default=12000,help='Total NEW curriculum steps, not original v3 step count')
    p.add_argument('--phase-steps',type=int,default=2000,help='Steps per phase; last phase persists')
    p.add_argument('--mix-counts',help='Single fixed five-count batch, e.g. 4,3,3,3,3')
    p.add_argument('--phase-counts',action='append',help='Repeat to define consecutive exact-count phases, e.g. --phase-counts 16,0,0,0,0 --phase-counts 8,4,2,2,0')
    p.add_argument('--batch-size',type=int,default=16)
    p.add_argument('--lr',type=float,default=2e-5)
    p.add_argument('--operation-loss-weight',type=float,default=0.05,help='Auxiliary operation classification weight; 0 disables its gradients')
    p.add_argument('--eval-every',type=int,default=500)
    p.add_argument('--report-every',type=int,default=50)
    p.add_argument('--validation-examples',type=int,default=32)
    p.add_argument('--seed',type=int,default=20261015)
    p.add_argument('--device',choices=('cpu','cuda','auto'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--captain-bank',type=Path,
        help='Verified Pass-One bank directory containing manifest.json and training-tasks.sqlite')
    p.add_argument('--training-mix',
        help='Fixed whole-batch exact quotas: registered_logbar=1,...,captain:story_semantic_no_op=2. '
             'No step-based transitions. Changing quotas requires explicit resume at checkpoint.')
    p.add_argument('--captain-milestone',action='append',default=[],
        help='Repeat future absolute steps: START:story_full_logbar=2,story_numeric_registers=1; each replaces earlier quotas')
    p.add_argument('--captain-eval-examples',type=int,default=32,
        help='Fixed held-out Pass-One dev examples per active captain representation')
    p.add_argument('--captain-paired-supervision',action='store_true',
        help='Opt in at a checkpoint: fix bank traversal, supervise Captain orientation pairs, and focus extra supervision on semantic operand binding')
    return p

if __name__=='__main__':raise SystemExit(run(parser().parse_args()))
