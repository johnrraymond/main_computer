"""Special milestone escrow test is a refusal/expressiveness test, NOT a Forge pass."""
from __future__ import annotations
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from tools import nanojev_contract_milestone_escrow_case as challenge
from tools import nanojev_contract_pull_smoke as smoke
from tools import nanojev_contract_pull_payments as compiler
from tools.nanojev_contract_solidity_smoke import SmokeFailure


RESPONSE = json.dumps({'status':'needs_clarification','questions':[
    'NanoJev Contract Language v2 cannot represent three progressive milestone payment releases from one escrow: prototype, beta and final need separate partial entitlements.',
    'The dispute resolution and mutually appointed arbitrator are not supported as an arbitration workflow or appointment state.',
    'What are the separate milestone deadlines and the exact conditions for granting each seven-day extension?',
    'What are the liveness recovery timeouts if the client, developer or arbitrator stops responding?'
]})


def test_fixture_is_pinned_has_conserved_accounting_and_explicit_limitations():
    f = challenge.load_fixture()
    assert f['deposit_wei'] == 12*10**18
    assert [m['wei'] for m in f['milestones']] == [3*10**18,4*10**18,5*10**18]
    assert sum(m['wei'] for m in f['milestones']) == f['deposit_wei']
    assert {x['id'] for x in f['required_capabilities'] if x['v2']=='unsupported'} >= {
        'progressive_partial_disbursement','per_milestone_deadlines',
        'discretionary_milestone_extension','rejection_and_dispute_workflow',
        'arbitrator_appointment_and_authority','unearned_balance_refund',
        'dispute_nonresponse_recovery'}
    assert challenge.capability_ceiling()['partial_nonterminal_disbursement']=='NOT_SUPPORTED'


def test_correct_detailed_refusal_passes_but_not_as_compiled_contract(tmp_path):
    calls=[]
    def head(request):
        calls.append(request)
        return RESPONSE
    path=tmp_path/'result.json'
    report=challenge.run(challenge.SpecialOptions(output=path),head=head)
    assert report['status']=='PASS_INJECTED_OFFLINE_ONLY',report.get('blockers')
    assert len(calls)==1
    assert calls[0] == challenge.load_fixture()['request']
    assert 'PIN' not in calls[0]
    assert report['forge']=='NOT_RUN_UNSUPPORTED_REQUEST'
    assert report['allow_solidity'] is False
    assert report['stages']['semantic_refusal']=='CORRECT_BOUNDED_REFUSAL'
    assert report['translation']['missing_topics']==[]
    assert not (tmp_path/'result.workspace').exists()
    assert json.loads(path.read_text())['status']=='PASS_INJECTED_OFFLINE_ONLY'


def test_brief_generic_refusal_does_not_masquerade_as_success(tmp_path):
    got=challenge.run(challenge.SpecialOptions(output=tmp_path/'bad.json'),
        head=lambda req:json.dumps({'status':'needs_clarification','questions':['Please clarify contract terms.']}))
    assert got['status']=='BLOCKED_NOT_VERIFIED'
    assert got['translation']['decision']=='INCOMPLETE_REFUSAL'
    assert got['blockers'][0]['stage']=='semantic_refusal'


def test_refusal_missing_one_topic_does_not_pass():
    doc=json.loads(RESPONSE)
    doc['questions'].pop()
    check=challenge.classify_response(json.dumps(doc),challenge.load_fixture())
    assert check['decision']=='INCOMPLETE_REFUSAL'
    assert 'liveness_or_recovery' in check['missing_topics']


def test_lossy_but_valid_v2_contract_is_never_accepted(tmp_path):
    known=next(x['expected'] for x in smoke.cases() if x['id']=='safe_reversible_split')
    assert compiler.validate(known)
    got=challenge.run(challenge.SpecialOptions(output=tmp_path/'bad.json'),
        head=lambda req:json.dumps({'status':'contract','contract':known}))
    assert got['status']=='BLOCKED_NOT_VERIFIED'
    assert got['translation']['decision']=='UNSAFE_ACCEPTED_UNREPRESENTABLE'
    assert got['translation']['v2_validation']=='VALID_V2_BUT_LOSSY_FOR_REQUEST'
    assert got['allow_solidity'] is False


