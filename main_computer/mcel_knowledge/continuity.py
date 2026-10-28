"""Patch 13 stage 4: evidence-scoped, read-only process reconciliation.

A canonical entity identity is not, by itself, a license to retain browser
state. Only the *actual TaskManagerService process producer* may opt into this
adapter, scoped to the lifetime of its serving Python process. The distinct
source kinds correspond to the two production enumeration paths; a bridge
without explicit source provenance stays on Patch 13 stage 3's gated path.

This does not attest operating-system identity cryptographically, establish
continuity for other MCEL concepts, or promote the canonical UI to production.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Mapping

# Do not accept an arbitrary caller-supplied proof label. The server route
# passes this from the actual TaskManagerService code path.
SOURCES = frozenset(('psutil-create-time', 'win32-cim-creation-date'))
_INSTANCE = re.compile(r'^[0-9a-f]{32}$')
_OBSERVATION = 'task-manager.processes'


def with_process_generation_evidence(shadow: Mapping[str, Any], *,
                                     server_instance: str,
                                     source_kind: str) -> dict[str, Any]:
    """Return copy with per-row *conditional* reconciliation evidence.

    Do not infer a generation from PID alone, a name, raw timestamp spelling,
    or a client-provided revision. Failure leaves the existing consumer path
    unmodified. A valid proof only enables **read-only lab** reconciliation;
    actual UI continuity must be measured separately in a hosted browser.
    """
    result = deepcopy(dict(shadow))
    obs = result.get('observations', {}).get(_OBSERVATION, {})
    if not (_INSTANCE.fullmatch(server_instance or '') and source_kind in SOURCES and
            result.get('ok') is True and obs.get('status') == 'shadow-only' and
            obs.get('parity', {}).get('ok') is True):
        result['processGenerationEvidence'] = {'status': 'withheld',
            'reason': 'producer-instance/source/parity not established'}
        return result

    collection = obs.get('collection', {})
    spec = collection.get('spec', {})
    rows = collection.get('rows', [])
    if not (spec.get('schema') == 'mcel.semantic-collection-spec.v2' and
            isinstance(rows, list) and spec.get('identity') == 'mcelIdentity' and
            'reconciliation' not in spec):
        result['processGenerationEvidence'] = {'status': 'withheld',
            'reason': 'unexpected collection schema'}
        return result

    issuer = hashlib.sha256(('mcel-stage4:' + server_instance).encode()).hexdigest()[:24]
    for row in rows:
        record = row.get('mcelCanonicalRecord', {})
        properties = record.get('properties', {})
        pid = properties.get('pid', {}).get('raw')
        created = properties.get('created_at', {})
        started = created.get('normalized')
        # The normalized timestamp was computed only from explicit, declared
        # source formats by Patch 13 foundations. No raw spelling guesses.
        valid = (record.get('identity', {}).get('status') == 'complete' and
                 isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 and
                 created.get('state') == 'present' and
                 isinstance(started, str) and started.endswith('+00:00') and
                 created.get('sourceFormat') is not None)
        if not valid:
            # A syntactically accepted row that lacks proof must never inherit
            # DOM/selection/focus state. Its generation is explicitly absent.
            row.pop('mcelGeneration', None)
            continue
        material = json.dumps(['task-process-generation-v1', issuer, pid, started],
                              ensure_ascii=False, separators=(',', ':'))
        row['mcelGeneration'] = 'pgen1:' + hashlib.sha256(material.encode()).hexdigest()
    # Reconciliation is row-local: rows with missing evidence get replaced.
    if 'mcelGeneration' not in spec['sourceFields']:
        spec['sourceFields'].append('mcelGeneration')
    spec['reconciliation'] = {'generationFrom': 'mcelGeneration'}
    proof_count = sum(bool(row.get('mcelGeneration')) for row in rows)
    evidence = {'status': 'eligible-read-only' if proof_count else 'no-qualified-rows',
                'sourceKind': source_kind,
                'producerInstanceScope': issuer,
                'qualifiedRows': proof_count,
                'unqualifiedRows': len(rows) - proof_count,
                'authority': 'lab-only; not promoted; no runtime effects',
                'hostedCertified': False}
    obs['continuity'] = evidence
    result['processGenerationEvidence'] = evidence
    # Never set continuityCertified here. A hosted DOM test must establish it.
    return result
