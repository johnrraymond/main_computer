"""Closed, non-executable interface-intent contract for the official MCEL DSL.

The author declares role, membership, and soft placement intent, NEVER geometry,
selectors, runtime authority, or a computed interface layout. A separate
semantic-graph extraction module deliberately does not read this information.
"""
from __future__ import annotations

from collections.abc import Mapping

ROLES = frozenset({
    'main-workarea', 'navigation', 'collection', 'document', 'inspector',
    'commands', 'results', 'status', 'auxiliary-workarea', 'mode-selector',
})
PRIORITIES = frozenset({'primary', 'secondary', 'supporting'})
SIDES = frozenset({'start', 'end', 'top', 'bottom'})
RELATIONS = frozenset({'selects', 'contextual-to', 'supports', 'reports-for', 'switches-mode-of',
                       'acts-on', 'informs', 'accompanies'})
PURPOSES = frozenset({'monitor', 'compare', 'inspect', 'navigate', 'manage'})

def surface_purpose_errors(surface: Mapping) -> list[str]:
    if 'purpose' not in surface:
        return []
    return [] if isinstance(surface['purpose'], str) and surface['purpose'] in PURPOSES else ['surface has unknown semantic purpose']



def interface_intent_errors(surface: Mapping) -> list[str]:
    """Validate declared intent against IDs on this surface; no I/O or inference."""
    if 'interfaceIntent' not in surface:
        return []
    intent = surface['interfaceIntent']
    if not isinstance(intent, Mapping) or set(intent) != {'schema', 'regions', 'relations'}:
        return ['interfaceIntent requires exactly schema, regions and relations']
    if intent.get('schema') != 'mcel.interface-intent.v1':
        return ['interfaceIntent schema must be mcel.interface-intent.v1']
    regions = intent.get('regions')
    relations = intent.get('relations')
    if not isinstance(regions, list) or not regions:
        return ['interfaceIntent must declare nonempty regions']
    if not isinstance(relations, list):
        return ['interfaceIntent relations must be an array']
    errors: list[str] = []
    surface_node_ids = {n.get('id') for n in (surface.get('nodes') or ()) if isinstance(n, Mapping)}
    region_ids: set[str] = set()
    covered: set[str] = set()
    for idx, region in enumerate(regions):
        where = f'regions[{idx}]'
        if not isinstance(region, Mapping):
            errors.append(f'{where} must be a record')
            continue
        if set(region) - {'id', 'kind', 'role', 'priority', 'prefer', 'members', 'source'} or not {'id', 'kind', 'role', 'members'} <= set(region):
            errors.append(f'{where} has unsupported or missing fields')
            continue
        if region.get('kind') != 'interface-region':
            errors.append(f'{where} has incorrect node kind')
        rid = region.get('id')
        if not isinstance(rid, str) or not rid.startswith('interface-region:') or rid in region_ids:
            errors.append(f'{where} has invalid or duplicate region ID')
        else:
            region_ids.add(rid)
        if region.get('role') not in ROLES:
            errors.append(f'{where} has unknown role')
        if 'priority' in region and region['priority'] not in PRIORITIES:
            errors.append(f'{where} has invalid priority')
        if 'prefer' in region:
            preference = region['prefer']
            if (not isinstance(preference, Mapping) or set(preference) != {'side'}
                    or preference.get('side') not in SIDES):
                errors.append(f'{where} has invalid soft placement preference')
        members = region.get('members')
        if not isinstance(members, list) or not members:
            errors.append(f'{where} must have members')
            continue
        for member in members:
            if not isinstance(member, Mapping) or set(member) != {'ref'} or not isinstance(member.get('ref'), str):
                errors.append(f'{where} has malformed member reference')
            elif member['ref'] not in surface_node_ids:
                errors.append(f'{where} references an undeclared surface node: {member["ref"]}')
            elif member['ref'] in covered:
                errors.append(f'{where} reuses a node owned by another region: {member["ref"]}')
            else:
                covered.add(member['ref'])
    if covered != surface_node_ids:
        errors.append('interfaceIntent must account for every declared surface node')
    seen = set()
    for idx, relation in enumerate(relations):
        where = f'relations[{idx}]'
        if not isinstance(relation, Mapping) or set(relation) != {'from', 'kind', 'to'}:
            errors.append(f'{where} must declare only from, kind and to')
            continue
        from_ref, to_ref = relation.get('from'), relation.get('to')
        f = from_ref.get('ref') if isinstance(from_ref, Mapping) and set(from_ref) == {'ref'} else None
        t = to_ref.get('ref') if isinstance(to_ref, Mapping) and set(to_ref) == {'ref'} else None
        if f not in region_ids or t not in region_ids or f == t:
            errors.append(f'{where} has unresolved or self-referential endpoints')
        if relation.get('kind') not in RELATIONS:
            errors.append(f'{where} has unknown semantic relationship')
        key = (f, relation.get('kind'), t)
        if key in seen:
            errors.append(f'{where} is a duplicate relationship')
        seen.add(key)
    return errors


