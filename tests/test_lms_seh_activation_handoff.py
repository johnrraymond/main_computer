"""LMS delegates SEH lifecycle to one harness invocation and preserves receipts.

No live Coolify, resident SLH or Mother mutations are performed by these tests.
"""
from __future__ import annotations

import time
from copy import deepcopy

from main_computer.slh.seh_deployment_management import SLHSEHDeploymentManager
from main_computer.mcel_slh_seh_deployment import execute_seh_deployment
from tests.test_seh_canonical_deploy_ui_harness import removed
from tests.test_seh_deleted_application_recovery import setup, APP, NEW
from tests.test_slh_seh_coolify_management_backend import root_state
from tools.seh_control import identity as st, deployment_state as ds


def done(manager, operation_id):
    until=time.monotonic()+8
    while time.monotonic()<until:
        row=manager.operation_status(operation_id)
        if row['phase']=='RECORDED':return row
        time.sleep(.01)
    raise AssertionError('LMS never recorded completion')


def _run(manager, action, operation_id):
    confirm={'deploy-seh':'DEPLOY_SEH','undeploy-seh':'UNDEPLOY_SEH',
             'activate-seh':'ACTIVATE_SEH'}[action]
    generation=manager.control.list('mainnet')['generation']
    ack=manager.harness({'action':action,'execute':True,
        'expected_generation':generation,'confirm':confirm,
        'operation_id':operation_id})
    assert ack['status']=='ACCEPTED' and ack['operation_id']==operation_id
    return done(manager,operation_id)


def test_registered_deploy_and_legacy_explicit_activate_each_delegate_once(tmp_path):
    root,slh,_=setup(tmp_path)
    calls=[]
    def runner(args,*,control):
        calls.append((args.operation,args.execute_mutations))
        return {'status':'CONNECTED_VERIFIED','application_uuid':APP,'remote_mutation':False}
    manager=SLHSEHDeploymentManager(root=root,state_dir=slh,harness_runner=runner,
        runtime_probe=lambda _: {'status':'UNVERIFIED'})
    assert manager.inspect({})['deployment_phase']=='APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED'
    row=_run(manager,'deploy-seh','mcel-seh-resume-two-action-00001')
    assert row['result']['status']=='CONNECTED_VERIFIED'
    assert calls==[('deploy-seh',True)]
    calls.clear()
    legacy=_run(manager,'activate-seh','mcel-seh-legacy-activation-00001')
    assert legacy['result']['status']=='CONNECTED_VERIFIED'
    assert calls==[('activate-seh',True)]


def test_deploy_after_verified_undeploy_uses_one_complete_harness_call(tmp_path):
    root,slh,_=removed(tmp_path)
    calls=[]
    def runner(args,*,control):
        calls.append((args.operation,args.execute_mutations))
        assert args.operation=='deploy-seh' and args.execute_mutations
        _,_,document=st.read(root,'mainnet')
        identity=st.deployment_record(document,'mainnet')
        current=ds.selected(root,'mainnet',identity)['deployment']
        replacement=deepcopy(current)
        replacement['application_uuid']=NEW
        replacement.pop('undeployed',None)
        replacement.pop('undeployed_at',None)
        ds.write(root,'mainnet',identity,expected_uuid=APP,deployment=replacement)
        return {'status':'CONNECTED_VERIFIED','application_uuid':NEW,
                'remote_mutation':True,'outbound_tunnel_authenticated':True}
    manager=SLHSEHDeploymentManager(root=root,state_dir=slh,harness_runner=runner)
    assert manager.inspect({})['deployment_phase']=='UNDEPLOYED'
    row=_run(manager,'deploy-seh','mcel-seh-redeploy-complete-00001')
    assert row['result']['status']=='CONNECTED_VERIFIED'
    assert row['result']['application_uuid']==NEW
    assert calls==[('deploy-seh',True)]


def test_queued_or_refused_outcomes_are_not_fabricated_into_success(tmp_path):
    root,slh,_=removed(tmp_path)
    calls=[]
    def queued(args,*,control):
        calls.append((args.operation,args.execute_mutations))
        return {'status':'REPLACEMENT_QUEUED_NOT_VERIFIED',
                'application_uuid':NEW,'remote_mutation':True}
    manager=SLHSEHDeploymentManager(root=root,state_dir=slh,harness_runner=queued)
    row=_run(manager,'deploy-seh','mcel-seh-queued-not-verified-0001')
    assert row['result']['status']=='REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert row['result']['remote_mutation'] is True
    assert calls==[('deploy-seh',True)]
    def refused(args,*,control):
        calls.append((args.operation,args.execute_mutations))
        return {'status':'REFUSED','error_code':'SEH_CANONICAL_DEPLOYMENT_DISABLED',
                'remote_mutation':False}
    manager.harness_runner=refused
    row=_run(manager,'deploy-seh','mcel-seh-refused-not-overridden-01')
    assert row['result']['status']=='REFUSED'
    assert row['result']['error_code']=='SEH_CANONICAL_DEPLOYMENT_DISABLED'
    assert calls[-1]==('deploy-seh',True)


