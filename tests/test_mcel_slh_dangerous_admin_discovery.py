"""Live-mutation administrator discovery against disposable real HTTP authority.

No test in this module reaches the operator's SLH, credentials, or Coolify.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import threading
from pathlib import Path
from wsgiref.simple_server import make_server

import pytest

from main_computer.slh.api import SLHAPI
from main_computer.slh.authority import SLHAuthority
from main_computer.slh.authenticator_store import LocalIdentityStore
from main_computer.slh.local_authenticator_api import LocalAuthenticatorAPI
from tools import mcel_slh_dangerous_functional_acceptance as dangerous
from tools.slh_authenticator_cli import _request, bootstrap_admin, save_identity
from tools.slh_enrollment_lifecycle import _open_authenticated_session
from tools.slh_seh_connector import service_identity


@contextmanager
def disposable_org(tmp_path: Path, *, designated_default: bool = True,
                   lms_signer: bool = False):
    state = tmp_path / 'authority'
    identity_dir = tmp_path / 'credentials'
    service_identity(state, create=True)
    store = LocalIdentityStore(identity_dir)
    if designated_default:
        root_id = store.ensure_default_identity()['device_id']
    else:
        root_id = 'fixture-root'
        save_identity(identity_dir, root_id)
    authority = SLHAuthority(state)
    local_auth = LocalAuthenticatorAPI(identity_dir) if lms_signer else None
    server = make_server('127.0.0.1', 0, SLHAPI(authority, local_authenticator=local_auth).wsgi_app,
                         handler_class=dangerous._QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        bootstrap_admin(port=server.server_port, state_dir=state,
                        identity_dir=identity_dir, device_id=root_id,
                        admin_id='fixture-owner', admin_name='Fixture Owner')
        yield server.server_port, identity_dir, root_id, authority
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def enroll_second_admin(port: int, identity_dir: Path, root_id: str) -> str:
    device_id = 'second-admin'
    request_id = dangerous.submit_identity(port, identity_dir, 'fixture-owner',
                                           device_id, 'admin')
    token, session = _open_authenticated_session(port, identity_dir, root_id)
    try:
        assert session['role'] == 'admin'
        receipt = _request(port, 'POST', '/v1/enrollment/approvals',
                           {'request_id': request_id}, token=token)
        assert receipt['status'] == 'APPROVED'
    finally:
        assert _request(port, 'POST', '/v1/auth/logout', {}, token=token)['status'] == 'LOGGED_OUT'
    return device_id


def test_live_identity_acceptance_autodiscovers_admin_and_mutates_only_test_records(tmp_path):
    with disposable_org(tmp_path) as (port, credentials, root, authority):
        report = tmp_path / 'live-report.json'
        assert dangerous.main([
            '--mode', 'live', '--scope', 'identities',
            '--local-port', str(port), '--identity-dir', str(credentials),
            '--execute-mutations', '--acknowledge-real-mutations',
            '--report', str(report),
        ]) == 0
        data = json.loads(report.read_text(encoding='utf-8'))
        assert data['status'] == 'PASS_LIVE_MUTATIONS'
        assert data['administrator_discovery'] == {
            'status': 'VERIFIED', 'device_id': root,
            'basis': 'explicit-filesystem-signed-session-and-admin-directory'}
        assert data['groups']['identities']['administrator_device'] == root
        assert data['groups']['identities']['cleanup'] == 'VERIFIED_REMOVED'
        assert [row['id'] for row in authority.devices()] == [root]


def test_prefers_designated_default_if_multiple_authorized_admins(tmp_path):
    with disposable_org(tmp_path) as (port, credentials, root, _):
        other = enroll_second_admin(port, credentials, root)
        assert dangerous.discover_admin_device(port=port, identity_dir=credentials) == root
        token, session = _open_authenticated_session(port, credentials, other)
        assert session['role'] == 'admin'
        assert _request(port, 'POST', '/v1/auth/logout', {}, token=token)['status'] == 'LOGGED_OUT'


def test_ambiguity_without_default_refused_before_mutation(tmp_path):
    with disposable_org(tmp_path, designated_default=False) as (port, credentials, root, authority):
        other = enroll_second_admin(port, credentials, root)
        initial = {row['id'] for row in authority.devices()}
        initial_queue = authority.queue()
        assert initial == {root, other}
        with pytest.raises(dangerous.AcceptanceError,
                           match='MULTIPLE_ADMIN_DEVICES_REQUIRE_EXPLICIT_SELECTION'):
            dangerous.discover_admin_device(port=port, identity_dir=credentials)
        report = tmp_path / 'ambiguous-report.json'
        exit_code = dangerous.main([
            '--mode', 'live', '--scope', 'identities', '--local-port', str(port),
            '--identity-dir', str(credentials), '--execute-mutations',
            '--acknowledge-real-mutations', '--report', str(report),
        ])
        assert exit_code == 4
        assert json.loads(report.read_text())['failed_gate'] == 'MULTIPLE_ADMIN_DEVICES_REQUIRE_EXPLICIT_SELECTION'
        assert {row['id'] for row in authority.devices()} == initial
        assert authority.queue() == initial_queue


def test_no_local_admin_refused_without_enrollment(tmp_path):
    with disposable_org(tmp_path) as (port, _credentials, root, authority):
        empty = tmp_path / 'empty-store'
        with pytest.raises(dangerous.AcceptanceError,
                           match='NO_LOCAL_IDENTITIES_FOR_ADMIN_DISCOVERY'):
            dangerous.discover_admin_device(port=port, identity_dir=empty)
        assert {row['id'] for row in authority.devices()} == {root}
        assert authority.queue() == []


def test_nonadmin_local_identity_is_not_mistaken_for_admin(tmp_path):
    with disposable_org(tmp_path, designated_default=False) as (port, credentials, root, authority):
        request_id = dangerous.submit_identity(port, credentials, 'fixture-owner',
                                               'worker-only', 'worker')
        token, _ = _open_authenticated_session(port, credentials, root)
        try:
            assert _request(port, 'POST', '/v1/enrollment/approvals',
                            {'request_id': request_id}, token=token)['status'] == 'APPROVED'
        finally:
            _request(port, 'POST', '/v1/auth/logout', {}, token=token)
        # The authenticated worker is inspected but cannot be selected as admin.
        assert dangerous.discover_admin_device(port=port, identity_dir=credentials) == root
        only_worker_dir = tmp_path / 'worker-only-keys'
        only_worker_dir.mkdir()
        for ext in ('.key.pem', '.meta.json'):
            source = credentials / ('worker-only' + ext)
            if source.exists():
                (only_worker_dir / source.name).write_bytes(source.read_bytes())
        with pytest.raises(dangerous.AcceptanceError, match='NO_AUTHORIZED_LOCAL_ADMIN_DEVICE'):
            dangerous.discover_admin_device(port=port, identity_dir=only_worker_dir)
        assert {row['id'] for row in authority.devices()} == {root, 'worker-only'}


def test_missing_mutation_approval_still_refuses_before_admin_discovery(tmp_path, monkeypatch):
    def no_discovery(**kwargs):
        raise AssertionError('discovery must not occur without approvals')
    monkeypatch.setattr(dangerous, 'discover_admin_device', no_discovery)
    report = tmp_path / 'report.json'
    with pytest.raises(SystemExit) as exc:
        dangerous.main(['--mode', 'live', '--scope', 'identities', '--report', str(report)])
    assert exc.value.code == 2
    assert not report.exists()


def test_explicit_admin_override_remains_supported(tmp_path, monkeypatch):
    with disposable_org(tmp_path, designated_default=False) as (port, credentials, root, _):
        enroll_second_admin(port, credentials, root)
        def no_discovery(**kwargs):
            raise AssertionError('explicit selection must bypass discovery')
        monkeypatch.setattr(dangerous, 'discover_admin_device', no_discovery)
        report = tmp_path / 'explicit-report.json'
        assert dangerous.main([
            '--mode', 'live', '--scope', 'identities', '--admin-device', root,
            '--local-port', str(port), '--identity-dir', str(credentials),
            '--execute-mutations', '--acknowledge-real-mutations',
            '--report', str(report),
        ]) == 0
        data = json.loads(report.read_text(encoding='utf-8'))
        assert data['status'] == 'PASS_LIVE_MUTATIONS'
        assert data['groups']['identities']['administrator_device'] == root
        assert 'administrator_discovery' not in data


def test_default_admin_avoids_probing_unrelated_keys(tmp_path, monkeypatch):
    with disposable_org(tmp_path) as (port, credentials, root, _):
        # An unrelated local key is not proof of organizational admission.
        save_identity(credentials, 'unrelated-key')
        original_request = dangerous._request
        def guarded(port_number, method, path, body=None, token=None):
            if path == '/v1/auth/challenge' and body and body['device_id'] == 'unrelated-key':
                raise AssertionError('default admin should be preferred without probing other keys')
            return original_request(port_number, method, path, body, token)
        monkeypatch.setattr(dangerous, '_request', guarded)
        assert dangerous.discover_admin_device(port=port, identity_dir=credentials) == root


def test_unregistered_local_identity_is_skipped_during_discovery(tmp_path):
    with disposable_org(tmp_path, designated_default=False) as (port, credentials, root, _):
        # Sorted before fixture-root, but has no known organization enrollment.
        save_identity(credentials, 'aaa-unregistered')
        assert dangerous.discover_admin_device(port=port, identity_dir=credentials) == root


def test_unexpected_authentication_error_fails_closed(tmp_path, monkeypatch):
    with disposable_org(tmp_path, designated_default=False) as (port, credentials, _, _):
        original_request = dangerous._request
        def reject(port_number, method, path, body=None, token=None):
            if path == '/v1/auth/challenge':
                raise RuntimeError('SLH returned 503 (SERVICE_UNAVAILABLE)')
            return original_request(port_number, method, path, body, token)
        monkeypatch.setattr(dangerous, '_request', reject)
        with pytest.raises(RuntimeError, match='SERVICE_UNAVAILABLE'):
            dangerous.discover_admin_device(port=port, identity_dir=credentials)


def test_real_lms_authenticator_autodiscovers_without_identity_dir_and_completes_mutations(tmp_path, monkeypatch):
    """Regression for real installations: LMS owns keys, not the CLI default directory."""
    with disposable_org(tmp_path, lms_signer=True) as (port, credentials, root, authority):
        def legacy_dir_forbidden(*args, **kwargs):
            raise AssertionError('live discovery must not read the unrelated user credential directory')
        monkeypatch.setattr(dangerous, 'LocalIdentityStore', legacy_dir_forbidden)
        report = tmp_path / 'lms-live.json'
        assert dangerous.main([
            '--mode', 'live', '--scope', 'identities',
            '--local-port', str(port),
            '--execute-mutations', '--acknowledge-real-mutations',
            '--report', str(report),
        ]) == 0
        data = json.loads(report.read_text(encoding='utf-8'))
        assert data['status'] == 'PASS_LIVE_MUTATIONS'
        assert data['administrator_discovery'] == {
            'status': 'VERIFIED', 'device_id': root,
            'basis': 'lms-local-authenticator-signed-session-and-admin-directory'}
        assert data['groups']['identities']['cleanup'] == 'VERIFIED_REMOVED'
        assert {r['id'] for r in authority.devices()} == {root}
        assert [r['device_id'] for r in LocalIdentityStore(credentials).list()['identities']] == [root]


def test_real_lms_authenticator_prefers_verified_default_without_unrelated_login(tmp_path, monkeypatch):
    with disposable_org(tmp_path, lms_signer=True) as (port, credentials, root, _):
        other = enroll_second_admin(port, credentials, root)
        original_request = dangerous._request
        def check_default_first(p, method, path, body=None, token=None):
            if path == '/v1/auth/challenge' and body and body.get('device_id') == other:
                raise AssertionError('no need to challenge secondary administrator')
            return original_request(p, method, path, body, token)
        monkeypatch.setattr(dangerous, '_request', check_default_first)
        assert dangerous.discover_admin_device(port=port, source='lms') == root


def test_real_lms_authenticator_no_admin_refuses_before_mutation(tmp_path, monkeypatch):
    with disposable_org(tmp_path, lms_signer=True) as (port, credentials, root, authority):
        original_request = dangerous._request
        def empty_local_inventory(p, method, path, body=None, token=None):
            if path == '/v1/local/authenticator/identities':
                return {'identities': [], 'invalid_identities': [], 'default_device_id': None}
            return original_request(p, method, path, body, token)
        monkeypatch.setattr(dangerous, '_request', empty_local_inventory)
        report = tmp_path / 'empty-lms.json'
        assert dangerous.main([
            '--mode', 'live', '--scope', 'identities',
            '--local-port', str(port),
            '--execute-mutations', '--acknowledge-real-mutations',
            '--report', str(report),
        ]) == 4
        data = json.loads(report.read_text(encoding='utf-8'))
        assert data['failed_gate'] == 'NO_LMS_IDENTITIES_FOR_ADMIN_DISCOVERY'
        assert {r['id'] for r in authority.devices()} == {root}


def test_real_lms_authenticator_signer_mismatch_fails_closed(tmp_path, monkeypatch):
    with disposable_org(tmp_path, lms_signer=True) as (port, credentials, root, authority):
        original_request = dangerous._request
        def corrupt_signature(p, method, path, body=None, token=None):
            result = original_request(p, method, path, body, token)
            if path == '/v1/local/authenticator/identities/' + root + '/sign':
                return {**result, 'fingerprint': '0' * 64}
            return result
        monkeypatch.setattr(dangerous, '_request', corrupt_signature)
        with pytest.raises(dangerous.AcceptanceError, match='LMS_ADMIN_SIGNATURE_UNVERIFIED'):
            dangerous.discover_admin_device(port=port, source='lms')
        assert {r['id'] for r in authority.devices()} == {root}


def test_real_lms_authenticator_ambiguity_without_designation_refused(tmp_path):
    with disposable_org(tmp_path, designated_default=False, lms_signer=True) as (port, credentials, root, authority):
        enroll_second_admin(port, credentials, root)
        baseline = {r['id'] for r in authority.devices()}
        with pytest.raises(dangerous.AcceptanceError,
                           match='MULTIPLE_ADMIN_DEVICES_REQUIRE_EXPLICIT_SELECTION'):
            dangerous.discover_admin_device(port=port, source='lms')
        assert {r['id'] for r in authority.devices()} == baseline


def test_explicit_admin_device_uses_lms_signer_without_legacy_directory(tmp_path, monkeypatch):
    with disposable_org(tmp_path, lms_signer=True) as (port, _credentials, root, authority):
        def forbid_filesystem_credentials(*args, **kwargs):
            raise AssertionError('no legacy files may be opened by the runner')
        monkeypatch.setattr(dangerous, 'LocalIdentityStore', forbid_filesystem_credentials)
        report = tmp_path / 'explicit-lms.json'
        assert dangerous.main([
            '--mode', 'live', '--scope', 'identities',
            '--local-port', str(port), '--admin-device', root,
            '--execute-mutations', '--acknowledge-real-mutations',
            '--report', str(report),
        ]) == 0
        data = json.loads(report.read_text(encoding='utf-8'))
        assert data['status'] == 'PASS_LIVE_MUTATIONS'
        assert 'administrator_discovery' not in data
        assert {r['id'] for r in authority.devices()} == {root}
