"""Experimental MCEL field-presentation contract projection.

The only persistent input is the existing flat JSON fixture. The adapter owns
presentation strings, while MCEL registered component identities and full typed
values remain authoritative. No network, registry writes or production changes.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

try:
    from tools.mcel_text_shrinker_adapter import adapt_widget
except ModuleNotFoundError:
    from mcel_text_shrinker_adapter import adapt_widget

SCHEMA = "mcel.semantic-information-contract.experiment.v1"


def project_collection(widget: dict[str, Any], value_sets: dict[str, list], *,
                       viewport_px: int, enabled: bool) -> dict[str, Any]:
    """Build a real registered-MCEL-element collection tree plus view projection.

    `enabled=False` changes presentation opt-ins only, never the actual rows.
    Rows, semantic field keys, values and interaction authority are immutable.
    """
    original = deepcopy(widget)
    setting = deepcopy(widget)
    for column in setting['columns']:
        field = column.setdefault('presentation', {}).setdefault('text_shrinker', {})
        if not enabled:
            field['enabled'] = False
    projection = adapt_widget(setting, value_sets, viewport_px=viewport_px)
    assert original == widget, 'semantic source mutated'
    schema_rows = []
    source_by_id = {str(row['id']): row for row in widget['rows']}
    for row in projection['rows']:
        rid = row['id']
        if rid not in source_by_id:
            raise ValueError(f'unknown source identity {rid}')
        fields = []
        for column in widget['columns']:
            key = column['key']
            cell = row['cells'][key]
            if cell['raw'] != source_by_id[rid].get(key, ''):
                raise ValueError('presentation changed authoritative value')
            fields.append({
                'id': f'field-{rid}-{key}', 'elementId': 'element.core.field',
                'label': column.get('label', key),
                'props': {'semanticField': key, 'presentation': column.get('presentation', {})},
                'children': [],
            })
        schema_rows.append({
            'id': 'row-' + rid, 'elementId': 'element.core.collection-row',
            'label': rid, 'props': {'rowId': rid}, 'children': fields,
        })
    tree = {'id': 'collection', 'elementId': 'element.core.collection',
            'label': 'Semantic collection', 'props': {}, 'children': schema_rows}
    return {'schema': SCHEMA, 'enabled': enabled, 'tree': tree,
            'source_rows': deepcopy(widget['rows']),
            'behavior': deepcopy(widget.get('behavior', {'task': 'browse'})),
            'projection': projection,
            'field_contracts': deepcopy(widget['columns']),
            'viewport_px': viewport_px,
            'contract_authority': 'semantic-field opt-in, source values immutable',
            'production_integrated': False}
