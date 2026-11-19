"""Regress live 0003 terminal-state label drift without weakening semantic gates."""
import copy
import json
import re

import pytest

from tools import nanojev_contract_pull_smoke as smoke
from tools import nanojev_contract_pull_payments as pull
from tools.nanojev_contract_solidity_smoke import SmokeFailure
from test_nanojev_contract_pull_smoke import baselines


def gold_case():
    return next(c for c in smoke.cases() if c['id']=='safe_review_workflow')


def alternate_states(spec):
    out=copy.deepcopy(spec)
    for r in out['rules']:
        if r['to']=='settled':
            r['to']='completed_approved'
        elif r['to']=='refunded':
            r['to']='completed_refunded'
    return out


def model_wire(spec):
    return json.dumps({'status':'contract','contract':spec},separators=(',',':'))


def test_exact_live_failure_is_recognized_as_terminal_alpha_equivalence():
    case=gold_case()
    modeled=alternate_states(case['expected'])
    assert pull.validate(modeled)!=pull.validate(case['expected'])
    assert smoke.terminal_state_alpha_map(pull.validate(modeled),pull.validate(case['expected']))=={
        'settled':'completed_approved','refunded':'completed_refunded'}
    assert smoke.validate_head(case,model_wire(modeled))==pull.validate(modeled)


def test_adapting_independent_oracle_changes_only_terminal_label():
    case=gold_case()
    original=copy.deepcopy(case['expected'])
    actual=pull.validate(alternate_states(original))
    adapted=smoke.gold_with_verified_terminal_aliases(original,actual)
    assert adapted==actual
    assert original==case['expected']
    code=pull.source(actual,'NanoJevPull01')
    assert 'State.CompletedApproved' in code
    assert 'State.CompletedRefunded' in code
    assert 'State.Settled' not in code
    # Oracle tests use gold authorizations, transfers, amounts, guards, etc.
    tests, count=pull.tests(adapted,'NanoJevPull01')
    assert count == len(re.findall(r'\bfunction\s+test\w*\s*\(',tests))
    assert count > 20


@pytest.mark.parametrize('corrupt',[
    lambda s:s.__setitem__('amount_wei',s['amount_wei']+1),
    lambda s:s.__setitem__('deadline_seconds',s['deadline_seconds']+1),
    lambda s:s['rules'][0].__setitem__('actor','reviewer'),
    lambda s:s['rules'][0].__setitem__('when','before_deadline'),
    lambda s:s['rules'][0].__setitem__('name','approve_another_action'),
    lambda s:s['rules'][0].__setitem__('from','funded'),
    lambda s:s['rules'][0]['payments'][0].__setitem__('to','payer'),
    lambda s:s['rules'][0]['payments'][0].__setitem__('bps',9000),
    lambda s:s['rules'][0].__setitem__('to','completed_refunded'),
])
def test_only_terminal_alpha_renaming_is_permitted(corrupt):
    case=gold_case()
    bad=alternate_states(case['expected'])
    corrupt(bad)
    with pytest.raises(SmokeFailure) as e:
        smoke.validate_head(case,model_wire(bad))
    assert e.value.stage in ('contract_validation','semantic_gold')


def test_terminal_merger_is_rejected():
    c=gold_case()
    bad=alternate_states(c['expected'])
    for r in bad['rules']:
        if r['to']=='completed_refunded':
            r['to']='completed_approved'
    with pytest.raises(SmokeFailure,match='Wrong semantics'):
        smoke.validate_head(c,model_wire(bad))


def test_swapping_known_gold_terminal_names_is_not_an_alias():
    c=gold_case()
    swapped=copy.deepcopy(c['expected'])
    for r in swapped['rules']:
        if r['to']=='settled':
            r['to']='refunded'
        elif r['to']=='refunded':
            r['to']='settled'
    with pytest.raises(SmokeFailure,match='Wrong semantics'):
        smoke.validate_head(c,model_wire(swapped))


