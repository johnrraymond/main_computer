"""Offline fault injections and exhaustive symbolic checks; not real Forge proof."""
import copy
import json
import re
from pathlib import Path

import pytest

from tools import nanojev_contract_liveness as liv
from tools import nanojev_contract_liveness_smoke as smoke
from tools import nanojev_contract_state_machine as machine
from tools import nanojev_contract_composition_smoke as comp
from tools.nanojev_contract_solidity_smoke import SmokeFailure


@pytest.fixture
def challenges():
    cases=smoke.prepare_cases()
    return {c['id']:c for c in cases}


def baselines(tmp_path):
    expansion=tmp_path/'expansion.json'
    expansion.write_text(json.dumps({'status':'PASS_SMOKE_NOT_CERTIFIED',
        'expected_foundry_tests':56,'stages':{'forge_build':{'exit_code':0},
        'forge_test':{'exit_code':0},'forge_tests_passed':56,'compiled_artifacts':'PASS'}}))
    composition=tmp_path/'composition.json'
    composition.write_text(json.dumps({'status':'PASS_SMOKE_NOT_CERTIFIED',
        'compiler_only':False,'fixture_sha256':comp.PIN,'blockers':[],
        'expected_foundry_tests':105,'stages':{
            'head':'PASS_LIVE','semantic_oracle':'PASS_6_POSITIVE_4_REFUSAL',
            'forge_build':{'exit_code':0},'forge_test':{'exit_code':0},
            'forge_tests_passed':105,'compiled_artifacts':'PASS'},
        'cases':[{'id':'reversible_split_base','result':'EXACT_GOLD_MATCH'},
                 {'id':'review_workflow_base','result':'EXACT_GOLD_MATCH'}]}))
    return expansion,composition


@pytest.mark.parametrize('name,role,state,phase',[
    ('unsafe_reversible_split','reviewer','funded','at_or_after_deadline'),
    ('unsafe_work_review','payer','submitted','at_or_after_deadline'),
    ('unsafe_work_review','payer','funded','at_or_after_deadline'),
])
def test_exposes_expected_lock_counterexample(challenges,name,role,state,phase):
    risk=liv.evaluate(challenges[name]['expected'])
    assert risk['status']=='FAIL_TRAPPED_FUNDS_RISK'
    assert any(x['unavailable_role']==role and x['state']==state and x['phase']==phase
               for x in risk['single_role_traps'])


@pytest.mark.parametrize('name',[
    'safe_reversible_split','safe_reversible_paraphrase',
    'safe_review_workflow','safe_review_duration_change'])
def test_remediation_covers_all_reachable_nodes_and_roles(challenges,name):
    gold=challenges[name]['expected']
    result=liv.evaluate(gold)
    assert result['status']=='PASS_CONDITIONAL'
    assert result['checked_role_unavailability_scenarios']==3*result['checked_reachable_nodes']
    assert not result['single_role_traps'] and not result['unconditional_traps']
    for node in liv.reachable(machine.validate(gold)):
        for blocked in liv.ROLES:
            trace=liv.recovery_path(gold,node,blocked)
            assert trace is not None
            assert trace[-1] != 'wait_until_deadline'
    assert result['receiver_refusal_residual_risk']['status']=='NOT_PROVED_SAFE'


def test_state_time_guard_semantics(challenges):
    spec=challenges['safe_review_workflow']['expected']
    assert liv.enabled('before_deadline','before_deadline')
    assert not liv.enabled('before_deadline','at_or_after_deadline')
    assert liv.enabled('at_or_after_deadline','at_or_after_deadline')
    assert not liv.enabled('at_or_after_deadline','before_deadline')
    assert liv.enabled('always','before_deadline') and liv.enabled('always','at_or_after_deadline')
    path=liv.reachable(spec)[('submitted','at_or_after_deadline')]
    assert path==['submit','wait_until_deadline']


def test_deadline_sealed_unconditional_trap(challenges):
    spec=copy.deepcopy(challenges['safe_review_workflow']['expected'])
    # The following changes are valid syntactically but leave submitted stuck after deadline:
    for rule in spec['rules']:
        if rule['name'] in ('approve','fallback_approve'):
            rule['when']='before_deadline'
    r=liv.evaluate(spec)
    assert r['unconditional_traps']
    assert any(x['state']=='submitted' and x['phase']=='at_or_after_deadline'
               for x in r['unconditional_traps'])


