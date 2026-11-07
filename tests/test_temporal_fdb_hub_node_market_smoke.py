from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

from main_computer.temporal_fdb_hub_node_market_smoke import (
    HubNodeMarketSmokeConfig,
    _post_json,
    _worker_wallet_address_for_config,
    run_temporal_fdb_hub_node_market_smoke,
)


class _RetryOnceHandler(BaseHTTPRequestHandler):
    attempts = 0

    def log_message(self, format: str, *args: object) -> None:  # pragma: no cover - keeps test output quiet
        return

    def do_POST(self) -> None:
        type(self).attempts += 1
        if type(self).attempts == 1:
            body = json.dumps(
                {
                    "ok": False,
                    "error": "Hub worker route overloaded; retry later.",
                    "error_type": "hub_worker_route_overloaded",
                    "retry_after_seconds": 0.01,
                }
            ).encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps({"ok": True, "retried": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TemporalFdbHubNodeMarketSmokeTests(unittest.TestCase):
    def test_retired_hub_market_smoke_refuses_before_network(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        smoke_config = HubNodeMarketSmokeConfig(repo_root=root,
            hub_url='http://127.0.0.1:1', report_path=None,
            event_log_path=root / 'events.jsonl', hub_start_mode='auto')
        with self.assertRaisesRegex(Exception, 'Retired'):
            asyncio.run(run_temporal_fdb_hub_node_market_smoke(smoke_config))
        self.assertEqual(list(root.iterdir()), [])

    def test_http_client_retries_retryable_hub_backpressure(self) -> None:
        _RetryOnceHandler.attempts = 0
        server = HTTPServer(("127.0.0.1", 0), _RetryOnceHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def cleanup() -> None:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.addCleanup(cleanup)

        result = _post_json(
            f"http://127.0.0.1:{server.server_port}",
            "/retry-once",
            {"hello": "world"},
            timeout=1,
            retry_attempts=2,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["retried"])
        self.assertEqual(_RetryOnceHandler.attempts, 2)



    def test_worker_wallet_addresses_can_be_assigned_by_node_index(self) -> None:
        config = HubNodeMarketSmokeConfig(
            repo_root=Path.cwd(),
            worker_wallet_addresses=(
                "0x0000000000000000000000000000000000000101",
                "0x0000000000000000000000000000000000000102",
            ),
        )

        first = SimpleNamespace(node_id="node-001")
        second = SimpleNamespace(node_id="node-002")
        fallback = SimpleNamespace(node_id="node-003")

        self.assertEqual(_worker_wallet_address_for_config(config, first), "0x0000000000000000000000000000000000000101")
        self.assertEqual(_worker_wallet_address_for_config(config, second), "0x0000000000000000000000000000000000000102")
        self.assertNotEqual(_worker_wallet_address_for_config(config, fallback), "")


if __name__ == "__main__":
    unittest.main()
