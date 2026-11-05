"""State-backed SEH Git deployment: selected Coolify host, exact placement, CAS, no duplicates."""
import json
from pathlib import Path

import pytest

from tools.seh_control import identity as st
from tools.seh_control import git_deployment as g
from tools.seh_control import dns_probe as dprobe
from tools.seh_control.commands import SEHControl
from tests.test_seh_identity_harness import fixture, SECRET, publickey

SOURCE = {'repository':'https://github.com/johnrraymond/main_computer', 'branch':'main',
          'revision':'a'*40, 'dockerfile_location':'/deploy/seh/Dockerfile'}


class FakeHost:
    def __init__(self, origin='https://host.example.org', env='mainnet-hubs'):
        self.origin=origin
        self.env=env
        self.calls=[]
        self.apps=[]
        self.envs={}
    def request(self, method, path, body=None):
        self.calls.append((method,path))
        if method=='GET' and path=='/api/v1/projects/proj-a':
            return {'uuid':'proj-a','name':'My first project'}
        if method=='GET' and path=='/api/v1/projects/proj-b':
            return {'uuid':'proj-b','name':'My first project'}
        if method=='GET' and path.endswith('/environments'):
            return [{'name':self.env,'uuid':'env-a'}]
        if method=='GET' and path in ('/api/v1/servers/server-a','/api/v1/servers/server-b'):
            return {'uuid':path.rsplit('/',1)[-1],'is_usable':True}
        if method=='GET' and path=='/api/v1/applications':
            return list(self.apps)
        if method=='GET' and path.startswith('/api/v1/applications/') and path.endswith('/envs'):
            uuid=path.split('/')[-2]
            return self.envs.get(uuid,[])
        if method=='GET' and path.startswith('/api/v1/applications/'):
            uuid=path.rsplit('/',1)[-1]
            return next((x for x in self.apps if x['uuid']==uuid),{})
        if method=='POST' and path=='/api/v1/applications/public':
            obj={'uuid':'app-one','name':body['name'],'description':body['description'],
                 'project_uuid':body['project_uuid'],'server_uuid':body['server_uuid'],
                 'environment_name':body['environment_name'], 'domains':body['domains']}
            self.apps.append(obj)
            return {'uuid':obj['uuid']}
        if method=='PATCH' and path.endswith('/envs/bulk'):
            uuid=path.split('/')[-3]
            current = {r['key']: r for r in self.envs.get(uuid, [])}
            for x in body['data']:
                current[x['key']] = {'key': x['key'], 'value': x['value']}
            self.envs[uuid] = list(current.values())
            return {'ok':True}
        if method=='PATCH' and path.startswith('/api/v1/applications/'):
            uuid=path.split('/')[-1]
            for app in self.apps:
                if app['uuid']==uuid: app.update(body)
            return {'ok':True}
        if method=='DELETE' and '/envs/' in path:
            uuid=path.split('/')[-3]
            key=path.split('/')[-1]
            rows=self.envs.get(uuid, [])
            if not any(row.get('uuid') == key for row in rows):
                raise AssertionError('Unknown env UUID')
            self.envs[uuid] = [row for row in rows if row.get('uuid') != key]
            return {'ok':True}
        raise AssertionError((method,path))


def setup(tmp_path, *, other=False):
    root,_=fixture(tmp_path)
    ctl=SEHControl(root)
    ctl.change('reserve','mainnet','company',None,'reserve',slh_id='slh')
    alias='coolify-b' if other else 'coolify-a'
    ctl.change('coolify-copy','mainnet','company',None,'copy',alias='primary',
               source=f'networks.mainnet.coolify.controllers.{alias}')
    return root,ctl


def prepare(root,host,**opts):
    opts.setdefault('public_key', publickey())
    return g.prepare(root,'mainnet','company',client=host,source=SOURCE,**opts)


def test_prepare_uses_selected_controller_and_never_leaks_credential(tmp_path):
    root,_=setup(tmp_path,other=True)
    host=FakeHost(origin='http://host-b.example.org:8000')
    result=prepare(root,host)
    assert result['status']=='PREPARED'
    assert result['placement']['project_uuid']=='proj-b'
    assert result['placement']['server_uuid']=='server-b'
    plan=g._loaded(root,'mainnet','company')
    assert plan['coolify_origin']=='https://coolify-b.example.org'
    assert plan['placement']['environment_name']=='mainnet-hubs'
    assert SECRET not in json.dumps(plan)
    assert not any(method!='GET' for method,_ in host.calls)
    assert result['management_transport']=='HTTPS'


