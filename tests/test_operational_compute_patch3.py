"""Patch 3: live Worker/Requester orchestration using existing services only."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from main_computer import operational_compute as ops

SHA = 'a' * 64


class StubBackend:
    instances = []
    def __init__(self, *, model, base_url):
        self.model = model
        self.base_url = base_url
        self.__class__.instances.append(self)
    def advertisement(self):
        if self.model != 'nanojev-clef':
            raise ValueError('not loaded')
        return {'models': ['nanojev-clef'],
                'capabilities': ['nanojev.clef.evaluate.v1'],
                'checkpoint_sha256': SHA}
    def execute_offer(self, offer):
        return {'status': 'success', 'response': {'model': self.model},
                'solution': {'model': {'family': self.model}}}


class FakeLiveClient:
    clients = []
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.worker_id = kwargs['worker_id']
        self.__class__.clients.append(self)


def test_two_direct_hub_workers_share_one_backend(monkeypatch):
    from main_computer import hub_cli_live_session as live
    from main_computer import worker_live_session as live_worker
    StubBackend.instances.clear()
    FakeLiveClient.clients.clear()
    monkeypatch.setattr(ops, 'NanoJevSharedWorkerBackend', StubBackend)
    monkeypatch.setattr(live_worker, '_WorkerHubLiveSessionClient', FakeLiveClient)
    monkeypatch.setattr(live, 'identify_hub_protocol', lambda _: {'service': 'main_computer.stable_hub'})
    monkeypatch.setattr(live, '_officer_wallet', lambda _: 'wallet')
    monkeypatch.setattr(live, 'issue_cli_multisession_authorization',
                        lambda *a, **kw: {'chain_id':'42424240','wallet_address':'0x'+'2'*40, 'key_id':'msk'})
    monkeypatch.setattr(ops, '_run_clients', lambda clients, **kw: None)
    assert ops.run_worker(model='nanojev-clef', count=2, via='hub', hub_url='https://hub.invalid') == 0
    assert len(StubBackend.instances) == 1
    assert len(FakeLiveClient.clients) == 2
    clients = FakeLiveClient.clients
    assert clients[0].worker_id != clients[1].worker_id
    for client in clients:
        offer_executor = client.kwargs['work_executor']
        assert offer_executor.__self__ is StubBackend.instances[0]
        assert client.kwargs['auth_message']['market']['models'] == ['nanojev-clef']
        assert client.kwargs['auth_message']['market']['checkpoint_sha256'] == SHA
        assert client.kwargs['auth_message']['market']['capabilities'] == ['nanojev.clef.evaluate.v1']
        assert client.kwargs['auth_message']['multisession_authorization']['key_id'] == 'msk'


def test_unloaded_model_prevents_worker_connections(monkeypatch):
    FakeLiveClient.clients.clear()
    monkeypatch.setattr(ops, 'NanoJevSharedWorkerBackend', StubBackend)
    with pytest.raises(ValueError, match='not loaded'):
        ops.run_worker(model='wrong', count=2, via='hub', hub_url='https://hub.invalid')
    assert not FakeLiveClient.clients


def test_seh_requires_authenticated_outbound_relay(monkeypatch):
    monkeypatch.setattr(ops, 'NanoJevSharedWorkerBackend', StubBackend)
    monkeypatch.setattr(ops, '_org_context', lambda **kw: (
        {'api_port': 8899, 'worker_port': 8898},
        SimpleNamespace(request=lambda *a, **kw: {'seh_enabled': True, 'seh_connection_verified': False}),
        SHA))
    with pytest.raises(ops.OperationalComputeError, match='not verified'):
        ops.run_worker(model='nanojev-clef', count=2, via='seh')


def test_two_managed_slh_workers_share_backend_and_separate_devices(monkeypatch, tmp_path):
    from main_computer.slh import managed_client
    FakeLiveClient.clients.clear()
    StubBackend.instances.clear()
    monkeypatch.setattr(ops, 'NanoJevSharedWorkerBackend', StubBackend)
    monkeypatch.setattr(managed_client, 'ManagedSLHWorkerClient', FakeLiveClient)
    monkeypatch.setattr(ops, '_org_context', lambda **kw: ({'api_port':8899,'worker_port':8898}, object(), SHA))
    approved = []
    monkeypatch.setattr(ops, '_assure_enrolled', lambda api, **kw: approved.append(kw['device_id']) or True)
    monkeypatch.setattr(ops, '_run_clients', lambda clients, **kw: None)
    assert ops.run_worker(model='nanojev-clef', via='slh', count=2, identity_dir=tmp_path) == 0
    assert approved == ['mc-nanojev-worker-01','mc-nanojev-worker-02']
    assert len(StubBackend.instances) == 1
    assert len(FakeLiveClient.clients) == 2
    for client in FakeLiveClient.clients:
        assert client.kwargs['hub_url'] == 'https://127.0.0.1:8898'
        assert client.kwargs['tls_fingerprint'] == SHA
        assert client.kwargs['auth_message']['device_id'] == client.worker_id
        assert client.kwargs['work_executor'].__self__ is StubBackend.instances[0]


def test_enrollment_is_not_auto_approved_and_reuses_pending(monkeypatch, tmp_path):
    marker = tmp_path / 'pending.json'
    monkeypatch.setattr(ops, '_enrollment_state_path', lambda: marker)
    monkeypatch.setattr(ops, '_local_bootstrap_status', lambda: {'service_fingerprint':'f' * 64})
    class TestAPI:
        requests = []
        def request(self, method, path, body=None, **kw):
            self.requests.append((method,path,body))
            if path == '/v1/auth/challenge':
                raise ops.OperationalComputeError('SLH /v1/auth/challenge rejected HTTP 403: UNKNOWN_OR_REVOKED_DEVICE')
            if path == '/v1/enrollment/admins':
                return {'admins':[{'id':'owner','name':'Owner'}]}
            if path == '/v1/enrollment/requests':
                assert body['proof']
                return {'request_id':'pending-123'}
            if path == '/v1/enrollment/requests/pending-123/status':
                return {'status':'pending'}
            raise AssertionError(path)
    api = TestAPI()
    assert ops._assure_enrolled(api,device_id='mc-nanojev-worker-01',role='worker',identity_dir=tmp_path) is False
    assert json.loads(marker.read_text())['mc-nanojev-worker-01']['request_id'] == 'pending-123'
    assert ops._assure_enrolled(api,device_id='mc-nanojev-worker-01',role='worker',identity_dir=tmp_path) is False
    assert [p for _,p,_ in api.requests].count('/v1/enrollment/requests') == 1


def test_request_preview_has_no_auth_or_service_calls(monkeypatch, capsys):
    monkeypatch.setattr(ops, '_org_context', lambda **kw: pytest.fail('SLH called for preview'))
    payload={'states':[{'id':'s'}]}
    assert ops.run_requester(model='nanojev-clef', operation='nanojev.clef.evaluate.v1',
                             payload=payload,to='slh',submit=False) == 0
    receipt=json.loads(capsys.readouterr().out)
    assert receipt['dry_run'] is True and receipt['request']['work']['model']=='nanojev-clef'
    assert receipt['request']['work']['capabilities'] == ['nanojev.clef.evaluate.v1']


def test_private_request_never_selects_worker_or_falls_back(monkeypatch, tmp_path, capsys):
    from main_computer.providers.nanojev import CLEF_EVALUATE
    class API:
        sent = []
        def request(self, method, path, body=None, *, token=''):
            self.sent.append((method,path,body,token))
            if path == '/v1/work':
                return {'status':'SUCCEEDED','result':{'response':{'model':'nanojev-clef'}}}
            return {'ok': True}
    api=API()
    monkeypatch.setattr(ops, '_org_context', lambda **kw: ({'api_port':8899}, api, SHA))
    monkeypatch.setattr(ops, '_assure_enrolled', lambda *a, **kw: True)
    monkeypatch.setattr(ops, '_login_requester', lambda *a, **kw: 'session-token')
    assert ops.run_requester(model='nanojev-clef',operation=CLEF_EVALUATE,payload={'states':[{'id':'s'}]},
                             to='slh',submit=True,identity_dir=tmp_path) == 0
    work = next(item[2]['work'] for item in api.sent if item[1]=='/v1/work')
    assert work['model']=='nanojev-clef' and work['operation']==CLEF_EVALUATE
    assert 'worker_id' not in work and 'worker_device_id' not in work
    assert api.sent[-1][1]=='/v1/auth/logout'


def test_invalid_count_and_price_rejected_before_auth(monkeypatch):
    monkeypatch.setattr(ops, 'NanoJevSharedWorkerBackend', StubBackend)
    with pytest.raises(ValueError, match='between 1 and 32'):
        ops.run_worker(model='nanojev-clef',count=33,via='hub')
    with pytest.raises(ValueError, match='finite positive'):
        ops.run_worker(model='nanojev-clef',count=1,price='-1',via='hub')


def test_hub_terminal_receipt_requires_requested_execution_model_and_checkpoint(monkeypatch):
    from main_computer import nanojev_hub_request as hub_request
    base = {'status':'success',
            'response':{'model':'nanojev-clef'},
            'solution':{'schema':'main-computer.nanojev-solution.v1',
                        'capability':'nanojev.clef.evaluate.v1',
                        'model':{'family':'nanojev-clef','release_manifest_sha256':SHA}}}
    accepted={'session_id':'sess-1', 'continuation_token':'opaque-requester-capability', 'continuation_url':
              'https://example.org/api/hub/v1/work/sessions/sess-1/stream'}
    monkeypatch.setattr(hub_request, '_hub_json',lambda *a, **kw: {
        'accepted_session':{'session_id':'sess-1','status':'succeeded',
                            'worker_result':{'result':base}, 'payout':{'charged_credits':'1'}}})
    assert hub_request.retrieve_solution(accepted,hub_url='https://example.org',timeout_s=5,
        expected_model='nanojev-clef',expected_operation='nanojev.clef.evaluate.v1')['status']=='succeeded'
    base['response']['model']='another-head'
    with pytest.raises(RuntimeError,match='model/operation'):
        hub_request.retrieve_solution(accepted,hub_url='https://example.org',timeout_s=5,
            expected_model='nanojev-clef',expected_operation='nanojev.clef.evaluate.v1')
    base['response']['model']='nanojev-clef'
    base['solution']['model']['release_manifest_sha256']='wrong'
    with pytest.raises(RuntimeError,match='checkpoint'):
        hub_request.retrieve_solution(accepted,hub_url='https://example.org',timeout_s=5,
            expected_model='nanojev-clef',expected_operation='nanojev.clef.evaluate.v1')


def test_operational_runtime_imports_no_repository_tool_modules():
    """Production Worker/Requester paths may not import uninstalled `tools`."""
    from pathlib import Path
    source = Path(ops.__file__).read_text(encoding='utf-8')
    managed = (Path(ops.__file__).parent / 'slh' / 'managed_client.py').read_text(encoding='utf-8')
    assert 'from tools.' not in source
    assert 'from tools.' not in managed


def test_packaged_hub_client_available():
    from main_computer.nanojev_hub_request import retrieve_solution, _hub_json
    assert callable(retrieve_solution)
    assert callable(_hub_json)