def html_interface_intent_errors(surface: Mapping) -> list[str]:
    """Validate paired HTML hierarchy and ownership without constructing layout."""
    intent = surface.get('interfaceIntent')
    if not isinstance(intent, Mapping) or 'hierarchy' not in intent:
        return []
    from main_computer.mcel_semantic_html import PURPOSES, TASKS, CONTENT, RELATIONS as HTML_RELATIONS, TAGS, NATIVE_PURPOSE
    errors = []
    if set(intent) != {'schema', 'regions', 'relations', 'hierarchy'} or intent.get('schema') != 'mcel.interface-intent.v1':
        return ['Invalid HTML-backed interface-intent contract fields/schema']
    regions = intent.get('regions')
    relations = intent.get('relations')
    hierarchy = intent.get('hierarchy')
    if not isinstance(regions, list) or not regions or not isinstance(relations,list) or not isinstance(hierarchy,Mapping) or set(hierarchy) != {'rootId','source'}:
        return ['HTML-backed hierarchy needs regions, relations and one root/source']
    node_ids = {n.get('id') for n in surface.get('nodes', []) if isinstance(n, Mapping)}
    region_ids = [r.get('id') for r in regions if isinstance(r, Mapping)]
    if len(region_ids) != len(regions) or len(region_ids) != len(set(region_ids)):
        errors.append('HTML region IDs must be unique')
    ids = set(region_ids)
    parent_by_id = {}
    member_ids = []
    children_by_parent = {}
    for i, region in enumerate(regions):
        if not isinstance(region, Mapping):
            continue
        valid = {'id','kind','nativeTag','parentId','ordinal','members','htmlSource','purpose','tasks','content','priority','preferences','staticContent','visibleWhen'}
        required = {'id','kind','nativeTag','parentId','ordinal','members','htmlSource'}
        if set(region)-valid or not required <= set(region):
            errors.append(f'regions[{i}] has unknown or missing properties')
            continue
        rid, parent = region['id'], region['parentId']
        if region['kind'] != 'interface-region' or not isinstance(rid,str) or not rid.startswith('interface-region:'):
            errors.append(f'regions[{i}] has invalid kind/id')
        if parent is not None and (parent not in ids or parent == rid):
            errors.append(f'regions[{i}] has unresolved/self parent')
        parent_by_id[rid] = parent
        if not isinstance(region['ordinal'],int) or isinstance(region['ordinal'],bool) or region['ordinal'] < 0:
            errors.append(f'regions[{i}] has invalid child ordinal')
        else:
            children_by_parent.setdefault(parent,[]).append(region['ordinal'])
        if region['nativeTag'] not in TAGS:
            errors.append(f'regions[{i}] has unsupported HTML tag')
        if region.get('purpose') not in PURPOSES and 'purpose' in region:
            errors.append(f'regions[{i}] has unknown purpose')
        if region.get('purpose') and region['nativeTag'] in NATIVE_PURPOSE and region['purpose'] != NATIVE_PURPOSE[region['nativeTag']]:
            errors.append(f'regions[{i}] conflicts with native HTML meaning')
        if 'tasks' in region and (not isinstance(region['tasks'],list) or len(set(region['tasks']))!=len(region['tasks']) or any(t not in TASKS for t in region['tasks'])):
            errors.append(f'regions[{i}] has invalid tasks')
        if 'staticContent' in region:
            static=region['staticContent']
            if not isinstance(static,list) or any(not isinstance(x,Mapping) or set(x)!={'tag','ordinal','text','source'}
                or x.get('tag') not in {'h1','h2','h3','h4','h5','h6','p','span','strong','em','small','label'}
                or not isinstance(x.get('text'),str) for x in static):
                errors.append(f'regions[{i}] has invalid static content')
            else:
                children_by_parent.setdefault(rid,[]).extend(x['ordinal'] for x in static)
        if 'visibleWhen' in region:
            when=region['visibleWhen']
            if (not isinstance(when,Mapping) or set(when) != {'state','equals'} or
                not isinstance(when.get('state'),Mapping) or set(when['state']) != {'ref'} or
                not isinstance(when['state'].get('ref'),str) or not isinstance(when.get('equals'),str)):
                errors.append(f'regions[{i}] has invalid conditional visibility')
        if 'content' in region:
            content=region['content']
            if not isinstance(content, Mapping) or set(content) != {'kind','ref'} or content.get('kind') not in CONTENT or not isinstance(content.get('ref'),str):
                errors.append(f'regions[{i}] has malformed content')
        if 'priority' in region and region['priority'] not in PRIORITIES:
            errors.append(f'regions[{i}] has invalid priority')
        if 'preferences' in region:
            prefs=region['preferences']
            allowed={'side':SIDES,'density':{'automatic','compact','comfortable'},'representation':{'automatic','tabular','graphical','textual'}}
            if not isinstance(prefs,Mapping) or set(prefs)-set(allowed) or any(v not in allowed[k] for k,v in prefs.items()):
                errors.append(f'regions[{i}] has invalid preferences')
        members=region['members']
        if not isinstance(members,list):
            errors.append(f'regions[{i}] members must be an array')
        else:
            for m in members:
                if not isinstance(m,Mapping) or set(m) != {'ref'} or not isinstance(m.get('ref'),str):
                    errors.append(f'regions[{i}] malformed member')
                else:
                    member_ids.append(m['ref'])
    roots=[rid for rid,parent in parent_by_id.items() if parent is None]
    if len(roots)!=1 or hierarchy.get('rootId') not in roots:
        errors.append('HTML hierarchy must have exactly one declared root')
    for parent,ordinals in children_by_parent.items():
        if len(ordinals)!=len(set(ordinals)) or sorted(ordinals)!=list(range(len(ordinals))):
            errors.append(f'HTML sibling ordinal sequence invalid under {parent}')
    for rid in ids:
        seen=set()
        at=rid
        while at is not None and at in parent_by_id:
            if at in seen:
                errors.append(f'HTML hierarchy cycle at {rid}')
                break
            seen.add(at)
            at=parent_by_id[at]
    if len(member_ids)!=len(set(member_ids)) or set(member_ids)!=node_ids:
        errors.append('HTML region binding must cover each surface node exactly once')
    found_relations=set()
    for i,relation in enumerate(relations):
        if not isinstance(relation,Mapping) or set(relation) != {'from','kind','to'}:
            errors.append(f'relations[{i}] invalid shape')
            continue
        f,t=relation['from'],relation['to']
        fid=f.get('ref') if isinstance(f,Mapping) and set(f)=={'ref'} else None
        tid=t.get('ref') if isinstance(t,Mapping) and set(t)=={'ref'} else None
        key=(fid,relation['kind'],tid)
        if fid not in ids or tid not in ids or fid==tid or relation['kind'] not in HTML_RELATIONS or key in found_relations:
            errors.append(f'relations[{i}] unresolved, duplicate, or invalid')
        found_relations.add(key)
    return errors


