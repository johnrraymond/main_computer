"""Contract Language v3 held-out corpus: frozen-kernel generalization gate."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import re
import pytest

from tools import nanojev_contract_v3_generalization as g
from tools import nanojev_contract_v3_language as lang
from tools import nanojev_contract_v3_reference as ref
from tools import nanojev_contract_v3_verifier as graph
from tools import nanojev_contract_v3_smoke as smoke


def corpus():
    return g.preflight()[1]


def spec(case):
    return json.loads((g.CORPUS/case['file']).read_text(encoding='utf-8'))


def test_core_digest_frozen_against_preexisting_v3_patch():
    _, cases=g.preflight()
    assert len(g.KERNEL_SHA256)==6
    assert len(cases)==13
    assert [x['expect'] for x in cases].count('accept')==6
    assert [x['expect'] for x in cases].count('reject')==7
    assert 'nanojev_contract_v3_generalization.py' not in g.KERNEL_SHA256


@pytest.mark.parametrize('case',corpus(),ids=lambda c:c['id'])
def test_heldout_case_has_correct_acceptance_label(case):
    obj=spec(case)
    if case['expect']=='accept':
        normalized=lang.validate(obj)
        audit=graph.verify(normalized)
        assert audit['status']=='PASS_CONDITIONAL', case['id']
        assert audit['emission_authorized_by_graph_audit']
        assert not audit['blockers']
        assert audit['conservation']['status']=='PASS_IR'
        assert not audit['conflicts']['divergent']
        assert audit['liveness']['status']=='PASS_CONDITIONAL'
        assert g.check_manual_oracles(normalized,case)
    elif case['expected_stage']=='schema':
        with pytest.raises(lang.V3Error):lang.validate(obj)
    else:
        audit=graph.verify(obj)
        assert not audit['emission_authorized_by_graph_audit']
        assert set(case['expected_blockers']) <= set(audit['blockers'])


@pytest.mark.parametrize('case',[x for x in corpus() if x['expect']=='accept'], ids=lambda c:c['id'])
def test_literal_trace_oracle_and_solidity_suite(case):
    normalized=lang.validate(spec(case))
    oracles=g.check_manual_oracles(normalized,case)
    golden,count=g.manual_foundry_suite(normalized,case)
    assert count==len(oracles)>=2
    assert len(re.findall(r'\bfunction\s+testManualOracle\d+\s*\(',golden)) == len(oracles)
    assert 'require(c.escrowConserved()' in golden
    assert not re.search(r'\bfunction\s+testFail\w+\s*\(',golden)
    for role in lang.ROLES:
        assert 'gold owed '+role in golden
    for trace in oracles:
        assert trace['expected']['unallocated_wei']==0
        assert sum(trace['expected']['pending_wei'].values())+sum(trace['expected']['withdrawn_wei'].values())==normalized['amount_wei']


def test_manual_expected_balances_cannot_be_replaced_by_self_fulfilling_model():
    target=next(x for x in corpus() if x['id']=='three_actor_fee_chain')
    wrong=copy.deepcopy(target)
    wrong['trace_oracles'][0]['expected']['pending_wei']['worker']+=1
    with pytest.raises(lang.V3Error,match='Gold/reference mismatch'):
        g.check_manual_oracles(lang.validate(spec(wrong)),wrong)


def test_graph_adversarial_fixtures_are_all_blocked_without_codegen(tmp_path):
    for c in corpus():
        if c['expect']!='reject':continue
        output=tmp_path/(c['id']+'.json')
        result=smoke.execute(g.CORPUS/c['file'],output)
        assert result['status']=='BLOCKED_NOT_VERIFIED'
        assert not (tmp_path/(c['id']+'.workspace')).exists()


def test_generalization_runner_runs_to_completion_without_forge(tmp_path):
    out=tmp_path/'heldout.json'
    report=g.execute(out)
    assert report['status']=='PASS_STATIC_HELDOUT_V3_CORPUS_FORGE_NOT_RUN',report['blockers']
    assert report['accepted_contracts']==6
    assert report['rejected_counterexamples']==7
    assert report['expected_forge_tests']==128
    assert report['passed_forge_tests']==0
    assert report['kernel_frozen']
    assert out.is_file()
    assert len(report['cases'])==13
    assert sum(x['result']=='PASS_STATIC' for x in report['cases'])==6
    assert sum(x['result']=='PASS_REFUSED' for x in report['cases'])==7
    for c in report['cases']:
        workspace=tmp_path/(out.stem+'-'+c['id']+'.workspace')
        if c['expect']=='accept':
            assert (workspace/'src'/'NanoJevProgressive01.sol').exists()
            assert (workspace/'test'/'NanoJevProgressive01Generated.t.sol').exists()
            golden=workspace/'test'/'NanoJevProgressive01ManualOracle.t.sol'
            assert golden.exists()
            assert c['manually_authored_forge_tests']>0
        else:
            assert not workspace.exists()


def test_existing_report_is_never_overwritten(tmp_path):
    out=tmp_path/'exists.json';out.write_text('original')
    result=g.execute(out)
    assert result['status']=='BLOCKED_NOT_VERIFIED'
    assert out.read_text()=='original'


def test_kernel_hash_mismatch_fails_closed(monkeypatch,tmp_path):
    altered=dict(g.KERNEL_SHA256)
    altered['nanojev_contract_v3_language.py']='0'*64
    monkeypatch.setattr(g,'KERNEL_SHA256',altered)
    result=g.execute(tmp_path/'corrupt.json')
    assert result['status']=='BLOCKED_NOT_VERIFIED'
    assert result['blockers'][0]['stage']=='kernel_freeze'
    assert result['cases']==[]


def test_manifest_digest_mismatch_fails_closed(monkeypatch,tmp_path):
    monkeypatch.setattr(g,'CORPUS_MANIFEST_SHA256','0'*64)
    result=g.execute(tmp_path/'corrupt.json')
    assert result['status']=='BLOCKED_NOT_VERIFIED'
    assert result['blockers'][0]['stage']=='corpus_freeze'


def test_forge_mode_never_claims_pass_when_docker_unavailable(tmp_path,monkeypatch):
    old=g.shutil.which
    monkeypatch.setattr(g.shutil,'which',lambda name: None if name=='docker' else old(name))
    result=g.execute(tmp_path/'no-docker.json',forge=True)
    assert result['status']=='BLOCKED_NOT_VERIFIED'
    assert result['forge']=='NOT_RUN'
    assert result['blockers'][0]['stage']=='forge_preflight'


def test_cli_static_mode(tmp_path):
    import subprocess,sys
    report=tmp_path/'cli.json'
    result=subprocess.run([sys.executable,'-m','tools.nanojev_contract_v3_generalization','--output',str(report)],
                          text=True,capture_output=True)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    assert 'PASS_STATIC_HELDOUT_V3_CORPUS_FORGE_NOT_RUN' in result.stdout
    assert json.loads(report.read_text())['accepted_contracts']==6
