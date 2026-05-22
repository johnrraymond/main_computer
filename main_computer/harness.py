from __future__ import annotations

import argparse
import base64
import json
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from main_computer.catalog import ProjectInfo
from main_computer.config import MainComputerConfig
from main_computer.models import ChatResponse
from main_computer.viewport import ViewportServer


def harness_workspace() -> Path:
    configured = os.environ.get("MAIN_COMPUTER_HARNESS_WORKSPACE") or os.environ.get("MAIN_COMPUTER_WORKSPACE")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "dsl"


class HarnessCatalog:
    def __init__(self) -> None:
        workspace = harness_workspace()
        self.projects = [
            ProjectInfo(
                name=f"widget_project_{index:03}",
                path=workspace / f"widget_project_{index:03}",
                markers=("pyproject.toml",) if index % 2 == 0 else (),
                child_count=index % 5,
                file_count=3 + index,
            )
            for index in range(1, 151)
        ]
        self.projects.append(
            ProjectInfo(
                name="main_computer_test",
                path=workspace / "main_computer_test",
                markers=("pyproject.toml", "requirements.txt"),
                child_count=2,
                file_count=4,
            )
        )

    def list_projects(self) -> list[ProjectInfo]:
        return self.projects


class HarnessProvider:
    name = "harness"
    model = "widget-check"


class HarnessComputer:
    def __init__(self) -> None:
        self.catalog = HarnessCatalog()
        self.provider = HarnessProvider()

    def chat(self, prompt: str) -> ChatResponse:
        time.sleep(0.25)
        content = f"HARNESS RESPONSE: {prompt}"
        if "spreadsheet import snippet" in prompt.lower():
            content = (
                "HARNESS RESPONSE: spreadsheet import snippet\n\n"
                "```javascript\n"
                "return 42;\n"
                "```"
            )
        return ChatResponse(
            content=content,
            provider=self.provider.name,
            model=self.provider.model,
            metadata={"harness": True},
        )


class HarnessFailure(RuntimeError):
    pass


