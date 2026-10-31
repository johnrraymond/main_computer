"""Connection-cause evidence must be bounded, correlated and secret-free."""
from collections import deque
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from main_computer.slh.managed_client import ManagedSLHWorkerClient
from main_computer.slh.seh_transport import SEHLocalClientAdapter
from main_computer.viewport_routes_energy import _WorkerHubLiveSessionClient
from tools.slh_seh_lab.integrated_stress import _connection_loss_timelines


def fake_worker():
    worker = object.__new__(ManagedSLHWorkerClient)
    worker.device_id = 'lab-stress-0042'
    worker.local_adapter_port = 53214
    worker._transport_trace_lock = threading.Lock()
    worker._transport_trace = deque(maxlen=48)
    worker.last_error = ''
    worker.close_reason = 'reader_error'
    worker._stop_event = threading.Event()
    worker._state_lock = threading.RLock()
    worker._reader_thread = None
    worker._socket = None
    worker.closed_at = ''
    return worker


def test_worker_exception_trace_is_structured_redacted_and_bounded():
    client = fake_worker()
    secret = 'Bearer secret-that-must-not-enter-any-report'
    for _ in range(60):
        client._record_transport_event('reader_exception', exc=ConnectionError(secret))
    events = client.transport_diagnostics()
    assert len(events) == 48
    assert all(row['exception_class'] == 'ConnectionError' for row in events)
    assert all(row['local_port'] == 53214 for row in events)
    assert all(isinstance(row['unix_ns'], int) for row in events)
    assert secret not in str(events)


def test_reader_loop_records_exception_before_connection_teardown():
    worker = fake_worker()
    worker._socket = Mock()
    worker._recv_json = lambda: (_ for _ in ()).throw(ConnectionResetError(10054, 'private-data'))
    _WorkerHubLiveSessionClient._reader_loop(worker)
    assert worker.transport_diagnostics()[0]['event'] == 'reader_exception'
    assert worker.transport_diagnostics()[0]['exception_class'] == 'ConnectionResetError'
    assert worker.close_reason == 'reader_error'
    assert worker._stop_event.is_set()
    assert 'private-data' not in str(worker.transport_diagnostics())


def test_adapter_and_worker_port_correlation_excludes_other_connections():
    worker = fake_worker()
    worker._record_transport_event('socket_connected')
    adapter = SimpleNamespace(diagnostics_for_ports=lambda ports: [
        {'event':'adapter_tcp_accepted', 'local_port':53214, 'unix_ns':1},
        {'event':'worker_tcp_recv_eof', 'local_port':53214, 'unix_ns':2},
    ] if ports == {53214} else [])
    result = _connection_loss_timelines(adapter, [worker], {'lab-stress-0042'})
    assert result['workers']['lab-stress-0042']['local_adapter_port'] == 53214
    assert len(result['adapter_events']) == 2
    assert result['adapter_events'][1]['event'] == 'worker_tcp_recv_eof'


def test_missing_worker_without_transport_hook_is_still_reported():
    class OldClient:
        device_id='lab-old'
        last_error='RuntimeError secret=private'
        close_reason='reader_error'
        is_alive=False
    result = _connection_loss_timelines(SimpleNamespace(), [OldClient()], {'lab-old'})
    assert result['workers']['lab-old']['tracked']
    assert result['adapter_events'] == []
    assert 'private' not in str(result)


def test_diagnostic_hook_failure_does_not_close_worker():
    worker = object.__new__(_WorkerHubLiveSessionClient)
    worker._record_transport_event = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('logging broken'))
    worker._diagnostic_transport_event('reader_exception', exc=RuntimeError('secret'))


def test_metadata_never_serializes_socket_addresses_or_work_payloads():
    worker = fake_worker()
    exc = OSError(10054, 'user-supplied-password=secret')
    worker._record_transport_event('reader_exception', exc=exc)
    record = worker.transport_diagnostics()[-1]
    assert record['errno'] == 10054
    assert 'password' not in str(record)
    assert set(record) <= {'event', 'unix_ns', 'monotonic_ns', 'local_port',
                           'exception_class', 'errno', 'ssl_reason', 'websocket_close_code'}
