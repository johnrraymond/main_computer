from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.service_control import pending_control_requests
from main_computer.viewport import ViewportServer


class ViewportSupervisorControlRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)
        self.server = ViewportServer(
            ("127.0.0.1", 0),
            MainComputerConfig(workspace=self.repo),
            verbose=False,
        )
        self.server.debug_root = self.repo.resolve()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tempdir.cleanup()

    def _post_json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        request = Request(
            f"{self.base}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_supervisor_action_route_queues_restart_request(self) -> None:
        payload = self._post_json(
            "/api/control-panel/supervisor/action",
            {"action": "restart", "target": "executor"},
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["queued"]["request"]["target"], "executor")
        pending = pending_control_requests(self.repo, channel="supervisor")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].action, "restart")
        self.assertEqual(pending[0].target, "executor")

        with urlopen(f"{self.base}/api/control-panel/supervisor/status", timeout=5) as response:
            status = json.loads(response.read().decode("utf-8"))
        self.assertTrue(status["ok"])
        self.assertEqual(status["control"]["channels"]["supervisor"]["pending_count"], 1)
