"""Regress the actual live 0002 failure: invented initial -> funded action."""
import copy
import json
import re

import pytest
from tools import nanojev_contract_pull_smoke as smoke
from tools.nanojev_contract_solidity_smoke import SmokeFailure
from test_nanojev_contract_pull_smoke import baselines


def first_case():
    return next(c for c in smoke.cases() if c['id']=='unsafe_reversible_split')


def invented_funding(c):
    bad=copy.deepcopy(c['expected'])
    # Actual run had a custom entry action before cancel and release.
    bad['rules'].insert(0,{
        'name':'fund_agreement','actor':'payer','from':'initial','to':'funded',
        'when':'always','payments':[]})
    return json.dumps({'status':'contract','contract':bad})


def correct(c):
    return json.dumps({'status':'contract','contract':c['expected']})


def test_exact_reproduced_failure_remains_rejected():
    c=first_case()
    with pytest.raises(SmokeFailure,match='Unsafe, duplicate or invalid identifier') as err:
        smoke.validate_head(c,invented_funding(c))
    assert err.value.stage=='contract_validation'
    assert smoke._structural_retry_kind(invented_funding(c),err.value)=='DUPLICATE_IMPLICIT_FUNDING'


def test_prompt_declares_funding_built_in_and_not_part_of_rules():
    assert 'Do NOT represent funding as a rule' in smoke.PROMPT
    assert 'NEVER create "fund_agreement"' in smoke.PROMPT
    assert '"initial" -> "funded"' in smoke.PROMPT
    assert 'cancel and' in smoke.PROMPT


def test_single_model_retry_uses_diagnostic_not_gold():
    c=first_case()
    candidate=invented_funding(c)
    requests=[]
    def infer(request):
        requests.append(request)
        return candidate if len(requests)==1 else correct(c)
    entry={'id':c['id']}
    result=smoke.infer_with_bounded_structural_retry(c,infer,entry)
    assert result is not None
    assert len(requests)==2
    assert requests[0]==c['request']
    assert 'DUPLICATE_IMPLICIT_FUNDING' in requests[1]
    assert 'fund_agreement' in requests[1]
    assert 'The compiler already supplies fund()' in requests[1]
    assert candidate in requests[1]
    assert 'gold' not in requests[1].lower()
    assert entry['first_attempt_output']==candidate
    assert entry['llm_output']==correct(c)
    assert entry['structural_retry_result']=='PASSED_ORIGINAL_VALIDATORS'
    assert entry['structural_retry_kind']=='DUPLICATE_IMPLICIT_FUNDING'
    assert entry['llm_attempts']==2


def test_second_invalid_attempt_fails_closed():
    c=first_case()
    requests=[]
    def infer(request):
        requests.append(request)
        return invented_funding(c)
    entry={}
    with pytest.raises(SmokeFailure,match='Unsafe, duplicate or invalid identifier'):
        smoke.infer_with_bounded_structural_retry(c,infer,entry)
    assert len(requests)==2
    assert entry['llm_attempts']==2
    assert 'structural_retry_result' not in entry


def test_second_structurally_valid_but_wrong_semantics_fails_closed():
    c=first_case()
    wrong=copy.deepcopy(c['expected'])
    wrong['deadline_seconds']+=86400
    outputs=[invented_funding(c),json.dumps({'status':'contract','contract':wrong})]
    entry={}
    with pytest.raises(SmokeFailure) as err:
        smoke.infer_with_bounded_structural_retry(c,lambda _ : outputs.pop(0),entry)
    assert err.value.stage=='semantic_gold'
    assert not outputs
    assert 'structural_retry_result' not in entry


@pytest.mark.parametrize('alter',[
    lambda x: x['rules'][0].update({'name':'Invalid-Name'}),
    lambda x: x['rules'][0].update({'name':'payer'}),
    lambda x: x['rules'][0].update({'to':'funded'}),
])
def test_other_invalid_identifiers_are_not_retried(alter):
    c=first_case()
    bad=copy.deepcopy(c['expected'])
    alter(bad)
    raw=json.dumps({'status':'contract','contract':bad})
    seen=[]
    def infer(request):
        seen.append(request)
        return raw
    with pytest.raises(SmokeFailure) as err:
        smoke.infer_with_bounded_structural_retry(c,infer,{})
    assert err.value.stage=='contract_validation'
    assert len(seen)==1


def test_no_retry_of_unsupported_request_or_semantic_mismatch():
    c=first_case()
    bad=copy.deepcopy(c['expected'])
    bad['amount_wei']+=1
    raw=json.dumps({'status':'contract','contract':bad})
    seen=[]
    with pytest.raises(SmokeFailure) as err:
        smoke.infer_with_bounded_structural_retry(c,lambda req:seen.append(req) or raw,{})
    assert err.value.stage=='semantic_gold'
    assert len(seen)==1


def test_injected_full_pipeline_recovers_funding_error_and_preserves_212_gate(tmp_path):
    args=baselines(tmp_path)
    examples={x['request']:x for x in smoke.cases()}
    for x in smoke.external_sources():
        examples[x['source_excerpt']]={'family':'needs_clarification'}
    requests=[]
    def fake_head(request):
        if 'COMPILER DIAGNOSTIC' in request:
            requests.append(request)
            assert 'DUPLICATE_IMPLICIT_FUNDING' in request
            return correct(first_case())
        c=examples[request]
        if c.get('family')=='needs_clarification':
            return json.dumps({'status':'needs_clarification','questions':['Please specify roles and amounts.']})
        if c['id']==first_case()['id']:
            return invented_funding(c)
        return correct(c)
    def docker(cmd,timeout):
        if 'test' not in cmd:
            return {'exit_code':0,'stdout_tail':'Compiler run successful!'}
        folder=tmp_path/'run.workspace'/'test'
        count=sum(len(re.findall(r'\bfunction\s+test\w*\s*\(',p.read_text()))
                  for p in folder.glob('*.t.sol'))
        return {'exit_code':0,'stdout_tail':
            f'Ran 4 test suites in 14ms: {count} tests passed, 0 failed, 0 skipped ({count} total tests)'}
    result=smoke.run(smoke.Options(output=tmp_path/'run.json',**args),
                     docker_available=True,head=fake_head,docker=docker)
    assert result['status']=='PASS_INJECTED_OFFLINE_ONLY',result['blockers']
    assert result['stages']['structural_retries']==1
    assert result['expected_foundry_tests']==212
    assert len(requests)==1
    assert result['cases'][0]['structural_retry_kind']=='DUPLICATE_IMPLICIT_FUNDING'
    assert result['cases'][0]['policy_action']=='BLOCK_UNSAFE_CONTRACT'
    assert result['stages']['head']=='PASS_INJECTED'
    assert result['stages']['compiled_artifacts']=='INJECTED_NOT_VERIFIED'
