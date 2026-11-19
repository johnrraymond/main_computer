"""Offline, non-LLM structural tests for the step-2 translator."""
import copy
import json

import pytest
from tools import nanojev_contract_translation_step2 as step


def as_reply(spec):
    return json.dumps({'status':'contract','contract':spec})


def test_sample_typed_ir_validates_and_has_recovery():
    r=step.process(step.EXAMPLE, as_reply(step.DEMONSTRATION_IR),mode='OFFLINE_RESPONSE_NO_LLM',example=True)
    assert r['status']=='DRAFT_REQUIRES_USER_CONFIRMATION'
    assert r['liveness']['status']=='PASS_CONDITIONAL'
    assert r['candidate']['amount_wei']==3*10**18
    assert r['candidate']['deadline_seconds']==7*86400
    assert r['user_confirmation_required'] is True
    assert 'off-chain' in ' '.join(r['open_issues']) or 'on-chain' in ' '.join(r['open_issues'])


def test_injected_head_is_never_reported_as_live():
    result=step.execute(step.EXAMPLE,model='dummy',ollama_url='http://127.0.0.1:11434',timeout=1,
                        infer=lambda request: as_reply(step.DEMONSTRATION_IR),example=True)
    assert result['mode']=='INJECTED_TEST_HEAD_NOT_LIVE'
    assert result['semantic_gold']=='NOT_PROVIDED'


def test_refusal_preserved():
    r=step.process(step.EXAMPLE,json.dumps({'status':'needs_clarification','questions':['Who verifies delivery?']}),
                   mode='OFFLINE_RESPONSE_NO_LLM',example=True)
    assert r['status']=='NEEDS_CLARIFICATION'
    assert r['candidate'] is None
    assert r['questions']==['Who verifies delivery?']


def test_liveness_failure_blocked():
    spec=copy.deepcopy(step.DEMONSTRATION_IR)
    spec['rules']=[r for r in spec['rules'] if r['name'] not in ('fallback_approve','fallback_refund')]
    r=step.process(step.EXAMPLE,as_reply(spec),mode='OFFLINE_RESPONSE_NO_LLM',example=True)
    assert r['status']=='BLOCKED_LIVENESS_RISK'
    assert r['liveness']['single_role_traps']


@pytest.mark.parametrize('alter',[
    lambda c: c.update(settlement_mode='push'),
    lambda c: c['rules'][0].update(payments=[{'to':'worker','bps':10000}]),
    lambda c: c['rules'][0].update(**{'from':'unfunded','to':'funded'}),
    lambda c: c['rules'][1].update(when='not_a_guard'),
    lambda c: c['rules'][1].update(payments=[{'to':'payer','bps':9000}]),
])
def test_invalid_contract_is_rejected(alter):
    spec=copy.deepcopy(step.DEMONSTRATION_IR)
    alter(spec)
    with pytest.raises(Exception):step.process(step.EXAMPLE,as_reply(spec),mode='OFFLINE_RESPONSE_NO_LLM',example=True)


def test_refuse_duplicate_json_keys():
    with pytest.raises(Exception):
        step.process(step.EXAMPLE,'{"status":"contract","status":"contract"}',
                     mode='OFFLINE_RESPONSE_NO_LLM',example=True)


@pytest.mark.parametrize('response',[
    {'status':'needs_clarification','questions':[]},
    {'status':'contract','questions':['wrong']},
    {'status':'wrong','contract':{}},
])
def test_reject_malformed_response(response):
    with pytest.raises(Exception):
        step.process(step.EXAMPLE,json.dumps(response),mode='OFFLINE_RESPONSE_NO_LLM',example=True)


def test_no_semantic_or_deployment_claims():
    r=step.process(step.EXAMPLE,as_reply(step.DEMONSTRATION_IR),mode='OFFLINE_RESPONSE_NO_LLM',example=True)
    assert r['scope']=='TRANSLATION_ONLY_NO_SOLIDITY_NO_DEPLOYMENT'
    assert r['independent_semantic_correctness']=='NOT_ESTABLISHED'
    assert r['user_confirmation_required'] is True
