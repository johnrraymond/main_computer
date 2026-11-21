"""Independent, fail-closed source/IR fidelity gate for the V3 model head.

This is a safety and observability tool, NOT proof of legal semantics. It never
supplies missing values, rewrites model JSON, or changes the frozen V3 compiler.
Ledger and reviewer come from model inference and are never trusted as gold.
"""
from __future__ import annotations

from collections import defaultdict
import re

from tools import nanojev_contract_v3_live_head as bridge
from tools import nanojev_contract_v3_governance_language as gov

ROOT_ROLES = {'payer','worker','arbitrator','asset','settlement_mode'}
FIELD_SET = {'amount_wei'} | {'policy.'+k for k in gov.POLICY_KEYS}
MILESTONE_FIELDS = set(gov.MILESTONE_KEYS) - {'id'}
PATH_PATTERN = re.compile(r'milestones\.([a-z][a-z0-9_]*)\.([a-z_]+)\Z')
# Over-approximate: false-positive blocking is preferable to ignoring a clause.
MATERIAL = re.compile(r'\b(?:escrow|payer|worker|arbitrat\w*|milestone|deadline|due|'
                      r'extensions?|extend|appoint\w*|review|dispute|ruling|refund|'
                      r'payment|approve|approval|release|funds|funding|consent|'
                      r'authorize|settle|submission|rejection|silence|failure|'
                      r'cancel|beneficiar\w*|deliver\w*)\b',re.I)
INJECTION_HINT = re.compile(r'^(?:UNTRUSTED\s+TEST\s+ANNOTATION|INJECTION\s+TEST\s+ANNOTATION)\s*:',re.I)


class SemanticGuardError(Exception):
    def __init__(self, code:str, detail:str, *, line:int|None=None):
        super().__init__(f'{code}: {detail}')
        self.code=code; self.detail=detail; self.line=line


def source_lines(request:str)->dict[int,str]:
    return {i:line.strip() for i,line in enumerate(request.splitlines(),1) if line.strip()}


def is_field_path(value:str)->bool:
    if value in FIELD_SET or value in ROOT_ROLES:
        return True
    m=PATH_PATTERN.fullmatch(value)
    return bool(m and m.group(2) in MILESTONE_FIELDS)


def validate_ledger(request:str, data:dict)->dict[int,dict]:
    """Require verbatim, one-to-one accounting for EVERY nonblank input line."""
    source=source_lines(request)
    if not isinstance(data,dict) or set(data)!={'schema','lines'} or data.get('schema')!='nanojev.v3.source_ledger.v1':
        raise SemanticGuardError('LEDGER_SCHEMA','Expected strict versioned source ledger')
    claims=data.get('lines')
    if not isinstance(claims,list) or len(claims)!=len(source):
        raise SemanticGuardError('LEDGER_COVERAGE','Ledger omitted or duplicated source lines')
    result={}
    for item in claims:
        if not isinstance(item,dict) or set(item)!={'line','quote','kind','paths','issue'}:
            raise SemanticGuardError('LEDGER_SCHEMA','Each line requires line/quote/kind/paths/issue')
        number=item['line'];kind=item['kind'];paths=item['paths'];issue=item['issue']
        if type(number)!=int or number not in source or number in result:
            raise SemanticGuardError('LEDGER_COVERAGE','Invalid/duplicate source line number')
        if item['quote']!=source[number]:
            raise SemanticGuardError('LEDGER_SOURCE','Nonliteral source quotation',line=number)
        if kind not in ('term','conflict','unsupported','instruction_injection','context'):
            raise SemanticGuardError('LEDGER_SCHEMA','Invalid clause type',line=number)
        if not isinstance(paths,list) or len(paths)>8 or len(paths)!=len(set(map(str,paths))) or not all(isinstance(p,str) and is_field_path(p) for p in paths):
            raise SemanticGuardError('LEDGER_SCHEMA','Unknown or duplicate path',line=number)
        if not isinstance(issue,str) or len(issue)>300:
            raise SemanticGuardError('LEDGER_SCHEMA','Issue must be bounded text',line=number)
        if kind in ('conflict','unsupported') and not issue.strip():
            raise SemanticGuardError('LEDGER_SCHEMA','Problematic clause must identify the issue',line=number)
        if kind=='term' and not paths:
            raise SemanticGuardError('UNMAPPED_MATERIAL','Term has no IR path',line=number)
        if kind=='context' and MATERIAL.search(source[number]):
            raise SemanticGuardError('UNMAPPED_MATERIAL','Potentially material source misclassified as context',line=number)
        if kind=='instruction_injection' and not INJECTION_HINT.search(source[number]):
            raise SemanticGuardError('UNVERIFIED_INJECTION','Instruction exclusion lacks explicit test marker',line=number)
        if INJECTION_HINT.search(source[number]) and kind!='instruction_injection':
            raise SemanticGuardError('INJECTION_UNACCOUNTED','Embedded instruction was not isolated',line=number)
        if kind=='term' and not MATERIAL.search(source[number]):
            raise SemanticGuardError('LEDGER_SCHEMA','Term classification has no apparent contractual subject',line=number)
        result[number]=item
    return result


