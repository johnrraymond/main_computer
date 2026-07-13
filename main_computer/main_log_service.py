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
from main_computer.main_log_pack import MainLogPackOptions, build_main_log_pack_zip_bytes
from main_computer.log_profile_mds import ProfileMapOptions, build_log_profile_map, render_profile_map_svg
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


def _zip_response(handler: BaseHTTPRequestHandler, status: int, body: bytes, *, filename: str = "main-log-surprise-pack.zip") -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "application/zip")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(body)


def _svg_response(handler: BaseHTTPRequestHandler, status: int, body: str) -> None:
    encoded = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "image/svg+xml; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(encoded)


def _coerce_nonnegative_int(value: object, *, fallback: int = 0, maximum: int = 10_000) -> int:
    try:
        number = int(str(value or "").strip())
    except (TypeError, ValueError):
        return fallback
    if number < 0:
        return fallback
    return min(number, maximum)


def _coerce_float(value: object, *, fallback: float = 0.0, minimum: float = 0.0, maximum: float = 3600.0) -> float:
    try:
        number = float(str(value or "").strip())
    except (TypeError, ValueError):
        return fallback
    if not (minimum <= number <= maximum):
        return fallback
    return number


def _event_surprise(event: dict[str, Any]) -> float:
    try:
        return float(event.get("_main_log_surprise_bits") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _event_seq(event: dict[str, Any]) -> int:
    try:
        return int(event.get("ingest_seq") or 0)
    except (TypeError, ValueError):
        return 0


def _follow_event_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "event": "log",
        "seq": _event_seq(event),
        "surprise_bits": _event_surprise(event),
        "signature_hash": event.get("_main_log_signature_hash"),
        "signature_id": event.get("_main_log_signature_id"),
        "signature_preview": event.get("_main_log_signature_preview"),
        "line_entropy_bits_per_bit": event.get("_main_log_line_entropy_bits_per_bit"),
        "record": event,
    }


def _write_sse_event(handler: BaseHTTPRequestHandler, *, event_name: str, payload: dict[str, Any], event_id: int | None = None) -> None:
    if event_id is not None:
        handler.wfile.write(f"id: {event_id}\n".encode("utf-8"))
    handler.wfile.write(f"event: {event_name}\n".encode("utf-8"))
    body = json.dumps(payload, sort_keys=True, default=str)
    for line in body.splitlines() or [""]:
        handler.wfile.write(f"data: {line}\n".encode("utf-8"))
    handler.wfile.write(b"\n")
    handler.wfile.flush()


