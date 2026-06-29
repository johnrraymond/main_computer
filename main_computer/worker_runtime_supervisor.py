from __future__ import annotations

import os
import threading
from typing import Any


class WorkerRuntimeSupervisor:
    """Backend-owned worker runtime reconciler.

    The Worker page may still issue a manual runtime-sync, but this supervisor is
    the daemon-like owner that restores the worker live session after viewport
    startup and periodically refreshes heartbeat/session state.
    """

    def __init__(self, runtime: Any, *, interval_s: float | None = None) -> None:
        self.runtime = runtime
        raw_interval = interval_s
        if raw_interval is None:
            try:
                raw_interval = float(os.environ.get("MAIN_COMPUTER_WORKER_RUNTIME_SUPERVISOR_INTERVAL_SECONDS", "10"))
            except (TypeError, ValueError):
                raw_interval = 10.0
        self.interval_s = max(1.0, float(raw_interval or 10.0))
        self._stop_event = threading.Event()
        self._kick_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._status_lock = threading.RLock()
        self._latest_status: dict[str, Any] | None = None
        self._latest_error = ""

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self.kick("already-started")
            return
        self._stop_event.clear()
        self._kick_event.set()
        self._thread = threading.Thread(
            target=self._run,
            name="main-computer-worker-runtime-supervisor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout_s: float = 2.0) -> None:
        self._stop_event.set()
        self._kick_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout_s or 0.0)))

    def kick(self, reason: str = "kick") -> None:
        signal = getattr(getattr(self.runtime, "server", None), "signal", None)
        if callable(signal):
            signal("worker-runtime-supervisor-kick", reason=str(reason or "kick"))
        self._kick_event.set()

    def update_status(self, status: dict[str, Any]) -> None:
        if not isinstance(status, dict):
            return
        with self._status_lock:
            self._latest_status = status
            self._latest_error = ""

    def status(self) -> dict[str, Any]:
        with self._status_lock:
            latest = self._latest_status
        if isinstance(latest, dict):
            return latest
        try:
            status = self.runtime.read_worker_runtime_status()
        except Exception as exc:
            status = {
                "ok": False,
                "error": str(exc),
                "autoConnect": {"network": "none", "enabled": False},
                "runtime": {
                    "state": "FAILED",
                    "phase": "not_accepting",
                    "enabled": False,
                    "allowed_to_accept": False,
                    "allowedToAccept": False,
                    "reason": str(exc),
                    "display": {
                        "state": "FAILED",
                        "center": "FAILED",
                        "tone": "bad",
                        "nw": "Status unavailable",
                        "ne": "Worker runtime",
                        "sw": "Read failed",
                        "se": "Retry scheduled",
                        "foot": "Check server logs.",
                    },
                },
                "runtimeDisplay": {
                    "state": "FAILED",
                    "center": "FAILED",
                    "tone": "bad",
                    "nw": "Status unavailable",
                    "ne": "Worker runtime",
                    "sw": "Read failed",
                    "se": "Retry scheduled",
                    "foot": "Check server logs.",
                },
            }
        self.update_status(status)
        return status

    def reconcile_now(self, *, reason: str = "manual", send_heartbeat: bool = True) -> dict[str, Any]:
        status = self.runtime.reconcile_worker_runtime(reason=reason, send_heartbeat=send_heartbeat)
        self.update_status(status)
        return status

    def _run(self) -> None:
        signal = getattr(getattr(self.runtime, "server", None), "signal", None)
        if callable(signal):
            signal("worker-runtime-supervisor-start", interval_s=self.interval_s)
        next_reason = "startup"
        while not self._stop_event.is_set():
            self._kick_event.clear()
            try:
                self.reconcile_now(reason=next_reason, send_heartbeat=True)
            except Exception as exc:
                self._latest_error = str(exc)
                if callable(signal):
                    signal("worker-runtime-supervisor-error", error=exc)
                try:
                    self.update_status(self.runtime.read_worker_runtime_status())
                except Exception:
                    pass
            next_reason = "periodic"
            self._kick_event.wait(self.interval_s)
        if callable(signal):
            signal("worker-runtime-supervisor-stop")
