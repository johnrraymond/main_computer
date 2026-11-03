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
            self.items[seh] = {'seh': seh, 'slh_id': kwargs['slh_id'], 'active_identity': None,
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
        '--slh-id', 'slh', '--public-url', 'https://seh.example.org',
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
        'seh': 'company-secondary', 'slh_id': 'slh',
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


def test_new_seh_gets_default_https_origin_without_explicit_url():
    from tools.seh_control.git_deployment import DEFAULT_PUBLIC_URL
    control = Control()
    a = _parser().parse_args(['deploy-seh', '--seh', 'secondary', '--host', 'coolify-b',
                             '--slh-id', 'slh',
                             '--execute-mutations', '--yes-i-know-this-mutates-seh'])
    result = run(a, control=control)
    assert result['status'] == 'DEPLOY_QUEUED_NOT_VERIFIED'
    assert result['public_url'] == DEFAULT_PUBLIC_URL
    assert result['ready'] is False
    assert control.calls == ['list', 'reserve', 'list', 'coolify-copy', 'list',
                             'prepare', 'preflight', 'deploy', 'status']


def test_existing_identity_mismatch_rejected_before_mutation():
    control = Control()
    control.items['company-secondary'] = {
        'seh': 'company-secondary', 'slh_id': 'other',
        'active_identity': 'coolify-b',
        'identities': {'coolify-b': {'copied_from': 'networks.mainnet.coolify.controllers.coolify-b'}},
        'deployment': {},
    }
    with pytest.raises(ValueError, match='slh_id differs'):
        run(args('--execute-mutations', '--yes-i-know-this-mutates-seh'), control=control)
    assert control.calls == ['list']


def test_alias_collision_refused_without_identity_replacement():
    control = Control()
    control.items['company-secondary'] = {
        'seh': 'company-secondary', 'slh_id': 'slh',
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
        'seh': 'company-secondary', 'slh_id': 'slh',
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
        'seh': 'company-primary', 'slh_id': 'my-slh',
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
        'seh': 'company-primary', 'slh_id': 'my-slh',
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
        'seh': 'company-primary', 'slh_id': 'my-slh',
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
        'seh': 'company-primary', 'slh_id': 'my-slh',
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
        'seh': 'company-primary', 'slh_id': 'my-slh',
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
              'slh_id': 'my-slh'}
    monkeypatch.setattr(g, '_selections', lambda *a: (record, {'enabled': True}, {}, 8, 'hash'))
    class AppClient:
        def __init__(self, domains, name='mc-seh-company-primary'):
            self.domains = domains
            self.name = name
            self.calls = []
        def request(self, method, path, body=None):
            self.calls.append((method, path))
            return {'uuid': 'known-app', 'name': self.name,
                    'description': 'main-computer-seh:git-v3 target=company-primary slh=my-slh',
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


# Deployment is exclusively an infrastructure mutation. No SQL, SLH key,
# or resident organization state may be touched by the operator command.
def test_existing_deploy_does_not_access_local_slh_or_its_sqlite(tmp_path, monkeypatch):
    from pathlib import Path
    import sqlite3
    control = Control()
    control.items['company-primary'] = {
        'seh': 'company-primary', 'slh_id': 'my-slh',
        'active_identity': 'primary',
        'identities': {'primary': {'copied_from': 'networks.mainnet.coolify.controllers.coolify-c'}},
        'deployment': {'public_url': 'https://external.greatlibrary.io',
                       'application_uuid': 'existing-app-uuid'},
    }
    def forbidden(*a, **k):
        raise AssertionError('Deploy must not inspect or modify local SLH state')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    from tools import slh_seh_connector
    monkeypatch.setattr(slh_seh_connector, 'default_state', forbidden)
    result = _run(_parser().parse_args([
        'deploy-seh', '--network', 'mainnet', '--seh', 'company-primary',
        '--host', 'coolify-c', '--execute-mutations',
        '--yes-i-know-this-mutates-seh',
    ]), control=control)
    assert result['status'] == 'DEPLOY_QUEUED_NOT_VERIFIED'
    assert 'slh-identity' not in [row['stage'] for row in result['steps']]
    assert control.calls == ['list', 'prepare', 'preflight', 'deploy', 'status']


def test_new_seh_deploy_requires_no_organization_or_slh_name():
    control = Control()
    result = _run(_parser().parse_args([
        'deploy-seh', '--seh', 'unbound-relay', '--host', 'coolify-b',
        '--public-url', 'https://seh.example.org',
        '--execute-mutations', '--yes-i-know-this-mutates-seh',
    ]), control=control)
    assert result['status'] == 'DEPLOY_QUEUED_NOT_VERIFIED'
    assert 'organization_id' not in control.items['unbound-relay']
    assert control.items['unbound-relay']['slh_id'] is None


def test_missing_organization_does_not_block_new_seh_registration():
    control = Control()
    a = _parser().parse_args([
        'deploy-seh', '--seh', 'unbound-relay', '--host', 'coolify-b',
        '--public-url', 'https://seh.example.org',
        '--execute-mutations', '--yes-i-know-this-mutates-seh',
    ])
    result = _run(a, control=control)
    assert result['status'] == 'DEPLOY_QUEUED_NOT_VERIFIED'
    assert 'organization_id' not in control.items['unbound-relay']


# The Hub harness prints a standalone command on a dry-run boundary. The SEH
# operator should get the same copy/paste affordance without corrupting JSON.
def test_git_refusal_prints_repair_and_retry_as_separate_powershell_commands(monkeypatch, capsys):
    import seh_mutate_harness as seh_harness

    git_command = (
        "git add -- 'main_computer/seh/relay.py'; "
        "if ($LASTEXITCODE -eq 0) { git push origin HEAD }"
    )
    monkeypatch.setattr(seh_harness, 'run', lambda _args: {
        'status': 'REFUSED', 'failed_stage': 'prepare',
        'git_repair_command': git_command,
    })
    result = seh_harness.main([
        'deploy-seh', '--network', 'mainnet', '--seh', 'company-primary',
        '--host', 'coolify-c', '--execute-mutations', '--yes-i-know-this-mutates-seh',
    ])
    captured = capsys.readouterr()
    assert result == 2
    assert json.loads(captured.out)['git_repair_command'] == git_command
    assert captured.out.rstrip().endswith('}')  # stdout remains machine-readable JSON
    assert '\n=== Next SEH command (PowerShell) ===' in captured.err
    assert git_command in captured.err
    assert captured.err.rstrip().endswith(git_command)
    assert (
        'python .\\seh_mutate_harness.py deploy-seh --network mainnet '
        '--seh company-primary --host coolify-c --execute-mutations '
        '--yes-i-know-this-mutates-seh'
    ) in captured.err


def test_plan_only_shows_full_copyable_command_without_mutating(monkeypatch, capsys):
    import seh_mutate_harness as seh_harness

    monkeypatch.setattr(seh_harness, 'run', lambda _args: {'status': 'PLAN_ONLY'})
    assert seh_harness.main([
        'deploy-seh', '--seh', 'my-seh', '--host', 'coolify-b',
        '--public-url', 'https://seh.example.org',
    ]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)['status'] == 'PLAN_ONLY'
    assert '--public-url https://seh.example.org' in captured.err
    assert captured.err.endswith('--yes-i-know-this-mutates-seh\n')


def test_power_shell_retry_quotes_literal_with_spaces_and_apostrophe():
    from seh_mutate_harness import _deploy_retry_command, _parser

    args = _parser().parse_args([
        'deploy-seh', '--seh', 'company-primary', '--host', 'coolify-c',
        '--runtime-state-root', "C:/Main Computer/John's runtime/state",
    ])
    command = _deploy_retry_command(args)
    assert "--runtime-state-root 'C:/Main Computer/John''s runtime/state'" in command


def test_queued_deploy_returns_nonzero_and_does_not_print_second_command(monkeypatch, capsys):
    import seh_mutate_harness as seh_harness

    monkeypatch.setattr(seh_harness, 'run', lambda _args: {
        'status': 'DEPLOY_QUEUED_NOT_VERIFIED', 'ready': False,
    })
    assert seh_harness.main(['deploy-seh', '--seh', 'company-primary']) == 3
    captured = capsys.readouterr()
    assert json.loads(captured.out)['ready'] is False
    assert captured.err == ''


def test_trust_redeploy_is_incomplete_and_never_prints_manual_connector(monkeypatch, capsys):
    import seh_mutate_harness as harness
    monkeypatch.setattr(harness, 'run', lambda _args: {
        'status': 'TRUST_DEPLOY_QUEUED_NOT_VERIFIED',
        'ready': False, 'public_wss_verified': False,
        'slh_id': 'slh',
        'public_url': 'https://seh.example.org',
    })
    assert harness.main(['bind-slh', '--seh', 'company-primary']) == 3
    report = capsys.readouterr()
    assert json.loads(report.out)['ready'] is False
    assert 'operational acceptance remains incomplete' in report.err
    assert 'tools.slh_seh_connector run' not in report.err

# Patch 1: read-only operations may resolve a sole legacy record, but no longer
# accept a user-selected SEH identity. No registration or remote mutation.
def test_inspect_without_alias_reports_absent_deployment_read_only():
    control = Control()
    result = run(_parser().parse_args(['inspect']), control=control)
    assert result['status'] == 'READ_ONLY'
    assert result['registered'] is False
    assert result['deployment'] is None
    assert 'seh' not in result
    assert control.calls == ['list']


def test_inspect_without_alias_reads_only_sole_existing_record():
    control = Control()
    control.items['obsolete-name'] = {
        'seh': 'obsolete-name', 'schema': 'main-computer.seh-identity.v3',
        'slh_id': 'slh', 'active_identity': 'primary',
        'identities': {'primary': {'copied_from': 'networks.mainnet.coolify.controllers.coolify-c'}},
        'deployment': {'application_uuid': 'existing-coolify-uuid'},
    }
    original = json.dumps(control.items, sort_keys=True)
    result = run(_parser().parse_args(['inspect']), control=control)
    assert result['registered'] is True
    assert result['deployment']['application_uuid'] == 'existing-coolify-uuid'
    assert 'seh' not in result
    assert json.dumps(control.items, sort_keys=True) == original
    assert control.calls == ['list']


@pytest.mark.parametrize('operation', ('inspect', 'diagnostic'))
def test_read_only_rejects_ambiguous_legacy_registrations_before_key_or_coolify(operation):
    control = Control()
    control.items['obsolete-one'] = {'seh': 'obsolete-one'}
    control.items['obsolete-two'] = {'seh': 'obsolete-two'}
    with pytest.raises(ValueError, match='Multiple legacy SEH records'):
        run(_parser().parse_args([operation]), control=control,
            slh_public_key_provider=lambda **_: (_ for _ in ()).throw(
                AssertionError('must not load signing identity on ambiguous state')))
    assert control.calls == ['list']


@pytest.mark.parametrize('operation', ('inspect', 'diagnostic'))
@pytest.mark.parametrize('flags', (('--seh', 'obsolete'), ('--host', 'coolify-c'),
                                    ('--slh-id', 'slh'), ('--public-url', 'https://seh.example.org')))
def test_read_only_rejects_target_selection_flags(operation, flags):
    control = Control()
    with pytest.raises(ValueError, match='omit target-selection flags'):
        run(_parser().parse_args([operation, *flags]), control=control)
    assert control.calls == []


def test_diagnostic_without_registration_refuses_without_key_or_remote_access():
    control = Control()
    result = run(_parser().parse_args(['diagnostic']), control=control,
                 slh_public_key_provider=lambda **_: (_ for _ in ()).throw(
                     AssertionError('no key lookup for absent deployment')))
    assert result['status'] == 'REFUSED'
    assert result['mutation_performed'] is False
    assert 'seh' not in result
    assert control.calls == ['list']


def test_diagnostic_without_alias_uses_single_verified_controller_and_app():
    class DiagnosticControl(Control):
        def diagnostic_slh_trust(self, network, seh, **kwargs):
            self.calls.append('diagnostic')
            assert (network, seh) == ('mainnet', 'legacy-obsolete')
            assert kwargs == {'slh_id': 'slh', 'public_key_b64': 'test-public-key'}
            return {'status': 'READ_ONLY', 'seh': seh, 'application_uuid': 'stable-uuid',
                    'issues': [], 'mutation_performed': False}
    control = DiagnosticControl()
    control.items['legacy-obsolete'] = {
        'seh': 'legacy-obsolete', 'slh_id': 'slh', 'active_identity': 'old-alias',
        'identities': {'old-alias': {
            'copied_from': 'networks.mainnet.coolify.controllers.coolify-c'}},
        'deployment': {'application_uuid': 'stable-uuid', 'public_url': 'https://external.greatlibrary.io'},
    }
    result = run(_parser().parse_args(['diagnostic']), control=control,
                 slh_public_key_provider=lambda **_: 'test-public-key')
    assert result['status'] == 'READ_ONLY'
    assert result['controller'] == 'coolify-c'
    assert result['application_uuid'] == 'stable-uuid'
    assert 'seh' not in result
    assert control.calls == ['list', 'diagnostic']
