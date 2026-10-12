#!/usr/bin/env python3
"""Read-only sealed binary task-bank and immutable CLEF cycle-136 cutover helpers.

No dataset generation, accuracy, or training loops live in this module.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import sqlite3
import sys
from typing import Any

TOOLS = Path(__file__).resolve().parent
SCHEMA = 'nanojev.binary-consensus-continuous.residual-v2'
PARENT_REPO = 'johnrraymond/NanoJev-CLEF'
PARENT_REVISION = '222a8e361121a5aa35e0ecd0e073d5f645ef54f9'
PARENT_RELEASE = 'clef-cycle-000136-reuse-002'
FAMILY = 'clef_binary_consensus'
VARIANTS = ('natural_rom', 'natural_rom_reverse')


def module(filename: str, alias: str):
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, TOOLS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError('BINARY_DEPENDENCY_MISSING: ' + filename)
    result = importlib.util.module_from_spec(spec)
    sys.modules[alias] = result
    spec.loader.exec_module(result)
    return result


def deps():
    champion = module('nanojev_three_backbone_clef_tinystories_structured_supervision_train.py', 'nanojev_binary_champion')
    rom = module('nanojev_three_backbone_clef_rom_structured_supervision_train.py', 'nanojev_binary_rom')
    objective = module('nanojev_objective_api.py', 'nanojev_binary_objective')
    return champion, rom, objective


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for piece in iter(lambda: f.read(1024 * 1024), b''):
            h.update(piece)
    return h.hexdigest()


def load_bank(bank_dir: Path, *, split: str, variants=VARIANTS, max_groups: int = 0):
    """Return (source, grouped variants) with fail-closed label and split checks.

    The DB is opened in read-only mode, never touched by the trainer/probe.
    """
    bank_dir = Path(bank_dir).expanduser().resolve(strict=True)
    manifest_path = bank_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('ok') is not True or manifest.get('binaryConsensusOnly') is not True:
        raise RuntimeError('BINARY_BANK_NOT_SEALED_BINARY_ONLY')
    if manifest.get('schema') != 'nanojev.training-task-generator.v1':
        raise RuntimeError('BINARY_BANK_SCHEMA_INVALID')
    if split not in ('train', 'dev', 'predev', 'test'):
        raise ValueError('BINARY_BANK_INVALID_SPLIT')
    if split == 'train' and tuple(variants) not in (VARIANTS, ('raw_rom', 'raw_rom_reverse'), ('rom333', 'rom333_reverse')):
        raise RuntimeError('BINARY_TRAIN_REQUIRES_BOTH_ORIENTATIONS')
    filename = bank_dir / 'training-tasks.sqlite'
    if not filename.is_file():
        raise RuntimeError('BINARY_BANK_DB_MISSING')
    conn = sqlite3.connect(filename.as_uri()+'?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('BINARY_BANK_SQLITE_INTEGRITY_FAILURE')
        records = conn.execute('''SELECT g.group_id,g.source_fingerprint,g.split,g.family,g.task,
                             g.oracle,g.label_status,g.target_json,g.provenance_json,
                             p.variant,p.sample_json,p.sample_sha256,c.source_json
                             FROM task_groups g JOIN presentations p USING(group_id)
                             JOIN cohorts c ON g.cohort_id=c.cohort_id
                             WHERE g.family=? AND g.split=? AND p.variant IN (%s)
                             ORDER BY g.group_id,p.variant''' % ','.join('?' for _ in variants),
                             (FAMILY,split,*variants)).fetchall()
        grouped = {}
        source_info = None
        for r in records:
            src = json.loads(r['source_json'])
            if source_info is None:
                source_info = src
            elif (src.get('sourceModel'),src.get('sourceRevision')) != (source_info.get('sourceModel'),source_info.get('sourceRevision')):
                raise RuntimeError('BINARY_BANK_SOURCE_MODEL_DRIFT')
            if (r['split'] != split or r['family'] != FAMILY or r['task'] != 'consensus_pairwise'
                or r['oracle'] != 'normalized-python-ast-equivalence-v1' or r['label_status'] != 'verified'):
                raise RuntimeError('BINARY_BANK_UNEXPECTED_TASK_OR_LABEL_AUTHORITY')
            target = json.loads(r['target_json'])
            provenance = json.loads(r['provenance_json'])
            sample_raw = r['sample_json']
            if hashlib.sha256(sample_raw.encode('utf-8')).hexdigest() != r['sample_sha256']:
                raise RuntimeError('BINARY_BANK_PRESENTATION_HASH_MISMATCH')
            sample = json.loads(sample_raw)
            label = str(target['label']).upper()
            gold = int(target['goldIndex'])
            if label not in ('SAME','DIFFERENT') or gold != (0 if label == 'SAME' else 1):
                raise RuntimeError('BINARY_BANK_INVALID_GOLD_LABEL')
            if (sample.get('task') != 'consensus_pairwise' or sample.get('gold_index') != gold
                or [str(c['candidate_id']).upper() for c in sample['candidates']] != ['SAME','DIFFERENT']
                or any(len(c['paths']) != 1 for c in sample['candidates'])):
                raise RuntimeError('BINARY_BANK_SAMPLE_CONTRACT_MISMATCH')
            if sample['candidates'][0]['paths'][0]['prompt'] != sample['candidates'][1]['paths'][0]['prompt']:
                raise RuntimeError('BINARY_BANK_BINARY_PROMPT_DRIFT')
            group = grouped.setdefault(r['group_id'], {'pair':target['pair'],'label':label,
                    'parent':provenance['parentFingerprint'],'topology':target['sourceTopology'],'variants':{}})
            if (group['pair'] != target['pair'] or group['label'] != label
                or group['parent'] != provenance['parentFingerprint'] or group['topology'] != target['sourceTopology']):
                raise RuntimeError('BINARY_BANK_GROUP_DRIFT')
            group['variants'][r['variant']] = sample
        if not grouped:
            raise RuntimeError('BINARY_BANK_SPLIT_EMPTY: '+split)
        all_groups = list(grouped.items())
        if max_groups:
            if max_groups < 1:
                raise ValueError('BINARY_BANK_MAX_GROUPS_INVALID')
            all_groups = all_groups[:max_groups]
        for _,g in all_groups:
            if set(g['variants']) != set(variants):
                raise RuntimeError('BINARY_BANK_ORIENTATION_MISSING')
        if split == 'train' and not max_groups:
            label_counts = {label:sum(v['label']==label for v in grouped.values()) for label in ('SAME','DIFFERENT')}
            if label_counts['SAME'] != label_counts['DIFFERENT']:
                raise RuntimeError('BINARY_BANK_LABELS_UNBALANCED: '+str(label_counts))
        if not source_info or not source_info.get('sourceModel') or not source_info.get('sourceRevision'):
            raise RuntimeError('BINARY_BANK_BACKBONE_PROVENANCE_MISSING')
        source = {'model':source_info['sourceModel'],'revision':source_info['sourceRevision']}
        identity = hashlib.sha256((hash_file(manifest_path) + ':' + hash_file(filename)).encode('ascii')).hexdigest()
        return {'bank_path':str(bank_dir),'manifest_sha256':identity,'split':split,
                'source':source, 'groups':all_groups,'count':len(all_groups)}
    finally:
        conn.close()


def pass_shard_order(n_shards: int, seed: int, pass_index: int) -> list[int]:
    if n_shards <= 0 or pass_index < 0:
        raise ValueError('BINARY_INVALID_PASS')
    ids = list(range(n_shards))
    random.Random(f'{seed}:pass:{pass_index}:shards').shuffle(ids)
    return ids


def shard_examples(groups: list, seed: int, pass_index: int, shard_index: int, *, variants=VARIANTS):
    """A pair's two orientations stay in its source family but both train separately."""
    yes = []
    no = []
    for group_id, g in groups:
        for variant in variants:
            row = (group_id, variant, g['variants'][variant])
            (yes if g['label'] == 'SAME' else no).append(row)
    rng = random.Random(f'{seed}:pass:{pass_index}:shard:{shard_index}')
    rng.shuffle(yes)
    rng.shuffle(no)
    if len(yes) == len(no):
        out = [row for pair in zip(yes, no) for row in pair]
    else:
        out = yes + no
        rng.shuffle(out)
    return out


