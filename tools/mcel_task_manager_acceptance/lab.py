"""Run-owned real process effects on local hosted Task Manager; never services.

Opt in at command line and confirm the owner PID/start time and observed generation.
No production schedule, viewport or heartbeat lifecycle command is invoked.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import build_opener, HTTPCookieProcessor, Request
from urllib.error import HTTPError

from .evidence import validate_host_url


def _http(opener,url,body=None,csrf=None):
    headers={'Accept':'application/json'}
    if body is not None:headers.update({'Content-Type':'application/json','Origin':url.split('/api/')[0]})
    if csrf:headers['X-MCEL-Task-CSRF']=csrf
    req=Request(url,data=json.dumps(body).encode() if body is not None else None,headers=headers,method='POST' if body is not None else 'GET')
    try:
        with opener.open(req,timeout=12) as r:return r.status,json.loads(r.read().decode())
    except HTTPError as e:
        try:payload=json.loads(e.read().decode())
        except Exception:payload={}
        return e.code,payload


def _birth(proc):
    return datetime.fromtimestamp(proc.create_time(),timezone.utc).isoformat()


def _verified_host_generation(row: dict, *, pid: int, observed_epoch: float) -> str | None:
    """Admit only a real OS PID with a birth time matching the host's precision.

    Windows CIM emits milliseconds; psutil may report microseconds. A strict
    string comparison mistakenly rejects the same process. Unknown or raw
    PowerShell /Date(...) formats are never accepted as action evidence.
    """
    if row.get('pid') != pid or not isinstance(row.get('created_at'), str):
        return None
    raw=row['created_at']
    try:
        instant=datetime.fromisoformat(raw.replace('Z','+00:00'))
        if instant.utcoffset() is None:
            return None
        from datetime import timedelta
        import re
        precise=bool(re.search(r"\.\d{4,}(?:[+-]\d{2}:\d{2}|Z)$", raw))
        tolerance=0.000001 if precise else 0.002
        if abs(instant.timestamp() - observed_epoch)>tolerance:
            return None
        return raw
    except (ValueError, OverflowError):
        return None


def disposable_process_effects(host_url:str, *, ack:bool=False):
    if not ack: return {'status':'BLOCKED','reason':'--ack-isolated-effects required'}
    import psutil
    host_url=validate_host_url(host_url)
    opener=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    # First check the real host before even creating a disposable target.
    code,session=_http(opener,host_url+'/api/applications/task/authority/session')
    if code!=200 or not isinstance(session.get('csrf'),str):
        return {'status':'BLOCKED','reason':'live host did not issue local task session'}
    token=session['csrf']  # never log or return
    evidence={'status':'BLOCKED','effects':[], 'host':host_url,
              'transport':'real localhost HTTP task/action endpoint; not a generated UI click',
              'limitation':'Browser button execution separately required for complete operator parity'}
    for action in ('terminate_pid','kill_pid'):
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            oschild=psutil.Process(child.pid)
            if child.pid==os.getpid() or oschild.ppid()!=os.getpid() or child.poll() is not None:
                raise RuntimeError('disposable-child-ownership-refused')
            observed_epoch=oschild.create_time()
            # Observe a dedicated PID through the real server. Then *independently*
            # compare its claimed generation with psutil before using it as
            # expected_created_at for the server-enforced process action.
            code,observed=_http(opener,host_url+'/api/applications/task/overview',
                                {'query':str(child.pid),'limit':100,'include_all':True,'include_connections':False})
            rows=observed.get('processes',[]) if code==200 else []
            matched=[generation for v in rows if
                     (generation:=_verified_host_generation(v,pid=child.pid,observed_epoch=observed_epoch))]
            if not matched:raise RuntimeError('child-generation-not-confirmed-by-host-observation')
            generation=matched[0]
            identity={'action':action,'pid':child.pid,'generation':generation,'osParent':os.getpid(),
                      'observedByRealHost':True}
            # Reject omission and mismatch, and independently verify that child survives.
            request_id='p36_'+uuid.uuid4().hex
            common={'action':action,'pid':child.pid,'confirm':True,'request_id':request_id}
            missing_code,_=_http(opener,host_url+'/api/applications/task/action',common,token)
            if missing_code<400 or child.poll() is not None:raise RuntimeError('missing-generation-action-was-not-refused')
            stale={**common,'request_id':'p36_'+uuid.uuid4().hex,'expected_created_at':'2000-01-01T00:00:00+00:00'}
            stale_code,_=_http(opener,host_url+'/api/applications/task/action',stale,token)
            if stale_code<400 or child.poll() is not None:raise RuntimeError('stale-generation-action-was-not-refused')
            # The positive action is limited to the verified, explicitly run-owned child.
            command={**common,'request_id':'p36_'+uuid.uuid4().hex,'expected_created_at':generation}
            status,receipt=_http(opener,host_url+'/api/applications/task/action',command,token)
            if status!=200 or receipt.get('ok') is not True or receipt.get('pid')!=child.pid or receipt.get('verified_created_at')!=generation:
                raise RuntimeError('verified-child-action-did-not-return-valid-receipt')
            try:child.wait(timeout=8)
            except subprocess.TimeoutExpired:raise RuntimeError('child-did-not-exit-after-accepted-action')
            replay_status,replay=_http(opener,host_url+'/api/applications/task/action',command,token)
            if replay_status!=200 or replay.get('request_id')!=receipt.get('request_id') or replay.get('outcome')!=receipt.get('outcome'):
                raise RuntimeError('idempotent-replay-mismatch')
            collision={**command,'action':('kill_pid' if action=='terminate_pid' else 'terminate_pid')}
            collide_status,_=_http(opener,host_url+'/api/applications/task/action',collision,token)
            if collide_status<400:raise RuntimeError('request-id-collision-not-refused')
            evidence['effects'].append({**identity,'negative':{'missingGeneration':missing_code,'staleGeneration':stale_code,
                         'idCollision':collide_status},'receipt':{'outcome':receipt.get('outcome'),
                         'requestId':receipt.get('request_id'),'deferred':receipt.get('deferred',False)},
                         'independentObservedExit':child.poll() is not None})
        except Exception as exc:
            evidence['reason']=str(exc)[:500]
            return evidence
        finally:
            # Never terminate any process except an exact child owned by this run.
            if child.poll() is None:
                try:
                    current=psutil.Process(child.pid)
                    if current.ppid()==os.getpid() and current.create_time()==oschild.create_time():
                        child.kill();child.wait(timeout=4)
                except Exception:pass
    evidence['status']='PASS';evidence['reason']='Two OS-observed disposable child effects via real hosted HTTP'
    return evidence


def isolated_schedule_store():
    """Real schedule disk state in a temporary root; server lifecycle dispatch excluded."""
    import tempfile
    from main_computer.task_manager import TaskManagerService
    with tempfile.TemporaryDirectory(prefix='mcel-task-manager-schedules-') as d:
        root=Path(d)
        svc=TaskManagerService(root,default_port=21000,control_root=root)
        svc.data_root.mkdir(parents=True,exist_ok=True)
        created=svc.create_schedule(action='server_restart',run_at='2026-11-01T01:30:00-07:00',note='isolated P3.6 only')
        record=created['schedule']; listed=svc.list_schedules()['schedules']
        if not svc.schedule_path.exists() or len(listed)!=1 or record['run_at']!='2026-11-01T08:30:00+00:00':
            return {'status':'FAIL','reason':'isolated schedule persistence or offset mismatch'}
        stale=False
        try:svc.delete_schedule(schedule_id=record['id'],expected_revision='stale')
        except ValueError:stale=True
        if not stale:return {'status':'FAIL','reason':'stale-revision-not-rejected'}
        deleted=svc.delete_schedule(schedule_id=record['id'],expected_revision=record['revision'])
        if deleted.get('schedules')!=[]:return {'status':'FAIL','reason':'isolation schedule deletion failed'}
        # Server *status* is the only non-destructive recorded action suitable
        # for an actual manual-invocation proof in an isolated filesystem.
        status_record=svc.create_schedule(action='server_status',
            run_at='2026-11-01T02:30:00-08:00',note='isolated non-destructive invocation')['schedule']
        request_id='p36status_'+uuid.uuid4().hex
        status=svc.run_recorded_schedule(schedule_id=status_record['id'],
            expected_revision=status_record['revision'],expected_action='server_status',
            request_id=request_id,confirm=True)
        replay=svc.run_recorded_schedule(schedule_id=status_record['id'],
            expected_revision=status_record['revision'],expected_action='server_status',
            request_id=request_id,confirm=True)
        if status!=replay or status.get('action')!='server_status' or not status.get('manual_invocation'):
            return {'status':'FAIL','reason':'isolated manual status action/replay mismatch'}
        return {'status':'PASS','reason':'real isolated schedule persistence and non-destructive manual status invocation',
                'createdUtc':record['run_at'],'persisted':True,'staleRefused':True,
                'manualStatusInvoked':True,'manualLifecycleActionDispatched':False}


"""Read-only Windows process-producer provenance probe for P3.6 acceptance.

