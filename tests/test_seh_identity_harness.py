"""First-class SEH identity, independent Coolify copies, verified CAS and harness integration."""
from __future__ import annotations

import base64
from copy import deepcopy
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import pytest

from main_computer.slh.authenticator import generate_identity
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    prepare_private_state_bootstrap, install_verified_private_state, read_private_state,
)
from tools.seh_control import identity as st
from tools.seh_control.commands import SEHControl
from tools.seh_harness import _FakeCoolify, _deployment_config_path, main

SECRET = '1|SECRETSEHTESTTOKENVALUEABCDEFGHIJKLMNOPQRSTUVWXYZ'


def fixture(tmp_path):
    root = tmp_path / 'runtime' / 'state'
    doc = {'kind':'main_computer.mother.private_state.v1', 'schema_version':1,
           'networks':{'mainnet':{'coolify':{'mutation_authority':'observe-only', 'controllers':{
               'coolify-a':{'url':'https://coolify.example.org','api_token':SECRET,
                            'project_uuid':'proj-a','server_uuid':'server-a','enabled':True},
               'coolify-b':{'url':'https://coolify-b.example.org','api_token':'another-token',
                            'project_uuid':'proj-b','server_uuid':'server-b','enabled':True}}},
                'hubs':{'important-hub':{'unchanged':True}},'foundationdb':{'keep':123},
                'nodes':{'keep':{'validator':'yes'}}}}}
    paths = MotherPaths(runtime_state_root=root).resolve_private_state_paths()
    op = OperationIdentity('seh-fixture', 'seh-fixture', 'mainnet', 'MOTHER-OP-IDENTITY-ROTATION')
    closure=prepare_private_state_bootstrap(paths,doc,updated_at='2026-10-28T00:00:00Z',updated_by_action_id='fixture',operation=op)
    install_verified_private_state(paths,closure,None,operation=op)
    return root,doc


def ctrl(root): return SEHControl(root)


def do(c, cmd, gen, seh='company', **opts):
    return c.change(cmd,'mainnet',seh,gen,f'seh-test-{cmd}-{gen}',**opts)


def publickey():
    _, key = generate_identity()
    return base64.b64encode(key.encode('ascii')).decode('ascii')


def configure_complete(c, gen, seh='company'):
    return do(c,'configure',gen,seh,changes={
        'public_url':'https://seh.example.org', 'image':'registry.example.org/main-computer/seh',
        'image_digest':'sha256:'+'1'*64,'environment_uuid':'env-a',
        'slh_public_key_b64':publickey(),'enabled':True})


def build(tmp_path):
    root,original=fixture(tmp_path)
    c=ctrl(root)
    assert do(c,'reserve',1,organization_id='org',slh_id='slh')['generation']==2
    assert do(c,'coolify-copy',2,alias='primary',source='networks.mainnet.coolify.controllers.coolify-a')['generation']==3
    return root,original,c


def test_cas_and_unrelated_state_preserved(tmp_path):
    root,old,c=build(tmp_path)
    with pytest.raises(st.SEHIdentityError,match='generation conflict'):
        do(c,'reserve',2,'other',organization_id='org',slh_id='slh')
    _,res,doc=st.read(root,'mainnet')
    assert res.binding.generation==3
    assert doc['networks']['mainnet']['hubs']==old['networks']['mainnet']['hubs']
    assert doc['networks']['mainnet']['foundationdb']==old['networks']['mainnet']['foundationdb']
    assert doc['networks']['mainnet']['nodes']==old['networks']['mainnet']['nodes']
    assert doc['networks']['mainnet']['coolify']==old['networks']['mainnet']['coolify']
    assert doc['networks']['mainnet']['sehs']['company']['coolify']['identities']['primary']['controller']['api_token']==SECRET
    assert SECRET not in json.dumps(c.inspect('mainnet','company'))
    assert 'token' not in repr(c.resolved)  # only public method repr


