"""Explicit, separately authorized SEH SLH trust enrollment and rotation."""
import base64
import json
from pathlib import Path

import pytest

from main_computer.slh.authenticator import generate_identity
from seh_mutate_harness import _parser, run
from tools.seh_control import git_deployment as git
from tests.test_seh_git_remote_deployment import FakeHost, setup


def public_b64():
    _, pub = generate_identity()
    return base64.b64encode(pub.encode('ascii')).decode('ascii')


def configured(tmp_path, monkeypatch):
    root, control = setup(tmp_path)
    host = FakeHost()
    app = {'uuid': 'app-one', 'name': 'mc-seh-company',
           'description': 'main-computer-seh:git-v3 target=company slh=slh',
           'domains': 'https://seh.example.org', 'project_uuid': 'proj-a',
           'server_uuid': 'server-a', 'environment_name': 'mainnet-hubs'}
    host.apps.append(app)
    control.change('record-service','mainnet','company',None,'record',service_uuid='ignored')
    from tools.seh_control import identity as st
    st.mutate(root,'mainnet',None,'set-app',
              lambda doc,gen,base: st.seh_doc(doc,'mainnet','company')['deployment'].update(application_uuid='app-one'))
    monkeypatch.setattr(git.hub_coolify,'trigger_deploy',lambda *a,**k:{'ok':True})
    return root,host,control


def test_new_binding_requires_execute_and_reads_back(tmp_path, monkeypatch):
    root, host, _ = configured(tmp_path, monkeypatch)
    key = public_b64()
    plan = git.bind_slh(root,'mainnet','company',slh_id='slh',
                        public_key_b64=key,client=host)
    assert plan['status']=='PLAN_ONLY'
    assert not any(m != 'GET' for m,p in host.calls)
    receipt = git.bind_slh(root,'mainnet','company',slh_id='slh',
                           public_key_b64=key,execute=True,client=host)
    assert receipt['status']=='TRUST_DEPLOY_QUEUED_NOT_VERIFIED'
    assert receipt['trust_readback_verified'] is True
    assert receipt['ready'] is False and receipt['public_wss_verified'] is False
    assert all('PRIVATE KEY' not in str(x) for x in host.envs.values())
    assert {r['key'] for r in host.envs['app-one']} == {
        'SEH_SLH_ID','SEH_SLH_PUBLIC_KEY_B64'}
    assert git.bind_slh(root,'mainnet','company',slh_id='slh',
                        public_key_b64=key,execute=True,client=host)['status'] == 'TRUST_DEPLOY_QUEUED_NOT_VERIFIED'


def test_rotation_requires_explicit_second_approval(tmp_path, monkeypatch):
    root,host,_ = configured(tmp_path, monkeypatch)
    old,new=public_b64(),public_b64()
    host.envs['app-one'] = [                           {'key':'SEH_SLH_ID','value':'slh'},
                           {'key':'SEH_SLH_PUBLIC_KEY_B64','value':old}]
    with pytest.raises(git.SEHGitError,match='replace-existing-trust'):
        git.bind_slh(root,'mainnet','company',slh_id='slh',
                     public_key_b64=new,execute=True,client=host)
    assert not any(m != 'GET' for m,p in host.calls)
    result=git.bind_slh(root,'mainnet','company',slh_id='slh',
                        public_key_b64=new,replace_existing=True,execute=True,client=host)
    assert result['status']=='TRUST_DEPLOY_QUEUED_NOT_VERIFIED'
    assert result['key_change'] is True


@pytest.mark.parametrize('fault', ['partial','duplicate','wrong-slh','wrong-app','bad-key'])
def test_refuses_unverifiable_or_inconsistent_binding(tmp_path,monkeypatch,fault):
    root,host,_ = configured(tmp_path,monkeypatch)
    key = public_b64()
    if fault=='partial':
        host.envs['app-one']=[{'key':'SEH_SLH_ID','value':'slh'}]
    elif fault=='duplicate':
        host.envs['app-one']=[{'key':'SEH_SLH_ID','value':'slh'}]*2
    elif fault=='wrong-slh':
        host.envs['app-one']=[{'key':'SEH_SLH_ID','value':'other'},
                               {'key':'SEH_SLH_PUBLIC_KEY_B64','value':public_b64()}]
    elif fault=='wrong-app':
        host.apps[0]['description']='not-owned'
    elif fault=='bad-key':
        key='not-a-valid-public-key'
    with pytest.raises(git.SEHGitError):
        git.bind_slh(root,'mainnet','company',slh_id='slh',
                     public_key_b64=key,replace_existing=True,execute=True,client=host)
    assert not any(m != 'GET' for m,p in host.calls)


