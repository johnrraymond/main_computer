"""YAGNI LMS deploy/activation ownership: no live Coolify or SLH mutation."""
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
    until = time.monotonic() + 6
    while time.monotonic() < until:
        status = manager.operation_status(operation_id)
        if status['phase'] == 'RECORDED':
            return status
        time.sleep(.01)
    raise AssertionError('LMS never recorded completion')


def _run(manager, action, operation_id):
    confirm = {'deploy-seh':'DEPLOY_SEH', 'activate-seh':'ACTIVATE_SEH'}[action]
    generation = manager.control.list('mainnet')['generation']
    receipt = manager.harness({'action':action, 'execute':True,
        'expected_generation':generation,'confirm':confirm,
        'operation_id':operation_id})
    assert receipt['status'] == 'ACCEPTED' and receipt['operation_id'] == operation_id
    return done(manager, operation_id)


def test_existing_registered_uuid_activates_without_any_deploy_mutation(tmp_path):
    root, slh, unused_remote = setup(tmp_path)
    calls = []
    def runner(args, *, control):
        calls.append((args.operation, args.execute_mutations))
        assert args.operation == 'activate-seh', 'existing application must not be redeployed'
        return {'status': 'CONNECTED_VERIFIED' if args.execute_mutations else 'PLAN_ONLY',
                'application_uuid': APP,
                'remote_mutation':False}
    manager = SLHSEHDeploymentManager(root=root,state_dir=slh,harness_runner=runner)
    assert manager.inspect({})['deployment_phase'] == 'APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED'
    receipt = _run(manager,'activate-seh','mcel-seh-explicit-activate-0000001')
    assert receipt['result']['status'] == 'CONNECTED_VERIFIED'
    assert calls == [('activate-seh',False),('activate-seh',True)]
    # A stale Deploy click also resumes the existing application, never creates another.
    calls.clear()
    receipt = _run(manager,'deploy-seh','mcel-seh-stale-deploy-0000001')
    assert receipt['result']['status'] == 'CONNECTED_VERIFIED'
    assert calls == [('activate-seh',False),('activate-seh',True)]


def test_intentional_redeployment_flows_to_activation_under_one_operation_id(tmp_path):
    root, slh, unused_remote = removed(tmp_path)
    calls=[]
    def runner(args, *, control):
        calls.append((args.operation, args.execute_mutations))
        if not args.execute_mutations:
            return {'status':'PLAN_ONLY', 'remote_mutation':False}
        if args.operation == 'deploy-seh':
            _,_,document = st.read(root,'mainnet')
            identity = st.deployment_record(document,'mainnet')
            prior = ds.selected(root,'mainnet',identity)['deployment']
            updated = deepcopy(prior)
            updated['application_uuid']=NEW
            updated.pop('undeployed',None)
            updated.pop('undeployed_at',None)
            ds.write(root,'mainnet',identity,expected_uuid=APP,deployment=updated)
            return {'status':'REPLACEMENT_QUEUED_NOT_VERIFIED',
                    'application_uuid':NEW,'remote_mutation':True}
        assert args.operation == 'activate-seh'
        return {'status':'CONNECTED_VERIFIED','application_uuid':NEW,
                'outbound_tunnel_authenticated':True}
    manager=SLHSEHDeploymentManager(root=root,state_dir=slh,harness_runner=runner)
    assert manager.inspect({})['deployment_phase']=='UNDEPLOYED'
    receipt=_run(manager,'deploy-seh','mcel-seh-redeploy-activate-0000001')
    assert receipt['result']['status']=='CONNECTED_VERIFIED'
    assert receipt['result']['deployment_status']=='REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert receipt['result']['deployment_remote_mutation'] is True
    assert receipt['result']['application_uuid']==NEW
    assert calls==[('deploy-seh',False),('deploy-seh',True),
                   ('activate-seh',False),('activate-seh',True)]


def test_queue_ack_without_canonical_readback_does_not_authorize_activation(tmp_path):
    root, slh, unused_remote = removed(tmp_path)
    calls=[]
    def runner(args, *, control):
        calls.append((args.operation,args.execute_mutations))
        return ({'status':'REPLACEMENT_QUEUED_NOT_VERIFIED','application_uuid':NEW,
                 'remote_mutation':True} if args.execute_mutations else {'status':'PLAN_ONLY'})
    manager=SLHSEHDeploymentManager(root=root,state_dir=slh,harness_runner=runner)
    receipt=_run(manager,'deploy-seh','mcel-seh-unverified-readback-000001')
    assert receipt['result']['status']=='DEPLOYMENT_QUEUED_ACTIVATION_PENDING'
    assert receipt['result']['activation_reason']=='CANONICAL_APPLICATION_READBACK_UNVERIFIED'
    assert calls==[('deploy-seh',False),('deploy-seh',True)]


