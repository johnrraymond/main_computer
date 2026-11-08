"""A self-attested marketplace Worker cannot claim independent verification."""
import pytest
from main_computer.model_execution_contract import (
    ModelContractError, request_contract, verify_execution_result,
)
from main_computer.stable_hub import _public_worker_result

WORK={'model':'demo-model','operation':'chat.completions','capabilities':['chat.completions'],
      'input':{'kind':'chat','value':'test'}}
RESULT={'status':'success','response':{'model':'demo-model','content':'ok','role':'assistant',
        'execution_assurance':'verified','worker_wallet':'private'},
        'execution_assurance':'verified'}


def test_normal_worker_output_is_not_independent_attestation():
    assert verify_execution_result(WORK,RESULT)=='demo-model'
    projected=_public_worker_result({'result':RESULT})
    assert projected['execution_assurance']=='worker_asserted_not_independently_attested'
    assert projected['result']['response']['content']=='ok'
    assert 'worker_wallet' not in projected['result']['response']
    assert 'execution_assurance' not in projected['result']['response']


@pytest.mark.parametrize('extra', [
    {'requires_verified_execution':True}, {'confidential':True},
    {'trust_tier':'trusted'}, {'trust_tier':'verified'},
    {'execution_assurance':'attested'},
])
def test_sensitive_or_verified_job_fails_closed_without_real_attestation(extra):
    with pytest.raises(ModelContractError,match='attestation unavailable'):
        request_contract({**WORK,**extra})


def test_unknown_model_identity_still_fails():
    with pytest.raises(ModelContractError):
        verify_execution_result(WORK,{'status':'success','response':{'model':'false-model'}})
