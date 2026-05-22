from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.viewport import APPLICATIONS_INDEX_HTML, ViewportServer, _application_route_target


FIXTURE_WINDOWS_USER = "fixture-user"
FIXTURE_DESKTOP_RELATIVE = f"Users/{FIXTURE_WINDOWS_USER}/Desktop"
FIXTURE_DESKTOP_NOTE_RELATIVE = f"{FIXTURE_DESKTOP_RELATIVE}/notes.txt"


class ViewportFileExplorerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)
        (self.repo / "main_computer").mkdir()
        (self.repo / "main_computer" / "app.py").write_text("print('hello')\n", encoding="utf-8")
        (self.repo / "notes.md").write_text("# Notes\n", encoding="utf-8")
        (self.repo / "budget.csv").write_text("A,B\n1,2\n", encoding="utf-8")
        (self.repo / "binary.bin").write_bytes(b"\x00\x01\x02\x03")
        (self.repo / "large.txt").write_text("x" * (600 * 1024), encoding="utf-8")
        (self.repo / "z-file.txt").write_text("z\n", encoding="utf-8")
        (self.repo / "a-dir").mkdir()
        game_projects = self.repo / "game_projects"
        game_projects.mkdir()
        (game_projects / "project.json").write_text("{}", encoding="utf-8")
        self.server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=self.repo), verbose=False)
        self.server.debug_root = self.repo.resolve()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tempdir.cleanup()

    def _get(self, path: str) -> dict[str, object]:
        with urlopen(f"{self.base}{path}", timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post(self, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        return self._post_to(self.base, path, payload)

    @staticmethod
    def _post_to(base: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        request = Request(
            f"{base}{path}",
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_error(self, path: str, payload: dict[str, object] | None = None) -> HTTPError:
        request = Request(
            f"{self.base}{path}",
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=5)
        return raised.exception

    def test_page_hooks_and_route(self) -> None:
        for text in [
            "File Explorer",
            'data-app="file-explorer"',
            'id="file-explorer-app"',
            'id="file-explorer-roots"',
            'id="file-explorer-path"',
            'id="file-explorer-list"',
            'id="file-explorer-preview"',
            'id="file-explorer-search"',
            'id="file-explorer-status"',
            "/api/applications/file-explorer/roots",
            "/api/applications/file-explorer/list",
            "/api/applications/file-explorer/read",
            "/api/applications/file-explorer/search",
            'data-file-explorer-tree',
            "FILE_EXPLORER_WUNDERBAUM_VERSION",
            "cdn.jsdelivr.net/gh/mar10/wunderbaum",
            "function systemFileExplorerWunderbaumConstructor()",
            "function systemFileExplorerLoadWunderbaum()",
            "function systemFileExplorerEntryToTreeNode(entry = {}, index = 0)",
            "function systemFileExplorerEntryNodeData(entry = {}, index = 0)",
            "fileExplorerEntry: normalizedEntry",
            "entries.map((entry, index) => systemFileExplorerEntryToTreeNode(entry, index))",
            "file-explorer:${ordinal}:${kind}:${path}:${title}",
            "function systemFileExplorerEntryFromNodeData(node = {})",
            "function systemFileExplorerEntryFromTreeEvent(event = {})",
            "function systemFileExplorerPreviewTreeEvent(event = {})",
            "data.fileExplorerEntry",
            "node.fileExplorerEntry",
            "click: (event) =>",
            "select: (event) =>",
            "function systemFileExplorerCreateWunderbaumHost(token)",
            "function systemFileExplorerRenderStillCurrent(token, host)",
            "function systemFileExplorerSelectLocation(rootId, relativePath = \"\")",
            "function systemFileExplorerSizeWunderbaum(element)",
            "element: treeHost",
            "new Wunderbaum({",
            "systemFileExplorerSelectLocation(root.id, \"\")",
            "Wunderbaum unavailable; using fallback list",
            ".file-explorer-list",
            ".file-explorer-list.wunderbaum",
            ".file-explorer-wunderbaum-host",
            ".file-explorer-fallback-list",
            ".file-explorer-list-with-wunderbaum",
            "overflow: hidden !important",
            ".file-explorer-list.wunderbaum .wb-row.wb-active .wb-title",
            "background: #123247 !important",
            "color: #ffffff !important",
            "Loading Wunderbaum file tree",
            "grid-auto-rows: max-content",
            "align-content: start",
        ]:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)
        self.assertEqual(_application_route_target("/applications/file-explorer"), "file-explorer")
        self.assertIsNone(_application_route_target("/applications/not-a-real-app"))

    def test_roots_endpoint_returns_workspace(self) -> None:
        data = self._post("/api/applications/file-explorer/roots")
        root_ids = {item["id"] for item in data["roots"]}

        self.assertTrue(data["ok"])
        self.assertIn("workspace", root_ids)
        self.assertTrue(any(item["main_computer_purview"] for item in data["roots"]))

    def test_path_mounts_endpoint_defaults_to_local_disabled(self) -> None:
        data = self._get("/api/path-mounts")

        self.assertTrue(data["ok"])
        self.assertEqual(data["path_mode"], "local")
        self.assertFalse(data["enabled"])
        self.assertEqual(data["count"], 0)

    def test_mounted_windows_mode_lists_and_reads_configured_drive(self) -> None:
        mount_tempdir = tempfile.TemporaryDirectory()
        server: ViewportServer | None = None
        thread: threading.Thread | None = None
        try:
            mounted_root = Path(mount_tempdir.name)
            docs = mounted_root / "Users" / FIXTURE_WINDOWS_USER / "Desktop"
            docs.mkdir(parents=True)
            (docs / "notes.txt").write_text("mounted hello\n", encoding="utf-8")
            config = MainComputerConfig(
                workspace=self.repo,
                path_mode="mounted-windows",
                host_os="windows",
                windows_drive_mounts=f"Z={mounted_root}",
            )
            server = ViewportServer(("127.0.0.1", 0), config, verbose=False)
            server.debug_root = self.repo.resolve()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"

            mounts = json.loads(urlopen(f"{base}/api/path-mounts", timeout=5).read().decode("utf-8"))
            roots = self._post_to(base, "/api/applications/file-explorer/roots")
            listed = self._post_to(base, "/api/applications/file-explorer/list", {"root_id": "drive-z", "relative_path": FIXTURE_DESKTOP_RELATIVE})
            read = self._post_to(base, "/api/applications/file-explorer/read", {"root_id": "drive-z", "relative_path": FIXTURE_DESKTOP_NOTE_RELATIVE})

            self.assertTrue(mounts["enabled"])
            self.assertEqual(mounts["path_mode"], "mounted-windows")
            self.assertIn("drive-z", {item["id"] for item in roots["roots"]})
            self.assertEqual(listed["relative_path"], FIXTURE_DESKTOP_RELATIVE)
            self.assertEqual(listed["entries"][0]["path_display"], rf"Z:\Users\{FIXTURE_WINDOWS_USER}\Desktop\notes.txt")
            self.assertIn("mounted hello", read["content"])
            self.assertEqual(read["entry"]["path_display"], rf"Z:\Users\{FIXTURE_WINDOWS_USER}\Desktop\notes.txt")
            self.assertTrue(read["entry"]["mounted_windows_drive"])
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None:
                thread.join(timeout=5)
            mount_tempdir.cleanup()

    def test_windows_host_drive_root_lists_and_reads_c_drive(self) -> None:
        mount_tempdir = tempfile.TemporaryDirectory()
        server: ViewportServer | None = None
        thread: threading.Thread | None = None
        try:
            host_root = Path(mount_tempdir.name) / "host"
            docs = host_root / "c" / "Users" / FIXTURE_WINDOWS_USER / "Desktop"
            docs.mkdir(parents=True)
            (docs / "notes.txt").write_text("mounted hello\n", encoding="utf-8")
            config = MainComputerConfig(
                workspace=self.repo,
                path_mode="mounted-windows",
                host_os="windows",
                host_drive_root=host_root,
            )
            server = ViewportServer(("127.0.0.1", 0), config, verbose=False)
            server.debug_root = self.repo.resolve()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"

            mounts = json.loads(urlopen(f"{base}/api/path-mounts", timeout=5).read().decode("utf-8"))
            roots = self._post_to(base, "/api/applications/file-explorer/roots")
            listed = self._post_to(base, "/api/applications/file-explorer/list", {"root_id": "drive-c", "relative_path": FIXTURE_DESKTOP_RELATIVE})
            read = self._post_to(base, "/api/applications/file-explorer/read", {"root_id": "drive-c", "relative_path": FIXTURE_DESKTOP_NOTE_RELATIVE})

            self.assertTrue(mounts["enabled"])
            self.assertEqual(mounts["path_mode"], "mounted-windows")
            self.assertIn("drive-c", {item["id"] for item in roots["roots"]})
            self.assertEqual(listed["entries"][0]["path_display"], rf"C:\Users\{FIXTURE_WINDOWS_USER}\Desktop\notes.txt")
            self.assertIn("mounted hello", read["content"])
            self.assertEqual(read["entry"]["path_display"], rf"C:\Users\{FIXTURE_WINDOWS_USER}\Desktop\notes.txt")
            self.assertTrue(read["entry"]["mounted_windows_drive"])
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None:
                thread.join(timeout=5)
            mount_tempdir.cleanup()


    def test_list_returns_sorted_classified_entries(self) -> None:
        data = self._post("/api/applications/file-explorer/list", {"root_id": "workspace", "relative_path": ""})
        entries = data["entries"]
        names = [entry["name"] for entry in entries]
        app_py = next(entry for entry in entries if entry["name"] == "main_computer")
        markdown = next(entry for entry in entries if entry["name"] == "notes.md")
        csv_entry = next(entry for entry in entries if entry["name"] == "budget.csv")
        game_dir = next(entry for entry in entries if entry["name"] == "game_projects")

        self.assertLess(names.index("a-dir"), names.index("budget.csv"))
        self.assertEqual(app_py["kind"], "directory")
        self.assertTrue(app_py["main_computer_purview"])
        self.assertEqual(markdown["category"], "text")
        self.assertEqual(markdown["suggested_app"], "document")
        self.assertEqual(csv_entry["category"], "spreadsheet")
        self.assertEqual(csv_entry["suggested_app"], "spreadsheet")
        self.assertEqual(game_dir["category"], "game")
        self.assertEqual(game_dir["suggested_app"], "game-editor")

    def test_list_preserves_alias_relative_paths(self) -> None:
        documents = self.repo / "Documents"
        documents.mkdir()
        alias = self.repo / "My Documents"
        try:
            os.symlink(documents, alias, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"directory symlink unavailable: {exc}")

        data = self._post("/api/applications/file-explorer/list", {"root_id": "workspace", "relative_path": ""})
        entries_by_name = {entry["name"]: entry for entry in data["entries"]}

        self.assertEqual(entries_by_name["Documents"]["relative_path"], "Documents")
        self.assertEqual(entries_by_name["My Documents"]["relative_path"], "My Documents")
        self.assertEqual(
            len({entries_by_name["Documents"]["relative_path"], entries_by_name["My Documents"]["relative_path"]}),
            2,
        )

    def test_nested_code_and_game_classification(self) -> None:
        code = self._post("/api/applications/file-explorer/list", {"root_id": "workspace", "relative_path": "main_computer"})
        app_py = next(entry for entry in code["entries"] if entry["name"] == "app.py")
        game = self._post("/api/applications/file-explorer/list", {"root_id": "workspace", "relative_path": "game_projects"})
        project = next(entry for entry in game["entries"] if entry["name"] == "project.json")

        self.assertEqual(app_py["category"], "code")
        self.assertEqual(app_py["suggested_app"], "code-editor")
        self.assertTrue(app_py["main_computer_purview"])
        self.assertEqual(project["category"], "game")
        self.assertEqual(project["suggested_app"], "game-editor")

    def test_read_text_and_refuse_directory_binary_large(self) -> None:
        text = self._post("/api/applications/file-explorer/read", {"root_id": "workspace", "relative_path": "notes.md"})
        binary = self._post("/api/applications/file-explorer/read", {"root_id": "workspace", "relative_path": "binary.bin"})
        large = self._post("/api/applications/file-explorer/read", {"root_id": "workspace", "relative_path": "large.txt"})

        self.assertTrue(text["readable"])
        self.assertIn("# Notes", text["content"])
        self.assertFalse(binary["readable"])
        self.assertIn("binary", binary["reason"])
        self.assertFalse(large["readable"])
        self.assertIn("large", large["reason"])
        self.assertEqual(self._post_error("/api/applications/file-explorer/read", {"root_id": "workspace", "relative_path": "a-dir"}).code, 400)

    def test_traversal_and_absolute_paths_rejected(self) -> None:
        for relative_path in ["../escape.txt", "nested/../../escape.txt", str((self.repo / "notes.md").resolve())]:
            with self.subTest(relative_path=relative_path):
                self.assertEqual(self._post_error("/api/applications/file-explorer/list", {"root_id": "workspace", "relative_path": relative_path}).code, 400)

    def test_search_is_bounded_and_classified(self) -> None:
        data = self._post("/api/applications/file-explorer/search", {"root_id": "workspace", "relative_path": "", "query": "notes", "limit": 10})

        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["name"], "notes.md")
        self.assertEqual(data["results"][0]["category"], "text")


if __name__ == "__main__":
    unittest.main()
