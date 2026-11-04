"""Stable Task Manager acceptance regression tests; migrated without weakening negative gates."""
from __future__ import annotations

import json

from pathlib import Path

import pytest

from tools.mcel_task_manager_acceptance.evidence import (init_report,set_gate,reconcile,validate_host_url,
                                    evidence_manifest,safe_json)

from tools.mcel_task_manager_acceptance.evidence import source_ledger

from tools.mcel_task_manager_acceptance.lab import isolated_schedule_store

from tools.mcel_task_manager_acceptance.__main__ import run,ROOT

def test_loopback_only_explicit_port_and_zero_credentials():
    assert validate_host_url('http://127.0.0.1:8765')=='http://127.0.0.1:8765'
    for value in ('http://other.example:8765','https://localhost:8765','http://localhost',
                  'http://user:password@localhost:8765','http://127.0.0.1:8765/foo',
                  'http://localhost:8765?x=1','file:///tmp/a'):
        with pytest.raises(ValueError):validate_host_url(value)

def test_gates_cannot_promote_without_independent_evidence():
    report=init_report('run',{})
    for k in ('P36_BASELINE','P3_WINDOWS_HOST','P36_PROCESS_EFFECT','P36_SCHEDULE_EFFECT',
              'P36_SERVER_LIFECYCLE_EFFECT','P3_PRODUCTION_PARITY'):
        set_gate(report,k,'PASS')
    reconcile(report)
    assert report['gates']['P3_REAL_SIDE_EFFECT']['status']=='PASS'
    assert report['gates']['P3_HUMAN_DESIGN']['status']=='UNVERIFIED'
    assert report['gates']['P3_PROMOTION']['status']=='BLOCKED'
    assert report['outcome']=='BLOCKED_INCOMPLETE_EVIDENCE'
    set_gate(report,'P36_SERVER_LIFECYCLE_EFFECT','BLOCKED')
    reconcile(report)
    assert report['gates']['P3_REAL_SIDE_EFFECT']['status']=='BLOCKED'
    set_gate(report,'P36_PROCESS_EFFECT','FAIL')
    reconcile(report)
    assert report['outcome']=='FAIL_ACCEPTANCE'

def test_isolated_schedule_real_persistence_without_server_effect():
    actual=isolated_schedule_store()
    assert actual['status']=='PASS', actual
    assert actual['persisted'] and actual['staleRefused']
    assert actual['manualLifecycleActionDispatched'] is False

def test_source_ledger_not_live_parity():
    result=source_ledger(ROOT)
    assert {v['workflow'] for v in result['rows']}>={'overview-parameters','server-lifecycle','ai-brief'}
    assert result['gaps']==[]
    assert all(v['liveParity']=='UNVERIFIED' for v in result['rows'])

def test_default_unavailable_host_fails_closed(tmp_path):
    rc=run(['--mode','observe','--report-dir',str(tmp_path)])
    assert rc==0 # lack of externally supplied host is BLOCKED, not an execution crash
    result=json.loads((tmp_path/'report.json').read_text())
    assert result['gates']['P3_WINDOWS_HOST']['status']=='BLOCKED'
    assert result['gates']['P3_PROMOTION']['status']=='BLOCKED'
    assert result['gates']['P3_REAL_SIDE_EFFECT']['status']=='BLOCKED'
    assert result['gates']['P3_HUMAN_DESIGN']['status']=='UNVERIFIED'
    assert result['outcome']=='BLOCKED_INCOMPLETE_EVIDENCE'
    assert (tmp_path/'parity-ledger.json').exists()
    assert (tmp_path/'human-review-template.json').exists()
    assert (tmp_path/'evidence-manifest.json').exists()

def test_reviewer_template_cannot_self_certify(tmp_path):
    report=init_report('r',{})
    from tools.mcel_task_manager_acceptance.__main__ import _review
    invalid=tmp_path/'review.json';safe_json(invalid,{'approved':True,'reviewerRole':'operator','reviewedAt':'2026-11-01'})
    _review(invalid,report,tmp_path)
    assert report['gates']['P3_HUMAN_DESIGN']['status']=='BLOCKED'

def test_manifest_excludes_self_and_hashes_real_files(tmp_path):
    (tmp_path/'e.txt').write_text('evidence')
    result=evidence_manifest(tmp_path)
    assert result['e.txt']
    assert 'report.json' not in result

import json

from pathlib import Path

import tools.mcel_task_manager_acceptance.__main__ as af

import tools.mcel_task_manager_acceptance.hosted as hp

from tools.mcel_task_manager_acceptance.evidence import init_report, safe_json, evidence_manifest, set_gate

HOST = 'http://127.0.0.1:8765'

SHA = 'a'*64

