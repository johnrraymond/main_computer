"""Canonical deployment through the existing SEH harness, after verified undeploy.

Never calls a live Coolify service. The source and exact UUID are mocked at
operator boundaries; ownership, private state and receipt checks are real.
"""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from seh_mutate_harness import _parser, run, HarnessError
from tools.seh_control import git_deployment as g, deployment_state as ds, identity as st
from main_computer.slh.organization_state import read_config,save_config
from tests.test_seh_deleted_application_recovery import setup, PUBLISHED, APP, NEW


def removed(tmp_path):
    root, slh, remote = setup(tmp_path)
    cfg=read_config(slh)
    save_config(slh,{**cfg,'seh_enabled':False})
    record,_,_,_=g._canonical_selection(root,'mainnet')
    _,_,document=st.read(root,'mainnet')
    canonical=st.deployment_record(document,'mainnet')
    receipt={'schema':'main-computer.seh-undeploy.v1',
             'application_uuid':APP,'identity_binding':ds.identity_binding(record),
             'phase':'VERIFIED','requested_utc':'2026-11-12T00:00:00+00:00'}
    g._save_undeploy_receipt(root,'mainnet',receipt)
    new=deepcopy(record['deployment']);new.update(undeployed=True,undeployed_at=receipt['requested_utc'])
    ds.write(root,'mainnet',canonical,expected_uuid=APP,deployment=new)
    return root,slh,remote


def opts(root,slh,*flags):
    return _parser().parse_args(['deploy-seh','--network','mainnet',
           '--runtime-state-root',str(root),'--slh-state-dir',str(slh),*flags])


def test_verified_undeploy_plan_and_explicit_deploy_use_exact_existing_harness(tmp_path,monkeypatch):
    root,slh,remote=removed(tmp_path)
    monkeypatch.setattr(g,'_client',lambda controller:remote)
    monkeypatch.setattr(g,'git_source',lambda *args,**kw:PUBLISHED)
    monkeypatch.setattr(g.hub_coolify,'trigger_deploy',lambda *args,**kw:{'ok':True,
                     'body':{'deployments':[{'resource_uuid':NEW}]}})
    before=st.read(root,'mainnet')[1].binding.generation
    plan=run(opts(root,slh))
    assert plan['status']=='PLAN_ONLY'
    assert plan['former_application_uuid']==APP
    assert plan['action']=='RECREATE_DELETED_APPLICATION'
    assert remote.created is None
    with pytest.raises(HarnessError,match='Both mutation approvals'):
        run(opts(root,slh,'--execute-mutations'))
    with pytest.raises(HarnessError,match='no target or signing overrides'):
        run(opts(root,slh,'--host','coolify-c'))
    assert remote.created is None
    deployed=run(opts(root,slh,'--execute-mutations','--yes-i-know-this-mutates-seh'))
    assert deployed['status']=='REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert deployed['application_uuid']==NEW
    assert deployed['outbound_tunnel_authenticated'] is False
    assert st.read(root,'mainnet')[1].binding.generation==before
    operational=g._canonical_selection(root,'mainnet')[0]['deployment']
    assert operational['application_uuid']==NEW
    assert operational.get('undeployed') is not True
    assert g._undeploy_receipt(root,'mainnet',APP)['phase']=='VERIFIED'
    assert g._undeploy_receipt(root,'mainnet',NEW) is None


def test_unverified_or_absent_prior_undeploy_cannot_create(tmp_path,monkeypatch):
    root,slh,remote=setup(tmp_path)
    monkeypatch.setattr(g,'_client',lambda controller:remote)
    monkeypatch.setattr(g,'git_source',lambda *args,**kw:PUBLISHED)
    with pytest.raises(HarnessError,match='verified intentional undeploy'):
        run(opts(root,slh))
    assert remote.created is None
    root,slh,remote=removed(tmp_path/'new')
    cfg=read_config(slh)
    save_config(slh,{**cfg,'seh_enabled':True})
    with pytest.raises(HarnessError,match='Disconnect outbound SEH'):
        run(opts(root,slh))
    assert remote.created is None


def test_management_inspect_reports_operational_application_not_historical_uuid(tmp_path):
    from main_computer.slh.seh_deployment_management import SLHSEHDeploymentManager
    root, slh, remote = removed(tmp_path)
    manager = SLHSEHDeploymentManager(root=root, state_dir=slh)
    old = manager.inspect({})
    assert old['deployment_phase'] == 'UNDEPLOYED'
    assert old['deployment']['deployment']['application_uuid'] == APP
    _, _, document = st.read(root, 'mainnet')
    identity = st.deployment_record(document, 'mainnet')
    update = deepcopy(g._canonical_selection(root, 'mainnet')[0]['deployment'])
    update['application_uuid'] = NEW
    update.pop('undeployed', None)
    update.pop('undeployed_at', None)
    ds.write(root, 'mainnet', identity, expected_uuid=APP, deployment=update)
    current = manager.inspect({})
    assert current['deployment_phase'] == 'APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED'
    assert current['deployment']['deployment']['application_uuid'] == NEW
    assert st.deployment_record(st.read(root, 'mainnet')[2], 'mainnet')['deployment']['application_uuid'] == APP
