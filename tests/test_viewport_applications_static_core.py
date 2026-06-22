from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from html.parser import HTMLParser
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


class _ComponentMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.components: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(attrs)

    def _record(self, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name: value for name, value in attrs if name}
        if "data-mc-component-id" in attr_map:
            self.components.append(attr_map)


class ViewportApplicationsStaticCoreTests(unittest.TestCase):
    def test_application_shell_taskbar_body_and_pointer_focus_handoff(self) -> None:
        self.assertRegex(APPLICATIONS_INDEX_HTML, r'<body\s+data-has-app-taskbar="true"\s*>')
        self.assertIn('class="panel stage" tabindex="-1"', APPLICATIONS_INDEX_HTML)
        self.assertIn("function focusActiveWorkspaceAfterPointerAppSelection(button, event)", APPLICATIONS_INDEX_HTML)
        self.assertIn('button?.closest?.(".launcher")', APPLICATIONS_INDEX_HTML)
        self.assertIn("if (!launcher || event.detail === 0) return;", APPLICATIONS_INDEX_HTML)
        self.assertIn('document.querySelector("[data-mc-component-id=\'applications.workspace\']")', APPLICATIONS_INDEX_HTML)
        self.assertIn("workspace.focus({preventScroll: true});", APPLICATIONS_INDEX_HTML)
        self.assertIn("focusActiveWorkspaceAfterPointerAppSelection(button, event);", APPLICATIONS_INDEX_HTML)

    def test_applications_index_contains_core_and_game_hooks(self) -> None:
        self.assertIn("Main Computer Applications", APPLICATIONS_INDEX_HTML)
        self.assertIn('class="app-card ready active" href="/applications" data-app="desktop"', APPLICATIONS_INDEX_HTML)
        self.assertIn("Desktop app launcher is ready.", APPLICATIONS_INDEX_HTML)
        self.assertIn("Choose an app from the desktop grid.", APPLICATIONS_INDEX_HTML)
        self.assertIn("Desktop applications", APPLICATIONS_INDEX_HTML)
        self.assertIn(".desktop-overlay.desktop-home", APPLICATIONS_INDEX_HTML)
        self.assertIn('data-has-app-taskbar="true"', APPLICATIONS_INDEX_HTML)
        self.assertRegex(APPLICATIONS_INDEX_HTML, r'<body\s+data-has-app-taskbar="true"\s*>')
        self.assertIn('class="panel stage" tabindex="-1"', APPLICATIONS_INDEX_HTML)
        self.assertIn("--app-taskbar-rail-width: 56px;", APPLICATIONS_INDEX_HTML)
        self.assertIn('body[data-has-app-taskbar="true"] main', APPLICATIONS_INDEX_HTML)
        self.assertIn("padding-left: calc(16px + var(--app-taskbar-reserved-width));", APPLICATIONS_INDEX_HTML)
        self.assertIn("left: 0;", APPLICATIONS_INDEX_HTML)
        self.assertIn("transform: translateX(calc(-100% + var(--app-taskbar-rail-width)));", APPLICATIONS_INDEX_HTML)
        self.assertIn("direction: rtl;", APPLICATIONS_INDEX_HTML)
        self.assertIn("overflow-x: hidden;", APPLICATIONS_INDEX_HTML)
        self.assertIn("overflow-y: auto;", APPLICATIONS_INDEX_HTML)
        self.assertIn("overscroll-behavior: contain;", APPLICATIONS_INDEX_HTML)
        self.assertIn(".launcher > *", APPLICATIONS_INDEX_HTML)
        self.assertIn("min-width: 0;", APPLICATIONS_INDEX_HTML)
        self.assertIn(".launcher::after", APPLICATIONS_INDEX_HTML)
        self.assertIn("overflow-wrap: anywhere;", APPLICATIONS_INDEX_HTML)
        self.assertIn("grid-template-columns: minmax(420px, 1fr) minmax(240px, 320px);", APPLICATIONS_INDEX_HTML)
        self.assertIn("Game Surface", APPLICATIONS_INDEX_HTML)
        self.assertIn("Arcstorm finale sprite/particle showcase", APPLICATIONS_INDEX_HTML)
        self.assertIn("Arcstorm finale surface is ready.", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="webgl-particle-density"', APPLICATIONS_INDEX_HTML)
        self.assertIn("Select an application tab to open it.", APPLICATIONS_INDEX_HTML)
        self.assertIn("Calculator", APPLICATIONS_INDEX_HTML)
        self.assertIn('class="app-card ready" href="/applications/calculator" data-app="calculator"', APPLICATIONS_INDEX_HTML)
        self.assertNotIn('class="app-card active" href="/applications" data-app="calculator"', APPLICATIONS_INDEX_HTML)
        self.assertIn("Document Editor", APPLICATIONS_INDEX_HTML)
        self.assertIn("Spreadsheet", APPLICATIONS_INDEX_HTML)
        self.assertIn("File Explorer", APPLICATIONS_INDEX_HTML)
        self.assertIn('data-app="file-explorer"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="file-explorer-app"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="file-explorer-roots"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="file-explorer-path"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="file-explorer-list"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="file-explorer-preview"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="file-explorer-search"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="file-explorer-status"', APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/file-explorer/roots", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/file-explorer/list", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/file-explorer/read", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/file-explorer/search", APPLICATIONS_INDEX_HTML)
        self.assertLess(
            APPLICATIONS_INDEX_HTML.index('data-app="document"'),
            APPLICATIONS_INDEX_HTML.index('data-app="spreadsheet"'),
        )
        self.assertIn("Task Manager", APPLICATIONS_INDEX_HTML)
        self.assertIn('data-widget-label="Task Overview"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-widget-label="Patch Preview"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-widget-label="Aider Workspace"', APPLICATIONS_INDEX_HTML)
        self.assertIn("function ensureApplicationWidgets()", APPLICATIONS_INDEX_HTML)
        self.assertIn("function desktopIconSvg(app)", APPLICATIONS_INDEX_HTML)
        self.assertIn("const desktopIconSvgByApp = {", APPLICATIONS_INDEX_HTML)
        self.assertIn('class="desktop-glyph-svg"', APPLICATIONS_INDEX_HTML)
        self.assertIn('desktop-glyph-svg" viewBox="0 0 24 24"', APPLICATIONS_INDEX_HTML)
        self.assertIn("setApplicationWidgetTicker(taskOverviewCard", APPLICATIONS_INDEX_HTML)
        self.assertIn("let taskManagerLoadingShown = false;", APPLICATIONS_INDEX_HTML)
        self.assertIn('aria-label="Task manager data views"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="task-tab-processes"', APPLICATIONS_INDEX_HTML)
        self.assertIn("Server Processes", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="task-tab-all-processes"', APPLICATIONS_INDEX_HTML)
        self.assertIn("All Processes", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="task-tab-connections"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-task-tab-group="task-notebook"', APPLICATIONS_INDEX_HTML)
        self.assertIn("function setTaskNotebookTab(tabName,", APPLICATIONS_INDEX_HTML)
        self.assertIn("function taskNotebookTabFromPath", APPLICATIONS_INDEX_HTML)
        self.assertIn("function syncTaskManagerTabRoute", APPLICATIONS_INDEX_HTML)
        self.assertIn("/applications/task-manager/${normalizedTaskNotebookTab(tabName)}", APPLICATIONS_INDEX_HTML)
        self.assertIn('? taskManagerTabPath(taskNotebookTabFromPath(window.location.pathname))', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="task-all-process-table"', APPLICATIONS_INDEX_HTML)
        self.assertNotIn('id="task-include-all"', APPLICATIONS_INDEX_HTML)
        self.assertIn("function taskHeartbeatRequest(action, extra = {})", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/heartbeat/control", APPLICATIONS_INDEX_HTML)
        self.assertNotIn('id="task-server-status"', APPLICATIONS_INDEX_HTML)
        self.assertIn('await taskHeartbeatRequest("shutdown")', APPLICATIONS_INDEX_HTML)
        self.assertIn('const data = await taskHeartbeatRequest("start")', APPLICATIONS_INDEX_HTML)
        self.assertIn("function waitForTaskManagerRecovery(timeoutMs = 15000)", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("Terminate Server", APPLICATIONS_INDEX_HTML)
        self.assertIn("Terminal", APPLICATIONS_INDEX_HTML)
        self.assertIn("Git Tools", APPLICATIONS_INDEX_HTML)
        self.assertIn("git status, patch inbox, shims, and console actions", APPLICATIONS_INDEX_HTML)
        self.assertIn("Terminal", APPLICATIONS_INDEX_HTML)
        self.assertIn("Git Tools", APPLICATIONS_INDEX_HTML)
        self.assertIn("git status, patch inbox, shims, and console actions", APPLICATIONS_INDEX_HTML)
        self.assertIn("function ensureApplicationWidgets()", APPLICATIONS_INDEX_HTML)
        self.assertIn("setApplicationWidgetTicker(taskOverviewCard", APPLICATIONS_INDEX_HTML)
        self.assertIn("let taskManagerLoadingShown = false;", APPLICATIONS_INDEX_HTML)
        self.assertIn("Terminal", APPLICATIONS_INDEX_HTML)
        self.assertIn("Git Tools", APPLICATIONS_INDEX_HTML)
        self.assertIn("git status, patch inbox, shims, and console actions", APPLICATIONS_INDEX_HTML)
        self.assertIn("Repository status, patch inbox, and harness actions are ready.", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="git-tools-app"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="git-repo-dir"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="git-patch-list"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="git-patch-name"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="git-dry-run-name"', APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/git/status", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/git/patches", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/git/patch/read", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/git/patch/apply", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/git/dry-run/read", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="git-console-input"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="git-shim-list"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="git-shim-id"', APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/git/shims", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/git/shim/read", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/git/shim/run", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/git/shim/delete", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/git/shim/ordination", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/git/console/extract", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/git/console/run", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/git/control/plan", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/git/ai-shim", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="git-ai-shim"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="git-shim-ordain"', APPLICATIONS_INDEX_HTML)
        self.assertIn("Page Element Wizard", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="git-page-wizard-input"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="git-page-wizard-next"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="git-page-wizard-send-console"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="git-page-wizard-output"', APPLICATIONS_INDEX_HTML)
        self.assertIn("function advanceGitPageWizard()", APPLICATIONS_INDEX_HTML)
        self.assertIn("function buildGitPageWizardPrompt()", APPLICATIONS_INDEX_HTML)
        self.assertIn("git-tools.feature.page-wizard", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("stubbed until revision commands are exposed here", APPLICATIONS_INDEX_HTML)
        self.assertIn("Code Editor", APPLICATIONS_INDEX_HTML)
        self.assertIn("Worker", APPLICATIONS_INDEX_HTML)
        self.assertIn('data-app="worker"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="worker-app"', APPLICATIONS_INDEX_HTML)
        self.assertIn("Sell Work", APPLICATIONS_INDEX_HTML)
        self.assertIn("Use Remote Workers", APPLICATIONS_INDEX_HTML)
        self.assertIn("How others pay me", APPLICATIONS_INDEX_HTML)
        self.assertIn("Target output tokens per request", APPLICATIONS_INDEX_HTML)
        self.assertIn("Minimum credits per estimated token", APPLICATIONS_INDEX_HTML)
        self.assertIn("Only when totally idle", APPLICATIONS_INDEX_HTML)
        self.assertIn("When AI is idle", APPLICATIONS_INDEX_HTML)
        self.assertIn("Enable paid overflow", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("Advanced seller availability ideas", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("Idle windows, resource limits, model warmth", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("Future seller pricing ideas", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("Lock AI model when working", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("worker-lock-ai-model", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("worker-rental-window", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("Rent Out My Local AI", APPLICATIONS_INDEX_HTML)
        self.assertIn("Game Editor", APPLICATIONS_INDEX_HTML)
        self.assertIn('data-app="game-editor"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-app"', APPLICATIONS_INDEX_HTML)
        self.assertIn("Project-backed scene editor is ready.", APPLICATIONS_INDEX_HTML)
        self.assertIn("function initGameEditorApp()", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-preview"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-webgl-canvas"', APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/game-editor/project/write", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/game-editor/asset/upload", APPLICATIONS_INDEX_HTML)
        self.assertIn("gameEditorApi", APPLICATIONS_INDEX_HTML)
        self.assertIn("MainComputerSceneStore", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("gameEditorPlaytest", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("grapesjs@", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("GrapesJS Layout Builder", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="webgl-demo"', APPLICATIONS_INDEX_HTML)
        self.assertIn('aria-label="Scene-aware game surface"', APPLICATIONS_INDEX_HTML)
        self.assertIn("function initWebgl(", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="desktop-overlay"', APPLICATIONS_INDEX_HTML)
        self.assertIn("desktop-icon", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-app"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-prompt"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-ask-model"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-display"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-mode-basic"', APPLICATIONS_INDEX_HTML)
        self.assertIn("Basic", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-mode-graphing" class="active"', APPLICATIONS_INDEX_HTML)
        self.assertNotIn('id="calculator-mode-chat"', APPLICATIONS_INDEX_HTML)
        self.assertIn("Scientific Graphing", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-basic-panel"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-graphing-panel"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-qa-panel"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-qa-prompt"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-qa-ask"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-qa-status"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-qa-answer"', APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/calculator/qa", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-graph-expression"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-graph-canvas"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-graph-status"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-graph-draw"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-graph-reset"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-scientific-prompt"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-scientific-ask-model"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-scientific-model-status"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-scientific-keypad"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-mathics-panel"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-mathics-prompt"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-mathics-ask-model"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-mathics-model-status"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-mathics-evaluation-status"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-mathics-expression"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-mathics-evaluate"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-mathics-clear"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-mathics-output"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-mathics-examples"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-chat-panel"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="calculator-chat-notebook"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-chat-console-embed="calculator"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-chat-console-notebook-id="calculator-chat-notebook"', APPLICATIONS_INDEX_HTML)
        self.assertIn("mountCalculatorEmbeddedChat", APPLICATIONS_INDEX_HTML)
        self.assertIn("calculator-pane", APPLICATIONS_INDEX_HTML)
        self.assertIn("calculator-mathics-pane", APPLICATIONS_INDEX_HTML)
        self.assertIn("calculator-graph-controls", APPLICATIONS_INDEX_HTML)
        self.assertIn("calculator-shell.graphing-active", APPLICATIONS_INDEX_HTML)
        self.assertIn('class="calculator-shell app-widget mc-app-shell graphing-active chat-docked"', APPLICATIONS_INDEX_HTML)
        self.assertIn("calculator-workspace mc-app-workspace", APPLICATIONS_INDEX_HTML)
        self.assertIn("mc-app-primary", APPLICATIONS_INDEX_HTML)
        self.assertIn("minmax(260px, 0.72fr) minmax(480px, 1.7fr) minmax(320px, 0.9fr)", APPLICATIONS_INDEX_HTML)
        self.assertIn("height: clamp(260px, 28vw, 430px)", APPLICATIONS_INDEX_HTML)
        self.assertIn("max-height: 430px", APPLICATIONS_INDEX_HTML)
        self.assertIn("border-radius: 8px 8px 0 0", APPLICATIONS_INDEX_HTML)


    def test_launcher_app_cards_are_real_links(self) -> None:
        expected_hrefs = {
            "webgl": "/applications/webgl",
            "calculator": "/applications",
            "document": "/applications/document",
            "spreadsheet": "/applications/spreadsheet",
            "onlyoffice": "/applications/onlyoffice",
            "task-manager": "/applications/task-manager",
            "terminal": "/applications/terminal",
            "chat-console": "/applications/chat-console",
            "git-tools": "/applications/git-tools",
            "code-editor": "/applications/code-editor",
            "file-explorer": "/applications/file-explorer",
            "game-editor": "/applications/game-editor",
            "website-builder": "/applications/website-builder",
            "worker": "/applications/worker",
        }
        self.assertNotIn('<button class="app-card', APPLICATIONS_INDEX_HTML)
        for app_name, href in expected_hrefs.items():
            self.assertRegex(
                APPLICATIONS_INDEX_HTML,
                rf'<a class="app-card[^"]*" href="{re.escape(href)}" data-app="{re.escape(app_name)}"',
            )
        self.assertIn("function isPlainPrimaryAppClick(event)", APPLICATIONS_INDEX_HTML)
        self.assertIn("if (!isPlainPrimaryAppClick(event)) return;", APPLICATIONS_INDEX_HTML)
        self.assertIn("event.preventDefault();", APPLICATIONS_INDEX_HTML)
        self.assertIn("function focusActiveWorkspaceAfterPointerAppSelection(button, event)", APPLICATIONS_INDEX_HTML)
        self.assertIn('button?.closest?.(".launcher")', APPLICATIONS_INDEX_HTML)
        self.assertIn("if (!launcher || event.detail === 0) return;", APPLICATIONS_INDEX_HTML)
        self.assertIn('document.querySelector("[data-mc-component-id=\'applications.workspace\']")', APPLICATIONS_INDEX_HTML)
        self.assertIn("workspace.focus({preventScroll: true});", APPLICATIONS_INDEX_HTML)
        self.assertIn("focusActiveWorkspaceAfterPointerAppSelection(button, event);", APPLICATIONS_INDEX_HTML)


    def test_calculator_components_have_widget_metadata_layer(self) -> None:
        calculator_path = (
            Path(__file__).resolve().parents[1]
            / "main_computer"
            / "web"
            / "applications"
            / "apps"
            / "calculator.html"
        )
        parser = _ComponentMetadataParser()
        parser.feed(calculator_path.read_text(encoding="utf-8"))
        parser.close()

        components = [
            attrs
            for attrs in parser.components
            if str(attrs.get("data-mc-component-id", "")).startswith("calculator.")
        ]
        self.assertTrue(components)

        for attrs in components:
            component_id = str(attrs["data-mc-component-id"])
            expected_widget_id = "calculator." + component_id.removeprefix("calculator.").replace(".", "-")
            component_kind = attrs.get("data-mc-component-kind")
            component_label = attrs.get("data-mc-component-label")

            with self.subTest(component_id=component_id):
                self.assertEqual(expected_widget_id, attrs.get("data-mc-widget-id"))
                self.assertEqual(component_kind, attrs.get("data-mc-widget-kind"))
                self.assertEqual(component_kind, attrs.get("data-mc-widget-class"))
                self.assertEqual(component_label, attrs.get("data-mc-widget-label"))


    def test_spreadsheet_tools_have_canonical_component_hierarchy(self) -> None:
        expected = [
            'data-mc-component-id="spreadsheet.root"',
            'data-mc-component-id="spreadsheet.shell"',
            'data-mc-component-id="spreadsheet.library.panel"',
            'data-mc-component-id="spreadsheet.file.toolbar"',
            'data-mc-component-id="spreadsheet.file.import-xlsx"',
            'data-mc-component-id="spreadsheet.persist.export-csv"',
            'data-mc-component-id="spreadsheet.selection.plot"',
            'data-mc-component-id="spreadsheet.plot.canvas"',
            'data-mc-component-id="spreadsheet.grid.host"',
            'data-mc-component-id="spreadsheet.inspector.panel"',
            'data-mc-component-id="spreadsheet.inspector.run-cell"',
            'data-mc-component-id="spreadsheet.ai.generate"',
            'data-mc-component-id="spreadsheet.chat.panel"',
            'data-chat-console-embed="spreadsheet"',
            'data-chat-console-title="Chat Console"',
            'data-mc-feature-id="spreadsheet.feature.files"',
            'data-mc-feature-id="spreadsheet.feature.xlsx-import"',
            'data-mc-feature-id="spreadsheet.feature.charting"',
            'data-mc-feature-id="spreadsheet.feature.code-cells"',
            'data-mc-feature-id="spreadsheet.feature.ai-range"',
            'data-mc-feature-id="spreadsheet.feature.chat"',
            'data-mc-widget-id="spreadsheet.inspector-run-cell"',
            'data-mc-widget-id="spreadsheet.ai-generate"',
            'data-mc-source="main_computer/web/applications/apps/spreadsheet.html; main_computer/web/applications/styles/spreadsheet.css"',
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)

    def test_spreadsheet_filebar_layout_is_compact(self) -> None:
        expected = [
            'class="spreadsheet-library spreadsheet-filebar mc-app-pane mc-app-tools"',
            'aria-label="Spreadsheet file bar"',
            'data-mc-widget-label="Spreadsheet File Bar"',
            'class="spreadsheet-filebar-identity"',
            'class="spreadsheet-filebar-nav"',
            'aria-label="Spreadsheet command menus"',
            '<summary role="menuitem">File</summary>',
            '<summary role="menuitem">Workbooks</summary>',
            '<summary role="menuitem">Selection</summary>',
            'class="spreadsheet-file-list spreadsheet-filebar-menu-panel"',
            "grid-template-columns: minmax(260px, 1fr) auto;",
            "overflow: visible;",
            "position: absolute;",
            "max-height: 260px;",
            "box-shadow: 0 16px 36px rgba(0, 0, 0, 0.55);",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)


    def test_spreadsheet_embedded_chat_gets_roomier_column(self) -> None:
        expected = [
            'class="spreadsheet-chat-thread spreadsheet-embedded-chat-console mc-app-pane mc-app-notebook"',
            "grid-template-columns: minmax(0, 1fr) clamp(440px, 32vw, 560px);",
            "grid-template-columns: minmax(128px, 0.28fr) minmax(0, 1fr);",
            "grid-template-columns: 1fr;",
            "grid-template-rows: auto minmax(0, 1fr);",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)


    def test_chat_console_embeds_use_shared_plugin_renderer(self) -> None:
        expected = [
            "function chatConsoleBuildEmbeddedShell",
            "function chatConsoleMountEmbedded",
            "window.MainComputerChatConsole",
            "mountEmbedded: chatConsoleMountEmbedded",
            "data-chat-console-embedded-notebook",
            "chatConsoleEmbeddedNotebooks()",
            "chatConsoleActiveEmbeddedNotebook()",
            "chatConsoleLegacyCalculatorNotebook()",
            "Cell creation lives in the shared notebook renderer",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)
        self.assertNotIn('data-chat-console-new="python"', APPLICATIONS_INDEX_HTML)
        self.assertNotIn('data-chat-console-new="javascript"', APPLICATIONS_INDEX_HTML)


    def test_spreadsheet_components_have_widget_metadata_layer(self) -> None:
        spreadsheet_path = (
            Path(__file__).resolve().parents[1]
            / "main_computer"
            / "web"
            / "applications"
            / "apps"
            / "spreadsheet.html"
        )
        parser = _ComponentMetadataParser()
        parser.feed(spreadsheet_path.read_text(encoding="utf-8"))
        parser.close()

        components = [
            attrs
            for attrs in parser.components
            if str(attrs.get("data-mc-component-id", "")).startswith("spreadsheet.")
        ]
        self.assertTrue(components)

        for attrs in components:
            component_id = str(attrs["data-mc-component-id"])
            expected_widget_id = "spreadsheet." + component_id.removeprefix("spreadsheet.").replace(".", "-")
            component_kind = attrs.get("data-mc-component-kind")
            component_label = attrs.get("data-mc-component-label")

            with self.subTest(component_id=component_id):
                self.assertEqual(expected_widget_id, attrs.get("data-mc-widget-id"))
                self.assertEqual(component_kind, attrs.get("data-mc-widget-kind"))
                self.assertEqual(component_kind, attrs.get("data-mc-widget-class"))
                self.assertEqual(component_label, attrs.get("data-mc-widget-label"))


if __name__ == "__main__":
    unittest.main()