def _stubs(monkeypatch, *, browser_pass=False):
    monkeypatch.setattr(af, '_bounded', lambda *args, **kwargs: {'status':'PASS','reason':'test only'})
    monkeypatch.setattr(af, 'source_ledger', lambda *_: {'gaps':[], 'rows':[]})
    monkeypatch.setattr(af, 'isolated_schedule_store', lambda: {'status':'PASS','reason':'stub'})
    calls={'effect':0,'browser':0}

    def browser(_host, _out):
        calls['browser']+=1
        return {'passed':browser_pass, 'widths': {'320':{'passed':browser_pass,'reason':'controlled-browser-failure'}}}

    def effect(_host, *, ack=False):
        assert ack is True and _host == HOST
        calls['effect']+=1
        return {'status':'PASS', 'reason':'controlled independent disposable child effects'}

    monkeypatch.setattr(hp, 'probe', browser)
    monkeypatch.setattr(af, 'disposable_process_effects', effect)
    return calls

def _report(path):
    return json.loads((path/'report.json').read_text(encoding='utf-8'))

def _carry_fixture(path, *, host=HOST, tamper=False):
    path.mkdir()
    source_hashes={rel:af.digest(af.ROOT/rel) for rel in af.SOURCES}
    report=init_report('previous-observe',source_hashes)
    report['host']=host
    report['mode']='observe'
    set_gate(report,'P3_WINDOWS_HOST','PASS','Verified live browser')
    hosted={'host':host,'route':hp.ROUTE,'passed':True,
            'widths':{str(w):{'passed':True,'hostPageSha256':SHA,
                            'expectedPageSha256':SHA,
                            **({'osPidConfirmed':True} if w==1100 else {})}
                      for w in hp.WIDTHS}}
    safe_json(path/'hosted-probe.json',hosted)
    report['artifactManifest']=evidence_manifest(path)
    safe_json(path/'report.json',report)
    if tamper:
        (path/'hosted-probe.json').write_text('{}')
    return path/'report.json'

def test_real_effect_gate_does_not_depend_on_second_browser_sweep(monkeypatch,tmp_path):
    calls=_stubs(monkeypatch,browser_pass=False)
    rc=af.run(['--mode','isolated-effects','--ack-isolated-effects',
               '--host-url',HOST,'--report-dir',str(tmp_path)])
    result=_report(tmp_path)
    assert rc==0
    assert calls=={'effect':1,'browser':1}
    assert result['gates']['P3_WINDOWS_HOST']['status']=='BLOCKED'
    assert result['gates']['P36_PROCESS_EFFECT']['status']=='PASS'
    assert result['gates']['P3_REAL_SIDE_EFFECT']['status']=='BLOCKED'
    assert result['gates']['P3_PROMOTION']['status']=='BLOCKED'

def test_ack_is_mandatory_even_if_browser_fails(monkeypatch,tmp_path):
    calls=_stubs(monkeypatch,browser_pass=False)
    af.run(['--mode','isolated-effects','--host-url',HOST,'--report-dir',str(tmp_path)])
    assert calls['effect']==0
    assert _report(tmp_path)['gates']['P36_PROCESS_EFFECT']['status']=='BLOCKED'

def test_verified_prior_host_skips_duplicate_browser_but_rechecks_live_page(monkeypatch,tmp_path):
    calls=_stubs(monkeypatch,browser_pass=False)
    monkeypatch.setattr(af,'_expected_host_page_sha256',lambda: SHA)
    live=[]
    monkeypatch.setattr(af,'_verify_current_host_page',lambda host:live.append(host) or SHA)
    previous=_carry_fixture(tmp_path/'observe')
    out=tmp_path/'effect'
    af.run(['--mode','isolated-effects','--ack-isolated-effects',
            '--host-url',HOST,'--carry-report',str(previous),'--report-dir',str(out)])
    report=_report(out)
    assert calls=={'effect':1,'browser':0}
    assert live==[HOST]
    assert report['gates']['P3_WINDOWS_HOST']['status']=='PASS'
    assert report['gates']['P36_PROCESS_EFFECT']['status']=='PASS'
    assert report['carriedEvidence'][0]['status']=='VERIFIED'
    assert report['gates']['P3_PROMOTION']['status']=='BLOCKED'

def test_carried_host_from_different_port_is_refused(monkeypatch,tmp_path):
    calls=_stubs(monkeypatch,browser_pass=False)
    monkeypatch.setattr(af,'_expected_host_page_sha256',lambda: SHA)
    previous=_carry_fixture(tmp_path/'observe',host='http://127.0.0.1:9999')
    out=tmp_path/'effect'
    af.run(['--mode','isolated-effects','--ack-isolated-effects',
            '--host-url',HOST,'--carry-report',str(previous),'--report-dir',str(out)])
    report=_report(out)
    assert report['carriedEvidence'][0]['status']=='REFUSED'
    assert report['carriedEvidence'][0]['reason']=='carried-evidence-host-mismatch'
    assert report['gates']['P3_WINDOWS_HOST']['status']=='BLOCKED'
    assert calls['effect']==1

