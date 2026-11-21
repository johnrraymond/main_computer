"""Read-only offline spectral research; never selects a runtime layout.

A small cohort is diagnostic, not an app classifier. The retired policy-based
select_code_override / attach_code_override presentation paths are removed.
"""
from __future__ import annotations

from collections.abc import Mapping
import math


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