def test_copy_independence_refresh_source_and_dest(tmp_path):
    root,_,c=build(tmp_path)
    assert do(c,'reserve',3,'other',organization_id='org2',slh_id='slh2')['generation']==4
    path='networks.mainnet.sehs.company.coolify.identities.primary'
    assert do(c,'coolify-copy',4,'other',source=path,alias='copy')['generation']==5
    _,_,doc=st.read(root,'mainnet')
    assert doc['networks']['mainnet']['sehs']['other']['coolify']['identities']['copy']['controller']['api_token']==SECRET
    # Rotate the original source (under Mother's CAS); both SEH copies are independent.
    def rotate(d,g,b):
        d['networks']['mainnet']['coolify']['controllers']['coolify-a']['api_token']='rotated-token'
    assert st.mutate(root,'mainnet',5,'seh-rotation',rotate)['generation']==6
    assert do(c,'coolify-refresh',6,alias='primary')['generation']==7
    _,_,doc=st.read(root,'mainnet')
    assert doc['networks']['mainnet']['sehs']['company']['coolify']['identities']['primary']['controller']['api_token']=='rotated-token'
    assert doc['networks']['mainnet']['sehs']['other']['coolify']['identities']['copy']['controller']['api_token']==SECRET
    with pytest.raises(st.SEHIdentityError,match='exists'):
        do(c,'coolify-copy',7,alias='primary',source='networks.mainnet.coolify.controllers.coolify-b')
    assert c.inspect('mainnet','company')['identities']['primary']['source_generation']==6


def test_copy_disabled_and_missing_credential_refused_without_committing(tmp_path):
    root,_,c=build(tmp_path)
    def change_source(d,g,b):
        d['networks']['mainnet']['coolify']['controllers']['coolify-b']['enabled']=False
    st.mutate(root,'mainnet',3,'seh-disable-source',change_source)
    with pytest.raises(st.SEHIdentityError,match='Disabled'):
        do(c,'coolify-copy',4,alias='second',source='networks.mainnet.coolify.controllers.coolify-b')
    assert c.inspect('mainnet','company')['generation']==4


def test_copy_environment_token_is_materialized(tmp_path,monkeypatch):
    root,_,c=build(tmp_path)
    monkeypatch.setenv('SEH_COPY_TEST_TOKEN','1|ENVRESOLVEDSECRETVALUE')
    def alter(d,g,b):
        ctl=d['networks']['mainnet']['coolify']['controllers']['coolify-b']
        ctl.pop('api_token')
        ctl['api_token_env']='SEH_COPY_TEST_TOKEN'
    st.mutate(root,'mainnet',3,'seh-env-token',alter)
    do(c,'coolify-copy',4,alias='env-copy',source='networks.mainnet.coolify.controllers.coolify-b')
    _,_,doc=st.read(root,'mainnet')
    copied=doc['networks']['mainnet']['sehs']['company']['coolify']['identities']['env-copy']['controller']
    assert copied['api_token']=='1|ENVRESOLVEDSECRETVALUE'
    assert 'api_token_env' not in copied
    monkeypatch.delenv('SEH_COPY_TEST_TOKEN')
    assert SECRET not in json.dumps(c.list('mainnet'))
    assert 'ENVRESOLVEDSECRETVALUE' not in json.dumps(c.list('mainnet'))


def test_plan_and_preflight_with_embedded_credential(tmp_path):
    root,_,c=build(tmp_path)
    configure_complete(c,3)
    resolved=c.resolved('mainnet','company')
    assert resolved.token==SECRET
    assert resolved.target.project_uuid=='proj-a'
    assert resolved.target.coolify_url=='https://coolify.example.org'
    assert SECRET not in repr(resolved)
    planned=c.plan('mainnet','company')
    assert planned['status']=='PLANNED_ONLY'
    assert planned['private_state_generation']==4
    assert len(planned['plan_digest'])==64
    fake=_FakeCoolify()
    # fake preflight uses project_uuid value, so no network needed
    result=c.preflight('mainnet','company',transport=fake)
    assert result['status']=='CREATE_ELIGIBLE'
    assert not any(method=='POST' for method,_ in fake.calls)
    assert SECRET not in json.dumps(planned)


