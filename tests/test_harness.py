from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from main_computer.harness import WidgetHarness


class HarnessTests(unittest.TestCase):
    def test_widget_harness_runs_against_disposable_server(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            report = WidgetHarness(output_dir=Path(temp_dir)).run()

        self.assertTrue(report["ok"])
        self.assertGreaterEqual(len(report["checks"]), 10)
        self.assertTrue(any(check["name"] == "graphical-control-panel-services-load" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-document-guide-loads" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-document-selection-restores" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-document-draft-banner" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-document-discard-draft" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-spreadsheet-embedded-chat-loads" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-spreadsheet-embedded-chat-links-thread" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-spreadsheet-embedded-chat-adds-js-cell" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-spreadsheet-embedded-chat-renders-importable-snippet" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-spreadsheet-chat-imports-snippet-to-selected-cell" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-spreadsheet-chat-import-records-history" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-game-editor-loads" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-game-editor-save-persists" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-game-editor-asset-upload" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-game-editor-defaults-to-webgl-demo" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-game-editor-edits-webgl-demo" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-game-editor-live-webgl-viewport" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-game-editor-webgl-updates-from-inspector" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-webgl-loads-project-backed-demo" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-webgl-project-backed-after-editor-save" for check in report["checks"]))
        self.assertTrue(any(check["name"] == "applications-webgl-demo-save-persists" for check in report["checks"]))