def test_wrong_project_or_environment_refuses_before_mutation(tmp_path):
    root,_=setup(tmp_path)
    with pytest.raises(g.SEHGitError,match='project'):
        prepare(root,FakeHost(),project_name='Wrong project')
    with pytest.raises(g.SEHGitError,match='environment'):
        prepare(root,FakeHost(env='wrong'))
    assert not g._plan_path(root,'mainnet','company').exists()


def test_deploy_creates_exact_application_records_uuid_then_idempotent_reuses(tmp_path,monkeypatch):
    root,ctl=setup(tmp_path)
    host=FakeHost()
    stable_key = publickey()  # An SLH cannot silently rotate its signing identity on repeat deployment.
    prepare(root,host,public_key=stable_key)
    monkeypatch.setattr(g.hub_coolify,'trigger_deploy', lambda client, **kwargs: {'ok':True})
    first=g.deploy(root,'mainnet','company',operation_id='git-create',client=host,source=SOURCE)
    assert first['status']=='DEPLOY_QUEUED' and first['created']
    assert first['application_uuid']=='app-one'
    _,_,doc=st.read(root,'mainnet')
    assert 'application_uuid' not in doc['networks']['mainnet']['sehs']['company']['deployment']
    assert g._selections(root,'mainnet','company')[0]['deployment']['application_uuid']=='app-one'
    assert len([x for x in host.calls if x==('POST','/api/v1/applications/public')])==1
    assert all('api_token' not in json.dumps(x) for x in host.envs.values())
    with pytest.raises(g.SEHGitError,match='SEH deployment changed'):
        g.deploy(root,'mainnet','company',operation_id='no-reprepare',client=host,source=SOURCE)
    prepare(root,host,public_key=stable_key)
    second=g.deploy(root,'mainnet','company',operation_id='git-redeploy',client=host,source=SOURCE)
    assert not second['created']
    assert len([x for x in host.calls if x==('POST','/api/v1/applications/public')])==1


def test_ambiguous_creation_can_adopt_after_reprepare(tmp_path,monkeypatch):
    root,_=setup(tmp_path)
    host=FakeHost()
    prepare(root,host)
    monkeypatch.setattr(g.hub_coolify,'trigger_deploy', lambda client, **kwargs: {'ok':True})
    # Service exists remotely but state commit was interrupted; exact ownership
    # permits safe adoption, avoiding another create POST.
    plan=g._loaded(root,'mainnet','company')
    host.apps.append({'uuid':'app-one','name':plan['application_name'],'description':plan['ownership_marker'],
                      'project_uuid':'proj-a','server_uuid':'server-a','environment_name':'mainnet-hubs'})
    result=g.deploy(root,'mainnet','company',operation_id='adopt',client=host,source=SOURCE)
    assert not result['created']
    assert ('POST','/api/v1/applications/public') not in host.calls
    assert SEHControl(root).inspect('mainnet','company')['deployment']['application_uuid']=='app-one'


def test_ownership_collision_and_modified_plan_refused(tmp_path,monkeypatch):
    root,_=setup(tmp_path)
    host=FakeHost()
    prepare(root,host)
    spec=g._loaded(root,'mainnet','company')
    host.apps.append({'uuid':'other','name':spec['application_name'],'description':'unrelated service'})
    monkeypatch.setattr(g.hub_coolify,'trigger_deploy', lambda client, **kwargs: {'ok':True})
    with pytest.raises(g.SEHGitError,match='collision'):
        g.deploy(root,'mainnet','company',operation_id='collision',client=host,source=SOURCE)
    assert ('POST','/api/v1/applications/public') not in host.calls
    path=g._plan_path(root,'mainnet','company')
    raw=json.loads(path.read_text())
    raw['placement']['project_uuid']='evil'
    path.write_text(json.dumps(raw))
    with pytest.raises(g.SEHGitError,match='modified'):
        g.deploy(root,'mainnet','company',operation_id='tampered',client=host,source=SOURCE)


def test_disallow_git_changes_after_prepare(tmp_path):
    root,_=setup(tmp_path)
    host=FakeHost()
    prepare(root,host)
    other={**SOURCE,'revision':'b'*40}
    with pytest.raises(g.SEHGitError,match='Git source changed'):
        g.deploy(root,'mainnet','company',operation_id='changed',client=host,source=other)
    assert not any(method=='POST' for method,_ in host.calls)


