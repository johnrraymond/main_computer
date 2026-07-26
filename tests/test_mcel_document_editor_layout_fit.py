from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer" / "web" / "applications"
DOCUMENT_HTML = WEB / "apps" / "document.html"
DOCUMENT_CSS = WEB / "styles" / "document.css"
COUNTER_CSS = WEB / "styles" / "mcel-diagnostics-counter-widget.css"
COUNTER_JS = WEB / "scripts" / "mcel-diagnostics-counter-widget.js"
SURFACE_JS = WEB / "scripts" / "mcel-document-editor-surface.js"
SELF_DIAGNOSIS_JS = WEB / "scripts" / "mcel-self-diagnosis.js"
DOC = ROOT / "pretty_docs" / "mcel-document-editor-layout-fit.md"


def test_document_editor_has_diagnostics_counter_widget_mount() -> None:
    html = DOCUMENT_HTML.read_text(encoding="utf-8")
    counter = COUNTER_JS.read_text(encoding="utf-8")
    css = COUNTER_CSS.read_text(encoding="utf-8")

    assert 'id="document-diagnostics-counter"' in html
    assert 'data-mcel-diagnostics-counter="document"' in html
    assert 'root: "#document-app"' in counter
    assert 'placeholder: "#document-diagnostics-counter"' in counter
    assert '"#document-app .document-head-actions"' in counter
    assert '[data-mcel-diagnostics-counter="document"]' in css
    assert "#document-app .document-head-actions .mcel-diagnostics-counter" in css


def test_document_editor_layout_keeps_ai_rail_right_and_shrinks_library() -> None:
    css = DOCUMENT_CSS.read_text(encoding="utf-8")

    assert "container: document-editor-app / inline-size;" in css
    assert "--document-page-lane-min: 0px;" in css
    assert "minmax(var(--document-page-lane-min), 1fr)" in css
    assert "@container document-editor-app (max-width: 1380px)" in css
    assert "@container document-editor-app (max-width: 1180px)" in css
    assert "@container document-editor-app (max-width: 900px)" in css
    assert '"navigation primary companion"' in css
    assert '"primary"\n          "navigation"\n          "companion"' not in css
    assert 'minmax(56px, var(--document-nav-lane))' in css
    assert 'minmax(208px, var(--document-companion-lane))' in css
    assert "box-sizing: border-box;" in css
    assert ".document-library-item *" in css
    assert "max-width: 100%;" in css
    assert '.document-library-item span {\n      min-width: 0;' in css
    assert 'text-overflow: ellipsis;' in css
    assert "container: document-library-rail / inline-size;" in css
    assert "@container document-library-rail (max-width: 180px)" in css
    assert "@container document-library-rail (max-width: 92px)" in css
    assert "#document-library-refresh::before" in css
    assert '#document-library-close::before' in css
    assert ".document-library-head-actions,\n      .document-library-empty" not in css


def test_document_editor_overbudget_layout_preserves_three_lane_workbench() -> None:
    css = DOCUMENT_CSS.read_text(encoding="utf-8")

    assert ".document-shell" in css
    assert 'grid-template-areas:\n          "menu menu menu"\n          "toolbar toolbar toolbar"\n          "navigation primary companion"\n          "status status status";' in css
    assert ".document-library,\n      .document-ai-pane,\n      .document-object-stage {" in css
    assert "max-width: 100%;" in css
    assert ".document-canvas {\n        max-width: 100%;" in css
    assert ".document-app.document-ai-open .document-ai-pane" not in css