def test_activation_preflight_refusal_is_recorded_without_mutation(tmp_path):
    root, slh, unused_remote = setup(tmp_path)
    calls=[]
    def runner(args, *,control):
        calls.append((args.operation,args.execute_mutations))
        return {'status':'REFUSED','remote_mutation':False}
    manager=SLHSEHDeploymentManager(root=root,state_dir=slh,harness_runner=runner)
    receipt=_run(manager,'activate-seh','mcel-seh-activation-refused-000001')
    assert receipt['result']['error_code']=='SEH_ACTIVATION_PREFLIGHT_REFUSED'
    assert receipt['result']['remote_mutation'] is False
    assert calls==[('activate-seh',False)]


def test_no_canonical_application_cannot_be_created_by_deploy_action(tmp_path):
    calls=[]
    manager=SLHSEHDeploymentManager(root=root_state(tmp_path),
        harness_runner=lambda args,**kwargs:calls.append((args.operation,args.execute_mutations)) or {'status':'REFUSED'})
    receipt=_run(manager,'deploy-seh','mcel-seh-unconfigured-000000001')
    assert receipt['result']['error_code']=='SEH_DEPLOYMENT_PREFLIGHT_REFUSED'
    assert receipt['result']['remote_mutation'] is False
    assert calls==[('deploy-seh',False)]


def test_ui_activation_uses_same_allowlisted_lms_harness_contract():
    class Client:
        def __init__(self):self.calls=[]
        def request(self,method,path,payload=None):
            self.calls.append((method,path,payload))
            if method=='GET':return {'generation':33}
            return {'status':'ACCEPTED','operation_id':'mcel-seh-ui-activate-00000001'}
    client=Client()
    result=execute_seh_deployment('activate',{},target='local',local_client=client,
        operation_id='mcel-seh-ui-activate-00000001')
    assert result['outcome']=='accepted-deferred'
    assert result['operationId']=='mcel-seh-ui-activate-00000001'
    assert client.calls[-1][2]=={'action':'activate-seh','execute':True,
        'expected_generation':33,'confirm':'ACTIVATE_SEH',
        'operation_id':'mcel-seh-ui-activate-00000001'}
    client.calls=[]
    preview=execute_seh_deployment('preview-activate',{},target='local',local_client=client)
    assert client.calls[-1][2]=={'action':'activate-seh','execute':False,
        'expected_generation':None,'confirm':None}


def test_network_slh_authenticates_then_forwards_activation_to_same_lms(tmp_path):
    from tests.test_lms_seh_ownership_cutover import Services, send
    services=Services(tmp_path)
    try:
        calls=[]
        services.manager.inspect=lambda body: {'deployment_phase':'APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED'}
        def runner(args, *,control):
            calls.append((args.operation,args.execute_mutations))
            assert args.operation=='activate-seh'
            return {'status':'PLAN_ONLY' if not args.execute_mutations else 'CONNECTED_VERIFIED'}
        services.manager.harness_runner=runner
        operation_id='mcel-seh-network-activate-0000001'
        body={'action':'activate-seh','execute':True,'expected_generation':1,
              'confirm':'ACTIVATE_SEH','operation_id':operation_id}
        remote='/v1/org/admin/seh/deployment/harness'
        assert send(services.org.server_port,'POST',remote,body)[0]==401
        code,result=send(services.org.server_port,'POST',remote,body,
                         headers={'Authorization':'Bearer good'})
        assert code==200 and result['status']=='ACCEPTED'
        receipt=done(services.manager,operation_id)
        assert receipt['result']['status']=='CONNECTED_VERIFIED'
        assert receipt['principal']=='owner-device'
        assert calls==[('activate-seh',False),('activate-seh',True)]
        code,view=send(services.org.server_port,'GET',
            '/v1/org/admin/seh/deployment/operations/'+operation_id,
            headers={'Authorization':'Bearer good'})
        assert code==200 and view['result']['status']=='CONNECTED_VERIFIED'
    finally:
        services.stop()


def test_activation_harness_stdout_stderr_are_durable_in_one_receipt(tmp_path):
    import json
    root, slh, unused_remote=setup(tmp_path)
    manager=SLHSEHDeploymentManager(root=root,state_dir=slh)
    script=tmp_path/'mock_activation_cli.py'
    script.write_text('''
import json,sys
from pathlib import Path
args=sys.argv[1:]
assert args[0]=='activate-seh'
report=Path(args[args.index('--report')+1])
mutation='--execute-mutations' in args
print('activation preflight' if not mutation else 'activation worker started', flush=True)
print('activation stderr detail', file=sys.stderr, flush=True)
report.write_text(json.dumps({'status':'CONNECTED_VERIFIED' if mutation else 'PLAN_ONLY',
                                'application_uuid':'owned-app'}),encoding='utf-8')
''',encoding='utf-8')
    manager._harness_script=lambda:script
    operation_id='mcel-seh-activation-streams-000001'
    receipt=_run(manager,'activate-seh',operation_id)
    assert receipt['result']['status']=='CONNECTED_VERIFIED'
    output='\n'.join(x['text'] for x in receipt['output'])
    assert 'activation preflight' in output
    assert 'activation worker started' in output
    assert 'activation stderr detail' in output
    assert manager.operation_snapshot()['busy'] is False
    reconnect=SLHSEHDeploymentManager(root=root,state_dir=slh)
    assert reconnect.operation_status(operation_id)['output']==receipt['output']
