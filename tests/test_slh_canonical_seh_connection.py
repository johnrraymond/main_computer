"""Patch 4: sole verified Mother deployment -> resident automatic SEH tunnel."""
from __future__ import annotations

import base64
import socket
import time
from copy import deepcopy
from unittest.mock import patch

import pytest

from main_computer.slh.seh_autoconnect import SEHAutoConnection, SEHConnectionSettings
from main_computer.slh.seh_canonical_connection import (
    SEHConnectionResolutionError, resolve_canonical_connection)
from main_computer.seh.relay import SEHRelay
from tools.seh_control import git_deployment as git
from tools.slh_seh_connector import service_identity


UUID = 'itr2ynyxjs4iy6dsj4fvekmn'

def _enable_seh_for_resolver(state_dir):
    from main_computer.slh.organization_state import save_config, DEFAULTS
    save_config(state_dir, {**DEFAULTS, 'configured': True, 'enabled': True,
                            'activation_confirmed': True, 'workers_enabled': True,
                            'seh_enabled': True})



class FakeCoolify:
    def __init__(self, public_key):
        self.application = {
            'uuid': UUID, 'name': git.CANONICAL_APPLICATION_NAME,
            'description': git.CANONICAL_DESCRIPTION,
            'project_uuid': 'proj1', 'environment_uuid': 'env1',
            'server_uuid': 'server1', 'environment_name': 'mainnet-hubs',
            'git_repository': 'https://github.com/johnrraymond/main_computer',
            'dockerfile_location': '/deploy/seh/Dockerfile',
            'domains': 'https://external.greatlibrary.io:8080',
        }
        self.envs = [
            {'key': 'SEH_SLH_PUBLIC_KEY_B64', 'value': public_key},
            {'key': 'SEH_SLH_ID', 'value': 'existing-route'},
        ]
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        assert method == 'GET', 'No remote mutation permitted during connection resolution'
        if path == f'/api/v1/applications/{UUID}':
            return deepcopy(self.application)
        if path == '/api/v1/projects/proj1/environments':
            return [{'uuid': 'env1', 'name': 'mainnet-hubs'}]
        if path == f'/api/v1/applications/{UUID}/envs':
            return deepcopy(self.envs)
        raise AssertionError('Unexpected Coolify read: ' + path)


def verified(root):
    return ({'coolify': {'controller': {'enabled': True, 'id':'unit-controller'}},
             'deployment': {'enabled': True, 'application_uuid': UUID,
                            'git': {'project_uuid': 'proj1', 'environment_uuid': 'env1',
                                    'server_uuid': 'server1'}}},
            {'url': 'http://127.0.0.1:8000/', 'api_token': 'not-used-in-tests'},
            32, 'hash-of-verified-mother-state')


def test_resolves_only_existing_uuid_public_origin_and_matching_key(tmp_path):
    private, public = service_identity(tmp_path / 'slh', create=True)
    _enable_seh_for_resolver(tmp_path / 'slh')
    remote = FakeCoolify(public)
    with patch.object(git, '_canonical_selection', side_effect=lambda root, net: verified(root)) as selection:
        settings = resolve_canonical_connection(tmp_path / 'slh',
                                                 mother_root=tmp_path / 'mother', client=remote)
    assert settings == SEHConnectionSettings(True, 'wss://external.greatlibrary.io',
                                             'existing-route', False, 2.0)
    assert selection.call_count == 2
    assert all(method == 'GET' for method, _ in remote.calls)
    assert (tmp_path / 'slh' / 'seh-service-signing-key.pem').is_file()
    assert not (tmp_path / 'slh' / 'seh-connection.json').exists()
    # The enabled configuration was pre-created by this fixture. Resolution
    # may not generate any other organization identity artifacts.
    assert [p.name for p in (tmp_path / 'slh').glob('*organization*')] == ['organization-service.json']


def test_masked_coolify_value_with_valid_real_value_is_supported(tmp_path):
    _, public = service_identity(tmp_path / 'slh', create=True)
    _enable_seh_for_resolver(tmp_path / 'slh')
    remote = FakeCoolify(public)
    remote.envs[0]['value'] = '********'
    remote.envs[0]['real_value'] = public
    with patch.object(git, '_canonical_selection', side_effect=lambda root, net: verified(root)):
        settings = resolve_canonical_connection(tmp_path / 'slh', mother_root=tmp_path,
                                                 client=remote)
    assert settings.enabled and settings.seh_url == 'wss://external.greatlibrary.io'


@pytest.mark.parametrize('corruption', ['key', 'missing_key', 'different_uuid',
                                      'different_domain', 'missing_route',
                                      'organization_field', 'changed_mother',
                                      'different_repository', 'disabled'])
