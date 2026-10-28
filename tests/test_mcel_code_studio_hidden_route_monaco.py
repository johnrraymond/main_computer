"""Route isolation: the older Code Studio bootstrap must not mount Monaco off-route.

The shared applications shell embeds Code Studio in the DOM on Task Manager routes.
Unlike code-editor.js, the older code-editor-mcel-studio.js independently mounts a
Monaco surface during its initial render. This regression exercises both owners.
"""
from __future__ import annotations

from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer/web/applications/scripts"
CODE_EDITOR_HTML = ROOT / "main_computer/web/applications/apps/code-editor.html"


def test_code_studio_monaco_isolation_on_hidden_route_and_editor_activation():
    playwright = pytest.importorskip("playwright.sync_api")
    chrome = shutil.which("chromium") or shutil.which("google-chrome")
    if not chrome:
        pytest.skip("Chromium executable unavailable")

    # Use the real Code Editor app markup, including its authored source model,
    # but replace only Monaco's external loader with an observable stub.
    html = (
        '<!doctype html><html><head><meta charset="utf-8"></head>'
        '<body data-active-app="task-manager"><div id="task-manager-app"></div>'
        + CODE_EDITOR_HTML.read_text(encoding="utf-8")
        + '''<script>
          window.__monacoMounts = [];
          window.MainComputerMonacoAdapter = {
            mount(options) {
              window.__monacoMounts.push(options.path || "");
              return Promise.resolve({ok:true, actionOutcome:"pass", externalOutcome:"test-stub"});
            },
            dispose() {return {ok:true};},
            layout() {},
            getValue() {return null;},
            setModel() {return {ok:true};}
          };
        </script></body></html>'''
    )

    with playwright.sync_playwright() as browser_api:
        browser = browser_api.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(html)
        page.add_script_tag(path=str(SCRIPTS / "code-editor-mcel-studio.js"))

        hidden = page.evaluate("""() => ({
          visible: document.querySelector('#code-editor-app').style.display !== 'none',
          mounts: window.__monacoMounts.length,
          studio: typeof window.MainComputerCodeStudio?.mountRuntime
        })""")
        assert hidden == {"visible": False, "mounts": 0, "studio": "function"}

        # A hidden rerender must not start the Monaco loader either.
        page.evaluate("window.MainComputerCodeStudio.mountRuntime()")
        assert page.evaluate("window.__monacoMounts.length") == 0

        # Route activation is still responsible for the real Code Editor.
        for filename in (
            "code-editor-core.js",
            "code-editor-view-model.js",
            "code-editor-capabilities.js",
            "code-editor.js",
        ):
            page.add_script_tag(path=str(SCRIPTS / filename))
        result = page.evaluate("""() => {
          document.body.dataset.activeApp = 'code-editor';
          const node = document.querySelector('#code-editor-app');
          node.style.display = 'grid';
          return window.MainComputerCodeEditorRuntime.mount(node).ok;
        }""")
        assert result is True
        page.wait_for_timeout(50)
        assert page.evaluate("window.__monacoMounts") == ["__no-file-open__.md"]
        assert not errors, errors
        browser.close()
