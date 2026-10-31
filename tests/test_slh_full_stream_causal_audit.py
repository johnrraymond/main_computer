"""Cross-boundary stream close evidence: causal ordering, not generic EOF counts."""
from collections import deque
import json
import threading
from types import SimpleNamespace

from main_computer.seh.relay import SEHRelay
from main_computer.slh.seh_transport import SLHSEHConnector, SEHLocalClientAdapter
from tools.slh_seh_lab.integrated_stress import _connection_loss_timelines


def test_relay_audit_follows_same_stream_from_admission_to_close():
    relay = object.__new__(SEHRelay)
    relay._lock = threading.RLock()
    relay._stream_events = deque(maxlen=4)
    ws = SimpleNamespace(remote_address=('127.0.0.1', 55020), protocol=SimpleNamespace(
        close_rcvd=SimpleNamespace(code=1000), close_sent=SimpleNamespace(code=1000),
        close_rcvd_then_sent=True))
    relay._stream_event('relay_client_accepted', 'sid-secret', ws=ws)
    relay._stream_event('relay_slh_requested_close', 'sid-secret', cause='local_eof')
    trace = relay.diagnostics_for_relay_ports({55020})
    assert [row['event'] for row in trace] == ['relay_client_accepted', 'relay_slh_requested_close']
    assert trace[0]['received_close_before_sent'] is True
    assert trace[1]['cause'] == 'local_eof'
    assert 'sid-secret' not in json.dumps(trace)
    assert '127.0.0.1' not in json.dumps(trace)


def test_connector_audit_matches_gateway_peer_port_and_retains_closure():
    conn = object.__new__(SLHSEHConnector)
    conn._lock = threading.RLock()
    conn._stream_events = deque(maxlen=16)
    local = SimpleNamespace(getsockname=lambda: ('127.0.0.1', 54021))
    conn._stream_event('slh_gateway_stream_opened', 'stream-secret', local=local)
    conn._stream_event('slh_origin_close_instruction', 'stream-secret', cause='local_eof')
    events = conn.diagnostics_for_gateway_ports({54021})
    assert len(events) == 2
    assert events[0]['stream_tag'] == events[1]['stream_tag']
    assert events[1]['cause'] == 'local_eof'
    assert 'stream-secret' not in json.dumps(events)


def test_stress_failure_report_stitches_relay_and_gateway_events():
    adapter = SimpleNamespace(
        diagnostics_for_ports=lambda ports:[{'event':'worker_tcp_recv_eof','local_port':53001}],
        relay_ports_for_worker_ports=lambda ports:{55021})
    relay = SimpleNamespace(diagnostics_for_relay_ports=lambda ports:[
        {'event':'relay_client_handler_exiting','relay_peer_port':55021,
         'received_close_before_sent':True}])
    client = SimpleNamespace(device_id='stress-1',is_alive=False,close_reason='reader_error',last_error='',
        local_adapter_port=53001, transport_diagnostics=lambda:[])
    gateway = {'events':[{'event':'gateway_handler_exception','device_id':'stress-1'}],
               'seh_gateway_stream_events':[{'event':'slh_received_seh_close_instruction'}]}
    result = _connection_loss_timelines(adapter,[client],{'stress-1'},
        gateway_diagnostics=gateway,relay=relay)
    assert result['seh_relay_stream_events'][0]['received_close_before_sent'] is True
    assert result['slh_gateway_stream_events'][0]['event']=='slh_received_seh_close_instruction'


def test_tunnel_supervisor_retains_events_from_retired_connector():
    from main_computer.slh.seh_autoconnect import SEHAutoConnection
    sup = object.__new__(SEHAutoConnection)
    sup._lock = threading.RLock()
    sup._retired_stream_events = deque(maxlen=4096)
    sup._tunnel_lifecycle = deque(maxlen=256)
    sup._current = None
    sup.connections = 2
    sup.failures = 1
    sup.last_error = 'ConnectionClosedError'
    sup._retired_stream_events.extend([
        {'event':'slh_gateway_stream_opened','stream_tag':'abcdef','gateway_peer_port':54021},
        {'event':'slh_gateway_tcp_eof','stream_tag':'abcdef'}])
    sup._tunnel_lifecycle.append({'event':'seh_tunnel_disconnected','error_class':'ConnectionClosedError'})
    witness = sup.stream_diagnostics_for_gateway_ports({54021})
    assert len(witness['events']) == 2
    assert witness['events'][1]['event'] == 'slh_gateway_tcp_eof'
    assert witness['connections'] == 2
    assert witness['tunnel_lifecycle'][0]['event'] == 'seh_tunnel_disconnected'


def test_tunnel_supervisor_filters_other_worker_streams():
    from main_computer.slh.seh_autoconnect import SEHAutoConnection
    sup = object.__new__(SEHAutoConnection)
    sup._lock = threading.RLock()
    sup._retired_stream_events = deque(maxlen=4096)
    sup._tunnel_lifecycle = deque(maxlen=256)
    sup._current = None
    sup.connections = sup.failures = 0
    sup.last_error = None
    sup._retired_stream_events.extend([
        {'event':'open','stream_tag':'wanted','gateway_peer_port':54021},
        {'event':'close','stream_tag':'wanted'},
        {'event':'open','stream_tag':'other','gateway_peer_port':54022},
        {'event':'close','stream_tag':'other'}])
    witness = sup.stream_diagnostics_for_gateway_ports({54021})
    assert [e['event'] for e in witness['events']] == ['open','close']
