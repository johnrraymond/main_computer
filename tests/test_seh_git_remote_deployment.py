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
            self.envs[uuid]=[{'key':x['key'],'value':x['value']} for x in body['data']]
            return {'ok':True}
        if method=='PATCH' and path.startswith('/api/v1/applications/'):
            return {'ok':True}
        raise AssertionError((method,path))


def setup(tmp_path, *, other=False):
    root,_=fixture(tmp_path)
    ctl=SEHControl(root)
    ctl.change('reserve','mainnet','company',None,'reserve',organization_id='org',slh_id='slh')
    alias='coolify-b' if other else 'coolify-a'
    ctl.change('coolify-copy','mainnet','company',None,'copy',alias='primary',
               source=f'networks.mainnet.coolify.controllers.{alias}')
    return root,ctl


def prepare(root,host,**opts):
    return g.prepare(root,'mainnet','company',client=host,source=SOURCE,public_key=publickey(),**opts)


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
    prepare(root,host)
    monkeypatch.setattr(g.hub_coolify,'trigger_deploy', lambda client, **kwargs: {'ok':True})
    first=g.deploy(root,'mainnet','company',operation_id='git-create',client=host,source=SOURCE)
    assert first['status']=='DEPLOY_QUEUED' and first['created']
    assert first['application_uuid']=='app-one'
    _,_,doc=st.read(root,'mainnet')
    assert doc['networks']['mainnet']['sehs']['company']['deployment']['application_uuid']=='app-one'
    assert len([x for x in host.calls if x==('POST','/api/v1/applications/public')])==1
    assert all('api_token' not in json.dumps(x) for x in host.envs.values())
    with pytest.raises(g.SEHGitError,match='Identity changed'):
        g.deploy(root,'mainnet','company',operation_id='no-reprepare',client=host,source=SOURCE)
    prepare(root,host)
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


def test_git_source_requires_exact_pushed_revision_and_clean_deployed_inputs(tmp_path,monkeypatch):
    import subprocess
    repo=tmp_path/'checkout'
    repo.mkdir()
    remote=tmp_path/'origin.git'
    def git(*args,cwd=repo):
        result=subprocess.run(['git',*args],cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              encoding='utf-8',check=True)
        return result.stdout.strip()
    git('init','-b','main')
    git('config','user.name','Unit Tests')
    git('config','user.email','test@example.org')
    for rel in g.SOURCES:
        path=repo/rel
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('test source\n',encoding='utf-8')
    git('add','.')
    git('commit','-m','source')
    subprocess.run(['git','init','--bare',str(remote)],capture_output=True,check=True)
    git('remote','add','origin',str(remote))
    git('push','origin','main')
    # The source guard validates origin's identity separately from Git's push.
    original_run=g.subprocess.run
    def fake_run(argv,**kwargs):
        if len(argv)>4 and argv[3:6]==['remote','get-url','origin']:
            return subprocess.CompletedProcess(argv,0,'https://github.com/johnrraymond/main_computer.git\n','')
        return original_run(argv,**kwargs)
    monkeypatch.setattr(g.subprocess,'run',fake_run)
    assert g.git_source(repo)['revision']==git('rev-parse','HEAD')
    (repo/'main_computer/seh/relay.py').write_text('changed but not pushed\n')
    with pytest.raises(g.SEHGitError,match='uncommitted') as dirty:
        g.git_source(repo)
    assert dirty.value.git_command == (
        "git add -- 'main_computer/seh/relay.py'; "
        "if ($LASTEXITCODE -eq 0) { git commit --only -m 'Update SEH deployment source' -- 'main_computer/seh/relay.py' }; "
        "if ($LASTEXITCODE -eq 0) { git push origin HEAD }")
    git('add','.')
    git('commit','-m','unpushed change')
    with pytest.raises(g.SEHGitError,match='not the pushed') as unpushed:
        g.git_source(repo)
    assert unpushed.value.git_command == 'git push origin HEAD'


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


def test_dirty_command_only_stages_relevant_seh_source():
    porcelain = (' M main_computer/seh/relay.py\n'
                 '?? deploy/seh/Dockerfile\n'
                 '?? runtime/state/mother/identity.private.yaml\n')
    paths = g._dirty_source_paths(porcelain)
    assert paths == ['deploy/seh/Dockerfile', 'main_computer/seh/relay.py']
    command = g._git_commit_push_command(paths)
    assert command.startswith("git add -- 'deploy/seh/Dockerfile' 'main_computer/seh/relay.py';")
    assert 'identity.private' not in command
    assert "git add ." not in command
    assert g._git_commit_push_command(['runtime/state/mother/identity.private.yaml']) == ''


def test_cli_prints_git_command_on_dirty_prepare(tmp_path,monkeypatch,capsys):
    from tools.seh_harness import main
    root,_=setup(tmp_path)
    monkeypatch.setattr(g,'_client',lambda controller: FakeHost())
    def dirty(*args, **kwargs):
        raise g.SEHGitError('SEH runtime source has uncommitted changes',
                            git_command=g._git_commit_push_command(['deploy/seh/Dockerfile']))
    monkeypatch.setattr(g,'git_source',dirty)
    args=['--network','mainnet','--seh','company','--runtime-state-root',str(root)]
    assert main(['prepare',*args]) == 2
    result=capsys.readouterr()
    assert 'SEH_GIT_COMMIT_PUSH_COMMAND:' in result.err
    assert "git add -- 'deploy/seh/Dockerfile';" in result.err
    assert json.loads(result.err.splitlines()[-1])['status']=='REFUSED'
    assert 'api_token' not in result.err and SECRET not in result.err


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
