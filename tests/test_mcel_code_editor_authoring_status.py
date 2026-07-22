from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer" / "web" / "applications"
SCRIPTS = WEB / "scripts"


def test_code_editor_authoring_status_files_are_wired() -> None:
    assert (SCRIPTS / "mcel-authored-surface-document.js").exists()
    assert (SCRIPTS / "mcel-code-editor-authoring-status.js").exists()

    app_shell = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    authored_idx = app_shell.index("mcel-authored-surface-document.js")
    status_idx = app_shell.index("mcel-code-editor-authoring-status.js")
    assert authored_idx < status_idx
    assert "mcel-code-editor-ridge-inspector.js" in app_shell


def test_code_editor_authoring_status_chip_is_hidden_by_default() -> None:
    html = (WEB / "apps" / "code-editor.html").read_text(encoding="utf-8")
    assert 'id="code-editor-mcel-authoring-status"' in html
    assert 'hidden data-mcel-authoring-status="selected-file"' in html
    assert 'data-mcel-node-id="code-editor.node.mcel-authored-surface-status-chip"' in html
    assert 'data-mcel-provenance="patch:mcel-safe-13-authoring-status"' in html


def test_code_editor_authoring_status_css_is_bounded_and_non_intrusive() -> None:
    css = (WEB / "styles" / "code-editor.css").read_text(encoding="utf-8")
    assert ".code-editor-mcel-authoring-status[hidden]" in css
    assert "display: none !important" in css
    assert "max-width: 190px" in css
    assert 'data-mcel-authoring-status-state="pass"' in css
    assert 'data-mcel-authoring-status-state="fail"' in css


def test_code_editor_authoring_status_uses_reusable_mcel_analyzer() -> None:
    script = (SCRIPTS / "mcel-code-editor-authoring-status.js").read_text(encoding="utf-8")
    assert "McelAuthoredSurfaceDocument" in script
    assert ".analyzeText(" in script
    assert "MainComputerMonacoAdapter" in script
    assert "element.hidden = !model.visible" in script
    assert "not-applicable" in script
    assert "Health" not in script
    assert "BIO_HEALTH" not in script