@pytest.mark.parametrize('mutation',[
    lambda s: s['rules'].__setitem__(next(i for i,r in enumerate(s['rules']) if r['name']=='rescue_release'),
        {**next(r for r in s['rules'] if r['name']=='rescue_release'),'actor':'reviewer'}),
    lambda s: s['rules'].__setitem__(next(i for i,r in enumerate(s['rules']) if r['name']=='rescue_release'),
        {**next(r for r in s['rules'] if r['name']=='rescue_release'),'when':'before_deadline'}),
])
def test_removing_fallback_protection_detected(challenges,mutation):
    spec=copy.deepcopy(challenges['safe_reversible_split']['expected'])
    mutation(spec)
    assert liv.evaluate(spec)['status']=='FAIL_TRAPPED_FUNDS_RISK'


def test_generated_forge_tests_include_recovery_witnesses(challenges):
    for case in challenges.values():
        if case['family']!='safe_positive':
            continue
        text, count=liv.forge_recovery_tests(case['expected'],'NanoJevComposition01')
        assert count==3*liv.evaluate(case['expected'])['checked_reachable_nodes']
        assert text.count('function testRecovery')==count
        assert text.count('funds remained locked')==count
        assert 'function testFail' not in text


def test_correct_semantic_drift_is_not_safety_pass(challenges):
    case=challenges['safe_reversible_split']
    wrong=copy.deepcopy(case['expected'])
    wrong['amount_wei']+=1
    with pytest.raises(SmokeFailure) as exc:
        smoke.validate_llm(case,json.dumps({'status':'contract','contract':wrong}))
    assert exc.value.stage=='semantic_gold'


def test_wrong_recovery_authorization_refused(challenges):
    case=challenges['safe_reversible_split']
    wrong=copy.deepcopy(case['expected'])
    next(r for r in wrong['rules'] if r['name']=='rescue_release')['actor']='reviewer'
    with pytest.raises(SmokeFailure):
        smoke.validate_llm(case,json.dumps({'status':'contract','contract':wrong}))
    assert liv.evaluate(wrong)['status']=='FAIL_TRAPPED_FUNDS_RISK'


def test_ambiguity_cannot_be_misclassified_as_contract(challenges):
    case=challenges['ambiguous_recovery']
    with pytest.raises(SmokeFailure):
        smoke.validate_llm(case,json.dumps({'status':'contract','contract':challenges['safe_review_workflow']['expected']}))
    with pytest.raises(SmokeFailure):
        smoke.validate_llm(case,json.dumps({'status':'needs_clarification','questions':['Need more details.']}))
    assert smoke.validate_llm(case,json.dumps({'status':'needs_clarification',
        'questions':['Who may authorize the fallback and at what deadline should it execute?']})) is None


def test_bad_baseline_refused(tmp_path):
    expansion,composition=baselines(tmp_path)
    bad=json.loads(composition.read_text());bad['stages']['head']='PASS_INJECTED'
    composition.write_text(json.dumps(bad))
    opts=smoke.Options(output=tmp_path/'out.json',composition_report=composition,expansion_report=expansion)
    report=smoke.run(opts,docker_available=True)
    assert report['status']=='BLOCKED_NOT_VERIFIED'
    assert report['blockers'][0]['stage']=='preflight'


def test_baseline_success_never_means_independent_gold_verified(tmp_path,challenges):
    expansion,composition=baselines(tmp_path)
    requests={c['request']:c for c in challenges.values()}
    def head(request):
        case=requests[request]
        if case['family']=='needs_clarification':
            return json.dumps({'status':'needs_clarification',
                'questions':['Who is authorized to settle? What deadline and payout applies?']})
        return json.dumps({'status':'contract','contract':case['expected']})
    count_holder={}
    def docker(args,timeout):
        if 'test' in args:
            out=tmp_path/'endtoend.workspace'/'test'
            count=sum(len(re.findall(r'\bfunction\s+test\w*\s*\(',f.read_text()))
                      for f in out.glob('*.t.sol'))
            count_holder['count']=count
            return {'exit_code':0,'stdout_tail':
                f'Ran 4 test suites in 15.0ms: {count} tests passed, 0 failed, 0 skipped ({count} total tests)'}
        return {'exit_code':0,'stdout_tail':'Compiler run successful!'}
    report=smoke.run(smoke.Options(output=tmp_path/'endtoend.json',
        composition_report=composition,expansion_report=expansion),
        head=head,docker=docker,docker_available=True)
    assert report['status']=='PASS_INJECTED_OFFLINE_TEST_ONLY',report['blockers']
    assert count_holder['count']==138
    assert report['expected_foundry_tests']==138
    assert len(report['generated'])==4
    assert len([c for c in report['cases'] if c['policy_action']=='BLOCK_SOLIDITY_EMISSION_TRAPPED_FUNDS'])==2
    assert report['stages']['independent_semantic_correctness']=='NOT_ESTABLISHED_ASSISTANT_AUTHORED_GOLD'
    assert report['stages']['compiled_artifacts']=='INJECTED_NO_ARTIFACT_VERIFICATION'
    assert all('gold_excluded_from_head' in c and c['gold_excluded_from_head'] for c in report['cases'])


