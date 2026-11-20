"""Executable bounded timed governance / accounting / single-actor recovery audit.

Relative clocks are reset on phase and milestone boundaries. Boundary-interior
representatives (threshold - 1, threshold, threshold + 1) are explored.
This is NOT a complete proof over all Solidity timestamps or arbitrary messages.
"""
from __future__ import annotations
from collections import deque
from tools import nanojev_contract_v3_governance_language as lang
from tools import nanojev_contract_v3_governance_model as model
from tools import nanojev_contract_v3_language as v3

MAX_NODES=45000

def _recover(s,start,missing):
    if model.terminal(start) and start.unallocated==0:return []
    queue=deque([(start,[])]);seen={start}
    while queue:
        node,path=queue.popleft()
        for name, child in model.successors(s,node,missing,withdrawals=False):
            if model.terminal(child) and child.unallocated==0:return path+[name]
            if child not in seen:
                seen.add(child);queue.append((child,path+[name]))
    return None

def verify(raw):
    s=lang.validate(raw);root=model.initial(s);visited={root:[]};queue=deque([root]);
    accounting=[];recovery_fails=[];witness=[];used=set();authorities=[]
    while queue:
        node=queue.popleft();trace=visited[node]
        if node.unallocated+sum(node.pending)+sum(node.withdrawn)!=s['amount_wei']:
            accounting.append({'kind':'CONSERVATION','node':node.report(),'trace':trace})
        if model.terminal(node) and node.unallocated!=0:
            accounting.append({'kind':'TERMINAL_RESIDUAL','node':node.report(),'trace':trace})
        for name,actor in model.available(s,node):
            used.add(name);authorities.append((name,actor))
        for name,child in model.successors(s,node):
            if child not in visited:
                if len(visited)>=MAX_NODES:
                    raise v3.V3Error('graph','Bound exhausted. Refuse to authorize Solidity')
                visited[child]=trace+[name];queue.append(child)
    unfinished=[(node,trace) for node,trace in visited.items() if not model.terminal(node)]
    for node,trace in unfinished:
        for absent in (None,'payer','worker','arbitrator'):
            route=_recover(s,node,absent)
            if route is None:
                recovery_fails.append({'node':node.report(),'unavailable':absent,'trace':trace})
            elif len(witness)<30:
                witness.append({'phase':node.phase,'milestone':node.index,
                                'unavailable':absent,'recovery_actions':route})
    # Nomination and decisions are intentional actor-authorized branches.
    # Refunding and awarding have opposite effects, but only appointed arb may
    # select them before expiry; do not misclassify judicial choice as a race.
    blockers=[]
    if accounting:blockers.append('ACCOUNTING_INVARIANT_BROKEN')
    if recovery_fails:blockers.append('SINGLE_ACTOR_NONRESPONSE_TRAP')
    expected={'submit','approve','reject','resolve_missed_deadline','resolve_review_silence',
              'file_dispute','resolve_undisputed','nominate_arbitrator',
              'confirm_arbitrator','award_worker','award_payer',
              'resolve_unappointed','resolve_arbitrator_silence'}
    if any(m['max_extensions'] for m in s['milestones']):expected.add('grant_extension')
    missing=sorted(expected-used)
    if missing:blockers.append('UNEXERCISABLE_TYPED_OPERATION')
    return {'schema':'nanojev.contract.v3.governance_graph.v1',
            'status':'PASS_CONDITIONAL' if not blockers else 'BLOCKED_BY_GOVERNANCE_GRAPH',
            'emission_authorized_by_graph_audit':not blockers,
            'source':'VALIDATED_GOVERNANCE_IR_NOT_ENGLISH',
            'reachable_nodes':len(visited),'unsettled_nodes':len(unfinished),
            'accounting_violations':accounting[:20],
            'authorization':sorted([{'action':a,'actor':r} for a,r in set(authorities)],key=lambda x:x['action']),
            'liveness':{'policy':'EXISTS_TERMINAL_ALLOCATION_PATH_AFTER_ONE_UNAVAILABLE_ROLE',
                        'checked_scenarios':len(unfinished)*4,
                        'traps':recovery_fails[:20],'recovery_witnesses':witness},
            'model_limits':['INTEGER_SECOND_BOUNDARY_REPRESENTATIVES_NOT_ALL_TIMESTAMPS',
                            'PHASE_CLOCK_RESET_ABSTRACTION','AT_MOST_ONE_UNAVAILABLE_ROLE',
                            'PERMISSIONLESS_CALL_NEEDS_REAL_TRANSACTION','EXTERNAL_EVIDENCE_NOT_VERIFIED',
                            'ENGLISH_TO_IR_NOT_CERTIFIED','SOLIDITY_EQUIVALENCE_NOT_PROVED'],
            'unexercisable':missing,'blockers':blockers}
