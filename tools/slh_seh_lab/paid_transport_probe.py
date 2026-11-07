"""One-Worker differential paid-transport probe, real Organization Service/Hub.

Fresh disposable org state is supplied by real_paid_service. This module never
changes any production gateway or ledger state directly: it observes via the
real authenticated org API and submits real synthetic-funded Hub transactions.
"""
from __future__ import annotations

import json
import itertools
import re
from urllib.parse import urlsplit
from pathlib import Path
import time

from .integrated_stress import _connection_loss_timelines, _fetch_gateway_diagnostics
from .paid_cycle import verify_paid_cycle
from .service_lifecycle import _http, _ok


def _witness(*, worker, proxy, api_port, org_token):
    status = proxy.status()
    rows = status.get('workers') or []
    registered = any(row.get('device_id') == worker.device_id for row in rows)
    sessions = status.get('sessions') or {}
    return {
        'client_alive': bool(worker.is_alive),
        'org_registered': registered,
        'org_registered_total': len(rows),
        'public_session': worker.device_id in sessions,
        'active_work': bool(worker.has_active_work),
        'transport_events_last': [row.get('event') for row in worker.transport_diagnostics()[-8:]],
    }


def run_paid_transport_probe(*, mode, cycles, idle_seconds, hub_url, ledger, tls_io_mode="serialized",
                             soak_seconds=0, admin_session_refresh=None,
                             admin_refresh_interval_seconds=600,
                             proxy, worker, adapter, relay, api_port, org_token,
                             worker_account_id, lab_mode, trace_file,
                             acceptance=False, worker_port=None,
                             market_isolation_capability=None):
    """Per-cycle live observation; every failure includes the exact failing boundary.

    A failed job and an unexpected Worker disconnect are distinct. The caller
    can compare direct TLS and SEH runs made with otherwise identical setup.
    """
    if mode not in ('direct', 'seh'):
        raise ValueError('Probe requires direct or seh mode')
    if acceptance and (cycles != 1 or idle_seconds != 0 or soak_seconds != 0
                       or not market_isolation_capability or worker_port is None):
        raise ValueError('Worker/requester acceptance requires one isolated paid job and known Worker endpoint')
    path = Path(trace_file) if trace_file is not None else None
    checkpoints = []
    last_error = None
    stage = 'ready'
    started_ns = time.time_ns()
    started_monotonic = time.monotonic()
    completed = 0
    acceptance_evidence = None
    admin_session_refreshes = 0
    next_admin_refresh_at = started_monotonic + admin_refresh_interval_seconds
    if admin_session_refresh is not None and admin_refresh_interval_seconds <= 0:
        raise ValueError('Admin session refresh interval must be positive')

    def refresh_admin_if_due(cycle):
        """Renew the lab administrator's short-lived bearer, not the Worker TLS session.

        Organization administrator bearers expire after 900 seconds. The hour-
        long paid soak must use the normal signed challenge to renew rather
        than extending token lifetime or reconnecting the authenticated Worker.
        """
        nonlocal admin_session_refreshes, next_admin_refresh_at
        if admin_session_refresh is None or time.monotonic() < next_admin_refresh_at:
            return
        new_token = admin_session_refresh()
        if not isinstance(new_token, str) or not new_token:
            raise RuntimeError('Lab administrator reauthentication returned no bearer')
        proxy.token = new_token
        admin_session_refreshes += 1
        next_admin_refresh_at = time.monotonic() + admin_refresh_interval_seconds
        checkpoints.append({'stage': 'lab_admin_reauthenticated',
                            'cycle': cycle, 'time_ns': time.time_ns(),
                            'refresh_count': admin_session_refreshes,
                            'worker_connection_preserved': bool(worker.is_alive)})
        write_trace()

    def write_trace(*, final=False, evidence=None):
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {'schema': 'main-computer.slh-paid-transport-differential.v1',
                'mode': mode, 'tls_io_mode': tls_io_mode, 'cycles_requested': cycles,
                'idle_seconds': idle_seconds, 'soak_seconds_required':soak_seconds,
                'elapsed_seconds':round(time.monotonic()-started_monotonic, 3),
                'cycles_completed':completed, 'started_ns': started_ns,
                'admin_session_refreshes': admin_session_refreshes,
                'stage': stage, 'checkpoints': checkpoints[-96:],
                'failure': last_error}
        if evidence is not None:
            data['failure_connection_evidence'] = evidence
        # Write after each checkpoint. If a process crashes, previous evidence
        # remains available; private job inputs and authorization are omitted.
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(data, sort_keys=True, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)

    def check(stage_name, cycle):
        nonlocal stage
        stage = stage_name
        refresh_admin_if_due(cycle)
        sample = _witness(worker=worker, proxy=proxy, api_port=api_port, org_token=org_token)
        row = {'stage': stage, 'cycle': cycle, 'time_ns': time.time_ns(), **sample}
        checkpoints.append(row)
        write_trace()
        if not sample['client_alive'] or not sample['org_registered']:
            raise RuntimeError('Worker unexpectedly disconnected at ' + stage_name)
        return row

    try:
        check('connected_before_idle', -1)
        if acceptance:
            # SEH is a transport: direct has no relay/adapter instance, and
            # remote must use the loopback adapter attached to this relay.
            endpoint = urlsplit(worker.hub_url)
            expected_port = adapter.server_address[1] if mode == 'seh' and adapter is not None else worker_port
            if endpoint.hostname != '127.0.0.1' or endpoint.port != expected_port or endpoint.scheme != 'https':
                raise RuntimeError('Worker connected to an unexpected transport endpoint')
            if mode == 'direct' and (adapter is not None or relay is not None):
                raise RuntimeError('Direct SLH acceptance must not create an SEH relay')
            if mode == 'seh' and (adapter is None or relay is None or not relay.tunnel_ready):
                raise RuntimeError('SEH acceptance requires a live authenticated relay tunnel')
        if idle_seconds:
            time.sleep(idle_seconds)
        check('idle_baseline_completed', -1)
        # Remain on ONE authenticated Worker connection for the entire soak;
        # independent short runs would not establish connection longevity.
        for cycle in itertools.count():
            if cycle >= 50000:
                raise RuntimeError('Soak exceeded the bounded 50000-cycle limit')
            if cycle >= cycles and time.monotonic() - started_monotonic >= soak_seconds:
                break
            check('before_paid_job', cycle)
            stage = 'paid_job_in_flight'
            write_trace()
            before_relay = relay.bytes_forwarded if relay is not None else 0
            before_adapter = adapter.bytes_forwarded if adapter is not None else 0
            result = verify_paid_cycle(
                hub_url=hub_url, proxy=proxy, gateway=None, ledger=ledger,
                worker_account_id=worker_account_id, lab_mode=lab_mode,
                market_isolation_capability=market_isolation_capability if acceptance else None)
            if acceptance:
                # A one-off private capability excludes all unrelated Hub Workers.
                # Compare the accepted Hub connection to the actual live SLH slot,
                # not merely to the Worker's advertised device ID.
                slot = proxy.sessions.get(worker.device_id)
                accepted_connection = (slot.accepted or {}).get('connection_id') if slot else None
                # The Worker records last_result immediately after sending its
                # terminal frame; Hub settlement may race that local assignment.
                deadline = time.monotonic() + 2
                worker_completed = False
                while True:
                    worker_completed = (worker.last_result.get('request_id') == result.get('request_id')
                                        and worker.last_result.get('type') == 'worker.work.result')
                    if worker_completed or time.monotonic() >= deadline:
                        break
                    time.sleep(0.02)
                route_gates = {
                    'enrolled_worker_authenticated': bool(worker.is_alive and slot is not None),
                    'specific_worker_selected': bool(accepted_connection and
                        result.get('worker_connection_id') == accepted_connection),
                    'isolated_worker_capability_enforced': bool(result.get('gates', {}).get(
                        'market_isolation_capability_verified')),
                    'worker_executed_request': worker_completed,
                    'correct_result_returned': bool(result.get('gates', {}).get(
                        'worker_result_matches_private_marker')),
                    'buyer_distinct_from_slh_wallet': bool(result.get('gates', {}).get('buyer_and_worker_distinct')),
                    'buyer_debited_exactly_once': bool(result.get('charge_count') == 1 and
                        result.get('gates', {}).get('buyer_debit_equals_charge') and
                        result.get('gates', {}).get('idempotent_request_not_double_charged')),
                    'slh_wallet_earned_exactly_once': bool(result.get('earning_count') == 1 and
                        result.get('gates', {}).get('worker_earning_not_prematurely_spendable') and
                        result.get('gates', {}).get('wallet_authorized_pending_batch')),
                }
                if mode == 'direct':
                    route_gates['direct_transport_without_seh'] = adapter is None and relay is None
                else:
                    route_gates['seh_relay_transferred_paid_job'] = (
                        relay is not None and adapter is not None and
                        relay.bytes_forwarded > before_relay and
                        adapter.bytes_forwarded > before_adapter)
                acceptance_evidence = {
                    'schema': 'main-computer.slh-worker-requester-evidence.v1',
                    'worker_connection': 'slh' if mode == 'direct' else 'seh',
                    'worker': {'device_authenticated': route_gates['enrolled_worker_authenticated'],
                               'hub_session_selected': route_gates['specific_worker_selected'],
                               'executed_request': route_gates['worker_executed_request']},
                    'requester': {'distinct_from_slh_wallet': route_gates['buyer_distinct_from_slh_wallet'],
                                  'hub_request_succeeded': result.get('terminal_status') == 'succeeded'},
                    'transport': {'direct_no_seh': route_gates.get('direct_transport_without_seh'),
                                  'seh_relay_forwarded_during_job': route_gates.get('seh_relay_transferred_paid_job')},
                    'payment': {'charge_count': result.get('charge_count'),
                                'earning_count': result.get('earning_count'),
                                'charged_credit_wei': result.get('charged_credit_wei'),
                                'earned_credit_wei': result.get('earned_credit_wei'),
                                'settlement': result.get('settlement')},
                    'gates': {**result.get('gates', {}), **route_gates},
                }
                if not result.get('pass') or not all(acceptance_evidence['gates'].values()):
                    raise RuntimeError('Worker/requester routing, transport or accounting acceptance failed')
            # Use only booleans and counts from the ledger, not request markers,
            # authorization, result content, or account identifiers.
            checkpoints.append({'stage': 'paid_job_terminal', 'cycle': cycle,
                'time_ns': time.time_ns(), 'terminal_status': result.get('terminal_status'),
                'all_accounting_gates_pass': bool(result.get('pass')),
                'charge_count': result.get('charge_count'),
                'earning_count': result.get('earning_count')})
            stage = 'paid_job_terminal'
            write_trace()
            if not result.get('pass'):
                raise RuntimeError('Paid job or exactly-once ledger acceptance failed')
            completed += 1
            check('after_paid_job', cycle)
            if idle_seconds:
                time.sleep(idle_seconds)
            check('after_paid_idle', cycle)
        stage = 'complete'
        write_trace(final=True)
        return {'schema': 'main-computer.slh-paid-transport-differential.v1',
                'mode': mode, 'tls_io_mode': tls_io_mode, 'status': 'PASS', 'pass': True,
                'cycles_completed': completed, 'cycles_requested': cycles,
                'soak_seconds_required':soak_seconds,
                'elapsed_seconds':round(time.monotonic()-started_monotonic, 3),
                'admin_session_refreshes': admin_session_refreshes,
                'last_stage': stage, 'worker_survived': True,
                'trace_path': str(path.resolve()) if path else None,
                'scope': 'real-organization-service-real-hub-synthetic-ledger',
                **({'acceptance': acceptance_evidence} if acceptance else {})}
    except Exception as exc:
        last_error = {'stage': stage, 'exception_class': type(exc).__name__,
                      'classification': 'WORKER_LOST' if not worker.is_alive else 'PAID_OR_CONTROL_FAILURE'}
        # Keep only the HTTP status (never bearer, challenge, or raw API body).
        status_match = re.search(r'Unexpected API response (\d{3})', str(exc))
        if status_match:
            last_error['http_status'] = int(status_match.group(1))
        evidence = {}
        try:
            # Intentionally include the *full* bounded diagnostic event sequence
            # only in the on-disk trace, not stdout or API responses.
            evidence = _connection_loss_timelines(
                adapter, [worker], {worker.device_id},
                gateway_diagnostics=_fetch_gateway_diagnostics(
                    api_port, proxy.token, {worker.device_id}), relay=relay) if adapter is not None else {
                    'workers': {worker.device_id: {
                        'client_alive': bool(worker.is_alive),
                        'worker_transport_events': worker.transport_diagnostics()}},
                    'gateway_events': _fetch_gateway_diagnostics(
                        api_port, proxy.token, {worker.device_id}).get('events', [])}
        except Exception as diagnostic_error:
            evidence = {'diagnostic_error_class': type(diagnostic_error).__name__}
        write_trace(final=True, evidence=evidence)
        return {'schema': 'main-computer.slh-paid-transport-differential.v1',
                'mode': mode, 'tls_io_mode': tls_io_mode, 'status': 'FAIL', 'pass': False,
                'cycles_completed': completed,
                'admin_session_refreshes': admin_session_refreshes,
                'soak_seconds_required':soak_seconds,
                'elapsed_seconds':round(time.monotonic()-started_monotonic, 3),
                'cycles_requested': cycles,
                'last_stage': stage, 'failure': last_error,
                'trace_path': str(path.resolve()) if path else None,
                'scope': 'real-organization-service-real-hub-synthetic-ledger',
                **({'acceptance': acceptance_evidence} if acceptance and acceptance_evidence is not None else {})}
