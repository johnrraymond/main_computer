"""MIT Gamer G2: exact, gold-blind capacity-constrained edge projection.

This is an adaptation of mathematically constrained prediction, not a copy
of a particular MIT paper. The existing G2-0088 joint decoder is greedy.
Here the same learned caps are enforced as *hard* linear inequalities while
maximizing the joint G2 score over eligible typed edges.

No oracle graph, game name, text rule, or validation label enters decoding.
A nonoptimal/failed solver result is rejected, never silently called exact.
"""
from __future__ import annotations

from collections import defaultdict
import math

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array

from tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 import (
    format_edge, validate_surface,
)
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

VERSION = 'MIT'
SCHEMA = 'nanojev.gamer.graph.g2.mit.v1'
MAX_VARIABLES = 16384
MAX_SECONDS = 30.0


def _checked_cap(value, name):
    if type(value) is not int or not 0 <= value <= 128:
        raise ValueError('MIT_G2_BAD_CAP_' + name)
    return value


def candidate_edges(record, spec, *, labels_per_pair=4):
    """All above-threshold labels, not just each pair's winning class.

    Allowing non-top labels rescues candidates excluded by the G2-0088 argmax.
    This does NOT add new evidence: labels are from G2's actual logits.
    """
    if type(labels_per_pair) is not int or not 1 <= labels_per_pair <= len(BIND_CLASSES)-1:
        raise ValueError('MIT_G2_BAD_LABEL_BUDGET')
    if not isinstance(spec, dict) or spec.get('method') != 'joint':
        raise ValueError('MIT_G2_REQUIRES_FROZEN_JOINT_DECODER')
    objects, pairs = validate_surface(record)
    base = float(spec.get('threshold', 0.0))
    if not math.isfinite(base) or abs(base) > 50:
        raise ValueError('MIT_G2_BAD_THRESHOLD')
    thresholds = spec.get('kind_thresholds', {})
    if not isinstance(thresholds, dict):
        raise ValueError('MIT_G2_BAD_KIND_THRESHOLDS')
    result = []
    for (i, j), margins in sorted(pairs.items()):
        ranked = sorted(range(1, len(BIND_CLASSES)),
                        key=lambda t: (-margins[t-1], t))[:labels_per_pair]
        for t in ranked:
            key = '|'.join((objects[i][2], objects[j][2], BIND_CLASSES[t]))
            cut = float(thresholds.get(key, base))
            if not math.isfinite(cut) or abs(cut) > 50:
                raise ValueError('MIT_G2_BAD_KIND_THRESHOLD')
            gain = float(margins[t-1]) - cut
            if gain > 0:
                result.append((i, j, t, key, gain))
    if len(result) > MAX_VARIABLES:
        raise ValueError('MIT_G2_EXACT_SOLVER_BUDGET_EXCEEDED')
    return objects, result


def decode(record, spec, *, labels_per_pair=4, time_limit=MAX_SECONDS,
           return_diagnostics=False):
    """Solve an exact binary integer program subject to graph capacities.

    x_ijt in {0,1}; each directed pair has <=1 label, each typed
    source/destination degree respects the frozen 0088 caps. Missing cap
    entries retain the 0088 default of two. x=0 always feasible.
    """
    if not math.isfinite(time_limit) or not 0 < time_limit <= 300:
        raise ValueError('MIT_G2_BAD_TIME_LIMIT')
    objects, candidates = candidate_edges(record, spec, labels_per_pair=labels_per_pair)
    if not candidates:
        diagnostic = {'solver':'scipy.milp/highs','status':'OPTIMAL',
                      'variables':0,'selected':0,'objective_gain':0.0,
                      'labels_per_pair':labels_per_pair}
        return (set(),diagnostic) if return_diagnostics else set()
    out_caps = spec.get('out_caps', {})
    in_caps = spec.get('in_caps', {})
    if not isinstance(out_caps, dict) or not isinstance(in_caps, dict):
        raise ValueError('MIT_G2_INVALID_CAPS')
    groups = defaultdict(list)
    limits = {}
    for index,(i,j,t,key,gain) in enumerate(candidates):
        src_kind, dst_kind = objects[i][2], objects[j][2]
        incoming = '|'.join((dst_kind,src_kind,BIND_CLASSES[t]))
        constraints = (
            ('pair',i,j),
            ('out',i,dst_kind,t),
            ('in',j,src_kind,t),
        )
        cap_values = (1,
            _checked_cap(out_caps.get(key,2),'OUT'),
            _checked_cap(in_caps.get(incoming,2),'IN'))
        for name, cap in zip(constraints, cap_values):
            groups[name].append(index)
            limits[name] = cap
    # A separate global graph boundary is a contract, not a learned rule.
    max_edges = min(2048,len(candidates))
    groups[('total',)].extend(range(len(candidates)))
    limits[('total',)] = max_edges
    ri=[];ci=[];dat=[]
    for row,(key,indices) in enumerate(groups.items()):
        ri.extend([row]*len(indices));ci.extend(indices);dat.extend([1.0]*len(indices))
    A=coo_array((np.asarray(dat,dtype=np.float64),
       (np.asarray(ri,dtype=np.int32),np.asarray(ci,dtype=np.int32))),
       shape=(len(groups),len(candidates))).tocsr()
    b=np.asarray([limits[k] for k in groups],dtype=np.float64)
    gains=np.asarray([c[-1] for c in candidates],dtype=np.float64)
    # No spurious tie-breaking bonus: exact objective is sum of real margins.
    result=milp(c=-gains,integrality=np.ones(len(candidates),dtype=np.int32),
       bounds=Bounds(np.zeros(len(candidates)),np.ones(len(candidates))),
       constraints=LinearConstraint(A,np.zeros(len(groups)),b),
       options={'time_limit':float(time_limit),'mip_rel_gap':0.0})
    if result.status != 0 or result.x is None:
        raise RuntimeError('MIT_G2_EXACT_OPTIMIZATION_NOT_PROVEN: '+str(result.message))
    binary=np.rint(result.x).astype(np.int8)
    if (np.max(np.abs(result.x-binary)) > 1e-5 or
        np.any(A @ binary > b + 1e-6)):
        raise RuntimeError('MIT_G2_SOLVER_VIOLATED_HARD_CONSTRAINT')
    chosen={format_edge(objects,i,j,t) for take,(i,j,t,_,_) in
             zip(binary,candidates) if take}
    diagnostic={'solver':'scipy.milp/highs','status':'OPTIMAL',
        'variables':len(candidates),'selected':len(chosen),
        'objective_gain':float(gains@binary),
        'labels_per_pair':labels_per_pair,
        'constraints':len(groups), 'message':str(result.message)}
    return (chosen,diagnostic) if return_diagnostics else chosen
