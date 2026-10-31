"""MCEL 07B.1a: layout-blind, provenance-backed semantic dependency audit.

This is intentionally NOT a task synthesizer.  It cannot infer presentation,
selection-to-inspector coupling, or action authority from shared state or names.

Input to ``extract_validated_graph`` must already be normalized, validated
mcel.application-ir.v1.  The pure extractor never reads surfaces/layouts.
``audit_application`` is the validation boundary and necessarily validates the
whole source IR (including its presentation); do not conflate those operations.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
from typing import Any, Mapping

from main_computer.mcel_application_ir import canonical_json_bytes, validate_application_ir

SCHEMA = 'mcel.semantic-dependency-audit.v1'
PROJECTION_ALGORITHM = 'sha256-mcel-layout-free-semantics-v1'
DECLARATIONS = ('models', 'states', 'derivations', 'intents', 'capabilities', 'effects')
# No surfaces (including surface.controls), layouts, labels, source/provenance,
# runtime facades, presentation roles, or authored shell data.


class SemanticAuditError(ValueError):
    """Invalid input or graph contract; never silently fall back to a layout."""


def _fingerprint(obj: Any) -> str:
    return 'sha256:' + hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def _reference_id(value: Any) -> str | None:
    if isinstance(value, Mapping) and isinstance(value.get('ref'), str):
        return value['ref']
    return None


def _semantic_node_fields(kind: str, item: Mapping[str, Any]) -> dict:
    """Finite allowlist of facts; no arbitrary, presentation-shaped copying."""
    if kind == 'model':
        keys = ('conceptRef', 'observationRef', 'cardinality', 'identityScope')
    elif kind == 'field':
        keys = ('conceptRef', 'propertyName', 'requiredObservation', 'semanticDerivation')
    elif kind == 'state':
        keys = ('authority', 'schema')
    elif kind == 'derivation':
        keys = ('schema', 'expression')
    elif kind == 'intent':
        keys = ('operationKind', 'risk', 'lane')
    elif kind == 'intent-input':
        keys = ('schema',)
    elif kind == 'capability':
        keys = ('risk', 'authority', 'operationKind')
    elif kind == 'effect':
        keys = ('effectKind', 'risk', 'cardinality')
    else:
        keys = ()
    return {k: item[k] for k in keys if k in item}


def extract_validated_graph(validated_ir: Mapping[str, Any]) -> dict[str, Any]:
    """Read ONLY the allowlisted semantic collections of an already validated IR.

    No calls to validation, filesystem, catalog loading, rendering, or browser.
    Exact JSON pointer provenance points to normalized IR declarations.
    """
    if validated_ir.get('schema') != 'mcel.application-ir.v1':
        raise SemanticAuditError('Expected normalized mcel.application-ir.v1')
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}

    def add_node(item: Mapping[str, Any], kind: str, path: str) -> None:
        ident = item.get('id')
        if not isinstance(ident, str) or not ident or ident in nodes:
            raise SemanticAuditError(f'Missing or duplicate semantic identity at {path}')
        nodes[ident] = {'id': ident, 'kind': kind, 'facts': _semantic_node_fields(kind, item),
                        'evidence': [path]}

    def add_edge(kind: str, origin: str, target: str, paths: list[str],
                 *, certainty: str = 'explicit-verified') -> None:
        if origin not in nodes or target not in nodes:
            raise SemanticAuditError(f'Unresolved semantic reference: {origin} -> {target} at {paths}')
        identity = [kind, origin, target, sorted(paths)]
        ident = 'edge:' + _fingerprint(identity).split(':', 1)[1][:24]
        edges[ident] = {'id': ident, 'kind': kind, 'from': origin, 'to': target,
                        'certainty': certainty, 'evidence': sorted(paths),
                        'impliesUiCoordination': False}

    # First pass collects identities, enabling strict reference resolution.
    for collection in DECLARATIONS:
        items = validated_ir.get(collection, [])
        if not isinstance(items, list):
            raise SemanticAuditError(f'Expected normalized {collection} array')
        for index, item in enumerate(items):
            path = f'/{collection}/{index}'
            if not isinstance(item, Mapping):
                raise SemanticAuditError(f'Invalid normalized declaration at {path}')
            add_node(item, item['kind'], path)
            nested = 'fields' if collection == 'models' else 'input' if collection == 'intents' else None
            if nested:
                for child_index, child in enumerate(item.get(nested, [])):
                    if not isinstance(child, Mapping):
                        raise SemanticAuditError(f'Invalid nested declaration at {path}/{nested}/{child_index}')
                    add_node(child, child['kind'], f'{path}/{nested}/{child_index}')

    # 07B.1b: typed reference selection is ONLY recognized when an explicit
    # schema-of model reference and an already validated bounded viewTransition
    # agree. These are semantic/type facts, not presentation instructions and
    # not proof that a chosen token is a real model record at runtime.
    typed_models: dict[str, str] = {}
    for collection in ('states', 'intents'):
        for index, item in enumerate(validated_ir.get(collection, [])):
            typed = ([(item, f'/states/{index}')] if collection == 'states' else
                     [(entry, f'/intents/{index}/input/{j}')
                      for j, entry in enumerate(item.get('input', []))])
            for entry, path in typed:
                schema = entry.get('schema', {})
                if not isinstance(schema, Mapping) or 'of' not in schema:
                    continue
                of = schema['of']
                model_id = _reference_id(of)
                if (not isinstance(of, Mapping) or set(of) != {'ref'} or
                        not model_id or not model_id.startswith('model:') or
                        model_id not in nodes or nodes[model_id]['kind'] != 'model'):
                    raise SemanticAuditError(f'Invalid semantic model type at {path}/schema/of')
                # A string enum can encode a finite set of typed reference
                # choices. Arbitrary dynamic record IDs are NOT supported by
                # the existing validated renderer-local transition contract.
                if (schema.get('kind') != 'string' or
                        not isinstance(schema.get('enum'), list) or
                        not schema['enum'] or
                        any(not isinstance(v, str) or not v for v in schema['enum']) or
                        len(set(schema['enum'])) != len(schema['enum'])):
                    raise SemanticAuditError(f'Model-typed probe selection must be a finite string enum: {path}')
                typed_models[entry['id']] = model_id
                add_edge('state-typed-as-model' if collection == 'states' else 'input-typed-as-model',
                         entry['id'], model_id, [f'{path}/schema/of/ref'])

    for index, intent in enumerate(validated_ir.get('intents', [])):
        transition = intent.get('viewTransition')
        if not isinstance(transition, Mapping):
            continue
        target = _reference_id(transition.get('target'))
        value = transition.get('value')
        input_id = _reference_id(value.get('input')) if isinstance(value, Mapping) else None
        if (not target or not input_id or
                typed_models.get(target) is None or
                typed_models.get(target) != typed_models.get(input_id)):
            continue
        # validate_application_ir has already checked the transition is an
        # enumerated renderer-local assignment to one declared view-write.
        add_edge('bounded-typed-view-selection', intent['id'], typed_models[target], [
            f'/intents/{index}/viewTransition/target/ref',
            f'/intents/{index}/viewTransition/value/input/ref',
            *[f'/states/{j}/schema/of/ref' for j, st in enumerate(validated_ir['states'])
              if st['id'] == target],
            *[f'/intents/{index}/input/{j}/schema/of/ref'
              for j, entry in enumerate(intent.get('input', [])) if entry['id'] == input_id],
        ], certainty='mechanically-derived')

    writers: dict[str, list[tuple[str, str]]] = defaultdict(list)
    readers: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for collection in DECLARATIONS:
        for index, item in enumerate(validated_ir.get(collection, [])):
            path = f'/{collection}/{index}'
            ident = item['id']
            if collection == 'models':
                for j, field in enumerate(item.get('fields', [])):
                    add_edge('declares-field', ident, field['id'], [f'{path}/fields/{j}'])
            elif collection == 'intents':
                for j, inp in enumerate(item.get('input', [])):
                    add_edge('accepts-input', inp['id'], ident, [f'{path}/input/{j}'])
                for attr in ('reads', 'writes', 'viewWrites', 'effectRefs'):
                    for j, ref in enumerate(item.get(attr, [])):
                        target = _reference_id(ref)
                        if not target:
                            raise SemanticAuditError(f'Unbound {attr} at {path}/{attr}/{j}')
                        evidence = f'{path}/{attr}/{j}/ref'
                        if attr == 'reads':
                            add_edge('reads-state', target, ident, [evidence])
                            readers[target].append((ident, evidence))
                        elif attr in ('writes', 'viewWrites'):
                            add_edge('writes-state' if attr == 'writes' else 'view-writes-state',
                                     ident, target, [evidence])
                            writers[target].append((ident, evidence))
                        else:
                            add_edge('requests-effect', ident, target, [evidence])
                # A view transition's declared state assignment is already
                # represented by viewWrites. Do not execute or introspect it.
            elif collection == 'effects':
                for attr, kind in (('owner','owned-by-intent'), ('authority','governed-by')):
                    ref = _reference_id(item.get(attr))
                    if ref:
                        add_edge(kind, ident if attr == 'authority' else ref,
                                 ref if attr == 'authority' else ident,
                                 [f'{path}/{attr}/ref'])
                # target may be an expression (not a semantic reference); no guesses.
                ref = _reference_id(item.get('target'))
                if ref:
                    add_edge('effect-target-ref', ident, ref, [f'{path}/target/ref'])
            elif collection == 'derivations':
                for j, ref in enumerate(item.get('dependsOn', [])):
                    target = _reference_id(ref)
                    if target:
                        add_edge('derivation-depends-on', target, ident, [f'{path}/dependsOn/{j}/ref'])

    # Shared state yields a mechanical DATA dependency, not a workflow or
    # permission to create an inspector, tab, navigation tree, or UI edge.
    for state_id in sorted(set(writers) & set(readers)):
        for writer, write_path in writers[state_id]:
            for reader, read_path in readers[state_id]:
                if writer != reader:
                    add_edge('shared-state-dataflow', writer, reader,
                             [write_path, read_path], certainty='mechanically-derived')

    ordered_nodes = sorted(nodes.values(), key=lambda n: n['id'])
    ordered_edges = sorted(edges.values(), key=lambda e: (e['kind'], e['from'], e['to'], e['id']))
    # A distinct *projection* hash, never the existing presentation-sensitive
    # application semanticFingerprint. The normalized evidence addresses are
    # stable for canonical IR; fingerprint only intrinsic facts/relationships.
    projection = {'nodes': [{k: v for k, v in n.items() if k != 'evidence'} for n in ordered_nodes],
                  'edges': [{k: v for k, v in e.items() if k not in ('id', 'evidence')}
                            for e in ordered_edges]}
    direct = [e for e in ordered_edges if e['kind'] in ('reads-state','writes-state','view-writes-state')]
    missing = []
    if not any(n['kind'] == 'model' for n in ordered_nodes):
        missing.append({'code': 'NO_TYPED_MODELS', 'detail': 'No independent model declaration was provided.'})
    if not any(n['kind'] == 'state' for n in ordered_nodes):
        missing.append({'code': 'NO_DECLARED_STATES', 'detail': 'No executable state relationships were declared.'})
    if not direct:
        missing.append({'code': 'NO_DECLARED_DATA_DEPENDENCIES', 'detail': 'No intent read/write/viewWrite relations are declared.'})
    # The IR used here has no explicit task/workflow coordination relation.
    # Do NOT promote ordinary state dataflow to user/task coordination.
    missing.append({'code': 'TASK_COORDINATION_NOT_ESTABLISHED',
                    'detail': 'Dataflow does not prove navigation, selection-to-details, or task priority.'})
    return {'schema': SCHEMA, 'projectionAlgorithm': PROJECTION_ALGORITHM,
            'projectionFingerprint': _fingerprint(projection),
            'status': 'DEPENDENCIES_EXTRACTED' if direct else 'SEMANTICS_INSUFFICIENT',
            'synthesisReady': False,
            'nodes': ordered_nodes, 'edges': ordered_edges,
            'unresolved': missing,
            'counts': {'nodes': len(ordered_nodes), 'edges': len(ordered_edges),
                       'declaredDataDependencies': len(direct),
                       'mechanicalDataflows': sum(e['kind']=='shared-state-dataflow' for e in ordered_edges),
                       'verifiedTaskCoordinations': 0,
                       'boundedTypedViewSelections': sum(e['kind']=='bounded-typed-view-selection' for e in ordered_edges)}}


def audit_catalog_join(graph: Mapping[str, Any], catalog: Mapping[str, Any]) -> dict[str, Any]:
    """Only explicit observationRef joins to catalog observations; no name matching.

    A joined *model* is not evidence that a catalog operation authorizes an
    executable intent. No catalog operation is added to the output graph.
    """
    observations = catalog.get('observations', {})
    if not isinstance(observations, Mapping):
        raise SemanticAuditError('Invalid catalog observations')
    joins = []
    for node in graph['nodes']:
        if node['kind'] != 'model':
            continue
        ref = node['facts'].get('observationRef')
        if ref is not None:
            if ref not in observations:
                raise SemanticAuditError(f'Explicit observationRef missing from catalog: {ref}')
            observation = observations[ref]
            if (node['facts'].get('conceptRef') != observation.get('concept') or
                    node['facts'].get('cardinality') != observation.get('cardinality')):
                raise SemanticAuditError(f'Explicit observationRef has incompatible type/cardinality: {ref}')
            joins.append({'modelId': node['id'], 'observationId': ref,
                          'evidence': node['evidence'][0]+'/observationRef'})
    return {'status': 'JOINED_MODELS_ONLY' if joins else 'BLOCKED_UNJOINED_SOURCES',
            'joins': sorted(joins, key=lambda x: x['modelId']),
            'operationsImported': 0, 'verifiedTaskRelationsImported': 0}


def audit_application(document: Mapping[str, Any], *, catalog: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate full IR before extracting its layout-independent projection.

    The validator necessarily consumes layouts/surfaces to check IR validity.
    Never claim the validator itself is a layout-nonreader.
    """
    validation = validate_application_ir(document)
    if not validation.valid or validation.normalized is None:
        return {'schema': SCHEMA, 'status': 'FAIL_CONTRACT', 'valid': False,
                'diagnostics': [d.to_dict() for d in validation.diagnostics]}
    graph = extract_validated_graph(validation.normalized)
    graph['valid'] = True
    if catalog is not None:
        graph['catalogJoin'] = audit_catalog_join(graph, catalog)
    return graph
