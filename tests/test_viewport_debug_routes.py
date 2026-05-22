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


class ViewportDebugRouteTests(unittest.TestCase):
    def test_debug_indexes_contain_separate_interfaces(self) -> None:
        self.assertIn("Main Computer Text Debug", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("Graphical Debug", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("/api/ollama-debug/status", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("/api/ollama-debug/chat", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("/api/ollama-debug/read", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("/api/ollama-debug/write", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("/api/ollama-debug/revise", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("/api/debug-assets/write", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("/api/debug-assets/read", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("/api/debug-assets/delete", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("/api/debug-assets/reset", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("/api/debug-assets/history", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("/api/debug-assets", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("auto-name on save", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("debug-asset-options", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("asset-nav", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("renderAssetNav", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("refreshAssetList", DEBUG_TEXT_INDEX_HTML)
        self.assertNotIn('value="scan-fractals.txt"', DEBUG_TEXT_INDEX_HTML)
        self.assertIn("inferDebugReadPath", DEBUG_TEXT_INDEX_HTML)
        self.assertIn('return "TODO.md"', DEBUG_TEXT_INDEX_HTML)
        self.assertIn("loaded ${data.path}\\n\\n${data.content}", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("data-fullscreen-target", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("requestFullscreen", DEBUG_TEXT_INDEX_HTML)
        self.assertIn(".fullscreen-widget:not(:fullscreen)", DEBUG_TEXT_INDEX_HTML)
        self.assertIn("padding-top: 42px", DEBUG_TEXT_INDEX_HTML)

        self.assertIn("Main Computer Graphical Debug", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("Text Debug", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("/api/ollama-debug/status", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("/api/ollama-debug/chat", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("/api/ollama-debug/read", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("/api/ollama-debug/write", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("/api/ollama-debug/revise", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("/api/debug-assets/write", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("/api/debug-assets/read", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("/api/debug-assets/delete", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("/api/debug-assets/reset", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("/api/debug-assets/history", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("/api/debug-assets", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("auto-name on save", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("debug-asset-options", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("asset-nav", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("renderAssetNav", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("refreshAssetList", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertNotIn('value="scan-fractals.txt"', DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("inferDebugReadPath", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn('return "TODO.md"', DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("loaded ${data.path}\\n\\n${data.content}", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("data-fullscreen-target", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("requestFullscreen", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn(".fullscreen-widget:not(:fullscreen)", DEBUG_GRAPHICAL_INDEX_HTML)
        self.assertIn("padding-top: 42px", DEBUG_GRAPHICAL_INDEX_HTML)

    def test_workspace_timestamp_uses_debug_root_core_files(self) -> None:
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            debug_root = Path(tmpdir)
            workspace = debug_root / "external-workspace"
            workspace.mkdir()
            (workspace / "far_newer.py").write_text("print('workspace')\n", encoding="utf-8")
            core_dir = debug_root / "main_computer"
            core_dir.mkdir()
            core_file = core_dir / "router.py"
            core_file.write_text("print('debug root')\n", encoding="utf-8")
            old_time = 1_700_000_100
            newer_time = 1_700_000_200
            os.utime(core_file, (old_time, old_time))
            os.utime(workspace / "far_newer.py", (newer_time, newer_time))
            os.chdir(debug_root)
            try:
                server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=workspace), verbose=False)
                handler = ViewportHandler.__new__(ViewportHandler)
                handler.server = server
                timestamp = handler._workspace_timestamp()
                self.assertEqual(timestamp["workspace"], str(debug_root.resolve()))
                self.assertEqual(timestamp["latest_path"], str(core_file.resolve()))
                self.assertEqual(timestamp["latest_mtime_ms"], int(old_time * 1000))
                server.server_close()
            finally:
                os.chdir(old_cwd)

    def test_workspace_timestamp_ignores_root_mtime_and_non_core_files(self) -> None:
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            debug_root = Path(tmpdir)
            core_dir = debug_root / "main_computer"
            core_dir.mkdir()
            core_file = core_dir / "viewport.py"
            core_file.write_text("print('core')\n", encoding="utf-8")
            readme = debug_root / "README.md"
            readme.write_text("# docs\n", encoding="utf-8")
            noisy_dir = debug_root / "diagnostics_output_live"
            noisy_dir.mkdir()
            noisy_file = noisy_dir / "report.json"
            noisy_file.write_text('{"ok": true}\n', encoding="utf-8")

            core_time = 1_700_000_300
            ignored_time = 1_700_000_900
            os.utime(core_file, (core_time, core_time))
            os.utime(readme, (ignored_time, ignored_time))
            os.utime(noisy_file, (ignored_time, ignored_time))
            os.utime(noisy_dir, (ignored_time, ignored_time))
            os.utime(debug_root, (ignored_time, ignored_time))

            os.chdir(debug_root)
            try:
                server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=debug_root / "workspace"), verbose=False)
                handler = ViewportHandler.__new__(ViewportHandler)
                handler.server = server
                timestamp = handler._workspace_timestamp()
                self.assertEqual(timestamp["latest_path"], str(core_file.resolve()))
                self.assertEqual(timestamp["latest_mtime_ms"], int(core_time * 1000))
                server.server_close()
            finally:
                os.chdir(old_cwd)

    def test_workspace_timestamp_tracks_critical_resource_files(self) -> None:
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            debug_root = Path(tmpdir)
            (debug_root / "main_computer").mkdir()
            init_file = debug_root / "main_computer" / "__init__.py"
            init_file.write_text("", encoding="utf-8")
            requirements = debug_root / "requirements.txt"
            requirements.write_text("fastapi\n", encoding="utf-8")
            older_time = 1_700_000_950
            resource_time = 1_700_001_000
            os.utime(init_file, (older_time, older_time))
            os.utime(requirements, (resource_time, resource_time))

            os.chdir(debug_root)
            try:
                server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=debug_root), verbose=False)
                handler = ViewportHandler.__new__(ViewportHandler)
                handler.server = server
                timestamp = handler._workspace_timestamp()
                self.assertEqual(timestamp["latest_path"], str(requirements.resolve()))
                self.assertEqual(timestamp["latest_mtime_ms"], int(resource_time * 1000))
                server.server_close()
            finally:
                os.chdir(old_cwd)

    def test_ollama_debug_mode_can_read_and_write_project_files(self) -> None:
        config = MainComputerConfig(workspace=Path.cwd().parent)
        server = ViewportServer(("127.0.0.1", 0), config)
        tempdir = tempfile.TemporaryDirectory()
        server.debug_root = Path(tempdir.name).resolve()
        server.debug_assets_root = server.debug_root / "debug_assets"
        server.debug_asset_revisions = DebugAssetRevisionControl(
            server.debug_assets_root,
            server.debug_root / "debug_asset_revisions",
        )
        server.revisions = RevisionControl(server.debug_root, server.debug_root / "revision_control")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            enable = Request(
                f"{base}/api/ollama-debug/session",
                data=json.dumps({"action": "enable"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(enable, timeout=5) as response:
                enabled = json.loads(response.read().decode("utf-8"))
            self.assertTrue(enabled["active"])
            self.assertTrue(enabled["can_self_edit"])

            write = Request(
                f"{base}/api/ollama-debug/write",
                data=json.dumps({"path": "scratch.py", "content": "print('debug')\n"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(write, timeout=5) as response:
                written = json.loads(response.read().decode("utf-8"))
            self.assertEqual(written["path"], "scratch.py")

            read = Request(
                f"{base}/api/ollama-debug/read",
                data=json.dumps({"path": "scratch.py"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(read, timeout=5) as response:
                loaded = json.loads(response.read().decode("utf-8"))
            self.assertEqual(loaded["content"], "print('debug')\n")

            asset_write = Request(
                f"{base}/api/debug-assets/write",
                data=json.dumps({"name": "scan fractals", "content": "fractal result", "kind": "scan"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(asset_write, timeout=5) as response:
                asset = json.loads(response.read().decode("utf-8"))
            self.assertEqual(asset["name"], "scan_fractals.txt")

            class FakeAssetNameProvider:
                def __init__(self, **kwargs: object) -> None:
                    self.model = str(kwargs.get("model", "fake-model"))

                def chat(self, messages: object) -> ChatResponse:
                    return ChatResponse(content="Fractal Scan Summary.txt", provider="fake", model=self.model)

            with patch("main_computer.viewport.OllamaProvider", FakeAssetNameProvider):
                generated_asset_write = Request(
                    f"{base}/api/debug-assets/write",
                    data=json.dumps({"name": "", "content": "generated artifact", "kind": "debug-note"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(generated_asset_write, timeout=5) as response:
                    generated_asset = json.loads(response.read().decode("utf-8"))
                self.assertEqual(generated_asset["name"], "fractal_scan_summary.txt")
                self.assertEqual(generated_asset["name_source"], "ollama")

                generated_collision_write = Request(
                    f"{base}/api/debug-assets/write",
                    data=json.dumps({"name": "", "auto_name": True, "content": "generated artifact again", "kind": "debug-note"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(generated_collision_write, timeout=5) as response:
                    generated_collision = json.loads(response.read().decode("utf-8"))
                self.assertEqual(generated_collision["name"], "fractal_scan_summary-2.txt")
                self.assertEqual(generated_collision["name_source"], "ollama")

            with urlopen(f"{base}/api/debug-assets", timeout=5) as response:
                assets = json.loads(response.read().decode("utf-8"))
            asset_names = [item["name"] for item in assets["assets"]]
            self.assertIn("scan_fractals.txt", asset_names)
            self.assertIn(generated_asset["name"], asset_names)
            self.assertIn(generated_collision["name"], asset_names)

            asset_read = Request(
                f"{base}/api/debug-assets/read",
                data=json.dumps({"name": "scan_fractals.txt"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(asset_read, timeout=5) as response:
                loaded_asset = json.loads(response.read().decode("utf-8"))
            self.assertEqual(loaded_asset["content"], "fractal result")

            asset_snapshot = Request(
                f"{base}/api/debug-assets/history/snapshot",
                data=json.dumps({"label": "asset state one"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(asset_snapshot, timeout=5) as response:
                asset_state = json.loads(response.read().decode("utf-8"))
            asset_state_id = asset_state["created"]["id"]

            asset_delete = Request(
                f"{base}/api/debug-assets/delete",
                data=json.dumps({"name": "scan_fractals.txt"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(asset_delete, timeout=5) as response:
                deleted_asset = json.loads(response.read().decode("utf-8"))
            self.assertTrue(deleted_asset["deleted"])

            asset_restore = Request(
                f"{base}/api/debug-assets/history/restore",
                data=json.dumps({"id": asset_state_id}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(asset_restore, timeout=5) as response:
                restored_assets = json.loads(response.read().decode("utf-8"))
            self.assertTrue(restored_assets["restored"])

            with urlopen(asset_read, timeout=5) as response:
                restored_asset = json.loads(response.read().decode("utf-8"))
            self.assertEqual(restored_asset["content"], "fractal result")

            asset_reset = Request(
                f"{base}/api/debug-assets/reset",
                data=json.dumps({"label": "reset test"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(asset_reset, timeout=5) as response:
                reset_assets = json.loads(response.read().decode("utf-8"))
            self.assertTrue(reset_assets["reset"])
            self.assertEqual(reset_assets["assets"], [])

            second_asset_write = Request(
                f"{base}/api/debug-assets/write",
                data=json.dumps({"name": "system_moment.txt", "content": "system moment", "kind": "test"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(second_asset_write, timeout=5) as response:
                system_asset = json.loads(response.read().decode("utf-8"))
            self.assertEqual(system_asset["name"], "system_moment.txt")

            snapshot_request = Request(
                f"{base}/api/revisions/snapshot",
                data=json.dumps({"label": "debug checkpoint"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(snapshot_request, timeout=20) as response:
                snapshot = json.loads(response.read().decode("utf-8"))
            self.assertEqual(snapshot["created"]["label"], "debug checkpoint")
            self.assertTrue(snapshot["created"]["metadata"]["debug_asset_snapshot_id"])

            later_asset_write = Request(
                f"{base}/api/debug-assets/write",
                data=json.dumps({"name": "system_moment.txt", "content": "later asset", "kind": "test"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(later_asset_write, timeout=5) as response:
                json.loads(response.read().decode("utf-8"))
            after_later_asset_state = Request(
                f"{base}/api/debug-assets/history/snapshot",
                data=json.dumps({"label": "later asset visible state"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(after_later_asset_state, timeout=5) as response:
                later_visible_asset_state = json.loads(response.read().decode("utf-8"))["created"]["id"]

            system_restore = Request(
                f"{base}/api/revisions/restore-system",
                data=json.dumps({"id": snapshot["created"]["id"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(system_restore, timeout=5) as response:
                restored_system = json.loads(response.read().decode("utf-8"))
            self.assertTrue(restored_system["restored"])
            self.assertTrue(restored_system["debug_assets"]["restored"])

            restored_asset_read = Request(
                f"{base}/api/debug-assets/read",
                data=json.dumps({"name": "system_moment.txt"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(restored_asset_read, timeout=5) as response:
                system_moment_asset = json.loads(response.read().decode("utf-8"))
            self.assertEqual(system_moment_asset["content"], "system moment")

            later_asset_restore = Request(
                f"{base}/api/debug-assets/history/restore",
                data=json.dumps({"id": later_visible_asset_state}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(later_asset_restore, timeout=5) as response:
                restored_later_asset = json.loads(response.read().decode("utf-8"))
            self.assertTrue(restored_later_asset["restored"])
            with urlopen(restored_asset_read, timeout=5) as response:
                later_visible_asset = json.loads(response.read().decode("utf-8"))
            self.assertEqual(later_visible_asset["content"], "later asset")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            tempdir.cleanup()

    def test_ollama_debug_read_can_resolve_workspace_priority_project_files(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        workspace = Path(tempdir.name).resolve()
        debug_root = workspace / "main_computer_test"
        source_root = workspace / "main_computer"
        debug_root.mkdir()
        source_root.mkdir()
        (source_root / "TODO.md").write_text("# TODO\n\n- workspace todo\n", encoding="utf-8")

        config = MainComputerConfig(workspace=workspace)
        server = ViewportServer(("127.0.0.1", 0), config)
        server.debug_root = debug_root
        server.debug_assets_root = server.debug_root / "debug_assets"
        server.debug_asset_revisions = DebugAssetRevisionControl(
            server.debug_assets_root,
            server.debug_root / "debug_asset_revisions",
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            enable = Request(
                f"{base}/api/ollama-debug/session",
                data=json.dumps({"action": "enable"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(enable, timeout=5) as response:
                enabled = json.loads(response.read().decode("utf-8"))
            self.assertTrue(enabled["active"])

            read = Request(
                f"{base}/api/ollama-debug/read",
                data=json.dumps({"path": "main_computer/TODO.md"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(read, timeout=5) as response:
                loaded = json.loads(response.read().decode("utf-8"))
            self.assertEqual(loaded["path"], "main_computer/TODO.md")
            self.assertIn("workspace todo", loaded["content"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            tempdir.cleanup()

    def test_ollama_debug_read_treats_package_prefix_as_project_root_for_root_files(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        workspace = Path(tempdir.name).resolve()
        debug_root = workspace / "main_computer_test"
        package_root = debug_root / "main_computer"
        package_root.mkdir(parents=True)
        (debug_root / "TODO.md").write_text("# TODO\n\n- root todo\n", encoding="utf-8")
        (package_root / "viewport.py").write_text("VIEWPORT = True\n", encoding="utf-8")

        config = MainComputerConfig(workspace=workspace)
        server = ViewportServer(("127.0.0.1", 0), config)
        server.debug_root = debug_root
        server.debug_assets_root = server.debug_root / "debug_assets"
        server.debug_asset_revisions = DebugAssetRevisionControl(
            server.debug_assets_root,
            server.debug_root / "debug_asset_revisions",
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            enable = Request(
                f"{base}/api/ollama-debug/session",
                data=json.dumps({"action": "enable"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(enable, timeout=5):
                pass

            root_read = Request(
                f"{base}/api/ollama-debug/read",
                data=json.dumps({"path": "main_computer/TODO.md"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(root_read, timeout=5) as response:
                loaded_root = json.loads(response.read().decode("utf-8"))
            self.assertEqual(loaded_root["path"], "TODO.md")
            self.assertIn("root todo", loaded_root["content"])

            package_read = Request(
                f"{base}/api/ollama-debug/read",
                data=json.dumps({"path": "main_computer/viewport.py"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(package_read, timeout=5) as response:
                loaded_package = json.loads(response.read().decode("utf-8"))
            self.assertEqual(loaded_package["path"], "main_computer/viewport.py")
            self.assertIn("VIEWPORT", loaded_package["content"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            tempdir.cleanup()

    def test_revision_control_snapshots_diffs_and_restores_files(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        try:
            root = Path(tempdir.name)
            sample = root / "sample.txt"
            sample.write_text("one\n", encoding="utf-8")
            revisions = RevisionControl(root, root / "revision_control")

            created = revisions.create_snapshot(label="first", reason="test")
            snapshot_id = created["created"]["id"]
            sample.write_text("two\n", encoding="utf-8")

            diff = revisions.diff_snapshot(snapshot_id, "sample.txt")
            self.assertIn("-one", diff["diff"])
            self.assertIn("+two", diff["diff"])

            restored = revisions.restore_file(snapshot_id, "sample.txt")
            self.assertTrue(restored["restored"])
            self.assertEqual(sample.read_text(encoding="utf-8"), "one\n")
        finally:
            tempdir.cleanup()

    def test_debug_asset_revision_control_restores_assets_without_project_files(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        try:
            root = Path(tempdir.name)
            assets = root / "debug_assets"
            assets.mkdir()
            (assets / "note.txt").write_text("alpha", encoding="utf-8")
            project_file = root / "main_computer.py"
            project_file.write_text("project stays put", encoding="utf-8")
            history = DebugAssetRevisionControl(assets, root / "debug_asset_revisions")

            state = history.create_snapshot(label="alpha", reason="test")
            state_id = state["created"]["id"]
            (assets / "note.txt").write_text("beta", encoding="utf-8")
            reset = history.reset(label="before clear")
            self.assertTrue(reset["reset"])
            self.assertFalse((assets / "note.txt").exists())

            restored = history.restore(state_id)
            self.assertTrue(restored["restored"])
            self.assertEqual((assets / "note.txt").read_text(encoding="utf-8"), "alpha")
            self.assertEqual(project_file.read_text(encoding="utf-8"), "project stays put")
        finally:
            tempdir.cleanup()


if __name__ == "__main__":
    unittest.main()
