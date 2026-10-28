"""Patch 13 stage 3: read-only, same-snapshot live semantic shadow.

The producer, not this module, obtains the data.  We never fetch/execute,
assign revisions, or claim observation completeness/continuity.  Calling this
with the *very same* snapshot that is served to an existing consumer avoids
sampling races in volatile process and resource measurements.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from . import ABSENT, SemanticCatalogError, _path_get, load_catalog
from .ir_bridge import compile_knowledge_ir, resolve_knowledge, collection_projection
from .presentation_policy import apply_presentation_policy, load_presentation_policy

LIVE_SCHEMA = 'mcel.live-semantic-shadow.v1'
PROFILES: dict[str, tuple[str, ...]] = {
    'task-manager': ('task-manager.processes',),
    'file-explorer': ('file-explorer.entries',),
    'worker': ('worker.logical-workers', 'worker.market-workers', 'worker.sessions', 'worker.runtime'),
}


def _bindings(profile: str, source: Mapping[str, Any], scope: Mapping[str, Any] | None) -> dict:
    if profile == 'task-manager':
        # Both actual Task Manager producer paths occur in the field, and the
        # catalog can normalize them without altering the authoritative raw.
        envelope = {'producer': 'TaskManagerService.snapshot', 'completeness': 'bounded',
                    'scope': {'kind': 'bounded-top-n', 'parameters': {
                        'limit': 'mcel_live_request.limit',
                        'query': 'mcel_live_request.query',
                        'include_all': 'mcel_live_request.include_all'}}}
        return {'task-manager.processes': {
            'properties': {'created_at': {'path': 'created_at',
                            'formats': ['iso8601', 'cim-dmtf', 'dotnet-json']}},
            'envelope': envelope}}
    if profile == 'file-explorer':
        return {'file-explorer.entries': {
            'properties': {'modified_at': {'path': 'mtime', 'format': 'unix-seconds'}},
            'envelope': {'producer': 'FileExplorer.list-or-search', 'completeness': 'bounded',
                         'scope': {'kind': 'filtered', 'parameters': {'root_id': 'root_id'}}}}}
    if profile == 'worker':
        # The real /worker/runtime-status endpoint returns a top-level runtime;
        # archived market snapshots use worker_runtime.runtime. Both compile
        # through the same runtime concept with declarative source remapping.
        return {'worker.runtime': {'source': 'runtime'}} if 'runtime' in source and 'worker_runtime' not in source else {}
    raise SemanticCatalogError('Unknown live observation profile')


def _raw_parity(source: Mapping[str, Any], projection: Mapping[str, Any],
                snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Compare typed records to the independent, exact *served* producer dict.

    The canonical interpreter's raw copy alone isn't a meaningful parity test;
    reconstruct record bindings against the producer's original source records.
    This catches accidental changes even if the adapter consistently changes
    both the canonical output and its projection.
    """
    from copy import deepcopy
    raw_source = _path_get(snapshot, source.get('sourcePath', ''))
    if isinstance(raw_source, list):
        original = raw_source
    elif isinstance(raw_source, Mapping):
        original = list(raw_source.values()) if source.get('cardinality') == 'many' else [raw_source]
    else:
        original = []
    interpreted = source.get('records', [])
    projected = projection.get('rows', [])
    rejected = projection.get('quarantinedRows', [])
    expected = len(original)
    count_ok = len(projected) + len(rejected) == expected
    canonical_preserved = all(row.get('mcelCanonicalRecord') in interpreted for row in projected)
    consumed: set[int] = set()
    for item in interpreted:
        # Only properties bound to source records are compared; derived and
        # parent/context facts have their own evidence and are not producer keys.
        def matches(record: Any) -> bool:
            if not isinstance(record, Mapping):
                return False
            for property_value in item['properties'].values():
                if property_value.get('fromContext') != 'record':
                    continue
                raw = _path_get(record, property_value['source'])
                expected_raw = None if raw is ABSENT else deepcopy(raw)
                if expected_raw != property_value['raw']:
                    return False
            return True
        index = next((i for i, record in enumerate(original)
                     if i not in consumed and matches(record)), None)
        if index is None:
            canonical_preserved = False
            break
        consumed.add(index)
    # Individually rejected nonmapping records still count in the producer.
    return {'ok': count_ok and canonical_preserved and
            len(interpreted) + len(source.get('rejectedRecords', [])) == expected,
            'sourceRecords': expected, 'accepted': len(projected), 'quarantined': len(rejected),
            'sourceRecordsAccountedFor': count_ok,
            'canonicalRecordsUnchanged': canonical_preserved,
            'note': 'same-response independent producer-field comparison; no second OS sample'}


