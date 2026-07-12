from __future__ import annotations

import io
import json
import threading
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from main_computer.main_log_client import emit_main_log_event
from main_computer.main_log_codec import iter_lex_records
from main_computer.main_log_service import MainLogHTTPServer, MainLogRequestHandler, MainLogStore


def test_main_log_service_accepts_events_writes_lexlog_and_exposes_surprise(tmp_path: Path) -> None:
    store = MainLogStore(root=tmp_path)
    server = MainLogHTTPServer(("127.0.0.1", 0), MainLogRequestHandler, store)
    port = int(server.server_port)
    store.start(host="127.0.0.1", port=port)
    store.mark_ready(host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        health_request = Request(f"http://127.0.0.1:{port}/health", method="GET")
        with urlopen(health_request, timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
        assert health["ok"] is True
        assert "surprise_path" in health
        assert health["follow_path"] == "/v1/log/follow"
        assert health["compress_path"] == "/v1/log/compress"

        for index in range(12):
            result = emit_main_log_event(
                {
                    "service": "test-service",
                    "source_service": "test-service",
                    "stream": "stdout",
                    "message": f"2026-07-11T20:41:{index:02d}Z INFO health check request_id={10_000_000_000 + index}",
                },
                url=f"http://127.0.0.1:{port}",
                timeout_s=1.0,
            )
            assert result["ok"] is True

        result = emit_main_log_event(
            {
                "service": "test-service",
                "source_service": "test-service",
                "stream": "stderr",
                "message": "2026-07-11T20:42:59Z ERROR database timeout duration_ms=9312 status=500 request_id=999999999999",
            },
            url=f"http://127.0.0.1:{port}",
            timeout_s=1.0,
        )
        assert result["ok"] is True
        store._queue.join()

        surprise_request = Request(f"http://127.0.0.1:{port}/v1/log/surprise?limit=5", method="GET")
        with urlopen(surprise_request, timeout=5) as response:
            surprise = json.loads(response.read().decode("utf-8"))

        follow_request = Request(f"http://127.0.0.1:{port}/v1/log/follow?format=ndjson&replay=2&limit=2", method="GET")
        with urlopen(follow_request, timeout=5) as response:
            follow_lines = [
                json.loads(line)
                for line in response.read().decode("utf-8").splitlines()
                if line.strip()
            ]

        compress_request = Request(f"http://127.0.0.1:{port}/v1/log/compress?top=5&surprise_threshold=4", method="GET")
        with urlopen(compress_request, timeout=5) as response:
            assert response.headers.get_content_type() == "application/zip"
            compressed_zip = response.read()
    finally:
        server.shutdown()
        server.server_close()
        store.stop(host="127.0.0.1", port=port)
        thread.join(timeout=5)

    assert store.log_path.name == "main.log.lex"
    records = list(iter_lex_records(store.log_path))
    assert any("health check" in str(record.get("message")) for record in records)
    assert any("database timeout" in str(record.get("message")) for record in records)
    assert records[-1]["ingest_seq"] >= 1

    assert store.surprise_path.exists()
    sidecar = json.loads(store.surprise_path.read_text(encoding="utf-8"))
    assert sidecar["mode"] == "semantic_surprise_summary"

    assert surprise["ok"] is True
    assert surprise["summary"]["total_events"] == 13
    assert surprise["summary"]["dominant_signature_fraction"] > 0.8
    assert surprise["histograms"]["surprise_bits"]
    assert surprise["top_surprise_events"]
    assert "database timeout" in surprise["top_surprise_events"][0]["signature_preview"]

    follow_log_lines = [line for line in follow_lines if line.get("event") == "log"]
    assert follow_lines[0]["event"] == "hello"
    assert len(follow_log_lines) == 2
    assert follow_log_lines[-1]["record"]["message"].startswith("2026-07-11T20:42:59Z ERROR")
    assert follow_log_lines[-1]["signature_preview"]
    assert follow_log_lines[-1]["surprise_bits"] > 0

    with zipfile.ZipFile(io.BytesIO(compressed_zip), "r") as zf:
        assert "main-log-surprise-pack.json" in zf.namelist()
        pack = json.loads(zf.read("main-log-surprise-pack.json").decode("utf-8"))
    assert pack["schema"] == "mclog-surprise-pack-v1"
    assert pack["summary"]["total_events"] == 13
    assert pack["summary"]["unique_signatures"] == 2
    assert pack["summary"]["run_count"] == 2

    recent = store.recent(limit=1)[0]
    assert "_main_log_surprise_bits" in recent
    assert "_main_log_signature_hash" in recent
    assert "_main_log_signature_id" in recent
    assert "_main_log_signature_preview" in recent
