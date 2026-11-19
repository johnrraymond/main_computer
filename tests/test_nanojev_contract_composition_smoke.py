"""Offline regression coverage. Fake head/Forge outcomes NEVER count as live verification."""
import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from tools import nanojev_contract_composition_smoke as c
from tools import nanojev_contract_state_machine as m
from tools.nanojev_contract_solidity_smoke import SmokeFailure


@pytest.fixture
def corpus():
    d=c.fixture()
    return d, {x['id']:x for x in d['cases']}


@pytest.mark.parametrize('name', [
    'reversible_split_base','reversible_split_paraphrase','reversible_split_ratio_change',
    'review_workflow_base','review_workflow_paraphrase','review_workflow_duration_change'])
def test_gold_compiles_to_generic_source_and_tests(corpus,name):
    _, cases=corpus
    spec=c.gold(cases[name],cases)
    solidity=m.source(spec,'NanoJevComposition01')
    tests=m.tests(spec,'NanoJevComposition01')
    assert 'enum State' in solidity and 'function fund()' in solidity
    assert 'fundedAt + DEADLINE_SECONDS' in solidity
    assert len(re.findall(r'function test\w*\(',tests))>=16
    assert 'function testFail' not in tests
    for rule in spec['rules']:
        assert f'function {rule["name"]}() external' in solidity
        assert f'testOracleHappy{rule["name"].capitalize()}' in tests


def test_distinct_compositions_not_family_templates(corpus):
    _, cases=corpus
    a=c.gold(cases['reversible_split_base'],cases)
    b=c.gold(cases['review_workflow_base'],cases)
    assert {r['name'] for r in a['rules']}=={'cancel','release'}
    assert {r['name'] for r in b['rules']}=={'submit','approve','refund'}
    assert 'submitted' in {r['to'] for r in b['rules']}
    assert any(not r['payments'] for r in b['rules'])
    assert a != b


def test_paraphrase_and_contrast(corpus):
    _,cases=corpus
    assert c.gold(cases['reversible_split_base'],cases)==c.gold(cases['reversible_split_paraphrase'],cases)
    assert c.gold(cases['review_workflow_base'],cases)==c.gold(cases['review_workflow_paraphrase'],cases)
    assert c.gold(cases['reversible_split_base'],cases)!=c.gold(cases['reversible_split_ratio_change'],cases)
    assert c.gold(cases['review_workflow_base'],cases)!=c.gold(cases['review_workflow_duration_change'],cases)


@pytest.mark.parametrize('mutator',[
    lambda x: x.update(amount_wei=0),
    lambda x: x.update(amount_wei=True),
    lambda x: x.update(deadline_seconds=-1),
    lambda x: x.update(extra='silent'),
    lambda x: x['rules'][0].update(actor='outsider'),
    lambda x: x['rules'][0].update(name='testFailHack'),
    lambda x: x['rules'][0].update(when='sometime'),
    lambda x: x['rules'][0].update(to='funded'),
    lambda x: x['rules'][0].update(payments=[{'to':'payer','bps':9999}]),
    lambda x: x['rules'][0].update(payments=[{'to':'payer','bps':10000},{'to':'payer','bps':1}]),
    lambda x: x['rules'][0].update(unknown='hidden'),
])
def test_rejects_invalid_language(corpus,mutator):
    _,cases=corpus
    obj=copy.deepcopy(c.gold(cases['reversible_split_base'],cases))
    mutator(obj)
    with pytest.raises(SmokeFailure):
        m.validate(obj)


def test_rejects_cycles(corpus):
    _,cases=corpus
    obj=copy.deepcopy(c.gold(cases['review_workflow_base'],cases))
    obj['rules'].append({'name':'go_back','actor':'payer','from':'submitted','to':'funded','when':'always','payments':[]})
    with pytest.raises(SmokeFailure):
        m.validate(obj)


def test_rejects_wrong_positive_semantics(corpus):
    _,cases=corpus
    case=cases['reversible_split_base']
    obj=copy.deepcopy(c.gold(case,cases)); obj['rules'][1]['actor']='payer'
    with pytest.raises(SmokeFailure) as x:
        c.accept(case,{'status':'contract','contract':obj},cases)
    assert x.value.stage=='semantic_oracle'


def test_negative_must_refuse(corpus):
    _,cases=corpus
    neg=cases['unsupported_automatic_timeout']
    with pytest.raises(SmokeFailure) as x:
        c.accept(neg,{'status':'contract','contract':c.gold(cases['reversible_split_base'],cases)},cases)
    assert x.value.stage=='semantic_oracle'
    assert c.accept(neg,{'status':'needs_clarification','questions':['Who must trigger the transaction?']},cases) is None


def test_duplicate_key_refused():
    with pytest.raises(SmokeFailure):
        c.parse('{"status":"contract","status":"needs_clarification"}')


