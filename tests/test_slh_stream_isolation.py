"""A bad or over-capacity Worker stream must not take down SLH's SEH tunnel."""
import base64
import json
import threading
from collections import deque
from unittest.mock import Mock

from main_computer.slh.seh_transport import SLHSEHConnector
from main_computer.seh.relay import SEHRelay
from main_computer.seh.stream_writer import StreamWriter
import main_computer.seh.relay as relay_module


def _connector():
    obj = object.__new__(SLHSEHConnector)
    obj._lock = threading.RLock()
    obj._streams = {}
    obj._stream_writers = {}
    obj._stream_events = deque(maxlen=2048)
    obj.gateway_port = 12345
    obj._running = threading.Event()
    obj._running.set()
    sent = []
    obj._send = sent.append
    return obj, sent


def test_full_connector_refuses_only_extra_stream():
    obj, sent = _connector()
    obj._streams = {f'worker-{i}': Mock() for i in range(64)}
    obj._handle({'op': 'open', 'id': 'worker-64'})
    assert len(obj._streams) == 64
    assert obj._running.is_set()
    assert sent == [{'op': 'close', 'id': 'worker-64'}]


def test_connector_local_connect_failure_closes_only_that_stream(monkeypatch):
    obj, sent = _connector()
    existing = Mock()
    obj._streams = {'healthy': existing}
    def reject(*_args, **_kwargs):
        raise ConnectionRefusedError('temporary local refusal')
    monkeypatch.setattr('main_computer.slh.seh_transport.socket.create_connection', reject)
    obj._handle({'op': 'open', 'id': 'bad'})
    assert obj._running.is_set()
    assert obj._streams == {'healthy': existing}
    assert sent == [{'op':'close','id':'bad'}]


def test_connector_one_send_failure_does_not_disrupt_other_streams():
    obj, sent = _connector()
    bad = Mock()
    bad.sendall.side_effect = ConnectionResetError('worker left')
    bad.getsockname.return_value = ('127.0.0.1', 41001)
    healthy = Mock()
    healthy.getsockname.return_value = ('127.0.0.1', 41002)
    obj._streams = {'bad': bad, 'healthy': healthy}
    obj._stream_writers = {
        'bad': StreamWriter(bad.sendall, lambda: obj._local_write_failed('bad')),
        'healthy': StreamWriter(healthy.sendall, lambda: obj._local_write_failed('healthy')),
    }
    obj._handle({'op': 'data', 'id': 'bad', 'data': base64.b64encode(b'hello').decode()})
    import time
    deadline=time.monotonic()+1
    while 'bad' in obj._streams and time.monotonic()<deadline:
        time.sleep(.01)
    assert obj._running.is_set()
    assert 'bad' not in obj._streams and obj._streams['healthy'] is healthy
    assert {'op':'close','id':'bad','cause':'write_error'} in sent
    obj._handle({'op':'data','id':'healthy', 'data':base64.b64encode(b'work').decode()})
    deadline=time.monotonic()+1
    while not healthy.sendall.called and time.monotonic()<deadline:
        time.sleep(.01)
    healthy.sendall.assert_called_once_with(b'work')


def test_relay_single_failed_client_send_keeps_other_clients(monkeypatch):
    monkeypatch.setattr(relay_module, 'verify_object', lambda *_: True)
    relay = object.__new__(SEHRelay)
    relay.organization_id = 'lab-org'
    relay.slh_id = 'lab-slh'
    relay.slh_public_key = 'fixture-public-key'
    relay._lock = threading.RLock()
    relay._send_lock = threading.RLock()
    relay._closing_clients = set()
    relay._pending_clients = {}
    relay._stream_events = deque(maxlen=2048)
    relay.slh_close_causes = {}
    relay._slh = None
    relay._lab_slhs = {}
    relay._lab_clients = {}
    bad = Mock()
    bad.send.side_effect = BrokenPipeError('one failed socket')
    healthy = Mock()
    relay._clients = {'broken':bad,'healthy':healthy}
    relay._client_writers = {
        (None,'broken'):StreamWriter(bad.send, lambda:relay._schedule_client_close(bad,code=1011,reason='STREAM_WRITE_FAILED')),
        (None,'healthy'):StreamWriter(healthy.send, lambda:relay._schedule_client_close(healthy,code=1011,reason='STREAM_WRITE_FAILED')),
    }
    relay.bytes_forwarded = 0
    relay.authenticated_slh_count = 0
    class Tunnel:
        def __init__(self): self.sent=[]; self.receives=0
        def send(self, message): self.sent.append(json.loads(message))
        def recv(self, timeout=None):
            self.receives += 1
            return json.dumps({'type':'seh.auth.proof','signature':'fixture'})
        def __iter__(self):
            for sid in ('broken','healthy'):
                yield json.dumps({'op':'data','id':sid,
                                  'data':base64.b64encode(b'ok').decode()})
            import time
            deadline=time.monotonic()+1
            while (not bad.close.called or not healthy.send.called) and time.monotonic()<deadline:
                time.sleep(.01)
    ws=Tunnel()
    relay._handle_slh(ws)
    assert ws.receives == 1
    import time
    deadline = time.monotonic()+1
    while (not bad.close.called or not healthy.send.called) and time.monotonic() < deadline:
        time.sleep(.01)
    healthy.send.assert_called_once_with(b'ok')
    bad.close.assert_any_call(code=1011, reason='STREAM_WRITE_FAILED')
    # This isolated mock counts offered bytes; production increments only
    # after the per-stream sender has successfully written them.
    assert relay.bytes_forwarded == 0


