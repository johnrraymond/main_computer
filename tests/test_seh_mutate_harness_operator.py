"""SEH operator harness is a guarded, single-command staging facade."""
from __future__ import annotations

import json
import pytest

from seh_mutate_harness import _parser, run as _run


def run(*args, **kwargs):
    kwargs.setdefault('slh_public_key_provider', lambda **ids: 'mock-public-key')
    return _run(*args, **kwargs)


class Control:
    def __init__(self):
        self.items = {}
        self.calls = []
        self.next_application = 'seh-app-uuid'
        self.preflight_status = 'CREATE_ELIGIBLE'
        self.fail_stage = ''
        self.plan_digest = 'a' * 64

    def list(self, network):
        self.calls.append('list')
        return {'sehs': list(self.items.values()), 'status': 'READ_ONLY'}

    def inspect(self, network, seh):
        return self.items[seh]

    def change(self, command, network, seh, generation, operation_id, **kwargs):
        self.calls.append(command)
        if command == 'reserve':
            self.items[seh] = {'seh': seh, 'organization_id': kwargs['organization_id'],
                               'slh_id': kwargs['slh_id'], 'active_identity': None,
                               'identities': {}, 'deployment': {}}
        elif command == 'coolify-copy':
            self.items[seh]['identities'][kwargs['alias']] = {'copied_from': kwargs['source']}
            self.items[seh]['active_identity'] = self.items[seh]['active_identity'] or kwargs['alias']
        elif command == 'coolify-select':
            self.items[seh]['active_identity'] = kwargs['alias']
        return {'status': 'COMMITTED', 'generation': len(self.calls)}

    def prepare_git(self, network, seh, **kwargs):
        self.calls.append('prepare')
        if self.fail_stage == 'prepare':
            raise ValueError('test unavailable')
        return {'status': 'PREPARED', 'plan_digest': self.plan_digest,
                'public_url': kwargs['public_url']}

    def preflight_git(self, network, seh):
        self.calls.append('preflight')
        return {'status': self.preflight_status, 'plan_digest': self.plan_digest}

    def deploy_git(self, network, seh, *, operation_id):
        self.calls.append('deploy')
        self.items[seh]['deployment']['application_uuid'] = self.next_application
        return {'status': 'DEPLOY_QUEUED', 'application_uuid': self.next_application,
                'plan_digest': self.plan_digest}

    def status_git(self, network, seh):
        self.calls.append('status')
        return {'status': 'COOLIFY_OBSERVED', 'application_uuid': self.next_application,
                'dns_ready': False, 'coolify_status': 'building'}


def args(*extra):
    return _parser().parse_args(['deploy-seh', '--seh', 'company-secondary', '--host', 'coolify-b',
        '--organization-id', 'org', '--slh-id', 'slh', '--public-url', 'https://seh.example.org',
        *extra])


def test_no_mutation_without_both_explicit_approvals():
    for flags in [[], ['--execute-mutations'], ['--yes-i-know-this-mutates-seh']]:
        control = Control()
        result = run(args(*flags), control=control)
        assert result['status'] == 'PLAN_ONLY'
        assert control.calls == ['list']


def test_exact_single_operator_command_performs_guarded_lifecycle():
    control = Control()
    result = run(args('--execute-mutations', '--yes-i-know-this-mutates-seh'), control=control)
    assert result['status'] == 'DEPLOY_QUEUED_NOT_VERIFIED'
    assert result['public_wss_verified'] is False
    assert result['ready'] is False
    assert result['dns_ready'] is False
    assert control.calls == ['list', 'reserve', 'list', 'coolify-copy', 'list',
                             'prepare', 'preflight', 'deploy', 'status']
    assert control.items['company-secondary']['identities']['coolify-b']['copied_from'] == \
        'networks.mainnet.coolify.controllers.coolify-b'


def test_repeat_same_identity_does_not_reserve_or_copy_again():
    control = Control()
    a = args('--execute-mutations', '--yes-i-know-this-mutates-seh')
    assert run(a, control=control)['status'] == 'DEPLOY_QUEUED_NOT_VERIFIED'
    control.calls.clear()
    result = run(a, control=control)
    assert result['status'] == 'DEPLOY_QUEUED_NOT_VERIFIED'
    assert control.calls == ['list', 'prepare', 'preflight', 'deploy', 'status']


