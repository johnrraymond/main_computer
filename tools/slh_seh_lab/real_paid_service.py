"""Paid Stable Hub acceptance through the real spawned SLH Organization Service.

The lab does not instantiate SLHManagedGateway or SLHMarketplaceProxy. It
configures the localhost service, talks to the Organization Service over TLS,
uses a real relay and Worker socket, and submits paid jobs to the real Hub.
Everything is disposable, loopback-bound, and funded only in the lab ledger.
"""
from __future__ import annotations

from pathlib import Path
import json
import secrets
import tempfile
import time
from types import SimpleNamespace

from main_computer.slh.authenticator import generate_identity, sign_object
from main_computer.slh.managed_client import ManagedSLHWorkerClient
from main_computer.slh.seh_autoconnect import SEHConnectionSettings, write_seh_settings
from main_computer.seh.relay import SEHRelay
from main_computer.slh.seh_transport import SEHLocalClientAdapter
from main_computer.hub_cli_live_session import identify_hub_protocol, issue_cli_multisession_authorization
from main_computer.multisession_key_signing import private_key_to_address
from tools.slh_authenticator_cli import save_identity, load_identity

from .paid_cycle import verify_paid_cycle
from .lab_stage_diagnostics import stage as lab_stage, record as lab_record
from .integrated_stress import StressProfile, run_integrated_stress
from .service_lifecycle import ServiceLabFailure, _local, _http, _ok, _await, _ports, _login, _fingerprint


class RemoteMarketplace:
    """Lab driver of the real Organization Service; no in-process scheduler."""
    def __init__(self, port, token, upstream_auth):
        self.port, self.token, self.upstream_auth = port, token, upstream_auth

    def _call(self, method, route, body=None):
        return _ok(_http(self.port, method, '/v1/org/admin/marketplace/' + route,
                         body, bearer=self.token, tls=True))

    @property
    def sessions(self):
        rows = self._call('GET', 'status')['sessions']
        return {did: SimpleNamespace(accepted=row,
                    auth_message={'multisession_authorization': self.upstream_auth})
                for did, row in rows.items()}

    def status(self):
        return self._call('GET', 'status')

    def configure(self, *, hub_url, interval_seconds=1, enabled=True):
        current = self._call('GET', 'config')['configuration']
        return self._call('PATCH', 'config', {'expected_revision': current['revision'],
                    'changes': {'hub_url': hub_url, 'interval_seconds': interval_seconds,
                                'enabled': enabled}})

    def authorize(self, auth):
        return self._call('POST', 'authorization', {'multisession_authorization': auth})

    def reconcile(self, *, hub_url=None, auth=None):
        # Retained for diagnostics; paid acceptance must not call it.
        return self._call('POST', 'reconcile', {})


def _await_policy_transition(proxy, device: str, expected_public: bool, *,
                             earlier_attempts: int, decision: int, timeout: float = 25.0):
    """Observe an actual post-update reconciliation, not a stale slot snapshot.

    Use a bounded time budget for the real WebSocket/Hub lifecycle. On failure,
    report only the existing secret-free Organization Service status so an
    intermittent upstream close/reopen failure has an actionable diagnosis.
    """
    deadline = time.monotonic() + timeout
    last_status = None
    last_exception = None
    while time.monotonic() < deadline:
        try:
            status = proxy.status()
            last_status = status
            automatic = status.get('automatic') or {}
            if (automatic.get('attempts', 0) > earlier_attempts
                    and (device in (status.get('sessions') or {})) == expected_public):
                return status
        except (OSError, TimeoutError, ValueError, ServiceLabFailure) as exc:
            last_exception = type(exc).__name__
        time.sleep(0.1)
    auto = (last_status or {}).get('automatic') or {}
    recent = auto.get('recent_decisions') or []
    diagnostics = {
        'decision': decision,
        'expected_public': expected_public,
        'observed_public': device in ((last_status or {}).get('sessions') or {}),
        'attempts_before': earlier_attempts,
        'attempts_after': auto.get('attempts'),
        'last_attempt': auto.get('last_attempt'),
        'last_success': auto.get('last_success'),
        'last_error': auto.get('last_error'),
        'consecutive_failures': auto.get('consecutive_failures'),
        'last_result': auto.get('last_result'),
        'recent_decisions': recent[-3:],
        'status_request_error': last_exception,
    }
    # Status is already redacted by the Organization API. Do not include
    # authorization, secrets, complete worker auth payloads, or local keys.
    raise ServiceLabFailure('Timed out: automatic real-process policy decision '
                            + str(decision) + '; state=' + json.dumps(diagnostics, sort_keys=True))


