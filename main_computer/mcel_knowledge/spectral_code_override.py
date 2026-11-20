"""Read-only cross-app spectral tripwire and source-verified `code` template.

The model infers *presentation* arguments only. It cannot infer authorization,
capability arguments, backend effects, scrolling permissions, or mutations.
A small cohort is diagnostic rather than a statistically certified classifier.
"""
from __future__ import annotations

from collections.abc import Mapping
import copy
import json
import math
import re

from .semantic_composition import sha

SCHEMA = 'mcel.spectral-code-tripwire.v1'
FEATURES = ('states', 'intents', 'capabilities', 'effects', 'nodes', 'forms',
            'controls', 'collections', 'documentEditors', 'sourceWrites',
            'consequential', 'regions', 'nestedRegions', 'largestRegion')
MIN_PEERS = 4
RESIDUAL_MULTIPLIER = 4.0
SCALE_FLOOR = 0.45


def signature(ir: Mapping) -> list[float]:
    if ir.get('schema') != 'mcel.application-ir.v1' or len(ir.get('surfaces', [])) != 1:
        raise ValueError('Spectral features require one normalized MCEL IR surface')
    surface = ir['surfaces'][0]
    nodes = surface.get('nodes') or []
    regions = (surface.get('interfaceIntent') or {}).get('regions') or []
    root = ((surface.get('interfaceIntent') or {}).get('hierarchy') or {}).get('rootId')
    intents = ir.get('intents') or []
    counts = [len(ir.get(kind) or []) for kind in ('states', 'intents', 'capabilities', 'effects')]
    counts += [len(nodes)]
    counts += [sum(n.get('nodeKind') == kind for n in nodes)
               for kind in ('form', 'control', 'collection', 'document-editor')]
    counts += [sum(i.get('risk') == risk for i in intents)
               for risk in ('source-write', 'consequential')]
    counts += [len(regions), sum(r.get('parentId') not in (None, root) for r in regions),
               max((len(r.get('members') or []) for r in regions), default=0)]
    return [math.log1p(n) for n in counts]


def _jacobi_symmetric(matrix, *, tolerance=1e-11):
    """Symmetric eigensystem without a runtime numpy/scipy dependency."""
    n = len(matrix)
    a = [list(map(float, row)) for row in matrix]
    vectors = [[float(i == j) for j in range(n)] for i in range(n)]
    for _ in range(12 * n * n):
        p, q = max(((i, j) for i in range(n) for j in range(i+1, n)),
                   key=lambda t: abs(a[t[0]][t[1]]))
        if abs(a[p][q]) < tolerance:
            break
        teta = 0.5 * math.atan2(2*a[p][q], a[q][q]-a[p][p])
        c, s = math.cos(teta), math.sin(teta)
        # Rotate rows/columns with a stable Jacobi plane rotation.
        app, aqq, apq = a[p][p], a[q][q], a[p][q]
        for k in range(n):
            if k in (p, q):
                continue
            akp, akq = a[k][p], a[k][q]
            a[k][p] = a[p][k] = c*akp-s*akq
            a[k][q] = a[q][k] = s*akp+c*akq
        a[p][p] = c*c*app - 2*c*s*apq + s*s*aqq
        a[q][q] = s*s*app + 2*c*s*apq + c*c*aqq
        a[p][q] = a[q][p] = 0.0
        for k in range(n):
            vkp, vkq = vectors[k][p], vectors[k][q]
            vectors[k][p], vectors[k][q] = c*vkp-s*vkq, s*vkp+c*vkq
    pairs = sorted(((max(0.0, a[i][i]), [vectors[j][i] for j in range(n)])
                    for i in range(n)), key=lambda e: (-e[0], tuple(e[1])))
    result = []
    for val, v in pairs:
        pivot = max(range(n), key=lambda i: (abs(v[i]), -i))
        if v[pivot] < 0:
            v = [-x for x in v]
        result.append((val, v))
    return result