def test_guarded_remote_deploy_commits_receipt_and_start(tmp_path):
    root,_,c=build(tmp_path)
    configure_complete(c,3)
    fake=_FakeCoolify()
    planned=c.plan('mainnet','company')
    with pytest.raises(st.SEHIdentityError, match='generation'):
        c.deploy('mainnet','company',generation=3,digest=planned['plan_digest'],target_id='company',operation_id='bad',transport=fake)
    assert not any(method=='POST' for method,_ in fake.calls)
    receipt=c.deploy('mainnet','company',generation=4,digest=planned['plan_digest'],target_id='company',operation_id='deploy-success',transport=fake)
    assert receipt['status']=='CREATED_NOT_STARTED'
    assert receipt['identity_committed'] is True
    assert receipt['private_state_generation']==5
    assert c.resolved('mainnet','company').target.service_uuid=='mock-seh-uuid'
    assert c.preflight('mainnet','company',transport=fake)['status']=='EXISTING_SEH_READ_ONLY'
    planned2=c.plan('mainnet','company')
    started=c.start('mainnet','company',generation=5,digest=planned2['plan_digest'],uuid='mock-seh-uuid',transport=fake)
    assert started['status']=='START_QUEUED' and started['ready'] is False


def test_ambiguous_remote_uuid_recovery_validates_exact_marker(tmp_path):
    root,_,c=build(tmp_path)
    configure_complete(c,3)
    fake=_FakeCoolify()
    fake.services.append({'uuid':'trusted-seh','name':'mc-seh-company',
                          'description':'main-computer-seh:v1 target=company org=org slh=slh'})
    result=c.recover('mainnet','company',generation=4,uuid='trusted-seh',operation_id='recover-ok',transport=fake)
    assert result['generation']==5
    assert c.resolved('mainnet','company').target.service_uuid=='trusted-seh'
    with pytest.raises(st.SEHIdentityError):
        c.recover('mainnet','company',generation=5,uuid='wrong',operation_id='recover-wrong',transport=fake)


def test_harness_cli_read_only_and_state_mutations(tmp_path):
    root,_=fixture(tmp_path)
    stdout=io.StringIO();stderr=io.StringIO()
    args=['reserve','--runtime-state-root',str(root),'--network','mainnet','--seh','company',
          '--organization-id','org','--slh-id','slh']
    with redirect_stdout(stdout),redirect_stderr(stderr):
        assert main(args)==2 # no approval
    assert SECRET not in (stdout.getvalue()+stderr.getvalue())
    with redirect_stdout(io.StringIO()) as out:
        assert main(args+['--execute'])==0
    with redirect_stdout(io.StringIO()) as out:
        assert main(['coolify-sources','--network','mainnet','--runtime-state-root',str(root)])==0
    assert 'networks.mainnet.coolify.controllers.coolify-a' in out.getvalue()
    assert SECRET not in out.getvalue()
    with redirect_stdout(io.StringIO()) as out:
        assert main(['coolify-copy','--network','mainnet','--seh','company','--source',
                     'networks.mainnet.coolify.controllers.coolify-a','--as','primary',
                     '--execute','--runtime-state-root',str(root)])==0
    assert SECRET not in out.getvalue()
    with redirect_stdout(io.StringIO()) as out:
        assert main(['inspect','--network','mainnet','--seh','company','--runtime-state-root',str(root)])==0
    assert 'primary' in out.getvalue() and SECRET not in out.getvalue()


def test_automatic_generation_for_state_mutations_and_optional_pin(tmp_path):
    root, _ = fixture(tmp_path)
    c = ctrl(root)
    # Service and CLI callers do not need to discover or supply a generation.
    result = c.change('reserve', 'mainnet', 'company', None, 'auto-register',
                      organization_id='org', slh_id='slh')
    assert result['status'] == 'COMMITTED' and result['generation'] == 2
    assert c.change('coolify-copy', 'mainnet', 'company', None, 'auto-copy',
                    alias='primary', source='networks.mainnet.coolify.controllers.coolify-a')['generation'] == 3
    assert c.change('configure', 'mainnet', 'company', None, 'auto-configure', changes={
        'public_url':'https://seh.example.org','image':'registry.example.org/main-computer/seh',
        'image_digest':'sha256:'+'1'*64, 'environment_uuid':'env-a',
        'slh_public_key_b64':publickey(), 'enabled':True})['generation'] == 4
    fake=_FakeCoolify()
    plan = c.plan('mainnet','company')
    receipt=c.deploy('mainnet','company',digest=plan['plan_digest'],target_id='company',
                     operation_id='auto-deploy',transport=fake)
    assert receipt['private_state_generation']==5
    plan = c.plan('mainnet','company')
    assert c.start('mainnet','company',digest=plan['plan_digest'],uuid='mock-seh-uuid',
                   transport=fake)['status']=='START_QUEUED'
    # A deliberately pinned old generation still fails before a remote POST.
    fake2=_FakeCoolify()
    with pytest.raises(st.SEHIdentityError, match='generation'):
        c.start('mainnet','company',generation=3,digest=plan['plan_digest'],
                uuid='mock-seh-uuid',transport=fake2)
    assert not any(method=='POST' for method,_ in fake2.calls)