def test_existing_deployed_controller_change_is_refused_before_remote_mutation():
    control = Control()
    control.items['company-secondary'] = {
        'seh': 'company-secondary', 'organization_id': 'org', 'slh_id': 'slh',
        'active_identity': 'coolify-c',
        'identities': {'coolify-c': {'copied_from': 'networks.mainnet.coolify.controllers.coolify-c'}},
        'deployment': {'application_uuid': 'existing-service'},
    }
    with pytest.raises(ValueError, match="another Coolify controller"):
        run(args('--execute-mutations', '--yes-i-know-this-mutates-seh'), control=control)
    assert control.calls == ['list']


def test_preflight_blocks_remote_deploy_and_reports_stage():
    control = Control()
    control.preflight_status = 'NOT_ELIGIBLE'
    result = run(args('--execute-mutations', '--yes-i-know-this-mutates-seh'), control=control)
    assert result['status'] == 'REFUSED'
    assert result['failed_stage'] == 'preflight'
    assert 'deploy' not in control.calls
    assert 'status' not in control.calls


def test_missing_public_url_rejected_before_mutation():
    control = Control()
    a = _parser().parse_args(['deploy-seh', '--seh', 'secondary', '--host', 'coolify-b',
                             '--organization-id', 'org', '--slh-id', 'slh',
                             '--execute-mutations', '--yes-i-know-this-mutates-seh'])
    with pytest.raises(ValueError, match='public-url'):
        run(a, control=control)
    assert control.calls == ['list']  # A read-only state lookup is permitted.


def test_existing_identity_mismatch_rejected_before_mutation():
    control = Control()
    control.items['company-secondary'] = {
        'seh': 'company-secondary', 'organization_id': 'other', 'slh_id': 'slh',
        'active_identity': 'coolify-b',
        'identities': {'coolify-b': {'copied_from': 'networks.mainnet.coolify.controllers.coolify-b'}},
        'deployment': {},
    }
    with pytest.raises(ValueError, match='organization_id differs'):
        run(args('--execute-mutations', '--yes-i-know-this-mutates-seh'), control=control)
    assert control.calls == ['list']


def test_alias_collision_refused_without_identity_replacement():
    control = Control()
    control.items['company-secondary'] = {
        'seh': 'company-secondary', 'organization_id': 'org', 'slh_id': 'slh',
        'active_identity': 'coolify-b',
        'identities': {'coolify-b': {'copied_from': 'networks.mainnet.coolify.controllers.coolify-a'}},
        'deployment': {},
    }
    with pytest.raises(ValueError, match='alias collision'):
        run(args('--execute-mutations', '--yes-i-know-this-mutates-seh'), control=control)
    assert control.calls == ['list']


def test_existing_primary_alias_same_host_preserved_without_copy_or_switch():
    control = Control()
    control.items['company-secondary'] = {
        'seh': 'company-secondary', 'organization_id': 'org', 'slh_id': 'slh',
        'active_identity': 'primary',
        'identities': {'primary': {'copied_from': 'networks.mainnet.coolify.controllers.coolify-b'}},
        'deployment': {},
    }
    result = run(args('--execute-mutations', '--yes-i-know-this-mutates-seh'), control=control)
    assert result['status'] == 'DEPLOY_QUEUED_NOT_VERIFIED'
    assert 'coolify-copy' not in control.calls
    assert 'coolify-select' not in control.calls
    assert control.items['company-secondary']['active_identity'] == 'primary'


def test_existing_company_primary_on_coolify_c_needs_no_registration_copy_or_public_url():
    """Exact user command: preserve the owned alias 'primary', use its registered UUID."""
    class ExistingCoolify(Control):
        def observed_public_origin(self, network, seh):
            self.calls.append('observe-domain')
            return {'status': 'COOLIFY_OBSERVED', 'application_uuid': self.next_application,
                    'public_url': 'https://seh.example.org', 'dns_ready': True,
                    'coolify_status': 'running'}

    control = ExistingCoolify()
    control.items['company-primary'] = {
        'seh': 'company-primary', 'organization_id': 'my-company', 'slh_id': 'my-slh',
        'active_identity': 'primary',
        'identities': {'primary': {'copied_from': 'networks.mainnet.coolify.controllers.coolify-c'}},
        'deployment': {'enabled': False, 'application_uuid': control.next_application, 'git': {'enabled': True}},
    }
    arguments = _parser().parse_args([
        'deploy-seh', '--network', 'mainnet', '--seh', 'company-primary', '--host', 'coolify-c',
        '--execute-mutations', '--yes-i-know-this-mutates-seh',
    ])
    result = run(arguments, control=control)
    assert result['status'] == 'DEPLOY_QUEUED_NOT_VERIFIED'
    assert result['public_url'] == 'https://seh.example.org'
    assert result['application_uuid'] == control.next_application
    assert control.items['company-primary']['active_identity'] == 'primary'
    assert 'reserve' not in control.calls
    assert 'coolify-copy' not in control.calls
    assert 'coolify-select' not in control.calls
    assert control.calls == ['list', 'observe-domain', 'prepare', 'preflight', 'deploy', 'status']


