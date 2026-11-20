"""Versioned Contract Language v3 governance/milestone extension.

Pure, strict, bounded semantics. Accounting credits delegate to unchanged v3
accounting primitives. The four timeout outcomes are AUTHOR-DEFINED policies,
never synthesized by the compiler. External evidence of work is NOT verified.
"""
from __future__ import annotations
import re
from tools import nanojev_contract_v3_language as v3

LANG = 'nanojev.contract.governed_escrow.v3'
IDENT = re.compile(r'[a-z][a-z0-9_]{0,31}\Z')
POLICIES = ('pay_worker', 'refund_payer')
ROOT = {
    'language': LANG, 'asset': 'native_eth', 'settlement_mode': 'pull',
    'payer': 'deployer', 'worker': 'constructor_worker',
    'arbitrator': 'jointly_appointed_after_dispute',
}
POLICY_KEYS = {
    'review_seconds', 'dispute_filing_seconds', 'appointment_seconds',
    'ruling_seconds', 'on_review_silence', 'on_appointment_failure',
    'on_arbitrator_silence', 'on_missed_submission', 'on_undisputed_rejection',
}
MILESTONE_KEYS = {'id', 'amount_wei', 'deadline_seconds', 'extension_seconds',
                  'extension_decision_seconds', 'max_extensions'}


def _pos_int(value, lo, hi, label):
    if type(value) is not int or not lo <= value <= hi:
        raise v3.V3Error('schema', f'{label} outside integer bounds {lo}..{hi}')
    return value


def validate(raw):
    if not isinstance(raw, dict) or set(raw) != set(ROOT) | {'amount_wei','milestones','policy'}:
        raise v3.V3Error('schema','Exact governed escrow fields required')
    for key,val in ROOT.items():
        if raw.get(key) != val:
            raise v3.V3Error('schema',f'Incorrect v3 governance authority or field {key}')
    total = _pos_int(raw['amount_wei'],1,2**128-1,'deposit_wei')
    ms = raw['milestones']
    if not isinstance(ms,list) or not 1 <= len(ms) <= 5:
        raise v3.V3Error('schema','Expected 1..5 independent milestone obligations')
    names=set(); subtotal=0; normalized=[]
    for m in ms:
        if not isinstance(m,dict) or set(m) != MILESTONE_KEYS:
            raise v3.V3Error('schema','Milestone requires exact amount, deadlines, and extension limits')
        ident=m['id']
        if not isinstance(ident,str) or not IDENT.fullmatch(ident) or ident in names:
            raise v3.V3Error('schema','Duplicate/invalid milestone identifier')
        names.add(ident)
        amount=_pos_int(m['amount_wei'],1,total,'milestone amount')
        subtotal+=amount
        _pos_int(m['deadline_seconds'],3600,365*86400,'deadline_seconds')
        _pos_int(m['extension_seconds'],3600,30*86400,'extension_seconds')
        _pos_int(m['extension_decision_seconds'],60,7*86400,'extension_decision_seconds')
        _pos_int(m['max_extensions'],0,2,'max_extensions')
        if m['extension_decision_seconds'] >= m['extension_seconds']:
            raise v3.V3Error('schema','Extension must exceed extension decision window')
        normalized.append(dict(m))
    if subtotal != total:
        raise v3.V3Error('accounting','All milestone obligations must exactly cover deposit')
    policy=raw['policy']
    if not isinstance(policy,dict) or set(policy) != POLICY_KEYS:
        raise v3.V3Error('schema','All governance and timeout policies must be specified')
    for name in ('review_seconds','dispute_filing_seconds','appointment_seconds','ruling_seconds'):
        _pos_int(policy[name],3600,30*86400,name)
    if policy['appointment_seconds'] >= policy['ruling_seconds']:
        raise v3.V3Error('schema','Arbitrator appointment window must end before ruling window')
    for key in ('on_review_silence','on_appointment_failure','on_arbitrator_silence'):
        if policy[key] not in POLICIES:
            raise v3.V3Error('schema',f'Explicit award/refund fallback required for {key}')
    for key in ('on_missed_submission','on_undisputed_rejection'):
        if policy[key] != 'refund_payer':
            raise v3.V3Error('schema',f'{key} must explicitly specify refund_payer in this extension')
    return {**ROOT,'amount_wei':total,'milestones':normalized,'policy':dict(policy)}


def allocate(unallocated, outstanding, target, amount):
    """Use the original verified v3 integer-wei crediting primitive verbatim."""
    return v3.apply_credits(unallocated, outstanding,
                            [{'to':target,'amount_wei':amount}])
