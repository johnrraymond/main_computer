from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[3]


def test_code_editor_existing_host_surface_remains_present_for_host_bound_projection() -> None:
    html = (REPO / "main_computer/web/applications/apps/code-editor.html").read_text(encoding="utf-8")
    shell = (REPO / "main_computer/web/applications.html").read_text(encoding="utf-8")

    assert 'id="code-editor-app"' in html
    assert "<!-- @include applications/apps/code-editor.html -->" in shell
    assert "<!-- @include applications/scripts/code-editor-monaco-adapter.js -->" in shell
    assert "<!-- @include applications/scripts/code-editor-mcel-studio.js -->" in shell
    assert "<!-- @include applications/scripts/code-editor-core.js -->" in shell
    assert "<!-- @include applications/scripts/code-editor-view-model.js -->" in shell
    assert "<!-- @include applications/scripts/code-editor-capabilities.js -->" in shell
    assert "<!-- @include applications/scripts/code-editor.js -->" in shell
    assert "<!-- @include applications/scripts/code-editor-semantic-adapter.js -->" not in shell
    assert shell.index("applications/scripts/code-editor-monaco-adapter.js") < shell.index("applications/scripts/code-editor.js")
    assert shell.index("applications/scripts/code-editor-mcel-studio.js") < shell.index("applications/scripts/code-editor.js")


def test_code_editor_canonical_runtime_facade_is_declared_but_not_shimmed_to_old_studio() -> None:
    source = (REPO / "mcel_apps/code-editor/application.js").read_text(encoding="utf-8")
    assert 'runtimeFacade: "MainComputerCodeEditorRuntime"' in source
    assert "MainComputerCodeStudio" not in source


def test_code_editor_monaco_host_is_permanent_center_pane_surface() -> None:
    runtime = (REPO / "main_computer/web/applications/scripts/code-editor.js").read_text(encoding="utf-8")
    styles = (REPO / "main_computer/web/applications/styles/code-editor.css").read_text(encoding="utf-8")
    adapter = (REPO / "main_computer/web/applications/scripts/code-editor-monaco-adapter.js").read_text(encoding="utf-8")

    assert "monacoDocumentFor(active)" in runtime
    assert "__no-file-open__.md" in runtime
    assert "readOnly: true" in runtime
    assert "readOnly: !documentModel.placeholder" not in runtime
    assert "does not flip" in runtime
    assert "#code-editor-app[data-code-editor-runtime=\"dsl-native\"] #code-studio-runtime-monaco.code-studio-monaco-host.mcel-code-editor-authoring-host" in styles
    assert "#code-editor-app[data-code-editor-runtime-surface-mode=\"legacy-fidelity\"] .code-studio-body" in styles
    assert "preserveExistingCodeStudioSurface(rootNode)" in runtime
    assert "renderPrimaryEditorPreview(active, patch, receipts)" in runtime
    assert "box-sizing: content-box !important" in styles
    assert 'const LOCAL_VS_BASE = "/applications/vendor/monaco-editor/min/vs";' in adapter
    assert "inspect," in adapter
