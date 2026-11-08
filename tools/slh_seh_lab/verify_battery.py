"""Evidence-first, non-production-mutating SLH/SEH acceptance battery.

Usage (from the repository root):
  python -m tools.slh_seh_lab.verify_battery plan --with-fdb --live-readonly
  python -u -m tools.slh_seh_lab.verify_battery run --with-fdb --live-readonly
  python -u -m tools.slh_seh_lab.verify_battery run --with-fdb --live-readonly --admin-device admin-pc

The local laboratory creates disposable loopback processes and synthetic credits.
The optional FDB lane is restricted by the existing run_lab smoke-topology guard.
The optional mainnet inspections are read-only: NO deploy/activate/sweep/payout.
No result from this runner certifies a public paid job or full laboratory exit.
"""
from __future__ import annotations

import argparse
import errno
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
from typing import Any

from .lab_exit import UNIMPLEMENTED_REQUIRED

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / 'runtime' / 'reports' / 'slh-seh-battery'
SCHEMA = 'main-computer.slh-seh-evidence-battery.v1'

# Retain separate groups so one collection failure cannot hide the other lanes.
REGRESSIONS: dict[str, tuple[str, ...]] = {
    'scheduler_and_pinky_core': (
        'tests/test_slh_worker_bank_scheduler.py',
        'tests/test_slh_scheduler_policy.py',
        'tests/test_slh_pinky_obligations.py',
        'tests/test_slh_pinky_periodic_sweep.py',
    ),
    'identity_authorization_and_hardening': (
        'tests/test_slh_enrollment_lifecycle.py',
        'tests/test_slh_canonical_port_authenticator_cli.py',
        'tests/test_slh_no_organization_identity.py',
        'tests/test_slh_private_key_acl.py',
        'tests/test_slh_local_authenticator_api.py',
        'tests/test_slh_phase2a_auth_api.py',
        'tests/test_slh_managed_gateway.py',
        'tests/test_slh_seh_four_issue_hardening.py',
    ),
    'worker_private_compute_and_scheduler': (
        'tests/test_slh_private_requests.py',
        'tests/test_slh_private_execution_integration.py',
        'tests/test_slh_worker_requester_acceptance.py',
        'tests/test_slh_marketplace_automatic.py',
        'tests/test_slh_organization_wallet.py',
        'tests/test_slh_operational_service.py',
    ),
    'outbound_relay_and_recovery': (
        'tests/test_seh_real_relay.py',
        'tests/test_seh_stream_write_queues.py',
        'tests/test_slh_seh_autoconnect.py',
        'tests/test_slh_canonical_seh_connection.py',
        'tests/test_slh_connection_lifecycle_diagnostics.py',
        'tests/test_slh_tls_io_serialization_ab.py',
        'tests/test_slh_stream_isolation.py',
        'tests/test_slh_seh_governed_activation.py',
        'tests/test_slh_seh_fresh_identity_bootstrap.py',
        'tests/test_slh_seh_lab.py',
        'tests/test_slh_lab_exit_acceptance.py',
    ),
    'pinky_exactly_once_accounting': (
        'tests/test_slh_pinky_shared_hub_lab.py',
        'tests/test_stable_hub_fdb_atomic_accounting.py',
        'tests/test_slh_pinky_lab_stage_diagnostics.py',
        'tests/test_slh_pinky_timeout_diagnostics.py',
    ),
    'seh_deployment_and_trust_controls': (
        'tests/test_seh_canonical_remote_metadata.py',
        'tests/test_seh_deployment_state_authority.py',
        'tests/test_seh_dns_probe.py',
        'tests/test_seh_failed_build_retry.py',
        'tests/test_seh_git_publication_safety.py',
        'tests/test_seh_git_remote_deployment.py',
        'tests/test_seh_mutate_harness_operator.py',
        'tests/test_seh_operator_trust_binding.py',
        'tests/test_seh_topology_runtime_recovery.py',
        'tests/test_seh_trust_diagnostic.py',
        'tests/test_stable_hub_runtime.py',
        'tests/test_slh_seh_verify_battery.py',
    ),
}


def _json_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    temp.replace(path)