def spectral_loading(ir: Mapping, peers: Mapping[str, Mapping]) -> dict:
    """Leave-target-out PCA, with conservative refusal for inadequate peers."""
    target = signature(ir)
    if len(peers) < MIN_PEERS or len(set(peers)) != len(peers):
        return {'schema': SCHEMA, 'outcome': 'INSUFFICIENT_PEERS',
                'peerCount': len(peers), 'trip': False}
    fingerprints = [p.get('fingerprints', {}).get('semantic') for p in peers.values()]
    target_fp = ir.get('fingerprints', {}).get('semantic')
    if not target_fp or None in fingerprints or len(set(fingerprints)) != len(fingerprints) or target_fp in fingerprints:
        return {'schema': SCHEMA, 'outcome': 'NONINDEPENDENT_OR_UNVERIFIED_PEERS',
                'peerCount': len(peers), 'trip': False}
    names = sorted(peers)
    examples = [signature(peers[name]) for name in names]
    n, dim = len(examples), len(FEATURES)
    center = [sum(x[j] for x in examples)/n for j in range(dim)]
    scales = [max(SCALE_FLOOR, math.sqrt(sum((x[j]-center[j])**2 for x in examples)/(n-1)))
              for j in range(dim)]
    centered = [[(x[j]-center[j])/scales[j] for j in range(dim)] for x in examples]
    candidate = [(target[j]-center[j])/scales[j] for j in range(dim)]
    covariance = [[sum(x[i]*x[j] for x in centered)/(n-1) for j in range(dim)] for i in range(dim)]
    values = _jacobi_symmetric(covariance)
    rank = sum(val > 1e-8 for val, _ in values)
    if rank < 2:
        return {'schema': SCHEMA, 'outcome': 'DEGENERATE_PEER_SPECTRUM',
                'peerCount': len(peers), 'rank': rank, 'trip': False}
    components = [v for val, v in values[:2]]
    def project(x):
        loadings = [sum(a*b for a, b in zip(x, v)) for v in components]
        residual = [x[i] - sum(loadings[k]*components[k][i] for k in range(2)) for i in range(dim)]
        return loadings, residual, sum(v*v for v in residual)
    loads, residual, energy = project(candidate)
    peers_energy = [project(row)[2] for row in centered]
    limit = RESIDUAL_MULTIPLIER * max(1.0, max(peers_energy))
    trip = energy > limit
    # Values are rounded for an inspectable report, not fed back to the decision.
    rounded = lambda v: round(float(v), 7)
    ranked = sorted(zip(FEATURES, residual), key=lambda pair: (-abs(pair[1]), pair[0]))
    return {
        'schema': SCHEMA, 'outcome': 'SPECTRAL_OUTLIER' if trip else 'WITHIN_PEER_ENVELOPE',
        'trip': trip, 'peerCount': n, 'peerApplications': names,
        'sourceIrHash': target_fp, 'peerIrHashes': {name: peers[name]['fingerprints']['semantic'] for name in names},
        'featureNames': list(FEATURES), 'effectiveRank': rank, 'retainedComponents': 2,
        'eigenvalues': [rounded(v[0]) for v in values[:min(5,rank)]],
        'eigenvectors': [[rounded(c) for c in vec] for _, vec in values[:2]],
        'targetLoadings': [rounded(v) for v in loads],
        'residualEnergy': rounded(energy), 'maxPeerResidualEnergy': rounded(max(peers_energy)),
        'tripThreshold': rounded(limit),
        'residualFeatureContributions': [{'feature':name, 'loading':rounded(val)} for name,val in ranked[:6]],
        'caveat': 'Exploratory structural outlier with four peers; not a statistical or authorization guarantee',
    }


