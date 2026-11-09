"""P5.3h: Worker client extraction, all network calls isolated to fakes."""
from __future__ import annotations

import json
from dataclasses import replace
from urllib.error import HTTPError, URLError

import pytest

from main_computer import hub, hub_worker_client
from main_computer.config import MainComputerConfig
from main_computer.models import ChatResponse
from tools.hub_worker_client_verify import verify_contract


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.payload).encode('utf-8')


def test_readonly_extraction_verifier_and_original_import_identity():
    result = verify_contract()
    assert result['ok'] is True, result
    assert hub.register_worker_with_hub is hub_worker_client.register_worker_with_hub
    assert hub.serve_hub_worker_pull is hub_worker_client.serve_hub_worker_pull


def test_registration_preserves_original_endpoint_headers_payload_and_credit_units(monkeypatch):
    received=[]
    def fake_open(request, timeout):
        received.append((request.full_url, dict(request.header_items()), json.loads(request.data),timeout))
        return FakeResponse({'ok': True, 'worker': {'node_id': 'worker-1'}})
    monkeypatch.setattr(hub_worker_client,'urlopen',fake_open)
    reply=hub.register_worker_with_hub(hub_url='https://mainnet-hub.greatlibrary.io',
             node_id='worker-1', endpoint='https://worker.example', model='model-a',
             credits_per_request='0.2', assigned_ring=3, capabilities={'worker_pull_v0':True})
    assert reply['ok']
    url,headers,payload,timeout=received[0]
    assert url=='https://mainnet-hub.greatlibrary.io/api/hub/v1/workers/register'
    assert payload['node_id']=='worker-1'
    assert payload['model']=='model-a' and payload['models']==['model-a']
    assert payload['credits_per_request']=='0.2' and payload['credits_per_request_wei']=='200000000000000000'
    assert payload['assigned_ring']==3 and payload['capabilities']['worker_pull_v0']
    assert {k.lower():v for k,v in headers.items()}['x-main-computer-client']=='main-computer-worker-cli'
    assert timeout==10.0


def test_registration_refuses_public_plaintext_before_any_network_call(monkeypatch):
    monkeypatch.setattr(hub_worker_client,'urlopen',lambda *a,**kw:pytest.fail('network must not be touched'))
    with pytest.raises((RuntimeError,ValueError)):
        hub.register_worker_with_hub(hub_url='http://public.example',node_id='worker',endpoint='https://worker.example')


def test_rest_client_preserves_url_and_http_error_context(monkeypatch):
    requests=[]
    def fake_open(request,timeout):
        requests.append(request.full_url)
        return FakeResponse({'ok':True})
    monkeypatch.setattr(hub_worker_client,'urlopen',fake_open)
    result=hub_worker_client._post_hub_worker_json(hub_url='https://example.org',path='/api/hub/v1/workers/poll',payload={'worker_node_id':'w'},timeout_s=4)
    assert result['ok'] is True
    assert requests==['https://example.org/api/hub/v1/workers/poll']
    monkeypatch.setattr(hub_worker_client,'urlopen',lambda *a,**kw:FakeResponse({'error':'denied'}))
    with pytest.raises(RuntimeError,match='denied'):
        hub_worker_client._post_hub_worker_json(hub_url='https://example.org',path='/api/hub/v1/workers/poll',payload={},timeout_s=1)


def test_rest_pull_loop_preserves_register_heartbeat_poll_result_order_and_metadata(monkeypatch,tmp_path):
    from main_computer import hub_cli_live_session as live
    monkeypatch.setattr(live,'hub_uses_live_sessions',lambda url:False)
    recorded=[]
    def fake_register(**kw):
        recorded.append(('register',kw))
        return {'ok':True}
    def fake_post(**kw):
        recorded.append((kw['path'],kw['payload']))
        if kw['path'].endswith('/poll'):
            return {'lease':{'request_id':'request-1','lease_id':'lease-1',
                             'messages':[{'role':'user','content':'compute this'}]}}
        return {'ok':True,'request':{'state':'completed'}}
    monkeypatch.setattr(hub_worker_client,'register_worker_with_hub',fake_register)
    monkeypatch.setattr(hub_worker_client,'_post_hub_worker_json',fake_post)
    config=replace(MainComputerConfig.from_env(),provider='ollama',model='test-model',
                   hub_worker_node_id='test-worker',hub_url='https://example.org',
                   hub_allow_insecure_dev_network=False)
    seen=[]
    def chat(messages):
        seen.append([(message.role,message.content) for message in messages])
        return ChatResponse(content='done',provider='mock',model='test-model',metadata={'sample':'yes'})
    hub.serve_hub_worker_pull(config,chat,verbose=False,max_requests=1,poll_interval_s=.1)
    assert [key for key,_ in recorded] == ['register',
        '/api/hub/v1/workers/heartbeat','/api/hub/v1/workers/poll',
        '/api/hub/v1/workers/heartbeat','/api/hub/v1/workers/results']
    assert seen==[[('user','compute this')]]
    submitted=recorded[-1][1]
    assert submitted['request_id']=='request-1' and submitted['lease_id']=='lease-1'
    assert submitted['result']['response']['content']=='done'
    assert submitted['result']['response']['metadata']['worker_pull_v0'] is True
    assert submitted['result']['response']['metadata']['sample']=='yes'


def test_stable_live_session_delegation_unchanged_and_no_rest_registration(monkeypatch):
    from main_computer import hub_cli_live_session as live
    monkeypatch.setattr(live,'hub_uses_live_sessions',lambda url:True)
    seen=[]
    monkeypatch.setattr(live,'serve_cli_live_worker',lambda **kw:seen.append(kw))
    monkeypatch.setattr(hub_worker_client,'register_worker_with_hub',lambda **kw:pytest.fail('no REST register on live session'))
    config=replace(MainComputerConfig.from_env(),provider='ollama',model='m',
                   hub_url='https://mainnet-hub.greatlibrary.io')
    chat=lambda _:ChatResponse(content='unused',provider='mock',model='m')
    hub.serve_hub_worker_pull(config,chat,verbose=False,max_requests=1,solve_fn=None)
    assert len(seen)==1
    assert seen[0]['hub_url']=='https://mainnet-hub.greatlibrary.io'
    assert seen[0]['chat_fn'] is chat and seen[0]['max_requests']==1


def test_nanojev_cannot_fall_back_to_legacy_rest(monkeypatch):
    from main_computer import hub_cli_live_session as live
    monkeypatch.setattr(live,'hub_uses_live_sessions',lambda url:False)
    monkeypatch.setattr(hub_worker_client,'register_worker_with_hub',lambda **kw:pytest.fail('should refuse first'))
    config=replace(MainComputerConfig.from_env(),provider='nanojev',model='nanojev-clef')
    with pytest.raises(RuntimeError,match='authenticated live-session'):
        hub.serve_hub_worker_pull(config,lambda _:None,verbose=False)