def test_full_mocked_pipeline_does_not_claim_live(tmp_path,corpus):
    data,cases=corpus
    base_report=tmp_path/'baseline.json'
    base_report.write_text(json.dumps({'status':'PASS_SMOKE_NOT_CERTIFIED',
        'expected_foundry_tests':56,'stages':{'forge_build':{'exit_code':0},
        'forge_test':{'exit_code':0},'forge_tests_passed':56,'compiled_artifacts':'PASS'}}))
    def head(request):
        case=next(x for x in data['cases'] if x['request']==request)
        if case['family']=='positive':
            return json.dumps({'status':'contract','contract':c.gold(case,cases)})
        return json.dumps({'status':'needs_clarification','questions':[{'ambiguous_split_allocation':'What split ratio?', 'contradictory_review':'Should refund remain possible after submit?', 'unsupported_automatic_timeout':'Who triggers the on-chain transaction instead of automatic execution?', 'unsupported_recurring_installments':'Monthly recurring installments require a keeper or counter.'}[case['id']]]})
    def docker(argv,timeout):
        if 'test' in argv:
            count=len(re.findall(r'function test\w*\(',''.join(
                p.read_text() for p in (tmp_path/'composition.workspace'/'test').glob('*.t.sol'))))
            return {'exit_code':0,'stdout_tail':f'Ran 6 test suites in 25.0ms: {count} tests passed, 0 failed, 0 skipped ({count} total tests)'}
        return {'exit_code':0,'stdout_tail':'Compiler run successful!'}
    out=tmp_path/'composition.json'
    result=c.run(c.Options(output=out,baseline_report=base_report),head=head,docker=docker,docker_available=True)
    assert result['status']=='PASS_INJECTED_OFFLINE_TEST_ONLY', result['blockers']
    assert result['stages']['forge_tests_passed']>=60
    assert len(result['generated'])==6
    assert len(result['cases'])==10
    assert out.exists()


def test_baseline_missing_blocks(tmp_path):
    result=c.run(c.Options(output=tmp_path/'probe.json',baseline_report=tmp_path/'missing.json'),
                 docker_available=True)
    assert result['status']=='BLOCKED_NOT_VERIFIED'
    assert result['blockers'][0]['stage']=='preflight'


def test_baseline_false_pass_blocks(tmp_path):
    base_report=tmp_path/'false.json'
    base_report.write_text(json.dumps({'status':'PASS_SMOKE_NOT_CERTIFIED',
        'expected_foundry_tests':56,'stages':{'forge_build':{'exit_code':0},
        'forge_test':{'exit_code':1},'forge_tests_passed':56,'compiled_artifacts':'PASS'}}))
    result=c.run(c.Options(output=tmp_path/'probe.json',baseline_report=base_report),docker_available=True)
    assert result['status']=='BLOCKED_NOT_VERIFIED'
    assert result['blockers'][0]['stage']=='preflight'


def test_external_gold_case_loading(corpus,tmp_path):
    _,cases=corpus
    valid=c.gold(cases['reversible_split_base'],cases)
    payload={'schema':'nanojev.contract-composition.external.v1','cases':[
        {'id':'external_new_1','family':'positive',
         'request':cases['reversible_split_base']['request']+' This is a separately described request.',
         'expected':valid},
        {'id':'external_negative_1','family':'needs_clarification',
         'request':cases['unsupported_automatic_timeout']['request'],
         'reason':'Cannot execute without a transaction'}]}
    file=tmp_path/'external.json'
    file.write_text(json.dumps(payload))
    items,sha=c.load_independent(file,{x['id'] for x in c.fixture()['cases']})
    assert len(items)==2 and len(sha)==64
    assert c.gold(items[0], {items[0]['id']:items[0]})==valid


def test_external_gold_duplicate_disallowed(corpus,tmp_path):
    _,cases=corpus
    item={'id':'reversible_split_base','family':'positive','request':cases['reversible_split_base']['request'],
          'expected':c.gold(cases['reversible_split_base'],cases)}
    file=tmp_path/'invalid.json'
    file.write_text(json.dumps({'schema':'nanojev.contract-composition.external.v1',
                                'cases':[item,{'id':'external_negative_1','family':'needs_clarification',
                                               'request':cases['unsupported_automatic_timeout']['request'],
                                               'reason':'unsupported'}]}))
    with pytest.raises(SmokeFailure):
        c.load_independent(file,{x['id'] for x in c.fixture()['cases']})


def test_irrelevant_negative_clarification_rejected(corpus):
    _,cases=corpus
    case=cases['ambiguous_split_allocation']
    with pytest.raises(SmokeFailure):
        c.accept(case,{'status':'needs_clarification','questions':['Who deploys?']},cases)


def test_compiler_only_never_reports_llm_generalization(tmp_path):
    prior=tmp_path/'prior.json'
    prior.write_text(json.dumps({'status':'PASS_SMOKE_NOT_CERTIFIED',
        'expected_foundry_tests':56,'stages':{'forge_build':{'exit_code':0},
        'forge_test':{'exit_code':0},'forge_tests_passed':56,'compiled_artifacts':'PASS'}}))
    def pretend_forge(args,timeout):
        if 'test' in args:
            sources=''.join(p.read_text() for p in (tmp_path/'compiler.workspace'/'test').glob('*.t.sol'))
            count=len(re.findall(r'function test\w*\(',sources))
            return {'exit_code':0,'stdout_tail':f'Ran 6 test suites in 50.0ms: {count} tests passed, 0 failed, 0 skipped ({count} total tests)'}
        return {'exit_code':0,'stdout_tail':'Compiler run successful!'}
    report=c.run(c.Options(output=tmp_path/'compiler.json',baseline_report=prior,compiler_only=True),
                 docker=pretend_forge,docker_available=True)
    assert report['status']=='PASS_COMPILER_ONLY_NOT_LLM_GENERALIZATION', report['blockers']
    assert report['stages']['head']=='BYPASSED_COMPILER_ONLY'
    assert report['expected_foundry_tests']==105