def test_undeploy_auto_disconnect_is_owned_by_one_harness_call(tmp_path):
    root,slh,_=setup(tmp_path)
    calls=[]
    def runner(args,*,control):
        calls.append((args.operation,args.execute_mutations))
        return {'status':'SEH_UNDEPLOYED','application_removed_verified':True,
                'remote_mutation':True,'application_uuid':APP}
    manager=SLHSEHDeploymentManager(root=root,state_dir=slh,harness_runner=runner)
    row=_run(manager,'undeploy-seh','mcel-seh-undeploy-two-action-00001')
    assert row['result']['status']=='SEH_UNDEPLOYED'
    assert calls==[('undeploy-seh',True)]


def test_unconfigured_deploy_cannot_be_fabricated_by_lms(tmp_path):
    calls=[]
    manager=SLHSEHDeploymentManager(root=root_state(tmp_path),
        harness_runner=lambda args,**kw:calls.append((args.operation,args.execute_mutations)) or
             {'status':'REFUSED','remote_mutation':False})
    row=_run(manager,'deploy-seh','mcel-seh-unconfigured-two-act-01')
    assert row['result']['status']=='REFUSED'
    assert calls==[('deploy-seh',True)]


def test_ui_deploy_and_undeploy_use_existing_bounded_harness_route():
    class Client:
        def __init__(self):self.calls=[]
        def request(self,method,path,payload=None):
            self.calls.append((method,path,payload))
            if method=='GET':return {'generation':33}
            return {'status':'ACCEPTED','operation_id':payload['operation_id']}
    client=Client()
    for verb,expected in [('deploy','deploy-seh'),('undeploy','undeploy-seh')]:
        operation_id='mcel-seh-two-action-'+verb+'-000001'
        result=execute_seh_deployment(verb,{},target='local',local_client=client,
            operation_id=operation_id)
        assert result['outcome']=='accepted-deferred'
        assert result['operationId']==operation_id
        assert client.calls[-1][2]=={'action':expected,'execute':True,
            'expected_generation':33,'confirm':expected.upper().replace('-','_'),
            'operation_id':operation_id}


def test_network_slh_authenticates_and_forwards_same_lms_operation(tmp_path):
    from tests.test_lms_seh_ownership_cutover import Services,send
    services=Services(tmp_path)
    try:
        calls=[]
        def runner(args,*,control):
            calls.append((args.operation,args.execute_mutations))
            return {'status':'CONNECTED_VERIFIED'}
        services.manager.harness_runner=runner
        operation_id='mcel-seh-network-two-action-0001'
        body={'action':'deploy-seh','execute':True,'expected_generation':1,
              'confirm':'DEPLOY_SEH','operation_id':operation_id}
        remote='/v1/org/admin/seh/deployment/harness'
        assert send(services.org.server_port,'POST',remote,body)[0]==401
        code,ack=send(services.org.server_port,'POST',remote,body,
                      headers={'Authorization':'Bearer good'})
        assert code==200 and ack['status']=='ACCEPTED'
        row=done(services.manager,operation_id)
        assert row['result']['status']=='CONNECTED_VERIFIED'
        assert row['principal']=='owner-device'
        assert calls==[('deploy-seh',True)]
    finally:
        services.stop()


def test_harness_live_progress_and_final_report_share_one_receipt(tmp_path):
    root,slh,_=setup(tmp_path)
    manager=SLHSEHDeploymentManager(root=root,state_dir=slh)
    script=tmp_path/'mock_two_action_cli.py'
    script.write_text('''
import json,sys
from pathlib import Path
args=sys.argv[1:]
assert args[0]=='deploy-seh' and '--execute-mutations' in args
report=Path(args[args.index('--report')+1])
print('[SEH +0.1s] build.verify: in_progress', file=sys.stderr, flush=True)
print('[SEH +0.2s] resident.connection: verified', file=sys.stderr, flush=True)
report.write_text(json.dumps({'status':'CONNECTED_VERIFIED',
                                'application_uuid':'owned-app'}),encoding='utf-8')
''',encoding='utf-8')
    manager._harness_script=lambda:script
    operation_id='mcel-seh-two-action-output-000001'
    row=_run(manager,'deploy-seh',operation_id)
    assert row['result']['status']=='CONNECTED_VERIFIED'
    output='\n'.join(item['text'] for item in row['output'])
    assert 'build.verify' in output and 'resident.connection' in output
    restarted=SLHSEHDeploymentManager(root=root,state_dir=slh)
    assert restarted.operation_status(operation_id)['output']==row['output']
