from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_APP = ROOT / "main_computer" / "web" / "applications"
MCEL_LAB_HTML = WEB_APP / "apps" / "mcel-lab.html"
MCEL_LAB_JS = WEB_APP / "scripts" / "mcel-lab.js"
MCEL_LAB_CSS = WEB_APP / "styles" / "mcel-lab.css"

GENERIC_ZONES = [
    "identity",
    "actions",
    "navigation",
    "primary",
    "inspector",
    "evidence",
    "status",
    "advanced",
]

GENERIC_ASPECTS = [
    "overview",
    "objects",
    "workflows",
    "layout",
    "actions",
    "capabilities",
    "evidence",
    "source",
    "tests",
    "annotations",
    "findings",
    "repair",
]


def test_mcel_lab_phase_two_renders_app_blueprint_aspect_inspector_shell() -> None:
    source = MCEL_LAB_HTML.read_text(encoding="utf-8")

    assert "App Blueprint / Aspect Inspector" in source
    assert 'data-mcel-workbench="mcel-lab"' in source
    assert 'data-mcel-layout="app-aspect-inspector-workbench"' in source
    assert 'id="mcel-blueprint-app-select"' in source
    assert 'id="mcel-blueprint-aspect-select"' in source
    assert 'id="mcel-blueprint-detail-stack"' in source
    assert 'data-mcel-detail-source="blueprint-detail-groups"' in source
    assert 'id="mcel-blueprint-findings"' in source
    assert 'id="mcel-blueprint-validity-status"' in source
    assert 'class="mcel-lab-shell-card mcel-lab-work-area"' in source
    assert 'id="mcel-blueprint-work-surface"' in source
    assert "Drill down" in source

    for zone in GENERIC_ZONES:
        assert f'data-mcel-zone="{zone}"' in source
        assert f'data-mcel-layout-zone="{zone}"' in source

    assert "element.layout.identity-zone" in source
    assert "element.layout.primary-work-zone" in source
    assert "element.layout.inspector-zone" in source
    assert "element.layout.advanced-zone" in source


def test_mcel_lab_phase_two_exposes_static_targets_and_generic_aspects() -> None:
    source = MCEL_LAB_HTML.read_text(encoding="utf-8")

    assert '<option value="document-editor">Document Editor</option>' in source
    assert '<option value="mcel-lab">MCEL Lab</option>' in source
    assert 'data-mcel-blueprint-app-option="document-editor"' in source
    assert 'data-mcel-blueprint-app-option="mcel-lab"' in source

    for aspect in GENERIC_ASPECTS:
        assert f'value="{aspect}"' in source
        assert f'data-mcel-blueprint-aspect-option="{aspect}"' in source
        assert f'data-mcel-blueprint-outline-item="{aspect}"' in source


def test_mcel_lab_default_surface_moves_contract_drilldowns_to_right_rail() -> None:
    source = MCEL_LAB_HTML.read_text(encoding="utf-8")
    main_start = source.index('class="mcel-lab-blueprint-primary mc-app-primary"')
    main_end = source.index("</main>", main_start)
    right_rail_index = source.index('class="mcel-lab-blueprint-right-rail"')
    advanced_index = source.index("Advanced / Legacy proof lab")

    primary_surface = source[main_start:main_end]
    right_rail = source[right_rail_index:advanced_index]

    assert 'class="mcel-lab-shell-card mcel-lab-work-area"' in primary_surface
    assert "Drill down" not in primary_surface
    assert "Blueprint details" not in primary_surface
    assert 'id="mcel-blueprint-detail-stack"' in right_rail
    assert "Contract internals stay here in the inspector rail" in right_rail
    assert "<details open" not in right_rail
    assert 'id="mcel-blueprint-preview"' not in source
    assert 'id="mcel-blueprint-zone-map"' not in source
    assert "element.inspection.aspect-panel" not in primary_surface
    assert "element.refactor.annotation-map" not in primary_surface
    assert "Static blueprint seed" not in primary_surface


