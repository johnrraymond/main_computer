"""Independent global-clock semantic interpreter for governed v3 contract tests.

This deliberately does not import the production transition model or reuse its
phase-relative clocks. It receives elapsed time deltas and executes actions by
separate conditions, providing a differential oracle for the model and Solidity
trace tests. It is still author-written, not independently certified gold.
"""
from __future__ import annotations
from tools import nanojev_contract_v3_governance_language as lang

class Rejected(ValueError): pass

class Protocol:
    def __init__(self,raw):
        self.spec=lang.validate(raw)
        self.now=0;self.state='active';self.milestone=0
        self.due=self.spec['milestones'][0]['deadline_seconds'];self.extensions=0
        self.submitted=None;self.rejected=None;self.disputed=None
        self.candidate=None;self.appointee=None
        self.left=self.spec['amount_wei']
        self.owed={'payer':0,'worker':0};self.withdrawn={'payer':0,'worker':0}

    def warp(self,seconds):
        if type(seconds) is not int or seconds<0:raise Rejected('time cannot reverse')
        self.now+=seconds

    def credit(self,beneficiary,n):
        if n<1 or n>self.left:raise Rejected('over allocation')
        self.left-=n;self.owed[beneficiary]+=n

    def refund(self):
        self.credit('payer',self.left)
        self.state='refunded'

    def earn(self):
        self.credit('worker',self.spec['milestones'][self.milestone]['amount_wei'])
        if self.milestone==len(self.spec['milestones'])-1:
            if self.left!=0:raise Rejected('residual at completion')
            self.state='completed'
        else:
            self.milestone+=1;self.state='active'
            self.due=self.now+self.spec['milestones'][self.milestone]['deadline_seconds']
            self.extensions=0;self.submitted=None;self.rejected=None;self.disputed=None
            self.candidate=None;self.appointee=None

    def outcome(self,key):
        choice=self.spec['policy'][key]
        if choice=='pay_worker':self.earn()
        elif choice=='refund_payer':self.refund()
        else:raise Rejected('unknown policy')

    def invoke(self,operation,role,nominee='candidate_1'):
        p=self.spec['policy'];m=self.spec['milestones'][self.milestone]
        t=self.now;z=self.state
        def require(test):
            if not test:raise Rejected(f'{operation} illegal at {z}/{t} caller {role}')
        def authorized(who):require(role==who)
        if operation=='submit':
            authorized('worker');require(z=='active' and t<=self.due)
            self.state='submitted';self.submitted=t
        elif operation=='grant_extension':
            authorized('payer');require(z=='active' and self.extensions<m['max_extensions'])
            require(self.due<=t<=self.due+m['extension_decision_seconds'])
            self.extensions+=1;self.due+=m['extension_seconds']
        elif operation=='resolve_missed_deadline':
            require(z=='active' and t>self.due+m['extension_decision_seconds'])
            self.outcome('on_missed_submission')
        elif operation=='approve':
            authorized('payer');require(z=='submitted' and t<=self.submitted+p['review_seconds'])
            self.earn()
        elif operation=='reject':
            authorized('payer');require(z=='submitted' and t<=self.submitted+p['review_seconds'])
            self.state='rejected';self.rejected=t
        elif operation=='resolve_review_silence':
            require(z=='submitted' and t>self.submitted+p['review_seconds'])
            self.outcome('on_review_silence')
        elif operation=='file_dispute':
            authorized('worker');require(z=='rejected' and t<=self.rejected+p['dispute_filing_seconds'])
            self.state='disputed';self.disputed=t;self.candidate=None;self.appointee=None
        elif operation=='resolve_undisputed':
            require(z=='rejected' and t>self.rejected+p['dispute_filing_seconds'])
            self.outcome('on_undisputed_rejection')
        elif operation=='nominate_arbitrator':
            authorized('payer');require(z=='disputed' and t<=self.disputed+p['appointment_seconds'])
            require(self.candidate is None and nominee not in ('payer','worker',''))
            self.candidate=nominee
        elif operation=='confirm_arbitrator':
            authorized('worker');require(z=='disputed' and t<=self.disputed+p['appointment_seconds'])
            require(self.candidate is not None and nominee==self.candidate and self.appointee is None)
            self.appointee=nominee
        elif operation=='award_worker':
            authorized('arbitrator');require(z=='disputed' and self.appointee is not None)
            require(t<=self.disputed+p['ruling_seconds']);self.earn()
        elif operation=='award_payer':
            authorized('arbitrator');require(z=='disputed' and self.appointee is not None)
            require(t<=self.disputed+p['ruling_seconds']);self.refund()
        elif operation=='resolve_unappointed':
            require(z=='disputed' and self.appointee is None)
            require(t>self.disputed+p['appointment_seconds'])
            self.outcome('on_appointment_failure')
        elif operation=='resolve_arbitrator_silence':
            require(z=='disputed' and self.appointee is not None)
            require(t>self.disputed+p['ruling_seconds'])
            self.outcome('on_arbitrator_silence')
        else:raise Rejected('unknown action')
        self.assert_conserved()

    def withdraw(self,role,accepts=True):
        if not accepts or self.owed.get(role,0)<=0:raise Rejected('recipient rejection or no credit')
        n=self.owed[role];self.owed[role]=0;self.withdrawn[role]+=n
        self.assert_conserved();return n

    def assert_conserved(self):
        assert self.left+sum(self.owed.values())+sum(self.withdrawn.values())==self.spec['amount_wei']
