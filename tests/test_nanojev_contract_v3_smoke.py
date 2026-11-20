"""Safe script integration, fail-closed gates, and v2 non-regression tests."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import re

from tools import nanojev_contract_v3_smoke as smoke
from tools import nanojev_contract_v3_language as v3
from tools import nanojev_contract_pull_payments as v2
from tools import nanojev_contract_v2_verifier as v2check


def test_fixture_script_compiler_and_oracle_generation(tmp_path):
    output=tmp_path/'forge_not_run.json'
    result=smoke.execute(smoke.FIXTURE,output)
    assert result['status']=='PASS_V3_IR_AND_SOLIDITY_GENERATED_FORGE_NOT_RUN'
    assert result['stages']['graph']=='PASS_CONDITIONAL'
    assert result['forge']=='NOT_REQUESTED'
    assert output.is_file()
    workspace=tmp_path/'forge_not_run.workspace'
    source=(workspace/'src'/'NanoJevProgressive01.sol').read_text()
    assert 'function approve_final() external' in source
    assert result['generated']['expected_forge_tests']>=36
    for suite in (workspace/'test').glob('*.t.sol'):
        assert len(re.findall(r'\bfunction\s+test\w*\s*\(',suite.read_text()))>0
        assert not re.search(r'\bfunction\s+testFail\w*\(',suite.read_text())


def test_injected_forge_never_attests_to_real_forge_execution(tmp_path):
    calls=[]
    def pretend(args,timeout):
        calls.append(args)
        return {'exit_code':0,'stdout_tail':'pretend success','stderr_tail':''}
    result=smoke.execute(smoke.FIXTURE,tmp_path/'fake.json',forge=True,docker_command=pretend)
    assert result['status']=='PASS_INJECTED_FORGE_NOT_CERTIFIED'
    assert result['forge']=='INJECTED_NOT_ACTUAL_FORGE'
    assert len(calls)==2
    assert any('build' in c for c in calls)
    assert any('test' in c for c in calls)


def test_docker_failure_preserves_blocker_and_no_false_pass(tmp_path):
    result=smoke.execute(smoke.FIXTURE,tmp_path/'fail.json',forge=True,
                         docker_command=lambda args,timeout:{'exit_code':1,'stdout_tail':'','stderr_tail':'error'})
    assert result['status']=='BLOCKED_NOT_VERIFIED'
    assert result['blockers'][0]['stage']=='forge_build'


def test_does_not_overwrite_prior_run_or_workspace(tmp_path):
    first=tmp_path/'existing.json'
    first.write_text('old')
    result=smoke.execute(smoke.FIXTURE,first)
    assert result['status']=='BLOCKED_NOT_VERIFIED'
    assert first.read_text()=='old'


def test_refuses_unsafe_program_before_solidity_generation(tmp_path):
    bad=json.loads(smoke.FIXTURE.read_text())
    for rule in bad['rules']:
        if rule['name']=='approve_final':
            rule['credits'][0]['amount_wei']=4*10**18
    filepath=tmp_path/'bad.json';filepath.write_text(json.dumps(bad))
    result=smoke.execute(filepath,tmp_path/'blocked.json')
    assert result['status']=='BLOCKED_NOT_VERIFIED'
    assert 'INVALID_OR_NONCONSERVING_CREDIT_PATH' in result['graph_audit']['blockers']
    assert not (tmp_path/'blocked.workspace').exists()


def test_v2_accepts_and_verifies_original_v2_shape_unchanged():
    template=json.loads(smoke.FIXTURE.read_text())
    assert template['language'] == v3.LANG
    legacy={
       'language':'nanojev.contract.state_machine.v2',
       'settlement_mode':'pull', 'asset':'native_eth',
       'payer':'deployer', 'worker':'constructor_worker', 'reviewer':'constructor_reviewer',
       'amount_wei':4*10**18, 'deadline_seconds':5*86400,
       'rules':[
         {'name':'cancel','actor':'payer','from':'funded','to':'cancelled',
          'when':'before_deadline','payments':[{'to':'payer','bps':10000}]},
         {'name':'release','actor':'reviewer','from':'funded','to':'settled',
          'when':'at_or_after_deadline','payments':[{'to':'worker','bps':10000}]},
         {'name':'rescue_release','actor':'payer','from':'funded','to':'settled',
          'when':'at_or_after_deadline','payments':[{'to':'worker','bps':10000}]}]}
    assert v2.validate(legacy)['language']=='nanojev.contract.state_machine.v2'
    assert v2check.verify(legacy)['status']=='PASS_CONDITIONAL'


def test_custom_v3_source_generates_without_fixture_specific_adversarial_suite(tmp_path):
    spec=json.loads(smoke.FIXTURE.read_text())
    spec['amount_wei']=6*10**18
    for rule in spec['rules']:
        for credit in rule['credits']:
            if isinstance(credit['amount_wei'],int):credit['amount_wei']//=2
    # All credit and remaining amounts adjusted; same topology, different economic deal.
    source=tmp_path/'custom.json';source.write_text(json.dumps(spec))
    result=smoke.execute(source,tmp_path/'custom_result.json')
    assert result['status']=='PASS_V3_IR_AND_SOLIDITY_GENERATED_FORGE_NOT_RUN'
    assert result['generated']['pinned_adversarial_forge_test_count']==0
    assert result['generated']['generic_forge_test_count']>=20


def test_help_and_diagnostic_cli_run(tmp_path):
    import subprocess,sys
    destination=tmp_path/'cli-report.json'
    run=subprocess.run([sys.executable,'-m','tools.nanojev_contract_v3_smoke',
                        '--output',str(destination)], capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    assert 'PASS_V3_IR_AND_SOLIDITY_GENERATED_FORGE_NOT_RUN' in run.stdout
    assert destination.exists()
