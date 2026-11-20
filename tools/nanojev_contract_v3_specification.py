#!/usr/bin/env python3
"""Owner-authorization preparation for V3; never confuses code/tests with consent.

Stdlib-only. Additive; no compiler imports, source mutation, deployment or keys.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SPEC_SCHEMA = 'nanojev.contract.v3.proposed_authoritative_specification.v1'
APPROVAL_SCHEMA = 'nanojev.contract.v3.owner_approval.v1'
RESULT_SCHEMA = 'nanojev.contract.v3.specification_authority_gate.v1'
EXPECTED_IDS = [f'R{i:02d}' for i in range(1, 15)]
POLICIES = {'pay_worker', 'refund_payer'}
ROOT_FIELDS = {'language', 'asset', 'settlement_mode', 'payer', 'worker', 'arbitrator'}
POLICY_SECONDS = ('review_seconds', 'dispute_filing_seconds', 'appointment_seconds', 'ruling_seconds')
SELECTABLE = ('on_review_silence', 'on_appointment_failure', 'on_arbitrator_silence')
FIXED = ('on_missed_submission', 'on_undisputed_rejection')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_report_sha(path):
    """Digest the semantic JSON content using the frozen V3 evidence convention.

    The existing independent-behavior and evidence-lock tools use
    json.dumps(data, sort_keys=True, separators=(",", ":")) with
    ensure_ascii=True (the json.dumps default). The result is independent
    of file indentation and CRLF/LF, but not of JSON content changes.
    Do not use this to hash the proposed specification or approval records.
    """
    data = load(path)
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()


def canonical_sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def positive_int(x, low, high):
    return type(x) is int and low <= x <= high


def verify_spec(spec):
    errors = []
    if spec.get('schema') != SPEC_SCHEMA or spec.get('status') != 'DRAFT_COMPLETE_OWNER_AUTHORIZATION_REQUIRED':
        errors.append('Invalid or non-draft V3 specification schema/status')
    decisions = spec.get('normative_decisions')
    if not isinstance(decisions, list) or [d.get('id') for d in decisions] != EXPECTED_IDS:
        errors.append('Exactly R01..R14, in order, are mandatory')
    else:
        for d in decisions:
            if d.get('decision_status') != 'PROPOSED_REQUIRES_OWNER_APPROVAL' or not d.get('proposed_rule') or not d.get('question'):
                errors.append(f'{d.get("id")}: missing explicit proposed rule or unresolved owner authority')
    brief = spec.get('recorded_user_design_intent', [])
    if [e.get('id') for e in brief] != [f'B{i:02d}' for i in range(1, 9)]:
        errors.append('Recorded user-design brief is incomplete or reordered')
    if spec.get('adjudication_requirements') != {'external_expert_verified': False, 'owner_approved': False, 'decision_count': 14}:
        errors.append('Draft must not claim owner/expert approval')
    limits = spec.get('candidate_language_profile', {})
    if not all(limits.get(k) for k in ROOT_FIELDS):
        errors.append('Candidate language profile missing role or asset')
    return errors


def check_review(spec, review):
    errors = []
    claims = review.get('claims')
    if not isinstance(claims, list) or [x.get('id') for x in claims] != EXPECTED_IDS:
        return ['The prior review must contain exactly R01..R14']
    for decision, claim in zip(spec['normative_decisions'], claims):
        if decision['question'] != claim.get('question'):
            errors.append(decision['id'] + ': wording differs from original adjudication question')
        if claim.get('certification_disposition') != 'UNRESOLVED_REQUIRES_OUTSIDE_ADJUDICATION':
            errors.append(decision['id'] + ': unexpected prior review disposition')
    if review.get('pending_external_decisions') != 14 or review.get('expert_gold_independence_verified') is not False:
        errors.append('Prior review cannot be represented as an independent certification')
    if review.get('source_report_sha256') != spec['provenance']['source_report_sha256']:
        errors.append('Source report hash differs between specification and prior review')
    return errors


def approval_template(spec, spec_sha):
    return {
        'schema': APPROVAL_SCHEMA,
        'status': 'OWNER_APPROVAL_NOT_PROVIDED',
        'spec_sha256': spec_sha,
        'spec_version': spec['document_version'],
        'owner_name': None,
        'owner_authority_basis': None,
        'approved_at_utc': None,
        'declaration': None,
        'decisions': [{
            'id': d['id'], 'proposed_rule_sha256': canonical_sha(d['proposed_rule']),
            'decision': 'PENDING', 'authority_reference': None, 'rationale': None,
        } for d in spec['normative_decisions']],
        'warning': 'This is a blank template. Approval is a human decision, not inferred from the compiler, tests, or this command. A self-declared record is not independently authenticated.'
    }


def check_approval(spec, spec_sha, approval):
    errors = []
    if approval.get('schema') != APPROVAL_SCHEMA or approval.get('spec_sha256') != spec_sha or approval.get('spec_version') != spec['document_version']:
        errors.append('Owner approval record has wrong schema, version or SHA-256')
    for name in ('owner_name', 'owner_authority_basis', 'approved_at_utc'):
        if not isinstance(approval.get(name), str) or not approval[name].strip():
            errors.append(f'Missing owner approval field: {name}')
    exact_declaration = 'I approve every R01-R14 clause in this exact NanoJev V3 specification version as the project requirements owner.'
    if approval.get('declaration') != exact_declaration:
        errors.append('Exact, conscious owner approval declaration is missing')
    decisions = approval.get('decisions', [])
    if len(decisions) != 14 or [d.get('id') for d in decisions] != EXPECTED_IDS:
        return errors + ['Owner approval needs all 14 ordered decisions']
    for d, source in zip(decisions, spec['normative_decisions']):
        if d.get('decision') != 'APPROVE' or d.get('proposed_rule_sha256') != canonical_sha(source['proposed_rule']):
            errors.append(source['id'] + ': clause not explicitly approved against its exact rule hash')
        if not isinstance(d.get('authority_reference'), str) or not d['authority_reference'].strip():
            errors.append(source['id'] + ': missing authority reference')
        if not isinstance(d.get('rationale'), str) or not d['rationale'].strip():
            errors.append(source['id'] + ': missing approval rationale')
    return errors


def check_instance(spec, instance):
    """Checks *syntactic* governed-escrow instance compatibility, never consent."""
    e=[]
    profile=spec['candidate_language_profile']
    for key in ROOT_FIELDS:
        if instance.get(key) != profile[key]:
            e.append(f'{key}: incorrect V3 language/role/asset value')
    if not positive_int(instance.get('amount_wei'), 1, 2**128-1):
        e.append('amount_wei must be a positive integer <= 2**128-1')
    ms=instance.get('milestones')
    if not isinstance(ms,list) or not 1 <= len(ms) <= 5:
        return e + ['Expected one through five milestones']
    ids=[];amounts=[]
    for n,m in enumerate(ms):
        ident=m.get('id') if isinstance(m,dict) else None
        if not isinstance(ident,str) or not ident or not ident.replace('_','a').isalnum() or len(ident)>32 or not ident[0].islower() or ident[0].isdigit() or ident.lower()!=ident:
            e.append(f'Milestone {n}: invalid identifier')
        ids.append(ident)
        amount=m.get('amount_wei')
        if not positive_int(amount,1,2**128-1):e.append(f'Milestone {n}: invalid amount_wei')
        else:amounts.append(amount)
        for key in ('deadline_seconds','extension_seconds','extension_decision_seconds'):
            bounds=profile[key]
            if not positive_int(m.get(key),bounds['min'],bounds['max']):
                e.append(f'Milestone {n}: missing or out-of-range {key}')
        if not positive_int(m.get('max_extensions'),0,2):e.append(f'Milestone {n}: undecided/invalid max_extensions')
        if type(m.get('extension_seconds')) is int and type(m.get('extension_decision_seconds')) is int and m['extension_decision_seconds'] >= m['extension_seconds']:
            e.append(f'Milestone {n}: extension decision must be shorter than extension')
    if len(ids)!=len(set(ids)):e.append('Duplicate milestone identity')
    if type(instance.get('amount_wei')) is int and sum(amounts)!=instance['amount_wei']:
        e.append('Milestone values must sum exactly to funded wei')
    policy=instance.get('policy')
    if not isinstance(policy,dict):return e+['Policy missing']
    for key in POLICY_SECONDS:
        if not positive_int(policy.get(key),3600,30*86400):e.append(f'Policy: {key} must be explicitly bounded')
    if all(type(policy.get(k)) is int for k in ('appointment_seconds','ruling_seconds')) and policy['appointment_seconds'] >= policy['ruling_seconds']:
        e.append('Appointment window must end before ruling window')
    for key in SELECTABLE:
        if policy.get(key) not in POLICIES:e.append(f'Policy: {key} needs explicit pay_worker/refund_payer')
    for key in FIXED:
        if policy.get(key) != 'refund_payer':e.append(f'Policy: {key} must explicitly be refund_payer')
    return e


def run(spec_path, review_path=None, report_path=None, approval_path=None, instance_path=None, template_path=None, out_path=None):
    spec=load(spec_path); digest=sha(spec_path)
    errors=verify_spec(spec)
    if review_path and not errors:errors+=check_review(spec,load(review_path))
    if report_path and canonical_report_sha(report_path)!=spec['provenance']['source_report_sha256']:
        errors.append('Frozen governance report canonical JSON digest differs from recorded evidence')
    approval_errors=[]
    if approval_path and not errors:
        approval_errors=check_approval(spec,digest,load(approval_path))
    instance_errors=[]
    if instance_path and not errors:instance_errors=check_instance(spec,load(instance_path))
    if template_path and not errors:
        dst=Path(template_path);dst.parent.mkdir(parents=True,exist_ok=True)
        dst.write_text(json.dumps(approval_template(spec,digest),indent=2)+'\n',encoding='utf-8')
    status=('FAIL_SPECIFICATION_INTEGRITY' if errors else
            'OWNER_APPROVAL_RECORDED_SELF_ATTESTED_NOT_EXPERT_CERTIFIED' if approval_path and not approval_errors else
            'DRAFT_COMPLETE_OWNER_AUTHORIZATION_REQUIRED')
    if not errors and approval_path and approval_errors:status='FAIL_OWNER_APPROVAL_INCOMPLETE'
    if not errors and instance_path and instance_errors:
        # Independent of owner approval: never conceal an unusable instance.
        status='DRAFT_INSTANCE_INCOMPLETE_OR_NONCONFORMING' if not approval_path else 'FAIL_INSTANCE_INCOMPLETE_OR_NONCONFORMING'
    result={'schema':RESULT_SCHEMA,'status':status,'spec_sha256':digest,'decisions':14,
            'owner_approved':status=='OWNER_APPROVAL_RECORDED_SELF_ATTESTED_NOT_EXPERT_CERTIFIED',
            'expert_gold_independence_verified':False,'deployment_authorized':False,
            'errors':errors,'approval_errors':approval_errors,'instance_errors':instance_errors,
            'reference_review_checked':bool(review_path),
            'source_report_bytes_checked':bool(report_path),
            'source_report_digest_method':'canonical_sorted_ascii_json_v1' if report_path else None,
            'source_report_canonical_sha256':canonical_report_sha(report_path) if report_path else None,
            'source_report_raw_sha256':sha(report_path) if report_path else None,
            'approval_record_checked':bool(approval_path),'instance_syntax_checked':bool(instance_path)}
    if out_path:
        dst=Path(out_path);dst.parent.mkdir(parents=True,exist_ok=True)
        dst.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--spec',default='docs/nanojev/v3-authoritative-specification.v1.json')
    ap.add_argument('--review')
    ap.add_argument('--report')
    ap.add_argument('--approval')
    ap.add_argument('--instance')
    ap.add_argument('--approval-template')
    ap.add_argument('--output',default='runtime/reports/nanojev-contract-v3-specification-authority-0001.json')
    ns=ap.parse_args(argv)
    result=run(ns.spec,ns.review,ns.report,ns.approval,ns.instance,ns.approval_template,ns.output)
    print(json.dumps({'status':result['status'],'decisions':result['decisions'],
                      'owner_approved':result['owner_approved'],
                      'expert_gold_independence_verified':False,
                      'errors':result['errors'],'approval_errors':result['approval_errors'],
                      'instance_errors':result['instance_errors'], 'output':ns.output}))
    return 1 if result['status'].startswith('FAIL_') else 0


if __name__=='__main__':
    raise SystemExit(main())
