from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_APP = ROOT / "main_computer" / "web" / "applications"
MCEL_LAB_HTML = WEB_APP / "apps" / "mcel-lab.html"
MCEL_LAB_JS = WEB_APP / "scripts" / "mcel-lab.js"
MCEL_LAB_CSS = WEB_APP / "styles" / "mcel-lab.css"
BLUEPRINTS = WEB_APP / "scripts" / "mcel-app-blueprints-core.js"


REQUIRED_CAPTURE_FIELDS = [
    "appId",
    "route",
    "domSnapshot",
    "dataMcelAttributes",
    "layoutZones",
    "visibleText",
    "boundingBoxes",
    "sourceFileHints",
    "plannerMetadata",
    "knownTests",
    "knownDocs",
    "cssOwners",
    "jsOwners",
]


def run_node_json(script: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_phase_three_blueprints_define_hint_driven_mount_policies() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        global.window = global;
        eval(fs.readFileSync("main_computer/web/applications/scripts/mcel-app-blueprints-core.js", "utf8"));
        const doc = McelAppBlueprintsCore.inspectableBlueprintFor("document-editor");
        const lab = McelAppBlueprintsCore.inspectableBlueprintFor("mcel-lab");
        console.log(JSON.stringify({
          doc: doc.mountPolicy,
          lab: lab.mountPolicy,
          required: McelAppBlueprintsCore.requiredMountCaptureFields(),
          docCssOwners: doc.sourceHints.filter((hint) => hint.endsWith(".css")),
          labCssOwners: lab.sourceHints.filter((hint) => hint.endsWith(".css"))
        }));
        """
    )
    result = run_node_json(script)

    assert result["doc"]["mode"] == "same-page-contained-clone"
    assert result["doc"]["rootSelector"] == "#document-app"
    assert result["doc"]["route"] == "/applications/document"
    assert result["doc"]["sourceMutationAllowed"] is False
    assert result["doc"]["stripDuplicateIds"] is True
    assert result["doc"]["preserveDataMcelAttributes"] is True
    assert result["doc"]["autoMountOnActivate"] is True
    assert result["doc"]["autoMountOnSelect"] is True

    assert result["lab"]["rootSelector"] == "#mcel-lab-app"
    assert result["lab"]["selfMountRecursionGuard"] is True
    assert result["lab"]["sourceMutationAllowed"] is False
    assert result["lab"]["autoMountOnActivate"] is True
    assert result["lab"]["autoMountOnSelect"] is True

    for field in REQUIRED_CAPTURE_FIELDS:
        assert field in result["required"]
        assert field in result["doc"]["requiredCaptureFields"]
        assert field in result["lab"]["requiredCaptureFields"]

    assert "main_computer/web/applications/styles/document.css" in result["docCssOwners"]
    assert "main_computer/web/applications/styles/mcel-lab.css" in result["labCssOwners"]


def test_phase_three_lab_shell_has_contained_mount_surface_and_evidence_panel() -> None:
    source = MCEL_LAB_HTML.read_text(encoding="utf-8")
    primary_start = source.index('class="mcel-lab-blueprint-primary mc-app-primary"')
    primary_end = source.index("</main>", primary_start)
    right_rail_start = source.index('class="mcel-lab-blueprint-right-rail"')
    advanced_start = source.index("Advanced / Legacy proof lab")
    primary = source[primary_start:primary_end]
    right_rail = source[right_rail_start:advanced_start]

    assert 'id="mcel-blueprint-mount-action"' in source
    assert 'data-mcel-mount-action="same-page-contained-clone"' in source
    assert 'id="mcel-blueprint-work-surface"' in primary
    assert "Selecting an app mounts it automatically" in primary
    assert 'id="mcel-blueprint-mount-status"' in right_rail
    assert 'id="mcel-blueprint-mount-report"' in right_rail
    assert 'data-mcel-mount-report-field="dataMcelAttributes"' in right_rail
    assert 'data-mcel-mount-report-field="layoutZones"' in right_rail
    assert 'data-mcel-mount-report-field="boundingBoxes"' in right_rail
    assert 'id="mcel-blueprint-dom-snapshot"' in right_rail
    assert "No app mounted yet" in right_rail


def test_phase_three_mount_script_uses_same_page_clone_not_iframe_or_fetch() -> None:
    source = MCEL_LAB_JS.read_text(encoding="utf-8")
    phase_block = source[
        source.index("function mcelBlueprintShellReportFor"):
        source.index("function renderMcelBlueprintShell")
    ]

    required_functions = [
        "function mcelBlueprintShellFindMountSource",
        "function mcelBlueprintShellPreparePreviewClone",
        "function mcelBlueprintShellBuildMountReport",
        "function mcelBlueprintShellCollectDataMcelAttributes",
        "function mcelBlueprintShellCollectLayoutZones",
        "function mcelBlueprintShellCollectBoundingBoxes",
        "function mcelBlueprintShellMountSelectedApp",
    ]

    for function_name in required_functions:
        assert function_name in phase_block

    assert 'data-mcel-preview-clone' in phase_block
    assert 'data-mcel-source-id' in phase_block
    assert 'data-mcel-mounted-app' in phase_block
    assert "selfMountRecursionGuard" in phase_block
    assert "sourceMutationAllowed" in phase_block
    assert "localStorage.setItem" not in phase_block
    assert "fetch(" not in phase_block
    assert "iframe" not in phase_block.lower()


def test_phase_three_styles_keep_preview_contained_and_non_mutating() -> None:
    source = MCEL_LAB_CSS.read_text(encoding="utf-8")

    assert ".mcel-lab-mounted-preview" in source
    assert ".mcel-lab-mounted-preview-frame" in source
    assert "[data-mcel-preview-clone]" in source
    assert "pointer-events: none" in source
    assert ".mcel-lab-mount-evidence-card" in source
    assert ".mcel-lab-mount-snapshot" in source


def test_phase_three_blueprint_shell_initializes_before_legacy_dependency_gate() -> None:
    source = MCEL_LAB_JS.read_text(encoding="utf-8")
    init_start = source.index("function initMcelLabApp")
    init_end = source.index("function mcelBlueprintShellState", init_start)
    init_block = source[init_start:init_end]

    assert "initMcelBlueprintShell({mountSelected: true});" in init_block
    assert init_block.index("initMcelBlueprintShell({mountSelected: true});") < init_block.index(
        "if (!mcelLabDependenciesReady())"
    )
    assert "window.setTimeout(initMcelLabApp, 0);" in init_block
    assert "blueprintShellInitialized" in source


def test_phase_three_app_selection_uses_hint_driven_automatic_mounting() -> None:
    source = MCEL_LAB_JS.read_text(encoding="utf-8")
    helper_start = source.index("function mcelBlueprintShellSelectApp")
    helper_end = source.index("function renderMcelBlueprintShell", helper_start)
    helper = source[helper_start:helper_end]
    render_start = helper_end
    render_end = source.index("function validateMcelBlueprintShell", render_start)
    render = source[render_start:render_end]
    html = MCEL_LAB_HTML.read_text(encoding="utf-8")

    assert "policy.autoMountOnSelect !== false" in helper
    assert "mcelBlueprintShellMountSelectedApp()" in helper
    assert "mcelBlueprintShellSelectApp(appSelect.value)" in render
    assert "mcelBlueprintShellSelectApp(candidate.appId)" in render
    assert 'mountAction.textContent = mountReport ? "Remount" : "Mount";' in render
    assert "Selecting an app mounts it automatically" in html