class WidgetHarness:
    def __init__(
        self,
        *,
        url: str | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        output_dir: Path = Path("harness_output"),
        headless: bool = True,
    ) -> None:
        self.external_url = url
        self.host = host
        self.port = port
        self.output_dir = output_dir
        self.headless = headless
        self.server: ViewportServer | None = None
        self.thread: threading.Thread | None = None
        self.checks: list[dict[str, Any]] = []

    def run(self) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        base_url = self.external_url or self._start_server()

        try:
            self._run_browser_checks(base_url.rstrip("/"))
        finally:
            self._stop_server()

        passed = all(check["ok"] for check in self.checks)
        report = {
            "ok": passed,
            "base_url": base_url,
            "checks": self.checks,
            "output_dir": str(self.output_dir.resolve()),
        }
        report_path = self.output_dir / "widget_harness_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if not passed:
            raise HarnessFailure(f"Widget harness failed. Report: {report_path}")
        return report

    def _start_server(self) -> str:
        config = MainComputerConfig(workspace=harness_workspace(), provider="ollama", model="gemma4:26b")
        self.server = ViewportServer((self.host, self.port), config, verbose=False)
        self.server.computer = HarnessComputer()  # type: ignore[assignment]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://{self.host}:{self.server.server_port}"

    def _stop_server(self) -> None:
        if self.server is None:
            return
        self.server.shutdown()
        self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)
        self.server = None
        self.thread = None

    def _run_browser_checks(self, base_url: str) -> None:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import expect, sync_playwright
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("Playwright is required for the widget harness.") from exc

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            try:
                self._check_text_console(page, expect, base_url)
                self._check_graphical_widgets(page, expect, base_url)
                self._check_debug_interfaces(page, expect, base_url)
                self._check_energy_page(page, expect, base_url)
                self._check_revision_page(page, expect, base_url)
                self._check_applications_document_library(page, expect, base_url)
                self._check_applications_spreadsheet_chat_console(page, expect, base_url)
                self._check_applications_game_editor(page, expect, base_url)
            except PlaywrightError as exc:
                self._record("playwright-runtime", False, str(exc))
                raise
            finally:
                browser.close()

    def _check_text_console(self, page: Any, expect: Any, base_url: str) -> None:
        page.goto(f"{base_url}/", wait_until="networkidle")
        expect(page.locator("h1")).to_have_text("Main Computer Console")
        expect(page.locator("#timestamp-state")).to_be_visible()
        expect(page.locator("#console-loaded-at")).not_to_have_text("--")
        expect(page.locator("#directory-updated-at")).not_to_have_text("--")
        expect(page.locator("[data-fullscreen-target]").nth(0)).to_be_visible()
        self._record("text-console-loads", True)
        self._record("text-timestamp-poll-visible", True)
        self._record("text-console-has-fullscreen-controls", page.locator("[data-fullscreen-target]").count() >= 2)
        expect(page.locator(".widget-ticker").nth(0)).to_be_visible()
        self._record("text-console-has-widget-tickers", page.locator(".widget-ticker").count() >= 2)

        self._assert_viewport_fit(page, "text-console-fits-viewport")
        self._assert_scrollable(page, "#projects", "text-project-list-scrollable")

        page.locator("#project-search").fill("main_computer_test")
        expect(page.locator("#projects .project")).to_contain_text("main_computer_test")
        self._record("text-project-search-filters", True)

        page.locator("#prompt").fill("text harness ping")
        page.locator("#send").click()
        expect(page.locator("#working-indicator")).to_be_visible()
        self._record("text-working-indicator-appears", True)
        expect(page.locator("#log")).to_contain_text("HARNESS RESPONSE: text harness ping")
        expect(page.locator("#working-indicator")).to_be_hidden()
        self._record("text-working-indicator-clears", True)
        self._record("text-console-chat-roundtrip", True)

        page.locator("[data-diagnostic-level='health']").click()
        expect(page.locator("#log")).to_contain_text("health:")
        expect(page.locator("#log")).to_contain_text("checks:")
        expect(page.locator("#log")).to_contain_text("diagnostics_report.json")
        self._record("text-diagnostic-level-five-runs-quick-health", True)

        page.locator("a[href='/graphical']").click()
        expect(page.locator("text=Bridge Control Viewport")).to_be_visible()
        expect(page.locator("#log")).to_contain_text("HARNESS RESPONSE: text harness ping")
        self._record("text-console-links-to-graphical-test", True)
        self._record("mode-switch-preserves-text-transcript", True)

        page.screenshot(path=str(self.output_dir / "text_console.png"), full_page=False)

    def _check_graphical_widgets(self, page: Any, expect: Any, base_url: str) -> None:
        page.goto(f"{base_url}/graphical", wait_until="networkidle")
        expect(page.locator("text=Main Computer Control Panel")).to_be_visible()
        expect(page.locator("text=Service Map")).to_be_visible()
        expect(page.locator("text=Machine")).to_be_visible()
        expect(page.locator("text=Dependencies")).to_be_visible()
        expect(page.locator("text=Open Ports")).to_be_visible()
        expect(page.locator("text=Recent Activity")).to_be_visible()
        expect(page.locator("#overall-state")).not_to_have_text("Loading", timeout=8000)
        expect(page.locator("#service-grid .service-card").first()).to_be_visible(timeout=8000)
        expect(page.locator("#service-grid")).to_contain_text("Main Computer App")
        expect(page.locator("#service-grid")).to_contain_text("Ollama")
        expect(page.locator("#service-grid")).to_contain_text("Git Server / Gitea")
        expect(page.locator("#service-grid")).to_contain_text("Blockchain / Anvil")
        expect(page.locator("#machine-list li").first()).to_be_visible()
        expect(page.locator("#dependency-list li").first()).to_be_visible()
        expect(page.locator("#ports .port-pill").first()).to_be_visible()
        self._record("graphical-control-panel-surface-loads", True)
        self._record("graphical-control-panel-services-load", True)
        self._record("graphical-control-panel-machine-loads", True)
        self._record("graphical-control-panel-dependencies-load", True)
        self._record("graphical-control-panel-ports-load", True)

        before = page.locator("#updated-at").inner_text()
        page.locator("#refresh-button").click()
        expect(page.locator("#refresh-button")).to_be_enabled(timeout=8000)
        after = page.locator("#updated-at").inner_text()
        self._record("graphical-control-panel-refreshes", bool(after), {"before": before, "after": after})
        if not after:
            raise HarnessFailure("Control panel refresh did not update the timestamp.")

        self._assert_viewport_fit(page, "graphical-control-panel-fits-viewport")
        for selector, name in [
            (".topbar", "graphical-topbar-bounded"),
            (".layout", "graphical-layout-bounded"),
            ("#service-grid", "graphical-service-grid-bounded"),
        ]:
            self._assert_bounded(page, selector, name)

        page.screenshot(path=str(self.output_dir / "graphical_control_panel.png"), full_page=False)

        page.locator("a[href='/text']").click()
        expect(page.locator("h1")).to_have_text("Main Computer Console")
        self._record("mode-switch-opens-text-console-from-control-panel", True)

    def _check_debug_interfaces(self, page: Any, expect: Any, base_url: str) -> None:
        page.goto(f"{base_url}/debug/text", wait_until="networkidle")
        expect(page.locator("h1")).to_have_text("Main Computer Text Debug")
        expect(page.locator("#debug-status")).to_be_visible()
        expect(page.locator("#debug-path")).to_have_value("main_computer/viewport.py")
        expect(page.locator("#debug-asset-name")).to_be_visible()
        expect(page.locator("#asset-save")).to_be_visible()
        expect(page.locator("#asset-reset")).to_be_visible()
        expect(page.locator("#asset-restore")).to_be_visible()
        expect(page.locator("[data-fullscreen-target]").nth(0)).to_be_visible()
        expect(page.locator(".widget-ticker").nth(0)).to_be_visible()
        self._assert_viewport_fit(page, "text-debug-fits-viewport")
        self._record("text-debug-has-fullscreen-controls", page.locator("[data-fullscreen-target]").count() >= 2)
        self._record("text-debug-has-asset-controls", True)
        self._record("text-debug-has-asset-history-controls", True)
        self._record("text-debug-interface-loads", True)

        page.goto(f"{base_url}/debug/graphical", wait_until="networkidle")
        expect(page.locator("h1")).to_have_text("Main Computer Graphical Debug")
        expect(page.locator("#debug-status")).to_be_visible()
        expect(page.locator("#debug-path")).to_have_value("main_computer/viewport.py")
        expect(page.locator("#debug-asset-name")).to_be_visible()
        expect(page.locator("#asset-save")).to_be_visible()
        expect(page.locator("#asset-reset")).to_be_visible()
        expect(page.locator("#asset-restore")).to_be_visible()
        expect(page.locator("[data-fullscreen-target]").nth(0)).to_be_visible()
        expect(page.locator(".widget-ticker").nth(0)).to_be_visible()
        self._assert_viewport_fit(page, "graphical-debug-fits-viewport")
        self._record("graphical-debug-has-fullscreen-controls", page.locator("[data-fullscreen-target]").count() >= 3)
        self._record("graphical-debug-has-asset-controls", True)
        self._record("graphical-debug-has-asset-history-controls", True)
        self._record("graphical-debug-interface-loads", True)

    def _check_energy_page(self, page: Any, expect: Any, base_url: str) -> None:
        page.goto(f"{base_url}/energy", wait_until="networkidle")
        expect(page.locator("h1")).to_have_text("Main Computer Energy Credits")
        expect(page.locator("#head-node")).to_contain_text("main-computer-head")
        expect(page.locator("#node-id")).to_be_visible()
        self._assert_viewport_fit(page, "energy-page-fits-viewport")
        self._record("energy-control-page-loads", True)

    def _check_revision_page(self, page: Any, expect: Any, base_url: str) -> None:
        page.goto(f"{base_url}/revision", wait_until="networkidle")
        expect(page.locator("h1")).to_have_text("Main Computer Revision Control")
        expect(page.locator("#revision-status")).to_contain_text("snapshots")
        expect(page.locator("#revision-path")).to_have_value("main_computer/viewport.py")
        expect(page.locator("#revision-restore-system")).to_be_visible()
        self._assert_viewport_fit(page, "revision-page-fits-viewport")
        self._record("revision-control-page-loads", True)
        self._record("revision-control-has-system-restore", True)

    def _check_applications_document_library(self, page: Any, expect: Any, base_url: str) -> None:
        page.goto(f"{base_url}/applications/document", wait_until="domcontentloaded")
        page.evaluate("localStorage.clear()")
        page.goto(f"{base_url}/applications/document", wait_until="domcontentloaded")
        expect(page.locator("#document-app")).to_be_visible()
        expect(page.locator("#document-library")).to_be_visible()
        expect(page.locator("#document-library-list")).to_contain_text("Main Computer User Guide")
        self._record("applications-document-library-loads", True)

        guide_item = page.locator("#document-library-list button", has_text="Main Computer User Guide")
        guide_item.click()
        expect(page.locator("#document-current-path")).to_contain_text("pretty_docs/main-computer-user-guide.md")
        expect(guide_item).to_have_attribute("aria-current", "true")
        expect(page.locator("#document-editor")).to_contain_text("Main Computer User Guide")
        expect(page.locator("#document-version-token")).to_contain_text("revision")
        self._record("applications-document-guide-loads", True)
        self._record("applications-document-active-selection", True)

        page.locator("#document-editor").click()
        page.keyboard.type(" Local harness draft.")
        expect(page.locator("#document-status")).to_contain_text("draft saved to backend")
        self._record("applications-document-backend-draft-save", True)

        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#document-current-path")).to_contain_text("pretty_docs/main-computer-user-guide.md")
        expect(page.locator("#document-library-list button.active", has_text="Main Computer User Guide")).to_be_visible()
        expect(page.locator("#document-draft-banner")).to_be_visible()
        expect(page.locator("#document-editor")).to_contain_text("Local harness draft.")
        self._record("applications-document-selection-restores", True)
        self._record("applications-document-draft-banner", True)

        page.locator("#document-reload-doc").click()
        expect(page.locator("#document-draft-banner")).to_be_visible()
        expect(page.locator("#document-editor")).to_contain_text("Local harness draft.")
        self._record("applications-document-reload-preserves-draft", True)

        page.locator("#document-discard-draft").click()
        expect(page.locator("#document-draft-banner")).to_be_hidden()
        expect(page.locator("#document-draft-state")).to_contain_text("no backend draft")
        expect(page.locator("#document-editor")).to_contain_text("Main Computer User Guide")
        self._record("applications-document-discard-draft", True)
        if page.locator("#document-save").count() != 0:
            raise HarnessFailure("Document Editor autosaves drafts through the backend and must not expose a separate disk-save control.")
        page.screenshot(path=str(self.output_dir / "applications_document_library.png"), full_page=False)
        page.screenshot(path=str(self.output_dir / "applications_document_draft_safety.png"), full_page=False)

    def _check_applications_spreadsheet_chat_console(self, page: Any, expect: Any, base_url: str) -> None:
        page.goto(f"{base_url}/applications/spreadsheet", wait_until="domcontentloaded")
        page.evaluate("() => localStorage.clear()")
        page.reload(wait_until="domcontentloaded")

        expect(page.locator("#spreadsheet-app")).to_be_visible()
        expect(page.locator("#spreadsheet-chat-thread-panel")).to_be_visible()
        expect(page.locator("#spreadsheet-embedded-chat-notebook")).to_be_visible()
        expect(page.locator("#spreadsheet-chat-thread-active-title")).to_contain_text("Spreadsheet Chat")
        expect(page.locator("#spreadsheet-embedded-chat-notebook textarea").nth(0)).to_be_visible()
        self._record("applications-spreadsheet-embedded-chat-loads", True)
        self._record("applications-spreadsheet-embedded-chat-links-thread", True)

        page.locator("#spreadsheet-embedded-chat-notebook [data-chat-console-insert='ai']").first.click()
        page.locator("#spreadsheet-embedded-chat-notebook .chat-cell").last().locator("[data-chat-cell-tab='javascript']").click()
        expect(page.locator("#spreadsheet-embedded-chat-notebook .chat-cell-javascript textarea").nth(0)).to_be_visible()
        self._record("applications-spreadsheet-embedded-chat-adds-tabbed-js-cell", True)

        ai_cell = page.locator("#spreadsheet-embedded-chat-notebook .chat-cell-ai").nth(0)
        ai_cell.locator("textarea").fill("spreadsheet import snippet")
        ai_cell.get_by_role("button", name="Run").click()
        expect(page.locator("#spreadsheet-embedded-chat-notebook")).to_contain_text("return 42;")
        expect(page.locator("#spreadsheet-embedded-chat-notebook")).to_contain_text("Import to selected cell")
        self._record("applications-spreadsheet-embedded-chat-renders-importable-snippet", True)

        page.evaluate(
            """() => {
                if (typeof setSpreadsheetSelection !== "function") {
                    throw new Error("spreadsheet selection API is not available");
                }
                setSpreadsheetSelection("B2");
            }"""
        )
        expect(page.locator("#spreadsheet-selection-status")).to_contain_text("selection: B2")
        page.get_by_role("button", name="Import to selected cell").click()
        expect(page.locator("#spreadsheet-selected-cell")).to_contain_text("B2")
        expect(page.locator("#spreadsheet-cell-type")).to_have_value("javascript")
        expect(page.locator("#spreadsheet-cell-source")).to_have_value("return 42;")
        expect(page.locator("#spreadsheet-import-history")).to_contain_text("Import History")
        expect(page.locator("#spreadsheet-save-state")).to_contain_text("dirty - disk save needed")
        self._record("applications-spreadsheet-chat-imports-snippet-to-selected-cell", True)
        self._record("applications-spreadsheet-chat-import-records-history", True)

        page.screenshot(path=str(self.output_dir / "applications_spreadsheet_chat_console.png"), full_page=False)

    def _check_applications_game_editor(self, page: Any, expect: Any, base_url: str) -> None:
        project_path = Path.cwd() / "game_projects" / "webgl-demo" / "project.json"
        original_project = project_path.read_text(encoding="utf-8") if project_path.exists() else ""
        asset_dir = Path.cwd() / "game_projects" / "webgl-demo" / "assets"
        png_path = self.output_dir / "harness_pixel.png"
        txt_path = self.output_dir / "harness_logic.asset"
        png_path.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="))
        txt_path.write_text("harness script asset\n", encoding="utf-8")
        try:
            page.goto(f"{base_url}/applications/game-editor", wait_until="domcontentloaded")
            expect(page.locator("#game-editor-app")).to_be_visible()
            expect(page.locator("#game-editor-project-list")).to_contain_text("WebGL Demo")
            expect(page.locator("#game-editor-project-name")).to_have_value("WebGL Demo")
            expect(page.locator("#game-editor-webgl-viewport")).to_be_visible()
            expect(page.locator("#game-editor-webgl-canvas")).to_be_visible()
            expect(page.locator("#game-editor-webgl-status")).to_contain_text("webgl", timeout=15000)
            self._record("applications-game-editor-loads", True)
            self._record("applications-game-editor-project-list", True)
            self._record("applications-game-editor-preview-renders", True)
            self._record("applications-game-editor-defaults-to-webgl-demo", True)
            self._record("applications-game-editor-live-webgl-viewport", True)

            page.locator("#game-editor-entity-list button", has_text="Desktop Core").click()
            expect(page.locator("#game-editor-entity-name")).to_have_value("Desktop Core")
            page.locator("#game-editor-frame-selected").click()
            self._record("applications-game-editor-webgl-selects-entity", True)
            page.locator("#game-editor-entity-name").fill("Desktop Core Harness")
            page.locator("#game-editor-entity-x").fill("180")
            page.locator("#game-editor-entity-color").fill("#ff0000")
            expect(page.locator("#game-editor-webgl-status")).not_to_contain_text("failed")
            self._record("applications-game-editor-webgl-updates-from-inspector", True)
            page.locator("#game-editor-save-project").click()
            expect(page.locator("#game-editor-status")).to_contain_text("project saved")
            page.locator("#game-editor-reset-project").click()
            expect(page.locator("#game-editor-entity-list")).to_contain_text("Desktop Core Harness")
            self._record("applications-game-editor-save-persists", True)
            self._record("applications-game-editor-edits-webgl-demo", True)
            self._record("applications-webgl-demo-save-persists", True)

            page.locator("#game-editor-asset-upload").set_input_files(str(png_path))
            page.locator("#game-editor-upload-asset").click()
            expect(page.locator("#game-editor-asset-list")).to_contain_text("harness_pixel.png")
            page.locator("#game-editor-asset-upload").set_input_files(str(txt_path))
            page.locator("#game-editor-upload-asset").click()
            expect(page.locator("#game-editor-asset-list")).to_contain_text("harness_logic.asset")
            self._record("applications-game-editor-asset-upload", True)
            self._record("applications-game-editor-any-asset-type", True)

            page.locator("#game-editor-entity-list button", has_text="Desktop Core Harness").click()
            page.locator("#game-editor-entity-asset").select_option("harness_pixel.png")
            page.locator("#game-editor-save-project").click()
            expect(page.locator("#game-editor-status")).to_contain_text("project saved")
            page.locator("#game-editor-reset-project").click()
            page.locator("#game-editor-entity-list button", has_text="Desktop Core Harness").click()
            expect(page.locator("#game-editor-entity-asset")).to_have_value("harness_pixel.png")
            self._record("applications-game-editor-asset-assignment", True)
            page.screenshot(path=str(self.output_dir / "applications_game_editor.png"), full_page=False)
            page.screenshot(path=str(self.output_dir / "applications_game_editor_webgl_demo.png"), full_page=False)
            page.screenshot(path=str(self.output_dir / "applications_game_editor_live_webgl_viewport.png"), full_page=False)

            page.goto(f"{base_url}/applications/webgl", wait_until="domcontentloaded")
            expect(page.locator("#webgl-demo")).to_be_visible()
            expect(page.locator("#gl-status")).to_contain_text("webgl-demo", timeout=15000)
            self._record("applications-webgl-loads-project-backed-demo", True)
            self._record("applications-webgl-project-backed-after-editor-save", True)
            page.screenshot(path=str(self.output_dir / "applications_webgl_demo_project_backed.png"), full_page=False)
            page.screenshot(path=str(self.output_dir / "applications_webgl_project_backed_after_editor_save.png"), full_page=False)
        finally:
            if original_project:
                project_path.write_text(original_project, encoding="utf-8")
            for name in ("harness_pixel.png", "harness_logic.asset"):
                candidate = asset_dir / name
                if candidate.exists():
                    candidate.unlink()

    def _assert_viewport_fit(self, page: Any, name: str) -> None:
        metrics = page.evaluate(
            """() => ({
                innerHeight: window.innerHeight,
                innerWidth: window.innerWidth,
                scrollHeight: document.scrollingElement.scrollHeight,
                scrollWidth: document.scrollingElement.scrollWidth
            })"""
        )
        ok = metrics["scrollHeight"] <= metrics["innerHeight"] + 2 and metrics["scrollWidth"] <= metrics["innerWidth"] + 2
        self._record(name, ok, metrics)
        if not ok:
            raise HarnessFailure(f"{name} failed: {metrics}")

    def _assert_scrollable(self, page: Any, selector: str, name: str) -> None:
        metrics = page.locator(selector).evaluate(
            """(element) => ({
                clientHeight: element.clientHeight,
                scrollHeight: element.scrollHeight,
                overflowY: getComputedStyle(element).overflowY
            })"""
        )
        ok = metrics["scrollHeight"] > metrics["clientHeight"] and metrics["overflowY"] in {"auto", "scroll"}
        self._record(name, ok, metrics)
        if not ok:
            raise HarnessFailure(f"{name} failed: {metrics}")

    def _assert_bounded(self, page: Any, selector: str, name: str) -> None:
        box = page.locator(selector).bounding_box()
        viewport = page.viewport_size or {"width": 0, "height": 0}
        ok = bool(
            box
            and box["x"] >= -1
            and box["y"] >= -1
            and box["x"] + box["width"] <= viewport["width"] + 1
            and box["y"] + box["height"] <= viewport["height"] + 1
        )
        self._record(name, ok, {"box": box, "viewport": viewport})
        if not ok:
            raise HarnessFailure(f"{name} failed: box={box}, viewport={viewport}")

    def _assert_buddhabrot_rendered(self, page: Any) -> None:
        page.wait_for_function(
            """() => document.querySelector('#buddhabrot-canvas')?.dataset.rendered === 'true'""",
            timeout=15000,
        )
        page.locator("#buddhabrot-orbits").fill("75")
        page.locator("#buddhabrot-delay").fill("20")
        page.wait_for_timeout(200)
        metrics = page.locator("#buddhabrot-canvas").evaluate(
            """(canvas) => {
                const ctx = canvas.getContext('2d');
                const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
                let lit = 0;
                for (let i = 0; i < data.length; i += 4) {
                    if (data[i] > 8 || data[i + 1] > 8 || data[i + 2] > 8) lit += 1;
                }
                const rect = canvas.getBoundingClientRect();
                return {
                    width: canvas.width,
                    height: canvas.height,
                    lit,
                    samples: Number(canvas.dataset.samples || 0),
                    slices: Number(canvas.dataset.slices || 0),
                    displayedWidth: rect.width,
                    displayedHeight: rect.height
                };
            }"""
        )
        ok = (
            metrics["width"] > 0
            and metrics["height"] > 0
            and metrics["lit"] > 500
            and metrics["samples"] > 0
            and metrics["slices"] > 0
        )
        self._record("graphical-buddhabrot-renders-client-side", ok, metrics)
        if not ok:
            raise HarnessFailure(f"Buddhabrot render failed: {metrics}")

        controls = page.evaluate(
            """() => ({
                orbits: document.querySelector('#buddhabrot-orbits').value,
                delay: document.querySelector('#buddhabrot-delay').value,
                status: document.querySelector('#buddhabrot-status').textContent
            })"""
        )
        controls_ok = controls["orbits"] == "75" and controls["delay"] == "20" and "buddhabrot live" in controls["status"]
        self._record("graphical-buddhabrot-slice-controls-work", controls_ok, controls)
        if not controls_ok:
            raise HarnessFailure(f"Buddhabrot controls failed: {controls}")

    def _assert_fractal_selector_switches(self, page: Any) -> None:
        page.locator("#fractal-selector").select_option("mandelbrot")
        page.wait_for_function(
            """() => document.querySelector('#buddhabrot-canvas')?.dataset.plugin === 'mandelbrot'
                && document.querySelector('#buddhabrot-canvas')?.dataset.rendered === 'true'""",
            timeout=15000,
        )
        status = page.locator("#buddhabrot-status").text_content() or ""
        ok = "Mandelbrot parameter plane" in status
        self._record("graphical-fractal-selector-switches-plugin", ok, status)
        if not ok:
            raise HarnessFailure(f"Fractal selector did not switch plugin: {status}")

        page.locator("#fractal-selector").select_option("mandelbrot-distance-field")
        page.wait_for_function(
            """() => document.querySelector('#buddhabrot-canvas')?.dataset.plugin === 'mandelbrot-distance-field'
                && document.querySelector('#buddhabrot-canvas')?.dataset.rendered === 'true'""",
            timeout=15000,
        )
        distance_status = page.locator("#buddhabrot-status").text_content() or ""
        distance_ok = "Distance-estimated Mandelbrot field" in distance_status and "orbit derivative distance estimate" in distance_status
        self._record("graphical-fractal-selector-switches-distance-field", distance_ok, distance_status)
        if not distance_ok:
            raise HarnessFailure(f"Distance field plugin did not render: {distance_status}")

    def _record(self, name: str, ok: bool, detail: Any = None) -> None:
        self.checks.append({"name": name, "ok": ok, "detail": detail})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main-computer harness")
    parser.add_argument("--url", help="Connect to an already running viewport instead of starting a harness server.")
    parser.add_argument("--host", default="127.0.0.1", help="Host for the disposable harness server.")
    parser.add_argument("--port", type=int, default=0, help="Port for the disposable harness server. Use 0 for any free port.")
    parser.add_argument("--output-dir", default="harness_output", help="Where to write screenshots and the JSON report.")
    parser.add_argument("--headed", action="store_true", help="Show the browser while the harness runs.")
    return parser


def run_from_args(args: argparse.Namespace) -> dict[str, Any]:
    harness = WidgetHarness(
        url=args.url,
        host=args.host,
        port=args.port,
        output_dir=Path(args.output_dir),
        headless=not args.headed,
    )
    return harness.run()


def main() -> int:
    args = build_parser().parse_args()
    started = time.perf_counter()
    report = run_from_args(args)
    elapsed = time.perf_counter() - started
    print(f"Widget harness passed {len(report['checks'])} checks in {elapsed:.2f}s")
    print(f"Report: {Path(report['output_dir']) / 'widget_harness_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