def _specs(*, with_fdb: bool, live_readonly: bool):
    specs = []
    for name, files in REGRESSIONS.items():
        specs.append((name, [sys.executable, '-m', 'pytest', '-q', '--maxfail=1', *files], 420, 'pytest'))
    base = [sys.executable, '-m', 'tools.slh_seh_lab.run_lab']
    specs += [
        ('formal_smoke_exit_lanes', base + ['lab-exit', '--exit-profile', 'smoke',
                 '--exit-continue-on-failure', '--json'], 1200, 'lab-exit'),
        ('paid_requester_direct', base + ['verify-worker-requester', '--worker-connection',
                 'slh', '--json'], 360, 'lab'),
        ('paid_requester_seh', base + ['verify-worker-requester', '--worker-connection',
                 'seh', '--json'], 360, 'lab'),
    ]
    if with_fdb:
        specs.append(('three_hub_fdb_pinky_cycle', base + ['run', '--pinky-integrated',
                      '--pinky-repeat', '3', '--json'], 900, 'fdb'))
    if live_readonly:
        specs += [
            ('mainnet_hub_inspect_readonly', [sys.executable, 'hub_mutate_harness.py',
                 'inspect', '--network', 'mainnet'], 180, 'inspect'),
            ('mainnet_seh_diagnostic_readonly', [sys.executable, 'seh_mutate_harness.py',
                 'diagnostic', '--network', 'mainnet'], 180, 'seh-inspect'),
            ('resident_slh_connection_readonly', None, 30, 'resident'),
        ]
    return specs


def _extract_json(text: str) -> dict | None:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        return None