def test_existing_company_primary_auto_selects_its_verified_controller():
    control = Control()
    control.items['company-primary'] = {
        'seh': 'company-primary', 'organization_id': 'my-company', 'slh_id': 'my-slh',
        'active_identity': 'primary',
        'identities': {'primary': {'copied_from': 'networks.mainnet.coolify.controllers.coolify-c'}},
        'deployment': {'public_url': 'https://seh.example.org', 'application_uuid': 'seh-app-uuid'},
    }
    arguments = _parser().parse_args(['deploy-seh', '--seh', 'company-primary',
        '--execute-mutations', '--yes-i-know-this-mutates-seh'])
    result = run(arguments, control=control)
    assert result['status'] == 'DEPLOY_QUEUED_NOT_VERIFIED'
    assert result['controller'] == 'coolify-c'
    assert control.calls == ['list', 'prepare', 'preflight', 'deploy', 'status']


def test_no_url_and_ambiguous_remote_identity_fails_before_any_mutation():
    class WrongRemote(Control):
        def observed_public_origin(self, network, seh):
            self.calls.append('observe-domain')
            return {'application_uuid': self.next_application, 'public_url': 'https://seh.example.org'}
    control = WrongRemote()
    control.items['company-primary'] = {
        'seh': 'company-primary', 'organization_id': 'my-company', 'slh_id': 'my-slh',
        'active_identity': 'primary',
        'identities': {'primary': {'copied_from': 'networks.mainnet.coolify.controllers.coolify-c'}},
        'deployment': {'application_uuid': 'app-original'},
    }
    arguments = _parser().parse_args(['deploy-seh', '--seh', 'company-primary',
        '--host', 'coolify-c', '--execute-mutations', '--yes-i-know-this-mutates-seh'])
    result = run(arguments, control=control)
    assert result['status'] == 'REFUSED'
    assert result['failed_stage'] == 'public-url'
    assert control.calls == ['list', 'observe-domain']


def test_read_only_plan_existing_url_not_known_does_not_contact_coolify():
    control = Control()
    control.items['company-primary'] = {
        'seh': 'company-primary', 'organization_id': 'my-company', 'slh_id': 'my-slh',
        'active_identity': 'primary',
        'identities': {'primary': {'copied_from': 'networks.mainnet.coolify.controllers.coolify-c'}},
        'deployment': {'application_uuid': 'seh-app-uuid'},
    }
    arguments = _parser().parse_args(['deploy-seh', '--seh', 'company-primary'])
    result = run(arguments, control=control)
    assert result['status'] == 'PLAN_ONLY'
    assert result['public_url_discovery'] == 'REQUIRED_ON_EXECUTION'
    assert control.calls == ['list']


def test_refuse_explicit_controller_change_before_url_lookup():
    control = Control()
    control.items['company-primary'] = {
        'seh': 'company-primary', 'organization_id': 'my-company', 'slh_id': 'my-slh',
        'active_identity': 'primary',
        'identities': {'primary': {'copied_from': 'networks.mainnet.coolify.controllers.coolify-c'}},
        'deployment': {'application_uuid': 'seh-app-uuid'},
    }
    arguments = _parser().parse_args(['deploy-seh', '--seh', 'company-primary',
        '--host', 'coolify-b', '--execute-mutations', '--yes-i-know-this-mutates-seh'])
    with pytest.raises(ValueError, match='another Coolify controller'):
        run(arguments, control=control)
    assert control.calls == ['list']



