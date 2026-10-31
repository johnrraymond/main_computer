"""Bounded per-stream outgoing writer: one slow Worker must not stall a shared tunnel."""
from __future__ import annotations

import queue
import threading
from typing import Callable


class StreamWriter:
    """FIFO, nonblocking producer and bounded per-stream byte / packet budget.

    A fixed eight-packet buffer closed otherwise healthy Workers on short
    bursts when the socket writer couldn't keep up. Both limits are necessary:
    a packet-only limit punishes tiny TLS records, while a byte-only limit
    permits unbounded queue metadata. Only a persistently overloaded individual
    stream is closed; the shared tunnel reader never blocks on a slow socket.
    """

    def __init__(self, send: Callable[[bytes], None], failed: Callable[[], None],
                 *, max_items: int = 64, max_pending_bytes: int = 2 * 1024 * 1024,
                 name: str = 'seh-stream-writer'):
        if max_items < 1 or max_pending_bytes < 1:
            raise ValueError('StreamWriter budgets must be positive')
        self._send = send
        self._failed = failed
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=max_items)
        self._max_pending_bytes = max_pending_bytes
        self._pending_bytes = 0
        self._state_lock = threading.Lock()
        self._closed = threading.Event()
        # Safe bounded classifications only, never payload or credentials.
        self.failure_reason: str | None = None
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def offer(self, data: bytes) -> bool:
        reason = None
        with self._state_lock:
            if self._closed.is_set():
                return False
            if self._pending_bytes + len(data) > self._max_pending_bytes:
                reason = 'write_byte_budget'
            else:
                try:
                    self._queue.put_nowait(data)
                except queue.Full:
                    reason = 'write_queue_full'
                else:
                    self._pending_bytes += len(data)
                    return True
            self.failure_reason = reason
            self._closed.set()
        self._failed()
        return False

    def close(self) -> None:
        with self._state_lock:
            self._closed.set()

    def _run(self) -> None:
        while not self._closed.is_set():
            try:
                data = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if not self._closed.is_set():
                    self._send(data)
            except Exception:
                with self._state_lock:
                    invoke_failure = not self._closed.is_set()
                    if invoke_failure:
                        self.failure_reason = 'write_error'
                        self._closed.set()
                if invoke_failure:
                    self._failed()
                break
            finally:
                with self._state_lock:
                    self._pending_bytes -= len(data)
                self._queue.task_done()
        self._closed.set()