def _evaluate(kind: str, result: dict | None, code: int, stdout: str):
    if kind == 'pytest':
        match = re.search(r'(?<!\d)(\d+) passed\b', stdout)
        count = int(match.group(1)) if match else 0
        failed = re.findall(r'(?m)^(?:FAILED|ERROR) [^\r\n]{1,300}', stdout)
        return (code == 0 and count > 0, {'tests_passed': count,
                'reason': 'PYTEST_PASS' if code == 0 and count > 0 else 'PYTEST_ERROR_OR_FAILURE',
                'failed_cases': failed[:3]})
    if kind == 'inspect':
        accepted = re.search(r'(?m)^status:\s+accepted\s*$', stdout) is not None
        verified = re.search(r'(?m)^verification:\s+verified\s*$', stdout) is not None
        hubs = all(name in stdout for name in ('mainneta-hub1', 'mainnetc-hub1'))
        passed = code == 0 and accepted and verified and hubs
        return passed, {'reason': 'MAINNET_TOPOLOGY_VERIFIED' if passed else 'MAINNET_TOPOLOGY_UNVERIFIED',
                        'accepted': accepted, 'verified': verified,
                        'both_mainnet_hubs_present': hubs,
                        'not_proven': 'live paid processing'}
    if kind == 'seh-inspect':
        observed = result or {}
        passed = code == 0 and observed.get('status') == 'READ_ONLY' and (
            observed.get('signing_key_trust') == 'MATCHED' or
            observed.get('operation') == 'diagnostic' and observed.get('signing_key_trust') == 'MATCHED')
        return passed, {'reason': 'SEH_TRUST_RESOLVED' if passed else 'SEH_TRUST_NOT_VERIFIED',
                        'status': observed.get('status'),
                        'resident_tunnel': 'NOT_OBSERVED_BY_DIAGNOSTIC'}
    if kind == 'lab-exit':
        # lab_exit deliberately returns NOT_ACCEPTED and exit 1 even if *all*
        # diagnostic smoke lanes pass. Inspect explicit child evidence instead.
        observed = result or {}
        expected = {'core_local_acceptance', 'security_negative_regressions',
                    'strict_integrated_smoke', 'paid_transport_smoke'}
        lanes = observed.get('lanes') or []
        got = {row.get('name') for row in lanes if row.get('status') == 'PASS'}
        passed = (observed.get('profile') == 'smoke' and observed.get('status') == 'NOT_ACCEPTED'
                  and got == expected and len(lanes) == len(expected))
        return passed, {'reason': ('SMOKE_LANES_PASS_FULL_EXIT_OPEN' if passed
                                  else 'SMOKE_LANE_FAILURE'),
                        'child_lanes': {row.get('name'): row.get('status') for row in lanes},
                        'failed_child_reasons': {row.get('name'): row.get('reason')
                            for row in lanes if row.get('status') != 'PASS'},
                        'failed_child_evidence': {row.get('name'): row.get('child_failure')
                            for row in lanes if row.get('status') != 'PASS' and row.get('child_failure')},
                        'full_exit_accepted': False,
                        'exit_report_path': observed.get('exit_report_path')}
    if kind == 'fdb':
        observed = result or {}
        repeat = observed.get('pinky_repeatability') or {}
        cluster = observed.get('hub_cluster') or {}
        passed = (code == 0 and observed.get('status') == 'PASS'
                  and repeat.get('pass') is True and repeat.get('cycles_completed') == 3
                  and repeat.get('cycles_requested') == 3
                  and cluster.get('ok') is True and len(cluster.get('ids') or []) == 3
                  and observed.get('pinky_internal_reserve_sweep') == 'CONFIRMED_IN_FDB')
        # A repeatability summary can report only aggregate failures such as
        # 'all_individual_cycles_passed'. Preserve the underlying failed gates
        # from every completed economic cycle so a one-cycle stop is actionable.
        cycle_failures = [
            {'cycle': cycle.get('cycle'),
             'status': cycle.get('status'),
             'failed_gates': sorted(key for key, value in
                                    (cycle.get('gates') or {}).items() if value is not True)}
            for cycle in (repeat.get('cycles') or [])
            if cycle.get('pass') is not True or any(
                value is not True for value in (cycle.get('gates') or {}).values())
        ]
        return passed, {'reason': 'FDB_PINKY_3_CYCLES_VERIFIED' if passed
                        else 'FDB_PINKY_EVIDENCE_INCOMPLETE',
                        'reconciliation_cycle_failures': cycle_failures,
                        'cycles': repeat.get('cycles_completed'),
                        'cluster_size': len(cluster.get('ids') or []),
                        'settlement': observed.get('paid_work_settlement'),
                        'receipt': observed.get('pinky_economic_reconciliation_path'),
                        'error_type': observed.get('error_type'),
                        'failed_stage': observed.get('terminal_failed_lab_stage'),
                        'first_failed_stage': observed.get('first_failed_lab_stage'),
                        'failure_before_hub_launch': observed.get('failure_before_hub_launch'),
                        'last_paid_cycle_event': observed.get('last_paid_cycle_event'),
                        'reconciliation_failed_gates': [key for key, value in
                            (repeat.get('gates') or {}).items() if value is not True],
                        'child_report': observed.get('report_path')}
    if kind == 'lab':
        observed = result or {}
        gates = observed.get('gates') or {}
        passed = (code == 0 and observed.get('status') == 'PASS'
                  and observed.get('pass') is True and bool(gates)
                  and all(value is True for value in gates.values()))
        return passed, {'reason': 'PAID_REQUESTER_VERIFIED' if passed
                        else 'PAID_REQUESTER_INCOMPLETE',
                        'passed_gates': sum(value is True for value in gates.values()),
                        'total_gates': len(gates),
                        'failed_gates': sorted(key for key, value in gates.items() if value is not True),
                        'failure': observed.get('failure'), 'last_stage': observed.get('last_stage'),
                        'child_report': observed.get('report_path'),
                        'limitations': observed.get('limitations')}
    return False, {'reason': 'UNKNOWN_LANE'}


