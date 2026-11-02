"""P3.6 evidence must not elevate controlled or source-level proof into live certification."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from tools.mcel_p36_evidence import (init_report,set_gate,reconcile,validate_host_url,
                                    evidence_manifest,safe_json)
from tools.mcel_p36_parity import source_ledger
from tools.mcel_p36_isolated_effects import isolated_schedule_store
from tools.mcel_p36_acceptance_frob import run,ROOT


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
    from tools.mcel_p36_acceptance_frob import _review
    invalid=tmp_path/'review.json';safe_json(invalid,{'approved':True,'reviewerRole':'operator','reviewedAt':'2026-11-01'})
    _review(invalid,report,tmp_path)
    assert report['gates']['P3_HUMAN_DESIGN']['status']=='BLOCKED'


def test_manifest_excludes_self_and_hashes_real_files(tmp_path):
    (tmp_path/'e.txt').write_text('evidence')
    result=evidence_manifest(tmp_path)
    assert result['e.txt']
    assert 'report.json' not in result
