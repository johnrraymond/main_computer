"""Bounded SEH public health projection and mounted-page refresh; no network/mutations."""
from __future__ import annotations

from main_computer.slh.seh_deployment_management import SLHSEHDeploymentManager
from main_computer.mcel_slh_application import render_slh_page
from tests.test_seh_deleted_application_recovery import setup


def test_registered_public_runtime_status_is_explicit_and_read_only(tmp_path):
    root, slh, _ = setup(tmp_path)
    calls = []
    responses = [
        {'status':'PUBLIC_RUNTIME_PRESENT','http_status':200,'slh_connected':True},
        {'status':'PUBLIC_RUNTIME_PRESENT','http_status':200,'slh_connected':False},
        {'status':'PUBLIC_RUNTIME_UNAVAILABLE','http_status':503},
    ]
    def probe(url):
        calls.append(url)
        return responses.pop(0)
    manager = SLHSEHDeploymentManager(root=root, state_dir=slh, runtime_probe=probe)
    before = manager.control.list('mainnet')['generation']
    assert manager.inspect({})['deployment_phase'] == 'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED'
    assert manager.inspect({})['deployment_phase'] == 'PUBLIC_RUNTIME_PRESENT_SLH_DISCONNECTED'
    assert manager.inspect({})['deployment_phase'] == 'APPLICATION_REGISTERED_RUNTIME_UNAVAILABLE'
    assert calls == ['https://external.greatlibrary.io'] * 3
    assert manager.control.list('mainnet')['generation'] == before


def test_failed_probe_never_converts_registration_to_verified(tmp_path):
    root, slh, _ = setup(tmp_path)
    def offline(_url):
        raise OSError('offline')
    manager = SLHSEHDeploymentManager(root=root,state_dir=slh,runtime_probe=offline)
    assert manager.inspect({})['deployment_phase'] == 'APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED'


def test_existing_page_timer_refreshes_seh_and_skips_overlap():
    html = render_slh_page('overview')
    assert 'void refreshSehObservations();' in html
    assert '"intent:read-seh-deployment"' in html
    assert '"intent:read-seh-connection"' in html
    assert 'Promise.allSettled(intents.map(intent=>app.execute(intent)))' in html
    assert 'sehObservationPending' in html
    assert 'window.clearInterval(healthPoll)' in html
    assert '},15000):null;' in html


def test_deployment_read_projection_passes_through_health_observation(tmp_path):
    from main_computer.mcel_slh_seh_deployment import execute_seh_deployment
    root, slh, _ = setup(tmp_path)
    manager = SLHSEHDeploymentManager(root=root, state_dir=slh,
        runtime_probe=lambda _: {'status':'PUBLIC_RUNTIME_PRESENT',
                                 'slh_connected':True})
    class ReadOnly:
        def request(self, method, path, payload=None):
            assert (method,path,payload)==('GET','/v1/local/seh/deployment',None)
            return manager.inspect({})
    result = execute_seh_deployment('inspect', {},target='local',local_client=ReadOnly())
    assert result['deploymentStatus'] == 'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED'


def test_undeploy_remains_available_after_public_health_observation():
    from main_computer.mcel_slh_application import compile_slh_application
    ir, _ = compile_slh_application()
    nodes = {node['id']:node for node in ir['surfaces'][0]['nodes']}
    action = nodes['surface-node:slh.local-seh-lifecycle']['tabs'][1]['actions']['Undeploy']
    assert action['intent']['ref'] == 'intent:seh-deployment-undeploy'
    # The harness is authoritative; no single-status UI gate may disable Undeploy.
    assert 'enabledWhen' not in action
