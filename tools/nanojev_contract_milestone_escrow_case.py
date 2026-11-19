#!/usr/bin/env python3
"""Read-only special-case probe: 12 ETH, 3 milestones, extensions, arbitration.

This deliberately probes beyond Contract Language v2. Passing means the LLM
correctly REFUSED to fabricate a v2 contract; it does not mean the requested
multi-milestone contract was compiled. No keys, Solidity or Docker involved.
The prior 212/212 Forge regression remains a separate result.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from tools import nanojev_contract_solidity_smoke as base
from tools import nanojev_contract_pull_payments as pull
from tools import nanojev_contract_state_machine as state_machine
from tools import nanojev_contract_pull_smoke as smoke
from tools import nanojev_contract_composition_smoke as composition
from tools import nanojev_contract_completeness as completeness

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'nanojev_contract_milestone_escrow_special_v1.json'
# SHA256 of pinned, assistant-authored test fixture; the user may choose to change
# the contract later, but that requires an intentional fixture/version update.
PIN = '79bf4a4039bdf2011f0f9e90efc2577a494995619a178325dc8532b6b1714e5f'
CASE = 'milestone_escrow'

# General expressiveness-review instructions, not the escrow fixture or its gold
# category/phrase sets.  This is an *assisted* evaluation of whether the head
# follows a documented refusal procedure, not a blind understanding score.
ASSURANCE_REVIEW = """
When a request cannot faithfully fit the v2 schema, return needs_clarification.
A refusal must not merely request more facts if the language lacks necessary
primitives. State that Contract Language v2 cannot represent/compile the full
agreement, and name the unsupported behavior using the user's own terms.
Separately identify any facts the user still needs to supply. Before finishing,
check whether the request entails multiple conditional obligations, more than
one independent deadline, mutable deadlines, multi-party decisions or disputed
outcomes, and whether failure to respond can trap held assets. Identify which
behaviors the schema cannot encode; do not invent missing transitions, default
arbitration rules, automatic settlement, or recovery authorities. State any
unresolved liveness/recovery rule explicitly rather than assuming that a
recipient's ability to withdraw implies the escrow can always settle.
Only include facts and limitations actually relevant to the user's request.
Use the JSON needs_clarification questions array for all identified issues.
"""
SPECIAL_PROMPT = smoke.PROMPT + "\n" + ASSURANCE_REVIEW


@dataclass(frozen=True)
class SpecialOptions:
    output: Path
    model: str = base.DEFAULT_MODEL
    ollama_url: str = base.DEFAULT_URL
    llm_timeout: float = 240.0
    dry_run: bool = False
    audit_from_report: Path | None = None


def load_fixture() -> dict:
    raw = FIXTURE.read_bytes()
    if base.digest(raw) != PIN:
        raise base.SmokeFailure('preflight', 'Pinned milestone escrow challenge changed')
    obj = json.loads(raw.decode('utf-8', 'strict'))
    if (obj.get('schema') != 'nanojev.contract.special_case.milestone_escrow.v1'
            or obj.get('id') != CASE
            or [m.get('name') for m in obj.get('milestones', [])] !=
               ['prototype', 'beta', 'completed_application']):
        raise base.SmokeFailure('preflight', 'Milestone escrow test definition invalid')
    if sum(m['wei'] for m in obj['milestones']) != obj['deposit_wei']:
        raise base.SmokeFailure('preflight', 'Milestone amounts violate escrow conservation')
    if not obj['required_capabilities'] or not obj['missing_user_terms']:
        raise base.SmokeFailure('preflight', 'Incomplete expressiveness oracle')
    return obj


def capability_ceiling() -> dict:
    """Ground the refusal in CURRENT v2 compiler/validator interfaces."""
    if pull.LANG != 'nanojev.contract.state_machine.v2':
        raise base.SmokeFailure('preflight', 'Contract Language version changed; revisit challenge')
    if state_machine.FIXED.get('asset') != 'native_eth':
        raise base.SmokeFailure('preflight', 'Unexpected Contract Language asset')
    # Validate a known accepted v2 control: one deadline, single terminal credit
    # distribution. This is not a live inference or an expert annotation.
    original = smoke.cases()
    control = next((x for x in original if x['id'] == 'safe_reversible_split'), None)
    if control is None:
        raise base.SmokeFailure('preflight', 'Original v2 positive control missing')
    known = pull.validate(control['expected'])
    if set(known) != {'language', 'asset', 'payer', 'worker', 'reviewer',
                      'amount_wei', 'deadline_seconds', 'rules', 'settlement_mode'}:
        raise base.SmokeFailure('preflight', 'V2 representation has changed; reassess expressiveness')
    if set(x['actor'] for x in known['rules']) - {'payer', 'worker', 'reviewer'}:
        raise base.SmokeFailure('preflight', 'V2 authorization model changed')
    if any(sum(p['bps'] for p in r['payments']) not in (0, 10000) for r in known['rules']):
        raise base.SmokeFailure('preflight', 'V2 settlement model changed')
    # Intentionally probe incompatible schema additions; never silently ignore.
    hypothetical = dict(known)
    hypothetical['milestones'] = [{'name':'prototype', 'amount_wei':3*10**18}]
    try:
        pull.validate(hypothetical)
    except base.SmokeFailure:
        pass
    else:
        raise base.SmokeFailure('preflight', 'Milestones unexpectedly supported; update test oracle')
    return {'language': pull.LANG,
            'one_funding_amount': True,
            'single_deadline_seconds_field': True,
            'allowed_roles': ['payer','worker','reviewer'],
            'partial_nonterminal_disbursement': 'NOT_SUPPORTED',
            'milestone_schema_extension': 'REJECTED',
            'settlement_mode': 'pull'}


def classify_response(raw: str, fixture: dict) -> dict:
    """Never treat a well-formed but lossy v2 graph as success."""
    obj = composition.parse(raw)
    if obj.get('status') == 'contract':
        parsed = obj.get('contract')
        validation = None
        try:
            pull.validate(parsed)
            validation = 'VALID_V2_BUT_LOSSY_FOR_REQUEST'
        except (base.SmokeFailure, TypeError, ValueError) as e:
            validation = 'INVALID_V2: ' + str(e)[:180]
        return {'decision':'UNSAFE_ACCEPTED_UNREPRESENTABLE', 'v2_validation':validation,
                'found_topics':[], 'missing_topics':list(fixture['refusal_topic_groups']),
                'questions':[], 'reason':'A single-settlement v2 contract cannot implement the three-milestone dispute escrow'}
    if set(obj) != {'status','questions'} or obj.get('status') != 'needs_clarification':
        raise base.SmokeFailure('llm', 'Expected a contract or precise needs_clarification JSON object')
    qs=obj['questions']
    if (not isinstance(qs,list) or not 1 <= len(qs) <= 12
            or any(not isinstance(x,str) or not x.strip() or len(x)>400 for x in qs)):
        raise base.SmokeFailure('llm', 'Malformed or empty clarification questions')
    text = ' '.join(qs).lower()
    found=[key for key,phrases in fixture['refusal_topic_groups'].items()
           if any(p in text for p in phrases)]
    missing=[key for key in fixture['refusal_topic_groups'] if key not in found]
    # Asking for dates does not establish that the model noticed an impossible
    # representation. A green refusal must distinguish missing user details
    # from a fundamental Contract Language capability gap.
    # Never count "the arbitrator cannot respond" as acknowledgment that the
    # *language* lacks the requested operations.  Look for an expressiveness
    # limitation paired with a reference to the IR/compiler, not a generic
    # inability belonging to a contract party.
    anchors=('v2', 'contract language', 'schema', 'compiler', 'representation',
             'semantic primitive', 'language')
    gap_terms=('cannot', "can't", 'does not support', 'doesn\'t support',
               'not supported', 'not representable', 'not expressible',
               'unable to represent', 'requires extension', 'requires an extension',
               'lacks', 'missing primitives', 'cannot compile', 'not possible')
    identified_gap=False
    for question in qs:
        q=question.lower()
        anchor_positions=[q.find(a) for a in anchors if a in q]
        term_positions=[q.find(t) for t in gap_terms if t in q]
        if anchor_positions and term_positions and any(
                abs(a-t)<=180 for a in anchor_positions for t in term_positions):
            identified_gap=True
            break
    return {'decision': 'CORRECT_BOUNDED_REFUSAL' if not missing and identified_gap else 'INCOMPLETE_REFUSAL',
            'v2_validation':'NOT_APPLICABLE_REFUSED', 'found_topics':found,
            'missing_topics':missing, 'explicit_v2_limitation_acknowledged': identified_gap,
            'questions':qs}


def replay_live_response(source: Path, *, output: Path, fx: dict) -> tuple[str, str]:
    """Read-only replay of a verified earlier *live* report, no LLM/network call."""
    if source.resolve() == output.resolve():
        raise base.SmokeFailure('preflight', 'Replay output cannot overwrite its source')
    try:
        wire = source.read_bytes()
        prior = json.loads(wire.decode('utf-8', 'strict'))
    except (OSError, ValueError, UnicodeError) as e:
        raise base.SmokeFailure('preflight', 'Cannot read live report for replay') from e
    if (not isinstance(prior, dict) or prior.get('schema') != 'nanojev.contract.special_case_report.v1'
            or prior.get('case_id') != CASE or prior.get('mode') != 'LIVE_OLLAMA_LLM'
            or prior.get('fixture_sha256') != PIN
            or prior.get('compiler_sha256') != base.digest(Path(pull.__file__).read_bytes())
            or prior.get('request_sha256') != base.digest(fx['request'])
            or prior.get('stages', {}).get('head') != 'PASS_LIVE'
            or not isinstance(prior.get('head_response'), str)
            or prior.get('head_response_sha256') != base.digest(prior['head_response'])
            or prior.get('head_prompt_sha256') != base.digest(SPECIAL_PROMPT)):
        raise base.SmokeFailure('preflight', 'Replay requires a matching, integrity-checked live special-case report')
    return prior['head_response'], base.digest(wire)


def run(options: SpecialOptions, *, head: Callable[[str],str] | None = None) -> dict:
    """Single live challenge, no implicit repairs or gold-fed LLM prompts."""
    path=options.output.resolve()
    result={'schema':'nanojev.contract.special_case_report.v1',
            'case_id':CASE, 'status':'BLOCKED_NOT_VERIFIED',
            'mode':'PLAN_ONLY' if options.dry_run else ('REPORT_REPLAY_NO_LLM' if options.audit_from_report is not None else ('LIVE_OLLAMA_LLM' if head is None else 'OFFLINE_INJECTED')),
            'started_at':base.now(), 'model':options.model,
            'fixture_sha256':None, 'compiler_sha256':base.digest(Path(pull.__file__).read_bytes()),
            'independent_auditor_sha256':base.digest(Path(completeness.__file__).read_bytes()),
            'head_prompt_sha256':base.digest(SPECIAL_PROMPT), 'gold_excluded_from_head':True,
            'review_guidance':'GENERIC_V2_ASSURANCE_CHECKLIST_ASSISTED_NOT_BLIND',
            'provenance':'assistant_authored_user_requested_challenge_not_independent_gold',
            'allow_solidity':False, 'forge':'NOT_RUN_UNSUPPORTED_REQUEST',
            'stages':{}, 'blockers':[]}
    try:
        fx=load_fixture()
        result['fixture_sha256']=PIN
        result['request_sha256']=base.digest(fx['request'])
        result['amount_wei']=fx['deposit_wei']
        result['milestone_amounts_wei']={m['name']:m['wei'] for m in fx['milestones']}
        result['missing_user_terms']=fx['missing_user_terms']
        result['capability_matrix']=fx['required_capabilities']
        result['v2_ceiling']=capability_ceiling()
        result['stages']['preflight']='PASS_PINNED_CHALLENGE_AND_V2_CEILING'
        result['stages']['expressiveness']='UNSUPPORTED_MULTI_MILESTONE_DISPUTE_ESCROW'
        # Crucially, this policy audit is computed from the pinned request and
        # real v2 compiler ceiling, BEFORE any LLM reply is read. The LLM's
        # questionnaire cannot make this gate green or suppress its findings.
        audit = completeness.evaluate_milestone_requirements(fx, result['v2_ceiling'])
        result['independent_system_audit'] = audit
        result['stages']['system_completeness'] = 'PASS_FAIL_CLOSED_UNREPRESENTABLE_AND_RECOVERY_UNRESOLVED'
        result['emission_gate'] = 'BLOCKED_BY_SYSTEM_NOT_LLM'
        if options.dry_run:
            result['status']='PLAN_ONLY_NOT_EXECUTED'
            result['request']=fx['request']
            return result
        if path.exists():
            raise base.SmokeFailure('preflight', 'Existing report: use a new --output name')
        if options.audit_from_report is not None and (head is not None or options.dry_run):
            raise base.SmokeFailure('preflight', 'Replay cannot be combined with injection or dry-run')
        if options.llm_timeout <= 0:
            raise base.SmokeFailure('preflight', 'LLM timeout must be positive')
        base.local_url(options.ollama_url)
        if options.audit_from_report is not None:
            print('[SPECIAL_CASE] milestone_escrow: audit prior live response (no model call)', flush=True)
            raw, sha = replay_live_response(options.audit_from_report, output=path, fx=fx)
            result['replayed_source_sha256'] = sha
            result['replayed_source_path'] = str(options.audit_from_report)
        else:
            print('[SPECIAL_CASE] milestone_escrow: live Contract Language v2 refusal challenge',flush=True)
            raw = head(fx['request']) if head else smoke.real_head(
                options.ollama_url,options.model,fx['request'],options.llm_timeout,
                prompt=SPECIAL_PROMPT)
        if not isinstance(raw,str) or len(raw)>40000:
            raise base.SmokeFailure('llm','Malformed or excessive LLM response')
        result['head_response_sha256']=base.digest(raw)
        result['head_response']=raw
        result['stages']['head']='PASS_REPLAYED_LIVE_OUTPUT_NO_MODEL' if options.audit_from_report is not None else ('PASS_LIVE' if head is None else 'PASS_INJECTED')
        check=classify_response(raw,fx)
        result['translation']=check
        result['stages']['semantic_refusal']=check['decision']
        if check['decision']=='CORRECT_BOUNDED_REFUSAL':
            result['status']=('PASS_REPLAY_DIAGNOSTIC_ONLY' if options.audit_from_report is not None else 'PASS_UNSUPPORTED_REFUSED_NOT_CERTIFIED' if head is None else 'PASS_INJECTED_OFFLINE_ONLY')
        else:
            reasons=[]
            if check['decision']=='UNSAFE_ACCEPTED_UNREPRESENTABLE':
                reasons.append('LLM attempted to compile a contract beyond v2 capabilities')
            else:
                if check['missing_topics']:
                    reasons.append('Missing required refusal topics: '+', '.join(check['missing_topics']))
                if not check.get('explicit_v2_limitation_acknowledged',False):
                    reasons.append('Refusal did not explicitly identify Contract Language v2 expressiveness limits')
            result['blockers'].append({'stage':'semantic_refusal',
                 'reason':'; '.join(reasons) or check.get('reason','Incomplete or unsafe refusal'),
                 'missing_topics':check['missing_topics'],
                 'explicit_v2_limitation_acknowledged':check.get('explicit_v2_limitation_acknowledged',False)})
    except base.SmokeFailure as e:
        result['blockers'].append({'stage':e.stage,'reason':e.reason})
    except Exception as e:
        result['blockers'].append({'stage':'internal','reason':type(e).__name__+': '+str(e)[:300]})
    finally:
        result['finished_at']=base.now()
        if not options.dry_run and not path.exists():
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return result
