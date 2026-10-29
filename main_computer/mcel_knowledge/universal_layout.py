"""Patch 14.3 experimental, *single* universal semantic-to-application composer.

Consumes already validated read-only knowledge observations. No application identifiers,
source-field names, guessed authority, I/O, DOM or execution. It composes existing
MCEL presentation recipes, v2 collection and canonical-tree inputs; it is NOT a
second Application IR or production layout policy.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping

from . import SemanticCatalogError, load_catalog
from .ir_bridge import VIEW_SCHEMA, collection_projection
from tools.mcel_presentation_planner import RECIPES

SCHEMA = 'mcel.universal-semantic-composition.v1'
PROFILE = 'mcel.layout.universal.v1'
POLICY = 'mcel.universal-composer-policy.v1'
PURPOSES = frozenset(('monitor', 'compare', 'inspect', 'navigate', 'manage'))
# Stable organizational capabilities; do not confuse these with whole-app profiles.
ROLE_RECIPES = {
    'observed-status': 'summary-strip',
    'comparable-entities': 'comparable-collection',
    'selected-details': 'context-inspector',
    'observed-activity': 'transient-activity',
    'available-intents': 'contextual-toolbar',
    'additional-tools': 'advanced-disclosure',
}
ORDER = ('observed-status', 'comparable-entities', 'selected-details',
         'observed-activity', 'available-intents', 'additional-tools')


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _hash(value: Any) -> str:
    return 'sha256:' + hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def _property_class(prop: Mapping[str, Any], concept: Mapping[str, Any]) -> str:
    # Only registered type properties, never display names or producer field names.
    kind = concept['kind']
    if kind == 'measurement':
        return 'measurement'
    if kind == 'state':
        return 'state'
    if prop['type'] == 'mc.timestamp':
        return 'temporal'
    if kind == 'structured-value':
        return 'structured'
    if prop['type'] == 'mc.boolean':
        return 'flag'
    return 'attribute'


def extract_capabilities(view: Mapping[str, Any], observation: str, *, catalog: Mapping[str, Any] | None = None) -> dict:
    cat = catalog if catalog is not None else load_catalog()
    if not isinstance(view, Mapping) or view.get('schema') != VIEW_SCHEMA or observation not in view.get('sources', {}):
        raise SemanticCatalogError('Composer requires validated canonical knowledge view')
    if observation not in cat['observations']:
        raise SemanticCatalogError('Unregistered semantic observation')
    src = view['sources'][observation]
    definition = cat['observations'][observation]
    if src['state'] != 'present':
        raise SemanticCatalogError('No present semantic observation: ' + str(src['state']))
    props = [*definition['properties'], *definition.get('derivedProperties', [])]
    capabilities = { 'comparison':definition['cardinality']=='many' and bool(definition.get('identity')),
      'inspection':bool(props), 'measurement':False, 'state':False, 'temporal':False,
      'hierarchy':False, 'activity':False, 'authorizedActions':False}
    by_class = {k:[] for k in ('measurement','state','temporal','structured','flag','attribute')}
    for prop in props:
        cls = _property_class(prop, cat['concepts'][prop['type']]);by_class[cls].append(prop['name'])
        if cls in capabilities:
            capabilities[cls] = True
    # Only authoritative parent/child relationships establish hierarchy.
    capabilities['hierarchy'] = any(rel.get('certainty') == 'verified' and rel.get('kind') == 'parent-child'
                                    for rel in definition.get('relationships', []))
    # No operation in the current semantic catalog grants execution authority.
    # A known operation is not an available intent, and raw status is not a live activity feed.
    identity_names = {p['name'] for p in props if p.get('source') in definition.get('identity', {}).get('fields', [])}
    # A readable scalar label is a generic presentation affordance, not a
    # guess based on producer field names. Never mistake derived text for a
    # canonical name; its evidence/meaning must remain inspectable.
    readable = sorted(p['name'] for p in definition['properties'] if p['type']=='mc.text'
                      and p['name'] not in identity_names)
    label_name = readable[0] if readable else None
    ordered_types = sorted((p['name'], p['type']) for p in props)
    return {'concept':definition['concept'], 'cardinality':definition['cardinality'],
      'entityCount':len(src['records']), 'observedState':src['state'],
      'propertiesByClass':by_class, 'identityProperties':sorted(identity_names),
      'readableLabelProperty':label_name,
      'capabilities':capabilities, 'semanticProperties':ordered_types,
      'observationCompleteness':(src.get('envelope') or {}).get('completeness', 'unknown'),
      'relationshipCertainty':[r.get('certainty','unknown') for r in definition.get('relationships', [])],
      'note':'Counts are observed records, never a claim about all entities in the system'}


def _roles(cap: Mapping[str, Any], purpose: str) -> list[str]:
    c = cap['capabilities']; many = cap['cardinality']=='many'
    roles = []
    if purpose in ('monitor','manage') and (c['measurement'] or c['state']):
        roles.append('observed-status')
    if many and c['comparison']:
        roles.append('comparable-entities')
    if c['inspection']:
        roles.append('selected-details')
    # A verifiable hierarchy needs its own compositional representation capability;
    # no verified hierarchy exists in the current catalog. Never fake a tree from path text.
    return [r for r in ORDER if r in roles]


def _ranked_fields(cap: Mapping[str, Any], purpose: str) -> list[str]:
    classes = cap['propertiesByClass']
    identity = cap['identityProperties']
    ordering = {'monitor':('state','measurement','attribute','flag','temporal','structured'),
      'compare':('measurement','state','attribute','flag','temporal','structured'),
      'inspect':('attribute','state','measurement','temporal','flag','structured'),
      'navigate':('attribute','state','temporal','measurement','flag','structured'),
      'manage':('state','flag','attribute','measurement','temporal','structured')}[purpose]
    return list(dict.fromkeys([*([cap['readableLabelProperty']] if cap.get('readableLabelProperty') else []),
       *sorted(identity), *(name for cls in ordering for name in sorted(classes[cls]))]))



def _summary_facts(view: Mapping[str, Any], observation: str,
                   capabilities: Mapping[str, Any]) -> list[dict]:
    """Small factual summary of the *observed subset*; never a population claim.

    Constructed from canonical value states, not from guessed field names or
    flattened formatted strings. Bounded to avoid a data-dependent layout tree.
    """
    records=view['sources'][observation]['records']
    result=[]
    for name in sorted(capabilities['propertiesByClass']['state']):
        counts={}
        for r in records:
            item=r['properties'][name]
            if item['state']=='present' and isinstance(item['raw'],str):
                counts[item['raw']]=counts.get(item['raw'],0)+1
        if counts:
            result.append({'property':name,'kind':'observed-state-counts',
              'counts':dict(sorted(counts.items())), 'denominator':len(records),
              'scope':'observed-subset-only'})
    for name in sorted(capabilities['propertiesByClass']['measurement']):
        states={}
        for r in records:
            state=r['properties'][name]['state'];states[state]=states.get(state,0)+1
        result.append({'property':name,'kind':'measurement-availability',
          'states':dict(sorted(states.items())), 'denominator':len(records),
          'scope':'observed-subset-only'})
    return result[:3]


def compose(view: Mapping[str, Any], observation: str, *, purpose: str,
            catalog: Mapping[str, Any] | None = None, prior: Mapping[str, Any] | None = None) -> dict:
    """Deterministically select *regions* and fields using registered semantics.

    A previous decision is an input rather than runtime search.  Once purpose and
    structural semantic signature match, region organization is retained.  No
    mutable source data is used in the naming or decision fingerprint.
    """
    if purpose not in PURPOSES:
        raise SemanticCatalogError('Unsupported semantic purpose')
    cat = catalog if catalog is not None else load_catalog()
    caps = extract_capabilities(view, observation, catalog=cat)
    # v2 exact source/availability/identity contract is shared with Patch 12.
    proj = collection_projection(view, observation, catalog=cat)
    if not proj.get('noOperations'):
        raise SemanticCatalogError('Universal composer is read-only')
    original = {f['key']:f for f in proj['spec']['fields']}
    ranked = _ranked_fields(caps, purpose)
    identity = caps['identityProperties']
    # Preserve required mcelIdentity and keep all original semantic properties
    # through mcelCanonicalRecord even if the v2 16-field budget defers some.
    # The exact composite identity is still required by the value contract, but
    # it is not a human-readable title. Present the meaningful semantic fields
    # first and retain the machine key as a recoverable, low-salience field.
    fields=[]
    for name in ranked:
        if name not in original:continue  # deferred structure/timestamp remains inspectable
        field=deepcopy(original[name])
        field['priority']=len(fields)
        field['tier']='primary' if (name==caps.get('readableLabelProperty') or
          name in identity and name not in caps['propertiesByClass']['temporal']) else 'secondary'
        if name in caps['propertiesByClass']['structured'] or name in caps['propertiesByClass']['temporal']:
            field['tier']='detail'
        fields.append(field)
    fields.append(deepcopy(original['mcelIdentity']))
    proj['spec']['fields']=fields
    roles=_roles(caps, purpose)
    if not roles:
        raise SemanticCatalogError('No valid semantic composition for observation')
    regions=[{'id':'semantic-'+role,'role':role,'recipe':ROLE_RECIPES[role],
              'title':{'observed-status':'Observed status','comparable-entities':'Observed entities',
                       'selected-details':'Details'}[role],
              'visible':role!='selected-details' or caps['capabilities']['inspection'],
              'semanticTarget':caps['concept']} for role in roles]
    # Existing canonical tree interpreter consumes these stable authored node IDs.
    tree={'schema':'mcel.layout-tree.v1','root':{'id':'workspace','selector':'#mcel-universal-workspace',
          'role':'group','maxColumns':2,'gap':8,'children':[
            {'id':r['id'],'selector':'#'+r['id'],'role':'region','minTargetWidth':240 if r['role']=='comparable-entities' else 80} for r in regions]}}
    signature={'profile':PROFILE,'concept':caps['concept'],'purpose':purpose,
       'cardinality':caps['cardinality'],'semanticProperties':caps['semanticProperties'],
       'regions':[r['id'] for r in regions], 'propertyOrder':[f['key'] for f in fields]}
    name='mcel.layout.universal/'+caps['concept']+'/'+purpose
    decision={'name':name,'profile':PROFILE,'policy':POLICY,'purpose':purpose,
       'regions':[r['id'] for r in regions], 'fields':[f['key'] for f in fields],
       'semanticSignature':_hash(signature)}
    retained = prior is not None and prior.get('decision')==decision
    # A previous decision with a different semantic signature is not silently
    # carried into a new application, while content churn changes no signature.
    result={'schema':SCHEMA,'profile':PROFILE,'observation':observation,'concept':caps['concept'],
       'purpose':purpose,'capabilities':caps,'regions':regions, 'tree':tree,
       'decision':decision, 'commit':{'decision':decision,'status':'retained' if retained else 'new',
              'reason':'semantic-composition-invariant' if retained else 'semantic-structure-or-purpose'},
       'collection':proj, 'summaryFacts':_summary_facts(view,observation,caps) if 'observed-status' in roles else [], 'recipesUsed':{r['recipe']:deepcopy(RECIPES[r['recipe']]) for r in regions},
       'sourceIndependent':True,'readOnly':True,'productionPromotion':False,
       'layoutSearch':'not-performed-at-runtime',
       'deferredFields':deepcopy(proj.get('deferredFields',[]))}
    return result