def rolling_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else math.nan


def validate_training_controls(args):
    for key in ('grad_accumulation','shard_groups','rolling_questions','report_steps','checkpoint_steps'):
        if int(getattr(args,key)) <= 0:
            raise ValueError('BINARY_INVALID_POSITIVE_CONTROL: '+key)
    for key in ('target_loss','head_lr','grad_clip'):
        value = float(getattr(args,key))
        if not math.isfinite(value) or value <= 0:
            raise ValueError('BINARY_INVALID_NUMERIC_CONTROL: '+key)
    if not 0 < args.target_loss < math.log(2):
        raise ValueError('BINARY_TARGET_LOSS_OUTSIDE_BINARY_RANGE')
    if args.rolling_questions % args.grad_accumulation:
        raise ValueError('BINARY_ROLLING_WINDOW_MUST_DIVIDE_BY_GRAD_ACCUMULATION')
    if int(args.max_steps) < 0:
        raise ValueError('BINARY_NEGATIVE_MAX_STEPS')


# Match the original consensus-pairwise residual-tap learning phase. The pinned
# mature champion is an immutable anchor; all six residual taps are trainable.
RESIDUAL_FIELDS = (
    'memory', 'option_context', 'option_predictor',
    'option_terminal', 'option_question', 'global',
)
TRAINING_POLICY = 'cycle136-frozen-core-six-tinystories-residual-adapters-adamw-v2'


