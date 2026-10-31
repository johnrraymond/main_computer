"""Slow or failed stream output never blocks other multiplexed clients."""
import threading
import time

from main_computer.seh.stream_writer import StreamWriter


def test_stalled_send_does_not_stall_other_worker():
    slow_started=threading.Event()
    release=threading.Event()
    fast_done=threading.Event()

    def slow_send(_data):
        slow_started.set()
        assert release.wait(2)

    slow=StreamWriter(slow_send, lambda: None)
    fast=StreamWriter(lambda data: fast_done.set(), lambda: None)
    try:
        assert slow.offer(b'blocked')
        assert slow_started.wait(1)
        assert fast.offer(b'healthy')
        assert fast_done.wait(1)
    finally:
        release.set()
        slow.close()
        fast.close()


def test_one_stream_backpressure_fails_closed_without_affecting_other_stream():
    stalled=threading.Event()
    release=threading.Event()
    failures=[]
    worker=StreamWriter(lambda data: (stalled.set(), release.wait(2)),
                        lambda: failures.append('overloaded'), max_items=1)
    try:
        assert worker.offer(b'1')
        assert stalled.wait(1)
        assert worker.offer(b'2')
        assert not worker.offer(b'3')
        assert failures == ['overloaded']
    finally:
        release.set()
        worker.close()


def test_write_error_is_stream_scoped():
    failure=threading.Event()
    def fail(data):
        raise ConnectionResetError('client was closed')
    worker=StreamWriter(fail, failure.set)
    assert worker.offer(b'one')
    assert failure.wait(1)
    assert not worker.offer(b'two')
    worker.close()


def test_short_burst_of_small_tls_chunks_is_buffered_without_disconnect():
    """The previous eight-packet FIFO falsely dropped responsive Workers."""
    started = threading.Event()
    release = threading.Event()
    received = []
    failures = []

    def send(data):
        started.set()
        assert release.wait(2)
        received.append(data)

    writer = StreamWriter(send, lambda: failures.append('failed'))
    try:
        assert writer.offer(b'000')
        assert started.wait(1)
        for index in range(1, 33):
            assert writer.offer(f'{index:03d}'.encode())
        assert not failures
        release.set()
        deadline = time.monotonic() + 3
        while len(received) < 33 and time.monotonic() < deadline:
            time.sleep(.01)
        assert received == [f'{i:03d}'.encode() for i in range(33)]
    finally:
        release.set()
        writer.close()


def test_byte_budget_limits_memory_even_for_large_packets():
    started = threading.Event()
    release = threading.Event()
    failures = []

    def stalled(_data):
        started.set()
        assert release.wait(2)

    writer = StreamWriter(stalled, lambda: failures.append('failed'),
                          max_items=100, max_pending_bytes=24)
    try:
        assert writer.offer(b'a' * 8)
        assert started.wait(1)
        assert writer.offer(b'b' * 16)
        assert not writer.offer(b'c' * 8)
        assert failures == ['failed']
        assert writer.failure_reason == 'write_byte_budget'
    finally:
        release.set()
        writer.close()
