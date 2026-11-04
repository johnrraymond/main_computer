"""Patch 3: registered-UUID-only Coolify metadata, with operator-approved HTTP."""
from __future__ import annotations

from copy import deepcopy
import json
import pytest

from seh_mutate_harness import _parser, run, HarnessError
from tools.seh_control import git_deployment as g, identity as st
from tools.seh_control.commands import SEHControl
from tests.test_seh_single_deployment_migration import populated, APP, TOKEN

PROJECT = 'vbja82onq0xpuzy6mwi3v11r'
ENV = 'xgkuuosdiwxacky2btgsoz0h'
SERVER = 'xsuyj7sxd0kymhhyz2ctcwl4'
LEGACY_NAME = 'mc-seh-company-primary'
LEGACY_DESCRIPTION = 'main-computer-seh:git-v3 target=company-primary slh=my-slh'


def installed(tmp_path, *, secure=True):
    root, _ = populated(tmp_path)
    if secure:
        def change(doc, gen, base):
            net = doc['networks']['mainnet']
            net['coolify']['controllers']['coolify-c']['url'] = 'https://coolify.example.org/'
            net['sehs']['company-primary']['coolify']['identities']['primary']['controller']['url'] = 'https://coolify.example.org/'
        st.mutate(root, 'mainnet', 1, 'secure-test-controller', change)
    state = st.read(root, 'mainnet')[1]
    SEHControl(root).migrate_single('mainnet', state.binding.generation, 'single-record')
    return root


class FakeCoolify:
    def __init__(self):
        self.app = {
            'uuid': APP, 'name': LEGACY_NAME, 'description': LEGACY_DESCRIPTION,
            'project_uuid': PROJECT, 'environment_uuid': ENV, 'server_uuid': SERVER,
            'environment_name': 'mainnet-hubs',
            'git_repository': 'https://github.com/johnrraymond/main_computer',
            'dockerfile_location': '/deploy/seh/Dockerfile',
            'domains': 'https://external.greatlibrary.io:8080',
        }
        self.trust = [
            {'key': 'SEH_SLH_PUBLIC_KEY_B64', 'value': 'EXISTING-KEY-UNTOUCHED', 'uuid': 'env-one'},
            {'key': 'SEH_SLH_ID', 'value': 'legacy-id', 'uuid': 'env-two'},
        ]
        self.calls = []
        self.application_mutations = []
        self.environment_deletions = []

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        if method == 'GET' and path == f'/api/v1/applications/{APP}':
            return deepcopy(self.app)
        if method == 'GET' and path == f'/api/v1/projects/{PROJECT}/environments':
            return [{'uuid': ENV, 'name': 'mainnet-hubs'}]
        if method == 'GET' and path == f'/api/v1/applications/{APP}/envs':
            return deepcopy(self.trust)
        if method == 'DELETE' and path.startswith(f'/api/v1/applications/{APP}/envs/'):
            uuid = path.rsplit('/', 1)[-1]
            matches = [row for row in self.trust if row.get('uuid') == uuid]
            assert len(matches) == 1
            self.trust.remove(matches[0])
            self.environment_deletions.append(uuid)
            return {'ok': True}
        if method == 'PATCH' and path == f'/api/v1/applications/{APP}':
            assert set(body) == {'name', 'description'}
            self.application_mutations.append(deepcopy(body))
            self.app.update(body)
            return {'ok': True}
        raise AssertionError(f'Unexpected Coolify operation: {method} {path}')


