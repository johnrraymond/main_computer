from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

from main_computer.config import MainComputerConfig
from main_computer.harness import HarnessComputer
from main_computer.viewport import ViewportServer


class SpreadsheetChatDeepIntegrationTests(unittest.TestCase):
    """Browser-backed checks for the embedded Chat Console + Spreadsheet contract.

    This intentionally lives outside the broad WidgetHarness test so a developer can
    run the chat/spreadsheet integration hard path directly while iterating on either
    feature.
    """

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name)
        self.server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=self.workspace), verbose=False)
        self.server.debug_root = self.workspace.resolve()
        self.server.computer = HarnessComputer()  # type: ignore[assignment]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tempdir.cleanup()

    def test_embedded_chat_import_roundtrip_persists_and_runs_in_spreadsheet(self) -> None:
        try:
            from playwright.sync_api import expect, sync_playwright
        except Exception as exc:  # pragma: no cover - exercised only without harness extra
            raise unittest.SkipTest("Playwright is required for spreadsheet/chat functional tests.") from exc

        console_errors: list[str] = []
        page_errors: list[str] = []

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True)
            except Exception as exc:
                if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
                    raise unittest.SkipTest("Playwright Chromium is required; run `playwright install chromium`.") from exc
                raise
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on(
                "console",
                lambda message: console_errors.append(message.text) if message.type == "error" else None,
            )
            page.on("pageerror", lambda error: page_errors.append(str(error)))

            try:
                page.goto(f"{self.base_url}/applications/spreadsheet", wait_until="domcontentloaded")
                page.evaluate("() => localStorage.clear()")
                page.reload(wait_until="domcontentloaded")
                page.wait_for_function(
                    """() => typeof spreadsheetWorkbook !== "undefined"
                        && spreadsheetWorkbook
                        && typeof setSpreadsheetSelection === "function"
                        && window.MainComputerChatThreads?.exportState"""
                )

                expect(page.locator("#spreadsheet-app")).to_be_visible()
                expect(page.locator("#spreadsheet-chat-thread-panel")).to_be_visible()
                expect(page.locator("#spreadsheet-embedded-chat-notebook")).to_be_visible()
                expect(page.locator("#spreadsheet-chat-thread-active-title")).to_be_visible()
                expect(page.locator("#spreadsheet-selection-status")).to_contain_text("selection: none")

                initial_link = page.evaluate(
                    """() => {
                        const store = window.MainComputerChatThreads.exportState();
                        const active = store.threads[store.active_thread_id] || null;
                        const chat = spreadsheetWorkbook.metadata?.chat || {};
                        return {
                            activeThreadId: active?.id || "",
                            activeTitle: active?.title || "",
                            workbookThreadId: chat.active_thread_id || "",
                            linkedBy: chat.linked_by || "",
                            linkedWorkbooks: active?.metadata?.linked_workbooks || [],
                            path: spreadsheetPath,
                            urlThreadId: new URL(window.location.href).searchParams.get("thread") || "",
                        };
                    }"""
                )
                self.assertTrue(initial_link["activeThreadId"])
                self.assertEqual(initial_link["workbookThreadId"], initial_link["activeThreadId"])
                self.assertEqual(initial_link["urlThreadId"], initial_link["activeThreadId"])
                self.assertEqual(initial_link["linkedBy"], "spreadsheet")
                self.assertIn(initial_link["path"], initial_link["linkedWorkbooks"])

                page.once("dialog", lambda dialog: dialog.accept("Spreadsheet Import Deep Dive"))
                page.locator("#spreadsheet-chat-thread-rename").click()
                expect(page.locator("#spreadsheet-chat-thread-active-title")).to_contain_text(
                    "Spreadsheet Import Deep Dive"
                )

                ai_cell = page.locator("#spreadsheet-embedded-chat-notebook .chat-cell-ai").first
                ai_cell.locator("textarea").fill("spreadsheet import snippet")
                ai_cell.get_by_role("button", name="Run").click()
                expect(page.locator("#spreadsheet-embedded-chat-notebook")).to_contain_text("return 42;")
                expect(page.get_by_role("button", name="Import to selected cell")).to_be_visible()

                page.get_by_role("button", name="Clear Selection").click()
                page.get_by_role("button", name="Import to selected cell").last.click()
                expect(page.locator("#spreadsheet-chat-thread-status")).to_contain_text(
                    "select a target spreadsheet cell before importing chat code"
                )
                expect(page.locator("#spreadsheet-selected-cell")).to_contain_text("No cell selected")

                page.evaluate("() => setSpreadsheetSelection('B2')")
                expect(page.locator("#spreadsheet-selection-status")).to_contain_text("selection: B2")
                page.get_by_role("button", name="Import to selected cell").last.click()
                expect(page.locator("#spreadsheet-selected-cell")).to_contain_text("B2")
                expect(page.locator("#spreadsheet-cell-type")).to_have_value("javascript")
                expect(page.locator("#spreadsheet-cell-source")).to_have_value("return 42;")
                expect(page.locator("#spreadsheet-import-history")).to_contain_text("Import History")
                expect(page.locator("#spreadsheet-import-history")).to_contain_text("Language: javascript")
                expect(page.locator("#spreadsheet-import-history")).to_contain_text("Original target: B2")
                expect(page.locator("#spreadsheet-save-state")).to_contain_text("dirty - disk save needed")

                imported_state = page.evaluate("() => window.__spreadsheetChatDeepState = JSON.parse(JSON.stringify({workbook: spreadsheetWorkbook, threads: window.MainComputerChatThreads.exportState()}))")
                self.assertIn("workbook", imported_state)
                imported_cell = imported_state["workbook"]["sheets"]["Sheet1"]["cells"]["B2"]
                imported_history = imported_cell["metadata"]["chat_import_history"]
                active_thread_id = imported_state["threads"]["active_thread_id"]
                active_thread = imported_state["threads"]["threads"][active_thread_id]
                self.assertEqual(imported_cell["kind"], "javascript")
                self.assertEqual(imported_cell["source"], "return 42;")
                self.assertEqual(imported_history[-1]["origin_thread_id"], active_thread_id)
                self.assertEqual(imported_history[-1]["origin_thread_title"], "Spreadsheet Import Deep Dive")
                self.assertEqual(imported_history[-1]["original_code"], "return 42;")
                self.assertEqual(imported_history[-1]["original_target"], "B2")
                self.assertTrue(any(cell["type"] == "ai" and cell["source"] == "spreadsheet import snippet" for cell in active_thread["cells"]))
                self.assertTrue(any(cell["type"] == "output" for cell in active_thread["cells"]))

                page.locator("#spreadsheet-cell-source").fill("return 43;")
                page.evaluate("() => setSpreadsheetSelection('B2')")
                expect(page.locator("#spreadsheet-import-history")).to_contain_text("modified since import")
                page.get_by_role("button", name="Restore Original").click()
                expect(page.locator("#spreadsheet-cell-source")).to_have_value("return 42;")
                expect(page.locator("#spreadsheet-import-history")).to_contain_text("Import History")

                page.get_by_role("button", name="Run Cell").click()
                expect(page.locator("#spreadsheet-code-status")).to_contain_text("status: clean")
                expect(page.locator("#spreadsheet-cell-output")).to_contain_text("Result")
                expect(page.locator("#spreadsheet-cell-output")).to_contain_text("42")

                page.get_by_role("button", name="Save").click()
                expect(page.locator("#spreadsheet-save-state")).to_contain_text("clean")
                page.reload(wait_until="domcontentloaded")
                page.wait_for_function(
                    """() => typeof spreadsheetWorkbook !== "undefined"
                        && spreadsheetWorkbook
                        && window.MainComputerChatThreads?.exportState"""
                )
                page.evaluate("() => setSpreadsheetSelection('B2')")
                expect(page.locator("#spreadsheet-cell-type")).to_have_value("javascript")
                expect(page.locator("#spreadsheet-cell-source")).to_have_value("return 42;")
                expect(page.locator("#spreadsheet-cell-output")).to_contain_text("42")
                expect(page.locator("#spreadsheet-import-history")).to_contain_text("Spreadsheet Import Deep Dive")

                reloaded_state = page.evaluate(
                    """() => {
                        const store = window.MainComputerChatThreads.exportState();
                        const active = store.threads[store.active_thread_id] || null;
                        const cell = spreadsheetWorkbook.sheets.Sheet1.cells.B2 || {};
                        return {
                            path: spreadsheetPath,
                            activeThreadId: active?.id || "",
                            title: active?.title || "",
                            workbookThreadId: spreadsheetWorkbook.metadata?.chat?.active_thread_id || "",
                            cellKind: cell.kind || "",
                            cellLanguage: cell.language || "",
                            cellSource: cell.source || "",
                            historyCount: cell.metadata?.chat_import_history?.length || 0,
                            lastHistory: (cell.metadata?.chat_import_history || []).slice(-1)[0] || {},
                            threadCells: active?.cells?.map((cell) => ({type: cell.type, source: cell.source || "", status: cell.status || ""})) || [],
                        };
                    }"""
                )
                self.assertEqual(reloaded_state["path"], "untitled.json")
                self.assertEqual(reloaded_state["activeThreadId"], reloaded_state["workbookThreadId"])
                self.assertEqual(reloaded_state["title"], "Spreadsheet Import Deep Dive")
                self.assertEqual(reloaded_state["cellKind"], "javascript")
                self.assertEqual(reloaded_state["cellLanguage"], "javascript")
                self.assertEqual(reloaded_state["cellSource"], "return 42;")
                self.assertGreaterEqual(reloaded_state["historyCount"], 1)
                self.assertEqual(reloaded_state["lastHistory"]["origin_thread_id"], reloaded_state["activeThreadId"])
                self.assertTrue(any(cell["type"] == "ai" and "spreadsheet import snippet" in cell["source"] for cell in reloaded_state["threadCells"]))

                screenshot_path = self.workspace / "spreadsheet_chat_deep_integration.png"
                page.screenshot(path=str(screenshot_path), full_page=False)
                self.assertTrue(screenshot_path.exists())
            finally:
                browser.close()

        disk_workbook = self.workspace / "spreadsheets" / "untitled.json"
        self.assertTrue(disk_workbook.is_file())
        disk_data: dict[str, Any] = json.loads(disk_workbook.read_text(encoding="utf-8"))
        disk_cell = disk_data["sheets"]["Sheet1"]["cells"]["B2"]
        self.assertEqual(disk_cell["kind"], "javascript")
        self.assertEqual(disk_cell["language"], "javascript")
        self.assertEqual(disk_cell["source"], "return 42;")
        self.assertGreaterEqual(len(disk_cell["metadata"]["chat_import_history"]), 1)
        self.assertEqual(disk_data["metadata"]["chat"]["linked_by"], "spreadsheet")

        self.assertEqual(page_errors, [])
        self.assertEqual(console_errors, [])
