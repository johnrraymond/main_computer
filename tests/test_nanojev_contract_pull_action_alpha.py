"""Regress live 0004 submit_work-vs-submit drift without relaxing typed semantics."""
from __future__ import annotations
import copy
import json
import re
import pytest
from tools import nanojev_contract_pull_smoke as smoke
from tools import nanojev_contract_pull_payments as pull
from tools.nanojev_contract_solidity_smoke import SmokeFailure
from test_nanojev_contract_pull_smoke import baselines


def case():
    return next(c for c in smoke.cases() if c['id']=='safe_review_duration_change')


def qualified(spec):
    s=copy.deepcopy(spec)
    named=[r for r in s['rules'] if r['name']=='submit']
    assert len(named)==1
    named[0]['name']='submit_work'
    return s


def wire(s):
    return json.dumps({'status':'contract','contract':s},separators=(',',':'))


def test_live_0004_only_action_name_changes_and_is_audited():
    c=case()
    actual=pull.validate(qualified(c['expected']))
    gold=pull.validate(c['expected'])
    assert actual != gold
    assert smoke.terminal_state_alpha_map(actual,gold) is None
    assert smoke.semantic_alpha_maps(actual,gold)==({}, {'submit':'submit_work'})
    assert smoke.validate_head(c,wire(actual))==actual
    assert smoke.gold_with_verified_alpha_aliases(gold,actual)==actual
    sol=pull.source(actual,'NanoJevPull01')
    assert 'function submit_work() external' in sol
    assert 'function submit() external' not in sol
    test_source,count=pull.tests(smoke.gold_with_verified_alpha_aliases(gold,actual),'NanoJevPull01')
    assert 'c.submit_work();' in test_source
    assert count == len(re.findall(r'\bfunction\s+test\w*\s*\(',test_source))


@pytest.mark.parametrize('mod',[
    lambda x:x.__setitem__('amount_wei',x['amount_wei']+1),
    lambda x:x.__setitem__('deadline_seconds',x['deadline_seconds']+1),
    lambda x:next(r for r in x['rules'] if r['name']=='submit_work').__setitem__('actor','payer'),
    lambda x:next(r for r in x['rules'] if r['name']=='submit_work').__setitem__('when','always'),
    lambda x:next(r for r in x['rules'] if r['name']=='submit_work').__setitem__('from','submitted'),
    lambda x:next(r for r in x['rules'] if r['name']=='submit_work').__setitem__('to','settled'),
    lambda x:next(r for r in x['rules'] if r['name']=='submit_work').__setitem__('payments',[{'to':'worker','bps':10000}]),
    lambda x:next(r for r in x['rules'] if r['name']=='approve')['payments'][0].__setitem__('to','payer'),
    lambda x:next(r for r in x['rules'] if r['name']=='approve').__setitem__('actor','reviewer'),
    lambda x:next(r for r in x['rules'] if r['name']=='approve').__setitem__('when','before_deadline'),
    lambda x:next(r for r in x['rules'] if r['name']=='submit_work').__setitem__('name','refund_work'),
    lambda x:next(r for r in x['rules'] if r['name']=='submit_work').__setitem__('name','approve_work'),
    lambda x:next(r for r in x['rules'] if r['name']=='submit_work').__setitem__('name','submit_another_action'),
])
def test_action_alias_never_masks_other_semantic_changes(mod):
    c=case()
    bad=qualified(c['expected'])
    mod(bad)
    with pytest.raises(SmokeFailure) as e:
        smoke.validate_head(c,wire(bad))
    assert e.value.stage in ('contract_validation','semantic_gold')


def test_unqualified_arbitrary_renaming_remains_refused():
    c=case(); bad=copy.deepcopy(c['expected'])
    for r in bad['rules']:
        if r['name']=='submit': r['name']='record_submission'
    with pytest.raises(SmokeFailure,match='Wrong semantics'):
        smoke.validate_head(c,wire(bad))


