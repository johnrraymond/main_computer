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


class ViewportEditorRouteTests(unittest.TestCase):
    def test_editor_files_accept_workspace_name_when_workspace_is_project_root(self) -> None:
        config = MainComputerConfig(workspace=Path.cwd())
        server = ViewportServer(("127.0.0.1", 0), config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            editor_root_request = Request(
                f"{base}/api/applications/editor/files",
                data=json.dumps({"repo_dir": Path.cwd().name, "path": "", "limit": 50}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(editor_root_request, timeout=10) as response:
                editor_root = json.loads(response.read().decode("utf-8"))
            self.assertEqual(editor_root["path"], "")
            self.assertTrue(any(item["path"] == "main_computer" and item["kind"] == "dir" for item in editor_root["entries"]))
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
