#!/usr/bin/env python3
"""Versioned, deterministic title/family-cue removal for GAMER advisory routing.

The same transform is used for training and checkpoint inference. Only cue
strings (never their labels) are stored with the checkpoint. This does not
make classification into strategic computation and does not claim an independent
holdout where no independent wording exists.
"""
from __future__ import annotations

import re

SCHEMA = 'nanojev.gamer.v1.cue-blind-prompt-features.v1'


def titles_from_registry(registry):
    return sorted({title.strip() for title, _family, _scope in registry.values()
                   if title.strip()}, key=lambda title: (-len(title), title.lower()))


def family_names_from_registry(family_models):
    return sorted({name.strip() for name, _equation in family_models.values()
                   if name.strip()}, key=lambda name: (-len(name), name.lower()))


def blind_text(prompt: str, titles, family_names, *, mask_numbers=True) -> str:
    """Remove direct registry identifiers and catalog equations, not game rules.

    Never reads a correct answer, objective program, family label, or game id.
    Cue removal is label-neutral: the same input text always has the same
    representation regardless of its true game/label.
    """
    if not isinstance(prompt, str):
        raise TypeError('GAMER_CUE_BLIND_PROMPT_MUST_BE_TEXT')
    text = prompt
    # The catalog rows are *not specifications*. Their title, family name, and
    # mathematical formula are prefilled from the label registry and hence are
    # invalid family-recognition evidence. Remove the whole cue-bearing preface.
    if text.startswith('Game: ') and 'This entry is a catalog reference' in text:
        text = 'This entry is a catalog reference' + text.split('This entry is a catalog reference', 1)[1]
    elif text.startswith('Consider a proposed strategic transformation of ') and 'Describe the original and transformed game specifications' in text:
        text = ('Consider an unspecified strategic transformation. Describe the original and transformed game specifications'
                + text.split('Describe the original and transformed game specifications', 1)[1])
    for cue in (*titles, *family_names):
        if not cue:
            continue
        # Whole names only; avoid replacing words contained in longer words.
        text = re.sub(r'(?<!\w)' + re.escape(cue) + r'(?!\w)', ' GAME_CUE_REDACTED ', text, flags=re.IGNORECASE)
    text = re.sub(r'\bFamily\s*:\s*GAME_CUE_REDACTED\b', ' ', text, flags=re.I)
    # Make a single redacted title indistinguishable from a different title.
    text = re.sub(r'\s+', ' ', text).strip()
    if mask_numbers:
        # Same number masking as the legacy advisory router, but NEVER claim
        # this representation can solve payoff-sensitive decisions.
        from tools.nanojev_gamer_v1_prompt_db_train import _sanitize
        text = _sanitize(text)
    return text
