from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer" / "web" / "applications"
DOCUMENT_HTML = WEB / "apps" / "document.html"
DOCUMENT_CSS = WEB / "styles" / "document.css"
CODE_EDITOR_CSS = WEB / "styles" / "code-editor.css"
BASE_CSS = WEB / "styles" / "base.css"
WIDGET_EDITOR_CORE = WEB / "scripts" / "widget-editor-core.js"
COUNTER_CSS = WEB / "styles" / "mcel-diagnostics-counter-widget.css"
COUNTER_JS = WEB / "scripts" / "mcel-diagnostics-counter-widget.js"
SURFACE_JS = WEB / "scripts" / "mcel-document-editor-surface.js"
DOCUMENT_LAYOUT_JS = WEB / "scripts" / "document-layout.js"
SELF_DIAGNOSIS_JS = WEB / "scripts" / "mcel-self-diagnosis.js"
LAYOUT_JS = WEB / "scripts" / "document-layout.js"
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
    assert "Patch 22b: keep the center page readable" in css
    assert "overflow-x: hidden;" in css
    assert '.document-canvas[data-document-auto-fit="true"]' in css
    assert 'minmax(96px, var(--document-nav-lane))' in css
    assert 'minmax(208px, var(--document-companion-lane))' in css
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


def test_document_editor_host_and_side_rails_are_viewport_bounded() -> None:
    base = BASE_CSS.read_text(encoding="utf-8")
    css = DOCUMENT_CSS.read_text(encoding="utf-8")

    assert 'body[data-active-app="document"] {' in base
    assert "height: 100vh;" in base
    assert 'body[data-active-app="document"] .viewport {' in base
    assert 'body[data-active-app="document"] main,' in base
    assert 'body[data-active-app="document"] .stage,' in base
    assert 'body[data-active-app="document"] .canvas-wrap {' in base
    assert "overflow: hidden;" in base
    assert 'body[data-active-app="document"] .stage {\n      grid-template-rows: auto minmax(0, 1fr) auto;' in base
    assert 'body[data-active-app="document"] .stage,\n    body[data-active-app="document"] .canvas-wrap {\n      overflow: visible;' not in base

    assert ".document-app {" in css
    assert "height: 100%;\n      max-height: 100%;" in css
    assert ".document-shell {" in css
    assert "width: 100%;\n      height: 100%;\n      max-height: 100%;" in css
    assert ".document-library {" in css
    assert "max-height: 100%;\n      align-self: stretch;" in css
    assert ".document-library-list {" in css
    assert "overflow-x: hidden;\n      overflow-y: auto;" in css
    assert "overscroll-behavior: contain;" in css
    assert "scrollbar-gutter: stable;" in css
    assert ".document-object-stage {" in css
    assert ".document-ai-pane {" in css
    assert css.count(".document-ai-pane {\n        min-height: 0;") == 2
    assert ".document-ai-pane {\n        min-height: 320px;" not in css


def test_document_library_rows_do_not_collapse_inside_bounded_scroll_owner() -> None:
    css = DOCUMENT_CSS.read_text(encoding="utf-8")

    assert "Patch 24a3: a bounded navigation list must overflow as whole rows" in css
    assert ".document-library-list {\n  grid-auto-rows: max-content;" in css
    assert ".document-library-item {\n  min-height: 42px;" in css
    assert "grid-template-rows: auto auto;" in css
    assert "align-content: start;" in css
    assert ".document-library-item strong {\n  display: block;" in css
    assert "min-height: 1.2em;" in css
    assert "line-height: 1.2;" in css
    assert "writing-mode: horizontal-tb;" in css


def test_document_editor_overbudget_layout_preserves_three_lane_workbench() -> None:
    css = DOCUMENT_CSS.read_text(encoding="utf-8")
    layout_js = LAYOUT_JS.read_text(encoding="utf-8")

    assert ".document-shell" in css
    assert 'grid-template-areas:\n          "menu menu menu"\n          "toolbar toolbar toolbar"\n          "navigation primary companion"\n          "status status status";' in css
    assert ".document-library,\n      .document-ai-pane,\n      .document-object-stage {" in css
    assert "max-width: 100%;" in css
    assert ".document-canvas {\n        max-width: 100%;" in css
    assert ".document-app.document-ai-open .document-ai-pane" not in css
    assert "documentEffectiveLayoutZoom" in layout_js
    assert "ResizeObserver" in layout_js
    assert 'documentCanvas?.setAttribute("data-document-auto-fit"' in layout_js
    assert "DOCUMENT_AUTO_FIT_MIN_ZOOM" in layout_js



def test_document_page_auto_fit_refreshes_after_visibility_and_container_changes() -> None:
    layout = DOCUMENT_LAYOUT_JS.read_text(encoding="utf-8")

    assert "DOCUMENT_AUTO_FIT_MIN_ZOOM = 0.45" in layout
    assert "DOCUMENT_AUTO_FIT_GUTTER = 40" in layout
    assert "documentLayoutVisibilityObserver" in layout
    assert "MutationObserver" in layout
    assert "documentObjectStage" in layout
    assert "documentLayoutFitObserver.observe(el)" in layout
    assert "setTimeout(() => scheduleDocumentLayoutFitRefresh(), 240)" in layout

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
    assert 'appId === "document" || appId === "file-explorer"' in diagnosis
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
    assert 'applyFitPolicy(scope.querySelector(selector), "wrap"' in surface
    assert 'applyFitPolicy(scope.querySelector(selector), "compact-icon"' in surface
    assert 'applyFitPolicy(scope.querySelectorAll(selector), "truncate"' in surface
    assert '".document-head-actions",\n      ".document-library-head"' not in surface


