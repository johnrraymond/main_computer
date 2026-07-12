from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import signal
import sys
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from main_computer.main_log_codec import LexLogWriter, canonical_json_line
from main_computer.log_surprise_compressor import LogSurpriseCompressor
from main_computer.main_log_client import (
    DEFAULT_MAIN_LOG_HOST,
    DEFAULT_MAIN_LOG_PORT,
    ENV_MAIN_LOG_HOST,
    ENV_MAIN_LOG_PORT,
    ENV_MAIN_LOG_URL,
)


SERVICE_NAME = "main-computer-main-log-service"
MAIN_LOG_SERVICE_PID_FILENAME = ".main_computer_main_log_service.pid"
DEFAULT_RECENT_LIMIT = 200
MAX_EVENT_BYTES = 256 * 1024
ENV_MAIN_LOG_RAW_MIRROR = "MAIN_COMPUTER_MAIN_LOG_RAW_MIRROR"


def _truthy_env(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_port(value: object, *, fallback: int = DEFAULT_MAIN_LOG_PORT) -> int:
    try:
        port = int(str(value or "").strip())
    except (TypeError, ValueError):
        return fallback
    if 1 <= port <= 65535:
        return port
    return fallback


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class MainLogStore:
    def __init__(self, *, root: Path | str, recent_limit: int = DEFAULT_RECENT_LIMIT) -> None:
        self.root = Path(root).resolve()
        self.runtime_dir = self.root / "runtime" / "main_log"
        self.log_path = self.runtime_dir / "main.log.lex"
        self.raw_log_path = self.runtime_dir / "main.log.jsonl"
        self.surprise_path = self.runtime_dir / "main.log.surprise.json"
        self.surprise_compressor = LogSurpriseCompressor()
        self.raw_mirror = _truthy_env(ENV_MAIN_LOG_RAW_MIRROR)
        self.state_path = self.runtime_dir / "state.json"
        self.pid_path = self.root / MAIN_LOG_SERVICE_PID_FILENAME
        self.recent_limit = max(1, int(recent_limit))
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._recent: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._seq = 0
        self._stop_event = threading.Event()
        self._writer = threading.Thread(target=self._writer_loop, name="main-log-writer", daemon=True)

    def start(self, *, host: str, port: int) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "service": SERVICE_NAME,
            "pid": os.getpid(),
            "root": str(self.root),
            "state": "starting",
            "ok": False,
            "host": host,
            "port": int(port),
            "url": f"http://{host}:{int(port)}",
            "log_path": str(self.log_path),
            "log_format": "mclog-lex-v1",
            "raw_log_path": str(self.raw_log_path),
            "raw_mirror": self.raw_mirror,
            "state_path": str(self.state_path),
            "surprise_path": str(self.surprise_path),
            "pid_file": str(self.pid_path),
            "updated_at": _now_iso(),
        }
        self.state_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.pid_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._writer.start()

    def mark_ready(self, *, host: str, port: int) -> None:
        self._write_state(state="ready", ok=True, host=host, port=port)

    def _write_state(self, *, state: str, ok: bool, host: str, port: int, message: str = "") -> None:
        payload = {
            "schema_version": 1,
            "service": SERVICE_NAME,
            "pid": os.getpid(),
            "root": str(self.root),
            "state": state,
            "ok": bool(ok),
            "host": host,
            "port": int(port),
            "url": f"http://{host}:{int(port)}",
            "log_path": str(self.log_path),
            "log_format": "mclog-lex-v1",
            "raw_log_path": str(self.raw_log_path),
            "raw_mirror": self.raw_mirror,
            "state_path": str(self.state_path),
            "surprise_path": str(self.surprise_path),
            "pid_file": str(self.pid_path),
            "message": message,
            "updated_at": _now_iso(),
        }
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.pid_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def append_many(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        accepted = 0
        for event in events:
            if not isinstance(event, dict):
                continue
            sanitized = self._sanitize_event(event)
            self._queue.put(sanitized)
            accepted += 1
        return {"ok": True, "state": "accepted", "accepted": accepted}

    def _sanitize_event(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = dict(event)
        payload.setdefault("schema_version", 1)
        payload.setdefault("at", _now_iso())
        payload.setdefault("received_at", _now_iso())
        with self._lock:
            self._seq += 1
            payload["ingest_seq"] = self._seq

        # Bound pathological messages so logging can not become an unbounded
        # memory/disk amplification path.
        encoded_size = len(json.dumps(payload, sort_keys=True, default=str).encode("utf-8", errors="replace"))
        if encoded_size > MAX_EVENT_BYTES:
            message = str(payload.get("message") or payload.get("chunk") or "")
            payload["message"] = message[:8192] + "...[main-log-truncated]"
            payload["truncated_by_main_log"] = True
        return payload

    def _writer_loop(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        raw_handle = None
        try:
            if self.raw_mirror:
                raw_handle = self.raw_log_path.open("a", encoding="utf-8")
            with LexLogWriter(self.log_path) as lex_writer:
                while True:
                    item = self._queue.get()
                    if item is None:
                        self._queue.task_done()
                        break
                    try:
                        lex_writer.write_record(item)
                        if raw_handle is not None:
                            raw_handle.write(canonical_json_line(item) + "\n")
                            raw_handle.flush()
                        surprise_record = self.surprise_compressor.observe(item)
                        if self.surprise_compressor.should_flush():
                            self.surprise_compressor.write_snapshot(self.surprise_path, limit=self.recent_limit)
                            self.surprise_compressor.mark_flushed()
                        with self._lock:
                            item["_main_log_surprise_bits"] = surprise_record["surprise_bits"]
                            item["_main_log_signature_hash"] = surprise_record["signature_hash"]
                            self._recent.append(item)
                            if len(self._recent) > self.recent_limit:
                                del self._recent[: len(self._recent) - self.recent_limit]
                    finally:
                        self._queue.task_done()
        finally:
            if raw_handle is not None:
                raw_handle.close()

    def recent(self, *, limit: int = DEFAULT_RECENT_LIMIT) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._recent)
        return items[-max(1, int(limit)):]

    def surprise_snapshot(self, *, limit: int = DEFAULT_RECENT_LIMIT) -> dict[str, Any]:
        return self.surprise_compressor.snapshot(limit=max(1, int(limit)))

    def stop(self, *, host: str, port: int) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        self._write_state(state="stopping", ok=False, host=host, port=port)
        self._queue.put(None)
        self._writer.join(timeout=5.0)
        self._write_state(state="stopped", ok=False, host=host, port=port)
        try:
            current = json.loads(self.pid_path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
        if current.get("pid") == os.getpid():
            try:
                self.pid_path.unlink()
            except OSError:
                pass


class MainLogHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], RequestHandlerClass: type[BaseHTTPRequestHandler], store: MainLogStore) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.store = store


class MainLogRequestHandler(BaseHTTPRequestHandler):
    server: MainLogHTTPServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "state": "ready",
                    "service": SERVICE_NAME,
                    "pid": os.getpid(),
                    "root": str(self.server.store.root),
                    "log_path": str(self.server.store.log_path),
                    "log_format": "mclog-lex-v1",
                    "raw_log_path": str(self.server.store.raw_log_path),
                    "raw_mirror": self.server.store.raw_mirror,
                    "surprise_path": str(self.server.store.surprise_path),
                    "at": _now_iso(),
                },
            )
            return
        if parsed.path == "/v1/log/recent":
            limit = DEFAULT_RECENT_LIMIT
            query = parse_qs(parsed.query or "")
            if query.get("limit"):
                limit = _coerce_port(query["limit"][0], fallback=DEFAULT_RECENT_LIMIT)
            _json_response(self, 200, {"ok": True, "events": self.server.store.recent(limit=limit)})
            return
        if parsed.path == "/v1/log/surprise":
            limit = DEFAULT_RECENT_LIMIT
            query = parse_qs(parsed.query or "")
            if query.get("limit"):
                limit = _coerce_port(query["limit"][0], fallback=DEFAULT_RECENT_LIMIT)
            _json_response(self, 200, self.server.store.surprise_snapshot(limit=limit))
            return
        _json_response(self, 404, {"ok": False, "state": "not-found", "path": parsed.path})

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        parsed = urlparse(self.path)
        if parsed.path != "/v1/log/events":
            _json_response(self, 404, {"ok": False, "state": "not-found", "path": parsed.path})
            return

        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            _json_response(self, 400, {"ok": False, "state": "empty"})
            return
        if length > MAX_EVENT_BYTES * 4:
            _json_response(self, 413, {"ok": False, "state": "too-large", "max_bytes": MAX_EVENT_BYTES * 4})
            return

        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            _json_response(self, 400, {"ok": False, "state": "bad-json", "error": str(exc)})
            return

        events: list[dict[str, Any]]
        if isinstance(payload, dict) and isinstance(payload.get("events"), list):
            events = [item for item in payload["events"] if isinstance(item, dict)]
        elif isinstance(payload, dict):
            events = [payload]
        else:
            _json_response(self, 400, {"ok": False, "state": "bad-payload"})
            return
        result = self.server.store.append_many(events)
        _json_response(self, 200, result)


