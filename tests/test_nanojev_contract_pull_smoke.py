"""Offline tests are NOT evidence of successful local Forge/Docker execution."""
import copy
import json
import re
from pathlib import Path
import pytest
from tools.nanojev_contract_solidity_smoke import SmokeFailure
from tools import nanojev_contract_pull_payments as pull
from tools import nanojev_contract_pull_smoke as smoke
from tools import nanojev_contract_liveness as liveness
from tools import nanojev_contract_state_machine as old
from tools import nanojev_contract_composition_smoke as comp

@pytest.fixture
def cases():
    return {c['id']:c for c in smoke.cases()}

def baselines(root):
    expansion=root/'expansion.json'
    expansion.write_text(json.dumps({'status':'PASS_SMOKE_NOT_CERTIFIED',
        'expected_foundry_tests':56,'stages':{
            'forge_build':{'exit_code':0},'forge_test':{'exit_code':0},
            'forge_tests_passed':56,'compiled_artifacts':'PASS'}}))
    composition=root/'composition.json'
    composition.write_text(json.dumps({'status':'PASS_SMOKE_NOT_CERTIFIED',
        'compiler_only':False,'fixture_sha256':comp.PIN,'blockers':[],
        'expected_foundry_tests':105,'stages':{
            'head':'PASS_LIVE','semantic_oracle':'PASS_6_POSITIVE_4_REFUSAL',
            'forge_build':{'exit_code':0},'forge_test':{'exit_code':0},
            'forge_tests_passed':105,'compiled_artifacts':'PASS'},
        'cases':[{'id':'reversible_split_base','result':'EXACT_GOLD_MATCH'},
                 {'id':'review_workflow_base','result':'EXACT_GOLD_MATCH'}]}))
    live=root/'live.json'
    live.write_text(json.dumps({'status':'PASS_SMOKE_NOT_CERTIFIED','compiler_only':False,
        'expected_foundry_tests':138,'blockers':[],
        'stages':{'head':'PASS_LIVE','forge_tests_passed':138,
        'compiled_artifacts':'PASS', 'liveness_gate':'PASS_UNSAFE_BLOCKED_SAFE_CONDITIONAL',
        'forge_build':{'exit_code':0},'forge_test':{'exit_code':0}}}))
    return dict(liveness_report=live,composition_report=composition,expansion_report=expansion)

@pytest.mark.parametrize('cid',('safe_reversible_split','safe_reversible_paraphrase',
                                'safe_review_workflow','safe_review_duration_change'))
def test_v2_ir_v1_unchanged_and_liveness(cases,cid):
    gold=cases[cid]['expected']
    assert gold['language']==pull.LANG
    assert gold['settlement_mode']=='pull'
    assert old.validate(pull.old(gold))['language']==old.LANG
    assert liveness.evaluate(pull.old(gold))['status']=='PASS_CONDITIONAL'

@pytest.mark.parametrize('cid',('unsafe_reversible_split','unsafe_work_review'))
def test_original_unsafe_v2_not_silently_accepted(cases,cid):
    gold=cases[cid]['expected']
    assert liveness.evaluate(pull.old(gold))['status']=='FAIL_TRAPPED_FUNDS_RISK'

@pytest.mark.parametrize('bad',[
    lambda d:d.pop('settlement_mode'),
    lambda d:d.__setitem__('settlement_mode','push'),
    lambda d:d.__setitem__('language',old.LANG),
    lambda d:d['rules'][0].__setitem__('name','withdraw'),
    lambda d:d['rules'][0].__setitem__('name','withdraw_to'),
    lambda d:d['rules'][0].__setitem__('name','withdraw_attack'),
])
def test_v2_refuses_wrong_modes_reserved_action(cases,bad):
    spec=copy.deepcopy(cases['safe_reversible_split']['expected'])
    bad(spec)
    with pytest.raises(SmokeFailure):pull.validate(spec)

@pytest.mark.parametrize('cid',('safe_reversible_split','safe_review_workflow'))
def test_v2_source_has_credit_isolation(cases,cid):
    text=pull.source(cases[cid]['expected'],'NanoJevPull01')
    assert 'pendingWithdrawals[' in text
    assert 'totalPending' in text
    assert 'withdrawTo(address payable destination)' in text
    assert 'withdrawalLock = true;' in text
    assert 'pendingWithdrawals[msg.sender] = 0;' in text
    assert 'withdrawalLock = false;' in text
    assert text.count('.call{value:') == 1  # only withdrawal, NEVER settlement
    assert 'require(ok, "withdrawal failed");' in text
    assert 'emit CreditAssigned(' in text
    assert not any('transfer failed' in line for line in text.splitlines())
    assert 'function withdraw()' in text
    assert not 'function test' in text


