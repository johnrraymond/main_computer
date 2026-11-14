#!/usr/bin/env python3
"""Verified 32-example arithmetic learning sentinel for the live raw-ROM trainer.

No data generation, separate head, separate optimizer, held-out evaluation, or
accuracy calculation. This is the exact arithmetic *training* set and prompt
encoding used by nanojev_arithmetic_residual_micro_overfit.py. Its loss measures
memorization only; it is not evidence of arithmetic generalization.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path

from nanojev_arithmetic_runtime import load_bank as load_arithmetic_bank, DEFAULT_BANK
from nanojev_arithmetic_residual_micro_overfit import select_fixed_32
from nanojev_binary_consensus_runtime import (
    frozen_evidence, evidence_on_cuda, capture_residual_before, inspect_residual_step,
)

DEFAULT_ARITHMETIC_BANK = DEFAULT_BANK
CONTROL_ID = 'arithmetic-fixed32-interleaved-v1'


def load_control_plan(*, bank_dir: Path, seed: int, expected_source: dict):
    """Verify sealed bank, exact gold arithmetic, and fixed deterministic selection."""
    bank = load_arithmetic_bank(bank_dir, split='train')
    if bank['source'] != expected_source:
        raise RuntimeError('ROM_ARITH_CONTROL_BACKBONE_SOURCE_MISMATCH')
    examples = select_fixed_32(bank, seed)
    if len(examples) != 32 or len(set((gid, var) for gid, var, _ in examples)) != 32:
        raise RuntimeError('ROM_ARITH_CONTROL_EXAMPLE_SELECTION_DRIFT')
    # Bind the precisely selected presentations into the resume contract.
    stable = json.dumps(examples, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    selected_sha = hashlib.sha256(stable.encode('utf-8')).hexdigest()
    counts = {label: 0 for label in ('LESS', 'EQUAL', 'MORE')}
    for _, _, sample in examples:
        counts[('LESS', 'EQUAL', 'MORE')[sample['gold_index']]] += 1
    if counts != {'LESS': 12, 'EQUAL': 10, 'MORE': 10}:
        raise RuntimeError('ROM_ARITH_CONTROL_LABEL_DISTRIBUTION_DRIFT')
    return {'bank': bank, 'examples': examples, 'selection_sha256': selected_sha,
            'label_counts': counts}


def plan_identity(plan, args):
    return {'id': CONTROL_ID,
            'bank_manifest_sha256': plan['bank']['manifest_sha256'],
            'selection_sha256': plan['selection_sha256'],
            'bank_dir': str(Path(args.arithmetic_control_bank_dir).expanduser().resolve()),
            'control_every_rom_steps': args.arithmetic_control_every,
            'control_report_every': args.arithmetic_control_report_every,
            'control_target_loss': args.arithmetic_control_target_loss,
            'control_examples': 32}


def prepare(*, torch, champion, objective, bundles, args, plan, logger):
    prepared = []
    for gid, orientation, serialized in plan['examples']:
        question = champion.base.deserialize_question(serialized, objective)
        evidence = frozen_evidence(torch=torch, champion=champion, bundles=bundles,
                                   question=question, args=args)
        prepared.append((gid, orientation, question.gold_index, evidence))
    logger.emit('rom_arithmetic_control_evidence_ready', examples=len(prepared),
                label_counts=plan['label_counts'], accuracy_computed=False)
    return prepared


def fixed_loss(*, torch, champion, head, prepared):
    """Loss on the *same* 32 examples, not DEV and never accuracy."""
    was_training = head.training
    head.eval()
    try:
        with torch.inference_mode():
            losses = []
            for _, _, gold, rows in prepared:
                logits = head(evidence_on_cuda(champion, rows))
                loss = torch.nn.functional.cross_entropy(
                    logits.float().unsqueeze(0),
                    torch.tensor([gold], dtype=torch.long, device=logits.device))
                if not bool(torch.isfinite(loss).item()):
                    raise RuntimeError('ROM_ARITH_CONTROL_NONFINITE_FIXED_LOSS')
                losses.append(float(loss.item()))
        return sum(losses) / len(losses)
    finally:
        head.train(was_training)


def train_step(*, torch, champion, head, optimizer, params, prepared, args,
               control_steps, logger):
    """Exactly the shared raw-ROM forward/backward/AdamW path on arithmetic data."""
    start = (control_steps * args.grad_accumulation) % len(prepared)
    batch = [prepared[(start+i) % len(prepared)] for i in range(args.grad_accumulation)]
    optimizer.zero_grad(set_to_none=True)
    losses = []
    for _, _, gold, rows in batch:
        logits = head(evidence_on_cuda(champion, rows))
        loss = torch.nn.functional.cross_entropy(logits.float().unsqueeze(0),
                   torch.tensor([gold], dtype=torch.long, device=logits.device))
        if not bool(torch.isfinite(loss).item()):
            raise RuntimeError('ROM_ARITH_CONTROL_NONFINITE_TRAIN_LOSS')
        (loss / len(batch)).backward()
        losses.append(float(loss.detach().item()))
    norm = float(torch.nn.utils.clip_grad_norm_(params, args.grad_clip).item())
    if not math.isfinite(norm):
        raise RuntimeError('ROM_ARITH_CONTROL_NONFINITE_GRADIENT')
    before = (capture_residual_before(head) if control_steps == 0
              or (control_steps + 1) % args.arithmetic_control_report_every == 0
              else None)
    optimizer.step()
    if before is not None:
        # This *must* verify the very same adapters used by the ROM trainer.
        inspect_residual_step(torch=torch, head=head, before=before, logger=logger,
                              step=control_steps+1)
    return {'batch_loss': sum(losses)/len(losses), 'gradient_norm': norm}