def test_swapped_new_terminal_outcome_labels_are_refused():
    c=gold_case()
    swapped=copy.deepcopy(c['expected'])
    for r in swapped['rules']:
        if r['to']=='settled':
            r['to']='completed_refunded'
        elif r['to']=='refunded':
            r['to']='completed_approved'
    with pytest.raises(SmokeFailure,match='Wrong semantics'):
        smoke.validate_head(c,model_wire(swapped))


def test_unknown_terminal_alias_is_not_silently_accepted():
    c=gold_case()
    bad=copy.deepcopy(c['expected'])
    for r in bad['rules']:
        if r['to']=='settled':
            r['to']='closed_out'
    with pytest.raises(SmokeFailure,match='Wrong semantics'):
        smoke.validate_head(c,model_wire(bad))


def test_intermediate_state_renaming_is_not_permitted():
    c=gold_case()
    bad=alternate_states(c['expected'])
    for r in bad['rules']:
        if r['from']=='submitted':
            r['from']='review_pending'
        if r['to']=='submitted':
            r['to']='review_pending'
    with pytest.raises(SmokeFailure,match='Wrong semantics'):
        smoke.validate_head(c,model_wire(bad))


def test_semantic_error_does_not_trigger_structural_retry():
    c=gold_case()
    bad=alternate_states(c['expected'])
    bad['amount_wei']+=1
    seen=[]
    with pytest.raises(SmokeFailure) as e:
        smoke.infer_with_bounded_structural_retry(c,lambda req:seen.append(req) or model_wire(bad),{})
    assert e.value.stage=='semantic_gold'
    assert len(seen)==1


def test_full_injected_run_audits_alias_and_preserves_212_test_gate(tmp_path):
    args=baselines(tmp_path)
    cases={x['request']:x for x in smoke.cases()}
    published={x['source_excerpt'] for x in smoke.external_sources()}
    target=gold_case()
    changed=alternate_states(target['expected'])
    def fake_head(request):
        if request in published:
            return json.dumps({'status':'needs_clarification','questions':['Missing contract terms.']})
        c=cases[request]
        if c['family']=='needs_clarification':
            return json.dumps({'status':'needs_clarification','questions':['Missing contract terms.']})
        return model_wire(changed if c['id']==target['id'] else c['expected'])
    def fake_docker(cmd,timeout):
        if 'test' not in cmd:
            return {'exit_code':0,'stdout_tail':'Compiler run successful!'}
        files=(tmp_path/'run.workspace'/'test').glob('*.t.sol')
        actual=sum(len(re.findall(r'\bfunction\s+test\w*\s*\(',p.read_text())) for p in files)
        return {'exit_code':0,'stdout_tail':f'Ran 4 test suites in 14ms: {actual} tests passed, 0 failed, 0 skipped ({actual} total tests)'}
    r=smoke.run(smoke.Options(output=tmp_path/'run.json',**args),
                docker_available=True,head=fake_head,docker=fake_docker)
    assert r['status']=='PASS_INJECTED_OFFLINE_ONLY',r.get('blockers')
    assert r['expected_foundry_tests']==212
    assert r['stages']['terminal_state_alias_cases']==1
    item=next(x for x in r['cases'] if x['id']==target['id'])
    assert item['semantic_result']=='TERMINAL_ALPHA_EQUIVALENT'
    assert item['terminal_state_alpha_map_gold_to_model']=={
        'settled':'completed_approved','refunded':'completed_refunded'}
    assert r['stages']['structural_retries']==0
    assert r['stages']['head']=='PASS_INJECTED'
    generated=next(k for k,v in r['generated'].items() if v['case']==target['id'])
    content=(tmp_path/'run.workspace'/'src'/f'{generated}.sol').read_text()
    assert 'State.CompletedApproved' in content
    assert 'State.CompletedRefunded' in content
    assert 'State.Settled' not in content
    assert r['stages']['compiled_artifacts']=='INJECTED_NOT_VERIFIED'
