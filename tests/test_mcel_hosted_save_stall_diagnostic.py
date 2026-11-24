"""Stall-specific regression tests: no credential or source disclosure, no host changes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

import pytest

from main_computer import mcel_editor_save_stall_probe as probe
from main_computer import mcel_project_edit_transaction as engine
from tools import mcel_hosted_save_stall_diagnostic as diag


def test_only_exact_disposable_paths_enable_backend_probe(tmp_path):
    assert probe.is_disposable_save_path('runtime/reports/mcel-hosted-signal-abc/reference.js')
    assert probe.is_disposable_save_path('runtime\\reports\\mcel-hosted-signal-abc\\candidate.js')
    for value in ('runtime/reports/ordinary/reference.js',
                  'runtime/reports/mcel-hosted-signal-abc/README.md',
                  'runtime/reports/mcel-hosted-signal-abc/reference.js/../../important.py',
                  '../runtime/reports/mcel-hosted-signal-abc/reference.js',
                  'runtime/reports/mcel-hosted-signal-abc/other.js',None,''):
        assert not probe.is_disposable_save_path(value)
    with probe.observe_save(tmp_path, 'runtime/reports/ordinary/reference.js'):
        with probe.stage('nothing'):
            pass
    assert not (diag._report_base(tmp_path)/'server').exists()


def test_probe_writes_content_free_stages_outside_repository(tmp_path):
    workspace=tmp_path/'workspace'
    workspace.mkdir()
    with probe.observe_save(workspace,'runtime/reports/mcel-hosted-signal-xyz/reference.js'):
        with probe.stage('prepare.manifest_hash'):
            pass
        with probe.stage('prepare.stage_copy'):
            pass
    traces=diag._traces(workspace)
    assert len(traces)==1
    item=traces[0]
    assert item['status']=='settled'
    assert item['lastEvent']=='request_complete'
    assert item['stageTimings'][0]['stage']=='prepare.manifest_hash'
    assert item['stageTimings'][1]['stage']=='prepare.stage_copy'
    assert 'server' in item['path']
    assert not (workspace/'runtime').exists()
    lines=Path(item['path']).read_text()
    for secret in ['reference.js','mcel-hosted-signal-xyz',str(workspace)]:
        assert secret not in lines


def test_probe_error_does_not_hide_original_error(tmp_path):
    with pytest.raises(LookupError,match='sample'):
        with probe.observe_save(tmp_path,'runtime/reports/mcel-hosted-signal-test/candidate.js'):
            with probe.stage('prepare.stage_copy'):
                raise LookupError('sample')
    trace=diag._traces(tmp_path)[0]
    assert trace['lastEvent']=='request_error'
    assert trace['stageTimings'][-1]['status']=='error'


def test_stage_probe_does_not_change_actual_prepared_transaction(tmp_path):
    workspace=tmp_path/'small_repo'
    workspace.mkdir()
    original=b'export const test = 1;'
    (workspace/'candidate.js').write_bytes(original)
    # dry-run uses the exact actual tool from the installed checkout; it is
    # copied only into this temporary synthetic repo.
    source=Path(__file__).resolve().parents[1]/'new_patch.py'
    assert source.is_file()
    (workspace/'new_patch.py').write_bytes(source.read_bytes())
    output=tmp_path/'transaction_out'
    with probe.observe_save(workspace,'runtime/reports/mcel-hosted-signal-proof/reference.js'):
        result=engine.prepare_project_edit_transaction(
            repo_root=workspace, project_root='.',
            changes=[{'operation':'modify','path':'candidate.js',
                      'expected_before_sha256':hashlib.sha256(original).hexdigest(),
                      'replacement_text':'export const test = 2;'}],
            output_dir=output,validations=[],artifact_name='mcel-code-editor-project-edit.zip')
    assert result['ok'] is True
    assert (workspace/'candidate.js').read_bytes()==original
    with probe.observe_save(workspace,'runtime/reports/mcel-hosted-signal-proof/candidate.js'):
        receipt=engine.apply_project_edit_transaction(
            repo_root=workspace,transaction=Path(result['report_path']),reviewed=True)
    assert receipt['ok'] is True
    assert (workspace/'candidate.js').read_bytes()==b'export const test = 2;'
    applied=[t for t in diag._traces(workspace) if any(x['stage']=='apply.atomic_replacement' for x in t['stageTimings'])]
    assert len(applied)==1
    assert {x['stage'] for x in applied[0]['stageTimings']} >= {
        'apply.manifest_hash','apply.compare_manifest','apply.atomic_replacement','apply.receipt_write'}
    prepare=next(t for t in diag._traces(workspace) if any(
        stage['stage']=='prepare.manifest_hash' for stage in t['stageTimings']))
    stages=[t['stage'] for t in prepare['stageTimings']]
    for expected in ('prepare.manifest_hash','prepare.stage_copy','prepare.stage_apply',
                     'prepare.validations','prepare.package_overlay',
                     'prepare.new_patch_dry_run','prepare.report_write'):
        assert expected in stages


def test_classify_backend_active_or_no_entry():
    basic={'hostPid':77,'latestHealth':{'connected':True},'sessions':[]}
    assert diag._classification(basic)=='PRE_SAVE_NOT_REACHED'
    assert diag._classification({**basic,'lastAcceptanceProgress':'observe-directory-2_incumbent'})==\
           'PRE_SAVE_STALL_AT_observe-directory-2_incumbent'
    assert diag._classification({**basic,'saveAttempted':True,'latestHealth':{'connected':False}})==\
           'HOST_UNRESPONSIVE_AFTER_SAVE_ATTEMPT'
    assert diag._classification({**basic,'saveAttempted':True})==\
           'SAVE_ATTEMPT_NO_INSTRUMENTED_SERVER_ENTRY'
    assert diag._classification({**basic,'sessions':[{'serverPid':77,'lastStage':'prepare.stage_copy',
            'status':'active'}]})=='SERVER_REQUEST_ACTIVE_AT_prepare.stage_copy'
    assert diag._classification({**basic,'sessions':[{'serverPid':77,'lastStage':'between_stages',
            'status':'settled','lastEvent':'request_error'}]})=='SERVER_REQUEST_ENDED_WITH_ERROR'


def test_inspection_does_not_issue_live_save(tmp_path,monkeypatch):
    (tmp_path/'tools').mkdir()
    (tmp_path/'tools/mcel_hosted_signal_cutover_acceptance.py').write_bytes(b'# only for path check\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(diag,'_port_owner',lambda host,port:(None,'missing'))
    monkeypatch.setattr(diag,'_host_health',lambda url:{'connected':False,'errorClass':'ConnectionRefusedError'})
    output=diag.run('http://127.0.0.1:8765',read_history=False,write_authorized=False,
                    inspect_only=True,report_dir=tmp_path/'reports')
    assert output['liveReproductionStarted'] is False
    assert output['acceptanceExitCode'] is None
    assert (tmp_path/'reports/report.json').is_file()


def test_live_save_requires_explicit_authorizations():
    with pytest.raises(ValueError,match='BOTH_EXPLICIT_AUTHORIZATIONS'):
        diag.run('http://127.0.0.1:8765',read_history=True,write_authorized=False)
    with pytest.raises(ValueError,match='HOST_URL_MUST_BE_EXACT_LOCAL_LOOPBACK'):
        diag.run('https://example.com',read_history=True,write_authorized=True)


def test_existing_unsettled_save_refuses_duplicate_live_write(tmp_path, monkeypatch):
    (tmp_path/'tools').mkdir()
    (tmp_path/'tools/mcel_hosted_signal_cutover_acceptance.py').write_bytes(b'# sentinel\n')
    path=tmp_path/'main_computer/mcel_editor_save_stall_probe.py'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'# probe\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(diag,'_port_owner',lambda host,port:(12345,'mock'))
    monkeypatch.setattr(diag,'_process_state',lambda pid:{'startEpoch':time.time()+100})
    monkeypatch.setattr(diag,'_traces',lambda root:[{'id':'prior','status':'active',
        'serverPid':12345,'lastStage':'prepare.stage_copy'}])
    result=diag.run('http://127.0.0.1:8765',read_history=True,write_authorized=True,
                    report_dir=tmp_path/'report')
    assert result['classification']=='EXISTING_LIVE_SAVE_UNSETTLED_REPRO_REFUSED'
    assert result['liveReproductionStarted'] is False


def test_browse_intent_bounded_after_single_real_semantic_execute(tmp_path):
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from tools import mcel_hosted_cutover_disposable as disposable
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page()
            page.set_content("""<script>
              window.__calls=[];
              window.__mcelWorkbench={app:{
                receipts:()=>[],
                execute:async(intent,arg)=>{
                  window.__calls.push({intent});
                  if(arg.path==='runtime/reports')return new Promise(()=>{});
                  return {disposition:'completed'};
                }
              }};
            </script>""")
            firewall={'requests':[{'method':'POST','path':'/api/applications/editor/files'}],
                     'responses':[],'failedRequests':[],'blocked':[]}
            disposable._browse_disposable_directory(page,'runtime','incumbent',1,
                                                     firewall=firewall,timeout_ms=2500)
            with pytest.raises(RuntimeError,match='DISPOSABLE_DIRECTORY_BROWSE_TIMEOUT:incumbent:depth=2') as caught:
                disposable._browse_disposable_directory(page,'runtime/reports','incumbent',2,
                                                         firewall=firewall,timeout_ms=600)
            message=str(caught.value)
            assert '"requestCount": 1' in message
            assert '"phase": "pending"' in message
            assert 'runtime/reports' not in message
            calls=page.evaluate('window.__calls.map(x=>x.intent)')
            assert calls==['intent:browse-directory','intent:browse-directory']
            with pytest.raises(RuntimeError,match='DISPOSABLE_DIRECTORY_PREVIOUS_INTENT_UNSETTLED'):
                disposable._browse_disposable_directory(page,'runtime/reports','incumbent',2,
                                                         firewall=firewall,timeout_ms=600)
            assert page.evaluate('window.__calls.length')==2
        finally:browser.close()


def test_browse_intent_failure_is_blocked_without_reexecution():
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from tools import mcel_hosted_cutover_disposable as disposable
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page()
            page.set_content("""<script>
              window.__calls=0;
              window.__mcelWorkbench={app:{receipts:()=>[],execute:()=>{
                window.__calls++;return Promise.resolve({disposition:'failed'});
              }}};
            </script>""")
            with pytest.raises(RuntimeError,match='DISPOSABLE_DIRECTORY_NOT_AUTHORIZED_OR_FAILED'):
                disposable._browse_disposable_directory(page,'runtime','signals',1,timeout_ms=1500)
            assert page.evaluate('window.__calls')==1
        finally:browser.close()


def test_pre_save_classification_does_not_claim_backend_request():
    base={'hostPid':43848,'latestHealth':{'connected':True},
          'saveAttempted':False,'lastAcceptanceProgress':'observe-directory-2_incumbent',
          'sessions':[],'harnessDeadlineExceeded':True}
    assert diag._classification(base)=='PRE_SAVE_STALL_AT_observe-directory-2_incumbent'
    assert 'SERVER_ENTRY' not in diag._classification(base)


def test_live_runner_attests_exact_imported_browse_protocol():
    """The module launched by `python -m` must be 7C, not just the ZIP on disk."""
    from tools import mcel_hosted_cutover_disposable as disposable
    repo=Path(__file__).resolve().parents[1]
    result=diag._attest_browse_module(repo)
    assert result['verified'] is True
    assert result['reason']=='imported-protocol-and-bytes-match'
    assert result['importedFromExpectedPath'] is True
    assert result['diskSha256']==result['importedSha256']
    assert result['importedProtocol']==disposable.FORENSIC_BROWSE_PROTOCOL
    assert result['importedProtocol']=='ONE_SHOT_BOUNDED_DIRECTORY_V7C'


def test_live_runner_rejects_unpatched_browser_before_any_live_save(tmp_path,monkeypatch):
    """A 7A-style browse module must refuse before the acceptance child runs."""
    browser=tmp_path/'tools/mcel_hosted_cutover_disposable.py'
    browser.parent.mkdir(parents=True)
    browser.write_text('ORIGINAL=\"old\"\n',encoding='utf8')
    (tmp_path/'tools/mcel_hosted_signal_cutover_acceptance.py').write_bytes(b'# installed\n')
    result=diag._attest_browse_module(tmp_path)
    assert result['verified'] is False
    assert result['reason']=='browse-source-hash-does-not-match-patch-7c'
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(diag,'_port_owner',lambda host,port:(None,'not-observed'))
    monkeypatch.setattr(diag,'_traces',lambda repo:[])
    def reject_subprocess(*args,**kwargs):
        raise AssertionError('no subprocess must be started before attestation')
    monkeypatch.setattr(diag.subprocess,'Popen',reject_subprocess)
    out=diag.run('http://127.0.0.1:8765',read_history=True,
                 write_authorized=True,report_dir=tmp_path/'out')
    assert out['classification']=='BROWSE_DIAGNOSTIC_MODULE_MISMATCH_REPRO_REFUSED'
    assert out['liveReproductionStarted'] is False
    assert out['browseModuleAttestation']['verified'] is False
    assert (tmp_path/'out/report.json').exists()


def test_runner_unique_default_reports_and_diagnostics_code_no_collision():
    a=diag._run_report_dir()
    b=diag._run_report_dir()
    assert a!=b
    assert a.parent==b.parent==diag.REPORT_DIR/'runs'
    assert diag.FORENSIC_RUNNER_PROTOCOL=='BROWSE_IMPORT_ATTESTATION_V7C'


def test_forensic_report_exposes_exact_browser_error_and_failed_stage(tmp_path):
    report=tmp_path/'hosted-acceptance.json'
    fixture={
        'status':'BLOCKED','apps':{'code-editor':{
            'status':'BLOCKED','liveWrites':'PASS_DISPOSABLE_ONLY',
            'disposableWrite':'PASS_DISPOSABLE_SOURCE_WRITE_ROLLBACK',
            'failures':['BROWSER_PAGEERROR','HOSTED_SCHEDULER_DIFFERENTIAL'],
            'browserPageErrorCount':{'incumbent':1,'signals':0},
            'browserPageErrors':{'incumbent':[{
                'role':'incumbent','stage':'post-disposable-live-save',
                'message':'TypeError: cannot read property',
                'errorClass':'Error','fingerprint':'abcdef1234567890',
                'frames':[{'file':'mcel-semantic-operational-runtime.js',
                           'line':42,'column':12,'function':'save'}]}]},
            'stages':[{'stage':'canonical-host-mount','status':'PASS',
                       'semanticDifferences':0,'pixelDifferences':0},
                      {'stage':'rollback-while-real-read-pending','status':'FAIL',
                       'semanticDifferences':1,'pixelDifferences':0,
                       'pixelMaxDelta':0,'differencePaths':['state.state:doc'],
                       'differenceDiagnostics':[{'path':'state.state:doc'}]}]}}}
    report.write_text(json.dumps(fixture),encoding='utf8')
    result=diag._acceptance_evidence(report)
    assert result['available'] is True
    assert result['liveWrites']=='PASS_DISPOSABLE_ONLY'
    assert result['failedStages'][0]['stage']=='rollback-while-real-read-pending'
    assert result['browserPageErrors']['incumbent'][0]['frames'][0]['line']==42
    assert 'BROWSER_PAGEERROR' in result['gateFailures']
    assert diag._classification({'hostPid':7,'sessions':[{'serverPid':7,
        'status':'settled','lastEvent':'request_complete'}],
        'acceptanceEvidence':result})=='HOSTED_BROWSER_PAGEERROR_AND_SCHEDULER_DIFFERENTIAL'


def test_forensic_report_missing_or_malformed_never_recycles_previous(tmp_path):
    fresh=tmp_path/'not-yet-written.json'
    assert diag._acceptance_evidence(fresh)['reason']=='acceptance-worker-did-not-write-report'
    fresh.write_text('not json',encoding='utf8')
    assert diag._acceptance_evidence(fresh)['reason']=='acceptance-report-unreadable'
    fresh.write_text(json.dumps({'apps':{}}),encoding='utf8')
    assert diag._acceptance_evidence(fresh)['reason']=='code-editor-acceptance-entry-missing'


def test_forensic_console_forwards_browser_exceptions_and_exact_stage_failures():
    assert diag._forward_acceptance_line('MCEL_HOSTED_PAGEERROR code-editor {...}')
    assert diag._forward_acceptance_line('MCEL_HOSTED_STAGE code-editor rollback FAIL differences=1')
    assert diag._forward_acceptance_line('MCEL_HOSTED_DIFF_FINGERPRINT code-editor rollback {...}')
    assert diag._forward_acceptance_line('MCEL_HOSTED_DISPOSABLE_PROGRESS step=save role=signals')
    assert not diag._forward_acceptance_line('user arbitrary error')
    assert not diag._forward_acceptance_line('MCEL_HOSTED_PAGEERROR_MALICIOUS path /private')


def test_classification_live_write_settled_but_browser_error_is_not_success():
    row={'hostPid':4,'sessions':[{'serverPid':4,'status':'settled','lastEvent':'request_complete'}]}
    assert diag._classification({**row,'acceptanceEvidence':{
        'available':True,'editorStatus':'BLOCKED','gateFailures':['BROWSER_PAGEERROR']}})=='HOSTED_BROWSER_PAGEERROR'
    assert diag._classification({**row,'acceptanceEvidence':{
        'available':True,'editorStatus':'BLOCKED','gateFailures':['HOSTED_SCHEDULER_DIFFERENTIAL']}})=='HOSTED_SCHEDULER_DIFFERENTIAL'
    assert diag._classification({**row,'acceptanceEvidence':{
        'available':True,'editorStatus':'BLOCKED','gateFailures':[]}})=='HOSTED_ACCEPTANCE_BLOCKED_AFTER_SETTLED_SAVE'
    assert diag._classification({**row,'acceptanceEvidence':{
        'available':True,'editorStatus':'PASS_HOSTED_READONLY_WITH_SIGNALS','gateFailures':[]}})=='SERVER_REQUEST_SETTLED_CHECK_HTTP_AND_RECEIPT'


def test_forensic_classification_never_calls_failed_worker_a_success_even_if_report_missing():
    row={'hostPid':4,'sessions':[{'serverPid':4,'status':'settled','lastEvent':'request_complete'}],
         'acceptanceExitCode':1,'acceptanceEvidence':{'available':False}}
    assert diag._classification(row)=='HOSTED_ACCEPTANCE_FAILED_AFTER_SETTLED_SAVE'