def _write_ndjson_event(handler: BaseHTTPRequestHandler, *, event_name: str, payload: dict[str, Any]) -> None:
    envelope = {"event": event_name, **payload}
    handler.wfile.write(json.dumps(envelope, sort_keys=True, default=str).encode("utf-8") + b"\n")
    handler.wfile.flush()


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
        self._condition = threading.Condition(self._lock)
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
            "follow_path": "/v1/log/follow",
            "compress_path": "/v1/log/compress",
            "profile_map_path": "/v1/log/profile-map",
            "profile_nmds_path": "/v1/log/profile-nmds",
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
            "follow_path": "/v1/log/follow",
            "compress_path": "/v1/log/compress",
            "profile_map_path": "/v1/log/profile-map",
            "profile_nmds_path": "/v1/log/profile-nmds",
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
                        with self._condition:
                            item["_main_log_surprise_bits"] = surprise_record["surprise_bits"]
                            item["_main_log_signature_hash"] = surprise_record["signature_hash"]
                            item["_main_log_signature_id"] = surprise_record["signature_id"]
                            item["_main_log_signature_preview"] = surprise_record["signature_preview"]
                            item["_main_log_probability_estimate"] = surprise_record["probability_estimate"]
                            item["_main_log_line_entropy_bits_per_bit"] = surprise_record["line_entropy_bits_per_bit"]
                            self._recent.append(item)
                            if len(self._recent) > self.recent_limit:
                                del self._recent[: len(self._recent) - self.recent_limit]
                            self._condition.notify_all()
                    finally:
                        self._queue.task_done()
        finally:
            if raw_handle is not None:
                raw_handle.close()

    def recent(self, *, limit: int = DEFAULT_RECENT_LIMIT) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._recent)
        return items[-max(1, int(limit)):]

    def recent_after(self, *, since_seq: int = 0, replay: int = 0, min_surprise: float = 0.0) -> list[dict[str, Any]]:
        with self._lock:
            items = [
                dict(item)
                for item in self._recent
                if _event_seq(item) > int(since_seq) and _event_surprise(item) >= float(min_surprise)
            ]
        if replay > 0:
            return items[-int(replay):]
        return items

    def wait_for_recent_after(self, *, since_seq: int, timeout: float, min_surprise: float = 0.0) -> list[dict[str, Any]]:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while not self._stop_event.is_set():
                items = [
                    dict(item)
                    for item in self._recent
                    if _event_seq(item) > int(since_seq) and _event_surprise(item) >= float(min_surprise)
                ]
                if items:
                    return items
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(timeout=remaining)
        return []

    def surprise_snapshot(self, *, limit: int = DEFAULT_RECENT_LIMIT) -> dict[str, Any]:
        return self.surprise_compressor.snapshot(limit=max(1, int(limit)))

    def stop(self, *, host: str, port: int) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
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
                    "follow_path": "/v1/log/follow",
                    "compress_path": "/v1/log/compress",
                    "profile_map_path": "/v1/log/profile-map",
                    "profile_nmds_path": "/v1/log/profile-nmds",
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
        if parsed.path == "/v1/log/compress":
            self._handle_compress(parsed.query or "")
            return
        if parsed.path == "/v1/log/profile-map":
            self._handle_profile_map(parsed.query or "", default_embedding="pca")
            return
        if parsed.path == "/v1/log/profile-nmds":
            self._handle_profile_map(parsed.query or "", default_embedding="nmds")
            return
        if parsed.path == "/v1/log/follow":
            self._handle_follow(parsed.query or "")
            return
        _json_response(self, 404, {"ok": False, "state": "not-found", "path": parsed.path})

    def _handle_profile_map(self, query_string: str, *, default_embedding: str = "pca") -> None:
        query = parse_qs(query_string or "")
        window = str(query.get("window", ["information"])[0] or "information").strip().lower()
        if window not in {"information", "events", "time"}:
            _json_response(self, 400, {"ok": False, "state": "bad-window", "windows": ["information", "events", "time"]})
            return
        normalize = str(query.get("normalize", ["log1p_l1"])[0] or "log1p_l1").strip().lower()
        if normalize not in {"raw", "log1p", "sqrt", "l1", "log1p_l1", "binary"}:
            _json_response(self, 400, {"ok": False, "state": "bad-normalize", "normalizations": ["raw", "log1p", "sqrt", "l1", "log1p_l1", "binary"]})
            return
        distance = str(query.get("distance", query.get("metric", ["manhattan"]))[0] or "manhattan").strip().lower()
        if distance not in {"manhattan", "braycurtis", "weighted_jaccard", "cosine"}:
            _json_response(self, 400, {"ok": False, "state": "bad-distance", "distances": ["manhattan", "braycurtis", "weighted_jaccard", "cosine"]})
            return
        feature_weighting = str(query.get("feature_weighting", ["tfidf"])[0] or "tfidf").strip().lower()
        if feature_weighting not in {"none", "idf", "tfidf", "tfidf_l2"}:
            _json_response(self, 400, {"ok": False, "state": "bad-feature-weighting", "feature_weightings": ["none", "idf", "tfidf", "tfidf_l2"]})
            return
        embedding = str(query.get("embedding", [default_embedding])[0] or default_embedding).strip().lower()
        if embedding not in {"pca", "mds", "classical_mds", "pcoa", "nmds", "nonmetric_mds", "non_metric_mds"}:
            _json_response(self, 400, {"ok": False, "state": "bad-embedding", "embeddings": ["pca", "mds", "classical_mds", "pcoa", "nmds", "nonmetric_mds"]})
            return
        output_format = str(query.get("format", ["json"])[0] or "json").strip().lower()
        if output_format not in {"json", "svg"}:
            _json_response(self, 400, {"ok": False, "state": "bad-format", "formats": ["json", "svg"]})
            return
        options = ProfileMapOptions(
            window=window,
            target_surprise_bits=_coerce_float(query.get("target_surprise_bits", ["512"])[0], fallback=512.0, minimum=0.001, maximum=1_000_000.0),
            stride_surprise_bits=_coerce_float(query.get("stride_surprise_bits", ["512"])[0], fallback=512.0, minimum=0.001, maximum=1_000_000.0),
            event_window=_coerce_nonnegative_int(query.get("event_window", ["500"])[0], fallback=500, maximum=100_000),
            event_stride=_coerce_nonnegative_int(query.get("event_stride", ["500"])[0], fallback=500, maximum=100_000),
            seconds_window=_coerce_float(query.get("seconds_window", ["60"])[0], fallback=60.0, minimum=0.001, maximum=86400.0),
            seconds_stride=_coerce_float(query.get("seconds_stride", ["60"])[0], fallback=60.0, minimum=0.001, maximum=86400.0),
            max_coverage_points=_coerce_nonnegative_int(query.get("max_coverage_points", ["10000"])[0], fallback=10_000, maximum=100_000),
            max_profiles=_coerce_nonnegative_int(query.get("max_profiles", ["200"])[0], fallback=200, maximum=2_000),
            normalize=normalize,
            distance=distance,
            feature_weighting=feature_weighting,
            min_df=_coerce_nonnegative_int(query.get("min_df", ["1"])[0], fallback=1, maximum=10_000),
            max_df_fraction=_coerce_float(query.get("max_df_fraction", ["0.95"])[0], fallback=0.95, minimum=0.000001, maximum=1.0),
            embedding=embedding,
            alpha=_coerce_float(query.get("alpha", ["0.5"])[0], fallback=0.5, minimum=0.000001, maximum=100.0),
            nmds_iterations=_coerce_nonnegative_int(query.get("nmds_iterations", ["80"])[0], fallback=80, maximum=2_000),
            nmds_restarts=max(1, _coerce_nonnegative_int(query.get("nmds_restarts", ["3"])[0], fallback=3, maximum=100)),
            nmds_seed=_coerce_nonnegative_int(query.get("nmds_seed", ["17"])[0], fallback=17, maximum=2_147_483_647),
            include_distance_matrix=str(query.get("include_distance_matrix", ["0"])[0] or "").strip().lower() in {"1", "true", "yes", "on"},
        )
        try:
            profile_map = build_log_profile_map(root=self.server.store.root, input_path=self.server.store.log_path, options=options)
        except Exception as exc:
            _json_response(self, 500, {"ok": False, "state": "profile-map-failed", "error": str(exc)})
            return
        if output_format == "svg":
            svg_scale = str(query.get("svg_scale", query.get("scale", ["robust"]))[0] or "robust").strip().lower()
            if svg_scale not in {"robust", "full"}:
                _json_response(self, 400, {"ok": False, "state": "bad-svg-scale", "svg_scales": ["robust", "full"]})
                return
            label_limit = _coerce_nonnegative_int(query.get("label_limit", ["24"])[0], fallback=24, maximum=1000)
            show_labels = str(query.get("labels", ["1"])[0] or "1").strip().lower() not in {"0", "false", "no", "off"}
            svg_width = _coerce_nonnegative_int(query.get("width", ["1200"])[0], fallback=1200, maximum=5000)
            svg_height = _coerce_nonnegative_int(query.get("height", ["800"])[0], fallback=800, maximum=5000)
            _svg_response(
                self,
                200,
                render_profile_map_svg(
                    profile_map,
                    width=svg_width,
                    height=svg_height,
                    label_limit=label_limit,
                    scale=svg_scale,
                    show_labels=show_labels,
                ),
            )
            return
        _json_response(self, 200, profile_map)

    def _handle_compress(self, query_string: str) -> None:
        query = parse_qs(query_string or "")
        top = _coerce_nonnegative_int(query.get("top", ["200"])[0], fallback=200, maximum=10_000)
        bins = _coerce_nonnegative_int(query.get("bins", ["16"])[0], fallback=16, maximum=200)
        threshold = _coerce_float(query.get("surprise_threshold", ["8"])[0], fallback=8.0, minimum=0.0, maximum=10_000.0)
        alpha = _coerce_float(query.get("alpha", ["0.5"])[0], fallback=0.5, minimum=0.000001, maximum=100.0)
        compression = str(query.get("compression", ["lzma"])[0] or "lzma").strip().lower()
        if compression not in {"lzma", "deflate", "bzip2", "stored"}:
            _json_response(self, 400, {"ok": False, "state": "bad-compression", "formats": ["lzma", "deflate", "bzip2", "stored"]})
            return
        include_lossless_source = str(query.get("include_raw", ["0"])[0] or "").strip().lower() in {"1", "true", "yes", "on"}
        include_report = str(query.get("report", ["1"])[0] or "").strip().lower() not in {"0", "false", "no", "off"}
        include_surprise_literals = str(query.get("surprise_literals", ["1"])[0] or "").strip().lower() not in {"0", "false", "no", "off"}
        options = MainLogPackOptions(
            alpha=alpha,
            top=max(1, top),
            histogram_bins=max(1, bins),
            surprise_threshold_bits=threshold,
            include_lossless_source=include_lossless_source,
            include_surprise_literals=include_surprise_literals,
            include_report=include_report,
            compression=compression,
        )
        try:
            body = build_main_log_pack_zip_bytes(root=self.server.store.root, input_path=self.server.store.log_path, options=options)
        except Exception as exc:
            _json_response(self, 500, {"ok": False, "state": "compress-failed", "error": str(exc)})
            return
        _zip_response(self, 200, body)

    def _handle_follow(self, query_string: str) -> None:
        query = parse_qs(query_string or "")
        since_seq = _coerce_nonnegative_int(query.get("since", ["0"])[0], fallback=0)
        replay = _coerce_nonnegative_int(query.get("replay", ["20"])[0], fallback=20, maximum=self.server.store.recent_limit)
        max_events = _coerce_nonnegative_int(query.get("limit", ["0"])[0], fallback=0)
        min_surprise = _coerce_float(query.get("min_surprise", ["0"])[0], fallback=0.0, minimum=0.0, maximum=10_000.0)
        heartbeat = _coerce_float(query.get("heartbeat", ["15"])[0], fallback=15.0, minimum=0.1, maximum=300.0)
        output_format = str(query.get("format", ["sse"])[0] or "sse").strip().lower()
        if output_format not in {"sse", "ndjson"}:
            _json_response(self, 400, {"ok": False, "state": "bad-format", "formats": ["sse", "ndjson"]})
            return

        if output_format == "ndjson":
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close" if max_events else "keep-alive")
            self.end_headers()

            def write_event(name: str, payload: dict[str, Any], event_id: int | None = None) -> None:
                del event_id
                _write_ndjson_event(self, event_name=name, payload=payload)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close" if max_events else "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            def write_event(name: str, payload: dict[str, Any], event_id: int | None = None) -> None:
                _write_sse_event(self, event_name=name, payload=payload, event_id=event_id)

        sent = 0
        last_seq = since_seq
        try:
            hello = {
                "ok": True,
                "event": "hello",
                "service": SERVICE_NAME,
                "mode": "follow",
                "format": output_format,
                "since_seq": since_seq,
                "replay": replay,
                "min_surprise": min_surprise,
                "heartbeat_seconds": heartbeat,
                "warning": "Follow records are live log events with model-relative surprise fields, not proof of semantic meaning.",
            }
            write_event("hello", hello)

            for event in self.server.store.recent_after(since_seq=since_seq, replay=replay, min_surprise=min_surprise):
                payload = _follow_event_payload(event)
                last_seq = max(last_seq, int(payload["seq"]))
                write_event("log", payload, event_id=last_seq)
                sent += 1
                if max_events and sent >= max_events:
                    write_event("done", {"ok": True, "event": "done", "sent": sent, "last_seq": last_seq})
                    self.close_connection = True
                    return

            while not self.server.store._stop_event.is_set():
                events = self.server.store.wait_for_recent_after(
                    since_seq=last_seq,
                    timeout=heartbeat,
                    min_surprise=min_surprise,
                )
                if not events:
                    write_event("heartbeat", {"ok": True, "event": "heartbeat", "last_seq": last_seq, "at": _now_iso()})
                    continue
                for event in events:
                    payload = _follow_event_payload(event)
                    last_seq = max(last_seq, int(payload["seq"]))
                    write_event("log", payload, event_id=last_seq)
                    sent += 1
                    if max_events and sent >= max_events:
                        write_event("done", {"ok": True, "event": "done", "sent": sent, "last_seq": last_seq})
                        self.close_connection = True
                        return
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            return

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
