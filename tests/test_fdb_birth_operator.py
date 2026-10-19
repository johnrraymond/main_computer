from __future__ import annotations
import argparse
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('fdb_birth_operator', ROOT / 'fdb_mutate_harness.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
from tools.fdb_control import __main__ as cli


def args(tmp_path, *, execute=False):
    return argparse.Namespace(operation='create-cluster', network='testnet', service='testnetb-fdb1',
        repo_root=tmp_path, allow_full_deletion=False, execute_mutations=execute,
        yes_i_know_this_mutates_fdb=execute)


def birth_target(h):
    return {'request': {
        'network':'testnet', 'cluster':{'description':'main_computer_testnet','cluster_id':h.state['cluster_id']},
        'services':[{'service_id':'testnetb-fdb1','host_id':'coolify-b','address':'10.124.0.3','port':4550}],
        'coordinator_service_ids':['testnetb-fdb1'],'redundancy_mode':'single', 'storage_engine':'ssd'},
        'deployment':{'project_uuid':'project-id','server_uuid':'server-id'}}


def accept(h, step, result):
    h._validate_step(step,result)
    h.state['last_completed_step']=step
    h._write_state()


def test_birth_parser_and_intent_only(tmp_path):
    assert 'create-cluster' in MODULE._parser().format_help()
    h=MODULE.Harness(args(tmp_path))
    cmd=h.prep_cmd()
    assert cmd[cmd.index('create-cluster'):cmd.index('--cluster-id')] == ['create-cluster','prep','testnet','--service','testnetb-fdb1','--infer-placement']
    assert '--infer-placement' in cmd
    assert cmd[-1]==h.state['cluster_id']
    assert '--host' not in cmd and '--address' not in cmd and '--project-uuid' not in cmd
    assert len(h.state['cluster_id'])==16


def test_birth_is_resumable_with_same_cluster_id(tmp_path):
    h=MODULE.Harness(args(tmp_path))
    accept(h,'pre-inspect',{'status':'unborn','accepted':None})
    old_id=h.state['cluster_id']
    resumed=MODULE.Harness(args(tmp_path,execute=True))
    assert resumed._resumed_existing_run
    assert resumed.state['cluster_id']==old_id
    assert resumed.run_dir==h.run_dir


def test_only_unborn_network_can_begin_birth(tmp_path):
    for status,accepted in [('accepted', {'generation':1}),('accepted-empty',{}),('unborn',{'generation':0})]:
        h=MODULE.Harness(args(tmp_path / status))
        with pytest.raises(MODULE.HarnessError):
            accept(h,'pre-inspect',{'status':status,'accepted':accepted})


def test_birth_accepts_verified_transaction(tmp_path):
    h=MODULE.Harness(args(tmp_path))
    accept(h,'pre-inspect',{'status':'unborn','accepted':None})
    accept(h,'prep',{'status':'prepared','details':{'operation_id':'fdb-create-testnet-deadbeef', 'target':birth_target(h)}})
    assert h.state['target_coordinators']==['10.124.0.3:4550']
    assert h.state['target_generation']==1
    accept(h,'do',{'status':'deployed'})
    accept(h,'operation-inspect',{'birth_verification':{'verified':True,'reason':'fdb-observer-proof-satisfied','service_id':'testnetb-fdb1'}})
    accept(h,'finalize',{'status':'finalized','details':{'verified':True,'accepted_state_path':'runtime/state/fdb/testnet/accepted.json'}})
    accept(h,'final-inspect',{'status':'accepted','accepted_generation':1,
      'cluster':birth_target(h)['request']['cluster'],
      'coordinators':['10.124.0.3:4550'],
      'services':[{'service_id':'testnetb-fdb1','host_id':'coolify-b','endpoint':'10.124.0.3:4550'}],
      'birth_verification':{'verified':True}})
    assert h.state['final_coordinators']==['10.124.0.3:4550']


def test_birth_rejects_wrong_service_identity_and_unproved_birth(tmp_path):
    h=MODULE.Harness(args(tmp_path))
    accept(h,'pre-inspect',{'status':'unborn','accepted':None})
    target=birth_target(h); target['request']['services'][0]['service_id']='testnetc-fdb1'
    with pytest.raises(MODULE.HarnessError,match='different service'):
        accept(h,'prep',{'status':'prepared','details':{'operation_id':'fake','target':target}})
    target=birth_target(h)
    accept(h,'prep',{'status':'prepared','details':{'operation_id':'good','target':target}})
    with pytest.raises(MODULE.HarnessError,match='did not verify'):
        accept(h,'operation-inspect',{'birth_verification':{'verified':False}})
    with pytest.raises(MODULE.HarnessError,match='FDB observer'):
        accept(h,'operation-inspect',{'birth_verification':{'verified':True,'service_id':'testnetb-fdb1','reason':'wrong'}})


def test_inferred_birth_coordinates_are_read_from_private_state(monkeypatch,tmp_path):
    monkeypatch.setattr(cli,'load_private_infrastructure',lambda ctx:{'mock':True})
    monkeypatch.setattr(cli,'resolve_host_for_placement',lambda doc,net,placement:SimpleNamespace(host_id='coolify-b'))
    monkeypatch.setattr(cli,'resolve_host_fdb_address',lambda doc,net,host:('10.124.0.3','private'))
    monkeypatch.setattr(cli,'resolve_controller_coordinates',lambda doc,net,host:{'project_uuid':'project','server_uuid':'server'})
    captured=[]
    def fake_prep(ctx, req, dep):
        captured.append((req,dep))
        return SimpleNamespace(operation='create-cluster',stage='prep',status='prepared',details={'operation_id':'one'})
    monkeypatch.setattr(cli,'create_prep',fake_prep)
    parser=cli._parser()
    ns=parser.parse_args(['create-cluster','prep','testnet','--service','testnetb-fdb1',
                         '--infer-placement','--cluster-id','a1b2c3d4e5f60708'])
    response=cli._dispatch(SimpleNamespace(),ns)
    assert response['status']=='prepared'
    req,dep=captured[0]
    assert req.cluster.description=='main_computer_testnet'
    assert req.services[0].host_id=='coolify-b'
    assert req.services[0].endpoint=='10.124.0.3:4550'
    assert dep.project_uuid=='project' and dep.server_uuid=='server'
    assert req.coordinator_service_ids==('testnetb-fdb1',)


def test_inferred_birth_fails_closed_on_manual_overrides_or_nonfirst_service():
    for changes in [('--host','arbitrary'),('--project-uuid','unverified')]:
        args=cli._parser().parse_args(['create-cluster','prep','testnet','--service','testnetb-fdb1',
              '--infer-placement','--cluster-id','abcdefgh',*changes])
        with pytest.raises(ValueError, match='does not accept'):
            cli._dispatch(SimpleNamespace(),args)
    args=cli._parser().parse_args(['create-cluster','prep','testnet','--service','testnetb-fdb2',
          '--infer-placement','--cluster-id','abcdefgh'])
    with pytest.raises(ValueError,match='first service'):
        cli._dispatch(SimpleNamespace(),args)


def test_existing_mainnet_placement_naming_still_infers_controller_a(monkeypatch):
    monkeypatch.setattr(cli,'load_private_infrastructure',lambda ctx:{'mock':True})
    monkeypatch.setattr(cli,'resolve_host_for_placement',lambda doc,net,placement:SimpleNamespace(host_id='coolify-a' if placement=='a' else 'bad'))
    monkeypatch.setattr(cli,'resolve_host_fdb_address',lambda doc,net,host:('10.116.0.3','private'))
    monkeypatch.setattr(cli,'resolve_controller_coordinates',lambda doc,net,host:{'project_uuid':'mainnet-project','server_uuid':'mainnet-server'})
    captured=[]
    monkeypatch.setattr(cli,'create_prep',lambda ctx,req,dep:(captured.append((req,dep)) or SimpleNamespace(operation='create-cluster',stage='prep',status='prepared',details={'operation_id':'mainnet'})))
    ns=cli._parser().parse_args(['create-cluster','prep','mainnet','--service','mainneta-fdb1',
          '--infer-placement','--cluster-id','ac826580a04d022d'])
    assert cli._dispatch(SimpleNamespace(),ns)['status']=='prepared'
    req,dep=captured[0]
    assert req.cluster.description=='main_computer_mainnet'
    assert req.services[0].host_id=='coolify-a'
    assert req.services[0].endpoint=='10.116.0.3:4550'
    assert dep.project_uuid=='mainnet-project'


def test_create_cluster_preflight_stops_before_do_without_mutation_flags(tmp_path, monkeypatch):
    h=MODULE.Harness(args(tmp_path))
    stages=[]
    def fake_run_step(step, cmd):
        stages.append(step)
        if step=='pre-inspect': return {'status':'unborn','accepted':None}
        if step=='prep': return {'status':'prepared','details':{'operation_id':'fdb-create-testnet-safe', 'target':birth_target(h)}}
        raise AssertionError('attempted live operation without authorization')
    monkeypatch.setattr(h,'_run_step',fake_run_step)
    assert h.run()==0
    assert stages==['pre-inspect','prep']
    assert h.state['last_completed_step']=='prep'
    resumed=MODULE.Harness(args(tmp_path,execute=True))
    assert resumed.run_dir==h.run_dir
    assert resumed.state['operation_id']=='fdb-create-testnet-safe'
