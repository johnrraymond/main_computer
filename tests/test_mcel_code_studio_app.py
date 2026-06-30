from __future__ import annotations

from pathlib import Path
import unittest

from main_computer.viewport import APPLICATIONS_INDEX_HTML


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "main_computer" / "web" / "applications" / "apps" / "code-editor.html"
STYLE_PATH = ROOT / "main_computer" / "web" / "applications" / "styles" / "code-editor.css"
SCRIPT_PATH = ROOT / "main_computer" / "web" / "applications" / "scripts" / "code-editor-mcel-studio.js"
PRETTY_DOC = ROOT / "pretty_docs" / "mcel-code-studio-example.md"


class McelCodeStudioAppTests(unittest.TestCase):
    def test_code_editor_is_source_safe_workbench_not_page_stack(self) -> None:
        app = APP_PATH.read_text(encoding="utf-8")
        expected = [
            "MCEL Code Studio",
            "source-safe-code-editor",
            "code-studio-titlebar",
            "code-studio-activitybar",
            "code-studio-sidebar",
            "code-studio-editor-group",
            "code-studio-inspector",
            "code-studio-statusbar",
            'id="code-studio-source-editor"',
            'id="code-studio-runtime-preview"',
            'id="code-studio-serialized-output"',
            'id="code-studio-contract-report"',
            'id="code-studio-damage-runtime"',
            'id="code-studio-repair-runtime"',
            'id="code-studio-bottom-panel" data-expanded="false"',
            'id="code-studio-toggle-assistant"',
            "Author-owned source",
            "Generated runtime",
            "Serialized clean source",
            "MCEL contract report",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, app)

        self.assertNotIn("<main class=\"code-studio-editor-group\"", app)
        self.assertNotIn("<header class=\"code-studio-titlebar\"", app)

    def test_layout_is_locked_to_a_workbench_viewport(self) -> None:
        style = STYLE_PATH.read_text(encoding="utf-8")
        expected = [
            ".code-editor-app {",
            "height: clamp(720px, calc(100dvh - 150px), 1040px);",
            "max-height: calc(100dvh - 112px);",
            ".code-studio-shell {",
            "grid-template-rows: 36px minmax(0, 1fr) 24px;",
            ".code-studio-body {",
            "grid-template-columns: 48px clamp(230px, 18vw, 300px) minmax(480px, 1fr) clamp(280px, 22vw, 360px);",
            ".code-studio-sidebar {",
            "grid-template-rows: auto auto minmax(0, 1fr);",
            ".code-studio-editor-group {",
            "grid-template-columns: none;",
            "padding: 0;",
            ".code-studio-bottom-panel[data-expanded=\"false\"] .code-studio-aider-shell",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, style)

    def test_existing_aider_file_map_docs_and_gridstack_hooks_remain_available(self) -> None:
        expected = [
            'data-mc-widget-id="code-editor.file-map-panel"',
            'data-mc-widget-id="code-editor.aider-workspace"',
            'id="file-map-refresh"',
            'id="file-map-apply"',
            'id="aider-instruction"',
            'id="aider-preview"',
            'id="aider-run"',
            'id="aider-output"',
            'id="aider-history-list"',
            'id="aider-archive-list"',
            'id="code-editor-doc-viewport"',
            'id="code-editor-doc-load"',
            'id="code-editor-gridstack-toggle"',
            'id="code-editor-gridstack-reset"',
            'id="code-editor-gridstack-status"',
            "code-editor-file-map.js",
            "code-editor-aider-actions.js",
            "code-editor-documentation-viewport.js",
            "window.MainComputerCodeStudio",
            "gridstack.min.css",
            "gridstack-all.js",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)

    def test_code_studio_script_exposes_contract_workflow(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        expected = [
            "window.MainComputerCodeStudio",
            "validateSource",
            "renderRuntime",
            "damageRuntime",
            "repairRuntime",
            "serializeCleanSource",
            "commitRuntimeDraft",
            "data-mc-generated=\"runtime\"",
            "data-mc-serialize=\"omit\"",
            "source-safe-code-editor",
            "code-studio-toggle-assistant",
            "code-studio-bottom-panel",
            "Layout locked",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, script)

    def test_pretty_doc_explains_better_than_react_lane(self) -> None:
        doc = PRETTY_DOC.read_text(encoding="utf-8")
        expected = [
            "MCEL Code Studio",
            "React, Vue, Svelte, and Web Components are better choices",
            "source-safe-code-editor",
            "author-owned source is canonical",
            "generated editor chrome is runtime-only",
            "dirty runtime drafts do not serialize until committed",
            "Repair runtime chrome from the author-owned source",
            "Serialize clean source without generated runtime nodes",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, doc)