def test_domain_discovery_reads_remote_application_not_cached_plan(monkeypatch):
    from tools.seh_control import git_deployment as g
    record = {'deployment': {'application_uuid': 'known-app'},
              'organization_id': 'my-company', 'slh_id': 'my-slh'}
    monkeypatch.setattr(g, '_selections', lambda *a: (record, {'enabled': True}, {}, 8, 'hash'))
    class AppClient:
        def __init__(self, domains, name='mc-seh-company-primary'):
            self.domains = domains
            self.name = name
            self.calls = []
        def request(self, method, path, body=None):
            self.calls.append((method, path))
            return {'uuid': 'known-app', 'name': self.name,
                    'description': 'main-computer-seh:git-v1 target=company-primary org=my-company slh=my-slh',
                    'domains': self.domains}
    client = AppClient('https://seh.example.org:8080')
    r = g.observed_public_origin('/tmp/not-used', 'mainnet', 'company-primary', client=client)
    assert r['public_url'] == 'https://seh.example.org'
    assert client.calls == [('GET', '/api/v1/applications/known-app')]
    for domains in ('', 'https://first.example.org,https://second.example.org'):
        with pytest.raises(g.SEHGitError, match='exactly one public domain'):
            g.observed_public_origin('/tmp/not-used', 'mainnet', 'company-primary',
                                     client=AppClient(domains))
    with pytest.raises(g.SEHGitError, match='ownership'):
        g.observed_public_origin('/tmp/not-used', 'mainnet', 'company-primary',
                                 client=AppClient('https://seh.example.org', name='foreign-app'))


# Actual local SLH signer provisioning contract (no Coolify mutation required).
def test_local_signing_identity_requires_existing_matching_organization(tmp_path, monkeypatch):
    import sqlite3
    from pathlib import Path
    from seh_mutate_harness import _verified_slh_public_key, HarnessError
    from tools import slh_seh_connector
    import main_computer.slh.storage as storage

    local = tmp_path / 'slh'
    monkeypatch.setattr(slh_seh_connector, 'default_state', lambda: local)
    monkeypatch.setattr(storage, 'legacy_slh_state_dir', lambda: tmp_path / 'no-legacy')
    with pytest.raises(HarnessError, match='no initialized SLH authority'):
        _verified_slh_public_key(organization_id='my-company', slh_id='my-slh')
    assert not local.exists()

    local.mkdir()
    with sqlite3.connect(local / 'organization.sqlite3') as conn:
        conn.execute('CREATE TABLE organization (org_id TEXT PRIMARY KEY)')
        conn.execute('INSERT INTO organization VALUES (?)', ('different-company',))
    with pytest.raises(HarnessError, match='organization does not match'):
        _verified_slh_public_key(organization_id='my-company', slh_id='my-slh')
    assert not (local / 'seh-service-signing-key.pem').exists()


def test_local_signing_identity_creates_once_on_matched_slh(tmp_path, monkeypatch):
    import base64
    import sqlite3
    from pathlib import Path
    from seh_mutate_harness import _verified_slh_public_key
    from tools import slh_seh_connector
    import main_computer.slh.storage as storage

    local = tmp_path / 'slh'
    local.mkdir()
    with sqlite3.connect(local / 'organization.sqlite3') as conn:
        conn.execute('CREATE TABLE organization (org_id TEXT PRIMARY KEY)')
        conn.execute('INSERT INTO organization VALUES (?)', ('my-company',))
    monkeypatch.setattr(slh_seh_connector, 'default_state', lambda: local)
    monkeypatch.setattr(storage, 'legacy_slh_state_dir', lambda: tmp_path / 'no-legacy')
    public_one = _verified_slh_public_key(organization_id='my-company', slh_id='my-slh')
    private_path = local / 'seh-service-signing-key.pem'
    private_one = private_path.read_bytes()
    assert b'PRIVATE KEY' in private_one
    assert b'PUBLIC KEY' in base64.b64decode(public_one)
    public_two = _verified_slh_public_key(organization_id='my-company', slh_id='my-slh')
    assert public_one == public_two
    assert private_path.read_bytes() == private_one
    assert private_one.decode() not in public_one


