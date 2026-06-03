from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from main_computer.main_log_client import emit_main_log_event
from main_computer.main_log_codec import iter_lex_records
from main_computer.main_log_service import MainLogHTTPServer, MainLogRequestHandler, MainLogStore


def test_main_log_service_accepts_events_and_writes_lexlog(tmp_path: Path) -> None:
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

        result = emit_main_log_event(
            {
                "service": "test-service",
                "source_service": "test-service",
                "stream": "stdout",
                "message": "hello main log",
            },
            url=f"http://127.0.0.1:{port}",
            timeout_s=1.0,
        )
        assert result["ok"] is True
        store._queue.join()
    finally:
        server.shutdown()
        server.server_close()
        store.stop(host="127.0.0.1", port=port)
        thread.join(timeout=5)

    assert store.log_path.name == "main.log.lex"
    records = list(iter_lex_records(store.log_path))
    assert any(record.get("message") == "hello main log" for record in records)
    assert records[-1]["ingest_seq"] >= 1