def test_automatic_generation_rejects_concurrent_write_at_commit(tmp_path, monkeypatch):
    root, _ = fixture(tmp_path)
    c=ctrl(root)
    original_replace=st.replace_verified_private_state

    def concurrent_replace(paths, closure, expected_binding, *, operation):
        # Another writer commits a valid Mother successor after this operation's
        # read, but before its compare-and-swap. The old successor must NOT win.
        monkeypatch.setattr(st, 'replace_verified_private_state', original_replace)
        st.mutate(root, 'mainnet', None, 'other-writer',
                  lambda doc, gen, base: st.reserve(doc, 'mainnet', 'other', 'org', 'slh'))
        return original_replace(paths, closure, expected_binding, operation=operation)

    monkeypatch.setattr(st, 'replace_verified_private_state', concurrent_replace)
    with pytest.raises(st.SEHIdentityError, match='changed concurrently'):
        c.change('reserve', 'mainnet', 'company', None, 'stale-writer',
                 organization_id='org', slh_id='slh')
    _, current, doc=st.read(root,'mainnet')
    assert current.binding.generation==2
    assert 'other' in doc['networks']['mainnet']['sehs']
    assert 'company' not in doc['networks']['mainnet']['sehs']


def test_cli_explicit_generation_remains_optional_strict_pin(tmp_path):
    root, _=fixture(tmp_path)
    args=['reserve','--network','mainnet','--seh','company',
          '--organization-id','org','--slh-id','slh',
          '--runtime-state-root',str(root),'--execute']
    with redirect_stdout(io.StringIO()):
        assert main(args)==0
    # Supplying a wrong explicit pin still refuses to advance state.
    args2=['reserve','--network','mainnet','--seh','other',
           '--organization-id','org','--slh-id','slh',
           '--runtime-state-root',str(root),'--execute',
           '--approve-generation','1']
    with redirect_stderr(io.StringIO()) as err:
        assert main(args2)==2
    assert 'generation conflict' in err.getvalue()
    _, state, doc=st.read(root,'mainnet')
    assert state.binding.generation == 2
    assert 'other' not in doc['networks']['mainnet']['sehs']


def test_configure_uses_per_seh_runtime_deployment_path_by_default(tmp_path):
    root, _, c = build(tmp_path)
    path = _deployment_config_path(root, 'mainnet', 'company')
    assert path == tmp_path / 'runtime/deployments/mainnet/sehs/company/deployment.json'
    assert path.parent != root / 'mother'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'public_url': 'https://seh.example.org',
                                'enabled': False}), encoding='utf-8')
    args = ['configure', '--network', 'mainnet', '--seh', 'company', '--execute',
            '--runtime-state-root', str(root)]
    with redirect_stdout(io.StringIO()) as out:
        assert main(args) == 0
    assert json.loads(out.getvalue())['status'] == 'COMMITTED'
    assert c.inspect('mainnet', 'company')['deployment']['public_url'] == 'https://seh.example.org'
    assert path.exists()  # The input file is not deleted or rewritten on commit.


