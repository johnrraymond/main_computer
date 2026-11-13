"""Canonical SEH undeployment: UUID-bound delete, durable intent, no authority loss."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from seh_mutate_harness import _parser, run, HarnessError
from main_computer.slh.organization_state import save_config
from main_computer.slh.seh_deployment_management import SLHSEHDeploymentManager, SEHManagementRefused
from tools.seh_control import git_deployment as g, identity as st, deployment_state as ds
from tools.slh_seh_connector import service_identity
from tests.test_seh_canonical_redeploy_runtime import Coolify, APP, ctx


class Deletable(Coolify):
    def __init__(self):
        super().__init__()
        self.deleted = False
        self.deletes = 0
        self.ack_lost = False
        self.lingering = False
        self.forbidden_status = None

    def request(self, method, path, body=None):
        if method == 'GET' and path == f'/api/v1/applications/{APP}':
            self.calls.append((method, path))
            if self.forbidden_status:
                return SimpleNamespace(ok=False, status=self.forbidden_status, body={})
            if self.deleted and not self.lingering:
                return SimpleNamespace(ok=False, status=404, body={})
        if method == 'DELETE' and path.startswith(f'/api/v1/applications/{APP}?'):
            self.calls.append((method, path))
            self.deletes += 1
            assert 'delete_volumes=false' in path
            assert 'delete_connected_networks=false' in path
            if not self.lingering:
                self.deleted = True
            if self.ack_lost:
                raise ConnectionError('lost delete acknowledgement')
            return {'message': 'Application deleted.'}
        return super().request(method, path, body)


def make_fixture(tmp_path):
    root, _ = ctx(tmp_path)
    remote = Deletable()
    state_dir = tmp_path / 'slh'
    state_dir.mkdir()
    _, public = service_identity(state_dir, create=True)
    remote.trust = [{'key': 'SEH_SLH_PUBLIC_KEY_B64', 'value': public, 'uuid': 'env-trust'}]
    save_config(state_dir, {'schema':'main-computer.slh-organization-config.v1',
        'configured':True,'enabled':True,'activation_confirmed':True,
        'host':'127.0.0.1','api_port':19042,'worker_port':19043,
        'workers_enabled':True,'seh_enabled':False})
    return root, remote, state_dir


def disconnected(url):
    assert url == g.DEFAULT_PUBLIC_URL
    return {'status': 'PUBLIC_RUNTIME_PRESENT', 'slh_connected': False}


def connected(url):
    return {'status': 'PUBLIC_RUNTIME_PRESENT', 'slh_connected': True}


def call(root, remote, state_dir, **kw):
    return g.undeploy_canonical_runtime(root, 'mainnet', client=remote,
                                        state_dir=state_dir, probe=disconnected, **kw)


def test_plan_is_read_only_and_returns_exact_uuid_and_admission_check(tmp_path):
    root, remote, slh = make_fixture(tmp_path)
    before = st.read(root, 'mainnet')[2]
    result = call(root, remote, slh)
    assert result['status'] == 'PLAN_ONLY'
    assert result['application_uuid'] == APP
    assert result['ready_for_undeploy'] is True
    assert all(method == 'GET' for method, _ in remote.calls)
    assert not (ds.directory(root, 'mainnet') / (g._UNDEPLOY_RECEIPT + APP + '.json')).exists()
    assert st.read(root, 'mainnet')[2] == before


def test_undeploy_deletes_only_owned_uuid_and_keeps_tombstone_identity(tmp_path):
    root, remote, slh = make_fixture(tmp_path)
    mother = deepcopy(st.read(root, 'mainnet')[2])
    first = call(root, remote, slh, execute=True)
    assert first['status'] == 'SEH_UNDEPLOYED'
    assert first['application_removed_verified'] is True
    assert remote.deletes == 1 and remote.deleted
    assert [p for m,p in remote.calls if m == 'DELETE'] == [
        f'/api/v1/applications/{APP}' + g._UNDEPLOY_DELETE_PATH_SUFFIX]
    assert st.read(root, 'mainnet')[2] == mother
    rec = g._canonical_selection(root, 'mainnet')[0]['deployment']
    assert rec['application_uuid'] == APP
    assert rec['undeployed'] is True and rec['enabled'] is False
    assert g._undeploy_receipt(root, 'mainnet', APP)['phase'] == 'VERIFIED'
    second = call(root, remote, slh, execute=True)
    assert second['status'] == 'ALREADY_UNDEPLOYED' and remote.deletes == 1


def test_active_tunnel_or_enabled_policy_refuses_delete(tmp_path):
    root, remote, slh = make_fixture(tmp_path)
    with pytest.raises(g.SEHGitError, match='disabled and disconnected'):
        g.undeploy_canonical_runtime(root, 'mainnet', execute=True,
                                     client=remote, state_dir=slh, probe=connected)
    cfg = json.loads((slh / 'organization-service.json').read_text())
    save_config(slh, {**cfg, 'seh_enabled':True})
    with pytest.raises(g.SEHGitError, match='disabled and disconnected'):
        call(root, remote, slh, execute=True)
    assert remote.deletes == 0
    assert g._undeploy_receipt(root, 'mainnet', APP) is None


@pytest.mark.parametrize('field', ['name', 'git_repository', 'environment_uuid', 'domains', 'trust'])
def test_wrong_application_or_trust_refused_before_delete(tmp_path, field):
    root, remote, slh = make_fixture(tmp_path)
    if field == 'trust':
        remote.trust[0]['value'] = 'not-the-slh-key'
    elif field == 'name':
        remote.app['name'] = 'Other application'
    elif field == 'git_repository':
        remote.app['git_repository'] = 'https://github.com/other/repository'
    elif field == 'environment_uuid':
        remote.app['environment_uuid'] = 'foreign-env'
    else:
        remote.app['domains'] = 'https://foreign.invalid:8080'
    with pytest.raises(g.SEHGitError):
        call(root, remote, slh, execute=True)
    assert remote.deletes == 0
    assert g._undeploy_receipt(root, 'mainnet', APP) is None


def test_lost_delete_ack_reconciles_by_get_without_second_delete(tmp_path):
    root, remote, slh = make_fixture(tmp_path)
    remote.ack_lost = True
    first = call(root, remote, slh, execute=True)
    assert first['status'] == 'INCOMPLETE' and first['remote_mutation'] == 'UNKNOWN'
    assert remote.deletes == 1 and remote.deleted
    assert g._undeploy_receipt(root, 'mainnet', APP)['phase'] == 'INTENT'
    last = call(root, remote, slh, execute=True)
    assert last['status'] == 'ALREADY_UNDEPLOYED'
    assert remote.deletes == 1
    assert g._undeploy_receipt(root, 'mainnet', APP)['phase'] == 'VERIFIED'
    assert g._canonical_selection(root, 'mainnet')[0]['deployment']['undeployed'] is True


def test_lost_ack_while_application_remains_does_not_retry(tmp_path):
    root, remote, slh = make_fixture(tmp_path)
    remote.ack_lost = True
    remote.lingering = True
    assert call(root, remote, slh, execute=True)['status'] == 'INCOMPLETE'
    assert call(root, remote, slh, execute=True)['status'] == 'INCOMPLETE'
    assert remote.deletes == 1
    assert g._canonical_selection(root, 'mainnet')[0]['deployment'].get('undeployed') is not True


def test_unexplained_missing_application_is_not_idempotent_success(tmp_path):
    root, remote, slh = make_fixture(tmp_path)
    remote.deleted = True
    with pytest.raises(g.SEHGitError, match='without intentional undeploy receipt'):
        call(root, remote, slh, execute=True)
    assert remote.deletes == 0


def test_unauthorized_coolify_read_cannot_be_interpreted_as_missing(tmp_path):
    root, remote, slh = make_fixture(tmp_path)
    remote.forbidden_status = 403
    with pytest.raises(g.SEHGitError, match='HTTP 403'):
        call(root, remote, slh, execute=True)
    assert remote.deletes == 0


def test_mutation_requires_both_harness_approvals_and_no_target_override(tmp_path, monkeypatch):
    root, remote, slh = make_fixture(tmp_path)
    monkeypatch.setattr(g, '_client', lambda controller: remote)
    monkeypatch.setattr(g, '_public_runtime_probe', disconnected)
    opts = ['undeploy-seh', '--network','mainnet','--runtime-state-root',str(root),
            '--slh-state-dir',str(slh)]
    assert run(_parser().parse_args(opts))['status'] == 'PLAN_ONLY'
    with pytest.raises(HarnessError, match='Both mutation approvals'):
        run(_parser().parse_args(opts + ['--execute-mutations']))
    with pytest.raises(HarnessError, match='no alias'):
        run(_parser().parse_args(opts + ['--seh', 'bad']))
    done = run(_parser().parse_args(opts + [
        '--execute-mutations', '--yes-i-know-this-mutates-seh']))
    assert done['status'] == 'SEH_UNDEPLOYED'


def test_slh_manager_accepts_guarded_undeploy_but_not_relay_or_legacy_deploy(tmp_path):
    root, _, _ = make_fixture(tmp_path)
    captured=[]
    def runner(args, *, control):
        captured.append(args)
        return {'status':'PLAN_ONLY', 'remote_mutation': False}
    mgr=SLHSEHDeploymentManager(root=root, harness_runner=runner, state_dir=tmp_path / 'slh')
    body={'action':'undeploy-seh', 'execute':False,
          'expected_generation':None, 'confirm':None}
    assert mgr.harness(body)['status']=='PLAN_ONLY'
    with pytest.raises(SEHManagementRefused, match='SEH_MUTATION_CONFIRMATION_REQUIRED'):
        mgr.harness({**body, 'execute':True, 'expected_generation':2})
    with pytest.raises(SEHManagementRefused, match='SEH_GENERATION_CONFLICT'):
        mgr.harness({**body, 'execute':True, 'expected_generation':999,
                     'confirm':'UNDEPLOY_SEH'})
    generation = mgr.inspect({})['generation']
    assert mgr.harness({**body, 'execute':True, 'expected_generation':generation,
                        'confirm':'UNDEPLOY_SEH'})['status']=='PLAN_ONLY'
    assert captured[-1].operation=='undeploy-seh'
    assert captured[-1].execute_mutations is True
    assert captured[-1].slh_state_dir == (tmp_path / 'slh').resolve()
    for action in ('deploy', 'undeploy'):
        with pytest.raises(SEHManagementRefused, match='NOT_AVAILABLE'):
            mgr.harness({**body, 'action': action})


def test_explicit_later_deployment_clears_tombstone_but_keeps_old_receipt(tmp_path, monkeypatch):
    from tests.test_seh_deleted_application_recovery import setup, PUBLISHED, NEW
    root, slh, remote = setup(tmp_path)
    record, _, _, _ = g._canonical_selection(root, 'mainnet')
    uuid = record['deployment']['application_uuid']
    receipt = {'schema':'main-computer.seh-undeploy.v1',
               'application_uuid':uuid,
               'identity_binding':ds.identity_binding(record),
               'phase':'VERIFIED', 'requested_utc':'2026-11-12T00:00:00+00:00'}
    g._save_undeploy_receipt(root, 'mainnet', receipt)
    _, _, document = st.read(root, 'mainnet')
    identity=st.deployment_record(document, 'mainnet')
    old=deepcopy(record['deployment'])
    old.update({'undeployed':True,'undeployed_at':receipt['requested_utc']})
    ds.write(root, 'mainnet', identity, expected_uuid=uuid, deployment=old)
    monkeypatch.setattr(g.hub_coolify,'trigger_deploy',
                        lambda *a, **kw: {'ok':True, 'body':{'deployments':[
                            {'resource_uuid':NEW}]}})
    result = g.restore_deleted_canonical_application(
        root,'mainnet',execute=True,state_dir=slh,client=remote,source=PUBLISHED)
    assert result['status']=='REPLACEMENT_QUEUED_NOT_VERIFIED'
    new = g._canonical_selection(root,'mainnet')[0]['deployment']
    assert new['application_uuid'] == NEW
    assert 'undeployed' not in new and 'undeployed_at' not in new
    assert g._undeploy_receipt(root, 'mainnet', uuid) == receipt
    assert g._undeploy_receipt(root, 'mainnet', NEW) is None


def test_failed_state_commit_reconciles_missing_uuid_without_duplicate_delete(tmp_path, monkeypatch):
    root, remote, slh = make_fixture(tmp_path)
    write = ds.write
    def blocked(*args, **kwargs):
        raise OSError('disk temporarily unavailable')
    monkeypatch.setattr(ds,'write',blocked)
    first = call(root, remote, slh, execute=True)
    assert first['status']=='INCOMPLETE' and first['failed_stage']=='undeploy_state_commit'
    assert remote.deletes == 1
    monkeypatch.setattr(ds,'write',write)
    second = call(root, remote, slh, execute=True)
    assert second['status']=='ALREADY_UNDEPLOYED'
    assert remote.deletes == 1
    assert g._canonical_selection(root,'mainnet')[0]['deployment']['undeployed'] is True


def test_undeploy_admin_api_relay_cannot_reach_harness(tmp_path):
    from main_computer.slh.api import SLHAPI
    from main_computer.slh.seh_deployment_management import management_request
    from tests.test_slh_seh_coolify_management_backend import _Authority
    root, _, _ = make_fixture(tmp_path)
    def runner(args, *, control):
        assert args.operation == 'undeploy-seh'
        return {'status': 'PLAN_ONLY', 'remote_mutation': False}
    manager = SLHSEHDeploymentManager(root=root, harness_runner=runner)
    api=SLHAPI(_Authority(), seh_deployment_manager=manager)
    path='/v1/local/seh/deployment/harness'
    body={'action':'undeploy-seh', 'execute':False, 'expected_generation':None,'confirm':None}
    assert api.call('POST', path, body, token='', loopback=True)[0] == 401
    assert api.call('POST', path, body, token='good', loopback=False)[0] == 403
    assert api.call('POST', path, body, token='good', loopback=True)[0] == 200
    # The restricted SEH listener excludes the entire management router.
    from main_computer.slh.organization_service import OrganizationHTTPServer, OrganizationHTTPHandler
    import threading
    from http.client import HTTPConnection
    server=OrganizationHTTPServer(('127.0.0.1', 0), OrganizationHTTPHandler)
    server.seh_deployment_manager=manager
    server.authority=_Authority()
    server.seh_identity_only=True
    thread=threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client=HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            client.request('POST','/v1/org/admin/seh/deployment/harness',
                           json.dumps(body), {'Authorization':'Bearer good',
                                              'Content-Type':'application/json'})
            resp=client.getresponse()
            assert resp.status == 403
            assert json.loads(resp.read())['error']['code'] == 'SEH_OPERATION_NOT_ALLOWED'
        finally:
            client.close()
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def _historical_completed_recovery(state_dir, uuid, *, status='finished',
                                   build_uuid='completed-recovery-build'):
    import base64
    import hashlib
    _, public = service_identity(state_dir, create=False)
    return {'schema':'seh-coolify-recovery.v1',
            'old_uuid':'former-seh-uuid', 'new_uuid':uuid,
            'mother_digest':'historical-pre-recovery-digest',
            'git_revision':'a'*40,
            'key_sha256':hashlib.sha256(base64.b64decode(public, validate=True)).hexdigest(),
            'deployment_uuid':build_uuid, 'build_submission':'ACKNOWLEDGED'}


def test_finished_verified_historical_recovery_receipt_does_not_block_undeploy(tmp_path):
    root, remote, slh = make_fixture(tmp_path)
    checkpoint = _historical_completed_recovery(slh, APP)
    g._save_recovery_checkpoint(slh, checkpoint)
    original_request=remote.request
    def request(method, path, body=None):
        if method == 'GET' and path == '/api/v1/deployments/completed-recovery-build':
            return {'deployment_uuid':'completed-recovery-build',
                    'application_id':remote.app.get('id'), 'resource_uuid':APP,
                    'force_rebuild':True,'is_api':True,'restart_only':False,
                    'commit':'a'*40,'status':'finished'}
        return original_request(method,path,body)
    remote.request=request
    plan = call(root, remote, slh)
    assert plan['status'] == 'PLAN_ONLY'
    assert plan['recovery_receipt'] == 'COMPLETED_VERIFIED'
    assert plan['ready_for_undeploy'] is True
    assert remote.deletes == 0
    assert call(root, remote, slh, execute=True)['status'] == 'SEH_UNDEPLOYED'
    assert remote.deletes == 1
    # No reconciliation deletes/rewrites this historical recovery evidence.
    assert g._recovery_checkpoint(slh) == checkpoint


@pytest.mark.parametrize('receipt_change,build_status',[('uuid','finished'),('key','finished'),
                                                          ('pending','queued'),('failed','failed'),
                                                          ('unverified','unverified'),('intent','finished')])
def test_unresolved_recovery_receipt_refuses_undeploy_without_delete(tmp_path,
                                                                    receipt_change, build_status):
    root, remote, slh = make_fixture(tmp_path)
    checkpoint = _historical_completed_recovery(slh, APP)
    if receipt_change == 'uuid':checkpoint['new_uuid']='another-canonical-app'
    if receipt_change == 'key':checkpoint['key_sha256']='0'*64
    if receipt_change == 'intent':checkpoint['build_submission']='INTENT'
    g._save_recovery_checkpoint(slh, checkpoint)
    original_request=remote.request
    def request(method,path,body=None):
        if method=='GET' and path=='/api/v1/deployments/completed-recovery-build':
            if receipt_change=='unverified':return SimpleNamespace(ok=False,status=404,body={})
            return {'deployment_uuid':'completed-recovery-build',
                    'application_id':remote.app.get('id'), 'resource_uuid':APP,
                    'force_rebuild':True, 'is_api':True, 'restart_only':False,
                    'commit':'a'*40, 'status':build_status}
        return original_request(method,path,body)
    remote.request=request
    with pytest.raises(g.SEHGitError, match='Recovery|recovery'):
        call(root, remote, slh, execute=True)
    assert remote.deletes == 0
    assert g._undeploy_receipt(root, 'mainnet', APP) is None
    assert g._recovery_checkpoint(slh) == checkpoint


@pytest.mark.parametrize('status,expected_exit',[('REFUSED',2),('PLAN_ONLY',0),
                                                 ('SEH_UNDEPLOYED',0),('INCOMPLETE',2)])
def test_undeploy_cli_reporter_never_reinvokes_harness_or_crashes(monkeypatch,capsys,
                                                                status,expected_exit):
    import seh_mutate_harness as cli
    calls=[]
    def fake_run(args):
        calls.append(args.operation)
        return {'status':status,'ready_for_undeploy':True}
    monkeypatch.setattr(cli,'run',fake_run)
    code = cli.main(['undeploy-seh','--network','mainnet'])
    captured=capsys.readouterr()
    assert code==expected_exit
    assert calls==['undeploy-seh']
    assert 'NameError' not in captured.out + captured.err
    assert f'"status": "{status}"' in captured.out
    assert ('--execute-mutations' in captured.err) is (status=='PLAN_ONLY')