def test_untrusted_or_changed_canonical_target_refused_without_mutation(tmp_path, corruption):
    _, public = service_identity(tmp_path / 'slh', create=True)
    remote = FakeCoolify(public)
    if corruption == 'key':
        remote.envs[0]['value'] = 'not-matching'
    if corruption == 'missing_key':
        remote.envs.pop(0)
    if corruption == 'different_uuid':
        remote.application['uuid'] = 'some-other-application'
    if corruption == 'different_domain':
        remote.application['domains'] = 'https://external.greatlibrary.io,https://attacker.example'
    if corruption == 'missing_route':
        remote.envs.pop(1)
    if corruption == 'organization_field':
        remote.envs.append({'key': 'SEH_ORGANIZATION_ID', 'value': 'obsolete'})
    if corruption == 'different_repository':
        remote.application['git_repository'] = 'https://github.com/other/repo'
    calls = [0]
    def selection(root, network):
        calls[0] += 1
        result = verified(root)
        if corruption == 'disabled':
            result[0]['deployment']['enabled'] = False
        if corruption == 'changed_mother' and calls[0] > 1:
            return result[:2] + (33, 'different')
        return result
    with patch.object(git, '_canonical_selection', side_effect=selection):
        with pytest.raises((SEHConnectionResolutionError, git.SEHGitError)):
            resolve_canonical_connection(tmp_path / 'slh', mother_root=tmp_path / 'mother', client=remote)
    assert all(method == 'GET' for method, _ in remote.calls)
    assert not (tmp_path / 'slh' / 'seh-connection.json').exists()


def wait_for(predicate, seconds=7):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.05)
    return False


def test_resolved_tunnel_retries_authenticates_and_recovers_without_written_alias(tmp_path):
    _, public = service_identity(tmp_path / 'slh', create=True)
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    settings = SEHConnectionSettings(True, f'ws://127.0.0.1:{port}',
                                     'existing-route', True, .15)
    iterations = []
    def resolver(directory):
        iterations.append(1)
        return settings
    supervisor = SEHAutoConnection(tmp_path / 'slh', gateway_port=1,
                                    settings_resolver=resolver).start()
    relay = None
    try:
        assert wait_for(lambda: supervisor.failures >= 1), 'offline retries must be supervised'
        relay = SEHRelay(slh_id='existing-route',
                         slh_public_key=base64.b64decode(public).decode('ascii'),
                         host='127.0.0.1', port=port).start()
        assert wait_for(lambda: supervisor.connected and relay.tunnel_ready)
        with supervisor._lock:
            previous = supervisor._current
        previous.stop()  # A disposable lab tunnel drop; never a production API.
        assert wait_for(lambda: supervisor.connections >= 2 and supervisor.connected and relay.tunnel_ready)
        assert len(iterations) >= 3
        assert not (tmp_path / 'slh' / 'seh-connection.json').exists()
    finally:
        supervisor.stop()
        if relay is not None:
            relay.stop()
    assert not supervisor.connected


def test_gateway_owns_canonical_connection_without_any_standalone_process(tmp_path):
    """Exercise the actual resident managed-worker gateway startup boundary."""
    from main_computer.slh.authenticator import generate_identity, sign_object
    from main_computer.slh.authority import SLHAuthority
    from main_computer.slh.managed_gateway import SLHManagedGateway
    from main_computer.slh.seh_autoconnect import write_seh_settings

    slh = tmp_path / 'slh'
    _, public_b64 = service_identity(slh, create=True)
    authority = SLHAuthority(slh)
    admin_signer, admin_public = generate_identity()
    statement = {'version': 1, 'action': 'slh.admin.bootstrap',
                 'admin_id': 'owner', 'device_id': 'admin-pc', 'public_key': admin_public}
    authority.bootstrap({**statement, 'proof': sign_object(admin_signer, statement),
                         'bootstrap_secret': (slh / 'bootstrap.secret').read_text(),
                         'admin_name': 'Owner'}, loopback=True)
    relay = SEHRelay(slh_id='existing-route',
                     slh_public_key=base64.b64decode(public_b64).decode('ascii'),
                     host='127.0.0.1', port=0).start()
    settings = SEHConnectionSettings(True, f'ws://127.0.0.1:{relay.port}',
                                     'existing-route', True, .15)
    gateway = SLHManagedGateway(authority, host='127.0.0.1', port=0, allow_seh=True)
    try:
        with patch('main_computer.slh.seh_canonical_connection.resolve_canonical_connection',
                   return_value=settings) as resolved:
            gateway.start_background()
            assert wait_for(lambda: gateway.seh_connection is not None and
                            gateway.seh_connection.connected and relay.tunnel_ready)
            assert resolved.call_count >= 1
            assert not (slh / 'seh-connection.json').exists()
    finally:
        gateway.stop()
        relay.stop()


