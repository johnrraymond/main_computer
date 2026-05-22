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


class ViewportApiRouteTests(unittest.TestCase):
    def test_viewport_api_serves_projects_and_chat(self) -> None:
        config = MainComputerConfig(workspace=__import__("pathlib").Path.cwd().parent)
        server = ViewportServer(("127.0.0.1", 0), config)
        aider_context_tempdir = tempfile.TemporaryDirectory()
        server.aider_web_context = type(server.aider_web_context)(Path(aider_context_tempdir.name) / "aider_web_context")

        class FakeCatalog:
            def list_projects(self):
                return []

        class FakeProvider:
            name = "fake"
            model = "fake-model"

        class FakeComputer:
            catalog = FakeCatalog()
            provider = FakeProvider()

            def chat(self, prompt: str) -> ChatResponse:
                return ChatResponse(content=f"echo: {prompt}", provider="fake", model="fake-model")

        server.computer = FakeComputer()  # type: ignore[assignment]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(f"{base}/api/projects", timeout=5) as response:
                projects = json.loads(response.read().decode("utf-8"))
            self.assertEqual(projects["provider"], "fake")
            self.assertEqual(projects["ollama_timeout_s"], 600.0)
            self.assertEqual(projects["patch_level"], "0.1.0")
            self.assertEqual(projects["runtime_bridge"]["control_model"], "one graphical bridge, shared control code, explicit production and engineering roots")
            self.assertIn("main_computer_test", projects["runtime_bridge"]["engineering_root"])
            self.assertIn("dev-control.ps1 start -Mode local", projects["runtime_bridge"]["commands"]["dev"])
            self.assertIn("-LocalPort 8766", projects["runtime_bridge"]["commands"]["production"])

            with urlopen(f"{base}/api/workspace-timestamp", timeout=5) as response:
                timestamp = json.loads(response.read().decode("utf-8"))
            self.assertIn("latest_mtime_iso", timestamp)
            self.assertIn("latest_path", timestamp)
            self.assertEqual(timestamp["patch_level"], "0.1.0")

            with urlopen(f"{base}/api/ollama-debug/status", timeout=5) as response:
                debug_status = json.loads(response.read().decode("utf-8"))
            self.assertFalse(debug_status["active"])
            self.assertEqual(debug_status["model"], "gemma4:26b")
            self.assertEqual(debug_status["ollama_timeout_s"], 600.0)
            self.assertEqual(debug_status["patch_level"], "0.1.0")

            with urlopen(f"{base}/", timeout=5) as response:
                text_page = response.read().decode("utf-8")
            self.assertIn("Main Computer Console", text_page)

            with urlopen(f"{base}/graphical", timeout=5) as response:
                graphical_page = response.read().decode("utf-8")
            self.assertIn("Main Computer Control Panel", graphical_page)

            with urlopen(f"{base}/api/control-panel/status", timeout=5) as response:
                control_panel = json.loads(response.read().decode("utf-8"))
            self.assertTrue(control_panel["ok"])
            self.assertIn(control_panel["overall"]["state"], {"healthy", "degraded", "broken"})
            self.assertTrue(any(service["id"] == "ollama" for service in control_panel["services"]))
            self.assertTrue(any(service["id"] == "gitea" for service in control_panel["services"]))
            gitea_service = next(service for service in control_panel["services"] if service["id"] == "gitea")
            self.assertEqual(gitea_service["port"], 3000)
            self.assertIn("gitea", control_panel["ports"])
            self.assertEqual(control_panel["ports"]["gitea"]["port"], 3000)
            self.assertTrue(any(service["id"] == "blockchain" for service in control_panel["services"]))
            self.assertIn("memory", control_panel["machine"])
            self.assertIn("dependencies", control_panel)

            with urlopen(f"{base}/debug/text", timeout=5) as response:
                debug_text_page = response.read().decode("utf-8")
            self.assertIn("Main Computer Text Debug", debug_text_page)

            with urlopen(f"{base}/debug/graphical", timeout=5) as response:
                debug_graphical_page = response.read().decode("utf-8")
            self.assertIn("Main Computer Graphical Debug", debug_graphical_page)

            with urlopen(f"{base}/energy", timeout=5) as response:
                energy_page = response.read().decode("utf-8")
            self.assertIn("Main Computer Energy Credits", energy_page)

            with urlopen(f"{base}/applications", timeout=5) as response:
                applications_page = response.read().decode("utf-8")
            self.assertIn("Main Computer Applications", applications_page)

            with urlopen(f"{base}/applications/task-manager", timeout=5) as response:
                task_manager_page = response.read().decode("utf-8")
            self.assertIn("Main Computer Applications", task_manager_page)
            self.assertIn("applicationFromPath", task_manager_page)

            with urlopen(f"{base}/applications/task-manager/connections", timeout=5) as response:
                task_manager_connections_page = response.read().decode("utf-8")
            self.assertIn("Main Computer Applications", task_manager_connections_page)
            self.assertIn("taskNotebookTabFromPath", task_manager_connections_page)

            with urlopen(f"{base}/apps/git-tools", timeout=5) as response:
                git_tools_page = response.read().decode("utf-8")
            self.assertIn("Main Computer Applications", git_tools_page)

            with self.assertRaises(HTTPError) as invalid_app:
                urlopen(f"{base}/applications/not-a-real-app", timeout=5)
            self.assertEqual(invalid_app.exception.code, 404)

            with urlopen(f"{base}/api/energy/status", timeout=5) as response:
                energy = json.loads(response.read().decode("utf-8"))
            self.assertEqual(energy["head"]["node_id"], "main-computer-head")

            with urlopen(f"{base}/revision", timeout=5) as response:
                revision_page = response.read().decode("utf-8")
            self.assertIn("Main Computer Revision Control", revision_page)

            with urlopen(f"{base}/api/revisions/status", timeout=5) as response:
                revisions = json.loads(response.read().decode("utf-8"))
            self.assertIn("snapshots", revisions)

            request = Request(
                f"{base}/api/chat",
                data=json.dumps({"prompt": "hello"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                chat = json.loads(response.read().decode("utf-8"))
            self.assertEqual(chat["content"], "echo: hello")

            diagnostic_request = Request(
                f"{base}/api/diagnostics",
                data=json.dumps({"level": "health"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(diagnostic_request, timeout=15) as response:
                diagnostic = json.loads(response.read().decode("utf-8"))
            self.assertTrue(diagnostic["ok"])
            self.assertEqual(diagnostic["level"], "health")

            terminal_request = Request(
                f"{base}/api/applications/terminal/run",
                data=json.dumps({"command": "Write-Output terminal-ok", "cwd": ".", "timeout_s": 5}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(terminal_request, timeout=10) as response:
                terminal = json.loads(response.read().decode("utf-8"))
            self.assertEqual(terminal["exit_code"], 0)
            self.assertIn("terminal-ok", terminal["stdout"])
            self.assertFalse(terminal["timed_out"])

            cd_request = Request(
                f"{base}/api/applications/terminal/run",
                data=json.dumps({"command": "cd ..", "cwd": ".", "timeout_s": 5}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(cd_request, timeout=10) as response:
                changed_dir = json.loads(response.read().decode("utf-8"))
            self.assertEqual(changed_dir["exit_code"], 0)
            self.assertEqual(Path(changed_dir["cwd"]).resolve(), Path.cwd().parent.resolve())

            pwd_request = Request(
                f"{base}/api/applications/terminal/run",
                data=json.dumps({"command": "Get-Location", "cwd": changed_dir["cwd"], "timeout_s": 5}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(pwd_request, timeout=10) as response:
                pwd = json.loads(response.read().decode("utf-8"))
            self.assertEqual(Path(pwd["cwd"]).resolve(), Path.cwd().parent.resolve())
            self.assertIn(str(Path.cwd().parent), pwd["stdout"])

            bad_terminal_request = Request(
                f"{base}/api/applications/terminal/run",
                data=json.dumps({"command": "ello", "cwd": ".", "timeout_s": 5}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(bad_terminal_request, timeout=10) as response:
                bad_terminal = json.loads(response.read().decode("utf-8"))
            self.assertEqual(bad_terminal["exit_code"], 1)
            self.assertIn("ello", bad_terminal["stderr"])
            self.assertNotIn("__mc_exit", bad_terminal["stderr"])
            self.assertNotIn("__MAIN_COMPUTER_CWD__", bad_terminal["stderr"])

            editor_files_request = Request(
                f"{base}/api/applications/editor/files",
                data=json.dumps({"repo_dir": "main_computer_test", "query": "viewport", "limit": 20}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(editor_files_request, timeout=10) as response:
                editor_files = json.loads(response.read().decode("utf-8"))
            self.assertGreaterEqual(editor_files["count"], 1)
            self.assertTrue(any(item["path"] == "main_computer/viewport.py" for item in editor_files["files"]))
            self.assertFalse(any("__pycache__" in item["path"] for item in editor_files["files"]))

            editor_root_request = Request(
                f"{base}/api/applications/editor/files",
                data=json.dumps({"repo_dir": ".", "path": "", "limit": 500}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(editor_root_request, timeout=10) as response:
                editor_root = json.loads(response.read().decode("utf-8"))
            self.assertEqual(editor_root["path"], "")
            self.assertIn("entries", editor_root)
            self.assertTrue(any(item["path"] == "main_computer" and item["kind"] == "dir" for item in editor_root["entries"]))

            editor_dir_request = Request(
                f"{base}/api/applications/editor/files",
                data=json.dumps({"repo_dir": "main_computer_test", "path": "main_computer", "limit": 100}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(editor_dir_request, timeout=10) as response:
                editor_dir = json.loads(response.read().decode("utf-8"))
            self.assertEqual(editor_dir["path"], "main_computer")
            self.assertTrue(any(item["path"] == "main_computer/viewport.py" and item["kind"] == "file" for item in editor_dir["entries"]))

            editor_read_request = Request(
                f"{base}/api/applications/editor/read",
                data=json.dumps({"repo_dir": "main_computer_test", "files": ["TODO.md"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(editor_read_request, timeout=10) as response:
                editor_read = json.loads(response.read().decode("utf-8"))
            self.assertTrue(editor_read["ok"])
            self.assertEqual(editor_read["kind"], "read")
            self.assertIn("--- TODO.md ---", editor_read["stdout"])

            git_target = Path.cwd() / "git_tools_viewport_target.txt"
            git_patch = Path.cwd() / "tools" / "patching" / "patches" / "incoming" / "git_tools_viewport_target.patch"
            git_target.write_text("alpha\n", encoding="utf-8")
            git_patch.write_text(
                """--- a/git_tools_viewport_target.txt
+++ b/git_tools_viewport_target.txt
@@ -1 +1,2 @@
 alpha
+beta
""",
                encoding="utf-8",
            )
            try:
                git_status_request = Request(
                    f"{base}/api/applications/git/status",
                    data=json.dumps({"repo_dir": "."}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(git_status_request, timeout=10) as response:
                    git_status = json.loads(response.read().decode("utf-8"))
                self.assertIn("capabilities", git_status)
                self.assertIn("patching", git_status)

                git_patches_request = Request(
                    f"{base}/api/applications/git/patches",
                    data=json.dumps({}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(git_patches_request, timeout=10) as response:
                    git_patches = json.loads(response.read().decode("utf-8"))
                self.assertIn("ok", git_patches)
                if git_patches.get("ok"):
                    self.assertTrue(any(item["name"] == git_patch.name for item in git_patches["incoming"]))

                    git_patch_read_request = Request(
                        f"{base}/api/applications/git/patch/read",
                        data=json.dumps({"patch_name": git_patch.name}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(git_patch_read_request, timeout=10) as response:
                        git_patch_preview = json.loads(response.read().decode("utf-8"))
                    self.assertIn("git_tools_viewport_target.txt", git_patch_preview["preview"])

                    git_patch_apply_request = Request(
                        f"{base}/api/applications/git/patch/apply",
                        data=json.dumps({"patch_name": git_patch.name, "target_root": ".", "dry_run": True}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(git_patch_apply_request, timeout=15) as response:
                        git_patch_apply = json.loads(response.read().decode("utf-8"))
                    self.assertTrue(git_patch_apply["ok"])
                    self.assertTrue(git_patch_apply["dry_run"])
                    self.assertTrue(git_patch_apply["dry_run_output_dir"])

                    dry_run_name = Path(git_patch_apply["dry_run_output_dir"]).name
                    git_dry_run_read_request = Request(
                        f"{base}/api/applications/git/dry-run/read",
                        data=json.dumps({"run_name": dry_run_name}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(git_dry_run_read_request, timeout=10) as response:
                        git_dry_run = json.loads(response.read().decode("utf-8"))
                    self.assertTrue(git_dry_run["ok"])
                    self.assertTrue(any(item["relative_path"] == "git_tools_viewport_target.txt" for item in git_dry_run["preview_files"]))
                else:
                    self.assertIn("unavailable", str(git_patches.get("error", "")).lower())
            finally:
                if git_patch.exists():
                    git_patch.unlink()
                if git_target.exists():
                    git_target.unlink()

            aider_prepare_request = Request(
                f"{base}/api/applications/aider/prepare",
                data=json.dumps(
                    {
                        "repo_dir": "main_computer_test",
                        "files": ["main_computer/viewport.py"],
                        "instruction": "Prepare a small editor integration change.",
                        "model": "ollama_chat/llama3.1:8b",
                        "dry_run": True,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(aider_prepare_request, timeout=10) as response:
                aider_prepare = json.loads(response.read().decode("utf-8"))
            self.assertTrue(aider_prepare["ok"])
            self.assertTrue(aider_prepare["dry_run"])
            self.assertIn("--dry-run", aider_prepare["command"])
            self.assertIn("--yes-always", aider_prepare["command"])
            self.assertIn("--no-pretty", aider_prepare["command"])
            self.assertIn("--no-show-model-warnings", aider_prepare["command"])
            self.assertIn("--subtree-only", aider_prepare["command"])
            self.assertIn("main_computer/viewport.py", aider_prepare["command"])
            self.assertNotIn("main_computer_test/main_computer/viewport.py", aider_prepare["command"])

            with urlopen(f"{base}/api/applications/aider/context", timeout=10) as response:
                aider_context = json.loads(response.read().decode("utf-8"))
            self.assertTrue(aider_context["ok"])
            self.assertGreaterEqual(aider_context["active"]["entry_count"], 1)
            self.assertEqual(aider_context["active"]["entries"][-1]["kind"], "prepare")
            self.assertTrue(aider_context["active"]["archive_id"])
            self.assertEqual(aider_context["current_archive"]["id"], aider_context["active"]["archive_id"])

            aider_archive_request = Request(
                f"{base}/api/applications/aider/context/archive",
                data=json.dumps({"repo_dir": "main_computer_test", "files": ["main_computer/viewport.py"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(aider_archive_request, timeout=10) as response:
                aider_archive = json.loads(response.read().decode("utf-8"))
            self.assertTrue(aider_archive["ok"])
            self.assertEqual(aider_archive["active"]["entry_count"], 0)
            self.assertTrue(aider_archive["active"]["archive_id"])
            self.assertGreaterEqual(len(aider_archive["archives"]), 1)

            aider_load_request = Request(
                f"{base}/api/applications/aider/context/load",
                data=json.dumps({"archive_id": aider_archive["archived"]["id"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(aider_load_request, timeout=10) as response:
                aider_loaded = json.loads(response.read().decode("utf-8"))
            self.assertTrue(aider_loaded["ok"])
            self.assertGreaterEqual(aider_loaded["active"]["entry_count"], 1)
            self.assertEqual(aider_loaded["active"]["origin_archive_id"], aider_archive["archived"]["id"])
            self.assertTrue(aider_loaded["active"]["archive_id"])
            self.assertNotEqual(aider_loaded["active"]["archive_id"], aider_archive["archived"]["id"])

            aider_reset_request = Request(
                f"{base}/api/applications/aider/context/reset",
                data=json.dumps({"repo_dir": "main_computer_test", "files": ["main_computer/viewport.py"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(aider_reset_request, timeout=10) as response:
                aider_reset = json.loads(response.read().decode("utf-8"))
            self.assertTrue(aider_reset["ok"])
            self.assertEqual(aider_reset["active"]["entry_count"], 0)
            self.assertTrue(aider_reset["active"]["archive_id"])
            self.assertGreaterEqual(aider_reset["archive_count"], 1)

            aider_log = (Path.cwd() / "aider.log").read_text(encoding="utf-8")
            self.assertIn('"event": "editor_read"', aider_log)
            self.assertIn('"event": "prepare"', aider_log)
            self.assertIn('"TODO.md"', aider_log)
            self.assertIn('"main_computer/viewport.py"', aider_log)
            self.assertIn('"debug_asset": "aider-editor_read-', aider_log)
            self.assertIn('"debug_asset": "aider-prepare-', aider_log)

            debug_manifest = json.loads((Path.cwd() / "debug_assets" / "manifest.json").read_text(encoding="utf-8"))
            editor_assets = [name for name, meta in debug_manifest.items() if meta.get("kind") == "aider-editor_read"]
            prepare_assets = [name for name, meta in debug_manifest.items() if meta.get("kind") == "aider-prepare"]
            self.assertTrue(editor_assets)
            self.assertTrue(prepare_assets)
            editor_artifact = (Path.cwd() / "debug_assets" / editor_assets[-1]).read_text(encoding="utf-8")
            self.assertIn('"event": "editor_read"', editor_artifact)
            self.assertIn('"TODO.md"', editor_artifact)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            aider_context_tempdir.cleanup()

    def test_control_panel_runtime_service_reports_viewport_port(self) -> None:
        config = MainComputerConfig(workspace=__import__("pathlib").Path.cwd().parent)
        server = ViewportServer(("127.0.0.1", 0), config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(f"{base}/api/control-panel/status", timeout=5) as response:
                control_panel = json.loads(response.read().decode("utf-8"))
            runtime_service = next(service for service in control_panel["services"] if service["id"] == "runtime")
            self.assertEqual(runtime_service["port"], server.server_port)
            self.assertEqual(runtime_service["probe"]["port"], server.server_port)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
