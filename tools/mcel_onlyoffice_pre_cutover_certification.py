"""Fail-closed, read-only pre-cutover certification for shared MC/ONLYOFFICE.

This command NEVER performs a cutover or changes production routes. It runs
source-backed disposable tests, gathers their actual summaries, and emits an
explicit readiness report. A successful experiment is NOT automatically a
production certification: unimplemented or unavailable gates remain BLOCKED.

Commands (run from MC checkout):
    python -m tools.mcel_onlyoffice_pre_cutover_certification run --profile offline
    python -m tools.mcel_onlyoffice_pre_cutover_certification run --profile full

The full profile runs Docker experiments on the user's Windows host; no Docker
builds replace the machine-wide ONLYOFFICE service. Reports are automatically
created under runtime/reports/mcel-onlyoffice-pre-cutover/.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import signal
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
REPORT_BASE=ROOT/'runtime'/'reports'/'mcel-onlyoffice-pre-cutover'
FINGERPRINT_FILES=[
    'main_computer/auth_bootstrap.py',
    'main_computer/auth_bootstrap_service.py',
    'main_computer/viewport_routes_onlyoffice.py',
    'main_computer/viewport_server.py',
    'main_computer/service_supervisor.py',
    'docker-compose.onlyoffice.yml',
    'scripts/open-main-computer-browser.ps1',
    'scripts/main-computer-start-stop.ps1',
]
REQUIRED_PROOFS={
  'authenticated-editor-and-workbook-roundtrip': 'Real Document Server uses updated XLSX bytes from callback and reopens them',
  'existing-security-regression-suite': 'Prior authenticated gateway/correction/hostname/ONLYOFFICE regressions remain passing',
  'cross-process-durable-authority': 'Actual independent local processes share, revoke and reissue the same durable session store',
  'one-user-session-and-host-only-cookies': 'Same authorized session; no MC cookie transmitted to docs origin',
  'credentialless-bob-denials': 'Unauthorized editor, download, callback, and host routes blocked',
  'valid-but-wrong-scope-and-fuzz': 'Expired, forged, cross-document, cross-mode, duplicate and SSRF attempts denied',
  'corrected-application-ingress-candidates': 'Real selected application handlers match gateway policy in Docker',
  'one-unified-session-across-components': 'One authority across corrected candidate component handlers',
  'production-routing-rejects-bob': 'No real MC callback/download direct ingress bypass remains',
  'production-local-hostnames-and-trusted-tls': 'TLS SAN, SNI, Windows CurrentUser trust, browser-only loopback DNS verified',
  'cross-host-sso-and-revocation': 'Same server-side session across independently host-restricted cookies and replay-rejecting handoff',
  'per-account-authority-and-bob': 'Real second Windows SID cannot read the proof or use another SID session',
  'concurrent-mode-no-collision': 'Dev and Unleashed mutually exclusive; Debug/Safe may coexist without conflicts',
  'scoped-document-capabilities-in-production': 'All real config, download, callback and mode grants are enforced',
  'signed-document-server-traffic': 'Production equivalent JWT configuration and valid signed real callbacks',
  'failure-recovery-and-conflicts': 'Interrupted saves and replayed old revisions cannot corrupt newer workbooks',
  'shadow-revision-storage-candidate': 'Disposable source-backed callback uses atomic CAS, rejects stale/replayed saves and cleans failed writes',
  'shadow-revision-real-editor-candidate': 'Real Document Server saves/reopens through the shadow CAS and old editor grants are rejected',
  'continuous-editor-ordered-forcesaves': 'One uninterrupted editor saves twice using authenticated ordered command userdata without changing its document key',
  'disposable-service-outage-recovery': 'Real Document Server and MC fixture stop/restart preserves bytes, sessions and denies Bob',
  'durable-journal-process-restart': 'Independent source-backed fixture processes preserve ordered save state and reject a replay',
  'durable-real-editor-backend-restart': 'Genuine open Document Server editor survives backend restart between two ordered force-saves',
  'durable-final-close-journal-candidate': 'Source-backed status=2 admission uses one-use durable final-close grant with replay/crash protection',
  'final-close-unsaved-edits': 'Genuine signed Document Server status=2 callback must persist the final edit on last-editor closure',
  'two-live-authenticated-editor-tabs': 'After force-saves two concurrent real editor tabs keep one document key and both can persist authorized edits',
  'lifecycle-persistence': 'Browser, MC, Docker, and Windows restarts maintain valid or revoked state correctly',
  'backend-and-streaming-bypass-denials': 'Host bypass, SSE, WebSocket and cross-service credential forgery denied',
  'source-unchanged-through-experiments': 'Read-only verification did not mutate original production source files',
}


def fingerprint() -> dict[str,str]:
    result={}
    for name in FINGERPRINT_FILES:
        file=ROOT/name
        result[name]=hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else 'MISSING'
    return result


def marker_summary(stdout: str, marker: str) -> dict[str,Any] | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith(marker):
            try:
                decoded=json.loads(line[len(marker):].strip())
                return decoded if isinstance(decoded,dict) else None
            except (json.JSONDecodeError,ValueError):return None
    return None


def command_run(argv: list[str], log: Path, timeout: int) -> dict[str,Any]:
    """Stream child output without indefinite hangs or silent subprocesses."""
    result={'command':argv,'returncode':None,'timeout':False,'launch_error':None}
    start=time.monotonic()
    q: queue.Queue[str|None]=queue.Queue()
    log.parent.mkdir(parents=True,exist_ok=True)
    with log.open('w',encoding='utf8',errors='replace') as output:
        try:
            proc=subprocess.Popen(argv,cwd=ROOT,stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,text=True,bufsize=1,errors='replace',
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0),
                start_new_session=(os.name!='nt'))
        except (OSError, ValueError) as exc:
            result['launch_error']=type(exc).__name__
            output.write('START_FAILURE '+str(exc)+'\n')
            return result
        def reader():
            try:
                assert proc.stdout is not None
                for line in iter(proc.stdout.readline,''):
                    q.put(line)
            finally:q.put(None)
        thread=threading.Thread(target=reader,daemon=True)
        thread.start()
        def stop_gracefully():
            # Allow Python experiment's finally block to run Docker Compose
            # cleanup; an immediate kill may strand disposable containers.
            try:
                if os.name=='nt':proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:os.killpg(proc.pid,signal.SIGINT)
                proc.wait(timeout=35)
            except (OSError,ValueError,subprocess.TimeoutExpired):
                proc.kill()
                try:proc.wait(timeout=8)
                except subprocess.TimeoutExpired:pass
        stopped=False
        try:
            while not stopped:
                try:item=q.get(timeout=.5)
                except queue.Empty:item=''
                if item is None:
                    stopped=True
                elif item:
                    output.write(item);output.flush()
                    print(item,end='',flush=True)
                if time.monotonic()-start>timeout:
                    result['timeout']=True
                    print('MC_OO_CERT command-timeout '+str(timeout)+'s; terminating disposable child',flush=True)
                    stop_gracefully()
                    break
            proc.wait(timeout=10)
        except KeyboardInterrupt:
            stop_gracefully()
            raise
        except (OSError,subprocess.TimeoutExpired):
            stop_gracefully()
        # Drain anything written just before the process terminated.
        while True:
            try:item=q.get_nowait()
            except queue.Empty:break
            if item:
                output.write(item)
                print(item,end='',flush=True)
        result['returncode']=proc.returncode
        result['duration_seconds']=round(time.monotonic()-start,2)
    return result


def proof_result(label, status, evidence, detail=''):
    if status not in ('PASS','FAIL','UNVERIFIED'):raise ValueError(status)
    return {'name':label,'status':status,'evidence':evidence,'detail':detail}


def run(profile: str, fuzz_count: int, tag: str, run_root: Path) -> int:
    run_root.mkdir(parents=True,exist_ok=True)
    if any(run_root.iterdir()):raise RuntimeError('report directory must be empty')
    before=fingerprint()
    started=datetime.now(timezone.utc).isoformat()
    results=[];proofs={name:proof_result(name,'UNVERIFIED','not yet evidenced',message)
                       for name,message in REQUIRED_PROOFS.items()}
    def stage(label,cmd,marker,timeout=180,always=False):
        if not always and profile=='offline' and label.startswith('docker-'):
            results.append({'name':label,'status':'UNVERIFIED','reason':'offline profile; Docker was not run'})
            print('MC_OO_CERT '+label+' UNVERIFIED offline profile',flush=True)
            return None
        if label.startswith('docker-') and not shutil.which('docker'):
            results.append({'name':label,'status':'UNVERIFIED','reason':'Docker command unavailable here'})
            print('MC_OO_CERT '+label+' UNVERIFIED docker unavailable',flush=True)
            return None
        print('MC_OO_CERT '+label+' START',flush=True)
        proc=command_run(cmd,run_root/(label+'.log'),timeout)
        text=(run_root/(label+'.log')).read_text(encoding='utf8',errors='replace')
        summary=marker_summary(text,marker)
        status=('UNVERIFIED' if proc['timeout'] or proc['launch_error'] or summary is None else
                'PASS' if proc['returncode']==0 and summary.get('status')=='PASS' else 'FAIL')
        entry={'name':label,'status':status,'summary':summary,
               'log':label+'.log',**proc}
        results.append(entry)
        print('MC_OO_CERT '+label+' '+status,flush=True)
        return entry
    py=sys.executable
    interrupted=False
    runner_error=None
    try:
        contract=stage('offline-real-editor-contract',
            [py,'-m','tools.mcel_onlyoffice_real_editor_contract_smoke'],
            'MC_ONLYOFFICE_REAL_CONTRACT SUMMARY ',timeout=120,always=True)
        final_close_contract=stage('offline-final-close-contract',
            [py,'-m','tools.test_mcel_onlyoffice_final_close_contract'],
            'MC_OO_FINAL_CLOSE_CONTRACT SUMMARY ',timeout=120,always=True)
        coeditor_contract=stage('offline-two-editor-contract',
            [py,'-m','tools.test_mcel_onlyoffice_two_editor_contract'],
            'MC_OO_TWO_EDITOR_CONTRACT SUMMARY ',timeout=120,always=True)
        final_close_journal=stage('offline-final-close-journal',
            [py,'-m','tools.test_mcel_onlyoffice_final_close_journal'],
            'MC_OO_FINAL_CLOSE_JOURNAL SUMMARY ',timeout=180,always=True)
        if final_close_journal is not None:
            s=final_close_journal.get('summary') or {}
            valid=(final_close_journal['status']=='PASS' and
                   s.get('tests')==16 and s.get('errors')==0 and
                   s.get('failures')==0 and s.get('production_modified') is False)
            proofs['durable-final-close-journal-candidate']=proof_result(
                'durable-final-close-journal-candidate','PASS' if valid else 'FAIL',
                'offline-final-close-journal',
                'Source-backed synthetic signed status=2 admission and cross-process journal replay controls; not real Document Server evidence')
        existing=stage('offline-existing-security-regressions',
            [py,'-m','tools.mcel_onlyoffice_pre_cutover_existing_tests'],
            'MC_OO_EXISTING_REGRESSIONS SUMMARY ',timeout=180,always=True)
        if existing is not None:
            s=existing.get('summary') or {}
            proofs['existing-security-regression-suite']=proof_result(
                'existing-security-regression-suite',
                'PASS' if existing['status']=='PASS' and s.get('tests',0)>=13 else 'FAIL',
                'offline-existing-security-regressions','Existing source-backed regression tests')
        adversarial=stage('offline-adversarial',
            [py,'-m','tools.mcel_onlyoffice_pre_cutover_adversarial','run',
             '--fuzz-count',str(fuzz_count)],
            'MC_OO_PRECUTOVER_ATTACK SUMMARY ',timeout=300,always=True)
        if adversarial is not None:
            summary=adversarial.get('summary') or {}
            valid=(adversarial['status']=='PASS' and
                   summary.get('fuzz_iterations')==fuzz_count and summary.get('checks',0)>=32)
            proofs['valid-but-wrong-scope-and-fuzz']=proof_result(
                'valid-but-wrong-scope-and-fuzz','PASS' if valid else 'FAIL','offline-adversarial',
                'Source-backed fixture and real callback method with a test-only authorization wrapper')
        sessions=stage('offline-cross-process-sessions',
            [py,'-m','tools.mcel_onlyoffice_pre_cutover_sessions'],
            # 8 real Windows authorities perform ACL inspections and compete
            # for the session lock; the per-child 120s deadline is authoritative.
            # The earlier 180s *aggregate* timeout killed a valid stress test
            # before it reported whether those bounded children succeeded.
            'MC_OO_SESSIONS SUMMARY ',timeout=600,always=True)
        if sessions is not None:
            s=sessions.get('summary') or {}
            valid=sessions['status']=='PASS' and s.get('checks')==15 and s.get('processes_separate') is True
            proofs['cross-process-durable-authority']=proof_result(
                'cross-process-durable-authority','PASS' if valid else 'FAIL',
                'offline-cross-process-sessions','15 cross-process tests, same Windows SID')
        storage=stage('offline-storage-and-revision-faults',
            [py,'-m','tools.mcel_onlyoffice_pre_cutover_storage'],
            'MC_OO_STORAGE SUMMARY ',timeout=120,always=True)
        if storage is not None:
            summary=storage.get('summary') or {}
            failed=summary.get('failed',[])
            proofs['failure-recovery-and-conflicts']=proof_result(
                'failure-recovery-and-conflicts','FAIL' if failed else storage['status'],
                'offline-storage-and-revision-faults',
                'real source fault injection: '+','.join(failed))
        journal=stage('offline-durable-journal',
            [py,'-m','tools.test_mcel_onlyoffice_durable_save_journal'],
            'MC_OO_DURABLE_JOURNAL SUMMARY ',timeout=210,always=True)
        durable_contract=stage('offline-durable-editor-restart-contract',
            [py,'-m','tools.test_mcel_onlyoffice_durable_editor_restart_contract'],
            'MC_OO_DURABLE_EDITOR_RESTART_CONTRACT SUMMARY ',timeout=240,always=True)
        if journal is not None and durable_contract is not None:
            a=journal.get('summary') or {}
            b=durable_contract.get('summary') or {}
            valid=(journal['status']=='PASS' and a.get('tests')==17 and
                   a.get('errors')==0 and a.get('failures')==0 and
                   durable_contract['status']=='PASS' and b.get('tests')==5 and
                   b.get('errors')==0 and b.get('failures')==0)
            proofs['durable-journal-process-restart']=proof_result(
                'durable-journal-process-restart', 'PASS' if valid else 'FAIL',
                'offline-durable-journal + offline-durable-editor-restart-contract',
                'Actual independent local Python processes; not the real Document Server callback')
        shadow=stage('offline-shadow-revision-candidate',
            [py,'-m','tools.mcel_onlyoffice_shadow_revision_smoke'],
            'MC_OO_SHADOW SUMMARY ',timeout=120,always=True)
        if shadow is not None:
            s=shadow.get('summary') or {}
            valid=(shadow['status']=='PASS' and s.get('checks',0)>=19 and
                   s.get('real_source_callback_executed') is True and
                   s.get('production_modified') is False)
            proofs['shadow-revision-storage-candidate']=proof_result(
                'shadow-revision-storage-candidate','PASS' if valid else 'FAIL',
                'offline-shadow-revision-candidate',
                'Shadow only. Original production storage failure remains separately blocked')
        # Intentionally red until the actual live MC source is secured. This
        # does not downgrade a candidate-lab PASS into a fictional promotion.
        hostname=stage('source-hostname-and-auth-acceptance',
            [py,'-m','tools.mcel_security_ingress_smoke','run','--hostname-only'],
            'MC_SECURITY_INGRESS SUMMARY ',timeout=180,always=True)
        if hostname and hostname.get('summary'):
            s=hostname['summary'];failed=s.get('failed',[]);unverified=s.get('unverified',[])
            if not isinstance(failed,list) or not isinstance(unverified,list):
                failed=['invalid-hostname-summary']
            def mark(name, needles):
                detected=[n for n in failed if any(x in n for x in needles)]
                deferred=[n for n in unverified if any(x in n for x in needles)]
                return proof_result(name,'FAIL' if detected else 'UNVERIFIED' if deferred else 'PASS',
                    'source-hostname-and-auth-acceptance',
                    'fail='+','.join(detected)+'; unverified='+','.join(deferred))
            proofs['production-local-hostnames-and-trusted-tls']=mark(
                'production-local-hostnames-and-trusted-tls',
                ['hostname','mc-test','mode-aware','login-canonical','mapping','namespace','tls','cert'])
            proofs['cross-host-sso-and-revocation']=mark(
                'cross-host-sso-and-revocation',['sso','one-auth-https-owner'])
            proofs['scoped-document-capabilities-in-production']=mark(
                'scoped-document-capabilities-in-production',
                ['onlyoffice-own','onlyoffice-download','onlyoffice-callback','onlyoffice-per-mode'])
            proofs['signed-document-server-traffic']=mark(
                'signed-document-server-traffic',['onlyoffice-server-jwt','onlyoffice-live-edit'])
            proofs['concurrent-mode-no-collision']=mark(
                'concurrent-mode-no-collision',['real-dev-unleashed','real-concurrent'])
        baseline=stage('source-ingress-baseline',
            [py,'-m','tools.mcel_security_ingress_smoke','run'],
            'MC_SECURITY_INGRESS SUMMARY ',timeout=180,always=True)
        if baseline and baseline.get('summary'):
            failed=baseline['summary'].get('failed',[])
            if not isinstance(failed,list):failed=['invalid-baseline-evidence']
            unsafe=[n for n in failed if any(x in n for x in (
                'viewport-entry','onlyoffice-anonymous','onlyoffice-status-only','main-log-anonymous',
                'heartbeat-uncredentialed','browser-control-spoofed','openclaw-no-token'))]
            proofs['production-routing-rejects-bob']=proof_result(
                'production-routing-rejects-bob','FAIL' if unsafe else 'UNVERIFIED',
                'source-ingress-baseline','Unprotected live-source routes='+','.join(unsafe))
            proofs['backend-and-streaming-bypass-denials']=proof_result(
                'backend-and-streaming-bypass-denials','FAIL' if unsafe else 'UNVERIFIED',
                'source-ingress-baseline','Unprotected HTTP/route canaries; WebSocket still separately unverified')
        real=stage('docker-real-documentserver-editor',
            [py,'-m','tools.mcel_onlyoffice_real_editor_lab','run','--onlyoffice-tag',tag],
            'MC_ONLYOFFICE_REAL SUMMARY ',timeout=1500)
        if real is not None:
            s=real.get('summary') or {}
            evidence=(run_root/'docker-real-documentserver-editor.log').read_text(encoding='utf8',errors='replace')
            def verified(substring):
                return real['status']=='PASS' and substring in evidence and s.get('real_documentserver_edit_save_reopen') is True
            proofs['authenticated-editor-and-workbook-roundtrip']=proof_result(
                'authenticated-editor-and-workbook-roundtrip',
                'PASS' if verified('reopened-workbook-cell-persisted PASS') else 'FAIL',
                'docker-real-documentserver-editor','Requires genuine edited workbook bytes and reopening')
            proofs['one-user-session-and-host-only-cookies']=proof_result(
                'one-user-session-and-host-only-cookies',
                'PASS' if verified('editor-origin-never-received-mc-session-cookie PASS') else 'FAIL',
                'docker-real-documentserver-editor','Only the disposable first-party account and two browser origins')
            proofs['credentialless-bob-denials']=proof_result(
                'credentialless-bob-denials',
                'PASS' if verified('credentialless-bob-container-attacks-blocked PASS') else 'FAIL',
                'docker-real-documentserver-editor','12 Bob attacks inside Docker, not a second Windows account')
        shadow_real=stage('docker-shadow-revision-editor',
            [py,'-m','tools.mcel_onlyoffice_real_editor_lab','run',
             '--onlyoffice-tag',tag,'--shadow-revision'],
            'MC_ONLYOFFICE_REAL SUMMARY ',timeout=1500)
        if shadow_real is not None:
            s=shadow_real.get('summary') or {}
            log=(run_root/'docker-shadow-revision-editor.log').read_text(encoding='utf8',errors='replace')
            valid=(shadow_real['status']=='PASS' and s.get('shadow_revision_fence') is True and
                   s.get('real_documentserver_edit_save_reopen') is True and
                   'shadow-real-editor-callback-committed-through-revision-fence PASS' in log and
                   'shadow-replayed-old-grant-rejected-after-real-editor-save PASS' in log and
                   'credentialless-bob-container-attacks-blocked PASS' in log)
            proofs['shadow-revision-real-editor-candidate']=proof_result(
                'shadow-revision-real-editor-candidate','PASS' if valid else 'FAIL',
                'docker-shadow-revision-editor',
                'Not the production routing or a multi-save-live-editor guarantee')
        continuous=stage('docker-continuous-editor-saves',
            [py,'-m','tools.mcel_onlyoffice_real_editor_lab','run',
             '--onlyoffice-tag',tag,'--shadow-revision','--continuous-saves'],
            'MC_ONLYOFFICE_REAL SUMMARY ',timeout=1500)
        if continuous is not None:
            s=continuous.get('summary') or {}
            log=(run_root/'docker-continuous-editor-saves.log').read_text(encoding='utf8',errors='replace')
            required=('continuous-second-genuine-editor-save-persisted PASS',
                      'continuous-document-key-remains-stable-after-second-save PASS',
                      'continuous-two-ordered-server-issued-save-commands PASS',
                      'shadow-replayed-old-grant-rejected-after-real-editor-save PASS',
                      'credentialless-bob-container-attacks-blocked PASS')
            valid=(continuous['status']=='PASS' and
                   s.get('continuous_saves_in_one_editor') is True and
                   s.get('shadow_revision_fence') is True and
                   s.get('production_modified') is False and
                   all(item in log for item in required))
            proofs['continuous-editor-ordered-forcesaves']=proof_result(
                'continuous-editor-ordered-forcesaves','PASS' if valid else 'FAIL',
                'docker-continuous-editor-saves',
                'Two genuine editor saves, one stable key, signed command userdata; this fixture is not durable cross-process production state')
        durable_real=stage('docker-durable-backend-restart-editor',
            [py,'-m','tools.mcel_onlyoffice_real_editor_lab','run',
             '--onlyoffice-tag',tag,'--shadow-revision','--continuous-saves',
             '--durable-restart'], 'MC_ONLYOFFICE_REAL SUMMARY ',timeout=1500)
        if durable_real is not None:
            summary=durable_real.get('summary') or {}
            observed=(run_root/'docker-durable-backend-restart-editor.log').read_text(
                encoding='utf8',errors='replace')
            mandatory=('durable-editor-remains-open-during-backend-restart PASS',
                'durable-backend-restarted-with-different-process PASS',
                'durable-session-still-authenticated-after-restart PASS',
                'durable-first-save-state-recovered-across-restart PASS',
                'durable-real-issued-first-command-replay-denied-after-restart PASS',
                'durable-second-save-journal-persisted-after-restart PASS',
                'continuous-second-genuine-editor-save-persisted PASS',
                'continuous-document-key-remains-stable-after-second-save PASS',
                'reopened-workbook-cell-persisted PASS',
                'editor-origin-never-received-mc-session-cookie PASS',
                'credentialless-bob-container-attacks-blocked PASS')
            valid=(durable_real['status']=='PASS' and
                summary.get('durable_backend_restart_between_saves') is True and
                summary.get('continuous_saves_in_one_editor') is True and
                summary.get('real_documentserver_edit_save_reopen') is True and
                summary.get('production_modified') is False and
                all(item in observed for item in mandatory))
            proofs['durable-real-editor-backend-restart']=proof_result(
                'durable-real-editor-backend-restart','PASS' if valid else 'FAIL',
                'docker-durable-backend-restart-editor',
                'Real Document Server and browser editor remain open while only backend restarts; source-backed journal and callback')
        final_close=stage('docker-final-close-callback',
            [py,'-m','tools.mcel_onlyoffice_real_editor_lab','run',
             '--onlyoffice-tag',tag,'--shadow-revision','--continuous-saves',
             '--durable-restart','--final-close-probe'],
            'MC_ONLYOFFICE_REAL SUMMARY ',timeout=1500)
        if final_close is not None:
            data=final_close.get('summary') or {}
            observed=(run_root/'docker-final-close-callback.log').read_text(
                encoding='utf8',errors='replace')
            required=('final-close-genuine-jwt-authenticated-status2-observed PASS',
                      'final-close-last-unsaved-edit-saved-through-source-callback PASS',
                      'reopened-workbook-cell-persisted PASS',
                      'credentialless-bob-container-attacks-blocked PASS')
            valid=(final_close_contract is not None and
                   final_close_contract['status']=='PASS' and
                   final_close_journal is not None and
                   final_close_journal['status']=='PASS' and
                   final_close['status']=='PASS' and
                   data.get('final_close_status2_persisted') is True and
                   data.get('durable_backend_restart_between_saves') is True and
                   data.get('production_modified') is False and
                   all(item in observed for item in required))
            proofs['final-close-unsaved-edits']=proof_result(
                'final-close-unsaved-edits','PASS' if valid else 'FAIL',
                'docker-final-close-callback',
                'Must persist a third, unsaved edit via an authentic status=2 callback; two earlier force-saves do not count')
        coedit=stage('docker-two-live-editor-tabs',
            [py,'-m','tools.mcel_onlyoffice_real_editor_lab','run',
             '--onlyoffice-tag',tag,'--shadow-revision','--continuous-saves',
             '--durable-restart','--coeditor-probe'],
            'MC_ONLYOFFICE_REAL SUMMARY ',timeout=1500)
        if coedit is not None:
            data=coedit.get('summary') or {}
            observed=(run_root/'docker-two-live-editor-tabs.log').read_text(
                encoding='utf8',errors='replace')
            mandatory=('coeditor-first-editor-remains-open-after-two-saves PASS',
                'coeditor-second-editor-joins-original-live-document-key PASS',
                'coeditor-two-live-editors-ready-simultaneously PASS',
                'coeditor-second-real-spreadsheet-iframe PASS',
                'coeditor-second-tab-genuine-edit-persisted PASS',
                'coeditor-first-editor-survives-second-tab-closure PASS',
                'coeditor-original-tab-next-edit-persisted PASS',
                'reopened-workbook-cell-persisted PASS',
                'credentialless-bob-container-attacks-blocked PASS')
            valid=(coeditor_contract is not None and
                   coeditor_contract['status']=='PASS' and
                   coedit['status']=='PASS' and
                   data.get('two_authenticated_live_editor_tabs') is True and
                   data.get('production_modified') is False and
                   all(item in observed for item in mandatory))
            proofs['two-live-authenticated-editor-tabs']=proof_result(
                'two-live-authenticated-editor-tabs','PASS' if valid else 'FAIL',
                'docker-two-live-editor-tabs',
                'Two simultaneous editors share a stable key and persist independently. '
                'Uses one Windows SID; does not prove cross-account editing.')
        faults=stage('docker-real-service-faults',
            [py,'-m','tools.mcel_onlyoffice_pre_cutover_docker_faults','run','--onlyoffice-tag',tag],
            'MC_OO_DOCKER_FAULTS SUMMARY ',timeout=1200)
        if faults is not None:
            s=faults.get('summary') or {}
            valid=(faults['status']=='PASS' and s.get('checks',0)>=10 and
                   s.get('production_modified') is False)
            proofs['disposable-service-outage-recovery']=proof_result(
                'disposable-service-outage-recovery','PASS' if valid else 'FAIL',
                'docker-real-service-faults',
                'Stop/start of real Document Server and restart of fixture; not a mid-save fault')
        corrections=stage('docker-correction-and-baseline',
            [py,'-m','tools.mcel_security_ingress_smoke','run','--docker-correct-all'],
            'MC_SECURITY_INGRESS SUMMARY ',timeout=1800)
        if corrections and corrections.get('summary'):
            s=corrections['summary']
            # The overall smoke must stay red on the intentionally unpatched
            # production routes, but its isolated corrected-lab checks must
            # explicitly pass. Inspect the log, not just the return code.
            log=(run_root/'docker-correction-and-baseline.log').read_text(encoding='utf8',errors='replace')
            required={
                'corrected-application-ingress-candidates':'five-component-disposable-docker-corrections PASS',
                'one-unified-session-across-components':'one-mc-session-all-ingress-candidates PASS',
            }
            for name,text in required.items():
                proofs[name]=proof_result(name,'PASS' if text in log else 'FAIL',
                    'docker-correction-and-baseline',
                    'Verified isolated candidate, not live production')
            failed=s.get('failed',[])
            proof_names=['viewport-http-deny','onlyoffice-anonymous-callback',
                         'onlyoffice-status-only-callback','viewport-entry']
            detected=[n for n in failed if any(n.startswith(x) for x in proof_names)]
            proofs['production-routing-rejects-bob']=proof_result(
                'production-routing-rejects-bob','FAIL' if detected else 'UNVERIFIED',
                'docker-correction-and-baseline','Unprotected original routes='+','.join(detected))
            # Other application ingress and streaming remain unverified until
            # the actual candidate has been integrated and rerun.
        # Real account, reboot and fault-injection checks cannot legitimately
        # be replaced with mock requests or assertions about source strings.
    except KeyboardInterrupt:
        interrupted=True
        print('MC_OO_CERT INTERRUPTED -- no promotion permitted',flush=True)
    except Exception as exc:
        runner_error=type(exc).__name__+': '+str(exc)[:250]
        print('MC_OO_CERT ERROR '+runner_error,flush=True)
    finally:
        after=fingerprint()
        safe=(before==after)
        proofs['source-unchanged-through-experiments']=proof_result(
            'source-unchanged-through-experiments','PASS' if safe else 'FAIL',
            'sha256-before-after',
            'Only tracked production source and Compose configuration files; runtime report files are written')
        for n in proofs:
            if n in ('per-account-authority-and-bob','failure-recovery-and-conflicts',
                     'lifecycle-persistence','backend-and-streaming-bypass-denials'):
                proofs[n]['detail']+='; requires separate real-world experiment, not certified by the isolated editor'
        values=list(proofs.values())
        counts={state:sum(v['status']==state for v in values)
                for state in ('PASS','FAIL','UNVERIFIED')}
        decision='READY' if not interrupted and not runner_error and counts['FAIL']==0 and counts['UNVERIFIED']==0 else 'BLOCKED'
        summary={'decision':decision,'profile':profile,'started_utc':started,
                 'finished_utc':datetime.now(timezone.utc).isoformat(),
                 'proofs':values,'counts':counts,'stages':results,
                 'production_source_changed':not safe,
                 'production_cutover_performed':False,
                 'interrupted':interrupted,'runner_error':runner_error,
                 'original_source_sha256':before,
                 'result_source_sha256':after,
                 'limitations':[
                     'An isolated source-backed callback is not the full real production dispatcher',
                     'A Docker Bob container is not a different Windows SID',
                     'Disposable Chromium SPKI pinning is not Windows CurrentUser TLS trust',
                     'No power-cycle or Windows reboot occurred in this test',
                     'The original production callback remains vulnerable; a passing shadow revision test is not a production cutover',
                     'The legacy single-save shadow CAS remains one-shot; the separate ordered-commands fixture proves repeated saves only in an isolated process',
                     'Only the actual deployed handler cutover can finally verify live ingress; never infer it from a fixture',
                 ]}
        (run_root/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True),encoding='utf8')
        with (run_root/'summary.txt').open('w',encoding='utf8') as f:
            f.write('MAIN COMPUTER ONLYOFFICE PRE-CUTOVER: '+decision+'\n')
            f.write('PASS='+str(counts['PASS'])+' FAIL='+str(counts['FAIL'])+
                    ' UNVERIFIED='+str(counts['UNVERIFIED'])+'\n')
            for p in values:f.write(p['status']+' '+p['name']+' '+p['evidence']+'\n')
        print('MC_OO_CERT SUMMARY '+json.dumps({
            'decision':decision,'counts':counts,'production_cutover_performed':False,
            'production_source_changed':not safe,'report_dir':str(run_root)},sort_keys=True),flush=True)
    return 130 if interrupted else 2 if runner_error else 0 if decision=='READY' else 1


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',nargs='?',default='run',choices=['run'])
    p.add_argument('--profile',choices=['offline','full'],default='full')
    p.add_argument('--fuzz-count',type=int,default=300)
    p.add_argument('--onlyoffice-tag',default=os.environ.get('MAIN_COMPUTER_ONLYOFFICE_IMAGE_TAG','latest'))
    p.add_argument('--report-dir',type=Path)
    args=p.parse_args(argv)
    if not 1<=args.fuzz_count<=10000:p.error('fuzz count must be 1 through 10000')
    from secrets import token_hex
    run_root=args.report_dir or (REPORT_BASE/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+token_hex(3)))
    return run(args.profile,args.fuzz_count,args.onlyoffice_tag,run_root)

if __name__=='__main__':raise SystemExit(main())
