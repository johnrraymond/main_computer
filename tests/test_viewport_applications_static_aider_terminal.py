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


class ViewportApplicationsStaticAiderTerminalTests(unittest.TestCase):
    def test_applications_index_contains_aider_and_terminal_hooks(self) -> None:
            self.assertIn("grid-template-columns: minmax(220px, 340px) minmax(0, 1fr)", APPLICATIONS_INDEX_HTML)
            self.assertIn('id="aider-instruction"', APPLICATIONS_INDEX_HTML)
            self.assertIn('<select id="aider-model">', APPLICATIONS_INDEX_HTML)
            self.assertIn('value="ollama_chat/gemma4:26b"', APPLICATIONS_INDEX_HTML)
            self.assertIn("Balanced: llama3.1:8b", APPLICATIONS_INDEX_HTML)
            self.assertIn("Default local: gemma4:26b", APPLICATIONS_INDEX_HTML)
            self.assertIn('id="aider-dry-run"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="aider-preview"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="aider-run"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="aider-output"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="aider-session-meta"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="aider-archive-current"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="aider-reset-context"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="aider-history-list"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="aider-archive-list"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="aider-load-archive"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="file-map-search"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="file-map-refresh"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="file-map-apply"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="file-map-list"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="file-map-status"', APPLICATIONS_INDEX_HTML)
            self.assertIn("Aider action dock", APPLICATIONS_INDEX_HTML)
            self.assertIn("Editor file map", APPLICATIONS_INDEX_HTML)
            self.assertIn("/api/applications/editor/files", APPLICATIONS_INDEX_HTML)
            self.assertIn("/api/applications/aider/context", APPLICATIONS_INDEX_HTML)
            self.assertIn("fileMapMarked", APPLICATIONS_INDEX_HTML)
            self.assertIn("syncAiderFilesFromMarked", APPLICATIONS_INDEX_HTML)
            self.assertIn("main-computer-aider-map-files-v1", APPLICATIONS_INDEX_HTML)
            self.assertIn("toggleFileExplorerDir", APPLICATIONS_INDEX_HTML)
            self.assertIn("loadFileExplorerDir", APPLICATIONS_INDEX_HTML)
            self.assertIn("fileExplorerNodes", APPLICATIONS_INDEX_HTML)
            self.assertIn("fileExplorerOpen", APPLICATIONS_INDEX_HTML)
            self.assertIn("Web Context History", APPLICATIONS_INDEX_HTML)
            self.assertIn("Archived Contexts", APPLICATIONS_INDEX_HTML)
            self.assertIn("Latest result", APPLICATIONS_INDEX_HTML)
            self.assertIn("Prompt", APPLICATIONS_INDEX_HTML)
            self.assertIn("aider-result", APPLICATIONS_INDEX_HTML)
            self.assertIn("loadAiderContext", APPLICATIONS_INDEX_HTML)
            self.assertIn("renderAiderContext", APPLICATIONS_INDEX_HTML)
            self.assertIn("main-computer-aider-instruction-v1", APPLICATIONS_INDEX_HTML)
            self.assertIn("loadAiderInstructionDraft", APPLICATIONS_INDEX_HTML)
            self.assertIn("saveAiderInstructionDraft", APPLICATIONS_INDEX_HTML)
            self.assertIn('aiderInstruction.readOnly = busy;', APPLICATIONS_INDEX_HTML)
            self.assertIn('aria-readonly', APPLICATIONS_INDEX_HTML)
            self.assertNotIn("runningActivity && runningActivity.archive_id", APPLICATIONS_INDEX_HTML)
            self.assertIn("const selectedActivity = latestAiderActivityForArchive(selectedAiderArchiveId());", APPLICATIONS_INDEX_HTML)
            self.assertIn("fileExplorerAutoLoaded", APPLICATIONS_INDEX_HTML)
            self.assertIn("if (!fileExplorerAutoLoaded)", APPLICATIONS_INDEX_HTML)
            self.assertIn("files: [...fileMapMarked].sort()", APPLICATIONS_INDEX_HTML)
            self.assertIn("/api/applications/editor/read", APPLICATIONS_INDEX_HTML)
            self.assertIn("isReadOnlyEditorInstruction", APPLICATIONS_INDEX_HTML)
            self.assertIn("Activity console", APPLICATIONS_INDEX_HTML)
            self.assertIn("cleanedAiderStdout", APPLICATIONS_INDEX_HTML)
            self.assertIn("stripInitialAiderPreamble", APPLICATIONS_INDEX_HTML)
            self.assertIn("stripTrailingAiderTokenLine", APPLICATIONS_INDEX_HTML)
            self.assertIn("isAiderStartupWarningLine", APPLICATIONS_INDEX_HTML)
            self.assertIn("isAiderHeaderDetailLine", APPLICATIONS_INDEX_HTML)
            self.assertIn("skipAiderBannerBlock", APPLICATIONS_INDEX_HTML)
            self.assertNotIn("AIDER_NOISE_LINE_RE", APPLICATIONS_INDEX_HTML)
            self.assertIn("userFacingAiderResult", APPLICATIONS_INDEX_HTML)
            self.assertIn("renderAiderResult", APPLICATIONS_INDEX_HTML)
            self.assertIn("aider-console", APPLICATIONS_INDEX_HTML)
            self.assertIn("aider-result", APPLICATIONS_INDEX_HTML)
            self.assertIn("startAiderTimer", APPLICATIONS_INDEX_HTML)
            self.assertIn("stopAiderTimer", APPLICATIONS_INDEX_HTML)
            self.assertIn('activity.started_at ? "epoch" : "performance"', APPLICATIONS_INDEX_HTML)
            self.assertIn("formatDuration", APPLICATIONS_INDEX_HTML)
            self.assertIn("frontend_duration_ms", APPLICATIONS_INDEX_HTML)
            self.assertIn("/api/applications/aider/prepare", APPLICATIONS_INDEX_HTML)
            self.assertIn("/api/applications/aider/run", APPLICATIONS_INDEX_HTML)
            self.assertIn("splitAiderFiles", APPLICATIONS_INDEX_HTML)
            self.assertIn("sendAiderAction", APPLICATIONS_INDEX_HTML)
            self.assertIn('id="terminal-analysis"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="terminal-analysis-toggle"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="terminal-ai-prompt"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="terminal-ai-suggest"', APPLICATIONS_INDEX_HTML)
            self.assertIn('id="terminal-ai-status"', APPLICATIONS_INDEX_HTML)
            self.assertIn("stageTerminalCommand", APPLICATIONS_INDEX_HTML)
            self.assertIn("/api/applications/terminal/suggest", APPLICATIONS_INDEX_HTML)
            self.assertIn("terminal-analysis-panel", APPLICATIONS_INDEX_HTML)

    def test_code_editor_aider_layout_has_room_for_output(self) -> None:
            code_editor_source = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "apps" / "code-editor.html").read_text(encoding="utf-8")
            code_editor_css = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "styles" / "code-editor.css").read_text(encoding="utf-8")
            context_js = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "code-editor-aider-context.js").read_text(encoding="utf-8")

            for hook in [
                'id="aider-output"',
                'id="aider-session-meta"',
                'id="aider-history-list"',
                'id="aider-archive-list"',
                'id="aider-run"',
                'id="aider-preview"',
                'data-mc-widget-id="code-editor.file-map-panel"',
                'data-mc-widget-id="code-editor.aider-workspace"',
            ]:
                with self.subTest(hook=hook):
                    self.assertIn(hook, code_editor_source)

            self.assertNotIn('file-map-panel mc-panel mc-sidebar app-widget', code_editor_source)
            self.assertNotIn('aider-workspace app-widget', code_editor_source)
            self.assertIn("overflow: auto;", code_editor_css)
            self.assertIn("grid-template-rows: auto auto minmax(260px, auto) auto minmax(240px, 1fr)", code_editor_css)
            self.assertIn("min-height: 260px", code_editor_css)
            self.assertIn("max-height: none", code_editor_css)
            self.assertIn("overflow: visible", code_editor_css)
            self.assertIn("min-height: inherit", code_editor_css)
            self.assertIn("-webkit-line-clamp: 2", code_editor_css)
            self.assertIn("function compactAiderRepoPath(path)", context_js)
            self.assertIn("aiderSessionMeta.title", context_js)


if __name__ == "__main__":
    unittest.main()
