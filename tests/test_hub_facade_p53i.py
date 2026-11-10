"""P5.3i - unchanged helper sources, legacy facade exports, and entrypoint seams."""
from __future__ import annotations

from types import SimpleNamespace

from main_computer import hub, hub_facade_helpers
from main_computer.config import MainComputerConfig
from main_computer.models import ChatAttachment, ChatMessage
from tools.hub_facade_verify import verify_contract


def test_readonly_facade_verifier_preserves_all_prior_exports():
    report = verify_contract()
    assert report['ok'] is True, report
    assert report['historical_export_count'] == 139
    assert report['locally_owned_entrypoints'] == ['serve_hub', 'serve_hub_worker']


def test_stable_identifier_helpers_keep_legacy_identity_and_shapes():
    assert hub._stable_request_id is hub_facade_helpers._stable_request_id
    assert hub._stable_session_id is hub_facade_helpers._stable_session_id
    first = hub._stable_request_id({'messages': ['test']})
    second = hub._stable_session_id(first, {'model': 'demo'})
    assert first.startswith('hub_') and len(first) == 24
    assert second.startswith('sess_') and len(second) == 29


def test_message_wire_conversion_preserves_original_shapes():
    message = ChatMessage(role='user', content='hello', attachments=[])
    assert hub.chat_message_to_dict(message) == {
        'role': 'user', 'content': 'hello', 'attachments': [],
    }
    decoded = hub.chat_response_from_dict({'content':'done', 'metadata':{'x':2}},
                                          default_provider='mock', default_model='base')
    assert decoded.content == 'done' and decoded.provider == 'mock'
    assert decoded.model == 'base' and decoded.metadata == {'x': 2}
    decoded.metadata['x'] = 4
    assert hub.chat_response_from_dict({'metadata':None},
                                        default_provider='fallback', default_model='m').metadata == {}


def test_hub_identity_helper_is_identical_for_local_and_stable():
    local = SimpleNamespace(stable_hub_node=None,config=SimpleNamespace(hub_id='dev-hub',hub_url='http://127.0.0.1:8871'))
    local_identity = hub.serving_hub_identity_for_server(local)
    assert local_identity['source'] == 'local_hub'
    assert local_identity['hub_id'] == 'dev-hub'
    stable = SimpleNamespace(stable_hub_node=SimpleNamespace(
        hub_id='testnet-hub2',hub_url='https://internal.example',
        public_url='https://testnet-hub.greatlibrary.io',roles=['entry']))
    stable_identity = hub.serving_hub_identity_for_server(stable)
    assert stable_identity['source'] == 'stable_topology'
    assert stable_identity['hub_id'] == 'testnet-hub2'
    assert stable_identity['public_url'] == 'https://testnet-hub.greatlibrary.io'


def test_legacy_hub_server_entrypoint_keeps_facade_monkeypatch_seam(tmp_path,monkeypatch):
    events=[]
    class FakeServer:
        def __init__(self,bind,config,verbose):
            events.append(('init',bind,verbose))
            self.server_port=bind[1]
            self.hub_root=tmp_path
        def serve_forever(self):events.append(('serve',))
        def server_close(self):events.append(('close',))
    monkeypatch.setattr(hub,'HubHttpServer',FakeServer)
    config=MainComputerConfig(workspace=tmp_path,hub_root=tmp_path/'hub', hub_network='dev')
    hub.serve_hub(config,host='127.0.0.1',port=8871,verbose=False)
    assert events == [('init',('127.0.0.1',8871),False),('serve',),('close',)]


def test_legacy_worker_entrypoint_keeps_registration_and_handler_monkeypatch_seams(tmp_path,monkeypatch):
    events=[]
    class FakeServer:
        def __init__(self,bind,config,chat_fn,verbose):
            events.append(('init',bind,verbose))
            self.server_port=bind[1]
        def serve_forever(self): events.append(('serve',))
        def server_close(self): events.append(('close',))
    monkeypatch.setattr(hub,'HubWorkerHttpServer',FakeServer)
    monkeypatch.setattr(hub,'register_worker_with_hub',lambda **kw:events.append(('register',kw)) or {'worker':{}})
    config=MainComputerConfig(workspace=tmp_path,hub_root=tmp_path/'hub',hub_network='dev')
    hub.serve_hub_worker(config,lambda messages:None,host='127.0.0.1',port=8771,
                         hub_url='http://127.0.0.1:8871',verbose=False)
    assert [e[0] for e in events] == ['init','register','serve','close']
    assert events[1][1]['hub_url'] == 'http://127.0.0.1:8871'