def test_document_editor_visual_fit_diagnostics_cover_document_regions() -> None:
    diagnosis = SELF_DIAGNOSIS_JS.read_text(encoding="utf-8")

    for expected in [
        'if (appId === "document")',
        '".document-shell"',
        '".document-library"',
        '".document-object-stage"',
        '".document-canvas"',
        '".document-ai-pane"',
        '".document-ai-anchor-summary"',
        '".document-ai-preview"',
        '"#document-editor"',
        'appId === "document"',
        '"semantic-content-fit"',
        '"visual-integrity-violation"',
    ]:
        assert expected in diagnosis

    assert 'selector: ".document-shell", label: "Document workspace shell"' in diagnosis
    assert '".document-workspace"' not in diagnosis
    assert "isDocumentFloatingMenuElement" in diagnosis
    assert "isLayoutProbeIgnoredElement" in diagnosis
    assert '.mc-page-overlay-layer[aria-hidden=' in diagnosis
    assert 'String(current.display || "") === "contents"' in diagnosis
    assert 'directVisibleChildren(container, context)' in diagnosis
    assert 'appId === "document" ||\n        appId === "file-explorer"' in diagnosis
    assert "contentFitViolationSeverity(contract, snapshot)" in diagnosis
    assert "contentFitPolicyFor(el)" in diagnosis
    assert "declaredFitPolicyAllowsClip" in diagnosis
    assert "fitPolicy," in diagnosis


def test_document_editor_surface_marks_runtime_readable_candidates_without_menu_container_text() -> None:
    surface = SURFACE_JS.read_text(encoding="utf-8")
    html = DOCUMENT_HTML.read_text(encoding="utf-8")

    library_tag_start = html.index('id="document-library-list"')
    library_tag_end = html.index(">", library_tag_start)
    export_tag_start = html.index('id="document-export-menu"')
    export_tag_end = html.index(">", export_tag_start)
    assert 'data-mcel-readable="true"' not in html[library_tag_start:library_tag_end]
    assert 'data-mcel-readable="true"' not in html[export_tag_start:export_tag_end]
    assert 'data-mcel-fit-policy="wrap"' in html
    assert 'aria-label="Refresh Pretty Docs"' in html
    assert 'aria-label="Close Pretty Docs library"' in html

    assert 'scope.querySelector(".document-shell")' in surface
    assert 'scope.querySelector(".document-head-actions")' in surface
    assert '".document-library-list"' in surface
    assert '".document-canvas"' in surface
    assert '".document-ai-main"' in surface
    assert '"data-mcel-readable": "true"' in surface
    assert '"data-mcel-visual-owner": surfaceId' in surface
    assert '#document-export-menu' in surface
    assert 'removeAttribute("data-mcel-readable")' in surface
    assert '"data-mcel-fit-policy": "wrap"' in surface
    assert '"data-mcel-fit-policy": "compact-icon"' in surface
    assert '"data-mcel-fit-policy": "truncate"' in surface
    assert '".document-head-actions",\n      ".document-library-head"' not in surface


def test_document_editor_visual_fit_ignores_intentional_overlay_and_display_contents_owner() -> None:
    diagnosis = SELF_DIAGNOSIS_JS.read_text(encoding="utf-8")

    assert 'isLayoutProbeIgnoredElement(el, appId)' in diagnosis
    assert '.mc-page-overlay-layer[contenteditable=' in diagnosis
    assert '#document-mcel-surface-carriers' in diagnosis
    assert 'if (String(current.display || "") === "contents" || String(parent.display || "") === "contents")' in diagnosis
    assert 'const children = directVisibleChildren(container, context);' in diagnosis
    assert 'const children = directVisibleChildren(container, context)\n        .filter' in diagnosis


def test_document_editor_content_fit_violations_are_hard_failures_for_semantic_runtime_apps() -> None:
    diagnosis = SELF_DIAGNOSIS_JS.read_text(encoding="utf-8")

    assert 'function contentFitViolationSeverity(contract, snapshot)' in diagnosis
    assert 'appId === "document" || appId === "file-explorer"' in diagnosis
    assert '"semantic-content-fit-violation"' in diagnosis
    assert 'contentFitViolationSeverity(contract, snapshot)' in diagnosis



def test_document_editor_layout_fit_documentation_exists() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "Document Editor layout fit" in text
    assert "container width" in text
    assert "diagnostics counter" in text
    assert "runtime visual-fit" in text
    assert "right rail" in text
