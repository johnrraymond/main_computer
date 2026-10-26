"""Reusable MCEL semantic-compression and presentation-pattern planning.

Does not create CSS, inspect application names, or authorize effects. Preserves
all semantic source fields and provides an access path for deferred fields.
The renderer consumes this plan, not an application-specific set of templates.
"""
from __future__ import annotations

from collections import Counter
import json

# Generic semantic traits, not Task Manager / row-type IDs.
STATE_TERMS = ('status', 'state', 'severity', 'availability', 'freshness', 'health')
MEASURE_TERMS = ('cpu', 'memory', 'percent', 'usage', 'utilization', 'latency', 'duration', 'progress', 'frequency')

RECIPES = {
    'comparable-collection': {
        'category': 'collection', 'wide': 'comparison-grid', 'narrow': 'compact-list',
        'uses': ['element.core.panel', 'element.core.field', 'element.core.action'],
        'requires': ['identity', 'inspector'],
    },
    'summary-strip': {
        'category': 'status', 'wide': 'status-summary', 'narrow': 'status-summary',
        'uses': ['element.core.region', 'element.core.panel', 'element.core.field'],
        'requires': ['observation-truth'],
    },
    'context-inspector': {
        'category': 'detail', 'wide': 'details-panel', 'narrow': 'detail-disclosure',
        'uses': ['element.core.panel', 'element.core.field'],
        'requires': ['selection'],
    },
    'transient-activity': {
        'category': 'activity', 'wide': 'contextual-feedback', 'narrow': 'contextual-feedback',
        'uses': ['element.core.panel', 'element.core.field'],
        'requires': ['operation-state'],
    },
    'contextual-toolbar': {
        'category': 'action', 'wide': 'search-toolbar', 'narrow': 'search-toolbar',
        'uses': ['element.core.toolbar', 'element.core.action', 'element.core.field'],
        'requires': ['available-intents'],
    },
    'advanced-disclosure': {
        'category': 'action', 'wide': 'optional-disclosure', 'narrow': 'optional-disclosure',
        'uses': ['element.core.panel', 'element.core.action'],
        'requires': ['authorization'],
    },
}

ROLE_TITLES = {
    'primary-content':'Items',
    'status-and-attention':'Status & attention',
    'selection-details':'Details',
    'persistent-nonblocking-feedback':'Activity',
    'global-context-and-search':'Search',
    'supporting-tools':'More tools',
}

ROLE_RECIPES = {
    'primary-content':'comparable-collection',
    'status-and-attention':'summary-strip',
    'selection-details':'context-inspector',
    'persistent-nonblocking-feedback':'transient-activity',
    'global-context-and-search':'contextual-toolbar',
    'supporting-tools':'advanced-disclosure',
}


def classify_field(field: str) -> str:
    token = field.casefold()
    if any(term in token for term in STATE_TERMS): return 'status'
    if any(term in token for term in MEASURE_TERMS): return 'measurement'
    if any(term in token for term in ('path', 'cmdline', 'command', 'evidence', 'payload', 'reason', 'result', 'error')):
        return 'detail'
    return 'attribute'


def compress_row(row_id, spec):
    """Primary identity always shown. Summary is prioritized; remainder inspectable."""
    primary = list(dict.fromkeys(spec.get('primary') or []))
    summary = [field for field in dict.fromkeys(spec.get('summary', [])) if field not in primary]
    if not primary: raise ValueError(f'{row_id}: missing identity field')
    priorities = {'status': 0, 'measurement': 1, 'attribute': 2, 'detail': 3}
    ranked = sorted(enumerate(summary), key=lambda pair: (priorities[classify_field(pair[1])], pair[0]))
    # Up to two compact facts. The entire summary is still accessible in inspector.
    display = [field for index, field in ranked[:2]]
    detail = [field for field in summary if field not in display]
    return {
        'entity':spec['entity'], 'identity':primary, 'summary':display, 'deferred':detail,
        'all_fields':primary+summary, 'actions':list(spec.get('actions', [])),
        'pattern': 'comparable-collection',
        'reason': 'Identity remains visible; comparable facts summarized; other properties accessible by selection',
    }


def plan_presentation(design):
    """One explainable plan for arbitrary semantic application with these generic roles."""
    if not isinstance(design.get('rows', {}).get('types'), dict):
        raise ValueError('Semantic rows declaration missing')
    rows = {id:compress_row(id,spec) for id,spec in design['rows']['types'].items()}
    roles={}
    for region in design['hierarchy']['children']:
        role=region['role']
        if role not in ROLE_RECIPES:raise ValueError(f'Unsupported presentation role {role!r}')
        if role in roles:raise ValueError(f'Duplicate semantic presentation role {role!r}')
        recipe=ROLE_RECIPES[role]
        roles[role]={'region':region['id'],'recipe':recipe,'title':ROLE_TITLES[role],
                     'reason':f'{role}: matched generic semantic presentation recipe {recipe}',
                     'default_visibility':'contextual' if role in ('selection-details','persistent-nonblocking-feedback','supporting-tools') else 'visible'}
    if set(roles)!=set(ROLE_RECIPES):raise ValueError('Required semantic roles missing')
    for rowid,row in rows.items():
        if not set(row['deferred']).issubset(row['all_fields']):
            raise ValueError(f'{rowid}: lost source fields')
        if len(row['all_fields'])!=len(set(row['all_fields'])):
            raise ValueError(f'{rowid}: duplicate field after compression')
    return {'schema':'mcel.presentation-plan.v1','rows':rows,'regions':roles,
            'recipes':RECIPES,
            'accessibility_contract':'deferred fields available through selecting the semantic record',
            'effect_contract':'no presentation pattern enables consequential operations',
            'semantic_preservation':all(
                set(spec['primary']+spec.get('summary',[]))==set(rows[id]['all_fields'])
                for id,spec in design['rows']['types'].items()),
            'stats':{
                'row_types':len(rows),
                'visible_row_fields':sum(len(r['identity'])+len(r['summary']) for r in rows.values()),
                'deferred_row_fields':sum(len(r['deferred']) for r in rows.values()),
                'declared_row_fields':sum(len(r['all_fields']) for r in rows.values()),
            }}


def validate_pattern_registry(plan, registered_ids):
    for recipe in plan['recipes'].values():
        missing=set(recipe['uses'])-set(registered_ids)
        if missing:raise ValueError('Required MCEL components not registered: '+', '.join(sorted(missing)))
    return True
