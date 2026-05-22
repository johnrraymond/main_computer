from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.viewport import APPLICATIONS_INDEX_HTML, ViewportServer


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "code-editor-documentation-viewport.js"
PROJECT_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "project-documentation-display.js"
STYLE_PATH = Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "styles" / "code-editor.css"
APP_ROUTING_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "app-routing.js"


class CodeEditorDocumentationViewportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)
        docs_root = self.repo / "generated_component_docs"
        (docs_root / "nodes").mkdir(parents=True)
        (docs_root / "nodes" / "code-editor.viewport.root.html").write_text(
            '<article class="mc-component-doc" data-mc-doc-target="code-editor.viewport.root"><h1>Documentation Viewport</h1><p>Seed documentation.</p></article>',
            encoding="utf-8",
        )
        (docs_root / "nodes" / "code-editor.aider.run.html").write_text(
            '<article class="mc-component-doc" data-mc-doc-target="code-editor.aider.run"><h1>Run Aider</h1></article>',
            encoding="utf-8",
        )
        (docs_root / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "entries": [
                        {
                            "id": "code-editor.viewport.root",
                            "aliases": [],
                            "doc_path": "nodes/code-editor.viewport.root.html",
                            "content_type": "text/html",
                            "feature_id": "code-editor.feature.documentation-viewport",
                            "status": "current",
                        },
                        {
                            "id": "code-editor.aider.run",
                            "aliases": ["aider-run", "code-editor.aider-run"],
                            "title": "Run Aider",
                            "doc_path": "nodes/code-editor.aider.run.html",
                            "content_type": "text/html",
                            "status": "current",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
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

    def _post(self, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        request = Request(
            f"{self.base}{path}",
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

    def test_applications_index_contains_documentation_viewport_components(self) -> None:
        for text in [
            'data-mc-component-id="code-editor.viewport.root"',
            'data-mc-component-id="code-editor.viewport.toolbar"',
            'data-mc-component-id="code-editor.viewport.surface"',
            'data-mc-component-id="code-editor.viewport.visual"',
            'data-mc-component-id="code-editor.viewport.vram-canvas"',
            'data-mc-component-id="code-editor.viewport.script-widget"',
            'data-mc-component-id="code-editor.viewport.script-run"',
            'data-mc-component-id="code-editor.viewport.vram-reset"',
            'data-mc-component-id="code-editor.viewport.load-doc"',
            'data-mc-component-id="code-editor.viewport.status"',
            'data-mc-feature-id="code-editor.feature.documentation-viewport"',
            "code-editor-documentation-viewport.js",
            "project-documentation-display.js",
            "window.MainComputerCodeEditorViewport",
            "window.MainComputerProjectDocumentation",
            'data-mc-component-id="project.documentation-display"',
            'data-mc-component-id="project.documentation-display.frame"',
            'data-mc-feature-id="project.feature.documentation-display"',
            "sandbox=\"\"",
            "/api/applications/component-docs/manifest",
            "/api/applications/component-docs/read",
            "Docs Compact 720x480",
            "Docs Wide 1024x640",
            "Desktop 1440x900",
            "Mobile 390x844",
        ]:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)


    def test_documentation_viewport_defaults_collapsed_above_instruction(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        style = STYLE_PATH.read_text(encoding="utf-8")

        self.assertIn('id="code-editor-doc-viewport" data-mode="collapsed"', APPLICATIONS_INDEX_HTML)
        self.assertIn('<option value="collapsed" selected>Collapsed</option>', APPLICATIONS_INDEX_HTML)
        self.assertIn('mode: "collapsed"', script)
        self.assertIn('codeEditorDocViewportState.mode === "collapsed" ? "Expand Viewport" : "Collapse Viewport"', script)
        self.assertIn('.code-editor-doc-viewport[data-mode="collapsed"] .code-editor-doc-viewport-toolbar > :not(#code-editor-doc-collapse)', style)
        self.assertIn('.code-editor-doc-viewport[data-mode="collapsed"] .code-editor-doc-status', style)
        self.assertIn('.code-editor-doc-script-widget[hidden]', style)
        self.assertLess(
            APPLICATIONS_INDEX_HTML.index('id="code-editor-doc-viewport"'),
            APPLICATIONS_INDEX_HTML.index('data-mc-component-id="code-editor.aider.instruction"'),
        )

    def test_script_controlled_vram_viewport_has_widget_and_api(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        style = STYLE_PATH.read_text(encoding="utf-8")

        for text in [
            'id="code-editor-doc-vram" class="code-editor-doc-vram vram-style"',
            'id="code-editor-doc-script-widget" hidden',
            'id="code-editor-doc-script-source"',
            'id="code-editor-doc-script-run"',
            'id="code-editor-doc-vram-reset"',
            'vram.reset({ width: 320, height: 200, fill: [8, 10, 12, 255] });',
            'vram.setPixels(wave);',
            'console.log("VRAM size", vram.getSize());',
            'API: viewport.vram.reset(), blitImage(), setPixel(), setPixels(), fillRect(), getPixel(), getImageData().',
        ]:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)

        for text in [
            'reset: resetCodeEditorDocVram',
            'blitImage: blitCodeEditorDocVramImage',
            'setPixel: setCodeEditorDocVramPixel',
            'setPixels: setCodeEditorDocVramPixels',
            'fillRect: fillCodeEditorDocVramRect',
            'getPixel: getCodeEditorDocVramPixel',
            'getImageData: getCodeEditorDocVramImageData',
            'getSize: getCodeEditorDocVramSize',
            'new AsyncFunction(',
            '"viewport",',
            '"console",',
            'const vram = viewport.vram;',
            '${scriptSource}',
            'codeEditorDocFrame.hidden = ["inspect", "script", "collapsed"].includes(codeEditorDocViewportState.mode)',
            'codeEditorDocVramCanvas.hidden = codeEditorDocViewportState.mode !== "script"',
            'codeEditorDocScriptWidget.hidden = codeEditorDocViewportState.mode !== "script"',
        ]:
            with self.subTest(text=text):
                self.assertIn(text, script)

        self.assertNotIn('new AsyncFunction("viewport", "vram", "console"', script)
        self.assertNotIn('await runner(window.MainComputerCodeEditorViewport, codeEditorDocVramApi, codeEditorDocScriptConsole())', script)

        self.assertIn('.code-editor-doc-viewport[data-mode="script"] .code-editor-doc-surface', style)
        self.assertIn('grid-template-columns: minmax(0, var(--code-editor-doc-width, 1024px)) minmax(280px, 360px);', style)
        self.assertIn('image-rendering: pixelated;', style)
        self.assertIn('@media (max-width: 900px)', style)

    def test_component_ids_are_duplicate_free(self) -> None:
        component_ids = re.findall(r'data-mc-component-id="([^"]+)"', APPLICATIONS_INDEX_HTML)
        duplicates = sorted({component_id for component_id in component_ids if component_ids.count(component_id) > 1})
        self.assertEqual(duplicates, [])

    def test_component_docs_read_rejects_traversal(self) -> None:
        error = self._post_error("/api/applications/component-docs/read", {"path": "../README.md"})
        self.assertEqual(error.code, 400)

    def test_component_docs_read_rejects_absolute_path(self) -> None:
        error = self._post_error("/api/applications/component-docs/read", {"path": str((self.repo / "generated_component_docs" / "nodes" / "code-editor.viewport.root.html").resolve())})
        self.assertEqual(error.code, 400)

    def test_component_docs_read_returns_seed_html(self) -> None:
        data = self._post("/api/applications/component-docs/read", {"id": "code-editor.viewport.root"})
        self.assertTrue(data["ok"])
        self.assertTrue(data["exists"])
        self.assertEqual(data["content_type"], "text/html")
        self.assertEqual(data["path"], "nodes/code-editor.viewport.root.html")
        self.assertIn("Documentation Viewport", data["content"])

    def test_component_docs_read_accepts_alias_and_returns_canonical_id(self) -> None:
        data = self._post("/api/applications/component-docs/read", {"id": "aider-run"})
        self.assertTrue(data["ok"])
        self.assertTrue(data["exists"])
        self.assertEqual(data["id"], "code-editor.aider.run")
        self.assertEqual(data["content_type"], "text/html")
        self.assertEqual(data["path"], "nodes/code-editor.aider.run.html")
        self.assertIn("Run Aider", data["content"])

    def test_component_docs_read_tolerates_legacy_prefixed_manifest_path(self) -> None:
        manifest_path = self.repo / "generated_component_docs" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["entries"][0]["doc_path"] = "generated_component_docs/nodes/code-editor.viewport.root.html"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        data = self._post("/api/applications/component-docs/read", {"id": "code-editor.viewport.root"})
        self.assertTrue(data["ok"])
        self.assertTrue(data["exists"])
        self.assertEqual(data["path"], "nodes/code-editor.viewport.root.html")

    def test_component_docs_read_returns_missing_doc_metadata(self) -> None:
        data = self._post("/api/applications/component-docs/read", {"id": "code-editor.missing"})
        self.assertTrue(data["ok"])
        self.assertFalse(data["exists"])
        self.assertEqual(data["content_type"], "text/html")
        self.assertEqual(data["path"], "nodes/code-editor.missing.html")
        self.assertIn("No generated documentation yet", data["content"])

    def test_seed_generated_html_contains_no_script(self) -> None:
        seed_path = Path(__file__).resolve().parents[1] / "generated_component_docs" / "nodes" / "code-editor.viewport.root.html"
        content = seed_path.read_text(encoding="utf-8").lower()
        self.assertNotIn("<script", content)
        self.assertNotIn("onclick=", content)

    def test_project_documentation_display_wires_alt_click_like_widget_editor_ctrl_click(self) -> None:
        project_script = PROJECT_SCRIPT_PATH.read_text(encoding="utf-8")
        widget_script = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "widget-editor-layout.js").read_text(encoding="utf-8")
        self.assertIn('document.addEventListener("click", handleProjectDocAltClick, true)', project_script)
        self.assertIn("function handleWidgetEditorCtrlClick(event)", widget_script)
        self.assertIn("event.altKey", project_script)
        self.assertIn("event.ctrlKey", project_script)
        self.assertIn("event.metaKey", project_script)
        self.assertIn("event.preventDefault();", project_script)
        self.assertIn("event.stopPropagation();", project_script)
        self.assertIn("window.MainComputerProjectDocumentation.loadDoc(resolvedId)", project_script)

    def test_project_documentation_display_alt_click_prevents_only_after_resolution(self) -> None:
        script = PROJECT_SCRIPT_PATH.read_text(encoding="utf-8")
        handler = re.search(r"function handleProjectDocAltClick\(event\) \{(?P<body>.*?)\n    \}", script, re.S)
        self.assertIsNotNone(handler)
        body = handler.group("body")
        self.assertLess(body.index("if (!resolvedId) return;"), body.index("event.preventDefault();"))
        self.assertLess(body.index("if (!resolvedId) return;"), body.index("event.stopPropagation();"))

    def test_project_documentation_display_resolver_uses_manifest_alias_lookup(self) -> None:
        script = PROJECT_SCRIPT_PATH.read_text(encoding="utf-8")
        resolver = re.search(r"function resolveProjectDocClickTarget\(target\) \{(?P<body>.*?)\n    \}", script, re.S)
        self.assertIsNotNone(resolver)
        body = resolver.group("body")
        self.assertIn("projectDocDisplayManifestLookup.get(String(candidate))", body)
        self.assertIn("return candidates[0] || \"\";", body)
        self.assertIn("window.MainComputerProjectDocumentation.loadDoc(resolvedId)", script)
        self.assertIn('id="aider-dry-run"', APPLICATIONS_INDEX_HTML)

    def test_project_documentation_display_resolver_falls_back_to_component_and_widget_ids(self) -> None:
        script = PROJECT_SCRIPT_PATH.read_text(encoding="utf-8")
        candidates = re.search(r"function projectDocClickCandidates\(target\) \{(?P<body>.*?)\n    \}", script, re.S)
        self.assertIsNotNone(candidates)
        body = candidates.group("body")
        self.assertIn("element.id", body)
        self.assertIn("const generatedItem = element.closest(\"[data-mc-generated-item]\")", body)
        self.assertIn("generatedItem?.dataset.mcComponentOwner", body)
        self.assertIn("generatedItem?.dataset.mcFeatureId", body)
        self.assertIn('closest("[data-mc-doc-id]")?.dataset.mcDocId', body)
        self.assertIn('closest("[data-mc-component-id]")?.dataset.mcComponentId', body)
        self.assertIn('closest("[data-mc-widget-id]")?.dataset.mcWidgetId', body)
        self.assertIn('closest("[data-mc-component-owner]")?.dataset.mcComponentOwner', body)
        self.assertIn('closest("[data-mc-feature-id]")?.dataset.mcFeatureId', body)
        self.assertIn("return [...new Set(candidates)];", body)
        self.assertLess(body.index("generatedItem?.dataset.mcComponentOwner"), body.index('closest("[data-mc-component-id]")'))
        self.assertLess(body.index('closest("[data-mc-component-id]")'), body.index('closest("[data-mc-widget-id]")'))
        self.assertLess(body.index('closest("[data-mc-widget-id]")'), body.index("element.id"))

    def test_project_documentation_display_manifest_cache_maps_aliases_to_canonical_ids(self) -> None:
        script = PROJECT_SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("const projectDocDisplayManifestLookup = new Map()", script)
        self.assertIn("projectDocDisplayManifestLookup.set(String(entry.id), String(entry.id))", script)
        self.assertIn("projectDocDisplayManifestLookup.set(String(alias), String(entry.id))", script)
        self.assertIn('id="aider-run"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-mc-component-id="code-editor.aider.run"', APPLICATIONS_INDEX_HTML)

    def test_code_editor_route_does_not_autoload_page_documentation(self) -> None:
        script = APP_ROUTING_SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertNotIn('window.MainComputerCodeEditorViewport?.loadDoc?.("code-editor.viewport.root")', script)


if __name__ == "__main__":
    unittest.main()
