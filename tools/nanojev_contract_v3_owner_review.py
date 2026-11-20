#!/usr/bin/env python3
"""Read-only V3 12-ETH owner review / ratification integrity gate.

Checks proposed terms, clause/parameter acknowledgement, contractual acceptance
artifacts, and risk acknowledgement. NEVER executes, signs, or authorizes deploy.
Owner-supplied records can only be described as self-attested, not authenticated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

SPEC = 'v3-authoritative-specification.v1.json'
INSTANCE = 'v3-original-12eth-instance-PROPOSED.json'
COMPILER = 'v3-original-12eth-compiler-input-PROPOSED.json'
MANIFEST = 'v3-original-12eth-manifest-PROPOSED.json'
REVIEW = 'v3-original-12eth-owner-review-PENDING.json'
ACCEPTANCE = 'v3-original-12eth-acceptance-schedule-TEMPLATE.json'
RATIFICATION = 'v3-original-12eth-owner-ratification-TEMPLATE.json'
FIELDS = ('deadline_seconds', 'extension_decision_seconds', 'max_extensions')
POLICY_FIELDS = ('review_seconds', 'dispute_filing_seconds', 'appointment_seconds',
                 'ruling_seconds', 'on_review_silence', 'on_appointment_failure',
                 'on_arbitrator_silence')
RISKS = ('payer_review_silence_pay_worker', 'failed_arbitrator_appointment_refund_payer',
         'silent_arbitrator_refund_payer', 'post_due_extension')
APPROVAL_DECLARATION = (
    'I approve all fourteen R01–R14 normative clauses and all sixteen parameters '
    'of the exact NanoJev V3 original 12 ETH proposal identified by the hashes '
    'in this record. I have reviewed the milestone acceptance schedules and '
    'expressly acknowledge the four listed economic risks.'
)
SHA_RE = re.compile(r'^[0-9a-f]{64}$')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True,
                                      separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def valid_hash(x):
    return isinstance(x, str) and SHA_RE.fullmatch(x) is not None


def nonempty(x):
    return isinstance(x, str) and bool(x.strip())


def parameters(instance):
    out = []
    for m in instance['milestones']:
        for k in FIELDS:
            out.append((f'milestones.{m["id"]}.{k}', m[k]))
    for k in POLICY_FIELDS:
        out.append(('policy.' + k, instance['policy'][k]))
    return out


def check_acceptance(schedule, instance):
    errors=[]
    if schedule.get('schema') != 'nanojev.contract.v3.original_12eth_acceptance_schedule.v1' \
            or schedule.get('instance_sha256') is None:
        errors.append('Acceptance schedule schema or binding missing')
    if not isinstance(schedule.get('milestones'), list) or len(schedule['milestones']) != 3:
        return errors + ['Acceptance schedule requires exactly three milestones']
    required = ('deliverable_scope_sha256','deliverable_scope_reference',
                'acceptance_test_plan_sha256','acceptance_test_plan_reference',
                'acceptance_thresholds','build_and_runtime_environment_sha256',
                'submission_evidence_requirements','permitted_rejection_grounds',
                'payer_acceptance_acknowledgement','worker_acceptance_acknowledgement')
    hashes = ('deliverable_scope_sha256','acceptance_test_plan_sha256','build_and_runtime_environment_sha256')
    for original, row in zip(instance['milestones'], schedule['milestones']):
        label=original['id']
        if row.get('id') != label or row.get('amount_wei') != original['amount_wei']:
            errors.append(f'{label}: acceptance milestone identity or amount mismatch')
        for k in required:
            val=row.get(k)
            if k in hashes:
                if not valid_hash(val): errors.append(f'{label}: missing or invalid {k}')
            elif not nonempty(val):
                errors.append(f'{label}: missing {k}')
    party=schedule.get('party_contract',{})
    if not isinstance(party,dict):
        errors.append('Missing party contract');return errors
    if type(party.get('chain_id')) is not int or party['chain_id']<=0:
        errors.append('Missing authorized chain_id')
    for k in ('payer_address','worker_address','jurisdiction_or_governing_terms_sha256',
              'payer_agreement_sha256','worker_agreement_sha256','block_timestamp_tolerance_agreed'):
        val=party.get(k)
        if k.endswith('_sha256'):
            if not valid_hash(val): errors.append('Missing or invalid '+k)
        elif not nonempty(val): errors.append('Missing '+k)
    if schedule.get('approved') is not True:
        errors.append('Acceptance schedule not expressly approved')
    return errors


def assess(root=Path('.'), ratification_path=None, schedule_path=None):
    docs=Path(root)/'docs'/'nanojev'; errors=[]; blockers=[]
    try:
        spec=read(docs/SPEC);inst=read(docs/INSTANCE);payload=read(docs/COMPILER)
        manifest=read(docs/MANIFEST);review=read(docs/REVIEW)
    except (OSError, ValueError, TypeError) as exc:
        return {'status':'FAIL_OWNER_REVIEW_INPUT_INTEGRITY','errors':[str(exc)],
                'blockers':[],'clauses_reviewed':0,'parameters_reviewed':0,
                'owner_approved':False,'party_agreed':False,
                'expert_gold_independence_verified':False,'deployment_authorized':False}
    for name,filename in (('target_spec_sha256',SPEC),('proposal_sha256',INSTANCE),
                          ('compiler_input_sha256',COMPILER)):
        if sha(docs/filename)!=manifest.get(name):errors.append('Immutable input mismatch: '+name)
    if review.get('spec_sha256')!=sha(docs/SPEC) \
        or review.get('instance_sha256')!=sha(docs/INSTANCE) \
        or review.get('compiler_input_sha256')!=sha(docs/COMPILER):
        errors.append('Review is not bound to frozen target inputs')
    if review.get('status')!='ANALYST_REVIEW_COMPLETE_OWNER_DECISION_PENDING' \
        or any(review.get(k) is not False for k in ('owner_approved','party_agreed',
                                 'expert_gold_independence_verified','deployment_authorized')):
        errors.append('Advisory review falsely represents authorization')
    if spec.get('status')!='DRAFT_COMPLETE_OWNER_AUTHORIZATION_REQUIRED' \
        or inst.get('status')!='DRAFT_INSTANCE_PROPOSED_NOT_AUTHORIZED':
        errors.append('Source spec/instance has unexpected authorization status')
    try:
        expected=parameters(inst)
        review_params=review['instance_parameter_reviews'];rules=spec['normative_decisions']
        review_rules=review['normative_clause_reviews']
        if len(expected)!=16 or len(review_params)!=16:errors.append('Not exactly 16 instance reviews')
        else:
            for (path,val),rec in zip(expected,review_params):
                if rec.get('path')!=path or type(rec.get('proposed_value')) is not type(val) or rec.get('proposed_value')!=val \
                  or rec.get('owner_decision')!='PENDING':
                    errors.append('Parameter review was changed or incorrectly authorized: '+path)
        if len(rules)!=14 or len(review_rules)!=14:errors.append('Not exactly 14 normative reviews')
        else:
            for rule,rec in zip(rules,review_rules):
                if rec.get('id')!=rule['id'] or rec.get('question')!=rule['question'] \
                    or rec.get('proposed_rule_sha256')!=canonical(rule['proposed_rule']) \
                    or rec.get('owner_decision')!='PENDING':
                    errors.append('Normative review differs from frozen clause: '+rule['id'])
        if payload['amount_wei']!=inst['amount_wei'] or payload['policy']!=inst['policy'] \
           or len(payload['milestones'])!=len(inst['milestones']):
            errors.append('Compiler payload and proposed instance differ')
        else:
            for a,b in zip(payload['milestones'],inst['milestones']):
                if any(a[k]!=b[k] for k in a):errors.append('Compiler payload milestone diverges: '+b['id'])
    except (KeyError,TypeError,ValueError,IndexError) as exc:
        errors.append('Malformed review or instance: '+str(exc))
        expected=[];rules=[]
    if errors:
        return {'status':'FAIL_OWNER_REVIEW_INPUT_INTEGRITY','errors':errors,'blockers':[],
            'clauses_reviewed':len(review.get('normative_clause_reviews',[])),
            'parameters_reviewed':len(review.get('instance_parameter_reviews',[])),
            'owner_approved':False,'party_agreed':False,
            'expert_gold_independence_verified':False,'deployment_authorized':False}
    schedule=None
    if schedule_path:
        schedule=read(schedule_path)
        if schedule.get('instance_sha256') != sha(docs/INSTANCE):
            blockers.append('Acceptance schedule instance hash mismatch')
        blockers.extend(check_acceptance(schedule,inst))
    else:
        blockers.append('Agreed three-milestone acceptance schedule not provided')
    if not ratification_path:
        blockers.append('Explicit owner ratification record not provided')
    else:
        rat=read(ratification_path)
        if rat.get('schema')!='nanojev.contract.v3.original_12eth_owner_ratification.v1':
            blockers.append('Wrong owner ratification schema')
        for key,file in (('spec_sha256',SPEC),('instance_sha256',INSTANCE),
                         ('compiler_input_sha256',COMPILER),('review_sha256',REVIEW)):
            if rat.get(key)!=sha(docs/file): blockers.append('Owner ratification does not bind '+key)
        for k in ('owner_name','owner_authority_basis','approved_at_utc'):
            if not nonempty(rat.get(k)):blockers.append('Owner ratification lacks '+k)
        if rat.get('owner_declaration')!=APPROVAL_DECLARATION:
            blockers.append('Owner has not made the explicit approval declaration')
        cds=rat.get('normative_clause_decisions',[])
        if len(cds)!=14:blockers.append('14 explicit clause approvals required')
        else:
            for d,r in zip(cds,review_rules):
                if d.get('id')!=r['id'] or d.get('proposed_rule_sha256')!=r['proposed_rule_sha256'] \
                    or d.get('decision')!='APPROVE' or not nonempty(d.get('authority_reference')) \
                    or not nonempty(d.get('rationale')):
                    blockers.append('Normative clause not ratified: '+r['id'])
        pds=rat.get('instance_parameter_decisions',[])
        if len(pds)!=16:blockers.append('16 explicit parameter approvals required')
        else:
            for p,orig in zip(pds,review_params):
                if p.get('path')!=orig['path'] or type(p.get('proposed_value')) is not type(orig['proposed_value']) \
                    or p.get('proposed_value')!=orig['proposed_value'] or p.get('decision')!='APPROVE' \
                    or not nonempty(p.get('rationale')):
                    blockers.append('Parameter not ratified: '+orig['path'])
        risk=rat.get('explicit_economic_risk_acknowledgements',{})
        for k in RISKS:
            if risk.get(k) is not True:blockers.append('Risk not expressly accepted: '+k)
        if schedule is None or rat.get('acceptance_schedule_sha256')!=sha(schedule_path):
            blockers.append('Owner ratification lacks exact agreed acceptance schedule hash')
        if not valid_hash(rat.get('approval_document_sha256')):
            blockers.append('Owner signed approval document digest missing')
    return {
        'schema':'nanojev.contract.v3.original_12eth_owner_review_gate.v1',
        'status':('OWNER_RATIFICATION_SELF_ATTESTED_NOT_PARTY_OR_EXPERT_CERTIFIED' if not blockers
                  else 'OWNER_REVIEW_READY_RATIFICATION_PENDING'),
        'errors':errors,'blockers':blockers,'clauses_reviewed':14,'parameters_reviewed':16,
        'owner_approved':False,
        'owner_approval_record_complete':not bool(blockers),
        'owner_approval_authentication':'NOT_INDEPENDENTLY_VERIFIED',
        'party_agreed':False,
        'expert_gold_independence_verified':False,'deployment_authorized':False,
        'review_sha256':sha(docs/REVIEW),
        'spec_sha256':sha(docs/SPEC),'instance_sha256':sha(docs/INSTANCE)
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',default='.')
    p.add_argument('--ratification',help='Explicit owner-completed ratification record')
    p.add_argument('--acceptance',help='Agreed milestone acceptance schedule')
    p.add_argument('--output',default='runtime/reports/nanojev-v3-original-12eth-owner-review-0001.json')
    ns=p.parse_args(argv)
    result=assess(Path(ns.root),ns.ratification,ns.acceptance)
    path=Path(ns.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'], 'clauses_reviewed':result['clauses_reviewed'],
                      'parameters_reviewed':result['parameters_reviewed'],
                      'blockers':len(result['blockers']),
                      'deployment_authorized':False,'output':str(path)}))
    return int(result['status'].startswith('FAIL_'))


if __name__=='__main__':
    raise SystemExit(main())