@pytest.mark.parametrize('wrong',[
    'not json',
    '[]',
    json.dumps({'status':'needs_clarification','questions':[]}),
    json.dumps({'status':'contract','contract':{'language':'v2'}}),
])
def test_malformed_or_unsafe_head_replies_blocked(tmp_path,wrong):
    got=challenge.run(challenge.SpecialOptions(output=tmp_path/'bad.json'),head=lambda r:wrong)
    assert got['status']=='BLOCKED_NOT_VERIFIED'
    assert got['blockers']
    assert got['allow_solidity'] is False


def test_dry_run_never_invokes_llm_and_does_not_write(tmp_path):
    def head(_):raise AssertionError('not called')
    report=challenge.run(challenge.SpecialOptions(output=tmp_path/'plan.json',dry_run=True),head=head)
    assert report['status']=='PLAN_ONLY_NOT_EXECUTED'
    assert not (tmp_path/'plan.json').exists()
    assert 'milestone' in report['request'].lower()


def test_existing_report_not_overwritten(tmp_path):
    p=tmp_path/'report.json'
    p.write_text('SENTINEL')
    got=challenge.run(challenge.SpecialOptions(output=p),head=lambda r:RESPONSE)
    assert got['status']=='BLOCKED_NOT_VERIFIED'
    assert got['blockers'][0]['stage']=='preflight'
    assert p.read_text()=='SENTINEL'


def test_existing_default_runner_entrypoint_has_special_case_and_normal_unchanged():
    source=Path(smoke.__file__).read_text()
    assert "p.add_argument('--special-case'" in source
    assert 'r=run(Options(' in source
    cmd=[sys.executable,'-m','tools.nanojev_contract_pull_smoke',
         '--special-case','milestone_escrow','--dry-run',
         '--output','runtime/reports/unused-for-dry-run.json']
    proc=subprocess.run(cmd,text=True,capture_output=True,timeout=15)
    assert proc.returncode==0,proc.stderr
    assert json.loads(proc.stdout)['status']=='PLAN_ONLY_NOT_EXECUTED'


def test_all_topics_but_no_explicit_v2_limit_is_not_green():
    doc=json.loads(RESPONSE)
    doc['questions']=[
      'What are the three milestone due dates for prototype, beta and final?',
      'Who is the arbitrator for rejected work and disputes?',
      'How long are seven-day extensions?',
      'What recovery timeout applies if someone refuses to respond?'
    ]
    check=challenge.classify_response(json.dumps(doc),challenge.load_fixture())
    assert check['missing_topics']==[]
    assert check['explicit_v2_limitation_acknowledged'] is False
    assert check['decision']=='INCOMPLETE_REFUSAL'


def test_actual_prompt_supplies_generic_assurance_review_without_fixture_gold():
    prompt = challenge.SPECIAL_PROMPT
    assert prompt.startswith(smoke.PROMPT)
    assert 'cannot represent/compile' in prompt
    assert 'failure to respond can trap held assets' in prompt
    assert 'unresolved liveness/recovery rule' in prompt
    assert challenge.load_fixture()['request'] not in prompt
    assert 'prototype' not in challenge.ASSURANCE_REVIEW.lower()
    assert '12 ETH' not in challenge.ASSURANCE_REVIEW


