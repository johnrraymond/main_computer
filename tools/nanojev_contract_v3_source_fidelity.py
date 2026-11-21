#!/usr/bin/env python3
"""Source-grounded, limited-scope V3 fidelity checks; never a language oracle.

This module detects only *demonstrable* contradictions and extra authority
clauses in an explicit governed-escrow source grammar, and compares extracted
literal values with an untrusted candidate IR. It cannot establish that every
meaningful clause was understood. It never rewrites the source or candidate.

No model calls, no network, no Solidity, no compiler mutation.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any

NUMBER = r'(\d+(?:\.\d+)?)'
CURRENCY = r'(ETH|wei)'
DURATION = r'(days?|weeks?|hours?|minutes?|seconds?)'
NAME = r'([a-z][a-z0-9_]*(?:\s+[a-z][a-z0-9_]*){0,3})'
SCALE = {'eth':10**18,'wei':1,'day':86400,'week':604800,'hour':3600,
         'minute':60,'second':1}

MONEY_ROOT = [
    re.compile(r'^the\s+payer\s+(?:escrows|deposits)\s+'+NUMBER+r'\s*'+CURRENCY+r'\b',re.I),
    re.compile(r'^the\s+total\s+escrow\s+must\s+equal\s+'+NUMBER+r'\s*'+CURRENCY+r'\b',re.I),
    re.compile(r'^the\s+total\s+(?:payer\s+)?escrow\s+(?:shall|must)\s+be\s+'+NUMBER+r'\s*'+CURRENCY+r'\b',re.I),
    re.compile(r'^for\s+the\s+worker,?\s+the\s+payer\s+will\s+escrow\s+'+NUMBER+r'\s*'+CURRENCY+r'\b',re.I),
]
AMOUNT = re.compile(r'^the\s+'+NAME+r'\s+milestone\s+(?:is\s+worth|pays)\s+'+NUMBER+r'\s*'+CURRENCY+r'\b',re.I)
DEADLINE = re.compile(r'^the\s+'+NAME+r'\s+(?:deadline\s+is\s+|delivery\s+is\s+due\s+within\s+)'+NUMBER+r'\s*'+DURATION+r'\b',re.I)
EXTENSION = re.compile(r'^the\s+'+NAME+r'\s+extension\s+(?:adds\s+|is\s+)'+NUMBER+r'\s*'+DURATION+r'\b',re.I)
EXTENSION_DECISION = re.compile(r'^the\s+'+NAME+r'\s+extension\s+decision\s+window\s+(?:is|lasts)\s+'+NUMBER+r'\s*'+DURATION+r'\b',re.I)
MAX_EXTENSION = [
    re.compile(r'^the\s+'+NAME+r'\s+permits\s+a\s+maximum\s+of\s+([0-9]+)\s+extensions?\b',re.I),
    re.compile(r'^the\s+'+NAME+r'\s+extension\s+limit\s+is\s+([0-9]+)\s+extensions?\b',re.I),
]
WINDOW = re.compile(r'^the\s+(review|dispute\s+filing|appointment|ruling)\s+window\s+(?:is|lasts)\s+'+NUMBER+r'\s*'+DURATION+r'\b',re.I)
POLICY = re.compile(r'^(?:on\s+|if\s+there\s+is\s+|for\s+)(review\s+silence|appointment\s+failure|arbitrator\s+silence|missed\s+submission|undisputed\s+rejection)\s*,\s*(pay\s+(?:the\s+)?worker|refund\s+(?:the\s+)?payer)\b',re.I)
AUTHORITY_ACTOR = re.compile(r'\b(?:payer|worker|arbitrator|observer|administrator|reviewer|fourth[- ]party|third[- ]party|beneficiary|agent|delegate|approver)\b',re.I)
AUTHORITY_ACTION = re.compile(r'\b(?:approve\w*|appoint\w*|release\w*|extend\w*|extension|overrule\w*|veto\w*|disburse\w*)\b',re.I)
AUTHORITY_CHANGE = re.compile(r'\b(?:may|can|must|shall|solely|alone|unilaterally|without|prohibit\w*|exclusively|authorized|power|right)\b',re.I)
ANNOTATION = re.compile(r'^\s*(?:UNTRUSTED|INJECTION)\s+TEST\s+ANNOTATION\s*:',re.I)

WINDOW_KEYS = {'review':'review_seconds','dispute filing':'dispute_filing_seconds',
               'appointment':'appointment_seconds','ruling':'ruling_seconds'}
POLICY_KEYS = {'review silence':'on_review_silence',
               'appointment failure':'on_appointment_failure',
               'arbitrator silence':'on_arbitrator_silence',
               'missed submission':'on_missed_submission',
               'undisputed rejection':'on_undisputed_rejection'}
MAX_SOURCE=16000


def _canonical_name(name:str)->str:
    return '_'.join(name.lower().split())


def _number(token:str, unit:str)->int|None:
    try: value=Decimal(token)*SCALE[unit.lower().rstrip('s')]
    except (InvalidOperation,KeyError):return None
    if value < 0 or value!=value.to_integral_value() or value>2**256-1:return None
    return int(value)


def _obs(path:str,value:Any,line:int,quote:str)->dict:
    return {'path':path,'value':value,'line':line,'source_sha256':hashlib.sha256(quote.encode()).hexdigest()}


def audit_source(source:str)->dict:
    """Check definite source conflicts and explicit non-V3 authority clauses.

    Unknown or unparsed lines are *not* silently claimed to be verified. Source
    line hashes and short literal quotes provide human-auditable evidence.
    """
    if not isinstance(source,str) or not source.strip() or len(source.encode())>MAX_SOURCE:
        raise ValueError('Source is empty or exceeds 16000 UTF-8 bytes')
    observed=[]
    issues=[]
    unrecognized=[]
    for line,raw in enumerate(source.splitlines(),1):
        quote=raw.strip()
        if not quote:continue
        if ANNOTATION.match(quote):continue
        k=quote.lower()
        recognized=False
        for pat in MONEY_ROOT:
            m=pat.match(quote)
            if m:
                value=_number(m.group(1),m.group(2))
                if value is not None:observed.append(_obs('amount_wei',value,line,quote))
                recognized=True
                break
        if recognized:continue
        m=AMOUNT.match(quote)
        if m:
            value=_number(m.group(2),m.group(3))
            if value is not None:observed.append(_obs('milestones.'+_canonical_name(m.group(1))+'.amount_wei',value,line,quote))
            continue
        m=EXTENSION_DECISION.match(quote)
        if m:
            value=_number(m.group(2),m.group(3))
            if value is not None:observed.append(_obs('milestones.'+_canonical_name(m.group(1))+'.extension_decision_seconds',value,line,quote))
            continue
        m=DEADLINE.match(quote)
        if m:
            value=_number(m.group(2),m.group(3))
            if value is not None:observed.append(_obs('milestones.'+_canonical_name(m.group(1))+'.deadline_seconds',value,line,quote))
            continue
        m=EXTENSION.match(quote)
        if m:
            value=_number(m.group(2),m.group(3))
            if value is not None:observed.append(_obs('milestones.'+_canonical_name(m.group(1))+'.extension_seconds',value,line,quote))
            continue
        if any((m:=pat.match(quote)) for pat in MAX_EXTENSION):
            observed.append(_obs('milestones.'+_canonical_name(m.group(1))+'.max_extensions',int(m.group(2)),line,quote))
            continue
        m=WINDOW.match(quote)
        if m:
            value=_number(m.group(2),m.group(3))
            if value is not None:observed.append(_obs('policy.'+WINDOW_KEYS[' '.join(m.group(1).lower().split())],value,line,quote))
            continue
        m=POLICY.match(quote)
        if m:
            path='policy.'+POLICY_KEYS[' '.join(m.group(1).lower().split())]
            value='pay_worker' if m.group(2).lower().startswith('pay') else 'refund_payer'
            observed.append(_obs(path,value,line,quote))
            continue
        if AUTHORITY_ACTOR.search(quote) and AUTHORITY_ACTION.search(quote) and AUTHORITY_CHANGE.search(quote):
            issues.append({'code':'EXPLICIT_AUTHORITY_OUTSIDE_V3','lines':[line],
                           'path':None,'detail':'Explicit authority/power modification is not a representable V3 IR field',
                           'source':[quote[:350]]})
        else:
            unrecognized.append({'line':line,'source_sha256':hashlib.sha256(quote.encode()).hexdigest()})
    by_path=defaultdict(list)
    for x in observed:by_path[x['path']].append(x)
    for path,entries in sorted(by_path.items()):
        differing={json.dumps(x['value']) for x in entries}
        if len(differing)>1:
            issues.append({'code':'CONTRADICTORY_EXPLICIT_VALUES','lines':[x['line'] for x in entries],
                           'path':path,'detail':'More than one binding explicit source value for the same field',
                           'values':[{'line':x['line'],'value':x['value']} for x in entries],
                           'source':[]})
    return {'schema':'nanojev.v3.source_fidelity.v1',
            'status':'BLOCK_SOURCE_CONFLICT_OR_UNREPRESENTABLE_AUTHORITY' if issues else 'NO_DEMONSTRABLE_SOURCE_CONFLICT',
            'issues':issues,'observations':observed,
            'unparsed_lines':unrecognized,'recognized_values':len(observed),
            'scope':'bounded explicit governed-escrow syntax; NOT full semantic verification'}


def _get(spec:dict,path:str):
    parts=path.split('.')
    if parts[0]=='amount_wei':return spec.get('amount_wei')
    if parts[0]=='policy':return spec.get('policy',{}).get(parts[1])
    if parts[0]=='milestones':
        milestones=spec.get('milestones',[])
        for item in milestones:
            if isinstance(item,dict) and item.get('id')==parts[1]:return item.get(parts[2])
    return None


def audit_candidate(source:str,spec:dict,*,source_audit:dict|None=None)->dict:
    """Confirm candidate agrees with recognized explicit source fields.

    Both source and IR are untrusted. A conflict blocks without selecting a
    preferred source value. No repair or source-derived IR construction occurs.
    """
    initial=source_audit if source_audit is not None else audit_source(source)
    issues=list(initial['issues'])
    if not isinstance(spec,dict):raise ValueError('Candidate must be an object')
    # Do not repeat conflict errors as candidate-value mismatches.
    contested={x['path'] for x in issues if x['code']=='CONTRADICTORY_EXPLICIT_VALUES'}
    for item in initial['observations']:
        if item['path'] in contested:continue
        actual=_get(spec,item['path'])
        if type(actual)!=type(item['value']) or actual!=item['value']:
            issues.append({'code':'CANDIDATE_SOURCE_VALUE_MISMATCH','path':item['path'],
                           'lines':[item['line']],'expected_source_value':item['value'],
                           'candidate_value':actual,'detail':'Candidate diverges from an explicit source value'})
    return {'schema':'nanojev.v3.source_fidelity.v1',
            'status':'BLOCK_UNSUPPORTED_OR_MISMATCH' if issues else 'PASS_RECOGNIZED_FIELD_CHECK_NOT_SEMANTIC_PROOF',
            'issues':issues,'recognized_values':initial['recognized_values'],
            'unparsed_lines':initial['unparsed_lines'],
            'scope':initial['scope']}