def test_isolated_metadata_reconciliation_preserves_uuid_trust_generation_and_domain(tmp_path):
    root = installed(tmp_path)
    remote = FakeCoolify()
    _, before, doc = st.read(root, 'mainnet')
    original = deepcopy(doc)
    existing_trust = deepcopy(remote.trust)
    planned = g.reconcile_metadata(root, 'mainnet', client=remote)
    assert planned['status'] == 'PLAN_ONLY' and planned['application_uuid'] == APP
    assert planned['remote_mutation'] is False
    assert all(op == 'GET' for op, _ in remote.calls)
    actual = g.reconcile_metadata(root, 'mainnet', execute=True, client=remote)
    assert actual['status'] == 'METADATA_RECONCILED'
    assert actual['application_uuid'] == APP and actual['trust_untouched']
    assert remote.application_mutations == [{'name': g.CANONICAL_APPLICATION_NAME,
                                             'description': g.CANONICAL_DESCRIPTION}]
    assert remote.app['domains'] == 'https://external.greatlibrary.io:8080'
    assert remote.trust == existing_trust
    assert st.read(root, 'mainnet')[2] == original
    assert st.read(root, 'mainnet')[1].binding == before.binding
    already = g.reconcile_metadata(root, 'mainnet', execute=True, client=remote)
    assert already['status'] == 'UNCHANGED'
    assert len(remote.application_mutations) == 1
    assert ('POST', '/api/v1/applications/public') not in remote.calls


def test_http_management_origin_is_operator_accepted_without_changing_target(tmp_path):
    root = installed(tmp_path, secure=False)
    remote = FakeCoolify()
    _, before, original = st.read(root, 'mainnet')
    planned = g.reconcile_metadata(root, 'mainnet', client=remote)
    assert planned['status'] == 'PLAN_ONLY'
    assert planned['management_transport'] == 'OPERATOR_ACCEPTED_HTTP'
    assert all(method == 'GET' for method, _ in remote.calls)
    applied = g.reconcile_metadata(root, 'mainnet', execute=True, client=remote)
    assert applied['status'] == 'METADATA_RECONCILED'
    assert applied['management_transport'] == 'OPERATOR_ACCEPTED_HTTP'
    assert applied['application_uuid'] == APP
    assert remote.application_mutations == [{'name': g.CANONICAL_APPLICATION_NAME,
                                             'description': g.CANONICAL_DESCRIPTION}]
    assert st.read(root, 'mainnet')[1].binding == before.binding
    assert st.read(root, 'mainnet')[2] == original
    # The normal client must use the configured controller origin, not rewrite
    # HTTP into HTTPS or create a different Coolify application.
    controller = original['networks']['mainnet']['seh_deployment']['coolify']['controller']
    actual_client = g._client(controller)
    assert actual_client.base_url == 'http://159.203.184.182:8000'
    assert actual_client.retries == 0


def test_management_origin_validation_still_rejects_credential_urls(tmp_path):
    root = installed(tmp_path, secure=False)
    _, _, doc = st.read(root, 'mainnet')
    ctl = dict(doc['networks']['mainnet']['seh_deployment']['coolify']['controller'])
    for value in ('ftp://coolify.example.org/', 'http://user:pass@coolify.example.org/',
                  'http://coolify.example.org:8000/api/v1', 'http://coolify.example.org/?token=unsafe'):
        ctl['url'] = value
        with pytest.raises(g.SEHGitError, match='Invalid Coolify management origin'):
            g._client(ctl)


@pytest.mark.parametrize('change', ['wrong-uuid', 'wrong-marker', 'wrong-source', 'wrong-server', 'missing-dockerfile'])
def test_untrusted_remote_candidate_never_mutated(tmp_path, change):
    root = installed(tmp_path)
    remote = FakeCoolify()
    if change == 'wrong-uuid': remote.app['uuid'] = 'different'
    if change == 'wrong-marker': remote.app['description'] = 'arbitrary'
    if change == 'wrong-source': remote.app['git_repository'] = 'https://github.com/other/repo'
    if change == 'wrong-server': remote.app['server_uuid'] = 'another-server'
    if change == 'missing-dockerfile': remote.app.pop('dockerfile_location')
    with pytest.raises(g.SEHGitError):
        g.reconcile_metadata(root, 'mainnet', execute=True, client=remote)
    assert remote.application_mutations == []


def test_duplicate_trust_field_refuses_before_patch(tmp_path):
    root = installed(tmp_path)
    remote = FakeCoolify()
    remote.trust.append(deepcopy(remote.trust[0]))
    with pytest.raises(g.SEHGitError, match='Duplicate remote SEH trust'):
        g.reconcile_metadata(root, 'mainnet', execute=True, client=remote)
    assert remote.application_mutations == []


