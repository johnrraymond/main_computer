"""One-command forensic test for a hanging real hosted MCEL Code Editor Save.

Requires explicit authorization for the *same* disposable-only live-write
harness. The backend observer logs stage timings and request-thread stacks
without source content, credentials, or arbitrary host mutation. The runner
collects browser/HTTP, server-process, staged-directory and telemetry evidence.

Usage:
 python -m tools.mcel_hosted_save_stall_diagnostic \\
   --host-url http://127.0.0.1:8765 --allow-editor-read-history \\
   --authorize-disposable-writes
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import uuid
from pathlib import Path
import queue
import hashlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
from urllib.request import urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
def _report_base(repo: Path) -> Path:
    key=hashlib.sha256(str(repo.resolve()).encode('utf8')).hexdigest()[:20]
    # Keep instrumentation outside the workspace. The engine hashes and stages
    # the entire workspace and must not race with its own diagnostic logs.
    return Path(tempfile.gettempdir())/'mcel-hosted-save-stall-diagnostics'/key

REPORT_DIR = _report_base(ROOT)/'client'
BACKEND_TRACES = 'server' 
STAGE_PREFIXES = ('mcel-project-edit-stage-', 'mcel-project-edit-dryrun-')
FORENSIC_RUNNER_PROTOCOL='BROWSE_IMPORT_ATTESTATION_V7C'
EXPECTED_BROWSE_PROTOCOL='ONE_SHOT_BOUNDED_DIRECTORY_V7C'
EXPECTED_BROWSE_SHA256='1fae0acac07fa134394cc392ab9eab8a7ef87b6b08faf04689bcedc630c47606'


def _attest_browse_module(repo: Path):
    """Identify the *actual* module imported by the acceptance child.

    The command uses the same interpreter and working directory as the later
    acceptance process. Source fingerprinting prevents a legacy test from
    silently consuming the entire diagnostic window. Never dispatch writes
    when this attestation is incomplete.
    """
    expected_path=(repo/'tools/mcel_hosted_cutover_disposable.py').resolve()
    result={'verified':False,'expectedProtocol':EXPECTED_BROWSE_PROTOCOL,
            'expectedSha256':EXPECTED_BROWSE_SHA256}
    if not expected_path.is_file():
        result['reason']='browse-module-missing'
        return result
    observed=hashlib.sha256(expected_path.read_bytes()).hexdigest()
    result['diskSha256']=observed
    if observed!=EXPECTED_BROWSE_SHA256:
        result['reason']='browse-source-hash-does-not-match-patch-7c'
        return result
    child_code=(
        'import importlib,json,hashlib,pathlib;'
        'm=importlib.import_module("tools.mcel_hosted_cutover_disposable");'
        'p=pathlib.Path(m.__file__).resolve();'
        'print(json.dumps({"file":str(p),'
        '"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),'
        '"protocol":getattr(m,"FORENSIC_BROWSE_PROTOCOL",None)}))'
    )
    try:
        child=subprocess.run([sys.executable,'-c',child_code],cwd=repo,
                             text=True,capture_output=True,timeout=25)
    except (OSError,subprocess.TimeoutExpired):
        result['reason']='browse-child-import-failed-or-timed-out'
        return result
    if child.returncode!=0:
        result['reason']='browse-child-import-failed'
        result['childExitCode']=child.returncode
        return result
    try:
        detail=json.loads(child.stdout.strip())
    except (ValueError,TypeError):
        result['reason']='browse-child-import-json-unavailable'
        return result
    result['importedSha256']=detail.get('sha256')
    result['importedProtocol']=detail.get('protocol')
    try:correct_path=Path(detail.get('file','')).resolve()==expected_path
    except (TypeError,ValueError,OSError):correct_path=False
    result['importedFromExpectedPath']=correct_path
    if not correct_path or result['importedSha256']!=EXPECTED_BROWSE_SHA256 or result['importedProtocol']!=EXPECTED_BROWSE_PROTOCOL:
        result['reason']='browse-imported-module-mismatch'
        return result
    result['verified']=True
    result['reason']='imported-protocol-and-bytes-match'
    return result


def _run_report_dir():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return REPORT_DIR/'runs'/(stamp+'-'+str(os.getpid())+'-'+uuid.uuid4().hex[:10])



def _now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def _json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload,indent=2,sort_keys=True,default=str)+'\n',encoding='utf8')
    temporary.replace(path)


def _port_owner(host: str, port: int):
    try:
        import psutil
        for item in psutil.net_connections(kind='tcp'):
            local=item.laddr
            if item.status!='LISTEN' or not local or local.port!=port:continue
            ip=local.ip
            if ip in (host,'0.0.0.0','::','::1') and item.pid:
                return int(item.pid), 'psutil'
    except (ImportError, PermissionError, OSError):
        pass
    if os.name=='nt':
        try:
            result=subprocess.run(['netstat','-ano'],capture_output=True,text=True,timeout=6)
            for line in result.stdout.splitlines():
                parts=line.split()
                if len(parts)<5 or parts[0].upper()!='TCP' or parts[3].upper()!='LISTENING':continue
                if parts[1].rsplit(':',1)[-1]==str(port):
                    return int(parts[-1]),'netstat'
        except (OSError,ValueError,subprocess.TimeoutExpired):pass
    return None,'not_observed'


def _process_state(pid: int | None):
    if not pid:return {'available':False,'reason':'host-pid-unavailable'}
    try:
        import psutil
        p=psutil.Process(pid)
        with p.oneshot():
            mem=p.memory_info()
            io=p.io_counters() if hasattr(p,'io_counters') else None
            cpu=p.cpu_times()
            children=p.children(recursive=True)
            state={'available':True,'pid':pid,'name':p.name(),'status':p.status(),
                   'startEpoch':round(p.create_time(),3),
                   'threadCount':p.num_threads(),'rssBytes':mem.rss,
                   'cpuUserSeconds':round(cpu.user,3),'cpuSystemSeconds':round(cpu.system,3),
                   'ioReadBytes':getattr(io,'read_bytes',None),'ioWriteBytes':getattr(io,'write_bytes',None),
                   'ioReadCount':getattr(io,'read_count',None),'ioWriteCount':getattr(io,'write_count',None)}
        state['children']=[{'pid':c.pid,'name':c.name(),'ageSeconds':round(time.time()-c.create_time(),1)}
                           for c in children[:24] if c.is_running()]
        return state
    except (ImportError, OSError, Exception) as exc:
        # No error strings or process arguments are included in the report.
        return {'available':False,'pid':pid,'errorClass':type(exc).__name__}


def _stage_directories():
    try:
        temp=Path(tempfile.gettempdir());results=[]
        for child in temp.iterdir():
            if not child.is_dir() or not child.name.startswith(STAGE_PREFIXES):continue
            stat=child.stat()
            # Never recurse into large in-progress trees; that would compete
            # with the operation under investigation for disk resources.
            results.append({'kind':next(x for x in STAGE_PREFIXES if child.name.startswith(x)),
                            'mtimeAgeSeconds':round(max(0,time.time()-stat.st_mtime),1)})
            if len(results)>=48:break
        return results
    except OSError:return []


def _traces(root: Path):
    folder=_report_base(root)/BACKEND_TRACES
    if not folder.is_dir():return []
    result=[]
    for p in sorted(folder.glob('*.jsonl'),key=lambda x:x.stat().st_mtime,reverse=True)[:30]:
        try:
            with p.open(encoding='utf8') as handle:
                lines=[json.loads(line) for line in handle if line.strip()]
        except (OSError,ValueError):continue
        if not lines:continue
        last=lines[-1]
        completed=any(x['event'] in ('request_complete','request_error') for x in lines)
        result.append({'id':p.stem,'path':str(p), 'status':'settled' if completed else 'active',
                       'serverPid':next((x.get('serverPid') for x in lines if x.get('event')=='request_enter'),None),
                       'lastEvent':last.get('event'),'lastStage':last.get('stage'),
                       'elapsedSeconds':last.get('elapsedSeconds'),
                       'sample':next((x for x in reversed(lines) if x.get('event')=='still_active'),None),
                       'stageTimings':[{'stage':x['stage'],'durationSeconds':x.get('stageElapsedSeconds'),
                                        'status':x.get('status')}
                                       for x in lines if x.get('event')=='stage_exit'][-22:]})
    return result


def _host_health(url: str):
    started=time.monotonic()
    try:
        with urlopen(url+'/applications/code-editor',timeout=4) as response:
            status=response.status
            response.read(16)
        return {'connected':True,'httpStatus':status,'elapsedSeconds':round(time.monotonic()-started,3)}
    except HTTPError as exc:
        return {'connected':True,'httpStatus':exc.code,'elapsedSeconds':round(time.monotonic()-started,3)}
    except Exception as exc:
        return {'connected':False,'errorClass':type(exc).__name__,
                'elapsedSeconds':round(time.monotonic()-started,3)}


FORWARDED_ACCEPTANCE_PREFIXES=(
    'MCEL_HOSTED_DISPOSABLE_PROGRESS',
    'MCEL_HOSTED_DISPOSABLE_BROWSE_DIAGNOSTIC',
    'MCEL_HOSTED_RESULT',
    'MCEL_HOSTED_ISOLATED_CERT',
    'MCEL_HOSTED_PAGEERROR ',
    'MCEL_HOSTED_STAGE ',
    'MCEL_HOSTED_DIFF_FINGERPRINT ',
)


def _forward_acceptance_line(line: str) -> bool:
    """Forward only structured acceptance diagnostics, never raw server logs."""
    return line.startswith(FORWARDED_ACCEPTANCE_PREFIXES)


def _acceptance_evidence(report_path: Path) -> dict:
    """Extract only bounded, action-oriented evidence from a single-run report.

    The acceptance worker writes this file outside the repository. It may
    contain snapshots and user data; only safe reported JS errors, stage
    fingerprints, and symbolic gate outcomes are included in the forensic
    summary. Never copy the entire acceptance report into forensic telemetry.
    """
    if not report_path.is_file():
        return {'available':False,'reason':'acceptance-worker-did-not-write-report'}
    try:
        with report_path.open('r',encoding='utf8') as stream:
            data=json.load(stream)
    except (OSError,ValueError,TypeError):
        return {'available':False,'reason':'acceptance-report-unreadable'}
    if not isinstance(data,dict) or not isinstance(data.get('apps'),dict):
        return {'available':False,'reason':'acceptance-report-schema-invalid'}
    editor=data['apps'].get('code-editor')
    if not isinstance(editor,dict):
        return {'available':False,'reason':'code-editor-acceptance-entry-missing'}

    # Browser errors already pass through the Code Editor acceptance worker's
    # safe-text + basename/line/column scrub. Accept only known fields.
    events={}
    for role in ('incumbent','signals'):
        found=editor.get('browserPageErrors',{}).get(role,[]) if isinstance(editor.get('browserPageErrors'),dict) else []
        clean=[]
        for event in found[:8] if isinstance(found,list) else []:
            if not isinstance(event,dict):continue
            clean.append({'role':role,
                'stage':str(event.get('stage',''))[:96],
                'errorClass':str(event.get('errorClass',''))[:64],
                'message':str(event.get('message',''))[:360],
                'frames':[{'file':str(f.get('file',''))[:80],
                           'line':f.get('line'),'column':f.get('column'),
                           'function':str(f.get('function') or '')[:80]}
                          for f in event.get('frames',[])[:6] if isinstance(f,dict)],
                'fingerprint':str(event.get('fingerprint',''))[:24]})
        if clean:events[role]=clean

    failed=[]
    stages=editor.get('stages',[])
    for stage in stages if isinstance(stages,list) else []:
        if not isinstance(stage,dict) or stage.get('status')=='PASS':continue
        failed.append({'stage':str(stage.get('stage',''))[:96],
                       'status':str(stage.get('status',''))[:32],
                       'semanticDifferences':stage.get('semanticDifferences'),
                       'pixelDifferences':stage.get('pixelDifferences'),
                       'pixelMaxDelta':stage.get('pixelMaxDelta'),
                       'differencePaths':[str(v)[:160] for v in stage.get('differencePaths',[])[:8]],
                       'differenceDiagnostics':stage.get('differenceDiagnostics',[])[:8]})
        if len(failed)>=10:break
    # Symbolic acceptance gate names only. Never carry exceptions that could
    # contain arbitrary source, absolute paths, or secrets into this summary.
    failures=[v for v in editor.get('failures',[])
              if isinstance(v,str) and len(v)<=96 and v.replace('_','').isupper()
              and v.replace('_','').isalpha()]
    return {'available':True,'overallStatus':str(data.get('status',''))[:72],
            'editorStatus':str(editor.get('status',''))[:72],
            'liveWrites':str(editor.get('liveWrites',''))[:72],
            'disposableWrite':str(editor.get('disposableWrite',''))[:96],
            'actualSkips':editor.get('actualSkips',0),
            'stageCount':len(stages) if isinstance(stages,list) else None,
            'failedStages':failed,'browserPageErrors':events,
            'browserPageErrorCount':editor.get('browserPageErrorCount',{}),
            'gateFailures':failures}


def _classification(report):
    traces=report.get('sessions',[])
    relevant=[t for t in traces if t.get('serverPid')==report.get('hostPid')]
    if relevant:
        t=relevant[0]
        stage=t.get('lastStage') or 'unknown'
        if t['status']=='active':return 'SERVER_REQUEST_ACTIVE_AT_'+stage
        if t['lastEvent']=='request_error':return 'SERVER_REQUEST_ENDED_WITH_ERROR'
        acceptance=report.get('acceptanceEvidence',{})
        if acceptance.get('available') and acceptance.get('editorStatus')=='BLOCKED':
            gate=set(acceptance.get('gateFailures',[]))
            if 'BROWSER_PAGEERROR' in gate and 'HOSTED_SCHEDULER_DIFFERENTIAL' in gate:
                return 'HOSTED_BROWSER_PAGEERROR_AND_SCHEDULER_DIFFERENTIAL'
            if 'BROWSER_PAGEERROR' in gate:return 'HOSTED_BROWSER_PAGEERROR'
            if 'HOSTED_SCHEDULER_DIFFERENTIAL' in gate:return 'HOSTED_SCHEDULER_DIFFERENTIAL'
            return 'HOSTED_ACCEPTANCE_BLOCKED_AFTER_SETTLED_SAVE'
        if report.get('acceptanceExitCode') not in (None,0):
            return 'HOSTED_ACCEPTANCE_FAILED_AFTER_SETTLED_SAVE'
        return 'SERVER_REQUEST_SETTLED_CHECK_HTTP_AND_RECEIPT'
    if not report.get('saveAttempted',False):
        marker=report.get('lastAcceptanceProgress')
        if marker:
            # This indicates a *pre-Save* harness blockage, not a Save route
            # or a stale server-side telemetry module. Keep the exact step.
            return 'PRE_SAVE_STALL_AT_'+marker.replace(' ','_').replace('=','-')
        return 'PRE_SAVE_NOT_REACHED'
    if report.get('hostPid') is None:return 'HOST_PROCESS_UNIDENTIFIED'
    if not report.get('latestHealth',{}).get('connected'):
        return 'HOST_UNRESPONSIVE_AFTER_SAVE_ATTEMPT'
    return 'SAVE_ATTEMPT_NO_INSTRUMENTED_SERVER_ENTRY'


def _stream(pipe, output):
    try:
        for line in iter(pipe.readline,''):
            output.put(line)
    finally:output.put(None)


def run(host_url: str, *, read_history: bool, write_authorized: bool,
        inspect_only: bool=False, max_seconds: int=340, report_dir: Path | None=None,
        storage_state: Path | None=None):
    parsed=urlsplit(host_url)
    if (parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or
        parsed.path not in ('','/') or parsed.query or parsed.fragment):
        raise ValueError('HOST_URL_MUST_BE_EXACT_LOCAL_LOOPBACK')
    if not inspect_only and not (read_history and write_authorized):
        raise ValueError('DISPOSABLE_SAVE_REPRO_REQUIRES_BOTH_EXPLICIT_AUTHORIZATIONS')
    cwd=Path.cwd().resolve()
    if not (cwd/'tools/mcel_hosted_signal_cutover_acceptance.py').is_file():
        raise ValueError('RUN_FROM_MAIN_COMPUTER_REPOSITORY_ROOT')
    report_dir=report_dir if report_dir is not None else _run_report_dir()
    path=report_dir if report_dir.is_absolute() else cwd/report_dir
    path.mkdir(parents=True,exist_ok=True)
    pid,method=_port_owner(parsed.hostname, parsed.port or 80)
    module_file=cwd/'main_computer/mcel_editor_save_stall_probe.py'
    module_mtime=module_file.stat().st_mtime if module_file.exists() else None
    before=_traces(cwd)
    state=_process_state(pid)
    summary={'schema':'mcel.hosted-save-stall-diagnostic.v1', 'generatedAt':_now(),
             'forensicRunnerProtocol':FORENSIC_RUNNER_PROTOCOL,
             'hostUrl':host_url,'hostPid':pid,'pidDiscovery':method,'initialHostState':state,
             'hostProbeModuleMtime':module_mtime,
             'existingSessions':before, 'maxSeconds':max_seconds, 'samples':[],
             'acceptanceLines':0,'lastAcceptanceProgress':None,'saveAttempted':False,
             'acceptanceExitCode':None,'liveReproductionStarted':False,
             'productionSchedulerChanged':False, 'cutoverAuthorized':False}
    active=[x for x in before if x['status']=='active' and x['serverPid']==pid]
    if active and not inspect_only:
        summary['classification']='EXISTING_LIVE_SAVE_UNSETTLED_REPRO_REFUSED'
        _json(path/'report.json',summary)
        print('MCEL_SAVE_STALL_DIAGNOSTIC REFUSED existing traced Save still active; inspect its session',flush=True)
        return summary
    if not inspect_only:
        attestation=_attest_browse_module(cwd)
        summary['browseModuleAttestation']=attestation
        if not attestation['verified']:
            summary['classification']='BROWSE_DIAGNOSTIC_MODULE_MISMATCH_REPRO_REFUSED'
            summary['finishedAt']=_now()
            _json(path/'report.json',summary)
            print('MCEL_SAVE_STALL_PREFLIGHT_REFUSED',
                  json.dumps(attestation,sort_keys=True),flush=True)
            print('MCEL_SAVE_STALL_REPORT',path/'report.json',flush=True)
            return summary
        print('MCEL_SAVE_STALL_PREFLIGHT VERIFIED protocol='+FORENSIC_RUNNER_PROTOCOL+
              ' importedBrowse='+EXPECTED_BROWSE_PROTOCOL,flush=True)
    if not inspect_only and state.get('startEpoch') and module_mtime and module_mtime>state['startEpoch']+2:
        summary['classification']='HOST_RESTART_REQUIRED_FOR_STAGE_INSTRUMENTATION'
        _json(path/'report.json',summary)
        print('MCEL_SAVE_STALL_DIAGNOSTIC REFUSED backend process predates diagnostic patch; restart MCEL host first',flush=True)
        return summary
    if not inspect_only and (not pid or not module_mtime):
        summary['classification']='HOST_NOT_INSTRUMENTATION_VERIFIED'
        _json(path/'report.json',summary)
        print('MCEL_SAVE_STALL_DIAGNOSTIC REFUSED unable to verify instrumented host PID',flush=True)
        return summary
    print('MCEL_SAVE_STALL_PROBE hostPid='+str(pid)+' mode='+('inspect' if inspect_only else 'reproduce'),flush=True)
    base_health=_host_health(host_url)
    summary['initialHealth']=base_health
    try:
        workspace_disk=shutil.disk_usage(cwd)
        temp_disk=shutil.disk_usage(tempfile.gettempdir())
        summary['storage']={'workspaceFreeBytes':workspace_disk.free,
                            'tempFreeBytes':temp_disk.free}
    except OSError:
        summary['storage']={'available':False}
    # Allocate the acceptance report in the same unique out-of-workspace
    # forensic directory. This prevents a prior report from being mistaken for
    # the current one and avoids writing diagnostic output into the project.
    acceptance_report=path/'hosted-acceptance.json'
    summary['acceptanceReportPath']=str(acceptance_report)
    command=[sys.executable,'-m','tools.mcel_hosted_signal_cutover_acceptance',
             '--host-url',host_url,'--app','code-editor','--allow-editor-read-history',
             '--authorize-disposable-writes','--report',str(acceptance_report)]
    if storage_state:command.extend(['--storage-state',str(storage_state)])
    worker=None;log=None;pipe_queue=queue.Queue();pump=None;ended=False;last_health=0;start=time.monotonic()
    if not inspect_only:
        log=(path/'hosted-acceptance.log').open('w',encoding='utf8')
        worker=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                cwd=cwd,encoding='utf8',errors='replace',bufsize=1)
        summary['liveReproductionStarted']=True
        pump=threading.Thread(target=_stream,args=(worker.stdout,pipe_queue),daemon=True);pump.start()
    last_markers=set();observed_session_ids={t['id'] for t in before}
    try:
        while True:
            while True:
                try:line=pipe_queue.get_nowait()
                except queue.Empty:break
                if line is None:ended=True;continue
                summary['acceptanceLines']+=1
                log.write(line);log.flush()
                if line.startswith('MCEL_HOSTED_DISPOSABLE_PROGRESS'):
                    # Only explicit safe progress markers; no user file paths
                    # or arbitrary error text in this field.
                    parts=line.split()
                    step=next((x.split('=',1)[1] for x in parts if x.startswith('step=')),None)
                    role=next((x.split('=',1)[1] for x in parts if x.startswith('role=')),None)
                    if step and role and len(step)<=100 and len(role)<=20:
                        summary['lastAcceptanceProgress']=step+'_'+role
                        if step.startswith('save-incumbent-disposable-source') or step.startswith('save-signal-disposable-source'):
                            summary['saveAttempted']=True
                if _forward_acceptance_line(line):
                    # These are bounded, scrubbed diagnostics emitted by the
                    # acceptance harness. Do not forward arbitrary child lines.
                    print(line.rstrip(),flush=True)
            elapsed=time.monotonic()-start
            sessions=_traces(cwd)
            fresh=[t for t in sessions if t['id'] not in observed_session_ids]
            for t in fresh:
                key=(t['id'],t['lastStage'],t['lastEvent'])
                if key not in last_markers:
                    print('MCEL_SAVE_STALL_SERVER session='+t['id']+
                          ' stage='+str(t['lastStage'])+' state='+t['status'],flush=True)
                    last_markers.add(key)
            if time.monotonic()-last_health>=14 or last_health==0:
                health=_host_health(host_url)
                last_health=time.monotonic()
            else:health=summary.get('latestHealth',base_health)
            summary['latestHealth']=health
            snap={'utc':_now(),'elapsedSeconds':round(elapsed,2),'hostProcess':_process_state(pid),
                  'hostHealth':health,'stageDirectories':_stage_directories(),
                  'activeSessions':[{'id':t['id'],'stage':t['lastStage'],'status':t['status'],
                                     'stageStack':(t.get('sample') or {}).get('requestThreadStack',[])}
                                    for t in fresh]}
            summary['samples'].append(snap)
            if len(summary['samples'])>250:summary['samples']=summary['samples'][-250:]
            _json(path/'report.json',summary)
            if inspect_only or (worker.poll() is not None and ended):break
            if elapsed>=max_seconds:
                summary['harnessDeadlineExceeded']=True
                print('MCEL_SAVE_STALL_DIAGNOSTIC harness deadline; terminating only diagnostic child, not backend',flush=True)
                if worker.poll() is None:
                    worker.terminate()
                    try:worker.wait(timeout=5)
                    except subprocess.TimeoutExpired:worker.kill();worker.wait(timeout=5)
                break
            time.sleep(2.0)
    finally:
        if worker is not None:
            if worker.poll() is None:worker.terminate()
            summary['acceptanceExitCode']=worker.wait(timeout=10)
            if log:log.close()
    summary['sessions']=[t for t in _traces(cwd) if inspect_only or t['id'] not in observed_session_ids]
    if not inspect_only:
        summary['acceptanceEvidence']=_acceptance_evidence(acceptance_report)
        evidence=summary['acceptanceEvidence']
        if evidence.get('available'):
            print('MCEL_SAVE_STALL_ACCEPTANCE_STATUS',
                  'editor='+evidence['editorStatus'],
                  'writes='+evidence['liveWrites'],
                  'failedStages='+str(len(evidence['failedStages'])),
                  'pageErrors='+str(sum(evidence.get('browserPageErrorCount',{}).values())),
                  flush=True)
            for stage in evidence['failedStages']:
                print('MCEL_SAVE_STALL_FAILED_STAGE',json.dumps(stage,sort_keys=True),flush=True)
            for role,events in evidence['browserPageErrors'].items():
                for event in events:
                    print('MCEL_SAVE_STALL_PAGEERROR',json.dumps(event,sort_keys=True),flush=True)
        else:
            print('MCEL_SAVE_STALL_ACCEPTANCE_EVIDENCE_UNAVAILABLE',evidence.get('reason'),flush=True)
    summary['classification']=_classification(summary)
    summary['finishedAt']=_now()
    _json(path/'report.json',summary)
    print('MCEL_SAVE_STALL_CLASSIFICATION',summary['classification'],flush=True)
    print('MCEL_SAVE_STALL_REPORT',path/'report.json',flush=True)
    if any(t['status']=='active' for t in summary['sessions']):
        print('MCEL_SAVE_STALL_WARNING backend Save still active: do not repeat live writes until settled',flush=True)
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host-url',required=True)
    parser.add_argument('--allow-editor-read-history',action='store_true')
    parser.add_argument('--authorize-disposable-writes',action='store_true')
    parser.add_argument('--inspect-only',action='store_true',help='Read-only inspection of prior trace and current process; no save dispatched')
    parser.add_argument('--storage-state',type=Path,help='Optional existing browser auth state; never copied into report')
    parser.add_argument('--max-seconds',type=int,default=340)
    parser.add_argument('--report-dir',type=Path,default=None)
    args=parser.parse_args(argv)
    result=run(args.host_url,read_history=args.allow_editor_read_history,
               write_authorized=args.authorize_disposable_writes,
               inspect_only=args.inspect_only,max_seconds=args.max_seconds,
               report_dir=args.report_dir,storage_state=args.storage_state)
    return 0 if args.inspect_only or (result['classification']=='SERVER_REQUEST_SETTLED_CHECK_HTTP_AND_RECEIPT' and
        result['acceptanceExitCode']==0) else 1

if __name__=='__main__':raise SystemExit(main())
