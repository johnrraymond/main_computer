"""SEH Coolify identity/harness backend, not MCEL or live Coolify acceptance."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import threading
from http.client import HTTPConnection

import pytest

from main_computer.slh.api import SLHAPI
from main_computer.slh.seh_deployment_management import (
    SLHSEHDeploymentManager, SEHManagementRefused, management_request,
)
from main_computer.slh.organization_service import OrganizationHTTPServer, OrganizationHTTPHandler
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import prepare_private_state_bootstrap, install_verified_private_state
from tools.seh_control import identity as st


TOKEN = 'PRIVATE-COOLIFY-TEST-TOKEN-NOT-FOR-HTTP-OUTPUT'


def root_state(tmp_path):
    root = tmp_path / 'runtime' / 'state'
    content = {'kind': 'main_computer.mother.private_state.v1', 'schema_version': 1,
               'networks': {'mainnet': {'coolify': {'controllers': {
                   'coolify-a': {'url': 'https://coolify-a.example.org', 'api_token': TOKEN}
               }}, 'foundationdb': {'keep': 'untouched'}}}}
    paths = MotherPaths(runtime_state_root=root).resolve_private_state_paths()
    op = OperationIdentity('coolify-fixture', 'coolify-fixture', 'mainnet', 'MOTHER-OP-IDENTITY-ROTATION')
    closure = prepare_private_state_bootstrap(paths, content,
                 updated_at='2026-11-12T00:00:00Z', updated_by_action_id='fixture', operation=op)
    install_verified_private_state(paths, closure, None, operation=op)
    return root


def _register(manager, *, generation=1, name='coolify-b', url='https://coolify-b.example.org', token=TOKEN):
    return manager.register({'name': name, 'url': url, 'api_token': token,
                             'expected_generation': generation, 'operation_id': 'register-test'})


def _select(manager, name='coolify-b', generation=2):
    return manager.select({'name': name, 'expected_generation': generation,
                           'operation_id': 'select-test'})


def test_registration_and_selection_use_verified_mother_and_never_expose_token(tmp_path):
    root = root_state(tmp_path)
    manager = SLHSEHDeploymentManager(root=root)
    before = manager.inspect({})
    assert before['generation'] == 1 and before['deployment'] is None
    assert {x['identity'] for x in before['controllers']} == {'coolify-a'}
    registered = _register(manager)
    assert registered['status'] == 'COMMITTED' and registered['generation'] == 2
    assert registered['remote_mutation'] is False
    selected = _select(manager)
    assert selected['status'] == 'COMMITTED' and selected['generation'] == 3
    assert selected['remote_mutation'] is False
    _, state, document = st.read(root, 'mainnet')
    record = st.deployment_record(document, 'mainnet')
    assert record['coolify']['source'] == 'networks.mainnet.coolify.controllers.coolify-b'
    assert record['coolify']['controller']['api_token'] == TOKEN
    assert record['deployment'] == {'enabled': False}
    assert document['networks']['mainnet']['foundationdb'] == {'keep': 'untouched'}
    public = manager.inspect({})
    assert public['generation'] == 3
    assert public['deployment']['coolify']['source'].endswith('.coolify-b')
    assert TOKEN not in json.dumps(public)
    assert TOKEN not in json.dumps(registered)
    assert TOKEN not in json.dumps(selected)
    assert state.binding.generation == 3


def test_duplicate_stale_and_plaintext_registration_never_mutate(tmp_path):
    root = root_state(tmp_path)
    manager = SLHSEHDeploymentManager(root=root)
    with pytest.raises(SEHManagementRefused, match='COOLIFY_REQUIRES_HTTPS_ORIGIN'):
        _register(manager, url='http://coolify-b.example.org')
    with pytest.raises(SEHManagementRefused, match='INVALID_COOLIFY_ORIGIN'):
        _register(manager, url='https://[invalid')
    assert st.read(root, 'mainnet')[1].binding.generation == 1
    _register(manager)
    with pytest.raises(SEHManagementRefused):
        _register(manager, generation=2)
    assert st.read(root, 'mainnet')[1].binding.generation == 2
    with pytest.raises(SEHManagementRefused):
        _select(manager, generation=1)
    assert st.read(root, 'mainnet')[1].binding.generation == 2


def test_selection_idempotent_and_deployed_retarget_refused(tmp_path):
    root = root_state(tmp_path)
    manager = SLHSEHDeploymentManager(root=root)
    _select(manager, name='coolify-a', generation=1)
    assert _select(manager, name='coolify-a', generation=2)['status'] == 'UNCHANGED'
    _register(manager, generation=2)
    st.mutate(root, 'mainnet', 3, 'record-existing-service', lambda doc, *_:
              doc['networks']['mainnet']['seh_deployment']['deployment'].update(
                  application_uuid='exact-app-uuid'))
    with pytest.raises(SEHManagementRefused):
        _select(manager, name='coolify-b', generation=4)
    assert st.read(root, 'mainnet')[1].binding.generation == 4
    assert st.deployment_record(st.read(root, 'mainnet')[2], 'mainnet')['coolify']['source'].endswith('.coolify-a')


def test_harness_reuses_operator_entry_point_and_mutations_require_exact_approval(tmp_path):
    root = root_state(tmp_path)
    captured = []
    def runner(args, *, control):
        captured.append(args)
        return {'status': 'PLAN_ONLY', 'application_uuid': 'existing-uuid',
                'private_key': 'fake-secret-not-exposed'}
    manager = SLHSEHDeploymentManager(root=root, harness_runner=runner)
    initial = {'action': 'redeploy-runtime', 'execute': False,
               'expected_generation': None, 'confirm': None}
    result = manager.harness(initial)
    assert result['status'] == 'PLAN_ONLY' and 'private_key' not in result
    assert captured[-1].runtime_state_root == root
    assert captured[-1].execute_mutations is False
    with pytest.raises(SEHManagementRefused, match='SEH_MUTATION_CONFIRMATION_REQUIRED'):
        manager.harness({**initial, 'execute': True, 'expected_generation': 1})
    with pytest.raises(SEHManagementRefused, match='SEH_GENERATION_CONFLICT'):
        manager.harness({**initial, 'execute': True, 'expected_generation': 999,
                         'confirm': 'REDEPLOY_RUNTIME'})
    assert len(captured) == 1
    result = manager.harness({**initial, 'execute': True, 'expected_generation': 1,
                              'confirm': 'REDEPLOY_RUNTIME'})
    assert result['status'] == 'PLAN_ONLY' and captured[-1].execute_mutations is True
    assert captured[-1].yes_i_know_this_mutates_seh is True
    for action in ('deploy','undeploy'):
        with pytest.raises(SEHManagementRefused, match='SEH_HARNESS_OPERATION_NOT_AVAILABLE'):
            manager.harness({**initial, 'action': action})


class _Authority:
    def authenticate(self, token, *, admin=False):
        from main_computer.slh.authority import SLHError
        if not admin or token != 'good':
            raise SLHError('UNAUTHORIZED', 401)
        return {'user_id': 'owner', 'device_id': 'owner-device', 'role': 'admin'}


def test_local_api_requires_administrator_and_loopback(tmp_path):
    manager = SLHSEHDeploymentManager(root=root_state(tmp_path))
    api = SLHAPI(_Authority(), seh_deployment_manager=manager)
    for token, loopback, expected in [('', True, 401), ('good', False, 403), ('good', True, 200)]:
        code, reply = api.call('GET', '/v1/local/seh/deployment', token=token, loopback=loopback)
        assert code == expected, reply
    request = {'name': 'coolify-b', 'url': 'https://coolify-b.example.org',
               'api_token': TOKEN, 'expected_generation': 1, 'operation_id': 'add-coolify'}
    assert api.call('POST', '/v1/local/seh/deployment/controllers', request, token='good', loopback=True)[0] == 200
    assert manager.inspect({})['generation'] == 2
    assert api.call('POST', '/v1/local/seh/deployment/controllers', request, token='', loopback=True)[0] == 401
    assert manager.inspect({})['generation'] == 2


def _http(port, method, path, body=None, *, bearer=''):
    conn = HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        headers = {'Content-Type': 'application/json'}
        if bearer:
            headers['Authorization'] = 'Bearer ' + bearer
        conn.request(method, path, body=json.dumps(body or {}), headers=headers)
        r = conn.getresponse()
        return r.status, json.loads(r.read())
    finally:
        conn.close()


def test_direct_org_management_available_but_same_request_relay_denied(tmp_path):
    manager = SLHSEHDeploymentManager(root=root_state(tmp_path))
    server = OrganizationHTTPServer(('127.0.0.1', 0), OrganizationHTTPHandler)
    server.seh_deployment_manager = manager
    server.authority = _Authority()
    server.seh_identity_only = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        path = '/v1/org/admin/seh/deployment'
        assert _http(server.server_port, 'GET', path)[0] == 401
        code, data = _http(server.server_port, 'GET', path, bearer='good')
        assert code == 200 and data['generation'] == 1
        server.seh_identity_only = True
        code, data = _http(server.server_port, 'GET', path, bearer='good')
        assert code == 403 and data['error']['code'] == 'SEH_OPERATION_NOT_ALLOWED'
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_router_cannot_accept_caller_selected_state_paths(tmp_path):
    manager = SLHSEHDeploymentManager(root=root_state(tmp_path))
    for payload in ({'runtime_state_root': '/tmp/arbitrary'}, {'network': 'testnet'}):
        with pytest.raises(SEHManagementRefused, match='INVALID_SEH_MANAGEMENT_REQUEST'):
            management_request(manager, 'GET', '/v1/local/seh/deployment', payload)
    with pytest.raises(SEHManagementRefused, match='SEH_MANAGEMENT_ROUTE_NOT_FOUND'):
        management_request(manager, 'POST', '/v1/local/seh/deployment/delete-all', {})


def test_actual_harness_inspection_uses_the_selected_canonical_identity(tmp_path):
    root = root_state(tmp_path)
    manager = SLHSEHDeploymentManager(root=root)
    request = {'action': 'inspect', 'execute': False,
               'expected_generation': None, 'confirm': None}
    empty = manager.harness(request)
    assert empty['registered'] is False and empty['status'] == 'READ_ONLY'
    _select(manager, name='coolify-a', generation=1)
    selected = manager.harness(request)
    assert selected['registered'] is True and selected['status'] == 'READ_ONLY'
    assert selected['coolify']['source'].endswith('.coolify-a')
    assert TOKEN not in json.dumps(selected)
    assert selected['deployment'] == {'enabled': False}


def test_incomplete_legacy_state_cannot_be_replaced_by_new_controller(tmp_path):
    root = root_state(tmp_path)
    def legacy(doc, *_):
        doc['networks']['mainnet']['sehs'] = {'old': {'incomplete': 'do not adopt'}}
    st.mutate(root, 'mainnet', 1, 'legacy-fixture', legacy)
    manager = SLHSEHDeploymentManager(root=root)
    with pytest.raises(SEHManagementRefused):
        _select(manager, name='coolify-a', generation=2)
    assert st.read(root, 'mainnet')[1].binding.generation == 2


def test_selection_refused_when_prepared_plan_exists(tmp_path):
    from tools.seh_control.git_deployment import _plan_path
    root = root_state(tmp_path)
    manager = SLHSEHDeploymentManager(root=root)
    _select(manager, name='coolify-a', generation=1)
    _register(manager, generation=2)
    path = _plan_path(root, 'mainnet')
    path.parent.mkdir(parents=True)
    path.write_text('draft', encoding='utf-8')
    with pytest.raises(SEHManagementRefused):
        _select(manager, name='coolify-b', generation=3)
    assert st.read(root, 'mainnet')[1].binding.generation == 3