def test_document_editor_visual_fit_ignores_intentional_overlay_and_display_contents_owner() -> None:
    diagnosis = SELF_DIAGNOSIS_JS.read_text(encoding="utf-8")

    assert 'isLayoutProbeIgnoredElement(el, appId)' in diagnosis
    assert '.mc-page-overlay-layer[contenteditable=' in diagnosis
    assert '#document-mcel-surface-carriers' in diagnosis
    assert 'if (String(current.display || "") === "contents" || String(parent.display || "") === "contents")' in diagnosis
    assert 'const children = directVisibleChildren(container, context);' in diagnosis
    assert 'const children = directVisibleChildren(container, context)\n        .filter' in diagnosis


def test_document_editor_content_fit_violations_remain_hard_failures_while_parked() -> None:
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


def test_patch24a_layout_spec_assigns_outline_modal_and_docked_companion() -> None:
    text = " ".join(DOC.read_text(encoding="utf-8").split())

    required_phrases = [
        "left Document Outline | editable document page | right Document AI dock",
        "The Pretty Docs file list is no longer a persistent lane",
        "heading hierarchy of the loaded document",
        "MUST NOT fall back to displaying the Pretty Docs file list",
        "The center lane remains the primary width owner",
        "companion expanded/docked transition",
        "docked -> compact right-side affordance",
        "Docking preserves thread, draft, result, and selection context",
        "The companion must not auto-dock during a running request",
        "The file picker is a temporary overlay, not a fourth persistent lane",
        "pass through save/discard/cancel",
        "dynamic outline/file rows do not pollute static layout collision evidence",
        "conceptual zones, not three columns that must always consume width",
        "Independent scroll ownership (Patch 24a1)",
        "Document scrolling MUST NOT move either side panel",
        "The outline list owns its own vertical scrollbar",
        "Only the companion conversation/result body scrolls",
        "The editor shell is not the document scroll owner",
        "Viewport containment correction (Patch 24a2)",
        "Long outline or file-list content MUST NOT increase the height of the editor shell",
        "Responsive rules MUST NOT impose a positive minimum height on the companion",
        "Patch 24a is specification and contract-test work only",
    ]
    for phrase in required_phrases:
        assert phrase in text

def test_patch24a4_focused_host_reserves_apps_gutter_for_code_and_document() -> None:
    base = BASE_CSS.read_text(encoding="utf-8")
    code_css = CODE_EDITOR_CSS.read_text(encoding="utf-8")
    code_doc = (ROOT / "pretty_docs" / "mcel-code-editor-surface-diagnostics.md").read_text(encoding="utf-8")

    assert "Patch 24a4: focused application host" in base
    assert '[data-active-app="code-editor"]' in base
    assert '[data-active-app="document"]' in base
    assert "inset: 0 0 0 var(--app-taskbar-reserved-width);" in base
    assert "grid-template-rows: minmax(0, 1fr);" in base
    assert ") .stage-head," in base
    assert ") .demo-controls {" in base
    assert "display: none !important;" in base
    assert ") .canvas-wrap {" in base
    assert "height: 100%;" in base

    assert "Patch 24a4: participate in the focused applications host" in code_css
    assert 'body[data-active-app="code-editor"] #code-editor-app' in code_css
    assert "position: absolute !important;" in code_css
    assert "width: 100% !important;" in code_css
    assert "height: 100% !important;" in code_css
    assert 'body[data-active-app="document"]:has(#code-editor-app)' in code_css
    assert "overflow: hidden !important;" in code_css
    assert "Apps gutter right edge <= Code Editor root left edge" in code_doc


def test_patch24a4_document_uses_compact_app_owned_chrome_and_ai_scroll_body() -> None:
    html = DOCUMENT_HTML.read_text(encoding="utf-8")
    css = DOCUMENT_CSS.read_text(encoding="utf-8")
    widgets = WIDGET_EDITOR_CORE.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")

    assert 'data-widget-chrome="app-owned"' in html
    assert 'id="document-fullscreen-control"' in html
    assert 'data-fullscreen-target="closest"' in html
    assert 'const appOwnedChrome = widget.dataset.widgetChrome === "app-owned";' in widgets
    assert 'if (!appOwnedChrome && !widget.querySelector(":scope > .fullscreen-control"))' in widgets
    assert 'if (!appOwnedChrome && !widget.querySelector(":scope > .widget-ticker"))' in widgets

    assert "Patch 24a4: compact document-owned chrome" in css
    assert "grid-template-rows: 36px 34px minmax(0, 1fr) 26px;" in css
    assert '#document-app .document-identity-purpose {\n  display: none;' in css
    assert '#document-app .document-head-actions {\n  flex: 0 1 auto;\n  flex-wrap: nowrap;' in css
    assert '#document-app .document-toolbar {\n  height: 34px;' in css
    assert '#document-app .document-shell[data-widget-chrome="app-owned"]:fullscreen {' in css
    assert '#document-app .document-readonly-note {\n  display: none;' in css
    assert '#document-app .document-ai-pane {\n  grid-template-rows: auto minmax(0, 1fr) auto;' in css
    assert '#document-app .document-ai-main {\n  min-height: 0;\n  overflow: auto;' in css

    ai_main = html.index('class="document-ai-main" id="document-ai-main"')
    anchor = html.index('id="document-ai-anchor-summary"')
    composer = html.index('class="document-ai-composer"')
    assert ai_main < anchor < composer

    assert "Focused host and vertical budget correction (Patch 24a4)" in doc
    assert "The generic embedded-widget ticker is not part of the Document Editor layout" in doc
    assert "anchored header scrollable context/conversation/result body anchored prompt composer" in " ".join(doc.split())