def test_explicit_disabled_settings_never_fall_back_to_remote_canonical(tmp_path):
    from main_computer.slh.seh_autoconnect import read_seh_settings, write_seh_settings
    from main_computer.slh.authenticator import generate_identity, sign_object
    from main_computer.slh.authority import SLHAuthority
    from main_computer.slh.managed_gateway import SLHManagedGateway

    slh = tmp_path / 'slh'
    service_identity(slh, create=True)
    auth = SLHAuthority(slh)
    signer, pub = generate_identity()
    stmt = {'version': 1, 'action': 'slh.admin.bootstrap',
            'admin_id': 'owner', 'device_id': 'admin-pc', 'public_key': pub}
    auth.bootstrap({**stmt, 'proof': sign_object(signer, stmt),
                    'bootstrap_secret': (slh / 'bootstrap.secret').read_text(),
                    'admin_name': 'Owner'}, loopback=True)
    disabled = SEHConnectionSettings(False, 'ws://127.0.0.1:18080', 'existing-route', True, .2)
    write_seh_settings(slh, disabled)
    gateway = SLHManagedGateway(auth, host='127.0.0.1', port=0, allow_seh=True)
    try:
        with patch('main_computer.slh.seh_canonical_connection.resolve_canonical_connection',
                   side_effect=AssertionError('disabled config must not trigger remote lookups')) as resolver:
            gateway.start_background()
            assert wait_for(lambda: gateway._server is not None)
            assert gateway.seh_connection is None
            resolver.assert_not_called()
            assert read_seh_settings(slh) == disabled
    finally:
        gateway.stop()


def test_canonical_diagnostic_reports_trust_only_not_fabricated_live_readiness(tmp_path):
    from seh_mutate_harness import _parser, run
    from main_computer.slh.storage import slh_state_dir
    from tools.seh_control.commands import SEHControl

    class ReadOnlyControl:
        root = tmp_path
        def list(self, network):
            return {'status': 'READ_ONLY', 'generation': 32,
                    'deployment': {'deployment': {'application_uuid': UUID}}}
    resolved = SEHConnectionSettings(True, 'wss://external.greatlibrary.io',
                                     'existing-route', False, 2.0)
    with patch('main_computer.slh.seh_canonical_connection.resolve_canonical_connection',
               return_value=resolved) as resolver:
        result = run(_parser().parse_args(['diagnostic']), control=ReadOnlyControl())
    assert result['status'] == 'READ_ONLY'
    assert result['application_uuid'] == UUID
    assert result['signing_key_trust'] == 'MATCHED'
    assert result['resident_tunnel'] == 'NOT_OBSERVED_BY_THIS_DIAGNOSTIC'
    assert 'slh_id' not in result and 'organization_id' not in result
    resolver.assert_called_once()


def test_diagnostic_can_check_trust_while_deployment_is_disabled(tmp_path):
    """Read-only preflight must not activate a disabled deployment."""
    from seh_mutate_harness import _parser, run

    _, public = service_identity(tmp_path / 'slh', create=True)
    remote = FakeCoolify(public)

    def disabled_selection(root, network):
        record, controller, generation, binding = verified(root)
        record['deployment']['enabled'] = False
        return record, controller, generation, binding

    class ReadOnlyControl:
        root = tmp_path / 'mother'

        def list(self, network):
            return {'status': 'READ_ONLY', 'generation': 32,
                    'deployment': {'deployment': {'application_uuid': UUID, 'enabled': False}}}

    with patch.object(git, '_canonical_selection', side_effect=disabled_selection), \
         patch.object(git, '_client', return_value=remote), \
         patch('main_computer.slh.storage.slh_state_dir', return_value=tmp_path / 'slh'):
        result = run(_parser().parse_args(['diagnostic']), control=ReadOnlyControl())
    assert result['status'] == 'READ_ONLY'
    assert result['deployment_enabled'] is False
    assert result['signing_key_trust'] == 'MATCHED'
    assert result['resident_tunnel'] == 'NOT_OBSERVED_BY_THIS_DIAGNOSTIC'
    assert all(method == 'GET' for method, _ in remote.calls)
    assert not (tmp_path / 'slh' / 'seh-connection.json').exists()


def test_disabled_deployment_diagnostic_never_bypasses_trust(tmp_path):
    """Disabling activation must not waive the identity/trust checks."""
    from seh_mutate_harness import _parser, run

    _, public = service_identity(tmp_path / 'slh', create=True)
    remote = FakeCoolify(public)
    _, unrelated_public = service_identity(tmp_path / 'unrelated-slh', create=True)
    remote.envs[0]['value'] = unrelated_public

    def disabled_selection(root, network):
        record, controller, generation, binding = verified(root)
        record['deployment']['enabled'] = False
        return record, controller, generation, binding

    class ReadOnlyControl:
        root = tmp_path / 'mother'

        def list(self, network):
            return {'status': 'READ_ONLY', 'generation': 32,
                    'deployment': {'deployment': {'application_uuid': UUID, 'enabled': False}}}

    with patch.object(git, '_canonical_selection', side_effect=disabled_selection), \
         patch.object(git, '_client', return_value=remote), \
         patch('main_computer.slh.storage.slh_state_dir', return_value=tmp_path / 'slh'):
        result = run(_parser().parse_args(['diagnostic']), control=ReadOnlyControl())
    assert result['status'] == 'REFUSED'
    assert result['reason'] == 'Remote SEH public-key trust differs from preserved SLH identity'
    assert all(method == 'GET' for method, _ in remote.calls)
