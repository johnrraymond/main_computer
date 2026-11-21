#!/usr/bin/env python3
"""Auditable, allowlisted inference-time meta-instructions for V3 experiments.

These are instructions to re-evaluate an answer, NOT changes to model weights,
not a replacement for source evidence, and not instructions to change the IR.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCHEMA = 'nanojev.contract.v3.meta_update.v1'
PROFILES = {
    'literal_evidence': (
        'Before answering, enumerate the required source_spans keys for the proposed V3 '
        'contract, then verify every quoted span is an exact substring of the original '
        'request, names the proper milestone if applicable, and demonstrates the exact '
        'value. Include the whole milestone source line in each milestone quotation. '
        'Never add extra source_spans keys, fabricate a quotation, or infer an unstated term.'
    ),
    'semantic_countercheck': (
        'Re-derive the candidate from the ORIGINAL CONTRACT REQUEST, not from a prior '
        'candidate or familiar example. Check each amount, deadline, review period, '
        'extension count, extension decision window and disposition independently '
        'against its source sentence. Recompute the escrow total from milestone values. '
        'Correct unsupported values in the new proposal; if the source is insufficient, '
        'ask about the specific term rather than inventing it.'
    ),
    'contradiction_authority': (
        'Read ALL original clauses, including later clauses. Check for two incompatible '
        'values for the same field and for added, reassigned or unilateral authority '
        'that the V3 governance IR cannot express. Do not silently discard either '
        'clause or reuse the original sample contract. When a conflict or unsupported '
        'authority is explicit, request clarification and name the exact conflict.'
    ),
    'full_recheck': (
        'Audit the ORIGINAL CONTRACT REQUEST before replying: (1) account for every '
        'explicit monetary, temporal, policy and authority clause; (2) detect material '
        'contradictions and unsupported roles; (3) reconcile total amount with milestone '
        'amounts; (4) derive each IR field only from an explicit source value; '
        '(5) produce one EXACT literal source quote for each required evidence field, '
        'including the complete milestone sentence; (6) refuse only for a real '
        'unresolved source defect, with the affected field identified. Do not copy '
        'an earlier contract or accept a syntactically valid but semantically '
        'different proposal.'
    ),
}


def hash_feedback(feedback: str) -> str:
    return hashlib.sha256(feedback.encode('utf-8')).hexdigest()


def load_meta_update(path: Path) -> tuple[str, dict]:
    """Use only an allowlisted, successful probe result; fail closed otherwise."""
    path = Path(path)
    raw = path.read_bytes()
    if len(raw) > 16_000:
        raise ValueError('Meta update file is oversized')
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('schema') != SCHEMA:
        raise ValueError('Invalid meta update schema')
    if data.get('status') != 'PROMOTED_ON_LOCAL_PROBES_NOT_INDEPENDENTLY_VALIDATED':
        raise ValueError('Meta update was not successfully locally promoted')
    profile = data.get('profile')
    if profile not in PROFILES or data.get('feedback') != PROFILES[profile]:
        raise ValueError('Meta update is not an allowlisted profile')
    if data.get('feedback_sha256') != hash_feedback(data['feedback']):
        raise ValueError('Meta update feedback hash mismatch')
    if not isinstance(data.get('evidence'), dict):
        raise ValueError('Meta update lacks validation evidence')
    return data['feedback'], data