def html_content_binding_errors(surface: Mapping, application: Mapping) -> list[str]:
    """Revalidate cross-source content references even on manually supplied IR."""
    intent = surface.get('interfaceIntent')
    if not isinstance(intent,Mapping) or 'hierarchy' not in intent:
        return []
    sources = {x.get('id'):x for key in ('models','states') for x in application.get(key, []) if isinstance(x,Mapping)}
    errors=[]
    for region in intent.get('regions', []):
        if not isinstance(region,Mapping):
            continue
        when=region.get('visibleWhen')
        if isinstance(when,Mapping):
            ident=when.get('state',{}).get('ref') if isinstance(when.get('state'), Mapping) else None
            target=sources.get(ident)
            schema=target.get('schema',{}) if isinstance(target,Mapping) else {}
            if (not isinstance(target,Mapping) or target.get('kind')!='state' or
                target.get('authority')!='renderer-local' or schema.get('kind')!='string' or
                when.get('equals') not in (schema.get('enum') or [])):
                errors.append(f'HTML conditional visibility {ident!r} requires declared renderer-local enum value')
        if not isinstance(region.get('content'), Mapping):
            continue
        content=region['content']
        ref=content.get('ref')
        if ref not in sources:
            errors.append(f'HTML content binding {ref!r} has no real model/state')
            continue
        schema=sources[ref].get('schema') or {}
        kind=schema.get('kind') if isinstance(schema,Mapping) else None
        expected={'collection':{'list'}, 'record':{'record'},
                  'scalar':{'string','text','integer','number','boolean'}}.get(content.get('kind'))
        if expected and kind and kind not in expected:
            errors.append(f'HTML {content.get("kind")} binding {ref} incompatible with schema {kind}')
    return errors