def test_special_case_live_head_uses_audited_prompt_without_changing_normal_head(tmp_path, monkeypatch):
    calls=[]
    import io
    def fake_urlopen(req, timeout):
        message = json.loads(req.data.decode('utf-8'))['messages']
        calls.append(message)
        return io.BytesIO(json.dumps({'message':{'content':RESPONSE}}).encode('utf-8'))
    monkeypatch.setattr(smoke, 'urlopen', fake_urlopen)
    result=challenge.run(challenge.SpecialOptions(output=tmp_path/'special.json'))
    assert result['status']=='PASS_UNSUPPORTED_REFUSED_NOT_CERTIFIED'
    assert result['head_prompt_sha256']==smoke.base.digest(challenge.SPECIAL_PROMPT)
    assert result['review_guidance']=='GENERIC_V2_ASSURANCE_CHECKLIST_ASSISTED_NOT_BLIND'
    assert calls[0][0]['content']==challenge.SPECIAL_PROMPT
    assert calls[0][1]['content']==challenge.load_fixture()['request']
    assert 'milestone_amounts_wei' not in calls[0][0]['content']

    raw = smoke.real_head(smoke.base.DEFAULT_URL,smoke.base.DEFAULT_MODEL,'A test request',10)
    assert raw == RESPONSE
    assert calls[1][0]['content']==smoke.PROMPT
    assert calls[1][1]['content']=='A test request'


def test_exact_live_report_failure_identifies_both_missing_properties(tmp_path):
    real_questions=[
        'What are the exact deadline durations (in seconds) for each of the three milestones?',
        "What is the specific evidence or action required to trigger the 'accepted' state for prototype, beta and completed work?",
        'What is the procedure for appointing the arbitrator?',
        'What is the time limit for the arbitrator to resolve a dispute?',
        "What is the required action or evidence for the developer to dispute a rejected milestone?",
        'What evidence is required for the payer to grant seven more days?'
    ]
    raw=json.dumps({'status':'needs_clarification','questions':real_questions})
    report=challenge.run(challenge.SpecialOptions(output=tmp_path/'case.json'),head=lambda _:raw)
    assert report['status']=='BLOCKED_NOT_VERIFIED'
    assert report['translation']['missing_topics']==['liveness_or_recovery']
    assert not report['translation']['explicit_v2_limitation_acknowledged']
    reason=report['blockers'][0]['reason']
    assert 'liveness_or_recovery' in reason
    assert 'expressiveness limits' in reason
    assert report['forge']=='NOT_RUN_UNSUPPORTED_REQUEST'


def test_only_liveness_mentioned_without_v2_limit_still_blocked(tmp_path):
    qs={'status':'needs_clarification','questions':[
        'What are the three milestone payment criteria?',
        'Who handles disputes, rejection, and appointment of the arbitrator?',
        'What are the dates and seven day extension rules?',
        'What recovery timeout applies if a party refuses to respond?'
    ]}
    # Covers each topical group but does not acknowledge a schema limitation.
    got=challenge.run(challenge.SpecialOptions(output=tmp_path/'no_gap.json'),
                      head=lambda _:json.dumps(qs))
    assert got['status']=='BLOCKED_NOT_VERIFIED'
    assert got['translation']['missing_topics']==[]
    assert got['blockers'][0]['reason'].endswith('Contract Language v2 expressiveness limits')


def test_party_cannot_respond_is_not_a_contract_language_limitation():
    qs = {'status':'needs_clarification','questions':[
        'What are the three milestone partial payment rules?',
        'Who is the arbitrator for disputes and rejections?',
        'What is the deadline and seven-day extension rule?',
        'If the arbitrator cannot respond, what is the liveness recovery timeout?'
    ]}
    result=challenge.classify_response(json.dumps(qs),challenge.load_fixture())
    assert result['missing_topics']==[]
    assert result['explicit_v2_limitation_acknowledged'] is False
    assert result['decision']=='INCOMPLETE_REFUSAL'


def test_schema_specific_limit_counts_when_all_topics_are_present():
    qs={'status':'needs_clarification','questions':[
        'Contract Language v2 does not support successive partial milestone disbursement.',
        'Who acts as the arbitrator to decide disputes and rejections?',
        'What are the multiple deadlines and extension rules?',
        'What recovery timeouts prevent a nonresponse deadlock?'
    ]}
    result=challenge.classify_response(json.dumps(qs),challenge.load_fixture())
    assert result['missing_topics']==[]
    assert result['explicit_v2_limitation_acknowledged']
    assert result['decision']=='CORRECT_BOUNDED_REFUSAL'