def test_cli_prepare_preflight_deploy_selected_coolify_host(tmp_path,monkeypatch,capsys):
    from tools.seh_harness import main
    root,_=setup(tmp_path,other=True)
    host=FakeHost()
    monkeypatch.setattr(g,'_client',lambda ctl:host)
    monkeypatch.setattr(g,'git_source',lambda *args,**kwargs:SOURCE)
    monkeypatch.setattr(g,'_public_key',publickey)
    monkeypatch.setattr(dprobe,'_resolve',lambda hostname:['104.16.12.3'])
    monkeypatch.setattr(g.hub_coolify,'trigger_deploy',lambda client,**kwargs:{'ok':True})
    args=['--network','mainnet','--seh','company','--runtime-state-root',str(root)]
    assert main(['prepare',*args])==0
    report=json.loads(capsys.readouterr().out)
    assert report['status']=='PREPARED'
    assert report['placement']['project_uuid']=='proj-b'
    assert main(['preflight',*args])==0
    assert json.loads(capsys.readouterr().out)['status']=='CREATE_ELIGIBLE'
    assert main(['deploy',*args])==2
    assert json.loads(capsys.readouterr().err)['status']=='REFUSED'
    assert main(['deploy',*args,'--execute'])==0
    output=capsys.readouterr().out
    assert json.loads(output)['status']=='DEPLOY_QUEUED'
    assert SECRET not in output
    assert main(['status',*args])==0
    assert json.loads(capsys.readouterr().out)['status']=='COOLIFY_OBSERVED'


def test_different_host_origin_is_selected_from_active_identity(tmp_path,monkeypatch):
    root,ctl=setup(tmp_path)
    # Copy second controller into an alias; switching active identity changes
    # host and project and invalidates previously prepared plans via CAS.
    host_a=FakeHost()
    prepare(root,host_a)
    ctl.change('coolify-copy','mainnet','company',None,'copy-second',alias='other',
               source='networks.mainnet.coolify.controllers.coolify-b')
    ctl.change('coolify-select','mainnet','company',None,'select-second',alias='other')
    host_b=FakeHost()
    prepared=prepare(root,host_b)
    assert prepared['placement']['project_uuid']=='proj-b'
    assert g._loaded(root,'mainnet','company')['coolify_origin']=='https://coolify-b.example.org'


def test_git_source_requires_actual_published_files_not_local_head(tmp_path, monkeypatch):
    import subprocess
    repo = tmp_path / 'checkout'
    repo.mkdir()
    remote = tmp_path / 'origin.git'

    def git(*args, cwd=repo):
        result = subprocess.run(['git', *args], cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, encoding='utf-8', check=True)
        return result.stdout.strip()

    git('init', '-b', 'main')
    git('config', 'user.name', 'Unit Tests')
    git('config', 'user.email', 'test@example.org')
    for rel in g.SOURCES:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((Path(__file__).resolve().parents[1] / rel).read_bytes())
    git('add', '.')
    git('commit', '-m', 'source')
    subprocess.run(['git', 'init', '--bare', str(remote)], capture_output=True, check=True)
    git('remote', 'add', 'origin', str(remote))
    git('push', 'origin', 'main')
    original_run = g.subprocess.run

    def fake_run(argv, **kwargs):
        if len(argv) > 4 and argv[3:6] == ['remote', 'get-url', 'origin']:
            return subprocess.CompletedProcess(argv, 0,
                'https://github.com/johnrraymond/main_computer.git\n', '')
        return original_run(argv, **kwargs)

    monkeypatch.setattr(g.subprocess, 'run', fake_run)
    assert g.git_source(repo)['revision'] == git('rev-parse', 'HEAD')
    (repo / 'main_computer/seh/relay.py').write_text('changed but not pushed\n')
    with pytest.raises(g.SEHGitPublicationError, match='build inputs differ') as dirty:
        g.git_source(repo)
    assert 'main_computer/seh/relay.py' in str(dirty.value)
    assert "git commit --only -m 'Update Hub deployment source'" in dirty.value.git_command
    assert 'git push origin HEAD' in dirty.value.git_command
    git('add', '.')
    git('commit', '-m', 'still unpublished')
    with pytest.raises(g.SEHGitPublicationError, match='build inputs differ') as unpushed:
        g.git_source(repo)
    assert unpushed.value.git_command == ''
    assert 'If HEAD is the intended revision, run: git push origin HEAD' in str(unpushed.value)