def test_gold_is_not_modified_when_emitting(cases):
    original=copy.deepcopy(cases['safe_reversible_split']['expected'])
    pull.source(original,'NanoJevPull01')
    tests,count=pull.tests(original,'NanoJevPull01')
    assert original==cases['safe_reversible_split']['expected']
    assert count==len(re.findall(r'\bfunction\s+test\w*\s*\(',tests))
    assert 'testRejectorCannotBlockSettlement' in tests
    assert 'recipient.claimTo' in tests
    assert 'double withdrawal' in tests
    assert 'priorPending' in tests or 'sum credits unequal deposit' in tests
    assert 'testFail' not in tests


def test_refusing_receiver_has_independent_claimant_and_alternate_receiver(cases):
    test,n=pull.tests(cases['safe_reversible_split']['expected'],'NanoJevPull01')
    assert 'receive() external payable { revert("reject ETH"); }' in test
    assert 'recipient.attemptWithdraw' in test
    assert 'recipient.claimTo(address(c), DESTINATION)' in test
    assert 'other payee blocked' in test


def test_external_published_source_metadata_and_three_assertions(cases):
    sources=smoke.external_sources()
    assert len(sources)==3
    assert len({s['source_url'] for s in sources})==3
    assert all(s['llm_expectation']=='needs_clarification' for s in sources)
    t,n=smoke.external_tests(cases['safe_reversible_split']['expected'],'NanoJevPull01')
    assert n==3
    assert {s['forge_test'] for s in sources}<=set(re.findall(r'function\s+(test\w+)',t))
    assert smoke.external_tests(cases['safe_review_workflow']['expected'],'NanoJevPull03')[1]==0


def test_live_head_exact_gold_and_incomplete_external_refusal(cases):
    case=cases['safe_reversible_split']
    ok=json.dumps({'status':'contract','contract':case['expected']})
    assert smoke.validate_head(case,ok)==pull.validate(case['expected'])
    e=smoke.external_sources()[0]
    challenge={'id':e['id'],'family':'needs_clarification','request':e['source_excerpt']}
    assert smoke.validate_head(challenge,json.dumps({'status':'needs_clarification',
        'questions':['Who are the payees, and what are their amounts and deadlines?']})) is None
    with pytest.raises(SmokeFailure): smoke.validate_head(challenge,ok)


def test_semantic_drift_is_a_failure(cases):
    case=cases['safe_reversible_split']
    wrong=copy.deepcopy(case['expected'])
    wrong['rules'][1]['payments'][0]['bps']+=1
    with pytest.raises(SmokeFailure):smoke.validate_head(case,json.dumps({'status':'contract','contract':wrong}))


def test_bundled_cases_are_not_independent_gold(cases):
    assert all(x['origin']=='assistant_authored_diagnostic' for x in cases.values())
    assert len(smoke.external_sources())==3


def test_dry_run_no_artifact(tmp_path):
    args=baselines(tmp_path)
    report=smoke.run(smoke.Options(output=tmp_path/'out.json',dry_run=True,**args))
    assert report['status']=='PLAN_ONLY_NOT_EXECUTED'
    assert not (tmp_path/'out.json').exists()
    assert len(report['planned_cases'])==10


def test_missing_real_liveness_baseline_fails_closed(tmp_path):
    args=baselines(tmp_path)
    report=json.loads(args['liveness_report'].read_text())
    report['stages']['forge_tests_passed']=137
    args['liveness_report'].write_text(json.dumps(report))
    got=smoke.run(smoke.Options(output=tmp_path/'fail.json',**args),docker_available=True)
    assert got['status']=='BLOCKED_NOT_VERIFIED'
    assert got['blockers'][0]['stage']=='preflight'


def test_no_docker_fails_closed(tmp_path):
    args=baselines(tmp_path)
    got=smoke.run(smoke.Options(output=tmp_path/'fail.json',**args),docker_available=False)
    assert got['status']=='BLOCKED_NOT_VERIFIED'
    assert got['blockers'][0]['stage']=='preflight'


