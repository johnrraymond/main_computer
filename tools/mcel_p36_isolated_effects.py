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

from tools.mcel_p36_evidence import validate_host_url


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
            generation=_birth(oschild)
            # Observe a dedicated PID through the real server. No action admitted if not present.
            code,observed=_http(opener,host_url+'/api/applications/task/overview',
                                {'query':str(child.pid),'limit':100,'include_all':True,'include_connections':False})
            rows=observed.get('processes',[]) if code==200 else []
            matched=[v for v in rows if v.get('pid')==child.pid and v.get('created_at')==generation]
            if not matched:raise RuntimeError('child-generation-not-confirmed-by-host-observation')
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
    with tempfile.TemporaryDirectory(prefix='mcel-p36-schedules-') as d:
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
