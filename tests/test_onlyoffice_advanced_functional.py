from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

from main_computer.config import MainComputerConfig
from main_computer.harness import HarnessComputer
from main_computer.viewport import ViewportServer


ONLYOFFICE_DOCS_API_STUB = r"""
(() => {
  const records = window.__onlyofficeAdvancedFunctional = window.__onlyofficeAdvancedFunctional || {
    docEditorConstructed: 0,
    destroyed: 0,
    connectorCreated: 0,
    comments: [],
    commandResults: [],
    commandErrors: [],
    hostId: "",
    config: null
  };

  window.DocsAPI = {
    DocEditor: function(hostId, config) {
      records.docEditorConstructed += 1;
      records.hostId = hostId;
      records.config = config;
      this.hostId = hostId;
      this.config = config;
      this.destroyEditor = function() {
        records.destroyed += 1;
      };
      this.createConnector = function() {
        records.connectorCreated += 1;
        return {
          callCommand(command, callback) {
            const selection = {
              GetAddress() { return "B2:C3"; },
              AddComment(text, author) {
                records.comments.push({text: String(text || ""), author: String(author || "")});
              }
            };
            const previousApi = window.Api;
            window.Api = {
              GetSelection() { return selection; },
              GetActiveSheet() {
                return {
                  GetSelection() { return selection; },
                  GetActiveCell() { return selection; }
                };
              }
            };
            let result = "";
            try {
              result = command();
              records.commandResults.push(String(result || ""));
            } catch (error) {
              records.commandErrors.push(String(error && error.message ? error.message : error));
              throw error;
            } finally {
              window.Api = previousApi;
            }
            if (typeof callback === "function") callback(result);
          }
        };
      };
      return this;
    }
  };
})();
"""