def _resident_status(*, admin_device: str | None, local_port: int) -> tuple[bool, dict]:
    """Read an existing SLH's authenticated status, without changing services.

    Preserve the failing stage and only known sanitized CLI error strings.
    A raw exception from an arbitrary authenticator is never printed.
    """
    from main_computer.slh.authenticator_store import LocalIdentityStore
    from tools.slh_authenticator_cli import _request
    stage = 'local_identity_lookup'
    token = None
    try:
        store = LocalIdentityStore()
        identities = store.list()
        valid = identities.get('identities') or []
        if admin_device:
            if admin_device not in {x['device_id'] for x in valid}:
                return False, {'reason': 'ADMIN_DEVICE_NOT_FOUND'}
            selected = admin_device
        elif len(valid) == 1 and not identities.get('invalid_identities'):
            selected = valid[0]['device_id']
        else:
            return False, {'reason': 'ADMIN_DEVICE_SELECTION_REQUIRED',
                           'identity_count': len(valid)}
        stage = 'auth_challenge'
        challenge = _request(local_port, 'POST', '/v1/auth/challenge',
                             {'device_id': selected})
        stage = 'local_challenge_signing'
        signed = store.sign(selected, challenge)
        stage = 'auth_verify'
        login = _request(local_port, 'POST', '/v1/auth/verify',
                         {'device_id': selected, 'challenge_id': challenge['challenge_id'],
                          'signature': signed['signature']})
        token = login.get('access_token')
        if not isinstance(token, str) or not token:
            return False, {'reason': 'ADMIN_LOGIN_NO_TOKEN', 'stage': stage}
        stage = 'read_seh_status'
        status = _request(local_port, 'GET', '/v1/local/seh/status', token=token)
        checked = ('outbound_tunnel_authenticated', 'private_requests_available',
                   'pinky_sweeper_available', 'workers_enabled')
        passed = status.get('status') == 'CONNECTED_VERIFIED' and all(
            status.get(key) is True for key in checked)
        return passed, {'reason': 'RESIDENT_TUNNEL_AND_SERVICES_VERIFIED' if passed
                         else 'RESIDENT_NOT_FULLY_CONNECTED',
                        'status': status.get('status'),
                        **{key: status.get(key) for key in checked},
                        'worker_serving': status.get('workers_enabled'),
                        'resident_child_observed': status.get('resident_child_observed'),
                        'resident_outbound_phase': status.get('resident_outbound_phase'),
                        'resident_outbound_last_error_class': status.get('resident_outbound_last_error_class')}
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        message = str(exc)
        # Only pass through the _request() transport's explicitly bounded,
        # secret-free diagnostics. Never serialize signed challenges or tokens.
        known = ('SLH returned ', 'SLH is unreachable at ', 'SLH redirect refused',
                 'SLH returned invalid JSON', 'Invalid SLH response')
        return False, {'reason': 'RESIDENT_PROBE_ERROR', 'stage': stage,
                       'error_class': type(exc).__name__,
                       **({'safe_error': message[:180]} if message.startswith(known) else {})}
    finally:
        if token:
            try:
                _request(local_port, 'POST', '/v1/auth/logout', {}, token=token)
            except (OSError, ValueError, RuntimeError):
                pass



def _resident_live_supervisor_claim(*, service: dict) -> bool:
    """Prove an old supervisor policy still belongs to the active process.

    A stale state file on its own cannot establish the active startup policy.
    This checks the PID claim, exact checkout, and the current process command
    without starting or altering any service. Never use os.kill(pid, 0) here:
    on Windows it can terminate the target process.
    """
    from main_computer import service_supervisor as supervisor

    try:
        pid = int(service.get('pid') or 0)
        if pid <= 0:
            return False
        raw = (ROOT / supervisor.SERVICE_SUPERVISOR_PID_FILENAME).read_text(encoding='utf-8')
        claim = supervisor._pid_entry_from_text(raw)
        if (supervisor._pid_from_entry(claim) != pid
                or claim.get('service') != supervisor.SERVICE_NAME
                or supervisor._safe_resolved_path(claim.get('root')) != str(ROOT.resolve())):
            return False
        command = supervisor._read_process_command_line_default(pid)
        return bool(supervisor._looks_like_service_supervisor_command(command)
                    and supervisor._pid_file_command_matches_live(claim, command))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _resident_explicit_connection_refusal(*, detail: dict, local_port: int) -> bool:
    """Recognize a direct localhost refusal observed by our own HTTP probe.

    The socket preflight can return an indeterminate result on Windows even
    when the actual HTTP challenge reports WSAECONNREFUSED (10061). Treat the
    HTTP evidence as an independent, *positive* absence check. Never infer
    absence from a timeout, a generic network error, or an auth failure.
    """
    if (detail.get('reason') != 'RESIDENT_PROBE_ERROR'
            or detail.get('stage') != 'auth_challenge'
            or detail.get('error_class') != 'RuntimeError'):
        return False
    error = detail.get('safe_error')
    if not isinstance(error, str):
        return False
    prefix = f'SLH is unreachable at 127.0.0.1:{local_port}: '
    if not error.startswith(prefix):
        return False
    refusal = error[len(prefix):]
    return ('[WinError 10061]' in refusal or '[Errno 111]' in refusal or
            '[Errno 61]' in refusal)


