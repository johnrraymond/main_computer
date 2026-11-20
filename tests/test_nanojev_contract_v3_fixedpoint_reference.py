"""Independent fixed-point recovery oracle against the new v3 graph checker.

Unlike the production verifier's per-node BFS, this enumerates an operational
reference machine and computes the least fixed point of settlement-winning
nodes under each single-role failure. No production successor/recovery calls.
"""
from __future__ import annotations
from collections import deque
import copy
import json
from pathlib import Path

from tools import nanojev_contract_v3_reference as ref
from tools import nanojev_contract_v3_verifier as verifier
from tools import nanojev_contract_v3_language as language

FIXTURE=Path(__file__).resolve().parents[1]/'tools/fixtures/nanojev_contract_v3_progressive_escrow_v1.json'


def spec():
    return json.loads(FIXTURE.read_text())


def evaluate_path(s, path):
    m=ref.ReferenceEscrow(s)
    m.fund('payer',s['amount_wei'])
    for step in path:
        if step=='tick':
            m.tick_to_deadline()
        else:
            m.execute(step,m.rules[step]['actor'])
    return m


def reference_reachability(s, unavailable=None):
    start=ref.ReferenceEscrow(s)
    start.fund('payer',s['amount_wei'])
    paths={start.snapshot():()}
    queue=deque([start.snapshot()])
    adjacency={}
    while queue:
        node=queue.popleft()
        path=paths[node]
        machine=evaluate_path(s,path)
        moves=[]
        if not machine.after_deadline:
            trial=evaluate_path(s,path)
            trial.tick_to_deadline()
            moves.append(('tick',trial.snapshot()))
        if machine.state not in machine.sinks:
            for name,r in machine.rules.items():
                if r['actor']==unavailable:
                    continue
                trial=evaluate_path(s,path)
                try:
                    trial.execute(name,r['actor'])
                except ref.ReferenceRevert:
                    continue
                moves.append((name,trial.snapshot()))
        adjacency[node]=set(dest for _action,dest in moves)
        for action,dest in moves:
            if dest not in paths:
                paths[dest]=(*path,action)
                queue.append(dest)
    return paths,adjacency


def reference_unsafe(s):
    paths,adjacency=reference_reachability(s)
    evaluated={}
    for absent in (None,*language.ROLES):
        # Rebuild graph with actions suppressed for one unavailable role.
        # Enumerate outgoing edges for ALL originally reachable states, not only
        # states reachable from fresh funding after that role was removed.
        edges={}
        for origin,path in paths.items():
            m=evaluate_path(s,path)
            destinations=set()
            if not m.after_deadline:
                future=evaluate_path(s,path);future.tick_to_deadline()
                destinations.add(future.snapshot())
            if m.state not in m.sinks:
                for name,r in m.rules.items():
                    if r['actor']==absent:continue
                    clone=evaluate_path(s,path)
                    try:clone.execute(name,r['actor'])
                    except ref.ReferenceRevert:continue
                    destinations.add(clone.snapshot())
            edges[origin]=destinations
        terminal={node for node in paths if node[0] in {'completed','refunded'} and node[2]==0}
        winning=set(terminal)
        while True:
            grown=winning|{state for state,outs in edges.items() if outs & winning}
            if grown==winning:break
            winning=grown
        evaluated[absent]={node for node in paths if node not in winning and node[0] not in ('completed','refunded')}
    return evaluated


def test_fixedpoint_reference_agrees_on_safe_contract():
    s=spec()
    expected=reference_unsafe(s)
    assert all(not points for points in expected.values())
    got=verifier.verify(s)
    assert got['status']=='PASS_CONDITIONAL'
    assert got['liveness']['single_role_traps']==[]


def test_fixedpoint_detects_same_missing_rescue_as_graph():
    s=spec()
    s['rules']=[r for r in s['rules'] if r['name']!='refund_funded_worker']
    expected=reference_unsafe(s)
    got=verifier.verify(s)
    ref_traps={(p[0],p[1],p[2],role) for role,nodes in expected.items()
               if role is not None for p in nodes}
    actual={(v['node']['state'],v['node']['phase'],v['node']['unallocated_wei'],v['unavailable_role'])
            for v in got['liveness']['single_role_traps']}
    assert ref_traps==actual
    assert len(ref_traps)>0
