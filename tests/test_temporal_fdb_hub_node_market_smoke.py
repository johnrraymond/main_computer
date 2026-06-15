from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
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
    def test_direct_activity_smoke_drives_hub_http_worker_pull_path(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        hub_config = MainComputerConfig(
            workspace=root,
            model="temporal-fdb-hub-node-market-model",
            hub_root=root / "hub-runtime",
            hub_credits_per_request=1,
        )
        hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
        thread = threading.Thread(target=hub.serve_forever, daemon=True)
        thread.start()

        def cleanup() -> None:
            hub.shutdown()
            thread.join(timeout=5)
            hub.server_close()

        self.addCleanup(cleanup)

        smoke_config = HubNodeMarketSmokeConfig(
            repo_root=Path.cwd(),
            hub_url=f"http://127.0.0.1:{hub.server_port}",
            execution_mode="direct-activity",
            report_path=None,
            event_log_path=root / "events.jsonl",
            node_count=10,
            request_count=5,
            token_count=2,
            token_interval_seconds=0,
            require_foundationdb_backends=False,
            emit_progress=False,
            http_timeout_seconds=5,
            account_id="tiny-json-hub-smoke-client",
        )
        report = asyncio.run(run_temporal_fdb_hub_node_market_smoke(smoke_config))

        self.assertTrue(report["ok"])
        self.assertEqual(report["nodes_registered"], 10)
        self.assertEqual(report["requests_submitted"], 5)
        self.assertEqual(report["requests_completed"], 5)
        self.assertGreaterEqual(report["selected_worker_count"], 2)
        self.assertEqual(report["selected_worker_rings"], [1])
        self.assertEqual(report["selected_worker_prices"], [2])
        self.assertEqual(report["token_events"], 10)
        self.assertEqual(report["expected_spend_credits"], 10)
        self.assertEqual(report["final_spent_credits_total"], 10)
        self.assertEqual(report["active_hold_count_total"], 0)
        self.assertEqual(report["charge_count_total"], 5)
        self.assertEqual(report["worker_earning_count_total"], 5)

    def test_unreachable_hub_without_autostart_explains_how_to_start_it(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        smoke_config = HubNodeMarketSmokeConfig(
            repo_root=Path.cwd(),
            hub_url=f"http://127.0.0.1:{port}",
            execution_mode="direct-activity",
            report_path=None,
            event_log_path=root / "events.jsonl",
            node_count=1,
            request_count=1,
            require_foundationdb_backends=False,
            emit_progress=False,
            http_timeout_seconds=1,
            hub_start_mode="never",
        )

        with self.assertRaises(Exception) as raised:
            asyncio.run(run_temporal_fdb_hub_node_market_smoke(smoke_config))

        message = str(raised.exception)
        self.assertIn("No Hub is listening", message)
        self.assertIn("Start the Hub in another terminal", message)
        self.assertIn("exp-fdb-hub.py", message)
        self.assertIn("--port", message)



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
