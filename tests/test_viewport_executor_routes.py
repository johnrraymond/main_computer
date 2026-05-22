from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.models import ChatResponse
from main_computer.viewport import ViewportServer


class FakeExecutorProvider:
    name = "fake"
    model = "fake-executor-model"

    def chat(self, messages: object) -> ChatResponse:
        return ChatResponse(
            content='{"action":"execute_shell","command":"python -c \\"print(123)\\"","cwd":"/workspace","timeout_s":5}',
            provider=self.name,
            model=self.model,
        )


class ViewportExecutorRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)
        self.server = ViewportServer(
            ("127.0.0.1", 0),
            MainComputerConfig(
                workspace=self.repo,
                executor_root=self.repo / "runtime" / "executor",
                executor_enabled=False,
                executor_max_upload_bytes=1024 * 1024,
            ),
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

    def _json_request(self, path: str, payload: dict[str, object] | None = None, method: str = "POST") -> dict[str, object]:
        request = Request(
            f"{self.base}{path}",
            data=None if method == "GET" else json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_status_and_raw_upload_routes(self) -> None:
        status = self._json_request("/api/executor/status", method="GET")
        self.assertTrue(status["ok"])
        self.assertFalse(status["executor"]["enabled"])
        self.assertEqual(status["executor"]["backend"], "docker")

        body = b"alpha,beta\n1,2\n"
        request = Request(
            f"{self.base}/api/executor/uploads?filename={quote('large data.csv')}",
            data=body,
            headers={"Content-Type": "text/csv"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            uploaded = json.loads(response.read().decode("utf-8"))

        self.assertTrue(uploaded["ok"])
        upload = uploaded["upload"]
        self.assertEqual(upload["filename"], "large data.csv")
        self.assertEqual(upload["size"], len(body))
        self.assertEqual(upload["container_path"], f"/inputs/{upload['id']}/payload.bin")

        listed = self._json_request("/api/executor/uploads", method="GET")
        self.assertEqual(listed["uploads"][0]["id"], upload["id"])

    def test_run_route_reports_disabled_executor_without_running_docker(self) -> None:
        request = Request(
            f"{self.base}/api/executor/run",
            data=json.dumps({"command": "python -c \"print(123)\""}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=5)
        self.assertEqual(raised.exception.code, 400)
        payload = json.loads(raised.exception.read().decode("utf-8"))
        self.assertFalse(payload["ok"])
        self.assertIn("disabled", payload["error"])


    def test_executor_ai_route_returns_approval_request_without_auto_run(self) -> None:
        self.server.computer.provider = FakeExecutorProvider()
        data = self._json_request(
            "/api/executor/ai",
            {
                "prompt": "Use Linux python to inspect something.",
                "upload_ids": ["upload_0123456789abcdef"],
                "auto_run": True,
                "max_steps": 2,
            },
        )

        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "tool_requested")
        self.assertTrue(data["needs_approval"])
        self.assertFalse(data["policy"]["auto_run_effective"])
        self.assertEqual(data["tool_request"]["input_ids"], ["upload_0123456789abcdef"])
        self.assertEqual(data["tool_request"]["cwd"], "/workspace")

    def test_artifact_download_route_serves_output_file_and_rejects_traversal(self) -> None:
        job_id = "0123456789abcdef"
        artifact_dir = self.server.executor_backend.outputs_root / job_id
        artifact_dir.mkdir(parents=True)
        # Write bytes so Windows newline translation does not change the served artifact.
        (artifact_dir / "result.txt").write_bytes(b"download me\n")

        with urlopen(f"{self.base}/api/executor/artifacts/{job_id}/result.txt", timeout=5) as response:
            self.assertEqual(response.read(), b"download me\n")

        with self.assertRaises(HTTPError) as raised:
            urlopen(f"{self.base}/api/executor/artifacts/{job_id}/../result.txt", timeout=5)
        self.assertEqual(raised.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