def test_preflight_read_only_and_status_before_create(tmp_path):
    root,_=setup(tmp_path)
    host=FakeHost()
    prepare(root,host)
    report=g.preflight(root,'mainnet','company',client=host,source=SOURCE)
    assert report['status']=='CREATE_ELIGIBLE'
    report=g.status(root,'mainnet','company',client=host,dns_resolver=lambda hostname:['104.16.12.3'])
    assert report['status']=='NOT_DEPLOYED'
    assert report['dns_ready'] is False
    assert not any(method not in ('GET',) for method,_ in host.calls)


def test_seh_git_guard_does_not_run_repository_mutations():
    assert not hasattr(g, '_isolated_git_publication_command')


def test_status_reports_dns_independently_from_running_coolify_and_without_mutation(tmp_path):
    root,_ = setup(tmp_path)
    host = FakeHost()
    prepare(root,host)
    # Register an application (mock) in verified Mother state.
    g.st.mutate(root, 'mainnet', None, 'status-setup',
                lambda doc, generation, base: doc['networks']['mainnet']['sehs']['company']['deployment'].update(application_uuid='app-one'))
    host.apps.append({'uuid':'app-one', 'status':'running:healthy', 'domains':'https://external.greatlibrary.io:8080'})
    direct = g.status(root,'mainnet','company',client=host,dns_resolver=lambda h:['159.203.184.182'])
    assert direct['status']=='COOLIFY_OBSERVED'
    assert direct['coolify_status']=='running:healthy'
    assert direct['public_wss_verified'] is False
    assert direct['dns']['status']=='DNS_NOT_CLOUDFLARE'
    assert direct['dns_ready'] is False
    assert all(method == 'GET' for method, _ in host.calls[-1:])
    cloudflare = g.status(root,'mainnet','company',client=host,dns_resolver=lambda h:['104.16.12.3'])
    assert cloudflare['dns_ready'] is True
    assert cloudflare['dns']['coolify_domain_matches'] is True


def test_status_without_local_plan_uses_live_coolify_domain(tmp_path):
    root,_ = setup(tmp_path)
    host = FakeHost()
    prepare(root,host,public_url='https://custom.example.org')
    g._plan_path(root,'mainnet','company').unlink()
    g.st.mutate(root, 'mainnet', None, 'status-without-plan',
                lambda doc, generation, base: doc['networks']['mainnet']['sehs']['company']['deployment'].update(application_uuid='app-one'))
    host.apps.append({'uuid':'app-one', 'status':'running:healthy','fqdn':'https://other.example.net:8080'})
    report = g.status(root,'mainnet','company',client=host,dns_resolver=lambda h:['104.16.12.3'])
    assert report['public_url']=='https://other.example.net'
    assert report['dns']['hostname']=='other.example.net'
    assert report['dns_ready'] is True

class CoolifyDocumentedGetHost(FakeHost):
    """Realistic Coolify application GET omits project/server UUIDs and env name."""
    def __init__(self, *, actual_environment_id=42, server_has_app=True):
        super().__init__()
        self.actual_environment_id = actual_environment_id
        self.server_has_app = server_has_app

    def request(self, method, path, body=None):
        if method == 'GET' and path == '/api/v1/projects/proj-a/env-a':
            self.calls.append((method, path))
            return {'id':42, 'name':'mainnet-hubs', 'project_id':17}
        if method == 'GET' and path == '/api/v1/servers/server-a/resources':
            self.calls.append((method, path))
            return ([{'uuid':app['uuid'],'type':'application'} for app in self.apps]
                    if self.server_has_app else [])
        if method == 'GET' and path.startswith('/api/v1/applications/') and not path.endswith('/envs'):
            self.calls.append((method,path))
            uuid=path.rsplit('/',1)[-1]
            app=next((x for x in self.apps if x['uuid']==uuid),None)
            if not app:
                return {}
            return {key:value for key,value in app.items()
                    if key not in ('project_uuid','server_uuid','environment_name')} | {
                       'environment_id':self.actual_environment_id}
        return super().request(method,path,body)


