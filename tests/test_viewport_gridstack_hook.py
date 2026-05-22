from __future__ import annotations

import unittest

from main_computer.viewport import APPLICATIONS_INDEX_HTML


class ViewportGridStackHookTests(unittest.TestCase):
    def test_applications_index_contains_code_editor_gridstack_hook(self) -> None:
        self.assertIn("gridstack.min.css", APPLICATIONS_INDEX_HTML)
        self.assertIn("gridstack-all.js", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="code-editor-gridstack-toggle"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="code-editor-gridstack-reset"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="code-editor-gridstack-status"', APPLICATIONS_INDEX_HTML)
        self.assertIn("main-computer-code-editor-gridstack-layout-v1", APPLICATIONS_INDEX_HTML)
        self.assertIn("main-computer-code-editor-gridstack-enabled-v1", APPLICATIONS_INDEX_HTML)
        self.assertIn("GridStack.init", APPLICATIONS_INDEX_HTML)
        self.assertIn("enableCodeEditorGridStackTest", APPLICATIONS_INDEX_HTML)
        self.assertIn('data-grid-key="file-map-panel"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-grid-key="aider-workspace"', APPLICATIONS_INDEX_HTML)