def _record_preliminary_worker_checkpoint(*, trace_file, stage, worker, proxy,
                                          api_port, org_token, adapter, relay=None):
    """Observe baseline connection before/after paid job, not only afterward."""
    from .integrated_stress import _baseline_connection_evidence
    status = proxy.status()
    evidence = _baseline_connection_evidence(
        worker_id=worker.device_id, status=status, workers=[worker],
        adapter=adapter, api_port=api_port, org_token=org_token, relay=relay)
    # Capture the original client's diagnostic timelines even if registered,
    # so the two-stage trace has comparable observations.
    if 'connection_timelines' not in evidence:
        evidence['connection_timelines'] = {'workers': {
            worker.device_id: {'worker_transport_events': worker.transport_diagnostics()[-12:]
                               if callable(getattr(worker, 'transport_diagnostics', None)) else []}}}
    if trace_file is not None:
        path = Path(trace_file)
        row = {'event': 'preliminary_paid_worker_checkpoint', 'stage': stage,
               'worker': worker.device_id, 'unix_ns': time.time_ns(), **evidence}
        previous = []
        if path.exists():
            try:
                previous = json.loads(path.read_text(encoding='utf-8')).get('events', [])
            except (OSError, ValueError, TypeError):
                previous = []
        path.write_text(json.dumps({'schema': 'main-computer.slh-preliminary-paid-checkpoints.v1',
                        'events': [*previous[-3:], row]}, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    return evidence


def verify_real_process_paid(*, hub_url: str, payout_ledger,
                             lab_mode: str = 'in-memory-stable-hub',
                             stress_profile: StressProfile | None = None,
                             stress_trace_file: Path | None = None,
                             diagnostic_mode: str | None = None,
                             diagnostic_cycles: int = 0,
                             diagnostic_idle_seconds: float = 0,
                             diagnostic_soak_seconds: int = 0,
                             diagnostic_trace_file: Path | None = None,
                             diagnostic_tls_io_mode: str = 'serialized',
                             worker_requester_acceptance: bool = False,
                             pinky_integrated: bool = False, reserve_wallet: str | None = None,
                             paid_trace_file: Path | None = None,
                             credit_recovery_acceptance: bool = False,
                             hub_session_directory=None) -> dict:
    """One paid request through Local -> org process -> real SEH -> Worker.

    No production state, Cloudflare, mainnet bridge, or external hub access.
    """
    if credit_recovery_acceptance and (pinky_integrated or stress_profile is not None or diagnostic_mode is not None or worker_requester_acceptance or lab_mode!='in-memory-stable-hub'):
        raise ValueError('Credit recovery acceptance requires the isolated real-process in-memory Hub lane')
    if credit_recovery_acceptance and hub_session_directory is None:
        raise ValueError('Credit recovery acceptance requires real Hub owner directory evidence')
    if pinky_integrated and (lab_mode!='three-hub-smoke-fdb' or stress_profile is not None or
                             diagnostic_mode is not None or not reserve_wallet):
        raise ValueError('Integrated Pinky needs verified real smoke/FDB Hubs, no diagnostic substitutions')
    if diagnostic_mode is not None and diagnostic_mode not in {'direct', 'seh'}:
        raise ValueError('Diagnostic transport must be direct or seh')
    if worker_requester_acceptance and (diagnostic_mode not in {'direct', 'seh'} or diagnostic_cycles != 1
                                       or diagnostic_idle_seconds != 0 or diagnostic_soak_seconds != 0
                                       or stress_profile is not None or pinky_integrated):
        raise ValueError('Worker/requester acceptance requires one isolated paid job on direct or SEH')
    if diagnostic_tls_io_mode not in {'baseline', 'serialized'}:
        raise ValueError('Diagnostic TLS I/O mode must be baseline or serialized')
    if diagnostic_mode is not None and (not 0 <= diagnostic_cycles <= 128 or not 0 <= diagnostic_idle_seconds <= 60
                                        or not 0 <= diagnostic_soak_seconds <= 7200):
        raise ValueError('Diagnostic cycles must be 0..128 and idle seconds 0..60')
    if not hub_url.startswith('http://127.0.0.1:'):
        raise ValueError('Real-process paid lab only accepts a loopback Stable Hub')
    from cryptography.hazmat.primitives import serialization

    # A new emulated market capability per Pinky cycle makes an old, even
    # incorrectly lingering, Worker advertisement ineligible for this sale.
    # Ordinary marketplace tests retain the original unscoped capability.
    market_isolation_capability = (
        'slh.lab.pinky.' + secrets.token_hex(16) if pinky_integrated else
        'slh.lab.worker-requester.' + secrets.token_hex(16) if worker_requester_acceptance else None
    )
    gates = {}
    with tempfile.TemporaryDirectory(prefix='mc-slh-two-service-lab-paid-') as td:
        root = Path(td)
        state, identities = root / 'state', root / 'identities'
        slh_id = 'paid-slh'
        api_port, worker_port = _ports()
        signer, relay_public = generate_identity()
        relay = (SEHRelay(
                         slh_public_key=relay_public, host='127.0.0.1', port=0).start()
                 if diagnostic_mode != 'direct' else None)
        adapter = None
        workers = []
        try:
            with _local(state, identities) as local:
                lp = local.server_port
                created = _ok(_http(lp, 'POST', '/v1/local/authenticator/identities',
                                    {'device_id': 'lab-owner-device', 'label': 'Lab Owner'}), 201)
                secret = (state / 'bootstrap.secret').read_text(encoding='utf-8').strip()
                begun = _ok(_http(lp, 'POST', '/v1/bootstrap/begin',
                                  {'bootstrap_secret': secret, }), 201)
                transaction = begun['transaction_id']
                statement = {**begun['proof_context'], 'admin_id': 'lab-owner',
                             'device_id': 'lab-owner-device', 'public_key': created['public_key']}
                signed = _ok(_http(lp, 'POST',
                    '/v1/local/authenticator/identities/lab-owner-device/sign',
                    {'challenge': statement}))['signature']
                _ok(_http(lp, 'PUT', f'/v1/bootstrap/{transaction}/administrator',
                          {'bootstrap_secret': secret, 'admin_id': 'lab-owner',
                           'admin_name': 'Lab Owner', 'device_id': 'lab-owner-device',
                           'public_key': created['public_key'], 'proof': signed}))
                _ok(_http(lp, 'POST', f'/v1/bootstrap/{transaction}/activate-p256',
                          {'bootstrap_secret': secret, 'defer_totp': True}))
                token = _login(lp, local_sign_port=lp)
                gates['actual_local_service_bootstrapped'] = local.organization_controller.config()['enabled'] is False
                # Configure the same Organization Service used in real deployment.
                relay_url = None
                if relay is not None:
                    relay_key = state / 'seh-service-signing-key.pem'
                    relay_key.write_bytes(signer.private_bytes(
                        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption()))
                    relay_key.chmod(0o600)
                    relay_url = f'ws://127.0.0.1:{relay.port}'
                    write_seh_settings(state, SEHConnectionSettings(
                        enabled=True, seh_url=relay_url, 
                         allow_loopback_lab=True, retry_seconds=0.2))
                _ok(_http(lp, 'PATCH', '/v1/local/organization/config',
                          {'host': '127.0.0.1', 'api_port': api_port, 'worker_port': worker_port,
                           'workers_enabled': True, 'seh_enabled': relay is not None}, bearer=token))
                _ok(_http(lp, 'POST', '/v1/local/organization/enable', {}, bearer=token))
                _await(lambda: local.organization_controller.status()['listening'] and (relay is None or relay.tunnel_ready),
                       'real paid Organization Service and SEH ready', timeout=18)
                gates['actual_organization_child_running'] = (
                    local.organization_controller.status()['pid'] is not None)
                pin = _fingerprint(api_port)
                org_identity = _ok(_http(api_port, 'GET', '/v1/org/identity', tls=True))
                gates['org_identity_tls_fingerprint_pinned'] = (
                    org_identity['tls_sha256'] == pin)
                gates['unauthorized_marketplace_api_denied'] = (
                    _http(api_port, 'GET', '/v1/org/admin/marketplace/status', tls=True)[0] == 401
                    and _http(api_port, 'POST', '/v1/org/admin/marketplace/reconcile',
                              {}, tls=True)[0] == 401)
                org_token = _login(api_port, local_sign_port=lp, tls=True)
                if relay_url is not None:
                    adapter = SEHLocalClientAdapter(relay_url,  allow_lab=True)
                # The only active managed Worker is reached through the SEH,
                # so the Hub cannot route to an accidental direct LAN slot.
                device = 'lab-paid-remote-worker'
                save_identity(root / 'worker-keys', device)
                worker_private, worker_public = load_identity(root / 'worker-keys', device)
                request = {'selected_admin': 'lab-owner', 'device_id': device,
                           'public_key': worker_public, 'kind': 'worker',
                           'device_name': device, 'claimed_user': 'Lab Owner',
                           'requested_role': 'worker', 'client_nonce': secrets.token_urlsafe(24)}
                proof_context = {**request, 'version': 1, 'action': 'slh.device.enrollment.request',
                                 'audience': 'slh://' + local.organization_controller.authority.service_fingerprint()}
                pending = _ok(_http(api_port, 'POST', '/v1/enrollment/requests',
                                     {**request, 'proof': sign_object(worker_private, proof_context)},
                                     tls=True), 201)
                _ok(_http(api_port, 'POST', '/v1/enrollment/approvals',
                          {'request_id': pending['request_id']}, bearer=org_token, tls=True), 201)
                gates['real_org_api_enrollment_approval'] = bool(pending['request_id'])
                def echo(offer):
                    payload = (offer.get('work') or {}).get('input')
                    return {'status': 'success', 'response': {
                        'model': 'mock-model', 'content': payload.get('value', '') if isinstance(payload, dict)
                        else str(payload or '')}}
                worker = ManagedSLHWorkerClient(
                    device_id=device, identity_dir=root / 'worker-keys',
                    tls_fingerprint=pin, hub_url=f'https://127.0.0.1:{adapter.server_address[1] if adapter is not None else worker_port}',
                    worker_id=device, auth_message={
                        'type': 'worker.auth', 'device_id': device,
                        'market': {'rings': ['ring-2'], 'capabilities': ['chat.completions'] + ([market_isolation_capability] if market_isolation_capability else []),
                                   'models': ['mock-model'],
                                   'price': {'amount': '1', 'unit': 'compute_credit'},
                                   'max_concurrency': 1}}, work_executor=echo)
                # Strict TLS EOF classification remains probe-only; all
                # managed Workers use serialized I/O by default. The probe
                # may explicitly restore the baseline control for A/B tests.
                if diagnostic_mode is not None and not worker_requester_acceptance:
                    worker.diagnostic_strict_tls_eof = True
                    worker.tls_io_mode = diagnostic_tls_io_mode
                with lab_stage('pinky.remote_worker_start', transport='seh' if relay is not None else 'direct', endpoint_port=adapter.server_address[1] if adapter is not None else worker_port):
                    worker.start()
                workers.append(worker)
                with lab_stage('pinky.remote_worker_registration', endpoint_port=api_port):
                    _await(lambda: len(_ok(_http(api_port, 'GET',
                       '/v1/org/admin/marketplace/status', bearer=org_token, tls=True))['workers']) == 1,
                       'paid Worker authenticated into real child scheduler')
                gates['real_scheduler_has_relay_worker'] = True
                # Normal organizational scheduler API, not direct SQLite test
                # mutation or a scheduler object in the calling lab process.
                policy = _ok(_http(api_port, 'GET', '/v1/admin/scheduler/policy',
                                   bearer=org_token, tls=True))
                _ok(_http(api_port, 'PATCH', '/v1/admin/scheduler/policy',
                          {'expected_revision': policy['revision'],
                           'changes': {'max_public_percent': 100,
                                       'min_private_idle_percent': 0}},
                          bearer=org_token, tls=True))
                identity = identify_hub_protocol(hub_url)
                if identity.get('service') != 'main_computer.stable_hub':
                    raise ValueError('Lab Hub has wrong service identity')
                wallet_key = '0x' + secrets.token_hex(32)
                wallet = SimpleNamespace(private_key=wallet_key,
                              address=private_key_to_address(wallet_key))
                upstream = issue_cli_multisession_authorization(
                    hub_url, wallet=wallet, stable_identity=identity)
                authorization = {'kind': 'multisession_key',
                                 'multisession_key_id': upstream['multisession_key_id']}
                proxy = RemoteMarketplace(api_port, org_token, authorization)
                before = proxy.status()['automatic']
                gates['marketplace_disabled_by_default'] = (
                    not before['configuration']['enabled'] and not proxy.sessions)
                proxy.authorize(authorization)
                proxy.configure(hub_url=hub_url, interval_seconds=1, enabled=True)
                _await(lambda: device in proxy.sessions, 'automatic real process public Hub session')
                outcome = proxy.status()['automatic']['last_result']
                gates['real_process_public_session'] = outcome['live_public_sessions'] == [device]
                gates['automatic_open_without_manual_reconcile'] = True
                status = proxy.status()
                gates['upstream_msk_never_in_status'] = (
                    upstream['multisession_key_id'] not in str(status))
                sessions = proxy.sessions
                accounts = {s.accepted.get('account_id') for s in sessions.values()}
                gates['one_wallet_for_real_worker'] = len(accounts) == 1 and None not in accounts
                if stress_profile is not None:
                    _record_preliminary_worker_checkpoint(
                        trace_file=stress_trace_file, stage='before_preliminary_paid_job',
                        worker=worker, proxy=proxy, api_port=api_port,
                        org_token=org_token, adapter=adapter, relay=relay)
                if diagnostic_mode is not None:
                    from .paid_transport_probe import run_paid_transport_probe
                    return run_paid_transport_probe(
                        mode=diagnostic_mode, cycles=diagnostic_cycles,
                        idle_seconds=diagnostic_idle_seconds, tls_io_mode=diagnostic_tls_io_mode,
                        soak_seconds=diagnostic_soak_seconds,
                        # Renew only the lab administrator bearer through the
                        # existing signed P-256 login. Worker TLS stays open.
                        admin_session_refresh=(
                            (lambda: _login(api_port, local_sign_port=lp, tls=True))
                            if diagnostic_soak_seconds > 0 else None),
                        hub_url=hub_url, ledger=payout_ledger, proxy=proxy,
                        worker=worker, adapter=adapter, relay=relay,
                        api_port=api_port, org_token=org_token,
                        worker_account_id=next(iter(accounts)),
                        lab_mode=lab_mode, trace_file=diagnostic_trace_file,
                        worker_port=worker_port,
                        acceptance=worker_requester_acceptance,
                        market_isolation_capability=market_isolation_capability)
                forwarded_before_paid = relay.bytes_forwarded
                with lab_stage('pinky.public_paid_cycle'):
                    paid = verify_paid_cycle(hub_url=hub_url, proxy=proxy, gateway=None,
                                             ledger=payout_ledger, worker_account_id=next(iter(accounts)),
                                             lab_mode=lab_mode, trace_file=paid_trace_file,
                                             market_isolation_capability=market_isolation_capability)
                gates.update({'paid_' + name: success for name, success in paid['gates'].items()})
                if stress_profile is not None:
                    witness = _record_preliminary_worker_checkpoint(
                        trace_file=stress_trace_file, stage='after_preliminary_paid_job',
                        worker=worker, proxy=proxy, api_port=api_port,
                        org_token=org_token, adapter=adapter, relay=relay)
                    if not witness['org_original_worker_registered'] or not witness['client_is_alive']:
                        # Gateway cleanup is asynchronous. Re-snapshot on failure only;
                        # never change the acceptance result or mask the missing Worker.
                        time.sleep(0.4)
                        witness = _record_preliminary_worker_checkpoint(
                            trace_file=stress_trace_file, stage='failed_preliminary_paid_job_audit',
                            worker=worker, proxy=proxy, api_port=api_port,
                            org_token=org_token, adapter=adapter, relay=relay)
                        raise ServiceLabFailure(
                            'Original Worker lost during preliminary paid job; '
                            'gateway and Worker timelines saved in integrated-stress-events.json')
                _await(lambda: worker.last_result.get('request_id') == paid['request_id'],
                       'paid job executed by SEH Worker', timeout=12)
                gates['actual_worker_executed_paid_job'] = worker.last_result.get('type') == 'worker.work.result'
                gates['paid_job_crossed_real_seh'] = (
                    relay.bytes_forwarded > forwarded_before_paid and adapter.bytes_forwarded > 0)
                # Keep two independent acceptance lanes. The normal lab tests
                # eight rapid single-Worker policy reversals; the integrated
                # load test exercises its own multi-Worker decisions. Running
                # the former as a mandatory preflight made a 64-Worker run fail
                # before its first extra Worker had connected, obscuring scope.
                integrated_stress = None
                credit_recovery = None
                if credit_recovery_acceptance:
                    from .credit_recovery_acceptance import verify_credit_recovery
                    with lab_stage('slh.credit_recovery_real_process'):
                        credit_recovery = verify_credit_recovery(
                            root=root, api_port=api_port, worker_port=worker_port,
                            local=local, org_token=org_token, proxy=proxy,
                            wallet=wallet, hub_identity=identity, ledger=payout_ledger,
                            hub_url=hub_url, original_worker=worker, pin=pin,
                            workers=workers, paid=paid,
                            hub_session_directory=hub_session_directory)
                    gates['credit_recovery_end_to_end'] = credit_recovery['pass']
                elif stress_profile is None:
                    # Decision storm on the *real child*: successive changes
                    # alternate private reserve and public offers. No explicit
                    # reconcile is needed; the service reacts to policy writes.
                    for i in range(8):
                        current = _ok(_http(api_port, 'GET', '/v1/admin/scheduler/policy',
                                            bearer=org_token, tls=True))
                        offer = 100 if i % 2 else 0
                        attempts_before = proxy.status()['automatic']['attempts']
                        _ok(_http(api_port, 'PATCH', '/v1/admin/scheduler/policy',
                                  {'expected_revision': current['revision'],
                                   'changes': {'max_public_percent': offer}},
                                  bearer=org_token, tls=True))
                        _await_policy_transition(proxy, device, offer == 100,
                                                 earlier_attempts=attempts_before,
                                                 decision=i)
                    gates['rapid_real_process_policy_decisions'] = True
                    # Explicit diagnostic requests may overlap automatic events,
                    # but must never create a duplicate advertised slot.
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(max_workers=4) as executor:
                        manual = list(executor.map(lambda _: proxy.reconcile(), range(4)))
                    gates['concurrent_manual_and_auto_no_duplicates'] = (
                        all(x['live_public_sessions'] == [device] for x in manual)
                        and len(proxy.sessions) == 1)
                    gates['automatic_reconcile_health_visible'] = (
                        proxy.status()['automatic']['last_success'] is not None)
                    # Changing the org policy through its API drains the upstream
                    # advertisement, while leaving the actual Worker connected.
                    current = _ok(_http(api_port, 'GET', '/v1/admin/scheduler/policy',
                                        bearer=org_token, tls=True))
                    _ok(_http(api_port, 'PATCH', '/v1/admin/scheduler/policy',
                              {'expected_revision': current['revision'],
                               'changes': {'max_public_percent': 0}},
                              bearer=org_token, tls=True))
                    _await(lambda: not proxy.sessions, 'automatic policy drain')
                    gates['real_process_public_slot_drained'] = not proxy.sessions
                    gates['automatic_policy_change_drained'] = True
                else:
                    integrated_stress = run_integrated_stress(
                        profile=stress_profile, hub_url=hub_url, ledger=payout_ledger,
                        root=root, api_port=api_port, org_token=org_token,
                        lp=lp, local_token=token, local=local, pin=pin,
                        adapter=adapter, relay=relay, proxy=proxy, workers=workers,
                        original_device=device, wallet_key=wallet_key,
                        upstream_auth=authorization, trace_file=stress_trace_file)
                pinky_result = None
                if pinky_integrated:
                    from .pinky_integrated import verify_pinky_shared_hub
                    with lab_stage('pinky.private_fulfillment_and_sweep'):
                        pinky_result = verify_pinky_shared_hub(
                            root=root, state=state, local=local, local_port=lp,
                            local_token=token, api_port=api_port,
                            worker_port=worker_port, hub_url=hub_url,
                            hub_identity=identity, ledger=payout_ledger,
                            org_wallet_private=wallet_key, org_wallet_address=wallet.address,
                            worker=worker, reserve_wallet=reserve_wallet,
                            market_controller=proxy, paid_marketplace=paid)
                else:
                    _ok(_http(lp, 'POST', '/v1/local/organization/disable', {}, bearer=token))
                gates['org_stops_without_stopping_local'] = (
                    local.organization_controller.status()['enabled'] is False and
                    _ok(_http(lp, 'GET', '/v1/local/status'))['local_service']['state'] == 'running')
                return {'schema': 'main-computer.slh-real-process-paid.v1',
                        'status': 'PASS' if all(gates.values()) else 'FAIL',
                        'pass': bool(gates) and all(gates.values()), 'gates': gates,
                        'paid_marketplace': paid,
                        **({'pinky_integrated':pinky_result} if pinky_integrated else {}),
                        **({'credit_recovery':credit_recovery} if credit_recovery_acceptance else {}),
                        **({'integrated_stress': integrated_stress} if integrated_stress is not None else {}),
                        'scope': 'real-org-child-real-seh-local-hub-synthetic-ledger',
                        'limitations': {'external_bridge': 'NOT_TESTED', 'public_wss': 'NOT_TESTED',
                                        'cloud_coolify': 'NOT_TESTED'}}
        finally:
            for worker in reversed(workers):
                worker.close(reason='real_paid_lab_teardown')
            if adapter is not None:
                adapter.stop()
            if relay is not None:
                relay.stop()
