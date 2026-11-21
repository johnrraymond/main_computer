"""Frozen MIT Gamer G2 scope-aware decoder (promotion of the 0003 experiment).

This module intentionally reproduces the *identity_scope* inference arm of
nanojev_gamer_v1_mit_scope_0003 without importing the experiment, its sealed
answers, or any corpus/generator code. Edges are optimized again after removing
ineligible pair-label candidates. No gold, gradients, retraining, or state writes.

This is a fallible, punctuation/action-anchored constraint tested on the Gamer
synthetic-game distribution. It is not a general semantic-scope parser, and it
is NOT a certificate for a complete G1-G7 decision.
"""
from __future__ import annotations

import re

from tools import nanojev_gamer_v1_mit_decoder as projector
from tools import nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 as legacy
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

DECODER = 'identity_scope'
EVIDENCE = 'MIT_SCOPE_0003'
BLOCKED_MARGIN = -1000.0


def scopes(prompt, objects):
    """The frozen 0003 action-anchored continuation rule, unchanged in effect."""
    points = [0]
    for m in re.finditer(r'[;.\n]', prompt):
        i = m.start()
        if prompt[i] == '.' and i > 0 and i + 1 < len(prompt) and \
                prompt[i-1].isdigit() and prompt[i+1].isdigit():
            continue
        points.append(i + 1)
    points.append(len(prompt) + 1)
    parts = [(a, b) for a, b in zip(points, points[1:]) if b > a]

    def segment_of(span):
        if not 0 <= span[0] < span[1] <= len(prompt):
            raise ValueError('MIT_SCOPE_OBJECT_OUT_OF_BOUNDS')
        matches = [n for n, (a, b) in enumerate(parts) if a <= span[0] < b]
        if len(matches) != 1:
            raise ValueError('MIT_SCOPE_NO_SCOPE_FOR_OBJECT')
        return matches[0]

    per_object = [segment_of(obj) for obj in objects]
    anchored = {s for obj, s in zip(objects, per_object) if obj[2] == 'action'}
    scope_id = []
    current = -1
    for index in range(len(parts)):
        if current < 0 or index in anchored:
            current += 1
        scope_id.append(current)
    return [scope_id[s] for s in per_object]


def allowed(prompt, source, target, label, *, source_scope, target_scope):
    if label == 'refers_to':
        return (prompt[source[0]:source[1]].strip().casefold() ==
                prompt[target[0]:target[1]].strip().casefold())
    if label in ('measures', 'applies_to'):
        return source_scope == target_scope
    return True


def restrict_surface(surface, prompt):
    """Apply the tested restrictions *before* MILP, preserving source logits."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('MIT_SCOPE_EMPTY_PROMPT')
    objects, _pairs = legacy.validate_surface(surface)
    ids = scopes(prompt, objects)
    rejected = 0
    pairs = []
    for i, j, margins in surface['pair_margins']:
        updated = list(margins)
        for k, label in enumerate(BIND_CLASSES[1:]):
            if not allowed(prompt, objects[i], objects[j], label,
                           source_scope=ids[i], target_scope=ids[j]):
                updated[k] = BLOCKED_MARGIN
                rejected += 1
        pairs.append([i, j, updated])
    return {**surface, 'pair_margins': pairs}, rejected


def decode(surface, prompt, spec, *, return_diagnostics=False):
    constrained, rejected = restrict_surface(surface, prompt)
    predicted, status = projector.decode(constrained, spec, return_diagnostics=True)
    status = {'decoder': DECODER, 'scope_evidence': EVIDENCE,
              'blocked_pair_labels': rejected, **status}
    return (predicted, status) if return_diagnostics else predicted