def test_unrelated_mother_generation_does_not_gate_metadata_reconciliation(tmp_path):
    root = installed(tmp_path)
    class MovingRemote(FakeCoolify):
        def request(self, method, path, body=None):
            if method == 'GET' and path.endswith('/envs') and not getattr(self, 'moved', False):
                self.moved = True
                st.mutate(root, 'mainnet', None, 'unrelated-concurrent-change',
                          lambda doc, gen, base: doc['networks']['mainnet'].update(test_marker=True))
            return super().request(method, path, body)
    remote = MovingRemote()
    result = g.reconcile_metadata(root, 'mainnet', execute=True, client=remote)
    assert result['status'] == 'METADATA_RECONCILED'
    assert len(remote.application_mutations) == 1


def test_cli_reconcile_metadata_refuses_alias_and_requires_both_approvals(tmp_path, monkeypatch):
    root = installed(tmp_path)
    remote = FakeCoolify()
    monkeypatch.setattr(g, '_client', lambda controller: remote)
    args = ['reconcile-metadata', '--runtime-state-root', str(root)]
    assert run(_parser().parse_args(args))['status'] == 'PLAN_ONLY'
    for extra in (['--seh', 'company-primary'], ['--host', 'coolify-c'], ['--slh-id', 'my-slh']):
        with pytest.raises(HarnessError):
            run(_parser().parse_args(args + extra))
    with pytest.raises(HarnessError):
        run(_parser().parse_args(args + ['--execute-mutations']))
    with pytest.raises(HarnessError):
        run(_parser().parse_args(args + ['--yes-i-know-this-mutates-seh']))
    result = run(_parser().parse_args(args + ['--execute-mutations', '--yes-i-know-this-mutates-seh']))
    assert result['status'] == 'METADATA_RECONCILED'
    assert TOKEN not in json.dumps(result)


def test_canonical_plan_path_and_uuid_lookup_have_no_alias_dependency(tmp_path):
    root = installed(tmp_path)
    path = g._plan_path(root, 'mainnet')
    assert path.as_posix().endswith('deployments/mainnet/seh/git-plan.json')
    assert 'company-primary' not in str(path)
    remote = FakeCoolify()
    remote.app.update(name=g.CANONICAL_APPLICATION_NAME,
                      description=g.CANONICAL_DESCRIPTION)
    plan = {'schema': 'main-computer.seh-git-plan.v2',
            'application_uuid': APP,
            'placement': {'project_uuid': PROJECT, 'environment_uuid': ENV,
                          'server_uuid': SERVER, 'environment_name': 'mainnet-hubs'}}
    assert g._lookup(remote, plan) == (APP, True)
    assert all(op == 'GET' for op, _ in remote.calls)
    assert ('GET', '/api/v1/applications') not in remote.calls
    plan['application_uuid'] = ''
    with pytest.raises(g.SEHGitError, match='previously registered'):
        g._lookup(remote, plan)
    assert not any(op == 'POST' for op, _ in remote.calls)


@pytest.mark.parametrize('repository', [
    'https://github.com/johnrraymond/main_computer',
    'https://github.com/johnrraymond/main_computer.git',
    'https://github.com/johnrraymond/main_computer.git/',
    'HTTPS://GITHUB.COM/JohnRRaymond/Main_Computer/',
    'git@github.com:johnrraymond/main_computer.git',
    'ssh://git@github.com/johnrraymond/main_computer.git',
    'johnrraymond/main_computer',
])
def test_metadata_accepts_exact_git_repository_in_coolify_supported_forms(tmp_path, repository):
    root = installed(tmp_path)
    remote = FakeCoolify()
    remote.app['git_repository'] = repository
    planned = g.reconcile_metadata(root, 'mainnet', client=remote)
    assert planned['status'] == 'PLAN_ONLY'
    assert all(method == 'GET' for method, _ in remote.calls)
    assert remote.application_mutations == []


