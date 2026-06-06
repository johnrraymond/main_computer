from __future__ import annotations

import base64
from dataclasses import replace
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main_computer.cli import build_parser, _config_from_args
from main_computer.config import MainComputerConfig
from main_computer.viewport import APPLICATIONS_INDEX_HTML, ViewportServer, _application_route_target


class ViewportOnlyOfficeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)
        self.server = ViewportServer(
            ("127.0.0.1", 0),
            MainComputerConfig(
                workspace=self.repo,
                onlyoffice_storage_root=Path("runtime/onlyoffice-test/workbooks"),
                onlyoffice_mode="docker",
                onlyoffice_public_url="http://127.0.0.1:18085",
                onlyoffice_internal_url="http://127.0.0.1:18085",
                onlyoffice_jwt_enabled=False,
                onlyoffice_jwt_secret=None,
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
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def post_json(self, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        request = Request(
            self.base + path,
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_onlyoffice_app_is_routeable_and_shell_is_included(self) -> None:
        self.assertEqual(_application_route_target("/applications/onlyoffice"), "onlyoffice")
        self.assertIn('data-app="onlyoffice"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="onlyoffice-app"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="onlyoffice-editor-host"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="onlyoffice-server-status"', APPLICATIONS_INDEX_HTML)
        self.assertIn("onlyofficeScheduleEditorViewportFix", APPLICATIONS_INDEX_HTML)
        self.assertIn("onlyofficeCandidateDocumentServerUrls", APPLICATIONS_INDEX_HTML)
        self.assertIn("mainComputerOnlyofficeApi", APPLICATIONS_INDEX_HTML)
        self.assertIn("browser API:", APPLICATIONS_INDEX_HTML)
        self.assertIn("onlyoffice-contained-editor-stage", APPLICATIONS_INDEX_HTML)
        self.assertIn("onlyoffice-contained-editor-frame", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="stage-advanced-toggle"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="stage-advanced-pane"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="onlyoffice-advanced-code-panel"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="onlyoffice-advanced-code-source"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="onlyoffice-advanced-attach-code"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="onlyoffice-advanced-chat-panel"', APPLICATIONS_INDEX_HTML)
        self.assertIn('name="onlyoffice-advanced-code-type" value="javascript"', APPLICATIONS_INDEX_HTML)
        self.assertIn('name="onlyoffice-advanced-code-type" value="python"', APPLICATIONS_INDEX_HTML)
        self.assertIn('name="onlyoffice-advanced-code-type" value="basic"', APPLICATIONS_INDEX_HTML)
        self.assertIn("onlyofficeCloseAdvancedPane", APPLICATIONS_INDEX_HTML)
        self.assertIn("onlyofficeMountAdvancedChat", APPLICATIONS_INDEX_HTML)
        self.assertIn("const mount = api.mountEmbedded || window.chatConsoleMountEmbedded;", APPLICATIONS_INDEX_HTML)
        self.assertIn('embedId: "onlyoffice"', APPLICATIONS_INDEX_HTML)
        self.assertIn('activeApp: "onlyoffice"', APPLICATIONS_INDEX_HTML)
        self.assertIn('notebookId: "onlyoffice-advanced-chat-notebook"', APPLICATIONS_INDEX_HTML)
        self.assertIn("onlyofficeState.advancedChatController", APPLICATIONS_INDEX_HTML)
        self.assertIn("window.MainComputerChatConsole", APPLICATIONS_INDEX_HTML)
        self.assertIn("onlyofficeAttachAdvancedCodeToSelectedCells", APPLICATIONS_INDEX_HTML)
        self.assertIn('http://127.0.0.1:18085', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="spreadsheet-app"', APPLICATIONS_INDEX_HTML)

        code_panel_index = APPLICATIONS_INDEX_HTML.index('id="onlyoffice-advanced-code-panel"')
        chat_panel_index = APPLICATIONS_INDEX_HTML.index('id="onlyoffice-advanced-chat-panel"')
        self.assertLess(code_panel_index, chat_panel_index)

    def test_onlyoffice_advanced_pane_scrolls_chat_without_scrolling_code_area(self) -> None:
        self.assertIn(".stage-advanced-pane", APPLICATIONS_INDEX_HTML)
        self.assertIn("grid-template-rows: auto auto minmax(0, 1fr);", APPLICATIONS_INDEX_HTML)
        self.assertIn(".stage-advanced-code", APPLICATIONS_INDEX_HTML)
        self.assertIn("overflow: visible;", APPLICATIONS_INDEX_HTML)
        self.assertIn(".stage-advanced-chat", APPLICATIONS_INDEX_HTML)
        self.assertIn("overflow-y: auto;", APPLICATIONS_INDEX_HTML)
        self.assertIn("overflow-x: hidden;", APPLICATIONS_INDEX_HTML)
        self.assertIn("overscroll-behavior: contain;", APPLICATIONS_INDEX_HTML)
        self.assertIn(".stage-advanced-chat .chat-console-notebook", APPLICATIONS_INDEX_HTML)
        self.assertIn("overflow: visible;", APPLICATIONS_INDEX_HTML)

    def test_onlyoffice_advanced_code_addons_attach_to_selected_cells_not_chat_cells(self) -> None:
        self.assertIn('data-stage-advanced-code-type="javascript"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-stage-advanced-code-type="python"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-stage-advanced-code-type="basic"', APPLICATIONS_INDEX_HTML)
        self.assertNotIn("data-stage-advanced-chat-cell", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("onlyofficeAddAdvancedChatCell", APPLICATIONS_INDEX_HTML)
        self.assertIn("onlyofficeSelectedAdvancedCodeType", APPLICATIONS_INDEX_HTML)
        self.assertIn("onlyofficeCreateAdvancedCodeComment", APPLICATIONS_INDEX_HTML)
        self.assertIn("onlyofficeAdvancedConnector", APPLICATIONS_INDEX_HTML)
        self.assertIn("createConnector", APPLICATIONS_INDEX_HTML)
        self.assertIn("Api.GetSelection()", APPLICATIONS_INDEX_HTML)
        self.assertIn("selection.AddComment", APPLICATIONS_INDEX_HTML)
        self.assertIn("Main Computer ONLYOFFICE code add-on", APPLICATIONS_INDEX_HTML)

    def test_onlyoffice_status_reports_docker_urls(self) -> None:
        status = self.post_json("/api/applications/onlyoffice/status", {"probe": False})

        self.assertTrue(status["ok"])
        self.assertEqual(status["mode"], "docker")
        self.assertEqual(status["default_mode"], "docker")
        self.assertEqual(status["public_url"], "http://127.0.0.1:18085")
        self.assertEqual(status["internal_url"], "http://127.0.0.1:18085")
        self.assertEqual(status["callback_base_url"], f"http://host.docker.internal:{self.server.server_port}")
        self.assertEqual(status["public_api_url"], "http://127.0.0.1:18085/web-apps/apps/api/documents/api.js")
        self.assertIn("http://127.0.0.1:18085", status["browser_public_url_candidates"])
        self.assertIn("http://localhost:18085", status["browser_public_url_candidates"])
        self.assertNotIn("http://127.0.0.1:18084", status["browser_public_url_candidates"])
        self.assertIn("server_probe", status)

    def test_onlyoffice_docker_browser_candidates_use_loopback_aliases_only(self) -> None:
        status = self.post_json("/api/applications/onlyoffice/status", {"probe": False})

        self.assertEqual(status["browser_public_url_candidates"][0], "http://127.0.0.1:18085")
        self.assertIn("http://localhost:18085", status["browser_public_url_candidates"])
        self.assertNotIn("http://127.0.0.1:18084", status["browser_public_url_candidates"])

    def test_onlyoffice_browser_public_url_override_uses_configured_docker_url(self) -> None:
        self.post_json("/api/applications/onlyoffice/create", {"path": "Twiddle.xlsx"})
        callback_base = f"http://host.docker.internal:{self.server.server_port}"
        self.server.config = replace(
            self.server.config,
            onlyoffice_browser_public_url="http://127.0.0.1:18085",
            onlyoffice_callback_base_url=callback_base,
        )

        status = self.post_json("/api/applications/onlyoffice/status", {"probe": False})
        config = self.post_json("/api/applications/onlyoffice/config", {"path": "Twiddle.xlsx"})

        self.assertTrue(status["ok"])
        self.assertEqual(status["mode"], "docker")
        self.assertEqual(status["configured_public_url"], "http://127.0.0.1:18085")
        self.assertEqual(status["browser_public_url_override"], "http://127.0.0.1:18085")
        self.assertEqual(status["public_url"], "http://127.0.0.1:18085")
        self.assertEqual(status["internal_url"], "http://127.0.0.1:18085")
        self.assertEqual(status["callback_base_url"], callback_base)
        self.assertEqual(status["public_api_url"], "http://127.0.0.1:18085/web-apps/apps/api/documents/api.js")
        self.assertEqual(status["browser_public_url_candidates"][0], "http://127.0.0.1:18085")
        self.assertIn("http://localhost:18085", status["browser_public_url_candidates"])

        self.assertTrue(config["ok"])
        self.assertEqual(config["public_url"], "http://127.0.0.1:18085")
        self.assertEqual(config["configured_public_url"], "http://127.0.0.1:18085")
        self.assertEqual(config["internal_url"], "http://127.0.0.1:18085")
        self.assertEqual(config["callback_base_url"], callback_base)
        self.assertTrue(config["config"]["document"]["url"].startswith(callback_base))
        self.assertTrue(config["config"]["editorConfig"]["callbackUrl"].startswith(callback_base))

    def test_onlyoffice_docker_mode_uses_18085_host_callback_and_no_token(self) -> None:
        self.post_json("/api/applications/onlyoffice/create", {"path": "Docker.xlsx"})
        callback_base = f"http://host.docker.internal:{self.server.server_port}"
        self.server.config = replace(
            self.server.config,
            onlyoffice_mode="docker",
            onlyoffice_public_url="http://127.0.0.1:18085",
            onlyoffice_internal_url="http://127.0.0.1:18085",
            onlyoffice_browser_public_url="http://127.0.0.1:18085",
            onlyoffice_callback_base_url=callback_base,
            onlyoffice_jwt_enabled=False,
            onlyoffice_jwt_secret=None,
        )

        status = self.post_json("/api/applications/onlyoffice/status", {"probe": False})
        config = self.post_json("/api/applications/onlyoffice/config", {"path": "Docker.xlsx"})

        self.assertEqual(status["mode"], "docker")
        self.assertEqual(status["public_url"], "http://127.0.0.1:18085")
        self.assertEqual(status["internal_url"], "http://127.0.0.1:18085")
        self.assertEqual(status["callback_base_url"], callback_base)
        self.assertFalse(status["jwt_enabled"])
        self.assertFalse(status["jwt_configured"])

        editor_config = config["config"]
        self.assertNotIn("token", editor_config)
        self.assertTrue(editor_config["document"]["url"].startswith(callback_base))
        self.assertTrue(editor_config["editorConfig"]["callbackUrl"].startswith(callback_base))

    def test_onlyoffice_configured_non_loopback_callback_base_url_wins(self) -> None:
        self.post_json("/api/applications/onlyoffice/create", {"path": "Gateway.xlsx"})
        expected_base = f"http://172.21.0.1:{self.server.server_port}"
        self.server.config = replace(self.server.config, onlyoffice_callback_base_url=expected_base)

        config = self.post_json("/api/applications/onlyoffice/config", {"path": "Gateway.xlsx"})

        self.assertEqual(config["callback_base_url"], expected_base)
        self.assertTrue(config["config"]["document"]["url"].startswith(expected_base))
        self.assertTrue(config["config"]["editorConfig"]["callbackUrl"].startswith(expected_base))

    def test_onlyoffice_configured_loopback_callback_base_url_is_preserved(self) -> None:
        self.post_json("/api/applications/onlyoffice/create", {"path": "Gateway.xlsx"})
        loopback_base = f"http://localhost:{self.server.server_port}"
        self.server.config = replace(self.server.config, onlyoffice_callback_base_url=loopback_base)

        config = self.post_json("/api/applications/onlyoffice/config", {"path": "Gateway.xlsx"})

        self.assertEqual(config["callback_base_url"], loopback_base)
        self.assertTrue(config["config"]["document"]["url"].startswith(loopback_base))
        self.assertTrue(config["config"]["editorConfig"]["callbackUrl"].startswith(loopback_base))

    def test_onlyoffice_docker_default_uses_host_docker_internal_callback_url(self) -> None:
        self.post_json("/api/applications/onlyoffice/create", {"path": "Gateway.xlsx"})
        expected_base = f"http://host.docker.internal:{self.server.server_port}"

        config = self.post_json("/api/applications/onlyoffice/config", {"path": "Gateway.xlsx"})

        self.assertEqual(config["callback_base_url"], expected_base)
        self.assertTrue(config["config"]["document"]["url"].startswith(expected_base))
        self.assertTrue(config["config"]["editorConfig"]["callbackUrl"].startswith(expected_base))

    def test_onlyoffice_browser_loader_uses_plain_api_script_without_cors_mode(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        source = (repo_root / "main_computer/web/applications/scripts/onlyoffice-app.js").read_text(encoding="utf-8")
        self.assertIn("function onlyofficeApiScriptUrl(baseUrl, attempt)", source)
        self.assertNotIn("_mc_onlyoffice_api", source)
        self.assertNotIn("script.crossOrigin", source)
        self.assertIn("browser_public_url_candidates", source)

    def test_onlyoffice_control_scripts_and_compose_use_docker_port(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        for relative in [
            "tools/onlyoffice/onlyoffice-control.ps1",
            "tools/onlyoffice/check-onlyoffice.py",
            "docker-compose.onlyoffice.yml",
        ]:
            self.assertTrue((repo_root / relative).is_file(), relative)

        for relative in [
            "tools/onlyoffice/wsl-install-onlyoffice.sh",
            "tools/onlyoffice/wsl-start-onlyoffice.sh",
            "tools/onlyoffice/wsl-status-onlyoffice.sh",
            "tools/onlyoffice/wsl-stop-onlyoffice.sh",
        ]:
            self.assertFalse((repo_root / relative).exists(), relative)

        control = (repo_root / "tools/onlyoffice/onlyoffice-control.ps1").read_text(encoding="utf-8")
        compose = (repo_root / "docker-compose.onlyoffice.yml").read_text(encoding="utf-8")
        self.assertIn("[int]$Port = 18085", control)
        self.assertIn("[int]$AppPort = 8765", control)
        self.assertIn("Invoke-DockerOnlyOffice \"start\"", control)
        self.assertIn("Invoke-DockerOnlyOffice \"status\"", control)
        self.assertIn("MAIN_COMPUTER_ONLYOFFICE_PORT:-18085", compose)
        self.assertIn("127.0.0.1:${MAIN_COMPUTER_ONLYOFFICE_PORT:-18085}:80", compose)
        self.assertIn("MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED:-false", compose)
        self.assertIn("ALLOW_PRIVATE_IP_ADDRESS", compose)
        self.assertIn("ALLOW_META_IP_ADDRESS", compose)
        self.assertIn("ONLYOFFICE Docker uses 18085", compose)

        forbidden = [
            "netsh interface portproxy add v4tov4",
            "New-NetFirewallRule",
            "Start-Service iphlpsvc",
            "Get-OnlyOfficeBridgeStatus",
            "wsl.exe",
            "bridge-start",
            "bridge-status",
            "bridge-stop",
        ]
        for needle in forbidden:
            self.assertNotIn(needle, control)

    def test_onlyoffice_control_has_no_firewall_or_portproxy_code(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        control = (repo_root / "tools/onlyoffice/onlyoffice-control.ps1").read_text(encoding="utf-8")

        forbidden = [
            "function Test-FirewallRule",
            "function Test-OnlyOfficeBridgeConfigurationReady",
            "Portproxy already present:",
            "Firewall rule already present:",
            "Set-PortProxyEntry",
            "Remove-PortProxyEntry",
            "Remove-FirewallRuleIfPresent",
            "-RemoteAddress LocalSubnet",
            "Invoke-WslCurlProbe",
        ]
        for needle in forbidden:
            self.assertNotIn(needle, control)

    def test_start_v2_ensures_onlyoffice_docker_container(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        launcher = (repo_root / "start_v2.bat").read_text(encoding="utf-8")
        start_stop = (repo_root / "scripts/main-computer-start-stop.ps1").read_text(encoding="utf-8")

        self.assertIn("main-computer-start-stop.ps1", launcher)
        self.assertIn("Invoke-MainComputerOnlyOfficeControl", start_stop)
        self.assertIn('Invoke-MainComputerOnlyOfficeControl $RootPath $launchContext "start"', start_stop)
        self.assertIn('Invoke-MainComputerOnlyOfficeControl $RootPath $launchContext "status"', start_stop)
        self.assertIn("MAIN_COMPUTER_ONLYOFFICE_MODE = \"docker\"", start_stop)
        self.assertIn("MAIN_COMPUTER_ONLYOFFICE_PORT = \"18085\"", start_stop)
        self.assertIn("MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME = \"main-computer-onlyoffice-documentserver\"", start_stop)
        self.assertIn("MAIN_COMPUTER_ONLYOFFICE_BROWSER_PUBLIC_URL = \"http://127.0.0.1:18085\"", start_stop)
        self.assertIn("MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL = \"http://host.docker.internal:$port\"", start_stop)
        self.assertIn("MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED = \"false\"", start_stop)
        self.assertIn("MAIN_COMPUTER_ONLYOFFICE_ALLOW_PRIVATE_IP_ADDRESS = \"true\"", start_stop)
        self.assertIn("Leaving ONLYOFFICE Docker container running for faster next startup.", start_stop)
        self.assertIn("MAIN_COMPUTER_ONLYOFFICE_STOP_ON_STOP", start_stop)

        forbidden = [
            "bridge-status",
            "bridge-stop",
            "MAIN_COMPUTER_ONLYOFFICE_REMOVE_BRIDGES_ON_STOP",
            "ONLYOFFICE WSL bridge",
            "portproxies",
        ]
        for needle in forbidden:
            self.assertNotIn(needle, start_stop)

    def test_onlyoffice_create_files_config_and_file_route(self) -> None:
        created = self.post_json("/api/applications/onlyoffice/create", {"path": "Demo.xlsx"})
        self.assertTrue(created["ok"])
        self.assertEqual(created["path"], "Demo.xlsx")

        files = self.post_json("/api/applications/onlyoffice/files", {})
        self.assertTrue(files["ok"])
        self.assertEqual(files["count"], 1)
        self.assertEqual(files["files"][0]["path"], "Demo.xlsx")

        config = self.post_json("/api/applications/onlyoffice/config", {"path": "Demo.xlsx"})

        self.assertTrue(config["ok"])
        editor_config = config["config"]
        self.assertEqual(editor_config["documentType"], "cell")
        self.assertEqual(editor_config["document"]["fileType"], "xlsx")
        self.assertEqual(config["public_url"], "http://127.0.0.1:18085")
        self.assertEqual(config["internal_url"], "http://127.0.0.1:18085")
        self.assertEqual(config["callback_base_url"], f"http://host.docker.internal:{self.server.server_port}")
        self.assertIn("http://localhost:18085", config["browser_public_url_candidates"])
        self.assertIn("/api/applications/onlyoffice/file?", editor_config["document"]["url"])
        self.assertIn("/api/applications/onlyoffice/callback?", editor_config["editorConfig"]["callbackUrl"])
        self.assertTrue(editor_config["document"]["url"].startswith(f"http://host.docker.internal:{self.server.server_port}"))

        with urlopen(self.base + "/api/applications/onlyoffice/file?path=Demo.xlsx", timeout=5) as response:
            data = response.read()
        self.assertTrue(data.startswith(b"PK"))
        self.assertIn(b"xl/workbook.xml", data)

    def test_onlyoffice_safe_path_rejects_traversal(self) -> None:
        request = Request(
            self.base + "/api/applications/onlyoffice/create",
            data=json.dumps({"path": "../evil.xlsx"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 400)
        payload = json.loads(caught.exception.read().decode("utf-8"))
        self.assertFalse(payload["ok"])

    def test_onlyoffice_callback_status_2_saves_downloaded_xlsx(self) -> None:
        created = self.post_json("/api/applications/onlyoffice/create", {"path": "Source.xlsx"})
        self.assertTrue(created["ok"])
        with urlopen(self.base + "/api/applications/onlyoffice/file?path=Source.xlsx", timeout=5) as response:
            workbook_bytes = response.read()

        data_url = (
            "data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,"
            + base64.b64encode(workbook_bytes).decode("ascii")
        )
        callback = self.post_json(
            "/api/applications/onlyoffice/callback?path=Saved.xlsx",
            {"status": 2, "url": data_url},
        )
        self.assertEqual(callback, {"error": 0})

        saved_path = self.repo / "runtime" / "onlyoffice-test" / "workbooks" / "Saved.xlsx"
        self.assertEqual(saved_path.read_bytes(), workbook_bytes)

    def test_onlyoffice_jwt_token_is_added_when_secret_is_configured(self) -> None:
        self.server.config = MainComputerConfig(
            workspace=self.repo,
            onlyoffice_storage_root=Path("runtime/onlyoffice-test/workbooks"),
            onlyoffice_public_url="http://127.0.0.1:18085",
            onlyoffice_internal_url="http://127.0.0.1:18085",
            onlyoffice_jwt_enabled=True,
            onlyoffice_jwt_secret="dev-secret",
        )
        self.post_json("/api/applications/onlyoffice/create", {"path": "Token.xlsx"})
        config = self.post_json("/api/applications/onlyoffice/config", {"path": "Token.xlsx"})
        editor_config = config["config"]
        self.assertIn("token", editor_config)
        self.assertEqual(editor_config["token"].count("."), 2)

    def test_onlyoffice_from_env_uses_local_docker_defaults(self) -> None:
        config = MainComputerConfig.from_env()
        self.assertEqual(config.onlyoffice_mode, "docker")
        self.assertEqual(config.onlyoffice_public_url, "http://127.0.0.1:18085")
        self.assertEqual(config.onlyoffice_internal_url, "http://127.0.0.1:18085")
        self.assertIsNone(config.onlyoffice_browser_public_url)
        self.assertFalse(config.onlyoffice_jwt_enabled)
        self.assertIsNone(config.onlyoffice_jwt_secret)

    def test_cli_config_preserves_onlyoffice_env_defaults_for_viewport(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["viewport", "-noverbose"])
        config = _config_from_args(args)
        self.assertEqual(config.onlyoffice_mode, "docker")
        self.assertEqual(config.onlyoffice_public_url, "http://127.0.0.1:18085")
        self.assertEqual(config.onlyoffice_internal_url, "http://127.0.0.1:18085")
        self.assertIsNone(config.onlyoffice_browser_public_url)
        self.assertFalse(config.onlyoffice_jwt_enabled)
        self.assertIsNone(config.onlyoffice_jwt_secret)


    def test_onlyoffice_from_env_can_disable_jwt_for_docker_mode(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "MAIN_COMPUTER_ONLYOFFICE_MODE": "docker",
                "MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL": "http://127.0.0.1:18085",
                "MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL": "http://127.0.0.1:18085",
                "MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL": "http://host.docker.internal:8765",
                "MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED": "false",
                "MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET": "",
            },
            clear=False,
        ):
            config = MainComputerConfig.from_env()

        self.assertEqual(config.onlyoffice_mode, "docker")
        self.assertEqual(config.onlyoffice_public_url, "http://127.0.0.1:18085")
        self.assertEqual(config.onlyoffice_internal_url, "http://127.0.0.1:18085")
        self.assertEqual(config.onlyoffice_callback_base_url, "http://host.docker.internal:8765")
        self.assertFalse(config.onlyoffice_jwt_enabled)
        self.assertIsNone(config.onlyoffice_jwt_secret)


if __name__ == "__main__":
    unittest.main()