def build_live_shadow(snapshot: Mapping[str, Any], *, profile: str,
                      request_scope: Mapping[str, Any] | None = None,
                      include_canonical: bool = False,
                      presentation_policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Opt-in live shadow from a previously obtained producer response.

    A profile selects observation declarations; generic compiler, interpreter
    and consumer adapter are shared. Never mutate the incoming snapshot.
    Individual observation problems are reported as REFUSED, not turned into
    an empty successful collection. No write/capability endpoints are exposed.
    """
    if presentation_policy is not None and profile != 'task-manager':
        raise SemanticCatalogError('Presentation policy requires an explicitly selected compatible profile')
    if profile not in PROFILES or not isinstance(snapshot, Mapping):
        raise SemanticCatalogError('Unsupported semantic live profile or snapshot')
    clean = deepcopy(dict(snapshot))
    if request_scope is not None:
        if profile != 'task-manager':
            raise SemanticCatalogError('Request scope is only bound for Task Manager')
        if not isinstance(request_scope, Mapping):
            raise SemanticCatalogError('Invalid request scope')
        clean['mcel_live_request'] = {
            k: deepcopy(request_scope[k]) for k in ('query', 'limit', 'include_all') if k in request_scope
        }
    catalog = load_catalog()
    selected = PROFILES[profile]
    bridge = compile_knowledge_ir('live-semantic-shadow', observation_ids=list(selected),
                                  bindings=_bindings(profile, clean, request_scope), catalog=catalog)
    view = resolve_knowledge(clean, bridge, catalog=catalog)
    results: dict[str, Any] = {}
    ok = True
    for ident in selected:
        src = view['sources'][ident]
        if src['state'] != 'present':
            # Some worker endpoints provide only one of several observation
            # families. Missing sources are NOT fabricated as empty collections.
            results[ident] = {'status': 'not-observed', 'state': src['state'],
                              'envelope': src.get('envelope')}
            continue
        # Some real observations are single objects without a declared
        # consumer identity; canonical interpretation is meaningful, but a
        # keyed collection would invent an identity. Defer collection only.
        if not catalog['observations'][ident].get('identity'):
            results[ident] = {'status': 'canonical-only', 'reason': 'no-declared-collection-identity',
                              'recordCount': len(src['records']), 'envelope': src.get('envelope'),
                              'canonicalSource': deepcopy(src) if include_canonical else None}
            continue
        try:
            projection = collection_projection(view, ident, catalog=catalog)
            if presentation_policy is not None:
                projection = apply_presentation_policy(projection, presentation_policy)
            parity = _raw_parity(src, projection, snapshot)
            if not parity['ok']:
                ok = False
            result = {'status': 'shadow-only' if parity['ok'] else 'parity-failed',
                      'parity': parity, 'collection': projection,
                      'continuity': {'status': 'gated-unverified',
                         'reason': 'source generation and hosted DOM continuity not certified for canonical consumer'}}
            if include_canonical:
                result['canonicalSource'] = deepcopy(src)
            results[ident] = result
        except (SemanticCatalogError, ValueError, KeyError, TypeError) as exc:
            ok = False
            results[ident] = {'status': 'refused', 'reason': str(exc),
                              'sourceState': src['state'],
                              'diagnostics': [d for d in view['diagnostics'] if d.get('source') == ident]}
    return {'schema': LIVE_SCHEMA, 'ok': ok, 'profile': profile,
            'irSchema': bridge['applicationIr']['schema'],
            'semanticFingerprint': bridge['semanticFingerprint'],
            'sourceBindingFingerprint': bridge['sourceBindingFingerprint'],
            'observations': results, 'diagnostics': view['diagnostics'],
            'authority': 'read-only; not promoted; no operational effects',
            'producerSnapshotSampledAgain': False,
            'revision': None, 'revisionEvidence': 'producer did not publish a revision',
            'continuityCertified': False}