def test_metadata_accepts_documented_git_full_url_if_repo_field_unset(tmp_path):
    root = installed(tmp_path)
    remote = FakeCoolify()
    remote.app.pop('git_repository')
    remote.app['git_full_url'] = 'git@github.com:johnrraymond/main_computer.git'
    assert g.reconcile_metadata(root, 'mainnet', client=remote)['status'] == 'PLAN_ONLY'
    assert remote.application_mutations == []


@pytest.mark.parametrize('bad_repo', [
    'https://github.com/johnrraymond/main_computer-other',
    'johnrraymond/main_computer-other',
    'https://github.com/another-owner/main_computer',
    'https://user:secret@github.com/johnrraymond/main_computer',
    'https://github.com/johnrraymond/main_computer?token=secret',
    'https://github.com:invalid/johnrraymond/main_computer',
    'https://github.com/johnrraymond/main_computer/other',
    'https://github.com.evil.example/johnrraymond/main_computer',
])
def test_wrong_or_unsafe_repository_refuses_and_does_not_suggest_push(tmp_path, bad_repo):
    root = installed(tmp_path)
    remote = FakeCoolify()
    remote.app['git_repository'] = bad_repo
    with pytest.raises(g.SEHGitError, match='A Git push will not change this Coolify configuration') as ex:
        g.reconcile_metadata(root, 'mainnet', execute=True, client=remote)
    assert ex.value.git_command == ''
    assert 'secret' not in str(ex.value)
    assert remote.application_mutations == []
    assert all(method == 'GET' for method, _ in remote.calls)


def test_missing_repository_refuses_without_push_and_no_mutation(tmp_path):
    root = installed(tmp_path)
    remote = FakeCoolify()
    remote.app.pop('git_repository')
    with pytest.raises(g.SEHGitError, match='no repository source field was returned') as ex:
        g.reconcile_metadata(root, 'mainnet', execute=True, client=remote)
    assert ex.value.git_command == ''
    assert remote.application_mutations == []


def test_mismatch_in_authoritative_repository_is_not_masked_by_matching_fallback(tmp_path):
    root = installed(tmp_path)
    remote = FakeCoolify()
    remote.app['git_repository'] = 'https://github.com/different/repository'
    remote.app['git_full_url'] = 'https://github.com/johnrraymond/main_computer'
    with pytest.raises(g.SEHGitError, match='git_repository identifies github.com/different/repository'):
        g.reconcile_metadata(root, 'mainnet', execute=True, client=remote)
    assert remote.application_mutations == []


def test_conflicting_git_full_url_refuses_even_when_primary_repo_matches(tmp_path):
    root = installed(tmp_path)
    remote = FakeCoolify()
    remote.app['git_full_url'] = 'https://github.com/untrusted/repository'
    with pytest.raises(g.SEHGitError, match='git_full_url identifies github.com/untrusted/repository'):
        g.reconcile_metadata(root, 'mainnet', execute=True, client=remote)
    assert remote.application_mutations == []


def test_canonical_metadata_reconcile_removes_only_legacy_env_with_matched_signing_key(tmp_path, monkeypatch):
    from tools.slh_seh_connector import service_identity
    from main_computer.slh import storage
    root = installed(tmp_path)
    slh = tmp_path / 'slh'
    _, public = service_identity(slh, create=True)
    monkeypatch.setattr(storage, 'slh_state_dir', lambda: slh)
    remote = FakeCoolify()
    remote.app.update(name=g.CANONICAL_APPLICATION_NAME, description=g.CANONICAL_DESCRIPTION)
    remote.trust[0]['value'] = public
    remote.trust.append({'uuid': 'legacy-env-1', 'key': 'SEH_ORGANIZATION_ID', 'value': 'obsolete'})
    before_trust = deepcopy(remote.trust[:2])
    _, state, before_doc = st.read(root, 'mainnet')
    planned = g.reconcile_metadata(root, 'mainnet', client=remote)
    assert planned['status'] == 'PLAN_ONLY'
    assert planned['legacy_organization_env'] == 'PRESENT'
    assert all(method == 'GET' for method, _ in remote.calls)
    applied = g.reconcile_metadata(root, 'mainnet', execute=True, client=remote)
    assert applied['status'] == 'LEGACY_ENV_REMOVED'
    assert applied['legacy_organization_env'] == 'REMOVED'
    assert applied['signing_key_untouched'] and applied['application_uuid'] == APP
    assert remote.environment_deletions == ['legacy-env-1']
    assert remote.trust == before_trust
    assert remote.application_mutations == []
    assert all(method in ('GET', 'DELETE') for method, _ in remote.calls)
    assert st.read(root, 'mainnet')[1].binding == state.binding
    assert st.read(root, 'mainnet')[2] == before_doc
    assert g.reconcile_metadata(root, 'mainnet', execute=True, client=remote)['status'] == 'UNCHANGED'
    assert remote.environment_deletions == ['legacy-env-1']


