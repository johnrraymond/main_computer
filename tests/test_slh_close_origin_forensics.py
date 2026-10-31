"""Regression tests ensure logs diagnose origins without claiming EOF is causation."""
import json
import inspect
import threading
from types import SimpleNamespace

from tools.slh_seh_lab.connection_loss_attribution import summarize_loss_evidence
from main_computer.slh.managed_client import ManagedSLHWorkerClient
from main_computer.slh.seh_transport import SLHSEHConnector


def test_eof_before_adapter_cleanup_is_not_a_close_origin():
    timeline = {'workers': {'one': {'worker_transport_events': [
        {'event': 'socket_recv_eof', 'monotonic_ns': 100}] }},
        'adapter_events': [
            {'event':'worker_tcp_recv_eof', 'monotonic_ns':101},
            {'event':'adapter_initiating_websocket_close','monotonic_ns':102}],
        'gateway_events': [
            {'event':'gateway_handler_exception','monotonic_ns':103,'exception_class':'ConnectionClosedError'}]}
    result = summarize_loss_evidence(timeline)
    assert result['status'] == 'ORIGIN_NOT_ESTABLISHED'
    assert result['candidate_prior_close'] is None
    assert result['first_explicit_close']['component'] == 'adapter'
    assert result['first_observed_failure']['event'] == 'socket_recv_eof'


def test_prior_explicit_close_is_candidate_not_proof():
    rows = {'slh_gateway_stream_events': [
        {'event':'slh_local_socket_shutdown_invoked','monotonic_ns':90,
         'cause':'seh_close_instruction', 'stream_tag':'secret-stream'}],
        'workers': {'one': {'worker_transport_events':[{'event':'socket_recv_eof','monotonic_ns':100}]}}}
    result = summarize_loss_evidence(rows)
    assert result['status'] == 'EXPLICIT_CLOSE_PRECEDED_OBSERVED_FAILURE'
    assert result['candidate_prior_close']['component']=='slh'
    assert 'secret-stream' not in json.dumps(result)


def test_worker_event_trace_redacts_exception_message():
    client=object.__new__(ManagedSLHWorkerClient)
    client._transport_trace_lock=threading.Lock()
    from collections import deque
    client._transport_trace=deque(maxlen=48)
    client.local_adapter_port=53125
    client._record_transport_event('websocket_send_failed', exc=OSError(32, 'TOKEN=supersecret'))
    out=json.dumps(client.transport_diagnostics())
    assert 'supersecret' not in out and 'TOKEN' not in out
    assert 'exception_class' in out


def test_sl_h_close_stream_records_shutdown_intention_before_close():
    from collections import deque
    conn=object.__new__(SLHSEHConnector)
    conn._lock=threading.RLock()
    conn._stream_events=deque(maxlen=10)
    conn._streams={}
    conn._stream_writers={}
    class Socket:
        def getsockname(self): return ('127.0.0.1', 49322)
        def shutdown(self,how): pass
        def close(self): pass
    sock=Socket()
    conn._streams['opaque-session']=sock
    assert conn._close_stream('opaque-session',initiator='seh_close_instruction')
    rows=conn.diagnostics_for_gateway_ports({49322})
    assert any(e['event']=='slh_local_socket_shutdown_invoked' and
               e['cause']=='seh_close_instruction' for e in rows)
    assert 'opaque-session' not in json.dumps(rows)


def test_harness_recollects_gateway_after_failed_preliminary_job():
    from tools.slh_seh_lab import real_paid_service
    src=inspect.getsource(real_paid_service.verify_real_process_paid)
    assert 'failed_preliminary_paid_job_audit' in src
    assert 'relay=relay' in src
