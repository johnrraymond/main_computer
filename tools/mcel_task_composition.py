"""Domain-independent task-aware composition for generated MCEL applications.

Combines *existing* registered semantic regions, never deletes authored facts or
creates authority over live actions. The browser renderer consumes this plan.
"""
from __future__ import annotations

import re

SCHEMA = 'mcel.task-composition.v1'
TASKS = ('browse', 'search', 'inspect', 'monitor', 'recover')
ACRONYMS = {'id':'ID','pid':'PID','cpu':'CPU','gpu':'GPU','rss':'RSS','ram':'RAM',
            'io':'I/O','ai':'AI','ip':'IP','tcp':'TCP','udp':'UDP','uri':'URI',
            'url':'URL','http':'HTTP','https':'HTTPS','api':'API','uuid':'UUID',
            'rpc':'RPC','dns':'DNS','tls':'TLS'}
WORDS = {'cmdline':'Command line','timestamp':'Timestamp'}
COMPOSITES = {
  'searchable-collection':{
    'roles':['global-context-and-search','primary-content','selection-details'],
    'narrow':'search-then-list-with-on-demand-details',
    'wide':'search-then-collection-with-side-inspector',
    'require':['search','collection','selection-disclosure'],
  },
  'attention-and-feedback':{
    'roles':['status-and-attention','persistent-nonblocking-feedback'],
    'narrow':'compact-status-with-conditional-warning',
    'wide':'compact-status-with-conditional-feedback',
    'require':['observation-truth','operation-receipt-truth'],
  },
  'optional-capabilities':{
    'roles':['supporting-tools'],
    'narrow':'collapsed-disclosure','wide':'collapsed-disclosure',
    'require':['authorization-boundary'],
  },
}


def natural_label(identifier, authored=None):
    """Conservative naming. No guessing at data values, authorization or outcomes."""
    if isinstance(authored,str) and authored.strip():
        return authored.strip()
    if not isinstance(identifier,str) or not identifier.strip():
        raise ValueError('Presentation identifier must be nonempty')
    pieces=re.split(r'[-_\s]+',identifier.strip())
    if pieces and pieces[-1].lower() in ('row','record') and len(pieces)>1:
        pieces=pieces[:-1]
    return ' '.join(WORDS.get(x.lower(), ACRONYMS.get(x.lower(),x.capitalize())) for x in pieces)


def plan_task_composition(design,presentation):
    if not presentation or not presentation.get('semantic_preservation'):
        raise ValueError('Cannot compose a presentation that lost semantics')
    roles=presentation['regions']
    expected={role for composite in COMPOSITES.values() for role in composite['roles']}
    if set(roles)!=expected:raise ValueError('Incomplete semantic role coverage')
    region_ids=[part['region'] for part in roles.values()]
    if len(region_ids)!=len(set(region_ids)):
        raise ValueError('Duplicate semantic region ID')
    labels={'rows':{},'fields':{},'views':{},'entities':{}}
    for name,spec in design['rows']['types'].items():
        labels['rows'][name]=natural_label(name,spec.get('display_label'))
        for field in presentation['rows'][name]['all_fields']:
            labels['fields'][field]=natural_label(field)
    for entity,spec in design['semantics']['entities'].items():
        labels['entities'][entity]=natural_label(entity,spec.get('display_label'))
    views=[]
    for part in design['hierarchy']['children']:
        if part['role']=='primary-content':
            for view in part.get('views',[]):
                labels['views'][view['id']]=natural_label(view['id'],view.get('title'))
                views.append(view['id'])
    if not views:raise ValueError('Task-aware composition needs at least one declared view')
    role_map={role:value['region'] for role,value in roles.items()}
    return {'schema':SCHEMA,'composites':COMPOSITES,'role_ids':role_map,'labels':labels,
            'tasks':TASKS,'views':views,
            'active_task_rule':{
               'failure':'recover','unverified_operation':'monitor',
               'search_query':'search','selection':'inspect','default':'browse'},
            'preservation':{
                'regions_preserved':True,'all_fields_inspectable':presentation['semantic_preservation'],
                'actions_authorized_by_composition':False},
            'explanation':('Search, collection, and inspector form one workspace; '
                           'status and activity are proportional to current evidence; '
                           'optional actions remain gated by original authorization.'),
            'authoring':{'app_specific_css_rules':0,'added_author_hints':0}}