def test_preflight_existing_app_with_documented_coolify_get_schema(tmp_path):
    root,_=setup(tmp_path)
    host=CoolifyDocumentedGetHost()
    prepare(root,host)
    host.apps.append({'uuid':'app-one','name':'mc-seh-company',
                      'description':g._loaded(root,'mainnet','company')['ownership_marker'],
                      'project_uuid':'proj-a','server_uuid':'server-a',
                      'environment_name':'mainnet-hubs'})
    # The UUID is frozen as it would be after a successful initial deploy.
    def save(doc,gen,base):
        st.seh_doc(doc,'mainnet','company')['deployment']['application_uuid']='app-one'
    _,state,_=st.read(root,'mainnet')
    st.mutate(root,'mainnet',state.binding.generation,'record-app',save)
    prepare(root,host)
    result=g.preflight(root,'mainnet','company',client=host,source=SOURCE)
    assert result['status']=='EXISTING_APPLICATION_VERIFIED'
    assert result['application_uuid']=='app-one'
    assert ('GET','/api/v1/projects/proj-a/env-a') in host.calls
    assert ('GET','/api/v1/servers/server-a/resources') in host.calls
    assert not any(method!='GET' for method,_ in host.calls)


@pytest.mark.parametrize('environment_id,server_has_app,match', [
    (43,True,'environment ID'),
    (42,False,'not listed on selected Coolify server'),
])
def test_preflight_refuses_wrong_live_placement_even_when_uuid_owned(
        tmp_path,environment_id,server_has_app,match):
    root,_=setup(tmp_path)
    host=CoolifyDocumentedGetHost(actual_environment_id=environment_id,
                                   server_has_app=server_has_app)
    prepare(root,host)
    host.apps.append({'uuid':'app-one','name':'mc-seh-company',
                      'description':g._loaded(root,'mainnet','company')['ownership_marker']})
    with pytest.raises(g.SEHGitError,match=match):
        g.preflight(root,'mainnet','company',client=host,source=SOURCE)
    assert not any(method!='GET' for method,_ in host.calls)


def test_preflight_refuses_missing_live_environment_evidence(tmp_path):
    root,_=setup(tmp_path)
    host=FakeHost()
    prepare(root,host)
    host.apps.append({'uuid':'app-one','name':'mc-seh-company',
                      'description':g._loaded(root,'mainnet','company')['ownership_marker']})
    with pytest.raises(g.SEHGitError,match='no environment_id'):
        g.preflight(root,'mainnet','company',client=host,source=SOURCE)


def test_deploy_reuses_existing_application_with_documented_coolify_response(tmp_path, monkeypatch):
    root,_=setup(tmp_path)
    host=CoolifyDocumentedGetHost()
    prepare(root,host)
    plan=g._loaded(root,'mainnet','company')
    host.apps.append({'uuid':'app-one','name':plan['application_name'],
                      'description':plan['ownership_marker']})
    triggered=[]
    monkeypatch.setattr(g.hub_coolify,'trigger_deploy',
                        lambda client,**kwargs: triggered.append(kwargs) or {'ok':True})
    deployed=g.deploy(root,'mainnet','company',operation_id='recover-existing',
                      client=host,source=SOURCE)
    assert deployed['status']=='DEPLOY_QUEUED'
    assert deployed['application_uuid']=='app-one' and deployed['created'] is False
    assert ('POST','/api/v1/applications/public') not in host.calls
    assert triggered and triggered[0]['application_uuid']=='app-one'
    assert SEHControl(root).inspect('mainnet','company')['deployment']['application_uuid']=='app-one'


@pytest.mark.parametrize('existing_key', ['different-public-key', '********'])
def test_existing_application_must_not_silently_replace_slh_public_key(tmp_path, existing_key):
    root, _ = setup(tmp_path)
    host = FakeHost()
    prepare(root, host)
    plan = g._loaded(root, 'mainnet', 'company')
    host.apps.append({'uuid': 'app-one', 'name': plan['application_name'],
                      'description': plan['ownership_marker'], 'project_uuid': 'proj-a',
                      'server_uuid': 'server-a', 'environment_name': 'mainnet-hubs'})
    host.envs['app-one'] = [{'key': 'SEH_SLH_PUBLIC_KEY_B64', 'value': existing_key}]
    with pytest.raises(g.SEHGitError, match='refusing silent key rotation'):
        g.preflight(root, 'mainnet', 'company', client=host, source=SOURCE)
    with pytest.raises(g.SEHGitError, match='refusing silent key rotation'):
        g.deploy(root, 'mainnet', 'company', operation_id='no-key-rotation', client=host, source=SOURCE)
    assert all(method == 'GET' for method, _ in host.calls)