def _resident_policy_decision(*, local_port: int,
                              resident_probe: dict | None = None) -> tuple[dict | None, dict]:
    """Return an explicit disabled-policy skip or a safe reason it was refused.

    Other child services being degraded must not erase an explicit SLH-disabled
    policy. A stale policy is accepted only if its PID claim and live command
    demonstrate the same supervisor is still active. A present listener is
    ALWAYS checked with authenticated verification instead of being skipped.
    """
    diagnostic = {'policy_check': 'NOT_ELIGIBLE'}
    if local_port != 8897:
        return None, {**diagnostic, 'reason': 'NONDEFAULT_RESIDENT_PORT'}

    from main_computer.service_supervisor import load_service_supervisor_state
    try:
        state = load_service_supervisor_state(ROOT)
        if not isinstance(state, dict):
            return None, {**diagnostic, 'reason': 'INVALID_SUPERVISOR_STATE'}
        service = state.get('service') or {}
        if not isinstance(service, dict):
            return None, {**diagnostic, 'reason': 'INVALID_SUPERVISOR_SERVICE'}
        if state.get('slh_supervised') is not False:
            return None, {**diagnostic, 'reason': 'SLH_NOT_EXPLICITLY_DISABLED',
                          'slh_supervised': state.get('slh_supervised')}
        if service.get('state') != 'supervising':
            return None, {**diagnostic, 'reason': 'SUPERVISOR_NOT_SUPERVISING'}
        if 'slh' in (state.get('children') or {}):
            return None, {**diagnostic, 'reason': 'SUPERVISOR_HAS_SLH_CHILD'}

        updated_at = datetime.fromisoformat(str(state['updated_at']))
        if updated_at.tzinfo is None:
            return None, {**diagnostic, 'reason': 'UNDATED_SUPERVISOR_STATE'}
        age = (datetime.now(timezone.utc) - updated_at).total_seconds()
        if age < -5:
            return None, {**diagnostic, 'reason': 'FUTURE_SUPERVISOR_STATE'}
        if age > 60:
            if not _resident_live_supervisor_claim(service=service):
                return None, {**diagnostic, 'reason': 'STALE_SUPERVISOR_POLICY_UNVERIFIED'}
            source = 'verified_live_supervisor_pid_claim'
        else:
            source = 'fresh_supervisor_state'
        listener = 'undetermined'
        socket_error: OSError | None = None
        try:
            with socket.create_connection(('127.0.0.1', local_port), timeout=0.5):
                return None, {**diagnostic, 'reason': 'RESIDENT_LISTENER_PRESENT'}
        except OSError as exc:
            socket_error = exc
            # Winsock sometimes raises a plain OSError for WSAECONNREFUSED.
            # Do not classify timeouts or other Winsock errors as absence.
            if (isinstance(exc, ConnectionRefusedError)
                    or getattr(exc, 'winerror', None) == 10061
                    or getattr(exc, 'errno', None) in (errno.ECONNREFUSED, 10061)):
                listener = 'connection_refused'
        if listener == 'undetermined' and resident_probe is not None:
            if _resident_explicit_connection_refusal(detail=resident_probe,
                                                     local_port=local_port):
                listener = 'http_challenge_connection_refused'
        if listener == 'undetermined':
            return None, {**diagnostic, 'reason': 'RESIDENT_LISTENER_UNDETERMINED',
                          'socket_error_class': type(socket_error).__name__ if socket_error else None,
                          'socket_errno': getattr(socket_error, 'errno', None),
                          'socket_winerror': getattr(socket_error, 'winerror', None)}
        return ({'reason': 'RESIDENT_DISABLED_BY_POLICY',
                 'policy': 'SKIPPED_BY_POLICY',
                 'policy_source': source,
                 'slh_supervised': False,
                 'listener': listener,
                 'resident_verified': False},
                {'policy_check': 'VERIFIED', 'reason': 'RESIDENT_DISABLED_BY_POLICY'})
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None, {**diagnostic, 'reason': 'SUPERVISOR_POLICY_EVIDENCE_UNAVAILABLE'}