Run: python -m tools.mcel_task_manager_acceptance diagnose
No mutating calls; no external HTTP or production service changes.
"""


import json
import os
from pathlib import Path

from main_computer.task_manager import TaskManagerService

ROOT = Path(__file__).resolve().parents[2]


def diagnose() -> int:
    current = os.getpid()
    snapshot = TaskManagerService(ROOT).snapshot(
        query=str(current), limit=100, include_all=True,
        include_connections=False, process_diagnostics=True,
    )
    process = next((row for row in snapshot['processes']
                    if row.get('pid') == current and row.get('created_at')), None)
    result = {
        'osProcessIdentityConfirmed': bool(process),
        'currentPid': current,
        'observedGeneration': process.get('created_at') if process else None,
        'producer': snapshot.get('_mcel_task_process_producer'),
        'processRowsReturned': len(snapshot['processes']),
    }
    print(json.dumps(result, indent=2))
    return 0 if process else 1




# Real disposable-service lifecycle. Its executor is explicitly *not* a proof
# of control-main-computer.ps1 target-selection fidelity on Windows.
_SERVICE_CODE = r"""
import http.server,sys
port=int(sys.argv[1]); nonce=sys.argv[2]
class Service(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  data=(nonce if self.path=='/health' else 'not-found').encode()
  self.send_response(200 if self.path=='/health' else 404)
  self.send_header('Content-Length',str(len(data)))
  self.end_headers();self.wfile.write(data)
http.server.ThreadingHTTPServer(('127.0.0.1',port),Service).serve_forever()
"""


def isolated_server_lifecycle(*, ack=False):
    """Exercise real TaskManagerService idempotent lifecycle on owned HTTP child.

    Returns BLOCKED until independent production PowerShell controller fidelity
    has been verified. Never attempts lifecycle actions on production ports.
    """
    if not ack:
        return {'status':'BLOCKED','reason':'Explicit --ack-isolated-effects required',
                'controllerFidelity':'UNVERIFIED'}
    import socket
    import tempfile
    import urllib.request
    import psutil
    from main_computer.task_manager import TaskManagerService

    with tempfile.TemporaryDirectory(prefix='mcel-task-manager-lifecycle-') as folder:
        root=Path(folder).resolve()
        if root.is_symlink():
            return {'status':'FAIL','reason':'Symlinked control root refused'}
        # Reserve ephemeral port using OS allocator; release immediately before
        # the owned child binds. If anyone wins the race, refuse rather than kill.
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0))
            port=probe.getsockname()[1]
        if port==8765:
            return {'status':'FAIL','reason':'Production port refused'}
        nonce=uuid.uuid4().hex
        child=None
        original_birth=None
        transitions=[]

        def is_owned():
            if child is None or child.poll() is not None:return False
            try:
                proc=psutil.Process(child.pid)
                return (proc.ppid()==os.getpid() and
                        abs(proc.create_time()-original_birth)<0.000001 and
                        Path(proc.exe()).resolve()==Path(sys.executable).resolve() and
                        nonce in proc.cmdline())
            except (psutil.Error, OSError, ValueError):return False

        def healthy():
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=0.4) as response:
                    return response.status==200 and response.read().decode()==nonce
            except (OSError, ValueError):return False

        def wait_health(expected, seconds=7):
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                if healthy()==expected:return True
                time.sleep(.05)
            return False

        def owned_stop():
            nonlocal child
            if not is_owned():raise RuntimeError('Refused to stop non-owned or stale process')
            old=child
            old.terminate()
            try:old.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Force-kill only when the *same* child birth is still verified.
                if is_owned():old.kill();old.wait(timeout=3)
                else:raise RuntimeError('Stale PID at force-cleanup')
            if not wait_health(False):raise RuntimeError('Listener remains healthy after stop')
            transitions.append({'operation':'stop','oldPid':old.pid,'observedExit':old.poll() is not None})
            child=None

        def owned_start():
            nonlocal child,original_birth
            if child is not None and child.poll() is None:raise RuntimeError('Already running')
            if healthy():raise RuntimeError('Port occupied by unknown service')
            candidate=subprocess.Popen([sys.executable,'-u','-c',_SERVICE_CODE,str(port),nonce],
                 stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            child=candidate
            try:
                original_birth=psutil.Process(child.pid).create_time()
                if not is_owned() or not wait_health(True):
                    raise RuntimeError('Child ownership or health not verified')
                transitions.append({'operation':'start','newPid':child.pid,'observedHealth':True})
            except Exception:
                if is_owned():
                    child.kill();child.wait(timeout=4)
                child=None
                raise

        def executor(verb):
            if verb=='start':owned_start()
            elif verb=='shutdown':owned_stop()
            elif verb=='restart':
                old_pid=child.pid if is_owned() else None
                if old_pid is None:raise RuntimeError('Cannot restart unowned service')
                owned_stop();owned_start()
                if old_pid==child.pid:raise RuntimeError('Restart reused PID unexpectedly')
                transitions.append({'operation':'restart','oldPid':old_pid,'newPid':child.pid,'observedHealth':True})
            else:raise ValueError('unsupported-lifecycle-verb')
            return {'ok':True,'completed':True,'labOwned':True,'transition':verb}

        try:
            service=TaskManagerService(root,default_port=port,control_root=root/'control',
                                       isolated_server_executor=executor)
            (root/'control').mkdir()
            for verb in ('start','restart','shutdown'):
                request_id='tm_lab_'+uuid.uuid4().hex
                receipt=service.perform_action_idempotent(request_id=request_id,action='server_'+verb,confirm=True)
                if not receipt.get('ok') or not receipt.get('completed'):
                    raise RuntimeError('Unconfirmed effect '+verb)
                replay=service.perform_action_idempotent(request_id=request_id,action='server_'+verb,confirm=True)
                if replay!=receipt:raise RuntimeError('Non-idempotent replay '+verb)
                # An already-used request ID may not redirect to a different operation.
                try:
                    service.perform_action_idempotent(request_id=request_id,
                        action='server_'+('shutdown' if verb!='shutdown' else 'start'),confirm=True)
                except ValueError:pass
                else:raise RuntimeError('Idempotency collision not refused')
            if healthy():raise RuntimeError('Service remains reachable after shutdown')
            return {'status':'BLOCKED','reason':'Disposable lifecycle verified; Windows production controller fidelity not verified',
                    'isolatedLifecycle':'PASS','controllerFidelity':'UNVERIFIED','controlRootOwned':True,
                    'productionPortTouched':False,'transitions':transitions}
        except Exception as exc:
            return {'status':'FAIL','reason':str(exc)[:250],'isolatedLifecycle':'FAIL',
                    'controllerFidelity':'UNVERIFIED','productionPortTouched':False,'transitions':transitions}
        finally:
            if is_owned():
                child.kill();child.wait(timeout=4)