def test_configure_missing_creates_nonsecret_runtime_draft_and_preserves_state(tmp_path, monkeypatch):
    root, _, c = build(tmp_path)
    from tools import slh_seh_connector
    # Reproduce a real local SLH with an existing signing identity. Drafting
    # must read only its public identity and must not create a private key.
    key_dir = tmp_path / 'local-slh'
    _, public = slh_seh_connector.service_identity(key_dir, create=True)
    monkeypatch.setattr(slh_seh_connector, 'default_state', lambda: key_dir)
    args = ['configure', '--network', 'mainnet', '--seh', 'company', '--execute',
            '--runtime-state-root', str(root)]
    with redirect_stdout(io.StringIO()) as output:
        assert main(args) == 0
    created = json.loads(output.getvalue())
    path = _deployment_config_path(root, 'mainnet', 'company')
    assert created['status'] == 'DRAFT_CREATED'
    assert created['path'] == str(path)
    assert created['identity_committed'] is False
    assert created['remote_mutation'] is False
    assert set(created['missing_fields']) == {'public_url', 'image', 'image_digest', 'environment_uuid'}
    draft = json.loads(path.read_text(encoding='utf-8'))
    assert draft['project_uuid'] == 'proj-a' and draft['server_uuid'] == 'server-a'
    assert draft['slh_public_key_b64'] == public
    assert draft['enabled'] is True
    assert SECRET not in json.dumps(draft)
    assert 'api_token' not in json.dumps(draft)
    assert 'coolify_url' not in json.dumps(draft)
    assert c.inspect('mainnet', 'company')['generation'] == 3
    with redirect_stdout(io.StringIO()) as second:
        assert main(args) == 2
    assert json.loads(second.getvalue())['status'] == 'NEEDS_CONFIGURATION'
    assert path.read_text(encoding='utf-8') == json.dumps(draft, indent=2, sort_keys=True) + '\n'
    assert c.inspect('mainnet', 'company')['generation'] == 3


def test_configure_draft_completed_then_commits(tmp_path, monkeypatch):
    root, _, c = build(tmp_path)
    from tools import slh_seh_connector
    monkeypatch.setattr(slh_seh_connector, 'default_state', lambda: tmp_path / 'absent-key')
    args = ['configure', '--network', 'mainnet', '--seh', 'company', '--execute',
            '--runtime-state-root', str(root)]
    with redirect_stdout(io.StringIO()) as out:
        assert main(args) == 0
    draft = json.loads(out.getvalue())
    assert 'slh_public_key_b64' in draft['missing_fields']
    path = Path(draft['path'])
    config = json.loads(path.read_text())
    config.update({'public_url': 'https://seh.example.org',
                   'image': 'registry.example.org/main-computer/seh',
                   'image_digest': 'sha256:' + '1' * 64,
                   'environment_uuid': 'env-a',
                   'slh_public_key_b64': publickey()})
    path.write_text(json.dumps(config), encoding='utf-8')
    with redirect_stdout(io.StringIO()) as out:
        assert main(args) == 0
    assert json.loads(out.getvalue())['status'] == 'COMMITTED'
    assert c.inspect('mainnet', 'company')['generation'] == 4
    assert c.resolved('mainnet', 'company').target.environment_uuid == 'env-a'


def test_configure_missing_explicit_override_refuses_without_draft(tmp_path):
    root, _, c = build(tmp_path)
    missing = tmp_path / 'missing-overridden.json'
    args = ['configure', '--network', 'mainnet', '--seh', 'company', '--execute',
            '--runtime-state-root', str(root), '--deployment-file', str(missing)]
    with redirect_stderr(io.StringIO()) as err:
        assert main(args) == 2
    assert 'Explicit deployment configuration not found' in err.getvalue()
    assert not missing.exists()
    assert not _deployment_config_path(root, 'mainnet', 'company').exists()
    assert c.inspect('mainnet', 'company')['generation'] == 3


def test_configure_explicit_deployment_file_override_remains_supported(tmp_path):
    root, _, c = build(tmp_path)
    custom = tmp_path / 'legacy_deployment.json'
    custom.write_text(json.dumps({'enabled': False, 'internal_port': 9099}), encoding='utf-8')
    args = ['configure', '--network', 'mainnet', '--seh', 'company', '--execute',
            '--runtime-state-root', str(root), '--deployment-file', str(custom)]
    with redirect_stdout(io.StringIO()) as out:
        assert main(args) == 0
    assert json.loads(out.getvalue())['status'] == 'COMMITTED'
    assert c.inspect('mainnet', 'company')['deployment']['internal_port'] == 9099


def test_default_path_rejects_untrusted_seh_name(tmp_path):
    with pytest.raises(st.SEHIdentityError, match='Invalid seh'):
        _deployment_config_path(tmp_path / 'runtime/state', 'mainnet', '../../escape')
