#!/usr/bin/env python3
"""Narrow repair of OMITTED source_spans using unique, literal source observations.

Never change a proposed contract, infer source values, delete extra citation keys,
repair an incorrect citation, or claim semantic correctness. All omissions are
listed in the run report, even when they can be mechanically attributed.
"""
from __future__ import annotations

from collections import defaultdict
import json

from tools import nanojev_contract_v3_live_head as bridge
from tools import nanojev_contract_v3_source_fidelity as fidelity


def diagnose(raw_head: str) -> dict:
    """Field-path diagnostics only; do not put raw model output in reports."""
    try:
        data = json.loads(raw_head)
        if not isinstance(data, dict) or data.get('status') != 'contract':
            return {'status': 'NOT_CONTRACT'}
        contract = bridge.govlang.validate(data.get('contract'))
        needed = set(bridge._expected_evidence(contract))
        spans = data.get('source_spans')
        if not isinstance(spans, dict):
            return {'status': 'CITATION_MAP_NOT_OBJECT',
                    'missing_paths': sorted(needed), 'extra_paths': []}
        return {'status': 'COMPLETE' if set(spans) == needed else 'FIELD_SET_MISMATCH',
                'missing_paths': sorted(needed - set(spans)),
                'extra_paths': sorted(set(spans) - needed)}
    except (TypeError, KeyError, ValueError, bridge.V3Error):
        return {'status': 'CANDIDATE_NOT_VALIDATED'}


def reconcile(request_text: str, raw_head: str) -> tuple[str, dict]:
    """Fill a *missing* field only from exactly one recognized original source line.

    No rescue if there are extra citation keys, field-level source contradictions,
    ambiguous line identity, bad original quotes, or a mismatched candidate value.
    The original bridge validates every completed literal quote afterward.
    """
    details = diagnose(raw_head)
    report = {**details, 'recovered_paths': [], 'status_after': details['status'],
              'method': 'UNIQUE_LITERAL_SOURCE_OBSERVATION_NO_IR_CHANGE'}
    if details['status'] != 'FIELD_SET_MISMATCH':
        return raw_head, report
    if details['extra_paths'] or not details['missing_paths']:
        report['status_after'] = 'UNRESOLVED_EXTRA_OR_UNKNOWN_CITATIONS'
        return raw_head, report
    # Never reconstruct an entire absent evidence ledger on the model's behalf.
    if len(details['missing_paths']) > 3:
        report['status_after'] = 'UNRESOLVED_TOO_MANY_MISSING_CITATIONS'
        return raw_head, report
    data = json.loads(raw_head)
    spec = bridge.govlang.validate(data['contract'])
    obs = fidelity.audit_source(request_text)
    if obs['issues']:
        report['status_after'] = 'UNRESOLVED_SOURCE_CONFLICT'
        return raw_head, report
    entries = defaultdict(list)
    lines = request_text.splitlines()
    for item in obs['observations']:
        path = item['path']
        number = item['line']
        if 1 <= number <= len(lines):
            quote = lines[number - 1].strip()
            entries[path].append((item['value'], quote))
    required = bridge._expected_evidence(spec)
    repair = {}
    for path in details['missing_paths']:
        matches = [quote for value, quote in entries[path]
                   if type(value) is type(required[path]) and value == required[path]
                   and quote and len(quote) <= 350]
        # Enforce UNIQUE source occurrence, not just unique quoted text.
        if len(matches) != 1:
            report['status_after'] = 'UNRESOLVED_NOT_UNIQUELY_GROUNDED'
            return raw_head, report
        repair[path] = matches[0]
    proposed = dict(data['source_spans'])
    proposed.update(repair)
    # Crucially: enforce the original complete evidence validator on *all* keys.
    # If any quote is wrong, *nothing* is repaired or authorized.
    try:
        bridge._check_evidence(request_text, spec, proposed)
    except bridge.BridgeError:
        report['status_after'] = 'UNRESOLVED_FULL_EVIDENCE_CHECK'
        return raw_head, report
    result = dict(data)
    result['source_spans'] = proposed
    report['recovered_paths'] = sorted(repair)
    report['status_after'] = 'RECOVERED_LITERAL_CITATIONS_NOT_SEMANTIC_PROOF'
    return json.dumps(result, ensure_ascii=False, separators=(',', ':')), report
