"""The gateway must preserve close direction and exception class without leaking data."""
from collections import deque
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from main_computer.slh.managed_gateway import MANAGED_PATH, SLHManagedGateway
from tools.slh_seh_lab.integrated_stress import _connection_loss_timelines


def gateway_stub():
    gateway = object.__new__(SLHManagedGateway)
    gateway._connection_events = deque(maxlen=16)
    gateway._connection_events_lock = threading.Lock()
    gateway._connection_events_total = 0
    return gateway


def test_gateway_audit_redacts_exception_text_and_records_websocket_direction():
    gateway = gateway_stub()
    exception = RuntimeError('secret-multisession-key-dont-record')
    exception.rcvd = SimpleNamespace(code=1006)
    exception.sent = SimpleNamespace(code=1000)
    exception.rcvd_then_sent = True
    ws = SimpleNamespace(remote_address=('127.0.0.1', 53214), close_code=1006)
    gateway._note_connection('gateway_handler_exception', device_id='lab-stress-0042',
                             socket=ws, phase='live', exc=exception)
    row = gateway.connection_diagnostics()['events'][-1]
    assert row['exception_class'] == 'RuntimeError'
    assert row['received_close_code'] == 1006
    assert row['sent_close_code'] == 1000
    assert row['received_close_before_sent'] is True
    assert row['gateway_peer_port'] == 53214
    assert 'secret' not in json.dumps(row)
    assert '127.0.0.1' not in json.dumps(row)


def test_gateway_audit_ring_is_bounded():
    gateway = gateway_stub()
    for i in range(32):
        gateway._note_connection('gateway_worker_unregistered', device_id='lab-stress-0001')
    snap = gateway.connection_diagnostics()
    assert snap['events_retained'] == 16
    assert snap['total_events'] == 32


def test_gateway_handler_records_actual_exit_before_unregistration():
    gateway = gateway_stub()
    authority = Mock()
    authority.challenge.return_value = {'challenge_id': '1'}
    authority.verify_login.return_value = {'access_token': 'secret'}
    authority.authenticate.return_value = {'role': 'worker'}
    gateway.authority = authority
    gateway.scheduler = Mock()
    gateway.on_worker_registered = None
    gateway.on_worker_disconnected = None

    class FakeWebSocket:
        request = SimpleNamespace(path=MANAGED_PATH)
        remote_address = ('127.0.0.1', 53678)
        close_code = 1006
        calls = 0
        def recv(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                return json.dumps({'type':'worker.auth', 'device_id':'lab-stress-0001',
                                   'execution_protocol':'durable-authorization-v1',
                                   'market':{'models':['mock-model']}})
            return json.dumps({'type':'slh.auth.proof','device_id':'lab-stress-0001','signature':'redacted'})
        def send(self, data):
            pass
        def __iter__(self):
            raise RuntimeError('secret-should-not-be-persisted')

    gateway.handler(FakeWebSocket())
    events = gateway.connection_diagnostics()['events']
    names = [row['event'] for row in events]
    assert names.index('gateway_worker_registered') < names.index('gateway_handler_exception')
    assert names.index('gateway_handler_exception') < names.index('gateway_worker_unregistered')
    assert any(row.get('device_id') == 'lab-stress-0001' for row in events)
    assert 'secret-should-not-be-persisted' not in json.dumps(events)
    gateway.scheduler.unregister.assert_called_once()


def test_integrated_loss_report_joins_gateway_and_worker_by_identity():
    client = SimpleNamespace(device_id='lab-stress-0001', last_error='', close_reason='reader_error',
                             is_alive=False, local_adapter_port=54000,
                             transport_diagnostics=lambda: [{'event':'socket_recv_eof','local_port':54000}])
    adapter = SimpleNamespace(diagnostics_for_ports=lambda ports: [{'event':'worker_tcp_recv_eof','local_port':54000}])
    audit = {'events':[{'event':'gateway_worker_unregistered','device_id':'lab-stress-0001'},
                       {'event':'gateway_worker_registered','device_id':'lab-stress-0002'}],
             'total_events':2,'events_retained':2}
    result = _connection_loss_timelines(adapter, [client], {'lab-stress-0001'}, gateway_diagnostics=audit)
    assert len(result['gateway_events']) == 1
    assert result['gateway_events'][0]['event'] == 'gateway_worker_unregistered'
    assert result['gateway_events_total'] == 2


def test_gateway_clean_close_exposes_direction_without_an_exception():
    gateway = gateway_stub()
    ws = SimpleNamespace(remote_address=('127.0.0.1', 55000), close_code=1000,
        protocol=SimpleNamespace(close_rcvd=SimpleNamespace(code=1000),
                                 close_sent=SimpleNamespace(code=1000),
                                 close_rcvd_then_sent=False))
    gateway._note_connection('gateway_receive_loop_finished',
                             device_id='lab-stress-0030', socket=ws, phase='live')
    row = gateway.connection_diagnostics()['events'][-1]
    assert row['received_close_before_sent'] is False
    assert row['received_close_code'] == row['sent_close_code'] == 1000


def test_gateway_filtered_trace_preserves_missing_worker_after_other_events():
    gateway = gateway_stub()
    gateway._connection_events = deque(maxlen=1024)
    gateway._note_connection('gateway_handler_exception', device_id='lab-stress-0042',
                             exc=RuntimeError('private'))
    for i in range(20):
        gateway._note_connection('gateway_worker_registered', device_id=f'lab-stress-{i:04d}')
    result = gateway.connection_diagnostics(device_ids={'lab-stress-0042'})
    assert len(result['events']) == 1
    assert result['events'][0]['device_id'] == 'lab-stress-0042'


def test_marketplace_administrative_status_does_not_exceed_lab_response_budget(tmp_path):
    """At 64 Workers, 100 full-size decisions used to exceed the 128 KiB reader."""
    from main_computer.slh.marketplace_auto import AutomaticMarketplace
    controller = object.__new__(AutomaticMarketplace)
    controller._lock = threading.RLock()
    controller.state_dir = tmp_path
    controller.attempts = 100
    controller.failures = 0
    controller.consecutive_failures = 0
    controller.last_attempt = 123.0
    controller.last_success = 123.0
    controller.last_error = None
    controller.last_result = None
    controller._restart_required = False
    devices = [f'lab-stress-{i:04d}' for i in range(64)]
    controller.decisions = [{'time': i, 'config_revision':1, 'desired':devices,
                             'live':devices, 'blocked':[], 'reason':'policy'} for i in range(100)]
    status = controller.status()
    assert len(status['recent_decisions']) <= 8
    assert len(json.dumps(status).encode('utf-8')) < 131072