def serve_main_log(
    *,
    root: Path | str,
    host: str = DEFAULT_MAIN_LOG_HOST,
    port: int = DEFAULT_MAIN_LOG_PORT,
    ready_event: threading.Event | None = None,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    store = MainLogStore(root=root_path)
    server = MainLogHTTPServer((host, int(port)), MainLogRequestHandler, store)
    os.environ[ENV_MAIN_LOG_URL] = f"http://{host}:{int(port)}"
    os.environ[ENV_MAIN_LOG_HOST] = host
    os.environ[ENV_MAIN_LOG_PORT] = str(int(port))
    store.start(host=host, port=int(port))
    store.mark_ready(host=host, port=int(port))
    if ready_event is not None:
        ready_event.set()

    stopping = False

    def _signal_stop(signum: int, frame: object | None = None) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        threading.Thread(target=server.shutdown, name="main-log-shutdown", daemon=True).start()

    old_sigterm = None
    old_sigint = None
    try:
        old_sigterm = signal.signal(signal.SIGTERM, _signal_stop)
        old_sigint = signal.signal(signal.SIGINT, _signal_stop)
    except (ValueError, OSError):
        pass

    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        store.stop(host=host, port=int(port))
        try:
            if old_sigterm is not None:
                signal.signal(signal.SIGTERM, old_sigterm)
            if old_sigint is not None:
                signal.signal(signal.SIGINT, old_sigint)
        except (ValueError, OSError):
            pass
    return {"ok": True, "state": "stopped", "service": SERVICE_NAME, "root": str(root_path)}


def load_main_log_state(root: Path | str) -> dict[str, Any]:
    state_path = Path(root).resolve() / "runtime" / "main_log" / "state.json"
    if not state_path.exists():
        return {"ok": False, "state": "missing", "state_path": str(state_path)}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"ok": False, "state": "corrupt", "state_path": str(state_path), "error": str(exc)}
    except OSError as exc:
        return {"ok": False, "state": "unreadable", "state_path": str(state_path), "error": str(exc)}
    if isinstance(payload, dict):
        payload.setdefault("state_path", str(state_path))
        return payload
    return {"ok": False, "state": "invalid", "state_path": str(state_path)}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Main Computer centralized main-log service.")
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument("--host", default=os.environ.get(ENV_MAIN_LOG_HOST, DEFAULT_MAIN_LOG_HOST))
    parser.add_argument("--port", type=int, default=_coerce_port(os.environ.get(ENV_MAIN_LOG_PORT), fallback=DEFAULT_MAIN_LOG_PORT))

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Serve the main log HTTP append endpoint.")
    subparsers.add_parser("status", help="Print main-log service state.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    command = args.command or "serve"
    if command == "status":
        print(json.dumps(load_main_log_state(args.root), indent=2, sort_keys=True))
        return 0
    result = serve_main_log(root=args.root, host=args.host, port=int(args.port))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
