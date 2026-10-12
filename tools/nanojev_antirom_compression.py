#!/usr/bin/env python3
"""Six controlled prompt-only compression levels for sealed arithmetic evidence.

Op names are experimental strings, not a proposed universal ROM-code grammar.
No answer, computed sum, or gold class is included in any rendered prompt.
"""
from __future__ import annotations
import hashlib
import inspect
import json
from pathlib import Path

from nanojev_arithmetic_task_bank import render_prompt
from nanojev_rom_arithmetic_language import convert_presentation as verified_rom_conversion

LEVELS = ('C0', 'C1', 'C2', 'C3', 'C4', 'C5')
SCHEMA = 'antirom-arithmetic-compression-v1'
OUTPUT_ROOT = Path(r'C:\Users\subsi\NanoJev\runs\antirom_compression_sweep_v1')
DESCRIPTIONS = {
    'C0': 'Unchanged natural-English arithmetic control',
    'C1': 'Minimal English',
    'C2': 'Symbolic English with visible choices',
    'C3': 'Assembly-like mnemonic with visible choices',
    'C4': 'Mnemonic without displayed choices',
    'C5': 'Bare symbolic expression',
}


def representation(level: str) -> str:
    if level not in LEVELS:
        raise ValueError('ANTIROM_UNKNOWN_COMPRESSION_LEVEL: ' + str(level))
    return f'{SCHEMA}:{level}'


def output_for(level: str, root: Path = OUTPUT_ROOT) -> Path:
    representation(level)
    return Path(root) / level


def render(level: str, a: int, b: int, threshold: int) -> str:
    """Format the same addition/comparison; operands never include the result."""
    representation(level)
    if any(type(v) is not int or v < 0 for v in (a, b, threshold)):
        raise ValueError('ANTIROM_INVALID_OPERANDS')
    if level == 'C0':
        return render_prompt(a, b, threshold)
    if level == 'C1':
        return f'Add {a} and {b}. Compare with {threshold}.\nLESS, EQUAL, or MORE?\nAnswer:'
    if level == 'C2':
        return f'ADD {a} + {b}; COMPARE {threshold}\nLESS | EQUAL | MORE\nANS:'
    if level == 'C3':
        return f'ADD.CMP3 {a}+{b} ? {threshold}\nLESS|EQUAL|MORE\nANS:'
    if level == 'C4':
        return f'ADD.CMP3 {a}+{b} ? {threshold}\nANS:'
    return f'{a}+{b} ? {threshold}\nANS:'


def convert_presentation(sample: dict, target: dict, orientation: str, level: str) -> dict:
    """Verify the sealed English question, then rewrite only the prompt.

    Reuse the ROM 1.0 verifier rather than introduce a second gold authority.
    """
    representation(level)
    rewritten = verified_rom_conversion(sample, target, orientation)
    a, b, threshold = (target[k] for k in ('a', 'b', 'threshold'))
    left, right = (b, a) if orientation == 'reverse' else (a, b)
    prompt = render(level, left, right, threshold)
    for candidate in rewritten['candidates']:
        candidate['paths'][0]['prompt'] = prompt
    return rewritten


def renderer_fingerprint(level: str) -> str:
    """Bind both the formatter and baseline English renderer to checkpoints."""
    representation(level)
    payload = (SCHEMA, level, inspect.getsource(render), inspect.getsource(render_prompt),
               inspect.getsource(convert_presentation))
    return hashlib.sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()


def selection_fingerprint(examples: list, groups: dict, level: str) -> str:
    rows = [(gid, variant, convert_presentation(sample, groups[gid]['target'], variant, level))
            for gid, variant, sample in examples]
    payload = json.dumps(rows, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()