def resolve_code_arguments(ir: Mapping, plan: Mapping, policy: Mapping) -> dict:
    """Resolve a fixed named code layout from verified IR, never DOM positions."""
    if policy.get('schema') != 'mcel.code-override-policy.v1' or policy.get('profile') != 'code':
        raise ValueError('Unknown code override policy')
    names=policy.get('groups')
    rules=policy.get('operationRules')
    if (names != ['execution','preview','session'] or not isinstance(rules,list) or not rules
            or len(rules)>32 or not all(isinstance(r,Mapping)
            and set(r) in ({'group','operation','risk'},{'group','operation','risk','nodeKind'})
            and r.get('group') in names
            and isinstance(r.get('operation'),str) and re.fullmatch(r'[a-z][a-z0-9-]*',r['operation'])
            and r.get('risk') in {'external-read','source-write','consequential'}
            and (r.get('nodeKind','control') in {'form','control'}) for r in rules)):
        raise ValueError('Invalid code override operation rules')
    if policy.get('sharedResultPrecedence') != names or policy.get('unclaimedResultGroup') not in names:
        raise ValueError('Invalid code override result policy')
    d = plan.get('documentWorkbench')
    if not isinstance(d, Mapping) or d.get('schema') != 'mcel.document-workbench.v1':
        raise ValueError('Code override requires an already-verified document workbench')
    regions = {r['id']:r for r in ir['surfaces'][0]['interfaceIntent']['regions']}
    nodes = {n['id']:n for n in ir['surfaces'][0]['nodes']}
    intents = {i['id']:i for i in ir['intents']}
    def ref(x): return x.get('ref') if isinstance(x, Mapping) else None
    def zone_matches(ids, kind, side):
        return all(regions[id]['purpose'] == kind and regions[id].get('preferences', {}).get('side') == side for id in ids)
    if not (zone_matches(d['navigation'],'navigation','start') and
            zone_matches(d['status'],'status','bottom') and
            all(regions[id].get('preferences',{}).get('side') == 'end' for id in d['companion']) and
            all(regions[id].get('preferences',{}).get('side') == 'bottom' for id in d['bottom']) and
            len(d['navigation']) == len(d['status']) == 1):
        raise ValueError('Code override arguments do not satisfy source-owned dock relationships')
    anchor = regions[d['document']]
    if not (anchor.get('priority') == 'primary' and
            sum(nodes[m['ref']]['nodeKind']=='document-editor' for m in anchor['members']) == 1):
        raise ValueError('Code override has no unique verified document editor')
    aid = [regions[id] for id in d['companion'] if regions[id].get('purpose') == policy['assistancePurpose']]
    if len(aid) != 1:
        raise ValueError('Code override requires one semantic assistance region')
    aide = aid[0]
    if any(region.get('parentId') == aide['id'] for region in regions.values()):
        # Existing authored nested groups are stronger than inferred fallbacks.
        return {'profile':'code', 'docks':copy.deepcopy(dict(d)), 'grouping':'authored', 'groups':{}}
    groups = {name:[] for name in names}
    result_bindings = {}
    for m in aide['members']:
        node = nodes[m['ref']]
        action = intents.get(ref(node.get('intent')), {})
        if node.get('nodeKind') == 'state-value':
            continue
        op,risk = action.get('operation'),action.get('risk')
        matched=[r['group'] for r in rules if r['operation']==op and r['risk']==risk
                 and r.get('nodeKind', node.get('nodeKind'))==node.get('nodeKind')]
        if len(set(matched)) != 1:
            raise ValueError('Unclassified or ambiguous code override operation: '+node['id'])
        group=matched[0]
        groups[group].append(node['id'])
        for binding in action.get('resultBindings') or []:
            target=ref(binding.get('target'))
            if target:result_bindings.setdefault(target,set()).add(group)
    if any(not groups[group] for group in names):
        raise ValueError('Incomplete code override semantic operations')
    for m in aide['members']:
        node=nodes[m['ref']]
        if node['nodeKind'] != 'state-value':continue
        state=ref((node.get('value') or {}).get('state')) or ref(node.get('state'))
        owners=result_bindings.get(state,set())
        # Ambiguity is intentionally governed by the named application policy,
        # never by generated DOM order or a guessed backend operation.
        if owners:
            owner=next(g for g in policy['sharedResultPrecedence'] if g in owners)
        else:
            owner=policy['unclaimedResultGroup']
        groups[owner].append(node['id'])
    owned=[n for group in groups.values() for n in group]
    if sorted(owned)!=sorted(m['ref'] for m in aide['members']) or len(owned)!=len(set(owned)):
        raise ValueError('Code override did not account for each assistance node exactly once')
    return {'profile':'code','docks':copy.deepcopy(dict(d)), 'grouping':'inferred',
            'region':aide['id'], 'groups':{k:sorted(v) for k,v in groups.items()}}