def test_mcel_lab_legacy_proof_lab_is_advanced_not_primary() -> None:
    source = MCEL_LAB_HTML.read_text(encoding="utf-8")

    advanced_index = source.index("Advanced / Legacy proof lab")
    legacy_title_index = source.index("Semantic Interface Forge")
    scenario_index = source.index('id="mcel-scenario-select"')
    skeleton_index = source.index("Minimal site acid test")

    assert advanced_index < legacy_title_index
    assert advanced_index < scenario_index
    assert advanced_index < skeleton_index
    assert source.index("App Blueprint / Aspect Inspector") < advanced_index

    primary_region = source[:advanced_index]
    forbidden_primary_markers = [
        "Semantic Interface Forge",
        'id="mcel-scenario-select"',
        "Minimal site acid test",
        'id="mcel-open-editor-modal"',
        'id="mcel-open-site-modal"',
        'data-mcel-mode="stress"',
    ]
    for marker in forbidden_primary_markers:
        assert marker not in primary_region


def test_mcel_lab_shell_script_consumes_blueprint_core_without_mounting_or_persistence() -> None:
    source = MCEL_LAB_JS.read_text(encoding="utf-8")

    assert "function renderMcelBlueprintShell" in source
    assert "McelAppBlueprintsCore" in source
    assert "listInspectableAppBlueprints" in source
    assert "inspectableBlueprintFor" in source
    assert "document-editor" in source
    assert "mcel-lab" in source
    assert "Mounting is deferred to Phase 3" in source
    assert "Point-and-inspect is deferred to Phase 4" in source
    assert "Export packet generation is deferred to Phase 7" in source
    assert "mcelBlueprintShellPopulateList" in source
    assert "mcelBlueprintShellRenderDetailStack" in source
    assert "genericDetailGroups" in source
    assert "candidate.elementId || \"element.inspection.aspect-panel\"" not in source

    phase_two_block = source[
        source.index("function mcelBlueprintShellState"):
        source.index("function renderMcelElementLibraryAcidTest")
    ]
    assert "localStorage.setItem" not in phase_two_block
    assert "fetch(" not in phase_two_block
    assert "iframe" not in phase_two_block.lower()


def test_mcel_lab_phase_two_styles_define_generic_workbench_regions() -> None:
    source = MCEL_LAB_CSS.read_text(encoding="utf-8")

    required_selectors = [
        ".mcel-lab-blueprint-shell",
        ".mcel-lab-blueprint-topbar",
        ".mcel-lab-blueprint-workbench",
        ".mcel-lab-blueprint-navigation",
        ".mcel-lab-blueprint-primary",
        ".mcel-lab-blueprint-right-rail",
        ".mcel-lab-blueprint-status",
        ".mcel-lab-work-area",
        ".mcel-lab-work-surface",
        ".mcel-lab-drilldowns",
        ".mcel-lab-advanced-legacy",
    ]

    for selector in required_selectors:
        assert selector in source

    assert 'grid-template-areas: "navigation primary rail";' in source
    assert "minmax(640px, 1fr)" in source
    assert "Phase 2 App Blueprint / Aspect Inspector shell" in source


def test_mcel_lab_shell_still_requires_non_lab_target() -> None:
    source = MCEL_LAB_HTML.read_text(encoding="utf-8")
    js = MCEL_LAB_JS.read_text(encoding="utf-8")

    assert source.count("document-editor") >= 2
    assert source.count("mcel-lab") >= 2
    assert re.search(r"inspectableBlueprintFor\?\.\(\"document-editor\"\)", js)
    assert re.search(r"inspectableBlueprintFor\?\.\(\"mcel-lab\"\)", js)


def test_phase_two_does_not_add_visible_debug_cards_to_product_apps() -> None:
    product_apps = [
        WEB_APP / "apps" / "document.html",
        WEB_APP / "apps" / "git-tools.html",
        WEB_APP / "apps" / "code-editor.html",
    ]

    forbidden_visible_shell_markers = [
        "App Blueprint / Aspect Inspector",
        "mcel-blueprint-zone-map",
        "Advanced / Legacy proof lab",
        "element.layout.advanced-zone",
    ]

    for app_file in product_apps:
        source = app_file.read_text(encoding="utf-8")
        for marker in forbidden_visible_shell_markers:
            assert marker not in source
