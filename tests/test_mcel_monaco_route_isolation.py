"""Patch 10: Monaco must not initialize when unrelated MCEL apps are active.

The shared applications shell includes Code Editor JavaScript on every route.
The Code Editor runtime may be constructed globally but its editor surface is
mounted exclusively by the Code Editor branch of the existing route dispatcher.
"""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer/web/applications/scripts"
APP_HTML = ROOT / "main_computer/web/applications/apps/code-editor.html"


def test_code_editor_boot_does_not_mount_hidden_editor_and_route_opt_in_is_explicit():
    runtime = (SCRIPTS / "code-editor.js").read_text(encoding="utf-8")
    router = (SCRIPTS / "app-routing.js").read_text(encoding="utf-8")
    assert "root.MainComputerCodeEditorRuntime = api.createCodeEditorRuntime({root: rootNode});" in runtime
    boot = runtime.split("})(typeof globalThis", 1)[0]
    assert "root.MainComputerCodeEditorRuntime.mount(rootNode)" not in boot
    assert 'if (codeEditorApp && codeEditorApp.dataset.codeEditorRuntime !== "dsl-native")' in router
    assert "window.MainComputerCodeEditorRuntime?.mount(codeEditorApp);" in router
    assert router.index("codeEditorApp.style.display = isCodeEditor") < router.index("window.MainComputerCodeEditorRuntime?.mount(codeEditorApp)")
    assert router.count("window.MainComputerCodeEditorRuntime?.mount(codeEditorApp)") == 1
    assert "window.MainComputerCodeEditorRuntime?.mount(codeEditorApp)" not in router.split("} else if (isTaskManager) {", 1)[1].split("} else if (isConductor) {", 1)[0]


def test_hidden_code_editor_never_starts_monaco_until_route_activation():
    playwright = pytest.importorskip("playwright.sync_api")
    chrome = shutil.which("chromium") or shutil.which("google-chrome")
    if not chrome:
        pytest.skip("Chromium executable unavailable")

    html = APP_HTML.read_text(encoding="utf-8")
    scripted_names = [
        "code-editor-core.js",
        "code-editor-view-model.js",
        "code-editor-capabilities.js",
        "code-editor.js",
    ]
    html = (
        '<!doctype html><html><head><meta charset="utf-8"></head><body>'
        '<div id="task-manager-app">Task Manager</div>'
        + html
        + '<script>window.__monacoAttempts=[]; window.MainComputerMonacoAdapter={'
        'mount(opts){window.__monacoAttempts.push({path:opts.path}); '
        'return Promise.resolve({ok:true,externalOutcome:"controlled-stub",actionOutcome:"pass"});},'
        'layout(){},getValue(){return null;},setModel(){return {ok:true}}};</script>'
        + '</body></html>'
    )
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        page.set_content(html)
        for name in scripted_names:
            page.add_script_tag(path=str(SCRIPTS / name))
        before = page.evaluate("""() => ({
          facade: typeof window.MainComputerCodeEditorRuntime?.mount,
          initialized: document.querySelector('#code-editor-app').dataset.codeEditorRuntime || '',
          monacoAttempts: window.__monacoAttempts.length,
          source: document.querySelector('#code-editor-app')?.style.display
        })""")
        assert before == {"facade": "function", "initialized": "", "monacoAttempts": 0, "source": "none"}
        assert not errors, errors

        # Exercise the same conditional as the Code Editor routing branch,
        # then repeat it to assert idempotent activation from the router.
        activation = """() => {
          const codeEditorApp = document.querySelector('#code-editor-app');
          codeEditorApp.style.display = 'grid';
          if (codeEditorApp && codeEditorApp.dataset.codeEditorRuntime !== 'dsl-native') {
            window.MainComputerCodeEditorRuntime?.mount(codeEditorApp);
          }
          return {
            mounted: codeEditorApp.dataset.codeEditorRuntime,
            attempts: window.__monacoAttempts.length
          };
        }"""
        first = page.evaluate(activation)
        assert first == {"mounted": "dsl-native", "attempts": 1}
        second = page.evaluate(activation)
        assert second == first
        assert not errors, errors
        browser.close()
