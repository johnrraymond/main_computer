from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from main_computer.cli import _config_from_args
from main_computer.config import DEFAULT_ENERGY_CHAIN_ID, DEFAULT_ENERGY_CHAIN_RPC_URL, MainComputerConfig
from main_computer.energy import EnergyCreditLedger
from main_computer.governance import bridge_governance_status
from main_computer.models import ChatMessage, ChatResponse
from main_computer.revision import DebugAssetRevisionControl, RevisionControl
from main_computer.viewport import APPLICATIONS_INDEX_HTML, DEBUG_GRAPHICAL_INDEX_HTML, DEBUG_TEXT_INDEX_HTML, ENERGY_INDEX_HTML, GRAPHICAL_INDEX_HTML, REVISION_INDEX_HTML, TEXT_INDEX_HTML, ViewportHandler, ViewportServer, _application_route_target, serve


class ViewportAiderRouteTests(unittest.TestCase):
    def test_aider_context_status_payload_includes_activities(self) -> None:
        handler = object.__new__(ViewportHandler)
        handler.server = type(
            "ServerStub",
            (),
            {
                "aider_web_context": type(
                    "ContextStub",
                    (),
                    {
                        "status": staticmethod(lambda: {
                            "active": {"archive_id": "thread-1"},
                            "current_archive": {"id": "thread-1"},
                            "archives": [{"id": "thread-1"}],
                            "archive_count": 1,
                        }),
                    },
                )(),
                "aider_jobs": type(
                    "JobsStub",
                    (),
                    {
                        "status": staticmethod(lambda: [
                            {"id": "job-1", "archive_id": "thread-1", "status": "running"},
                        ]),
                    },
                )(),
            },
        )()

        payload = handler._aider_context_status_payload()

        self.assertIn("activities", payload)
        self.assertEqual(payload["activities"][0]["id"], "job-1")
        self.assertEqual(payload["active"]["activities"][0]["id"], "job-1")
        self.assertEqual(payload["archives"][0]["activities"][0]["id"], "job-1")
        self.assertEqual(payload["current_archive"]["activities"][0]["id"], "job-1")

    def test_aider_run_returns_accepted_job_and_context_activities(self) -> None:
        config = MainComputerConfig(workspace=Path.cwd().parent)
        server = ViewportServer(("127.0.0.1", 0), config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        job: dict[str, object] = {}
        try:
            base = f"http://127.0.0.1:{server.server_port}"

            with patch("main_computer.viewport.prepare_aider_action") as mocked_prepare:
                mocked_prepare.side_effect = lambda request, config: type(
                    "PreparedStub",
                    (),
                    {
                        "repo_dir": request.repo_dir,
                        "command": ["aider", "--dry-run"],
                        "timeout_seconds": 7,
                    },
                )()

                def start_run_stub(**kwargs):
                    nonlocal job
                    aider_history = kwargs["aider_history"]
                    job = {
                        "id": "job-1",
                        "kind": "run",
                        "status": "running",
                        "archive_id": aider_history["archive_id"],
                        "session_id": aider_history["session_id"],
                        "repo_dir": kwargs["prepared"].repo_dir,
                        "files": list(kwargs["request"].files),
                        "file_count": len(kwargs["request"].files),
                        "instruction": kwargs["request"].instruction,
                        "dry_run": bool(kwargs["request"].dry_run),
                        "command": list(kwargs["prepared"].command),
                        "timeout_seconds": int(kwargs["prepared"].timeout_seconds),
                        "started_at": "2026-04-24T00:00:00+00:00",
                        "updated_at": "2026-04-24T00:00:00+00:00",
                        "finished_at": None,
                        "result": None,
                        "error": None,
                    }
                    return dict(job)

                server.aider_jobs.start_run = start_run_stub  # type: ignore[method-assign]
                server.aider_jobs.status = lambda: [dict(job)] if job else []  # type: ignore[assignment]

                aider_run_request = Request(
                    f"{base}/api/applications/aider/run",
                    data=json.dumps(
                        {
                            "repo_dir": "main_computer_test",
                            "files": ["TODO.md"],
                            "instruction": "hi there",
                            "model": "ollama_chat/llama3.1:8b",
                            "dry_run": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(aider_run_request, timeout=10) as response:
                    aider_run = json.loads(response.read().decode("utf-8"))

            self.assertEqual(response.status, 202)
            self.assertTrue(aider_run["accepted"])
            self.assertTrue(aider_run["job"]["id"])
            self.assertIn("activities", aider_run)
            self.assertEqual(aider_run["activities"][0]["id"], "job-1")
            self.assertEqual(aider_run["active"]["activities"][0]["id"], "job-1")
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
