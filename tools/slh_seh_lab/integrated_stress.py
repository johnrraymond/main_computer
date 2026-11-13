"""Bounded, reproducible real-socket SLH -> SEH -> Stable Hub stress lane.

Operates only on the disposable in-memory Hub and transient SLH state prepared
by real_paid_service.verify_real_process_paid. Does not instantiate an SLH
scheduler/gateway or claim that simulated Workers are real network Workers.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import random
import math
import secrets
import socket
import ssl
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, urlsplit

from main_computer.hub_cli_live_session import identify_hub_protocol, issue_cli_multisession_authorization
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.multisession_key_signing import private_key_to_address
from main_computer.slh.authenticator import sign_object
from tools.slh_authenticator_cli import load_identity, save_identity

from .paid_cycle import _json
from .service_lifecycle import ServiceLabFailure, _http, _ok, _await


@dataclass(frozen=True)
class StressProfile:
    workers: int = 8
    requests: int = 16
    concurrency: int = 4
    seed: int = 1729
    causal_probe: bool = False
    strict_acceptance: bool = False
    capacity_probe: bool = False

    def __post_init__(self):
        if not 2 <= self.workers <= 256:
            raise ValueError('Integrated stress requires 2..256 real network Workers')
        if not 2 <= self.requests <= 2048:
            raise ValueError('Integrated stress requires 2..2048 paid requests')
        if not 1 <= self.concurrency <= min(self.requests, 64):
            raise ValueError('Integrated stress concurrency must be 1..min(requests, 64)')


def _policy(api_port, token, changes):
    base = '/v1/admin/scheduler/policy'
    current = _ok(_http(api_port, 'GET', base, bearer=token, tls=True))
    return _ok(_http(api_port, 'PATCH', base,
                     {'expected_revision': current['revision'], 'changes': changes},
                     bearer=token, tls=True))


def _worker(*, name, root, api_port, org_token, pin, adapter, workers, execution_delay, audience):
    identity_dir = root / 'stress-keys'
    save_identity(identity_dir, name)
    private, public = load_identity(identity_dir, name)
    payload = {'selected_admin': 'lab-owner', 'device_id': name, 'public_key': public,
               'kind': 'worker', 'device_name': name, 'claimed_user': 'Lab Owner',
               'requested_role': 'worker', 'client_nonce': secrets.token_urlsafe(24)}
    proof = {**payload, 'version': 1, 'action': 'slh.device.enrollment.request',
             'audience': audience}
    pending = _ok(_http(api_port, 'POST', '/v1/enrollment/requests',
                         {**payload, 'proof': sign_object(private, proof)}, tls=True), 201)
    _ok(_http(api_port, 'POST', '/v1/enrollment/approvals',
              {'request_id': pending['request_id']}, bearer=org_token, tls=True), 201)

    def execute(offer):
        if execution_delay:
            time.sleep(execution_delay)
        data = (offer.get('work') or {}).get('input')
        value = data.get('value', '') if isinstance(data, dict) else str(data or '')
        return {'status': 'success', 'response': {'model':'mock-model','content': value}}

    from main_computer.slh.managed_client import ManagedSLHWorkerClient
    client = ManagedSLHWorkerClient(
        device_id=name, identity_dir=identity_dir, tls_fingerprint=pin,
        hub_url=f'https://127.0.0.1:{adapter.server_address[1]}', worker_id=name,
        auth_message={'type': 'worker.auth', 'device_id': name,
                      'market': {'rings': ['ring-2'], 'capabilities': ['chat.completions'],
                                 'models': ['mock-model'],
                                 'price': {'amount': '1', 'unit': 'compute_credit'},
                                 'max_concurrency': 1}}, work_executor=execute)
    client.start()
    workers.append(client)
    return client


def _terminal_records(ledger, session_ids, timeout=45):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        state = ledger.store.load()
        sessions = state.get('accepted_sessions') or {}
        if all(sessions.get(sid, {}).get('status') in ('succeeded', 'failed', 'cancelled')
               for sid in session_ids):
            return state
        time.sleep(.1)
    raise ServiceLabFailure('Integrated paid stress: terminal settlement timed out '
                            f'({len(session_ids)} accepted jobs)')


def _gate(ok, message):
    if not ok:
        raise ServiceLabFailure('Integrated stress failed: ' + message)
    return True


def _registered_worker_ids(status):
    """The Organization Service publishes workers as rows, not a mapping."""
    return {str(row['device_id']) for row in (status.get('workers') or [])}


def _worker_close_evidence(clients, device_ids):
    """Report only liveness and classified errors, never private auth data."""
    by_id = {client.device_id: client for client in clients}
    result = {}
    for did in sorted(device_ids):
        client = by_id.get(did)
        if client is None:
            result[did] = {'tracked': False}
            continue
        error = str(client.last_error or '').lower()
        if 'eof' in error or 'closed early' in error:
            kind = 'EOF'
        elif 'timeout' in error or 'timed out' in error:
            kind = 'TIMEOUT'
        elif 'reset' in error or 'broken pipe' in error:
            kind = 'CONNECTION_RESET'
        elif 'ssl' in error or 'tls' in error:
            kind = 'TLS'
        elif '1011' in error or 'keepalive' in error:
            kind = 'KEEPALIVE_OR_1011'
        elif 'close' in error:
            kind = 'CLOSED'
        elif error:
            kind = 'OTHER'
        else:
            kind = 'NONE'
        # Distinguish a real remote WebSocket close (with a standard numeric
        # code) from a local EOF without ever returning the exception text,
        # which might contain sensitive transport details.
        import re
        match = re.search(r'close_code=([1-4][0-9]{3})\b', error)
        trace = client.transport_diagnostics() if callable(getattr(client, 'transport_diagnostics', None)) else []
        result[did] = {
            'tracked': True,
            'client_alive': bool(client.is_alive),
            'client_close_reason': str(client.close_reason or '')[:48],
            'client_error_class': kind,
            'websocket_close_code': int(match.group(1)) if match else None,
            'local_adapter_port': getattr(client, 'local_adapter_port', None),
            'worker_transport_events': trace[-48:],
        }
    return result


def _connection_loss_timelines(adapter, clients, missing_ids, *, gateway_diagnostics=None, relay=None):
    """Link individual Worker and adapter observations by ephemeral local port."""
    workers = _worker_close_evidence(clients, missing_ids)
    ports = {row['local_adapter_port'] for row in workers.values()
             if isinstance(row.get('local_adapter_port'), int)}
    adapter_events = (adapter.diagnostics_for_ports(ports)
                      if callable(getattr(adapter, 'diagnostics_for_ports', None)) else [])
    result = {'workers': workers, 'adapter_events': adapter_events[-160:]}
    if isinstance(gateway_diagnostics, dict):
        result['gateway_events'] = [row for row in gateway_diagnostics.get('events', [])
                                    if isinstance(row, dict) and row.get('device_id') in missing_ids][-160:]
        result['gateway_events_retained'] = gateway_diagnostics.get('events_retained')
        result['gateway_events_total'] = gateway_diagnostics.get('total_events')
        result['slh_gateway_stream_events'] = gateway_diagnostics.get('seh_gateway_stream_events', [])
        result['slh_tunnel_lifecycle'] = gateway_diagnostics.get('seh_tunnel_lifecycle', [])
        result['slh_tunnel_connections'] = gateway_diagnostics.get('seh_tunnel_connections')
        result['slh_tunnel_failures'] = gateway_diagnostics.get('seh_tunnel_failures')
        result['slh_tunnel_last_error_class'] = gateway_diagnostics.get('seh_tunnel_last_error_class')
    if relay is not None and callable(getattr(adapter, 'relay_ports_for_worker_ports', None)):
        relay_ports=adapter.relay_ports_for_worker_ports(ports)
        if callable(getattr(relay, 'diagnostics_for_relay_ports', None)):
            result['seh_relay_stream_events'] = relay.diagnostics_for_relay_ports(relay_ports)
    from .connection_loss_attribution import summarize_loss_evidence
    result['causal_summary'] = summarize_loss_evidence(result)
    return result


def _fetch_gateway_diagnostics(api_port, org_token, device_ids):
    try:
        path = ('/v1/org/admin/marketplace/connection-diagnostics?device_ids=' +
                quote(','.join(sorted(device_ids)), safe=','))
        return _ok(_http(api_port, 'GET', path,
                         bearer=org_token, tls=True))
    except Exception as exc:
        return {'error_class': type(exc).__name__, 'events': []}


def _relay_close_codes(relay):
    with relay._lock:
        return dict(getattr(relay, 'client_close_codes', {}))


def _relay_slh_close_causes(relay):
    with relay._lock:
        return dict(getattr(relay, 'slh_close_causes', {}))


def _adapter_first_closes(adapter):
    with adapter.stats_lock:
        return dict(getattr(adapter, 'first_close_causes', {}))


def _baseline_connection_evidence(*, worker_id, status, workers, adapter, api_port, org_token, relay=None):
    """Small, secret-free witness for an original-Worker preflight failure.

    A registered but busy Worker is *not* a disconnected Worker. Audit evidence
    is fetched only when needed, using the existing authenticated org endpoint.
    """
    rows = status.get('workers') or []
    client = next((w for w in workers if w.device_id == worker_id), None)
    matching = next((r for r in rows if r.get('device_id') == worker_id), None)
    evidence = {
        'org_registered_worker_count': len(rows),
        'org_original_worker_registered': matching is not None,
        'org_original_worker_available': bool(matching and matching.get('available')),
        'org_original_worker_reservations': (matching or {}).get('reserved'),
        'org_public_count': len(status.get('sessions') or {}),
        'client_is_alive': bool(client and client.is_alive),
        'client_close_reason': str(client.close_reason or '')[:48] if client else None,
        'reconcile_last_reason': ((status.get('automatic') or {}).get('last_result') or {}).get('reason'),
    }
    if not matching or not (client and client.is_alive):
        evidence['connection_timelines'] = _connection_loss_timelines(
            adapter, workers, {worker_id}, gateway_diagnostics=_fetch_gateway_diagnostics(
                api_port, org_token, {worker_id}), relay=relay)
    return evidence


def run_integrated_stress(*, profile: StressProfile, hub_url: str, ledger,
                          root: Path, api_port: int, org_token: str,
                          lp: int, local_token: str, local, pin: str, adapter,
                          relay, proxy, workers: list, original_device: str,
                          wallet_key: str, upstream_auth: dict,
                          trace_file: Path | None = None):
    """Run while the actual child service/SEH/Hub are alive; leaves teardown to caller."""
    import json
    rng = random.Random(profile.seed)
    started = time.monotonic()
    gates = {}
    # Preserve the preliminary paid job's pre/post connection checkpoints.
    # Previously the first integrated-stress event overwrote those witnesses.
    events = []
    if trace_file is not None and Path(trace_file).exists():
        try:
            previous = json.loads(Path(trace_file).read_text(encoding='utf-8'))
            events = [r for r in previous.get('events', [])[-4:] if isinstance(r, dict)
                      and r.get('event') == 'preliminary_paid_worker_checkpoint']
        except (OSError, ValueError, TypeError):
            events = []

    def record(event, **safe_values):
        events.append({'event': event, 'elapsed_ms': int((time.monotonic()-started)*1000),
                       **safe_values})
        if trace_file is not None:
            # Save a secret-free trace before moving to the next potentially
            # blocking network phase; a timeout still leaves replay context.
            Path(trace_file).write_text(json.dumps({
                'schema': 'main-computer.slh-integrated-stress-events.v1',
                'profile': {'workers':profile.workers, 'requests':profile.requests,
                            'concurrency':profile.concurrency, 'seed':profile.seed},
                'events': events}, sort_keys=True, indent=2) + '\n', encoding='utf-8')

    def slots():
        return set(proxy.status()['sessions'])

    initial_bytes = relay.bytes_forwarded
    if profile.workers > relay.max_clients:
        raise ServiceLabFailure(f'Integrated stress requests {profile.workers} '
                                f'Workers but one SEH supports only {relay.max_clients} '
                                'client streams; a multi-SEH topology is needed')
    other_names = [f'lab-stress-{i:04d}' for i in range(1, profile.workers)]
    # Availability can be false for a reserved (busy) Worker. Only an absent
    # registration or a dead client establishes preflight connection loss.
    baseline = proxy.status()
    original_client = next((w for w in workers if w.device_id == original_device), None)
    baseline_ids = _registered_worker_ids(baseline)
    if original_device not in baseline_ids or not (original_client and original_client.is_alive):
        evidence = _baseline_connection_evidence(
            worker_id=original_device, status=baseline, workers=workers,
            adapter=adapter, api_port=api_port, org_token=org_token, relay=relay)
        record('baseline_worker_missing', **evidence)
        # The trace contains the full bounded audit. Avoid truncated JSON in
        # the CLI error and never include raw exception strings or credentials.
        raise ServiceLabFailure('Integrated stress preflight lost original Worker '
                                'before fleet startup; stage=after_preliminary_paid_job; '
                                'state=' + json.dumps({k:v for k,v in evidence.items()
                                if k != 'connection_timelines'}, sort_keys=True)
                                + '; connection_timeline_saved=' + str(trace_file is not None))
    record('workers_starting', target=profile.workers,
           baseline_workers=len(baseline.get('workers') or {}),
           baseline_public=len(baseline.get('sessions') or {}))
    for i, name in enumerate(other_names, 1):
        try:
            _worker(name=name, root=root, api_port=api_port,
                    org_token=org_token, pin=pin, adapter=adapter, workers=workers,
                    execution_delay=0.04,
                    audience='slh://' + local.organization_controller.authority.service_fingerprint())
        except (OSError, TimeoutError, ValueError, RuntimeError, ssl.SSLError) as exc:
            record('worker_connect_failed', worker_index=i + 1,
                   error_type=type(exc).__name__)
            raise ServiceLabFailure('Integrated stress Worker startup failed '
                                    f'at {i+1}/{profile.workers}; '
                                    f'error={type(exc).__name__}') from exc
        if i % 8 == 0 or i == len(other_names):
            record('worker_connection_progress', started=i + 1,
                   target=profile.workers)
    def workers_ready():
        status = proxy.status()
        return _registered_worker_ids(status) == {original_device, *other_names}
    _await(workers_ready, 'integrated stress Worker registration',
           timeout=max(20, profile.workers * 0.8))
    gates['real_network_workers_connected'] = _gate(
        _registered_worker_ids(proxy.status()) == {original_device, *other_names}, 'connected Worker identities')
    record('workers_connected', workers=profile.workers)

    all_worker_ids = {original_device, *other_names}

    def assert_fleet(stage: str):
        # Do not infer the cause of a loss from the phase where it was first
        # noticed. A checkpoint must precede and follow each candidate cause.
        current = _registered_worker_ids(proxy.status())
        missing = sorted(all_worker_ids - current)
        extra = sorted(current - all_worker_ids)
        evidence = {
            'stage': stage,
            'registered_count': len(current),
            'missing_workers': missing,
            'unexpected_workers': extra,
            'relay_close_codes': _relay_close_codes(relay),
            'relay_slh_close_causes': _relay_slh_close_causes(relay),
            'adapter_first_close_causes': _adapter_first_closes(adapter),
            'dead_worker_clients': sorted(w.device_id for w in workers
                                           if w.device_id in all_worker_ids and not w.is_alive),
        }
        record('causal_fleet_checkpoint', **evidence)
        if missing or extra or (profile.strict_acceptance and evidence['dead_worker_clients']):
            details = _connection_loss_timelines(adapter, workers, missing,
                                                 gateway_diagnostics=_fetch_gateway_diagnostics(api_port, org_token, missing), relay=relay)
            record('causal_first_worker_loss', **evidence, connection_timelines=details)
            raise ServiceLabFailure('Integrated causal probe first observed Worker loss at ' +
                                    stage + '; evidence=' + json.dumps({**evidence,
                                    'connection_timelines': details}, sort_keys=True)[:5000])
        return evidence

    # The existing paid lane leaves the policy at zero public capacity. Restoring
    # 100% here must be handled by the autonomous controller, never manual reconcile.
    attempts = proxy.status()['automatic']['attempts']
    _policy(api_port, org_token, {'max_public_percent': 100, 'min_private_idle_percent': 0})
    _await(lambda: len(slots()) == profile.workers,
           'integrated stress automatic public Worker advertisements',
           timeout=max(30, profile.workers * 1.5))
    gates['automatic_all_workers_advertised'] = _gate(
        proxy.status()['automatic']['attempts'] > attempts, 'automatic reconcile not observed')
    gates['public_sessions_unique'] = _gate(
        len(slots()) == profile.workers, 'public session count or duplicate')
    record('public_capacity_ready', sessions=len(slots()))
    if profile.capacity_probe:
        # The 65th client must receive a bounded BUSY close (1013), without
        # terminating the existing 64 authenticated Worker streams or tunnel.
        from websockets.sync.client import connect as ws_connect
        from websockets.exceptions import ConnectionClosed
        _gate(profile.workers == relay.max_clients,
              'Capacity probe requires exactly the relay client limit')
        _await(lambda: len(relay._clients) + len(relay._lab_clients) == relay.max_clients,
               'relay capacity probe fleet attached', timeout=10)
        capacity_close_code = None
        capacity_error = None
        probe = None
        try:
            probe = ws_connect(f'ws://127.0.0.1:{relay.port}/v1/seh/client',
                               open_timeout=5, close_timeout=1, ping_interval=None)
            try:
                probe.recv(timeout=5)
            except ConnectionClosed as exc:
                capacity_close_code = getattr(getattr(exc, 'rcvd', None), 'code', None)
            except (OSError, TimeoutError) as exc:
                capacity_error = type(exc).__name__
        except (OSError, TimeoutError, ConnectionError) as exc:
            capacity_error = type(exc).__name__
        finally:
            if probe is not None:
                try:
                    probe.close()
                except (OSError, ConnectionError, RuntimeError):
                    pass
        record('capacity_65th_probe', close_code=capacity_close_code,
               error_class=capacity_error)
        gates['capacity_65th_rejected_without_disruption'] = _gate(
            capacity_close_code == 1013,
            f'65th SEH client must receive 1013; got {capacity_close_code} '
            f'error={capacity_error}')
        assert_fleet('after_capacity_probe')
        gates['capacity_existing_workers_retained'] = True
    if profile.causal_probe or profile.strict_acceptance:
        # CONTROL: exactly the same real Organization Service, SEH relay and
        # Worker fleet, but without any integrated paid-job traffic yet.
        # If this loses Workers, paid work is NOT a prerequisite for the bug.
        assert_fleet('before_prepaid_reserve_change')
        _policy(api_port, org_token, {'max_public_percent': 50,
                                      'min_private_idle_percent': 50})
        _await(lambda: len(slots()) <= profile.workers // 2,
               'prepaid reserve-only drain', timeout=40)
        assert_fleet('after_prepaid_reserve_change')
        _policy(api_port, org_token, {'max_public_percent': 100,
                                      'min_private_idle_percent': 0})
        _await(lambda: len(slots()) == profile.workers,
               'prepaid reserve-only recovery', timeout=max(40, profile.workers))
        assert_fleet('after_prepaid_reserve_recovery')
        record('prepaid_control_complete', workers=profile.workers)

    hub_identity = identify_hub_protocol(hub_url)
    _gate(hub_identity.get('service') == 'main_computer.stable_hub', 'Hub identity mismatch')
    requester_key = '0x' + secrets.token_hex(32)
    requester_address = private_key_to_address(requester_key)
    requester_account = wallet_account_id(requester_address)
    auth = issue_cli_multisession_authorization(
        hub_url, wallet=SimpleNamespace(private_key=requester_key, address=requester_address),
        stable_identity=hub_identity)
    requester_proof = {'kind': 'multisession_key',
                       'multisession_key_id': auth['multisession_key_id']}
    ledger.fund_account(account_id=requester_account, wallet_address=requester_address,
                        credits=str(profile.requests * 3), replace=True)

    # Controlled concurrent batches are intentionally permitted to reject
    # excess requests under contention. Every ACCEPTED job must settle exactly
    # once. Rejections are observed, not silently classified as successful jobs.
    specs = [(f'slh-stress-{profile.seed}-{i}-{secrets.token_hex(5)}',
              f'integrated-{profile.seed}-{i}-{rng.getrandbits(64):016x}')
             for i in range(profile.requests)]
    def submit(spec):
        rid, marker = spec
        body = {'request_id': rid, 'multisession_authorization': requester_proof,
                'work': {'ring':'ring-2',
                         'max_price': {'amount':'2', 'unit':'compute_credit'},
                         'capabilities':['chat.completions'], 'model':'mock-model',
                         'input': {'kind':'chat', 'value': marker}}}
        began = time.monotonic()
        try:
            response = _json(hub_url.rstrip('/')+'/api/hub/v1/work/requests', body, timeout=25)
            return {'id': rid, 'marker': marker, 'body': body, 'reply': response,
                    'latency_ms': round((time.monotonic()-began)*1000, 2)}
        except Exception as exc:
            # Count Hub HTTP refusal separately from transport failure. Do not
            # put exception bodies (which could contain authorization) in reports.
            import re
            status = re.search(r'Stable Hub HTTP (\d{3})', str(exc))
            return {'id': rid, 'marker': marker, 'body': body,
                    'error': 'HTTP_' + status.group(1) if status else type(exc).__name__,
                    'latency_ms': round((time.monotonic()-began)*1000, 2)}
    if profile.causal_probe or profile.strict_acceptance:
        assert_fleet('immediately_before_paid_requests')
    # R5: force protocol refusals to compete with real paid-job admission.
    # Requests carry no credentials and no body; size refusal must precede
    # authorization, scheduling, ledger holds, and model execution.
    def malicious_http():
        address = urlsplit(hub_url)
        try:
            with socket.create_connection((address.hostname, address.port), timeout=4) as attacker:
                attacker.settimeout(5)
                attacker.sendall((
                    'POST /api/hub/v1/work/requests HTTP/1.1\r\n'
                    f'Host: {address.hostname}\r\n'
                    'Content-Length: 2097153\r\n'
                    'Connection: close\r\n\r\n').encode('ascii'))
                line = attacker.recv(256).split(b'\r\n', 1)[0]
            parts = line.split()
            return int(parts[1]) if len(parts) >= 2 else None
        except (OSError, ValueError, IndexError):
            return None

    def malicious_relay_client():
        # This stream is not enrolled or authenticated at SLH. Even if it gets
        # as far as the relay, its 1 MiB+1 byte payload is refused by the
        # stream frame gate. Do not inject across the primary SLH tunnel.
        from websockets.sync.client import connect as ws_connect
        from websockets.exceptions import ConnectionClosed
        from main_computer.seh.relay import MAX_FRAME
        try:
            with ws_connect(f'ws://127.0.0.1:{relay.port}/v1/seh/client',
                            open_timeout=5,close_timeout=2) as attacker:
                attacker.send(b'M'*(MAX_FRAME+1))
                try:
                    attacker.recv(timeout=5)
                except ConnectionClosed:
                    return True
            return False
        except (OSError, ConnectionError, TimeoutError):
            return False

    record('paid_requests_starting', count=profile.requests, concurrency=profile.concurrency)
    with ThreadPoolExecutor(max_workers=profile.concurrency+2) as pool:
        refused_http = pool.submit(malicious_http)
        # A 64-Worker saturation probe has no spare SEH client slot by design;
        # do not classify its expected capacity refusal as a frame-limit proof.
        test_ws_limit = profile.workers < 64
        refused_ws = pool.submit(malicious_relay_client) if test_ws_limit else None
        future_list = [pool.submit(submit, spec) for spec in specs]
        responses = [f.result() for f in as_completed(future_list)]
        hostile_http_status = refused_http.result()
        hostile_ws_closed = refused_ws.result() if refused_ws is not None else None
    record('concurrent_hostile_protocol_probes', http_status=hostile_http_status,
           websocket_stream_refused=hostile_ws_closed,
           websocket_probe_scope='tested' if test_ws_limit else 'capacity_probe_excluded')
    gates['mixed_traffic_http_size_rejection'] = _gate(
        hostile_http_status == 413, 'malicious HTTP declaration was not rejected 413')
    if test_ws_limit:
        gates['mixed_traffic_oversize_relay_stream_refused'] = _gate(
            hostile_ws_closed is True, 'oversized SEH client stream not closed')
    # The accounting checks below independently assert zero extra charges,
    # one earning per accepted paid request, and idempotent replay.
    accepted = [r for r in responses if (r.get('reply') or {}).get('accepted') is True]
    rejected = [r for r in responses if r not in accepted]
    # Transport failures are not legitimate marketplace refusals. Preserve
    # that distinction, even if other requests succeeded and settled.
    transport_errors = [r for r in rejected if r.get('error') and
                        not r['error'].startswith('HTTP_')]
    record('concurrent_paid_requests', submitted=len(responses), accepted=len(accepted),
           rejected=len(rejected), transport_failures=len(transport_errors),
           concurrency=profile.concurrency)
    _gate(not transport_errors, 'Hub request transport failures: ' +
          repr({k: sum(1 for r in transport_errors if r['error']==k)
                for k in sorted(set(r['error'] for r in transport_errors))}))
    _gate(len(accepted) >= max(2, math.ceil(profile.requests / 2)),
          'paid acceptance below 50% under bounded load')
    session_ids = [r['reply']['session_id'] for r in accepted]
    gates['at_least_half_paid_requests_accepted'] = True
    if profile.strict_acceptance:
        gates['all_paid_requests_accepted'] = _gate(
            len(accepted) == profile.requests and not rejected,
            f'Laboratory exit requires 100% accepted paid requests; accepted={len(accepted)} '
            f'rejected={len(rejected)} requested={profile.requests}')
    gates['accepted_session_ids_unique'] = _gate(
        len(session_ids) == len(set(session_ids)), 'duplicate session IDs')
    state = _terminal_records(ledger, session_ids, timeout=max(45, profile.requests * 1.5))
    terminals = state.get('accepted_sessions') or {}
    charges = list((state.get('payout_charges') or {}).values())
    earnings = list((state.get('worker_earnings') or {}).values())
    session_set = set(session_ids)
    session_charges = [row for row in charges if row.get('session_id') in session_set]
    session_earnings = [row for row in earnings if row.get('session_id') in session_set]
    counts_charges = {sid:sum(x.get('session_id') == sid for x in session_charges)
                      for sid in session_ids}
    counts_earnings = {sid:sum(x.get('session_id') == sid for x in session_earnings)
                       for sid in session_ids}
    succeeded = [r for r in accepted if terminals[r['reply']['session_id']]['status']=='succeeded']
    failed = len(accepted) - len(succeeded)
    gates['completed_paid_work'] = _gate(bool(succeeded), 'no paid jobs completed successfully')
    if profile.strict_acceptance:
        gates['all_paid_requests_succeeded'] = _gate(
            len(succeeded) == profile.requests and failed == 0,
            f'Laboratory exit requires 100% successful paid jobs; '
            f'succeeded={len(succeeded)} failed={failed}')
    gates['exactly_once_charges'] = _gate(
        all(counts_charges[sid] == (1 if terminals[sid]['status']=='succeeded' else 0)
            for sid in session_ids), 'unexpected charge count on accepted sessions')
    gates['exactly_once_earnings'] = _gate(
        all(counts_earnings[sid] == (1 if terminals[sid]['status']=='succeeded' else 0)
            for sid in session_ids), 'unexpected earning count on accepted sessions')
    by_request = {r['id']: r for r in accepted}
    by_session = {r['reply']['session_id']:r for r in accepted}
    for earning in session_earnings:
        sample = by_session[earning['session_id']]
        _gate(earning.get('request_id') == sample['id'] and
              ((terminals[earning['session_id']].get('worker_result') or {})
                  .get('result',{}).get('response',{}).get('content') == sample['marker']),
              'paid Worker returned wrong marker')
    gates['each_paid_result_corresponds_to_request'] = True
    gates['actual_seh_traffic_during_stress'] = _gate(
        relay.bytes_forwarded > initial_bytes, 'no SEH traffic during stress')

    # Replay a deterministic subset of accepted requests after terminal. This
    # must return the original session and must not add payouts.
    sample_replay = sorted(succeeded, key=lambda x:x['id'])[:min(5, len(succeeded))]
    for r in sample_replay:
        replay = _json(hub_url.rstrip('/')+'/api/hub/v1/work/requests', r['body'], timeout=12)
        _gate(replay.get('session_id') == r['reply']['session_id'], 'idempotent replay changed session')
    after_replay = ledger.store.load()
    gates['retries_no_duplicate_ledger_entries'] = _gate(
        sum(x.get('session_id') in session_set for x in (after_replay.get('payout_charges') or {}).values()) == len(session_charges)
        and sum(x.get('session_id') in session_set for x in (after_replay.get('worker_earnings') or {}).values()) == len(session_earnings),
        'replayed request changed the ledger')
    # Wallet-authorized claim and pending batch for every successful earning.
    # NEVER let a Worker self-finalize settlement; independent proof is required.
    payout_ids = [e['earning_id'] for e in session_earnings]
    for sid in session_ids:
        payout = terminals[sid].get('payout') or {}
        if terminals[sid]['status'] == 'succeeded':
            _gate(payout.get('hold_status') == 'charged', 'unsettled successful payout hold')
    gates['all_successful_holds_charged'] = True
    if payout_ids:
        claim = _json(hub_url.rstrip('/') + '/api/hub/v1/workers/' +
                      quote(wallet_account_id(private_key_to_address(wallet_key))) + '/payout/claim',
                      {'multisession_authorization':upstream_auth,
                       'earning_ids': payout_ids}, timeout=20)
        _gate(claim.get('ok') is True and
              set((claim.get('claim') or {}).get('earning_ids') or ()) == set(payout_ids),
              'wallet payout claim missing stress earnings')
        settlement = _json(hub_url.rstrip('/') + '/api/hub/v1/workers/' +
                           quote(wallet_account_id(private_key_to_address(wallet_key))) + '/payout/settlements',
                           {'multisession_authorization':upstream_auth,
                            'claim_ids':[claim['claim']['claim_id']],
                            'idempotency_key':'stress-batch-' + str(profile.seed) + '-' +
                            secrets.token_hex(5)}, timeout=20)
        _gate(settlement.get('ok') is True and
              (settlement.get('settlement') or {}).get('status') == 'batched',
              'wallet-authorized pending payout batch failed')
    gates['wallet_authorized_stress_pending_batch'] = True
    record('paid_ledger_verified', succeeded=len(succeeded), failed=failed,
           replayed=len(sample_replay), batched_earnings=len(payout_ids),
           registered_workers=len(_registered_worker_ids(proxy.status())),
           relay_slh_close_causes=_relay_slh_close_causes(relay))

    if profile.causal_probe or profile.strict_acceptance:
        # ESSENTIAL: the former test only counted Workers after changing reserve.
        # Without this checkpoint it was impossible to tell whether job traffic
        # had already disconnected Workers.
        assert_fleet('paid_batched_before_reserve_change')

    # Enforce a more restrictive private reserve with Workers still attached.
    private_reserve = 50
    _policy(api_port, org_token, {'max_public_percent': 50,
                                  'min_private_idle_percent': private_reserve})
    limit = profile.workers // 2
    _await(lambda: len(slots()) <= limit, 'integrated stress reserve drain', timeout=40)
    gates['reserve_limits_public_sessions'] = _gate(
        len(slots()) <= limit, 'private reserve exceeded')
    if profile.causal_probe or profile.strict_acceptance:
        assert_fleet('paid_batched_after_reserve_change')
    record('reserve_changed', max_public=limit, live_sessions=len(slots()),
           registered_workers=len(_registered_worker_ids(proxy.status())),
           relay_slh_close_causes=_relay_slh_close_causes(relay))

    # A private reserve policy must withdraw *advertisements*, not the actual
    # connected Workers. Assert that independently, before intentional churn.
    # This pins the phase where the first unexpected Worker loss occurs.
    all_worker_ids = {original_device, *other_names}
    def complete_fleet():
        return _registered_worker_ids(proxy.status()) == all_worker_ids
    try:
        _await(complete_fleet, 'integrated stress Workers retained after reserve change', timeout=5)
    except ServiceLabFailure as exc:
        observed = _registered_worker_ids(proxy.status())
        close_details = _connection_loss_timelines(adapter, workers, all_worker_ids - observed,
                                                    gateway_diagnostics=_fetch_gateway_diagnostics(api_port, org_token, all_worker_ids - observed), relay=relay)
        record('reserve_worker_loss', missing_workers=sorted(all_worker_ids-observed),
               connection_timelines=close_details, relay_close_codes=_relay_close_codes(relay),
               relay_slh_close_causes=_relay_slh_close_causes(relay),
               adapter_first_close_causes=_adapter_first_closes(adapter))
        raise ServiceLabFailure(str(exc) + '; evidence=' + json.dumps({
            'missing_workers': sorted(all_worker_ids - observed),
            'connection_timelines': close_details,
            'relay_close_codes': _relay_close_codes(relay),
            'relay_slh_close_causes': _relay_slh_close_causes(relay),
            'adapter_first_close_causes': _adapter_first_closes(adapter)}, sort_keys=True)) from exc
    gates['reserve_does_not_disconnect_workers'] = True

    # Disconnect some Workers while public offers are active. Every departure
    # must be removed from the scheduler and public advertisements.
    leaving = other_names[::max(1, len(other_names)//max(1, profile.workers//4))]
    leaving = leaving[:max(1, profile.workers//4)]
    by_device = {w.device_id:w for w in workers}
    record('worker_churn_starting', count=len(leaving))
    for name in leaving:
        record('worker_churn_close_entered', worker=name)
        by_device[name].close(reason='integrated_stress_churn')
        record('worker_churn_close_returned', worker=name)
    # Marketplace status exposes workers as a list of rows, NOT a mapping.
    # Comparing names to that list always returned True and silently bypassed
    # the actual worker-unregistration requirement.
    def departed_workers_unregistered():
        rows = proxy.status().get('workers') or []
        present = _registered_worker_ids({'workers': rows})
        return not (set(leaving) & present)
    _await(departed_workers_unregistered,
           'integrated stress Worker disconnection', timeout=30)
    _await(lambda: not(slots() & set(leaving)),
           'integrated stress upstream Worker drain', timeout=40)
    gates['worker_churn_drains_invalid_sessions'] = True
    expected_remaining = profile.workers-len(leaving)
    # A single per-Worker failure must not drop the shared SEH tunnel or
    # disconnect otherwise healthy Workers. This was previously invisible
    # because the test checked only that the selected identities disappeared.
    expected_ids = ({original_device, *other_names} - set(leaving))
    latest_survivors = {}
    def surviving_workers():
        status = proxy.status()
        rows = status.get('workers') or []
        actual_ids = _registered_worker_ids({'workers': rows})
        with relay._lock:
            tunnel_ready = relay._slh is not None
            relay_clients = len(relay._clients) + len(relay._lab_clients)
            pending_closes = len(relay._closing_clients)
            closed_streams = relay.closed_streams
        latest_survivors.clear()
        latest_survivors.update({
            'expected_count':len(expected_ids), 'actual_count':len(actual_ids),
            'missing_workers':sorted(expected_ids - actual_ids),
            'unexpected_workers':sorted(actual_ids - expected_ids),
            'tunnel_ready':tunnel_ready, 'relay_clients':relay_clients,
            'pending_relay_closes':pending_closes,
            'relay_closed_streams':closed_streams,
            'relay_close_codes': _relay_close_codes(relay),
            'relay_slh_close_causes': _relay_slh_close_causes(relay),
            'missing_worker_clients': _connection_loss_timelines(
                adapter, workers, expected_ids - actual_ids,
                gateway_diagnostics=_fetch_gateway_diagnostics(api_port, org_token, expected_ids - actual_ids), relay=relay),
        })
        return actual_ids == expected_ids and tunnel_ready
    try:
        _await(surviving_workers, 'integrated stress surviving Workers and SEH tunnel',
               timeout=25)
    except ServiceLabFailure as exc:
        record('surviving_worker_check_failed', **latest_survivors)
        raise ServiceLabFailure(str(exc) + '; state=' +
                                json.dumps(latest_survivors, sort_keys=True)) from exc
    gates['tunnel_and_other_workers_survive_churn'] = True
    record('worker_churn', removed=len(leaving), remaining=expected_remaining)
    # The relay can still be closing client streams asynchronously after the
    # local scheduler has removed Workers. Wait for actual SEH slot capacity
    # before reopening sessions with the same approved identities.
    def relay_capacity_restored():
        with relay._lock:
            live_clients = len(relay._clients) + len(relay._lab_clients)
        return relay.tunnel_ready and live_clients <= expected_remaining
    try:
        _await(relay_capacity_restored, 'integrated stress SEH drain convergence', timeout=20)
    except ServiceLabFailure as exc:
        # A scheduler deregistration isn't proof that the SEH has released
        # its actual socket. Keep evidence for a stuck stream before teardown.
        with relay._lock:
            live_relay_clients = len(relay._clients) + len(relay._lab_clients)
            pending_closes = len(relay._closing_clients)
            recent_streams = [dict(row) for row in list(relay._stream_events)[-64:]]
        state = {'expected_max_clients': expected_remaining,
                 'actual_relay_clients': live_relay_clients,
                 'pending_closes': pending_closes,
                 'tunnel_ready': relay.tunnel_ready,
                 'relay_close_codes': _relay_close_codes(relay),
                 'relay_slh_close_causes': _relay_slh_close_causes(relay),
                 'recent_relay_events': recent_streams}
        record('seh_drain_failed', **state)
        raise ServiceLabFailure(str(exc) + '; evidence saved in event trace; '
                                f'live_relay_clients={live_relay_clients}, '
                                f'expected_max={expected_remaining}') from exc
    gates['seh_capacity_restored_before_reconnect'] = True
    record('seh_capacity_restored')

    # Reconnect the SAME approved identities without repeating enrollment.
    # Bound the fan-out so a slow TLS handshake cannot serially consume the
    # entire stress budget across 16+ workers. Report completed progress.
    from main_computer.slh.managed_client import ManagedSLHWorkerClient
    def reconnect(name):
        def echo(offer):
            data = (offer.get('work') or {}).get('input')
            value = data.get('value','') if isinstance(data,dict) else str(data or '')
            return {'status':'success', 'response': {'model':'mock-model','content':value}}
        restored = ManagedSLHWorkerClient(
            device_id=name, identity_dir=root/'stress-keys', tls_fingerprint=pin,
            hub_url=f'https://127.0.0.1:{adapter.server_address[1]}', worker_id=name,
            auth_message={'type':'worker.auth', 'device_id':name,
                          'market':{'rings':['ring-2'], 'capabilities':['chat.completions'],
                                    'models':['mock-model'],
                                    'price':{'amount':'1','unit':'compute_credit'},
                                    'max_concurrency':1}}, work_executor=echo)
        # Register for teardown before start; failed handshakes still own a
        # socket which must be closed on failure.
        workers.append(restored)
        try:
            restored.start()
        except BaseException:
            restored.close(reason='stress_reconnection_failed')
            raise
        return name
    record('worker_reconnect_starting', count=len(leaving), concurrency=min(4,len(leaving)))
    with ThreadPoolExecutor(max_workers=min(4,len(leaving))) as pool:
        pending = {pool.submit(reconnect, name): name for name in leaving}
        completed = 0
        for future in as_completed(pending):
            try:
                future.result()
            except (RuntimeError, OSError, TimeoutError, ValueError, ssl.SSLError) as exc:
                record('worker_reconnect_failed', completed=completed,
                       error_type=type(exc).__name__)
                raise ServiceLabFailure('Integrated stress Worker reconnect failed '
                                        f'after {completed}/{len(leaving)}: '
                                        + type(exc).__name__) from exc
            completed += 1
            if completed % 4 == 0 or completed == len(leaving):
                record('worker_reconnect_progress', completed=completed, target=len(leaving))
    _await(lambda: _registered_worker_ids(proxy.status()) == all_worker_ids,
           'integrated stress reconnect Workers', timeout=45)
    gates['worker_reconnections_restore_registered_identity'] = True
    record('workers_reconnected', workers=len(proxy.status()['workers']))

    # Policy disable is a hard boundary: no new offered sessions, independent
    # of the organization's TLS listener continuing to run.
    settings = proxy._call('GET', 'config')['configuration']
    proxy._call('PATCH', 'config', {'expected_revision': settings['revision'],
                                 'changes': {'enabled': False}})
    _await(lambda: not slots(), 'integrated stress marketplace disable', timeout=45)
    gates['disabled_marketplace_has_no_public_sessions'] = True
    record('marketplace_disabled')

    settings = proxy._call('GET', 'config')['configuration']
    proxy._call('PATCH', 'config', {'expected_revision': settings['revision'],
                                 'changes': {'enabled': True}})
    _await(lambda: len(slots()) == limit,
           'integrated stress autonomous recovery after reenable', timeout=45)
    gates['marketplace_reenable_converges'] = True
    record('marketplace_reenabled', live_sessions=len(slots()))

    latencies = sorted(r['latency_ms'] for r in responses)
    def percentile(p):
        return round(latencies[min(len(latencies)-1, int((len(latencies)-1)*p))],2)
    record('complete', result='PASS')
    return {'schema':'main-computer.slh-integrated-network-stress.v1',
            'status':'PASS', 'pass':all(gates.values()), 'gates':gates,
            'profile': {'workers':profile.workers, 'paid_requests':profile.requests,
                        'concurrency':profile.concurrency,'seed':profile.seed,
                        'strict_acceptance':profile.strict_acceptance,
                        'capacity_probe':profile.capacity_probe},
            'paid': {'submitted':len(responses), 'accepted':len(accepted),
                     'rejected':len(rejected), 'succeeded':len(succeeded), 'failed':failed,
                     'rejection_types':{k:sum(1 for r in rejected if
                         (r.get('error') or str((r.get('reply') or {}).get('reason') or 'refused'))==k)
                         for k in set(r.get('error') or str((r.get('reply') or {}).get('reason') or 'refused')
                                      for r in rejected)},
                     'latency_ms': {'p50':percentile(.5), 'p95':percentile(.95),
                                    'max':max(latencies)},
                     'synthetic_funding':True, 'ledger_only':True},
            'elapsed_seconds':round(time.monotonic()-started,2),
            'events':events,
            'limitations': {'public_wss':'NOT_TESTED', 'external_bridge':'NOT_TESTED',
                            'hub':'DISPOSABLE_LOCAL_HTTP', 'worker_compute':'ECHO_EXECUTOR',
                            'seh_transport':'REAL_LOCAL_RELAY'}}
