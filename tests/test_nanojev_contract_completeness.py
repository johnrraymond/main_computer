"""Adversarial tests for LLM-independent completeness and fail-closed replay."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from tools import nanojev_contract_completeness as audit
from tools import nanojev_contract_milestone_escrow_case as case
from tools import nanojev_contract_pull_smoke as pull_smoke
from tools import nanojev_contract_solidity_smoke as base


# The questions from the user's failing 0002 run, not a gold solution.
LIVE_0002 = json.dumps({'status': 'needs_clarification', 'questions': [
    'What are the exact deadline durations (in seconds) for the prototype, beta, and completed application milestones?',
    "What is the specific evidence or action required to trigger the 'accepted' state for each milestone (e.g., a specific transaction or data input)?",
    'What is the procedure for appointing the arbitrator?',
    'What is the deadline or time limit for the arbitrator to resolve a dispute?',
    "How is the 'seven more days' extension triggered and by whom?",
    "What is the specific action or event that constitutes a 'failure' to meet a milestone?",
    'The v2 NanoJev Contract Language cannot represent multi-party decisions, arbitrary oracle inputs, or dispute resolution logic. How would you like to proceed?'
]})


def independence_oracle():
    return audit.evaluate_milestone_requirements(case.load_fixture(), case.capability_ceiling())


def make_live_source(path: Path, answer: str = LIVE_0002):
    fixture = case.load_fixture()
    obj = {
        'schema': 'nanojev.contract.special_case_report.v1',
        'case_id': 'milestone_escrow',
        'mode': 'LIVE_OLLAMA_LLM',
        'fixture_sha256': case.PIN,
        'compiler_sha256': base.digest(Path(case.pull.__file__).read_bytes()),
        'request_sha256': base.digest(fixture['request']),
        'head_prompt_sha256': base.digest(case.SPECIAL_PROMPT),
        'head_response': answer,
        'head_response_sha256': base.digest(answer),
        'stages': {'head': 'PASS_LIVE'},
        'status': 'BLOCKED_NOT_VERIFIED',
    }
    path.write_text(json.dumps(obj), encoding='utf-8')
    return obj


def test_covers_every_milestone_and_nonresponse_actor_without_llm():
    out = independence_oracle()
    assert out['head_independent'] is True
    assert out['representability'] == 'FAIL_V2_UNSUPPORTED'
    assert out['status'] == 'BLOCK_UNSUPPORTED_AND_UNRESOLVED'
    assert out['escrow_conservation']['static_amount_sum'] == 'PASS'
    assert out['escrow_conservation']['dynamic_credit_refund_conservation'].startswith('NOT_PROVED')
    assert len(out['unsupported_capabilities']) == 7
    assert len(out['supported_capabilities']) == 2
    assert len(out['unspecified_user_terms']) == 6
    checkpoints = out['nonresponse_recovery']['checkpoints']
    assert out['nonresponse_recovery']['checkpoint_count'] == 21
    assert len(checkpoints) == 21
    assert set(c['milestone'] for c in checkpoints) == {'prototype', 'beta', 'completed_application'}
    assert set(c['unavailable_role'] for c in checkpoints) == {'payer', 'worker', 'arbitrator'}
    assert out['emission_authorized'] is False
    assert out['proof_scope'] == 'STATIC_REQUIREMENT_SURFACES_NOT_REACHABLE_STATE_FORMAL_PROOF'


def test_same_system_audit_for_incomplete_complete_and_losy_head(tmp_path):
    full = json.dumps({'status':'needs_clarification','questions':[
        'Contract Language v2 cannot represent progressive milestone payments.',
        'An arbitrator must handle rejections and disputes; appointment remains unknown.',
        'Milestone deadlines and seven-day extension rules remain unspecified.',
        'A silent payer, worker or arbitrator may prevent recovery; define liveness fallback and timeout.'
    ]})
    candidate = next(c['expected'] for c in pull_smoke.cases() if c['id'] == 'safe_reversible_split')
    outcomes = [LIVE_0002, full, json.dumps({'status':'contract','contract':candidate})]
    reports = [case.run(case.SpecialOptions(output=tmp_path/f'{i}.json'), head=lambda _req, r=r: r)
               for i, r in enumerate(outcomes)]
    assert [r['translation']['decision'] for r in reports] == [
        'INCOMPLETE_REFUSAL','CORRECT_BOUNDED_REFUSAL','UNSAFE_ACCEPTED_UNREPRESENTABLE']
    assert [r['status'] for r in reports] == [
        'BLOCKED_NOT_VERIFIED','PASS_INJECTED_OFFLINE_ONLY','BLOCKED_NOT_VERIFIED']
    assert all(r['independent_system_audit'] == reports[0]['independent_system_audit'] for r in reports)
    assert all(r['emission_gate'] == 'BLOCKED_BY_SYSTEM_NOT_LLM' for r in reports)
    assert all(r['forge'] == 'NOT_RUN_UNSUPPORTED_REQUEST' for r in reports)
    assert all(r['stages']['system_completeness'] == 'PASS_FAIL_CLOSED_UNREPRESENTABLE_AND_RECOVERY_UNRESOLVED' for r in reports)


@pytest.mark.parametrize('tamper', [
    lambda x: x['milestones'][0].update(wei=42),
    lambda x: x['milestones'].pop(),
    lambda x: x['required_capabilities'].pop(),
    lambda x: x['required_capabilities'][0].update(v2='supported'),
    lambda x: x.update(deposit_wei=True),
])
def test_invalid_pinned_requirements_are_rejected(tamper):
    f = copy.deepcopy(case.load_fixture())
    tamper(f)
    with pytest.raises(base.SmokeFailure):
        audit.evaluate_milestone_requirements(f, case.capability_ceiling())


@pytest.mark.parametrize('field,value', [
    ('language','nanojev.contract.state_machine.v3'),
    ('partial_nonterminal_disbursement','SUPPORTED'),
    ('single_deadline_seconds_field', False),
    ('milestone_schema_extension','ACCEPTED'),
    ('allowed_roles',['payer','worker','reviewer','arbitrator']),
])
def test_unverified_compiler_ceiling_refuses_to_grade(field,value):
    ceiling = {**case.capability_ceiling(),field:value}
    with pytest.raises(base.SmokeFailure):
        audit.evaluate_milestone_requirements(case.load_fixture(), ceiling)


def test_existing_v2_graph_passes_or_fails_actual_reachability():
    orig = next(x['expected'] for x in pull_smoke.cases() if x['id']=='unsafe_work_review')
    safe = next(x['expected'] for x in pull_smoke.cases() if x['id']=='safe_review_workflow')
    bad = audit.evaluate_existing_v2_graph(orig)
    good = audit.evaluate_existing_v2_graph(safe)
    assert bad['status'] == 'FAIL_TRAPPED_FUNDS_RISK'
    assert bad['emission_authorized_by_liveness'] is False
    assert any(t['unavailable_role']=='payer' for t in bad['single_role_traps'])
    assert good['status'] == 'PASS_CONDITIONAL'
    assert good['emission_authorized_by_liveness'] is True
    assert good['single_role_traps'] == []
    assert good['recipient_acceptance'] == 'NOT_REQUIRED_TO_SETTLE_V2_CREDITED_PAYMENTS'


def test_live_0002_replay_no_llm_and_original_unmodified(tmp_path):
    source=tmp_path/'live_0002.json'
    make_live_source(source)
    original=source.read_bytes()
    dest=tmp_path/'audit_0003.json'
    result=case.run(case.SpecialOptions(output=dest,audit_from_report=source))
    assert result['status']=='BLOCKED_NOT_VERIFIED'
    assert result['mode']=='REPORT_REPLAY_NO_LLM'
    assert result['stages']['head']=='PASS_REPLAYED_LIVE_OUTPUT_NO_MODEL'
    assert result['translation']['missing_topics']==['liveness_or_recovery']
    assert result['translation']['explicit_v2_limitation_acknowledged'] is True
    assert result['independent_system_audit']['nonresponse_recovery']['checkpoint_count']==21
    assert result['stages']['system_completeness'].startswith('PASS_FAIL_CLOSED')
    assert result['replayed_source_sha256']==base.digest(original)
    assert source.read_bytes()==original
    assert json.loads(dest.read_text())['mode']=='REPORT_REPLAY_NO_LLM'
    assert result['allow_solidity'] is False


def test_replay_requires_live_source_and_consistent_hashes(tmp_path):
    source=tmp_path/'source.json'
    base_doc=make_live_source(source)
    alterations=[
        {'mode':'OFFLINE_INJECTED'},
        {'head_response_sha256':'f'*64},
        {'fixture_sha256':'0'*64},
        {'case_id':'other'},
        {'compiler_sha256':'0'*64},
        {'request_sha256':'0'*64},
        {'stages':{'head':'PASS_INJECTED'}},
    ]
    for i,mod in enumerate(alterations):
        source.write_text(json.dumps({**base_doc,**mod}),encoding='utf-8')
        output=tmp_path/f'out_{i}.json'
        got=case.run(case.SpecialOptions(output=output,audit_from_report=source))
        assert got['status']=='BLOCKED_NOT_VERIFIED'
        assert got['blockers'][0]['stage']=='preflight'
        assert not got.get('translation')
    source.write_text(json.dumps(base_doc),encoding='utf-8')
    same=case.run(case.SpecialOptions(output=source,audit_from_report=source))
    assert same['status']=='BLOCKED_NOT_VERIFIED'
    assert same['blockers'][0]['stage']=='preflight'


def test_cli_replay_mode_is_no_network_and_sets_failure_exit_code(tmp_path):
    source=tmp_path/'case.json'
    make_live_source(source)
    output=tmp_path/'audit.json'
    cmd=[sys.executable,'-m','tools.nanojev_contract_pull_smoke',
         '--special-case','milestone_escrow','--audit-from-report',str(source),
         '--output',str(output)]
    p=subprocess.run(cmd,text=True,capture_output=True,timeout=15)
    assert p.returncode == 1, p.stderr
    assert 'no model call' in p.stdout
    data=json.loads(output.read_text())
    assert data['status']=='BLOCKED_NOT_VERIFIED'
    assert data['independent_system_audit']['head_independent'] is True
    assert data['forge']=='NOT_RUN_UNSUPPORTED_REQUEST'


def test_cli_normal_mode_rejects_replay_option(tmp_path):
    p=subprocess.run([sys.executable,'-m','tools.nanojev_contract_pull_smoke',
         '--audit-from-report',str(tmp_path/'other.json'),
         '--output',str(tmp_path/'out.json')],text=True,capture_output=True,timeout=15)
    assert p.returncode != 0
    assert '--audit-from-report requires' in p.stderr


def test_audit_can_proceed_without_llm_in_dry_run(tmp_path):
    report=case.run(case.SpecialOptions(output=tmp_path/'plan.json',dry_run=True),
                    head=lambda _: (_ for _ in ()).throw(AssertionError('LLM must not run')))
    assert report['status']=='PLAN_ONLY_NOT_EXECUTED'
    assert report['independent_system_audit']['emission_authorized'] is False
    assert not (tmp_path/'plan.json').exists()
