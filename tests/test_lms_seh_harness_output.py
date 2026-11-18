"""Captured CLI stdout/stderr, bounded durable LMS receipts, no live Coolify."""
from __future__ import annotations

import json
import time
from pathlib import Path

from main_computer.slh.seh_deployment_management import SLHSEHDeploymentManager
from test_lms_seh_ownership_cutover import root_state, Services, send
from test_slh_seh_coolify_management_backend import TOKEN


def _fake_cli(tmp_path: Path, *, preflight_refuses=False) -> Path:
    script = tmp_path / 'mock_seh_cli.py'
    script.write_text('''
import json, sys, time
from pathlib import Path
args = sys.argv[1:]
report = Path(args[args.index('--report')+1])
mutation = '--execute-mutations' in args
print('SEH_PROGRESS: preflight started', flush=True)
print('stderr: preflight details', file=sys.stderr, flush=True)
time.sleep(.12)
if %r:
    result = {'status':'REFUSED','error_code':'SEH_DEPLOYMENT_PREFLIGHT_REFUSED',
              'remote_mutation':False}
else:
    assert mutation
    print('SEH_PROGRESS: live mutation started', flush=True)
    print('stderr: actual harness diagnostic', file=sys.stderr, flush=True)
    print(%r, file=sys.stderr, flush=True)
    print('Authorization: Bearer top-secret-token', file=sys.stderr, flush=True)
    time.sleep(.20)
    result = {'status':'REPLACEMENT_QUEUED_NOT_VERIFIED','application_uuid':'new-uuid',
              'remote_mutation':True}
report.write_text(json.dumps(result), encoding='utf-8')
sys.exit(2 if result['status']=='REFUSED' else 3 if mutation else 0)
''' % (preflight_refuses, 'value: ' + TOKEN), encoding='utf-8')
    return script


def _wait(manager, operation_id, timeout=6):
    until = time.monotonic()+timeout
    while time.monotonic()<until:
        row = manager.operation_status(operation_id)
        if row['phase']=='RECORDED':
            return row
        time.sleep(.02)
    raise AssertionError('Timed out waiting for captured CLI receipt')


def test_single_harness_lifecycle_streams_are_durable_and_redacted(tmp_path):
    manager = SLHSEHDeploymentManager(root=root_state(tmp_path), state_dir=tmp_path/'slh')
    manager._harness_script=lambda:_fake_cli(tmp_path)
    operation_id='mcel-seh-captured-stdout-stderr-0001'
    body={'action':'deploy-seh','execute':True,'expected_generation':1,
          'confirm':'DEPLOY_SEH','operation_id':operation_id}
    accepted=manager.harness(body)
    assert accepted['status']=='ACCEPTED' and accepted['operation_id']==operation_id
    snapshots=[]
    until=time.monotonic()+6
    while time.monotonic()<until:
        snapshot=manager.operation_snapshot()
        snapshots.append(snapshot)
        if snapshot['state']=='BUSY' and snapshot['operation'].get('output'):
            break
        time.sleep(.02)
    assert any(x['state']=='BUSY' and x['operation'].get('output') for x in snapshots)
    completed=_wait(manager, operation_id)
    assert completed['result']['status']=='REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert completed['result']['remote_mutation'] is True
    assert completed['stage']=='HARNESS_RETURNED'
    assert any(entry['stream']=='stdout' and 'preflight started' in entry['text'] for entry in completed['output'])
    assert any(entry['stream']=='stdout' and 'live mutation started' in entry['text'] for entry in completed['output'])
    assert any(entry['stream']=='stderr' and 'actual harness diagnostic' in entry['text'] for entry in completed['output'])
    assert [item['seq'] for item in completed['output']]==sorted(item['seq'] for item in completed['output'])
    assert TOKEN not in json.dumps(completed)
    assert 'top-secret-token' not in json.dumps(completed)
    assert '[REDACTED]' in json.dumps(completed)
    # A new LMS process reconstructs the same durable sanitized receipt.
    new_manager=SLHSEHDeploymentManager(root=manager.root,state_dir=tmp_path/'slh')
    assert new_manager.operation_status(operation_id)['output']==completed['output']
    assert TOKEN not in (manager.operations.path/(operation_id+'.json')).read_text()


