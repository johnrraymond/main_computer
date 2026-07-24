from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "main_computer" / "web" / "applications" / "styles" / "file-explorer.css"
SURFACE_JS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-file-explorer-surface.js"
SELF_DIAGNOSIS_JS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-self-diagnosis.js"
DOC = ROOT / "pretty_docs" / "mcel-file-explorer-layout-fit.md"


def test_file_explorer_sidebar_text_wraps_and_shell_is_bounded() -> None:
    css = CSS.read_text(encoding="utf-8")

    assert ".file-explorer-app" in css
    assert "overflow: hidden;" in css
    assert ".file-explorer-shell" in css
    assert "min-width: 0;" in css
    assert "grid-template-columns: minmax(200px, 260px) minmax(0, 1fr) minmax(220px, 320px);" in css

    assert "overflow-wrap: anywhere;" in css
    assert "word-break: break-word;" in css
    assert ".file-explorer-root-button {\n      white-space: pre-wrap;" in css


def test_file_explorer_has_medium_width_responsive_contract() -> None:
    css = CSS.read_text(encoding="utf-8")

    assert "@media (max-width: 1180px)" in css
    assert "grid-template-columns: minmax(200px, 250px) minmax(0, 1fr);" in css
    assert ".file-explorer-preview {\n        grid-column: 1 / -1;" in css
    assert "@media (max-width: 780px)" in css


def test_file_explorer_ridges_mark_visual_owners_and_readable_text() -> None:
    surface = SURFACE_JS.read_text(encoding="utf-8")

    assert '"data-mcel-visual-owner": "file-explorer.surface.primary"' in surface
    assert '"data-mcel-layout-zone": "file-explorer.surface.primary"' in surface
    assert '"data-mcel-layout-zone": id' in surface
    assert '"data-mcel-visual-owner": id' in surface
    assert '"data-mcel-readable": "true"' in surface
    assert '"data-mcel-visual-owner": "file-explorer.root-button"' in surface
    assert '"data-mcel-visual-owner": "file-explorer.entry"' in surface


def test_self_diagnosis_knows_file_explorer_visual_fit_selectors() -> None:
    diagnosis = SELF_DIAGNOSIS_JS.read_text(encoding="utf-8")

    for expected in [
        'if (appId === "file-explorer")',
        '".file-explorer-shell"',
        '".file-explorer-roots-panel"',
        '".file-explorer-root-button"',
        '".file-explorer-entry"',
        '"#file-explorer-list"',
        '"#file-explorer-preview"',
        'appId === "file-explorer"',
        '"semantic-content-fit"',
        '"visual-integrity-violation"',
    ]:
        assert expected in diagnosis

    assert 'appId === "file-explorer"\n      ) {\n        detectSemanticProjectionBleed' in diagnosis


def test_layout_fit_documentation_exists() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "root buttons must wrap long Windows paths" in text
    assert "Runtime diagnostics must treat File Explorer panes" in text


def test_self_diagnosis_clipped_paint_box_helper_is_defined_and_exported() -> None:
    diagnosis = SELF_DIAGNOSIS_JS.read_text(encoding="utf-8")

    definition = diagnosis.index("function clippedPaintBox")
    semantic_bleed = diagnosis.index("function detectSemanticProjectionBleed")
    call = diagnosis.index("clippedPaintBox(", semantic_bleed)

    assert definition < semantic_bleed < call
    assert "function intersectPaintBox" in diagnosis
    assert "function overflowClipsPaint" in diagnosis
    assert "clippedPaintBox," in diagnosis
    assert "degrades into a bounded measurement instead of throwing" in diagnosis


def test_self_diagnosis_clipped_range_box_helper_is_defined_and_exported() -> None:
    diagnosis = SELF_DIAGNOSIS_JS.read_text(encoding="utf-8")

    paint_definition = diagnosis.index("function clippedPaintBox")
    range_definition = diagnosis.index("function clippedRangeBox")
    collector = diagnosis.index("function collectReadableTextBoxes")
    range_call = diagnosis.index("clippedRangeBox(", collector)

    assert paint_definition < range_definition < collector < range_call
    assert "return clippedPaintBox(el, rawBox, boundaryElement);" in diagnosis
    assert "clippedRangeBox," in diagnosis
    assert "Text Range#getClientRects() returns painted fragments" in diagnosis
