from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from main_computer.main_log_codec import iter_lex_records
from main_computer.main_log_hooks import install_main_log_hooks, uninstall_main_log_hooks
from main_computer.main_log_service import MainLogHTTPServer, MainLogRequestHandler, MainLogStore


def _start_main_log(root: Path) -> tuple[MainLogStore, MainLogHTTPServer, threading.Thread, str]:
    store = MainLogStore(root=root)
    server = MainLogHTTPServer(("127.0.0.1", 0), MainLogRequestHandler, store)
    port = int(server.server_port)
    store.start(host="127.0.0.1", port=port)
    store.mark_ready(host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return store, server, thread, f"http://127.0.0.1:{port}"


def _stop_main_log(store: MainLogStore, server: MainLogHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    store.stop(host="127.0.0.1", port=int(server.server_port))
    thread.join(timeout=5)


def test_main_log_hooks_capture_path_open_log_writes(tmp_path: Path) -> None:
    store, server, thread, url = _start_main_log(tmp_path)
    try:
        installed = install_main_log_hooks(
            service_name="hook-test",
            root=tmp_path,
            url=url,
        )
        assert installed["ok"] is True

        log_path = tmp_path / "runtime" / "example" / "worker.log"
        log_path.parent.mkdir(parents=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("hello from Path.open\n")

        store._queue.join()
    finally:
        uninstall_main_log_hooks()
        _stop_main_log(store, server, thread)

    assert log_path.read_text(encoding="utf-8") == "hello from Path.open\n"
    records = list(iter_lex_records(store.log_path))
    assert any(
        record.get("kind") == "file-write"
        and record.get("stream") == "file-log"
        and record.get("source_service") == "hook-test"
        and record.get("repo_path") == os.path.join("runtime", "example", "worker.log")
        and record.get("message") == "hello from Path.open"
        for record in records
    )


def test_main_log_hooks_do_not_recurse_on_main_log_file(tmp_path: Path) -> None:
    store, server, thread, url = _start_main_log(tmp_path)
    try:
        install_main_log_hooks(service_name="hook-test", root=tmp_path, url=url)
        store.log_path.parent.mkdir(parents=True, exist_ok=True)
        with store.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"manual": True}) + "\n")

        request = Request(f"{url}/v1/log/recent?limit=10", method="GET")
        with urlopen(request, timeout=5) as response:
            recent = json.loads(response.read().decode("utf-8"))
        assert recent["ok"] is True
        assert recent["events"] == []
    finally:
        uninstall_main_log_hooks()
        _stop_main_log(store, server, thread)