def test_action_alias_does_not_trigger_structural_retry():
    c=case(); calls=[]
    result=smoke.infer_with_bounded_structural_retry(c,lambda request: calls.append(request) or wire(qualified(c['expected'])),{})
    assert len(calls)==1
    assert result==pull.validate(qualified(c['expected']))


def test_ambiguous_duplicate_rule_pairing_is_refused():
    c=case()
    bad=qualified(c['expected'])
    # Changing a different action name to collide with the submitted action
    # must not make action equivalence accept duplicate ABI methods.
    for r in bad['rules']:
        if r['name']=='approve': r['name']='submit_work'
    with pytest.raises(SmokeFailure):
        smoke.validate_head(c,wire(bad))


def test_combined_terminal_and_action_renaming_is_behavioral_not_abi_equivalence():
    c=case(); actual=qualified(c['expected'])
    for r in actual['rules']:
        if r['to']=='settled': r['to']='completed_approved'
        if r['to']=='refunded': r['to']='completed_refunded'
    expected=pull.validate(c['expected'])
    validated=pull.validate(actual)
    assert smoke.semantic_alpha_maps(validated,expected)==(
        {'settled':'completed_approved','refunded':'completed_refunded'},
        {'submit':'submit_work'})
    assert smoke.gold_with_verified_alpha_aliases(expected,validated)==validated


def test_end_to_end_injected_harness_uses_gold_with_remapped_abi(tmp_path):
    args=baselines(tmp_path)
    cases={x['request']:x for x in smoke.cases()}
    published={x['source_excerpt'] for x in smoke.external_sources()}
    target=case()
    changed=qualified(target['expected'])
    def fake_head(request):
        if request in published:
            return json.dumps({'status':'needs_clarification','questions':['Missing contract terms.']})
        c=cases[request]
        if c['family']=='needs_clarification':
            return json.dumps({'status':'needs_clarification','questions':['Missing contract terms.']})
        return wire(changed if c['id']==target['id'] else c['expected'])
    def fake_docker(cmd,timeout):
        if 'test' not in cmd:
            return {'exit_code':0,'stdout_tail':'Compiler run successful!'}
        test_dir=tmp_path/'run.workspace'/'test'
        actual=sum(len(re.findall(r'\bfunction\s+test\w*\s*\(',p.read_text())) for p in test_dir.glob('*.t.sol'))
        return {'exit_code':0,'stdout_tail':f'Ran 4 test suites in 14ms: {actual} tests passed, 0 failed, 0 skipped ({actual} total tests)'}
    r=smoke.run(smoke.Options(output=tmp_path/'run.json',**args),
                docker_available=True,head=fake_head,docker=fake_docker)
    assert r['status']=='PASS_INJECTED_OFFLINE_ONLY',r['blockers']
    assert r['expected_foundry_tests']==212
    assert r['stages']['action_name_alias_cases']==1
    assert r['stages']['terminal_state_alias_cases']==0
    ent=next(x for x in r['cases'] if x['id']==target['id'])
    assert ent['semantic_result']=='ACTION_NAME_ALPHA_EQUIVALENT_ABI_CHANGED'
    assert ent['action_name_alpha_map_gold_to_model']=={'submit':'submit_work'}
    assert ent['abi_name_compatibility']=='DIFFERS_FROM_GOLD_REQUIRES_INTERFACE_REVIEW'
    assert r['stages']['structural_retries']==0
    target_name=next(k for k,v in r['generated'].items() if v['case']==target['id'])
    solidity=(tmp_path/'run.workspace'/'src'/f'{target_name}.sol').read_text()
    foundry=(tmp_path/'run.workspace'/'test'/f'{target_name}.t.sol').read_text()
    assert 'function submit_work() external' in solidity
    assert 'c.submit_work();' in foundry
    assert r['stages']['compiled_artifacts']=='INJECTED_NOT_VERIFIED'
