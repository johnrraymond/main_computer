from __future__ import annotations

import copy
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        self.stale_after_s = max(self.interval_s * 2.5, self.interval_s + 5.0)
        self._stop_event = threading.Event()
        self._kick_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._status_lock = threading.RLock()
        self._latest_status: dict[str, Any] | None = None
        self._latest_status_at = 0.0
        self._latest_status_at_iso = ""
        self._started_at = ""
        self._stopped_at = ""
        self._last_attempt_at = ""
        self._last_attempt_monotonic = 0.0
        self._last_success_at = ""
        self._last_success_monotonic = 0.0
        self._last_error_at = ""
        self._last_error = ""
        self._last_reason = ""
        self._loop_count = 0
        self._success_count = 0
        self._error_count = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self.kick("already-started")
            return
        self._stop_event.clear()
        self._kick_event.set()
        with self._status_lock:
            self._started_at = _now_iso()
            self._stopped_at = ""
        self._thread = threading.Thread(
            target=self._run,
            name="main-computer-worker-runtime-supervisor",
            daemon=True,
        )
        self._thread.start()

    def ensure_started(self, reason: str = "ensure-started") -> bool:
        thread = self._thread
        if thread is not None and thread.is_alive():
            return False
        signal = getattr(getattr(self.runtime, "server", None), "signal", None)
        if callable(signal):
            signal("worker-runtime-supervisor-autostart", reason=str(reason or "ensure-started"))
        self.start()
        return True

    def stop(self, *, timeout_s: float = 2.0) -> None:
        self._stop_event.set()
        self._kick_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout_s or 0.0)))
        with self._status_lock:
            self._stopped_at = _now_iso()

    def kick(self, reason: str = "kick") -> None:
        signal = getattr(getattr(self.runtime, "server", None), "signal", None)
        if callable(signal):
            signal("worker-runtime-supervisor-kick", reason=str(reason or "kick"))
        self._kick_event.set()

    def update_status(self, status: dict[str, Any]) -> None:
        if not isinstance(status, dict):
            return
        now = time.monotonic()
        now_iso = _now_iso()
        with self._status_lock:
            self._latest_status = copy.deepcopy(status)
            self._latest_status_at = now
            self._latest_status_at_iso = now_iso
            self._last_error = ""

    def diagnostics(self) -> dict[str, Any]:
        thread = self._thread
        now = time.monotonic()
        with self._status_lock:
            last_attempt_age = (now - self._last_attempt_monotonic) if self._last_attempt_monotonic else None
            last_success_age = (now - self._last_success_monotonic) if self._last_success_monotonic else None
            latest_status_age = (now - self._latest_status_at) if self._latest_status_at else None
            return {
                "running": bool(thread and thread.is_alive()),
                "thread_alive": bool(thread and thread.is_alive()),
                "threadAlive": bool(thread and thread.is_alive()),
                "interval_s": self.interval_s,
                "intervalSeconds": self.interval_s,
                "stale_after_s": self.stale_after_s,
                "staleAfterSeconds": self.stale_after_s,
                "started_at": self._started_at,
                "startedAt": self._started_at,
                "stopped_at": self._stopped_at,
                "stoppedAt": self._stopped_at,
                "last_attempt_at": self._last_attempt_at,
                "lastAttemptAt": self._last_attempt_at,
                "last_attempt_age_s": last_attempt_age,
                "lastAttemptAgeSeconds": last_attempt_age,
                "last_success_at": self._last_success_at,
                "lastSuccessAt": self._last_success_at,
                "last_success_age_s": last_success_age,
                "lastSuccessAgeSeconds": last_success_age,
                "last_error_at": self._last_error_at,
                "lastErrorAt": self._last_error_at,
                "last_error": self._last_error,
                "lastError": self._last_error,
                "last_reason": self._last_reason,
                "lastReason": self._last_reason,
                "loop_count": self._loop_count,
                "loopCount": self._loop_count,
                "success_count": self._success_count,
                "successCount": self._success_count,
                "error_count": self._error_count,
                "errorCount": self._error_count,
                "latest_status_at": self._latest_status_at_iso,
                "latestStatusAt": self._latest_status_at_iso,
                "latest_status_age_s": latest_status_age,
                "latestStatusAgeSeconds": latest_status_age,
            }

    def _cached_connected_state_is_stale(self, diagnostics: dict[str, Any]) -> bool:
        last_success_age = diagnostics.get("last_success_age_s")
        if isinstance(last_success_age, (int, float)):
            return float(last_success_age) > self.stale_after_s
        latest_status_age = diagnostics.get("latest_status_age_s")
        if isinstance(latest_status_age, (int, float)):
            return float(latest_status_age) > self.stale_after_s
        return True

    def _with_supervisor_metadata(self, status: dict[str, Any]) -> dict[str, Any]:
        payload = copy.deepcopy(status) if isinstance(status, dict) else {}
        diagnostics = self.diagnostics()
        stale_connected = False

        runtime = payload.get("runtime")
        if not isinstance(runtime, dict):
            runtime = {}
            payload["runtime"] = runtime
        runtime_state = str(runtime.get("state") or "").strip().upper()
        if runtime_state in {"CONNECTED", "ACTIVE"} and self._cached_connected_state_is_stale(diagnostics):
            stale_connected = True
            display = {
                "state": "RECONNECTING",
                "center": "RECONNECTING",
                "tone": "warn",
                "nw": "Cached worker state",
                "ne": "Backend supervisor",
                "sw": "Supervisor stale",
                "se": "Heartbeat pending",
                "foot": "Waiting for backend heartbeat.",
            }
            runtime["state"] = "RECONNECTING"
            runtime["stale"] = True
            runtime["supervisor_stale"] = True
            runtime["supervisorStale"] = True
            runtime["reason"] = "Worker runtime supervisor has not produced a fresh heartbeat."
            runtime["display"] = display
            payload["runtimeDisplay"] = display

        diagnostics["stale"] = stale_connected
        diagnostics["supervisor_stale"] = stale_connected
        diagnostics["supervisorStale"] = stale_connected
        runtime["supervisor"] = diagnostics
        payload["supervisor"] = diagnostics
        return payload

    def status(self, *, ensure_running: bool = True) -> dict[str, Any]:
        if ensure_running:
            self.ensure_started("runtime-status")
        with self._status_lock:
            latest = copy.deepcopy(self._latest_status) if isinstance(self._latest_status, dict) else None
        if isinstance(latest, dict):
            return self._with_supervisor_metadata(latest)
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
        return self._with_supervisor_metadata(status)

    def reconcile_now(self, *, reason: str = "manual", send_heartbeat: bool = True) -> dict[str, Any]:
        now = time.monotonic()
        now_iso = _now_iso()
        with self._status_lock:
            self._last_attempt_at = now_iso
            self._last_attempt_monotonic = now
            self._last_reason = str(reason or "manual")
        status = self.runtime.reconcile_worker_runtime(reason=reason, send_heartbeat=send_heartbeat)
        self.update_status(status)
        success_iso = _now_iso()
        with self._status_lock:
            self._last_success_at = success_iso
            self._last_success_monotonic = time.monotonic()
            self._success_count += 1
            self._last_error = ""
        return self._with_supervisor_metadata(status)

    def _run(self) -> None:
        signal = getattr(getattr(self.runtime, "server", None), "signal", None)
        if callable(signal):
            signal("worker-runtime-supervisor-start", interval_s=self.interval_s)
        next_reason = "startup"
        while not self._stop_event.is_set():
            self._kick_event.clear()
            with self._status_lock:
                self._loop_count += 1
            try:
                status = self.reconcile_now(reason=next_reason, send_heartbeat=True)
                runtime = status.get("runtime") if isinstance(status.get("runtime"), dict) else {}
                if callable(signal):
                    signal(
                        "worker-runtime-supervisor-reconcile",
                        reason=next_reason,
                        state=runtime.get("state"),
                        phase=runtime.get("phase"),
                        loop_count=self._loop_count,
                    )
            except Exception as exc:
                error_iso = _now_iso()
                with self._status_lock:
                    self._last_error = str(exc)
                    self._last_error_at = error_iso
                    self._error_count += 1
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
