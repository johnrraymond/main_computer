"""Step-2 explicit recovery of the live Gemma programmer example, without fabrication."""
import copy
import hashlib
import json

import pytest
from tools import nanojev_contract_translation_step2 as step


def model_candidate():
    # Reproduces the candidate shown in the user's first Step 2 live report.
    return {
        'language':'nanojev.contract.state_machine.v2',
        'asset':'native_eth', 'payer':'deployer',
        'worker':'constructor_worker','reviewer':'constructor_reviewer',
        'amount_wei':3000000000000000000,'deadline_seconds':604800,
        'settlement_mode':'pull',
        'rules':[
            {'name':'approve_work','actor':'payer','from':'submitted',
             'to':'completed','when':'before_deadline',
             'payments':[{'to':'worker','bps':10000}]},
            {'name':'claim_after_deadline','actor':'worker','from':'submitted',
             'to':'completed','when':'at_or_after_deadline',
             'payments':[{'to':'worker','bps':10000}]},
            {'name':'refund_no_submission','actor':'payer','from':'funded',
             'to':'refunded','when':'at_or_after_deadline',
             'payments':[{'to':'payer','bps':10000}]},
            {'name':'submit_work','actor':'worker','from':'funded',
             'to':'submitted','when':'before_deadline',
             'payments':[]},
        ],
    }


def blocked_report(candidate=None):
    reply = json.dumps({'status':'contract','contract':candidate or model_candidate()})
    report=step.process(step.EXAMPLE,reply,mode='LIVE_OLLAMA_LLM',example=True)
    assert report['status']=='BLOCKED_LIVENESS_RISK'
    return report


def test_original_live_candidate_caught_both_traps():
    report=blocked_report()
    traps={(x['state'],x['phase'],x['unavailable_role'])
           for x in report['liveness']['single_role_traps']}
    assert traps == {('funded','at_or_after_deadline','payer'),
                     ('submitted','at_or_after_deadline','worker')}


def test_approved_recovery_is_liveness_safe_and_preserves_intended_allocation():
    original=blocked_report()
    result=step.approved_example_recovery(original,source_sha256='abcd')
    assert result['status']=='DRAFT_REQUIRES_USER_CONFIRMATION'
    assert result['liveness']['status']=='PASS_CONDITIONAL'
    assert not result['liveness']['single_role_traps']
    assert not result['liveness']['unconditional_traps']
    assert result['user_confirmation_required']
    assert result['mode']=='APPROVED_DETERMINISTIC_RECOVERY_NOT_LLM'
    assert result['scope']=='TRANSLATION_ONLY_NO_SOLIDITY_NO_DEPLOYMENT'
    assert result['semantic_gold']=='NOT_PROVIDED'
    assert result['independent_semantic_correctness']=='NOT_ESTABLISHED'
    assert result['recovery_provenance']['no_new_llm_call'] is True
    assert result['recovery_provenance']['source_report_sha256']=='abcd'
    assert len(result['recovery_provenance']['modifications']) == 2
    spec=result['candidate']
    assert spec['amount_wei']==3000000000000000000
    assert spec['deadline_seconds']==604800
    original_rules={r['name']:r for r in original['candidate']['rules']}
    new_rules={r['name']:r for r in spec['rules']}
    assert len(new_rules)==5
    assert set(new_rules)==set(original_rules) | {'fallback_refund'}
    assert new_rules['approve_work']['when']=='always'
    assert new_rules['claim_after_deadline']==original_rules['claim_after_deadline']
    assert new_rules['refund_no_submission']==original_rules['refund_no_submission']
    assert new_rules['submit_work']==original_rules['submit_work']
    assert new_rules['fallback_refund']=={
        'name':'fallback_refund','actor':'worker','from':'funded','to':'refunded',
        'when':'at_or_after_deadline','payments':[{'to':'payer','bps':10000}],
    }


def test_prior_report_is_not_mutated():
    report=blocked_report()
    before=copy.deepcopy(report)
    step.approved_example_recovery(report,source_sha256='abcd')
    assert report==before


@pytest.mark.parametrize('mutation',[
    lambda r: r.update(request_sha256='0'*64),
    lambda r: r.update(mode='OFFLINE_RESPONSE_NO_LLM'),
    lambda r: r.update(status='DRAFT_REQUIRES_USER_CONFIRMATION'),
    lambda r: r['candidate'].update(amount_wei=4*10**18),
    lambda r: r['candidate'].update(deadline_seconds=8*86400),
    lambda r: r['candidate']['rules'][0].update(actor='reviewer'),
    lambda r: r['candidate']['rules'][0].update(payments=[{'to':'payer','bps':10000}]),
    lambda r: r['candidate']['rules'][1].update(**{'to':'refunded'}),
    lambda r: r['candidate']['rules'][2].update(when='before_deadline'),
    lambda r: r['candidate']['rules'][3].update(**{'to':'other_state'}),
    lambda r: r['candidate']['rules'].append({'name':'junk','actor':'payer',
       'from':'funded','to':'another','when':'before_deadline',
       'payments':[{'to':'payer','bps':10000}]}),
])
def test_refuse_unapproved_semantic_mutations(mutation):
    report=blocked_report()
    mutation(report)
    with pytest.raises((ValueError,Exception)):
        step.approved_example_recovery(report,source_sha256='abcd')


def test_repair_cli_reads_prior_report_without_llm(tmp_path, monkeypatch):
    source=tmp_path/'live-blocked.json'
    source.write_text(json.dumps(blocked_report(),indent=2),encoding='utf-8')
    output=tmp_path/'corrected.json'
    def no_llm(*args,**kwargs):
        raise AssertionError('The repair must not call the LLM')
    monkeypatch.setattr(step.head,'real_head',no_llm)
    assert step.main(['--repair-from-report',str(source),'--output',str(output)])==0
    result=json.loads(output.read_text(encoding='utf-8'))
    assert result['status']=='DRAFT_REQUIRES_USER_CONFIRMATION'
    assert result['liveness']['status']=='PASS_CONDITIONAL'
    assert result['recovery_provenance']['source_report_sha256']==hashlib.sha256(source.read_bytes()).hexdigest()


def test_repair_cli_rejects_unexpected_report_and_records_blocker(tmp_path):
    source=tmp_path/'wrong.json'
    source.write_text(json.dumps({'schema':'not-valid'}),encoding='utf-8')
    output=tmp_path/'failed.json'
    assert step.main(['--repair-from-report',str(source),'--output',str(output)])==1
    r=json.loads(output.read_text(encoding='utf-8'))
    assert r['status']=='BLOCKED_UNVERIFIED'
    assert r['blockers']


def test_no_repair_with_other_requested_inputs(tmp_path):
    source=tmp_path/'report.json'
    source.write_text(json.dumps(blocked_report()),encoding='utf-8')
    request=tmp_path/'request.txt'
    request.write_text(step.EXAMPLE,encoding='utf-8')
    output=tmp_path/'bad.json'
    assert step.main(['--repair-from-report',str(source),
                      '--request-file',str(request),'--output',str(output)])==1