def configure_residual_only(head):
    expected = {f'tinystories_residual.{field}.weight' for field in RESIDUAL_FIELDS}
    found = {name for name, _ in head.named_parameters() if name.startswith('tinystories_residual.')}
    if found != expected:
        raise RuntimeError(f'RESIDUAL_ADAPTER_ARCHITECTURE_DRIFT: expected={sorted(expected)} observed={sorted(found)}')
    frozen = trainable = 0
    for name, param in head.named_parameters():
        enabled = name in expected
        param.requires_grad_(enabled)
        if enabled:
            trainable += param.numel()
        else:
            frozen += param.numel()
    if trainable != 7077888:
        raise RuntimeError(f'RESIDUAL_ADAPTER_SIZE_DRIFT: {trainable}')
    return {'policy': TRAINING_POLICY, 'frozen_parameters': frozen,
            'trainable_parameters': trainable,
            'trainable_names': sorted(expected)}


def build_residual_optimizer(*, torch, head, tiny, head_lr, weight_decay):
    if any(p.requires_grad for p in tiny.parameters()):
        raise RuntimeError('RESIDUAL_TINYSTORIES_BACKBONE_NOT_FROZEN')
    actual = [(name, p) for name, p in head.named_parameters() if p.requires_grad]
    expected = {f'tinystories_residual.{field}.weight' for field in RESIDUAL_FIELDS}
    if {name for name, _ in actual} != expected:
        raise RuntimeError('RESIDUAL_OPTIMIZER_WRONG_TRAINABLE_PARAMETERS')
    # Exactly the single-group AdamW policy of the original pairwise trainer.
    return torch.optim.AdamW([{'params': [p for _, p in actual],
              'lr': float(head_lr), 'weight_decay': float(weight_decay),
              'group_name': 'tinystories_residual_taps'}], foreach=False)


def capture_residual_before(head):
    return {name: p.detach().clone() for name, p in head.named_parameters() if p.requires_grad}