def validate_review(ledger:dict[int,dict], data:dict)->dict:
    if not isinstance(data,dict) or set(data)!={'schema','verdict','covered_lines','issues'} or data.get('schema')!='nanojev.v3.source_review.v1':
        raise SemanticGuardError('REVIEW_SCHEMA','Expected strict versioned review')
    verdict=data['verdict'];coverage=data['covered_lines'];issues=data['issues']
    if verdict not in ('proceed','clarify') or not isinstance(coverage,list) or not isinstance(issues,list):
        raise SemanticGuardError('REVIEW_SCHEMA','Invalid review types')
    if len(coverage)!=len(ledger) or set(coverage)!=set(ledger) or any(type(i)!=int for i in coverage):
        raise SemanticGuardError('REVIEW_COVERAGE','Review does not account for all source lines')
    if len(issues)>30:
        raise SemanticGuardError('REVIEW_SCHEMA','Too many review issues')
    for item in issues:
        if not isinstance(item,dict) or set(item)!={'line','category','reason'}:
            raise SemanticGuardError('REVIEW_SCHEMA','Invalid issue')
        if type(item['line'])!=int or item['line'] not in ledger or item['category'] not in ('missing','contradiction','unsupported','ambiguous','unrepresentable','injection') or not isinstance(item['reason'],str) or not 2<=len(item['reason'])<=300:
            raise SemanticGuardError('REVIEW_SCHEMA','Invalid issue details')
    ledger_issues=[i for i,entry in ledger.items() if entry['kind'] in ('unsupported','conflict')]
    if (verdict=='proceed' and issues) or (verdict=='clarify' and not issues):
        raise SemanticGuardError('REVIEW_INCONSISTENT','Verdict and issues disagree')
    if verdict=='proceed' and ledger_issues:
        raise SemanticGuardError('REVIEW_DISAGREEMENT','Reviewer overlooked a flagged source clause')
    return data


def check_coverage(request:str, ledger:dict[int,dict], spec:dict, spans:dict)->dict:
    """Ensure EACH declared material mapping is present in verified model evidence.

    This catches omitted extra authority/contradiction clauses even when model IR
    passes V3 graph verification. It deliberately does not infer corrected terms.
    """
    expected=bridge._expected_evidence(spec)
    if not isinstance(spans,dict):
        raise SemanticGuardError('IR_EVIDENCE','Model failed to supply per-field quotes')
    source=source_lines(request)
    source_line_by_field=defaultdict(set)
    for path,quote in spans.items():
        if path not in expected or not isinstance(quote,str):continue
        for lineno,line in source.items():
            if quote in line:
                source_line_by_field[path].add(lineno)
    checked=0
    for number,claim in ledger.items():
        if claim['kind'] in ('unsupported','conflict'):
            raise SemanticGuardError('UNRESOLVED_MATERIAL',claim['issue'],line=number)
        if claim['kind']!='term':continue
        for path in claim['paths']:
            if path in ROOT_ROLES:
                # A role identifier is not a general permission or action. Only
                # the root escrow/deposit declaration can establish fixed roles.
                if ('amount_wei' not in claim['paths'] or
                    number not in source_line_by_field.get('amount_wei',set())):
                    raise SemanticGuardError('UNSUPPORTED_ROLE_CLAIM',
                        'A role name cannot account for a new authority rule',line=number)
                continue
            if path not in expected:
                raise SemanticGuardError('UNREPRESENTABLE_PATH','Model ledger references path not present in V3 IR: '+path,line=number)
            if number not in source_line_by_field.get(path,set()):
                raise SemanticGuardError('UNCITED_MATERIAL','IR does not cite source clause for '+path,line=number)
            checked+=1
    return {'status':'PASS_LEDGER_TO_IR_COVERAGE_NOT_SEMANTIC_PROOF','mapped_fields':checked,
            'source_lines':len(source), 'material_lines':sum(v['kind']=='term' for v in ledger.values())}


def check_known_collisions(request:str, ledger:dict[int,dict])->list[dict]:
    """Independent numerical disagreement detection for ledger-mapped field paths.

    The numerical test only makes determinations when units are explicit; it
    never assumes that a missing or differently phrased value equals another.
    """
    source=source_lines(request)
    seen=defaultdict(list)
    for number,item in ledger.items():
        if item['kind'] not in ('term','conflict'):continue
        for path in item['paths']:
            if path not in FIELD_SET and not PATH_PATTERN.fullmatch(path):continue
            if path.endswith('max_extensions'):
                # Numeric count is not a money or time unit. If ambiguous, let
                # the independent semantic review resolve it; do not guess.
                numbers=set()
            else:
                numbers=bridge._to_units(source[number],path)
            if len(numbers)==1:seen[path].append((number,next(iter(numbers))))
    collisions=[]
    for path,entries in seen.items():
        if len({v for _,v in entries})>1:
            collisions.append({'path':path,'lines':[n for n,_ in entries],
                               'values':sorted({v for _,v in entries})})
    return collisions