def test_local_signing_identity_rejects_wrong_slh_binding(tmp_path, monkeypatch):
    import sqlite3
    import json
    from seh_mutate_harness import _verified_slh_public_key, HarnessError
    from tools import slh_seh_connector
    import main_computer.slh.storage as storage

    local = tmp_path / 'slh'
    local.mkdir()
    with sqlite3.connect(local / 'organization.sqlite3') as conn:
        conn.execute('CREATE TABLE organization (org_id TEXT PRIMARY KEY)')
        conn.execute('INSERT INTO organization VALUES (?)', ('my-company',))
    (local / 'seh-connection.json').write_text(json.dumps({
        'enabled': False, 'seh_url': 'wss://external.greatlibrary.io',
        'organization_id': 'my-company', 'slh_id': 'another-slh',
        'allow_loopback_lab': False, 'retry_seconds': 2.0,
    }))
    monkeypatch.setattr(slh_seh_connector, 'default_state', lambda: local)
    monkeypatch.setattr(storage, 'legacy_slh_state_dir', lambda: tmp_path / 'no-legacy')
    with pytest.raises(HarnessError, match='differs from the registered SEH'):
        _verified_slh_public_key(organization_id='my-company', slh_id='my-slh')
    assert not (local / 'seh-service-signing-key.pem').exists()


def test_remote_operator_not_allowed_to_create_slh_authority(tmp_path, monkeypatch):
    from seh_mutate_harness import _verified_slh_public_key, HarnessError
    from tools import slh_seh_connector
    import main_computer.slh.storage as storage

    local = tmp_path / 'slh'
    monkeypatch.setattr(slh_seh_connector, 'default_state', lambda: local)
    monkeypatch.setattr(storage, 'legacy_slh_state_dir', lambda: tmp_path / 'no-legacy')
    with pytest.raises(HarnessError, match='actual organization SLH host'):
        _verified_slh_public_key(organization_id='my-company', slh_id='my-slh')
    assert not (local / 'organization.sqlite3').exists()


def test_existing_coolify_c_legacy_slh_refuses_with_migration_steps_and_no_mutation(tmp_path, monkeypatch):
    """A real legacy identity must neither be re-created nor produce a traceback."""
    import main_computer.slh.storage as storage

    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'user'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'machine'))
    monkeypatch.setattr(storage, 'machine_root', lambda: tmp_path / 'runtime' / 'state')
    monkeypatch.setattr(storage, 'previous_machine_slh_state_dir', lambda: tmp_path / 'no-prior-machine')
    legacy = storage.legacy_slh_state_dir()
    legacy.mkdir(parents=True)
    (legacy / 'organization.sqlite3').write_bytes(b'keep-this-legacy-identity')
    destination = storage.slh_state_dir()

    control = Control()
    control.items['company-primary'] = {
        'seh': 'company-primary', 'organization_id': 'my-company', 'slh_id': 'my-slh',
        'active_identity': 'primary',
        'identities': {'primary': {
            'copied_from': 'networks.mainnet.coolify.controllers.coolify-c'}},
        'deployment': {'public_url': 'https://external.greatlibrary.io',
                       'application_uuid': 'existing-app-uuid'},
    }
    arguments = _parser().parse_args([
        'deploy-seh', '--network', 'mainnet', '--seh', 'company-primary',
        '--host', 'coolify-c', '--execute-mutations',
        '--yes-i-know-this-mutates-seh',
    ])
    result = _run(arguments, control=control)
    assert result['status'] == 'REFUSED'
    assert result['failed_stage'] == 'slh-identity'
    assert result['error_type'] == 'HarnessError'
    assert 'LEGACY_SLH_STATE_REQUIRES_OFFLINE_MIGRATION' in result['reason']
    assert 'python -m tools.slh_storage_migrate plan' in result['reason']
    assert 'python -m tools.slh_storage_migrate slh --execute' in result['reason']
    assert control.calls == ['list']
    assert not destination.exists()
    assert (legacy / 'organization.sqlite3').read_bytes() == b'keep-this-legacy-identity'


def test_unexpected_storage_guard_failure_is_still_fail_closed(tmp_path, monkeypatch):
    from seh_mutate_harness import HarnessError, _verified_slh_public_key
    from tools import slh_seh_connector
    import main_computer.slh.storage as storage

    monkeypatch.setattr(slh_seh_connector, 'default_state', lambda: tmp_path / 'slh')
    monkeypatch.setattr(storage, 'guard_legacy_state',
                        lambda directory: (_ for _ in ()).throw(RuntimeError('unknown')))
    with pytest.raises(HarnessError, match='SLH storage guard failed'):
        _verified_slh_public_key(organization_id='my-company', slh_id='my-slh')
    assert not (tmp_path / 'slh').exists()
