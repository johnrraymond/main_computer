"""Regression for a real live-model error: settlement attached to a nonterminal rule.

No live Ollama calls or Forge compilation: test the strict oracle, bounded
model correction, failure isolation and report provenance with injected heads.
"""
import copy
import json
import re

import pytest
from tools.nanojev_contract_solidity_smoke import SmokeFailure
from tools import nanojev_contract_pull_smoke as smoke
from tools import nanojev_contract_pull_payments as pull
from test_nanojev_contract_pull_smoke import baselines


def case():
    return next(c for c in smoke.cases() if c['id']=='unsafe_work_review')


def submit_paid_candidate(gold):
    bad=copy.deepcopy(gold)
    action=next(r for r in bad['rules'] if r['to']=='submitted')
    assert action['payments']==[]
    action['payments']=[{'to':'worker','bps':10000}]
    return json.dumps({'status':'contract','contract':bad})


def good_candidate(c):
    return json.dumps({'status':'contract','contract':c['expected']})


def test_live_error_reproduced_validator_unchanged():
    c=case()
    invalid=submit_paid_candidate(c['expected'])
    with pytest.raises(SmokeFailure,match='Transitions after settlement forbidden') as err:
        smoke.validate_head(c,invalid)
    assert err.value.stage=='contract_validation'
    assert 'MUST have "payments":[]' in smoke.PROMPT
    assert 'Only transitions entering' in smoke.PROMPT


def test_model_correction_is_reported_and_uses_only_diagnostic():
    c=case()
    invalid=submit_paid_candidate(c['expected'])
    seen=[]
    def fake_head(request):
        seen.append(request)
        return invalid if len(seen)==1 else good_candidate(c)
    entry={'id':c['id']}
    got=smoke.infer_with_bounded_structural_retry(c,fake_head,entry)
    assert got==pull.validate(c['expected'])
    assert len(seen)==2
    assert seen[0]==c['request']
    assert 'Transitions after settlement forbidden' in seen[1]
    assert 'COMPILER DIAGNOSTIC' in seen[1]
    assert '"payments":[]' in seen[1]
    assert invalid in seen[1]
    assert c['request'] in seen[1]
    assert 'gold' not in seen[1].lower()
    assert entry['llm_attempts']==2
    assert entry['first_attempt_validation_error']=='Transitions after settlement forbidden'
    assert entry['first_attempt_output']==invalid
    assert entry['structural_retry_result']=='PASSED_ORIGINAL_VALIDATORS'


def test_repeat_invalid_fails_closed_with_both_outputs():
    c=case()
    invalid=submit_paid_candidate(c['expected'])
    attempts=[]
    def bad_head(request):
        attempts.append(request)
        return invalid
    record={'id':c['id']}
    with pytest.raises(SmokeFailure,match='Transitions after settlement forbidden'):
        smoke.infer_with_bounded_structural_retry(c,bad_head,record)
    assert len(attempts)==2
    assert record['llm_attempts']==2
    assert record['first_attempt_output']==invalid
    assert record['llm_output']==invalid
    assert 'structural_retry_result' not in record


def test_structurally_valid_semantic_drift_is_never_retried():
    c=case()
    wrong=copy.deepcopy(c['expected'])
    wrong['deadline_seconds']+=86400
    attempts=[]
    def fake_head(request):
        attempts.append(request)
        return json.dumps({'status':'contract','contract':wrong})
    with pytest.raises(SmokeFailure) as err:
        smoke.infer_with_bounded_structural_retry(c,fake_head,{'id':c['id']})
    assert err.value.stage=='semantic_gold'
    assert len(attempts)==1


def test_other_structural_errors_do_not_trigger_targeted_retry():
    c=case()
    wrong=copy.deepcopy(c['expected'])
    wrong['rules'][0]['actor']='unrecognized_actor'
    seen=[]
    def fake_head(request):
        seen.append(request)
        return json.dumps({'status':'contract','contract':wrong})
    with pytest.raises(SmokeFailure) as err:
        smoke.infer_with_bounded_structural_retry(c,fake_head,{'id':c['id']})
    assert err.value.stage=='contract_validation'
    assert len(seen)==1


