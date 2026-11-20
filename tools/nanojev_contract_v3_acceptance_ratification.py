#!/usr/bin/env python3
"""Validate original 12-ETH V3 acceptance schedule and ratification *draft*.

Read-only; does not sign, attest, certify, deploy, or approve anything. This
validator does not replace V3's frozen compiler or existing owner-review gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tools import nanojev_contract_v3_owner_review as owner

FILES = {
    'scope': 'v3-original-12eth-feature-scope-PROPOSED.json',
    'plan': 'v3-original-12eth-test-plan-PROPOSED.json',
    'environment': 'v3-original-12eth-test-environment-PROPOSED.json',
    'schedule': 'v3-original-12eth-acceptance-schedule-PROPOSED.json',
    'ratification': 'v3-original-12eth-owner-ratification-DRAFT.json',
}
EXPECTED_IDS = ('prototype', 'beta', 'final_application')
EXPECTED_TESTS = (
    ('P01', 'P02', 'P03', 'P04'),
    ('B01', 'B02', 'B03', 'B04', 'B05'),
    ('F01', 'F02', 'F03', 'F04', 'F05', 'F06'),
)
EXPECTED_AMOUNTS = (3 * 10**18, 4 * 10**18, 5 * 10**18)
EXPECTED_THRESHOLDS = (None, None, 0)


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect(root: Path) -> dict:
    root = Path(root)
    docs = root / 'docs' / 'nanojev'
    problems: list[str] = []
    gaps = [
        'Original application-specific scope and mandatory user journeys not yet agreed',
        'Functional input/output fixtures and expected results not yet approved',
        'Pinned test environment, test commands and supported target platforms absent',
        'Payer/worker identity, chain, governing terms, signatures and timestamp tolerance absent',
        'Owner decision on R01–R14, all 16 parameters, four economic risks and scope absent',
        'Independent expert semantic certification and deployment authorization absent',
    ]
    try:
        records = {k: read(docs / v) for k, v in FILES.items()}
        instance = read(docs / owner.INSTANCE)
        review = read(docs / owner.REVIEW)
        spec = read(docs / owner.SPEC)
        baseline = owner.assess(root)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        return {'schema': 'nanojev.contract.v3.acceptance_ratification_draft_gate.v1',
                'status': 'FAIL_ACCEPTANCE_RATIFICATION_DRAFT_INTEGRITY',
                'errors': ['Inputs missing or unreadable: ' + str(exc)],
                'outstanding_decisions': gaps,
                'owner_approved': False, 'party_agreed': False,
                'expert_gold_independence_verified': False,
                'deployment_authorized': False}

    scope, plan, env, schedule, rat = (
        records[x] for x in ('scope', 'plan', 'environment', 'schedule', 'ratification')
    )
    if baseline['status'] != 'OWNER_REVIEW_READY_RATIFICATION_PENDING' or baseline['errors']:
        problems.append('Frozen owner review inputs do not pass original integrity gate')
    if len(instance.get('milestones', [])) != 3 or instance.get('amount_wei') != 12 * 10**18:
        problems.append('Instance no longer is 12 ETH in three milestones')
    if schedule.get('schema') != 'nanojev.contract.v3.original_12eth_acceptance_schedule.v1':
        problems.append('Wrong schedule schema')
    if schedule.get('instance_sha256') != sha(docs / owner.INSTANCE):
        problems.append('Schedule does not bind exact frozen instance')
    if rat.get('schema') != 'nanojev.contract.v3.original_12eth_owner_ratification.v1':
        problems.append('Wrong ratification schema')
    for key, fname in (('spec_sha256', owner.SPEC), ('instance_sha256', owner.INSTANCE),
                       ('compiler_input_sha256', owner.COMPILER), ('review_sha256', owner.REVIEW)):
        if rat.get(key) != sha(docs / fname):
            problems.append('Ratification not bound to ' + key)
    for key, kind in (('scope_sha256', 'scope'), ('test_plan_sha256', 'plan'),
                      ('test_environment_sha256', 'environment'),
                      ('acceptance_schedule_sha256', 'schedule')):
        if rat.get(key) != sha(docs / FILES[kind]):
            problems.append('Ratification artifact digest mismatch: ' + key)
    for k, h in (('deliverable_scope_sha256', 'scope'),
                 ('acceptance_test_plan_sha256', 'plan'),
                 ('build_and_runtime_environment_sha256', 'environment')):
        for j, row in enumerate(schedule.get('milestones', [])):
            if row.get(k) != sha(docs / FILES[h]):
                problems.append('Schedule attachment hash mismatch: milestone ' + str(j) + ' ' + k)

    if scope.get('status') != 'UNAGREED_FEATURE_SCOPE' or scope.get('scope_authority_document_sha256') is not None \
       or scope.get('party_agreed') is not False or scope.get('owner_approved') is not False:
        problems.append('Feature scope draft falsely asserts agreement')
    if plan.get('status') != 'TEST_METHODS_PROPOSED_FIXTURES_UNAGREED' or plan.get('party_agreed') is not False:
        problems.append('Test plan draft falsely asserts agreement')
    if env.get('status') != 'TARGET_ENVIRONMENT_UNAGREED' or env.get('signed_target_environment') is not False:
        problems.append('Environment draft falsely asserts agreement')
    if len(scope.get('milestones', [])) != 3 or len(plan.get('milestones', [])) != 3 \
       or len(schedule.get('milestones', [])) != 3:
        problems.append('Exactly three milestone entries required in every draft')
    else:
        for idx, (s, p, a) in enumerate(zip(scope['milestones'], plan['milestones'], schedule['milestones'])):
            name = EXPECTED_IDS[idx]
            if s.get('id') != name or p.get('id') != name or a.get('id') != name \
               or a.get('amount_wei') != EXPECTED_AMOUNTS[idx]:
                problems.append('Milestone identifier or amount mismatch: ' + name)
            if s.get('release_scope_agreed') is not False \
               or s.get('mandatory_requirement_ids') != [] \
               or s.get('mandatory_user_journeys') != [] \
               or s.get('approved_input_output_fixture_sha256') is not None:
                problems.append('Draft scope has unsupported approval or invented requirements: ' + name)
            if p.get('technical_scope_signed') is not False:
                problems.append('Draft plan falsely asserts signed scope: ' + name)
            test_ids = [t.get('id') for t in p.get('tests', [])]
            if tuple(test_ids) != EXPECTED_TESTS[idx] or a.get('proposed_mandatory_test_ids') != test_ids:
                problems.append('Mandatory acceptance test IDs changed: ' + name)
            for test in p.get('tests', []):
                if test.get('mandatory') is not True or not owner.nonempty(test.get('procedure')) \
                   or not owner.nonempty(test.get('observable_pass_condition')):
                    problems.append('Unverifiable acceptance test: ' + str(test.get('id')))
                if any(test.get(key) is not None for key in ('approved_fixture_id', 'approved_command',
                                                           'approved_reference_hash')):
                    problems.append('Draft misrepresents fixture or command approval: ' + str(test.get('id')))
            th = a.get('proposed_technical_thresholds', {})
            if not isinstance(th, dict) or th.get('mandatory_pass_percentage') != 100 \
               or th.get('max_open_critical') != 0 or th.get('max_open_high') != 0 \
               or th.get('max_open_medium') != EXPECTED_THRESHOLDS[idx]:
                problems.append('Proposed acceptance thresholds changed: ' + name)
            if a.get('payer_acceptance_acknowledgement') is not None \
               or a.get('worker_acceptance_acknowledgement') is not None:
                problems.append('Draft claims unauthorized party acknowledgement: ' + name)
    if schedule.get('scope_fully_defined') is not False \
       or schedule.get('acceptance_methods_agreed') is not False \
       or schedule.get('approved') is not False or schedule.get('deployment_authorized') is not False:
        problems.append('Draft acceptance schedule falsely asserts approval or deployment')
    if schedule.get('party_contract', {}).get('payer_agreement_sha256') is not None \
       or schedule.get('party_contract', {}).get('worker_agreement_sha256') is not None:
        problems.append('Draft acceptance schedule falsely asserts party agreement')
    if rat.get('status') != 'DRAFT_READY_FOR_OWNER_DECISION_NO_RATIFICATION' \
       or rat.get('owner_declaration') is not None or rat.get('owner_name') is not None \
       or rat.get('approved_at_utc') is not None or rat.get('owner_authority_basis') is not None \
       or rat.get('approval_document_sha256') is not None:
        problems.append('Owner ratification draft falsely asserts approval')
    for field in ('owner_approved', 'parties_signed_acceptance_schedule',
                  'expert_gold_independence_verified', 'deployment_authorized'):
        if rat.get(field) is not False:
            problems.append('Ratification draft falsely asserts ' + field)
    if rat.get('explicit_economic_risk_acknowledgements') != {r:False for r in owner.RISKS}:
        problems.append('Owner risk acknowledgements have been filled without consent')
    clauses = rat.get('normative_clause_decisions', [])
    if len(clauses) != 14 or len(spec.get('normative_decisions', [])) != 14 \
       or len(review.get('normative_clause_reviews', [])) != 14:
        problems.append('Exactly 14 normative decisions required')
    else:
        for row, r, source in zip(clauses, review['normative_clause_reviews'], spec['normative_decisions']):
            if row.get('id') != r.get('id') or row.get('proposed_rule_sha256') != r.get('proposed_rule_sha256') \
               or row.get('proposed_rule_sha256') != owner.canonical(source['proposed_rule']) \
               or row.get('decision') != 'PENDING' or row.get('authority_reference') is not None \
               or row.get('rationale') is not None or not owner.nonempty(row.get('analyst_rationale_for_owner_consideration')):
                problems.append('Improper ratification clause decision: ' + str(row.get('id')))
    params = rat.get('instance_parameter_decisions', [])
    source_params = owner.parameters(instance)
    if len(params) != 16 or len(source_params) != 16:
        problems.append('Exactly 16 parameter decisions required')
    else:
        for row, (path, value) in zip(params, source_params):
            if row.get('path') != path or row.get('proposed_value') != value \
               or type(row.get('proposed_value')) is not type(value) \
               or row.get('decision') != 'PENDING' or row.get('rationale') is not None \
               or not owner.nonempty(row.get('analyst_rationale_for_owner_consideration')):
                problems.append('Improper parameter approval: ' + str(path))
    # The original owner gate remains authoritative on whether party and owner
    # approvals are present. Its rejection is EXPECTED, not an integrity error.
    try:
        owner_state = owner.assess(root, docs / FILES['ratification'], docs / FILES['schedule'])
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        problems.append('Original owner gate rejected malformed draft: ' + str(exc))
        owner_state = {'status': 'FAIL_OWNER_GATE_DRAFT_INPUT',
                       'deployment_authorized': False, 'blockers': ['Malformed draft']}
    if owner_state['status'] != 'OWNER_REVIEW_READY_RATIFICATION_PENDING' \
       or owner_state['deployment_authorized'] is not False or not owner_state['blockers']:
        problems.append('Original owner gate did not retain fail-closed authorization status')
    return {
        'schema': 'nanojev.contract.v3.acceptance_ratification_draft_gate.v1',
        'status': ('FAIL_ACCEPTANCE_RATIFICATION_DRAFT_INTEGRITY' if problems
                   else 'PASS_ACCEPTANCE_SCHEDULE_AND_RATIFICATION_DRAFT_NOT_AUTHORIZED'),
        'errors': problems,
        'milestones': 3, 'planned_mandatory_tests': 15,
        'normative_decisions_prepared': 14, 'parameter_decisions_prepared': 16,
        'ratification_draft_sha256': sha(docs / FILES['ratification']),
        'acceptance_schedule_sha256': sha(docs / FILES['schedule']),
        'feature_scope_sha256': sha(docs / FILES['scope']),
        'test_plan_sha256': sha(docs / FILES['plan']),
        'test_environment_sha256': sha(docs / FILES['environment']),
        'outstanding_decisions': gaps,
        'owner_review_status': owner_state['status'],
        'owner_review_blockers': owner_state['blockers'],
        'owner_approved': False,
        'party_agreed': False,
        'expert_gold_independence_verified': False,
        'deployment_authorized': False,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', default='.')
    ap.add_argument('--output', default='runtime/reports/nanojev-v3-original-12eth-acceptance-ratification-draft-0001.json')
    opts = ap.parse_args(argv)
    result = inspect(Path(opts.root))
    path = Path(opts.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'errors': result['errors'],
                      'planned_mandatory_tests': result.get('planned_mandatory_tests', 0),
                      'owner_approved': False, 'deployment_authorized': False,
                      'output': str(path)}))
    return 1 if result['status'].startswith('FAIL_') else 0

if __name__ == '__main__':
    raise SystemExit(main())