def test_slow_client_close_cannot_block_following_tunnel_frames(monkeypatch):
    """A slow Worker close must not freeze unrelated multiplexed Workers."""
    import time
    monkeypatch.setattr(relay_module, 'verify_object', lambda *_: True)
    relay = object.__new__(SEHRelay)
    relay.organization_id, relay.slh_id = 'lab-org', 'lab-slh'
    relay.slh_public_key = 'fixture-public-key'
    relay._lock = threading.RLock()
    relay._send_lock = threading.RLock()
    relay._closing_clients = set()
    relay._pending_clients = {}
    relay._stream_events = deque(maxlen=2048)
    relay.slh_close_causes = {}
    relay._slh = None
    relay._lab_slhs = {}
    relay._lab_clients = {}
    slow = Mock()
    started = threading.Event()
    release = threading.Event()
    def wait_to_close(*, code, reason=''):
        started.set()
        release.wait(timeout=2)
    slow.close.side_effect = wait_to_close
    healthy = Mock()
    relay._clients = {'slow':slow,'healthy':healthy}
    relay._client_writers={(None,'healthy'): StreamWriter(healthy.send,lambda:None)}
    relay.bytes_forwarded = 0
    relay.authenticated_slh_count = 0

    class Tunnel:
        def send(self, message): pass
        def recv(self, timeout=None): return json.dumps({'type':'seh.auth.proof','signature':'fixture'})
        def __iter__(self):
            yield json.dumps({'op':'close','id':'slow'})
            assert started.wait(timeout=.5)
            yield json.dumps({'op':'data','id':'healthy',
                'data':base64.b64encode(b'still-live').decode()})
    began = time.monotonic()
    try:
        relay._handle_slh(Tunnel())
        assert time.monotonic()-began < .5
        deadline=time.monotonic()+1
        while not healthy.send.called and time.monotonic()<deadline:
            time.sleep(.01)
        healthy.send.assert_called_once_with(b'still-live')
    finally:
        release.set()


def test_relay_marks_slh_buffer_exhaustion_as_1013_without_tunnel_failure(monkeypatch):
    """An overloaded Worker stream must not masquerade as a normal close."""
    monkeypatch.setattr(relay_module, 'verify_object', lambda *_: True)
    relay = object.__new__(SEHRelay)
    relay.organization_id = 'lab-org'
    relay.slh_id = 'lab-slh'
    relay.slh_public_key = 'fixture-public-key'
    relay._lock = threading.RLock()
    relay._send_lock = threading.RLock()
    relay._closing_clients = set()
    relay._pending_clients = {}
    relay._stream_events = deque(maxlen=2048)
    relay.slh_close_causes = {}
    relay._slh = None
    relay._lab_slhs = {}
    relay._lab_clients = {}
    failed = Mock()
    healthy = Mock()
    relay._clients = {'failed':failed,'healthy':healthy}
    relay._client_writers = {}
    relay.authenticated_slh_count = 0

    class Tunnel:
        def send(self, _message): pass
        def recv(self, timeout=None):
            return json.dumps({'type':'seh.auth.proof','signature':'fixture'})
        def __iter__(self):
            yield json.dumps({'op':'close','id':'failed','cause':'write_queue_full'})
            # A failed stream must not prevent the next event.
            yield json.dumps({'op':'close','id':'healthy','cause':'local_eof'})

    relay._handle_slh(Tunnel())
    import time
    deadline = time.monotonic() + 1
    while (not failed.close.called or not healthy.close.called) and time.monotonic() < deadline:
        time.sleep(.01)
    failed.close.assert_any_call(code=1013, reason='')
    healthy.close.assert_any_call(code=1000, reason='')
    assert relay.slh_close_causes == {'write_queue_full': 1, 'local_eof': 1}