def test_preflight_refusal_is_logged_under_operation_id_without_mutation(tmp_path):
    manager=SLHSEHDeploymentManager(root=root_state(tmp_path),state_dir=tmp_path/'slh')
    manager._harness_script=lambda:_fake_cli(tmp_path,preflight_refuses=True)
    operation_id='mcel-seh-refused-preflight-000001'
    accepted=manager.harness({'action':'deploy-seh','execute':True,
                 'expected_generation':1,'confirm':'DEPLOY_SEH','operation_id':operation_id})
    assert accepted['status']=='ACCEPTED'
    receipt=_wait(manager,operation_id)
    assert receipt['result']['status']=='REFUSED'
    assert receipt['result']['error_code']=='SEH_DEPLOYMENT_PREFLIGHT_REFUSED'
    assert receipt['result']['remote_mutation'] is False
    output='\n'.join(x['text'] for x in receipt['output'])
    assert 'preflight started' in output
    assert 'preflight details' in output
    assert 'live mutation started' not in output


def test_authenticated_network_status_replays_same_sanitized_harness_output(tmp_path):
    services=Services(tmp_path)
    try:
        services.manager._harness_script=lambda:_fake_cli(tmp_path)
        operation_id='mcel-seh-network-output-12345678'
        body={'action':'deploy-seh','execute':True,'expected_generation':1,
              'confirm':'DEPLOY_SEH','operation_id':operation_id}
        code, accepted=send(services.lms.server_port,'POST','/v1/local/seh/deployment/harness',body)
        assert code==200 and accepted['status']=='ACCEPTED'
        local=_wait(services.manager,operation_id)
        remote_path='/v1/org/admin/seh/deployment/operations/'+operation_id
        assert send(services.org.server_port,'GET',remote_path)[0]==401
        code, remote=send(services.org.server_port,'GET',remote_path,
                headers={'Authorization':'Bearer good'})
        assert code==200
        assert remote['output']==local['output']
        assert TOKEN not in json.dumps(remote)
        assert 'live mutation started' in json.dumps(remote)
    finally:
        services.stop()


def test_python_import_failure_reaches_stderr_without_coolify_mutation(tmp_path):
    manager=SLHSEHDeploymentManager(root=root_state(tmp_path),state_dir=tmp_path/'slh')
    broken=tmp_path/'broken_harness.py'
    broken.write_text('import sys\nprint("ModuleNotFoundError: No module named cryptography",file=sys.stderr,flush=True)\nsys.exit(1)\n',encoding='utf-8')
    manager._harness_script=lambda:broken
    operation_id='mcel-seh-import-error-1234567890'
    assert manager.harness({'action':'deploy-seh','execute':True,
              'expected_generation':1,'confirm':'DEPLOY_SEH','operation_id':operation_id})['status']=='ACCEPTED'
    receipt=_wait(manager,operation_id)
    assert receipt['result']['status']=='INCOMPLETE'
    assert receipt['result']['error_code']=='HARNESS_EXECUTION_UNCONFIRMED'
    assert receipt['result']['remote_mutation']=='UNKNOWN'
    assert any(entry['stream']=='stderr' and 'ModuleNotFoundError' in entry['text']
               for entry in receipt['output'])


def test_bounded_output_cannot_exceed_forwarding_response_limit(tmp_path):
    import threading
    from main_computer.slh.seh_management_journal import ManagementJournal
    journal=ManagementJournal(tmp_path,'mainnet')
    gate=threading.Event()
    operation_id='mcel-seh-bounded-console-12345678'
    journal.submit(operation_id,{'action':'deploy-seh','principal':'local-machine'},
                   lambda: (gate.wait(4),{'status':'PLAN_ONLY'})[1])
    try:
        # Worst-case JSON expansion from non-ASCII output remains under the
        # existing 128-KiB LMS forwarding payload limit.
        for i in range(120):
            journal.append_output(operation_id,'stderr','雪'*600)
        state=journal.snapshot()
        assert state['state']=='BUSY'
        rows=state['operation']['output']
        assert len(rows)==64
        assert rows[0]['seq']==57 and rows[-1]['seq']==120
        assert max(len(row['text']) for row in rows)<=256
        assert len(json.dumps(state).encode('utf-8'))<131072
    finally:
        gate.set()