def _resident_policy_skip(*, local_port: int) -> dict | None:
    """Skip an absent resident only when its disabled policy is evidenced."""
    skip, _diagnostic = _resident_policy_decision(local_port=local_port)
    return skip


def _run_step(name: str, argv: list[str] | None, timeout: int, kind: str,
              output: Path, *, admin_device: str | None, local_port: int,
              require_resident_slh: bool = False) -> dict:
    began = time.monotonic()
    log = output / (name + '.log')
    status, detail, code = 'FAIL', {'reason': 'NOT_RUN'}, None
    if kind == 'resident':
        try:
            skip = None if require_resident_slh else _resident_policy_skip(local_port=local_port)
            if skip is not None:
                status, detail, code = 'SKIPPED_BY_POLICY', skip, None
            else:
                passed, detail = _resident_status(admin_device=admin_device,
                                                  local_port=local_port)
                if not passed and not require_resident_slh:
                    # Retain the reason a disabled-policy skip was refused.
                    # This is diagnostic evidence, never an alternative PASS.
                    recovery_skip, decision = _resident_policy_decision(
                        local_port=local_port, resident_probe=detail)
                    if recovery_skip is not None:
                        status, detail, code = 'SKIPPED_BY_POLICY', recovery_skip, None
                    else:
                        detail['policy_skip_decision'] = decision
                if status != 'SKIPPED_BY_POLICY':
                    status = 'PASS' if passed else 'FAIL'
                    code = 0 if passed else 1
            log.write_text(json.dumps(detail, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            detail = {'reason': 'RESIDENT_PROBE_ERROR', 'error_class': type(exc).__name__}
            log.write_text(json.dumps(detail, indent=2) + '\n', encoding='utf-8')
            code = 1
    else:
        try:
            completed = subprocess.run(argv or (), cwd=ROOT, capture_output=True, text=True,
                                       errors='replace', timeout=timeout, check=False)
            code = completed.returncode
            text = completed.stdout + ('\n--- STDERR ---\n' + completed.stderr if completed.stderr else '')
            log.write_text(text, encoding='utf-8')
            passed, detail = _evaluate(kind, _extract_json(completed.stdout), code, completed.stdout)
            status = 'PASS' if passed else 'FAIL'
        except subprocess.TimeoutExpired:
            code = -1
            detail = {'reason': 'TIMEOUT', 'timeout_seconds': timeout}
            log.write_text(json.dumps(detail) + '\n', encoding='utf-8')
        except OSError as exc:
            code = -1
            detail = {'reason': 'EXECUTION_ERROR', 'error_class': type(exc).__name__}
            log.write_text(json.dumps(detail) + '\n', encoding='utf-8')
    return {'name': name, 'status': status, 'kind': kind, 'exit_code': code,
            'duration_seconds': round(time.monotonic() - began, 2),
            'log': str(log), 'evidence': detail}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('plan', 'run'))
    parser.add_argument('--with-fdb', action='store_true',
                        help='Run 3 isolated real FDB/three-Hub Pinky economic cycles')
    parser.add_argument('--live-readonly', action='store_true',
                        help='Read mainnet Hub/SEH state and authenticated resident SLH status')
    parser.add_argument('--only', action='append', choices=[*REGRESSIONS, 'formal_smoke_exit_lanes',
                        'paid_requester_direct', 'paid_requester_seh',
                        'three_hub_fdb_pinky_cycle', 'mainnet_hub_inspect_readonly',
                        'mainnet_seh_diagnostic_readonly', 'resident_slh_connection_readonly'],
                        help='Run only named lane(s); repeat --only for multiple')
    parser.add_argument('--admin-device', default=None,
                        help='Existing local SLH administrator identity (never created by this tool)')
    parser.add_argument('--require-resident-slh', action='store_true',
                        help='Treat absent resident SLH as a failure even when supervisor policy disables it')
    parser.add_argument('--local-port', type=int, default=8897,
                        help='Existing SLH loopback API port; default 8897')
    parser.add_argument('--report-dir', type=Path, default=REPORTS,
                        help='Evidence output root')
    args = parser.parse_args(argv)
    if not 1 <= args.local_port <= 65535:
        parser.error('--local-port must be 1..65535')
    if args.admin_device and not args.live_readonly:
        parser.error('--admin-device requires --live-readonly')
    if args.require_resident_slh and not args.live_readonly:
        parser.error('--require-resident-slh requires --live-readonly')
    specs = _specs(with_fdb=args.with_fdb, live_readonly=args.live_readonly)
    if args.only:
        specs = [spec for spec in specs if spec[0] in set(args.only)]
        missing = set(args.only) - {spec[0] for spec in specs}
        if missing:
            parser.error('Selected lanes require --with-fdb or --live-readonly: ' + ', '.join(sorted(missing)))
    gaps = {key: {'status': 'NOT_IMPLEMENTED', 'reason': value}
            for key, value in UNIMPLEMENTED_REQUIRED.items()}
    gaps['public_mainnet_paid_job_receipt'] = {'status': 'NOT_TESTED',
        'reason': 'No existing production-safe mainnet buyer-funded paid traversal is invoked'}
    if args.operation == 'plan':
        print(json.dumps({'schema': SCHEMA, 'status': 'PLANNED_ONLY',
            'production_mutations': False,
            'steps': [{'name': name, 'kind': kind, 'command': command,
                       'timeout_seconds': limit} for name, command, limit, kind in specs],
            'unresolved_exit_gates': gaps}, indent=2))
        return 0
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(3)
    output = args.report_dir.resolve() / run_id
    output.mkdir(parents=True, exist_ok=False)
    revision = 'UNAVAILABLE'
    try:
        git = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
                             text=True, timeout=6, check=False)
        if git.returncode == 0:
            revision = git.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    report: dict[str, Any] = {'schema': SCHEMA, 'status': 'IN_PROGRESS',
        'started_utc': datetime.now(timezone.utc).isoformat(),
        'git_revision': revision, 'python': sys.version.split()[0],
        'platform': sys.platform, 'production_mutations': False,
        'with_fdb': args.with_fdb, 'live_readonly': args.live_readonly,
        'selected_lanes_only': bool(args.only),
        'lanes': [], 'unresolved_exit_gates': gaps,
        'full_exit_accepted': False,
        'notes': ['LOCAL smoke and FDB evidence use disposable identities/credits.',
                  'Public paid mainnet job and cross-Hub failover remain separate gates.']}
    summary = output / 'report.json'
    _json_file(summary, report)
    for index, (name, command, limit, kind) in enumerate(specs, 1):
        print(f'[{index}/{len(specs)}] {name}: running', flush=True)
        row = _run_step(name, command, limit, kind, output,
                        admin_device=args.admin_device, local_port=args.local_port,
                        require_resident_slh=args.require_resident_slh)
        report['lanes'].append(row)
        _json_file(summary, report)
        print(f'[{index}/{len(specs)}] {name}: {row["status"]} ({row["evidence"]["reason"]})', flush=True)
    passed = sum(row['status'] == 'PASS' for row in report['lanes'])
    skipped = sum(row['status'] == 'SKIPPED_BY_POLICY' for row in report['lanes'])
    failed = len(report['lanes']) - passed - skipped
    if failed:
        report['status'] = 'BATTERY_FAILED'
    elif skipped and not passed:
        report['status'] = 'SCOPE_SKIPPED_BY_POLICY'
    elif skipped:
        report['status'] = 'SCOPE_PASS_WITH_POLICY_SKIPS_FULL_EXIT_OPEN'
    else:
        report['status'] = 'SCOPE_PASS_FULL_EXIT_OPEN'
    report['passes'] = passed
    report['skipped_by_policy'] = skipped
    report['failures'] = failed
    report['finished_utc'] = datetime.now(timezone.utc).isoformat()
    _json_file(summary, report)
    print(f'BATTERY {report["status"]}: {passed} passed, {skipped} policy-skipped, '
          f'{failed} failed / {len(report["lanes"])} lanes; report={summary}', flush=True)
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