def test_operator_bind_routes_existing_key_without_sql_or_deploy_prepare(monkeypatch):
    class Control:
        def __init__(self): self.calls=[]
        def list(self, network):
            return {'sehs':[{'seh':'company-primary','slh_id':'my-slh','active_identity':'primary',
                             'identities':{'primary':{'copied_from':'networks.mainnet.coolify.controllers.coolify-c'}},
                             'deployment':{'application_uuid':'owned-app','public_url':'https://seh.example.org'}}]}
        def bind_slh(self,network,seh,**kwargs):
            self.calls.append(kwargs)
            return {'status':'TRUST_DEPLOY_QUEUED_NOT_VERIFIED' if kwargs['execute'] else 'PLAN_ONLY',
                    'slh_id':kwargs['slh_id'],
                    'ready':False,'public_wss_verified':False}
    control=Control()
    a=_parser().parse_args(['bind-slh','--network','mainnet','--seh','company-primary',
                            '--host','coolify-c','--replace-existing-trust',
                            '--execute-mutations','--yes-i-know-this-mutates-seh'])
    result=run(a,control=control,slh_public_key_provider=lambda **kw:'public-key-only')
    assert result['status']=='TRUST_DEPLOY_QUEUED_NOT_VERIFIED'
    assert control.calls[0]['public_key_b64']=='public-key-only'
    assert control.calls[0]['replace_existing'] is True
    a=_parser().parse_args(['bind-slh','--network','mainnet','--seh','company-primary'])
    assert run(a,control=control,slh_public_key_provider=lambda **kw:'public-key-only')['status']=='PLAN_ONLY'
    assert control.calls[-1]['execute'] is False


def test_trust_readback_mismatch_blocks_redeploy(tmp_path,monkeypatch):
    root,host,_=configured(tmp_path,monkeypatch)
    seen=[]
    monkeypatch.setattr(git.hub_coolify,'trigger_deploy',lambda *a,**k:seen.append(1))
    raw_request=host.request
    def masked(method,path,body=None):
        result=raw_request(method,path,body)
        if method=='GET' and path.endswith('/envs') and host.envs:
            return [{**r,'value':'********'} if r['key']=='SEH_SLH_PUBLIC_KEY_B64' else r for r in result]
        return result
    host.request=masked
    with pytest.raises(git.SEHGitError,match='readback'):
        git.bind_slh(root,'mainnet','company',slh_id='slh',
                     public_key_b64=public_b64(),execute=True,client=host)
    assert not seen


def test_operator_refuses_to_present_standalone_connector_as_next_step(capsys,monkeypatch):
    from seh_mutate_harness import main
    class Control:
        def list(self,network):
            return {'sehs':[{'seh':'company-primary','slh_id':'my-slh','active_identity':'primary',
                            'identities':{'primary':{'copied_from':'networks.mainnet.coolify.controllers.coolify-c'}},
                            'deployment':{'application_uuid':'owned-app','public_url':'https://external.greatlibrary.io'}}]}
        def bind_slh(self,network,seh,**opts):
            return {'status':'TRUST_DEPLOY_QUEUED_NOT_VERIFIED', 'slh_id':'my-slh','public_url':'https://external.greatlibrary.io',
                    'ready':False,'public_wss_verified':False}
    import seh_mutate_harness as mod
    monkeypatch.setattr(mod,'SEHControl',lambda *_:Control())
    monkeypatch.setattr('tools.slh_seh_connector.service_identity',lambda root,create=False:(None,'public-key-only'))
    assert main(['bind-slh','--network','mainnet','--seh','company-primary','--host','coolify-c',
                 '--replace-existing-trust','--execute-mutations','--yes-i-know-this-mutates-seh'])==3
    got=capsys.readouterr()
    assert json.loads(got.out)['status']=='TRUST_DEPLOY_QUEUED_NOT_VERIFIED'
    assert 'tools.slh_seh_connector run' not in got.err
    assert 'operational acceptance remains incomplete' in got.err
    assert 'public-key-only' not in got.out + got.err
