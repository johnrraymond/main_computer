"""Bounded timed operational model for v3 governed progressive escrow.

Logical actors payer, worker, appointed arbitrator, or anyone (permissionless).
Concrete candidate addresses are enforced by Solidity, abstracted here to two
consent flags; no oracle/evidence-of-work or autonomous transaction guarantees.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from tools import nanojev_contract_v3_governance_language as g
from tools import nanojev_contract_v3_language as v3

@dataclass(frozen=True)
class Node:
    phase: str = 'active'
    index: int = 0
    now: int = 0
    since: int = 0
    due: int = 0
    extensions: int = 0
    submitted_at: int = 0
    rejected_at: int = 0
    disputed_at: int = 0
    nominated: bool = False
    appointed: bool = False
    unallocated: int = 0
    pending: tuple[int,int,int] = (0,0,0)
    withdrawn: tuple[int,int,int] = (0,0,0)

    def report(self):
        return {'phase':self.phase,'milestone_index':self.index,'now':self.now,
                'due':self.due,'extensions':self.extensions,'nominated':self.nominated,
                'appointed':self.appointed,'unallocated_wei':self.unallocated,
                'pending_wei':dict(zip(v3.ROLES,self.pending)),
                'withdrawn_wei':dict(zip(v3.ROLES,self.withdrawn))}


def initial(s):
    return Node(due=s['milestones'][0]['deadline_seconds'],unallocated=s['amount_wei'])


def terminal(n):
    return n.phase in ('completed','refunded')


def _policy(s,n,choice):
    if choice=='refund_payer':
        u,p=g.allocate(n.unallocated,n.pending,'payer','remaining')
        return replace(n,phase='refunded',now=0,due=0,unallocated=u,pending=p)
    if choice=='pay_worker':
        return _award(s,n)
    raise v3.V3Error('model','Unsupported policy outcome')


def _award(s,n):
    m=s['milestones'][n.index]
    unallocated,pending=g.allocate(n.unallocated,n.pending,'worker',m['amount_wei'])
    if n.index+1==len(s['milestones']):
        if unallocated:
            raise v3.V3Error('accounting','Terminal milestone leaves residual escrow')
        return replace(n,phase='completed',now=0,due=0,unallocated=unallocated,pending=pending)
    i=n.index+1
    return replace(n,phase='active',index=i,now=0,since=0,
                   due=s['milestones'][i]['deadline_seconds'],
                   extensions=0,submitted_at=0,rejected_at=0,disputed_at=0,
                   nominated=False,appointed=False,unallocated=unallocated,pending=pending)


def available(s,n):
    """List (action,actor) pairs. Permissionless actions require any caller."""
    if terminal(n): return []
    p=s['policy']; m=s['milestones'][n.index]; t=n.now
    if n.phase=='active':
        out=[]
        if t <= n.due: out.append(('submit','worker'))
        if n.extensions<m['max_extensions'] and n.due<=t<=n.due+m['extension_decision_seconds']:
            out.append(('grant_extension','payer'))
        if t>n.due+m['extension_decision_seconds']:
            out.append(('resolve_missed_deadline','anyone'))
        return out
    if n.phase=='submitted':
        return ([('approve','payer'),('reject','payer')]
                if t<=n.submitted_at+p['review_seconds']
                else [('resolve_review_silence','anyone')])
    if n.phase=='rejected':
        return ([('file_dispute','worker')] if t<=n.rejected_at+p['dispute_filing_seconds']
                else [('resolve_undisputed','anyone')])
    if n.phase=='disputed':
        appointment_end=n.disputed_at+p['appointment_seconds']
        ruling_end=n.disputed_at+p['ruling_seconds']
        if not n.appointed:
            out=[]
            if t<=appointment_end:
                if not n.nominated: out.append(('nominate_arbitrator','payer'))
                if n.nominated: out.append(('confirm_arbitrator','worker'))
            else:
                out.append(('resolve_unappointed','anyone'))
            return out
        if t<=ruling_end:
            return [('award_worker','arbitrator'),('award_payer','arbitrator')]
        return [('resolve_arbitrator_silence','anyone')]
    raise v3.V3Error('model','Unknown phase')


def execute(s,n,action,actor):
    allowed=dict(available(s,n))
    if action not in allowed or (actor != allowed[action] and allowed[action] != 'anyone'):
        raise v3.V3Error('authority','Action unavailable or unauthorized')
    p=s['policy'];m=s['milestones'][n.index]
    if action=='submit': return replace(n,phase='submitted',now=0,due=0,extensions=0,submitted_at=0)
    if action=='grant_extension':
        return replace(n,extensions=n.extensions+1,due=n.due+m['extension_seconds'])
    if action=='resolve_missed_deadline': return _policy(s,n,p['on_missed_submission'])
    if action=='approve': return _award(s,n)
    if action=='reject': return replace(n,phase='rejected',now=0,rejected_at=0)
    if action=='resolve_review_silence': return _policy(s,n,p['on_review_silence'])
    if action=='file_dispute':
        return replace(n,phase='disputed',now=0,disputed_at=0,nominated=False,appointed=False)
    if action=='resolve_undisputed':return _policy(s,n,p['on_undisputed_rejection'])
    if action=='nominate_arbitrator':return replace(n,nominated=True)
    if action=='confirm_arbitrator':return replace(n,appointed=True)
    if action=='award_worker':return _award(s,n)
    if action=='award_payer':return _policy(s,n,'refund_payer')
    if action=='resolve_unappointed':return _policy(s,n,p['on_appointment_failure'])
    if action=='resolve_arbitrator_silence':return _policy(s,n,p['on_arbitrator_silence'])
    raise v3.V3Error('model','Unknown action')


def time_targets(s,n):
    if terminal(n):return []
    p=s['policy'];m=s['milestones'][n.index]
    if n.phase=='active':bounds=[n.due,n.due+m['extension_decision_seconds']]
    elif n.phase=='submitted':bounds=[n.submitted_at+p['review_seconds']]
    elif n.phase=='rejected':bounds=[n.rejected_at+p['dispute_filing_seconds']]
    else:bounds=[n.disputed_at+p['appointment_seconds'],n.disputed_at+p['ruling_seconds']]
    return sorted({t for b in bounds for t in (b-1,b,b+1) if n.now<t})


def successors(s,n,unavailable=None,withdrawals=True):
    for t in time_targets(s,n):
        yield f'warp:{t}',replace(n,now=t)
    for action,actor in available(s,n):
        if actor!='anyone' and actor==unavailable:continue
        try:
            yield action,execute(s,n,action,actor)
        except v3.V3Error:
            pass
    if withdrawals:
        for i,role in enumerate(v3.ROLES):
            if n.pending[i] and role!=unavailable:
                pend=list(n.pending);paid=list(n.withdrawn)
                paid[i]+=pend[i];pend[i]=0
                yield f'withdraw:{role}',replace(n,pending=tuple(pend),withdrawn=tuple(paid))