def test_tampered_carried_browser_artifact_is_refused(monkeypatch,tmp_path):
    calls=_stubs(monkeypatch,browser_pass=False)
    previous=_carry_fixture(tmp_path/'observe',tamper=True)
    out=tmp_path/'effect'
    af.run(['--mode','isolated-effects','--ack-isolated-effects',
            '--host-url',HOST,'--carry-report',str(previous),'--report-dir',str(out)])
    report=_report(out)
    assert report['carriedEvidence'][0]['status']=='REFUSED'
    assert 'evidence-hash-mismatch' in report['carriedEvidence'][0]['reason']
    assert report['gates']['P3_WINDOWS_HOST']['status']=='BLOCKED'
    assert calls['effect']==1

import json

from types import SimpleNamespace

import tools.mcel_task_manager_acceptance.__main__ as frob

def test_combined_pytest_failure_names_are_reported(monkeypatch, tmp_path, capsys):
    failure = ('F                                                                    [100%]\n'
               'FAILED tests/test_mcel_p3_operational.py::test_generation_required_in_service_even_without_http_marker\n'
               '1 failed, 69 passed in 50.0s\n')
    original_run = frob.subprocess.run
    def stub_only_pytest(command, *args, **kwargs):
        if len(command) >= 3 and command[1:3] == ['-m', 'pytest']:
            return SimpleNamespace(returncode=1, stdout=failure, stderr='')
        return original_run(command, *args, **kwargs)
    monkeypatch.setattr(frob.subprocess, 'run', stub_only_pytest)
    assert frob.bounded_main(['--report-dir', str(tmp_path)]) == 1
    report = json.loads((tmp_path/'report.json').read_text(encoding='utf-8'))
    assert report['outcome'] == 'FAIL_OR_UNVERIFIED'
    assert report['statuses']['P3_AUTHORING'] == 'PASS'
    assert report['statuses']['P3_BASELINE'] == 'FAIL_BOUNDED'
    assert report['evidence']['pytestFailures'] == [
        'tests/test_mcel_p3_operational.py::test_generation_required_in_service_even_without_http_marker']
    printed = json.loads(capsys.readouterr().out)
    assert printed['failingTests'] == report['evidence']['pytestFailures']
    assert report['statuses']['P3_PROMOTION'] == 'BLOCKED'



def test_parity_effect_ack_without_isolated_host_is_refused(monkeypatch,tmp_path):
    import tools.mcel_task_manager_acceptance.__main__ as cli
    def never_called(*args,**kwargs):
        raise AssertionError('production browser must not run with ambiguous effect acknowledgment')
    monkeypatch.setattr('tools.mcel_task_manager_acceptance.hosted.live_readonly_parity',never_called)
    rc=cli.main(['parity','--host-url','http://127.0.0.1:8765',
                 '--ack-isolated-effects','--report-dir',str(tmp_path)])
    report=json.loads((tmp_path/'report.json').read_text())
    assert rc==0 and report['status']=='BLOCKED'
    assert report['productionMutations'] is False
    assert 'refused' in report['reason']


def test_workflow_results_cannot_convert_blockers_to_pass():
    from tools.mcel_task_manager_acceptance.hosted import _parity_totals,_parity_result
    rows={name:{'status':'UNVERIFIED'} for name in ('refresh','inspection','scheduling')}
    _parity_result(rows,'refresh','PASS',evidence=['real periodic HTTP trace'])
    _parity_result(rows,'inspection','FAIL',reason='wrong selected process')
    _parity_result(rows,'scheduling','BLOCKED',reason='isolated host absent')
    total=_parity_totals(rows)
    assert total['status']=='FAIL'
    assert total['fail']==['inspection']
    assert total['blocked']==['scheduling']
    assert total['pass']==['refresh']


def test_bounded_integration_command_fails_closed_and_never_promotes(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from tools.mcel_task_manager_acceptance.__main__ import integration_main
    import tools.mcel_task_manager_acceptance.__main__ as runner
    monkeypatch.setattr(runner.subprocess, 'run', lambda *args, **kw:
        SimpleNamespace(returncode=0, stdout='4 passed, 1 skipped in 1.0s', stderr=''))
    rc=integration_main(['--report-dir', str(tmp_path / 'skip')])
    report=json.loads((tmp_path/'skip'/'report.json').read_text())
    assert rc==1 and report['status']=='FAIL_OR_UNVERIFIED'
    assert report['promotionAuthorized'] is False
    assert 'real Windows PowerShell controller fidelity' in report['notCertified']
    monkeypatch.setattr(runner.subprocess, 'run', lambda *args, **kw:
        SimpleNamespace(returncode=1, stdout='4 passed, 1 failed', stderr=''))
    rc=integration_main(['--report-dir', str(tmp_path / 'failure')])
    assert rc==1
    assert json.loads((tmp_path/'failure'/'report.json').read_text())['status']=='FAIL_OR_UNVERIFIED'
