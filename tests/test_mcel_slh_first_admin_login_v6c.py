"""V6-C: Login Administrator only bootstraps when local ORG has no active admin.

Real local HTTP + pinned ORG HTTPS; disposable keys and SQLite only.
"""
from __future__ import annotations

from contextlib import contextmanager
from threading import Thread
import time

import pytest

from main_computer.mcel_slh_first_run import SlhFirstRunController, FirstRunError
from main_computer.slh.api import SLHAPI
from main_computer.slh.authority import SLHAuthority, SLHError
from main_computer.slh.authenticator_store import LocalIdentityStore
from main_computer.slh.local_service import create_server
from main_computer.slh.managed_gateway import transport_identity
from main_computer.slh.organization_service import OrganizationHTTPHandler, OrganizationHTTPServer
from main_computer.slh.organization_state import DEFAULTS, save_config
from tools.slh_seh_connector import service_identity


@contextmanager
def setup(tmp_path, *, pre_enrolled=False):
    state, identities = tmp_path / 'org', tmp_path / 'identities'
    service_identity(state, create=True)
    org = SLHAuthority(state)
    org.ensure_local_auth_key()
    with org._db() as db:
        db.execute('INSERT INTO slh_setup(singleton,initialized_at) VALUES (1,?)', (int(time.time()),))
        if pre_enrolled:
            db.execute('INSERT INTO users VALUES (?,?,?,?)', ('prior-user', 'prior', 'admin', 'active'))
            db.execute('INSERT INTO devices VALUES (?,?,?,?,?,?,?,?,?)',
                       ('prior-admin', 'prior-user', 'old-public-key', 'old-fingerprint',
                        'p256_device', 'prior', '', 'active', int(time.time())))
    context, fingerprint = transport_identity(state)
    server = OrganizationHTTPServer(('127.0.0.1', 0), OrganizationHTTPHandler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.authority = org
    server.api = SLHAPI(org, root_api_enabled=True)
    server.fingerprint = fingerprint
    server.state_dir = state
    save_config(state, {**DEFAULTS, 'configured': True, 'enabled': True,
                        'activation_confirmed': True, 'host': '127.0.0.1',
                        'api_port': server.server_port})
    org_thread = Thread(target=server.serve_forever, daemon=True)
    org_thread.start()
    try:
        with create_server(state_dir=state, identity_dir=identities, port=0) as lms:
            local_thread = Thread(target=lms.serve_forever, daemon=True)
            local_thread.start()
            try:
                controller = SlhFirstRunController(state_dir=state, identity_dir=identities,
                                                   local_port=lms.server_port)
                yield org, identities, controller, lms
            finally:
                lms.shutdown()
                local_thread.join(timeout=5)
    finally:
        server.shutdown()
        org_thread.join(timeout=5)
        server.server_close()


def admin_count(org):
    with org._db() as db:
        return db.execute("""SELECT COUNT(*) FROM devices d JOIN users u ON d.user_id=u.id
            WHERE d.status='active' AND u.status='active' AND u.role='admin'""").fetchone()[0]


def test_first_login_bootstraps_only_when_no_admin_then_reuses_authority(tmp_path):
    with setup(tmp_path) as (org, identities, controller, lms):
        selected = LocalIdentityStore(identities).list()['default_device_id']
        assert selected and admin_count(org) == 0
        assert controller._request('GET', '/v1/local/status')['administrator_enrollment']['state'] == 'MISSING'
        initial_root = org.local_auth_key_path.read_bytes()
        login = controller.login(device_id=selected)
        assert login['role'] == 'admin' and login['device_id'] == selected
        assert org.authenticate(login['access_token'], admin=True)['device_id'] == selected
        assert admin_count(org) == 1
        assert controller.login(device_id=selected)['role'] == 'admin'
        assert admin_count(org) == 1
        assert org.local_auth_key_path.read_bytes() == initial_root
        actions = [a for a in org.audit() if a['action'] == 'administrator.root_grant']
        assert len(actions) == 1 and actions[0]['target'] == selected
        assert initial_root.decode().strip() not in str(login)


def test_existing_admin_prevents_automatic_grant_and_preserves_root(tmp_path):
    with setup(tmp_path, pre_enrolled=True) as (org, identities, controller, lms):
        selected = LocalIdentityStore(identities).list()['default_device_id']
        root_before = org.local_auth_key_path.read_bytes()
        assert admin_count(org) == 1
        with pytest.raises(FirstRunError) as failure:
            controller.login(device_id=selected)
        assert failure.value.code == 'UNKNOWN_OR_REVOKED_DEVICE'
        assert admin_count(org) == 1
        assert org.local_auth_key_path.read_bytes() == root_before
        assert not any(a['action'] == 'administrator.root_grant' for a in org.audit())
        assert controller._request('GET', '/v1/local/status')['administrator_enrollment']['state'] == 'ENROLLED'


def test_repeated_auto_bootstrap_is_rejected_if_admin_exists(tmp_path):
    with setup(tmp_path, pre_enrolled=True) as (org, identities, controller, lms):
        selected = LocalIdentityStore(identities).list()['default_device_id']
        with pytest.raises(FirstRunError) as failure:
            controller._request('POST', '/v1/local/organization/admin/identities',
                                {'device_id': selected, 'bootstrap_only': True})
        assert failure.value.code == 'ADMINISTRATOR_ALREADY_ENROLLED'
        assert admin_count(org) == 1


def test_race_guard_at_org_authority_after_lms_status_check(tmp_path, monkeypatch):
    with setup(tmp_path) as (org, identities, controller, lms):
        selected = LocalIdentityStore(identities).list()['default_device_id']
        local_reg = lms.organization_controller.authority
        observed = local_reg.administrator_enrollment_status
        def altered():
            state = observed()
            with org._db() as db:
                db.execute('INSERT INTO users VALUES (?,?,?,?)', ('race-user', 'race', 'admin', 'active'))
                db.execute('INSERT INTO devices VALUES (?,?,?,?,?,?,?,?,?)',
                    ('race-admin', 'race-user', 'race-pub', 'race-fingerprint',
                     'p256_device', 'race', '', 'active', int(time.time())))
            return state
        monkeypatch.setattr(local_reg, 'administrator_enrollment_status', altered)
        with pytest.raises(FirstRunError) as failure:
            controller._request('POST', '/v1/local/organization/admin/identities',
                                {'device_id': selected, 'bootstrap_only': True})
        assert failure.value.code == 'ADMINISTRATOR_ALREADY_ENROLLED'
        assert admin_count(org) == 1
        assert not any(a['action'] == 'administrator.root_grant' for a in org.audit())


def test_explicit_recovery_remains_a_separate_authorized_operation(tmp_path):
    with setup(tmp_path, pre_enrolled=True) as (org, identities, controller, lms):
        selected = LocalIdentityStore(identities).list()['default_device_id']
        result = controller._request('POST', '/v1/local/organization/admin/identities', {'device_id': selected})
        assert result['status'] == 'REGISTERED'
        assert admin_count(org) == 2
        assert controller.login(device_id=selected)['role'] == 'admin'
        assert admin_count(org) == 2


@pytest.mark.parametrize('previous_config_enabled', [False, True])
def test_fresh_organization_login_initializes_and_authenticates_with_selected_identity(tmp_path, monkeypatch, previous_config_enabled):
    """Real control HTTP and real ORG subprocess: no bootstrap form or CLI grant."""
    import socket
    from main_computer.slh.organization_state import read_config, save_config
    from main_computer.slh.authority import SLHAuthority
    import main_computer.mcel_slh_first_run as first_run

    # Choose two adjacent free ports to exercise the built-in loopback default
    # without trying to take the normal 8898/8899 ports during acceptance.
    def available_pair():
        for _ in range(100):
            with socket.socket() as a:
                a.bind(('127.0.0.1', 0))
                first = a.getsockname()[1]
                if first >= 65535:
                    continue
                with socket.socket() as b:
                    try:
                        b.bind(('127.0.0.1', first + 1))
                    except OSError:
                        continue
                    return first
        raise RuntimeError('no adjacent free ports')

    worker_port = available_pair()
    monkeypatch.setattr(first_run, 'DEFAULT_SLH_MANAGED_PORT', worker_port)
    state, identities = tmp_path / 'new-org', tmp_path / 'identities'
    if previous_config_enabled:
        # Mirrors the user's reset: delete only organization.sqlite3 and keep
        # previously enabled TLS configuration and service signing identity.
        service_identity(state, create=True)
        save_config(state, {**DEFAULTS, 'configured': True, 'enabled': True,
                            'activation_confirmed': True, 'host': '127.0.0.1',
                            'api_port': worker_port + 1, 'worker_port': worker_port,
                            'workers_enabled': True, 'seh_enabled': False})
    with create_server(state_dir=state, identity_dir=identities, port=0) as lms:
        local_thread = Thread(target=lms.serve_forever, daemon=True)
        local_thread.start()
        try:
            controller = SlhFirstRunController(state_dir=state, identity_dir=identities,
                                               local_port=lms.server_port)
            selected = LocalIdentityStore(identities).list()['default_device_id']
            assert controller._request('GET', '/v1/local/status')['administrator_enrollment']['state'] == 'NOT_ESTABLISHED'
            first = controller.login(device_id=selected)
            assert first['device_id'] == selected and first['role'] == 'admin'
            with SLHAuthority(state)._db() as db:
                assert db.execute("SELECT COUNT(*) FROM devices WHERE id=? AND status='active'", (selected,)).fetchone()[0] == 1
                assert db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND status='active'").fetchone()[0] == 1
            assert (state / 'LOCAL-AUTH-KEY').is_file()
            cfg = read_config(state)
            assert cfg['host'] == '127.0.0.1' and cfg['enabled'] and cfg['configured']
            assert cfg['api_port'] == worker_port + 1
            assert controller.status()['organization'] == 'running'
            second = controller.login(device_id=selected)
            assert second['role'] == 'admin'
            with SLHAuthority(state)._db() as db:
                assert db.execute("SELECT COUNT(*) FROM devices WHERE status='active'").fetchone()[0] == 1
        finally:
            lms.shutdown()
            local_thread.join(timeout=5)
