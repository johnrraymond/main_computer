#!/usr/bin/env python3
"""English-like ROM arithmetic notation for the sealed CLEF arithmetic bank.

Only text encoding changes: operands, label indices, candidate completions,
provenance and split assignments remain identical to the proven English control.
"""
from __future__ import annotations
import copy
import hashlib
import inspect
import json
from pathlib import Path

from nanojev_arithmetic_task_bank import LABELS, VARIANTS, render_prompt, label_for

REPRESENTATION = 'rom1-english-arithmetic-v1'
ROM_OUTPUT = Path(r'C:\Users\subsi\NanoJev\runs\clef_rom_arithmetic_fixed32_v1')


def render_rom(a: int, b: int, threshold: int) -> str:
    """No computed sum or ground-truth answer is exposed to the learner."""
    if any(type(v) is not int or v < 0 for v in (a, b, threshold)):
        raise ValueError('ROM_ARITH_INVALID_OPERANDS')
    return (f'ROM 1.0 - Arithmetic comparison\n'
            f'Let RESULT be the sum of {a} and {b}.\n'
            f'Compare RESULT with {threshold}.\n'
            f'Return LESS if RESULT is smaller than {threshold}.\n'
            f'Return EQUAL if RESULT is equal to {threshold}.\n'
            f'Return MORE if RESULT is greater than {threshold}.\n'
            'Choose exactly one answer: LESS, EQUAL, or MORE.\n'
            'Answer:')


def convert_presentation(sample: dict, target: dict, orientation: str) -> dict:
    """Fail closed if the provided question is not the original verified English one."""
    if orientation not in VARIANTS:
        raise RuntimeError('ROM_ARITH_INVALID_ORIENTATION')
    a, b, threshold = (target[k] for k in ('a', 'b', 'threshold'))
    if any(type(v) is not int or v < 0 for v in (a, b, threshold)):
        raise RuntimeError('ROM_ARITH_TARGET_INVALID')
    label = label_for(a, b, threshold)
    if target['sum'] != a+b or target['label'] != label or target['goldIndex'] != LABELS.index(label):
        raise RuntimeError('ROM_ARITH_TARGET_GOLD_DRIFT')
    left, right = (b, a) if orientation == 'reverse' else (a, b)
    original = render_prompt(left, right, threshold)
    candidates = sample.get('candidates', [])
    if (sample.get('gold_index') != LABELS.index(label)
            or [c.get('candidate_id') for c in candidates] != list(LABELS)):
        raise RuntimeError('ROM_ARITH_PRESENTATION_GOLD_DRIFT')
    for c in candidates:
        if c.get('paths') != [{'prompt': original, 'answer': ' ' + c['candidate_id']}]:
            raise RuntimeError('ROM_ARITH_ENGLISH_SOURCE_MISMATCH')
    rewritten = copy.deepcopy(sample)
    prompt = render_rom(left, right, threshold)
    for c in rewritten['candidates']:
        c['paths'][0]['prompt'] = prompt
    # The sample's semantic identity is unchanged; provenance is bound by mode.
    return rewritten


def selection_identity(examples: list, groups: dict) -> str:
    """Bind the 32 selected presentations and their ROM rendering."""
    rows = [(gid, variant, convert_presentation(sample, groups[gid]['target'], variant))
            for gid, variant, sample in examples]
    stable = json.dumps(rows, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(stable.encode('utf-8')).hexdigest()


def representation_fingerprint() -> str:
    """Fail resume/probe when the language renderer is modified."""
    source = inspect.getsource(render_rom).encode('utf-8')
    return hashlib.sha256(source).hexdigest()


def verify_prompt_visibility(*, samples: list, bundles: dict, args) -> dict:
    """Fail closed rather than allow suffix truncation of ROM arithmetic prompts."""
    max_seen = {label: {'prompt_tokens': 0, 'answer_tokens': 0} for label in bundles}
    for sample in samples:
        for candidate in sample['candidates']:
            for path in candidate['paths']:
                for label, bundle in bundles.items():
                    p=len(bundle.tokenizer.encode(path['prompt'], add_special_tokens=False))
                    a=len(bundle.tokenizer.encode(path['answer'], add_special_tokens=False))
                    budget=min(int(args.max_prompt_tokens), int(bundle.max_positions)-a)
                    if p>budget:
                        raise RuntimeError(f'ROM_ARITH_WOULD_TRUNCATE: backbone={label} prompt={p} budget={budget}')
                    max_seen[label]['prompt_tokens']=max(max_seen[label]['prompt_tokens'],p)
                    max_seen[label]['answer_tokens']=max(max_seen[label]['answer_tokens'],a)
    return max_seen
