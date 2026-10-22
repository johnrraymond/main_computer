"""Regression: Activity Dock snapshot GETs must not create activity events.

Uses the repository's actual dispatch_get, ViewportServer.signal and ActivityBus
in a minimal loopback HTTP harness. Extracting the two methods from source keeps
unrelated page assets and services out of this focused route test.

On unfixed code, both snapshot tests fail while the ordinary GET control passes.
After excluding only /api/activity/snapshot from generic GET logging, all pass.
"""
from __future__ import annotations

import ast
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from main_computer.activity import ActivityBus


ROOT = Path(__file__).resolve().parents[1]


def _original_method(relative_path: str, *, class_name: str | None, name: str, namespace: dict):
    """Execute one unmodified method from the checked-out production source."""
    source_file = ROOT / relative_path
    tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
    nodes = tree.body
    if class_name is not None:
        nodes = next(
            node.body for node in nodes
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
    function = next(
        node for node in nodes
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    local = dict(namespace)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), str(source_file), "exec"), local)
    return local[name]


_SIGNAL = _original_method(
    "main_computer/viewport_server.py",
    class_name="ViewportServer",
    name="signal",
    namespace={},
)
_DISPATCH_GET = _original_method(
    "main_computer/viewport_route_dispatch.py",
    class_name=None,
    name="dispatch_get",
    namespace={
        "urlsplit": urlsplit,
        "parse_qs": parse_qs,
        "HARD_HALT_PATH": "/system/hard-halt",
        "SYSTEM_SHUTDOWN_PATH": "/system/shutdown",
        "_is_website_builder_application_route": lambda _path: False,
        "_application_route_target": lambda _path: None,
    },
)


class _ActivityServer(ThreadingHTTPServer):
    signal = _SIGNAL

    def __init__(self, address, handler_class, workspace: Path):
        super().__init__(address, handler_class)
        self.activity = ActivityBus(workspace)
        self.verbose = False


class _ActivityHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        _DISPATCH_GET(self)

    def _send_json(self, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


class ActivitySnapshotPollFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.server = _ActivityServer(("127.0.0.1", 0), _ActivityHandler, Path(self.tempdir.name))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tempdir.cleanup()

    def get(self, path):
        with urlopen(self.base + path, timeout=5) as response:
            return json.load(response)

    def test_snapshot_get_creates_no_event_and_returns_no_self_event(self):
        before = self.server.activity.snapshot()["event_count"]
        payload = self.get("/api/activity/snapshot")
        after = self.server.activity.snapshot()["event_count"]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["event_count"], before, "Snapshot payload must not include its own HTTP request")
        self.assertEqual(after, before, "Snapshot GET must not write to the Activity Bus")

    def test_snapshot_get_with_query_parameters_is_read_only(self):
        before = self.server.activity.snapshot()["event_count"]
        self.get("/api/activity/snapshot?probe=1")
        self.get("/api/activity/snapshot?probe=2")
        after = self.server.activity.snapshot()["event_count"]
        self.assertEqual(after, before, "Query parameters must not bypass the snapshot logging exclusion")

    def test_unrelated_get_still_logs_http_request(self):
        before = self.server.activity.snapshot()["event_count"]
        payload = self.get("/api/activity/events?limit=5")
        after = self.server.activity.snapshot()["event_count"]
        self.assertTrue(payload["ok"])
        self.assertEqual(after - before, 2, "Ordinary GET retains generic HTTP and route-specific activity signals")
        events = self.server.activity.events(limit=5)
        self.assertTrue(any(event["title"] == "Http Request" for event in events))
        self.assertTrue(any(event["title"] == "Api Activity Events" for event in events))


if __name__ == "__main__":
    unittest.main()