def test_pipeline_injected_not_categorized_live(tmp_path,cases):
    args=baselines(tmp_path)
    mapping={x['request']:x for x in cases.values()}
    for x in smoke.external_sources():
        mapping[x['source_excerpt']]={'family':'needs_clarification'}
    def head(request):
        case=mapping[request]
        if case['family']=='needs_clarification':
            return json.dumps({'status':'needs_clarification',
                'questions':['Which parties, deadlines, and payout amounts are intended?']})
        return json.dumps({'status':'contract','contract':case['expected']})
    def docker(cmd,timeout):
        if 'test' not in cmd:return {'exit_code':0,'stdout_tail':'Compiler run successful!'}
        folder=tmp_path/'injected.workspace'/'test'
        n=sum(len(re.findall(r'\bfunction\s+test\w*\s*\(',p.read_text()))
              for p in folder.glob('*.t.sol'))
        return {'exit_code':0,'stdout_tail':f'Ran 4 test suites in 14ms: {n} tests passed, 0 failed, 0 skipped ({n} total tests)'}
    report=smoke.run(smoke.Options(output=tmp_path/'injected.json',**args),
        docker_available=True,head=head,docker=docker)
    assert report['status']=='PASS_INJECTED_OFFLINE_ONLY',report['blockers']
    assert report['stages']['compiled_artifacts']=='INJECTED_NOT_VERIFIED'
    assert report['expected_foundry_tests']>=100
    assert report['stages']['external_published_behavior_tests'].startswith('GENERATED_')
    assert report['stages']['independent_semantic_correctness'].startswith('NOT_ESTABLISHED')
    assert len(report['cases'])==10
    assert len(report['generated'])==4
    assert sum(x['policy_action']=='BLOCK_UNSAFE_CONTRACT' for x in report['cases'])==2
    assert sum(x['policy_action']=='REFUSE_UNDERSPECIFIED' for x in report['cases'])==4
    assert report['user_supplied_cases']==0


def test_forge_failure_is_never_green(tmp_path):
    args=baselines(tmp_path)
    def docker(cmd,timeout): return {'exit_code':1,'stdout_tail':'error compilation'}
    report=smoke.run(smoke.Options(output=tmp_path/'bad.json',compiler_only=True,**args),
        docker_available=True,docker=docker)
    assert report['status']=='BLOCKED_NOT_VERIFIED'
    assert report['stages']['forge_build']['exit_code']==1
    assert report['blockers'][0]['stage']=='forge_build'


def test_external_gold_author_unattested(tmp_path,cases):
    # Existing external case syntax requires v1 gold; v2 is an internal upgrade.
    a=cases['safe_reversible_split'];b=cases['unsafe_work_review']
    corpus={'schema':'nanojev.contract-liveness.external.v1','cases':[
      {'id':'external_safe','family':'safe_positive','request':a['request'],
       'expected':pull.old(a['expected'])},
      {'id':'external_unsafe','family':'unsafe_compilable','request':b['request'],
       'expected':pull.old(b['expected'])}]}
    f=tmp_path/'external.json';f.write_text(json.dumps(corpus))
    got,sha=smoke.prior.load_independent(f,{x['id'] for x in smoke.cases()})
    assert len(got)==2 and sha
    bad=copy.deepcopy(corpus)
    bad['cases'][1]['family']='safe_positive'
    f.write_text(json.dumps(bad))
    with pytest.raises(SmokeFailure):smoke.prior.load_independent(f,set())


def test_multi_payee_credit_rounding_covers_last_wei(cases):
    # Three-way split, with rounding in BOTH preceding shares. The final
    # recipient receives the exact remainder, not a rounded bps aggregate.
    gold=copy.deepcopy(cases['safe_reversible_split']['expected'])
    gold['amount_wei']=10007
    r=next(r for r in gold['rules'] if r['name']=='release')
    r['payments']=[{'to':'payer','bps':3333},{'to':'reviewer','bps':3333},
                   {'to':'worker','bps':3334}]
    parsed=pull.validate(gold)
    src=pull.source(parsed,'NanoJevPull01')
    tests,_=pull.tests(parsed,'NanoJevPull01')
    assert 'AMOUNT_WEI - (share0 + share1)' in src
    assert 'AMOUNT - ((AMOUNT * 3333) / 10000 + (AMOUNT * 3333) / 10000)' in tests


def test_zero_wei_allocations_fail_closed(cases):
    gold=copy.deepcopy(cases['safe_reversible_split']['expected'])
    gold['amount_wei']=1
    with pytest.raises(SmokeFailure,match='Zero-wei beneficiary credit'):
        pull.validate(gold)