def test_refusal_cannot_be_turned_into_contract_through_retry():
    c=next(c for c in smoke.cases() if c['family']=='needs_clarification')
    wrong=good_candidate(case())
    seen=[]
    def fake_head(request):
        seen.append(request)
        return wrong
    with pytest.raises(SmokeFailure) as err:
        smoke.infer_with_bounded_structural_retry(c,fake_head,{'id':c['id']})
    assert err.value.stage=='semantic_gold'
    assert len(seen)==1


def test_injected_pipeline_retries_once_without_turning_injected_into_live(tmp_path):
    args=baselines(tmp_path)
    examples={x['request']:x for x in smoke.cases()}
    for x in smoke.external_sources():
        examples[x['source_excerpt']]={'family':'needs_clarification'}
    attempt_count=[]
    def fake_head(request):
        if 'COMPILER DIAGNOSTIC' in request:
            assert request.count('COMPILER DIAGNOSTIC')==1
            assert 'Transitions after settlement forbidden' in request
            attempt_count.append(request)
            return good_candidate(case())
        c=examples[request]
        if c.get('family')=='needs_clarification':
            return json.dumps({'status':'needs_clarification',
                               'questions':['Please specify roles, payout values, and deadlines.']})
        if c['id']=='unsafe_work_review':
            return submit_paid_candidate(c['expected'])
        return good_candidate(c)
    def docker(cmd, timeout):
        if 'test' not in cmd:
            return {'exit_code':0,'stdout_tail':'Compiler run successful!'}
        folder=tmp_path/'injected.workspace'/'test'
        n=sum(len(re.findall(r'\bfunction\s+test\w*\s*\(',p.read_text()))
              for p in folder.glob('*.t.sol'))
        return {'exit_code':0,'stdout_tail':
                f'Ran 4 test suites in 14ms: {n} tests passed, 0 failed, 0 skipped ({n} total tests)'}
    report=smoke.run(smoke.Options(output=tmp_path/'injected.json',**args),
                     docker_available=True,head=fake_head,docker=docker)
    assert report['status']=='PASS_INJECTED_OFFLINE_ONLY',report['blockers']
    assert report['expected_foundry_tests']==212
    assert report['stages']['structural_retries']==1
    assert len(attempt_count)==1
    item=next(x for x in report['cases'] if x['id']=='unsafe_work_review')
    assert item['semantic_result']=='EXACT_GOLD_MATCH'
    assert item['policy_action']=='BLOCK_UNSAFE_CONTRACT'
    assert item['llm_attempts']==2
    assert item['first_attempt_validation_error']=='Transitions after settlement forbidden'
    assert report['stages']['head']=='PASS_INJECTED'


def test_pipeline_second_attempt_invalid_cannot_reach_forge(tmp_path):
    args=baselines(tmp_path)
    gold=case()
    def fake_head(request):
        if 'COMPILER DIAGNOSTIC' in request:
            return submit_paid_candidate(gold['expected'])
        base=next((c for c in smoke.cases() if c['request']==request),None)
        if base is None: raise AssertionError('Unexpected prior input')
        if base['id']=='unsafe_work_review':
            return submit_paid_candidate(base['expected'])
        return good_candidate(base)
    called=[]
    def no_docker(cmd,timeout):
        called.append(cmd)
        raise AssertionError('Docker must not be invoked')
    report=smoke.run(smoke.Options(output=tmp_path/'blocked.json',**args),
                    docker_available=True,head=fake_head,docker=no_docker)
    assert report['status']=='BLOCKED_NOT_VERIFIED'
    assert report['stages']['structural_retries']==1
    assert not called
    assert report['blockers'][0]['stage']=='contract_validation'
    item=next(x for x in report['cases'] if x['id']=='unsafe_work_review')
    assert item['llm_attempts']==2
    assert 'first_attempt_output' in item