class OnlyOfficeAdvancedFunctionalTests(unittest.TestCase):
    """Browser-backed checks for ONLYOFFICE Advanced tools.

    These tests do not require a live ONLYOFFICE Docs service. They stub the DocsAPI
    browser script and Automation connector so the Advanced pane can be tested against
    the real application DOM, event handlers, scrolling behavior, and attach command.
    """

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name)
        self.server = ViewportServer(
            ("127.0.0.1", 0),
            MainComputerConfig(
                workspace=self.workspace,
                onlyoffice_storage_root=Path("runtime/onlyoffice-functional/workbooks"),
                onlyoffice_public_url="http://127.0.0.1:18084",
                onlyoffice_internal_url="http://127.0.0.1:18084",
            ),
            verbose=False,
        )
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

    def test_advanced_chat_scroll_is_isolated_and_code_addon_attaches_to_selected_cells(self) -> None:
        try:
            from playwright.sync_api import expect, sync_playwright
        except Exception as exc:  # pragma: no cover - exercised only without harness extra
            raise unittest.SkipTest("Playwright is required for ONLYOFFICE Advanced functional tests.") from exc

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

            def fulfill_external_asset(route: Any) -> None:
                url = route.request.url
                if url.endswith(".css"):
                    route.fulfill(status=200, content_type="text/css", body="")
                else:
                    route.fulfill(
                        status=200,
                        content_type="application/javascript",
                        body="window.grapesjs = window.grapesjs || {init: () => ({destroy() {}})};",
                    )

            page.route("https://cdn.jsdelivr.net/**", fulfill_external_asset)
            page.route("https://unpkg.com/**", fulfill_external_asset)
            page.route(
                "**/web-apps/apps/api/documents/api.js",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/javascript",
                    body=ONLYOFFICE_DOCS_API_STUB,
                ),
            )

            try:
                page.goto(f"{self.base_url}/applications/onlyoffice", wait_until="domcontentloaded")
                page.evaluate("() => localStorage.clear()")
                page.reload(wait_until="domcontentloaded")
                page.wait_for_function(
                    """() => document.body.dataset.activeApp === "onlyoffice"
                        && typeof initOnlyOfficeApp === "function"
                        && document.querySelector("#stage-advanced-toggle")"""
                )

                page.once("dialog", lambda dialog: dialog.accept("FunctionalAdvanced.xlsx"))
                page.locator("#onlyoffice-new-workbook").click()
                page.wait_for_function(
                    "() => window.__onlyofficeAdvancedFunctional?.docEditorConstructed === 1"
                )
                expect(page.locator("#onlyoffice-current-path")).to_contain_text("FunctionalAdvanced.xlsx")

                advanced_toggle = page.locator("#stage-advanced-toggle")
                advanced_pane = page.locator("#stage-advanced-pane")
                code_panel = page.locator("#onlyoffice-advanced-code-panel")
                chat_panel = page.locator("#onlyoffice-advanced-chat-panel")
                code_source = page.locator("#onlyoffice-advanced-code-source")
                code_status = page.locator("#onlyoffice-advanced-code-status")

                expect(advanced_toggle).to_be_visible()
                advanced_toggle.click()
                expect(advanced_pane).to_be_visible()
                expect(code_panel).to_be_visible()
                expect(chat_panel).to_be_visible()
                expect(page.locator("#onlyoffice-advanced-chat-notebook")).to_be_visible()

                layout = page.evaluate(
                    """() => {
                        const code = document.querySelector("#onlyoffice-advanced-code-panel").getBoundingClientRect();
                        const chat = document.querySelector("#onlyoffice-advanced-chat-panel").getBoundingClientRect();
                        const pane = document.querySelector("#stage-advanced-pane").getBoundingClientRect();
                        return {
                            codeTop: code.top,
                            codeBottom: code.bottom,
                            chatTop: chat.top,
                            paneTop: pane.top,
                            paneBottom: pane.bottom
                        };
                    }"""
                )
                self.assertLess(layout["paneTop"], layout["codeTop"])
                self.assertLess(layout["codeBottom"], layout["chatTop"])

                scroll_result = page.evaluate(
                    """() => {
                        const pane = document.querySelector("#stage-advanced-pane");
                        const code = document.querySelector("#onlyoffice-advanced-code-panel");
                        const chat = document.querySelector("#onlyoffice-advanced-chat-panel");
                        chat.insertAdjacentHTML(
                            "beforeend",
                            "<div id='onlyoffice-functional-scroll-filler' style='height: 2400px; padding-top: 12px;'>scroll filler</div>"
                        );
                        chat.scrollTop = 0;
                        pane.scrollTop = 0;
                        const before = {
                            codeTop: code.getBoundingClientRect().top,
                            codeBottom: code.getBoundingClientRect().bottom,
                            chatScrollTop: chat.scrollTop,
                            chatScrollHeight: chat.scrollHeight,
                            chatClientHeight: chat.clientHeight,
                            paneScrollTop: pane.scrollTop
                        };
                        chat.scrollTop = chat.scrollHeight;
                        const after = {
                            codeTop: code.getBoundingClientRect().top,
                            codeBottom: code.getBoundingClientRect().bottom,
                            chatScrollTop: chat.scrollTop,
                            chatScrollHeight: chat.scrollHeight,
                            chatClientHeight: chat.clientHeight,
                            paneScrollTop: pane.scrollTop,
                            codeVisible: code.getBoundingClientRect().bottom > 0
                        };
                        return {before, after};
                    }"""
                )
                self.assertGreater(
                    scroll_result["before"]["chatScrollHeight"],
                    scroll_result["before"]["chatClientHeight"] + 100,
                )
                self.assertGreater(scroll_result["after"]["chatScrollTop"], 100)
                self.assertEqual(scroll_result["after"]["paneScrollTop"], 0)
                self.assertAlmostEqual(
                    scroll_result["before"]["codeTop"],
                    scroll_result["after"]["codeTop"],
                    delta=1.5,
                )
                self.assertAlmostEqual(
                    scroll_result["before"]["codeBottom"],
                    scroll_result["after"]["codeBottom"],
                    delta=1.5,
                )
                self.assertTrue(scroll_result["after"]["codeVisible"])

                js_radio = page.locator('input[name="onlyoffice-advanced-code-type"][value="javascript"]')
                python_radio = page.locator('input[name="onlyoffice-advanced-code-type"][value="python"]')
                basic_radio = page.locator('input[name="onlyoffice-advanced-code-type"][value="basic"]')
                expect(js_radio).to_be_checked()
                expect(python_radio).not_to_be_checked()
                expect(basic_radio).not_to_be_checked()

                python_radio.check()
                expect(python_radio).to_be_checked()
                expect(code_status).to_contain_text("Python code area selected")

                code_source.fill('print("attached to selected cells")')
                chat_cells_before = page.evaluate(
                    """() => document.querySelectorAll("#onlyoffice-advanced-chat-panel .chat-cell").length"""
                )
                chat_textarea_values_before = page.evaluate(
                    """() => [...document.querySelectorAll("#onlyoffice-advanced-chat-panel textarea")]
                        .map((textarea) => textarea.value)"""
                )
                self.assertNotIn("attached to selected cells", "\n".join(chat_textarea_values_before))

                page.locator("#onlyoffice-advanced-attach-code").click()
                page.wait_for_function(
                    "() => window.__onlyofficeAdvancedFunctional?.comments?.length === 1"
                )
                expect(code_status).to_contain_text("Python add-on attached to B2:C3")

                attach_record = page.evaluate(
                    """() => {
                        const records = window.__onlyofficeAdvancedFunctional;
                        return {
                            connectorCreated: records.connectorCreated,
                            comments: records.comments,
                            commandResults: records.commandResults,
                            commandErrors: records.commandErrors,
                            chatCells: document.querySelectorAll("#onlyoffice-advanced-chat-panel .chat-cell").length,
                            chatTextareas: [...document.querySelectorAll("#onlyoffice-advanced-chat-panel textarea")]
                                .map((textarea) => textarea.value)
                        };
                    }"""
                )
                self.assertEqual(attach_record["connectorCreated"], 1)
                self.assertEqual(attach_record["commandErrors"], [])
                self.assertEqual(attach_record["chatCells"], chat_cells_before)
                self.assertNotIn("attached to selected cells", "\n".join(attach_record["chatTextareas"]))

                comment = attach_record["comments"][0]
                self.assertEqual(comment["author"], "Main Computer")
                self.assertIn("Main Computer ONLYOFFICE code add-on", comment["text"])
                self.assertIn("Language: Python", comment["text"])
                self.assertIn("Workbook: FunctionalAdvanced.xlsx", comment["text"])
                self.assertIn('print("attached to selected cells")', comment["text"])
                self.assertEqual(attach_record["commandResults"], ["Python add-on attached to B2:C3."])

                page.locator("#active-app-title").click()
                expect(advanced_pane).to_be_hidden()
                expect(advanced_toggle).to_have_attribute("aria-expanded", "false")
            finally:
                browser.close()

        self.assertEqual(page_errors, [])
        actionable_console_errors = [
            error for error in console_errors
            if "Could not load ONLYOFFICE API" not in error
        ]
        self.assertEqual(actionable_console_errors, [])


if __name__ == "__main__":
    unittest.main()
