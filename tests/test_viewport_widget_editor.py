from __future__ import annotations

import re
import unittest

from main_computer.viewport import APPLICATIONS_INDEX_HTML


class ViewportWidgetEditorTests(unittest.TestCase):
    def test_applications_index_contains_widget_editor_hooks(self) -> None:
        expected = [
            "mc-widget-editor-root",
            "mc-widget-editor-pane",
            "main-computer-widget-editor-pane-v1",
            "main-computer-widget-overrides-v1",
            "data-mc-widget-id",
            "code-editor.aider-workspace",
            "code-editor.aider-archive-list",
            "code-editor.aider-history-list",
            "code-editor.aider-output",
            'data-mc-widget-class="list"',
            'data-mc-widget-class="action"',
            'data-mc-widget-class="input"',
            'data-mc-widget-class="box"',
            "function ensureWidgetEditorChrome",
            "function refreshWidgetEditorHandles",
            "function scheduleWidgetEditorHandleRefresh",
            "function widgetEditorVisibleRect",
            "function widgetEditorResolvedSettings",
            "const MainComputerWidgets",
            "const widgetEditorSchemas",
            "function resolveWidgetEditorSchema",
            "function sanitizeWidgetEditorOverride",
            "function normalizeWidgetEditorOverridePatch",
            "function handleWidgetEditorCtrlClick",
            "rememberWidgetEditorFocus",
            "restoreWidgetEditorFocus",
            "function openWidgetEditorFor",
            "function resetSelectedWidgetOverrides",
            "function nearestWidgetEditorDockFromRect",
            "widgetEditorChromeReady",
            "paneCreated",
            "renderWidgetEditorPane();",
            "applyWidgetOverrides();",
            'element.closest("#code-editor-app")',
            'window.addEventListener("load", markWidgetEditorChromeReady',
            "document.fonts",
            'document.addEventListener("mousedown", handleWidgetEditorCtrlClick, true)',
            'document.addEventListener("click", handleWidgetEditorCtrlClick, true)',
            "event.preventDefault();",
            "event.stopPropagation();",
            'if (target.matches("input, textarea, select, button")) target.blur();',
            "openWidgetEditorFor(widget);",
            'document.addEventListener("scroll", () =>',
            "scheduleWidgetEditorHandleRefresh();",
            "updateWidgetEditorSelection();",
            "MutationObserver",
            ".aider-shell",
            "gap: var(--mc-density-gap, 24px)",
            "column-gap: 24px",
            "width: var(--mc-widget-width-preset-value, revert-layer)",
            "data-mc-density",
            "data-mc-layout",
            "data-mc-width-preset",
            "data-mc-min-height-preset",
            "data-mc-item-display-preset",
            'data-mc-widget-kind="repeater"',
            'data-mc-widget-kind="container"',
            'data-mc-widget-kind="input"',
            'data-mc-widget-kind="action"',
            'data-mc-widget-kind="output"',
            'code-editor.file-map-list',
            'code-editor.aider-history-list',
            'code-editor.aider-archive-list',
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)

    def test_widget_editor_chrome_creation_does_not_rerender_on_refresh(self) -> None:
        bad_pattern = "renderWidgetEditorPane();\n      applyWidgetOverrides();\n    }\n\n    function getEditableWidgets"
        self.assertNotIn(bad_pattern, APPLICATIONS_INDEX_HTML)

    def test_widget_editor_uses_ctrl_click_without_picker_handles(self) -> None:
        expected = [
            "function refreshWidgetEditorChrome",
            'widgetEditorRoot.querySelectorAll(".mc-widget-handle").forEach((handle) => handle.remove());',
            'if (!(event.ctrlKey || event.metaKey)) return;',
            'if (!target || target.closest("#mc-widget-editor-root")) return;',
            "const widget = MainComputerWidgets.ownerWidgetForTarget(target);",
            "openWidgetEditorFor(widget);",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)

        forbidden = [
            ".mc-widget-handle {",
            ".mc-widget-handle:hover",
            ".mc-widget-handle.active",
            "function positionWidgetEditorHandle",
            "getWidgetHandleAnchorRect",
            "findWidgetHandleSafeSide",
            "data-mc-widget-handle-for",
            "widgetEditorRoot.append(handle)",
            "document.addEventListener(\"contextmenu\"",
            "--mc-widget-handle-size",
            "--mc-widget-handle-gutter",
            "--mc-widget-handle-offset",
            'data-widget-editor-field="width"',
            'data-widget-editor-field="height"',
            'data-widget-editor-field="maxHeight"',
            "function normalizeWidgetEditorLength",
            "data-mc-widget-local-handle",
            "mc-widget-handle-inside",
            "mc-widget-handle-host",
            "mc-widget-form-handle",
            "element.append(handle)",
            'element.insertAdjacentElement("afterend", handle)',
            "--mc-widget-edit-gutter-reserve",
            "margin-right: var(--mc-widget-edit-gutter-reserve",
        ]
        for text in forbidden:
            with self.subTest(text=text):
                self.assertNotIn(text, APPLICATIONS_INDEX_HTML)

    def test_widget_editor_tracks_generated_items_as_repeater_owned_data(self) -> None:
        expected = [
            "row.dataset.mcGeneratedItem = \"true\";",
            "row.dataset.mcItemKind = entry.kind === \"dir\" ? \"file-map-directory\" : \"file-map-file\";",
            "row.dataset.mcItemKey = entry.path || entry.name || \"\";",
            "row.dataset.mcComponentOwner = \"code-editor.file-map.list\";",
            "row.dataset.mcFeatureId = \"code-editor.feature.file-map\";",
            "card.dataset.mcGeneratedItem = \"true\";",
            "card.dataset.mcItemKind = \"aider-history-entry\";",
            "card.dataset.mcItemKey = entry.id || entry.timestamp || aiderHistoryTitle(entry);",
            "card.dataset.mcComponentOwner = \"code-editor.aider.history-list\";",
            "card.dataset.mcFeatureId = \"code-editor.feature.aider-context\";",
            "option.dataset.mcGeneratedItem = \"true\";",
            "option.dataset.mcItemKind = \"aider-archive-option\";",
            "option.dataset.mcItemKey = archive.id || archive.label || \"\";",
            "option.dataset.mcComponentOwner = \"code-editor.aider.archive-list\";",
            "option.dataset.mcFeatureId = \"code-editor.feature.aider-context\";",
            "closestGeneratedItem(target)",
            "ownerWidgetForTarget(target)",
            "if (item) return item.closest(\"[data-mc-widget-id]\");",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)

        forbidden = [
            "row.dataset.mcWidgetId",
            "card.dataset.mcWidgetId",
            "option.dataset.mcWidgetId",
            "row.setAttribute(\"data-mc-widget-id\"",
            "card.setAttribute(\"data-mc-widget-id\"",
            "option.setAttribute(\"data-mc-widget-id\"",
        ]
        for text in forbidden:
            with self.subTest(text=text):
                self.assertNotIn(text, APPLICATIONS_INDEX_HTML)

    def test_code_editor_has_canonical_component_hierarchy(self) -> None:
        expected = [
            'data-mc-component-id="main-computer.applications"',
            'data-mc-component-id="applications.launcher"',
            'data-mc-component-id="applications.workspace"',
            'data-mc-component-id="applications.card.code-editor"',
            'data-mc-component-id="code-editor.root"',
            'data-mc-component-kind="app"',
            'data-mc-component-owner="applications.workspace"',
            'data-mc-component-id="code-editor.file-map.panel"',
            'data-mc-component-id="code-editor.file-map.toolbar"',
            'data-mc-component-id="code-editor.file-map.search"',
            'data-mc-component-id="code-editor.file-map.refresh"',
            'data-mc-component-id="code-editor.file-map.apply"',
            'data-mc-component-id="code-editor.file-map.status"',
            'data-mc-component-id="code-editor.file-map.list"',
            'data-mc-component-id="code-editor.aider.workspace"',
            'data-mc-component-id="code-editor.aider.settings"',
            'data-mc-component-id="code-editor.aider.repo"',
            'data-mc-component-id="code-editor.aider.model"',
            'data-mc-component-id="code-editor.aider.timeout-seconds"',
            'data-mc-component-id="code-editor.aider.selected-files"',
            'data-mc-component-id="code-editor.aider.instruction"',
            'data-mc-component-id="code-editor.aider.actions"',
            'data-mc-component-id="code-editor.aider.preview"',
            'data-mc-component-id="code-editor.aider.run"',
            'data-mc-component-id="code-editor.aider.dry-run"',
            'data-mc-component-id="code-editor.aider.output"',
            'data-mc-component-id="code-editor.aider.context-toolbar"',
            'data-mc-component-id="code-editor.aider.session-meta"',
            'data-mc-component-id="code-editor.aider.archive-current"',
            'data-mc-component-id="code-editor.aider.reset-context"',
            'data-mc-component-id="code-editor.aider.history-panel"',
            'data-mc-component-id="code-editor.aider.history-list"',
            'data-mc-component-id="code-editor.aider.archive-panel"',
            'data-mc-component-id="code-editor.aider.archive-list"',
            'data-mc-component-id="code-editor.aider.archive-meta"',
            'data-mc-component-id="code-editor.aider.load-archive"',
            'data-mc-feature-id="code-editor.feature.file-map"',
            'data-mc-feature-id="code-editor.feature.aider-run"',
            'data-mc-feature-id="code-editor.feature.aider-context"',
            'data-mc-source="main_computer/web/applications/apps/code-editor.html; main_computer/web/applications/styles/code-editor.css"',
            "/api/applications/editor/files",
            "/api/applications/aider/prepare",
            "/api/applications/aider/run",
            "/api/applications/aider/context",
            "/api/applications/aider/context/archive",
            "/api/applications/aider/context/reset",
            "/api/applications/aider/context/load",
            'data-mc-widget-id="code-editor.aider-run"',
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)

    def test_code_editor_component_ids_are_duplicate_free(self) -> None:
        component_ids = re.findall(r'data-mc-component-id="([^"]+)"', APPLICATIONS_INDEX_HTML)
        duplicates = sorted({component_id for component_id in component_ids if component_ids.count(component_id) > 1})
        self.assertEqual(duplicates, [])

    def test_widget_editor_uses_local_html_escape_helper(self) -> None:
        helper_index = APPLICATIONS_INDEX_HTML.find("function widgetEditorEscapeHtml")
        render_index = APPLICATIONS_INDEX_HTML.find("function renderWidgetEditorPane")
        self.assertGreaterEqual(helper_index, 0)
        self.assertGreater(render_index, helper_index)
        self.assertIn("${widgetEditorEscapeHtml(label)}", APPLICATIONS_INDEX_HTML)
        self.assertIn("widgetEditorEscapeHtml(target.dataset.mcWidgetLabel || \"\")", APPLICATIONS_INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