@pytest.mark.parametrize('corrupt', ['wrong-key', 'missing-key', 'missing-route', 'missing-env-uuid',
                                     'duplicate-legacy', 'wrong-application'])
def test_canonical_legacy_cleanup_refuses_without_remote_mutation(tmp_path, monkeypatch, corrupt):
    from tools.slh_seh_connector import service_identity
    from main_computer.slh import storage
    root = installed(tmp_path)
    slh = tmp_path / 'slh'
    _, public = service_identity(slh, create=True)
    monkeypatch.setattr(storage, 'slh_state_dir', lambda: slh)
    remote = FakeCoolify()
    remote.app.update(name=g.CANONICAL_APPLICATION_NAME, description=g.CANONICAL_DESCRIPTION)
    remote.trust[0]['value'] = public
    remote.trust.append({'uuid': 'legacy-env-1', 'key': 'SEH_ORGANIZATION_ID', 'value': 'obsolete'})
    if corrupt == 'wrong-key': remote.trust[0]['value'] = 'different-key'
    if corrupt == 'missing-key': remote.trust.pop(0)
    if corrupt == 'missing-route': remote.trust.pop(1)
    if corrupt == 'missing-env-uuid': remote.trust[-1].pop('uuid')
    if corrupt == 'duplicate-legacy': remote.trust.append(deepcopy(remote.trust[-1]))
    if corrupt == 'wrong-application': remote.app['uuid'] = 'unrelated'
    with pytest.raises(g.SEHGitError):
        g.reconcile_metadata(root, 'mainnet', execute=True, client=remote)
    assert remote.application_mutations == []
    assert remote.environment_deletions == []
    assert all(method == 'GET' for method, _ in remote.calls)


def test_canonical_legacy_env_cleanup_refuses_stale_mother_without_delete(tmp_path, monkeypatch):
    from tools.slh_seh_connector import service_identity
    from main_computer.slh import storage
    root = installed(tmp_path)
    slh = tmp_path / 'slh'
    _, public = service_identity(slh, create=True)
    monkeypatch.setattr(storage, 'slh_state_dir', lambda: slh)
    class ChangingMother(FakeCoolify):
        def request(self, method, path, body=None):
            if method == 'GET' and path.endswith('/envs') and not getattr(self, 'moved', False):
                self.moved = True
                st.mutate(root, 'mainnet', None, 'state-changed',
                          lambda doc, gen, base: doc['networks']['mainnet']['seh_deployment']['coolify']['controller']
                          .__setitem__('url', 'https://new-controller.example.org/'))
            return super().request(method, path, body)
    remote = ChangingMother()
    remote.app.update(name=g.CANONICAL_APPLICATION_NAME, description=g.CANONICAL_DESCRIPTION)
    remote.trust[0]['value'] = public
    remote.trust.append({'uuid': 'legacy-env-1', 'key': 'SEH_ORGANIZATION_ID', 'value': 'obsolete'})
    with pytest.raises(g.SEHGitError, match='SEH identity or deployment changed'):
        g.reconcile_metadata(root, 'mainnet', execute=True, client=remote)
    assert remote.environment_deletions == []
