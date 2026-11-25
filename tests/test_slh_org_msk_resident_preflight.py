"""Resident-only acceptance must use LMS authority, not a CLI private key store."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer import mcel_slh_application as application
from main_computer import mcel_slh_first_run as first_run
from main_computer import mcel_slh_worker_bridge as bridge
from main_computer import hub_networks
from main_computer.slh import authenticator_store
from tools.slh_seh_lab import org_msk_passthrough as gate


@pytest.fixture
def resident_fixture(monkeypatch):
    events = []
    token = 'disposable-test-token-secret'
    state = Path('test-resident-state')
    profile = SimpleNamespace(hub_url='https://mainnet.example', chain_id=42424240)
    inventory = {'schema': application.IDENTITIES_SCHEMA, 'state': 'AVAILABLE',
        'defaultDeviceId': 'admin-default', 'identities': [
            {'deviceId': 'other', 'defaultStatus': 'ADDITIONAL'},
            {'deviceId': 'admin-default', 'defaultStatus': 'DEFAULT'}]}
    wallet = {'schema': 'main-computer.slh-org-wallet.v1', 'state': 'CURRENT',
        'binding': {'chain_id': '42424240', 'hub_url': profile.hub_url,
            'trust_mode': 'SYSTEM_TLS', 'account_id': 'account', 'wallet_address': '0x' + '12'*20},
        'balance': {'available_credit_wei': '1000', 'secret': token}}
    status = {'schema': 'main-computer.slh-org-marketplace.v1', 'sessions': {},
        'automatic': {'schema': 'main-computer.slh-marketplace-auto.v1',
            'configuration': {'enabled': True, 'hub_url': profile.hub_url},
            'authorization_present': True, 'organization_wallet': wallet,
            'financial_readiness': {'state': 'READY', 'network': 'mainnet', 'wallet_address': wallet['binding']['wallet_address']}}}
    options = SimpleNamespace(inventory=inventory, wallet=wallet, status=status, events=events,
        session={'access_token': token, 'role': 'admin', 'device_id': 'admin-default'},
        login_error=None, read_error=None, cleanup_error=None, token=token)
    class Controller:
        state_dir = state
        local_port = 8897
        def login(self, *, device_id):
            events.append(('login', device_id))
            if options.login_error: raise options.login_error
            return options.session
    class Client:
        def __init__(self, *, state_dir):
            assert state_dir == state
        def request(self, method, path, supplied):
            assert supplied == token
            events.append((method, path))
            if options.read_error: raise options.read_error
            return status
        def logout(self, supplied):
            assert supplied == token
            events.append(('logout',))
            if options.cleanup_error: raise options.cleanup_error
    def observe(*, port):
        assert port == Controller.local_port
        events.append(('inventory',))
        return inventory
    def no_key_store(*args, **kwargs):
        pytest.fail('resident probe must not access local private key store')
    monkeypatch.setattr(authenticator_store, 'LocalIdentityStore', no_key_store)
    monkeypatch.setattr(application, 'observe_lms_slh_identities', observe)
    monkeypatch.setattr(first_run, 'SlhFirstRunController', Controller)
    monkeypatch.setattr(bridge, 'OrganizationWorkerClient', Client)
    monkeypatch.setattr(hub_networks, 'load_hub_network_registry', lambda: SimpleNamespace(networks={'mainnet': profile}))
    return options


def test_resident_uses_lms_default_and_only_reads_status(resident_fixture):
    f = resident_fixture
    r = gate.resident()
    assert r['status'] == 'PASS_PREFLIGHT_ONLY'
    assert r['missing'] == [] and r['blockers'] == []
    assert f.events == [('inventory',), ('login', 'admin-default'),
        ('GET', '/v1/org/admin/marketplace/status'), ('logout',)]
    assert r['observations']['wallet_balance_wei'] == {'available_credit_wei': '1000'}
    assert r['observations']['worker_account_check'] == 'NOT_TESTED_NO_CONNECTED_WORKERS'
    assert f.token not in str(r)


@pytest.mark.parametrize('defect', ['unavailable', 'missing-default', 'wrong-schema', 'duplicate-default'])
def test_invalid_inventory_never_attempts_login(resident_fixture, defect):
    f = resident_fixture
    if defect == 'unavailable': f.inventory['state'] = 'UNAVAILABLE'
    if defect == 'missing-default': f.inventory['defaultDeviceId'] = 'absent'
    if defect == 'wrong-schema': f.inventory['schema'] = 'unknown'
    if defect == 'duplicate-default': f.inventory['identities'].append(dict(f.inventory['identities'][-1]))
    r = gate.resident()
    assert r['status'] == 'BLOCKED' and r['stage'] == 'resident-lms-inventory'
    assert r['error_code'].startswith('RESIDENT_LMS_')
    assert f.events == [('inventory',)]
    assert set(r['missing']) <= gate.RESIDENT_REQUIRED


def test_admin_refusal_not_a_wallet_failure(resident_fixture):
    f = resident_fixture
    f.login_error = first_run.FirstRunError('ADMIN_REQUIRED', 403)
    r = gate.resident()
    assert r['stage'] == 'resident-admin-auth' and r['error_code'] == 'ADMIN_REQUIRED'
    assert r['status'] == 'BLOCKED' and 'wallet_state' not in r['observations']
    assert f.events == [('inventory',), ('login', 'admin-default')]


@pytest.mark.parametrize('defect,blocker', [
    ('unbound', 'RESIDENT_MAINNET_WALLET_NOT_CURRENT'),
    ('stale', 'RESIDENT_MAINNET_WALLET_NOT_CURRENT'),
    ('wrong-chain', 'RESIDENT_MAINNET_WALLET_NOT_CURRENT'),
    ('no-msk', 'RESIDENT_MSK_NOT_PRESENT'),
    ('wrong-worker', 'RESIDENT_WORKER_ACCOUNT_MISMATCH'),
    ('cleanup', 'RESIDENT_SESSION_CLEANUP_UNVERIFIED'),
    ('startup-blocked', 'RESIDENT_FINANCIAL_STARTUP_NOT_READY'),
])
def test_resident_blocks_inadequate_wallet_evidence(resident_fixture, defect, blocker):
    f = resident_fixture
    if defect == 'unbound': f.wallet.update(state='UNBOUND', binding=None, balance=None)
    if defect == 'stale': f.wallet['state'] = 'STALE'
    if defect == 'wrong-chain': f.wallet['binding']['chain_id'] = '42424241'
    if defect == 'no-msk': f.status['automatic']['authorization_present'] = False
    if defect == 'wrong-worker': f.status['sessions']['w'] = {'connected': True, 'account_id': 'other'}
    if defect == 'cleanup': f.cleanup_error = RuntimeError(f.token)
    if defect == 'startup-blocked': f.status['automatic']['financial_readiness'] = {'state': 'BLOCKED', 'reason': 'PINKY_SWEEP_CONFIGURATION_REQUIRED'}
    r = gate.resident()
    assert r['status'] == 'BLOCKED' and blocker in r['blockers']
    assert f.events[-1] == ('logout',) and f.token not in str(r)


def test_failed_status_read_still_revokes_session(resident_fixture):
    f = resident_fixture
    f.read_error = bridge.WorkerBridgeError('ORG_TRANSPORT_UNAVAILABLE', 503)
    r = gate.resident()
    assert r['status'] == 'BLOCKED' and r['error_code'] == 'ORG_TRANSPORT_UNAVAILABLE'
    assert r['checks']['resident_test_session_revoked'] is True
    assert f.events[-1] == ('logout',)


def test_resident_restart_evidence_reads_policy_and_never_requests_secret(resident_fixture, monkeypatch):
    from tools.slh_seh_lab import resident_restart as restart
    f = resident_fixture
    f.wallet['binding'].update(hub_id='hub', cluster_id='cluster')
    f.status['automatic']['configuration'].update(revision=1, interval_seconds=30)
    f.status['automatic']['financial_readiness']['checked_at'] = 1015.
    client = bridge.OrganizationWorkerClient
    original = client.request
    def request(self, method, path, token):
        if path == restart.POLICY_PATH:
            assert method == 'GET' and token == f.token
            f.events.append((method,path))
            return {'enabled':False,'revision':0,'max_request_credit_wei':'0','budget_credit_wei':'0'}
        return original(self, method, path, token)
    monkeypatch.setattr(client,'request',request)
    monkeypatch.setattr(restart,'process_identity',lambda _: {'pid':101,'created':1000.})
    monkeypatch.setattr(restart,'signer_acl',lambda _: {'platform':'WINDOWS','directory_private':True,'file_private':True})
    # Avoid monkeypatching the global time module used by other libraries.
    monkeypatch.setattr(restart,'time',SimpleNamespace(time=lambda:1020.))
    r = gate.resident(restart_evidence=True)
    assert restart.snapshot_valid(r),r
    assert f.events[-2:] == [('GET',restart.POLICY_PATH),('logout',)]
    assert f.token not in str(r)
