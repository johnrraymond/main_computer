"""Fail-closed SLH/SEH laboratory-exit evidence runner.

No unimplemented requirement can become PASS. This is a local lab acceptance
harness, not a deployment or external-bridge tool. Each lane executes in a
separate child process, preserving the exact existing lab reports and traces.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import platform
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any

SCHEMA = 'main-computer.slh-seh-laboratory-exit.v1'

# These are genuine missing capabilities, not waiverable statuses. A complete
# lab exit requires implementing corresponding observable integration tests.
UNIMPLEMENTED_REQUIRED = {
    'crash_during_paid_execution': 'No automated Worker/Organization/SEH crash at each in-flight paid state with recovery and exactly-once audit',
    'persistent_three_hub_crash_restart': 'Three-Hub smoke verifies live FDB, but not crash/restart persistence while paid work is in flight',
    'cross_hub_owner_handoff': 'The SLH paid lane exercises one selected Hub, not cross-Hub owner handoff',
    'representative_compute_output': 'The integrated Worker uses an echo executor, not a production compute workload',
    'credential_rotation_live_connections': 'Authenticated lifecycle is tested, but live credential rotation and revocation under traffic lack an exit lane',
    'pinky_promise_economic_cycle': (
        'No verified end-to-end Pinky Promise cycle: organization wallet earnings, '
        'authenticated local secure-model fulfillment, exactly-once 20%-of-market-price '
        'assessment, and delayed Main Computer Reserve settlement'
    ),
}


@dataclass(frozen=True)
class ExitSpec:
    name: str
    command: tuple[str, ...]
    timeout: int
    kind: str
    scope: str


def exit_specs(*, profile: str, repeats: int = 10, soak_seconds: int = 3600) -> list[ExitSpec]:
    if profile not in {'smoke', 'full'}:
        raise ValueError('profile must be smoke or full')
    if repeats < 1 or repeats > 100 or soak_seconds < 0 or soak_seconds > 7200:
        raise ValueError('invalid laboratory exit bounds')
    base = ('-m', 'tools.slh_seh_lab.run_lab')
    specs = [
        ExitSpec('core_local_acceptance', base + ('verify-local', '--json'), 240,
                 'local', 'real-process and in-memory laboratory checks'),
        ExitSpec('security_negative_regressions',
                 ('-m','pytest','-q','tests/test_slh_enrollment_lifecycle.py',
                  'tests/test_slh_phase2a_auth_api.py','tests/test_slh_managed_gateway.py'),
                 240, 'pytest', 'existing authorization and enrollment regression tests'),
    ]
    if profile == 'smoke':
        specs += [
            ExitSpec('strict_integrated_smoke', base + ('stress-integrated', '--workers','8',
                    '--requests','8','--concurrency','4','--seed','1729',
                    '--causal-probe','--strict-acceptance','--json'), 240, 'stress',
                     'eight real connections, exact paid ledger and lifecycle'),
            ExitSpec('paid_transport_smoke', base + ('diagnose-paid-transport',
                    '--transport','both','--cycles','8','--idle-seconds','0.5',
                    '--tls-io-mode','serialized','--json'), 180, 'paid',
                     'one direct and one SEH Worker, eight paid cycles each'),
        ]
    else:
        if repeats < 10:
            raise ValueError('Full exit requires at least ten repeated Windows stress runs')
        if soak_seconds < 3600:
            raise ValueError('Full exit requires at least 3600 seconds on one Worker connection')
        for i in range(repeats):
            seed = 1729 + 104729 * i
            extra = ('--capacity-probe',) if i == 0 else ()
            specs.append(ExitSpec(f'windows_stress_{i+1:02d}',
                base + ('stress-integrated','--workers','64','--requests','64',
                        '--concurrency','8','--seed',str(seed),'--causal-probe',
                        '--strict-acceptance') + extra + ('--json',),
                360, 'stress', 'real 64-Worker network stress with strict 100% acceptance'))
        for concurrency in (16,32):
            specs.append(ExitSpec(f'concurrency_{concurrency}',
                base + ('stress-integrated','--workers','64','--requests','64',
                        '--concurrency',str(concurrency),'--seed',str(9001+concurrency),
                        '--causal-probe','--strict-acceptance','--json'),
                360, 'stress', 'concurrent paid load and exactly-once accounting'))
        specs += [
            ExitSpec('one_hour_same_connection_soak',
                base + ('diagnose-paid-transport','--transport','seh','--cycles','16',
                        '--idle-seconds','0.5','--tls-io-mode','serialized',
                        '--soak-seconds',str(soak_seconds),'--json'),
                soak_seconds+420, 'paid-soak',
                'one authenticated Worker remains connected for >=1h of paid cycles'),
            ExitSpec('direct_tls_32_paid_cycles',
                base + ('diagnose-paid-transport','--transport','direct','--cycles','32',
                        '--idle-seconds','0.5','--tls-io-mode','serialized','--json'),
                240, 'paid', 'direct TLS control through actual paid job protocol'),
            ExitSpec('three_hub_fdb_smoke', base + ('run','--json'), 300,
                'three-hub', 'three genuine isolated smoke Hubs with FDB, not in-memory Hub'),
        ]
    return specs


def _flag(command: tuple[str,...], name: str):
    try:return command[command.index(name)+1]
    except (ValueError, IndexError):return None


def judge(spec: ExitSpec, report: dict, *, returncode: int) -> tuple[bool, str]:
    """A subprocess exit code or a generic PASS is never enough by itself."""
    if returncode != 0:
        return False, 'NONZERO_EXIT'
    if spec.kind == 'pytest':
        return True, 'PYTEST_EXIT_ZERO_NARROW_SCOPE'
    if report.get('status') != 'PASS':
        return False, 'CHILD_DID_NOT_PASS'
    if spec.kind == 'stress':
        stress = report.get('integrated_stress') or {}
        paid = stress.get('paid') or {}
        p = stress.get('profile') or {}
        gates = stress.get('gates') or {}
        expected = int(p.get('paid_requests') or -1)
        if (p.get('workers') != int(_flag(spec.command, '--workers') or -1) or
                expected != int(_flag(spec.command, '--requests') or -1) or
                p.get('concurrency') != int(_flag(spec.command, '--concurrency') or -1) or
                p.get('seed') != int(_flag(spec.command, '--seed') or -1) or
                p.get('strict_acceptance') is not True):
            return False, 'STRESS_PROFILE_OR_STRICT_MODE_NOT_PROVEN'
        if (not report.get('pass') or stress.get('pass') is not True or
                expected < 1 or not all(gates.values()) or
                gates.get('all_paid_requests_accepted') is not True or
                gates.get('all_paid_requests_succeeded') is not True or
                paid.get('submitted') != expected or paid.get('accepted') != expected or
                paid.get('succeeded') != expected or paid.get('rejected') != 0 or
                paid.get('failed') != 0):
            return False, 'STRICT_PAID_INVARIANT_FAILED'
        if '--capacity-probe' in spec.command and (
                gates.get('capacity_65th_rejected_without_disruption') is not True or
                gates.get('capacity_existing_workers_retained') is not True):
            return False, 'CAPACITY_GATE_MISSING'
        events = stress.get('events') or []
        if not any(e.get('event') == 'complete' and e.get('result') == 'PASS' for e in events):
            return False, 'STRESS_COMPLETION_WITNESS_MISSING'
        if any(e.get('event') == 'causal_fleet_checkpoint' and
               (e.get('missing_workers') or e.get('unexpected_workers') or
                e.get('dead_worker_clients')) for e in events):
            return False, 'WORKER_LOSS_WITNESSED'
        return True, 'STRICT_INTEGRATED_ACCEPTED'
    if spec.kind in {'paid','paid-soak'}:
        paths = report.get('paths') or {}
        requested = int(report.get('cycles_requested') or -1)
        requested_transport = _flag(spec.command, '--transport')
        expected_paths = {'direct','seh'} if requested_transport == 'both' else {requested_transport}
        if set(paths) != expected_paths or requested != int(_flag(spec.command, '--cycles') or -1):
            return False, 'PAID_DIFFERENTIAL_PATH_OR_CYCLE_MISSING'
        if (not report.get('pass') or requested < 0 or not paths or
            any(not v.get('pass') or v.get('cycles_completed',-1) < requested or
                v.get('worker_survived') is not True or
                v.get('tls_io_mode') != 'serialized' for v in paths.values())):
            return False, 'PAID_CYCLE_OR_WORKER_SURVIVAL_FAILED'
        if spec.kind == 'paid-soak':
            required = int(report.get('soak_seconds_required') or 0)
            if required < 3600 or required != int(_flag(spec.command,'--soak-seconds') or -1) or any(
                    v.get('soak_seconds_required') != required or
                    v.get('elapsed_seconds',0) < required or
                v.get('admin_session_refreshes', 0) < required // 900
                for v in paths.values()):
                return False, 'SUSTAINED_DURATION_NOT_PROVEN'
        return True, 'PAID_CONNECTION_RETAINED'
    if spec.kind == 'three-hub':
        cluster=report.get('hub_cluster') or {}
        return (bool(cluster.get('ok') and len(cluster.get('ids') or ()) == 3),
                'THREE_HUB_SMOKE_VERIFIED' if cluster.get('ok') and len(cluster.get('ids') or ()) == 3
                else 'THREE_HUB_WITNESS_MISSING')
    if spec.kind == 'local':
        required=('two_service_lab','real_process_paid_work','real_process_credit_recovery','decision_stress','slh')
        if any(report.get(k, {}).get('pass') is not True for k in required):
            return False, 'LOCAL_REQUIRED_SUBSUITE_MISSING'
        from .credit_recovery_acceptance import accepted as recovery_accepted
        recovery = (report.get('real_process_credit_recovery') or {}).get('credit_recovery')
        if not recovery_accepted(recovery):
            return False, 'REAL_CREDIT_RECOVERY_EVIDENCE_MISSING'
        edge = report.get('credit_edge_regressions') or {}
        if edge.get('pass') is not True or edge.get('cases_passed') != 6:
            return False, 'CREDIT_EDGE_REGRESSIONS_MISSING'
        return True, 'LOCAL_SUBSUITES_VERIFIED'
    return False, 'UNKNOWN_LANE'


def _atomic(path: Path, report: dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    tmp.replace(path)


def _terminate_tree(proc):
    if proc.poll() is not None:
        return
    if os.name == 'nt':
        # Kill lab child processes too. Never aim at any pre-existing service.
        subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],
                       capture_output=True,timeout=20,check=False)
    else:
        try:os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    try:proc.wait(timeout=10)
    except subprocess.TimeoutExpired:proc.kill()


def _execute(spec: ExitSpec, folder: Path, *, root: Path, python: str):
    folder.mkdir(parents=True,exist_ok=True)
    command = [python, *spec.command]
    if spec.kind != 'pytest':
        command.extend(['--report-dir', str(folder/'child-reports')])
    began=time.monotonic()
    log=folder/'child-output.log'
    timed_out=False
    with log.open('w',encoding='utf-8') as out:
        proc=subprocess.Popen(command,cwd=root,stdout=out,stderr=subprocess.STDOUT,
             creationflags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0) if os.name=='nt' else 0,
             start_new_session=os.name!='nt')
        try:rc=proc.wait(timeout=spec.timeout)
        except subprocess.TimeoutExpired:
            timed_out=True
            _terminate_tree(proc)
            rc=-1
    child_report={}
    if spec.kind != 'pytest':
        found=sorted((folder/'child-reports').glob('*/report.json'))
        if len(found)==1:
            try:child_report=json.loads(found[0].read_text(encoding='utf-8'))
            except (OSError,ValueError):pass
    passed,why=judge(spec,child_report,returncode=rc) if not timed_out else (False,'TIMEOUT')
    # Preserve bounded, secret-free failure attribution; an exit code alone
    # cannot distinguish auth expiry, paid failure, or transport termination.
    child_failure = None
    if not passed and child_report:
        path_failures = {}
        for mode, path_report in (child_report.get('paths') or {}).items():
            failure = (path_report or {}).get('failure') or {}
            if failure:
                path_failures[mode] = {key: failure[key] for key in
                    ('stage', 'exception_class', 'classification', 'http_status') if key in failure}
        child_failure = {
            'status': child_report.get('status'),
            'mode': child_report.get('mode'),
            'error_type': child_report.get('error_type'),
            'path_failures': path_failures,
            'trace_paths': {mode: path_report.get('trace_path')
                            for mode, path_report in (child_report.get('paths') or {}).items()
                            if (path_report or {}).get('trace_path')},
        }
    result = {'name':spec.name,'status':'PASS' if passed else 'FAIL',
            'reason':why,'kind':spec.kind,'scope':spec.scope,
            'duration_seconds':round(time.monotonic()-began,2),
            'exit_code':rc,'timeout_seconds':spec.timeout,
            'child_report_path':child_report.get('report_path'),
            'console_log':str(log.resolve())}
    if child_failure is not None:
        result['child_failure'] = child_failure
    return result


def run_exit(*, profile='smoke', repeats=10, soak_seconds=3600,
             report_dir: Path, stop_on_failure=True, python=sys.executable,
             root: Path | None = None, executor=None) -> dict:
    from .run_lab import validate_topology, DEFAULT_TOPOLOGY
    # Even a smoke profile must not target a production/remote cluster.
    topology = validate_topology(DEFAULT_TOPOLOGY)
    specs=exit_specs(profile=profile,repeats=repeats,soak_seconds=soak_seconds)
    root = Path(root or Path.cwd()).resolve()
    report_dir=Path(report_dir)
    report_dir.mkdir(parents=True,exist_ok=True)
    report={'schema':SCHEMA,'status':'IN_PROGRESS','accepted':False,
            'profile':profile,'configured_repetitions':repeats if profile=='full' else 0,
            'required_soak_seconds':soak_seconds if profile=='full' else 0,
            'topology':topology,'lanes':[], 'required_gaps':{},
            'test_platform':platform.system(),
            'scope':'LOCAL_LAB_ONLY_SYNTHETIC_FUNDS_NO_REAL_BRIDGE',
            'started_utc':datetime.now(timezone.utc).isoformat()}
    journal=report_dir/'exit-report.json'
    _atomic(journal,report)
    execute=executor or _execute
    for idx,spec in enumerate(specs):
        try:
            item=execute(spec, report_dir/f'{idx+1:02d}-{spec.name}',root=root,python=python)
        except Exception as exc:
            item={'name':spec.name,'status':'FAIL','reason':'RUNNER_EXCEPTION_'+type(exc).__name__,
                  'kind':spec.kind,'scope':spec.scope}
        report['lanes'].append(item)
        _atomic(journal, report)
        if stop_on_failure and item['status']!='PASS':
            break
    if len(report['lanes']) < len(specs):
        for spec in specs[len(report['lanes']):]:
            report['lanes'].append({'name':spec.name,'kind':spec.kind,'scope':spec.scope,
                                    'status':'NOT_RUN','reason':'EARLIER_LANE_FAILED'})
    # Unimplemented requirements are not silently waived, even if all existing
    # lab lanes pass and every paid credit settles exactly once.
    if profile=='full':
        report['required_gaps']={name:{'status':'BLOCKED_UNIMPLEMENTED', 'reason':reason}
                                 for name,reason in UNIMPLEMENTED_REQUIRED.items()}
        if platform.system() != 'Windows':
            report['required_gaps']['required_windows_validation'] = {
                'status':'BLOCKED_UNIMPLEMENTED',
                'reason':'Full acceptance must run on Windows, where the original TLS defect occurred'}
    else:
        report['required_gaps']={'full_laboratory_exit':{
            'status':'NOT_RUN','reason':'Smoke profile is a diagnostic preview, not an exit'}}
    report['status']='ACCEPTED' if (profile=='full' and not report['required_gaps'] and
        len(report['lanes'])==len(specs) and all(row['status']=='PASS' for row in report['lanes'])) else 'NOT_ACCEPTED'
    report['accepted']=report['status']=='ACCEPTED'
    report['finished_utc']=datetime.now(timezone.utc).isoformat()
    report['exit_report_path']=str(journal.resolve())
    _atomic(journal,report)
    return report