def test_existing_application_with_same_slh_public_key_can_redeploy(tmp_path, monkeypatch):
    root, _ = setup(tmp_path)
    host = FakeHost()
    prepare(root, host)
    plan = g._loaded(root, 'mainnet', 'company')
    host.apps.append({'uuid': 'app-one', 'name': plan['application_name'],
                      'description': plan['ownership_marker'], 'project_uuid': 'proj-a',
                      'server_uuid': 'server-a', 'environment_name': 'mainnet-hubs'})
    host.envs['app-one'] = [{'key': 'SEH_SLH_PUBLIC_KEY_B64', 'value': plan['slh_public_key_b64']}]
    assert g.preflight(root, 'mainnet', 'company', client=host, source=SOURCE)['status'] == 'EXISTING_APPLICATION_VERIFIED'
    monkeypatch.setattr(g.hub_coolify, 'trigger_deploy', lambda client, **kwargs: {'ok': True})
    result = g.deploy(root, 'mainnet', 'company', operation_id='same-key', client=host, source=SOURCE)
    assert result['status'] == 'DEPLOY_QUEUED'
    assert ('POST', '/api/v1/applications/public') not in host.calls


def test_infrastructure_only_prepare_does_not_load_slh_signer(tmp_path, monkeypatch):
    root, _ = setup(tmp_path)
    host = FakeHost()
    monkeypatch.setattr(g, '_public_key', lambda: (_ for _ in ()).throw(AssertionError('local signer read')))
    result = g.prepare(root, 'mainnet', 'company', client=host, source=SOURCE,
                       public_url='https://seh.example.org')
    plan = g._loaded(root, 'mainnet', 'company')
    assert result['status'] == 'PREPARED'
    assert plan['slh_public_key_b64'] == ''
    assert 'SEH_ORGANIZATION_ID' not in g._envs(plan)
    assert 'SEH_SLH_PUBLIC_KEY_B64' not in g._envs(plan)


def test_redeploy_preserves_existing_remote_slh_binding_without_local_key(tmp_path, monkeypatch):
    root, _ = setup(tmp_path)
    host = FakeHost()
    g.prepare(root, 'mainnet', 'company', client=host, source=SOURCE,
              public_url='https://seh.example.org')
    plan = g._loaded(root, 'mainnet', 'company')
    host.apps.append({'uuid': 'app-one', 'name': plan['application_name'],
                      'description': plan['ownership_marker'], 'project_uuid': 'proj-a',
                      'server_uuid': 'server-a', 'environment_name': 'mainnet-hubs'})
    protected = {'SEH_ORGANIZATION_ID': 'org', 'SEH_SLH_ID': 'slh',
                 'SEH_SLH_PUBLIC_KEY_B64': 'public-binding-preserved'}
    host.envs['app-one'] = [{'key': k, 'value': v} for k,v in protected.items()]
    monkeypatch.setattr(g.hub_coolify, 'trigger_deploy', lambda *a, **k: {'ok': True})
    result = g.deploy(root,'mainnet','company',operation_id='infra-redeploy',client=host,source=SOURCE)
    assert result['status'] == 'DEPLOY_QUEUED'
    updated = {x['key']:x['value'] for x in host.envs['app-one']}
    for k,v in protected.items():
        assert updated[k] == v


def test_unbound_new_registration_and_infrastructure_only_plan(tmp_path):
    root, _ = fixture(tmp_path)
    ctl = SEHControl(root)
    ctl.change('reserve', 'mainnet', 'relay-unbound', None, 'reserve-unbound')
    ctl.change('coolify-copy', 'mainnet', 'relay-unbound', None, 'copy-unbound',
               alias='primary',source='networks.mainnet.coolify.controllers.coolify-a')
    _, _, doc = st.read(root, 'mainnet')
    rec = doc['networks']['mainnet']['sehs']['relay-unbound']
    assert rec['schema'] == 'main-computer.seh-identity.v3'
    assert 'organization_id' not in rec and 'slh_id' not in rec
    host = FakeHost()
    with pytest.raises(g.SEHGitError, match='requires registered SLH identity'):
        g.prepare(root, 'mainnet', 'relay-unbound', client=host, source=SOURCE,
                  public_url='https://seh.example.org')
