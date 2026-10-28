"""Patch 13 completion: data-only v2 presentation compatibility over canonical meaning.

Never changes canonical observations, source bindings, authority, or generation
proof. An overlay is counted as app-authored presentation work, not semantic
knowledge and not an unhinted layout. The renderer remains the existing v2 UI.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import re
from typing import Any, Mapping

from . import SemanticCatalogError

SCHEMA = 'mcel.knowledge-presentation-overlay.v1'
_PATTERN = re.compile(r'^[a-zA-Z][\w-]*$')
_ROOT = Path(__file__).resolve().parent / 'presentation'


def load_presentation_policy(filename: str = 'task-manager-processes.v1.json') -> dict:
    # Explicitly restricted to local, versioned data documents. Not a path loader.
    if not isinstance(filename, str) or not re.fullmatch(r'[a-z][a-z0-9-]*\.v[1-9][0-9]*\.json', filename):
        raise SemanticCatalogError('Invalid registered presentation filename')
    return json.loads((_ROOT / filename).read_text(encoding='utf-8'))


def _check(policy: Mapping[str, Any], projection: Mapping[str, Any]) -> None:
    if not isinstance(policy, Mapping) or set(policy) != {
        'schema', 'observation', 'fields', 'labels', 'primary', 'detail',
        'presentation', 'valueLabels',
    } or policy['schema'] != SCHEMA or policy['observation'] != projection.get('observation'):
        raise SemanticCatalogError('Presentation overlay schema or observation mismatch')
    spec = projection.get('spec', {})
    if spec.get('schema') != 'mcel.semantic-collection-spec.v2' or spec.get('identity') != 'mcelIdentity':
        raise SemanticCatalogError('Presentation requires canonical v2 identity')
    original_fields = {f['key']: f for f in spec.get('fields', [])}
    columns = policy['fields']
    if (not isinstance(columns, list) or not 1 <= len(columns) <= 15 or
        len(set(columns)) != len(columns) or
        any(not isinstance(k, str) or not _PATTERN.fullmatch(k) or k not in original_fields or k == 'mcelIdentity'
            for k in columns)):
        raise SemanticCatalogError('Unknown or duplicate presentation fields')
    for name in ('labels', 'presentation', 'valueLabels'):
        if not isinstance(policy[name], Mapping) or set(policy[name]) - set(columns):
            raise SemanticCatalogError('Invalid presentation overlay: ' + name)
    for name in ('primary', 'detail'):
        if not isinstance(policy[name], list) or len(set(policy[name])) != len(policy[name]) or set(policy[name]) - set(columns):
            raise SemanticCatalogError('Invalid presentation tiers')
    if set(policy['primary']) & set(policy['detail']):
        raise SemanticCatalogError('Contradictory presentation tiers')
    if not policy['primary']:
        raise SemanticCatalogError('Presentation must retain a meaningful primary field')
    for key, label in policy['labels'].items():
        if not isinstance(label, str) or not label.strip() or len(label) > 128:
            raise SemanticCatalogError('Invalid presentation label: ' + key)
    for key, opt in policy['presentation'].items():
        if not isinstance(opt, Mapping) or set(opt) - {'maxChars', 'compactDisclosure'} or original_fields[key]['kind'] not in {'text', 'long-text'}:
            raise SemanticCatalogError('Invalid disclosure policy: ' + key)
        if not (isinstance(opt.get('maxChars'), int) and 16 <= opt['maxChars'] <= 512 and
                isinstance(opt.get('compactDisclosure'), bool)):
            raise SemanticCatalogError('Invalid disclosure bounds: ' + key)
    for key, labels in policy['valueLabels'].items():
        if original_fields[key]['kind'] not in {'text', 'enum'} or not isinstance(labels, Mapping) or not labels:
            raise SemanticCatalogError('Value labels require text/enum and a nonempty mapping')
        if any(not isinstance(k, str) or not isinstance(v, str) or not v.strip() or len(v) > 128
               for k, v in labels.items()):
            raise SemanticCatalogError('Invalid value labels')


def apply_presentation_policy(projection: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    """Return a v2 projection with a reversible compatibility view.

    Identity and evidence are never remapped. Value labels are *display-only*
    source slots, distinct from original semantic values; a missing or unknown
    label leaves the original scalar untouched, not fabricated. This is a
    generic adapter driven entirely by the supplied, validated policy.
    """
    _check(policy, projection)
    result = deepcopy(dict(projection))
    spec = result['spec']
    available = {f['key']: f for f in spec['fields']}
    fields = [available['mcelIdentity']]
    row_slots: dict[str, str] = {}
    for i, key in enumerate(policy['fields']):
        field = deepcopy(available[key])
        field['label'] = policy['labels'].get(key, field['label'])
        field['tier'] = 'primary' if key in policy['primary'] else 'detail' if key in policy['detail'] else 'secondary'
        field['priority'] = i + 1
        if key in policy['presentation']:
            field['presentation'] = {**field.get('presentation', {}), **policy['presentation'][key]}
            if 'maxChars' in policy['presentation'][key]:
                field['kind'] = 'long-text'
                # Display can be abbreviated; the canonical record and exact
                # inspector retain the full original command line.
                field['exact'] = False
        if key in policy['valueLabels']:
            field['kind'] = 'enum'  # A finite display vocabulary; canonical type stays unchanged.
            slot = 'mcelDisplay_' + key
            if slot in spec['sourceFields'] or any(slot in r for r in result['rows']):
                raise SemanticCatalogError('Display slot collision: ' + slot)
            field['from'] = slot
            row_slots[key] = slot
        fields.append(field)
    if len(fields) > 16:
        raise SemanticCatalogError('v2 field limit exceeded')
    for row in result['rows']:
        for key, slot in row_slots.items():
            if row.get(key + 'Availability') == 'present' and isinstance(row.get(key), str):
                row[slot] = policy['valueLabels'][key].get(row[key], row[key])
    spec['sourceFields'] += list(row_slots.values())
    spec['fields'] = fields
    result['presentationCompatibility'] = {
        'status': 'explicit-application-overlay',
        'identityUnchanged': True,
        'canonicalRawUnchanged': True,
        'overlaySchema': SCHEMA,
        'observation': policy['observation'],
        'selectedFields': list(policy['fields']),
        'originalProjectionFields': list(available),
        'additionalSourceSlots': list(row_slots.values()),
        'noOperations': True,
    }
    return result