def inspect_residual_step(*, torch, head, before, logger, step):
    """Verify actual stored weights changed; gradients alone are not evidence."""
    named = dict(head.named_parameters())
    expected = {f'tinystories_residual.{field}.weight' for field in RESIDUAL_FIELDS}
    if set(before) != expected:
        raise RuntimeError('RESIDUAL_STEP_PARAMETER_SET_DRIFT')
    rows = []
    changed = 0
    for name in sorted(before):
        param = named[name]
        grad = param.grad
        if grad is not None and not bool(torch.isfinite(grad).all().item()):
            raise RuntimeError(f'RESIDUAL_NONFINITE_GRADIENT: {name}')
        delta = (param.detach().float() - before[name].float()).abs()
        nchanged = int((param.detach() != before[name]).sum().item())
        changed += nchanged
        rows.append({'name': name, 'dtype': str(param.dtype),
                     'gradient_max': round(float(grad.detach().abs().max().item()), 8) if grad is not None else None,
                     'changed_elements': nchanged,
                     'max_abs_delta': round(float(delta.max().item()), 10)})
    logger.emit('residual_optimizer_movement', step=int(step), changed_elements=changed,
                changed_adapters=sum(bool(x['changed_elements']) for x in rows), adapters=rows)
    if not changed:
        raise RuntimeError('RESIDUAL_OPTIMIZER_NO_WEIGHT_MOVEMENT: gradients may be rounded away')
    return rows


def load_model(*, torch, args, source, logger, checkpoint=None, load_optimizer=True):
    """Pinned champion cutover. Only head optimizer state is local/resumable."""
    from safetensors.torch import load_file
    champion, rom, _ = deps()
    parent = rom.resolve_parent_release(PARENT_REPO, PARENT_REVISION)
    if parent['release_name'] != PARENT_RELEASE:
        raise RuntimeError('BINARY_CHAMPION_RELEASE_MISMATCH')
    snapshot = Path(parent['snapshot']).resolve(strict=True)
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError('BINARY_CUDA_BF16_REQUIRED')
    bundles, _ = champion.smoke.load_backbones(
        source=source, local_files_only=args.local_files_only, logger=logger)
    frozen = champion.configure_backbone_trainability(bundles)
    if any(frozen.values()):
        raise RuntimeError('BINARY_BACKBONE_TRAINABILITY_VIOLATION')
    tiny = bundles[champion.TRAINABLE_LABEL].lm
    tiny.load_state_dict(load_file(str(snapshot/'tinystories.safetensors'), device='cpu'),strict=True)
    tiny.eval()
    torch.manual_seed(int(args.seed)+17)
    hidden_sizes={k:v.hidden_size for k,v in bundles.items()}
    head,_ = champion.build_layer_tap_head(torch=torch,hidden_sizes=hidden_sizes)
    head.load_state_dict(load_file(str(snapshot/'head.safetensors'),device='cpu'), strict=True)
    head=head.to(device='cuda',dtype=torch.bfloat16)
    policy = configure_residual_only(head)
    optimizer = build_residual_optimizer(torch=torch,head=head,tiny=tiny,
                                         head_lr=args.head_lr,weight_decay=args.weight_decay)
    if checkpoint is not None:
        ckpt=Path(checkpoint).expanduser().resolve(strict=True)
        head.load_state_dict(load_file(str(ckpt/'head.safetensors'), device='cpu'),strict=True)
        if load_optimizer:
            optimizer.load_state_dict(torch.load(ckpt/'optimizer.pt',map_location='cpu',weights_only=False))
            rng=torch.load(ckpt/'rng.pt',map_location='cpu',weights_only=False)
            random.setstate(rng['python'])
            torch.set_rng_state(rng['cpu'])
            torch.cuda.set_rng_state_all(rng['cuda'])
    logger.emit('binary_model_ready',head_trainable=champion.count_trainable(head),
                backbone_trainable=frozen, mature_core_frozen=policy['frozen_parameters'],
                residual_adapter_names=policy['trainable_names'],
                training_policy=TRAINING_POLICY)
    return head, bundles, optimizer, parent


def frozen_evidence(*,torch,champion,bundles,question,args):
    result={}
    for label in champion.FROZEN_LABELS:
        row,_=champion.extract_one_bundle(torch=torch,bundle=bundles[label],question=question,
                                         args=args,track_grad=False)
        result[label]=champion._cpu_detached_evidence(row)
    return result


def evidence_on_cuda(champion, rows):
    return {k:champion._cuda_evidence(v) for k,v in rows.items()}


def quiet_logger():
    class Logger:
        def emit(self,*_args,**_kwargs):
            pass
    return Logger()
