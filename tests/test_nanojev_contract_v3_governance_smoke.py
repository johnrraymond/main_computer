"""Governed escrow CLI, integrity, denial, and honest Forge evidence tests."""
from __future__ import annotations
from copy import deepcopy
import json
import re
import subprocess
import sys
from pathlib import Path
import pytest
from tools import nanojev_contract_v3_governance_smoke as runner
from tools import nanojev_contract_v3_governance_language as language
from tools import nanojev_contract_v3_governance_verifier as audit
from tools import nanojev_contract_v3_governance_solidity as emitter
from tools import nanojev_contract_v3_governance_foundry as foundry


def fixture():
    return json.loads(runner.FIXTURE.read_text())


def test_static_runner_matches_gate_and_emits_complete_suite(tmp_path):
    result=runner.execute(runner.FIXTURE,tmp_path/'governance.json')
    assert result['status']=='PASS_V3_GOVERNANCE_IR_AND_SOLIDITY_GENERATED_FORGE_NOT_RUN'
    assert result['kernel_frozen']
    assert result['forge']=='NOT_REQUESTED'
    assert result['graph_audit']['status']=='PASS_CONDITIONAL'
    assert len(result['graph_audit']['liveness']['traps'])==0
    assert result['generated']['foundry_test_count']>=30
    workspace=tmp_path/'governance.workspace'
    src=(workspace/'src'/'NanoJevGoverned01.sol').read_text()
    suite=(workspace/'test'/'NanoJevGoverned01Generated.t.sol').read_text()
    assert 'contract NanoJevGoverned01' in src
    assert 'function resolveUnappointed()' in src
    assert 'function resolveArbitratorSilence()' in src
    assert 'function testFuzzWithdrawOrder(uint8 selector)' in suite
    assert len(re.findall(r'\bfunction\s+test\w*\s*\(',suite))==result['generated']['foundry_test_count']
    assert not re.search(r'\bfunction\s+testFail\w*\(',suite)


def test_failed_forge_does_not_report_pass(tmp_path):
    calls=[]
    def rejected(args,timeout):
        calls.append(args)
        return {'exit_code':1,'stdout_tail':'','stderr_tail':'fail'}
    report=runner.execute(runner.FIXTURE,tmp_path/'failed.json',forge=True,docker_command=rejected)
    assert report['status']=='BLOCKED_NOT_VERIFIED'
    assert report['blockers'][0]['stage']=='forge_build'
    assert len(calls)==1
    assert report['forge']=='NOT_RUN'


def test_injected_forge_never_certifies_real_foundry(tmp_path):
    calls=[]
    def pretend(args,timeout):
        calls.append(args)
        return {'exit_code':0,'stdout_tail':'mock','stderr_tail':''}
    result=runner.execute(runner.FIXTURE,tmp_path/'fake.json',forge=True,docker_command=pretend)
    assert result['status']=='PASS_INJECTED_FORGE_NOT_CERTIFIED'
    assert result['forge']=='INJECTED_NOT_REAL_FORGE'
    assert len(calls)==2


def test_existing_output_not_overwritten(tmp_path):
    p=tmp_path/'prev.json';p.write_text('DO NOT OVERWRITE')
    result=runner.execute(runner.FIXTURE,p)
    assert result['status']=='BLOCKED_NOT_VERIFIED'
    assert p.read_text()=='DO NOT OVERWRITE'


def test_bad_spec_cannot_create_solidity(tmp_path):
    spec=fixture();spec['policy']['on_arbitrator_silence']='never'
    source=tmp_path/'invalid.json';source.write_text(json.dumps(spec))
    result=runner.execute(source,tmp_path/'result.json')
    assert result['status']=='BLOCKED_NOT_VERIFIED'
    assert not (tmp_path/'result.workspace').exists()


def test_alternate_shape_and_fallbacks_supported(tmp_path):
    for n in range(1,6):
        x=fixture()
        x['milestones']=[deepcopy(x['milestones'][i%3]) for i in range(n)]
        for i,m in enumerate(x['milestones']):m['id']=f'tranch_{i}'
        x['amount_wei']=sum(m['amount_wei'] for m in x['milestones'])
        x['policy']['on_appointment_failure']='pay_worker'
        x['policy']['on_review_silence']='refund_payer'
        x['policy']['on_arbitrator_silence']='pay_worker'
        path=tmp_path/f'alternate-{n}.json';path.write_text(json.dumps(x))
        result=runner.execute(path,tmp_path/f'alternate-{n}-report.json')
        assert result['status']=='PASS_V3_GOVERNANCE_IR_AND_SOLIDITY_GENERATED_FORGE_NOT_RUN'
        assert result['graph_audit']['reachable_nodes']>0
        assert f'MILESTONES = {n}' in (tmp_path/f'alternate-{n}-report.workspace'/'src'/'NanoJevGoverned01.sol').read_text()


def test_cli_help_and_actual_IR_run(tmp_path):
    out=tmp_path/'cli-gov.json'
    proc=subprocess.run([sys.executable,'-m','tools.nanojev_contract_v3_governance_smoke',
                         '--output',str(out)],capture_output=True,text=True)
    assert proc.returncode==0,proc.stderr+'\n'+proc.stdout
    assert 'PASS_V3_GOVERNANCE_IR_AND_SOLIDITY_GENERATED_FORGE_NOT_RUN' in proc.stdout
    assert out.is_file()
