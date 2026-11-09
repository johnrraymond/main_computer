"""Source-bound, read-only oracle for the proposed MCEL Secure Local Hub app.

This is deliberately NOT an app, an SLH service launcher, or a browser-facing
credential bridge. It establishes truthful source contracts and optionally
observes the already-running loopback SLH service. No mutation endpoints.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from main_computer.mcel_navigation_contract import validate_destination

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / 'mcel_oracles/slh/oracle.v1.json'
DEFAULT_OUTPUT = ROOT / 'runtime/reports/mcel-slh-oracle/report.json'
SOURCE_FILES = (
    'main_computer/slh/api.py',
    'main_computer/slh/local_service.py',
    'main_computer/slh/organization_service.py',
    'main_computer/slh/organization_controller.py',
    'main_computer/slh/scheduler_policy.py',
    'main_computer/slh/managed_gateway.py',
    'main_computer/slh/organization_wallet.py',
    'main_computer/slh/pinky_sweep.py',
)
# Independent floor: editing the human-readable reference cannot silently
# remove a required surface or convert a read-only observation into an action.
REQUIRED_SOURCE_CONTRACTS = frozenset({
    ('GET', '/v1/local/status', 'public', 'implemented', 'slh-local'),
    ('GET', '/v1/capabilities', 'public', 'implemented', 'slh-local'),
    ('GET', '/v1/admin/scheduler/policy', 'admin', 'implemented', 'slh-local'),
    ('GET', '/v1/admin/scheduler/status', 'admin', 'implemented', 'slh-local'),
    ('GET', '/v1/local/seh/status', 'admin', 'implemented', 'slh-local'),
    ('GET', '/v1/org/admin/marketplace/status', 'admin', 'implemented', 'slh-org-TLS'),
    ('GET', '/v1/credits', 'admin', 'implemented-if-wallet-service-present', 'slh-org-TLS'),
    ('GET', '/v1/workers', 'known', 'unimplemented', 'slh-local'),
    ('GET', '/v1/marketplace', 'admin', 'unimplemented', 'slh-local'),
})



class OracleError(ValueError):
    pass


def _require(value, reason):
    if not value:
        raise OracleError(reason)


def verify_source(repo: Path = ROOT) -> dict:
    """Compare independently authored oracle requirements against real code."""
    from main_computer.slh.api import ENDPOINTS
    from main_computer.slh.scheduler_policy import DEFAULT_POLICY
    from main_computer.slh.ports import DEFAULT_SLH_API_HOST

    spec = json.loads((repo / 'mcel_oracles/slh/oracle.v1.json').read_text(encoding='utf-8'))
    _require(spec['schema'] == 'mcel.slh.application-oracle.v1', 'oracle-schema-invalid')
    nav = spec.get('navigation_contract')
    _require(isinstance(nav, dict) and nav.get('schema') == 'mcel.slh.navigation-oracle.v1',
             'oracle-navigation-schema-invalid')
    _require(nav.get('stage') == 'planned-not-hosted', 'oracle-navigation-not-yet-hosted')
    _require(nav.get('not_navigable_without_url') is True and
             nav.get('permission_from_url') is False and
             nav.get('reload_and_history_required') is True,
             'oracle-navigation-invariants-disabled')
    required_destinations = {
        'slh.overview': ('/applications/slh', 'authenticated'),
        'slh.worker-bank': ('/applications/slh/workers', 'administrator'),
        'slh.worker': ('/applications/slh/workers/{workerId}', 'administrator'),
    }
    actual_destinations = nav.get('destinations')
    _require(isinstance(actual_destinations, list) and
             len(actual_destinations) == len(required_destinations),
             'oracle-navigation-inventory-invalid')
    try:
        for destination in actual_destinations:
            validate_destination(destination)
    except ValueError as exc:
        raise OracleError('oracle-navigation-destination-invalid: ' + str(exc)) from exc
    _require({x['id']: (x['href'], x['access']) for x in actual_destinations} ==
             required_destinations, 'oracle-navigation-address-drift')

    _require(spec['surfaces'] == ['overview', 'worker-bank'], 'oracle-surfaces-changed')
    _require(spec['single_organization'] is True, 'multiple-organization-ui-forbidden')
    _require(all(value is True for key, value in spec['presentation_contract'].items()
                 if key != 'mutations_enabled'), 'oracle-safety-invariant-disabled')
    _require(spec['presentation_contract']['mutations_enabled'] is False, 'oracle-mutations-forbidden')
    declared = frozenset((x['method'], x['path'], x['access'], x['state'], x['source'])
                         for x in spec['source_contracts'])
    _require(declared == REQUIRED_SOURCE_CONTRACTS and
             len(spec['source_contracts']) == len(REQUIRED_SOURCE_CONTRACTS),
             'oracle-required-contracts-changed')
    observed = {(x.method, x.path): (x.access, x.state) for x in ENDPOINTS}
    proofs = {}
    # The org marketplace status uses its own authenticated TLS dispatcher,
    # rather than the loopback SLHAPI endpoint catalog.
    org_source = (repo / 'main_computer/slh/organization_service.py').read_text(encoding='utf-8')
    for item in spec['source_contracts']:
        key = (item['method'], item['path'])
        if item['source'] == 'slh-org-TLS' and item['path'] == '/v1/org/admin/marketplace/status':
            _require(item['access'] == 'admin' and
                     "'/v1/org/admin/marketplace/status'" in org_source and
                     "authority.authenticate(token, admin=True)" in org_source,
                     'org-marketplace-source-contract-changed')
        elif item['source'] == 'slh-org-TLS' and item['path'] == '/v1/credits':
            _require(observed.get(key) == ('admin', 'implemented'), 'credit-contract-changed')
            _require('wallet_service is None' in (repo / 'main_computer/slh/api.py').read_text(encoding='utf-8'),
                     'credit-availability-gate-missing')
        else:
            _require(observed.get(key) == (item['access'], item['state']),
                     f'endpoint-drift:{item["method"]} {item["path"]}')
        proofs[item['method'] + ' ' + item['path']] = 'MATCH'
    _require(DEFAULT_SLH_API_HOST == '127.0.0.1', 'loopback-contract-changed')
    _require(DEFAULT_POLICY['min_organization_percent'] == 20 and
             DEFAULT_POLICY['max_public_percent'] == 80 and
             DEFAULT_POLICY['mode'] == 'private_first', 'worker-bank-default-policy-drift')
    hashes = {}
    for relative in SOURCE_FILES:
        path = repo / relative
        _require(path.is_file(), 'source-missing:' + relative)
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        'status': 'PASS', 'contracts': proofs, 'source_sha256': hashes,
        'policy_default': {'mode': 'private_first',
                           'min_organization_percent': 20,
                           'max_public_percent': 80},
        'scope': 'source, policy and future navigation contracts; not proof of live workers or hosted SLH routes',
        'navigation': {'stage': 'planned-not-hosted',
                       'destinations': [x['id'] for x in actual_destinations]},
    }


def _local_get(path: str, *, token: str | None = None, port: int = 8897,
               timeout: float = 2.0) -> tuple[int, dict]:
    """Only GET against an explicit loopback destination; no env proxy usage."""
    _require(path.startswith('/v1/') and '?' not in path and '\\' not in path, 'invalid-probe-path')
    _require(1 <= port <= 65535, 'invalid-port')
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = Request(f'http://127.0.0.1:{port}{path}', headers=headers, method='GET')
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:
            _require(response.status == 200, 'unexpected-http-status')
            return response.status, json.loads(response.read(131073))
    except HTTPError as exc:
        # Do not place remote message contents or authorization headers in report.
        return exc.code, {}


def _observation(state: str, *, source: str, value: dict | None = None, **details) -> dict:
    return {'state': state, 'source': source, 'value': value, **details}


def _local_projection(payload: dict) -> dict:
    _require(isinstance(payload, dict) and payload.get('schema') ==
             'main-computer.slh-local-status.v1', 'local-status-schema-mismatch')
    local = payload.get('local_service')
    _require(isinstance(local, dict) and local.get('state') == 'running' and
             local.get('binding') == 'loopback' and
             local.get('host') == '127.0.0.1', 'local-status-boundary-invalid')
    init = payload.get('initialization')
    _require(isinstance(init, dict) and type(init.get('initialized')) is bool,
             'local-initialization-status-invalid')
    org = payload.get('organization_service')
    _require(isinstance(org, dict) and type(org.get('enabled')) is bool and
             type(org.get('listening')) is bool and isinstance(org.get('state'), str),
             'organization-service-status-invalid')
    # Whitelist public-safe status; never echo unknown fields or identity keys.
    return {
        'local_service': _observation('RUNNING', source='GET /v1/local/status',
                                      value={'binding': 'loopback'}),
        'initialization': _observation('INITIALIZED' if init['initialized'] else 'NOT_INITIALIZED',
                                        source='GET /v1/local/status'),
        'organization_service': _observation(org['state'].upper(),
                                             source='GET /v1/local/status',
                                             value={'enabled': org['enabled'],
                                                    'listening': org['listening'],
                                                    'workers_enabled': org.get('workers_enabled') is True,
                                                    'seh_enabled': org.get('seh_enabled') is True}),
    }


def _scheduler_projection(payload: dict) -> dict:
    _require(isinstance(payload, dict), 'scheduler-not-object')
    policy_record = payload.get('policy')
    runtime = payload.get('runtime')
    _require(isinstance(policy_record, dict) and
             policy_record.get('schema') == 'main-computer.slh-scheduler-policy.v1'
             and isinstance(policy_record.get('policy'), dict) and
             type(policy_record.get('revision')) is int, 'scheduler-policy-schema-mismatch')
    policy = policy_record['policy']
    _require(policy.get('mode') in ('private_first', 'percentage', 'round_robin', 'dedicated'),
             'scheduler-mode-invalid')
    for key in ('min_organization_percent', 'max_public_percent', 'min_private_idle_percent'):
        number = policy.get(key)
        _require(type(number) in (int, float) and 0 <= number <= 100,
                 'scheduler-policy-percent-invalid:' + key)
    _require(isinstance(runtime, dict) and runtime.get('status') in
             ('NOT_OBSERVED', 'OBSERVED', 'STALE'), 'scheduler-runtime-status-invalid')
    live = runtime.get('status') == 'OBSERVED' and runtime.get('live') is True
    _require(runtime.get('status') != 'OBSERVED' or live, 'scheduler-runtime-freshness-contradiction')
    projection = {
        'scheduler_policy': _observation('CONFIGURED', source='GET /v1/admin/scheduler/status',
                value={'revision': policy_record['revision'], 'mode': policy['mode'],
                       'min_organization_percent': policy['min_organization_percent'],
                       'max_public_percent': policy['max_public_percent'],
                       'min_private_idle_percent': policy['min_private_idle_percent']}),
        'worker_runtime': _observation('OBSERVED' if live else runtime['status'],
                 source='GET /v1/admin/scheduler/status', value=None),
        'live_worker_count': _observation('NOT_OBSERVED', source='org marketplace TLS admin status',
                                          value=None),
        'credit_meter': _observation('NOT_CONNECTED' if payload.get('credit_meter') == 'NOT_CONNECTED'
                else 'NOT_OBSERVED', source='scheduler credit meter', value=None),
    }
    if live:
        snapshot = runtime.get('snapshot')
        _require(isinstance(snapshot, dict), 'scheduler-observed-snapshot-missing')
        allowed = ('live_public_sessions','private_idle_ready','private_idle_target',
                   'reserve_satisfied','max_public_slots','reason')
        safe = {k: snapshot[k] for k in allowed if k in snapshot}
        # Never turn device identifiers into counts, nor claim a live fleet
        # census from a scheduler reconciliation snapshot.
        projection['worker_runtime']['value'] = safe
    return projection


def _seh_projection(payload: dict) -> dict:
    _require(isinstance(payload, dict) and payload.get('status') in
             ('CONNECTED_VERIFIED', 'NOT_CONNECTED'), 'seh-status-invalid')
    for key in ('outbound_tunnel_authenticated', 'organization_enabled',
                'seh_enabled', 'workers_enabled', 'resident_child_observed'):
        _require(type(payload.get(key)) is bool, 'seh-field-invalid:' + key)
    _require(payload['status'] != 'CONNECTED_VERIFIED' or
             (payload['outbound_tunnel_authenticated'] and payload['resident_child_observed']),
             'seh-verified-without-connection-proof')
    return _observation(payload['status'], source='GET /v1/local/seh/status',
        value={key: payload[key] for key in (
            'outbound_tunnel_authenticated', 'organization_enabled',
            'seh_enabled', 'workers_enabled', 'resident_child_observed')})


def observe(*, port: int = 8897, admin_token: str | None = None,
            get=_local_get) -> dict:
    """Observe existing SLH service only; no admin credentials in results."""
    observations = {
        'local_service': _observation('UNAVAILABLE', source='GET /v1/local/status'),
        'initialization': _observation('NOT_OBSERVED', source='GET /v1/local/status'),
        'organization_service': _observation('NOT_OBSERVED', source='GET /v1/local/status'),
        'scheduler_policy': _observation('NOT_AUTHORIZED' if not admin_token else 'NOT_OBSERVED',
                                          source='GET /v1/admin/scheduler/status'),
        'worker_runtime': _observation('NOT_AUTHORIZED' if not admin_token else 'NOT_OBSERVED',
                                        source='GET /v1/admin/scheduler/status'),
        'live_worker_count': _observation('NOT_OBSERVED', source='org marketplace TLS admin status'),
        'credit_meter': _observation('NOT_OBSERVED', source='scheduler credit meter'),
        'seh_connection': _observation('NOT_AUTHORIZED' if not admin_token else 'NOT_OBSERVED',
                                        source='GET /v1/local/seh/status'),
    }
    try:
        code, raw = get('/v1/local/status', port=port)
    except (OSError, URLError, TimeoutError, ConnectionError):
        return {'status': 'INCOMPLETE_NO_LOCAL_SERVICE', 'observations': observations}
    _require(code == 200, 'local-status-http-' + str(code))
    observations.update(_local_projection(raw))
    if not admin_token:
        return {'status': 'PASS_PUBLIC_OBSERVATION', 'observations': observations}
    try:
        code, raw = get('/v1/admin/scheduler/status', port=port, token=admin_token)
    except (OSError, URLError, TimeoutError, ConnectionError):
        observations['scheduler_policy']['state'] = 'UNAVAILABLE'
        observations['worker_runtime']['state'] = 'UNAVAILABLE'
        return {'status': 'INCOMPLETE_ADMIN_OBSERVATION', 'observations': observations}
    if code in (401, 403):
        observations['scheduler_policy']['state'] = 'NOT_AUTHORIZED'
        observations['worker_runtime']['state'] = 'NOT_AUTHORIZED'
        return {'status': 'INCOMPLETE_ADMIN_OBSERVATION', 'observations': observations}
    _require(code == 200, 'scheduler-status-http-' + str(code))
    observations.update(_scheduler_projection(raw))
    try:
        code, raw = get('/v1/local/seh/status', port=port, token=admin_token)
    except (OSError, URLError, TimeoutError, ConnectionError):
        observations['seh_connection']['state'] = 'UNAVAILABLE'
        return {'status': 'INCOMPLETE_ADMIN_OBSERVATION', 'observations': observations}
    if code in (401, 403):
        observations['seh_connection']['state'] = 'NOT_AUTHORIZED'
        return {'status': 'INCOMPLETE_ADMIN_OBSERVATION', 'observations': observations}
    _require(code == 200, 'seh-status-http-' + str(code))
    observations['seh_connection'] = _seh_projection(raw)
    # Deliberately do not contact organization TLS; that requires a separate
    # authenticated server-side capability adapter, which does not yet exist.
    return {'status': 'PASS_ADMIN_OBSERVATION', 'observations': observations}


def run(*, mode: str = 'contract', port: int = 8897,
        admin_token: str | None = None, output: Path = DEFAULT_OUTPUT) -> dict:
    try:
        source = verify_source()
        result = {'schema': 'mcel.slh.oracle-report.v1', 'mode': mode,
                  'status': 'PASS_CONTRACT', 'source': source,
                  'scope': ['overview', 'worker-bank'], 'production_promoted': False}
        if mode == 'observe':
            observed = observe(port=port, admin_token=admin_token)
            result['observed'] = observed
            result['status'] = observed['status']
    except (OracleError, OSError, KeyError, TypeError, ValueError) as exc:
        result = {'schema': 'mcel.slh.oracle-report.v1', 'mode': mode,
                  'status': 'FAIL', 'error': str(exc), 'production_promoted': False}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only SLH MCEL application oracle (no SLH startup)')
    parser.add_argument('--mode', choices=('contract', 'observe'), default='contract')
    parser.add_argument('--port', type=int, default=8897)
    parser.add_argument('--admin-token-env', help='optional environment variable holding an existing SLH admin session')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    token = os.environ.get(args.admin_token_env) if args.admin_token_env else None
    result = run(mode=args.mode, port=args.port, admin_token=token, output=args.output)
    print('MCEL_SLH_ORACLE ' + result['status'])
    print('  scope: overview, worker-bank')
    print('  promoted: false')
    if 'observed' in result:
        for name, value in result['observed']['observations'].items():
            print(f'  {name}: {value["state"]}')
    if result.get('error'):
        print('  error: ' + result['error'])
    print('  report: ' + str(args.output))
    return (0 if result['status'].startswith('PASS')
            else 2 if result['status'].startswith('INCOMPLETE') else 1)


if __name__ == '__main__':
    raise SystemExit(main())
