from __future__ import annotations

from pathlib import Path


WEB_APP = Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications"
DOCUMENT_HTML = (WEB_APP / "apps" / "document.html").read_text(encoding="utf-8")
DOCUMENT_CSS = (WEB_APP / "styles" / "document.css").read_text(encoding="utf-8")
DOCUMENT_APP_JS = (WEB_APP / "scripts" / "document-app.js").read_text(encoding="utf-8")


def test_document_editor_binds_product_layout_to_mcel_zones() -> None:
    assert 'data-mcel-workbench="document-editor"' in DOCUMENT_HTML
    assert 'data-mcel-layout="page-centered-writing-workbench"' in DOCUMENT_HTML
    for zone in ["menu", "toolbar", "navigation", "primary", "companion", "status", "advanced"]:
        assert f'data-mcel-layout-zone="{zone}"' in DOCUMENT_HTML


def test_document_editor_uses_three_lane_workbench_grid_on_desktop() -> None:
    assert "grid-template-areas:" in DOCUMENT_CSS
    assert '"navigation primary companion"' in DOCUMENT_CSS
    assert "--document-nav-lane: clamp(210px, 15vw, 260px)" in DOCUMENT_CSS
    assert "--document-companion-lane: clamp(280px, 19vw, 340px)" in DOCUMENT_CSS
    assert "--document-page-lane-min: min(100%, 864px)" in DOCUMENT_CSS
    assert "minmax(var(--document-page-lane-min), 1fr)" in DOCUMENT_CSS
    assert ".document-workspace {" in DOCUMENT_CSS
    assert "display: contents;" in DOCUMENT_CSS
    assert ".document-ai-pane {" in DOCUMENT_CSS
    assert "grid-area: companion;" in DOCUMENT_CSS
    assert ".document-library {" in DOCUMENT_CSS
    assert "grid-area: navigation;" in DOCUMENT_CSS


def test_document_editor_reclaims_top_space_with_compact_menu_toolbar_status() -> None:
    assert "document-menu-popover" in DOCUMENT_HTML
    assert "document-top-status" in DOCUMENT_HTML
    assert '"toolbar toolbar toolbar"' in DOCUMENT_CSS
    assert '"status status status"' in DOCUMENT_CSS
    assert ".document-toolbar {" in DOCUMENT_CSS
    assert "grid-area: toolbar;" in DOCUMENT_CSS
    assert ".document-top-status {" in DOCUMENT_CSS
    assert "grid-area: status;" in DOCUMENT_CSS
    # Feature-heavy commands are no longer peer-level toolbar buttons.
    toolbar_start = DOCUMENT_HTML.index('id="document-toolbar"')
    toolbar_end = DOCUMENT_HTML.index('id="document-layout-popover"', toolbar_start)
    toolbar_fragment = DOCUMENT_HTML[toolbar_start:toolbar_end]
    assert "document-export-pdf" not in toolbar_fragment
    assert "document-add-hidden-plugin" not in toolbar_fragment
    assert "document-insert-game-scene" not in toolbar_fragment


def test_document_editor_left_navigation_and_right_companion_are_independent_lanes() -> None:
    assert 'class="document-app document-library-open document-ai-open"' in DOCUMENT_HTML
    assert 'aria-hidden="false" data-mcel-layout-zone="navigation"' in DOCUMENT_HTML
    assert 'aria-hidden="false" data-mcel-layout-zone="companion"' in DOCUMENT_HTML
    assert "if (isOpen) setDocumentLibraryOpen(false)" not in DOCUMENT_APP_JS
    assert "if (isOpen) setDocumentAiOpen(false)" not in DOCUMENT_APP_JS



def test_document_editor_primary_page_lane_protects_against_side_panel_clipping() -> None:
    assert "max-width: none;" in DOCUMENT_CSS
    assert "justify-items: safe center;" in DOCUMENT_CSS
    assert "scrollbar-gutter: stable both-edges;" in DOCUMENT_CSS
    assert "min-width: 0;\n      min-height: 0;\n      overflow: auto;" in DOCUMENT_CSS
    assert "@media (max-width: 1380px)" in DOCUMENT_CSS
    assert '"companion companion"' in DOCUMENT_CSS

def test_document_editor_has_no_visible_mwsl_pollution() -> None:
    assert "mwsl-workbench-card" not in DOCUMENT_HTML
    assert "MWSL workbench contract" not in DOCUMENT_HTML
    assert "data-mwsl-slot" not in DOCUMENT_HTML


def test_document_editor_library_selection_keeps_navigation_lane_open() -> None:
    assert '.document-library-item")) setDocumentLibraryOpen(false)' not in DOCUMENT_APP_JS
    assert 'documentLibraryList?.addEventListener("click"' not in DOCUMENT_APP_JS

