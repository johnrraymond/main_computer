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
from main_computer.service_control import pending_control_requests
from main_computer.viewport_route_dispatch import _control_panel_url_port


class ViewportStaticPageTests(unittest.TestCase):
    def test_text_index_contains_console_hooks(self) -> None:
        self.assertIn("Main Computer Console", TEXT_INDEX_HTML)
        self.assertIn("Open Graphical Test", TEXT_INDEX_HTML)
        self.assertIn("Open Energy Credits", TEXT_INDEX_HTML)
        self.assertIn('/energy', TEXT_INDEX_HTML)
        self.assertIn("Open Applications", TEXT_INDEX_HTML)
        self.assertIn('/applications', TEXT_INDEX_HTML)
        self.assertIn("main-computer-viewport-session-v1", TEXT_INDEX_HTML)
        self.assertIn('<button class="diag-button" type="button" data-diagnostic-level="functional">Level 1</button>', TEXT_INDEX_HTML)
        self.assertIn('<button class="diag-button" type="button" data-diagnostic-level="health">Level 5</button>', TEXT_INDEX_HTML)
        self.assertIn('data-diagnostic-level="ollama-visibility"', TEXT_INDEX_HTML)
        self.assertIn("Search projects", TEXT_INDEX_HTML)
        self.assertIn("console loaded", TEXT_INDEX_HTML)
        self.assertIn("local directory", TEXT_INDEX_HTML)
        self.assertIn("patch level", TEXT_INDEX_HTML)
        self.assertIn("workspace-patch-level", TEXT_INDEX_HTML)
        self.assertIn("/api/workspace-timestamp", TEXT_INDEX_HTML)
        self.assertIn("working on prompt", TEXT_INDEX_HTML)
        self.assertIn("progress-bar", TEXT_INDEX_HTML)
        self.assertIn("startWorkingCountdown", TEXT_INDEX_HTML)
        self.assertIn("timeout in", TEXT_INDEX_HTML)
        self.assertIn("ollama_timeout_s", TEXT_INDEX_HTML)
        self.assertIn("/api/chat", TEXT_INDEX_HTML)
        self.assertIn("/api/diagnostics", TEXT_INDEX_HTML)
        self.assertIn("formatDiagnosticReport", TEXT_INDEX_HTML)
        self.assertIn("diagnostics_report.json", TEXT_INDEX_HTML)
        self.assertIn("data-raw-content", TEXT_INDEX_HTML)
        self.assertIn("renderMode", TEXT_INDEX_HTML)
        self.assertIn("renderPlainTextContent", TEXT_INDEX_HTML)
        self.assertIn("checks:", TEXT_INDEX_HTML)
        self.assertIn("checks:", TEXT_INDEX_HTML)
        self.assertIn("/api/projects", TEXT_INDEX_HTML)
        self.assertIn("/debug/text", TEXT_INDEX_HTML)
        self.assertIn("data-fullscreen-target", TEXT_INDEX_HTML)
        self.assertIn("requestFullscreen", TEXT_INDEX_HTML)
        self.assertIn(".fullscreen-widget:not(:fullscreen)", TEXT_INDEX_HTML)
        self.assertIn("padding-top: 42px", TEXT_INDEX_HTML)
        self.assertNotIn("Ollama Debug Mode", TEXT_INDEX_HTML)
        self.assertNotIn("/api/ollama-debug/chat", TEXT_INDEX_HTML)
        self.assertIn("/server hard-halt", TEXT_INDEX_HTML)
        self.assertIn("/system/hard-halt", TEXT_INDEX_HTML)
        self.assertIn("Hard halt requested. The local viewport server will stop now. Restart it to load patched code.", TEXT_INDEX_HTML)
        self.assertIn("This will immediately stop the local viewport server.", TEXT_INDEX_HTML)

    def test_control_panel_gitea_probe_uses_standalone_web_port(self) -> None:
        self.assertEqual(_control_panel_url_port("http://localhost:3000/", 3000), 3000)
        self.assertEqual(_control_panel_url_port("http://localhost:3123/", 3000), 3123)
        self.assertEqual(_control_panel_url_port("", 3000), 3000)

    def test_graphical_index_contains_widget_hooks(self) -> None:
        self.assertIn("Main Computer Control Panel", GRAPHICAL_INDEX_HTML)
        self.assertIn("/graphical renamed into a useful live machine and services view", GRAPHICAL_INDEX_HTML)
        self.assertIn("Service Map", GRAPHICAL_INDEX_HTML)
        self.assertIn("detailed runtime and product-service cards", GRAPHICAL_INDEX_HTML)
        self.assertIn('id="service-grid"', GRAPHICAL_INDEX_HTML)
        self.assertIn("renderServices", GRAPHICAL_INDEX_HTML)
        self.assertNotIn('id="service-map"', GRAPHICAL_INDEX_HTML)
        self.assertIn("Machine", GRAPHICAL_INDEX_HTML)
        self.assertIn("User Runtime", GRAPHICAL_INDEX_HTML)
        self.assertIn("Dependencies", GRAPHICAL_INDEX_HTML)
        self.assertIn("Open Ports", GRAPHICAL_INDEX_HTML)
        self.assertIn("Configuration", GRAPHICAL_INDEX_HTML)
        self.assertIn("Recent Activity", GRAPHICAL_INDEX_HTML)
        self.assertIn("Energy Credits Network Topology", GRAPHICAL_INDEX_HTML)
        self.assertIn("mainnet, testnet, test, dev", GRAPHICAL_INDEX_HTML)
        self.assertIn('id="network-grid"', GRAPHICAL_INDEX_HTML)
        self.assertIn("renderNetworks", GRAPHICAL_INDEX_HTML)
        self.assertIn("remote hub", GRAPHICAL_INDEX_HTML)
        self.assertIn("remote rpc", GRAPHICAL_INDEX_HTML)
        self.assertIn(".network-token.green", GRAPHICAL_INDEX_HTML)
        self.assertIn("--token-face: var(--mc-green-face);", GRAPHICAL_INDEX_HTML)
        self.assertIn("--token-face: var(--mc-gold-face);", GRAPHICAL_INDEX_HTML)
        self.assertIn("--token-face: var(--mc-coral-face);", GRAPHICAL_INDEX_HTML)
        self.assertNotIn(".network-token.green { color:", GRAPHICAL_INDEX_HTML)
        self.assertIn("Energy Credits", GRAPHICAL_INDEX_HTML)
        self.assertNotIn("<h3>Hub</h3>", GRAPHICAL_INDEX_HTML)
        self.assertNotIn("Blockchain / Anvil</h3>", GRAPHICAL_INDEX_HTML)
        self.assertNotIn("Loading service map", GRAPHICAL_INDEX_HTML)
        self.assertIn("/api/control-panel/status", GRAPHICAL_INDEX_HTML)
        self.assertIn("refreshStatus", GRAPHICAL_INDEX_HTML)
        self.assertIn("127.0.0.1 probes", GRAPHICAL_INDEX_HTML)
        self.assertIn("per-user default", GRAPHICAL_INDEX_HTML)
        self.assertIn("Text Console", GRAPHICAL_INDEX_HTML)
        self.assertIn("Energy Credits", GRAPHICAL_INDEX_HTML)
        self.assertIn('/energy', GRAPHICAL_INDEX_HTML)
        self.assertIn("Applications", GRAPHICAL_INDEX_HTML)
        self.assertIn('/applications', GRAPHICAL_INDEX_HTML)
        self.assertIn("/debug/graphical", GRAPHICAL_INDEX_HTML)
        self.assertIn("/revision", GRAPHICAL_INDEX_HTML)
        self.assertIn('id="level-1-telemetry-button"', GRAPHICAL_INDEX_HTML)
        self.assertIn("Level 1 Telemetry", GRAPHICAL_INDEX_HTML)
        self.assertIn("/api/control-panel/level-1-telemetry", GRAPHICAL_INDEX_HTML)
        self.assertIn("runLevel1Telemetry", GRAPHICAL_INDEX_HTML)
        self.assertIn('id="level-4-diagnostic-button"', GRAPHICAL_INDEX_HTML)
        self.assertIn("Level 4 System Check", GRAPHICAL_INDEX_HTML)
        self.assertIn("/api/control-panel/level-4-diagnostic", GRAPHICAL_INDEX_HTML)
        self.assertIn("runLevel4Diagnostic", GRAPHICAL_INDEX_HTML)
        self.assertIn("locks overread", GRAPHICAL_INDEX_HTML)
        self.assertIn('id="telemetry-services"', GRAPHICAL_INDEX_HTML)
        self.assertIn("renderTelemetryServices", GRAPHICAL_INDEX_HTML)
        self.assertIn('id="telemetry-groups"', GRAPHICAL_INDEX_HTML)
        self.assertIn("renderTelemetryGroups", GRAPHICAL_INDEX_HTML)
        self.assertIn('id="telemetry-operator"', GRAPHICAL_INDEX_HTML)
        self.assertIn('id="level-5-diagnostics-button"', GRAPHICAL_INDEX_HTML)
        self.assertIn("Level 5 Diagnostics", GRAPHICAL_INDEX_HTML)
        self.assertIn("/api/control-panel/system-sanity/stream", GRAPHICAL_INDEX_HTML)
        self.assertIn("EventSource", GRAPHICAL_INDEX_HTML)
        self.assertIn("runSystemSanity", GRAPHICAL_INDEX_HTML)
        self.assertIn("diagnostics-summary", GRAPHICAL_INDEX_HTML)
        self.assertIn("diagnostics-findings", GRAPHICAL_INDEX_HTML)
        self.assertIn('id="hard-halt-server-button"', GRAPHICAL_INDEX_HTML)
        self.assertIn("Shutdown System", GRAPHICAL_INDEX_HTML)
        self.assertIn("/system/shutdown", GRAPHICAL_INDEX_HTML)
        self.assertIn("stop Main Computer services", GRAPHICAL_INDEX_HTML)
        self.assertIn("without auto-restart", GRAPHICAL_INDEX_HTML)
        self.assertNotIn("Buddhabrot client render", GRAPHICAL_INDEX_HTML)
        self.assertNotIn("fractal-selector", GRAPHICAL_INDEX_HTML)
        self.assertNotIn("Bridge Control Viewport", GRAPHICAL_INDEX_HTML)
        self.assertNotIn("Ollama Debug", GRAPHICAL_INDEX_HTML)
        self.assertNotIn("/api/ollama-debug/chat", GRAPHICAL_INDEX_HTML)

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

    def test_energy_index_contains_control_hooks(self) -> None:
        self.assertIn("Main Computer Energy Credits", ENERGY_INDEX_HTML)
        self.assertIn("/api/energy/status", ENERGY_INDEX_HTML)
        self.assertIn("Native Energy Chain", ENERGY_INDEX_HTML)
        self.assertIn("/api/energy/chain/status", ENERGY_INDEX_HTML)
        self.assertIn("energy-chain-connected", ENERGY_INDEX_HTML)
        self.assertIn("energy-chain-block", ENERGY_INDEX_HTML)
        self.assertIn("energy-chain-peers", ENERGY_INDEX_HTML)
        self.assertIn("energy-chain-defaults", ENERGY_INDEX_HTML)
        self.assertIn("energy-chain-rpc-source", ENERGY_INDEX_HTML)
        self.assertIn("energy-chain-id-source", ENERGY_INDEX_HTML)
        self.assertIn("Main Computer Governance", ENERGY_INDEX_HTML)
        self.assertIn("/api/bridge/governance", ENERGY_INDEX_HTML)
        self.assertIn("bridge-governance-status", ENERGY_INDEX_HTML)
        self.assertIn("Bridge Order Flow", ENERGY_INDEX_HTML)
        self.assertIn("bridge-order-flow", ENERGY_INDEX_HTML)
        self.assertIn("bridge-order-belay", ENERGY_INDEX_HTML)
        self.assertIn("bridge-order-helm", ENERGY_INDEX_HTML)
        self.assertIn("X-LAG Contract Reserve", ENERGY_INDEX_HTML)
        self.assertIn("/api/xlag/contract/status", ENERGY_INDEX_HTML)
        self.assertIn("xlag-payout-propose", ENERGY_INDEX_HTML)
        self.assertIn("xlag-payout-second", ENERGY_INDEX_HTML)
        self.assertIn("xlag-payout-belay", ENERGY_INDEX_HTML)
        self.assertIn("xlag-payout-contest", ENERGY_INDEX_HTML)
        self.assertIn("xlag-reset-propose", ENERGY_INDEX_HTML)
        self.assertIn("xlag-reset-approve", ENERGY_INDEX_HTML)
        self.assertIn("xlag-reset-contest", ENERGY_INDEX_HTML)
        self.assertIn("xlag-reset-execute", ENERGY_INDEX_HTML)
        self.assertIn("/api/energy/nodes/register", ENERGY_INDEX_HTML)
        self.assertIn("/api/energy/credits/issue", ENERGY_INDEX_HTML)
        self.assertIn("/api/energy/credits/spend", ENERGY_INDEX_HTML)

    def test_revision_index_contains_control_hooks(self) -> None:
        self.assertIn("Main Computer Revision Control", REVISION_INDEX_HTML)
        self.assertIn("/api/revisions/status", REVISION_INDEX_HTML)
        self.assertIn("/api/revisions/snapshot", REVISION_INDEX_HTML)
        self.assertIn("/api/revisions/diff", REVISION_INDEX_HTML)
        self.assertIn("/api/revisions/restore", REVISION_INDEX_HTML)
        self.assertIn("/api/revisions/restore-system", REVISION_INDEX_HTML)

    def test_document_editor_layout_shell_uses_page_model(self) -> None:
        self.assertIn("letter: {label: \"Letter\", widthPx: 816, heightPx: 1056}", APPLICATIONS_INDEX_HTML)
        self.assertIn("a4: {label: \"A4\", widthPx: 794, heightPx: 1123}", APPLICATIONS_INDEX_HTML)
        self.assertIn("legal: {label: \"Legal\", widthPx: 816, heightPx: 1344}", APPLICATIONS_INDEX_HTML)
        self.assertIn("screen: {label: \"Screen\", widthPx: 960, heightPx: 1280}", APPLICATIONS_INDEX_HTML)
        self.assertIn('preset: "letter"', APPLICATIONS_INDEX_HTML)
        self.assertIn("margins: {top: 96, right: 96, bottom: 96, left: 96}", APPLICATIONS_INDEX_HTML)
        self.assertIn('mode: "paged"', APPLICATIONS_INDEX_HTML)
        self.assertIn("showPageBreaks: true", APPLICATIONS_INDEX_HTML)
        self.assertIn('rawView.mode === "endless" ? "endless" : "paged"', APPLICATIONS_INDEX_HTML)
        self.assertIn('documentLayoutWidth.disabled = !isCustom;', APPLICATIONS_INDEX_HTML)
        self.assertIn('documentLayoutHeight.disabled = !isCustom;', APPLICATIONS_INDEX_HTML)
        self.assertIn("saveDocumentLayoutForCurrentPath", APPLICATIONS_INDEX_HTML)
        self.assertIn("loadDocumentLayoutForCurrentPath(selectedPath)", APPLICATIONS_INDEX_HTML)
        self.assertIn("--document-page-width", APPLICATIONS_INDEX_HTML)
        self.assertIn("--document-page-height", APPLICATIONS_INDEX_HTML)
        self.assertIn("--document-margin-top", APPLICATIONS_INDEX_HTML)
        self.assertIn("--document-margin-right", APPLICATIONS_INDEX_HTML)
        self.assertIn("--document-margin-bottom", APPLICATIONS_INDEX_HTML)
        self.assertIn("--document-margin-left", APPLICATIONS_INDEX_HTML)
        self.assertIn("--document-zoom", APPLICATIONS_INDEX_HTML)
        self.assertIn(".document-canvas.document-view-endless", APPLICATIONS_INDEX_HTML)
        self.assertIn(".document-canvas.document-view-endless.document-show-page-breaks", APPLICATIONS_INDEX_HTML)
        self.assertIn("renderDocumentPages", APPLICATIONS_INDEX_HTML)
        self.assertIn("repaginateDocument", APPLICATIONS_INDEX_HTML)
        self.assertIn("createPage", APPLICATIONS_INDEX_HTML)
        self.assertIn("measurePageContent", APPLICATIONS_INDEX_HTML)
        self.assertIn("moveOverflowToNextPage", APPLICATIONS_INDEX_HTML)
        self.assertIn("preserveCaretDuringRepagination", APPLICATIONS_INDEX_HTML)
        self.assertIn('documentCanvas.addEventListener("input"', APPLICATIONS_INDEX_HTML)
        self.assertIn("getDocumentEditorHtml", APPLICATIONS_INDEX_HTML)
        self.assertIn("setDocumentEditorHtml", APPLICATIONS_INDEX_HTML)
        self.assertIn("handleDocumentEditorKeydown", APPLICATIONS_INDEX_HTML)
        self.assertIn('event.key !== "Enter"', APPLICATIONS_INDEX_HTML)
        self.assertIn("splitDocumentBlockAtSelection", APPLICATIONS_INDEX_HTML)
        self.assertIn("documentBlockForRange", APPLICATIONS_INDEX_HTML)
        self.assertIn('documentCanvas.addEventListener("keydown", handleDocumentEditorKeydown)', APPLICATIONS_INDEX_HTML)
        self.assertIn("documentFormatValueForBlock", APPLICATIONS_INDEX_HTML)
        self.assertIn("updateDocumentFormatForCaret", APPLICATIONS_INDEX_HTML)
        self.assertIn("applyDocumentBlockFormat", APPLICATIONS_INDEX_HTML)
        self.assertIn('document.addEventListener("selectionchange"', APPLICATIONS_INDEX_HTML)
        self.assertIn('documentCanvas.addEventListener("keyup", updateDocumentFormatForCaret)', APPLICATIONS_INDEX_HTML)
        self.assertIn('const newBlock = createDocumentBlock("P");', APPLICATIONS_INDEX_HTML)
        self.assertNotIn('document.execCommand("formatBlock"', APPLICATIONS_INDEX_HTML)
        page_content_rule = re.search(r"\.mc-page-content\s*{(?P<body>.*?)\n    }", APPLICATIONS_INDEX_HTML, re.S)
        self.assertIsNotNone(page_content_rule)
        self.assertNotIn("overflow: auto", page_content_rule.group("body"))
        self.assertNotIn("overflow-y: auto", page_content_rule.group("body"))
        self.assertIn("overflow: visible", page_content_rule.group("body"))
        self.assertIn(".document-canvas {\n      min-height: 0;\n      overflow: auto;", APPLICATIONS_INDEX_HTML)
        self.assertIn(".document-canvas.document-view-paged .mc-page.document-page-oversize", APPLICATIONS_INDEX_HTML)
        self.assertLess(
            APPLICATIONS_INDEX_HTML.index('id="document-layout-button"'),
            APPLICATIONS_INDEX_HTML.index('id="document-layout-popover"'),
        )
        self.assertLess(
            APPLICATIONS_INDEX_HTML.index('id="document-layout-popover"'),
            APPLICATIONS_INDEX_HTML.index('id="document-canvas"'),
        )
        self.assertLess(
            APPLICATIONS_INDEX_HTML.index('id="document-canvas"'),
            APPLICATIONS_INDEX_HTML.index('id="document-editor"'),
        )

    def test_application_routes_default_to_calculator(self) -> None:
        self.assertEqual(_application_route_target("/applications"), "calculator")
        self.assertEqual(_application_route_target("/apps"), "calculator")
        self.assertEqual(_application_route_target("/app"), "calculator")
        self.assertEqual(_application_route_target("/applications/calculator"), "calculator")
        self.assertEqual(_application_route_target("/apps/calculator"), "calculator")
        self.assertEqual(_application_route_target("/app/calculator"), "calculator")
        self.assertEqual(_application_route_target("/applications/worker"), "worker")
        self.assertEqual(_application_route_target("/apps/worker"), "worker")
        self.assertEqual(_application_route_target("/app/worker"), "worker")
        self.assertEqual(_application_route_target("/applications/wallet"), "wallet")
        self.assertEqual(_application_route_target("/apps/wallet"), "wallet")
        self.assertEqual(_application_route_target("/app/wallet"), "wallet")
        self.assertEqual(_application_route_target("/applications/website-builder"), "website-builder")
        self.assertEqual(_application_route_target("/applications/website-builder/hub-site"), "website-builder")
        self.assertEqual(_application_route_target("/apps/website-builder/blog-site"), "website-builder")
        self.assertIsNone(_application_route_target("/applications/website-builder/UpperCase"))
        self.assertIsNone(_application_route_target("/applications/website-builder/not/a/site"))
        self.assertIn('if (!parts.length) return "calculator";', APPLICATIONS_INDEX_HTML)
        self.assertIn('let currentApp = "calculator";', APPLICATIONS_INDEX_HTML)
        self.assertIn('window.history.pushState(state, "", nextPath)', APPLICATIONS_INDEX_HTML)
        self.assertIn('window.addEventListener("popstate"', APPLICATIONS_INDEX_HTML)
        self.assertIn('`/applications/${normalized}`', APPLICATIONS_INDEX_HTML)
        self.assertIn('function websiteBuilderPath(siteId = "")', APPLICATIONS_INDEX_HTML)
        self.assertIn('function websiteBuilderSiteIdFromPath(pathname = window.location.pathname)', APPLICATIONS_INDEX_HTML)
        self.assertIn('function syncWebsiteBuilderRoute(siteId', APPLICATIONS_INDEX_HTML)
        self.assertIn("Enter runs. Up and Down recall command history.", APPLICATIONS_INDEX_HTML)
        self.assertIn("stubbed", APPLICATIONS_INDEX_HTML)

        config = MainComputerConfig(workspace=Path.cwd().parent)
        server = ViewportServer(("127.0.0.1", 0), config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
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

            with urlopen(f"{base}/applications/website-builder", timeout=5) as response:
                website_builder_page = response.read().decode("utf-8")
            self.assertIn("Main Computer Applications", website_builder_page)
            self.assertIn("websiteBuilderSiteIdFromPath", website_builder_page)

            with urlopen(f"{base}/applications/website-builder/hub-site", timeout=5) as response:
                website_builder_project_page = response.read().decode("utf-8")
            self.assertIn("Main Computer Applications", website_builder_project_page)
            self.assertIn("syncWebsiteBuilderRoute", website_builder_project_page)

            with urlopen(f"{base}/apps/git-tools", timeout=5) as response:
                git_tools_page = response.read().decode("utf-8")
            self.assertIn("Main Computer Applications", git_tools_page)

            with urlopen(f"{base}/applications/worker", timeout=5) as response:
                worker_page = response.read().decode("utf-8")
            self.assertIn("Main Computer Applications", worker_page)
            self.assertIn("Lock AI model when working", worker_page)

            with urlopen(f"{base}/applications/wallet", timeout=5) as response:
                wallet_page = response.read().decode("utf-8")
            self.assertIn("Main Computer Applications", wallet_page)
            self.assertIn("Standalone wallet connect/disconnect workbench is ready.", wallet_page)

            with self.assertRaises(HTTPError) as invalid_app:
                urlopen(f"{base}/applications/not-a-real-app", timeout=5)
            self.assertEqual(invalid_app.exception.code, 404)
        finally:
            server.shutdown()
            thread.join(timeout=5)


    def test_graphical_level4_diagnostic_endpoint_reports_underlying_system(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main_computer").mkdir()
            (root / "new_patch.py").write_text("# local patch harness marker\n", encoding="utf-8")
            (root / "pyproject.toml").write_text("[project]\nname = \"main-computer-test\"\n", encoding="utf-8")

            config = MainComputerConfig(workspace=root)
            server = ViewportServer(("127.0.0.1", 0), config, verbose=False)
            server.debug_root = root
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(f"{base}/api/control-panel/level-4-diagnostic", timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertTrue(payload["ok"])
                self.assertEqual(4, payload["level"])
                self.assertEqual("graphical-underlying-system", payload["scope"])
                self.assertIn(payload["overall_status"], {"PASS", "WARN", "FAIL"})
                self.assertGreaterEqual(payload["finding_count"], 6)
                self.assertIn("operational_percent", payload)
                self.assertIn("locks", payload)
                self.assertTrue(
                    any(check["area"] == "locks" and "overread" in check["message"] for check in payload["checks"])
                )
                self.assertTrue(
                    any(check["area"] == "ports" and check["evidence"].get("service") == "app" for check in payload["checks"])
                )
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()


    def test_hard_halt_endpoint_requires_post_and_invokes_shutdown_once(self) -> None:
        config = MainComputerConfig(workspace=Path.cwd().parent)
        server = ViewportServer(("127.0.0.1", 0), config, verbose=False)
        requested: list[str] = []

        def fake_request_hard_halt(*, source: str = "unknown") -> None:
            requested.append(source)

        server.request_hard_halt = fake_request_hard_halt  # type: ignore[method-assign]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"

            with self.assertRaises(HTTPError) as get_error:
                urlopen(f"{base}/system/hard-halt", timeout=5)
            self.assertEqual(get_error.exception.code, 405)
            self.assertEqual([], requested)

            request = Request(
                f"{base}/system/hard-halt",
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)

            self.assertEqual(
                {
                    "ok": True,
                    "message": "Viewport server hard halt requested. Restart the server to load patched code.",
                },
                payload,
            )
            self.assertEqual(["system-hard-halt-endpoint"], requested)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


    def test_system_shutdown_endpoint_queues_supervisor_shutdown_without_app_halt_when_supervised(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "runtime" / "service_supervisor"
            state_dir.mkdir(parents=True)
            (state_dir / "state.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "state": "supervising",
                        "service": {"pid": 12345},
                        "children": {"app": {"state": "running", "pid": 23456}},
                    }
                ),
                encoding="utf-8",
            )

            config = MainComputerConfig(workspace=Path.cwd().parent)
            server = ViewportServer(("127.0.0.1", 0), config, verbose=False)
            server.debug_root = root
            requested: list[str] = []

            def fake_request_hard_halt(*, source: str = "unknown") -> None:
                requested.append(source)

            server.request_hard_halt = fake_request_hard_halt  # type: ignore[method-assign]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"

                with self.assertRaises(HTTPError) as get_error:
                    urlopen(f"{base}/system/shutdown", timeout=5)
                self.assertEqual(get_error.exception.code, 405)

                request = Request(
                    f"{base}/system/shutdown",
                    data=b"{}",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 200)

                self.assertTrue(payload["ok"])
                self.assertEqual(
                    "Main Computer system shutdown requested. Supervised services will stop instead of restarting.",
                    payload["message"],
                )
                self.assertFalse(payload["fallback_to_viewport_halt"])
                self.assertEqual([], requested)

                queued = pending_control_requests(root, channel="supervisor")
                self.assertEqual(1, len(queued))
                self.assertEqual("shutdown", queued[0].action)
                self.assertEqual("system", queued[0].target)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()


    def test_server_emits_signals_by_default(self) -> None:
        config = MainComputerConfig(workspace=__import__("pathlib").Path.cwd().parent)
        server = ViewportServer(("127.0.0.1", 0), config)
        output = StringIO()

        with redirect_stdout(output):
            server.signal("test-signal", value="yes")

        self.assertIn("[signal] test-signal value=yes", output.getvalue())
        server.server_close()


    def test_server_can_suppress_signals(self) -> None:
        config = MainComputerConfig(workspace=__import__("pathlib").Path.cwd().parent)
        server = ViewportServer(("127.0.0.1", 0), config, verbose=False)
        output = StringIO()

        with redirect_stdout(output):
            server.signal("test-signal", value="yes")

        self.assertEqual("", output.getvalue())
        server.server_close()


    def test_serve_writes_runtime_viewport_pid_file_for_heartbeat(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        previous_cwd = Path.cwd()
        observed: dict[str, object] = {}

        class FakeViewportServer:
            def __init__(self, server_address, config, *, verbose=True):
                self.server_port = server_address[1]
                self.provider_name = "fake-provider"
                observed["instance"] = self

            def signal(self, name: str, **fields: object) -> None:
                observed.setdefault("signals", []).append((name, fields))

            def serve_forever(self) -> None:
                pid_path = Path(tempdir.name) / ".main_computer_viewport.pid"
                observed["pid_exists_during_run"] = pid_path.exists()
                observed["pid_text_during_run"] = pid_path.read_text(encoding="utf-8").strip() if pid_path.exists() else ""
                raise KeyboardInterrupt

            def server_close(self) -> None:
                observed["server_closed"] = True

        try:
            os.chdir(tempdir.name)
            config = MainComputerConfig(workspace=Path(tempdir.name))
            with patch("main_computer.viewport.ensure_heartbeat_service") as ensure_mock, patch("main_computer.viewport.ViewportServer", FakeViewportServer):
                serve(config, host="127.0.0.1", port=8876, verbose=False)
            ensure_mock.assert_called_once()
            self.assertTrue(observed.get("pid_exists_during_run"))
            self.assertEqual(str(os.getpid()), observed.get("pid_text_during_run"))
            self.assertFalse((Path(tempdir.name) / ".main_computer_viewport.pid").exists())
            self.assertTrue(observed.get("server_closed"))
        finally:
            os.chdir(previous_cwd)
            tempdir.cleanup()


if __name__ == "__main__":
    unittest.main()