def attach_code_override(ir: Mapping, plan: Mapping, evidence: Mapping, policy: Mapping) -> dict:
    """Select code fallback only with verified outlier evidence and inferred args."""
    if evidence.get('schema') != SCHEMA or evidence.get('sourceIrHash') != ir.get('fingerprints',{}).get('semantic'):
        raise ValueError('Spectral evidence is not bound to the compiled application')
    if not evidence.get('trip'):
        return copy.deepcopy(plan)
    args=resolve_code_arguments(ir, plan, policy)
    result=copy.deepcopy(plan)
    result['codeOverride']={'schema':'mcel.code-override.v1','profile':'code',
                            'sourceIrHash':evidence['sourceIrHash'],
                            'spectralOutcome':evidence['outcome'],
                            'policyHash':sha(policy),
                            'arguments':args}
    # This is a presentation-only selection. Verified IR and operations are unchanged.
    result['documentWorkbench'] = copy.deepcopy(args['docks'])
    result.pop('fingerprint',None)
    result['fingerprint']=sha(result)
    return result


def load_peer_applications(repo):
    """Use existing MCEL application sources, never generated proxy examples."""
    from pathlib import Path
    from main_computer.mcel_dsl_compiler import compile_dsl_application
    from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
    root = Path(repo)
    peers = {name: compile_semantic_app(name, root) for name in ('file-manager', 'task-manager')}
    for name in ('calculator', 'slh'):
        compiled = compile_dsl_application(root / 'mcel_apps' / name / 'application.js',
                                           write_candidate=False)
        if not compiled.valid or compiled.normalized_ir is None:
            raise ValueError('Peer application failed to compile: ' + name)
        peers[name] = compiled.normalized_ir
    return peers


def select_code_override(ir: Mapping, base_plan: Mapping, repo):
    """Return validated presentation plan and full spectral evidence.

    A failure to infer valid presentation arguments NEVER changes application
    operations or invents a fallback. The ordinary verified plan survives.
    """
    app=ir.get('application',{}).get('appId','')
    if not re.fullmatch(r'[a-z][a-z0-9-]*', app):
        raise ValueError('Invalid app ID for override source resolution')
    policy_path=__import__('pathlib').Path(repo) / 'mcel_apps' / app / 'code-override.json'
    if not policy_path.is_file():
        return copy.deepcopy(base_plan), {'schema':SCHEMA,'outcome':'NO_AUTHORED_CODE_OVERRIDE','trip':False}
    policy=json.loads(policy_path.read_text(encoding='utf-8'))
    try:
        evidence = spectral_loading(ir, load_peer_applications(repo))
    except (OSError, ValueError, RuntimeError) as exc:
        # A changed/missing peer is not permission to execute a guessed fallback.
        return copy.deepcopy(base_plan), {'schema':SCHEMA,
            'outcome':'PEER_REFERENCE_UNAVAILABLE', 'trip':False,
            'refusal':type(exc).__name__}
    if not evidence.get('trip'):
        return copy.deepcopy(base_plan), evidence
    try:
        candidate = attach_code_override(ir, base_plan, evidence, policy)
    except ValueError as exc:
        return copy.deepcopy(base_plan), {**evidence, 'outcome':'OVERRIDE_ARGUMENT_REFUSED',
                                           'trip':False, 'refusal':str(exc)}
    # Independently reconstruct the entire selected plan before handing it to
    # the renderer; do not trust an inferred argument without source proof.
    if candidate != attach_code_override(ir, base_plan, evidence, policy):
        raise ValueError('Non-deterministic code override projection')
    return candidate, evidence