def test_mocked_forge_failure_retains_blocker(tmp_path):
    expansion,composition=baselines(tmp_path)
    def docker(args,timeout):
        return {'exit_code':2,'stdout_tail':'real compiler failure'}
    report=smoke.run(smoke.Options(output=tmp_path/'out.json',
        composition_report=composition,expansion_report=expansion,compiler_only=True),
        docker=docker,docker_available=True)
    assert report['status']=='BLOCKED_NOT_VERIFIED'
    assert report['blockers'][0]['stage']=='forge_build'
    assert report['stages']['forge_build']['exit_code']==2


def test_load_external_gold_and_mislabeled_rejected(tmp_path,challenges):
    good={'id':'external_safe_one','family':'safe_positive','request':
          challenges['safe_review_workflow']['request'],
          'expected':challenges['safe_review_workflow']['expected']}
    unsafe={'id':'external_unsafe_one','family':'unsafe_compilable','request':
            challenges['unsafe_work_review']['request'],
            'expected':challenges['unsafe_work_review']['expected']}
    file=tmp_path/'external.json'
    file.write_text(json.dumps({'schema':'nanojev.contract-liveness.external.v1','cases':[good,unsafe]}))
    items,sha=smoke.load_independent(file,set(challenges))
    assert len(items)==2 and len(sha)==64
    unsafe['family']='safe_positive'
    file.write_text(json.dumps({'schema':'nanojev.contract-liveness.external.v1','cases':[good,unsafe]}))
    with pytest.raises(SmokeFailure):
        smoke.load_independent(file,set(challenges))


def test_dry_run_does_not_infer_llm_or_write(tmp_path):
    expansion,composition=baselines(tmp_path)
    dest=tmp_path/'out.json'
    report=smoke.run(smoke.Options(output=dest,composition_report=composition,
                                    expansion_report=expansion,dry_run=True))
    assert report['status']=='PLAN_ONLY_NOT_EXECUTED',report['blockers']
    assert not dest.exists()


def test_injected_compiler_only_never_claims_real_forge(tmp_path):
    expansion,composition=baselines(tmp_path)
    def fake_forge(args,timeout):
        if 'test' in args:
            return {'exit_code':0,'stdout_tail':
                'Ran 4 test suites in 20.0ms: 138 tests passed, 0 failed, 0 skipped (138 total tests)'}
        return {'exit_code':0,'stdout_tail':'Compiler run successful!'}
    report=smoke.run(smoke.Options(output=tmp_path/'injected.json',
        composition_report=composition,expansion_report=expansion,compiler_only=True),
        docker=fake_forge,docker_available=True)
    assert report['status']=='PASS_INJECTED_OFFLINE_TEST_ONLY',report['blockers']
    assert report['stages']['independent_semantic_correctness']=='NOT_TESTED_COMPILER_ONLY'
    assert report['stages']['compiled_artifacts']=='INJECTED_NO_ARTIFACT_VERIFICATION'


@pytest.mark.parametrize('summary',[
    'Ran 4 test suites in 20.0ms: 137 tests passed, 1 failed, 0 skipped (138 total tests)',
    'Ran 4 test suites in 20.0ms: 137 tests passed, 0 failed, 1 skipped (138 total tests)',
    'No tests run',
])
def test_forge_false_success_is_rejected(tmp_path,summary):
    expansion,composition=baselines(tmp_path)
    def fake_forge(args,timeout):
        if 'test' in args:
            return {'exit_code':0,'stdout_tail':summary}
        return {'exit_code':0,'stdout_tail':'Compiler run successful!'}
    report=smoke.run(smoke.Options(output=tmp_path/'bad_forge.json',
        composition_report=composition,expansion_report=expansion,compiler_only=True),
        docker=fake_forge,docker_available=True)
    assert report['status']=='BLOCKED_NOT_VERIFIED'
    assert report['blockers'][0]['stage']=='forge_test'
